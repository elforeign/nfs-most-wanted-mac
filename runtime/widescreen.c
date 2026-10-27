// widescreen.c - widescreen display modes and a Hor+ field of view (the X360 version is 16:9).
//
// Modes: the retail game has six 4:3 modes (__getResolutionByIndex 006C27D0, a copy at 006C28B0, and the width and
// height tables at 008F9414 / 008F942C that start-up matches against the adapter's modes to mark the usable ones).
// The six slots follow the display: heights 720, 1080, 1440, 1800 and 2160 at the main display's aspect, then its
// native size, capped at 4K (3840 wide or 2160 high); sizes above the display's are left empty (the game marks them
// unavailable and the options menu skips them). Slot 1, 1080p, is what the game's first-run detection picks. The
// options menu prints the size (Draw__18VOScreenResolution 0051B7A0 otherwise shows a localized 4:3 label per slot).
// NFS_WIDESCREEN=off keeps the retail table.
//
// Field of view: FUN_006CF400 builds each view's projection from its viewport: horizontal half angle fov/2, vertical
// fov/2 * h/w, times 1.15 for the player cameras (view types 1, 2, 4, 5), or fov/2 * 0.6 for types 8 and 9. Wider
// than 4:3 that is Vert- (less visible above and below), and types 8/9 stretch. For full-screen views wider than 4:3
// the projection is corrected just before the game multiplies it with the view matrix (the D3DXMatrixMultiply at
// 006CF62A), so the frustum planes and the depth-biased copy built from it follow: the vertical angle is the one 4:3
// gives, and the horizontal scale keeps 4:3's ratio to it (Hor+). The road reflection view (eViews[4], 009197A0) gets
// the same correction for the screen's aspect, whatever its own target's size (PC 320x240, X360 half the back buffer:
// x360.m): wet roads look its target up at the main view's screen position, so its projection has to be the main
// view's (mirrored camera, same angles).
//
// 2D: every FEng quad (menus, HUD, loading screens, the mirror quad) is drawn by eViewPlatInterface::Render 006D1260
// from 640x480 FE space with fixed 1/320, 1/240 scales, under the WorldViewProj the FE pass FUN_006E6E40 sets to
// identity (FUN_006C8000, for FEShader and FEMaskShader). Wider than 4:3 that matrix scales x by (4/3)/aspect, so the
// 2D layer keeps its proportions (pillarboxed); other passes that draw full-screen quads the same way are untouched.
// From 3:2 the HUD uses the game's own widescreen layout (FEngHud::SetWideScreenMode 005696C0, the X360's: HUD
// packages and the minimap move out by 120 FE units, made for 16:9; see fe_transform for narrower screens), and the
// mouse maps back through the same transform (__UpdateCursorPos).
// The menus stay pillarboxed: they are the same packages on X360 (Global\FEng_Screens\Stripped), whose widescreen
// differences are the WS_ packages and CURRENT_GEN_WIDESCREEN (eIsWidescreen below). A full-screen movie quad (all
// are overscanned for TVs; movies.c stores the 16:9 frame without bars on wide screens) is fitted to the movie's
// aspect in the visible area, letterboxed only when the screen is narrower than the movie.
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

#include "host.h"
#include "materials.h"

#define RESOLUTION_INDEX 0x90181Cu      // __resolution_index (g_RacingResolution)
#define MODE_WIDTHS 0x8F9414u           // int[6], matched against the adapter's modes at start-up
#define MODE_HEIGHTS 0x8F942Cu
#define FN_FEPRINTF 0x515D70u           // FEPrintf(FEString *, const char *fmt, ...), cdecl
#define RA_PROJ_MULTIPLY 0x006CF62Fu    // return address of FUN_006CF400's D3DXMatrixMultiply(viewProj, view, proj)
#define WINDOW_WIDTH 0x982BE4u          // __window_width, __window_height: the back buffer
#define WINDOW_HEIGHT 0x982BE8u
#define FE_DATABASE 0x91CF90u           // FEDatabase*: +0x10 options, +0x34 widescreen HUD
#define MOVIE_TEXTURE_INFO 0x91CB28u    // the movie's TextureInfo (movies.c)
#define CURSOR 0x987F50u                // __UpdateCursorPos output (FE units): +4 x, +8 y, +0xC/+0x10 previous, +0x14/+0x18 delta
#define GAMEFLOW_STATE 0x925E90u        // 3 front end, 6 racing
#define VIEW_REFLECTION 0x9197A0u       // eViews[4] (eViews 009195E0, 0x70 each): the road reflection view

