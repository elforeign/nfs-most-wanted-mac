// report.m - problem reports (F10 menu > Diagnostics > Report a problem) and the always-on ring they draw on.
//
// Always on, in normal play too (a few stores per frame, one small computation a second): a ring of the last 3 minutes
// of per-second stats (fps, frame time p50/p99/max, game-thread busy time, GPU time, mix RMS per channel, game-flow
// state) and a ring of the last 512 notable events (report_note: game-flow changes, stalls, audio output and device
// changes, underruns, setting changes, menu use, hangs and crashes). So a report always has the last minute, with
// or without BOOT_DIAG.
//
// A report is a folder, <userdata>/reports/<YYYYMMDD-HHMMSS>/ (a "-2" suffix if two land in one second):
//   report.txt     the summary, first: when, category, the user's note, why (user / crash / hang), build, game state,
//                  display, render settings, audio output and device, controller, the last minute in numbers,
//                  settings that differ from their defaults, and this file list
//   screenshot.png the game frame when the menu opened (user reports; none for crash/hang reports)
//   stats.txt      the per-second ring, oldest first, one line a second with a header
//   events.txt     the notable-events ring, then the last 400 lines of the diagnostic event ring
//   log.txt        the end (256 KB) of this run's log (stderr: NFSMW.log in the app, the run script's file)
//   settings.txt   every setting: id, value, source (default / app.txt / env NFS_...)
//   crash.txt      crash and hang reports only: the diagnostic report (backtraces, guest stacks, events)
// Crash and hang reports are written by themselves (category Crash-freeze): diag.c diag_report calls report_auto for
// crash.txt and hang_N.txt. Without BOOT_DIAG a watchdog thread here reports a hang (no frame for 15 s while the game
// isn't idle on purpose), at most 3 times a run.
// The session log (always on, no BOOT_DIAG): a folder per run, <log dir>/sessions/<YYYYMMDD-HHMMSS>/ (under BOOT_USERDATA,
// the scratch profile, when that is set), the newest 20 kept. session.txt: the setup, then every note as it happens, a
// line per hitch (a frame over 1.5x the display interval, attributed: shaders compiled / draws deferred, game-flow
// change, menu, audio, sim catch-up), and a SUMMARY per game-flow state every 60 s and at exit. perf.tsv: a line a
// second for the whole run. runtime/session_report.py reads it. Per frame this adds a few loads, a few counter
// increments and, for a hitch, a copy into a small ring; every write to disk is in the once-a-second pass or a note.
// "Full diagnostic logging" (Diagnostics, next launch) starts the next run as BOOT_DIAG=<userdata>/logs/<timestamp>.
#import <Cocoa/Cocoa.h>
#import <CoreAudio/CoreAudio.h>
#import <GameController/GameController.h>
#include <fcntl.h>
#include <math.h>
#include <pthread.h>
#include <stdarg.h>
#include <sys/stat.h>
#include <dirent.h>
#include <IOKit/ps/IOPowerSources.h>
#include <sys/sysctl.h>
#include <time.h>
#include <unistd.h>

#include "d3d9_internal.h"
#include "settings.h"
#include "gpuclients.h"

#define GAMEFLOW_STATE 0x00925E90u   // 3 front end, 6 racing

#ifndef PORT_BUILD_ID
#define PORT_BUILD_ID "unknown"
#endif
const char *port_build_id(void) { return PORT_BUILD_ID; }
extern const char speed_exe_fingerprint[];   // the code fingerprint the game was translated from (recomp/exe_fingerprint.py)
int dsound_output_info(const char **mode, int *live, uint64_t *underruns);
int dsound_mix_levels(int reader, double rms[6], float peak[6], uint64_t clipped[6], float dip[6], uint64_t *limited);
uint64_t diag_current_frame(void);
uint64_t diag_last_frame_ns(void);
int diag_is_idle(void);
void diag_ring_dump(FILE *f, int last);
int user32_display_hz(void);
int render_msaa_samples(void);
uint64_t diag_rss_bytes(void);
int diag_car_state(float pos[3], float *speed);
void render_frame_counters(uint64_t *deferred, int *nshader, int *npipe);
int pacing_sim_hz(void);
int portmenu_is_open(void);
static void session_note(const char *line, int audio);

// ---------------- notable events ----------------
#define NOTES 512
#define NOTE_LEN 176
static char notes[NOTES][NOTE_LEN];
static volatile uint32_t notes_head;
static uint64_t t_start;                 // report_init
static double uptime(void) { return (host_ticks() - t_start) / 1e9; }
void report_note(const char *fmt, ...) {
    uint32_t i = __atomic_fetch_add(&notes_head, 1, __ATOMIC_RELAXED) % NOTES;
    int n = snprintf(notes[i], NOTE_LEN, "%9.2f s  f%-7llu ", uptime(), (unsigned long long)diag_current_frame());
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(notes[i] + n, NOTE_LEN - (size_t)n, fmt, ap);
    va_end(ap);
    session_note(notes[i], !strncmp(fmt, "audio:", 6));
}

// ---------------- the per-second ring (game thread) ----------------
typedef struct { double t; uint64_t frame; float fps, p50, p99, max, busy, gpu; float rms[6]; int have_rms; uint32_t flow; } Sec;
#define SECS 180
static Sec secs[SECS];
static uint64_t nsecs;
// Cached on the game thread every few seconds, so a report from the hang watchdog or a crash handler needs no
// Cocoa / GameController calls.
static char sys_display[160] = "?", sys_edr[128] = "?", sys_presentation[256] = "?", sys_pad[128] = "none", sys_device[96] = "?";

