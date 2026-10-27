// detail.c - world detail distance (pop-in): diagnostics of what pops, and the fix.
//
// Scenery draw path (PC v1.3, decomp/src/speed_007237d0.c, speed_0072d0e0.c):
//  GrandSceneryCullInfo::CullView 00731B10 -> WhatSectionsShouldWeDraw 00731870: the sections drawn are the camera's
//  drivable section's visible list (DrivableScenerySection +0x10 count, +0x12 shorts; precomputed) that are loaded
//  (ScenerySectionHeaders 009A3BB0[n] -> +4 header), plus the global sections 2600..2699. Section numbers whose
//  n % 100 is in [ScenerySectionLODOffset 00903164, 2x) are the low-detail LOD versions of n - offset.
//  -> ScenerySectionHeader::TreeCull 00729640 -> DrawAScenery 00729120 per instance (0x40 bytes: bbox, flags +0x18,
//  position +0x20, info index +0x3E; info 0x48 bytes: models +0x28 A, +0x30 B, +0x34 C, radius +0x38): projected size
//  FUN_00729060 = pixscale (cull info +0xC0 = view +0xC) * r / (d - r), r = radius + 6; main view: < 18 px culled,
//  LOD A when g_WorldLodLevel >= 2 unless (A has >= 40 of +0x14, d >= 25, size / max(A +0x9C, 6) < 8.7) -> B.
//  TrackStreamer::LoadSection 0072DAC0 queues a section's file, SectionLoadedCallback 00727EE0 marks it loaded.
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "host.h"

#define SECTION_HEADERS 0x009A3BB0u     // ScenerySectionHeaders[n] (CullView 00731B49): -> +4 ScenerySectionHeader*
#define LOD_OFFSET 0x00903164u          // ScenerySectionLODOffset
#define SIMTIME 0x009885D8u

static inline uint32_t section_header(int n) {
    if (n < 0 || n >= 0xA8C) return 0;
    uint32_t e = M32(SECTION_HEADERS + 4u * (uint32_t)n);
    return e ? M32(e + 4) : 0;
}
static inline float simtime(void) { return MF32(SIMTIME); }
static inline int is_lod(int n) { int o = (int)M32(LOD_OFFSET), m = n % 100; return o > 0 && m >= o && m < 2 * o; }

// ---- Detail distance (NFS_DETAIL_DISTANCE=pc|2|4, F10 Graphics; default pc) ----
// The main view's scenery is LOD-picked and culled by projected size with the cull info's pixel scale (+0xC0, copied
// from the view's +0xC by DoCulling 00731B80 every frame, before CullView). Scaling it for the main view's cull pass
// moves the size cull (< 18 px) and the LOD A -> B switch (size / radius < 8.7) k times farther; car LODs
// (eView::GetPixelSize, the view's own +0xC) and the other views are untouched. Section streaming is not changed.
// Measured at 4K (run_shots/popin_detail_distance_ab.jpg, same held frame): 2x/4x change 0.16 % of a distant treeline
// and nothing else visible, so the default stays pc; the pop the user sees is not these thresholds at 4K.
static float detail_k = 0, ab_k;        // ab_k: the A/B's held pass forces this scale (0: none)
void detail_set_distance(int v) { detail_k = v == 0 ? 1.0f : v == 1 ? 2.0f : 4.0f; }
static float detail_scale(void) {
    if (detail_k <= 0) {
        const char *e = getenv("NFS_DETAIL_DISTANCE");
        detail_k = !e || !*e || !strcmp(e, "pc") ? 1.0f : atof(e) >= 1 ? (float)atof(e) : 1.0f;
    }
    return ab_k > 0 ? ab_k : detail_k;
}

// ---- diagnostics (BOOT_DIAG) ----
static uint32_t cur_ci;                 // the main view's cull info while its DrawAScenery calls run
static struct { uint32_t calls, a, b, c, other, small, cull; float small_min_d, b_min_d, a_max_d; } st;
static uint64_t st_frame;
static struct { uint32_t a, b; float b_min_d; } fr;   // this frame's main-view scenery (diag)

