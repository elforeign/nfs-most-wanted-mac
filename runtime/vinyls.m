// vinyls.m - X360 HD car vinyls (HD_VINYLS.md).
//
// The PC composites a car's skin (DUMMY_SKIN1..4, 512x512 A8R8G8B8) on the CPU from a vinyl layer and its mask, both
// 512x512 P8 that TextureInfoPlatInterface::Init expands to A8R8G8B8. The X360 has the same art at 1024x1024 32-bit
// (LIN_A8R8G8B8) in CARS/<car>/VINYLS.BIN, compressed per texture like the PC packs. With the X360 files present
// (NFS_X360_VINYLS, default the roots.txt X360 root; =off keeps the PC vinyls):
// - the packs' tables (hash -> blob offset and sizes) are indexed on first use; a vinyl or mask the game creates is
//   read from its X360 blob, decompressed (JDLZ and HUFF natively, jdlz.h / huff.h, as the game's LZDecompress 00650350), reordered from the
//   X360's A R G B bytes to B G R A, checked against the PC texture at the PC size, and created at 1024x1024;
// - DUMMY_SKIN1..4 are created at 1024x1024 with a full mip chain (NFS_VINYL_MIPS=0: one level, like PC and X360);
// - CompositeSkin32 (0073B010) and DumpPreComp (0073B360) run natively and read each image's size from its texture,
//   so a layer of another size (a PC vinyl that failed the check) is sampled at the target size. The arithmetic is the
//   PC's (GetBlendColour 0073AE60: per-byte truncated products, summed and clamped; byte 3 = alpha max for recolours).
//   NFS_VINYL_BLEND=x360 runs the X360 compositor's (824F6EC0) byte order instead: its GetBlendColour treats the
//   big-endian word's byte 3 (blue) as alpha, and it byte-swaps the vinyl colour on anti-aliased mask edges.
// With BOOT_DIAG every composite is written to <diag>/vinyls/ next to the same composite from the PC textures, with
// the mean difference at the PC size. NFS_VINYL_SELFTEST=1 (or =remap, forcing a recolour) with NFS_X360_VINYLS=off
// checks the native compositor against the game's CompositeSkin32 byte for byte (both identical on 16.2.1).
#include <dirent.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/stat.h>

#include "d3d9_internal.h"
#include "roots.h"

#define FN_LZDECOMPRESS 0x00650350u
#define TEX_REDUCE      0x00982AE0u    // Init: mip levels to drop (texture detail); 0 at the detected levels
#define FMT_ARGB        21u
#define FMT_P8          0x29u
#define X360_LIN_ARGB   0x18280086u    // k_8_8_8_8, 8in32, linear
#define SKIN_HASH0      0x530B82B0u    // DUMMY_SKIN1..4: the composite targets (GetSkinCompositeParams 0073AB50)

void f_006DDD90_orig(Cpu *c);

typedef struct { uint32_t hash, file, off, csize, usize; } VEnt;
static VEnt *vt;                         // open addressing, hash 0 = empty
static uint32_t vt_mask;
static char **vfiles;
static int nvfiles, venabled = -1, vblend_x360, vmips = 1;
static uint32_t nvinyls, vswapped, vkept;

static VEnt *vfind(uint32_t h) {
    if (!vt || !h) return NULL;
    for (uint32_t i = h & vt_mask;; i = (i + 1) & vt_mask) {
        if (vt[i].hash == h) return &vt[i];
        if (!vt[i].hash) return NULL;
    }
}
static void vinsert(const VEnt *e) {
    for (uint32_t i = e->hash & vt_mask;; i = (i + 1) & vt_mask)
        if (!vt[i].hash || vt[i].hash == e->hash) { vt[i] = *e; return; }
}

