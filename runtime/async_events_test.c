// async_events_test.c - drives the production threads_set_event_async / apply_pending_events (threads.c, included):
// 200 distinct events are queued from a host thread before the guest applies them (audit2 #10); every one must be signaled.
// Prints how many of the 200 ended up signaled. -DTHREADS_C=\"path\" selects the threads.c under test.
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
    return sig == 200 && npending == 0 ? 0 : 1;
}