static uint32_t modes[6][2] = {{640, 480}, {800, 600}, {1024, 768}, {1280, 960}, {1280, 1024}, {1600, 1200}};
static int wide, native_slot;      // native_slot: the largest mode, the display's native size (at most 4K)

static void build_modes(void) {        // the resolution list for this display (at most 4K)
    int dw, dh;
    user32_display_pixels(&dw, &dh);
    double aspect = dh > 0 ? (double)dw / dh : 16.0 / 9.0;
    double cap = fmin(1.0, fmin(3840.0 / dw, 2160.0 / dh));            // native, at most 4K
    uint32_t nw = (uint32_t)(dw * cap) & ~7u, nh = (uint32_t)(dh * cap) & ~7u;
    static const uint32_t heights[5] = {720, 1080, 1440, 1800, 2160};
    memset(modes, 0, sizeof modes);
    int n = 0;
    for (int i = 0; i < 5; ++i) {
        uint32_t w = (uint32_t)lround(heights[i] * aspect) & ~7u;
        if (heights[i] <= nh && w <= nw) { modes[n][0] = w; modes[n][1] = heights[i]; ++n; }
    }
    if (n == 0 || modes[n - 1][1] != nh) { modes[n][0] = nw; modes[n][1] = nh; ++n; }   // native, when not listed
    fprintf(stderr, "boot: display %dx%d: resolutions", dw, dh);
    for (int i = 0; i < n; ++i) fprintf(stderr, " %ux%u", modes[i][0], modes[i][1]);
    fprintf(stderr, "\n");
}

void widescreen_setup(void) {
    const char *e = getenv("NFS_WIDESCREEN");
    wide = !(e && (!strcmp(e, "off") || !strcmp(e, "0")));
    if (wide) build_modes();
    for (int i = 0; i < 6; ++i) { M32(MODE_WIDTHS + 4 * i) = modes[i][0]; M32(MODE_HEIGHTS + 4 * i) = modes[i][1]; }
    // First run (no saved g_RacingResolution, which the game reads over this later): start in slot 1, the one the
    // hardware detection picks here (performance level 4). It runs after the device exists and doesn't resize it,
    // so with the retail 0 the whole first session ran at 1280x720.
    if (wide && modes[1][0]) M32(RESOLUTION_INDEX) = 1;
    for (native_slot = 5; native_slot > 0 && !modes[native_slot][0]; --native_slot) {}
}

// The main display changed size (user32.m, display_poll). On the native resolution the game follows with its own
// resolution change, the one the Options screen uses (LoadedFlagMaybe: eDisplayFrame re-reads the size and resets
// the device); a resolution picked in Options stays, and so does the list, which keeps its meaning.
int widescreen_display_changed(void) {
    int boot_reg_override(const char *name);
    if (!wide || M32(RESOLUTION_INDEX) != (uint32_t)native_slot || boot_reg_override("g_RacingResolution")) return 0;
    if (M32(0x91CA20)) return 0;                // Options screen mid-edit (its pending settings)
    uint32_t ow = modes[native_slot][0], oh = modes[native_slot][1];
    build_modes();
    for (native_slot = 5; native_slot > 0 && !modes[native_slot][0]; --native_slot) {}
    for (int i = 0; i < 6; ++i) { M32(MODE_WIDTHS + 4 * i) = modes[i][0]; M32(MODE_HEIGHTS + 4 * i) = modes[i][1]; }
    M32(RESOLUTION_INDEX) = (uint32_t)native_slot;
    if (modes[native_slot][0] == ow && modes[native_slot][1] == oh) return 0;
    G[0x982C39] = 1;                          // LoadedFlagMaybe (a byte)
    fprintf(stderr, "boot: native resolution %ux%u -> %ux%u (slot %d), game resolution change queued\n", ow, oh, modes[native_slot][0], modes[native_slot][1], native_slot);
    return 1;
}
int widescreen_native_slot(void) { return wide ? native_slot : -1; }   // first run's default resolution (d3d9.m)
void widescreen_mode(int i, uint32_t *w, uint32_t *h) { *w = modes[i][0]; *h = modes[i][1]; }   // 0x0: no mode in that slot

