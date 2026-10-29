// controltype.c - the X360's Control Type 1-10 (and its "Steer with left stick", the same choice) on the PC's bindings
//
// The PC keeps the selector's logic (PlayerSettings::GetControllerAttribs 0056D4B0: drive collection key from table
// 0089F6AC at index leftStick + 2 * variant) but not its widgets, and its data can't serve it: PC Types 6-10 are Type 1
// byte for byte, and PC Types 2-5 steer from ALX_L/ALX_R, scalars the PC GameDevice doesn't publish (no steering). On the
// X360 every type's record names a physical pad control, so a type is a button preset plus the steering source:
// variant 1-5 picks the buttons, leftStick picks the left stick (Types 1-5) or the D-pad (Types 6-10).
//
// Here the game stays on Type 1 (PlayerSettings untouched: nothing in the career profile changes) and the type is a
// port setting. FUN_006349B0 (each frame, every GameAction's value: the largest of its non-empty bindings through
// FUN_00628940) is hooked: for a type other than 1, the controller slots (2, 3) of the actions a type changes hold the
// preset's DirectInput object for the call, then the user's own come back: the keyboard slots, the Controls screen
// and the saved bindings stay the user's. Variant 1 (Types 1 / 6) keeps the port's bindings; Types 6-10 steer with the D-pad only.
// X360 names by position: A Cross, B Circle, X Square, Y Triangle, White RB / R1, Black LB / L1, L / R the LT / RT
// (L2 / R2) axes, ARY_U / D the right stick, RThumb R3, Back View / Create. Ramps: X360 mode 0 is Ramp(x, low, 1 - high)
// (ActionQueue::FetchCurrentValues 00642C90); the one with an effect on an analog source is the right-stick pedals'
// 0.46 (Types 5 / 10), applied to the action's value here. The left-stick dead zone (all of Types 1-5) is the Steering dead zone
// row's (attrib_x360.c, on the drive_analog the game keeps); camera / look back 0.30 and D-pad 0.40 act on buttons.
// Non-PlayStation pads: user32.m's driving layout re-makes the buttons, so for variants 2-5 it passes the physical ones
// through (controltype_passthrough).
// Switch: controls.control_type (NFS_CONTROL_TYPE 1..10, default 1, live). BOOT_DIAG: events.log "controltype:" lines.
// Offline test: runtime/controltype_test.py (this table against the X360 ATTRIBUTES.BIN through the exe's selector table).
#include <stdint.h>
#include <stdio.h>
#include <string.h>

enum { CT_GAS = 0, CT_BRAKE = 1, CT_STEERLEFT = 2, CT_STEERRIGHT = 3, CT_HANDBRAKE = 4, CT_GAMEBREAKER = 5, CT_NOS = 6,
       CT_SHIFTDOWN = 7, CT_SHIFTUP = 8, CT_RESET = 9, CT_CAMERA = 15, CT_LOOKBACK = 16 };
enum { B_BUTTON = 2, B_POV, B_AXIS_LO, B_AXIS_HI, B_ROT_LO, B_ROT_HI };   // padbind.c's binding types

typedef struct { const char *name; uint32_t type, obj; } CtSource;
// X360 scalar -> the DirectInput object dinput.c gives (PlayStation P_* button order; axes X Y Z Rx Ry Rz = lx, -ly,
// LT, rx, -ry, RT; the D-pad POV 0 in hundredths of a degree: object 1 right, 3 left).
static const CtSource ct_sources[] = {
    {"A", B_BUTTON, 0}, {"B", B_BUTTON, 1}, {"X", B_BUTTON, 2}, {"Y", B_BUTTON, 3}, {"Black", B_BUTTON, 4},
    {"White", B_BUTTON, 5}, {"Back", B_BUTTON, 6}, {"RThumb", B_BUTTON, 9}, {"L", B_AXIS_HI, 2}, {"R", B_ROT_HI, 2},
    {"ARY_U", B_ROT_LO, 1}, {"ARY_D", B_ROT_HI, 1}, {"ALX_L", B_AXIS_LO, 0}, {"ALX_R", B_AXIS_HI, 0},
    {"DLeft", B_POV, 3}, {"DRight", B_POV, 1},
};
typedef struct { int action; const char *src; float low; } CtMap;
#define CT_NBUTTONS 10
// Variants 2-5 (X360 drive2..drive5 / drive2_analog..drive5_analog, identical apart from steering).
static const CtMap ct_buttons[4][CT_NBUTTONS] = {   // all-empty (every type keeps the player's buttons) with no X360 copy
#include "gen/controltype_buttons.inc"                  // tools/gen_tables.py, from the player's X360 ATTRIBUTES.BIN
};
// The X360 selector: Type 1-5 leftStick, 6-10 D-pad; variant = (type - 1) % 5 (8223BD78 shows variant + 1 / + 6).
static int ct_left_stick(int type) { return type <= 5; }
static int ct_variant(int type) { return (type - 1) % 5; }
static int ct_table_index(int type) { return ct_left_stick(type) + 2 * ct_variant(type); }   // GetControllerAttribs

