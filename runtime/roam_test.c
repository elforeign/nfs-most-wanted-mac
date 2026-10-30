// roam_test.c - online free roam core + relay over 127.0.0.1 (driver: roam_test.py, which also builds the broken run).
// Three clients and one relay on loopback sockets, on a simulated clock: A drives a 100 m circle at 20 m/s and sends
// 20 states/s with its clock 12.345 s ahead; the relay's bundles to B pass a shim that drops 20 % and delays each by
// 0-120 ms (so they arrive out of order). B's interpolated pose of A is compared with the true path at the render time.
// Also: LOOK forwarding, nearest-N interest (near 1), version mismatch answered once, rate limit under a flood,
// timeout, and a 1000-client relay tick timed without sockets. Exit 0 pass, 1 fail, 3 socket EPERM (SOCKET PENDING).
// Recorded: see roam_test.py (normal and broken numbers).
#include "roam_core.h"

#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <math.h>
#include <netinet/in.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

static int fails;
#define CHECK(c, ...) do { if (!(c)) { fails++; printf("FAIL: "); printf(__VA_ARGS__); printf("\n"); } } while (0)

static uint32_t rng = 2463534242u;
static uint32_t xr(void) { rng ^= rng << 13; rng ^= rng >> 17; rng ^= rng << 5; return rng; }

static int mksock(struct sockaddr_in *a) {
    int fd = socket(AF_INET, SOCK_DGRAM, 0);
    if (fd < 0) return -errno;
    memset(a, 0, sizeof *a);
    a->sin_family = AF_INET; a->sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if (bind(fd, (struct sockaddr *)a, sizeof *a)) { int e = errno; close(fd); return -e; }
    socklen_t l = sizeof *a;
    getsockname(fd, (struct sockaddr *)a, &l);
    fcntl(fd, F_SETFL, O_NONBLOCK);
    return fd;
}

static void truth(double t_ms, float pos[3], float vel[3], float q[4]) {
    double w = 20.0 / 100.0, a = w * t_ms / 1000.0;
    pos[0] = (float)(100 * cos(a)); pos[1] = (float)(100 * sin(a)); pos[2] = 5;
    vel[0] = (float)(-20 * sin(a)); vel[1] = (float)(20 * cos(a)); vel[2] = 0;
    double h = a + M_PI / 2;   // heading, a rotation about z
    q[0] = 0; q[1] = 0; q[2] = (float)sin(h / 2); q[3] = (float)cos(h / 2);
}

static void tostate(RoamState *s, uint32_t t, const float pos[3], const float vel[3], const float q[4]) {
    memset(s, 0, sizeof *s);
    s->t = t;
    memcpy(s->pos, pos, sizeof s->pos); memcpy(s->vel, vel, sizeof s->vel);
    for (int i = 0; i < 4; ++i) s->q[i] = (int16_t)lrintf(q[i] * 32767);
}

typedef struct { uint32_t at; int n; uint8_t b[ROAM_MAX_DGRAM]; } Late;
typedef struct {
    int fd;
    struct sockaddr_in a;
    RoamClient c;
    int lossy;
    Late late[256];
    int nlate;
} Peer;

static struct sockaddr_in relay_addr;

static void psend(Peer *p, const uint8_t *b, int n) {
    sendto(p->fd, b, (size_t)n, 0, (struct sockaddr *)&relay_addr, sizeof relay_addr);
}

// Wait until loopback delivery settles (up to 2 ms of real time), then read everything queued.
static void prx(Peer *p, uint32_t now) {
    struct pollfd pf = {.fd = p->fd, .events = POLLIN};
    while (poll(&pf, 1, 2) > 0) {
        uint8_t b[2048];
        ssize_t n = recv(p->fd, b, sizeof b, 0);
        if (n <= 0) break;
        if (p->lossy && n > 3 && b[3] == ROAM_BUNDLE) {
            if (xr() % 100 < 20) continue;
            if (p->nlate < 256) {
                Late *l = &p->late[p->nlate++];
                l->at = now + xr() % 121; l->n = (int)n; memcpy(l->b, b, (size_t)n);
            }
            continue;
        }
        roam_client_input(&p->c, b, (int)n, now);
    }
    for (int i = 0; i < p->nlate;)
        if ((int32_t)(now - p->late[i].at) >= 0) {
            roam_client_input(&p->c, p->late[i].b, p->late[i].n, now);
            p->late[i] = p->late[--p->nlate];
        } else i++;
}

