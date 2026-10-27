// Offline proof for runtime/x360abk.c (A10, G3): no game, no audio device. Build and run from port/:
//   clang -O1 -I third_party/ffmpeg/include runtime/x360abk_test.c runtime/x360abk.c third_party/ffmpeg/lib/libavcodec.a \
//     third_party/ffmpeg/lib/libavutil.a -lz -o /tmp/x360abk_test
//   /tmp/x360abk_test g3 <X360 SOUND dir> <PC SOUND dir>          (exit 0 = every check passed)
//   /tmp/x360abk_test inventory <X360 SOUND dir> <PC SOUND dir> <inventory output>
//   /tmp/x360abk_test allbanks <X360 SOUND dir> <PC SOUND dir> <scratch cache dir>   (exit 0 = every check passed)
//   /tmp/x360abk_test allbanks-quiet <X360 SOUND dir> <PC SOUND dir> <scratch cache dir> (negative: PCM /32, must fail)
//   /tmp/x360abk_test allbanks-silent <X360 SOUND dir> <PC SOUND dir> <scratch cache dir> (negative: zero PCM, must fail)
// g3: TRAFFIC_MB + ENV_COMMON_MB through the production x360abk_convert_pinned: exact declared frame counts, non-silent,
// pinned CRCs, rebuilt banks read back as the PC parses them, every converted selector row names a present sound; then
// negatives (payload byte flip, S10A count, sound header offset, packet sequence, truncated X360 file, truncated PC
// file, ABKC size field) must all be rejected, pinned and (but the payload flip, which FFmpeg also happens to reject)
// unpinned. 2026-10-25: PASS 139 checks; 28 sounds exact length, PCM CRC32 traffic 26AA3B1C / ENV 5A76F2EE, rebuilt
// banks 3111976 / 1436760 bytes. Broken run: with wrong PCM pins, and with the PT headers misplaced over the PCM (a
// builder bug while writing it), the convert/read-back checks FAILED (4 and 2 failures).
// inventory: 301/301 banks decode (0.9 s).
// allbanks (A10): production swap + independent PC PT/channel reads; exact rate/frame/loop metadata, non-silent PCM
// equal to the XMA decode, unchanged PC modules/selectors and preserved placeholders. The 24 newly mapped retail
// pairs additionally decode the PC EA-XA (FFmpeg R2) and compare content after rate matching/filtering, for both stereo
// channels; altered source identities must refuse the exceptional mapping. Wrong TURBO sound 0 -> PC slot 1 has
// identity 0.056196 / 0.036519 / 0.036519 (right mappings >=0.913337); the test rejects all three.
// 2026-10-25: PASS 39027 checks, 299 A10 banks, 2514 sounds (40 stereo), 50 PC placeholders (36 all-zero X360 + 14
// PC-only); TRAFFIC/ENV go through G3. 170.7 MB rebuilt, 168.5 MB PCM16, largest 12.7 MB. Broken production run with
// the TURBO swap removed: FAIL 18 checks (exact lengths/loops/PCM), despite passing its own builder read-back.
// Serve cold/warm/damaged/unwritable cache and malformed-file/selector negatives remain covered. g3: PASS 139 checks.
// Cache integrity: zero only PCM (metadata/length intact), remove/damage the digest, and truncate the file.
// The old structural predicate accepts zero PCM; production reuse must regenerate byte-identical non-silent data.
// 2026-10-25: current PASS 46452 checks; actual pre-change x360abk.c FAIL 3 (zero PCM / missing / bad digest reused).
// SHA256, largest rebuilt bank 12678844 bytes: 20 hashes mean 3.851 ms, max 3.894 ms; reuse is synchronous at open.
// Quiet-race investigation, 2026-10-25: independently decode every original PC EA-XA channel, compare rebuilt full
// and loop-region RMS with PC (0.5..2.0, RMS >= 1), and report race-bank identities. Unmodified 3d14787 converter:
// PASS 46285 checks; 2554 channels 0.911160..1.185705, 1230 loop channels 0.911160..1.120386. Thus this does NOT
// reproduce the reported race-mix regression. allbanks-quiet and allbanks-silent each FAIL 6338 checks, including
// all 3784 new full/loop level checks (quiet ratios 0.028435..0.036984; silent 0).
#include <dirent.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>
#include <sys/xattr.h>
#include <CommonCrypto/CommonDigest.h>
#include <time.h>
#include <unistd.h>
#include <zlib.h>
#include <libavcodec/avcodec.h>
#include <libavutil/channel_layout.h>
#include "x360abk.h"

static unsigned checks, failures;
static int quiet_output;   // negative controls: attenuate/zero rebuilt PCM after the production read-back
#define CHECK(x) do { checks++; if (!(x)) { fprintf(stderr, "FAIL %d: %s\n", __LINE__, #x); failures++; } } while (0)

static uint8_t *slurp(const char *path, size_t *size) {
    FILE *f = fopen(path, "rb");
    if (!f) return NULL;
    fseek(f, 0, SEEK_END); *size = (size_t)ftell(f); rewind(f);
    uint8_t *b = malloc(*size ? *size : 1);
    if (fread(b, 1, *size, f) != *size) { free(b); b = NULL; }
    fclose(f);
    return b;
}
// Case-insensitive path below root (the PC folder names differ in case from the X360's).
static int find_ci(const char *root, const char *rel, char *out, size_t n) {
    char cur[4096], part[1024], *st;
    snprintf(cur, sizeof cur, "%s", root); snprintf(part, sizeof part, "%s", rel);
    for (char *tok = strtok_r(part, "/", &st); tok; tok = strtok_r(NULL, "/", &st)) {
        DIR *d = opendir(cur); struct dirent *e; int found = 0;
        while (d && (e = readdir(d))) if (!strcasecmp(e->d_name, tok)) { strlcat(cur, "/", sizeof cur); strlcat(cur, e->d_name, sizeof cur); found = 1; break; }
        if (d) closedir(d);
        if (!found) return 0;
    }
    snprintf(out, n, "%s", cur);
    return 1;
}

static const X360AbkPin kTraffic = {"TRAFFIC_MB", 0x87C6513Bu, 0xF41BF541u, 0x26AA3B1Cu};
static const X360AbkPin kEnv = {"ENV_COMMON_MB", 0x8E26F599u, 0xBCE7608Bu, 0x5A76F2EEu};

