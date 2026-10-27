// x360_post.m - X360 post-processing applied to the PC game.
//
// X360 post-processing (plan milestones M4-M6): (the default;
// NFS_POSTFX=pc keeps PC's chain) the PC
// visual treatment FUN_006d53d0 is replaced by the X360 composite (render_x360_post in render_post.m): auto-exposure with
// X360 adaptation, bright pass and bloom, the polynomial tone curve from the BlackBloomCurve, radial speed blur,
// depth of field when the camera sets a focal distance, vignette and exposure. PC's own motion blur (FUN_006dbb20)
// and overbright (FUN_006dc040) are skipped then, as on X360.
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <math.h>

#include "x360_internal.h"
#include "settings.h"
#include "gen/x360_shader_lits.inc"   // X360_SHADER_LITS: 0 without the X360 shaders' constants (render_post.m fs_vt): PC chain
#include "gen/x360_engine.inc"       // X360E_*: the X360 engine's post constants from the player's default.xex (0: PC chain)

#define VT_PLAT          0x00982AF0u   // pVisualTreatmentPlat (IVisualTreatment fields, layout shared with X360)
#define VT_TECHNIQUE     0x0093DEB8u   // the VT effect wrapper; [10] = technique (1 = the two-pass ps_1_1 fallback)
#define VT_PINGPONG      0x00982C60u
#define SCREEN_TEXTURES  0x0093DEF8u   // [2] screen copies (the VT input; the rain-on-lens pass draws into them)
#define SCREEN_SURFACES  0x0093DE60u
#define BACKBUFFER_SURF  0x00982A28u
#define RT_ENTRY_CUR     0x00982A20u   // current RT table entry: +8 colour, +0xC depth, +0x14 w, +0x18 h
#define D3D_DEVICE       0x00982BDCu
#define CAMERA_PTR       0x00919690u   // eViews[1] + 0x40: Camera (+0xB4 focal, +0xB8 DOF, +0xBC near, +0x200 velocity)
#define PLAYER_VIEW      0x009842D0u   // player eViewPlatInfo: +0x00 view matrix (row vectors)
#define MOTION_BLUR_ON   0x009017DCu   // g_MotionBlurEnable
#define FN_VT_EPILOGUE   0x006E2F50u
#define REAL_TIME_ELAPSED 0x009259BCu  // RealTimeElapsed: seconds of real time this main-loop pass (PrepareRealTimestep)
static int postfx = -1;                // 1 = X360 chain
static float adapted = X360E_ADAPT_START, measured = X360E_ADAPT_START;
static int reset_adaptation = 1, measured_real;   // measured_real: a luminance has been read back

// ---- Look settings: bloom amount and colour filter ----
// Settings (settings.h, F10 > Graphics): graphics.bloom (0-200 %, 100 = the subtle realistic default, see BLOOM_GAIN) and
// graphics.colour_filter (0 natural .. 100 the full visuallook grade, default 0 since 2026-10-21), sliders in steps of 10, saved in
// app.txt and read every frame, so a change shows on the next frame; NFS_BLOOM / NFS_COLOUR_FILTER=0..100 override.
// X360 chain: bloom scales g_fBloomScale (X360E_BLOOM_SCALE); the colour filter moves the visuallook tint to its own luminance
// (grey), the desaturation (a saturation factor) to 1 and both tone curves to a constant that cancels the exposure,
// so Natural is the scene as the game drew it (the reference: PC with its look treatment off): no cast, grey stays
// grey, white stays white, colours keep their chroma; vignette, speed blur, DOF and bloom stay. PC chain
// (NFS_POSTFX=pc): the same on the VT fields its treatment reads (tint, intensities, desaturation, the curves its
// SPLINE LUT is built from), and bloom scales PC's overbright BLENDFACTOR.
#define PC_CURVE_TREF  0.4f            // PC chain: the curves' input (bloom luminance) of a typical scene, for flat curves
static Setting s_bloom = {.id = "graphics.bloom", .category = SET_PICTURE, .order = 55, .label = "Bloom strength",
    .help = "How strongly lights, glints and the sun glow, never the sky as a whole; 100% is the default and 200% twice as strong.", .type = SET_SLIDER,
    .min = 0, .max = 200, .step = 10, .format = "%d%%", .def = 100, .env = "NFS_BLOOM"};
static Setting s_colour = {.id = "graphics.colour_filter", .category = SET_PICTURE, .order = 70, .label = "Colour filter",
    .help = "The game's sepia grade: 100% is the original look and 0% shows natural colours, the scene as drawn.",
    .type = SET_SLIDER, .min = 0, .max = 100, .step = 10, .format = "%d%%", .def = 0, .env = "NFS_COLOUR_FILTER"};
__attribute__((constructor)) static void look_settings_register(void) { settings_add(&s_bloom); settings_add(&s_colour); }
// NFS_BLOOM_MASK=none|pc|x360: what the scene alpha means to the bloom source. Default none (2): every pixel may bloom and
// only the bright pass decides, as a camera or an eye does (cars' glints, headlights, lit signs, the sun), not the sky
                                                                                                                      
