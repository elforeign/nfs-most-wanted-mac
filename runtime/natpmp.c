// NAT-PMP wire codec (RFC 6886): network byte order, UDP mappings only.
#include "natmap.h"
#include <string.h>
static void put16(uint8_t *p, uint16_t v) { p[0] = v >> 8; p[1] = v; }
static void put32(uint8_t *p, uint32_t v) { p[0] = v >> 24; p[1] = v >> 16; p[2] = v >> 8; p[3] = v; }
static uint16_t get16(const uint8_t *p) { return (uint16_t)(p[0] * 256u + p[1]); }
static uint32_t get32(const uint8_t *p) { return (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 | p[2] << 8 | p[3]; }
size_t natpmp_request(uint8_t b[12], int address, uint16_t in, uint16_t out, uint32_t life) {
    memset(b, 0, 12); if (address) return 2;
    b[1] = 1; put16(b + 4, in); put16(b + 6, out); put32(b + 8, life); return 12;
}
int natpmp_response(const uint8_t *b, size_t n, int address, uint16_t in, NatReply *r) {
    if (n != (address ? 12u : 16u) || b[0] || b[1] != (address ? 128 : 129) || get16(b + 2)) return -1;
    if (address) r->ip = get32(b + 8);
    else { if (get16(b + 8) != in) return -1; r->port = get16(b + 10); r->lifetime = get32(b + 12); }
    return 0;
}
