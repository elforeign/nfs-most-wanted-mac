// pad_tests.cpp - the virtual pad as the guest sees it: the DirectInput
// joystick device (dx/dinput_joystick.cpp) and, later, XInput.
//
// The host_pad_* callbacks are strong definitions here, backed by a
// test-controlled pad state and edge list, so they override host_api.cpp's
// weak defaults for this binary only. Every other host callback keeps its
// weak default: nothing here draws or plays sound.
//
// Calls go through the real guest path (guest_abi.h, shared with
// dx_tests.cpp): arguments are pushed on the guest stack and the shim is
// reached through imports_dispatch, so a wrong argc in a vtable shows up as a
// stack mismatch.
#include "../com.h"
#include "../dinput_joystick.h"
#include "../dx.h"
#include "../host_api.h"
#include "../../runtime/memory.h"
#include "guest_abi.h"
#include "../../platform/os.h"

#include <initializer_list>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <vector>

// ---------------------------------------------------------------------------
// Test harness
// ---------------------------------------------------------------------------
#define CHECK(c)                                                                                   \
    do {                                                                                           \
        if (!(c)) {                                                                                \
            fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #c);                           \
            ++g_failures;                                                                          \
        }                                                                                          \
    } while (0)
#define CHECK_EQ(a, b)                                                                             \
    do {                                                                                           \
        long long va = (long long)(a), vb = (long long)(b);                                        \
        if (va != vb) {                                                                            \
            ++g_failures;                                                                          \
            fprintf(stderr, "FAIL %s:%d: %s == %s (%lld vs %lld)\n", __FILE__, __LINE__, #a, #b,   \
                    va, vb);                                                                       \
        }                                                                                          \
    } while (0)

// ---------------------------------------------------------------------------
// The fake host pad
// ---------------------------------------------------------------------------
static int g_pad_mode = 2;
static int g_pad_apis = 1;
static HostPadState g_pad;
static uint32_t g_pad_packet = 0;
// The host's retained edges, oldest first, as vpad's ring would hold them.
static std::vector<HostPadEvent> g_pad_events;
static uint32_t g_pad_next_sequence = 1;
static uint16_t g_rumble_low = 0, g_rumble_high = 0;
static const char *const kDefaultAxes = "x,y,z,rz,rx,ry";
static const char *const kDefaultButtons =
    "square,cross,circle,triangle,l1,r1,l2,r2,select,start,l3,r3,ps";

static void pad_reset() {
    g_pad_mode = 2;
    g_pad_apis = 1;
    memset(&g_pad, 0, sizeof g_pad);
    g_pad_packet = 0;
    g_pad_events.clear();
    g_pad_next_sequence = 1;
    g_rumble_low = g_rumble_high = 0;
}

// Queues one edge with the next sequence number.
static void pad_edge(uint8_t kind, uint8_t index, int32_t value) {
    HostPadEvent e;
    memset(&e, 0, sizeof e);
    e.sequence = g_pad_next_sequence++;
    e.kind = kind;
    e.index = index;
    e.value = value;
    g_pad_events.push_back(e);
}

extern "C" {
int host_pad_mode(void) {
    return g_pad_mode;
}
int host_pad_native_apis(void) {
    return g_pad_apis;
}
uint32_t host_pad_state(HostPadState *out) {
    if (out)
        *out = g_pad;
    return g_pad_packet;
}
int host_pad_next_event(uint32_t after, HostPadEvent *out) {
    for (const HostPadEvent &e : g_pad_events)
        if (e.sequence > after) {
            *out = e;
            return 1;
        }
    return 0;
}
void host_pad_rumble(uint16_t low, uint16_t high) {
    g_rumble_low = low;
    g_rumble_high = high;
}
const char *host_pad_native_axes(void) {
    return kDefaultAxes;
}
const char *host_pad_native_buttons(void) {
    return kDefaultButtons;
}
} // extern "C"

static void put_guid(uint32_t at, const uint8_t g[16]) {
    for (uint32_t i = 0; i < 16; ++i)
        wr8(at + i, g[i]);
}

// ===========================================================================
// DirectInput joystick
// ===========================================================================

// IDirectInputA and IDirectInputDevice2A slots.
enum {
    DI_CreateDevice = 3,
    DI_EnumDevices = 4,
    DI_GetDeviceStatus = 5,
    DID_GetCapabilities = 3,
    DID_EnumObjects = 4,
    DID_GetProperty = 5,
    DID_SetProperty = 6,
    DID_Acquire = 7,
    DID_Unacquire = 8,
    DID_GetDeviceState = 9,
    DID_GetDeviceData = 10,
    DID_SetDataFormat = 11,
    DID_GetObjectInfo = 14,
    DID_GetDeviceInfo = 15,
    DID_Poll = 25,
    // IDirectInputDevice8A continues the same vtable: EnumEffectsInFile,
    // WriteEffectToFile, BuildActionMap, SetActionMap, GetImageInfo.
    DID8_GetImageInfo = 31,
};

// E_NOTIMPL, which the version 8 device's own additions answer with.
static const uint32_t DI8_E_NOTIMPL = 0x80004001u;

// SDK numbers, from dinput.h's field lists rather than the shim's constants,
// so a layout mistake cannot hide behind both sides agreeing on it.
enum {
    // DIJOYSTATE: 6 axes, 2 sliders, 4 POVs (32 bytes), 32 buttons = 80.
    SDK_DIJOYSTATE = 80,
    // DIJOYSTATE2: DIJOYSTATE with 128 buttons (176), then velocity,
    // acceleration and force blocks of 6 axes + 2 sliders (32 each) = 272.
    SDK_DIJOYSTATE2 = 272,
    SDK_DIJOYSTATE_POV = 32,
    SDK_DIJOYSTATE_BUTTONS = 48,
    // DIDATAFORMAT: dwSize dwObjSize dwFlags dwDataSize dwNumObjs rgodf.
    SDK_DIDATAFORMAT = 24,
    // DIOBJECTDATAFORMAT: pguid dwOfs dwType dwFlags.
    SDK_DIOBJECTDATAFORMAT = 16,
    // DIDEVICEOBJECTINSTANCEA: dwSize guidType dwOfs dwType dwFlags (32),
    // tszName[260], then the DirectX 5 tail: dwFFMaxForce
    // dwFFForceResolution (8), wCollectionNumber wDesignatorIndex wUsagePage
    // wUsage (8), dwDimension (4), wExponent wReserved (4) = 316.
    SDK_DIDEVICEOBJECTINSTANCEA = 316,
    // The W record has the name as 260 UTF-16 units: 32 + 520 + 24 = 576.
    SDK_DIDEVICEOBJECTINSTANCEW = 576,
    SDK_DIDOI_OFF_tszName = 32,
    // DIPROPRANGE: DIPROPHEADER (16) + lMin + lMax.
    SDK_DIPROPRANGE = 24,
    SDK_DIPROPDWORD = 20,
    // DIDEVCAPS (DirectX 5): 11 dwords. DIDEVCAPS_DX3 stops after dwPOVs:
    // dwSize dwFlags dwDevType dwAxes dwButtons dwPOVs.
    SDK_DIDEVCAPS = 44,
    SDK_DIDEVCAPS_DX3 = 24,
    SDK_DIDEVICEOBJECTDATA = 16,
};

static const uint8_t kGuidJoystick[16] = {0x70, 0x2B, 0x1D, 0x6F, 0xA0, 0xD5, 0xCF, 0x11,
                                          0xBF, 0xC7, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00};
// GUID_XAxis {A36D02E0-C9F3-11CF-BFC7-444553540000} and GUID_YAxis (E1).
static const uint8_t kGuidXAxis[16] = {0xE0, 0x02, 0x6D, 0xA3, 0xF3, 0xC9, 0xCF, 0x11,
                                       0xBF, 0xC7, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00};
static const uint8_t kGuidYAxis[16] = {0xE1, 0x02, 0x6D, 0xA3, 0xF3, 0xC9, 0xCF, 0x11,
                                       0xBF, 0xC7, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00};

static const uint32_t PROP_RANGE = 4, PROP_DEADZONE = 5, PROP_BUFFERSIZE = 1;
static const uint32_t PH_DEVICE = 0, PH_BYOFFSET = 1, PH_BYID = 2;