static int cmp_f(const void *a, const void *b) { float x = *(const float *)a, y = *(const float *)b; return (x > y) - (x < y); }
static AudioDeviceID default_device(void) {
    AudioObjectPropertyAddress a = {kAudioHardwarePropertyDefaultOutputDevice, kAudioObjectPropertyScopeGlobal, kAudioObjectPropertyElementMain};
    AudioDeviceID dev = 0;
    UInt32 sz = sizeof dev;
    return AudioObjectGetPropertyData(kAudioObjectSystemObject, &a, 0, NULL, &sz, &dev) ? 0 : dev;
}
static void device_name(AudioDeviceID dev, char *out, size_t n) {
    snprintf(out, n, "?");
    AudioObjectPropertyAddress a = {kAudioObjectPropertyName, kAudioObjectPropertyScopeGlobal, kAudioObjectPropertyElementMain};
    CFStringRef name = NULL;
    UInt32 sz = sizeof name;
    if (!dev || AudioObjectGetPropertyData(dev, &a, 0, NULL, &sz, &name) || !name) return;
    CFStringGetCString(name, out, (CFIndex)n, kCFStringEncodingUTF8);
    CFRelease(name);
}
const char *report_audio_device(void) { return sys_device; }

static float budget_ms = 25;
static void refresh_power_thermal(void);
static void cache_system(void) {
    @autoreleasepool {
        NSScreen *s = layer && [layer.delegate isKindOfClass:NSView.class] ? ((NSView *)layer.delegate).window.screen : NSScreen.mainScreen;
        if (!s) s = NSScreen.mainScreen;
        int pw = 0, ph = 0;
        void user32_display_pixels(int *w, int *h);
        user32_display_pixels(&pw, &ph);
        char disp[sizeof sys_display];
        snprintf(disp, sizeof disp, "%s, %dx%d pixels, %d Hz, backing scale %.1f",
                 s.localizedName.UTF8String ?: "?", pw, ph, user32_display_hz(), s.backingScaleFactor);
        if (strcmp(disp, sys_display)) {
            int first = !strcmp(sys_display, "?");
            snprintf(sys_display, sizeof sys_display, "%s", disp);
            if (!first) report_note("display: %s", disp);
        }
        // Headroom describes what EDR content can use, not the display link's HDR
        // signal mode. The game's BGRA8 sRGB layer still outputs SDR either way.
        char edr[sizeof sys_edr];
        snprintf(edr, sizeof edr, "EDR unavailable; output=SDR sRGB; HDR transport=unknown");
        if (s) {
            if (@available(macOS 10.15, *))
                snprintf(edr, sizeof edr, "EDR current=%.3f potential=%.3f reference=%.3f; output=SDR sRGB; HDR transport=unknown",
                         s.maximumExtendedDynamicRangeColorComponentValue, s.maximumPotentialExtendedDynamicRangeColorComponentValue,
                         s.maximumReferenceExtendedDynamicRangeColorComponentValue);
        }
        if (strcmp(edr, sys_edr)) {
            int first = !strcmp(sys_edr, "?");
            snprintf(sys_edr, sizeof sys_edr, "%s", edr);
            if (!first) report_note("display: %s", edr);
        }
        RenderPresentation ps;
        render_presentation_stats(&ps);
        char present[sizeof sys_presentation];
        snprintf(present, sizeof present,
                 "refresh min/max/granularity=%.3f/%.3f/%.3f ms, sync=%d, VRR range=%d native_fullscreen=%d; adaptive requested/applicable=%d/%d",
                 ps.min_ms, ps.max_ms, ps.granularity_ms, ps.display_sync, ps.vrr_available, ps.native_fullscreen,
                 ps.vrr_requested, ps.adaptive_scheduling);
        if (strcmp(present, sys_presentation)) {
            int first = !strcmp(sys_presentation, "?");
            snprintf(sys_presentation, sizeof sys_presentation, "%s", present);
            if (!first) report_note("display: %s", present);
        }
        int hz = user32_display_hz(), sim = pacing_sim_hz();
        if (sim > 0 && sim < hz) hz = sim;
        budget_ms = 1500.0f / (float)(hz > 0 ? hz : 60);
        refresh_power_thermal();
        snprintf(sys_pad, sizeof sys_pad, "none");
        for (GCController *k in GCController.controllers)
            if (k.extendedGamepad) {
                snprintf(sys_pad, sizeof sys_pad, "%s (%s)%s", k.vendorName.UTF8String ?: "controller", k.productCategory.UTF8String ?: "?",
                         getenv("BOOT_PAD") ? ", BOOT_PAD simulated" : "");
                break;
            }
        if (!strcmp(sys_pad, "none") && getenv("BOOT_PAD")) snprintf(sys_pad, sizeof sys_pad, "BOOT_PAD simulated");
    }
}

// ---------------- the session log ----------------
static char sys_power[40] = "?", sys_thermal[16] = "?";
static int sys_thermal_n = -1, sys_lowpower = -1;   // perf.tsv thermal (0 nominal .. 3 critical) and low_power columns, refreshed with sys_thermal
// budget_ms: 1.5x the frame interval (the display's, or the sim rate when that is lower), set by cache_system
static void print_render(FILE *f) {
    fprintf(f, "back buffer %ux%u, MSAA %dx, post %s, shadows %u (map %d), car paint %s", dev.pp[0], dev.pp[1],
            render_msaa_samples(), settings_get("graphics.postfx") == 1 ? "PC" : "X360", M32(0x00901830u), x360_shadow_map_size(),
            settings_get("graphics.car_paint") == 1 ? "PC" : "X360");
}
static int print_nondefault(FILE *f) {
    char vbuf[96];
    int any = 0;
    for (int i = 0; i < settings_count(); ++i) {
        Setting *s = settings_at(i);
        if (s->type != SET_TOGGLE && s->type != SET_CHOICE && s->type != SET_SLIDER) continue;
        int v = s->get ? s->get() : s->value;
        if (v == s->def && !s->locked) continue;
        fprintf(f, "  %-28s %s (%s%s%s)\n", s->id, settings_value_name(s, v, vbuf, sizeof vbuf), settings_source(s), s->locked ? "=" : "",
                s->locked ? s->env_text : "");
        any = 1;
    }
    return any;
}
static void refresh_power_thermal(void) {
    static const char *tn[] = {"nominal", "fair", "serious", "critical"};
    @autoreleasepool {
        NSInteger t = NSProcessInfo.processInfo.thermalState;
        const char *tv = t >= 0 && t < 4 ? tn[t] : "?";
        sys_thermal_n = t >= 0 && t < 4 ? (int)t : -1;
        sys_lowpower = NSProcessInfo.processInfo.lowPowerModeEnabled ? 1 : 0;
        if (strcmp(tv, sys_thermal)) {
            char old[16];
            snprintf(old, sizeof old, "%s", sys_thermal);
            snprintf(sys_thermal, sizeof sys_thermal, "%s", tv);
            if (strcmp(old, "?")) report_note("thermal: %s -> %s", old, tv);
        }
        CFTypeRef info = IOPSCopyPowerSourcesInfo();
        CFStringRef src = info ? IOPSGetProvidingPowerSourceType(info) : NULL;
        char pv[40] = "?";
        if (src) CFStringGetCString(src, pv, sizeof pv, kCFStringEncodingUTF8);
        if (info) CFRelease(info);
        if (strcmp(pv, sys_power)) {
            char old[40];
            snprintf(old, sizeof old, "%s", sys_power);
            snprintf(sys_power, sizeof sys_power, "%s", pv);
            if (strcmp(old, "?")) report_note("power: %s -> %s", old, pv);
        }
    }
}

