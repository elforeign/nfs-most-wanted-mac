// x360_textures.m - X360 HD textures swapped in for the PC ones.
//
// HD textures (X360 assets): TextureInfoPlatInterface::Init (006ddd90) creates every pack texture's D3D texture and
// copies its mips. When the X360 set (NFS_X360_TEXTURES, default hd_textures_mips_alpha in the assets root, else
// hd_textures_mips; made by tools_snapshot/x360_tex.py --mips --pc-alpha --tone-match) has a larger version of the
// texture's name hash, Init runs with that texture's size, format, mip count and data, and the TextureInfo is restored
// afterwards. On when the folder exists; NFS_X360_TEXTURES=off disables it, NFS_X360_TEXTURES=<folder> picks another
// set. Skipped: the DUMMY_SKIN composite targets (the car skin compositor writes them at PC size) and swaps that
// would drop alpha the PC texture has (PC DXT3/DXT5/A8R8G8B8 -> X360 DXT1). Every swap is checked first: the X360 mip
// at the PC size is decoded and compared with the PC texture (mean RGB difference <= 12, alpha <= 16 of 255); a
// texture that differs (garbled conversions, different content or channel use) keeps the PC version. Where the PC
// keeps a mask in alpha (specular, blend weights, cut-outs) that the X360 version lacks, the set has X360 colour + PC
// alpha (DXT5); X360 art regraded for the X360 lighting is in the PC's tones, with the X360 grading in
// <set>/x360_tone/ for NFS_X360_TEXTURES_TONE=x360 (default pc).
// Private NFS_LATER_TEXTURES: exactly six owner-approved 2026-10-28 payloads,
// pinned below. Changed art uses those pins instead of MW RGB similarity;
// native tyre aliases preserve the MW rim UV region. All other art stays MW.
#include <dirent.h>
#include <dispatch/dispatch.h>
#include <fcntl.h>
#include <math.h>
#include <mach-o/dyld.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "x360_internal.h"
#include "roots.h"
#include "settings.h"
#include "hd_file_cache.h"

// state: what the first look at this texture decided, for good (streamed textures load again and again)
enum { HD_NEW, HD_SWAP, HD_KEEP };                         // not looked at yet / swap it / keep the PC texture
typedef struct { uint32_t hash; char *path; int state; uint32_t ww, hh, mips, fmt; } HdTex;   // file header facts once looked at
int x360_hd_creating;
static HdTex *hd, *hd_tone;               // the set; its X360-tone variants (<set>/x360_tone/)
static HdTex *hd_later;                  // private built-in overlay, preferred over the MW set
static int nhd_later;
static uint32_t hd_later_loaded;
static uint32_t hd_later_applied_mask;
static uint64_t hd_sum(const void *p, size_t n);
#ifndef NFS_PUBLIC
#include "later_textures_private.h"
#else
static int later_rule(uint32_t hash) { (void)hash; return -1; }
static int later_tire(uint32_t hash, const char *name) { (void)hash; (void)name; return 0; }
static int later_pin(const HdTex *hx, const uint8_t *p, size_t size) {
    (void)hx; (void)p; (void)size; return 0;
}
#endif
static int nhd = -1, nhd_tone;
static uint32_t hd_loaded, hd_bytes, hd_tone_loaded;
// The private builder emits a complete chain, with explicit BGRA alpha masks.
// Validate before hd_verify (which assumes sane dimensions), including cached reads.
static int later_header(const uint8_t *p, size_t size) {
    if (size < 128 || memcmp(p, "DDS ", 4)) return 0;
    uint32_t d[32]; memcpy(d, p, 128);
    uint32_t w = d[4], h = d[3], mips = d[7], fmt = d[21];
    if (d[1] != 124 || d[19] != 32 || w < 4 || h < 4 || w > 4096 || h > 4096 ||
        (w & (w - 1)) || (h & (h - 1))) return 0;
    int rgba = !fmt && d[20] == 0x41 && d[22] == 32 && d[23] == 0xFF0000 &&
        d[24] == 0xFF00 && d[25] == 0xFF && d[26] == 0xFF000000;
    if (!rgba && (d[20] != 4 || (fmt != 0x31545844 && fmt != 0x33545844 && fmt != 0x35545844))) return 0;
    size_t need = 128; uint32_t levels = 0;
    for (;;) {
        need += rgba ? (size_t)w * h * 4 : (size_t)((w + 3) / 4) * ((h + 3) / 4) * (fmt == 0x31545844 ? 8 : 16);
        ++levels;
        if (w == 1 && h == 1) break;
        w = w > 1 ? w / 2 : 1; h = h > 1 ? h / 2 : 1;
    }
    return levels == mips && need == size;
}
static int hd_list(const char *dir, HdTex **out, int n) {    // <hash>_<name>.dds files in dir, appended after the n entries in *out
    DIR *d = opendir(dir);
    if (!d) return n ? n : -1;
    struct dirent *ent;
    int cap = n;
    while ((ent = readdir(d))) {
        size_t len = strlen(ent->d_name);
        if (len < 13 || strcasecmp(ent->d_name + len - 4, ".dds") || ent->d_name[8] != '_') continue;
        if (n == cap) { cap = cap ? cap * 2 : 1024; *out = realloc(*out, (size_t)cap * sizeof **out); }
        (*out)[n].hash = (uint32_t)strtoul(ent->d_name, NULL, 16);
        (*out)[n].state = HD_NEW;
        asprintf(&(*out)[n].path, "%s/%s", dir, ent->d_name);
        ++n;
    }
    closedir(d);
    return n;
}
// Private defaults follow the executable, never the cwd or the selected game folder.
static const char *later_root(char *out, size_t size) {
#ifdef NFS_PUBLIC
    (void)out; (void)size;
    return NULL;                         // other games' payloads are private, even with an override
#else
    const char *e = getenv("NFS_LATER_TEXTURES");
    if (e && (!strcmp(e, "off") || !strcmp(e, "0"))) return NULL;
    if (e && *e) snprintf(out, size, "%s", e);
    else {
        char path[4096], absolute[4096];
        uint32_t n = sizeof path;
        if (_NSGetExecutablePath(path, &n) || !realpath(path, absolute)) return NULL;
        char *slash = strrchr(absolute, '/');
        if (!slash) return NULL;
        *slash = 0;
        const char *macos = "/Contents/MacOS";
        size_t len = strlen(absolute), suffix = strlen(macos);
        if (len >= suffix && !strcmp(absolute + len - suffix, macos))
            snprintf(out, size, "%s/../Resources/later_textures", absolute);
        else
            snprintf(out, size, "%s/../recomp/out/later_textures", absolute);
    }
    struct stat st;
    return !stat(out, &st) && S_ISDIR(st.st_mode) ? out : NULL;
#endif
}
static void hd_scan(void) {
    nhd = 0;
    const char *e = getenv("NFS_X360_TEXTURES");
    if (e && (!strcmp(e, "off") || !strcmp(e, "0"))) return;
    char dir[1024];
    struct stat st;
    if (e && strcmp(e, "on") && strcmp(e, "1")) snprintf(dir, sizeof dir, "%s", e);
    else {                                                  // the set with the PC alpha masks, else the earlier one
        roots_asset_dir("hd_textures_mips_alpha", dir, sizeof dir);
        if (stat(dir, &st)) roots_asset_dir("hd_textures_mips", dir, sizeof dir);
    }
    int n = hd_list(dir, &hd, 0);
    const char *ui = getenv("NFS_X360_UI_TEXTURES");        // the same-size UI set (hd_textures_ui): after the main set, which wins on a shared hash
    int nmain = n;
    if (!ui || (strcmp(ui, "off") && strcmp(ui, "0"))) {
        char uidir[1100];
        roots_asset_dir("hd_textures_ui", uidir, sizeof uidir);
        n = hd_list(uidir, &hd, n < 0 ? 0 : n);
        if (n > (nmain < 0 ? 0 : nmain)) fprintf(stderr, "boot: %d X360 UI textures (same size, better format) in %s\n", n - (nmain < 0 ? 0 : nmain), uidir);
    }
    if (n < 0 && diag_on) diag_event("x360: no HD texture folder %s", dir);
    nhd = n < 0 ? 0 : n;
    char later_dir[4096];
    const char *later = later_root(later_dir, sizeof later_dir);
    if (later) {
        nhd_later = hd_list(later, &hd_later, 0);
        if (nhd_later < 0) nhd_later = 0;
        if (diag_on) diag_event("later: %d overlay textures in %s", nhd_later, later);
    }
    const char *tone = getenv("NFS_X360_TEXTURES_TONE");
    if (tone && !strcmp(tone, "x360")) {
        char sub[1100];
        snprintf(sub, sizeof sub, "%s/x360_tone", dir);
        nhd_tone = hd_list(sub, &hd_tone, 0);
        if (nhd_tone < 0) nhd_tone = 0;
    }
    fprintf(stderr, "boot: %d X360 HD textures in %s%s\n", nmain < 0 ? 0 : nmain, dir, nhd_tone ? " (X360 tones)" : "");
    if (nhd_tone) fprintf(stderr, "boot: %d of them in X360 tones (NFS_X360_TEXTURES_TONE=x360)\n", nhd_tone);
}