static void test_joy_sdk_layouts() {
    CHECK_EQ(JOY_DIJOYSTATE_SIZE, SDK_DIJOYSTATE);
    CHECK_EQ(JOY_DIJOYSTATE2_SIZE, SDK_DIJOYSTATE2);
    CHECK_EQ(JOY_OFF_POV, SDK_DIJOYSTATE_POV);
    CHECK_EQ(JOY_OFF_BUTTONS, SDK_DIJOYSTATE_BUTTONS);
    CHECK_EQ(DIDATAFORMAT_SIZE, SDK_DIDATAFORMAT);
    CHECK_EQ(DIOBJECTDATAFORMAT_SIZE, SDK_DIOBJECTDATAFORMAT);
    CHECK_EQ(DIDEVICEOBJECTINSTANCEA_SIZE, SDK_DIDEVICEOBJECTINSTANCEA);
    CHECK_EQ(DIDEVICEOBJECTINSTANCEW_SIZE, SDK_DIDEVICEOBJECTINSTANCEW);
    CHECK_EQ(DIDOI_OFF_tszName, SDK_DIDOI_OFF_tszName);
    CHECK_EQ(DIPROPRANGE_SIZE, SDK_DIPROPRANGE);
    CHECK_EQ(DIPROPDWORD_SIZE, SDK_DIPROPDWORD);
    CHECK_EQ(DIDEVCAPS_SIZE, SDK_DIDEVCAPS);
    CHECK_EQ(DIDEVCAPS_DX3_SIZE, SDK_DIDEVCAPS_DX3);
    CHECK_EQ(DIDEVICEOBJECTDATA_SIZE, SDK_DIDEVICEOBJECTDATA);
    CHECK(!memcmp(GUID_Joystick_, kGuidJoystick, 16));
}

// The pure helpers on their own: a DirectInput 8 caller filters by class and
// sees a gamepad devtype, an older one filters and sees DIDEVTYPE_JOYSTICK.
static void test_joy_pure_helpers() {
    pad_reset();
    CHECK(joy_served());
    g_pad_mode = 1;
    CHECK(!joy_served());
    g_pad_mode = 2;
    g_pad_apis = 2; // XInput only
    CHECK(!joy_served());
    pad_reset();

    CHECK(joy_guid(kGuidJoystick));
    CHECK(joy_guid(GUID_RecompPadInstance_));
    CHECK(!joy_guid(GUID_RecompPadProduct_));
    CHECK(!joy_guid(kGuidXAxis));

    // DirectInput 5/7: DIDEVTYPE_*.
    CHECK(joy_enum_matches(0, 0x0500));
    CHECK(joy_enum_matches(4, 0x0700));
    CHECK(!joy_enum_matches(2, 0x0500)); // mouse
    CHECK(!joy_enum_matches(3, 0x0500)); // keyboard
    CHECK(!joy_enum_matches(0x15, 0x0700));
    // DirectInput 8: DI8DEVCLASS_* and DI8DEVTYPE_*.
    CHECK(joy_enum_matches(0, 0x0800));     // DI8DEVCLASS_ALL
    CHECK(joy_enum_matches(4, 0x0800));     // DI8DEVCLASS_GAMECTRL
    CHECK(joy_enum_matches(0x15, 0x0800));  // DI8DEVTYPE_GAMEPAD
    CHECK(!joy_enum_matches(1, 0x0800));    // DI8DEVCLASS_DEVICE
    CHECK(!joy_enum_matches(2, 0x0800));    // DI8DEVCLASS_POINTER
    CHECK(!joy_enum_matches(3, 0x0800));    // DI8DEVCLASS_KEYBOARD
    CHECK(!joy_enum_matches(0x14, 0x0800)); // DI8DEVTYPE_JOYSTICK

    CHECK_EQ(joy_devtype(0x0500), 0x0404u); // DIDEVTYPE_JOYSTICK, GAMEPAD subtype
    CHECK_EQ(joy_devtype(0x0700), 0x0404u);
    CHECK_EQ(joy_devtype(0x0800), 0x0215u); // DI8DEVTYPE_GAMEPAD, STANDARD subtype

    const std::vector<JoyObject> &objs = joy_objects();
    CHECK_EQ(objs.size(), 20u);

    JoyAxisRange r[6];
    uint32_t ofs = 0, data = 0;
    HostPadEvent e;
    memset(&e, 0, sizeof e);
    e.kind = 0;
    e.index = 0; // cross: DirectInput button 1 in the default order
    e.value = 1;
    CHECK(joy_event(e, r, &ofs, &data));
    CHECK_EQ(ofs, 48u + 1u);
    CHECK_EQ(data, 0x80u);
    e.kind = 1;
    e.value = 1 | 2; // up-right
    CHECK(joy_event(e, r, &ofs, &data));
    CHECK_EQ(ofs, 32u);
    CHECK_EQ(data, 4500u);
    e.value = 0;
    CHECK(joy_event(e, r, &ofs, &data));
    CHECK_EQ(data, 0xFFFFFFFFu);
    e.kind = 2;
    e.index = 3; // ry -> Rz in the default order
    e.value = -32767;
    CHECK(joy_event(e, r, &ofs, &data));
    CHECK_EQ(ofs, 20u);
    CHECK_EQ((int32_t)data, 0);
    e.index = 4; // l2 -> Rx; the event value runs 0..32767
    e.value = 32767;
    CHECK(joy_event(e, r, &ofs, &data));
    CHECK_EQ(ofs, 12u);
    CHECK_EQ((int32_t)data, 65535);
    e.value = 0;
    CHECK(joy_event(e, r, &ofs, &data));
    CHECK_EQ((int32_t)data, 0);
    e.kind = 7;
    CHECK(!joy_event(e, r, &ofs, &data));
}

// The enumeration callback records what it saw.
static uint32_t g_enum_calls = 0;
static char g_enum_name[64];
static uint32_t g_enum_devtype = 0;
static uint8_t g_enum_instance[16];

static void enum_devices_cb(X86 *c) {
    uint32_t inst = arg(c, 0);
    ++g_enum_calls;
    g_enum_devtype = rd32(inst + 36);
    for (uint32_t i = 0; i < 16; ++i)
        g_enum_instance[i] = (uint8_t)rd8(inst + 4 + i);
    uint32_t i = 0;
    for (; i + 1 < sizeof g_enum_name && rd8(inst + 300 + i); ++i)
        g_enum_name[i] = (char)rd8(inst + 300 + i);
    g_enum_name[i] = 0;
    set_eax(c, 1); // DIENUM_CONTINUE
}

static uint32_t make_dinput(uint32_t version) {
    CHECK_EQ(call_shim(tramp("DINPUT.dll", "DirectInputCreateA"), {0x400000, version, sc(0), 0}),
             DI_OK);
    return rd32(sc(0));
}

static void test_joy_enum_devices() {
    cpu_reset();
    pad_reset();
    static uint32_t cb = imports_alloc_trampoline("TEST", "EnumDevicesCb", enum_devices_cb, 2);
    uint32_t di = make_dinput(0x0700);
    CHECK(di != 0);

    g_enum_calls = 0;
    CHECK_EQ(call_method(di, DI_EnumDevices, {4 /* DIDEVTYPE_JOYSTICK */, cb, 0, 1}), DI_OK);
    CHECK_EQ(g_enum_calls, 1u);
    CHECK(!strcmp(g_enum_name, "Controller"));
    CHECK_EQ(g_enum_devtype, 0x0404u);
    CHECK(!memcmp(g_enum_instance, GUID_RecompPadInstance_, 16));

    // All devices: mouse, keyboard and the pad.
    g_enum_calls = 0;
    CHECK_EQ(call_method(di, DI_EnumDevices, {0, cb, 0, 0}), DI_OK);
    CHECK_EQ(g_enum_calls, 3u);

    // A pad with force feedback was asked for: there is none.
    g_enum_calls = 0;
    CHECK_EQ(call_method(di, DI_EnumDevices, {4, cb, 0, 0x100 /* DIEDFL_FORCEFEEDBACK */}), DI_OK);
    CHECK_EQ(g_enum_calls, 0u);

    // Mapped mode: no joystick, as today.
    g_pad_mode = 1;
    g_enum_calls = 0;
    CHECK_EQ(call_method(di, DI_EnumDevices, {4, cb, 0, 1}), DI_OK);
    CHECK_EQ(g_enum_calls, 0u);
    put_guid(sc(0x40), kGuidJoystick);
    CHECK_EQ(call_method(di, DI_CreateDevice, {sc(0x40), sc(0x50), 0}), DIERR_DEVICENOTREG);
    CHECK_EQ(call_method(di, DI_GetDeviceStatus, {sc(0x40)}), S_FALSE);
    g_pad_mode = 2;
    CHECK_EQ(call_method(di, DI_GetDeviceStatus, {sc(0x40)}), DI_OK);
    put_guid(sc(0x40), GUID_RecompPadInstance_);
    CHECK_EQ(call_method(di, DI_GetDeviceStatus, {sc(0x40)}), DI_OK);
}

// A created, formatted and acquired joystick. `format` is the DIDATAFORMAT
// size to set, or 0 to leave the format unset.
static uint32_t make_joystick(uint32_t data_size, uint32_t buffer = 0) {
    uint32_t di = make_dinput(0x0500);
    put_guid(sc(0x40), kGuidJoystick);
    wr32(sc(0x50), 0);
    CHECK_EQ(call_method(di, DI_CreateDevice, {sc(0x40), sc(0x50), 0}), DI_OK);
    uint32_t dev = rd32(sc(0x50));
    CHECK(dev != 0);
    if (!dev)
        return 0;
    if (buffer) {
        uint32_t p = sc(0x60);
        gm_zero(p, SDK_DIPROPDWORD);
        wr32(p, SDK_DIPROPDWORD);
        wr32(p + 4, 16);
        wr32(p + 16, buffer);
        CHECK_EQ(call_method(dev, DID_SetProperty, {PROP_BUFFERSIZE, p}), DI_OK);
    }
    if (data_size) {
        uint32_t df = sc(0x80);
        gm_zero(df, SDK_DIDATAFORMAT);
        wr32(df, SDK_DIDATAFORMAT);
        wr32(df + 4, SDK_DIOBJECTDATAFORMAT);
        wr32(df + 8, 1); // DIDF_ABSAXIS
        wr32(df + 12, data_size);
        CHECK_EQ(call_method(dev, DID_SetDataFormat, {df}), DI_OK);
        CHECK_EQ(call_method(dev, DID_Acquire, {}), DI_OK);
    }
    return dev;
}

