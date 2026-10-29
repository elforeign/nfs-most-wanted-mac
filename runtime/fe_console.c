// fe_console.c - front-end items the PC has and the X360 doesn't, or that don't work in the port.
// Each is a hook on a game function (recomp/hooks.txt); switches are in runtime/README.md.
#include <stdlib.h>
#include <string.h>
#include <math.h>

#include "host.h"
#include "brightness.h"
#include "settings.h"

void render_set_gamma(const uint16_t ramp[768]);
static float brightness_value;
static int brightness_loaded;
float brightness_get(void) {
    if (!brightness_loaded) {
        brightness_loaded = 1;
        brightness_value = BRIGHTNESS_IDENTITY;
        const char *p = app_pref("graphics.brightness");
        if (p && *p) {
            char *end;
            float s = strtof(p, &end);
            if (end != p && !*end && isfinite(s) && s >= 0 && s <= 1) brightness_value = s;
        }
    }
    return brightness_value;
}
void brightness_preview(float s) {
    if (!isfinite(s)) return;
    brightness_get();
    brightness_value = fminf(1, fmaxf(0, s));
    uint16_t ramp[768];
    brightness_ramp(brightness_value, ramp);
    render_set_gamma(ramp);
}
void brightness_save(void) {
    char value[32];
    snprintf(value, sizeof value, "%.9g", brightness_get());
    app_pref_set("graphics.brightness", value);
}
void brightness_init(void) { brightness_preview(brightness_get()); }

// The F10 menu displays whole percentages, like the native slider's scale 100.
// Its rounded default (BRIGHTNESS_IDENTITY_PCT) selects exact identity, including after Reset.
static int brightness_percent(void) { return (int)lroundf(brightness_get() * 100); }
static void brightness_options_rebase(void);
static char brightness_help[120];
static void brightness_percent_apply(int percent) {
    brightness_preview(percent == BRIGHTNESS_IDENTITY_PCT ? BRIGHTNESS_IDENTITY : percent / 100.0f);
    brightness_save();
    brightness_options_rebase();
}
static Setting s_brightness = {.id = "graphics.brightness", .category = SET_PICTURE, .order = 20, .label = "Brightness",
    .help = brightness_help,
    .type = SET_SLIDER, .min = 0, .max = 100, .step = 1, .format = "%d%%", .def = BRIGHTNESS_IDENTITY_PCT,
    .pref = "", .own_pref = 1, .get = brightness_percent, .apply = brightness_percent_apply};
__attribute__((constructor)) static void brightness_register(void) {
    snprintf(brightness_help, sizeof brightness_help, "Brightness of the whole picture, HUD included; the default %d%% is the original picture.", BRIGHTNESS_IDENTITY_PCT);
    settings_add(&s_brightness);
}

// MainLAN follows the original menu/login state machine. F10's saved choice
// takes effect at restart; an explicit NFS_LAN keeps its original exact-1 override.
// Dead MainOnline stays hidden in LAN mode; the legacy online switch is unchanged.
static int lan_items(void) {
    const char *e = getenv("NFS_LAN");
    if (e) return !strcmp(e, "1");
    const Setting *s = settings_find("gameplay.lan");
    return s && s->boot_value == 1;
}
static int online_items(void) {
    static int v = -1;
    if (v < 0) { const char *e = getenv("NFS_FE_ONLINE_ITEMS"); v = e && *e == '1'; }
    return v;
}
void f_00573960_orig(Cpu *c);
void f_00573960(Cpu *c) {                                  // IconScrollerMenu::AddOption(this = ecx, IconOption *)
    uint32_t opt = M32(c->esp + 4);
    if (opt) {
        uint32_t name = M32(opt + 0x2C);
        int hidden = name == 0xD6DB74DEu ? !lan_items() && !online_items() :
                     name == 0x74FB90A8u ? lan_items() || !online_items() : 0;
        if (hidden) {
            if (diag_on) diag_event("fe_console: main menu item %08X not added", name);
            c->esp += 8;                                   // ret 4
            return;
        }
        if (name == 0xD6DB74DEu && diag_on) diag_event("lan-menu: MainLAN added");
    }
    f_00573960_orig(c);
}

