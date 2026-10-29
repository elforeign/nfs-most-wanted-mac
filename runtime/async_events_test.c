// async_events_test.c - drives the production threads_set_event_async / apply_pending_events (threads.c, included):
// 200 distinct events are queued from a host thread before the guest applies them (audit2 #10); every one must be signaled.
// Prints how many of the 200 ended up signaled. -DTHREADS_C=\"path\" selects the threads.c under test.
// Challenger item 5: queue/close/reuse and close/reuse/queue must both reject the old submission generation.
// Measured 2026-10-27: production 200/200 signals + 6 generation checks, 0 failures (exit 0).
// Source control replacing `o->generation == ev[i].generation` with `1`: 200/200 + 6 checks, 2 failures (exit 1).
// Source control disabling pending-capacity growth: 64/200 signals, 1 failure (exit 1).
#include <stdio.h>
#include <string.h>
#include <time.h>
#ifndef THREADS_C
#define THREADS_C "threads.c"
#endif
#include THREADS_C
uint8_t *G; int diag_on = 0; int boot_trace; uint32_t last_error; uint64_t diag_idle_ns[IDLE_N]; volatile uint64_t perf_nframes;
uint64_t host_ticks(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return (uint64_t)t.tv_sec * 1000000000ull + (uint64_t)t.tv_nsec; }
uint64_t host_filetime(void) { return 0; }
void diag_event(const char *f, ...) { (void)f; }
const char *diag_symbol(uint32_t a, uint32_t *off) { (void)a; if (off) *off = 0; return ""; }
uint32_t galloc(uint32_t n) { (void)n; return 0; }
uint32_t call_guest(Cpu *c, uint32_t fn, int n, const uint32_t *args) { (void)c; (void)fn; (void)n; (void)args; return 0; }
int main(void) {
    G = calloc(1, 0x1000000);
    gil_acquire();
    uint32_t h[200];
    for (int i = 0; i < 200; ++i) { KObj *o; h[i] = knew(K_EVENT, &o); o->signaled = 0; }
    for (int i = 0; i < 200; ++i) threads_set_event_async(h[i]);
    apply_pending_events();
    int sig = 0;
    for (int i = 0; i < 200; ++i) sig += kget(h[i])->signaled;
    printf("signaled=%d of 200, pending left=%d\n", sig, npending);
    int failures = sig != 200 || npending != 0, checks = 0;
    for (int after_queue = 0; after_queue < 2; ++after_queue) {
        uint32_t old = threads_event_new(1, 0);
        uint64_t generation = threads_event_generation(old);
        if (after_queue) threads_set_event_generation_async(old, generation);
        threads_close(old);
        uint32_t replacement = threads_event_new(1, 0);
        ++checks; failures += replacement != old || threads_event_generation(replacement) == generation;
        if (!after_queue) threads_set_event_generation_async(old, generation);
        apply_pending_events();
        ++checks; failures += kget(replacement)->signaled != 0;
        threads_set_event_generation_async(replacement, threads_event_generation(replacement));
        apply_pending_events();
        ++checks; failures += kget(replacement)->signaled != 1;
        threads_close(replacement);
    }
    printf("event generations: checks=%d failures=%d\n", checks, failures);
    return failures ? 1 : 0;
}
