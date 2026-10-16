// vpad.cpp - see vpad.h.
#include "vpad.h"
#include "../../runtime/mods_seam.h"

#include <algorithm>
#include <cmath>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

namespace controls {

namespace {
float axis_merge(float a, float b) {
    return std::fabs(a) >= std::fabs(b) ? a : b;
}

// The axis' wire value: * 32767, rounded, and clamped to int16 range.
int32_t scaled_axis(float v) {
    double s = std::lround(double(v) * 32767.0);
    return int32_t(std::clamp(s, -32768.0, 32767.0));
}
} // namespace

PadState merge(const PadState &a, const PadState &b) {
    PadState r;
    r.buttons = a.buttons | b.buttons;
    r.hat = a.hat | b.hat;
    r.lx = axis_merge(a.lx, b.lx);
    r.ly = axis_merge(a.ly, b.ly);
    r.rx = axis_merge(a.rx, b.rx);
    r.ry = axis_merge(a.ry, b.ry);
    r.l2 = axis_merge(a.l2, b.l2);
    r.r2 = axis_merge(a.r2, b.r2);
    return r;
}

void stick_output(double dx, double dy, double radius_px, double deadzone, float *x, float *y) {
    const double h = std::hypot(dx, dy);
    double m = radius_px > 0 ? h / radius_px : 0.0;
    if (m > 1.0)
        m = 1.0;
    if (m <= deadzone || h <= 0.0) {
        *x = 0;
        *y = 0;
        return;
    }
    const double k = (m - deadzone) / (1.0 - deadzone);
    *x = float(dx / h * k);
    *y = float(dy / h * k);
}

uint8_t dpad_hat(double dx, double dy, double half) {
    if (std::hypot(dx, dy) < 0.25 * half)
        return 0;
    // 8 sectors, 45 degrees each, centred on the axes and diagonals; sector
    // 0 is centred on +x (right) and sectors increase with the angle toward
    // +y (down, screen convention).
    const double angle_deg = std::atan2(dy, dx) * 180.0 / M_PI;
    long sector = std::lround(angle_deg / 45.0) % 8;
    if (sector < 0)
        sector += 8;
    static const uint8_t bits[8] = {
        kHatRight,            // 0
        kHatRight | kHatDown, // 1
        kHatDown,             // 2
        kHatDown | kHatLeft,  // 3
        kHatLeft,             // 4
        kHatLeft | kHatUp,    // 5
        kHatUp,               // 6
        kHatUp | kHatRight,   // 7
    };
    return bits[sector];
}

void Vpad::push_edge(uint8_t kind, uint8_t index, int32_t value) {
    const uint32_t seq = ++edge_count_;
    ring_[(seq - 1) % 256] = PadEdge{seq, kind, index, value};
}

void Vpad::set_source(int source, const PadState &s) {
    std::lock_guard lock(mutex_);
    if (source < 0 || source >= kPadSourceCount)
        return;
    sources_[source] = s;

    PadState next;
    for (int i = 0; i < kPadSourceCount; ++i)
        next = merge(next, sources_[i]);

    if (next == merged_)
        return;

    for (int bit = 0; bit < 13; ++bit) {
        const uint16_t mask = uint16_t(1u << bit);
        if ((next.buttons & mask) != (merged_.buttons & mask))
            push_edge(0, uint8_t(bit), (next.buttons & mask) ? 1 : 0);
    }
    if (next.hat != merged_.hat)
        push_edge(1, 0, next.hat);

    const float *old_axes[6] = {&merged_.lx, &merged_.ly, &merged_.rx,
                                &merged_.ry, &merged_.l2, &merged_.r2};
    const float *new_axes[6] = {&next.lx, &next.ly, &next.rx, &next.ry, &next.l2, &next.r2};
    for (int i = 0; i < 6; ++i) {
        const int32_t old_scaled = scaled_axis(*old_axes[i]);
        const int32_t new_scaled = scaled_axis(*new_axes[i]);
        if (old_scaled != new_scaled)
            push_edge(2, uint8_t(i), new_scaled);
    }

    merged_ = next;
    ++packet_;
}

PadState Vpad::state() const {
    std::lock_guard lock(mutex_);
    return merged_;
}

uint32_t Vpad::packet() const {
    std::lock_guard lock(mutex_);
    return packet_;
}

bool Vpad::next_edge(uint32_t after_sequence, PadEdge *out) const {
    std::lock_guard lock(mutex_);
    if (edge_count_ == 0)
        return false;
    const uint32_t oldest = edge_count_ > 256 ? edge_count_ - 256 + 1 : 1;
    uint32_t target = after_sequence + 1;
    if (target < oldest)
        target = oldest;
    if (target > edge_count_)
        return false;
    *out = ring_[(target - 1) % 256];
    return true;
}

void Vpad::request_rumble(uint16_t low, uint16_t high) {
    std::lock_guard lock(mutex_);
    rumble_low_ = low;
    rumble_high_ = high;
    ++rumble_serial_;
}

uint64_t Vpad::rumble_serial() const {
    std::lock_guard lock(mutex_);
    return rumble_serial_;
}

void Vpad::rumble(uint16_t *low, uint16_t *high) const {
    std::lock_guard lock(mutex_);
    uint16_t l = rumble_low_, h = rumble_high_;
    for (const auto &[owner, p] : pulses_) {
        l = std::max(l, p.low);
        h = std::max(h, p.high);
    }
    if (low)
        *low = l;
    if (high)
        *high = h;
}

bool Vpad::rumble_pulse(uint32_t owner, uint16_t low, uint16_t high, uint32_t ms, uint64_t now_ns) {
    std::lock_guard lock(mutex_);
    if (ms > 5000)
        return false;
    if (!ms || (!low && !high)) {
        if (pulses_.erase(owner))
            ++rumble_serial_;
        return true;
    }
    if (pulses_.size() >= 16 && !pulses_.count(owner))
        return false;
    pulses_[owner] = {low, high, now_ns + uint64_t(ms) * 1000000};
    ++rumble_serial_;
    return true;
}

void Vpad::expire_rumble_pulses(uint64_t now_ns) {
    std::lock_guard lock(mutex_);
    for (auto it = pulses_.begin(); it != pulses_.end();) {
        if (now_ns >= it->second.until_ns) {
            it = pulses_.erase(it);
            ++rumble_serial_;
        } else
            ++it;
    }
}

void Vpad::clear_rumble_pulses() {
    std::lock_guard lock(mutex_);
    if (!pulses_.empty()) {
        pulses_.clear();
        ++rumble_serial_;
    }
}

bool Vpad::controller_connected() const {
    std::lock_guard lock(mutex_);
    return controller_connected_;
}

void Vpad::set_controller_connected(bool on) {
    std::lock_guard lock(mutex_);
    controller_connected_ = on;
    if (!on && !pulses_.empty()) {
        pulses_.clear();
        ++rumble_serial_;
    }
}

void Vpad::reset() {
    std::lock_guard lock(mutex_);
    for (PadState &s : sources_)
        s = PadState();
    merged_ = PadState();
    packet_ = 0;
    for (PadEdge &e : ring_)
        e = PadEdge();
    edge_count_ = 0;
    rumble_low_ = rumble_high_ = 0;
    rumble_serial_ = 0;
    pulses_.clear();
    controller_connected_ = false;
}

Vpad &vpad() {
    static Vpad instance;
    return instance;
}

namespace {
int16_t scaled_stick(float v) {
    const double s = std::lround(double(v) * 32767.0);
    return int16_t(std::clamp(s, -32768.0, 32767.0));
}
uint8_t scaled_trigger(float v) {
    const double s = std::lround(double(v) * 255.0);
    return uint8_t(std::clamp(s, 0.0, 255.0));
}
} // namespace

HostPadState to_host(const PadState &s) {
    HostPadState h{};
    h.buttons = s.buttons;
    h.hat = s.hat;
    h.reserved = 0;
    h.lx = scaled_stick(s.lx);
    h.ly = scaled_stick(s.ly);
    h.rx = scaled_stick(s.rx);
    h.ry = scaled_stick(s.ry);
    h.l2 = scaled_trigger(s.l2);
    h.r2 = scaled_trigger(s.r2);
    return h;
}

namespace {
float unit_axis(int16_t v, float lo) {
    return std::clamp(float(v) / 32767.0f, lo, 1.0f);
}

// A stick pair inside the radial dead zone reads as centred; outside it
// passes through unscaled (the game applies its own curve). NFSMW 3.0.4: the
// zone is the core mod's stick_deadzone (percent; 8 as before).
void stick_pair(int16_t x, int16_t y, float dead, float *ox, float *oy) {
    const float fx = unit_axis(x, -1.0f), fy = unit_axis(y, -1.0f);
    if (std::hypot(fx, fy) < dead) {
        *ox = *oy = 0;
        return;
    }
    *ox = fx;
    *oy = fy;
}
} // namespace

PadState pad_from_sdl(const bool buttons[kSdlPadButtonCount], const int16_t axes[kSdlAxisCount]) {
    static const struct {
        int sdl;
        uint16_t bit;
    } kButtons[] = {
        {kSdlPadSouth, kPadCross},     {kSdlPadEast, kPadCircle},      {kSdlPadWest, kPadSquare},
        {kSdlPadNorth, kPadTriangle},  {kSdlPadBack, kPadSelect},      {kSdlPadGuide, kPadPs},
        {kSdlPadStart, kPadStart},     {kSdlPadLeftStick, kPadL3},     {kSdlPadRightStick, kPadR3},
        {kSdlPadLeftShoulder, kPadL1}, {kSdlPadRightShoulder, kPadR1},
    };
    static const struct {
        int sdl;
        uint8_t bit;
    } kHat[] = {
        {kSdlPadDpadUp, kHatUp},
        {kSdlPadDpadDown, kHatDown},
        {kSdlPadDpadLeft, kHatLeft},
        {kSdlPadDpadRight, kHatRight},
    };
    PadState p;
    for (const auto &b : kButtons)
        if (buttons[b.sdl])
            p.buttons |= b.bit;
    for (const auto &h : kHat)
        if (buttons[h.sdl])
            p.hat |= h.bit;
    // NFSMW 3.0.4 (idea: SpeedBreaker's deadzone settings; no code taken): Controls > Stick deadzone / Trigger deadzone (core mod, percent; 8 and 0 = as before). A trigger
    // below its zone reads 0 and above it is rescaled to 0..1, so a worn trigger that never quite returns stops
    // giving gas or brake.
    const float stick_dead = std::clamp(float(mods_core_setting("stick_deadzone", 8)), 0.0f, 30.0f) / 100.0f;
    const float trigger_dead = std::clamp(float(mods_core_setting("trigger_deadzone", 0)), 0.0f, 30.0f) / 100.0f;
    stick_pair(axes[kSdlAxisLeftX], axes[kSdlAxisLeftY], stick_dead, &p.lx, &p.ly);
    stick_pair(axes[kSdlAxisRightX], axes[kSdlAxisRightY], stick_dead, &p.rx, &p.ry);
    auto trigger = [trigger_dead](int16_t v) {
        const float t = unit_axis(v, 0.0f);
        return t <= trigger_dead ? 0.0f : (t - trigger_dead) / (1.0f - trigger_dead);
    };
    p.l2 = trigger(axes[kSdlAxisLeftTrigger]);
    p.r2 = trigger(axes[kSdlAxisRightTrigger]);
    if (p.l2 > 0.5f)
        p.buttons |= kPadL2;
    if (p.r2 > 0.5f)
        p.buttons |= kPadR2;
    return p;
}

} // namespace controls
