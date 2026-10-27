// xex_read.c - reads bytes of the player's X360 executable (default.xex, XEX2) at a virtual address, for
// tools_snapshot/pubtables_gen.py. Decrypts the image (AES-128-CBC, the file key unwrapped with the retail XEX key;
// CommonCrypto), undoes the "normal" compression (block chain checked by SHA-1, then one LZX stream), checks that the
// whole decoded image is the expected build (SHA-256) and writes the requested bytes to stdout. Nothing is written
// beside the input.
//   usage: xex_read <default.xex> <virtual address> <length> <title id> <image SHA-256 hex>
// The input is untrusted: every header offset, block, chunk, LZX length and match is bounds-checked; anything else
// (another title, another build, a damaged or crafted file) exits 1 with a message and writes nothing.
// Negative tests: tools_snapshot/test_xex_read.py (crafted files, built with -fsanitize=address,undefined).
#include <CommonCrypto/CommonCrypto.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

static void die(const char *m) { fprintf(stderr, "xex_read: %s\n", m); exit(1); }
static uint32_t be32(const uint8_t *p) { return (uint32_t)p[0] << 24 | p[1] << 16 | p[2] << 8 | p[3]; }
static uint16_t be16(const uint8_t *p) { return (uint16_t)(p[0] << 8 | p[1]); }
static uint32_t le32(const uint8_t *p) { return (uint32_t)p[0] | p[1] << 8 | p[2] << 16 | (uint32_t)p[3] << 24; }

// ---- LZX (the Cabinet variant: 3-bit block type, 24-bit block size, 32 KB frames realigned to 16 bits) ----
typedef struct {
    const uint8_t *src;
    size_t i, n;                                   // next unread byte, input size (reads past n are refused)
    uint32_t buf; int left;
} Bits;
static void ensure(Bits *b, int n) {
    while (b->left < n) {
        if (b->i + 2 > b->n) die("LZX: input truncated");
        uint32_t w = (uint32_t)(b->src[b->i + 1] << 8 | b->src[b->i]);
        b->i += 2;
        b->buf |= w << (16 - b->left);
        b->left += 16;
    }
}
static uint32_t peek(Bits *b, int n) { ensure(b, n); return n ? b->buf >> (32 - n) : 0; }
static void drop(Bits *b, int n) { b->buf <<= n; b->left -= n; }
static uint32_t bits(Bits *b, int n) { uint32_t v = peek(b, n); drop(b, n); return v; }

#define TB 16
typedef struct { uint16_t sym[1 << TB]; uint8_t len[1 << TB]; } Tree;
static int build(Tree *t, const uint8_t *lens, int n) {   // canonical Huffman; -1 over-subscribed, 0 empty
    memset(t->len, 0, sizeof t->len);
    uint32_t code = 0; int any = 0;
    for (int l = 1; l <= 16; ++l) {
        for (int s = 0; s < n; ++s) {
            if (lens[s] != l) continue;
            any = 1;
            uint32_t lo = code << (TB - l), hi = (code + 1) << (TB - l);
            if (hi > (1u << TB)) return -1;
            for (uint32_t i = lo; i < hi; ++i) { t->sym[i] = (uint16_t)s; t->len[i] = (uint8_t)l; }
            ++code;
        }
        code <<= 1;
    }
    return any;
}
static int decode(Bits *b, const Tree *t) {
    uint32_t v = peek(b, TB);
    if (!t->len[v]) die("LZX: bad Huffman code");
    drop(b, t->len[v]);
    return t->sym[v];
}
static void read_lens(Bits *b, Tree *pt, uint8_t *lens, int first, int last) {
    uint8_t pl[20];
    for (int i = 0; i < 20; ++i) pl[i] = (uint8_t)bits(b, 4);
    if (build(pt, pl, 20) <= 0) die("LZX: bad pretree");
    for (int x = first; x < last;) {
        int z = decode(b, pt), y;
        if (z == 17) { y = (int)bits(b, 4) + 4; while (y-- && x < last) lens[x++] = 0; }
        else if (z == 18) { y = (int)bits(b, 5) + 20; while (y-- && x < last) lens[x++] = 0; }
        else if (z == 19) {
            y = (int)bits(b, 1) + 4;
            z = decode(b, pt);
            if (z > 16) die("LZX: bad tree length delta");
            z = lens[x] - z; if (z < 0) z += 17;
            while (y-- && x < last) lens[x++] = (uint8_t)z;
        } else { z = lens[x] - z; if (z < 0) z += 17; lens[x++] = (uint8_t)z; }
    }
}