static float inst_dist(uint32_t ci, uint32_t inst) {
    float dx = MF32(inst + 0x20) - MF32(ci + 0xA0), dy = MF32(inst + 0x24) - MF32(ci + 0xA4),
          dz = MF32(inst + 0x28) - MF32(ci + 0xA8);
    return sqrtf(dx * dx + dy * dy + dz * dz);
}
static void section_reach(uint32_t ci, int n, int *count, float *near, float *far) {   // instances' distances
    uint32_t h = section_header(n);
    *count = 0; *near = 1e9f; *far = 0;
    if (!h) return;
    int k = (int)M32(h + 0x24);
    uint32_t a = M32(h + 0x20);
    *count = k;
    for (int i = 0; i < k; ++i) {
        float d = inst_dist(ci, a + 0x40u * (uint32_t)i);
        if (d < *near) *near = d;
        if (d > *far) *far = d;
    }
}

// ---- flicker diagnostics (BOOT_DIAG): per-instance main-view state frame to frame ----
// Each DrawAScenery call's outcome per instance (culled < 18 px, culled other, LOD A, B, C/other) is remembered for
// the last two frames it was called; an A-B-A pattern over three consecutive frames is a blink (lod: A<->B, cull:
// drawn<->culled, other), and a drawn instance not called for exactly one frame in between is a dropout (tree/frustum).
#define FL_N (1u << 18)
static struct fl { uint32_t self; uint32_t idx; uint64_t last, last2; uint8_t s1, s2; } *fl_tab;
static struct { uint32_t visits, lod, cull, other, gaps; float dmin, dmax; } fs;
static uint16_t fl_sec[0xA8C];
static int fl_drawn(int s) { return s >= 3; }
static void flicker_note(uint32_t self, uint32_t idx, int s, float d) {
    if (!fl_tab && !(fl_tab = calloc(FL_N, sizeof *fl_tab))) return;
    uint32_t h = (self * 2654435761u ^ idx * 40503u) & (FL_N - 1);
    struct fl *e = 0;
    for (uint32_t k = 0; k < 64; ++k) {
        struct fl *x = &fl_tab[(h + k) & (FL_N - 1)];
        if (!x->self || (x->self == self && x->idx == idx)) { e = x; break; }
    }
    if (!e) return;
    uint64_t f = perf_nframes;
    ++fs.visits;
    if (e->self && e->last == f) return;
    int sec = (int16_t)M16(self + 0xC), hit = 0;
    if (e->self && e->last == f - 1 && e->last2 == f - 2 && s != e->s1 && s == e->s2) {
        int a = s, b = e->s1;
        if ((a == 3 && b == 4) || (a == 4 && b == 3)) ++fs.lod, hit = 1;
        else if (fl_drawn(a) != fl_drawn(b)) ++fs.cull, hit = 1;
        else ++fs.other, hit = 1;
    } else if (e->self && e->last == f - 2 && fl_drawn(s) && fl_drawn(e->s1)) ++fs.gaps, hit = 1;
    if (hit) {
        if (sec >= 0 && sec < 0xA8C && fl_sec[sec] < 0xFFFF) ++fl_sec[sec];
        if (d < fs.dmin) fs.dmin = d;
        if (d > fs.dmax) fs.dmax = d;
    }
    e->self = self; e->idx = idx; e->last2 = e->last; e->s2 = e->s1; e->last = f; e->s1 = (uint8_t)s;
}
static void flicker_flush(void) {
    char top[160] = "";
    for (int t = 0; t < 4; ++t) {
        int b = -1;
        for (int i = 0; i < 0xA8C; ++i) if (fl_sec[i] && (b < 0 || fl_sec[i] > fl_sec[b])) b = i;
        if (b < 0) break;
        snprintf(top + strlen(top), 24, " %d:%u", b, fl_sec[b]);
        fl_sec[b] = 0;
    }
    memset(fl_sec, 0, sizeof fl_sec);
    uint32_t n = fs.lod + fs.cull + fs.other + fs.gaps;
    diag_event("detail flicker: t %.1f visits %u blinks lod %u cull %u other %u, dropouts %u | %.0f-%.0f m | top%s",
               simtime(), fs.visits, fs.lod, fs.cull, fs.other, fs.gaps, n ? fs.dmin : 0, n ? fs.dmax : 0, top);
    memset(&fs, 0, sizeof fs);
    fs.dmin = 1e9f;
}

