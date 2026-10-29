// settings_port.c - the port's own settings in the F10 menu (settings.h): the NFS_* switches that make sense to
// users, live where the code allows it. Other branches register theirs the same way from their own files.
#include <math.h>
#include <dirent.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>
#include <CoreFoundation/CoreFoundation.h>

#include "host.h"
#include "settings.h"
#include "roots.h"            // X360_ENGINE_CONSTS / X360E_MIRROR_WORLD (gen/x360_engine.inc)
#include "posaudio.h"
#include "../host/audio/dsound_host.h"

void app_restart_game(int unused);         // app.m: confirmation, then the normal Quit path
void app_quit_game(int unused);            // app.m: confirmation, then the normal Quit path
void x360_set_tone_curve(int x360);
void x360_set_reflection(int x360);
void x360_set_shadow_fit(int preset);
void mirror_set_size(int pct);
void mirror_set_detail(int full);
void mirror_set_msaa(int on);
void materials_set_car_paint(int x360);      // materials.m
void materials_set_rain_ripples(int x360);
void materials_set_parallax(int x360);
void materials_set_car_glass(int x360);
void materials_set_car_normals(int x360);
void materials_set_car_flake(int x360);
void materials_set_grass(int x360);
void materials_set_water(int x360);
void materials_set_building_glass(int x360);
void materials_set_particles(int x360);
void materials_set_trees(int x360);
void auxlights_set_enabled(int x360);
void lighting_set_world(int neutral);         // lighting.c
void x360_post_set_chain(int x360);           // x360_post.m
void x360_post_set_raindrops(int mode);
void x360_post_set_mirror_exposure(float x);
void x360_post_set_speed_blur(int mode);
void x360_post_set_curve_glow(int mode);
void x360_post_set_bloom_surfaces(int mode);
void x360_post_set_highlights(int mode);
void x360_post_set_deband(int mode);
void rumble_set_enabled(int on);              // rumble.m
void feedback_set_surfaces(int on);           // feedback.c
void feedback_set_props(int on);
const char *report_write(const char *category, const char *note, const char *why, const char *attach);   // report.m
const char *report_last_path(void);
const char *report_audio_device(void);
void report_live(float *fps, float *p99, float *gpu);
int dsound_output_info(const char **mode, int *live, uint64_t *underruns);
void portmenu_open_folder(const char *path);  // portmenu.m
void portmenu_status(const char *fmt, ...);

#define N(a) (int)(sizeof(a) / sizeof *(a))
#define REG(s) settings_add(&(s))

// ---------------- Picture: the look preset ----------------
// Original PC / Xbox 360 set every PC-vs-X360 choice (the table is in settings.c); My picks restores the user's own values; any
// edit of a picture row shows Custom. Its state is derived from the rows, so it is never saved itself.
static const char *const kLook[] = {"Original PC", "Xbox 360", "My picks", "Custom"};
static void look_apply(int v) {
    int r = settings_look_apply(v);
    if (r < 0) portmenu_status("No saved picks yet: change a picture row first.");
    else if (r > 0) portmenu_status("%d changed row%s take%s effect after Restart game \u27F3 (Display & performance).", r, r == 1 ? "" : "s", r == 1 ? "s" : "");
}
static Setting s_look = {.id = "graphics.look_preset", .category = SET_PICTURE, .order = 10, .label = "Look preset",
    .help = "Sets every PC-versus-Xbox 360 picture choice at once, leaving port extras such as Natural bloom as you set them; changing any row shows Custom.",
    .type = SET_CHOICE, .choices = kLook, .nchoices = 4, .def = PRESET_CUSTOM, .pref = "", .own_pref = 1,
    .apply = look_apply, .get = settings_look_state};

// ---------------- Section headings (not selectable) ----------------
                                                                                                                     
//   World (SET_WORLD)               (Scenery lights took order 80 between Building glass 70 and Road reflection 90)
//   Cars & cutscenes (SET_CARS)     (Arrest scenes took order 40 after Police light glow 30; x360_textures.m)
//   Audio (SET_AUDIO)               Cutscene audio 90 (x360nisaudio.c), Reverb 65 (x360audio.c), under Xbox 360 sound (50)
//                                   "Extra EA Trax" (master switch; per-song options live in the game's list): order 100
// Orders leave room: in-flight branches add rows at the commented slots (World, Cars & cutscenes, Audio) when merged.
#define HEAD(var, id_, cat, ord, text, helptext) static Setting var = {.id = id_, .category = cat, .order = ord, .label = text, .help = helptext, .type = SET_HEADING}
HEAD(h_out, "audio.h_output", SET_AUDIO, 5, "Sound", "Where the game's sound goes and how it is protected from distortion.");
HEAD(h_x360snd, "audio.h_x360", SET_AUDIO, 50, "Xbox 360 sound", "Each PC or X360 choice needs the X360 files and a restart.");
HEAD(h_pad, "controls.h_pad", SET_CONTROLS, 5, "Pad", "How the controller reaches the game and this menu.");
HEAD(h_rumble, "controls.h_rumble", SET_CONTROLS, 60, "Rumble", "What the controller's motors do.");
HEAD(h_steer, "controls.h_steering", SET_CONTROLS, 100, "Steering", "How the stick turns the wheels.");
HEAD(h_wl, "world.h_lighting", SET_WORLD, 5, "Lighting and shadows", "How light, shadow and the scenery's own lamps look.");
HEAD(h_wsurf, "world.h_surfaces", SET_WORLD, 48, "Surfaces and weather", "Materials, water, smoke and rain.");
HEAD(h_wdet, "world.h_detail", SET_WORLD, 108, "Detail and textures", "How far detail reaches and which texture sets load.");
HEAD(h_career, "gameplay.h_career", SET_GAMEPLAY, 5, "Career", "Rules of your career and the game's start-up.");
HEAD(h_cheats, "gameplay.h_cheats", SET_GAMEPLAY, 100, "Changes your save", "These change what your save contains.");

// ---------------- Display ----------------
static const char *const kWindow[] = {"Full screen", "Window"}, *const kWindowV[] = {"1", "0"};
static int window_get(void) { return !user32_fullscreen(); }
static void window_apply(int v) { if (window_get() != v) user32_toggle_fullscreen(); }
static Setting s_window = {.id = "display.window", .category = SET_DISPLAY, .order = 10, .label = "Window mode",
    .help = "Full screen covers the whole screen and keeps Cmd-Tab instant, while Window is a resizable window, the same as the green button.",
    .type = SET_CHOICE, .choices = kWindow, .values = kWindowV, .nchoices = 2, .env = "NFS_FULLSCREEN", .pref = "",
    .own_pref = 1, .apply = window_apply, .get = window_get};

