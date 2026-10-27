// dinput.c - DINPUT8.dll for the guest: the system keyboard and mouse, fed from user32.m's Cocoa events.
//
// Keyboard: GetDeviceState gives the 256-byte DIK array; GetDeviceData drains user32's buffered key events.
// Mouse: DIMOUSESTATE(2) with relative motion since the last read.
// Game controller: always one gamepad, from start-up (centred while user32.m's pad_read() has no controller: none yet,
// or unplugged; GameController.framework, or BOOT_PAD=sim:...), named
// after the pad (user32.m pad_identity; the Controls screen titles its page with it and padbind.c keys the binding sets
// by it). A PlayStation pad (DualSense 054C:0CE6, DualShock 4 054C:09CC, DualSense Edge 054C:0DF2) is itself: buttons
// Cross Circle Square Triangle L1 R1 Create (Share) Options L3 R3, PS, Touchpad (and the Edge's paddles) under those
// names, the sticks as X/Y and Rx/Ry, L2 / R2 as Z / Rz (centred at rest, full at a full press, so each is one analog
// half-axis to the game's bindings), the D-pad as POV 0. Other pads keep their Xbox-style identity and 12 buttons
// (user32.m's layouts), but share the same six-axis contract: X/Y, LT on Z, Rx/Ry, RT on Rz. Both triggers run
// independently from centre at rest to maximum, including before a controller connects.
// The game enumerates attached game controllers (FUN_006dc540: type 0x15 = gamepad) and reads DIJOYSTATE2 each frame;
// No force feedback: the PC game's DirectInput effects were wheel forces. Rumble is the console model in feedback.c.
#include <math.h>
#include <stdlib.h>

#include "host.h"

extern uint8_t di_keys[256];
extern int di_mouse_dx, di_mouse_dy, di_mouse_dz;
extern uint8_t di_mouse_btn[8];
int user32_di_next(uint32_t *ofs, uint32_t *data, uint32_t *time, uint32_t *seq);
void user32_pump(void);

#define DI_OK 0
#define DI_BUFFEROVERFLOW 1
#define DIERR_INVALIDPARAM 0x80070057u
#define DIERR_NOTACQUIRED 0x8007000Cu
#define DIERR_DEVICENOTREG 0x80040154u
#define E_NOINTERFACE 0x80004002u

enum { DEV_KEYBOARD = 1, DEV_MOUSE = 2, DEV_PAD = 3 };
#define PAD_BUTTONS 12                  // 10 like the Xbox 360 pad, + 2 for actions the PC defaults leave unbound
#define PAD_BUTTONS_PS 16               // user32.m's P_* order (L2 / R2, 10 and 11, are axes and never pressed)
typedef struct {
    int kind, refs; uint32_t guest; int dev; int acquired; uint32_t buffer_size; int32_t rmin[8], rmax[8];
} DiObj;
static DiObj **objs;
static unsigned nobjs;
static uint32_t vt_di, vt_dev;

static DiObj *new_obj(int kind, uint32_t vt) {
    DiObj *o = calloc(1, sizeof *o);
    o->kind = kind; o->refs = 1;
    o->guest = galloc(16);
    M32(o->guest) = vt; M32(o->guest + 4) = nobjs; M32(o->guest + 8) = 0x0D1B0B1E;
    objs = realloc(objs, (nobjs + 1) * sizeof *objs);
    objs[nobjs++] = o;
    return o;
}
static DiObj *obj(uint32_t g) {
    if (!g || M32(g + 8) != 0x0D1B0B1E) return NULL;
    uint32_t i = M32(g + 4);
    return i < nobjs ? objs[i] : NULL;
}
#define THIS obj(arg(c, 0))

