// dinput_joystick.cpp - the virtual pad as a DirectInput joystick device.
//
// The pad (host_pad_state, host_pad_next_event) reaches the guest as one
// gamepad with six absolute axes, one POV hat and 13 buttons, laid out as
// DIJOYSTATE. Which pad axis feeds which DIJOYSTATE axis, and which pad
// button is rgbButtons[i], comes from the game's [controls.native] order
// (host_pad_native_axes / host_pad_native_buttons), read on every use so a
// changed setting applies without recreating the device.
//
// Buffered data is not diffed here: the host keeps an edge queue with
// sequence numbers, and each device remembers the newest edge it has
// delivered (ComObj::last_sequence). A gap between that and the oldest edge
// the host still holds means edges were lost, which is DirectInput's
// DI_BUFFEROVERFLOW.
#include "dinput_joystick.h"
#include "dx.h"

#include "../runtime/memory.h"
#include "../runtime/win32.h"

#include <cmath>
#include <cstring>
#include <string>

#define GUID_BYTES(a, b, c, d0, d1, d2, d3, d4, d5, d6, d7)                                        \
    {(uint8_t)((a) & 0xff),                                                                        \
     (uint8_t)(((a) >> 8) & 0xff),                                                                 \
     (uint8_t)(((a) >> 16) & 0xff),                                                                \
     (uint8_t)(((a) >> 24) & 0xff),                                                                \
     (uint8_t)((b) & 0xff),                                                                        \
     (uint8_t)(((b) >> 8) & 0xff),                                                                 \
     (uint8_t)((c) & 0xff),                                                                        \
     (uint8_t)(((c) >> 8) & 0xff),                                                                 \
     d0,                                                                                           \
     d1,                                                                                           \
     d2,                                                                                           \
     d3,                                                                                           \
     d4,                                                                                           \
     d5,                                                                                           \
     d6,                                                                                           \
     d7}

const uint8_t GUID_Joystick_[16] =
    GUID_BYTES(0x6F1D2B70, 0xD5A0, 0x11CF, 0xBF, 0xC7, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00);
// The kit's own instance and product: "RCMP" "AD", so they cannot collide
// with a real device's PIDVID product GUID.
const uint8_t GUID_RecompPadInstance_[16] =
    GUID_BYTES(0x52434D50, 0x4144, 0x11F0, 0x80, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01);
const uint8_t GUID_RecompPadProduct_[16] =
    GUID_BYTES(0x52434D50, 0x4144, 0x11F0, 0x80, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x02);

void di_put_wide(uint32_t at, const char *name, uint32_t units) {
    uint32_t i = 0;
    for (; name[i] && i + 1 < units; ++i)
        wr16(at + 2 * i, (uint8_t)name[i]);
    wr16(at + 2 * i, 0);
}

