// framelate.h - how long the game thread has gone without a frame, as diag.c's hang monitor measures it.
// The monitor runs on its own thread while the game thread stamps each frame (diag_frame: last_frame_ns = now).
// The stamp is read before the clock, then once more after it. Read after the clock only, a frame stamped between the two reads is newer than the clock
// reading and the unsigned difference wraps to ~584 years: a "stall: no frame for 500 ms" report about a frame that
// came on time. A loaded Mac makes that likely (the monitor is preempted between the reads while frames keep coming);
// the report's signals then cut that frame's pacing wait short, so the stack shows pacing_present's mach_wait_until.
// The stamp read first can itself be stale: a monitor preempted for as long as the stall limit before it reads the
// clock would measure a frame that was replaced meanwhile. The second read takes the newest stamp, so a frame that
// landed during any delay is never counted as late (a stamp newer than the clock reading is a gap of 0).
// Pure (the clock is passed in) so runtime/framelate_test.c runs it against a game thread stamping frames.
#pragma once
#include <stdint.h>

// stamp: the game thread's last frame time (0 = none yet); clock: host_ticks. Returns the ns since that frame (0 when
// there is none) and stores the stamp it measured from in *from (the monitor reports each late frame once).
static inline uint64_t framelate_ns(const uint64_t *stamp, uint64_t (*clock)(void), uint64_t *from) {
    uint64_t last = __atomic_load_n(stamp, __ATOMIC_ACQUIRE), now = clock();
    uint64_t again = __atomic_load_n(stamp, __ATOMIC_ACQUIRE);
    if (again != last) last = again;
    if (from) *from = last;
    return last && now > last ? now - last : 0;
}
