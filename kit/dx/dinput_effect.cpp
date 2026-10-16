// dinput_effect.cpp - DirectInput force feedback on the virtual pad, played on its two rumble motors.
//
// NFS Underground (run 8, review/run08/ffb_research.md in that port) drives a wheel-style force-feedback library: it
// counts the axes EnumObjects marks DIDOI_FFACTUATOR, switches the actuators on with SendForceFeedbackCommand (a
// failure there skips its GetDeviceState, so the two land together), and plays ConstantForce (steering load),
// Square / Sine / Triangle (collision jolts: 150 ms with a fade; road surfaces: infinite, 20-100 ms periods),
// Spring and Damper (centering). A pad has no actuated axis, so only the periodic effects reach the motors:
//   - a finite periodic effect (a jolt) drives both motors, the low one heavier;
//   - an infinite periodic effect (a surface) drives mostly the high motor, Square also a little of the low one;
//   - constant, ramp, condition and custom effects are kept, start, stop and report their status, and drive nothing
//     (a continuous steering torque would drone through the whole race).
// Levels follow each effect's gain, envelope and duration, and the device's DIPROP_FFGAIN. The motors are sent only on
// change, through host_pad_rumble (the same path as XInput, so focus loss and the controls editor still stop them).
// GetEffectStatus is exact (1 while playing, 0 once a finite effect ran out or was stopped): UG1 fires its next jolt
// only after the last one reads 0. A game that stops calling its effects for 200 ms while still reading the pad (a
// pause menu over an infinite surface effect) is muted until it calls again.
#include "dinput_effect.h"

#include "dinput_joystick.h"
#include "dx.h"
#include "host_api.h"
#include "../platform/os.h"
#include "../runtime/memory.h"
#include "../runtime/win32.h"

#include <algorithm>
#include <cstring>
#include <iterator>
#include <stdlib.h>
#include <unordered_map>

