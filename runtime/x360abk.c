// x360abk.c - X360 sound banks (A10, G3): ABKC container + S10A bank parsed, codec 3 (EA-wrapped XMA1) decoded to PCM16.
// ABKC (X360 825D7E90 / PC 0081AB36): typed big-endian fields on X360, no blanket swap: +0A uint16 module count,
// +14 file size, +1C module table, +20/+24 bank offset/extent. S10A (bank): "S10A", +4 reserved 0, +8 count, +0C count
// uint32 header offsets relative to the bank. Each header (825EA4C8, bit reader 825E9440, MSB first): version:4 codec:4
// channels-1:6 rate:18 | storage:2 loop:1 samples:29 [| loop start:32]. Resident data follows the header as blocks
// {uint32 block size, uint32 samples} {uint32 inner size<<2 | flags} then 2048-byte XMA1 packets (sequence nibble mod 16 within a block; the last packet may be short).
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <sys/stat.h>
#include <sys/xattr.h>
#include <CommonCrypto/CommonDigest.h>
#include <libavcodec/avcodec.h>
#include <libavutil/channel_layout.h>
#include <zlib.h>
#include "x360abk.h"
#include "roots.h"

static uint32_t rd32(const uint8_t *p) { return (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 | (uint32_t)p[2] << 8 | p[3]; }
static int fail(char *why, size_t n, const char *fmt, unsigned a, unsigned b) { snprintf(why, n, fmt, a, b); return 0; }

int x360abk_parse(const uint8_t *f, size_t size, X360Abk *o, char *why, size_t n) {
    memset(o, 0, sizeof *o);
    if (size < 0x60 || memcmp(f, "ABKC", 4)) return fail(why, n, "not ABKC", 0, 0);
    if (rd32(f + 0x14) != size) return fail(why, n, "ABKC size %u != file %u", rd32(f + 0x14), (unsigned)size);
    uint32_t bank = rd32(f + 0x20), ext = rd32(f + 0x24);
    if (!bank || bank < 0x60 || ext < 12 || ext > size - bank || memcmp(f + bank, "S10A", 4))
        return fail(why, n, "no S10A bank at %08X (extent %u)", bank, ext);
    unsigned cnt = rd32(f + bank + 8);
    if (rd32(f + bank + 4) || cnt > 512 || 12 + 4 * cnt > ext) return fail(why, n, "S10A count %u bad (extent %u)", cnt, ext);
    o->bank = bank; o->bank_size = ext; o->count = cnt;
    X360AbkSound *last = NULL;
    for (unsigned i = 0; i < cnt; ++i) {
        X360AbkSound *s = &o->s[i];
        uint32_t rel = rd32(f + bank + 12 + 4 * i);
        if (rel == 0xffffffffu) continue;   // empty slot (ENV_COMMON_MB's last three)
        if (rel < 12 + 4 * cnt || rel + 8 > ext) return fail(why, n, "sound %u header offset %08X outside bank", i, rel);
        const uint8_t *h = f + bank + rel;
        uint32_t a = rd32(h), b = rd32(h + 4);
        s->version = a >> 28; s->codec = a >> 24 & 15; s->channels = (a >> 18 & 63) + 1; s->rate = a & 0x3ffff;
        s->storage = b >> 30; s->loop = b >> 29 & 1; s->samples = b & 0x1fffffff;
        unsigned hs = 8;
        if (s->loop) { if (rel + 12 > ext) return fail(why, n, "sound %u loop field outside bank", i, 0); s->loop_start = rd32(h + 8); hs = 12; }
        s->hdr = bank + rel; s->data = s->hdr + hs;
        if (last && s->hdr <= last->hdr) return fail(why, n, "sound %u header not after sound %u", i, (unsigned)(last - o->s));
        if (last) last->end = s->hdr;
        last = s;
    }
    if (last) last->end = bank + ext;
    snprintf(why, n, "ok: S10A %u sounds", cnt);
    return 1;
}

// Walks the blocks of one resident sound and returns the packet payloads (stripped of the EA wrapping) in order.
static int collect(const uint8_t *f, const X360AbkSound *s, uint8_t **out, unsigned *npk, char *why, size_t n) {
    uint32_t pos = s->data, total = 0;
    unsigned pk = 0, cap = 64, seq = 0;
    uint8_t *buf = malloc(cap * 2048);
    while (total < s->samples) {
        if (pos + 12 > s->end) { free(buf); return fail(why, n, "block at %08X outside sound (%u samples so far)", pos, total); }
        uint32_t bsize = rd32(f + pos), bsamp = rd32(f + pos + 4), inner = rd32(f + pos + 8) >> 2;
        if (bsize < 12 + 4 || bsize > s->end - pos || !bsamp || bsamp > s->samples - total || inner + 8 != bsize)
            { free(buf); return fail(why, n, "bad block header at %08X (size %u)", pos, bsize); }
        // The last packet of a block may be cut short (half the X360 engine banks): zero-padded to 2048 bytes, the
        // padding lies after the block's last frame.
        for (uint32_t p = pos + 12; p < pos + bsize; p += 2048) {
            if (p != pos + 12 && (unsigned)(f[p] >> 4) != ((seq + 1) & 15)) {   // a block may restart the sequence
                free(buf); return fail(why, n, "packet %u sequence break at %08X", pk, p);
            }
            seq = f[p] >> 4;
            if (pk == cap) buf = realloc(buf, (cap *= 2) * 2048);
            uint32_t len = pos + bsize - p < 2048 ? pos + bsize - p : 2048;
            memcpy(buf + pk * 2048, f + p, len);
            memset(buf + pk++ * 2048 + len, 0, 2048 - len);
        }
        total += bsamp; pos += bsize;
    }
    *out = buf; *npk = pk;
    return 1;
}

int x360abk_decode(const uint8_t *f, const X360AbkSound *s, int16_t **pcm, unsigned *frames, char *why, size_t n) {
    *pcm = NULL; *frames = 0;
    if (s->codec != 3 || s->storage != 0 || s->channels > 2 || !s->rate || !s->samples)
        return fail(why, n, "unsupported codec %u storage %u", s->codec, s->storage);
    uint8_t *pk; unsigned npk;
    if (!collect(f, s, &pk, &npk, why, n)) return 0;
    const AVCodec *codec = avcodec_find_decoder(AV_CODEC_ID_XMA1);
    AVCodecContext *c = codec ? avcodec_alloc_context3(codec) : NULL;
    AVPacket *p = av_packet_alloc();
    AVFrame *fr = av_frame_alloc();
    unsigned cap = s->samples + 8192, got = 0, ch = s->channels;
    int16_t *o = calloc((size_t)cap * ch, 2);
    int ok = 0;
    if (!c || !p || !fr || !o) { fail(why, n, "no xma1 decoder in FFmpeg", 0, 0); goto done; }
    av_channel_layout_default(&c->ch_layout, ch);
    c->sample_rate = s->rate;
    c->pkt_timebase = (AVRational){1, (int)s->rate};
    c->extradata = av_mallocz(28 + AV_INPUT_BUFFER_PADDING_SIZE);   // XMAWAVEFORMAT: one stream of `ch` channels
    c->extradata_size = 28; c->extradata[4] = 1; c->extradata[8 + 17] = ch;
    if (avcodec_open2(c, codec, NULL) < 0) { fail(why, n, "xma1 open failed", 0, 0); goto done; }
    for (unsigned i = 0; i <= npk; ++i) {
        if (i < npk) {
            if (av_new_packet(p, 2048) < 0) goto done;
            memcpy(p->data, pk + i * 2048, 2048);
            p->pts = 0;   // a timestamp, so the decoder's start trim needs no log note
        }
        if (avcodec_send_packet(c, i < npk ? p : NULL) < 0) { fail(why, n, "packet %u rejected", i, 0); goto done; }
        av_packet_unref(p);
        while (avcodec_receive_frame(c, fr) >= 0) {
            for (int k = 0; k < fr->nb_samples && got < cap; ++k, ++got)
                for (unsigned j = 0; j < ch; ++j) {
                    float v = ((const float *)fr->extended_data[j])[k] * 32768.f;
                    o[got * ch + j] = v > 32767.f ? 32767 : v < -32768.f ? -32768 : (int16_t)v;
                }
            av_frame_unref(fr);
        }
    }
    snprintf(why, n, "decoded %u frames from %u packets (declared %u)", got, npk, s->samples);
    if (got < s->samples) goto done;
    *pcm = o; o = NULL; *frames = s->samples; ok = 1;
done:
    free(o); free(pk);
    av_frame_free(&fr); av_packet_free(&p); avcodec_free_context(&c);
    return ok;
}

// ---- PC side (BNKl, the only bank PC Snd reads: 008222A1 / 00823E2D) ----
// ABKC little-endian header; BNKl: "BNKl", uint16 5, uint16 slots, uint32 bytes used, 2 x uint32 0, then one uint32 per
// slot, relative to itself (0 = null slot). Each slot is a "PT\0\0" tag list (00823E83): tag, length, signed big-endian
// value; 0xFD opens the channel section, 0xFF ends it. 0x80 version, 0x82 channels, 0x84 rate (default 22050), 0x85
// samples, 0x86/0x87 loop start/end (default -1: one-shot), 0x88 data offset from BNKl, 0xA0 codec (default 10 EA-XA,
// 8 = PCM16LE). Sample tables (SNDAEMSI_updateplayer 0081A52C, rows clamped to the count): uint32 count, then 12-byte
// rows {uint32 0x0000FF00 (player 0), uint32 BNKl slot, uint32 0}; the X360's are {idx<<16 | FF00, slot, -1} big-endian,
// {FFFF0000, 0, 0} for an empty row.
static uint32_t le(const uint8_t *p) { return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24; }
static void put(uint8_t *p, uint32_t v) { p[0] = v; p[1] = v >> 8; p[2] = v >> 16; p[3] = v >> 24; }

int x360abk_pc_bank(const uint8_t *f, size_t size, X360AbkPcSlot *slots, unsigned max, unsigned *count, char *why, size_t n) {
    if (size < 0x60 || memcmp(f, "ABKC", 4) || le(f + 0x14) != size) return fail(why, n, "PC: not ABKC / size", 0, 0);
    uint32_t b = le(f + 0x20), ext = le(f + 0x24);
    if (b < 0x60 || ext < 0x14 || ext > size - b || memcmp(f + b, "BNKl", 4)) return fail(why, n, "PC: no BNKl at %08X", b, 0);
    unsigned cnt = f[b + 6] | f[b + 7] << 8;
    if (cnt > max || 0x14 + 4 * cnt > ext) return fail(why, n, "PC: BNKl slot count %u", cnt, 0);
    *count = cnt;
    for (unsigned i = 0; i < cnt; ++i) {
        X360AbkPcSlot *s = &slots[i];
        memset(s, 0, sizeof *s);
        uint32_t at = b + 0x14 + 4 * i, rel = le(f + at);
        if (!rel) continue;
        uint32_t p = at + rel;
        if (rel > ext || p + 5 > b + ext || memcmp(f + p, "PT\0\0", 4)) return fail(why, n, "PC: slot %u PT at %08X", i, p);
        s->present = 1; s->channels = 1; s->rate = 22050; s->codec = 10; s->loop_start = s->loop_end = -1;
        for (p += 4; p < b + ext;) {
            unsigned tag = f[p++];
            if (tag == 0xff) break;
            if (tag == 0xfc || tag == 0xfd || tag == 0xfe) continue;
            unsigned len = f[p++];
            if (len > 4 || p + len > b + ext) return fail(why, n, "PC: slot %u bad tag %02X", i, tag);
            int32_t v = len && (f[p] & 0x80) ? -1 : 0;
            for (unsigned j = 0; j < len; ++j) v = (int32_t)((uint32_t)v << 8 | f[p++]);
            if (tag == 0x82) s->channels = v; else if (tag == 0x84) s->rate = v; else if (tag == 0x85) s->samples = v;
            else if (tag == 0x86) s->loop_start = v; else if (tag == 0x87) s->loop_end = v; else if (tag == 0x88) s->data = v;
            else if (tag == 0xa0) s->codec = v;
        }
        if (s->data >= ext) return fail(why, n, "PC: slot %u data offset outside bank", i, 0);
        s->data += b;
    }
    return 1;
}

// One X360 sample table (big-endian, at t) → is every row a used row with slot = idx + 1, or an empty row?
static int x_sample_table(const uint8_t *x, uint32_t t, uint32_t lim, unsigned sounds) {
    if (t + 4 > lim) return 0;
    unsigned c = rd32(x + t);
    if (!c || c > 256 || t + 4 + 12 * c > lim) return 0;
    for (unsigned r = 0; r < c; ++r) {
        const uint8_t *row = x + t + 4 + 12 * r;
        uint32_t w = rd32(row), slot = rd32(row + 4), z = rd32(row + 8);
        unsigned stereo = (w & 0xffff) == 0xffe0;
        int used = ((w & 0xffff) == 0xff00 || stereo) && (w >> 16) < sounds &&
                   slot == ((w >> 16) + 1 + (stereo << 29)) && z == 0xffffffffu;
        if (!used && !(w == 0xffff0000u && !slot && !z)) return 0;
    }
    return 1;
}
static int pc_sample_table(const uint8_t *f, uint32_t t, uint32_t lim) {
    if (t + 4 > lim) return 0;
    unsigned c = le(f + t);
    if (!c || c > 256 || t + 4 + 12 * c > lim) return 0;
    for (unsigned r = 0; r < c; ++r) if (le(f + t + 4 + 12 * r) != 0xff00 || le(f + t + 12 + 12 * r)) return 0;
    return 1;
}
static unsigned put_tag(uint8_t *p, unsigned tag, int32_t v) {   // signed minimal big-endian, as the PC banks
    unsigned len = 0;
    while (len < 4 && !(len ? (v >> (8 * len - 1)) == 0 || (v >> (8 * len - 1)) == -1 : v == 0)) ++len;
    p[0] = tag; p[1] = len;
    for (unsigned j = 0; j < len; ++j) p[2 + j] = (uint8_t)(v >> (8 * (len - 1 - j)));
    return 2 + len;
}

// The PC's fixup tail (F1 to the end) after a bank rebuilt at nb with `used` bytes (padded to 128 as the PC's): copied,
// the CSIS name pointers (+0x38: count, then {uint32, name pointer, uint32}, file offsets into this tail) and the ABKC
// header offsets moved. o must hold nb + 128-padded used + the tail.
static int put_tail(uint8_t *o, const uint8_t *pc, size_t pcsize, uint32_t nb, size_t used, size_t *total, char *why, size_t n) {
    uint32_t F1 = le(pc + 0x30), F2 = le(pc + 0x34), F3 = le(pc + 0x38);
    uint32_t NE = (used + 0x7f) & ~0x7fu, NF1 = nb + NE, delta = NF1 - F1;
    memcpy(o + NF1, pc + F1, pcsize - F1);
    uint32_t c3 = le(pc + F3);
    if (F3 + 4 + 12 * (size_t)c3 > pcsize) return fail(why, n, "PC: CSIS table outside file", 0, 0);
    for (unsigned i = 0; i < c3; ++i) {
        uint32_t at = NF1 + (F3 - F1) + 8 + 12 * i, v = le(o + at);
        if (v < F3 || v >= pcsize) return fail(why, n, "PC: CSIS name pointer %08X outside the tail", v, 0);
        put(o + at, v + delta);
    }
    *total = NF1 + (pcsize - F1);
    put(o + 0x14, *total); put(o + 0x18, nb); put(o + 0x20, nb); put(o + 0x24, NE);
    put(o + 0x30, F1 + delta); put(o + 0x34, F2 + delta); put(o + 0x38, F3 + delta);
    return 1;
}

int x360abk_build_pc(const uint8_t *pc, size_t pcsize, const uint8_t *x, size_t xsize, const X360Abk *k,
                     int16_t *const *pcm, uint8_t **out, size_t *outsize, X360AbkBuildInfo *info, char *why, size_t n) {
    *out = NULL; *outsize = 0;
    memset(info, 0, sizeof *info);
    if (pcsize < 0x60 || memcmp(pc, "ABKC", 4) || le(pc + 0x14) != pcsize) return fail(why, n, "PC: not ABKC / size", 0, 0);
    uint32_t B = le(pc + 0x20), E = le(pc + 0x24), F1 = le(pc + 0x30), F2 = le(pc + 0x34), F3 = le(pc + 0x38);
    if (le(pc + 0x18) != B || B + E != F1 || !(F1 <= F2 && F2 < F3 && F3 + 4 <= pcsize) || memcmp(pc + B, "BNKl", 4))
        return fail(why, n, "PC: unexpected ABKC layout (bank %08X, fixups %08X)", B, F1);
    uint32_t xB = rd32(x + 0x20), xF2 = rd32(x + 0x34);
    unsigned pn = le(pc + F2), xn = xF2 + 4 <= xsize ? rd32(x + xF2) : ~0u;
    if (pn != xn || F2 + 4 + 4 * pn > F3 || xF2 + 4 + 4 * xn > xsize) return fail(why, n, "pointer fixups differ: PC %u, X360 %u", pn, xn);
    // The PC's first PT header up to its 0xFD: the bank's per-sound defaults (priority/volume tags) kept for every sound.
    uint8_t prefix[32];
    unsigned plen = 0;
    for (unsigned i = 0, cnt = pc[B + 6] | pc[B + 7] << 8; i < cnt && !plen; ++i) {
        uint32_t at = B + 0x14 + 4 * i, rel = le(pc + at), p = at + rel;
        if (!rel || p + 4 > F1 || memcmp(pc + p, "PT\0\0", 4)) continue;
        for (uint32_t q = p + 4; q < F1 && q - p < sizeof prefix; q += 2 + pc[q + 1])
            if (pc[q] == 0xfd) { plen = q + 1 - p; break; }
        if (plen) memcpy(prefix, pc + p, plen);
    }
    if (!plen) return fail(why, n, "PC: no PT header with a channel section", 0, 0);
    for (unsigned i = 0; i < k->count; ++i)
        if (k->s[i].hdr && (k->s[i].channels != 1 || !pcm[i])) return fail(why, n, "sound %u: not decoded mono (stereo PC layout unverified)", i, 0);

    // Size: module area + converted tables, bank (header, PT headers, PCM), fixups.
    size_t tables = 0, pcm_bytes = 0;
    for (unsigned i = 0; i < pn; ++i) {
        uint32_t xt = rd32(x + rd32(x + xF2 + 4 + 4 * i));
        if (x_sample_table(x, xt, xB, k->count)) tables += 4 + 12 * rd32(x + xt);
    }
    for (unsigned i = 0; i < k->count; ++i) if (k->s[i].hdr) pcm_bytes += (size_t)k->s[i].samples * 2;
    unsigned slots = k->count + 1;
    size_t NB = (B + tables + 0x7f) & ~(size_t)0x7f, hdr_cap = 0x14 + 4 * slots + slots * (plen + 48);
    size_t cap = NB + hdr_cap + pcm_bytes + 0x80 + (pcsize - F1);
    uint8_t *o = calloc(1, cap);
    if (!o) return fail(why, n, "out of memory", 0, 0);
    memcpy(o, pc, B);
    // Converted sample tables after the PC modules; the PC template pointers redirected to them.
    uint32_t t = B;
    for (unsigned i = 0; i < pn; ++i) {
        uint32_t ps = le(pc + F2 + 4 + 4 * i), xs = rd32(x + xF2 + 4 + 4 * i);
        if (ps + 4 > B || xs + 4 > xB) { free(o); return fail(why, n, "pointer site %u outside modules", i, 0); }
        uint32_t pt = le(pc + ps), xt = rd32(x + xs);
        int xtab = x_sample_table(x, xt, xB, k->count), ptab = pc_sample_table(pc, pt, B);
        if (xtab != ptab) { free(o); return fail(why, n, "pointer %u: sample table on one platform only (PC %08X)", i, pt); }
        if (!xtab) continue;
        unsigned c = rd32(x + xt);
        put(o + t, c);
        for (unsigned r = 0; r < c; ++r) {
            put(o + t + 4 + 12 * r, 0xff00);
            put(o + t + 8 + 12 * r, rd32(x + xt + 8 + 12 * r));
        }
        put(o + ps, t);
        if (info->tables < 8) { info->table_rows[info->tables][0] = le(pc + pt); info->table_rows[info->tables][1] = c; }
        info->tables++;
        t += 4 + 12 * c;
    }
    // BNKl: null slot 0, X360 sound i at slot i + 1 (the X360 tables' slot numbers stay valid).
    uint8_t *bk = o + NB;
    memcpy(bk, "BNKl", 4); bk[4] = 5; bk[6] = slots; bk[7] = slots >> 8;
    uint32_t hp = 0x14 + 4 * slots;   // pass 0 sizes the PT headers, pass 1 writes them and the PCM after them
    uint32_t data_at[512];
    for (int pass = 0; pass < 2; ++pass) {
        uint32_t h = 0x14 + 4 * slots, d = 0;
        if (pass) d = (hp + 3) & ~3u;
        for (unsigned i = 0; i < k->count; ++i) {
            const X360AbkSound *s = &k->s[i];
            if (!s->hdr) continue;
            if (pass) {
                put(bk + 0x14 + 4 * (i + 1), h - (0x14 + 4 * (i + 1)));
                data_at[i] = d;
                memcpy(bk + d, pcm[i], (size_t)s->samples * 2);   // mono PCM16: host (arm64) order is little-endian
                d += s->samples * 2;
                d = (d + 3) & ~3u;
            }
            uint8_t *p = bk + h;
            uint8_t tmp[64];
            if (!pass) p = tmp;
            unsigned l = plen;
            memcpy(p, prefix, plen);
            l += put_tag(p + l, 0x80, 2);
            l += put_tag(p + l, 0x84, s->rate);
            l += put_tag(p + l, 0x85, s->samples);
            if (s->loop) { l += put_tag(p + l, 0x86, s->loop_start); l += put_tag(p + l, 0x87, s->samples - 1); }
            l += put_tag(p + l, 0xa0, 8);
            p[l++] = 0x8a; p[l++] = 4; memset(p + l, 0, 4); l += 4;
            p[l++] = 0x88; p[l++] = 4; l += 4;   // data offset filled below
            if (pass) { uint32_t v = data_at[i]; p[l - 4] = v >> 24; p[l - 3] = v >> 16; p[l - 2] = v >> 8; p[l - 1] = v; }
            p[l++] = 0xff;
            h += (l + 3) & ~3u;
        }
        if (!pass) hp = h;
        else {
            put(bk + 8, d);
            info->bank_bytes = d;
        }
    }
    size_t total;
    if (!put_tail(o, pc, pcsize, NB, info->bank_bytes, &total, why, n)) { free(o); return 0; }
    info->sounds = k->count; info->pcm_bytes = pcm_bytes;
    *out = o; *outsize = total;
    snprintf(why, n, "ok: BNKl %u slots, %u sample tables, %zu bytes PCM16", slots, info->tables, pcm_bytes);
    return 1;
}

int x360abk_convert_pinned(const uint8_t *pc, size_t pc_size, const uint8_t *x, size_t x_size, const X360AbkPin *pin,
                           uint8_t **out, size_t *out_size, X360AbkBuildInfo *info, char *why, size_t n) {
    *out = NULL; *out_size = 0;
    memset(info, 0, sizeof *info);
    uint32_t pcrc = crc32(0, pc, pc_size), xcrc = crc32(0, x, x_size);
    if (pcrc != pin->pc_crc || xcrc != pin->x_crc)
        return fail(why, n, "source identity: CRC32 PC %08X X360 %08X differ from the pinned retail files", pcrc, xcrc);
    static X360Abk k;   // callers are serialized (x360audio.c's pthread_once, the offline test)
    if (!x360abk_parse(x, x_size, &k, why, n)) return 0;
    int16_t *pcm[512] = {0};
    uint32_t pcm_crc = 0;
    int ok = 0;
    for (unsigned i = 0; i < k.count; ++i) {
        unsigned frames;
        if (!k.s[i].hdr) continue;
        if (!x360abk_decode(x, &k.s[i], &pcm[i], &frames, why, n)) goto done;
        pcm_crc = crc32(pcm_crc, (const uint8_t *)pcm[i], frames * 2 * k.s[i].channels);
    }
    if (pcm_crc != pin->pcm_crc) { fail(why, n, "decoded PCM CRC32 %08X differs from the pinned %08X", pcm_crc, pin->pcm_crc); goto done; }
    if (!x360abk_build_pc(pc, pc_size, x, x_size, &k, pcm, out, out_size, info, why, n)) goto done;
    // Read the result back the way the PC parses it: every slot codec 8 at the X360's rate/length/loop, same PCM.
    static X360AbkPcSlot sl[513];
    unsigned cnt;
    char tmp[160];
    if (!x360abk_pc_bank(*out, *out_size, sl, 513, &cnt, tmp, sizeof tmp) || cnt != k.count + 1 || sl[0].present) {
        fail(why, n, "rebuilt bank does not read back (%u slots)", cnt, 0); goto bad;
    }
    for (unsigned i = 0; i < k.count; ++i) {
        const X360AbkSound *s = &k.s[i];
        const X360AbkPcSlot *p = &sl[i + 1];
        if (!s->hdr) { if (p->present) goto bad_slot; continue; }
        if (!p->present || p->codec != 8 || p->channels != 1 || p->rate != (int)s->rate || p->samples != (int)s->samples ||
            p->loop_start != (s->loop ? (int)s->loop_start : -1) || p->loop_end != (s->loop ? (int)s->samples - 1 : -1) ||
            p->data + (size_t)s->samples * 2 > *out_size || memcmp(*out + p->data, pcm[i], (size_t)s->samples * 2)) {
        bad_slot:
            fail(why, n, "rebuilt slot %u does not match X360 sound %u", i + 1, i); goto bad;
        }
    }
    snprintf(why, n, "ok: %u sounds, %u sample tables, %zu bytes PCM16, PCM CRC32 %08X", info->sounds, info->tables, info->pcm_bytes, pcm_crc);
    ok = 1;
    goto done;
bad:
    free(*out); *out = NULL; *out_size = 0;
done:
    for (unsigned i = 0; i < k.count; ++i) free(pcm[i]);
    return ok;
}

// ---- A10: every other bank, the PC bank with only its samples swapped ----
// A PC PT header at p: *pre = its bytes up to and including the 0xFD, *len = its whole length. The channel section may
// hold only the tags the swap rewrites or keeps (80 82 84-89 8A A0). Stereo uses planar streams, not interleaved PCM.
static int pt_parts(const uint8_t *f, uint32_t p, uint32_t end, unsigned *pre, unsigned *len) {
    *pre = 0;
    for (uint32_t q = p + 4; q < end;) {
        unsigned tag = f[q++];
        if (tag == 0xff) { *len = q - p; return *pre != 0; }
        if (tag == 0xfd) { if (*pre) return 0; *pre = q - p; continue; }
        if (tag == 0xfc || tag == 0xfe) { if (*pre) return 0; continue; }
        if (q >= end || f[q] > 4 || q + 1 + f[q] > end) return 0;
        unsigned l = f[q++];
        if (*pre && !((tag == 0x80 || (tag >= 0x84 && tag <= 0x89) || tag == 0x8a || tag == 0xa0) ||
                      (tag == 0x82 && l == 1 && (f[q] == 1 || f[q] == 2)))) return 0;
        q += l;
    }
    return 0;
}

// Exceptional retail pairs, proven by decoded XA/XMA content, duration and selectors.
// Pins are essential: the same structural difference in an unknown bank is not evidence for this mapping.
// kind 1: PC slot 3 is a silent placeholder, subsequent sounds shift by one; 2: same sample order, different selectors;
// kind 3: looped TURBO sounds 0 and 4 exchange places; 4: FE stereo, same slots/selectors.
static const struct { uint32_t pc, x; unsigned kind; } a10_maps[] = {
    {0xF7FD9111u, 0x2797D02Bu, 2}, // ENGINE/CAR_02_ENG_MB_SPU.ABK
    {0x8A8FF053u, 0x80438C5Au, 1}, // ENGINE/SWTN_CAR_00_MB.ABK
    {0x28B41BFBu, 0xE36832C4u, 1}, // ENGINE/SWTN_CAR_01_MB.ABK
    {0xBF60E7E4u, 0xD900C965u, 1}, // ENGINE/SWTN_CAR_02_MB.ABK
    {0xDE426090u, 0x2BC88727u, 1}, // ENGINE/SWTN_CAR_03_MB.ABK
    {0x9C43A6A1u, 0x2BA2EEFAu, 1}, // ENGINE/SWTN_CAR_04_MB.ABK
    {0x79A7B8C1u, 0xE2F4E4B0u, 1}, // ENGINE/SWTN_CAR_05_MB.ABK
    {0x5EE24AB9u, 0x5B456E7Du, 1}, // ENGINE/SWTN_CAR_06_MB.ABK
    {0x059D4DDCu, 0xE64FA02Cu, 1}, // ENGINE/SWTN_CAR_07_MB.ABK
    {0x5128137Fu, 0x6EBD8B31u, 1}, // ENGINE/SWTN_CAR_08_MB.ABK
    {0x6E09006Bu, 0x33F31AC6u, 1}, // ENGINE/SWTN_CAR_09_MB.ABK
    {0xC150E8EAu, 0x46F87F93u, 1}, // ENGINE/SWTN_CAR_10_MB.ABK
    {0xF608CD8Cu, 0xCD917C8Fu, 1}, // ENGINE/SWTN_CAR_11_MB.ABK
    {0xCBB2FE93u, 0x70B91CC2u, 1}, // ENGINE/SWTN_CAR_13_MB.ABK
    {0x7A72882Cu, 0xEA67E948u, 2}, // ENGINE/SWTN_CAR_28_MB.ABK
    {0xB7560B39u, 0x616B2B8Cu, 2}, // ENGINE/SWTN_CAR_37_MB.ABK
    {0x6A6A5DE9u, 0xB33EC6BFu, 2}, // ENGINE/SWTN_CAR_39_MB.ABK
    {0xDAF027B7u, 0x0B54E157u, 2}, // ENGINE/SWTN_CAR_40_MB.ABK
    {0x4EC2F774u, 0xB1785917u, 1}, // ENGINE/SWTN_CAR_61_MB.ABK
    {0xBEDF7240u, 0xE724C654u, 4}, // FE/FE_MB.ABK
    {0x5DCBAF89u, 0xA99C39B9u, 4}, // GLOBAL/FE_COMMON_MB.ABK
    {0x694E39BCu, 0xA44090E9u, 3}, // TURBO/TURBO_TUN_MED_0_MB.ABK
    {0xAC674A82u, 0x8F93ABF5u, 3}, // TURBO/TURBO_TUN_MED_1_MB.ABK
    {0xC5BAB24Au, 0x94212E09u, 3}, // TURBO/TURBO_TUN_MED_2_MB.ABK
};

// The PC PT reader stores tags 88/89 in adjacent channel pointers (00823E83); 008262DA forms one pointer per
// channel, and 00823691 creates one decoder per channel. Codec 8 (00822C41) therefore consumes planar PCM16LE.
// Find the second stream without changing the public mono/G3 slot description.
static uint32_t pc_data2(const uint8_t *f, unsigned slot) {
    uint32_t b = le(f + 0x20), at = b + 0x14 + 4 * slot, p = at + le(f + at);
    for (p += 4; f[p] != 0xff;) {
        unsigned tag = f[p++];
        if (tag >= 0xfc) continue;
        unsigned len = f[p++];
        if (tag == 0x89) {
            uint32_t v = 0;
            for (unsigned i = 0; i < len; ++i) v = v << 8 | f[p + i];
            return b + v;
        }
        p += len;
    }
    return 0;
}

#define WHY(...) do { snprintf(why, n, __VA_ARGS__); goto done; } while (0)

int x360abk_swap_pc(const uint8_t *pc, size_t pcsize, const uint8_t *x, size_t xsize, uint8_t **out, size_t *outsize,
                    X360AbkSwapInfo *info, char *why, size_t n) {
    *out = NULL; *outsize = 0;
    memset(info, 0, sizeof *info);
    X360Abk *k = malloc(sizeof *k);
    X360AbkPcSlot *sl = calloc(513, sizeof *sl), *rb = calloc(513, sizeof *rb);
    int16_t *pcm[512] = {0};
    uint8_t keep[513] = {0};   // slot keeps the PC's sample (PC-only or X360 placeholder)
    uint32_t extent[513] = {0}, at88[513] = {0}, at89[513] = {0};
    int map[513];   // PC slot -> X360 sound, -1 = PC-only placeholder
    uint8_t *o = NULL;
    unsigned cnt = 0, rcnt = 0;
    int ok = 0;
    char tmp[160];
    if (!k || !sl || !rb) WHY("memory: out of memory");
    if (!x360abk_parse(x, xsize, k, tmp, sizeof tmp)) WHY("X360 parse: %s", tmp);
    if (!x360abk_pc_bank(pc, pcsize, sl, 513, &cnt, tmp, sizeof tmp)) WHY("PC parse: %s", tmp);
    uint32_t B = le(pc + 0x20), E = le(pc + 0x24), F1 = le(pc + 0x30), F2 = le(pc + 0x34), F3 = le(pc + 0x38);
    if (le(pc + 0x18) != B || B + E != F1 || !(F1 <= F2 && F2 < F3 && F3 + 4 <= pcsize))
        WHY("layout: unexpected PC ABKC layout (bank %08X, fixups %08X)", B, F1);

    unsigned kind = 0;
    uint32_t pc_crc = crc32(0, pc, pcsize), x_crc = crc32(0, x, xsize);
    for (unsigned i = 0; i < sizeof a10_maps / sizeof *a10_maps; ++i) {
        if (a10_maps[i].pc == pc_crc && a10_maps[i].x == x_crc) kind = a10_maps[i].kind;
        else if (a10_maps[i].pc == pc_crc || a10_maps[i].x == x_crc)
            WHY("source identity: exceptional PC/X360 pair %08X/%08X is not proven", pc_crc, x_crc);
    }
    if (!cnt || sl[0].present || (!kind && cnt > k->count + 1))
        WHY("sound count: PC %u slots, X360 %u sounds", cnt, k->count);
    for (unsigned j = 0; j < 513; ++j) map[j] = -1;
    for (unsigned j = 1; j < cnt; ++j) {
        if (!sl[j].present) continue;
        if (kind == 1 && j == 3) { keep[j] = 1; info->placeholders++; continue; }
        unsigned i = j - 1 - (kind == 1 && j > 3);
        if (kind == 3 && (i == 0 || i == 4)) i = 4 - i;
        if (i >= k->count || !k->s[i].hdr) WHY("sound count: PC slot %u has no X360 sound", j);
        map[j] = i;
        if (k->s[i].channels != (unsigned)sl[j].channels || sl[j].channels < 1 || sl[j].channels > 2 ||
            (sl[j].channels == 2 && kind != 4))
            WHY("channels: sound %u has %u channels (PC %d), no proven stereo mapping", i, k->s[i].channels, sl[j].channels);
    }
    for (unsigned i = 0; i < k->count; ++i) {
        if (!k->s[i].hdr) continue;
        unsigned matches = 0;
        for (unsigned j = 1; j < cnt; ++j) matches += map[j] == (int)i;
        if (matches != 1) WHY("sound count: X360 sound %u maps to %u PC slots", i, matches);
    }
    // Same sample tables row for row, except pinned SWTN/engine selector differences proven against decoded identities.
    uint32_t xB = rd32(x + 0x20), xF2 = rd32(x + 0x34);
    unsigned pn = le(pc + F2), xn = xF2 + 4 <= xsize ? rd32(x + xF2) : ~0u;
    if (pn != xn || F2 + 4 + 4 * (size_t)pn > F3 || xF2 + 4 + 4 * (size_t)xn > xsize)
        WHY("sample tables: pointer fixups differ (PC %u, X360 %u)", pn, xn);
    for (unsigned i = 0; i < pn; ++i) {
        uint32_t ps = le(pc + F2 + 4 + 4 * i), xs = rd32(x + xF2 + 4 + 4 * i);
        if (ps + 4 > B || xs + 4 > xB) WHY("layout: pointer site %u outside modules", i);
        uint32_t pt = le(pc + ps), xt = rd32(x + xs);
        int xtab = x_sample_table(x, xt, xB, k->count), ptab = pc_sample_table(pc, pt, B);
        if (xtab != ptab) WHY("sample tables: pointer %u is a sample table on one platform only", i);
        if (!xtab) continue;
        unsigned c = rd32(x + xt);
        if (c != le(pc + pt)) WHY("sample tables: table %u has %u rows on the PC, %u on the X360", i, le(pc + pt), c);
        for (unsigned r = 0; r < c; ++r) {
            unsigned slot = le(pc + pt + 8 + 12 * r);
            if (slot >= cnt || (slot && !sl[slot].present)) WHY("sample tables: PC table %u row %u names an absent slot", i, r);
            uint32_t xslot = rd32(x + xt + 8 + 12 * r);
            if (kind == 4 && xslot) {
                unsigned idx = (xslot & 0x1fffffff) - 1, ch = (xslot >> 29) + 1;
                if (idx >= k->count || k->s[idx].channels != ch) WHY("sample tables: stereo row %u flags disagree with sound %u", r, idx);
                xslot &= 0x1fffffff;
            }
            if (kind != 1 && kind != 2 && xslot != slot)
                WHY("sample tables: table %u row %u names slot %u on the PC, %u on the X360", i, r, le(pc + pt + 8 + 12 * r), rd32(x + xt + 8 + 12 * r));
        }
    }
    // Per sound: the same loop flag, not another slot's sound (a sound whose length differs >1% from its
    // slot's but is within 0.5% of another slot's: the order differs, as TURBO_TUN_MED_* sounds 0 and 4).
    for (unsigned slot = 1; slot < cnt; ++slot) {
        if (map[slot] < 0) continue;
        unsigned i = map[slot];
        const X360AbkSound *s = &k->s[i];
        const X360AbkPcSlot *p = &sl[slot];
        if (s->loop != (p->loop_start >= 0)) WHY("loop: sound %u loops on one platform only", i);
        if (p->rate <= 0) WHY("PC parse: slot %u rate %d", i + 1, p->rate);
        double xd = (double)s->samples / s->rate, pd = (double)p->samples / p->rate;
        if (!kind && (xd > pd * 1.01 || xd < pd * 0.99))
            for (unsigned j = 1; j < cnt; ++j) {
                double d = sl[j].present && sl[j].rate > 0 ? (double)sl[j].samples / sl[j].rate : -1;
                if (j != i + 1 && xd <= d * 1.005 && xd >= d * 0.995)
                    WHY("reordered: X360 sound %u (%.3f s) has the length of PC slot %u, not of its slot %u (%.3f s)", i, xd, j, i + 1, pd);
            }
    }
    // Each PC slot's data extent (to the next slot's data, else the bank's used bytes) for the slots that keep it.
    uint32_t used = le(pc + B + 8);
    if (used > E) WHY("PC parse: BNKl uses %u of %u bytes", used, E);
    for (unsigned j = 1; j < cnt; ++j) {
        if (!sl[j].present) continue;
        uint32_t e = B + used;
        for (unsigned m = 1; m < cnt; ++m) if (sl[m].present && sl[m].data > sl[j].data && sl[m].data < e) e = sl[m].data;
        if (sl[j].data >= e) WHY("PC parse: slot %u data %08X past the bank's used bytes", j, sl[j].data);
        extent[j] = e - sl[j].data;
        if (sl[j].channels == 2) {
            uint32_t d2 = pc_data2(pc, j);
            if (d2 <= sl[j].data || d2 >= e) WHY("PC parse: slot %u second channel outside sample extent", j);
        }
    }
    // Decode; an all-zero sound is an X360 placeholder and its slot keeps the PC's sample.
    for (unsigned j = 1; j < cnt; ++j) {
        if (map[j] < 0) continue;
        unsigned i = map[j], fr;
        if (!x360abk_decode(x, &k->s[i], &pcm[i], &fr, tmp, sizeof tmp)) WHY("decode: sound %u: %s", i, tmp);
        size_t z = 0, ns = (size_t)fr * k->s[i].channels;
        while (z < ns && !pcm[i][z]) ++z;
        if (z == ns) {
            if (sl[j].channels != 1) WHY("placeholder: stereo sound %u needs PC stream preservation", i);
            keep[j] = 1; info->placeholders++;
        } else { info->sounds++; info->pcm_bytes += ns * 2; }
    }

    // Build: modules unchanged (the bank stays at B), a new BNKl, the tail moved.
    size_t hdr_cap = 0x14 + 4 * (size_t)cnt, data_cap = 0;
    for (unsigned j = 1; j < cnt; ++j) {
        if (!sl[j].present) continue;
        uint32_t at = B + 0x14 + 4 * j, p = at + le(pc + at);
        unsigned pre, len;
        if (!pt_parts(pc, p, B + E, &pre, &len)) WHY("PT tags: slot %u has a channel section the swap does not know", j);
        hdr_cap += len + 64;
        data_cap += (keep[j] ? extent[j] : (size_t)k->s[map[j]].samples * 2 * k->s[map[j]].channels) + 4;
    }
    size_t cap = B + hdr_cap + data_cap + 0x80 + (pcsize - F1);
    if (!(o = calloc(1, cap))) WHY("memory: out of memory");
    memcpy(o, pc, B);
    uint8_t *bk = o + B;
    memcpy(bk, pc + B, 0x14);   // "BNKl", version, slot count, bytes used (set below), reserved
    uint32_t h = 0x14 + 4 * cnt;
    for (unsigned j = 1; j < cnt; ++j) {
        if (!sl[j].present) continue;
        uint32_t at = B + 0x14 + 4 * j, p = at + le(pc + at);
        unsigned pre, len, l;
        pt_parts(pc, p, B + E, &pre, &len);
        put(bk + 0x14 + 4 * j, h - (0x14 + 4 * j));
        uint8_t *d = bk + h;
        memcpy(d, pc + p, pre);   // "PT", then the PC's per-sound tags (priority, volume, ...) up to the channel section
        l = pre;
        if (keep[j]) {   // the PC's channel section as it is, its data offset re-pointed
            for (uint32_t q = p + pre; pc[q] != 0xff; q += 2 + pc[q + 1]) {
                if (pc[q] == 0x88) continue;
                memcpy(d + l, pc + q, 2 + pc[q + 1]); l += 2 + pc[q + 1];
            }
        } else {
            const X360AbkSound *s = &k->s[map[j]];
            l += put_tag(d + l, 0x80, 2);
            l += put_tag(d + l, 0x82, s->channels);
            l += put_tag(d + l, 0x84, s->rate);
            l += put_tag(d + l, 0x85, s->samples);
            if (s->loop) { l += put_tag(d + l, 0x86, s->loop_start); l += put_tag(d + l, 0x87, s->samples - 1); }
            l += put_tag(d + l, 0xa0, 8);
            d[l++] = 0x8a; d[l++] = 4; memset(d + l, 0, 4); l += 4;
        }
        d[l++] = 0x88; d[l++] = 4; at88[j] = h + l; l += 4;   // filled below
        if (sl[j].channels == 2) { d[l++] = 0x89; d[l++] = 4; at89[j] = h + l; l += 4; }
        d[l++] = 0xff;
        h += (l + 3) & ~3u;
    }
    uint32_t dpos = (h + 3) & ~3u;
    for (unsigned j = 1; j < cnt; ++j) {
        if (!sl[j].present) continue;
        bk[at88[j]] = dpos >> 24; bk[at88[j] + 1] = dpos >> 16; bk[at88[j] + 2] = dpos >> 8; bk[at88[j] + 3] = dpos;
        size_t sz = keep[j] ? extent[j] : (size_t)k->s[map[j]].samples * 2 * k->s[map[j]].channels;
        if (keep[j]) memcpy(bk + dpos, pc + sl[j].data, sz);
        else {
            const X360AbkSound *s = &k->s[map[j]];
            for (unsigned ch = 0; ch < s->channels; ++ch) {
                uint32_t pos = dpos + ch * s->samples * 2;
                if (ch) {
                    uint8_t *v = bk + at89[j];
                    v[0] = pos >> 24; v[1] = pos >> 16; v[2] = pos >> 8; v[3] = pos;
                }
                for (unsigned fr = 0; fr < s->samples; ++fr) {
                    int16_t v = pcm[map[j]][fr * s->channels + ch];
                    bk[pos + fr * 2] = v; bk[pos + fr * 2 + 1] = (uint16_t)v >> 8;
                }
            }
        }
        dpos = (dpos + sz + 3) & ~3u;
    }
    put(bk + 8, dpos);
    size_t total;
    if (!put_tail(o, pc, pcsize, B, dpos, &total, tmp, sizeof tmp)) WHY("layout: %s", tmp);

    // Read back as the PC parses it: served slots codec 8 at the X360's rate/length/loop with the decoded PCM, kept
    // slots the PC's fields and bytes; modules and tail as the PC's.
    if (!x360abk_pc_bank(o, total, rb, 513, &rcnt, tmp, sizeof tmp) || rcnt != cnt) WHY("read back: %s (%u slots)", tmp, rcnt);
    for (unsigned j = 0; j < cnt; ++j) {
        const X360AbkPcSlot *r = &rb[j], *p = &sl[j];
        int bad = r->present != p->present;
        if (!bad && p->present && keep[j])
            bad = r->codec != p->codec || r->channels != p->channels || r->rate != p->rate || r->samples != p->samples ||
                  r->loop_start != p->loop_start || r->loop_end != p->loop_end || r->data + (size_t)extent[j] > total ||
                  memcmp(o + r->data, pc + p->data, extent[j]);
        else if (!bad && p->present) {
            const X360AbkSound *s = &k->s[map[j]];
            bad = r->codec != 8 || r->channels != (int)s->channels || r->rate != (int)s->rate || r->samples != (int)s->samples ||
                  r->loop_start != (s->loop ? (int)s->loop_start : -1) || r->loop_end != (s->loop ? (int)s->samples - 1 : -1) ||
                  r->data + (size_t)s->samples * 2 > total;
            for (unsigned ch = 0; !bad && ch < s->channels; ++ch) {
                uint32_t pos = ch ? pc_data2(o, j) : r->data;
                if (!pos || pos + (size_t)s->samples * 2 > total) { bad = 1; break; }
                for (unsigned fr = 0; fr < s->samples; ++fr)
                    if ((int16_t)(o[pos + fr * 2] | o[pos + fr * 2 + 1] << 8) != pcm[map[j]][fr * s->channels + ch]) { bad = 1; break; }
            }
        }
        if (bad) WHY("read back: slot %u does not match", j);
    }
    for (uint32_t w = 0; w + 4 <= B; w += 4)
        if (le(o + w) != le(pc + w) && w != 0x14 && w != 0x24 && w != 0x30 && w != 0x34 && w != 0x38)
            WHY("read back: module word %08X changed", w);
    if (memcmp(o + le(o + 0x30), pc + F1, F3 - F1)) WHY("read back: fixup tables changed");
    snprintf(why, n, "ok: %u sounds, %u placeholders kept PC, %zu bytes PCM16", info->sounds, info->placeholders, info->pcm_bytes);
    *out = o; o = NULL; *outsize = total;
    ok = 1;
done:
    free(o);
    for (unsigned i = 0; k && i < k->count; ++i) free(pcm[i]);
    free(k); free(sl); free(rb);
    return ok;
}

static uint8_t *read_all(int fd, size_t *size) {
    struct stat st;
    if (fd < 0 || fstat(fd, &st) || st.st_size <= 0) return NULL;
    uint8_t *b = malloc(st.st_size);
    if (b && pread(fd, b, st.st_size, 0) != st.st_size) { free(b); b = NULL; }
    *size = st.st_size;
    return b;
}
static int write_all(int fd, const uint8_t *p, size_t n) {
    for (ssize_t w; n; p += w, n -= w) if ((w = write(fd, p, n)) <= 0) return 0;
    return 1;
}

// Metadata stays on the payload inode where supported; ENOTSUP uses atomic checksum sidecars. Neither changes
// the ABK bytes/length seen by the PC. Old caches without a digest are regenerated once. CommonCrypto uses
// the CPU's SHA instructions for hashing a large bank on its first open.
#include "cachemeta.h"

int x360abk_serve(const char *pc_path, const char *x_path, const char *cache_dir, X360AbkServe *st, char *why, size_t n) {
    struct timespec t0, t1;
    clock_gettime(CLOCK_MONOTONIC, &t0);
    memset(st, 0, sizeof *st);
    size_t ps = 0, xs = 0, os = 0;
    int pfd = open(pc_path, O_RDONLY), xfd = open(x_path, O_RDONLY), fd = -1;
    uint8_t *pc = read_all(pfd, &ps), *x = read_all(xfd, &xs), *o = NULL;
    if (pfd >= 0) close(pfd);
    if (xfd >= 0) close(xfd);
    if (!pc || !x) { snprintf(why, n, "file: %s file not readable", pc ? "X360" : "PC"); goto done; }
    const char *base = strrchr(pc_path, '/');
    base = base ? base + 1 : pc_path;
    char path[4096], stem[256];
    snprintf(stem, sizeof stem, "%.*s", (int)(strcspn(base, ".")), base);
    snprintf(path, sizeof path, "%s/%s_%08lX_%08lX_v1.abk", cache_dir, stem, crc32(0, pc, ps), crc32(0, x, xs));
    if ((fd = open(path, O_RDONLY)) >= 0) {
        X360AbkPcSlot *sl = malloc(513 * sizeof *sl);
        unsigned cnt;
        char tmp[160];
        uint8_t *c = read_all(fd, &os);
        int good = c && sl && os >= 0x60 && le(c + 0x14) == os && x360abk_pc_bank(c, os, sl, 513, &cnt, tmp, sizeof tmp)
                   && cache_digest(fd, path, c, os, 0);
        free(c); free(sl);
        if (good) { st->cached = 1; st->bytes = os; snprintf(why, n, "ok: from the cache"); goto done; }
        close(fd); fd = -1;
        unlink(path);
        fprintf(stderr, "x360abk: %s cache invalid (structure or content digest); regenerating\n", stem);
    }
    if (!x360abk_swap_pc(pc, ps, x, xs, &o, &os, &st->info, why, n)) goto done;
    st->bytes = os;
    char tmpl[4200];
    snprintf(tmpl, sizeof tmpl, "%s/.tmp_XXXXXX", cache_dir);
    char dir[4096];   // mkdir -p
    snprintf(dir, sizeof dir, "%s", cache_dir);
    for (char *p = dir + 1; *p; ++p) if (*p == '/') { *p = 0; mkdir(dir, 0755); *p = '/'; }
    int tfd = mkdir(dir, 0755) && errno != EEXIST ? -1 : mkstemp(tmpl);
    if (tfd >= 0 && write_all(tfd, o, os) && cache_digest(tfd, path, o, os, 1) && !rename(tmpl, path)) {
        lseek(tfd, 0, SEEK_SET);
        fd = tfd;
        goto done;
    }
    if (tfd >= 0) { close(tfd); unlink(tmpl); }
    char t2[1200]; roots_tmp_template("nfsmw_x360abk_", t2, sizeof t2);   // the cache is not writable: an unlinked temp file
    if ((fd = mkstemp(t2)) >= 0) {
        unlink(t2);
        if (!write_all(fd, o, os) || lseek(fd, 0, SEEK_SET)) { close(fd); fd = -1; snprintf(why, n, "file: temp file not written"); }
        else strlcat(why, " (cache not writable)", n);
    }
done:
    free(pc); free(x); free(o);
    clock_gettime(CLOCK_MONOTONIC, &t1);
    st->ms = (t1.tv_sec - t0.tv_sec) * 1e3 + (t1.tv_nsec - t0.tv_nsec) / 1e6;
    return fd;
}
