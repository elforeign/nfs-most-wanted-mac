// portmenu.m - the port's settings menu: F10 (Fn-F10 on Mac keyboards), the controller's touchpad click.
//
// What it shows: the settings registry (settings.h) by category, Picture / World / Cars & cutscenes / Mirror / Display &
// performance / Audio / Controls / Gameplay / Help, one row per setting with its value, in each setting's order
// key, with section headings; a one-sentence description of the selected row and the controls of the device in use
// (keys, or the layout's console buttons) below. Locked rows (an NFS_* switch is set) show the switch; rows that need a
// restart carry a ⟳ after the value (amber once a change is waiting for it).
//
// Input (user32.m hands everything to the menu while it's open; the game gets no keys, characters, mouse or pad):
//   keyboard  F10 or Esc close, Up/Down choose, Left/Right change, Enter/Space select (toggle, cycle, run, edit text),
                                                                                                   
//   mouse     hover, click a tab, click a value to change it (left arrow: back), drag a slider, scroll; a click
//             outside the panel closes
//   pad       the touchpad click (default; Controls > "Controller: open this menu": PS/Guide, L3+R3 held, Create/View
//             held) opens and closes; D-pad or left stick choose/change, Cross select, Circle back (closes),
//             L1/R1 category, Triangle default. Xbox: A, B, LB/RB, Y. Options stays the game's pause.
// Pause: in a race (game-flow 6) the menu freezes the game: pacing.c's AdvanceRealTime hook holds the real-time clock
// (portmenu_hold, as the probe hold does), so RealTimer, the Scheduler's target, stops: no sim ticks, sim time, race
// clock, AI and pursuit timers stand still while frames keep rendering (live graphics changes show on the frozen
// scene). The game's own PauseMenu sound state (SetSoundControlState 004B9080, state 1) pauses its sound effects
// unless the game's pause menu already did. Closing resumes from the same tick (the Scheduler's accumulator is
// untouched); the real-time ticker restarts from the last held pass, so no time jump.
//
// Drawing: a Core Animation layer over the game's layer at the window's backing scale (pixel-sharp at any game
// resolution, nothing drawn while closed) holding the panel, rasterized with CoreText into a bitmap at most once a
// frame and only when something changed (once a second for the live readouts), plus a dim layer over the game.
// BOOT_DIAG_SHOTS frames with the menu open also write menu_NNNNN.png (the panel with alpha) for composites.
#import <Cocoa/Cocoa.h>
#import <CoreText/CoreText.h>
#import <QuartzCore/QuartzCore.h>
#include <ctype.h>
#include <stdarg.h>

#include "d3d9_internal.h"
#include "settings.h"
#include "guide.h"
#include "report_github.h"
#include "onscreen_keyboard.h"
void osk_pass(Cpu *c);

#define GAMEFLOW_STATE 0x00925E90u       // 3 front end, 6 racing
#define SND_CTL_STATES 0x00911FE8u       // g_ActiveCtlStates (SetSoundControlState 004B9080)
const char *port_build_id(void);
void report_request_shot(void);
void report_note(const char *fmt, ...);
void user32_menu_opening(void);          // user32.m: release held keys and buttons to the game
void user32_menu_cursor(void);           // user32.m: the system cursor over the game while the menu is open
uint32_t call_guest(Cpu *c, uint32_t fn, int n, const uint32_t *args);

// ---------------- state (game thread) ----------------
static int is_open, tab, sel = -1, top, editing, dragging = -1, hover = -1, hover_tab = -1, dirty = 1;
static int device;                       // help line: 0 keyboard, 1 mouse, 2 controller
static char status[256];
static double status_until, next_refresh;
static uint8_t swallowed[256];           // key-downs the menu took: their key-ups go to it too

#define PW 980.0                         // the panel in design points (scaled down to fit small windows)
#define PH 640.0
#define ROW 30.0
#define LIST_Y 116.0
#define LIST_H (PH - LIST_Y - 118.0)
#define VX (PW * 0.50)                   // value column
#define SLIDER_W 200.0
#define TAB_Y 64.0

int portmenu_is_open(void) { return is_open; }
void portmenu_status(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(status, sizeof status, fmt, ap);
    va_end(ap);
    status_until = host_ticks() / 1e9 + 8;
    dirty = 1;
}
void portmenu_open_folder(const char *path) {
    if (getenv("BOOT_KEYS")) { portmenu_status("%s (not opened in scripted runs)", path); return; }
    @autoreleasepool { [NSWorkspace.sharedWorkspace openURL:[NSURL fileURLWithPath:[NSString stringWithUTF8String:path]]]; }
}

