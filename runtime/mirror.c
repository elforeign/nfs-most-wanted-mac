// mirror.c - rear-view mirror resolution and world detail (NFS_MIRROR_SIZE, NFS_MIRROR_DETAIL; F10 Graphics).
//
// The PC draws the mirror view (eViews[3] 00919730, RenderingViewMode 2: bumper and hood cameras) into a texture
// (__g_ID3Texture 00982A7C) and its own depth surface, both created by FUN_006BCFA0 at the size in the static words
// 008F9008/008F900C (256x256, no MSAA; read only there and by FUN_006BD0D0, which gives render-target table entry 3
// 0093DB0C the same size), and composites it in the HUD pass FUN_006E6E40 as an ePoly of (190..450, 19..89) FE
// units: 260x70 of the 640x480 FE space, 1170x315 pixels at 2160p (the FE layer is pillarboxed, widescreen.c), so
// each texel is stretched 4.6x across and 1.2x down. The view's projection is a square 110 deg frustum (m00 = m11 =
// 0.702 from the 256x256 viewport): the PC look is that frustum squeezed into the 3.7:1 quad. The camera is
// RearViewMirrorCameraMover::Update 00476690: fov 20000 (bAngle, 109.9 deg) at +0xC4, near/far RVMnearz/RVMfarz, the
// player camera's matrix turned to face backwards. The left-right flip of a mirror is in the composite, not the
// camera: the quad's first vertex is at x 450 and the second at 190 (FUN_006E6E40), so u runs right to left.
//
// NFS_MIRROR_VIEW=<hfov deg>|pc (default 60: the PC's 110 deg looks stretched; F10 Graphics "Mirror view",
// live): a frustum with the quad's own aspect (260x70 FE units, 3.71:1 on screen at any display aspect), so the world
// is undistorted: m00 = 1/tan(hfov/2), m11 = m00 * aspect (vertical fov 2 atan(tan(hfov/2) / aspect): 17.6 deg at 60).
// It is written into the projection before FUN_006CF400 multiplies it with the view matrix (widescreen.c's hook), so
// the six frustum planes it extracts from that product (+0x140..) and the depth-biased copy follow: culling matches
// the picture. The view's pixel scale +0xC (FUN_006BE9F0: (__window_width/2)/tan(fov/2), the back buffer's width for
// every view, 1348 for the mirror at 2160p) becomes the quad's on-screen (width/2)/tan(hfov/2), so objects are culled
// (>= 18 px with full detail) and LOD-picked at the size they appear in the mirror, as in the main view. pc keeps the
// squeezed 110 x 110 deg view and the PC's pixel scale. For scale: a flat interior mirror shows the world at the eye's
// own magnification, which on this screen layout (quad 1170 of 3840 px, main view ~94 deg across) is ~36 deg; the
// PC's 110 deg pinches the world 3x at the edges of a rectilinear projection. 60 is in between: a slightly convex
// mirror's view (0.55x the main view's scale) that still spans the lanes behind.
//
// NFS_MIRROR_SIZE=pc|50|67|100 (default 100): the target becomes that percentage of the on-screen quad (back-buffer
// height / 480 pixels per FE unit), never fewer texels than the PC's 256 on either axis. With NFS_MIRROR_VIEW=pc the
// projection is kept square (m11 = m00) so the picture is the PC's, only sharper. The size doesn't change culling
// (the pixel scale comes from the back buffer's width, not the target's).
//
// NFS_MIRROR_DETAIL=pc|full (default full since 2026-10-21): the PC culls the mirror like an environment-map face, not like the main
// view. FUN_006BFDD0 hands its cull info flag 0x1000 (main view 0x6000) and FUN_004FAE40 adds 0x8000040 | 2 for it
// and 0x20 for view type 3; DrawAScenery 00729160 then needs >= 32 px and draws model slot +0x34 (the lowest LOD)
// where the main view needs >= 18 px and draws +0x28 (LOD A) at world LOD 2. StuffScenery(view 3, 1) also ORs
// render flag 0x1000 into every model it queues (the main view passes 0x20, which StuffScenery doesn't read). full
// gives the mirror the main view's flags: cull info 0x6000 | 0x12 (no 0x1000, 0x8000040, 0x20) and StuffScenery 0
// instead of 1 (the render flag's own effect on the picture is not isolated). Measured at 2160p with size 100
// (draws dumps, hood camera): 395 -> 886 draws, 24.7k -> 190k primitives, trees 8 draws / 160 prims -> 163 / 36.6k.
//
// NFS_MIRROR_FOG=on|off (default on since 2026-10-21; F10 Graphics "Mirror fog", live): the PC draws the mirror's world
// without distance fog. eViewPlatInterface::Render 006DA9B0 picks each solid's effect from the render-target table entry
// being drawn (DAT_00982A20, entries of 7 words at 0093DAB8 whose word 0 is the entry's index, FUN_006BD0D0): for any
// entry but 0 (the back buffer) WORLD (effect 0) becomes WORLDNOFOG (8, DAT_0093DE98) and WORLDNORMALMAP (3) and
// WORLDREFLECT (1) become WORLDNORMALMAPNOFOG (30, DAT_0093DEF0); entry 4 also swaps CAR and GLOSSYWINDOW. That is
// right for the env-map faces and the road reflection, not for the mirror (entry 3, 0093DB0C), a view of the same
// world from the same eye that the main view fogs. on: while Render runs for entry 3, its word 0 reads 0, so the mirror
// gets the main view's effects (nothing else in Render or the draw it queues, FUN_006E0AB0, reads word 0; the effect
// setup at flush time, FUN_006C6630, still sees 3).
//
// NFS_MIRROR_MSAA=on|off (default on since 2026-10-21; F10 Graphics "Mirror anti-aliasing", live via device reset): the PC creates the
// mirror texture and its depth without multisampling. on: both get the back buffer's sample count (the game's
// Antialiasing option; none when that is off), render.m draws the mirror into a multisampled texture and resolves it
// into the mirror texture at the end of each pass (render_target_msaa).
#include <math.h>
#include <stdlib.h>
#include <string.h>

