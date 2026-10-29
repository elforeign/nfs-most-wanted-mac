// x360.m - X360 renderer features applied to the PC game.
//
// Shadow range (plan milestone M1, data only): the PC shadow-map fit (FUN_006e44c0) is the X360 one with different
// tuning. NFS_SHADOW_FIT=x360range writes the X360 values every frame: focus frustum far in races and in the front end
// (00901AB0, PC 50; X360E_SHADOW_FOCUS_RACE / _FE, from the player's default.xex), the full horizontal focus angle (00901AB4, PC 0.75) and PerspectiveLH width
// factor 1 (00901A94, PC 1.5). NFS_SHADOW_FIT=pc keeps the PC values. Only matters with g_ShadowDetail > 0.
// Exact X360 light camera (M2, NFS_SHADOW_FIT=x360, the default): FUN_006e44c0 builds view 15's light
// view and projection and multiplies them (D3DXMatrixMultiply at 006E50C3) just before the casters draw; at that call
// the view becomes identity and the projection the X360 LiSPSM fit (x360_shadow_fit.cpp) over the player's frustum, so
// the view-projection the casters and receivers use is X360's. The light direction is the one the PC fit uses:
// *TimeOfDayPointer + 0x180, pointing towards the sun (the PC puts its light at centre + k * that).
// The rest of the X360 shadow path (plan S3, S4, §1.4, item 9), on with NFS_SHADOW_FIT=x360 (default), each overridable:
// - map size (NFS_SHADOW_SIZE, X360 X360E_SHADOW_SIZE, PC 1024): GetShadowWidth/Height (006C86B0/C0) and the colour and depth
//   textures FUN_006c8770 creates. Every user of the size (RT table viewport, SHADOWMAPSCALE = 1/size, the car
//   shadow's texel centre at 006D6BE2) asks GetShadowWidth/Height.
// - texel centre (always): the bias matrix FUN_006e44c0 multiplies into view 15's projection (D3DXMatrixMultiply
//   at 006E5428) has 0.5 + 0.5/1024 compiled in (006E53FC/006E5407); it becomes 0.5 + 0.5/size. D3D9 pixel centres
//   are emulated (sm_translate pos_fixup), so the D3D9 half-texel term is right for the actual size.
// - white border (NFS_SHADOW_BORDER=white|clamp): receivers sample the depth map with CLAMP; X360 uses BORDER with
//   a white border, so outside the map is lit. render.m forces it on every compare (shadow-map) sampler.
// - caster cull (NFS_SHADOW_CULL=front|none): FUN_006c68b0 sets the effect's cull mode from the pass mode
//   008FAE6C: 1 (shadow pass) gives D3DCULL_NONE, 2 (normal) CW, 3 (mirrored) CCW, the X360 caster rule
//   (eEffect_SetMaterialTextures: front faces culled, double-sided materials stay NONE). So in the shadow pass
//   (00982B38 = 1) the function runs with mode 3. FUN_006c6d10 skips the material setup when a draw repeats the last
//   material, so the cull modes set this way go back to NONE (what PC leaves) once FUN_006e44c0 returns; the game's
//   own cache reset (FUN_006cfce0) only comes after the flush at 006DEC80.
//
// Tone curve (plan milestone M3): the visual treatment looks its curves up in a 256x1 LUT (SPLINE, 00982900) that
// FUN_006c3b20 rebuilds every frame: R = BlackBloomCurve (VT +0x10, the desaturated-scene term), G =
// ColourBloomCurve (VT +0x50, the tinted colour-bloom term), B = DetailMapCurve. X360 drives both terms with the
// BlackBloomCurve and never reads the ColourBloomCurve, so with NFS_TONE_CURVE=x360 (opt-in; PC's curve is the default)
// G is set to R after the game builds the LUT.
//
// The X360 post chain is in x360_post.m, the X360 HD textures in x360_textures.m; x360_internal.h has what they share.
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <math.h>

#include "x360_internal.h"
#include "materials.h"
#include "gen/x360_engine.inc"   // X360E_SHADOW_*: the X360 shadow map size, focus and near plane (default.xex)

#define SHADOW_FOCUS_FAR   0x00901AB0u
#define SHADOW_FOCUS_ANGLE 0x00901AB4u
#define SHADOW_WIDTH       0x00901A94u

