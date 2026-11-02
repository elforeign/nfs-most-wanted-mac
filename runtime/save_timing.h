// Save-task timings are compiled into the runtime, and omitted by isolated shim fixtures.
#pragma once
#include <stdint.h>
#ifdef SAVE_TIMING_ENABLED
extern int diag_on;
unsigned save_timing_enter(const char *stage);
void save_timing_leave(unsigned span, int64_t result);
void save_timing_task_begin(uint32_t task);
void save_timing_task_end(void);
// Each operation has one predicted branch when BOOT_DIAG is off: no clock, TLS or logging work.
#define SAVE_TIME(stage, expression) __extension__ ({ \
    __typeof__(expression) save_result; \
    if (__builtin_expect(diag_on, 0)) { \
        unsigned save_span = save_timing_enter(stage); \
        save_result = (expression); \
        save_timing_leave(save_span, (int64_t)save_result); \
    } else save_result = (expression); \
    save_result; \
})
#define SAVE_VOID(stage, expression) do { \
    if (__builtin_expect(diag_on, 0)) { \
        unsigned save_span = save_timing_enter(stage); \
        expression; \
        save_timing_leave(save_span, 0); \
    } else { expression; } \
} while (0)
#else
static inline void save_timing_task_begin(uint32_t task) { (void)task; }
static inline void save_timing_task_end(void) {}
#define SAVE_TIME(stage, expression) (expression)
#define SAVE_VOID(stage, expression) do { expression; } while (0)
#endif
