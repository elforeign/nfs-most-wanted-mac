// World lighting: Neutral / Original (NFS_WORLD_LIGHTING, default Neutral; F10, live).
                                                                                
// not the ambient+sun sum. At low sun the ground remains mostly skylight blue.
//
// World effects read DiffuseColour=min(1,k*TOD+0x20) via 006C6300 and
// AmbientColour=min(1,TOD+0x30), W=TOD+0x3C via 006C6450. Flat grey ground:
// A = ambient*W*PRELIGHT; lit = A + direct*N.L, N.L=normalized towards-sun Z.
// Neutral desaturates ambient 2/3 toward Y (.2126/.7152/.0722), preserving Y.
// Across the active PC/X360 quartet and blends this keeps shade below 10000 K,
// within Duv [0,.006] (no below-locus magenta), and bluer than direct sunlight.
//
// Direct source CCT is linearly interpolated in elevation degrees through
// (5,3500),(15,4500),(30,5300),(62,5800), constant outside. These are chosen
// artistic endpoints/interpolation, NOT a measured atmosphere/weather model.
// Lower elevation increases air mass: https://pvpmc.sandia.gov/modeling-guide/1-weather-design-inputs/irradiance-insolation/air-mass/
// Planckian xy: Kang et al. 2002, JKPS 41(6), 865-871, as documented at
// https://colour.readthedocs.io/en/develop/_modules/colour/temperature/kang2002.html
// Partial adaptation: XYZ'=0.85*sourceXYZ+0.15*D65XYZ, both with Y=1. For the
// source white this equals 15% Bradford cone adaptation toward D65; artistic D,
// not a measured observer. Adapted direct is ~3796 K at 5 deg, ~5899 K at 62.
// Convert to linear Rec.709, scale to the original direct Y, then reduce ONE
// scalar if any channel would exceed 1. No per-channel clipping/hue rotation;
// infeasible brightness becomes a documented shortfall, never dimmer shade.
// No low-sun fallback to the old grey solve. TOD RGB is restored after
// each original setter. Original and the front-end garage retain authored light.
// Baked prelight/lamp data, cars, sky and Tunnel shadows are untouched. Offline
// proof and max luminance shortfall: recomp/lighting_options_test.py,
                                                                                
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

#include "host.h"
#include "settings.h"

#define TIME_OF_DAY_PTR 0x009B392Cu   // +0x20 sun colour, +0x30 ambient colour (+0x3C its scale), +0x180 towards the sun
#define SUN_SCALE       0x009018C4u   // k in FUN_006C6300
#define GAMEFLOW_STATE  0x00925E90u   // 3 front end, 6 in game

static inline float rf(uint32_t a) { float f; memcpy(&f, G + a, 4); return f; }
static inline void wf(uint32_t a, float f) { memcpy(G + a, &f, 4); }

static int mode = -1;   // 0 original, 1 neutral
static int wl_mode(void) {
    if (mode < 0) {
        const char *e = getenv("NFS_WORLD_LIGHTING");
        mode = !(e && (!strcasecmp(e, "original") || !strcasecmp(e, "pc") || !strcmp(e, "0")));
    }
    int postfx_look_variant(const char *v);   // x360_post.m: BOOT_DIAG_LOOK_AB held variants
    if (postfx_look_variant("wlorig")) return 0;
    if (postfx_look_variant("wlneutral")) return 1;
    return mode;
}

static const char *const kTunnel[] = {"Original", "Keep"}, *const kTunnelV[] = {"pc", "keep"};
static Setting s_tunnel = {.id = "graphics.tunnel_shadows", .category = SET_WORLD, .order = 30, .label = "Tunnel shadows",
    .help = "Original fades sun shadows out inside tunnels, while Keep retains the mapped shadows there (roof coverage still matters).",
    .type = SET_CHOICE, .choices = kTunnel, .values = kTunnelV, .nchoices = 2,
    .def = 1, .env = "NFS_TUNNEL_SHADOWS"};
__attribute__((constructor)) static void lighting_register(void) {
    settings_add(&s_tunnel);
}

// Both PC sites call ID3DXEffect::SetFloat on InvShadowStrength:
// FUN_006D7660 (decomp speed_006d6820.c:753) and FUN_006E0200 (:8157).
// d3dx_effect.c intercepts that named parameter, so both uploads use 1-strength
// under Keep, omitting tunnelBlend (00982BA8). No caster/fit changes.
float lighting_inv_shadow_strength(float original) {
    return s_tunnel.value == 1 ? 1.0f - rf(0x008FAE68u) : original;
}
void lighting_set_world(int neutral) {   // the F10 menu (live: read at every setter call)
    mode = neutral != 0;
    if (diag_on) diag_event("lighting: world lighting now %s", mode ? "neutral" : "original");
}

#define PRELIGHT 0.27f   // mean world vertex colour (15.1.1 draw dump: WORLD 68.7, WORLDNOFOG 60.0, NORMALMAP 62.7 of 255; grey)

static float luminance(const float rgb[3]) {
    return 0.2126f * rgb[0] + 0.7152f * rgb[1] + 0.0722f * rgb[2];
}
static void ambient_rgb(uint32_t tod, float out[3]) {
    static int reported;
    float before[3];
    int in_game = M32(GAMEFLOW_STATE) == 6;
    int active = in_game && wl_mode();
    for (int i = 0; i < 3; ++i) before[i] = out[i] = fminf(1.0f, rf(tod + 0x30 + 4 * i));
    if (active) {
        float y = luminance(before), t = 2.0f / 3.0f;
        for (int i = 0; i < 3; ++i) out[i] += t * (y - out[i]);
    }
    // Once at the first in-game TOD after a load; FE setters rearm the line.
    if (!in_game) reported = 0;
    if (diag_on && in_game && !reported) {
        diag_event("lighting_options: tunnel_shadows=%s shade_desat=2/3 neutral=%d tod=%08X time=%.3f ambient %.6f %.6f %.6f -> %.6f %.6f %.6f",
                   s_tunnel.value ? "keep" : "pc", active, tod, rf(tod + 8),
                   before[0], before[1], before[2], out[0], out[1], out[2]);
        reported = 1;
    }
}

