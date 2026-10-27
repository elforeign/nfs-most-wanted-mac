// settings.c - the settings registry (settings.h): values, app.txt, the NFS_* switches that override them.
#include <ctype.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

#include "host.h"
#include "settings.h"
#include "roots.h"

// X360 rows and the files they need (roots.h features). With a roots.txt that lacks them the row is hidden, locked and
// PC is in effect (also exported to its switch); with no roots.txt (the owner's layout) nothing changes.
static const struct { const char *id, *feature; } kNeedsX360[] = {
    {"audio.x360_mix", "audio"}, {"audio.x360_music", "music"}, {"audio.x360_reverb", "reverb"},
    {"audio.x360_copspeech", "police"}, {"audio.x360_nisaudio", "nis"},
    {"graphics.x360_arrest", "arrest"}, {"graphics.hd_textures", "textures"}, {"graphics.ui_textures", "ui"},
    {"graphics.car_glass", "world"}, {"graphics.car_normals", "world"}, {"graphics.car_flake", "world"},
    {"graphics.trees", "world"}, {"graphics.scenery_lights", "world"}, {"graphics.water", "world"},
    {"graphics.building_glass", "world"}, {"graphics.grass", "world"}, {"graphics.rain_ripples", "world"},
    {"graphics.raindrops", "world"}, {"graphics.lighting_data", "world"}, {"gameplay.x360_data", "world"},
    {"controls.control_type", "world"},   // the X360 Control Types (controltype_buttons.inc): Type 1, the PC bindings
    // X360 options built on the X360 shaders' constants (ZZDATA0.BIN) or the X360 engine's (default.xex): without them
    // the option would silently render PC, so the row is hidden
    {"graphics.car_paint", "shaders"}, {"graphics.car_normals", "shaders"}, {"graphics.car_flake", "shaders"},
    {"graphics.trees", "shaders"}, {"graphics.scenery_lights", "shaders"}, {"graphics.water", "shaders"},
    {"graphics.building_glass", "shaders"}, {"graphics.grass", "shaders"}, {"graphics.rain_ripples", "shaders"},
    {"graphics.parallax", "shaders"}, {"graphics.x360_arrest", "shaders"},
    {"graphics.postfx", "shaders"}, {"graphics.tone_curve", "shaders"}, {"graphics.speed_blur", "shaders"},
    {"graphics.curve_glow", "shaders"},
    {"graphics.postfx", "engine"}, {"graphics.tone_curve", "engine"}, {"graphics.speed_blur", "engine"},
    {"graphics.curve_glow", "engine"},
    {"graphics.particles", "shaders"}, {"graphics.building_glass", "engine"}, {"graphics.x360_arrest", "engine"},
    {"graphics.rain_ripples", "engine"}, {"graphics.raindrops", "shaders"}, {"graphics.raindrops", "engine"},
    {"graphics.shadow_fit", "engine"},
    // The half-screen road reflection is listed as an X360 choice: without X360 files the PC's 320x240 is in effect and
    // the row is not shown (M4 F6).
    {"graphics.road_reflection", "world"},
};
static int x360_unavailable(Setting *s) {
    for (unsigned i = 0; i < sizeof kNeedsX360 / sizeof *kNeedsX360; ++i)
        if (!strcmp(s->id, kNeedsX360[i].id) && !roots_x360_has(kNeedsX360[i].feature)) return 1;   // any missing: hidden
    return 0;
}
static int parse(const Setting *s, const char *str);
static void value_str(const Setting *s, int v, char *buf, int n);
static void x360_gate(Setting *s) {
    if (!x360_unavailable(s)) return;
    int v = parse(s, "pc");
    if (v < 0) v = parse(s, "off");
    if (v < 0) v = 0;
    s->value = s->boot_value = v;
    s->locked = s->hidden = 1;
    value_str(s, v, s->env_text, sizeof s->env_text);
    if (s->env && s->env_text[0]) setenv(s->env, s->env_text, 1);
}
const char *const settings_category_names[SET_NCATEGORIES] = {"Picture", "World", "Cars & cutscenes", "Mirror", "Display & performance", "Audio", "Controls", "Gameplay", "Diagnostics"};
static const char *const kOffOn[] = {"Off", "On"};

