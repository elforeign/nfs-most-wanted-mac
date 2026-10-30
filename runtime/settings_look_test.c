// settings_look_test.c - offline F10 look tests, no game/GPU.
// clang -std=c11 -Wall -Wextra -Werror -g -fsanitize=address,undefined -I recomp runtime/settings_look_test.c runtime/settings.c -o /tmp/slt && /tmp/slt
// PASS: 0 failures, including fresh-process preference reload, every affected extra,
// My picks beyond 1 KB, Port defaults, legacy spellings, locked overrides and menu order.
// Broken controls: missing Colour filter in preset FAIL (8 checks); missing automatic
// My picks snapshot FAIL (26 checks); suppressed restore under an override FAIL
// (3 checks), versus production 0. builtin_features_test.py runs all three mutants.
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <spawn.h>
#include <sys/wait.h>
#include <unistd.h>
extern char **environ;
#include "settings.h"
#include "roots.h"

int diag_on = 0;
const char *game_dir;
static char store[256][2][1024];
static int nstore;
const char *app_pref(const char *key) { for (int i = 0; i < nstore; ++i) if (!strcmp(store[i][0], key)) return store[i][1]; return NULL; }
void app_pref_set(const char *key, const char *value) {
    for (int i = 0; i < nstore; ++i) if (!strcmp(store[i][0], key)) { snprintf(store[i][1], sizeof store[i][1], "%s", value); return; }
    snprintf(store[nstore][0], sizeof store[0][0], "%s", key); snprintf(store[nstore++][1], sizeof store[0][1], "%s", value);
}
static int batch_depth, batch_ends;
void app_pref_batch_begin(void) { ++batch_depth; }
void app_pref_batch_end(void) { --batch_depth; ++batch_ends; }   // audit4 #7: a preset's rows are one app.txt write
void report_note(const char *fmt, ...) { (void)fmt; }
void diag_event(const char *fmt, ...) { (void)fmt; }
static int failures;
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); ++failures; } } while (0)

static const char *const kPcX[] = {"PC", "X360"}, *const kPcXV[] = {"pc", "x360"};
static const char *const kXPc[] = {"X360", "PC"}, *const kXPcV[] = {"x360", "pc"};
static const char *const kDrops[] = {"X360", "PC", "PC, before post"}, *const kDropsV[] = {"x360", "pc", "pc_before"};
static const char *const kBlur[] = {"Off", "X360", "Strong"}, *const kBlurV[] = {"off", "x360", "strong"};
static const char *const kOnOff[] = {"X360", "PC"}, *const kOnOffV[] = {"on", "off"};
static const char *const kMirror[] = {"100%", "140%"}, *const kMirrorV[] = {"1", "1.4"};
#define ROW(var, id_, ch, vals, n, restart_, def_) static Setting var = {.id = id_, .category = SET_PICTURE, .order = 100, .label = id_, .type = SET_CHOICE, \
    .choices = ch, .values = vals, .nchoices = n, .def = def_, .restart = restart_}
