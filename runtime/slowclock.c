// GPU-clock drop evidence for the session log: what perf.tsv's per-second rows cannot show.
//  - cpu_game_ms: CPU time the game thread really used per frame (busy_p50 is wall time minus the named waits,
//    so a wait nobody named looks like work there);
//  - proc_cpu_ms, proc_pcore_pct, proc_ghz: the process's CPU ms per second, the share of its cycles spent on
//    performance cores and its mean core clock (cycles per CPU second);
//  - proc_faults, sys_cpu_pct, mem_pressure: the process's page faults this second, the share of all cores busy
//    (every process), and the kernel's memory pressure level (1 normal, 2 warning, 4 critical): what else the
//    machine was doing while the game thread got slower;
//  - clock_low: the second's largest GPU state is P1-P3;
//  - clock_<n>.tsv: the always-on per-frame ring (host.h PerfFrame) at each entry into P1-P3, so the order of
//    "GPU time per frame rises" and "the first waits for the GPU" is known frame by frame.
// Called once a second on the game thread; nothing runs per frame.
#include <libproc.h>
#include <mach/mach.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/sysctl.h>
#include <time.h>
#include <unistd.h>
#include "host.h"
#include "slowclock.h"

#define SLOWCLOCK_DUMPS 24   // files a session

int slowclock_low(const char *states) {
    char top[16] = "";
    double best = -1;
    for (const char *p = states; p && *p;) {
        const char *colon = strchr(p, ':');
        if (!colon) break;
        char *end;
        double v = strtod(colon + 1, &end);
        if (v > best && (size_t)(colon - p) < sizeof top) {
            best = v;
            memcpy(top, p, (size_t)(colon - p)); top[colon - p] = 0;
        }
        p = *end == ',' ? end + 1 : NULL;
    }
    return !strcmp(top, "P1") || !strcmp(top, "P2") || !strcmp(top, "P3");
}

static double thread_cpu_s(void) {
    struct timespec t;
    return clock_gettime(CLOCK_THREAD_CPUTIME_ID, &t) ? NAN : t.tv_sec + t.tv_nsec / 1e9;
}
static double process_cpu_s(long *faults) {
    struct rusage u;
    if (getrusage(RUSAGE_SELF, &u)) { *faults = -1; return NAN; }
    *faults = u.ru_minflt + u.ru_majflt;
    return u.ru_utime.tv_sec + u.ru_stime.tv_sec + (u.ru_utime.tv_usec + u.ru_stime.tv_usec) / 1e6;
}
// Busy and total scheduler ticks of every core since boot.
static int system_ticks(uint64_t *busy, uint64_t *total) {
    host_cpu_load_info_data_t c;
    mach_msg_type_number_t n = HOST_CPU_LOAD_INFO_COUNT;
    mach_port_t host = mach_host_self();
    kern_return_t result = host_statistics(host, HOST_CPU_LOAD_INFO, (host_info_t)&c, &n);
    mach_port_deallocate(mach_task_self(), host);
    if (result != KERN_SUCCESS) return 0;
    *busy = (uint64_t)c.cpu_ticks[CPU_STATE_USER] + c.cpu_ticks[CPU_STATE_SYSTEM] + c.cpu_ticks[CPU_STATE_NICE];
    *total = *busy + c.cpu_ticks[CPU_STATE_IDLE];
    return 1;
}