// The compressed-TPK table (chunk 0x33310003 inside 0xB3310000 inside the root 0xB3300000): 24-byte entries
// hash, blob offset in the file, packed size, unpacked size (pixels + 156: TextureInfo + platform record), flags, 0.
static int index_pack(const char *path, int fi) {
    FILE *f = fopen(path, "rb");
    if (!f) return 0;
    uint32_t h[2], n = 0;
    if (fread(h, 4, 2, f) != 2 || h[0] != 0xB3300000u) { fclose(f); return 0; }
    long p = 8, end = 8 + (long)h[1];
    while (p + 8 <= end && fseek(f, p, SEEK_SET) == 0 && fread(h, 4, 2, f) == 2) {
        if (h[0] == 0xB3310000u) {
            long q = p + 8, qend = q + (long)h[1];
            while (q + 8 <= qend && fseek(f, q, SEEK_SET) == 0 && fread(h, 4, 2, f) == 2) {
                if (h[0] == 0x33310003u && h[1] % 24 == 0) {
                    uint32_t *t = malloc(h[1]);
                    if (fread(t, 1, h[1], f) == h[1])
                        for (uint32_t k = 0; k < h[1] / 24; ++k) {
                            uint32_t *e = t + 6 * k;
                            if (!e[0] || e[3] < 156 || e[3] > (64u << 20) || e[2] > e[3] + 64) continue;
                            VEnt v = {e[0], (uint32_t)fi, e[1], e[2], e[3]};
                            vinsert(&v); ++n;
                        }
                    free(t);
                }
                q += 8 + (long)h[1];
            }
            break;
        }
        p += 8 + (long)h[1];
    }
    fclose(f);
    return (int)n;
}
static void vinyls_setup(void) {
    venabled = 0;
    const char *e = getenv("NFS_VINYL_BLEND");
    vblend_x360 = e && !strcmp(e, "x360");
    e = getenv("NFS_VINYL_MIPS");
    vmips = !(e && !strcmp(e, "0"));
    e = getenv("NFS_X360_VINYLS");
    if (e && (!strcmp(e, "off") || !strcmp(e, "0"))) return;
    char dir[1024];
    e = getenv("NFS_X360_VINYLS");
    if (e && strcmp(e, "on") && strcmp(e, "1")) snprintf(dir, sizeof dir, "%s/CARS", e); else if (roots_active()) snprintf(dir, sizeof dir, "%s/CARS", roots_get("x360_root") ? roots_get("x360_root") : ROOTS_NONE);
    else snprintf(dir, sizeof dir, "%s/../x360/files/CARS", game_dir);
    DIR *d = opendir(dir);
    if (!d) { if (diag_on) diag_event("vinyls: no X360 CARS folder %s, PC vinyls", dir); return; }
    vt_mask = (1u << 15) - 1;            // 32768 slots for ~11k vinyls and masks
    vt = calloc(vt_mask + 1, sizeof *vt);
    uint64_t t0 = host_ticks();
    struct dirent *ent;
    while ((ent = readdir(d))) {
        if (ent->d_name[0] == '.') continue;
        char path[1200];
        snprintf(path, sizeof path, "%s/%s/VINYLS.BIN", dir, ent->d_name);
        vfiles = realloc(vfiles, (size_t)(nvfiles + 1) * sizeof *vfiles);
        vfiles[nvfiles] = strdup(path);
        int n = index_pack(path, nvfiles);
        if (n > 0) { nvinyls += (uint32_t)n; ++nvfiles; } else free(vfiles[nvfiles]);
    }
    closedir(d);
    venabled = nvinyls > 0;
    fprintf(stderr, "boot: %u X360 vinyls in %d packs (%s), blend %s\n", nvinyls, nvfiles, dir, vblend_x360 ? "x360" : "pc");
    if (diag_on) diag_event("vinyls: %u X360 vinyl/mask textures in %d packs, indexed in %.1f ms, blend %s, skin mips %s",
                            nvinyls, nvfiles, (host_ticks() - t0) / 1e6, vblend_x360 ? "x360" : "pc", vmips ? "on" : "off");
}

// ---- PC copies kept for the diag comparison (decoded at Init, before the swap) ----
typedef struct { uint32_t hash, w, h; uint8_t *bgra; } PcCopy;
static PcCopy pcs[64];
static int npcs;
static void keep_pc_copy(uint32_t ti) {
    uint32_t hash = M32(ti + 0x24), w = M16(ti + 0x44), h = M16(ti + 0x46), pi = M32(ti), px = M32(ti + 0x70), pal = M32(ti + 0x74);
    if (M32(pi + 0x14) != FMT_P8 || !pal || w > 1024 || h > 1024) return;
    PcCopy *k = NULL;
    for (int i = 0; i < npcs; ++i) if (pcs[i].hash == hash) k = &pcs[i];
    if (!k) { k = &pcs[npcs < 64 ? npcs++ : (int)(hash % 64)]; free(k->bgra); k->bgra = NULL; }
    k->hash = hash; k->w = w; k->h = h;
    k->bgra = realloc(k->bgra, (size_t)w * h * 4);
    for (uint32_t i = 0; i < w * h; ++i) memcpy(k->bgra + 4 * i, G + pal + 4 * M8(px + i), 4);
}
static PcCopy *pc_copy(uint32_t hash) {
    for (int i = 0; i < npcs; ++i) if (pcs[i].hash == hash && pcs[i].bgra) return &pcs[i];
    return NULL;
}

#include "huff.h"
#include "jdlz.h"

// JDLZ and HUFF natively (jdlz.h, huff.h), anything else (or a blob the native decoder rejects) through the game's
// LZDecompress. NFS_VINYL_LZ=guest: all through the game's; =verify: JDLZ and HUFF both ways, compared byte for byte,
// every blob. In diag mode the first 40 blobs of each format are verified unless NFS_VINYL_LZ=native is set explicitly.
static int vlz_mode = -1;                // 0 guest, 1 native, 2 verify
static uint32_t vlz_verified[2], vlz_mismatch[2];   // [0] JDLZ, [1] HUFF
typedef struct { double read, lz, lz_guest, swz; uint32_t magic; int native; } LoadTimes;

static uint32_t lz_native(uint32_t magic, const uint8_t *in, uint32_t inlen, uint8_t *out, uint32_t cap) {
    return magic == 0x5a4c444au ? jdlz_native(in, inlen, out, cap) : magic == 0x46465548u ? huff_native(in, inlen, out, cap) : 0;
}