// The rows of a category, in menu order (each setting's order key; settings.c).
static int rows(int cat, Setting **out, int cap) {
    if (cat == SET_DIAGNOSTICS) guide_refresh();
    return settings_rows(cat, out, cap);
}
static int selectable(const Setting *s) { return s->type != SET_HEADING && s->type != SET_INFO; }
static int value_of(const Setting *s) { return s->get ? s->get() : s->value; }
static int visible_rows(void) { return (int)(LIST_H / ROW); }
static void clamp_sel(void) {
    Setting *r[128];
    int n = rows(tab, r, 128);
    if (!n) { sel = -1; top = 0; return; }
    if (sel < 0 || sel >= n || !selectable(r[sel])) {
        int from = sel < 0 ? 0 : sel >= n ? n - 1 : sel;
        sel = -1;
        for (int i = from; i < n && sel < 0; ++i) if (selectable(r[i])) sel = i;
        for (int i = from; i >= 0 && sel < 0; --i) if (selectable(r[i])) sel = i;
    }
    int vis = visible_rows();
    if (sel >= 0 && sel < top) top = sel > 0 && r[sel - 1]->type == SET_HEADING ? sel - 1 : sel;
    if (sel >= top + vis) top = sel - vis + 1;
    if (top > n - vis) top = n - vis;
    if (top < 0) top = 0;
}
static void move(int d) {
    Setting *r[128];
    int n = rows(tab, r, 128);
    for (int i = sel + d; i >= 0 && i < n; i += d) if (selectable(r[i])) { sel = i; break; }
    clamp_sel();
    dirty = 1;
}
static void log_tab(void) {          // BOOT_DIAG: the tab and its rows ((-) not selectable), so scripts can count
    Setting *r[128];
    char ids[1024] = "";
    int n = rows(tab, r, 128);
    for (int i = 0; i < n; ++i) {
        size_t l = strlen(ids);
        snprintf(ids + l, sizeof ids - l, " %s%s%s", r[i]->id, selectable(r[i]) ? "" : "(-)", r[i]->locked ? "(locked)" : "");
    }
    diag_event("menu: tab %s:%s", settings_category_names[tab], ids);
}
static void set_tab(int t) {
    tab = (t + SET_NCATEGORIES) % SET_NCATEGORIES;
    sel = -1; top = 0; editing = 0; hover = -1;
    clamp_sel();
    dirty = 1;
    if (diag_on) log_tab();
}
static Setting *current(void) {
    Setting *r[128];
    int n = rows(tab, r, 128);
    return sel >= 0 && sel < n ? r[sel] : NULL;
}
static void changed(Setting *s) {
    char b[96];
    if (diag_on) diag_event("menu: %s = %s", s->id, settings_value_name(s, value_of(s), b, sizeof b));
    dirty = 1;
}
// Left/Right, a click on a value: step a choice (wrapping) or a slider.
static void change(Setting *s, int d) {
    if (!s || s->locked) return;
    if (s->type == SET_CHOICE || s->type == SET_TOGGLE) {
        int n = s->type == SET_TOGGLE && !s->choices ? 2 : s->nchoices;
        int v = !strcmp(s->id, "graphics.look_preset") ? settings_look_choice() : value_of(s);
        if (settings_set(s, (v + d + n) % n)) changed(s);
    } else if (s->type == SET_SLIDER) {
        if (settings_set(s, value_of(s) + d * (s->step > 0 ? s->step : 1))) changed(s);
    }
}
static void paste_text(void) {
    Setting *s = current();
    if (!is_open || !editing || !s || s->type != SET_TEXT) return;
    NSString *paste = [NSPasteboard.generalPasteboard stringForType:NSPasteboardTypeString];
    if (!paste) return;
    const char *p = paste.UTF8String; size_t used = 0;
    while (p && *p && used + 1 < sizeof s->text) {
        unsigned char ch = (unsigned char)*p++;
        if (ch >= 32 && ch < 127) s->text[used++] = (char)ch;
    }
    s->text[used] = 0; dirty = 1;
}
static void activate(Setting *s) {
    if (!s) return;
    if (s->type == SET_ACTION) { if (s->apply) s->apply(0); if (diag_on) diag_event("menu: run %s", s->id); dirty = 1; }
    else if (s->type == SET_TEXT) {
        editing = !editing; dirty = 1;
        if (editing) osk_note_begin(s->text, sizeof s->text, osk_last_pad());
        else osk_note_end(0);
    }
    else change(s, 1);
}
static void set_open(int o) {
    if (o == is_open) return;
    feedback_poll_pause();
    feedback_suppress(FEEDBACK_MENU, o); // stop before holding the sim; independent of the game's own pause menu
    if (o) {
        static id paste_monitor;
        if (!paste_monitor) paste_monitor = [NSEvent addLocalMonitorForEventsMatchingMask:NSEventMaskKeyDown handler:^NSEvent *(NSEvent *event) {
            if (is_open && editing && event.keyCode == 9 && (event.modifierFlags & NSEventModifierFlagCommand)) {
                paste_text(); return nil;
            }
            return event;
        }];
        user32_menu_opening();           // keys and buttons held now are released to the game first
        report_request_shot();           // the frame as it is, for a problem report
        clamp_sel();
    }
    if (!o) guide_close();
    if (!o) report_github_close();
    is_open = o;
    editing = 0; dragging = -1; hover = -1;
    dirty = 1;
    user32_menu_cursor();
    report_note("menu %s", o ? "opened" : "closed");
    fprintf(stderr, "boot: settings menu %s\n", o ? "opened" : "closed");
    if (diag_on && o) log_tab();
    if (diag_on) diag_event("menu: %s (tab %s, game-flow %u)", o ? "open" : "closed", settings_category_names[tab], M32(GAMEFLOW_STATE));
}
void portmenu_toggle(void) { set_open(!is_open); }

// ---------------- pause (pacing.c, every main-loop pass) ----------------
int portmenu_hold(void) { return is_open && M32(GAMEFLOW_STATE) == 6; }
void portmenu_pass(Cpu *c) {
    osk_pass(c);
    feedback_poll_pause();                // even with no pad reads / DoFFB ticks while the game is paused
    static int sound_paused;             // we set the PauseMenu sound state (the game's pause menu hadn't)
    static uint32_t tag;
    int want = portmenu_hold();
    if (want == sound_paused || (want && (M32(SND_CTL_STATES) & 2))) return;
    if (!tag) tag = gstr("PortMenu");
    uint32_t args[3] = {(uint32_t)want, 1, tag}, esp = c->esp, ecx = c->ecx, eax = c->eax;
    call_guest(c, 0x004B9080u, 3, args);  // SetSoundControlState(bool, eSNDCTLSTATE, const char *), cdecl
    c->esp = esp; c->ecx = ecx; c->eax = eax;
    sound_paused = want;
    if (diag_on) diag_event("menu: game sound %s (control states %08X)", want ? "paused" : "resumed", M32(SND_CTL_STATES));
}

// ---------------- keyboard ----------------
// user32.m key_event, every key (scripted ones too): 1 = the menu took it.
int portmenu_key(uint8_t vk, int down, int repeat) {
    if (vk == 0x79) {                    // F10: also Fn-F10 on Mac keyboards (plain F10 is Mute there)
        if (down && !repeat) set_open(!is_open);
        swallowed[vk] = (uint8_t)down;
        return 1;
    }
    if (!is_open) {
        if (!down && swallowed[vk]) { swallowed[vk] = 0; return 1; }
        return 0;
    }
    swallowed[vk] = (uint8_t)down;
    if (!down) return 1;
    if (device != 0) { device = 0; dirty = 1; }
    if (report_github_active()) { report_github_key(vk); dirty = 1; return 1; }
    if (guide_active()) {
        switch (vk) {
        case 0x1B: case 0x08: guide_command(GUIDE_BACK); break;
        case 0x26: guide_command(GUIDE_UP); break;
        case 0x28: guide_command(GUIDE_DOWN); break;
        case 0x21: guide_command(GUIDE_PAGE_UP); break;
        case 0x22: guide_command(GUIDE_PAGE_DOWN); break;
        case 0x24: guide_command(GUIDE_HOME); break;
        case 0x23: guide_command(GUIDE_END); break;
        }
        dirty = 1;
        return 1;
    }
    Setting *s = current();
    if (editing) {
        if (vk == 0x0D || vk == 0x1B) {
            editing = 0; dirty = 1; osk_note_end(vk == 0x1B);
            if (vk == 0x0D && s && s->apply) s->apply(0);
        }
        else if (vk == 'V' && (NSEvent.modifierFlags & NSEventModifierFlagCommand) && s) {
            paste_text();
        }
        else if (vk == 0x08 && s && s->text[0]) { s->text[strlen(s->text) - 1] = 0; dirty = 1; }
        return 1;
    }
    int shift = (NSEvent.modifierFlags & NSEventModifierFlagShift) != 0;
    switch (vk) {
    case 0x1B: set_open(0); break;                          // Esc
    case 0x26: move(-1); break;                             // Up
    case 0x28: move(1); break;                              // Down
    case 0x25: change(s, -1); break;                        // Left
    case 0x27: change(s, 1); break;                         // Right
    case 0x0D: case 0x20: activate(s); break;               // Enter, Space
    case 0x09: set_tab(tab + (shift ? -1 : 1)); break;      // Tab
    case 'Q': set_tab(tab - 1); break;
    case 'E': set_tab(tab + 1); break;
    case 0x08: if (s && !s->locked) { settings_reset(s); changed(s); } break;   // Backspace: default
    case 0x21: for (int i = 0; i < visible_rows(); ++i) move(-1); break;       // PgUp
    case 0x22: for (int i = 0; i < visible_rows(); ++i) move(1); break;        // PgDn
    case 0x24: sel = 0; clamp_sel(); dirty = 1; break;      // Home
    case 0x23: sel = 1000; clamp_sel(); dirty = 1; break;   // End
    default: break;
    }
    return 1;
}
// Characters (WM_CHAR): the note being typed; all of them are the menu's while it's open.
int portmenu_char(uint32_t ch) {
    if (!is_open) return 0;
    if (report_github_active()) return 1;
    Setting *s = current();
    if (editing && s && s->type == SET_TEXT && ch >= 32 && ch < 256 && ch != 127) {
        size_t n = strlen(s->text);
        if (n + 1 < sizeof s->text) { s->text[n] = (char)ch; s->text[n + 1] = 0; dirty = 1; }
    }
    return 1;
}

