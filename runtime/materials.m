// materials.m - material-shader replacement layer: hand-written (or X360-derived) MSL in place of a game shader.
//
// How it works (ARCHITECTURE.md "Material shaders"):
// - recognise: render.m translates every game shader (sm_translate) and, before compiling, asks mat_replace() with
//   the bytecode. A table entry matches on the FNV-1a 64 hash of the bytecode (the same hash render.m keys its cache
//   with) and on the sampler types the translation reflects; its switch must be on.
// - substitute: the entry's MSL is compiled instead of the translation. It keeps the translator's binding contract
//   (constants buffer(0..3), texture(n)/sampler(n) = s#, canonical varyings), so render.m binds the draw from the
//   translation's reflection as usual. A replacement that fails to compile falls back to the translation.
// - extra constants: values the game never uploads go in fragment buffer(MAT_PS_BUF) (MatConsts, 4 float4), filled
//   per draw by the entry's constants() from host state: hooks on the game's setters (below) and the device's
//   vertex shader constants.
// A new payload adds a table entry, its MSL and, if it needs values the game doesn't upload, a hook.
// BOOT_DIAG logs each replacement compiled; BOOT_DIAG_MSL saves its source like any shader (msl/<key>.metal).
//
// Payload 1, car paint (NFS_CAR_PAINT=x360|pc, default x360): the PC car pixel shaders t1..t5 (shadow levels 1-5,
// render_diff/hdr_dof/pc_effects/car) with the X360 `car` PS 09bb919c specular (x360/shaders/hlsl/00_car):
// pow(R.V, SpecularPower) + K1 * SpecularHotSpot * lit * pow(R.V, K2 * SpecularPower), times the sun colour (K1, K2: the PS's
// literals, X360S_CAR_HOT_SCALE / _POWER, generated)
// (LocalColourMatrix[0..2].x), the diffuse alpha, the specular Fresnel colour and the lit fraction, instead of the
// PC's fixed 2 * pow(R.V, 16). The PC's shadow lookups, environment term (x 0.5, no X360 (1 + 2a) boost), alpha
// (DiffuseRange.w x 0.25, from the PC vertex shader) and vertex shader are kept; the metallic flake is R30 (below). t0 (shadows off, ps_1_1 with its own VS) stays PC.
//
// Payload 2, rain ripples on wet roads (NFS_RAIN_RIPPLES=x360|pc, default x360): the PC worldreflect shaders patched
// with the X360 `wetroad` PS 09c17c18 rain term (x360/shaders/hlsl/17_road): two scales of an animated RAINSPLASH
// frame perturb the projective reflection lookup and add glints; see "payload 2" below. A payload either replaces
// the shader source (msl) or edits the translation (patch), which keeps everything else the PC shader does exactly.
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "x360_internal.h"
#include "roots.h"
#include "materials.h"
#include "trees.h"

#define TIME_OF_DAY_PTR   0x009B392Cu    // TimeOfDayPointer: +0x60 the specular/hotspot time-of-day gain
#define CUR_EFFECT        0x00982C80u    // the effect being set up: +4 its type (4 = car)

// ---- eLightMaterial values the PC drops ----
// FUN_006c76e0 (eLightMaterial setter, decomp speed_006c5350.c:1906) stops at +0x9C; the X360 setter 824531C0 also
// reads +0xA0 MetallicScale and +0xA4 SpecularHotSpot (x TimeOfDay+0x60), and gives the PS the raw SpecularPower +0x58.
MatCarPaint mat_car;

// ---- car glass (NFS_CAR_GLASS=pc|x360, default pc; live) ----
// Both car VSs give alpha = DiffuseMin.w + N.V x DiffuseRange.w (N.V >= 0.01) and both PSs output tex.a x that alpha and multiply
// the specular and the environment by it again; the environment Fresnel is EnvmapMin.x + pow(N.V, EnvmapPower) x
// EnvmapRange.x. The PC differs in three places [V]: its setter FUN_006c76e0 scales DiffuseRange.w by 0.25 (the X360
// setter 824531C0 doesn't), its loader runs eLightMaterialPlatInterface::UpdatePlatInfo (006BE1B0), which overwrites
// DiffuseMinA/MaxA (+0x50/+0x54) of 23 materials by name hash (speed.exe .data 008F91E4..008F9208: 0.3/1.0 WINDSHIELD,
// WINDOWMASK, MOLDINGS, the L1 tints BLACK/GREEN; 0.4/1.0 four L2 tints; 0.4/0.8 HEADLIGHTGLASS, CLEARPLASTIC; 0.5/1.0
// twelve L3 tints; the X360 loader (default_82218f00.c:1942) has no such step), and its GLOBALB data has weaker glass
// reflections. x360 gives the PC setter the X360 values for these materials: alpha and the environment Fresnel scales
// (+0x80 EnvmapMinScale, +0x90 EnvmapMaxScale) from the X360's GLOBAL/GLOBALB.BUN light materials (chunk 0x135200,
// the same records as the PC's), DiffuseMaxA pre-scaled so the setter's x 0.25 hands the VS the X360's range; the
// record is put back after the call. The X360 PS's (1 + 2 a) environment boost is NFS_CAR_GLASS_BOOST (below).
// Rows (generated from the player's X360 GLOBALB.BUN, tools_snapshot/pubtables_gen.py): name hash, DiffuseMinA (+0x50),
// DiffuseMaxA (+0x54), EnvmapMinScale (+0x80), EnvmapMaxScale (+0x90), rounded to 6 digits (X360 data; the PC data differs in alpha only
// for WINDOWMASK, 0.4/0.4, and in the environment scales of WINDOWMASK, WINDSHIELD and every tint).
typedef struct GlassX360 { uint32_t hash; float min_a, max_a, env_min, env_max; } GlassX360;
#include "gen/glass_x360.inc"       // tools/gen_tables.py, from the player's X360 GLOBALB.BUN (no X360 copy: none)
#include "gen/car_shadow_off.inc"   // and the PC speed.exe (CAR_SHADOW_OFF16, the car PS shadow taps, below)
#include "gen/pc_shader_lits.inc"   // and the PC particles PS's shadow floor (PCS_PARTICLE_*, particles_msl)
#include "gen/x360_engine.inc"      // and the X360 engine constants from default.xex (X360E_*: brightness, shadow bias, ripple rate)
#include "gen/x360_shader_lits.inc" // and the X360 NFS/ZZDATA0.BIN shaders' literal constants (X360L_<shader>_C<reg>,
                                     // X360S_*; tools_snapshot/x360_shader_lits.py): spliced into the MSL below, so the
                                     // source holds no game numbers. Without them (X360_SHADER_LITS 0) the features stay PC.
#include "gen/x360_shader_body.inc" // and the bodies of the X360 shader programs below (X360B_<array>: grass, water, scenery
                                     // lights, building glass, trees), translated from the same file at build time
                                     // (tools_snapshot/x360_shader_body.py); this file keeps only the port's own lines.
#if X360_SHADER_BODIES != X360_SHADER_LITS
#error "gen/x360_shader_body.inc and gen/x360_shader_lits.inc come from different inputs: rerun tools/gen_tables.py"
#endif
static int car_glass = -1;
static int car_glass_on(void) {
    if (car_glass < 0) { const char *e = getenv("NFS_CAR_GLASS"); car_glass = !(e && !strcmp(e, "pc")); }
    return car_glass;
}
void materials_set_car_glass(int x360) {   // the F10 menu: live (read at every setter call)
    car_glass = x360 != 0;
    if (diag_on) diag_event("materials: car glass now %s", car_glass ? "x360" : "pc");
}
static const GlassX360 *glass_find(uint32_t hash) {
    for (size_t i = 0; i < GLASS_X360_COUNT; ++i) if (glass_x360[i].hash == hash) return &glass_x360[i];
    return NULL;
}

// NFS_CAR_GLASS_BOOST=on|off (default on; only with NFS_CAR_GLASS=x360, restart): the X360 car PS multiplies the
// environment by (1 + 2a), a = the pixel's alpha (render_diff car_glass_rvm.md (e)), on glass; the replaced PC PS gets
// it for the draws the glass hook above matched (k[2].x), paint and the PC look stay as they were.
static int glass_boost;                   // set by the setter hook for this draw
// BOOT_DIAG_GLASS_AB=1|2 (with BOOT_DIAG_SIMSHOTS, NFS_CAR_GLASS=x360): the car paint A/B's held pass (below) draws the
// same frame with PC glass (1) or X360 glass without the env boost (2) instead of the PC specular: simshot_<t>_pcpaint.png.
static int glass_ab = -1, ab_state;
// BOOT_DIAG_AUX_AB=1 (with BOOT_DIAG_SIMSHOTS, NFS_AUX_LIGHTS=x360): the same held pass draws without the R20 scenery
// light pass (car paint unchanged): simshot_<t>_pcaux.png, a same-frame PC / X360 scenery-light pair.
static int aux_ab = -1;
static int aux_ab_on(void) {
    if (aux_ab < 0) aux_ab = diag_on && getenv("BOOT_DIAG_AUX_AB") != NULL;
    return aux_ab;
}
int mat_aux_ab_held(void) { return aux_ab_on() && ab_state == 2; }
static int glass_ab_held(void) {
    if (glass_ab < 0) glass_ab = diag_on && getenv("BOOT_DIAG_GLASS_AB") ? atoi(getenv("BOOT_DIAG_GLASS_AB")) : 0;
    return glass_ab > 0 && ab_state == 2 ? glass_ab : 0;
}
static int glass_boost_on(void) {
    static int v = -1;
    if (v < 0) { const char *e = getenv("NFS_CAR_GLASS_BOOST"); v = !(e && !strcmp(e, "off")); }
    return v;
}
// ---- R26: X360 car_normalmap on tyres and badges (NFS_CAR_NORMALMAPS=pc|x360, default pc; live) ----
// The PC car effect has no normal sampler, but 006c6d10 (called just before the setter above, same flags) still caches the
// material's TextureInfos in 00982CB4 (diffuse) / 00982CB8 (normal), and BOOT_DIAG shows the normal slot = the diffuse
// (none) on tyres and badges: their `_N` maps are in the car's pack but no material names them. They are keyed by the diffuse
// name: <CAR>_TIRE / <CAR>_BADGING -> + "_N" through GetTextureInfo (00503400, bStringHash), the X360-only TREAD / TREADR /
// DOORLINE / DAMAGE0 by hash to the X360 `_N` in GLOBALB / INGAMEA (loaded at start-up), any other draw whose slot holds a
// different non-default texture (MISC_N) by that slot. UNIVERSAL_TIRE_N has no PC diffuse to key it by (not used).
// The draw's guest D3D texture is returned by mat_carnm_texture for render.m to bind at fragment texture(15).
// Green sign (decoded 2026-10-25): the X360 VS 09bbea64 unpacks TANGENT0 to (T, w), builds B = w * cross(N, T) and gives the PS the
// light and eye in (T, B, N); the PS reads n = 2 rgb - 1 with no flip. The sign w lives in the mesh data, but the map pins the axis:
// x360_tex.py on 997S_TIRE / 997S_TIRE_N (X360 TEXTURES.BIN): R correlates +0.205 with the diffuse's d/du and G +0.191 with its d/dv
// (same sign, same size), so G is a gradient along +v like R along +u: B points to +v (image down, D3D). The derivative frame
// (T = d/du, B = d/dv, 1/det applied, so mirrored uv keep the same meaning) is that frame, G is not flipped (k[7].y = +1).
static int carnm = -1;
static int carnm_on(void) {
    if (carnm < 0) { const char *e = getenv("NFS_CAR_NORMALMAPS"); carnm = !(e && !strcmp(e, "pc")) && X360_SHADER_LITS; }
    return carnm;
}
void materials_set_car_normals(int x360) { carnm = x360 != 0; if (diag_on) diag_event("materials: car normal maps live now %s", carnm ? "x360" : "pc"); }
static char carnm_name[32];
static int carnm_kind;                    // 1: map from the material's own slot, 2: <CAR>_TIRE_N, 3: <CAR>_BADGING_N, 0: X360-only TREAD...
static uint32_t carnm_tex;                // this draw's normal map (guest D3D texture), 0 = none
uint32_t mat_carnm_texture(void) { return carnm_tex; }
static const struct { const char *diffuse, *file; uint32_t d3d; } carnm_x360[] = {
    {"TREAD", "GLOBAL/GLOBALB.BUN", 0}, {"TREADR", "GLOBAL/GLOBALB.BUN", 0},
    {"DOORLINE", "GLOBAL/GLOBALB.BUN", 0}, {"DAMAGE0", "GLOBAL/INGAMEA.BUN", 0},
};
static uint32_t carnm_x360_d3d[4];
static uint32_t bhash(const char *s) { uint32_t h = 0xFFFFFFFFu; while (*s) h = h * 33 + (uint8_t)*s++; return h; }   // bStringHash
uint32_t x360_texture_load(Cpu *c, const char *file, uint32_t hash, uint32_t as_hash);   // x360_textures.m
void materials_car_textures(Cpu *c) {      // x360_textures.m, at the first texture Init (with materials_textures)
    int n = 0;
    for (int i = 0; i < 4; ++i) {
        char nm[32]; snprintf(nm, sizeof nm, "%s_N", carnm_x360[i].diffuse);
        uint32_t h = bhash(nm), ti = x360_texture_load(c, carnm_x360[i].file, h, h);
        carnm_x360_d3d[i] = ti && M32(ti) ? M32(M32(ti) + 0x18) : 0;
        n += carnm_x360_d3d[i] != 0;
    }
    fprintf(stderr, "boot: %d X360 car normal maps (TREAD, TREADR, DOORLINE, DAMAGE0)\n", n);
}
static uint32_t ti_d3d(uint32_t ti) { return ti && M32(ti) ? M32(M32(ti) + 0x18) : 0; }
static void carnm_find(Cpu *c) {
    carnm_tex = 0;
    if (!carnm_on()) return;
    uint32_t dti = M32(0x00982CB4u), nti = M32(0x00982CB8u);
    if (!dti) return;
    const char *name = (const char *)G + dti + 0x0C;
    size_t len = strnlen(name, 31);
    uint32_t tex = 0;
    carnm_kind = 0;
    if (nti && nti != dti && strncmp((const char *)G + nti + 0x0C, "DEFAULTALPHA", 12)) { tex = ti_d3d(nti); carnm_kind = 1; }
    else if ((len > 5 && !strcmp(name + len - 5, "_TIRE")) || (len > 8 && !strcmp(name + len - 8, "_BADGING"))) {
        char nm[48]; snprintf(nm, sizeof nm, "%s_N", name);
        carnm_kind = name[len - 1] == 'E' ? 2 : 3;
        uint32_t a[3] = {bhash(nm), 1, 0}, esp = c->esp, ti = call_guest(c, 0x00503400u, 3, a);
        c->esp = esp;
        tex = ti_d3d(ti);
        if (diag_on) { static uint32_t seen[64]; static int ns; int i = 0; while (i < ns && seen[i] != a[0]) ++i;
            if (i == ns && ns < 64) { seen[ns++] = a[0]; diag_event("carnm: %s -> TextureInfo %08X d3d %08X", nm, ti, tex); } }
    } else
        for (int i = 0; i < 4; ++i) if (!strcmp(name, carnm_x360[i].diffuse)) tex = carnm_x360_d3d[i];
    carnm_tex = tex;
    snprintf(carnm_name, sizeof carnm_name, "%s", name);
}
// BOOT_DIAG only: car-model draws by blend state (tail of this section, mat_cardraw_note).
static uint32_t cd_flags, cd_mat;        // the last car setter call's render flags (bit 0x80000 = traffic body, CarRenderInfo::Render 0074E160) and material
void f_006C76E0_orig(Cpu *c);
uint32_t call_guest(Cpu *c, uint32_t fn, int n, const uint32_t *args);
void f_006C76E0(Cpu *c) {                // (eLightMaterial *m, uint flags), called just before each draw that uses it
    uint32_t m = arg(c, 0), fx = M32(CUR_EFFECT), tod = M32(TIME_OF_DAY_PTR);
    if (diag_on && m && fx && M32(fx + 4) == 4) { cd_flags = arg(c, 1); cd_mat = m; }
    const GlassX360 *gx = m && fx && M32(fx + 4) == 4 && car_glass_on() && glass_ab_held() != 1 ? glass_find(M32(m + 0xC)) : NULL;
    float keep[4] = {0};
    if (gx) {
        keep[0] = MF32(m + 0x50); keep[1] = MF32(m + 0x54); keep[2] = MF32(m + 0x80); keep[3] = MF32(m + 0x90);
        MF32(m + 0x50) = gx->min_a;
        MF32(m + 0x54) = gx->min_a + 4.0f * (gx->max_a - gx->min_a);   // the setter's x 0.25 leaves max_a - min_a
        MF32(m + 0x80) = gx->env_min;
        MF32(m + 0x90) = gx->env_max;
    }
    f_006C76E0_orig(c);
    if (gx) { MF32(m + 0x50) = keep[0]; MF32(m + 0x54) = keep[1]; MF32(m + 0x80) = keep[2]; MF32(m + 0x90) = keep[3]; }
    if (!m || !fx || M32(fx + 4) != 4) return;
    float g = tod ? F32(tod + 0x60) : 1.0f;
    mat_car.spec_power = F32(m + 0x58);
    mat_car.metallic = F32(m + 0xA0);
    mat_car.hotspot = F32(m + 0xA4) * g;
    glass_boost = gx && glass_boost_on() && !glass_ab_held();
    carnm_find(c);
    if (diag_on) {                       // each material once: what the PC data carries
        static uint32_t seen[512];
        static int nseen;
        int k = 0;
        while (k < nseen && seen[k] != m) ++k;
        if (k == nseen && nseen < 512) {
            seen[nseen++] = m;
            char name[29];
            memcpy(name, G + m + 0x14, 28); name[28] = 0;
            diag_event("materials: car material %08X '%s' hash %08X: SpecularPower %g EnvmapPower %g MetallicScale %g "
                       "SpecularHotSpot %g (tod gain %g; PC scales %g %g)", m, name, M32(m + 0xC), F32(m + 0x58),
                       F32(m + 0x7C), F32(m + 0xA0), F32(m + 0xA4), g, F32(0x009018E0u), F32(0x009018DCu));
            diag_event("materials: car material '%s' alpha min/max %g %g env min/max scale %g %g%s", name,
                       F32(m + 0x50), F32(m + 0x54), F32(m + 0x80), F32(m + 0x90), gx ? " (X360 glass row applied)" : "");
        }
    }
}

// BOOT_DIAG: once a second one `cardraw:` line per (render flags, material) that drew with blending or alpha test on,
// then a total: where a translucent car comes from (render.m calls this for every draw; only effect type 4 counts).
// flags 0x80000 is set by CarRenderInfo::Render (0074E160, `bVar34`) for traffic; the player's and cops' flags lack it.
typedef struct CarDrawStat { uint32_t flags, mat; uint32_t total, blend, atest; } CarDrawStat;
// BOOT_DIAG: a car draw whose pixel shader samples a texture that is missing (1 none bound, 2 no GPU copy, 3 wrong
// type: the zero texture, alpha 0, is bound) or has no level-0 memory (4: a blank texture). One line per distinct
// (class, material, kind, texture) and every 120th repeat, plus the per-second counts in the cardraw total.
static uint32_t cd_miss[5][2];            // [kind][traffic?] per-second counts
void mat_cardraw_missing(int kind, int sampler, uint32_t guest, uint32_t fmt) {
    uint32_t fx = M32(CUR_EFFECT);
    if (!diag_on || !fx || M32(fx + 4) != 4 || !cd_mat) return;
    int t = (cd_flags & 0x80000) != 0;
    uint32_t n = ++cd_miss[kind][t];
    if (n == 1 || n % 120 == 0) {
        char name[29];
        memcpy(name, G + cd_mat + 0x14, 28); name[28] = 0;
        diag_event("cardraw: MISSING texture kind %d (1 none 2 no-gpu 3 type 4 blank) %s mat '%s' sampler s%d tex %08X fmt %u (count %u this second)",
                   kind, t ? "TRAFFIC" : "other", name, sampler, guest, fmt, n);
    }
}
void mat_cardraw_note(const uint32_t *rs) {
    static CarDrawStat st[64];
    static int n;
    static uint64_t t0;
    uint32_t fx = M32(CUR_EFFECT);
    if (!diag_on) return;
    uint64_t now = host_ticks();
    if (!t0) t0 = now;
    if (fx && M32(fx + 4) == 4 && cd_mat) {
        int k = 0;
        while (k < n && !(st[k].flags == cd_flags && st[k].mat == cd_mat)) ++k;
        if (k == n && n < 64) { st[n].flags = cd_flags; st[n].mat = cd_mat; ++n; }
        if (k < 64) { ++st[k].total; st[k].blend += rs[27] != 0; st[k].atest += rs[15] != 0; }
    }
    if (now - t0 < 1000000000ull) return;
    t0 = now;
    uint32_t tot[2] = {0}, bl[2] = {0}, at[2] = {0};
    for (int k = 0; k < n; ++k) {
        int t = (st[k].flags & 0x80000) != 0;
        tot[t] += st[k].total; bl[t] += st[k].blend; at[t] += st[k].atest;
        if (st[k].blend || st[k].atest) {
            char name[29];
            memcpy(name, G + st[k].mat + 0x14, 28); name[28] = 0;
            diag_event("cardraw: flags %05X %s mat '%s' alpha %g..%g: draws %u blended %u atest %u", st[k].flags,
                       t ? "TRAFFIC" : "other", name, F32(st[k].mat + 0x50), F32(st[k].mat + 0x54), st[k].total, st[k].blend, st[k].atest);
        }
    }
    for (int k = 1; k < 5; ++k)
        if (cd_miss[k][0] || cd_miss[k][1]) diag_event("cardraw: missing-texture draws kind %d: traffic %u other %u", k, cd_miss[k][1], cd_miss[k][0]);
    memset(cd_miss, 0, sizeof cd_miss);
    if (n) diag_event("cardraw: total traffic %u draws, %u blended, %u atest | other %u draws, %u blended, %u atest", tot[1], bl[1], at[1], tot[0], bl[0], at[0]);
    n = 0;
    memset(st, 0, sizeof st);
}