static const char *const kOverlay[] = {"Off", "Compact", "Full"};
static void overlay_apply(int v) { overlay_set_level(v); }
static Setting s_overlay = {.id = "display.overlay", .category = SET_DISPLAY, .order = 40, .label = "Performance overlay",
    .help = "Frame rate and timing in the corner, and Full adds a frame-time graph, memory, render, audio and controller details (View menu, Cmd-Option-P).",
    .type = SET_CHOICE, .choices = kOverlay, .nchoices = 3, .env = "NFS_OVERLAY", .pref = "overlay", .own_pref = 1,
    .apply = overlay_apply, .get = overlay_level};

// ---------------- Graphics ----------------
// Restart: hc_transform specializes world shaders for float_scene_on, while
// get_shader_variant caches by bytecode/options/material, not that mode.
static const char *const kHdr[] = {"Off", "On"}, *const kHdrV[] = {"off", "edr"};
static Setting s_hdr = {.id = "graphics.hdr_output", .category = SET_PICTURE, .order = 20, .label = "HDR output",
    .help = "Uses HDR highlights when the screen supports them, with SDR fallback otherwise. Restart game to apply.",
    .type = SET_CHOICE, .choices = kHdr, .values = kHdrV, .nchoices = 2, .def = 0, .env = "NFS_HDR_OUTPUT", .restart = 1};
static void hdr_status(char *buf, int n) { snprintf(buf, (size_t)n, "%s", render_hdr_output_status()); }
static Setting s_hdr_status = {.id = "graphics.hdr_status", .category = SET_PICTURE, .order = 21, .label = "HDR status",
    .help = "The current output. SDR fallback keeps your HDR preference for when the screen has HDR headroom; split screen uses SDR.",
    .type = SET_INFO, .value_text = hdr_status};
static const char *const kFrameLimit[] = {"Off", "120", "60", "40"};
static const char *const kFrameLimitV[] = {"off", "120", "60", "40"};
static void presentation_apply(int v) { (void)v; RenderPresentation ps; render_presentation_stats(&ps); }
static Setting s_framelimit = {.id = "graphics.frame_rate_limit", .category = SET_DISPLAY, .order = 20, .label = "Frame rate limit",
    .help = "Caps the frames shown, live: at 120 Hz choose 120, 60 or 40 fps (at 60 Hz, 40 gives 30 fps), while the simulation keeps real time.",
    .type = SET_CHOICE, .choices = kFrameLimit, .values = kFrameLimitV, .nchoices = 4, .env = "NFS_FRAME_LIMIT", .apply = presentation_apply};
static Setting s_vrr = {.id = "graphics.variable_refresh", .category = SET_DISPLAY, .order = 30, .label = "Variable refresh pacing",
    .help = "Asks macOS for adaptive refresh pacing when the display and a full screen window support it; availability is recorded in session logs.",
    .type = SET_TOGGLE, .env = "NFS_VRR", .apply = presentation_apply};
static const char *const kX360Pc[] = {"X360", "PC"}, *const kX360PcV[] = {"x360", "pc"};
static const char *const kPcX360[] = {"PC", "X360"}, *const kPcX360V[] = {"pc", "x360"};
static void postfx_apply(int v) { x360_post_set_chain(v == 0); }
static Setting s_postfx = {.id = "graphics.postfx", .category = SET_PICTURE, .order = 30, .label = "Post-processing",
    .help = "X360 adds auto-exposure, bloom, tone curve, speed blur, depth of field and vignette; PC keeps the PC's own look treatment.",
    .type = SET_CHOICE, .choices = kX360Pc, .values = kX360PcV, .nchoices = 2, .env = "NFS_POSTFX", .apply = postfx_apply};
static void tone_apply(int v) { x360_set_tone_curve(v == 1); }
static Setting s_tone = {.id = "graphics.tone_curve", .category = SET_PICTURE, .order = 40, .label = "Tone curve",
    .help = "PC keeps the PC's colour-bloom curve with a brighter sky; X360 uses the X360's black-bloom curve for both terms.",
    .type = SET_CHOICE, .choices = kPcX360, .values = kPcX360V, .nchoices = 2, .env = "NFS_TONE_CURVE", .apply = tone_apply};
// BEGIN private car settings
#if !defined(NFS_PUBLIC) || !NFS_PUBLIC
#include "private_cars.h"
static Setting s_later_cars = {.id="cars.later_games", .category=SET_CARS, .order=50,
    .label="Cars from later games", .help="Use approved installed cars. Takes effect after restart.",
    .type=SET_TOGGLE, .def=1, .restart=1};
static Setting h_original_cars = {.id="cars.original_models", .category=SET_CARS, .order=60,
    .label="Use original MW model", .type=SET_HEADING};
static const char *const original_car_names[] = {PRIVATE_CARS_SWAP_NAMES};
static Setting s_original_cars[14];
static char original_car_ids[14][64];
static void register_private_car_settings(void) {
    settings_add(&s_later_cars); settings_add(&h_original_cars);
    for (unsigned i=0;i<14;++i) {
        snprintf(original_car_ids[i],sizeof original_car_ids[i],"cars.original.%s",original_car_names[i]);
        s_original_cars[i]=(Setting){.id=original_car_ids[i], .category=SET_CARS, .order=61+(int)i,
            .label=original_car_names[i], .help="On uses the original MW model. Takes effect after restart.",
            .type=SET_TOGGLE, .restart=1};
        settings_add(&s_original_cars[i]);
    }
}
#endif
// END private car settings
static void paint_apply(int v) { materials_set_car_paint(v == 0); }
static Setting s_paint = {.id = "graphics.car_paint", .category = SET_CARS, .order = 10, .label = "Car paint",
    .help = "X360 gives the console's sun-coloured paint highlight, while PC uses a fixed highlight.",
    .type = SET_CHOICE, .choices = kX360Pc, .values = kX360PcV, .nchoices = 2, .env = "NFS_CAR_PAINT", .live_from_default = 1, .apply = paint_apply};
static void glass_apply(int v) { materials_set_car_glass(v == 1); }
static Setting s_glass = {.id = "graphics.car_glass", .category = SET_CARS, .order = 20, .label = "Car glass",
    .help = "PC keeps the retuned car windows, while X360 uses the console's window opacity and stronger reflections.",
    .type = SET_CHOICE, .choices = kPcX360, .values = kPcX360V, .nchoices = 2, .def = 1, .env = "NFS_CAR_GLASS", .apply = glass_apply};
static void carnm_apply(int v) { materials_set_car_normals(v == 1); }
static Setting s_carnm = {.id = "graphics.car_normals", .category = SET_CARS, .order = 30, .label = "Tyre and badge detail",
    .help = "X360 adds the console's bump detail to tyre sidewalls, tread and badges (needs the X360 car paint on); PC keeps them flat.",
    .type = SET_CHOICE, .choices = kPcX360, .values = kPcX360V, .nchoices = 2, .def = 1, .env = "NFS_CAR_NORMALMAPS", .apply = carnm_apply};