// GUID_SysKeyboard {6F1D2B61-D5A0-11CF-BFC7-444553540000}, GUID_SysMouse {6F1D2B60-...}
static const uint8_t guid_tail[8] = {0xBF, 0xC7, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00};
static void write_guid(uint32_t p, uint32_t d1) {
    M32(p) = d1; M16(p + 4) = 0xD5A0; M16(p + 6) = 0x11CF;
    memcpy(G + p + 8, guid_tail, 8);
}
#define PAD_GUID 0x6F1D2B70u        // our gamepad's instance GUID (same family as the system devices')
static int guid_dev(uint32_t p) {
    if (M16(p + 4) != 0xD5A0 || M16(p + 6) != 0x11CF || memcmp(G + p + 8, guid_tail, 8)) return 0;
    return M32(p) == 0x6F1D2B61 ? DEV_KEYBOARD : M32(p) == 0x6F1D2B60 ? DEV_MOUSE : M32(p) == PAD_GUID ? DEV_PAD : 0;
}
static int pad_ps(void) { int k = pad_identity(NULL, 0); return k == PAD_KIND_DUALSENSE || k == PAD_KIND_DUALSHOCK4 || k == PAD_KIND_EDGE; }
static void fill_instance(uint32_t p, int dev) {        // DIDEVICEINSTANCEA (580 bytes)
    memset(G + p + 4, 0, 576);
    if (dev == DEV_PAD) {
        char name[64];
        int kind = pad_identity(name, sizeof name);
        uint32_t pidvid = kind == PAD_KIND_DUALSENSE ? 0x0CE6054C : kind == PAD_KIND_DUALSHOCK4 ? 0x09CC054C :
                          kind == PAD_KIND_EDGE ? 0x0DF2054C : 0x028E045E;
        if (!name[0]) snprintf(name, sizeof name, "Controller (XBOX 360 For Windows)");
        M32(p) = 580;
        write_guid(p + 4, PAD_GUID);
        M32(p + 20) = pidvid; M16(p + 24) = 0; M16(p + 26) = 0; memcpy(G + p + 28, "\0\0PIDVID", 8);   // guidProduct
        M32(p + 36) = 0x00010215;                              // DI8DEVTYPE_GAMEPAD, standard, HID
        snprintf((char *)G + p + 40, 260, "%s", name);
        snprintf((char *)G + p + 300, 260, "%s", name);
        M16(p + 576) = 1; M16(p + 578) = 5;                    // HID usage page generic desktop, usage game pad
        return;
    }
    M32(p) = 580;
    write_guid(p + 4, dev == DEV_KEYBOARD ? 0x6F1D2B61 : 0x6F1D2B60);
    write_guid(p + 20, dev == DEV_KEYBOARD ? 0x6F1D2B61 : 0x6F1D2B60);
    M32(p + 36) = dev == DEV_KEYBOARD ? 0x0413 : 0x0112;    // DI8DEVTYPE_KEYBOARD / MOUSE, subtype 1..
    strcpy((char *)G + p + 40, dev == DEV_KEYBOARD ? "Keyboard" : "Mouse");
    strcpy((char *)G + p + 300, dev == DEV_KEYBOARD ? "Keyboard" : "Mouse");
}

static int q_QueryInterface(Cpu *c) { DiObj *o = THIS; ++o->refs; M32(arg(c, 2)) = o->guest; RET(DI_OK, 3); }   // IDirectInput8 / Device8 only
static int q_AddRef(Cpu *c) { DiObj *o = THIS; RET(o ? ++o->refs : 0, 1); }
static int q_Release(Cpu *c) { DiObj *o = THIS; RET(o && o->refs > 0 ? --o->refs : 0, 1); }