// ---- payload 1: car paint ----
// MAT_VARIANT n = the PC car PS tn (its shadow lookup, transcribed from tn_car_p0_ps_2_*.asm); MAT_SHADOW_HW = s2 is a
// depth texture (render.m sets shadow_samplers from the bound texture), sampled with compare like the translator does.
// c[0].x InvShadowStrength, c[1].x ShadowMapScale (t2, t3). m.k[0] = (SpecularPower, SpecularHotSpot x tod gain,
// MetallicScale, mode: 0 X360, 1 probe signature (magenta), 2 the PC specular for BOOT_DIAG_CARPAINT_AB),
// m.k[1].rgb = sun colour.
static const char car_paint_msl[] =
"#include <metal_stdlib>\n"
"using namespace metal;\n"
"#pragma clang diagnostic ignored \"-Wunused-function\"\n"
"struct SmPSAux { uint alpha_func; float alpha_ref; uint pad0, pad1; float4 bumpenv[8]; float4 bumplum[8]; };\n"
"struct MatConsts { float4 k[12]; };\n"
"struct In {\n"
"    float4 pos [[position]];\n"
"    float4 color0 [[user(color0)]]; float4 color1 [[user(color1)]];\n"
"    float4 texcoord0 [[user(texcoord0)]]; float4 texcoord1 [[user(texcoord1)]]; float4 texcoord2 [[user(texcoord2)]];\n"
"    float4 texcoord3 [[user(texcoord3)]]; float4 texcoord4 [[user(texcoord4)]]; float4 texcoord5 [[user(texcoord5)]];\n"
"    float4 texcoord6 [[user(texcoord6)]];   // R26: object position, vertex colour x (the t1 VS patch)\n"
"};\n"
"struct Out { float4 c0 [[color(0)]]; };\n"
"static bool alpha_test(uint f, float a, float ref) {\n"
"    float qa = rint(saturate(a) * 255.0f), qr = rint(saturate(ref) * 255.0f);\n"
"    switch (f) {\n"
"    case 1: return false;      case 2: return qa < qr;  case 3: return qa == qr;\n"
"    case 4: return qa <= qr;   case 5: return qa > qr;  case 6: return qa != qr;\n"
"    case 7: return qa >= qr;   default: return true;\n"
"    }\n"
"}\n"
"#if MAT_SHADOW_HW\n"
"#define SHADOW_TEX depth2d<float>\n"
"static float sd(SHADOW_TEX t, sampler s, float4 c) { return t.sample_compare(s, c.xy / c.w, c.z / c.w); }   // texldp\n"
"static float lit1(SHADOW_TEX t, sampler s, float2 uv, float z) { return t.sample_compare(s, uv, z); }\n"
"#else\n"
"#define SHADOW_TEX texture2d<float>\n"
"static float sd(SHADOW_TEX t, sampler s, float4 c) { return t.sample(s, c.xy / c.w).x; }\n"
"static float lit1(SHADOW_TEX t, sampler s, float2 uv, float z) { return z - t.sample(s, uv).x >= 0.0f ? 0.0f : 1.0f; }\n"
"#endif\n"
"static float sc(texture2d<float> t, sampler s, float4 c) { return t.sample(s, c.xy / c.w).x; }\n"
"fragment Out sm_main(In pi [[stage_in]], constant float4 *c [[buffer(0)]], constant SmPSAux &aux [[buffer(3)]],\n"
"    constant MatConsts &m [[buffer(4)]],\n"
"    texture2d<float> tex0 [[texture(0)]], sampler smp0 [[sampler(0)]],\n"
"    texturecube<float> tex1 [[texture(1)]], sampler smp1 [[sampler(1)]],\n"
"    SHADOW_TEX tex2 [[texture(2)]], sampler smp2 [[sampler(2)]],\n"
"    texture2d<float> tex3 [[texture(3)]], sampler smp3 [[sampler(3)]],\n"
"    texture3d<float> flake_vol [[texture(14)]],\n"
"    texture2d<float> nmap [[texture(15)]]) {\n"
"#if MAT_CAR_HDR\n"
"    float4 v0 = float4(max(pi.color0.rgb, 0.0f), saturate(pi.color0.a));   // NFS_CAR_LIGHT=hdr: the VS light unclamped\n"
"#else\n"
"    float4 v0 = saturate(pi.color0);\n"
"#endif\n"
"    float4 v1 = saturate(pi.color1), t2 = pi.texcoord2;\n"
"    float lit;                                   // lit fraction of the PC variant's shadow lookup\n"
"#if MAT_VARIANT == 1\n"
"    lit = 0.5f - sc(tex3, smp3, t2) >= 0.0f ? sd(tex2, smp2, t2) : 1.0f;\n"
"#elif MAT_VARIANT == 2 || MAT_VARIANT == 3\n"
"#if MAT_VARIANT == 2\n"
"    const int n = 2; const float o0 = -0.5f;     // 2x2 taps at +-0.5 texel\n"
"#else\n"
"    const int n = 4; const float o0 = -1.5f;     // 4x4 taps at +-0.5, +-1.5 texel\n"
"#endif\n"
"    float k = t2.w * c[1].x, sum = 0.0f, scsum = 0.0f, scprod = 1.0f;\n"
"    for (int y = 0; y < n; ++y) for (int x = 0; x < n; ++x) {\n"
"        float4 p = float4(t2.xy + k * float2(o0 + x, o0 + y), t2.zw);\n"
"        sum += sd(tex2, smp2, p);\n"
"        float s3 = sc(tex3, smp3, p); scsum += s3; scprod *= s3;\n"
"    }\n"
"#if MAT_VARIANT == 2\n"
"    lit = 1.0f - scsum >= 0.0f ? sum * 0.25f : 1.0f;\n"
"#else\n"
"    lit = -scprod >= 0.0f ? sum * 0.0625f : 1.0f;\n"
"#endif\n"
"#elif MAT_VARIANT == 4\n"
"    float2 uv = t2.xy / t2.w;\n"
"    lit = 0.5f - tex3.sample(smp3, uv).x >= 0.0f ? lit1(tex2, smp2, uv, t2.z / t2.w) : 1.0f;\n"
"#else\n"
CAR_SHADOW_OFF16   // tools/gen_tables.py, from the player's speed.exe (the PC car PS's 16 taps)
"    float2 uv = t2.xy / t2.w; float z = t2.z / t2.w, sum = 0.0f;\n"
"    for (int i = 0; i < 16; ++i) sum += lit1(tex2, smp2, uv + off[i], z);\n"
"    lit = 0.5f - tex3.sample(smp3, uv).x >= 0.0f ? sum * 0.0625f : 1.0f;\n"
"#endif\n"
"    float shade = mix(" PCS_CAR_SHADOW_FLOOR "f + c[0].x, 1.0f, lit);   // the PC's shadow factor\n"
"    float4 tex = tex0.sample(smp0, pi.texcoord1.xy), env = tex1.sample(smp1, pi.texcoord0.xyz);\n"
"    // X360 09bb919c: N and L normalised per pixel, R = reflect(-V, N), spec lobe + hotspot lobe, x sun colour\n"
"    // R30 metallic flake (X360 09bb919c): n' = n + (2 tex3D(VOLUMEMAP, " X360S_CAR_FLAKE_VOL_T " pos) - 1) MetallicScale " X360S_CAR_FLAKE_SCALE_T " saturate(" X360S_CAR_FADE_A_T " - " X360S_CAR_FADE_B_T " dist), point, wrap\n"
"    constexpr sampler fls(filter::nearest, address::repeat);\n"
"    float metal = m.k[11].w >= 0.0f ? m.k[11].w : m.k[0].z;                        // k[11].w: test, MetallicScale forced (-1: the material's)\n"
"    float flake = m.k[11].x > 0.5f ? metal * " X360S_CAR_FLAKE_SCALE "f * (m.k[11].z > 0.5f ? 1.0f : saturate(pi.texcoord3.w)) : 0.0f;   // k[11].z: test, no fade\n"
"    float3 fv = flake > 0.0f ? 2.0f * flake_vol.sample(fls, pi.texcoord6.xyz * " X360S_CAR_FLAKE_VOL "f, level(0)).xyz - 1.0f : float3(0.0f);\n"
"    float3 nn = normalize(normalize(pi.texcoord4.xyz) + fv * flake), l = normalize(pi.texcoord3.xyz), v = pi.texcoord5.xyz;\n"
"    float rv = saturate(dot(2.0f * dot(nn, v) * nn - v, l));\n"
"    float sp = m.k[0].x;\n"
"    float spec = powr(rv, sp) + " X360S_CAR_HOT_SCALE "f * m.k[0].y * lit * powr(rv, " X360S_CAR_HOT_POWER "f * sp);\n"
"    float3 s = spec * m.k[1].rgb * v0.w * v1.rgb * lit;\n"
"    if (m.k[0].w == 2.0f) {                      // BOOT_DIAG_CARPAINT_AB: the PC PS's own 2 pow(R.V, 16) (unnormalised)\n"
"        float3 t3 = pi.texcoord3.xyz, t4 = pi.texcoord4.xyz;\n"
"        float p = saturate(dot(2.0f * dot(t4, t3) * t4 - t3, pi.texcoord5.xyz)); p *= p; p *= p; p *= p; p *= p;\n"
"        s = 2.0f * p * v0.w * v1.rgb * shade;\n"
"    }\n"
"    Out o;\n"
"    o.c0 = float4((v0.rgb * tex.rgb + s) * shade + env.rgb * v1.w * v0.w * 0.5f * (1.0f + 2.0f * m.k[2].x * v0.w), tex.a * v0.w);\n"
"    if (m.k[3].w > 0.5f) {                       // R26: X360 PS 09bc003c (car_normalmap) on this draw's tangent-space normal map\n"
"        float2 uv = pi.texcoord1.xy;\n"
"        float3 P = pi.texcoord6.xyz, N = normalize(pi.texcoord4.xyz), V = normalize(pi.texcoord5.xyz), L0 = pi.texcoord3.xyz, L1 = m.k[2].yzw;\n"
"        // derivative tangent frame (the PC car vertices have no tangent), handedness from the uv winding\n"
"        float3 dp1 = dfdx(P), dp2 = dfdy(P); float2 du1 = dfdx(uv), du2 = dfdy(uv);\n"
"        float3 T = dp1 * du2.y - dp2 * du1.y, B = dp2 * du1.x - dp1 * du2.x;\n"
"        T *= (du1.x * du2.y - du2.x * du1.y) < 0.0f ? -1.0f : 1.0f;                // the 1/det of the frame: T = d/du, B = d/dv even on mirrored uv\n"
"        T -= N * dot(N, T);\n"
"        T = length(T) > 1e-12f ? normalize(T) : normalize(cross(N, abs(N.x) < 0.9f ? float3(1, 0, 0) : float3(0, 1, 0)));\n"
"        float3 Bn = cross(N, T); Bn = dot(Bn, B) < 0.0f ? -Bn : Bn;\n"
"        float3 nt = normalize(nmap.sample(smp0, uv).xyz * 2.0f - 1.0f);        // r2.wxy = 2 rgb - 1, normalised\n"
"        nt.y *= m.k[7].y;                                                      // green flip (the handedness check)\n"
"        nt = normalize(nt + fv * flake);                                       // R30: the X360 09bc003c adds the same " X360S_CARNM_FLAKE_T " MetallicScale volume term, faded by oT3.w\n"
"        float3 n = normalize(T * nt.x + Bn * nt.y + N * nt.z);\n"
"        float nv = max(dot(N, V), " X360S_CARNM_NV_MIN "f);\n"
"        float3 D = m.k[4].xyz + nv * m.k[5].xyz;                              // oColor0 = DiffuseMin + max(N.V, " X360S_CARNM_NV_MIN_T ") DiffuseRange\n"
"        float w0 = saturate(dot(n, L0) + " X360S_CARNM_WRAP "f), w1 = saturate(dot(n, L1) + " X360S_CARNM_WRAP "f);   // two wrap lights, wrap " X360S_CARNM_WRAP_T "\n"
"        float3 dif = m.k[1].rgb * w0 + m.k[3].rgb * w1;                       // LocalColourMatrix[c].x w0 + [c].y w1\n"
"        float rv = saturate(dot(2.0f * dot(n, L0) * n - L0, V));\n"
"        float aoq = pi.texcoord6.w / max(saturate(2.0f * pi.texcoord6.w), 1e-3f);   // X360 AO is the raw vertex colour x; PC colour1 has saturate(2x) in it\n"
"        float spec = " X360S_CARNM_SPEC "f * powr(rv, sp) + m.k[0].y * lit * powr(rv, " X360S_CARNM_SPEC_POWER "f * sp);\n"
"        float3 s2 = spec * m.k[1].rgb * v0.w * v1.rgb * aoq * lit;\n"
"        float3 Vt = float3(dot(T, V), dot(Bn, V), dot(N, V));                  // the X360 samples the cube with the tangent-space reflection\n"
"        float3 Rt = 2.0f * max(dot(nt, Vt), " X360S_CARNM_NV_MIN "f) * nt - Vt;\n"
"        float3 fres = exp2(m.k[10].x * log2(nv)) * m.k[9].xyz + m.k[8].xyz;      // X360: pow(N.V, EnvmapPower) EnvmapRange + EnvmapMin (per channel)\n"
"        float3 e2 = 2.0f * tex1.sample(smp1, Rt).rgb * fres * v0.w * pi.texcoord6.w;   // 2 env x fresnel x alpha x raw vertex AO\n"
"        o.c0 = float4((D * dif * tex.rgb + s2) * shade + e2, tex.a * v0.w);\n"
"        if (m.k[7].x > 0.5f) o.c0 = float4(1.0f, 0.0f, 1.0f, 1.0f);           // NFS_TEST_CARNM_TINT\n"
"    }\n"
"    if (m.k[1].w > 0.5f) {                       // NFS_HIGHLIGHTS=rolloff: a shoulder on the HDR result before the 8-bit clip\n"
"        float mx = max(o.c0.r, max(o.c0.g, o.c0.b));\n"
"        if (mx > 0.8f) {                            // knee 0.8 -> white asymptotically, hue kept, desaturating as it compresses\n"
"            float y = 0.8f + 0.2f * (1.0f - exp(-(mx - 0.8f) / 0.2f));\n"
"            o.c0.rgb = mix(o.c0.rgb * (y / mx), float3(y), 0.5f * saturate((mx - 1.0f) * 0.5f));\n"
"        }\n"
"    }\n"
"    if (m.k[11].y > 0.5f && flake > 0.0f) o.c0 = float4(1.0f, 0.0f, 1.0f, 1.0f);   // NFS_TEST_FLAKE_TINT: where the flake term is non-zero\n"
"    if (m.k[0].w == 1.0f) o.c0 = float4(1.0f, 0.0f, 1.0f, 1.0f);   // probe signature\n"
"    if (aux.alpha_func != 0u && !alpha_test(aux.alpha_func, o.c0.w, aux.alpha_ref)) discard_fragment();\n"
"    return o;\n"
"}\n";

// BOOT_DIAG_CARPAINT_AB=1 (diag runs with BOOT_DIAG_SIMSHOTS): after each simshot the next pass is held (pacing.c:
// no sim tick, no real time, the same scene) and drawn with the PC car specular in the same replacement shader, saved
// as simshot_<t>_pcpaint.png: same-frame X360 / PC pairs.
static int ab = -1;                      // (ab_state above) 1 shot taken, 2 the held pass (PC specular), 3 its shot taken
static char ab_path[512];
static int carpaint_ab(void) {
    if (ab < 0) ab = diag_on && (getenv("BOOT_DIAG_CARPAINT_AB") || getenv("BOOT_DIAG_GLASS_AB") || getenv("BOOT_DIAG_AUX_AB")) ? atoi(getenv("BOOT_DIAG_CARPAINT_AB") ? getenv("BOOT_DIAG_CARPAINT_AB") : "1") : 0;
    return ab > 0;
}
int carpaint_ab_shot(char *path, size_t n) {        // render_present.m, each Present: `path` is this frame's shot (or empty)
    if (!carpaint_ab()) return 0;
    if (ab_state == 2) { snprintf(path, n, "%s", ab_path); ab_state = 3; return 1; }
    if (ab_state == 0 && path[0] && strstr(path, "/simshot_")) {
        snprintf(ab_path, sizeof ab_path, "%.*s%s", (int)(strlen(path) - 4), path, aux_ab_on() ? "_pcaux.png" : "_pcpaint.png");
        ab_state = 1;
    }
    return 0;
}
int carpaint_ab_hold(void) {                        // pacing.c, end of each main-loop pass: 1 = hold the next pass
    if (ab_state == 1) { ab_state = 2; return 1; }
    if (ab_state == 3) { ab_state = 0; return 1; }
    return 0;
}

static int car_paint = -1, car_paint_live = 1;
static int car_paint_on(void) {
    if (car_paint < 0) { const char *e = getenv("NFS_CAR_PAINT"); car_paint = !(e && !strcmp(e, "pc")) && X360_SHADER_LITS; }
    return car_paint;
}
// The F10 menu (portmenu.m): the replacement is chosen when a shader is translated, so a run started with the X360
// paint switches live to PC through the replaced shader's PC-specular path (the A/B's held pass, k[0][3] = 2) and
// back; a run started with PC paint keeps the PC shaders until the next start.
void materials_set_car_paint(int x360) { car_paint_live = x360 != 0; if (diag_on) diag_event("materials: car paint live now %s", car_paint_live ? "x360" : "pc"); }
// ---- R30: X360 metallic flake (NFS_CAR_FLAKE=pc|x360, default pc; live) ----
// X360 car PS 09bb919c / 09bc003c [V]: the normal is jittered by (2 tex3D(VOLUMEMAP, V pos) - 1) x MetallicScale x S x
// saturate(A - B dist) (V, S, A, B: the VS's literals, X360S_CAR_FLAKE_* / X360S_CAR_FADE_*) (dist = |LocalEyePos - pos| in object space, from the VS 09bb7b1c; point filter, wrap), the 32^3 volume R27
// loads (GLOBALB chunk 30300200). The PC data has the same MetallicScale (+0xA0) as the X360's GLOBALB (offline read of both
// GLOBAL/GLOBALB.BUN eLightMaterial records, 0xA0 = 1 on METPAINT*, PEARL*, MAGSILVER, USER_RIMS, HOSES, else 0), so the flake shows on
// metallic and pearl paints. The t1 car VS patch (above) gives the PS the position (texcoord6.xyz) and the fade (texcoord3.w).
int materials_flake_tint, materials_flake_force = -1;   // test only: NFS_TEST_FLAKE_TINT (1 / 2), NFS_TEST_FLAKE_FORCE (0 / 1), BOOT_KEYS flake: steps
static int flake = -1;
static int flake_on(void) {
    if (flake < 0) {
        const char *e = getenv("NFS_CAR_FLAKE"), *t = getenv("NFS_TEST_FLAKE_TINT"), *f = getenv("NFS_TEST_FLAKE_FORCE");
        flake = !(e && !strcmp(e, "pc")) && X360_SHADER_LITS;
        if (t) materials_flake_tint = atoi(t);
        if (f) materials_flake_force = atoi(f);
    }
    return flake;
}
void materials_flake_test(int tint, int force) { materials_flake_tint = tint; materials_flake_force = force; if (diag_on) diag_event("materials: flake test tint %d force %d", tint, force); }
void materials_set_car_flake(int x360) { flake = x360 != 0; if (diag_on) diag_event("materials: car flake live now %s", flake ? "x360" : "pc"); }
int mat_flake_volume_ok(void) { uint32_t w, h, b; return mat_grass_noise(&w, &h) && mat_grass_volume(&b); }   // render.m binds the same volume as R27's
static void car_paint_constants(const float (*vsc)[4], MatConsts *o) {
    o->k[0][0] = mat_car.spec_power; o->k[0][1] = mat_car.hotspot; o->k[0][2] = mat_car.metallic;
    o->k[0][3] = (probe_sig & PROBE_CARPAINT) ? 1.0f : (ab_state == 2 && !glass_ab_held() && !aux_ab_on()) || !car_paint_live ? 2.0f : 0.0f;
    o->k[2][0] = glass_boost ? 1.0f : 0.0f;
    {
        o->k[11][0] = flake_on() && mat_flake_volume_ok() && o->k[0][3] != 2.0f ? 1.0f : 0.0f;
        o->k[11][1] = materials_flake_tint ? 1.0f : 0.0f;
        o->k[11][2] = materials_flake_tint == 2 ? 1.0f : 0.0f;                   // tint 2 (test): distance fade ignored (which draws carry MetallicScale at any range)
        if (materials_flake_force >= 0) o->k[11][3] = (float)materials_flake_force;       // force 0 / 1 (test): the flake's MetallicScale (0 = the negative control)
        else o->k[11][3] = -1.0f;
        if (diag_on) {
            static uint32_t nd, nm, nf;
            ++nd; nm += mat_car.metallic > 0.0f; nf += o->k[11][0] > 0.0f && mat_car.metallic > 0.0f;
            if (nd % 20000 == 0) diag_event("flake: %u car paint draws, %u with MetallicScale > 0, %u flaking (switch %s)", nd, nm, nf, flake_on() ? "x360" : "pc");
        }
    }
    if (probe_sig & PROBE_CARPAINT) probe_applied(PROBE_CARPAINT, 1);
    for (int i = 0; i < 3; ++i) o->k[1][i] = vsc[11 + i][0];   // LocalColourMatrix[0..2].x (PC car VS c11-c13): light 0, the sun
    int x360_post_rolloff(void);                                // x360_post.m: NFS_HIGHLIGHTS (the held look variant's)
    o->k[1][3] = x360_post_rolloff() ? 1.0f : 0.0f;
    // R26 (above): the normal-map path needs this draw's map, the PC specular compare off, and the t1 VS's object position in texcoord6
    static int tint = -1;
    if (tint < 0) tint = getenv("NFS_TEST_CARNM_TINT") ? atoi(getenv("NFS_TEST_CARNM_TINT")) : 0;
    o->k[3][3] = carnm_tex && carnm_on() && o->k[0][3] != 2.0f && o->k[0][3] != 1.0f ? 1.0f : 0.0f;
    if (o->k[3][3] > 0.0f) {
        for (int i = 0; i < 3; ++i) {
            o->k[2][1 + i] = vsc[15][i];           // LocalDirectionMatrix[1] (light 1)
            o->k[3][i] = vsc[11 + i][1];           // LocalColourMatrix[0..2].y
            o->k[4][i] = vsc[18][i];               // DiffuseMin
            o->k[5][i] = vsc[19][i];               // DiffuseRange
            o->k[8][i] = vsc[22][i];               // EnvmapMin
            o->k[9][i] = vsc[23][i];               // EnvmapRange
        }
        o->k[7][0] = tint == 1 || (tint == 2 && carnm_kind != 1) || (tint == 3 && carnm_kind == 2) || (tint == 4 && carnm_kind == 3) ? 1.0f : 0.0f;   // =2 not the slot-keyed MISC, =3 only _TIRE_N, =4 only _BADGING_N
        o->k[7][1] = 1.0f;                         // green sign: +1 (decoded, see the note above the PS)
        o->k[10][0] = vsc[24][0];                  // EnvmapPower
        if (diag_on) { static uint32_t n; ++n; if (n < 6 || n % 2000 == 0) diag_event("carnm: %u normal-mapped car draws (%.31s)", n, carnm_name); }
    }
    if (diag_on) {                       // R26 premise: which car draws carry a normal-map slot (006c6d10's cache)
        uint32_t nt = M32(0x00982CB8u), dt = M32(0x00982CB4u);
        static uint32_t seen[256][2]; static int ns;
        int i = 0;
        while (i < ns && !(seen[i][0] == dt && seen[i][1] == nt)) ++i;
        if (i == ns && ns < 256 && nt) { seen[ns][0] = dt; seen[ns][1] = nt; ++ns;
            diag_event("carnm: draw diffuse %08X \"%.31s\" normal %08X \"%.31s\" hash %08X", dt, dt ? (const char *)G + dt + 0x0C : "", nt, (const char *)G + nt + 0x0C, dt ? M32(dt + 0x24) : 0); }
    }
    if (diag_on) {
        static int calls;
        if (calls++ % 20000 == 0)
            diag_event("materials: car paint draw %d: SpecularPower %g HotSpot %g Metallic %g sun %g %g %g", calls,
                       o->k[0][0], o->k[0][1], o->k[0][2], o->k[1][0], o->k[1][1], o->k[1][2]);
    }
}