#define MAX_SETTINGS 128
static Setting *all[MAX_SETTINGS];
static int nall, inited;

static const char *const *labels(const Setting *s, int *n) {
    if (s->type == SET_TOGGLE && !s->choices) { *n = 2; return kOffOn; }
    *n = s->nchoices;
    return s->choices;
}
static const char *pref_key(const Setting *s) { return s->pref ? s->pref : s->id; }
// The saved / switch spelling of choice i.
static void value_str(const Setting *s, int v, char *buf, int n) {
    if (s->type == SET_SLIDER) { if (s->unit) snprintf(buf, (size_t)n, "%g", v * s->unit); else snprintf(buf, (size_t)n, "%d", v); return; }
    int nc;
    const char *const *l = labels(s, &nc);
    if (v < 0 || v >= nc) { buf[0] = 0; return; }
    if (s->values) { snprintf(buf, (size_t)n, "%s", s->values[v]); return; }
    snprintf(buf, (size_t)n, "%s", l[v]);
    for (char *p = buf; *p; ++p) *p = (char)tolower((unsigned char)*p);
}
// A saved or switch spelling -> value; -1 if it isn't one of them.
static int parse(const Setting *s, const char *str) {
    if (!str || !*str) return -1;
    char trimmed[1024];
    while (isspace((unsigned char)*str)) ++str;
    size_t len = strlen(str);
    while (len && isspace((unsigned char)str[len - 1])) --len;
    if (!len || len >= sizeof trimmed) return -1;
    memcpy(trimmed, str, len); trimmed[len] = 0; str = trimmed;
    if (s->type == SET_SLIDER) {
        char *end;
        double d = strtod(str, &end);
        if (end == str || *end || !isfinite(d)) return -1;
        if (s->unit) d /= s->unit;
        if (d <= s->min) return s->min;
        if (d >= s->max) return s->max;
        int v = (int)(d + (d < 0 ? -0.5 : 0.5));
        return v < s->min ? s->min : v > s->max ? s->max : v;
    }
    int nc;
    labels(s, &nc);
    char buf[64];
    for (int i = 0; i < nc; ++i) { value_str(s, i, buf, sizeof buf); if (!strcasecmp(buf, str)) return i; }
    if (s->type == SET_TOGGLE) {          // the usual spellings of a boolean switch
        if (!strcmp(str, "1") || !strcasecmp(str, "on") || !strcasecmp(str, "true")) return 1;
        if (!strcmp(str, "0") || !strcasecmp(str, "off") || !strcasecmp(str, "false")) return 0;
    }
    return -1;
}
static void look_row_edited(Setting *s);
static int has_value(const Setting *s) { return s->type == SET_TOGGLE || s->type == SET_CHOICE || s->type == SET_SLIDER; }

static void load(Setting *s) {
    s->value = s->boot_value = s->def;
    if (!has_value(s)) return;
    const char *e = s->env ? getenv(s->env) : NULL;
    if (e && *e) {
        s->locked = 1;
        snprintf(s->env_text, sizeof s->env_text, "%s", e);
        int v = parse(s, e);
        if (v >= 0) s->value = s->boot_value = v;
        if (s->live_from_default && s->boot_value != s->def) s->restart = 1;
        return;
    }
    const char *key = pref_key(s);
    int v = *key ? parse(s, app_pref(key)) : -1;
    if (v >= 0) { s->value = s->boot_value = v; s->saved = 1; }
    if (s->live_from_default && s->boot_value != s->def) s->restart = 1;
    if (s->env && !s->own_pref && s->value != s->def) {   // the switch carries it to the code that reads it at start-up
        char buf[64];
        value_str(s, s->value, buf, sizeof buf);
        if (buf[0]) setenv(s->env, buf, 1);
    }
}