namespace {

// Shown by games that name the device, for example on a controls screen.
const char *const kPadName = "Controller";

// The object GUIDs of dinput.h: GUID_XAxis and friends share everything but
// their first dword.
JoyObject make_object(uint32_t data1, uint32_t ofs, uint32_t type, const char *name) {
    JoyObject o = {
        GUID_BYTES(data1, 0xC9F3, 0x11CF, 0xBF, 0xC7, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00), ofs,
        type, name};
    return o;
}

// Index layout of joy_objects(): axes 0..5, the POV, then the buttons.
const uint32_t kObjButton0 = JOY_AXES + JOY_POVS;

// HID usages the object records carry (Generic Desktop page, and the Button
// page numbered from 1).
const uint16_t kAxisUsage[JOY_AXES] = {0x30, 0x31, 0x32, 0x33, 0x34, 0x35};
const uint16_t kHatUsage = 0x39;
const uint16_t kPageGenericDesktop = 1, kPageButton = 9, kUsageGamepad = 5;

// PadButton bit names, in bit order (controls::PadBit).
const char *const kButtonNames[] = {"cross", "circle", "square", "triangle", "l1",    "r1", "l2",
                                    "r2",    "l3",     "r3",     "select",   "start", "ps"};
// DIJOYSTATE axis slots by name, in slot order.
const char *const kSlotNames[JOY_AXES] = {"x", "y", "z", "rx", "ry", "rz"};

// The configured orders, parsed from the host's comma-separated lists.
struct NativeOrder {
    int axis_slot[JOY_AXES];     // pad axis (lx ly rx ry l2 r2) -> DIJOYSTATE slot, or -1
    int button_bit[JOY_BUTTONS]; // DirectInput button -> pad bit, or -1
};

std::vector<std::string> split_list(const char *s) {
    std::vector<std::string> out;
    std::string cur;
    for (; s && *s; ++s) {
        if (*s == ',') {
            out.push_back(cur);
            cur.clear();
        } else if (*s != ' ') {
            cur += *s;
        }
    }
    if (!cur.empty() || !out.empty())
        out.push_back(cur);
    return out;
}

NativeOrder native_order() {
    NativeOrder o;
    for (int &v : o.axis_slot)
        v = -1;
    for (int &v : o.button_bit)
        v = -1;
    std::vector<std::string> axes = split_list(host_pad_native_axes());
    for (size_t i = 0; i < axes.size() && i < JOY_AXES; ++i)
        for (int s = 0; s < JOY_AXES; ++s)
            if (axes[i] == kSlotNames[s])
                o.axis_slot[i] = s;
    std::vector<std::string> buttons = split_list(host_pad_native_buttons());
    for (size_t i = 0; i < buttons.size() && i < JOY_BUTTONS; ++i)
        for (int b = 0; b < (int)std::size(kButtonNames); ++b)
            if (buttons[i] == kButtonNames[b])
                o.button_bit[i] = b;
    return o;
}

// A pad axis position t in -1..1 to a value in `r`, the way DirectInput
// applies DIPROP_DEADZONE and DIPROP_SATURATION: both are fractions of the
// distance from centre in 0..10000; inside the dead zone reads centre, past
// the saturation reads the end of the range, and between them scales
// linearly. The centre of an even-sized range rounds up (-32768..32767 is 0,
// 0..1000 is 500).
int32_t scale_axis(double t, const JoyAxisRange &r) {
    if (t > 1)
        t = 1;
    if (t < -1)
        t = -1;
    double dz = r.deadzone > 10000 ? 10000 : r.deadzone;
    double sat = r.saturation > 10000 ? 10000 : r.saturation;
    if (sat < dz)
        sat = dz;
    double mag = std::fabs(t) * 10000.0;
    double f;
    if (mag <= dz)
        f = 0;
    else if (mag >= sat)
        f = 1;
    else
        f = (mag - dz) / (sat - dz);
    int64_t lo = r.min, hi = r.max;
    int64_t centre = lo + (hi - lo + 1) / 2;
    int64_t v = t >= 0 ? centre + std::llround(f * double(hi - centre))
                       : centre - std::llround(f * double(centre - lo));
    return (int32_t)v;
}

// Pad axes 0..3 are sticks (-32767..32767); 4 and 5 are triggers, which
// span the whole range from released to fully down.
double stick_position(int16_t v) {
    return v / 32767.0;
}
double trigger_position(double fraction) {
    return fraction * 2.0 - 1.0;
}

// rgdwPOV: hundredths of a degree clockwise from north, or -1 when centred.
// Opposite directions cancel.
uint32_t pov_value(uint32_t hat) {
    int x = ((hat & 2) ? 1 : 0) - ((hat & 8) ? 1 : 0);
    int y = ((hat & 4) ? 1 : 0) - ((hat & 1) ? 1 : 0);
    static const uint32_t table[3][3] = {
        // x = -1, 0, +1
        {31500, 0, 4500},           // y = -1 (up)
        {27000, 0xFFFFFFFFu, 9000}, // y = 0
        {22500, 18000, 13500},      // y = +1 (down)
    };
    return table[y + 1][x + 1];
}

// A full DIJOYSTATE2 in host memory; the first 80 bytes are DIJOYSTATE.
void fill_state(uint8_t *buf, const HostPadState &s, const JoyAxisRange ranges[6]) {
    memset(buf, 0, JOY_DIJOYSTATE2_SIZE);
    NativeOrder order = native_order();
    auto put32 = [&](uint32_t ofs, uint32_t v) { memcpy(buf + ofs, &v, 4); };
    double pos[JOY_AXES] = {stick_position(s.lx),           stick_position(s.ly),
                            stick_position(s.rx),           stick_position(s.ry),
                            trigger_position(s.l2 / 255.0), trigger_position(s.r2 / 255.0)};
    bool fed[JOY_AXES] = {false};
    for (int a = 0; a < JOY_AXES; ++a) {
        int slot = order.axis_slot[a];
        if (slot < 0)
            continue;
        put32((uint32_t)slot * 4, (uint32_t)scale_axis(pos[a], ranges[slot]));
        fed[slot] = true;
    }
    // An axis nothing feeds rests at its centre.
    for (int slot = 0; slot < JOY_AXES; ++slot)
        if (!fed[slot])
            put32((uint32_t)slot * 4, (uint32_t)scale_axis(0, ranges[slot]));
    put32(JOY_OFF_POV, pov_value(s.hat));
    // The POVs this pad does not have read centred, as DirectInput reports them.
    for (uint32_t i = 1; i < JOY_POV_COUNT; ++i)
        put32(JOY_OFF_POV + 4 * i, 0xFFFFFFFFu);
    for (int b = 0; b < JOY_BUTTONS; ++b) {
        int bit = order.button_bit[b];
        if (bit >= 0 && (s.buttons & (1u << bit)))
            buf[JOY_OFF_BUTTONS + b] = 0x80;
    }
}

uint32_t object_width(const JoyObject &o) {
    return (o.type & DIDFT_BUTTON) ? 1u : 4u;
}

// The offset the guest's data format gives object `index`, or ~0 when a
// custom format leaves it out.
uint32_t guest_ofs(const ComObj *d, uint32_t index) {
    if (d->joy_format.empty())
        return joy_objects()[index].ofs;
    for (const JoyFormatSlot &s : d->joy_format)
        if (s.object == index)
            return s.ofs;
    return 0xFFFFFFFFu;
}

// The object at a guest data-format offset, or -1.
int object_at_ofs(const ComObj *d, uint32_t ofs) {
    const std::vector<JoyObject> &objs = joy_objects();
    if (d->joy_format.empty()) {
        for (size_t i = 0; i < objs.size(); ++i)
            if (objs[i].ofs == ofs)
                return (int)i;
        return -1;
    }
    for (const JoyFormatSlot &s : d->joy_format)
        if (s.ofs == ofs)
            return (int)s.object;
    return -1;
}

// The object a DIPH_BYID id names: same instance, overlapping type bits.
int object_by_id(uint32_t id) {
    const std::vector<JoyObject> &objs = joy_objects();
    for (size_t i = 0; i < objs.size(); ++i)
        if (didft_instance(objs[i].type) == didft_instance(id) &&
            (objs[i].type & id & DIDFT_TYPEMASK))
            return (int)i;
    return -1;
}

int find_object(const ComObj *d, uint32_t obj, uint32_t how) {
    if (how == DIPH_BYOFFSET)
        return object_at_ofs(d, obj);
    if (how == DIPH_BYID)
        return object_by_id(obj);
    return -1;
}

// One DIDEVICEOBJECTINSTANCE{A,W} of `size` bytes for object `index`.
void write_object_instance(uint32_t at, uint32_t size, bool wide, const ComObj *d, uint32_t index) {
    const JoyObject &o = joy_objects()[index];
    gm_zero(at, size);
    wr32(at, size);
    memcpy(gm_ptr(at + DIDOI_OFF_guidType), o.guid, 16);
    uint32_t ofs = guest_ofs(d, index);
    wr32(at + DIDOI_OFF_dwOfs, ofs == 0xFFFFFFFFu ? o.ofs : ofs);
    wr32(at + DIDOI_OFF_dwType, o.type);
    uint32_t tail = DIDOI_OFF_tszName + DIDOI_NAME_CHARS * (wide ? 2u : 1u);
    if (wide)
        di_put_wide(at + DIDOI_OFF_tszName, o.name, DIDOI_NAME_CHARS);
    else
        gm_put_str(at + DIDOI_OFF_tszName, o.name, DIDOI_NAME_CHARS);
    if (size < tail + DIDOI_TAIL_OFF_wUsage + 2)
        return;
    uint16_t page = kPageGenericDesktop, usage = kHatUsage;
    if (index < JOY_AXES)
        usage = kAxisUsage[index];
    else if (index >= kObjButton0) {
        page = kPageButton;
        usage = (uint16_t)(index - kObjButton0 + 1);
    }
    wr16(at + tail + DIDOI_TAIL_OFF_wUsagePage, page);
    wr16(at + tail + DIDOI_TAIL_OFF_wUsage, usage);
}

// Resolves one DIOBJECTDATAFORMAT against the objects not yet used: the
// type bits must overlap, the GUID (when given) must match, and a specific
// instance must match. Returns the object index or -1.
int match_format_object(uint32_t guid_addr, uint32_t type, const std::vector<bool> &used) {
    const std::vector<JoyObject> &objs = joy_objects();
    uint32_t bits = type & DIDFT_TYPEMASK;
    uint32_t inst = didft_instance(type);
    bool any = (type & DIDFT_ANYINSTANCE) == DIDFT_ANYINSTANCE;
    const uint8_t *guid = nullptr;
    if (guid_addr) {
        if (!gm_valid(guid_addr, 16))
            return -1;
        guid = gm_ptr(guid_addr);
    }
    for (size_t i = 0; i < objs.size(); ++i) {
        if (used[i])
            continue;
        if (bits && !(objs[i].type & bits))
            continue;
        if (guid && memcmp(guid, objs[i].guid, 16) != 0)
            continue;
        if (!any && didft_instance(objs[i].type) != inst)
            continue;
        return (int)i;
    }
    return -1;
}

} // namespace

