/* nfsmw.c - Most Wanted's engine rate, resolution and widescreen view.
 *
 * What the community widescreen fix (ThirteenAG, MIT; see NOTICE) did with
 * patched code bytes, done for a recompiled game: game.toml redirects and
 * patches the instructions involved to read slots in the section padding
 * (0x00a37800-0x00a3782f), and this mod fills those slots and hooks whole
 * functions.
 *
 * SIMULATION RATE. The engine steps and caps its frames at 1/60 s. The timer
 * constructor's argument (a code immediate) is rewritten on the stack, three
 * readers of the pooled 1/60 read slot 0x00a37800, and World_Service's initial
 * timestep (.data) is written.
 *
 * RESOLUTION. 0x006c27d0 maps the video option to a width and height. The
 * option offers six sizes in the original; here it offers every common size
 * from 640x480 to 7680x4320 and the screen's own, after an "Auto" entry that
 * is the screen's shape at up to 1080 rows (the renderer draws any of them at
 * the window's size). game.toml points the tables the option is checked
 * against (supported, refresh rate, label) at slots this mod fills, and the
 * wrap-around at the slot holding the count. The saved value is the size
 * packed as 0x80000000 | width << 16 | height, as the widescreen fix saves it,
 * so a list that differs between machines still finds the same size.
 * `width` and `height` override the option.
 *
 * FIELD OF VIEW. 0x006cf400 builds a view's projection. A hook at its entry
 * reads the view id and sets the aspect term and three scale factors the
 * redirected instructions read: hor+ for the player and headlight views, the
 * original values elsewhere, 0.4 for the rear-view mirror.
 *
 * HUD. The game's own widescreen HUD layout is forced on (game.toml). This mod
 * sets its horizontal scale and centre, moves the left and right HUD groups
 * and the minimap out to the screen edges, and scales the front end down when
 * the screen is narrower than it is.
 *
 * CINEMATICS. Full widescreen replacement movies fit the current viewport.
 * Original4:3 movies keep embedded-letterbox sizing. The movie-only quad
 * samples the complete exact-size texture allocated by the PC renderer. */
#include "pop_mod_api.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <errno.h>
#include <ctype.h>
#include <string.h>
#include <stdatomic.h>
#include <time.h>

POP_MOD_DECLARE_ABI();

/* Code */
#define TIMER_CTOR 0x006484e0u
#define GAME_DEVICE_UPDATE 0x006349b0u /* thiscall; 00628940 evaluates the binding table */
#define GAME_BINDINGS 0x0091f418u     /* 76 rows: name, four {kind, device, control} triples */
#define GET_RESOLUTION_A 0x006c27d0u
#define LOAD_SETTINGS 0x006c1c00u        /* reads the registry */
#define SAVE_SETTINGS 0x006c1f10u        /* writes it */
#define APPLY_VIDEO_PRESET 0x006c1b60u  /* legacy detail slider, then capability clamp */
#define LOCALIZED_WIDE 0x0057e920u       /* cdecl (wchar_t *out, int size, hash) -> al */
#define VIEW_PROJECTION 0x006cf400u
#define DRAW_FRONTEND_QUAD 0x006d1260u /* cdecl (quad *, material *, flags) */
#define MIRROR_QUAD_RETURN 0x006e719eu /* only the rear-view image, not its frame/HUD */
#define CREATE_MIRROR_RESOURCES 0x006bcfa0u
#define FLUSH_RENDER_BATCHES 0x006e2f50u
#define BEFORE_MIRROR_RETURN 0x006de893u
#define DRAW_WORLD_SHADOWS 0x006e54e0u
#define MIRROR_VIEW 0x00919730u
#define SET_TRANSFORM 0x006c8000u        /* cdecl (D3DMATRIX *, view id) */
#define SET_TRANSFORM_RET_A 0x006e6fbcu
#define SET_TRANSFORM_RET_B 0x006e7016u
#define SET_WIDESCREEN_MODE 0x005696c0u  /* thiscall FEngHud::SetWideScreenMode */
#define QUEUE_PACKAGE_MESSAGE 0x00516c90u /* thiscall (hash, package, arg), ret 0xc */
#define QPM_RET_A 0x005696f6u
#define QPM_RET_B 0x00569717u
#define MINIMAP_ADJUST 0x005678a0u       /* thiscall (char wide), ret 4 */
#define FIND_OBJECT 0x00524850u          /* cdecl (package, hash) */
#define GET_CENTER 0x00524ee0u           /* cdecl (object, float *x, float *y) */
#define SET_CENTER 0x00525050u           /* cdecl (object, float x, float y) */

/* Data */
#define REDIRECTED_FRAME_TIME 0x00a37800u
#define SLOT_FOV_H 0x00a37804u
#define SLOT_FOV_HALF 0x00a37808u
#define SLOT_FOV_V 0x00a3780cu
#define SLOT_FOV_ASPECT 0x00a37810u
#define SLOT_SHADOW_H 0x00a37818u
#define SLOT_SHADOW_DISTANCE 0x00a3781cu
#define SLOT_MIRROR_A 0x00a37824u
#define SLOT_MIRROR_B 0x00a37828u
#define SLOT_RES_LAST 0x00a37830u   /* entries - 1 */
#define SLOT_RES_COUNT 0x00a37834u  /* entries */
#define SLOT_RES_SAVED 0x00a37838u  /* the registry value, packed */
#define TABLE_RES_SUPPORTED 0x00a37840u
#define TABLE_RES_REFRESH 0x00a37940u
#define TABLE_RES_LABEL 0x00a37a40u
#define SLOT_FMV_BOTTOM 0x00a37b40u
#define SLOT_FMV_RIGHT 0x00a37b44u
#define SLOT_FMV_TOP 0x00a37b48u
#define SLOT_FMV_LEFT 0x00a37b4cu
/* The part of a movie frame's height its picture covers. */
#define FMV_PICTURE_HEIGHT 0.796f
#define RES_MAX 64
#define RESOLUTION_INDEX 0x0090181cu
#define WORLD_TIMESTEP 0x00903290u
#define HUD_SCALE_X 0x008af9a4u
#define HUD_CENTRE_X 0x00894b40u
#define AUTOSCULPT_SCALE 0x008ae8f8u
#define ARREST_BLUR 0x008afa08u
#define MINIMAP_PIVOT_X 0x0091cf04u
#define MINIMAP_DISP_X 0x0091cf0cu
#define FENG_INSTANCE 0x0091cadcu
#define MOVIE_PLAYER 0x0091cb10u
#define SPLASH_WIDE_NAME 0x0089f828u
#define SPLASH_NAME 0x008a0114u
#define ORIGINAL_FRAME_TIME 0x3c888889u /* 1/60 */

static const PopModApi *g_api;
static uint32_t g_hooks[128];   /* 90..95: sparks and light trails (xenon_fx.h); 105..109: Extra Options (eo_extra.h) */
/* Owner-selected Extra Options that need hooks (native_options.h). */
static int g_exo_max_perf, g_exo_special_vinyls, g_exo_replay_blacklist;
static uint32_t g_exo_starting_cash, g_exo_rival;
static uint32_t g_frame_time_bits = ORIGINAL_FRAME_TIME;

static int g_widescreen = 1;
static float g_aspect = 4.0f / 3.0f;
static float g_hor = 1.0f, g_vert = 1.215f, g_half = 0.43434f;
static float g_hud_offset = 0.0f; /* how far the side HUD groups move out */
static float g_fe_scale = 1.0f;
static int g_hud_dirty = 0;
static uint32_t g_matrix = 0, g_floats = 0; /* guest scratch */

static uint32_t float_bits(float f) {
    uint32_t b;
    memcpy(&b, &f, 4);
    return b;
}
static float bits_float(uint32_t b) {
    float f;
    memcpy(&f, &b, 4);
    return f;
}
static void put_float(uint32_t addr, float f) {
    g_api->guest_write_u32(g_api, addr, float_bits(f));
}
static float get_float(uint32_t addr) {
    uint32_t b = 0;
    g_api->guest_read_u32(g_api, addr, &b);
    return bits_float(b);
}
static uint32_t get_u32(uint32_t addr) {
    uint32_t v = 0;
    g_api->guest_read_u32(g_api, addr, &v);
    return v;
}
/* Appearance: 0 Definitive (default), 2 PC - Original, 3 Definitive - Custom (appearance_recipe.h); 1 and 4 are
 * retired values and read as Definitive. The look the game was started with decides the resources loaded at boot
 * (the texture pack and the light pools); the bloom owner and the light passes follow the current value every
 * frame (look_poll). */
enum { LOOK_HYBRID = 0, LOOK_PC = 2 };
static int g_look = LOOK_HYBRID, g_look_boot = -1;
static int look_read(void) {
    int64_t v = LOOK_HYBRID;
    g_api->settings_get(g_api, "appearance", &v);
    return v == LOOK_PC ? LOOK_PC : LOOK_HYBRID;
}
/* Resources a PC-look launch leaves out (they replace PC data at load time). */
static int look_pc_boot_off(const char *key) {
    static const char *const keys[] = {"texture_pack", "clean_pools"};
    for (unsigned i = 0; i < sizeof keys / sizeof keys[0]; ++i)
        if (!strcmp(key, keys[i])) return 1;
    return 0;
}
static int64_t setting(const char *key, int64_t fallback) {
    int64_t v = fallback;
    g_api->settings_get(g_api, key, &v);
    if (g_look_boot < 0) g_look = g_look_boot = look_read();
    if (g_look_boot == LOOK_PC && look_pc_boot_off(key)) return 0;
    return v;
}

#include "impact_feedback.h"
#include "frontend_pad.h"
#include "texture_pack.h"
#include "native_options.h"
#include "native_audio.h"
#include "heli_bounty.h"
#include "wheel_probe.h"

/* ---- original PC input, with the requested PlayStation layout ----------- */

