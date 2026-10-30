// Loopback-only load and deliberate controls; driven by roam_load_test.py.
// Measure relay-thread CPU (one core), successful wire bytes (IPv4+UDP included),
// actual client receive bytes and the longest production tick. No game or router traffic.
#include "roam_core.h"
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <math.h>
#include <poll.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

typedef struct { int fd; uint16_t id, seq; uint64_t bytes, packets, entries; } Bot;
typedef struct {
    RoamRelay *r; int fd; atomic_int stop, snapshot;
    double cpu;
    uint64_t uploads;
    RoamRelayStats baseline;
} RelayTask;
static double seconds(clockid_t clock) {
    struct timespec ts; clock_gettime(clock, &ts); return ts.tv_sec + ts.tv_nsec / 1e9;
}
static uint32_t ms(void) { return (uint32_t)(seconds(CLOCK_MONOTONIC) * 1000); }
static void nap(void) { struct timespec t = {.tv_nsec=1000000}; nanosleep(&t, NULL); }
static void *work(void *arg) {
    RelayTask *w = arg; double start = seconds(CLOCK_THREAD_CPUTIME_ID);
    while (!atomic_load(&w->stop)) {
        if (atomic_load(&w->snapshot) == 1) {
            w->baseline = roam_relay_stats(w->r);
            atomic_store(&w->snapshot, 2);
            while (atomic_load(&w->snapshot) == 2 && !atomic_load(&w->stop)) nap();
            start = seconds(CLOCK_THREAD_CPUTIME_ID);
        }
        roam_relay_service(w->r, ms());
        struct pollfd p = {.fd=w->fd, .events=POLLIN}; poll(&p, 1, 1);
    }
    // sendto can return before loopback delivery finishes. Drain to the known upload count;
    // a real ingress loss still fails after a bounded 50 ms settlement period.
    double until = seconds(CLOCK_MONOTONIC) + 0.05;
    do {
        roam_relay_service(w->r, ms());
        if (roam_relay_stats(w->r).rx >= w->uploads) break;
        struct pollfd p = {.fd=w->fd, .events=POLLIN}; poll(&p, 1, 1);
    } while (seconds(CLOCK_MONOTONIC) < until);
    w->cpu = seconds(CLOCK_THREAD_CPUTIME_ID) - start; return NULL;
}
static int sock(struct sockaddr_in *a) {
    int fd = socket(AF_INET, SOCK_DGRAM, 0); if (fd < 0) return -1;
    memset(a, 0, sizeof *a); a->sin_family=AF_INET; a->sin_addr.s_addr=htonl(INADDR_LOOPBACK);
    if (bind(fd, (struct sockaddr *)a, sizeof *a)) { int e=errno; close(fd); errno=e; return -1; }
    socklen_t n=sizeof *a; if (getsockname(fd, (struct sockaddr *)a, &n)) { close(fd); return -1; }
    if (fcntl(fd, F_SETFL, O_NONBLOCK)) { close(fd); return -1; }
    return fd;
}
static int receive(Bot *b, int count) {
    int bad=0;
    for (int i=0; i<count; ++i) {
        uint8_t pkt[2048]; ssize_t n;
        while ((n=recv(b[i].fd, pkt, sizeof pkt, MSG_DONTWAIT)) >= 0) {
            int type; uint16_t id, seq;
            if (n>ROAM_MAX_DGRAM || roam_hdr_get(pkt, (int)n, &type, &id, &seq)) { bad++; continue; }
            if (type==ROAM_WELCOME && n==ROAM_HDR+4) b[i].id=(uint16_t)(pkt[ROAM_HDR] | pkt[ROAM_HDR+1]<<8);
            if (type==ROAM_BUNDLE) {
                if (n<ROAM_HDR+1 || n!=ROAM_HDR+1+pkt[ROAM_HDR]*ROAM_ENTRY_BYTES) { bad++; continue; }
                b[i].entries+=pkt[ROAM_HDR];
            }
            b[i].bytes+=(uint64_t)n+28; b[i].packets++;
        }
    }
    return bad;
}
static void state(Bot *b, int i, int count, int spread, const struct sockaddr_in *to, uint32_t now) {
    RoamState s={.t=now,.q={0,0,0,32767},.flags=ROAM_FLAG_CAR};
    if (spread) {
        int side=(int)ceil(sqrt(count));
        s.pos[0]=(float)(i%side)*120; s.pos[1]=(float)(i/side)*120;
    }
    uint8_t pkt[ROAM_HDR+ROAM_STATE_BYTES]; roam_hdr_put(pkt, ROAM_STATE, b->id, b->seq++);
    roam_state_put(pkt+ROAM_HDR, &s);
    if (sendto(b->fd, pkt, sizeof pkt, MSG_DONTWAIT, (const struct sockaddr *)to, sizeof *to)!=(ssize_t)sizeof pkt) {
        perror("load state send"); exit(1);
    }
}
static void pure_input(RoamRelay *r, int i, int type, uint16_t seq, float x, uint32_t now) {
    struct sockaddr_in a={.sin_family=AF_INET,.sin_port=htons((uint16_t)(20000+i)),.sin_addr.s_addr=htonl(INADDR_LOOPBACK)};
    uint8_t b[ROAM_MAX_DGRAM]; int n=roam_hdr_put(b,type,(uint16_t)(i+1),seq);
    if (type==ROAM_HELLO) { memset(b+n,0,20); b[n]=7; n+=20; }
    else if (type==ROAM_STATE) { RoamState s={.t=now,.pos={x,0,0},.q={0,0,0,32767},.flags=ROAM_FLAG_CAR}; roam_state_put(b+n,&s); n+=ROAM_STATE_BYTES; }
    roam_relay_input(r,b,n,&a,now);
}
static int churn(void) {
    RoamRelay *r=roam_relay_new(-1,32,1024); if (!r) return 1;
    for (int i=0;i<1000;++i) pure_input(r,i,ROAM_HELLO,0,0,0);
    for (int i=0;i<1000;i+=2) pure_input(r,i,ROAM_BYE,1,0,1000);
    for (int i=1;i<1000;i+=2) pure_input(r,i,ROAM_STATE,1,0,1000);
    for (int i=1000;i<1500;++i) {
        pure_input(r,i,ROAM_HELLO,0,0,1000); pure_input(r,i,ROAM_STATE,1,0,1000);
    }
    RoamRelayStats s=roam_relay_stats(r);
    int failed=s.clients!=1000 || s.rx_bad!=0;
    printf("CHURN clients=%" PRIu64 " bad=%" PRIu64 "\n",s.clients,s.rx_bad);
    roam_relay_free(r);
    r=roam_relay_new(-1,1,2); if (!r) return 1;
    pure_input(r,0,ROAM_HELLO,0,0,0); // Keep id 1 alive across a complete id cycle.
    struct sockaddr_in a={.sin_family=AF_INET,.sin_port=htons(20001),.sin_addr.s_addr=htonl(INADDR_LOOPBACK)};
    uint8_t b[ROAM_HDR];
    for (int i=0;i<65536;++i) {
        pure_input(r,1,ROAM_HELLO,0,0,0);
        roam_hdr_put(b,ROAM_BYE,(uint16_t)(2+i%65534),0);
        roam_relay_input(r,b,sizeof b,&a,0);
    }
    s=roam_relay_stats(r);
    printf("IDWRAP clients=%" PRIu64 " bad=%" PRIu64 " full=%" PRIu64 "\n",s.clients,s.rx_bad,s.full);
    failed|=s.clients!=1 || s.rx_bad!=0 || s.full!=0;
    roam_relay_free(r); return failed;
}
static int fixture(void) {
    RoamRelay *r=roam_relay_new(-1,32,64); if (!r) return 1;
    for (int i=0;i<34;++i) pure_input(r,i,ROAM_HELLO,0,0,0);
    RoamRelayStats before={0}, after;
    for (int tick=0;tick<100;++tick) {
        uint32_t now=(uint32_t)tick*50;
        for (int i=0;i<34;++i) {
            float x=i<17?0:i<32?500+(i-17)*10:1000;
            if (i>=32) x+=(tick/4%2==i-32)?10:-10;
            pure_input(r,i,ROAM_STATE,(uint16_t)(tick+1),x,now);
        }
        roam_relay_tick(r,now);
        if (tick==7) before=roam_relay_stats(r);
    }
    after=roam_relay_stats(r);
    printf("FLAP changes=%" PRIu64 " tier=%" PRIu64 " overflow=%" PRIu64 "\n",
           after.interest_changes-before.interest_changes,after.tier_changes-before.tier_changes,after.bundle_overflow);
    roam_relay_free(r);
    r=roam_relay_new(-1,32,40); if (!r) return 1;
    for (int i=0;i<33;++i) pure_input(r,i,ROAM_HELLO,0,0,0);
    for (int tick=0;tick<24;++tick) {
        for (int i=0;i<33;++i) pure_input(r,i,ROAM_STATE,(uint16_t)(tick+1),0,(uint32_t)tick*50);
        roam_relay_tick(r,(uint32_t)tick*50);
        if (tick==3) before=roam_relay_stats(r);
    }
    after=roam_relay_stats(r);
    printf("TIERS near=%" PRIu64 " mid=%" PRIu64 " map=%" PRIu64 " overflow=%" PRIu64 "\n",
           after.state_entries[0]-before.state_entries[0], after.state_entries[1]-before.state_entries[1],
           after.state_entries[2]-before.state_entries[2],after.bundle_overflow);
    int failed=after.state_entries[0]-before.state_entries[0]!=33*160 ||
        after.state_entries[1]-before.state_entries[1]!=33*80 ||
        after.state_entries[2]-before.state_entries[2]!=33*32 || after.bundle_overflow!=0;
    roam_relay_free(r); return churn() || failed;
}
static int lookup(void) {
    RoamRelay *r=roam_relay_new(-1,32,1024); if (!r) return 1;
    for (int i=0;i<1000;++i) pure_input(r,i,ROAM_HELLO,0,0,0);
    uint64_t before=roam_relay_stats(r).lookup_probes;
    double start=seconds(CLOCK_THREAD_CPUTIME_ID);
    // 1000 complete ingress seconds; rate limits see a genuine 20 Hz clock.
    for (int tick=0;tick<20000;++tick)
        for (int i=0;i<1000;++i) pure_input(r,i,ROAM_STATE,(uint16_t)(tick+1),0,(uint32_t)tick*50);
    double cpu=seconds(CLOCK_THREAD_CPUTIME_ID)-start;
    uint64_t probes=roam_relay_stats(r).lookup_probes-before;
    printf("LOOKUP cpu_pct=%.4f probes_per_packet=%.3f budget_pct=0.1\n",cpu/10,probes/20000000.0);
    roam_relay_free(r); return 0;
}
int main(int argc,char **argv) {
    if (argc==2 && !strcmp(argv[1],"--fixture")) return fixture();
    if (argc==2 && !strcmp(argv[1],"--lookup")) return lookup();
    if (argc!=4) { fprintf(stderr,"usage: roam_load CLIENTS crowd|spread SECONDS\n"); return 2; }
    int count=atoi(argv[1]), spread=!strcmp(argv[2],"spread"); double duration=atof(argv[3]);
    if (count<2 || count>1000 || duration<1 || duration>30 || (strcmp(argv[2],"crowd") && !spread)) return 2;
    struct rlimit limit; if (getrlimit(RLIMIT_NOFILE,&limit)) return 1;
    if (limit.rlim_cur<(rlim_t)(count+32)) { limit.rlim_cur=(rlim_t)(count+32); if (setrlimit(RLIMIT_NOFILE,&limit)) { perror("load fd limit"); return 1; } }
    struct sockaddr_in relay; int fd=sock(&relay);
    if (fd<0) { if (errno==EPERM || errno==EACCES) { puts("SOCKET PENDING: loopback load"); return 3; } perror("load socket"); return 1; }
    Bot *b=calloc((size_t)count,sizeof *b); if (!b) return 1;
    RelayTask w={.fd=fd,.r=roam_relay_new(fd,32,count)};
    atomic_init(&w.stop,0); atomic_init(&w.snapshot,0); if (!w.r) return 1;
    pthread_t thread; if (pthread_create(&thread,NULL,work,&w)) return 1;
    for (int i=0;i<count;++i) {
        struct sockaddr_in a; b[i].fd=sock(&a); if (b[i].fd<0) { perror("load client socket"); exit(1); }
        uint8_t pkt[ROAM_HDR+20]={0}; roam_hdr_put(pkt,ROAM_HELLO,0,b[i].seq++); pkt[ROAM_HDR]=7;
        if (sendto(b[i].fd,pkt,sizeof pkt,0,(struct sockaddr *)&relay,sizeof relay)!=(ssize_t)sizeof pkt) return 1;
    }
    int welcomed=0,bad=0;
    double until=seconds(CLOCK_MONOTONIC)+2;
    while (seconds(CLOCK_MONOTONIC)<until && welcomed<count) {
        bad+=receive(b,count); welcomed=0; for (int i=0;i<count;++i) if (b[i].id) welcomed++; nap();
    }
    if (welcomed!=count) { fprintf(stderr,"load welcomed %d/%d\n",welcomed,count); exit(1); }
    // Warm up interest membership for one second, then measure an integral number of nominal seconds.
    double start=seconds(CLOCK_MONOTONIC), next=start, measure=start+1, end=measure+duration;
    int measured=0; uint64_t uploads=(uint64_t)count;
    while (seconds(CLOCK_MONOTONIC)<end) {
        double now=seconds(CLOCK_MONOTONIC);
        if (now>=next) {
            for (int i=0;i<count;++i) state(&b[i],i,count,spread,&relay,ms());
            uploads+=(uint64_t)count;
            next+=0.05;
        }
        bad+=receive(b,count);
        if (!measured && now>=measure) {
            // Pause the relay at a service boundary to exclude exactly the same warmup packets on both sides.
            atomic_store(&w.snapshot,1);
            while (atomic_load(&w.snapshot)!=2) nap();
            bad+=receive(b,count);
            for (int i=0;i<count;++i) b[i].bytes=b[i].packets=b[i].entries=0;
            measure=seconds(CLOCK_MONOTONIC); end=measure+duration;
            atomic_store(&w.snapshot,3);
            measured=1;
        }
        nap();
    }
    w.uploads=uploads; atomic_store(&w.stop,1); pthread_join(thread,NULL);
    double elapsed=seconds(CLOCK_MONOTONIC)-measure;
    bad+=receive(b,count);
    uint64_t bytes=0,packets=0,minimum=UINT64_MAX,maximum=0;
    for (int i=0;i<count;++i) {
        bytes+=b[i].bytes; packets+=b[i].packets;
        if (b[i].bytes<minimum) minimum=b[i].bytes;
        if (b[i].bytes>maximum) maximum=b[i].bytes;
        if (!b[i].entries) bad++;
        close(b[i].fd);
    }
    RoamRelayStats s=roam_relay_stats(w.r);
    uint64_t sent=s.tx-w.baseline.tx;
    double txwire=(double)(s.tx_bytes-w.baseline.tx_bytes+sent*28);
    int buffer=0; socklen_t buffer_n=sizeof buffer;
    if (getsockopt(fd,SOL_SOCKET,SO_RCVBUF,&buffer,&buffer_n)) bad++;
    printf("LOAD n=%d shape=%s down_kBps=%.3f out_Mbps=%.3f cpu_pct=%.2f max_tick_ms=%.3f min_kBps=%.3f max_kBps=%.3f "
           "tx=%" PRIu64 " rx_packets=%" PRIu64 " errors=%" PRIu64 " bad=%d overflow=%" PRIu64
           " probes=%.2f candidates=%" PRIu64 " ingress=%" PRIu64 "/%" PRIu64
           " ingress_bad=%" PRIu64 " rate_dropped=%" PRIu64 " rcvbuf=%d\n",
           count,argv[2],bytes/elapsed/count/1000.0,txwire/elapsed*8/1000000,w.cpu/elapsed*100,s.max_tick_us/1000.0,
           minimum/elapsed/1000.0,maximum/elapsed/1000.0,sent,packets,s.tx_errors,bad,s.bundle_overflow,
           s.lookup_probes/(double)s.rx,s.candidates,s.rx,uploads,s.rx_bad,s.rx_rate_dropped,buffer);
    int failed=bad || s.tx_errors || s.bundle_overflow || s.clients!=(uint64_t)count || minimum==0 ||
        (double)bytes<txwire*0.98 || s.rx!=uploads || s.rx_bad || s.rx_rate_dropped;
    roam_relay_free(w.r); close(fd); free(b); return failed?1:0;
}