// The selected slot, made valid: a saved or BOOT_REG index can point at a slot this display leaves empty (or one
// past the table); it becomes the largest available slot below it, and the game's index is corrected so the
// refresh-rate lookup and the saved setting agree.
static uint32_t valid_slot(void) {
    uint32_t i = M32(RESOLUTION_INDEX);
    if (i > 5) i = 5;
    while (i > 0 && !modes[i][0]) --i;
    if (M32(RESOLUTION_INDEX) != i) M32(RESOLUTION_INDEX) = i;
    return i;
}
static void mode_size(Cpu *c) {         // stdcall (int *w, int *h)
    uint32_t i = valid_slot();
    M32(arg(c, 0)) = modes[i][0]; M32(arg(c, 1)) = modes[i][1];
    c->esp += 12;
}
void first_run_settings(Cpu *c, const char *where);
void f_006C27D0(Cpu *c) {                 // __getResolutionByIndex; WinMain's call, the first, follows its first-run detection
    static int first;
    if (!first++) first_run_settings(c, "WinMain");
    mode_size(c);
}
void f_006C28B0(Cpu *c) { mode_size(c); }

void f_0051B7A0_orig(Cpu *c);
void f_0051B7A0(Cpu *c) {                // Draw__18VOScreenResolution (fastcall this): label the slot with its size
    uint32_t self = c->ecx;
    f_0051B7A0_orig(c);
    uint32_t label = M32(self + 0x30), pending = M32(0x91CA20);   // the options being edited: +0x6C resolution slot
    uint32_t i = pending ? M32(M32(pending) + 0x6C) : 6;
    if (!wide || !label || i >= 6) return;
    static uint32_t fmt;
    if (!fmt) fmt = gstr("%d x %d");
    uint32_t a[4] = {label, fmt, modes[i][0], modes[i][1]};
    call_guest(c, FN_FEPRINTF, 4, a);
}

// ---- Hor+ ----
static uint32_t cur_view;               // the view FUN_006CF400 is setting up
void f_006CF400_orig(Cpu *c);
void f_006CF400(Cpu *c) {                // (thiscall) builds a view's matrices and frustum; arg 0 is the view
    uint32_t saved = cur_view;
    cur_view = arg(c, 0);
    f_006CF400_orig(c);
    cur_view = saved;
}
// A full-screen view is one whose viewport is the back buffer. (Not the selected slot's size: on a first run the
// game's hardware detection moves the slot from 0 to 1 after the device exists, so the back buffer keeps slot 0's
// size until the next start.)
void widescreen_projection(Cpu *c, uint32_t proj) {   // from d3dx.c's D3DXMatrixMultiply
    if (M32(c->esp) != RA_PROJ_MULTIPLY || !cur_view) return;
    mirror_projection(cur_view, proj);   // mirror.c: a resized mirror target keeps the square frustum
    if (!wide) return;
    uint32_t type = M32(cur_view + 4), vp = M32(cur_view + 0x60);
    if (!vp) return;
    uint32_t w = M32(vp + 0x14), h = M32(vp + 0x18);
    int full = w == M32(WINDOW_WIDTH) && h == M32(WINDOW_HEIGHT), refl = cur_view == VIEW_REFLECTION;
    if (refl) { w = M32(WINDOW_WIDTH); h = M32(WINDOW_HEIGHT); }   // its texture is stretched over the screen
    float aspect = h ? (float)w / (float)h : 0;
    int fix = (full || refl) && aspect > 4.0f / 3.0f + 1e-3f;
    if (diag_on) {                       // each kind of view once
        static uint32_t seen[64][3];
        static int nseen;
        int known = 0;
        uint32_t vw = M32(vp + 0x14), vh = M32(vp + 0x18), key = type | (uint32_t)refl << 31;
        for (int k = 0; k < nseen && !known; ++k) known = seen[k][0] == key && seen[k][1] == vw && seen[k][2] == vh;
        if (!known && nseen < 64) {
            seen[nseen][0] = key; seen[nseen][1] = vw; seen[nseen][2] = vh; ++nseen;
            diag_event("widescreen: view type %u viewport %ux%u%s m00 %.4f m11 %.4f%s", type, M32(vp + 0x14), M32(vp + 0x18),
                       refl ? " (road reflection)" : "", MF32(proj), MF32(proj + 20), fix ? " -> Hor+" : "");
        }
    }
    if (!fix) return;
    float m00 = MF32(proj), half_h = atanf(1.0f / fabsf(m00));
    float f43 = type == 8 || type == 9 ? 0.6f : 0.75f * (type == 1 || type == 2 || type == 4 || type == 5 ? 1.15f : 1.0f);
    MF32(proj + 20) = copysignf(1.0f / tanf(half_h * f43), MF32(proj + 20));   // the game's is negative
    MF32(proj) = m00 * (4.0f / 3.0f) / aspect;
    // The view's pixels per unit at distance 1 (+0xC, (w/2)/tan(fov/2) from FUN_006BE9F0, set just before this
    // multiply) is what eView::GetPixelSize sizes car LODs with; it follows the wider horizontal angle.
    MF32(cur_view + 0xC) *= (4.0f / 3.0f) / aspect;
}

