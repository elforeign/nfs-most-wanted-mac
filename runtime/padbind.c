// padbind.c - the game's own controller bindings for every controller type.
//
// The PC game (v1.3) binds input through one table, GameAction (0091F418, 76 actions of 0x34 bytes: the action's name,
// then four bindings of {type, device or key, object}: keyboard primary / secondary, joystick primary / secondary).
// Binding types (FUN_00628940, which reads them each frame, and the bind capture FUN_00634490): 1 key (DIK code),
// 2 joystick button (object = button), 3 POV direction (0 up, 1 right, 2 down, 3 left), 4/5 X/Y/Z axis below / above
// its centre (object 0..2), 6/7 Rx/Ry/Rz below / above, 8/9 slider. Each action's value is the largest of its four
// bindings, 0..1 (an axis half is analog; the game's input mappings press digital actions at 0.5: ActionQueue::
// IO_UpdateFromDevice 006448E0). The menus read the same table (FE_ACTION_* entries, not on the Controls screen).
// The Controls screen (UIOptionsPCController) edits the first 19 actions; the profile saves those 19 with the hash of
// the joystick's name (0056E180 / 0056E200) and, when a profile is loaded with another joystick, the game puts that
// joystick's defaults in (FUN_0063CD80, a table of PC pads by name) and the profile's set for the other pad is lost.
//
// Here:
// - A PlayStation pad (dinput.c: itself, with PlayStation control names) gets the PS2 layout as its defaults: the
//   Xbox 360 defaults translated through what user32.m's PlayStation layout made of the emulated pad's buttons (so the
//   defaults drive exactly as before), plus what that layout sent outside the bindings (right stick up / down gas and
//   brake, R3 speedbreaker, R2 / L2 shift up / down; in menus Square, Create and Options for the keys 2 / 3 / 4).
//   Menus: Cross accepts, Triangle goes back (FE_ACTION_ACCEPT / CANCEL), as the PS2. The PS button and the touchpad
//   click are bindable but bound to nothing (the touchpad click opens the port menu). Other pads use separate PS2/Xbox layout variants, with independent Z/Rz trigger axes.
// - Each controller type (the joystick's name) keeps its own set per profile: the game saves the active one in the
//   profile, this file all of them in USERDATA/pad_bindings.txt (published after a career commit; type changes stay
//   dirty in memory until that save), so
//   loading a profile with another pad, or changing pads while playing, brings that pad's own set back.
// - The binding text on the Controls screen names PlayStation controls and stick directions (FUN_006280C0 would show
//   the DirectInput object's name, the same for both halves of an axis).
// BOOT_DIAG: bindings.txt (the table at each defaults / load / save / type change / capture) and events.log "padbind:"
// lines, including the Controls actions' values as they change (the bindings at work in a race).
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

#include "host.h"

#define GA 0x0091F418u          // GameAction[76]
#define GA_EXE 0x008F6D80u      // the exe's copy (FUN_00628410 copies it when the IOModule is created)
#define GA_N 76
#define GA_PAGE 19              // the Controls screen's actions, saved in the profile
#define JOY 0x0091F150u         // the IOModule's joystick (name at +0x114, from DIDEVICEINSTANCE)
#define BASELINE 0x0091F1E8u    // the joystick's state at IOModule start: bind capture takes an axis only away from it
#define FEDB 0x0091CF90u        // FEDatabase: +0x10 the player's UserProfile (+0 name, +0x20 named)
#define IOMOD_DEVS 0x00920DE8u  // IOModule devices (+0x18 scalars {1, hash, &previous, &current} per action)
#define XBOX_L3 11              // DirectInput button index user32.m's Xbox layout gives L3
#define XBOX_NAME "Controller (XBOX 360 For Windows)"
enum { T_KEY = 1, T_BUTTON, T_POV, T_AXIS_LO, T_AXIS_HI, T_ROT_LO, T_ROT_HI, T_SLIDER_LO, T_SLIDER_HI };
// user32.m's P_* (the PlayStation pad's DirectInput buttons)
enum { P_CROSS, P_CIRCLE, P_SQUARE, P_TRIANGLE, P_L1, P_R1, P_CREATE, P_OPTIONS, P_L3, P_R3, P_L2, P_R2, P_PS, P_TOUCHPAD,
       P_LPADDLE, P_RPADDLE };
enum { A_GAS, A_BRAKE, A_GAMEBREAKER = 5, A_SHIFTDOWN = 7, A_SHIFTUP = 8, A_NEXTSONG = 24, A_FE_B0 = 37, A_FE_B3 = 39, A_FE_B4 = 40 };

static uint32_t rec(int a, int slot) { return GA + 0x34u * (uint32_t)a + 4 + 12u * (uint32_t)slot; }
static void set_rec(uint32_t r, uint32_t t, uint32_t d, uint32_t o) { M32(r) = t; M32(r + 4) = d; M32(r + 8) = o; }
static uint32_t name_hash(const char *s) {     // bStringHashUpper
    uint32_t h = 0xFFFFFFFFu;
    for (; *s; ++s) h = h * 33 + (uint32_t)(*s >= 'a' && *s <= 'z' ? *s - 32 : *s);
    return h;
}
static int name_ps(const char *n) {
    return !strcmp(n, "Controller (DualSense)") || !strcmp(n, "Controller (DualShock 4)") || !strcmp(n, "Controller (DualSense Edge)");
}
int user32_pad_layout_xbox(void);
enum { L_LEGACY, L_NATIVE_PS, L_NONPS_PS2, L_NONPS_XBOX };
static const char *const layout_names[] = {"legacy", "native_ps", "nonps_ps2", "nonps_xbox"};
static int layout_for(const char *name) {
    return name_ps(name) ? L_NATIVE_PS : user32_pad_layout_xbox() ? L_NONPS_XBOX : L_NONPS_PS2;
}
static uint32_t legacy_defaults[GA_PAGE][2][3], nonps_defaults_page[GA_PAGE][2][3];
static int legacy_defaults_valid;
static int active_layout;
static uint32_t joy(void) { uint32_t d = M32(JOY); return d && d != 0xFFFFFFFCu ? d : 0; }
static char *joy_name(uint32_t dev) { return (char *)G + dev + 0x114; }
int padbind_pedals(int port) {
    if (!pedals_selected()) return 0;
    if (port == 1) { char name[64]; pad_identity_n(1, name, sizeof name); return name_ps(name); }
    uint32_t d = joy();
    return d && name_ps(joy_name(d));
}
static const char *action_name(int a) { const char *n = gptr(M32(GA + 0x34u * (uint32_t)a)); return n ? n : "?"; }
static const char *profile_name(void) {
    uint32_t db = M32(FEDB), up = db ? M32(db + 0x10) : 0;
    static char n[33];
    snprintf(n, sizeof n, "%.32s", up ? (const char *)G + up : "");
    return n;
}