// ===========================================================================
// Pure helpers
// ===========================================================================
bool joy_served() {
    return host_pad_mode() == 2 && (host_pad_native_apis() & 1) != 0;
}

bool joy_guid(const uint8_t *guid16) {
    return guid16 && (memcmp(guid16, GUID_Joystick_, 16) == 0 ||
                      memcmp(guid16, GUID_RecompPadInstance_, 16) == 0);
}

// DirectInput 8 enumerates by class (DI8DEVCLASS_ALL, _GAMECTRL) or by an
// exact DI8DEVTYPE; older versions by DIDEVTYPE, where 0 is every device.
// Both branches are live: dx/dinput.cpp enumerates the pad for the version 8
// interface as well as the older ones, and the version decides which.
bool joy_enum_matches(uint32_t devtype_filter, uint32_t di_version) {
    uint32_t t = devtype_filter & 0xFFu;
    if (di_version >= DIRECTINPUT_VERSION_8)
        return t == DI8DEVCLASS_ALL || t == DI8DEVCLASS_GAMECTRL || t == DI8DEVTYPE_GAMEPAD;
    return t == 0 || t == DIDEVTYPE_JOYSTICK;
}

uint32_t joy_devtype(uint32_t di_version) {
    if (di_version >= DIRECTINPUT_VERSION_8)
        return DI8DEVTYPE_GAMEPAD | (DI8DEVTYPEGAMEPAD_STANDARD << 8);
    return DIDEVTYPE_JOYSTICK | (DIDEVTYPEJOYSTICK_GAMEPAD << 8);
}