#include "host.h"

#define MIRROR_W 0x008F9008u            // static: the mirror texture's width and height (256, 256)
#define MIRROR_H 0x008F900Cu
#define VIEW_MIRROR 0x00919730u         // eViews[3]
#define WINDOW_WIDTH 0x00982BE4u        // __window_width, __window_height: the back buffer
#define WINDOW_HEIGHT 0x00982BE8u
#define MIRROR_TEXTURE 0x00982A7Cu      // __g_ID3Texture: the mirror texture (level 0 surface at 00982A80)
#define MIRROR_DEPTH 0x00982A84u        // __g_I3DSurface: its depth surface
#define RT_CURRENT 0x00982A20u          // the render-target table entry being drawn
#define RT_MIRROR 0x0093DB0Cu           // entry 3: the mirror (word 0 = 3)

static int size_pct = -1, detail_full = -1;
static float view_hfov = -1;            // NFS_MIRROR_VIEW: horizontal field of view in degrees; 0 = pc (squeezed)
static void mirror_options(void) {
    if (view_hfov < 0) {
        const char *v = getenv("NFS_MIRROR_VIEW");
        float d = v && *v ? (float)atof(v) : 60.0f;                                                                 
        view_hfov = d >= 10.0f && d <= 150.0f ? d : 0.0f;
    }
    if (size_pct >= 0) return;
    const char *e = getenv("NFS_MIRROR_SIZE");
    size_pct = !e || !*e ? 100 : atoi(e) > 0 ? atoi(e) : 0;                                                      
    if (size_pct > 100) size_pct = 100;
    e = getenv("NFS_MIRROR_DETAIL");
    detail_full = !e || !*e || !strcmp(e, "full");            // unset: full (the default)
}
static int resized;                     // the target isn't square any more: keep the PC's square projection
static int fog_on = -1, msaa_on = -1;   // NFS_MIRROR_FOG, NFS_MIRROR_MSAA (default on)
static int pending_size = -1, pending_msaa = -1;
void graphics_reset_request(void);
void mirror_set_size(int pct) { pending_size = pct; graphics_reset_request(); }
void mirror_set_msaa(int on) { pending_msaa = on; graphics_reset_request(); }
void mirror_set_detail(int full) {
    mirror_options(); detail_full = full;
    if (diag_on) diag_event("mirror: world detail now %s (next cull)", full ? "full" : "pc");
}
static int env_on(const char *name) { const char *e = getenv(name); return !e || !*e || strcmp(e, "off"); }