namespace {

bool is_joystick(const ComObj *d) {
    return d && d->dev_type == DIDEVTYPE_JOYSTICK;
}

enum EffectKind { FX_CONSTANT, FX_RAMP, FX_PERIODIC, FX_CONDITION, FX_CUSTOM };

// GUID_ConstantForce .. GUID_CustomForce: {13541C20..2B-8E33-11D0-9AD0-00A0C9A06E35}; only Data1 differs.
const uint8_t kEffectGuidTail[12] = {0x33, 0x8E, 0xD0, 0x11, 0x9A, 0xD0, 0x00, 0xA0, 0xC9, 0xA0, 0x6E, 0x35};
const uint32_t kEffectGuidFirst = 0x13541C20u, kEffectGuidLast = 0x13541C2Bu;
const uint32_t kGuidSquare = 0x13541C22u;
// IID_IDirectInputEffect {E7E1F7C0-88D2-11D0-9AD0-00A0C9A06E35}
const uint8_t kIidEffect[16] = {0xC0, 0xF7, 0xE1, 0xE7, 0xD2, 0x88, 0xD0, 0x11,
                                0x9A, 0xD0, 0x00, 0xA0, 0xC9, 0xA0, 0x6E, 0x35};

const uint32_t INFINITE_ = 0xFFFFFFFFu;
const uint32_t DIEFFECT_SIZE = 0x38, DIEFFECT_DX5_SIZE = 0x34, DIENVELOPE_SIZE = 0x14;
const uint32_t DIEP_DURATION = 0x1, DIEP_GAIN = 0x4, DIEP_ENVELOPE = 0x80, DIEP_TYPESPECIFICPARAMS = 0x100,
               DIEP_STARTDELAY = 0x200, DIEP_ALLPARAMS_DX5 = 0x1FF, DIEP_ALLPARAMS = 0x3FF, DIEP_START = 0x20000000;
const uint32_t DIES_SOLO = 0x1;
const uint32_t DISFFC_RESET = 0x1, DISFFC_STOPALL = 0x2, DISFFC_PAUSE = 0x4, DISFFC_CONTINUE = 0x8,
               DISFFC_SETACTUATORSON = 0x10, DISFFC_SETACTUATORSOFF = 0x20;
const uint32_t DIGFFS_EMPTY = 0x1, DIGFFS_STOPPED = 0x2, DIGFFS_PAUSED = 0x4, DIGFFS_ACTUATORSON = 0x10,
               DIGFFS_ACTUATORSOFF = 0x20, DIGFFS_POWERON = 0x40, DIGFFS_SAFETYSWITCHON = 0x100,
               DIGFFS_USERFFSWITCHON = 0x400;
const uint32_t DIPROP_FFGAIN_ = 7, DIPROP_AUTOCENTER_ = 9;
const uint32_t CLASS_E_NOAGGREGATION_ = 0x80040110u;
const uint64_t kQuietNs = 200ull * 1000 * 1000; // no effect call for this long: the motors rest

struct Effect {
    uint32_t device = 0; // the K_DIDEVICE object id
    uint32_t guid = 0;   // Data1 of its GUID
    EffectKind kind = FX_CONSTANT;
    uint32_t duration = INFINITE_, gain = 10000, start_delay = 0; // microseconds, 0..10000, microseconds
    bool envelope = false;
    uint32_t attack_level = 0, attack_time = 0, fade_level = 0, fade_time = 0;
    uint32_t magnitude = 0; // 0..10000, the strength before the envelope
    bool playing = false;
    uint64_t start_ns = 0;
    uint32_t iterations = 1;
};
struct DeviceFF {
    uint32_t gain = 10000;
    bool actuators = true, paused = false;
};

std::unordered_map<uint32_t, Effect> g_effects; // by effect object id
std::unordered_map<uint32_t, DeviceFF> g_devices; // by device object id
uint64_t g_last_call_ns = 0;
uint16_t g_low = 0, g_high = 0;

uint64_t now_ns() {
    return os_monotonic_ns();
}

uint32_t clamp10k(int64_t v) {
    return (uint32_t)std::clamp<int64_t>(v, 0, 10000);
}

// The effect's strength now (0..10000); a finite effect that ran out stops here.
uint32_t level(Effect &e, uint64_t now) {
    if (!e.playing)
        return 0;
    uint64_t us = (now - e.start_ns) / 1000;
    if (us < e.start_delay)
        return 0;
    uint64_t t = us - e.start_delay;
    if (e.duration != INFINITE_) {
        if (e.duration == 0 || (e.iterations != INFINITE_ && t >= (uint64_t)e.duration * e.iterations)) {
            e.playing = false;
            return 0;
        }
        t %= e.duration;
    }
    int64_t mag = e.magnitude;
    if (e.envelope) {
        if (e.attack_time && t < e.attack_time)
            mag = e.attack_level + (mag - (int64_t)e.attack_level) * (int64_t)t / e.attack_time;
        if (e.duration != INFINITE_ && e.fade_time && e.duration - t < e.fade_time)
            mag = e.fade_level + (mag - (int64_t)e.fade_level) * (int64_t)(e.duration - t) / e.fade_time;
    }
    return clamp10k(mag * e.gain / 10000);
}

void send(uint16_t low, uint16_t high) {
    if (low == g_low && high == g_high)
        return;
    g_low = low;
    g_high = high;
    host_pad_rumble(low, high);
}

// Sums every playing effect into the two motors and sends them when they changed.
void update(uint64_t now) {
    if (g_effects.empty() && !g_low && !g_high)
        return;
    double low = 0, high = 0;
    for (auto &[id, e] : g_effects) {
        uint32_t l = level(e, now);
        if (!l)
            continue;
        const DeviceFF &dev = g_devices[e.device];
        if (!dev.actuators || dev.paused || !com_get(e.device))
            continue;
        double v = double(l) * dev.gain / 10000.0;
        if (e.kind != FX_PERIODIC)
            continue; // steering torque and centering: nothing a rumble motor can say
        if (e.duration != INFINITE_) { // a jolt
            low += v;
            high += 0.6 * v;
        } else { // a surface
            high += 0.8 * v;
            if (e.guid == kGuidSquare)
                low += 0.3 * v;
        }
    }
    if (now - g_last_call_ns > kQuietNs)
        low = high = 0;
    auto motor = [](double v) { return (uint16_t)std::lround(std::min(v, 10000.0) * 65535.0 / 10000.0); };
    send(motor(low), motor(high));
}

void touched() {
    g_last_call_ns = now_ns();
}

Effect *this_effect(X86 *c, ComObj **obj = nullptr) {
    ComObj *o = com_this_arg(c);
    if (!o || o->kind != K_DIEFFECT)
        return nullptr;
    auto it = g_effects.find(o->id);
    if (it == g_effects.end())
        return nullptr;
    if (obj)
        *obj = o;
    return &it->second;
}

void start(Effect &e, uint32_t iterations, bool solo) {
    if (solo)
        for (auto &[id, other] : g_effects)
            if (other.device == e.device)
                other.playing = false;
    e.playing = true;
    e.start_ns = now_ns();
    e.iterations = iterations ? iterations : 1;
}

// DIEFFECT at `p` with the DIEP_* `flags`: the fields this player uses, each read only when its flag is set.
uint32_t set_parameters(Effect &e, uint32_t p, uint32_t flags) {
    if (!p || !gm_valid(p, 4))
        return DIERR_INVALIDPARAM;
    uint32_t size = rd32(p);
    if ((size != DIEFFECT_SIZE && size != DIEFFECT_DX5_SIZE) || !gm_valid(p, size))
        return DIERR_INVALIDPARAM;
    if (size == DIEFFECT_DX5_SIZE && (flags & DIEP_STARTDELAY))
        return DIERR_INVALIDPARAM;
    Effect next = e;
    if (flags & DIEP_DURATION)
        next.duration = rd32(p + 8);
    if (flags & DIEP_GAIN)
        next.gain = std::min(rd32(p + 0x10), 10000u);
    if (flags & DIEP_ENVELOPE) {
        uint32_t env = rd32(p + 0x28);
        next.envelope = env != 0;
        if (env) {
            if (!gm_valid(env, DIENVELOPE_SIZE) || rd32(env) != DIENVELOPE_SIZE)
                return DIERR_INVALIDPARAM;
            next.attack_level = std::min(rd32(env + 4), 10000u);
            next.attack_time = rd32(env + 8);
            next.fade_level = std::min(rd32(env + 12), 10000u);
            next.fade_time = rd32(env + 16);
        }
    }
    if (flags & DIEP_TYPESPECIFICPARAMS) {
        uint32_t cb = rd32(p + 0x2c), ts = rd32(p + 0x30);
        if (cb && (!ts || !gm_valid(ts, cb)))
            return DIERR_INVALIDPARAM;
        switch (next.kind) {
        case FX_CONSTANT: // DICONSTANTFORCE { LONG lMagnitude; }
            if (cb != 4)
                return DIERR_INVALIDPARAM;
            next.magnitude = clamp10k(std::llabs((int32_t)rd32(ts)));
            break;
        case FX_RAMP: // DIRAMPFORCE { LONG lStart, lEnd; }
            if (cb != 8)
                return DIERR_INVALIDPARAM;
            next.magnitude = clamp10k(std::max(std::llabs((int32_t)rd32(ts)), std::llabs((int32_t)rd32(ts + 4))));
            break;
        case FX_PERIODIC: // DIPERIODIC { DWORD dwMagnitude; LONG lOffset; DWORD dwPhase, dwPeriod; }
            if (cb != 16)
                return DIERR_INVALIDPARAM;
            next.magnitude = clamp10k(rd32(ts));
            break;
        case FX_CONDITION: // DICONDITION[cAxes], 24 bytes each
            if (!cb || cb % 24)
                return DIERR_INVALIDPARAM;
            break;
        case FX_CUSTOM:
            break;
        }
    }
    if (flags & DIEP_STARTDELAY)
        next.start_delay = rd32(p + 0x34);
    // Axes, directions, the trigger and the sample period: accepted; a rumble motor has no use for them.
    const bool was_playing = e.playing;
    e = next;
    e.playing = was_playing;
    if (flags & DIEP_START)
        start(e, 1, false);
    return DI_OK;
}

// --- IDirectInputEffect --------------------------------------------------------------------------
void Eff_Initialize(X86 *c) {
    com_ret(c, this_effect(c) ? DI_OK : DIERR_INVALIDPARAM);
}
void Eff_GetEffectGuid(X86 *c) {
    Effect *e = this_effect(c);
    uint32_t out = arg(c, 1);
    if (!e || !out || !gm_valid(out, 16)) {
        com_ret(c, DIERR_INVALIDPARAM);
        return;
    }
    wr32(out, e->guid);
    memcpy(gm_ptr(out + 4), kEffectGuidTail, 12);
    com_ret(c, DI_OK);
}
void Eff_GetParameters(X86 *c) {
    Effect *e = this_effect(c);
    uint32_t p = arg(c, 1), flags = arg(c, 2);
    if (!e || !p || !gm_valid(p, 4) || (rd32(p) != DIEFFECT_SIZE && rd32(p) != DIEFFECT_DX5_SIZE) ||
        !gm_valid(p, rd32(p))) {
        com_ret(c, DIERR_INVALIDPARAM);
        return;
    }
    if (flags & DIEP_DURATION)
        wr32(p + 8, e->duration);
    if (flags & DIEP_GAIN)
        wr32(p + 0x10, e->gain);
    if ((flags & DIEP_STARTDELAY) && rd32(p) == DIEFFECT_SIZE)
        wr32(p + 0x34, e->start_delay);
    com_ret(c, DI_OK);
}
void Eff_SetParameters(X86 *c) {
    touched();
    Effect *e = this_effect(c);
    uint32_t hr = e ? set_parameters(*e, arg(c, 1), arg(c, 2)) : DIERR_INVALIDPARAM;
    update(now_ns());
    com_ret(c, hr);
}
void Eff_Start(X86 *c) {
    touched();
    Effect *e = this_effect(c);
    if (!e) {
        com_ret(c, DIERR_INVALIDPARAM);
        return;
    }
    start(*e, arg(c, 1), (arg(c, 2) & DIES_SOLO) != 0);
    log_once("dinput.ff.start", "dinput: force feedback effect %08x started (magnitude %u, duration %u us)", e->guid,
             e->magnitude, e->duration);
    update(now_ns());
    com_ret(c, DI_OK);
}
void Eff_Stop(X86 *c) {
    touched();
    Effect *e = this_effect(c);
    if (e)
        e->playing = false;
    update(now_ns());
    com_ret(c, e ? DI_OK : DIERR_INVALIDPARAM);
}
void Eff_GetEffectStatus(X86 *c) {
    touched();
    Effect *e = this_effect(c);
    uint32_t out = arg(c, 1);
    if (!e || !out || !gm_valid(out, 4)) {
        com_ret(c, DIERR_INVALIDPARAM);
        return;
    }
    uint64_t now = now_ns();
    level(*e, now); // a finite effect that ran out stops here
    wr32(out, e->playing ? 1u : 0u); // DIEGES_PLAYING
    update(now);
    com_ret(c, DI_OK);
}
void Eff_Download(X86 *c) {
    touched();
    com_ret(c, this_effect(c) ? DI_OK : DIERR_INVALIDPARAM);
}
void Eff_Unload(X86 *c) {
    touched();
    Effect *e = this_effect(c);
    if (e)
        e->playing = false;
    update(now_ns());
    com_ret(c, e ? DI_OK : DIERR_INVALIDPARAM);
}
DX_STUB(Eff_Escape, DIERR_UNSUPPORTED)

const ComMethod g_dieffect[] = {
    {"QueryInterface", 3, com_QueryInterface},
    {"AddRef", 1, com_AddRef},
    {"Release", 1, com_Release},
    {"Initialize", 4, Eff_Initialize},
    {"GetEffectGuid", 2, Eff_GetEffectGuid},
    {"GetParameters", 3, Eff_GetParameters},
    {"SetParameters", 3, Eff_SetParameters},
    {"Start", 3, Eff_Start},
    {"Stop", 1, Eff_Stop},
    {"GetEffectStatus", 2, Eff_GetEffectStatus},
    {"Download", 1, Eff_Download},
    {"Unload", 1, Eff_Unload},
    {"Escape", 2, Eff_Escape},
};

void effect_destroy(ComObj *o) {
    g_effects.erase(o->id);
    update(now_ns());
}

} // namespace