#define KEEP_SESSIONS 20
#define HB 800                           // histogram buckets of 0.25 ms (200 ms and over in the last)
enum { ST_OTHER, ST_FRONT, ST_LOADING, ST_RACE, ST_N };
static const char *st_name[ST_N] = {"starting", "frontend", "loading", "race"};
enum { C_COMPILE, C_FLOW, C_MENU, C_AUDIO, C_CATCHUP, C_OTHER, C_N };
static const char *c_name[C_N] = {"compile", "flow", "menu", "audio", "catchup", "unknown"};
typedef struct {
    uint64_t frames, over, hitches, gframes;
    uint32_t hf[HB], hb[HB], hg[HB], cause[C_N];
    float maxf;
} StateAcc;
static StateAcc sacc[ST_N];
static FILE *sess_f, *perf_f;
static char sess_dir[1200];
const char *report_session_dir(void) { return sess_dir; }
static int state_of(uint32_t flow) { return flow == 3 ? ST_FRONT : flow == 4 ? ST_LOADING : flow == 6 ? ST_RACE : ST_OTHER; }
static int bucket(float ms) { int b = (int)(ms * 4); return b < 0 ? 0 : b >= HB ? HB - 1 : b; }
static float pct(const uint32_t *h, uint64_t total, double q) {
    if (!total) return 0;
    uint64_t want = (uint64_t)(total * q), acc = 0;
    for (int i = 0; i < HB; ++i) { acc += h[i]; if (acc > want) return (i + 1) / 4.0f; }
    return HB / 4.0f;
}

typedef struct {
    double t; uint64_t frame, head;
    float ms, busy, idle[IDLE_N], pos[3];
    uint32_t ticks, flow, causes;
    int dsh, dpipe, car; uint64_t ddef;
} Hitch;
#define HITCHES 64
static Hitch hitches[HITCHES];
static uint64_t hitch_n, hitch_done, hitch_dropped;
static volatile int audio_ev;