static int fit = -1;                     // 0 PC, 1 X360 range, 2 X360 range + light camera and the rest (default)
static int shadow_size = 1024, shadow_border, shadow_cull_front;
static int pending_fit = -1;
static float pc_shadow[3];
static int pc_shadow_saved;
// Only queue the game's own device reset. eDisplayFrame consumes it before drawing, after Present ended encoding.
void graphics_reset_request(void) {
    G[0x00982C39u] = 1;                 // LoadedFlagMaybe, also used by Options and widescreen_display_changed
    if (diag_on) diag_event("graphics: device reset queued for next eDisplayFrame");
}
void x360_set_shadow_fit(int v) { pending_fit = v; graphics_reset_request(); }
static void shadow_options(void) {
    if (fit >= 0) return;
    const char *e = getenv("NFS_SHADOW_FIT");
    fit = e && !strcmp(e, "pc") ? 0 : e && !strcmp(e, "x360range") ? 1 : 2;
    if (pending_fit >= 0) fit = 2 - pending_fit;   // F10: x360, x360range, pc
    if (!X360_ENGINE_CONSTS) fit = 0;               // no X360 default.xex: the X360 shadow values are unknown, PC fit
    if (!pc_shadow_saved) {
        pc_shadow[0] = F32(SHADOW_FOCUS_FAR); pc_shadow[1] = F32(SHADOW_FOCUS_ANGLE); pc_shadow[2] = F32(SHADOW_WIDTH);
        pc_shadow_saved = 1;
    }
    shadow_size = fit == 2 ? X360E_SHADOW_SIZE : 1024;
    if ((e = getenv("NFS_SHADOW_SIZE")) && atoi(e) >= 256 && atoi(e) <= 4096) shadow_size = atoi(e);
    shadow_border = fit == 2;
    if ((e = getenv("NFS_SHADOW_BORDER"))) shadow_border = !strcmp(e, "white");
    shadow_cull_front = fit == 2;
    if ((e = getenv("NFS_SHADOW_CULL"))) shadow_cull_front = !strcmp(e, "front");
    if (diag_on) diag_event("x360: shadow fit %s, map %dx%d, border %s, caster cull %s", fit == 2 ? "x360" : fit ? "x360range" : "pc",
                            shadow_size, shadow_size, shadow_border ? "white" : "clamp", shadow_cull_front ? "front" : "none");
}
int x360_shadow_border(void) { shadow_options(); return shadow_border; }

void f_006C86B0(Cpu *c) { shadow_options(); c->eax = (uint32_t)shadow_size; c->esp += 4; }   // GetShadowWidth
int x360_shadow_map_size(void) { shadow_options(); return shadow_size; }                    // the overlay
void f_006C86C0(Cpu *c) { shadow_options(); c->eax = (uint32_t)shadow_size; c->esp += 4; }   // GetShadowHeight
static int creating_shadow_maps;
void f_006C8770_orig(Cpu *c);
void f_006C8770(Cpu *c) {                // creates the shadow colour target and depth texture (1024 compiled in)
    if (pending_fit >= 0) fit = -1;      // refresh the preset at recreation, retaining explicit size/border/cull overrides
    shadow_options();
    pending_fit = -1;
    if (!fit) {
        put_float(SHADOW_FOCUS_FAR, pc_shadow[0]); put_float(SHADOW_FOCUS_ANGLE, pc_shadow[1]); put_float(SHADOW_WIDTH, pc_shadow[2]);
    }
    creating_shadow_maps = 1;
    f_006C8770_orig(c);
    creating_shadow_maps = 0;
}
void x360_shadow_texture_size(uint32_t *w, uint32_t *h) {   // from d3d9.m's CreateTexture
    if (!creating_shadow_maps || *w != 1024 || *h != 1024) return;
    *w = *h = (uint32_t)shadow_size;
    if (diag_on) diag_event("x360: shadow map texture %ux%u", *w, *h);
}

