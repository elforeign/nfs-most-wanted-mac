// Loopback fixture: one production relay and 0..6 UDP peers following the received game pose.
// Usage: fixture [seconds [port [N]]]; defaults preserve the single-peer invocation.
#include "roam_core.h"
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

#define PEERS 6
#define STEP_MS (1000 / ROAM_HZ)
typedef struct {
    int fd;
    struct sockaddr_in addr;
    RoamClient client;
    unsigned hellos, states, looks, received_states;
    uint32_t hello_ms;
    uint64_t rx_wire_bytes, rx_packets;
} Peer;

static uint64_t ns(clockid_t clock) {
    struct timespec t;
    clock_gettime(clock, &t);
    return (uint64_t)t.tv_sec * 1000000000 + t.tv_nsec;
}
static uint32_t ms(void) { return (uint32_t)(ns(CLOCK_MONOTONIC) / 1000000); }
static int argument(const char *s, int min, int max, int *out) {
    char *end; errno = 0;
    long n = strtol(s, &end, 10);
    if (errno || !*s || *end || n < min || n > max) return -1;
    *out = (int)n; return 0;
}
static int bound_socket(struct sockaddr_in *a) {
    int fd = socket(AF_INET, SOCK_DGRAM, 0);
    if (fd < 0) return -1;
    socklen_t len = sizeof *a;
    if (bind(fd, (void *)a, len) || getsockname(fd, (void *)a, &len) ||
        fcntl(fd, F_SETFL, O_NONBLOCK) < 0) { close(fd); return -1; }
    return fd;
}
static int send_packet(Peer *p, const struct sockaddr_in *relay, const uint8_t *packet, int n) {
    return sendto(p->fd, packet, n, 0, (const void *)relay, sizeof *relay) == n ? 0 : -1;
}
static void offset(RoamState *s, int peer) {
    float x=s->q[0]/32767.0f, y=s->q[1]/32767.0f;
    float z=s->q[2]/32767.0f, w=s->q[3]/32767.0f;
    // Render row 0 is forward; row 1 is lateral. Three pairs fit ahead of the chase camera.
    float ahead=12.0f+6.0f*(peer/2), side=(peer&1 ? -1.0f : 1.0f)*(4.0f+2.0f*(peer/2));
    s->pos[0]+=ahead*(1-2*(y*y+z*z))+side*2*(x*y+z*w);
    s->pos[1]+=ahead*2*(x*y-z*w)+side*(1-2*(x*x+z*z));
    s->pos[2]+=ahead*2*(x*z+y*w)+side*2*(y*z-x*w);
    s->flags|=ROAM_FLAG_GHOST;
}
static void tick_log(RoamRelay *relay, uint32_t now, RoamRelayStats *previous) {
    uint64_t wall=ns(CLOCK_MONOTONIC), cpu=ns(CLOCK_PROCESS_CPUTIME_ID);
    roam_relay_tick(relay, now);
    uint64_t cpu_ns=ns(CLOCK_PROCESS_CPUTIME_ID)-cpu, wall_ns=ns(CLOCK_MONOTONIC)-wall;
    RoamRelayStats st=roam_relay_stats(relay);
    printf("relay_tick mono_ns=%" PRIu64 " now_ms=%u wall_ns=%" PRIu64 " cpu_ns=%" PRIu64
           " clients=%" PRIu64 " max_tick_us=%" PRIu64, wall, now, wall_ns, cpu_ns, st.clients, st.max_tick_us);
    // Counter deltas include inputs since the preceding tick; clients/max_tick_us are gauges.
#define DELTA(field) printf(" d_" #field "=%" PRIu64, st.field-previous->field)
    DELTA(rx); DELTA(rx_bytes); DELTA(rx_rate_dropped); DELTA(rx_bad); DELTA(rx_version);
    DELTA(tx); DELTA(tx_bytes); DELTA(tx_attempts); DELTA(tx_errors); DELTA(tx_wouldblock);
    DELTA(tx_wire_bytes); DELTA(tx_suppressed); DELTA(tx_suppressed_bytes);
    DELTA(ingress_dropped); DELTA(cookie_rejected); DELTA(admission_rejected); DELTA(source_rejected);
    DELTA(challenge_suppressed); DELTA(challenges);
    DELTA(tx_error_bytes); DELTA(timeouts); DELTA(full); DELTA(lookup_probes); DELTA(candidates);
    DELTA(selections); DELTA(interest_changes); DELTA(tier_changes); DELTA(bundle_overflow); DELTA(ticks);
#undef DELTA
    for (int i=0; i<3; ++i) printf(" d_state_entries%d=%" PRIu64, i, st.state_entries[i]-previous->state_entries[i]);
    putchar('\n'); *previous=st;
}
int main(int argc, char **argv) {
    int seconds=2, port=47811, count=1;
    if (argc>4 || (argc>1 && argument(argv[1],1,86400,&seconds)) ||
        (argc>2 && argument(argv[2],0,65535,&port)) ||
        (argc>3 && argument(argv[3],0,PEERS,&count))) {
        fprintf(stderr,"usage: %s [seconds (1..86400) [port (0..65535) [N (0..6)]]]\n",argv[0]); return 2;
    }
    struct sockaddr_in addr={.sin_family=AF_INET,.sin_port=htons((uint16_t)port),
                             .sin_addr.s_addr=htonl(INADDR_LOOPBACK)};
    int fd=bound_socket(&addr), result=3;
    Peer *peers=calloc(PEERS,sizeof *peers);
    RoamRelay *relay=NULL;
    if (!peers) { if (fd>=0) close(fd); return 3; }
    for (int i=0; i<PEERS; ++i) peers[i].fd=-1;
    if (fd<0) { perror("loopback fixture bind"); goto done; }
    for (int i=0; i<count; ++i) {
        Peer *p=&peers[i]; p->addr=addr; p->addr.sin_port=0;
        p->fd=bound_socket(&p->addr);
        if (p->fd<0) { perror("loopback fixture bind"); goto done; }
        char name[16];
        if (!i) snprintf(name,sizeof name,"Roadrunner");
        else snprintf(name,sizeof name,"Roadrunner%d",i+1);
        roam_client_init(&p->client,1,name);
    }
    relay=roam_relay_new(fd,32,128);
    if (!relay) goto done;
    uint8_t packet[ROAM_MAX_DGRAM+1]; RoamState local={0}; RoamLook appearance={0};
    int has=0, game_states=0, has_appearance=0;
    RoamRelayStats previous={0};
    uint32_t start=ms(), last=start-STEP_MS;
    printf("loopback fixture ready port %u seconds=%d peers=%d\n",ntohs(addr.sin_port),seconds,count); fflush(stdout);
    while ((uint32_t)(ms()-start)<(uint32_t)seconds*1000) {
        uint32_t now=ms(); struct sockaddr_in from; socklen_t len=sizeof from;
        int n;
        while ((n=(int)recvfrom(fd,packet,sizeof packet,0,(void*)&from,&len))>0) {
            int bot=-1, type; uint16_t id,seq;
            for (int i=0; i<count; ++i) if (from.sin_addr.s_addr==peers[i].addr.sin_addr.s_addr &&
                from.sin_port==peers[i].addr.sin_port) bot=i;
            RoamRelayStats before=roam_relay_stats(relay);
            roam_relay_input(relay,packet,n,&from,now);
            RoamRelayStats after=roam_relay_stats(relay);
            if (after.rx_bad!=before.rx_bad || after.rx_rate_dropped!=before.rx_rate_dropped ||
                roam_hdr_get(packet,n,&type,&id,&seq)) continue;
            if (type==ROAM_STATE && n==ROAM_HDR+ROAM_STATE_BYTES) {
                if (bot>=0) ++peers[bot].received_states;
                else {
                    roam_state_get(packet+ROAM_HDR,&local);
                    has=(local.flags&ROAM_FLAG_CAR)!=0; ++game_states;
                }
            }
            // Freeze the first observed appearance so peer revisions stay stable throughout a measurement.
            if (bot<0 && type==ROAM_LOOK && !has_appearance)
                has_appearance=!roam_look_get(packet+ROAM_HDR,n-ROAM_HDR,&appearance);
        }
        for (int i=0; i<count; ++i)
            while ((n=(int)recv(peers[i].fd,packet,sizeof packet,0))>0) {
                peers[i].rx_wire_bytes+=(uint64_t)n+28; peers[i].rx_packets++;
                roam_client_input(&peers[i].client,packet,n,now);
            }
        if ((uint32_t)(now-last)>=STEP_MS) {
            last=now;
            for (int i=0; i<count; ++i) {
                Peer *p=&peers[i];
                if (!p->client.welcomed || (!has && (uint32_t)(now-p->hello_ms)>=1000)) {
                    n=roam_client_hello(&p->client,packet);
                    if (send_packet(p,&addr,packet,n)) goto send_error;
                    ++p->hellos; p->hello_ms=now;
                }
                if (p->client.welcomed && has) {
                    RoamState s=local; s.t=now; offset(&s,i);
                    if (has_appearance) s.look=appearance.rev;
                    n=roam_client_state(&p->client,&s,packet);
                    if (send_packet(p,&addr,packet,n)) goto send_error;
                    ++p->states;
                    if (has_appearance) {
                        n=roam_client_look(&p->client,&appearance,packet);
                        if (send_packet(p,&addr,packet,n)) goto send_error;
                        ++p->looks;
                    }
                }
            }
            tick_log(relay,now,&previous); fflush(stdout);
        }
        struct pollfd p={.fd=fd,.events=POLLIN}; poll(&p,1,2);
    }
    result=0; int welcomed=0;
    for (int i=0; i<count; ++i) {
        Peer *p=&peers[i];
        printf("peer=%d id=%u welcomed=%d hello_sent=%u state_sent=%u state_received=%u look_sent=%u bundles=%u entries=%u rx_wire_bytes=%" PRIu64 " rx_packets=%" PRIu64 "\n",
               i+1,p->client.id,p->client.welcomed,p->hellos,p->states,
               p->received_states,p->looks,p->client.rx_bundles,p->client.rx_entries,p->rx_wire_bytes,p->rx_packets);
        welcomed+=p->client.welcomed!=0;
        if (!p->client.welcomed) result=1;
    }
    int unique=1;
    for (int i=0; i<count; ++i) {
        if (peers[i].addr.sin_port==addr.sin_port) unique=0;
        for (int j=0; j<i; ++j) if (peers[i].addr.sin_port==peers[j].addr.sin_port) unique=0;
    }
    if (!unique) result=1;
    printf("loopback fixture: peers=%d welcomed=%d game_states=%d ports_unique=%d\n",count,welcomed,game_states,unique);
    goto done;
send_error:
    perror("loopback fixture send");
done:
    if (relay) roam_relay_free(relay);
    for (int i=0; i<count; ++i) if (peers[i].fd>=0) close(peers[i].fd);
    free(peers); if (fd>=0) close(fd); return result;
}
