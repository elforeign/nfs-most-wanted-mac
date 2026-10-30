// PCP MAP wire codec (RFC 6887), IPv4-mapped addresses and UDP protocol 17.
#include "natmap.h"
#include <string.h>
static void p16(uint8_t *p, uint16_t v) { p[0] = v >> 8; p[1] = v; }
static void p32(uint8_t *p, uint32_t v) { p[0] = v >> 24; p[1] = v >> 16; p[2] = v >> 8; p[3] = v; }
static uint32_t g32(const uint8_t *p) { return (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 | p[2] << 8 | p[3]; }
size_t pcp_request(uint8_t b[60], uint32_t local, uint16_t in, uint16_t out, uint32_t life, const uint8_t nonce[12]) {
    memset(b, 0, 60); b[0] = 2; b[1] = 1; p32(b + 4, life);
    b[18] = b[19] = 255; p32(b + 20, local); memcpy(b + 24, nonce, 12);
    b[36] = 17; p16(b + 40, in); p16(b + 42, out); return 60;
}
int pcp_response(const uint8_t *b, size_t n, uint16_t in, const uint8_t nonce[12], NatReply *r) {
    if (n < 60 || b[0] != 2 || b[1] != 129 || b[3] || memcmp(b + 24, nonce, 12) || b[36] != 17 ||
        ((unsigned)b[40] << 8 | b[41]) != in) return -1;
    for (int i = 44; i < 54; ++i) if (b[i]) return -1;
    if (b[54] != 255 || b[55] != 255) return -1;
    r->ip = g32(b + 56); r->port = (uint16_t)(b[42] * 256u + b[43]); r->lifetime = g32(b + 4); return 0;
}