/* Verified against 00628940 and 006349b0 in the pinned executable. The first
 * two triples are keyboard bindings. Change only the two joystick triples,
 * in guest memory, after saved/custom device defaults have been loaded.
 * Kinds: 2 button, 3 POV, 4/5 negative/positive translation axis, 6/7
 * negative/positive rotation axis. DirectInput uses unsigned axes centred
 * at 32768. game.toml defines the button numbering and axis order.
 * This keeps the game's real analog steering and its keyboard path intact.
 * The driving rows are the player's: see pad_mapping.h. */
static void pad_binding(unsigned row, unsigned slot, uint32_t kind, uint32_t control) {
    uint32_t addr = GAME_BINDINGS + row * 52u + 28u + slot * 12u;
    g_api->guest_write_u32(g_api, addr, kind);
    g_api->guest_write_u32(g_api, addr + 4, 0);
    g_api->guest_write_u32(g_api, addr + 8, control);
}

#include "pad_mapping.h"    /* controller mapping: the Controls screen, analog triggers, names */
static void msaa_apply(const PopModApi *api, int reset);
static void quality_apply(const PopModApi *api, int reset);
static void look_poll(const PopModApi *api);
static void appearance_recipe(const PopModApi *api);
static void cop_numbers_poll(const PopModApi *api); /* cop_numbers.h */

#include "position_log.h"   /* Test136; needs setting/get_u32/get_float above */
#include "preculler_switch.h" /* Test149: optional preculler off (experimental) */
#include "memory_log.h"       /* Test151: game footprint + Mac swap once a minute (crash evidence) */
static void game_device_update(const PopModApi *api, pop_cpu_v1 *cpu,
                               PopHookInvocation *inv, void *user) {
    (void)inv;
    (void)user;
    uint32_t device = cpu->ecx;
    native_audio_present(api); /* Audio Mode presentation; host call only on change */
    quality_apply(api, 1); /* a chosen Quality preset, then the label from the actual values (Test84) */
    msaa_apply(api, 1); /* a settings-menu change of anti-aliasing; nothing when unchanged */
    appearance_recipe(api); /* a chosen appearance recipe, then the Original/Custom label (Test96) */
    look_poll(api);     /* an applied appearance change (Test77) */
    heat_levels_apply(api); /* Heat Level Override slot (Test88); logs only on a change */
    cop_numbers_poll(api);  /* TEST ONLY police-car count (NFSMW_TEST_COP_LOG) */
    position_log(api);  /* diagnostics: time + world position every 2 s while driving (Test136) */
    preculler_switch(api); /* Test149: only acts when the switch is off, or to restore */
    memory_log(api);       /* Test151: one line a minute */
    /* Driving rows are the player's (Options -> Controls); the layout is their default (pad_mapping.h). */
    pad_settings_poll();
    if (setting("ps2_controls", 1)) pad_frontend_rows();
    api->call_original(api, cpu->target, cpu);
    if (get_u32(device + 36) == 0) {
        pad_trigger_analog(api, device);
        pad_rows_save();   /* the player's current rows, kept across a controller reconnect */
        pad_mapping_test(api); /* TEST ONLY: NFSMW_TEST_PADMAP */
    }
    if (get_u32(device + 36) == 0) frontend_pad_activity(api);
    impact_feedback_tick(api);
    if (setting("input_trace", 0) && get_u32(device + 36) == 0) {
        static float previous[42];
        uint32_t values = get_u32(device + 32);
        if (values) {
            for (unsigned row = 0; row < 42; ++row) {
                float value = get_float(values + row * 4);
                if (fabsf(value - previous[row]) > 0.01f) {
                    char line[96];
                    snprintf(line, sizeof line, "core.nfsmw: input row=%u value=%.3f", row, value);
                    api->log(api, line);
                    previous[row] = value;
                }
            }
        }
    }
}

/* The PC front end emits trigger actions as relative events (mouse-wheel
 * style). A held pad trigger must maintain zoom speed until released. Keep
 * the original keyboard/mouse dispatcher, bounds and camera integrator. */
static void showcase_pad_zoom(const PopModApi *api, pop_cpu_v1 *cpu,
                              PopHookInvocation *inv, void *user) {
    (void)inv; (void)user;
    uint32_t mover = cpu->ecx;
    api->call_original(api, cpu->target, cpu);
    static uint32_t owned_camera;
    uint32_t camera = get_u32(mover + 0x4c), pad = get_u32(0x0091f150u), state = 0;
    float zoom = 0.0f;
    int held = 0;
    if (setting("ps2_controls", 1) && camera &&
        (get_u32(mover + 0x78) & 0xffu || get_u32(0x00926134u) & 0xffu) && pad) {
        uint32_t getter = get_u32(get_u32(pad) + 4);
        if (getter && api->guest_call(api, getter, pad, NULL, 0, &state) == POP_OK && state) {
            int out = get_u32(state + 12 + 6 * 4) != 0;
            int in = get_u32(state + 12 + 7 * 4) != 0;
            held = out || in;
            zoom = (float)(out - in);
        }
    }
    if (camera && (held || owned_camera == camera)) {
        if (setting("input_trace", 0)) {
            static float last_zoom = 9.0f, last_original = 9.0f;
            float original = get_float(camera + 0x120);
            if (zoom != last_zoom || original != last_original) {
                char line[180];
                snprintf(line, sizeof line, "core.nfsmw: showcase zoom camera=%08x original=%.3f pad=%.3f distance=%.3f", camera, original, zoom, get_float(camera + 0x98));
                api->log(api, line); last_zoom = zoom; last_original = original;
            }
        }
        uint32_t bits; memcpy(&bits, &zoom, 4);
        api->guest_write_u32(api, mover + 0x84, bits);
        api->guest_call(api, 0x00476ff0u, camera, &bits, 1, NULL);
        owned_camera = held ? camera : 0;
    } else {
        owned_camera = 0;
    }
}

/* ---- simulation rate ---------------------------------------------------- */

static void timer_frame_time(const PopModApi *api, pop_cpu_v1 *cpu, PopHookInvocation *inv, void *user) {
    uint32_t arg;
    (void)inv;
    (void)user;
    if (api->guest_read_u32(api, cpu->esp + 4, &arg) == POP_OK && arg == ORIGINAL_FRAME_TIME)
        api->guest_write_u32(api, cpu->esp + 4, g_frame_time_bits);
}

/* ---- resolution and everything that follows from it ---------------------- */

/* Entry 0 is "Auto"; w and h are 0 there. */
typedef struct {
    uint16_t w, h;
    uint32_t hash;
    char label[24];
} ResEntry;
static ResEntry g_res[RES_MAX];
static int g_res_count = 0;

static const uint16_t kCommonSizes[][2] = {
    {640, 480},   {768, 480},   {800, 600},   {854, 480},   {960, 540},   {960, 640},   {1024, 576},
    {1024, 768},  {1152, 864},  {1280, 720},  {1280, 800},  {1280, 960},  {1280, 1024}, {1360, 768},
    {1366, 768},  {1400, 1050}, {1440, 900},  {1600, 900},  {1600, 1200}, {1680, 1050}, {1920, 1080},
    {1920, 1200}, {2048, 1152}, {2048, 1536}, {2560, 1080}, {2560, 1440}, {2560, 1600}, {2880, 1800},
    {3200, 1800}, {3440, 1440}, {3840, 1600}, {3840, 2160}, {3840, 2400}, {4096, 2160}, {5120, 1440},
    {5120, 2160}, {5120, 2880}, {6016, 3384}, {7680, 4320},
};

/* The engine's string hash (bStringHash). */
static uint32_t string_hash(const char *s) {
    uint32_t h = 0xffffffffu;
    while (*s)
        h = h * 33u + (uint8_t)*s++;
    return h;
}

static void screen_shape(uint32_t *w, uint32_t *h) {
    uint32_t screen_w = 0, screen_h = 0;
    if (g_api->screen_size && g_api->screen_size(g_api, &screen_w, &screen_h) == POP_OK && screen_w &&
        screen_h) {
        float aspect = (float)screen_w / (float)screen_h;
        uint32_t rows = screen_h < 1080 ? screen_h : 1080;
        *h = rows;
        *w = ((uint32_t)lroundf(rows * aspect) + 1) & ~1u;
        return;
    }
    *w = 1280;
    *h = 720;
}

static void add_size(uint32_t w, uint32_t h) {
    if (g_res_count >= RES_MAX || !w || !h || w > 0x7fff || h > 0xffff)
        return;
    int at = 1;
    while (at < g_res_count && (g_res[at].w < w || (g_res[at].w == w && g_res[at].h < h)))
        ++at;
    if (at < g_res_count && g_res[at].w == w && g_res[at].h == h)
        return;
    memmove(&g_res[at + 1], &g_res[at], (size_t)(g_res_count - at) * sizeof g_res[0]);
    ResEntry *e = &g_res[at];
    char key[48];
    e->w = (uint16_t)w;
    e->h = (uint16_t)h;
    snprintf(e->label, sizeof e->label, "%ux%u", w, h);
    snprintf(key, sizeof key, "OPT_VO_PC_RES_%uX%u", w, h);
    e->hash = string_hash(key);
    ++g_res_count;
}

static void build_resolution_list(void) {
    uint32_t sw = 0, sh = 0, aw, ah;
    screen_shape(&aw, &ah);
    memset(g_res, 0, sizeof g_res);
    snprintf(g_res[0].label, sizeof g_res[0].label, "Auto (%ux%u)", aw, ah);
    g_res[0].hash = string_hash("OPT_VO_PC_RES_AUTO");
    g_res_count = 1;
    for (size_t i = 0; i < sizeof kCommonSizes / sizeof kCommonSizes[0]; ++i)
        add_size(kCommonSizes[i][0], kCommonSizes[i][1]);
    if (g_api->screen_size && g_api->screen_size(g_api, &sw, &sh) == POP_OK)
        add_size(sw, sh);
    for (int i = 0; i < RES_MAX; ++i) {
        g_api->guest_write_u32(g_api, TABLE_RES_SUPPORTED + 4u * (uint32_t)i, i < g_res_count);
        g_api->guest_write_u32(g_api, TABLE_RES_REFRESH + 4u * (uint32_t)i, 0); /* the default rate */
        g_api->guest_write_u32(g_api, TABLE_RES_LABEL + 4u * (uint32_t)i, i < g_res_count ? g_res[i].hash : 0);
    }
    g_api->guest_write_u32(g_api, SLOT_RES_LAST, (uint32_t)g_res_count - 1);
    g_api->guest_write_u32(g_api, SLOT_RES_COUNT, (uint32_t)g_res_count);
}

