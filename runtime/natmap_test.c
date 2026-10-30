// Deterministic router transport fixtures: no sockets, DNS, multicast or router traffic.
#include "natmap.h"
#include "roam_online.h"
#include <arpa/inet.h>
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <ifaddrs.h>
#include <net/route.h>
#include <net/if.h>
#include <netdb.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/socket.h>
#include <sys/sysctl.h>
#include <time.h>
#include <unistd.h>
static int checks, failures, adds, deletes, permanent_adds, scenario, hostname_fixture, host_headers, pins;
static atomic_int cancel;
static char request[8192];
#define CHECK(x) do { ++checks; if (!(x)) { ++failures; printf("FAIL %d: %s\n", __LINE__, #x); } } while (0)
static int fake_socket(int domain, int type, int protocol) { (void)domain; (void)type; (void)protocol; return 123; }
static int fake_close(int fd) { (void)fd; return 0; }
static int fake_setsockopt(int fd, int level, int name, const void *value, socklen_t n) {
    (void)fd; (void)level; (void)name; (void)value; (void)n; return 0;
}
static int fake_getsockopt(int fd, int level, int name, void *value, socklen_t *n) {
    (void)fd; (void)level; (void)name; (void)n; *(int *)value = 0; return 0;
}
static int fake_fcntl(int fd, int op, ...) { (void)fd; (void)op; return 0; }
static int fake_connect(int fd, const struct sockaddr *addr, socklen_t n) {
    (void)fd; (void)n; const struct sockaddr_in *a = (const void *)addr;
    CHECK(ntohl(a->sin_addr.s_addr) == 0x7f000001u); ++pins; request[0] = 0; return 0;
}
static int fake_poll(struct pollfd *p, nfds_t n, int timeout) {
    (void)n; (void)timeout; p->revents = p->events; return 1;
}
static ssize_t fake_send(int fd, const void *data, size_t n, int flags) {
    (void)fd; (void)flags; CHECK(n < sizeof request);
    memcpy(request, data, n); request[n] = 0;
    if (hostname_fixture) { CHECK(strstr(request, "Host: router.test:5000\r\n")); ++host_headers; }
    if (strstr(request, "#AddPortMapping\"")) {
        ++adds; if (strstr(request, "<NewLeaseDuration>0</NewLeaseDuration>")) ++permanent_adds;
        if (scenario == 3) atomic_store(&cancel, 1);
    }
    if (strstr(request, "#DeletePortMapping\"")) ++deletes;
    return (ssize_t)n;
}
static ssize_t fake_recv(int fd, void *data, size_t n, int flags) {
    (void)fd; (void)flags;
    const char *body = "", *status = "200 OK";
    if (strstr(request, "GET ")) {
        body = hostname_fixture ?
            "<root><URLBase>http://router.test:5000/base/</URLBase><service><serviceType>urn:schemas-upnp-org:service:WANIPConnection:1</serviceType><controlURL>http://router.test:5000/ctl</controlURL></service></root>" :
            "<root><service><serviceType>urn:schemas-upnp-org:service:WANIPConnection:1</serviceType><controlURL>/ctl</controlURL></service></root>";
    } else if (strstr(request, "#GetExternalIPAddress\"")) body = "<NewExternalIPAddress>203.0.113.5</NewExternalIPAddress>";
    else if (strstr(request, "#GetSpecificPortMappingEntry\"")) {
        if (scenario == 9) { errno = ECONNRESET; return -1; }
        if (scenario == 6) body = "<NewInternalClient>127.0.0.2</NewInternalClient><NewInternalPort>47800</NewInternalPort><NewPortMappingDescription>Online free roam</NewPortMappingDescription>";
        else if (scenario == 7) body = "<NewInternalClient>127.0.0.1</NewInternalClient><NewInternalPort>47801</NewInternalPort><NewPortMappingDescription>Online free roam</NewPortMappingDescription>";
        else if (scenario == 8) body = "<NewInternalClient>127.0.0.1</NewInternalClient><NewInternalPort>47800</NewInternalPort><NewPortMappingDescription>Other service</NewPortMappingDescription>";
        else body = "<NewInternalClient>127.0.0.1</NewInternalClient><NewInternalPort>47800</NewInternalPort><NewPortMappingDescription>Online free roam</NewPortMappingDescription>";
    }
    else if (strstr(request, "#DeletePortMapping\"")) body = "<DeletePortMappingResponse/>";
    else if (strstr(request, "#AddPortMapping\"")) {
        if (scenario == 2) { errno = ECONNRESET; return -1; }
        if ((scenario == 1 || scenario == 4) && !strstr(request, "<NewLeaseDuration>0</NewLeaseDuration>")) {
            status = "500 Internal Server Error";
            body = scenario == 4 ? "<Fault><errorCode>718</errorCode></Fault>" : "<Fault><errorCode>725</errorCode></Fault>";
        } else if (scenario == 5) {
            body = "<not-an-add-response/>";
        } else body = "<AddPortMappingResponse/>";
    }
    int count = snprintf(data, n, "HTTP/1.1 %s\r\nContent-Length: %zu\r\n\r\n%s", status, strlen(body), body);
    CHECK(count > 0 && (size_t)count < n); return count;
}
static ssize_t fake_sendto(int fd, const void *data, size_t n, int flags, const struct sockaddr *to, socklen_t len) {
    (void)fd; (void)data; (void)flags; (void)to; (void)len; return (ssize_t)n;
}
static ssize_t fake_recvfrom(int fd, void *data, size_t n, int flags, struct sockaddr *from, socklen_t *len) {
    (void)fd; (void)flags; (void)len;
    struct sockaddr_in *a = (void *)from; a->sin_addr.s_addr = htonl(0x7f000001u);
    return snprintf(data, n, "HTTP/1.1 200 OK\r\nLOCATION: http://%s:5000/root.xml\r\n\r\n", hostname_fixture ? "router.test" : "127.0.0.1");
}
static int fake_getaddrinfo(const char *host, const char *service, const struct addrinfo *hints, struct addrinfo **out) {
    (void)service; CHECK(hints->ai_family == AF_INET);
    static struct sockaddr_in addr, other; static struct addrinfo info, next;
    addr.sin_family = AF_INET; addr.sin_addr.s_addr = htonl(!strcmp(host, "router.test") ? 0x7f000001u : 0x7f000002u);
    info.ai_family = AF_INET; info.ai_addrlen = sizeof addr; info.ai_addr = (void *)&addr; info.ai_next = NULL;
    if (!strcmp(host, "mixed.test")) {
        addr.sin_addr.s_addr = htonl(0x7f000001u); other = addr; other.sin_addr.s_addr = htonl(0x7f000002u);
        next = info; next.ai_addr = (void *)&other; next.ai_next = NULL; info.ai_next = &next;
    }
    *out = &info; return 0;
}
static void fake_freeaddrinfo(struct addrinfo *a) { (void)a; }
#define socket fake_socket
#define close fake_close
#define setsockopt fake_setsockopt
#define getsockopt fake_getsockopt
#define fcntl fake_fcntl
#define connect fake_connect
#define poll fake_poll
#define send fake_send
#define recv fake_recv
#define sendto fake_sendto
#define recvfrom fake_recvfrom
#define getaddrinfo fake_getaddrinfo
#define freeaddrinfo fake_freeaddrinfo
#ifndef NATMAP_SOURCE
#define NATMAP_SOURCE "natmap.c"
#endif
#include NATMAP_SOURCE
static NatMap mapping(void) {
    NatMap m = {.gateway=0x7f000001u, .local_ip=0x7f000001u, .internal_port=47800, .external_port=47800, .protocol=3};
    snprintf(m.control, sizeof m.control, "http://127.0.0.1:5000/ctl");
    snprintf(m.service, sizeof m.service, "urn:schemas-upnp-org:service:WANIPConnection:1"); return m;
}
int main(int argc, char **argv) {
    if (argc != 2) return 2;
    if (!strcmp(argv[1], "lease")) {
        scenario = 1; NatMap m = mapping();
        CHECK(discover_upnp(&m, &cancel) < 0); CHECK(adds == 1 && permanent_adds == 0);
        natmap_close(&m); CHECK(deletes == 0); natmap_close(&m); CHECK(deletes == 0);
    } else if (!strcmp(argv[1], "lost")) {
        for (scenario = 2; scenario <= 5; ++scenario) {
            atomic_store(&cancel, 0); NatMap m = mapping();
            CHECK(discover_upnp(&m, &cancel) < 0); CHECK(!m.mapped);
            CHECK(!m.upnp_owned); natmap_close(&m); CHECK(deletes == 0);
        }
    } else if (!strcmp(argv[1], "owned")) {
        NatMap m = mapping(); CHECK(!discover_upnp(&m, &cancel)); CHECK(m.mapped && m.upnp_owned);
        // A lost renewal does not discard ownership acquired by the successful Add.
        scenario = 2; CHECK(natmap_renew(&m, &cancel) < 0); CHECK(m.upnp_owned);
        natmap_close(&m); CHECK(deletes == 1 && !m.upnp_owned && !m.mapped);
        natmap_close(&m); CHECK(deletes == 1);
    } else if (!strcmp(argv[1], "reassigned")) {
        for (int other = 6; other <= 9; ++other) {
            scenario = 0; NatMap m = mapping(); CHECK(!discover_upnp(&m, &cancel));
            scenario = other; natmap_close(&m); CHECK(deletes == 0);
        }
    } else if (!strcmp(argv[1], "hostname")) {
        hostname_fixture = 1; NatMap m = mapping();
        CHECK(!discover_upnp(&m, &cancel)); CHECK(m.mapped && adds == 1);
        natmap_close(&m); CHECK(deletes == 1); CHECK(host_headers == 5 && pins == 5);
        Url u;
        CHECK(!url("http://router.test:5000/root.xml", m.gateway, &u) && u.ip == m.gateway);
        CHECK(url("http://wrong.test:5000/root.xml", m.gateway, &u) < 0);
        CHECK(url("http://127.0.0.2/root.xml", m.gateway, &u) < 0);
        CHECK(url("http://router.test\r\nX:1/", m.gateway, &u) < 0);
        CHECK(url("http://user@router.test/", m.gateway, &u) < 0);
        CHECK(url("http://mixed.test/", m.gateway, &u) < 0);
        CHECK(url("http://router.test:\r\n80/", m.gateway, &u) < 0);
        CHECK(url("http://router.test:+80/", m.gateway, &u) < 0);
        CHECK(url("http://router.test:/", m.gateway, &u) < 0);
    } else return 2;
    printf("%s: %d checks, %d failures; adds=%d permanent=%d deletes=%d hosts=%d pins=%d\n", argv[1], checks, failures, adds, permanent_adds, deletes, host_headers, pins);
    return failures ? 1 : 0;
}