static int convert(const uint8_t *pc, size_t ps, const uint8_t *x, size_t xs, const X360AbkPin *pin, X360AbkBuildInfo *info, uint8_t **out, size_t *os) {
    char why[256];
    int ok = x360abk_convert_pinned(pc, ps, x, xs, pin, out, os, info, why, sizeof why);
    printf("  %s: %s\n", pin->name, why);
    return ok;
}

// The unpinned production steps: parse, decode every sound, rebuild. 1 = accepted.
static int structural(const uint8_t *pc, size_t ps, const uint8_t *x, size_t xs, char *why, size_t n) {
    static X360Abk k;
    int16_t *pcm[512] = {0};
    int ok = x360abk_parse(x, xs, &k, why, n);
    for (unsigned i = 0; ok && i < k.count; ++i) {
        unsigned fr;
        if (k.s[i].hdr) ok = x360abk_decode(x, &k.s[i], &pcm[i], &fr, why, n);
    }
    uint8_t *out = NULL; size_t os; X360AbkBuildInfo info;
    if (ok) ok = x360abk_build_pc(pc, ps, x, xs, &k, pcm, &out, &os, &info, why, n);
    for (unsigned i = 0; i < k.count; ++i) free(pcm[i]);
    free(out);
    return ok;
}

static int g3(const char *xdir, const char *pdir) {
    const X360AbkPin *pins[2] = {&kTraffic, &kEnv};
    for (int b = 0; b < 2; ++b) {
        char rel[256], xp[4096], pp[4096];
        snprintf(rel, sizeof rel, "IG_GLOBAL/%s.ABK", pins[b]->name);
        CHECK(find_ci(xdir, rel, xp, sizeof xp) && find_ci(pdir, rel, pp, sizeof pp));
        size_t xs = 0, ps = 0;
        uint8_t *x = slurp(xp, &xs), *pc = slurp(pp, &ps), *out = NULL;
        CHECK(x && pc);
        if (!x || !pc) return 1;
        // Per sound: exact declared length (the decoder's start trim/end trim leave exactly the S10A sample count), non-silent.
        static X360Abk k;
        char why[256];
        CHECK(x360abk_parse(x, xs, &k, why, sizeof why));
        unsigned present = 0;
        for (unsigned i = 0; i < k.count; ++i) {
            if (!k.s[i].hdr) continue;
            int16_t *pcm; unsigned fr;
            CHECK(x360abk_decode(x, &k.s[i], &pcm, &fr, why, sizeof why));
            CHECK(fr == k.s[i].samples && k.s[i].channels == 1 && k.s[i].codec == 3);
            double e = 0; int peak = 0;
            for (unsigned j = 0; j < fr; ++j) { e += (double)pcm[j] * pcm[j]; if (abs(pcm[j]) > peak) peak = abs(pcm[j]); }
            printf("  %s sound %2u: %u Hz, %u frames (%s), loop %s, peak %d, RMS %.0f\n", pins[b]->name, i, k.s[i].rate, fr, why,
                   k.s[i].loop ? "yes" : "no", peak, sqrt(e / fr));
            CHECK(peak > 1000 && sqrt(e / fr) > 100);
            free(pcm); ++present;
        }
        X360AbkBuildInfo info;
        size_t os;
        CHECK(convert(pc, ps, x, xs, pins[b], &info, &out, &os));
        // Every converted selector row names a slot that holds a sound; the decoded counts cover traffic engine
        // 3 -> 11 and truck 6 -> 6 rows, ENV_STATIC horn 12 -> 14.
        if (b == 0) CHECK(present == 17 && info.tables == 2 && info.table_rows[0][0] == 3 && info.table_rows[0][1] == 11 && info.table_rows[1][0] == 6 && info.table_rows[1][1] == 6);
        else CHECK(present == 11 && info.tables == 1 && info.table_rows[0][0] == 12 && info.table_rows[0][1] == 14);
        if (out) {
            static X360AbkPcSlot sl[513];
            unsigned cnt;
            CHECK(x360abk_pc_bank(out, os, sl, 513, &cnt, why, sizeof why));
            uint32_t F2 = *(uint32_t *)(out + 0x34);
            for (unsigned i = 0, np = *(uint32_t *)(out + F2); i < np; ++i) {
                uint32_t site = *(uint32_t *)(out + F2 + 4 + 4 * i), t = *(uint32_t *)(out + site);
                if (t < *(uint32_t *)(pc + 0x20)) continue;   // a PC table/curve kept in place
                unsigned rows = *(uint32_t *)(out + t), used = 0;
                for (unsigned r = 0; r < rows; ++r) {
                    uint32_t slot = *(uint32_t *)(out + t + 8 + 12 * r);
                    CHECK(slot < cnt && (!slot || sl[slot].present));
                    used += slot != 0;
                }
                printf("  %s: table at %08X, %u rows, %u with a sound\n", pins[b]->name, t, rows, used);
            }
            printf("  %s: PC %zu bytes -> rebuilt %zu bytes (CRC32 %08lX)\n", pins[b]->name, ps, os, crc32(0, out, os));
            free(out);
        }
        // Negatives. Arbitrary compressed-byte corruption can still decode, so the pins carry the identity proof.
        if (b == 0) {
            uint8_t *m = malloc(xs);
            size_t os2;
            X360AbkBuildInfo i2;
            struct { const char *what; uint32_t off; uint8_t xor; size_t len; } neg[] = {
                {"payload byte 0xB868 flipped", 0xB868, 0x01, xs},
                {"S10A count corrupted", 0xA0B, 0x40, xs},
                {"sound 3 header offset corrupted", 0xA18, 0x80, xs},
                {"packet 1 sequence nibble broken", 0xA5C + 2048 + 12, 0x30, xs},
                {"X360 file truncated by one packet", 0, 0, xs - 2048},
            };
            for (unsigned j = 0; j < sizeof neg / sizeof neg[0]; ++j) {
                memcpy(m, x, xs);
                if (neg[j].xor) m[neg[j].off] ^= neg[j].xor;
                uint8_t *o2 = NULL;
                int ok = x360abk_convert_pinned(pc, ps, m, neg[j].len, &kTraffic, &o2, &os2, &i2, why, sizeof why);
                printf("  negative: %s -> %s\n", neg[j].what, ok ? "ACCEPTED" : why);
                CHECK(!ok && !o2);
                // Without the pins, parse + decode + rebuild must still reject everything but the payload flip.
                int st = structural(pc, ps, m, neg[j].len, why, sizeof why);
                printf("            unpinned -> %s\n", st ? "accepted (decodable compressed bytes: only the pin catches it)" : why);
                CHECK(j == 0 ? 1 : !st);
            }
            uint8_t *o2 = NULL;
            CHECK(!x360abk_convert_pinned(pc, ps - 4, x, xs, &kTraffic, &o2, &os2, &i2, why, sizeof why) && !o2);
            printf("  negative: PC file truncated -> %s\n", why);
            static X360Abk k3;
            memcpy(m, x, xs);
            m[0x14] ^= 1;
            CHECK(!x360abk_parse(m, xs, &k3, why, sizeof why));
            printf("  negative: ABKC size field corrupted -> %s\n", why);
            free(m);
        }
        free(x); free(pc);
    }
    return 0;
}

