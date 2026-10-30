// Session-control proof with fake router/server backends: no sockets or router calls.
#include "roam_online.h"
#include "natmap.h"
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
static int checks, failed, starts, stops, closed, scenario;
static atomic_int notifications, renewals;
uint64_t test_renewal_ms(uint32_t lifetime);
static uint32_t bind_ip;
#define CHECK(x) do { ++checks; if (!(x)) { ++failed; printf("FAIL %d: %s\n", __LINE__, #x); } } while (0)
void roam_game_wake(void) { atomic_fetch_add(&notifications, 1); }
RoamServer *roam_server_start(uint32_t ip, uint16_t port) {
    bind_ip = ip; ++starts; CHECK(port == 47800); return (RoamServer *)(uintptr_t)1;
}
int roam_server_probe(uint32_t ip, uint16_t port, const atomic_int *stop) { (void)ip; (void)port; (void)stop; return scenario != 7; }
void roam_server_stop(RoamServer *s) { if (s) ++stops; }
uint16_t roam_server_port(RoamServer *s) { (void)s; return 47800; }
int natmap_open(NatMap *m, uint16_t port, const atomic_int *stop) {
    (void)stop; memset(m, 0, sizeof *m); m->internal_port = m->external_port = port;
    m->local_ip = 0xc0a80109u; m->lifetime = scenario == 3 || scenario == 8 ? 1 : 3600;
    if (scenario != 2) m->external_ip = 0xcb007105u;
    if (!scenario || scenario >= 3) { m->mapped = 1; m->protocol = scenario == 4 ? 2 : scenario == 5 ? 3 : 1; }
    if (scenario == 6) m->external_ip = 0x64400001u;
    return scenario == 1 || scenario == 2 ? -1 : 0;
}
int natmap_renew(NatMap *m, const atomic_int *stop) { (void)m; (void)stop; atomic_fetch_add(&renewals, 1); return scenario == 8 ? -1 : 0; }
void natmap_close(NatMap *m) { if (m->mapped) ++closed; m->mapped = 0; }
static void delay(void) { struct timespec t = {.tv_sec=0, .tv_nsec=10000000}; nanosleep(&t, NULL); }
static int target(char host[64], int *port, uint32_t *room) {
    int action = 0;
    for (int i = 0; i < 200; ++i) { action = roam_online_target(host, port, room); if (action == 1) break; delay(); }
    return action;
}
int main(void) {
    char status[512], host[64], code[ROAM_CODE_SIZE]; int port; uint32_t room;
    roam_online_status(status, sizeof status); CHECK(!strcmp(status, "Off") && starts == 0 && !atomic_load(&notifications));
    CHECK(!roam_online_target(host, &port, &room));
    roam_online_join("bad"); CHECK(starts == 0); roam_online_status(status, sizeof status); CHECK(strstr(status, "Invalid code"));
    roam_code_encode(0xcb007105u, 47803, code); roam_online_join(code);
    CHECK(roam_online_target(host, &port, &room) == 1 && !strcmp(host, "203.0.113.5") && port == 47803 && room == roam_code_room(0xcb007105u, 47803));
    CHECK(starts == 0); roam_online_players(1, 3, 0); roam_online_status(status, sizeof status); CHECK(!strcmp(status, "Reachable: joined 3 players"));
    roam_online_leave(); CHECK(roam_online_target(host, &port, &room) == -1); roam_online_status(status, sizeof status); CHECK(!strcmp(status, "Off"));
    roam_online_host(""); CHECK(starts == 1 && bind_ip == 0); CHECK(target(host, &port, &room) == 1);
    CHECK(!strcmp(host, "127.0.0.1") && port == 47800 && room == roam_code_room(0xcb007105u, 47800));
    roam_online_status(status, sizeof status); CHECK(strstr(status, "UDP port mapped by NAT-PMP") && !strstr(status, "failed"));
    roam_online_host_code(code, sizeof code); CHECK(*code && !strstr(status, code));
    roam_online_leave(); CHECK(stops == 1 && closed == 1);
    scenario = 1; roam_online_host(""); CHECK(target(host, &port, &room) == 1);
    roam_online_status(status, sizeof status); CHECK(strstr(status, "automatic router setup failed") && strstr(status, "forward UDP 47800 to 192.168.1.9"));
    CHECK(!strstr(status, "router blocks it") && strstr(status, "Try another host"));
    roam_online_leave(); CHECK(stops == 2 && closed == 1);
    scenario = 2; roam_online_host("198.51.100.7"); CHECK(target(host, &port, &room) == 1);
    CHECK(room == roam_code_room(0xc6336407u, 47800)); roam_online_leave(); CHECK(stops == 3);
    roam_online_host("");
    for (int i = 0; i < 100; ++i) {
        roam_online_status(status, sizeof status); if (strstr(status, "Public IPv4 unavailable")) break; delay();
    }
    CHECK(strstr(status, "Public IPv4 unavailable") && !strstr(status, "code "));
    CHECK(roam_online_target(host, &port, &room) <= 0); roam_online_leave(); CHECK(stops == 4);
    // Invalid actions preserve an existing session; Leave removes it explicitly.
    roam_online_join(code); CHECK(roam_online_target(host, &port, &room) == 1);
    roam_online_players(1, 3, 0); roam_online_join("bad"); roam_online_status(status, sizeof status); CHECK(strstr(status, "Invalid code"));
    roam_online_host("not an IP"); CHECK(starts == 4); CHECK(!roam_online_target(host, &port, &room));
    roam_online_status(status, sizeof status); CHECK(strstr(status, "Public IPv4"));
    roam_online_join(code); CHECK(roam_online_target(host, &port, &room) == 1);
    roam_online_players(0, 0, 4); roam_online_status(status, sizeof status); CHECK(strstr(status, "Host did not answer") && strstr(status, "swap hosts") && strstr(status, "permissions"));
    roam_online_leave(); CHECK(roam_online_target(host, &port, &room) == -1);
    CHECK(starts == stops && closed == 1);
    const uint32_t life[] = {0,1,2,3,5,59,3600,UINT32_MAX};
    const uint64_t expected[] = {1000,1000,1000,1500,2500,29500,1800000,2147483647500ULL};
    for (unsigned i = 0; i < sizeof life / sizeof *life; ++i) CHECK(test_renewal_ms(life[i]) == expected[i]);
    scenario = 3; atomic_store(&renewals, 0); roam_online_host(""); CHECK(target(host, &port, &room) == 1);
    for (int i = 0; i < 200 && !atomic_load(&renewals); ++i) delay();
    CHECK(atomic_load(&renewals) > 0); roam_online_leave(); CHECK(starts == stops && closed == 2);
    const char *protocols[] = {"PCP", "UPnP", "NAT-PMP", "NAT-PMP"};
    for (scenario = 4; scenario <= 7; ++scenario) {
        roam_online_host(""); CHECK(target(host, &port, &room) == 1);
        roam_online_status(status, sizeof status); CHECK(strstr(status, protocols[scenario-4]));
        if (scenario == 6) CHECK(strstr(status, "Possible double NAT / carrier NAT"));
        else CHECK(strstr(status, "Internet joining is untested"));
        if (scenario == 7) CHECK(strstr(status, "Router loopback: unavailable"));
        roam_online_leave(); CHECK(starts == stops);
    }
    CHECK(closed == 6);
    // Router address classes include boundaries; a manual code address must not erase the hint.
    const uint32_t upstream[] = {0x0a000001,0xac100000,0xac1fffff,0xc0a80001,0x64400000,0x647fffff,0x643fffff,0x64800000,0xac200000};
    for (unsigned i=0; i<sizeof upstream/sizeof *upstream; ++i) {
        scenario=0; roam_online_host("198.51.100.7"); CHECK(target(host,&port,&room)==1);
        extern void test_publish_upstream(uint32_t ip);
        test_publish_upstream(upstream[i]); roam_online_status(status,sizeof status);
        CHECK(!!strstr(status,"Possible double NAT / carrier NAT") == (i<6));
        CHECK(room == roam_code_room(0xc6336407u,47800));
        roam_online_host_code(code,sizeof code); CHECK(*code && !strstr(status,code));
        roam_online_leave();
    }
    scenario=8; roam_online_host(""); CHECK(target(host,&port,&room)==1);
    for (int i=0;i<200;++i) { roam_online_status(status,sizeof status); if (strstr(status,"could not be renewed")) break; delay(); }
    CHECK(strstr(status,"New joins may fail") && strstr(status,"Leave, then Host"));
    roam_online_leave(); CHECK(starts==stops);
    int expected_closed=closed;
    setenv("NFS_LAN_LOOPBACK", "1", 1);
    roam_online_host(""); CHECK(bind_ip == 0x7f000001u && target(host, &port, &room) == 1);
    CHECK(!strcmp(host, "127.0.0.1")); roam_online_leave(); CHECK(closed == expected_closed && starts == stops);
    unsetenv("NFS_LAN_LOOPBACK");
    printf("session: %d checks, %d failures; server starts/stops=%d/%d, mapping deletes=%d\n", checks, failed, starts, stops, closed);
    return failed ? 1 : 0;
}