static void flake_apply(int v) { materials_set_car_flake(v == 1); }
static Setting s_flake = {.id = "graphics.car_flake", .category = SET_CARS, .order = 35, .label = "Paint flake",
    .help = "X360 adds the console's fine sparkle to metallic and pearl paint, rims and hoses close to the camera (needs the X360 car paint on); PC keeps them smooth.",
    .type = SET_CHOICE, .choices = kPcX360, .values = kPcX360V, .nchoices = 2, .def = 1, .env = "NFS_CAR_FLAKE", .apply = flake_apply};
static void grass_apply(int v) { materials_set_grass(v == 1); }
static void trees_apply(int v) { materials_set_trees(v == 1); }
static Setting s_trees = {.id = "graphics.trees", .category = SET_WORLD, .order = 55, .label = "Tree shading",
    .help = "PC lights tree leaves by their flat faces, X360 shades each tree as a soft ball with a lit side and a brighter shade (trees without X360 data keep PC).",
    .type = SET_CHOICE, .choices = kPcX360, .values = kPcX360V, .nchoices = 2, .def = 1, .env = "NFS_TREES", .apply = trees_apply};
static void water_apply(int v) { materials_set_water(v == 1); }
static void particles_apply(int v) { materials_set_particles(v == 1); }
static Setting s_particles = {.id = "graphics.particles", .category = SET_WORLD, .order = 75, .label = "Smoke and dust shading",
    .help = "X360 softens shadows on smoke and sparks and keeps close smoke from filling the screen, while PC uses hard shadows.",
    .type = SET_CHOICE, .choices = kPcX360, .values = kPcX360V, .nchoices = 2, .def = 1, .env = "NFS_PARTICLES", .apply = particles_apply};
static void aux_apply(int v) { auxlights_set_enabled(v == 1); }
static Setting s_aux = {.id = "graphics.scenery_lights", .category = SET_WORLD, .order = 45, .label = "Scenery lights",
    .help = "PC keeps the original scenery lighting, while X360 adds tunnel and road lights on exactly matched scenery (unmatched scenery keeps PC lighting).",
    .type = SET_CHOICE, .choices = kPcX360, .values = kPcX360V, .nchoices = 2, .def = 1, .env = "NFS_AUX_LIGHTS", .apply = aux_apply};
static Setting s_water = {.id = "graphics.water", .category = SET_WORLD, .order = 60, .label = "Water",
    .help = "PC shows animated water, X360 adds wave normals, reflections and sun highlights on sea and ponds at the cost of a reflection pass near water.",
    .type = SET_CHOICE, .choices = kPcX360, .values = kPcX360V, .nchoices = 2, .def = 1, .env = "NFS_WATER", .apply = water_apply};
static void bglass_apply(int v) { materials_set_building_glass(v == 1); }
static Setting s_bglass = {.id = "graphics.building_glass", .category = SET_WORLD, .order = 70, .label = "Building glass",
    .help = "PC slides a reflection with the view, X360 reflects by viewing angle with Fresnel edges and sun glints (car glass is separate).",
    .type = SET_CHOICE, .choices = kPcX360, .values = kPcX360V, .nchoices = 2, .def = 1, .env = "NFS_BUILDING_GLASS", .apply = bglass_apply};
static Setting s_grass = {.id = "graphics.grass", .category = SET_WORLD, .order = 50, .label = "Grass",
    .help = "PC draws flat grass, X360 adds five layers on nearby grass and one in the distance, and missing grass assets keep the PC draw.",
    .type = SET_CHOICE, .choices = kPcX360, .values = kPcX360V, .nchoices = 2, .def = 1, .env = "NFS_GRASS", .apply = grass_apply};
static const char *const kWorldLight[] = {"Neutral", "Original"}, *const kWorldLightV[] = {"neutral", "original"};
static void worldlight_apply(int v) { lighting_set_world(v == 0); }
static Setting s_worldlight = {.id = "graphics.world_lighting", .category = SET_WORLD, .order = 10, .label = "World lighting",
    .help = "Neutral gives daylight sun that turns warm when low with softer blue shade, while Original keeps the game's authored lighting.",
    .type = SET_CHOICE, .choices = kWorldLight, .values = kWorldLightV, .nchoices = 2, .def = 0, .env = "NFS_WORLD_LIGHTING",
    .apply = worldlight_apply};
static const char *const kRefl[] = {"X360 (half screen)", "PC (320x240)"};
static void reflection_apply(int v) { x360_set_reflection(v == 0); }
static Setting s_refl = {.id = "graphics.road_reflection", .category = SET_WORLD, .order = 90, .label = "Road reflection",
    .help = "How sharp the reflection on wet roads is, the X360's half-screen resolution or the PC's 320x240.", .type = SET_CHOICE, .choices = kRefl, .values = kX360PcV, .nchoices = 2,
    .env = "NFS_ROAD_REFLECTION", .apply = reflection_apply};
static void ripples_apply(int v) { materials_set_rain_ripples(v == 0); }
static Setting s_ripples = {.id = "graphics.rain_ripples", .category = SET_WORLD, .order = 100, .label = "Rain ripples",
    .help = "X360 ripples wet roads with raindrop rings, while PC leaves a still reflection.",
    .type = SET_CHOICE, .choices = kX360Pc, .values = kX360PcV, .nchoices = 2, .env = "NFS_RAIN_RIPPLES", .live_from_default = 1,
    .apply = ripples_apply};
static void parallax_apply(int v) { materials_set_parallax(v == 0); }
static Setting s_parallax = {.id = "graphics.parallax", .category = SET_WORLD, .order = 85, .label = "Wall and kerb depth",
    .help = "X360 shifts normal-mapped walls, kerbs and cliffs with the viewing angle and darkens them faster away from the sun, while PC leaves them flat.",
    .type = SET_CHOICE, .choices = kX360Pc, .values = kX360PcV, .nchoices = 2, .env = "NFS_PARALLAX", .apply = parallax_apply};
static const char *const kDrops[] = {"X360", "PC", "PC, before post"}, *const kDropsV[] = {"x360", "pc", "pc_before"};
static void drops_apply(int v) { x360_post_set_raindrops(v == 0 ? 2 : v == 1 ? 1 : 0); }
static Setting s_drops = {.id = "graphics.raindrops", .category = SET_WORLD, .order = 105, .label = "Raindrops on the lens",
    .help = "Drops on the lens in rain: X360 draws the console's after post-processing, PC the PC's after it, and PC, before post, as the PC drew them.",
    .type = SET_CHOICE, .choices = kDrops, .values = kDropsV, .nchoices = 3, .env = "NFS_RAINDROPS", .apply = drops_apply};
static const char *const kSpeedBlur[] = {"Off", "X360", "Strong"}, *const kSpeedBlurV[] = {"off", "x360", "strong"};
static void speed_blur_apply(int v) { x360_post_set_speed_blur(v); }
static Setting s_speedblur = {.id = "graphics.speed_blur", .category = SET_PICTURE, .order = 90, .label = "Speed blur",
    .help = "A zoom blur at speed that keeps the car and centre sharp: X360 is the console's length, Strong is 1.5 times that, Off is none.",
    .type = SET_CHOICE, .choices = kSpeedBlur, .values = kSpeedBlurV, .nchoices = 3, .def = 1, .env = "NFS_SPEED_BLUR",
    .apply = speed_blur_apply};
