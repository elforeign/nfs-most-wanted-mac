// roam_relay_main.c - standalone online free roam relay.
//   roam_relay [--bind 127.0.0.1] [--port 47800] [--near 32] [--max 1024] [--seconds N]
// The default bind is loopback (same-Mac play and tests). To host friends over the internet, pass --bind 0.0.0.0
// and forward UDP 47800 (or --port) on the router to this machine; game clients only ever send to the relay.
#include "roam_core.h"

#include <arpa/inet.h>
#include <netinet/in.h>
#include <inttypes.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

static uint32_t now_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint32_t)(t.tv_sec * 1000 + t.tv_nsec / 1000000);
}

int main(int argc, char **argv) {
    const char *bind_ip = "127.0.0.1";
    int port = ROAM_PORT, near = ROAM_NEAR_DEFAULT, max = 1024, seconds = 0;
    for (int i = 1; i + 1 < argc; i += 2) {
        if (!strcmp(argv[i], "--bind")) bind_ip = argv[i + 1];
        else if (!strcmp(argv[i], "--port")) port = atoi(argv[i + 1]);
        else if (!strcmp(argv[i], "--near")) near = atoi(argv[i + 1]);
        else if (!strcmp(argv[i], "--max")) max = atoi(argv[i + 1]);
        else if (!strcmp(argv[i], "--seconds")) seconds = atoi(argv[i + 1]);
        else { fprintf(stderr, "roam_relay: unknown option %s\n", argv[i]); return 2; }
    }
    int fd = socket(AF_INET, SOCK_DGRAM, 0);
    struct sockaddr_in a = {.sin_family = AF_INET, .sin_port = htons((uint16_t)port)};
    if (fd < 0 || inet_pton(AF_INET, bind_ip, &a.sin_addr) != 1 || bind(fd, (struct sockaddr *)&a, sizeof a)) {
        perror("roam_relay: bind");
        return 1;
    }
    RoamRelay *r = roam_relay_new(fd, near, max);
    if (!r) { close(fd); return 1; }
    printf("roam_relay: listening on %s:%d near %d max %d (wire v%d)\n", bind_ip, port, near, max, ROAM_VERSION);
    fflush(stdout);
    uint32_t start = now_ms(), last_print = start;
    for (;;) {
        struct pollfd p = {.fd = fd, .events = POLLIN};
        poll(&p, 1, 1000 / ROAM_HZ / 4);
        uint32_t t = now_ms();
        roam_relay_service(r, t);
        if (t - last_print >= 10000) {
            RoamRelayStats s = roam_relay_stats(r);
            printf("roam_relay: clients %" PRIu64 " rx %" PRIu64 " rate-dropped %" PRIu64
                   " bad %" PRIu64 " version %" PRIu64 " tx %" PRIu64 " (%" PRIu64
                   " bytes) errors %" PRIu64 " wouldblock %" PRIu64 " timeouts %" PRIu64
                   " max-tick-us %" PRIu64 "\n", s.clients, s.rx, s.rx_rate_dropped, s.rx_bad,
                   s.rx_version, s.tx, s.tx_bytes, s.tx_errors, s.tx_wouldblock, s.timeouts, s.max_tick_us);
            fflush(stdout);
            last_print = t;
        }
        if (seconds && t - start >= (uint32_t)seconds * 1000) break;
    }
    roam_relay_free(r);
    close(fd);
    return 0;
}
