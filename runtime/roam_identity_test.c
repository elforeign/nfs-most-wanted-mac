// Relay/client production path with fake UDP sends; no sockets or router I/O.
#include "roam_core.h"
#include <arpa/inet.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
static ssize_t fake_sendto(int,const void*,size_t,int,const struct sockaddr*,socklen_t);
static int fake_fcntl(int fd,int cmd,...) { (void)fd;(void)cmd;return 0; }
static int fake_setsockopt(int a,int b,int c,const void*d,socklen_t e) { (void)a;(void)b;(void)c;(void)d;(void)e;return 0; }
#define sendto fake_sendto
#define fcntl fake_fcntl
#define setsockopt fake_setsockopt
#include "roam_relay.c"
#undef sendto
#undef fcntl
#undef setsockopt
static RoamClient peers[3];
static uint32_t now;
static int checks, failures, names, looks;
#define CHECK(x) do { ++checks; if (!(x)) { ++failures; printf("FAIL %d: %s\n",__LINE__,#x); } } while (0)
static ssize_t fake_sendto(int fd,const void*b,size_t n,int flags,const struct sockaddr*addr,socklen_t len) {
    (void)fd;(void)flags;(void)len;
    int i=ntohs(((const struct sockaddr_in*)addr)->sin_port)-10000;
    int type;uint16_t id,seq; CHECK(!roam_hdr_get(b,(int)n,&type,&id,&seq));
    if (type==ROAM_NAME) ++names; if (type==ROAM_LOOK) ++looks;
    CHECK(i>=0 && i<3); if (i>=0 && i<3) roam_client_input(&peers[i],b,(int)n,now);
    return (ssize_t)n;
}
int main(void) {
    RoamRelay *relay=roam_relay_new(42,32,8); CHECK(relay!=NULL);
    struct sockaddr_in addr[3]; uint8_t b[1200];
    for (int i=0;i<3;++i) {
        addr[i]=(struct sockaddr_in){.sin_family=AF_INET,.sin_port=htons((uint16_t)(10000+i)),.sin_addr.s_addr=htonl(INADDR_LOOPBACK)};
        roam_client_init(&peers[i],i==2?2:1,i==0?"A\nB\033C":"Roadrunner");
        now=1000; int n=roam_client_hello(&peers[i],b);roam_relay_input(relay,b,n,&addr[i],now);
        RoamState st={.t=1000,.pos={10+i,20,30},.q={0,0,0,32767},.flags=ROAM_FLAG_CAR};
        n=roam_client_state(&peers[i],&st,b);roam_relay_input(relay,b,n,&addr[i],now);
    }
    RoamLook look={.rev=17,.car=0x12345678,.nparts=23,.parts={123,0xff00ff,0xabcdef}};
    int n=roam_client_look(&peers[0],&look,b);roam_relay_input(relay,b,n,&addr[0],now);
    for (now=1000;now<=1200;now+=50) roam_relay_tick(relay,now);
    RoamRemote *a=roam_view_find(&peers[1].view,peers[0].id);
    CHECK(a && !strcmp(a->name,"A?B?C"));
    CHECK(a && a->has_look && a->look.rev==17 && a->look.car==0x12345678 && a->look.nparts==23 && a->look.parts[2]==0xabcdef);
    CHECK(!roam_view_find(&peers[2].view,peers[0].id));
    CHECK(names==2 && looks==1);
    // Neither absent membership nor a forged name may manufacture a visible peer.
    n=roam_hdr_put(b,ROAM_NAME,65000,0);memset(b+n,'X',16);roam_client_input(&peers[1],b,n+16,1250);
    CHECK(!roam_view_find(&peers[1].view,65000));
    char full[16],clean[16];memset(full,'Z',16);roam_name_copy(clean,full);CHECK(strlen(clean)==15);
    CHECK(clean[15]==0);full[0]=(char)255;roam_name_copy(clean,full);CHECK(clean[0]=='?');
    printf("identity: %d checks, %d failures; names=%d looks=%d\n",checks,failures,names,looks);
    roam_relay_free(relay);return failures?1:0;
}
