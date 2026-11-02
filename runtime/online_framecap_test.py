"""Native production Present cap with a virtual clock, sleep and GIL (no GPU).

600 frames, 3 ms work, 1 ms sleep overshoot + 0.5 ms GIL delay:
original actual-wake anchor: 898.500 ms drift, 55.046 fps (FAIL);
scheduled deadline: 1.500 ms drift, 59.991 fps (PASS).
The original cap block is pinned below, independent of repository refs.
The native harness also checks a 100 ms frame, late wake/GIL stalls, reset,
cap-off, cap changes, EINTR and the full-period overrun boundary.
"""
from pathlib import Path
import subprocess
import tempfile

RT = Path(__file__).resolve().parent

BASELINE = r'''    // Also bound submitted frames when a background/occluded window has no drawable.
    // Anchor to the actual previous frame, so a slow frame never creates a catch-up burst.
    static uint64_t online_previous;
    if (atomic_load_explicit(&roam_online_active, memory_order_relaxed)) {
        RenderPresentation policy; render_presentation_stats(&policy);
        uint64_t now = host_ticks();
        uint64_t deadline = online_previous + 1000000000ull / (unsigned)policy.limit;
        if (online_previous && deadline > now) {
            uint64_t wait_started = now;
            gil_release();
            while ((now = host_ticks()) < deadline) {
                uint64_t remaining = deadline - now;
                struct timespec t = {.tv_sec=(time_t)(remaining / 1000000000), .tv_nsec=(long)(remaining % 1000000000)};
                if (nanosleep(&t, NULL) && errno != EINTR) break;
            }
            uint64_t woke = host_ticks(); gil_acquire();
            diag_idle_ns[IDLE_SLEEP] += woke - wait_started;
            diag_idle_ns[IDLE_GIL] += host_ticks() - woke;
        }
        online_previous = host_ticks();
    } else online_previous = 0;
'''

HARNESS = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdatomic.h>
#include <time.h>
#include <errno.h>
#include <string.h>
#include "roam_online.h"