// ---- validation: the X360 mip at the PC texture's size must look like the PC texture ----
static void rgb565(uint32_t c, int *o) { o[0] = (c >> 11 & 31) * 255 / 31; o[1] = (c >> 5 & 63) * 255 / 63; o[2] = (c & 31) * 255 / 31; }
// Decodes one level to RGBA8 (w*h*4). fmt: DXT1/3/5 FourCC, 21/22 (A8R8G8B8/X8R8G8B8), 0x29 P8 with a BGRA palette.
static int decode_ok(uint32_t fmt, uint32_t w, uint32_t h, const uint8_t *pal) {
    if (fmt == 21 || fmt == 22) return 1;
    if (fmt == 0x29) return pal != NULL;
    return (fmt == 0x31545844 || fmt == 0x33545844 || fmt == 0x35545844) && w >= 4 && h >= 4;
}
static uint32_t decode_nrows(uint32_t fmt, uint32_t h) { return fmt == 21 || fmt == 22 || fmt == 0x29 ? h : h / 4; }   // pixel rows, or block rows
// Rows r0..r1 of decode_nrows() of the level (what decode_level does whole; bands of a level decode on separate threads).
static int decode_rows(uint32_t fmt, uint32_t w, uint32_t h, const uint8_t *src, const uint8_t *pal, uint8_t *out, uint32_t r0, uint32_t r1) {
    if (fmt == 21 || fmt == 22) {
        for (uint32_t i = r0 * w; i < r1 * w; ++i) { out[4 * i] = src[4 * i + 2]; out[4 * i + 1] = src[4 * i + 1]; out[4 * i + 2] = src[4 * i]; out[4 * i + 3] = fmt == 22 ? 255 : src[4 * i + 3]; }
        return 1;
    }
    if (fmt == 0x29) {
        if (!pal) return 0;
        for (uint32_t i = r0 * w; i < r1 * w; ++i) { const uint8_t *p = pal + 4 * src[i]; out[4 * i] = p[2]; out[4 * i + 1] = p[1]; out[4 * i + 2] = p[0]; out[4 * i + 3] = p[3]; }
        return 1;
    }
    int bpb = fmt == 0x31545844 ? 8 : (fmt == 0x33545844 || fmt == 0x35545844) ? 16 : 0;
    if (!bpb || w < 4 || h < 4) return 0;
    for (uint32_t by = r0; by < r1; ++by)
        for (uint32_t bx = 0; bx < w / 4; ++bx) {
            const uint8_t *b = src + (by * (w / 4) + bx) * (uint32_t)bpb, *cb = b + bpb - 8;
            uint32_t c0 = cb[0] | cb[1] << 8, c1 = cb[2] | cb[3] << 8, idx = cb[4] | cb[5] << 8 | cb[6] << 16 | (uint32_t)cb[7] << 24;
            int p[4][4];
            rgb565(c0, p[0]); rgb565(c1, p[1]);
            int four = c0 > c1 || bpb == 16;
            for (int k = 0; k < 3; ++k) {
                p[2][k] = four ? (2 * p[0][k] + p[1][k]) / 3 : (p[0][k] + p[1][k]) / 2;
                p[3][k] = four ? (p[0][k] + 2 * p[1][k]) / 3 : 0;
            }
            p[0][3] = p[1][3] = p[2][3] = 255; p[3][3] = four ? 255 : 0;
            int at[8] = {0};
            uint64_t abits = 0;
            if (fmt == 0x35545844) {
                at[0] = b[0]; at[1] = b[1];
                for (int k = 2; k < 8; ++k) at[k] = at[0] > at[1] ? ((8 - k) * at[0] + (k - 1) * at[1]) / 7 : k < 6 ? ((6 - k) * at[0] + (k - 1) * at[1]) / 5 : k == 6 ? 0 : 255;
                for (int k = 0; k < 6; ++k) abits |= (uint64_t)b[2 + k] << (8 * k);
            }
            for (int i = 0; i < 16; ++i) {
                uint8_t *o = out + 4 * ((by * 4 + (uint32_t)i / 4) * w + bx * 4 + (uint32_t)i % 4);
                int *q = p[idx >> (2 * i) & 3];
                o[0] = (uint8_t)q[0]; o[1] = (uint8_t)q[1]; o[2] = (uint8_t)q[2];
                o[3] = fmt == 0x33545844 ? (uint8_t)((b[i / 2] >> (4 * (i % 2)) & 15) * 17) : fmt == 0x35545844 ? (uint8_t)at[abits >> (3 * i) & 7] : (uint8_t)q[3];
            }
        }
    return 1;
}
static int decode_level(uint32_t fmt, uint32_t w, uint32_t h, const uint8_t *src, const uint8_t *pal, uint8_t *out) {
    return decode_rows(fmt, w, h, src, pal, out, 0, decode_nrows(fmt, h));
}
// Diag: <dir>/hd_compare/<hash>_<verdict>_<name>.png = PC colour | X360 colour | PC alpha | X360 alpha (RGBA8 inputs).
static void hd_compare_png(uint32_t hash, const char *name, int verdict, const uint8_t *pc, const uint8_t *x, uint32_t w, uint32_t h) {
    char path[1024];
    snprintf(path, sizeof path, "%s/hd_compare", diag_dir);
    mkdir(path, 0755);
    snprintf(path + strlen(path), sizeof path - strlen(path), "/%08X_%s_%.24s.png", hash, verdict > 0 ? "swap" : "differs", name);
    uint8_t *img = malloc((size_t)w * h * 16);
    for (uint32_t y = 0; y < h; ++y)
        for (uint32_t i = 0; i < w; ++i) {
            const uint8_t *src[2] = {pc + 4 * (y * w + i), x + 4 * (y * w + i)};
            for (int k = 0; k < 4; ++k) {
                uint8_t *o = img + 4 * ((size_t)y * w * 4 + k * w + i), *s = (uint8_t *)src[k & 1];
                if (k < 2) { o[0] = s[2]; o[1] = s[1]; o[2] = s[0]; } else o[0] = o[1] = o[2] = s[3];
                o[3] = 255;
            }
        }
    diag_png(path, img, w * 4, h, w * 16, 0);
    free(img);
}
// 1: swap, 0: keep PC (no level at the PC size, or the images differ), -1: can't tell (keep PC). img: the whole DDS file.
static uint8_t *scratch(int k, size_t need) {              // the check's pixel buffers: kept between textures (big mallocs page-fault afresh each time)
    static uint8_t *buf[3]; static size_t cap[3];
    if (cap[k] < need) { free(buf[k]); buf[k] = malloc(cap[k] = need); }
    return buf[k];
}
static int hd_verify(uint32_t ti, uint32_t pi, const uint8_t *img, size_t imgsize, uint32_t ww, uint32_t hh, uint32_t mips, uint32_t fmt, float *out_rgb, float *out_a) {
    *out_rgb = *out_a = -1;                                 // -1: no level to compare
    uint32_t pw = M16(ti + 0x44), ph = M16(ti + 0x46), pcfmt = M32(pi + 0x14), hash = M32(ti + 0x24);
    uint32_t w = ww, h = hh, off = 128, l = 0;
    int bpb = fmt == 0x31545844 ? 8 : fmt == 21 ? 0 : 16;
    // the smallest level covering the PC size; when the X360 texture has another aspect (2x on one side), that level
    // is box-filtered down to the PC size along its longer side before comparing
    for (; l < mips && w > pw && h > ph; ++l) {
        off += fmt == 21 ? w * h * 4 : ((w + 3) / 4) * ((h + 3) / 4) * (uint32_t)bpb;
        w = w > 1 ? w / 2 : 1; h = h > 1 ? h / 2 : 1;
    }
    if (l == mips || w < pw || h < ph || w % pw || h % ph || pw < 4 || ph < 4 || pw > 2048 || ph > 2048) {
        if (diag_on) diag_event("x360: HD texture %08X %s %ux%u -> %ux%u: no level covering the PC size, kept", hash, (const char *)G + ti + 0x0c, pw, ph, ww, hh);
        return 0;
    }
    uint32_t fx = w / pw, fy = h / ph;
    size_t lsz = fmt == 21 ? (size_t)w * h * 4 : (size_t)(w / 4) * (h / 4) * (size_t)bpb;
    const uint8_t *pcpal = M32(ti + 0x74) ? G + M32(ti + 0x74) : NULL;
    int ok = off + lsz <= imgsize && decode_ok(fmt, w, h, NULL) && decode_ok(pcfmt, pw, ph, pcpal);
    uint8_t *a = NULL, *b = NULL;
    double drgb = 0, da = 0;
    if (!ok) *out_rgb = *out_a = 0;
    if (ok) {
        // A few hundred textures of ~100K pixels per track load: the level is cut into bands of rows (multiples of 4
        // PC rows, so the block rows line up) that decode, box filter and compare on the global queue while the game
        // thread waits; the comparison sums are integers, so the result is the one a single thread gets.
        enum { MAXB = 8 };
        uint32_t gran = ph % 4 ? 1 : ph / 4, nb = (size_t)w * h >= 32768 ? MAXB : 1;
        if (nb > gran) nb = gran;
        a = scratch(0, (size_t)w * h * 4); b = scratch(1, (size_t)pw * ph * 4);
        uint8_t *f = fx > 1 || fy > 1 ? scratch(2, (size_t)pw * ph * 4) : a;      // the X360 level at the PC size
        uint64_t (*sums)[2] = calloc(MAXB, sizeof *sums);
        const uint8_t *src = img + off, *pcsrc = G + M32(ti + 0x70);
        dispatch_apply(nb, dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^(size_t band) {
            uint32_t y0 = nb == 1 ? 0 : 4 * (gran * (uint32_t)band / nb), y1 = nb == 1 ? ph : 4 * (gran * ((uint32_t)band + 1) / nb);
            uint32_t xr0 = fmt == 21 || fmt == 22 ? y0 * fy : y0 * fy / 4, xr1 = y1 == ph ? decode_nrows(fmt, h) : fmt == 21 || fmt == 22 ? y1 * fy : y1 * fy / 4;
            uint32_t pr0 = pcfmt == 21 || pcfmt == 22 || pcfmt == 0x29 ? y0 : y0 / 4, pr1 = y1 == ph ? decode_nrows(pcfmt, ph) : pcfmt == 21 || pcfmt == 22 || pcfmt == 0x29 ? y1 : y1 / 4;
            decode_rows(fmt, w, h, src, NULL, a, xr0, xr1);
            decode_rows(pcfmt, pw, ph, pcsrc, pcpal, b, pr0, pr1);
            if (f != a)                                     // box filter
                for (uint32_t y = y0; y < y1; ++y)
                    for (uint32_t xx = 0; xx < pw; ++xx) {
                        uint32_t sum[4] = {0, 0, 0, 0};
                        for (uint32_t j = 0; j < fy; ++j) {
                            const uint8_t *p = a + 4 * ((y * fy + j) * w + xx * fx);
                            for (uint32_t i = 0; i < fx; ++i, p += 4) { sum[0] += p[0]; sum[1] += p[1]; sum[2] += p[2]; sum[3] += p[3]; }
                        }
                        for (int k = 0; k < 4; ++k) f[4 * (y * pw + xx) + k] = (uint8_t)((sum[k] + fx * fy / 2) / (fx * fy));
                    }
            uint64_t srgb = 0, sa = 0;
            for (size_t i = (size_t)y0 * pw; i < (size_t)y1 * pw; ++i) {
                srgb += (uint32_t)abs(f[4 * i] - b[4 * i]) + (uint32_t)abs(f[4 * i + 1] - b[4 * i + 1]) + (uint32_t)abs(f[4 * i + 2] - b[4 * i + 2]);
                sa += (uint32_t)abs(f[4 * i + 3] - b[4 * i + 3]);
            }
            sums[band][0] = srgb; sums[band][1] = sa;
        });
        for (uint32_t k = 0; k < nb; ++k) { drgb += (double)sums[k][0]; da += (double)sums[k][1]; }
        free(sums);
        a = f;
        drgb /= 3.0 * pw * ph; da /= (double)pw * ph;
        *out_rgb = (float)drgb; *out_a = (float)da;
    }
    int verdict = !ok ? -1 : (drgb <= 12.0 && da <= 16.0) ? 1 : 0;
    static int compare_png = -1;                            // BOOT_DIAG_HDCOMPARE=1: the hd_compare/ images (~300 per track)
    if (compare_png < 0) compare_png = getenv("BOOT_DIAG_HDCOMPARE") != NULL;
    if (diag_on && ok && compare_png) hd_compare_png(hash, (const char *)G + ti + 0x0c, verdict, b, a, pw, ph);
    if (diag_on) diag_event("x360: HD texture %08X %s %ux%u -> %ux%u: rgb diff %.1f alpha diff %.1f -> %s", hash, (const char *)G + ti + 0x0c,
                            pw, ph, ww, hh, drgb, da, verdict > 0 ? "swap" : verdict == 0 ? "kept (differs)" : "kept (can't compare)");
    return verdict;
}
// ---------------- X360-only textures ----------------
// Textures the X360 data has and the PC's packs lack. GetTextureInfo (00503400) looks a hash up in the loaded packs,
// then the render targets, then the extra-texture list OtherEcstacyTextures (names 009159A0, TextureInfo* 00915A30,
// count 00915FA8; FUN_004FAF10 adds one), which nothing fills on PC; a texture found nowhere draws the default texture.
// x360_extra_texture reads one from an uncompressed TPK in the X360 files (NFS_X360_EXTRA_TEXTURES, default
// the roots.txt X360 root; =off disables them), untiles it and undoes the GPU byte swap (as tools_snapshot/x360_tex.py),
// builds its TextureInfo and platform record from the X360's own (the same 124 + 32-byte layout on both, with the
// same compression codes at +0x4A; the platform record gets the PC's version fields 5, 6 and D3D format), creates the
// D3D texture with TextureInfoPlatInterface::Init, and adds it to that list. Top level only (no mips: the FE draws
// these about 1:1). They are made at the first Init, when the first pack loads at start-up.
#define FN_ADD_OTHER_TEXTURE 0x004FAF10u   // cdecl (uint hash, TextureInfo *)
static const struct { const char *file; uint32_t hash; const char *name; } extra_list[] = {
    {"GLOBAL/GLOBALB.BUN", 0x524FAC6Bu, "WS_XENON_SPLASHBACK"},   // XENON_WS wide FE backdrop, 1024x512 DXT5 (widescreen.c)
};
static uint32_t extra_ti[sizeof extra_list / sizeof *extra_list];
static int extra_done;
static uint32_t tiled_offset(uint32_t x, uint32_t y, uint32_t width, uint32_t log2) {   // XGAddress2DTiledOffset, in blocks
    uint32_t aligned = (width + 31) & ~31u;
    uint32_t macro = ((x >> 5) + (y >> 5) * (aligned >> 5)) << (log2 + 7);
    uint32_t micro = ((x & 7) + ((y & 6) << 2)) << log2;
    uint32_t off = macro + ((micro & ~15u) << 1) + (micro & 15) + ((y & 8) << (3 + log2)) + ((y & 1) << 4);
    return (((off & ~511u) << 3) + ((off & 448) << 2) + (off & 63) + ((y & 16) << 7) + (((((y & 8) >> 2) + (x >> 3)) & 3) << 6)) >> log2;
}
// The TPK texture record (TextureInfo, platform record) and the file offset of its top level, searched in the chunk
// tree from pos to end: TPKs (B3300000) hold B3310000 (33310004 TextureInfos, 33310005 platform records) and B3320000
// (33320002/3 pixels, after 0x11 padding that ends 4 KB aligned relative to the TPK, as the X360 loads it).
static int tpk_find(FILE *f, long pos, long end, uint32_t hash, uint8_t ti[124], uint8_t pl[32], long *pixels) {
    uint32_t h[2];
    for (; pos + 8 <= end; pos += 8 + (long)h[1]) {
        if (fseek(f, pos, SEEK_SET) || fread(h, 4, 2, f) != 2 || pos + 8 + (long)h[1] > end) return 0;
        if (h[0] != 0xB3300000u) {
            if ((h[0] & 0x80000000u) && tpk_find(f, pos + 8, pos + 8 + (long)h[1], hash, ti, pl, pixels)) return 1;
            continue;
        }
        long infos = 0, plats = 0, data = 0;
        uint32_t ninfos = 0, c[2], s[2];
        for (long q = pos + 8; q + 8 <= pos + 8 + (long)h[1]; q += 8 + (long)c[1]) {
            if (fseek(f, q, SEEK_SET) || fread(c, 4, 2, f) != 2) return 0;
            if (c[0] != 0xB3310000u && c[0] != 0xB3320000u) continue;
            for (long r = q + 8; r + 8 <= q + 8 + (long)c[1]; r += 8 + (long)s[1]) {
                if (fseek(f, r, SEEK_SET) || fread(s, 4, 2, f) != 2) return 0;
                if (s[0] == 0x33310004u) { infos = r + 8; ninfos = s[1] / 124; }
                else if (s[0] == 0x33310005u) plats = r + 8;
                else if (s[0] == 0x33320002u || s[0] == 0x33320003u) data = r + 8;
            }
        }
        for (uint32_t k = 0; infos && plats && data && k < ninfos; ++k) {
            uint32_t th;
            if (fseek(f, infos + 124L * k + 0x24, SEEK_SET) || fread(&th, 4, 1, f) != 1) return 0;
            if (th != hash) continue;
            fseek(f, infos + 124L * k, SEEK_SET);
            if (fread(ti, 1, 124, f) != 124 || fseek(f, plats + 32L * k, SEEK_SET) || fread(pl, 1, 32, f) != 32) return 0;
            uint8_t pad[0x1000];
            size_t n = fseek(f, data, SEEK_SET) ? 0 : fread(pad, 1, sizeof pad, f), i = 0;
            while (i < n && pad[i] == 0x11) ++i;
            long base = pos + ((data + (long)i - pos) & ~0xFFFL);
            *pixels = (base > data ? base : data) + (long)(ti[0x30] | ti[0x31] << 8 | ti[0x32] << 16 | (uint32_t)ti[0x33] << 24);
            return 1;
        }
    }
    return 0;
}
// Adds a texture to the extra-texture list: builds its TextureInfo from rec (124 bytes, the TPK record layout) and the
// platform record from pl (32 bytes), with D3D format fmt, top level only, pixels at guest address data (size bytes,
// kept), creates the D3D texture with TextureInfoPlatInterface::Init and registers it under the hash at rec + 0x24
// (0: failed). The list holds 36 names (009159A0..00915A30, FUN_004FAF10 doesn't check): keep the users few.
uint32_t extra_texture_add(Cpu *c, const uint8_t rec[124], const uint8_t pl[32], uint32_t fmt, uint32_t data, uint32_t size, int add) {
    uint32_t hash;
    memcpy(&hash, rec + 0x24, 4);
    uint32_t ti = gmalloc(0x80 + 0x20), pi = ti + 0x80;
    memcpy(G + ti, rec, 124);
    M32(ti) = pi; M32(ti + 4) = M32(ti + 8) = 0;
    M32(ti + 0x30) = M32(ti + 0x34) = M32(ti + 0x3C) = 0;      // placements, palette size
    M32(ti + 0x38) = M32(ti + 0x40) = size;                    // image size, top level size
    M8(ti + 0x4E) = 1;                                         // mip levels
    M32(ti + 0x68) = 0; M32(ti + 0x6C) = 0; M32(ti + 0x70) = data; M32(ti + 0x74) = M32(ti + 0x78) = 0;   // no pack
    memcpy(G + pi, pl, 32);
    M32(pi) = M32(pi + 4) = 0; M32(pi + 0x0C) = 5; M32(pi + 0x10) = 6; M32(pi + 0x14) = fmt; M32(pi + 0x18) = 0;
    uint32_t esp = c->esp, eax = c->eax, ecx = c->ecx, edx = c->edx;
    c->ecx = ti;
    call_guest(c, 0x006DDD90u, 0, NULL);                       // TextureInfoPlatInterface::Init: the D3D texture
    c->esp = esp; c->ecx = ecx;
    if (!M32(pi + 0x18)) { c->eax = eax; c->edx = edx; fprintf(stderr, "boot: extra texture %08X: Init made no D3D texture\n", hash); return 0; }
    if (add) {
        uint32_t a[2] = {hash, ti};
        call_guest(c, FN_ADD_OTHER_TEXTURE, 2, a);
    }
    c->esp = esp; c->eax = eax; c->ecx = ecx; c->edx = edx;
    return ti;
}
// The top level of an X360 texture (hash) from file in the X360 files dir, untiled and byte-swapped, in a malloc'd
// block (*size bytes, D3D format *fmt), with its TPK records (NULL: missing, compressed pack or unhandled format).
static uint8_t *x360_read_top(const char *dir, const char *file, uint32_t hash, uint8_t rec[124], uint8_t pl[32],
                              uint32_t *out_fmt, uint32_t *out_w, uint32_t *out_h, uint32_t *out_size) {
    char path[1200];
    snprintf(path, sizeof path, "%s/%s", dir, file);
    FILE *f = fopen(path, "rb");
    long pixels = 0;
    if (!f) { fprintf(stderr, "boot: X360 texture %08X: no %s\n", hash, path); return NULL; }
    fseek(f, 0, SEEK_END);
    int found = tpk_find(f, 0, ftell(f), hash, rec, pl, &pixels);
    uint32_t xfmt, w = (uint32_t)(rec[0x44] | rec[0x45] << 8), h = (uint32_t)(rec[0x46] | rec[0x47] << 8);
    memcpy(&xfmt, pl + 0x14, 4);
    // Xenon D3DFORMAT: GPU format v & 0x3F (18/19/20 DXT1/3/5, 6 8888), endian swap (v >> 6) & 3, tiled bit 8
    uint32_t gpu = xfmt & 0x3F, endian = xfmt >> 6 & 3, tiled = xfmt >> 8 & 1;
    uint32_t fmt = gpu == 18 ? 0x31545844u : gpu == 19 ? 0x33545844u : gpu == 20 ? 0x35545844u : gpu == 6 ? 21 : 0;
    uint32_t bdim = fmt == 21 ? 1 : 4, bpb = fmt == 0x31545844u ? 8 : fmt == 21 ? 4 : 16, log2 = bpb == 4 ? 2 : bpb == 8 ? 3 : 4;
    uint32_t wb = (w + bdim - 1) / bdim, hb = (h + bdim - 1) / bdim, awb = (wb + 31) & ~31u, ahb = (hb + 31) & ~31u;
    // linear rows: 256-byte aligned, except 32-bit ones, which are 32 texels aligned: ONSCREENRAINDROP (32x32 8888,
    // linear) read with a 256-byte pitch came out as its even rows squashed into the top half (2026-10-20)
    uint32_t pitch = tiled ? awb * bpb : fmt == 21 ? awb * bpb : wb * bpb < 256 ? 256 : (wb * bpb + 255) & ~255u, rows = tiled ? ahb : hb;
    if (!found || !fmt || !w || !h || w > 4096 || h > 4096) {
        fclose(f);
        fprintf(stderr, "boot: X360 texture %08X: %s in %s\n", hash, found ? "unhandled format" : "not found (or in a compressed pack)", path);
        return NULL;
    }
    uint8_t *src = calloc(rows, pitch);                     // the last texture of a pack can end before the padding
    size_t got = fseek(f, pixels, SEEK_SET) ? 0 : fread(src, 1, (size_t)pitch * rows, f);
    fclose(f);
    if (!got) { free(src); fprintf(stderr, "boot: X360 texture %08X: no pixels in %s\n", hash, path); return NULL; }
    uint32_t size = wb * hb * bpb;
    uint8_t *dst = malloc(size);
    for (uint32_t y = 0; y < hb; ++y)
        for (uint32_t x = 0; x < wb; ++x) {
            const uint8_t *s = src + (tiled ? (size_t)tiled_offset(x, y, awb, log2) * bpb : (size_t)y * pitch + x * bpb);
            uint8_t *d = dst + ((size_t)y * wb + x) * bpb;
            for (uint32_t k = 0; k < bpb; ++k)       // 8in16 swaps byte pairs, 8in32 reverses words, 16in32 swaps halves
                d[k] = s[endian == 1 ? k ^ 1 : endian == 2 ? k ^ 3 : endian == 3 ? k ^ 2 : k];
        }
    free(src);
    *out_fmt = fmt; *out_w = w; *out_h = h; *out_size = size;
    return dst;
}
// An X360-only texture (hash) from file in the X360 files dir, registered under as_hash (add: also put it in the list).
static uint32_t x360_extra_texture_as(Cpu *c, const char *dir, const char *file, uint32_t hash, uint32_t as_hash, int add) {
    uint8_t rec[124], pl[32];
    uint32_t fmt, w, h, size;
    uint8_t *top = x360_read_top(dir, file, hash, rec, pl, &fmt, &w, &h, &size);
    if (!top) return 0;
    uint32_t data = gmalloc(size);
    uint8_t *dst = G + data;
    memcpy(dst, top, size);
    free(top);
    memcpy(rec + 0x24, &as_hash, 4);
    uint32_t ti = extra_texture_add(c, rec, pl, fmt, data, size, add);
    if (!ti) return 0;
    if (diag_on) {
        diag_event("x360: X360-only texture %08X %s %ux%u (%.4s) from %s as %08X", hash, (const char *)G + ti + 0x0C, w, h,
                   fmt == 21 ? "8888" : (const char *)&fmt, file, as_hash);
        uint8_t *img = malloc((size_t)w * h * 4);
        if (decode_level(fmt, w, h, dst, NULL, img)) {
            for (uint32_t i = 0; i < w * h; ++i) { uint8_t t = img[4 * i]; img[4 * i] = img[4 * i + 2]; img[4 * i + 2] = t; }
            char png[1100];
            snprintf(png, sizeof png, "%s/x360_texture_%08X.png", diag_dir, hash);
            diag_png(png, img, w, h, w * 4, 1);
        }
        free(img);
    }
    return ti;
}
static uint32_t x360_extra_texture(Cpu *c, const char *dir, const char *file, uint32_t hash) {
    return x360_extra_texture_as(c, dir, file, hash, hash, 1);
}
static char extra_dir[1024];                               // the X360 files dir ("" with NFS_X360_EXTRA_TEXTURES=off)
// For padprompts.c: an X360 texture from file, under as_hash, not in the list (0: off, missing or failed).
uint32_t x360_texture_load(Cpu *c, const char *file, uint32_t hash, uint32_t as_hash) {
    return extra_dir[0] ? x360_extra_texture_as(c, extra_dir, file, hash, as_hash, 0) : 0;
}
// For materials.m: an X360 texture's top level decoded to RGBA8 (malloc'd, *w x *h; NULL: off, missing or failed).
uint8_t *x360_texture_rgba(const char *file, uint32_t hash, uint32_t *w, uint32_t *h) {
    uint8_t rec[124], pl[32];
    uint32_t fmt, size;
    uint8_t *top = extra_dir[0] ? x360_read_top(extra_dir, file, hash, rec, pl, &fmt, w, h, &size) : NULL;
    if (!top) return NULL;
    uint8_t *img = malloc((size_t)*w * *h * 4);
    if (!decode_level(fmt, *w, *h, top, NULL, img)) { free(img); img = NULL; }
    free(top);
    return img;
}
static void extra_textures(Cpu *c) {
    extra_done = 1;
    const char *e = getenv("NFS_X360_EXTRA_TEXTURES");
    if (e && (!strcmp(e, "off") || !strcmp(e, "0"))) return;
    if (!e && roots_active() && !roots_get("x360_root")) return;     // no X360 copy: nothing to look for
    char *dir = extra_dir;
    if (e && strcmp(e, "on") && strcmp(e, "1")) snprintf(extra_dir, sizeof extra_dir, "%s", e);
    else if (roots_active()) snprintf(extra_dir, sizeof extra_dir, "%s", roots_get("x360_root") ? roots_get("x360_root") : ROOTS_NONE);
    else snprintf(extra_dir, sizeof extra_dir, "%s/../x360/files", game_dir);
    int n = 0;
    for (unsigned i = 0; i < sizeof extra_list / sizeof *extra_list; ++i)
        n += (extra_ti[i] = x360_extra_texture(c, dir, extra_list[i].file, extra_list[i].hash)) != 0;
    fprintf(stderr, "boot: %d X360-only texture%s from %s\n", n, n == 1 ? "" : "s", dir);
    void arrest_textures(Cpu *c);
    arrest_textures(c);
}
// ---- X360 arrest-scene characters (graphics.x360_arrest / NFS_X360_ARREST=pc|x360, default pc, restart) ----
// tools_snapshot/x360_nis_arrest.py rebuilds the PC's 26 Scene_Arrest*_BundleB.bun with the X360's character meshes
// (~3x the triangles: FCOP01/02, MCOP01/02, PERP01) in the PC's vertex/draw layout, into <assets_root>/nis_arrest/.
// With x360, boot.c's CreateFileA opens those instead of the PC's (x360_arrest_path), and the X360 maps their draws'
// five slots name (diffuse, normal, specular; the X360 art has its own UV layout, so each is registered under an alias
// hash, textures.tsv) are added here at the first pack load, top level only; materials.m draws the normal-mapped ones
// with the X360 worldbone shader. Their D3D textures are tagged (hd_swap 2): d3d9.m counts the draws that use them
// (x360_arrest_draws, diag).
static const char *const kArrest[] = {"PC", "X360"}, *const kArrestV[] = {"pc", "x360"};
static Setting s_arrest = {.id = "graphics.x360_arrest", .category = SET_CARS, .order = 40, .label = "Arrest scenes",
    .help = "Arrest cutscene characters: PC meshes, or the X360's (about 3x the triangles, X360 face and uniform art with their normal and specular maps). Restart required: the scenes' files are picked at startup.",
    .type = SET_CHOICE, .choices = kArrest, .values = kArrestV, .nchoices = 2,
    .def = 1, .env = "NFS_X360_ARREST", .restart = 1};