static const CtSource *ct_source(const char *name) {
    for (size_t i = 0; i < sizeof ct_sources / sizeof ct_sources[0]; ++i)
        if (!strcmp(ct_sources[i].name, name)) return &ct_sources[i];
    return NULL;
}
// What a type does to one action: the X360 source (NULL: the port's bindings) and the ramp applied to its value.
static const char *ct_map(int type, int action, float *low) {
    *low = 0;
    if (type < 2 || type > 10) return NULL;
    if (action == CT_STEERLEFT || action == CT_STEERRIGHT) {
        if (ct_left_stick(type) && ct_variant(type) == 0) return NULL;
        return ct_left_stick(type) ? (action == CT_STEERLEFT ? "ALX_L" : "ALX_R") : (action == CT_STEERLEFT ? "DLeft" : "DRight");
    }
    if (ct_variant(type) == 0) return NULL;
    for (int i = 0; i < CT_NBUTTONS; ++i)
        if (ct_buttons[ct_variant(type) - 1][i].action == action) {
            *low = ct_buttons[ct_variant(type) - 1][i].low;
            return ct_buttons[ct_variant(type) - 1][i].src;
        }
    return NULL;
}

#ifndef CONTROLTYPE_TEST
#include "funcs.h"
#include "host.h"
#include "settings.h"

#define GA 0x0091F418u          // GameAction[76] (padbind.c): name, then 4 bindings {type, device, object}

static const char *const kType[] = {"Type 1", "Type 2", "Type 3", "Type 4", "Type 5", "Type 6", "Type 7", "Type 8",
                                    "Type 9", "Type 10"};
static const char *const kTypeV[] = {"1", "2", "3", "4", "5", "6", "7", "8", "9", "10"};
static int ct_type = 1;
static void ct_apply(int v) {
    ct_type = v + 1;
    fprintf(stderr, "boot: control type %d\n", ct_type);
    if (diag_on) diag_event("controltype: type %d (%s stick, variant %d, X360 table index %d)", ct_type,
                            ct_left_stick(ct_type) ? "left" : "D-pad", ct_variant(ct_type) + 1, ct_table_index(ct_type));
}
static Setting s_type = {.id = "controls.control_type", .category = SET_CONTROLS, .order = 105, .label = "Control type",
    .help = "The X360's Control Types: 1 keeps your bindings; 2-5 move the driving buttons as on the X360; 6-10 are 1-5 steering with the D-pad.",
    .type = SET_CHOICE, .choices = kType, .values = kTypeV, .nchoices = 10, .def = 0, .env = "NFS_CONTROL_TYPE",
    .apply = ct_apply};
__attribute__((constructor)) static void controltype_register(void) { settings_add(&s_type); }

int controltype_passthrough(void) {             // user32.m: the driving buttons as the pad's own (variants 2-5)
    if (ct_type != s_type.value + 1) ct_apply(s_type.value);
    return ct_type > 1 && ct_variant(ct_type) != 0;
}

// Split-screen producer, item 1: creation of the second logical device is separate.
// DI enumerates host pads in index order; 006283D0 appends these controller objects
// to 0091F150 (keyboard/mouse have separate globals). Port 0 polls this shared
// backend in the original update, before port 1 in IOModule's ordered device loop.
void f_00634B50_orig(Cpu *c);
void f_00634B50(Cpu *c) {
    if (pad_count() != 2) { f_00634B50_orig(c); return; }
    uint32_t dev = c->ecx;
    int later = M32(0x009203C0u) != 0;
    f_00634B50_orig(c);                         // scalar entries +0x34 are already per instance
    if (!later) return;                       // first instance also enumerates the shared backend
    M32(dev + 0x18) = dev + 0x34;
    M32(dev + 0x1C) = dev + 0x1034;
    M32(dev + 0x20) = dev + 0x1434;
    memset(G + dev + 0x1034, 0, 0x800);
    M8(dev + 0x183C) = 0;
    M32(dev + 0x1840) = 1;
    M32(dev + 0x1844) = M32(dev + 0x1848) = 0;
}

