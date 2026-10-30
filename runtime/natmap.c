// Router I/O, called only by explicit Host. All waits are bounded and cancellable.
#include "natmap.h"
#include "roam_online.h"
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <ifaddrs.h>
#include <net/route.h>
#include <netdb.h>
#include <ctype.h>
#include <net/if.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/socket.h>
#include <sys/sysctl.h>
#include <time.h>
#include <unistd.h>
static uint64_t milliseconds(void) {
    struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return (uint64_t)t.tv_sec * 1000 + t.tv_nsec / 1000000;
}
static int stopped(const atomic_int *s) { return s && atomic_load(s); }
static int ready(int fd, short events, uint64_t end, const atomic_int *stop) {
    while (!stopped(stop)) {
        uint64_t now = milliseconds(); if (now >= end) return -1;
        struct pollfd p = {.fd = fd, .events = events};
        int rc = poll(&p, 1, (int)(end - now > 50 ? 50 : end - now));
        if (rc > 0) return (p.revents & events) || (events == POLLIN && (p.revents & POLLHUP)) ? 0 : -1;
        if (rc < 0 && errno != EINTR) return -1;
    }
    return -1;
}
static uint32_t gateway(void) {
    int mib[] = {CTL_NET, PF_ROUTE, 0, AF_INET, NET_RT_FLAGS, RTF_GATEWAY}; size_t n = 0;
    if (sysctl(mib, 6, NULL, &n, NULL, 0) || !n || n > 1024 * 1024) return 0;
    char *buf = malloc(n); if (!buf) return 0;
    if (sysctl(mib, 6, buf, &n, NULL, 0)) { free(buf); return 0; }
    uint32_t result = 0;
    for (char *p = buf; p + sizeof(struct rt_msghdr) <= buf + n;) {
        struct rt_msghdr *r = (void *)p;
        if (r->rtm_msglen < sizeof *r || p + r->rtm_msglen > buf + n) break;
        struct sockaddr *dst = NULL, *gw = NULL;
        char *q = p + sizeof *r;
        for (int i = 0; i < RTAX_MAX; ++i) if (r->rtm_addrs & (1 << i)) {
            if (q + sizeof(struct sockaddr) > p + r->rtm_msglen) break;
            struct sockaddr *a = (void *)q;
            size_t len = a->sa_len ? 1 + ((a->sa_len - 1) | (sizeof(uint32_t) - 1)) : sizeof(uint32_t);
            if (q + len > p + r->rtm_msglen) break;
            if (i == RTAX_DST) dst = a;
            if (i == RTAX_GATEWAY) gw = a;
            q += len;
        }
        if ((r->rtm_flags & RTF_UP) && dst && gw && dst->sa_family == AF_INET && gw->sa_family == AF_INET &&
            !((struct sockaddr_in *)dst)->sin_addr.s_addr) {
            result = ntohl(((struct sockaddr_in *)gw)->sin_addr.s_addr); break;
        }
        p += r->rtm_msglen;
    }
    free(buf); return result;
}
static int udp(uint32_t ip, uint16_t port, uint32_t *local) {
    int fd = socket(AF_INET, SOCK_DGRAM, 0); if (fd < 0) return -1;
    struct sockaddr_in a = {.sin_family = AF_INET, .sin_addr.s_addr = htonl(ip), .sin_port = htons(port)};
    if (connect(fd, (struct sockaddr *)&a, sizeof a)) { close(fd); return -1; }
    if (local) { socklen_t n = sizeof a; if (getsockname(fd, (struct sockaddr *)&a, &n)) { close(fd); return -1; }
        *local = ntohl(a.sin_addr.s_addr); }
    return fd;
}
static int exchange(NatMap *m, const uint8_t *req, size_t n, uint8_t *out, size_t cap, const atomic_int *stop) {
    int fd = udp(m->gateway, 5351, NULL); if (fd < 0) return -1;
    int got = -1;
    for (int i = 0; i < 3 && !stopped(stop); ++i) {
        if (send(fd, req, n, 0) != (ssize_t)n) break;
        if (!ready(fd, POLLIN, milliseconds() + (250u << i), stop)) {
            got = (int)recv(fd, out, cap, 0); if (got >= 0) break;
        }
    }
    close(fd); return got;
}
static int port_ok(uint16_t port) { return port >= ROAM_CODE_PORT && port <= ROAM_CODE_PORT + 255; }
static int pmp_map(NatMap *m, uint32_t life, const atomic_int *stop) {
    uint8_t req[12], res[64]; NatReply r = {0};
    size_t n = natpmp_request(req, 0, m->internal_port, m->external_port, life);
    int got = exchange(m, req, n, res, sizeof res, stop);
    if (got < 0 || natpmp_response(res, (size_t)got, 0, m->internal_port, &r)) return -1;
    m->external_port = r.port; m->lifetime = r.lifetime;
    return life && !r.lifetime ? -1 : 0;
}
static int pcp_map(NatMap *m, uint32_t life, const atomic_int *stop) {
    uint8_t req[60], res[128]; NatReply r = {0};
    size_t n = pcp_request(req, m->local_ip, m->internal_port, m->external_port, life, m->nonce);
    int got = exchange(m, req, n, res, sizeof res, stop);
    if (got < 0 || pcp_response(res, (size_t)got, m->internal_port, m->nonce, &r)) return -1;
    m->external_ip = r.ip; m->external_port = r.port; m->lifetime = r.lifetime;
    return life && !r.lifetime ? -1 : 0;
}
typedef struct { uint32_t ip; uint16_t port; char path[512], authority[256]; } Url;
static int url(const char *s, uint32_t gateway_ip, Url *u) {
    if (strncmp(s, "http://", 7)) return -1;
    s += 7; const char *slash = strchr(s, '/'); size_t n = slash ? (size_t)(slash - s) : strlen(s);
    if (!n || n >= sizeof u->authority) return -1;
    char host[256]; memcpy(host, s, n); host[n] = 0;
    memcpy(u->authority, host, n + 1);
    char *colon = strchr(host, ':'); u->port = 80;
    if (colon) { char *end; *colon++ = 0;
        if (!*colon) return -1;
        for (const unsigned char *p = (const unsigned char *)colon; *p; ++p)
            if (!isdigit(*p)) return -1;
        long v = strtol(colon, &end, 10);
        if (*end || v < 1 || v > 65535) return -1;
        u->port = (uint16_t)v; }
    if (!*host) return -1;
    for (const unsigned char *p = (const unsigned char *)host; *p; ++p)
        if (!isalnum(*p) && *p != '.' && *p != '-') return -1;
    struct in_addr a;
    if (inet_pton(AF_INET, host, &a) == 1) {
        if (ntohl(a.s_addr) != gateway_ip) return -1;
    } else {
        struct addrinfo hints = {.ai_family = AF_INET, .ai_socktype = SOCK_STREAM}, *all = NULL;
        if (getaddrinfo(host, NULL, &hints, &all)) return -1;
        int match = all != NULL;
        for (struct addrinfo *p = all; p; p = p->ai_next)
            if (p->ai_family != AF_INET || p->ai_addrlen < sizeof(struct sockaddr_in) ||
                ntohl(((struct sockaddr_in *)p->ai_addr)->sin_addr.s_addr) != gateway_ip) match = 0;
        freeaddrinfo(all);
        if (!match) return -1;
    }
    u->ip = gateway_ip; // Pin transport to the discovered gateway, never resolve again for connect.
    if (slash && (strlen(slash) >= sizeof u->path || strpbrk(slash, "\r\n "))) return -1;
    snprintf(u->path, sizeof u->path, "%s", slash ? slash : "/"); return 0;
}
static int unchunk(char *body) {
    char *src = body, *dst = body;
    for (;;) {
        char *end; unsigned long n = strtoul(src, &end, 16);
        if (end == src || (*end != '\r' && *end != ';')) return -1;
        char *line = strstr(end, "\r\n"); if (!line) return -1; src = line + 2;
        if (!n) { *dst = 0; return 0; }
        if (n > strlen(src) || strlen(src) - n < 2 || src[n] != '\r' || src[n + 1] != '\n') return -1;
        memmove(dst, src, n); dst += n; src += n + 2;
    }
}
static int http(const char *location, uint32_t gw, const char *service, const char *action, const char *args,
                char *reply, size_t cap, const atomic_int *stop) {
    Url u; if (url(location, gw, &u) || stopped(stop)) return -1;
    int fd = socket(AF_INET, SOCK_STREAM, 0); if (fd < 0) return -1;
    int one = 1; setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &one, sizeof one);
    fcntl(fd, F_SETFL, O_NONBLOCK);
    struct sockaddr_in a = {.sin_family = AF_INET, .sin_addr.s_addr = htonl(u.ip), .sin_port = htons(u.port)};
    uint64_t deadline = milliseconds() + 1800;
    if (connect(fd, (struct sockaddr *)&a, sizeof a) && errno != EINPROGRESS) { close(fd); return -1; }
    if (ready(fd, POLLOUT, deadline, stop)) { close(fd); return -1; }
    int err = 0; socklen_t en = sizeof err;
    if (getsockopt(fd, SOL_SOCKET, SO_ERROR, &err, &en) || err) { close(fd); return -1; }
    char body[2048] = "", req[4096];
    int len = 0;
    if (action) { len = upnp_soap(body, sizeof body, service, action, args); if (len < 0) { close(fd); return -1; } }
    int n = action ? snprintf(req, sizeof req, "POST %s HTTP/1.1\r\nHost: %s\r\nConnection: close\r\n"
        "Content-Type: text/xml; charset=\"utf-8\"\r\nSOAPAction: \"%s#%s\"\r\nContent-Length: %d\r\n\r\n%s",
        u.path, u.authority, service, action, len, body) :
        snprintf(req, sizeof req, "GET %s HTTP/1.1\r\nHost: %s\r\nConnection: close\r\n\r\n", u.path, u.authority);
    if (n < 0 || (size_t)n >= sizeof req) { close(fd); return -1; }
    size_t sent = 0, used = 0;
    while (sent < (size_t)n) {
        if (ready(fd, POLLOUT, deadline, stop)) goto fail;
        ssize_t q = send(fd, req + sent, (size_t)n - sent, 0); if (q <= 0) goto fail; sent += (size_t)q;
    }
    for (;;) {
        if (ready(fd, POLLIN, deadline, stop)) goto fail;
        ssize_t q = recv(fd, reply + used, cap - used - 1, 0); if (q < 0) goto fail;
        if (!q) break;
        used += (size_t)q; reply[used] = 0;
        if (used + 1 >= cap) goto fail;
        char length[32], *payload = strstr(reply, "\r\n\r\n");
        if (payload && !upnp_header(reply, "Content-Length", length, sizeof length)) {
            char *end; unsigned long count = strtoul(length, &end, 10);
            if (*end || count >= cap) goto fail;
            if (used >= (size_t)(payload + 4 - reply) + count) break;
        }
    }
    close(fd); reply[used] = 0;
    int success = !strncmp(reply, "HTTP/1.1 200 ", 13) || !strncmp(reply, "HTTP/1.0 200 ", 13);
    char *payload = strstr(reply, "\r\n\r\n"); if (!payload) return -1;
    char transfer[64];
    if (!upnp_header(reply, "Transfer-Encoding", transfer, sizeof transfer) && !strcasecmp(transfer, "chunked") && unchunk(payload + 4)) return -1;
    memmove(reply, payload + 4, strlen(payload + 4) + 1); return success ? 0 : -1;
