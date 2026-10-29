// Test-only transport adapter: default native traffic stays on 127.0.0.1.
// NFS_LAN_REAL=1 deliberately enables the production LAN addresses/fanout.
// Included after socket headers and before production ws2_32.c by the harness.
static unsigned lan_native_binds, lan_nonloopback_binds;
static uint32_t lan_requested_bind;
static int lan_real(void) {
    const char *v = getenv("NFS_LAN_REAL");
    return v && !strcmp(v, "1");
}
static struct sockaddr_in lan_transport_address(const struct sockaddr *a) {
    struct sockaddr_in out = *(const struct sockaddr_in *)a;
    if (!lan_real()) out.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    return out;
}
static int lan_test_bind(int fd, const struct sockaddr *a, socklen_t len) {
    lan_requested_bind = ((const struct sockaddr_in *)a)->sin_addr.s_addr;
    struct sockaddr_in to = lan_transport_address(a);
    ++lan_native_binds;
    lan_nonloopback_binds += to.sin_addr.s_addr != htonl(INADDR_LOOPBACK);
    if (!lan_real() && to.sin_addr.s_addr != htonl(INADDR_LOOPBACK)) { errno = EACCES; return -1; }
    fprintf(stderr, "LAN NATIVE BIND %s:%u\n", inet_ntoa(to.sin_addr), ntohs(to.sin_port));
    return bind(fd, (const struct sockaddr *)&to, len);
}
static int lan_test_connect(int fd, const struct sockaddr *a, socklen_t len) {
    struct sockaddr_in to = lan_transport_address(a);
    if (!lan_real() && to.sin_addr.s_addr != htonl(INADDR_LOOPBACK)) { errno = EACCES; return -1; }
    return connect(fd, (const struct sockaddr *)&to, len);
}
static ssize_t lan_test_sendto(int fd, const void *p, size_t n, int flags, const struct sockaddr *a, socklen_t len) {
    struct sockaddr_in to = lan_transport_address(a);
    if (!lan_real() && to.sin_addr.s_addr != htonl(INADDR_LOOPBACK)) { errno = EACCES; return -1; }
    return sendto(fd, p, n, flags, (const struct sockaddr *)&to, len);
}
// A failed bind must not let listen() implicitly bind an ANY address.
static int lan_test_listen(int fd, int backlog) {
    if (!lan_real()) {
        struct sockaddr_in a; socklen_t len = sizeof a;
        if (getsockname(fd, (struct sockaddr *)&a, &len) ||
            a.sin_addr.s_addr != htonl(INADDR_LOOPBACK) || !a.sin_port) { errno = EACCES; return -1; }
    }
    return listen(fd, backlog);
}
#undef listen
#undef bind
#undef connect
#undef sendto
#define bind lan_test_bind
#define connect lan_test_connect
#define sendto lan_test_sendto
#define listen lan_test_listen