static int resolution_index(void) {
    uint32_t i = get_u32(RESOLUTION_INDEX);
    return i < (uint32_t)g_res_count ? (int)i : 0;
}

static void choose_resolution(uint32_t *w, uint32_t *h) {
    int64_t sw = setting("width", 0), sh = setting("height", 0);
    if (sw > 0 && sh > 0) {
        *w = (uint32_t)sw;
        *h = (uint32_t)sh;
        return;
    }
    const ResEntry *e = &g_res[resolution_index()];
    if (e->w && e->h) {
        *w = e->w;
        *h = e->h;
        return;
    }
    screen_shape(w, h);
}

/* HD Reflections AutoRes uses half the width and one sixth the height.
 * Use the selected game size, not the Retina display size. The verified
 * resource constructor reads these globals for both colour and depth;
 * 006bd0d0 also uses them for the mirror viewport. Re-evaluate on reset. */
static void mirror_resources(const PopModApi *api, pop_cpu_v1 *cpu,
                             PopHookInvocation *inv, void *user) {
    uint32_t w = 256, h = 256;
    (void)cpu; (void)inv; (void)user;
    if (setting("mirror_hd", 1)) {
        choose_resolution(&w, &h);
        w /= 2; h /= 6;
        if (w < 64) w = 64;
        if (h < 32) h = 32;
        if (w > 4096) w = 4096;
        if (h > 2048) h = 2048;
    }
    api->guest_write_u32(api, 0x008f9008u, w);
    api->guest_write_u32(api, 0x008f900cu, h);
    char line[96];
    snprintf(line, sizeof line, "core.nfsmw: mirror colour/depth target %ux%u", w, h);
    api->log(api, line);
}

/* Test62: the rear-view mirror stays visible while looking back.
 * The mirror state update 006cfad0 (state 00982b3c: 1 off, 2 on) asks 00595d40(view 1) whether
 * the mirror is allowed (its only caller). 00595d40 returns 0 when any of these holds:
 *  (a) 0092d884 set and the camera director's current action (*[0092d87c] slot 0x20) answers
 *      slot 0x18 = 0 (005a0c20: [+0x18] | [+0x1c] == 0);
 *  (b) the view's first camera mover is type 1 and slot 0x30 is non-zero (004741d0: 0x8000,
 *      a 180 degree yaw, while byte [+0xa1] is set: Look Back);
 *  (c) 00525b20(1) (pause/overlay); (d) no options or option byte +0x39 clear, or the view's
 *      camera setting is 2..6 (the views without a mirror); (e) 00516b50(0089fbec) fails.
 * Test62 traces (smoke m5/m6): holding Look Back sets (b) at once and (a) from the next frame;
 * (c)(d)(e) stay as before. Policy while Look Back is held on a type-1 mover with that exact
 * slot 0x30: the same checks without the two Look Back vetoes (a)(b), in the original order;
 * otherwise the original, unchanged. Not HD Reflections' ForceEnableMirror (all camera views):
 * (d) still hides the mirror in chase views. An optional enhancement (default off); `mirror_look_back` 0 is the
 * original. */
static uint32_t mirror_view_mover(uint32_t view) {
    const uint32_t list = 0x009195e0u + 0x70u * view + 0x44u, first = get_u32(list);
    return (first && first != list) ? first - 4 : 0;
}
static void mirror_look_back(const PopModApi *api, pop_cpu_v1 *cpu,
                             PopHookInvocation *inv, void *user) {
    (void)inv; (void)user;
    const uint32_t view = get_u32(cpu->esp + 4);
    const uint32_t mover = view < 8 ? mirror_view_mover(view) : 0;
    uint8_t looking_back = 0;
    static uint32_t last_mover;   /* the mover that was looking back on the previous call */
    /* Default OFF since Test144: in Look Back the mirror only duplicated the screen. */
    const int eligible = setting("mirror_look_back", 0) && mover && get_u32(mover + 0xc) == 1 &&
                         get_u32(get_u32(mover) + 0x30) == 0x004741d0u && api->guest_call &&
                         api->guest_read_u8(api, mover + 0xa1, &looking_back) == POP_OK;
    /* Release: the director's action (a) still reports Look Back for one more frame (Test62 m7:
     * a one-frame drop to state 1 at release), so the first call after release is treated alike. */
    const int just_released = eligible && !looking_back && last_mover == mover;
    last_mover = eligible && looking_back ? mover : 0;
    if (!eligible || (!looking_back && !just_released)) {
        api->call_original(api, cpu->target, cpu);
        return;
    }
    uint32_t result = 0, one = 1, r = 0;
    do {
        if (api->guest_call(api, 0x00525b20u, 0, &one, 1, &r) != POP_OK || (r & 0xffu)) break;      /* (c) */
        const uint32_t opts = get_u32(0x0091cf90u);
        if (!opts) break;
        const uint32_t o = get_u32(opts + 0x10);
        uint8_t enabled = 0;
        if (!o || api->guest_read_u8(api, o + 0x39, &enabled) != POP_OK || !enabled) break;          /* (d) */
        const uint32_t camera = get_u32(o + (view * 5u + 0x1eu) * 4u);
        if (camera >= 2 && camera <= 6) break;
        uint32_t key = 0x0089fbecu;                                                                  /* (e) */
        if (api->guest_call(api, 0x00516b50u, get_u32(0x0091cadcu), &key, 1, &r) != POP_OK) break;
        result = (r & 0xffu) != 0;
    } while (0);
    if (setting("input_trace", 0)) {
        static uint32_t last = 0xffffffffu;
        if (result != last) {
            char line[96];
            snprintf(line, sizeof line, "core.nfsmw: mirror in Look Back: allowed=%u (view %u)", result, view);
            api->log(api, line);
            last = result;
        }
    }
    api->hook_return(api, cpu, (cpu->eax & 0xffffff00u) | result, 0);   /* cdecl: caller pops */
}

/* HD Reflections' full mirror scenery selection: 006bfdd0 supplies 0x1000
 * for view3, and 004fae40 additionally sets reduced-detail bit0x20.
 * Apply only to this view; keep the road/cubemap/player branches untouched.
 * The resulting 0x6002 input and cleared0x20 match the mod's two patches. */
static void mirror_scenery(const PopModApi *api, pop_cpu_v1 *cpu,
                           PopHookInvocation *inv, void *user) {
    (void)inv; (void)user;
    uint32_t view = get_u32(cpu->esp + 4), cull = get_u32(cpu->esp + 8);
    int enabled = view == MIRROR_VIEW && setting("mirror_full_scenery", 1);
    if (enabled) api->guest_write_u32(api, cpu->esp + 12, 0x6002);
    api->call_original(api, cpu->target, cpu);
    if (enabled && cull) {
        uint32_t flags = get_u32(cull + 0x84) & ~0x20u;
        api->guest_write_u32(api, cull + 0x84, flags);
        static int logged;
        if (!logged++) api->log(api, "core.nfsmw: full mirror scenery selection active");
    }
}

/* HD Reflections' mirror-shadow pass, verified against 006de300 and
 * 006e54e0. The original caller owns a frustum scratch matrix at ESP+e0.
 * Our entry still has its return address, hence +e4. guest_call preserves
 * registers/x87; all pointers remain guest addresses. The extra pass only
 * runs when the mirror is enabled, has a camera, and shadows are visible. */
/* Set when the mirror pass has drawn its target this frame. */
static int g_mirror_fresh;
static void mirror_shadows(const PopModApi *api, pop_cpu_v1 *cpu,
                           PopHookInvocation *inv, void *user) {
    uint32_t scratch = cpu->esp + 0xe4u;
    uint8_t mirror_on = 0;
    void *validated = NULL;
    (void)inv; (void)user;
    api->call_original(api, FLUSH_RENDER_BATCHES, cpu);
    g_mirror_fresh = 1;
    api->guest_read_u8(api, MIRROR_VIEW + 8, &mirror_on);
    /* Default OFF since Test142 (3 Oct): ~1.35 ms per frame (Test141); a second shadow-map render for the mirror. */
    if (!setting("mirror_shadows", 0) || !mirror_on || !get_u32(MIRROR_VIEW + 0x40) ||
        !get_u32(0x00901830u) || !(get_float(0x008fae68u) >= get_float(0x008fae50u)) ||
        api->guest_ptr(api, scratch, 64, &validated) != POP_OK)
        return;
    const uint32_t args[] = {MIRROR_VIEW, scratch};
    uint32_t unused = 0;
    /* TEST ONLY (Test141, NFSMW_TEST_MIRROR_COST=1): time of this extra world-shadow pass, per 600 frames. */
    static int cost = -1;
    static uint64_t cost_ns; static uint32_t cost_frames;
    if (cost < 0) cost = getenv("NFSMW_TEST_MIRROR_COST") != NULL;
    struct timespec c0, c1;
    if (cost) clock_gettime(CLOCK_MONOTONIC, &c0);
    if (api->guest_call(api, DRAW_WORLD_SHADOWS, 0, args, 2, &unused) == POP_OK) {
        api->guest_call(api, FLUSH_RENDER_BATCHES, 0, NULL, 0, &unused);
        if (cost) {
            clock_gettime(CLOCK_MONOTONIC, &c1);
            cost_ns += (uint64_t)(c1.tv_sec - c0.tv_sec) * 1000000000ull + (uint64_t)(c1.tv_nsec - c0.tv_nsec);
            if (++cost_frames % 600 == 0) {
                char line[128];
                snprintf(line, sizeof line, "core.nfsmw: TEST mirror cost: world-shadow pass %.1f us/frame", cost_ns / 600.0 / 1000.0);
                api->log(api, line);
                cost_ns = 0;
            }
        }
        static int logged = 0;
        if (!logged++) api->log(api, "core.nfsmw: mirror world-shadow pass active");
    }
}