static int32_t read_state_axis(uint32_t dev, uint32_t size, uint32_t ofs) {
    uint32_t st = sc(0x400);
    gm_zero(st, size);
    CHECK_EQ(call_method(dev, DID_GetDeviceState, {size, st}), DI_OK);
    return (int32_t)rd32(st + ofs);
}

static void set_range(uint32_t dev, uint32_t how, uint32_t obj, int32_t lo, int32_t hi) {
    uint32_t p = sc(0x100);
    gm_zero(p, SDK_DIPROPRANGE);
    wr32(p, SDK_DIPROPRANGE);
    wr32(p + 4, 16);
    wr32(p + 8, obj);
    wr32(p + 12, how);
    wr32(p + 16, (uint32_t)lo);
    wr32(p + 20, (uint32_t)hi);
    CHECK_EQ(call_method(dev, DID_SetProperty, {PROP_RANGE, p}), DI_OK);
}

// IID_IDirectInput8A {BF798030-483A-4DA2-AA99-5D64ED369700}.
static const uint8_t kIidDirectInput8A[16] = {0x30, 0x80, 0x79, 0xBF, 0x3A, 0x48, 0xD2, 0x4A,
                                              0xAA, 0x99, 0x5D, 0x64, 0xED, 0x36, 0x97, 0x00};
// DI8DEVCLASS_* / DI8DEVTYPE_* filter values, and the pad's version 8 type.
static const uint32_t DI8_CLASS_ALL = 0, DI8_CLASS_GAMECTRL = 4, DI8_TYPE_GAMEPAD = 0x15;
static const uint32_t DI8_PAD_DEVTYPE = 0x0215; // DI8DEVTYPE_GAMEPAD, STANDARD subtype

// DirectInput8Create(hinst, dwVersion, riid, ppvOut, punkOuter). The shim
// serves IID_IDirectInput8A alone, so the riid is handed over as itself.
static uint32_t make_dinput8(uint32_t version) {
    put_guid(sc(0x20), kIidDirectInput8A);
    CHECK_EQ(call_shim(tramp("DINPUT8.dll", "DirectInput8Create"),
                       {0x400000, version, sc(0x20), sc(0), 0}),
             DI_OK);
    return rd32(sc(0));
}

// The pad through DirectInput 8. The version 8 interfaces serve the same
// devices, so the pad has to be enumerated, created and read there too - and
// described by its DI8DEVTYPE_, which is what a version 8 game filters on.
static void test_joy_dinput8() {
    cpu_reset();
    pad_reset();
    static uint32_t cb = imports_alloc_trampoline("TEST", "EnumDevices8Cb", enum_devices_cb, 2);
    uint32_t di = make_dinput8(0x0800);
    CHECK(di != 0);

    // DI8DEVCLASS_GAMECTRL: the pad alone, as a version 8 gamepad.
    g_enum_calls = 0;
    CHECK_EQ(call_method(di, DI_EnumDevices, {DI8_CLASS_GAMECTRL, cb, 0, 1}), DI_OK);
    CHECK_EQ(g_enum_calls, 1u);
    CHECK(!strcmp(g_enum_name, "Controller"));
    CHECK_EQ(g_enum_devtype, DI8_PAD_DEVTYPE);
    CHECK(!memcmp(g_enum_instance, GUID_RecompPadInstance_, 16));

    // An exact DI8DEVTYPE_GAMEPAD names it too.
    g_enum_calls = 0;
    CHECK_EQ(call_method(di, DI_EnumDevices, {DI8_TYPE_GAMEPAD, cb, 0, 0}), DI_OK);
    CHECK_EQ(g_enum_calls, 1u);
    CHECK_EQ(g_enum_devtype, DI8_PAD_DEVTYPE);

    // DI8DEVCLASS_ALL: the mouse, the keyboard and the pad.
    g_enum_calls = 0;
    CHECK_EQ(call_method(di, DI_EnumDevices, {DI8_CLASS_ALL, cb, 0, 0}), DI_OK);
    CHECK_EQ(g_enum_calls, 3u);

    // A pad with force feedback was asked for: there is none.
    g_enum_calls = 0;
    CHECK_EQ(call_method(di, DI_EnumDevices, {DI8_CLASS_GAMECTRL, cb, 0, 0x100}), DI_OK);
    CHECK_EQ(g_enum_calls, 0u);

    // Mapped mode: the version 8 interface serves no joystick either.
    g_pad_mode = 1;
    g_enum_calls = 0;
    CHECK_EQ(call_method(di, DI_EnumDevices, {DI8_CLASS_GAMECTRL, cb, 0, 1}), DI_OK);
    CHECK_EQ(g_enum_calls, 0u);
    put_guid(sc(0x40), kGuidJoystick);
    CHECK_EQ(call_method(di, DI_CreateDevice, {sc(0x40), sc(0x50), 0}), DIERR_DEVICENOTREG);
    CHECK_EQ(call_method(di, DI_GetDeviceStatus, {sc(0x40)}), S_FALSE);
    g_pad_mode = 2;
    CHECK_EQ(call_method(di, DI_GetDeviceStatus, {sc(0x40)}), DI_OK);

    // CreateDevice hands out the version 8 device, whose joystick methods are
    // the same ones: capabilities, the device instance and a read of a stick.
    wr32(sc(0x50), 0);
    CHECK_EQ(call_method(di, DI_CreateDevice, {sc(0x40), sc(0x50), 0}), DI_OK);
    uint32_t dev = rd32(sc(0x50));
    CHECK(dev != 0);
    if (!dev)
        return;

    uint32_t caps = sc(0x200);
    gm_zero(caps, SDK_DIDEVCAPS);
    wr32(caps, SDK_DIDEVCAPS);
    CHECK_EQ(call_method(dev, DID_GetCapabilities, {caps}), DI_OK);
    CHECK_EQ(rd32(caps + 4), 1u); // DIDC_ATTACHED
    CHECK_EQ(rd32(caps + 8), DI8_PAD_DEVTYPE);
    CHECK_EQ(rd32(caps + 12), 6u);
    CHECK_EQ(rd32(caps + 16), 13u);
    CHECK_EQ(rd32(caps + 20), 1u);

    uint32_t info = sc(0x800);
    gm_zero(info, DIDEVICEINSTANCEA_SIZE);
    wr32(info, DIDEVICEINSTANCEA_SIZE);
    CHECK_EQ(call_method(dev, DID_GetDeviceInfo, {info}), DI_OK);
    CHECK_EQ(rd32(info + DIDI_OFF_dwDevType), DI8_PAD_DEVTYPE);
    CHECK(!memcmp(gm_ptr(info + DIDI_OFF_guidInstance), GUID_RecompPadInstance_, 16));

    uint32_t df = sc(0x80);
    gm_zero(df, SDK_DIDATAFORMAT);
    wr32(df, SDK_DIDATAFORMAT);
    wr32(df + 4, SDK_DIOBJECTDATAFORMAT);
    wr32(df + 8, 1); // DIDF_ABSAXIS
    wr32(df + 12, SDK_DIJOYSTATE);
    CHECK_EQ(call_method(dev, DID_SetDataFormat, {df}), DI_OK);
    CHECK_EQ(call_method(dev, DID_Acquire, {}), DI_OK);
    CHECK_EQ(call_method(dev, DID_Poll, {}), DI_OK);
    g_pad.lx = 32767;
    CHECK_EQ(read_state_axis(dev, SDK_DIJOYSTATE, 0), 65535);
    // The version 8 device's own slots, past the ones it shares.
    CHECK_EQ(call_method(dev, DID8_GetImageInfo, {sc(0x900)}), DI8_E_NOTIMPL);
}