static void stats_flush(void) {
    uint64_t f = perf_nframes;
    if (f - st_frame < 60) return;
    flicker_flush();
    if (st.calls) diag_event("detail: t %.1f scenery calls %u A %u B %u C %u other %u | culled %u (size<18 %u, nearest %.0f m) "
                             "| B nearest %.0f m, A farthest %.0f m, pixscale %.0f",
                             simtime(), st.calls, st.a, st.b, st.c, st.other, st.cull, st.small,
                             st.small < 1 ? 0 : st.small_min_d, st.b < 1 ? 0 : st.b_min_d, st.a_max_d,
                             cur_ci ? MF32(cur_ci + 0xC0) : 0);
    memset(&st, 0, sizeof st);
    st.small_min_d = st.b_min_d = 1e9f;
    st_frame = f;
}

void f_00729120_orig(Cpu *c);
void f_00729120(Cpu *c) {               // ScenerySectionHeader::DrawAScenery (this, index, cull info, visible state)
    uint32_t self = c->ecx, idx = arg(c, 0), ci = arg(c, 1);
    if (!diag_on || ci != cur_ci || !ci) { f_00729120_orig(c); return; }
    uint32_t before = M32(ci + 0x8C);
    f_00729120_orig(c);
    uint32_t inst = M32(self + 0x20) + 0x40u * idx, info = M32(self + 0x18) + 0x48u * (uint32_t)(int16_t)M16(inst + 0x3E);
    float d = inst_dist(ci, inst), r = MF32(info + 0x38) + 6.0f;
    float size = d > r ? MF32(ci + 0xC0) * r / (d - r) : MF32(ci + 0xC0);
    ++st.calls;
    if (M32(ci + 0x8C) == before) {
        flicker_note(self, idx, size >= 2 && size < 18 ? 1 : 2, d);
        ++st.cull;
        if (size >= 2 && size < 18) { ++st.small; if (d < st.small_min_d) st.small_min_d = d; }
        return;
    }
    uint32_t A = M32(info + 0x28), B = M32(info + 0x30), C = M32(info + 0x34);
    uint32_t m = M32(before) & ~3u;     // the entry is model + visible state (1, 2), 0072940C
    flicker_note(self, idx, m == A ? 3 : m == B ? 4 : 5, d);
    if (m == B) { ++fr.b; if (d < fr.b_min_d) fr.b_min_d = d; } else if (m == A) ++fr.a;
    if (m == A) { ++st.a; if (d > st.a_max_d) st.a_max_d = d; }
    else if (m == B) { ++st.b; if (d < st.b_min_d) st.b_min_d = d; }
    else if (m == C) ++st.c;
    else ++st.other;
}

static int in_what;                     // inside WhatSectionsShouldWeDraw for the main view
static uint32_t drivable;               // what its FindDrivableSection returned
void f_0072CA00_orig(Cpu *c);
void f_0072CA00(Cpu *c) {               // VisibleSectionManager::FindDrivableSection (this, position) -> section
    f_0072CA00_orig(c);
    if (in_what) drivable = c->eax;
}

