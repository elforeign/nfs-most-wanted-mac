// A2 measured 2026-10-27: 5/5 suites, 5029 checks, 0 failures. Same checks against
// ws2_lan_test.py source controls: broadcast 5 failures; reuse 5; LAN bind 5;
// source identity 5; shifted discovery 10; route identity 5; lobby target 5.
                                                                                    
// Challenger-fix plan (2026-10-27): check accepted/connected peers; translate refusal and datagram
// truncation; bound diagnostics; capture event generations at receive submission; normalize/validate
// online race IDs; retain connect failures for select/SO_ERROR. Extend this offline harness and prove
// each required fix with a source variant, then build and run offline_tests.sh ws2_policy.
// Measured 2026-10-27: production 5/5 suites, 4879 checks, 0 failures (exit 0), also ASan/UBSan clean.
// Broken source controls: each runs the SAME 4879 checks and exits 1; failures per individual mutation:
//   1 accept guard removed: 21; connected send guard removed: 84 (including stream sendto with a safe address).
//   2 wsa_of_errno ECONNREFUSED -> 10054 instead of 10061: 30.
//   3 recv_packet sets *truncated = 0: 120 (recv/recvfrom, immediate and pending WSARecv/WSARecvFrom).
//   4 log_once fresh = i == n without the capacity/diag condition: 5.
//   5 wsa_recv captures generation 0: 50; delivery-time reuse is tested separately in async_events_test.c.
//   6 race ID taken from raw BOOT_RACE instead of G+0x926068: 5; missing-event refusal disabled: 5.
//   7 failed-connect select hit uses k == 1 instead of k == 2: 45 (write/read/except, except-only, SO_ERROR order).
//   8 select polling an fd once per set (pre-2026-10-27 ws2_32.c; the fake poll models macOS, where an fd listed for
//     POLLIN and again for POLLPRI reports nothing): 70 (14 per suite, including "listener in read and except sets is
//     readable", server.dll's lobby). With the one-entry-per-fd fix: 5/5 suites, 4889 checks, 0 failures.
//   Entire policy disabled (`return policy_open;` -> `return 1;`): 1067 failures, 1/5 suites passed.
// Restricted suites: 0 resolver calls; unsafe-open suite: 5. All strict builds: 0 warnings/errors.
// clang -O1 -Iruntime -Irecomp -Irecomp/out runtime/ws2_policy_test.c -o /tmp/ws2_policy_test
// /tmp/ws2_policy_test
// -DWS2_C='"/absolute/path/ws2_32.c"' selects a source variant with one mutation above.
// All host socket/connect/accept/send/sendto/recvmsg/poll/peer/resolver calls are fakes:
// neither the good nor the broken run can send packets or resolve a name. No game, GPU or Unicorn.
#include <arpa/inet.h>
#include <errno.h>
#include <netdb.h>
#include <poll.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdlib.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <unistd.h>
#include "host.h"

