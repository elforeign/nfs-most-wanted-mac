#pragma once
#include <stddef.h>
#include <stdint.h>
#include <stdatomic.h>
typedef struct {
    uint32_t ip, lifetime; uint16_t port;
} NatReply;
size_t natpmp_request(uint8_t out[12], int address, uint16_t inside, uint16_t outside, uint32_t life);
int natpmp_response(const uint8_t *b, size_t n, int address, uint16_t inside, NatReply *r);
size_t pcp_request(uint8_t out[60], uint32_t local_ip, uint16_t inside, uint16_t outside, uint32_t life, const uint8_t nonce[12]);
int pcp_response(const uint8_t *b, size_t n, uint16_t inside, const uint8_t nonce[12], NatReply *r);
int upnp_header(const char *http, const char *key, char *out, size_t n);
int upnp_xml(const char *xml, const char *key, char *out, size_t n);
int upnp_service(const char *xml, char *service, size_t sn, char *control, size_t cn);
int upnp_soap(char *out, size_t n, const char *service, const char *action, const char *args);
typedef struct {
    uint32_t gateway, local_ip, external_ip, lifetime;
    uint16_t internal_port, external_port;
    int protocol, mapped, upnp_owned;
    uint8_t nonce[12];
    char control[512], service[128];
} NatMap;
/* Only explicit Host calls these I/O functions. Tests replace router transport. */
int natmap_open(NatMap *m, uint16_t port, const atomic_int *stop);
int natmap_renew(NatMap *m, const atomic_int *stop);
void natmap_close(NatMap *m);