void f_006BCFA0_orig(Cpu *c);
void f_006BCFA0(Cpu *c) {               // creates the mirror texture and depth surface (and the 320x200 one at 00982B18 > 0)
    mirror_options();
    if (pending_size >= 0) { size_pct = pending_size; pending_size = -1; }
    if (pending_msaa >= 0) { msaa_on = pending_msaa; pending_msaa = -1; }
    uint32_t w = 256, h = 256, bh = M32(WINDOW_HEIGHT);
    if (size_pct && bh) {
        float s = (float)bh / 480.0f * (float)size_pct / 100.0f;
        uint32_t qw = (uint32_t)lroundf(260.0f * s), qh = (uint32_t)lroundf(70.0f * s);
        w = qw > 256 ? qw : 256;
        h = qh > 256 ? qh : 256;
    }
    M32(MIRROR_W) = w;
    M32(MIRROR_H) = h;
    resized = w != h;
    if (diag_on) diag_event("mirror: target %ux%u (size %d%%, quad %.0fx%.0f px), detail %s", w, h,
                            size_pct, 260.0f * bh / 480.0f, 70.0f * bh / 480.0f,
                            detail_full ? "full" : "pc");
    f_006BCFA0_orig(c);
    if (msaa_on < 0) msaa_on = env_on("NFS_MIRROR_MSAA");
    int samples = msaa_on ? render_msaa_samples() : 1;
    if (samples > 1) {
        int ok = render_target_msaa(M32(MIRROR_TEXTURE), M32(MIRROR_DEPTH), samples);
        if (diag_on) diag_event("mirror: %dx MSAA %s", samples, ok ? "on" : "not applied (already in use)");
    } else if (diag_on) diag_event("mirror: MSAA off (%s)", msaa_on ? "main view has no MSAA" : "mirror setting off");
}

void mirror_set_fog(int on) { fog_on = on; }   // F10 "Mirror fog"

void f_006DA9B0_orig(Cpu *c);
void mirror_scenery_render(Cpu *c) {   // called by materials.m's scoped 006DA9B0 hook
    if (M32(RT_CURRENT) != RT_MIRROR) { f_006DA9B0_orig(c); return; }
    if (fog_on < 0) fog_on = env_on("NFS_MIRROR_FOG");
    if (!fog_on) { f_006DA9B0_orig(c); return; }
    M32(RT_MIRROR) = 0;                 // the effect choice reads "the back buffer": the fogged world effects
    f_006DA9B0_orig(c);
    M32(RT_MIRROR) = 3;
}

void mirror_set_view(float hfov) { mirror_options(); view_hfov = hfov; }   // F10 "Mirror view" (0 = pc)

