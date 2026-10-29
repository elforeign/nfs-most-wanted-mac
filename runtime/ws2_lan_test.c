// Socket complement to ws2_policy_test.c; loopback by default, NFS_LAN_REAL=1 for real LAN.
// One binary handles every mutation by argument (ws2_lan_test.py).
// Non-socket: 5029/0; broken failures: broadcast/reuse/bind/identity/route/target 5 each, shift 10.
                                                                                             
// Fixed fixture: 5/0; overlap control 5/2; boolean control 5/1 (ws2_lan_fixture_test.py).
                                                                      
// Revised loopback/default and single-binary REAL modes: native rerun pending here (bind EPERM).
                                                                                                     
#define WS2_REAL_SOCKETS 1
#define main policy_main_unused
#include "ws2_policy_test.c"
#undef main

static void socket_check(int ok, const char *what) {
    check(ok, what);
    check(lan_real() || lan_nonloopback_binds == 0, "default kernel binds are 127.0.0.1 only");
    printf("LAN BIND AUDIT: %u native binds, %u non-loopback, real=%d\n", lan_native_binds, lan_nonloopback_binds, lan_real());
    printf("WS2 LAN %s: %s\n", ok ? "PASS" : "FAIL", what);
}
#define check socket_check

static uint32_t udp(void) {
    uint32_t s = invoke("#23", 3, 2u, 2u, 0u);
    M32(0x6000) = 1;
    // Literal Winsock SOL_SOCKET/SO_REUSEADDR, as the game's pre-bind call.
    check(invoke("#21", 5, s, 0xFFFFu, 4u, 0x6000u, 4u) == 0, "guest SO_REUSEADDR before bind");
    check(invoke("#21", 5, s, 0xFFFFu, (uint32_t)SO_BROADCAST, 0x6000u, 4u) == 0, "enable broadcast");
    check(invoke("#10", 3, s, 0x8004667Eu, 0x6000u) == 0, "nonblocking receive");
    return s;
}
// BSD returns the option bit (512 on macOS), not necessarily the integer 1.
static int reuse_enabled(uint32_t s) {
    int reuse = 0; socklen_t len = sizeof reuse;
    return !getsockopt(sock_fd(s), SOL_SOCKET, SO_REUSEPORT, &reuse, &len) && reuse != 0;
}
static int packet(uint32_t s, unsigned tag, uint32_t expected_ip) {
    struct pollfd p = {.fd=sock_fd(s), .events=POLLIN};
    uint64_t until = clock_gettime_nsec_np(CLOCK_MONOTONIC) + 1000000000ull;
    while (clock_gettime_nsec_np(CLOCK_MONOTONIC) < until) {
        if (poll(&p, 1, 50) <= 0) continue;
        M32(0x7300) = 16;
        uint32_t n = invoke("#17", 6, s, 0x7000u, 384u, 0u, 0x7200u, 0x7300u);
        if (n == 384 && M32(0x7000) == tag)
            return !expected_ip || (M32(0x7204) == htonl(expected_ip) && ntohs(M16(0x7202)) == 9999);
    }
    return 0;
}
static void broadcast(uint32_t s, unsigned tag) {
    memset(G + 0x3000, 0, 384); M32(0x3000) = tag;
    address("255.255.255.255", 9999);
    check(invoke("#20", 6, s, 0x3000u, 384u, 0u, 0x2000u, 16u) == 384, "384 byte broadcast send");
}
int main(void) {
    // Socket creation or preflight bind EPERM means pending. Later delivery errors fail.
    int probe = socket(AF_INET, SOCK_DGRAM, 0);
    if (probe < 0) {
        if (errno == EPERM) { puts("SOCKET PENDING: run on a socket-enabled host (ws2_lan_test)"); return 3; }
        perror("socket"); puts("WS2 LAN FAIL: socket preflight"); return 1;
    }
    struct sockaddr_in preflight = {.sin_len=sizeof preflight, .sin_family=AF_INET, .sin_addr.s_addr=htonl(INADDR_LOOPBACK)};
    if (bind(probe, (struct sockaddr *)&preflight, sizeof preflight) && errno == EPERM) {
        close(probe); puts("SOCKET PENDING: run on a socket-enabled host (bind EPERM)"); return 3;
    }
    close(probe);
    G = calloc(1, 0x01000000); if (!G) return 2;
    unsetenv("NFS_LAN_COPY"); unsetenv("NFS_ONLINE_OPEN"); unsetenv("BOOT_ONLINE_PEER");
    uint32_t ip = lan_address();
    check(ip && (lan_real() ? (ip >> 24) != 127 : ip == INADDR_LOOPBACK), "selected transport route exists");
    uint32_t a = udp(); address("0.0.0.0", 9999);
    check(invoke("#2", 3, a, 0x2000u, 16u) == 0, "first UDP 9999 bind");
    if (!lan_real()) {
        broadcast(a, 0xA200);
        check(packet(a, 0xA200, ip), "copy 0 loopback 384-byte receive");
    }
    setenv("NFS_LAN_COPY", "1", 1);
    uint32_t b = udp(); address("0.0.0.0", 9999);
    check(invoke("#2", 3, b, 0x2000u, 16u) == 0, "second UDP 9999 bind");
    check(reuse_enabled(a), "native reuseport enabled on first binder");
    check(reuse_enabled(b), "native reuseport enabled on second binder");
    address("192.168.1.1", 9);
    check(invoke("WSAIoctl", 9, b, 0xC8000014u, 0x2000u, 16u, 0x4000u, 16u, 0u, 0u, 0u) == 0 &&
        M32(0x4004) == htonl(0x7F000002), "copy 1 route identity");
    int enabled = 0; socklen_t enabled_len = sizeof enabled;
    check(!getsockopt(sock_fd(a), SOL_SOCKET, SO_BROADCAST, &enabled, &enabled_len) && enabled != 0,
        "native broadcast option enabled");
    if (lan_real()) {
        broadcast(a, 0xA201);
        check(packet(a, 0xA201, ip), "first binder receives copy 0 broadcast");
        check(packet(b, 0xA201, ip), "second binder receives copy 0 broadcast");
        broadcast(b, 0xA202);
        check(packet(a, 0xA202, 0x7F000002), "first binder sees copy 1 distinct identity");
        check(packet(b, 0xA202, 0x7F000002), "second binder recognizes its own identity");
        check(invoke("#3", 1, a) == 0, "first discovery cleanup");
    } else {
        // UDP unicast selects one shared-port receiver; it is not broadcast
        // fanout. Test each receive path alone; actual fanout is opt-in above.
        check(invoke("#3", 1, a) == 0, "first discovery cleanup");
        broadcast(b, 0xA202);
        check(packet(b, 0xA202, 0x7F000002), "copy 1 loopback distinct identity");
    }
    check(invoke("#3", 1, b) == 0, "second discovery cleanup");
    struct sockaddr_in translated = {.sin_family=AF_INET, .sin_port=htons(9900),
        .sin_addr.s_addr=htonl(0x7F000002)};
    lan_target(&translated);
    check(ntohs(translated.sin_port) == 9901 && translated.sin_addr.s_addr == htonl(INADDR_LOOPBACK),
        "copy 1 lobby target");
    unsetenv("NFS_LAN_COPY");
    // Bind the actual interface, and separately prove the DLL's loopback fallback accepts LAN clients.
    uint32_t listener = invoke("#23", 3, 2u, 1u, 0u);
    address("0.0.0.0", 0); M32(0x2004) = htonl(ip);
    check(invoke("#2", 3, listener, 0x2000u, 16u) == 0, "explicit LAN address bind");
    invoke("#3", 1, listener);
    listener = invoke("#23", 3, 2u, 1u, 0u);
    address("127.0.0.1", 9900);
    check(invoke("#2", 3, listener, 0x2000u, 16u) == 0, "lobby bind");
    check(lan_requested_bind == INADDR_ANY, "production lobby requests LAN-capable bind");
    check(invoke("#13", 2, listener, 4u) == 0, "lobby listen");
    int client = socket(AF_INET, SOCK_STREAM, 0);
    struct sockaddr_in target = {.sin_len=sizeof target, .sin_family=AF_INET,
        .sin_port=htons(9900), .sin_addr.s_addr=htonl(ip)};
    fcntl(client, F_SETFL, O_NONBLOCK);
    int r = connect(client, (struct sockaddr *)&target, sizeof target);
    struct pollfd cp = {.fd=client, .events=POLLOUT};
    int e = r ? errno : 0; socklen_t el = sizeof e;
    if (e == EINPROGRESS && poll(&cp, 1, 1000) > 0) getsockopt(client, SOL_SOCKET, SO_ERROR, &e, &el);
    check(!e, "LAN client reaches lobby");
    close(client); invoke("#3", 1, listener);
    check(lan_real() || lan_nonloopback_binds == 0, "default kernel binds are 127.0.0.1 only");
    printf("LAN BIND AUDIT: %u native binds, %u non-loopback, real=%d\n", lan_native_binds, lan_nonloopback_binds, lan_real());
    printf("WS2 LAN %s: %u checks, %u failures\n", failures ? "FAIL" : "PASS", checks, failures);
    free(G); return failures ? 1 : 0;
}