static void rm_session(const char *root, const char *name) {
    char d[1400], f[1700];
    snprintf(d, sizeof d, "%s/%s", root, name);
    DIR *dir = opendir(d);
    if (!dir) return;
    struct dirent *e;
    while ((e = readdir(dir))) {
        if (e->d_name[0] == '.') continue;
        snprintf(f, sizeof f, "%s/%s", d, e->d_name);
        unlink(f);
    }
    closedir(dir);
    rmdir(d);
}
static void session_open(void) {
    char root[1100], stamp[32];
    snprintf(root, sizeof root, "%s/sessions", getenv("BOOT_USERDATA") ? userdata_dir() : log_dir());
    mkdir(root, 0755);
    time_t now = time(NULL);
    struct tm tm;
    localtime_r(&now, &tm);
    strftime(stamp, sizeof stamp, "%Y%m%d-%H%M%S", &tm);
    snprintf(sess_dir, sizeof sess_dir, "%s/%s", root, stamp);
    for (int k = 2; mkdir(sess_dir, 0755) != 0 && k < 10; ++k) snprintf(sess_dir, sizeof sess_dir, "%s/%s-%d", root, stamp, k);
    char path[1400];
    snprintf(path, sizeof path, "%s/session.txt", sess_dir);
    sess_f = fopen(path, "w");
    snprintf(path, sizeof path, "%s/perf.tsv", sess_dir);
    perf_f = fopen(path, "w");
    if (perf_f)
        fprintf(perf_f, "t\tframe\tstate\tfps\tp50\tp99\tmax\tbusy_p50\tbusy_p99\tidle_sleep\tidle_gil\tidle_drawable\tidle_gpu\tgpu_mean\tgpu_p99\t"
                        "draws\tticks\tover\trss_mb\tx\ty\tz\tspeed\tmenu\tframe_limit\tpresent_min_ms\trefresh_ms\t"
                        "present_8_3\tpresent_16_7\tpresent_25\tpresent_other\tpresent_dropped\tvrr_requested\tadaptive_scheduling\t"
                        "rms_fl\trms_fr\trms_c\trms_lfe\trms_sl\trms_sr\tpeak_fl\tpeak_fr\tpeak_c\tpeak_lfe\tpeak_sl\tpeak_sr\tclip\tlimit_samples\t"
                        "dip_fl\tdip_fr\tdip_c\tdip_lfe\tdip_sl\tdip_sr\t"
                        "thermal\tlow_power\tapp_active\toccluded\tfullscreen\tgpu_wall\t"
                        "gpu_dev_util\tgpu_self_ms\tgpu_other_ms\tgpu_top\tgpu_mhz\n");
    if (sess_f) {
        char when[64];
        strftime(when, sizeof when, "%Y-%m-%d %H:%M:%S %Z", &tm);
        fprintf(sess_f, "# NFSMW port session log: %s, build %s\n", when, port_build_id());
        fflush(sess_f);
    }
    // keep the newest KEEP_SESSIONS (names sort by time)
    DIR *dir = opendir(root);
    if (!dir) return;
    static char names[64][40];
    int n = 0;
    struct dirent *e;
    while ((e = readdir(dir)) && n < 64)
        if (e->d_name[0] >= '0' && e->d_name[0] <= '9' && strlen(e->d_name) < 40) snprintf(names[n++], 40, "%s", e->d_name);
    closedir(dir);
    for (int i = 1; i < n; ++i)                                   // insertion sort, oldest first
        for (int j = i; j > 0 && strcmp(names[j - 1], names[j]) > 0; --j) { char t[40]; memcpy(t, names[j], 40); memcpy(names[j], names[j - 1], 40); memcpy(names[j - 1], t, 40); }
    for (int i = 0; i < n - KEEP_SESSIONS; ++i) rm_session(root, names[i]);
}
// report_note's feed: every note also lands in session.txt (flushed; notes are rare).
static void session_note(const char *line, int audio) {
    if (audio) audio_ev = 1;
    if (!sess_f) return;
    char buf[NOTE_LEN];
    snprintf(buf, sizeof buf, "%s", line);
    fprintf(sess_f, "%s\n", buf);
    fflush(sess_f);
}
static void session_header(void) {
    if (!sess_f) return;
    char model[64] = "?", chip[96] = "?";
    size_t n = sizeof model;
    sysctlbyname("hw.model", model, &n, NULL, 0);
    n = sizeof chip;
    sysctlbyname("machdep.cpu.brand_string", chip, &n, NULL, 0);
    fprintf(sess_f, "setup: build %s (speed.exe code %.12s)\n", port_build_id(), speed_exe_fingerprint);
    fprintf(sess_f, "setup: macOS %s, %s, %s\n", NSProcessInfo.processInfo.operatingSystemVersionString.UTF8String ?: "?", model, chip);
    fprintf(sess_f, "setup: display %s\n", sys_display);
    fprintf(sess_f, "setup: display %s\n", sys_edr);
    fprintf(sess_f, "setup: display %s\n", sys_presentation);
    RenderPresentation ps;
    render_presentation_stats(&ps);
    fprintf(sess_f, "setup: presentation limit=%d (0=Off), minimum=%.3f ms; presentedTime buckets 8.3/16.7/25 ms +/-0.75, other, dropped\n",
            ps.limit, ps.duration_ms);
    fprintf(sess_f, "setup: render ");
    print_render(sess_f);
    fprintf(sess_f, "\nsetup: power %s, thermal %s, frame budget %.2f ms (1.5x the interval)\n", sys_power, sys_thermal, budget_ms);
    fprintf(sess_f, "setup: settings that differ from their defaults or are set by a switch:\n");
    if (!print_nondefault(sess_f)) fprintf(sess_f, "  (none)\n");
    fflush(sess_f);
}
static void session_summary(void) {
    if (!sess_f) return;
    double t = uptime();
    for (int i = 0; i < ST_N; ++i) {
        const StateAcc *a = &sacc[i];
        if (!a->frames) continue;
        char ln[640];
        size_t k = (size_t)snprintf(ln, sizeof ln, "SUMMARY t=%.0f state=%s frames=%llu p50=%.2f p95=%.2f p99=%.2f max=%.1f over=%.2f%% hitches=%llu causes=", t, st_name[i],
                (unsigned long long)a->frames, pct(a->hf, a->frames, .5), pct(a->hf, a->frames, .95), pct(a->hf, a->frames, .99), a->maxf,
                100.0 * a->over / a->frames, (unsigned long long)a->hitches);
        for (int c = 0; c < C_N && k < sizeof ln; ++c) k += (size_t)snprintf(ln + k, sizeof ln - k, "%s%s:%u", c ? "," : "", c_name[c], a->cause[c]);
        if (k < sizeof ln)
            snprintf(ln + k, sizeof ln - k, " busy_p50=%.2f busy_p99=%.2f gpu_p50=%.2f gpu_p99=%.2f\n", pct(a->hb, a->frames, .5), pct(a->hb, a->frames, .99),
                     pct(a->hg, a->gframes, .5), pct(a->hg, a->gframes, .99));
        fputs(ln, sess_f);
    }
    fflush(sess_f);
    if (perf_f) fflush(perf_f);
}
// The hitches queued by report_frame, written in the once-a-second pass: at most 5 lines a second, then a count.
static void session_flush_hitches(uint64_t head, uint64_t gser) {
    if (!sess_f) return;
    int written = 0;
    uint64_t skipped = hitch_dropped;
    hitch_dropped = 0;
    for (; hitch_done < hitch_n; ++hitch_done) {
        const Hitch *h = &hitches[hitch_done % HITCHES];
        if (hitch_n - hitch_done > HITCHES) { ++skipped; continue; }     // overwritten before it was written
        if (written >= 5) { ++skipped; continue; }
        ++written;
        // the frame's GPU time: the render serial counts like the frame records, so it is the frame's records back from the
        // newest (the completion lags a frame or two: the largest of the four around it)
        float gpu = -1;
        uint64_t back = head - h->head;
        for (int k = -2; k <= 1; ++k) {
            int64_t sr = (int64_t)gser - (int64_t)back + k;
            if (sr > 0 && (uint64_t)sr <= gser && gser - (uint64_t)sr < PERF_RING && perf_gpu_ms[(uint64_t)sr % PERF_RING] > gpu) gpu = perf_gpu_ms[(uint64_t)sr % PERF_RING];
        }
        char ln[640];                       // one line, one fputs: a note from another thread can't split it
        size_t k = (size_t)snprintf(ln, sizeof ln, "%9.2f s  f%-7llu HITCH ms=%.1f busy=%.1f sleep=%.1f gil=%.1f drawable=%.1f gpuwait=%.1f gpu=%.1f ticks=%u state=%s flow=%u causes=",
                h->t, (unsigned long long)h->frame, h->ms, h->busy, h->idle[IDLE_SLEEP], h->idle[IDLE_GIL], h->idle[IDLE_DRAWABLE], h->idle[IDLE_GPU], gpu,
                h->ticks, st_name[state_of(h->flow)], h->flow);
        int any = 0;
        for (int c = 0; c < C_N - 1 && k < sizeof ln; ++c) if (h->causes >> c & 1) k += (size_t)snprintf(ln + k, sizeof ln - k, "%s%s", any++ ? "," : "", c_name[c]);
        if (!any && k < sizeof ln) k += (size_t)snprintf(ln + k, sizeof ln - k, "unknown");
        if (k < sizeof ln)
            snprintf(ln + k, sizeof ln - k, " shaders=+%d pipelines=+%d deferred=+%llu pos=%.0f,%.0f,%.0f\n", h->dsh, h->dpipe, (unsigned long long)h->ddef,
                     h->pos[0], h->pos[1], h->pos[2]);
        fputs(ln, sess_f);
    }
    if (skipped) fprintf(sess_f, "%9.2f s  (%llu more hitches not listed)\n", uptime(), (unsigned long long)skipped);
    if (written || skipped) fflush(sess_f);
}
static void session_exit(void) { session_summary(); }