// ---- 2D ----
// FE space (640x480) -> NDC: x scaled by kx, y by ky around the centre; kx/ky = (4/3)/aspect keeps FE units square.
// Pillarbox (ky = 1) by default. The wide HUD layout needs ~853 FE units across (16:9); racing on screens between 3:2
// and 16:9 (16:10 Macs), the whole layer scales down so they fit (kx = 0.75), instead of the HUD leaving the screen.
#define WIDE_HUD_MIN_ASPECT 1.5f
static float screen_aspect(void) {
    uint32_t w = M32(WINDOW_WIDTH), h = M32(WINDOW_HEIGHT);
    return wide && h ? (float)w / (float)h : 4.0f / 3.0f;
}
static int wide_hud(void) { return screen_aspect() >= WIDE_HUD_MIN_ASPECT; }
static void fe_transform(float *kx, float *ky) {
    float a = screen_aspect();
    *kx = *ky = 1.0f;
    if (a <= 4.0f / 3.0f + 1e-3f) return;
    if (a < 16.0f / 9.0f && wide_hud() && M32(GAMEFLOW_STATE) == 6) { *kx = 0.75f; *ky = 0.5625f * a; }
    else *kx = 4.0f / 3.0f / a;
}
float widescreen_fe_scale(void) { float kx, ky; fe_transform(&kx, &ky); return kx / ky; }   // < 1: wider than 4:3

