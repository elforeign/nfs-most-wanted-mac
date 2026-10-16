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
static uint32_t g_hooks[160];   /* Remaster (5 Oct): 128 -> 160 for Extra Options batch 3c (slots 128-139) */ //   /* 90..95: Xbox particles (xenon_fx.h); 100..127 free (enlarged 3 Oct, Test145) */
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
/* Appearance (Test77; reshaped 2 Oct 2026, owner): 0 Definitive (default; the former Hybrid), 3 Definitive - Custom.
 * Slots 1 and 4 (the former Xbox 360 looks) and 2 (the former PC - Original) are retired: appearance_recipe.h moves
 * such a profile to Definitive once and logs it. The Xbox 360 colour filter is its own switch (`xbox_colour_filter`). */
enum { LOOK_HYBRID = 0 };
/* Run 13 (owner 8 Oct) to run 29: the master switch `look` (0 Definitive, 1 Xbox 360, 2 PC) made the Remaster features
 * read their console values through a follow table. Run 29 retired the PC look (D-163); run 30 retired the Xbox 360
 * look (owner 12 Oct, D-170): Definitive is the only look, the key is hidden, and settings_migrate() moves a profile on
 * Xbox 360 to Definitive once (logged). The features with their own Xbox 360 modes (wet roads, car reflections, glass,
 * water, rain drops) keep them as their own choices; the golden colour filter is its own Off/On switch (golden_grade). */
/* Retired settings (owner 10 Oct, D-117, under the defaults rule D-115): grass, dof_scene_depth, x360_exposure,
 * upscale_temporal and spray_fill are hidden and their code runs at the value that works (grass and spray off, the
 * scene-depth DoF and the exposure stage on, no temporal upscaling); run 29 (D-167) removed the reads, so a saved value
 * changes nothing (review/r2close/defaults-review.md). */
static int64_t setting(const char *key, int64_t fallback);
/* Run 13 (owner 8 Oct: "all modes should have a toggle ... leave it up to the user"): the golden colour filter, drawn
 * by the clean-room grade (G2). Run 30 (D-170): a plain Off/On switch, `golden_grade` (default Off); the old
 * `golden_filter` (Auto/On/Off, Auto = on in the Xbox 360 look) is hidden and read once by settings_migrate(). */
static int golden_on(void) { return setting("golden_grade", 0) != 0; }
/* R2 close perf pass (review/r2close/perf-design.md 3.2), TEST ONLY: NFSMW_TEST_SETTING_AB=<key>=<off>[,<key>=<off>...]:<s>.
 * In odd periods of CLOCK_UPTIME_RAW (the frames.csv sealed_s clock, as harness/tools/ab_stats.py assigns frames) the
 * listed keys answer their off value; in even periods the saved value. Parsed once; one log line per phase change.
 * Only keys read through setting() while running are covered ("live" keys); restart keys and keys the kit reads itself
 * (hdr_output) are not. Unset = no effect. */