void settings_add(Setting *s) {
    if (nall == MAX_SETTINGS || settings_find(s->id)) return;
    all[nall++] = s;
    if (inited) { load(s); x360_gate(s); }
}
// boot.c main, right after the game folder is known and before anything reads an NFS_* switch: settings registered
// by constructors are loaded now (app.txt is in USERDATA, which BOOT_USERDATA may move).
void settings_init(void) {
    if (inited) return;
    inited = 1;
    for (int i = 0; i < nall; ++i) { load(all[i]); x360_gate(all[i]); }
}
Setting *settings_find(const char *id) {
    for (int i = 0; i < nall; ++i) if (!strcmp(all[i]->id, id)) return all[i];
    return NULL;
}
int settings_count(void) { return nall; }
// A category's settings in menu order: the explicit `order` key, ties by id (never constructor link order).
int settings_rows(int cat, Setting **out, int cap) {
    int n = 0;
    for (int i = 0; i < nall && n < cap; ++i) {
        Setting *s = all[i];
        if (s->category != cat || s->hidden) continue;
        int j = n++;
        while (j > 0 && (out[j - 1]->order > s->order || (out[j - 1]->order == s->order && strcmp(out[j - 1]->id, s->id) > 0))) { out[j] = out[j - 1]; --j; }
        out[j] = s;
    }
    // A heading whose rows are all hidden (no X360 files) is not listed either: PC-only showed an empty "Xbox 360
    // sound" heading (M4 F6). Only a heading with hidden rows can go, so a layout with nothing hidden is unchanged.
    int m = 0;
    for (int i = 0; i < n; ++i) {
        Setting *h = out[i];
        if (h->type == SET_HEADING) {
            int next = i + 1;
            while (next < n && out[next]->type != SET_HEADING) ++next;
            int end = next < n ? out[next]->order : 1 << 30, visible = next - i - 1, hidden = 0;
            for (int k = 0; k < nall; ++k)
                if (all[k]->category == cat && all[k]->hidden && all[k]->type != SET_HEADING && all[k]->order >= h->order && all[k]->order < end) hidden++;
            if (!visible && hidden) continue;
        }
        out[m++] = h;
    }
    return m;
}
// BOOT_DIAG: the F10 list as the player sees it (hidden rows are named at the end): <diag>/settings_rows.txt.
void settings_dump(const char *path) {
    FILE *f = fopen(path, "w");
    if (!f) return;
    Setting *rows[128];
    for (int c = 0; c < SET_NCATEGORIES; ++c) {
        int n = settings_rows(c, rows, 128);
        for (int i = 0; i < n; ++i) {
            char v[64];
            fprintf(f, "%s\t%s\t%s\n", settings_category_names[c], rows[i]->id, has_value(rows[i]) ? settings_value_name(rows[i], rows[i]->value, v, sizeof v) : "");
        }
    }
    for (int i = 0; i < nall; ++i) if (all[i]->hidden) fprintf(f, "hidden\t%s\t(needs X360 files not in roots.txt; PC in effect)\n", all[i]->id);
    fclose(f);
}
Setting *settings_at(int i) { return i >= 0 && i < nall ? all[i] : NULL; }
static int current(const Setting *s) { return s->get ? s->get() : s->value; }
int settings_get(const char *id) { Setting *s = settings_find(id); return s ? current(s) : -1; }
const char *settings_text(const char *id) { Setting *s = settings_find(id); return s ? s->text : ""; }