// From widescreen.c's projection hook (FUN_006CF400's D3DXMatrixMultiply, after it set the pixel scale +0xC).
void mirror_projection(uint32_t view, uint32_t proj) {
    if (view != VIEW_MIRROR) return;
    mirror_options();
    if (view_hfov <= 0) {               // pc: the square frustum the 256x256 target gives (the game's vertical is fov/2 * h/w)
        if (resized) MF32(proj + 20) = copysignf(fabsf(MF32(proj)), MF32(proj + 20));
        return;
    }
    // The quad on screen: 260x70 FE units; FE units are kx * W/640 by ky * H/480 pixels (widescreen.c's fe_transform).
    uint32_t bw = M32(WINDOW_WIDTH), bh = M32(WINDOW_HEIGHT);
    if (!bw || !bh) return;
    float fe = widescreen_fe_scale();   // kx/ky: (4/3)/aspect when pillarboxed
    // ky is 1 from 16:9 up (pillarboxed); between 3:2 and 16:9 (wide HUD scaled down) it is < 1 and the pixel
    // scale below comes out up to 11 % high (more detail, never less). The aspect is exact either way.
    float qw = 260.0f * (float)bw / 640.0f * fe, qh = 70.0f * (float)bh / 480.0f;
    float aspect = qw / qh, t = tanf(view_hfov * 0.5f * (float)M_PI / 180.0f);
    float m00 = 1.0f / t, m11 = m00 * aspect;
    MF32(proj) = copysignf(m00, MF32(proj));
    MF32(proj + 20) = copysignf(m11, MF32(proj + 20));
    MF32(view + 0xC) = qw * 0.5f / t;   // pixels per unit at distance 1, as FUN_006BE9F0 gives the main view
    if (diag_on) { static int once; if (!once++) diag_event("mirror: view %.0f x %.1f deg (quad %.0fx%.0f px, aspect %.3f), m00 %.4f m11 %.4f, pixel scale %.1f",
                                                       view_hfov, 2.0f * atanf(t / aspect) * 180.0f / (float)M_PI, qw, qh, aspect, m00, m11, MF32(view + 0xC)); }
}

void f_004FAE40_orig(Cpu *c);
void f_004FAE40(Cpu *c) {               // (view, cull info, flags): sets the cull info's flags (+0x84)
    mirror_options();
    uint32_t view = arg(c, 0), ci = arg(c, 1);
    f_004FAE40_orig(c);
    if (view != VIEW_MIRROR || !detail_full) return;
    uint32_t f = M32(ci + 0x84), g = (f & ~(0x1000u | 0x8000040u | 0x20u)) | 0x6000u | 0x12u;
    if (diag_on && f != g) { static int once; if (!once++) diag_event("mirror: full detail: cull flags %08X -> %08X", f, g); }
    M32(ci + 0x84) = g;
}

static int mirror_stuffed;              // the mirror's scenery is queued and not drawn yet (see the flare order below)
void f_00723FA0_orig(Cpu *c);
void f_00723FA0(Cpu *c) {               // GrandSceneryCullInfo::StuffScenery (this, view, flags)
    if (arg(c, 0) == VIEW_MIRROR) mirror_stuffed = 1;
    if (detail_full && arg(c, 0) == VIEW_MIRROR && arg(c, 1) == 1) M32(c->esp + 8) = 0;
    f_00723FA0_orig(c);
}

// ---- Flares after the mirror's world ----
// The game's view renderer (006D6820, the mirror at ~7100) queues the mirror's scenery (StuffScenery) and its cars
// (thunk 00750B10), and draws the queue with the sort-and-draw call FUN_006E2F50. Every other view calls it before its
// flare pass (the main view, the env-map faces at 7066: world first, flares on top, depth-tested); the mirror calls it
// only after the flare pass, so the lights (cop lightbars, headlights, lamps) went into the mirror target first and
// the world's opaque draws then covered them wherever there was geometry: a lightbar showed only against the sky
// (BOOT_DIAG mirror pass log: 2 flare draws at draw 13, 290 opaque draws after). Draw the queue before the flare pass
// for the mirror view only: wrapper after 00750B10, the cars' queueing call that just precedes the pass.
// NFS_MIRROR_FLARE_ORDER=pc keeps the PC order.
static int flare_order = -1;
void f_00750B10_orig(Cpu *c);
void f_00750B10(Cpu *c) {
    f_00750B10_orig(c);
    if (!mirror_stuffed) return;
    mirror_stuffed = 0;
    if (flare_order < 0) flare_order = !getenv("NFS_MIRROR_FLARE_ORDER") || strcmp(getenv("NFS_MIRROR_FLARE_ORDER"), "pc");
    if (!flare_order) return;
    Cpu saved = *c;
    call_guest(c, 0x006E2F50u, 0, NULL);
    *c = saved;
}