static const char *const kGlow[] = {"X360 (scaled to screen)", "Tight (16 px)", "None (per pixel)"}, *const kGlowV[] = {"x360", "tight", "pixel"};
static void glow_apply(int v) { x360_post_set_curve_glow(v); }
static Setting s_glow = {.id = "graphics.curve_glow", .category = SET_PICTURE, .order = 45, .label = "Tone curve glow",
    .help = "How wide the tone curve's local glow spreads: X360 scales it to the screen at any resolution, Tight shows halos on edges at 4K.",
    .type = SET_CHOICE, .choices = kGlow, .values = kGlowV, .nchoices = 3, .env = "NFS_CURVE_GLOW", .apply = glow_apply};
static const char *const kBloomSurf[] = {"Glow", "Halo only", "Natural (eye)"}, *const kBloomSurfV[] = {"glow", "halo", "natural"};
static void bloom_surf_apply(int v) { x360_post_set_bloom_surfaces(v); }
static Setting s_bloomsurf = {.id = "graphics.bloom_surfaces", .category = SET_PICTURE, .order = 50, .label = "Bloom style",
    .help = "Natural spreads glare softly as the eye sees it, Glow brightens bright areas over themselves, and Halo draws a ring around them.",
    .type = SET_CHOICE, .choices = kBloomSurf, .values = kBloomSurfV, .nchoices = 3, .def = 2, .env = "NFS_BLOOM_SURFACES", .apply = bloom_surf_apply};
static const char *const kHigh[] = {"Roll off", "Clip"}, *const kHighV[] = {"rolloff", "clip"};
static void high_apply(int v) { x360_post_set_highlights(v); }
static Setting s_high = {.id = "graphics.highlights", .category = SET_PICTURE, .order = 60, .label = "Highlights",
    .help = "Roll off eases bright car paint and glare into white like a camera or the eye, while Clip cuts them off at flat white.",
    .type = SET_CHOICE, .choices = kHigh, .values = kHighV, .nchoices = 2, .def = 0, .env = "NFS_HIGHLIGHTS", .apply = high_apply};
static const char *const kBand[] = {"Smooth", "Off"}, *const kBandV[] = {"smooth", "off"};
static void band_apply(int v) { x360_post_set_deband(v); }
static Setting s_band = {.id = "graphics.deband", .category = SET_PICTURE, .order = 80, .label = "Banding",
    .help = "Smooth removes the steps in sky gradients with deband and fine dither, while Off shows the 8-bit image as drawn.",
    .type = SET_CHOICE, .choices = kBand, .values = kBandV, .nchoices = 2, .def = 0, .env = "NFS_DEBAND", .apply = band_apply};
// Keep the old slider's saved numeric spellings and default; `main` names the existing x1.0 behavior explicitly.
static const char *kMirrorExposure[] = {"50%", "60%", "70%", "80%", "90%", "100% (default)", "110%", "120%",
    "130%", "140%", "150%", "160%", "170%", "180%", "190%", "200%", "Follow main view"};   // the X360's is marked at start
static const char *const kMirrorExposureV[] = {"0.5", "0.6", "0.7", "0.8", "0.9", "1", "1.1", "1.2", "1.3", "1.4",
    "1.5", "1.6", "1.7", "1.8", "1.9", "2", "main"};
static void mirror_apply(int v) { x360_post_set_mirror_exposure(v == 16 ? 0 : (50 + 10 * v) / 100.0f); }
static Setting s_mirror = {.id = "graphics.mirror_exposure", .category = SET_MIRROR, .order = 10, .label = "Mirror brightness",
    .help = "How bright the rear-view mirror picture is; (X360) marks the console's, and Follow main view matches the main view's exposure.",
    .type = SET_CHOICE, .choices = kMirrorExposure, .values = kMirrorExposureV, .nchoices = N(kMirrorExposure), .def = 5,
    .env = "NFS_MIRROR_EXPOSURE", .apply = mirror_apply};
static const char *const kMirrorSize[] = {"PC (256x256)", "50% of screen size", "67% of screen size", "100% of screen size"},
                         *const kMirrorSizeV[] = {"pc", "50", "67", "100"};
static void mirror_size_apply(int v) { static const int pct[] = {0, 50, 67, 100}; mirror_set_size(pct[v]); }
static Setting s_mirrorsize = {.id = "graphics.mirror_size", .category = SET_MIRROR, .order = 20, .label = "Mirror resolution",
    .help = "How sharp the rear-view mirror is: the PC's 256x256, or a share of its size on screen (1170x315 at 2160p).",
    .type = SET_CHOICE, .choices = kMirrorSize, .values = kMirrorSizeV, .nchoices = 4, .def = 3, .env = "NFS_MIRROR_SIZE", .apply = mirror_size_apply};
static const char *const kMirrorDetail[] = {"PC (reduced)", "Full (as the main view)"}, *const kMirrorDetailV[] = {"pc", "full"};
static Setting s_mirrordetail = {.id = "graphics.mirror_detail", .category = SET_MIRROR, .order = 30, .label = "Mirror world detail",
    .help = "PC draws only the lowest scenery detail and skips small objects in the mirror, while Full uses the main view's detail and culling.",
    .type = SET_CHOICE, .choices = kMirrorDetail, .values = kMirrorDetailV, .nchoices = 2, .def = 1, .env = "NFS_MIRROR_DETAIL", .apply = mirror_set_detail};
static const char *const kMirrorView[] = {"Natural, 45 deg across", "Natural, 60 deg across", "Natural, 75 deg across", "Natural, 90 deg across", "PC (110 deg, squeezed)"},
                         *const kMirrorViewV[] = {"45", "60", "75", "90", "pc"};
static void mirror_view_apply(int v) { static const float d[] = {45, 60, 75, 90, 0}; mirror_set_view(d[v]); }
static Setting s_mirrorview = {.id = "graphics.mirror_view", .category = SET_MIRROR, .order = 40, .label = "Mirror view",
    .help = "Natural shows the world undistorted this wide across, while PC squeezes a square 110 degree view sideways 3.7 times.",
    .type = SET_CHOICE, .choices = kMirrorView, .values = kMirrorViewV, .nchoices = 5, .def = 1, .env = "NFS_MIRROR_VIEW", .apply = mirror_view_apply};
static const char *const kMirrorOnOff[] = {"Off (PC)", "On"}, *const kMirrorOnOffV[] = {"off", "on"};
static void mirror_fog_apply(int v) { mirror_set_fog(v); }
static Setting s_mirrorfog = {.id = "graphics.mirror_fog", .category = SET_MIRROR, .order = 50, .label = "Mirror fog",
    .help = "On fades distant scenery in the mirror into the haze as in the main view, while Off is the PC's fog-free mirror.",
    .type = SET_CHOICE, .choices = kMirrorOnOff, .values = kMirrorOnOffV, .nchoices = 2, .def = 1, .env = "NFS_MIRROR_FOG", .apply = mirror_fog_apply};