static int bloom_mask_pc(void) {
    static int on = -1;
    if (on < 0) { const char *e = getenv("NFS_BLOOM_MASK"); on = e && !strcmp(e, "x360") ? 0 : e && !strcmp(e, "pc") ? 1 : 2; }
    return on;
}
// NFS_TONE_CURVE=x360: the colour-bloom term follows the BlackBloomCurve (the X360's renderer); default pc: its own
// ColourBloomCurve, as PC's treatment (GetValueFromSpline 0073DAB0 on VT +0x50 for the LUT's G, FUN_006c3b20)
static int tone_curve_x360(void) {
    int x360_tone_curve(void);          // shared with the PC SPLINE hook, changed live by F10
    return x360_tone_curve();
}
// BOOT_DIAG_LOOK_AB=<variants> (diag): after each shot (BOOT_SHOT, simshots) the next passes are held (no tick, no
// real time) and draw the same picture once per variant, saved as <shot>_<variant>.png. A variant is b<pct> (bloom),
// c<pct> (colour filter), both (b50c0), or `old` (the chain before 2026-10-20's fixes: X360 bloom mask, X360 curve,
// bloom and colour 100), `x360look` (NFS_VISUALLOOK=x360), `raw` (the chain's input, untreated).
// `lampglow` / `lampglowoff`: selective police lamp emission On / Off, retaining the current Natural/Glow mode.
// BOOT_DIAG_LOOK_AB=1: old,b0,b25,b50,b100,c0,c50,c100.
static char look_vars[16][16];
static int look_nvars = -1, look_state, look_var;   // state 1 base shot taken, 2 the held pass draws variant look_var, 3 its shot set
static char look_base[512];
static int look_ab(void) {
    if (look_nvars < 0) {
        look_nvars = 0;
        const char *e = diag_on ? getenv("BOOT_DIAG_LOOK_AB") : NULL;
        if (e && !strcmp(e, "1")) e = "old,b0,b25,b50,b100,c0,c50,c100";
        for (const char *p = e; p && *p && look_nvars < 16;) {
            size_t n = strcspn(p, ",");
            if (n && n < sizeof look_vars[0]) { memcpy(look_vars[look_nvars], p, n); look_vars[look_nvars++][n] = 0; }
            p += n + (p[n] == ',');
        }
    }
    return look_nvars > 0;
}
// this pass's settings: the held variant's when one is drawn, else the controls'
static int look_var_is(const char *v) { return look_state == 2 && !strcmp(look_vars[look_var], v); }
int postfx_look_variant(const char *v) { return look_var_is(v); }   // lighting.c: `wlorig` / `wlneutral`
static const char *var_num(const char *v, char key) {   // the digits after `key` in a variant ("b50c0"), or NULL
    for (; *v; ++v) if (*v == key && v[1] >= '0' && v[1] <= '9') return v + 1;
    return NULL;
}
static int look_old(void) { return look_var_is("old"); }
static float bloom_amount(void) {
    if (look_state == 2) {
        const char *b = var_num(look_vars[look_var], 'b');
        if (b) return (float)atoi(b) / 100.0f;
        if (look_old()) return 1.0f;
    }
    return (float)s_bloom.value / 100.0f;
}
// The bright pass subtracts this from colour x middle gray / adapted (the X360's, X360E_BRIGHT_THRESH, is tuned for its scenes at an
// adapted luminance near its middle gray; the PC scenes adapt about twice as bright, so it asks for values above ~1.0:
// nothing blooms). Variant t<pct>.
#define BLOOM_THRESH 0.25f
#define BLOOM_GAIN   0.40f             // x g_fBloomScale at 100 %: the kernels sum to 4 and the X360's scale is X360E_BLOOM_SCALE
static float bloom_thresh(void) {
    if (look_state == 2) {
        const char *t = var_num(look_vars[look_var], 't');
        if (t) return (float)atoi(t) / 100.0f;
    }
    return BLOOM_THRESH;
}
static float bloom_gain(void) {          // variant g<pct>
    if (look_state == 2) { const char *g = var_num(look_vars[look_var], 'g'); if (g) return (float)atoi(g) / 100.0f; }
    return BLOOM_GAIN;
}
static float colour_amount(void) {
    if (look_state == 2) {
        const char *cc = var_num(look_vars[look_var], 'c');
        if (cc) return (float)atoi(cc) / 100.0f;
        if (look_old()) return 1.0f;
    }
    return (float)s_colour.value / 100.0f;
}
static float bez(const float P[4], float t) { float u = 1.0f - t; return u * u * u * P[0] + 3.0f * u * u * t * P[1] + 3.0f * u * t * t * P[2] + t * t * t * P[3]; }
// The colour filter on a look (tint rgb, CBI, desaturation, BBI, BlackBloomCurve .y x4, ColourBloomCurve .y x4):
// f = 1 leaves it, f = 0 is natural: the tint at its own luminance (grey), saturation 1, both curves flat, and their
// constant values scaled so the two terms add up to `gain` (with the chain's exposure that makes the output the scene
// as drawn: grey stays grey, white stays white, colours keep their chroma). Between, every parameter is interpolated.
// tref: the curves' input where the flat curves keep their split between the black and the colour term.
static void colour_filter(float look[14], float f, float tref, float gain) {
    if (f >= 1.0f) return;
    if (f < 0.0f) f = 0.0f;
    float tl = 0.2125f * look[0] + 0.7154f * look[1] + 0.0721f * look[2];   // the composite's LUM weights (Rec. 709, a public standard, not read from ZZDATA0 on purpose)
    float bbi = look[5], cbi = look[3];
    float cb = bez(look + 6, tref), cc = bez(look + 10, tref);
    float kb = (1.0f - bbi) + bbi * cb, kc = cbi * tl * cc, g = kb + kc;
    if (g > 1e-4f) {
        float sc = gain / g;
        cc *= sc;
        if (bbi > 1e-4f) cb = (sc * kb - (1.0f - bbi)) / bbi; else cc = tl * cbi > 1e-4f ? (gain - kb) / (cbi * tl) : cc;
    }
    for (int ch = 0; ch < 3; ++ch) look[ch] = tl + (look[ch] - tl) * f;
    look[4] = 1.0f + (look[4] - 1.0f) * f;
    for (int i = 0; i < 4; ++i) { look[6 + i] = cb + (look[6 + i] - cb) * f; look[10 + i] = cc + (look[10 + i] - cc) * f; }
}
// the VT fields of a look, in colour_filter's order (IVisualTreatment layout, shared by PC and X360)
static const uint32_t LOOK_OFFS[14] = {0xA0, 0xA4, 0xA8, 0x94, 0x98, 0x90, 0x14, 0x24, 0x34, 0x44, 0x54, 0x64, 0x74, 0x84};

// BOOT_DIAG_POSTFX_AB=1 (diag runs with BOOT_DIAG_SIMSHOTS): both chains on the same picture, for comparisons. The
// game shows PC's chain, and the X360 chain also runs every frame into a scratch target, so its luminance
// measurement and adaptation stay live. After each simshot the pass's real time is dropped (pacing.c), so the next
// pass runs no sim tick and no real time (camera, particles and the camera velocity stay as they were) and draws the
// same scene again, this time through the X360 chain; it is saved as simshot_<t>_x360.png. Its time is dropped too.
static int ab_var;                     // BOOT_DIAG_POSTFX_AB=3: extra held passes 1 X360 looks, 2 input, 3 t, 4 magenta, 5 bloom mask;
                                       // =4: one per test skip (1 << (ab_var - 1), below)
static int ab = -1, ab_state;          // 1 PC shot taken, 2 the held pass (X360 chain), 3 the held pass's shot taken
static char ab_path[512];
static int postfx_ab(void) {
    if (ab < 0) ab = diag_on && getenv("BOOT_DIAG_POSTFX_AB") ? atoi(getenv("BOOT_DIAG_POSTFX_AB")) : 0;
    return ab > 0;
}
// NFS_TEST_POST_SKIP=<stage>[,<stage>...] (test only): the X360 chain with single stages off, to measure what each one
                                                                                                                     
// added x4; curve: tone curve and desaturation (identity grade); curvelocal: the curve driven by the pixel's own
// luminance instead of the quarter-res (16x16 px) blurred one; vignette: the brightness and additive vignette (the
// vignette texture's blur masks stay); adapt: exposure fixed at the adaptation's equilibrium (adapted = middle gray,
// x1.99); all: the composite outputs its input (the resolved scene). With NFS_POSTFX=pc, motionblur skips PC's motion
// blur pass (FUN_006dbb20). smoothtaps: the speed blur in the X360's 7 taps at any resolution (render_post.m spreads
// more over the same length above 720p); scaledglow: the tone curve's luminance at the fixed 16 pixels instead of the
// X360's footprint scaled to the screen (NFS_CURVE_GLOW=tight). BOOT_DIAG_POSTFX_AB=4 shows all but motionblur in turn
// on the same frame (held passes after each shot's X360 pass: simshot_<t>_x360_no<stage>.png), one launch for a set.
enum { SKIP_SPEEDBLUR = 1, SKIP_DOF = 2, SKIP_BLOOM = 4, SKIP_CURVE = 8, SKIP_CURVELOCAL = 16, SKIP_VIGNETTE = 32, SKIP_ADAPT = 64,
       SKIP_ALL = 128, SKIP_MOTIONBLUR = 256, SKIP_SMOOTHTAPS = 512, SKIP_SCALEDGLOW = 1024 };
static const char *const skip_names[] = {"speedblur", "dof", "bloom", "curve", "curvelocal", "vignette", "adapt", "all", "motionblur",
                                         "smoothtaps", "scaledglow"};
