"""Four particle review regressions, pinned to the original f381a1fa.

Production: four checks pass. Original: saved PC/X360, viewport reconstruction,
P2 draw selection and shadowless registration each fail (4/4 regressions).
Each check compiles production functions or shader patchers; the old sources
are embedded as SHA-256-checked fragments; no Git history is needed.
"""
import ast
import os
from pathlib import Path
import hashlib
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RT = ROOT / 'runtime'

# Baseline integrity proof: 6/6 authentic fragments accepted; 6/6 one-byte corruptions rejected.
# Reviewed baseline fragment; its digest prevents accidental control drift.
OLD_SETTINGS_PORT_C = r'''static void particles_apply(int v) { particles_physical_set(v == 1); }
static Setting s_particles = {.id = "graphics.particles", .category = SET_WORLD, .order = 75, .label = "Particles",
    .help = "Retail keeps the original card shading. Physical softens smoke, dust and spray where they meet surfaces, and lights them from the scene.",
    .type = SET_CHOICE, .choices = (const char *const[]){"Retail", "Physical"},
    .values = (const char *const[]){"retail", "physical"}, .nchoices = 2, .def = 0,
    .env = "NFS_PARTICLES", .apply = particles_apply};'''
assert hashlib.sha256(OLD_SETTINGS_PORT_C.encode()).hexdigest() == '3b11aa1d730a934614f6ba55ae09a30a91b0391a65bb3ad025b681057f96f56a', 'pinned baseline changed'