// diag.c diag_frame, every frame, always: once a second a stats line into the ring; stalls and audio changes as notes.
void report_frame(uint64_t frame, uint32_t flow) {
    uint64_t head = __atomic_load_n(&perf_nframes, __ATOMIC_ACQUIRE);
    static uint32_t prev_causes, last_flow = ~0u;
    static uint64_t def0, last_def;
    static int sh0, pipe0;
    static float ref_p50;                // the last second's median frame time: a hitch must also stand out from it (a run that is
                                         // over budget all the time, GPU-bound, would list every frame)
    if (head) {
        const PerfFrame *p = &perf_frames[(head - 1) % PERF_RING];
        if (p->frame_ms > 500 && frame > 600 && !diag_is_idle()) report_note("stall: a frame took %.0f ms", p->frame_ms);
        // the session log's per-frame part: counters only; a hitch is copied into a ring that the once-a-second pass writes
        StateAcc *a = &sacc[state_of(flow)];
        ++a->frames;
        ++a->hf[bucket(p->frame_ms)];
        ++a->hb[bucket(p->busy_ms)];
        if (p->frame_ms > a->maxf) a->maxf = p->frame_ms;
        int over = p->frame_ms > budget_ms;
        a->over += over;
        uint64_t def;
        int sh, pipe;
        render_frame_counters(&def, &sh, &pipe);
        uint32_t causes = (flow != last_flow && last_flow != ~0u ? 1u << C_FLOW : 0) | (def != last_def || sh != sh0 || pipe != pipe0 ? 1u << C_COMPILE : 0) |
                          (portmenu_is_open() ? 1u << C_MENU : 0) | (__atomic_exchange_n(&audio_ev, 0, __ATOMIC_RELAXED) ? 1u << C_AUDIO : 0) |
                          (p->ticks > 1 ? 1u << C_CATCHUP : 0);
        if (over && p->frame_ms > 1.5f * ref_p50 && frame > 600 && !diag_is_idle()) {
            uint32_t all = causes | prev_causes;
            Hitch *h = &hitches[hitch_n % HITCHES];
            if (hitch_n - hitch_done >= HITCHES) ++hitch_dropped;
            h->t = uptime(); h->frame = frame; h->head = head; h->ms = p->frame_ms; h->busy = p->busy_ms;
            memcpy(h->idle, p->idle_ms, sizeof h->idle);
            h->ticks = p->ticks; h->flow = flow; h->causes = all;
            h->dsh = sh - sh0; h->dpipe = pipe - pipe0; h->ddef = def - def0;
            h->car = diag_car_state(h->pos, &(float){0});
            int first = -1;
            for (int c = 0; c < C_OTHER; ++c) if (all >> c & 1) { first = c; break; }
            ++a->cause[first < 0 ? C_OTHER : first];
            ++a->hitches;
            ++hitch_n;
        }
        prev_causes = causes; last_flow = flow; last_def = def; def0 = def; sh0 = sh; pipe0 = pipe;
    }
    static double next, next_sys, next_sum = 20;
    static int session_hdr_done;
    double now = host_ticks() / 1e9;
    if (now < next) return;
    next = now + 1;
    if (now >= next_sys) { next_sys = now + 5; cache_system(); }
    Sec *s = &secs[nsecs % SECS];
    memset(s, 0, sizeof *s);
    s->t = uptime(); s->frame = frame; s->flow = flow;
    // the device mix this second: RMS (stats.txt, perf.tsv), peak and full-scale samples (perf.tsv)
    double rms[6] = {0};
    float mpeak[6] = {0};
    float mdip[6] = {0};
    uint64_t mclip[6] = {0}, limited = 0;
    s->have_rms = dsound_mix_levels(0, rms, mpeak, mclip, mdip, &limited);
    for (int k = 0; k < 6; ++k) s->rms[k] = (float)rms[k];
    static float ft[PERF_RING], bt[PERF_RING], gt[PERF_RING];
    float sum = 0, busy = 0, idl[IDLE_N] = {0}, draws = 0, ticks = 0;
    int n = 0, nover = 0;
    for (; n < PERF_RING - 1 && (uint64_t)n < head && sum < 1000; ++n) {
        const PerfFrame *p = &perf_frames[(head - 1 - n) % PERF_RING];
        ft[n] = p->frame_ms; sum += p->frame_ms; busy += p->busy_ms; bt[n] = p->busy_ms;
        for (int k = 0; k < IDLE_N; ++k) idl[k] += p->idle_ms[k];
        draws += p->draws; ticks += p->ticks; nover += p->frame_ms > budget_ms;
    }
    if (n) {
        s->fps = sum > 0 ? n * 1000.0f / sum : 0;
        s->busy = busy / n;
        qsort(ft, (size_t)n, sizeof *ft, cmp_f);
        s->p50 = ft[n / 2]; s->p99 = ft[(n * 99) / 100]; s->max = ft[n - 1];
        uint64_t g = __atomic_load_n(&perf_gpu_serial, __ATOMIC_ACQUIRE);
        float gs = 0;
        int gn = 0;
        for (; gn < n && (uint64_t)gn < g; ++gn) { gt[gn] = perf_gpu_ms[(g - gn) % PERF_RING]; gs += gt[gn]; }
        s->gpu = gn ? gs / gn : 0;
        // the session log's perf.tsv line (percentiles need the sorted copies; ft is sorted above, so busy and gpu here)
        ref_p50 = s->p50;
        qsort(bt, (size_t)n, sizeof *bt, cmp_f);
        if (gn) qsort(gt, (size_t)gn, sizeof *gt, cmp_f);
        if (!session_hdr_done && frame >= 60) { session_hdr_done = 1; session_header(); }
        if (perf_f) {
            float pos[3], spd;
            int car = diag_car_state(pos, &spd);
            RenderPresentation ps;
            render_presentation_stats(&ps);
            static uint64_t shown_before[5];
            uint64_t shown[5];
            for (int i = 0; i < 5; ++i) { shown[i] = ps.shown[i] - shown_before[i]; shown_before[i] = ps.shown[i]; }
            fprintf(perf_f, "%.1f\t%llu\t%s\t%.1f\t%.2f\t%.2f\t%.2f\t%.2f\t%.2f\t%.2f\t%.2f\t%.2f\t%.2f\t%.2f\t%.2f\t%.1f\t%.2f\t%d\t%.0f\t%.1f\t%.1f\t%.1f\t%.2f\t%d\t%d\t%.3f\t%.3f\t%llu\t%llu\t%llu\t%llu\t%llu\t%d\t%d",
                    s->t, (unsigned long long)frame, st_name[state_of(flow)], s->fps, s->p50, s->p99, s->max, bt[n / 2], bt[(n * 99) / 100],
                    idl[IDLE_SLEEP] / n, idl[IDLE_GIL] / n, idl[IDLE_DRAWABLE] / n, idl[IDLE_GPU] / n, s->gpu, gn ? gt[(gn * 99) / 100] : 0,
                    draws / n, ticks / n, nover, diag_rss_bytes() / 1048576.0, car ? pos[0] : 0, car ? pos[1] : 0, car ? pos[2] : 0, car ? spd : 0,
                    portmenu_is_open(), ps.limit, ps.duration_ms, ps.refresh_ms,
                    (unsigned long long)shown[0], (unsigned long long)shown[1], (unsigned long long)shown[2],
                    (unsigned long long)shown[3], (unsigned long long)shown[4], ps.vrr_requested, ps.adaptive_scheduling);
            // mix levels in dBFS (-120 = silent; empty while no device mix, e.g. a movie): RMS then peak per channel
            // FL FR C LFE SL SR, then full-scale and gain-reduced samples (all channels) this second, then the quietest 50 ms
            // stretch (a cutout shorter than the second shows here, not in the RMS)
            uint64_t clips = 0;
            for (int k = 0; k < 6; ++k) clips += mclip[k];
            if (s->have_rms) {
                for (int k = 0; k < 6; ++k) fprintf(perf_f, "\t%.1f", rms[k] > 1e-6 ? 20 * log10(rms[k]) : -120.0);
                for (int k = 0; k < 6; ++k) fprintf(perf_f, "\t%.1f", mpeak[k] > 1e-6f ? 20 * log10f(mpeak[k]) : -120.0f);
                fprintf(perf_f, "\t%llu\t%llu", (unsigned long long)clips, (unsigned long long)limited);
                for (int k = 0; k < 6; ++k) fprintf(perf_f, "\t%.1f", mdip[k] > 1e-6f ? 20 * log10f(mdip[k]) : -120.0f);
            } else fputs("\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t", perf_f);
            // what separates "the GPU got slower" causes: macOS thermal state (0 nominal .. 3 critical, -1 unknown), low power mode,
            // app frontmost, window hidden, native full screen, and the command buffers' queue-to-end ms (render_gpu_wall_ms)
            fprintf(perf_f, "\t%d\t%d\t%d\t%d\t%d\t%.2f", sys_thermal_n, sys_lowpower, (int)NSApp.isActive, user32_window_occluded(),
                    ps.native_fullscreen, render_gpu_wall_ms());
            GPUClientsSample clients;
            gpuclients_cached(&clients);
            gpuclients_print(perf_f, &clients);
            fputc('\n', perf_f);
            fflush(perf_f);
        }
        // GPU times of the frames since last second into this state's histogram
        static uint64_t gdone;
        if (g > gdone + PERF_RING) gdone = g - PERF_RING + 1;
        for (; gdone < g; ++gdone) { StateAcc *a = &sacc[state_of(flow)]; ++a->gframes; ++a->hg[bucket(perf_gpu_ms[(gdone + 1) % PERF_RING])]; }
        session_flush_hitches(head, g);
        if (s->t >= next_sum) { next_sum = s->t + 60; session_summary(); }
    }
    ++nsecs;
    // the audio output and the default device: a note when either changes, and for new underruns
    static char last_mode[96] = "";
    static int last_live = -9;
    static uint64_t last_under;
    const char *mode = NULL;
    int live = -2;
    uint64_t under = 0;
    if (!dsound_output_info(&mode, &live, &under)) { mode = "no sound device"; live = -2; }
    if (strcmp(last_mode, mode ? mode : "") || live != last_live) {
        report_note("audio: output %s (%s)", mode ? mode : "?", live > 0 ? "playing" : live == 0 ? "silent" : live == -1 ? "waiting for a device" : "none");
        snprintf(last_mode, sizeof last_mode, "%s", mode ? mode : "");
        last_live = live;
    }
    if (under > last_under) report_note("audio: %llu new underruns", (unsigned long long)(under - last_under));
    last_under = under;
    static AudioDeviceID last_dev = (AudioDeviceID)-1;
    AudioDeviceID dev = default_device();
    if (dev != last_dev) {
        device_name(dev, sys_device, sizeof sys_device);
        report_note("audio: default output device %s", sys_device);
        last_dev = dev;
    }
}