static void relay_rx(RoamRelay *r, int fd, uint32_t now) {
    struct pollfd pf = {.fd = fd, .events = POLLIN};
    while (poll(&pf, 1, 1) > 0) {
        if (roam_relay_service(r, now) == 0) break;
    }
    roam_relay_service(r, now);
}

static void hello_all(RoamRelay *r, int rfd, Peer *p, int n, uint32_t *now) {
    for (int k = 0; k < 20; ++k) {
        int all = 1;
        for (int i = 0; i < n; ++i) if (!p[i].c.welcomed) { uint8_t b[64]; psend(&p[i], b, roam_client_hello(&p[i].c, b)); all = 0; }
        if (all) return;
        relay_rx(r, rfd, *now);
        for (int i = 0; i < n; ++i) prx(&p[i], *now);
        *now += 5;
    }
}

static void send_static(Peer *p, uint32_t now, float x, float y) {
    float pos[3] = {x, y, 5}, vel[3] = {0}, q[4] = {0, 0, 0, 1};
    RoamState s; tostate(&s, now, pos, vel, q);
    uint8_t b[64]; psend(p, b, roam_client_state(&p->c, &s, b));
}

static double now_s(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec + t.tv_nsec / 1e9; }


// Receiver membership has its own ordering; a delayed bundle must not resurrect an evicted id.
static int members_packet(uint8_t *b, uint16_t seq, uint16_t id, int tier, uint16_t state_seq) {
    int n = roam_hdr_put(b, ROAM_MEMBERS, 0, seq); b[n++] = id ? 1 : 0;
    if (id) { b[n++] = (uint8_t)id; b[n++] = (uint8_t)(id >> 8); b[n++] = (uint8_t)tier;
        b[n++] = (uint8_t)state_seq; b[n++] = (uint8_t)(state_seq >> 8); }
    return n;
}
static int bundle_packet(uint8_t *b, uint16_t id, uint16_t seq, uint32_t now) {
    RoamState st = {.t=now, .pos={12,0,0}, .q={0,0,0,32767}, .flags=ROAM_FLAG_CAR};
    int n=roam_hdr_put(b,ROAM_BUNDLE,0,1); b[n++]=1;
    b[n++]=(uint8_t)id; b[n++]=(uint8_t)(id>>8); b[n++]=(uint8_t)seq; b[n++]=(uint8_t)(seq>>8);
    roam_state_put(b+n,&st); return n+ROAM_STATE_BYTES;
}
static void membership_checks(void) {
    RoamClient c; roam_client_init(&c,7,"membership"); c.id=1; c.welcomed=1;
    uint8_t b[ROAM_MAX_DGRAM]; float p[3],q[4],v[3];
    roam_client_input(&c,b,members_packet(b,65535,2,0,100),1000);
    roam_client_input(&c,b,bundle_packet(b,2,99,950),1000);
    CHECK(!roam_view_find(&c.view,2),"membership: pre-entry bundle admitted");
    roam_client_input(&c,b,bundle_packet(b,2,100,1000),1000);
    CHECK(roam_view_pose(&c.view,2,1050,p,q,v),"membership: current entry missing");
    roam_client_input(&c,b,members_packet(b,0,2,0,101),1100);
    roam_client_input(&c,b,bundle_packet(b,2,100,1000),1900);
    CHECK(!roam_view_pose(&c.view,2,2101,p,q,v),"freshness: duplicate refreshed pose");
    // After more than half a state-sequence cycle, a continuously present member still accepts new states.
    roam_client_input(&c,b,members_packet(b,1,2,0,40000),2000);
    roam_client_input(&c,b,bundle_packet(b,2,40000,2000),2000);
    CHECK(roam_view_find(&c.view,2)->s[roam_view_find(&c.view,2)->n-1].t==2000,
          "membership: long-lived entry hit sequence half-wrap");
    roam_client_input(&c,b,members_packet(b,2,0,0,0),2050);
    roam_client_input(&c,b,bundle_packet(b,2,40000,2000),2060);
    roam_client_input(&c,b,members_packet(b,1,2,0,40000),2070);
    CHECK(!roam_view_find(&c.view,2) && c.nmembers==0,"membership: delayed packet resurrected entry");
    roam_client_input(&c,b,members_packet(b,3,2,2,50000),2100);
    roam_client_input(&c,b,bundle_packet(b,2,40000,2000),2100);
    CHECK(!roam_view_find(&c.view,2),"membership: old incarnation state admitted");
    roam_client_input(&c,b,bundle_packet(b,2,50000,2100),2100);
    CHECK(roam_view_find(&c.view,2) && !roam_view_pose(&c.view,2,2150,p,q,v),"membership: map tier became drawable");
    roam_client_input(&c,b,members_packet(b,4,2,0,50000),2200);
    CHECK(roam_view_pose(&c.view,2,2250,p,q,v),"membership: promotion did not enable pose");
    roam_client_input(&c,b,bundle_packet(b,2,50001,3300),3300);
    CHECK(!roam_view_pose(&c.view,2,3300,p,q,v),"membership: expired control accepted bundle");
    uint32_t bad=c.rx_bad;
    roam_client_input(&c,b,members_packet(b,5,2,3,50000),3400);
    CHECK(c.rx_bad==bad+1 && c.member_seq==4,"membership: invalid tier mutated snapshot");
    uint16_t pins[5]={2,3,4,5,6};
    CHECK(roam_client_pins(&c,pins,4,b)==ROAM_HDR+9 && !roam_client_pins(&c,pins,5,b),"pins: bound");
    puts("membership: wrap, reordering, leave/rejoin, map tier, promotion, stale state and bounded pins checked");
}
static int tier_of(const RoamClient *c,uint16_t id) {
    for (int i=0;i<c->nmembers;++i) if (c->members[i]==id) return c->tiers[i];
    return -1;
}
static void interest_boundaries(int rfd,uint32_t *clock) {
    RoamRelay *r=roam_relay_new(rfd,32,8); Peer peers[3]; memset(peers,0,sizeof peers);
    for (int i=0;i<3;++i) { peers[i].fd=mksock(&peers[i].a); roam_client_init(&peers[i].c,9,"boundary"); }
    hello_all(r,rfd,peers,3,clock);
    for (int phase=0;phase<5;++phase) {
        if (phase==1) {
            uint8_t b[64]; uint16_t pin=peers[2].c.id;
            psend(&peers[0],b,roam_client_pins(&peers[0].c,&pin,1,b));
        }
        if (phase==3) { uint8_t b[64]; psend(&peers[0],b,roam_client_pins(&peers[0].c,NULL,0,b)); }
        uint32_t duration=phase==4?1100:600;
        for (uint32_t t0=*clock;*clock<t0+duration;*clock+=10) {
            if ((*clock-t0)%50==0) {
                send_static(&peers[0],*clock,0,0); send_static(&peers[2],*clock,2400,0);
                if (phase<4) {
                    float x=phase==0?90:phase==1?110:phase==2?120:200;
                    if (phase==2) {
                        RoamState st={.t=*clock,.pos={120,0,5},.vel={-80,0,0},.q={0,0,0,32767},.flags=ROAM_FLAG_CAR};
                        uint8_t b[64]; psend(&peers[1],b,roam_client_state(&peers[1].c,&st,b));
                    } else send_static(&peers[1],*clock,x,0);
                } else {
                    RoamLook look={.rev=2,.car=123}; uint8_t b[256];
                    psend(&peers[1],b,roam_client_look(&peers[1].c,&look,b));
                }
            }
            relay_rx(r,rfd,*clock); for (int i=0;i<3;++i) prx(&peers[i],*clock);
        }
        int b=tier_of(&peers[0].c,peers[1].c.id), c=tier_of(&peers[0].c,peers[2].c.id);
        CHECK(b==(phase<3?0:phase==3?1:-1),"tier boundary phase %d B=%d",phase,b);
        CHECK(c==(phase==1 || phase==2?2:-1),"friend pin phase %d C=%d",phase,c);
    }
    CHECK(roam_relay_stats(r).clients==3,"LOOK heartbeat should retain connection, not state interest");
    CHECK(!roam_view_find(&peers[0].c.view,peers[1].c.id),"stale state retained by LOOK heartbeat");
    roam_relay_free(r); for (int i=0;i<3;++i) close(peers[i].fd);
    puts("interest boundaries: 100 m hysteresis, approach promotion, distant pin/unpin, stale LOOK-only peer checked");
}