static uint32_t matrix_ra;              // diagnostics: who set the WorldViewProj the next FE quads draw under
void f_006C8000_orig(Cpu *c);
void f_006C8000(Cpu *c) {                // (bMatrix4 *world, int view): sets the current effect's WorldViewProj
    if (M32(c->esp) == 0x006E03F9u) {
        materials_grass_record(c->esi);
        materials_aux_record(c->esi); // sorted queue's inlined 006D7660
    }
    if (M32(c->esp) == 0x006E03F9u || M32(c->esp) == 0x006D7685u) { materials_glass_record(c->esi); materials_trees_record(c->esi); }   // R19, R28
    uint32_t ra = M32(c->esp), slot = c->esp + 4, world = M32(slot);
    matrix_ra = ra;
    float kx, ky;
    fe_transform(&kx, &ky);
    int fe = (ra == 0x006E6FBCu || ra == 0x006E7016u) && (kx != 1.0f || ky != 1.0f);   // the FE pass: FEShader, FEMaskShader
    if (fe) {
        static uint32_t m;
        if (!m) m = galloc(64);
        memcpy(G + m, G + world, 64);
        for (int r = 0; r < 4; ++r) { MF32(m + 16 * r) *= kx; MF32(m + 16 * r + 4) *= ky; }   // columns 0, 1: output x, y
        M32(slot) = m;
    }
    f_006C8000_orig(c);
    if (fe) M32(slot) = world;          // cdecl: the caller's argument slot, as it was
}
void f_005696C0_orig(Cpu *c);
void f_005696C0(Cpu *c) {                // FEngHud::SetWideScreenMode: the X360 HUD layout when the screen is wide
    uint32_t db = M32(FE_DATABASE), opts = db ? M32(db + 0x10) : 0;
    if (opts && wide && screen_aspect() > 4.0f / 3.0f + 1e-3f) M8(opts + 0x34) = (uint8_t)wide_hud();
    f_005696C0_orig(c);
}
// Chyron::Start (0058D670) picks WIDESCREEN_MODE or NORMAL_MODE (the song-title banner's layout) from options +0x34,
// the wide-HUD flag. The X360 sets it to eIsWidescreen() at defaults and when a profile loads (8223E200, 82297320,
// 822C1E78, 822C3A60); here only the race HUD set it (above), so the front-end banner kept the 4:3 layout until the
// first race. Set it before the banner starts. NFS_WIDE_CHYRON=off keeps the PC behaviour.
void f_0058D670_orig(Cpu *c);
void f_0058D670(Cpu *c) {
    static int on = -1;
    if (on < 0) { const char *e = getenv("NFS_WIDE_CHYRON"); on = !(e && !strcmp(e, "off")); }
    uint32_t db = M32(FE_DATABASE), opts = db ? M32(db + 0x10) : 0;
    if (diag_on && opts) diag_event("chyron: wide HUD flag %u before Chyron::Start (gameflow %u)", M8(opts + 0x34), M32(GAMEFLOW_STATE));
    if (on && opts && wide && screen_aspect() > 4.0f / 3.0f + 1e-3f) M8(opts + 0x34) = (uint8_t)wide_hud();
    f_0058D670_orig(c);
}
// eIsWidescreen (004FB000, hard-wired false on PC) picks the X360's widescreen FE assets: GLOBAL\WIDESCREEN_GLOBAL.BUN
// instead of THINSCREEN_GLOBAL.BUN (WS_Loading.fng, WS_Loading_Controller.fng), the WS_ movie, loading and splash
// packages, and the CURRENT_GEN_WIDESCREEN message to screens that lay themselves out wider (XENON_WS below). True from 3:2
// like the HUD layout (it is first asked for at start-up, before the window exists: the selected mode's aspect then).
// NFS_WS_ASSETS=0 keeps the 4:3 assets.
static int ws_assets(void) {
    static int env = -1;
    if (env < 0) { const char *e = getenv("NFS_WS_ASSETS"); env = !(e && !strcmp(e, "0")); }
    if (!wide || !env) return 0;
    uint32_t w = M32(WINDOW_WIDTH), h = M32(WINDOW_HEIGHT);
    if (!h) { uint32_t i = valid_slot(); w = modes[i][0]; h = modes[i][1]; }
    return h && (float)w / (float)h >= WIDE_HUD_MIN_ASPECT;
}
// For bootflow.c: 1 with the widescreen assets on (the X360's widescreen boot list), 2 if the mode is also HD (at least
// 1280 wide, the X360's SetVideoMode 82440188 threshold for clearing its "not HD" flag DAT_828F4A8E).
int widescreen_boot_assets(void) {
    if (!ws_assets()) return 0;
    uint32_t w = M32(WINDOW_WIDTH);
    if (!w) w = modes[valid_slot()][0];
    return w >= 1280 ? 2 : 1;
}
#define RA_BOOTFLOW_PAL 0x0058EA07u      // BootFlowManager::BootFlowManager: its widescreen lists leave out the attract
#define RA_BOOTFLOW_NTSC 0x0058EAB9u     // movie (MW_LS_AttractFMV.fng), so the boot flow keeps the PC list
// XENON_WS (0x3D4B7270): where the PC sends CURRENT_GEN_WIDESCREEN (CGW, 0xCB835EE3) to a package, the X360 sends
// XENON_WS instead to the title (SplashScreen ctor 822B8500), the loading screen (822BA1B8) and the showcase
// (8256AB78), and both to the controller loading screen (822BA600, CGW from 822BAC70). speed.exe has no XENON_WS, so
// the packages (the same files on both) never get it. What it does there (FNG message responses):
// - WS_Loading.fng: image WS_MWSPLASHBACK fades in with texture WS_XENON_SPLASHBACK (alpha 0 -> 255, 1024x512, z 1080),
//   group 6A0831F6 hides, 15600BA2 runs SHOW. (CGW would move group 1E617436 forward, z 1010 -> 645.) Without
//   XENON_WS the wide loading screen has no backdrop (black).
// - WS_Loading_Controller.fng: group C8E0B85C runs XENON_WS (alpha 255); CGW: group 02DDF58C runs CURRENT_GEN.
// - WS_MW_LS_Splash.fng: groups 84CCAA00 and 84CCA6E8 are shown (the X360 backdrop image FD35FE70, texture
//   WS_XENON_SPLASHBACK, and the X360 title layout), 15600BA2 runs SHOW; CGW shows 2FA84699, animates CA753BBC and
//   runs CURRENT_GEN on 329D8BCB.
// - Showcase.fng: two groups run XENON_WS or CURRENT_GEN, scripts identical byte for byte: no difference, not sent.
// On PC the LoadingScreen and LoadingControllerScreen ctors (0058EF50, 0058F070, which would send CGW) are dead: the
// screen factory table (008F3xxx-008F46xx) has no entry for the loading packages and nothing calls them, so the loading
// package ELoadingScreenOn (0062D570) queues for a push gets no message at all. The texture is only in the X360's
// GLOBALB.BUN; x360_textures.m adds it (x360_texture_added). With it, the wide loading packages get what the X360's screens send
// when FEngine::PushPackage has made them (a message queued by name before the push is processed is dropped: QueueMessage
// finds no package) with NFS_LOADING_LAYOUT=x360, the default (=pc sends nothing, as the PC): WS_Loading.fng XENON_WS,
// WS_Loading_Controller.fng XENON_WS and CGW. It shows until the track starts loading (gameflow 5), when FadeScreen.fng
// and its own loading bar cover it on both layouts. NFS_SPLASH_LAYOUT=x360 (default; =pc keeps the PC layout) sends the title XENON_WS instead
// of CGW (bootflow.c; its eIsWidescreen call answers false, as on X360).
#define MSG_XENON_WS 0x3D4B7270u
#define MSG_CGW 0xCB835EE3u
#define TEX_XENON_SPLASHBACK 0x524FAC6Bu
#define FENG_INSTANCE 0x0091CADCu        // cFEng::mInstance
#define FN_QUEUE_PACKAGE_MESSAGE 0x00516C90u   // cFEng::QueuePackageMessage (thiscall: uint msg, const char *pkg, FEObject *)
#define FN_FENGINE_QUEUE_MESSAGE 0x005B7780u   // FEngine::QueueMessage (thiscall: msg, FEObject *, FEPackage *, FEObject *target, mask)
#define RA_SPLASH 0x005A307Eu            // SplashScreen::SplashScreen's eIsWidescreen (gates only its CGW send)
int x360_texture_added(uint32_t hash);   // x360_textures.m
int widescreen_xenon_ws(int title) {     // the X360 layout for the title (1) or the loading screens (0)
    static int env[2] = {-1, -1};
    if (env[title] < 0) {
        const char *e = getenv(title ? "NFS_SPLASH_LAYOUT" : "NFS_LOADING_LAYOUT");
        env[title] = e ? !strcmp(e, "x360") : 1;
    }
    return env[title] && ws_assets() && x360_texture_added(TEX_XENON_SPLASHBACK);
}
void widescreen_send_xenon_ws(Cpu *c, int title, uint32_t pkg) {   // the title: after SplashScreen's ctor (bootflow.c)
    if (!pkg || !widescreen_xenon_ws(title)) return;
    uint32_t a[3] = {MSG_XENON_WS, pkg, 0}, ecx = c->ecx, eax = c->eax;
    c->ecx = M32(FENG_INSTANCE);
    call_guest(c, FN_QUEUE_PACKAGE_MESSAGE, 3, a);
    c->ecx = ecx; c->eax = eax;
    if (diag_on) diag_event("widescreen: XENON_WS to %s", gptr(pkg));
}
void f_005C4DF0_orig(Cpu *c);
void f_005C4DF0(Cpu *c) {                // FEngine::PushPackage (thiscall: const char *, uchar, uint) -> FEPackage *
    f_005C4DF0_orig(c);
    uint32_t pkg = c->eax;
    const char *name = pkg ? gptr(M32(pkg + 0xC)) : NULL;   // FENode: +0xC name
    if (!name || strncasecmp(name, "WS_Loading", 10) || !widescreen_xenon_ws(0)) return;
    int controller = !strcasecmp(name, "WS_Loading_Controller.fng");
    uint32_t ecx = c->ecx;
    for (int i = 0; i <= controller; ++i) {  // as QueuePackageMessage (00516C90): every object (-4), mask 0xFF
        uint32_t a[5] = {i ? MSG_CGW : MSG_XENON_WS, 0, pkg, 0xFFFFFFFCu, 0xFF};
        c->ecx = M32(M32(FENG_INSTANCE));    // cFEng +0: its FEngine
        call_guest(c, FN_FENGINE_QUEUE_MESSAGE, 5, a);
    }
    c->ecx = ecx; c->eax = pkg;
    if (diag_on) diag_event("widescreen: XENON_WS%s to %s", controller ? " and CURRENT_GEN_WIDESCREEN" : "", name);
}