// The live numbers for the menu's readout: the last second.
void report_live(float *fps, float *p99, float *gpu) {
    if (!nsecs) { *fps = *p99 = *gpu = 0; return; }
    const Sec *s = &secs[(nsecs - 1) % SECS];
    *fps = s->fps; *p99 = s->p99; *gpu = s->gpu;
}

// ---------------- the screenshot: the frame when the menu opened ----------------
static volatile int shot_wanted;
static char shot_file[1100];
static double shot_at;
static const char *reports_dir(void) {
    static char dir[1100];
    if (!dir[0]) { snprintf(dir, sizeof dir, "%s/reports", userdata_dir()); mkdir(dir, 0755); }
    return dir;
}
void report_request_shot(void) { shot_wanted = 1; }
// render_present.m: the next back buffer is saved as <userdata>/reports/.menu_frame.png (encoded off the game thread).
int report_shot(char *path, size_t n) {
    if (!shot_wanted || path[0]) return 0;
    shot_wanted = 0;
    snprintf(shot_file, sizeof shot_file, "%s/.menu_frame.png", reports_dir());
    snprintf(path, n, "%s", shot_file);
    shot_at = host_ticks() / 1e9;
    return 1;
}

// ---------------- writing a report ----------------
static void copy_tail(const char *from, const char *to, long max) {
    FILE *in = fopen(from, "rb");
    if (!in) return;
    FILE *out = fopen(to, "wb");
    if (!out) { fclose(in); return; }
    fseek(in, 0, SEEK_END);
    long size = ftell(in);
    if (max > 0 && size > max) {
        fseek(in, size - max, SEEK_SET);
        int ch;
        while ((ch = fgetc(in)) != EOF && ch != '\n') {}     // start at a line
    } else fseek(in, 0, SEEK_SET);
    char buf[65536];
    size_t got;
    while ((got = fread(buf, 1, sizeof buf, in)) > 0) fwrite(buf, 1, got, out);
    fclose(in);
    fclose(out);
}
static const char *flow_name(uint32_t f) {
    switch (f) {
    case 3: return "front end";
    case 4: return "loading";
    case 6: return "racing / free roam";
    default: return "starting";
    }
}