int settings_set(Setting *s, int v) {
    if (!s || s->locked || !has_value(s)) return 0;
    if (s->type == SET_SLIDER) {
        if (s->step > 1) v = s->min + (v - s->min + s->step / 2) / s->step * s->step;
        v = v < s->min ? s->min : v > s->max ? s->max : v;
    } else {
        int nc;
        labels(s, &nc);
        if (v < 0 || v >= nc) return 0;
    }
    if (v == current(s)) return 0;
    s->value = v;
    if (s->apply) s->apply(v);
    const char *key = pref_key(s);
    if (*key) {
        char buf[64];
        value_str(s, v, buf, sizeof buf);
        app_pref_set(key, buf);
        s->saved = 1;
    }
    look_row_edited(s);
    fprintf(stderr, "boot: setting %s = %s%s\n", s->id, settings_value_name(s, v, (char[64]){0}, 64),
            s->restart && v != s->boot_value ? " (after a restart)" : "");
    void report_note(const char *fmt, ...);
    report_note("setting %s = %d", s->id, v);
    if (diag_on) diag_event("setting: %s = %d (%s%s; reads back %d)", s->id, v, s->apply ? "applied live" : "no live apply", s->restart && v != s->boot_value ? ", after a restart" : "", current(s));
    return 1;
}
void settings_reset(Setting *s) {
    if (!s || s->locked || !has_value(s)) return;
    settings_set(s, s->def);
    if (*pref_key(s)) app_pref_set(pref_key(s), "");
    s->saved = 0;
}

const char *settings_value_name(const Setting *s, int v, char *buf, int n) {
    if (s->type == SET_SLIDER) { snprintf(buf, (size_t)n, s->format ? s->format : "%d", v); return buf; }
    int nc;
    const char *const *l = labels(s, &nc);
    snprintf(buf, (size_t)n, "%s", v >= 0 && v < nc ? l[v] : "?");
    return buf;
}
const char *settings_source(const Setting *s) {
    static char buf[64];
    if (s->locked) { snprintf(buf, sizeof buf, "env %s", s->env); return buf; }
    if (!s->saved && has_value(s) && current(s) != s->def) return "non-default";   // kept under another key (pref "", e.g. the window mode, the overlay twin)
    return s->saved ? "app.txt" : "default";
}

// ---------------- Look preset (the Picture tab's first row) ----------------
// Every PC-vs-X360 choice of the picture, in one table with each side's saved spelling. Port extras (Natural bloom, Highlights,
// Banding, Colour filter, World lighting, Tunnel shadows, Detail distance, World streaming, the mirror's shape...) are not in it:
// presets leave them as they are. Not in it either: audio, controls and gameplay data (X360 sound, steering dead zone, X360 game
// data), which are not "look". Tunnel shadows: Keep is not established X360 behaviour (VERSIONS.md), so it stays out.
static const struct { const char *id, *pc, *x360; } kLook[] = {
    {"graphics.postfx", "pc", "x360"},
    {"graphics.tone_curve", "pc", "x360"},
    {"graphics.car_paint", "pc", "x360"},
    {"graphics.car_glass", "pc", "x360"},
    {"graphics.car_normals", "pc", "x360"},
    {"graphics.car_flake", "pc", "x360"},
    {"graphics.x360_arrest", "pc", "x360"},      // restart
    {"graphics.grass", "pc", "x360"},
    {"graphics.trees", "pc", "x360"},
    {"graphics.water", "pc", "x360"},
    {"graphics.building_glass", "pc", "x360"},
    {"graphics.scenery_lights", "pc", "x360"},
    {"graphics.parallax", "pc", "x360"},
    {"graphics.lighting_data", "pc", "x360"},        // restart
    {"graphics.road_reflection", "pc", "x360"},
    {"graphics.rain_ripples", "pc", "x360"},
    {"graphics.particles", "pc", "x360"},
    {"graphics.raindrops", "pc_before", "x360"},     // PC: as the PC drew them
    {"graphics.speed_blur", "off", "x360"},          // the PC had no radial blur
    {"graphics.shadow_fit", "pc", "x360"},
    {"graphics.hd_textures", "off", "on"},           // restart
    {"graphics.ui_textures", "off", "on"},           // restart
    {"graphics.mirror_exposure", "1", "1.4"},        // the X360 mirror used 140 %
};
#define NLOOK (int)(sizeof kLook / sizeof *kLook)
#define PICKS_KEY "graphics.my_picks"                // app.txt: "id=value;id=value", the values as saved
static int look = -1, look_busy;
void app_pref_batch_begin(void) __attribute__((weak));   // app.m; absent in the offline test builds
void app_pref_batch_end(void) __attribute__((weak));