// ---- inventory (A10) ----
typedef struct { char rel[512]; } Rel;
static Rel *rels; static unsigned nrel, caprel;
static void walk(const char *root, const char *sub) {
    char path[4096];
    snprintf(path, sizeof path, "%s/%s", root, sub);
    DIR *d = opendir(path); struct dirent *e;
    while (d && (e = readdir(d))) {
        if (e->d_name[0] == '.') continue;
        char rel[512];
        snprintf(rel, sizeof rel, "%s%s%s", sub, *sub ? "/" : "", e->d_name);
        struct stat st; char full[4096];
        snprintf(full, sizeof full, "%s/%s", root, rel);
        if (!stat(full, &st) && S_ISDIR(st.st_mode)) walk(root, rel);
        else if (strlen(rel) > 4 && !strcasecmp(rel + strlen(rel) - 4, ".abk")) {
            if (nrel == caprel) rels = realloc(rels, (caprel = caprel ? caprel * 2 : 256) * sizeof *rels);
            snprintf(rels[nrel++].rel, sizeof rels->rel, "%s", rel);
        }
    }
    if (d) closedir(d);
}
static int cmp_rel(const void *a, const void *b) { return strcasecmp(((const Rel *)a)->rel, ((const Rel *)b)->rel); }
static void rates(char *out, size_t n, const unsigned *r, unsigned cnt) {
    unsigned seen[8] = {0}, ns = 0; out[0] = 0;
    for (unsigned i = 0; i < cnt; ++i) {
        unsigned j = 0;
        while (j < ns && seen[j] != r[i]) ++j;
        if (j == ns && ns < 8) seen[ns++] = r[i];
    }
    for (unsigned j = 0; j < ns; ++j) { char t[16]; snprintf(t, sizeof t, "%s%.4g", j ? "/" : "", seen[j] / 1000.0); strlcat(out, t, n); }
}

static int inventory(const char *xdir, const char *pdir, const char *md) {
    walk(xdir, "");
    qsort(rels, nrel, sizeof *rels, cmp_rel);
    FILE *o = fopen(md, "w");
    if (!o) return 1;
    static char rows[400][640];
    unsigned decoded_banks = 0, fail_banks = 0, more = 0, longer = 0, higher = 0, better = 0, nopc = 0;
    unsigned long xsounds = 0, psounds = 0, stereo = 0, silent_all = 0;
    double xsec = 0, psec = 0;
    size_t xbytes = 0, pbytes = 0, pcm_total = 0;
    for (unsigned b = 0; b < nrel; ++b) {
        char xp[4096], pp[4096], why[256] = "";
        snprintf(xp, sizeof xp, "%s/%s", xdir, rels[b].rel);
        size_t xs = 0, ps = 0;
        uint8_t *x = slurp(xp, &xs), *pc = find_ci(pdir, rels[b].rel, pp, sizeof pp) ? slurp(pp, &ps) : NULL;
        static X360Abk k;
        static X360AbkPcSlot sl[600];
        unsigned xr[512], pr[600], nx = 0, np = 0, bad = 0, pcnt = 0, silent = 0;
        double xs_sec = 0, ps_sec = 0, min_rms = 1e9;
        xbytes += xs; pbytes += ps;
        if (x && x360abk_parse(x, xs, &k, why, sizeof why)) {
            for (unsigned i = 0; i < k.count; ++i) {
                if (!k.s[i].hdr) continue;
                int16_t *pcm; unsigned fr;
                xr[nx++] = k.s[i].rate;
                xs_sec += (double)k.s[i].samples / k.s[i].rate;
                stereo += k.s[i].channels == 2;
                if (!x360abk_decode(x, &k.s[i], &pcm, &fr, why, sizeof why)) { ++bad; continue; }
                double e = 0;
                for (size_t j = 0; j < (size_t)fr * k.s[i].channels; ++j) e += (double)pcm[j] * pcm[j];
                double rms = sqrt(e / ((double)fr * k.s[i].channels));
                if (rms < 1) ++silent;
                else if (rms < min_rms) min_rms = rms;
                pcm_total += (size_t)fr * 2 * k.s[i].channels;
                free(pcm);
            }
        } else bad = ~0u;
        if (pc && x360abk_pc_bank(pc, ps, sl, 600, &pcnt, why, sizeof why))
            for (unsigned i = 0; i < pcnt; ++i) if (sl[i].present) { pr[np++] = sl[i].rate; ps_sec += sl[i].rate ? (double)sl[i].samples / sl[i].rate : 0; }
        xsounds += nx; psounds += np; silent_all += silent; xsec += xs_sec; psec += ps_sec;
        if (bad) ++fail_banks; else ++decoded_banks;
        unsigned xmax = 0, pmax = 0;
        for (unsigned i = 0; i < nx; ++i) if (xr[i] > xmax) xmax = xr[i];
        for (unsigned i = 0; i < np; ++i) if (pr[i] > pmax) pmax = pr[i];
        char verdict[96] = "";
        int m = pc && nx > np, l = pc && xs_sec > ps_sec * 1.05, h = pc && xmax > pmax;
        if (!pc) { snprintf(verdict, sizeof verdict, "X360 only"); ++nopc; }
        else {
            if (m) strlcat(verdict, "more ", sizeof verdict);
            if (l) strlcat(verdict, "longer ", sizeof verdict);
            if (h) strlcat(verdict, "higher-rate", sizeof verdict);
            if (!m && !l && !h) strlcat(verdict, nx < np ? "fewer" : "same", sizeof verdict);
        }
        more += m; longer += l; higher += h; better += m || l || h;
        char xrs[96], prs[96];
        rates(xrs, sizeof xrs, xr, nx); rates(prs, sizeof prs, pr, np);
        snprintf(rows[b], sizeof rows[b], "| %s | %u | %s | %.1f | %s | %u | %.0f | %u | %s | %.1f | %s |\n", rels[b].rel, nx, xrs, xs_sec,
                 bad ? (bad == ~0u ? "parse fail" : "FAIL") : "ok", silent, min_rms < 1e9 ? min_rms : 0, np, pc ? prs : "-", ps_sec, verdict);
        free(x); free(pc);
    }
    fprintf(o, "# X360 sound banks (A10 inventory)\n\n");
    fprintf(o, "Generated by `runtime/x360abk_test.c inventory` (runtime/x360abk.c parser + FFmpeg xma1) from the X360 extract and the PC game's SOUND folders; do not edit by hand.\n\n");
    fprintf(o, "**Headline:** %u of %u X360 banks parse and decode completely (%lu sounds, %lu stereo, %lu all-zero placeholders, %.1f s, %.1f MB PCM16); %u fail. "
               "Against the PC bank of the same name, %u banks are better: %u have more sounds, %u are >5%% longer in total, %u have a higher top sample rate. "
               "%u have no PC counterpart. All X360 sounds are codec 3 (EA-wrapped XMA1), resident; PC: %lu sounds, %.1f s.\n\n",
            decoded_banks, nrel, xsounds, stereo, silent_all, xsec, pcm_total / 1e6, fail_banks, better, more, longer, higher, nopc, psounds, psec);
    fprintf(o, "Files: X360 %.1f MB, PC %.1f MB. Columns: sounds present (empty S10A slots not counted), sample rates (kHz), total seconds, decode result, "
               "silent sounds (all-zero PCM: placeholders, e.g. one per SWTN sweetener bank), lowest RMS of the others (PCM16), then the same for the PC BNKl (PT headers), and the verdict.\n\n", xbytes / 1e6, pbytes / 1e6);
    fprintf(o, "| bank | X360 sounds | X360 kHz | X360 s | decode | silent | min RMS | PC sounds | PC kHz | PC s | X360 vs PC |\n|---|---|---|---|---|---|---|---|---|---|---|\n");
    for (unsigned b = 0; b < nrel; ++b) fputs(rows[b], o);
    fclose(o);
    printf("inventory: %u banks, %u decode, %u fail, %u better (%u more, %u longer, %u higher-rate), %u X360 only\n",
           nrel, decoded_banks, fail_banks, better, more, longer, higher, nopc);
    return fail_banks != 0;
}