static char last_report[1200];
const char *report_last_path(void) { return last_report; }

// Writes a report folder; returns its path ("" on failure). safe: called from a normal thread (not a signal handler).
const char *report_write(const char *category, const char *note, const char *why, const char *attach) {
    time_t now = time(NULL);
    struct tm tm;
    localtime_r(&now, &tm);
    char stamp[32], dir[1200];
    strftime(stamp, sizeof stamp, "%Y%m%d-%H%M%S", &tm);
    snprintf(dir, sizeof dir, "%s/%s", reports_dir(), stamp);
    for (int k = 2; mkdir(dir, 0755) != 0 && k < 10; ++k) snprintf(dir, sizeof dir, "%s/%s-%d", reports_dir(), stamp, k);
    char path[1400];
    // settings.txt
    snprintf(path, sizeof path, "%s/settings.txt", dir);
    FILE *f = fopen(path, "w");
    char vbuf[96];
    if (f) {
        fprintf(f, "# id = value (source)\n");
        for (int i = 0; i < settings_count(); ++i) {
            Setting *s = settings_at(i);
            if (s->type != SET_TOGGLE && s->type != SET_CHOICE && s->type != SET_SLIDER) continue;
            int v = s->get ? s->get() : s->value;
            fprintf(f, "%s = %s (%s%s%s)%s\n", s->id, settings_value_name(s, v, vbuf, sizeof vbuf), settings_source(s),
                    s->locked ? "=" : "", s->locked ? s->env_text : "", s->restart && v != s->boot_value ? " [after restart]" : "");
        }
        fclose(f);
    }
    // stats.txt
    snprintf(path, sizeof path, "%s/stats.txt", dir);
    if ((f = fopen(path, "w"))) {
        fprintf(f, "# t(s)     frame     fps   p50ms  p99ms  maxms  busyms  gpums  flow  mixrms FL FR C LFE BL BR\n");
        uint64_t first = nsecs > SECS ? nsecs - SECS : 0;
        for (uint64_t i = first; i < nsecs; ++i) {
            const Sec *s = &secs[i % SECS];
            fprintf(f, "%9.1f %9llu %6.1f %6.2f %6.2f %6.2f %7.2f %6.2f %5u  ", s->t, (unsigned long long)s->frame, s->fps, s->p50, s->p99,
                    s->max, s->busy, s->gpu, s->flow);
            if (s->have_rms) fprintf(f, "%.3f %.3f %.3f %.3f %.3f %.3f\n", s->rms[0], s->rms[1], s->rms[2], s->rms[3], s->rms[4], s->rms[5]);
            else fprintf(f, "n/a\n");
        }
        fclose(f);
    }
    // events.txt
    snprintf(path, sizeof path, "%s/events.txt", dir);
    if ((f = fopen(path, "w"))) {
        fprintf(f, "# notable events (time since start, frame), oldest first\n");
        uint32_t head = notes_head, start = head > NOTES ? head - NOTES : 0;
        for (uint32_t i = start; i < head; ++i) fprintf(f, "%s\n", notes[i % NOTES]);
        diag_ring_dump(f, 400);
        fclose(f);
    }
    // log.txt: this run's stderr when it goes to a file
    char logp[1024] = "";
    if (fcntl(2, F_GETPATH, logp) == 0 && logp[0]) {
        snprintf(path, sizeof path, "%s/log.txt", dir);
        copy_tail(logp, path, 256 * 1024);
    }
    // screenshot.png: the frame when the menu opened (a user report while the menu is open)
    int have_shot = 0;
    if (!attach && shot_file[0] && host_ticks() / 1e9 - shot_at < 600 && access(shot_file, R_OK) == 0) {
        snprintf(path, sizeof path, "%s/screenshot.png", dir);
        copy_tail(shot_file, path, 0);
        have_shot = 1;
    }
    if (attach) {
        snprintf(path, sizeof path, "%s/crash.txt", dir);
        copy_tail(attach, path, 0);
    }
    // report.txt, the summary on top
    snprintf(path, sizeof path, "%s/report.txt", dir);
    if ((f = fopen(path, "w"))) {
        char when[64];
        strftime(when, sizeof when, "%Y-%m-%d %H:%M:%S %Z", &tm);
        uint32_t flow = M32(GAMEFLOW_STATE);
        fprintf(f, "NFSMW port problem report\n\n");
        fprintf(f, "when:       %s\n", when);
        fprintf(f, "category:   %s\n", category && *category ? category : "Other");
        fprintf(f, "note:       %s\n", note && *note ? note : "(none)");
        fprintf(f, "reason:     %s\n", why && *why ? why : "reported from the F10 menu");
        fprintf(f, "build:      %s (speed.exe code %.12s)\n", port_build_id(), speed_exe_fingerprint);
        fprintf(f, "game:       %s (game-flow %u), frame %llu, %.0f s since start%s\n", flow_name(flow), flow,
                (unsigned long long)diag_current_frame(), uptime(), diag_on ? ", BOOT_DIAG on" : "");
        fprintf(f, "display:    %s\n", sys_display);
        fprintf(f, "display:    %s\n", sys_edr);
        fprintf(f, "render:     ");
        print_render(f);
        fprintf(f, "\n");
        const char *mode = NULL;
        int live = -2;
        uint64_t under = 0;
        if (dsound_output_info(&mode, &live, &under))
            // sys_device is macOS's default output, not the device we play on: holding the HDMI TV for passthrough makes
                                                                                                                              
            fprintf(f, "audio:      %s (%s, %llu underruns); macOS default output %s\n", mode ? mode : "?",
                    live > 0 ? "playing" : live == 0 ? "silent: NFS_AUDIO=null" : "waiting for a device", (unsigned long long)under,
                    sys_device);
        else fprintf(f, "audio:      no sound device yet (default output %s)\n", sys_device);
        fprintf(f, "controller: %s, %s layout\n", sys_pad, user32_pad_layout_xbox() ? "Xbox" : "PlayStation");
        // the last minute
        float fmin = 1e9f, fsum = 0, p99max = 0, gsum = 0;
        int n = 0;
        for (uint64_t i = nsecs > 60 ? nsecs - 60 : 0; i < nsecs; ++i) {
            const Sec *s = &secs[i % SECS];
            if (s->fps <= 0) continue;           // before the first frame
            ++n;
            fmin = fminf(fmin, s->fps); fsum += s->fps; p99max = fmaxf(p99max, s->p99); gsum += s->gpu;
        }
        if (n) fprintf(f, "last %ds:   fps mean %.1f min %.1f, worst p99 %.2f ms, GPU mean %.2f ms\n", n, fsum / n, fmin, p99max, gsum / n);
        fprintf(f, "\nsettings that differ from their defaults or are set by a switch:\n");
        int any = print_nondefault(f);
        if (!any) fprintf(f, "  (none)\n");
        fprintf(f, "\nfiles: report.txt%s, stats.txt, events.txt%s, settings.txt%s\n", have_shot ? ", screenshot.png" : "",
                logp[0] ? ", log.txt" : "", attach ? ", crash.txt" : "");
        fclose(f);
    }
    snprintf(last_report, sizeof last_report, "%s", dir);
    report_note("report written: %s (%s)", dir, category ? category : "?");
    fprintf(stderr, "boot: problem report written: %s\n", dir);
    if (diag_on) diag_event("report: %s (%s)", dir, category ? category : "?");
    return last_report;
}

