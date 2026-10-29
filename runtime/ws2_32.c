// ws2_32.c - WS2_32 (winsock 2) and NETAPI32 (Netbios) for the game's own network code (DirtySock's winsock layer,
// speed.exe 0084A8B0..0084BA10, and NetConnMAC 0083CBF0). LAN discovery uses limited broadcast;
// outbound policy still refuses internet destinations by default.
//
// SOCKETs are SOCK_BASE + 4*k over host fds (always O_NONBLOCK on the host; a guest-blocking socket waits in poll with
// the GIL released). Most socket error codes are 10000 + BSD errno; connect errors have explicit Winsock mappings.
// Overlapped WSARecvFrom/WSARecv: tried at once; when nothing is waiting the request is parked and one host thread
// (ovl_thread) polls the parked sockets, receives straight into guest memory and signals the request's event with
// threads_set_event_generation_async. DirtySock's service thread (0084AC10) then collects it with
// WSAGetOverlappedResult. WSA events are ordinary manual-reset kernel events (threads.c), because that thread waits on
// a CreateEventA event and the socket events in one WSAWaitForMultipleEvents call.
//
// Two copies of the game on one Mac (BOOT_ONLINE, boot.c): ws2_port_shift moves this copy's binds of a fixed port by N
// so both can use the game's port 3658; ws2_local_ip is the address the game reports for itself.
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <ifaddrs.h>
#include <netdb.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <pthread.h>
#include <stdlib.h>
#include <strings.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <unistd.h>

#include "host.h"
#include "lan_diag.h"

uint32_t threads_event_new(int manual, int initial);
int threads_event_set(uint32_t h, int on);
uint32_t threads_wait(Cpu *c, uint32_t n, const uint32_t *h, int all, uint32_t ms);
uint64_t threads_event_generation(uint32_t h);
void threads_set_event_generation_async(uint32_t h, uint64_t generation);

extern int ws2_online;
uint32_t ws2_local_ip = 0x7F000001;   // host order, as DirtySock keeps addresses
int ws2_port_shift;                   // added to every bind of a nonzero port (second copy on one Mac)
uint8_t ws2_mac_last = 1;             // last byte of the fake adapter address (differs per copy)
// BOOT_DIAG_LANSERVER (server_loader.c): every listen / accept / listener close is logged and counted, so the LAN
// host's lobby socket is seen bound, accepting and closed on each start/stop cycle (log_once would hide repeats).
int ws2_trace, ws2_trace_listening, ws2_trace_accepts;
uint16_t ws2_trace_port, ws2_trace_peers[64];  // the accepted peers' ports, by accept count

#define SOCK_BASE 0x4000u
#define MAX_SOCK 128
typedef struct {
    int fd;                           // 0: free slot; else host fd + 1
    int type;                         // 1 stream, 2 datagram
    int discovery_tx;                 // auxiliary fd + 1: copy 1 sends from 10000, receives on shared 9999
    int bind_shift;                   // actual applied shift (discovery always stays on 9999)
    int guest_nonblock;
    int connect_pending, connect_error; // pending: 1 connecting, -1 failed; retain SO_ERROR for the guest
    int listening;                    // ws2_trace only
    LanWire wire_tx, wire_rx;          // NFS_LAN + BOOT_DIAG only
} Sock;
static Sock socks[MAX_SOCK];
typedef struct {                      // a parked overlapped receive (DirtySock posts one per socket)
    int active, done, fd, err, from;
    uint32_t sock, buf, len, from_va, fromlen_va, ovl, event, bytes;
    uint64_t event_generation;          // captured while posting, before a close/reuse can happen
} Ovl;
static Ovl ovls[MAX_SOCK];
static pthread_mutex_t ovl_mu = PTHREAD_MUTEX_INITIALIZER;
static int wake_pipe[2] = {-1, -1};
static __thread uint32_t wsa_err;

static int sock_fd(uint32_t s) {
    uint32_t k = (s - SOCK_BASE) / 4;
    return s >= SOCK_BASE && k < MAX_SOCK && socks[k].fd ? socks[k].fd - 1 : -1;
}
static Sock *sock_of(uint32_t s) { return sock_fd(s) >= 0 ? &socks[(s - SOCK_BASE) / 4] : NULL; }
static uint32_t wsa_of_errno(int e) { return e == EINPROGRESS ? 10035 : e == ECONNREFUSED ? 10061 : 10000u + (uint32_t)e; }
static int fail(int e) { wsa_err = wsa_of_errno(e); return -1; }
static void log_once(const char *what) {   // any thread (the overlapped thread too)
    static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;
    static char seen[128][64];
    static int n;
    pthread_mutex_lock(&mu);
    int i = 0;
    while (i < n && strcmp(seen[i], what)) ++i;
    int fresh = i == n && (n < 128 || diag_on);
    if (fresh && n < 128) snprintf(seen[n++], sizeof seen[0], "%s", what);
    pthread_mutex_unlock(&mu);
    if (fresh) fprintf(stderr, "ws2: %s\n", what);
}

// Default to direct player traffic, even without BOOT_ONLINE. Never resolve the peer with DNS.
static pthread_once_t policy_once = PTHREAD_ONCE_INIT;
static int policy_open, policy_has_peer;
static struct in_addr policy_peer;
static void policy_init(void) {
    const char *open = getenv("NFS_ONLINE_OPEN"), *peer = getenv("BOOT_ONLINE_PEER");
    policy_open = open && !strcmp(open, "1");
    if (!peer) return;
    char ip[INET_ADDRSTRLEN];
    const char *colon = strchr(peer, ':');
    size_t n = colon ? (size_t)(colon - peer) : strlen(peer);
    if (!n || n >= sizeof ip) return;
    memcpy(ip, peer, n); ip[n] = 0;
    if (colon) {
        unsigned port = 0;
        const char *p = colon + 1;
        if (!*p) return;
        for (; *p; ++p) {
            if (*p < '0' || *p > '9') return;
            port = port * 10 + (unsigned)(*p - '0');
            if (port > 65535) return;
        }
        if (!port) return;
    }
    policy_has_peer = inet_pton(AF_INET, ip, &policy_peer) == 1;
}
static int outbound_open(void) {
    pthread_once(&policy_once, policy_init);
    return policy_open;
}
// Separate from the bounded diagnostic cache: blocked destinations/names stay deduplicated even after
// 128 ordinary socket messages, and long hostnames cannot collide through truncation. Guest calls hold the GIL.
static void policy_log_once(const char *kind, const char *name) {
    typedef struct Seen { struct Seen *next; char text[]; } Seen;
    static Seen *seen;
    size_t n = strlen(kind) + strlen(name) + 1;
    Seen *item = malloc(sizeof *item + n);
    if (!item) return;
    snprintf(item->text, n, "%s%s", kind, name);
    for (Seen *p = seen; p; p = p->next)
        if (!strcmp(p->text, item->text)) { free(item); return; }
    item->next = seen; seen = item;
    fprintf(stderr, "ws2: %s\n", item->text);
}
static int lan_loopback(void) {
    const char *v = getenv("NFS_LAN_LOOPBACK");
    return v && !strcmp(v, "1");
}
static int outbound_allowed(const struct sockaddr_in *a) {
    uint32_t ip = ntohl(a->sin_addr.s_addr);
    if (lan_loopback() && (ip >> 24) != 127 &&
        !(ip == 0xFFFFFFFFu && ntohs(a->sin_port) == 9999)) { wsa_err = 10051; return 0; }
    if (outbound_open()) return 1;
    // 255.255.255.255 is the limited broadcast (LAN discovery): routers never forward it off the local network.
    if (ip == 0xFFFFFFFFu || (ip >> 24) == 127 || (ip >> 24) == 10 || (ip >> 20) == 0xAC1 ||
        (ip >> 16) == 0xC0A8 || (ip >> 16) == 0xA9FE || (ip >> 22) == 0x191 ||
        (policy_has_peer && a->sin_addr.s_addr == policy_peer.s_addr)) return 1;
    char ipstr[INET_ADDRSTRLEN], dest[INET_ADDRSTRLEN + 6];
    inet_ntop(AF_INET, &a->sin_addr, ipstr, sizeof ipstr);
    snprintf(dest, sizeof dest, "%s:%u", ipstr, ntohs(a->sin_port));
    policy_log_once("blocked outbound to ", dest);
    // DirtySock 0084A840/0084A8B0 maps WSAENETUNREACH to its normal unreachable result (-5).
    wsa_err = 10051;
    return 0;
}