// ---- allbanks (A10): every X360 bank through the production x360abk_swap_pc, the serve/cache path, negatives ----
static double now_ms(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec * 1e3 + t.tv_nsec / 1e6; }
static int swap_ok(const uint8_t *pc, size_t ps, const uint8_t *x, size_t xs, char *why, size_t n) {
    uint8_t *o = NULL; size_t os; X360AbkSwapInfo in;
    int ok = x360abk_swap_pc(pc, ps, x, xs, &o, &os, &in, why, n);
    free(o);
    return ok;
}

// Independent fixtures describe the decoded identities, never the production CRC/mapping table.
static unsigned extra_kind(const char *rel) {
    if (!strcmp(rel, "FE/FE_MB.ABK") || !strcmp(rel, "GLOBAL/FE_COMMON_MB.ABK")) return 4;
    if (strstr(rel, "TURBO/TURBO_TUN_MED_")) return 3;
    if (!strcmp(rel, "ENGINE/CAR_02_ENG_MB_SPU.ABK")) return 2;
    unsigned car;
    if (sscanf(rel, "ENGINE/SWTN_CAR_%u_MB.ABK", &car) == 1) {
        if (car <= 11 || car == 13 || car == 61) return 1;
        if (car == 28 || car == 37 || car == 39 || car == 40) return 2;
    }
    return 0;
}
static int expected_sound(unsigned kind, unsigned slot) {
    if (kind == 1) return slot == 3 ? -1 : (int)slot - (slot > 3 ? 2 : 1);
    if (kind == 3 && (slot == 1 || slot == 5)) return slot == 1 ? 4 : 0;
    return (int)slot - 1;
}
static int race_bank(const char *rel) {
    return strstr(rel, "CAR_66_ENG_MB_EE") || strstr(rel, "SWTN_CAR_66") || strstr(rel, "CAR_WHINE_00") ||
           strstr(rel, "TURBO_TUN_SML_0") || strstr(rel, "NITROUS_00") || strstr(rel, "SKID_BIG_MB") ||
           strstr(rel, "GEAR_MED_LEV3");
}

// PT tag walk independent of the production builder/read-back. Offsets 88 and 89 are relative to BNKl.
static uint32_t stream_at(const uint8_t *f, size_t size, unsigned slot, unsigned ch) {
    if (size < 0x60) return 0;
    uint32_t b = *(const uint32_t *)(f + 0x20), at = b + 20 + 4 * slot;
    if (at + 4 > size) return 0;
    uint32_t q = at + *(const uint32_t *)(f + at) + 4;
    while (q < size && f[q] != 255) {
        unsigned tag = f[q++];
        if (tag >= 252) continue;
        if (q >= size) return 0;
        unsigned len = f[q++]; uint32_t v = 0;
        if (len > 4 || q + len > size) return 0;
        for (unsigned k = 0; k < len; ++k) v = v << 8 | f[q + k];
        if (tag == 0x88 + ch) return v < size - b ? b + v : 0;
        q += len;
    }
    return 0;
}