// Options > Audio > Audio Mode (F15): the row offers Mono / Stereo but the port always outputs discrete 5.1 (dsound.c
// SetOutputMode), and its value text doesn't show on this screen, so it only misleads; the port's Audio output menu
// (app menu, F10) is the real control. UIOptionsScreen::SetupAudio builds it with UIWidgetMenu::AddToggleOption
// (00588570, thiscall, args widget + bool): the AOAudioMode widget is recognised by its vtable holding Act 0050F600
// and Draw 0051B2D0, and is never added, so the other rows close up and navigation never sees it.
// NFS_FE_AUDIO_MODE=1 puts it back.
// F14: the Advanced video rows replaced by the X360 post chain use the same add call.
// Match both Act and Draw, so unrelated widgets are kept. PC post processing keeps the native rows/labels.
int x360_post_chain_on(void);
static const struct { uint32_t act, draw; const char *name; } k_post_rows[] = {
    {0x0050FF10u, 0x0051C030u, "Visual Treatment"},
    {0x0050FCA0u, 0x0051BCA0u, "Over Bright"},
    // SetupVideo (005295F0) does not construct a Motion Blur widget in this PC executable.
};
void f_00588570_orig(Cpu *c);
void f_00588570(Cpu *c) {                                  // UIWidgetMenu::AddToggleOption(this = ecx, FEToggleWidget *, bool)
    static int keep = -1;
    if (keep < 0) { const char *e = getenv("NFS_FE_AUDIO_MODE"); keep = e && *e == '1'; }
    uint32_t w = M32(c->esp + 4);
    if (w) {
        uint32_t vt = M32(w);
        uint32_t funcs[16] = {0};
        if (vt > 0x10000 && vt < 0x01000000u)
            for (int i = 0; i < 16; ++i) funcs[i] = M32(vt + 4 * i);
        int act = 0, draw = 0;
        for (int i = 0; i < 16; ++i) { act |= funcs[i] == 0x0050F600u; draw |= funcs[i] == 0x0051B2D0u; }
        const char *hidden = !keep && act && draw ? "Audio > Audio Mode" : NULL;
        if (x360_post_chain_on())
            for (size_t row = 0; row < sizeof k_post_rows / sizeof *k_post_rows; ++row) {
                act = draw = 0;
                for (int i = 0; i < 16; ++i) { act |= funcs[i] == k_post_rows[row].act; draw |= funcs[i] == k_post_rows[row].draw; }
                if (act && draw) { hidden = k_post_rows[row].name; break; }
            }
        if (hidden) {
            if (diag_on) diag_event("fe_console: Options > %s row not added", hidden);
            c->eax = M32(c->ecx + 0x104);                  // the index the next row would get
            c->esp += 12;                                  // ret 8
            return;
        }
    }
    f_00588570_orig(c);
}

// F29: the pause Video basic page has two PC rows and native FNG bindings for a third slider.
// Append a normal FESliderWidget, owned/freed by UIWidgetMenu. A private VOLevelOfDetail vtable
// keeps native focus/layout/destruction, uses the surviving VOTimeOfDay Draw for its localized
// 999D1383 label, and identifies our Act/initial-values overrides without changing the LOD row.
// Keep this backup across basic/Advanced rebuilds; never write phase just for opening/drawing.
static uint32_t tod_vtable;
static uint32_t brightness_vtable;
static struct {
    uint32_t screen, widget;
    float original, value;
    int changed, accepted;
} bright;
// F10 saved a brightness while pause > Options is open: that value becomes the one Back restores,
// so the screen and app.txt can't disagree after the user backs out.
static void brightness_options_rebase(void) {
    if (!bright.screen) return;
    bright.original = bright.value = brightness_get();
    bright.changed = bright.accepted = 0;
}
static struct {
    uint32_t screen, widget, object, original, value;
    int changed, accepted;
} tod;

void f_00652AD0(Cpu *c);                                    // game allocator, cdecl (size)
void f_00589540(Cpu *c);                                    // FESliderWidget(bool)
void f_00588860(Cpu *c);                                    // UIWidgetMenu::AddSliderOption
void f_0056CB20(Cpu *c);                                    // cSlider::InitValues
void f_0056CD20(Cpu *c);                                    // cSlider::SetValue
void f_00574BC0(Cpu *c);                                    // FESliderWidget::UpdateSlider
void f_007696B0(Cpu *c);                                    // SetTimeOfDay(float)