// ---------------- geometry (design points, top-left origin) ----------------
static double fit_scale(CGSize view) {
    double f = fmin((view.width - 24) / PW, (view.height - 24) / PH);
    double cap = guide_scale_cap();
    return f > cap ? cap : f < 0.3 ? 0.3 : f;
}
static void panel_origin(CGSize view, double f, double *ox, double *oy) { *ox = (view.width - PW * f) / 2; *oy = (view.height - PH * f) / 2; }
static double tab_x[SET_NCATEGORIES + 1];  // tab i spans tab_x[i]..tab_x[i+1] (set while drawing)
static int row_at(double y) {
    if (y < LIST_Y || y >= LIST_Y + visible_rows() * ROW) return -1;
    return top + (int)((y - LIST_Y) / ROW);
}

// ---------------- mouse ----------------
// user32.m pump: kind 0 move, 1 down, 2 up, 3 drag, 4 scroll (dy lines); x, y in view points (top-left). 1 = taken.
int portmenu_mouse(int kind, double vx, double vy, double dy) {
    if (!is_open || !layer) return 0;
    CGSize view = layer.bounds.size;
    double f = fit_scale(view), ox, oy;
    panel_origin(view, f, &ox, &oy);
    double x = (vx - ox) / f, y = (vy - oy) / f;
    if (device != 1 && kind != 0) { device = 1; dirty = 1; }
    if (report_github_active()) { report_github_mouse(kind, x, y, dy); dirty = 1; return 1; }
    if (guide_active()) {
        if (kind == 4) guide_scroll(-dy);
        if (kind == 1 && x >= PW - 160 && y >= 44 && y < 100) guide_command(GUIDE_BACK);
        dirty = 1;
        return 1;
    }
    Setting *r[128];
    int n = rows(tab, r, 128);
    int row = row_at(y);
    if (row >= n) row = -1;
    if (kind == 0 || kind == 3) {
        if (kind == 0 && device != 1) { device = 1; dirty = 1; }
        int h = row >= 0 && selectable(r[row]) ? row : -1, ht = -1;
        for (int t = 0; t < SET_NCATEGORIES; ++t) if (y >= TAB_Y - 6 && y < TAB_Y + 30 && x >= tab_x[t] && x < tab_x[t + 1]) ht = t;
        if (h != hover || ht != hover_tab) { hover = h; hover_tab = ht; dirty = 1; }
        if (kind == 3 && dragging >= 0 && dragging < n) {
            Setting *s = r[dragging];
            double t = (x - (VX + 24)) / SLIDER_W;
            int v = s->min + (int)lround((t < 0 ? 0 : t > 1 ? 1 : t) * (s->max - s->min));
            if (settings_set(s, v)) changed(s);
        }
        return 1;
    }
    if (kind == 2) { dragging = -1; return 1; }
    if (kind == 4) {
        top -= (int)lround(dy);
        int vis = visible_rows();
        if (top > n - vis) top = n - vis;
        if (top < 0) top = 0;
        dirty = 1;
        return 1;
    }
    // kind 1: a click
    if (x < 0 || y < 0 || x > PW || y > PH) { set_open(0); return 1; }
    for (int t = 0; t < SET_NCATEGORIES; ++t)
        if (y >= TAB_Y - 6 && y < TAB_Y + 30 && x >= tab_x[t] && x < tab_x[t + 1]) { set_tab(t); return 1; }
    if (row < 0 || !selectable(r[row])) return 1;
    sel = row; editing = 0; dirty = 1;
    Setting *s = r[row];
    if (x >= VX) {
        if (s->type == SET_SLIDER && !s->locked) { dragging = row; return portmenu_mouse(3, vx, vy, 0); }
        if (s->type == SET_CHOICE && x < VX + 20) change(s, -1);
        else activate(s);
    } else if (s->type == SET_ACTION || s->type == SET_TEXT) activate(s);
    return 1;
}