# Reviewed baseline fragment; its digest prevents accidental control drift.
OLD_SETTINGS_C = r'''// settings.c - the settings registry (settings.h): values, app.txt, the NFS_* switches that override them.
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
    {"graphics.building_glass", "engine"}, {"graphics.x360_arrest", "engine"},
    {"graphics.rain_ripples", "engine"}, {"graphics.raindrops", "shaders"}, {"graphics.raindrops", "engine"},
    {"graphics.shadow_fit", "engine"},
    // The half-screen road reflection is listed as an X360 choice: without X360 files the PC's 320x240 is in effect and
    // the row is not shown (M4 F6).
    {"graphics.road_reflection", "world"},
    {"graphics.look_data", "look"},
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
const char *const settings_category_names[SET_NCATEGORIES] = {"Picture", "World", "Cars & cutscenes", "Mirror", "Display & performance", "Audio", "Controls", "Gameplay", "Online (beta)", "Help"};
static const char *const kOffOn[] = {"Off", "On"};

#define MAX_SETTINGS 160
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
    for (int i = 0; i < nall; ++i) if (all[i]->hidden) {
        char v[64];
        fprintf(f, "%s\t%s\t%s\n", x360_unavailable(all[i]) ? "hidden" : "preset", all[i]->id,
            x360_unavailable(all[i]) ? "(needs X360 files not in roots.txt; PC in effect)" : settings_value_name(all[i], all[i]->value, v, sizeof v));
    }
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
    // A partial preset may display My picks while another choice is active.
    // Selecting My picks still has to restore its saved values in that case.
    if (v == current(s) && (strcmp(s->id, "graphics.look_preset") || v == settings_look_choice())) return 0;
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
// Retail platform choices plus the visual extras that otherwise hide the authored look.
// Port uses each row's shipped default. Device/audio/input/gameplay preferences stay outside
// this table, except the optional chase-camera effect, explicitly disabled for retail looks.
static const struct { const char *id, *pc, *x360; } kLook[] = {
    {"graphics.postfx", "pc", "x360"},
    {"graphics.tone_curve", "pc", "x360"},
    {"graphics.car_paint", "pc", "x360"},
    {"graphics.car_glass", "pc", "x360"},
    {"graphics.headlamp_glass", "retail", "retail"},
    {"graphics.realworld_materials", "off", "off"},
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
    {"graphics.road_film", "0", "0"},
    {"graphics.rain_ripples", "pc", "x360"},
    {"graphics.particles", "pc", "x360"},
    {"graphics.raindrops", "pc_before", "x360"},     // PC: as the PC drew them
    {"graphics.speed_blur", "off", "x360"},          // the PC had no radial blur
    {"graphics.shadow_fit", "pc", "x360"},
    {"graphics.hd_textures", "off", "on"},           // restart
    {"graphics.ui_textures", "off", "on"},           // restart
    {"graphics.mirror_exposure", "1", "1.4"},        // the X360 mirror used 140 %
    {"graphics.look_data", "pc", "x360"},
    {"graphics.colour_filter", "100", "100"},
    {"graphics.bloom", "100", "100"},
    {"graphics.world_lighting", "original", "original"},
    {"graphics.highlights", "clip", "clip"},
    {"graphics.deband", "off", "off"},
    {"graphics.hdr_output", "off", "off"},
    {"graphics.lamp_glow", "off", "off"},
    {"graphics.car_reflection", "pc", "pc"},         // public visible row
    {"graphics.look_reflections", "pc", "pc"},      // private hidden override
    {"graphics.look_contact_shadows", "off", "off"},
    {"graphics.look_soft_smoke", "off", "off"},
    {"graphics.particles", "retail", "retail"},
    {"controls.chase_camera", "off", "off"},
    {"graphics.curve_glow", "x360", "x360"},
    {"graphics.bloom_surfaces", "glow", "glow"},
    {"graphics.mirror_size", "pc", "100"},
    {"graphics.mirror_detail", "pc", "full"},
    {"graphics.mirror_view", "pc", "60"},
    {"graphics.mirror_fog", "off", "on"},
    {"graphics.mirror_msaa", "off", "on"},
    {"graphics.look_mirror_art", "pc", "smooth"},
    {"graphics.look_fog", "pc", "x360"},
    {"graphics.detail_distance", "pc", "pc"},
    {"graphics.world_streaming", "pc", "ahead"},
    {"graphics.tunnel_shadows", "pc", "keep"},
    {"cars.later_games", "off", "off"},
    {"graphics.look_later_textures", "off", "off"},
};
#define NLOOK (int)(sizeof kLook / sizeof *kLook)
// Each chunk fits app.txt's 1024-byte value limit. The first key also reads old picks.
static const char *const picks_keys[] = {"graphics.my_picks", "graphics.my_picks.2", "graphics.my_picks.3"};
#define NCHUNKS (int)(sizeof picks_keys / sizeof *picks_keys)
#define LOOK_KEY "graphics.look_choice"
static const char *const look_names[] = {"port", "x360", "pc", "mine"};
static int look = -1, look_busy;
void app_pref_batch_begin(void) __attribute__((weak));
void app_pref_batch_end(void) __attribute__((weak));

static const char *look_picks(void) {
    static char buf[3100];
    buf[0] = 0;
    for (int i = 0; i < NCHUNKS; ++i) {
        const char *p = app_pref(picks_keys[i]);
        if (!p || !*p) continue;
        size_t n = strlen(buf);
        snprintf(buf + n, sizeof buf - n, "%s%s", n ? ";" : "", p);
    }
    return buf;
}
// Presets own the table; My picks also remembers device rows in Picture.
static int look_index(const Setting *s) {
    for (int i = 0; i < NLOOK; ++i) if (!strcmp(kLook[i].id, s->id)) return i;
    return -1;
}
static int look_tracks(const Setting *s) {
    return strcmp(s->id, "graphics.look_preset") && has_value(s) &&
           (s->category == SET_PICTURE || look_index(s) >= 0);
}
static const char *look_spell(Setting *s, int which, const char *picks) {
    static char v[64];
    int i = look_index(s);
    if (which != PRESET_MINE) {
        if (i < 0) return NULL;
        if (which == PRESET_PC) return kLook[i].pc;
        if (which == PRESET_X360) return kLook[i].x360;
        value_str(s, s->def, v, sizeof v);
        return v;
    }
    size_t n = strlen(s->id);
    for (const char *q = picks; q && *q; ) {
        if (!strncmp(q, s->id, n) && q[n] == '=') {
            size_t l = strcspn(q + n + 1, ";");
            snprintf(v, sizeof v, "%.*s", (int)(l < sizeof v - 1 ? l : sizeof v - 1), q + n + 1);
            return v;
        }
        q = strchr(q, ';');
        if (q) ++q;
    }
    return NULL;                         // legacy picks did not include the new extras
}
static int look_matches(int which) {
    const char *picks = which == PRESET_MINE ? look_picks() : NULL;
    if (which == PRESET_MINE && !*picks) return 0;
    for (int i = 0; i < nall; ++i) {
        Setting *s = all[i];
        if (!look_tracks(s) || x360_unavailable(s)) continue;
        const char *sp = look_spell(s, which, picks);
        if (sp && current(s) != parse(s, sp)) return 0;
    }
    return 1;
}
int settings_look_partial(void) {
    for (int i = 0; i < NLOOK; ++i) {
        Setting *s = settings_find(kLook[i].id);
        if (s && x360_unavailable(s)) return 1;
    }
    return 0;
}
static void look_save_picks(void) {
    char buf[1000] = "", entry[160], v[64];
    int chunk = 0;
    for (int i = 0; i < nall; ++i) {
        Setting *s = all[i];
        if (!look_tracks(s)) continue;
        value_str(s, current(s), v, sizeof v);
        snprintf(entry, sizeof entry, "%s=%s", s->id, v);
        size_t l = strlen(buf);
        if (l + 1 + strlen(entry) >= sizeof buf) {
            app_pref_set(picks_keys[chunk++], buf);
            buf[0] = 0; l = 0;
        }
        snprintf(buf + l, sizeof buf - l, "%s%s", l ? ";" : "", entry);
    }
    app_pref_set(picks_keys[chunk++], buf);
    while (chunk < NCHUNKS) app_pref_set(picks_keys[chunk++], "");
}
int settings_look_state(void) {
    if (look < 0) {
        int snapshot = 0;
        const char *p = app_pref(LOOK_KEY);
        if (p) for (int i = PRESET_PORT; i <= PRESET_MINE; ++i)
            if (!strcmp(p, look_names[i])) { look = i; break; }
        if (p && !strcmp(p, "custom")) { look = PRESET_MINE; snapshot = 1; }
        if (look < 0) {
            look = look_matches(PRESET_PORT) ? PRESET_PORT : look_matches(PRESET_X360) ? PRESET_X360 :
                   look_matches(PRESET_PC) ? PRESET_PC : PRESET_MINE;
            snapshot = look == PRESET_MINE;
        }
        if (snapshot) {
            if (app_pref_batch_begin) app_pref_batch_begin();
            look_save_picks();
            app_pref_set(LOOK_KEY, look_names[look]);
            if (app_pref_batch_end) app_pref_batch_end();
        }
    }
    // A locked override changes the label, never the user's saved picks.
    return look_matches(look) ? look : PRESET_MINE;
}
int settings_look_choice(void) { (void)settings_look_state(); return look; }
static void look_row_edited(Setting *s) {
    if (look_busy || !look_tracks(s)) return;
    look = PRESET_MINE;
    if (app_pref_batch_begin) app_pref_batch_begin();
    look_save_picks();
    app_pref_set(LOOK_KEY, look_names[look]);
    if (app_pref_batch_end) app_pref_batch_end();
}
int settings_look_apply(int which) {
    if (which < PRESET_PORT || which > PRESET_MINE) return -1;
    (void)settings_look_state();
    // Copy before saving anything: app_pref implementations may invalidate their return buffer.
    char picks[3100];
    snprintf(picks, sizeof picks, "%s", look_picks());
    if (which == PRESET_MINE && !*picks) return -1;
    int restart = 0;
    if (app_pref_batch_begin) app_pref_batch_begin();
    look_busy = 1;
    for (int i = 0; i < nall; ++i) {
        Setting *s = all[i];
        if (!look_tracks(s)) continue;
        const char *sp = look_spell(s, which, picks);
        int v = sp ? parse(s, sp) : -1;
        if (v >= 0) settings_set(s, v);
        if (s->restart && current(s) != s->boot_value) ++restart;
    }
    look_busy = 0;
    look = which;
    app_pref_set(LOOK_KEY, look_names[which]);
    if (app_pref_batch_end) app_pref_batch_end();
    return restart;
}
'''
assert hashlib.sha256(OLD_SETTINGS_C.encode()).hexdigest() == '9be2ac13e941454d9778e23d37c24d0fbb625888b4444d78a853d60f7cd2fceb', 'pinned baseline changed'