static const int ab_stages[] = {0, 1, 2, 3, 4, 5, 6, 7, 9, 10};   // BOOT_DIAG_POSTFX_AB=4's held passes (skip_names index)
#define SKIP_AB_STAGES (int)(sizeof ab_stages / sizeof *ab_stages)
static int post_skip(void) {
    static int env = -1;
    if (env < 0) {
        env = 0;
        const char *e = getenv("NFS_TEST_POST_SKIP");
        for (const char *t = e; t && *t; t += strcspn(t, ","), t += *t == ',')
            for (int i = 0; i < (int)(sizeof skip_names / sizeof *skip_names); ++i)
                if (strlen(skip_names[i]) == strcspn(t, ",") && !strncmp(t, skip_names[i], strlen(skip_names[i]))) env |= 1 << i;
        if (e) fprintf(stderr, "boot: x360 post: test skips %s (0x%X)\n", e, env);
    }
    return ab == 4 && ab_state == 4 ? 1 << ab_stages[ab_var - 1] : env;
}
// NFS_SPEED_BLUR=off|x360|strong (F10 Graphics > Speed blur): the radial speed blur's length x0, x1 (the X360's), x1.5;
// its mask and speed ramp stay the X360's (render_post.m spreads its taps over that length by resolution).
// NFS_CURVE_GLOW=x360|tight|pixel (F10 Graphics > Tone curve glow): what the tone curve's luminance averages
// (render_post.m 4b): the X360's footprint scaled to the screen (default), a fixed 16 pixels (the port before
                                                                                                       
static int speed_blur = -1, curve_glow = -1;
static int env_choice(const char *name, const char *const *v, int n, int def) {
    const char *e = getenv(name);
    for (int i = 0; e && i < n; ++i) if (!strcmp(e, v[i])) return i;
    return def;
}
void x360_post_set_speed_blur(int mode) { speed_blur = mode; if (diag_on) diag_event("x360: speed blur mode now %d", mode); }   // the F10 menu (settings_port.c): from the next frame
void x360_post_set_curve_glow(int mode) { curve_glow = mode; if (diag_on) diag_event("x360: curve glow mode now %d", mode); }
                                                                                                               
// everywhere, so an area bigger than the bloom kernel (sun-lit snow cap, smoke, a lit road strip) is lifted by ~6x its
// bright-pass excess and clips flat; halo subtracts that area's own lift, so it keeps its texture and only the light
                                                                                                                        
// bloom source). Look variants `glow` / `halo` (BOOT_DIAG_LOOK_AB).
                                                                                                                       
// energy-conserving glare, scene + s (blur(B) - B) with B the near-white part of each pixel: a large bright area keeps
// its value and texture (no lift, no ring), light only moves from bright things into their surroundings. Variant `natural`.
static int bloom_surfaces = -1;
void x360_post_set_bloom_surfaces(int mode) { bloom_surfaces = mode; if (diag_on) diag_event("x360: bloom surfaces mode now %d", mode); }
#define GLARE_S 0.20f                  // natural glare strength at Bloom 100 %
// NFS_HIGHLIGHTS=rolloff|clip (F10 Graphics > Highlights; default rolloff): the post's added light fills the headroom above
// the pixel with a soft limit instead of clipping, and the car paint (materials.m) rolls its HDR result off with a
// shoulder before the 8-bit write. Variants `rolloff` / `clip`.
static int highlights = -1;
void x360_post_set_highlights(int mode) { highlights = mode; if (diag_on) diag_event("x360: highlights mode now %d", mode); }
int x360_post_rolloff(void) {           // materials.m: the car paint's shoulder (the held look variant's when one is drawn)
    if (highlights < 0) { static const char *const v[] = {"rolloff", "clip"}; highlights = env_choice("NFS_HIGHLIGHTS", v, 2, 0); }
    if (look_var_is("clip")) return 0;
    if (look_var_is("rolloff")) return 1;
    return highlights == 0;
}
// NFS_DEBAND=smooth|off (F10 Graphics > Banding; default smooth): deband the scene (gradients stored in 8 bits / DXT
// steps, threshold DEBAND_LSB) and a +-1 LSB triangular dither before the 8-bit write. Variants d<lsb> (d0 = off).
static int deband = -1;
void x360_post_set_deband(int mode) { deband = mode; if (diag_on) diag_event("x360: deband mode now %d", mode); }
#define DEBAND_LSB 3.0f
static float deband_thresh(void) {
    if (deband < 0) { static const char *const v[] = {"smooth", "off"}; deband = env_choice("NFS_DEBAND", v, 2, 0); }
    if (look_state == 2) { const char *d = var_num(look_vars[look_var], 'd'); if (d) return (float)atoi(d) / 255.0f; }
    return deband == 0 ? DEBAND_LSB / 255.0f : 0.0f;
}
void x360_post_set_chain(int x360) { postfx = x360 && !postfx_ab() && X360_SHADER_LITS && X360_ENGINE_CONSTS; if (diag_on) diag_event("x360: post-processing now %s", postfx ? "x360" : "pc"); }   // the F10 menu (portmenu.m): from the next frame
static int postfx_x360(void) {
    if (postfx < 0) {
        const char *e = getenv("NFS_POSTFX");
        postfx = !(e && !strcmp(e, "pc")) && !postfx_ab() && X360_SHADER_LITS && X360_ENGINE_CONSTS;
        if (diag_on) diag_event("x360: post-processing %s", postfx ? "x360" : postfx_ab() ? "pc, x360 alongside (BOOT_DIAG_POSTFX_AB)" : "pc");
    }
    return postfx || (ab_state == 2 && ab != 2) || ab_state == 4;   // BOOT_DIAG_POSTFX_AB=2: the held pass stays PC (checks the hold)
}

int x360_post_chain_on(void) { return postfx_x360(); }   // fe_console.c: the Video > Advanced labels