// ---------------- binding text ----------------
static void ps_text(uint32_t t, uint32_t o, int ds4, char *out, size_t n) {
    static const char *const buttons[16] = {"Cross", "Circle", "Square", "Triangle", "L1", "R1", "Create", "Options", "L3",
                                            "R3", "L2", "R2", "PS", "Touchpad", "Left Paddle", "Right Paddle"};
    static const char *const pov[4] = {"D-Pad Up", "D-Pad Right", "D-Pad Down", "D-Pad Left"};
    static const char *const stick[2][2][2] = {{{"Left Stick Left", "Left Stick Right"}, {"Left Stick Up", "Left Stick Down"}},
                                               {{"Right Stick Left", "Right Stick Right"}, {"Right Stick Up", "Right Stick Down"}}};
    if (t == T_BUTTON) snprintf(out, n, "%s", o < 16 ? (o == P_CREATE && ds4 ? "Share" : buttons[o]) : "Button");
    else if (t == T_POV) snprintf(out, n, "%s", pov[o & 3]);
    else if (t >= T_AXIS_LO && t <= T_ROT_HI) {
        int rot = t >= T_ROT_LO, hi = t == T_AXIS_HI || t == T_ROT_HI;   // 4 6: below the centre (up, left), 5 7: above
        if (o == 2) snprintf(out, n, "%s", rot ? "R2" : "L2");
        else snprintf(out, n, "%s", stick[rot][o & 1][hi]);
    } else if (t == T_KEY) snprintf(out, n, "Key %u", o);
    else snprintf(out, n, "Slider %u", o);
}
void f_006280C0_orig(Cpu *c);
void f_006280C0(Cpu *c) {                      // cdecl (char *out, int size, int device 1 keyboard / 3 joystick, binding *)
    uint32_t out = arg(c, 0), size = arg(c, 1), page = arg(c, 2), r = arg(c, 3), dev = joy();
    if (page != 3 || !dev || !name_ps(joy_name(dev)) || !size || !r) { f_006280C0_orig(c); return; }
    char t[64];
    ps_text(M32(r), M32(r + 8), strstr(joy_name(dev), "DualShock") != NULL, t, sizeof t);
    snprintf((char *)G + out, size, "%s", t);
    c->esp += 4;
}

// ---------------- diagnostics ----------------
static void rec_text(uint32_t r, int joystick, int ps, char *out, size_t n) {
    uint32_t t = M32(r), d = M32(r + 4), o = M32(r + 8);
    if (!t) { snprintf(out, n, "---"); return; }
    char x[48];
    if (joystick && ps) ps_text(t, o, 0, x, sizeof x);
    else if (t == T_KEY) snprintf(x, sizeof x, "key %02X", d);
    else snprintf(x, sizeof x, "type %u obj %u", t, o);
    snprintf(out, n, "%s (%u,%u,%u)", x, t, d, o);
}
static void dump(const char *why) {
    if (!diag_on) return;
    static FILE *f;
    if (!f) {
        char p[1100];
        snprintf(p, sizeof p, "%s/bindings.txt", diag_dir);
        if (!(f = fopen(p, "w"))) return;
    }
    uint32_t dev = joy();
    int ps = dev && name_ps(joy_name(dev));
    fprintf(f, "== %s: joystick %s, profile \"%s\"\n", why, dev ? joy_name(dev) : "(none)", profile_name());
    for (int a = 0; a < 42; ++a) {
        char k0[64], k1[64], j0[64], j1[64];
        rec_text(rec(a, 0), 0, ps, k0, sizeof k0); rec_text(rec(a, 1), 0, ps, k1, sizeof k1);
        rec_text(rec(a, 2), 1, ps, j0, sizeof j0); rec_text(rec(a, 3), 1, ps, j1, sizeof j1);
        fprintf(f, "  %2d %-28s kb %-22s %-22s joy %-34s %s\n", a, action_name(a), k0, k1, j0, j1);
    }
    fflush(f);
    diag_event("padbind: %s (bindings.txt)", why);
}