static unsigned binds, broadcasts, reuseports;
static struct sockaddr_in last_bind, last_connect;
static unsigned connects, sends, resolves, sockets, accepts, closes, connected_sends, peer_queries, error_queries;
static int connect_errno, completion_errno, recv_len = -1, recv_datagram = 1;
static short poll_revents;
static unsigned polls, poll_nonzero_timeouts;
static uint64_t ticks;
static struct sockaddr_in fake_peer;
static int fake_connect(int fd, const struct sockaddr *a, socklen_t n) {
    (void)fd; (void)n; memcpy(&last_connect, a, sizeof last_connect); ++connects;
    if (connect_errno) { errno = connect_errno; return -1; }
    return 0;
}
static int fake_accept(int fd, struct sockaddr *a, socklen_t *n) {
    (void)fd; ++accepts; memcpy(a, &fake_peer, sizeof fake_peer); *n = sizeof fake_peer; return 101;
}
static int fake_getpeername(int fd, struct sockaddr *a, socklen_t *n) {
    (void)fd; ++peer_queries; memcpy(a, &fake_peer, sizeof fake_peer); *n = sizeof fake_peer; return 0;
}
static ssize_t fake_send(int fd, const void *buf, size_t n, int flags) {
    (void)fd; (void)buf; (void)flags; ++connected_sends; return (ssize_t)n;
}
static int fake_getsockopt(int fd, int level, int name, void *out, socklen_t *n) {
    (void)fd; (void)level; (void)name; ++error_queries;
    memcpy(out, &completion_errno, sizeof completion_errno); *n = sizeof completion_errno;
    completion_errno = 0; return 0;
}
static int fake_poll(struct pollfd *p, nfds_t n, int ms) {
    ++polls; poll_nonzero_timeouts += ms != 0;
    short re = poll_revents; poll_revents = 0;
    int ready = 0;
    for (nfds_t i = 0; i < n; ++i) {
        p[i].revents = p[i].fd < 0 ? 0 : re & (p[i].events | POLLERR | POLLHUP);
        // As macOS's poll (measured 2026-10-27): an fd listed twice (POLLIN and POLLPRI) reports nothing at all.
        for (nfds_t j = 0; j < n; ++j) if (j != i && p[j].fd >= 0 && p[j].fd == p[i].fd) p[i].revents = 0;
        ready += p[i].revents != 0;
    }
    ticks += ready ? 1000000 : ms > 0 ? (uint64_t)ms * 1000000 : 0;
    return ready;
}
static ssize_t fake_recvmsg(int fd, struct msghdr *m, int flags) {
    (void)fd;
    if (recv_len < 0) { errno = EWOULDBLOCK; return -1; }
    size_t cap = m->msg_iov[0].iov_len, n = (size_t)recv_len < cap ? (size_t)recv_len : cap;
    for (size_t i = 0; i < n; ++i) ((uint8_t *)m->msg_iov[0].iov_base)[i] = (uint8_t)(i % 251);
    m->msg_flags = recv_datagram && (size_t)recv_len > cap ? MSG_TRUNC : 0;
    if (m->msg_name) { memcpy(m->msg_name, &fake_peer, sizeof fake_peer); m->msg_namelen = sizeof fake_peer; }
    if (!(flags & MSG_PEEK)) recv_len = -1;
    return (ssize_t)n;
}
static int fake_bind(int fd, const struct sockaddr *a, socklen_t n) {
    (void)fd; (void)n; ++binds; memcpy(&last_bind, a, sizeof last_bind); return 0;
}
static int fake_fcntl(int fd, int cmd, ...) { (void)fd; (void)cmd; return 0; }
static int fake_setsockopt(int fd, int l, int n, const void *p, socklen_t len) {
    (void)fd; (void)len;
    if (l == SOL_SOCKET && n == SO_BROADCAST && *(const int *)p) ++broadcasts;
    if (l == SOL_SOCKET && n == SO_REUSEPORT && *(const int *)p) ++reuseports;
    return 0;
}
static int fake_pthread_create(pthread_t *t, const pthread_attr_t *a, void *(*f)(void *), void *p) {
    *t = pthread_self(); (void)a; (void)f; (void)p; return 0; // receives are advanced explicitly by the test
}
static int fake_pthread_detach(pthread_t t) { (void)t; return 0; }
static ssize_t fake_sendto(int fd, const void *buf, size_t n, int flags, const struct sockaddr *a, socklen_t al) {
    (void)fd; (void)buf; (void)flags; (void)a; (void)al; ++sends; return (ssize_t)n;
}
static int fake_socket(int af, int type, int proto) {
    (void)af; (void)type; (void)proto; ++sockets; return 100;
}
static int fake_close(int fd) { (void)fd; ++closes; return 0; }
static int fake_getsockname(int fd, struct sockaddr *a, socklen_t *n) {
    (void)fd; (void)n;
    struct sockaddr_in *ip = (struct sockaddr_in *)a;
    memset(ip, 0, sizeof *ip); ip->sin_family = AF_INET; ip->sin_addr.s_addr = htonl(INADDR_LOOPBACK); ip->sin_port = htons(9999);
    return 0;
}
static int fake_getaddrinfo(const char *name, const char *serv, const struct addrinfo *h, struct addrinfo **out) {
    (void)name; (void)serv; (void)h;
    static struct sockaddr_in a;
    static struct addrinfo ai;
    ++resolves; a.sin_addr.s_addr = htonl(0x12010203); ai.ai_addr = (struct sockaddr *)&a; *out = &ai; return 0;
}
static void fake_freeaddrinfo(struct addrinfo *a) { (void)a; }
#ifndef WS2_REAL_SOCKETS
#define bind fake_bind
#define connect fake_connect
#define accept fake_accept
#define getpeername fake_getpeername
#define send fake_send
#define getsockopt fake_getsockopt
#define poll fake_poll
#define recvmsg fake_recvmsg
#define fcntl fake_fcntl
#define setsockopt fake_setsockopt
#define pthread_create fake_pthread_create
#define pthread_detach fake_pthread_detach
#define sendto fake_sendto
#define socket fake_socket
#define close fake_close
#define getsockname fake_getsockname
#define getaddrinfo fake_getaddrinfo
#define freeaddrinfo fake_freeaddrinfo
#endif
#ifndef WS2_C
#define WS2_C "ws2_32.c"
#endif
#ifdef WS2_REAL_SOCKETS
#include "ws2_lan_transport.h"
#endif
#include WS2_C
#undef listen
#undef bind
#undef connect
#undef accept
#undef getpeername
#undef send
#undef getsockopt
#undef poll
#undef recvmsg
#undef fcntl
#undef setsockopt
#undef pthread_create
#undef pthread_detach
#undef sendto
#undef socket
#undef close
#undef getsockname
#undef getaddrinfo
#undef freeaddrinfo