const std::vector<JoyObject> &joy_objects() {
    static const std::vector<JoyObject> objs = [] {
        std::vector<JoyObject> v;
        // GUID_XAxis E0, YAxis E1, ZAxis E2, RxAxis F4, RyAxis F5, RzAxis E3.
        static const uint32_t axis_guid[JOY_AXES] = {0xA36D02E0, 0xA36D02E1, 0xA36D02E2,
                                                     0xA36D02F4, 0xA36D02F5, 0xA36D02E3};
        static const char *const axis_name[JOY_AXES] = {"X Axis",     "Y Axis",     "Z Axis",
                                                        "X Rotation", "Y Rotation", "Z Rotation"};
        for (uint32_t i = 0; i < JOY_AXES; ++i)
            v.push_back(make_object(axis_guid[i], i * 4, DIDFT_ABSAXIS | (i << 8), axis_name[i]));
        v.push_back(make_object(0xA36D02F2 /* GUID_POV */, JOY_OFF_POV, DIDFT_POV, "Hat Switch"));
        static const char *const button_name[JOY_BUTTONS] = {
            "Button 0", "Button 1", "Button 2", "Button 3",  "Button 4",  "Button 5", "Button 6",
            "Button 7", "Button 8", "Button 9", "Button 10", "Button 11", "Button 12"};
        for (uint32_t i = 0; i < JOY_BUTTONS; ++i)
            v.push_back(make_object(0xA36D02F0 /* GUID_Button */, JOY_OFF_BUTTONS + i,
                                    DIDFT_PSHBUTTON | (i << 8), button_name[i]));
        return v;
    }();
    return objs;
}