// ---------------- defaults ----------------
// What user32.m's PlayStation layout made of the emulated Xbox 360 pad's buttons (Button 1 = index 0), while driving
// (the Controls actions) and in menus (the others); L2 / R2 are axes here.
#define L2_AXIS 100
#define R2_AXIS 101
static const int drive_map[11] = {P_CROSS, P_R1, P_SQUARE, P_CIRCLE, P_TRIANGLE, P_CREATE, P_L1, P_OPTIONS, R2_AXIS, L2_AXIS, -1};
static const int menu_map[11] = {P_CIRCLE, P_CROSS, P_TRIANGLE, -1, P_L1, P_R1, -1, P_OPTIONS, R2_AXIS, L2_AXIS, -1};
static void put(uint32_t r, int m) {
    if (m == L2_AXIS) set_rec(r, T_AXIS_HI, 0, 2);
    else if (m == R2_AXIS) set_rec(r, T_ROT_HI, 0, 2);
    else if (m < 0) set_rec(r, 0, 0, 0);
    else set_rec(r, T_BUTTON, 0, (uint32_t)m);
}
static void add(int a, uint32_t t, uint32_t o) {   // into the action's first free joystick slot, unless it is there
    for (int s = 2; s < 4; ++s) if (M32(rec(a, s)) == t && M32(rec(a, s) + 8) == o) return;
    for (int s = 2; s < 4; ++s) if (!M32(rec(a, s))) { set_rec(rec(a, s), t, 0, o); return; }
}
static void ps_defaults(void) {
    for (int a = 0; a < GA_N; ++a) {
        for (int s = 2; s < 4; ++s) {
            uint32_t r = rec(a, s), t = M32(r), o = M32(r + 8);
            if (t == T_BUTTON) put(r, o < 11 ? (a < GA_PAGE ? drive_map : menu_map)[o] : -1);
            else if (((t >= T_AXIS_LO && t <= T_ROT_HI) && o == 2) || t == T_SLIDER_LO || t == T_SLIDER_HI)
                set_rec(r, 0, 0, 0);            // Z (the Xbox pad's triggers, centred by the layout) and sliders: dead before
        }
        if (!M32(rec(a, 2)) && M32(rec(a, 3))) {   // the primary first
            memcpy(G + rec(a, 2), G + rec(a, 3), 12);
            set_rec(rec(a, 3), 0, 0, 0);
        }
    }
    add(A_GAS, T_ROT_LO, 1);                   // right stick up / down: gas / brake (analog)
    add(A_BRAKE, T_ROT_HI, 1);
    add(A_GAMEBREAKER, T_BUTTON, P_R3);
    add(A_SHIFTDOWN, T_AXIS_HI, 2);            // L2 / R2 (pressed at half travel)
    add(A_SHIFTUP, T_ROT_HI, 2);
    add(A_NEXTSONG, T_BUTTON, P_L3);           // the X360's default for HUDACTION_NEXTSONG: LThumb (the PC's was the key T)
    add(A_FE_B4, T_BUTTON, P_SQUARE);          // menus: the keys 2 / 3 / 4 (the packages' SQUARE / SELECT / START)
    add(A_FE_B0, T_BUTTON, P_CREATE);
    add(A_FE_B3, T_BUTTON, P_OPTIONS);
}
static void nonps_defaults(int xbox) {
    for (int a = A_GAS; a <= A_BRAKE; ++a)
        for (int k = 2; k < 4; ++k) {
            uint32_t r = rec(a, k), t = M32(r), o = M32(r + 8);
            if ((o == 2 && t >= T_AXIS_LO && t <= T_ROT_HI) ||
                (xbox && t == T_BUTTON && o == (uint32_t)(a == A_GAS ? 0 : 2))) set_rec(r, 0, 0, 0);
        }
    if (xbox) {
        add(A_GAS, T_ROT_HI, 2);
        add(A_BRAKE, T_AXIS_HI, 2);
    } else {
        // The layout still supplies Button 1/3 for face/right-stick pedals. Triggers only shift.
        for (int a = A_SHIFTDOWN; a <= A_SHIFTUP; ++a)
            for (int k = 2; k < 4; ++k) {
                uint32_t r = rec(a, k);
                if (M32(r) == T_BUTTON && M32(r + 8) == (uint32_t)(a == A_SHIFTDOWN ? 9 : 8)) set_rec(r, 0, 0, 0);
            }
        add(A_SHIFTDOWN, T_AXIS_HI, 2);
        add(A_SHIFTUP, T_ROT_HI, 2);
    }
    add(A_NEXTSONG, T_BUTTON, XBOX_L3);
}
// P2 uses its own layout defaults when P1's shared table describes another layout.
// Cache from the retail Xbox base, before profile/custom bindings are applied.
static uint32_t port_defaults[3][GA_N][6], port_saved[GA_N][6];
static int port_defaults_valid;
static void port_page(uint32_t page[GA_N][6], int install) {
    for (int a = 0; a < GA_N; ++a)
        if (install) memcpy(G + rec(a, 2), page[a], 24);
        else memcpy(page[a], G + rec(a, 2), 24);
}
static void port_cache_defaults(void) {
    if (pad_count() != 2) return;
    port_page(port_saved, 0);                // unmodified retail Xbox defaults
    for (int layout = L_NATIVE_PS; layout <= L_NONPS_XBOX; ++layout) {
        port_page(port_saved, 1);
        if (layout == L_NATIVE_PS) ps_defaults();
        else nonps_defaults(layout == L_NONPS_XBOX);
        port_page(port_defaults[layout - L_NATIVE_PS], 0);
    }
    port_page(port_saved, 1);
    port_defaults_valid = 1;
}
int padbind_port_begin(int idx) {
    if (idx != 1 || pad_count() != 2 || !port_defaults_valid) return 0;
    char name[64];
    pad_identity_n(idx, name, sizeof name);
    int layout = layout_for(name);
    if (layout == active_layout) return 0;   // same layout retains the shared custom bindings
    port_page(port_saved, 0);
    port_page(port_defaults[layout - L_NATIVE_PS], 1);
    return 1;
}
void padbind_port_end(int swapped) { if (swapped) port_page(port_saved, 1); }