// Calls below the live guest frame, preserving its registers/x87 state and calling convention.
// The copied Cpu is discarded, so both cdecl and callee-cleaned calls leave the caller intact.
static uint32_t tod_call(Cpu *c, void (*fn)(Cpu *), uint32_t self, int n, const uint32_t *args) {
    Cpu t = *c;
    t.ecx = self;
    for (int i = n; i-- > 0;) { t.esp -= 4; M32(t.esp) = args[i]; }
    t.esp -= 4; M32(t.esp) = 0x005295F0u;
    fn(&t);
    return t.eax;
}
static void tod_restore(Cpu *c) {
    if (!tod.changed) return;
    if (M32(0x009B392Cu) == tod.object) {
        tod_call(c, f_007696B0, 0, 1, &tod.original);
        if (diag_on) diag_event("fe_console: Time Of Day cancel restore %.6f", MF32(tod.object + 8));
    } else if (diag_on) {
        diag_event("fe_console: Time Of Day cancel restore skipped (world changed)");
    }
    tod.value = tod.original;
    tod.changed = tod.accepted = 0;
    if (tod.widget && M32(tod.widget) == tod_vtable)
        tod_call(c, f_0056CD20, tod.widget + 0x5C, 1, &tod.value);
}
static void brightness_restore(Cpu *c) {
    if (!bright.changed) return;
    brightness_preview(bright.original);
    bright.value = bright.original;
    bright.changed = bright.accepted = 0;
    // Setup clears/replaces widgets when switching Basic/Advanced; only touch our live row.
    if (bright.widget && M32(bright.widget) == brightness_vtable) {
        uint32_t value;
        memcpy(&value, &bright.value, 4);
        tod_call(c, f_0056CD20, bright.widget + 0x5C, 1, &value);
    }
    if (diag_on) diag_event("fe_console: Brightness cancel restore %.9g", bright.value);
}
static void brightness_add(Cpu *c, uint32_t screen) {
    if (bright.screen != screen) return;
    if (!brightness_vtable) {
        uint32_t size = 0x40;
        brightness_vtable = tod_call(c, f_00652AD0, 0, 1, &size);
        if (!brightness_vtable) return;
        for (int i = 0; i < 16; ++i) M32(brightness_vtable + 4 * i) = M32(0x0089BB40u + 4 * i);
        M32(brightness_vtable + 0x0C) = 0x0051B750u;        // OPT_DO_GAMMA, same layout as Time Of Day
    }
    uint32_t size = 0x9C, enabled = 1;
    uint32_t widget = tod_call(c, f_00652AD0, 0, 1, &size);
    if (!widget) return;
    tod_call(c, f_00589540, widget, 1, &enabled);
    M32(widget) = brightness_vtable;
    bright.widget = widget;
    uint32_t args[] = {widget, 1};
    uint32_t slot = tod_call(c, f_00588860, screen, 2, args);
    if (diag_on) diag_event("fe_console: Brightness row added screen=%08X slot=%u value=%.9g range=0..1 step=0.01 scale=100",
                           screen, slot, bright.value);
}

void f_005295F0_orig(Cpu *c);
void f_005295F0(Cpu *c) {                                   // UIOptionsScreen::SetupVideo
    uint32_t screen = c->ecx;
    int pause = M8(screen + 0x124) != 0;
    if (pause && !M32(screen + 0x144)) {                     // first setup, not Advanced/basic rebuild
        memset(&bright, 0, sizeof bright);
        bright.screen = screen;
        bright.original = bright.value = brightness_get();
        memset(&tod, 0, sizeof tod);
        uint32_t object = M32(0x009B392Cu);
        if (object && isfinite(MF32(object + 8))) {
            tod.screen = screen;
            tod.object = object;
            tod.original = tod.value = M32(object + 8);
        }
    }
    if (tod.screen == screen) tod.widget = 0;               // previous widgets were cleared by Setup
    if (bright.screen == screen) bright.widget = 0;
    f_005295F0_orig(c);
    if (!pause || M8(screen + 0x154)) return;
    if (tod.screen != screen) { brightness_add(c, screen); return; }
    // Original basic rows consumed 1 and 2. Slot 3 is also used natively on Advanced.
    if (M32(screen + 0x104) != 3) return;
    if (!tod_vtable) {
        uint32_t size = 0x40;
        tod_vtable = tod_call(c, f_00652AD0, 0, 1, &size);
        if (!tod_vtable) return;
        for (int i = 0; i < 16; ++i) M32(tod_vtable + 4 * i) = M32(0x0089BB40u + 4 * i);
        M32(tod_vtable + 0x0C) = 0x0051B700u;              // native Time Of Day label/slider Draw
    }
    uint32_t size = 0x9C, enabled = 1;
    uint32_t widget = tod_call(c, f_00652AD0, 0, 1, &size);
    if (!widget) return;
    tod_call(c, f_00589540, widget, 1, &enabled);
    M32(widget) = tod_vtable;
    tod.widget = widget;
    uint32_t args[] = {widget, 1};
    tod_call(c, f_00588860, screen, 2, args);
    if (diag_on) diag_event("fe_console: Time Of Day row added screen=%08X slot=3 value=%.6f range=0..1 step=0.01 scale=100",
                           screen, MF32(widget + 0x84));
    brightness_add(c, screen);
}

