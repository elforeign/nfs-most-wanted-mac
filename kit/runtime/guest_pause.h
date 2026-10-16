// guest_pause.h - the settings menu pausing the game (NFSMW 3.0.4, D-202 item 4; design
// review/run36/design-kit-focus-pause.md section 3). Game-agnostic: no game address, no time scaling.
//
// The main guest thread parks at its frame boundary (after Present, host_guest_pause_point) while the menu is open
// and the core mod's pause_in_menu is on: the guest clock (GetTickCount, timeGetTime, QueryPerformanceCounter,
// multimedia timers) is held and the mixer paused, so on resume the game sees about 0 ms gone and nothing jumps.
// While parked it sleeps in short slices: that gives up the baton (workers, loaders and the audio feed keep
// running), the scheduler's checkpoint drains the menu's deferred Apply calls on the main thread (the renderer reset
// runs between two frames, as without the pause), and a clock read services the host so menu keys arrive.
//
// The logic is here, pure, so runtime_tests drive it with fakes; misc.cpp wires the real operations.
#pragma once

struct GuestPauseOps {
    void *ctx = nullptr;
    bool (*wanted)(void *ctx) = nullptr;            // the menu is open, the setting on, no close asked for
    void (*hold)(void *ctx, bool on) = nullptr;     // guest clock + mixer (+ the menu's Apply watchdog)
    void (*slice)(void *ctx) = nullptr;             // one parked slice: sleep, then service the host
    double (*now)(void *ctx) = nullptr;             // real seconds, for the log line
    void (*log)(void *ctx, bool paused, double seconds) = nullptr;
};

// Parks while wanted() holds; returns the real seconds parked (0 when it did not park).
inline double guest_pause_run(const GuestPauseOps &o) {
    if (!o.wanted(o.ctx))
        return 0.0;
    const double t0 = o.now(o.ctx);
    o.hold(o.ctx, true);
    o.log(o.ctx, true, 0.0);
    while (o.wanted(o.ctx))
        o.slice(o.ctx);
    const double seconds = o.now(o.ctx) - t0;
    o.hold(o.ctx, false);
    o.log(o.ctx, false, seconds);
    return seconds;
}