// ---- X360 vinyl -> guest buffer of B G R A pixels (gmalloc), or 0 ----
static uint32_t load_x360(Cpu *c, const VEnt *v, uint32_t *w, uint32_t *h, LoadTimes *lt) {
    if (vlz_mode < 0) {
        const char *e = getenv("NFS_VINYL_LZ");
        vlz_mode = e && !strcmp(e, "guest") ? 0 : e && !strcmp(e, "verify") ? 2 : 1;
        if (e && !strcmp(e, "native")) vlz_verified[0] = vlz_verified[1] = 40;   // explicit: no diag-mode check either (timing runs)
    }
    memset(lt, 0, sizeof *lt);
    uint64_t t0 = host_ticks();
    FILE *f = fopen(vfiles[v->file], "rb");
    if (!f) return 0;
    uint32_t src = gmalloc(v->csize + 16), dst = gmalloc(v->usize + 64), ok = 0;
    if (fseek(f, (long)v->off, SEEK_SET) == 0 && fread(G + src, 1, v->csize, f) == v->csize) {
        uint64_t t1 = host_ticks();
        lt->read = (t1 - t0) / 1e6;
        memcpy(&lt->magic, G + src, 4);
        int k = lt->magic == 0x5a4c444au ? 0 : lt->magic == 0x46465548u ? 1 : -1;
        int verify = k >= 0 && (vlz_mode == 2 || (diag_on && vlz_mode == 1 && vlz_verified[k] < 40));
        uint32_t n = 0;
        if (k >= 0 && vlz_mode && !verify) {
            n = lz_native(lt->magic, G + src, v->csize, G + dst, v->usize + 64);
            lt->native = n != 0;
            if (!n && diag_on) diag_event("vinyls: %08X: native %s decoder rejected the blob", v->hash, k ? "HUFF" : "JDLZ");
        }
        // The game's decoder trusts the blob's own unpacked size and has no output bound, so it only runs (mode guest, or the
        // diag check of the first blobs) on a JDLZ/HUFF blob whose header size fits dst; a blob native rejected under the
        // default mode is unusable, not retried through the unbounded decoder (retail packs hold only JDLZ and HUFF).
        uint32_t hdr_usize = ~0u;
        if (k >= 0 && v->csize >= 16) memcpy(&hdr_usize, G + src + 8, 4);
        if (!lt->native && (verify || vlz_mode == 0) && k >= 0 && hdr_usize <= v->usize + 64) {
            uint32_t a[2] = {src, dst}, esp = c->esp, eax = c->eax, ecx = c->ecx, edx = c->edx;
            n = call_guest(c, FN_LZDECOMPRESS, 2, a);       // __cdecl; ecx is still Init's TextureInfo
            c->esp = esp; c->eax = eax; c->ecx = ecx; c->edx = edx;
        }
        uint64_t t2 = host_ticks();
        lt->lz = (t2 - t1) / 1e6;
        if (verify) {
            uint8_t *mine = malloc(v->usize + 64);
            uint32_t m = lz_native(lt->magic, G + src, v->csize, mine, v->usize + 64);
            uint64_t t3 = host_ticks();
            lt->lz_guest = lt->lz; lt->lz = (t3 - t2) / 1e6;
            ++vlz_verified[k];
            int same = m == n && !memcmp(mine, G + dst, n);
            if (!same) ++vlz_mismatch[k];
            if (diag_on) diag_event("vinyls: %s check %08X: native %u bytes in %.2f ms, game LZDecompress %u bytes in %.2f ms: %s (%u checked, %u differ)",
                                    k ? "HUFF" : "JDLZ", v->hash, m, lt->lz, n, lt->lz_guest, same ? "identical" : "DIFFERENT", vlz_verified[k], vlz_mismatch[k]);
            free(mine);
            t2 = t3;
        }
        uint32_t tail = dst + v->usize - 156;
        *w = M16(tail + 0x44); *h = M16(tail + 0x46);
        uint32_t fmt = M32(tail + 124 + 0x14), pitch = (*w * 4 + 255) & ~255u;
        if (n == v->usize && fmt == X360_LIN_ARGB && *w >= 64 && *h >= 64 && *w <= 2048 && *h <= 2048 && pitch * *h + 156 <= v->usize) {
            uint32_t *p = (uint32_t *)(G + dst);            // A R G B -> B G R A (rows are tightly packed for w >= 64)
            for (uint32_t i = 0; i < *w * *h; ++i) p[i] = __builtin_bswap32(p[i]);
            ok = 1;
        } else if (diag_on)
            diag_event("vinyls: %08X: X360 blob unusable (LZDecompress %u of %u bytes, %ux%u, format %08X)", v->hash, n, v->usize, *w, *h, fmt);
        lt->swz = (host_ticks() - t2) / 1e6;
    }
    fclose(f);
    gfree(src);
    if (!ok) { gfree(dst); return 0; }
    return dst;
}