// NFS_VISUALLOOK=x360: the X360 chain with the X360's visuallook data. Both builds' GLOBAL/ATTRIBUTES.BIN hold the
// same five visuallook records in the same order (PC at file offset 0x43350, X360 at 0x52C80, 0xE0 bytes each:
// +0x40 BlackBloomCurve, +0xC0 ColourBloomTint, +0xD0 ColourBloomIntensity, +0xD4 Desaturation, +0xDC
// BlackBloomIntensity), but PC's values were retuned: record 4, the look of the races checked (15.1.1 at heat 0
// reads exactly it), has tint (0.88, 0.80, 0.44) and curve .y (0, 0.17, 1.12, 1) on PC against (0.85, 0.75, 0.25)
// and (0.15, 0.35, 0.98, 1) on X360 (warmer, lifted blacks, black bloom 0.8). The game blends two looks by heat
// (IVisualTreatment::UpdateHeat), so the VT fields are matched to a blend of two PC records and the same blend of
// the X360 records is used. Per record: tint rgb, CBI, desaturation, BBI, BlackBloomCurve rows' .y,
// ColourBloomCurve rows' .y (+0x80; the colour term's curve with the PC tone curve, the default).
#include "gen/look_pc.inc"     // tools/gen_tables.py, from the player's PC GLOBAL/attributes.bin
#include "gen/look_x360.inc"   // and the X360 ATTRIBUTES.BIN (no X360 copy: the PC records, the look unchanged)
static int looks_x360(void) {
    static int on = -1;
    if (on < 0) { const char *e = getenv("NFS_VISUALLOOK"); on = e && !strcmp(e, "x360"); }
    return on || (ab == 3 && ab_state == 4 && ab_var == 1) || look_var_is("x360look");
}
static void look_to_x360(float v[14]) {
    // best fit v ~ (1-w) PC[i] + w PC[j] on tint, BBI and curve (CBI and desaturation can be ENISVisualLook overrides)
    static const int fit[] = {0, 1, 2, 5, 6, 7, 8, 9};
    int bi = -1, bj = 0; float bw = 0, best = 1e30f;
    for (int i = 0; i < 5; ++i)
        for (int j = i; j < 5; ++j) {
            float num = 0, den = 0, w, e = 0;
            for (int k = 0; k < 8; ++k) { float a = LOOK_PC[i][fit[k]], b = LOOK_PC[j][fit[k]]; num += (v[fit[k]] - a) * (b - a); den += (b - a) * (b - a); }
            w = den > 0 ? fminf(fmaxf(num / den, 0.0f), 1.0f) : 0.0f;
            for (int k = 0; k < 8; ++k) { float m = LOOK_PC[i][fit[k]] + w * (LOOK_PC[j][fit[k]] - LOOK_PC[i][fit[k]]) - v[fit[k]]; e += m * m; }
            if (e < best) { best = e; bi = i; bj = j; bw = w; }
        }
    static int logged;
    if (best > 1e-5f) {                                         // not a blend of PC looks (a cinematic): unchanged
        if (diag_on && logged++ < 5) diag_event("x360 look: no PC visuallook blend matches (residual %.4f)", best);
        return;
    }
    if (diag_on && !logged++) diag_event("x360 look: PC looks %d/%d w %.3f -> X360 looks", bi, bj, bw);
    for (int k = 0; k < 14; ++k) {
        float pcv = LOOK_PC[bi][k] + bw * (LOOK_PC[bj][k] - LOOK_PC[bi][k]);
        if ((k == 3 || k == 4) && fabsf(pcv - v[k]) > 1e-4f) continue;   // an override: kept
        v[k] = LOOK_X360[bi][k] + bw * (LOOK_X360[bj][k] - LOOK_X360[bi][k]);
    }
}
// render_present.m, at each Present: `path` is this frame's shot (empty if none). Returns 1 when it set the path.
// BOOT_DIAG_SCENE_SHOT=<n> (diag): a shot n composites after each return from frames without a scene (the garage
// coming up after the title screens, a race after its loading screen): <diag>/scene_<frame>.png (with
// BOOT_DIAG_LOOK_AB, its variants too). Independent of NFS_ADAPT_BLACK, for same-moment before/after pairs.
static int scene_shot_n = -2, scene_shot_left = -1, scene_was_black;
static void scene_shot_track(float m) {
    if (scene_shot_n == -2) { const char *e = diag_on ? getenv("BOOT_DIAG_SCENE_SHOT") : NULL; scene_shot_n = e ? atoi(e) : -1; }
    if (scene_shot_n < 0 || m < 0) return;
    if (m < 0.002f) scene_was_black = 1;
    else if (scene_was_black) { scene_was_black = 0; scene_shot_left = scene_shot_n; }
}
int postfx_ab_shot(char *path, size_t n) {
    if (scene_shot_left >= 0 && look_state != 2 && scene_shot_left-- == 0 && !path[0]) {
        uint64_t diag_current_frame(void);
        snprintf(path, n, "%s/scene_%05llu.png", diag_dir, (unsigned long long)diag_current_frame());
    }
    if (look_ab()) {                                         // BOOT_DIAG_LOOK_AB: the held variants of a shot
        if (look_state == 2) {
            snprintf(path, n, "%.*s_%s.png", (int)(strlen(look_base) - 4), look_base, look_vars[look_var]);
            look_state = 3;
            return 1;
        }
        if (look_state == 0 && path[0] && strlen(path) > 4) { snprintf(look_base, sizeof look_base, "%s", path); look_state = 1; }
    }
    if (!postfx_ab()) return 0;
    if (ab_state == 2 || ab_state == 4) {                    // =3: a second held pass with NFS_VISUALLOOK=x360
        static const char *suf[] = {"", "look", "input", "t", "magenta", "mask"};
        if (ab_state == 4 && ab == 4) snprintf(path, n, "%.*s_no%s.png", (int)(strlen(ab_path) - 4), ab_path, skip_names[ab_stages[ab_var - 1]]);
        else if (ab_state == 4) snprintf(path, n, "%.*s%s.png", (int)(strlen(ab_path) - 4), ab_path, suf[ab_var]);
        else snprintf(path, n, "%s", ab_path);
        ab_state++;
        return 1;
    }
    // a simshot, or a full-size frame shot (BOOT_DIAG_SHOTS, for the front end, which has no sim time)
    if (ab_state == 0 && path[0] && (strstr(path, "/simshot_") || (strstr(path, "/frame_") && strstr(path, ".png")))) {
        snprintf(ab_path, sizeof ab_path, "%.*s_x360.png", (int)(strlen(path) - 4), path);
        ab_state = 1;
    }
    return 0;
}
// pacing.c, at the end of each main-loop pass: 1 = drop this pass's real time (the next pass draws the same scene).
int postfx_ab_hold(void) {
    if (look_state == 1) { look_var = 0; look_state = 2; return 1; }
    if (look_state == 3) { look_state = ++look_var < look_nvars ? 2 : 0; return 1; }
    if (ab_state == 1) { ab_state = 2; return 1; }
    int extra = ab == 3 ? 5 : ab == 4 ? SKIP_AB_STAGES : 0;
    if (ab_state == 3) { ab_var = 1; ab_state = extra ? 4 : 0; return 1; }
    if (ab_state == 5) { if (++ab_var <= extra) ab_state = 4; else { ab_var = 0; ab_state = 0; } return 1; }
    return 0;
}
static uint32_t dev_call(Cpu *c, int slot, int n, const uint32_t *args) {
    uint32_t d = M32(D3D_DEVICE), a[8] = {d};
    for (int i = 0; i < n; ++i) a[i + 1] = args[i];
    uint32_t esp = c->esp, r = call_guest(c, M32(M32(d) + 4 * (uint32_t)slot), n + 1, a);
    c->esp = esp;
    return r;
}

static void x360_log(const X360PostParams *pp, float bbi, float cbi, float pulse, float speedfx, float speed, float step,
                     float focal, float dof, uint32_t cam, float dt30) {
    X360PostParams p = *pp;
    if (diag_on) {
        static uint64_t n_frames;
        if (++n_frames % 300 == 1) diag_event("x360 post: measured %.4f adapted %.4f exposure %.3f speed %.1f blur %.5f dof %d", measured, adapted, p.exposure, speed, step, p.dof);
        // postfx.log, one line per frame: sim = race sim time (Sim::GetTime), dt30 = the adaptation step's frame
        // time in X360 frames, alpha = mean scene alpha (the bloom mask), masked = luminance of the bloom source
        static FILE *f;
        extern float x360_post_alpha, x360_post_masked;
        uint64_t diag_current_frame(void);
        if (!f) {
            char path[1024];
            snprintf(path, sizeof path, "%s/postfx.log", diag_dir);
            if ((f = fopen(path, "w")))
                fprintf(f, "# frame state sim dt30 measured adapted exposure alpha masked desat bbi cbi pulse speedfx vignette speed step dof focal depthoffield near far bloomscale\n");
        }
        if (f) {
            float sim;
            memcpy(&sim, G + 0x009885D8u, 4);
            fprintf(f, "%llu %u %.4f %.3f %.4f %.4f %.3f %.4f %.4f %.3f %.3f %.3f %.3f %.3f %.3f %.2f %.5f %d %.2f %.2f %.3f %.1f %.2f\n",
                    (unsigned long long)diag_current_frame(), M32(GAMEFLOW_STATE), sim, dt30, measured, adapted, p.exposure, x360_post_alpha, x360_post_masked,
                    p.desaturation, bbi, cbi, pulse, speedfx, p.vignette, speed, step, p.dof, focal, dof, p.znear, cam ? F32(cam + 0xC0) : 0.0f, p.bloom_scale);
        }
    }
}