static Setting s_mirrormsaa = {.id = "graphics.mirror_msaa", .category = SET_MIRROR, .order = 60, .label = "Mirror anti-aliasing",
    .help = "On gives the mirror the main view's anti-aliasing (the game's Antialiasing setting), while Off is the PC's unsmoothed mirror.",
    .type = SET_CHOICE, .choices = kMirrorOnOff, .values = kMirrorOnOffV, .nchoices = 2, .def = 1, .env = "NFS_MIRROR_MSAA", .apply = mirror_set_msaa};
static const char *const kDetail[] = {"PC", "Far (2x)", "Max (4x)"}, *const kDetailV[] = {"pc", "2", "4"};
void detail_set_distance(int v);
static Setting s_detail = {.id = "graphics.detail_distance", .category = SET_WORLD, .order = 110, .label = "Detail distance",
    .help = "How far scenery keeps its full-detail models and small objects stay drawn, where PC matches the PC at this resolution.",
    .type = SET_CHOICE, .choices = kDetail, .values = kDetailV, .nchoices = 3, .def = 0, .env = "NFS_DETAIL_DISTANCE",
    .apply = detail_set_distance};
static const char *const kStream[] = {"Ahead (no pop-in)", "PC"}, *const kStreamV[] = {"ahead", "pc"};
void streaming_set_mode(int v);
static void stream_apply(int v) { streaming_set_mode(v == 0); }
static Setting s_stream = {.id = "graphics.world_streaming", .category = SET_WORLD, .order = 120, .label = "World streaming",
    .help = "Ahead loads the zones you drive into before you reach them so distant buildings don't pop in, while PC loads them as you enter.",
    .type = SET_CHOICE, .choices = kStream, .values = kStreamV, .nchoices = 2, .def = 0, .env = "NFS_WORLD_STREAMING",
    .live_from_default = 1, .apply = stream_apply};
static const char *const kOnOff[] = {"X360", "PC"}, *const kOnOffV[] = {"on", "off"};   // shown X360 / PC, saved on / off
static Setting s_hdtex = {.id = "graphics.hd_textures", .category = SET_WORLD, .order = 130, .label = "HD textures",
    .help = "X360 uses the console's larger world textures where they match the PC's (needs the generated set beside the game), PC keeps the PC's.",
    .type = SET_CHOICE, .choices = kOnOff, .values = kOnOffV, .nchoices = 2, .env = "NFS_X360_TEXTURES", .restart = 1};
static Setting s_uitex = {.id = "graphics.ui_textures", .category = SET_WORLD, .order = 140, .label = "UI textures",
    .help = "X360 uses the console's better texture formats for menu, HUD and font textures at the same size.",
    .type = SET_CHOICE, .choices = kOnOff, .values = kOnOffV, .nchoices = 2, .env = "NFS_X360_UI_TEXTURES", .restart = 1};
static const char *const kFit[] = {"X360", "X360 range only", "PC"}, *const kFitV[] = {"x360", "x360range", "pc"};
static Setting s_fit = {.id = "graphics.shadow_fit", .category = SET_WORLD, .order = 40, .label = "Shadow fit",
    .help = "X360 uses the console's light camera with a 1600x1600 shadow map, white border and front-face casters; shadow quality itself is in Video options.",
    .type = SET_CHOICE, .choices = kFit, .values = kFitV, .nchoices = 3, .env = "NFS_SHADOW_FIT", .apply = x360_set_shadow_fit};

// ---------------- Audio ----------------
static Setting s_limiter = {.id = "audio.peak_limiter", .category = SET_AUDIO, .order = 40, .label = "Peak limiter",
    .help = "Stops loud game sounds distorting while keeping the surround balance, adding up to 2 ms (movies are unaffected).",
    .type = SET_TOGGLE, .def = 1, .env = "NFS_AUDIO_LIMITER", .apply = ds_set_peak_limiter};
static const char *const kSpeakerLayout[] = {"Game (±45°/±135°)", "ITU 5.1 (±30°/±110°)"};
static const char *const kSpeakerLayoutV[] = {"game", "itu"};
static Setting s_speaker_layout = {.id = "audio.speaker_layout", .category = SET_AUDIO, .order = 30, .label = "Speaker layout",
    .help = "Game keeps the original speaker angles, while ITU 5.1 puts the fronts at 30 degrees and surrounds at 110 with smooth positional gains.",
    .type = SET_CHOICE, .choices = kSpeakerLayout, .values = kSpeakerLayoutV, .nchoices = 2,
    .env = "NFS_SPEAKER_LAYOUT", .restart = 1};
static const char *const kEngSnd[] = {"Retail", "Realistic"}, *const kEngSndV[] = {"retail", "realistic"};
static Setting s_engine_sound = {.id = "audio.engine_sound", .category = SET_AUDIO, .order = 35, .label = "Engine sound",
    .help = "Retail plays your engine as one point in one speaker group (behind you in the hood view, whatever the car). Realistic places it where the car's engine really is (front, mid or rear) and spreads it over neighbouring speakers at the same loudness; rival racers, cops, sirens and traffic spread by how close they are, and your own tyre, road and body noise comes from the four wheels instead of one point behind you.",
    .type = SET_CHOICE, .choices = kEngSnd, .values = kEngSndV, .nchoices = 2, .def = 1, .env = "NFS_ENGINE_SOUND", .apply = posaudio_set_engine_sound};
static const char *const kAudio[] = {"Automatic", "Dolby TrueHD 5.1 (lossless)", "Dolby Digital Plus 5.1", "Dolby Digital 5.1", "PCM 5.1", "Stereo"};
static const char *const kAudioV[] = {"auto", "truehd", "ddp", "dd", "pcm51", "stereo"};
static int audio_get(void) {
    const char *p = app_pref("audio_output");
    for (int i = 0; p && *p && i < N(kAudioV); ++i) if (!strcasecmp(p, kAudioV[i])) return i;
    return 0;
}
static void audio_apply(int v) { app_pref_set("audio_output", kAudioV[v]); ds_set_output_choice(kAudioV[v]); }   // as the Audio menu
static Setting s_audio = {.id = "audio.output", .category = SET_AUDIO, .order = 10, .label = "Output",
    .help = "Automatic picks Dolby TrueHD for devices that take it, else Dolby Digital or PCM, and the output reopens at once.",
    .type = SET_CHOICE, .choices = kAudio, .values = kAudioV, .nchoices = N(kAudio), .env = "NFS_AUDIO", .pref = "audio_output",
    .own_pref = 1, .apply = audio_apply, .get = audio_get};