// Mean |difference| between the X360 image box-filtered to the PC size and the PC P8 texture (RGB, alpha).
static int matches_pc(uint32_t ti, const uint8_t *x, uint32_t xw, uint32_t xh, double *drgb, double *da) {
    uint32_t pw = M16(ti + 0x44), ph = M16(ti + 0x46), px = M32(ti + 0x70), pal = M32(ti + 0x74);
    if (!pw || !ph || xw % pw || xh % ph || !px || !pal) return 0;
    uint32_t fx = xw / pw, fy = xh / ph, n = fx * fy;
    uint64_t s[2] = {0, 0};                                 // sums of |box sum - n * PC value| (n times the mean difference)
    const uint8_t *idx = G + px, *palette = G + pal;
    for (uint32_t y = 0; y < ph; ++y)
        for (uint32_t i = 0; i < pw; ++i) {
            const uint8_t *p = palette + 4 * idx[y * pw + i], *r0 = x + 4 * ((size_t)y * fy * xw + i * fx);
            for (int k = 0; k < 4; ++k) {
                int sum = 0;
                if (n == 4 && fx == 2) sum = r0[k] + r0[4 + k] + r0[4 * xw + k] + r0[4 * xw + 4 + k];   // the 1024 -> 512 case
                else
                    for (uint32_t j = 0; j < fy; ++j)
                        for (uint32_t q = 0; q < fx; ++q) sum += r0[4 * (j * xw + q) + k];
                s[k == 3] += (uint64_t)abs(sum - (int)n * p[k]);
            }
        }
    *drgb = s[0] / (3.0 * n * pw * ph); *da = s[1] / ((double)n * pw * ph);
    return *drgb <= 6.0 && *da <= 8.0;
}

static struct { uint32_t hash; int verdict; } verdicts[4096];
static int nverdicts;

// Box-filters level 0 of an A8R8G8B8 texture into its other levels.
static void build_mips(Obj *t) {
    for (uint32_t l = 1; l < t->levels; ++l) {
        uint32_t sw = t->w >> (l - 1), sh = t->h >> (l - 1), dw = sw > 1 ? sw / 2 : 1, dh = sh > 1 ? sh / 2 : 1;
        const uint8_t *s = G + t->mem[l - 1];
        uint8_t *d = G + t->mem[l];
        uint32_t sp = t->pitch[l - 1], dp = t->pitch[l];
        if (!t->mem[l] || !t->mem[l - 1]) return;
        for (uint32_t y = 0; y < dh; ++y)
            for (uint32_t x = 0; x < dw; ++x)
                for (int k = 0; k < 4; ++k) {
                    uint32_t x1 = sw > 1 ? 2 * x + 1 : x, y1 = sh > 1 ? 2 * y + 1 : y;
                    d[y * dp + 4 * x + k] = (uint8_t)((s[2 * y * sp + 8 * x + k] + s[2 * y * sp + 4 * x1 + k] + s[y1 * sp + 8 * x + k] + s[y1 * sp + 4 * x1 + k] + 2) / 4);
                }
    }
}

// ---- live HD textures (diag: stats.log "vinyls" / "skins" columns, the swap events) ----
static struct { uint32_t tex; int skin; } live[512];
static int nlive;
static void live_add(uint32_t tex, int skin) {
    for (int i = 0; i < nlive; ++i) if (live[i].tex == tex) { live[i].skin = skin; return; }
    if (nlive < 512) { live[nlive].tex = tex; live[nlive++].skin = skin; }
}
// Textures this file created that are still alive (count and MB of all levels), vinyls/masks and skins.
void vinyls_live(uint32_t *nv, uint32_t *mbv, uint32_t *ns, uint32_t *mbs) {
    uint64_t b[2] = {0, 0};
    uint32_t n[2] = {0, 0};
    int k = 0;
    for (int i = 0; i < nlive; ++i) {
        Obj *o = obj(live[i].tex);
        if (!o || o->refs <= 0 || !o->mem[0] || o->fmt != FMT_ARGB || o->w < 1024) continue;   // released (or the handle reused)
        live[k++] = live[i];
        ++n[live[i].skin];
        for (uint32_t l = 0; l < o->levels; ++l) b[live[i].skin] += (uint64_t)o->pitch[l] * mipdim(o->h, l);
    }
    nlive = k;
    *nv = n[0]; *mbv = (uint32_t)(b[0] >> 20); *ns = n[1]; *mbs = (uint32_t)(b[1] >> 20);
}