// ---------------- controller ----------------
// user32.m pad_read, every read (open or not). p: the pad's physical buttons (P_* bits as user32.m), D-pad, left
// stick, PS/Guide (home) and touchpad click. Returns the physical buttons the game must not see (~0: everything);
// *inject gets buttons to send to the game anyway (a Create/View tap that wasn't a hold).
enum { B_CROSS = 1, B_CIRCLE = 2, B_SQUARE = 4, B_TRIANGLE = 8, B_L1 = 16, B_R1 = 32, B_CREATE = 64, B_OPTIONS = 128, B_L3 = 256, B_R3 = 512 };
uint32_t portmenu_pad(uint32_t phys, int up, int down, int left, int right, float lx, float ly, int home, int touchpad, int has_touchpad,
                      uint32_t *inject) {
    *inject = 0;
    double now = host_ticks() / 1e9;
    static uint32_t last_phys, hold_after_close;
    static GuideShortcut shortcut;
    GuideShortcutResult input = guide_shortcut(&shortcut, now, phys, home, touchpad, has_touchpad,
        settings_get("controls.menu_button"), settings_get("controls.guide_button") == 0, is_open);
    uint32_t hide = input.hide;
    *inject = input.inject;
    int trig = input.action != 0;
    if (trig && diag_on) diag_event("pad: %s shortcut", input.action == 2 ? "guide" : "menu");
    if (input.action == 2) {
        set_tab(SET_DIAGNOSTICS);
        set_open(1);
        guide_open(0);
        dirty = 1;
    } else if (input.action == 1) {
        set_open(!is_open);
        if (!is_open) hold_after_close = phys;
    }
    if (!is_open) {
        hold_after_close &= phys;        // buttons held when it closed stay away from the game until released
        last_phys = phys;
        return hide | hold_after_close;
    }
    // The opening sample belongs entirely to the shortcut, including stick/D-pad navigation.
    if (trig) { last_phys = phys; return ~0u; }
    // navigation with repeat
    int dir_up = up || ly > 0.5f, dir_down = down || ly < -0.5f, dir_left = left || lx < -0.5f, dir_right = right || lx > 0.5f;
    int dir = dir_up ? 1 : dir_down ? 2 : dir_left ? 3 : dir_right ? 4 : 0;
    static int last_dir;
    static double dir_next;
    if (dir && (dir != last_dir || now >= dir_next)) {
        dir_next = now + (dir != last_dir ? 0.40 : 0.09);
        Setting *s = current();
        if (report_github_active()) { report_github_pad(dir, 0); dirty = 1; }
        else if (guide_active()) { if (dir == 1 || dir == 2) { guide_command(dir == 1 ? GUIDE_UP : GUIDE_DOWN); dirty = 1; } }
        else if (dir == 1) move(-1); else if (dir == 2) move(1); else if (!editing) change(s, dir == 3 ? -1 : 1);
    }
    last_dir = dir;
    uint32_t edge = phys & ~last_phys;
    last_phys = phys;
    if ((edge || dir) && device != 2) { device = 2; dirty = 1; }
    if (report_github_active()) { report_github_pad(0, edge); if (edge) dirty = 1; return ~0u; }
    if (guide_active()) {
        if (edge & B_CIRCLE) guide_command(GUIDE_BACK);
        if (edge & B_L1) guide_command(GUIDE_PAGE_UP);
        if (edge & B_R1) guide_command(GUIDE_PAGE_DOWN);
        if (edge) dirty = 1;
        return ~0u;
    }
    Setting *s = current();
    if (edge & B_CROSS) activate(s);
    if (edge & B_CIRCLE) { if (editing) { editing = 0; dirty = 1; } else { set_open(0); hold_after_close = phys; } }
    if (edge & B_TRIANGLE) { if (s && !s->locked) { settings_reset(s); changed(s); } }
    if (edge & B_L1) set_tab(tab - 1);
    if (edge & B_R1) set_tab(tab + 1);
    return ~0u;
}

// ---------------- drawing ----------------
static CTFontRef fnt(int which, double size) {
    static CTFontRef f[3][4];
    static double fs[3][4];
    int k = size <= 12 ? 0 : size <= 15 ? 1 : size <= 18 ? 2 : 3;
    if (!f[which][k] || fs[which][k] != size) {
        if (f[which][k]) CFRelease(f[which][k]);
        f[which][k] = CTFontCreateUIFontForLanguage(which == 1 ? kCTFontUIFontEmphasizedSystem : which == 2 ? kCTFontUIFontUserFixedPitch
                                                                                                  : kCTFontUIFontSystem, size, NULL);
        fs[which][k] = size;
    }
    return f[which][k];
}
static CGColorRef rgba(double r, double g, double b, double a) { return CGColorCreateSRGB(r, g, b, a); }
static CGColorRef c_text, c_dim, c_faint, c_accent, c_amber, c_bg, c_line, c_sel, c_hover, c_dark, c_tab;
static void colors(void) {
    if (c_text) return;
    c_tab = rgba(0.08, 0.06, 0.04, 1);
    c_text = rgba(0.95, 0.96, 0.97, 1); c_dim = rgba(0.62, 0.66, 0.70, 1); c_faint = rgba(0.45, 0.48, 0.52, 1);
    c_accent = rgba(1.00, 0.62, 0.20, 1); c_amber = rgba(1.00, 0.78, 0.30, 1); c_bg = rgba(0.055, 0.062, 0.075, 0.94);
    c_line = rgba(1, 1, 1, 0.10); c_sel = rgba(1, 1, 1, 0.10); c_hover = rgba(1, 1, 1, 0.05); c_dark = rgba(1, 1, 1, 0.16);
}
// CoreText layout is independent of the destination bitmap. Keep a bounded working set
// of exact text/font/color combinations; drawing still uses the original CTLine/CTFrame.
static NSMutableDictionary<NSArray *, id> *text_layouts;
static id cached_layout(const char *s, CTFontRef f, CGColorRef col, int frame) {
    if (!s) return nil;
    NSString *str = [[NSString alloc] initWithUTF8String:s];
    if (!str) return nil;
    if (!text_layouts) text_layouts = [NSMutableDictionary dictionary];
    NSArray *key = @[str, (__bridge id)f, (__bridge id)col, @(frame)];
    id found = text_layouts[key];
    if (found) return found;
    NSDictionary *attr = @{(__bridge id)kCTFontAttributeName:(__bridge id)f,
                          (__bridge id)kCTForegroundColorAttributeName:(__bridge id)col};
    NSAttributedString *as = [[NSAttributedString alloc] initWithString:str attributes:attr];
    id result = frame ? CFBridgingRelease(CTFramesetterCreateWithAttributedString((__bridge CFAttributedStringRef)as)) :
                        CFBridgingRelease(CTLineCreateWithAttributedString((__bridge CFAttributedStringRef)as));
    if (text_layouts.count >= 512) [text_layouts removeAllObjects];
    if (result) text_layouts[key] = result;
    return result;
}
// Text at x (align 0 left, 1 right edge, 2 centre), its top at y; returns the width.
static double text(CGContextRef ctx, const char *s, double x, double y, CTFontRef f, CGColorRef col, int align) {
    if (!s || !*s) return 0;
    CTLineRef line = (__bridge CTLineRef)cached_layout(s, f, col, 0);
    if (!line) return 0;
    double w = CTLineGetTypographicBounds(line, NULL, NULL, NULL);
    double px = align == 1 ? x - w : align == 2 ? x - w / 2 : x;
    CGContextSetTextPosition(ctx, px, PH - (y + CTFontGetAscent(f)));
    CTLineDraw(line, ctx);
    return w;
}
static void wrapped(CGContextRef ctx, const char *s, double x, double y, double w, double h, CTFontRef f, CGColorRef col) {
    CTFramesetterRef fs = (__bridge CTFramesetterRef)cached_layout(s, f, col, 1);
    if (!fs) return;
    CGPathRef path = CGPathCreateWithRect(CGRectMake(x, PH - y - h, w, h), NULL);
    CTFrameRef fr = CTFramesetterCreateFrame(fs, CFRangeMake(0, 0), path, NULL);
    CTFrameDraw(fr, ctx);
    CFRelease(fr); CGPathRelease(path);
}
static void fill(CGContextRef ctx, double x, double y, double w, double h, double r, CGColorRef col) {
    CGContextSetFillColorWithColor(ctx, col);
    CGRect rc = CGRectMake(x, PH - y - h, w, h);
    if (r > 0) { CGPathRef p = CGPathCreateWithRoundedRect(rc, fmin(r, w / 2), fmin(r, h / 2), NULL); CGContextAddPath(ctx, p); CGContextFillPath(ctx); CGPathRelease(p); }
    else CGContextFillRect(ctx, rc);
}

