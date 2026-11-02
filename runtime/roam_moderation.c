#include "roam_moderation.h"
#include "roam_core.h"
#include "roam_auth.h"
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>
typedef struct { uint8_t id[32]; uint64_t expiry; } Ban;
typedef struct { int fd; size_t used; uint64_t start; char input[ROAM_ADMIN_INPUT+1]; } Admin;
struct RoamModeration {
    int state, admin, listener, count;
    uint64_t next_expiry, last_cleanup_attempt;
    char socket_path[sizeof(((struct sockaddr_un *)0)->sun_path)];
    Ban bans[ROAM_BANS_MAX];
    Admin clients[4];
};
uint64_t roam_moderation_utc(void) { time_t now = time(NULL); return now > 0 ? (uint64_t)now : 0; }
static int safe(int fd, int directory) {
    struct stat st;
    return !fstat(fd, &st) && st.st_uid == geteuid() && !(st.st_mode & 077) &&
        (directory ? S_ISDIR(st.st_mode) && (st.st_mode & 0700) == 0700 : S_ISREG(st.st_mode) && st.st_nlink == 1);
}
static int decimal(const char *s, uint64_t max, uint64_t *out) {
    uint64_t n = 0;
    if (!*s) return 0;
    for (; *s; ++s) {
        unsigned d = (unsigned char)*s - '0';
        if (d > 9 || n > max/10 || (n == max/10 && d > max%10)) return 0;
        n = n*10 + d;
    }
    *out = n; return 1;
}
static int load(RoamModeration *m) {
    int fd = openat(m->state, "bans", O_RDONLY|O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC);
    if (fd < 0) return errno == ENOENT;
    struct stat st; int ok = 0;
    // 64 lowercase hex, space, up to 20 decimal digits, newline. Never allocate from file size.
    if (!safe(fd, 0)) { errno = EACCES; goto done; }
    if (fstat(fd, &st) || st.st_size > ROAM_BANS_MAX*86 || st.st_size < 1) { errno = EINVAL; goto done; }
    FILE *f = fdopen(fd, "r"); if (!f) goto done;
    char line[88];
    while (fgets(line, sizeof line, f)) {
        if (!m->count && !strcmp(line, "# empty\n")) {
            int c = fgetc(f); if (c != EOF || ferror(f)) goto corrupt;
            fclose(f); return 1;
        }
        size_t n = strlen(line); uint64_t expiry;
        if (m->count == ROAM_BANS_MAX || n < 67 || line[n-1] != '\n' || line[64] != ' ') goto corrupt;
        line[64] = line[n-1] = 0;
        Ban *b = &m->bans[m->count];
        if (!roam_auth_unhex(line, b->id) || !decimal(line+65, UINT64_MAX, &expiry) || !expiry) goto corrupt;
        for (int i = 0; i < m->count; ++i) if (!memcmp(m->bans[i].id, b->id, 32)) goto corrupt;
        b->expiry = expiry; m->count++;
    }
    if (ferror(f)) goto corrupt;
    fclose(f); return 1;
corrupt:
    fclose(f); errno = EINVAL; return 0;
done: { int saved = errno; close(fd); errno = saved; return ok; }
}
static int save(RoamModeration *m, const Ban *bans, int count) {
    // Refuse changed permissions, symlinks or concurrent/left-over staging files explicitly.
    if (!safe(m->state, 1)) { errno = EACCES; return 0; }
    struct stat old;
    if (!fstatat(m->state, "bans", &old, AT_SYMLINK_NOFOLLOW)) {
        if (!S_ISREG(old.st_mode) || old.st_uid != geteuid() || (old.st_mode & 077) || old.st_nlink != 1) { errno = EACCES; return 0; }
    } else if (errno != ENOENT) return 0;
    int fd = openat(m->state, "bans.tmp", O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC, 0600);
    if (fd < 0) return 0;
    int ok = 1;
    // An empty valid state is an explicit format marker, never a zero-byte partial write.
    if (!count && write(fd, "# empty\n", 8) != 8) ok = 0;
    for (int i = 0; i < count && ok; ++i) {
        char hex[65], line[88]; roam_auth_hex(bans[i].id, hex);
        int n = snprintf(line, sizeof line, "%s %" PRIu64 "\n", hex, bans[i].expiry);
        if (n <= 0 || n >= (int)sizeof line || write(fd, line, (size_t)n) != n) ok = 0;
    }
    if (ok && fsync(fd)) ok = 0;
    if (close(fd)) ok = 0;
    if (ok && renameat(m->state, "bans.tmp", m->state, "bans")) ok = 0;
    if (!ok) { int saved = errno; unlinkat(m->state, "bans.tmp", 0); errno = saved; return 0; }
    // Rename has committed: keep enforcement in memory even if directory fsync fails.
    memcpy(m->bans, bans, (size_t)count*sizeof *bans); m->count = count;
    m->next_expiry = 0;
    for (int i = 0; i < count; ++i) if (!m->next_expiry || bans[i].expiry < m->next_expiry) m->next_expiry = bans[i].expiry;
    return !fsync(m->state);
}
RoamModeration *roam_moderation_open(const char *state, const char *admin) {
    if (!state || !admin || !*state || !*admin) { errno = EINVAL; return NULL; }
    RoamModeration *m = calloc(1, sizeof *m); if (!m) return NULL;
    m->state = m->admin = m->listener = -1;
    for (int i = 0; i < 4; ++i) m->clients[i].fd = -1;
    m->state = open(state, O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
    m->admin = open(admin, O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
    if (m->state < 0 || m->admin < 0) goto fail;
    if (!safe(m->state, 1) || !safe(m->admin, 1)) { errno = EACCES; goto fail; }
    if (flock(m->state, LOCK_EX|LOCK_NB)) goto fail;
    if (!load(m)) goto fail;
    uint64_t now = roam_moderation_utc();
    if (!now) { errno = EIO; goto fail; }
    int active = 0;
    Ban retained[ROAM_BANS_MAX];
    for (int i = 0; i < m->count; ++i) if (m->bans[i].expiry > now) retained[active++] = m->bans[i];
    if (active != m->count && !save(m, retained, active)) goto fail;
    for (int i = 0; i < m->count; ++i) if (!m->next_expiry || m->bans[i].expiry < m->next_expiry) m->next_expiry = m->bans[i].expiry;
    struct sockaddr_un a = {.sun_family = AF_UNIX};
    int n = snprintf(a.sun_path, sizeof a.sun_path, "%s/admin.sock", admin);
    if (n < 0 || n >= (int)sizeof a.sun_path) { errno = ENAMETOOLONG; goto fail; }
    m->listener = socket(AF_UNIX, SOCK_STREAM, 0);
    if (m->listener < 0) goto fail;
    if (fcntl(m->listener, F_SETFL, O_NONBLOCK) || fcntl(m->listener, F_SETFD, FD_CLOEXEC)) goto fail;
    struct stat st;
    if (!fstatat(m->admin, "admin.sock", &st, AT_SYMLINK_NOFOLLOW)) {
        if (!S_ISSOCK(st.st_mode) || st.st_uid != geteuid() || (st.st_mode & 077)) { errno = EACCES; goto fail; }
        // Never remove a live listener's socket. Stale sockets can be recovered after a crash.
        if (!connect(m->listener, (struct sockaddr *)&a, sizeof a) || (errno != ECONNREFUSED && errno != ENOENT)) { errno = EADDRINUSE; goto fail; }
        if (unlinkat(m->admin, "admin.sock", 0)) goto fail;
    } else if (errno != ENOENT) goto fail;
    mode_t mask = umask(0077);
    int bound = bind(m->listener, (struct sockaddr *)&a, sizeof a);
    umask(mask);
    if (bound) goto fail;
    memcpy(m->socket_path, a.sun_path, (size_t)n+1);
    if (chmod(a.sun_path, 0600) || listen(m->listener, 4)) goto fail;
    return m;
fail: {
    int saved = errno; roam_moderation_close(m); errno = saved; return NULL;
    }
}
void roam_moderation_close(RoamModeration *m) {
    if (!m) return;
    for (int i = 0; i < 4; ++i) if (m->clients[i].fd >= 0) close(m->clients[i].fd);
    if (m->listener >= 0) close(m->listener);
    if (*m->socket_path && m->admin >= 0) unlinkat(m->admin, "admin.sock", 0);
    if (m->state >= 0) close(m->state);
    if (m->admin >= 0) close(m->admin);
    memset(m, 0, sizeof *m); free(m);
}
int roam_moderation_banned(RoamModeration *m, const uint8_t id[32], uint64_t now) {
    if (!m) return 0;
    for (int i = 0; i < m->count; ++i) if (!memcmp(m->bans[i].id, id, 32) && (!now || m->bans[i].expiry > now)) return 1;
    return 0;
}
int roam_moderation_ban(RoamModeration *m, const uint8_t id[32], uint64_t expiry, uint64_t now) {
    if (!m || !now || expiry <= now || expiry-now > ROAM_BAN_MAX_SECONDS) { errno = EINVAL; return 0; }
    Ban next[ROAM_BANS_MAX]; int n = 0;
    for (int i = 0; i < m->count; ++i) if (m->bans[i].expiry > now && memcmp(m->bans[i].id, id, 32)) next[n++] = m->bans[i];
    if (n == ROAM_BANS_MAX) { errno = ENOSPC; return 0; }
    memcpy(next[n].id, id, 32); next[n++].expiry = expiry;
    return save(m, next, n);
}
int roam_moderation_unban(RoamModeration *m, const uint8_t id[32], uint64_t now) {
    Ban next[ROAM_BANS_MAX]; int n = 0;
    if (!m || !now) { errno = EINVAL; return 0; }
    for (int i = 0; i < m->count; ++i) if (m->bans[i].expiry > now && memcmp(m->bans[i].id, id, 32)) next[n++] = m->bans[i];
    return save(m, next, n);
}
int roam_moderation_command(RoamModeration *m, RoamRelay *r, const char *line, size_t n, char out[ROAM_ADMIN_OUTPUT]) {
    char input[ROAM_ADMIN_INPUT+1]; uint64_t number = 0, seconds = 0;
    if (!n || n > ROAM_ADMIN_INPUT || line[n-1] != '\n') goto invalid;
    for (size_t i = 0; i < n-1; ++i) if ((unsigned char)line[i] < 32 || (unsigned char)line[i] > 126) goto invalid;
    memcpy(input, line, n-1); input[n-1] = 0;
    if (!strncmp(input, "list ", 5) && decimal(input+5, 65535, &number)) return roam_relay_list(r, (uint16_t)number, out, ROAM_ADMIN_OUTPUT);
    if (!strncmp(input, "kick ", 5)) {
        uint8_t id[32]; int removed;
        if (roam_auth_unhex(input+5, id)) removed = roam_relay_kick_identity(r, id);
        else if (decimal(input+5, 65535, &number) && number) removed = roam_relay_kick(r, (uint16_t)number);
        else goto invalid;
        return snprintf(out, ROAM_ADMIN_OUTPUT, removed ? "OK\n" : "ERR session\n");
    }
    if (!strncmp(input, "ban ", 4)) {
        char *space = strchr(input+4, ' ');
        if (!space) goto invalid;
        *space++ = 0;
        if (!decimal(space, ROAM_BAN_MAX_SECONDS, &seconds) || !seconds) goto invalid;
        uint8_t id[32]; uint64_t now = roam_moderation_utc();
        if (!roam_auth_unhex(input+4, id)) {
            if (!decimal(input+4, 65535, &number) || !number) goto invalid;
            if (!roam_relay_identity(r, (uint16_t)number, id)) return snprintf(out, ROAM_ADMIN_OUTPUT, "ERR session\n");
        }
        if (!roam_moderation_ban(m, id, now+seconds, now)) {
            // A rename may have committed despite a directory sync failure. Always enforce current state.
            roam_relay_enforce_bans(r);
            return snprintf(out, ROAM_ADMIN_OUTPUT, "ERR storage %d\n", errno);
        }
        roam_relay_enforce_bans(r); return snprintf(out, ROAM_ADMIN_OUTPUT, "OK\n");
    }
    if (!strncmp(input, "unban ", 6)) {
        uint8_t id[32]; if (!roam_auth_unhex(input+6, id)) goto invalid;
        if (!roam_moderation_unban(m, id, roam_moderation_utc())) return snprintf(out, ROAM_ADMIN_OUTPUT, "ERR storage %d\n", errno);
        return snprintf(out, ROAM_ADMIN_OUTPUT, "OK\n");
    }
invalid: return snprintf(out, ROAM_ADMIN_OUTPUT, "ERR syntax\n");
}
void roam_moderation_service(RoamModeration *m, RoamRelay *r, uint64_t now) {
    if (!m) return;
    uint64_t utc = roam_moderation_utc();
    if (m->next_expiry && utc >= m->next_expiry && (!m->last_cleanup_attempt || now-m->last_cleanup_attempt >= 10000)) {
        m->last_cleanup_attempt = now;
        Ban retained[ROAM_BANS_MAX]; int count = 0;
        for (int i = 0; i < m->count; ++i) if (m->bans[i].expiry > utc) retained[count++] = m->bans[i];
        if (!save(m, retained, count)) fprintf(stderr, "roam_relay: expiry cleanup failed (error %d)\n", errno);
    }
    for (int i = 0; i < 4; ++i) {
        Admin *c = &m->clients[i];
        if (c->fd < 0) {
            c->fd = accept(m->listener, NULL, NULL);
            if (c->fd < 0) continue;
            if (fcntl(c->fd, F_SETFL, O_NONBLOCK) || fcntl(c->fd, F_SETFD, FD_CLOEXEC)) { close(c->fd); c->fd = -1; continue; }
#ifdef __APPLE__
            uid_t uid; gid_t gid;
            int trusted = !getpeereid(c->fd, &uid, &gid) && uid == geteuid();
#else
            struct { pid_t pid; uid_t uid; gid_t gid; } credential;
            socklen_t length = sizeof credential;
            int trusted = !getsockopt(c->fd, SOL_SOCKET, SO_PEERCRED, &credential, &length) && length == sizeof credential && credential.uid == geteuid();
#endif
            if (!trusted) { close(c->fd); c->fd = -1; continue; }
            c->used = 0; c->start = now;
        }
        ssize_t got = recv(c->fd, c->input+c->used, ROAM_ADMIN_INPUT+1-c->used, 0);
        if (got > 0) c->used += (size_t)got;
        int complete = memchr(c->input, '\n', c->used) != NULL;
        if (complete || c->used > ROAM_ADMIN_INPUT) {
            char out[ROAM_ADMIN_OUTPUT];
            int n = roam_moderation_command(m, r, c->input, c->used, out);
#ifdef MSG_NOSIGNAL
            (void)send(c->fd, out, (size_t)n, MSG_DONTWAIT|MSG_NOSIGNAL);
#else
            int one = 1; setsockopt(c->fd, SOL_SOCKET, SO_NOSIGPIPE, &one, sizeof one);
            (void)send(c->fd, out, (size_t)n, MSG_DONTWAIT);
#endif
        } else if (got != 0 && (got >= 0 || errno == EAGAIN || errno == EWOULDBLOCK) && now-c->start < 2000) continue;
        close(c->fd); c->fd = -1; c->used = 0;
    }
}
