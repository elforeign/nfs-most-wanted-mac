// Standalone online free roam relay and local control client; no game assets.
#include "roam_core.h"
#include "roam_moderation.h"

#include <arpa/inet.h>
#include <errno.h>
#include <inttypes.h>
#include <netinet/in.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

static volatile sig_atomic_t stopping;
static void stop_relay(int sig) { stopping = sig; }

static uint64_t now_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint64_t)t.tv_sec * 1000 + (uint64_t)t.tv_nsec / 1000000;
}

// Decimal digits only: no signs, whitespace, suffixes or overflowing intermediate values.
static int number(const char *s, unsigned lo, unsigned hi, int *out) {
    unsigned n = 0;
    if (!*s) return 0;
    for (; *s; ++s) {
        if (*s < '0' || *s > '9') return 0;
        unsigned d = (unsigned)(*s - '0');
        if (n > hi / 10 || (n == hi / 10 && d > hi % 10)) return 0;
        n = n * 10 + d;
    }
    if (n < lo) return 0;
    *out = (int)n;
    return 1;
}

static void usage(void) {
    fprintf(stderr, "usage: roam_relay [--bind IPv4] [--port 0..65535] [--near 1..32] "
                    "[--max 1..65000] [--seconds 0..86400] [--state-dir PATH --admin-dir PATH]\n       roam_relay --control PATH --request COMMAND\n");
}

// A local client keeps administration usable in the minimal production image.
// Validate all caller-controlled lengths before opening an AF_UNIX socket.
static int control_client(const char *path, const char *request) {
    struct sockaddr_un a = {.sun_family = AF_UNIX};
    size_t n = strlen(request), path_n = strlen(path);
    if (!n || n >= ROAM_ADMIN_INPUT || !path_n || path_n >= sizeof a.sun_path) return 2;
    for (size_t i = 0; i < n; ++i) if ((unsigned char)request[i] < 32 || (unsigned char)request[i] > 126) return 2;
    memcpy(a.sun_path, path, path_n+1);
    struct stat st;
    if (lstat(path, &st) || !S_ISSOCK(st.st_mode) || st.st_uid != geteuid() || (st.st_mode & 077)) {
        fprintf(stderr, "roam_relay: unsafe/unavailable local control socket\n"); return 1;
    }
    if (!roam_auth_no_dumps()) return 1;
    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) { perror("roam_relay: local control"); return 1; }
    struct timeval timeout = {.tv_sec = 2};
    int flags = 0;
#ifdef MSG_NOSIGNAL
    flags = MSG_NOSIGNAL;
#else
    int one = 1;
    if (setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &one, sizeof one)) goto error;
#endif
    if (setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof timeout) ||
        setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof timeout) ||
        connect(fd, (struct sockaddr *)&a, sizeof a)) goto error;
    char line[ROAM_ADMIN_INPUT]; memcpy(line, request, n); line[n++] = '\n';
    size_t sent = 0;
    while (sent < n) {
        ssize_t got = send(fd, line+sent, n-sent, flags);
        if (got < 0 && errno == EINTR) continue;
        if (got <= 0) goto error;
        sent += (size_t)got;
    }
    char response[ROAM_ADMIN_OUTPUT]; size_t used = 0;
    for (;;) {
        ssize_t got = recv(fd, response+used, sizeof response-used, 0);
        if (got < 0 && errno == EINTR) continue;
        if (got < 0) goto error;
        if (!got) break;
        used += (size_t)got;
        if (used == sizeof response) goto error;
    }
    close(fd);
    if (!used || response[used-1] != '\n') { fprintf(stderr, "roam_relay: incomplete local control reply\n"); return 1; }
    if (fwrite(response, 1, used, stdout) != used) return 1;
    return used >= 4 && !memcmp(response, "ERR ", 4) ? 1 : 0;
error: {
    int saved = errno; close(fd); fprintf(stderr, "roam_relay: local control failed (error %d)\n", saved); return 1;
    }
}