/* Test73 (Development 59 test session: police light bars dark in the rear-view mirror).
 * The game draws the per-frame vehicle light flares (headlights, brake lights, police light
 * bars: the pool at 00916a78, stride 0x30, count 00916018) with 00507770(view). The player
 * view (006de620/006de817/006df359) calls it; the PC mirror pass in 006de300 (006de96d-006de9f8)
 * calls only the world-section flares 00505e80 and never 00507770, although 00507770 keeps a
 * dedicated branch for view 3 (the mirror, 00919730). HD Reflections' RestoreDetails adds the
 * call for the mirror at 006de9f0 and tests visibility bit 4 instead of the mirror's own bit 3
 * of the per-flare view mask (00915aa8[i]; its patch is "mov eax,2" for "mov eax,1" at
 * 0050777c). Same here, without code patches: after the mirror's own 00505e80 call returns
 * (006de9f5; only reached when the game's flares are on, 009017e4), copy bit 4 of each mask
 * into bit 3, call 00507770(mirror view), and put every mask back exactly as it was. */
#define FLARE_POOL_COUNT 0x00916018u
#define FLARE_VIEW_MASKS 0x00915aa8u
#define FLARE_POOL_MAX 348u /* the mask array ends where the count begins */
#define RENDER_VEHICLE_FLARES 0x00507770u
/* TEST ONLY (NFSMW_TEST_MIRROR_LIGHT_LOG): flares 00505380 is asked to draw for the mirror. */
static int g_mirror_lights_active;
static uint32_t g_mirror_flare_submits;
static void mirror_flare_count(const PopModApi *api, pop_cpu_v1 *cpu, PopHookInvocation *inv, void *user) {
    (void)api; (void)inv; (void)user;
    if (g_mirror_lights_active && get_u32(cpu->esp + 4) == MIRROR_VIEW) ++g_mirror_flare_submits;
}
static void mirror_lights(const PopModApi *api, pop_cpu_v1 *cpu,
                          PopHookInvocation *inv, void *user) {
    (void)cpu; (void)inv; (void)user;
    if (!setting("mirror_lights", 1)) return;
    uint32_t count = get_u32(FLARE_POOL_COUNT);
    if (count > FLARE_POOL_MAX) count = FLARE_POOL_MAX;
    uint32_t *masks = NULL, saved[FLARE_POOL_MAX];
    if (count && api->guest_ptr(api, FLARE_VIEW_MASKS, count * 4u, (void **)&masks) != POP_OK) return;
    static int own_bit = -1;
    if (own_bit < 0) own_bit = getenv("NFSMW_TEST_MIRROR_OWN_MASK") ? 1 : 0; /* TEST ONLY: bit 3 */
    for (uint32_t i = 0; i < count; ++i) {
        saved[i] = masks[i];
        if (!own_bit) masks[i] = (masks[i] & ~0x8u) | ((masks[i] >> 1) & 0x8u);
    }
    uint32_t own_clear = 0, used_clear = 0;
    for (uint32_t i = 0; i < count; ++i) {
        own_clear += !(saved[i] & 0x8u);
        used_clear += !(masks[i] & 0x8u);
    }
    const uint32_t view = MIRROR_VIEW;
    uint32_t unused = 0;
    const uint32_t submits_before = g_mirror_flare_submits;
    g_mirror_lights_active = 1;
    PopModStatus r = api->guest_call(api, RENDER_VEHICLE_FLARES, 0, &view, 1, &unused);
    g_mirror_lights_active = 0;
    /* 00507770 neither adds nor removes pool entries; restore what was there. */
    if (count && api->guest_ptr(api, FLARE_VIEW_MASKS, count * 4u, (void **)&masks) == POP_OK)
        for (uint32_t i = 0; i < count; ++i) masks[i] = saved[i];
    static uint32_t calls, drawn_frames;
    ++calls;
    if (count) ++drawn_frames;
    static int test_log = -1;
    if (test_log < 0) test_log = getenv("NFSMW_TEST_MIRROR_LIGHT_LOG") != NULL;
    if (test_log && calls % 120 == 0) {
        char line[200];
        snprintf(line, sizeof line, "core.nfsmw: TEST mirror lights call %u: pool %u, mirror bit clear %u, view-4 bit clear %u, flares submitted %u",
                 calls, count, own_clear, used_clear, g_mirror_flare_submits - submits_before);
        api->log(api, line);
    }
    if (r != POP_OK || calls == 1 || (setting("input_trace", 0) && calls % 600 == 0)) {
        char line[160];
        snprintf(line, sizeof line, "core.nfsmw: mirror vehicle lights %s (call %u, %u frames with flares, %u now)",
                 r == POP_OK ? "drawn" : "call failed", calls, drawn_frames, count);
        api->log(api, line);
    }
}

/* The PC detail slider re-enables the PC colour treatment/overbright/blur.
 * Those are independent of geometry quality and conflict with the accepted
 * native mod presentation. Apply at load/save/reset boundaries, not per draw.
 * This explicit native option is reversible for original-PC comparisons. */
static void video_mod_lighting(const PopModApi *api) {
    /* Appearance. PC: the PC's own colour treatment and OverBright bloom (the rebuilt effects stand aside while
     * either is on); motion blur stays the game's own option. */
    if (g_look == LOOK_PC) {
        api->guest_write_u32(api, 0x00901828u, 1); /* VisualTreatment */
        api->guest_write_u32(api, 0x009017fcu, 1); /* OverBright */
        return;
    }
    /* One bloom: while the rebuilt effects are on (clean_effects), the PC OverBright bloom stays off whatever
     * mod_lighting says. */
    if (setting("clean_effects", 1)) api->guest_write_u32(api, 0x009017fcu, 0); /* OverBright */
    if (!setting("mod_lighting", 1)) return;
    api->guest_write_u32(api, 0x00901828u, 0); /* VisualTreatment: the PC sepia/golden colour grade */
    api->guest_write_u32(api, 0x009017fcu, 0); /* OverBright */
    api->guest_write_u32(api, 0x009017dcu, 0); /* MotionBlur */
}

/* Once per device update: an applied appearance change takes effect here - the PC treatment/bloom flags
 * at once, the effects and light passes from the next frame (they read g_look). Resources chosen at
 * launch (look_pc_boot_off) follow at the next launch; the log says so. */
static void look_poll(const PopModApi *api) {
    if (g_look_boot < 0) g_look = g_look_boot = look_read();
    const int look = look_read();
    if (look == g_look) return;
    static const char *const names[] = {"Definitive", "Definitive", "PC - Original"};
    const int from = g_look;
    g_look = look;
    video_mod_lighting(api);
    char line[300];
    const int pending = (look == LOOK_PC) != (g_look_boot == LOOK_PC);
    snprintf(line, sizeof line, "core.nfsmw: appearance %s -> %s applied (colour treatment, bloom, light passes)%s",
             names[from], names[look],
             pending ? look == LOOK_PC ? "; the texture pack and light pools loaded at launch stay until the next launch"
                                       : "; the texture pack and light pools load at the next launch"
                     : "");
    api->log(api, line);
}

/* TEST ONLY (NFSMW_TEST_LOOK_KEY set): F3 or F4 switches Definitive <-> PC - Original through the same settings
 * path the menu's Apply uses. The key is consumed. Not installed otherwise. */
static uint32_t g_look_key_hook;
static int32_t look_test_key(const PopModApi *api, int32_t dik, int32_t vk, int32_t down, void *user) {
    (void)vk; (void)user;
    if (dik != 0x3d && dik != 0x3e) return 0; /* DIK_F3 forward, DIK_F4 back */
    if (down) {
        const int next = look_read() == LOOK_PC ? LOOK_HYBRID : LOOK_PC;
        char line[96];
        snprintf(line, sizeof line, "core.nfsmw: appearance: TEST key sets %d", next);
        api->log(api, line);
        api->settings_set(api, "appearance", next);
    }
    return 1;
}

/* Anti-aliasing from the settings menu (Test73; MENU3 slice 1). The game's own FSAA value
 * 00901808 (0 off; 1 = NONMASKABLE quality 1, which the shim draws as 4x) is read when the device's
 * present parameters are rebuilt (006bfab0); its own video menu (0051034f/0051d1a4) asks for that
 * rebuild by setting the byte 00982c39, which 006e7220 handles at a safe point: resources released,
 * present parameters rebuilt, the device reset (006db0d0, which itself drops FSAA to 0 and retries
 * if the reset fails). The setting `msaa` is -1 (the game's own value, the default, so no saved
 * profile changes), 0 off or 1 4x. It is applied at video load (before the device exists) and, when
 * the menu changes it, once through the same request the game's menu makes - an edge, never a
 * continuous override, so a refused reset is not retried. */
#define GAME_FSAA 0x00901808u
#define GAME_VIDEO_RESET_REQUEST 0x00982c39u
static int64_t g_msaa_applied = -2;
/* Test75: after a requested reset, the value it actually ran with. The game's reset path drops
 * FSAA to 0 and retries when the device refuses the multisampled one; the setting then says what
 * is really running (Off) instead of the refused 4x, and the log says why. -1: nothing pending. */
