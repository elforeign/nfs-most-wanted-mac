/* Discord status (Rich Presence). Included by nfsmw.c.
 *
 * Shows "Playing Need for Speed: Most Wanted" on the player's Discord profile, with "Driving in Rockport" or "In the
 * menus", the time played and a "Play it on a Mac" button to this project (Discord
 * shows buttons to others, not to the player). Discord's desktop app listens on a Unix socket, $TMPDIR/discord-ipc-0 (to -9); a frame
 * is a little-endian opcode and length, then JSON (opcode 0 handshake, 1 a command). No library and no network: the
 * socket is local, and without Discord running nothing happens.
 *
 * game_device_update reads the game flow (0x00925e90: 6 in the world) and the setting once a frame and only stores
 * them; a worker thread does all socket work, so a slow or absent Discord never holds a frame. It sends a change at
 * most every 15 s (Discord drops faster updates) and retries a missing Discord every 15 s. Setting `discord_status`
 * (on); off clears the status at once. */
#ifndef DISCORD_STATUS_H
#define DISCORD_STATUS_H

#include <pthread.h>
#include <sys/socket.h>
#include <sys/un.h>

#define DISCORD_APP_ID "1559567073565479046" /* the Discord application whose name, "Need for Speed: Most Wanted", is shown */
#define DISCORD_RETRY_S 15

enum { DS_OFF = 0, DS_MENUS = 1, DS_DRIVING = 2 };
static _Atomic int g_ds_want = DS_MENUS; /* written by the game thread, read by the worker */
static _Atomic int g_ds_stop;
static _Atomic int g_ds_connected; /* set by the worker; logged by the game thread */
static pthread_t g_ds_thread;
static int g_ds_started;
static time_t g_ds_session_start;

/* Game thread, once a frame: two reads and a store. */
static void discord_status_tick(const PopModApi *api) {
    if (!g_ds_started) return;
    static int logged;
    if (atomic_load(&g_ds_connected) != logged) {
        logged = atomic_load(&g_ds_connected);
        api->log(api, logged ? "core.nfsmw: Discord status: connected" : "core.nfsmw: Discord status: disconnected");
    }
    const int want = !setting("discord_status", 1) ? DS_OFF : get_u32(0x00925e90u) == 6u ? DS_DRIVING : DS_MENUS;
    atomic_store(&g_ds_want, want);
}

static int ds_write(int fd, uint32_t op, const char *json) {
    const uint32_t len = (uint32_t)strlen(json);
    uint8_t head[8];
    for (int i = 0; i < 4; ++i) { head[i] = (uint8_t)(op >> (8 * i)); head[4 + i] = (uint8_t)(len >> (8 * i)); }
    return send(fd, head, sizeof head, 0) == (ssize_t)sizeof head && send(fd, json, len, 0) == (ssize_t)len ? 0 : -1;
}

/* Reads and discards one reply frame; -1 when Discord closed the socket or sent an error/close frame. */
static int ds_read(int fd) {
    uint8_t head[8];
    if (recv(fd, head, sizeof head, MSG_WAITALL) != (ssize_t)sizeof head) return -1;
    const uint32_t op = head[0] | head[1] << 8 | (uint32_t)head[2] << 16 | (uint32_t)head[3] << 24;
    uint32_t left = head[4] | head[5] << 8 | (uint32_t)head[6] << 16 | (uint32_t)head[7] << 24;
    char buf[512];
    while (left) {
        const ssize_t n = recv(fd, buf, left < sizeof buf ? left : sizeof buf, 0);
        if (n <= 0) return -1;
        left -= (uint32_t)n;
    }
    return op == 2u ? -1 : 0; /* 2: Discord closes the connection */
}