#define SHADOW_PASS 0x00982B38u          // 1 while FUN_006e44c0 draws the casters
#define PASS_MODE   0x008FAE6Cu          // 1 shadow, 2 normal, 3 mirrored: picks the effect cull mode in FUN_006c68b0
#define CUR_EFFECT  0x00982C80u          // current effect wrapper: +0x44 handles (+0x1DC cull mode), +0x48 ID3DXEffect
static uint32_t culled[64][2];           // (effect, cull handle) pairs given a front-face cull in this shadow pass
static int nculled;
static uint32_t shadow_source_view;
static int x360_vp_set;
void f_006C68B0_orig(Cpu *c);
void f_006C68B0(Cpu *c) {                // per-material effect states, including the cull mode
    if (!shadow_cull_front || M32(SHADOW_PASS) != 1 || M32(PASS_MODE) != 1) { f_006C68B0_orig(c); return; }
    M32(PASS_MODE) = 3;
    f_006C68B0_orig(c);
    M32(PASS_MODE) = 1;
    uint32_t w = M32(CUR_EFFECT), fx = w ? M32(w + 0x48) : 0, h = w ? M32(M32(w + 0x44) + 0x1DC) : 0;
    int i = 0;
    while (i < nculled && (culled[i][0] != fx || culled[i][1] != h)) ++i;
    if (fx && h && i == nculled && nculled < 64) { culled[nculled][0] = fx; culled[nculled][1] = h; ++nculled; }
    static int logged;
    if (diag_on && !logged++) diag_event("x360: shadow casters cull front faces (pass mode 3 for FUN_006c68b0)");
}
// Draws reuse an effect's cull mode when their material doesn't change, so after the shadow pass every cull mode set
// above goes back to D3DCULL_NONE, the value the PC shadow pass leaves (else main-pass car parts lose front faces).
void f_006E44C0_orig(Cpu *c);
void f_006E44C0(Cpu *c) {                // shadow light camera + caster pass
    shadow_source_view = M32(c->esp + 4);
    x360_vp_set = 0;
    f_006E44C0_orig(c);
    for (int i = 0; i < nculled; ++i) {
        uint32_t a[3] = {culled[i][0], culled[i][1], 1}, esp = c->esp;
        call_guest(c, M32(M32(culled[i][0]) + 0x68), 3, a);   // ID3DXEffect::SetInt
        c->esp = esp;
    }
    static int logged;
    if (diag_on && nculled && !logged++) diag_event("x360: shadow pass done, %d effect cull modes reset to none", nculled);
    nculled = 0;
}

// Test only (NFS_TEST_SUN_ELEVATION=<deg>): the time-of-day sun direction with its elevation replaced, azimuth
// kept, for checking shadows at a low sun (the races' own sun is ~62 deg high). Applied once a frame and again at the
// shadow fit so lighting and shadows see the same sun.
#define TIME_OF_DAY_PTR 0x009B392Cu      // TimeOfDayPointer: +0x8 time of day, +0x180 sun direction (towards the sun, world)
static void test_sun(void) {
    static float elev = -1000;
    if (elev == -1000) { const char *e = getenv("NFS_TEST_SUN_ELEVATION"); elev = e ? (float)atof(e) : -999; }
    uint32_t tod = M32(TIME_OF_DAY_PTR);
    if (elev == -999 || !tod) return;
    float l[3];
    memcpy(l, G + tod + 0x180, 12);
    float h = sqrtf(l[0] * l[0] + l[1] * l[1]), r = elev / 57.29578f;
    if (h < 1e-4f) { l[0] = 1; l[1] = 0; h = 1; }
    float out[3] = {l[0] / h * cosf(r), l[1] / h * cosf(r), sinf(r)};
    memcpy(G + tod + 0x180, out, 12);
}

// Diagnostics: the techniques FUN_006d5c10 picked for an effect (thiscall, arg = effect id): +0x18[4] technique index
// per shadow level, +0x28[4] their "shader" annotation, +0x38 highest shadow level; and the inputs that gate the
// shadow-level techniques (vendor/device 0093D898/9C, 0093D9E4, 008FAD9C, shadow-map format 00982C08).
void f_006D5C10_orig(Cpu *c);
void f_006D5C10(Cpu *c) {
    uint32_t self = c->ecx, id = M32(c->esp + 4);
    f_006D5C10_orig(c);
    if (diag_on) diag_event("x360: effect %u techniques by shadow level %d %d %d %d (shader %d %d %d %d), max level %d; vendor %04X device %04X "
                            "93D9E4 %08X 8FAD9C %u shadow format %u", id, (int)M32(self + 0x18), (int)M32(self + 0x1C), (int)M32(self + 0x20),
                            (int)M32(self + 0x24), (int)M32(self + 0x28), (int)M32(self + 0x2C), (int)M32(self + 0x30), (int)M32(self + 0x34),
                            (int)M32(self + 0x38), M32(0x0093D898), M32(0x0093D89C), M32(0x0093D9E4), M8(0x008FAD9C), M32(0x00982C08));
}