// Linear Rec.709 source white at Y=1, with the stated partial adaptation.
static void sun_white(float elevation, float out[3]) {
    float t;
    if (elevation <= 5) t = 3500;
    else if (elevation < 15) t = 3500 + (elevation - 5) * 100;
    else if (elevation < 30) t = 4500 + (elevation - 15) * (800.0f / 15);
    else if (elevation < 62) t = 5300 + (elevation - 30) * (500.0f / 32);
    else t = 5800;
    float inv = 1 / t, inv2 = inv * inv, inv3 = inv2 * inv;
    float x = t <= 4000 ? -0.2661239e9f * inv3 - 0.2343589e6f * inv2 + 0.8776956e3f * inv + 0.179910f
                         : -3.0258469e9f * inv3 + 2.1070379e6f * inv2 + 0.2226347e3f * inv + 0.240390f;
    float y = t <= 4000 ? -0.9549476f*x*x*x - 1.37418593f*x*x + 2.09137015f*x - 0.16748867f
                         : 3.0817580f*x*x*x - 5.87338670f*x*x + 3.75112997f*x - 0.37001483f;
    float X = 0.85f * x/y + 0.15f * 0.95047f;
    float Z = 0.85f * (1-x-y)/y + 0.15f * 1.08883f;
    out[0] = 3.2404542f*X - 1.5371385f - 0.4985314f*Z;
    out[1] = -0.9692660f*X + 1.8760108f + 0.0415560f*Z;
    out[2] = 0.0556434f*X - 0.2040259f + 1.0572252f*Z;
    float scale = 1 / luminance(out);
    for (int i = 0; i < 3; ++i) out[i] *= scale;
}

// TOD sun colour BEFORE k. Ambient desaturation preserves Y, so preserving lit
// Y is exactly preserving original direct Y, unless its new colour reaches 1.
static int neutral_sun(uint32_t tod, float out[3]) {
    float k = rf(SUN_SCALE);
    if (k <= 0) return 0;
    float sx = rf(tod + 0x180), sy = rf(tod + 0x184), sz = rf(tod + 0x188);
    float len = sqrtf(sx*sx + sy*sy + sz*sz);
    float nl = len > 0 ? fmaxf(-1, fminf(1, sz/len)) : 0;
    float original[3];
    for (int i = 0; i < 3; ++i) original[i] = fminf(1, k * rf(tod + 0x20 + 4*i));
    sun_white(asinf(nl) * 57.29577951308232f, out);
    float peak = fmaxf(out[0], fmaxf(out[1], out[2]));
    float scale = fminf(luminance(original), 1 / peak);
    for (int i = 0; i < 3; ++i) out[i] *= scale / k;
    return 1;
}

static void wl_log(uint32_t tod, const float *ns) {
    static uint64_t last;
    uint64_t now = host_ticks();
    if (!diag_on || (last && now - last < 5000000000ull)) return;
    last = now;
    diag_event("lighting: world %s: tod %.3f sun %.3f %.3f %.3f x k %.3f, ambient %.3f %.3f %.3f x %.2f, towards sun z %.3f, neutral sun %.3f %.3f %.3f",
               ns ? "neutral" : "original", rf(tod + 8), rf(tod + 0x20), rf(tod + 0x24), rf(tod + 0x28), rf(SUN_SCALE),
               rf(tod + 0x30), rf(tod + 0x34), rf(tod + 0x38), rf(tod + 0x3C), rf(tod + 0x188),
               ns ? ns[0] : 0, ns ? ns[1] : 0, ns ? ns[2] : 0);
}

// FUN_006C6300 (effect wrapper *), cdecl: the game's DiffuseColour setter, run on the neutral sun colour
void f_006C6300_orig(Cpu *c);
void f_006C6300(Cpu *c) {
    uint32_t tod = M32(TIME_OF_DAY_PTR);
    float ns[3], keep[3];
    if (!tod) { f_006C6300_orig(c); return; }
    if (!wl_mode() || M32(GAMEFLOW_STATE) != 6 || !neutral_sun(tod, ns)) { wl_log(tod, NULL); f_006C6300_orig(c); return; }
    wl_log(tod, ns);
    for (int i = 0; i < 3; ++i) { keep[i] = rf(tod + 0x20 + 4 * i); wf(tod + 0x20 + 4 * i, ns[i]); }
    f_006C6300_orig(c);
    for (int i = 0; i < 3; ++i) wf(tod + 0x20 + 4 * i, keep[i]);
}

// Desaturate ambient at its own luminance as part of Neutral. Restore the
// live TOD immediately: authored records, baked prelight and lamp data stay intact.
void f_006C6450_orig(Cpu *c);
void f_006C6450(Cpu *c) {
    uint32_t tod = M32(TIME_OF_DAY_PTR);
    if (!tod) { f_006C6450_orig(c); return; }
    float amb[3], keep[3];
    ambient_rgb(tod, amb);
    if (!wl_mode() || M32(GAMEFLOW_STATE) != 6) { f_006C6450_orig(c); return; }
    for (int i = 0; i < 3; ++i) { keep[i] = rf(tod + 0x30 + 4 * i); wf(tod + 0x30 + 4 * i, amb[i]); }
    f_006C6450_orig(c);
    for (int i = 0; i < 3; ++i) wf(tod + 0x30 + 4 * i, keep[i]);
}