static int32_t g_msaa_verify = -1;
static void msaa_verify(const PopModApi *api) {
    if (g_msaa_verify < 0)
        return;
    uint8_t pending = 0;
    api->guest_read_u8(api, GAME_VIDEO_RESET_REQUEST, &pending);
    if (pending)
        return; /* the game has not handled the request yet */
    const uint32_t running = get_u32(GAME_FSAA);
    const uint32_t wanted = (uint32_t)g_msaa_verify;
    g_msaa_verify = -1;
    char line[160];
    if (running == wanted) {
        snprintf(line, sizeof line, "core.nfsmw: anti-aliasing %s confirmed after the renderer reset",
                 running ? "4x" : "off");
        api->log(api, line);
        return;
    }
    snprintf(line, sizeof line,
             "core.nfsmw: anti-aliasing %s was refused by the renderer reset; running %s (setting changed to match)",
             wanted ? "4x" : "off", running ? "4x" : "off");
    api->log(api, line);
    const int64_t effective = running ? 1 : 0;
    g_msaa_applied = effective; /* not a new request: the game already runs it */
    api->settings_set(api, "msaa", effective);
}
static void msaa_apply(const PopModApi *api, int reset) {
    if (reset)
        msaa_verify(api);
    const int64_t want = setting("msaa", -1);
    if (want == g_msaa_applied)
        return;
    g_msaa_applied = want;
    if (want < 0)
        return;
    const uint32_t fsaa = want ? 1u : 0u;
    if (get_u32(GAME_FSAA) == fsaa)
        return;
    api->guest_write_u32(api, GAME_FSAA, fsaa);
    if (reset) {
        api->guest_write_u8(api, GAME_VIDEO_RESET_REQUEST, 1);
        g_msaa_verify = (int32_t)fsaa;
    }
    char line[120];
    snprintf(line, sizeof line, "core.nfsmw: anti-aliasing %s (%s)", fsaa ? "4x" : "off",
             reset ? "renderer reset requested" : "at video load");
    api->log(api, line);
}

#include "quality_presets.h"
#include "appearance_recipe.h"

static void video_settings_log(const PopModApi *api, const char *reason) {
    char line[280];
    uint32_t w = 0, h = 0;
    choose_resolution(&w, &h);
    snprintf(line, sizeof line,
        "core.nfsmw: video %s %ux%u level=%u car-reflection=%u road=%u world=%u shadow=%u filter=%u VT=%u bloom=%u blur=%u guest-vsync=%u fsaa=%u",
        reason, w, h, get_u32(0x00982c00u), get_u32(0x009017b8u),
        get_u32(0x009017d4u), get_u32(0x009017f4u), get_u32(0x00901830u),
        get_u32(0x00901818u), get_u32(0x00901828u), get_u32(0x009017fcu),
        get_u32(0x009017dcu), get_u32(0x00901824u), get_u32(0x00901808u));
    api->log(api, line);
}

/* Verified in 006c1990/006c1780 and the six-face table at008f9028:
 * the legacy top preset selects car-reflection1 (one face each frame),
 * road2/world2/shadow1/filter1. The accepted HQ preset uses 3/3/3/2/2.
 * Upgrade only a newly selected top preset; custom individual reductions
 * and saved settings remain intact. Never exceed the game's actual caps.
 * Preserve VSync: changing detail should not silently turn it off. */
static void video_preset(const PopModApi *api, pop_cpu_v1 *cpu,
                         PopHookInvocation *inv, void *user) {
    (void)inv; (void)user;
    uint32_t vsync = get_u32(0x00901824u);
    api->call_original(api, cpu->target, cpu);
    if (setting("mod_hq_preset", 1) && get_u32(0x00982c00u) >= 4) {
        static const struct { uint32_t value, cap, high; } quality[] = {
            {0x009017b8u, 0x00901840u, 3}, /* all six cubemap faces each frame */
            {0x009017bcu, 0x00901844u, 1},
            {0x009017d4u, 0x0090185cu, 3},
            {0x009017f4u, 0x0090187cu, 3},
            {0x009017f8u, 0x00901880u, 1},
            {0x00901830u, 0x009018b8u, 2},
            {0x00901818u, 0x009018a0u, 2},
        };
        for (unsigned i = 0; i < sizeof quality / sizeof quality[0]; ++i) {
            uint32_t cap = get_u32(quality[i].cap);
            api->guest_write_u32(api, quality[i].value,
                                 cap < quality[i].high ? cap : quality[i].high);
        }
    }
    api->guest_write_u32(api, 0x00901824u, vsync);
    video_mod_lighting(api);
    video_settings_log(api, "preset");
}

/* The registry keeps the size, not its place in the list. */
static void save_settings(const PopModApi *api, pop_cpu_v1 *cpu, PopHookInvocation *inv, void *user) {
    const ResEntry *e = &g_res[resolution_index()];
    uint32_t packed = e->w ? 0x80000000u | (uint32_t)e->w << 16 | e->h : 0;
    (void)inv;
    (void)user;
    (void)cpu;
    api->guest_write_u32(api, SLOT_RES_SAVED, packed);
    video_mod_lighting(api);
    video_settings_log(api, "save");
}

static void load_settings(const PopModApi *api, pop_cpu_v1 *cpu, PopHookInvocation *inv, void *user) {
    (void)inv;
    (void)user;
    /* No value, or one the original game saved (a place in its own list of
     * six), leaves "Auto". */
    api->guest_write_u32(api, SLOT_RES_SAVED, 0);
    api->guest_write_u32(api, RESOLUTION_INDEX, 0);
    api->call_original(api, cpu->target, cpu);
    uint32_t packed = get_u32(SLOT_RES_SAVED);
    int index = 0;
    if (packed & 0x80000000u) {
        uint32_t w = (packed >> 16) & 0x7fff, h = packed & 0xffff;
        for (int i = 1; i < g_res_count; ++i)
            if (g_res[i].w == w && g_res[i].h == h)
                index = i;
    }
    api->guest_write_u32(api, RESOLUTION_INDEX, (uint32_t)index);
    video_mod_lighting(api);
    g_qp_applied = -2;
    quality_apply(api, 0);
    g_msaa_applied = -2;
    msaa_apply(api, 0);
    video_settings_log(api, "load");
}

/* The option's labels: the game has strings for its own six sizes only. */
static void localized_wide(const PopModApi *api, pop_cpu_v1 *cpu, PopHookInvocation *inv, void *user) {
    uint32_t out = 0, size = 0, hash = 0;
    (void)inv;
    (void)user;
    api->guest_read_u32(api, cpu->esp + 4, &out);
    api->guest_read_u32(api, cpu->esp + 8, &size);
    api->guest_read_u32(api, cpu->esp + 12, &hash);
    for (int i = 0; i < g_res_count && out && size; ++i) {
        if (g_res[i].hash != hash)
            continue;
        const char *s = g_res[i].label;
        size_t n = strlen(s);
        if (n + 1 > size)
            n = size - 1;
        void *p;
        if (api->guest_ptr(api, out, (uint32_t)(2 * (n + 1)), &p) != POP_OK)
            break;
        uint8_t *b = (uint8_t *)p;
        for (size_t k = 0; k < n; ++k) {
            b[2 * k] = (uint8_t)s[k];
            b[2 * k + 1] = 0;
        }
        b[2 * n] = b[2 * n + 1] = 0;
        api->hook_return(api, cpu, (cpu->eax & 0xffffff00u) | 1u, 0);
        return;
    }
    api->call_original(api, cpu->target, cpu);
}

static void apply_resolution(uint32_t w, uint32_t h) {
    int64_t scaling = setting("fov_scaling", 1);
    float fe = (float)setting("fe_scale", 100) / 100.0f;
    g_aspect = (float)w / (float)h;
    g_hor = (4.0f / 3.0f) / g_aspect;
    g_vert = 1.215f;
    g_half = 0.43434f;
    if (scaling) {
        g_hor /= 1.047485948f;
        g_vert = scaling == 2 ? 1.27f : 1.21f;
    }
    float hud_scale = (1.0f / (float)w * ((float)h / 480.0f)) * 2.0f;
    float centre = 1.0f / hud_scale; /* 320 at 4:3 */
    put_float(HUD_SCALE_X, hud_scale);
    put_float(HUD_CENTRE_X, centre);
    put_float(AUTOSCULPT_SCALE, 480.0f * g_aspect);
    put_float(ARREST_BLUR, (1.0f / 640.0f) * ((4.0f / 3.0f) / g_aspect));
    put_float(SLOT_MIRROR_A, (centre - 320.0f) + 450.0f);
    put_float(SLOT_MIRROR_B, (centre - 320.0f) + 190.0f);
    put_float(SLOT_SHADOW_H, g_hor);
    g_hud_offset = 240.0f * g_aspect - 320.0f;
    g_fe_scale = fe * g_aspect / (4.0f / 3.0f);
    if (g_fe_scale > fe)
        g_fe_scale = fe;
    {
        /* 0.5 is the 4:3 frame's half size; the screen is aspect / (4/3) of
         * that wide and the picture 1 / FMV_PICTURE_HEIGHT of it tall. */
        float s = g_aspect / (4.0f / 3.0f);
        if (s > 1.0f / FMV_PICTURE_HEIGHT)
            s = 1.0f / FMV_PICTURE_HEIGHT;
        if (s < 1.0f)
            s = 1.0f;
        put_float(SLOT_FMV_BOTTOM, 0.5f * s);
        put_float(SLOT_FMV_RIGHT, 0.5f * s);
        put_float(SLOT_FMV_TOP, -0.5f * s);
        put_float(SLOT_FMV_LEFT, -0.5f * s);
    }
    g_hud_dirty = 1;
}

/* The verified movie-only callsite (0059a03f ->00591460) supplies a
 * normalized quad transformed by the FE asset's matrix, plus UV bounds
 * width/nextPowerOfTwo(width), height/nextPowerOfTwo(height). The actual PC
 * texture creator006ddd90 requests exact dimensions, as our Metal capture
 * confirms (1280x720). Sampling only0.625x0.703125 crops the decoded image.
 * Fit full widescreen movies inside the current480-high FE viewport using
 * the actual asset matrix (the shipped movie asset scales640x512). Preserve
 * embedded-letterbox sizing for old4:3 movies and preserve vertical UV flip.
 * No global texture, decoder, subtitle or unrelated FE transform changes. */