bool dinput_ff_served() {
    const char *v = recomp_env("DINPUT_FF");
    return v && *v == '1' && joy_served();
}

// CreateEffect(this, rguid, lpeff, ppdeff, punkOuter)
void dinput_ff_create_effect(X86 *c, ComObj *d) {
    uint32_t rguid = arg(c, 1), p = arg(c, 2), out = arg(c, 3), outer = arg(c, 4);
    if (!dinput_ff_served() || !is_joystick(d)) {
        log_once("dinput.ff.off", "dinput: CreateEffect: no force feedback served (RECOMP_DINPUT_FF)");
        com_ret(c, DIERR_UNSUPPORTED);
        return;
    }
    if (!rguid || !gm_valid(rguid, 16) || !out || !gm_valid(out, 4)) {
        com_ret(c, DIERR_INVALIDPARAM);
        return;
    }
    wr32(out, 0);
    if (outer) {
        com_ret(c, CLASS_E_NOAGGREGATION_);
        return;
    }
    uint32_t data1 = rd32(rguid);
    if (data1 < kEffectGuidFirst || data1 > kEffectGuidLast || memcmp(gm_ptr(rguid + 4), kEffectGuidTail, 12)) {
        log_once("dinput.ff.guid", "dinput: CreateEffect: effect %08x is not one of the standard effects", data1);
        com_ret(c, DIERR_DEVICENOTREG);
        return;
    }
    Effect e;
    e.device = d->id;
    e.guid = data1;
    e.kind = data1 == 0x13541C20u   ? FX_CONSTANT
             : data1 == 0x13541C21u ? FX_RAMP
             : data1 <= 0x13541C26u ? FX_PERIODIC
             : data1 <= 0x13541C2Au ? FX_CONDITION
                                    : FX_CUSTOM;
    if (p) {
        uint32_t size = gm_valid(p, 4) ? rd32(p) : 0;
        uint32_t hr = set_parameters(e, p, size == DIEFFECT_DX5_SIZE ? DIEP_ALLPARAMS_DX5 : DIEP_ALLPARAMS);
        if (hr != DI_OK) {
            com_ret(c, hr);
            return;
        }
    }
    touched();
    ComObj *o = com_new(K_DIEFFECT);
    g_effects[o->id] = e;
    g_devices.emplace(d->id, DeviceFF());
    wr32(out, com_view(o, IF_DIEFFECT));
    log_once("dinput.ff.create", "dinput: force feedback: the game made its first effect (%08x)", data1);
    com_ret(c, DI_OK);
}