// Rear-view mirror (X360 chain, now the default): PC draws the mirror view (eViews[3]) into a texture and composites it as an FE
// quad in the HUD pass FUN_006e6e40(0), after the visual treatment, so it gets none. The X360 tone-maps its mirror
// with the main view's curve and exposure x X360E_MIRROR_WORLD (RVM_RenderMirror 8245CDF0); the port defaults to x1.0. Here the treatment runs on the texture
// (__g_ID3Texture, 0x982A7C) just before the HUD pass, with the parameters the composite used this frame.
// NFS_MIRROR_EXPOSURE=<x> replaces the multiplier 1.0 (X360E_MIRROR_WORLD is the X360's). `main` explicitly follows the
// main view: it is equivalent to x1.0, which already multiplies this frame's adapted exposure, not a fixed exposure.
#define MIRROR_TEXTURE   0x00982A7Cu
#define MIRROR_ON        0x00919738u   // eViews[3] enabled (RenderingViewMode 2: the mirror view is drawn)
static X360PostParams mirror_p;
static int mirror_have;
static float mirror_scale = -1;                           // 0 = Follow main view, >0 = multiplier of main exposure
void x360_post_set_mirror_exposure(float x) {             // the F10 menu: 0 = `main`
    mirror_scale = x >= 0 ? x : 1.0f;
    if (diag_on) diag_event("x360: mirror exposure now %s (main x%.2f)", mirror_scale == 0 ? "main" : "scaled", mirror_scale == 0 ? 1.0f : mirror_scale);
}
void f_006E6E40_orig(Cpu *c);
void f_006E6E40(Cpu *c) {                                  // FE / HUD pass (cdecl, int hud_pass: 0 = the HUD)
    uint64_t diag_current_frame(void);
    if (M32(c->esp + 4) == 0 && mirror_have && postfx_x360() && M8(MIRROR_ON) && M32(MIRROR_TEXTURE)) {
        if (mirror_scale < 0) {
            const char *e = getenv("NFS_MIRROR_EXPOSURE");
            mirror_scale = e && !strcmp(e, "main") ? 0 : e && atof(e) > 0 ? (float)atof(e) : 1.0f;
        }
        int ab = probe_mirror_ab(), sig = (probe_sig & PROBE_MIRROR) != 0 && !ab;
        float scale = ab == 1 ? X360E_MIRROR_WORLD : mirror_scale == 0 ? 1.0f : mirror_scale;
        float exposure = mirror_p.exposure * scale;
        if (ab != 2 && render_x360_mirror(M32(MIRROR_TEXTURE), &mirror_p, exposure, sig)) {
            probe_applied(PROBE_MIRROR, 1);
            if (diag_on) diag_event("mirror exposure: frame %llu mode %s main %.6f mirror %.6f scale %.3f adapted %.6f texture_gain 0.5 hud_gain 2",
                (unsigned long long)diag_current_frame(), ab == 1 ? "x360-ab" : mirror_scale == 0 ? "main" : "scaled",
                mirror_p.exposure, exposure, scale, mirror_p.adapted_lum);
        }
    }
    mirror_have = 0;
    f_006E6E40_orig(c);
}