void f_004FB000(Cpu *c) {                // eIsWidescreen (cdecl, returns bool in al)
    uint32_t ra = M32(c->esp);
    int r = ra != RA_BOOTFLOW_PAL && ra != RA_BOOTFLOW_NTSC && ws_assets();
    if (ra == RA_SPLASH && widescreen_xenon_ws(1)) r = 0;   // the title gets XENON_WS instead (bootflow.c)
    if ((ra == RA_BOOTFLOW_PAL || ra == RA_BOOTFLOW_NTSC) && ws_assets()) {   // but the title screen is the wide one
        static uint32_t splash;
        if (!splash) splash = gstr("WS_MW_LS_Splash.fng");
        for (uint32_t a = 0x8F3C48u; a < 0x8F3CD0u; a += 4) if (M32(a) == 0x008A0114u) M32(a) = splash;
    }
    static int logged = -1;
    if (diag_on && logged != r) { logged = r; diag_event("widescreen: eIsWidescreen -> %d (from %08X)", r, M32(c->esp)); }
    c->eax = (c->eax & ~0xFFu) | (uint32_t)r;
    c->esp += 4;
}
void f_00585820_orig(Cpu *c);
void f_00585820(Cpu *c) {                // FEPackageManager::FindFEPackageData(const char *) (thiscall): diagnostics
    uint32_t name = arg(c, 0), self = c->ecx;
    f_00585820_orig(c);
    if (!diag_on || !name) return;
    static uint32_t seen[256];
    static int nseen;
    uint32_t hash = 2166136261u;
    for (const char *s = (const char *)G + name; *s; ++s) hash = (hash ^ (uint8_t)*s) * 16777619u;
    for (int k = 0; k < nseen; ++k) if (seen[k] == hash) return;
    if (nseen < 256) seen[nseen++] = hash;
    diag_event("widescreen: FE package %s%s", (const char *)G + name, c->eax ? "" : " (not loaded)");
    static int probed;                   // once the front end is up: which of the game's WS_ packages this data has
    if (!probed && !strcmp((const char *)G + name, "MainMenu.fng")) {
        probed = 1;
        static const char *ws[] = {"WS_FEAnyMovie.fng", "WS_InGameAnyMovie.fng", "WS_MW_LS_Splash.fng", "WS_Loading.fng",
                                   "WS_Loading_Controller.fng", "WS_LS_EA_hidef.fng", "WS_LS_EALogo.fng", "WS_LS_PSA.fng",
                                   "WS_LS_IntroFMV.fng", "WS_MW_LS_AttractFMV.fng", "FEAnyMovie.fng", "InGameAnyMovie.fng"};
        uint32_t eax = c->eax;
        for (unsigned k = 0; k < sizeof ws / sizeof *ws; ++k) {
            uint32_t a = gstr(ws[k]);
            c->ecx = self;
            call_guest(c, 0x00585820u, 1, &a);   // logs through this hook
        }
        c->eax = eax;
    }
}