static void audio_now(char *buf, int n) {
    const char *mode = NULL;
    int live = -2;
    uint64_t under = 0;
    if (!dsound_output_info(&mode, &live, &under)) { snprintf(buf, (size_t)n, "no sound device yet"); return; }
    snprintf(buf, (size_t)n, "%s%s%s", live > 0 ? (mode ? mode : "?") : live == 0 ? "silent (NFS_AUDIO=null)" : "waiting for a device",
             live > 0 ? " on " : "", live > 0 ? report_audio_device() : "");
}
static Setting s_audio_now = {.id = "audio.now", .category = SET_AUDIO, .order = 20, .label = "Playing now", .type = SET_INFO,
    .help = "The format the game's sound goes out in, and the device it plays on.", .value_text = audio_now};

// ---------------- Controls ----------------
static const char *const kMenuBtn[] = {"Touchpad click", "PS / Guide button", "L3 + R3 (hold)", "Create / View (hold)"};
static const char *const kMenuBtnV[] = {"touchpad", "home", "l3r3", "view"};
static Setting s_menubtn = {.id = "controls.menu_button", .category = SET_CONTROLS, .order = 10, .label = "Controller: open this menu",
    .help = "Picks the controller button that opens and closes this menu: the touchpad click on DualSense and DualShock 4, else View held or Guide.",
    .type = SET_CHOICE, .choices = kMenuBtn, .values = kMenuBtnV, .nchoices = N(kMenuBtn), .env = "NFS_MENU_BUTTON"};
static const char *const kLayout[] = {"PlayStation", "Xbox"}, *const kLayoutV[] = {"playstation", "xbox"};
static void layout_apply(int v) { user32_set_pad_layout_xbox(v); }
static Setting s_layout;
static int layout_get(void) {                 // with no pick, the active pad's own console layout: also the row's default
    s_layout.def = !user32_pad_native();
    return user32_pad_layout_xbox();
}
static Setting s_layout = {.id = "controls.pad_layout", .category = SET_CONTROLS, .order = 20, .label = "Button layout",
    .help = "PlayStation follows the PS2 manual's layout and Xbox the Xbox manual's, and menus show each console's buttons and prompts. Until you pick one, each controller gets its own console's.",
    .type = SET_CHOICE, .choices = kLayout, .values = kLayoutV, .nchoices = 2, .env = "NFS_PAD_LAYOUT", .pref = "pad_layout",
    .own_pref = 1, .apply = layout_apply, .get = layout_get};
static const char *const kPrompts[] = {"Automatic", "Console glyphs", "PC keycaps"}, *const kPromptsV[] = {"auto", "console", "pc"};
static Setting s_prompts = {.id = "controls.prompts", .category = SET_CONTROLS, .order = 30, .label = "Button prompts",
    .help = "Automatic shows console glyphs while a controller is in use and keycaps after a key press or the mouse.",
    .type = SET_CHOICE, .choices = kPrompts, .values = kPromptsV, .nchoices = 3, .env = "NFS_BUTTON_PROMPTS", .restart = 1};
static const char *const kPads[] = {"1 (single player)", "2 (split-screen)"}, *const kPadsV[] = {"1", "2"};
static Setting s_pads = {.id = "controls.pads", .category = SET_CONTROLS, .order = 35, .label = "Controllers",
    .help = "2 lets a second controller join: Quick Race then offers Split Screen, each player with their own half.",
    .type = SET_CHOICE, .choices = kPads, .values = kPadsV, .nchoices = 2, .env = "NFS_PADS", .restart = 1};
static const char *const kOffOnL[] = {"Off", "On"}, *const kOffOnV[] = {"off", "on"};
static void rumble_apply(int v) { rumble_set_enabled(v); }
static Setting s_rumble = {.id = "controls.rumble", .category = SET_CONTROLS, .order = 70, .label = "Rumble",
    .help = "Rumbles the controller with the X360 rumble model (the game's own Rumble option must be on too).",
    .type = SET_TOGGLE, .choices = kOffOnL, .values = kOffOnV, .nchoices = 2, .def = 1, .env = "NFS_RUMBLE", .apply = rumble_apply};
static const char *const kX360OnL[] = {"X360", "On"}, *const kX360OnV[] = {"x360", "on"};
static void surfaces_apply(int v) { feedback_set_surfaces(v); }
static Setting s_rumble_surfaces = {.id = "controls.rumble_surfaces", .category = SET_CONTROLS, .order = 80, .label = "Rumble on cobbles",
    .help = "Adds a light road texture to cobble, stone and railroad that rises with speed, which the X360 felt like asphalt.",
    .type = SET_TOGGLE, .choices = kX360OnL, .values = kX360OnV, .nchoices = 2, .def = 1, .env = "NFS_RUMBLE_SURFACES", .apply = surfaces_apply};
static void props_apply(int v) { feedback_set_props(v); }
static Setting s_rumble_props = {.id = "controls.rumble_props", .category = SET_CONTROLS, .order = 90, .label = "Prop hits rumble by impact",
    .help = "Boxes, cones and other props rumble by how hard they were hit, while X360 rumbled by your speed so a box at 50 m/s felt like a wall.",
    .type = SET_TOGGLE, .choices = kX360OnL, .values = kX360OnV, .nchoices = 2, .def = 1, .env = "NFS_RUMBLE_PROPS", .apply = props_apply};
static void triggers_apply(int v) { padfx_set_triggers(v); }
static Setting s_triggers = {.id = "controls.pad_triggers", .category = SET_CONTROLS, .order = 40, .label = "DualSense adaptive triggers",
    .help = "Gives the gas and brake triggers resistance and vibration at wheelspin or lock-up, and a shift trigger a click.",
    .type = SET_TOGGLE, .choices = kOffOnL, .values = kOffOnV, .nchoices = 2, .def = 0, .env = "NFS_PAD_TRIGGERS", .apply = triggers_apply};
static void light_apply(int v) { padfx_set_light(v); }
static Setting s_light = {.id = "controls.pad_light", .category = SET_CONTROLS, .order = 50, .label = "Controller lightbar",
    .help = "Colours the lightbar with the heat level in a race, police lights in a pursuit, and tints for speedbreaker and nitrous.",
    .type = SET_TOGGLE, .choices = kOffOnL, .values = kOffOnV, .nchoices = 2, .def = 0, .env = "NFS_PAD_LIGHT", .apply = light_apply};

// ---------------- Diagnostics ----------------
static Setting h_perf = {.id = "diag.h_perf", .category = SET_DIAGNOSTICS, .order = 5, .label = "Performance", .type = SET_HEADING,
    .help = "Live readouts of speed and sound; the overlay itself is in Display & performance."};
static void perf_now(char *buf, int n) {
    float fps, p99, gpu;
    report_live(&fps, &p99, &gpu);
    snprintf(buf, (size_t)n, "%.1f fps   p99 %.2f ms   GPU %.2f ms", fps, p99, gpu);
}
static Setting s_perf = {.id = "diag.perf", .category = SET_DIAGNOSTICS, .order = 10, .label = "Last second", .type = SET_INFO,
    .help = "Frame rate, 99th-percentile frame time and GPU time over the last second (the game is frozen while this menu is open in a race).",
    .value_text = perf_now};
