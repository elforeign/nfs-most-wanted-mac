// R- prefix, eight version/room symbols and one GF(32) checksum.
#include "roam_online.h"
#include <ctype.h>
#include <stddef.h>
#include <string.h>
static const char alphabet[] = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ";
_Static_assert(sizeof alphabet == 33, "base32 alphabet");
// Multiply by x in GF(32), modulo x^5 + x^2 + 1. Nonzero weights detect
// every single-symbol substitution and adjacent transposition of distinct symbols.
static unsigned twice(unsigned v) { return ((v << 1) ^ ((v & 16) ? 0x25u : 0)) & 31; }
int roam_code_encode(uint32_t room, char out[ROAM_CODE_SIZE]) {
    if (!room || !out) return -1;
    uint64_t v = ((uint64_t)ROAM_CODE_VERSION << 32) | room;
    unsigned symbols[8], check = 0;
    for (int i = 7; i >= 0; --i) { symbols[i] = v & 31; v >>= 5; }
    out[0] = 'R'; out[1] = '-';
    for (int i = 0; i < 8; ++i) {
        check = twice(check) ^ symbols[i];
        out[2 + i + (i >= 4)] = alphabet[symbols[i]];
    }
    out[6] = '-'; out[11] = alphabet[twice(check)]; out[12] = 0;
    return 0;
}
int roam_code_decode(const char *code, uint32_t *room) {
    if (!code || !room || strlen(code) != ROAM_CODE_SIZE - 1 ||
        toupper((unsigned char)code[0]) != 'R' || code[1] != '-' || code[6] != '-') return -1;
    uint64_t v = 0; unsigned check = 0; int count = 0;
    for (int i = 2; i < ROAM_CODE_SIZE - 1; ++i) {
        if (i == 6) continue;
        const char *p = strchr(alphabet, toupper((unsigned char)code[i]));
        if (!p || !*p) return -1;
        unsigned symbol = (unsigned)(p - alphabet);
        check = twice(check) ^ symbol;
        if (count < 8) v = (v << 5) | symbol;
        ++count;
    }
    if (count != 9 || check || (v >> 32) != ROAM_CODE_VERSION || !(uint32_t)v) return -1;
    *room = (uint32_t)v; return 0;
}