fail: close(fd); return -1;
}
static int upnp_map(NatMap *m, int remove, const atomic_int *stop) {
    char args[1024], reply[8192], ip[INET_ADDRSTRLEN]; struct in_addr a = {.s_addr = htonl(m->local_ip)};
    inet_ntop(AF_INET, &a, ip, sizeof ip);
    if (remove) {
        // A lease may have expired and been reassigned since our successful Add.
        // Check the current entry before deleting; uncertainty leaves the finite lease to expire.
        snprintf(args, sizeof args, "<NewRemoteHost></NewRemoteHost><NewExternalPort>%u</NewExternalPort><NewProtocol>UDP</NewProtocol>", m->external_port);
        char client[64], port[32], description[128], expected_port[32];
        snprintf(expected_port, sizeof expected_port, "%u", m->internal_port);
        if (http(m->control, m->gateway, m->service, "GetSpecificPortMappingEntry", args, reply, sizeof reply, stop) ||
            upnp_xml(reply, "NewInternalClient", client, sizeof client) || strcmp(client, ip) ||
            upnp_xml(reply, "NewInternalPort", port, sizeof port) || strcmp(port, expected_port) ||
            upnp_xml(reply, "NewPortMappingDescription", description, sizeof description) || strcmp(description, "Online free roam")) return -1;
    }
    if (!remove) snprintf(args, sizeof args, "<NewRemoteHost></NewRemoteHost><NewExternalPort>%u</NewExternalPort>"
        "<NewProtocol>UDP</NewProtocol><NewInternalPort>%u</NewInternalPort><NewInternalClient>%s</NewInternalClient>"
        "<NewEnabled>1</NewEnabled><NewPortMappingDescription>Online free roam</NewPortMappingDescription>"
        "<NewLeaseDuration>%u</NewLeaseDuration>", m->external_port, m->internal_port, ip, 3600u);
    const char *action = remove ? "DeletePortMapping" : "AddPortMapping";
    reply[0] = 0;
    if (http(m->control, m->gateway, m->service, action, args, reply, sizeof reply, stop)) {
        return -1;
    }
    char expected[80]; snprintf(expected, sizeof expected, "%sResponse", action);
    if (!strstr(reply, expected) || strstr(reply, "Fault>")) return -1;
    // Only an acknowledged Add establishes ownership. Faults, cancellation and lost
    // replies cannot authorize deleting a possibly pre-existing forward.
    m->upnp_owned = !remove;
    m->lifetime = 3600; return 0;
}
static int discover_upnp(NatMap *m, const atomic_int *stop) {
    int fd = socket(AF_INET, SOCK_DGRAM, 0); if (fd < 0) return -1;
    struct in_addr iface = {.s_addr = htonl(m->local_ip)};
    setsockopt(fd, IPPROTO_IP, IP_MULTICAST_IF, &iface, sizeof iface);
    const char msg[] = "M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\nMX: 1\r\nST: urn:schemas-upnp-org:device:InternetGatewayDevice:1\r\n\r\n";
    struct sockaddr_in to = {.sin_family = AF_INET, .sin_port = htons(1900), .sin_addr.s_addr = inet_addr("239.255.255.250")};
    if (sendto(fd, msg, sizeof msg - 1, 0, (struct sockaddr *)&to, sizeof to) < 0) { close(fd); return -1; }
    uint64_t end = milliseconds() + 1400; char location[512] = "";
    while (!ready(fd, POLLIN, end, stop)) {
        char res[4096]; struct sockaddr_in from; socklen_t fl = sizeof from;
        ssize_t n = recvfrom(fd, res, sizeof res - 1, 0, (struct sockaddr *)&from, &fl);
        if (n <= 0 || ntohl(from.sin_addr.s_addr) != m->gateway) continue;
        res[n] = 0; Url u;
        if (!upnp_header(res, "location", location, sizeof location) && !url(location, m->gateway, &u)) break;
        location[0] = 0;
    }
    close(fd); if (!*location || stopped(stop)) return -1;
    char *xml = malloc(65536); if (!xml) return -1;
    char control[512], base[512]; int rc = -1;
    if (http(location, m->gateway, NULL, NULL, NULL, xml, 65536, stop) ||
        upnp_service(xml, m->service, sizeof m->service, control, sizeof control)) goto done;
    if (upnp_xml(xml, "URLBase", base, sizeof base)) snprintf(base, sizeof base, "%s", location);
    Url u; if (url(base, m->gateway, &u)) goto done;
    if (!strncmp(control, "http://", 7)) snprintf(m->control, sizeof m->control, "%s", control);
    else {
        char *path = strchr(base + 7, '/'); if (path) *path = 0;
        if (*control == '/') snprintf(m->control, sizeof m->control, "%s%s", base, control);
        else { char *dir = strrchr(u.path, '/'); if (dir) dir[1] = 0;
            snprintf(m->control, sizeof m->control, "%s%s%s", base, u.path, control); }
    }
    if (url(m->control, m->gateway, &u)) goto done;
    if (!http(m->control, m->gateway, m->service, "GetExternalIPAddress", "", xml, 65536, stop)) {
        char external[64]; struct in_addr a;
        if (!upnp_xml(xml, "NewExternalIPAddress", external, sizeof external) && inet_pton(AF_INET, external, &a) == 1)
            m->external_ip = ntohl(a.s_addr);
    }
    m->external_port = m->internal_port;
    m->protocol = 3;
    if (!upnp_map(m, 0, stop)) { m->mapped = 1; rc = 0; }
done: free(xml); return rc;
}
static uint32_t local_address(void) {
    struct ifaddrs *all = NULL; uint32_t result = 0;
    if (getifaddrs(&all)) return 0;
    for (struct ifaddrs *p = all; p; p = p->ifa_next) {
        if (!p->ifa_addr || p->ifa_addr->sa_family != AF_INET || !(p->ifa_flags & IFF_UP) || (p->ifa_flags & IFF_LOOPBACK)) continue;
        uint32_t ip = ntohl(((struct sockaddr_in *)p->ifa_addr)->sin_addr.s_addr);
        if (ip && (ip >> 16) != 0xa9fe) { result = ip; break; }
    }
    freeifaddrs(all); return result;
}
int natmap_open(NatMap *m, uint16_t port, const atomic_int *stop) {
    memset(m, 0, sizeof *m); m->internal_port = m->external_port = port; m->gateway = gateway(); m->local_ip = local_address();
    if (!m->gateway || stopped(stop)) return -1;
    int fd = udp(m->gateway, 5351, &m->local_ip); if (fd < 0) return -1; close(fd);
    arc4random_buf(m->nonce, sizeof m->nonce);
    uint8_t req[12], res[64]; NatReply r = {0};
    int got = exchange(m, req, natpmp_request(req, 1, 0, 0, 0), res, sizeof res, stop);
    if (got >= 0 && !natpmp_response(res, (size_t)got, 1, 0, &r)) m->external_ip = r.ip;
    if (!stopped(stop) && !pmp_map(m, 3600, stop)) {
        m->protocol = 1; m->mapped = 1;
        if (port_ok(m->external_port) && m->external_ip) return 0;
        natmap_close(m);
    }
    m->external_port = port;
    if (!stopped(stop) && !pcp_map(m, 3600, stop)) {
        m->protocol = 2; m->mapped = 1;
        if (port_ok(m->external_port) && m->external_ip) return 0;
        natmap_close(m);
    }
    m->external_port = port;
    if (!stopped(stop) && !discover_upnp(m, stop)) return 0;
    return -1;
}
int natmap_renew(NatMap *m, const atomic_int *stop) {
    if (!m->mapped) return -1;
    uint32_t ip = m->external_ip; uint16_t port = m->external_port;
    int rc = m->protocol == 1 ? pmp_map(m, 3600, stop) : m->protocol == 2 ? pcp_map(m, 3600, stop) : upnp_map(m, 0, stop);
    if (!rc && m->protocol == 1) {
        uint8_t req[12], res[64]; NatReply r = {0};
        size_t n = natpmp_request(req, 1, 0, 0, 0);
        int got = exchange(m, req, n, res, sizeof res, stop);
        if (got < 0 || natpmp_response(res, (size_t)got, 1, 0, &r)) rc = -1;
        else m->external_ip = r.ip;
    } else if (!rc && m->protocol == 3) {
        char reply[8192], external[64]; struct in_addr a;
        if (http(m->control, m->gateway, m->service, "GetExternalIPAddress", "", reply, sizeof reply, stop) ||
            upnp_xml(reply, "NewExternalIPAddress", external, sizeof external) || inet_pton(AF_INET, external, &a) != 1) rc = -1;
        else m->external_ip = ntohl(a.s_addr);
    }
    // A changed endpoint invalidates the displayed code; the caller asks the player to Host again.
    if (rc || ip != m->external_ip || port != m->external_port) return -1;
    return 0;
}
void natmap_close(NatMap *m) {
    if (m->protocol == 3 ? !m->upnp_owned : !m->mapped) return;
    if (m->protocol == 1) pmp_map(m, 0, NULL);
    else if (m->protocol == 2) pcp_map(m, 0, NULL);
    else if (m->protocol == 3) upnp_map(m, 1, NULL);
    m->mapped = m->upnp_owned = 0;
}
