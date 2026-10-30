                                                                               
#include "roam_online.h"
#include "natmap.h"
#include <arpa/inet.h>
#include <errno.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
void roam_game_wake(void);
static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_t mapping_thread;
static int mapping_started, mode, target_kind, welcomed, players, session_error, hairpin;
static atomic_int stopping;
static RoamServer *server;
static NatMap mapping;
static char status[512] = "Off", action_error[128], code[ROAM_CODE_SIZE], target_host[64], manual_ip[64];
static int target_port;
static uint32_t target_room;
static void set_target(int kind, uint32_t ip, uint16_t port, uint32_t room) {
    struct in_addr a = {.s_addr = htonl(ip)};
    target_kind = kind; inet_ntop(AF_INET, &a, target_host, sizeof target_host);
    target_port = port; target_room = room;
}
// Classify the router's address, even when a manual public address supplies the code.
static int upstream_class(uint32_t ip) {
    if ((ip >> 24) == 10 || (ip >> 20) == 0xac1 || (ip >> 16) == 0xc0a8) return 1;
    if ((ip >> 22) == 0x191) return 2; // 100.64.0.0/10
    return 0;
}
static void publish_host(int renewed) {
    uint32_t external = mapping.external_ip; struct in_addr a;
    if (*manual_ip && inet_pton(AF_INET, manual_ip, &a) == 1) external = ntohl(a.s_addr);
    int upstream = upstream_class(mapping.external_ip);
    pthread_mutex_lock(&lock);
    if (!external || roam_code_encode(external, mapping.external_port, code)) {
        *code = 0;
        snprintf(status, sizeof status, "Public IPv4 unavailable. Set up forwarding, enter Public IPv4 (optional), then Host again.");
    } else {
        char local[INET_ADDRSTRLEN]; a.s_addr = htonl(mapping.local_ip); inet_ntop(AF_INET, &a, local, sizeof local);
        const char *protocol = mapping.protocol == 1 ? "NAT-PMP" : mapping.protocol == 2 ? "PCP" : mapping.protocol == 3 ? "UPnP" : "unknown";
        if (!mapping.mapped || !renewed) snprintf(status, sizeof status,
            "Hosting: automatic router setup failed. Try another host, or forward UDP %u to %s. See Guide.%s",
            mapping.internal_port, local, upstream ? " Possible double NAT / carrier NAT." : "");
        else snprintf(status, sizeof status,
            "Hosting: UDP port mapped by %s. Internet joining is untested; ask a friend on another connection to join.%s Router loopback: %s; this does not confirm internet reachability.",
            protocol, upstream ? " Possible double NAT / carrier NAT; try another host. See Guide." : "", hairpin ? "available" : "unavailable");
        set_target(1, 0x7f000001u, mapping.internal_port, roam_code_room(external, mapping.external_port));
    }
    fprintf(stderr, "roam: host mapped=%d renewed=%d protocol=%d upstream_class=%d loopback=%d\n",
            mapping.mapped, renewed, mapping.protocol, upstream, hairpin);
    pthread_mutex_unlock(&lock); roam_game_wake();
}
static uint64_t renewal_ms(uint32_t lifetime) {
    uint64_t half = (uint64_t)lifetime * 500u;
    return half < 1000 ? 1000 : half;
}
static uint64_t monotonic_ms(void) {
    struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint64_t)t.tv_sec * 1000 + t.tv_nsec / 1000000;
}
static void *map_worker(void *unused) {
    (void)unused;
    int ok = natmap_open(&mapping, ROAM_CODE_PORT, &stopping) == 0;
    uint64_t next_renewal = monotonic_ms() + renewal_ms(mapping.lifetime);
    if (ok && !atomic_load(&stopping)) {
        uint32_t ip = mapping.external_ip; struct in_addr a;
        if (*manual_ip && inet_pton(AF_INET, manual_ip, &a) == 1) ip = ntohl(a.s_addr);
        hairpin = roam_server_probe(ip, mapping.external_port, &stopping);
    }
    if (!atomic_load(&stopping)) publish_host(ok);
    while (!atomic_load(&stopping)) {
        struct timespec t = {.tv_sec = 0, .tv_nsec = 100000000}; nanosleep(&t, NULL);
        if (!mapping.mapped || monotonic_ms() < next_renewal) continue;
        if (natmap_renew(&mapping, &stopping)) {
            pthread_mutex_lock(&lock);
            snprintf(status, sizeof status, "Router mapping could not be renewed. New joins may fail. Choose Leave, then Host.");
            fprintf(stderr, "roam: host renewal failed protocol=%d\n", mapping.protocol);
            pthread_mutex_unlock(&lock);
            break;
        }
        next_renewal = monotonic_ms() + renewal_ms(mapping.lifetime);
    }
    return NULL;
}
void roam_online_leave(void) {
    atomic_store(&stopping, 1);
    if (mapping_started) { pthread_join(mapping_thread, NULL); mapping_started = 0; }
    natmap_close(&mapping); memset(&mapping, 0, sizeof mapping);
    roam_server_stop(server); server = NULL;
    pthread_mutex_lock(&lock);
    mode = welcomed = players = session_error = hairpin = 0; *code = 0;
    action_error[0] = 0; snprintf(status, sizeof status, "Off"); set_target(-1, 0, 0, 0);
    pthread_mutex_unlock(&lock); roam_game_wake();
}
static void cleanup(void) { roam_online_leave(); }
static void init_cleanup(void) { static int once; if (!once) { atexit(cleanup); once = 1; } }
void roam_online_host(const char *public_ip) {
    if (public_ip && *public_ip) {
        struct in_addr a;
        if (inet_pton(AF_INET, public_ip, &a) != 1 || !ntohl(a.s_addr)) {
            pthread_mutex_lock(&lock); snprintf(action_error, sizeof action_error, "Public IPv4 must look like 203.0.113.5."); pthread_mutex_unlock(&lock); return;
        }
    }
    roam_online_leave(); init_cleanup();
    snprintf(manual_ip, sizeof manual_ip, "%s", public_ip ? public_ip : "");
    const char *loopback = getenv("NFS_LAN_LOOPBACK");
    int loopback_only = loopback && !strcmp(loopback, "1");
    server = roam_server_start(loopback_only ? INADDR_LOOPBACK : INADDR_ANY, ROAM_CODE_PORT);
    if (!server) { pthread_mutex_lock(&lock); snprintf(status, sizeof status, "Cannot host on UDP 47800: %s", strerror(errno)); pthread_mutex_unlock(&lock); return; }
    atomic_store(&stopping, 0);
    pthread_mutex_lock(&lock); mode = 1; snprintf(status, sizeof status, "Hosting: checking router setup..."); pthread_mutex_unlock(&lock);
    if (loopback_only) {
        mapping.internal_port = mapping.external_port = ROAM_CODE_PORT;
        mapping.local_ip = mapping.external_ip = INADDR_LOOPBACK;
        publish_host(0); return;
    }
    int err = pthread_create(&mapping_thread, NULL, map_worker, NULL);
    if (err) {
        roam_server_stop(server); server = NULL;
        pthread_mutex_lock(&lock); mode = 0; snprintf(status, sizeof status, "Cannot start router setup: %s", strerror(err)); pthread_mutex_unlock(&lock);
    } else mapping_started = 1;
}
void roam_online_join(const char *text) {
    uint32_t ip; uint16_t port;
    if (roam_code_decode(text, &ip, &port)) {
        pthread_mutex_lock(&lock); snprintf(action_error, sizeof action_error, "Invalid code. Use nine letters/numbers, like XXXX-XXXXX."); pthread_mutex_unlock(&lock); return;
    }
    roam_online_leave(); init_cleanup();
    pthread_mutex_lock(&lock); mode = 2; snprintf(status, sizeof status, "Joining...");
    set_target(1, ip, port, roam_code_room(ip, port)); pthread_mutex_unlock(&lock); roam_game_wake();
}
int roam_online_target(char host[64], int *port, uint32_t *room) {
    pthread_mutex_lock(&lock); int kind = target_kind; target_kind = 0;
    if (kind == 1) { snprintf(host, 64, "%s", target_host); *port = target_port; *room = target_room; }
    pthread_mutex_unlock(&lock); return kind;
}
void roam_online_players(int w, int p, int error) {
    pthread_mutex_lock(&lock);
    if (welcomed != w || session_error != error)
        fprintf(stderr, "roam: join status %s players %d error %d\n", error ? "failed" : w ? "reachable" : "waiting", p, error);
    welcomed = w; players = p; session_error = error; pthread_mutex_unlock(&lock);
}
void roam_online_status(char *out, int n) {
    pthread_mutex_lock(&lock);
    if (*action_error) snprintf(out, n, "%s", action_error);
    else if (session_error) snprintf(out, n, "%s", session_error == 1 ? "Relay uses a different game version." :
        session_error == 2 ? "This host is full." : session_error == 3 ? "Cannot open connection to host." : "Host did not answer. Check that hosting is active, verify the code, then check host permissions/router setup or swap hosts.");
    else if (mode != 1 && welcomed) snprintf(out, n, "Reachable: joined %d players", players);
    else snprintf(out, n, "%s", status);
    pthread_mutex_unlock(&lock);
}

void roam_online_host_code(char *out, int n) {
    pthread_mutex_lock(&lock);
    snprintf(out, n, "%s", mode == 1 ? code : "");
    pthread_mutex_unlock(&lock);
}