static void test_joy_device_state() {
    cpu_reset();
    pad_reset();

    // Unacquired: the same refusal the mouse gives.
    uint32_t raw = make_joystick(0);
    CHECK_EQ(call_method(raw, DID_GetDeviceState, {SDK_DIJOYSTATE, sc(0x400)}), DIERR_NOTACQUIRED);

    uint32_t dev = make_joystick(SDK_DIJOYSTATE);
    CHECK_EQ(call_method(dev, DID_Poll, {}), DI_OK);

    // Neutral: centred axes, POV centred, nothing pressed.
    uint32_t st = sc(0x400);
    memset(gm_ptr(st), 0xAB, SDK_DIJOYSTATE + 4);
    CHECK_EQ(call_method(dev, DID_GetDeviceState, {SDK_DIJOYSTATE, st}), DI_OK);
    CHECK_EQ((int32_t)rd32(st + 0), 32768);
    CHECK_EQ(rd32(st + 32), 0xFFFFFFFFu);
    CHECK_EQ(rd8(st + 48), 0u);
    CHECK_EQ(rd8(st + SDK_DIJOYSTATE), 0xABu); // nothing past the record
    // A size that is not the format's is refused.
    CHECK_EQ(call_method(dev, DID_GetDeviceState, {SDK_DIJOYSTATE2, st}), DIERR_INVALIDPARAM);

    g_pad.lx = 32767;
    CHECK_EQ(read_state_axis(dev, SDK_DIJOYSTATE, 0), 65535);
    g_pad.ly = -32767;
    CHECK_EQ(read_state_axis(dev, SDK_DIJOYSTATE, 4), 0);

    // A 0..1000 range on X, by offset: the stick at rest reads the centre.
    set_range(dev, PH_BYOFFSET, 0, 0, 1000);
    g_pad.lx = 0;
    CHECK_EQ(read_state_axis(dev, SDK_DIJOYSTATE, 0), 500);
    g_pad.lx = 32767;
    CHECK_EQ(read_state_axis(dev, SDK_DIJOYSTATE, 0), 1000);
    // Y still has the default range.
    CHECK_EQ(read_state_axis(dev, SDK_DIJOYSTATE, 4), 0);

    // Range reads back.
    uint32_t p = sc(0x100);
    wr32(p + 8, 0);
    wr32(p + 12, PH_BYOFFSET);
    CHECK_EQ(call_method(dev, DID_GetProperty, {PROP_RANGE, p}), DI_OK);
    CHECK_EQ((int32_t)rd32(p + 16), 0);
    CHECK_EQ((int32_t)rd32(p + 20), 1000);

    // A 20% dead zone: 3000/32767 is inside it.
    gm_zero(p, SDK_DIPROPDWORD);
    wr32(p, SDK_DIPROPDWORD);
    wr32(p + 4, 16);
    wr32(p + 8, 0);
    wr32(p + 12, PH_BYOFFSET);
    wr32(p + 16, 2000);
    CHECK_EQ(call_method(dev, DID_SetProperty, {PROP_DEADZONE, p}), DI_OK);
    g_pad.lx = 3000;
    CHECK_EQ(read_state_axis(dev, SDK_DIJOYSTATE, 0), 500);
    wr32(p + 16, 0);
    CHECK_EQ(call_method(dev, DID_GetProperty, {PROP_DEADZONE, p}), DI_OK);
    CHECK_EQ(rd32(p + 16), 2000u);
    // An object that is not there.
    wr32(p + 8, 7);
    CHECK(call_method(dev, DID_SetProperty, {PROP_DEADZONE, p}) != DI_OK);

    // Cross is button 1 in the default order (square, cross, ...).
    g_pad.buttons = 1 << 0;
    uint32_t btn = (uint32_t)read_state_axis(dev, SDK_DIJOYSTATE, 48);
    CHECK_EQ(btn & 0xff, 0u);
    CHECK_EQ((btn >> 8) & 0xff, 0x80u);

    // Right and down: south-east, 135 degrees.
    g_pad.hat = 2 | 4;
    CHECK_EQ(read_state_axis(dev, SDK_DIJOYSTATE, 32), 13500);

    // L2 fully down drives Rx to its maximum; the device-wide range sets it.
    set_range(dev, PH_DEVICE, 0, -100, 100);
    g_pad.l2 = 255;
    CHECK_EQ(read_state_axis(dev, SDK_DIJOYSTATE, 12), 100);
    g_pad.l2 = 0;
    CHECK_EQ(read_state_axis(dev, SDK_DIJOYSTATE, 12), -100);

    // DIJOYSTATE2 is accepted, and the state is laid out the same.
    uint32_t dev2 = make_joystick(SDK_DIJOYSTATE2);
    g_pad.rx = 32767;
    CHECK_EQ(read_state_axis(dev2, SDK_DIJOYSTATE2, 8), 65535);
    CHECK_EQ(rd8(sc(0x400) + 49), 0x80u);

    // Capabilities: 6 axes, 13 buttons, 1 POV, attached, no force feedback.
    uint32_t caps = sc(0x200);
    gm_zero(caps, SDK_DIDEVCAPS);
    wr32(caps, SDK_DIDEVCAPS);
    CHECK_EQ(call_method(dev, DID_GetCapabilities, {caps}), DI_OK);
    CHECK_EQ(rd32(caps + 4), 1u);
    CHECK_EQ(rd32(caps + 8), 0x0404u);
    CHECK_EQ(rd32(caps + 12), 6u);
    CHECK_EQ(rd32(caps + 16), 13u);
    CHECK_EQ(rd32(caps + 20), 1u);
    // The DirectX 3 record carries the same counts; any other size is refused.
    gm_zero(caps, SDK_DIDEVCAPS);
    wr32(caps, SDK_DIDEVCAPS_DX3);
    wr32(caps + SDK_DIDEVCAPS_DX3, 0xC0FFEEu);
    CHECK_EQ(call_method(dev, DID_GetCapabilities, {caps}), DI_OK);
    CHECK_EQ(rd32(caps + 4), 1u);
    CHECK_EQ(rd32(caps + 8), 0x0404u);
    CHECK_EQ(rd32(caps + 12), 6u);
    CHECK_EQ(rd32(caps + 16), 13u);
    CHECK_EQ(rd32(caps + 20), 1u);
    CHECK_EQ(rd32(caps + SDK_DIDEVCAPS_DX3), 0xC0FFEEu); // nothing past the record
    wr32(caps, 16);
    CHECK_EQ(call_method(dev, DID_GetCapabilities, {caps}), DIERR_INVALIDPARAM);

    // Device info names the pad.
    uint32_t info = sc(0x800);
    wr32(info, 580);
    CHECK_EQ(call_method(dev, DID_GetDeviceInfo, {info}), DI_OK);
    CHECK_EQ(rd32(info + 36), 0x0404u);
    CHECK_EQ(rd8(info + 300), (uint32_t)'C');   // "Controller" (run 13: the pad's shown name)
}

static uint32_t g_obj_calls = 0;
static uint32_t g_obj_axes = 0, g_obj_buttons = 0, g_obj_povs = 0;
static uint32_t g_obj_last_size = 0;
static void enum_objects_cb(X86 *c) {
    uint32_t o = arg(c, 0);
    ++g_obj_calls;
    g_obj_last_size = rd32(o);
    uint32_t type = rd32(o + 24);
    if (type & 3)
        ++g_obj_axes;
    if (type & 0xC)
        ++g_obj_buttons;
    if (type & 0x10)
        ++g_obj_povs;
    set_eax(c, 1);
}