void f_006349B0_orig(Cpu *c);
void f_00628940(Cpu *c);                      // the game's binding evaluator (translated)
static void ct_update(Cpu *c) {
    uint32_t dev = c->ecx;
    if (pad_count() != 2 || M32(dev + 0x24) != 1) { f_006349B0_orig(c); return; }
    // Capture remains owned by port 0. Never run the shared keyboard/mouse capture
    // routine for port 1, nor poll/consume physical events a second time.
    if (M32(0x00982BECu) && !M8(dev + 0x183C)) {
        uint32_t out = M32(dev + 0x20);
        memcpy(G + M32(dev + 0x1C), G + out, 0x400);
        for (uint32_t a = 0; a < 76; ++a) {
            float value = 0;
            for (uint32_t b = 0; b < 4; ++b) {
                uint32_t r = GA + 0x34 * a + 4 + 12 * b;
                uint32_t type = M32(r);
                if (type < 2 || type > 9) continue; // only controller button/POV/axis/rotation/slider
                uint32_t saved = M32(r + 4);
                M32(r + 4) = M32(dev + 0x24);      // explicit logical port -> physical controller slot
                Cpu q = *c;
                q.esp -= 12;
                M32(q.esp) = 0; M32(q.esp + 4) = a; M32(q.esp + 8) = b;
                f_00628940(&q);                    // original value/axis/diagonal-POV rules, x87 return
                float v = (float)q.st[q.top];
                M32(r + 4) = saved;
                if (v > value) value = v;
            }
            MF32(out + 4 * a) = value;
        }
    }
    c->esp += 4;                                   // fastcall ret; nonvolatile registers/x87 untouched
}
void f_006349B0(Cpu *c) {                       // fastcall (IOModule device): every GameAction's value, each frame
    if (ct_type != s_type.value + 1) ct_apply(s_type.value);
    uint32_t dev = c->ecx;
    int port_swapped = pad_count() == 2 && M32(dev + 0x24) == 1 ? padbind_port_begin(1) : 0;
    if (ct_type < 2 || M8(dev + 0x183C)) { ct_update(c); padbind_port_end(port_swapped); return; }   // Type 1, or a binding capture in progress
    // FUN_00628940 reads each non-empty binding and the largest wins: the controller slots of the actions this type
    // changes hold the preset's control for the call (slot 3 empty), then the user's own come back.
    if (pad_count() != 2 || M32(dev + 0x24) == 0)
        padbind_follow(c);                      // only port 0 follows shared binding/identity changes
    uint32_t saved[12][6];
    int acts[12], ramp[12], n = 0;
    float lows[12];
    for (int a = 0; a < 17 && n < 12; ++a) {
        float low;
        const char *src = ct_map(ct_type, a, &low);
        const CtSource *s = src ? ct_source(src) : NULL;
        if (!s) continue;
        uint32_t r = GA + 0x34u * (uint32_t)a + 4 + 24u;
        for (int k = 0; k < 6; ++k) saved[n][k] = M32(r + 4 * (uint32_t)k);
        M32(r) = s->type; M32(r + 4) = 0; M32(r + 8) = s->obj; M32(r + 12) = M32(r + 16) = M32(r + 20) = 0;
        acts[n] = a; lows[n] = low; ramp[n] = low > 0; ++n;
    }
    padbind_swap(1);                            // the joystick reads inside it must not see the preset
    ct_update(c);
    padbind_swap(0);
    uint32_t out = M32(dev + 0x20);
    for (int i = 0; i < n; ++i) {
        uint32_t r = GA + 0x34u * (uint32_t)acts[i] + 4 + 24u;
        for (int k = 0; k < 6; ++k) M32(r + 4 * (uint32_t)k) = saved[i][k];
        if (!out) continue;
        float v = MF32(out + 4u * (uint32_t)acts[i]);
        // Keyboard bindings are 0 or 1, so ramping the largest value ramps exactly the stick's share (Ramp(1) = 1).
        if (ramp[i]) { v = (v - lows[i]) / (1 - lows[i]); v = v < 0 ? 0 : v > 1 ? 1 : v; MF32(out + 4u * (uint32_t)acts[i]) = v; }
        if (diag_on) {
            static float last[17];
            if ((v > .5f) != (last[acts[i]] > .5f))
                diag_event("controltype: type %d action %d %s %.3f", ct_type, acts[i], v > .5f ? "on" : "off", v);
            last[acts[i]] = v;
        }
    }
    padbind_port_end(port_swapped);
}
#endif