static const char *pad_close_name(void) {
    switch (settings_get("controls.menu_button")) {
    case 1: return user32_pad_layout_xbox() ? "Guide" : "PS";
    case 2: return "L3+R3";
    case 3: return user32_pad_layout_xbox() ? "View" : "Create";
    default: return "Touchpad";
    }
}
static void help_line(char *buf, size_t n) {
    if (device == 2) {
        int xb = user32_pad_layout_xbox();
        snprintf(buf, n, "D-pad  choose / change      %s  select      %s  back      %s  category      %s  default      %s  close",
                 xb ? "A" : "✕", xb ? "B" : "○", xb ? "LB RB" : "L1 R1", xb ? "Y" : "△", pad_close_name());
    } else if (device == 1) {
        snprintf(buf, n, "Click  select / change      Drag  sliders      Scroll  list      Click outside or F10  close");
    } else {
        snprintf(buf, n, "↑↓  choose      ←→  change      Enter  select      Tab / Q E  category      Backspace  default      Esc / F10  close");
    }
}

static CALayer *panel_layer, *dim_layer;
static CGContextRef last_ctx;            // the last panel bitmap (diag captures)
static size_t last_w, last_h;
static uint64_t panel_rasters, keyboard_full_rasters, keyboard_region_rasters, keyboard_changed_regions;