static struct { char key[40]; int64_t off; } g_sab[16];
static atomic_int g_sab_n = -1, g_sab_phase = -1;
static uint64_t g_sab_period_ns;
static void sab_parse(void) {
    const char *e = getenv("NFSMW_TEST_SETTING_AB");
    int n = 0;
    const char *colon = e ? strrchr(e, ':') : NULL;
    const double secs = colon ? atof(colon + 1) : 0.0;
    if (e && colon && secs > 0.0) {
        g_sab_period_ns = (uint64_t)(secs * 1e9);
        for (const char *p = e; p < colon && n < 16;) {
            const char *comma = memchr(p, ',', (size_t)(colon - p)), *end = comma ? comma : colon;
            const char *eq = memchr(p, '=', (size_t)(end - p));
            if (eq && eq > p && (size_t)(eq - p) < sizeof g_sab[0].key) {
                memcpy(g_sab[n].key, p, (size_t)(eq - p));
                g_sab[n].key[eq - p] = 0;
                g_sab[n].off = strtoll(eq + 1, NULL, 10);
                ++n;
            }
            p = end + 1;
        }
    }
    if (n) {
        char line[256];
        snprintf(line, sizeof line, "core.nfsmw: [remaster] TEST setting A/B: %d keys, period %.1f s (%s)", n, secs, e);
        g_api->log(g_api, line);
    }
    atomic_store(&g_sab_n, n);
}
static void sab_apply(const char *key, int64_t *v) {
    if (atomic_load(&g_sab_n) < 0) sab_parse();
    if (atomic_load(&g_sab_n) <= 0) return;
    const uint64_t now = clock_gettime_nsec_np(CLOCK_UPTIME_RAW);
    const int phase = (int)((now / g_sab_period_ns) & 1u);
    if (atomic_exchange(&g_sab_phase, phase) != phase) {
        char line[200];
        snprintf(line, sizeof line, "core.nfsmw: [remaster] TEST setting A/B period %llu: keys %s (uptime raw %llu ns)",
                 (unsigned long long)(now / g_sab_period_ns), phase ? "off" : "on", (unsigned long long)now);
        g_api->log(g_api, line);
    }
    if (!phase) return;
    for (int i = 0; i < atomic_load(&g_sab_n); ++i)
        if (!strcmp(g_sab[i].key, key)) { *v = g_sab[i].off; return; }
}
static int tier_follow(const char *key, int64_t *v);   /* mac_tier.h */
static int64_t setting(const char *key, int64_t fallback) {
    int64_t v = fallback;
    g_api->settings_get(g_api, key, &v);
    tier_follow(key, &v);   /* run 29 weaker-Mac presets: read-time caps */
    sab_apply(key, &v);
    return v;
}
#include "mac_tier.h"   /* run 29 weaker-Mac presets (needs setting() above) */

#include "x360_sky.h"
#include "impact_feedback.h"
#include "boot_movies.h"
#include "frontend_pad.h"
#include "x360_textures.h"
#include "native_options.h"
#include "native_audio.h"
#include "heli_bounty.h"

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
static void appearance_recipe(const PopModApi *api);
static void cop_numbers_poll(const PopModApi *api); /* cop_numbers.h */

#include "position_log.h"   /* Test136; needs setting/get_u32/get_float above */
#include "preculler_switch.h" /* Test149: optional preculler off (experimental) */
#include "memory_log.h"       /* Test151: game footprint + Mac swap once a minute (crash evidence) */
#include "discord_status.h"   /* run 34 (D-181, PR 9): Discord status, opt-in; its worker starts when switched on */
static void lamp_daylight_tick(const PopModApi *api);
static void vehicle_lights_tick(const PopModApi *api);  /* vehicle_lights.h */    /* lamp_daylight.h */
/* Owner 7 Oct: the keyboard T (the game's row 14, FE_ACTION_B2) did not skip the music track in free roam; the pad
 * action loop 0x00559400 skips it with a thiscall 0x00517070 on [0x0091cae0] (no arguments). The key callback only
 * flags a T press (not consumed: menus keep their typing); the next device update in the world (flow 6) skips. */
static uint32_t g_tskip_hook;
static volatile int g_tskip_pending, g_tskip_down;
static int32_t tskip_key(const PopModApi *api, int32_t dik, int32_t vk, int32_t down, void *user) {
    (void)api; (void)vk; (void)user;
    if (dik != 0x14) return 0;   /* DIK_T */
    if (down && !g_tskip_down) g_tskip_pending = 1;
    g_tskip_down = down != 0;
    return 0;
}
/* Run 27 (owner: L3 does not skip; review/run27/l3-cursor-findings.md): the pad's L3 is bound to the same row 14,
 * which only the front-end action loop reads, so in the world it never skipped either. The pad device's row values
 * ([device+32], as input_trace reads them) give the press; a rising edge flags the same skip. 0x517070 checks the game
 * flow itself (the Extra Options hooks fake it in the front-end path): the skip is called with the flow read as 3 for
 * the call and restored, so it does not depend on those hooks firing inside a guest_call. */