void f_0063CD80_orig(Cpu *c);
void f_0063CD80(Cpu *c) {                      // cdecl (void): the joystick's defaults, by its name
    uint32_t dev = joy();
    if (!dev) { f_0063CD80_orig(c); return; }
    char name[64];
    snprintf(name, sizeof name, "%s", joy_name(dev));
    int ps = name_ps(name);
    // All joystick slots from the exe first: the game's defaults set only some actions (not the menus'), and a pad of
    // another type may have had the table before.
    for (int a = 0; a < GA_N; ++a) memcpy(G + rec(a, 2), G + GA_EXE + 0x34u * (uint32_t)a + 4 + 24, 24);
    strcpy(joy_name(dev), XBOX_NAME);          // every pad starts from the Xbox 360 controller's PC defaults
    f_0063CD80_orig(c);
    strcpy(joy_name(dev), name);
    for (int a = 0; a < GA_PAGE; ++a)
        for (int k = 0; k < 2; ++k) for (int i = 0; i < 3; ++i)
            legacy_defaults[a][k][i] = M32(rec(a, k + 2) + 4u * (uint32_t)i);
    legacy_defaults_valid = 1;
    port_cache_defaults();
    if (ps) ps_defaults();
    else {
        nonps_defaults(user32_pad_layout_xbox());
        for (int a = 0; a < GA_PAGE; ++a)
            for (int k = 0; k < 2; ++k) for (int i = 0; i < 3; ++i)
                nonps_defaults_page[a][k][i] = M32(rec(a, k + 2) + 4u * (uint32_t)i);
    }
    active_layout = layout_for(name);
    char why[96];
    snprintf(why, sizeof why, "defaults for %s", name);
    dump(why);
}

