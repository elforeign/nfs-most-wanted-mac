// settings_look_test.c - offline test of settings.c's F10 menu logic (never starts the game):
//   cc -std=c11 -Wall -Wextra -Werror -g -fsanitize=address,undefined -I ../recomp runtime/settings_look_test.c runtime/settings.c -o /tmp/slt && /tmp/slt
// Checks: (1) menu order is the explicit order key (rows registered in reverse come out sorted, ties by id), (2) an old
// app.txt loads to the same values (legacy spellings, audio toggles now shown PC / X360 but saved off / on), (3) Original PC /
// Xbox 360 set every row of the look table and the preset row follows, (4) an edit shows Custom and is kept as My picks,
// (5) My picks restores it, (6) rows needing a restart report as such. Result: PASS, 0 failures. Broken run (settings.c without the
// look_row_edited call, i.e. no Custom / My picks on an edit): FAIL, 4 failures (lines 112, 114, 119, 120).
// Defaults: the rows carry the shipped defaults; with water left at its old default 0: FAIL, 1 failure (line 96); runtime/defaults_check.py
// reads the real Setting rows (broken water row: FAIL 1 wrong).
// graphics.trees row (R28): PASS; settings.c without its kLook entry: FAIL, 1 failure (line 111, Xbox 360 leaves it PC).
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "settings.h"

int diag_on = 0;
const char *game_dir;
static char store[64][2][1100];
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
    .choices = (const char *const[]){"Original PC", "Xbox 360", "My picks", "Custom"}, .nchoices = 4, .def = PRESET_CUSTOM, .pref = "",
    .apply = (void (*)(int))NULL, .get = settings_look_state};
static void look_apply(int v) { settings_look_apply(v); }
// a port extra outside the look table, and the audio toggle with its legacy spellings
static Setting r_bloom = {.id = "graphics.bloom_surfaces", .category = SET_PICTURE, .order = 50, .label = "Bloom style", .type = SET_CHOICE,
    .choices = (const char *const[]){"Glow", "Halo", "Natural"}, .values = (const char *const[]){"glow", "halo", "natural"}, .nchoices = 3, .def = 2};
static Setting a_music = {.id = "audio.x360_music", .category = SET_AUDIO, .order = 70, .label = "Music", .type = SET_TOGGLE,
    .choices = kPcX, .values = (const char *const[]){"off", "on"}, .nchoices = 2, .def = 1, .restart = 1};
static Setting a_mix = {.id = "audio.x360_mix", .category = SET_AUDIO, .order = 60, .label = "Sound mix", .type = SET_TOGGLE,
    .choices = kPcX, .values = (const char *const[]){"off", "on"}, .nchoices = 2, .def = 1, .restart = 1};
static Setting a_head = {.id = "audio.h_x360", .category = SET_AUDIO, .order = 50, .label = "Xbox 360 sound", .type = SET_HEADING};

