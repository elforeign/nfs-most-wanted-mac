// looppace.h - the game loop's frame slot after Present (pacing.c), including the F10 frame rate limit.
// Pure policy (no clock, no guest memory) so runtime/looppace_test.c runs the same code with a simulated loop.
//
// Every pass normally runs one sim tick and Present waits until one tick after the previous frame. A frame rate limit
// longer than a tick (60 or 40 on a 120 Hz display) makes the frame slot the limit's whole-refresh duration, so the
// loop renders exactly the frames the display shows, each advancing the sim by the same k = period / tick ticks.
// Presentation alone (Metal's minimum duration) left the loop at the tick rate: at limit 60 on 120 Hz it rendered
// ~90-100 fps, the presenter skipped a third of them and the shown frames advanced the sim by an uneven 1 or 2 ticks.
#pragma once
#include <math.h>
#include <stdint.h>

// Ticks one frame runs under the limit: 1 when the limit is Off or not longer than a tick.
static inline int looppace_ticks(double step_s, double limit_s) {
    if (!(step_s > 0) || !(limit_s > step_s * 1.01)) return 1;
    return (int)ceil(limit_s / step_s - 1e-3);
}

// deadline: the previous slot (0 = none); now: the time after Present; step_s, frac: the Scheduler's tick and leftover
// tick fraction; limit_s: the limit's frame duration (0 = Off); ticks_run: the most ticks one pass ran since the last
// Present. Returns the next slot; the caller sleeps until it when it is later than now.
static inline uint64_t looppace_deadline(uint64_t deadline, uint64_t now, float step_s, float frac, double limit_s,
                                         uint32_t ticks_run, int keep_slot_when_behind) {
    int k = looppace_ticks(step_s, limit_s);
    uint64_t tick = (uint64_t)(step_s * 1e9), period = k > 1 ? (uint64_t)(limit_s * 1e9) : tick;
    // The sim needed more ticks than a frame holds: let it recover without a pacing sleep, even if the old slot is ahead.
    if (ticks_run > (uint32_t)k && !keep_slot_when_behind) return now;
    // keep the leftover fraction near the middle: a pass that measures slightly short or long still runs k ticks
    int64_t adjust = (int64_t)((0.5f - frac) * 0.1f * (float)tick);
    deadline += (uint64_t)((int64_t)period + adjust);
    if (deadline + period < now || deadline > now + 2 * period) deadline = now;   // behind by a frame, or first call
    return deadline;
}