// Init hook (from x360_textures.m's f_006DDD90): returns 1 when it created the texture.
int vinyls_init(Cpu *c) {
    if (venabled < 0) vinyls_setup();
    if (!venabled) return 0;
    uint32_t ti = c->ecx, pi = M32(ti);
    if (!pi || !M32(ti + 0x70)) return 0;
    uint32_t hash = M32(ti + 0x24), pw = M16(ti + 0x44), ph = M16(ti + 0x46), pfmt = M32(pi + 0x14);
    uint32_t buf = 0, w = 0, h = 0, levels = 1;
    uint64_t t0 = host_ticks();
    LoadTimes lt = {0};
    double t_check = 0;
    if (hash - SKIN_HASH0 < 4) {
        // composite target: 2x the PC size, level 0 upscaled from the PC texture (overwritten by every composite)
        if (pfmt != FMT_ARGB || pw != 512 || ph != 512) return 0;
        w = 2 * pw; h = 2 * ph;
        if (vmips) for (uint32_t s = w > h ? w : h; s > 1; s >>= 1) ++levels;
        uint32_t total = 0;
        for (uint32_t l = 0; l < levels; ++l) total += (w * h * 4) >> (2 * l);
        buf = gmalloc(total);
        uint32_t src = M32(ti + 0x70);
        for (uint32_t y = 0; y < h; ++y)
            for (uint32_t x = 0; x < w; ++x) M32(buf + 4 * (y * w + x)) = M32(src + 4 * ((y / 2) * pw + x / 2));
    } else {
        VEnt *v = vfind(hash);
        if (!v || pfmt != FMT_P8) return 0;
        int verdict = -2;
        for (int i = 0; i < nverdicts; ++i) if (verdicts[i].hash == hash) verdict = verdicts[i].verdict;
        if (verdict == 0) return 0;
        buf = load_x360(c, v, &w, &h, &lt);
        if (!buf) return 0;
        if (verdict == -2) {
            uint64_t tc = host_ticks();
            double drgb = 0, da = 0;
            verdict = matches_pc(ti, G + buf, w, h, &drgb, &da);
            t_check = (host_ticks() - tc) / 1e6;
            if (nverdicts < 4096) { verdicts[nverdicts].hash = hash; verdicts[nverdicts++].verdict = verdict; }
            if (diag_on) diag_event("vinyls: %08X %s %ux%u -> %ux%u: rgb diff %.2f alpha diff %.2f -> %s", hash, (const char *)G + ti + 0x0c,
                                    pw, ph, w, h, drgb, da, verdict ? "swap" : "kept (differs)");
            if (!verdict) { ++vkept; gfree(buf); return 0; }
        }
        if (diag_on) keep_pc_copy(ti);
        ++vswapped;
    }
    // swap in, run Init, restore (as x360_textures.m's HD textures)
    uint32_t s70 = M32(ti + 0x70), s40 = M32(ti + 0x40), s44 = M32(ti + 0x44), s4c = M32(ti + 0x4c), sfmt = M32(pi + 0x14), sred = M32(TEX_REDUCE);
    M32(ti + 0x70) = buf; M32(ti + 0x40) = w * h * 4;
    M16(ti + 0x44) = (uint16_t)w; M16(ti + 0x46) = (uint16_t)h; M8(ti + 0x4e) = (uint8_t)levels;
    M32(pi + 0x14) = FMT_ARGB;
    if (levels > 1) M32(TEX_REDUCE) = 0;
    uint64_t tcr = host_ticks();
    f_006DDD90_orig(c);
    double t_create = (host_ticks() - tcr) / 1e6;
    M32(TEX_REDUCE) = sred;
    M32(ti + 0x70) = s70; M32(ti + 0x40) = s40; M32(ti + 0x44) = s44; M32(ti + 0x4c) = s4c; M32(pi + 0x14) = sfmt;
    M8(ti + 0x4a) = 0x20;                                   // what Init does when it expands P8
    gfree(buf);
    if (diag_on) live_add(M32(pi + 0x18), hash - SKIN_HASH0 < 4);
    if (diag_on) {
        uint32_t nv, mv, ns, ms;
        vinyls_live(&nv, &mv, &ns, &ms);
        if (hash - SKIN_HASH0 < 4)
            diag_event("vinyls: created skin %08X %s at %ux%u, %u levels in %.1f ms (live: %u vinyls/masks %u MB, %u skins %u MB)", hash,
                       (const char *)G + ti + 0x0c, w, h, levels, (host_ticks() - t0) / 1e6, nv, mv, ns, ms);
        else
            diag_event("vinyls: swap %08X %.24s %ux%u %.1f ms: read %.2f %s %s %.2f reorder %.2f check %.2f create %.2f; live %u/%uMB skins %u/%uMB",
                       hash, (const char *)G + ti + 0x0c, w, h, (host_ticks() - t0) / 1e6 - lt.lz_guest, lt.read,
                       lt.magic == 0x5a4c444au ? "JDLZ" : lt.magic == 0x46465548u ? "HUFF" : "LZ", lt.native || lt.lz_guest > 0 ? "native" : "game", lt.lz,
                       lt.swz, t_check, t_create, nv, mv, ns, ms);
    }
    return 1;
}

// ---------------- compositor ----------------
static Obj *tex_of(uint32_t ti) {                          // TextureInfo -> its D3D texture object
    uint32_t pi = ti ? M32(ti) : 0, t = pi ? M32(pi + 0x18) : 0;
    Obj *o = t ? obj(t) : NULL;
    return o && o->fmt == FMT_ARGB && o->mem[0] && o->w && o->h ? o : NULL;
}
static uint32_t flipbgr(uint32_t v) { return (v & 0xFF00FF00u) | (v >> 16 & 0xFF) | (v & 0xFF) << 16; }   // __flipbgr 006BEB80