void joy_write_state(uint32_t at, uint32_t size, const HostPadState &s,
                     const JoyAxisRange ranges[6]) {
    uint8_t buf[JOY_DIJOYSTATE2_SIZE];
    fill_state(buf, s, ranges);
    if (size > JOY_DIJOYSTATE2_SIZE)
        size = JOY_DIJOYSTATE2_SIZE;
    memcpy(gm_ptr(at), buf, size);
}

bool joy_event(const HostPadEvent &e, const JoyAxisRange ranges[6], uint32_t *ofs, uint32_t *data) {
    NativeOrder order = native_order();
    switch (e.kind) {
    case 0: // button: index is the pad bit
        for (int b = 0; b < JOY_BUTTONS; ++b)
            if (order.button_bit[b] == (int)e.index) {
                *ofs = JOY_OFF_BUTTONS + (uint32_t)b;
                *data = e.value ? 0x80u : 0u;
                return true;
            }
        return false;
    case 1: // hat: value is the direction bits
        *ofs = JOY_OFF_POV;
        *data = pov_value((uint32_t)e.value);
        return true;
    case 2: { // axis: value is the position times 32767
        if (e.index >= JOY_AXES)
            return false;
        int slot = order.axis_slot[e.index];
        if (slot < 0)
            return false;
        double t = e.index < 4 ? e.value / 32767.0 : trigger_position(e.value / 32767.0);
        *ofs = (uint32_t)slot * 4;
        *data = (uint32_t)scale_axis(t, ranges[slot]);
        return true;
    }
    default:
        return false;
    }
}

// ===========================================================================
// Device methods
// ===========================================================================
void joy_write_device_instance(uint32_t at, uint32_t size, bool wide, uint32_t di_version) {
    if (size >= DIDI_OFF_guidInstance + 16)
        memcpy(gm_ptr(at + DIDI_OFF_guidInstance), GUID_RecompPadInstance_, 16);
    if (size >= DIDI_OFF_guidProduct + 16)
        memcpy(gm_ptr(at + DIDI_OFF_guidProduct), GUID_RecompPadProduct_, 16);
    if (size >= DIDI_OFF_dwDevType + 4)
        wr32(at + DIDI_OFF_dwDevType, joy_devtype(di_version));
    const uint32_t units = 260;
    const uint32_t name_bytes = wide ? 2 * units : units;
    const uint32_t product = DIDI_OFF_tszInstanceName + name_bytes;
    const uint32_t usage_page = product + name_bytes + 16; // past guidFFDriver
    for (uint32_t name_at : {(uint32_t)DIDI_OFF_tszInstanceName, product}) {
        if (size < name_at + name_bytes)
            continue;
        if (wide)
            di_put_wide(at + name_at, kPadName, units);
        else
            gm_put_str(at + name_at, kPadName, units);
    }
    if (size >= usage_page + 4) {
        wr16(at + usage_page, kPageGenericDesktop);
        wr16(at + usage_page + 2, kUsageGamepad);
    }
}

uint32_t joy_get_capabilities(ComObj *d, uint32_t out, uint32_t size) {
    wr32(out + DIDC_OFF_dwFlags, DIDC_ATTACHED);
    wr32(out + DIDC_OFF_dwDevType, joy_devtype(d->di_version));
    if (size >= DIDEVCAPS_DX3_SIZE) {
        wr32(out + DIDC_OFF_dwAxes, JOY_AXES);
        wr32(out + DIDC_OFF_dwButtons, JOY_BUTTONS);
        wr32(out + DIDC_OFF_dwPOVs, JOY_POVS);
    }
    return DI_OK;
}