// Codec 10 is headerless EA-XA R2, including EE literal blocks. Wrap one PC channel for FFmpeg's R2 decoder;
// its packet header is {LE padded-frame-count, LE channel offset 0}. Trim only the final 28-frame block padding.
static int16_t *decode_pc(const uint8_t *pc, size_t size, unsigned slot, unsigned ch, const X360AbkPcSlot *s) {
    uint32_t pos = stream_at(pc, size, slot, ch), b = *(const uint32_t *)(pc + 0x20);
    uint32_t end = b + *(const uint32_t *)(pc + b + 8);
    if (s->codec != 10 || !pos || end > size || pos >= end || s->samples <= 0) return NULL;
    const AVCodec *codec = avcodec_find_decoder(AV_CODEC_ID_ADPCM_EA_R2);
    AVCodecContext *c = codec ? avcodec_alloc_context3(codec) : NULL;
    AVPacket *pk = av_packet_alloc(); AVFrame *fr = av_frame_alloc(); int16_t *out = NULL;
    if (!c || !pk || !fr) goto done;
    av_channel_layout_default(&c->ch_layout, 1); c->sample_rate = s->rate;
    if (avcodec_open2(c, codec, NULL) < 0 || av_new_packet(pk, 8 + end - pos) < 0) goto done;
    unsigned padded = (s->samples + 27) / 28 * 28;
    for (unsigned j = 0; j < 4; ++j) pk->data[j] = padded >> (8 * j);
    memset(pk->data + 4, 0, 4); memcpy(pk->data + 8, pc + pos, end - pos);
    if (avcodec_send_packet(c, pk) < 0 || avcodec_receive_frame(c, fr) < 0 || fr->nb_samples < s->samples) goto done;
    out = malloc((size_t)s->samples * 2);
    if (out) memcpy(out, fr->extended_data[0], (size_t)s->samples * 2);
done:
    av_frame_free(&fr); av_packet_free(&pk); avcodec_free_context(&c);
    return out;
}

// Compare content across lossy codecs/rates after a 15-PC-sample box filter. Search encoder delay +/-128 PC samples,
// with at most 4096 points across the entire duration (not a matching prefix). Exact lengths/loops are checked apart.
static double *smooth(const int16_t *p, unsigned frames, unsigned stride, unsigned ch, unsigned radius) {
    double *o = calloc(frames, sizeof *o), sum = 0;
    if (!o) return NULL;
    for (unsigned i = 0; i < frames; ++i) {
        sum += p[i * stride + ch];
        if (i >= 2 * radius + 1) sum -= p[(i - 2 * radius - 1) * stride + ch];
        if (i >= radius) o[i - radius] = sum / (2 * radius + 1);
    }
    return o;
}
static double identity(const int16_t *pc, unsigned pn, unsigned pr, const int16_t *x, const X360AbkSound *s, unsigned ch) {
    double *a = smooth(pc, pn, 1, 0, 7), *b = smooth(x, s->samples, s->channels, ch, (unsigned)((15.0 * s->rate / pr - 1) / 2 + 0.5));
    if (!a || !b) { free(a); free(b); return -1; }
    unsigned length = fmin(pn, (double)s->samples * pr / s->rate), points = length > 300 ? fmin(4096, length - 300) : 0;
    double best = -1;
    for (int lag = -128; points && lag <= 128; ++lag) {
        double dot = 0, aa = 0, bb = 0;
        for (unsigned j = 0; j < points; ++j) {
            unsigned i = 140 + (uint64_t)j * (length - 280) / points;
            double t = (double)(i + lag) * s->rate / pr;
            unsigned k = t; if (k + 1 >= s->samples) continue;
            double v = b[k] + (b[k + 1] - b[k]) * (t - k);
            dot += a[i] * v; aa += a[i] * a[i]; bb += v * v;
        }
        double v = aa && bb ? dot / sqrt(aa * bb) : 0;
        if (v > best) best = v;
    }
    free(a); free(b); return best;
}