// Test only (NFS_TEST_SHADOW_SWITCH=<s>[:<level>|:mark]): <s> seconds of race time after the race starts, does what
// the Video options' Accept does for a changed shadow detail (FUN_00510300: the setting into g_ShadowDetail 00901830,
// LoadedFlagMaybe 00982C39 = 1, so the next eDisplayFrame runs the game's full device Reset FUN_006db0d0, which creates
// the shadow maps). The scripted menu path is unreliable at 4K (keys dropped while the Reset runs). `mark` changes
// nothing (the boot-on comparison run); either way the frame counts as a scripted key for diag's key+N.
static void test_shadow_switch(void) {
    static const char *e = (const char *)1;
    static float race_t0 = -1;
    if (e == (const char *)1) e = getenv("NFS_TEST_SHADOW_SWITCH");
    if (!e || M32(GAMEFLOW_STATE) != 6) return;
    float now = F32(0x009885D8u);                          // Sim::GetTime
    if (race_t0 < 0) race_t0 = now;
    if (now - race_t0 < (float)atof(e)) return;
    const char *lv = strchr(e, ':');
    int mark = lv && !strcmp(lv + 1, "mark");
    if (!mark) { M32(0x00901830) = lv ? (uint32_t)atoi(lv + 1) : 2; G[0x00982C39] = 1; }
    void diag_mark_key(void);
    diag_mark_key();
    if (diag_on) diag_event("x360: test shadow switch at race time %.2f: %s", now - race_t0, mark ? "mark only" : "g_ShadowDetail set, device reset requested");
    e = NULL;
}

void x360_frame(void) {
    mat_water_frame();
    shadow_options();
    test_sun();
    test_shadow_switch();
    if (diag_on) {                                     // the road reflection target's size in the RT table (view 4's viewport)
        static uint32_t last_w, last_h;
        uint32_t w = M32(0x0093DAD4u + 0x14), h = M32(0x0093DAD4u + 0x18);
        if (w != last_w || h != last_h) diag_event("x360: road reflection RT entry %ux%u, view 4 viewport entry %08X", w, h, M32(0x009197A0u + 0x60));
        last_w = w; last_h = h;
    }
    static uint32_t n;
    if (diag_on && !(n++ % 300)) {                     // shadow strength inputs (FUN_006c83d0)
        uint32_t tod = M32(TIME_OF_DAY_PTR), v = M32(0x009196B8);   // eViews[1] +0x68: the Rain object
        diag_event("x360: shadow strength 8FAE64 %.3f 8FAE68 %.3f, sun z %.3f, weather (Rain %08X +0x290) %.3f, rain +0x28C %.3f, "
                   "wetness +0x36A0 %.3f", F32(0x008FAE64), F32(0x008FAE68), tod ? F32(tod + 0x188) : -1.0f, v, v ? F32(v + 0x290) : -1.0f,
                   v ? F32(v + 0x28C) : -1.0f, v ? F32(v + 0x36A0) : -1.0f);
    }
    if (fit) {
        put_float(SHADOW_FOCUS_FAR, M32(GAMEFLOW_STATE) == 3 ? X360E_SHADOW_FOCUS_FE : X360E_SHADOW_FOCUS_RACE);
        put_float(SHADOW_FOCUS_ANGLE, 1.0f);
        put_float(SHADOW_WIDTH, 1.0f);
    }
}

void x360_shadow_fit(const float player_view[16], float focus_x, float focus_y, const float light_dir[3],
                     float focus_near, float focus_far, float proj_out[16]);