static int ds_connect(void) {
    char dir[PATH_MAX] = "";
    const char *tmp = getenv("TMPDIR");
    if (tmp && *tmp) snprintf(dir, sizeof dir, "%s", tmp);
    else if (!confstr(_CS_DARWIN_USER_TEMP_DIR, dir, sizeof dir)) snprintf(dir, sizeof dir, "/tmp/");
    for (int i = 0; i < 10; ++i) {
        struct sockaddr_un sa = {.sun_family = AF_UNIX};
        snprintf(sa.sun_path, sizeof sa.sun_path, "%s%sdiscord-ipc-%d", dir, dir[strlen(dir) - 1] == '/' ? "" : "/", i);
        const int fd = socket(AF_UNIX, SOCK_STREAM, 0);
        if (fd < 0) return -1;
        const int one = 1;
        const struct timeval tv = {.tv_sec = 3};
        setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &one, sizeof one); /* a closed Discord must not end the game */
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);
        setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof tv);
        if (connect(fd, (struct sockaddr *)&sa, sizeof sa) == 0) {
            if (ds_write(fd, 0, "{\"v\":1,\"client_id\":\"" DISCORD_APP_ID "\"}") == 0 && ds_read(fd) == 0) return fd;
            close(fd);
            return -1;
        }
        close(fd);
    }
    return -1;
}

static int ds_send(int fd, int want) {
    static unsigned nonce;
    char json[900];
    if (want == DS_OFF)
        snprintf(json, sizeof json, "{\"cmd\":\"SET_ACTIVITY\",\"args\":{\"pid\":%d},\"nonce\":\"%u\"}", (int)getpid(), ++nonce);
    else
        snprintf(json, sizeof json,
                 "{\"cmd\":\"SET_ACTIVITY\",\"args\":{\"pid\":%d,\"activity\":{\"details\":\"%s\","
                 "\"timestamps\":{\"start\":%lld},"
                 "\"assets\":{\"large_image\":\"logo\",\"large_text\":\"Need for Speed: Most Wanted (native Mac)\"},"
                 "\"buttons\":[{\"label\":\"Play it on a Mac\",\"url\":\"https://github.com/elforeign/nfs-most-wanted-mac\"}]}},"
                 "\"nonce\":\"%u\"}",
                 (int)getpid(), want == DS_DRIVING ? "Driving in Rockport" : "In the menus",
                 (long long)g_ds_session_start, ++nonce);
    return ds_write(fd, 1, json) == 0 && ds_read(fd) == 0 ? 0 : -1;
}

static void *ds_worker(void *arg) {
    (void)arg;
    int fd = -1, shown = -1;
    time_t next_try = 0, next_send = 0;
    while (!atomic_load(&g_ds_stop)) {
        const int want = atomic_load(&g_ds_want);
        const time_t now = time(NULL);
        if (fd < 0 && want != DS_OFF && now >= next_try) {
            fd = ds_connect();
            next_try = now + DISCORD_RETRY_S;
            shown = -1;
            atomic_store(&g_ds_connected, fd >= 0);
        }
        if (fd >= 0 && want != shown && now >= next_send) {
            if (ds_send(fd, want) == 0) {
                shown = want;
                next_send = now + DISCORD_RETRY_S;
            } else {
                close(fd); /* Discord quit or restarted: connect again later */
                fd = -1;
                atomic_store(&g_ds_connected, 0);
            }
        }
        if (fd >= 0 && want == DS_OFF && shown == DS_OFF) { close(fd); fd = -1; atomic_store(&g_ds_connected, 0); }
        usleep(500000);
    }
    if (fd >= 0) { ds_send(fd, DS_OFF); close(fd); }
    return NULL;
}

static void discord_status_start(const PopModApi *api) {
    if (g_ds_started) return;
    g_ds_session_start = time(NULL);
    atomic_store(&g_ds_stop, 0);
    if (pthread_create(&g_ds_thread, NULL, ds_worker, NULL) == 0) g_ds_started = 1;
    else api->log(api, "core.nfsmw: Discord status: no worker thread; off for this session");
}

static void discord_status_stop(void) {
    if (!g_ds_started) return;
    atomic_store(&g_ds_stop, 1);
    pthread_join(g_ds_thread, NULL);
    g_ds_started = 0;
}

#endif