ROW(r_postfx, "graphics.postfx", kXPc, kXPcV, 2, 0, 0);          // default X360 (index 0), as in the game
ROW(r_tone, "graphics.tone_curve", kPcX, kPcXV, 2, 0, 0);
ROW(r_paint, "graphics.car_paint", kXPc, kXPcV, 2, 0, 0);
ROW(r_cglass, "graphics.car_glass", kPcX, kPcXV, 2, 0, 1);
ROW(r_normals, "graphics.car_normals", kPcX, kPcXV, 2, 0, 1);
ROW(r_flake, "graphics.car_flake", kPcX, kPcXV, 2, 0, 1);
ROW(r_grass, "graphics.grass", kPcX, kPcXV, 2, 0, 1);
ROW(r_trees, "graphics.trees", kPcX, kPcXV, 2, 0, 1);
ROW(r_water, "graphics.water", kPcX, kPcXV, 2, 0, 1);
ROW(r_bglass, "graphics.building_glass", kPcX, kPcXV, 2, 0, 1);
ROW(r_ldata, "graphics.lighting_data", kPcX, kPcXV, 2, 1, 1);   // restart
ROW(r_par, "graphics.parallax", kXPc, kXPcV, 2, 0, 0);
ROW(r_aux, "graphics.scenery_lights", kPcX, kPcXV, 2, 0, 1);
ROW(r_refl, "graphics.road_reflection", kXPc, kXPcV, 2, 0, 0);
ROW(r_ripples, "graphics.rain_ripples", kXPc, kXPcV, 2, 0, 0);
ROW(r_drops, "graphics.raindrops", kDrops, kDropsV, 3, 0, 0);
ROW(r_blur, "graphics.speed_blur", kBlur, kBlurV, 3, 0, 1);
ROW(r_fit, "graphics.shadow_fit", kXPc, kXPcV, 2, 0, 0);
ROW(r_hd, "graphics.hd_textures", kOnOff, kOnOffV, 2, 1, 0);    // restart
ROW(r_ui, "graphics.ui_textures", kOnOff, kOnOffV, 2, 1, 0);    // restart
ROW(r_mirror, "graphics.mirror_exposure", kMirror, kMirrorV, 2, 0, 0);
ROW(r_arrest, "graphics.x360_arrest", kPcX, kPcXV, 2, 1, 1);      // restart
static Setting r_look = {.id = "graphics.look_preset", .category = SET_PICTURE, .order = 10, .label = "Look preset", .type = SET_CHOICE,
    .choices = (const char *const[]){"Port (recommended)", "Xbox 360 retail", "PC retail", "My picks"}, .nchoices = 4, .def = PRESET_PORT, .pref = "",
    .apply = (void (*)(int))NULL, .get = settings_look_state};
static void look_apply(int v) { settings_look_apply(v); }
// Picture rows outside the preset table must survive My picks, including own-pref getters.
static Setting r_brightness = {.id="graphics.brightness", .category=SET_PICTURE, .order=20,
    .type=SET_SLIDER, .min=0, .max=100, .step=1, .def=12};
static int aa_value = 2;
static int aa_get(void) { return aa_value; }
static void aa_apply(int v) { aa_value = v; }
static Setting r_aa = {.id="graphics.antialiasing", .category=SET_PICTURE, .order=30,
    .type=SET_CHOICE, .choices=(const char *const[]){"Off","2x","4x","8x"}, .nchoices=4,
    .def=2, .pref="", .own_pref=1, .get=aa_get, .apply=aa_apply};
// Bloom is now part of the look table; audio keeps its legacy spellings.
static Setting r_bloom = {.id = "graphics.bloom_surfaces", .category = SET_PICTURE, .order = 50, .label = "Bloom style", .type = SET_CHOICE,
    .choices = (const char *const[]){"Glow", "Halo", "Natural"}, .values = (const char *const[]){"glow", "halo", "natural"}, .nchoices = 3, .def = 2};