// Optional second menu-driven copy. Kept separate from BOOT_ONLINE's existing A1 rules.
// Both receive on UDP 9999. Copy 1 transmits from 10000; local recipients translate
// that source to 127.0.0.2:9999, so DirtySock's route-derived self comparison works.
// Only locally owned source addresses are translated; physical-LAN peers stay real.
static int lan_copy(void) {
    const char *v = getenv("NFS_LAN_COPY");
    return v && !strcmp(v, "1");
}
static uint32_t lan_address(void) {
    if (lan_loopback()) return INADDR_LOOPBACK;
    struct sockaddr_in to = {.sin_len = sizeof to, .sin_family = AF_INET,
        .sin_port = htons(9), .sin_addr.s_addr = htonl(0xC0A80101)};
    struct sockaddr_in me = {0}; socklen_t len = sizeof me;
    int fd = socket(AF_INET, SOCK_DGRAM, 0);
    int ok = fd >= 0 && !connect(fd, (struct sockaddr *)&to, sizeof to) &&
        !getsockname(fd, (struct sockaddr *)&me, &len);
    if (fd >= 0) close(fd);
    return ok ? ntohl(me.sin_addr.s_addr) : ws2_local_ip;
}
static int own_address(uint32_t ip) {
    if ((ntohl(ip) >> 24) == 127) return 1;
    struct ifaddrs *all = NULL;
    if (getifaddrs(&all)) return 0;
    int own = 0;
    for (struct ifaddrs *p = all; p; p = p->ifa_next)
        if (p->ifa_addr && p->ifa_addr->sa_family == AF_INET &&
            ((struct sockaddr_in *)p->ifa_addr)->sin_addr.s_addr == ip) own = 1;
    freeifaddrs(all);
    return own;
}
static void lan_source(int fd, struct sockaddr_in *a) {
    // Normal menu LAN advertises copy 1 as 127.0.0.2:3658, but binds
    // 127.0.0.1:3659. Keep A1's manually configured peer/port pair untouched.
    if (lan_loopback() && !ws2_online && ntohs(a->sin_port)==3659 && own_address(a->sin_addr.s_addr)) {
        a->sin_addr.s_addr=htonl(0x7F000002u);a->sin_port=htons(3658);return;
    }
    if (ntohs(a->sin_port) == 10000 && own_address(a->sin_addr.s_addr)) {
        struct sockaddr_in bound; socklen_t len = sizeof bound;
        if (getsockname(fd, (struct sockaddr *)&bound, &len) ||
            (ntohs(bound.sin_port) != 9999 && !(lan_loopback() && ntohs(bound.sin_port) == 10001))) return;
        a->sin_addr.s_addr = htonl(0x7F000002);
        a->sin_port = htons(9999);
    }
}
static void lan_target(struct sockaddr_in *a) {
    if (a->sin_addr.s_addr == htonl(0x7F000002) && ntohs(a->sin_port) == 9900) {
        a->sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        a->sin_port = htons(9901);
    }
    if(lan_loopback() && !ws2_online && a->sin_addr.s_addr==htonl(0x7F000002u)) {
        unsigned port=ntohs(a->sin_port);
        if(port==3658) a->sin_port=htons(3659);
        else if(port==9999) a->sin_port=htons(10001);
        else return;
        a->sin_addr.s_addr=htonl(INADDR_LOOPBACK);
    }
}

// Windows sockaddr_in (family u16, port, addr, zero[8]) <-> BSD (sin_len, sin_family u8).
static int sa_from_guest(uint32_t va, uint32_t len, struct sockaddr_in *out) {
    memset(out, 0, sizeof *out);
    if (!va || len < 8) return 0;
    out->sin_len = sizeof *out;
    out->sin_family = (sa_family_t)(G[va] | G[va + 1] << 8);
    memcpy(&out->sin_port, G + va + 2, 2);
    memcpy(&out->sin_addr, G + va + 4, 4);
    return out->sin_family == AF_INET;
}
static void sa_to_guest(const struct sockaddr_in *in, uint32_t va, uint32_t lenva) {
    if (!va) return;
    uint32_t cap = lenva ? M32(lenva) : 16;
    uint8_t w[16] = {AF_INET, 0};
    memcpy(w + 2, &in->sin_port, 2);
    memcpy(w + 4, &in->sin_addr, 4);
    memcpy(G + va, w, cap < 16 ? cap : 16);
    if (lenva) M32(lenva) = 16;
}
// Waits until fd is ready (guest-blocking sockets), with the GIL released. 0 on ready, -1 on timeout.
static int wait_fd(int fd, short ev, int ms) {
    struct pollfd p = {fd, ev, 0};
    gil_release();
    int r = poll(&p, 1, ms);
    gil_acquire();
    return r > 0 ? 0 : -1;
}

// ---------------- overlapped receives ----------------
// recvmsg reports MSG_TRUNC without discarding the bytes that fit (recv/recvfrom hide this on BSD).
static ssize_t recv_packet(int fd, void *buf, size_t len, int flags, struct sockaddr_in *from, int *truncated) {
    struct iovec iov = {buf, len};
    struct msghdr msg = {0};
    msg.msg_iov = &iov; msg.msg_iovlen = 1;
    msg.msg_name = from; msg.msg_namelen = from ? sizeof *from : 0;
    ssize_t n = recvmsg(fd, &msg, flags);
    *truncated = n >= 0 && (msg.msg_flags & MSG_TRUNC);
    if (n>0 && !(flags & MSG_PEEK) && lan_diag_on())
        for(int k=0;k<MAX_SOCK;++k) if(socks[k].fd==fd+1 && socks[k].type==1) {
            lan_wire_feed(&socks[k].wire_rx,"rx",buf,(size_t)n);break;
        }
    if (n >= 0 && from) lan_source(fd, from);
    return n;
}
static int ovl_recv_now(Ovl *o) {     // with ovl_mu held; 1 done, 0 nothing waiting
    struct sockaddr_in from;
    int truncated;
    ssize_t n = recv_packet(o->fd, G + o->buf, o->len, 0, o->from ? &from : NULL, &truncated);
    if (n < 0 && (errno == EWOULDBLOCK || errno == EINTR)) return 0;
    o->err = n < 0 ? (int)wsa_of_errno(errno) : truncated ? 10040 : 0;
    o->bytes = n < 0 ? 0 : (uint32_t)n;
    if (n >= 0 && o->from) {
        sa_to_guest(&from, o->from_va, o->fromlen_va);
        char m[96];
        snprintf(m, sizeof m, "recvfrom %s:%u (%zd bytes)", inet_ntoa(from.sin_addr), ntohs(from.sin_port), n);
        log_once(m);
    }
    if (o->ovl) { M32(o->ovl) = o->err ? 0xC0000000u : 0; M32(o->ovl + 4) = o->bytes; }
    o->done = 1;
    return 1;
}
static void *ovl_thread(void *p) {
    (void)p;
    for (;;) {
        struct pollfd pf[MAX_SOCK + 1];
        int idx[MAX_SOCK + 1], n = 0;
        pf[n].fd = wake_pipe[0]; pf[n].events = POLLIN; idx[n++] = -1;
        pthread_mutex_lock(&ovl_mu);
        for (int k = 0; k < MAX_SOCK; ++k)
            if (ovls[k].active && !ovls[k].done) { pf[n].fd = ovls[k].fd; pf[n].events = POLLIN; idx[n++] = k; }
        pthread_mutex_unlock(&ovl_mu);
        if (poll(pf, (nfds_t)n, -1) <= 0) continue;
        if (pf[0].revents) { char b[64]; while (read(wake_pipe[0], b, sizeof b) > 0) {} }
        for (int i = 1; i < n; ++i) {
            if (!pf[i].revents) continue;
            pthread_mutex_lock(&ovl_mu);
            Ovl *o = &ovls[idx[i]];
            uint32_t ev = 0;
            uint64_t generation = 0;
            if (o->active && !o->done && o->fd == pf[i].fd && ovl_recv_now(o)) {
                ev = o->event; generation = o->event_generation;
            }
            pthread_mutex_unlock(&ovl_mu);
            if (ev && generation) threads_set_event_generation_async(ev, generation);
        }
    }
    return NULL;
}
static void ovl_wake(void) {
    static pthread_once_t once = PTHREAD_ONCE_INIT;
    void ovl_start(void);
    pthread_once(&once, ovl_start);
    char b = 1;
    (void)!write(wake_pipe[1], &b, 1);
}
void ovl_start(void) {
    if (pipe(wake_pipe)) { perror("ws2: pipe"); exit(1); }
    fcntl(wake_pipe[0], F_SETFL, O_NONBLOCK);
    fcntl(wake_pipe[1], F_SETFL, O_NONBLOCK);
    pthread_t t;
    pthread_create(&t, NULL, ovl_thread, NULL);
    pthread_detach(t);
}
// WSARecvFrom/WSARecv(s, WSABUF*, count, bytes*, flags*, [from, fromlen*,] ovl, completion)
static uint32_t wsa_recv(uint32_t s, uint32_t bufs, uint32_t bytes_va, uint32_t from_va, uint32_t fromlen_va, uint32_t ovl, int from) {
    int fd = sock_fd(s);
    if (fd < 0) { wsa_err = 10038; return 0xFFFFFFFF; }
    uint32_t k = (s - SOCK_BASE) / 4;
    pthread_mutex_lock(&ovl_mu);
    Ovl *o = &ovls[k];
    *o = (Ovl){.active = 1, .fd = fd, .from = from, .sock = s, .buf = M32(bufs + 4), .len = M32(bufs),
        .from_va = from_va, .fromlen_va = fromlen_va, .ovl = ovl, .event = ovl ? M32(ovl + 16) : 0};
    o->event_generation = threads_event_generation(o->event);
    int now = ovl_recv_now(o);
    uint32_t err = o->err, n = o->bytes;
    if (now) o->active = 0;                          // completed at once: nothing left for WSAGetOverlappedResult
    pthread_mutex_unlock(&ovl_mu);
    if (now) {
        if (bytes_va) M32(bytes_va) = n;
        if (err) { wsa_err = err; return 0xFFFFFFFF; }
        return 0;
    }
    if (!ovl) { o->active = 0; wsa_err = 10035; return 0xFFFFFFFF; }
    ovl_wake();
    wsa_err = 997;                                    // WSA_IO_PENDING
    return 0xFFFFFFFF;
}