// GetBlendColour on values (byte k of the result = sum of byte k of each colour times its weight, each product
// truncated, clamped to 255). alpha_byte: the byte that takes the max instead (-1: all summed). PC products are
// exact (x87); the X360's are single precision (fmuls).
static uint32_t blend(const uint32_t *col, const float *wt, int n, int alpha_byte, int x360) {
    int s[4] = {0, 0, 0, 0};
    for (int i = 0; i < n; ++i) {
        if (!(wt[i] > 0.003921569f)) continue;
        for (int k = 0; k < 4; ++k) {
            uint32_t b = col[i] >> (8 * k) & 0xFF;
            int v = x360 ? (int)((float)b * wt[i]) : (int)((double)b * (double)wt[i]);
            if (k == alpha_byte) { if ((v & 0xFF) > s[k]) s[k] = v & 0xFF; }
            else s[k] += v;
        }
    }
    uint32_t r = 0;
    for (int k = 0; k < 4; ++k) r |= (uint32_t)(s[k] > 255 ? 255 : s[k]) << (8 * k);
    return r;
}

typedef struct {
    const uint8_t *img, *mask;
    uint32_t w, h, pitch, mw, mh, mpitch;
    int remap;
    uint32_t sw[4];                       // swatch colours as the params hold them (gloss<<24 | B<<16 | G<<8 | R)
} Layer;
static uint32_t marker_off[4][16], marker_n[4], marker_w;
static int marker_init;
// CompositeSkin32's body on host memory (BGRA rows): base fill, layers, swatch-region markers.
static void composite(uint8_t *dst, uint32_t tw, uint32_t th, uint32_t tpitch, uint32_t base, const uint32_t *regions,
                      const Layer *L, int nl, int x360, int scan_markers) {
    static const uint32_t markers[4] = {0xBF0000FFu, 0xBF00FF00u, 0xBFFF0000u, 0xBFFF00FFu};
    if (scan_markers && !marker_init) {                    // swatch_offset_init: once, on the first target (none has any)
        for (uint32_t y = 0; y < th; ++y)
            for (uint32_t x = 0; x < tw; ++x) {
                uint32_t v; memcpy(&v, dst + y * tpitch + 4 * x, 4);
                for (int k = 0; k < 4; ++k) if (v == markers[k] && marker_n[k] < 16) marker_off[k][marker_n[k]++] = y * tw + x;
            }
        marker_init = 1; marker_w = tw;
    }
    uint32_t fill = flipbgr(base);
    for (uint32_t y = 0; y < th; ++y)
        for (uint32_t x = 0; x < tw; ++x) memcpy(dst + y * tpitch + 4 * x, &fill, 4);
    int alpha_byte = x360 ? 0 : 3, mask_byte = x360 ? 2 : 1;
    for (int li = 0; li < nl; ++li) {
        const Layer *l = &L[li];
        uint32_t sw[4];
        for (int k = 0; k < 4; ++k) sw[k] = flipbgr(l->sw[k]);
        for (uint32_t y = 0; y < th; ++y) {
            const uint8_t *irow = l->img + (size_t)(y * l->h / th) * l->pitch, *mrow = l->mask + (size_t)(y * l->mh / th) * l->mpitch;
            uint8_t *drow = dst + (size_t)y * tpitch;
            for (uint32_t x = 0; x < tw; ++x) {
                uint32_t cv, tv;
                memcpy(&cv, irow + 4 * (size_t)(x * l->w / tw), 4);
                uint32_t m = mrow[4 * (size_t)(x * l->mw / tw) + mask_byte];
                if (!m) continue;
                if (l->remap) {
                    float w3[3] = {(float)((double)(cv >> 16 & 0xFF) * 0.003921569f), (float)((double)(cv >> 8 & 0xFF) * 0.003921569f),
                                   (float)((double)(cv & 0xFF) * 0.003921569f)};
                    cv = blend(sw, w3, 3, alpha_byte, x360);
                }
                if (m >= 128) { memcpy(drow + 4 * x, &cv, 4); continue; }
                memcpy(&tv, drow + 4 * x, 4);
                float t = (float)m * 0.0078125f, w2[2] = {t, 1.0f - t};
                uint32_t pair[2] = {x360 ? __builtin_bswap32(cv) : cv, tv};
                cv = blend(pair, w2, 2, -1, x360);
                memcpy(drow + 4 * x, &cv, 4);
            }
        }
    }
    if (marker_w == tw)
        for (int k = 0; k < 4; ++k)
            for (uint32_t i = 0; i < marker_n[k]; ++i) {
                uint32_t v = x360 ? regions[k] : flipbgr(regions[k]);
                memcpy(dst + (marker_off[k][i] / tw) * tpitch + 4 * (marker_off[k][i] % tw), &v, 4);
            }
}