__attribute__((constructor)) static void arrest_register(void) { settings_add(&s_arrest); }
uint32_t x360_arrest_draws;
static int arrest_on(void) { return s_arrest.boot_value == 1; }
int x360_arrest_enabled(void) { return arrest_on(); }     // materials.m: the X360 worldbone shader on their draws
// CAnimPlayer::Load (0044CD0A, recomp.py) refuses a scene needing over 3,000,000 bytes; the converted bundles need up to
// ~4.4 MB (the X360's were 5.3-5.9 MB) and come from the same streamer pool, so the cap is 8 MB with them.
uint32_t x360_arrest_nis_cap(void) { return arrest_on() ? 8000000u : 3000000u; }
int x360_arrest_path(const char *guest, char *host, size_t n) {    // 1: host is the converted bundle for guest
    if (!arrest_on()) return 0;
    const char *b = guest + strlen(guest);
    while (b > guest && b[-1] != '\\' && b[-1] != '/') --b;
    size_t len = strlen(b);
    if (strncasecmp(b, "Scene_Arrest", 12) || len < 16 || strcasecmp(b + len - 12, "_BundleB.bun")) return 0;
    char dir[1100];
    roots_asset_dir("nis_arrest", dir, sizeof dir);
    DIR *d = opendir(dir);
    struct dirent *ent;
    int found = 0;
    while (d && !found && (ent = readdir(d)))
        if (!strcasecmp(ent->d_name, b)) { snprintf(host, n, "%s/%s", dir, ent->d_name); found = 1; }
    if (d) closedir(d);
    if (diag_on) diag_event("x360: arrest scene %s -> %s", guest, found ? host : "no converted bundle, PC kept");
    return found;
}
void arrest_textures(Cpu *c) {
    if (!arrest_on() || !extra_dir[0]) return;
    char path[1100], line[512];
    { char ad[1100]; snprintf(path, sizeof path, "%s/textures.tsv", roots_asset_dir("nis_arrest", ad, sizeof ad)); }
    FILE *f = fopen(path, "r");
    if (!f) { fprintf(stderr, "boot: X360 arrest scenes: no %s\n", path); return; }
    int n = 0, want = 0;
    while (fgets(line, sizeof line, f)) {
        char name[128], file[256];
        unsigned hash, alias;
        if (sscanf(line, "%x\t%x\t%127s\t%255s", &hash, &alias, name, file) != 4) continue;   // the header
        ++want;
        x360_hd_creating = 2;
        n += x360_extra_texture_as(c, extra_dir, file, hash, alias, 1) != 0;
        x360_hd_creating = 0;
    }
    fclose(f);
    fprintf(stderr, "boot: X360 arrest scenes: %d/%d X360 character textures\n", n, want);
}
int x360_texture_added(uint32_t hash) {                    // for widescreen.c: whether hash is one of them, made
    for (unsigned i = 0; i < sizeof extra_list / sizeof *extra_list; ++i)
        if (extra_list[i].hash == hash) return extra_ti[i] != 0;
    return 0;
}

