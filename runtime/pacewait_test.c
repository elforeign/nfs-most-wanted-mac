// Offline: clang -std=c11 -Wall -Wextra -Werror runtime/pacewait_test.c -o /tmp/pacewait_test
// Real thread-directed signals interrupt the production absolute wait. The suite also
// mutates its retry loop to a single call (the original behavior), which must fail.
// Measured: fixed 250.007 / 240 ms, 4 waits, 3 aborted, 3 signals, exit 0;
// single-wait control 41.737 / 240 ms, 1 wait, 1 aborted, 3 signals, exit 1.
// Uninterrupted control: 85.098 / 80 ms, exactly 1 wait, 0 aborted.
#include <mach/mach_time.h>
#include <mach/kern_return.h>
#include <pthread.h>
#include <signal.h>
#include <stdio.h>
#include <stdint.h>

static unsigned waits, aborted;
static kern_return_t observed_wait(uint64_t deadline) {
    ++waits;
    kern_return_t result = mach_wait_until(deadline);
    if (result == KERN_ABORTED) ++aborted;
    return result;
}
#define mach_wait_until observed_wait
#include "pacewait.h"
#undef mach_wait_until

static mach_timebase_info_data_t tb;
static uint64_t ms_ticks(unsigned ms) {
    return (uint64_t)ms * 1000000 * tb.denom / tb.numer;
}
static double elapsed_ms(uint64_t start, uint64_t end) {
    return (double)(end - start) * tb.numer / tb.denom / 1e6;
}
static volatile sig_atomic_t signals_seen;
static void signal_handler(int sig) { (void)sig; ++signals_seen; }
typedef struct { pthread_t target; uint64_t start; int error; } Sender;
static void *send_signals(void *arg) {
    Sender *s = arg;
    for (unsigned i = 1; i <= 3; ++i) {
        while (mach_wait_until(s->start + ms_ticks(40 * i)) == KERN_ABORTED) {}
        int error = pthread_kill(s->target, SIGUSR1);
        if (error) s->error = error;
    }
    return NULL;
}

int main(void) {
    if (mach_timebase_info(&tb) != KERN_SUCCESS) return 2;
    struct sigaction action = {0};
    action.sa_handler = signal_handler;
    sigemptyset(&action.sa_mask);
    if (sigaction(SIGUSR1, &action, NULL)) return 2;
    sigset_t mask;
    sigemptyset(&mask);
    sigaddset(&mask, SIGUSR1);
    if (pthread_sigmask(SIG_UNBLOCK, &mask, NULL)) return 2;

    uint64_t start = mach_absolute_time(), deadline = start + ms_ticks(80);
    pacewait_until(deadline);
    uint64_t woke = mach_absolute_time();
    int plain_ok = woke >= deadline && waits == 1 && aborted == 0;
    printf("plain: %.3f / 80 ms, %u waits, %u aborted: %s\n",
           elapsed_ms(start, woke), waits, aborted, plain_ok ? "PASS" : "FAIL");

    waits = aborted = 0;
    Sender sender = {.target = pthread_self(), .start = mach_absolute_time()};
    pthread_t thread;
    if (pthread_create(&thread, NULL, send_signals, &sender)) return 2;
    deadline = sender.start + ms_ticks(240);
    pacewait_until(deadline);
    woke = mach_absolute_time();
    if (pthread_join(thread, NULL)) return 2;
    int interrupted_ok = woke >= deadline && aborted >= 1 &&
                         waits == aborted + 1 && signals_seen == 3 && !sender.error;
    printf("signaled: %.3f / 240 ms, %u waits, %u aborted, %d signals: %s\n",
           elapsed_ms(sender.start, woke), waits, aborted, (int)signals_seen,
           interrupted_ok ? "PASS" : "FAIL");
    printf("PACEWAIT %s\n", plain_ok && interrupted_ok ? "PASS" : "FAIL");
    return plain_ok && interrupted_ok ? 0 : 1;
}
