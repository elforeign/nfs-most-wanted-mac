// Embedded relay lifecycle; the relay keeps working while the game/menu is paused.
#include "roam_online.h"
#include "roam_core.h"
#include <arpa/inet.h>
#include <errno.h>
#include <poll.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>
struct RoamServer { int fd; uint16_t port; pthread_t thread; atomic_int stop; RoamRelay *relay; };
static void *serve(void *arg) {
    RoamServer *s = arg;
    while (!atomic_load(&s->stop)) {
        struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts);
        roam_relay_service(s->relay, (uint32_t)((uint64_t)ts.tv_sec * 1000 + ts.tv_nsec / 1000000));
        struct pollfd p = {.fd = s->fd, .events = POLLIN}; poll(&p, 1, 10);
    }
    return NULL;
}
RoamServer *roam_server_start(uint32_t ip, uint16_t port) {
    RoamServer *s = calloc(1, sizeof *s); if (!s) return NULL;
    atomic_init(&s->stop, 0);
    s->fd = socket(AF_INET, SOCK_DGRAM, 0); if (s->fd < 0) { free(s); return NULL; }
    struct sockaddr_in a = {.sin_family = AF_INET, .sin_port = htons(port), .sin_addr.s_addr = htonl(ip)};
    if (bind(s->fd, (struct sockaddr *)&a, sizeof a)) goto fail;
    socklen_t n = sizeof a; if (getsockname(s->fd, (struct sockaddr *)&a, &n)) goto fail;
    s->port = ntohs(a.sin_port); s->relay = roam_relay_new(s->fd, ROAM_NEAR_DEFAULT, 1024);
    if (!s->relay) goto fail;
    int err = pthread_create(&s->thread, NULL, serve, s);
    if (err) { errno = err; goto fail; }
    return s;
fail: {
    int err = errno; roam_relay_free(s->relay); close(s->fd); free(s); errno = err; return NULL;
    }
}
uint16_t roam_server_port(RoamServer *s) { return s->port; }
void roam_server_stop(RoamServer *s) {
    if (!s) return;
    atomic_store(&s->stop, 1); pthread_join(s->thread, NULL);
    roam_relay_free(s->relay); close(s->fd); free(s);
}

int roam_server_probe(uint32_t ip, uint16_t port, const atomic_int *stop) {
    int fd = socket(AF_INET, SOCK_DGRAM, 0); if (fd < 0) return 0;
    struct sockaddr_in to = {.sin_family = AF_INET, .sin_port = htons(port), .sin_addr.s_addr = htonl(ip)};
    if (connect(fd, (struct sockaddr *)&to, sizeof to)) { close(fd); return 0; }
    RoamClient c; roam_client_init(&c, UINT32_MAX, "probe");
    uint8_t b[ROAM_MAX_DGRAM]; int n = roam_client_hello(&c, b);
    if (send(fd, b, (size_t)n, 0) == n) {
        for (int i = 0; i < 16 && !atomic_load(stop) && !c.welcomed; ++i) {
            struct pollfd p = {.fd = fd, .events = POLLIN};
            if (poll(&p, 1, 50) <= 0) continue;
            int got = (int)recv(fd, b, sizeof b, 0);
            if (got > 0) roam_client_input(&c, b, got, 0);
        }
    }
    n = roam_client_bye(&c, b); send(fd, b, (size_t)n, 0); close(fd); return c.welcomed;
}
