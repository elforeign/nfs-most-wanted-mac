// Eight endpoint symbols plus one GF(32) checksum symbol. No ambiguous letters.
#include "roam_online.h"
#include <ctype.h>
#include <stddef.h>
#include <string.h>
static const char alphabet[] = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ";
_Static_assert(sizeof alphabet == 33, "base32 alphabet");
// Multiply by x in GF(32), modulo x^5 + x^2 + 1. Nonzero weights detect
// every single-symbol substitution and adjacent transposition of distinct symbols.
static unsigned twice(unsigned v) { return ((v << 1) ^ ((v & 16) ? 0x25u : 0)) & 31; }
int roam_code_encode(uint32_t ip, uint16_t port, char out[ROAM_CODE_SIZE]) {
    if (!ip || port < ROAM_CODE_PORT || port > ROAM_CODE_PORT + 255) return -1;
    uint64_t v = ((uint64_t)ip << 8) | (port - ROAM_CODE_PORT);
    unsigned symbols[8], check = 0;
    for (int i = 7; i >= 0; --i) { symbols[i] = v & 31; v >>= 5; }
    for (int i = 0; i < 8; ++i) {
        check = twice(check) ^ symbols[i];
        out[i + (i >= 4)] = alphabet[symbols[i]];
    }
    out[4] = '-'; out[9] = alphabet[twice(check)]; out[10] = 0;
    return 0;
}
int roam_code_decode(const char *code, uint32_t *ip, uint16_t *port) {
    if (!code) return -1;
    size_t len = strlen(code);
    if (len != 9 && len != 10) return -1;
    uint64_t v = 0; unsigned check = 0; int count = 0;
    for (size_t i = 0; i < len; ++i) {
        if (len == 10 && i == 4) { if (code[i] != '-') return -1; continue; }
        const char *p = strchr(alphabet, toupper((unsigned char)code[i]));
        if (!p || !*p) return -1;
        unsigned symbol = (unsigned)(p - alphabet);
        check = twice(check) ^ symbol;
        if (count < 8) v = (v << 5) | symbol;
        ++count;
    }
    if (count != 9 || check || !(v >> 8)) return -1;
    *ip = (uint32_t)(v >> 8); *port = (uint16_t)(ROAM_CODE_PORT + (v & 255)); return 0;
}
uint32_t roam_code_room(uint32_t ip, uint16_t port) {
    char code[ROAM_CODE_SIZE]; uint32_t h = 2166136261u;
    if (roam_code_encode(ip, port, code)) return 0;
    for (int i = 0; code[i]; ++i) h = (h ^ (unsigned char)code[i]) * 16777619u;
    return h;
}
