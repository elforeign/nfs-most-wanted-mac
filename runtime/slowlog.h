#ifndef NFS_SLOWLOG_H
#define NFS_SLOWLOG_H
#include <stdint.h>
#include "gpuclients.h"

typedef struct {
    GPUClientsSample gpu;
    double time, interval, lum_sum_ms, lum_max_ms, battery_pct;
    uint64_t serial, lum_count, lum_dropped;
    int enabled, thermal, low_power;
    char power[16];
} SlowlogSample;

// Initialized once before frames. The render producer only appends measured waits.
void slowlog_start(void);
void slowlog_lum_wait(uint64_t ns);
// Single consumer: report.m's once-a-second game-thread pass. No locks or allocation.
void slowlog_cached(SlowlogSample *out);
void slowlog_print(FILE *f, const SlowlogSample *s, const char *post_path);
#define SLOWLOG_COLUMNS "slowlog_enabled\tslowlog_sample\tslowlog_dt\tslowlog_age_ms\tpost_path\tlum_wait_sum_ms\tlum_wait_max_ms\tlum_wait_count\tlum_wait_dropped\tpower_source\tbattery_pct\tgpu_pstates\tgpu_clock_status"
#endif
