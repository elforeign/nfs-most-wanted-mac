// huff.h - the game's HUFF decompressor natively, for vinyls.m (huff_test.py checks it against the game's own x86 code
// on every HUFF blob of the X360 vinyl packs).
#pragma once
#include <stdint.h>
#include <string.h>

// HUFF_decode (0064DCA0, behind LZDecompress's "HUFF" case 006500F0) natively. Container: "HUFF", 01, 16-byte header
// with the unpacked size at +8; then EA's Huffman-with-runs stream, read MSB first:
// - type (16 bits): 0x8000 = 32-bit sizes (else 24), 0x100 = a packed-size field to skip, 0x32FB / 0x34FB = the output
//   is a running sum / a running sum of a running sum (cleared 0x100 compared, so 0x33FB etc. count too);
//   then the unpacked size and the "clue" byte (8 bits), the escape symbol;
// - code lengths: per length 1, 2, ... a count (num below) until the canonical code space is full;
// - the symbols in code order, each as num + 1 = how many unused byte values to step forward (cyclically, from the
//   last symbol; the first step starts after 0xFF);
// - then codes. A decoded clue is followed by num r: r > 0 repeats the last output byte r times; r = 0 is followed by
//   one bit: 1 ends the stream, 0 is a literal byte (8 bits).
// num: "1xx" = 0..3; else k zero bits, a one, then n bits, value = bits + 2^n - 4, n = k + 2. The game counts runs of
// 16+ zeros bit by bit and then reads one more bit (n = k + 3); the encoder's numbers never need that many zeros
// (k >= 16 would mean a value over 256k), but it's mirrored. The game does no bounds checks (it trusts the stream);
// here input, output, tables and symbol indices are bounded, and anything the game would have done out of bounds
// returns 0. Returns the unpacked size on success.

typedef struct { const uint8_t *in; uint32_t len, pos; uint64_t buf; int n; } HuffBits;   // pos: next byte; n: bits in buf

static inline void huffb_refill(HuffBits *b) {               // to >= 56 bits; bits below the n valid ones are stream bits or 0
    if (b->n >= 56) return;
    if (b->pos + 8 <= b->len) {
        uint64_t w;
        memcpy(&w, b->in + b->pos, 8);
        b->buf |= __builtin_bswap64(w) >> b->n;
        b->pos += (63 - b->n) >> 3;
        b->n |= 56;
    } else
        while (b->n <= 56) {                                 // the tail reads as zero bits (an overrun is checked at the end)
            if (b->pos < b->len) b->buf |= (uint64_t)b->in[b->pos] << (56 - b->n);
            ++b->pos; b->n += 8;
        }
}
static inline uint32_t huffb_get(HuffBits *b, int k) {        // 1 <= k <= 32
    huffb_refill(b);
    uint32_t v = (uint32_t)(b->buf >> (64 - k));
    b->buf <<= k; b->n -= k;
    return v;
}
static inline int huff_num(HuffBits *b, uint32_t *v) {
    huffb_refill(b);
    if (b->buf >> 63) { *v = huffb_get(b, 3) - 4; return 1; }
    uint32_t k = 0;
    for (;;) {                                               // count the zeros before the first one
        huffb_refill(b);
        int z = b->buf ? __builtin_clzll(b->buf) : 64;
        if (z < 32) { k += (uint32_t)z; b->buf <<= z + 1; b->n -= z + 1; break; }
        k += 32; b->buf <<= 32; b->n -= 32;
        if (k > 32) return 0;
    }
    uint32_t n = k + 2 + (k >= 16);
    if (n > 31) return 0;
    *v = huffb_get(b, (int)n) - 4 + (1u << n);
    return 1;
}

#define HUFF_TB 11                                           // first-level table: codes up to 11 bits in one lookup