static CGContextRef panel_background;
static double panel_background_scale;
static Setting *readout_rows[128];
static char readout_text[128][160];
static void readout(Setting *s, int i, char *b, size_t n) {
    if (s->value_text) s->value_text(b, (int)n);
    readout_rows[i] = s;
    snprintf(readout_text[i], sizeof readout_text[i], "%s", b);
}
static CGContextRef bitmap(size_t W, size_t H, double sx, double sy) {
    CGColorSpaceRef cs = CGColorSpaceCreateWithName(kCGColorSpaceSRGB);
    CGContextRef ctx = CGBitmapContextCreate(NULL, W, H, 8, W * 4, cs,
        kCGImageAlphaPremultipliedFirst | kCGBitmapByteOrder32Little);
    CGColorSpaceRelease(cs);
    if (ctx) { CGContextScaleCTM(ctx, sx, sy); CGContextSetShouldSmoothFonts(ctx, false); }
    return ctx;
}
static int render(void) {
    colors();
    CGSize view = layer.bounds.size;
    id v = layer.delegate;
    CGFloat backing = [v isKindOfClass:NSView.class] && ((NSView *)v).window ? ((NSView *)v).window.backingScaleFactor : 2;
    double f = fit_scale(view), k = f * backing;
    size_t W = (size_t)ceil(PW * k), H = (size_t)ceil(PH * k);
    if (!panel_background || W != CGBitmapContextGetWidth(panel_background) ||
        H != CGBitmapContextGetHeight(panel_background) || k != panel_background_scale) {
        CGContextRef ctx = bitmap(W, H, k, k);
        if (!ctx) return 0;
        fill(ctx, 0, 0, PW, PH, 14, c_bg);
        CGContextSetStrokeColorWithColor(ctx, c_line);
        CGContextSetLineWidth(ctx, 1);
        CGPathRef border = CGPathCreateWithRoundedRect(CGRectMake(0.5, 0.5, PW - 1, PH - 1), 14, 14, NULL);
        CGContextAddPath(ctx, border); CGContextStrokePath(ctx); CGPathRelease(border);
        if (panel_background) CGContextRelease(panel_background);
        panel_background = ctx; panel_background_scale = k;
    }
    CGContextRef ctx = bitmap(W, H, k, k);
    if (!ctx) return 0;
    memcpy(CGBitmapContextGetData(ctx), CGBitmapContextGetData(panel_background), W * H * 4);
    memset(readout_rows, 0, sizeof readout_rows);
    if (report_github_active()) {
        report_github_draw(ctx, PH, CGRectMake(32, LIST_Y, PW - 64, LIST_H), user32_pad_layout_xbox());
    } else if (guide_active()) {
        guide_draw(ctx, PH, CGRectMake(32, LIST_Y, PW - 64, LIST_H), user32_pad_layout_xbox());
        text(ctx, "Back to Help", PW - 36, 62, fnt(0, 18), c_accent, 1);
    } else {
    // title
    text(ctx, "Settings", 28, 20, fnt(1, 22), c_text, 0);
    char b[512];
    snprintf(b, sizeof b, "NFSMW Mac port  ·  %s", port_build_id());
    text(ctx, b, PW - 28, 27, fnt(0, 12), c_faint, 1);
    // tabs
    double x = 28;
    const char *prev = device == 2 ? (user32_pad_layout_xbox() ? "LB" : "L1") : "Q", *next = device == 2 ? (user32_pad_layout_xbox() ? "RB" : "R1") : "E";
    x += text(ctx, prev, x, TAB_Y + 5, fnt(0, 12), c_faint, 0) + 12;
    for (int t = 0; t < SET_NCATEGORIES; ++t) {
        CTFontRef tf = fnt(t == tab ? 1 : 0, 14);
        CTLineRef line = (__bridge CTLineRef)cached_layout(settings_category_names[t], tf, c_text, 0);
        double w = CTLineGetTypographicBounds(line, NULL, NULL, NULL);
        tab_x[t] = x;
        if (t == tab) fill(ctx, x, TAB_Y - 4, w + 16, 30, 15, c_accent);
        else if (t == hover_tab) fill(ctx, x, TAB_Y - 4, w + 16, 30, 15, c_hover);
        text(ctx, settings_category_names[t], x + 8, TAB_Y + 1, tf, t == tab ? c_tab : c_text, 0);
        x += w + 20;
        tab_x[t + 1] = x - 6;
    }
    text(ctx, next, x + 6, TAB_Y + 5, fnt(0, 12), c_faint, 0);
    fill(ctx, 20, LIST_Y - 12, PW - 40, 1, 0, c_line);
    // rows
    Setting *r[128];
    int n = rows(tab, r, 128), vis = visible_rows();
    if (!n) {
        text(ctx, tab == SET_GAMEPLAY ? "Gameplay options are on their way." : "Nothing here yet.", PW / 2, LIST_Y + 80, fnt(0, 15), c_dim, 2);
    }
    for (int i = top; i < n && i < top + vis; ++i) {
        Setting *s = r[i];
        double y = LIST_Y + (i - top) * ROW, ty = y + 6;
        if (s->type == SET_HEADING) {
            char up[96];
            snprintf(up, sizeof up, "%s", s->label);
            for (char *p = up; *p; ++p) *p = (char)toupper((unsigned char)*p);
            double hw = text(ctx, up, 32, y + 10, fnt(1, 11), c_accent, 0);
            if (s->help) text(ctx, s->help, 32 + hw + 14, y + 9, fnt(0, 12), c_faint, 0);   // what the section is
            continue;
        }
        if (i == sel) { fill(ctx, 18, y, PW - 36, ROW - 2, 7, c_sel); fill(ctx, 18, y + 5, 3, ROW - 12, 1.5, c_accent); }
        else if (i == hover) fill(ctx, 18, y, PW - 36, ROW - 2, 7, c_hover);
        CGColorRef lc = s->locked ? c_dim : c_text;
        text(ctx, s->label, 32, ty, fnt(0, 15), s->type == SET_INFO ? c_dim : lc, 0);
        double vx = VX + 24;
        int val = value_of(s);
        char vb[512] = "";
        double w = 0;
        switch (s->type) {
        case SET_TOGGLE: case SET_CHOICE:
            settings_value_name(s, val, vb, sizeof vb);
            if (s->locked) { w = text(ctx, vb, vx, ty, fnt(0, 15), c_dim, 0); break; }
            if (i == sel || i == hover) { text(ctx, "‹", VX + 4, ty - 2, fnt(0, 18), c_dim, 0); }
            w = text(ctx, vb, vx, ty, fnt(i == sel ? 1 : 0, 15), c_text, 0);
            if (i == sel || i == hover) text(ctx, "›", vx + w + 10, ty - 2, fnt(0, 18), c_dim, 0), w += 18;
            break;
        case SET_SLIDER: {
            double t = s->max > s->min ? (double)(val - s->min) / (s->max - s->min) : 0;
            fill(ctx, vx, y + 12, SLIDER_W, 5, 2.5, c_dark);
            fill(ctx, vx, y + 12, SLIDER_W * t, 5, 2.5, s->locked ? c_dim : c_accent);
            fill(ctx, vx + SLIDER_W * t - 6, y + 8.5, 12, 12, 6, s->locked ? c_dim : c_text);
            settings_value_name(s, val, vb, sizeof vb);
            w = SLIDER_W + 12 + text(ctx, vb, vx + SLIDER_W + 14, ty, fnt(0, 15), s->locked ? c_dim : c_text, 0);
            break;
        }
        case SET_TEXT:
            if (editing && i == sel) {
                snprintf(vb, sizeof vb, "%s_", s->text);
                fill(ctx, vx - 6, y + 2, PW - 40 - vx, ROW - 6, 5, c_hover);
                w = text(ctx, vb, vx, ty, fnt(0, 15), c_amber, 0);
            } else w = text(ctx, s->text[0] ? s->text : "(none)", vx, ty, fnt(0, 15), s->text[0] ? c_text : c_faint, 0);
            break;
        case SET_ACTION:
            readout(s, i, vb, sizeof vb);
            w = text(ctx, vb[0] ? vb : i == sel ? (device == 2 ? (user32_pad_layout_xbox() ? "A" : "✕") : "Enter") : "", vx, ty,
                     fnt(0, 13), c_dim, 0);
            break;
        case SET_INFO:
            readout(s, i, vb, sizeof vb);
            if (!strcmp(s->id, "online.status")) {
                wrapped(ctx, vb, vx, ty, PW - vx - 36, ROW - 3, fnt(0, 11), c_text); w = 0;
            } else w = text(ctx, vb, vx, ty, fnt(0, 15), c_text, 0);
            break;
        default: break;
        }
        // tags: locked by a switch, after a restart
        double tx = vx + w + 14;
        if (s->locked && s->env) {
            snprintf(vb, sizeof vb, "set by %s=%s", s->env, s->env_text);
            if (strlen(vb) > 44) strcpy(vb + 41, "...");
            text(ctx, vb, tx, ty + 2, fnt(0, 12), c_faint, 0);
        } else if (s->restart) {            // ⟳: a change takes effect after Restart game (amber while one is waiting)
            int pending = val != s->boot_value;
            double rw = text(ctx, "\u27F3", vx + w + 8, ty - 1, fnt(0, 17), pending ? c_amber : c_dim, 0);
            if (pending) text(ctx, "after restart", vx + w + 12 + rw, ty + 2, fnt(0, 12), c_amber, 0);
        }
    }
    if (n > vis) {                       // scroll bar
        double h = LIST_H * vis / n, y = LIST_Y + (LIST_H - h) * top / (n - vis);
        fill(ctx, PW - 12, y, 4, h, 2, c_dark);
    }
    // description / status, help line
    fill(ctx, 20, PH - 108, PW - 40, 1, 0, c_line);
    double now = host_ticks() / 1e9;
    Setting *s = current();
    if (now < status_until) wrapped(ctx, status, 32, PH - 96, PW - 64, 44, fnt(0, 13), c_amber);
    else if (s && !strcmp(s->id, "graphics.look_preset") && settings_look_state() == PRESET_X360 && settings_look_partial()) {
        wrapped(ctx, "partial: your Xbox 360 files are not installed. Applies the available picture settings, closest to retail.",
                32, PH - 96, PW - 64, 44, fnt(0, 13), c_amber);
    } else if (s && !strcmp(s->id, "online.status") && s->value_text) {
        char detail[512]; s->value_text(detail, sizeof detail);
        wrapped(ctx, detail, 32, PH - 96, PW - 64, 44, fnt(0, 11), c_text);
    } else if (s && s->help) {
        wrapped(ctx, s->help, 32, PH - 96, PW - 64, 44, fnt(0, 13), c_dim);
    }
    help_line(b, sizeof b);
    text(ctx, b, PW / 2, PH - 38, fnt(0, 12), c_faint, 2);
    }
    // to the layer
    CGImageRef img = CGBitmapContextCreateImage(ctx);
    if (!img) { CGContextRelease(ctx); return 0; }
    double ox, oy;
    panel_origin(view, f, &ox, &oy);
    [CATransaction begin];
    [CATransaction setDisableActions:YES];
    panel_layer.contents = (__bridge id)img;
    panel_layer.contentsScale = backing;
    panel_layer.frame = CGRectMake(ox, oy, PW * f, PH * f);
    dim_layer.frame = layer.bounds;
    [CATransaction commit];
    CGImageRelease(img);
    if (last_ctx) CGContextRelease(last_ctx);
    last_ctx = ctx; last_w = W; last_h = H;
    if (diag_on) ++panel_rasters;
    return 1;
}