static Setting *const all_rows[] = {&r_postfx, &r_tone, &r_paint, &r_cglass, &r_grass, &r_trees, &r_water, &r_bglass, &r_aux, &r_par, &r_ldata, &r_refl, &r_ripples,
                                    &r_drops, &r_blur, &r_fit, &r_hd, &r_ui, &r_mirror, &r_arrest};
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
int main(void) {
    r_look.apply = look_apply;
    // the old file: saved values in the spellings the menu always wrote
    app_pref_set("graphics.tone_curve", "x360"); app_pref_set("audio.x360_music", "off"); app_pref_set("graphics.raindrops", "pc_before"); app_pref_set("graphics.trees", "pc");
    // registered in reverse, so only the order keys (not registration) can produce the menu order
    for (int i = NROWS - 1; i >= 0; --i) settings_add(all_rows[i]);
    settings_add(&a_head); settings_add(&a_music); settings_add(&a_mix); settings_add(&r_bloom); settings_add(&r_look);
    settings_init();
    CHECK(settings_get("graphics.tone_curve") == 1 && settings_get("audio.x360_music") == 0 && settings_get("graphics.raindrops") == 2);
    CHECK(settings_get("audio.x360_mix") == 1 && a_music.saved && !a_mix.saved);
    // shipped defaults: untouched rows start X360 everywhere but the tone curve (PC)
    CHECK(settings_get("graphics.water") == 1 && settings_get("graphics.car_glass") == 1 && settings_get("graphics.parallax") == 0);
    CHECK(settings_get("graphics.x360_arrest") == 1 && settings_get("graphics.lighting_data") == 1 && settings_get("graphics.scenery_lights") == 1);
    CHECK(settings_get("graphics.trees") == 0 && r_trees.saved);                // a saved PC choice still beats the new X360 default
    CHECK(settings_get("graphics.look_preset") == PRESET_CUSTOM);                // picks are neither preset (tone curve PC, HD textures, mirror 100 %)
    Setting *r[64];
    int n = settings_rows(SET_AUDIO, r, 64);
    CHECK(n == 3 && r[0] == &a_head && r[1] == &a_mix && r[2] == &a_music);
    n = settings_rows(SET_PICTURE, r, 64);
    CHECK(n == NROWS + 2 && r[0] == &r_look && r[1] == &r_bloom);
    for (int i = 2; i + 1 < n; ++i) CHECK(strcmp(r[i]->id, r[i + 1]->id) <= 0);   // all order 0: ties by id
    // the saved audio toggle reads back as before and the legacy switch spellings still parse
    char b[64];
    CHECK(!strcmp(settings_value_name(&a_music, 0, b, sizeof b), "PC") && !strcmp(settings_value_name(&a_music, 1, b, sizeof b), "X360"));
    CHECK(settings_set(&a_music, 1) && !strcmp(app_pref("audio.x360_music"), "on"));
    // 1: this mixed state is Custom
    CHECK(settings_get("graphics.look_preset") == PRESET_CUSTOM);
    // 2: Original PC
    static const char *const pc[] = {"pc", "pc", "pc", "pc", "pc", "pc", "pc", "pc", "pc", "pc", "pc", "pc", "pc", "pc_before", "off", "pc", "off", "off", "1", "pc"};
    static const char *const x3[] = {"x360", "x360", "x360", "x360", "x360", "x360", "x360", "x360", "x360", "x360", "x360", "x360", "x360", "x360", "x360", "x360", "on", "on", "1.4", "x360"};
    CHECK(settings_set(&r_look, PRESET_PC));
    CHECK(batch_ends >= 1 && batch_depth == 0);       // audit4 #7: fails (0 ends) with settings.c without the batch calls
    CHECK(all_are(pc) && settings_get("graphics.look_preset") == PRESET_PC);
    CHECK(settings_get("graphics.bloom_surfaces") == 2);                        // port extra untouched
    // 3: Xbox 360, and the restart rows it changed are reported
    CHECK(settings_set(&r_look, PRESET_X360));
    CHECK(all_are(x3) && settings_get("graphics.look_preset") == PRESET_X360);
    r_ldata.boot_value = 0; r_hd.boot_value = 0; r_ui.boot_value = 0; r_arrest.boot_value = 0;   // as booted with PC (arrest and lighting data now default X360): three restart rows will move
    CHECK(settings_look_apply(PRESET_PC) == 2);                                  // back to PC: hd/ui differ from their boot value (X360), lighting data is back at its boot value
    // 4: an edit shows Custom and is kept
    CHECK(settings_set(&r_tone, 1));                                             // tone curve -> X360 on top of Original PC
    CHECK(settings_get("graphics.look_preset") == PRESET_CUSTOM);                // edit shows Custom
    CHECK(app_pref("graphics.my_picks") && strstr(app_pref("graphics.my_picks"), "graphics.tone_curve=x360"));
    CHECK(settings_set(&r_bloom, 0) && settings_get("graphics.look_preset") == PRESET_CUSTOM);   // an extra: no change to the state
    // 5: Xbox 360, then My picks restores the edited state exactly
    CHECK(settings_set(&r_look, PRESET_X360) && settings_get("graphics.look_preset") == PRESET_X360);
    CHECK(settings_set(&r_look, PRESET_MINE) && settings_get("graphics.look_preset") == PRESET_MINE);
    static const char *const mine[] = {"pc", "x360", "pc", "pc", "pc", "pc", "pc", "pc", "pc", "pc", "pc", "pc", "pc", "pc_before", "off", "pc", "off", "off", "1", "pc"};
    CHECK(all_are(mine));
    CHECK(settings_set(&r_water, 1) && settings_get("graphics.look_preset") == PRESET_CUSTOM);
    // a switch-locked row is left alone by presets
    r_grass.locked = 1;
    int g = settings_get("graphics.grass");
    settings_look_apply(PRESET_X360);
    CHECK(settings_get("graphics.grass") == g);
    printf("settings_look_test: %s (%d failures)\n", failures ? "FAIL" : "PASS", failures);
    return failures != 0;
}