// ---- payload 2: rain ripples on wet roads ----
// X360 [V] (x360/shaders/hlsl/17_road): `wetroad` VS 09c14d8c passes the vertex position's X/Y x a literal (X360S_RIPPLE_TILE) as oT0.zw (the
// ripple coordinates: a top-down projection, 5 world units per tile); PS 09c17c18 samples MISCMAP2 there and at a second scale (X360S_RIPPLE_SCALE2),
// s = tex(uv) + tex(1.5 uv), R = RainIntensity, adds 0.5 R s.x to x, y and w of the projective reflection lookup
// (MISCMAP1, texldp), adds the glint G R (s.y - s.z) to the reflection colour and scales it by (1 - F R) (X360S_RIPPLE_GLINT / _REFL), before
// SurfaceReflection (wetness), the vertex alpha and specmap.r. MISCMAP2 is RAINSPLASH00-15 (X360 GLOBAL/GLOBALB.BUN only,
// 128x128 DXT1; names "RAINSPLASH%02d", Function_8244A5E0), the frame (int)(20 * fmod(t, 16 / 20)) with t = the
// 1/4000 s ticker x 0.00025 (setter Function_8244F928, frame rate 20.0 at +0x68 set at default_8242ddc0.c:16840):
// 16 frames, a 0.8 s loop; only while R > 0. RAIN_INTENSITY = Rain+0x28c on both (8244F928; PC Rain::Update smooths
// +0x28c towards the zone/override intensity, 0..1, speed_0074d050.c:9274-9292 [V]).
// PC: worldreflect (render_diff/hdr_dof/pc_effects/worldreflect) PS t0-t5 all look the reflection up once with
// `texldp r0, t1, s3` and use only its rgb times 2 specmap.r x diffuse.a x v0.w x SpecularColour.w (wetness) x fog,
// so the X360 term folds into the sampled colour: (refl(perturbed) + glint) x (1 - F R) keeps the X360 maths with
// the PC's own balance. The PC VS (t0's own, and t1's shared by t1-t5) writes oT2.xy = uv, which no PS reads; the
// patch adds oT2.zw = v0.xy x X360S_RIPPLE_TILE (v0 = dcl_position). Patched, not replaced: the translation keeps every other
// instruction, so with R = 0 (no rain, switch off live, no X360 textures) the output is the PC shader's.
// Per draw: R from the current view's Rain (FUN_006d71f0 reads the same object: *(0x00919648 + view id x 0x70), view
// = DAT_00982a20), the frame from the sim time (Sim::GetTime, 009885D8): deterministic per sim tick, so the gate's
// reference frames repeat (RealTimer, closer to the X360's ticker [I], follows wall time and made them differ).
// m.k[0] = (R, frame, mode: 0 X360, 1 probe signature (magenta where R > 0 on wet roads), 0).
#define CUR_VIEW       0x00982A20u     // the eView being drawn: +4 its id
#define VIEW_RAIN      0x00919648u     // eViews[0] + 0x68 (Rain *), stride 0x70
#define SIM_TIME       0x009885D8u     // Sim::GetTime, seconds of sim time
static int ripples = -1, ripples_live = 1;
static uint8_t *ripple_rgba[16];
static uint32_t ripple_w, ripple_h;
static int ripples_on(void) {          // any thread (shader compiles): the env only, the textures are checked per draw
    if (ripples < 0) { const char *e = getenv("NFS_RAIN_RIPPLES"); ripples = !(e && !strcmp(e, "pc")) && X360_SHADER_LITS && X360_ENGINE_CONSTS; }
    return ripples;
}
void materials_set_rain_ripples(int x360) { ripples_live = x360 != 0; if (diag_on) diag_event("materials: rain ripples live now %s", ripples_live ? "x360" : "pc"); }   // the F10 menu: live in a run started with x360
uint8_t *x360_texture_rgba(const char *file, uint32_t hash, uint32_t *w, uint32_t *h);   // x360_textures.m
void materials_textures(void) {        // x360_textures.m, at the first texture Init (start-up, after the X360 dir is known)
    if (!ripples_on()) return;
    int n = 0;
    for (int i = 0; i < 16; ++i) {
        char name[16];
        snprintf(name, sizeof name, "RAINSPLASH%02d", i);
        uint32_t hh = 0xFFFFFFFFu, w = 0, h = 0;
        for (const char *q = name; *q; ++q) hh = hh * 33 + (uint8_t)*q;    // bStringHash
        ripple_rgba[i] = x360_texture_rgba("GLOBAL/GLOBALB.BUN", hh, &w, &h);
        if (ripple_rgba[i] && (!n || (w == ripple_w && h == ripple_h))) { ripple_w = w; ripple_h = h; ++n; }
        else { free(ripple_rgba[i]); ripple_rgba[i] = NULL; }
    }
    if (n < 16) {
        for (int i = 0; i < 16; ++i) { free(ripple_rgba[i]); ripple_rgba[i] = NULL; }
        fprintf(stderr, "boot: rain ripples: no X360 RAINSPLASH frames, wet roads stay PC\n");
    } else if (diag_on) diag_event("materials: rain ripples: 16 RAINSPLASH frames %ux%u from the X360 GLOBALB.BUN", ripple_w, ripple_h);
}
const uint8_t *mat_ripple_frame(int i, uint32_t *w, uint32_t *h) {   // render.m: RGBA8 frame i (NULL: none)
    *w = ripple_w; *h = ripple_h;
    return i >= 0 && i < 16 ? ripple_rgba[i] : NULL;
}
static void ripple_constants(const float (*vsc)[4], MatConsts *o) {
    (void)vsc;
    uint32_t view = M32(CUR_VIEW), rain = view ? M32(VIEW_RAIN + M32(view + 4) * 0x70u) : 0;
    float r = rain && ripple_rgba[15] && ripples_live ? F32(rain + 0x28C) : 0.0f;
    if (!(r > 0.0f)) r = 0.0f;
    if (r > 1.0f) r = 1.0f;
    double t = F32(SIM_TIME);
    int frame = (int)(X360E_RIPPLE_FPS * fmod(t, 16.0 / X360E_RIPPLE_FPS));   // 16 frames at the X360's rate
    o->k[0][0] = r; o->k[0][1] = (float)(frame < 0 ? 0 : frame > 15 ? 15 : frame);
    o->k[0][2] = (probe_sig & PROBE_RIPPLES) ? 1.0f : 0.0f;
    if ((probe_sig & PROBE_RIPPLES) && r > 0.0f) probe_applied(PROBE_RIPPLES, 1);
    if (diag_on) {
        static int calls;
        if (calls++ % 20000 == 0) diag_event("materials: rain ripples draw %d: rain %.3f frame %d", calls, r, frame);
    }
}
// The PS patch (all six worldreflect PS translate the lookup the same way; anything else keeps the translation).
static const char ripple_ps_helpers[] =
"struct MatConsts { float4 k[4]; };\n"
"// rain ripples (materials.m): rip = (lookup offset 0.5 R s.x, glint " X360S_RIPPLE_GLINT_T " R (s.y - s.z), reflection scale 1 - " X360S_RIPPLE_REFL_T " R)\n"
"static float4 mat_ripple(float4 t2, constant MatConsts &m, texture2d_array<float> tr) {\n"
"    constexpr sampler rs(filter::linear, mip_filter::linear, address::repeat, max_anisotropy(8));\n"
"    float rain = m.k[0].x;\n"
"    if (!(rain > 0.0f)) return float4(0.0f, 0.0f, 1.0f, 0.0f);\n"
"    uint f = uint(m.k[0].y);\n"
"    float3 s = tr.sample(rs, t2.zw, f).rgb + tr.sample(rs, t2.zw * " X360S_RIPPLE_SCALE2 "f, f).rgb;\n"
"    return float4(0.5f * rain * s.x, " X360S_RIPPLE_GLINT "f * rain * (s.y - s.z), 1.0f - " X360S_RIPPLE_REFL "f * rain, 0.0f);\n"
"}\n"
"static float4 mat_refl(texture2d<float> t, sampler s, float4 p, float4 rip, float lodbias) {   // X360 09c17c18\n"
"    float4 q = p + float4(rip.x, rip.x, 0.0f, rip.x);\n"
"    float4 r = t.sample(s, q.xy / q.w, bias(lodbias));\n"
"    return float4((r.rgb + rip.y) * rip.z, r.a);\n"
"}\n";
// Edits *s (malloc'd): the first `what` at or after `from` (NULL: the start) replaced by `with`, or `with` inserted
// before it (before = 1). 0 when not found.
static int edit(char **s, const char *from, const char *what, const char *with, int before) {
    char *f = from ? strstr(*s, from) : *s, *at = f ? strstr(f, what) : NULL;
    if (!at) return 0;
    size_t pre = (size_t)(at - *s), keep = before ? 0 : strlen(what), nl = strlen(with);
    char *o = malloc(strlen(*s) - keep + nl + 1);
    memcpy(o, *s, pre);
    memcpy(o + pre, with, nl);
    strcpy(o + pre + nl, at + keep);
    free(*s);
    *s = o;
    return 1;
}
static int count(const char *s, const char *what) { int n = 0; for (; (s = strstr(s, what)); ++s) ++n; return n; }
static char *ripple_ps_patch(const char *msl) {
    // the reflection lookup, exactly once: tex3.sample(smp3, (X).xy / (X).w, bias(aux.lodbias[3]))
    const char *at = strstr(msl, "tex3.sample(smp3, (");
    size_t n = at ? strcspn(at + 19, ")") : 0;
    char var[64], old[256], rep[256];
    if (!at || n == 0 || n >= sizeof var || count(msl, "tex3.sample(") != 1) return NULL;
    memcpy(var, at + 19, n); var[n] = 0;
    snprintf(old, sizeof old, "tex3.sample(smp3, (%s).xy / (%s).w, bias(aux.lodbias[3]))", var, var);
    snprintf(rep, sizeof rep, "mat_refl(tex3, smp3, %s, si.tex[2], aux.lodbias[3])", var);
    char *s = strdup(msl);
    int ok = edit(&s, NULL, old, rep, 0) && edit(&s, NULL, "static void sm_body(", ripple_ps_helpers, 1);
    if (ok && !strstr(s, "[[user(texcoord2)]]"))
        ok = edit(&s, "struct SmPSStageIn", "    float4 texcoord3 [[user(texcoord3)]];\n", "    float4 texcoord2 [[user(texcoord2)]];\n", 1);
    ok = ok && edit(&s, "fragment SmPSStageOut sm_main(", ") {\n",
                    ",\n    constant MatConsts &mk [[buffer(4)]], texture2d_array<float> texR [[texture(15)]]) {\n", 0);
    ok = ok && edit(&s, "fragment SmPSStageOut sm_main(", "    SmPSOut so;\n",
                    "    si.tex[2] = mat_ripple(pi.texcoord2, mk, texR);   // t2: no worldreflect PS reads it\n", 1);
    ok = ok && edit(&s, "fragment SmPSStageOut sm_main(", "    return r;\n}",
                    "    if (mk.k[0].z == 1.0f && mk.k[0].x > 0.0f && c[1].w > 0.01f) r.c0 = float4(1.0f, 0.0f, 1.0f, 1.0f);   // probe signature (c1.w = wetness)\n", 1);
    if (!ok) { free(s); return NULL; }
    return s;
}
static char *ripple_vs_patch(const char *msl) {
    char *s = strdup(msl);
    if (!edit(&s, NULL, "    r.texcoord2 = so.tex[2];\n",
              "    r.texcoord2 = float4(so.tex[2].xy, si.v[0].xy * " X360S_RIPPLE_TILE "f);   // + the X360 wetroad ripple coordinates (materials.m)\n", 0)) {
        free(s); return NULL;
    }
    return s;
}

// ---- R25: X360 parallax and N.L self-shadow on world normal maps (NFS_PARALLAX=x360|pc, default pc) ----
// The shaders are always patched (with the flag 0 and the mode 0 they give the PC output), so the F10 switch is live both ways.
static int parallax_live = -1;
static int parallax_on(void) { return X360_SHADER_LITS; }
static int parallax_mode(void) { if (parallax_live < 0) { const char *e = getenv("NFS_PARALLAX"); parallax_live = !(e && !strcmp(e, "pc")); } return parallax_live; }
void materials_set_parallax(int x360) { parallax_live = x360 != 0; if (diag_on) diag_event("materials: parallax live now %s", parallax_live ? "x360" : "pc"); }
#define NORMAL_SLOT 0x00982CB8u        // FUN_006c6d10's cached normal-map TextureInfo of the material being drawn
static void parallax_constants(const float (*vsc)[4], MatConsts *o) {
    (void)vsc;
    uint32_t ti = M32(NORMAL_SLOT);
    static int tint = -1;
    if (tint < 0) tint = getenv("NFS_TEST_PARALLAX_TINT") != NULL;
    o->k[0][0] = parallax_mode() && ti && G[ti + 0x55] != 0 ? 1.0f : 0.0f;
    o->k[0][1] = parallax_mode() ? 1.0f : 0.0f;
    o->k[0][2] = tint ? 1.0f : 0.0f;
    if (diag_on && ti) {
        static uint32_t seen[512]; static int n;
        int i = 0;
        while (i < n && seen[i] != ti) ++i;
        if (i == n && n < 512) { seen[n++] = ti; diag_event("materials: parallax normal map %08X \"%.31s\" +0x55=%u", ti, (const char *)G + ti + 0x0C, G[ti + 0x55]); }
    }
}
// The PS patch (worldnormalmap PS t0-t5 all sample s0 diffuse, s1 normal (alpha = height), s2 specular at t0, read the tangent-space
// light in t2 and eye in t3, and end the sun term as `mad r1.xyz, <diffuse N.L>, c0, <spec>`). X360 09c0d800: all samples at
// uv + flag (2 s a - b) normalize(eye).xy, and the sun term x saturate(k N.L_geo) (s, b, k: the PS's literals, X360S_PARALLAX_*;
// the port doubles the height scale). si.tex[1] (no worldnormalmap PS reads t1)
// carries (flag, x360 live, tint, 0) from MatConsts into sm_body.
static const char parallax_ps_helpers[] =
"struct MatConsts { float4 k[4]; };\n";
static char *parallax_ps_patch(const char *msl) {
    char *s = strdup(msl);
    int ok = edit(&s, NULL, "static void sm_body(", parallax_ps_helpers, 1);
    ok = ok && edit(&s, "static void sm_body(", "    so.depth = 0.0f; so.killed = false;\n",
        "    float4 pt0 = si.tex[0];   // R25 parallax: the lookups below read pt0, not t0\n"
        "    if (si.tex[1].x > 0.0f) {\n"
        "        float h = tex1.sample(smp1, si.tex[0].xy, bias(aux.lodbias[1])).a;\n"
        "        pt0.xy += (" X360S_PARALLAX_SCALE2 "f * h - " X360S_PARALLAX_BIAS "f) * normalize(si.tex[3].xyz).xy;\n"
        "    }\n", 1);
    for (int guard = 0; ok && strstr(s, "= si.tex[0]; float4 d") && guard < 16; ++guard)
        ok = edit(&s, NULL, "= si.tex[0]; float4 d", "= pt0; float4 d", 0);
    ok = ok && count(s, "= pt0; float4 d") == 3 && !strstr(s, "= si.tex[0]; float4 d");
    // the sun term (diffuse N.L x c0 + specular) x saturate(4 N.L_geo): the line after `// mad_pp r1.xyz, ..., c0, ...`
    const char *m = ok ? strstr(s, "    // mad_pp r1.xyz, ") : NULL;
    char *nl = m ? strchr(strchr(m, '\n') + 1, '\n') : NULL;
    if (nl) { char *o = malloc(strlen(s) + 160); size_t k = (size_t)(nl + 1 - s);
        memcpy(o, s, k); strcpy(o + k, "    if (si.tex[1].y > 0.0f) r1.xyz *= saturate(" X360S_PARALLAX_SELFSHADOW "f * si.tex[2].z);   // R25 N.L self-shadow\n");
        strcat(o, nl + 1); free(s); s = o; } else ok = 0;
    ok = ok && edit(&s, "static void sm_body(", "}\n\nstruct SmPSStageIn",
        "    if (si.tex[1].z > 0.0f && any(pt0.xy != si.tex[0].xy)) so.oc[0] = float4(1.0f, 0.0f, 1.0f, 1.0f);   // NFS_TEST_PARALLAX_TINT\n", 1);
    ok = ok && edit(&s, "fragment SmPSStageOut sm_main(", ") {\n", ",\n    constant MatConsts &mk [[buffer(4)]]) {\n", 0);
    ok = ok && edit(&s, "fragment SmPSStageOut sm_main(", "    si.vpos =", "    si.tex[1] = mk.k[0];\n", 1);
    if (!ok) { free(s); return NULL; }
    return s;
}

// ---- the table ----
// ---- Payload 3, car lighting above 1 (NFS_CAR_LIGHT=hdr|pc, default hdr; compile time) ----
// The PC car VSs (t0 and t1, render_diff/hdr_dof/pc_effects/car) give oD0 = vertex AO x sum of three lights
// (clamp(N.L + 0.1) x LocalColourMatrix) x (DiffuseMin + N.V x DiffuseRange), and vs_1_x colour outputs are clamped
// to [0,1] (D3D9; host/shaders/sm_msl.cpp :460 on the write, :889 on the PS read). With the sun (0.71/0.75/0.83) and
// two fill lights the sum passes 1 on most sun-facing panels, and DECAL / DULLPLASTIC (DiffuseMin scale 1.2, GLOBALB
// light materials, the same on X360) more so: white paint and the cop decals come out flat 255 before the post sees
                                                                                                                       
// product to a float interpolator into a 7e3 HDR target, so the gradient above 1 reaches its tone map. hdr: the two
// car VS translations write oD0.rgb unclamped, the car paint replacement (t1-t5) and the t0 PS (decals, and every car
// part at shadow level 0) read it unclamped and roll the result off with the highlight shoulder (knee 0.8): a lit
// white keeps its shading. pc: the clamp (flat white).
static int car_light = -1;
int car_light_hdr(void) {
    if (car_light < 0) { const char *e = getenv("NFS_CAR_LIGHT"); car_light = !(e && !strcmp(e, "pc")); }
    return car_light;
}
static char *dup_replace(const char *src, const char *at, size_t cut, const char *ins) {   // src with [at, at+cut) -> ins
    size_t pre = (size_t)(at - src), il = strlen(ins), rest = strlen(at + cut);
    char *o = malloc(pre + il + rest + 1);
    memcpy(o, src, pre); memcpy(o + pre, ins, il); memcpy(o + pre + il, at + cut, rest + 1);
    return o;
}
// VS: `... d = saturate(<expr>); so.col[0].xyz = d.xyz;` -> without the saturate (that line only).
static char *car_vs_patch(const char *msl) {
    const char *w = strstr(msl, "so.col[0].xyz = ");
    if (!w) return NULL;
    const char *line = w;
    while (line > msl && line[-1] != '\n') --line;
    const char *sat = strstr(line, "= saturate(");
    if (!sat || sat > w) return NULL;
    return dup_replace(msl, sat + 2, 8, "");   // "saturate(" -> "(": the parentheses stay balanced
}
// R26: the t1 VS (shadow levels 1-5) also writes the object position and the vertex colour x to texcoord6 (the PC VS leaves it
// unused; the X360 car_normalmap VS passes the position in oT4.w/oT5.w/oT6.w and the colour x in oT0.w), for the PS's derivative
// tangent frame and the X360's unscaled AO. Always patched; the PS reads it only on the normal-map path.
static char *car_vs_nm_patch(const char *msl) {
    char *s = car_light_hdr() ? car_vs_patch(msl) : strdup(msl);
    if (!s) return NULL;
    if (!edit(&s, NULL, "    r.texcoord6 = so.tex[6];\n", "    r.texcoord6 = float4(si.v[0].xyz, si.v[2].x);   // R26 (materials.m)\n", 0)) { free(s); return NULL; }
    // R30: the X360 car VS 09bb7b1c gives the PS oT3.w = saturate(A - B |LocalEyePos - pos|), the flake's distance fade (the PC leaves oT3.w unused)
    if (!edit(&s, NULL, "    r.texcoord3 = so.tex[3];\n", "    r.texcoord3 = float4(so.tex[3].xyz, saturate(" X360S_CAR_FADE_A "f - " X360S_CAR_FADE_B "f * length(c[17].xyz - si.v[0].xyz)));   // R30 (materials.m)\n", 0)) { free(s); return NULL; }
    return s;
}
static int car_vs_nm_on(void) { return 1; }
// t0 PS (ps_1_1): v0.rgb unclamped, and the shoulder on the result (as the car paint PS: knee 0.8, hue kept).
static char *car_t0_ps_patch(const char *msl) {
    const char *r = strstr(msl, "float4 v0 = saturate(si.col[0]);");
    const char *o = strstr(msl, "    so.oc[0] = r0;\n");
    if (!r || !o || o < r) return NULL;
    char *a = dup_replace(msl, r, strlen("float4 v0 = saturate(si.col[0]);"),
                          "float4 v0 = float4(max(si.col[0].xyz, 0.0f), saturate(si.col[0].w));");
    o = strstr(a, "    so.oc[0] = r0;\n");
    char *b = dup_replace(a, o, strlen("    so.oc[0] = r0;\n"),
        "    so.oc[0] = r0;\n"
        "    { float mx = max(r0.x, max(r0.y, r0.z));   // NFS_CAR_LIGHT=hdr: highlight shoulder (materials.m payload 3)\n"
        "      if (mx > 0.8f) { float y = 0.8f + 0.2f * (1.0f - exp(-(mx - 0.8f) / 0.2f));\n"
        "        so.oc[0].xyz = mix(r0.xyz * (y / mx), float3(y), 0.5f * saturate((mx - 1.0f) * 0.5f)); } }\n");
    free(a);
    return b;
}

// ---- payload 4 (R21): particle shading and size clamp (NFS_PARTICLES=pc|x360, default pc; live) ----
// The PC particles PS t1 (hardware shadow map, 1 texldp tap, saturate(s + 0.5)) and t2 (1 compare, a floor: PCS_PARTICLE_FLOOR, from speed.exe) are always
// replaced by one shader whose mode (m.k[0].x) picks the PC output (transcribed from the .asm) or the X360 PS 09bea960:
// 9 compares at 0 and +-0.5 texel, shadow = saturate(count / 9 + saturate(1 - 4 v)) (the far-edge fade: X360 2 y_ndc - 1
// with v = .5 - .5 y_ndc, PC bias matrix 006E5378-006E5428, decoded), colour = tex x Brightness (2, = the PC's x2) x vertex
// colour x (0.5 + 0.5 shadow), alpha included. m.k[0].y = 1: NFS_TEST_PARTICLE_TINT, magenta on the X360 pixels.
// Rain streaks (draws inside Rain::Render 0074ACC0, m.k[0].z = 1) are not X360 particles: Function_824FB2C8 draws them as ePoly
// quads (Function_824460A8: effect 0 "standardshader", technique 0, world = identity) with PS 09bf3b34 = vertex colour x
// Brightness (2 in the world pass, DAT_828fcc18) x tex on rgb, vertex alpha x tex alpha (x DiffuseColour.w, opacity map) on
// alpha: no shadow term, no x2 on alpha. With the tint rain is cyan (m.k[0].w), to tell it from the magenta smoke.
static const char particles_msl[] =
"#include <metal_stdlib>\n"
"using namespace metal;\n"
"struct SmPSAux { uint alpha_func; float alpha_ref; uint pad0, pad1; float4 bumpenv[8]; float4 bumplum[8]; };\n"
"struct MatConsts { float4 k[4]; };\n"
"struct In { float4 pos [[position]]; float4 color0 [[user(color0)]]; float4 texcoord0 [[user(texcoord0)]]; float4 texcoord1 [[user(texcoord1)]]; };\n"
"struct Out { float4 c0 [[color(0)]]; };\n"
"static bool alpha_test(uint f, float a, float ref) {\n"
"    float qa = rint(saturate(a) * 255.0f), qr = rint(saturate(ref) * 255.0f);\n"
"    switch (f) {\n"
"    case 1: return false;      case 2: return qa < qr;  case 3: return qa == qr;\n"
"    case 4: return qa <= qr;   case 5: return qa > qr;  case 6: return qa != qr;\n"
"    case 7: return qa >= qr;   default: return true;\n"
"    }\n"
"}\n"
"#if MAT_SHADOW_HW\n"
"#define SHADOW_TEX depth2d<float>\n"
"static float lit1(SHADOW_TEX t, sampler s, float2 uv, float z) { return t.sample_compare(s, uv, z); }\n"
"#else\n"
"#define SHADOW_TEX texture2d<float>\n"
"static float lit1(SHADOW_TEX t, sampler s, float2 uv, float z) { return z - t.sample(s, uv).x >= 0.0f ? 0.0f : 1.0f; }\n"
"#endif\n"
"fragment Out sm_main(In pi [[stage_in]], constant float4 *c [[buffer(0)]], constant SmPSAux &aux [[buffer(3)]],\n"
"    constant MatConsts &m [[buffer(4)]],\n"
"    texture2d<float> tex0 [[texture(0)]], sampler smp0 [[sampler(0)]],\n"
"    SHADOW_TEX tex1 [[texture(1)]], sampler smp1 [[sampler(1)]]) {\n"
"    float4 v0 = saturate(pi.color0), tex = tex0.sample(smp0, pi.texcoord0.xy);\n"
"    float2 uv = pi.texcoord1.xy / pi.texcoord1.w; float z = pi.texcoord1.z / pi.texcoord1.w;\n"
"    Out o;\n"
"    if (m.k[0].x > 0.5f) {                          // X360 09bea960\n"
"        float2 tx = 1.0f / float2(tex1.get_width(), tex1.get_height());\n"
"        float n = 0.0f;\n"
"        for (int y = -1; y <= 1; ++y) for (int x = -1; x <= 1; ++x) n += lit1(tex1, smp1, uv + 0.5f * tx * float2(x, y), z);\n"
"        float shadow = saturate(n * (1.0f / 9.0f) + saturate(1.0f - 4.0f * uv.y));\n"
"        o.c0 = tex * 2.0f * v0 * (" X360S_PARTICLE_HALF "f + " X360S_PARTICLE_HALF "f * shadow);\n"
"        if (m.k[0].y > 0.5f) o.c0 = float4(1.0f, 0.0f, 1.0f, 1.0f);   // NFS_TEST_PARTICLE_TINT\n"
"        if (m.k[0].z > 0.5f) {                      // rain streaks: X360 draws them as ePoly quads with the standard effect (PS 09bf3b34)\n"
"            o.c0 = float4(2.0f * tex.rgb * v0.rgb, tex.a * v0.a);   // no shadow; Brightness 2 on colour only\n"
"            if (m.k[0].w > 0.5f) o.c0 = float4(0.0f, 1.0f, 1.0f, 1.0f);   // NFS_TEST_PARTICLE_TINT: cyan on rain\n"
"        }\n"
"    } else {                                        // PC t1 / t2\n"
"        float s = lit1(tex1, smp1, uv, z);\n"
"#if MAT_SHADOW_HW\n"
"        float l = saturate(s + " PCS_PARTICLE_HW_BIAS "f);\n"
"#else\n"
"        float l = " PCS_PARTICLE_FLOOR "f + " PCS_PARTICLE_LIT "f * s;                  // t2: " PCS_PARTICLE_FLOOR " in shadow\n"
"#endif\n"
"        float4 r = 2.0f * tex * v0; r.rgb *= l;\n"
"        o.c0 = r;\n"
"    }\n"
"    if (aux.alpha_func != 0u && !alpha_test(aux.alpha_func, o.c0.w, aux.alpha_ref)) discard_fragment();\n"
"    return o;\n"
"}\n";

static int particles_x360 = -1, particles_tint = -1;
static unsigned particles_draws, particles_clamped, particles_quads, particles_rain_draws;
static int particles_rain;   // inside Rain::Render 0074ACC0
static float particles_maxd = 0.0f, particles_maxd_after = 0.0f;
static int particles_always(void) { return 1; }   // the mode is a per-draw constant: PC output when off
static int particles_on(void) {
    if (particles_x360 < 0) { const char *e = getenv("NFS_PARTICLES"); particles_x360 = !(e && !strcmp(e, "pc")); }
    return particles_x360;
}
void materials_set_particles(int x360) {   // the F10 menu: live (the shader reads the mode per draw, the clamp per particle)
    particles_x360 = x360 != 0;
    if (diag_on) diag_event("materials: particles now %s", particles_x360 ? "x360" : "pc");
}
static void particle_constants(const float (*vsc)[4], MatConsts *o) {
    (void)vsc;
    if (particles_tint < 0) { const char *s = getenv("NFS_TEST_PARTICLE_TINT"); particles_tint = s && *s && strcmp(s, "0"); }
    o->k[0][0] = X360_SHADER_LITS && particles_on() ? 1.0f : 0.0f;   // no X360 shader constants: the PC output
    o->k[0][1] = particles_tint ? 1.0f : 0.0f;
    o->k[0][2] = particles_rain ? 1.0f : 0.0f;
    o->k[0][3] = particles_rain && particles_tint ? 1.0f : 0.0f;
    if (particles_on()) { ++particles_draws; if (particles_rain) ++particles_rain_draws; }
}
void f_0074ACC0_orig(Cpu *c);
void f_0074ACC0(Cpu *c) {   // Rain::Render: its quads (FUN_006d1e30) are flushed by FUN_006d1f60 inside this call
    particles_rain = 1;
    f_0074ACC0_orig(c);
    particles_rain = 0;
}