int render_dump_active(void);
// The receivers' matrix is not view 15's projection: FUN_006e44c0 keeps its fit M1 (playerView x LiSPSM) in a stack
// local, copies it into view 15's projection for the casters (006E4FEF), and after the casters multiplies that same
// stack M1 by the bias (006E5428) into view 15's projection, which every receiver's ShadowTransform reads (World x
// 00985990+0x40: FUN_006c8000, and FUN_006d6aa0 for the car shadow effect). So the X360 projection written at
// 006E50C3 must replace M1 at 006E5428 too, else receivers look the casters' X360 map up through the PC fit.
static float x360_vp[16];
void x360_shadow_camera(Cpu *c, uint32_t view, uint32_t proj) {   // from d3dx.c's D3DXMatrixMultiply
    if (M32(c->esp) == 0x006E542Du) {                              // shadow view-projection x texel-centre bias
        float centre = 0.5f + 0.5f / (float)shadow_size;
        if (M32(proj + 48) == 0x3F002000u && M32(proj + 52) == 0x3F002000u) { put_float(proj + 48, centre); put_float(proj + 52, centre); }
        if (x360_vp_set) { memcpy(G + view, x360_vp, 64); x360_vp_set = 0; }   // M1 := the casters' X360 projection
        if (diag_on && render_dump_active()) {   // M1 x bias -> view 15 proj; the casters drew with view 15's viewProj (00985990+0x80)
            const uint32_t ms[5] = {view, proj, 0x00985990u, 0x00985990u + 0x40, 0x00985990u + 0x80};
            const char *nm[5] = {"M1", "bias", "v15 view", "v15 proj", "v15 viewproj"};
            for (int k = 0; k < 5; ++k) {
                const float *m = (const float *)(G + ms[k]);
                for (int r = 0; r < 4; ++r) diag_event("x360: shadow matrix %s row %d: %.9g %.9g %.9g %.9g", nm[k], r, m[4 * r], m[4 * r + 1], m[4 * r + 2], m[4 * r + 3]);
            }
            const float *pv = (const float *)(G + M32(shadow_source_view) + 0x80);
            for (int r = 0; r < 4; ++r) diag_event("x360: shadow matrix player viewproj row %d: %.9g %.9g %.9g %.9g", r, pv[4 * r], pv[4 * r + 1], pv[4 * r + 2], pv[4 * r + 3]);
        }
        return;
    }
    if (M32(c->esp) != 0x006E50C8u) return;                        // FUN_006e44c0's view-15 multiply only
    uint32_t tod = M32(TIME_OF_DAY_PTR);
    if (!tod) return;
    float pv[16], pp[16], light[3], out[16], far;
    test_sun();
    memcpy(light, G + tod + 0x180, 12);
    static float logged_light[3];
    if (diag_on && fabsf(light[0] - logged_light[0]) + fabsf(light[1] - logged_light[1]) + fabsf(light[2] - logged_light[2]) > 0.01f) {
        float len = sqrtf(light[0] * light[0] + light[1] * light[1] + light[2] * light[2]), z = len > 0 ? light[2] / len : 0;
        diag_event("x360: sun direction %.3f %.3f %.3f, elevation %.1f deg, time of day %.4f%s", light[0], light[1], light[2],
                   asinf(z) * 57.29578f, F32(tod + 8),
                   z < 0.7f ? " (X360 fit raises it to z 0.7, 44.4 deg)" : "");
        memcpy(logged_light, light, 12);
    }
    if (fit != 2) return;
    uint32_t source_plat = M32(shadow_source_view);
    memcpy(pv, G + source_plat, 64); memcpy(pp, G + source_plat + 0x40, 64);
    memcpy(&far, G + SHADOW_FOCUS_FAR, 4);
    x360_shadow_fit(pv, pp[0], -pp[5], light, X360E_SHADOW_NEAR, far, out);
    static const float id[16] = {1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1};
    memcpy(G + view, id, 64);
    memcpy(G + proj, out, 64);
    memcpy(x360_vp, out, 64); x360_vp_set = 1;
    static int logged;
    if (diag_on && !logged++) diag_event("x360: shadow light camera: light dir %.3f %.3f %.3f, focus far %.1f, proj m00 %.4f m11 %.4f",
                                          light[0], light[1], light[2], far, out[0], out[5]);
}