static int i_CreateDevice(Cpu *c) {            // (this, rguid, ppDevice, outer)
    int dev = guid_dev(arg(c, 1));
    if (boot_trace) fprintf(stderr, "      DirectInput CreateDevice(%s)\n", dev == DEV_KEYBOARD ? "keyboard" : dev == DEV_MOUSE ? "mouse" : "unknown");
    if (!dev) { M32(arg(c, 2)) = 0; RET(DIERR_DEVICENOTREG, 4); }
    DiObj *d = new_obj(2, vt_dev);
    d->dev = dev;
    for (int i = 0; i < 8; ++i) { d->rmin[i] = 0; d->rmax[i] = 65535; }   // DirectInput's default axis range
    M32(arg(c, 2)) = d->guest;
    RET(DI_OK, 4);
}
static int i_EnumDevices(Cpu *c) {             // (this, devType, callback, ref, flags)
    uint32_t type = arg(c, 1), cb = arg(c, 2), ref = arg(c, 3);
    uint32_t inst = galloc(580), esp = c->esp;
    // DI8DEVCLASS_ALL 0, DEVICE 1, POINTER 2, KEYBOARD 3, GAMECTRL 4; or a DI8DEVTYPE_* value
    int want_kb = type == 0 || type == 1 || type == 3 || type == 0x13;
    int want_mouse = type == 0 || type == 1 || type == 2 || type == 0x12;
    if (want_kb) { fill_instance(inst, DEV_KEYBOARD); uint32_t a[2] = {inst, ref}; if (!call_guest(c, cb, 2, a)) { c->esp = esp; RET(DI_OK, 5); } }
    if (want_mouse) { fill_instance(inst, DEV_MOUSE); uint32_t a[2] = {inst, ref}; if (!call_guest(c, cb, 2, a)) { c->esp = esp; RET(DI_OK, 5); } }
    // The gamepad is always there: the game enumerates once (FUN_006dc540, at start-up), so a controller that connects,
    // wakes or is plugged in later still reaches it. Until one does it reads centred (d_GetDeviceState) and is named
    // after the Xbox 360 pad; the first controller's type then takes it over (padbind.c padbind_sync).
    PadState ps;
    int want_pad = type == 0 || type == 4 || type == 0x15;
    if (want_pad) {
        int present = pad_read(&ps);       // a controller already there names the device
        fill_instance(inst, DEV_PAD);
        uint32_t a[2] = {inst, ref};
        call_guest(c, cb, 2, a);
        if (diag_on) diag_event("dinput: gamepad enumerated (%s)", present ? "controller connected" : "no controller yet: centred until one connects");
    }
    c->esp = esp;
    RET(DI_OK, 5);
}
static int i_GetDeviceStatus(Cpu *c) { PadState ps; int d = guid_dev(arg(c, 1)); RET(d && (d != DEV_PAD || pad_read(&ps)) ? DI_OK : 1 /* DI_NOTATTACHED */, 2); }
static int i_Initialize(Cpu *c) { RET(DI_OK, 3); }