// Size clamp: the X360 VS clamps a particle corner's projected distance from its centre to MaxParticleSize (NDC; X360F_PARTICLE_MAXD).
// PlatAddParticle 006C1160 builds the quad on the CPU (4 vertices x 6 floats at the cursor 0093CB1C); after it, a corner
// farther than that from the projected centre (the mean of the four corners = particle + 0x50 + the offset argument) is pulled in along its offset. The view comes from
// PlatStartParticleRender 006CEEE0 (eView +4 = the id; eViews[id].pView + 0x80 = view-projection).
#define PARTICLE_CURSOR 0x0093CB1Cu
#define PARTICLE_MAXD   X360F_PARTICLE_MAXD   // MaxParticleSize: the 06_particles VS default (generated)
static uint32_t particle_vid = 99;
void f_006CEEE0_orig(Cpu *c);
void f_006CEEE0(Cpu *c) {
    uint32_t view = M32(c->esp + 4);
    particle_vid = view ? M32(view + 4) : 99;
    f_006CEEE0_orig(c);
}
static int particle_ndc(const float *m, const float *p, float *x, float *y) {
    float cx = 0, cy = 0, cw = 0;
    for (int k = 0; k < 3; ++k) { cx += p[k] * m[4 * k]; cy += p[k] * m[4 * k + 1]; cw += p[k] * m[4 * k + 3]; }
    cx += m[12]; cy += m[13]; cw += m[15];
    if (!(cw > 1e-4f)) return 0;
    *x = cx / cw; *y = cy / cw;
    return 1;
}
void f_006C1160_orig(Cpu *c);
void f_006C1160(Cpu *c) {
    uint32_t particle = M32(c->esp + 4), before = M32(PARTICLE_CURSOR);
    f_006C1160_orig(c);
    if (!X360_SHADER_LITS || !particles_on() || !before || M32(PARTICLE_CURSOR) != before + 96 || particle_vid >= 16) return;   // no MaxParticleSize: PC size
    uint32_t pview = M32(0x009195E0u + particle_vid * 0x70u);
    if (!pview) return;
    float m[16], ctr[3], cxn, cyn;
    memcpy(m, G + pview + 0x80, 64);
    (void)particle;                                      // the quad's centre = particle + 0x50 + the offset argument: the corners' mean
    const float *q = (const float *)(G + before);
    for (int a = 0; a < 3; ++a) ctr[a] = 0.25f * (q[a] + q[6 + a] + q[12 + a] + q[18 + a]);
    ++particles_quads;
    if (!particle_ndc(m, ctr, &cxn, &cyn)) return;
    for (int v = 0; v < 4; ++v) {
        float *p = (float *)(G + before + 24 * v);
        for (int pass = 0; pass < 3; ++pass) {          // perspective makes the pull-in nonlinear: a few steps
            float x, y;
            if (!particle_ndc(m, p, &x, &y)) break;
            float d = hypotf(x - cxn, y - cyn);
            if (pass == 0) { if (d > particles_maxd) particles_maxd = d; }
            if (!(d > PARTICLE_MAXD * 1.001f)) { if (d > particles_maxd_after) particles_maxd_after = d; break; }
            if (pass == 0) ++particles_clamped;
            float k = PARTICLE_MAXD / d;
            for (int a = 0; a < 3; ++a) p[a] = ctr[a] + (p[a] - ctr[a]) * k;
        }
    }
}
typedef struct MatShader {
    const char *name;                    // for logs
    uint64_t hash;                       // FNV-1a 64 of the game shader's bytecode, up to and including the END token
    int stage;                           // SM_PIXEL / SM_VERTEX
    int variant;                         // MAT_VARIANT for the source
    uint8_t samplers[4];                 // sampler types s0..s3 the translation must reflect (SM_TEX_*; 0 = any)
    uint16_t depth_ok;                   // sampler registers that may be depth textures (s2 sets MAT_SHADOW_HW)
    const char *msl;                     // the replacement source, or
    char *(*patch)(const char *msl);     // an edit of the translation (NULL: keep it)
    int (*enabled)(void);                // the payload's switch
    void (*constants)(const float (*vsc)[4], MatConsts *out);
    int extra_tex;                       // MAT_TEX_*: render.m binds it at fragment texture(MAT_TEX_SLOT)
} MatShader;
#define CAR_SAMPLERS {SM_TEX_2D, SM_TEX_CUBE, SM_TEX_2D, SM_TEX_2D}
#define CAR(name, hash, v) {name, hash, SM_PIXEL, v, CAR_SAMPLERS, 1u << 2, car_paint_msl, NULL, car_paint_on, car_paint_constants, MAT_TEX_CARNM}
#define WR_SAMPLERS {SM_TEX_2D, SM_TEX_2D, SM_TEX_2D, SM_TEX_2D}
#define RIPPLE_PS(name, hash) {name, hash, SM_PIXEL, 0, WR_SAMPLERS, 1u << 4, NULL, ripple_ps_patch, ripples_on, ripple_constants, MAT_TEX_RIPPLES}
#define RIPPLE_VS(name, hash) {name, hash, SM_VERTEX, 0, {0, 0, 0, 0}, 0, NULL, ripple_vs_patch, ripples_on, NULL, 0}
#define PARALLAX_PS(name, hash) {name, hash, SM_PIXEL, 0, WR_SAMPLERS, 1u << 3, NULL, parallax_ps_patch, parallax_on, parallax_constants, 0}
#define PARTICLE_PS(name, hash) {name, hash, SM_PIXEL, 0, {SM_TEX_2D, SM_TEX_2D, 0, 0}, 1u << 1, particles_msl, NULL, particles_always, particle_constants, 0}
static const MatShader table[] = {
    PARTICLE_PS("particles t1 ps_2_0 (hardware shadow)", 0x97086D94D4253C51ULL),
    PARTICLE_PS("particles t2 ps_2_0 (shadow compare)", 0x847C8B08F59A807EULL),
    PARALLAX_PS("worldnormalmap t0 ps_2_x", 0x8CBD8764E8812945ULL),
    PARALLAX_PS("worldnormalmap t1 ps_2_x", 0x58C4434A7033B3BAULL),
    PARALLAX_PS("worldnormalmap t2 ps_2_x", 0xE5AF96AEE84674F6ULL),
    PARALLAX_PS("worldnormalmap t3 ps_2_x", 0xD71DE6C5AA405EAAULL),
    PARALLAX_PS("worldnormalmap t4 ps_2_x", 0xB2A0298B595A8C2CULL),
    PARALLAX_PS("worldnormalmap t5 ps_2_x", 0xB0057ADE7F4A5B3FULL),
    CAR("car t1 ps_2_0 (shadow level 1)", 0x393123DA8B3EC17FULL, 1),
    CAR("car t2 ps_2_x (shadow level 2)", 0x2CD941122737DDF5ULL, 2),
    CAR("car t3 ps_2_x (shadow level 3)", 0xE3C84E78B9847542ULL, 3),
    CAR("car t4 ps_2_x (shadow level 4)", 0x6F1A7154236357C9ULL, 4),
    CAR("car t5 ps_2_x (shadow level 5)", 0xA8F9086D6BE6DB85ULL, 5),
    // car lighting above 1 (hashes of host/shaders/testdata/car)
    {"car t0 vs_1_1 (no shadows) oD0 unclamped", 0x1BEA38904139377AULL, SM_VERTEX, 0, {0, 0, 0, 0}, 0, NULL, car_vs_patch, car_light_hdr, NULL, 0},
    {"car t1 vs_1_1 (shadow levels 1-5) oD0 unclamped + R26 texcoord6", 0xB1CE127C28045F2CULL, SM_VERTEX, 0, {0, 0, 0, 0}, 0, NULL, car_vs_nm_patch, car_vs_nm_on, NULL, 0},
    {"car t0 ps_1_1 (no shadows) light unclamped + shoulder", 0x89E9A52CCEE09BCFULL, SM_PIXEL, 0, {0, 0, 0, 0}, 0, NULL, car_t0_ps_patch, car_light_hdr, NULL, 0},
    // worldreflect (hashes of host/shaders/testdata/worldreflect, the same bytecode as speed.exe's effect)
    RIPPLE_VS("worldreflect t0 vs_1_1 (no shadows)", 0xBB253668A28870F5ULL),
    RIPPLE_VS("worldreflect t1 vs_1_1 (shadow levels 1-5)", 0x8BBB8DFEF2EDAE21ULL),
    RIPPLE_PS("worldreflect t0 ps_2_0 (no shadows)", 0xE5F37CD552B4BD5FULL),
    RIPPLE_PS("worldreflect t1 ps_2_0 (shadow level 1)", 0xD2D9B9D538A06AEEULL),
    RIPPLE_PS("worldreflect t2 ps_2_x (shadow level 2)", 0xCB861F5D715664A3ULL),
    RIPPLE_PS("worldreflect t3 ps_2_x (shadow level 3)", 0x268CB5F65C872238ULL),
    RIPPLE_PS("worldreflect t4 ps_2_x (shadow level 4)", 0xCCAACC20F85F0BA7ULL),
    RIPPLE_PS("worldreflect t5 ps_2_x (shadow level 5)", 0x242B92C6A96DE029ULL),
};

char *mat_replace(const uint32_t *tokens, uint32_t n, const SmResult *refl, int *id) {
    *id = 0;
    uint64_t h = 1469598103934665603ull;
    const uint8_t *b = (const uint8_t *)tokens;
    for (size_t i = 0; i < 4 * (size_t)n; ++i) h = (h ^ b[i]) * 1099511628211ull;
    for (int k = 0; k < (int)(sizeof table / sizeof *table); ++k) {
        const MatShader *e = &table[k];
        if (e->hash != h || e->stage != refl->shader_type || !e->enabled()) continue;
        int shadow_hw = 0, ok = 1;
        for (int s = 0; s < refl->n_samplers; ++s) {
            const SmSampler *sm = &refl->samplers[s];
            if (sm->reg < 4 && e->samplers[sm->reg] && e->samplers[sm->reg] != sm->type) ok = 0;
            if (sm->shadow && !(e->depth_ok >> sm->reg & 1)) ok = 0;
            if (sm->shadow) shadow_hw = 1;
        }
        char *src = NULL;
        if (ok && e->patch) src = e->patch(refl->msl);
        else if (ok) {
            size_t len = strlen(e->msl) + 128;
            src = malloc(len);
            snprintf(src, len, "#define MAT_VARIANT %d\n#define MAT_SHADOW_HW %d\n#define MAT_CAR_HDR %d\n%s", e->variant, shadow_hw, car_light_hdr(), e->msl);
        }
        if (!src) {
            if (diag_on) diag_event("materials: %s: %s, translated PC shader kept", e->name, ok ? "translation not as expected" : "unexpected samplers");
            return NULL;
        }
        if (diag_on) diag_event("materials: %s %s (hash %016llX, depth shadow map %d)", e->name, e->patch ? "patched" : "replaced", (unsigned long long)h, shadow_hw);
        *id = k + 1;
        return src;
    }
    return NULL;
}

void mat_constants(int id, const float (*vsc)[4], MatConsts *out) {
    memset(out, 0, sizeof *out);
    if (id > 0 && id <= (int)(sizeof table / sizeof *table) && table[id - 1].constants) table[id - 1].constants(vsc, out);
}
int mat_extra_texture(int id) { return id > 0 && id <= (int)(sizeof table / sizeof *table) ? table[id - 1].extra_tex : 0; }

// ---------------- R20: load generations and queue-owned auxiliary contexts ----------------
typedef struct { uint32_t header, instances, count, id, generation; } AuxSection;
static AuxSection aux_sections[1024];
static uint32_t aux_generation, aux_queue_generation;
static MatAuxDraw aux_source, aux_queue[4096];
MatAuxDraw mat_aux_draw;
static struct { uint32_t matched, attached, draws, lights, fallback[AUX_REASONS]; } aux_diag;
static char aux_drawn_names[20][35];
static unsigned aux_drawn_count;

void mat_aux_fallback(int reason) {
    if (diag_on && reason >= 0 && reason < AUX_REASONS) ++aux_diag.fallback[reason];
}
void materials_aux_clear(void) { memset(&mat_aux_draw, 0, sizeof mat_aux_draw); }
static uint32_t aux_header(uint32_t chunk) {
    if (!chunk || M32(chunk) != 0x80034100u) return 0;
    uint32_t end = chunk + 8 + M32(chunk+4);
    if (end < chunk || end-chunk > 0x1000000u) return 0;
    for (uint32_t p = chunk+8; p+8 <= end;) {
        uint32_t next = p+8+M32(p+4);
        if (next <= p || next > end) break;
        uint32_t h = (p+23)&~15u;
        if (M32(p) == 0x34101u && h+0x30 <= next) return h;
        p = next;
    }
    return 0;
}
void f_007311F0_orig(Cpu *c);
void f_007311F0(Cpu *c) {
    uint32_t chunk = M32(c->esp+4);
    f_007311F0_orig(c);
    uint32_t h = aux_header(chunk);
    if (!h || !c->eax) return;
    AuxSection *slot = NULL;
    for (unsigned i = 0; i < 1024; ++i) {
        if (aux_sections[i].header == h) { slot = &aux_sections[i]; break; }
        if (!aux_sections[i].header && !slot) slot = &aux_sections[i];
    }
    if (!slot) { mat_aux_fallback(AUX_SECTION); return; }
    *slot = (AuxSection){h, M32(h+0x20), M32(h+0x24), M32(h+0xc), ++aux_generation};
}
void f_007307E0_orig(Cpu *c);
void f_007307E0(Cpu *c) {
    uint32_t h = aux_header(M32(c->esp+4));
    if (h) {
        for (unsigned i = 0; i < 1024; ++i) if (aux_sections[i].header == h) memset(&aux_sections[i],0,sizeof aux_sections[i]);
        // Queued descriptors own their data; invalidate only this section's generation.
        for (unsigned i = 0; i < 4096; ++i) if (aux_queue[i].valid && aux_queue[i].section == M32(h+0xc)) aux_queue[i].valid = 0;
        if (mat_aux_draw.section == M32(h+0xc)) materials_aux_clear();
    }
    f_007307E0_orig(c);
}
void mirror_scenery_render(Cpu *c);
void f_006DA9B0(Cpu *c) {
    MatAuxDraw saved = aux_source;
    memset(&aux_source,0,sizeof aux_source); // nested/unlit submissions must shadow a lit parent
    if (auxlights_enabled() && M32(c->esp) == 0x00724203u) {
        uint32_t instance = M32(c->edi+8);
        AuxSection *s = NULL;
        for (unsigned i = 0; i < 1024; ++i) {
            AuxSection *v = &aux_sections[i];
            if (v->header && instance >= v->instances && (instance-v->instances)/64 < v->count &&
                !((instance-v->instances)%64)) { s = v; break; }
        }
        if (!s) mat_aux_fallback(AUX_SECTION);
        else {
            const AuxLightContext *ctx = auxlights_find(s->id,G+instance);
            if (!ctx) mat_aux_fallback(AUX_UNMATCHED);
            else {
                if (diag_on) ++aux_diag.matched;
                aux_source.context = *ctx; aux_source.valid = 1;
                aux_source.instance = instance; aux_source.section = s->id; aux_source.generation = s->generation;
                memcpy(aux_source.bbox,G+instance,24);
                // Offline source/PC transform parity: all 1,003 matched references
                // have identical baked flags and transform bytes (999 keys).
                // Context positions are SOURCE LOCAL, including transformed pipes.
                // Decode SceneryInstance::GetMatrix (006BEEB0), not the bbox centre.
                float original[4][4] = {{1,0,0,0},{0,1,0,0},{0,0,1,0},{0,0,0,1}};
                if (!(M32(instance+0x18)&0x200)) {
                    int16_t basis[9]; memcpy(basis,G+instance+0x2c,sizeof basis);
                    for (int r = 0; r < 3; ++r) for (int col = 0; col < 3; ++col) original[r][col] = basis[r*3+col]/8192.f;
                    memcpy(original[3],G+instance+0x20,12);
                }
                memcpy(aux_source.source_world,original,sizeof original);
            }
        }
    }
    // Leave the guest NULL light argument intact: PC 006C7420 only accepts type0.
    mirror_scenery_render(c);
    aux_source = saved;
}
void f_006E0AB0_orig(Cpu *c);
void f_006E0AB0(Cpu *c) {
    uint32_t old = M32(0x00982CDCu);
    if (!old) ++aux_queue_generation; // invalidate old slots without clearing megabytes in PC mode
    f_006E0AB0_orig(c);
    if (old < 4096 && M32(0x00982CDCu) == old+1) {
        aux_queue[old] = aux_source; // including a fresh empty descriptor for every unlit append
        aux_queue[old].queue_generation = aux_queue_generation;
        uint32_t q = 0x0093E878u+old*0x44, transform = M32(q+28);
        aux_queue[old].mesh = M32(q);
        if (transform) memcpy(aux_queue[old].world,G+transform,64);
        if (diag_on && aux_source.valid) ++aux_diag.attached;
    }
}
void materials_aux_record(uint32_t q) {
    materials_aux_clear();
    if (!auxlights_enabled() || q < 0x0093E878u || (q-0x0093E878u)%0x44) return;
    uint32_t index = (q-0x0093E878u)/0x44;
    if (index >= 4096 || index >= M32(0x00982CDCu) || !aux_queue[index].valid) return;
    MatAuxDraw *d = &aux_queue[index];
    if (d->queue_generation != aux_queue_generation) { mat_aux_fallback(AUX_GENERATION); return; }
    int loaded = 0;
    for (unsigned i = 0; i < 1024; ++i) loaded |= aux_sections[i].header && aux_sections[i].generation == d->generation;
    if (!loaded) { mat_aux_fallback(AUX_GENERATION); return; }
    uint32_t view = M32(CUR_VIEW), fx = M32(q+16), transform = M32(q+28), mesh = M32(q);
    // Main colour only. Mirror fog may temporarily select main effects, but retains view id3.
    if (!view || M32(view) || M32(view+4) != 1) { mat_aux_fallback(AUX_VIEW); return; }
    if (M32(q+4) || !fx || (M32(fx+4) != 1 && M32(fx+4) != 3) || !mesh) { mat_aux_fallback(AUX_EFFECT); return; }
    uint32_t material_name = M32(mesh+0x60);
    if (material_name && (!memcmp(G+material_name,"grasstype",9) || !memcmp(G+material_name,"glossywindow",11))) { mat_aux_fallback(AUX_EFFECT); return; }
    if (!transform) { mat_aux_fallback(AUX_SPACE); return; }
    if (d->mesh != mesh || memcmp(d->world,G+transform,64)) { mat_aux_fallback(AUX_GENERATION); return; }
    mat_aux_draw = *d;
    mat_aux_draw.mesh = mesh; mat_aux_draw.effect = M32(fx+4);
    memcpy(mat_aux_draw.world,G+transform,64);
    float bounds[6]; memcpy(bounds,G+mesh,24);
    AuxLightContext world_context = d->context;
    // Source local -> world -> queued local. Validate this space relationship
    // even when both instance matrices coincide, then preserve original light
    // bits in that common case instead of a lossy round trip through world XYZ.
    for (int i = 0; i < d->context.count; ++i) for (int col = 0; col < 3; ++col) {
        float v = d->source_world[3][col];
        for (int r = 0; r < 3; ++r) v += d->context.lights[i].position[r]*d->source_world[r][col];
        world_context.lights[i].position[col] = v;
    }
    mat_aux_draw.space = auxlights_local(&world_context,mat_aux_draw.world,bounds,d->bbox,&mat_aux_draw.context);
    if (!mat_aux_draw.space) { mat_aux_fallback(AUX_SPACE); materials_aux_clear(); return; }
    if (!memcmp(d->source_world,mat_aux_draw.world,64)) mat_aux_draw.context = d->context;
    for (int i = 0; i < 5; ++i) mat_aux_draw.textures[i] = M32(q+36+i*4);
}
void f_006D7660_orig(Cpu *c);
void materials_glass_clear(void);
void f_006D7660(Cpu *c) {
    materials_aux_record(c->ecx);
    f_006D7660_orig(c);
    materials_aux_clear();
    materials_glass_clear();              // R19: a record that skipped its draw must not mark the next unrelated draw
    materials_trees_clear();              // R28, same reason
}
void mat_aux_emit(void) {
    mat_aux_draw.emitted = 1;
    if (!diag_on) return;
    ++aux_diag.draws; aux_diag.lights += mat_aux_draw.context.count < 6 ? mat_aux_draw.context.count : 6;
    for (unsigned i = 0; i < aux_drawn_count; ++i) if (!strcmp(aux_drawn_names[i],mat_aux_draw.context.name)) return;
    if (aux_drawn_count == 20) return;
    strcpy(aux_drawn_names[aux_drawn_count++],mat_aux_draw.context.name);
    diag_event("auxlights: first_draw context=%s section=%u generation=%u instance=%08X mesh=%08X effect=%u source/uploaded/evaluated=%u/%u/%u space=%s bbox=%.3f,%.3f,%.3f/%.3f,%.3f,%.3f textures=%08X/%08X/%08X names=%.24s/%.24s/%.24s local_light0=%.3f,%.3f,%.3f extra=1",
        mat_aux_draw.context.name,mat_aux_draw.section,mat_aux_draw.generation,mat_aux_draw.instance,mat_aux_draw.mesh,mat_aux_draw.effect,
        mat_aux_draw.context.source_count,mat_aux_draw.context.count,mat_aux_draw.context.count < 6 ? mat_aux_draw.context.count : 6,
        mat_aux_draw.space == 1 ? "world-baked" : "inverse-instance",mat_aux_draw.bbox[0],mat_aux_draw.bbox[1],mat_aux_draw.bbox[2],
        mat_aux_draw.bbox[3],mat_aux_draw.bbox[4],mat_aux_draw.bbox[5],mat_aux_draw.textures[0],mat_aux_draw.textures[1],mat_aux_draw.textures[3],
        G+mat_aux_draw.textures[0]+0xc,G+mat_aux_draw.textures[1]+0xc,G+mat_aux_draw.textures[3]+0xc,
        mat_aux_draw.context.lights[0].position[0],mat_aux_draw.context.lights[0].position[1],mat_aux_draw.context.lights[0].position[2]);
}
void mat_aux_frame(void) {
    static uint64_t last;
    if (!diag_on) return;
    uint64_t now = host_ticks(); if (last && now-last < 1000000000ull) return;
    static const char *names[AUX_REASONS] = {"unmatched","section","generation","view","effect","space","texture","input","constants","state","shader","pipeline"};
    char skips[512] = ""; size_t at = 0;
    for (int i = 0; i < AUX_REASONS; ++i) at += snprintf(skips+at,sizeof skips-at,"%s%s=%u",i ? "," : "",names[i],aux_diag.fallback[i]);
    diag_event("auxlights: mode=%s matched=%u attached=%u draws=%u lights_evaluated=%u fallbacks=%s",
        auxlights_enabled() ? "x360" : "pc",aux_diag.matched,aux_diag.attached,aux_diag.draws,aux_diag.lights,skips);
    memset(&aux_diag,0,sizeof aux_diag); last = now;
}