# Reviewed baseline fragment; its digest prevents accidental control drift.
OLD_MATERIALS_M = r'''static int particles_on(void) {
    if (particles_x360 < 0) { const char *e = getenv("NFS_PARTICLES"); particles_x360 = !(e && !strcmp(e, "pc")); }
    return particles_x360;
}

int mat_soft_particle_view(void) { return !particles_rain && particle_vid == 1; }

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

char *mat_soft_smoke_shader(const uint32_t *tokens, uint32_t n, const SmResult *refl, int *id) {
    char *s = mat_replace(tokens, n, refl, id);
    if (!s || !mat_is_particle(*id)) { free(s); *id = 0; return NULL; }
    if (!edit(&s, NULL, "fragment Out sm_main(", soft_smoke_math, 1) ||
        !edit(&s, NULL, "sampler smp1 [[sampler(1)]])", "sampler smp1 [[sampler(1)]], texture2d<float> smoke_depth [[texture(15)]])", 0) ||
        !edit(&s, NULL, "    return o;\n", "    if (m.k[1].w < 0.5f) o.c0.a *= smoke_fade(smoke_depth.read(uint2(pi.pos.xy)).r, pi.pos.z, m.k[1].x, m.k[1].y, m.k[1].z);\n    return o;\n", 0)) {
        free(s); *id = 0; return NULL;
    }
    s = particles_physical_shader(s);
    if (!s) *id = 0;
    return s;
}

char *mat_soft_smoke_resolved_shader(const uint32_t *tokens, uint32_t n, const SmResult *refl, int *id) {
    char *s=mat_soft_smoke_shader(tokens,n,refl,id);
    if(!s)return NULL;
    if(!edit(&s,NULL,"texture2d<float> smoke_depth", "depth2d<float, access::read> smoke_depth",0) ||
       !edit(&s,NULL,"smoke_depth.read(uint2(pi.pos.xy)).r", "smoke_depth.read(uint2(pi.pos.xy))",0)){free(s);*id=0;return NULL;}
    return s;
}

char *mat_soft_smoke_late_shader(const uint32_t *tokens, uint32_t n, const SmResult *refl, int *id) {
    char *s=mat_soft_smoke_resolved_shader(tokens,n,refl,id);if(!s)return NULL;
    if(!edit(&s,NULL,"    float4 v0 = saturate(pi.color0)",
        "    float scene_z=smoke_depth.read(uint2(pi.pos.xy));\n"
        "    if(scene_z < pi.pos.z || (m.k[2].x < 3.0f && scene_z == pi.pos.z)) discard_fragment();\n"
        "    float fade=1.0f; if(m.k[1].w < 0.5f && m.k[2].y > 0.5f && m.k[0].z < 0.5f) fade=smoke_fade(scene_z,pi.pos.z,m.k[1].x,m.k[1].y,m.k[1].z);\n"
        "    if(fade<=0.0f)discard_fragment();\n",1) ||
       !edit(&s,NULL,"    if (m.k[1].w < 0.5f) o.c0.a *= smoke_fade(smoke_depth.read(uint2(pi.pos.xy)), pi.pos.z, m.k[1].x, m.k[1].y, m.k[1].z);", "    o.c0.a *= fade;",0)){free(s);*id=0;return NULL;}
    return s;
}
static const char particles_msl[] =
"#include <metal_stdlib>\n"
"using namespace metal;\n"
SM_ALPHA_COVERAGE_MSL
"struct SmPSAux { uint alpha_func; float alpha_ref; uint alpha_to_coverage, pad1; float4 bumpenv[8]; float4 bumplum[8]; };\n"
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
SM_ALPHA_COVERAGE_APPLY("o.c0.w")
"    if (aux.alpha_func != 0u && !alpha_test(aux.alpha_func, o.c0.w, aux.alpha_ref)) discard_fragment();\n"
"    return o;\n"
"}\n";
static const char soft_smoke_math[] =
"inline float smoke_view_z(float d, float zn, float zf) { return zn*zf/(zf-d*(zf-zn)); }\n"
"inline float smoke_fade(float scene, float particle, float zn, float zf, float range) {\n"
"    if (scene >= 0.999999f) return 1.0f;\n"
"    float pz = smoke_view_z(particle, zn, zf);\n"
"    float fade = saturate((smoke_view_z(scene, zn, zf) - pz) / range);\n"
"    float camera = saturate((pz - zn - range) / range);\n"
"    return 1.0f + camera * (fade - 1.0f);\n"
"}\n";
'''
assert hashlib.sha256(OLD_MATERIALS_M.encode()).hexdigest() == '1bfbc41ca2887df5fc9bc4d7aa5c09bdc46329930a2c40e302850c2e39cf34e5', 'pinned baseline changed'