// ---------------- the sets per controller type (USERDATA/pad_bindings.txt) ----------------
typedef struct { char profile[33], name[64]; uint32_t hash, r[GA_PAGE][2][3]; int layout; } Set;
static Set *sets;
static int nsets, sets_loaded, sets_dirty, migration_backup_needed;
static void sets_path(char *p, size_t n) { snprintf(p, n, "%s/pad_bindings.txt", userdata_dir()); }
static int ps_hash(uint32_t h) {
    return h == name_hash("Controller (DualSense)") || h == name_hash("Controller (DualShock 4)") ||
           h == name_hash("Controller (DualSense Edge)");
}
static void sets_load(void) {
    if (sets_loaded) return;
    sets_loaded = 1;
    char p[1100], line[2048];
    sets_path(p, sizeof p);
    FILE *f = fopen(p, "r");
    if (!f) return;
    int v2 = 0;
    while (fgets(line, sizeof line, f)) {
        if (!strncmp(line, "# NFSMW pad bindings v2", 22)) v2 = 1;
        if (line[0] == '#' || line[0] == '\n') continue;
        Set row = {0};
        char *tab1 = strchr(line, '\t'), *tab2 = tab1 ? strchr(tab1 + 1, '\t') : NULL;
        char *tab3 = tab2 ? strchr(tab2 + 1, '\t') : NULL;
        if (!tab3) continue;
        snprintf(row.profile, sizeof row.profile, "%.*s", (int)(tab1 - line), line);
        row.hash = (uint32_t)strtoul(tab1 + 1, NULL, 16);
        snprintf(row.name, sizeof row.name, "%.*s", (int)(tab3 - tab2 - 1), tab2 + 1);
        char *q = tab3 + 1;
        if (v2) {
            char *tab4 = strchr(q, '\t');
            if (!tab4) continue;
            *tab4 = 0;
            int layout;
            for (layout = L_LEGACY; layout <= L_NONPS_XBOX; ++layout) if (!strcmp(q, layout_names[layout])) break;
            if (layout > L_NONPS_XBOX) continue;
            row.layout = layout;
            q = tab4 + 1;
        } else row.layout = name_ps(row.name) || ps_hash(row.hash) ? L_NATIVE_PS : L_LEGACY;
        int ok = 1;
        for (int a = 0; a < GA_PAGE && ok; ++a)
            for (int k = 0; k < 2 && ok; ++k) {
                int used = 0;
                ok = sscanf(q, " %u,%u,%u%n", &row.r[a][k][0], &row.r[a][k][1], &row.r[a][k][2], &used) == 3;
                q += used;
            }
        if (!ok) continue;
        sets = realloc(sets, (size_t)(nsets + 1) * sizeof *sets);
        sets[nsets++] = row;
    }
    fclose(f);
}
// Keep the original text file byte-for-byte, once, before a migration writes v2. Legacy rows also stay in v2.
static int migration_backup(void) {
    char p[1100], backup[1110];
    sets_path(p, sizeof p); snprintf(backup, sizeof backup, "%s.v1", p);
    FILE *old = fopen(p, "rb");
    if (!old) return 1;                       // profile-only legacy set, no sidecar yet
    FILE *exists = fopen(backup, "rb");
    if (exists) { fclose(exists); fclose(old); return 1; }
    FILE *out = fopen(backup, "wx");
    if (!out) { fclose(old); return 0; }
    char buf[4096]; size_t n;
    int ok = 1;
    while ((n = fread(buf, 1, sizeof buf, old))) if (fwrite(buf, 1, n, out) != n) { ok = 0; break; }
    if (ferror(old)) ok = 0;
    fclose(old);
    if (fclose(out)) ok = 0;
    if (!ok) remove(backup);
    return ok;
}
static void sets_save(void) {
    if (!sets_dirty) return;                  // no-op loads/swaps never rewrite the sidecar
    if (migration_backup_needed && !migration_backup()) return;
    char p[1100], tmp[1110];
    sets_path(p, sizeof p); snprintf(tmp, sizeof tmp, "%s.tmp", p);
    FILE *f = fopen(tmp, "w");
    if (!f) return;
    fprintf(f, "# NFSMW pad bindings v2\n# profile, name hash, name, layout, 19 primary/secondary type,device,object triples\n"
               "# legacy rows are rollback copies; native_ps values and hashes are never migrated.\n");
    for (int i = 0; i < nsets; ++i) {
        fprintf(f, "%s\t%08X\t%s\t%s\t", sets[i].profile, sets[i].hash, sets[i].name, layout_names[sets[i].layout]);
        for (int a = 0; a < GA_PAGE; ++a)
            for (int k = 0; k < 2; ++k) fprintf(f, " %u,%u,%u", sets[i].r[a][k][0], sets[i].r[a][k][1], sets[i].r[a][k][2]);
        fputc('\n', f);
    }
    int ok = !ferror(f);
    if (fclose(f)) ok = 0;
    if (ok && !rename(tmp, p)) sets_dirty = migration_backup_needed = 0;
    else remove(tmp);
}
static Set *set_find(const char *profile, uint32_t hash, int layout) {
    sets_load();
    for (int i = 0; i < nsets; ++i)
        if (sets[i].hash == hash && sets[i].layout == layout && !strcmp(sets[i].profile, profile)) return &sets[i];
    return NULL;
}
static Set *set_put(const char *profile, uint32_t hash, const char *name, int layout) {
    Set *row = set_find(profile, hash, layout);
    if (!row) {
        sets = realloc(sets, (size_t)(nsets + 1) * sizeof *sets);
        row = &sets[nsets++];
        memset(row, 0, sizeof *row);
        snprintf(row->profile, sizeof row->profile, "%s", profile);
        row->hash = hash; row->layout = layout;
        sets_dirty = 1;
    }
    if (name && *name && strcmp(row->name, name)) { snprintf(row->name, sizeof row->name, "%s", name); sets_dirty = 1; }
    return row;
}
static void set_from_table(Set *row) {
    for (int a = 0; a < GA_PAGE; ++a)
        for (int k = 0; k < 2; ++k) for (int i = 0; i < 3; ++i) {
            uint32_t v = M32(rec(a, 2 + k) + 4u * (uint32_t)i);
            if (row->r[a][k][i] != v) { row->r[a][k][i] = v; sets_dirty = 1; }
        }
}
static void set_to_table(const Set *row) {
    for (int a = 0; a < GA_PAGE; ++a)
        for (int k = 0; k < 2; ++k) set_rec(rec(a, 2 + k), row->r[a][k][0], row->r[a][k][1], row->r[a][k][2]);
}
static int has_v2(const char *profile, uint32_t hash) {
    for (int l = L_NATIVE_PS; l <= L_NONPS_XBOX; ++l) if (set_find(profile, hash, l)) return 1;
    return 0;
}
static void migrate_nonps(Set *row, int layout) {
    // Only legacy clones enter here. New v2 Z-negative captures must stay deliberate Z-negative bindings.
    for (int a = A_GAS; a <= A_BRAKE; ++a) {
        if (legacy_defaults_valid && !memcmp(row->r[a], legacy_defaults[a], sizeof row->r[a])) {
            memcpy(row->r[a], nonps_defaults_page[a], sizeof row->r[a]);
            continue;                       // replace old default button aliases only as an exact action pair
        }
        for (int k = 0; k < 2; ++k) {
            uint32_t *r = row->r[a][k];
            if (r[1] || r[2] != 2 || r[0] != (uint32_t)(a == A_GAS ? T_AXIS_LO : T_AXIS_HI)) continue;
            if (layout == L_NONPS_XBOX) r[0] = a == A_GAS ? T_ROT_HI : T_AXIS_HI;
            else memset(r, 0, 12);            // PS2 pedals must not respond to shift triggers
        }
    }
    if (layout == L_NONPS_PS2 && legacy_defaults_valid)
        for (int a = A_SHIFTDOWN; a <= A_SHIFTUP; ++a)
            if (!memcmp(row->r[a], legacy_defaults[a], sizeof row->r[a]))
                memcpy(row->r[a], nonps_defaults_page[a], sizeof row->r[a]);
    row->layout = layout;
}
// Materialize the requested variant once. Never reinterpret an existing v2 variant or a native PS record.
static Set *set_for(const char *profile, const char *name, int layout) {
    uint32_t hash = name_hash(name);
    Set *row = set_find(profile, hash, layout);
    if (row || layout == L_NATIVE_PS) return row;
    Set *old = set_find(profile, hash, L_LEGACY);
    if (!old && !has_v2(profile, hash)) old = set_find(profile, name_hash(XBOX_NAME), L_LEGACY);
    if (!old) return NULL;
    Set copy = *old;                          // set_put can realloc
    migrate_nonps(&copy, layout);
    row = set_put(profile, hash, name, layout);
    memcpy(row->r, copy.r, sizeof row->r);
    sets_dirty = migration_backup_needed = 1;
    if (diag_on) diag_event("padbind: migrated legacy %08X to %s (rollback retained)", hash, layout_names[layout]);
    return row;
}
static void install_defaults(Cpu *c) {
    uint32_t esp = c->esp, eax = c->eax, ecx = c->ecx, edx = c->edx;
    call_guest(c, 0x0063CD80u, 0, NULL);
    c->esp = esp; c->eax = eax; c->ecx = ecx; c->edx = edx;
}