uint32_t dinput_ff_command(ComObj *d, uint32_t flags) {
    if (!dinput_ff_served() || !is_joystick(d))
        return DIERR_UNSUPPORTED;
    touched();
    DeviceFF &dev = g_devices[d->id];
    if (flags & (DISFFC_RESET | DISFFC_STOPALL))
        for (auto &[id, e] : g_effects)
            if (e.device == d->id)
                e.playing = false;
    if (flags & DISFFC_PAUSE)
        dev.paused = true;
    if (flags & (DISFFC_CONTINUE | DISFFC_RESET))
        dev.paused = false;
    if (flags & DISFFC_SETACTUATORSON)
        dev.actuators = true;
    if (flags & DISFFC_SETACTUATORSOFF)
        dev.actuators = false;
    update(now_ns());
    return DI_OK;
}

uint32_t dinput_ff_state(ComObj *d, uint32_t out) {
    if (!dinput_ff_served() || !is_joystick(d))
        return DIERR_UNSUPPORTED;
    if (!out || !gm_valid(out, 4))
        return DIERR_INVALIDPARAM;
    const DeviceFF &dev = g_devices[d->id];
    bool any = false, playing = false;
    uint64_t now = now_ns();
    for (auto &[id, e] : g_effects)
        if (e.device == d->id) {
            any = true;
            level(e, now);
            playing |= e.playing;
        }
    wr32(out, (any ? 0u : DIGFFS_EMPTY) | (playing ? 0u : DIGFFS_STOPPED) | (dev.paused ? DIGFFS_PAUSED : 0u) |
                  (dev.actuators ? DIGFFS_ACTUATORSON : DIGFFS_ACTUATORSOFF) | DIGFFS_POWERON |
                  DIGFFS_SAFETYSWITCHON | DIGFFS_USERFFSWITCHON);
    return DI_OK;
}