static void movie_fit(const PopModApi *api, pop_cpu_v1 *cpu,
                      PopHookInvocation *inv, void *user) {
    (void)inv; (void)user;
    if (!setting("movie_fit", 1)) return;
    uint32_t player = get_u32(MOVIE_PLAYER);
    if (!player || get_u32(player + 0x11cu) != 5) return;
    uint32_t dimensions = get_u32(0x0091cb6cu);
    uint32_t w = dimensions & 0xffffu, h = dimensions >> 16;
    if (!w || !h || w > 8192 || h > 8192) return;
    /*006ddd90 allocates exactly TextureInfo width x height, not POT padding. */
    put_float(cpu->esp + 24, 0.0f);
    put_float(cpu->esp + 32, 1.0f);
    put_float(cpu->esp + 28, get_float(cpu->esp + 28) > 0.0f ? 1.0f : 0.0f);
    put_float(cpu->esp + 36, get_float(cpu->esp + 36) > 0.0f ? 1.0f : 0.0f);
    float aspect = (float)w / (float)h;
    if (g_widescreen && aspect > 1.5f) {
        float mx = fabsf(get_float(cpu->ecx + 0x30));
        float my = fabsf(get_float(cpu->ecx + 0x44));
        if (mx > 0.0f && my > 0.0f) {
            float height = 480.0f * fminf(1.0f, g_aspect / aspect);
            float x = height * aspect / (2.0f * mx);
            float y = height / (2.0f * my);
            put_float(cpu->esp + 4, -x);
            put_float(cpu->esp + 8, -y);
            put_float(cpu->esp + 12, x);
            put_float(cpu->esp + 16, y);
        }
    }
    static uint32_t last_dimensions;
    static float last_aspect;
    if (dimensions != last_dimensions || g_aspect != last_aspect) {
        char line[144];
        snprintf(line, sizeof line, "core.nfsmw: movie %ux%u full-texture fit; screen aspect %.4f",
                 w, h, g_aspect);
        api->log(api, line);
        last_dimensions = dimensions; last_aspect = g_aspect;
    }
}

static void resolution(const PopModApi *api, pop_cpu_v1 *cpu, PopHookInvocation *inv, void *user) {
    uint32_t w, h, pw, ph;
    (void)inv;
    (void)user;
    choose_resolution(&w, &h);
    if (api->guest_read_u32(api, cpu->esp + 4, &pw) == POP_OK && pw)
        api->guest_write_u32(api, pw, w);
    if (api->guest_read_u32(api, cpu->esp + 8, &ph) == POP_OK && ph)
        api->guest_write_u32(api, ph, h);
    if (g_widescreen)
        apply_resolution(w, h);
    api->hook_return(api, cpu, cpu->eax, 8);
}

/* ---- field of view ------------------------------------------------------ */

static void view_projection(const PopModApi *api, pop_cpu_v1 *cpu, PopHookInvocation *inv, void *user) {
    uint32_t view = 0, id = 0;
    float h = 1.0f, half = 0.5f, v = 1.0f, aspect = 1.0f;
    (void)inv;
    (void)user;
    if (api->guest_read_u32(api, cpu->esp + 4, &view) != POP_OK || !view ||
        api->guest_read_u32(api, view + 4, &id) != POP_OK)
        return;
    if (id == 1 || id == 4) { /* the player's view and the headlights */
        h = g_hor;
        half = g_half;
        v = g_vert;
    }
    if (id == 3) /* rear-view mirror */
        aspect = 0.4f;
    put_float(SLOT_FOV_H, h);
    put_float(SLOT_FOV_HALF, half);
    put_float(SLOT_FOV_V, v);
    put_float(SLOT_FOV_ASPECT, aspect);
}

/* The pinned PC mirror compositor fills four RGBA colours with 255 at
 * 006e7120. Its captured pixel shader f8813735427aff31 multiplies RGB by
 * texture * vertex colour * 2, clipping cloudy sky before presentation.
 * Use the nearest byte to half intensity for this quad only. Alpha stays
 * untouched; the shared front-end shader and the mirror render pass retain
 * their original behavior. Disable mirror_neutral_exposure for an A/B run.
 * The 006d1260 listing verifies the four RGBA records at quad+0x80. */
static void mirror_exposure(const PopModApi *api, pop_cpu_v1 *cpu,
                            PopHookInvocation *inv, void *user) {
    uint32_t quad = 0;
    void *data = NULL;
    (void)inv;
    (void)user;
    if (!setting("mirror_neutral_exposure", 1) ||
        api->guest_read_u32(api, cpu->esp + 4, &quad) != POP_OK || !quad ||
        api->guest_ptr(api, quad, 0x90, &data) != POP_OK)
        return;
    uint8_t *colours = (uint8_t *)data + 0x80;
    for (unsigned vertex = 0; vertex < 4; ++vertex)
        for (unsigned channel = 0; channel < 3; ++channel)
            colours[vertex * 4 + channel] = 128;
}

/* ---- front end and cinematics scale -------------------------------------- */

static void set_transform(const PopModApi *api, pop_cpu_v1 *cpu, PopHookInvocation *inv, void *user) {
    uint32_t mat;
    void *src;
    /* A cinematic's quad is sized on its own; the rest of the front end keeps
     * its place, so what sits off screen stays there. */
    float scale = get_u32(MOVIE_PLAYER) ? 1.0f : g_fe_scale;
    (void)inv;
    (void)user;
    if (scale == 1.0f || !g_matrix || api->guest_read_u32(api, cpu->esp + 4, &mat) != POP_OK || !mat)
        return;
    if (api->guest_ptr(api, mat, 64, &src) != POP_OK)
        return;
    void *dst;
    if (api->guest_ptr(api, g_matrix, 64, &dst) != POP_OK)
        return;
    memcpy(dst, src, 64);
    put_float(g_matrix + 0, get_float(mat + 0) * scale);   /* _11 */
    put_float(g_matrix + 20, get_float(mat + 20) * scale); /* _22 */
    api->guest_write_u32(api, cpu->esp + 4, g_matrix);
}

/* ---- HUD groups ---------------------------------------------------------- */

struct Centre {
    uint32_t object;
    float x, y;
};
static struct Centre g_left, g_right;

static void shift_group(uint32_t package, uint32_t hash, struct Centre *cache, float offset) {
    uint32_t object = 0;
    uint32_t args[3] = {package, hash, 0};
    if (g_api->guest_call(g_api, FIND_OBJECT, 0, args, 2, &object) != POP_OK || !object)
        return;
    if (cache->object != object) {
        uint32_t get[3] = {object, g_floats, g_floats + 4};
        if (g_api->guest_call(g_api, GET_CENTER, 0, get, 3, 0) != POP_OK)
            return;
        cache->object = object;
        cache->x = get_float(g_floats);
        cache->y = get_float(g_floats + 4);
    }
    uint32_t set[3] = {object, float_bits(cache->x + offset), float_bits(cache->y)};
    g_api->guest_call(g_api, SET_CENTER, 0, set, 3, 0);
}

static void shift_groups(uint32_t package) {
    if (!package || !g_floats || !g_api->guest_call)
        return;
    shift_group(package, 0x1603009Eu, &g_left, -g_hud_offset);
    shift_group(package, 0x5D0101F1u, &g_right, g_hud_offset);
}

/* The two package messages FEngHud sends when the layout changes. */
static void queue_package_message(const PopModApi *api, pop_cpu_v1 *cpu, PopHookInvocation *inv, void *user) {
    uint32_t ret = get_u32(cpu->esp);
    uint32_t package = get_u32(cpu->esp + 8);
    (void)inv;
    (void)user;
    api->call_original(api, cpu->target, cpu);
    if (ret == QPM_RET_A || ret == QPM_RET_B)
        shift_groups(package);
}

/* Minimap: its local position, five children and pivot, moved with the HUD. */
struct MinimapCache {
    uint32_t object;
    float local_x;
    float child_x[5];
};
static struct MinimapCache g_minimap;
static const uint32_t kChildOffsets[5] = {60, 64, 68, 72, 144};

static uint32_t child_data(uint32_t object, int i) {
    uint32_t child = get_u32(object + kChildOffsets[i]);
    return child ? get_u32(child + 44) : 0;
}

static void adjust_minimap(uint32_t object, int wide) {
    const float offset = wide ? -g_hud_offset : g_hud_offset;
    put_float(MINIMAP_PIVOT_X, wide ? -g_hud_offset : 0.0f);
    put_float(MINIMAP_DISP_X, wide ? -0.9375f : 0.9375f);
    if (g_minimap.object != object) {
        g_minimap.object = object;
        g_minimap.local_x = get_float(object + 184);
        for (int i = 0; i < 5; ++i) {
            uint32_t data = child_data(object, i);
            g_minimap.child_x[i] = data ? get_float(data + 28) : 0.0f;
        }
    }
    put_float(object + 184, g_minimap.local_x + offset);
    for (int i = 0; i < 5; ++i) {
        uint32_t data = child_data(object, i);
        if (data)
            put_float(data + 28, g_minimap.child_x[i] + offset);
    }
    uint32_t data = child_data(object, 4);
    if (data)
        g_api->guest_write_u32(g_api, data + 32, get_u32(object + 188));
}

static void minimap(const PopModApi *api, pop_cpu_v1 *cpu, PopHookInvocation *inv, void *user) {
    uint8_t wide = 0;
    (void)inv;
    (void)user;
    api->guest_read_u8(api, cpu->esp + 4, &wide);
    if (cpu->ecx)
        adjust_minimap(cpu->ecx, wide != 0);
    api->hook_return(api, cpu, cpu->eax, 4);
}

static void set_widescreen_mode(const PopModApi *api, pop_cpu_v1 *cpu, PopHookInvocation *inv, void *user) {
    uint32_t self = cpu->ecx;
    (void)inv;
    (void)user;
    if (g_hud_dirty && self) {
        uint8_t wide = 0;
        uint32_t package = get_u32(self + 36);
        uint32_t args[3] = {0, package, 0};
        api->guest_read_u8(api, self + 816, &wide);
        args[0] = wide ? 0x62ED04ECu : 0x53EC068Cu;
        if (api->guest_call(api, QUEUE_PACKAGE_MESSAGE, get_u32(FENG_INSTANCE), args, 3, 0) == POP_OK)
            shift_groups(package);
        uint32_t map = get_u32(self + 800);
        if (map)
            adjust_minimap(map, wide != 0);
        g_hud_dirty = 0;
    }
    api->call_original(api, cpu->target, cpu);
}