uint8_t *G;
int diag_on;
uint32_t galloc(uint32_t n) { static uint32_t next = 0x8000; uint32_t a = next; next += n; return a; }
void gil_acquire(void) {}
void gil_release(void) {}
uint32_t threads_event_new(int manual, int initial) { (void)manual; (void)initial; return 0; }
int threads_event_set(uint32_t h, int on) { (void)h; (void)on; return 0; }
uint32_t threads_wait(Cpu *c, uint32_t n, const uint32_t *h, int all, uint32_t ms) {
    (void)c; (void)n; (void)h; (void)all; (void)ms; return 0;
}
uint64_t threads_event_generation(uint32_t h) { return h ? 123 : 0; }
void threads_set_event_generation_async(uint32_t h, uint64_t gen) { (void)h; (void)gen; }
int threads_close(uint32_t h) { (void)h; return 0; }
uint64_t host_ticks(void) { return ticks; }
void diag_event(const char *fmt, ...) { (void)fmt; }
const char *diag_symbol(uint32_t a, uint32_t *off) { (void)a; (void)off; return NULL; }
uint32_t host_fn(HostFn fn, const char *name) { (void)fn; (void)name; return 0; }
static int race_found = 1, setup_called;
static char hashed_race[32];
uint32_t call_guest(Cpu *c, uint32_t fn, int n, const uint32_t *a) {
    (void)n;
    if (fn == 0x004519D0) {
        snprintf(hashed_race, sizeof hashed_race, "%s", (char *)G + *a);
        return c->eax = !strcmp(hashed_race, "15.1.1") ? 0x12345678 : 0;
    }
    if (fn == 0x005FB710) return c->eax = race_found && *a == 0x12345678 && c->ecx == 0xA0000 ? 0xA1000 : 0;
    return 0;
}
uint32_t gstr(const char *s) { uint32_t a = galloc((uint32_t)strlen(s) + 1); strcpy((char *)G + a, s); return a; }
#define ORIG(fn) void fn(Cpu *c) { (void)c; }
ORIG(f_007A0060_orig) ORIG(f_00792230_orig) ORIG(f_00797450_orig) ORIG(f_0079FF50_orig)
ORIG(f_005FE3E0_orig) ORIG(f_00761140_orig) ORIG(f_006F2850_orig) ORIG(f_007356A0_orig)
ORIG(f_007617D0_orig) ORIG(f_00760B20_orig) ORIG(f_005FACD0_orig) ORIG(f_006F9BB0_orig) ORIG(f_0083CCC0_orig)
ORIG(f_00755140_orig)
void f_005A3590_orig(Cpu *c) { (void)c; if (!race_found) _exit(3); ++setup_called; }