static int look_matches(int x360) {
    for (int i = 0; i < NLOOK; ++i) {
        Setting *s = settings_find(kLook[i].id);
        if (!s || s->locked) continue;               // an NFS_* switch decides that row
        if (current(s) != parse(s, x360 ? kLook[i].x360 : kLook[i].pc)) return 0;
    }
    return 1;
}
static void look_save_picks(void) {
    char buf[1000] = "", v[64];
    for (int i = 0; i < NLOOK; ++i) {
        Setting *s = settings_find(kLook[i].id);
        if (!s) continue;
        value_str(s, current(s), v, sizeof v);
        size_t l = strlen(buf);
        snprintf(buf + l, sizeof buf - l, "%s%s=%s", l ? ";" : "", kLook[i].id, v);
    }
    app_pref_set(PICKS_KEY, buf);
}
int settings_look_state(void) {
    if (look < 0) look = look_matches(0) ? PRESET_PC : look_matches(1) ? PRESET_X360 : PRESET_CUSTOM;
    return look;
}
// A user edit of one of the table's rows: the preset row reads Custom, and the values are kept as "My picks". Port extras
// (Bloom strength, Brightness...) are outside the table: presets never touch them, so editing one leaves the preset as it is.
static void look_row_edited(Setting *s) {
    if (look_busy) return;
    int in_table = 0;
    for (int i = 0; i < NLOOK; ++i) if (!strcmp(kLook[i].id, s->id)) in_table = 1;
    if (!in_table) return;
    look = PRESET_CUSTOM;
    look_save_picks();
}
static int look_set_all(const char *(*spell)(int i, const char *picks), const char *picks) {
    int restart = 0;
    look_busy = 1;
    if (app_pref_batch_begin) app_pref_batch_begin();       // one app.txt write for the whole preset (audit4 #7)
    for (int i = 0; i < NLOOK; ++i) {
        Setting *s = settings_find(kLook[i].id);
        const char *sp = s ? spell(i, picks) : NULL;
        int v = sp ? parse(s, sp) : -1;
        if (v < 0 || s->locked || v == current(s)) continue;
        if (settings_set(s, v) && s->restart && s->value != s->boot_value) ++restart;
    }
    if (app_pref_batch_end) app_pref_batch_end();
    look_busy = 0;
    return restart;
}
static const char *spell_pc(int i, const char *p) { (void)p; return kLook[i].pc; }
static const char *spell_x360(int i, const char *p) { (void)p; return kLook[i].x360; }
static const char *spell_picks(int i, const char *p) {
    static char v[64];
    size_t n = strlen(kLook[i].id);
    for (const char *q = p; q && *q; ) {
        if (!strncmp(q, kLook[i].id, n) && q[n] == '=') {
            size_t l = strcspn(q + n + 1, ";");
            snprintf(v, sizeof v, "%.*s", (int)(l < sizeof v - 1 ? l : sizeof v - 1), q + n + 1);
            return v;
        }
        q = strchr(q, ';');
        if (q) ++q;
    }
    return NULL;
}
int settings_look_apply(int which) {
    int restart = 0;
    if (which == PRESET_CUSTOM) { look = PRESET_CUSTOM; return 0; }
    if (settings_look_state() == PRESET_CUSTOM) look_save_picks();   // leaving the user's own values: keep them
    if (which == PRESET_MINE) {
        const char *picks = app_pref(PICKS_KEY);
        if (!picks || !*picks) return -1;
        restart = look_set_all(spell_picks, picks);
    } else restart = look_set_all(which == PRESET_X360 ? spell_x360 : spell_pc, NULL);
    look = which;
    return restart;
}