#include "vehicle_admission.h"
#include "frame_arena.h"
#include "sign_trace.h"     /* narrow draw trace for one scenery sign (scenery_trace) */
#include "post_fx.h"        /* the rebuilt post-process effects */

/* ---- lifecycle ---------------------------------------------------------- */

/* Every hook owns one g_hooks slot. A slot outside the table or already holding a live
 * hook is refused (and logged) rather than overwritten, so a new hook can never corrupt a
 * neighbour or lose the handle exit() needs to remove it. */
static int hook_slot_free(uint32_t addr, int slot) {
    if (slot >= 0 && (unsigned)slot < sizeof g_hooks / sizeof g_hooks[0] && !g_hooks[slot])
        return 1;
    char line[128];
    snprintf(line, sizeof line, "core.nfsmw: hook %08x slot %d out of range or in use", addr, slot);
    g_api->log(g_api, line);
    return 0;
}
static PopModStatus install(uint32_t addr, PopHookFn fn, int32_t mode, int slot) {
    if (!hook_slot_free(addr, slot))
        return POP_E_RANGE;
    PopModStatus result = g_api->hook_install(g_api, addr, fn, mode, 0, &g_hooks[slot]);
    if (result != POP_OK) {
        char line[128];
        snprintf(line, sizeof line, "core.nfsmw: hook %08x failed (%d)", addr, result);
        g_api->log(g_api, line);
    }
    return result;
}
static PopModStatus install_at(uint32_t addr, uint32_t ret, PopHookFn fn, int32_t mode, int slot) {
    if (!g_api->hook_install_at_callsite)
        return POP_E_STATE;
    if (!hook_slot_free(addr, slot))
        return POP_E_RANGE;
    return g_api->hook_install_at_callsite(g_api, addr, ret, fn, mode, 0, &g_hooks[slot]);
}
#include "stream_timing.h"   /* TEST ONLY (Test115); needs hook_slot_free/g_hooks above */
#include "audio_env_probe.h" /* TEST ONLY (Test118); likewise */
#include "scenery_lod_probe.h" /* TEST ONLY (Test127); likewise */
#include "mirror_flare_probe.h" /* TEST ONLY (Test131); likewise */
#include "xenon_fx.h"        /* sparks and light trails (XenonEffects); needs install/install_at above */
#include "mirror_car_shadows.h" /* car shadows in the mirror (Test134) */
#include "cop_numbers.h"       /* police numbers per heat level (Test161) */
#include "eo_extra.h"          /* more Extra Options: unlock all, barriers, LOD */
#include "mirror_props.h"       /* knocked-over props in the mirror (Test137) */
#include "world_lights.h"       /* light pools from the PC lamps (clean_pools); needs install/install_at above */

/* The hooked Extra Options. Each is optional: a hook that cannot be
 * installed logs, and its option's slots go back to the original values so
 * the game never runs half an option. */
static void extra_options_hooks(const PopModApi *api) {
    if (g_exo_special_vinyls &&
        (install(0x007bd060u, exo_vinyl_parts, POP_HOOK_REPLACE, 41) != POP_OK ||
         install_at(0x007bb560u, 0x007bc8ffu, exo_vinyl_groups, POP_HOOK_REPLACE, 42) != POP_OK)) {
        api->log(api, "core.nfsmw: ShowSpecialVinyls unavailable (hook); category left original");
        g_exo_special_vinyls = 0;
        api->guest_write_u32(api, EXO_VINYL_MAX, 0x409u);
    }
    if ((g_exo_starting_cash || setting("ug2_save_bonus", 1)) &&
        install(0x0056d7c0u, exo_starting_cash, POP_HOOK_REPLACE, 43) != POP_OK) {
        api->log(api, "core.nfsmw: StartingCash unavailable (hook)");
        g_exo_starting_cash = 0;
    }
    if (g_exo_replay_blacklist &&
        (install(0x00624360u, exo_rival_screen, POP_HOOK_BEFORE, 44) != POP_OK ||
         install_at(0x00573020u, 0x006243d8u, exo_rival_keep, POP_HOOK_BEFORE, 45) != POP_OK ||
         install_at(0x005dcea0u, 0x0060076bu, exo_rival_shown, POP_HOOK_REPLACE, 46) != POP_OK)) {
        api->log(api, "core.nfsmw: ReplayBlacklistRaces unavailable (hook); rival races left original");
        g_exo_replay_blacklist = 0;
        exo_replay_slots(api, 0);
    }
    if (getenv("NFSMW_TEST_WHEEL_PROBE") && install(0x007422d0u, wheel_probe, POP_HOOK_REPLACE, 75) == POP_OK)
        g_wheel_probe = 1;   /* TEST ONLY */
    g_heli_enabled = setting("helicopter_bounty", 1) != 0;
    if (g_heli_enabled &&
        (install(0x00418f30u, heli_bounty_enter, POP_HOOK_BEFORE, 76) != POP_OK ||
         install(0x00418f30u, heli_bounty_leave, POP_HOOK_AFTER, 77) != POP_OK ||
         install(0x00595af0u, heli_announce_enter, POP_HOOK_BEFORE, 78) != POP_OK ||
         install(0x00595af0u, heli_announce_leave, POP_HOOK_AFTER, 79) != POP_OK)) {
        api->log(api, "core.nfsmw: helicopter bounty fix unavailable (hook)");
        g_heli_enabled = 0;
    }
    char line[200];
    snprintf(line, sizeof line,
             "core.nfsmw: Extra Options hooks: special vinyls %s, starting cash %u, replay blacklist %s; "
             "helicopter takedown %s, Underground 2 save bonus %s",
             g_exo_special_vinyls ? "on" : "off", (unsigned)g_exo_starting_cash,
             g_exo_replay_blacklist ? "on" : "off", g_heli_enabled ? "on" : "off",
             setting("ug2_save_bonus", 1) ? "on" : "off");
    api->log(api, line);
}