// DIJOYSTATE and DIJOYSTATE2 are taken as themselves. Any other size is a
// custom format, accepted when each of its objects resolves to one of ours
// (or is DIDFT_OPTIONAL) and fits inside dwDataSize.
uint32_t joy_set_data_format(ComObj *d, uint32_t df) {
    if (d->acquired)
        return DIERR_ACQUIRED;
    uint32_t data_size = rd32(df + DIDF_OFF_dwDataSize);
    if (data_size == JOY_DIJOYSTATE_SIZE || data_size == JOY_DIJOYSTATE2_SIZE) {
        d->data_format_size = data_size;
        d->joy_format.clear();
        return DI_OK;
    }
    uint32_t obj_size = rd32(df + DIDF_OFF_dwObjSize);
    uint32_t count = rd32(df + DIDF_OFF_dwNumObjs);
    uint32_t rgodf = rd32(df + DIDF_OFF_rgodf);
    if (!data_size || obj_size != DIOBJECTDATAFORMAT_SIZE || count > 1024 ||
        (count && (!rgodf || !gm_fits_n(rgodf, count, DIOBJECTDATAFORMAT_SIZE)))) {
        log_once("dinput.joyformat", "dinput: joystick data format of %u bytes refused", data_size);
        return DIERR_INVALIDPARAM;
    }
    const std::vector<JoyObject> &objs = joy_objects();
    std::vector<bool> used(objs.size(), false);
    std::vector<JoyFormatSlot> slots;
    for (uint32_t i = 0; i < count; ++i) {
        uint32_t rec = rgodf + i * DIOBJECTDATAFORMAT_SIZE;
        uint32_t ofs = rd32(rec + DIODF_OFF_dwOfs);
        uint32_t type = rd32(rec + DIODF_OFF_dwType);
        int idx = match_format_object(rd32(rec + DIODF_OFF_pguid), type, used);
        if (idx < 0) {
            if (type & DIDFT_OPTIONAL)
                continue;
            log_once("dinput.joyformat.obj",
                     "dinput: joystick data format names an object (type %08x) the pad lacks",
                     type);
            return DIERR_INVALIDPARAM;
        }
        if ((uint64_t)ofs + object_width(objs[(size_t)idx]) > data_size)
            return DIERR_INVALIDPARAM;
        used[(size_t)idx] = true;
        JoyFormatSlot s;
        s.ofs = ofs;
        s.object = (uint32_t)idx;
        slots.push_back(s);
    }
    d->data_format_size = data_size;
    d->joy_format = slots;
    LOGV("dinput: joystick custom data format, %u bytes, %zu objects", data_size, slots.size());
    return DI_OK;
}

uint32_t joy_enum_objects(X86 *c, ComObj *d, uint32_t cb, uint32_t ref, uint32_t flags) {
    if (!cb)
        return DIERR_INVALIDPARAM;
    // The pad has no force-feedback, output, alias or vendor objects, so a
    // filter asking for any of those attributes matches nothing.
    if (flags & 0x1F000000u)
        return DI_OK;
    uint32_t size = d->di_wide ? DIDEVICEOBJECTINSTANCEW_SIZE : DIDEVICEOBJECTINSTANCEA_SIZE;
    uint32_t bits = flags & DIDFT_TYPEMASK;
    const std::vector<JoyObject> &objs = joy_objects();
    for (uint32_t i = 0; i < objs.size(); ++i) {
        if (bits && !(objs[i].type & bits))
            continue;
        uint32_t a = di_scratch(size);
        if (!a)
            return E_OUTOFMEMORY;
        write_object_instance(a, size, d->di_wide, d, i);
        if (guest_call(c, cb, a, ref) != 1 /* DIENUM_CONTINUE */)
            break;
    }
    return DI_OK;
}

uint32_t joy_get_object_info(ComObj *d, uint32_t out, uint32_t obj, uint32_t how) {
    uint32_t size = rd32(out);
    bool ok =
        d->di_wide
            ? (size == DIDEVICEOBJECTINSTANCEW_SIZE || size == DIDEVICEOBJECTINSTANCE_DX3W_SIZE)
            : (size == DIDEVICEOBJECTINSTANCEA_SIZE || size == DIDEVICEOBJECTINSTANCE_DX3A_SIZE);
    if (!ok || !gm_valid(out, size) || (how != DIPH_BYOFFSET && how != DIPH_BYID))
        return DIERR_INVALIDPARAM;
    int idx = find_object(d, obj, how);
    if (idx < 0)
        return DIERR_OBJECTNOTFOUND;
    write_object_instance(out, size, d->di_wide, d, (uint32_t)idx);
    return DI_OK;
}