static uint32_t ncomposites;
// Diag: <diag>/vinyls/<n>_<target>_<layer>.png = HD composite at the PC size | PC composite (colour, then alpha),
// and the HD composite itself.
static void diag_composite(Obj *T, uint32_t target_hash, uint32_t base, const uint32_t *regions, const Layer *L, const uint32_t *lhash,
                           const uint32_t *mhash, int nl) {
    char path[1024];
    snprintf(path, sizeof path, "%s/vinyls", diag_dir);
    mkdir(path, 0755);
    size_t n = strlen(path);
    snprintf(path + n, sizeof path - n, "/%03u_%08X_%08X_hd.png", ncomposites, target_hash, nl ? lhash[0] : 0);
    diag_png(path, G + T->mem[0], T->w, T->h, T->pitch[0], 0);
    // the PC composite from the PC copies of the layers
    uint32_t pw = T->w / 2, ph = T->h / 2;
    Layer P[1];
    int np = 0, have = 1;
    for (int i = 0; i < nl && i < 1; ++i) {
        PcCopy *a = pc_copy(lhash[i]), *b = pc_copy(mhash[i]);
        if (!a || !b) { have = 0; break; }
        P[np] = L[i];
        P[np].img = a->bgra; P[np].w = a->w; P[np].h = a->h; P[np].pitch = a->w * 4;
        P[np].mask = b->bgra; P[np].mw = b->w; P[np].mh = b->h; P[np].mpitch = b->w * 4;
        ++np;
    }
    if (!have) { diag_event("vinyls: composite %u into %08X (%ux%u): no PC copies of the layers to compare", ncomposites, target_hash, T->w, T->h); return; }
    uint8_t *pc = malloc((size_t)pw * ph * 4), *img = malloc((size_t)pw * ph * 16);
    composite(pc, pw, ph, pw * 4, base, regions, P, np, vblend_x360, 0);
    double s[2] = {0, 0};
    const uint8_t *hd = G + T->mem[0];
    uint32_t hp = T->pitch[0];
    for (uint32_t y = 0; y < ph; ++y)
        for (uint32_t x = 0; x < pw; ++x) {
            uint8_t *o[4] = {img + 4 * ((size_t)y * pw * 4 + x), img + 4 * ((size_t)y * pw * 4 + pw + x), img + 4 * ((size_t)y * pw * 4 + 2 * pw + x),
                             img + 4 * ((size_t)y * pw * 4 + 3 * pw + x)};
            for (int k = 0; k < 4; ++k) {
                int v = (hd[2 * y * hp + 8 * x + k] + hd[2 * y * hp + 8 * x + 4 + k] + hd[(2 * y + 1) * hp + 8 * x + k] + hd[(2 * y + 1) * hp + 8 * x + 4 + k] + 2) / 4;
                int p = pc[4 * ((size_t)y * pw + x) + k];
                s[k == 3] += abs(v - p);
                if (k < 3) { o[0][k] = (uint8_t)v; o[1][k] = (uint8_t)p; }
                else { memset(o[2], v, 3); memset(o[3], p, 3); }
            }
            o[0][3] = o[1][3] = o[2][3] = o[3][3] = 255;
        }
    snprintf(path + n, sizeof path - n, "/%03u_%08X_%08X_cmp.png", ncomposites, target_hash, nl ? lhash[0] : 0);
    diag_png(path, img, pw * 4, ph, pw * 16, 0);
    diag_event("vinyls: composite %u into %08X (%ux%u), layer %08X remap %d: HD at %ux%u vs PC composite: rgb diff %.2f alpha diff %.2f",
               ncomposites, target_hash, T->w, T->h, nl ? lhash[0] : 0, nl ? L[0].remap : 0, pw, ph, s[0] / (3.0 * pw * ph), s[1] / ((double)pw * ph));
    free(pc); free(img);
}