static void test_joy_objects() {
    cpu_reset();
    pad_reset();
    static uint32_t cb = imports_alloc_trampoline("TEST", "EnumObjectsCb", enum_objects_cb, 2);
    uint32_t dev = make_joystick(SDK_DIJOYSTATE);

    g_obj_calls = g_obj_axes = g_obj_buttons = g_obj_povs = 0;
    CHECK_EQ(call_method(dev, DID_EnumObjects, {cb, 0, 0 /* DIDFT_ALL */}), DI_OK);
    CHECK_EQ(g_obj_calls, 20u);
    CHECK_EQ(g_obj_axes, 6u);
    CHECK_EQ(g_obj_buttons, 13u);
    CHECK_EQ(g_obj_povs, 1u);
    CHECK_EQ(g_obj_last_size, SDK_DIDEVICEOBJECTINSTANCEA);

    g_obj_calls = 0;
    CHECK_EQ(call_method(dev, DID_EnumObjects, {cb, 0, 3 /* DIDFT_AXIS */}), DI_OK);
    CHECK_EQ(g_obj_calls, 6u);
    g_obj_calls = 0;
    CHECK_EQ(call_method(dev, DID_EnumObjects, {cb, 0, 0xC /* DIDFT_BUTTON */}), DI_OK);
    CHECK_EQ(g_obj_calls, 13u);
    g_obj_calls = 0;
    CHECK_EQ(call_method(dev, DID_EnumObjects, {cb, 0, 0x10 /* DIDFT_POV */}), DI_OK);
    CHECK_EQ(g_obj_calls, 1u);

    // GetObjectInfo by offset: button 1 is at 49.
    uint32_t o = sc(0x800);
    gm_zero(o, SDK_DIDEVICEOBJECTINSTANCEA);
    wr32(o, SDK_DIDEVICEOBJECTINSTANCEA);
    CHECK_EQ(call_method(dev, DID_GetObjectInfo, {o, 49, PH_BYOFFSET}), DI_OK);
    CHECK_EQ(rd32(o + 20), 49u);
    uint32_t type = rd32(o + 24);
    CHECK_EQ(type & 0xFF, 4u);          // DIDFT_PSHBUTTON
    CHECK_EQ((type >> 8) & 0xFFFF, 1u); // instance 1
    CHECK(rd8(o + SDK_DIDOI_OFF_tszName) != 0);
    // The name is the pad's own: the test order puts cross at button 1.
    CHECK(!strcmp((const char *)gm_ptr(o + SDK_DIDOI_OFF_tszName), "Cross"));
    // ... and by id, which is that dwType.
    gm_zero(o + 4, SDK_DIDEVICEOBJECTINSTANCEA - 4);
    CHECK_EQ(call_method(dev, DID_GetObjectInfo, {o, type, PH_BYID}), DI_OK);
    CHECK_EQ(rd32(o + 20), 49u);
    // X axis carries GUID_XAxis.
    CHECK_EQ(call_method(dev, DID_GetObjectInfo, {o, 0, PH_BYOFFSET}), DI_OK);
    CHECK(!memcmp(gm_ptr(o + 4), kGuidXAxis, 16));
    CHECK(!strcmp((const char *)gm_ptr(o + SDK_DIDOI_OFF_tszName), "Left Stick X"));
    // Rz is the right stick's Y in the test order (x,y,z,rz,...): offset 20.
    CHECK_EQ(call_method(dev, DID_GetObjectInfo, {o, 20, PH_BYOFFSET}), DI_OK);
    CHECK(!strcmp((const char *)gm_ptr(o + SDK_DIDOI_OFF_tszName), "Right Stick Y"));
    CHECK(call_method(dev, DID_GetObjectInfo, {o, 7, PH_BYOFFSET}) != DI_OK);

    // A wide DirectInput hands out the W record.
    uint32_t w = sc(0xC00);
    gm_zero(w, SDK_DIDEVICEOBJECTINSTANCEW);
    wr32(w, SDK_DIDEVICEOBJECTINSTANCEW);
    CHECK_EQ(call_shim(tramp("DINPUT.dll", "DirectInputCreateW"), {0x400000, 0x0700, sc(0), 0}),
             DI_OK);
    uint32_t diw = rd32(sc(0));
    put_guid(sc(0x40), kGuidJoystick);
    CHECK_EQ(call_method(diw, DI_CreateDevice, {sc(0x40), sc(0x50), 0}), DI_OK);
    uint32_t devw = rd32(sc(0x50));
    CHECK_EQ(call_method(devw, DID_GetObjectInfo, {w, 0, PH_BYOFFSET}), DI_OK);
    CHECK(rd16(w + SDK_DIDOI_OFF_tszName) != 0);
    CHECK_EQ(rd8(w + SDK_DIDOI_OFF_tszName + 1), 0u);
    g_obj_calls = 0;
    CHECK_EQ(call_method(devw, DID_EnumObjects, {cb, 0, 0}), DI_OK);
    CHECK_EQ(g_obj_calls, 20u);
    CHECK_EQ(g_obj_last_size, SDK_DIDEVICEOBJECTINSTANCEW);
}

static void test_joy_device_data() {
    cpu_reset();
    pad_reset();
    uint32_t dev = make_joystick(SDK_DIJOYSTATE, 16);

    pad_edge(0, 0, 1); // cross down
    pad_edge(0, 0, 0); // cross up
    uint32_t out = sc(0x400), inout = sc(0x60);
    const uint32_t N = SDK_DIDEVICEOBJECTDATA;

    // A buffer that runs off the end of the guest arena fails whole: no
    // record, the count untouched, and the edges still waiting.
    uint32_t tail = (uint32_t)(GUEST_SIZE - N - 4);
    wr32(inout, 8);
    CHECK_EQ(call_method(dev, DID_GetDeviceData, {N, tail, inout, 0}), DIERR_INVALIDPARAM);
    CHECK_EQ(rd32(inout), 8u);
    CHECK_EQ(rd32(tail), 0u);

    // Peek reports without consuming.
    wr32(inout, 8);
    CHECK_EQ(call_method(dev, DID_GetDeviceData, {N, out, inout, 1 /* DIGDD_PEEK */}), DI_OK);
    CHECK_EQ(rd32(inout), 2u);

    wr32(inout, 8);
    CHECK_EQ(call_method(dev, DID_GetDeviceData, {N, out, inout, 0}), DI_OK);
    CHECK_EQ(rd32(inout), 2u);
    CHECK_EQ(rd32(out + 0), 49u);
    CHECK_EQ(rd32(out + 4), 0x80u);
    CHECK_EQ(rd32(out + N + 0), 49u);
    CHECK_EQ(rd32(out + N + 4), 0u);
    CHECK(rd32(out + N + 12) > rd32(out + 12)); // sequence grows

    wr32(inout, 8);
    CHECK_EQ(call_method(dev, DID_GetDeviceData, {N, out, inout, 0}), DI_OK);
    CHECK_EQ(rd32(inout), 0u);

    // The caller's count limits one call; the rest stay for the next.
    pad_edge(1, 0, 1);      // hat up
    pad_edge(2, 0, -32767); // lx full left
    wr32(inout, 1);
    CHECK_EQ(call_method(dev, DID_GetDeviceData, {N, out, inout, 0}), DI_OK);
    CHECK_EQ(rd32(inout), 1u);
    CHECK_EQ(rd32(out + 0), 32u);
    CHECK_EQ(rd32(out + 4), 0u);
    wr32(inout, 4);
    CHECK_EQ(call_method(dev, DID_GetDeviceData, {N, out, inout, 0}), DI_OK);
    CHECK_EQ(rd32(inout), 1u);
    CHECK_EQ(rd32(out + 0), 0u);
    CHECK_EQ((int32_t)rd32(out + 4), 0);

    // The DirectInput 8 stride carries uAppData.
    pad_edge(0, 2, 1); // square: button 0
    wr32(out + 16, 0xdeadbeef);
    wr32(inout, 4);
    CHECK_EQ(call_method(dev, DID_GetDeviceData, {20, out, inout, 0}), DI_OK);
    CHECK_EQ(rd32(inout), 1u);
    CHECK_EQ(rd32(out + 0), 48u);
    CHECK_EQ(rd32(out + 16), 0u);

    // The host dropped edges this reader never saw.
    g_pad_events.clear();
    g_pad_next_sequence += 300;
    pad_edge(0, 0, 1);
    wr32(inout, 4);
    CHECK_EQ(call_method(dev, DID_GetDeviceData, {N, out, inout, 0}), 1u /* DI_BUFFEROVERFLOW */);
    CHECK_EQ(rd32(inout), 1u);
    wr32(inout, 4);
    CHECK_EQ(call_method(dev, DID_GetDeviceData, {N, out, inout, 0}), DI_OK);
    CHECK_EQ(rd32(inout), 0u);

    // Edges from before Acquire are not replayed.
    pad_edge(0, 0, 0);
    uint32_t fresh = make_joystick(SDK_DIJOYSTATE, 16);
    wr32(inout, 4);
    CHECK_EQ(call_method(fresh, DID_GetDeviceData, {N, out, inout, 0}), DI_OK);
    CHECK_EQ(rd32(inout), 0u);
}

// A custom format: two axes and two buttons at offsets of the game's choice.
static void test_joy_custom_format() {
    cpu_reset();
    pad_reset();
    uint32_t dev = make_joystick(0, 16);

    uint32_t gx = sc(0x300), gy = sc(0x310);
    put_guid(gx, kGuidXAxis);
    put_guid(gy, kGuidYAxis);
    const uint32_t ANY = 0x00FFFF00u;
    uint32_t odf = sc(0x320);
    struct {
        uint32_t guid, ofs, type;
    } objs[] = {
        {gy, 0, 3 | ANY},        // Y at 0
        {gx, 4, 2 | ANY},        // X at 4
        {0, 8, 0xC | (1u << 8)}, // button instance 1 (cross) at 8
        {0, 9, 0xC | ANY},       // the first free button (square) at 9
    };
    for (uint32_t i = 0; i < 4; ++i) {
        uint32_t a = odf + i * SDK_DIOBJECTDATAFORMAT;
        wr32(a, objs[i].guid);
        wr32(a + 4, objs[i].ofs);
        wr32(a + 8, objs[i].type);
        wr32(a + 12, 0);
    }
    uint32_t df = sc(0x80);
    wr32(df, SDK_DIDATAFORMAT);
    wr32(df + 4, SDK_DIOBJECTDATAFORMAT);
    wr32(df + 8, 1);
    wr32(df + 12, 16); // bytes 10..15 are padding the device never writes
    wr32(df + 16, 4);
    wr32(df + 20, odf);
    CHECK_EQ(call_method(dev, DID_SetDataFormat, {df}), DI_OK);
    CHECK_EQ(call_method(dev, DID_Acquire, {}), DI_OK);

    g_pad.lx = 32767;
    g_pad.ly = -32767;
    g_pad.buttons = 1 << 0; // cross
    uint32_t st = sc(0x400);
    memset(gm_ptr(st), 0x5A, 16);
    CHECK_EQ(call_method(dev, DID_GetDeviceState, {16, st}), DI_OK);
    CHECK_EQ((int32_t)rd32(st + 0), 0);
    CHECK_EQ((int32_t)rd32(st + 4), 65535);
    CHECK_EQ(rd8(st + 8), 0x80u);
    CHECK_EQ(rd8(st + 9), 0u);
    CHECK_EQ(rd8(st + 10), 0x5Au);
    CHECK_EQ(rd8(st + 15), 0x5Au);

    // Events report the format's offsets, and objects outside it are dropped.
    pad_edge(0, 1, 1); // circle: not in the format
    pad_edge(0, 2, 1); // square: at 9
    uint32_t out = sc(0x500), inout = sc(0x60);
    wr32(inout, 8);
    CHECK_EQ(call_method(dev, DID_GetDeviceData, {16, out, inout, 0}), DI_OK);
    CHECK_EQ(rd32(inout), 1u);
    CHECK_EQ(rd32(out), 9u);

    // A property by offset uses the format's offsets too: X is at 4.
    set_range(dev, PH_BYOFFSET, 4, 0, 10);
    CHECK_EQ(call_method(dev, DID_GetDeviceState, {16, st}), DI_OK);
    CHECK_EQ((int32_t)rd32(st + 4), 10);

    // An object this pad does not have is refused. The format changes only
    // while the device is not acquired.
    CHECK_EQ(call_method(dev, DID_SetDataFormat, {df}), DIERR_ACQUIRED);
    CHECK_EQ(call_method(dev, DID_Unacquire, {}), DI_OK);
    uint8_t slider[16];
    memcpy(slider, kGuidXAxis, 16);
    slider[0] = 0xE4; // GUID_Slider
    put_guid(gx, slider);
    CHECK_EQ(call_method(dev, DID_SetDataFormat, {df}), DIERR_INVALIDPARAM);
    // ... unless it is optional.
    wr32(odf + SDK_DIOBJECTDATAFORMAT + 8, 2 | ANY | 0x80000000u); // DIDFT_OPTIONAL
    CHECK_EQ(call_method(dev, DID_SetDataFormat, {df}), DI_OK);
}