static Setting s_audio_now2 = {.id = "diag.audio", .category = SET_DIAGNOSTICS, .order = 20, .label = "Audio output", .type = SET_INFO,
    .help = "The format the game's sound goes out in, and the output device.", .value_text = audio_now};
static Setting h_report = {.id = "diag.h_report", .category = SET_DIAGNOSTICS, .order = 30, .label = "Report a problem", .type = SET_HEADING,
    .help = "Saves a folder with a screenshot, recent stats, the log and your settings."};
static const char *const kCat[] = {"Audio", "Graphics", "Controls", "Gameplay", "Crash / freeze", "Other"};
static const char *const kCatV[] = {"Audio", "Graphics", "Controls", "Gameplay", "Crash-freeze", "Other"};
static Setting s_cat = {.id = "diag.report_category", .category = SET_DIAGNOSTICS, .order = 40, .label = "What's wrong",
    .help = "Pick the kind of problem; it goes on the report's first lines.", .type = SET_CHOICE, .choices = kCat, .values = kCatV,
    .nchoices = N(kCat), .def = 5, .pref = ""};
static Setting s_note = {.id = "diag.report_note", .category = SET_DIAGNOSTICS, .order = 50, .label = "Note", .type = SET_TEXT,
    .help = "Select to enter a short optional description with your keyboard or controller."};
static void report_save(int v) {
    (void)v;
    Setting *c = settings_find("diag.report_category");
    const char *p = report_write(c ? kCatV[c->get ? c->get() : c->value] : "Other", settings_text("diag.report_note"), NULL, NULL);
    if (p && *p) portmenu_status("Report saved: %s", p);
    else portmenu_status("Couldn't write the report");
}
static void report_saved_text(char *buf, int n) {
    const char *p = report_last_path(), *slash = p ? strstr(p, "/reports/") : NULL;
    snprintf(buf, (size_t)n, "%s", slash ? slash + 1 : "");
}
static Setting s_save = {.id = "diag.report_save", .category = SET_DIAGNOSTICS, .order = 60, .label = "Save report", .type = SET_ACTION,
    .help = "Writes a folder in reports/ with a screenshot, the last minutes of stats and events, the log and all settings.",
    .apply = report_save, .value_text = report_saved_text};
static void open_reports(int v) {
    (void)v;
    char dir[1100];
    snprintf(dir, sizeof dir, "%s/reports", userdata_dir());
    portmenu_open_folder(dir);
}
static Setting s_open = {.id = "diag.report_open", .category = SET_DIAGNOSTICS, .order = 70, .label = "Show reports in Finder", .type = SET_ACTION,
    .help = "Opens the reports folder.", .apply = open_reports};
static Setting h_log = {.id = "diag.h_log", .category = SET_DIAGNOSTICS, .order = 80, .label = "Logging", .type = SET_HEADING,
    .help = "Deep logs, the logs folder and restarting the game."};
static Setting s_full = {.id = "diag.full_logging", .category = SET_DIAGNOSTICS, .order = 90, .label = "Full diagnostic logging",
    .help = "From the next launch writes every event, frame and stat into logs/<time>/ for a deep session, so turn it off afterwards.",
    .type = SET_TOGGLE, .choices = kOffOnL, .values = kOffOnV, .nchoices = 2, .env = "BOOT_DIAG", .pref = "diag_logging",
    .own_pref = 1, .restart = 1};
static void open_logs(int v) { (void)v; portmenu_open_folder(diag_on && diag_dir ? diag_dir : log_dir()); }
static Setting s_restart = {.id = "diag.restart_game", .category = SET_DISPLAY, .order = 50, .label = "Restart game", .type = SET_ACTION,
    .help = "Saves settings and restarts after a second press within 4 seconds, losing race progress since the last save.", .apply = app_restart_game};
static Setting s_quit = {.id = "diag.quit_game", .category = SET_DISPLAY, .order = 49, .label = "Quit game", .type = SET_ACTION,
    .help = "Saves settings and closes the game after a second press within 4 seconds, losing race progress since the last save.", .apply = app_quit_game};
static Setting s_logs = {.id = "diag.logs_open", .category = SET_DIAGNOSTICS, .order = 100, .label = "Show logs in Finder", .type = SET_ACTION,
    .help = "Opens this run's log folder (the diagnostic folder with full logging).", .apply = open_logs};

// Keep the LAN row outside the language-only extraction used by gamelang_test.py.
static Setting s_lan = {.id = "gameplay.lan", .category = SET_GAMEPLAY, .order = 80, .label = "LAN play (beta)",
    .help = "In development; both Macs must be on the same network. Takes effect after a restart.",
    .type = SET_TOGGLE, .def = 0, .env = "NFS_LAN", .own_pref = 1, .restart = 1};