static void layers(int show) {
    if (!layer) return;
    if (!panel_layer && show) {
        NSDictionary *none = @{@"bounds": NSNull.null, @"position": NSNull.null, @"contents": NSNull.null, @"hidden": NSNull.null,
                               @"contentsScale": NSNull.null, @"frame": NSNull.null};
        dim_layer = [CALayer layer];
        dim_layer.backgroundColor = CGColorGetConstantColor(kCGColorBlack);
        dim_layer.opacity = 0.45f;
        dim_layer.actions = none;
        dim_layer.zPosition = 2;
        dim_layer.anchorPoint = CGPointZero;
        panel_layer = [CALayer layer];
        panel_layer.actions = none;
        panel_layer.zPosition = 3;
        panel_layer.anchorPoint = CGPointZero;
        panel_layer.contentsGravity = kCAGravityResize;
        [layer addSublayer:dim_layer];
        [layer addSublayer:panel_layer];
    }
    if (!panel_layer || panel_layer.hidden == !show) return;
    [CATransaction begin];
    [CATransaction setDisableActions:YES];
    panel_layer.hidden = dim_layer.hidden = !show;
    [CATransaction commit];
}

// A transparent upper half leaves the game's field visible above the keyboard.
static CALayer *keyboard_layer;
static CGContextRef keyboard_ctx;
typedef struct {
    unsigned key;
    int enabled, selected;
    char label[16];
} KeyboardKey;
typedef struct {
    size_t W, H;
    int xbox, count[5];
    char title[128], text[512];
    KeyboardKey keys[5][16];
} KeyboardPixels;
static KeyboardPixels keyboard_previous;
static CGContextRef keyboard_background;
// Restore from the pristine background in device pixels. A copy, rather than a
// blend, preserves the translucent background and antialiasing exactly.
static void keyboard_restore(CGContextRef ctx, CGRect rect) {
    size_t W = CGBitmapContextGetWidth(ctx), H = CGBitmapContextGetHeight(ctx);
    int x0 = (int)fmax(0, floor(rect.origin.x * W / PW));
    int x1 = (int)fmin(W, ceil(CGRectGetMaxX(rect) * W / PW));
    int y0 = (int)fmax(0, floor(rect.origin.y * H / PH));
    int y1 = (int)fmin(H, ceil(CGRectGetMaxY(rect) * H / PH));
    unsigned char *dst = CGBitmapContextGetData(ctx), *src = CGBitmapContextGetData(keyboard_background);
    for (int y = y0; y < y1; ++y) memcpy(dst + (y * W + x0) * 4, src + (y * W + x0) * 4, (x1 - x0) * 4);
}
static void keyboard_update(void) {
    int show = osk_visible();
    if (!layer) return;
    if (!keyboard_layer && show) {
        keyboard_layer = [CALayer layer];
        keyboard_layer.actions = @{@"contents": NSNull.null, @"bounds": NSNull.null, @"position": NSNull.null,
                                   @"hidden": NSNull.null, @"contentsScale": NSNull.null};
        keyboard_layer.zPosition = 4;
        keyboard_layer.anchorPoint = CGPointZero;
        [layer addSublayer:keyboard_layer];
    }
    if (!keyboard_layer) return;
    keyboard_layer.hidden = !show;
    if (!show) return;
    const OskCapture *s = osk_capture();
    CGSize view = layer.bounds.size;
    id v = layer.delegate;
    CGFloat backing = [v isKindOfClass:NSView.class] && ((NSView *)v).window ? ((NSView *)v).window.backingScaleFactor : 2;
    double f = fmin(fit_scale(view), fmax(0.3, (view.height * 0.46 - 12) / (PH - 298)));
    KeyboardPixels pixels = {0};
    pixels.W = (size_t)ceil(PW * f * backing); pixels.H = (size_t)ceil(PH * f * backing);
    pixels.xbox = osk_xbox();
    snprintf(pixels.title, sizeof pixels.title, "%s  %u / %u", s->note ? "Note" : "Text", s->length, s->limit);
    snprintf(pixels.text, sizeof pixels.text, "%s", s->text);
    for (int r = 0; r < 5; ++r) {
        pixels.count[r] = osk_key_count(r);
        for (int col = 0; col < pixels.count[r]; ++col) {
            KeyboardKey *key = &pixels.keys[r][col];
            key->key = osk_key(r, col); key->enabled = osk_key_enabled(key->key); key->selected = osk_selected(r, col);
            char label[16];
            snprintf(key->label, sizeof key->label, "%s", osk_key_label(key->key, label));
        }
    }
    // Position can change without changing the bitmap (for example a wider window).
    double ox = (view.width - PW * f) / 2, oy = view.height - PH * f - 12;
    CGRect frame = CGRectMake(ox, oy, PW * f, PH * f);
    if (!CGRectEqualToRect(keyboard_layer.frame, frame) || keyboard_layer.contentsScale != backing) {
        [CATransaction begin]; [CATransaction setDisableActions:YES];
        keyboard_layer.contentsScale = backing;
        keyboard_layer.frame = frame;
        [CATransaction commit];
    }
    if (keyboard_ctx && !memcmp(&pixels, &keyboard_previous, sizeof pixels)) return;
    size_t W = pixels.W, H = pixels.H;
    if (!W || !H) return;
    uint64_t raster_start = diag_on ? host_ticks() : 0;
    colors();
    int full = !keyboard_ctx || W != keyboard_previous.W || H != keyboard_previous.H ||
               pixels.xbox != keyboard_previous.xbox || memcmp(pixels.count, keyboard_previous.count, sizeof pixels.count);
    CGContextRef ctx = bitmap(W, H, (double)W / PW, (double)H / PH);
    if (!ctx) return;
    // Match the original keyboard's default font smoothing (the panel disables it).
    CGContextSetShouldSmoothFonts(ctx, true);
    if (!keyboard_background || W != CGBitmapContextGetWidth(keyboard_background) || H != CGBitmapContextGetHeight(keyboard_background)) {
        CGContextRef bg = bitmap(W, H, (double)W / PW, (double)H / PH);
        if (!bg) { CGContextRelease(ctx); return; }
        fill(bg, 12, 298, PW - 24, PH - 304, 12, c_bg);
        if (keyboard_background) CGContextRelease(keyboard_background);
        keyboard_background = bg;
    }
    // New storage avoids modifying a bitmap still held by Core Animation.
    memcpy(CGBitmapContextGetData(ctx), CGBitmapContextGetData(full ? keyboard_background : keyboard_ctx), W * H * 4);
    int text_changed = full || strcmp(pixels.title, keyboard_previous.title) || strcmp(pixels.text, keyboard_previous.text);
    if (text_changed) {
        if (!full) keyboard_restore(ctx, CGRectMake(28, 306, PW - 54, 36));
        text(ctx, pixels.title, 32, 310, fnt(1, 16), c_accent, 0);
        CGContextSaveGState(ctx);
        CGContextClipToRect(ctx, CGRectMake(215, PH - 339, PW - 245, 26));
        text(ctx, pixels.text, 220, 309, fnt(0, 17), c_text, 0);
        CGContextRestoreGState(ctx);
    }
    unsigned regions = text_changed;
    for (int r = 0; r < 5; ++r) {
        int count = pixels.count[r];
        double width = r == 4 ? 240 : count > 10 ? 54 : 72;
        double x = (PW - count * (width + 6) + 6) / 2;
        for (int col = 0; col < count; ++col) {
            KeyboardKey *key = &pixels.keys[r][col];
            if (!full && !memcmp(key, &keyboard_previous.keys[r][col], sizeof *key)) continue;
            double xx = x + col * (width + 6), y = 350 + r * 44;
            if (!full) keyboard_restore(ctx, CGRectMake(xx - 2, y - 2, width + 4, 42));
            fill(ctx, xx, y, width, 38, 5, key->selected ? c_accent : c_sel);
            text(ctx, key->label, xx + width / 2, y + 9, fnt(1, 16),
                 key->enabled ? key->selected ? c_bg : c_text : c_faint, 2);
            ++regions;
        }
    }
    if (full) {
        text(ctx, pixels.xbox ? "A  Type     B  Delete     X  Accept     Y  Cancel" : "✕  Type     ○  Delete     □  Accept     △  Cancel",
             PW / 2, 578, fnt(1, 15), c_text, 2);
        text(ctx, pixels.xbox ? "D-pad / Left stick  Move       LB  Shift       RB  Symbols" :
                             "D-pad / Left stick  Move       L1  Shift       R1  Symbols", PW / 2, 606, fnt(0, 12), c_dim, 2);
    }
    CGImageRef img = CGBitmapContextCreateImage(ctx);
    if (!img) { CGContextRelease(ctx); return; }
    [CATransaction begin]; [CATransaction setDisableActions:YES];
    keyboard_layer.contents = (__bridge id)img;
    [CATransaction commit];
    CGImageRelease(img);
    if (keyboard_ctx) CGContextRelease(keyboard_ctx);
    keyboard_ctx = ctx; keyboard_previous = pixels;
    if (diag_on) {
        if (full) ++keyboard_full_rasters; else ++keyboard_region_rasters;
        keyboard_changed_regions += regions;
    }
    if (diag_on) diag_event("osk: raster full=%llu regions=%llu changed=%u ms=%.3f generation=%llu length=%u owner=%d",
        (unsigned long long)keyboard_full_rasters, (unsigned long long)keyboard_region_rasters, regions,
        (host_ticks() - raster_start) / 1e6, (unsigned long long)s->generation, s->length, s->owner);
}