static uint32_t hd_read(const char *path, uint32_t *size) {   // guest allocation/copy need the GIL; host reads do not
    gil_release();
    HdFile *file = hd_file_get(path);
    gil_acquire();
    if (!file) return 0;
    uint32_t g = gmalloc(file->size);
    memcpy(G + g, file->bytes, file->size);
    *size = file->size;
    hd_file_release(file);
    return g;
}

// ---- verdict cache: USERDATA/hd_verdicts.bin ----
// The check's verdict only changes when a file does, so it is kept on disk, one record per set entry, keyed by the X360
// file's size and mtime, the PC texture's format, size and a checksum of the data compared, and HD_VERDICT_VERSION (bump
// it when the thresholds or the check change). Loaded once at hd_scan; new verdicts are written by a background task a
// few seconds after the last one (and at exit), to a temp file renamed over the old one. Anything unreadable: recompute.
#define HD_VERDICT_VERSION 1u
typedef struct { uint32_t hash; uint64_t xsize; int64_t xsec; int64_t xnsec; uint32_t pcfmt, pcsize, pcsum_lo, pcsum_hi;
                 uint32_t ww, hh, mips, fmt; float drgb, da; int32_t verdict; } HdVerdict;   // 80 bytes
static HdVerdict *hv; static int nhv, caphv, hv_dirty, hv_scheduled;
static pthread_mutex_t hv_lock = PTHREAD_MUTEX_INITIALIZER;
static char hv_path[1100];
static uint64_t hd_sum(const void *p, size_t n) {          // FNV-style over 8-byte words
    uint64_t h = 1469598103934665603ull, w;
    const uint8_t *q = p;
    for (; n >= 8; n -= 8, q += 8) { memcpy(&w, q, 8); h = (h ^ w) * 1099511628211ull; h ^= h >> 29; }
    for (; n; --n, ++q) h = (h ^ *q) * 1099511628211ull;
    return h;
}
static void hv_write(void) {
    pthread_mutex_lock(&hv_lock);
    if (!hv_dirty) { pthread_mutex_unlock(&hv_lock); return; }
    HdVerdict *copy = malloc((size_t)nhv * sizeof *copy + 1);
    int n = nhv;
    memcpy(copy, hv, (size_t)n * sizeof *copy);
    hv_dirty = 0; hv_scheduled = 0;
    pthread_mutex_unlock(&hv_lock);
    char tmp[1200];
    snprintf(tmp, sizeof tmp, "%s.tmp%d", hv_path, (int)getpid());
    FILE *f = fopen(tmp, "wb");
    if (f) {
        uint32_t head[4] = {0x31564448u, HD_VERDICT_VERSION, (uint32_t)sizeof *copy, (uint32_t)n};   // "HDV1"
        int ok = fwrite(head, sizeof head, 1, f) == 1 && fwrite(copy, sizeof *copy, (size_t)n, f) == (size_t)n;
        ok = fclose(f) == 0 && ok;
        if (ok) rename(tmp, hv_path); else unlink(tmp);
    }
    free(copy);
}
static void hv_load(void) {
    snprintf(hv_path, sizeof hv_path, "%s/hd_verdicts.bin", userdata_dir());
    FILE *f = fopen(hv_path, "rb");
    if (!f) return;
    uint32_t head[4];
    if (fread(head, sizeof head, 1, f) == 1 && head[0] == 0x31564448u && head[1] == HD_VERDICT_VERSION && head[2] == sizeof(HdVerdict) && head[3] < (1u << 20)) {
        hv = malloc((size_t)head[3] * sizeof *hv + 1);
        size_t n = fread(hv, sizeof *hv, head[3], f);
        nhv = caphv = (int)n;
        if (n != head[3]) nhv = 0;                          // truncated: ignore it all
    }
    fclose(f);
    atexit(hv_write);
}
static void hv_add(const HdVerdict *v) {
    pthread_mutex_lock(&hv_lock);
    int i = 0;
    while (i < nhv && hv[i].hash != v->hash) ++i;
    if (i == nhv) {
        if (nhv == caphv) { caphv = caphv ? caphv * 2 : 512; hv = realloc(hv, (size_t)caphv * sizeof *hv); }
        ++nhv;
    }
    hv[i] = *v;
    hv_dirty = 1;
    int sched = !hv_scheduled;
    hv_scheduled = 1;
    pthread_mutex_unlock(&hv_lock);
    if (sched) dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 3 * NSEC_PER_SEC), dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^{ hv_write(); });
}
// The key of ti's check against the file at path (stat failure: 0).
static int hv_key(HdVerdict *k, uint32_t ti, uint32_t pi, const char *path) {
    struct stat st;
    if (stat(path, &st)) return 0;
    uint32_t pw = M16(ti + 0x44), ph = M16(ti + 0x46), pcfmt = M32(pi + 0x14), pal = M32(ti + 0x74);
    size_t sz = pcfmt == 21 || pcfmt == 22 ? (size_t)pw * ph * 4 : pcfmt == 0x31545844 ? (size_t)pw * ph / 2 : (size_t)pw * ph;
    if (sz > (64u << 20)) return 0;
    memset(k, 0, sizeof *k);
    k->hash = M32(ti + 0x24); k->xsize = (uint64_t)st.st_size; k->xsec = st.st_mtimespec.tv_sec; k->xnsec = st.st_mtimespec.tv_nsec;
    k->pcfmt = pcfmt; k->pcsize = pw | ph << 16;
    uint64_t sum = hd_sum(G + M32(ti + 0x70), sz);
    if (pcfmt == 0x29 && pal) sum = sum * 31 + hd_sum(G + pal, 1024);
    k->pcsum_lo = (uint32_t)sum; k->pcsum_hi = (uint32_t)(sum >> 32);
    return 1;
}
static const HdVerdict *hv_find(const HdVerdict *k) {
    const HdVerdict *r = NULL;
    pthread_mutex_lock(&hv_lock);
    for (int i = 0; i < nhv; ++i)
        if (hv[i].hash == k->hash) {
            if (hv[i].xsize == k->xsize && hv[i].xsec == k->xsec && hv[i].xnsec == k->xnsec && hv[i].pcfmt == k->pcfmt && hv[i].pcsize == k->pcsize &&
                hv[i].pcsum_lo == k->pcsum_lo && hv[i].pcsum_hi == k->pcsum_hi) r = &hv[i];
            break;
        }
    pthread_mutex_unlock(&hv_lock);
    return r;                                               // entries are only replaced, never moved, once the load batch has begun; read at once
}
void f_006DDD90_orig(Cpu *c);
int vinyls_init(Cpu *c);