// Decodes `want` bytes into out (capacity cap >= want); refuses anything that would read or write outside.
static void lzx(const uint8_t *src, size_t n, uint8_t *out, size_t want, size_t cap, int window_bits) {
    static uint32_t base[52]; static uint8_t extra[52];
    // extra: 0,0,0,0,1,1,2,2,...; base: 0,1,2,3,4,6,8,12,...
    for (int i = 0, j = 0; i < 52; i += 2) { extra[i] = extra[i + 1] = (uint8_t)j; if (i && j < 17) ++j; }
    base[0] = 0; for (int i = 1; i < 52; ++i) base[i] = base[i - 1] + (1u << extra[i - 1]);
    int slots = window_bits == 15 ? 30 : window_bits == 16 ? 32 : window_bits == 17 ? 34 : window_bits == 18 ? 36 :
                window_bits == 19 ? 38 : window_bits == 20 ? 42 : window_bits == 21 ? 50 : 0;
    if (!slots) die("LZX: unsupported window");
    if (want > cap) die("LZX: output larger than the image");
    int nmain = 256 + slots * 8;
    static uint8_t mlen[256 + 50 * 8], llen[249], alen[8];
    memset(mlen, 0, sizeof mlen); memset(llen, 0, sizeof llen);
    Tree *mt = malloc(sizeof *mt), *lt = malloc(sizeof *lt), *at = malloc(sizeof *at), *pt = malloc(sizeof *pt);
    if (!mt || !lt || !at || !pt) die("out of memory");
    int lt_ok = 0;
    Bits b = {src, 0, n, 0, 0};
    uint32_t r0 = 1, r1 = 1, r2 = 1;
    if (bits(&b, 1)) die("LZX: Intel E8 translation (not in an X360 executable)");
    size_t pos = 0, frame_end = 32768;
    int type = 0; uint32_t remaining = 0, blen = 0;
    while (pos < want) {
        if (pos == frame_end) {                    // frame boundary: the bit stream restarts on a 16-bit word
            if (b.left > 0) ensure(&b, 16);
            if (b.left & 15) drop(&b, b.left & 15);
            frame_end += 32768;
        }
        if (!remaining) {
            if (type == 3 && (blen & 1)) {         // uncompressed blocks are padded to an even length
                if (b.i + 1 > b.n) die("LZX: input truncated");
                b.i++;
            }
            type = (int)bits(&b, 3);
            uint32_t hi = bits(&b, 16), lo = bits(&b, 8);
            remaining = blen = hi << 8 | lo;
            if (!blen) die("LZX: empty block");
            if (type == 2) {
                for (int i = 0; i < 8; ++i) alen[i] = (uint8_t)bits(&b, 3);
                if (build(at, alen, 8) <= 0) die("LZX: bad aligned tree");
            }
            if (type == 1 || type == 2) {
                read_lens(&b, pt, mlen, 0, 256);
                read_lens(&b, pt, mlen, 256, nmain);
                if (build(mt, mlen, nmain) <= 0) die("LZX: bad main tree");
                read_lens(&b, pt, llen, 0, 249);
                int r = build(lt, llen, 249);
                if (r < 0) die("LZX: bad length tree");
                lt_ok = r > 0;
            } else if (type == 3) {
                ensure(&b, 16);
                if (b.left > 16) b.i -= 2;
                b.left = 0; b.buf = 0;
                if (b.i + 12 > b.n) die("LZX: input truncated");
                r0 = le32(src + b.i); r1 = le32(src + b.i + 4); r2 = le32(src + b.i + 8);
                b.i += 12;
            } else die("LZX: bad block type");
        }
        size_t run = remaining;
        if (run > frame_end - pos) run = frame_end - pos;
        if (run > want - pos) run = want - pos;
        size_t stop = pos + run;
        if (type == 3) {
            if (run > b.n - b.i) die("LZX: input truncated");
            memcpy(out + pos, src + b.i, run); b.i += run; pos += run; remaining -= (uint32_t)run;
            continue;
        }
        while (pos < stop) {
            int m = decode(&b, mt);
            if (m < 256) { out[pos++] = (uint8_t)m; --remaining; continue; }
            m -= 256;
            uint32_t len = m & 7, off = (uint32_t)m >> 3;
            if (len == 7) { if (!lt_ok) die("LZX: no length tree"); len += (uint32_t)decode(&b, lt); }
            len += 2;
            if (off > 2) {
                int e = extra[off];
                uint32_t o = base[off] - 2;
                if (type == 2 && e >= 3) {
                    if (e > 3) o += bits(&b, e - 3) << 3;
                    o += (uint32_t)decode(&b, at);
                } else if (e) o += bits(&b, e);
                r2 = r1; r1 = r0; r0 = o;
            } else if (off == 1) { uint32_t t = r1; r1 = r0; r0 = t; }
            else if (off == 2) { uint32_t t = r2; r2 = r0; r0 = t; }
            if (!r0 || r0 > pos) die("LZX: match before the start");
            if (len > remaining || len > frame_end - pos || len > cap - pos) die("LZX: match past the block, frame or image");
            for (uint32_t i = 0; i < len; ++i, ++pos) out[pos] = out[pos - r0];
            remaining -= len;
        }
    }
    free(mt); free(lt); free(at); free(pt);
}