void f_0050F9E0_orig(Cpu *c);
void f_0050F9E0(Cpu *c) {                                   // VOLevelOfDetail::SetInitialValues
    if (brightness_vtable && M32(c->ecx) == brightness_vtable) {
        uint32_t value;
        memcpy(&value, &bright.value, 4);
        uint32_t args[] = {0, 0x3F800000u, 0x3C23D70Au, value, 0x42C80000u};
        tod_call(c, f_0056CB20, c->ecx + 0x5C, 5, args);
        c->esp += 4;
        return;
    }
    if (!tod_vtable || M32(c->ecx) != tod_vtable) { f_0050F9E0_orig(c); return; }
    uint32_t args[] = {0, 0x3F800000u, 0x3C23D70Au, tod.value, 0x42C80000u};
    tod_call(c, f_0056CB20, c->ecx + 0x5C, 5, args);         // min 0, max 1, step .01, value, scale 100
    c->esp += 4;
}
void f_0051B870_orig(Cpu *c);
void f_0051B870(Cpu *c) {                                   // VOLevelOfDetail::Act(package, message)
    if (brightness_vtable && M32(c->ecx) == brightness_vtable) {
        uint32_t widget = c->ecx, message = M32(c->esp + 8), before = M32(widget + 0x84);
        tod_call(c, f_00574BC0, widget, 1, &message);
        if (M32(widget + 0x84) != before) {
            bright.value = MF32(widget + 0x84);
            bright.changed = 1;
            bright.accepted = 0;
            brightness_preview(bright.value);
            if (diag_on) diag_event("fe_console: Brightness value %.9g exponent %.9g (live preview)",
                                   bright.value, brightness_exponent(bright.value));
        }
        c->esp += 12;
        return;
    }
    if (!tod_vtable || M32(c->ecx) != tod_vtable) { f_0051B870_orig(c); return; }
    uint32_t widget = c->ecx, message = M32(c->esp + 8), before = M32(widget + 0x84);
    tod_call(c, f_00574BC0, widget, 1, &message);             // native keys, mouse, pad, clamping/drawing
    uint32_t value = M32(widget + 0x84);
    if (value != before && M32(0x009B392Cu) == tod.object) {
        tod.value = value;
        tod.changed = 1;
        tod.accepted = 0;
        tod_call(c, f_007696B0, 0, 1, &value);
        if (diag_on) diag_event("fe_console: Time Of Day value %.6f (live preview)", MF32(widget + 0x84));
    }
    c->esp += 12;                                         // ret 8
}