uint32_t f_0056E180_orig(Cpu *c);
// BEGIN offline bindings transaction: no disk writes until the career save task publishes its file.
static Set pending_bindings;
static int pending_bindings_valid;
static void bindings_stage(const char *profile, const char *name, int layout) {
    memset(&pending_bindings, 0, sizeof pending_bindings);
    snprintf(pending_bindings.profile, sizeof pending_bindings.profile, "%s", profile);
    snprintf(pending_bindings.name, sizeof pending_bindings.name, "%s", name);
    pending_bindings.hash = name_hash(name); pending_bindings.layout = layout;
    int dirty = sets_dirty;
    set_from_table(&pending_bindings);
    sets_dirty = dirty;
    pending_bindings_valid = 1;
}
void padbind_save_complete(int committed) {
    if (committed) {
        if (pending_bindings_valid) {
            Set *row = set_put(pending_bindings.profile, pending_bindings.hash, pending_bindings.name, pending_bindings.layout);
            *row = pending_bindings; sets_dirty = 1;
        }
        sets_save();
    }
    pending_bindings_valid = 0;
}
// END offline bindings transaction
void f_0056E180(Cpu *c) {
    pending_bindings_valid = 0;
    f_0056E180_orig(c);
    uint32_t dev = joy();
    if (!dev) return;
    const char *name = joy_name(dev);
    bindings_stage(profile_name(), name, active_layout ? active_layout : layout_for(name));
    dump("profile staged");
}
void f_0056E200_orig(Cpu *c);
void f_0056E200(Cpu *c) {
    uint32_t buf = arg(c, 0), end = arg(c, 1), dev = joy();
    char cur[64] = "";
    if (dev) snprintf(cur, sizeof cur, "%s", joy_name(dev));
    uint32_t cur_hash = name_hash(cur), saved = buf + 4 <= end ? M32(buf) : 0;
    // Let the game load its keyboard records unchanged. Controller variants are restored separately below.
    if (dev && saved == name_hash(XBOX_NAME) && saved != cur_hash && !name_ps(cur)) M32(buf) = cur_hash;
    f_0056E200_orig(c);
    if (buf + 4 <= end) M32(buf) = saved;
    if (!dev) return;
    // Preserve the PC/native-PS profile precedence exactly: its own saved bindings are already installed.
    // A stale sidecar must never override or migrate a DualSense profile loaded with that same pad.
    if (name_ps(cur) && saved == cur_hash) {
        active_layout = L_NATIVE_PS;
        dump("profile loaded (native PS unchanged)");
        return;
    }
    const char *prof = profile_name();
    if (saved && buf + 4 + 0x30 * GA_PAGE <= end && !has_v2(prof, saved) && !set_find(prof, saved, L_LEGACY)) {
        // A profile with a known v2 set (even for another attached pad/layout) is never demoted to legacy.
        Set *row = set_put(prof, saved, saved == cur_hash ? cur : NULL, ps_hash(saved) ? L_NATIVE_PS : L_LEGACY);
        for (int a = 0; a < GA_PAGE; ++a)
            for (int k = 0; k < 2; ++k) for (int i = 0; i < 3; ++i)
                row->r[a][k][i] = M32(buf + 4 + 0x30u * (uint32_t)a + 24 + 12u * (uint32_t)k + 4u * (uint32_t)i);
    }
    install_defaults(c);                     // menus/defaults for this identity and layout, joystick slots only
    Set *mine = set_for(prof, cur, layout_for(cur));
    if (mine) set_to_table(mine);
    // Migration stays dirty in memory until a career commit succeeds.
    dump("profile loaded (layout variant)");
}