int main(int argc, char **argv) {
    if (argc != 6) die("usage: xex_read <default.xex> <virtual address> <length> <title id> <image SHA-256 hex>");
    uint32_t va = (uint32_t)strtoul(argv[2], NULL, 0), want = (uint32_t)strtoul(argv[3], NULL, 0);
    uint32_t title = (uint32_t)strtoul(argv[4], NULL, 0);
    if (strlen(argv[5]) != 64) die("the expected SHA-256 is 64 hex digits");
    FILE *f = fopen(argv[1], "rb");
    if (!f) die("cannot open the executable");
    if (fseek(f, 0, SEEK_END)) die("cannot read the executable");
    long fl = ftell(f);
    if (fl < 0x200 || fl > (256L << 20)) die("not an XEX2 file (size)");
    fseek(f, 0, SEEK_SET);
    uint8_t *x = malloc((size_t)fl);
    if (!x || fread(x, 1, (size_t)fl, f) != (size_t)fl) die("read failed");
    fclose(f);
    size_t n = (size_t)fl;
#define NEED(off, len) do { if ((uint64_t)(off) + (uint64_t)(len) > n) die("header field past the end of the file"); } while (0)
    if (memcmp(x, "XEX2", 4)) die("not an XEX2 file");
    uint32_t pe = be32(x + 8), sec = be32(x + 0x10), nopt = be32(x + 0x14), ffi = 0, image_base = 0, exec = 0;
    if (nopt > 64) die("too many optional headers");
    NEED(0x18, 8 * nopt);
    for (uint32_t i = 0; i < nopt; ++i) {
        uint32_t k = be32(x + 0x18 + 8 * i), v = be32(x + 0x1C + 8 * i);
        if (k == 0x000003FF) ffi = v;
        if (k == 0x00010201) image_base = v;
        if (k == 0x00040006) exec = v;
    }
    if (!ffi || !image_base || !exec) die("no file format / base address / execution info header");
    NEED(exec, 0x18);
    if (be32(x + exec + 0x0C) != title) die("another title (title ID differs)");
    NEED(sec, 0x160);
    NEED(ffi, 0x24);
    if (pe >= n) die("image data offset past the end of the file");
    uint32_t image_size = be32(x + sec + 4);
    if (!image_size || image_size > (256u << 20)) die("bad image size");
    if (be32(x + ffi) < 0x24 || be16(x + ffi + 4) != 1 || be16(x + ffi + 6) != 2)
        die("not an encrypted, LZX-compressed executable");
    uint32_t window = be32(x + ffi + 8), first_size = be32(x + ffi + 12);
    const uint8_t *first_hash = x + ffi + 16;
    int wb = 0;
    while (wb < 22 && (1u << wb) != window) ++wb;
    if (wb < 15 || wb > 21) die("unsupported LZX window size");
    if (va < image_base || want > image_size || va - image_base > image_size - want) die("address outside the image");
    // the session key: the file key decrypted with the retail key (AES-128-ECB)
    static const uint8_t retail[16] = {0x20, 0xB1, 0x85, 0xA5, 0x9D, 0x28, 0xFD, 0xC3,
                                       0x40, 0x58, 0x3F, 0xBB, 0x08, 0x96, 0xBF, 0x91};
    uint8_t key[16]; size_t moved = 0;
    if (CCCrypt(kCCDecrypt, kCCAlgorithmAES128, kCCOptionECBMode, retail, 16, NULL, x + sec + 0x150, 16, key, 16, &moved)
        != kCCSuccess) die("AES failed");
    size_t dl = (n - pe) & ~(size_t)15;
    if (!dl) die("no image data");
    uint8_t *d = malloc(dl), iv[16] = {0};
    if (!d || CCCrypt(kCCDecrypt, kCCAlgorithmAES128, 0, key, 16, iv, x + pe, dl, d, dl, &moved) != kCCSuccess)
        die("AES failed");
    // block chain: {next size, next SHA-1, chunks {u16 size, data}... u16 0}; the hash covers the whole block
    uint8_t *comp = malloc(dl); size_t cn = 0;
    if (!comp) die("out of memory");
    uint32_t size = first_size; uint8_t hash[20]; memcpy(hash, first_hash, 20);
    size_t p = 0;
    while (size) {
        if (size < 26 || size > dl - p) die("block past the end of the data");
        uint8_t h[20]; CC_SHA1(d + p, size, h);
        if (memcmp(h, hash, 20)) die("block SHA-1 mismatch (not a retail executable, or damaged)");
        uint32_t next = be32(d + p); memcpy(hash, d + p + 4, 20);
        size_t q = p + 24, end = p + size;
        for (;;) {
            if (q + 2 > end) die("chunk header past the end of its block");
            uint32_t c = be16(d + q); q += 2;
            if (!c) break;
            if (c > end - q) die("chunk past the end of its block");
            memcpy(comp + cn, d + q, c); cn += c; q += c;   // cn <= q <= dl: chunks only shrink the data
        }
        p = end; size = next;
    }
    uint8_t *img = calloc(1, image_size);
    if (!img) die("out of memory");
    lzx(comp, cn, img, image_size, image_size, wb);   // the whole image: its hash identifies the build
    uint8_t sum[32]; char hex[65];
    CC_SHA256(img, image_size, sum);
    for (int i = 0; i < 32; ++i) snprintf(hex + 2 * i, 3, "%02x", sum[i]);
    if (strcasecmp(hex, argv[5])) {
        fprintf(stderr, "xex_read: image SHA-256 %s, expected %s: another build of the title\n", hex, argv[5]);
        return 1;
    }
    fwrite(img + (va - image_base), 1, want, stdout);
    return 0;
}