// DIPROPDWORD for the whole device (DIPH_DEVICE): dwSize 20, dwHeaderSize 16, dwObj 0, dwHow 0, dwData.
bool dinput_ff_get_property(ComObj *d, uint32_t prop, uint32_t ph, uint32_t *hr) {
    if ((prop != DIPROP_FFGAIN_ && prop != DIPROP_AUTOCENTER_) || !dinput_ff_served() || !is_joystick(d))
        return false;
    *hr = DI_OK;
    wr32(ph + 16, prop == DIPROP_FFGAIN_ ? g_devices[d->id].gain : 0u); // DIPROPAUTOCENTER_OFF
    return true;
}
bool dinput_ff_set_property(ComObj *d, uint32_t prop, uint32_t ph, uint32_t *hr) {
    if ((prop != DIPROP_FFGAIN_ && prop != DIPROP_AUTOCENTER_) || !dinput_ff_served() || !is_joystick(d))
        return false;
    uint32_t v = rd32(ph + 16);
    *hr = DI_OK;
    if (prop == DIPROP_FFGAIN_) {
        if (v > 10000) {
            *hr = DIERR_INVALIDPARAM;
            return true;
        }
        g_devices[d->id].gain = v;
        update(now_ns());
    }
    return true; // DIPROP_AUTOCENTER: a pad has no spring to centre
}

void dinput_ff_device_unacquired(ComObj *d) {
    bool any = false;
    for (auto &[id, e] : g_effects)
        if (e.device == d->id && e.playing) {
            e.playing = false;
            any = true;
        }
    if (any)
        update(now_ns());
}

void dinput_ff_pump() {
    update(now_ns());
}

void dinput_ff_register() {
    static bool done = false;
    if (done)
        return;
    done = true;
    com_define(IF_DIEFFECT, "DINPUT8.dll", "IDirectInputEffect", g_dieffect, std::size(g_dieffect));
    com_bind(IF_DIEFFECT, K_DIEFFECT);
    com_register_iid(IF_DIEFFECT, kIidEffect);
    com_set_destructor(K_DIEFFECT, effect_destroy);
}

void dinput_ff_reset() {
    g_effects.clear();
    g_devices.clear();
    g_last_call_ns = 0;
    g_low = g_high = 0;
}