// The panel follows: every Present (diag.c) and after each batch of input (user32.m pump). Nothing while closed.
void portmenu_update(void) {
    if (editing && !osk_note_active()) { editing = 0; dirty = 1; }
    if (osk_note_active() && (!is_open || !editing)) osk_note_end(0);
    @autoreleasepool { keyboard_update(); }
    if (!is_open) { if (panel_layer && !panel_layer.hidden) layers(0); return; }
    // Keep the redraw pending until the window can hold a bitmap.
    if (!layer || layer.bounds.size.width <= 0 || layer.bounds.size.height <= 0) return;
    double now = host_ticks() / 1e9;
    static CGSize last_size;
    static CGFloat last_backing;
    id view = layer.delegate;
    CGFloat backing = [view isKindOfClass:NSView.class] && ((NSView *)view).window ? ((NSView *)view).window.backingScaleFactor : 2;
    if (!CGSizeEqualToSize(layer.bounds.size, last_size) || backing != last_backing) {
        last_size = layer.bounds.size; last_backing = backing; dirty = 1;
    }
    if (now >= next_refresh) {           // live readouts (once a second, on tabs that have them), the status line's timeout
        next_refresh = now + 1;
        if (!guide_active() && !report_github_active()) {
            Setting *r[128];
            int n = rows(tab, r, 128);
            for (int i = top; i < n && i < top + visible_rows(); ++i) {
                if (r[i]->type != SET_INFO && r[i]->type != SET_ACTION) continue;
                char b[160] = "";
                if (r[i]->value_text) r[i]->value_text(b, sizeof b);
                if (readout_rows[i] != r[i] || strcmp(readout_text[i], b)) dirty = 1;
            }
            if (status_until && now >= status_until) { status_until = 0; dirty = 1; }
        }
    }
    if (!dirty) return;
    @autoreleasepool {
        // First open must create/attach the layers before render assigns their contents and frames.
        layers(1);
        uint64_t t0 = diag_on ? host_ticks() : 0;
        if (!render()) return;
        dirty = 0;
        if (diag_on) diag_event("menu: raster full=%llu panel %zux%zu ms=%.3f (layers attached %d, visible %d)",
            (unsigned long long)panel_rasters, last_w, last_h, (host_ticks() - t0) / 1e6,
            panel_layer.superlayer == layer && dim_layer.superlayer == layer,
            !panel_layer.hidden && !dim_layer.hidden && panel_layer.contents != nil);
    }
}
void portmenu_frame(void) {
    portmenu_update();
    if (osk_visible() && diag_on && keyboard_ctx && diag_shot_due(dev.frames) == 2) {
        char path[1024];
        snprintf(path, sizeof path, "%s/keyboard_%05llu.png", diag_dir, (unsigned long long)dev.frames);
        diag_png(path, CGBitmapContextGetData(keyboard_ctx), (uint32_t)CGBitmapContextGetWidth(keyboard_ctx),
                 (uint32_t)CGBitmapContextGetHeight(keyboard_ctx), (uint32_t)CGBitmapContextGetBytesPerRow(keyboard_ctx), 1);
        if (diag_on) diag_event("osk: capture %s frame=(%.0f,%.0f,%.0f,%.0f)", path,
                               keyboard_layer.frame.origin.x, keyboard_layer.frame.origin.y,
                               keyboard_layer.frame.size.width, keyboard_layer.frame.size.height);
    }
    if (is_open && diag_on && last_ctx && getenv("BOOT_DIAG_SHOTS") && diag_frame_wanted(dev.frames, "BOOT_DIAG_SHOTS")) {
        char path[1024];
        snprintf(path, sizeof path, "%s/menu_%05llu.png", diag_dir, (unsigned long long)dev.frames);
        diag_png(path, CGBitmapContextGetData(last_ctx), (uint32_t)last_w, (uint32_t)last_h, (uint32_t)CGBitmapContextGetBytesPerRow(last_ctx), 1);
        CGSize v = layer.bounds.size;
        diag_event("menu: capture %s (panel %zux%zu in a %.0fx%.0f pt view, tab %s, row %d)", path, last_w, last_h, v.width, v.height,
                   settings_category_names[tab], sel);
    }
}