// The streamer entry (TheTrackStreamer 009AA9D0: +0 array of 0x5C, +4 count; number +8, state +0xC, 3/4 resident)
// that carries scenery section n: n itself, else its LOD slot n + offset (GetCombinedSectionNumber 007236E0).
// 1 resident, 0 not resident, -1 no entry.
static int stream_resident(int n) {
    uint32_t base = M32(0x009AA9D0u), cnt = M32(0x009AA9D4u);
    int o = (int)M32(LOD_OFFSET);
    for (int pass = 0; pass < 2 && base; ++pass) {
        int want = pass ? n + o : n;
        if (pass && (o <= 0 || n % 100 < 1 || n % 100 >= o)) break;
        for (uint32_t i = 0; i < cnt; ++i) {
            uint32_t e = base + 0x5Cu * i;
            if ((int16_t)M16(e + 8) != want) continue;
            uint32_t st = M32(e + 0xC);
            return st == 3 || st == 4;
        }
    }
    return -1;
}

static int16_t prev[256];
static int prev_zone = -1;
static float race0 = -1;                // sim time when the race started (simshot times are relative to it)
static int nprev;
void f_00731870_orig(Cpu *c);
void f_00731870(Cpu *c) {               // GrandSceneryCullInfo::WhatSectionsShouldWeDraw (this, out, max, cull info) -> count
    uint32_t out = arg(c, 0), ci = arg(c, 2);
    uint32_t view = M32(ci + 0x80);
    int main_view = view && M32(view + 4) == 1;
    in_what = main_view && diag_on;
    drivable = 0;
    f_00731870_orig(c);
    in_what = 0;
    if (!main_view) return;
    if (race0 < 0 && M32(0x00925E90u) == 6) race0 = simtime();   // GAMEFLOW_STATE racing: as diag_simshot_due
    MF32(ci + 0xC0) *= detail_scale();  // CullView runs TreeCull with this cull info next
    if (!diag_on) return;
    int zone = drivable ? (int16_t)M16(drivable + 0xC) : -1;
    if (zone != prev_zone) {           // the camera entered another drivable section: its list, and what isn't loaded
        char miss[512] = "";
        // Only A-T ids are scenery; U-Y (2100-2599) are model/texture packs without a scenery chunk (STREAML2RA.BUN),
        // so a missing ScenerySectionHeaders entry says nothing about them, and 69 of them aren't in the PC streamer
        // table at all (L2RA.BUN 0x34110: AddCurrentStreamingSections 00728060 skips them, retail too). An A-T id is
        // streamed as itself or combined into its LOD slot's file (GetCombinedSectionNumber 007236E0: G64 holds 724),
        // so "not resident" is that streamer entry's state, not the header.
        int nl = drivable ? (int16_t)M16(drivable + 0x10) : 0, nm = 0, nlod = 0, npack = 0;
        for (int i = 0; i < nl; ++i) {
            int s = (int16_t)M16(drivable + 0x12 + 2u * (uint32_t)i);
            nlod += is_lod(s);
            if (s < 100 || s / 100 > 20) { ++npack; continue; }
            if (stream_resident(s) == 0 && strlen(miss) < 480) { snprintf(miss + strlen(miss), 16, " %d", s); ++nm; }
        }
        diag_event("detail: t %.2f camera zone %d -> %d: lists %d sections (%d LOD, %d packs), %d scenery not resident:%s",
                   simtime(), prev_zone, zone, nl, nlod, npack, nm, miss);
        // BOOT_DIAG_POPSHOTS=1: a full-size shot the next frame when sections are missing (streaming before/after)
        static int pops = -1;
        if (pops < 0) pops = getenv("BOOT_DIAG_POPSHOTS") ? 0 : 99;
        if (nm > 0 && pops < 24 && prev_zone >= 0) {
            void diag_shot_request(uint64_t frame);
            diag_shot_request(perf_nframes + 1);
            ++pops;
            diag_event("detail: popshot frame %llu (race sim %.3f + 1 frame)", (unsigned long long)perf_nframes + 1,
                       simtime() - race0);
        }
        prev_zone = zone;
    }
    stats_flush();
    cur_ci = ci;
    memset(&fr, 0, sizeof fr);
    fr.b_min_d = 1e9f;
    int n = (int)c->eax;
    if (n > 256) n = 256;
    int16_t now[256];
    for (int i = 0; i < n; ++i) now[i] = (int16_t)M16(out + 2u * (uint32_t)i);
    for (int i = 0; i < n; ++i) {
        int seen = 0;
        for (int j = 0; j < nprev && !seen; ++j) seen = prev[j] == now[i];
        if (seen) continue;
        int k; float nr, fr;
        section_reach(ci, now[i], &k, &nr, &fr);
        diag_event("detail: t %.1f section %d%s appears: %d instances, nearest %.0f m, farthest %.0f m (drawn %d)",
                   simtime(), now[i], is_lod(now[i]) ? " (LOD)" : "", k, nr, fr, n);
    }
    for (int j = 0; j < nprev; ++j) {
        int seen = 0;
        for (int i = 0; i < n && !seen; ++i) seen = prev[j] == now[i];
        if (seen) continue;
        int k; float nr, fr;
        section_reach(ci, prev[j], &k, &nr, &fr);
        diag_event("detail: t %.1f section %d%s gone: %d instances, nearest %.0f m%s", simtime(), prev[j],
                   is_lod(prev[j]) ? " (LOD)" : "", k, nr, section_header(prev[j]) ? "" : " (unloaded)");
    }
    memcpy(prev, now, sizeof(int16_t) * (size_t)n);
    nprev = n;
}

