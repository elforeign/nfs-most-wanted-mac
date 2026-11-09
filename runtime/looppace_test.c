// Offline: clang -std=c11 -Wall -Wextra -Werror runtime/looppace_test.c -o /tmp/looppace_test && /tmp/looppace_test
// --broken runs the loop policy as it was before the limit paced the loop (pinned below): must FAIL the limit checks.
// A simulated game loop (Scheduler accumulator, 9 ms frames with jitter, sleep wake latency) feeding a fixed-refresh
// presenter with Metal's minimum display duration (framecap.h) that skips a frame while two are already queued, as
// render_present's presenter does when nextDrawable blocks. Measured 2026-11-03:
//   pass:   31 checks, 0 failures; limit 60 at 120 Hz: 60.00 fps, 100 % of frames shown, 100 % two-tick frames
//   broken: 31 checks, 11 failures (exit 1); limit 60 at 120 Hz: 110.6 fps, 54 % shown, 9.5 % two-tick frames
// (the minimum duration keeps the shown intervals even either way; the judder was the unshown frames and the uneven
// sim advance, 1 or 2 ticks, between the shown ones).
// The Off / 120 limit slots must match the pinned policy frame for frame.
#include <stdio.h>
#include <string.h>
#include "framecap.h"
#include "looppace.h"

static int checks, failures, broken;
#define CHECK(x) do { ++checks; if (!(x)) { ++failures; fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); } } while (0)

// The loop policy before the limit paced the loop (pacing_present, main 5a9b1d46), verbatim apart from the signature.
static uint64_t pinned_deadline(uint64_t deadline, uint64_t now, float step, float frac, uint32_t ticks_run, int lock) {
    int behind = ticks_run > 1;
    uint64_t period = (uint64_t)(step * 1e9);
    if (behind && !lock) { deadline = now; return deadline; }
    int64_t adjust = (int64_t)((0.5f - frac) * 0.1f * (float)period);
    deadline += (uint64_t)((int64_t)period + adjust);
    if (deadline + period < now || deadline > now + 2 * period) deadline = now;   // behind by a frame, or first call
    return deadline;
}

static uint32_t rng = 12345;
static double jitter(double amp) { rng = rng * 1103515245u + 12345u; return amp * (((rng >> 8) & 0xFFFF) / 65535.0 * 2 - 1); }

typedef struct { double hz; int sim_hz, limit; double work_ms, jitter_ms; int frames, hitch_frame; int policy; } Run;
typedef struct { double fps, shown_ratio, interval_ok, ticks_ok, drift_ms, min_gap_ms; uint64_t times[4096]; int n; } Result;

static Result simulate(Run r) {
    static Result o; memset(&o, 0, sizeof o);
    rng = 12345;
    float step = 1.0f / (float)r.sim_hz;
    double limit_s = framecap_duration(r.hz, r.limit), vsync = 1.0 / r.hz;
    int k = looppace_ticks(step, limit_s);
    double t = 1.0, last_pass = t, acc = 0.5 * step, last_shown = 0, queued[64]; int nq = 0;
    uint64_t deadline = 0;
    int warm = 60, rendered = 0, shown = 0, intervals = 0, interval_ok = 0, ticks_ok = 0;
    double t0 = 0, ticks0 = 0, ticks_total = 0, prev_start = 0;
    o.min_gap_ms = 1e9;
    for (int f = 0; f < r.frames; ++f) {
        if (f == warm) { t0 = t; ticks0 = ticks_total; }
        // frame start to frame start (the slot); Present times also carry the frame's work jitter
        if (f > warm && f > r.hitch_frame + 2 && (t - prev_start) * 1000 < o.min_gap_ms) o.min_gap_ms = (t - prev_start) * 1000;
        prev_start = t;
        acc += t - last_pass; last_pass = t;              // Scheduler::Run: whole ticks of the measured time
        int n = (int)(acc / step), cap = 6 * r.sim_hz / 60;
        if (n > cap) { acc -= (n - cap) * (double)step; n = cap; }
        acc -= n * (double)step; ticks_total += n;
        float frac = (float)(acc / step);
        t += (r.work_ms + jitter(r.jitter_ms) + (f == r.hitch_frame ? 100 : 0)) / 1000;
        // Present: the presenter skips the frame while two are queued (nextDrawable blocks), else shows it at the first
        // refresh after the GPU finishes and at least the minimum duration after the previous one.
        int q = 0;
        for (int i = 0; i < nq; ++i) if (queued[i] > t) queued[q++] = queued[i];
        nq = q;
        int counted = f >= warm && f != r.hitch_frame && f != r.hitch_frame + 1;
        if (f >= warm) { ++rendered; if (n == k) ++ticks_ok; }
        if (nq < 2) {
            double ready = fmax(t + 0.002, last_shown + fmax(limit_s, vsync) - 1e-6);
            double at = ceil((ready - 0.00037) / vsync) * vsync + 0.00037;
            if (last_shown > 0 && f >= warm) {
                ++shown;
                if (counted) { ++intervals; if (fabs((at - last_shown) - fmax(limit_s, vsync)) < 0.00075) ++interval_ok; }
            }
            last_shown = at; queued[nq++] = at;
        }
        uint64_t now = (uint64_t)(t * 1e9);
        deadline = r.policy ? pinned_deadline(deadline, now, step, frac, (uint32_t)n, 0)
                            : looppace_deadline(deadline, now, step, frac, limit_s, (uint32_t)n, 0);
        if (deadline > now) t = deadline / 1e9 + (50 + (rng >> 20) % 250) / 1e6;   // sleep, then a 50-300 us wake
        if (f < 4096) o.times[o.n++] = deadline;
    }
    double wall = t - t0;
    o.fps = rendered / wall;
    o.shown_ratio = (double)shown / rendered;
    o.interval_ok = intervals ? (double)interval_ok / intervals : 0;
    o.ticks_ok = (double)ticks_ok / rendered;
    o.drift_ms = ((ticks_total - ticks0) * step - wall) * 1000;
    return o;
}
static Result run(Run r) { r.policy = broken; return simulate(r); }
static void show(const char *name, Result o) {
    printf("%-26s %7.2f fps, %5.1f %% shown, %5.1f %% intervals at the limit, %5.1f %% frames of k ticks, sim-wall %+.2f ms, min gap %.2f ms\n",
           name, o.fps, 100 * o.shown_ratio, 100 * o.interval_ok, 100 * o.ticks_ok, o.drift_ms, o.min_gap_ms);
}