static int d_GetCapabilities(Cpu *c) {         // DIDEVCAPS (44 bytes)
    DiObj *d = THIS;
    uint32_t p = arg(c, 1);
    memset(G + p + 4, 0, 40);
    M32(p + 4) = 7;                                           // ATTACHED | POLLEDDEVICE? no: ATTACHED | EMULATED
    M32(p + 8) = d->dev == DEV_KEYBOARD ? 0x0413 : 0x0112;
    M32(p + 12) = d->dev == DEV_MOUSE ? 3 : 0;                // axes
    M32(p + 16) = d->dev == DEV_MOUSE ? 3 : 128;              // buttons / keys
    if (d->dev == DEV_PAD) {                                   // ATTACHED; 6 axes, the existing per-type buttons, 1 POV
        int ps = pad_ps();
        M32(p + 4) = 0x001; M32(p + 8) = 0x00010215; M32(p + 12) = 6; M32(p + 16) = ps ? PAD_BUTTONS_PS : PAD_BUTTONS; M32(p + 20) = 1;
    }
    RET(DI_OK, 2);
}
// Gamepad objects: offsets into DIJOYSTATE, GUID_XAxis... ({A36D02xx-C9F3-11CF-BFC7-444553540000}), DIDFT types
// (2 absolute axis, 4 push button, 0x10 POV) and instances (axes X Y Z Rx Ry Rz = 0..5, as the game asks for them:
// FUN_006280C0).
typedef struct { uint32_t guid, ofs, type, inst; char name[32]; } PadObj;
static int pad_objects(PadObj *o) {             // the current pad's objects; returns how many
    static const struct { uint32_t guid, ofs, inst; const char *xbox, *ps; } ax[] = {
        {0xA36D02E0, 0, 0, "X Axis", "Left Stick X"}, {0xA36D02E1, 4, 1, "Y Axis", "Left Stick Y"},
        {0xA36D02E2, 8, 2, "LT", "L2"}, {0xA36D02F4, 12, 3, "X Rotation", "Right Stick X"},
        {0xA36D02F5, 16, 4, "Y Rotation", "Right Stick Y"}, {0xA36D02E3, 20, 5, "RT", "R2"}};
    static const char *const ps_buttons[PAD_BUTTONS_PS] = {"Cross", "Circle", "Square", "Triangle", "L1", "R1", "Create",
        "Options", "L3", "R3", NULL, NULL, "PS", "Touchpad", "Left Paddle", "Right Paddle"};
    int kind = pad_identity(NULL, 0), ps = pad_ps(), n = 0;
    for (int k = 0; k < 6; ++k) {
        o[n] = (PadObj){ax[k].guid, ax[k].ofs, 2, ax[k].inst, ""};
        snprintf(o[n++].name, 32, "%s", ps ? ax[k].ps : ax[k].xbox);
    }
    o[n] = (PadObj){0xA36D02F2, 32, 0x10, 0, ""};
    snprintf(o[n++].name, 32, "%s", ps ? "D-Pad" : "Hat Switch");
    for (int b = 0; b < (ps ? PAD_BUTTONS_PS : PAD_BUTTONS); ++b) {
        if (ps && (!ps_buttons[b] || (b >= 14 && kind != PAD_KIND_EDGE))) continue;
        o[n] = (PadObj){0xA36D02F0, 48 + (uint32_t)b, 4, (uint32_t)b, ""};
        if (ps) snprintf(o[n].name, 32, "%s", b == 6 && kind == PAD_KIND_DUALSHOCK4 ? "Share" : ps_buttons[b]);
        else snprintf(o[n].name, 32, "Button %d", b + 1);
        ++n;
    }
    return n;
}
static void fill_object(uint32_t o, const PadObj *p) {   // DIDEVICEOBJECTINSTANCEA (316 bytes) after dwSize
    memset(G + o + 4, 0, 312);
    M32(o + 4) = p->guid; M16(o + 8) = 0xC9F3; M16(o + 10) = 0x11CF; memcpy(G + o + 12, guid_tail, 8);
    M32(o + 20) = p->ofs;
    M32(o + 24) = p->type | p->inst << 8;
    snprintf((char *)G + o + 32, 260, "%s", p->name);
}
static int d_EnumObjects(Cpu *c) {             // (this, callback, ref, flags)
    DiObj *d = THIS;
    uint32_t cb = arg(c, 1), ref = arg(c, 2), flags = arg(c, 3), esp = c->esp;
    if (d->dev != DEV_PAD) RET(DI_OK, 4);
    uint32_t o = galloc(316);
    PadObj objs_[32];
    int n = pad_objects(objs_);
    for (int k = 0; k < n; ++k) {
        uint32_t type = objs_[k].type;
        if (flags && !(flags & type) && !(flags == 3 && type == 2)) continue;   // DIDFT_ALL = 0; DIDFT_AXIS = 3
        M32(o) = 316;
        fill_object(o, &objs_[k]);
        uint32_t a[2] = {o, ref};
        if (!call_guest(c, cb, 2, a)) break;               // DIENUM_STOP
    }
    c->esp = esp;
    RET(DI_OK, 4);
}
static int pad_axis_index(uint32_t ofs) { return ofs < 32 ? (int)(ofs / 4) : -1; }
static int d_GetProperty(Cpu *c) {             // (this, rguidProp, DIPROPHEADER*)
    DiObj *d = THIS;
    uint32_t prop = arg(c, 1), h = arg(c, 2);
    if (prop == 1) { M32(h + 16) = d->buffer_size; RET(DI_OK, 3); }   // DIPROP_BUFFERSIZE (MAKEDIPROP(1))
    if (prop == 4 && d->dev == DEV_PAD) {                     // DIPROP_RANGE, by offset (dwHow 1) or the device
        int i = M32(h + 12) == 1 ? pad_axis_index(M32(h + 8)) : 0;
        if (i < 0) RET(DIERR_INVALIDPARAM, 3);
        M32(h + 16) = (uint32_t)d->rmin[i]; M32(h + 20) = (uint32_t)d->rmax[i];
        RET(DI_OK, 3);
    }
    RET(DIERR_INVALIDPARAM, 3);
}
static int d_SetProperty(Cpu *c) {
    DiObj *d = THIS;
    uint32_t prop = arg(c, 1), h = arg(c, 2);
    if (prop == 1) d->buffer_size = M32(h + 16);
    if (prop == 4 && d->dev == DEV_PAD) {                     // DIPROP_RANGE: one axis (by offset) or all
        int i = M32(h + 12) == 1 ? pad_axis_index(M32(h + 8)) : -1;
        for (int k = 0; k < 8; ++k) if (i < 0 || k == i) { d->rmin[k] = (int32_t)M32(h + 16); d->rmax[k] = (int32_t)M32(h + 20); }
    }
    RET(DI_OK, 3);
}
static int d_Acquire(Cpu *c) { THIS->acquired = 1; RET(DI_OK, 1); }
static int d_Unacquire(Cpu *c) { THIS->acquired = 0; RET(DI_OK, 1); }
static int d_GetDeviceState(Cpu *c) {          // (this, size, data*)
    DiObj *d = THIS;
    uint32_t n = arg(c, 1), p = arg(c, 2);
    user32_pump();
    if (d->dev == DEV_KEYBOARD) { memcpy(G + p, di_keys, n < 256 ? n : 256); RET(DI_OK, 3); }
    if (d->dev == DEV_PAD) {                                  // DIJOYSTATE (80) / DIJOYSTATE2 (272)
        PadState ps;
        memset(G + p, 0, n);
        if (n >= 48) for (int k = 0; k < 4; ++k) M32(p + 32 + 4 * k) = 0xFFFFFFFF;   // POVs centred
        // No controller (none yet, or unplugged): centred, nothing pressed, DI_OK. DIERR_INPUTLOST would leave the
        // game's copy of the last state in place (FUN_007f28a4 returns without storing it), holding whatever was
        // pressed when the pad went away.
        if (!pad_read(&ps)) ps.native = pad_ps();
        padbind_sync(c);                                       // the game's joystick follows a change of controller type
        float ax[6] = {ps.lx, -ps.ly, ps.lt, ps.rx, -ps.ry, ps.rt};   // Y down; independent positive trigger halves
        for (int k = 0; k < 6 && 4 * k + 4 <= (int)n; ++k) {
            float v = ax[k] < -1 ? -1 : ax[k] > 1 ? 1 : ax[k];
            M32(p + 4 * k) = (uint32_t)(int32_t)lroundf(d->rmin[k] + (v + 1.0f) * 0.5f * (float)(d->rmax[k] - d->rmin[k]));
        }
        if (n >= 36) M32(p + 32) = ps.pov < 0 ? 0xFFFFFFFF : (uint32_t)ps.pov;
        for (int b = 0; b < (ps.native ? PAD_BUTTONS_PS : PAD_BUTTONS) && 48 + b < (int)n; ++b) M8(p + 48 + b) = (ps.buttons >> b) & 1 ? 0x80 : 0;
        if (diag_on && n >= 24) {
            unsigned user32_pad_trigger_step(float *, float *);
            void padbind_trigger_sample(unsigned, float, float, uint32_t, uint32_t, int);
            float lt, rt;
            unsigned step = user32_pad_trigger_step(&lt, &rt);
            padbind_trigger_sample(step, lt, rt, M32(p + 8), M32(p + 20), ps.native);
        }
        RET(DI_OK, 3);
    }
    // DIMOUSESTATE: lX, lY, lZ, rgbButtons[4]; DIMOUSESTATE2: 8 buttons
    memset(G + p, 0, n);
    if (n >= 12) { M32(p) = (uint32_t)di_mouse_dx; M32(p + 4) = (uint32_t)di_mouse_dy; M32(p + 8) = (uint32_t)di_mouse_dz; }
    for (uint32_t b = 0; b < 8 && 12 + b < n; ++b) M8(p + 12 + b) = di_mouse_btn[b];
    di_mouse_dx = di_mouse_dy = di_mouse_dz = 0;
    RET(DI_OK, 3);
}
static int d_GetDeviceData(Cpu *c) {           // (this, objsize, rgdod*, pdwInOut, flags)
    DiObj *d = THIS;
    uint32_t sz = arg(c, 1), out = arg(c, 2), inout = arg(c, 3), flags = arg(c, 4);
    uint32_t cap = M32(inout), n = 0;
    user32_pump();
    if (d->dev == DEV_KEYBOARD) {
        uint32_t ofs, data, time, seq;
        while (n < cap && !(flags & 1 /* DIGDD_PEEK */) && user32_di_next(&ofs, &data, &time, &seq)) {
            if (out) { uint32_t e = out + n * sz; memset(G + e, 0, sz); M32(e) = ofs; M32(e + 4) = data; M32(e + 8) = time; M32(e + 12) = seq; }
            ++n;
        }
    }
    M32(inout) = n;
    RET(DI_OK, 5);
}
static int d_SetDataFormat(Cpu *c) { RET(DI_OK, 2); }
static int d_SetEventNotification(Cpu *c) { RET(DI_OK, 2); }
static int d_SetCooperativeLevel(Cpu *c) { RET(DI_OK, 3); }
static int d_GetDeviceInfo(Cpu *c) { fill_instance(arg(c, 1), THIS->dev); RET(DI_OK, 2); }
static int d_Poll(Cpu *c) { RET(THIS && THIS->dev == DEV_PAD ? DI_OK : 1 /* DI_NOEFFECT */, 1); }
// Keyboard key names as Windows reports them (DIDEVICEOBJECTINSTANCE.tszName, US layout), by DIK scan code: the
// controls menu shows them for keyboard bindings.
static const char *dik_name(uint32_t k) {
    static const char *const low[0x59] = {
        [0x01] = "Esc", [0x02] = "1", [0x03] = "2", [0x04] = "3", [0x05] = "4", [0x06] = "5", [0x07] = "6", [0x08] = "7",
        [0x09] = "8", [0x0A] = "9", [0x0B] = "0", [0x0C] = "-", [0x0D] = "=", [0x0E] = "Backspace", [0x0F] = "Tab",
        [0x10] = "Q", [0x11] = "W", [0x12] = "E", [0x13] = "R", [0x14] = "T", [0x15] = "Y", [0x16] = "U", [0x17] = "I",
        [0x18] = "O", [0x19] = "P", [0x1A] = "[", [0x1B] = "]", [0x1C] = "Enter", [0x1D] = "Ctrl", [0x1E] = "A", [0x1F] = "S",
        [0x20] = "D", [0x21] = "F", [0x22] = "G", [0x23] = "H", [0x24] = "J", [0x25] = "K", [0x26] = "L", [0x27] = ";",
        [0x28] = "'", [0x29] = "`", [0x2A] = "Shift", [0x2B] = "\\", [0x2C] = "Z", [0x2D] = "X", [0x2E] = "C", [0x2F] = "V",
        [0x30] = "B", [0x31] = "N", [0x32] = "M", [0x33] = ",", [0x34] = ".", [0x35] = "/", [0x36] = "Right Shift",
        [0x37] = "Num *", [0x38] = "Alt", [0x39] = "Space", [0x3A] = "Caps Lock", [0x3B] = "F1", [0x3C] = "F2", [0x3D] = "F3",
        [0x3E] = "F4", [0x3F] = "F5", [0x40] = "F6", [0x41] = "F7", [0x42] = "F8", [0x43] = "F9", [0x44] = "F10",
        [0x45] = "Pause", [0x46] = "Scroll Lock", [0x47] = "Num 7", [0x48] = "Num 8", [0x49] = "Num 9", [0x4A] = "Num -",
        [0x4B] = "Num 4", [0x4C] = "Num 5", [0x4D] = "Num 6", [0x4E] = "Num +", [0x4F] = "Num 1", [0x50] = "Num 2",
        [0x51] = "Num 3", [0x52] = "Num 0", [0x53] = "Num Del", [0x57] = "F11", [0x58] = "F12"};
    if (k < 0x59) return low[k];
    switch (k) {
    case 0x9C: return "Num Enter";  case 0x9D: return "Right Ctrl"; case 0xB5: return "Num /";
    case 0xB7: return "Prnt Scrn";  case 0xB8: return "Right Alt";  case 0xC5: return "Num Lock";
    case 0xC7: return "Home";       case 0xC8: return "Up";         case 0xC9: return "Page Up";
    case 0xCB: return "Left";       case 0xCD: return "Right";      case 0xCF: return "End";
    case 0xD0: return "Down";       case 0xD1: return "Page Down";  case 0xD2: return "Insert";
    case 0xD3: return "Delete";
    }
    return NULL;
}
static int d_GetObjectInfo(Cpu *c) {           // (this, DIDEVICEOBJECTINSTANCEA*, dwObj, dwHow): the pad's objects by offset or id
    DiObj *d = THIS;
    uint32_t o = arg(c, 1), obj_ = arg(c, 2), how = arg(c, 3);
    if (diag_on) diag_event("dinput: GetObjectInfo(dev %d, obj %X, how %u)", d ? d->dev : 0, obj_, how);
    if (d && d->dev == DEV_KEYBOARD) {                       // by offset (= DIK code) or by id (instance = DIK code)
        uint32_t k = how == 1 ? obj_ : how == 2 ? (obj_ >> 8) & 0xFFFF : 0x100;
        const char *n = k < 0x100 ? dik_name(k) : NULL;
        if (!n) RET(0x80070002u, 4);                           // DIERR_OBJECTNOTFOUND
        memset(G + o + 4, 0, 312);
        M32(o + 4) = 0x55728220; M16(o + 8) = 0xD33C; M16(o + 10) = 0x11CF;   // GUID_Key
        static const uint8_t key_tail[8] = {0xBF, 0xC7, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00};
        memcpy(G + o + 12, key_tail, 8);
        M32(o + 20) = k; M32(o + 24) = 0x0C | k << 8;          // DIDFT_BUTTON | instance
        snprintf((char *)G + o + 32, 260, "%s", n);
        RET(DI_OK, 4);
    }
    if (!d || d->dev != DEV_PAD) RET(DIERR_INVALIDPARAM, 4);
    PadObj objs_[32];
    int n = pad_objects(objs_);
    for (int k = 0; k < n; ++k) {
        uint32_t id = objs_[k].type | objs_[k].inst << 8;
        if (!(how == 1 ? obj_ == objs_[k].ofs : how == 2 ? (obj_ & 0xFFFF1F) == (id & 0xFFFF1F) : 0)) continue;   // DIPH_BYOFFSET / BYID
        fill_object(o, &objs_[k]);
        RET(DI_OK, 4);
    }
    RET(0x80070002u, 4);                                      // DIERR_OBJECTNOTFOUND
}
// ---- force feedback: none (what DirectInput answers for a pad without it) ----
#define DIERR_UNSUPPORTED 0x80004001u
static int d_CreateEffect(Cpu *c) { M32(arg(c, 3)) = 0; RET(DIERR_UNSUPPORTED, 5); }   // (this, rguid, DIEFFECT*, ppEffect, outer)
static int d_EnumEffects(Cpu *c) { RET(DI_OK, 4); }                                      // (this, callback, ref, type): none
static int d_GetForceFeedbackState(Cpu *c) { RET(DIERR_UNSUPPORTED, 2); }
static int d_SendForceFeedbackCommand(Cpu *c) { RET(DIERR_UNSUPPORTED, 2); }
static int d_Escape(Cpu *c) { RET(DI_OK, 2); }