// ---------------- R27 grass: scoped identity and retail data ----------------
// X360 824513F0 binds queue slot4 to OPACITYMAP and substitutes WHITE32X32
// when slot4 == slot0. 82450050 selects GrassTransition only for non-default opacity.
MatGrassDraw mat_grass_draw;
static struct {
    unsigned seen, fx0, fx3, transition, candidates[2], draws[2], shells[2], applied, skips[GRASS_SKIP_COUNT];
    unsigned mirror_draws, mirror_shells;
    uint32_t mesh, view;
    float nearest, sim, bounds[6], params[64][4];
} grass_diag;
void mat_grass_diag_emit(int n, const float k[64][4]) {
    mat_grass_draw.emitted += n;
    if (!diag_on) return;
    int near = n > 1 ? 0 : 1;
    ++grass_diag.draws[near]; grass_diag.shells[near] += n; ++grass_diag.applied;
    if (mat_grass_draw.view == 3) { ++grass_diag.mirror_draws; grass_diag.mirror_shells += n; }
    memcpy(grass_diag.params, k, sizeof grass_diag.params);
}
void mat_grass_diag_frame(void) {
    if (!diag_on) return;
    static uint64_t last;
    static float race0 = -1;
    uint64_t now = host_ticks();
    float sim = F32(0x009885D8u);
    if (M32(0x00925E90u) != 6) race0 = -1;
    else if (race0 < 0) race0 = sim;
    if (!last) { last = now; return; }
    if (now - last < 1000000000ull) return;
    static const char *const reasons[] = {"no_geometry", "pc", "view", "effect", "transform", "state", "diffuse", "assets", "constants", "shadow", "shader", "pipeline", "depth", "transition_maps", "tangent"};
    char skipped[512] = "none"; size_t used = 0;
    for (int i = 0; i < GRASS_SKIP_COUNT; ++i) if (grass_diag.skips[i])
        used += snprintf(skipped + used, sizeof skipped - used, "%s%s=%u", used ? "," : "", reasons[i], grass_diag.skips[i]);
    const float (*p)[4] = grass_diag.params;
    diag_event("grass: dt %.2f sim %.3f race_s %.3f mode %s grasstype1=A79251B8 seen %u (fx0 %u fx3 %u transition %u) candidates near/far %u/%u draws near/far %u/%u shells near/far %u/%u values_applied %u mirror draws/shells %u/%u [colour %.3g/%.3g/%.3g/%.3g height %.3g N %.0f LOD %.3g diffuse_space %.3g noise %.3g/%.3g low %.3g scruff %.3g curl %.3g bias %.3g/%.3g] skipped %s closest mesh %08X view %u race_s %.3f distance %.2f bbox %.2f,%.2f,%.2f/%.2f,%.2f,%.2f",
        (now-last)*1e-9, sim, race0 < 0 ? -1 : sim-race0, mat_grass_enabled() ? "x360" : "pc",
        grass_diag.seen, grass_diag.fx0, grass_diag.fx3, grass_diag.transition, grass_diag.candidates[0], grass_diag.candidates[1], grass_diag.draws[0], grass_diag.draws[1], grass_diag.shells[0], grass_diag.shells[1], grass_diag.applied,
        grass_diag.mirror_draws, grass_diag.mirror_shells,
        p[32][0],p[32][1],p[32][2],p[32][3],p[21][0],p[20][0],p[27][0],p[22][0],p[23][0],p[24][0],p[36][0],p[25][0],p[26][0],p[34][0],p[35][0], skipped,
        grass_diag.mesh, grass_diag.view, grass_diag.mesh && race0 >= 0 ? grass_diag.sim-race0 : -1, grass_diag.mesh ? grass_diag.nearest : -1,
        grass_diag.bounds[0],grass_diag.bounds[1],grass_diag.bounds[2],grass_diag.bounds[3],grass_diag.bounds[4],grass_diag.bounds[5]);
    memset(&grass_diag, 0, sizeof grass_diag); last = now;
}
static int grass_mode = -1;
int mat_grass_enabled(void) {
    if (!X360_SHADER_LITS) return 0;
    if (grass_mode < 0) { const char *s = getenv("NFS_GRASS"); grass_mode = !(s && !strcmp(s, "pc")); }                                       
    int postfx_look_variant(const char *v);
    if (postfx_look_variant("grasspc")) return 0;
    if (postfx_look_variant("grassx360")) return 1;
    return grass_mode;
}
void materials_set_grass(int x360) {
    grass_mode = x360 != 0;
    if (diag_on) diag_event("materials: grass now %s", grass_mode ? "x360" : "pc");
}
void materials_grass_clear(void) {
    if (diag_on && mat_grass_draw.mesh && !mat_grass_draw.emitted) ++grass_diag.skips[mat_grass_draw.skip];
    memset(&mat_grass_draw, 0, sizeof mat_grass_draw);
    memset(&mat_water_draw, 0, sizeof mat_water_draw);
    void materials_glass_clear(void);
    materials_glass_clear();             // R19
    materials_trees_clear();             // R28
}
void materials_grass_record(uint32_t q) {
    // 006E0200's inline sorted draw: ESI = 0093E878 + sorted_index * 0x44.
    // Retire even a record that skipped its D3D draw before inspecting the next.
    materials_grass_clear();
    if (!diag_on && !mat_grass_enabled() && !mat_water_enabled()) return;
    uint32_t mesh = q ? M32(q) : 0, view = M32(CUR_VIEW), effect = q ? M32(q + 16) : 0;
    uint32_t name = mesh && !M32(q + 4) ? M32(mesh + 0x60) : 0;
    // ANM_WATERA is shared by five generic-material trailer entries. Exact
    // material identity limits R18 to the six PC watersea meshes (effect world).
    if (name && !memcmp(G + name, "watersea", 9) && effect && !M32(effect + 4) &&
        view && M32(view + 4) == 1 && !M32(view) && M32(q + 28)) {
        mat_water_draw.mesh = mesh; mat_water_draw.view = 1;
        memcpy(mat_water_draw.world, G + M32(q + 28), sizeof mat_water_draw.world);
    }
    if (name && !memcmp(G + name, "grasstype1", 11)) {
        mat_grass_draw.mesh = mesh;
        mat_grass_draw.skip = GRASS_VIEW;
        if (diag_on) ++grass_diag.seen;
        // X360 82450590 has no view filter (82450218 only reduces shells for distance/debug/shadow).
        // Keep other offscreen views out; mirror RT entry 3 uses its own view id/matrices below, like main RT 0.
        if (view && (!M32(view) || (M32(view) == 3 && M32(view + 4) == 3)) && effect) {
            uint32_t fx = M32(effect + 4), transform = M32(q + 28), vid = M32(view + 4);
            mat_grass_draw.effect = fx;
            mat_grass_draw.skip = GRASS_EFFECT;
            if (diag_on) { grass_diag.fx0 += fx == 0; grass_diag.fx3 += fx == 3; }
            if (fx == 0 || fx == 3) {
                mat_grass_draw.skip = GRASS_TRANSFORM;
                if (transform && vid < 16) {
                    mat_grass_draw.mesh = mesh; mat_grass_draw.view = vid; mat_grass_draw.effect = fx;
                    memcpy(mat_grass_draw.bounds, G + mesh, 24);
                    memcpy(mat_grass_draw.world, G + transform, 64);
                    // eViews[vid].pView: projection +0, view +40, view-projection +80
                    // (006C8000); fog needs world * view, not world * projection.
                    uint32_t v = M32(0x009195E0u + vid * 0x70u);
                    if (v) for (int col = 0; col < 3; ++col) for (int row = 0; row < 4; ++row) {
                        float sum = 0;
                        for (int k = 0; k < 4; ++k) sum += mat_grass_draw.world[row][k] * F32(v + 0x40 + 16 * k + 4 * col);
                        mat_grass_draw.world_view[col][row] = sum;
                    }
                    for (int i = 0; i < 5; ++i) mat_grass_draw.textures[i] = M32(q + 36 + i * 4);
                    uint32_t opacity = mat_grass_draw.textures[4];
                    mat_grass_draw.transition = opacity && opacity != mat_grass_draw.textures[0] && M32(opacity + 0x24) != 0xE2C7C5E2u; // WHITE32X32
                    if (diag_on) grass_diag.transition += mat_grass_draw.transition;
                    mat_grass_draw.skip = v ? GRASS_NO_GEOMETRY : GRASS_TRANSFORM;
                }
            }
        }
    }
}
void f_006E0200_orig(Cpu *c);
void f_006E0200(Cpu *c) {
    materials_grass_clear();
    materials_aux_clear();
    f_006E0200_orig(c);
    // Missing geometry / zero passes can bypass render_draw, including on the
    // last record. Clear on every exit, including the empty-queue early return.
    materials_grass_clear();
    materials_aux_clear();
    // Other PC append routines (e.g. particle strips) bypass 006E0AB0. Retire
    // the whole host queue when the guest flush retires its generation.
    if (!M32(0x00982CDCu)) ++aux_queue_generation;
}
uint32_t mat_grass_texture(int slot) {
    uint32_t ti = mat_grass_draw.textures[slot], plat = ti ? M32(ti) : 0;
    return plat ? M32(plat + 0x18) : 0;
}

static uint8_t *grass_noise_rgba, *grass_volume_rgba;
static uint32_t grass_noise_w, grass_noise_h;
static int grass_assets_tried;
static uint32_t grass_u32(const uint8_t *p) { uint32_t x; memcpy(&x, p, 4); return x; }
static void grass_assets(void) {
    if (grass_assets_tried) return;
    grass_assets_tried = 1;
    uint32_t hash = 0xFFFFFFFFu;
    for (const char *s = "2DNOISE"; *s; ++s) hash = hash * 33u + (uint8_t)*s;
    grass_noise_rgba = x360_texture_rgba("GLOBAL/GLOBALB.BUN", hash, &grass_noise_w, &grass_noise_h);
    const char *s = getenv("NFS_X360_EXTRA_TEXTURES");
    char path[1400];
    FILE *f = NULL;
    if (!(s && (!strcmp(s, "off") || !strcmp(s, "0")))) {
        if (s && strcmp(s, "on") && strcmp(s, "1")) snprintf(path, sizeof path, "%s/GLOBAL/GLOBALB.BUN", s);
        else if (roots_active()) snprintf(path, sizeof path, "%s/GLOBAL/GLOBALB.BUN", roots_get("x360_root") ? roots_get("x360_root") : ROOTS_NONE);
        else snprintf(path, sizeof path, "%s/../x360/files/GLOBAL/GLOBALB.BUN", game_dir);
        f = fopen(path, "rb");
    }
    uint8_t header[136];
    // First 30300200 chunk, DDS at +8. Its pitch is 0 and DDSD_PITCH is absent:
    // tight ARGB rows (not tiled), 32^3 through 1^3, exactly 149796 pixel bytes.
    int ok = f && !fseek(f, 0xF44000, SEEK_SET) && fread(header, 1, sizeof header, f) == sizeof header &&
        grass_u32(header) == 0x30300200u && grass_u32(header + 4) == 149924 && !memcmp(header + 8, "DDS ", 4) &&
        grass_u32(header + 12) == 124 && grass_u32(header + 20) == 32 && grass_u32(header + 24) == 32 &&
        grass_u32(header + 32) == 32 && grass_u32(header + 36) == 6 && grass_u32(header + 28) == 0 &&
        !(grass_u32(header + 16) & 8u) && grass_u32(header + 84) == 32 && grass_u32(header + 88) == 65 &&
        grass_u32(header + 92) == 0 && grass_u32(header + 96) == 32 &&
        grass_u32(header + 100) == 0x00FF0000u && grass_u32(header + 104) == 0x0000FF00u &&
        grass_u32(header + 108) == 0x000000FFu && grass_u32(header + 112) == 0xFF000000u;
    if (ok) {
        grass_volume_rgba = malloc(149796);
        ok = grass_volume_rgba && fread(grass_volume_rgba, 1, 149796, f) == 149796;
        if (ok) for (uint32_t i = 0; i < 149796; i += 4) {
            uint8_t b = grass_volume_rgba[i]; grass_volume_rgba[i] = grass_volume_rgba[i + 2]; grass_volume_rgba[i + 2] = b;
        }
    }
    if (f) fclose(f);
    if (!ok || !grass_noise_rgba || grass_noise_w != 512 || grass_noise_h != 512) {
        free(grass_volume_rgba); grass_volume_rgba = NULL;
        free(grass_noise_rgba); grass_noise_rgba = NULL;
    }
}
const uint8_t *mat_grass_noise(uint32_t *w, uint32_t *h) {
    grass_assets(); *w = grass_noise_w; *h = grass_noise_h; return grass_noise_rgba;
}
const uint8_t *mat_grass_volume(uint32_t *size) { grass_assets(); *size = 149796; return grass_volume_rgba; }

static int grass_reg(const SmResult *r, const char *name, int count) {
    for (int i = 0; i < r->n_constants; ++i) if (r->constants[i].set == 2 && r->constants[i].count >= count &&
        !strcmp(r->constants[i].name, name)) return r->constants[i].reg;
    return -1;
}
int mat_grass_constants(const SmResult *pcvs, const SmResult *pcps, const float (*vsc)[4], const float (*psc)[4], float out[64][4]) {
    const MatGrassParams *p = attrib_x360_grass(0xA79251B8u);
    if (!p || !mat_grass_draw.mesh) return 0;
    memset(out, 0, 64 * 16);
    static const struct { const char *name; int dst, count; } copy[] = {
        {"WorldViewProj", 0, 4}, {"Fog_Br_Plus_Bm", 5, 1}, {"Fog_One_Over_BrBm", 6, 1},
        {"Fog_Const_1", 7, 1}, {"Fog_Const_2", 8, 1}, {"Fog_Const_3", 9, 1},
        {"FogValue", 11, 1}, {"LocalEyePos", 12, 1}, {"LocalLightVec", 18, 1},
        {"DiffuseColour", 19, 1}, {"AmbientColour", 57, 1}
    };
    for (size_t i = 0; i < sizeof copy / sizeof *copy; ++i) {
        int reg = grass_reg(pcvs, copy[i].name, copy[i].count);
        const float (*bank)[4] = vsc;
        if (reg < 0 && !strcmp(copy[i].name, "DiffuseColour")) { reg = grass_reg(pcps, "DiffuseColour", 1); bank = psc; }
        if (reg < 0 || reg + copy[i].count > 256) return 0;
        memcpy(out[copy[i].dst], bank[reg], 16 * copy[i].count);
    }
    memcpy(out[58], out[18], 16);
    if (mat_grass_draw.transition) {
        int spec = grass_reg(pcps, "SpecularColour", 1), power = grass_reg(pcps, "SpecularPower", 1);
        if (spec < 0 || power < 0) return 0;
        memcpy(out[59], psc[spec], 16); memcpy(out[60], psc[power], 16);
        uint32_t normal = mat_grass_draw.textures[1];
        out[61][0] = normal && G[normal + 0x55] != 0; // 824513F0: IS_PARALLEX_MAPPED
    }
    out[10][0] = X360F_KMAXDIST_GRASS;
    memcpy(out[13], mat_grass_draw.world_view, 48);
    for (int col = 0; col < 2; ++col) for (int row = 0; row < 4; ++row) out[16 + col][row] = mat_grass_draw.world[row][col];
    int shadow = grass_reg(pcvs, "ShadowTransform", 4);
    // PC ShadowTransform already maps clip xy to texture xy. The X360 bodies expect
    // clip xy; undo the bias so their own projection maps back to the PC shadow UV.
    if (shadow >= 0) {
        memcpy(out[28], vsc[shadow], 64);
        for (int k = 0; k < 4; ++k) {
            out[28][k] = 2 * vsc[shadow][k] - vsc[shadow + 3][k];
            out[29][k] = vsc[shadow + 3][k] - 2 * vsc[shadow + 1][k];
        }
        out[38][0] = 1;
    }
    float d2 = 0, r2 = 0;
    for (int k = 0; k < 3; ++k) {
        float lo = mat_grass_draw.bounds[k], hi = mat_grass_draw.bounds[k + 3];
        if (!isfinite(lo) || !isfinite(hi) || lo > hi) return 0;
        float half = (hi - lo) * .5f, d = (lo + hi) * .5f - out[12][k];
        d2 += d * d; r2 += half * half;
    }
    int n = sqrtf(d2) - sqrtf(r2) > p->lod_start ? 1 : (int)p->shells;
    float distance = sqrtf(d2) - sqrtf(r2);
    if (diag_on) ++grass_diag.candidates[n > 1 ? 0 : 1];
    if (diag_on && (!grass_diag.mesh || distance < grass_diag.nearest)) {
        grass_diag.mesh = mat_grass_draw.mesh; grass_diag.view = mat_grass_draw.view;
        grass_diag.sim = F32(0x009885D8u);
        grass_diag.nearest = distance; memcpy(grass_diag.bounds, mat_grass_draw.bounds, 24);
    }
    out[20][0] = n; out[21][0] = p->height; out[22][0] = p->diffuse_space;
    out[23][0] = p->noise_space; out[24][0] = p->low_noise_space; out[25][0] = p->scruff;
    out[26][0] = p->curl; out[27][0] = p->lod_start;
    memcpy(out[32], p->colour, 16); out[33][0] = n;
    out[34][0] = p->diffuse_bias; out[35][0] = p->noise_bias; out[36][0] = p->low_noise_intensity;
    for (int i = 0; i < 6; ++i) {
        out[39 + i][0] = p->ambient_shell[i]; out[45 + i][0] = p->ambient_noise[i]; out[51 + i][0] = p->diffuse_shell[i];
    }
    return n;
}

// ---------------- R27 ordinary Grass MSL (bodies translated at build time: gen/x360_shader_body.inc) ----------------
// VS 09BCBDA4/09BCC710/09BCD0B8/09BCDA60/09BCE418. PS 09BD294C and
// 09BD31E8, with the verified literal-only specialization for Shell2/3/4.
// Side constants: vertex buffer20 / fragment buffer5, explicit varyings grass_t0..6,
// grass_c0..3; diffuse 2D/volume 3D/noise 2D/shadow at textures0/1/2/3.
// PC normals are already converted by the MTLVertexDescriptor: no Xenos decoder.
static const char grass_common_msl[] =
"#include <metal_stdlib>\n"
"using namespace metal;\n"
"struct GrassConstants { float4 k[64]; };\n"
"struct GrassOut { float4 pos [[position]];\n"
"float4 t0 [[user(grass_t0)]];\n"
"float4 t1 [[user(grass_t1)]];\n"
"float4 t2 [[user(grass_t2)]];\n"
"float4 t3 [[user(grass_t3)]];\n"
"float4 t4 [[user(grass_t4)]];\n"
"float4 t5 [[user(grass_t5)]];\n"
"float4 t6 [[user(grass_t6)]];\n"
"float4 c0 [[user(grass_c0)]];\n"
"float4 c1 [[user(grass_c1)]];\n"
"float4 c2 [[user(grass_c2)]];\n"
"float4 c3 [[user(grass_c3)]];\n"
"};\n"
;
static const char grass_vs_head[] =
"struct GrassAux { float4 fixup; float point_size; float3 pad; };\n"
;
static const char grass_vs_pre[] =
"float4 oPos = float4(0);\n"
"float4 oTexCoord0 = float4(0);\n"
"float4 oTexCoord1 = float4(0);\n"
"float4 oTexCoord2 = float4(0);\n"
"float4 oTexCoord3 = float4(0);\n"
"float4 oTexCoord4 = float4(0);\n"
"float4 oTexCoord5 = float4(0);\n"
"float4 oTexCoord6 = float4(0);\n"
"float4 oColor0 = float4(0);\n"
"float4 oColor1 = float4(0);\n"
"float4 oColor2 = float4(0);\n"
"float4 oColor3 = float4(0);\n"
;
static const char grass_vs_end[] =
"GrassOut o; o.pos=oPos; o.pos.xy += aux.fixup.xy * oPos.w;\n"
"o.t0 = oTexCoord0;\n"
"o.t1 = oTexCoord1;\n"
"o.t2 = oTexCoord2;\n"
"o.t3 = oTexCoord3;\n"
"o.t4 = oTexCoord4;\n"
"o.t5 = oTexCoord5;\n"
"o.t6 = oTexCoord6;\n"
"o.c0 = oColor0;\n"
"o.c1 = oColor1;\n"
"o.c2 = oColor2;\n"
"o.c3 = oColor3;\n"
"return o;\n"
"}\n"
;
static const char grass_vs_body0[] =
"const float4 c255 = " X360L_09BCBDA4_C255 ";\n"
X360B_GRASS_VS_BODY0
;
static const char grass_vs_body1[] =
"const float4 c255 = " X360L_09BCC710_C255 ";\n"
X360B_GRASS_VS_BODY1
;
static const char grass_vs_body2[] =
"const float4 c255 = " X360L_09BCD0B8_C255 ";\n"
X360B_GRASS_VS_BODY2
;
static const char grass_vs_body3[] =
"const float4 c254 = " X360L_09BCDA60_C254 ";\n"
"const float4 c255 = " X360L_09BCDA60_C255 ";\n"
X360B_GRASS_VS_BODY3
;
static const char grass_vs_body4[] =
"const float4 c254 = " X360L_09BCE418_C254 ";\n"
"const float4 c255 = " X360L_09BCE418_C255 ";\n"
X360B_GRASS_VS_BODY4
;
static const char grass_ps_head[] =
"constexpr sampler diffuse_s(filter::linear, mip_filter::linear, address::repeat, max_anisotropy(8));\n"
"constexpr sampler noise_s(filter::linear, mip_filter::linear, address::repeat);\n"
"constexpr sampler shadow_s(filter::linear, address::clamp_to_edge);\n"
"static float4 grass_shadow(GrassShadow t, float2 uv, float2 offset) {\n"
"    return float4(t.sample(shadow_s, uv + offset / float2(t.get_width(), t.get_height())));\n"
"}\n"
"fragment float4 sm_main(GrassOut i [[stage_in]], constant GrassConstants &g [[buffer(5)]],\n"
" texture2d<float> td [[texture(0)]], texture3d<float> tv [[texture(1)]],\n"
" texture2d<float> tn [[texture(2)]], GrassShadow ts [[texture(3)]],\n"
" texture2d<float> tm [[texture(4)]], texture2d<float> tw [[texture(5)]], texture2d<float> to [[texture(6)]]) {\n"
"float4 iTexCoord0 = i.t0;\n"
"float4 iTexCoord1 = i.t1;\n"
"float4 iTexCoord2 = i.t2;\n"
"float4 iTexCoord3 = i.t3;\n"
"float4 iTexCoord4 = i.t4;\n"
"float4 iTexCoord5 = i.t5;\n"
"float4 iTexCoord6 = i.t6;\n"
"float4 iColor0 = i.c0;\n"
"float4 iColor1 = i.c1;\n"
"float4 iColor2 = i.c2;\n"
"float4 iColor3 = i.c3;\n"
"float4 oC0 = float4(0);\n"
;
static const char grass_ps_end[] =
"// Probe replaces colour only after the production samples, per-vertex LOD and discard.\n"
"if (g.k[63].y != 0 && g.k[63].x > 0) oC0.rgb = float3(1,0,1);\n"
"return oC0;\n"
"}\n"
;
static const char grass_ps_body0[] =
"const float4 c254 = " X360L_09BD294C_C254 ";\n"
"const float4 c255 = " X360L_09BD294C_C255 ";\n"
X360B_GRASS_PS_BODY0
;
static const char grass_ps_body1[] =
"const float4 c254 = " X360L_09BD31E8_C254 ";\n"
"const float4 c255 = " X360L_09BD31E8_C255 ";\n"
X360B_GRASS_PS_BODY1
;

// GrassTransition: retail opacity comparison, all five VS/PS passes.
static const char grass_transition_vs_body0[] =
"const float4 c255 = " X360L_09BCEDD0_C255 ";\n"
X360B_GRASS_TRANSITION_VS_BODY0
;
static const char grass_transition_vs_body1[] =
"const float4 c255 = " X360L_09BCF7C4_C255 ";\n"
X360B_GRASS_TRANSITION_VS_BODY1
;
static const char grass_transition_vs_body2[] =
"const float4 c255 = " X360L_09BD020C_C255 ";\n"
X360B_GRASS_TRANSITION_VS_BODY2
;
static const char grass_transition_vs_body3[] =
"const float4 c254 = " X360L_09BD0C54_C254 ";\n"
"const float4 c255 = " X360L_09BD0C54_C255 ";\n"
X360B_GRASS_TRANSITION_VS_BODY3
;
static const char grass_transition_vs_body4[] =
"const float4 c254 = " X360L_09BD16AC_C254 ";\n"
"const float4 c255 = " X360L_09BD16AC_C255 ";\n"
X360B_GRASS_TRANSITION_VS_BODY4
;
static const char grass_transition_ps_body0[] =
"const float4 c253 = " X360L_09BD5428_C253 ";\n"
"const float4 c254 = " X360L_09BD5428_C254 ";\n"
"const float4 c255 = " X360L_09BD5428_C255 ";\n"
X360B_GRASS_TRANSITION_PS_BODY0
;
static const char grass_transition_ps_body1[] =
"const float4 c254 = " X360L_09BD6274_C254 ";\n"
"const float4 c255 = " X360L_09BD6274_C255 ";\n"
X360B_GRASS_TRANSITION_PS_BODY1
;
static const char grass_transition_ps_body2[] =
"const float4 c254 = " X360L_09BD6BD4_C254 ";\n"
"const float4 c255 = " X360L_09BD6BD4_C255 ";\n"
X360B_GRASS_TRANSITION_PS_BODY2
;
static const char grass_transition_ps_body3[] =
"const float4 c254 = " X360L_09BD7534_C254 ";\n"
"const float4 c255 = " X360L_09BD7534_C255 ";\n"
X360B_GRASS_TRANSITION_PS_BODY3
;
static const char grass_transition_ps_body4[] =
"const float4 c254 = " X360L_09BD7E94_C254 ";\n"
"const float4 c255 = " X360L_09BD7E94_C255 ";\n"
X360B_GRASS_TRANSITION_PS_BODY4
;