// Extras have deliberately different starting values, so retail cannot pass by leaving them alone.
#define EXTRA(id_, ...) {.id=id_, .category=SET_WORLD, .order=100, __VA_ARGS__}
#define CH2(a,b) .type=SET_CHOICE, .choices=(const char *const[]){a,b}, .values=(const char *const[]){a,b}, .nchoices=2
static Setting extras[] = {
    EXTRA("graphics.look_data", CH2("pc","x360"), .hidden=1, .restart=1, .env="NFS_VISUALLOOK"),
    EXTRA("graphics.colour_filter", .type=SET_SLIDER, .min=0, .max=100, .step=10),
    EXTRA("graphics.bloom", .type=SET_SLIDER, .min=0, .max=200, .step=10, .def=100),
    EXTRA("graphics.world_lighting", CH2("neutral","original")),
    EXTRA("graphics.highlights", CH2("rolloff","clip")),
    EXTRA("graphics.deband", CH2("smooth","off")),
    EXTRA("graphics.hdr_output", .type=SET_TOGGLE, .restart=1),
    EXTRA("graphics.lamp_glow", .type=SET_TOGGLE),
    EXTRA("graphics.headlamp_glass", CH2("retail","clear")),
    EXTRA("graphics.road_film", CH2("0","1")),
    EXTRA("graphics.car_reflection", CH2("pc","sharp"), .def=1, .restart=1),
    EXTRA("graphics.look_reflections", CH2("pc","sharp"), .def=1, .restart=1, .hidden=1),
    EXTRA("graphics.look_contact_shadows", .type=SET_TOGGLE, .def=1, .restart=1, .hidden=1, .env="NFS_SSAO"),
    EXTRA("graphics.look_soft_smoke", .type=SET_TOGGLE, .def=1, .restart=1, .hidden=1, .env="NFS_SOFT_SMOKE"),
    EXTRA("controls.chase_camera", .type=SET_TOGGLE),
    EXTRA("graphics.curve_glow", CH2("x360","tight")),
    EXTRA("graphics.mirror_size", CH2("pc","100"), .def=1),
    EXTRA("graphics.mirror_detail", CH2("pc","full"), .def=1),
    EXTRA("graphics.mirror_view", CH2("pc","60"), .def=1),
    EXTRA("graphics.mirror_fog", .type=SET_TOGGLE, .def=1),
    EXTRA("graphics.mirror_msaa", .type=SET_TOGGLE, .def=1),
    EXTRA("graphics.look_mirror_art", CH2("pc","smooth"), .def=1, .restart=1, .hidden=1),
    EXTRA("graphics.look_fog", CH2("pc","x360"), .def=1, .restart=1, .hidden=1),
    EXTRA("graphics.detail_distance", CH2("pc","2")),
    EXTRA("graphics.world_streaming", CH2("ahead","pc")),
    EXTRA("graphics.tunnel_shadows", CH2("pc","keep"), .def=1),
    EXTRA("cars.later_games", .type=SET_TOGGLE, .def=1, .restart=1),
    EXTRA("graphics.look_later_textures", .type=SET_TOGGLE, .def=1, .restart=1, .hidden=1),
};
#define NEXTRAS (int)(sizeof extras / sizeof *extras)
static Setting a_music = {.id = "audio.x360_music", .category = SET_AUDIO, .order = 70, .label = "Music", .type = SET_TOGGLE,
    .choices = kPcX, .values = (const char *const[]){"off", "on"}, .nchoices = 2, .def = 1, .restart = 1};
static Setting a_mix = {.id = "audio.x360_mix", .category = SET_AUDIO, .order = 60, .label = "Sound mix", .type = SET_TOGGLE,
    .choices = kPcX, .values = (const char *const[]){"off", "on"}, .nchoices = 2, .def = 1, .restart = 1};
static Setting a_head = {.id = "audio.h_x360", .category = SET_AUDIO, .order = 50, .label = "Xbox 360 sound", .type = SET_HEADING};

static Setting *const all_rows[] = {&r_postfx, &r_tone, &r_paint, &r_cglass, &r_grass, &r_trees, &r_water, &r_bglass, &r_aux, &r_par, &r_ldata, &r_refl, &r_ripples,
                                    &r_drops, &r_blur, &r_fit, &r_hd, &r_ui, &r_mirror, &r_arrest, &r_normals, &r_flake};
