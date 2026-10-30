// Guarded-game fixture: production relay, one synthetic friend following the received game pose.
// Explicit loopback on every socket; never used by a player session.
#include "roam_core.h"
#include <arpa/inet.h>
#include <fcntl.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>
static uint32_t ms(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC,&t); return (uint32_t)((uint64_t)t.tv_sec*1000+t.tv_nsec/1000000); }
int main(int argc, char **argv) {
    int seconds = argc > 1 ? atoi(argv[1]) : 2;
    int port = argc > 2 ? atoi(argv[2]) : 47811;
    if (port < 0 || port > 65535) return 2;
    int fd = socket(AF_INET,SOCK_DGRAM,0), bot = socket(AF_INET,SOCK_DGRAM,0);
    struct sockaddr_in a = {.sin_family=AF_INET,.sin_port=htons((uint16_t)port),.sin_addr.s_addr=htonl(INADDR_LOOPBACK)}, b=a;
    b.sin_port=0;
    if (fd<0 || bot<0 || bind(fd,(void*)&a,sizeof a) || bind(bot,(void*)&b,sizeof b)) { perror("loopback fixture bind"); return 3; }
    socklen_t len=sizeof b; getsockname(bot,(void*)&b,&len);
    len=sizeof a; getsockname(fd,(void*)&a,&len);
    fcntl(fd,F_SETFL,O_NONBLOCK); fcntl(bot,F_SETFL,O_NONBLOCK);
    RoamRelay *relay=roam_relay_new(fd,32,128); RoamClient friend; roam_client_init(&friend,1,"Roadrunner");
    uint8_t packet[ROAM_MAX_DGRAM]; RoamState local={0}; int has=0, states=0; RoamLook appearance={0}; int has_appearance=0;
    uint32_t start=ms(), last=0;
    printf("loopback fixture ready 127.0.0.1:%u seconds=%d\n",ntohs(a.sin_port),seconds); fflush(stdout);
    while ((uint32_t)(ms()-start)<(uint32_t)seconds*1000) {
        uint32_t now=ms(); struct sockaddr_in from; socklen_t nfrom=sizeof from;
        int n;
        while ((n=(int)recvfrom(fd,packet,sizeof packet,0,(void*)&from,&nfrom))>0) {
            int type; uint16_t id,seq;
            if (!roam_hdr_get(packet,n,&type,&id,&seq) && type==ROAM_STATE && n==ROAM_HDR+ROAM_STATE_BYTES && from.sin_port!=b.sin_port) {
                roam_state_get(packet+ROAM_HDR,&local); has=(local.flags&ROAM_FLAG_CAR)!=0; ++states;
            }
            if (!roam_hdr_get(packet,n,&type,&id,&seq) && type==ROAM_LOOK && from.sin_port!=b.sin_port)
                has_appearance = !roam_look_get(packet+ROAM_HDR,n-ROAM_HDR,&appearance);
            roam_relay_input(relay,packet,n,&from,now);
        }
        while ((n=(int)recv(bot,packet,sizeof packet,0))>0) roam_client_input(&friend,packet,n,now);
        if ((uint32_t)(now-last)>=50) {
            last=now;
            if (!friend.welcomed || !(now%1000/50)) { n=roam_client_hello(&friend,packet); roam_relay_input(relay,packet,n,&b,now); }
            if (friend.welcomed && has) {
                RoamState s=local; s.t=now;
                float x=s.q[0]/32767.0f,y=s.q[1]/32767.0f,z=s.q[2]/32767.0f,w=s.q[3]/32767.0f;
                int phase=0;
                float sign=(phase&1)?-1.0f:1.0f;
                if (phase<2) {
                    s.pos[0]+=sign*60*(1-2*(y*y+z*z)); s.pos[1]+=sign*60*2*(x*y-z*w); s.pos[2]+=sign*60*2*(x*z+y*w);
                } else {
                    s.pos[0]+=sign*60*2*(x*y+z*w); s.pos[1]+=sign*60*(1-2*(x*x+z*z)); s.pos[2]+=sign*60*2*(y*z-x*w);
                }
                s.flags|=ROAM_FLAG_GHOST;
                n=roam_client_state(&friend,&s,packet); roam_relay_input(relay,packet,n,&b,now);
            }
            if (friend.welcomed && has_appearance && now%1000/50==0) {
                n=roam_client_look(&friend,&appearance,packet); roam_relay_input(relay,packet,n,&b,now);
            }
            roam_relay_tick(relay,now);
        }
        struct pollfd p={.fd=fd,.events=POLLIN}; poll(&p,1,2);
    }
    printf("loopback fixture: welcomed=%d game_states=%d\n",friend.welcomed,states);
    roam_relay_free(relay); close(bot); close(fd); return friend.welcomed ? 0 : 1;
}
