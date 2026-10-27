                                                                                                              
                                                                                                       
                                                                                  
  
                                                                                                                          
  
                                                                                                                  
                                                                                                                    
                                                                                                                  
                                                                                                               
                                   
  
                                                                                                                            
                                                                                                               
                                                                                                                 
                                                                                                                
                                                                                                                      
                                                                                                               
                                                                                                                   
                                                  
#ifndef PUB_DISCORD_H
#define PUB_DISCORD_H

#include <errno.h>
#include <limits.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

#define DISCORD_APP_ID "1559581678853431396" /* NFS Most Wanted Native: the project's own Discord application */
#ifndef PUB_PROJECT_URL
#define PUB_PROJECT_URL "https://github.com/elforeign/nfs-most-wanted-mac" /* the button; build.sh may override */
#endif
#ifndef DISCORD_RETRY_S
#define DISCORD_RETRY_S 15 /* a test sets it shorter */
#endif

enum { DS_OFF = 0, DS_MENUS = 1, DS_DRIVING = 2 };
static _Atomic int g_ds_want = DS_OFF;                                                     
static _Atomic int g_ds_connected;                                                       
static pthread_mutex_t g_ds_mu = PTHREAD_MUTEX_INITIALIZER; /* guards the four below */
static pthread_cond_t g_ds_cv = PTHREAD_COND_INITIALIZER;
static int g_ds_stop;
static int g_ds_fd = -1;                                                                       
static char g_ds_note[200];  /* a Discord error for the log, taken by the game thread */
static pthread_t g_ds_thread;
static int g_ds_started;
static time_t g_ds_session_start;

static void ds_note(const char *what, const char *reply) {
    /* Discord's error JSON carries "code" and "message"; only those go to the log (the handshake's READY reply holds
     * the player's Discord name and is never logged). */
    char msg[120] = "";
    const char *m = strstr(reply, "\"message\":\"");
    if (m) {
        m += 11;
        size_t n = 0;
        for (; *m && *m != '"' && n < sizeof msg - 1; ++m) {
            if (*m == '\\' && m[1]) ++m; /* \" in the JSON is a quote in the message */
            msg[n++] = *m;
        }
        msg[n] = 0;
    }
    const char *c = strstr(reply, "\"code\":");
    static char last[sizeof g_ds_note]; /* the same refusal (a retry every 15 s) is logged once */
    char note[sizeof g_ds_note];
    snprintf(note, sizeof note, "Discord refused the %s: code %d, \"%s\"", what, c ? atoi(c + 7) : 0, msg);
    if (!strcmp(note, last)) return;
    memcpy(last, note, sizeof last);
    pthread_mutex_lock(&g_ds_mu);
    if (!g_ds_note[0]) memcpy(g_ds_note, note, sizeof g_ds_note);
    pthread_mutex_unlock(&g_ds_mu);
}

static int ds_write(int fd, uint32_t op, const char *json) {
    const uint32_t len = (uint32_t)strlen(json);
    uint8_t head[8];
    for (int i = 0; i < 4; ++i) { head[i] = (uint8_t)(op >> (8 * i)); head[4 + i] = (uint8_t)(len >> (8 * i)); }
    return send(fd, head, sizeof head, 0) == (ssize_t)sizeof head && send(fd, json, len, 0) == (ssize_t)len ? 0 : -1;
}

/* Reads one reply frame (its first 1 KB kept, the rest discarded). -1: the socket failed or Discord closed it
 * (opcode 2); 1: Discord answered with an error (noted for the log); 0: fine. */