// diag.c diag_report: a crash or a hang writes a report folder by itself (at most 3 a run, 30 s apart).
void report_auto(const char *why, const char *diag_file) {
    static int count;
    static double last;
    double now = host_ticks() / 1e9;
    if (count >= 3 || (count && now - last < 30)) return;
    ++count;
    last = now;
    report_note("%s", why);
    session_summary();
    report_write("Crash-freeze", "", why, diag_file);
}

// ---------------- start-up: full logging, the hang watchdog ----------------
static void *watchdog(void *p) {
    (void)p;
    uint64_t reported = 0;
    for (;;) {
        sleep(1);
        uint64_t last = diag_last_frame_ns(), now = host_ticks();
        if (!last || diag_is_idle() || last == reported || now - last < 15000000000ull) continue;
        reported = last;
        char why[128];
        snprintf(why, sizeof why, "hang: no frame for 15 s after frame %llu", (unsigned long long)diag_current_frame());
        diag_report("hang.txt", why);
    }
    return NULL;
}
// boot.c, after settings_init and before diag_init.
void report_init(void) {
    t_start = host_ticks();
    if (!getenv("BOOT_DIAG") && settings_get("diag.full_logging") == 1) {
        char dir[1200], stamp[32];
        time_t now = time(NULL);
        struct tm tm;
        localtime_r(&now, &tm);
        strftime(stamp, sizeof stamp, "%Y%m%d-%H%M%S", &tm);
        snprintf(dir, sizeof dir, "%s/logs", userdata_dir());
        mkdir(dir, 0755);
        snprintf(dir, sizeof dir, "%s/logs/%s", userdata_dir(), stamp);
        setenv("BOOT_DIAG", dir, 1);
        setenv("BOOT_DIAG_GPUPASS", "1", 0);   // per-pass GPU time (gpupass.m); A/B 2026-10-26 4K 2x: busy 6.49 vs 6.48 ms, GPU 6.18 vs 6.21 ms
        fprintf(stderr, "boot: full diagnostic logging (Diagnostics menu): %s\n", dir);
    }
    session_open();
    gpuclients_start();
    atexit(session_exit);
    report_note("start: build %s", port_build_id());
    if (!getenv("BOOT_DIAG")) {
        pthread_t t;
        pthread_create(&t, NULL, watchdog, NULL);
        pthread_detach(t);
    }
}