# Reviewed baseline fragment; its digest prevents accidental control drift.
OLD_RENDER_M = r'''particles_physical_constants(&mk, (unsigned)rt0.width, (unsigned)rt0.height);
    int smoke_draw = 0;
    ShaderEntry *smoke_original = pse;
    if(diag_on && gpt_on==2 && !ssao_replay && !lamp_replay && !q_active)soft_smoke_cost_note(pse->mat);
    if (diag_on && !ssao_replay && soft_smoke_on() && mat_is_particle(pse->mat)) {
        static unsigned reports;
        if (reports++ < 12) diag_event("soft smoke: candidate view %d z %u write %u blend %u(%u,%u) mainrt %d maindepth %d vp %08X %08X",
            mat_soft_particle_view(), rs[7], rs[14], rs[27], rs[19], rs[20], rt0 == (bb_rt.ms ? bb_rt.ms : bb_rt.tex),
            dst == (ad_rt.ms ? ad_rt.ms : ad_rt.tex), dev.viewport[4], dev.viewport[5]);
    }
    if (!ssao_replay && !lamp_replay && !q_active && soft_smoke_on() && !soft_smoke_late_on() && mat_is_particle(pse->mat) && mat_soft_particle_view() &&
        rs[7] == 1 && !rs[14] && rs[27] && rs[19] == 5 && rs[20] == 6 &&
        dev.viewport[4] == 0 && dev.viewport[5] == 0x3F800000u &&
        rt0 == (bb_rt.ms ? bb_rt.ms : bb_rt.tex) && dst == (ad_rt.ms ? ad_rt.ms : ad_rt.tex) && dst) {
        ShaderEntry *sp = get_shader_variant(ps, &po, dst==ad_rt.ms?MAT_VARIANT_SOFT_SMOKE_RESOLVED:MAT_VARIANT_SOFT_SMOKE);
        if (!__atomic_load_n(&sp->pending, __ATOMIC_ACQUIRE) && sp->ok && soft_smoke_snapshot(dst)) { pse = sp; smoke_draw = 1; }
    }
    int blend = rs[27]'''
assert hashlib.sha256(OLD_RENDER_M.encode()).hexdigest() == 'a549a101be547340455802c8921becfeae30c8913cef85113edca58a009ab17e', 'pinned baseline changed'