// ---------------- the rest ----------------
static uint32_t hostent_for(uint32_t ip_net, const char *name) {
    static uint32_t he;
    if (!he) he = galloc(0x80);
    snprintf((char *)G + he + 0x40, 0x40, "%s", name);
    M32(he + 0x00) = he + 0x40;                       // h_name
    M32(he + 0x04) = he + 0x20;                       // h_aliases -> {NULL}
    G[he + 0x08] = AF_INET; G[he + 0x09] = 0;         // h_addrtype
    G[he + 0x0A] = 4; G[he + 0x0B] = 0;               // h_length
    M32(he + 0x0C) = he + 0x10;                       // h_addr_list -> {addr, NULL}
    M32(he + 0x10) = he + 0x18; M32(he + 0x14) = 0;
    memcpy(G + he + 0x18, &ip_net, 4);
    M32(he + 0x20) = 0;
    return he;
}
static const char *const our_hostname = "nfsmw-port";

int win32_ws2(Cpu *c, const char *fn) {
    static int off = -1;                              // NFS_TEST_WS2=off (test only, online_check.sh's broken run):
    if (off < 0) off = getenv("NFS_TEST_WS2") && !strcmp(getenv("NFS_TEST_WS2"), "off");   // no shim, as before it
    if (off) return 0;
    if (!strcmp(fn, "Netbios")) {                     // NetConnMAC: NCBRESET (0x32), then NCBASTAT (0x33) for the MAC
        uint32_t ncb = arg(c, 0);
        uint8_t cmd = G[ncb], rc = 0;
        if (cmd == 0x33) {
            uint32_t buf = M32(ncb + 4), len = M32(ncb + 8) & 0xFFFF;
            static const uint8_t mac[5] = {0x02, 0x4E, 0x46, 0x53, 0x4D};   // locally administered "NFSM"
            if (len >= 6) { memset(G + buf, 0, len); memcpy(G + buf, mac, 5); G[buf + 5] = ws2_mac_last; }
        } else if (cmd != 0x32) rc = 3;               // NRC_ILLCMD
        G[ncb + 1] = rc;
        RET(rc, 1);
    }
    if (fn[0] == '#') {
        switch (atoi(fn + 1)) {
        case 8: case 14: RET(__builtin_bswap32(arg(c, 0)), 1); // htonl / ntohl
        case 9: case 15: RET(__builtin_bswap16((uint16_t)arg(c, 0)), 1); // htons / ntohs
        case 151: {                                  // __WSAFDIsSet: guest count + socket handles
            uint32_t set = arg(c, 1), count = set ? M32(set) : 0;
            for (uint32_t i = 0; i < count && i < 64; ++i)
                if (M32(set + 4 + 4*i) == arg(c, 0)) RET(1, 2);
            RET(0, 2);
        }
        case 115: {                                   // WSAStartup(version, WSADATA*)
            uint32_t d = arg(c, 1);
            memset(G + d, 0, 0x190);
            G[d] = 2; G[d + 1] = 2; G[d + 2] = 2; G[d + 3] = 2;   // wVersion / wHighVersion 2.2
            strcpy((char *)G + d + 4, "NFSMW port winsock");
            strcpy((char *)G + d + 4 + 257, "Running");
            G[d + 0x186] = 0; G[d + 0x187] = 1;      // iMaxSockets 256 (low byte first: 0x0100)
            G[d + 0x188] = 0xFF; G[d + 0x189] = 0xFF;
            log_once("WSAStartup");
            RET(0, 2);
        }
        case 116: RET(0, 0);                          // WSACleanup
        case 111: RET(wsa_err, 0);                    // WSAGetLastError
        case 23: {                                    // socket(af, type, proto)
            (void)lan_diag_on(); // initialize the immutable diagnostic switch before async receives
            int type = (int)arg(c, 1);
            if (arg(c, 0) != AF_INET || (type != 1 && type != 2)) { wsa_err = 10047; RET(0xFFFFFFFF, 3); }
            int fd = socket(AF_INET, type == 1 ? SOCK_STREAM : SOCK_DGRAM, 0);
            if (fd < 0) { fail(errno); RET(0xFFFFFFFF, 3); }
            int k = 0;
            while (k < MAX_SOCK && socks[k].fd) ++k;
            if (k == MAX_SOCK) { close(fd); wsa_err = 10024; RET(0xFFFFFFFF, 3); }
            fcntl(fd, F_SETFL, fcntl(fd, F_GETFL) | O_NONBLOCK);
            int one = 1;
            setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &one, sizeof one);
            socks[k] = (Sock){.fd = fd + 1, .type = type};
            RET(SOCK_BASE + 4 * (uint32_t)k, 3);
        }
        case 3: {                                     // closesocket(s)
            Sock *s = sock_of(arg(c, 0));
            if (!s) { wsa_err = 10038; RET(0xFFFFFFFF, 1); }
            pthread_mutex_lock(&ovl_mu);
            ovls[s - socks].active = 0;
            if (s->listening) { --ws2_trace_listening; fprintf(stderr, "ws2: listener %u closed\n", arg(c, 0)); }
            if (s->discovery_tx) close(s->discovery_tx - 1);
            close(s->fd - 1);
            s->fd = 0;
            pthread_mutex_unlock(&ovl_mu);
            if (wake_pipe[1] >= 0) ovl_wake();
            RET(0, 1);
        }
        case 22: {                                    // shutdown(s, how): SD_* = SHUT_* (0, 1, 2)
            int fd = sock_fd(arg(c, 0));
            if (fd < 0) { wsa_err = 10038; RET(0xFFFFFFFF, 2); }
            RET(shutdown(fd, (int)arg(c, 1)) ? (uint32_t)fail(errno) : 0, 2);
        }
        case 2: case 4: {                             // bind / connect(s, name, len)
            int fd = sock_fd(arg(c, 0));
            struct sockaddr_in a;
            if (fd < 0) { wsa_err = 10038; RET(0xFFFFFFFF, 3); }
            if (!sa_from_guest(arg(c, 1), arg(c, 2), &a)) { wsa_err = 10047; RET(0xFFFFFFFF, 3); }
            int r;
            if (fn[1] == '2') {
                Sock *s = sock_of(arg(c, 0));
                unsigned port = ntohs(a.sin_port);
                int discovery = s->type == 2 && port == 9999;
                int shift = lan_copy() ? 1 : ws2_port_shift;
                s->bind_shift = discovery ? 0 : (port ? shift : 0);
                if (discovery && lan_loopback() && lan_copy()) s->bind_shift = 2;
                if (s->bind_shift) a.sin_port = htons((uint16_t)(port + s->bind_shift));
                // The lobby must accept LAN clients even if DLL hostname fallback was loopback.
                if (s->type == 1 && port == 9900 &&
                    (ntohl(a.sin_addr.s_addr) >> 24) == 127) a.sin_addr.s_addr = INADDR_ANY;
                if (lan_copy() && a.sin_addr.s_addr == htonl(0x7F000002)) a.sin_addr.s_addr = INADDR_ANY;
                if (lan_loopback()) a.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
                int one = 1;
                if (setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one)) RET((uint32_t)fail(errno), 3);
                if (discovery && setsockopt(fd, SOL_SOCKET, SO_REUSEPORT, &one, sizeof one)) RET((uint32_t)fail(errno), 3);
                r = bind(fd, (struct sockaddr *)&a, sizeof a);
                if (!r && (discovery || port == 9900)) {
                    if (!ws2_online) ws2_local_ip = lan_copy() ? 0x7F000002 : lan_address();
                    if (discovery && lan_copy()) {
                        int tx = socket(AF_INET, SOCK_DGRAM, 0), enabled = 0;
                        socklen_t el = sizeof enabled;
                        struct sockaddr_in sender = a; sender.sin_port = htons(10000);
                        sender.sin_addr.s_addr = lan_loopback() ? htonl(INADDR_LOOPBACK) : INADDR_ANY;
                        int e = 0;
                        if (tx < 0) e = errno;
                        else if (setsockopt(tx, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one) ||
                                 setsockopt(tx, SOL_SOCKET, SO_REUSEPORT, &one, sizeof one) ||
                                 getsockopt(fd, SOL_SOCKET, SO_BROADCAST, &enabled, &el) ||
                                 setsockopt(tx, SOL_SOCKET, SO_BROADCAST, &enabled, sizeof enabled) ||
                                 fcntl(tx, F_SETFL, O_NONBLOCK) ||
                                 bind(tx, (struct sockaddr *)&sender, sizeof sender)) e = errno;
                        if (e) { if (tx >= 0) close(tx); RET((uint32_t)fail(e), 3); }
                        s->discovery_tx = tx + 1;
                    }
                }
                char m[96];
                snprintf(m, sizeof m, "bind %s:%u -> %d", inet_ntoa(a.sin_addr), ntohs(a.sin_port), r ? errno : 0);
                log_once(m);
            } else {
                if (lan_loopback() && (ntohl(a.sin_addr.s_addr) >> 24) != 127) { wsa_err = 10051; RET(0xFFFFFFFF, 3); }
                if (!outbound_allowed(&a)) RET(0xFFFFFFFF, 3);
                lan_target(&a);
                r = connect(fd, (struct sockaddr *)&a, sizeof a);
                int e = r ? errno : 0;
                char m[96];
                snprintf(m, sizeof m, "connect %s %s:%u -> %d", sock_of(arg(c, 0))->type == 1 ? "tcp" : "udp", inet_ntoa(a.sin_addr), ntohs(a.sin_port), e);
                log_once(m);
                Sock *s = sock_of(arg(c, 0));
                s->connect_pending = e == EINPROGRESS;
                s->connect_error = 0;
                if (s->connect_pending && !s->guest_nonblock) {
                    wait_fd(fd, POLLOUT, -1);
                    socklen_t el = sizeof e;
                    if (getsockopt(fd, SOL_SOCKET, SO_ERROR, &e, &el)) e = errno;
                    s->connect_pending = 0;
                    r = e ? -1 : 0;
                }
                if (r) errno = e;
            }
            RET(r ? (uint32_t)fail(errno) : 0, 3);
        }
        case 13: {                                    // listen(s, backlog)
            int fd = sock_fd(arg(c, 0));
            if (fd < 0) { wsa_err = 10038; RET(0xFFFFFFFF, 2); }
            if (lan_loopback()) {
                struct sockaddr_in bound; socklen_t len = sizeof bound;
                if (getsockname(fd, (struct sockaddr *)&bound, &len) ||
                    (ntohl(bound.sin_addr.s_addr) >> 24) != 127 || !bound.sin_port) {
                    wsa_err = 10049; RET(0xFFFFFFFF, 2);
                }
            }
            if (listen(fd, (int)arg(c, 1))) RET((uint32_t)fail(errno), 2);
            if (ws2_trace) {
                struct sockaddr_in a;
                socklen_t al = sizeof a;
                getsockname(fd, (struct sockaddr *)&a, &al);
                sock_of(arg(c, 0))->listening = 1;
                ++ws2_trace_listening;
                ws2_trace_port = ntohs(a.sin_port);
                fprintf(stderr, "ws2: listen %u on %s:%u\n", arg(c, 0), inet_ntoa(a.sin_addr), ws2_trace_port);
            }
            RET(0, 2);
        }
        case 1: {                                     // accept(s, addr, len*)
            Sock *s = sock_of(arg(c, 0));
            if (!s) { wsa_err = 10038; RET(0xFFFFFFFF, 3); }
            struct sockaddr_in a;
            socklen_t al = sizeof a;
            int fd;
            while ((fd = accept(s->fd - 1, (struct sockaddr *)&a, &al)) < 0 && errno == EWOULDBLOCK && !s->guest_nonblock)
                wait_fd(s->fd - 1, POLLIN, -1);
            if (fd < 0) { fail(errno); RET(0xFFFFFFFF, 3); }
            if (!outbound_allowed(&a)) { close(fd); wsa_err = 10061; RET(0xFFFFFFFF, 3); }
            int k = 0;
            while (k < MAX_SOCK && socks[k].fd) ++k;
            if (k == MAX_SOCK) { close(fd); wsa_err = 10024; RET(0xFFFFFFFF, 3); }
            fcntl(fd, F_SETFL, fcntl(fd, F_GETFL) | O_NONBLOCK);
            socks[k] = (Sock){.fd = fd + 1, .type = 1};
            sa_to_guest(&a, arg(c, 1), arg(c, 2));
            if (ws2_trace && s->listening) {
                ws2_trace_peers[ws2_trace_accepts++ & 63] = ntohs(a.sin_port);
                fprintf(stderr, "ws2: accept on %u from %s:%u\n", arg(c, 0), inet_ntoa(a.sin_addr), ntohs(a.sin_port));
            }
            RET(SOCK_BASE + 4 * (uint32_t)k, 3);
        }
        case 19: case 20: {                           // send(s, buf, len, flags) / sendto(..., to, tolen)
            int to = fn[2] == '0';
            Sock *s = sock_of(arg(c, 0));
            int na = to ? 6 : 4;
            if (!s) { wsa_err = 10038; RET(0xFFFFFFFF, na); }
            struct sockaddr_in a;
            if (to && arg(c, 4) && !sa_from_guest(arg(c, 4), arg(c, 5), &a)) { wsa_err = 10047; RET(0xFFFFFFFF, na); }
            if (to && arg(c, 4) && !outbound_allowed(&a)) RET(0xFFFFFFFF, na);
            if (to && arg(c, 4)) lan_target(&a);
            if (lan_loopback() && s->type != 2 && to && arg(c, 4) &&
                (ntohl(a.sin_addr.s_addr) >> 24) != 127) { wsa_err = 10051; RET(0xFFFFFFFF, na); }
            int flags = (int)(arg(c, 3) & 3);
            ssize_t n;
            for (;;) {
                // A stream sendto can ignore its explicit destination. Re-check after every blocking wait too.
                if (!to || !arg(c, 4) || s->type == 1) {
                    struct sockaddr_in peer;
                    socklen_t al = sizeof peer;
                    if (getpeername(s->fd - 1, (struct sockaddr *)&peer, &al)) { fail(errno); RET(0xFFFFFFFF, na); }
                    if (!outbound_allowed(&peer)) RET(0xFFFFFFFF, na);
                }
                if (lan_loopback() && s->type == 2 && to && arg(c, 4) &&
                    a.sin_addr.s_addr == htonl(0xFFFFFFFFu) && ntohs(a.sin_port) == 9999) {
                    // UDP unicast does not fan out to SO_REUSEPORT listeners. Each copy
                    // therefore has its own receive port, while the guest still sees 9999.
                    int tx = s->discovery_tx ? s->discovery_tx - 1 : s->fd - 1, enabled = 0;
                    socklen_t el = sizeof enabled;
                    if (getsockopt(tx, SOL_SOCKET, SO_BROADCAST, &enabled, &el) || !enabled) {
                        wsa_err = 10013; RET(0xFFFFFFFF, na);
                    }
                    struct sockaddr_in local = a;
                    local.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
                    local.sin_port = htons(9999);
                    n = sendto(tx, G + arg(c, 1), arg(c, 2), flags, (struct sockaddr *)&local, sizeof local);
                    if (n >= 0) {
                        local.sin_port = htons(10001);
                        n = sendto(tx, G + arg(c, 1), arg(c, 2), flags, (struct sockaddr *)&local, sizeof local);
                    }
                    log_once("loopback discovery fanout 127.0.0.1:9999,10001");
                } else {
                    n = to && arg(c, 4) ? sendto(s->discovery_tx ? s->discovery_tx - 1 : s->fd - 1, G + arg(c, 1), arg(c, 2), flags, (struct sockaddr *)&a, sizeof a)
                                        : send(s->fd - 1, G + arg(c, 1), arg(c, 2), flags);
                }
                if (n >= 0 || errno != EWOULDBLOCK || s->guest_nonblock) break;
                wait_fd(s->fd - 1, POLLOUT, -1);
            }
            if(n>0 && s->type==1)lan_wire_feed(&s->wire_tx,"tx",G+arg(c,1),(size_t)n);
            if (to && arg(c, 4)) {
                char m[96];
                const char *target = lan_loopback() && a.sin_addr.s_addr == htonl(0xFFFFFFFFu) &&
                    ntohs(a.sin_port) == 9999 ? "127.0.0.1 (fanout)" : inet_ntoa(a.sin_addr);
                snprintf(m, sizeof m, "sendto %s:%u -> %d", target, ntohs(a.sin_port), n < 0 ? errno : 0);
                log_once(m);
            }
            RET(n < 0 ? (uint32_t)fail(errno) : (uint32_t)n, na);
        }
        case 16: case 17: {                           // recv(s, buf, len, flags) / recvfrom(..., from, fromlen*)
            int from = fn[2] == '7';
            int na = from ? 6 : 4;
            Sock *s = sock_of(arg(c, 0));
            if (!s) { wsa_err = 10038; RET(0xFFFFFFFF, na); }
            struct sockaddr_in a;
            int flags = (int)(arg(c, 3) & 3);
            int truncated;
            ssize_t n;
            for (;;) {
                n = recv_packet(s->fd - 1, G + arg(c, 1), arg(c, 2), flags, from ? &a : NULL, &truncated);
                if (n >= 0 || errno != EWOULDBLOCK || s->guest_nonblock) break;
                wait_fd(s->fd - 1, POLLIN, -1);
            }
            if (n < 0) { fail(errno); RET(0xFFFFFFFF, na); }
            if (from) sa_to_guest(&a, arg(c, 4), arg(c, 5));
            if (truncated) { wsa_err = 10040; RET(0xFFFFFFFF, na); }
            RET((uint32_t)n, na);
        }
        case 18: {                                    // select(nfds, read, write, except, timeval*)
            struct pollfd pf[192];
            Sock *ps[192];
            int kind[192], hit[192], ignore[192] = {0};
            uint32_t set[3] = {arg(c, 1), arg(c, 2), arg(c, 3)}, n = 0;
            static const short evs[3] = {POLLIN, POLLOUT, POLLPRI};
            for (int k = 0; k < 3; ++k)
                if (set[k])
                    for (uint32_t i = 0; i < M32(set[k]) && i < 64; ++i) {
                        Sock *s = sock_of(M32(set[k] + 4 + 4 * i));
                        if (!s) { wsa_err = 10038; RET(0xFFFFFFFF, 5); }
                        ps[n] = s; kind[n++] = k;
                    }
            uint32_t cnt_in[3] = {set[0] ? M32(set[0]) : 0, set[1] ? M32(set[1]) : 0, set[2] ? M32(set[2]) : 0};
            uint32_t tv = arg(c, 4);
            int ms = tv ? (int)(M32(tv) * 1000 + M32(tv + 4) / 1000) : -1;
            uint64_t deadline = ms > 0 ? host_ticks() + (uint64_t)ms * 1000000 : 0;
            uint32_t total;
            for (;;) {
                int ready = 0;
                for (uint32_t i = 0; i < n; ++i) {
                    Sock *s = ps[i];
                    pf[i].fd = s->connect_pending == -1 || ignore[i] ? -1 : s->fd - 1;
                    pf[i].events = evs[kind[i]] | (kind[i] == 2 && s->connect_pending == 1 ? POLLOUT : 0);
                    pf[i].revents = 0;
                    ready |= kind[i] == 2 && s->connect_pending == -1;
                }
                // One entry per fd: macOS poll (kqueue underneath) drops an fd's POLLIN when the same fd is listed
                // again for POLLPRI, so a listener in both the read and except sets (server.dll's lobby) never
                // looked readable and its connections were never accepted.
                struct pollfd pu[192];
                int idx[192];
                nfds_t nu = 0;
                for (uint32_t i = 0; i < n; ++i) {
                    nfds_t j = 0;
                    while (j < nu && (pf[i].fd < 0 || pu[j].fd != pf[i].fd)) ++j;
                    if (j == nu) pu[nu++] = (struct pollfd){.fd = pf[i].fd};
                    pu[j].events |= pf[i].events;
                    idx[i] = (int)j;
                }
                int r, timeout = ready ? 0 : ms;
                if (!timeout) r = poll(pu, nu, 0);
                else { gil_release(); r = poll(pu, nu, timeout); gil_acquire(); }
                if (r < 0) { fail(errno); RET(0xFFFFFFFF, 5); }
                for (uint32_t i = 0; i < n; ++i) pf[i].revents = pu[idx[i]].revents;
                // Poll completion can consume SO_ERROR only once, even when the socket is in several sets.
                for (uint32_t i = 0; i < n; ++i) {
                    Sock *s = ps[i];
                    if (s->connect_pending == 1 && (pf[i].revents & (POLLOUT | POLLERR | POLLHUP))) {
                        int e = 0; socklen_t el = sizeof e;
                        if (getsockopt(s->fd - 1, SOL_SOCKET, SO_ERROR, &e, &el)) e = errno;
                        s->connect_error = e;
                        s->connect_pending = e ? -1 : 0;
                    }
                }
                total = 0;
                for (uint32_t i = 0; i < n; ++i) {
                    short re = pf[i].revents;
                    int k = kind[i];
                    hit[i] = ps[i]->connect_pending == -1 ? k == 2 :
                        k == 0 ? (re & (POLLIN | POLLHUP | POLLERR)) != 0 :
                        k == 1 ? (re & (POLLOUT | POLLERR)) != 0 : (re & POLLPRI) != 0;
                    total += hit[i];
                    if (k == 2 && (re & (POLLERR | POLLHUP))) ignore[i] = 1;
                }
                if (total || !r || !ms) break;
                // A successful connect wakes POLLOUT even in an except-only wait: keep waiting for an exception.
                if (deadline) {
                    uint64_t now = host_ticks();
                    if (now >= deadline) break;
                    ms = (int)((deadline - now + 999999) / 1000000);
                }
            }
            uint32_t j = 0;
            for (int k = 0; k < 3; ++k)
                if (set[k]) {
                    uint32_t cnt = M32(set[k]), out = 0;
                    for (uint32_t i = 0; i < cnt && i < 64; ++i, ++j) {
                        if (hit[j]) M32(set[k] + 4 + 4 * out++) = M32(set[k] + 4 + 4 * i);
                    }
                    M32(set[k]) = out;
                }
            static int traced;
            if (ws2_trace && traced < 40) {             // the first selects: their sets and readiness
                ++traced;
                fprintf(stderr, "ws2: select r%u w%u e%u timeout %d ms ->", set[0] ? cnt_in[0] : 0, set[1] ? cnt_in[1] : 0,
                        set[2] ? cnt_in[2] : 0, tv ? (int)(M32(tv) * 1000 + M32(tv + 4) / 1000) : -1);
                for (uint32_t i = 0; i < n; ++i) fprintf(stderr, " %c%u%s", "rwe"[kind[i]], (unsigned)((ps[i] - socks) * 4 + SOCK_BASE), hit[i] ? "*" : "");
                fprintf(stderr, " = %u\n", total);
            }
            RET(total, 5);
        }
        case 21: case 7: {                            // setsockopt(s, level, name, val, len) / getsockopt(.., len*)
            int fd = sock_fd(arg(c, 0));
            if (fd < 0) { wsa_err = 10038; RET(0xFFFFFFFF, 5); }
            int level = arg(c, 1) == 0xFFFF ? SOL_SOCKET : (int)arg(c, 1), name = (int)arg(c, 2), r;
            if (fn[2] == '1') {                       // SOL_SOCKET/IPPROTO_TCP/IPPROTO_IP option numbers match BSD
                r = setsockopt(fd, level, name, G + arg(c, 3), arg(c, 4));
                Sock *s = sock_of(arg(c, 0));
                if (!r && s->discovery_tx && level == SOL_SOCKET && name == SO_BROADCAST)
                    r = setsockopt(s->discovery_tx - 1, level, name, G + arg(c, 3), arg(c, 4));
            } else {
                socklen_t l = M32(arg(c, 4));
                Sock *s = sock_of(arg(c, 0));
                if (level == SOL_SOCKET && name == SO_ERROR && s->connect_pending == -1 && l >= 4) {
                    M32(arg(c, 3)) = (uint32_t)s->connect_error;
                    s->connect_error = 0;
                    l = 4; r = 0;
                } else {
                    r = getsockopt(fd, level, name, G + arg(c, 3), &l);
                    // The guest can read the failure before selecting. Keep the failed-connect state after
                    // SO_ERROR has been consumed, without returning that error a second time.
                    if (!r && level == SOL_SOCKET && name == SO_ERROR && l >= 4 &&
                        s->connect_pending == 1 && M32(arg(c, 3))) s->connect_pending = -1;
                }
                M32(arg(c, 4)) = l;
                if (!r && level == SOL_SOCKET && name == SO_ERROR && M32(arg(c, 3))) M32(arg(c, 3)) = wsa_of_errno((int)M32(arg(c, 3)));
            }
            if (r) { char m[64]; snprintf(m, sizeof m, "%s level %d opt %d: errno %d", fn[2] == '1' ? "setsockopt" : "getsockopt", level, name, errno); log_once(m); }
            RET(r ? (uint32_t)fail(errno) : 0, 5);
        }
        case 10: {                                    // ioctlsocket(s, cmd, arg*)
            Sock *s = sock_of(arg(c, 0));
            if (!s) { wsa_err = 10038; RET(0xFFFFFFFF, 3); }
            if (arg(c, 1) == 0x8004667E) { s->guest_nonblock = M32(arg(c, 2)) != 0; RET(0, 3); }   // FIONBIO
            if (arg(c, 1) == 0x4004667F) {                                                         // FIONREAD
                int n = 0;
                ioctl(s->fd - 1, FIONREAD, &n);
                M32(arg(c, 2)) = (uint32_t)n;
                RET(0, 3);
            }
            log_once("ioctlsocket: unknown command");
            wsa_err = 10045; RET(0xFFFFFFFF, 3);
        }
        case 5: case 6: {                             // getpeername / getsockname(s, name, len*)
            int fd = sock_fd(arg(c, 0));
            if (fd < 0) { wsa_err = 10038; RET(0xFFFFFFFF, 3); }
            struct sockaddr_in a;
            socklen_t al = sizeof a;
            int r = fn[1] == '5' ? getpeername(fd, (struct sockaddr *)&a, &al) : getsockname(fd, (struct sockaddr *)&a, &al);
            if (r) { fail(errno); RET(0xFFFFFFFF, 3); }
            if (fn[1] == '6' && sock_of(arg(c, 0))->bind_shift && a.sin_port)
                a.sin_port = htons((uint16_t)(ntohs(a.sin_port) - sock_of(arg(c, 0))->bind_shift));
            sa_to_guest(&a, arg(c, 1), arg(c, 2));
            RET(0, 3);
        }
        case 57: {                                    // gethostname(buf, len)
            if (arg(c, 1) <= strlen(our_hostname)) { wsa_err = 10014; RET(0xFFFFFFFF, 2); }
            strcpy((char *)G + arg(c, 0), our_hostname);
            RET(0, 2);
        }
        case 52: {                                    // gethostbyname(name): our own name without a DNS/mDNS lookup
            const char *name = gptr(arg(c, 0));
            uint32_t ip;
            char hostname[256] = {0};
            int own = !gethostname(hostname, sizeof hostname - 1) && name && !strcasecmp(name, hostname);
            struct in_addr numeric;
            if (!name || !*name || !strcasecmp(name, our_hostname) || own) {
                if (!ws2_online) ws2_local_ip = lan_copy() ? 0x7F000002 : lan_address();
                ip = htonl(ws2_local_ip);
            }
            else if (!strcasecmp(name, "localhost")) ip = htonl(INADDR_LOOPBACK);
            else if (inet_pton(AF_INET, name, &numeric) == 1) ip = numeric.s_addr;
            else if (!outbound_open()) {
                policy_log_once("blocked hostname ", name);
                wsa_err = 11001; RET(0, 1);            // WSAHOST_NOT_FOUND: no DNS/mDNS call
            }
            else {
                struct addrinfo hints = {0}, *res = NULL;
                hints.ai_family = AF_INET;
                gil_release();
                int r = getaddrinfo(name, NULL, &hints, &res);
                gil_acquire();
                if (r || !res) { wsa_err = 11001; RET(0, 1); }   // WSAHOST_NOT_FOUND
                ip = ((struct sockaddr_in *)res->ai_addr)->sin_addr.s_addr;
                freeaddrinfo(res);
            }
            RET(hostent_for(ip, name && *name ? name : our_hostname), 1);
        }
        }
        char m[64]; snprintf(m, sizeof m, "unimplemented WS2_32 ordinal %s", fn); log_once(m);
        return 0;
    }
    if (!strcmp(fn, "WSACreateEvent")) RET(threads_event_new(1, 0), 0);
    if (!strcmp(fn, "WSACloseEvent")) RET(threads_close(arg(c, 0)), 1);
    if (!strcmp(fn, "WSASetEvent")) RET(threads_event_set(arg(c, 0), 1), 1);
    if (!strcmp(fn, "WSAResetEvent")) RET(threads_event_set(arg(c, 0), 0), 1);
    if (!strcmp(fn, "WSAWaitForMultipleEvents")) {    // (n, events*, all, ms, alertable)
        uint32_t n = arg(c, 0), hs[64];
        for (uint32_t i = 0; i < n && i < 64; ++i) hs[i] = M32(arg(c, 1) + 4 * i);
        RET(threads_wait(c, n, hs, arg(c, 2) != 0, arg(c, 3)), 5);
    }
    if (!strcmp(fn, "WSARecvFrom"))                   // (s, bufs, count, bytes*, flags*, from, fromlen*, ovl, completion)
        RET(wsa_recv(arg(c, 0), arg(c, 1), arg(c, 3), arg(c, 5), arg(c, 6), arg(c, 7), 1), 9);
    if (!strcmp(fn, "WSARecv"))                       // (s, bufs, count, bytes*, flags*, ovl, completion)
        RET(wsa_recv(arg(c, 0), arg(c, 1), arg(c, 3), 0, 0, arg(c, 5), 0), 7);
    if (!strcmp(fn, "WSAGetOverlappedResult")) {      // (s, ovl, bytes*, wait, flags*)
        uint32_t ovl = arg(c, 1);
        pthread_mutex_lock(&ovl_mu);
        Ovl *o = NULL;
        for (int k = 0; k < MAX_SOCK && !o; ++k) if (ovls[k].active && ovls[k].ovl == ovl) o = &ovls[k];
        uint32_t r = 0;
        if (!o) wsa_err = 10022;
        else if (!o->done) wsa_err = 996;            // WSA_IO_INCOMPLETE
        else {
            if (arg(c, 2)) M32(arg(c, 2)) = o->bytes;
            if (arg(c, 4)) M32(arg(c, 4)) = 0;
            o->active = 0;
            if (o->err) wsa_err = o->err; else r = 1;
        }
        pthread_mutex_unlock(&ovl_mu);
        RET(r, 5);
    }
    if (!strcmp(fn, "WSAIoctl")) {                    // (s, code, in, inlen, out, outlen, bytes*, ovl, completion)
        if (arg(c, 1) == 0xC8000014 && arg(c, 5) >= 16) {   // SIO_ROUTING_INTERFACE_QUERY: our address towards `in`
            struct sockaddr_in to, me;
            if (lan_loopback()) {
                // DirtySock 0084B86B substitutes the destination for a successful
                // 127.0.0.1 answer. Refuse that query so its hostname fallback
                // returns loopback, rather than the probe's 192.168.1.1.
                uint32_t identity=ws2_online ? ws2_local_ip : lan_copy() ? 0x7F000002 : INADDR_LOOPBACK;
                if(identity==INADDR_LOOPBACK) { wsa_err=10051; RET(0xFFFFFFFF,9); }
                me = (struct sockaddr_in){.sin_len = sizeof me, .sin_family = AF_INET,
                    .sin_addr.s_addr = htonl(identity)};
                sa_to_guest(&me, arg(c, 4), 0);
                if (arg(c, 6)) M32(arg(c, 6)) = 16;
                RET(0, 9);
            }
            socklen_t ml = sizeof me;
            int fd = socket(AF_INET, SOCK_DGRAM, 0), ok = 0;
            if (fd >= 0 && sa_from_guest(arg(c, 2), arg(c, 3), &to)) {
                if (!outbound_allowed(&to)) { close(fd); RET(0xFFFFFFFF, 9); }
                if (!to.sin_port) to.sin_port = htons(9);
                ok = !connect(fd, (struct sockaddr *)&to, sizeof to) && !getsockname(fd, (struct sockaddr *)&me, &ml);
            }
            if (fd >= 0) close(fd);
            if (!ok) { me.sin_addr.s_addr = htonl(ws2_local_ip); }
            if (lan_copy()) me.sin_addr.s_addr = htonl(0x7F000002);
            me.sin_port = 0;
            sa_to_guest(&me, arg(c, 4), 0);
            if (arg(c, 6)) M32(arg(c, 6)) = 16;
            RET(0, 9);
        }
        log_once("WSAIoctl: unknown code");
        wsa_err = 10045; RET(0xFFFFFFFF, 9);
    }
    char m[64]; snprintf(m, sizeof m, "unimplemented WS2_32 %s", fn); log_once(m);
    return 0;
}