static inline uint32_t huff_native(const uint8_t *in, uint32_t inlen, uint8_t *out, uint32_t cap) {
    if (inlen < 0x14 || memcmp(in, "HUFF", 4) || in[4] != 1) return 0;
    uint32_t usize;
    memcpy(&usize, in + 8, 4);
    if (usize > cap) return 0;
    HuffBits b = {in, inlen, 16, 0, 0};
    uint32_t type = huffb_get(&b, 16), ulen;
    if (type & 0x8000) { if (type & 0x100) huffb_get(&b, 32); ulen = huffb_get(&b, 32); }
    else { if (type & 0x100) huffb_get(&b, 24); ulen = huffb_get(&b, 24); }
    type &= ~0x100u;
    uint32_t clue = huffb_get(&b, 8);

    // canonical code: base[l] = first code of length l - symbols before it; bound[l] = the end of the length-l codes
    // left-aligned to 16 bits (0 for an empty length, all ones for the longest), as the game's tables
    uint32_t cnt[18], base[18], bound[18], code = 0, nsym = 0, c, len = 1;
    do {
        if (len > 16) return 0;
        base[len] = code * 2 - nsym;
        if (!huff_num(&b, &c)) return 0;
        cnt[len] = c;
        code = code * 2 + c; nsym += c;
        bound[len] = c ? (code << (16 - len)) & 0xffff : 0;
        ++len;
    } while (c == 0 || bound[len - 1] != 0);
    uint32_t maxlen = len - 1;
    bound[maxlen] = 0xffffffffu;
    if (nsym > 256) return 0;

    uint8_t used[256] = {0}, sym[256];
    uint32_t last = 0xff, unused = 256;
    for (uint32_t i = 0; i < nsym; ++i) {
        uint32_t d;
        if (!huff_num(&b, &d) || d == 0xffffffffu || !unused) return 0;
        d = d % unused + 1;                                  // the game steps d + 1 unused values, wrapping
        do { last = (last + 1) & 0xff; if (!used[last]) --d; } while (d);
        used[last] = 1; --unused;
        sym[i] = (uint8_t)last;
    }

    uint16_t tab[1 << HUFF_TB];                              // sym | len << 8 | 0x1000 for the clue; 0 = longer code
    memset(tab, 0, sizeof tab);
    uint32_t pos = 0, si = 0;
    for (uint32_t l = 1; l <= maxlen && l <= HUFF_TB; ++l)
        for (uint32_t j = 0; j < cnt[l]; ++j, ++si) {
            uint32_t span = 1u << (HUFF_TB - l);
            if (pos + span > (1u << HUFF_TB)) return 0;
            uint16_t e = (uint16_t)(sym[si] | l << 8 | (sym[si] == clue ? 0x1000 : 0));
            for (uint32_t k = 0; k < span; ++k) tab[pos + k] = e;
            pos += span;
        }

    uint8_t *o = out, *end = out + usize;
    for (;;) {
        huffb_refill(&b);
        uint32_t e = tab[b.buf >> (64 - HUFF_TB)], s, l;
        if (e & 0xf00) {
            l = e >> 8 & 0xf; s = e & 0xff;
            if (!(e & 0x1000)) {
                if (o == end) return 0;
                *o++ = (uint8_t)s;
                b.buf <<= l; b.n -= (int)l;
                continue;
            }
        } else {
            uint32_t t = (uint32_t)(b.buf >> 48);
            for (l = HUFF_TB + 1; l <= maxlen && bound[l] <= t; ++l) {}
            if (l > maxlen) return 0;
            uint32_t idx = (uint32_t)(b.buf >> (64 - l)) - base[l];
            if (idx >= nsym) return 0;
            s = sym[idx];
        }
        b.buf <<= l; b.n -= (int)l;
        if (s != clue) {
            if (o == end) return 0;
            *o++ = (uint8_t)s;
            continue;
        }
        uint32_t r;
        if (!huff_num(&b, &r)) return 0;
        if (r == 0) {
            if (huffb_get(&b, 1)) break;
            if (o == end) return 0;
            *o++ = (uint8_t)huffb_get(&b, 8);
        } else {
            if (o == out || r > (uint32_t)(end - o)) return 0;
            memset(o, o[-1], r);
            o += r;
        }
    }
    if ((uint64_t)b.pos * 8 - (uint64_t)b.n > (uint64_t)inlen * 8 || o != end || ulen > usize) return 0;
    if (type == 0x32fb || type == 0xb2fb) {
        uint8_t s1 = 0;
        for (uint32_t i = 0; i < ulen; ++i) out[i] = s1 = (uint8_t)(s1 + out[i]);
    } else if (type == 0x34fb || type == 0xb4fb) {
        uint8_t s1 = 0, s2 = 0;
        for (uint32_t i = 0; i < ulen; ++i) { s1 = (uint8_t)(s1 + out[i]); out[i] = s2 = (uint8_t)(s2 + s1); }
    }
    return usize;
}