static void tskip_poll(const PopModApi *api) {
    if (!g_tskip_pending) return;
    g_tskip_pending = 0;
    const uint32_t flow = get_u32(0x00925e90u);
    if (flow != 6u) return;
    const uint32_t music = get_u32(0x0091cae0u);
    if (!music) return;
    api->guest_write_u32(api, 0x00925e90u, 3u);
    api->guest_call(api, 0x00517070u, music, NULL, 0, NULL);
    api->guest_write_u32(api, 0x00925e90u, flow);
    static unsigned n;
    if (++n <= 8) api->log(api, "core.nfsmw: music track skipped in game (T or L3)");
}
static void l3_skip_poll(uint32_t device) {   /* after the device update: the pad's row 14 (L3 by default) */
    static int down;
    const uint32_t values = get_u32(device + 32);
    if (!values || !setting("l3_skip_track", 1)) return;
    const int now = get_float(values + 14 * 4) > 0.5f;
    if (now && !down) g_tskip_pending = 1;
    down = now;
}
static void game_device_update(const PopModApi *api, pop_cpu_v1 *cpu,
                               PopHookInvocation *inv, void *user) {
    (void)inv;
    (void)user;
    uint32_t device = cpu->ecx;
    native_audio_present(api); /* Audio Mode presentation; host call only on change */
    tskip_poll(api);           /* keyboard T skips the music track in free roam (owner 7 Oct) */
    lamp_daylight_tick(api);
    vehicle_lights_tick(api);  /* car lights in the clean pools (setting vehicle_lights; off: nothing) */   /* outdoor lamps by daylight (setting lamp_daylight; off: activation 1) */
    quality_apply(api, 1); /* a chosen Quality preset, then the label from the actual values (Test84) */
    msaa_apply(api, 1); /* a settings-menu change of anti-aliasing; nothing when unchanged */
    heat_levels_apply(api); /* Heat Level Override slot (Test88); logs only on a change */
    cop_numbers_poll(api);  /* TEST ONLY police-car count (NFSMW_TEST_COP_LOG) */
    position_log(api);  /* diagnostics: time + world position every 2 s while driving (Test136) */
    preculler_switch(api); /* Test149: only acts when the switch is off, or to restore */
    memory_log(api);       /* Test151: one line a minute */
    discord_status_tick(api); /* menus or driving, for the Discord worker; no socket work here */
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
    if (get_u32(device + 36) == 0) l3_skip_poll(device);   /* run 27: L3 skips the track in the world too */
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

#include "resolution.h"

#include "quality_presets.h"
#include "appearance_recipe.h"

#include "video_modes.h"

#include "view_hud.h"

#include "x360_scenery_lights.h"
#include "x360_light_pools.h"
#include "clean_pools.h"      /* Test113 module F: clean-room light pools (owner setting clean_pools) */
#include "lamp_daylight.h"     /* R2 run 9: outdoor lamps fade out in daylight (setting lamp_daylight, off) */

#include "world_light_pass.h"

#include "post_fx.h"     /* the post host for the rebuilt effects (ported from the base public build, R2 N3, 7 Oct 2026) */
#include "upscale.h"     /* run 13: MetalFX spatial upscaling (setting upscale; Off installs nothing) */
#include "atmosphere.h"  /* run 14 B1: height fog and haze (setting atmosphere; Off installs nothing) */

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
#include "scenery_lod_probe.h" /* TEST ONLY (Test127); likewise */
#include "scenery_fade.h"     /* run 27 scenery fade-in (setting scenery_fade, D-153); needs pfx_call and g_hooks above */
#include "xenon_fx.h"        /* Xbox 360 particles (task 145); needs install/install_at above */
#include "smoke.h"           /* denser tyre smoke (run 14); likewise */
#include "smoke_light.h"     /* lit smoke (run 14 stage 1); needs install and post_fx.h's pfx_call above */
#include "mirror_car_shadows.h" /* car shadows in the mirror (Test134) */
#include "fe_shadows.h"        /* front-end shadows (R2 item 6); likewise */
#include "cop_numbers.h"       /* police numbers per heat level (Test161) */
#include "remaster_tex.h"      /* Remaster A1 step 1: texture keys (TEST, NFSMW_REMASTER_TEXTAG=1) */
#include "x360_frontend.h"   /* 2.0.4: the Stuff Pack front end (overlay); ported run 29 */
#include "datamods.h"        /* run 29 modding stage 1: the DataMods folder (overlay) */
#include "snd_stall.h"       /* run 32 issue 8: no DirectSound restart when the output starts slowly */
#include "snd_bank_log.h"    /* 3.0.4 issue 8: where the game puts its sound banks (diagnostics) */
#include "eo_extra.h"          /* Remaster EO batch 1: unlock all, barriers, LOD (owner, 5 Oct) */
#include "mirror_props.h"       /* knocked-over props in the mirror (Test137) */

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

/* Run 15 (owner's run-14 play test: a saved x360_sky 1 kept the community sky after the default moved to 2): one-time
 * moves of saved values that only meant "the default when it was saved". `settings_version` (hidden) records the last
 * migration done. 1: x360_sky 1 (the old default) -> 2 (ours). 2: run 30, the Xbox 360 look retired (below).
 * Run 16 (review: "sparse saving should preserve intent"): the kit keeps the player's explicit choices even when they
 * equal a default, and a key the player never set follows the default, so a default change needs no migration.
 * A migration writes through settings_set, which is a derived write (kit/mods/settings.cpp): writing the declared
 * default returns the key to "follows the default" (the sky above), any other value is held; a migration never
 * manufactures a choice of the default. Add a step here only to move a value whose meaning changed. */
static void settings_migrate(const PopModApi *api) {
    int64_t version = 0, sky = 2;
    api->settings_get(api, "settings_version", &version);
    if (version < 1) {
        api->settings_get(api, "x360_sky", &sky);
        if (sky == 1) {
            api->settings_set(api, "x360_sky", 2);
            api->log(api, "core.nfsmw: settings: Xbox 360 sky moved from the community shader to ours (run-14 default)");
        }
    }
    /* 2: run 30 (owner 12 Oct, D-170): the Xbox 360 look is retired. The golden filter becomes golden_grade Off/On:
     * on when the old golden_filter said On (1), or Auto (0) in the Xbox 360 look, which showed it; a profile on Look
     * Xbox 360 moves to Definitive (its features' own Xbox 360 modes are kept as saved). */
    if (version < 2) {
        int64_t look = 0, golden = 0;
        api->settings_get(api, "look", &look);
        api->settings_get(api, "golden_filter", &golden);
        if (golden == 1 || (golden == 0 && look == 1)) api->settings_set(api, "golden_grade", 1);
        if (look == 1) {
            api->settings_set(api, "look", 0);
            api->log(api, golden == 2 ? "core.nfsmw: settings: Look Xbox 360 moved to Definitive (run 30, D-170: one look)"
                                      : "core.nfsmw: settings: Look Xbox 360 moved to Definitive (run 30, D-170: one look); "
                                        "the golden colour filter stays on");
        }
        api->settings_set(api, "settings_version", 2);
    }
}

PopModStatus pop_mod_init(const PopModApi *api) {
    PopModStatus s;
    g_api = api;
    settings_migrate(api);
    appearance_recipe(api);   /* run 29: once at start (the key is hidden: nothing changes it while the game runs) */
    mac_tier_init(api);       /* run 29 weaker-Mac presets: before any init reads a key a tier caps */
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
    /* USA Xbox 360 vehicle rumble on the PC IFeedback stubs (x360_feedback.h). Optional:
     * if any hook is refused, all are removed and rumble_model 0 behaviour remains. */
    {
        static const struct { uint32_t addr; PopHookFn fn; } x360_hooks[] = {
            {0x00628800u, x360_pause}, {0x00628870u, x360_resume}, {0x00628880u, x360_reset},
            {0x00628890u, x360_begin}, {0x006288b0u, x360_road}, {0x006288c0u, x360_skid},
            {0x006288d0u, x360_slip}, {0x006288f0u, x360_shift_potential}, {0x00628900u, x360_nos},
            {0x00628910u, x360_blown}, {0x00628920u, x360_shifting},
        };
        static const struct { uint32_t addr; PopHookFn fn; int32_t mode; } x360_driver_hooks[] = {
            {0x006ee400u, x360_gate, POP_HOOK_AFTER}, {0x006ee490u, x360_driver, POP_HOOK_REPLACE},
        };
        unsigned n = 0;
        for (; n < sizeof x360_hooks / sizeof x360_hooks[0]; ++n)
            if (install(x360_hooks[n].addr, x360_hooks[n].fn, POP_HOOK_BEFORE, 51 + (int)n) != POP_OK) break;
        g_x360_hooked = n == sizeof x360_hooks / sizeof x360_hooks[0];
        unsigned m = 0;
        if (g_x360_hooked)
            for (; m < sizeof x360_driver_hooks / sizeof x360_driver_hooks[0]; ++m)
                if (install(x360_driver_hooks[m].addr, x360_driver_hooks[m].fn, x360_driver_hooks[m].mode,
                            62 + (int)m) != POP_OK) {
                    g_x360_hooked = 0;
                    break;
                }
        if (!g_x360_hooked) {
            for (unsigned i = 0; i < n; ++i) {
                g_api->hook_remove(g_api, g_hooks[51 + i]);
                g_hooks[51 + i] = 0;
            }
            for (unsigned i = 0; i < m; ++i) {
                g_api->hook_remove(g_api, g_hooks[62 + i]);
                g_hooks[62 + i] = 0;
            }
            char line[112];
            snprintf(line, sizeof line, "core.nfsmw: Xbox 360 rumble model unavailable (hook %08x refused)",
                     n < sizeof x360_hooks / sizeof x360_hooks[0] ? x360_hooks[n].addr : x360_driver_hooks[m].addr);
            g_api->log(g_api, line);
        }
    }
    /* Test61: IFeedback::ReportCollision (00628930), the 360 collision rumble. Optional. */
    if (g_x360_hooked) {
        if (g_api->guest_alloc(g_api, 16, &g_x360_scratch) == POP_OK &&
            install(0x00628930u, x360_collision, POP_HOOK_BEFORE, 64) == POP_OK)
            g_x360_collision_hooked = 1;
        else
            g_api->log(g_api, "core.nfsmw: Xbox 360 collision rumble unavailable (hook 00628930)");
    }
    /* Boot movies: optional; a refused hook leaves the PC order and logs. */
    if (install(BOOT_FLOW_CTOR, boot_flow_ctor, POP_HOOK_BEFORE, 49) == POP_OK)
        install(BOOT_OVERLAY_ALLOWED, boot_overlay_allowed, POP_HOOK_REPLACE, 50);
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
    if (install(0x005a3030u, splash_ctor, POP_HOOK_REPLACE, 66) != POP_OK ||
        install_at(0x00516c90u, 0x005a30a2u, splash_ws_message, POP_HOOK_BEFORE, 67) != POP_OK)
        g_api->log(g_api, "core.nfsmw: Xbox 360 splash screen unavailable (hooks 005a3030/00516c90)");
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
    x360_sky_init(api);
    int texture_map_ready = x360_texture_map_init(api);
    if (texture_map_ready || g_pad_icons_enabled) {
        if ((s = install_at(0x00664780u, 0x006660c5u, x360_load_textures,
                            POP_HOOK_REPLACE, 16)) != POP_OK)
            return s;
        if (texture_map_ready && (s = install(0x00503400u, x360_find_texture, POP_HOOK_REPLACE, 17)) != POP_OK)
            return s;
    }
    rtex_init(api);
    eo_extra_init(api);
    eo_extra_init2(api);
    eo_extra_init3(api);
    eo_extra_init3b(api);
    eo_extra_init3c(api);
    if (g_x360_sky_ready && (s = install(0x006c60f0u, x360_sky_params, POP_HOOK_REPLACE, 18)) != POP_OK)
        return s;
    if (g_x360_sky_ready && (s = install(0x006db310u, x360_sky_submit, POP_HOOK_REPLACE, 28)) != POP_OK)
        return s;
    const int cpl_on = setting("clean_pools", 1) != 0;   /* Test113 module F: the Xbox-derived scenery lights stay off */
    if ((cpl_on ? 1 : x3sl_init(api)) &&
        (install_at(0x006da9b0u, 0x00724203u, x3sl_render_item, POP_HOOK_REPLACE, 68) != POP_OK ||
         install_at(0x006c6d10u, 0x006e03e6u, x3sl_draw_entry, POP_HOOK_BEFORE, 69) != POP_OK)) {
        api->log(api, "core.nfsmw: Xbox scenery lights unavailable (hook)");
        g_x3sl_ready = 0;
    } else if (cpl_on) g_x3sl_ready = 1;   /* hooks in place; no Xbox data loaded (empty tables) */
    if (g_x3sl_ready) {
        const int normalmap = cpl_on ? 0 : x3sl_pass_init(api);
        const int world = x3lr_init(api) && x3lr_pass_init(api);
        if (normalmap || world) {
            if (install(0x006e0200u, x3sl_list_done, POP_HOOK_AFTER, 70) == POP_OK) {
                g_x3sl_pass_ready = normalmap;
                g_x3lr_pass_ready = world;
            } else
                api->log(api, "core.nfsmw: Xbox scenery lights: pass unavailable (hook 006e0200)");
        }
    }
    /* Xbox 360 exposure and bloom (Test68): before the frame's HUD/front-end call 006c3870 at 006e75a1,
     * which follows the main view's render 006de300. */
    {
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
    }
    scenery_lod_probe_init(api); /* Test127 probe: NFSMW_TEST_SCENERY_LOD, or the owner switch scenery_trace (Test139) */
    scenery_fade_init(api);   /* run 27: setting scenery_fade or TEST NFSMW_TEST_FADEIN=1; slots 145-147 */
    vehicle_lights_install(api);  /* run 13: flare capture hook, slot 155 */ /* TEST ONLY (Test131): absent unless NFSMW_TEST_MIRROR_FLARES=1 */
    fe_shadows_init(api);       /* setting fe_shadows: no car ground shadow in the front end (as on the 360) */
    if (api->on_key) api->on_key(api, tskip_key, NULL, &g_tskip_hook);   /* keyboard T skips the track (owner 7 Oct) */
    xfx_init(api);             /* Xbox 360 particles (task 145) */
    x360fe_init(api);          /* 2.0.4: the Stuff Pack front end (ported run 29) */
    datamods_init(api);        /* run 29 modding stage 1: DataMods layers, after the front end so a data mod wins */
    snd_stall_init(api);       /* run 32 issue 8: the sound thread's restart guard */
    snd_bank_log_init(api);    /* 3.0.4 issue 8: each sound bank's address (the first 16) */
    smoke_init(api);           /* denser tyre smoke (run 14): slot 152, same function as the spark hook */
    smoke_light_init(api);     /* lit smoke (run 14 stage 1): slot 153, the per-emitter particle draw */
    /* x360_exposure (label "Glow and auto brightness"; the key name is historical) gates the rebuilt effects. */
    atmosphere_init(api);   /* run 14 B1: before upscale_init (its scene-end packet goes before the 252 marker) */
    upscale_init(api);   /* run 13: before pfx_init (same-address hooks run in install order) */
    if (pfx_init(api) &&
        install_at(0x006c3870u, 0x006e75a6u, pfx_frame, POP_HOOK_BEFORE, 71) != POP_OK) {
        api->log(api, "core.nfsmw: effects unavailable (hook)");
        g_pfx_enabled = 0;
    }
    /* ...and the main view's colour pass writes alpha (the HDR scene mark) and gives up its depth surface. */
    if (g_pfx_enabled && install_at(0x006de210u, 0x006ded21u, pfx_view_colour, POP_HOOK_BEFORE, 72) != POP_OK)
        api->log(api, "core.nfsmw: effects: colour-pass hook unavailable; no HDR mark, depth of field from the bound depth");
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
    discord_status_stop();   /* never waits on Discord: a hung socket is shut down */
    scenery_lod_probe_exit(g_api);
    scenery_fade_exit(g_api);
    mirror_car_shadows_exit(g_api);
    mirror_props_exit(g_api);
    xfx_exit(g_api);
    smoke_exit(g_api);
    smoke_light_exit(g_api);
    upscale_exit(g_api);
    atmosphere_exit(g_api);
    if (g_x3sl_ready) {
        char line[200];
        snprintf(line, sizeof line, "core.nfsmw: Xbox scenery lights: %llu draw items, %llu verified instances drawn, %llu render entries tagged, %llu tagged draws, %llu with the aux pass",
                 (unsigned long long)g_x3sl_items, (unsigned long long)g_x3sl_matched_items,
                 (unsigned long long)g_x3sl_tagged_entries, (unsigned long long)g_x3sl_tagged_draws,
                 (unsigned long long)g_x3sl_aux_draws);
        g_api->log(g_api, line);
        char types[256]; int off = 0;
        for (unsigned t = 0; t < 16; ++t)
            if (g_x3sl_type_draws[t])
                off += snprintf(types + off, sizeof types - (size_t)off, " effect%u=%llu", t, (unsigned long long)g_x3sl_type_draws[t]);
        snprintf(line, sizeof line, "core.nfsmw: Xbox scenery lights: tagged draws by PC effect:%s", off ? types : " none");
        g_api->log(g_api, line);
    }
    if (g_x3lr_pass_ready) {
        char line[240];
        snprintf(line, sizeof line, "core.nfsmw: Xbox light pools: %llu terrain-piece items, %llu lit items with a non-identity matrix (skipped), %llu terrain-piece draws and %llu verified-instance draws with the WORLD pass (%llu past the single-pass check), %llu in other views (no pass)",
                 (unsigned long long)g_x3lr_recv_items, (unsigned long long)g_x3lr_matrix_items,
                 (unsigned long long)g_x3lr_recv_draws, (unsigned long long)g_x3lr_set_draws,
                 (unsigned long long)g_x3lr_single_pass, (unsigned long long)g_x3lr_other_view);
        g_api->log(g_api, line);
        snprintf(line, sizeof line, "core.nfsmw: mirror pools: %llu WORLD entries fused in the mirror (mirror_pools)",
                 (unsigned long long)g_mirror_pool_entries);
        g_api->log(g_api, line);
    }
    if (g_x3lr_pass_ready) {
        char types[256]; int off = 0;
        for (unsigned t = 0; t < 16; ++t)
            if (g_x3lr_type_draws[t])
                off += snprintf(types + off, sizeof types - (size_t)off, " effect%u=%llu", t, (unsigned long long)g_x3lr_type_draws[t]);
        char line[300];
        snprintf(line, sizeof line, "core.nfsmw: Xbox light pools: lit entries drawn by PC effect:%s", off ? types : " none");
        g_api->log(g_api, line);
    }
    if (g_x3sl_ready) {
        char line[160];
        snprintf(line, sizeof line, "core.nfsmw: Xbox scenery lights: %llu lit draw items placed by a matrix (lights given in model space)%s",
                 (unsigned long long)g_x3_placed_lit, g_x3_old_prop_space ? " - TEST: old world-space lights" : "");
        g_api->log(g_api, line);
    }
    x3lr_free(&g_x3lr);
    g_x3lr_pass_ready = 0;
    x3sl_free(&g_x3sl);
    g_x3sl_ready = 0;
    pfx_exit(g_api);
    fe_shadows_exit(g_api);
    lamp_daylight_exit(g_api);
    if (g_tskip_hook) { g_api->hook_remove(g_api, g_tskip_hook); g_tskip_hook = 0; }
    if (g_x3sl_key_hook) { g_api->hook_remove(g_api, g_x3sl_key_hook); g_x3sl_key_hook = 0; }
    impact_feedback_stop(g_api);
    x360_reset_state();
    g_x360_hooked = 0;
    g_x360_collision_hooked = 0;
    g_x360_scratch = 0;
    frontend_pad_exit();
    native_audio_exit(g_api);
    for (unsigned i = 0; i < sizeof g_hooks / sizeof g_hooks[0]; ++i)
        if (g_hooks[i]) {
            g_api->hook_remove(g_api, g_hooks[i]);
            g_hooks[i] = 0;
        }
    return POP_OK;
}
