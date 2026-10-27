// sleep_clock_test.c - drives the production Sleep (threads.c, included) with a stubbed clock and nanosleep, for
// sleep_clock_test.py (no game, no real sleeping). Each host_ticks() read advances 0.6 ms, so a Sleep(1) deadline
// passes between two reads. Prints the largest nanosleep request and the number of them.
// -DTHREADS_C=\"path\" selects the threads.c under test (default: this checkout's).
#include <stdio.h>
#include <string.h>
#include <time.h>
static unsigned long long clk;
unsigned long long host_ticks_stub(void) { clk += 600000ull; return clk; }
static unsigned long long max_req, nreq;
static int nanosleep_stub(const struct timespec *ts, struct timespec *rem) {
    (void)rem;
    unsigned long long ns = (unsigned long long)ts->tv_sec * 1000000000ull + (unsigned long long)ts->tv_nsec;
    if (ns > max_req) max_req = ns;
    clk += ns;                                    // the sleep takes as long as asked
    ++nreq;
    return 0;
}
#define nanosleep nanosleep_stub
#define host_ticks host_ticks_stub
#ifndef THREADS_C
#define THREADS_C "threads.c"
#endif
#include THREADS_C
#undef host_ticks
#undef nanosleep
uint8_t *G; int diag_on = 0; int boot_trace; uint32_t last_error; uint64_t diag_idle_ns[IDLE_N]; volatile uint64_t perf_nframes;
uint64_t host_ticks(void) { return host_ticks_stub(); }
uint64_t host_filetime(void) { return 0; }
void diag_event(const char *f, ...) { (void)f; }
const char *diag_symbol(uint32_t a, uint32_t *off) { (void)a; if (off) *off = 0; return ""; }
uint32_t galloc(uint32_t n) { (void)n; return 0; }
uint32_t call_guest(Cpu *c, uint32_t fn, int n, const uint32_t *args) { (void)c; (void)fn; (void)n; (void)args; return 0; }
int main(void) {
    G = calloc(1, 0x1000000);
    Cpu cpu; memset(&cpu, 0, sizeof cpu);
    cpu.esp = 0x800000;
    gil_acquire();
    for (int i = 0; i < 1000; ++i) {
        cpu.esp = 0x800000; M32(cpu.esp + 4) = 1;   // Sleep(1)
        win32_threads(&cpu, "Sleep");
    }
    printf("max_nanosleep_ns=%llu calls=%llu\n", max_req, nreq);
    return 0;
}
