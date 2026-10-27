// probe.m - screen-reach probe (diagnostic mode): proves that an opt-in render feature's output reaches the frame
// that is presented, not only that the feature runs.
//
//   BOOT_DIAG=<dir> BOOT_DIAG_PROBE=postfx,msaa,hdtex,shadows,carpaint,mirror,raindrops,roadrefl,ripples,grass [BOOT_DIAG_PROBE_AT=<s>] ./boot <game>
//
// From BOOT_DIAG_PROBE_AT seconds of race sim time (default 10; a feature written name@S starts at S instead, e.g.
// mirror@27 after a camera change at sim 20 in the same run), for each feature in turn, three passes draw the
// same scene: after each the pass's real time is dropped (pacing.c, as for BOOT_DIAG_POSTFX_AB), so the next pass
// runs no sim tick and nothing moves. Base (as normal), signature (the feature replaces its own output with a known
// signature), after (normal again). Each time the texture Present shows (the back buffer after the MSAA resolve)
// is captured, and <dir>/probe.txt gets one line per feature: PASS when the signature frame changed as expected,
// the feature reported applying its signature, and the after frame matches the base frame (the scene was held);
// FAIL otherwise, with the numbers and probe_<feature>_{base,sig,after}.png. Then a line `done`.
// Signatures and what PASS needs:
//   postfx   X360 post chain (NFS_POSTFX=x360): its composite outputs magenta (debug mode 3)    sig >= 50% magenta
//   msaa     the back buffer's resolve clears the multisampled back buffer to magenta first     sig >= 90% magenta
//   hdtex    every swapped X360 texture drawn is uploaded with magenta colour (alpha kept)      >= 1% more of the
//            frame magenta-tinted than base
//   shadows  every shadow-map lookup reads a depth-0 map through an edge-clamped compare     >= 1% of the frame darker,
//            sampler (all receivers in shadow, also outside the real map's border)          < 0.5% brighter
//   carpaint the car paint replacement (materials.m, NFS_CAR_PAINT=x360) outputs magenta     >= 0.5% more of the
//                                                                                          frame magenta-tinted
//   mirror   the rear-view mirror's tone pass (NFS_POSTFX=x360, bumper/hood camera) outputs      >= 0.5% of the frame
//            magenta on the mirror texture                                                         newly magenta, and < 0.5%
//                                                                                                  changed outside the
//                                                                                                  mirror's band (top centre),
//                                                                                                  beyond what rain moved there
//                                                                                                  (base vs after, same area)
//   raindrops the lens raindrops' X360 pass (NFS_RAINDROPS=x360 with the X360 chain, after the  >= 0.1% of the frame
//            composite) draws the drops' cover magenta (needs rain: 15.1.1 rains from the start)   newly magenta, < 3% changed
//   roadrefl wet roads (worldreflect) sample the road reflection target as magenta, only     >= 1% of the frame
//            when it has the X360 size (half the back buffer, x360.m; NFS_ROAD_REFLECTION=x360) changed (the reflection
//            (needs wet roads: 15.1.1 is wet from ~5 s with rain on and road reflections >= 2)  is added scaled by
//                                                                                          wetness and specular, so
//                                                                                          after the post chain it
//                                                                                          shifts colour without
//                                                                                          reading as magenta)
//   ripples the rain ripple patch of the wet-road shaders (materials.m, NFS_RAIN_RIPPLES=x360)   >= 0.5% more of the
//            outputs magenta where it rains (intensity > 0) on wet roads (wetness > 0.01)       frame magenta-tinted
//            (needs rain and wet roads: 15.1.1 from ~5 s with rain on and road reflections >= 2)
//   grass shells1..4 output magenta AFTER production LOD, opacity/noise discard and depth;
//         shell0 keeps production colour. Requires >=64 changed, >=16 newly magenta-tinted
//         pixels in BOOT_DIAG_GRASS_ROI=x,y,w,h (presented pixel coordinates; default whole
//         frame); only pixels stable in base/after count. applied counts marked shell draws.
//         Deliberately broken expectation: NFS_GRASS=pc -> applied=0, FAIL; a far-only N=1
//         view also has applied=0, FAIL. Real near-view PASS and PC broken measurements are
//         PENDING game run; 15.1.1 sim12 run1 had no established grass coverage.
//         Offline evaluator fixture (2026-10-23): applied=4, changed=80, newly tinted=80
//         PASS; applied=0 with identical base/sig gives 0/0 FAIL. Counter-off, moving-pixel,
//         excluded/invalid ROI and 63-pixel fixtures also FAIL (six negative cases total).
//         This is opt-in; the nine-feature gate is unchanged until a near frame is measured.
// A feature that never applied its signature (switched off, e.g. antialiasing level 0, or nothing drawn with it)
// fails. runtime/probe.sh runs one guarded game for all the features (settings together; each signature pass applies only
// its own feature) and prints a table; PROBE_SPLIT=1 runs one game per feature. BOOT_DIAG_PROBE_SHOTS=1 keeps the three
// captures on a PASS too; BOOT_DIAG_DRAWS=probe dumps the base and signature passes draw by draw (diag.c).
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "d3d9_internal.h"