// Newest frame first. GPU times are stored by completion order, so a row's gpu_ms lags its frame by the frames
// in flight (one or two): read the trend, not the exact pairing.
static void dump_frames(const char *session_dir, int n, double t, uint64_t frame, const char *states) {
    char path[1500];
    snprintf(path, sizeof path, "%s/clock_%d.tsv", session_dir, n);
    FILE *f = fopen(path, "w");
    if (!f) return;
    fprintf(f, "# GPU clock entered P1-P3 at t=%.1f s, frame %llu, states %s; newest frame first\n", t, (unsigned long long)frame, states);
    fprintf(f, "back\tframe_ms\tbusy_ms\tsleep_ms\tgil_ms\tdrawable_ms\tgpu_wait_ms\tdraws\tticks\tgpu_ms\n");
    uint64_t head = __atomic_load_n(&perf_nframes, __ATOMIC_ACQUIRE);
    uint64_t g = __atomic_load_n(&perf_gpu_serial, __ATOMIC_ACQUIRE);
    for (uint64_t i = 0; i < PERF_RING - 1 && i < head; ++i) {
        const PerfFrame *p = &perf_frames[(head - 1 - i) % PERF_RING];
        fprintf(f, "%llu\t%.2f\t%.2f\t%.2f\t%.2f\t%.2f\t%.2f\t%u\t%u\t", (unsigned long long)i, p->frame_ms, p->busy_ms,
                p->idle_ms[IDLE_SLEEP], p->idle_ms[IDLE_GIL], p->idle_ms[IDLE_DRAWABLE], p->idle_ms[IDLE_GPU], p->draws, p->ticks);
        if (i < g) fprintf(f, "%.2f\n", perf_gpu_ms[(g - i) % PERF_RING]); else fputs("-\n", f);
    }
    fclose(f);
}

void slowclock_second(FILE *perf, int enabled, uint64_t frame, const char *states, int fresh,
                      const char *session_dir, double t, void (*note)(const char *fmt, ...)) {
    static double thread_before = NAN, process_before = NAN, low_since;
    static uint64_t frame_before, cycles_before, pcycles_before, busy_before, total_before;
    static long faults_before = -1;
    static int low, dumps;
    if (!enabled) { fputs("\t-\t-\t-\t-\t-\t-\t-\t-", perf); return; }
    long faults;
    double thread_now = thread_cpu_s(), process_now = process_cpu_s(&faults);
    struct rusage_info_v6 ri;
    int have = !proc_pid_rusage(getpid(), RUSAGE_INFO_V6, (rusage_info_t *)&ri);
    uint64_t frames = frame - frame_before;
    double cpu = process_now - process_before;
    if (isfinite(thread_before) && isfinite(thread_now) && frames) fprintf(perf, "\t%.2f", (thread_now - thread_before) * 1000 / frames);
    else fputs("\t-", perf);
    if (isfinite(cpu)) fprintf(perf, "\t%.0f", cpu * 1000); else fputs("\t-", perf);
    if (have && cycles_before && ri.ri_cycles > cycles_before && isfinite(cpu) && cpu > 0)
        fprintf(perf, "\t%.0f\t%.2f", 100.0 * (ri.ri_pcycles - pcycles_before) / (ri.ri_cycles - cycles_before),
                (ri.ri_cycles - cycles_before) / cpu / 1e9);
    else fputs("\t-\t-", perf);
    if (faults >= 0 && faults_before >= 0) fprintf(perf, "\t%ld", faults - faults_before); else fputs("\t-", perf);
    faults_before = faults;
    uint64_t busy = 0, total = 0;
    int ticks = system_ticks(&busy, &total);
    if (ticks && total_before && total > total_before) fprintf(perf, "\t%.0f", 100.0 * (busy - busy_before) / (total - total_before));
    else fputs("\t-", perf);
    if (ticks) { busy_before = busy; total_before = total; }
    int pressure = 0;
    size_t size = sizeof pressure;
    if (!sysctlbyname("kern.memorystatus_vm_pressure_level", &pressure, &size, NULL, 0)) fprintf(perf, "\t%d", pressure);
    else fputs("\t-", perf);
    thread_before = thread_now; process_before = process_now; frame_before = frame;
    if (have) { cycles_before = ri.ri_cycles; pcycles_before = ri.ri_pcycles; }
    if (fresh) {
        int now_low = slowclock_low(states);
        if (now_low && !low) {
            low_since = t;
            if (dumps < SLOWCLOCK_DUMPS && session_dir && *session_dir) {
                dump_frames(session_dir, ++dumps, t, frame, states);
                note("gpu clock: entered P1-P3 (%s); the frames before it are in clock_%d.tsv", states, dumps);
            } else note("gpu clock: entered P1-P3 (%s)", states);
        } else if (!now_low && low) note("gpu clock: left P1-P3 after %.0f s (%s)", t - low_since, states);
        low = now_low;
    }
    fprintf(perf, "\t%d", low);
}