static int allbanks(const char *xdir, const char *pdir, const char *cache) {
    walk(xdir, "");
    qsort(rels, nrel, sizeof *rels, cmp_rel);
    char reasons[16][48] = {{0}};
    unsigned rcount[16] = {0}, nr = 0, served = 0, kept = 0, sounds = 0, holders = 0, extra[5] = {0}, stereo = 0;
    size_t bytes = 0, pcm = 0, biggest = 0;
    double ms = 0, hash_ms = 0, hash_max = 0;
    double level_min = 1e9, level_max = 0, loop_min = 1e9, loop_max = 0;
    unsigned level_channels = 0, loop_channels = 0;
    static X360Abk k;
    static X360AbkPcSlot sl[513], psl[513];
    for (unsigned b = 0; b < nrel; ++b) {
        char xp[4096], pp[4096], why[256];
        snprintf(xp, sizeof xp, "%s/%s", xdir, rels[b].rel);
        size_t xs = 0, ps = 0, os = 0;
        uint8_t *x = slurp(xp, &xs), *pc = find_ci(pdir, rels[b].rel, pp, sizeof pp) ? slurp(pp, &ps) : NULL, *o = NULL;
        CHECK(x && pc);
        if (!x || !pc) { free(x); free(pc); continue; }
        X360AbkSwapInfo in;
        double t = now_ms();
        int ok = x360abk_swap_pc(pc, ps, x, xs, &o, &os, &in, why, sizeof why);
        ms += now_ms() - t;
        int g3 = strcasestr(rels[b].rel, "TRAFFIC_MB") || strcasestr(rels[b].rel, "ENV_COMMON_MB");
        if (!ok) {
            ++kept;
            printf("  kept PC  %-36s %s\n", rels[b].rel, why);
            CHECK(!o);
            unsigned r = 0, len = (unsigned)strcspn(why, ":");
            while (r < nr && strncmp(reasons[r], why, len)) ++r;
            if (r == nr && nr < 16) snprintf(reasons[nr++], sizeof reasons[0], "%.*s", len, why);
            rcount[r]++;
            free(x); free(pc);
            continue;
        }
        CHECK(!g3);   // these two differ in their sample tables; G3's pinned path serves them
        ++served; sounds += in.sounds; holders += in.placeholders; bytes += os; pcm += in.pcm_bytes;
        if (os > biggest) {
            biggest = os;
            unsigned char digest[CC_SHA256_DIGEST_LENGTH];
            hash_ms = hash_max = 0;
            for (int rep = 0; rep < 20; ++rep) {
                double start = now_ms();
                CHECK(CC_SHA256(o, (CC_LONG)os, digest));
                double elapsed = now_ms() - start;
                hash_ms += elapsed / 20;
                if (elapsed > hash_max) hash_max = elapsed;
            }
        }
        unsigned kind = extra_kind(rels[b].rel);
        if (kind) extra[kind]++;
        double min_identity = 1;
        // Independently of the builder: the rebuilt bank parses as the PC parses it, the PC's slots, each served slot is
        // codec 8 at the X360's rate with its exact declared length and not silent, each kept slot is an all-zero X360
        // sound with the PC's fields; every sample-table row of the (unchanged) modules names a present slot or none.
        unsigned cnt = 0, pcnt = 0, s8 = 0, kp = 0;
        CHECK(x360abk_parse(x, xs, &k, why, sizeof why));
        CHECK(x360abk_pc_bank(o, os, sl, 513, &cnt, why, sizeof why) && x360abk_pc_bank(pc, ps, psl, 513, &pcnt, why, sizeof why));
        CHECK(cnt == pcnt && !memcmp(o + 0x60, pc + 0x60, *(uint32_t *)(pc + 0x20) - 0x60));
        for (unsigned j = 1; j < cnt; ++j) {
            CHECK(sl[j].present == psl[j].present);
            if (!sl[j].present) continue;
            int idx = expected_sound(kind, j);
            if (idx < 0) {   // retain the PC-only placeholder, including compressed data and padding
                int16_t *p = decode_pc(pc, ps, j, 0, &psl[j]);
                CHECK(p && (psl[j].samples == 3 || psl[j].samples == 319));
                if (p) for (int f = 0; f < psl[j].samples; ++f) CHECK(p[f] == 0);
                uint32_t end = *(uint32_t *)(pc + 0x20) + *(uint32_t *)(pc + *(uint32_t *)(pc + 0x20) + 8);
                for (unsigned m = 1; m < cnt; ++m) if (psl[m].present && psl[m].data > psl[j].data && psl[m].data < end) end = psl[m].data;
                CHECK(sl[j].codec == psl[j].codec && sl[j].rate == psl[j].rate && sl[j].samples == psl[j].samples &&
                      sl[j].data + end - psl[j].data <= os && !memcmp(o + sl[j].data, pc + psl[j].data, end - psl[j].data));
                free(p); ++kp; continue;
            }
            CHECK((unsigned)idx < k.count && k.s[idx].hdr);
            if ((unsigned)idx >= k.count || !k.s[idx].hdr) continue;
            const X360AbkSound *s = &k.s[idx];
            int16_t *p = NULL; unsigned fr = 0;
            CHECK(x360abk_decode(x, s, &p, &fr, why, sizeof why) && fr == s->samples);
            if (!p) continue;
            double e = 0;
            if (sl[j].codec == 8) {
                CHECK(sl[j].rate == (int)s->rate && sl[j].samples == (int)s->samples && sl[j].channels == (int)s->channels);
                CHECK(sl[j].loop_start == (s->loop ? (int)s->loop_start : -1) && sl[j].loop_end == (s->loop ? (int)s->samples - 1 : -1));
                for (unsigned ch = 0; ch < s->channels; ++ch) {
                    uint32_t pos = stream_at(o, os, j, ch);
                    CHECK(pos && pos + 2 * (size_t)fr <= os);
                    if (!pos || pos + 2 * (size_t)fr > os) continue;
                    const int16_t *d = (const int16_t *)(o + pos);
                    if (quiet_output) for (unsigned f = 0; f < fr; ++f)
                        ((int16_t *)(o + pos))[f] = quiet_output == 2 ? 0 : d[f] / 32;
                    double ce = 0; int equal = 1;
                    for (unsigned f = 0; f < fr; ++f) { ce += (double)d[f] * d[f]; if (d[f] != p[f * s->channels + ch]) equal = 0; }
                    CHECK(ce > 0 && equal); e += ce;
                    {
                        int16_t *original = decode_pc(pc, ps, j, ch, &psl[j]);
                        CHECK(original);
                        if (original) {
                            double pe = 0;
                            for (int f = 0; f < psl[j].samples; ++f) pe += (double)original[f] * original[f];
                            double prms = sqrt(pe / psl[j].samples), rms = sqrt(ce / fr);
                            double ratio = prms ? rms / prms : 0;
                            ++level_channels;
                            if (ratio < level_min) level_min = ratio;
                            if (ratio > level_max) level_max = ratio;
                            if (race_bank(rels[b].rel) || !(ratio >= 0.5 && ratio <= 2.0))
                                printf("  level %s slot %u ch %u: PC %.1f converted %.1f ratio %.6f\n", rels[b].rel, j, ch, prms, rms, ratio);
                            CHECK(rms >= 1 && ratio >= 0.5 && ratio <= 2.0);
                            if (sl[j].loop_start >= 0) {
                                int lo = sl[j].loop_start, hi = sl[j].loop_end;
                                int plo = psl[j].loop_start, phi = psl[j].loop_end;
                                CHECK(lo <= hi && hi < (int)fr && plo >= 0 && plo <= phi && phi < psl[j].samples);
                                if (lo <= hi && hi < (int)fr && plo >= 0 && plo <= phi && phi < psl[j].samples) {
                                    double le = 0, ple = 0;
                                    for (int f = lo; f <= hi; ++f) le += (double)d[f] * d[f];
                                    for (int f = plo; f <= phi; ++f) ple += (double)original[f] * original[f];
                                    double lrms = sqrt(le / (hi - lo + 1)), plrms = sqrt(ple / (phi - plo + 1));
                                    double lr = plrms ? lrms / plrms : 0;
                                    ++loop_channels;
                                    if (lr < loop_min) loop_min = lr;
                                    if (lr > loop_max) loop_max = lr;
                                    if (race_bank(rels[b].rel) || !(lr >= 0.5 && lr <= 2.0))
                                        printf("  loop %s slot %u: PC %d..%d %.1f converted %d..%d %.1f ratio %.6f\n",
                                               rels[b].rel, j, plo, phi, plrms, lo, hi, lrms, lr);
                                    CHECK(lrms >= 1 && lr >= 0.5 && lr <= 2.0);
                                }
                            }
                            if (kind || race_bank(rels[b].rel)) {
                                CHECK(fabs((double)psl[j].samples / psl[j].rate - (double)fr / s->rate) < 0.015);
                                double v = identity(original, psl[j].samples, psl[j].rate, p, s, ch);
                                if (race_bank(rels[b].rel)) printf("  identity %s slot %u ch %u: %.6f\n", rels[b].rel, j, ch, v);
                                if (v < min_identity) min_identity = v;
                                // Race diagnostics include a high-frequency turbo loop (0.832511 after filtering).
                                // The exceptional-map identity bounds remain the established content proof.
                                if (kind) CHECK(v > (kind == 4 ? 0.55 : 0.90));
                            }
                        }
                        free(original);
                    }
                }
                if (s->channels == 2) { CHECK(stream_at(o, os, j, 1) == sl[j].data + 2 * fr); stereo++; }
                ++s8;
            } else {
                for (unsigned f = 0; f < fr * s->channels; ++f) e += (double)p[f] * p[f];
                CHECK(e == 0 && sl[j].codec == psl[j].codec && sl[j].rate == psl[j].rate && sl[j].samples == psl[j].samples);
                ++kp;
            }
            free(p);
        }
        if (kind) {
            // A changed file with the same structural difference has no proven mapping: CRC pins must refuse it.
            pc[0x5f] ^= 1;
            CHECK(!swap_ok(pc, ps, x, xs, why, sizeof why));
            pc[0x5f] ^= 1;
            x[0x5f] ^= 1;
            CHECK(!swap_ok(pc, ps, x, xs, why, sizeof why));
            x[0x5f] ^= 1;
            if (kind == 3) {
                int16_t *original = decode_pc(pc, ps, 1, 0, &psl[1]), *wrong = NULL; unsigned fr;
                CHECK(original && x360abk_decode(x, &k.s[0], &wrong, &fr, why, sizeof why));
                if (original && wrong) {
                    double v = identity(original, psl[1].samples, psl[1].rate, wrong, &k.s[0], 0);
                    printf("  negative: %s unswapped sound 0 -> PC slot 1 identity %.6f\n", rels[b].rel, v);
                    CHECK(v < 0.5);
                }
                free(original); free(wrong);
            }
        }
        if (kind) printf("  mapped %-36s %u sounds, %u kept, minimum filtered identity %.6f\n", rels[b].rel, s8, kp, min_identity);
        CHECK(s8 == in.sounds && kp == in.placeholders);
        uint32_t F2 = *(uint32_t *)(o + 0x34);
        for (unsigned i = 0, np = *(uint32_t *)(o + F2); i < np; ++i) {
            uint32_t t2 = *(uint32_t *)(o + *(uint32_t *)(o + F2 + 4 + 4 * i));
            if (t2 + 4 > *(uint32_t *)(o + 0x20) || *(uint32_t *)(o + t2 + 4) != 0xff00) continue;   // not a sample table
            for (unsigned r = 0, rows = *(uint32_t *)(o + t2); r < rows && rows < 256; ++r) {
                uint32_t slot = *(uint32_t *)(o + t2 + 8 + 12 * r);
                CHECK(slot < cnt && (!slot || sl[slot].present));
            }
        }
        free(o); free(x); free(pc);
    }
    printf("allbanks: %u banks: %u served X360, %u kept PC (", nrel, served, kept);
    for (unsigned r = 0; r < nr; ++r) printf("%s%s %u", r ? ", " : "", reasons[r], rcount[r]);
    printf("); %u sounds at source rates, %u placeholders kept PC; rebuilt %.1f MB (%.1f MB PCM16, largest bank %.1f MB); convert %.0f ms in all\n",
           sounds, holders, bytes / 1e6, pcm / 1e6, biggest / 1e6, ms);
    printf("levels: %u channels RMS/PC %.6f..%.6f; %u loop channels %.6f..%.6f (bound 0.5..2.0, RMS >= 1)%s\n",
           level_channels, level_min, level_max, loop_channels, loop_min, loop_max,
           quiet_output == 2 ? " [silent PCM negative control]" : quiet_output ? " [PCM /32 negative control]" : "");
    CHECK(nrel == 301 && served == 299 && kept == 2 && sounds == 2514 && holders == 50);
    CHECK(extra[1] == 14 && extra[2] == 5 && extra[3] == 3 && extra[4] == 2 && stereo == 40);   // the retail pair's verdicts (2026-10-25)

    printf("cache SHA256: largest rebuilt bank %zu bytes, 20 hashes mean %.3f ms max %.3f ms (first-open path)\n", biggest, hash_ms, hash_max);
    // Serve path on one bank: convert + cache, then from the cache, a damaged cache file converted again, an unwritable
    // cache served from a temp file, a damaged X360 file refused.
    const char *rel = "ENGINE/CAR_00_ENG_MB_EE.ABK";
    char xp[4096], pp[4096], why[256], cp[4200];
    CHECK(find_ci(xdir, rel, xp, sizeof xp) && find_ci(pdir, rel, pp, sizeof pp));
    size_t xs = 0, ps = 0, os = 0, gs = 0;
    uint8_t *x = slurp(xp, &xs), *pc = slurp(pp, &ps), *o = NULL;
    X360AbkSwapInfo in;
    CHECK(x360abk_swap_pc(pc, ps, x, xs, &o, &os, &in, why, sizeof why));
    snprintf(cp, sizeof cp, "%s/%s_%08lX_%08lX_v1.abk", cache, "CAR_00_ENG_MB_EE", crc32(0, pc, ps), crc32(0, x, xs));
    unlink(cp);
    X360AbkServe st;
    const char *cases[] = {"cold", "warm", "truncated", "zero PCM", "missing digest", "damaged digest"};
    for (int pass = 0; pass < 6; ++pass) {
        if (pass == 2) truncate(cp, 1000);   // a damaged cache file
        if (pass == 3) {
            uint8_t *bad = slurp(cp, &gs);
            unsigned count = 0;
            CHECK(bad && gs == os && x360abk_pc_bank(bad, gs, sl, 513, &count, why, sizeof why));
            for (unsigned s = 0; s < count; ++s) if (sl[s].present && sl[s].codec == 8)
                memset(bad + sl[s].data, 0, (size_t)sl[s].samples * 2);
            // Prove the prior production validator accepts the damaged PCM, independent of the new digest.
            int old_accepts = *(uint32_t *)(bad + 0x14) == gs && x360abk_pc_bank(bad, gs, sl, 513, &count, why, sizeof why);
            CHECK(old_accepts && memcmp(bad, o, os));
            printf("  zero PCM: old structural validator %s (FAIL: accepts corruption)\n", old_accepts ? "accepts" : "rejects");
            FILE *f = fopen(cp, "r+b"); CHECK(f && fwrite(bad, 1, gs, f) == gs); if (f) fclose(f);
            free(bad);
        }
        if (pass == 4) removexattr(cp, "com.nfsmw.converter.sha256.v1", 0);
        if (pass == 5) {
            unsigned char zero[32] = {0};
            CHECK(!setxattr(cp, "com.nfsmw.converter.sha256.v1", zero, sizeof zero, 0, 0));
        }
        int fd = x360abk_serve(pp, xp, cache, &st, why, sizeof why);
        uint8_t *g = NULL;
        if (fd >= 0) { g = malloc(os + 1); gs = read(fd, g, os + 1); close(fd); }
        printf("  serve %s: fd %s, %s, %.1f ms (%s)\n", cases[pass], fd >= 0 ? "ok" : "-1",
               st.cached ? "from the cache" : "converted", st.ms, why);
        CHECK(fd >= 0 && gs == os && !memcmp(g, o, os) && st.cached == (pass == 1));
        free(g);
    }
    struct stat sb;
    CHECK(!stat(cp, &sb) && (size_t)sb.st_size == os);
    int fd = x360abk_serve(pp, xp, "/dev/null/nocache", &st, why, sizeof why);
    printf("  serve, cache not writable: fd %s (%s)\n", fd >= 0 ? "ok" : "-1", why);
    CHECK(fd >= 0 && !st.cached);
    if (fd >= 0) close(fd);
    // Negatives: each must be refused (structural; compressed-byte corruption that still decodes is not detectable
    // without pins, which A10 deliberately does not use).
    CHECK(x360abk_parse(x, xs, &k, why, sizeof why));
    uint32_t bank = k.bank, s1 = k.s[1].hdr - bank;
    uint8_t *m = malloc(xs + ps);
    struct { const char *what; int pcside; uint32_t off; uint8_t xor; size_t cut; } neg[] = {
        {"X360 S10A count corrupted", 0, bank + 11, 0x40, 0},
        {"X360 sound 1 header offset corrupted", 0, bank + 12 + 4 + 2, 0x80, 0},
        {"X360 sound 0 packet 1 sequence nibble broken", 0, k.s[0].data + 12 + 2048, 0x30, 0},
        {"X360 file truncated by one packet", 0, 0, 0, 2048},
        {"X360 ABKC size field corrupted", 0, 0x17, 1, 0},
        {"PC file truncated", 1, 0, 0, 4},
        {"PC sample table row re-pointed", 1, 0, 0, 0},
    };
    (void)s1;
    for (unsigned j = 0; j < sizeof neg / sizeof neg[0]; ++j) {
        uint8_t *mx = m, *mp = m + xs;
        memcpy(mx, x, xs); memcpy(mp, pc, ps);
        size_t mxs = xs, mps = ps;
        if (neg[j].pcside) mps -= neg[j].cut; else mxs -= neg[j].cut;
        if (neg[j].xor) (neg[j].pcside ? mp : mx)[neg[j].off] ^= neg[j].xor;
        if (j == 6) {   // first sample table's first used row: name the next slot
            uint32_t F2 = *(uint32_t *)(mp + 0x34);
            for (unsigned i = 0, np = *(uint32_t *)(mp + F2); i < np; ++i) {
                uint32_t t2 = *(uint32_t *)(mp + *(uint32_t *)(mp + F2 + 4 + 4 * i));
                if (t2 + 16 > *(uint32_t *)(mp + 0x20) || *(uint32_t *)(mp + t2 + 4) != 0xff00) continue;
                *(uint32_t *)(mp + t2 + 8) += 1;
                break;
            }
        }
        int ok = swap_ok(mp, mps, mx, mxs, why, sizeof why);
        printf("  negative: %s -> %s\n", neg[j].what, ok ? "ACCEPTED" : why);
        CHECK(!ok);
    }
    // Served end to end: a damaged X360 file gives -1, so the game opens the PC file.
    char bad[4200];
    snprintf(bad, sizeof bad, "%s/damaged_CAR_00_ENG_MB_EE.ABK", cache);
    FILE *f = fopen(bad, "wb");
    memcpy(m, x, xs); m[bank + 11] ^= 0x40;
    if (f) { fwrite(m, 1, xs, f); fclose(f); }
    fd = x360abk_serve(pp, bad, cache, &st, why, sizeof why);
    printf("  serve, damaged X360 file: fd %d (%s)\n", fd, why);
    CHECK(fd < 0);
    unlink(bad);
    free(m); free(o); free(x); free(pc);
    return 0;
}

int main(int argc, char **argv) {
    if (argc == 4 && !strcmp(argv[1], "g3")) {
        g3(argv[2], argv[3]);
        printf("%s: %u checks, %u failures\n", failures ? "FAIL" : "PASS", checks, failures);
        return failures != 0;
    }
    if (argc == 5 && (!strcmp(argv[1], "allbanks") || !strcmp(argv[1], "allbanks-quiet") || !strcmp(argv[1], "allbanks-silent"))) {
        quiet_output = !strcmp(argv[1], "allbanks-silent") ? 2 : !strcmp(argv[1], "allbanks-quiet");
        allbanks(argv[2], argv[3], argv[4]);
        printf("%s: %u checks, %u failures\n", failures ? "FAIL" : "PASS", checks, failures);
        return failures != 0;
    }
    if (argc == 5 && !strcmp(argv[1], "inventory")) return inventory(argv[2], argv[3], argv[4]);
    fprintf(stderr, "usage: x360abk_test g3 <X360 SOUND> <PC SOUND> | inventory <X360 SOUND> <PC SOUND> <out.md> | "
                    "allbanks[-quiet|-silent] <X360 SOUND> <PC SOUND> <scratch cache>\n");
    return 2;
}