// ---------------- a change of controller type ----------------
// The game enumerates its joystick once; a pad of another type (another name) takes it over: this type's set is kept,
// the joystick renamed (the Controls screen's page, the profile's hash) and the new type's set (or its defaults) put in.
// Not while the Controls screen is loaded (it would put its copy of the old set back): when it is gone.
static void action_values(void) {              // BOOT_DIAG: the Controls actions' values as they change
    static uint32_t dev;
    if (!dev || M32(dev + 0x1838) != GA_N)
        for (uint32_t i = 0, d; i < 8 && (!dev || M32(dev + 0x1838) != GA_N); ++i)
            if ((d = M32(IOMOD_DEVS + 4 * i)) && M32(d + 0x1838) == GA_N) dev = d;
    if (!dev || M32(dev + 0x1838) != GA_N) { dev = 0; return; }
    uint32_t sc = M32(dev + 0x18);
    static float last[GA_PAGE];
    for (int a = 0; a < GA_PAGE && sc; ++a) {
        uint32_t u = M32(M32(sc + 16u * (uint32_t)a + 12));
        float v;
        memcpy(&v, &u, 4);
        if (v - last[a] > 0.2f || last[a] - v > 0.2f || (v < 0.01f) != (last[a] < 0.01f)) {
            diag_event("padbind: %s %.2f", action_name(a), v);
            last[a] = v;
        }
    }
}
// dinput stores the paired DI sample at the end of a poll. At the next poll's sync the game's scalar update
// has completed: these are GameDevice values before FetchInput's 0.05 pedal snap, not the previous step's actions.
static struct { unsigned step; float lt, rt; uint32_t z, rz, base_z, base_rz; int native, layout, pending; } trigger_sample;
void padbind_trigger_sample(unsigned step, float lt, float rt, uint32_t z, uint32_t rz, int native) {
    if (!step || step == trigger_sample.step) return;
    trigger_sample.step = step; trigger_sample.lt = lt; trigger_sample.rt = rt;
    trigger_sample.z = z; trigger_sample.rz = rz; trigger_sample.native = native;
    trigger_sample.base_z = M32(BASELINE + 0x208); trigger_sample.base_rz = M32(BASELINE + 0x214);
    trigger_sample.layout = active_layout; trigger_sample.pending = 1;
    pedals_trace_sample(step, lt, rt);
}
static void trigger_sample_log(void) {
    if (!trigger_sample.pending) return;
    static uint32_t dev;
    if (!dev || M32(dev + 0x1838) != GA_N)
        for (uint32_t i = 0, d; i < 8 && (!dev || M32(dev + 0x1838) != GA_N); ++i)
            if ((d = M32(IOMOD_DEVS + 4 * i)) && M32(d + 0x1838) == GA_N) dev = d;
    uint32_t sc = dev && M32(dev + 0x1838) == GA_N ? M32(dev + 0x18) : 0;
    if (!sc) return;
    float gas, brake;
    uint32_t g = M32(M32(sc + 16u * A_GAS + 12)), b = M32(M32(sc + 16u * A_BRAKE + 12));
    memcpy(&gas, &g, 4); memcpy(&brake, &b, 4);
    diag_event("padtriggers: sample %u physical %.3f/%.3f DI %u/%u gas %.4f brake %.4f native %d layout %s baseline %u/%u",
               trigger_sample.step, trigger_sample.lt, trigger_sample.rt, trigger_sample.z, trigger_sample.rz,
               gas, brake, trigger_sample.native, layout_names[trigger_sample.layout], trigger_sample.base_z, trigger_sample.base_rz);
    trigger_sample.pending = 0;
}
static int controls_screen_open(void) {       // the Controls screen keeps its own copy of the table: no swap under it
    uint32_t fe = M32(0x0091CADCu) ? M32(M32(0x0091CADCu)) : 0;   // cFEng::mInstance -> FEngine, packages at +0xE4
    if (!fe || fe == 0xFFFFFF28u) return 0;
    for (uint32_t p = M32(fe + 0xE4); p; p = M32(p + 8)) {
        const char *n = gptr(M32(p + 0xC));
        if (n && strcasestr(n, "Options_PC_Controller")) return 1;
    }
    return 0;
}
// ---------------- next song ----------------
// HUDACTION_NEXTSONG (the X360's LThumb, the PC's key T) has no reader in the PC code: nothing in gameplay looks at it
// (the front end's banner and jukebox are separate). The press is noticed here (the action's value, each joystick
// read, while racing and not paused) and answered in SFXObj_PFEATrax::ProcessUpdate (004F6EA0, the music object's own
// update), with what the game does itself to move on in the menus (TestToLicensed 004DF490): stop the channel
// (PATH_stop 00829310) and StartLicensedMusic(0) (vtable +0x40), which draws the next track and shows the banner.
static volatile int song_req;
void padbind_request_next_song(void) { song_req = 1; }   // extratrax.c's NFS_EXTRA_TRAX_TEST_NEXT (as an L3 press)
static void next_song_poll(void) {
    static uint32_t dev;
    static int held;
    if (!dev || M32(dev + 0x1838) != GA_N)
        for (uint32_t i = 0, d; i < 8 && (!dev || M32(dev + 0x1838) != GA_N); ++i)
            if ((d = M32(IOMOD_DEVS + 4 * i)) && M32(d + 0x1838) == GA_N) dev = d;
    uint32_t sc = dev && M32(dev + 0x1838) == GA_N ? M32(dev + 0x18) : 0;
    if (!sc) { dev = 0; return; }
    uint32_t u = M32(M32(sc + 16u * A_NEXTSONG + 12));
    float v;
    memcpy(&v, &u, 4);
    int down = v > 0.5f;
    uint32_t sim = M32(0x009885E0u);
    if (down && !held && M32(0x00925E90u) == 6 && !(sim && M32(sim + 0x2C) == 4)) song_req = 1;
    held = down;
}
void f_004F6EA0_orig(Cpu *c);
void f_004F6EA0(Cpu *c) {                      // thiscall (SFXObj_PFEATrax this), virtual
    if (song_req) {
        song_req = 0;
        uint32_t self = c->ecx, esp = c->esp, ecx = c->ecx;
        uint32_t ch = M32(self + 0x13C), before = M32(0x008F2090u + M32(self + 0x150) * 0x18);
        if (!(M32(self + 0x114) & 0x800)) {
            uint32_t h = M32(self + ch * 0x74 + 0x4C);
            call_guest(c, 0x00829310u, 1, &h);
        }
        uint32_t zero = 0;
        c->ecx = self;
        call_guest(c, M32(M32(self) + 0x40), 1, &zero);
        c->esp = esp; c->ecx = ecx;
        if (diag_on) diag_event("padbind: next song requested (track %d -> %d)", (int)before, (int)M32(0x008F2090u + M32(self + 0x150) * 0x18));
    }
    f_004F6EA0_orig(c);
}

// controltype.c swaps preset bindings into the table for the length of its GameAction update, which polls the joystick:
// a change noticed then would save the preset as the old pad's set and be undone by the swap's restore. So controltype
// follows the pad itself just before the swap (padbind_follow) and the reads inside it (padbind_swap) leave it alone.
static int swap_on;
void padbind_swap(int on) { swap_on = on; }
static void follow_pad(Cpu *c) {
    uint32_t dev = joy();
    char want[64];
    if (!dev || pad_identity(want, sizeof want) == PAD_KIND_NONE || !want[0]) return;
    int layout = layout_for(want);
    if (!strcmp(joy_name(dev), want) && active_layout == layout) return;
    if (controls_screen_open()) return;
    const char *prof = profile_name();
    char old[64];
    snprintf(old, sizeof old, "%s", joy_name(dev));
    set_from_table(set_put(prof, name_hash(old), old, active_layout ? active_layout : layout_for(old)));
    snprintf(joy_name(dev), 64, "%s", want);
    install_defaults(c);
    Set *row = set_for(prof, want, layout);
    if (row) set_to_table(row);
    // Controller variants stay dirty in memory until a career commit succeeds.
    fprintf(stderr, "boot: controller bindings: %s -> %s / %s (%s set)\n", old, want, layout_names[layout], row ? "its own" : "default");
    char why[224];
    snprintf(why, sizeof why, "controller/layout changed: %s -> %s / %s (%s set)", old, want, layout_names[layout], row ? "own" : "default");
    dump(why);
}
void padbind_follow(Cpu *c) { follow_pad(c); }   // controltype.c, with the table as the user's own
// BOOT_KEYS padbindps (tests): on a PlayStation pad, the joystick primaries a player's capture of R2 / L2 records on
// Accelerate / Brake (FUN_00634490: Rz / Z above the centre), replacing Cross / Square; the rest of the set as it is.
static volatile int test_player_triggers;
void padbind_test_player_triggers(void) { test_player_triggers = 1; }
static void player_triggers_apply(void) {
    uint32_t dev = joy();
    if (!test_player_triggers || !dev || !name_ps(joy_name(dev)) || controls_screen_open()) return;
    test_player_triggers = 0;
    set_rec(rec(A_GAS, 2), T_ROT_HI, 0, 2);
    set_rec(rec(A_BRAKE, 2), T_AXIS_HI, 0, 2);
    if (diag_on) dump("test: Accelerate R2, Brake L2 (player bindings)");
}
void padbind_sync(Cpu *c) {                    // dinput.c, each joystick read
    if (diag_on) { action_values(); trigger_sample_log(); }
    next_song_poll();
    // Startup and every type/layout change share the six-axis capture baseline, even with no pad attached yet.
    M32(BASELINE + 0x208) = M32(BASELINE + 0x214) = 32768;
    if (!swap_on) follow_pad(c);
    player_triggers_apply();
}