int main(int argc, char **argv) {
    membership_checks();
    if (argc == 2 && !strcmp(argv[1], "--membership-only")) return fails ? 1 : 0;
    struct sockaddr_in ra;
    int rfd = mksock(&ra);
    if (rfd == -EPERM || rfd == -EACCES) { puts("SOCKET PENDING: run on a socket-enabled host (roam_test)"); return 3; }
    if (rfd < 0) { printf("FAIL: relay socket %d\n", rfd); return 1; }
    relay_addr = ra;
    printf("roam relay on %s:%d (loopback)\n", inet_ntoa(ra.sin_addr), ntohs(ra.sin_port));

    // ---- phase 1: interpolation through loss and reordering, LOOK forwarding
    RoamRelay *r = roam_relay_new(rfd, ROAM_NEAR_DEFAULT, 64);
    Peer P[3];
    memset(P, 0, sizeof P);
    const char *names[3] = {"A", "B", "C"};
    for (int i = 0; i < 3; ++i) {
        P[i].fd = mksock(&P[i].a);
        if (P[i].fd < 0) { printf("FAIL: client socket %d\n", P[i].fd); return 1; }
        roam_client_init(&P[i].c, 7, names[i]);
    }
    P[1].lossy = 1;
    uint32_t now = 1000;
    hello_all(r, rfd, P, 3, &now);
    CHECK(P[0].c.welcomed && P[1].c.welcomed && P[2].c.welcomed, "welcome");
    uint16_t idA = P[0].c.id;
    const uint32_t skew = 12345;
    double maxe = 0, sume = 0, maxq = 0, lmin = 1e9, lmax = -1e9; int ne = 0, nposed = 0;
    RoamLook look = {.rev = 1, .nparts = 3, .car = 0xC0FFEE01u, .parts = {11, 22, 33}};
    for (uint32_t t0 = now; now < t0 + 4000; now += 5) {
        if ((now - t0) % 50 == 0) {
            float pos[3], vel[3], q[4];
            truth(now, pos, vel, q);
            RoamState s; tostate(&s, now + skew, pos, vel, q);
            uint8_t b[256];
            psend(&P[0], b, roam_client_state(&P[0].c, &s, b));
            if ((now - t0) % 1000 == 0) psend(&P[0], b, roam_client_look(&P[0].c, &look, b));
            send_static(&P[1], now, 100, 0);
            send_static(&P[2], now, 0, 0);
        }
        relay_rx(r, rfd, now);
        for (int i = 0; i < 3; ++i) prx(&P[i], now);
        if (now - t0 < 1000) continue;
        float pos[3], q[4], vel[3];
        if (!roam_view_pose(&P[1].c.view, idA, now, pos, q, vel)) continue;
        nposed++;
        // Judged against the true path only (no core internals): the radial error from the 100 m circle, and the lag
        // behind A's true position now along the circle. The lag must sit at the view delay plus the least network
        // delay, and stay steady: a render clock that follows each packet's delay jitters by the 0-120 ms shim.
        double rad = fabs(hypot(pos[0], pos[1]) - 100) + fabs(pos[2] - 5);
        double w = 0.2, ph = atan2(pos[1], pos[0]), tph = fmod(w * now / 1000.0, 2 * M_PI);
        double dph = remainder(tph - ph, 2 * M_PI), lag = dph / w * 1000;
        float tp[3], tv[3], tq[4];
        truth(now - lag, tp, tv, tq);
        double d = fabs(q[0] * tq[0] + q[1] * tq[1] + q[2] * tq[2] + q[3] * tq[3]);
        double qa = 2 * acos(d > 1 ? 1 : d) * 180 / M_PI;
        if (rad > maxe) maxe = rad;
        if (qa > maxq) maxq = qa;
        if (lag < lmin) lmin = lag;
        if (lag > lmax) lmax = lag;
        sume += rad; ne++;
    }
    const RoamRemote *ra_b = roam_view_find(&P[1].c.view, idA);
    int32_t off = ra_b ? ra_b->off[0] : 0;
    for (int i = 1; ra_b && i < ra_b->noff; ++i) if (ra_b->off[i] < off) off = ra_b->off[i];
    int32_t lat = off + (int32_t)skew;   // the least one-way delay seen, ms
    printf("interp: %d poses, off-path error mean %.4f m max %.4f m, orientation max %.3f deg, lag %.1f..%.1f ms "
           "(spread %.1f), clock min delay %d ms, late %u dup %u, bundles %u\n", ne, ne ? sume / ne : 0, maxe, maxq,
           lmin, lmax, lmax - lmin, lat,
           ra_b ? ra_b->dropped_late : 0, ra_b ? ra_b->dropped_dup : 0, P[1].c.rx_bundles);
    CHECK(nposed > 500, "B posed A only %d times", nposed);
    CHECK(maxe < 0.05, "off-path error max %.4f m >= 0.05", maxe);
    CHECK(lmin >= 100 && lmax <= 170 && lmax - lmin < 25, "lag %.1f..%.1f ms: not delay + 0..70 ms or spread >= 25", lmin, lmax);
    CHECK(maxq < 2.0, "orientation error max %.3f deg >= 2", maxq);
    CHECK(lat >= 0 && lat <= 60, "clock offset: least delay %d ms outside 0..60", lat);
    CHECK(ra_b && ra_b->has_look && ra_b->look.car == 0xC0FFEE01u && ra_b->look.nparts == 3 && ra_b->look.parts[2] == 33,
          "LOOK not forwarded to B");
    const RoamRemote *c_sees_a = roam_view_find(&P[2].c.view, idA);
    CHECK(c_sees_a && c_sees_a->n > 0, "C does not see A");

    // ---- version mismatch: dropped, answered once per address
    uint8_t bad[ROAM_HDR + ROAM_STATE_BYTES];
    roam_hdr_put(bad, ROAM_STATE, P[2].c.id, 999); bad[2] = ROAM_VERSION + 1;
    RoamRelayStats s0 = roam_relay_stats(r);
    P[2].c.last_err = 0;
    for (int k = 0; k < 3; ++k) { psend(&P[2], bad, sizeof bad); relay_rx(r, rfd, now); prx(&P[2], now); now += 5; }
    RoamRelayStats s1 = roam_relay_stats(r);
    printf("version: %" PRIu64 " dropped, client error %d\n", s1.rx_version - s0.rx_version, P[2].c.last_err);
    CHECK(s1.rx_version - s0.rx_version == 3 && P[2].c.last_err == ROAM_ERR_VERSION, "version mismatch handling");

    // ---- rate limit: 200 states at once from C
    for (int k = 0; k < 200; ++k) send_static(&P[2], now, 0, 0);
    relay_rx(r, rfd, now);
    RoamRelayStats s2 = roam_relay_stats(r);
    printf("rate: %" PRIu64 " of 200 flood packets dropped\n", s2.rx_rate_dropped - s1.rx_rate_dropped);
    CHECK(s2.rx_rate_dropped - s1.rx_rate_dropped >= 170, "rate limit let %" PRIu64 " through", UINT64_C(200) - (s2.rx_rate_dropped - s1.rx_rate_dropped));

    // ---- timeout: C goes silent for 6 s
    uint64_t before = roam_relay_stats(r).clients;
    for (uint32_t t0 = now; now < t0 + 6000; now += 50) {
        float pos[3], vel[3], q[4];
        truth(now, pos, vel, q);
        RoamState s; tostate(&s, now + skew, pos, vel, q);
        uint8_t b[64];
        psend(&P[0], b, roam_client_state(&P[0].c, &s, b));
        send_static(&P[1], now, 100, 0);
        relay_rx(r, rfd, now);
        for (int i = 0; i < 3; ++i) prx(&P[i], now);
    }
    RoamRelayStats s3 = roam_relay_stats(r);
    printf("timeout: clients %" PRIu64 " -> %" PRIu64 "\n", before, s3.clients);
    CHECK(before == 3 && s3.clients == 2 && s3.timeouts >= 1, "timeout");
    roam_relay_free(r);
    for (int i = 0; i < 3; ++i) close(P[i].fd);

    // ---- interest: near 1. A (0,0), B (10,0), C (1000,0): A and C see only B, B sees only A.
    r = roam_relay_new(rfd, 1, 64);
    memset(P, 0, sizeof P);
    for (int i = 0; i < 3; ++i) { P[i].fd = mksock(&P[i].a); roam_client_init(&P[i].c, 7, names[i]); }
    hello_all(r, rfd, P, 3, &now);
    float xs[3] = {0, 10, 1000};
    for (uint32_t t0 = now; now < t0 + 500; now += 5) {
        if ((now - t0) % 50 == 0) for (int i = 0; i < 3; ++i) send_static(&P[i], now, xs[i], 0);
        relay_rx(r, rfd, now);
        for (int i = 0; i < 3; ++i) prx(&P[i], now);
    }
    uint16_t ia = P[0].c.id, ib = P[1].c.id, ic = P[2].c.id;
    int ok = roam_view_find(&P[0].c.view, ib) && !roam_view_find(&P[0].c.view, ic) &&
             roam_view_find(&P[1].c.view, ia) && !roam_view_find(&P[1].c.view, ic) &&
             roam_view_find(&P[2].c.view, ib) && !roam_view_find(&P[2].c.view, ia);
    printf("interest: near 1 links %s\n", ok ? "A-B, B-A, C-B" : "wrong");
    CHECK(ok, "nearest-1 interest");
    roam_relay_free(r);
    for (int i = 0; i < 3; ++i) close(P[i].fd);
    interest_boundaries(rfd, &now);
    // A real send failure must count as failure, never successful output bytes.
    int badfd=dup(rfd); r=roam_relay_new(badfd,32,2);
    CHECK(r && (fcntl(badfd,F_GETFL)&O_NONBLOCK),"relay socket is blocking");
    close(badfd);
    RoamClient error_client; roam_client_init(&error_client,1,"error"); uint8_t eb[64];
    roam_relay_input(r,eb,roam_client_hello(&error_client,eb),&ra,now);
    RoamRelayStats errors=roam_relay_stats(r);
    CHECK(errors.tx_attempts==1 && errors.tx_errors==1 && !errors.tx && !errors.tx_bytes,"send error counted as success");
    roam_relay_free(r);
    close(rfd);

    // ---- 1000 clients, no sockets: tick cost and bytes per client
    r = roam_relay_new(-1, ROAM_NEAR_DEFAULT, 1024);
    for (int i = 0; i < 1000; ++i) {
        struct sockaddr_in a = {.sin_family = AF_INET, .sin_port = htons((uint16_t)(20000 + i)), .sin_addr.s_addr = htonl(0x0A000000u + (uint32_t)i)};
        RoamClient c; roam_client_init(&c, 1, "x"); c.id = (uint16_t)(i + 1);
        uint8_t b[256];
        roam_relay_input(r, b, roam_client_hello(&c, b), &a, 0);
        // half spread over 4 km x 4 km, half in one 300 m crowd (the worst case for interest)
        float pos[3] = {i < 500 ? (float)(xr() % 4000) : 2000 + (float)(xr() % 300), i < 500 ? (float)(xr() % 4000) : 2000 + (float)(xr() % 300), 0};
        float vel[3] = {0}, q[4] = {0, 0, 0, 1};
        RoamState s; tostate(&s, 0, pos, vel, q);
        roam_relay_input(r, b, roam_client_state(&c, &s, b), &a, 0);
    }
    RoamRelayStats b0 = roam_relay_stats(r);
    double t = now_s();
    for (int k = 1; k <= 20; ++k) roam_relay_tick(r, (uint32_t)(k * 50));
    double el = now_s() - t;
    RoamRelayStats b1 = roam_relay_stats(r);
    printf("scale: 1000 clients, 20 ticks (1 s): %.1f ms CPU/s (%.1f %% of a core, sockets excluded), out %" PRIu64 " datagrams/s, "
           "%.1f KB/s per client\n", el * 1000, el * 100, b1.tx - b0.tx, (b1.tx_bytes - b0.tx_bytes) / 1000.0 / 1000);
    CHECK(b1.clients == 1000, "1000 clients registered (%" PRIu64 ")", b1.clients);
    roam_relay_free(r);

    // ---- car appearing: no-car snapshots at the origin, then the car 2 km away; the pose must never blend the two.
    {
        RoamView v;
        roam_view_init(&v);
        float worst = 0;
        for (int k = 0; k < 40; ++k) {
            RoamState st = {0};
            st.t = (uint32_t)(k * 50); st.q[3] = 32767;
            if (k >= 20) { st.flags = ROAM_FLAG_CAR; st.pos[0] = 2000 + (k - 20) * 1.0f; st.vel[0] = 20; }
            roam_view_add(&v, 9, (uint16_t)k, &st, st.t);
            for (int f = 0; f < 50; f += 10) {
                float p[3], q[4], vv[3];
                if (k >= 20 && roam_view_pose(&v, 9, st.t + (uint32_t)f, p, q, vv) && p[0] > 1 && p[0] < 1999 && p[0] - 1 > worst)
                    worst = p[0] - 1;
            }
        }
        printf("appear: worst blended pose %.1f m from either side\n", worst);
        CHECK(worst == 0, "appear: pose blended across a car appearing (%.1f m)", worst);
    }

    printf(fails ? "ROAM FAIL (%d)\n" : "ROAM PASS\n", fails);
    return fails ? 1 : 0;
}
