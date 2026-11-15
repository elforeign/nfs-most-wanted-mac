// Opt-in, game-thread luminance-wait recovery experiment. No per-frame hook.
#include "slowlog_lumab.h"
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <time.h>

#define BLOCK_SECONDS 10.0
#define BLOCKS 6
#define RECOVERY_SECONDS 5.0
#define LABELS 32
typedef struct { double gpu, draws; } RaceRow;
typedef struct { char name[64]; double seconds; } Residency;
typedef struct {
    Residency states[LABELS];
    int count, pstate;
    double mhz, active;
} Clock;
static struct {
    int enabled, running, failed, episode, block, baseline, requested, effective;
    uint32_t epoch, mode;
    FILE *file;
    RaceRow *race;
    double *scratch;
    size_t rows, capacity;
    uint64_t serial, frame0;
    double last_tick, last_time, qualified, slow_first, slow_last;
    unsigned slow_rows;
    double healthy_s, healthy_sum, healthy, frozen;
    double t0, valid, busy, mhz_sum, active_s, recover;
    double last_interval, entry_draws, entry_other;
    int high_count, entry_state, exit_state, entry_low_busy, comparable, numeric;
    int power_mode, thermal, ref_set, ref_thermal, ref_mode;
    double ref_draws, ref_other;
    char power[16], ref_power[16], decoding[96];
    Residency states[LABELS];
    int state_count;
} ab = {.enabled=-1};