void f_00510450_orig(Cpu *c);
void f_00510450(Cpu *c) {                                   // UIOptionsScreen::RestoreOriginals (incl. display rejection)
    if (c->ecx == bright.screen) brightness_restore(c);
    if (c->ecx == tod.screen) tod_restore(c);
    f_00510450_orig(c);
}
void f_00545750_orig(Cpu *c);
void f_00545750(Cpu *c) {                                   // UIOptionsScreen::NotificationMessage
    if (c->ecx == bright.screen) {
        uint32_t message = M32(c->esp + 4);
        if (message == 0x911AB364u) brightness_restore(c);
        if (message == 0x406415E3u || message == 0x4D0D83B9u || message == 0xE1FDE1D1u)
            bright.accepted = 1;
    }
    if (c->ecx == tod.screen) {
        uint32_t message = M32(c->esp + 4);
        if (message == 0x911AB364u) tod_restore(c);         // Back: no display-change dialog for phase alone
        if (message == 0x406415E3u || message == 0x4D0D83B9u || message == 0xE1FDE1D1u)
            tod.accepted = 1;                             // Accept / keep-display confirmation / leave accepted
    }
    f_00545750_orig(c);
}
void f_00529280_orig(Cpu *c);
void f_00529280(Cpu *c) {                                   // UIOptionsScreen destructor: also cover unpause/other exits
    if (c->ecx == bright.screen) {
        if (!bright.accepted) brightness_restore(c);
        else if (bright.changed) {
            brightness_save();
            if (diag_on) diag_event("fe_console: Brightness accept keep %.9g (saved)", brightness_get());
        }
        memset(&bright, 0, sizeof bright);
    }
    if (c->ecx == tod.screen) {
        if (!tod.accepted) tod_restore(c);
        else if (tod.changed && diag_on) diag_event("fe_console: Time Of Day accept keep %.6f", MF32(tod.object + 8));
        memset(&tod, 0, sizeof tod);
    }
    f_00529280_orig(c);
}

// Controller unplugged (I1, F20). IOModule::CheckUnplugged (00644E40) asks every input device whether it is still
// there (vtable +4); the pad's class (GameDevice, vtable 008A7B9C) shares the base class's "return 1" (006287D0), so the
// PC game's whole disconnect path (ActionQueue +0x280 -> cFEngJoyInput::CheckUnplugged 00559280 -> WantControllerError
// 0054ECE0 -> ControllerUnplugged.fng) never ran: dinput.c keeps a missing pad centred with DI_OK. Here the pad reports
// gone once the controller that was the active input is gone (padprompts.c), which the game only acts on when the
// player's input queue is on that device: keyboard-only play never sees it, and a key or mouse move hands the screen
// back (the game's check then finds the pad "present" and clears the error). Plugging it in again clears it too.
int prompts_pad_lost(void);
int padguest_connection(Cpu *c);
void f_006287D0_orig(Cpu *c);
void f_006287D0(Cpu *c) {                                  // InputDevice::IsConnected (thiscall)
    if (padguest_connection(c)) return;                   // NFS_PADS=2: independently connected port 1
    f_006287D0_orig(c);
    if (c->ecx > 0x10000 && M32(c->ecx) == 0x008A7B9Cu && prompts_pad_lost()) {
        static int logged;
        if (diag_on && !logged) { logged = 1; diag_event("fe_console: pad reported unplugged to the game"); }
        c->eax = 0;
    }
}

// Text swaps, applied where the game looks a label up (SearchForString 0056BB80, hooked by diag.c, which calls this
// instead of the original): `from` hash -> the `to` hash's text (an entry the PC table already has) or `text` as given
// (guest copy made once). A swap whose target is missing in the language falls back to the original text. `x360_only`:
// only while the X360 post chain is on (NFS_POSTFX, F10 Graphics).
static struct { uint32_t from, to; const char *text; int x360_only; uint32_t guest; } k_text[] = {
    // F20: the controller-disconnected prompt -> the X360 wording
    {0x54EEFEC2u, 0x54EE7FA6u, NULL, 0, 0},
    // F18: the PC saving message (UIMemcardBase::InitComplete 00562C30) -> the table's neutral "Saving %s."
    {0x50D23EBDu, 0xA71ED258u, NULL, 0, 0},
};
void f_0056BB80_orig(Cpu *c);
void fe_console_search(Cpu *c) {                           // SearchForString: hash in edx, text pointer in eax
    uint32_t hash = c->edx;
    for (size_t i = 0; i < sizeof k_text / sizeof *k_text; ++i) {
        if (k_text[i].from != hash || (k_text[i].x360_only && !x360_post_chain_on())) continue;
        if (diag_on) diag_event("fe_console: text %08X swapped", hash);
        if (k_text[i].text) {
            if (!k_text[i].guest) k_text[i].guest = gstr(k_text[i].text);
            c->eax = k_text[i].guest;
            return;
        }
        c->edx = k_text[i].to;
        f_0056BB80_orig(c);
        if (c->eax) return;
        c->edx = hash;
        break;
    }
    f_0056BB80_orig(c);
}