int probe_sig;                           // PROBE_* bit of the feature drawing its signature in this pass
static const char *const names[] = {"postfx", "msaa", "hdtex", "shadows", "carpaint", "mirror", "raindrops", "roadrefl", "ripples", "grass"};
#define MAXF 16
static int feats[MAXF], nfeats = -1, cur;
static int phase;                        // 0 waiting, 1 base shown, 2 signature pass, 3 signature shown, 4 after pass, 5 gap, 6 done
static int applied, gap;
static float at = 10, race_t0 = -1;
static float feat_at[MAXF];                 // per feature: its own start time (name@seconds), else `at`
static id<MTLBuffer> bufs[3];
static uint64_t frames[3];
static dispatch_queue_t eval_q;

// BOOT_DIAG_MIRROR_AB=1 (with the mirror probe, for run_shots composites; the verdict is meaningless then): the three
// passes show the mirror treated at the default exposure (base), at 1.4 (signature pass) and untreated (after pass).
int probe_mirror_ab(void) {
    static int on = -1;
    if (on < 0) on = getenv("BOOT_DIAG_MIRROR_AB") != NULL;
    if (!on || nfeats <= 0 || feats[cur] != 5) return 0;
    return phase == 2 ? 1 : phase == 4 ? 2 : 0;
}
void probe_applied(int bit, int n) { if (probe_sig & bit) applied += n; }

static int probe_init(void) {
    if (nfeats >= 0) return nfeats > 0;
    nfeats = 0;
    const char *e = getenv("BOOT_DIAG_PROBE");
    if (!e || !diag_on) return 0;
    if (getenv("BOOT_DIAG_PROBE_AT")) at = (float)atof(getenv("BOOT_DIAG_PROBE_AT"));
    for (const char *p = e; *p && nfeats < MAXF;) {
        size_t n = strcspn(p, ","), nn = strcspn(p, "@,");
        for (int k = 0; k < (int)(sizeof names / sizeof *names); ++k) if (strlen(names[k]) == nn && !strncmp(p, names[k], nn)) {
            feat_at[nfeats] = p[nn] == '@' ? (float)atof(p + nn + 1) : at;
            feats[nfeats++] = k;
        }
        p += n + (p[n] == ',');
    }
    diag_event("probe: %d feature(s) from race time %.1f s", nfeats, at);
    return nfeats > 0;
}

static id<MTLBuffer> capture(id<MTLCommandBuffer> cmd, id<MTLTexture> t) {
    NSUInteger w = t.width, h = t.height;
    id<MTLBuffer> b = [cmd.device newBufferWithLength:w * h * 4 options:MTLResourceStorageModeShared];
    id<MTLBlitCommandEncoder> e = [cmd blitCommandEncoder];
    [e copyFromTexture:t sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0, 0, 0) sourceSize:MTLSizeMake(w, h, 1)
              toBuffer:b destinationOffset:0 destinationBytesPerRow:w * 4 destinationBytesPerImage:w * h * 4];
    [e endEncoding];
    return b;
}

// BGRA8 pixels: magenta (R, B high, G low), magenta-tinted (R and B well above G), luma
static int magenta(const uint8_t *p) { return p[2] > 200 && p[0] > 200 && p[1] < 80; }
static int tinted(const uint8_t *p) { return (p[0] < p[2] ? p[0] : p[2]) > p[1] + 60; }
static int luma(const uint8_t *p) { return (p[2] * 77 + p[1] * 150 + p[0] * 29) >> 8; }
static int changed(const uint8_t *a, const uint8_t *b) { return abs(a[0] - b[0]) > 24 || abs(a[1] - b[1]) > 24 || abs(a[2] - b[2]) > 24; }