# Reviewed baseline fragment; its digest prevents accidental control drift.
OLD_PARTICLES_PHYSICAL_C = r'''// Opt-in single-scattering approximation. Constants/references: docs/particles.md.
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "host.h"
#include "particles_physical.h"

static int physical = -1;
extern uint64_t frame_serial;
static uint64_t lamp_frame;
static ParticleLamps lamps;
static uint32_t lamp_keys[8];
int postfx_look_variant(const char *variant);
int particles_physical_enabled(void) {
    if (diag_on && postfx_look_variant("softoff")) return 0;
    if (diag_on && (postfx_look_variant("softon") || postfx_look_variant("softbroken") ||
                   postfx_look_variant("softlitoff") || postfx_look_variant("softhdoff"))) return 1;
    if (physical < 0) {
        const char *e = getenv("NFS_PARTICLES");
        physical = e && !strcmp(e, "physical");
    }
    return physical;
}
void particles_physical_set(int enabled) { physical = enabled != 0; }

// Capture before flare facing/occlusion changes its brightness. Main-view car
// flares precede the emitter pass. Only the two decoded front marker hashes;
// exclude reflection/environment replays and never retain guest pointers.
void particles_physical_lamp(Cpu *cpu) {
    if(!particles_physical_enabled())return;
    uint32_t view=M32(cpu->esp+4), flare=M32(cpu->esp+8), matrix=M32(cpu->esp+12);
    if(!view || M32(view+4)!=1 || !flare || !matrix || M32(cpu->esp+20) || M32(cpu->esp+24))return;
    uint32_t key=M32(flare+8);
    if(key!=0x9DB90133u && key!=0xD09091C6u)return;
    float intensity=MF32(cpu->esp+16);
    if(!isfinite(intensity) || intensity<=0)return;
    if(lamp_frame!=frame_serial){memset(&lamps,0,sizeof lamps);lamp_frame=frame_serial;}
    unsigned count=(unsigned)lamps.k[0][0], index=0;
    for(;index<count;++index)if(lamp_keys[index]==flare)break;
    if(index>=8)return;
    float *p=lamps.k[1+2*index], *d=lamps.k[2+2*index];
    float length=0;
    for(int c=0;c<3;++c){
        p[c]=MF32(matrix+48+4*c);d[c]=0;
        for(int r=0;r<3;++r){
            float m=MF32(matrix+16*r+4*c);
            p[c]+=MF32(flare+0x10+4*r)*m;
            d[c]+=MF32(flare+0x20+4*r)*m;
        }
        if(!isfinite(p[c]) || !isfinite(d[c]))return;
        length+=d[c]*d[c];
    }
    if(length<1e-8f)return;
    for(int c=0;c<3;++c)d[c]/=sqrtf(length);
    p[3]=fminf(1,intensity);d[3]=60.0f;
    lamp_keys[index]=flare;
    if(index==count)lamps.k[0][0]=count+1;
}
void particles_physical_lamps(ParticleLamps *out) {
    if(lamp_frame==frame_serial && particles_physical_enabled())*out=lamps;
    else memset(out,0,sizeof *out);
    if(diag_on && postfx_look_variant("softhdoff"))memset(out,0,sizeof *out);
}

static const char particles_phase_msl[] =
"struct ParticleLamps { float4 k[17]; };\n"
"inline float particle_hg(float mu, float g) {\n"
"    float d = max(1.0f + g*g - 2.0f*g*clamp(mu,-1.0f,1.0f), 1.0e-6f);\n"
"    return (1.0f-g*g) / (12.566370614359172f * d * sqrt(d));\n"
"}\n"
"static float3 particle_world(float4 p, constant MatConsts &m) {\n"
"    float4 q=p.x*m.k[4]+p.y*m.k[5]+p.z*m.k[6]+p.w*m.k[7];\n"
"    return q.xyz / q.w;\n"
"}\n"
"static float3 particle_light(float4 p, constant MatConsts &m, constant ParticleLamps &lamps, float visibility) {\n"
"    float2 xy=float2(p.x*m.k[3].x-1.0f, 1.0f-p.y*m.k[3].y);\n"
"    float3 a=particle_world(float4(xy,0.0f,1.0f),m);\n"
"    float3 b=particle_world(float4(xy,0.5f,1.0f),m);\n"
"    float3 ray=normalize(b-a);\n"
"    float phase=12.566370614359172f*particle_hg(dot(ray,m.k[10].xyz),m.k[8].w);\n"
"    float3 light=m.k[8].xyz + m.k[9].xyz*visibility*phase;\n"
"    float3 world=particle_world(float4(xy,p.z,1.0f),m);\n"
"    for(uint i=0;i<min(uint(lamps.k[0].x),8u);++i) {\n"
"        float4 h=lamps.k[1+2*i], f=lamps.k[2+2*i];\n"
"        float3 delta=world-h.xyz; float r=length(delta);\n"
"        if(r<0.05f || r>=f.w)continue;\n"
"        float3 q=delta/r;\n"
"        float cone=smoothstep(0.819152f,0.965926f,dot(q,f.xyz));\n"
"        float attenuation=pow(1.0f-r/f.w,2.0f)/(1.0f+r*r/64.0f);\n"
"        light+=float3(h.w*cone*attenuation*12.566370614359172f*particle_hg(dot(q,-ray),m.k[8].w));\n"
"    }\n"
"    return m.k[9].w*light;\n"
"}\n";

// Replace one known anchor; fail closed if the source contract has changed.
static int particle_edit(char **source, const char *from, const char *to) {
    char *at=strstr(*source,from);
    if (!at || strstr(at+strlen(from),from)) return 0;
    char *next=NULL;
    if (asprintf(&next,"%.*s%s%s",(int)(at-*source),*source,to,at+strlen(from))<0) return 0;
    free(*source);*source=next;return 1;
}
char *particles_physical_shader(char *source) {
    char *head=NULL;
    if (asprintf(&head,"%sfragment Out sm_main(",particles_phase_msl)<0) { free(source);return NULL; }
    int ok=particle_edit(&source,"struct MatConsts { float4 k[4]; };","struct MatConsts { float4 k[12]; };") &&
        particle_edit(&source,"fragment Out sm_main(",head) &&
        particle_edit(&source,"constant MatConsts &m [[buffer(4)]],","constant MatConsts &m [[buffer(4)]], constant ParticleLamps &lamps [[buffer(6)]],") &&
        particle_edit(&source,"    Out o;\n","    Out o; float physical_visibility=1.0f;\n") &&
        particle_edit(&source,
            "        float shadow = saturate(n * (1.0f / 9.0f) + saturate(1.0f - 4.0f * uv.y));\n",
            "        float shadow = saturate(n * (1.0f / 9.0f) + saturate(1.0f - 4.0f * uv.y));\n        physical_visibility=shadow;\n") &&
        particle_edit(&source,"        float s = lit1(tex1, smp1, uv, z);\n",
            "        float s = lit1(tex1, smp1, uv, z);\n        physical_visibility=s;\n") &&
        particle_edit(&source,"    return o;\n",
            "    if(m.k[3].w > 0.5f && m.k[0].z < 0.5f && m.k[2].y > 0.5f) {\n"
            "        o.c0.rgb=2.0f*tex.rgb*v0.rgb*particle_light(pi.pos,m,lamps,physical_visibility);\n"
            "    }\n    return o;\n");
    free(head);
    if (!ok) { free(source);return NULL; }
    return source;
}

static int particle_inverse(const float *input, float output[4][4]) {
    double a[4][8]={0};
    for(int r=0;r<4;++r)for(int c=0;c<4;++c){a[r][c]=input[4*r+c];a[r][c+4]=r==c;}
    for(int c=0;c<4;++c){
        int pivot=c;for(int r=c+1;r<4;++r)if(fabs(a[r][c])>fabs(a[pivot][c]))pivot=r;
        if(!isfinite(a[pivot][c]) || fabs(a[pivot][c])<1e-12)return 0;
        for(int j=0;j<8;++j){double t=a[c][j];a[c][j]=a[pivot][j];a[pivot][j]=t;}
        double scale=a[c][c];for(int j=0;j<8;++j)a[c][j]/=scale;
        for(int r=0;r<4;++r)if(r!=c){double f=a[r][c];for(int j=0;j<8;++j)a[r][j]-=f*a[c][j];}
    }
    for(int r=0;r<4;++r)for(int c=0;c<4;++c){if(!isfinite(a[r][c+4]))return 0;output[r][c]=(float)a[r][c+4];}
    return 1;
}
void particles_physical_constants(MatConsts *out, unsigned width, unsigned height) {
    if(!particles_physical_enabled() || !width || !height)return;
    uint32_t view=M32(0x009195E0u+0x70u);
    if(!view || !particle_inverse((const float *)(G+view+0x80),&out->k[4]))return;
    out->k[3][0]=2.0f/width;out->k[3][1]=2.0f/height;out->k[3][3]=1;
    if(diag_on && postfx_look_variant("softlitoff"))out->k[3][3]=0;
    lighting_particle_scene(out->k[8],out->k[9],out->k[10]);
    // Mineral aerosol proxy, not a calibration of tyre smoke or this scene's dust.
    out->k[8][3]=0.682f;out->k[9][3]=0.93f;
    if(diag_on && getenv("BOOT_DIAG_SOFT_SMOKE_COST")){
        static uint64_t noted;
        if(noted!=frame_serial){noted=frame_serial;
            diag_event("particle scene: frame %llu ambient %.4f %.4f %.4f sun %.4f %.4f %.4f direction %.4f %.4f %.4f lamps %u",
                (unsigned long long)frame_serial,out->k[8][0],out->k[8][1],out->k[8][2],out->k[9][0],out->k[9][1],out->k[9][2],
                out->k[10][0],out->k[10][1],out->k[10][2],lamp_frame==frame_serial?(unsigned)lamps.k[0][0]:0);
        }
    }
}
'''
assert hashlib.sha256(OLD_PARTICLES_PHYSICAL_C.encode()).hexdigest() == 'eb7144ae880ed7da7cb42b5d3e8aaa6f5549884009daf11a7cbea360997ea86c', 'pinned baseline changed'