static int ds_read(int fd, const char *what) {
    uint8_t head[8];
    if (recv(fd, head, sizeof head, MSG_WAITALL) != (ssize_t)sizeof head) return -1;
    const uint32_t op = head[0] | head[1] << 8 | (uint32_t)head[2] << 16 | (uint32_t)head[3] << 24;
    uint32_t left = head[4] | head[5] << 8 | (uint32_t)head[6] << 16 | (uint32_t)head[7] << 24;
    char keep[1024], skip[512];
    size_t kept = 0;
    while (left) {
        char *dst = kept < sizeof keep - 1 ? keep + kept : skip;
        const size_t room = kept < sizeof keep - 1 ? sizeof keep - 1 - kept : sizeof skip;
        const ssize_t n = recv(fd, dst, left < room ? left : room, 0);
        if (n <= 0) return -1;
        if (dst == keep + kept) kept += (size_t)n;
        left -= (uint32_t)n;
    }
    keep[kept] = 0;
    if (op == 2u) { ds_note(what, keep); return -1; }
    if (strstr(keep, "\"evt\":\"ERROR\"")) { ds_note(what, keep); return 1; }
    return 0;
}

static void ds_close(int fd) {
    pthread_mutex_lock(&g_ds_mu);
    g_ds_fd = -1;
    pthread_mutex_unlock(&g_ds_mu);
    close(fd);
    atomic_store(&g_ds_connected, 0);
}

static int ds_connect(void) {
    char dir[PATH_MAX] = "";
    const char *tmp = getenv("TMPDIR");
    if (tmp && *tmp) snprintf(dir, sizeof dir, "%s", tmp);
#ifdef _CS_DARWIN_USER_TEMP_DIR
    else if (!confstr(_CS_DARWIN_USER_TEMP_DIR, dir, sizeof dir)) snprintf(dir, sizeof dir, "/tmp/");
#else
    else snprintf(dir, sizeof dir, "/tmp/");
#endif
    for (int i = 0; i < 10; ++i) {
        struct sockaddr_un sa = {.sun_family = AF_UNIX};
        snprintf(sa.sun_path, sizeof sa.sun_path, "%s%sdiscord-ipc-%d", dir, dir[strlen(dir) - 1] == '/' ? "" : "/", i);
        const int fd = socket(AF_UNIX, SOCK_STREAM, 0);
        if (fd < 0) return -1;
        const int one = 1;
        const struct timeval tv = {.tv_sec = 3};
#ifdef SO_NOSIGPIPE
        setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &one, sizeof one); /* a closed Discord must not end the game */
#endif
        (void)one;
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);
        setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof tv);
        pthread_mutex_lock(&g_ds_mu);
        const int stopping = g_ds_stop;
        if (!stopping) g_ds_fd = fd;
        pthread_mutex_unlock(&g_ds_mu);
        if (stopping) { close(fd); return -1; }
        if (connect(fd, (struct sockaddr *)&sa, sizeof sa) == 0) {
            pthread_mutex_lock(&g_ds_mu); /* a stop during connect() found nothing to shut down: check again */
            const int stopped = g_ds_stop;
            pthread_mutex_unlock(&g_ds_mu);
            if (stopped) { ds_close(fd); return -1; }
            if (ds_write(fd, 0, "{\"v\":1,\"client_id\":\"" DISCORD_APP_ID "\"}") == 0 && ds_read(fd, "connection") == 0) {
                atomic_store(&g_ds_connected, 1);
                return fd;
            }
            ds_close(fd);
            return -1;
        }
        ds_close(fd);
    }
    return -1;
}

/* -1: the socket failed (connect again later); 0: shown; 1: Discord refused it (noted; not sent again). */
static int ds_send(int fd, int want) {
    static unsigned nonce;
    char json[900];
    if (want == DS_OFF)
        snprintf(json, sizeof json, "{\"cmd\":\"SET_ACTIVITY\",\"args\":{\"pid\":%d},\"nonce\":\"%u\"}", (int)getpid(), ++nonce);
    else
        snprintf(json, sizeof json,
                 "{\"cmd\":\"SET_ACTIVITY\",\"args\":{\"pid\":%d,\"activity\":{\"details\":\"%s\","
                 "\"timestamps\":{\"start\":%lld},"
                 "\"buttons\":[{\"label\":\"Play it on a Mac\",\"url\":\"%s\"}]}},"
                 "\"nonce\":\"%u\"}",
                 (int)getpid(), want == DS_DRIVING ? "Driving in Rockport" : "In the menus",
                 (long long)g_ds_session_start, PUB_PROJECT_URL, ++nonce);
    if (ds_write(fd, 1, json)) return -1;
    return ds_read(fd, "status");
}

                                                                                                        
static time_t ds_now(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec;
}

