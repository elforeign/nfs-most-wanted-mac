// Production controller with counted forbidden backends. No sockets or router traffic.
#include "roam_online.h"
#include "natmap.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static const char *fixture_ip = "127.0.0.1";
static int fixture_port = 49321;
#define ROAM_SHARED_IPV4 fixture_ip
#define ROAM_SHARED_PORT fixture_port
static int checks, failed, routers, listeners, notifications;
#define CHECK(x) do { ++checks; if (!(x)) { ++failed; printf("FAIL %d: %s\n", __LINE__, #x); } } while (0)
void roam_game_wake(void) { ++notifications; }
RoamServer *roam_server_start(uint32_t ip, uint16_t port) { (void)ip; (void)port; ++listeners; return NULL; }
void roam_server_stop(RoamServer *s) { (void)s; ++listeners; }
uint16_t roam_server_port(RoamServer *s) { (void)s; ++listeners; return 0; }
int roam_server_probe(uint32_t ip, uint16_t p, const atomic_int *s) { (void)ip; (void)p; (void)s; ++listeners; return 0; }
int natmap_open(NatMap *m, uint16_t p, const atomic_int *s) { (void)m; (void)p; (void)s; ++routers; return -1; }
int natmap_renew(NatMap *m, const atomic_int *s) { (void)m; (void)s; ++routers; return -1; }
void natmap_close(NatMap *m) { (void)m; ++routers; }
#include "roam_online.c"
int main(void) {
    char status[512], host[64], a[ROAM_CODE_SIZE], b[ROAM_CODE_SIZE]; int port; uint32_t first, second, room;
    roam_online_status(status, sizeof status); CHECK(!strcmp(status,"Off"));
    CHECK(!notifications && !roam_online_target(host,&port,&room));
    roam_online_join("TE29-43A2W"); roam_online_status(status,sizeof status); CHECK(strstr(status,"Invalid code"));
    CHECK(!roam_online_target(host,&port,&room));
    roam_online_create(); CHECK(roam_online_target(host,&port,&first)==1);
    CHECK(!strcmp(host,"127.0.0.1") && port==fixture_port && first);
    roam_online_host_code(a,sizeof a); CHECK(!roam_code_decode(a,&room) && room==first);
    roam_online_status(status,sizeof status); CHECK(strstr(status,"Creating room") && !strstr(status,a));
    roam_online_players(1,2,0); roam_online_status(status,sizeof status); CHECK(strstr(status,"Connected: 2 players"));
    roam_online_players(0,0,0); roam_online_status(status,sizeof status); CHECK(strstr(status,"Reconnecting"));
    roam_online_players(0,0,4); roam_online_status(status,sizeof status); CHECK(strstr(status,"Reconnecting"));
    roam_online_players(0,0,ROAM_ONLINE_BANNED); roam_online_status(status,sizeof status); CHECK(strstr(status,"banned"));
    roam_online_players(0,0,2); roam_online_status(status,sizeof status); CHECK(strstr(status,"full"));
    roam_online_players(0,0,1); roam_online_status(status,sizeof status); CHECK(strstr(status,"different game version"));
    roam_online_players(0,0,3); roam_online_status(status,sizeof status); CHECK(strstr(status,"unavailable"));
    roam_online_create(); CHECK(roam_online_target(host,&port,&second)==1);
    CHECK(first!=second && !strcmp(host,"127.0.0.1") && port==fixture_port);
    roam_online_host_code(b,sizeof b); CHECK(strcmp(a,b));
    roam_online_join(a); CHECK(roam_online_target(host,&port,&room)==1 && room==first);
    CHECK(!strcmp(host,"127.0.0.1") && port==fixture_port);
    roam_online_host_code(b,sizeof b); CHECK(!*b);
    roam_online_status(status,sizeof status); CHECK(strstr(status,"Joining room"));
    roam_online_players(0,0,4); roam_online_status(status,sizeof status); CHECK(strstr(status,"Relay unavailable"));
    roam_online_players(1,2,0); roam_online_status(status,sizeof status); CHECK(strstr(status,"Connected"));
    roam_online_join("bad"); CHECK(!roam_online_target(host,&port,&room));
    roam_online_leave(); CHECK(roam_online_target(host,&port,&room)==-1);
    roam_online_status(status,sizeof status); CHECK(!strcmp(status,"Off"));
    const char *invalid[]={"", "relay.example", "0.0.0.0", "224.0.0.1", "255.255.255.255", "127.1", "0.2.3.4"};
    for (unsigned i=0;i<sizeof invalid/sizeof *invalid;++i) {
        fixture_ip=invalid[i]; roam_online_create(); CHECK(roam_online_target(host,&port,&room)==-1);
        roam_online_status(status,sizeof status); CHECK(strstr(status,"no shared relay configured") && strstr(status,"later update"));
        roam_online_host_code(b,sizeof b); CHECK(!*b);
        roam_online_join(a); CHECK(roam_online_target(host,&port,&room)==-1);
    }
    fixture_ip="127.0.0.1";
    int invalid_ports[]={0,-1,65536};
    for (unsigned i=0;i<sizeof invalid_ports/sizeof *invalid_ports;++i) {
        fixture_port=invalid_ports[i]; roam_online_join(a); CHECK(roam_online_target(host,&port,&room)==-1);
    }
    roam_online_leave(); CHECK(!routers && !listeners);
    printf("session: %d checks, %d failures; router calls=%d embedded calls=%d\n",checks,failed,routers,listeners);
    return failed?1:0;
}