// Smooth mirror art (NFS_MIRROR_ART=smooth, default since 2026-10-21; pc keeps the textures): the rear-view mirror's
// frame (MIRROR_BACKING_RADAR, 128x64 DXT5, drawn twice mirrored over the 1170x315 px quad at 2160p) and its rounded mask
// (RVM_MASK, 256x64 DXT5, drawn into the mirror target, alpha only) are magnified ~4.6x by bilinear filtering, which
// turns their 1-texel anti-aliased outlines into visible steps on the curves. Their texture is made from the same
// pixels (the PC's; the X360's are the same art) upscaled 4x with a Catmull-Rom filter, as A8R8G8B8 with box mips: the
// outlines become smooth curves of the same softness, the shape and the plateaus (the frame's dashes) unchanged.
static int smooth_art = -1;
static uint8_t cr_clamp(float v) { return v <= 0 ? 0 : v >= 255 ? 255 : (uint8_t)(v + 0.5f); }
static void cr_weights(float t, float w[4]) {          // Catmull-Rom
    float t2 = t * t, t3 = t2 * t;
    w[0] = -0.5f * t3 + t2 - 0.5f * t; w[1] = 1.5f * t3 - 2.5f * t2 + 1; w[2] = -1.5f * t3 + 2 * t2 + 0.5f * t; w[3] = 0.5f * t3 - 0.5f * t2;
}
static int smooth_art_init(Cpu *c, uint32_t ti, uint32_t pi) {
    if (smooth_art < 0) { const char *e = getenv("NFS_MIRROR_ART"); smooth_art = !e || !*e || strcmp(e, "pc"); }
    const char *name = (const char *)G + ti + 0x0c;
    if (!smooth_art || !pi || (strcmp(name, "MIRROR_BACKING_RADAR") && strcmp(name, "RVM_MASK"))) return 0;
    uint32_t w = M16(ti + 0x44), h = M16(ti + 0x46), fmt = M32(pi + 0x14), src = M32(ti + 0x70);
    if (!src || w > 512 || h > 512 || !decode_ok(fmt, w, h, NULL)) return 0;
    enum { K = 4 };
    uint32_t ww = w * K, hh = h * K, levels = 0, total = 0;
    for (uint32_t lw = ww, lh = hh; lw >= 4 && lh >= 4 && levels < 12; lw /= 2, lh /= 2) { total += lw * lh * 4; ++levels; }
    uint8_t *rgba = malloc((size_t)w * h * 4);
    uint32_t buf = gmalloc(total);
    if (!rgba || !buf || !decode_level(fmt, w, h, G + src, NULL, rgba)) { free(rgba); if (buf) gfree(buf); return 0; }
    uint8_t *out = G + buf;                               // level 0, BGRA (A8R8G8B8)
    for (uint32_t y = 0; y < hh; ++y) {
        float fy = ((float)y + 0.5f) / K - 0.5f, wy[4];
        int y0 = (int)floorf(fy);
        cr_weights(fy - (float)y0, wy);
        for (uint32_t x = 0; x < ww; ++x) {
            float fx = ((float)x + 0.5f) / K - 0.5f, wx[4], acc[4] = {0, 0, 0, 0};
            int x0 = (int)floorf(fx);
            cr_weights(fx - (float)x0, wx);
            for (int j = 0; j < 4; ++j) {
                int sy = y0 - 1 + j; sy = sy < 0 ? 0 : sy >= (int)h ? (int)h - 1 : sy;   // clamped, as the sampler's address mode 1 (wrap) is never
                for (int i = 0; i < 4; ++i) {                                              // reached: the art has a transparent border
                    int sx = x0 - 1 + i; sx = sx < 0 ? 0 : sx >= (int)w ? (int)w - 1 : sx;
                    const uint8_t *q = rgba + 4 * ((uint32_t)sy * w + (uint32_t)sx);
                    float k = wx[i] * wy[j];
                    for (int ch = 0; ch < 4; ++ch) acc[ch] += k * q[ch];
                }
            }
            uint8_t *o = out + 4 * (y * ww + x);
            o[0] = cr_clamp(acc[2]); o[1] = cr_clamp(acc[1]); o[2] = cr_clamp(acc[0]); o[3] = cr_clamp(acc[3]);
        }
    }
    free(rgba);
    uint8_t *prev = out;                                  // box mips
    for (uint32_t l = 1, lw = ww, lh = hh; l < levels; ++l) {
        uint8_t *next = prev + (size_t)lw * lh * 4;
        uint32_t nw = lw / 2, nh = lh / 2;
        for (uint32_t y = 0; y < nh; ++y)
            for (uint32_t x = 0; x < nw; ++x)
                for (int ch = 0; ch < 4; ++ch)
                    next[4 * (y * nw + x) + ch] = (uint8_t)((prev[4 * ((2 * y) * lw + 2 * x) + ch] + prev[4 * ((2 * y) * lw + 2 * x + 1) + ch] +
                                                             prev[4 * ((2 * y + 1) * lw + 2 * x) + ch] + prev[4 * ((2 * y + 1) * lw + 2 * x + 1) + ch] + 2) / 4);
        prev = next; lw = nw; lh = nh;
    }
    uint32_t s70 = M32(ti + 0x70), s40 = M32(ti + 0x40), s44 = M32(ti + 0x44), s4c = M32(ti + 0x4c), sfmt = M32(pi + 0x14);
    uint8_t s4e = M8(ti + 0x4e);
    M32(ti + 0x70) = buf; M32(ti + 0x40) = ww * hh * 4;
    M16(ti + 0x44) = (uint16_t)ww; M16(ti + 0x46) = (uint16_t)hh; M8(ti + 0x4e) = (uint8_t)levels;
    M32(pi + 0x14) = 21;
    f_006DDD90_orig(c);
    M32(ti + 0x70) = s70; M32(ti + 0x40) = s40; M32(ti + 0x44) = s44; M32(ti + 0x4c) = s4c; M32(pi + 0x14) = sfmt; M8(ti + 0x4e) = s4e;
    gfree(buf);
    if (diag_on) diag_event("mirror: art %s %ux%u -> %ux%u A8R8G8B8, %u levels (Catmull-Rom x%d)", name, w, h, ww, hh, levels, K);
    return 1;
}
// Opt-in upload evidence for the title atlas and HUD backing. The bytes here are
// the same rows gpu_tex uploads; draw dumps identify the sampler that reads them.
static int ui_diag_wanted(uint32_t hash) {
    if (!diag_on) return 0;
    const char *e = getenv("BOOT_DIAG_UI_TEXTURES");
    if (!e || !*e || !strcmp(e, "0")) return 0;
    return !strcmp(e, "1") ? hash == 0xE0463AFA || hash == 0x20865C0A || hash == 0xA87927BE : hash == (uint32_t)strtoul(e, NULL, 16);
}
static struct { uint32_t guest, hash; } ui_diag_textures[256];
static unsigned ui_diag_count;
uint32_t x360_ui_diag_hash(uint32_t guest) {
    for (unsigned i = 0; i < ui_diag_count; ++i) if (ui_diag_textures[i].guest == guest) return ui_diag_textures[i].hash;
    return 0;
}
static void ui_diag_bytes(uint32_t hash, const char *kind, const void *p, size_t n) {
    char path[1200];
    snprintf(path, sizeof path, "%s/ui_%08X_%s", diag_dir, hash, kind);
    FILE *f = fopen(path, "wb");
    if (f) { fwrite(p, 1, n, f); fclose(f); }
}
static void ui_diag_source(uint32_t ti, uint32_t pi) {
    uint32_t hash = M32(ti + 0x24);
    if (!pi || !M32(ti + 0x70) || !ui_diag_wanted(hash)) return;
    char text[512];
    int n = snprintf(text, sizeof text, "PC %08X %.24s %ux%u fmt=%08X levels=%u base=%u quality_drop=%u\n",
                     hash, (char *)G + ti + 12, M16(ti + 0x44), M16(ti + 0x46), M32(pi + 0x14),
                     M8(ti + 0x4E), M32(ti + 0x40), M32(0x00982AE0));
    ui_diag_bytes(hash, "source.txt", text, (size_t)n);
    ui_diag_bytes(hash, "pc.bin", G + M32(ti + 0x70), M32(ti + 0x40));
    if (M32(ti + 0x74)) ui_diag_bytes(hash, "palette.bin", G + M32(ti + 0x74), 1024);
}
static void ui_diag_upload(uint32_t ti, uint32_t pi, uint32_t raw, uint32_t size) {
    uint32_t hash = M32(ti + 0x24);
    if (!ui_diag_wanted(hash)) return;
    Obj *t = obj(M32(pi + 0x18));
    if (!t) return;
    unsigned i = 0;
    while (i < ui_diag_count && ui_diag_textures[i].guest != t->guest) ++i;
    if (i < sizeof ui_diag_textures / sizeof *ui_diag_textures) {
        ui_diag_textures[i] = (typeof(ui_diag_textures[0])){t->guest, hash};
        if (i == ui_diag_count) ++ui_diag_count;
    }
    if (raw && size) ui_diag_bytes(hash, "replacement.dds", G + raw, size);
    char text[512];
    int n = snprintf(text, sizeof text, "GPU tex=%08X %ux%u fmt=%08X levels=%u\n", t->guest, t->w, t->h, t->fmt, t->levels);
    ui_diag_bytes(hash, "upload.txt", text, (size_t)n);
    for (uint32_t l = 0; l < t->levels; ++l) {
        char kind[64]; snprintf(kind, sizeof kind, "uploaded_%u.bin", l);
        if (t->mem[l]) ui_diag_bytes(hash, kind, G + t->mem[l], (size_t)t->pitch[l] * level_rows(t->fmt, mipdim(t->h, l)));
    }
}
void f_006DDD90(Cpu *c) {                                  // TextureInfoPlatInterface::Init (thiscall, TextureInfo* in ecx)
    if (!extra_done) {                                     // the X360-only textures, once, at the first pack load
        void prompts_textures(Cpu *c);
        extra_textures(c);
        prompts_textures(c);                               // and the button prompt glyphs (padprompts.c)
        void raindrops_textures(Cpu *c);
        raindrops_textures(c);                             // and the X360 lens raindrop textures (x360_post.m)
        void materials_textures(void);
        materials_textures();                              // and the X360 rain ripple frames (materials.m)
        void materials_car_textures(Cpu *c);
        materials_car_textures(c);                         // and the X360 car normal maps (materials.m R26)
    }
    if (vinyls_init(c)) return;                            // X360 vinyls and the HD composite targets (vinyls.m)
    if (nhd < 0) { hd_scan(); hv_load(); }
    uint32_t ti = c->ecx, pi = M32(ti);
    ui_diag_source(ti, pi);
    if (smooth_art_init(c, ti, pi)) return;                // the mirror's frame and mask, upscaled (above)
hd_lookup: ;
    HdTex *hx = NULL;
    int is_later = 0;
    if (pi && M32(ti + 0x70)) {
        uint32_t h = M32(ti + 0x24);
        const char *name = (const char *)G + ti + 0x0C;
        int tire = later_tire(h, name);
        for (int i = 0; i < nhd_later && !hx; ++i)
            if ((hd_later[i].hash == h || (tire && hd_later[i].hash == 0x52F745B1)) &&
                later_rule(hd_later[i].hash) >= 0 && hd_later[i].state != HD_KEEP) { hx = &hd_later[i]; is_later = 1; }
        for (int i = 0; i < nhd && !hx; ++i) if (hd[i].hash == h) hx = &hd[i];
    }
    if (!hx || hx->state == HD_KEEP) { f_006DDD90_orig(c); ui_diag_upload(ti, pi, 0, 0); return; }   // decided already: no file is touched
    // the DDS (header, 128 bytes, then the mips back to back) is read once, whole, into guest memory: the check
    // below and the swap both use that copy
    uint32_t size = 0, raw = 0;
    if (hx->state == HD_NEW) {                              // first look: the X360 mip at the PC size against the PC texture
        HdVerdict key, got;
        const HdVerdict *cv = NULL;
        int haskey = !is_later && hv_key(&key, ti, pi, hx->path); // MW verdict cache never authorizes changed art
        if (haskey && (cv = hv_find(&key))) {               // decided on an earlier run, nothing changed since
            got = *cv;                                      // copied: hv can be reallocated by a later add
            if (diag_on) {
                if (got.drgb < 0) diag_event("x360: HD texture %08X %s %ux%u -> %ux%u: no level covering the PC size, kept (cached)", got.hash, (const char *)G + ti + 0x0c, M16(ti + 0x44), M16(ti + 0x46), got.ww, got.hh);
                else diag_event("x360: HD texture %08X %s %ux%u -> %ux%u: rgb diff %.1f alpha diff %.1f -> %s (cached)", got.hash, (const char *)G + ti + 0x0c,
                                M16(ti + 0x44), M16(ti + 0x46), got.ww, got.hh, got.drgb, got.da, got.verdict > 0 ? "swap" : got.verdict == 0 ? "kept (differs)" : "kept (can't compare)");
            }
            hx->ww = got.ww; hx->hh = got.hh; hx->mips = got.mips; hx->fmt = got.fmt;
            hx->state = got.verdict > 0 ? HD_SWAP : HD_KEEP;
            if (got.verdict <= 0) { if (is_later) goto hd_lookup; f_006DDD90_orig(c); return; }
        } else {
            raw = hd_read(hx->path, &size);
            const uint8_t *head = G + raw;
            if (!raw || size < 128 || memcmp(head, "DDS ", 4) || (is_later && !later_header(head, size))) { if (raw) gfree(raw); hx->state = HD_KEEP; if (is_later) goto hd_lookup; f_006DDD90_orig(c); return; }
            uint32_t fourcc;
            memcpy(&hx->hh, head + 12, 4); memcpy(&hx->ww, head + 16, 4); memcpy(&hx->mips, head + 28, 4); memcpy(&fourcc, head + 84, 4);
            if (!hx->mips) hx->mips = 1;
            hx->fmt = fourcc == 0x31545844 || fourcc == 0x33545844 || fourcc == 0x35545844 ? fourcc : (head[80] & 0x40) ? 21 : 0;
            if (is_later && !later_pin(hx, head, size)) { gfree(raw); hx->state = HD_KEEP; goto hd_lookup; }
            float drgb, da;
            int verdict = hd_verify(ti, pi, head, size, hx->ww, hx->hh, hx->mips, hx->fmt, &drgb, &da);
            if (is_later) {
                verdict = verdict >= 0 && drgb >= 0 && da >= 0; // pinned art; covering mip still must decode
                if (diag_on) diag_event("later: owner-approved %08X for %08X %s rgb=%.2f alpha=%.2f", hx->hash, M32(ti + 0x24), (const char *)G + ti + 12, drgb, da);
            }
            if (haskey) {
                key.ww = hx->ww; key.hh = hx->hh; key.mips = hx->mips; key.fmt = hx->fmt; key.drgb = drgb; key.da = da; key.verdict = verdict;
                hv_add(&key);
            }
            hx->state = verdict > 0 ? HD_SWAP : HD_KEEP;
            if (verdict <= 0) { gfree(raw); if (is_later) goto hd_lookup; f_006DDD90_orig(c); return; }
        }
    }
    uint32_t ww = hx->ww, hh = hx->hh, mips = hx->mips, fmt = hx->fmt;
    // Same-size swaps improve the format, so retain the PC mip policy. Init
    // drops quality mips (00982AE0) whenever a chain is present: adding a chain
    // to one-level UI art otherwise turns a 512x512 atlas into 128x128 at detail 0.
    uint32_t pc_mips = M8(ti + 0x4E);
    if (!is_later && ww == M16(ti + 0x44) && hh == M16(ti + 0x46) && pc_mips && mips > pc_mips)
        if (!(diag_on && getenv("BOOT_DIAG_UI_ADDED_MIPS"))) mips = pc_mips; // test only: reproduce the added-chain loss
    int bpb = fmt == 0x31545844 ? 8 : fmt == 21 ? 0 : 16;
    uint32_t pcfmt = M32(pi + 0x14);
    int pc_alpha = pcfmt == 0x33545844 || pcfmt == 0x35545844 || pcfmt == 21 || pcfmt == 0x29;
    if (!fmt || ww > 4096 || hh > 4096 || !strncmp((const char *)G + ti + 0x0c, "DUMMY_SKIN", 10) || (pc_alpha && fmt == 0x31545844)) {
        if (raw) gfree(raw);
        if (is_later) { hx->state = HD_KEEP; goto hd_lookup; }
        f_006DDD90_orig(c); return;
    }
    // Init sizes level i as base >> 2i, which ignores the 4x4 block minimum: keep only levels with both sides >= 4
    uint32_t levels = 0, w = ww, h = hh;
    while (levels < mips && (fmt == 21 || (w >= 4 && h >= 4))) {
        // Init quarters byte sizes: stop a private rectangular RGBA chain before
        // one side clamps to 1 and that arithmetic would truncate a texel.
        if (is_later && fmt == 21 && (ww * hh * 4 >> (2 * levels)) != w * h * 4) break;
        ++levels; w = w > 1 ? w / 2 : 1; h = h > 1 ? h / 2 : 1;
    }
    uint32_t base = fmt == 21 ? ww * hh * 4 : (ww / 4) * (hh / 4) * (uint32_t)bpb, total = 0;
    for (uint32_t l = 0; l < levels; ++l) total += base >> (2 * l);
    if (!raw) raw = hd_read(hx->path, &size);               // a texture decided on an earlier load
    if (!raw || 128 + (size_t)total > size || (is_later && (!later_header(G + raw, size) || !later_pin(hx, G + raw, size)))) { if (raw) gfree(raw); if (is_later) { hx->state = HD_KEEP; goto hd_lookup; } f_006DDD90_orig(c); return; }
    // NFS_X360_TEXTURES_TONE=x360: a texture the converter regraded to the PC's tones was validated above in those
    // tones (same content); the X360's own grading of it is loaded instead
    for (int i = 0; !is_later && i < nhd_tone; ++i) {
        if (hd_tone[i].hash != hx->hash) continue;
        uint32_t tsize, traw = hd_read(hd_tone[i].path, &tsize);
        const uint8_t *gh = G + traw, *head = G + raw;      // same height, width, pitch, mip count and format
        if (traw && !memcmp(gh + 12, head + 12, 20) && !memcmp(gh + 80, head + 80, 8) && 128 + (size_t)total <= tsize) {
            gfree(raw); raw = traw; ++hd_tone_loaded;
        } else if (traw) gfree(traw);
        break;
    }
    uint32_t buf = raw + 128;
    // swap in, run Init, restore
    uint32_t s70 = M32(ti + 0x70), s40 = M32(ti + 0x40), s44 = M32(ti + 0x44), s4c = M32(ti + 0x4c), sfmt = M32(pi + 0x14);
    M32(ti + 0x70) = buf; M32(ti + 0x40) = base;
    M16(ti + 0x44) = (uint16_t)ww; M16(ti + 0x46) = (uint16_t)hh; M8(ti + 0x4e) = (uint8_t)levels;
    M32(pi + 0x14) = fmt;
    x360_hd_creating = 1;                                  // the texture Init creates is tagged (the probe's hdtex)
    f_006DDD90_orig(c);
    x360_hd_creating = 0;
    ui_diag_upload(ti, pi, raw, size);
    M32(ti + 0x70) = s70; M32(ti + 0x40) = s40; M32(ti + 0x44) = s44; M32(ti + 0x4c) = s4c; M32(pi + 0x14) = sfmt;
    gfree(raw);
    ++hd_loaded; hd_bytes += total;
    if (is_later && diag_on) {
        uint32_t bit = 1u << later_rule(hx->hash);
        if (!(hd_later_applied_mask & bit)) { hd_later_applied_mask |= bit; ++hd_later_loaded; }
        diag_event("later: overlay %08X applied=%u %ux%u levels=%u", hx->hash, hd_later_loaded, ww, hh, levels);
    }
    if (diag_on && (hd_loaded <= 20 || hd_loaded % 100 == 0))
        diag_event("x360: HD texture %08X %ux%u -> %ux%u, %u levels (%u loaded, %u in X360 tones, %u KB)", M32(ti + 0x24), s44 & 0xFFFF, s44 >> 16, ww, hh, levels, hd_loaded, hd_tone_loaded, hd_bytes >> 10);
}