# Reviewed baseline fragment; its digest prevents accidental control drift.
OLD_PARTICLES_PHYSICAL_H = r'''// Scene-lit aerosol cards; authored textures and particle alpha remain the base.
#pragma once
#include "materials.h"
#include "host.h"

typedef struct ParticleLamps { float k[17][4]; } ParticleLamps;

int particles_physical_enabled(void);
void particles_physical_set(int enabled);
char *particles_physical_shader(char *source);
void particles_physical_constants(MatConsts *out, unsigned width, unsigned height);
void particles_physical_lamp(Cpu *cpu);
void particles_physical_lamps(ParticleLamps *out);
void lighting_particle_scene(float ambient[3], float sun[3], float direction[3]);
'''
assert hashlib.sha256(OLD_PARTICLES_PHYSICAL_H.encode()).hexdigest() == '4262b251f78f8c7a547be950826834538b57c00982eac4fa10f7e8321106192d', 'pinned baseline changed'

OLD_PARTICLES = {
    'settings_port.c': OLD_SETTINGS_PORT_C,
    'settings.c': OLD_SETTINGS_C,
    'materials.m': OLD_MATERIALS_M,
    'render.m': OLD_RENDER_M,
    'particles_physical.c': OLD_PARTICLES_PHYSICAL_C,
    'particles_physical.h': OLD_PARTICLES_PHYSICAL_H,
}




def run(args, **kwargs):
    p = subprocess.run(list(map(str, args)), capture_output=True, text=True, **kwargs)
    assert p.returncode == 0, p.stdout[-1000:] + p.stderr[-2500:]
    return p.stdout


def function(text, name):
    start = text.index(name + '(')
    start = text.rfind('\n', 0, start) + 1
    end = text.index('{', start) + 1
    depth = 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end]


def cstring(text, name):
    return re.search(r'static const char ' + name + r'\[\] =.*?;\n', text, re.S)[0]