typedef struct { const char *name; HostFn fn; } Impl;
static int unimplemented(Cpu *c) { (void)c; return 0; }
static uint32_t make_vtable(const char *iface, const char *const *names, int n, const Impl *impl) {
    uint32_t vt = galloc(4 * (uint32_t)n);
    for (int i = 0; i < n; ++i) {
        char *full = malloc(strlen(iface) + strlen(names[i]) + 3);
        sprintf(full, "%s::%s", iface, names[i]);
        HostFn fn = unimplemented;
        for (const Impl *p = impl; p->name; ++p) if (!strcmp(p->name, names[i])) fn = p->fn;
        M32(vt + 4 * i) = host_fn(fn, full);
    }
    return vt;
}
static const char *const n_di[] = {"QueryInterface", "AddRef", "Release", "CreateDevice", "EnumDevices", "GetDeviceStatus",
    "RunControlPanel", "Initialize", "FindDevice", "EnumDevicesBySemantics", "ConfigureDevices"};
static const Impl i_di[] = {{"QueryInterface", q_QueryInterface}, {"AddRef", q_AddRef}, {"Release", q_Release},
    {"CreateDevice", i_CreateDevice}, {"EnumDevices", i_EnumDevices}, {"GetDeviceStatus", i_GetDeviceStatus},
    {"Initialize", i_Initialize}, {0, 0}};