static int idle(const char *s) {
    return strcasestr(s, "idle") || strcasestr(s, "off") || strcasestr(s, "down");
}
static int pstate(const char *s) {
    int n = -1, end = 0;
    if (sscanf(s, "P%d%n", &n, &end) == 1 && !s[end] && n >= 0) return n;
    return -1;
}
static Clock clock_read(const GPUClientsSample *s) {
    Clock c = {.pstate=-1, .mhz=NAN};
    double dominant = -1;
    char text[sizeof s->states];
    snprintf(text, sizeof text, "%s", s->states);
    char *save = NULL;
    for (char *t = strtok_r(text, ",", &save); t; t = strtok_r(NULL, ",", &save)) {
        char *colon = strrchr(t, ':');
        if (!colon || c.count == LABELS) continue;
        *colon++ = 0;
        char *end;
        double pct = strtod(colon, &end);
        if (*end || !isfinite(pct) || pct < 0 || pct > 100 || strlen(t) >= sizeof c.states[0].name) continue;
        Residency *r = &c.states[c.count++];
        strcpy(r->name, t); r->seconds = pct / 100;
        if (!idle(t)) {
            c.active += r->seconds;
            if (pct > dominant) { dominant = pct; c.pstate = pstate(t); }
        }
    }
    // Numeric values here were decoded from explicit MHz/GHz labels by gpuclients.
    char *end;
    double n = strtod(s->mhz, &end);
    if (end != s->mhz && !*end && isfinite(n) && n > 0 && !strcmp(s->clock_status, "decoded_mhz")) c.mhz = n;
    return c;
}
static int state(const Clock *c, double healthy) {
    if (c->pstate >= 1) return c->pstate <= 5 ? 1 : c->pstate >= 9 ? 2 : 0;
    if (isfinite(c->mhz) && healthy > 0) return c->mhz <= .70 * healthy ? 1 : c->mhz >= .85 * healthy ? 2 : 0;
    return 0;
}
static int cmp_double(const void *a, const void *b) {
    double x = *(const double *)a, y = *(const double *)b;
    return (x > y) - (x < y);
}
static double median(size_t n) {
    qsort(ab.scratch, n, sizeof *ab.scratch, cmp_double);
    return n & 1 ? ab.scratch[n/2] : (ab.scratch[n/2-1] + ab.scratch[n/2]) / 2;
}
static int slow_gpu(double draws, double gpu) {
    // Causal version of session_report.py: all race rows observed so far,
    // >=20 rows, >=10 peers within 25% draws (otherwise the overall median).
    if (!isfinite(draws) || !isfinite(gpu) || draws <= 0 || gpu <= 0) return 0;
    if (ab.rows == ab.capacity) {
        size_t n = ab.capacity ? ab.capacity * 2 : 256;
        RaceRow *rows = realloc(ab.race, n * sizeof *rows);
        if (!rows) { ab.failed = 1; return 0; }
        ab.race = rows;
        double *scratch = realloc(ab.scratch, n * sizeof *scratch);
        if (!scratch) { ab.failed = 1; return 0; }
        ab.scratch = scratch; ab.capacity = n;
    }
    ab.race[ab.rows++] = (RaceRow){gpu, draws};
    if (ab.rows < 20) return 0;
    size_t peers = 0;
    for (size_t i = 0; i < ab.rows; ++i)
        if (fabs(ab.race[i].draws - draws) <= .25 * draws) ab.scratch[peers++] = ab.race[i].gpu;
    if (peers < 10) {
        peers = ab.rows;
        for (size_t i = 0; i < peers; ++i) ab.scratch[i] = ab.race[i].gpu;
    }
    return gpu > 2 * median(peers);
}
static void reset_qualification(void) {
    ab.qualified = 0; ab.slow_rows = 0; ab.slow_first = ab.slow_last = 0;
}
static int comparable(double a, double b) {
    return isfinite(a) && isfinite(b) && fabs(a-b) <= fmax(10, .25 * fabs(b));
}
static const char *state_name(int s) { return s == 1 ? "slow" : s == 2 ? "recovered" : "unknown"; }
static void begin_block(double now, uint64_t frame, int entry, const SlowlogSample *s, double draws) {
    ab.t0 = now; ab.frame0 = frame;
    ab.valid = ab.busy = ab.mhz_sum = ab.active_s = 0;
    ab.numeric = 1;
    ab.last_interval = now; ab.recover = -1; ab.high_count = 0;
    ab.state_count = 0; ab.decoding[0] = 0;
    ab.entry_state = ab.exit_state = entry;
    ab.entry_low_busy = entry == 1 && s->gpu.dev_util >= 90;
    ab.entry_draws = draws; ab.entry_other = s->gpu.other_ms;
    ab.comparable = isfinite(draws) && draws > 0 && isfinite(s->gpu.other_ms) && s->thermal >= 0;
    if (ab.entry_low_busy && !ab.ref_set && ab.comparable) {
        ab.ref_set = 1; ab.ref_draws = draws; ab.ref_other = s->gpu.other_ms;
        ab.ref_thermal = s->thermal; ab.ref_mode = s->low_power;
        strcpy(ab.ref_power, s->power);
    }
    ab.comparable &= ab.ref_set && fabs(draws - ab.ref_draws) <= .25 * ab.ref_draws &&
        comparable(s->gpu.other_ms, ab.ref_other) && s->thermal == ab.ref_thermal &&
        s->low_power == ab.ref_mode && !strcmp(s->power, ab.ref_power);
    ab.requested = ((ab.episode - 1) + ab.block) & 1;
    render_lum_set_nowait(ab.requested);
    ab.effective = render_lum_snapshot().nowait;
}
static void aggregate(const Clock *c, const SlowlogSample *s, double draws) {
    double start = s->time - s->interval;
    // Whole, distinct, nonoverlapping sample intervals only. Boundary overlaps
    // are discarded instead of attributing a mixed interval to either arm.
    if (start < ab.t0 - 1e-6 || start < ab.last_interval - 1e-6) { ab.high_count = 0; return; }
    double dt = s->interval;
    if (start > ab.last_interval + 1e-6) ab.high_count = 0;
    ab.last_interval = s->time;
    ab.valid += dt; ab.busy += s->gpu.dev_util * dt;
    if (isfinite(c->mhz)) { ab.mhz_sum += c->mhz * c->active * dt; ab.active_s += c->active * dt; }
    else ab.numeric = 0;
    for (int i = 0; i < c->count; ++i) {
        int j = 0;
        while (j < ab.state_count && strcmp(ab.states[j].name, c->states[i].name)) ++j;
        if (j == ab.state_count) {
            if (j == LABELS) { ab.comparable = 0; continue; }
            strcpy(ab.states[j].name, c->states[i].name); ab.states[j].seconds = 0; ++ab.state_count;
        }
        ab.states[j].seconds += c->states[i].seconds * dt;
    }
    if (!ab.decoding[0]) strcpy(ab.decoding, s->gpu.clock_status);
    else if (strcmp(ab.decoding, s->gpu.clock_status)) strcpy(ab.decoding, "mixed_decoding");
    ab.exit_state = state(c, ab.frozen);
    if (ab.exit_state == 2) ++ab.high_count; else ab.high_count = 0;
    if (ab.high_count >= 2 && ab.recover < 0 && s->time - ab.t0 <= RECOVERY_SECONDS + 1e-6)
        ab.recover = s->time - ab.t0;
    ab.comparable &= fabs(draws - ab.entry_draws) <= .25 * ab.entry_draws &&
        comparable(s->gpu.other_ms, ab.entry_other) && s->thermal == ab.thermal;
}
static int end_block(double now, uint64_t frame, uint32_t flow, const char *reason) {
    double dt = now - ab.t0;
    int eligible = !strcmp(reason, "complete") && dt > 0 && ab.valid >= .8 * dt &&
        ab.entry_low_busy && ab.comparable && ab.effective == ab.requested;
    FILE *f = ab.file;
    fprintf(f, "%d\t%d\t%.3f\t%.3f\t%.3f\t%s->%s\t%u\t%d\t%d\t", ab.episode, ab.block+1,
            ab.t0, now, dt, state_name(ab.entry_state), state_name(ab.exit_state), flow, ab.requested, ab.effective);
    if (ab.numeric && ab.active_s > 0) fprintf(f, "%.3f", ab.mhz_sum / ab.active_s); else fputc('-', f);
    int dom = -1;
    for (int i = 0; i < ab.state_count; ++i)
        if (!idle(ab.states[i].name) && (dom < 0 || ab.states[i].seconds > ab.states[dom].seconds)) dom = i;
    fprintf(f, "\t%s\t", dom >= 0 ? ab.states[dom].name : "-");
    if (!ab.state_count) fputc('-', f);
    for (int i = 0; i < ab.state_count; ++i)
        fprintf(f, "%s%s:%.2f", i ? "," : "", ab.states[i].name, ab.valid > 0 ? 100 * ab.states[i].seconds / ab.valid : 0);
    fprintf(f, "\t%.3f\t", dt > 0 && frame >= ab.frame0 ? (frame - ab.frame0) / dt : 0);
    if (ab.valid > 0) fprintf(f, "%.3f", ab.busy / ab.valid); else fputc('-', f);
    fprintf(f, "\t%.3f\t", ab.valid);
    if (ab.recover >= 0) fprintf(f, "%.3f", ab.recover); else fputc('-', f);
    fprintf(f, "\t%d\t%s:%s\n", eligible, reason, ab.decoding[0] ? ab.decoding : "unknown");
    return fflush(f) == 0 && !ferror(f);
}
static void restore(void) {
    render_lum_set_nowait(ab.baseline);
    ab.running = 0; reset_qualification();
}
static int open_log(const char *dir) {
    if (ab.file) return 1;
    char path[1400];
    if (!dir || !*dir || snprintf(path, sizeof path, "%s/lum_ab.tsv", dir) >= (int)sizeof path) return 0;
    ab.file = fopen(path, "w");
    if (!ab.file) return 0;
    fputs("episode\tblock\tt0\tt1\tdt\tstate\tflow\tnowait_requested\tnowait_effective\tclock_mhz_mean\tpstate_dom\tpstates\tfps\tgpu_busy_pct\tvalid_s\trecover_s\teligible\tstatus\n", ab.file);
    return fflush(ab.file) == 0 && !ferror(ab.file);
}
void slowlog_lumab_tick(uint64_t frame, uint32_t flow,
                       double draws, double gpu_ms, int active, int occluded,
                       int x360, const SlowlogSample *s, const char *dir) {
    if (ab.enabled < 0) {
        const char *e = getenv("NFS_SLOWLOG_LUM_AB");
        ab.enabled = e && !strcmp(e, "1");
    }
    if (!ab.enabled || ab.failed) return;
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    double now = ts.tv_sec + ts.tv_nsec / 1e9;
    SlowlogLumSnapshot lum = render_lum_snapshot();
    int fresh = s->serial && s->serial > ab.serial;
    int usable = s->enabled && s->serial && isfinite(s->time) && isfinite(s->interval) &&
        s->interval > 0 && s->interval <= 3 && now >= s->time && now - s->time < 3;
    Clock clock = clock_read(&s->gpu);
    usable &= clock.count > 0 && clock.active > 0 &&
        (!strcmp(s->gpu.clock_status, "decoded_mhz") || !strcmp(s->gpu.clock_status, "decoded_residency")) &&
        (clock.pstate >= 1 || isfinite(clock.mhz)) && isfinite(s->gpu.dev_util) &&
        s->gpu.dev_util >= 0 && s->gpu.dev_util <= 100;
    const char *abort = NULL;
    if (!x360 || !lum.single_player) abort = "abort:path";
    else if (flow != 6) abort = "abort:flow";
    else if (!active || occluded) abort = "abort:visibility";
    else if (!lum.ready || (ab.running && (lum.epoch != ab.epoch || lum.mode != ab.mode))) abort = "abort:epoch";
    else if (ab.last_tick && now - ab.last_tick > 3) abort = "abort:gap";
    else if (!usable || (ab.running && fresh && s->time - ab.last_time > 3)) abort = "abort:evidence";
    else if (s->low_power < 0 || !strcmp(s->power, "unknown") ||
             (ab.running && (s->low_power != ab.power_mode || strcmp(s->power, ab.power)))) abort = "abort:power";
    ab.last_tick = now;
    if (ab.running && abort) {
        ab.exit_state = 0;
        if (!end_block(now, frame, flow, abort)) ab.failed = 1;
        restore();
    }
    if (fresh) { ab.serial = s->serial; ab.last_time = s->time; }
    if (abort) { reset_qualification(); return; }
    int gpu_slow = fresh ? slow_gpu(draws, gpu_ms) : 0;
    if (ab.failed) {
        if (ab.running) { end_block(now, frame, flow, "abort:memory"); restore(); }
        return;
    }
    if (ab.running) {
        if (fresh) aggregate(&clock, s, draws);
        if (now - ab.t0 >= BLOCK_SECONDS) {
            if (!end_block(now, frame, flow, "complete")) { ab.failed = 1; restore(); return; }
            if (++ab.block == BLOCKS) { restore(); return; }
            begin_block(now, frame, state(&clock, ab.frozen), s, draws);
        }
        return;
    }
    if (!fresh || lum.nowait != 0) { reset_qualification(); return; }
    if (ab.epoch != lum.epoch || ab.mode != lum.mode || ab.power_mode != s->low_power || strcmp(ab.power, s->power)) {
        reset_qualification();
        ab.healthy_s = ab.healthy_sum = ab.healthy = 0;
        ab.epoch = lum.epoch; ab.mode = lum.mode; ab.power_mode = s->low_power;
        strcpy(ab.power, s->power);
    }
    if (s->time - s->interval < ab.last_interval - 1e-6) {
        reset_qualification(); return;
    }
    int low = state(&clock, ab.healthy) == 1;
    if (!gpu_slow && !low && isfinite(clock.mhz) && (clock.pstate < 1 || clock.pstate >= 9)) {
        // Freeze a healthy baseline after 20 seconds, before qualifying a trial.
        if (ab.healthy_s < 20) {
            ab.healthy_s += s->interval; ab.healthy_sum += clock.mhz * s->interval;
            if (ab.healthy_s >= 20) ab.healthy = ab.healthy_sum / ab.healthy_s;
        }
    }
    if (gpu_slow) {
        if (!ab.slow_rows || s->time - ab.slow_last >= 6) { ab.slow_rows = 0; ab.slow_first = s->time; }
        ++ab.slow_rows; ab.slow_last = s->time;
    }
    if (!gpu_slow || !low || s->gpu.dev_util < 90 ||
        (ab.qualified && s->time - s->interval > ab.last_interval + 1e-6)) ab.qualified = 0;
    if (gpu_slow && low && s->gpu.dev_util >= 90) ab.qualified += s->interval;
    ab.last_interval = s->time;
    if (ab.qualified < 5 || (ab.slow_rows < 4 && s->time - ab.slow_first < 4)) return;
    // Header must reach disk before any setter call, including a baseline write.
    if (!open_log(dir)) { ab.failed = 1; return; }
    ab.baseline = lum.nowait; ab.epoch = lum.epoch; ab.mode = lum.mode;
    ab.power_mode = s->low_power; ab.thermal = s->thermal; strcpy(ab.power, s->power);
    ab.frozen = ab.healthy; ++ab.episode; ab.block = 0; ab.running = 1;
    begin_block(now, frame, 1, s, draws);
}