namespace {
// The axis slots a property header addresses: all six for DIPH_DEVICE, or
// the one axis it names. Returns an HRESULT; `first`/`last` bound the slots.
uint32_t property_slots(ComObj *d, uint32_t ph, uint32_t *first, uint32_t *last) {
    uint32_t obj = rd32(ph + DIPH_OFF_dwObj), how = rd32(ph + DIPH_OFF_dwHow);
    if (how == DIPH_DEVICE) {
        if (obj)
            return DIERR_INVALIDPARAM;
        *first = 0;
        *last = JOY_AXES - 1;
        return DI_OK;
    }
    if (how != DIPH_BYOFFSET && how != DIPH_BYID)
        return DIERR_INVALIDPARAM;
    int idx = find_object(d, obj, how);
    if (idx < 0)
        return DIERR_OBJECTNOTFOUND;
    if (idx >= JOY_AXES)
        return DIERR_UNSUPPORTED; // ranges and zones belong to axes
    *first = *last = (uint32_t)idx;
    return DI_OK;
}

bool is_axis_property(uint32_t prop) {
    return prop == JOY_PROP_RANGE || prop == JOY_PROP_DEADZONE || prop == JOY_PROP_SATURATION;
}

uint32_t property_size(uint32_t prop) {
    return prop == JOY_PROP_RANGE ? (uint32_t)DIPROPRANGE_SIZE : (uint32_t)DIPROPDWORD_SIZE;
}
} // namespace

bool joy_get_property(ComObj *d, uint32_t prop, uint32_t ph, uint32_t *hr) {
    if (!is_axis_property(prop))
        return false;
    uint32_t size = property_size(prop);
    if (!gm_valid(ph, size) || rd32(ph + DIPH_OFF_dwSize) != size) {
        *hr = DIERR_INVALIDPARAM;
        return true;
    }
    uint32_t first = 0, last = 0;
    *hr = property_slots(d, ph, &first, &last);
    if (*hr != DI_OK)
        return true;
    const JoyAxisRange &r = d->joy_ranges[first];
    if (prop == JOY_PROP_RANGE) {
        wr32(ph + DIPROPRANGE_OFF_lMin, (uint32_t)r.min);
        wr32(ph + DIPROPRANGE_OFF_lMax, (uint32_t)r.max);
    } else {
        wr32(ph + DIPROPDWORD_OFF_dwData, prop == JOY_PROP_DEADZONE ? r.deadzone : r.saturation);
    }
    return true;
}

bool joy_set_property(ComObj *d, uint32_t prop, uint32_t ph, uint32_t *hr) {
    if (!is_axis_property(prop))
        return false;
    uint32_t size = property_size(prop);
    if (!gm_valid(ph, size) || rd32(ph + DIPH_OFF_dwSize) != size) {
        *hr = DIERR_INVALIDPARAM;
        return true;
    }
    uint32_t first = 0, last = 0;
    *hr = property_slots(d, ph, &first, &last);
    if (*hr != DI_OK)
        return true;
    int32_t lo = 0, hi = 0;
    uint32_t v = 0;
    if (prop == JOY_PROP_RANGE) {
        lo = (int32_t)rd32(ph + DIPROPRANGE_OFF_lMin);
        hi = (int32_t)rd32(ph + DIPROPRANGE_OFF_lMax);
        if (lo > hi) {
            *hr = DIERR_INVALIDPARAM;
            return true;
        }
    } else {
        v = rd32(ph + DIPROPDWORD_OFF_dwData);
        if (v > 10000) {
            *hr = DIERR_INVALIDPARAM;
            return true;
        }
    }
    for (uint32_t s = first; s <= last; ++s) {
        JoyAxisRange &r = d->joy_ranges[s];
        if (prop == JOY_PROP_RANGE) {
            r.min = lo;
            r.max = hi;
        } else if (prop == JOY_PROP_DEADZONE) {
            r.deadzone = v;
        } else {
            r.saturation = v;
        }
    }
    *hr = DI_OK;
    return true;
}

