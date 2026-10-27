// Presentation policy only; the simulation clock and Scheduler are independent.
#pragma once
#include <math.h>

static inline int framecap_refreshes(double hz, int limit) {
    if (limit <= 0 || !(hz > 0)) return 0;
    // Tolerate roundoff at exact divisors, not a fractional refresh. hz comes from 1000 / minimumRefreshInterval, and the
    // A 120 Hz display can report 8.333333 ms (120.0000048 Hz): 1e-9 made limit 60 wait 3 refreshes (25 ms,
    // 40 fps) and limit 120 wait 2 (60 fps). 1e-4 is far below any real fractional refresh (59.94 / 40 = 1.4985).
    return (int)fmax(1, ceil(hz / limit - 1e-4));
}
static inline double framecap_duration(double hz, int limit) {
    int n = framecap_refreshes(hz, limit);
    return n ? n / hz : 0;
}
static inline int framecap_interval_bucket(double ms) {
    if (fabs(ms - 1000.0 / 120) <= 0.75) return 0;
    if (fabs(ms - 1000.0 / 60) <= 0.75) return 1;
    if (fabs(ms - 25.0) <= 0.75) return 2;
    return 3;
}