// ===========================================================================
// XInput
// ===========================================================================

// dwPacketNumber (4), then XINPUT_GAMEPAD: wButtons (2), bLeftTrigger (1),
// bRightTrigger (1), sThumbLX/LY/RX/RY (2 each) = 16 bytes total.
enum {
    XI_STATE_OFF_dwPacketNumber = 0,
    XI_STATE_OFF_wButtons = 4,
    XI_STATE_OFF_sThumbLY = 10,
    XI_STATE_OFF_sThumbRY = 14,
};
static const uint32_t XI_ERROR_SUCCESS = 0, XI_ERROR_BAD_ARGUMENTS = 160,
                      XI_ERROR_DEVICE_NOT_CONNECTED = 1167, XI_ERROR_EMPTY = 4306;
static const uint32_t XUSER_INDEX_ANY = 0xFF;


// ---------------------------------------------------------------------------
// Force feedback (dinput_effect.cpp), in NFS Underground's order (run 8 research): the X/Y actuator count from
// EnumObjects, SETACTUATORSON, then effects made, started, polled and updated through IDirectInputEffect.
// ---------------------------------------------------------------------------
enum { DID_CreateEffect = 18, DID_GetForceFeedbackState = 21, DID_SendForceFeedbackCommand = 22 };
enum { EFF_Release = 2, EFF_GetEffectGuid = 4, EFF_SetParameters = 6, EFF_Start = 7, EFF_Stop = 8,
       EFF_GetEffectStatus = 9, EFF_Unload = 11 };
static uint32_t g_ff_axes = 0, g_ff_flagged = 0;
static void ff_objects_cb(X86 *c) {
    uint32_t o = arg(c, 0);
    if (rd32(o + 24) & 3) {
        ++g_ff_axes;
        if (rd32(o + 28) & 1) // DIDOI_FFACTUATOR
            ++g_ff_flagged;
    }
    set_eax(c, 1);
}
static void ff_guid(uint32_t at, uint32_t data1) {
    static const uint8_t tail[12] = {0x33, 0x8E, 0xD0, 0x11, 0x9A, 0xD0, 0x00, 0xA0, 0xC9, 0xA0, 0x6E, 0x35};
    wr32(at, data1);
    for (uint32_t i = 0; i < 12; ++i)
        wr8(at + 4 + i, tail[i]);
}
// UG1's DIEFFECT (0x38 bytes, cartesian, object offsets, X/Y, no trigger) at sc(0x1000); envelope at sc(0x1080),
// type-specific parameters at sc(0x10a0).
static uint32_t ff_effect(uint32_t duration, uint32_t fade_time, uint32_t cb, std::initializer_list<uint32_t> params) {
    uint32_t e = sc(0x1000), env = sc(0x1080), ts = sc(0x10a0), axes = sc(0x10e0), dirs = sc(0x10f0);
    gm_zero(e, 0x38);
    wr32(e, 0x38);
    wr32(e + 4, 0x12);
    wr32(e + 8, duration);
    wr32(e + 0x10, 10000);
    wr32(e + 0x14, 0xFFFFFFFFu);
    wr32(e + 0x1c, 2);
    wr32(axes, 0);
    wr32(axes + 4, 4);
    wr32(e + 0x20, axes);
    wr32(dirs, 1);
    wr32(dirs + 4, 0);
    wr32(e + 0x24, dirs);
    gm_zero(env, 0x14);
    wr32(env, 0x14);
    wr32(env + 16, fade_time);
    wr32(e + 0x28, fade_time ? env : 0);
    gm_zero(ts, 48);
    uint32_t i = 0;
    for (uint32_t v : params)
        wr32(ts + 4 * i++, v);
    wr32(e + 0x2c, cb);
    wr32(e + 0x30, ts);
    return e;
}
static uint32_t ff_make(uint32_t dev, uint32_t data1, uint32_t eff) {
    ff_guid(sc(0x1100), data1);
    wr32(sc(0x1110), 0);
    CHECK_EQ(call_method(dev, DID_CreateEffect, {sc(0x1100), eff, sc(0x1110), 0}), DI_OK);
    return rd32(sc(0x1110));
}
static uint32_t ff_status(uint32_t eff) {
    wr32(sc(0x1120), 0xAAAA);
    CHECK_EQ(call_method(eff, EFF_GetEffectStatus, {sc(0x1120)}), DI_OK);
    return rd32(sc(0x1120));
}
static void ff_sleep_ms(uint64_t ms) {
    uint64_t until = os_monotonic_ns() + ms * 1000000ull;
    while (os_monotonic_ns() < until) {
    }
}