int main(int argc, char **argv) {
    if (argc == 5 && !strcmp(argv[1], "--control") && !strcmp(argv[3], "--request")) return control_client(argv[2], argv[4]);
    const char *bind_ip = "127.0.0.1", *state_dir = NULL, *admin_dir = NULL;
    int port = ROAM_PORT, near = ROAM_NEAR_DEFAULT, max = 100, seconds = 0;
    unsigned seen = 0;
    for (int i = 1; i < argc; i += 2) {
        int *dest = NULL;
        unsigned lo = 0, hi = 0, bit = 0;
        if (!strcmp(argv[i], "--bind")) bit = 1;
        else if (!strcmp(argv[i], "--port")) { bit = 2; dest = &port; hi = 65535; }
        else if (!strcmp(argv[i], "--near")) { bit = 4; dest = &near; lo = 1; hi = ROAM_INTEREST_MAX; }
        else if (!strcmp(argv[i], "--max")) { bit = 8; dest = &max; lo = 1; hi = 65000; }
        else if (!strcmp(argv[i], "--state-dir")) bit = 32;
        else if (!strcmp(argv[i], "--admin-dir")) bit = 64;
        else if (!strcmp(argv[i], "--seconds")) { bit = 16; dest = &seconds; hi = 86400; }
        if (!bit || (seen & bit) || i + 1 == argc) {
            fprintf(stderr, "roam_relay: invalid, duplicate or missing option\n");
            usage(); return 2;
        }
        seen |= bit;
        if (bit == 1) bind_ip = argv[i + 1];
        else if (bit == 32) state_dir = argv[i + 1];
        else if (bit == 64) admin_dir = argv[i + 1];
        else if (!number(argv[i + 1], lo, hi, dest)) {
            fprintf(stderr, "roam_relay: invalid value for %s\n", argv[i]);
            usage(); return 2;
        }
    }
    if (!!state_dir != !!admin_dir || (state_dir && (!*state_dir || !*admin_dir))) {
        fprintf(stderr, "roam_relay: --state-dir and --admin-dir require nonempty paired values\n"); return 2;
    }
    struct sockaddr_in a = {.sin_family = AF_INET, .sin_port = htons((uint16_t)port)};
    if (inet_pton(AF_INET, bind_ip, &a.sin_addr) != 1) {
        fprintf(stderr, "roam_relay: --bind requires a numeric IPv4 address\n"); return 2;
    }
    // Install before opening the listener. The handler only stores a sig_atomic_t.
    struct sigaction sa = {0};
    sa.sa_handler = stop_relay;
    sigemptyset(&sa.sa_mask);
    if (sigaction(SIGINT, &sa, NULL) || sigaction(SIGTERM, &sa, NULL)) {
        perror("roam_relay: sigaction"); return 1;
    }
    if (!roam_auth_no_dumps()) { fprintf(stderr, "roam_relay: dump protection failed\n"); return 1; }
    RoamModeration *moderation = state_dir ? roam_moderation_open(state_dir, admin_dir) : NULL;
    if (state_dir && !moderation) { fprintf(stderr, "roam_relay: moderation storage/control failed (error %d)\n", errno); return 1; }
    int fd = socket(AF_INET, SOCK_DGRAM, 0);
    if (fd < 0) { perror("roam_relay: socket"); roam_moderation_close(moderation); return 1; }
    if (bind(fd, (struct sockaddr *)&a, sizeof a)) {
        perror("roam_relay: bind"); close(fd); roam_moderation_close(moderation); return 1;
    }
    socklen_t alen = sizeof a;
    if (getsockname(fd, (struct sockaddr *)&a, &alen)) {
        perror("roam_relay: getsockname"); close(fd); roam_moderation_close(moderation); return 1;
    }
    RoamRelay *r = roam_relay_new(fd, near, max);
    if (!r) { fprintf(stderr, "roam_relay: initialization failed\n"); close(fd); roam_moderation_close(moderation); return 1; }
    roam_relay_moderation(r, moderation);
    printf("roam_relay: listening port %u near %d max %d seconds %d (wire v%d)\n",
            (unsigned)ntohs(a.sin_port), near, max, seconds, ROAM_VERSION);
    fflush(stdout);
    uint64_t start = now_ms(), last_print = start;
    int rc = 0;
    while (!stopping) {
        struct pollfd p = {.fd = fd, .events = POLLIN};
        int ready = poll(&p, 1, 1000 / ROAM_HZ / 4);
        if (stopping) break;
        if (ready < 0 && errno != EINTR) { perror("roam_relay: poll"); rc = 1; break; }
        // UDP peer errors can arrive asynchronously; service drains them without stopping the relay.
        if (p.revents & (POLLHUP | POLLNVAL)) {
            fprintf(stderr, "roam_relay: listener failed\n"); rc = 1; break;
        }
        uint64_t t = now_ms();
        roam_relay_service(r, (uint32_t)t);
        roam_moderation_service(moderation, r, t);
        if (t - last_print >= 10000) {
            RoamRelayStats s = roam_relay_stats(r);
            printf("roam_relay: clients %" PRIu64 " rx %" PRIu64 " rate-dropped %" PRIu64
                   " bad %" PRIu64 " version %" PRIu64 " tx %" PRIu64 " (%" PRIu64
                   " bytes) errors %" PRIu64 " wouldblock %" PRIu64 " timeouts %" PRIu64
                   " max-tick-us %" PRIu64 " signatures %" PRIu64 " auth-rejected %" PRIu64
                   " auth-limited %" PRIu64 " replay-rejected %" PRIu64 " ban-rejected %" PRIu64 " kicks %" PRIu64 "\n", s.clients, s.rx, s.rx_rate_dropped, s.rx_bad,
                   s.rx_version, s.tx, s.tx_bytes, s.tx_errors, s.tx_wouldblock, s.timeouts, s.max_tick_us,
                   s.signature_checks, s.auth_rejected, s.auth_limited, s.replay_rejected, s.ban_rejected, s.kicks);
            fflush(stdout);
            last_print = t;
        }
        if (seconds && t - start >= (uint64_t)seconds * 1000) break;
    }
    roam_relay_free(r);
    close(fd);
    roam_moderation_close(moderation);
    printf("roam_relay: stopped signal %d\n", (int)stopping);
    return rc;
}