// ---- Lens raindrops (NFS_RAINDROPS; X360 chain only) ----
// PC draws the drops on the lens (FUN_006D4620, rain_drop.efx, 16x16 RAINDROPPC) before the visual treatment, into the
// screen copy the treatment reads, so with the X360 chain they were speed-blurred, depth-of-fielded and graded. The
// X360 draws them after its composite (Function_82448000, 8242ddc0.c:12215) with onscreen_distort. With the X360 chain
// the drop pass is deferred and replayed after render_x360_post: NFS_RAINDROPS=x360 (default) as the X360 draws them
// (render_x360_raindrops: its 32x32 displacement ONSCREENRAINDROP and 4-variant ONSCREENRAINDROPALPHA from
// GLOBALB.BUN, round on any aspect), =pc the game's own pass (PC look, 4:3 drops) after the composite, =pc_before the
// game's pass where PC draws it (blurred and graded: the port before this change, for comparisons). Without the X360
// textures x360 falls back to pc. NFS_POSTFX=pc: nothing changes.
// The drops: the Rain object (eViews[1] + 0x68, 009196B8) holds the count at +0, drop i's record at +i*0x1C: +4 x,
// +8 y (screen uv), +0xC life left / +0x10 life, +0x14 size, +0x1C variant (FUN_0073CFA0; FUN_0073CD30 makes 20,
// variant i & 3; FUN_0073CDB0 shows 20 in view modes < 3, 10 from 3 up, none in tunnels or without rain).
// NFS_TEST_RAIN=<intensity> (test only, needs g_RainEnable): sets rainOverrideIntensity (009B0A48, what
// SetOverRideRainIntensity 0073C970 sets; FUN_007539D0 returns it instead of the map's rain zones) every frame, so it
// rains anywhere (the race start resets it to 0, so it is written again each frame).
#define RAIN_PTR         0x009196B8u
#define RAIN_OVERRIDE    0x009B0A48u
#define VT_ENABLE        0x00901828u   // g_VisualTreatment
#define VT_LOOK_ON       0x008F9B5Cu   // the treatment's own enable (the call site's third condition)
#define FN_RAIN_ON_LENS  0x006D4620u
#define FN_GET_TEXTURE   0x00503400u   // GetTextureInfo(hash, 1, 0)
static int drops_mode = -1;            // 0 pc_before, 1 pc, 2 x360
void x360_post_set_raindrops(int mode) { drops_mode = mode; if (diag_on) diag_event("x360: raindrops mode now %d", mode); }   // the F10 menu (portmenu.m): from the next frame
static int drops_deferred, drops_replaying, drops_have_tex = -1;
static uint32_t drops_tex[2];          // D3D textures: ONSCREENRAINDROP, ONSCREENRAINDROPALPHA
static RainDrop drops[64];
static int ndrops;
static int raindrops_mode(void) {
    if (drops_mode < 0) {
        const char *e = getenv("NFS_RAINDROPS");
        drops_mode = e && !strcmp(e, "pc_before") ? 0 : e && !strcmp(e, "pc") ? 1 : 2;
    }
    return drops_mode;
}
static uint32_t name_hash(const char *s) { uint32_t h = 0xFFFFFFFFu; while (*s) h = h * 33 + (uint8_t)*s++; return h; }   // bStringHash
uint32_t x360_texture_load(Cpu *c, const char *file, uint32_t hash, uint32_t as_hash);
void raindrops_textures(Cpu *c) {      // x360_textures.m, at the first texture Init (start-up)
    if (raindrops_mode() != 2) return;
    static const char *const names[2] = {"ONSCREENRAINDROP", "ONSCREENRAINDROPALPHA"};
    int n = 0;
    for (int i = 0; i < 2; ++i) {
        uint32_t h = name_hash(names[i]), ti = x360_texture_load(c, "GLOBAL/GLOBALB.BUN", h, h);
        drops_tex[i] = ti && M32(ti) ? M32(M32(ti) + 0x18) : 0;             // platform record +0x18: the D3D texture
        n += drops_tex[i] != 0;
    }
    drops_have_tex = n == 2;
    if (!drops_have_tex) fprintf(stderr, "boot: lens raindrops: no X360 drop textures, the PC drops after the composite\n");
}
static void restore_rt(Cpu *c) {       // the RT table's current entry: colour, depth, viewport (as FUN_006e2f50 leaves it)
    uint32_t e = M32(RT_ENTRY_CUR);
    if (!e) return;
    uint32_t a2[2] = {0, M32(e + 8)}; dev_call(c, 37, 2, a2);                // SetRenderTarget (0x94)
    uint32_t a1[1] = {M32(e + 0xC)}; dev_call(c, 39, 1, a1);                 // SetDepthStencilSurface (0x9C)
    static uint32_t vp;
    if (!vp) vp = galloc(24);
    M32(vp) = 0; M32(vp + 4) = 0; M32(vp + 8) = M32(e + 0x14); M32(vp + 12) = M32(e + 0x18);
    float zero = 0, one = 1; memcpy(G + vp + 16, &zero, 4); memcpy(G + vp + 20, &one, 4);
    uint32_t a3[1] = {vp}; dev_call(c, 47, 1, a3);                           // SetViewport (0xBC)
}
static void rain_on_lens(Cpu *c, uint32_t to_bb) {   // the game's own drop pass, now
    uint32_t a[1] = {to_bb};
    drops_replaying = 1;
    call_guest(c, FN_RAIN_ON_LENS, 1, a);
    drops_replaying = 0;
}
void f_006D4620_orig(Cpu *c);
void f_006D4620(Cpu *c) {                                  // rain on the lens (cdecl, char to_backbuffer)
    static float test_rain = -2;
    if (test_rain < -1) { const char *e = getenv("NFS_TEST_RAIN"); test_rain = e ? (float)atof(e) : -1; }
    if (test_rain >= 0 && !drops_replaying) put_float(RAIN_OVERRIDE, test_rain);
    if (drops_deferred && diag_on) { static int n; if (n++ < 3) diag_event("raindrops: last frame's deferred drops were not drawn (no X360 composite)"); }
    drops_deferred = 0;
    uint32_t rain = M32(RAIN_PTR), vtw = M32(VT_TECHNIQUE), count = rain ? M32(rain) : 0;
    if (diag_on && !drops_replaying) {
        static uint32_t last = ~0u;
        if (count != last) { last = count; diag_event("raindrops: %u drops (intensity %.3f, override %.3f, to_bb %u)", count, rain ? F32(rain + 0x28C) : 0.0f, F32(RAIN_OVERRIDE), M8(c->esp + 4)); }
    }
    // deferred only when the X360 composite will run this frame (f_006D53D0's conditions and its call site's)
    if (drops_replaying || !count || M8(c->esp + 4) || raindrops_mode() == 0 || !postfx_x360() || !M32(VT_ENABLE) || !M32(VT_PLAT) ||
        !M8(VT_LOOK_ON) || !vtw || M32(vtw + 40) == 1) { f_006D4620_orig(c); return; }
    ndrops = 0;
    for (uint32_t i = 0; i < count && ndrops < 64; ++i) {
        uint32_t r = rain + i * 0x1C;
        float total = F32(r + 0x10);
        RainDrop *d = &drops[ndrops++];
        d->x = F32(r + 4); d->y = F32(r + 8); d->life = total != 0 ? F32(r + 0xC) / total : 0; d->size = F32(r + 0x14); d->variant = M32(r + 0x1C);
    }
    drops_deferred = 1;
    c->esp += 4;
}
// f_006D53D0: the deferred drops, after the X360 composite (1) or, if the PC treatment runs instead, before it (0)
static void raindrops_after(Cpu *c, int composited) {
    if (!drops_deferred) return;
    drops_deferred = 0;
    if (!composited) { rain_on_lens(c, 0); return; }        // where PC draws them: into the next screen copy, the treatment's input
    int sig = (probe_sig & PROBE_RAINDROPS) != 0;
    static int logged;
    if (raindrops_mode() == 2 && drops_have_tex == 1) {
        int n = render_x360_raindrops(M32(BACKBUFFER_SURF), drops, ndrops, drops_tex[0], drops_tex[1], sig);
        if (n > 0) {
            probe_applied(PROBE_RAINDROPS, n);
            if (diag_on && !logged++) diag_event("raindrops: x360 pass, %d drops, first x %.3f y %.3f life %.3f size %.3f variant %u", n, drops[0].x, drops[0].y, drops[0].life, drops[0].size, drops[0].variant);
            return;
        }
    }
    // the game's pass after the composite: it copies its source screen copy into the back buffer, then draws the drops
    // refracting that copy, so the copy gets the composite first (StretchRect, which also resolves AA)
    int32_t cur = (int32_t)M32(VT_PINGPONG);
    uint32_t surf = cur >= 0 && cur < 2 ? M32(SCREEN_SURFACES + 4 * (uint32_t)cur) : 0;
    if (!surf) return;
    uint32_t a[5] = {M32(BACKBUFFER_SURF), 0, surf, 0, 2};                  // D3DTEXF_LINEAR
    dev_call(c, 34, 5, a);                                                   // StretchRect (0x88)
    rain_on_lens(c, 1);
    restore_rt(c);
    if (diag_on && !logged++) diag_event("raindrops: PC pass after the composite, %d drops", ndrops);
}
void f_006D53D0_orig(Cpu *c);
// PC's visual treatment (NFS_POSTFX=pc, or when the X360 composite can't run) with the colour filter: the VT fields
// its parameters and SPLINE LUT come from (FUN_006d4ef0, called at 006D54E8, rebuilds the LUT from the curves through
// FUN_006c3b20) are filtered for the call and put back after it, since the game's look blending owns them.
static void pc_treatment(Cpu *c) {
    uint32_t vt = M32(VT_PLAT);
    float f = colour_amount();
    if (!vt || f >= 1.0f) { f_006D53D0_orig(c); return; }
    float look[14], saved[14];
    for (int i = 0; i < 14; ++i) saved[i] = look[i] = F32(vt + LOOK_OFFS[i]);
    colour_filter(look, f, PC_CURVE_TREF, 1.0f);           // natural: PC's treatment passes the scene through (no exposure)
    for (int i = 0; i < 14; ++i) put_float(vt + LOOK_OFFS[i], look[i]);
    f_006D53D0_orig(c);
    for (int i = 0; i < 14; ++i) put_float(vt + LOOK_OFFS[i], saved[i]);
}
void f_006D53D0(Cpu *c) {                                  // visual treatment (stdcall, char to_backbuffer)
    uint32_t vtw = M32(VT_TECHNIQUE), vt = M32(VT_PLAT);
    int shadow = !postfx_x360() && postfx_ab();              // A/B: the X360 chain into a scratch target, then PC's
    if ((!postfx_x360() && !shadow) || !vtw || !vt || M32(vtw + 40) == 1) { raindrops_after(c, 0); pc_treatment(c); return; }
    // the ping-pong index as PC's code uses it (signed): the screen filter FUN_006d3850 leaves it at -1, and then
    // the source is the entry before the arrays (0093DEF4) and the next index is 0
    int32_t cur = (int32_t)M32(VT_PINGPONG);
    uint32_t next = (uint32_t)(cur + 1) & 0x80000001u;
    if ((int32_t)next < 0) next = ((next - 1) | 0xFFFFFFFEu) + 1;
    uint32_t to_bb = M8(c->esp + 4);
    // The composite always goes to the back buffer: on X360 it is the last pass. PC's treatment writes into the
    // next screen copy when PC's overbright is on (to_bb 0, every race with g_OverBrightEnable) and the overbright
    // pass (FUN_006dc040) then draws that copy into the back buffer; that pass is skipped here, so writing the
    // screen copy left the X360 result unseen and the raw scene on screen.
    uint32_t src = M32(SCREEN_TEXTURES + 4 * cur), dst = shadow ? 0 : M32(BACKBUFFER_SURF);
    // vignette texture: **(VT+0x1F0)+0x18 (UVESVIGNETTE)
    uint32_t vig = 0, p1 = M32(vt + 0x1F0);
    if (p1 && M32(p1)) vig = M32(M32(p1) + 0x18);

    X360PostParams p;
    memset(&p, 0, sizeof p);
    // adaptation (82223220), per X360 frame (30 fps): steps scaled by the frame time, the game's real time of this
    // pass (what its cameras and particles advance by; the catch-up limit applies and diag stalls don't count)
    float dt30 = F32(REAL_TIME_ELAPSED) * 30.0f;
    if (!(dt30 >= 0.0f)) dt30 = 0.0f;
    if (dt30 > 4.0f) dt30 = 4.0f;
    // Frames without a scene (measured luminance ~0: the title and menu screens before the garage loads, loading
    // screens; the X360 draws those as simple frames without the HDR passes, eRender_QueueSimpleFrame 82445198) don't
    // step the adaptation, and the start-up snap waits for the first frame with a scene. Before, 16 s of black title
    // screens took it to its floor (X360E_ADAPT_FLOOR), so the main-menu garage came up at exposure 4.6 instead of ~0.9 for ~3 s,
    // with a bright-pass threshold of c > 0.21. NFS_ADAPT_BLACK=step (test only) keeps the old behaviour.
    static int adapt_black = -1;
    if (adapt_black < 0) { const char *e = getenv("NFS_ADAPT_BLACK"); adapt_black = e && !strcmp(e, "step"); }
    float d = measured - adapted;
    int no_scene = !measured_real || (measured < 0.002f && !adapt_black);
    { static int was = -1; if (diag_on && no_scene != was) { was = no_scene; diag_event("x360 post: %s (measured %.4f, adapted %.4f)", no_scene ? "no scene: adaptation held" : "scene: adaptation runs", measured, adapted); } }
    if (no_scene) ;
    else if (reset_adaptation) { adapted = measured; reset_adaptation = 0; }
    else if (adapted < X360E_ADAPT_FLOOR) adapted = X360E_ADAPT_FLOOR;
    else adapted += (d > 0 ? X360E_ADAPT_UP : X360E_ADAPT_DOWN) * d * dt30;
    // the look: tint, colour/black bloom intensity, desaturation, BlackBloomCurve and ColourBloomCurve rows' .y (the
    // X360's own values with NFS_VISUALLOOK=x360), then the colour filter (View > Colour)
    float look[14];
    for (int i = 0; i < 14; ++i) look[i] = F32(vt + LOOK_OFFS[i]);
    if (looks_x360()) look_to_x360(look);
    int old = look_old();                                   // BOOT_DIAG_LOOK_AB `old`: the chain before 2026-10-20's fixes
    colour_filter(look, colour_amount(), fminf(fmaxf(adapted, 0.0f), 1.0f), (adapted + X360E_EXPOSURE_EPS) / (2.0f * X360E_MIDDLE_GRAY));   // natural: x exposure = 1
    // tone curve: rows' .y as Bezier control points (82224C78, 82223BB8): the black term on the BlackBloomCurve; the
    // colour term on the ColourBloomCurve, as PC's treatment (default), or on the BlackBloomCurve as the X360's
    // renderer does (NFS_TONE_CURVE=x360)
    const float *PB = look + 6, *PC = tone_curve_x360() || old ? look + 6 : look + 10;
    float CB[4], CC[4];
    CB[0] = PB[0]; CB[1] = PB[1] * 3 - PB[0] * 3; CB[2] = PB[0] * 3 + (PB[2] * 3 - PB[1] * 6); CB[3] = (PB[1] * 3 + -(PB[2] * 3 - PB[3])) - PB[0];
    CC[0] = PC[0]; CC[1] = PC[1] * 3 - PC[0] * 3; CC[2] = PC[0] * 3 + (PC[2] * 3 - PC[1] * 6); CC[3] = (PC[1] * 3 + -(PC[2] * 3 - PC[3])) - PC[0];
    float bbi = look[5], cbi = look[3], pulse = F32(vt + 0x174), speedfx = F32(vt + 0x178);
    for (int i = 0; i < 4; ++i) {
        p.coeffs[i][0] = CB[i] * bbi + (i == 0 ? 1.0f - bbi : 0.0f);
        for (int ch = 0; ch < 3; ++ch) p.coeffs[i][1 + ch] = CC[i] * look[ch] * cbi;
    }
    float P[4] = {PB[0], PB[1], PB[2], PB[3]};             // (logged)
    p.middle_gray = X360E_MIDDLE_GRAY;
    p.adapted_lum = adapted;
    p.bloom_thresh = old ? X360E_BRIGHT_THRESH : bloom_thresh();
    p.bloom_scale = X360E_BLOOM_SCALE * bloom_gain() * bloom_amount();    // g_fBloomScale 82908AB4, x View > Bloom
    p.bloom_mask_pc = old ? 0 : bloom_mask_pc();
    p.exposure = 2.0f * p.middle_gray / (adapted + X360E_EXPOSURE_EPS) * (1.0f + pulse);
    p.desaturation = look[4];
    { static int lb = -1, lc = -1, lt = -1; int bl = (int)(bloom_amount() * 100.0f + 0.5f), cl = (int)(colour_amount() * 100.0f + 0.5f), tc = tone_curve_x360();
      if (diag_on && look_state != 2 && (bl != lb || cl != lc || tc != lt)) {
          lb = bl; lc = cl; lt = tc;
          diag_event("look: bloom %d%% (scale %.2f, mask %s), colour filter %d%%, colour-term curve %s", bl, p.bloom_scale, p.bloom_mask_pc == 2 ? "none" : p.bloom_mask_pc ? "pc" : "x360", cl, tone_curve_x360() ? "x360 (BlackBloomCurve)" : "pc (ColourBloomCurve)");
      } }
    if (ab == 3 && ab_state == 4 && ab_var >= 2) p.debug = ab_var - 1;   // 1 input, 2 t, 3 magenta, 4 bloom mask
    if (look_var_is("raw")) p.debug = 1;                  // BOOT_DIAG_LOOK_AB `raw`: the chain's input, untreated
    if ((probe_sig & PROBE_POSTFX) && !shadow) p.debug = 3;  // BOOT_DIAG_PROBE: the composite outputs magenta
    p.vignette = F32(vt + 0x17C);
    // camera: projection range, DOF, radial blur (82223D40)
    uint32_t cam = M32(CAMERA_PTR);
    p.znear = cam ? F32(cam + 0xBC) : 0.5f;
    if (!(p.znear > 0.01f && p.znear < 10.0f)) p.znear = 0.5f;
    p.zfar = cam ? F32(cam + 0xC0) : 10000.0f;                     // Camera far (10000 in races)
    if (!(p.zfar > p.znear * 2 && p.zfar < 1e6f)) p.zfar = 10000.0f;
    float focal = cam ? F32(cam + 0xB4) : 0, dof = cam ? F32(cam + 0xB8) : 0;
    p.dof = fabsf(focal) > 1e-4f || fabsf(dof) > 1e-4f;
    p.dof_params[0] = focal * X360E_DOF_FOCAL; p.dof_params[1] = dof * X360E_DOF_RANGE; p.dof_params[2] = dof != 0 ? 1.0f / (dof * X360E_DOF_RANGE) : 0;
    float vel[3] = {cam ? F32(cam + 0x200) : 0, cam ? F32(cam + 0x204) : 0, cam ? F32(cam + 0x208) : 0}, t[3], n[3] = {1, 0, 0};
    for (int j = 0; j < 3; ++j) t[j] = vel[0] * F32(PLAYER_VIEW + 4 * j) + vel[1] * F32(PLAYER_VIEW + 16 + 4 * j) + vel[2] * F32(PLAYER_VIEW + 32 + 4 * j);
    float speed = sqrtf(vel[0] * vel[0] + vel[1] * vel[1] + vel[2] * vel[2]), tl = sqrtf(t[0] * t[0] + t[1] * t[1] + t[2] * t[2]);
    if (tl != 0) for (int j = 0; j < 3; ++j) n[j] = t[j] / tl;
    int racing = M32(GAMEFLOW_STATE) == 6, allowed = M32(MOTION_BLUR_ON) != 0 && racing;
    float b[4] = {0, 0, 0, 0};
    if (speed > 1.0f && allowed) { b[0] = n[0] - n[2]; b[1] = n[1] - n[2]; b[2] = n[0] + n[2]; b[3] = n[1] + n[2]; }
    float amt = speed > X360E_BLUR_SPEED_MIN ? (speed >= X360E_BLUR_SPEED_FULL ? X360E_BLUR_MAX : fminf((speed - X360E_BLUR_SPEED_MIN) * X360E_BLUR_RAMP, X360E_BLUR_MAX)) : 0.0f;
    float step = (speedfx * X360E_BLUR_FX + X360E_BLUR_BASE) * amt * X360E_BLUR_STEP;
    // per-tap uv step at the left/top and right/bottom edges: the quad's corners (82224458) step by -i*b
    p.blur[0] = b[0] * step; p.blur[1] = b[1] * step; p.blur[2] = b[2] * step; p.blur[3] = b[3] * step;
    float ramp = fminf(fmaxf(speed * X360E_BLUR_FADE, 0.0f), 1.0f);
    p.blur_params[0] = speedfx * X360E_BLUR_FX_SCALE + (racing ? 0.0f : 1.0f);
    p.blur_params[1] = speedfx * ramp;
    if (speed_blur < 0) { static const char *const v[] = {"off", "x360", "strong"}; speed_blur = env_choice("NFS_SPEED_BLUR", v, 3, 1); }
    if (curve_glow < 0) { static const char *const v[] = {"x360", "tight", "pixel"}; curve_glow = env_choice("NFS_CURVE_GLOW", v, 3, 0); }
    if (speed_blur != 1) {
        float len = speed_blur == 2 ? 1.5f : 0.0f;
        for (int j = 0; j < 4; ++j) p.blur[j] *= len;
        if (!speed_blur) p.blur_params[1] = 0;
    }
    p.curve_glow = curve_glow;
    if (bloom_surfaces < 0) { static const char *const v[] = {"glow", "halo", "natural"}; bloom_surfaces = env_choice("NFS_BLOOM_SURFACES", v, 3, 2); }
    int bmode = look_var_is("glow") ? 0 : look_var_is("halo") ? 1 : look_var_is("natural") ? 2 : bloom_surfaces;
    p.bloom_self = bmode == 1 ? 1.0f : 0.0f;
    p.glare = bmode == 2 && !old ? GLARE_S * bloom_amount() : 0.0f;
    p.lamp_glow = lampglow_enabled() ? fminf(fmaxf(bloom_amount() * 0.5f, 0), 1) : 0;
    p.rolloff = x360_post_rolloff();
    p.deband = deband_thresh();
    int skip = post_skip();                                 // NFS_TEST_POST_SKIP / BOOT_DIAG_POSTFX_AB=4 (test only)
    if (skip & SKIP_SPEEDBLUR) { memset(p.blur, 0, sizeof p.blur); p.blur_params[1] = 0; }
    if (skip & SKIP_DOF) p.dof = 0;
    if (skip & SKIP_BLOOM) { p.bloom_scale = 0; p.glare = 0; p.lamp_glow = 0; }
    if (skip & SKIP_CURVE) { memset(p.coeffs, 0, sizeof p.coeffs); p.coeffs[0][0] = 1; p.desaturation = 1; }
    if (skip & SKIP_ADAPT) { p.adapted_lum = p.middle_gray; p.exposure = 2.0f * p.middle_gray / (p.middle_gray + X360E_EXPOSURE_EPS) * (1.0f + pulse); }
    if ((skip & SKIP_ALL) && !p.debug) p.debug = 1;
    if ((skip & SKIP_SCALEDGLOW) && p.curve_glow == 0) p.curve_glow = 1;
    p.skip = (skip & SKIP_VIGNETTE ? 1 : 0) | (skip & SKIP_CURVELOCAL ? 2 : 0) | (skip & SKIP_SMOOTHTAPS ? 4 : 0);
    static int logged;
    if (diag_on && !logged++)
        diag_event("x360 post: to_bb %u src %08X dst %08X vignette %08X near %.3f far %.1f focal %.2f dof %.2f bbi %.3f cbi %.3f desat %.3f curve %.3f %.3f %.3f %.3f tint %.3f %.3f %.3f %.3f; PC look flag 008F9B5D %d, blend 008FAE64 %.3f",
                   to_bb, src, dst, vig, p.znear, p.zfar, focal, dof, bbi, cbi, p.desaturation, P[0], P[1], P[2], P[3], F32(vt + 0xA0), F32(vt + 0xA4), F32(vt + 0xA8), F32(vt + 0xAC),
                   G[0x008F9B5Du], F32(0x008FAE64u));
    { static uint32_t last; uint32_t key = to_bb << 31 | (dst & 0x7FFFFFF) | (uint32_t)(cur & 3) << 28;
      if (diag_on && key != last && !shadow) { last = key; diag_event("x360 post: to_bb %u pingpong %d src %08X dst %08X (BACKBUFFER_SURF %08X) state %u", to_bb, cur, src, dst, M32(BACKBUFFER_SURF), M32(GAMEFLOW_STATE)); } }
    float m = render_x360_post(src, dst, vig, &p);
    if (m > -2 && raindrops_mode() == 2 && drops_have_tex == 1) render_x360_raindrops_warm(M32(BACKBUFFER_SURF), drops_tex[0], drops_tex[1]);
    if (m <= -2) { raindrops_after(c, 0); pc_treatment(c); return; }   // couldn't run (no source/target): the PC path
    if (m >= 0) { measured = m; measured_real = 1; }
    if (!shadow) scene_shot_track(m);
    if (!shadow) { probe_applied(PROBE_POSTFX, 1); mirror_p = p; mirror_have = 1; }
    if (shadow) { x360_log(&p, bbi, cbi, pulse, speedfx, speed, step, focal, dof, cam, dt30); raindrops_after(c, 0); pc_treatment(c); return; }
    // PC's bookkeeping: advance the screen-copy ping-pong, then the epilogue (FUN_006e2f50, restore the RT entry)
    M32(VT_PINGPONG) = next;
    uint32_t esp = c->esp;
    call_guest(c, FN_VT_EPILOGUE, 0, NULL);
    c->esp = esp;
    restore_rt(c);
    raindrops_after(c, 1);                                  // lens raindrops: after the composite, as on X360
    x360_log(&p, bbi, cbi, pulse, speedfx, speed, step, focal, dof, cam, dt30);
    c->esp += 8;                                            // ret 4
}
void f_006DBB20_orig(Cpu *c);
void f_006DBB20(Cpu *c) {                                  // PC motion blur: off with the X360 radial blur
    if (postfx_x360() || (post_skip() & SKIP_MOTIONBLUR)) { c->esp += 4; return; }
    f_006DBB20_orig(c);
}
void f_006DC040_orig(Cpu *c);
// PC overbright: off with the X360 bloom (cdecl). PC chain: View > Bloom scales its strength, the BLENDFACTOR it adds
// with (255 x [008F9B30], [008F9B2C] without the treatment, or [[009196B4] + 0x140] x blend x const while that
// object's +0x30 is set: 006DC0B4-006DC110), by scaling those three for the call (not skipped at 0: with the
// treatment writing a screen copy, this pass is what draws it into the back buffer).
void f_006DC040(Cpu *c) {
    if (postfx_x360()) { c->esp += 4; return; }
    float s = bloom_amount();
    uint32_t obj = M32(0x009196B4u), a[3] = {0x008F9B2Cu, 0x008F9B30u, obj ? obj + 0x140 : 0};
    float sv[3];
    if (s < 1.0f) for (int i = 0; i < 3; ++i) if (a[i]) { sv[i] = F32(a[i]); put_float(a[i], sv[i] * s); }
    f_006DC040_orig(c);
    if (s < 1.0f) for (int i = 0; i < 3; ++i) if (a[i]) put_float(a[i], sv[i]);
}