static void test_force_feedback() {
    cpu_reset();
    pad_reset();
    static uint32_t cb = imports_alloc_trampoline("TEST", "FFObjectsCb", ff_objects_cb, 2);

    // Off (the default): no actuators, no command, no effects - exactly the joystick before force feedback.
    unsetenv("RECOMP_DINPUT_FF");
    uint32_t dev = make_joystick(SDK_DIJOYSTATE2);
    g_ff_axes = g_ff_flagged = 0;
    CHECK_EQ(call_method(dev, DID_EnumObjects, {cb, 0, 0}), DI_OK);
    CHECK_EQ(g_ff_axes, 6u);
    CHECK_EQ(g_ff_flagged, 0u);
    CHECK_EQ(call_method(dev, DID_SendForceFeedbackCommand, {0x10}), DIERR_UNSUPPORTED);
    uint32_t sq = ff_effect(150000, 20000, 16, {10000, 0, 0, 75000});
    ff_guid(sc(0x1100), 0x13541C22u);
    CHECK_EQ(call_method(dev, DID_CreateEffect, {sc(0x1100), sq, sc(0x1110), 0}), DIERR_UNSUPPORTED);

    // On: X and Y are the actuators (UG1 counts them, capped at 2), and the actuators switch on.
    setenv("RECOMP_DINPUT_FF", "1", 1);
    g_ff_axes = g_ff_flagged = 0;
    CHECK_EQ(call_method(dev, DID_EnumObjects, {cb, 0, 0}), DI_OK);
    CHECK_EQ(g_ff_flagged, 2u);
    CHECK_EQ(call_method(dev, DID_SendForceFeedbackCommand, {0x10}), DI_OK);
    uint32_t caps = sc(0x200);
    gm_zero(caps, SDK_DIDEVCAPS);
    wr32(caps, SDK_DIDEVCAPS);
    CHECK_EQ(call_method(dev, DID_GetCapabilities, {caps}), DI_OK);
    CHECK_EQ(rd32(caps + 4) & 0x100u, 0x100u); // DIDC_FORCEFEEDBACK
    // An effect that is not one of the standard twelve.
    ff_guid(sc(0x1100), 0x13541C2Cu);
    CHECK_EQ(call_method(dev, DID_CreateEffect, {sc(0x1100), 0, sc(0x1110), 0}), DIERR_DEVICENOTREG);

    // The collision jolt: Square, 150 ms, a 20 ms fade. Not playing until started; then both motors, low heavier.
    uint32_t jolt = ff_make(dev, 0x13541C22u, ff_effect(150000, 20000, 16, {10000, 0, 0, 75000}));
    CHECK(jolt != 0);
    // QueryInterface(IID_IDirectInputEffect) finds the same object (its kind is bound past the old 64-kind mask).
    static const uint8_t kIidEffect[16] = {0xC0, 0xF7, 0xE1, 0xE7, 0xD2, 0x88, 0xD0, 0x11,
                                           0x9A, 0xD0, 0x00, 0xA0, 0xC9, 0xA0, 0x6E, 0x35};
    put_guid(sc(0x1130), kIidEffect);
    wr32(sc(0x1140), 0);
    CHECK_EQ(call_method(jolt, 0, {sc(0x1130), sc(0x1140)}), 0u);
    CHECK_EQ(rd32(sc(0x1140)), jolt);
    call_method(jolt, EFF_Release, {});
    CHECK_EQ(ff_status(jolt), 0u);
    CHECK_EQ(g_rumble_low, 0);
    CHECK_EQ(call_method(jolt, EFF_Start, {1, 0}), DI_OK);
    CHECK_EQ(ff_status(jolt), 1u);
    CHECK_EQ(g_rumble_low, 65535);
    CHECK_EQ(g_rumble_high, 39321); // 0.6
    // It runs out by itself: the status reads 0 (UG1 fires the next jolt only then) and the motors rest.
    ff_sleep_ms(170);
    CHECK_EQ(ff_status(jolt), 0u);
    CHECK_EQ(g_rumble_low, 0);
    CHECK_EQ(g_rumble_high, 0);
    // Fired again, it plays again.
    CHECK_EQ(call_method(jolt, EFF_Start, {1, 0}), DI_OK);
    CHECK_EQ(ff_status(jolt), 1u);
    CHECK_EQ(call_method(jolt, EFF_Stop, {}), DI_OK);
    CHECK_EQ(ff_status(jolt), 0u);

    // A road surface: an infinite Sine on the high motor; steering load (ConstantForce) and centering (Spring, two
    // DICONDITIONs) play and report their status but drive no motor.
    uint32_t surface = ff_make(dev, 0x13541C23u, ff_effect(0xFFFFFFFFu, 0, 16, {5000, 0, 0, 40000}));
    uint32_t steer = ff_make(dev, 0x13541C20u, ff_effect(0xFFFFFFFFu, 0, 4, {(uint32_t)-8627}));
    uint32_t spring = ff_make(dev, 0x13541C27u, ff_effect(0xFFFFFFFFu, 0, 48, {0, 7058, 7058, 7058, 7058, 0}));
    CHECK(surface && steer && spring);
    CHECK_EQ(call_method(steer, EFF_Start, {1, 0}), DI_OK);
    CHECK_EQ(call_method(spring, EFF_Start, {1, 0}), DI_OK);
    CHECK_EQ(ff_status(steer), 1u);
    CHECK_EQ(ff_status(spring), 1u);
    CHECK_EQ(g_rumble_low, 0);
    CHECK_EQ(g_rumble_high, 0);
    CHECK_EQ(call_method(surface, EFF_Start, {1, 0}), DI_OK);
    CHECK_EQ(g_rumble_low, 0);
    CHECK_EQ(g_rumble_high, 26214); // 0.8 x 5000
    // UG1 updates a surface with SetParameters(DIEP_DIRECTION | DIEP_TYPESPECIFICPARAMS): it keeps playing.
    ff_effect(0xFFFFFFFFu, 0, 16, {10000, 0, 0, 20000});
    CHECK_EQ(call_method(surface, EFF_SetParameters, {sc(0x1000), 0x140}), DI_OK);
    CHECK_EQ(ff_status(surface), 1u);
    CHECK_EQ(g_rumble_high, 52428);
    // A wrong type-specific size is refused and changes nothing.
    ff_effect(0xFFFFFFFFu, 0, 4, {1});
    CHECK_EQ(call_method(surface, EFF_SetParameters, {sc(0x1000), 0x140}), DIERR_INVALIDPARAM);
    CHECK_EQ(g_rumble_high, 52428);

    // The device gain scales every effect.
    uint32_t pd = sc(0x300);
    gm_zero(pd, SDK_DIPROPDWORD);
    wr32(pd, SDK_DIPROPDWORD);
    wr32(pd + 4, 16);
    wr32(pd + 16, 5000);
    CHECK_EQ(call_method(dev, DID_SetProperty, {7 /* DIPROP_FFGAIN */, pd}), DI_OK);
    CHECK_EQ(g_rumble_high, 26214);
    wr32(pd + 16, 0);
    CHECK_EQ(call_method(dev, DID_GetProperty, {7, pd}), DI_OK);
    CHECK_EQ(rd32(pd + 16), 5000u);
    wr32(pd + 16, 10000);
    CHECK_EQ(call_method(dev, DID_SetProperty, {7, pd}), DI_OK);

    // A pause menu: the game keeps reading the pad but stops calling its effects. After 200 ms the motors rest;
    // the next effect call brings them back (the effect never stopped).
    ff_sleep_ms(230);
    CHECK_EQ(call_method(dev, DID_Poll, {}), DI_OK);
    CHECK_EQ(g_rumble_high, 0);
    CHECK_EQ(ff_status(surface), 1u);
    CHECK_EQ(g_rumble_high, 52428);

    // SETACTUATORSOFF mutes, ON restores; STOPALL stops every effect.
    CHECK_EQ(call_method(dev, DID_SendForceFeedbackCommand, {0x20}), DI_OK);
    CHECK_EQ(g_rumble_high, 0);
    CHECK_EQ(call_method(dev, DID_SendForceFeedbackCommand, {0x10}), DI_OK);
    CHECK_EQ(g_rumble_high, 52428);
    uint32_t st = sc(0x320);
    CHECK_EQ(call_method(dev, DID_GetForceFeedbackState, {st}), DI_OK);
    CHECK_EQ(rd32(st) & 0x13u, 0x10u); // not empty, not stopped, actuators on
    CHECK_EQ(call_method(dev, DID_SendForceFeedbackCommand, {0x02}), DI_OK);
    CHECK_EQ(ff_status(surface), 0u);
    CHECK_EQ(g_rumble_high, 0);

    // Unacquire stops a device's effects, as DirectInput does.
    CHECK_EQ(call_method(surface, EFF_Start, {1, 0}), DI_OK);
    CHECK_EQ(g_rumble_high, 52428);
    CHECK_EQ(call_method(dev, DID_Unacquire, {}), DI_OK);
    CHECK_EQ(ff_status(surface), 0u);
    CHECK_EQ(g_rumble_high, 0);

    // Unload stops; Release frees (a released playing effect leaves the motors).
    CHECK_EQ(call_method(dev, DID_Acquire, {}), DI_OK);
    CHECK_EQ(call_method(surface, EFF_Start, {1, 0}), DI_OK);
    CHECK_EQ(call_method(surface, EFF_Unload, {}), DI_OK);
    CHECK_EQ(g_rumble_high, 0);
    CHECK_EQ(call_method(surface, EFF_Start, {1, 0}), DI_OK);
    CHECK_EQ(g_rumble_high, 52428);
    CHECK_EQ(call_method(surface, EFF_Release, {}), 0u);
    CHECK_EQ(g_rumble_high, 0);
    for (uint32_t e : {jolt, steer, spring})
        call_method(e, EFF_Release, {});
    unsetenv("RECOMP_DINPUT_FF");
}