PopModStatus pop_mod_init(const PopModApi *api) {
    PopModStatus s;
    g_api = api;
    if ((s = va_install(api)) != POP_OK) return s;
    if ((s = fa_install(api)) != POP_OK) return s;
    native_options_load(api);
    extra_options_apply(api);
    extra_options_hooks(api);
    heat_levels_apply(api);   /* Test88 */
    if (install_at(0x00402060u, 0x00443ddeu, heat_levels_event, POP_HOOK_REPLACE, 87) != POP_OK)
        g_api->log(g_api, "core.nfsmw: Heat Level Override for events unavailable (hook 00402060 from 00443dd9)");
    cop_numbers_install();   /* Test161: always installed; the live settings decide */
    g_audio_active = setting("surround_audio", 0) != 0;
    /* Always installed: both consult the live setting, so a later menu/F10
     * change reaches SND at its next own restart (load transition). */
    if ((s = install(0x0081b4acu, native_audio_mode, POP_HOOK_BEFORE, 30)) != POP_OK ||
        (s = install(0x0081b7aeu, native_audio_init, POP_HOOK_BEFORE, 31)) != POP_OK ||
        (s = install(0x0081ef6fu, native_audio_configured, POP_HOOK_BEFORE, 40)) != POP_OK)
        return s;
    if ((s = api->guest_alloc(api, 128, &g_audio_text)) != POP_OK)
        return s;
    if ((s = install(0x00529310u, native_audio_setup, POP_HOOK_REPLACE, 32)) != POP_OK ||
        (s = install(0x0050f600u, native_audio_act, POP_HOOK_REPLACE, 33)) != POP_OK ||
        (s = install(0x0051b2d0u, native_audio_draw, POP_HOOK_REPLACE, 34)) != POP_OK ||
        (s = install(0x00510360u, native_audio_unchanged, POP_HOOK_REPLACE, 35)) != POP_OK ||
        (s = install(0x00510450u, native_audio_restore, POP_HOOK_REPLACE, 36)) != POP_OK ||
        (s = install(0x0051d170u, native_audio_defaults, POP_HOOK_REPLACE, 37)) != POP_OK ||
        (s = install(0x00545750u, native_audio_notification, POP_HOOK_REPLACE, 38)) != POP_OK ||
        (s = install(0x00529280u, native_audio_destroy, POP_HOOK_BEFORE, 39)) != POP_OK)
        return s;
    int64_t rate = setting("sim_rate", 60);
    if (rate < 30)
        rate = 30;
    if (rate > 480)
        rate = 480;
    g_frame_time_bits = float_bits(1.0f / (float)rate);
    g_widescreen = (int)setting("widescreen", 1);
    {
        char line[128];
        snprintf(line, sizeof line, "core.nfsmw: simulation rate %d Hz, widescreen %s", (int)rate,
                 g_widescreen ? "on" : "off");
        api->log(api, line);
    }
    if ((s = api->guest_write_u32(api, REDIRECTED_FRAME_TIME, g_frame_time_bits)) != POP_OK)
        return s;
    if ((s = api->guest_write_u32(api, WORLD_TIMESTEP, g_frame_time_bits)) != POP_OK)
        return s;
    if ((s = install(TIMER_CTOR, timer_frame_time, POP_HOOK_BEFORE, 0)) != POP_OK)
        return s;
    if ((s = install(GAME_DEVICE_UPDATE, game_device_update, POP_HOOK_REPLACE, 13)) != POP_OK)
        return s;
    pad_mapping_install();
    if ((s = install(0x006dc800u, impact_feedback, POP_HOOK_REPLACE, 29)) != POP_OK)
        return s;
    /* Road-surface feedback is optional: without it only the impact bridge runs. */
    if (install(0x006dcda0u, road_feedback, POP_HOOK_BEFORE, 47) != POP_OK)
        g_api->log(g_api, "core.nfsmw: road-surface rumble unavailable (hook 006dcda0)");
    install_at(0x006dcda0u, 0x006434c5u, road_surface_trace, POP_HOOK_BEFORE, 48);
    if ((s = install(0x007a2310u, showcase_pad_zoom, POP_HOOK_REPLACE, 25)) != POP_OK)
        return s;
    if ((s = install(GET_RESOLUTION_A, resolution, POP_HOOK_REPLACE, 1)) != POP_OK)
        return s;
    /* The unused twin getter has no translated entry in this pinned build.
     * Its optional hook always failed; leave slot 2 empty. */
    build_resolution_list();
    if ((s = install_at(0x00591460u, 0x0059a044u, movie_fit, POP_HOOK_BEFORE, 27)) != POP_OK)
        return s;
    if ((s = install(LOAD_SETTINGS, load_settings, POP_HOOK_REPLACE, 10)) != POP_OK)
        return s;
    if ((s = install(SAVE_SETTINGS, save_settings, POP_HOOK_BEFORE, 11)) != POP_OK)
        return s;
    if ((s = install(APPLY_VIDEO_PRESET, video_preset, POP_HOOK_REPLACE, 26)) != POP_OK)
        return s;
    if ((s = install(LOCALIZED_WIDE, localized_wide, POP_HOOK_REPLACE, 12)) != POP_OK)
        return s;
    if ((s = install_at(DRAW_FRONTEND_QUAD, MIRROR_QUAD_RETURN, mirror_exposure,
                        POP_HOOK_BEFORE, 7)) != POP_OK)
        return s;
    if ((s = install(CREATE_MIRROR_RESOURCES, mirror_resources, POP_HOOK_BEFORE, 14)) != POP_OK)
        return s;
    if ((s = install_at(FLUSH_RENDER_BATCHES, BEFORE_MIRROR_RETURN, mirror_shadows,
                        POP_HOOK_REPLACE, 15)) != POP_OK)
        return s;
    if (install(0x00595d40u, mirror_look_back, POP_HOOK_REPLACE, 65) != POP_OK)
        g_api->log(g_api, "core.nfsmw: mirror in Look Back unavailable (hook 00595d40)");
    if ((s = install(0x004fae40u, mirror_scenery, POP_HOOK_REPLACE, 19)) != POP_OK)
        return s;
    if (install_at(0x00505e80u, 0x006de9f5u, mirror_lights, POP_HOOK_AFTER, 80) != POP_OK)
        g_api->log(g_api, "core.nfsmw: mirror vehicle lights unavailable (hook 00505e80 at 006de9f0)");
    if (install_at(0x007422d0u, 0x0074e7ffu, mirror_car_shadow, POP_HOOK_AFTER, 97) != POP_OK)
        g_api->log(g_api, "core.nfsmw: car shadows in the mirror unavailable (hook 007422d0 at 0074e7fa)");
    if (install_at(0x00750b10u, 0x006de990u, mirror_props, POP_HOOK_AFTER, 2) != POP_OK)
        g_api->log(g_api, "core.nfsmw: props in the mirror unavailable (hook 00750b10 at 006de98b)");
    if (getenv("NFSMW_TEST_MIRROR_LIGHT_LOG"))   /* TEST ONLY */
        install(0x00505380u, mirror_flare_count, POP_HOOK_BEFORE, 81);
    frontend_pad_init(api);
    if ((s = install(0x00516230u, frontend_pad_loaded, POP_HOOK_REPLACE, 20)) != POP_OK ||
        (s = install(0x005b85f0u, frontend_pad_tick, POP_HOOK_BEFORE, 21)) != POP_OK ||
        (s = install(0x00514cc0u, frontend_pad_visible, POP_HOOK_REPLACE, 22)) != POP_OK ||
        (s = install(0x00514c70u, frontend_pad_invisible, POP_HOOK_BEFORE, 23)) != POP_OK ||
        (s = install(0x005a45b0u, frontend_pad_render, POP_HOOK_REPLACE, 24)) != POP_OK)
        return s;
    int texture_map_ready = texture_pack_init(api);
    if (texture_map_ready || g_pad_icons_enabled) {
        if ((s = install_at(0x00664780u, 0x006660c5u, texture_pack_load,
                            POP_HOOK_REPLACE, 16)) != POP_OK)
            return s;
        if (texture_map_ready && (s = install(0x00503400u, texture_pack_find, POP_HOOK_REPLACE, 17)) != POP_OK)
            return s;
    }
    wl_install(api);   /* light pools (clean_pools); logs and stays off without its files */
    eo_extra_init(api);
    eo_keys_init(api);
    eo_skip_track_init(api);
    {
        static const char *const names[] = {"Definitive", "Definitive", "PC - Original"};
        if (g_look_boot < 0) g_look = g_look_boot = look_read();
        char line[120];
        snprintf(line, sizeof line, "core.nfsmw: appearance at launch: %s", names[g_look_boot]);
        api->log(api, line);
        {   /* Playbook v1.0 (§2.2J, §7A.4): settings that change diagnostics or timing away from their defaults are
             * named once at launch, so a profile copied with them on is visible in its first lines. */
            const int64_t trace = setting("input_trace", 0), rate = setting("sim_rate", 60);
            char note[200];
            if (trace || rate != 60) {
                char rate_text[48] = "";
                if (rate != 60) snprintf(rate_text, sizeof rate_text, "%s sim_rate %lld", trace ? ";" : "", (long long)rate);
                snprintf(note, sizeof note, "core.nfsmw: NOTE non-default diagnostics/timing:%s%s",
                         trace ? " input_trace ON (about 11 MB of log per hour)" : "", rate_text);
            } else {
                snprintf(note, sizeof note, "core.nfsmw: diagnostics/timing at defaults (input_trace off, sim_rate 60)");
            }
            api->log(api, note);
            if (setting("scenery_trace", 0) || setting("position_log", 0)) {   /* Test136/139 owner diagnostics */
                snprintf(note, sizeof note, "core.nfsmw: NOTE diagnostics on:%s%s",
                         setting("scenery_trace", 0) ? " scenery_trace (restart; costs some speed)" : "",
                         setting("position_log", 0) ? " position_log" : "");
                api->log(api, note);
            }
        }
        if (getenv("NFSMW_TEST_LOOK_KEY") && api->on_key) api->on_key(api, look_test_key, NULL, &g_look_key_hook);
    }
    stream_timing_init(api);   /* TEST ONLY (Test115): absent unless NFSMW_TEST_STREAM_TIMING is set */
    audio_env_probe_init(api); /* TEST ONLY (Test118): absent unless NFSMW_TEST_AUDIO_ENV is set */
    scenery_lod_probe_init(api); /* Test127 probe: NFSMW_TEST_SCENERY_LOD, or the player's switch scenery_trace (Test139) */
    mirror_flare_probe_init(api); /* TEST ONLY (Test131): absent unless NFSMW_TEST_MIRROR_FLARES=1 */
    xfx_init(api);             /* sparks and light trails (XenonEffects) */
    /* The rebuilt effects: before the frame's HUD/front-end call 006c3870 at 006e75a1, which follows the main
     * view's render 006de300. */
    if (pfx_init(api) && install_at(0x006c3870u, 0x006e75a6u, pfx_frame, POP_HOOK_BEFORE, 71) != POP_OK) {
        api->log(api, "core.nfsmw: effects unavailable (hook)");
        g_pfx_enabled = 0;
    }
    /* ...and the main view's colour pass: its depth surface and the alpha the HDR scene mark relies on. */
    if (g_pfx_enabled && install_at(0x006de210u, 0x006ded21u, pfx_view_colour, POP_HOOK_BEFORE, 72) != POP_OK)
        api->log(api, "core.nfsmw: effects: colour-pass hook unavailable; depth of field without scene depth");
    if (!g_widescreen)
        return POP_OK;

    put_float(SLOT_SHADOW_DISTANCE, 10.0f);
    {
        /* The widescreen splash screen's name replaces the 4:3 one. */
        void *wide, *narrow;
        if (api->guest_ptr(api, SPLASH_WIDE_NAME, 20, &wide) == POP_OK &&
            api->guest_ptr(api, SPLASH_NAME, 20, &narrow) == POP_OK)
            memcpy(narrow, wide, strlen((const char *)wide) + 1);
    }
    if (api->guest_alloc(api, 64, &g_matrix) != POP_OK)
        g_matrix = 0;
    if (api->guest_alloc(api, 8, &g_floats) != POP_OK)
        g_floats = 0;
    {
        uint32_t w, h;
        choose_resolution(&w, &h);
        apply_resolution(w, h);
    }
    if ((s = install(VIEW_PROJECTION, view_projection, POP_HOOK_BEFORE, 3)) != POP_OK)
        return s;
    if ((s = install(SET_WIDESCREEN_MODE, set_widescreen_mode, POP_HOOK_REPLACE, 4)) != POP_OK)
        return s;
    if ((s = install(MINIMAP_ADJUST, minimap, POP_HOOK_REPLACE, 5)) != POP_OK)
        return s;
    /* One replacement per function: the two FEngHud call sites are told
     * apart by their return address inside it. */
    if ((s = install(QUEUE_PACKAGE_MESSAGE, queue_package_message, POP_HOOK_REPLACE, 6)) != POP_OK)
        return s;
    if ((s = install_at(SET_TRANSFORM, SET_TRANSFORM_RET_A, set_transform, POP_HOOK_BEFORE, 8)) != POP_OK)
        return s;
    return install_at(SET_TRANSFORM, SET_TRANSFORM_RET_B, set_transform, POP_HOOK_BEFORE, 9);
}

PopModStatus pop_mod_exit(void) {
    stream_timing_exit(g_api);
    audio_env_probe_exit(g_api);
    scenery_lod_probe_exit(g_api);
    mirror_flare_probe_exit(g_api);
    mirror_car_shadows_exit(g_api);
    mirror_props_exit(g_api);
    xfx_exit(g_api);
    wl_exit(g_api);
    eo_keys_exit(g_api);
    pfx_exit(g_api);
    if (g_look_key_hook) { g_api->hook_remove(g_api, g_look_key_hook); g_look_key_hook = 0; }
    fa_stop(g_api);
    va_stop(g_api);
    impact_feedback_stop(g_api);
    frontend_pad_exit();
    native_audio_exit(g_api);
    for (unsigned i = 0; i < sizeof g_hooks / sizeof g_hooks[0]; ++i)
        if (g_hooks[i]) {
            g_api->hook_remove(g_api, g_hooks[i]);
            g_hooks[i] = 0;
        }
    return POP_OK;
}