// ---------------- bind capture (diagnostics) ----------------
void f_00634900_orig(Cpu *c);
void f_00634900(Cpu *c) {                      // fastcall (IOModule device): a capture in progress
    if (!diag_on) { f_00634900_orig(c); return; }
    static uint8_t before[GA_PAGE * 0x34];
    memcpy(before, G + GA, sizeof before);
    f_00634900_orig(c);
    if (memcmp(before, G + GA, sizeof before)) {
        uint32_t dev = joy();
        int ps = dev && name_ps(joy_name(dev));
        for (int a = 0; a < GA_PAGE; ++a)
            for (int s = 0; s < 4; ++s)
                if (memcmp(before + 0x34 * a + 4 + 12 * s, G + rec(a, s), 12)) {
                    char t[64];
                    rec_text(rec(a, s), s >= 2, ps, t, sizeof t);
                    diag_event("padbind: captured %s slot %d: %s", action_name(a), s, t);
                }
        dump("bind captured");
    }
}

// ---------------- the loading screen's controller diagram (padprompts.c) ----------------
// The label of PlayStation diagram callout `slot` (padprompts.c ps_labels: 1 L1, 3 R1, 4 Triangle, 5 Circle, 6 Cross,
// 7 Square, 12 L2, 13 R2, 14 Create, 15 / 16 right stick up / down, 17 Options) from the action bound to that control
// (0: none, the callout hidden); the left stick (2) and the D-pad (8-11) keep `fallback`, as without a PlayStation pad.
uint32_t padbind_ps_label(int slot, uint32_t fallback) {
    static const struct { uint32_t t, o; } ctl[18] = {
        [1] = {T_BUTTON, P_L1}, [3] = {T_BUTTON, P_R1}, [4] = {T_BUTTON, P_TRIANGLE}, [5] = {T_BUTTON, P_CIRCLE},
        [6] = {T_BUTTON, P_CROSS}, [7] = {T_BUTTON, P_SQUARE}, [12] = {T_AXIS_HI, 2}, [13] = {T_ROT_HI, 2},
        [14] = {T_BUTTON, P_CREATE}, [15] = {T_ROT_LO, 1}, [16] = {T_ROT_HI, 1}, [17] = {T_BUTTON, P_OPTIONS}};
    // The game's texts for the Controls actions (hashes from the PS2 / X360 diagram tables; camera from the X360's RB).
    static const uint32_t label[GA_PAGE] = {0x619B23B0u, 0x6AB80AFAu, 0, 0, 0xB70FF4A4u, 0x018AE810u, 0x5E5C5F2Eu,
        0x56AFE190u, 0xDC1CD97Du, 0x1CEDC132u, 0, 0, 0, 0, 0, 0xD38DF194u, 0x13121940u, 0, 0x6369F4B8u};
    uint32_t dev = joy();
    if (slot < 1 || slot > 17 || !ctl[slot].t || !dev || !name_ps(joy_name(dev))) return fallback;
    for (int a = 0; a < GA_PAGE; ++a) {
        uint32_t effective[6];
        int preset = pedals_binding(a, effective);
        for (int s = 2; s < 4; ++s)
            if ((preset ? effective[3*(s-2)] : M32(rec(a, s))) == ctl[slot].t &&
                (preset ? effective[3*(s-2)+2] : M32(rec(a, s) + 8)) == ctl[slot].o) return label[a];
    }
    return 0;
}

// ---------------- the adaptive triggers' roles (padfx.m) ----------------
// What L2 (right = 0) or R2 (right = 1) drives in a race, from the joystick slots of the Controls actions: L2 is Z
// above its centre (T_AXIS_HI 2), R2 Rz above it (T_ROT_HI 2), or the L2 / R2 button records a capture may have made.
// Gas wins over brake, brake over the shifts; anything else bound there, or nothing: PADBIND_ROLE_NONE.
int padbind_trigger_role(int right) {
    uint32_t dev = joy();
    if (!dev || !name_ps(joy_name(dev))) return PADBIND_ROLE_NONE;
    uint32_t axis = right ? T_ROT_HI : T_AXIS_HI, button = right ? P_R2 : P_L2;
    int best = PADBIND_ROLE_NONE;
    for (int a = 0; a < GA_PAGE; ++a) {
        uint32_t effective[6];
        int preset = pedals_binding(a, effective);
        for (int s = 2; s < 4; ++s) {
            uint32_t t = preset ? effective[3*(s-2)] : M32(rec(a, s));
            uint32_t o = preset ? effective[3*(s-2)+2] : M32(rec(a, s) + 8);
            if (!((t == axis && o == 2) || (t == T_BUTTON && o == button))) continue;
            int role = a == A_GAS ? PADBIND_ROLE_GAS : a == A_BRAKE ? PADBIND_ROLE_BRAKE :
                       a == A_SHIFTUP || a == A_SHIFTDOWN ? PADBIND_ROLE_SHIFT : PADBIND_ROLE_OTHER;
            if (best == PADBIND_ROLE_NONE || role < best) best = role;
        }
    }
    return best;
}
