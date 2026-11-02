// BOOT_DIAG save timing: fixed in-memory spans; output only after the entire save task returns.
#include "save_timing.h"
#include "host.h"
#include <errno.h>
#include <inttypes.h>
#include <mach/mach_time.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <string.h>

#define SAVE_SPANS 2048
#define NO_SPAN UINT32_MAX
typedef struct {
    const char *stage;
    uint64_t begin, end;
    int64_t result;
    int error;
    unsigned parent;
} SaveSpan;
static _Thread_local struct {
    SaveSpan spans[SAVE_SPANS];
    unsigned count, dropped, current;
    uint32_t task;
    uint64_t begin, end, frame, thread;
    int active;
} timing;
static atomic_uint sequence;

unsigned save_timing_enter(const char *stage) {
    int err = errno;
    unsigned span = NO_SPAN;
    if (timing.active) {
        if (timing.count == SAVE_SPANS) ++timing.dropped;
        else {
            span = timing.count++;
            timing.spans[span] = (SaveSpan){.stage=stage, .parent=timing.current};
            timing.current = span;
            timing.spans[span].begin = mach_absolute_time();
        }
    }
    errno = err;
    return span;
}
void save_timing_leave(unsigned span, int64_t result) {
    int err = errno;
    if (span != NO_SPAN) {
        timing.spans[span].end = mach_absolute_time();
        timing.spans[span].result = result;
        timing.spans[span].error = err;
        timing.current = timing.spans[span].parent;
    }
    errno = err;
}
void save_timing_task_begin(uint32_t task) {
    int err = errno;
    timing.count = timing.dropped = 0;
    timing.current = NO_SPAN;
    timing.task = task;
    timing.frame = perf_nframes;
    pthread_threadid_np(NULL, &timing.thread);
    timing.active = 1;
    timing.begin = mach_absolute_time();
    errno = err;
}
void save_timing_task_end(void) {
    int err = errno;
    timing.end = mach_absolute_time();
    timing.active = 0; // No diagnostic writes can be captured as save I/O.
    mach_timebase_info_data_t tb;
    mach_timebase_info(&tb);
    double ms = (double)tb.numer / tb.denom / 1e6;
    unsigned job = atomic_fetch_add(&sequence, 1) + 1;
    char path[1200];
    if (diag_dir && snprintf(path, sizeof path, "%s/save_%06u.txt", diag_dir, job) < (int)sizeof path) {
        FILE *f = fopen(path, "w");
        if (f) {
            fprintf(f, "save job=%u task=%08x frame=%" PRIu64 " thread=%" PRIu64
                    " total_ms=%.6f spans=%u dropped=%u clock=mach_absolute_time\n",
                    job, timing.task, timing.frame, timing.thread,
                    (timing.end-timing.begin)*ms, timing.count, timing.dropped);
            for (unsigned i=0; i<timing.count; ++i) {
                SaveSpan *s = &timing.spans[i];
                fprintf(f, "stage=%s id=%u parent=%d start_ms=%.6f end_ms=%.6f duration_ms=%.6f result=%" PRId64 " errno=%d\n",
                        s->stage, i, s->parent==NO_SPAN ? -1 : (int)s->parent,
                        (s->begin-timing.begin)*ms, (s->end-timing.begin)*ms,
                        (s->end-s->begin)*ms, s->result, s->error);
            }
            fclose(f);
        }
    }
    errno = err;
}
