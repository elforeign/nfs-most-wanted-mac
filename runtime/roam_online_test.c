// Pure code/NAT fixtures and loopback embedded-relay proof; driven by roam_online_test.py.
#include "roam_online.h"
#include "natmap.h"
#include "roam_core.h"
#include <inttypes.h>
#include <arpa/inet.h>
#include <ctype.h>
#include <errno.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>
static int checks, failures;
#define CHECK(x) do { ++checks; if (!(x)) { ++failures; if (failures < 5) printf("FAIL line %d: %s\n", __LINE__, #x); } } while (0)
static void code_test(void) {
    for (uint32_t i = 1; i < 1001; ++i) for (int p = 0; p < 256; ++p) {
        uint32_t ip = i * 2654435761u, decoded = 0; uint16_t port = 0; char s[ROAM_CODE_SIZE];
        CHECK(!roam_code_encode(ip, (uint16_t)(47800 + p), s));
        CHECK(!roam_code_decode(s, &decoded, &port) && decoded == ip && port == 47800 + p);
    }
    char s[ROAM_CODE_SIZE]; uint32_t ip; uint16_t p;
    CHECK(!roam_code_encode(0xcb007105u, 47800, s));
    printf("code vector: %s\n", s); CHECK(!strcmp(s, "TE29-43A2W"));
    char lower[ROAM_CODE_SIZE]; for (int i = 0; i < ROAM_CODE_SIZE; ++i) lower[i] = (char)tolower((unsigned char)s[i]);
    CHECK(!roam_code_decode(lower, &ip, &p) && ip == 0xcb007105u && p == 47800);
    char compact[10]; memcpy(compact, s, 4); memcpy(compact + 4, s + 5, 6);
    CHECK(!roam_code_decode(compact, &ip, &p));
    CHECK(roam_code_room(ip, p) == roam_code_room(0xcb007105u, 47800));
    CHECK(roam_code_room(ip, p) != roam_code_room(ip, p + 1));
    const char *bad[] = {"", "2345", "2345-67899", "I234-5678", "O234-5678", "1234-5678", "0234-5678", "2222-2222", "23456-789", "2345_6789", "2345 6789", " 23456789", "23456789 ", "2345-67/9"};
    for (unsigned i = 0; i < sizeof bad / sizeof *bad; ++i) CHECK(roam_code_decode(bad[i], &ip, &p) < 0);
    const char *alphabet = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ";
    for (uint32_t sample = 1; sample <= 64; ++sample) {
        char original[ROAM_CODE_SIZE], compact_code[10];
        CHECK(!roam_code_encode(sample * 2654435761u, (uint16_t)(47800 + sample), original));
        memcpy(compact_code, original, 4); memcpy(compact_code + 4, original + 5, 6);
        for (int i = 0; i < 9; ++i) {
            char saved = compact_code[i];
            for (int j = 0; j < 32; ++j) if (alphabet[j] != saved) {
                compact_code[i] = alphabet[j];
                CHECK(roam_code_decode(compact_code, &ip, &p) < 0);
            }
            compact_code[i] = saved;
        }
        for (int i = 0; i < 8; ++i) if (compact_code[i] != compact_code[i+1]) {
            char swapped[10]; memcpy(swapped, compact_code, sizeof swapped);
            swapped[i] = compact_code[i+1]; swapped[i+1] = compact_code[i];
            CHECK(roam_code_decode(swapped, &ip, &p) < 0);
        }
    }
    CHECK(roam_code_encode(0, 47800, s) < 0); CHECK(roam_code_encode(1, 47799, s) < 0); CHECK(roam_code_encode(1, 48056, s) < 0);
}
static void pmp_test(void) {
    uint8_t b[12]; NatReply r = {0};
    // RFC 6886 section 3 public-address request; section 3.1 response, epoch 1, 203.0.113.5.
    const uint8_t address[] = {0,128,0,0,0,0,0,1,203,0,113,5};
    CHECK(natpmp_request(b, 1, 0, 0, 0) == 2 && b[0] == 0 && b[1] == 0);
    CHECK(!natpmp_response(address, sizeof address, 1, 0, &r) && r.ip == 0xcb007105u);
    // Section 3.3: UDP, reserved=0, internal 47800, external 47801, lifetime 3600.
    const uint8_t request[] = {0,1,0,0,0xba,0xb8,0xba,0xb9,0,0,0x0e,0x10};
    CHECK(natpmp_request(b, 0, 47800, 47801, 3600) == sizeof request && !memcmp(b, request, sizeof request));
    uint8_t response[] = {0,129,0,0,0,0,0,1,0xba,0xb8,0xba,0xb9,0,0,0x0e,0x10};
    CHECK(!natpmp_response(response, sizeof response, 0, 47800, &r) && r.port == 47801 && r.lifetime == 3600);
    CHECK(natpmp_response(response, 15, 0, 47800, &r) < 0);
    CHECK(natpmp_response(response, 16, 0, 1, &r) < 0);
    response[3] = 2; CHECK(natpmp_response(response, 16, 0, 47800, &r) < 0);
    response[3] = 0; response[0] = 2; CHECK(natpmp_response(response, 16, 0, 47800, &r) < 0);
    CHECK(natpmp_request(b, 0, 47800, 47801, 0) == 12 && !b[8] && !b[9] && !b[10] && !b[11]);
}
static void pcp_test(void) {
    const uint8_t nonce[12] = {0,1,2,3,4,5,6,7,8,9,10,11}; uint8_t b[60]; NatReply r;
    // RFC 6887 sections 7.1/11.1: complete 60-byte MAP; IPv4-mapped client 192.168.1.9.
    const uint8_t request[60] = {
        2,1,0,0,0,0,0x0e,0x10, 0,0,0,0,0,0,0,0, 0,0,255,255,192,168,1,9,
        0,1,2,3,4,5,6,7,8,9,10,11, 17,0,0,0,0xba,0xb8,0xba,0xb9,
        0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0};
    CHECK(pcp_request(b, 0xc0a80109u, 47800, 47801, 3600, nonce) == 60 && !memcmp(b, request, 60));
    uint8_t response[60]; memcpy(response, request, 60); response[1] = 129;
    memset(response + 8, 0, 16); response[54] = response[55] = 255; response[56] = 203; response[57] = 0; response[58] = 113; response[59] = 5;
    CHECK(!pcp_response(response, 60, 47800, nonce, &r) && r.ip == 0xcb007105u && r.port == 47801 && r.lifetime == 3600);
    CHECK(pcp_response(response, 59, 47800, nonce, &r) < 0);
    CHECK(pcp_response(response, 60, 47801, nonce, &r) < 0);
    response[24] = 1; CHECK(pcp_response(response, 60, 47800, nonce, &r) < 0); response[24] = 0;
    response[3] = 2; CHECK(pcp_response(response, 60, 47800, nonce, &r) < 0); response[3] = 0;
    response[36] = 6; CHECK(pcp_response(response, 60, 47800, nonce, &r) < 0); response[36] = 17;
    response[44] = 1; CHECK(pcp_response(response, 60, 47800, nonce, &r) < 0);
    pcp_request(b, 0xc0a80109u, 47800, 47801, 0, nonce); CHECK(!b[4] && !b[5] && !b[6] && !b[7] && !memcmp(b + 24, nonce, 12));
}
static void upnp_test(void) {
    const char *ssdp = "HTTP/1.1 200 OK\r\nCACHE-CONTROL: max-age=120\r\nLoCaTiOn: http://192.168.1.1:5000/root.xml\r\nST: urn:schemas-upnp-org:device:InternetGatewayDevice:1\r\n\r\n";
    char b[2048], service[128], control[128];
    CHECK(!upnp_header(ssdp, "location", b, sizeof b) && !strcmp(b, "http://192.168.1.1:5000/root.xml"));
    CHECK(upnp_header(ssdp, "location", b, 5) < 0); CHECK(upnp_header(ssdp, "missing", b, sizeof b) < 0);
    CHECK(upnp_header("HTTP/1.1 200 OK\r\n\r\nLocation: bad", "location", b, sizeof b) < 0);
    const char *xml = "<root><device><serviceList><service><serviceType>urn:schemas-upnp-org:service:WANCommonInterfaceConfig:1</serviceType><controlURL>/wrong</controlURL></service>"
        "<service><serviceType>urn:schemas-upnp-org:service:WANIPConnection:1</serviceType><controlURL>/ctl/IPConn?x=1&amp;y=2</controlURL></service></serviceList></device></root>";
    CHECK(!upnp_service(xml, service, sizeof service, control, sizeof control) && !strcmp(control, "/ctl/IPConn?x=1&y=2"));
    CHECK(!strcmp(service, "urn:schemas-upnp-org:service:WANIPConnection:1"));
    const char *reply = "<s:Envelope xmlns:s=\"http://schemas.xmlsoap.org/soap/envelope/\"><s:Body>"
        "<u:GetExternalIPAddressResponse xmlns:u=\"urn:schemas-upnp-org:service:WANIPConnection:1\">"
        "<NewExternalIPAddress>203.0.113.5</NewExternalIPAddress></u:GetExternalIPAddressResponse></s:Body></s:Envelope>";
    CHECK(!upnp_xml(reply, "NewExternalIPAddress", b, sizeof b) && !strcmp(b, "203.0.113.5"));
    CHECK(!upnp_xml("<u:NewExternalIPAddress> 203.0.113.5 </u:NewExternalIPAddress>", "NewExternalIPAddress", b, sizeof b));
    CHECK(upnp_xml("<NewExternalIPAddress>bad</wrong>", "NewExternalIPAddress", b, sizeof b) < 0);
    CHECK(upnp_xml("<x>&evil;</x>", "x", b, sizeof b) < 0);
    CHECK(upnp_service("<service><serviceType>bad</serviceType><controlURL>/bad</controlURL></service>", service, sizeof service, control, sizeof control) < 0);
    CHECK(upnp_soap(b, sizeof b, "urn:schemas-upnp-org:service:WANIPConnection:1", "DeletePortMapping", "<NewExternalPort>47800</NewExternalPort><NewProtocol>UDP</NewProtocol>") > 0 && strstr(b, "</u:DeletePortMapping>"));
    CHECK(upnp_soap(b, 10, "x", "x", "") < 0);
    CHECK(upnp_soap(b, sizeof b, "x\"<", "x", "") < 0);
}
static int receive_client(int fd, RoamClient *c) {
    struct pollfd p = {.fd = fd, .events = POLLIN}; int packets = 0;
    while (poll(&p, 1, 100) > 0) {
        uint8_t b[2048]; int n = (int)recv(fd, b, sizeof b, 0); if (n <= 0) break;
        roam_client_input(c, b, n, 1000); ++packets;
        if (c->rx_entries) break;
    }
    return packets;
}
static int relay_test(void) {
    RoamRelay *r = roam_relay_new(-1, 2, 4); CHECK(r != NULL); if (!r) return 1;
    RoamClient c[2]; uint8_t b[1200];
    struct sockaddr_in from[2] = {{.sin_family=AF_INET, .sin_port=htons(10001), .sin_addr.s_addr=htonl(INADDR_LOOPBACK)},
                                {.sin_family=AF_INET, .sin_port=htons(10002), .sin_addr.s_addr=htonl(INADDR_LOOPBACK)}};
    for (int i = 0; i < 2; ++i) {
        roam_client_init(&c[i], 42, "test"); int n = roam_client_hello(&c[i], b); roam_relay_input(r, b, n, &from[i], 1000); c[i].id = (uint16_t)(i + 1);
        RoamState st = {.t=1000, .pos={1,2,3}, .q={0,0,0,32767}, .flags=ROAM_FLAG_CAR};
        n = roam_client_state(&c[i], &st, b); roam_relay_input(r, b, n, &from[i], 1000);
    }
    roam_relay_tick(r, 1000); RoamRelayStats stats = roam_relay_stats(r);
    printf("relay pure: clients=%" PRIu64 " tx=%" PRIu64 " (expected 2,8 incl. two names)\n", stats.clients, stats.tx);
    CHECK(stats.clients == 2 && stats.tx == 8); roam_relay_free(r);
    if (failures) return 1;
    // No test ever requests INADDR_ANY. No router calls, multicast or public traffic.
    for (int pass = 0; pass < 2; ++pass) {
        RoamServer *s = roam_server_start(INADDR_LOOPBACK, 0);
        if (!s && (errno == EPERM || errno == EACCES)) { puts("PENDING host run: embedded relay loopback sockets denied"); return 3; }
        CHECK(s != NULL); if (!s) return 1;
        int fds[2] = {-1,-1};
#define NET_CHECK(x) do { int ok = !!(x); if (!ok && (errno == EPERM || errno == EACCES)) goto socket_pending; CHECK(ok); } while (0)
        atomic_int stop = 0; NET_CHECK(roam_server_probe(INADDR_LOOPBACK, roam_server_port(s), &stop));
        for (int i = 0; i < 2; ++i) {
            fds[i] = socket(AF_INET, SOCK_DGRAM, 0); NET_CHECK(fds[i] >= 0); if (fds[i] < 0) break;
            struct sockaddr_in local = {.sin_family=AF_INET, .sin_addr.s_addr=htonl(INADDR_LOOPBACK)};
            NET_CHECK(!bind(fds[i], (struct sockaddr *)&local, sizeof local));
            struct sockaddr_in to = {.sin_family=AF_INET, .sin_port=htons(roam_server_port(s)), .sin_addr.s_addr=htonl(INADDR_LOOPBACK)};
            NET_CHECK(!connect(fds[i], (struct sockaddr *)&to, sizeof to));
            roam_client_init(&c[i], 42, "test"); int n = roam_client_hello(&c[i], b); NET_CHECK(send(fds[i], b, (size_t)n, 0) == n);
            receive_client(fds[i], &c[i]); CHECK(c[i].welcomed);
        }
        for (int i = 0; i < 2; ++i) {
            RoamState st = {.t=1000, .pos={10,20,30}, .q={0,0,0,32767}, .flags=ROAM_FLAG_CAR};
            st.pos[0] += i;
            int n = roam_client_state(&c[i], &st, b); NET_CHECK(send(fds[i], b, (size_t)n, 0) == n);
        }
        for (int i = 0; i < 2; ++i) { receive_client(fds[i], &c[i]); CHECK(c[i].rx_entries > 0);
            RoamRemote *remote = roam_view_find(&c[i].view, c[1-i].id);
            CHECK(remote && remote->n && remote->s[0].pos[0] == 11 - i);
            if (fds[i] >= 0) { close(fds[i]); fds[i] = -1; } }
        uint16_t port = roam_server_port(s); roam_server_stop(s);
        // Stopping releases the socket; restart on the exact port.
        s = roam_server_start(INADDR_LOOPBACK, port); CHECK(s != NULL); roam_server_stop(s);
        continue;
socket_pending:
        for (int i = 0; i < 2; ++i) if (fds[i] >= 0) close(fds[i]);
        roam_server_stop(s); puts("PENDING host run: embedded relay loopback sockets denied"); return 3;
#undef NET_CHECK
    }
    puts("relay socket: two clients exchanged state; 4 start/stop cycles"); return failures ? 1 : 0;
}
int main(int argc, char **argv) {
    if (argc != 2) return 2;
    int rc = 0;
    if (!strcmp(argv[1], "code")) code_test();
    else if (!strcmp(argv[1], "natpmp")) pmp_test();
    else if (!strcmp(argv[1], "pcp")) pcp_test();
    else if (!strcmp(argv[1], "upnp")) upnp_test();
    else if (!strcmp(argv[1], "relay")) rc = relay_test();
    else return 2;
    printf("%s: %d checks, %d failures\n", argv[1], checks, failures);
    return failures ? 1 : rc;
}