#define NROWS (int)(sizeof all_rows / sizeof *all_rows)
static int all_are(const char *const *spell /* per row, in all_rows order */) {
    for (int i = 0; i < NROWS; ++i) {
        char b[64];
        int v = settings_get(all_rows[i]->id);
        const char *vs = all_rows[i]->values[v];
        (void)b;
        if (strcmp(vs, spell[i])) { fprintf(stderr, "  %s = %s, want %s\n", all_rows[i]->id, vs, spell[i]); return 0; }
    }
    return 1;
}
int main(int argc, char **argv) {
    if (!X360_SHADER_LITS || !X360_ENGINE_CONSTS) {
        puts("SKIP: settings_look needs generated X360 shader/engine constants");
        return 3;
    }
    int legacy = argc == 3 && !strcmp(argv[1], "--legacy-custom");
    int reload = argc == 3 && (!strcmp(argv[1], "--reload") || legacy);
    if (reload) {
        FILE *f = fopen(argv[2], "r");
        CHECK(f != NULL);
        char line[2200];
        while (f && fgets(line, sizeof line, f)) {
            line[strcspn(line, "\n")] = 0;
            char *eq = strchr(line, '=');
            if (eq) { *eq++ = 0; app_pref_set(line, eq); }
        }
        if (f) fclose(f);
    }

    if (legacy) app_pref_set("graphics.look_choice", "custom");
    r_look.apply = look_apply;
    // the old file: saved values in the spellings the menu always wrote
    if (!reload) { app_pref_set("graphics.tone_curve", "x360"); app_pref_set("audio.x360_music", "off"); app_pref_set("graphics.raindrops", "pc_before"); app_pref_set("graphics.trees", "pc"); }
    // registered in reverse, so only the order keys (not registration) can produce the menu order
    for (int i = NROWS - 1; i >= 0; --i) settings_add(all_rows[i]);
    settings_add(&a_head); settings_add(&a_music); settings_add(&a_mix); settings_add(&r_bloom); settings_add(&r_look); settings_add(&r_brightness); settings_add(&r_aa);
    for (int i=0;i<NEXTRAS;++i) settings_add(&extras[i]);
    settings_init();
    if (reload) {
        CHECK(settings_look_state() == (legacy ? PRESET_MINE : PRESET_X360));
        CHECK(!strcmp(app_pref("graphics.look_choice"), legacy ? "mine" : "x360"));
        CHECK(settings_get("graphics.colour_filter") == 100);
        CHECK(settings_get("graphics.look_data") == 1);
        CHECK(!strcmp(getenv("NFS_VISUALLOOK"), "x360"));
        CHECK(!strcmp(getenv("NFS_SSAO"), "off"));
        CHECK(!strcmp(getenv("NFS_SOFT_SMOKE"), "off"));
        CHECK(settings_get("graphics.bloom_surfaces") == 0);
        if (!legacy) {
            settings_look_apply(PRESET_MINE);
            CHECK(settings_look_state() == PRESET_MINE);
            CHECK(settings_get("graphics.colour_filter") == 30);
            CHECK(settings_get("graphics.brightness") == 37);
            CHECK(settings_get("graphics.antialiasing") == 3);
            CHECK(settings_get("graphics.bloom_surfaces") == 2);
        }
        printf("settings_look restart: %s (%d failures)\n", failures ? "FAIL" : "PASS", failures);
        return failures != 0;
    }
    CHECK(settings_get("graphics.tone_curve") == 1 && settings_get("audio.x360_music") == 0 && settings_get("graphics.raindrops") == 2);
    CHECK(settings_get("audio.x360_mix") == 1 && a_music.saved && !a_mix.saved);
    CHECK(settings_get("graphics.headlamp_glass") == 0);
    // shipped defaults: untouched rows start X360 everywhere but the tone curve (PC)
    CHECK(settings_get("graphics.water") == 1 && settings_get("graphics.car_glass") == 1 && settings_get("graphics.parallax") == 0);
    CHECK(settings_get("graphics.x360_arrest") == 1 && settings_get("graphics.lighting_data") == 1 && settings_get("graphics.scenery_lights") == 1);
    CHECK(settings_get("graphics.trees") == 0 && r_trees.saved);                // a saved PC choice still beats the new X360 default
    CHECK(settings_get("graphics.look_preset") == PRESET_MINE);                // picks are neither preset (tone curve PC, HD textures, mirror 100 %)
    Setting *r[64];
    int n = settings_rows(SET_AUDIO, r, 64);
    CHECK(n == 3 && r[0] == &a_head && r[1] == &a_mix && r[2] == &a_music);
    n = settings_rows(SET_PICTURE, r, 64);
    CHECK(n == NROWS + 4 && r[0] == &r_look && r[1] == &r_brightness && r[2] == &r_aa && r[3] == &r_bloom);
    for (int i = 4; i + 1 < n; ++i) CHECK(strcmp(r[i]->id, r[i + 1]->id) <= 0);   // all order 100: ties by id
    // the saved audio toggle reads back as before and the legacy switch spellings still parse
    char b[64];
    CHECK(!strcmp(settings_value_name(&a_music, 0, b, sizeof b), "PC") && !strcmp(settings_value_name(&a_music, 1, b, sizeof b), "X360"));
    CHECK(settings_set(&a_music, 1) && !strcmp(app_pref("audio.x360_music"), "on"));
    // 1: this mixed state is My picks
    CHECK(settings_get("graphics.look_preset") == PRESET_MINE);
    // 2: PC retail
    static const char *const pc[] = {"pc", "pc", "pc", "pc", "pc", "pc", "pc", "pc", "pc", "pc", "pc", "pc", "pc", "pc_before", "off", "pc", "off", "off", "1", "pc", "pc", "pc"};
    static const char *const x3[] = {"x360", "x360", "x360", "x360", "x360", "x360", "x360", "x360", "x360", "x360", "x360", "x360", "x360", "x360", "x360", "x360", "on", "on", "1.4", "x360", "x360", "x360"};
    CHECK(settings_set(&r_look, PRESET_PC));
    CHECK(batch_ends >= 1 && batch_depth == 0);       // audit4 #7: fails (0 ends) with settings.c without the batch calls
    CHECK(all_are(pc) && settings_get("graphics.look_preset") == PRESET_PC);
    CHECK(settings_get("graphics.bloom_surfaces") == 0 && settings_get("graphics.colour_filter") == 100);
    // 3: Xbox 360, and the restart rows it changed are reported
    CHECK(settings_set(&r_look, PRESET_X360));
    CHECK(all_are(x3) && settings_get("graphics.look_preset") == PRESET_X360);
    r_ldata.boot_value = 0; r_hd.boot_value = 0; r_ui.boot_value = 0; r_arrest.boot_value = 0;   // lighting/arrest booted PC; texture rows booted X360
    CHECK(settings_look_apply(PRESET_PC) >= 2);                                  // back to PC: hd/ui differ from their boot value (X360), lighting data is back at its boot value
    // 4: an edit shows My picks and is kept
    CHECK(settings_set(&r_tone, 1));                                             // tone curve -> X360 on top of PC retail
    CHECK(settings_get("graphics.look_preset") == PRESET_MINE);                // edit shows My picks
    CHECK(app_pref("graphics.my_picks") && strstr(app_pref("graphics.my_picks"), "graphics.tone_curve=x360"));
    CHECK(settings_set(&r_bloom, 2) && settings_get("graphics.look_preset") == PRESET_MINE);   // retail extra participates in My picks
    // 5: Xbox 360, then My picks restores the edited state exactly
    CHECK(settings_set(&r_look, PRESET_X360) && settings_get("graphics.look_preset") == PRESET_X360);
    CHECK(settings_set(&r_look, PRESET_MINE) && settings_get("graphics.look_preset") == PRESET_MINE);
    static const char *const mine[] = {"pc", "x360", "pc", "pc", "pc", "pc", "pc", "pc", "pc", "pc", "pc", "pc", "pc", "pc_before", "off", "pc", "off", "off", "1", "pc", "pc", "pc"};
    CHECK(all_are(mine));
    CHECK(settings_set(&r_water, 1) && settings_get("graphics.look_preset") == PRESET_MINE);
    CHECK(settings_get("graphics.bloom_surfaces") == 2);
    CHECK(app_pref("graphics.my_picks.2") && *app_pref("graphics.my_picks.2")); // >1 KB, no truncation
    // Every affected extra participates in matching, automatic saving and restoration.
    for (int i=0;i<NEXTRAS;++i) {
        settings_look_apply(PRESET_X360);
        Setting *s = &extras[i];
        int target = s->value;
        CHECK(settings_set(s, s->type == SET_SLIDER ? (target ? 0 : 100) : 1-target));
        int custom = s->value;
        CHECK(settings_look_state() == PRESET_MINE);
        settings_look_apply(PRESET_PC);
        settings_look_apply(PRESET_MINE);
        CHECK(s->value == custom && settings_look_state() == PRESET_MINE);
    }
    settings_look_apply(PRESET_PORT);
    CHECK(settings_look_state() == PRESET_PORT);
    for (int i=0;i<NEXTRAS;++i) CHECK(extras[i].value == extras[i].def);
    CHECK(settings_get("graphics.bloom_surfaces") == 2);
    CHECK(settings_get("audio.x360_music") == 1); // sound kept through all looks
    // A value changed elsewhere must invalidate the cached selection too.
    settings_look_apply(PRESET_X360);
    extras[1].value = 0;
    CHECK(settings_look_state() == PRESET_MINE);
    settings_look_apply(PRESET_X360);
    // Every Picture edit immediately selects/saves My picks. Re-selecting it must
    // restore both prior edits even when they were made while My picks was selected.
    CHECK(settings_set(&r_brightness, 37));
    CHECK(strstr(app_pref("graphics.my_picks"), "graphics.brightness=37") ||
          strstr(app_pref("graphics.my_picks.2"), "graphics.brightness=37"));
    CHECK(settings_look_state() == PRESET_MINE && !strcmp(app_pref("graphics.look_choice"), "mine"));
    CHECK(settings_set(&r_aa, 3));
    CHECK(settings_set(&extras[1], 30));
    CHECK(settings_set(&r_bloom, 2));
    CHECK(r_look.nchoices == 4);
    settings_look_apply(PRESET_PC);
    CHECK(settings_get("graphics.brightness") == 37 && settings_get("graphics.antialiasing") == 3); // presets preserve device choices
    settings_look_apply(PRESET_MINE);
    CHECK(settings_look_state() == PRESET_MINE && settings_get("graphics.colour_filter") == 30);
    CHECK(settings_get("graphics.brightness") == 37 && settings_get("graphics.antialiasing") == 3);
    CHECK(settings_get("graphics.bloom_surfaces") == 2);
    settings_look_apply(PRESET_X360);
    // Save the real preference spellings and reload them in a fresh process.
    char path[] = "/tmp/settings-look-XXXXXX";
    int fd = mkstemp(path);
    CHECK(fd >= 0);
    FILE *f = fd >= 0 ? fdopen(fd, "w") : NULL;
    if (f) {
        for (int i=0;i<nstore;++i) fprintf(f, "%s=%s\n", store[i][0], store[i][1]);
        fclose(f);
        pid_t child;
        char *args[] = {argv[0], "--reload", path, NULL};
        int rc = posix_spawn(&child, argv[0], NULL, NULL, args, environ), status = 1;
        CHECK(rc == 0);
        if (!rc) { CHECK(waitpid(child, &status, 0) == child); CHECK(WIFEXITED(status) && WEXITSTATUS(status) == 0); }
        args[1] = "--legacy-custom";
        rc = posix_spawn(&child, argv[0], NULL, NULL, args, environ); status = 1;
        CHECK(rc == 0);
        if (!rc) { CHECK(waitpid(child, &status, 0) == child); CHECK(WIFEXITED(status) && WEXITSTATUS(status) == 0); }
        unlink(path);
    }
    // a switch-locked row is left alone by presets
    r_grass.locked = 1;
    int g = settings_get("graphics.grass");
    settings_look_apply(PRESET_X360);
    CHECK(settings_get("graphics.grass") == g);
    // A conflicting locked override is My picks; it must not claim the full preset.
    r_grass.value = 0;
    settings_look_apply(PRESET_X360);
    CHECK(settings_look_state() == PRESET_MINE);
    CHECK(settings_look_choice() == PRESET_X360); // retain requested choice for cycling past a conflicting override
    // A partial preset under an environment override must not overwrite My picks.
    CHECK(settings_set(&r_look, PRESET_MINE)); // label already My picks; restore must still run
    r_grass.locked = 0;
    CHECK(settings_get("graphics.colour_filter") == 30);
    CHECK(settings_get("graphics.bloom_surfaces") == 2);
    printf("settings_look_test: %s (%d failures)\n", failures ? "FAIL" : "PASS", failures);
    return failures != 0;
}