static void evaluate(int f, int napplied, int samples, id<MTLBuffer> b0, id<MTLBuffer> b1, id<MTLBuffer> b2,
                     uint32_t w, uint32_t h, const uint64_t *fr, int last) {
    const uint8_t *base = b0.contents, *sig = b1.contents, *after = b2.contents;
    double outside = 0, outside_drift = 0;
    unsigned grass_changed = 0, grass_marked = 0;
    unsigned rx = 0, ry = 0, rw = w, rh = h;
    const char *roi = getenv("BOOT_DIAG_GRASS_ROI");
    if (f == 9 && roi && (sscanf(roi, "%u,%u,%u,%u", &rx, &ry, &rw, &rh) != 4 ||
        rx >= w || ry >= h || !rw || !rh || rw > w-rx || rh > h-ry)) rw = rh = 0;
    double n = (double)w * h, mag_b = 0, mag_s = 0, tin_b = 0, tin_s = 0, darker = 0, brighter = 0, sig_changed = 0, drift = 0;
    for (size_t i = 0; i < (size_t)w * h; ++i) {
        const uint8_t *pb = base + 4 * i, *ps = sig + 4 * i, *pa = after + 4 * i;
        mag_b += magenta(pb); mag_s += magenta(ps); tin_b += tinted(pb); tin_s += tinted(ps);
        int lb = luma(pb), ls = luma(ps);
        darker += ls + 12 < lb; brighter += ls > lb + 12;
        {   // the mirror's band: the FE quad is x 190-450, y 19-89 of 640x480; allow the pillarbox and a margin
            size_t x = i % w, y = i / w;
            if (x < w * 3 / 10 || x > w * 7 / 10 || y > h * 22 / 100) { outside += changed(pb, ps); outside_drift += changed(pb, pa); }
        }
        sig_changed += changed(pb, ps); drift += changed(pb, pa);
        if (f == 9 && i % w >= rx && i % w - rx < rw && i / w >= ry && i / w - ry < rh && !changed(pb, pa)) {
            grass_changed += changed(pb, ps);
            grass_marked += changed(pb, ps) && tinted(ps) && !tinted(pb);
        }
    }
    mag_b /= n; mag_s /= n; tin_b /= n; tin_s /= n; darker /= n; brighter /= n; sig_changed /= n; drift /= n;
    outside /= n; outside_drift /= n;
    int held = drift < 0.02, seen;
    switch (f) {
    case 0: seen = mag_s >= 0.5 && mag_b < 0.05; break;
    case 1: seen = mag_s >= 0.9; break;
    case 2: seen = tin_s - tin_b >= 0.01; break;
    case 4: seen = tin_s - tin_b >= 0.005; break;
    case 5: seen = mag_s - mag_b >= 0.005 && outside - outside_drift < 0.005; break;     // rain moves between passes: only what exceeds base vs after counts
    case 6: seen = mag_s - mag_b >= 0.001 && sig_changed < 0.03; break;
    case 7: seen = sig_changed >= 0.01; break;
    case 8: seen = tin_s - tin_b >= 0.005; break;
    case 9: seen = grass_changed >= 64 && grass_marked >= 16; break;
    default: seen = darker >= 0.01 && brighter < 0.005; break;
    }
    int pass = napplied > 0 && held && seen;
    const char *why = pass ? "" : !napplied ? " (signature never applied: feature off or not drawn)" : !held ? " (scene not held: after differs from base)" : " (signature not on screen)";
    char path[1024];
    if (!pass || getenv("BOOT_DIAG_PROBE_SHOTS")) {          // before the probe.txt line: probe.sh stops the game at `done`
        static const char *const sfx[] = {"base", "sig", "after"};
        const uint8_t *imgs[3] = {base, sig, after};
        for (int k = 0; k < 3; ++k) {
            snprintf(path, sizeof path, "%s/probe_%s_%s.png", diag_dir, names[f], sfx[k]);
            diag_png(path, imgs[k], w, h, w * 4, 0);
        }
    }
    snprintf(path, sizeof path, "%s/probe.txt", diag_dir);
    FILE *o = fopen(path, "a");
    if (o) {
        fprintf(o, "%-8s %s%s  applied %d, aa %dx, %ux%u, frames %llu/%llu/%llu; signature frame: magenta %.1f%% (base %.1f%%), tinted %.1f%% (base %.1f%%), "
                   "darker %.1f%%, brighter %.1f%%, changed %.1f%% (outside the mirror band %.2f%%, of which base vs after %.2f%%); after vs base changed %.2f%%\n",
                names[f], pass ? "PASS" : "FAIL", why, napplied, samples, w, h, (unsigned long long)fr[0], (unsigned long long)fr[1],
                (unsigned long long)fr[2], 100 * mag_s, 100 * mag_b, 100 * tin_s, 100 * tin_b, 100 * darker, 100 * brighter, 100 * sig_changed, 100 * outside, 100 * outside_drift, 100 * drift);
        if (f == 9) fprintf(o, "grass_roi %u,%u,%u,%u: stable changed %u (need 64), newly tinted %u (need 16); PC/far-only control must FAIL applied=0\n", rx, ry, rw, rh, grass_changed, grass_marked);
        if (last) fprintf(o, "done\n");
        fclose(o);
    }
    diag_event("probe: %s %s%s", names[f], pass ? "PASS" : "FAIL", why);
}

