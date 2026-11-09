// Offline: clang -std=c11 -Wall -Wextra -Werror runtime/framelate_test.c -o /tmp/framelate_test && /tmp/framelate_test
// The hang monitor's "no frame for N ms" measurement (framelate.h) against a game thread stamping frames on time.
// --broken measures with the monitor's first read order (clock, then stamp; pinned below): must FAIL.
// --stale measures with the order before the stamp re-read (stamp, then clock; pinned below): must FAIL.
// 1. A frame stamped between the monitor's two reads (the monitor preempted there, as on a loaded Mac): no stall.
// 1b. The monitor preempted for 600 ms between loading the stamp and reading the clock, a frame stamped meanwhile:
//    the stamp it loaded is stale, the newest frame is on time: no stall (and none when that frame lands after the
//    clock read, where the stamp is newer than the clock: a gap of 0).
// 2. Two real threads for 0.5 s: a game thread stamping a frame every 20 us, the monitor checking as fast as it can
//    with a 500 ms stall limit: every report is false (no frame is ever late), so the count must be 0.
// Measured 2026-11-04 (load average 6, three runs each):
//   pass:   7 checks, 0 failures (before the preemption cases); threads: 0, 0, 0 false stalls in ~17.5 M checks
//   broken: 7 checks, 3 failures (exit 1); threads: 8, 5, 6 false stalls in ~18 M checks
// Re-read of the stamp after the clock (two scripted preemption cases added), same day: pass 10 checks, 0 failures;
//   --stale (the stamp-then-clock order without the re-read) 10 checks, 3 failures: the preempted case measures 616 ms
//   (want 5 ms) and a frame landing after the clock read measures 616 ms (want 0); --broken 10 checks, 4 failures.
// The in-game failures it reproduces: stall_1.txt "no frame for 500 ms after frame 1860" while frames.log shows
// frames 1860/1861 6.6 ms apart and the profiler (stamp read first) saw no frame over 100 ms.
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include "framelate.h"

static int checks, failures, broken;
#define CHECK(x) do { ++checks; if (!(x)) { ++failures; fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); } } while (0)

static const uint64_t STALL_NS = 500 * 1000000ull;      // BOOT_DIAG_STALL default

// The monitor's measurement before framelate.h (diag.c hang_monitor), verbatim apart from the signature:
//   uint64_t t = host_ticks();
//   if (... last_frame_ns && ... t - last_frame_ns > (uint64_t)stall_ms * 1000000ull && ...)
static uint64_t pinned_late(const uint64_t *last_frame_ns, uint64_t (*host_ticks)(void), uint64_t *from) {
    uint64_t t = host_ticks();
    uint64_t last = __atomic_load_n(last_frame_ns, __ATOMIC_ACQUIRE);
    if (from) *from = last;
    return last ? t - last : 0;
}
// The order before the stamp re-read (framelate.h as of the previous change), pinned.
static uint64_t stale_late(const uint64_t *stamp, uint64_t (*clock)(void), uint64_t *from) {
    uint64_t last = __atomic_load_n(stamp, __ATOMIC_ACQUIRE), now = clock();
    if (from) *from = last;
    return last && now > last ? now - last : 0;
}
static int stale;
static uint64_t late(const uint64_t *stamp, uint64_t (*clock)(void), uint64_t *from) {
    if (stale) return stale_late(stamp, clock, from);
    return broken ? pinned_late(stamp, clock, from) : framelate_ns(stamp, clock, from);
}

// 1. A scripted interleaving: the clock read is where the monitor gets preempted; the game thread stamps the next
// frame 16.7 ms later meanwhile.
static uint64_t fake_now, fake_stamp;
static int stamp_during_read, stamp_before_read;
static uint64_t fake_clock(void) {
    if (stamp_before_read) { fake_now += 600000000ull; fake_stamp = fake_now - 5000000; }   // preempted 600 ms, frame 5 ms ago
    if (stamp_before_read == 2) fake_stamp = fake_now + 3000000;                            // ... or a frame just after the clock
    uint64_t t = fake_now;
    if (stamp_during_read) { fake_now += 16700000; fake_stamp = fake_now; }
    return t;
}

// 2. Real threads.
static uint64_t mono_ns(void) { struct timespec t; clock_gettime(CLOCK_UPTIME_RAW, &t); return (uint64_t)t.tv_sec * 1000000000ull + (uint64_t)t.tv_nsec; }
static uint64_t frame_stamp;
static atomic_int running = 1;
static void *game_thread(void *p) {
    (void)p;
    while (atomic_load(&running)) {
        __atomic_store_n(&frame_stamp, mono_ns(), __ATOMIC_RELEASE);    // diag_frame
        uint64_t until = mono_ns() + 20000;
        while (mono_ns() < until) {}
    }
    return NULL;
}

int main(int argc, char **argv) {
    broken = argc > 1 && !strcmp(argv[1], "--broken");
    stale = argc > 1 && !strcmp(argv[1], "--stale");

    // the frame came 5 ms ago, nothing happens between the reads: 5 ms
    fake_now = 1000000000ull; fake_stamp = fake_now - 5000000; stamp_during_read = 0;
    uint64_t from = 0, l = late(&fake_stamp, fake_clock, &from);
    CHECK(l == 5000000);
    CHECK(from == fake_now - 5000000);
    // no frame yet: never late
    fake_stamp = 0;
    CHECK(late(&fake_stamp, fake_clock, NULL) == 0);
    // a real stall: the frame came 600 ms ago
    fake_stamp = fake_now - 600000000ull;
    CHECK(late(&fake_stamp, fake_clock, NULL) > STALL_NS);
    // a frame stamped between the two reads: the measurement is of a frame that is on time, not a stall
    fake_now = 2000000000ull; fake_stamp = fake_now - 16000000; stamp_during_read = 1;
    l = late(&fake_stamp, fake_clock, &from);
    CHECK(l <= 16700000);
    CHECK(l <= STALL_NS);
    // the stamp loaded first goes stale while the monitor is preempted before its clock read: the new frame counts
    stamp_during_read = 0; stamp_before_read = 1;
    fake_now = 3000000000ull; fake_stamp = fake_now - 16000000;
    l = late(&fake_stamp, fake_clock, &from);
    CHECK(l == 5000000);
    CHECK(from == fake_now - 5000000);
    // the replacing frame is newer than the clock reading: a gap of 0, never a wrapped difference
    stamp_before_read = 2;
    fake_now = 4000000000ull; fake_stamp = fake_now - 16000000;
    l = late(&fake_stamp, fake_clock, &from);
    CHECK(l == 0);
    stamp_before_read = 0;

    pthread_t th;
    pthread_create(&th, NULL, game_thread, NULL);
    while (!__atomic_load_n(&frame_stamp, __ATOMIC_ACQUIRE)) {}
    uint64_t end = mono_ns() + 500000000ull, n = 0, stalls = 0;
    while (mono_ns() < end) {
        ++n;
        if (late(&frame_stamp, mono_ns, NULL) > STALL_NS) ++stalls;
    }
    atomic_store(&running, 0);
    pthread_join(th, NULL);
    printf("threads: %llu false stalls in %llu checks\n", (unsigned long long)stalls, (unsigned long long)n);
    CHECK(stalls == 0);

    printf("%s%d checks, %d failures\n", broken ? "broken: " : stale ? "stale: " : "", checks, failures);
    return failures ? 1 : 0;
}