// The caller has checked the device is acquired. The size must be the one
// SetDataFormat set; a custom format writes only its own fields.
uint32_t joy_get_device_state(ComObj *d, uint32_t size, uint32_t out) {
    if (size != d->data_format_size)
        return DIERR_INVALIDPARAM;
    HostPadState s;
    memset(&s, 0, sizeof s);
    host_pad_state(&s);
    if (d->joy_format.empty()) {
        joy_write_state(out, size, s, d->joy_ranges);
        return DI_OK;
    }
    uint8_t buf[JOY_DIJOYSTATE2_SIZE];
    fill_state(buf, s, d->joy_ranges);
    const std::vector<JoyObject> &objs = joy_objects();
    for (const JoyFormatSlot &slot : d->joy_format) {
        const JoyObject &o = objs[slot.object];
        memcpy(gm_ptr(out + slot.ofs), buf + o.ofs, object_width(o));
    }
    return DI_OK;
}

// Drains the host's edge queue after last_sequence, up to the caller's count,
// as DIDEVICEOBJECTDATA records at the caller's stride. Edges with no object
// in the data format are consumed without a record.
//
// The records are gathered first and the guest buffer is checked for exactly
// that many before anything is written, as the mouse path does, so a bad
// buffer fails whole instead of leaving partial records without a count.
uint32_t joy_get_device_data(ComObj *d, uint32_t objsize, uint32_t out, uint32_t inout,
                             uint32_t flags) {
    struct Record {
        uint32_t ofs, data, sequence;
    };
    uint32_t want = rd32(inout);
    bool peek = (flags & DIGDD_PEEK) != 0;
    uint32_t hr = DI_OK;
    uint32_t seq = d->last_sequence;
    std::vector<Record> records;
    HostPadEvent e;
    bool first = true;
    // A call with want == 0 never enters this loop, so it never reports the
    // overflow; the next call that reads does.
    while (records.size() < want && host_pad_next_event(seq, &e)) {
        // The host no longer holds the edge right after the last one this
        // device delivered: some were lost.
        if (first && e.sequence > d->last_sequence + 1)
            hr = DI_BUFFEROVERFLOW;
        first = false;
        seq = e.sequence;
        uint32_t ofs = 0, data = 0;
        if (!joy_event(e, d->joy_ranges, &ofs, &data))
            continue;
        // joy_event speaks DIJOYSTATE offsets; the guest's format may move them.
        uint32_t gofs = 0xFFFFFFFFu;
        const std::vector<JoyObject> &objs = joy_objects();
        for (uint32_t i = 0; i < objs.size(); ++i)
            if (objs[i].ofs == ofs)
                gofs = guest_ofs(d, i);
        if (gofs != 0xFFFFFFFFu)
            records.push_back(Record{gofs, data, e.sequence});
    }
    uint32_t n = (uint32_t)records.size();
    if (out) {
        if (!gm_fits_n(out, n, objsize))
            return DIERR_INVALIDPARAM;
        uint32_t now = host_millis();
        for (uint32_t i = 0; i < n; ++i) {
            uint32_t a = out + i * objsize;
            wr32(a + DIDOD_OFF_dwOfs, records[i].ofs);
            wr32(a + DIDOD_OFF_dwData, records[i].data);
            wr32(a + DIDOD_OFF_dwTimeStamp, now);
            wr32(a + DIDOD_OFF_dwSequence, records[i].sequence);
            if (objsize >= DIDEVICEOBJECTDATA_DX8_SIZE)
                wr32(a + DIDOD_OFF_uAppData, 0);
        }
    }
    wr32(inout, n);
    if (!peek)
        d->last_sequence = seq;
    return hr;
}

void joy_acquired(ComObj *d) {
    HostPadEvent e;
    uint32_t seq = d->last_sequence;
    // The host's ring is small; the bound only guards a misbehaving host.
    for (int i = 0; i < 65536 && host_pad_next_event(seq, &e); ++i)
        seq = e.sequence;
    d->last_sequence = seq;
}
