#ifndef NFS_SLOWLOG_LUMAB_H
#define NFS_SLOWLOG_LUMAB_H
#include "slowlog.h"

// Game thread only. The setter changes the getter's cached scalar, not exposure.
typedef struct {
    int nowait, ready, single_player;
    uint32_t epoch, mode;
} SlowlogLumSnapshot;
void render_lum_set_nowait(int on);
SlowlogLumSnapshot render_lum_snapshot(void);

// Called only by the existing once-per-second report pass. Uses the sampler's
// CLOCK_MONOTONIC domain, which can differ from the frame timer after sleep.
void slowlog_lumab_tick(uint64_t frame, uint32_t flow,
                       double draws, double gpu_ms, int active, int occluded,
                       int x360, const SlowlogSample *sample, const char *session_dir);
#endif