void f_0072DAC0_orig(Cpu *c);
void f_0072DAC0(Cpu *c) {               // TrackStreamer::LoadSection (this, section)
    uint32_t s = arg(c, 0);
    if (diag_on) {
        char name[9] = {0};
        for (int i = 0; i < 8; ++i) name[i] = (char)M8(s + (uint32_t)i);
        diag_event("detail: t %.1f load %s (%d) %u KB visible %u", simtime(), name, (int16_t)M16(s + 8),
                   M32(s + 0x18) >> 10, M8(s + 0xB));
    }
    f_0072DAC0_orig(c);
}
void f_00727EE0_orig(Cpu *c);
void f_00727EE0(Cpu *c) {               // TrackStreamer::SectionLoadedCallback (this, section)
    uint32_t s = arg(c, 0);
    if (diag_on) {
        char name[9] = {0};
        for (int i = 0; i < 8; ++i) name[i] = (char)M8(s + (uint32_t)i);
        diag_event("detail: t %.1f loaded %s (%d) visible %u", simtime(), name, (int16_t)M16(s + 8), M8(s + 0xB));
    }
    f_00727EE0_orig(c);
}

// BOOT_DIAG_DETAIL_AB=1 (with BOOT_DIAG_SIMSHOTS): after each simshot two held passes (pacing.c: no tick, no real
// time, the same scene) are drawn with the detail scale forced to 2 and 4, saved as simshot_<t>_k2.png / _k4.png;
// events.log has each pass's main-view scenery (LOD A / B entries, nearest LOD B) as `detail ab:`.
static int ab = -1, ab_state;           // 1 shot taken, 2 k2 pass, 3 its shot, 4 k4 pass, 5 its shot
static char ab_base[512];
static int detail_ab(void) {
    if (ab < 0) ab = diag_on && getenv("BOOT_DIAG_DETAIL_AB") ? 1 : 0;
    return ab;
}
static void ab_log(const char *what) {
    diag_event("detail ab: %s frame %llu scale %.0f: scenery LOD A %u, LOD B %u, nearest LOD B %.0f m", what,
               (unsigned long long)perf_nframes, detail_scale(), fr.a, fr.b, fr.b ? fr.b_min_d : 0);
}
int detail_ab_shot(char *path, size_t n) {   // render_present.m, each Present: `path` is this frame's shot (or empty)
    if (!detail_ab()) return 0;
    if (ab_state == 2 || ab_state == 4) {
        snprintf(path, n, "%s_k%d.png", ab_base, ab_state == 2 ? 2 : 4);
        ab_log(path);
        ++ab_state;
        return 1;
    }
    if (ab_state == 0 && path[0] && strstr(path, "/simshot_")) {
        snprintf(ab_base, sizeof ab_base, "%.*s", (int)(strlen(path) - 4), path);
        ab_log(path);
        ab_state = 1;
    }
    return 0;
}
int detail_ab_hold(void) {              // pacing.c, end of each main-loop pass: 1 = hold the next pass
    if (ab_state == 1) { ab_state = 2; ab_k = 2; return 1; }
    if (ab_state == 3) { ab_state = 4; ab_k = 4; return 1; }
    if (ab_state == 5) { ab_state = 0; ab_k = 0; }
    return 0;
}