int mat_grass_shadow_slot(const SmResult *pc) {
    for (int i = 0; i < pc->n_constants; ++i) if (pc->constants[i].set == 3 && !strcmp(pc->constants[i].name, "shadow_sampler")) return pc->constants[i].reg;
    return -1;
}
char *mat_grass_shader(const SmResult *pc, int stage, int shell, uint16_t input_int, uint16_t input_uint) {
    if (shell < 0 || shell > 9) return NULL;
    int transition = shell >= 5; shell %= 5;
    char *result = NULL;
    if (stage == SM_VERTEX) {
        int reg[5] = {-1, -1, -1, -1, -1};
        const int usage[5] = {SM_USAGE_POSITION, SM_USAGE_NORMAL, SM_USAGE_COLOR, SM_USAGE_TEXCOORD, SM_USAGE_TANGENT};
        char attr[2048] = "struct GrassAttr {\n";
        size_t at = strlen(attr);
        for (int k = 0; k < pc->n_inputs; ++k) {
            const SmInput *in = &pc->inputs[k];
            for (int i = 0; i < 5; ++i) if (in->usage == usage[i] && !in->usage_index) reg[i] = in->reg;
            const char *ty = (input_int & (1u << in->reg)) ? "int4" : (input_uint & (1u << in->reg)) ? "uint4" : "float4";
            at += snprintf(attr + at, sizeof attr - at, "%s a%d [[attribute(%d)]];\n", ty, in->reg, in->reg);
        }
        for (int i = 0; i < (transition ? 5 : 4); ++i) if (reg[i] < 0) return NULL;
        snprintf(attr + at, sizeof attr - at, "};\n");
        char entry[1024];
        snprintf(entry, sizeof entry, "vertex GrassOut sm_main(GrassAttr i [[stage_in]], constant GrassConstants &g [[buffer(20)]], constant GrassAux &aux [[buffer(19)]]) {\n"
            "float4 iPosition0=float4(i.a%d), iNormal0=float4(i.a%d), iColor0=float4(i.a%d), iTexCoord0=float4(i.a%d);\nfloat4 iTangent0=float4(i.a%d);\n", reg[0],reg[1],reg[2],reg[3],transition ? reg[4] : reg[1]);
        static const char *const bodies[] = {grass_vs_body0,grass_vs_body1,grass_vs_body2,grass_vs_body3,grass_vs_body4};
        static const char *const tbodies[] = {grass_transition_vs_body0,grass_transition_vs_body1,grass_transition_vs_body2,grass_transition_vs_body3,grass_transition_vs_body4};
        asprintf(&result, "%s%s%s%s%s%s%s", grass_common_msl, grass_vs_head, attr, entry, grass_vs_pre, transition ? tbodies[shell] : bodies[shell], grass_vs_end);
    } else {
        int shadow_depth = 0;
        int ss = mat_grass_shadow_slot(pc);
        for (int i = 0; i < pc->n_samplers; ++i) if (pc->samplers[i].reg == ss) shadow_depth = pc->samplers[i].shadow;
        static const char *const tbodies[] = {grass_transition_ps_body0,grass_transition_ps_body1,grass_transition_ps_body2,grass_transition_ps_body3,grass_transition_ps_body4};
        asprintf(&result, "%s#define GrassShadow %s\n%s%s%s", grass_common_msl,
            shadow_depth ? "depth2d<float>" : "texture2d<float>", grass_ps_head, transition ? tbodies[shell] : shell ? grass_ps_body1 : grass_ps_body0, grass_ps_end);
    }
    return result;
}

// ---- R18 watersea (default PC) ----
// X360 13_watersea/09bfc724 VS and 09bfd3e0 PS, translated at build time (gen/x360_shader_body.inc, XenosRecomp MIT),
// same conversion/binding pattern as R27.
// Retail wave amplitudes are zero. Keep the projective normal perturbation,
// Fresnel term, pow(R.V, c254.z), diffuse x c254.y + c255.rgb and fog exactly (09bfd3e0's literals).
MatWaterDraw mat_water_draw;
static int water_mode = -1;
static unsigned water_this_frame, water_previous_frame;
static unsigned water_draws, water_variants, water_forced, water_shader_fail, water_pipeline_fail;
int mat_water_enabled(void) {
    if (!X360_SHADER_LITS) return 0;
    if (water_mode < 0) { const char *s = getenv("NFS_WATER"); water_mode = !(s && !strcmp(s, "pc")); }
    return water_mode;
}
void materials_set_water(int x360) {
    void graphics_reset_request(void);
    int changed = mat_water_enabled() != (x360 != 0);
    water_mode = x360 != 0;
    // The PC allocates a 1x1 placeholder when road reflections are disabled.
    // Enabling Water live needs the existing device-reset/allocation path then.
    if (changed && water_mode && !M32(0x009017D4u)) graphics_reset_request();
}
void mat_water_emit(int variant) {
    ++water_this_frame; ++water_draws; water_variants += variant != 0;
}
void mat_water_failure(int pipeline) {
    unsigned n = __atomic_add_fetch(pipeline ? &water_pipeline_fail : &water_shader_fail, 1, __ATOMIC_RELAXED);
    if (diag_on && n <= 8) diag_event("water: %s compile failed (PC draw retained)", pipeline ? "pipeline" : "shader");
}
void mat_water_reflection_forced(void) { ++water_forced; }
int mat_water_reflection_requested(void) {
    return mat_water_enabled() && water_previous_frame && M32(GAMEFLOW_STATE) == 6;
}
void mat_water_frame(void) {
    water_previous_frame = water_this_frame; water_this_frame = 0;
    if (!diag_on) return;
    static uint64_t last;
    uint64_t now = host_ticks();
    if (last && now - last < 1000000000ull) return;
    diag_event("water: draws %u variant %u refl_forced %u compile_failed %u pipeline_failed %u",
        water_draws, water_variants, water_forced,
        __atomic_exchange_n(&water_shader_fail, 0, __ATOMIC_RELAXED),
        __atomic_exchange_n(&water_pipeline_fail, 0, __ATOMIC_RELAXED));
    water_draws = water_variants = water_forced = 0; last = now;
}
const uint8_t *mat_water_normal(uint32_t *w, uint32_t *h) {
    static uint8_t *rgba; static uint32_t width, height; static int tried;
    if (!tried) {
        tried = 1;
        rgba = x360_texture_rgba("GLOBAL/GLOBALB.BUN", 0x70D682D0u, &width, &height);
        if (diag_on) diag_event("water: SFX_WAVES_NORM %ux%u %s", width, height, rgba ? "loaded" : "missing; PC retained");
    }
    *w = width; *h = height; return rgba;
}
int mat_water_constants(const SmResult *pcvs, const float (*vsc)[4], float out[32][4]) {
    if (!mat_water_draw.mesh) return 0;
    memset(out, 0, 32 * 16);
    static const struct { const char *name; int dst, count; } copy[] = {
        {"WorldViewProj",0,4}, {"Fog_Br_Plus_Bm",5,1}, {"Fog_One_Over_BrBm",6,1},
        {"Fog_Const_1",7,1}, {"Fog_Const_2",8,1}, {"Fog_Const_3",9,1},
        {"FogValue",11,1}, {"LocalLightVec",18,1}, {"LocalEyePos",19,1}, {"TextureOffset",20,1}
    };
    for (size_t i = 0; i < sizeof copy / sizeof *copy; ++i) {
        int reg = grass_reg(pcvs, copy[i].name, copy[i].count);
        if (reg < 0 || reg + copy[i].count > 256) return 0;
        memcpy(out[copy[i].dst], vsc[reg], copy[i].count * 16);
    }
    // Float3 PC constants leave their spare component unspecified; X360 uses
    // four-component eye/light arithmetic, so set homogeneous w explicitly.
    out[18][3] = 0; out[19][3] = 1;
    out[10][0] = X360F_KMAXDIST_WATER;
    uint32_t v = M32(0x009195E0u + mat_water_draw.view * 0x70u);
    if (!v) return 0;
    for (int col = 0; col < 3; ++col) for (int row = 0; row < 4; ++row) {
        out[15 + col][row] = mat_water_draw.world[row][col];
        for (int k = 0; k < 4; ++k)
            out[12 + col][row] += mat_water_draw.world[row][k] * F32(v + 0x40 + k * 16 + col * 4);
    }
    out[21][0] = X360F_WAVE0_0; out[23][0] = X360F_WAVE0_2; out[24][0] = X360F_WAVE0_3;   // wave0/wave1: the VS's defaults
    out[25][0] = X360F_WAVE1_0; out[27][0] = X360F_WAVE1_2; out[28][0] = X360F_WAVE1_3; out[28][1] = X360F_WAVE1_3N;
    uint32_t tod = M32(TIME_OF_DAY_PTR);
    out[29][0] = tod ? fminf(1, fmaxf(.4f, 1 - F32(tod + 0x100))) : 1;
    return 1;
}
static const char water_common_msl[] =
"#include <metal_stdlib>\n"
"using namespace metal;\n"
"struct WaterConstants { float4 k[32]; };\n"
"struct WaterAux { float4 fixup; float point_size; float pad[3]; };\n"
"struct WaterOut { float4 pos [[position]];\n"
"float4 t0 [[user(water_t0)]];\n"
"float4 t1 [[user(water_t1)]];\n"
"float4 t2 [[user(water_t2)]];\n"
"float4 t3 [[user(water_t3)]];\n"
"float4 t4 [[user(water_t4)]];\n"
"float4 t5 [[user(water_t5)]];\n"
"float4 t6 [[user(water_t6)]];\n"
"float4 t7 [[user(water_t7)]];\n"
"};\n"
;
static const char water_vs_body[] =
"float4 oPos=float4(0);\n"
"float4 oTexCoord0=float4(0);\n"
"float4 oTexCoord1=float4(0);\n"
"float4 oTexCoord2=float4(0);\n"
"float4 oTexCoord3=float4(0);\n"
"float4 oTexCoord4=float4(0);\n"
"float4 oTexCoord5=float4(0);\n"
"float4 oTexCoord6=float4(0);\n"
"float4 oTexCoord7=float4(0);\n"
"const float4 c253 = " X360L_09BFC724_C253 ";\n"
"const float4 c254 = " X360L_09BFC724_C254 ";\n"
"const float4 c255 = " X360L_09BFC724_C255 ";\n"
"\n"
X360B_WATER_VS_BODY_0
// Retail sea vertices have z=0. PC's creek mesh also has +/-0.355 local z:
// preserve that geometry rather than flattening it with the specialized X360 VS.
"\tr11 = float4(dot(r10, w.k[0]), dot(r10, w.k[1]), dot(r10, w.k[2]), dot(r10, w.k[3]));\n"
"\tr11.xy += r11.ww * w.k[4].xy;\n"
X360B_WATER_VS_BODY_1
"WaterOut o; o.pos=oPos; o.pos.xy += aux.fixup.xy * oPos.w;\n"
"o.t0=oTexCoord0;\n"
"o.t1=oTexCoord1;\n"
"o.t2=oTexCoord2;\n"
"o.t3=oTexCoord3;\n"
"o.t4=oTexCoord4;\n"
"o.t5=oTexCoord5;\n"
"o.t6=oTexCoord6;\n"
"o.t7=oTexCoord7;\n"
"return o;\n"
"}\n"
;
static const char water_ps_body[] =
"float4 oC0=float4(0);\n"
"const float4 c253 = " X360L_09BFD3E0_C253 ";\n"
"const float4 c254 = " X360L_09BFD3E0_C254 ";\n"
"const float4 c255 = " X360L_09BFD3E0_C255 ";\n"
"\n"
X360B_WATER_PS_BODY_0
"\tfloat4 r13 = float4(0);\n"
X360B_WATER_PS_BODY_1
"return oC0;\n"
"}\n"
;

char *mat_water_shader(const SmResult *pc, int stage, uint16_t input_int, uint16_t input_uint) {
    char *result = NULL;
    if (stage == SM_VERTEX) {
        int position = -1;
        char attr[2048] = "struct WaterAttr {\n"; size_t at = strlen(attr);
        for (int k = 0; k < pc->n_inputs; ++k) {
            const SmInput *in = &pc->inputs[k];
            if (in->usage == SM_USAGE_POSITION && !in->usage_index) position = in->reg;
            const char *ty = (input_int & (1u << in->reg)) ? "int4" : (input_uint & (1u << in->reg)) ? "uint4" : "float4";
            at += snprintf(attr + at, sizeof attr - at, "%s a%d [[attribute(%d)]];\n", ty, in->reg, in->reg);
        }
        if (position < 0) return NULL;
        snprintf(attr + at, sizeof attr - at, "};\n");
        char entry[512];
        snprintf(entry, sizeof entry, "vertex WaterOut sm_main(WaterAttr i [[stage_in]], constant WaterConstants &w [[buffer(20)]], constant WaterAux &aux [[buffer(19)]]) {\nfloat4 iPosition0=float4(i.a%d);\n", position);
        asprintf(&result, "%s%s%s%s", water_common_msl, attr, entry, water_vs_body);
    } else if (stage == SM_PIXEL) {
        asprintf(&result, "%s%s%s", water_common_msl,
            "fragment float4 sm_main(WaterOut in [[stage_in]], constant WaterConstants &w [[buffer(5)]], "
            "texture2d<float> diffuse [[texture(0)]], texture2d<float> waves [[texture(15)]], texture2d<float> reflection [[texture(14)]]) {\n"
            "constexpr sampler wrap(coord::normalized, address::repeat, filter::linear, mip_filter::linear);\n"
            "constexpr sampler edge(coord::normalized, address::clamp_to_edge, filter::linear);\n", water_ps_body);
        // NFS_TEST_WATER_TINT=1 (test only): the X360 water draws come out magenta, so a capture shows where they land.
        char *ret = result && getenv("NFS_TEST_WATER_TINT") ? strstr(result, "return oC0;") : NULL;
        if (ret) {
            char *tinted = NULL;
            asprintf(&tinted, "%.*soC0=float4(1,0,1,1);\n%s", (int)(ret - result), result, ret);
            free(result); result = tinted;
        }
    }
    return result;
}

int mat_aux_constants(const SmResult *vs, const float (*vsc)[4], float out[52][4]) {
    if (!mat_aux_draw.valid || mat_aux_draw.emitted) return 0;
    int wvp = grass_reg(vs,"WorldViewProj",4), eye = grass_reg(vs,"LocalEyePos",1), uv = grass_reg(vs,"TextureOffset",1);
    if (wvp < 0 || wvp+4 > 256 || eye < 0 || eye >= 256 || uv < 0 || uv >= 256) return 0;
    memset(out,0,52*16);
    memcpy(out,vsc[wvp],64); memcpy(out[5],vsc[eye],16); out[5][3] = 1;
    memcpy(out[47],vsc[uv],16); out[6][0] = -1;
    for (int i = 0; i < mat_aux_draw.context.count; ++i) {
        const AuxLight *l = &mat_aux_draw.context.lights[i];
        // The retail upload is column-major, including the first light's gain
        // for every derived RGB, but each light's OWN attenuation intensity.
        for (int c = 0; c < 3; ++c) {
            out[7+4*i+c][0] = l->position[c];
            out[7+4*i+c][1] = l->rgb[c];
            out[7+4*i+c][2] = l->rgb[c]*mat_aux_draw.context.lights[0].intensity*3;
        }
        out[7+4*i+3][0] = l->radius;
        out[7+4*i+3][1] = out[7+4*i+3][2] = l->intensity;
        out[39+i][0] = 1;
    }
    static int signature = -1;
    if (signature < 0) { const char *s = getenv("NFS_TEST_AUX_SIGNATURE"); signature = s && !strcmp(s,"1"); }
    out[48][0] = signature;
    return 1;
}

// R20 shader source: 09C0C3B4 / 09C0DE10, translated at build time (gen/x360_shader_body.inc, XenosRecomp MIT).
// Dry/wet road PSs are byte-for-byte identical after their comments. Retain the
// lane operations, first-light intensity and six separate user varyings.
static const char aux_common_msl[] =
"#include <metal_stdlib>\n"
"using namespace metal;\n"
"struct AuxConstants { float4 k[52]; };\n"
"struct AuxFixup { float4 fixup; float point_size; float pad[3]; };\n"
"struct AuxOut { float4 pos [[position]]; float4 uv [[user(aux_uv)]];\n"
"float3 eye [[user(aux_eye)]]; float3 local [[user(aux_local)]];\n"
"float3 l0 [[user(aux_l0)]]; float3 l1 [[user(aux_l1)]]; float3 l2 [[user(aux_l2)]];\n"
"float3 l3 [[user(aux_l3)]]; float3 l4 [[user(aux_l4)]]; float3 l5 [[user(aux_l5)]]; };\n"
;
static const char aux_vs_body[] =
"const float4 c255=" X360LC_09C0C3B4_C255 ";\n"
"float4 oPos=float4(0), oTexCoord0, oTexCoord1, oTexCoord2, oColor0, oColor1, oColor2, oColor3, oColor4, oColor5;\n"
X360B_AUX_VS_BODY_0
"\toPos = float4(dot(r1,a.k[0]),dot(r1,a.k[1]),dot(r1,a.k[2]),dot(r1,a.k[3]));\n"
"// D3D9 and Metal both use 0 <= clip.z <= clip.w. Retail -" X360S_AUX_DEPTH_NUDGE_T " is absolute clip depth.\n"
"oPos.z -= " X360S_AUX_DEPTH_NUDGE "f;\n"
X360B_AUX_VS_BODY_1
"AuxOut o; o.pos=oPos; o.pos.xy += aux.fixup.xy*oPos.w;\n"
"o.uv=oTexCoord0; o.eye=oTexCoord1.xyz; o.local=oTexCoord2.xyz;\n"
"o.l0=oColor0.xyz; o.l1=oColor1.xyz; o.l2=oColor2.xyz;\n"
"o.l3=oColor3.xyz; o.l4=oColor4.xyz; o.l5=oColor5.xyz;\n"
"return o;\n"
;
static const char aux_ps_body[] =
"const float4 c254=" X360LC_09C0DE10_C254 ", c255=" X360LC_09C0DE10_C255 ";\n"
"float4 iTexCoord0=in.uv,iTexCoord1=float4(in.eye,0),iTexCoord2=float4(in.local,1);\n"
"float4 iColor0=float4(in.l0,0),iColor1=float4(in.l1,0),iColor2=float4(in.l2,0),iColor3=float4(in.l3,0),iColor4=float4(in.l4,0),iColor5=float4(in.l5,0),oC0=float4(0);\n"
X360B_AUX_PS_BODY_0
"\tfloat4 r14 = float4(0);\n"
X360B_AUX_PS_BODY_1
"\t// PC/HD normals are RGB UNORM, not the X360 channel reconstruction.\n"
"\tr13 = float4(2*r10.y-1,2*r10.z-1,0,2*r10.x-1);\n"
X360B_AUX_PS_BODY_2
"\n"   // (the X360 reconstruction's second step: not needed)
X360B_AUX_PS_BODY_3
"if (a.k[48].x != 0 && any(oC0.xyz > float3(0))) oC0=float4(0,1,1,1);\n"
"return oC0;\n"
;