// ---------------- Gameplay: the game's text language (old public issues #7, #12) ----------------
// speed.exe reads one registry value, Language, and looks it up in its LanguageConversionTable (0x8fb578, FUN_006c5350
// area); the text then comes from LANGUAGES\<NAME>.BIN (LanguageInfoTable 0x8f40f8). Those ten are the only texts this exe
// can load (its table also names Korean, Chinese, Japanese and Thai, but there is no .bin path for them). Speech files are
// not suffixed (SOUND/SPEECH/copspeech.big): speech stays in the language the PC game was installed with.
// The choice: the F10 row when set; else (Auto) the first of the Mac's preferred languages whose .bin is in the player's
// LANGUAGES folder; else English US (today's fixed value) when English.bin is there, else the first pack present. The row
// is listed only when the folder has more than one pack, so an English-only install shows no new row.
// BEGIN offline gamelang: gamelang_test.py compiles this block in a scratch harness.
typedef struct { const char *reg, *bin, *iso, *label; } GameLang;
static const GameLang kGameLangs[] = {
    {"English US", "English", "en", "English"},
    {"French", "French", "fr", "French (Français)"},
    {"German", "German", "de", "German (Deutsch)"},
    {"Italian", "Italian", "it", "Italian (Italiano)"},
    {"Spanish", "Spanish", "es", "Spanish (Español)"},
    {"Dutch", "Dutch", "nl", "Dutch (Nederlands)"},
    {"Swedish", "Swedish", "sv", "Swedish (Svenska)"},
    {"Danish", "Danish", "da", "Danish (Dansk)"},
    {"Polish", "Polish", "pl", "Polish (Polski)"},
    {"Finnish", "Finnish", "fi", "Finnish (Suomi)"},
};
#define NGAMELANG (int)(sizeof kGameLangs / sizeof *kGameLangs)
// Bit i: LANGUAGES/<bin>.bin (any case, non-empty) is in the game folder.
static unsigned gamelang_packs(const char *dir) {
    unsigned packs = 0;
    DIR *top = dir ? opendir(dir) : NULL;
    struct dirent *e;
    char path[2048] = "";
    while (top && (e = readdir(top))) if (!strcasecmp(e->d_name, "LANGUAGES")) { snprintf(path, sizeof path, "%s/%s", dir, e->d_name); break; }
    if (top) closedir(top);
    DIR *d = path[0] ? opendir(path) : NULL;
    while (d && (e = readdir(d))) {
        size_t n = strlen(e->d_name);
        if (n < 5 || strcasecmp(e->d_name + n - 4, ".bin")) continue;
        for (int i = 0; i < NGAMELANG; ++i) {
            if (strlen(kGameLangs[i].bin) != n - 4 || strncasecmp(e->d_name, kGameLangs[i].bin, n - 4)) continue;
            char f[2400];
            struct stat st;
            snprintf(f, sizeof f, "%s/%s", path, e->d_name);
            if (!stat(f, &st) && S_ISREG(st.st_mode) && st.st_size > 0) packs |= 1u << i;
        }
    }
    if (d) closedir(d);
    return packs;
}
// The language to use: `choice` is a row value ("auto" or a registry name), `mac` the Mac's preferred languages in order
// ("de-DE", "en-US", "zh-Hans"). Returns an index into kGameLangs.
static int gamelang_pick(unsigned packs, const char *const *mac, int nmac, const char *choice) {
    for (int i = 0; choice && strcasecmp(choice, "auto") && i < NGAMELANG; ++i)
        if ((packs >> i & 1) && !strcasecmp(choice, kGameLangs[i].reg)) return i;
    for (int m = 0; m < nmac; ++m) {
        size_t n = mac[m] ? strcspn(mac[m], "-_") : 0;
        for (int i = 0; n && i < NGAMELANG; ++i)
            if ((packs >> i & 1) && strlen(kGameLangs[i].iso) == n && !strncasecmp(mac[m], kGameLangs[i].iso, n)) return i;
    }
    if (packs & 1 || !packs) return 0;          // English US, as before (also when no LANGUAGES folder was found)
    for (int i = 0; i < NGAMELANG; ++i) if (packs >> i & 1) return i;
    return 0;
}
static unsigned lang_packs;
static const char *lang_choices[NGAMELANG + 1], *lang_values[NGAMELANG + 1];
static Setting s_language = {.id = "gameplay.language", .category = SET_GAMEPLAY, .order = 90, .label = "Language",
    .help = "The game's text and menus, from the language files in your game folder; Auto follows your Mac's language when the game has it, else English. Speech stays as installed.",
    .type = SET_CHOICE, .choices = lang_choices, .values = lang_values, .def = 0, .env = "NFS_LANGUAGE", .pref = "game_language",
    .own_pref = 1, .restart = 1};
// boot.c main, right after settings_init: find the packs and list the row when there is a choice to make.
void settings_language_setup(const char *dir) {
    lang_packs = gamelang_packs(dir);
    int n = 0;
    lang_choices[n] = "Auto"; lang_values[n++] = "auto";
    for (int i = 0; i < NGAMELANG; ++i) if (lang_packs >> i & 1) { lang_choices[n] = kGameLangs[i].label; lang_values[n++] = kGameLangs[i].reg; }
    s_language.nchoices = n;
    if (n > 2) settings_add(&s_language);       // more than one pack
}
// END offline gamelang
// boot.c reg_load: the registry's Language value for this run (registry names: "English US", "German", ...).
const char *settings_game_language(void) {
    Setting *s = settings_find("gameplay.language");
    const char *choice = s && s->boot_value > 0 && s->boot_value < s->nchoices ? lang_values[s->boot_value] : "auto";
    const char *mac[8];
    char buf[8][32];
    int nmac = 0;
    CFArrayRef prefs = CFLocaleCopyPreferredLanguages();
    for (CFIndex i = 0; prefs && i < CFArrayGetCount(prefs) && nmac < 8; ++i) {
        CFStringRef str = CFArrayGetValueAtIndex(prefs, i);
        if (str && CFStringGetCString(str, buf[nmac], sizeof buf[nmac], kCFStringEncodingUTF8)) { mac[nmac] = buf[nmac]; ++nmac; }
    }
    if (prefs) CFRelease(prefs);
    int k = gamelang_pick(lang_packs, mac, nmac, choice);
    fprintf(stderr, "boot: language %s (%s; Mac %s; packs %#x)\n", kGameLangs[k].reg, strcmp(choice, "auto") ? "F10 choice" : "Auto",
            nmac ? mac[0] : "?", lang_packs);
    return kGameLangs[k].reg;
}

__attribute__((constructor)) static void settings_port_register(void) {
#if !defined(NFS_PUBLIC) || !NFS_PUBLIC
    register_private_car_settings();
#endif
    {   // mark the X360's mirror brightness (default.xex, X360E_MIRROR_WORLD) among the 10 % steps
        static char x360_label[24];
        int k = (int)lroundf(X360E_MIRROR_WORLD * 10.0f) - 5;
        if (X360_ENGINE_CONSTS && k >= 0 && k < 16) {
            snprintf(x360_label, sizeof x360_label, "%s (X360)", kMirrorExposure[k]);
            kMirrorExposure[k] = x360_label;
        }
    }
    REG(s_look); REG(h_out); REG(h_x360snd); REG(h_pad); REG(h_rumble); REG(h_steer); REG(h_wl); REG(h_wsurf); REG(h_wdet); REG(h_career); REG(h_cheats);
    REG(s_window); REG(s_overlay); REG(s_lan);
    REG(s_hdr); REG(s_hdr_status); REG(s_framelimit);
    REG(s_vrr);
    REG(s_postfx); REG(s_speedblur); REG(s_glow); REG(s_bloomsurf); REG(s_high); REG(s_band); REG(s_tone); REG(s_paint); REG(s_glass); REG(s_carnm); REG(s_flake); REG(s_grass); REG(s_trees); REG(s_water); REG(s_bglass); REG(s_aux); REG(s_particles); REG(s_worldlight); REG(s_refl); REG(s_ripples); REG(s_parallax); REG(s_drops); REG(s_mirror); REG(s_mirrorsize); REG(s_mirrordetail); REG(s_mirrorview); REG(s_mirrorfog); REG(s_mirrormsaa); REG(s_detail); REG(s_stream); REG(s_hdtex); REG(s_uitex); REG(s_fit);
    REG(s_audio); REG(s_limiter); REG(s_speaker_layout); REG(s_engine_sound); REG(s_audio_now);
    REG(s_menubtn); REG(s_layout); REG(s_prompts); REG(s_pads); REG(s_rumble); REG(s_rumble_surfaces); REG(s_rumble_props); REG(s_triggers); REG(s_light);
    REG(h_perf); REG(s_perf); REG(s_audio_now2);
    REG(h_report); REG(s_cat); REG(s_note); REG(s_save); REG(s_open);
    REG(h_log); REG(s_full); REG(s_logs); REG(s_quit); REG(s_restart);
}