void f_0073B010_orig(Cpu *c);
void f_0073B010(Cpu *c) {                                  // int CompositeSkin32(SkinCompositeParams *) (cdecl)
    uint32_t p = arg(c, 0), target = M32(p);
    Obj *T = target && M8(target + 0x4a) == 0x20 ? tex_of(target) : NULL;
    if (!T) { f_0073B010_orig(c); return; }
    uint64_t t0 = host_ticks();
    int nl = (int)M32(p + 0x4c);
    if (nl > 4) nl = 4;
    Layer L[4];
    uint32_t lhash[4], mhash[4], regions[4];
    int n = 0;
    for (int i = 0; i < 4; ++i) regions[i] = M32(p + 8 + 4 * i);
    for (int i = 0; i < nl; ++i) {
        uint32_t l = p + 0x18 + 0x34 * (uint32_t)i;
        if (!M32(l)) continue;
        Obj *I = tex_of(M32(l + 4)), *Mk = tex_of(M32(l + 8));
        if (!I || !Mk || !M32(l + 0xc) || !M32(l + 0x14)) { f_0073B010_orig(c); return; }   // not what we expect: the game's code
        Layer *y = &L[n];
        y->img = G + I->mem[0]; y->w = I->w; y->h = I->h; y->pitch = I->pitch[0];
        y->mask = G + Mk->mem[0]; y->mw = Mk->w; y->mh = Mk->h; y->mpitch = Mk->pitch[0];
        y->remap = M32(l + 0x20) != 0;
        for (int k = 0; k < 4; ++k) y->sw[k] = M32(l + 0x24 + 4 * (uint32_t)k);
        lhash[n] = M32(M32(l + 4) + 0x24); mhash[n] = M32(M32(l + 8) + 0x24);
        ++n;
    }
    // NFS_VINYL_SELFTEST=1 (=remap: also forces REMAP with fixed swatch colours), PC-size textures (NFS_X360_VINYLS=off):
    // the native result goes to a copy, the game's own CompositeSkin32 then runs on the target, and the two are compared.
    static int selftest = -1;
    if (selftest < 0) { const char *e = getenv("NFS_VINYL_SELFTEST"); selftest = !e ? 0 : !strcmp(e, "remap") ? 2 : 1; }
    if (selftest && !vblend_x360 && T->levels == 1 && (!n || (L[0].w == T->w && L[0].h == T->h && L[0].mw == T->w && L[0].mh == T->h))) {
        static const uint32_t sw[4] = {0x40FF2010u, 0x8010C0F0u, 0xC0F0F000u, 0xFF808080u};
        for (int i = 0; i < n && selftest == 2; ++i) {
            uint32_t l = p + 0x18 + 0x34 * (uint32_t)i;
            M32(l + 0x20) = 1; L[i].remap = 1;
            for (int k = 0; k < 4; ++k) M32(l + 0x24 + 4 * (uint32_t)k) = L[i].sw[k] = sw[k];
        }
        size_t bytes = (size_t)T->pitch[0] * T->h;
        uint8_t *mine = malloc(bytes);
        memcpy(mine, G + T->mem[0], bytes);
        composite(mine, T->w, T->h, T->pitch[0], M32(p + 4), regions, L, n, 0, 1);
        f_0073B010_orig(c);                                 // pops the return address itself
        uint32_t bad = 0, first = 0, a = 0, b = 0;
        for (uint32_t y = 0; y < T->h; ++y)
            for (uint32_t x = 0; x < T->w; ++x) {
                uint32_t u, v;
                memcpy(&u, mine + (size_t)y * T->pitch[0] + 4 * x, 4); memcpy(&v, G + T->mem[0] + (size_t)y * T->pitch[0] + 4 * x, 4);
                if (u != v && !bad++) { first = y * T->w + x; a = u; b = v; }
            }
        diag_event("vinyls: selftest composite into %08X %ux%u, %d layer(s), remap %d: %u pixels differ from the game's CompositeSkin32%s",
                   M32(target + 0x24), T->w, T->h, n, n ? L[0].remap : 0, bad, bad ? "" : " (identical)");
        if (bad) diag_event("vinyls: selftest first difference at pixel %u: native %08X game %08X", first, a, b);
        fprintf(stderr, "vinyls: selftest %u pixels differ (remap %d)\n", bad, n ? L[0].remap : 0);
        free(mine);
        ++ncomposites;
        return;
    }
    composite(G + T->mem[0], T->w, T->h, T->pitch[0], M32(p + 4), regions, L, n, vblend_x360, 1);
    build_mips(T);
    T->dirty = 1;
    ++ncomposites;
    if (diag_on) {
        double ms = (host_ticks() - t0) / 1e6;
        char sw[64] = "";
        if (n && L[0].remap) snprintf(sw, sizeof sw, ", swatches %08X %08X %08X %08X", L[0].sw[0], L[0].sw[1], L[0].sw[2], L[0].sw[3]);
        diag_event("vinyls: composite %u into %08X %ux%u (%u levels), %d layer(s)%s, layer %08X remap %d%s, base %08X, %.1f ms", ncomposites, M32(target + 0x24),
                   T->w, T->h, T->levels, n, n ? (L[0].w == T->w ? " same size" : " resampled") : "", n ? lhash[0] : 0, n ? L[0].remap : 0, sw,
                   M32(p + 4), ms);
        // the PNG comparison costs ~15x the composite: the first 30 composites, then every 8th (its time is logged apart;
        // NFS_VINYL_PNG=0: none, for timing runs)
        static int png = -1;
        if (png < 0) { const char *e = getenv("NFS_VINYL_PNG"); png = !(e && !strcmp(e, "0")); }
        if (png && T->w > 512 && (ncomposites <= 30 || ncomposites % 8 == 0)) {
            uint64_t td = host_ticks();
            diag_composite(T, M32(target + 0x24), M32(p + 4), regions, L, lhash, mhash, n);
            diag_event("vinyls: composite %u diag images %.1f ms (not part of the composite)", ncomposites, (host_ticks() - td) / 1e6);
        }
    }
    c->eax = 1;
    c->esp += 4;
}

void f_0073B360_orig(Cpu *c);
void f_0073B360(Cpu *c) {                                  // int DumpPreComp(VinylLayerInfo *, TextureInfo *) (cdecl)
    uint32_t l = arg(c, 0), target = arg(c, 1);
    Obj *T = target && M8(target + 0x4a) == 0x20 ? tex_of(target) : NULL, *I = l ? tex_of(M32(l + 4)) : NULL;
    if (!T || !I || !M32(l + 0xc)) { f_0073B360_orig(c); return; }
    uint8_t *d = G + T->mem[0];
    const uint8_t *s = G + I->mem[0];
    for (uint32_t y = 0; y < T->h; ++y)
        for (uint32_t x = 0; x < T->w; ++x) memcpy(d + (size_t)y * T->pitch[0] + 4 * x, s + (size_t)(y * I->h / T->h) * I->pitch[0] + 4 * (size_t)(x * I->w / T->w), 4);
    build_mips(T);
    T->dirty = 1;
    if (diag_on) diag_event("vinyls: precomposite %08X (%ux%u) into %08X (%ux%u)", M32(M32(l + 4) + 0x24), I->w, I->h, M32(target + 0x24), T->w, T->h);
    c->eax = 1;
    c->esp += 4;
}