def compile_run(tmp, source, *extra):
    path = tmp / 'check.c'
    path.write_text(source)
    binary = tmp / 'check'
    run(['clang', '-O1', '-DNFS_PUBLIC=1', '-I', tmp, '-I', RT, '-I', ROOT / 'recomp', path, *extra, '-o', binary])
    p = subprocess.run([str(binary)], capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    return p.stdout.strip()


def saved_choices(files, tmp):
    port = files['settings_port.c']
    row = re.search(r'static Setting s_particles =.*?};', port, re.S)[0]
    stubs = (RT / 'settings_look_test.c').read_text()
    stubs = stubs[stubs.index('#include <stdio.h>'):stubs.index('static int failures;')]
    source = stubs + '''
static int particles_x360=-1,physical=-1;
void materials_set_particles(int v){particles_x360=v;}
void particles_physical_set(int v){physical=v;}
int postfx_look_variant(const char *v){(void)v;return 0;}
''' + function(files['materials.m'], 'particles_on') + '\n'
    source += function(files['particles_physical.c'], 'particles_physical_enabled') + '\n'
    source += function(port, 'particles_apply') + '\n' + row
    source += '''
int main(int argc,char **argv){
  unsetenv("NFS_PARTICLES");
  app_pref_set("graphics.particles",argv[1]);settings_add(&s_particles);settings_init();
  int v=settings_get("graphics.particles");
  int failures=strcmp(s_particles.values[v],argv[1])!=0;
  failures+=particles_on()!=!!strcmp(argv[1],"pc");
  failures+=particles_physical_enabled()!=(!strcmp(argv[1],"physical"));
  for(int i=0;i<s_particles.nchoices;++i){
    s_particles.value=-1;settings_set(&s_particles,i);
    failures+=particles_on()!=!!strcmp(s_particles.values[i],"pc");
    failures+=particles_physical_enabled()!=(!strcmp(s_particles.values[i],"physical"));
  }
  printf("saved %s: %d failures\\n",argv[1],failures);return failures!=0;
}
'''
    path = tmp / 'saved.c'; path.write_text(source)
    binary = tmp / 'saved'
    loader = tmp / 'settings.c'; loader.write_text(files['settings.c'])
    run(['clang', '-I', RT, '-I', ROOT / 'recomp', path, loader, '-o', binary])
    results = [subprocess.run([str(binary), value], capture_output=True, text=True)
               for value in ('pc', 'x360', 'retail', 'physical')]
    failures = sum(p.returncode != 0 for p in results)
    assert failures == 0, '; '.join(p.stdout.strip() for p in results)
    # Both retail look presets must retain their platform value, without a later
    # duplicate row silently replacing it with Retail.
    look = files['settings.c'].split('kLook[] = {', 1)[1].split('};', 1)[0]
    rows = re.findall(r'\{"graphics.particles",\s*"([^"]+)",\s*"([^"]+)"\}', look)
    assert rows == [('pc', 'x360')], rows
    return '4 saved values and all live transitions retain style; PC/X360 look values retained'


def viewport(files, tmp):
    source = files['particles_physical.c']
    phase = cstring(source, 'particles_phase_msl')
    msl = ''.join(ast.literal_eval(s) for s in re.findall(r'"(?:\\.|[^"\\])*"', phase))
    xy = re.search(r'float2 xy=float2\((.*?), (.*?)\);', msl)
    assert xy
    def scalar(expr):
        expr = re.sub(r'm\.k\[(\d+)\]\.([xyzw])', lambda m: 'mk.k['+m[1]+']['+str('xyzw'.index(m[2]))+']', expr)
        return expr.replace('p.x', 'px').replace('p.y', 'py')
    call = re.search(r'particles_physical_constants\(&mk,.*?;', files['render.m'])[0]
    (tmp / 'particles_physical.c').write_text(source)
    (tmp / 'particles_physical.h').write_text(files['particles_physical.h'])
    harness = '''
#include <stdlib.h>
#include <math.h>
#include "particles_physical.h"
uint8_t *G; int diag_on; uint64_t frame_serial;
int postfx_look_variant(const char *v){return 0;}
void diag_event(const char *s,...){(void)s;}
void lighting_particle_scene(float *a,float *s,float *d){}
int main(void){
  G=calloc(0xa00000,1);particles_physical_set(1);
  M32(0x009195E0+0x70)=0x2000;M32(0x009195E0+0xe0)=0x3000;
  for(int i=0;i<4;++i){MF32(0x2080+20*i)=1;MF32(0x3080+20*i)=2;}
  struct {unsigned width,height;} rt0={1920,1080};
  struct {uint32_t viewport[6];} dev={{0,0,1920,540,0,0x3f800000}};
  int failures=0;
  for(unsigned particle_view=1;particle_view<=2;++particle_view){
    dev.viewport[1]=particle_view==1?0:540;
    MatConsts mk={0};
''' + call + '''
    float px=960,py=dev.viewport[1]+270;
    float x=''' + scalar(xy[1]) + ',y=' + scalar(xy[2]) + ''';
    if(fabsf(x)>1e-6 || fabsf(y)>1e-6)++failures;
    if(fabsf(mk.k[4][0]-(particle_view==1?1:.5f))>1e-6)++failures;
    printf("view %u center=(%.3f,%.3f) inverse=%.3f\\n",particle_view,x,y,mk.k[4][0]);
  }
  free(G);return failures!=0;
}
'''
    return compile_run(tmp, harness, tmp / 'particles_physical.c')


def player_two(files, tmp):
    material, render = files['materials.m'], files['render.m']
    block = render.split('    int smoke_draw = 0;', 1)[1].split('    int blend = rs[27]', 1)[0]
    source = '''
#include <stdint.h>
#include <stdio.h>
#define MAT_VARIANT_SOFT_SMOKE 0x104
#define MAT_VARIANT_SOFT_SMOKE_RESOLVED 0x105
typedef struct {int mat,pending,ok;} ShaderEntry;
static struct {uint32_t viewport[6],tex[8];} dev;
static int decal_depth_ground(uint32_t t){(void)t;return 0;}
static struct {void *ms,*tex;} bb_rt,ad_rt;
static int diag_on,ssao_replay,lamp_replay,q_active,gpt_on,particles_rain;
static unsigned particle_vid;
static int variants,snapshots;
static void soft_smoke_cost_note(int m){}
static int soft_smoke_on(void){return 1;}
static int soft_smoke_late_on(void){return 0;}
static int mat_is_particle(int id){return id==1;}
static void diag_event(const char *s,...){}
static ShaderEntry replacement={1,0,1};
static ShaderEntry *get_shader_variant(void *p,void *q,int v){++variants;return &replacement;}
static int soft_smoke_snapshot(void *d){++snapshots;return 1;}
static int split_view_rect(uint32_t view,uint32_t rect[4]){
  rect[0]=0;rect[1]=view==0x00919650?0:540;rect[2]=1920;rect[3]=540;return 1;
}
''' + function(material, 'mat_soft_particle_view') + '\n'
    if 'unsigned mat_particle_view(' in material:
        source = '#include <string.h>\n' + source + function(material, 'mat_particle_view') + '\n'
        view_check = '''
  uint32_t top[4]={0,0,1920,540},bottom[4]={0,540,1920,540};
  particle_vid=1;failures+=mat_particle_view(bottom)!=2;
  particle_vid=2;failures+=mat_particle_view(top)!=1;
'''
    else:
        view_check = '  ++failures;\n'
    source += '''
int draw(uint32_t *rs,void *rt0,void *dst){
  ShaderEntry original={1,0,1},*pse=&original;void *ps=0;int po=0,smoke_draw=0;
''' + block + '''
  return smoke_draw;
}
int main(void){
  uint32_t rs[256]={0};rs[7]=rs[27]=1;rs[19]=5;rs[20]=6;
  dev.viewport[5]=0x3f800000;bb_rt.tex=(void*)1;ad_rt.tex=(void*)2;
  int failures=0;
  for(particle_vid=1;particle_vid<=2;++particle_vid){
    int selected=draw(rs,bb_rt.tex,ad_rt.tex);failures+=!selected;
    printf("view %u selected=%d\\n",particle_vid,selected);
  }
  particle_vid=3;failures+=draw(rs,bb_rt.tex,ad_rt.tex);
  particle_vid=2;particles_rain=1;failures+=draw(rs,bb_rt.tex,ad_rt.tex);
  failures+=variants!=2 || snapshots!=2;
''' + view_check + '''
  return failures!=0;
}
'''
    # Also pin the renderer's depth camera to the emitter's view, not P1.
    result = compile_run(tmp, source)
    assert 'particle_view * 0x70u + 0x40u' in render
    assert 'mat_particle_view(dev.viewport)' in render
    return result + '; rain/mirrors excluded; stale emitter IDs use the active player viewport'


def shadowless(files, tmp):
    material = files['materials.m']
    shader = (ROOT / 'host/shaders/testdata/particles/t0_world_p0_ps_1_1.bin').read_bytes()
    digest = 1469598103934665603
    for value in shader:
        digest = ((digest ^ value) * 1099511628211) & ((1 << 64)-1)
    assert digest == 0xE30981E7D1BD50E9
    row = re.search(r'\{"particles t0.*?particle_constants, 0\},', material, re.S)
    assert row and f'0x{digest:016X}ULL' in row[0], 't0 bytecode has no material registration'
    assert 'particles_msl, particle_t0_patch, particles_always, particle_constants' in row[0]
    # Execute the production patcher. Both hardware and software functions must
    # become lit, without reading a missing shadow texture or t0's absent uv1.
    source = '''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "roots.h"
#include "../host/shaders/sm_coverage.h"
#include "gen/pc_shader_lits.inc"
''' + cstring(material, 'particles_msl') + function(material, 'edit') + '\n'
    source += function(material, 'particle_t0_patch') + '''
int main(void){char *s=particle_t0_patch(NULL);if(!s)return 1;puts(s);free(s);}
'''
    msl = compile_run(tmp, source)
    assert msl.count('return 1.0f;') == 2
    assert not any(s in msl for s in ('t.sample(', 't.sample_compare(', 'tex1.get_width()', 'pi.texcoord1.xy /'))
    # Compile all three production Physical paths from the registered t0 base.
    src = source[:source.index('int main(void)')]
    src += cstring(material, 'soft_smoke_math')
    src += cstring(files['particles_physical.c'], 'particles_phase_msl')
    for name in ('particle_edit', 'particles_physical_shader'):
        src += function(files['particles_physical.c'], name) + '\n'
    src += '''
typedef struct {int unused;} SmResult;
char *mat_replace(const uint32_t *t,uint32_t n,const SmResult *r,int *id){*id=1;return particle_t0_patch(NULL);}
int mat_is_particle(int id){return id==1;}
'''
    for name in ('mat_soft_smoke_shader', 'mat_soft_smoke_resolved_shader', 'mat_soft_smoke_late_shader'):
        src += function(material, name) + '\n'
    src += '''
int main(int argc,char **argv){int id=0;char *s=argc>1?(argv[1][0]=='l'?mat_soft_smoke_late_shader(0,0,0,&id):mat_soft_smoke_resolved_shader(0,0,0,&id)):mat_soft_smoke_shader(0,0,0,&id);if(!s||!id)return 1;puts(s);free(s);}
'''
    compile_run(tmp, src)
    binary = tmp / 'check'
    metal = run(['xcrun', '--find', 'metal']).strip()
    for variant in ([], ['resolved'], ['late']):
        msl = run([binary, *variant])
        path = tmp / 'shadowless.metal'
        path.write_text('#define MAT_SHADOW_HW 0\n' + msl)
        run([metal, '-fmodules-cache-path='+str(tmp / 'modules'), '-c', path, '-o', tmp / 'shadowless.air'],
            env={**os.environ, 'TMPDIR': str(tmp)+'/'})
    return 't0 hash matches; lit visibility/no shadow reads; 3 Physical shaders compile'


def main():
    names = ('settings_port.c', 'settings.c', 'materials.m', 'render.m', 'particles_physical.c', 'particles_physical.h')
    # Both production and the optional original controls need only the source tree.
    for label in (('original', 'production') if '--baseline' in sys.argv else ('production',)):
        files = {name: (OLD_PARTICLES[name] if label == 'original'
                        else (RT / name).read_text()) for name in names}
        failures = 0
        for check in (saved_choices, viewport, player_two, shadowless):
            with tempfile.TemporaryDirectory(prefix='particles-review-') as directory:
                try:
                    detail = check(files, Path(directory))
                except AssertionError as error:
                    failures += 1
                    print(f'{label} {check.__name__}: FAIL {str(error)[:500]}')
                else:
                    print(f'{label} {check.__name__}: PASS {detail}')
        expected = 4 if label == 'original' else 0
        assert failures == expected, (label, failures, expected)
        print(f'{label}: {failures}/4 regressions')


if __name__ == '__main__':
    main()