char *mat_aux_shader(const SmResult *pc, int stage, uint16_t input_int, uint16_t input_uint) {
    char *result = NULL;
    if (stage == SM_VERTEX) {
        int reg[4] = {-1,-1,-1,-1};
        const int usage[4] = {SM_USAGE_POSITION,SM_USAGE_NORMAL,SM_USAGE_TEXCOORD,SM_USAGE_TANGENT};
        char attr[2048] = "struct AuxAttr {\n"; size_t at = strlen(attr);
        for (int k = 0; k < pc->n_inputs; ++k) {
            const SmInput *in = &pc->inputs[k];
            for (int j = 0; j < 4; ++j) if (in->usage == usage[j] && !in->usage_index) reg[j] = in->reg;
            const char *ty = (input_int & (1u<<in->reg)) ? "int4" : (input_uint & (1u<<in->reg)) ? "uint4" : "float4";
            at += snprintf(attr+at,sizeof attr-at,"%s a%d [[attribute(%d)]];\n",ty,in->reg,in->reg);
        }
        for (int j = 0; j < 4; ++j) if (reg[j] < 0) return NULL;
        snprintf(attr+at,sizeof attr-at,"};\n");
        char entry[1024];
        snprintf(entry,sizeof entry,"vertex AuxOut sm_main(AuxAttr i [[stage_in]], constant AuxConstants &a [[buffer(20)]], constant AuxFixup &aux [[buffer(19)]]) {\n"
            "float4 iPosition0=float4(i.a%d), iNormal0=float4(i.a%d), iTexCoord0=float4(i.a%d), iTangent0=float4(i.a%d);\n",reg[0],reg[1],reg[2],reg[3]);
        asprintf(&result,"%s%s%s%s}\n",aux_common_msl,attr,entry,aux_vs_body);
    } else if (stage == SM_PIXEL) {
        asprintf(&result,"%s%s%s}\n",aux_common_msl,
            "fragment float4 sm_main(AuxOut in [[stage_in]],constant AuxConstants &a [[buffer(5)]],texture2d<float> td [[texture(0)]],texture2d<float> tn [[texture(1)]],texture2d<float> ts [[texture(2)]]) {\n"
            "constexpr sampler wrap(coord::normalized,address::repeat,filter::linear,mip_filter::linear);\n",aux_ps_body);
    }
    return result;
}
// ---- R19 building glass (NFS_BUILDING_GLASS=pc|x360, default PC; F10 Graphics > Building glass, live) ----
// X360 effect15 glassreflect/p0: VS 09BC692C, PS 09BC78B4 (translated at build time as R18/R27).
// Replaces PC effect5 glossywindow (all six techniques) for records reaching 006C8000 from 006D7660 / 006E0200, in
// every view that draws it (main, mirror, wet-road reflection, car env-map faces; entry 4, the shadow map, never does).
// Kept exactly: per-vertex Fresnel saturate(.2+.4(1-N.V)) and pow(R.V,100) glint, PS reflect + atan2 polynomial
// longitude, alpha 1-diffuseAlpha*spec, Brightness 2 once (PC's own x2 is not applied again), view-depth fog cap kMaxDist.
// Reflection V = the X360 UV1.y recovered per PC vertex (glass_uv1.inc, 84 %; .5*UV0.y where none corresponds);
// sun shadow = the X360 9-tap on the PC's shadow map (below).
MatGlassDraw mat_glass_draw;
static int glass_mode = -1;
static unsigned glass_seen, glass_replaced, glass_blended, glass_skips[GLASS_SKIP_COUNT], glass_shader_fail, glass_pipeline_fail;
static unsigned glass_rejects;            // RT entries whose rejection was logged once (diag)
static unsigned glass_entry[16][2];       // records per RT entry, accepted / not (diag): 0 main, 1 road reflection, 3 mirror, 4 shadow map
int mat_glass_enabled(void) {
    int postfx_look_variant(const char *v);
    if (!X360_SHADER_LITS || !X360_ENGINE_CONSTS) return 0;
    if (glass_mode < 0) { const char *s = getenv("NFS_BUILDING_GLASS"); glass_mode = !(s && !strcmp(s, "pc")); }
    if (postfx_look_variant("glasspc")) return 0;
    if (postfx_look_variant("glassx360")) return 1;
    return glass_mode;
}
void materials_set_building_glass(int x360) {
    glass_mode = x360 != 0;
    if (diag_on) diag_event("materials: building glass now %s", glass_mode ? "x360" : "pc");
}
void materials_glass_clear(void) {
    if (diag_on && mat_glass_draw.mesh && !mat_glass_draw.emitted) ++glass_skips[mat_glass_draw.skip];
    memset(&mat_glass_draw, 0, sizeof mat_glass_draw);
}
void materials_glass_record(uint32_t q) {
    materials_glass_clear();
    uint32_t mesh = q ? M32(q) : 0, effect = q ? M32(q + 16) : 0, view = M32(CUR_VIEW);
    if (!mesh || !effect || M32(effect + 4) != 5) return;      // effect 5 = IDI_GLOSSYWINDOW_FX (008F9BE8 table)
    if (diag_on) ++glass_seen;
    mat_glass_draw.mesh = mesh; mat_glass_draw.skip = GLASS_VIEW;
    uint32_t world = M32(q + 28), vid = view ? M32(view + 4) : 99;
    // Every view that draws effect 5: main (RT entry 0), mirror (3), wet-road reflection (1, view 4) and the car
    // env-map faces, so windows agree with their reflections. 006DA9B0 keeps effect 5 in every entry but 4, the shadow
    // map, where it swaps in the world caster effect (0093DE98), so entry 4 never reaches here.
    uint32_t entry = view ? M32(view) : 99;
    // eViews (009195E0, stride 0x70) has 24 entries: the loop at 0047C88F walks +0x44 from 00919624 to 0091A034, (0xA10 /
    // 0x70) + 1 = 24. Env-map faces are views 18-23 (RT entries 5-10 at 15.1.1).
    if (diag_on && entry < 16) ++glass_entry[entry][!view || entry == 4 || !world || vid >= 24];
    if (diag_on && entry < 16 && entry != 4 && (!world || vid >= 24) && !(glass_rejects & (1u << entry))) {
        glass_rejects |= 1u << entry;
        diag_event("glass: RT entry %u view id %u world %08X: not replaced (no world transform / no eView)", entry, vid, world);
    }
    if (!view || entry == 4 || entry >= 16 || !world || vid >= 24) return;
    // 006C8000 multiplies by the view's alternative camera (+C0) when the bound TextureInfo's +50 flag is set.
    uint32_t v = M32(0x009195E0u + vid * 0x70u), ti = M32(0x00982CB4u), off = ti && G[ti + 0x50] ? 0xC0 : 0x40;
    if (!v) return;
    const float *w = (const float *)(G + world);
    for (int col = 0; col < 3; ++col) for (int row = 0; row < 4; ++row) {
        float sum = 0;
        for (int k = 0; k < 4; ++k) sum += w[row * 4 + k] * F32(v + off + 16 * k + 4 * col);
        mat_glass_draw.world_view[col][row] = sum;
    }
    mat_glass_draw.view = vid;
    mat_glass_draw.skip = GLASS_NO_DRAW;
}
// UV source (build step 2): X360 UV1.y per PC glass vertex from glass_uv1.inc (tools_snapshot/x360_glass_uv1.py: the
// uniquely corresponding X360 vertex of the same solid; GLASS_UV1_MATCHED of GLASS_UV1_VERTICES), found by the FNV-1a 64
// of the vertex buffer's bytes. Vertices without one, and buffers not in the table, keep .5*UV0.y (GLASS_UV1_NONE).
#include "gen/glass_uv1.inc"   // tools/gen_tables.py, from the player's files
static unsigned glass_uv_draws[2], glass_uv_vertices[2];      // per draw: [0] no row (uv0half), [1] row; vertices matched / not
static float *glass_uv1_rows[GLASS_UV1_BUFFERS];
int mat_glass_uv1(const uint8_t *bytes, uint32_t size, const float **out, uint32_t *matched) {
    uint64_t h = 1469598103934665603ull;
    for (uint32_t i = 0; i < size; ++i) h = (h ^ bytes[i]) * 1099511628211ull;
    int lo = 0, hi = (int)GLASS_UV1_BUFFERS - 1;
    while (lo <= hi) {
        int mid = (lo + hi) / 2;
        if (glass_uv1_table[mid].hash < h) lo = mid + 1; else if (glass_uv1_table[mid].hash > h) hi = mid - 1;
        else {
            uint32_t n = glass_uv1_table[mid].vertices, m = 0;
            if (n * 36u != size) return -1;
            if (!glass_uv1_rows[mid]) {
                float *f = malloc(n * sizeof *f);
                for (uint32_t k = 0; k < n; ++k) {
                    uint16_t ix = glass_uv1_index[glass_uv1_table[mid].first + k];
                    f[k] = ix == 0xFFFF ? GLASS_UV1_NONE : glass_uv1_values[ix];
                }
                glass_uv1_rows[mid] = f;
            }
            for (uint32_t k = 0; k < n; ++k) m += glass_uv1_rows[mid][k] != GLASS_UV1_NONE;
            *out = glass_uv1_rows[mid]; *matched = m;
            return mid;
        }
    }
    return -1;
}
void mat_glass_uv_note(int found, uint32_t vertices, uint32_t matched) {
    if (!diag_on) return;
    ++glass_uv_draws[found != 0]; glass_uv_vertices[0] += matched; glass_uv_vertices[1] += vertices - matched;
}
void mat_glass_emit(int blended) { mat_glass_draw.emitted = 1; ++glass_replaced; glass_blended += blended != 0; }
void mat_glass_failure(int pipeline) {
    unsigned n = __atomic_add_fetch(pipeline ? &glass_pipeline_fail : &glass_shader_fail, 1, __ATOMIC_RELAXED);
    if (diag_on && n <= 8) diag_event("glass: %s compile failed (PC draw retained)", pipeline ? "pipeline" : "shader");
}
void mat_glass_frame(void) {
    materials_glass_clear();
    if (!diag_on) return;
    static uint64_t last;
    uint64_t now = host_ticks();
    if (!last) { last = now; return; }
    if (now - last < 1000000000ull) return;
    static const char *const reasons[] = {"no_draw", "pc", "view", "state", "constants", "shader", "pipeline"};
    char skipped[256] = "none"; size_t used = 0;
    for (int i = 0; i < GLASS_SKIP_COUNT; ++i) if (glass_skips[i])
        used += snprintf(skipped + used, sizeof skipped - used, "%s%s=%u", used ? "," : "", reasons[i], glass_skips[i]);
    char entries[160] = ""; size_t eu = 0;
    for (int i = 0; i < 16; ++i) if (glass_entry[i][0] || glass_entry[i][1])
        eu += snprintf(entries + eu, sizeof entries - eu, " rt%d=%u/%u", i, glass_entry[i][0], glass_entry[i][1]);
    diag_event("glass: dt %.2f mode %s uv_source draws x360uv1 %u uv0half %u vertices uv1 %u uv0half %u seen %u (views accepted/not%s) replaced %u blended %u fallback %s compile_failed %u pipeline_failed %u",
        (now - last) * 1e-9, mat_glass_enabled() ? "x360" : "pc", glass_uv_draws[1], glass_uv_draws[0], glass_uv_vertices[0], glass_uv_vertices[1],
        glass_seen, eu ? entries : " none", glass_replaced, glass_blended, skipped,
        __atomic_exchange_n(&glass_shader_fail, 0, __ATOMIC_RELAXED), __atomic_exchange_n(&glass_pipeline_fail, 0, __ATOMIC_RELAXED));
    glass_seen = glass_replaced = glass_blended = 0; memset(glass_skips, 0, sizeof glass_skips); memset(glass_entry, 0, sizeof glass_entry);
    memset(glass_uv_draws, 0, sizeof glass_uv_draws); memset(glass_uv_vertices, 0, sizeof glass_uv_vertices); last = now;
    diag_event("particles: mode %s replaced draws %u (rain %u) quads %u clamped corners %u max d %.3f (after the clamp %.3f)", particles_on() ? "x360" : "pc",
        particles_draws, particles_rain_draws, particles_quads, particles_clamped, particles_maxd, particles_maxd_after);
    particles_draws = particles_rain_draws = particles_quads = particles_clamped = 0; particles_maxd = particles_maxd_after = 0.0f;
}
int mat_glass_constants(const SmResult *pcvs, const SmResult *pcps, const float (*vsc)[4], const float (*psc)[4], float out[32][4]) {
    if (!mat_glass_draw.mesh) return 0;
    memset(out, 0, 32 * 16);
    static const struct { const char *name; int dst, count; } copy[] = {
        {"WorldViewProj",0,4}, {"Fog_Br_Plus_Bm",5,1}, {"Fog_One_Over_BrBm",6,1}, {"Fog_Const_1",7,1}, {"Fog_Const_2",8,1},
        {"Fog_Const_3",9,1}, {"FogValue",11,1}, {"DiffuseColour",15,1}, {"LocalEyePos",16,1}, {"LocalLightVec",17,1},
        {"AmbientColour",22,1}
    };
    for (size_t i = 0; i < sizeof copy / sizeof *copy; ++i) {
        int reg = grass_reg(pcvs, copy[i].name, copy[i].count);
        if (reg < 0 || reg + copy[i].count > 256) return 0;
        memcpy(out[copy[i].dst], vsc[reg], copy[i].count * 16);
    }
    out[10][0] = X360F_KMAXDIST_GLASS;                                   // kMaxDist; Fog_DistanceScale (c11) = PC FogValue
    out[16][3] = 1; out[17][3] = 0;                      // float3 eye/light: X360 dots all four components
    memcpy(out[12], mat_glass_draw.world_view, 48);      // WorldView rows from the matrices 006C8000 multiplies
    int shadow = grass_reg(pcvs, "ShadowTransform", 4);
    if (shadow >= 0 && shadow + 4 <= 256) { memcpy(out[18], vsc[shadow], 64); out[26][0] = 1; }
    out[24][0] = X360E_STANDARD_BRIGHTNESS;              // STANDARD_BRIGHTNESS (828FCC18), set by 82450698
    int postfx_look_variant(const char *v);
    out[27][3] = postfx_look_variant("glassuv0half");   // BOOT_DIAG_LOOK_AB: the same frame with .5*UV0.y everywhere
    out[27][0] = X360E_SHADOWMAP_BIAS;                   // SHADOWMAP_BIAS (828FCC54, set by 82451080)
    (void)pcps; (void)psc;
    static int tint = -1;
    if (tint < 0) { const char *s = getenv("NFS_TEST_GLASS_TINT"); tint = s && *s && strcmp(s, "0"); }
    out[27][2] = (float)tint;
    return 1;
}
static const char glass_common_msl[] =
"#include <metal_stdlib>\n"
"using namespace metal;\n"
"struct GlassConstants { float4 k[32]; };\n"
"struct GlassOut { float4 pos [[position]];\n"
"float4 t0 [[user(glass_t0)]]; float4 t1 [[user(glass_t1)]]; float4 t2 [[user(glass_t2)]]; float4 t3 [[user(glass_t3)]];\n"
"float4 t4 [[user(glass_t4)]]; float4 t5 [[user(glass_t5)]]; float4 t6 [[user(glass_t6)]];\n"
"float4 c0 [[user(glass_c0)]]; float4 c1 [[user(glass_c1)]]; float4 c2 [[user(glass_c2)]]; };\n"
"#define XMAX 3.402823466e+38f\n";
// 09BC692C. Constants k: 0-3 WorldViewProj, 4 ScreenOffset, 5-9 fog, 10 kMaxDist, 11 Fog_DistanceScale,
// 12-14 WorldView, 15 DiffuseColour, 16 LocalEyePos, 17 LightDirVec, 18-21 shadow matrix, 22 AmbientColour.
static const char glass_vs_body[] =
"const float4 c254 = " X360L_09BC692C_C254 ";\n"
"const float4 c255 = " X360L_09BC692C_C255 ";\n"
"float4 oPos=0, oTexCoord0=0, oTexCoord1=0, oTexCoord2=0, oTexCoord3=0, oTexCoord4=0, oTexCoord5=0, oTexCoord6=0, oColor0=0, oColor1=0, oColor2=0;\n"
"float4 r0=0, r1=0, r2=0, r3=0, r4=0, r5=0, r6=0, r7=0, r8=0, r9=0, r10=0, r11=0; float ps=0;\n"
X360B_GLASS_VS_BODY
"GlassOut o; o.pos = oPos; o.pos.xy += aux.fixup.xy * oPos.w;\n"
"o.t0 = oTexCoord0; o.t1 = oTexCoord1; o.t2 = oTexCoord2; o.t3 = oTexCoord3; o.t4 = oTexCoord4; o.t5 = oTexCoord5; o.t6 = oTexCoord6;\n"
"o.c0 = oColor0; o.c1 = oColor1; o.c2 = oColor2;\n"
"return o;\n"
"}\n";
// 09BC78B4 before its shadow block. Reflection U from the atan2 polynomial of R.xy, V = TexCoord0.w.
static const char glass_ps_body[] =
"const float4 c252 = " X360L_09BC78B4_C252 ";\n"
"const float4 c253 = " X360L_09BC78B4_C253 ";\n"
"const float4 c254 = " X360L_09BC78B4_C254 ";\n"
"const float4 c255 = " X360L_09BC78B4_C255 ";\n"
"float4 r0 = i.t0, r1 = i.t1, r2 = i.t2, r3 = i.t3, r4 = i.t4, r5 = i.t5, r6 = i.t6, r7 = i.c0, r8 = i.c1, r9 = i.c2;\n"
"float ps = 0; float4 oC0 = 0;\n"
X360B_GLASS_PS_BODY;
// After the shadow factor r7.w: lighting, glint, reflection*Fresnel*diffuseAlpha, Brightness, fog.
static const char glass_ps_end[] =
X360B_GLASS_PS_END_0
"if (g.k[27].z != 0) { r0.xyz = float3(1, 0, 1); r3.x = 1; }   // NFS_TEST_GLASS_TINT: the reflection term only\n"
X360B_GLASS_PS_END_1
"if (aux.alpha_func != 0u && !glass_alpha_test(aux.alpha_func, oC0.w, aux.alpha_ref)) discard_fragment();\n"
"return oC0;\n"
"}\n";
// 09BC78B4's shadow block (lines 96-130) on the PC's shadow map: 9 linear-filtered depth fetches at 0 and +-0.5 texel
// (SHADOWMAP_SAMPLER: linear, border white), each lit when depth >= z/w + g_fShadowMapBias * N.L (bias -0.0001, 828FCC54,
// set by 82451080; N.L = the VS's unsaturated oTexCoord3.y), shadow = saturate(lit/9 + saturate(2 y_ndc - 1)).
// PC r6 = ShadowTransform x pos is already texture space (u, v), so y_ndc = 1 - 2v and the far fade is saturate(1 - 4v),
// as R21 decoded. The PC technique's own taps / InvShadowStrength are not used. t0 (shadows off on PC) has none.
static const char glass_shadow_x360[] =
"if (g.k[26].x != 0) { float rw = 1.0 / r6.w, ref = r6.z * rw + g.k[27].x * r3.y, lit = 0;\n"
" float2 uv = r6.xy * rw, tx = 0.5 / float2(tex1.get_width(), tex1.get_height());\n"
" for (int y = -1; y <= 1; ++y) for (int x = -1; x <= 1; ++x) lit += GLASS_DEPTH(uv + tx * float2(x, y)) >= ref ? 1.0 : 0.0;\n"
" r7.w = saturate(lit * c254.z + saturate(1.0 - 4.0 * uv.y)); }\n";
char *mat_glass_shader(const uint32_t *tokens, uint32_t n, const SmResult *pc, int stage, uint16_t input_int, uint16_t input_uint) {
    uint64_t h = 1469598103934665603ull;
    const uint8_t *b = (const uint8_t *)tokens;
    for (size_t i = 0; i < 4 * (size_t)n; ++i) h = (h ^ b[i]) * 1099511628211ull;
    char *result = NULL;
    if (stage == SM_VERTEX) {
        // t0 / t1 (also t4, t5) vs_1_1, t2 (also t3) vs_3_0 (host/shaders/testdata/glossywindow)
        if (h != 0xEAA7AABC9BAF0607ull && h != 0x3B7041897590C529ull && h != 0x74E7D294D4B2ECD6ull) return NULL;
        int reg[4] = {-1, -1, -1, -1};
        const int usage[4] = {SM_USAGE_POSITION, SM_USAGE_NORMAL, SM_USAGE_COLOR, SM_USAGE_TEXCOORD};
        char attr[1024] = "struct GlassAttr {\n";
        size_t at = strlen(attr);
        for (int k = 0; k < pc->n_inputs; ++k) {
            const SmInput *in = &pc->inputs[k];
            for (int i = 0; i < 4; ++i) if (in->usage == usage[i] && !in->usage_index) reg[i] = in->reg;
            const char *ty = (input_int & (1u << in->reg)) ? "int4" : (input_uint & (1u << in->reg)) ? "uint4" : "float4";
            at += snprintf(attr + at, sizeof attr - at, "%s a%d [[attribute(%d)]];\n", ty, in->reg, in->reg);
        }
        for (int i = 0; i < 4; ++i) if (reg[i] < 0) return NULL;
        snprintf(attr + at, sizeof attr - at, "float uv1y [[attribute(%d)]];\n};\nstruct GlassAux { float4 fixup; float point_size; float3 pad; };\n", MAT_GLASS_UV1_SLOT);
        char entry[640];
        // UV1.y: the recovered X360 value per vertex (render.m binds glass_uv1.inc's row at MAT_GLASS_UV1_SLOT), or the
        // GLASS_UV1_NONE sentinel where none corresponds (no row, no unique X360 vertex): there PC's .5*UV0.y.
        snprintf(entry, sizeof entry, "vertex GlassOut sm_main(GlassAttr i [[stage_in]], constant GlassConstants &g [[buffer(20)]], constant GlassAux &aux [[buffer(19)]]) {\n"
            "float4 iPosition0=float4(i.a%d), iNormal0=float4(i.a%d), iColor0=float4(i.a%d), iTexCoord0=float4(i.a%d);\n"
            "float4 iTexCoord1=float4(0, i.uv1y > -1e29 && g.k[27].w == 0 ? i.uv1y : 0.5 * iTexCoord0.y, 0, 0);\n", reg[0], reg[1], reg[2], reg[3]);
        asprintf(&result, "%s%s%s%s", glass_common_msl, attr, entry, glass_vs_body);
    } else if (stage == SM_PIXEL) {
        // t0 PS1.1 (no shadow), t1 PS2.0, t2/t3 PS3.0, t4/t5 PS2.x (host/shaders/testdata/glossywindow)
        static const uint64_t techs[] = {0xA8D19FEBB9579A2Bull, 0x0919FCE65F8531A1ull, 0xF77FCE200B15449Cull,
                                         0x40064267BE4B6733ull, 0xD4B6AB8CE477F25Aull, 0x10B22A6361C33088ull};
        int t = -1;
        for (int k = 0; k < 6; ++k) if (techs[k] == h) t = k;
        if (t < 0) return NULL;
        int hw = 0, refl = t ? 2 : 1;            // WINDOWREFLECTION: t0 s1, t1..t5 s2; shadow_sampler s1
        for (int s = 0; s < pc->n_samplers; ++s) if (pc->samplers[s].reg == 1) hw = pc->samplers[s].shadow;
        char head[1800];
        snprintf(head, sizeof head,
            "struct GlassPSAux { uint alpha_func; float alpha_ref; uint pad0, pad1; float4 bumpenv[8]; float4 bumplum[8]; float lodbias[16]; };\n"
            "static inline bool glass_alpha_test(uint f, float a, float ref) {\n"
            "    float qa = rint(saturate(a) * 255.0f), qr = rint(saturate(ref) * 255.0f);\n"
            "    switch (f) { case 1: return false; case 2: return qa < qr; case 3: return qa == qr; case 4: return qa <= qr;\n"
            "    case 5: return qa > qr; case 6: return qa != qr; case 7: return qa >= qr; default: return true; }\n}\n"
            "constexpr sampler glass_shadow_s(filter::linear, address::clamp_to_border, border_color::opaque_white);\n"
            "#define GLASS_REFL tex%d\n#define GLASS_REFL_S smp%d\n#define GLASS_REFL_N %d\n"
            "#define GLASS_DEPTH(uv) %s\n"
            "fragment float4 sm_main(GlassOut i [[stage_in]], constant GlassConstants &g [[buffer(5)]], constant GlassPSAux &aux [[buffer(3)]],\n"
            " texture2d<float> tex0 [[texture(0)]], sampler smp0 [[sampler(0)]], %s tex1 [[texture(1)]], sampler smp1 [[sampler(1)]]%s) {\n",
            refl, refl, refl, hw ? "tex1.sample(glass_shadow_s, (uv))" : "tex1.sample(glass_shadow_s, (uv)).x",
            hw ? "depth2d<float>" : "texture2d<float>", t ? ", texture2d<float> tex2 [[texture(2)]], sampler smp2 [[sampler(2)]]" : "");
        asprintf(&result, "%s%s%s%s%s", glass_common_msl, head, glass_ps_body, t ? glass_shadow_x360 : "", glass_ps_end);
    }
    return result;
}

// ---- R28 tree shading (NFS_TREES=pc|x360, default PC; F10 World > Tree shading, live) ----
// X360 billboardshader (trees.fx, effect index 1) technique standard/p0: VS 09BF5164, PS 09BF6634 (translated at
// build time, as R19). Replaces PC effect 6 IDI_TREE_FX (t0 vs_1_1 / ps_1_1) for records reaching 006C8000
// from 006D7685 / 006E03F9 whose material (mesh entry +0x60) has X360 Params (trees_params.inc: treecards, treeleaves);
// any other tree material keeps PC. Kept exactly: N = normalize(pos - LocalCentre), light = saturate(saturate(N.L) +
// Params.w) * DiffuseColour * Params.y, PS (light + Params.x) * diffuse * 2 * extinction + inscatter, view-depth fog
// capped at kMaxDist. X360 8244F680 draws a tree twice (when 82A2CFBC == 0): pass 0 alpha test >= ref with depth write,
// pass 1 SRCALPHA/INVSRCALPHA, alpha test < ref, no depth write (ref = layout+0x88, treeleaves 180, treecards 100; PC draws once,
// > 95); render.m draws both when the draw is replaced (x360 only), ref from trees_params.inc.
MatTreesDraw mat_trees_draw;
static int trees_mode = -1;
static unsigned trees_blended, trees_seen, trees_replaced, trees_skips[TREES_SKIP_COUNT], trees_shader_fail, trees_pipeline_fail;
static char trees_unmatched[4][32];     // first distinct unmatched effect-6 material names this interval (diag)
int mat_trees_enabled(void) {
    int postfx_look_variant(const char *v);
    if (!X360_SHADER_LITS) return 0;
    if (trees_mode < 0) { const char *s = getenv("NFS_TREES"); trees_mode = !(s && !strcmp(s, "pc")); }
    if (postfx_look_variant("treespc")) return 0;
    if (postfx_look_variant("treesx360")) return 1;
    return trees_mode;
}
void materials_set_trees(int x360) {
    trees_mode = x360 != 0;
    if (diag_on) diag_event("materials: tree shading now %s", trees_mode ? "x360" : "pc");
}
void materials_trees_clear(void) {
    if (diag_on && mat_trees_draw.mesh && !mat_trees_draw.emitted) ++trees_skips[mat_trees_draw.skip];
    memset(&mat_trees_draw, 0, sizeof mat_trees_draw);
}
void materials_trees_record(uint32_t q) {
    materials_trees_clear();
    uint32_t mesh = q ? M32(q) : 0, effect = q ? M32(q + 16) : 0, view = M32(CUR_VIEW);
    if (!mesh || !effect || M32(effect + 4) != 6) return;      // effect 6 = IDI_TREE_FX "billboardshader" (008F9BE8 table)
    if (diag_on) ++trees_seen;
    mat_trees_draw.mesh = mesh; mat_trees_draw.skip = TREES_UNMATCHED;
    uint32_t name = M32(mesh + 0x60);
    const char *s = name ? (const char *)(G + name) : NULL;
    const float *p = trees_params(s);
    if (!p) {
        if (diag_on && s) for (int i = 0; i < 4; ++i) {
            if (!strncmp(trees_unmatched[i], s, sizeof trees_unmatched[i] - 1)) break;
            if (!trees_unmatched[i][0]) { snprintf(trees_unmatched[i], sizeof trees_unmatched[i], "%s", s); break; }
        }
        return;
    }
    mat_trees_draw.skip = TREES_VIEW;
    uint32_t world = M32(q + 28), vid = view ? M32(view + 4) : 99;
    if (!view || !(!M32(view) || (M32(view) == 3 && vid == 3)) || !world || vid >= 16) return;
    // 006C8000 multiplies by the view's alternative camera (+C0) when the bound TextureInfo's +50 flag is set (as R19).
    uint32_t v = M32(0x009195E0u + vid * 0x70u), ti = M32(0x00982CB4u), off = ti && G[ti + 0x50] ? 0xC0 : 0x40;
    if (!v) return;
    const float *w = (const float *)(G + world);
    for (int col = 0; col < 3; ++col) for (int row = 0; row < 4; ++row) {
        float sum = 0;
        for (int k = 0; k < 4; ++k) sum += w[row * 4 + k] * F32(v + off + 16 * k + 4 * col);
        mat_trees_draw.world_view[col][row] = sum;
    }
    // X360 8244F510: LocalCentre = centre of the mesh entry's bbox (+0 min, +0xC max), local space like PC's LocalEyePos.
    const float *b = (const float *)(G + mesh);
    for (int i = 0; i < 3; ++i) mat_trees_draw.centre[i] = (b[i] + b[3 + i]) * 0.5f;
    memcpy(mat_trees_draw.params, p, sizeof mat_trees_draw.params);
    mat_trees_draw.alpha_ref = trees_alpha_ref(s);
    mat_trees_draw.view = vid;
    mat_trees_draw.skip = TREES_NO_DRAW;
}
void mat_trees_emit(int blended) { mat_trees_draw.emitted = 1; ++trees_replaced; trees_blended += blended; }
void mat_trees_failure(int pipeline) {
    unsigned n = __atomic_add_fetch(pipeline ? &trees_pipeline_fail : &trees_shader_fail, 1, __ATOMIC_RELAXED);
    if (diag_on && n <= 8) diag_event("trees: %s compile failed (PC draw retained)", pipeline ? "pipeline" : "shader");
}
void mat_trees_frame(void) {
    materials_trees_clear();
    if (!diag_on) return;
    static uint64_t last;
    uint64_t now = host_ticks();
    if (!last) { last = now; return; }
    if (now - last < 1000000000ull) return;
    static const char *const reasons[] = {"no_draw", "unmatched", "pc", "view", "state", "constants", "shader", "pipeline"};
    char skipped[256] = "none", names[160] = "none"; size_t used = 0;
    for (int i = 0; i < TREES_SKIP_COUNT; ++i) if (trees_skips[i] && i != TREES_UNMATCHED)
        used += snprintf(skipped + used, sizeof skipped - used, "%s%s=%u", used ? "," : "", reasons[i], trees_skips[i]);
    used = 0;
    for (int i = 0; i < 4 && trees_unmatched[i][0]; ++i)
        used += snprintf(names + used, sizeof names - used, "%s%s", used ? "," : "", trees_unmatched[i]);
    diag_event("trees: dt %.2f mode %s seen %u replaced %u blended %u unmatched %u (%s) fallback %s compile_failed %u pipeline_failed %u",
        (now - last) * 1e-9, mat_trees_enabled() ? "x360" : "pc", trees_seen, trees_replaced, trees_blended, trees_skips[TREES_UNMATCHED], names, skipped,
        __atomic_exchange_n(&trees_shader_fail, 0, __ATOMIC_RELAXED), __atomic_exchange_n(&trees_pipeline_fail, 0, __ATOMIC_RELAXED));
    trees_seen = trees_replaced = trees_blended = 0; memset(trees_skips, 0, sizeof trees_skips); memset(trees_unmatched, 0, sizeof trees_unmatched); last = now;
}
// k = the X360 VS registers: 0-3 WorldViewProj, 4 ScreenOffset (0: the port's aux.fixup), 5-9 fog, 10 kMaxDist,
// 11 Fog_DistanceScale, 12-14 WorldView, 15 Params, 16 LightDirVec, 17 LocalCentre, 18 DiffuseColour, 19 LocalEyePos;
// 20.x NFS_TEST_TREES_TINT, 20.y 1 in the blended second pass. PS 09BF6634's Params (its c0) is read from k[15] of the same buffer.
int mat_trees_constants(const SmResult *pcvs, const float (*vsc)[4], float out[32][4]) {
    if (!mat_trees_draw.mesh) return 0;
    memset(out, 0, 32 * 16);
    static const struct { const char *name; int dst, count; } copy[] = {
        {"WorldViewProj",0,4}, {"Fog_Br_Plus_Bm",5,1}, {"Fog_One_Over_BrBm",6,1}, {"Fog_Const_1",7,1}, {"Fog_Const_2",8,1},
        {"Fog_Const_3",9,1}, {"FogValue",11,1}, {"LocalLightVec",16,1}, {"DiffuseColour",18,1}, {"LocalEyePos",19,1}
    };
    for (size_t i = 0; i < sizeof copy / sizeof *copy; ++i) {
        int reg = grass_reg(pcvs, copy[i].name, copy[i].count);
        if (reg < 0 || reg + copy[i].count > 256) return 0;
        memcpy(out[copy[i].dst], vsc[reg], copy[i].count * 16);
    }
    out[10][0] = X360F_KMAXDIST_TREES;                                   // kMaxDist; Fog_DistanceScale (c11) = PC FogValue
    out[16][3] = 0;                                      // float3 light: X360 dots all four components
    memcpy(out[12], mat_trees_draw.world_view, 48);      // WorldView rows from the matrices 006C8000 multiplies
    memcpy(out[15], mat_trees_draw.params, 16);
    memcpy(out[17], mat_trees_draw.centre, 12);
    static int tint = -1;
    if (tint < 0) { const char *s = getenv("NFS_TEST_TREES_TINT"); tint = s && *s && strcmp(s, "0"); }
    out[20][0] = (float)tint;
    return 1;
}
static const char trees_common_msl[] =
"#include <metal_stdlib>\n"
"using namespace metal;\n"
"struct TreesConstants { float4 k[32]; };\n"
"struct TreesOut { float4 pos [[position]];\n"
"float4 t0 [[user(trees_t0)]]; float4 t1 [[user(trees_t1)]]; float4 t2 [[user(trees_t2)]]; float4 t3 [[user(trees_t3)]]; };\n"
"#define XMAX 3.402823466e+38f\n";
// 09BF5164 (oColor0 is written but PS 09BF6634 never reads it: dropped).
static const char trees_vs_body[] =
"const float4 c255 = " X360L_09BF5164_C255 ";\n"
"float4 oPos=0, oTexCoord0=0, oTexCoord1=0, oTexCoord2=0, oTexCoord3=0;\n"
"float4 r0=0, r1=0, r2=0, r3=0, r4=0, r5=0, r6=0, r7=0; float ps=0;\n"
X360B_TREES_VS_BODY
"TreesOut o; o.pos = oPos; o.pos.xy += aux.fixup.xy * oPos.w;\n"
"o.t0 = oTexCoord0; o.t1 = oTexCoord1; o.t2 = oTexCoord2; o.t3 = oTexCoord3;\n"
"return o;\n"
"}\n";
// 09BF6634; alpha test stays the PC draw's render state (as on X360, a D3D state, not shader code).
static const char trees_ps_body[] =
"float4 r0 = i.t0, r1 = i.t1, r2 = i.t2, r3 = i.t3, r4 = 0;\n"
"float ps = 0; float4 oC0 = 0;\n"
X360B_TREES_PS_BODY
"if (aux.alpha_func != 0u && !trees_alpha_test(aux.alpha_func, oC0.w, aux.alpha_ref)) discard_fragment();\n"
"if (g.k[20].x != 0) { oC0.xyz = g.k[20].y != 0 ? float3(0, 1, 1) : float3(1, 0, 1); if (g.k[20].y != 0) oC0.w = 1; }   // NFS_TEST_TREES_TINT: pass 0 magenta, blended pass 1 cyan (opaque)\n"
"return oC0;\n"
"}\n";
char *mat_trees_shader(const uint32_t *tokens, uint32_t n, const SmResult *pc, int stage, uint16_t input_int, uint16_t input_uint) {
    uint64_t h = 1469598103934665603ull;
    const uint8_t *b = (const uint8_t *)tokens;
    for (size_t i = 0; i < 4 * (size_t)n; ++i) h = (h ^ b[i]) * 1099511628211ull;
    char *result = NULL;
    if (stage == SM_VERTEX) {
        if (h != 0x96681B7C39F397A7ull) return NULL;        // tree t0 vs_1_1 (host/shaders/testdata/tree)
        int reg[2] = {-1, -1};
        const int usage[2] = {SM_USAGE_POSITION, SM_USAGE_TEXCOORD};
        char attr[1024] = "struct TreesAttr {\n";
        size_t at = strlen(attr);
        for (int k = 0; k < pc->n_inputs; ++k) {
            const SmInput *in = &pc->inputs[k];
            for (int i = 0; i < 2; ++i) if (in->usage == usage[i] && !in->usage_index) reg[i] = in->reg;
            const char *ty = (input_int & (1u << in->reg)) ? "int4" : (input_uint & (1u << in->reg)) ? "uint4" : "float4";
            at += snprintf(attr + at, sizeof attr - at, "%s a%d [[attribute(%d)]];\n", ty, in->reg, in->reg);
        }
        for (int i = 0; i < 2; ++i) if (reg[i] < 0) return NULL;
        snprintf(attr + at, sizeof attr - at, "};\nstruct TreesAux { float4 fixup; float point_size; float3 pad; };\n");
        char entry[512];
        snprintf(entry, sizeof entry, "vertex TreesOut sm_main(TreesAttr i [[stage_in]], constant TreesConstants &g [[buffer(20)]], constant TreesAux &aux [[buffer(19)]]) {\n"
            "float4 iPosition0=float4(i.a%d), iTexCoord0=float4(i.a%d);\n", reg[0], reg[1]);
        asprintf(&result, "%s%s%s%s", trees_common_msl, attr, entry, trees_vs_body);
    } else if (stage == SM_PIXEL) {
        if (h != 0x460871E26A412F77ull) return NULL;        // tree t0 ps_1_1
        static const char head[] =
            "struct TreesPSAux { uint alpha_func; float alpha_ref; uint pad0, pad1; float4 bumpenv[8]; float4 bumplum[8]; float lodbias[16]; };\n"
            "static inline bool trees_alpha_test(uint f, float a, float ref) {\n"
            "    float qa = rint(saturate(a) * 255.0f), qr = rint(saturate(ref) * 255.0f);\n"
            "    switch (f) { case 1: return false; case 2: return qa < qr; case 3: return qa == qr; case 4: return qa <= qr;\n"
            "    case 5: return qa > qr; case 6: return qa != qr; case 7: return qa >= qr; default: return true; }\n}\n"
            "fragment float4 sm_main(TreesOut i [[stage_in]], constant TreesConstants &g [[buffer(5)]], constant TreesPSAux &aux [[buffer(3)]],\n"
            " texture2d<float> tex0 [[texture(0)]], sampler smp0 [[sampler(0)]]) {\n";
        asprintf(&result, "%s%s%s", trees_common_msl, head, trees_ps_body);
    }
    return result;
}