// ---- Frame memory (disappearing traffic near the Rosewood tunnel) ----
// eInitEngine (005009D0) gives the game two 400 KB frame-malloc buffers (0x64000 each, CRT malloc 007C66D0; swapped
// each frame by eSwapFrameMallocBuffers 004FAD80; freed by eCloseEngine with delete 007C7250). Every view's scenery
// and car draws take their render entries and matrices from it, and when it runs out CarRenderInfo::Render (0074E160)
// returns without drawing the car (CurrentBufferPos + 0x130 >= CurrentBufferEnd, or eFrameMallocMatrix gives 0):
// the car blinks out for that frame. The port's world (Far detail distance, the X360 water's road-reflection view,
// which renders a second set of scenery and cars) needs more than the PC's 400 KB in places: free roam into the tunnel
// with the X360 water on used 409,584 of 409,600 bytes and failed 1-5 view-1 car renders a frame (frames.log fmem).
// The buffers are replaced with 4x larger ones from the same allocator, so eCloseEngine frees them as before.
// NFS_FRAME_MEMORY_KB=<n> (test only) picks the size; 400 is the PC's.
#define FRAME_BUF_A   0x00915128u       // FrameMemoryBuffer
#define FRAME_BUF_B   0x0091512Cu       // the other buffer
#define FRAME_POS     0x00915F84u       // CurrentBufferPos
#define FRAME_END     0x00915F88u       // CurrentBufferEnd
#define FRAME_SIZE    0x00915F7Cu       // FrameMemoryBufferSize
#define FRAME_ALLOCS  0x00915F90u       // FrameMallocAllocNum
#define GUEST_MALLOC  0x007C66D0u
#define GUEST_DELETE  0x007C7250u
uint32_t call_guest(Cpu *c, uint32_t fn, int n, const uint32_t *args);
void f_005009D0_orig(Cpu *c);
void f_005009D0(Cpu *c) {               // eInitEngine
    f_005009D0_orig(c);
    uint32_t ret = c->eax;
    const char *e = getenv("NFS_FRAME_MEMORY_KB");
    uint32_t kb = e && atoi(e) >= 64 && atoi(e) <= 65536 ? (uint32_t)atoi(e) : 1600, size = kb << 10;
    if (size > M32(FRAME_SIZE) && M32(FRAME_BUF_A) && M32(FRAME_BUF_B)) {
        uint32_t esp = c->esp, a = call_guest(c, GUEST_MALLOC, 1, &size), b = call_guest(c, GUEST_MALLOC, 1, &size);
        if (a && b) {
            uint32_t old[2] = {M32(FRAME_BUF_A), M32(FRAME_BUF_B)};
            for (int i = 0; i < 2; ++i) call_guest(c, GUEST_DELETE, 1, &old[i]);
            M32(FRAME_BUF_A) = a; M32(FRAME_BUF_B) = b;
            M32(FRAME_POS) = a; M32(FRAME_END) = a + size; M32(FRAME_SIZE) = size; M32(FRAME_ALLOCS) = 0;
        } else {
            if (a) call_guest(c, GUEST_DELETE, 1, &a);
            if (b) call_guest(c, GUEST_DELETE, 1, &b);
        }
        c->esp = esp;
    }
    c->eax = ret;
    if (diag_on) diag_event("framemem: frame buffers 2 x %u KB (PC 400 KB)", M32(FRAME_SIZE) >> 10);
}