#define SPLINE_TEXTURE 0x00982900u
static int tone_x360 = -1;
int x360_tone_curve(void) {
    if (tone_x360 < 0) { const char *e = getenv("NFS_TONE_CURVE"); tone_x360 = e && !strcmp(e, "x360"); }
    return tone_x360;
}
void x360_set_tone_curve(int x360) {
    tone_x360 = x360;
    if (diag_on) diag_event("tone: %s; next composite / PC SPLINE rebuild", x360 ? "x360" : "pc");
}
void f_006C3B20_orig(Cpu *c);
void f_006C3B20(Cpu *c) {                                  // build the SPLINE LUT (fastcall, VT* in ecx)
    f_006C3B20_orig(c);
    Obj *t = x360_tone_curve() ? obj(M32(SPLINE_TEXTURE)) : NULL;
    if (!t || t->fmt != 21 || !t->mem[0]) return;          // A8R8G8B8: bytes B G R A
    static int logged;
    if (diag_on && !logged++) {
        uint32_t m = t->mem[0];
        diag_event("x360: tone curve LUT %ux%u, R/G at 0,64,128,192,255 before: %u/%u %u/%u %u/%u %u/%u %u/%u", t->w, t->h,
                   M8(m + 2), M8(m + 1), M8(m + 258), M8(m + 257), M8(m + 514), M8(m + 513), M8(m + 770), M8(m + 769), M8(m + 1022), M8(m + 1021));
    }
    for (uint32_t x = 0; x < t->w; ++x) M8(t->mem[0] + 4 * x + 1) = M8(t->mem[0] + 4 * x + 2);
    t->dirty = 1;
}

// Road reflection (NFS_ROAD_REFLECTION=x360|pc, default x360). Wet roads
// (worldreflect) look up a planar reflection, view 4 (eViews[4] 009197A0) drawn by the main render loop into its own
// target, with texldp at the main view's screen position (VS oT1 = (clip.xy * (1, -1) + w) / 2, w): the lookup is
// resolution-independent. PC: FUN_006bcdc0 creates the target (DAT_00982a6c, its surface 00982A68), its depth surface
// (00982A78, no MSAA) and, at g_RoadReflectionEnable 3, the blur texture (00982A74) at a fixed 320x240, and
// FUN_006bd0d0 and MaybeChangeViewMode (006CFAD0, every frame) give render-target table entry 1 (0093DAD4: view 4,
// +0x14 width, +0x18 height) the same 320x240;
// FUN_006bd3b0 sets the viewport (0, 0, width, height) from that entry and FUN_006cf400 builds view 4's projection from
// its height/width. X360 (eInitSurfaces 8243C060) creates the target at half the back buffer, no MSAA. With x360
// all three textures and the depth surface become half the back buffer (__window_width/height >> 1), and table entry
// 1 follows, so the viewport covers the whole target; widescreen.c gives view 4 the main view's (Hor+) projection,
// so the lookup lines up whatever the target's size. The level 3 blur (FUN_006d4960) steps in texels of the size it
// reads from the texture.
static int refl_x360 = -1;
static int pending_refl = -1;
void x360_set_reflection(int x360) { pending_refl = x360; graphics_reset_request(); }
static uint32_t refl_w, refl_h;          // the size given to the reflection textures (0: not resized)
static int creating_reflection;
static void refl_options(void) {
    if (refl_x360 >= 0) return;
    const char *e = getenv("NFS_ROAD_REFLECTION");
    refl_x360 = !(e && !strcmp(e, "pc"));
}
void x360_reflection_dimensions(uint32_t view_w, uint32_t view_h, uint32_t *w, uint32_t *h) {
    refl_options();
    *w = refl_x360 ? (view_w > 1 ? view_w / 2 : 1) : 320;
    *h = refl_x360 ? (view_h > 1 ? view_h / 2 : 1) : 240;
}
void f_006BCDC0_orig(Cpu *c);
void f_006BCDC0(Cpu *c) {                // creates the road reflection target, depth surface and blur texture (320x240)
    if (pending_refl >= 0) { refl_x360 = pending_refl; pending_refl = -1; }
    refl_options();
    refl_w = refl_h = 0;
    creating_reflection = 1;
    // Water needs a real target even when the road option allocates only 1x1.
    // This scope covers allocation only; road wetness/detail remain unchanged.
    uint32_t road_level = M32(0x009017D4u);
    int water_target = mat_water_enabled() && !road_level;
    if (water_target) M32(0x009017D4u) = 2;
    f_006BCDC0_orig(c);
    // A failed allocation sets both level and maximum to zero; keep that failure.
    if (water_target && M32(0x009017D4u)) M32(0x009017D4u) = road_level;
    creating_reflection = 0;
}

