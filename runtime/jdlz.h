// jdlz.h - the game's JDLZ decompressor natively, for vinyls.m (jdlz_test.c checks it on every X360 vinyl pack blob).
#pragma once
#include <stdint.h>
#include <string.h>

// JLZDecompress (0064DB40, "JDLZ" version 2) natively: 16-byte header (magic, version, unpacked size at +8,
// packed size at +0xC), then two flag bytes (literal/match, match kind), each refilled from the stream when its
// sentinel bit shifts down to 1. Match kind 0: length (b0 & 0x1F) + 3, distance ((b0 & 0xE0) << 3 | b1) + 0x11; kind 1:
// length ((b0 & 0xF0) << 4 | b1) + 3, distance (b0 & 0xF) + 1. The game bounds literals by the unpacked size only;
// here matches are bounded too (cap). Returns the unpacked size, or 0 (not JDLZ, or a stream that overruns).
static inline uint32_t jdlz_native(const uint8_t *in, uint32_t inlen, uint8_t *out, uint32_t cap) {
    if (inlen < 0x12 || memcmp(in, "JDLZ", 4) || in[4] != 2) return 0;
    uint32_t usize, csize;
    memcpy(&usize, in + 8, 4); memcpy(&csize, in + 0xc, 4);
    if (usize > cap || csize > inlen) return 0;
    const uint8_t *p = in + 0x12;
    uint8_t *o = out, *end = out + usize;
    int64_t n = (int64_t)csize - 0x12;
    uint32_t f1 = in[0x10] | 0x100, f2 = in[0x11] | 0x100;
    while (n > 0) {
        if (!(f1 & 1)) {
            if (o < end) *o++ = *p;
            ++p; --n;
        } else {
            uint32_t b0 = p[0], b1 = p[1], len, dist;
            if (!(f2 & 1)) { len = (b0 & 0x1f) + 3; dist = ((b0 & 0xe0) << 3 | b1) + 0x11; }
            else { len = ((b0 & 0xf0) << 4 | b1) + 3; dist = (b0 & 0xf) + 1; }
            if (dist > (uint32_t)(o - out) || len > (uint32_t)(end - o)) return 0;
            // the game copies byte by byte, so an overlapping match repeats its last `dist` bytes: copy in chunks that
            // never overlap their source (dist, 2*dist, 4*dist, ... bytes; the source stays periodic with period dist)
            const uint8_t *s = o - dist;
            for (uint32_t done = 0; done < len;) {
                uint32_t chunk = dist + done < len - done ? dist + done : len - done;
                memcpy(o + done, s, chunk);
                done += chunk;
            }
            o += len;
            f2 >>= 1;
            p += 2; n -= 2;
        }
        f1 >>= 1;
        if (f1 == 1) { f1 = *p++ | 0x100; --n; }
        if (f2 == 1) { f2 = *p++ | 0x100; --n; }
    }
    return n == 0 ? usize : 0;
}