static const char *const n_dev[] = {"QueryInterface", "AddRef", "Release", "GetCapabilities", "EnumObjects", "GetProperty",
    "SetProperty", "Acquire", "Unacquire", "GetDeviceState", "GetDeviceData", "SetDataFormat", "SetEventNotification",
    "SetCooperativeLevel", "GetObjectInfo", "GetDeviceInfo", "RunControlPanel", "Initialize", "CreateEffect", "EnumEffects",
    "GetEffectInfo", "GetForceFeedbackState", "SendForceFeedbackCommand", "EnumCreatedEffectObjects", "Escape", "Poll",
    "SendDeviceData", "EnumEffectsInFile", "WriteEffectToFile", "BuildActionMap", "SetActionMap", "GetImageInfo"};
#define D(n) {#n, d_##n}
static const Impl i_dev[] = {{"QueryInterface", q_QueryInterface}, {"AddRef", q_AddRef}, {"Release", q_Release},
    D(GetCapabilities), D(EnumObjects), D(GetProperty), D(SetProperty), D(Acquire), D(Unacquire), D(GetDeviceState),
    D(GetDeviceData), D(SetDataFormat), D(SetEventNotification), D(SetCooperativeLevel), D(GetObjectInfo), D(GetDeviceInfo),
    D(CreateEffect), D(EnumEffects), D(GetForceFeedbackState), D(SendForceFeedbackCommand), D(Escape), D(Poll), {0, 0}};

int win32_dinput(Cpu *c, const char *fn) {
    if (!vt_di) {
        vt_di = make_vtable("IDirectInput8", n_di, 11, i_di);
        vt_dev = make_vtable("IDirectInputDevice8", n_dev, 32, i_dev);
    }
    if (!strcmp(fn, "DirectInput8Create")) {     // (hinst, version, riid, ppvOut, outer)
        DiObj *o = new_obj(1, vt_di);
        M32(arg(c, 3)) = o->guest;
        RET(DI_OK, 5);
    }
    return 0;
}
