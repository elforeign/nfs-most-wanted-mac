// Production guard proof with counted transports; no game, sockets or public traffic.
#include "roam_core.h"
#include "roam_test_peer.h"
#include <arpa/inet.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>
static int flood;
// Advance only the flooded service fixture; host scheduling cannot hide a missing yield.
static uint64_t flood_us;
static int fixture_clock_gettime(clockid_t clock, struct timespec *ts) {
    if (!flood) return clock_gettime(clock, ts);
    ts->tv_sec = (time_t)(flood_us / 1000000);
    ts->tv_nsec = (long)(flood_us % 1000000) * 1000;
    return 0;
}
static ssize_t flood_recv(int fd, void *b, size_t n, int flags, struct sockaddr *a, socklen_t *len) {
    (void)fd; (void)flags;
    if (!flood) { errno=EAGAIN; return -1; }
    flood_us += 300;
    memset(b,0,n); struct sockaddr_in from={.sin_family=AF_INET,.sin_port=htons(1234),.sin_addr.s_addr=htonl(INADDR_LOOPBACK)};
    memcpy(a,&from,sizeof from); *len=sizeof from; return 28;
}
#define recvfrom flood_recv
#define clock_gettime fixture_clock_gettime
#include "roam_relay.c"
#undef clock_gettime
#undef recvfrom
static int checks, failures;
#define CHECK(x, name) do { ++checks; if (!(x)) { ++failures; if (failures<12) printf("FAIL %s line=%d\n",name,__LINE__); } } while (0)
typedef struct {
    uint64_t bytes, packets, types[12], bundles[32], members[32], controls[32];
    int n; uint8_t last[1200];
    RoamAuth auth;
    RoamClient *client; uint32_t now;
} Capture;
static int capture(void *ctx, const struct sockaddr_in *to, const uint8_t *b, int n) {
    Capture *c=ctx; c->bytes+=(unsigned)n+28; c->packets++;
    c->n=n; memcpy(c->last,b,(size_t)n);
    if (b[3]<12) c->types[b[3]]++;
    int i=(int)ntohs(to->sin_port)-10000;
    if (i>=0 && i<32) {
        if (b[3]==ROAM_BUNDLE) c->bundles[i]++;
        if (b[3]==ROAM_MEMBERS) c->members[i]++;
        if (b[3]==ROAM_NAME || b[3]==ROAM_LOOK) c->controls[i]++;
    }
    if (c->client) roam_client_input(c->client,b,n,c->now);
    return n;
}
static struct sockaddr_in address(unsigned ip, unsigned port) {
    return (struct sockaddr_in){.sin_family=AF_INET,.sin_port=htons((uint16_t)port),.sin_addr.s_addr=htonl(ip)};
}
static void hello(RoamRelay *r, Capture *out, const struct sockaddr_in *a, uint32_t now, uint8_t b[1200]) {
    RoamClient c; roam_client_init(&c,7,"peer"); out->client=&c; out->now=now;
    int n=roam_client_hello(&c,b); roam_relay_input(r,b,n,a,now);
    CHECK(!r->st.signature_checks,"cookie_before_signature"); CHECK(c.has_cookie,"challenge_received"); CHECK(out->n<=n,"challenge_no_amplification");
    CHECK(!roam_relay_stats(r).clients,"challenge_zero_allocation");
    (void)roam_client_hello(&c,b); out->auth=c.auth; out->client=NULL;
}
static void cookie_test(void) {
    for (int variant=0;variant<9;++variant) {
        RoamRelay *r=roam_relay_new(-1,32,4); Capture out={0}; roam_relay_sender(r,capture,&out);
        struct sockaddr_in a=address(0x7f000001,10000); uint8_t b[1200]; uint32_t now=1000;
        hello(r,&out,&a,now,b);
        int n=ROAM_HELLO_BYTES;
        if (variant==0) n=ROAM_HDR+ROAM_HELLO_BASE;    // absent
        if (variant==1) b[ROAM_HDR+ROAM_HELLO_BASE+7]^=1;                     // forged tag
        if (variant==2) now+=10001;                   // expired
        if (variant==3) b[8]^=1;                      // room
        if (variant==4) b[12]^=1;                     // name
        if (variant==5) a.sin_port=htons(10001);       // port
        if (variant==6) a.sin_addr.s_addr=htonl(0x7f000002);
        if (variant==7) {                             // process restart
            roam_relay_free(r); r=roam_relay_new(-1,32,4); roam_relay_sender(r,capture,&out);
        }
        if (variant==8) b[ROAM_HDR+ROAM_HELLO_BASE]=0xff;                    // future timestamp
        roam_relay_input(r,b,n,&a,now); roam_relay_tick(r,now);
        CHECK(roam_relay_stats(r).clients==0,"cookie_allocation");
        CHECK(!out.types[ROAM_MEMBERS] && !out.types[ROAM_BUNDLE] && !out.types[ROAM_NAME] && !out.types[ROAM_LOOK] && !out.types[ROAM_WELCOME],"cookie_no_fanout");
        roam_relay_free(r);
    }
    RoamRelay *r=roam_relay_new(-1,32,2); Capture out={0}; roam_relay_sender(r,capture,&out);
    struct sockaddr_in a=address(0x7f000001,10000); uint8_t b[1200];
    hello(r,&out,&a,UINT32_MAX-500,b);
    RoamClient c; roam_client_init(&c,7,"peer"); c.auth=out.auth; out.client=&c; out.now=100;
    roam_relay_input(r,b,ROAM_HELLO_BYTES,&a,100); uint16_t id=c.id;
    CHECK(c.welcomed && id && roam_relay_stats(r).clients==1,"cookie_wrap_valid");
    roam_relay_input(r,b,ROAM_HELLO_BYTES,&a,101);
    CHECK(c.id==id && roam_relay_stats(r).clients==1,"cookie_idempotent");
    roam_relay_free(r);
    // A full relay challenges first and only returns a bounded error after validation.
    r=roam_relay_new(-1,32,1); roam_client_init(&c,7,"one");
    CHECK(roam_test_join(r,&c,&a,1000),"full_first_join");
    RoamClient other; roam_client_init(&other,7,"two"); a.sin_port=htons(10001);
    memset(&out,0,sizeof out); out.client=&other; out.now=1000; roam_relay_sender(r,capture,&out);
    roam_relay_input(r,b,roam_client_hello(&other,b),&a,1000);
    CHECK(out.n==28 && !other.last_err,"full_challenge_first");
    roam_relay_input(r,b,roam_client_hello(&other,b),&a,1000);
    CHECK(out.n==9 && other.last_err==ROAM_ERR_FULL && roam_relay_stats(r).clients==1,"full_validated_error");
    roam_relay_free(r);
}
static void global_test(void) {
    // Issue cookies at 100/s, then retry them as a simultaneous admission flood.
    RoamRelayPolicy p=roam_relay_policy();
    RoamRelay *r=roam_relay_new_policy(-1,32,1000,&p); Capture out={0}; roam_relay_sender(r,capture,&out);
    uint8_t retry[400][ROAM_HELLO_BYTES];
    for (int i=0;i<400;++i) {
        RoamClient c; roam_client_init(&c,7,"peer"); out.client=&c; out.now=(uint32_t)i*10;
        struct sockaddr_in a=address(0x0a000001u+(unsigned)i,10000);
        uint8_t b[1200]; roam_relay_input(r,b,roam_client_hello(&c,b),&a,out.now);
        CHECK(c.has_cookie,"flood_challenge"); CHECK(roam_client_hello(&c,retry[i])==ROAM_HELLO_BYTES,"retry_size");
    }
    out.client=NULL;
    for (int i=0;i<400;++i) { struct sockaddr_in a=address(0x0a000001u+(unsigned)i,10000); roam_relay_input(r,retry[i],ROAM_HELLO_BYTES,&a,4000); }
    CHECK(roam_relay_stats(r).clients==100 && roam_relay_stats(r).admission_rejected==300,"global_admission_bound");
    for (int i=100;i<120;++i) { struct sockaddr_in a=address(0x0a000001u+(unsigned)i,10000); roam_relay_input(r,retry[i],ROAM_HELLO_BYTES,&a,5000); }
    CHECK(roam_relay_stats(r).clients==120,"global_admission_refill");
    printf("admission: clients=%llu bound=100+20*1 rejected=%llu\n",(unsigned long long)roam_relay_stats(r).clients,(unsigned long long)roam_relay_stats(r).admission_rejected);
    roam_relay_free(r);
    r=roam_relay_new(-1,32,4); memset(&out,0,sizeof out); roam_relay_sender(r,capture,&out);
    uint8_t b[1200]={0}; roam_hdr_put(b,ROAM_HELLO,0,0);
    for (int i=0;i<10000;++i) { struct sockaddr_in a=address(0x0a000001u+(unsigned)i,10000); roam_relay_input(r,b,ROAM_HDR+ROAM_HELLO_BASE,&a,1000); }
    RoamRelayStats s=roam_relay_stats(r);
    CHECK(s.ingress_dropped==10000-512,"global_ingress_packet_bound");
    CHECK(out.packets==100 && s.challenge_suppressed==412,"global_challenge_bound");
    CHECK(!s.clients && out.bytes<=100*56,"global_zero_allocation_bytes");
    printf("ingress: rejected=%llu/10000 challenge_bytes=%llu bound=5600\n",(unsigned long long)s.ingress_dropped,(unsigned long long)out.bytes);
    roam_relay_free(r);
    r=roam_relay_new(-1,32,4); memset(b,0,sizeof b); roam_hdr_put(b,ROAM_STATE,0,0);
    for (int i=0;i<1000;++i) { struct sockaddr_in a=address(0x7f000001,10000); roam_relay_input(r,b,1200,&a,1000); }
    CHECK(roam_relay_stats(r).ingress_dropped==1000-(256*1024/1228),"global_ingress_byte_bound");
    roam_relay_free(r);
    // Sustained rotating sources: credit is bounded at every millisecond, including wrap.
    for (int size=ROAM_HDR+ROAM_HELLO_BASE;size<=1200;size+=1200-ROAM_HDR-ROAM_HELLO_BASE) {
        r=roam_relay_new(-1,32,4); memset(&out,0,sizeof out); roam_relay_sender(r,capture,&out);
        memset(b,0,sizeof b); roam_hdr_put(b,ROAM_STATE,0,0); b[2]++;
        for (int step=0;step<=1000;++step) {
            for (int j=0;j<100;++j) {
                struct sockaddr_in a=address(0x0a000001u+(unsigned)(j%16),10000+(unsigned)j);
                roam_relay_input(r,b,size,&a,UINT32_MAX-500+(unsigned)step);
            }
            RoamRelayStats st=roam_relay_stats(r);
            uint64_t processed=st.rx-st.ingress_dropped;
            CHECK(processed<=512+(uint64_t)step*8000/1000,"global_ingress_refill_packets");
            CHECK(processed*(unsigned)(size+28)<=256*1024+(uint64_t)step*2*1024*1024/1000,"global_ingress_refill_bytes");
            CHECK(out.packets<=100+(uint64_t)step*100/1000,"global_reply_refill_bound");
            CHECK(out.bytes<=37*(100+(uint64_t)step*100/1000),"global_reply_measured_bytes");
        }
        CHECK(out.packets==200,"global_reply_refill_progress");
        CHECK(!roam_relay_stats(r).clients,"global_sustained_zero_allocation");
        roam_relay_free(r);
    }
    // Per-source admission: port rotation stays in the same IPv4 budget.
    p=roam_test_policy(); p.source_rate=10; p.source_burst=100;
    r=roam_relay_new_policy(-1,32,500,&p); memset(&out,0,sizeof out); roam_relay_sender(r,capture,&out);
    for (int i=0;i<120;++i) {
        struct sockaddr_in a=address(0x7f000001,10000+(unsigned)i);
        // Twenty old cookies plus a fresh hundred test the admission burst independently
        // of key retirement (which can shorten a cookie's maximum ten-second lifetime).
        RoamClient c; roam_client_init(&c,7,"source"); out.client=&c; out.now=i<20?(uint32_t)i*100:5000+(uint32_t)(i-20)*60;
        roam_relay_input(r,b,roam_client_hello(&c,b),&a,out.now);
        CHECK(c.has_cookie,"source_cookie"); (void)roam_client_hello(&c,retry[i]);
    }
    out.client=NULL;
    for (int i=0;i<120;++i) { struct sockaddr_in a=address(0x7f000001,10000+(unsigned)i); roam_relay_input(r,retry[i],ROAM_HELLO_BYTES,&a,12000); }
    // First twenty cookies expired; remaining hundred share the source burst.
    CHECK(roam_relay_stats(r).clients==100,"source_admission_burst");
    // New valid cookies are still refused until the source refills.
    RoamClient c; roam_client_init(&c,7,"source"); struct sockaddr_in a=address(0x7f000001,11000); out.client=&c; out.now=12000;
    roam_relay_input(r,b,roam_client_hello(&c,b),&a,12000);
    roam_relay_input(r,b,roam_client_hello(&c,b),&a,12000);
    CHECK(!c.welcomed && roam_relay_stats(r).admission_rejected==1,"source_admission_bound");
    roam_relay_input(r,b,roam_client_hello(&c,b),&a,12100);
    CHECK(c.welcomed,"source_admission_refill"); roam_relay_free(r);
}
static void source_test(void) {
    RoamRelayPolicy p=roam_test_policy(); RoamRelay *r=roam_relay_new_policy(-1,32,4,&p); Capture out={0}; roam_relay_sender(r,capture,&out);
    uint8_t b[1200]={0}; roam_hdr_put(b,ROAM_HELLO,0,0);
    for (int i=0;i<1025;++i) {
        struct sockaddr_in a=address(0x7f010000u+(unsigned)i,10000);
        RoamClient c; roam_client_init(&c,7,"validated"); out.client=&c; out.now=1000;
        roam_relay_input(r,b,roam_client_hello(&c,b),&a,1000);
        roam_relay_input(r,b,roam_client_hello(&c,b),&a,1000);
    }
    out.client=NULL;
    CHECK(roam_relay_stats(r).source_records==1024 && roam_relay_stats(r).source_rejected==1,"source_table_cap");
    struct sockaddr_in a=address(0x7f000001,10000); RoamClient c; roam_client_init(&c,7,"expiry");
    out.client=&c; out.now=61000;
    roam_relay_input(r,b,roam_client_hello(&c,b),&a,61000);
    roam_relay_input(r,b,roam_client_hello(&c,b),&a,61000); out.client=NULL;
    CHECK(roam_relay_stats(r).source_records==1,"source_table_expiry");
    roam_relay_tick(r,121001);
    CHECK(roam_relay_stats(r).source_records==0,"source_idle_expiry");
    roam_relay_free(r);
    r=roam_relay_new(-1,32,4); memset(&out,0,sizeof out); roam_relay_sender(r,capture,&out);
    roam_hdr_put(b,ROAM_HELLO,0,0); b[2]++;
    for (int i=0;i<1000;++i) { a=address(0x0a000001u+(unsigned)i,10000); roam_relay_input(r,b,ROAM_HDR+ROAM_HELLO_BASE,&a,1000); }
    CHECK(out.packets==100 && out.bytes==3700,"wrong_version_rotating_bound");
    CHECK(roam_relay_stats(r).clients==0,"wrong_version_no_allocation");
    uint64_t before=out.bytes; roam_relay_input(r,b,8,&a,2000);
    CHECK(out.bytes==before,"wrong_version_short_no_amplification"); roam_relay_free(r);
    // Modular source expiry across the 32-bit millisecond wrap.
    r=roam_relay_new(-1,32,4); memset(&out,0,sizeof out); roam_relay_sender(r,capture,&out);
    roam_client_init(&c,7,"wrap"); out.client=&c; out.now=UINT32_MAX-30000;
    roam_relay_input(r,b,roam_client_hello(&c,b),&a,out.now);
    roam_relay_input(r,b,roam_client_hello(&c,b),&a,out.now);
    a.sin_addr.s_addr=htonl(0x7f000002); roam_client_init(&c,7,"wrap"); out.now=30000;
    roam_relay_input(r,b,roam_client_hello(&c,b),&a,out.now);
    roam_relay_input(r,b,roam_client_hello(&c,b),&a,out.now); out.client=NULL;
    CHECK(roam_relay_stats(r).source_records==1,"source_expiry_wrap"); roam_relay_free(r);
}
// Security regressions on 7f28a0fb: 14,400/14,400 STATE drops, 120 timeouts;
// 3/3 legitimate sources rejected after 1,024 spoofed records; 1 revived cookie.
// Fixed: zero STATE drops/timeouts, zero legitimate rejections, zero revivals.
static void isolation_test(void) {
    RoamRelay *r=roam_test_relay(-1,1,120);
    RoamClient *c=calloc(120,sizeof *c); CHECK(c!=NULL,"isolation_storage");
    struct sockaddr_in a=address(0x7f000001,10000), attacker=address(0x7f000002,20000);
    uint8_t b[1200]={0};
    for (int i=0;i<120;++i) {
        a.sin_port=htons((uint16_t)(10000+i)); roam_client_init(&c[i],7,"admitted");
        CHECK(roam_test_join(r,&c[i],&a,0),"isolation_join");
    }
    r->policy=roam_relay_policy();
    memset(&r->ingress_packets,0,sizeof r->ingress_packets);
    memset(&r->ingress_bytes,0,sizeof r->ingress_bytes);
    int dropped=0;
    for (unsigned step=1;step<=120;++step) {
        uint32_t now=step*50;
        for (int j=0;j<512;++j) roam_relay_input(r,b,0,&attacker,now);
        for (int i=0;i<120;++i) {
            a.sin_port=htons((uint16_t)(10000+i));
            RoamState st={.t=now,.q={0,0,0,32767},.flags=ROAM_FLAG_CAR};
            int n=roam_client_state(&c[i],&st,b); roam_relay_input(r,b,n,&a,now);
            Client *k=by_id(r,c[i].id);
            if (!k || !k->has_state || k->state_ms!=now) ++dropped;
        }
        roam_relay_tick(r,now);
    }
    printf("isolation: STATE dropped=%d/14400 timeouts=%llu/120 duration_ms=6000\n",dropped,(unsigned long long)r->st.timeouts);
    CHECK(dropped==0,"isolation_state_progress");
    CHECK(r->st.clients==120 && !r->st.timeouts,"isolation_no_timeouts");
    // An incorrect id at a known endpoint must not spend its established allowance.
    a.sin_port=htons(10000);
    for (int j=0;j<512;++j) { roam_hdr_put(b,ROAM_STATE,0,0); roam_relay_input(r,b,ROAM_HDR+ROAM_STATE_BYTES,&a,6050); }
    RoamState st={.t=6050,.q={0,0,0,32767},.flags=ROAM_FLAG_CAR};
    int n=roam_client_state(&c[0],&st,b); roam_relay_input(r,b,n,&a,6050);
    Client *k=by_id(r,c[0].id);
    CHECK(k && k->state_ms==6050,"isolation_wrong_id");
    free(c); roam_relay_free(r);
}
static void spoof_test(void) {
    RoamRelay *r=roam_relay_new(-1,1,4);
    uint8_t b[1200]={0}; int rejected=0;
    for (unsigned round=0;round<3;++round) {
        roam_hdr_put(b,ROAM_HELLO,0,0);
        for (unsigned i=0;i<1024;++i) {
            struct sockaddr_in a=address(0x7f010000+i,10000);
            // About 34 packets/s stays within every default traffic/reply budget.
            uint32_t now=1000+round*30000+i*30000/1024;
            roam_relay_input(r,b,ROAM_HDR+ROAM_HELLO_BASE,&a,now);
        }
        uint32_t now=1000+(round+1)*30000;
        struct sockaddr_in legit=address(0x7f000001+round,20000);
        RoamClient c; roam_client_init(&c,7,"legitimate");
        if (!roam_test_join(r,&c,&legit,now)) ++rejected;
        roam_relay_tick(r,now);
    }
    printf("spoof: rejected legitimate sources=%d/3 spoofed_sources=1024 refresh_ms=30000\n",rejected);
    CHECK(!rejected,"spoof_legitimate_admission");
    roam_relay_free(r);
}
static void revival_test(void) {
    RoamRelay *r=roam_relay_new(-1,1,4); Capture out={0}; roam_relay_sender(r,capture,&out);
    struct sockaddr_in a=address(0x7f000001,10000); uint8_t b[1200];
    hello(r,&out,&a,1000,b);
    uint64_t end=(UINT64_C(1)<<32)+1001;
    // Keep servicing within the signed clock horizon, through a complete wrap.
    for (uint64_t t=1000+86400000;t<end;t+=86400000) roam_relay_tick(r,(uint32_t)t);
    roam_relay_tick(r,(uint32_t)end);
    roam_relay_input(r,b,ROAM_HELLO_BYTES,&a,(uint32_t)end);
    printf("revival: revived_cookie=%llu age_ms=%llu\n",(unsigned long long)r->st.clients,(unsigned long long)(end-1000));
    CHECK(!r->st.clients,"cookie_no_wrap_revival");
    // Age protection must also hold with a deliberately unchanged key.
    uint8_t key[32]={1}, cookie[20], hello_bytes[ROAM_HELLO_BASE]={0};
    CHECK(roam_cookie_make(key,&a,ROAM_VERSION,hello_bytes,1000,cookie),"absolute_cookie_make");
    CHECK(!roam_cookie_valid(key,&a,ROAM_VERSION,hello_bytes,cookie,end),"cookie_absolute_age");
    roam_relay_free(r);
}
// Keep the three regression models buildable against the pinned pre-key-ring relay.
#ifdef ROAM_KEY_MS
// Rotation-off control: 4 failures; previous-key-off: 5 failures;
// retention-refresh control: 2 failures; idle-reply control: 1 failure. Fixed: 26/0.
static void rotation_test(void) {
    RoamRelay *r=roam_relay_new(-1,1,4); Capture out={0}; roam_relay_sender(r,capture,&out);
    struct sockaddr_in a=address(0x7f000001,10000); uint8_t b[1200], key[32];
    hello(r,&out,&a,1000,b); memcpy(key,r->secret,sizeof key);
    // Tick alone rotates, even when no HELLO arrives on the boundary.
    roam_relay_tick(r,6000);
    CHECK(memcmp(key,r->secret,sizeof key)!=0,"cookie_scheduled_rotation");
    RoamClient c; roam_client_init(&c,7,"peer"); c.auth=out.auth; out.client=&c; out.now=6000;
    roam_relay_input(r,b,ROAM_HELLO_BYTES,&a,6000);
    CHECK(c.welcomed,"cookie_previous_key");
    CHECK(r->st.source_records==1,"retention_validated_record");
    out.now=10999; roam_relay_input(r,b,ROAM_HELLO_BYTES,&a,10999);
    CHECK(c.welcomed && r->st.clients==1,"cookie_previous_window");
    Source *src=NULL;
    for (int i=0;i<ROAM_SOURCES;++i) if (r->sources[i].used) src=&r->sources[i];
    CHECK(src && src->last==6000,"source_retry_no_refresh");
    out.now=11000; roam_relay_input(r,b,ROAM_HELLO_BYTES,&a,11000);
    CHECK(!c.welcomed && c.has_cookie,"cookie_retired_key");
    roam_relay_input(r,b,roam_client_hello(&c,b),&a,11000);
    CHECK(c.welcomed && r->st.clients==1,"cookie_rotation_recovery");
    CHECK(src && src->last==6000,"source_recovery_no_refresh");
    // Check absolute age independently of key retirement, including a recent wrap.
    uint8_t cookie[20], body[ROAM_HELLO_BASE]={0}; uint64_t issued=UINT32_MAX-500;
    CHECK(roam_cookie_make(key,&a,ROAM_VERSION,body,issued,cookie),"cookie_age_make");
    CHECK(roam_cookie_valid(key,&a,ROAM_VERSION,body,cookie,issued+10000),"cookie_age_boundary");
    CHECK(!roam_cookie_valid(key,&a,ROAM_VERSION,body,cookie,issued+10001),"cookie_age_expired");
    CHECK(!roam_cookie_valid(key,&a,ROAM_VERSION,body,cookie,issued-1),"cookie_age_future");
    uint64_t absolute=r->absolute_ms; relay_time(r,10999);
    CHECK(r->absolute_ms==absolute,"cookie_clock_backward");
    roam_relay_tick(r,71000);
    CHECK(!r->st.source_records,"source_refresh_cannot_pin");
    roam_relay_free(r);
    // Validated retries from a different port must not refresh a full-room record either.
    r=roam_test_relay(-1,1,1); roam_client_init(&c,7,"first");
    CHECK(roam_test_join(r,&c,&a,0),"retention_first_join");
    a.sin_port=htons(10001); memset(&out,0,sizeof out); roam_relay_sender(r,capture,&out);
    RoamClient other; roam_client_init(&other,7,"full"); out.client=&other; out.now=1000;
    roam_relay_input(r,b,roam_client_hello(&other,b),&a,1000);
    int n=roam_client_hello(&other,b);
    roam_relay_input(r,b,n,&a,1000); out.now=4000;
    roam_relay_input(r,b,n,&a,4000);
    CHECK(other.last_err==ROAM_ERR_FULL && r->sources[0].last==0,"source_full_retry_no_refresh");
    roam_relay_tick(r,60000);
    CHECK(!r->st.source_records,"source_full_retry_expiry");
    roam_relay_free(r);
    // Fixed reply buckets must recover after a quiet interval longer than half a clock wrap.
    r=roam_relay_new(-1,1,1); memset(&out,0,sizeof out); roam_relay_sender(r,capture,&out);
    roam_client_init(&c,7,"idle"); int request=roam_client_hello(&c,b);
    for (int i=0;i<100;++i) roam_relay_input(r,b,request,&a,1000);
    CHECK(out.packets==100,"reply_idle_exhausted");
    uint64_t end=(UINT64_C(1)<<31)+1001;
    for (uint64_t t=1000+86400000;t<end;t+=86400000) roam_relay_tick(r,(uint32_t)t);
    roam_relay_tick(r,(uint32_t)end);
    out.client=&c; out.now=(uint32_t)end;
    roam_relay_input(r,b,request,&a,(uint32_t)end);
    CHECK(c.has_cookie,"reply_idle_refill");
    roam_relay_free(r);
    // Simulate a process pause spanning an entire input-clock wrap with no service calls.
    r=roam_relay_new(-1,1,1); memset(&out,0,sizeof out); roam_relay_sender(r,capture,&out);
    hello(r,&out,&a,1000,b);
    r->key_clock_ms=clock_us()/1000-(UINT64_C(1)<<32);
    roam_relay_input(r,b,ROAM_HELLO_BYTES,&a,1001);
    CHECK(!r->st.clients && !r->previous_key,"cookie_paused_wrap");
    roam_relay_free(r);
}
#endif
static void egress_test(void) {
    RoamRelay *r=roam_relay_new(-1,32,4); Capture out={0}; roam_relay_sender(r,capture,&out);
    struct sockaddr_in a=address(0x7f000001,10000); uint8_t b[1200]={0};
    // The common path bounds every message type and counts headers, including send attempts.
    r->now=UINT32_MAX-500;
    for (int i=0;i<1000;++i) (void)tx(r,&a,b,9);
    CHECK(out.packets==512 && out.bytes==512*37,"egress_packet_burst");
    r->now+=10;
    for (int i=0;i<1000;++i) (void)tx(r,&a,b,9);
    CHECK(out.packets==593,"egress_packet_refill_fraction");
    r->now+=1; (void)tx(r,&a,b,9);
    CHECK(out.packets==594,"egress_packet_fraction_retained");
    roam_relay_free(r);
    r=roam_relay_new(-1,32,4); memset(&out,0,sizeof out); roam_relay_sender(r,capture,&out);
    uint32_t start=UINT32_MAX-500;
    for (int step=0;step<=1000;++step) {
        r->now=start+(uint32_t)step;
        for (int i=0;i<100;++i) (void)tx(r,&a,b,1200);
        CHECK(out.bytes<=256*1024+(uint64_t)step*4*1024*1024/1000,"egress_byte_rate_bound");
        CHECK(out.packets<=512+(uint64_t)step*8192/1000,"egress_packet_rate_bound");
    }
    CHECK(out.bytes>4*1024*1024,"egress_refill_progress");
    CHECK(out.bytes==roam_relay_stats(r).tx_wire_bytes,"egress_measured_wire_bytes");
    printf("egress: bytes=%llu <=4456448 packets=%llu suppressed=%llu duration_ms=1000\n",(unsigned long long)out.bytes,(unsigned long long)out.packets,(unsigned long long)roam_relay_stats(r).tx_suppressed);
    roam_relay_free(r);
    // Constrain output only after genuine exchanges establish all sixteen peers.
    r=roam_test_relay(-1,32,16); RoamClient *c=calloc(16,sizeof *c); CHECK(c!=NULL,"fairness_storage");
    for (int i=0;i<16;++i) { a=address(0x7f000001,10000+(unsigned)i); roam_client_init(&c[i],7,"fair"); CHECK(roam_test_join(r,&c[i],&a,0),"fairness_join"); }
    r->policy=roam_relay_policy(); r->policy.egress_packets=20; r->policy.egress_packet_burst=1;
    r->policy.egress_bytes=24000; r->policy.egress_byte_burst=1200;
    memset(&r->egress_packets,0,sizeof r->egress_packets); memset(&r->egress_bytes,0,sizeof r->egress_bytes);
    memset(&out,0,sizeof out); roam_relay_sender(r,capture,&out);
    for (int tick=0;tick<320;++tick) {
        uint32_t now=(uint32_t)tick*50;
        for (int i=0;i<16;++i) {
            a=address(0x7f000001,10000+(unsigned)i); RoamState st={.t=now,.q={0,0,0,32767},.flags=ROAM_FLAG_CAR};
            int n=roam_client_state(&c[i],&st,b); roam_relay_input(r,b,n,&a,now);
        }
        roam_relay_tick(r,now);
    }
    uint64_t least=UINT64_MAX,most=0; int kinds=1;
    for (int i=0;i<16;++i) { if (out.bundles[i]<least) least=out.bundles[i]; if (out.bundles[i]>most) most=out.bundles[i]; if (!out.members[i] || !out.controls[i]) kinds=0; }
    CHECK(least>0 && most-least<=4,"egress_fair_rotation");
    CHECK(kinds && least>0,"egress_fair_message_kinds");
    CHECK(out.packets<=1+20*15950/1000 && out.bytes<=1200+24000*15950/1000,"egress_fairness_wire_bound");
    printf("fairness: bundles/client=%llu..%llu packets=%llu bound=320\n",(unsigned long long)least,(unsigned long long)most,(unsigned long long)out.packets);
    free(c); roam_relay_free(r);
}
static void parse_test(void) {
    RoamRelay *r=roam_relay_new(-1,32,4); Capture out={0}; roam_relay_sender(r,capture,&out);
    struct sockaddr_in a=address(0x7f000001,10000); uint8_t b[1200]; hello(r,&out,&a,1000,b);
    uint64_t before=out.packets;
    for (int n=0;n<ROAM_HELLO_BYTES+2;++n) if (n!=ROAM_HDR+ROAM_HELLO_BASE && n!=ROAM_HELLO_BYTES) roam_relay_input(r,b,n,&a,1100+(unsigned)n*10);
    CHECK(!roam_relay_stats(r).clients && out.packets==before,"malformed_trailing_hello");
    RoamLook l; uint8_t look[8]={0}; CHECK(roam_look_get(look,8,&l)<0,"look_trailing_bytes"); roam_relay_free(r);
    RoamBucket bucket={0}; roam_refill(&bucket,UINT32_MAX-4,10,1); CHECK(roam_take(&bucket,1),"bucket_initial");
    roam_refill(&bucket,95,10,1); CHECK(roam_take(&bucket,1),"bucket_wrap_refill");
    roam_refill(&bucket,94,10,1); CHECK(!roam_take(&bucket,1),"bucket_backward_no_credit");
}
static void service_test(void) {
    RoamRelay *r=roam_relay_new(-1,32,4); flood_us=1000000; flood=1;
    for (int i=0;i<5;++i) {
        int n=roam_relay_service(r,1000+(unsigned)i*50);
        CHECK(n>=0 && n<100,"service_elapsed_bound");
        CHECK(roam_relay_stats(r).ticks>=(unsigned)i+1,"service_tick_progress");
    }
    CHECK(roam_relay_stats(r).receive_yields==5,"service_flood_yields"); flood=0; roam_relay_free(r);
}
static void client_test(void) {
    RoamClient c; roam_client_init(&c,7,"peer"); c.last_err=ROAM_ERR_FULL;
    RoamRelay *r=roam_relay_new(-1,32,4); struct sockaddr_in a=address(0x7f000001,10000);
    CHECK(roam_test_join(r,&c,&a,1000) && !c.last_err,"client_error_resolved");
    roam_relay_free(r); r=roam_relay_new(-1,32,4);
    uint8_t b[1200]; Capture out={.client=&c,.now=1000}; roam_relay_sender(r,capture,&out);
    roam_relay_input(r,b,roam_client_hello(&c,b),&a,1000);
    CHECK(!c.welcomed && !c.id && !c.has_members && c.has_cookie,"client_restart_challenge");
    uint32_t bad=c.rx_bad;
    roam_client_input(&c,out.last,27,1000); roam_client_input(&c,out.last,29,1000);
    CHECK(c.rx_bad==bad+2,"client_challenge_exact_length");
    c.last_err=ROAM_ERR_VERSION;
    roam_relay_input(r,b,roam_client_hello(&c,b),&a,1000);
    CHECK(c.welcomed && !c.last_err,"client_restart_recovery"); roam_relay_free(r);
}
int main(int argc, char **argv) {
    if (argc!=2) return 2;
    if (!strcmp(argv[1],"cookie")) cookie_test();
    else if (!strcmp(argv[1],"global")) global_test();
    else if (!strcmp(argv[1],"source")) source_test();
    else if (!strcmp(argv[1],"egress")) egress_test();
    else if (!strcmp(argv[1],"parse")) parse_test();
    else if (!strcmp(argv[1],"service")) service_test();
    else if (!strcmp(argv[1],"client")) client_test();
    else if (!strcmp(argv[1],"isolation")) isolation_test();
    else if (!strcmp(argv[1],"spoof")) spoof_test();
    else if (!strcmp(argv[1],"revival")) revival_test();
#ifdef ROAM_KEY_MS
    else if (!strcmp(argv[1],"rotation")) rotation_test();
#endif
    else return 2;
    printf("admission %s: checks=%d failures=%d\n",argv[1],checks,failures); return failures?1:0;
}