static void test_xinput() {
    cpu_reset();
    pad_reset();
    xinput_reset_for_test();
    g_pad_apis = 3; // DirectInput joystick and XInput both native

    uint32_t get_state = tramp("xinput1_3.dll", "XInputGetState");
    uint32_t set_state = tramp("xinput1_3.dll", "XInputSetState");
    uint32_t get_caps = tramp("xinput1_3.dll", "XInputGetCapabilities");
    uint32_t enable = tramp("xinput1_3.dll", "XInputEnable");
    uint32_t get_battery = tramp("xinput1_3.dll", "XInputGetBatteryInformation");
    uint32_t keystroke = tramp("xinput1_3.dll", "XInputGetKeystroke");
    uint32_t st = sc(0x400);

    // Any user but 0 is not connected, even when served.
    CHECK_EQ(call_shim(get_state, {1, st}), XI_ERROR_DEVICE_NOT_CONNECTED);

    // Mapped mode (not native): not connected either.
    g_pad_mode = 1;
    CHECK_EQ(call_shim(get_state, {0, st}), XI_ERROR_DEVICE_NOT_CONNECTED);
    g_pad_mode = 2;

    // A null state pointer is a caller bug, not "no controller": refused
    // before anything is read from the pad.
    CHECK_EQ(call_shim(get_state, {0, 0}), XI_ERROR_BAD_ARGUMENTS);

    // Served: cross + hat up -> buttons 0x1001 (A | DPAD_UP); the Y axes
    // invert (XInput up is positive, the pad's is negative) and clamp.
    g_pad.buttons = 1 << 0; // cross
    g_pad.hat = 1;          // up
    g_pad.ly = -32767;
    g_pad.ry = -32768; // out of the pad's own -32767..32767 range: clamp it
    g_pad_packet = 7;
    CHECK_EQ(call_shim(get_state, {0, st}), XI_ERROR_SUCCESS);
    CHECK_EQ(rd32(st + XI_STATE_OFF_dwPacketNumber), 7u);
    CHECK_EQ(rd16(st + XI_STATE_OFF_wButtons), 0x1001u);
    CHECK_EQ((int16_t)rd16(st + XI_STATE_OFF_sThumbLY), 32767);
    CHECK_EQ((int16_t)rd16(st + XI_STATE_OFF_sThumbRY), 32767);

    // The packet number is the host's own, unmodified.
    g_pad_packet = 8;
    CHECK_EQ(call_shim(get_state, {0, st}), XI_ERROR_SUCCESS);
    CHECK_EQ(rd32(st + XI_STATE_OFF_dwPacketNumber), 8u);

    // Capabilities: the fixed answer from the brief, not the live pad state.
    uint32_t caps = sc(0x480);
    CHECK_EQ(call_shim(get_caps, {0, 1 /* XINPUT_FLAG_GAMEPAD */, caps}), XI_ERROR_SUCCESS);
    CHECK_EQ(rd8(caps + 0), 1u);        // XINPUT_DEVTYPE_GAMEPAD
    CHECK_EQ(rd8(caps + 1), 1u);        // XINPUT_DEVSUBTYPE_GAMEPAD
    CHECK_EQ(rd16(caps + 4), 0xF3FFu);  // wButtons: all but 0x0400/0x0800
    CHECK_EQ(rd8(caps + 6), 255u);      // bLeftTrigger
    CHECK_EQ(rd8(caps + 7), 255u);      // bRightTrigger
    CHECK_EQ(rd16(caps + 8), 0xFFC0u);  // sThumbLX
    CHECK_EQ(rd16(caps + 10), 0xFFC0u); // sThumbLY
    CHECK_EQ(rd16(caps + 12), 0xFFC0u); // sThumbRX
    CHECK_EQ(rd16(caps + 14), 0xFFC0u); // sThumbRY
    CHECK_EQ(rd16(caps + 16), 0xFFFFu); // wLeftMotorSpeed
    CHECK_EQ(rd16(caps + 18), 0xFFFFu); // wRightMotorSpeed

    // Battery: the pad is always wired and full.
    uint32_t batt = sc(0x4A0);
    CHECK_EQ(call_shim(get_battery, {0, 0, batt}), XI_ERROR_SUCCESS);
    CHECK_EQ(rd8(batt + 0), 1u); // BATTERY_TYPE_WIRED
    CHECK_EQ(rd8(batt + 1), 3u); // BATTERY_LEVEL_FULL

    // SetState reaches the host rumble callback directly.
    uint32_t vib = sc(0x500);
    wr16(vib + 0, 1000);
    wr16(vib + 2, 2000);
    CHECK_EQ(call_shim(set_state, {0, vib}), XI_ERROR_SUCCESS);
    CHECK_EQ(g_rumble_low, 1000u);
    CHECK_EQ(g_rumble_high, 2000u);

    // Disabling sends one zero rumble and zeros the reported state, but the
    // packet number keeps tracking the real one.
    CHECK_EQ(call_shim(enable, {0}), XI_ERROR_SUCCESS);
    CHECK_EQ(g_rumble_low, 0u);
    CHECK_EQ(g_rumble_high, 0u);
    g_pad_packet = 9;
    CHECK_EQ(call_shim(get_state, {0, st}), XI_ERROR_SUCCESS);
    CHECK_EQ(rd32(st + XI_STATE_OFF_dwPacketNumber), 9u);
    CHECK_EQ(rd16(st + XI_STATE_OFF_wButtons), 0u);

    // SetState while disabled stores the values without forwarding them to
    // the host; re-enabling resends exactly what was last asked for.
    wr16(vib + 0, 5);
    wr16(vib + 2, 6);
    CHECK_EQ(call_shim(set_state, {0, vib}), XI_ERROR_SUCCESS);
    CHECK_EQ(g_rumble_low, 0u);
    CHECK_EQ(g_rumble_high, 0u);
    CHECK_EQ(call_shim(enable, {1}), XI_ERROR_SUCCESS);
    CHECK_EQ(g_rumble_low, 5u);
    CHECK_EQ(g_rumble_high, 6u);

    // On first use, only edges from now on are replayed: a "stale" edge
    // queued before the game's first XInputGetKeystroke call is skipped
    // rather than dumped as a backlog.
    uint32_t ks = sc(0x600);
    pad_edge(0, 0, 1); // cross, before the first call
    CHECK_EQ(call_shim(keystroke, {0, 0, ks}), XI_ERROR_EMPTY);

    // A cross press after that becomes one keystroke, with the full record
    // checked (Flags is a WORD at offset 4, not a byte); the queue then
    // reports empty.
    pad_edge(0, 0, 1);
    memset(gm_ptr(ks), 0xAB, 8);
    CHECK_EQ(call_shim(keystroke, {0, 0, ks}), XI_ERROR_SUCCESS);
    CHECK_EQ(rd16(ks + 0), 0x5800u); // VirtualKey
    CHECK_EQ(rd16(ks + 4), 1u);      // Flags: XINPUT_KEYSTROKE_KEYDOWN
    CHECK_EQ(rd8(ks + 6), 0u);       // UserIndex
    CHECK_EQ(call_shim(keystroke, {0, 0, ks}), XI_ERROR_EMPTY);

    // XUSER_INDEX_ANY is accepted for the keystroke queue.
    pad_edge(0, 1, 1); // circle
    CHECK_EQ(call_shim(keystroke, {XUSER_INDEX_ANY, 0, ks}), XI_ERROR_SUCCESS);
    CHECK_EQ(rd16(ks + 0), 0x5801u);
    CHECK_EQ(call_shim(keystroke, {XUSER_INDEX_ANY, 0, ks}), XI_ERROR_EMPTY);

    // A null or invalid keystroke pointer is refused before the queue is
    // touched: a bad pointer must not silently eat a pending keystroke.
    pad_edge(0, 2, 1); // square
    CHECK_EQ(call_shim(keystroke, {0, 0, 0}), XI_ERROR_BAD_ARGUMENTS);
    CHECK_EQ(call_shim(keystroke, {0, 0, ks}), XI_ERROR_SUCCESS);
    CHECK_EQ(rd16(ks + 0), 0x5802u); // still there: the bad call above ate nothing

    // A diagonal hat move queues two KEYDOWNs (bit order: up before right);
    // releasing both queues two KEYUPs in the same order.
    pad_edge(1, 0, 1 | 2); // up-right
    CHECK_EQ(call_shim(keystroke, {0, 0, ks}), XI_ERROR_SUCCESS);
    CHECK_EQ(rd16(ks + 0), 0x5810u); // up
    CHECK_EQ(rd16(ks + 4), 1u);
    CHECK_EQ(call_shim(keystroke, {0, 0, ks}), XI_ERROR_SUCCESS);
    CHECK_EQ(rd16(ks + 0), 0x5813u); // right
    CHECK_EQ(rd16(ks + 4), 1u);
    CHECK_EQ(call_shim(keystroke, {0, 0, ks}), XI_ERROR_EMPTY);

    pad_edge(1, 0, 0); // released
    CHECK_EQ(call_shim(keystroke, {0, 0, ks}), XI_ERROR_SUCCESS);
    CHECK_EQ(rd16(ks + 0), 0x5810u);
    CHECK_EQ(rd16(ks + 4), 2u); // XINPUT_KEYSTROKE_KEYUP
    CHECK_EQ(call_shim(keystroke, {0, 0, ks}), XI_ERROR_SUCCESS);
    CHECK_EQ(rd16(ks + 0), 0x5813u);
    CHECK_EQ(rd16(ks + 4), 2u);
    CHECK_EQ(call_shim(keystroke, {0, 0, ks}), XI_ERROR_EMPTY);

    CHECK(imports_serves_module("xinput9_1_0.dll"));
    // GetKeystroke is 1_3/1_4 only; 9_1_0 never registers it.
    CHECK_EQ(tramp("xinput9_1_0.dll", "XInputGetKeystroke"), 0u);
}

int main() {
    setvbuf(stdout, nullptr, _IONBF, 0);
    mem_init();
    imports_init();
    dx_register_shims();
    g_stack_top = STACK_TOP - 0x1000;
    g_scratch = heap_alloc(0x4000, true, 16);
    if (!g_scratch) {
        fprintf(stderr, "cannot allocate scratch\n");
        return 1;
    }

    struct {
        const char *name;
        void (*fn)();
    } tests[] = {
        {"joystick SDK layouts", test_joy_sdk_layouts},
        {"joystick pure helpers", test_joy_pure_helpers},
        {"joystick enumeration", test_joy_enum_devices},
        {"joystick through DirectInput 8", test_joy_dinput8},
        {"joystick state", test_joy_device_state},
        {"joystick objects", test_joy_objects},
        {"joystick buffered data", test_joy_device_data},
        {"joystick custom format", test_joy_custom_format},
        {"force feedback", test_force_feedback},
        {"xinput", test_xinput},
    };
    for (const auto &t : tests) {
        int before = g_failures;
        t.fn();
        printf("%s %s\n", g_failures == before ? "ok  " : "FAIL", t.name);
    }
    if (g_failures) {
        fprintf(stderr, "pad_tests: %d failure(s)\n", g_failures);
        return 1;
    }
    printf("pad_tests: all passed\n");
    return 0;
}
