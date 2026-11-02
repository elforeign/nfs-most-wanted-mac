// Real loopback transport, production controller/client/relay. No embedded server or router.
// Review pins: backoff 5 checks/3 failures -> 5/0; sticky ban 17/5 -> 17/0; signature 9/5 -> 8/0.
// Fixed review total: 50 checks/0 failures; pinned controls do not read repository history.
#include "roam_core.h"
#include "roam_online.h"
#include "roam_moderation.h"
#include "roam_test_peer.h"
#include "roam_hello_due.inc"
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>
static int assigned_port;
#define ROAM_SHARED_IPV4 "127.0.0.1"
#define ROAM_SHARED_PORT assigned_port
void roam_game_wake(void) {}
#include "roam_online.c"
static int checks, failures, relay_fd=-1;
static RoamRelay *relay;
static uint32_t now=1000;
static RoamClient clients[4];
static int fds[4]={-1,-1,-1,-1}, live[4];
#define CHECK(x) do { ++checks; if (!(x)) { ++failures; if(failures<8) printf("FAIL %d: %s\n",__LINE__,#x); } } while(0)
static int bind_loopback(int port) {
    int fd=socket(AF_INET,SOCK_DGRAM,0); if(fd<0) return -1;
    struct sockaddr_in addr={.sin_family=AF_INET,.sin_port=htons((uint16_t)port),.sin_addr.s_addr=htonl(INADDR_LOOPBACK)};
    if(bind(fd,(struct sockaddr*)&addr,sizeof addr) || fcntl(fd,F_SETFL,O_NONBLOCK)) { int e=errno;close(fd);errno=e;return -1; }
    return fd;
}
static int start_relay(int port) {
    relay_fd=bind_loopback(port); if(relay_fd<0)return 0;
    struct sockaddr_in addr; socklen_t len=sizeof addr;
    if(getsockname(relay_fd,(struct sockaddr*)&addr,&len))return 0;
    assigned_port=ntohs(addr.sin_port);
    relay=roam_relay_new(relay_fd,4,3); return relay!=NULL;
}
static void send_hello(int i) {
    uint8_t b[1200]; int n=roam_client_hello(&clients[i],b);
    CHECK(n>0 && send(fds[i],b,(size_t)n,0)==n);
}
static void pump(int rounds) {
    for(int round=0;round<rounds;++round) {
        now+=50;
        for(int i=0;i<4;++i) if(live[i] && now%500==0) send_hello(i);
        roam_relay_service(relay,now);
        for(int i=0;i<4;++i) if(live[i]) {
            for(int k=0;k<64;++k) {
                uint8_t b[1200]; int n=(int)recv(fds[i],b,sizeof b,0); if(n<=0)break;
                roam_client_input(&clients[i],b,n,now);
                int type;uint16_t id,seq;
                if(!roam_hdr_get(b,n,&type,&id,&seq) && type==ROAM_CHALLENGE)send_hello(i);
            }
            if(clients[i].welcomed) {
                uint8_t b[1200]; RoamState state={.t=now,.pos={10+i,20,30},.q={0,0,0,32767},.flags=ROAM_FLAG_CAR};
                int n=roam_client_state(&clients[i],&state,b);CHECK(send(fds[i],b,(size_t)n,0)==n);
            }
        }
        usleep(1000);
    }
}
static void open_client(int i,uint32_t room) {
    char host[64],code[ROAM_CODE_SIZE];int port;uint32_t selected;
    CHECK(!roam_code_encode(room,code));roam_online_join(code);
    CHECK(roam_online_target(host,&port,&selected)==1 && selected==room && port==assigned_port && !strcmp(host,"127.0.0.1"));
    fds[i]=bind_loopback(0);CHECK(fds[i]>=0);if(fds[i]<0)return;
    struct sockaddr_in addr={.sin_family=AF_INET,.sin_port=htons((uint16_t)port)};
    CHECK(inet_pton(AF_INET,host,&addr.sin_addr)==1 && !connect(fds[i],(struct sockaddr*)&addr,sizeof addr));
    roam_client_init(&clients[i],selected,"fixture");live[i]=1;send_hello(i);
}
static void leave_client(int i) {
    roam_online_leave();char host[64];int port;uint32_t room;
    CHECK(roam_online_target(host,&port,&room)==-1);
    uint8_t b[1200];int n=roam_client_bye(&clients[i],b);CHECK(send(fds[i],b,(size_t)n,0)==n);
    live[i]=0;close(fds[i]);fds[i]=-1;roam_auth_clear(&clients[i].auth);pump(2);
}
// Deterministic transport retains the production reply budgets and cookie/signature checks.
typedef struct { RoamClient *client; uint32_t now; int packets, bytes, last_n, request_n; } ReviewCapture;
static int review_capture(void *ctx, const struct sockaddr_in *to, const uint8_t *b, int n) {
    (void)to; ReviewCapture *out=ctx;
    out->packets++; out->bytes+=n; out->last_n=n;
    CHECK(n<=out->request_n);
    roam_client_input(out->client,b,n,out->now); return n;
}
static void review_send(RoamRelay *r, RoamClient *c, ReviewCapture *out, const struct sockaddr_in *a, uint32_t at) {
    uint8_t b[ROAM_MAX_DGRAM]; int n=roam_client_hello(c,b); CHECK(n>0);
    out->now=at; out->request_n=n; roam_relay_input(r,b,n,a,at);
}
static int review_tests(const char *state, const char *admin) {
    RoamModeration *m=roam_moderation_open(state,admin); CHECK(m!=NULL); if(!m)return 1;
    RoamRelayPolicy policy=roam_test_policy(); policy.source_rate=1; policy.source_burst=1;
    RoamRelay *r=roam_relay_new_policy(-1,4,4,&policy); CHECK(r!=NULL); if(!r)return 1;
    roam_relay_moderation(r,m);
    RoamClient c; roam_client_init(&c,7,"review"); CHECK(roam_auth_generate(&c.auth));
    uint8_t identity[32]; CHECK(roam_auth_identifier(c.auth.secret,identity));
    uint64_t utc=roam_moderation_utc(); CHECK(roam_moderation_ban(m,identity,utc+60,utc));
    struct sockaddr_in a={.sin_family=AF_INET,.sin_port=htons(19000),.sin_addr.s_addr=htonl(INADDR_LOOPBACK)};
    ReviewCapture out={.client=&c}; roam_relay_sender(r,review_capture,&out);
    review_send(r,&c,&out,&a,1000); CHECK(c.has_cookie);
    review_send(r,&c,&out,&a,2000); CHECK(c.last_err==ROAM_ERR_BANNED && !c.welcomed);
    assigned_port=ROAM_PORT; char selected_code[ROAM_CODE_SIZE];
    CHECK(!roam_code_encode(c.room,selected_code)); roam_online_join(selected_code);
    int start=checks, bad=failures;
    CHECK(!review_hello_due(&c,2500,2000));
    CHECK(!review_hello_due(&c,6999,2000));
    CHECK(review_hello_due(&c,7000,2000));
    CHECK(!review_hello_due(&c,100,(uint32_t)(100u-4999u)));
    CHECK(review_hello_due(&c,100,(uint32_t)(100u-5000u)));
    printf("review backoff: %d checks, %d failures\n",checks-start,failures-bad);
    start=checks; bad=failures;
    // The old cookie expires; its CHALLENGE consumes the only per-address reply token.
    review_send(r,&c,&out,&a,13001);
    CHECK(c.last_err==ROAM_ERR_BANNED);
    roam_online_players(c.welcomed,0,c.last_err); char status[256]; roam_online_status(status,sizeof status);
    CHECK(strstr(status,"banned"));
    int packets=out.packets; uint64_t suppressed=roam_relay_stats(r).challenge_suppressed;
    review_send(r,&c,&out,&a,13001);
    CHECK(out.packets==packets && roam_relay_stats(r).challenge_suppressed==suppressed+1);
    CHECK(c.last_err==ROAM_ERR_BANNED && !c.welcomed);
    uint8_t b[ROAM_MAX_DGRAM]; int n=roam_hdr_put(b,ROAM_ERR,0,0); b[n++]=ROAM_ERR_FULL;
    roam_client_input(&c,b,n,13002); CHECK(c.last_err==ROAM_ERR_BANNED);
    roam_online_players(c.welcomed,0,c.last_err); roam_online_status(status,sizeof status); CHECK(strstr(status,"banned"));
    CHECK(roam_moderation_unban(m,identity,utc));
    CHECK(review_hello_due(&c,18001,13001));
    review_send(r,&c,&out,&a,18001);
    CHECK(c.welcomed && !c.last_err);
    CHECK(!review_hello_due(&c,19999,18001) && review_hello_due(&c,20001,18001));
    c.last_err=ROAM_ERR_BANNED; roam_auth_clear(&c.auth); roam_client_init(&c,7,"review");
    CHECK(!c.last_err && !c.welcomed);
    CHECK(!review_hello_due(&c,499,0) && review_hello_due(&c,500,0));
    printf("review sticky_ban: %d checks, %d failures\n",checks-start,failures-bad);
    roam_relay_free(r);
    // A cookie binds the supplied public key, but does not prove possession of its private key.
    r=roam_test_relay(-1,4,4); CHECK(r!=NULL); roam_relay_moderation(r,m);
    out=(ReviewCapture){.client=&c}; roam_relay_sender(r,review_capture,&out);
    CHECK(roam_auth_generate(&c.auth)); CHECK(roam_auth_identifier(c.auth.secret,identity));
    CHECK(roam_moderation_ban(m,identity,utc+60,utc));
    review_send(r,&c,&out,&a,1000); CHECK(c.has_cookie);
    n=roam_client_hello(&c,b); CHECK(n==ROAM_HELLO_BYTES);
    uint8_t valid[ROAM_MAX_DGRAM]; memcpy(valid,b,(size_t)n);
    b[ROAM_HDR+ROAM_HELLO_BASE+20]=1; memset(b+ROAM_HDR+ROAM_HELLO_BASE+21,0,72);
    start=checks; bad=failures;
    RoamRelayStats before=roam_relay_stats(r); packets=out.packets; out.now=2000; out.request_n=n;
    roam_relay_input(r,b,n,&a,2000); RoamRelayStats after=roam_relay_stats(r);
    CHECK(out.packets==packets && c.last_err!=ROAM_ERR_BANNED);
    CHECK(after.signature_checks==before.signature_checks+1 && after.auth_rejected==before.auth_rejected+1);
    CHECK(after.ban_rejected==before.ban_rejected && !after.clients);
    // A valid signature still receives the explicit, bounded ban error.
    out.now=3000; roam_relay_input(r,valid,n,&a,3000);
    CHECK(out.packets==packets+1 && out.last_n==ROAM_HDR+1 && out.last_n<=n);
    CHECK(c.last_err==ROAM_ERR_BANNED && roam_relay_stats(r).ban_rejected==before.ban_rejected+1);
    CHECK(roam_moderation_unban(m,identity,utc));
    before=roam_relay_stats(r); packets=out.packets; out.now=4000;
    roam_relay_input(r,b,n,&a,4000); after=roam_relay_stats(r);
    CHECK(out.packets==packets && after.auth_rejected==before.auth_rejected+1 && !after.clients);
    printf("review ban_signature: %d checks, %d failures\n",checks-start,failures-bad);
    roam_auth_clear(&c.auth); roam_relay_free(r); roam_moderation_close(m);
    printf("review: %d checks, %d failures\n",checks,failures); return failures?1:0;
}
int main(int argc,char **argv) {
    if(argc==4 && !strcmp(argv[3],"review"))return review_tests(argv[1],argv[2]);
    CHECK(argc==3);if(argc!=3)return 1;
    if(!start_relay(0)) {
        if(errno==EPERM || errno==EACCES) { puts("PENDING host run: loopback denied"); return 3; }
        perror("loopback");return 1;
    }
    roam_online_create();char host[64],code[ROAM_CODE_SIZE];int port;uint32_t room;
    CHECK(roam_online_target(host,&port,&room)==1 && port==assigned_port);
    roam_online_host_code(code,sizeof code);uint32_t decoded;CHECK(!roam_code_decode(code,&decoded) && decoded==room);
    open_client(0,room);open_client(1,room);open_client(2,room==UINT32_MAX?1:room+1);pump(20);
    for(int i=0;i<3;++i)CHECK(clients[i].welcomed && clients[i].has_cookie && clients[i].auth.ready);
    CHECK(roam_relay_stats(relay).clients==3 && roam_relay_stats(relay).signature_checks>=3);
    CHECK(clients[0].rx_entries>0 && clients[1].rx_entries>0);
    CHECK(roam_view_find(&clients[0].view,clients[1].id));
    CHECK(!clients[2].nmembers && !clients[2].rx_entries && !roam_view_find(&clients[0].view,clients[2].id));
    open_client(3,room);pump(10);CHECK(!clients[3].welcomed && clients[3].last_err==ROAM_ERR_FULL);
    roam_online_players(0,0,clients[3].last_err);char status[256];roam_online_status(status,sizeof status);CHECK(strstr(status,"full"));
    leave_client(3);leave_client(2);CHECK(roam_relay_stats(relay).clients==2);
    uint8_t before[ROAM_AUTH_PUBLIC];memcpy(before,clients[0].auth.secret,sizeof before);
    roam_online_join(code);CHECK(roam_online_target(host,&port,&room)==1);
    roam_online_players(1,2,0);roam_online_players(0,0,0);roam_online_status(status,sizeof status);CHECK(strstr(status,"Reconnecting"));
    roam_relay_free(relay);close(relay_fd);CHECK(start_relay(assigned_port));
    // Retain both connected sockets, installation keys and stale cookies over restart.
    for(int i=0;i<2;++i) {clients[i].last_err=ROAM_ERR_FULL;send_hello(i);}
    pump(20);
    CHECK(clients[0].welcomed && clients[1].welcomed && !clients[0].last_err && !clients[1].last_err);
    CHECK(!memcmp(before,clients[0].auth.secret,sizeof before) && roam_relay_stats(relay).clients==2);
    CHECK(clients[0].nmembers==1 && clients[1].nmembers==1);
    roam_online_players(1,2,0);roam_online_status(status,sizeof status);CHECK(strstr(status,"Connected"));
    // Wrong wire version is surfaced through the real relay/client parser.
    uint8_t b[1200];int n=roam_client_hello(&clients[0],b);b[2]=ROAM_VERSION-1;
    CHECK(send(fds[0],b,(size_t)n,0)==n);pump(2);CHECK(clients[0].last_err==ROAM_ERR_VERSION);
    // A live ban and a challenged reconnect must reach the UI as an explicit wire error.
    RoamModeration *moderation=roam_moderation_open(argv[1],argv[2]);CHECK(moderation!=NULL);
    if(!moderation)return 1;
    roam_relay_moderation(relay,moderation);
    uint8_t identity[32];CHECK(roam_relay_identity(relay,clients[0].id,identity));
    uint64_t utc=roam_moderation_utc();
    CHECK(roam_moderation_ban(moderation,identity,utc+60,utc));
    roam_relay_enforce_bans(relay);pump(2);
    CHECK(clients[0].last_err==ROAM_ONLINE_BANNED);
    CHECK(!clients[0].welcomed && !clients[0].id && !clients[0].nmembers && !clients[0].has_members);
    CHECK(!roam_view_find(&clients[0].view,clients[1].id));
    CHECK(clients[1].welcomed && !clients[1].last_err && roam_relay_stats(relay).clients==1);
    // Start without a retained cookie: only a fresh challenge may disclose the ban result.
    clients[0].has_cookie=0;clients[0].last_err=0;send_hello(0);pump(4);
    CHECK(clients[0].has_cookie && !clients[0].welcomed && clients[0].last_err==ROAM_ONLINE_BANNED);
    roam_online_players(clients[0].welcomed,0,clients[0].last_err);
    roam_online_status(status,sizeof status);CHECK(strstr(status,"banned"));
    CHECK(roam_moderation_unban(moderation,identity,utc));send_hello(0);pump(4);
    CHECK(clients[0].welcomed && !clients[0].last_err && roam_relay_stats(relay).clients==2);
    roam_online_players(clients[0].welcomed,2,clients[0].last_err);
    roam_online_status(status,sizeof status);CHECK(strstr(status,"Connected"));
    leave_client(0);CHECK(roam_relay_stats(relay).clients==1);
    leave_client(1);CHECK(!roam_relay_stats(relay).clients);
    roam_relay_free(relay);close(relay_fd);roam_moderation_close(moderation);
    printf("shared: %d checks, %d failures; 2 authenticated peers, isolated room, full/version/banned, Leave, restart/reconnect/unban\n",checks,failures);
    return failures?1:0;
}