static int water_reflection_active(void) {
    if (!mat_water_reflection_requested() && !split_capture_reflection_pending()) return 0;
    Obj *t = obj(M32(0x00982A6Cu));
    return t && t->w > 1 && t->h > 1 && M32(0x00982A78u);
}
// Called only at the six exact inline gate operands in build_all.py.
// Returning gate-local values avoids touching Rain+0x36A0 or road material setup.
uint32_t x360_water_reflection_level(void) {
    uint32_t level = M32(0x009017D4u);
    return level < 2 && water_reflection_active() ? 2 : level;
}
float x360_water_reflection_wetness(uint32_t site, float wetness) {
    if (!water_reflection_active()) return wetness;
    if (site == 0x006BFEE6u && (wetness <= .01f || M32(0x009017D4u) < 2))
        mat_water_reflection_forced();
    return fmaxf(wetness, .02f);
}
void x360_reflection_size(uint32_t *w, uint32_t *h) {   // from d3d9.m's CreateTexture and CreateDepthStencilSurface
    if (!creating_reflection || !refl_x360 || *w != 320 || *h != 240) return;
    uint32_t bw = M32(0x00982BE4u) / 2, bh = M32(0x00982BE8u) / 2;   // __window_width/height: the back buffer
    if (!bw || !bh) return;
    *w = refl_w = bw; *h = refl_h = bh;
}
#define RT_REFLECTION 0x0093DAD4u        // render-target table entry 1 (view 4): +0x14 width, +0x18 height
static void refl_entry(void) {
    if (refl_w && M32(RT_REFLECTION + 0x14) == 320 && M32(RT_REFLECTION + 0x18) == 240) {
        M32(RT_REFLECTION + 0x14) = refl_w;
        M32(RT_REFLECTION + 0x18) = refl_h;
    }
}
void f_006BD0D0_orig(Cpu *c);
void f_006BD0D0(Cpu *c) {                // fills the render-target table (after the targets are created)
    split_views_reset();
    f_006BD0D0_orig(c);
    refl_entry();
    if (diag_on) diag_event("x360: road reflection %s: target %ux%u (g_RoadReflectionEnable %u, max %u)", refl_x360 ? "x360" : "pc",
                            M32(RT_REFLECTION + 0x14), M32(RT_REFLECTION + 0x18), M32(0x009017D4u), M32(0x0090185Cu));
}
void f_006CFAD0_orig(Cpu *c);
void f_006308E0_orig(Cpu *c);
void f_006308E0(Cpu *c) {                // ERestartRace finalizer: retail resets its singleton treatment here
    f_006308E0_orig(c);
    if (!split_view_rect(0x00919650u, NULL) || !split_view_rect(0x009196C0u, NULL)) return;
    // Restart reuses the players, cameras and targets, so identity-based invalidation cannot see it.
    // Run after the retail reset so the next per-player treatment clones the reset singleton.
    Cpu saved = *c;
    split_capture_reset(c);
    split_post_restart();
    *c = saved;
    if (diag_on) diag_event("split lifecycle: restart invalidated both capture and post banks (006308E0)");
}
void f_006CFAD0(Cpu *c) {                // MaybeChangeViewMode: sets view 4's entry to 320x240 again in view modes 1 and 2
    split_views_restore(); // retail layout must see its original descriptors
    f_006CFAD0_orig(c);
    refl_entry();
    split_views_update(); // dimensions installed before eDisplayFrame's matrix loop
    split_capture_mode(c, split_view_rect(0x00919650u, NULL) && split_view_rect(0x009196C0u, NULL));
}

// The roadrefl probe (probe.m): `guest` is the road reflection target at the X360 size.
int x360_reflection_target(uint32_t guest, uint32_t w, uint32_t h) {
    uint32_t source = split_capture_source(), r[4];
    if (source && split_view_rect(source, r))
        return refl_x360 && (guest == M32(0x00982A6Cu) || guest == M32(0x00982A74u)) && w == r[2]/2 && h == r[3]/2;
    return refl_w && guest == M32(0x00982A6Cu) && w == refl_w && h == refl_h;
}