// ---- Flare sprites in the mirror: a margin for the centre-point frustum test; BOOT_DIAG "flare:" line ----
// eRenderLightFlare 00505380 (view, flare, ...) draws a flare only when its centre passes the six frustum planes
// (GetVisibleStateSB 006BE8F0, radius 0; planes at *view+0x140, 16 bytes each, order from FUN_006CF400: 0 left, 1 right,
// 2 bottom, 3 top, 4 near, 5 far) and the distance gate. The PC's mirror frustum is 110 deg wide, the port's default
// 60 deg: a cop's lightbar beside the mirror's edge was culled whole by its centre (a pursuit run: whole seconds with
// 5-100 % of the mirror's cop flares rejected by plane 0/1, none by 2-5) though its sprite, up to ~2 m across, reached
// into the picture. For the mirror view only, a flare whose centre fails one of the four side planes by less than the
// sprite's world half-extent is drawn. Size as eRenderLightFlare computes it: lerp(p[0], p[1], intensity) *
// clamp(depth / view+0xC, p[8], p[9]) * param_9 for the two parameter blocks at 008F2868 + 8 * flare type (the largest
// is used, with the camera distance for the depth, never smaller than the real depth's clamp), times 1.5 for the quad's
// diagonal. NFS_MIRROR_FLARE=pc keeps the exact PC test. The main view and every other caller of 006BE8F0 are untouched.
// BOOT_DIAG counters (types 5/6 = cop lightbar, per view id view+4: 1 main, 3 mirror), one line a second: submitted to
// 00505380, drawn (returns nonzero), rejected by plane 0-5, rejected otherwise (distance gate), saved by the margin,
// and for the plane rejects the camera distance and how far outside the plane the centre was (metres, bins <.5 <1.5 <4 more).
typedef struct { unsigned sub, drawn, plane[6], other, saved, bin[4]; float dmin, dmax, out_max; } FlareStat;
static FlareStat fl_stat[2];             // [0] main, [1] mirror
static int fl_in = -1;                   // stat index of the cop flare being submitted, or -1
static int fl_view_mirror;               // the flare being submitted is the mirror's
static uint32_t fl_type; static float fl_scale = 1, fl_intensity = 1;
static int fl_plane = -1; static float fl_out;
static int flare_margin = -1;

void f_006BE8F0_orig(Cpu *c);
void f_006BE8F0(Cpu *c) {               // eViewPlatInterface::GetVisibleStateSB (this, point), radius 0
    uint32_t self = c->ecx, pt = arg(c, 0);
    f_006BE8F0_orig(c);
    if (!fl_view_mirror || c->eax) return;
    uint32_t pl = M32(self) + 0x140, cam = M32(self + 0x40);
    float x = MF32(pt), y = MF32(pt + 4), z = MF32(pt + 8);
    float dx = x - MF32(cam + 0x40), dy = y - MF32(cam + 0x44), dz = z - MF32(cam + 0x48), dist = sqrtf(dx * dx + dy * dy + dz * dz);
    float d[6], nn[6]; int fail = -1, fails = 0;
    for (int i = 0; i < 6; ++i) {
        uint32_t p = pl + 16 * i;
        d[i] = MF32(p) * x + MF32(p + 4) * y + MF32(p + 8) * z + MF32(p + 12);
        nn[i] = sqrtf(MF32(p) * MF32(p) + MF32(p + 4) * MF32(p + 4) + MF32(p + 8) * MF32(p + 8));
        if (d[i] <= 0) { if (fail < 0) fail = i; ++fails; }
    }
    if (fail < 0) return;
    if (flare_margin < 0) flare_margin = !getenv("NFS_MIRROR_FLARE") || strcmp(getenv("NFS_MIRROR_FLARE"), "pc");
    float out = nn[fail] > 0 ? -d[fail] / nn[fail] : -d[fail];
    if (diag_on && fl_in >= 0) { fl_plane = fail; fl_out = out; }
    if (!flare_margin || fail > 3) return;
    // the sprite's half-extent: the larger parameter block, at the camera distance
    float scale = MF32(self + 0xC) > 0 ? dist / MF32(self + 0xC) : 0, size = 0;
    for (int k = 0; k < 2; ++k) {
        uint32_t pp = M32(0x008F2868u + 8 * fl_type + 4 * k);
        if (!pp) continue;
        float sc = scale < MF32(pp + 32) ? MF32(pp + 32) : scale > MF32(pp + 36) ? MF32(pp + 36) : scale;
        float lo = MF32(pp), hi = MF32(pp + 4), sz = (lo > hi ? lo : hi) * sc * fl_scale;
        if (sz > size) size = sz;
    }
    float r = size * 1.5f;
    for (int i = 0; i < 4; ++i) if (nn[i] > 0 && d[i] / nn[i] + r <= 0) return;   // a side plane the sprite doesn't reach
    for (int i = 4; i < 6; ++i) if (d[i] <= 0) return;                              // near/far: exact
    c->eax = 2;
    if (diag_on && fl_in >= 0) fl_plane = -2;
    (void)fails;
}