void f_005859E0_orig(Cpu *c);
void f_005859E0(Cpu *c) {                // FEPackageManager::Loader(bChunk *, bool) (thiscall): diagnostics
    uint32_t chunk = arg(c, 0);
    f_005859E0_orig(c);
    if (diag_on && chunk && M32(chunk) == 0x30203u && M32(chunk + 8) == 0xE76E4546u && M32(chunk + 16) == 0x64486B50u)
        diag_event("widescreen: FE package loaded: %.64s", (const char *)G + chunk + 8 + 40);
}

void f_006C9B60_orig(Cpu *c);
void f_006C9B60(Cpu *c) {                // __UpdateCursorPos: window -> FE coordinates through the same transform
    f_006C9B60_orig(c);
    float kx, ky;
    fe_transform(&kx, &ky);
    if (kx == 1.0f && ky == 1.0f) return;
    int32_t x = (int32_t)lroundf(320.0f + (float)((int32_t)M32(CURSOR + 4) - 320) / kx);
    int32_t y = (int32_t)lroundf(240.0f + (float)((int32_t)M32(CURSOR + 8) - 240) / ky);
    M32(CURSOR + 4) = (uint32_t)x; M32(CURSOR + 0x14) = (uint32_t)(x - (int32_t)M32(CURSOR + 0xC));
    M32(CURSOR + 8) = (uint32_t)y; M32(CURSOR + 0x18) = (uint32_t)(y - (int32_t)M32(CURSOR + 0x10));
}