// ---- X360 worldbone on the X360 arrest characters (NFS_X360_ARREST=x360 only) ----
// X360 15_worldbone world/p0: VS 09c05e24, PS 09c07860 (XenosRecomp MIT). The
// converted arrest bundles (tools_snapshot/x360_nis_arrest.py) carry the X360 draw's five texture slots, so 006c6d10
// caches the normal map in 00982CB8 and the specular in 00982CC0; a PC worldbone (effect 2) draw whose normal slot is a
// distinct texture gets the X360 shader, any other (hair, cuffs: no map, no tangents) and every draw with `pc` keep PC.
// Kept from the X360: N and T rotated by the first palette bone, V from the skinned position, COLOR0 = DiffuseMin + nv
// DiffuseRange (only .w read), COLOR1 = light-0 Phong x COLOR0.w x (SpecularMin + nv^SP SpecularRange) and .w = 2 (EnvmapMin
// + EnvmapRange nv^EnvmapPower), the env coordinate 2 nv N_bind - V in view space; PS n = normalize4(2 tex - 1) (dp4), three
// tangent-space lights x LocalColourMatrix, + COLOR1 x g_fSpecMapScale 3.0 x spec map + COLOR1.w COLOR0.w env, x
// STANDARD_BRIGHTNESS 2; alpha 2 COLOR0.w diffuse.a. Port differences: tangent frame from the uv derivatives (decoded equal to
// the X360 mesh tangents: T = +d/du, B = +d/dv), WorldView rotation from WorldViewProj rows 0, 1, 3 (the PC worldbone VS
// has no WorldView), light 2's direction from the light context 006C7420 gets (the PC VS uploads lights 0-1 only), and the
// env cube is the last car draw's stage-1 cube [I: ENVIROMAP for worldbone assumed the car's env map].
static float bone_ldm[4][4];              // LocalDirectionMatrix as stored at the last 006C7420: light i = column i (D3DX uploads
                                          // the transpose: c71 = column 0, c72 = column 1, checked per draw; FF path, same order)
static uint32_t bone_cube;                 // guest cube texture of the last car draw's sampler 1
static unsigned bone_seen, bone_nmap, bone_replaced, bone_skips[BONE_SKIP_COUNT], bone_shader_fail, bone_pipeline_fail, bone_ldm_bad;
void f_006C7420_orig(Cpu *c);
void f_006C7420(Cpu *c) {                 // (light context *p, matrix): p+0x10 LocalColourMatrix, p+0x50 LocalDirectionMatrix
    uint32_t p = arg(c, 0);
    if (p && !M32(p)) memcpy(bone_ldm, G + p + 0x50, sizeof bone_ldm);   // p+0x50..0x8f
    f_006C7420_orig(c);
}
void mat_bone_car_cube(uint32_t tex) {
    uint32_t fx = M32(CUR_EFFECT);
    if (fx && M32(fx + 4) == 4) bone_cube = tex;
}
int mat_bone_draw(uint32_t tex[3], int *skip) {
    int x360_arrest_enabled(void);
    uint32_t fx = M32(CUR_EFFECT);
    if (!fx || M32(fx + 4) != 2) return 0;   // effect 2 = IDI_WORLDBONE_FX (008F9BE8 table)
    if (diag_on) ++bone_seen;
    uint32_t dti = M32(0x00982CB4u), nti = M32(0x00982CB8u);
    if (!nti || nti == dti) return 0;
    if (diag_on) ++bone_nmap;
    *skip = BONE_PC;
    if (!X360_SHADER_LITS || !X360_ENGINE_CONSTS || !x360_arrest_enabled()) return 0;
    *skip = BONE_TEXTURE;
    tex[0] = ti_d3d(nti); tex[1] = ti_d3d(M32(0x00982CC0u)); tex[2] = bone_cube;
    return tex[0] && tex[1];
}
void mat_bone_skip(int reason) { if (diag_on) ++bone_skips[reason]; }
void mat_bone_emit(void) { ++bone_replaced; }
void mat_bone_failure(int pipeline) {
    unsigned n = __atomic_add_fetch(pipeline ? &bone_pipeline_fail : &bone_shader_fail, 1, __ATOMIC_RELAXED);
    if (diag_on && n <= 8) diag_event("arrestnm: %s compile failed (PC draw retained)", pipeline ? "pipeline" : "shader");
}
void mat_bone_frame(void) {
    if (!diag_on) return;
    static uint64_t last;
    uint64_t now = host_ticks();
    if (!last) { last = now; return; }
    if (now - last < 1000000000ull) return;
    static const char *const reasons[] = {"no_draw", "pc", "texture", "state", "shader", "pipeline"};
    char skipped[256] = "none"; size_t used = 0;
    for (int i = 1; i < BONE_SKIP_COUNT; ++i) if (bone_skips[i])
        used += snprintf(skipped + used, sizeof skipped - used, "%s%s=%u", used ? "," : "", reasons[i], bone_skips[i]);
    if (bone_seen) diag_event("arrestnm: dt %.2f worldbone draws %u with a normal map %u replaced %u fallback %s ldm_mismatch %u env_cube %08X "
        "compile_failed %u pipeline_failed %u", (now - last) * 1e-9, bone_seen, bone_nmap, bone_replaced, skipped, bone_ldm_bad, bone_cube,
        __atomic_exchange_n(&bone_shader_fail, 0, __ATOMIC_RELAXED), __atomic_exchange_n(&bone_pipeline_fail, 0, __ATOMIC_RELAXED));
    bone_seen = bone_nmap = bone_replaced = bone_ldm_bad = 0; memset(bone_skips, 0, sizeof bone_skips); last = now;
}
// k[0..81] = the PC worldbone VS constants c0-c81 as uploaded (BlendMatrices c0, WorldViewProj c64, LocalColourMatrix c68,
// LocalDirectionMatrix lights 0-1 c71, LocalEyePos c73, DiffuseMin/Range c74-75, SpecularMin/Range c76-77, SpecularPower c78,
// EnvmapPower c79, EnvmapMin/Range c80-81); k[82] light 2's direction (the context's column 2); k[83] = (g_fSpecMapScale 3, STANDARD_BRIGHTNESS
// 2, NFS_TEST_ARRESTNM_TINT, 0). The PS reads the same buffer.
int mat_bone_constants(const float (*vsc)[4], float out[MAT_BONE_K][4]) {
    memset(out, 0, MAT_BONE_K * 16);
    memcpy(out, vsc, 82 * 16);
    int bad = 0;
    for (int r = 0; r < 3; ++r) bad |= fabsf(bone_ldm[r][0] - vsc[71][r]) > 1e-4f || fabsf(bone_ldm[r][1] - vsc[72][r]) > 1e-4f;
    if (diag_on && bad && !bone_ldm_bad++) diag_event("arrestnm: light context differs from c71-72: c71 %g %g %g ctx %g %g %g", vsc[71][0], vsc[71][1], vsc[71][2],
                                                      bone_ldm[0][0], bone_ldm[1][0], bone_ldm[2][0]);
    if (bad) memset(out[82], 0, 16);      // not this draw's context: light 2 off rather than another object's
    else for (int r = 0; r < 4; ++r) out[82][r] = bone_ldm[r][2];
    if (diag_on) { static unsigned n; if (!n++ || n % 4000 == 0) diag_event("arrestnm: light 2 dir %g %g %g colour %g %g %g (light 0 colour %g %g %g)",
        out[82][0], out[82][1], out[82][2], vsc[68][2], vsc[69][2], vsc[70][2], vsc[68][0], vsc[69][0], vsc[70][0]); }
    static int tint = -1;
    if (tint < 0) { const char *s = getenv("NFS_TEST_ARRESTNM_TINT"); tint = s && *s && strcmp(s, "0"); }
    out[83][0] = X360F_SPECMAPSCALE;      // g_fSpecMapScale: the 15_worldbone PS's effect default (generated), no setter
    out[83][1] = X360E_STANDARD_BRIGHTNESS;   // Brightness = STANDARD_BRIGHTNESS (828FCC18, see R23 above)
    out[83][2] = (float)tint;
    return 1;
}
static const char bone_common_msl[] =
"#include <metal_stdlib>\n"
"using namespace metal;\n"
"struct BoneConstants { float4 k[96]; };\n"
"struct BoneOut { float4 pos [[position]];\n"
"float4 uv [[user(bone_uv)]]; float4 p [[user(bone_p)]]; float4 n [[user(bone_n)]]; float4 env [[user(bone_env)]];\n"
"float4 c0 [[user(bone_c0)]]; float4 c1 [[user(bone_c1)]]; };\n"
"static inline float bpow(float x, float e) { return x > 0.0f ? exp2(e * log2(x)) : 0.0f; }\n";
// 09c05e24, the PC VS's skinning kept for the position (same depth as the PC pass would give).
static const char bone_vs_body[] =
"float4 v0 = float4(iPosition0.xyz, 1.0f);\n"
"float3 ix = floor(iBlendIndices0.xyz) * 4.0f;\n"
"int a = clamp(int(ix.x), 0, 60), b = clamp(int(ix.y), 0, 60), c = clamp(int(ix.z), 0, 60);\n"
"float4 sk = float4(dot(v0, g.k[b]), dot(v0, g.k[b + 1]), dot(v0, g.k[b + 2]), dot(v0, g.k[b + 3])) * iBlendWeight0.y\n"
"          + float4(dot(v0, g.k[a]), dot(v0, g.k[a + 1]), dot(v0, g.k[a + 2]), dot(v0, g.k[a + 3])) * iBlendWeight0.x\n"
"          + float4(dot(v0, g.k[c]), dot(v0, g.k[c + 1]), dot(v0, g.k[c + 2]), dot(v0, g.k[c + 3])) * iBlendWeight0.z;\n"
"BoneOut o;\n"
"o.pos = float4(dot(sk, g.k[64]), dot(sk, g.k[65]), dot(sk, g.k[66]), dot(sk, g.k[67]));\n"
"o.pos.xy += aux.fixup.xy * o.pos.w;\n"
"float3 nb = iNormal0.xyz;\n"
"float3 N = float3(dot(g.k[a].xyz, nb), dot(g.k[a + 1].xyz, nb), dot(g.k[a + 2].xyz, nb));   // first palette bone (X360 a0 = index.x)\n"
"float3 V = normalize(g.k[73].xyz - sk.xyz);\n"
"float nv = max(dot(V, N), " X360S_BONE_NV_MIN "f);\n"
"float4 c0 = g.k[74] + nv * g.k[75];\n"
"float3 L0 = g.k[71].xyz;\n"
"float rv = saturate(dot(2.0f * dot(N, L0) * N - L0, V));\n"
"float sp = g.k[78].x;\n"
"float3 sm = g.k[76].xyz + bpow(nv, sp) * g.k[77].xyz;\n"
"o.c1 = float4(float3(g.k[68].x, g.k[69].x, g.k[70].x) * bpow(rv, sp) * c0.w * sm, 2.0f * g.k[80].x + 2.0f * g.k[81].x * bpow(nv, g.k[79].x));\n"
"o.c0 = c0;\n"
"float3 re = 2.0f * nv * nb - V;                      // the X360 reflects the bind-pose normal here\n"
"float3 Z = normalize(g.k[67].xyz), X = normalize(g.k[64].xyz - dot(g.k[64].xyz, Z) * Z), Y = normalize(g.k[65].xyz - dot(g.k[65].xyz, Z) * Z);\n"
"o.env = float4(dot(re, X), dot(re, Y), dot(re, Z), 1.0f);   // WorldView[0..2] . R (rotation from WorldViewProj)\n"
"o.uv = iTexCoord0; o.p = float4(sk.xyz, 1.0f); o.n = float4(N, 0.0f);\n"
"return o;\n"
"}\n";
// 09c07860; alpha test stays the PC draw's render state.
static const char bone_ps_body[] =
"float2 uv = i.uv.xy;\n"
"float4 dif = tex0.sample(smp0, uv, bias(aux.lodbias[0]));\n"
"float4 nm = nmap.sample(smp0, uv, bias(aux.lodbias[0]));\n"
"float3 spm = smap.sample(smp0, uv, bias(aux.lodbias[0])).xyz;\n"
"float3 env = ecube.sample(smp0, i.env.xyz).xyz;\n"
"float4 n4 = 2.0f * nm - 1.0f;\n"
"float3 n = n4.xyz * rsqrt(max(dot(n4, n4), 1e-12f));    // dp4: the map's alpha takes part, as on the X360\n"
"float3 P = i.p.xyz, N = normalize(i.n.xyz);\n"
"float3 dp1 = dfdx(P), dp2 = dfdy(P); float2 du1 = dfdx(uv), du2 = dfdy(uv);\n"
"float3 T = dp1 * du2.y - dp2 * du1.y, B = dp2 * du1.x - dp1 * du2.x;\n"
"T *= (du1.x * du2.y - du2.x * du1.y) < 0.0f ? -1.0f : 1.0f;   // T = d/du, B = d/dv even on mirrored uv\n"
"T -= N * dot(N, T);\n"
"T = length(T) > 1e-12f ? normalize(T) : normalize(cross(N, abs(N.x) < 0.9f ? float3(1, 0, 0) : float3(0, 1, 0)));\n"
"float3 Bn = cross(N, T); Bn = dot(Bn, B) < 0.0f ? -Bn : Bn;\n"
"float3 L0 = g.k[71].xyz, L1 = g.k[72].xyz, L2 = g.k[82].xyz;\n"
"float3 t0 = float3(dot(T, L0), dot(Bn, L0), dot(N, L0)), t1 = float3(dot(T, L1), dot(Bn, L1), dot(N, L1)), t2 = float3(dot(T, L2), dot(Bn, L2), dot(N, L2));\n"
"t0 *= rsqrt(max(dot(t0, t0), 1e-12f)); t1 *= rsqrt(max(dot(t1, t1), 1e-12f)); t2 *= rsqrt(max(dot(t2, t2), 1e-12f));\n"
"float3 d = saturate(float3(dot(n, t0), dot(n, t1), dot(n, t2)));\n"
"float3 light = float3(dot(d, g.k[68].xyz), dot(d, g.k[69].xyz), dot(d, g.k[70].xyz));\n"
"float4 oC0;\n"
"oC0.xyz = (light * dif.xyz + i.c1.xyz * g.k[83].x * spm + i.c1.w * i.c0.w * env) * g.k[83].y;\n"
"oC0.w = 2.0f * i.c0.w * dif.w;\n"
"if (g.k[83].z != 0) oC0.xyz = float3(1, 0, 1);   // NFS_TEST_ARRESTNM_TINT\n"
"if (aux.alpha_func != 0u && !bone_alpha_test(aux.alpha_func, oC0.w, aux.alpha_ref)) discard_fragment();\n"
"return oC0;\n"
"}\n";
char *mat_bone_shader(const uint32_t *tokens, uint32_t n, const SmResult *pc, int stage, uint16_t input_int, uint16_t input_uint) {
    uint64_t h = 1469598103934665603ull;
    const uint8_t *bt = (const uint8_t *)tokens;
    for (size_t i = 0; i < 4 * (size_t)n; ++i) h = (h ^ bt[i]) * 1099511628211ull;
    char *result = NULL;
    if (stage == SM_VERTEX) {
        if (h != 0x112427A3B5E74D1EULL) return NULL;        // worldbone t0 vs_1_1 (host/shaders/testdata/worldbone)
        int reg[5] = {-1, -1, -1, -1, -1};
        const int usage[5] = {SM_USAGE_POSITION, SM_USAGE_NORMAL, SM_USAGE_TEXCOORD, SM_USAGE_BLENDWEIGHT, SM_USAGE_BLENDINDICES};
        char attr[1024] = "struct BoneAttr {\n";
        size_t at = strlen(attr);
        for (int k = 0; k < pc->n_inputs; ++k) {
            const SmInput *in = &pc->inputs[k];
            for (int i = 0; i < 5; ++i) if (in->usage == usage[i] && !in->usage_index) reg[i] = in->reg;
            const char *ty = (input_int & (1u << in->reg)) ? "int4" : (input_uint & (1u << in->reg)) ? "uint4" : "float4";
            at += snprintf(attr + at, sizeof attr - at, "%s a%d [[attribute(%d)]];\n", ty, in->reg, in->reg);
        }
        for (int i = 0; i < 5; ++i) if (reg[i] < 0) return NULL;
        snprintf(attr + at, sizeof attr - at, "};\nstruct BoneAux { float4 fixup; float point_size; float3 pad; };\n");
        char entry[640];
        snprintf(entry, sizeof entry, "vertex BoneOut sm_main(BoneAttr i [[stage_in]], constant BoneConstants &g [[buffer(20)]], constant BoneAux &aux [[buffer(19)]]) {\n"
            "float4 iPosition0=float4(i.a%d), iNormal0=float4(i.a%d), iTexCoord0=float4(i.a%d), iBlendWeight0=float4(i.a%d), iBlendIndices0=float4(i.a%d);\n",
            reg[0], reg[1], reg[2], reg[3], reg[4]);
        asprintf(&result, "%s%s%s%s", bone_common_msl, attr, entry, bone_vs_body);
    } else if (stage == SM_PIXEL) {
        if (h != 0xFD2B54560A2564FFULL) return NULL;        // worldbone t0 ps_1_1
        static const char head[] =
            "struct BonePSAux { uint alpha_func; float alpha_ref; uint pad0, pad1; float4 bumpenv[8]; float4 bumplum[8]; float lodbias[16]; };\n"
            "static inline bool bone_alpha_test(uint f, float a, float ref) {\n"
            "    float qa = rint(saturate(a) * 255.0f), qr = rint(saturate(ref) * 255.0f);\n"
            "    switch (f) { case 1: return false; case 2: return qa < qr; case 3: return qa == qr; case 4: return qa <= qr;\n"
            "    case 5: return qa > qr; case 6: return qa != qr; case 7: return qa >= qr; default: return true; }\n}\n"
            "fragment float4 sm_main(BoneOut i [[stage_in]], constant BoneConstants &g [[buffer(5)]], constant BonePSAux &aux [[buffer(3)]],\n"
            " texture2d<float> tex0 [[texture(0)]], sampler smp0 [[sampler(0)]], texture2d<float> nmap [[texture(12)]],\n"
            " texture2d<float> smap [[texture(13)]], texturecube<float> ecube [[texture(14)]]) {\n";
        asprintf(&result, "%s%s%s", bone_common_msl, head, bone_ps_body);
    }
    return result;
}