typedef struct { int limit; } RenderPresentation;
static int mtl = 1, selected_limit = 60;
atomic_int roam_online_active;
static uint64_t clock_ns, sleep_delay, acquire_delay, release_delay;
static uint64_t diag_idle_ns[2];
enum { IDLE_SLEEP, IDLE_GIL };
static int sleeps, releases, acquires, held = 1, interrupts, failures, checks;
#define CHECK(x) do { ++checks; if (!(x)) { ++failures; fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); } } while (0)
static uint64_t host_ticks(void) { return clock_ns; }
static void render_presentation_stats(RenderPresentation *p) {
    p->limit = roam_online_limit(selected_limit, atomic_load(&roam_online_active));
}
static void gil_release(void) { CHECK(held); held = 0; ++releases; clock_ns += release_delay; }
static void gil_acquire(void) { CHECK(!held); held = 1; ++acquires; clock_ns += acquire_delay; }
static int fake_sleep(const struct timespec *t, struct timespec *unused) {
    (void)unused;
    CHECK(!held);
    uint64_t ns = (uint64_t)t->tv_sec * 1000000000ull + (uint64_t)t->tv_nsec;
    CHECK(ns > 0);
    ++sleeps;
    if (interrupts) { --interrupts; clock_ns += ns / 2; errno = EINTR; return -1; }
    clock_ns += ns + sleep_delay;
    return 0;
}
#define nanosleep fake_sleep
static void present_cap(void) {
    if (!mtl) return;
@CAP@
}
#undef nanosleep
static const uint64_t period = 1000000000ull / 60;
static const uint64_t work = 3000000;
static void reset(void) {
    atomic_store(&roam_online_active, 0);
    present_cap();
    clock_ns = 1000000000;
    selected_limit = 60;
    sleep_delay = acquire_delay = release_delay = 0;
    sleeps = releases = acquires = interrupts = 0;
    diag_idle_ns[0] = diag_idle_ns[1] = 0;
    atomic_store(&roam_online_active, 1);
    present_cap();
}
static void cadence(uint64_t wake, uint64_t gil) {
    reset();
    sleep_delay = wake; acquire_delay = gil;
    uint64_t start = clock_ns;
    for (int frame = 1; frame < 600; ++frame) { clock_ns += work; present_cap(); }
    uint64_t elapsed = clock_ns - start, ideal = 599 * period;
    double fps = 599.0 * 1e9 / elapsed;
    printf("600 frames: wake=%.3f ms GIL=%.3f ms drift=%.3f ms fps=%.3f\n",
           wake / 1e6, gil / 1e6, (elapsed - ideal) / 1e6, fps);
    CHECK(elapsed >= ideal && elapsed - ideal <= 1500000);
    CHECK(fps > 59.98 && fps <= 60.001);
    CHECK(sleeps == 599 && releases == 599 && acquires == 599 && held);
    CHECK(diag_idle_ns[IDLE_GIL] == 599 * gil);
}
static void stall(void) {
    reset();
    sleep_delay = 1000000; acquire_delay = 500000;
    for (int i = 0; i < 40; ++i) { clock_ns += work; present_cap(); }
    clock_ns += 100000000;
    uint64_t before = clock_ns;
    int old_sleeps = sleeps;
    present_cap();
    CHECK(clock_ns == before && sleeps == old_sleeps);
    uint64_t minimum = UINT64_MAX;
    for (int i = 0; i < 20; ++i) {
        uint64_t previous = clock_ns;
        clock_ns += work; present_cap();
        uint64_t interval = clock_ns - previous;
        if (interval < minimum) minimum = interval;
        CHECK(interval >= period);
    }
    printf("100 ms frame: next 20 intervals min=%.6f ms, sleep calls=%d (no burst)\n",
           minimum / 1e6, sleeps - old_sleeps);
}
static void wait_stall(uint64_t wake, uint64_t gil) {
    reset();
    sleep_delay = wake; acquire_delay = gil;
    clock_ns += work; present_cap();
    sleep_delay = acquire_delay = 0;
    uint64_t previous = clock_ns;
    clock_ns += work; present_cap();
    CHECK(clock_ns - previous == period);
}
static void boundaries(void) {
    reset();
    uint64_t start = clock_ns;
    clock_ns += 2 * period - 1; present_cap();
    CHECK(sleeps == 0);
    clock_ns += 1; present_cap();
    CHECK(clock_ns == start + 2 * period); // less than a period late kept the slot
    reset(); start = clock_ns;
    clock_ns += 2 * period; present_cap(); // exactly one period overdue rebases
    clock_ns += work; present_cap();
    CHECK(clock_ns == start + 3 * period);
    reset();
    clock_ns += work; interrupts = 1; present_cap();
    CHECK(clock_ns == 1000000000 + period && sleeps == 2 && held);
    reset();
    release_delay = 100000000;
    clock_ns += work; present_cap();
    CHECK(sleeps == 0 && releases == 1 && acquires == 1);
    release_delay = 0;
    start = clock_ns;
    clock_ns += work; present_cap();
    CHECK(clock_ns - start == period);
}
static void modes(void) {
    reset();
    // Pause: no Present calls for 100 ms; resumption rebases, then waits a slot.
    clock_ns += 100000000; present_cap();
    uint64_t start = clock_ns;
    clock_ns += work; present_cap(); CHECK(clock_ns - start == period);
    // Cap changes rebase immediately, including 0 -> the online safety cap (60).
    const int limits[] = {40, 60, 0, 120, 40};
    for (unsigned i = 0; i < sizeof limits / sizeof *limits; ++i) {
        int previous = roam_online_limit(selected_limit, 1);
        selected_limit = limits[i];
        int effective = roam_online_limit(selected_limit, 1);
        if (effective == previous) continue;
        clock_ns += work; start = clock_ns; present_cap(); CHECK(clock_ns == start);
        clock_ns += work; present_cap(); CHECK(clock_ns - start == 1000000000ull / effective);
    }
    atomic_store(&roam_online_active, 0); selected_limit = 0;
    int count = sleeps;
    for (int i = 0; i < 600; ++i) {
        clock_ns += work; start = clock_ns; present_cap(); CHECK(clock_ns == start);
    }
    CHECK(sleeps == count); // offline cap off performs no wait or GIL release
    atomic_store(&roam_online_active, 1);
    start = clock_ns; present_cap(); CHECK(clock_ns == start);
    clock_ns += work; present_cap(); CHECK(clock_ns - start == period);
}
int main(int argc, char **argv) {
    cadence(1000000, 500000);
    if (argc == 1 || strcmp(argv[1], "--baseline")) {
        cadence(1500000, 0);
        cadence(0, 1500000);
        stall();
        wait_stall(100000000, 0);
        wait_stall(0, 100000000);
        boundaries();
        modes();
    }
    printf("ONLINE FRAMECAP %s: %d checks, %d failures\n", failures ? "FAIL" : "PASS", checks, failures);
    return failures ? 1 : 0;
}
'''


def main():
    source = (RT / 'render_present.m').read_text()
    # Compile the actual entry-point cap, leaving Metal/frame rendering out.
    cap = source.split('void render_present(void) {\n    if (!mtl) return;\n', 1)[1].split('    frame_pool_cycle();', 1)[0]
    with tempfile.TemporaryDirectory(prefix='online-framecap-') as td:
        td = Path(td)
        for name, block, expected in [('current', cap, 0), ('baseline', BASELINE, 1)]:
            c, exe = td / (name + '.c'), td / name
            c.write_text(HARNESS.replace('@CAP@', block))
            result = subprocess.run(['clang', '-std=c11', '-Wall', '-Wextra', '-Werror', '-g',
                                     '-fsanitize=address,undefined', '-I', str(RT), str(c), '-o', str(exe)],
                                    capture_output=True, text=True, timeout=60)
            assert result.returncode == 0, result.stderr[-4000:]
            result = subprocess.run([str(exe)] + (['--baseline'] if name == 'baseline' else []),
                                    capture_output=True, text=True, timeout=30)
            print(name + ':\n' + result.stdout, end='', flush=True)
            assert result.returncode == expected, result.stderr[-4000:]
            if name == 'baseline':
                assert 'drift=898.500 ms fps=55.046' in result.stdout, result.stdout
    print('ONLINE FRAMECAP TEST PASS: production passes, original anchor fails')


if __name__ == '__main__':
    main()