int main(int argc, char **argv) {
    broken = argc > 1 && !strcmp(argv[1], "--broken");
    // the policy's ticks per frame
    CHECK(looppace_ticks(1.0f / 120, 0) == 1);
    CHECK(looppace_ticks(1.0f / 120, framecap_duration(120, 120)) == 1);
    CHECK(looppace_ticks(1.0f / 120, framecap_duration(1000 / 8.333333, 60)) == 2);
    CHECK(looppace_ticks(1.0f / 120, framecap_duration(120, 40)) == 3);
    CHECK(looppace_ticks(1.0f / 60, framecap_duration(60, 60)) == 1);
    CHECK(looppace_ticks(1.0f / 60, framecap_duration(60, 40)) == 2);
    CHECK(looppace_ticks(1.0f / 60, framecap_duration(59.94, 60)) == 1);

    // limit 60 on a 120 Hz display, 9 +- 1.5 ms frames (the owner's case: ~90-100 fps rendered before)
    Result a = run((Run){.hz = 1000 / 8.333333, .sim_hz = 120, .limit = 60, .work_ms = 9, .jitter_ms = 1.5, .frames = 3000, .hitch_frame = -9});
    show("limit 60 @120 Hz", a);
    CHECK(fabs(a.fps - 60) < 0.3);
    CHECK(a.shown_ratio >= 0.99);
    CHECK(a.interval_ok >= 0.95);
    CHECK(a.ticks_ok >= 0.99);
    CHECK(fabs(a.drift_ms) < 20);
    // limit 40 on 120 Hz: 25 ms, three ticks
    Result b = run((Run){.hz = 120, .sim_hz = 120, .limit = 40, .work_ms = 9, .jitter_ms = 1.5, .frames = 2000, .hitch_frame = -9});
    show("limit 40 @120 Hz", b);
    CHECK(fabs(b.fps - 40) < 0.3); CHECK(b.shown_ratio >= 0.99); CHECK(b.interval_ok >= 0.95); CHECK(b.ticks_ok >= 0.99);
    // limit 40 on a 60 Hz display: two refreshes (30 fps), two ticks
    Result c = run((Run){.hz = 60, .sim_hz = 60, .limit = 40, .work_ms = 9, .jitter_ms = 1.5, .frames = 2000, .hitch_frame = -9});
    show("limit 40 @60 Hz", c);
    CHECK(fabs(c.fps - 30) < 0.3); CHECK(c.shown_ratio >= 0.99); CHECK(c.interval_ok >= 0.95); CHECK(c.ticks_ok >= 0.99);
    // one 100 ms frame at limit 60: one catch-up frame without a sleep (as before), then back on the slot with no burst
    Result d = run((Run){.hz = 120, .sim_hz = 120, .limit = 60, .work_ms = 9, .jitter_ms = 1.5, .frames = 1500, .hitch_frame = 600});
    show("limit 60, 100 ms hitch", d);
    CHECK(d.min_gap_ms > 16.0); CHECK(a.min_gap_ms > 16.0); CHECK(d.interval_ok >= 0.95);
    // a Mac too slow for the limit (20 ms frames at limit 60): no pacing sleep, sim time keeps wall time
    Result e = run((Run){.hz = 120, .sim_hz = 120, .limit = 60, .work_ms = 20, .jitter_ms = 1.5, .frames = 1500, .hitch_frame = -9});
    show("limit 60, 20 ms frames", e);
    CHECK(e.fps > 48 && e.fps < 51); CHECK(fabs(e.drift_ms) < 20);
    // Off and 120 (not longer than a tick): the same slots as the pinned policy, frame for frame
    const struct { int hz, limit; double work; } same[] = {{120, 0, 5}, {120, 120, 5}, {120, 0, 9}, {60, 0, 9}, {60, 120, 9}, {60, 60, 9}};
    for (unsigned i = 0; i < sizeof same / sizeof *same; ++i) {
        Run r = {.hz = same[i].hz, .sim_hz = same[i].hz, .limit = same[i].limit, .work_ms = same[i].work, .jitter_ms = 1.5, .frames = 1200, .hitch_frame = 500};
        static Result x, y;
        r.policy = 0; x = simulate(r);
        r.policy = 1; y = simulate(r);
        CHECK(x.n == y.n && !memcmp(x.times, y.times, sizeof x.times[0] * (size_t)x.n));
        if (i == 0) show("Off @120 Hz, 5 ms", x);
    }
    printf("LOOPPACE %s%s: %d checks, %d failures\n", failures ? "FAIL" : "PASS", broken ? " (broken)" : "", checks, failures);
    return failures ? 1 : 0;
}