void f_00505380_orig(Cpu *c);
void f_00505380(Cpu *c) {               // eRenderLightFlare (view, flare, matrix, intensity, reflexion, type, ...)
    uint32_t view = arg(c, 0), flare = arg(c, 1);
    lampglow_begin(c);
    int st = -1;
    fl_view_mirror = view == VIEW_MIRROR && flare;
    if (fl_view_mirror) { fl_type = (uint32_t)(int32_t)(int8_t)M8(flare + 0x2c); if (fl_type > 16) fl_view_mirror = 0; fl_scale = MF32(c->esp + 4 + 32); fl_intensity = MF32(c->esp + 16); }
    if (diag_on && flare) {
        int t = (int8_t)M8(flare + 0x2c), id = (int)M32(view + 4);
        if ((t == 5 || t == 6) && (id == 1 || id == 3)) st = id == 3;
    }
    if (st < 0) { f_00505380_orig(c); lampglow_end(); fl_view_mirror = 0; return; }
    fl_in = st; fl_plane = -1;
    f_00505380_orig(c);
    lampglow_end();
    fl_in = -1; fl_view_mirror = 0;
    FlareStat *s = &fl_stat[st];
    ++s->sub;
    if (fl_plane == -2) ++s->saved;
    if (c->eax) { ++s->drawn; return; }
    if (fl_plane < 0) { ++s->other; return; }
    ++s->plane[fl_plane];
    unsigned n = 0; for (int i = 0; i < 6; ++i) n += s->plane[i];
    ++s->bin[fl_out < 0.5f ? 0 : fl_out < 1.5f ? 1 : fl_out < 4.0f ? 2 : 3];
    if (fl_out > s->out_max) s->out_max = fl_out;
    (void)n;
}

int mirror_flare_cop(void) { return fl_view_mirror && (fl_type == 5 || fl_type == 6); }   // render.m's flaredraw diag: inside 00505380 for a mirror cop flare

void mirror_flare_info(int *type, float *intensity) { *type = (int)fl_type; *intensity = fl_intensity; }

void mirror_diag_second(void) {
    if (!diag_on) return;
    FlareStat z = {0};
    if (!memcmp(&fl_stat[0], &z, sizeof z) && !memcmp(&fl_stat[1], &z, sizeof z)) return;
    char line[400]; int n = 0;
    for (int v = 0; v < 2; ++v) {
        FlareStat *s = &fl_stat[v];
        n += snprintf(line + n, sizeof line - n, " v%d sub=%u drawn=%u saved=%u plane=%u/%u/%u/%u/%u/%u other=%u", v ? 3 : 1, s->sub, s->drawn, s->saved,
                      s->plane[0], s->plane[1], s->plane[2], s->plane[3], s->plane[4], s->plane[5], s->other);
        unsigned pr = 0; for (int i = 0; i < 6; ++i) pr += s->plane[i];
        if (pr) n += snprintf(line + n, sizeof line - n, " rejout<.5/<1.5/<4/more=%u/%u/%u/%u max=%.2fm", s->bin[0], s->bin[1], s->bin[2], s->bin[3], s->out_max);
    }
    diag_event("flare:%s", line);
    memset(fl_stat, 0, sizeof fl_stat);
}