// render_present.m, every Present, after the MSAA resolve: `tex` is what is shown.
int render_msaa_samples(void);
uint64_t diag_current_frame(void);
void probe_present(id<MTLCommandBuffer> cmd, id<MTLTexture> tex) {
    if (!probe_init() || phase == 6) return;
    if (phase == 0) {
        float now;
        memcpy(&now, G + 0x009885D8u, 4);                      // Sim::GetTime
        if (M32(0x00925E90u) != 6) return;                     // racing
        if (race_t0 < 0) race_t0 = now;
        if (now - race_t0 < feat_at[cur]) return;
        frames[0] = diag_current_frame();
        bufs[0] = capture(cmd, tex);
        phase = 1;
    } else if (phase == 2) {
        frames[1] = diag_current_frame();
        bufs[1] = capture(cmd, tex);
        probe_sig = 0;
        phase = 3;
    } else if (phase == 4) {
        frames[2] = diag_current_frame();
        bufs[2] = capture(cmd, tex);
        if (!eval_q) eval_q = dispatch_queue_create("nfsmw.probe", DISPATCH_QUEUE_SERIAL);
        id<MTLBuffer> b0 = bufs[0], b1 = bufs[1], b2 = bufs[2];
        int f = feats[cur], n = applied, samples = render_msaa_samples(), last = cur + 1 == nfeats;
        uint32_t w = (uint32_t)tex.width, h = (uint32_t)tex.height;
        uint64_t f0 = frames[0], f1 = frames[1], f2 = frames[2];
        [cmd addCompletedHandler:^(id<MTLCommandBuffer> b) {
            (void)b;
            dispatch_async(eval_q, ^{ const uint64_t fr[3] = {f0, f1, f2}; evaluate(f, n, samples, b0, b1, b2, w, h, fr, last); });
        }];
        bufs[0] = bufs[1] = bufs[2] = nil;
        gap = 0;
        phase = 5;
    } else if (phase == 5 && ++gap >= 30) {                    // half a second at 60 fps between features
        phase = ++cur < nfeats ? 0 : 6;
    }
}

// diag.c (BOOT_DIAG_DRAWS=probe): this frame is a probe base pass (it will be captured at its Present) or a signature
// pass, so both can be dumped and compared draw by draw.
int probe_dump_frame(void) {
    if (phase == 2) return 1;
    if (phase != 0 || nfeats <= 0 || race_t0 < 0 || M32(0x00925E90u) != 6) return 0;
    float now;
    memcpy(&now, G + 0x009885D8u, 4);
    return now - race_t0 >= feat_at[cur];
}

// pacing.c, at the end of each main-loop pass: 1 = drop this pass's real time (the next pass draws the same scene).
int probe_hold(void) {
    if (phase == 1) { phase = 2; probe_sig = 1 << feats[cur]; applied = 0; return 1; }
    if (phase == 3) { phase = 4; return 1; }
    return 0;
}

// The hdtex signature: a texture's level, as uploaded, with its colour made magenta (alpha and DXT1 punch-through
// kept). Formats other than DXT1/3/5 and 32-bit BGRA stay as they are.
void probe_tint(uint32_t fmt, uint8_t *p, size_t size, int bgra32) {
    if (bgra32) { for (size_t i = 0; i + 3 < size; i += 4) { p[i] = 255; p[i + 1] = 0; p[i + 2] = 255; } return; }
    int dxt1 = fmt == 0x31545844, bs = dxt1 ? 8 : (fmt == 0x33545844 || fmt == 0x35545844) ? 16 : 0;
    if (!bs) return;
    for (size_t i = 0; i + (size_t)bs <= size; i += (size_t)bs) {
        uint8_t *c = p + i + bs - 8;                           // the colour block: c0, c1 (RGB565), indices
        int three = dxt1 && (c[0] | c[1] << 8) <= (c[2] | c[3] << 8);   // DXT1 3-colour mode (index 3 transparent): keep it
        uint16_t hi = 0xF81F, lo = 0xF81E;                     // magenta, and one step less blue
        uint16_t c0 = three ? lo : hi, c1 = three ? hi : lo;
        c[0] = (uint8_t)c0; c[1] = (uint8_t)(c0 >> 8); c[2] = (uint8_t)c1; c[3] = (uint8_t)(c1 >> 8);
    }
}