// ---- Lens raindrops ----
// FUN_006D4620 draws each raindrop on the lens as a clip-space quad with the same half size r on both axes, so a
// drop is an ellipse of the screen's aspect (4:3 on the retail game); rain_drop.efx shows the scene at
// RAINDROPOFFSET.xy + displacement * .zw, i.e. offset.x = x - r/4, z = r, w = -r. Wider than 4:3 both are narrowed
// around the drop's centre by (4/3)/aspect, so drops keep their 4:3 shape.
#define RA_DROP_OFFSET 0x006D480Du      // its ID3DXEffect::SetVector(RAINDROPOFFSET)
#define RA_DROP_DRAW 0x006D48EEu        // its DrawPrimitiveUP(TRIANGLESTRIP, 2, quad, 0x18)
static float drop_scale(void) { float a = screen_aspect(); return a > 4.0f / 3.0f + 1e-3f ? 4.0f / 3.0f / a : 1.0f; }
void widescreen_drop_offset(Cpu *c, uint32_t vec) {   // from d3dx_effect.c's SetVector
    float k;
    if (M32(c->esp) != RA_DROP_OFFSET || (k = drop_scale()) == 1.0f) return;
    MF32(vec) += 0.25f * MF32(vec + 8) * (1.0f - k);
    MF32(vec + 8) *= k;
}
void widescreen_drop_quad(Cpu *c, uint32_t verts, uint32_t stride) {   // from d3d9.m's DrawPrimitiveUP
    float k;
    if (M32(c->esp) != RA_DROP_DRAW || (k = drop_scale()) == 1.0f) return;
    float cx = 0;
    for (int v = 0; v < 4; ++v) cx += 0.25f * MF32(verts + stride * v);
    static int logged;
    if (diag_on && !logged++)
        diag_event("widescreen: lens drop quad x %.3f %.3f %.3f %.3f y %.3f %.3f %.3f %.3f, x scaled by %.3f", MF32(verts), MF32(verts + stride),
                   MF32(verts + 2 * stride), MF32(verts + 3 * stride), MF32(verts + 4), MF32(verts + stride + 4), MF32(verts + 2 * stride + 4),
                   MF32(verts + 3 * stride + 4), k);
    for (int v = 0; v < 4; ++v) MF32(verts + stride * v) = cx + (MF32(verts + stride * v) - cx) * k;
}

void f_006D1260_orig(Cpu *c);
void f_006D1260(Cpu *c) {                // eViewPlatInterface::Render (cdecl ePoly *, TextureInfo *, int): one FE quad
    uint32_t poly = arg(c, 0);
    float kx, ky, a = arg(c, 1) == MOVIE_TEXTURE_INFO ? movies_picture_aspect() : 0;
    fe_transform(&kx, &ky);
    float x0 = 1e9f, x1 = -1e9f, y0 = 1e9f, y1 = -1e9f, saved[8];
    for (int v = 0; v < 4; ++v) {
        float x = MF32(poly + 16 * v), y = MF32(poly + 16 * v + 4);
        x0 = fminf(x0, x); x1 = fmaxf(x1, x); y0 = fminf(y0, y); y1 = fmaxf(y1, y);
    }
    if (diag_on && matrix_ra != 0x006E6FBCu && matrix_ra != 0x006E7016u) {   // each 2D caller outside the FE pass once
        static uint32_t seen[64];
        static int nseen;
        uint32_t ra = M32(c->esp);
        int known = 0;
        for (int k = 0; k < nseen && !known; ++k) known = seen[k] == ra;
        if (!known && nseen < 64) {
            seen[nseen++] = ra;
            diag_event("widescreen: 2D quad outside the FE pass from %08X (matrix from %08X, texture %08X): x %.1f..%.1f y %.1f..%.1f",
                       ra, matrix_ra, arg(c, 1), x0, x1, y0, y1);
        }
    }
    // A full-screen movie quad: the 4:3 packages' (0..640) or the WS_ packages' (-130..770, overscanned for TVs) is
    // fitted to the movie's aspect inside the visible FE area (640/kx x 480/ky), so no part of the picture is cut.
    int widen = kx / ky < 1.0f && a > 4.0f / 3.0f && x0 <= 1.0f && x1 >= 639.0f && y1 > y0;
    if (diag_on && a > 0) {              // each movie quad layout once
        static float lx0 = 1e9f, lx1 = 1e9f, ly0 = 1e9f, ly1 = 1e9f;
        if (x0 != lx0 || x1 != lx1 || y0 != ly0 || y1 != ly1) {
            lx0 = x0; lx1 = x1; ly0 = y0; ly1 = y1;
            diag_event("widescreen: movie quad x %.1f..%.1f y %.1f..%.1f, movie aspect %.3f%s", x0, x1, y0, y1, a, widen ? ", fitted" : "");
        }
    }
    if (widen) {
        float w = 640.0f / kx, h = 480.0f / ky;
        if (a < w / h) w = h * a; else h = w / a;
        for (int v = 0; v < 4; ++v) {
            saved[2 * v] = MF32(poly + 16 * v); saved[2 * v + 1] = MF32(poly + 16 * v + 4);
            MF32(poly + 16 * v) = 320.0f + ((saved[2 * v] - x0) / (x1 - x0) - 0.5f) * w;
            MF32(poly + 16 * v + 4) = 240.0f + ((saved[2 * v + 1] - y0) / (y1 - y0) - 0.5f) * h;
        }
    }
    f_006D1260_orig(c);
    if (widen) for (int v = 0; v < 4; ++v) { MF32(poly + 16 * v) = saved[2 * v]; MF32(poly + 16 * v + 4) = saved[2 * v + 1]; }
}