static void lan_translation(void);
static unsigned checks, failures;
static void check(int ok, const char *what) {
    ++checks;
    if (!ok) { ++failures; if (failures <= 6) printf("FAIL: %s\n", what); }
}
static uint32_t invoke(const char *fn, unsigned n, ...) {
    Cpu c = {0}; c.esp = 0x1000;
    va_list ap; va_start(ap, n);
    for (unsigned i = 0; i < n; ++i) M32(c.esp + 4 + 4 * i) = va_arg(ap, uint32_t);
    va_end(ap);
    int handled = win32_ws2(&c, fn);
    check(handled == 1 && c.esp == 0x1004 + n * 4, "handled and stdcall stack");
    return c.eax;
}
static int occurrences(FILE *f, const char *text) {
    fflush(stderr); rewind(f);
    int count = 0; char line[1024];
    while (fgets(line, sizeof line, f)) count += strstr(line, text) != NULL;
    fseek(f, 0, SEEK_END); return count;
}
static void address(const char *ip, unsigned port) {
    memset(G + 0x2000, 0, 16); M16(0x2000) = AF_INET;
    M16(0x2002) = htons((uint16_t)port);
    check(inet_pton(AF_INET, ip, G + 0x2004) == 1, "test address parsed");
}
static void destination(const char *ip, int allow) {
    address(ip, 3658);
    // TCP and UDP connects, sendto and SIO_ROUTING_INTERFACE_QUERY all exercise the real dispatcher.
    for (int path = 0; path < 4; ++path) {
        unsigned before = connects + sends;
        uint32_t r;
        if (path < 2) {
            socks[0].type = path + 1;
            r = invoke("#4", 3, SOCK_BASE, 0x2000u, 16u);
        } else if (path == 2) r = invoke("#20", 6, SOCK_BASE, 0x3000u, 1u, 0u, 0x2000u, 16u);
        else r = invoke("WSAIoctl", 9, SOCK_BASE, 0xC8000014u, 0x2000u, 16u, 0x4000u, 16u, 0x4100u, 0u, 0u);
        check(r == (allow ? (path == 2 ? 1u : 0u) : UINT32_MAX), "address return value");
        check(connects + sends == before + (unsigned)allow, "host call count (blocked emits nothing)");
        if (!allow) check(invoke("#111", 0) == 10051, "WSAENETUNREACH through WSAGetLastError");
    }
}
static void hostname(const char *name, int allow, int dns, uint32_t ip) {
    if (name) strcpy((char *)G + 0x5000, name);
    unsigned before = resolves;
    uint32_t h = invoke("#52", 1, name ? 0x5000u : 0u);
    check((h != 0) == allow, "hostname return value");
    check(resolves == before + (unsigned)dns, "resolver call count");
    if (allow) check(M32(M32(M32(h + 12))) == htonl(ip), "hostent address");
    else check(invoke("#111", 0) == 11001, "WSAHOST_NOT_FOUND through WSAGetLastError");
}
static void connected_peer(const char *ip, int allow) {
    check(inet_pton(AF_INET, ip, &fake_peer.sin_addr) == 1, "peer address parsed");
    fake_peer.sin_family = AF_INET; fake_peer.sin_port = htons(3658);
    unsigned closed = closes, accepted = accepts;
    uint32_t s = invoke("#1", 3, SOCK_BASE, 0u, 0u);
    check(accepts == accepted + 1 && (s != UINT32_MAX) == allow, "accept applies peer policy even without addr output");
    check(closes == closed + (unsigned)!allow, "rejected accepted fd closed");
    if (allow) check(invoke("#3", 1, s) == 0, "accepted socket closes");
    else check(invoke("#111", 0) == 10061, "accept policy refusal is WSAECONNREFUSED");
    // Inject a connected socket independently of accept: send must enforce policy on every call.
    address("127.0.0.1", 3658);
    for (int path = 0; path < 4; ++path) {
        socks[0].type = path == 0 || path == 3 ? 1 : 2;
        unsigned sent = connected_sends + sends, queried = peer_queries;
        uint32_t r = path < 2 ? invoke("#19", 4, SOCK_BASE, 0x3000u, 1u, 0u) :
            invoke("#20", 6, SOCK_BASE, 0x3000u, 1u, 0u, path == 3 ? 0x2000u : 0u, 16u);
        check(r == (allow ? 1u : UINT32_MAX), "connected TCP/UDP send return");
        check(connected_sends + sends == sent + (unsigned)allow && peer_queries == queried + 1,
              "connected send rechecks peer and blocked send emits nothing");
        if (!allow) check(invoke("#111", 0) == 10051, "connected send unreachable");
    }
    socks[0].type = 1;
}
static void put_set(uint32_t va) { M32(va) = 1; M32(va + 4) = SOCK_BASE; }
static void start_connect(int error) {
    address("127.0.0.1", 3658); socks[0].type = 1; socks[0].guest_nonblock = 1;
    connect_errno = EINPROGRESS; completion_errno = error;
    check(invoke("#4", 3, SOCK_BASE, 0x2000u, 16u) == UINT32_MAX, "nonblocking connect pending");
    check(invoke("#111", 0) == 10035, "connect pending WSAEWOULDBLOCK");
    connect_errno = 0;
}
static void connect_results(void) {
    address("127.0.0.1", 3658); socks[0].type = 1;
    connect_errno = ECONNREFUSED;
    check(invoke("#4", 3, SOCK_BASE, 0x2000u, 16u) == UINT32_MAX, "immediate TCP refusal");
    check(invoke("#111", 0) == 10061, "TCP refusal maps to WSAECONNREFUSED");
    connect_errno = EINPROGRESS; completion_errno = ECONNREFUSED; poll_revents = POLLOUT | POLLERR;
    socks[0].guest_nonblock = 0;
    check(invoke("#4", 3, SOCK_BASE, 0x2000u, 16u) == UINT32_MAX, "blocking TCP refusal");
    check(invoke("#111", 0) == 10061, "blocking refusal maps to WSAECONNREFUSED");
    connect_errno = 0; socks[0].guest_nonblock = 1;
    M32(0x7400) = 0; M32(0x7404) = 0;
    const short completions[] = {POLLOUT | POLLERR, POLLHUP, POLLOUT};
    for (unsigned i = 0; i < sizeof completions / sizeof *completions; ++i) {
        start_connect(ECONNREFUSED);
        unsigned queried = error_queries;
        poll_revents = completions[i]; put_set(0x7000); put_set(0x7100); put_set(0x7200);
        check(invoke("#18", 5, 0u, 0x7000u, 0x7100u, 0x7200u, 0x7400u) == 1, "failed connect select count");
        check(M32(0x7000) == 0 && M32(0x7100) == 0 && M32(0x7200) == 1 && M32(0x7204) == SOCK_BASE,
              "failed connect belongs only to exceptfds");
        check(error_queries == queried + 1, "SO_ERROR queried once across fd sets");
        put_set(0x7200);
        check(invoke("#18", 5, 0u, 0u, 0u, 0x7200u, 0x7400u) == 1, "cached failed connect remains exceptional");
        M32(0x7310) = 4;
        check(invoke("#7", 5, SOCK_BASE, 0xFFFFu, (uint32_t)SO_ERROR, 0x7300u, 0x7310u) == 0 &&
              M32(0x7300) == 10061, "guest SO_ERROR retains WSAECONNREFUSED after select");
        M32(0x7310) = 4;
        check(invoke("#7", 5, SOCK_BASE, 0xFFFFu, (uint32_t)SO_ERROR, 0x7300u, 0x7310u) == 0 &&
              M32(0x7300) == 0, "SO_ERROR clears after guest reads it");
    }
    start_connect(ECONNREFUSED); poll_revents = POLLOUT; put_set(0x7200);
    check(invoke("#18", 5, 0u, 0u, 0u, 0x7200u, 0x7400u) == 1 && M32(0x7200) == 1,
          "except-only select observes failed connect signaled as POLLOUT");
    start_connect(ECONNREFUSED); M32(0x7310) = 4;
    check(invoke("#7", 5, SOCK_BASE, 0xFFFFu, (uint32_t)SO_ERROR, 0x7300u, 0x7310u) == 0 &&
          M32(0x7300) == 10061, "guest reads TCP refusal before select");
    put_set(0x7100); put_set(0x7200); poll_revents = POLLOUT;
    check(invoke("#18", 5, 0u, 0u, 0x7100u, 0x7200u, 0x7400u) == 1 &&
          M32(0x7100) == 0 && M32(0x7200) == 1, "consumed SO_ERROR cannot turn a failed connect writable");
    start_connect(ECONNREFUSED); poll_revents = POLLERR; put_set(0x7100);
    check(invoke("#18", 5, 0u, 0u, 0x7100u, 0u, 0x7400u) == 0 && M32(0x7100) == 0,
          "write-only select excludes failed connect");
    start_connect(0); poll_revents = POLLOUT; put_set(0x7100); put_set(0x7200);
    check(invoke("#18", 5, 0u, 0u, 0x7100u, 0x7200u, 0x7400u) == 1 && M32(0x7100) == 1 && M32(0x7200) == 0,
          "successful connect belongs only to writefds");
    start_connect(0); poll_revents = POLLOUT; put_set(0x7200); M32(0x7404) = 10000;
    unsigned polled = polls, waited = poll_nonzero_timeouts;
    check(invoke("#18", 5, 0u, 0u, 0u, 0x7200u, 0x7400u) == 0 && M32(0x7200) == 0,
          "except-only successful connect times out");
    check(polls == polled + 2 && poll_nonzero_timeouts == waited + 2,
          "except-only success keeps waiting for remaining timeout");
    poll_revents = POLLPRI; put_set(0x7200); M32(0x7404) = 0;
    check(invoke("#18", 5, 0u, 0u, 0u, 0x7200u, 0x7400u) == 1, "ordinary OOB exception remains supported");
    socks[0].connect_pending = 0;               // server.dll's lobby: its listener in both readfds and exceptfds
    poll_revents = POLLIN; put_set(0x7000); put_set(0x7200);
    check(invoke("#18", 5, 0u, 0x7000u, 0u, 0x7200u, 0x7400u) == 1 && M32(0x7000) == 1 && M32(0x7200) == 0,
          "listener in read and except sets is readable");
}
static void datagrams(void) {
    socks[0].type = 2;
    const int lengths[] = {0, 575, 576, 577, 900};
    for (int from = 0; from < 2; ++from) for (unsigned i = 0; i < sizeof lengths / sizeof *lengths; ++i) {
        int length = lengths[i], truncated = length > 576;
        memset(G + 0x3000, 0xA5, 577); recv_len = length; M32(0x4100) = 16;
        uint32_t r = from ? invoke("#17", 6, SOCK_BASE, 0x3000u, 576u, 0u, 0x4000u, 0x4100u) :
            invoke("#16", 4, SOCK_BASE, 0x3000u, 576u, 0u);
        check(r == (truncated ? UINT32_MAX : (uint32_t)length), "UDP recv/recvfrom length or truncation error");
        if (truncated) check(invoke("#111", 0) == 10040, "truncated UDP returns WSAEMSGSIZE");
        int bytes = truncated ? 576 : length, valid = G[0x3000 + bytes] == 0xA5;
        for (int j = 0; j < bytes; ++j) valid &= G[0x3000 + j] == (uint8_t)(j % 251);
        check(valid, "UDP retains truncated data without overflowing buffer");
        if (from) check(M32(0x4100) == 16 && M32(0x4004) == fake_peer.sin_addr.s_addr, "UDP sender retained on truncation");
        // Same matrix through WSARecv/WSARecvFrom, both immediate and parked completion paths.
        for (int pending = 0; pending < 2; ++pending) {
            memset(G + 0x3000, 0xA5, 577);
            M32(0x6000) = 576; M32(0x6004) = 0x3000; M32(0x6200 + 16) = 0x2004;
            M32(0x6100) = UINT32_MAX; M32(0x4100) = 16; recv_len = pending ? -1 : length;
            r = from ? invoke("WSARecvFrom", 9, SOCK_BASE, 0x6000u, 1u, 0x6100u, 0x6110u, 0x4000u, 0x4100u, 0x6200u, 0u) :
                invoke("WSARecv", 7, SOCK_BASE, 0x6000u, 1u, 0x6100u, 0x6110u, 0x6200u, 0u);
            if (pending) {
                check(r == UINT32_MAX && invoke("#111", 0) == 997, "WSA receive parks with IO_PENDING");
                check(ovls[0].event_generation == 123, "receive captures event generation at submission");
                recv_len = length;
                pthread_mutex_lock(&ovl_mu);
                int done = ovl_recv_now(&ovls[0]);
                pthread_mutex_unlock(&ovl_mu);
                check(done, "parked datagram completes");
                r = invoke("WSAGetOverlappedResult", 5, SOCK_BASE, 0x6200u, 0x6100u, 0u, 0x6110u);
                check(r == (truncated ? 0u : 1u), "overlapped result preserves truncation failure");
            } else check(r == (truncated ? UINT32_MAX : 0u), "immediate WSA receive truncation failure");
            if (truncated) check(invoke("#111", 0) == 10040, "WSA receive reports WSAEMSGSIZE");
            check(M32(0x6100) == (uint32_t)bytes && M32(0x6204) == (uint32_t)bytes,
                  "WSA receive exposes truncated byte count");
            valid = G[0x3000 + bytes] == 0xA5;
            for (int j = 0; j < bytes; ++j) valid &= G[0x3000 + j] == (uint8_t)(j % 251);
            check(valid, "WSA receive retains truncated data without overflowing buffer");
        }
    }
    socks[0].type = 1; recv_datagram = 0; recv_len = 900;
    check(invoke("#16", 4, SOCK_BASE, 0x3000u, 576u, 0u) == 576, "TCP partial receive remains successful");
    recv_datagram = 1; recv_len = -1;
}
static void online_race(void) {
    for (int valid = 0; valid < 2; ++valid) {
        fflush(stdout);
        pid_t child = fork();
        if (child < 0) { check(0, "race test fork"); continue; }
        if (!child) {
            ws2_online = 1; setenv("BOOT_ONLINE_EVENT", "1", 1); setenv("BOOT_RACE", "1", 1);
            M32(0x0091CF90) = 0x90000; M32(0x0091E004) = 0xA0000;
            strcpy((char *)G + 0x00926068, "15.1.1"); // race_setup's normalized BOOT_RACE=1
            race_found = valid;
            Cpu c = {0}; c.esp = 0x1000; c.eax = 0x123; c.ecx = 0x456;
            f_005A3590(&c);
            _exit(valid && setup_called == 1 && !strcmp(hashed_race, "15.1.1") &&
                  M32(0x90000 + 0x18C) == 0x12345678 && c.eax == 0x123 && c.ecx == 0x456 ? 0 : 1);
        }
        int status = 0;
        check(waitpid(child, &status, 0) == child && WIFEXITED(status) && WEXITSTATUS(status) == (valid ? 0 : 2),
              valid ? "online race uses normalized BOOT_RACE=1" : "missing online race refuses before custom setters");
    }
}
static int suite(const char *label, const char *open, const char *peer) {
    unsetenv("NFS_TEST_WS2"); unsetenv("BOOT_ONLINE"); unsetenv("NFS_ONLINE_OPEN"); unsetenv("BOOT_ONLINE_PEER");
    if (open) setenv("NFS_ONLINE_OPEN", open, 1);
    if (peer) setenv("BOOT_ONLINE_PEER", peer, 1);
    int is_open = open && !strcmp(open, "1"), has_peer = peer && !strcmp(peer, "18.210.140.94:4567");
    G = calloc(1, 0x1000000);
    FILE *logs = tmpfile();
    if (!G || !logs || dup2(fileno(logs), STDERR_FILENO) < 0) return 2;
    socks[0] = (Sock){.fd = 101, .type = 1, .guest_nonblock = 1};
    // Exhaust the ordinary log cache first: policy logging must remain once per full destination/name.
    for (int i = 0; i < 140; ++i) { char msg[64]; snprintf(msg, sizeof msg, "test diagnostic %d", i); log_once(msg); }
    for (int i = 0; i < 20; ++i) log_once("uncached repeated packet");
    check(occurrences(logs, "ws2: test diagnostic ") == 128 && occurrences(logs, "uncached repeated packet") == 0,
          "full diagnostic cache stops uncached packet output");
    diag_on = 1; log_once("uncached diagnostic enabled"); diag_on = 0;
    check(occurrences(logs, "uncached diagnostic enabled") == 1, "diagnostics can print after cache fills");
    const char *allowed[] = {"127.0.0.1", "127.255.255.255", "10.0.0.0", "10.255.255.255",
        "172.16.0.0", "172.31.255.255", "192.168.0.0", "192.168.255.255", "169.254.0.0", "169.254.255.255",
        "100.64.0.0", "100.127.255.255", "255.255.255.255"};
    const char *blocked[] = {"126.255.255.255", "128.0.0.0", "9.255.255.255", "11.0.0.0", "172.15.255.255",
        "172.32.0.0", "192.167.255.255", "192.169.0.0", "169.253.255.255", "169.255.0.0", "100.63.255.255",
        "100.128.0.0", "0.0.0.0", "8.8.8.8", "255.255.255.254", "224.0.0.1", "18.210.140.95"};
    for (unsigned i = 0; i < sizeof allowed / sizeof *allowed; ++i) destination(allowed[i], 1);
    for (unsigned i = 0; i < sizeof blocked / sizeof *blocked; ++i) destination(blocked[i], is_open);
    destination("18.210.140.94", is_open || has_peer);
    destination("18.210.140.94", is_open || has_peer);
    // An explicit peer IP is allowed at other ports too, but adjacent addresses remain blocked.
    address("18.210.140.94", 9999);
    unsigned before = connects;
    uint32_t r = invoke("#4", 3, SOCK_BASE, 0x2000u, 16u);
    check((r == 0) == (is_open || has_peer) && connects == before + (unsigned)(is_open || has_peer), "exact peer IP, any port");
    if (!is_open && !has_peer) check(occurrences(logs, "ws2: blocked outbound to 18.210.140.94:3658\n") == 1, "address log once across all paths/retries");
    check(invoke("#57", 2, 0x6000u, 256u) == 0, "gethostname");
    char own[256]; strcpy(own, (char *)G + 0x6000);
    hostname(own, 1, 0, ws2_local_ip);
    if (gethostname(own, sizeof own) != 0) return 2;
    hostname(own, 1, 0, ws2_local_ip);
    hostname(NULL, 1, 0, ws2_local_ip); hostname("", 1, 0, ws2_local_ip);
    hostname("LOCALHOST", 1, 0, INADDR_LOOPBACK); hostname("NfSmW-PoRt", 1, 0, ws2_local_ip);
    hostname("18.210.140.94", 1, 0, 0x12D28C5E); hostname("192.168.1.2", 1, 0, 0xC0A80102);
    hostname("example.invalid", is_open, is_open, 0x12010203);
    hostname("example.invalid", is_open, is_open, 0x12010203);
    hostname("127.1", is_open, is_open, 0x12010203);
    char longname[240]; memset(longname, 'a', sizeof longname - 1); longname[sizeof longname - 1] = 0;
    hostname(longname, is_open, is_open, 0x12010203); hostname(longname, is_open, is_open, 0x12010203);
    if (!is_open) {
        check(occurrences(logs, "ws2: blocked hostname example.invalid\n") == 1, "hostname log once");
        check(occurrences(logs, longname) == 1, "long hostname log once without truncation");
    }
    before = sockets;
    check(invoke("#23", 3, 23u, 1u, 0u) == UINT32_MAX && sockets == before, "IPv6 remains unsupported, no host socket");
    connected_peer("127.0.0.1", 1); connected_peer("192.168.1.2", 1);
    connected_peer("18.210.140.94", is_open || has_peer); connected_peer("18.210.140.95", is_open);
    connect_results(); datagrams(); online_race(); lan_translation();
    printf("%s: checks=%u failures=%u host_connect=%u host_sendto=%u resolver=%u\n", label, checks, failures, connects, sends, resolves);
    return failures ? 1 : 0;
}
static void lan_translation(void) {
    unsetenv("NFS_LAN_COPY"); ws2_online = 0;
    uint32_t s = invoke("#23", 3, 2u, 2u, 0u);
    M32(0x6000) = 1;
    unsigned b = broadcasts, reuse = reuseports;
    check(invoke("#21", 5, s, 0xFFFFu, (uint32_t)SO_BROADCAST, 0x6000u, 4u) == 0 && broadcasts == b + 1,
        "SO_BROADCAST reaches native socket");
    ws2_port_shift = 7;
    address("0.0.0.0", 9999);
    check(invoke("#2", 3, s, 0x2000u, 16u) == 0 && ntohs(last_bind.sin_port) == 9999 && reuseports == reuse + 1,
        "discovery shares 9999 despite A1 shift");
    M32(0x6100) = 16;
    check(invoke("#6", 3, s, 0x6000u, 0x6100u) == 0 && ntohs(M16(0x6002)) == 9999,
        "discovery getsockname does not subtract shift");
    invoke("#3", 1, s);
    ws2_port_shift = 0;
    s = invoke("#23", 3, 2u, 1u, 0u);
    address("127.0.0.1", 9900);
    check(invoke("#2", 3, s, 0x2000u, 16u) == 0 && last_bind.sin_addr.s_addr == INADDR_ANY,
        "lobby listens on LAN too");
    invoke("#3", 1, s);
    setenv("NFS_LAN_COPY", "1", 1);
    s = invoke("#23", 3, 2u, 2u, 0u);
    address("0.0.0.0", 9999);
    check(invoke("#2", 3, s, 0x2000u, 16u) == 0 && socks[(s-SOCK_BASE)/4].discovery_tx &&
        ntohs(last_bind.sin_port) == 10000, "second copy has distinct sender");
    struct sockaddr_in src = {.sin_family=AF_INET, .sin_port=htons(10000), .sin_addr.s_addr=htonl(INADDR_LOOPBACK)};
    lan_source(sock_fd(s), &src);
    check(src.sin_addr.s_addr == htonl(0x7F000002) && ntohs(src.sin_port) == 9999,
        "copy source identity preserves self-filter port");
    address("192.168.1.1", 9);
    check(invoke("WSAIoctl", 9, s, 0xC8000014u, 0x2000u, 16u, 0x4000u, 16u, 0u, 0u, 0u) == 0 &&
        M32(0x4004) == htonl(0x7F000002), "second copy route identity differs");
    invoke("#3", 1, s);
    s = invoke("#23", 3, 2u, 1u, 0u);
    address("127.0.0.2", 9900);
    check(invoke("#4", 3, s, 0x2000u, 16u) == 0 && ntohs(last_connect.sin_port) == 9901 &&
        last_connect.sin_addr.s_addr == htonl(INADDR_LOOPBACK), "second lobby endpoint maps to 9901");
    invoke("#3", 1, s);
    unsetenv("NFS_LAN_COPY"); ws2_local_ip = INADDR_LOOPBACK; ws2_port_shift = 0;
}
int main(void) {
    const char *labels[] = {"default", "public-peer", "invalid-peer", "unsafe-open", "nonexact-open"};
    const char *opens[] = {NULL, NULL, NULL, "1", "01"};
    const char *peers[] = {NULL, "18.210.140.94:4567", "18.210.140.94:65536", NULL, "example.invalid"};
    unsigned failed = 0;
    for (unsigned i = 0; i < 5; ++i) {
        fflush(stdout);
        pid_t child = fork();
        if (child < 0) return 2;
        if (!child) exit(suite(labels[i], opens[i], peers[i]));
        int status;
        if (waitpid(child, &status, 0) != child || !WIFEXITED(status) || WEXITSTATUS(status)) ++failed;
    }
    printf("WS2 POLICY %s: %u/5 suites passed\n", failed ? "FAIL" : "PASS", 5 - failed);
    return failed ? 1 : 0;
}