// ---------------- BOOT_ONLINE hooks (recomp/hooks.txt): logs and one guard; untouched without BOOT_ONLINE ----------
#include "funcs.h"
int ws2_online;                       // boot.c: BOOT_ONLINE is set
static const char *const server_states[] = {"INITIAL", "WELCOME", "WAITJOINS", "LOADING", "WAITLOADS", "READY", "RACING"};
static const char *const client_states[] = {"INITIAL", "WAITING", "CONNECTED", "DESCRIBED", "LOADING", "READY", "RACING"};
static void log_state(const char *who, const char *const *names, uint32_t old, uint32_t now) {
    if (old == now) return;
    printf("online: %s state %s -> %s\n", who, old < 7 ? names[old] : "?", now < 7 ? names[now] : "?");
    fflush(stdout);
    if (diag_on) diag_event("online %s state %u -> %u", who, old, now);
}
void f_007A0060(Cpu *c) {             // Server::SetState(state), cdecl
    if (ws2_online || lan_diag_on()) log_state("server", server_states, M32(0x009B4220), arg(c, 0));
    f_007A0060_orig(c);
}
void f_00792230(Cpu *c) {             // client SetState(state), cdecl
    if (ws2_online || lan_diag_on()) log_state("client", client_states, M32(0x009B41FC), arg(c, 0));
    f_00792230_orig(c);
}
void f_00797450(Cpu *c) {             // client: R_BI_DRIVERFINISH received
    if (ws2_online || lan_diag_on()) { printf("online: client received DRIVERFINISH\n"); fflush(stdout); }
    f_00797450_orig(c);
}
void f_0079FF50(Cpu *c) {             // Server::SendMessageToAllClients(type, bits, reliable)
    if ((ws2_online || lan_diag_on()) && arg(c, 0) == 7) { printf("online: server sends DRIVERFINISH\n"); fflush(stdout); }
    f_0079FF50_orig(c);
}
// GRaceStatus::SetRoaming (from the GRaceStatus constructor) tells ICopMgr about each AI vehicle without a pursuit,
// but an online race creates no AICopManager (ICopMgr::mInstance 0x92C67C stays 0: indirect call to 0 at 005FE510).
// For that call only, a stand-in whose methods do nothing (slot 0x2C takes one argument).
static int copmgr_stub(Cpu *c) { RET(0, 1); }
void f_005FE3E0(Cpu *c) {
    if (!ws2_online || M32(0x0092C67C)) { f_005FE3E0_orig(c); return; }
    static uint32_t obj;
    if (!obj) {
        obj = galloc(8 + 4 * 64);
        uint32_t fn = host_fn(copmgr_stub, "online ICopMgr stand-in");
        M32(obj) = obj + 8;
        for (int i = 0; i < 64; ++i) M32(obj + 8 + 4 * i) = fn;
    }
    M32(0x0092C67C) = obj;
    f_005FE3E0_orig(c);
    if (M32(0x0092C67C) == obj) M32(0x0092C67C) = 0;
}
// Milestones of the online race (BOOT_ONLINE): each logged with its guest caller.
static void online_mark(Cpu *c, const char *what, int once) {
    static const char *seen[16];
    if (!ws2_online) return;
    if (once) {
        int i = 0;
        while (i < 16 && seen[i] && seen[i] != what) ++i;
        if (i < 16 && seen[i]) return;
        if (i < 16) seen[i] = what;
    }
    uint32_t off = 0, ra = M32(c->esp);
    const char *sym = diag_symbol(ra, &off);
    printf("online: %s (from %08X %s+0x%X)\n", what, ra, sym ? sym : "?", off);
    fflush(stdout);
}
void f_00761140(Cpu *c) { online_mark(c, "OnlineManager::Disconnect", 0); f_00761140_orig(c); }
void f_006F2850(Cpu *c) { online_mark(c, "OnlineGame::CreatePlayersAndCars", 0); f_006F2850_orig(c); }
void f_007356A0(Cpu *c) {
    online_mark(c, "OnlineManager::UpdateOutgoing (first)", 1);
    static uint64_t next;
    if (ws2_online && host_ticks() >= next) {          // the car-state send conditions (FUN_00793BE0), every 2 s
        next = host_ticks() + 2000000000ull;
        uint32_t hc = M32(0x009B4218), dn = M32(0x00905DDC);
        printf("online: outgoing hostConn %08X state %d driver %d client %d server-seen %d racer %08X\n", hc,
               hc ? (int)M32(hc + 0x88) : -1, (int)dn, (int)M32(0x009B41FC), (int)M32(0x009B420C), dn < 8 ? M32(0x009B0FA8 + 4 * dn) : 0);
        fflush(stdout);
    }
    f_007356A0_orig(c);
}
static int online_event(void) {
    static int on = -1;
    if (on < 0) on = getenv("BOOT_ONLINE_EVENT") && atoi(getenv("BOOT_ONLINE_EVENT"));
    return on;
}
// StartOnlineRace (007617D0) builds the host's race with SetupRaceParams only when SkipFE == 0 (00761877); the
// SkipFE-online route sets SkipFE, so the host never gets a startup race and free-roams. With BOOT_ONLINE_EVENT the
// host runs SetupOnlineRace itself (the client gets it through ImportRaceParams 00761A31). SetupRaceParams' lobby
// clock (LobbyCore 'stim') is skipped: FEDatabase+0x194 (time of day) keeps its value and the client imports it.
static int building_local_racer;           // f_00760B20: the racer being built is this copy's own
void f_007617D0(Cpu *c) {
    online_mark(c, "OnlineManager::StartOnlineRace", 0);
    building_local_racer = 1;                 // its CreateOnlineRacer(slot 0, persona) builds the local racer
    f_007617D0_orig(c);
    building_local_racer = 0;
    if (ws2_online && online_event() && M32(0x009B416C) && M32(0x00926064)) {
        uint32_t eax = c->eax;
        call_guest(c, 0x005A3590, 0, NULL);
        c->eax = eax;
    }
}
// Game_InitRacers (0061DBE0) adds 1 - racers + GetNumOpponents() AI racers (0061DCA9..0061DCBC, unsigned loop). An
// online race sets 0 opponents (SetupOnlineRace's custom race) and has 2 human racers, so the count wraps to
// 0xFFFFFFFF and the loop never ends (run 12: client hang in GetOpponentChar/AddRacer). Split screen avoids it by
// counting the second human as an opponent (SetNumOpponents(1), 007A2940/007B4020). Online, for that call only, the
// same: at least racers - 1.
void f_005FACD0(Cpu *c) {
    uint32_t ra = M32(c->esp);
    f_005FACD0_orig(c);
    if (ws2_online && ra == 0x0061DCBC) {
        uint32_t rs = M32(0x0091E000), racers = rs ? M32(rs + 0x1958) : 0;
        if (racers && (int32_t)c->eax < (int32_t)racers - 1) {
            printf("online: Game_InitRacers: %u racers, %d opponents -> %u (no AI)\n", racers, (int)c->eax, racers - 1);
            fflush(stdout);
            c->eax = racers - 1;
        }
    }
}
void f_006F9BB0(Cpu *c) { online_mark(c, "OnlineGame::OnlineGame", 0); f_006F9BB0_orig(c); }
// NetConnStartup ('open') clears the 'conn'/'onln' status (009C3624/009C3628); only the LAN screen (UI_PC_LAN_ServerSelect
// ctor 00560470) and 00533650 call NetConnConnect (0083CD10) to set them. The SkipFE-online route calls neither, so once
// racing, NetworkCore::__unk (0078B260) reads 'onln' 0, FUN_007361E0 closes the session and the race shows "Connection to
// other players has been lost". Online: connect right after startup, as the LAN screen does.
void f_0083CCC0(Cpu *c) {
    f_0083CCC0_orig(c);
    if (ws2_online && c->eax == 0) {
        uint32_t eax = c->eax;
        call_guest(c, 0x0083CD10, 0, NULL);
        c->eax = eax;
        printf("online: NetConnConnect after NetConnStartup\n");
        fflush(stdout);
    }
}
// RaceStarter::SetupOnlineRace (005A3590) builds the online race from the event the lobby/LAN screen chose
// (FEDatabase+0x18C, a race-ID hash; +0x168 type, 1 = laps from byte +0x190). The SkipFE-online route sets none, so
// both cars free-roam with no finish (run 11: no lap HUD). BOOT_ONLINE_EVENT=1 (experimental): the BOOT_RACE event and
// laps. Run 12: the client (which imports the host's params) then hangs at frame 119 in Game_InitRacers ->
// GRaceParameters::GetOpponentChar -> Attrib::Collection::GetNode; the host never calls SetupOnlineRace on this route.
void f_005A3590(Cpu *c) {
    uint32_t fe = M32(0x0091CF90);                    // FEDatabase
    if (ws2_online && online_event() && fe) {
        static uint32_t id;
        // race_setup (boot.c) has already validated and normalized SkipFERaceID, including BOOT_RACE=1.
        const char *race = (const char *)G + 0x00926068;
        if (!*race) {
            fprintf(stderr, "online: BOOT_ONLINE_EVENT needs a normalized BOOT_RACE ID; refusing SetupOnlineRace\n");
            exit(2);
        }
        if (!id) id = gstr(race);
        Cpu saved = *c;
        uint32_t hash = call_guest(c, 0x004519D0, 1, &id);   // Attrib::StringHash32 (cdecl)
        c->ecx = M32(0x0091E004);                         // GRaceDatabase::mObj
        uint32_t event = c->ecx ? call_guest(c, 0x005FB710, 1, &hash) : 0;
        *c = saved;
        if (!event) {
            // AllocCustomRace(NULL) returns NULL; the original immediately calls setters on that result.
            fprintf(stderr, "online: BOOT_RACE \"%s\" (hash %08X) is not a race; refusing SetupOnlineRace\n", race, hash);
            exit(2);
        }
        int laps = getenv("BOOT_RACE_LAPS") ? atoi(getenv("BOOT_RACE_LAPS")) : 1;
        uint32_t was = M32(fe + 0x18C);
        M32(fe + 0x18C) = hash;
        M32(fe + 0x168) = 1;
        G[fe + 0x190] = (uint8_t)(laps > 0 ? laps : 1);
        printf("online: race %s (hash %08X, was %08X), %d laps\n", gptr(id), hash, was, laps);
        fflush(stdout);
    }
    f_005A3590_orig(c);
}
// OnlineRacer constructor (00760B20, thiscall: slot index, ..., name): with SkipFE set it names every racer "SkipFE"
// (0x70696B53 0x4546 at +0x1156) instead of the lobby name, so both racers on both copies are "SkipFE" [V oc_d1; the
// lobby persona, FEDatabase+0x134+0x84, is never used for it]. OnlineManager::FinishDriver (00755140) finds a driver's
// racer by that name (GetOnlineRacer 00735AE0, first match): when the client finished first, the host's own finish
// matched the client's racer (state 3, finished) and returned without Finish__11OnlineRacer, so R_BI_DRIVERFINISH was
// never sent (3 of 4 online_check runs on 15.2.1). The local racer is the one StartOnlineRace (007617D0) builds
// (CreateOnlineRacer(0, .., persona)); remote racers are built later, also in slot 0 on the host (oc_g1), so the slot
// can't tell them apart. The host's racer name reaches the client in the join data (oc_f1, oc_g1). So the local racer
// is named BOOT_ONLINE_NAME (default "Host" / "Client") and every other one "SkipFE<slot>": different names on each copy.
void f_00760B20(Cpu *c) {
    uint32_t self = c->ecx, slot = arg(c, 0) & 0xFF;
    f_00760B20_orig(c);
    if (ws2_online && self && !strcmp((char *)G + self + 0x1156, "SkipFE")) {
        const char *nm = getenv("BOOT_ONLINE_NAME") ? getenv("BOOT_ONLINE_NAME") : M32(0x009B416C) ? "Host" : "Client";
        char *dst = (char *)G + self + 0x1156;
        memset(dst, 0, 16);
        // A user-chosen name ends in the role (-H/-C), so two copies can't pick the same one and it can't match a
                                                                                                                
        if (!building_local_racer) snprintf(dst, 16, "SkipFE%u", slot);
        else if (getenv("BOOT_ONLINE_NAME")) snprintf(dst, 16, "%.13s-%c", nm, M32(0x009B416C) ? 'H' : 'C');
        else strncpy(dst, nm, 15);
        printf("online: racer %08X slot %u named \"%s\"\n", self, slot, dst);
        fflush(stdout);
    }
}
// OnlineManager::FinishDriver(driver, ..., reason) (00755140, from Game_NotifyRaceFinished 00611B0C when the race
// context GRaceStatus+0x1964 is 1): logs the driver, the racer its name finds and that racer's state (+0x1144).
void f_00755140(Cpu *c) {
    if (ws2_online) {
        uint32_t rs = M32(0x0091E000), om = c->ecx, idx = arg(c, 0);
        printf("online: FinishDriver driver %u reason %u context %d local racer %08X\n", idx, arg(c, 3),
               rs ? (int)M32(rs + 0x1964) : -1, om ? M32(om + 4) : 0);
        // the driver's GRacerInfo name (GetRacerInfo 005DBED0: fObj + 0x18 + 0x194 * driver, name pointer at +8) and
        // the OnlineManager state (0x9B0FA0, compared with 6 at 00755195) and each slot: racer, name (+0x1156), state (+0x1144), slot index (+0x1148)
        uint32_t ri = rs ? rs + 0x18 + 0x194 * idx : 0, np = ri ? M32(ri + 8) : 0;
        printf("online:   driver name \"%.15s\"; OnlineManager state %u", np ? (char *)G + np : "", M32(0x009B0FA0));
        for (int i = 0; om && i < 4; ++i) {
            uint32_t r = M32(om + 8 + 4 * i);
            if (r) printf("; slot %d %08X \"%.15s\" state %u index %u", i, r, (char *)G + r + 0x1156, M32(r + 0x1144), G[r + 0x1148]);
        }
        printf("\n");
        fflush(stdout);
    }
    f_00755140_orig(c);
    if (ws2_online) { printf("online: FinishDriver -> %u\n", c->eax & 0xFF); fflush(stdout); }
}