static void *ds_worker(void *arg) {
    (void)arg;
    int fd = -1, shown = -1;
    time_t next_try = 0, next_send = 0;
    pthread_mutex_lock(&g_ds_mu);
    while (!g_ds_stop) {
        pthread_mutex_unlock(&g_ds_mu);
        const int want = atomic_load(&g_ds_want);
        const time_t now = ds_now();
        if (fd < 0 && want != DS_OFF && now >= next_try) {
            fd = ds_connect();
            next_try = now + DISCORD_RETRY_S;
            shown = -1;
        }
        if (fd >= 0 && want != shown && (now >= next_send || want == DS_OFF)) { /* off clears at once */
            const int r = ds_send(fd, want);
            if (r < 0) { ds_close(fd); fd = -1; } /* Discord quit or restarted: connect again later */
            else { shown = want; next_send = now + DISCORD_RETRY_S; }
        }
        if (fd >= 0 && want == DS_OFF && shown == DS_OFF) { ds_close(fd); fd = -1; }
        if (fd >= 0 && want == shown) { /* idle: notice a Discord that quit or restarted, so the status comes back */
            char c;
            const ssize_t n = recv(fd, &c, 1, MSG_PEEK | MSG_DONTWAIT);
            if (n == 0 || (n < 0 && errno != EAGAIN && errno != EWOULDBLOCK)) { ds_close(fd); fd = -1; }
        }
        pthread_mutex_lock(&g_ds_mu);
        if (g_ds_stop) break;
        struct timeval tv;
        gettimeofday(&tv, NULL);
        long usec = tv.tv_usec + 500000;
        struct timespec until = {.tv_sec = tv.tv_sec + usec / 1000000, .tv_nsec = (usec % 1000000) * 1000};
        pthread_cond_timedwait(&g_ds_cv, &g_ds_mu, &until);
    }
    pthread_mutex_unlock(&g_ds_mu);
    if (fd >= 0) ds_close(fd); /* no last clear: Discord clears the status when the socket closes */
    return NULL;
}

                                                                                                                     
                                                                                          
static int ds_set_want(int want, char *line, size_t size) {
    if (want != DS_OFF && !g_ds_started) {
        g_ds_session_start = time(NULL);
        if (pthread_create(&g_ds_thread, NULL, ds_worker, NULL) == 0) g_ds_started = 1;
        else { snprintf(line, size, "no worker thread; off for this session"); return 1; }
    }
    atomic_store(&g_ds_want, want);
    static int logged;
    if (atomic_load(&g_ds_connected) != logged) {
        logged = atomic_load(&g_ds_connected);
        snprintf(line, size, logged ? "connected" : "disconnected");
        return 1;
    }
    pthread_mutex_lock(&g_ds_mu);
    const int noted = g_ds_note[0] != 0;
    if (noted) { snprintf(line, size, "%s", g_ds_note); g_ds_note[0] = 0; }
    pthread_mutex_unlock(&g_ds_mu);
    return noted;
}

static void discord_status_stop(void) {
    if (!g_ds_started) return;
    pthread_mutex_lock(&g_ds_mu);
    g_ds_stop = 1;
    if (g_ds_fd >= 0) shutdown(g_ds_fd, SHUT_RDWR); /* wakes a send or receive waiting on a hung Discord */
    pthread_cond_signal(&g_ds_cv);
    pthread_mutex_unlock(&g_ds_mu);
    pthread_join(g_ds_thread, NULL);
    g_ds_started = 0;
}

#endif
