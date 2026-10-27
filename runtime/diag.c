// diag.c - diagnostic mode: one run records everything needed to diagnose it afterwards.
//
//   BOOT_DIAG=<dir> ./boot <game>
//
// <dir>/events.log     every import and host (COM) call: frame, thread, name, arguments, result, call site
// <dir>/frames.log     one line per frame: draws, time, bump heap; game-flow state changes
// <dir>/calls.txt      calls per import and per COM method so far (rewritten every 30 s), e.g. to see which stubs run
// <dir>/mem.log        every 10 s: rss, footprint, malloc, Metal allocations, the guest heaps, Metal copies by kind, render
//                      caches, the guest's D3D objects by kind (live/retired/with a Metal copy, guest MB)
// <dir>/stats.log      once a second: fps, frame time p50/p99/max, process RSS, bump/resource/game heap use and
//                      high-water marks, live GPU textures/buffers, mix RMS per channel (FL FR C LFE BL BR), game-flow state (3 front end, 6 racing),
//                      the game's shared string pool (sstr: strings, bytes of 32768, peaks), the DualSense's adaptive trigger modes and lightbar colour as last decided (padfx.m). BOOT_DIAG_DRAWS also takes
//                      race+N: N frames after the game-flow state first becomes 6, sim+S: the frame after the race's
//                      sim time passes S s (as BOOT_DIAG_SIMSHOTS), key+N: N frames after the last
//                      scripted key press (BOOT_KEYS), e.g. a fixed time after a scripted menu change, probe: the
//                      base and signature passes of BOOT_DIAG_PROBE (probe.m), e.g. the forced-shadow frame.
// <dir>/frame_NNNNN.png  the back buffer every BOOT_DIAG_SHOT_EVERY frames (default 300), and for the frames in
//                      BOOT_DIAG_SHOTS=f1,race+N,...
// <dir>/draws_NNNNN.txt  every draw of the frames in BOOT_DIAG_DRAWS=f1,f2,... (state, shaders, textures)
// <dir>/tex_NNNNN/     the textures those frames sample
// <dir>/stall_N.txt    a frame late by BOOT_DIAG_STALL ms (default 500, after frame 600; at most 5): same contents
// <dir>/hang_N.txt     no frame for BOOT_DIAG_HANG seconds (default 5): all threads' host backtraces with the
//                      game's function names, every guest thread's registers and stack, the last events
//                      (what each one is blocked on: critical section and owner, or handles); a hang that
//                      lasts is reported again 10 s and 20 s later (a loop shows different stacks)
// <dir>/hd_compare/   X360 HD texture validation: PC texture | X360 level at the PC size, one PNG per texture hash
// <dir>/timeline.log  loading and the race start in game time: events, messages, sim tasks, milestones (below)
// <dir>/profile_N.txt  where the game thread spent a frame slower than BOOT_DIAG_PROFILE ms (default 100)
// <dir>/probe.txt      BOOT_DIAG_PROBE=<features>: does each render feature's output reach the presented frame (probe.m)
// <dir>/crash.txt      the same on a crash, a stop on an unimplemented import/method, or a runtime error
//
// The last 64k events are always kept in memory (cheap) and written into hang/crash reports.
#include <CoreGraphics/CoreGraphics.h>
#include <ImageIO/ImageIO.h>
#include <ctype.h>
#include <dlfcn.h>
#include <mach/mach.h>
#include <malloc/malloc.h>
#include <math.h>
#include <execinfo.h>
#include <pthread.h>
#include <signal.h>
#include <stdarg.h>
#include <stdlib.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#include "host.h"

int diag_on;
const char *diag_dir;
static FILE *events_f, *frames_f;
static uint64_t frame_now, last_frame_ns;
static int idle_now;                     // diag_idle
static int shot_every = 3600;            // periodic overview shots (frame_NNNNN.jpg, small); 0 = none
static FILE *timeline_f;                 // timeline.log (below)
static float timeline_race0 = -1, timeline_secs = 60;
void diag_timeline(const char *fmt, ...) __attribute__((format(printf, 1, 2)));

// ---------------- event ring ----------------
#define RING 65536
#define LINE 192
static char ring[RING][LINE];
// Call records (imports and COM methods, millions a second) are stored raw and formatted only when a report is written.
static struct { uint64_t frame; const char *name; uint32_t v[8]; uint32_t tid; uint8_t nargs, raw; } rawrec[RING];
static volatile uint32_t ring_head;
static pthread_mutex_t file_mu = PTHREAD_MUTEX_INITIALIZER;

uint32_t diag_thread_id(void);   // threads.c

void diag_call_ring(const char *name, int nargs, const uint32_t *args, uint32_t ret, uint32_t ra) {
    uint32_t i = __atomic_fetch_add(&ring_head, 1, __ATOMIC_RELAXED) % RING;
    rawrec[i].frame = frame_now;
    rawrec[i].tid = diag_thread_id();
    rawrec[i].name = name;
    rawrec[i].nargs = (uint8_t)(nargs < 6 ? nargs : 6);
    for (int k = 0; k < rawrec[i].nargs; ++k) rawrec[i].v[k] = args[k];
    rawrec[i].v[6] = ret; rawrec[i].v[7] = ra;
    rawrec[i].raw = 1;
}
static const char *ring_line(uint32_t i, char *buf, size_t n) {
    if (!rawrec[i].raw) return ring[i];
    int k = snprintf(buf, n, "f%llu t%u %s(", (unsigned long long)rawrec[i].frame, rawrec[i].tid, rawrec[i].name);
    for (int a = 0; a < rawrec[i].nargs && k < (int)n; ++a) k += snprintf(buf + k, n - (size_t)k, a ? ", %X" : "%X", rawrec[i].v[a]);
    if (k < (int)n) snprintf(buf + k, n - (size_t)k, ") -> %X @%08X", rawrec[i].v[6], rawrec[i].v[7]);
    return buf;
}
static uint32_t ring_put(const char *fmt, va_list ap) {
    uint32_t i = __atomic_fetch_add(&ring_head, 1, __ATOMIC_RELAXED) % RING;
    rawrec[i].raw = 0;
    int n = snprintf(ring[i], LINE, "f%llu t%u ", (unsigned long long)frame_now, diag_thread_id());
    vsnprintf(ring[i] + n, LINE - (size_t)n, fmt, ap);
    return i;
}
void diag_event_ring(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    ring_put(fmt, ap);
    va_end(ap);
}
void diag_event(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    va_list file_ap;
    va_copy(file_ap, ap);
    uint64_t frame = frame_now;
    uint32_t tid = diag_thread_id();
    ring_put(fmt, ap);
    va_end(ap);
    if (events_f) {
        pthread_mutex_lock(&file_mu);
        // Keep the crash ring compact, but don't truncate file events to its 192-byte slots: audiochain's byte
        // totals grow during long runs and would otherwise lose the callback-gap/encode maxima at the end.
        fprintf(events_f, "f%llu t%u ", (unsigned long long)frame, tid);
        vfprintf(events_f, fmt, file_ap);
        fputc('\n', events_f);
        pthread_mutex_unlock(&file_mu);
    }
    va_end(file_ap);
}
void report_note(const char *fmt, ...) __attribute__((format(printf, 1, 2)));   // report.m
void report_frame(uint64_t frame, uint32_t flow);
void report_auto(const char *why, const char *diag_file);
void portmenu_frame(void);
static void dump_ring(FILE *f, int last) {
    uint32_t head = ring_head, start = head > (uint32_t)last ? head - (uint32_t)last : 0;
    fprintf(f, "\n==== last %u events ====\n", head - start);
    char buf[LINE];
    for (uint32_t k = start; k < head; ++k) fprintf(f, "%s\n", ring_line(k % RING, buf, sizeof buf));
}

// ---------------- symbols: the game's function names (decomp/functions.tsv) ----------------
typedef struct { uint32_t addr; char *name; } Sym;
static Sym *syms;
static int nsyms;
static pthread_once_t symbols_once = PTHREAD_ONCE_INIT;
static char symbols_path[1200];
static void load_symbols_once(void) {
    char exe[1024];
    Dl_info di;
    if (!dladdr((void *)load_symbols_once, &di) || !di.dli_fname) return;
    snprintf(exe, sizeof exe, "%s", di.dli_fname);
    char *slash = strrchr(exe, '/');
    if (slash) *slash = 0;
    // The owner's Ghidra export (named), else the committed manifest (addresses only: FUN_XXXXXXXX).
#ifdef NFS_PUBLIC
    FILE *f = NULL;   // the public build has no owner export beside its checkout: never look outside it (M4 F7)
#else
    snprintf(symbols_path, sizeof symbols_path, "%s/../../decomp/functions.tsv", exe);
    FILE *f = fopen(symbols_path, "r");
#endif
    if (!f) {
        snprintf(symbols_path, sizeof symbols_path, "%s/../recomp/functions_v13.tsv", exe);
        f = fopen(symbols_path, "r");
    }
    if (!f) return;
    char line[2048], fun[16];
    int cap = 0;
    while (fgets(line, sizeof line, f)) {
        if (line[0] == '#') continue;
        char *tab = strchr(line, '\t'), *name = fun, *end;
        if (tab) {
            name = tab + 1;
            if ((end = strchr(name, '\t')) || (end = strchr(name, '\n'))) *end = 0;
        } else if (strlen(line) >= 8) {
            snprintf(fun, sizeof fun, "FUN_%.8s", line);
            for (char *u = fun + 4; *u; ++u) *u = (char)toupper((unsigned char)*u);
        } else continue;
        if (nsyms == cap) { cap = cap ? cap * 2 : 65536; syms = realloc(syms, (size_t)cap * sizeof *syms); }
        syms[nsyms].addr = (uint32_t)strtoul(line, NULL, 16);
        syms[nsyms].name = strdup(name);
        ++nsyms;
    }
    fclose(f);
}
static void load_symbols(void) {
    // Cache failure too: scratch executables and app bundles may have no symbol table.
    pthread_once(&symbols_once, load_symbols_once);
}
// BGRA8 pixels (rows `pitch` bytes apart) to a PNG. keep_alpha 0 for render targets and the back buffer, whose
// alpha D3D9 doesn't display (often 0 where draws mask it out).
// A path ending in .jpg writes an overview: scaled down to at most 960 pixels wide, JPEG quality 0.8 (~50-100 KB
// instead of several MB at 4K); anything else is a full-size PNG.
void diag_png(const char *path, const void *bgra, uint32_t w, uint32_t h, uint32_t pitch, int keep_alpha) {
    size_t n = strlen(path);
    int small = n > 4 && !strcmp(path + n - 4, ".jpg");
    CGColorSpaceRef cs = CGColorSpaceCreateDeviceRGB();
    CGContextRef ctx = CGBitmapContextCreate((void *)bgra, w, h, 8, pitch, cs,
                                             (keep_alpha ? kCGImageAlphaPremultipliedFirst : kCGImageAlphaNoneSkipFirst) | kCGBitmapByteOrder32Little);
    CGImageRef img = ctx ? CGBitmapContextCreateImage(ctx) : NULL;
    if (img && small && w > 960) {
        size_t sw = 960, sh = (size_t)h * 960 / w;
        CGContextRef s = CGBitmapContextCreate(NULL, sw, sh, 8, 0, cs, kCGImageAlphaNoneSkipFirst | kCGBitmapByteOrder32Little);
        if (s) {
            CGContextSetInterpolationQuality(s, kCGInterpolationMedium);
            CGContextDrawImage(s, CGRectMake(0, 0, (CGFloat)sw, (CGFloat)sh), img);
            CGImageRelease(img);
            img = CGBitmapContextCreateImage(s);
            CGContextRelease(s);
        }
    }
    CFURLRef url = CFURLCreateFromFileSystemRepresentation(NULL, (const UInt8 *)path, (CFIndex)n, false);
    CGImageDestinationRef dst = img ? CGImageDestinationCreateWithURL(url, small ? CFSTR("public.jpeg") : CFSTR("public.png"), 1, NULL) : NULL;
    if (dst) {
        float quality = 0.8f;
        CFNumberRef q = CFNumberCreate(NULL, kCFNumberFloatType, &quality);
        const void *k[1] = {kCGImageDestinationLossyCompressionQuality}, *v[1] = {q};
        CFDictionaryRef opt = small ? CFDictionaryCreate(NULL, k, v, 1, &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks) : NULL;
        CGImageDestinationAddImage(dst, img, opt);
        CGImageDestinationFinalize(dst);
        if (opt) CFRelease(opt);
        CFRelease(q);
        CFRelease(dst);
    }
    if (img) CGImageRelease(img);
    if (ctx) CGContextRelease(ctx);
    CFRelease(url);
    CGColorSpaceRelease(cs);
}

const char *diag_symbol(uint32_t a, uint32_t *off) {
    load_symbols();
    int lo = 0, hi = nsyms - 1, best = -1;
    while (lo <= hi) {
        int m = (lo + hi) / 2;
        if (syms[m].addr <= a) { best = m; lo = m + 1; } else hi = m - 1;
    }
    if (best < 0) return NULL;
    if (off) *off = a - syms[best].addr;
    return syms[best].name;
}

// ---------------- reports ----------------
static FILE *report_f;
static volatile int bt_done;
static void write_backtrace(FILE *f) {
    void *pcs[96];
    int n = backtrace(pcs, 96);
    char **s = backtrace_symbols(pcs, n);
    for (int i = 0; i < n; ++i) {
        const char *fn = s ? strstr(s[i], "f_00") : NULL;
        if (fn && strlen(fn) >= 10) {
            uint32_t a = (uint32_t)strtoul(fn + 2, NULL, 16), off = 0;
            const char *name = diag_symbol(a, &off);
            fprintf(f, "  %s   [%s]\n", s[i], name ? name : "?");
        } else {
            fprintf(f, "  %s\n", s ? s[i] : "?");
        }
    }
    free(s);
}
static volatile int sample_mode;          // SIGUSR2 from the slow-frame profiler: record the stack, write nothing
static void *sample_pcs[64];
static volatile int sample_n;
static void bt_signal(int sig) {
    (void)sig;
    if (sample_mode) { sample_n = backtrace(sample_pcs, 64); bt_done = 1; return; }
    if (report_f) { fprintf(report_f, "\n-- host thread %p --\n", (void *)pthread_self()); write_backtrace(report_f); fflush(report_f); }
    bt_done = 1;
}
int threads_list(pthread_t *ths, Cpu **cpus, uint32_t *tids, int cap);   // threads.c

void threads_wait_desc(uint32_t tid, char *buf, size_t n);   // threads.c
static void guest_state(FILE *f, Cpu *c, uint32_t tid) {
    char wait[256];
    threads_wait_desc(tid, wait, sizeof wait);
    fprintf(f, "\n-- guest thread %u: eax %08X ecx %08X edx %08X ebx %08X esp %08X ebp %08X esi %08X edi %08X\n", tid,
            c->eax, c->ecx, c->edx, c->ebx, c->esp, c->ebp, c->esi, c->edi);
    fprintf(f, "   blocked: %s\n", wait[0] ? wait : "no (running, or in a host call)");
    fprintf(f, "   return addresses on the guest stack (innermost first):\n");
    int shown = 0;
    for (uint32_t a = c->esp; a < c->esp + 0x10000 && shown < 32; a += 4) {
        uint32_t v = M32(a);
        if (v > 0x401000 && v < 0x88F2A5 && (M8(v - 5) == 0xE8 || M8(v - 6) == 0xFF || M8(v - 2) == 0xFF || M8(v - 3) == 0xFF)) {
            uint32_t off = 0;
            const char *name = diag_symbol(v, &off);
            fprintf(f, "   [%08X] %08X  %s+0x%X\n", a, v, name ? name : "?", off);
            ++shown;
        }
    }
}
// Writes a full report: every thread's host backtrace, guest registers/stacks, the event ring.
void diag_report(const char *file, const char *why) {
    char path[1024];
    if (diag_dir) snprintf(path, sizeof path, "%s/%s", diag_dir, file);
    else snprintf(path, sizeof path, "%s/%s-%s", log_dir(), log_name(), file);   // NFSMW-crash.txt next to NFSMW.log
    FILE *f = fopen(path, "w");
    if (!f) return;
    fprintf(f, "%s\nframe %llu\n", why, (unsigned long long)frame_now);
    void sstr_pool_stats(uint32_t *n, uint32_t *peak_n, uint32_t *bytes, uint32_t *peak_bytes, uint32_t *size);
    uint32_t sn, spn, sb, spb, ssz;
    sstr_pool_stats(&sn, &spn, &sb, &spb, &ssz);
    fprintf(f, "shared string pool: %u strings, %u of %u bytes (peak %u strings, %u bytes)\n", sn, sb, ssz, spn, spb);
    report_f = f;
    fprintf(f, "\n-- reporting thread --\n");
    write_backtrace(f);
    pthread_t ths[64];
    Cpu *cpus[64];
    uint32_t tids[64];
    int n = threads_list(ths, cpus, tids, 64);
    signal(SIGUSR2, bt_signal);
    for (int i = 0; i < n; ++i) {
        if (pthread_equal(ths[i], pthread_self())) continue;
        bt_done = 0;
        if (pthread_kill(ths[i], SIGUSR2) == 0)
            for (int k = 0; k < 200 && !bt_done; ++k) usleep(5000);
    }
    for (int i = 0; i < n; ++i) if (cpus[i]) guest_state(f, cpus[i], tids[i]);
    dump_ring(f, 4000);
    report_f = NULL;
    fclose(f);
    fprintf(stderr, "boot: diagnostic report written: %s (%s)\n", path, why);
    if (!strcmp(file, "crash.txt") || !strncmp(file, "hang", 4)) report_auto(why, path);   // report.m: a report folder too
}
void diag_ring_dump(FILE *f, int last) { dump_ring(f, last); }   // report.m

static void crash_signal(int sig) {
    static int once;
    if (once++) _exit(128 + sig);
    char why[64];
    snprintf(why, sizeof why, "crash: signal %d", sig);
    diag_report("crash.txt", why);
    _exit(128 + sig);
}

// ---------------- slow-frame profiler ----------------
// <dir>/profile_N.txt: a frame that takes longer than BOOT_DIAG_PROFILE ms (default 100; 0 = off; at most 20 frames)
// is sampled every ~2 ms (the game thread's host stack, so translated functions show as f_XXXXXXXX with the game's
// name, host code by its own): functions by inclusive and self samples, and the most common stacks. Shows where a
// load hitch goes without a profiler attached.
static pthread_t game_thread;
static int have_game_thread;
#define PROF_SAMPLES 4096
#define PROF_DEPTH 48
static void *prof_pcs[PROF_SAMPLES][PROF_DEPTH];
static uint8_t prof_depth[PROF_SAMPLES];
static int prof_n, prof_files, prof_max = 20;   // BOOT_DIAG_PROFILE_MAX: how many profile_N.txt (default 20)
static uint64_t prof_frame_ns, prof_frame_no;
static void prof_name(void *pc, char *buf, size_t n) {
    Dl_info di;
    const char *s = dladdr(pc, &di) && di.dli_sname ? di.dli_sname : NULL;
    if (s && !strncmp(s, "f_00", 4) && strlen(s) >= 10) {
        uint32_t a = (uint32_t)strtoul(s + 2, NULL, 16), off = 0;
        const char *g = diag_symbol(a, &off);
        snprintf(buf, n, "%s %s", s, g ? g : "?");
    } else if (s) snprintf(buf, n, "%s", s);
    else snprintf(buf, n, "%p", pc);
}
typedef struct { void *pc; int incl, self; } ProfFn;
static int cmp_incl(const void *a, const void *b) { return ((const ProfFn *)b)->incl - ((const ProfFn *)a)->incl; }
static int cmp_self(const void *a, const void *b) { return ((const ProfFn *)b)->self - ((const ProfFn *)a)->self; }
static void prof_write(uint64_t end_ns) {
    if (prof_n == 0 || prof_files >= prof_max) return;
    char path[1024], nm[256];
    snprintf(path, sizeof path, "%s/profile_%d.txt", diag_dir, ++prof_files);
    FILE *f = fopen(path, "w");
    if (!f) return;
    fprintf(f, "frame %llu took %.1f ms; %d samples of the game thread (~2 ms apart, from %d ms into the frame)\n",
            (unsigned long long)prof_frame_no, (end_ns - prof_frame_ns) / 1e6, prof_n,
            getenv("BOOT_DIAG_PROFILE") ? atoi(getenv("BOOT_DIAG_PROFILE")) : 100);
    static ProfFn fns[8192];
    int nf = 0;
    for (int s = 0; s < prof_n; ++s) {
        void *start[PROF_DEPTH];                     // functions by their symbol's start: counted once per sample
        for (int d = 0; d < prof_depth[s]; ++d) {
            Dl_info di;
            start[d] = dladdr(prof_pcs[s][d], &di) && di.dli_saddr ? di.dli_saddr : prof_pcs[s][d];
            int k = 0, seen = 0;
            for (int e = 0; e < d; ++e) if (start[e] == start[d]) seen = 1;
            for (; k < nf && fns[k].pc != start[d]; ++k) {}
            if (k == nf) { if (nf == 8192) continue; fns[nf].pc = start[d]; fns[nf].incl = fns[nf].self = 0; ++nf; }
            if (!seen) fns[k].incl++;
            if (d == 0) fns[k].self++;
        }
    }
    qsort(fns, (size_t)nf, sizeof *fns, cmp_incl);
    fprintf(f, "\n-- inclusive (samples with the function on the stack) --\n");
    for (int k = 0; k < nf && k < 60; ++k) { prof_name(fns[k].pc, nm, sizeof nm); fprintf(f, "%6d %5.1f%%  %s\n", fns[k].incl, 100.0 * fns[k].incl / prof_n, nm); }
    qsort(fns, (size_t)nf, sizeof *fns, cmp_self);
    fprintf(f, "\n-- self (innermost function) --\n");
    for (int k = 0; k < nf && k < 30 && fns[k].self; ++k) { prof_name(fns[k].pc, nm, sizeof nm); fprintf(f, "%6d %5.1f%%  %s\n", fns[k].self, 100.0 * fns[k].self / prof_n, nm); }
    fprintf(f, "\n-- every 8th sample's stack --\n");
    for (int s = 0; s < prof_n; s += 8) {
        fprintf(f, "sample %d:\n", s);
        for (int d = 0; d < prof_depth[s]; ++d) { prof_name(prof_pcs[s][d], nm, sizeof nm); fprintf(f, "    %s\n", nm); }
    }
    fclose(f);
}
// Sleeps ~100 ms in 2 ms steps, sampling the game thread while its frame is late.
static void profile_wait_100ms(void) {
    static int ms = -1;
    if (ms < 0) ms = getenv("BOOT_DIAG_PROFILE") ? atoi(getenv("BOOT_DIAG_PROFILE")) : 100;
    if (ms <= 0) { usleep(100000); return; }
    for (int step = 0; step < 50; ++step) {
        usleep(2000);
        uint64_t last = last_frame_ns, now = host_ticks();
        if (prof_frame_ns && last != prof_frame_ns) { prof_write(last); prof_frame_ns = 0; prof_n = 0; }   // that frame ended
        if (!have_game_thread || idle_now || !last || now - last < (uint64_t)ms * 1000000ull || prof_files >= prof_max) continue;
        if (prof_frame_ns != last) { prof_frame_ns = last; prof_frame_no = frame_now; prof_n = 0; }
        if (prof_n >= PROF_SAMPLES) continue;
        sample_mode = 1; bt_done = 0; sample_n = 0;
        if (pthread_kill(game_thread, SIGUSR2) == 0)
            for (int k = 0; k < 400 && !bt_done; ++k) usleep(50);
        sample_mode = 0;
        if (!bt_done || sample_n < 3) continue;
        int d = sample_n - 2 > PROF_DEPTH ? PROF_DEPTH : sample_n - 2;                  // skip the signal handler's own frames
        for (int k = 0; k < d; ++k) prof_pcs[prof_n][k] = sample_pcs[k + 2];
        prof_depth[prof_n++] = (uint8_t)d;
    }
}

// ---------------- GIL waits: gilwait.log ----------------
// When the game thread waits >= BOOT_DIAG_GILWAIT ms (default 10) for the guest lock, one entry: where it waited,
// which guest thread held the lock (start address), for how long, the import/COM call it was in, and its host stack
// sampled every ~1 ms from 3 ms into the wait (recompiled guest functions show as f_00XXXXXX + the game symbol).
extern GilRec *volatile gil_holder;
extern volatile uint64_t gil_main_wait_since;
extern volatile void *gil_main_wait_site;
uint32_t threads_start_of(uint32_t tid);
#define GW_SAMPLES 1024
#define GW_DEPTH 40
static void *gw_pcs[GW_SAMPLES][GW_DEPTH];
static uint8_t gw_depth[GW_SAMPLES];
static uint32_t gw_tid[GW_SAMPLES];
static const char *gw_imp[GW_SAMPLES];
static float gw_imp_ms[GW_SAMPLES], gw_held_ms[GW_SAMPLES];
static void *gw_buf[64];
static volatile int gw_n, gw_done;
static uint64_t gw_t0;
static FILE *gw_f;
static void gw_signal(int sig) { (void)sig; gw_n = backtrace(gw_buf, 64); gw_done = 1; }
static void gw_write(uint64_t since, uint64_t end, void *site, int n, uint64_t frame) {
    if (!gw_f) { char path[1024]; snprintf(path, sizeof path, "%s/gilwait.log", diag_dir); gw_f = fopen(path, "w"); if (!gw_f) return; }
    char nm[256];
    prof_name(site, nm, sizeof nm);
    fprintf(gw_f, "\n== %.3f s (since diag start) frame %llu: game thread waited %.1f ms for the GIL in %s; %d holder samples\n",
            (since - gw_t0) / 1e9, (unsigned long long)frame, (end - since) / 1e6, nm, n);
    uint32_t tids[16]; int ntid = 0;
    for (int i = 0; i < n; ++i) { int k = 0; while (k < ntid && tids[k] != gw_tid[i]) ++k; if (k == ntid && ntid < 16) tids[ntid++] = gw_tid[i]; }
    for (int k = 0; k < ntid; ++k) {
        int cnt = 0, inimp = 0; float held = 0;
        for (int i = 0; i < n; ++i) if (gw_tid[i] == tids[k]) { ++cnt; if (gw_held_ms[i] > held) held = gw_held_ms[i]; if (gw_imp[i]) ++inimp; }
        uint32_t st = threads_start_of(tids[k]), off = 0;
        const char *sn = st ? diag_symbol(st, &off) : NULL;
        fprintf(gw_f, "  holder tid %u (start %08X %s): %d samples, held >= %.1f ms, %d samples inside an import/COM call\n",
                tids[k], st, sn ? sn : (tids[k] == 1 ? "main" : "?"), cnt, held, inimp);
        const char *seen[32]; int nseen = 0;
        for (int i = 0; i < n; ++i) {
            if (gw_tid[i] != tids[k] || !gw_imp[i]) continue;
            int j = 0; while (j < nseen && seen[j] != gw_imp[i]) ++j;
            if (j < nseen || nseen == 32) continue;
            seen[nseen++] = gw_imp[i];
            int c2 = 0; float mx = 0;
            for (int m = 0; m < n; ++m) if (gw_tid[m] == tids[k] && gw_imp[m] == gw_imp[i]) { ++c2; if (gw_imp_ms[m] > mx) mx = gw_imp_ms[m]; }
            fprintf(gw_f, "    in %s: %d samples, up to %.1f ms into the call\n", gw_imp[i], c2, mx);
        }
    }
    // inclusive / self over all samples (by symbol start)
    static ProfFn fns[4096];
    int nf = 0, ns = 0;
    for (int s2 = 0; s2 < n; ++s2) {
        if (!gw_depth[s2]) continue;
        ++ns;
        void *start[GW_DEPTH];
        for (int d = 0; d < gw_depth[s2]; ++d) {
            Dl_info di;
            start[d] = dladdr(gw_pcs[s2][d], &di) && di.dli_saddr ? di.dli_saddr : gw_pcs[s2][d];
            int seen2 = 0, k = 0;
            for (int e = 0; e < d; ++e) if (start[e] == start[d]) seen2 = 1;
            for (; k < nf && fns[k].pc != start[d]; ++k) {}
            if (k == nf) { if (nf == 4096) continue; fns[nf].pc = start[d]; fns[nf].incl = fns[nf].self = 0; ++nf; }
            if (!seen2) fns[k].incl++;
            if (d == 0) fns[k].self++;
        }
    }
    if (ns) {
        qsort(fns, (size_t)nf, sizeof *fns, cmp_incl);
        fprintf(gw_f, "  inclusive (%d stacks):\n", ns);
        for (int k = 0; k < nf && k < 40; ++k) { prof_name(fns[k].pc, nm, sizeof nm); fprintf(gw_f, "  %5d %5.1f%%  %s\n", fns[k].incl, 100.0 * fns[k].incl / ns, nm); }
        qsort(fns, (size_t)nf, sizeof *fns, cmp_self);
        fprintf(gw_f, "  self:\n");
        for (int k = 0; k < nf && k < 15 && fns[k].self; ++k) { prof_name(fns[k].pc, nm, sizeof nm); fprintf(gw_f, "  %5d %5.1f%%  %s\n", fns[k].self, 100.0 * fns[k].self / ns, nm); }
        for (int s2 = 0; s2 < n; s2 += (n > 4 ? n / 4 : 1)) {
            if (!gw_depth[s2]) continue;
            fprintf(gw_f, "  sample %d (tid %u, held %.1f ms, %s):\n", s2, gw_tid[s2], gw_held_ms[s2], gw_imp[s2] ? gw_imp[s2] : "guest code");
            for (int d = 0; d < gw_depth[s2]; ++d) { prof_name(gw_pcs[s2][d], nm, sizeof nm); fprintf(gw_f, "      %s\n", nm); }
        }
    }
    fflush(gw_f);
}
static void *gil_watch(void *p) {
    (void)p;
    int min_ms = getenv("BOOT_DIAG_GILWAIT") ? atoi(getenv("BOOT_DIAG_GILWAIT")) : 10;
    uint64_t cur = 0, frame = 0;
    void *site = NULL;
    int n = 0;
    for (;;) {
        usleep(1000);
        uint64_t w = gil_main_wait_since, now = host_ticks();
        if (cur && w != cur) {                                  // that wait ended (within ~1 ms)
            if (now - cur >= (uint64_t)min_ms * 1000000ull) gw_write(cur, now, site, n, frame);
            cur = 0;
        }
        if (!w) continue;
        if (!cur) { cur = w; site = (void *)gil_main_wait_site; frame = frame_now; n = 0; }
        if (now - w < 3000000ull || n >= GW_SAMPLES) continue;
        GilRec *h = gil_holder;
        if (!h || h->tid == 1) continue;
        const char *imp = h->imp;
        uint64_t it0 = h->imp_t0;
        gw_tid[n] = h->tid;
        gw_imp[n] = imp;
        gw_imp_ms[n] = imp && now > it0 ? (now - it0) / 1e6f : 0;
        gw_held_ms[n] = now > h->since ? (now - h->since) / 1e6f : 0;
        gw_depth[n] = 0;
        gw_done = 0; gw_n = 0;
        if (pthread_kill(h->th, SIGUSR1) == 0)
            for (int k = 0; k < 100 && !gw_done; ++k) usleep(50);
        if (gw_done && gw_n > 2) {
            int d = gw_n - 2 > GW_DEPTH ? GW_DEPTH : gw_n - 2;
            for (int k = 0; k < d; ++k) gw_pcs[n][k] = gw_buf[k + 2];
            gw_depth[n] = (uint8_t)d;
        }
        ++n;
    }
    return NULL;
}

// ---------------- hang monitor ----------------
static void *hang_monitor(void *p) {
    (void)p;
    int secs = getenv("BOOT_DIAG_HANG") ? atoi(getenv("BOOT_DIAG_HANG")) : 5, reports = 0, stalls = 0;
    int stall_ms = getenv("BOOT_DIAG_STALL") ? atoi(getenv("BOOT_DIAG_STALL")) : 500;
    uint64_t reported_at = 0, stall_at = 0, last_report = 0;
    int same_hang = 0;
    for (unsigned tick = 1;; ++tick) {
        profile_wait_100ms();
        // stall: a frame late by more than BOOT_DIAG_STALL ms (but not yet a hang): one report while it lasts
        uint64_t t = host_ticks();
        if (stall_ms > 0 && !idle_now && last_frame_ns && frame_now > 600 && t - last_frame_ns > (uint64_t)stall_ms * 1000000ull &&
            stall_at != last_frame_ns && stalls < 5) {
            stall_at = last_frame_ns;
            char file[64], why[128];
            snprintf(file, sizeof file, "stall_%d.txt", ++stalls);
            snprintf(why, sizeof why, "stall: no frame for %d ms after frame %llu", stall_ms, (unsigned long long)frame_now);
            diag_report(file, why);
        }
        if (tick % 10) continue;                                         // the rest once a second
        if (tick % 300 == 0) {
            void boot_write_call_counts(FILE *f);
            char path[1024], tmp[1100];
            snprintf(path, sizeof path, "%s/calls.txt", diag_dir);
            snprintf(tmp, sizeof tmp, "%s.tmp", path);
            FILE *f = fopen(tmp, "w");
            if (f) { boot_write_call_counts(f); fclose(f); rename(tmp, path); }
        }
        uint64_t now = host_ticks();
        // a hang that lasts gets a second and third report 10 s apart: a thread that loops shows a different
        // stack in each, one that's blocked the same
        if (!idle_now && last_frame_ns && now - last_frame_ns > (uint64_t)secs * 1000000000ull &&
            (reported_at != last_frame_ns || (same_hang < 3 && now - last_report > 10000000000ull)) && reports < 6) {
            same_hang = reported_at == last_frame_ns ? same_hang + 1 : 1;
            reported_at = last_frame_ns;
            last_report = now;
            char file[64], why[128];
            snprintf(file, sizeof file, "hang_%d.txt", ++reports);
            snprintf(why, sizeof why, "hang: no frame for %d s after frame %llu", secs, (unsigned long long)frame_now);
            diag_report(file, why);
        }
    }
    return NULL;
}

// ---------------- frames ----------------
#define GAMEFLOW_STATE 0x00925E90u       // TheGameFlowManager's state: 3 in the front end, 6 racing
static uint64_t race_frame;              // first frame in state 6 (0: not yet)
static uint64_t key_frame;               // frame of the last scripted key press (0: none yet)
static float sim_prev = -1, sim_cur = -1;  // race sim time (s) at the last two diag_frame calls (-1: not racing yet)
void diag_scripted_key(uint64_t frame) { key_frame = frame; }
void diag_mark_key(void) { key_frame = frame_now; }   // test switches that count as a key press (x360.m)

int diag_frame_wanted(uint64_t frame, const char *list_env) {
    // Called per draw and per COM call: each variable is looked up once (callers pass string literals, so the
    // pointer identifies it), and the answer is kept for the frame.
    static const char *cached_name[4], *cached_val[4];
    static uint64_t cached_frame[4], cached_race[4], cached_key[4];
    static int cached_ans[4];
    const char *s = NULL;
    int k = 0;
    for (; k < 4 && cached_name[k]; ++k) if (cached_name[k] == list_env || !strcmp(cached_name[k], list_env)) { s = cached_val[k]; break; }
    if (k < 4 && !cached_name[k]) { cached_name[k] = list_env; s = cached_val[k] = getenv(list_env); cached_frame[k] = ~0ull; }
    if (k < 4) {
        if (!s) return 0;
        if (strstr(s, "probe") && probe_dump_frame()) return 1;   // `probe`: the probe's base and signature passes (probe.m)
        if (cached_frame[k] == frame && cached_race[k] == race_frame && cached_key[k] == key_frame) return cached_ans[k];
        cached_frame[k] = frame; cached_race[k] = race_frame; cached_key[k] = key_frame;
        cached_ans[k] = 0;
    }
    int ans = 0;
    while (s && *s && *s != ':' && !ans) {                 // a ':' ends the list (BOOT_DIAG_SEQ=<frame>:<count>)
        const char *at = s;
        if (!strncmp(s, "race+", 5)) {
            uint64_t n = strtoull(s + 5, (char **)&s, 10);
            if (race_frame && frame == race_frame + n) ans = 1;
        } else if (!strncmp(s, "key+", 4)) {
            uint64_t n = strtoull(s + 4, (char **)&s, 10);
            if (key_frame && frame == key_frame + n) ans = 1;
        } else if (!strncmp(s, "sim+", 4)) {                  // sim+S: the frame after the race sim time passes S s
            float t = strtof(s + 4, (char **)&s);
            if (sim_prev >= 0 && sim_prev < t && sim_cur >= t) ans = 1;
        } else if (!strncmp(s, "probe", 5)) {
            s += 5;                                            // handled above
        } else if (strtoull(s, (char **)&s, 10) == frame) ans = 1;
        while (*s == ',' || *s == ' ') ++s;
        if (s == at) break;                                    // not a number: stop instead of spinning
    }
    if (k < 4) cached_ans[k] = ans;
    return ans;
}
// BOOT_DIAG_SIMSHOTS=s1,s2,...: the back buffer when the race's sim time (Sim::GetTime since the race started)
// passes each value, written as simshot_<s>.png. Runs at different frame or tick rates compare at equal game time.
int diag_simshot_due(char *path, size_t n) {
    static const char *list;
    static int init;
    static float race_sim = -1;
    if (!init) { init = 1; list = getenv("BOOT_DIAG_SIMSHOTS"); }
    if (!list || !*list || !diag_on) return 0;
    float now;
    memcpy(&now, G + 0x009885D8u, 4);
    if (M32(GAMEFLOW_STATE) != 6) return 0;
    if (race_sim < 0) race_sim = now;
    char *end;
    float want = strtof(list, &end);
    if (end == list || now - race_sim < want) return 0;
    snprintf(path, n, "%s/simshot_%06.2f.png", diag_dir, want);
    while (*end == ',' || *end == ' ') ++end;
    list = end;
    if (events_f) fflush(events_f);   // a launch ended at its last simshot (SIGTERM: no exit flush) keeps its events
    return 1;
}
// Frames asked for by the runtime itself (test switches that capture at moments found while running).
static uint64_t shot_req[32];
static int nshot_req;
void diag_shot_request(uint64_t frame) { if (nshot_req < 32) shot_req[nshot_req++] = frame; }
// 2: a frame asked for with BOOT_DIAG_SHOTS or diag_shot_request (full-size PNG), 1: a periodic overview (small
// JPEG), 0: none.
int diag_shot_due(uint64_t frame) {
    if (!diag_on) return 0;
    if (diag_frame_wanted(frame, "BOOT_DIAG_SHOTS")) return 2;
    for (int i = 0; i < nshot_req; ++i) if (shot_req[i] == frame) return 2;
    return shot_every > 0 && frame % (uint64_t)shot_every == 0;
}

// Allocator counters: weak defaults (0 = not tracked); an allocator overrides them with its own definitions.
__attribute__((weak)) uint32_t gmalloc_used(void) { return 0; }
__attribute__((weak)) uint32_t heap_used(void) { return 0; }
__attribute__((weak)) void vinyls_live(uint32_t *nv, uint32_t *mbv, uint32_t *ns, uint32_t *mbs) { *nv = *mbv = *ns = *mbs = 0; }

uint64_t diag_idle_ns[IDLE_N];
static uint64_t stats_idle_ns[IDLE_N];   // diag_idle_ns at the start of stats.log's current second
PerfFrame perf_frames[PERF_RING];
volatile uint64_t perf_nframes;
float perf_gpu_ms[PERF_RING];
volatile uint64_t perf_gpu_serial;
void perf_gpu_done(uint64_t serial, double ms) {   // a frame's command buffer completed (Metal's completion thread)
    perf_gpu_ms[serial % PERF_RING] = (float)ms;
    if (serial > perf_gpu_serial) __atomic_store_n(&perf_gpu_serial, serial, __ATOMIC_RELEASE);
}
static FILE *stats_f;
static float ft[8192];                   // frame times (ms) in the current second
static int nft;
static uint64_t sec_start_ns, sec_start_frame;
static uint32_t peak_bump, peak_gm, peak_heap;
static uint64_t peak_rss;
static int cmp_float(const void *a, const void *b) { float x = *(const float *)a, y = *(const float *)b; return (x > y) - (x < y); }
static uint64_t rss_bytes(void) {
    mach_task_basic_info_data_t info;
    mach_msg_type_number_t n = MACH_TASK_BASIC_INFO_COUNT;
    return task_info(mach_task_self(), MACH_TASK_BASIC_INFO, (task_info_t)&info, &n) == KERN_SUCCESS ? info.resident_size : 0;
}
uint64_t diag_rss_bytes(void) { return rss_bytes(); }   // report.m: the session log's perf.tsv
// mem.log, every 10 s: where the process memory is, to tell a working set that plateaus from a leak. rss and
// footprint (what Activity Monitor shows: phys_footprint, includes the GPU memory the process owns); malloc (host
// heap in use, all zones); metal (MTLDevice.currentAllocatedSize: every Metal resource); the guest heaps; render.m's
// Metal copies of guest textures (tex), render targets (rt), MSAA targets, buffers, and its caches (shaders,
// pipelines, depth-stencil and sampler states); the guest's D3D objects by kind (live / retired / with a GPU copy / guest MB).
static FILE *mem_f;
static void mem_second(uint64_t frame, uint64_t now, uint32_t state) {
    if (!mem_f) {
        char path[1024];
        snprintf(path, sizeof path, "%s/mem.log", diag_dir);
        if (!(mem_f = fopen(path, "w"))) return;
    }
    task_vm_info_data_t vm;
    mach_msg_type_number_t n = TASK_VM_INFO_COUNT;
    uint64_t foot = task_info(mach_task_self(), TASK_VM_INFO, (task_info_t)&vm, &n) == KERN_SUCCESS ? vm.phys_footprint : 0;
    malloc_statistics_t ms = {0};
    malloc_zone_statistics(NULL, &ms);
    void render_mem_stats(int64_t v[16]);
    int64_t r[16];
    render_mem_stats(r);
    int64_t render_msaa_bytes(void);
    void d3d9_obj_stats(uint32_t live[16], uint32_t retired[16], uint32_t gpu[16], uint64_t bytes[16], uint32_t *hd);
    uint32_t lv[16], rt[16], gp[16], hd;
    uint64_t by[16];                       // indexed by d3d9_internal.h's enum Kind: 3 texture, 4 cube, 5 volume, 6 surface, 7 VB, 8 IB, 9 decl, 10 VS, 11 PS, 13 query
    d3d9_obj_stats(lv, rt, gp, by, &hd);
    uint32_t galloc_used(void);
    uint32_t vnv, vmv, vns, vms;
    vinyls_live(&vnv, &vmv, &vns, &vms);
    float simtime;
    memcpy(&simtime, G + 0x009885D8u, 4);
    #define MB(x) ((double)(x) / 1048576.0)
    fprintf(mem_f, "t %.0f frame %llu flow %u simtime %.0f rss %.0f footprint %.0f malloc %.0f (%u blocks) metal %.0f bump %.0f gmalloc %.0f heap %.0f | "
                   "tex %lld %.0f rt %lld %.0f msaa %.0f buf %lld %.0f renamed %lld uploads %lld shaders %lld pipelines %lld dss %lld samplers %lld | "
                   "d3d tex %u/%u/%u %.0f cube %u/%u/%u %.1f vol %u/%u/%u %.1f surf %u/%u/%u %.1f vb %u/%u/%u %.1f ib %u/%u/%u %.1f hd %u vs %u ps %u decl %u query %u | vinyls %u (%u) skins %u (%u)\n",
            now / 1e9, (unsigned long long)frame, state, simtime, MB(rss_bytes()), MB(foot), MB(ms.size_in_use), ms.blocks_in_use, MB(r[12]),
            MB(galloc_used()), MB(gmalloc_used()), MB(heap_used()),
            (long long)r[0], MB(r[1]), (long long)r[2], MB(r[3]), MB(render_msaa_bytes()), (long long)r[4], MB(r[5]), (long long)r[6], (long long)r[7],
            (long long)r[8], (long long)r[9], (long long)r[10], (long long)r[11],
            lv[3], rt[3], gp[3], MB(by[3]), lv[4], rt[4], gp[4], MB(by[4]),
            lv[5], rt[5], gp[5], MB(by[5]), lv[6], rt[6], gp[6], MB(by[6]),
            lv[7], rt[7], gp[7], MB(by[7]), lv[8], rt[8], gp[8], MB(by[8]), hd, lv[10], lv[11], lv[9], lv[13],
            vnv, vmv, vns, vms);
    #undef MB
    fflush(mem_f);
}
// The player car's speed in m/s, or -1 when there is none (front end): the sound engine's copy of it, which it keeps
// whoever drives (the player or BOOT_AUTODRIVE's AI): SndCamera::m_PLayerCars[0] (009142D0, NumPlayers 008EF998;
// IsPlayerGoingFastEnough 004DDE10 reads them) -> EAXCar +0x34 EAX_CarState -> velocity at +0x50/54/58
// (EAX_CarState::GetVelocityMagnitude 004AB890; the MPH variant 004AB8C0 scales it by 2.237). Guest memory is mapped
// whole, so a stale pointer reads garbage, not a fault.
float diag_car_speed(void) {
    if ((int32_t)M32(0x008EF998u) < 1) return -1;
    uint32_t car = M32(0x009142D0u), st = car ? M32(car + 0x34) : 0;
    if (!st) return -1;
    float v[3];
    memcpy(v, G + st + 0x50, sizeof v);
    float s = sqrtf(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]);
    return isfinite(s) && s < 1000 ? s : -1;
}

// The player car's position (EAX_CarState +0x40, as frames.log's car field) and speed; 0 when there is none.
int diag_car_state(float pos[3], float *speed) {
    pos[0] = pos[1] = pos[2] = 0; *speed = 0;
    float v = diag_car_speed();
    if (v < 0) return 0;
    uint32_t car = M32(0x009142D0u), st = car ? M32(car + 0x34) : 0;
    memcpy(pos, G + st + 0x40, 3 * sizeof(float));
    *speed = v;
    return 1;
}

static void stats_second(uint64_t frame, uint64_t now, uint32_t state) {
    void posaudio_diag_second(void);
    posaudio_diag_second();
    void mirror_diag_second(void);
    mirror_diag_second();
    lampglow_diag_second();
    void render_flare_diag_second(void);
    render_flare_diag_second();
    static unsigned nsec;
    if (nsec++ % 10 == 0) mem_second(frame, now, state);
    uint32_t galloc_used(void);
    uint32_t bump = galloc_used(), gm = gmalloc_used(), hp = heap_used();
    uint64_t rss = rss_bytes();
    if (bump > peak_bump) peak_bump = bump;
    if (gm > peak_gm) peak_gm = gm;
    if (hp > peak_heap) peak_heap = hp;
    if (rss > peak_rss) peak_rss = rss;
    qsort(ft, (size_t)nft, sizeof *ft, cmp_float);
    float p50 = nft ? ft[nft / 2] : 0, p99 = nft ? ft[(nft * 99) / 100] : 0, mx = nft ? ft[nft - 1] : 0;
    double secs = (now - sec_start_ns) / 1e9;
    void render_live_stats(int64_t *tex, int64_t *tex_mb, int64_t *buf, int64_t *buf_mb);
    int64_t lt, ltm, lb, lbm;
    render_live_stats(&lt, &ltm, &lb, &lbm);
    int dsound_mix_rms(double rms[6], uint64_t *clipped, uint64_t *limited);
    double rms[6] = {0};
    uint64_t mix_clip = 0, mix_limited = 0;
    int have_rms = dsound_mix_rms(rms, &mix_clip, &mix_limited);
    void render_frame_timing(double *wait_ms, double *gpu_ms);
    double wait_ms, gpu_ms;
    render_frame_timing(&wait_ms, &gpu_ms);
    uint32_t vnv, vmv, vns, vms;               // X360 HD vinyls/masks and composite skins alive (vinyls.m)
    vinyls_live(&vnv, &vmv, &vns, &vms);
    // busy: wall time of the game thread minus its sleeps (the game's own frame limiter), GIL hand-overs to other
    // guest threads, and waits for a drawable or the GPU, per frame
    uint64_t nfr = frame - sec_start_frame, idle = 0;
    double idle_ms[IDLE_N];
    for (int k = 0; k < IDLE_N; ++k) {
        uint64_t d = diag_idle_ns[k] - stats_idle_ns[k];
        stats_idle_ns[k] = diag_idle_ns[k];
        idle += d; idle_ms[k] = nfr ? d / 1e6 / nfr : 0;
    }
    double busy = nfr && now - sec_start_ns > idle ? (now - sec_start_ns - idle) / 1e6 / nfr : 0;
    void render_present_hist(uint64_t out[5]);
    uint64_t ph[5];
    render_present_hist(ph);
    RenderPresentation ps;
    render_presentation_stats(&ps);
    static uint64_t shown_before[5];
    uint64_t shown[5];
    for (int i = 0; i < 5; ++i) { shown[i] = ps.shown[i] - shown_before[i]; shown_before[i] = ps.shown[i]; }
    int render_msaa_samples(void);
    int64_t render_msaa_bytes(void);
    uint64_t render_msaa_resolves(void);
    double resolves = nfr ? (double)render_msaa_resolves() / nfr : 0;
    void sstr_pool_stats(uint32_t *n, uint32_t *peak_n, uint32_t *bytes, uint32_t *peak_bytes, uint32_t *size);
    uint32_t sn, spn, sb, spb, ssz;            // the game's shared string pool (file names; full = loads fail)
    sstr_pool_stats(&sn, &spn, &sb, &spb, &ssz);
    char pfx[160];                             // the DualSense's trigger modes and light colour (padfx.m), sent or not
    padfx_diag_text(pfx, sizeof pfx);
    fprintf(stats_f, "t %.1f frame %llu fps %.1f ms p50 %.2f p99 %.2f max %.2f busy %.2f ms (sleep %.2f gil %.2f drawable %.2f) "
                     "shown %llu/%llu/%llu/%llu/%llu shown_ms_8.3_16.7_25_other_drop %llu/%llu/%llu/%llu/%llu frame_limit %d present_min %.3f ms adaptive %d/%d rss %llu MB (peak %llu) bump %u KB (peak %u) gmalloc %u KB (peak %u) heap %u KB (peak %u) gputex %lld (%lld MB) gpubuf %lld (%lld MB) presentwait %.2f ms gpu %.2f ms mixrms %s%.3f %.3f %.3f %.3f %.3f %.3f clip %llu limit_samples %llu car %.1f flow %u vinyls %u (%u MB) skins %u (%u MB) msaa %dx (%lld MB, %.1f resolves) sstr %u (peak %u) %u/%u B (peak %u) padfx %s\n",
            now / 1e9, (unsigned long long)frame, secs > 0 ? (frame - sec_start_frame) / secs : 0, p50, p99, mx,
            busy, idle_ms[IDLE_SLEEP], idle_ms[IDLE_GIL], idle_ms[IDLE_DRAWABLE],
            (unsigned long long)ph[0], (unsigned long long)ph[1], (unsigned long long)ph[2], (unsigned long long)ph[3], (unsigned long long)ph[4],
            (unsigned long long)shown[0], (unsigned long long)shown[1], (unsigned long long)shown[2],
            (unsigned long long)shown[3], (unsigned long long)shown[4], ps.limit, ps.duration_ms, ps.vrr_requested, ps.adaptive_scheduling,
            (unsigned long long)(rss >> 20), (unsigned long long)(peak_rss >> 20), bump >> 10, peak_bump >> 10, gm >> 10, peak_gm >> 10, hp >> 10, peak_heap >> 10,
            (long long)lt, (long long)ltm, (long long)lb, (long long)lbm, wait_ms, gpu_ms, have_rms ? "" : "(n/a) ",
            rms[0], rms[1], rms[2], rms[3], rms[4], rms[5], (unsigned long long)mix_clip, (unsigned long long)mix_limited, diag_car_speed(), state, vnv, vmv, vns, vms,
            render_msaa_samples(), (long long)(render_msaa_bytes() >> 20), resolves, sn, spn, sb, ssz, spb, pfx);
    fflush(stats_f);
    nft = 0;
    sec_start_ns = now;
    sec_start_frame = frame;
}

// The main thread waiting for messages on purpose (an inactive window, user32.m wait_messages): no stall or hang
// reports meanwhile, and frame times restart when the next frame comes.
void diag_idle(int idle) {
    if (idle == idle_now) return;
    idle_now = idle;                     // always: report.m's hang watchdog runs in normal play too
    if (diag_on) diag_event(idle ? "main thread idle: waiting for messages" : "main thread drawing again");
    last_frame_ns = host_ticks();
}
uint64_t diag_last_frame_ns(void) { return last_frame_ns; }
int diag_is_idle(void) { return idle_now; }

// Scripted race characters (BOOT_DIAG, races): GManager::mObj (0x0091E00C) keeps the GCharacters that
// GCharacter::Spawn (00601320) has asked for in a vector at +0x80..+0x84; ServicePendingCharacters (005FDD60) walks it
// and FUN_005FD820 creates each one's PVehicle (state +0x4C: 1 asked, 2 vehicle made, 3 waiting to be placed). The
// drag races' trafficspawntrigger collections spawn their child `character` this way (a traffic car at the trigger's
// position), not through AITrafficManager::SpawnTraffic (00439A10), which is the density-driven ambient traffic.
// Logs each character the first time it is in the list (position, speed) and every change of the list's states.
static void gchar_watch(void) {
    static uint32_t seen[256], nseen, last_sig = 0xFFFFFFFFu;
    uint32_t gm = M32(0x0091E00Cu);
    if (!gm) return;
    uint32_t b = M32(gm + 0x80), e = M32(gm + 0x84);
    if (!b || e < b || e - b > 4 * 256) return;
    uint32_t n = (e - b) / 4, hist[5] = {0};
    float simt; memcpy(&simt, G + 0x009885D8u, 4);
    for (uint32_t i = 0; i < n; ++i) {
        uint32_t ch = M32(b + 4 * i);
        if (!ch) continue;
        uint8_t s = M8(ch + 0x4C);
        ++hist[s < 4 ? s : 4];
        uint32_t k = 0;
        while (k < nseen && seen[k] != ch) ++k;
        if (k == nseen && nseen < 256) {
            seen[nseen++] = ch;
            float pos[3], v; memcpy(pos, G + ch + 0x40, sizeof pos); memcpy(&v, G + ch + 0x5C, 4);
            diag_event("gchar: character %08X asked to spawn at sim %.2f: position %.1f %.1f %.1f, speed %.1f m/s, state %u (%u characters so far)",
                       ch, simt, pos[0], pos[1], pos[2], v, s, nseen);
        }
    }
    uint32_t sig = n | hist[1] << 8 | hist[2] << 14 | hist[3] << 20 | hist[4] << 26;
    if (sig != last_sig) {
        diag_event("gchar: spawn list %u (state0 %u asked %u made %u placing %u other %u) at sim %.2f", n, hist[0], hist[1], hist[2], hist[3], hist[4], simt);
        last_sig = sig;
    }
}
void diag_frame(uint64_t frame, uint64_t draws) {
    diag_idle(0);
    uint64_t now = host_ticks(), prev = last_frame_ns;
    frame_now = frame;
    last_frame_ns = now;
    static uint32_t state = 0xFFFFFFFFu;
    uint32_t st = M32(GAMEFLOW_STATE);
#ifdef NFS_PUBLIC
    { void pub_discord_tick(uint32_t); pub_discord_tick(st); }   // pub_discord.c: public build only
#endif
    if (!have_game_thread) { game_thread = pthread_self(); have_game_thread = 1; }
    if (st != state) {
        if (diag_on) diag_event("gameflow state %u -> %u", state, st);
        report_note("game-flow state %d -> %u", (int)state, st);
        if (st == 6 && timeline_race0 < 0) memcpy(&timeline_race0, G + 0x009885D8u, 4);
        diag_timeline("gameflow %u -> %u", state, st);
        if (frames_f) fprintf(frames_f, "gameflow %u -> %u at frame %llu\n", state, st, (unsigned long long)frame);
        if (st == 6 && !race_frame) race_frame = frame;
        state = st;
    }
    if (st == 6 && timeline_race0 >= 0) {                     // sim+S in the frame lists
        float t; memcpy(&t, G + 0x009885D8u, 4);
        sim_prev = sim_cur; sim_cur = t - timeline_race0;
    }
    if (diag_on && st == 6) gchar_watch();
    void dsound_diag_tick(void);            // dsound.c: bounded buffer/refill snapshots, including during pause
    if (diag_on) dsound_diag_tick();
    void dsound_watchdog_tick(void);        // dsound.c: audio-loop watchdog, always on, one check per 250 ms
    dsound_watchdog_tick();
    // sim: the game's sim ticks so far (Scheduler::fgScheduler->ticks); frames that add none show the same world
    // state again.
    uint32_t sched = M32(0x009203B0u), ticks = sched ? M32(sched + 0x28) : 0;
    {
        // the always-on per-frame record (stats.log and the overlay): busy = the frame minus the game thread's waits
        static uint64_t idle_prev[IDLE_N];
        static uint32_t ticks_prev;
        PerfFrame *p = &perf_frames[perf_nframes % PERF_RING];
        float fms = prev ? (float)((now - prev) / 1e6) : 0, idle = 0;
        for (int k = 0; k < IDLE_N; ++k) {
            p->idle_ms[k] = prev ? (float)((diag_idle_ns[k] - idle_prev[k]) / 1e6) : 0;
            idle_prev[k] = diag_idle_ns[k];
            idle += p->idle_ms[k];
        }
        p->frame_ms = fms;
        p->busy_ms = fms > idle ? fms - idle : 0;
        p->draws = (uint32_t)draws;
        p->ticks = ticks >= ticks_prev ? ticks - ticks_prev : 0;
        ticks_prev = ticks;
        __atomic_store_n(&perf_nframes, perf_nframes + 1, __ATOMIC_RELEASE);
    }
    report_frame(frame, st);                // report.m: the always-on per-second ring (problem reports)
    overlay_frame();
    portmenu_frame();                       // portmenu.m: the F10 menu's layer
    void prompts_diag_frame(void);
    if (diag_on) prompts_diag_frame();      // padprompts.c: FE pad events
    if (timeline_f) {                       // what FEngHud::DetermineHudFeatures waits for (the HUD stays off until they clear)
        static uint32_t w[3] = {~0u, ~0u, ~0u};
        uint32_t v[3] = {M32(0x009885C8u), M32(0x008EE348u), M8(0x0091CA58u)};
        static const char *wn[3] = {"INIS instance", "TheICEManager", "hud-off flag 0091CA58"};
        for (int k = 0; k < 3; ++k) if (v[k] != w[k]) { diag_timeline("watch %s %08X -> %08X", wn[k], w[k], v[k]); w[k] = v[k]; }
        // Sim time scale (SimSystem::UpdateFrame 006F6CF0: dt = speed * step, speed from QuickGame::OnManageTime
        // 006F4D10): Tweak_GameSpeed 00901B1C if != 1, else the player's Speedbreaker (vtable +0x40) -> 0.25, else the
        // view camera's SimTimeMultiplier ([00919690]+0xCC, written by camera movers). Logged when one changes by > 0.005.
        static float tw[3] = {-1, -1, -1};
        uint32_t sys = M32(0x009885E0u), cam = M32(0x00919690u);
        float tv[3] = {-2, -2, -2};
        memcpy(&tv[0], G + 0x00901B1Cu, 4);
        if (sys) memcpy(&tv[1], G + sys + 0x24, 4);
        if (cam) memcpy(&tv[2], G + cam + 0xCC, 4);
        static const char *tn[3] = {"Tweak_GameSpeed", "Sim speed", "camera SimTimeMultiplier"};
        for (int k = 0; k < 3; ++k) if (fabsf(tv[k] - tw[k]) > 0.005f) { diag_timeline("watch %s %.4f -> %.4f", tn[k], tw[k], tv[k]); tw[k] = tv[k]; }
        // Sim state (SimSystem+0x2C from IStateManager): sim time only advances in state 3 (006F6CF0).
        static uint32_t sw = ~0u;
        uint32_t sv = sys ? M32(sys + 0x2C) : ~1u;
        if (sv != sw) { diag_timeline("watch Sim state %d -> %d", (int)sw, (int)sv); sw = sv; }
    }
    uint32_t galloc_used(void);
    extern unsigned diag_car1[4];
    int mat_water_reflection_requested(void);
    if (frames_f) {
        // view: hash of the D3D view matrix, to see whether the camera moved.
        uint32_t view_hash(void);
        float simtime;
        memcpy(&simtime, G + 0x009885D8u, 4);                       // Sim::GetTime (_mTime, seconds of sim time)
        float simspeed = 0, camrate = 0;                           // sim time scale (see the timeline watch above)
        uint32_t sys = M32(0x009885E0u), cam = M32(0x00919690u);
        if (sys) memcpy(&simspeed, G + sys + 0x24, 4);
        if (cam) memcpy(&camrate, G + cam + 0xCC, 4);
        // car: the player car's position (EAX_CarState +0x40, beside the velocity at +0x50: diag_car_speed) and speed,
        // to tell a camera that differs between runs from a car that does (refs.sh noise)
        float cp[3] = {0, 0, 0};
        uint32_t pc = (int32_t)M32(0x008EF998u) >= 1 ? M32(0x009142D0u) : 0, pst = pc ? M32(pc + 0x34) : 0;
        if (pst) memcpy(cp, G + pst + 0x40, sizeof cp);
        // fmem: the game's frame-malloc buffer (eInitEngine 0x64000 per buffer; eSwapFrameMallocBuffers 004FAD80):
        // this frame's use so far (CurrentBufferPos 00915F84 - CurrentBufferStart 00915F80), the last swapped frame's
        // use (Eframecurrent 00915FA4), this frame's failed bytes (FrameMallocFailAmount 00915F98) and the run's worst
        // (FrameMallocMaxFailAmount 00915F9C). A failed alloc makes CarRenderInfo::Render (0074E160) skip the car.
        fprintf(frames_f, "frame %llu draws %llu t %.4f bumpheap %u KB sim %u view %08X simtime %.4f simspeed %.3f camrate %.3f car %.4f %.4f %.4f %.4f "
                "fmem %u last %u size %u fail %u maxfail %u cars1 %u ok %u fmfail %u cars4 %u tunnel %u wrefl %d\n", (unsigned long long)frame, (unsigned long long)draws,
                now / 1e9, galloc_used() >> 10, ticks, view_hash(), simtime, simspeed, camrate, cp[0], cp[1], cp[2], diag_car_speed(),
                M32(0x00915F84u) - M32(0x00915F80u), M32(0x00915FA4u), M32(0x00915F7Cu), M32(0x00915F98u), M32(0x00915F9Cu),
                diag_car1[0], diag_car1[1], diag_car1[2], diag_car1[3], M32(0x00982BA4u), mat_water_reflection_requested());
        memset(diag_car1, 0, sizeof diag_car1);
        fflush(frames_f);
    }
    if (!stats_f) return;
    if (prev && nft < 8192) ft[nft++] = (float)((now - prev) / 1e6);
    if (!sec_start_ns) { sec_start_ns = now; sec_start_frame = frame; memcpy(stats_idle_ns, diag_idle_ns, sizeof stats_idle_ns); }
    if (now - sec_start_ns >= 1000000000ull) stats_second(frame, now, st);
}
uint64_t diag_current_frame(void) { return frame_now; }

void diag_init(void) {
    diag_dir = getenv("BOOT_DIAG");
    if (!diag_dir) {
        signal(SIGSEGV, crash_signal); signal(SIGBUS, crash_signal);   // reports go to log_dir() without BOOT_DIAG (NFSMW-crash.txt)
        return;
    }
    diag_on = 1;
    mkdir(diag_dir, 0755);
    char path[1024];
    snprintf(path, sizeof path, "%s/events.log", diag_dir);
    events_f = fopen(path, "w");
    if (events_f) setvbuf(events_f, NULL, _IOFBF, 1 << 20);
    snprintf(path, sizeof path, "%s/frames.log", diag_dir);
    frames_f = fopen(path, "w");
    snprintf(path, sizeof path, "%s/stats.log", diag_dir);
    stats_f = fopen(path, "w");
    if (getenv("BOOT_DIAG_SHOT_EVERY")) shot_every = atoi(getenv("BOOT_DIAG_SHOT_EVERY"));
    if (getenv("BOOT_DIAG_TIMELINE")) timeline_secs = (float)atof(getenv("BOOT_DIAG_TIMELINE"));
    snprintf(path, sizeof path, "%s/timeline.log", diag_dir);
    if (timeline_secs > 0) timeline_f = fopen(path, "w");
    if (timeline_f) setvbuf(timeline_f, NULL, _IOFBF, 1 << 16);
    signal(SIGUSR2, bt_signal);
    signal(SIGSEGV, crash_signal); signal(SIGBUS, crash_signal); signal(SIGILL, crash_signal); signal(SIGFPE, crash_signal);
    pthread_t t;
    pthread_create(&t, NULL, hang_monitor, NULL);
    pthread_detach(t);
    if (getenv("BOOT_DIAG_PROFILE_MAX")) prof_max = atoi(getenv("BOOT_DIAG_PROFILE_MAX"));
    gw_t0 = host_ticks();
    signal(SIGUSR1, gw_signal);
    pthread_create(&t, NULL, gil_watch, NULL);
    pthread_detach(t);
    load_symbols();
    fprintf(stderr, "boot: diagnostic mode: writing to %s (%d game symbols)\n", diag_dir, nsyms);
}
void diag_flush(void) { if (events_f) fflush(events_f); }

// ---------------- NIS car grounding (BOOT_DIAG only) ----------------
// Observe 0044B040 -> 00789870 / 00691110, without issuing another ground query.
// Bounds: 512 samples/actor/play, 16384 samples/run, 64 Load/Play metadata lines/run.
typedef struct {
    float last, sumdt, mindt, maxdt;
    unsigned lines, calls, ground_fail, init_fail;
} NisActor;
typedef struct {
    int due, ground, init, ret, wheels;
    uint32_t control;
    float height, query[3], target[3], pos[3], vy;
} NisSample;
static NisActor nis_actor[16];
static _Thread_local NisSample *nis_capture;
static uint32_t nis_activity, nis_hash;
static char nis_name[64];
static unsigned nis_play, nis_lines, nis_meta;
static int nis_playing;
static uint32_t nis_scratch;             // private guest stack + query/iterator scratch, diag only

// Read-only getters on a CPU copy/private stack preserve the live registers, x87 and caller's stack.
// Never request a load, zone switch or additional ground query.
static uint32_t nis_read(Cpu *c, uint32_t self, uint32_t fn, int n, const uint32_t *args) {
    if (!nis_scratch) nis_scratch = galloc(0x4100);
    Cpu q = *c;
    q.esp = nis_scratch + 0x4000;
    q.ecx = self;
    return call_guest(&q, fn, n, args);
}
static uint32_t nis_header(int section) { // detail.c's scenery test, distinct from collision below
    if (section < 0 || section >= 0xA8C) return 0;
    uint32_t e = M32(0x009A3BB0u + 4u * (uint32_t)section);
    return e ? M32(e + 4) : 0;
}
static void nis_residency(Cpu *c, const float pos[3], int *zone, int *loaded, char *packs, size_t size) {
    *zone = *loaded = -1;
    snprintf(packs, size, "unknown");
    if (!isfinite(pos[0]) || !isfinite(pos[1]) || !isfinite(pos[2])) return;
    if (!nis_scratch) nis_scratch = galloc(0x4100);
    uint32_t point = nis_scratch + 0x4000, rc = point + 16, it = point + 32;
    // bConvertToBond 0045E1A0: native (x,y,z) -> (-y,z,x); streamer's native XY = Bond Z,-X.
    MF32(point) = pos[2]; MF32(point + 4) = -pos[0];
    uint32_t d = nis_read(c, 0x009A3B50u, 0x0072CA00u, 1, &point);
    *zone = d ? (int16_t)M16(d + 0xC) : -1;
    *loaded = *zone < 0 ? -1 : nis_header(*zone) != 0;
    // Actual grid-cell collision references include neighbours/global packs, not just pack[zone].
    // Mirrors GetInstanceListGuts 00783B80's iterator setup/pack lookup; no instance mutations.
    uint32_t grid = M32(0x009B38A8u), list = M32(0x009B38A0u);
    if (!grid || !list || !M32(grid + 0x18) || !M32(grid + 0x1C) || !M32(grid + 0x20)) return;
    memcpy(G + point, pos, 12);
    uint32_t args[3] = {point, rc, rc + 4};
    nis_read(c, grid, 0x00763690u, 3, args); // GetRowCol: row(Z), col(X), clamped as in FindNodesBox
    uint32_t node = M32(M32(grid + 0x20) + 4 * (M32(rc) * M32(grid + 0x1C) + M32(rc + 4)));
    packs[0] = 0;
    if (!node) { snprintf(packs, size, "none"); return; }
    memset(G + it, 0, 24);
    M32(it + 4) = node;
    M32(it + 8) = M8(node + 8);
    if (M32(it + 8)) { M32(it + 12) = node + 0x14 + M16(node + 12); M8(it + 16) = 1; }
    uint32_t tree = M32(node);
    if (tree) { M32(it + 20) = M32(M32(tree + 4)); M8(it + 16) = 1; }
    uint16_t seen[32];
    unsigned ns = 0, k;
    for (k = 0; k < 4096; ++k) {
        uint32_t ind = nis_read(c, it, 0x0077A740u, 0, NULL);
        if (!ind) break;
        uint32_t section = M32(ind) >> 16;
        unsigned j = 0;
        while (j < ns && seen[j] != section) ++j;
        if (j < ns) continue;
        if (ns == 32 || strlen(packs) + 16 >= size) break;
        seen[ns++] = (uint16_t)section;
        snprintf(packs + strlen(packs), size - strlen(packs), "%s%u:%d", ns == 1 ? "" : ",", section,
                 section < 0xA8C ? M32(list + 4 * section) != 0 : -1);
    }
    if (k == 4096 || ns == 32) strlcat(packs, ",truncated", size);
    if (!*packs) snprintf(packs, size, "none");
}

void f_006F51F0_orig(Cpu *c);
void f_006F51F0(Cpu *c) {               // NISActivity::Load(kind, scene name, ...)
    // BOOT_NIS=<scene> (test only, inert without it): the race's own "IntroNis.." scene is swapped for the named one,
    // so a scene the plain race route never reaches (Blacklist rivals' IntroNisBL12) can be played and inspected.
    static const char *force; static int force_init;
    if (!force_init) { force_init = 1; force = getenv("BOOT_NIS"); if (force && !*force) force = NULL; }
    if (force) {
        uint32_t nm = arg(c, 1);
        // an "Arrest.." name likewise replaces the bust's arrest scene (the game picks one at random)
        int arrest = nm && !strncmp(force, "Arrest", 6) && !strncmp(gptr(nm), "Arrest", 6);
        if (nm && (!strncmp(gptr(nm), "IntroNis", 8) || arrest) && strcmp(gptr(nm), force)) {
            static uint32_t forced;
            if (!forced) { forced = galloc(64); snprintf(gptr(forced), 64, "%s", force); }
            fprintf(stderr, "BOOT_NIS: scene %s -> %s\n", gptr(nm), force);
            M32(c->esp + 8) = forced;
        }
    }
    if (!diag_on) { f_006F51F0_orig(c); return; }
    // Load is entered through INIS at root+48; Play/UpdatePlaying use the activity root.
    nis_activity = c->ecx - 0x48;
    nis_playing = 0;
    uint32_t name = arg(c, 1);
    snprintf(nis_name, sizeof nis_name, "%.63s", name ? gptr(name) : "(null)");
    f_006F51F0_orig(c);
    nis_hash = M32(nis_activity + 0x394); // Load's this+34C: game's own hash
    if (nis_meta < 64) {
        ++nis_meta;
        diag_event("nis: load name %s hash %08X activity %08X scene %08X", nis_name, nis_hash,
                   nis_activity, M32(nis_activity + 0x39C));
    }
}
void f_006F5750_orig(Cpu *c);
void f_006F5750(Cpu *c) {               // retains the timeline milestone formerly emitted by the macro
    if (diag_on) {
        diag_timeline("call NISActivity::Play from %08X", M32(c->esp));
        if (c->ecx == nis_activity) {
            memset(nis_actor, 0, sizeof nis_actor);
            ++nis_play;
            nis_playing = 1;
            if (nis_meta < 64) {
                ++nis_meta;
                diag_event("nis: play %u name %s hash %08X scene %08X", nis_play, nis_name, nis_hash,
                           M32(nis_activity + 0x39C));
            }
        }
    }
    f_006F5750_orig(c);
}
void f_00789870_orig(Cpu *c);
void f_00789870(Cpu *c) {               // original rigorous query within 0044B040 only
    NisSample *s = diag_on ? nis_capture : NULL;
    if (!s || M32(c->esp) != 0x0044B0C2u) { f_00789870_orig(c); return; }
    uint32_t height = arg(c, 1);
    memcpy(s->query, G + arg(c, 0), sizeof s->query);
    f_00789870_orig(c);
    s->ground = (c->eax & 255) != 0;
    s->height = s->ground ? MF32(height) : NAN;
}
void f_00691110_orig(Cpu *c);
void f_00691110(Cpu *c) {               // SuspensionSpline::SetNISPosition(matrix, init, dt)
    NisSample *s = diag_on ? nis_capture : NULL;
    if (!s) { f_00691110_orig(c); return; }
    s->control = c->ecx;
    s->init = (arg(c, 1) & 255) != 0;
    memcpy(s->target, G + arg(c, 0) + 0x30, sizeof s->target);
    f_00691110_orig(c);
    s->ret = (c->eax & 255) != 0;
    if (!s->due) return;
    // INISCarControl=root+90, ISuspension=root+4C; GetNumWheelsOnGround 006AE420 reads ISuspension+98.
    s->wheels = (int32_t)M32(s->control + 0x54);
    uint32_t rb = M32(s->control + 0x44); // same IRigidBody used by the original function
    if (!rb) return;
    uint32_t p = nis_read(c, rb, M32(M32(rb) + 0x20), 0, NULL);
    if (p) memcpy(s->pos, G + p, sizeof s->pos);
    uint32_t v = nis_read(c, rb, M32(M32(rb) + 0x24), 0, NULL);
    if (v) s->vy = MF32(v + 4);
}
void f_0044B040_orig(Cpu *c);
void f_0044B040(Cpu *c) {
    if (!diag_on || !nis_activity || !nis_playing || nis_lines >= 16384 ||
        c->ecx != M32(nis_activity + 0x39C) || M32(nis_activity + 0x2F4) != 5) {
        f_0044B040_orig(c); return;
    }
    uint32_t actor = arg(c, 0), ctrl = arg(c, 1), scene = c->ecx;
    if (actor >= 16 || !ctrl || nis_actor[actor].lines >= 512) { f_0044B040_orig(c); return; }
    NisActor *a = &nis_actor[actor];
    float st = MF32(scene + 0x20), dt = argf(c, 2), sample = MF32(ctrl + 0x68), rate = MF32(ctrl + 0x60);
    int due = isfinite(st) && (!a->lines || st - a->last >= 0.25f);
    if (!a->calls) a->mindt = a->maxdt = dt;
    a->sumdt += dt;
    a->mindt = fminf(a->mindt, dt); a->maxdt = fmaxf(a->maxdt, dt); ++a->calls;
    uint32_t vehicle = M32(0x0090DBA4u + 16 * actor), before = M32(0x0090DBA8u + 16 * actor);
    float anim[3] = {NAN, NAN, NAN};
    uint32_t pose = M32(ctrl + 0x2C);
    if (pose) memcpy(anim, G + pose + 0x70, sizeof anim);
    NisSample s = {.due = due, .ground = -1, .init = -1, .ret = -1, .wheels = -1, .height = NAN,
                   .query = {NAN, NAN, NAN}, .target = {NAN, NAN, NAN}, .pos = {NAN, NAN, NAN}, .vy = NAN};
    NisSample *prev = nis_capture;
    nis_capture = &s;
    f_0044B040_orig(c);
    nis_capture = prev;
    a->ground_fail += s.ground == 0;
    a->init_fail += s.init == 1 && s.ret == 0;
    if (!due) return;
    int zone, loaded;
    char packs[384];
    nis_residency(c, s.pos, &zone, &loaded, packs, sizeof packs);
    uint32_t sched = M32(0x009203B0u);
    diag_event("nis: sample play %u name %s hash %08X scene %08X time %.4f actor %u vehicle %08X ctrl %08X "
               "anim %.4f rate %.4f animpos_native %.3f,%.3f,%.3f dt %.6f calls %u sumdt %.6f dt_range %.6f,%.6f "
               "ground %d height %.3f query %.3f,%.3f,%.3f ground_fail %u init %d ret %d init_fail %u count %u->%u "
               "target %.3f,%.3f,%.3f control %08X pos %.3f,%.3f,%.3f vy %.4f wheels %d zone %d loaded %d packs %s "
               "sim %u simtime %.4f simspeed %.3f camrate %.3f t %.4f",
               nis_play, nis_name, nis_hash, scene, st, actor, vehicle, ctrl, sample, rate, anim[0], anim[1], anim[2],
               dt, a->calls, a->sumdt, a->mindt, a->maxdt, s.ground, s.height, s.query[0], s.query[1], s.query[2],
               a->ground_fail, s.init, s.ret, a->init_fail, before, M32(0x0090DBA8u + 16 * actor),
               s.target[0], s.target[1], s.target[2], s.control, s.pos[0], s.pos[1], s.pos[2], s.vy, s.wheels,
               zone, loaded, packs, sched ? M32(sched + 0x28) : 0, MF32(0x009885D8u),
               M32(0x009885E0u) ? MF32(M32(0x009885E0u) + 0x24) : NAN,
               M32(0x00919690u) ? MF32(M32(0x00919690u) + 0xCC) : NAN, host_ticks() / 1e9);
    a->last = st; ++a->lines; ++nis_lines;
    a->calls = a->ground_fail = a->init_fail = 0; a->sumdt = 0;
}

// ---------------- timeline: loading and the race start in game time ----------------
// <dir>/timeline.log: every game event the Event queue fires (FUN_00633EF0: vtable[0] of each queued Event, i.e.
// its type), every Hermes message posted (kind and channel CRCs), sim tasks created (rate and delay, in ticks), slow
// resource-loading steps and a few milestones (game-flow states, gameplay begin, NIS play/skip, countdown), each with
// the frame, the sim tick, Sim::GetTime, the time since the race started (the first frame in game-flow state 6),
// wall time and the game's RealTimer/WorldTimer (1/4000 s). Runs at different tick rates line up on these columns:
// whatever starts at a different game time after loading counts ticks or frames somewhere. Racing lines stop 60 s
// into the race (or BOOT_DIAG_TIMELINE=<seconds>; 0 = no timeline).
static int timeline_lines;
int diag_timeline_on(void) {
    if (!timeline_f || timeline_lines > 400000) return 0;
    if (timeline_race0 < 0) return 1;
    float now;
    memcpy(&now, G + 0x009885D8u, 4);
    return now - timeline_race0 < timeline_secs;
}
void diag_timeline(const char *fmt, ...) {
    if (!diag_timeline_on()) return;
    uint32_t sched = M32(0x009203B0u), ticks = sched ? M32(sched + 0x28) : 0;
    float now;
    memcpy(&now, G + 0x009885D8u, 4);
    char msg[512];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(msg, sizeof msg, fmt, ap);
    va_end(ap);
    pthread_mutex_lock(&file_mu);
    fprintf(timeline_f, "f%llu tick %u sim %.4f race %8.4f t %.4f rt %u wt %d flow %u  %s\n", (unsigned long long)frame_now, ticks, now,
            timeline_race0 < 0 ? -1.0f : now - timeline_race0, host_ticks() / 1e9, M32(0x00925B14u), (int32_t)M32(0x00925AE8u),
            M32(GAMEFLOW_STATE), msg);
    ++timeline_lines;
    pthread_mutex_unlock(&file_mu);
}
static const char *timeline_name(uint32_t fn, char *buf, size_t n) {
    uint32_t off = 0;
    const char *s = diag_symbol(fn, &off);
    if (s && !off) return s;
    snprintf(buf, n, "%08X", fn);
    return buf;
}
uint32_t call_guest(Cpu *c, uint32_t fn, int n, const uint32_t *args);
// The Event queue (cdecl, called after every tick and frame): the same loop as the game's, naming each event.
void f_00633EF0_orig(Cpu *c);
void f_00633EF0(Cpu *c) {
    if (!diag_timeline_on()) { f_00633EF0_orig(c); return; }
    const uint32_t one = 1;
    char buf[16];
    for (uint32_t p; (p = M32(0x00920394u)) < M32(0x00920390u);) {   // gDeletionPoint < gCreationPoint
        M32(0x0092039Cu) = p;
        if (p) {
            uint32_t fn = M32(M32(p)), esp = c->esp;
            diag_timeline("event %s", timeline_name(fn, buf, sizeof buf));
            c->ecx = p;
            call_guest(c, fn, 1, &one);                              // fires and destroys it, advancing gDeletionPoint
            c->esp = esp;
        }
        M32(0x0092039Cu) = 0;
    }
    M32(0x00920390u) = M32(0x00920394u) = M32(0x0092038Cu);        // back to gMemoryBuffer
    c->esp += 4;                                                    // ret
}
// Hermes::Message::Post(UCrc32 channel) (thiscall; the message's kind CRC at +0)
void f_00654AF0_orig(Cpu *c);
void f_00654AF0(Cpu *c) {
    if (diag_timeline_on()) diag_timeline("message %08X on %08X (from %08X)", M32(c->ecx), M32(c->esp + 4), M32(c->esp));
    f_00654AF0_orig(c);
}
// Milestones: logged with their caller.
#define TIMELINE_HOOK(addr)                                                                                        \
    void f_##addr##_orig(Cpu *c);                                                                                  \
    void f_##addr(Cpu *c) {                                                                                        \
        if (diag_timeline_on()) {                                                                                  \
            char b1[16], b2[16];                                                                                   \
            uint32_t off = 0;                                                                                      \
            const char *from = diag_symbol(M32(c->esp), &off);                                                     \
            diag_timeline("call %s from %s", timeline_name(0x##addr##u, b1, sizeof b1), from ? from : timeline_name(M32(c->esp), b2, sizeof b2)); \
        }                                                                                                          \
        f_##addr##_orig(c);                                                                                        \
    }
TIMELINE_HOOK(00567F10)   // Countdown::BeginCountdown
TIMELINE_HOOK(006EA960)   // NISActivity::SkipOverNIS
TIMELINE_HOOK(006EF530)   // NISActivity::OnMovieComplete
TIMELINE_HOOK(00603500)   // GManager::BeginGameplay
TIMELINE_HOOK(006035F0)   // GManager::PreBeginGameplay
TIMELINE_HOOK(00654030)   // ResetWorldTime
// Car rendering in BOOT_DIAG_DRAWS frames, written into the frame's draw dump (`car:` lines, before the flush that
// draws them): every CarRenderInfo::Render (0074E160: view, flags, reflection flag, LOD range, result) and RenderPart
// (007387E0: the part slot as its offset in the CarRenderInfo, decoded for the body parts table +0xB84 (part x 0x14 +
// LOD x 4), the eModel's name hash, the part matrix's up axis and position), so each view's car parts and LODs show.
unsigned diag_car1[4];   // this frame: view-1 CarRenderInfo::Render calls, true, frame-malloc failed during it; view-4 calls
static inline float car_f32(uint32_t a) { float f; memcpy(&f, G + a, 4); return f; }
FILE *dump_file(void);
void f_0074E160_orig(Cpu *c);
void f_0074E160(Cpu *c) {   // thiscall bool Render(view, pos, m, m, m, m, flags, p8, reflection, float, min LOD, max LOD)
    FILE *df = diag_on ? dump_file() : NULL;
    uint32_t self = c->ecx, view = arg(c, 0);
    if (df) fprintf(df, "car: Render %08X view %u flags %X p8 %u refl %u lod %d..%d (+0x1628 %d +0x162C %d) from %08X\n", self,
                    M32(view + 4), arg(c, 6), arg(c, 7), arg(c, 8), (int)arg(c, 10), (int)arg(c, 11), (int)M32(self + 0x1628),
                    (int)M32(self + 0x162C), M32(c->esp));
    uint32_t fail0 = M32(0x00915F98u), vid = M32(view + 4);
    f_0074E160_orig(c);
    if (df) fprintf(df, "car: Render %08X view %u -> %u\n", self, M32(view + 4), c->eax & 0xFF);
    if (vid == 1) { ++diag_car1[0]; diag_car1[1] += c->eax & 1; diag_car1[2] += M32(0x00915F98u) != fail0; }   // frames.log `cars1`
    else if (vid == 4) ++diag_car1[3];
}
void f_007387E0_orig(Cpu *c);
void f_007387E0(Cpu *c) {   // thiscall void RenderPart(view, CarPartModel *, bMatrix4 *, eDynamicLightContext *, flags)
    FILE *df = diag_on ? dump_file() : NULL;
    if (df) {
        uint32_t self = c->ecx, slot = arg(c, 1), m = arg(c, 2), off = slot - self, model = slot ? M32(slot) & ~3u : 0;
        char part[48] = "";
        if (off >= 0xB84 && off < 0xB84 + 0x4C * 0x14) snprintf(part, sizeof part, " part %02X lod %u", (off - 0xB84) / 0x14, (off - 0xB84) % 0x14 / 4);
        fprintf(df, "car: RenderPart %08X view %u slot +%X%s model %08X hash %08X flags %X up %.3f %.3f %.3f pos %.2f %.2f %.2f\n", self,
                M32(arg(c, 0) + 4), off, part, model, model ? M32(model + 8) : 0, arg(c, 4), m ? car_f32(m + 32) : 0, m ? car_f32(m + 36) : 0,
                m ? car_f32(m + 40) : 0, m ? car_f32(m + 48) : 0, m ? car_f32(m + 52) : 0, m ? car_f32(m + 56) : 0);
    }
    f_007387E0_orig(c);
}
// Countdown::Update (the HUD's 3-2-1, per HUD update): its first call per HUD and every change of its digit (+0x44)
void f_005679B0_orig(Cpu *c);
void f_005679B0(Cpu *c) {
    if (!diag_timeline_on()) { f_005679B0_orig(c); return; }
    static uint32_t last_obj, last_digit;
    uint32_t self = c->ecx, before = M32(self + 0x44);
    if (self != last_obj) { last_obj = self; last_digit = before; diag_timeline("Countdown::Update first call, digit %d", (int32_t)before); }
    f_005679B0_orig(c);
    uint32_t after = M32(self + 0x44);
    if (after != last_digit) { diag_timeline("Countdown::Update digit %d -> %d", (int32_t)last_digit, (int32_t)after); last_digit = after; }
}
// FEngHud::DetermineHudFeatures (0057CA60, thiscall, arg IPlayer*; hooked in pacing.c, which calls this after it):
// the HUD's elements (the countdown among them) only update while it returns non-zero. Logs the number of calls per
// HUD at each change of the result, and while it is 0, which of its conditions fail (bits, in the order it tests
// them): 1 player slot, 2 restarting, 4 pause request, 8 Sim state != 3, 0x10/0x20/0x40 Pause_Main/MC_Main/
// PostRace_Results pushed, 0x80 hud-off flag, 0x100 HUD resources not loaded, 0x200 INIS, 0x400 HUD type !=
// FEDatabase's, 0x800 ICE manager, 0x1000 game flow loading; 0x8000: held off by pacing.c (the 60 Hz set-up time).
// c->eax:edx hold the result the game sees; the registers are restored after the checks.
void diag_hud_features(Cpu *c, uint32_t self, uint32_t player, int held) {
    if (!diag_timeline_on()) return;
    static uint32_t hud, calls, last_lo = ~0u, last_hi = ~0u, last_mask = ~0u;
    if (self != hud) { hud = self; calls = 0; last_lo = last_hi = last_mask = ~0u; }
    ++calls;
    uint32_t lo = c->eax, hi = c->edx, mask = held ? 0x8000 : 0;
    if (!lo && !hi && !held) {
        Cpu save = *c;
        c->ecx = player;
        uint32_t idx = call_guest(c, M32(M32(player) + 0x2C), 0, NULL);
        uint32_t slot = 0x009195E0u + idx * 0x70u + 0x44u, p = M32(slot);
        if (p == slot || !p || p == 4 || M32(p - 4 + 0xC) != 1) mask |= 1;
        else { c->ecx = p - 4; if ((uint16_t)call_guest(c, M32(M32(p - 4) + 0x30), 0, NULL)) mask |= 1; }
        uint32_t rs = M32(0x0091E000u);
        if (rs && M32(rs + 0x1960) == 1 && M8(0x0091CF28u)) mask |= 2;
        if ((int32_t)M32(0x0091CAE4u) > 0) mask |= 4;
        if (call_guest(c, 0x006E8D20u, 0, NULL) != 3) mask |= 8;
        static const uint32_t pkg[3] = {0x00897464u, 0x0089AFBCu, 0x0089E778u};
        for (int k = 0; k < 3; ++k) { c->ecx = M32(0x0091CADCu); if (call_guest(c, 0x00516B50u, 1, &pkg[k]) & 0xFF) mask |= 0x10u << k; }
        if (M8(0x0091CA58u)) mask |= 0x80;
        uint32_t type = M32(self + 0x20);
        c->ecx = 0x008F5118u;
        if (!(call_guest(c, 0x00569530u, 1, &type) & 0xFF)) mask |= 0x100;
        if (M32(0x009885C8u)) mask |= 0x200;
        if (M8(self + 0x330) != M8(M32(M32(0x0091CF90u) + 0x10) + 0x34)) mask |= 0x400;
        if ((int32_t)M32(0x008EE348u) > 0) mask |= 0x800;
        c->ecx = 0x00925E70u;
        if (call_guest(c, 0x0050DE50u, 0, NULL) & 0xFF) mask |= 0x1000;
        *c = save;
    }
    if (lo != last_lo || hi != last_hi || mask != last_mask) {
        diag_timeline("DetermineHudFeatures call %u: %08X%08X%s blocked %04X", calls, hi, lo, lo || hi ? "" : " (HUD off)", mask);
        last_lo = lo; last_hi = hi; last_mask = mask;
    }
}
TIMELINE_HOOK(0060E160)   // Game_NotifyCountdownDone (Lua: releases the racers' vehicles)
// WaitForResourceLoadingComplete (the per-frame step, cdecl): steps over 20 ms, with the first delayed callback it
// ran (DelayedResourceCallbacks 00925688) and the resource list's head (ResourceFileList 009259A4).
void f_006626B0_orig(Cpu *c);
void f_006626B0(Cpu *c) {
    if (!diag_timeline_on()) { f_006626B0_orig(c); return; }
    uint32_t ndelayed = M32(0x0092585Cu), cb = ndelayed ? M32(0x00925688u) : 0, loading = M32(0x00925858u);
    uint64_t t0 = host_ticks();
    f_006626B0_orig(c);
    double ms = (host_ticks() - t0) / 1e6;
    char buf[16];
    if (ms > 20) diag_timeline("WaitForResourceLoadingComplete step %.1f ms (delayed callbacks %u, first %s; loading %u -> %u)", ms,
                               ndelayed, cb ? timeline_name(cb, buf, sizeof buf) : "-", loading, M32(0x00925858u));
}

// RECOMP_ABICHECK builds: a translated function returned with a callee-saved register or esp changed.
void abi_violation(Cpu *c, uint32_t fn, uint32_t ebx, uint32_t esi, uint32_t edi, uint32_t ebp, uint32_t esp) {
    static uint32_t seen[4096];
    static int nseen;
    for (int i = 0; i < nseen; ++i) if (seen[i] == fn) return;
    if (nseen < 4096) seen[nseen++] = fn;
    uint32_t off = 0, roff = 0;
    const char *name = diag_symbol(fn, &off), *caller = diag_symbol(M32(c->esp), &roff);
    char line[512];
    int n = snprintf(line, sizeof line, "ABI violation in %08X %s (returning to %08X %s+0x%X):", fn, name ? name : "?", M32(c->esp), caller ? caller : "?", roff);
    if (c->ebx != ebx) n += snprintf(line + n, sizeof line - (size_t)n, " ebx %08X->%08X", ebx, c->ebx);
    if (c->esi != esi) n += snprintf(line + n, sizeof line - (size_t)n, " esi %08X->%08X", esi, c->esi);
    if (c->edi != edi) n += snprintf(line + n, sizeof line - (size_t)n, " edi %08X->%08X", edi, c->edi);
    if (c->ebp != ebp) n += snprintf(line + n, sizeof line - (size_t)n, " ebp %08X->%08X", ebp, c->ebp);
    if (c->esp != esp) n += snprintf(line + n, sizeof line - (size_t)n, " esp %08X->%08X", esp, c->esp);
    fprintf(stderr, "boot: %s\n", line);
    if (diag_on) {
        diag_event("%s", line);
        char path[1024];
        snprintf(path, sizeof path, "%s/abi.txt", diag_dir);
        FILE *f = fopen(path, "a");
        if (f) { fprintf(f, "frame %llu: %s\n", (unsigned long long)frame_now, line); fclose(f); }
    }
}

// BOOT_DIAG_STRINGS=1: <dir>/strings.tsv, hash -> string for every distinct SearchForString (0056BB80, the hash in edx,
// pointer in eax) lookup, which the FEng text labels go through (FEngSetLanguageHash 00515C00 -> the label callback). Used to find
// the real label hashes (the LANGUAGES/*.bin offsets don't index the text directly).
void fe_console_search(Cpu *c);
void f_0056BB80(Cpu *c) {
    static int on = -1;
    static uint32_t seen[8192];
    uint32_t hash = c->edx;                 // register argument (edx)
    fe_console_search(c);                   // fe_console.c: the original plus the port's text swaps
    if (on < 0) on = diag_on && getenv("BOOT_DIAG_STRINGS") != NULL;
    if (!on) return;
    uint32_t h = (hash * 2654435761u) >> 19;
    for (; seen[h]; h = (h + 1) & 8191) if (seen[h] == hash) return;
    seen[h] = hash ? hash : 1;
    char path[1100];
    snprintf(path, sizeof path, "%s/strings.tsv", diag_dir);
    FILE *f = fopen(path, "a");
    if (!f) return;
    const char *s = c->eax ? (const char *)G + c->eax : "(none)";
    fprintf(f, "%08X\t", hash);
    for (; *s && *s != '\n'; ++s) fputc(*s == '\t' ? ' ' : (unsigned char)*s, f);
    fputc('\n', f);
    fclose(f);
}

// The game's shared string pool (gSharedStringPool 0090E690: bInitSharedStringPool(0x8000) in InitializeEverything):
// every bFile, queued file, streaming pack and car pack keeps its name there. bSharedStringPool::Allocate (004615A0,
// thiscall (name) -> copy or NULL) returns NULL when no free block fits, and a car's solid pack allocated then keeps a
// NULL name for good (CarLoader::AllocateSolidPack 00752E00). Every failure is logged (name, pool use), with or without
// BOOT_DIAG. A string freed while still referenced reads "" (Free clears its first byte). TEST (local):
// BOOT_TEST_SHAREDSTR_EMPTY=<substring>: the first allocation of a name containing it gets "" instead, as a car pack's
// name freed under it reads (e.g. M3GTRE46\GEOMETRY: the player car's solid pack). BOOT_TEST_SHAREDSTR_FULL=<substring>:
// the first allocation of a name containing it first fills the pool with dummy strings, so it and every new name after
// it fail as in a full pool.
#define SSTR_POOL 0x0090E690u
void sstr_pool_stats(uint32_t *n, uint32_t *peak_n, uint32_t *bytes, uint32_t *peak_bytes, uint32_t *size) {
    *size = M32(SSTR_POOL) * 8; *bytes = M32(SSTR_POOL + 0x2C); *peak_bytes = M32(SSTR_POOL + 0x30);
    *n = M32(SSTR_POOL + 0x34); *peak_n = M32(SSTR_POOL + 0x38);
}
void f_004615A0_orig(Cpu *c);
void f_004615A0(Cpu *c) {
    static const char *empty = (const char *)-1;
    static unsigned nfail;
    if (empty == (const char *)-1) { empty = getenv("BOOT_TEST_SHAREDSTR_EMPTY"); if (empty && !*empty) empty = NULL; }
    uint32_t name = arg(c, 0);
    const char *s = name ? (const char *)G + name : NULL;
    if (empty && s && strstr(s, empty)) {
        fprintf(stderr, "boot: BOOT_TEST_SHAREDSTR_EMPTY: \"%.80s\" allocated as \"\"\n", s);
        empty = NULL;
        M32(c->esp + 4) = name + (uint32_t)strlen(s);            // its terminator: ""
        f_004615A0_orig(c);
        return;
    }
    static const char *full = (const char *)-1;
    if (full == (const char *)-1) { full = getenv("BOOT_TEST_SHAREDSTR_FULL"); if (full && !*full) full = NULL; }
    if (full && s && strstr(s, full)) {
        uint32_t esp = c->esp, self = c->ecx, buf = galloc(32), k = 0;
        for (;; ++k) {
            snprintf((char *)G + buf, 32, "~sstr_fill_%u", k);
            c->esp = esp; c->ecx = self; M32(esp + 4) = buf;
            f_004615A0_orig(c);
            if (!c->eax) break;
        }
        c->esp = esp; c->ecx = self; M32(esp + 4) = name;
        fprintf(stderr, "boot: BOOT_TEST_SHAREDSTR_FULL: pool filled with %u strings at \"%.80s\"\n", k, s);
        full = NULL;
    }
    uint32_t ra = M32(c->esp + 8);                               // bAllocateSharedString's caller (its only caller)
    if (s && !strncmp(s, "~~~", 3)) {                           // a dead name in use (BOOT_TEST_SHAREDSTR_POISON)
        static unsigned ndead;
        if (++ndead <= 20) fprintf(stderr, "boot: shared string pool: freed name in use (%u chars) from %08X, guest stack %08X %08X %08X %08X\n",
                                   (unsigned)strlen(s), ra, M32(c->esp + 12), M32(c->esp + 16), M32(c->esp + 20), M32(c->esp + 24));
        if (diag_on) diag_event("sstr dead name %08X (%u chars) from %08X", name, (unsigned)strlen(s), ra);
    }
    f_004615A0_orig(c);
    if (diag_on && c->eax && s && !strncmp(s, "CARS\\", 5))         // the car packs' names: who holds them
        diag_event("sstr alloc %08X \"%.60s\" refs %u from %08X", c->eax, s, M16(c->eax - 2), ra);
    if (c->eax || !s || !M32(SSTR_POOL + 8)) return;
    uint32_t n, pn, b, pb, sz;
    sstr_pool_stats(&n, &pn, &b, &pb, &sz);
    if (++nfail <= 20 || (nfail & (nfail - 1)) == 0)
        fprintf(stderr, "boot: shared string pool: no room for \"%.80s\" (failure %u%s; %u strings, %u of %u bytes, peak %u strings %u bytes)\n",
                s, nfail, "", n, b, sz, pn, pb);
    if (diag_on) diag_event("bSharedStringPool::Allocate(%.80s) -> NULL (%u strings, %u/%u bytes)", s, n, b, sz);
}
// bSharedStringPool::Free (004617E0, thiscall (name)): the car packs' names, with the references left (diag only). A
// name freed while a LoadedSolidPack still points at it reads "" (the race-start hang's empty-name open loop).
// TEST (local): BOOT_TEST_SHAREDSTR_POISON=1: a string freed to 0 references keeps its length as '~'s (instead of ""),
// so a later use of the dead name is visible: Allocate logs every "~~~" name with its caller, and opens show it.
void f_004617E0_orig(Cpu *c);
void f_004617E0(Cpu *c) {
    static int poison = -1;
    if (poison < 0) poison = getenv("BOOT_TEST_SHAREDSTR_POISON") != NULL;
    uint32_t name = arg(c, 0), ra = M32(c->esp + 8);            // bFreeSharedString's caller
    uint32_t base = M32(SSTR_POOL + 8), end = base + M32(SSTR_POOL) * 8;
    int in_pool = name && base && name >= base + 6 && name < end;
    int car = diag_on && in_pool && !strncmp((const char *)G + name, "CARS\\", 5);
    char copy[64];
    size_t len = in_pool ? strnlen((const char *)G + name, end - name) : 0;
    if (car) snprintf(copy, sizeof copy, "%s", (const char *)G + name);
    f_004617E0_orig(c);
    if (car) diag_event("sstr free %08X \"%s\" refs %u from %08X", name, copy, M16(name - 2), ra);
    if (poison && in_pool && len && len < end - name && M16(name - 2) == 0) memset(G + name, '~', len);
}

// ---------------- race start placement (one-off diagnostic) ----------------
// World_DEBUGStartLocation (00733710, cdecl (pos, dir)): QuickGame's start location (startup race's start marker,
// else GManager's respawn location); GRaceDatabase::SetStartupRace (00600AB0); Game_WarpPlayerToTrigger (00611570);
// the race-grid placement 0060D820. Startup race = GRaceDatabase::mObj (0091E004) +0x2C.
void f_00733710_orig(Cpu *c);
void f_00733710(Cpu *c) {
    uint32_t pos = arg(c, 0), dir = arg(c, 1);
    f_00733710_orig(c);
    if (diag_on) {
        uint32_t db = M32(0x0091E004u);
        diag_event("racestart: World_DEBUGStartLocation pos %.1f %.1f %.1f dir %.2f %.2f %.2f startup race %08X (db %08X) from %08X",
                   MF32(pos), MF32(pos + 4), MF32(pos + 8), MF32(dir), MF32(dir + 4), MF32(dir + 8), db ? M32(db + 0x2C) : 0, db, M32(c->esp));
        diag_timeline("racestart: World_DEBUGStartLocation pos %.1f %.1f %.1f", MF32(pos), MF32(pos + 4), MF32(pos + 8));
    }
}
void f_00600AB0_orig(Cpu *c);
void f_00600AB0(Cpu *c) {
    if (diag_on) { diag_event("racestart: SetStartupRace %08X (db %08X) from %08X", arg(c, 0), c->ecx, M32(c->esp));
                   diag_timeline("racestart: SetStartupRace %08X", arg(c, 0)); }
    f_00600AB0_orig(c);
}
void f_00611570_orig(Cpu *c);
void f_00611570(Cpu *c) {
    uint32_t t = arg(c, 0);
    if (diag_on) { diag_event("racestart: Game_WarpPlayerToTrigger %08X pos %.1f %.1f %.1f from %08X", t, MF32(t + 0x28), MF32(t + 0x2C), MF32(t + 0x30), M32(c->esp));
                   diag_timeline("racestart: Game_WarpPlayerToTrigger pos %.1f %.1f %.1f", MF32(t + 0x28), MF32(t + 0x2C), MF32(t + 0x30)); }
    uint64_t t0 = host_ticks(), f0 = perf_nframes;
    f_00611570_orig(c);
    if (diag_on) diag_event("racestart: Game_WarpPlayerToTrigger done in %.1f ms, %llu frames", (host_ticks() - t0) / 1e6,
                            (unsigned long long)(perf_nframes - f0));
}
// BOOT_DIAG_WARP=<race sim s>:<x>,<y>,<z>[:block] (hitch work): warp the player once the race's sim time passes s, as a
// career event entry does (Game_WarpPlayerToTrigger with a trigger at UMath x,y,z), at the end of a main-loop pass.
void diag_warp_pass(Cpu *c) {
    static int state = -1;
    static float at, p[3], race0 = -1;
    if (state < 0) {
        const char *e = getenv("BOOT_DIAG_WARP");
        state = e && sscanf(e, "%f:%f,%f,%f", &at, &p[0], &p[1], &p[2]) == 4 ? 1 : 0;
    }
    if (state != 1 || M32(0x00925E90u) != 6) return;
    float now = MF32(0x009885D8u);
    if (race0 < 0) race0 = now;
    if (now - race0 < at) return;
    state = 2;
    static uint32_t trig;
    if (!trig) trig = galloc(0x40);
    memset(G + trig, 0, 0x40);
    MF32(trig + 0x28) = p[0]; MF32(trig + 0x2C) = p[1]; MF32(trig + 0x30) = p[2];
    MF32(trig + 0x34) = 1.0f;
    diag_event("warp: at race sim %.2f to %.1f %.1f %.1f", now - race0, p[0], p[1], p[2]);
    uint32_t esp = c->esp, ecx = c->ecx;
    const char *e = getenv("BOOT_DIAG_WARP");
    if (strstr(e, ":block")) {             // the user's freeze shape: a retail load wait in one frame right after a warp
        uint64_t t0 = host_ticks();        // (Sim::SetStream(pos, false) 006F1170, then BlockUntilLoadingComplete 007317A0
        uint32_t a[2] = {trig + 0x28, 0};  // as CAnimPlayer::Load / a HUD load's MakeSpaceInPool wait)
        call_guest(c, 0x006F1170u, 2, a);
        c->esp = esp; c->ecx = 0x009AA9D0u;
        call_guest(c, 0x007317A0u, 0, NULL);
        diag_event("warp: block until loading complete took %.1f ms", (host_ticks() - t0) / 1e6);
    } else
        call_guest(c, 0x00611570u, 1, &trig);
    c->esp = esp; c->ecx = ecx;
}
void f_0060D820_orig(Cpu *c);
void f_0060D820(Cpu *c) {
    if (diag_on) { diag_event("racestart: grid placement 0060D820 from %08X", M32(c->esp)); diag_timeline("racestart: grid placement 0060D820"); }
    f_0060D820_orig(c);
}
// The player vehicle: Game_SetPlayerStartPosition (00611670, cdecl (GRuntimeInstance*)); PVehicle::LoadBehaviors
// (00685240, thiscall (pos, matrix)); PVehicle::SetVehicleOnGround (00685810, thiscall on the IVehicle part (pos, dir),
// returns success; fails with no rigid body or no behaviours); PVehicle::ReloadBehaviors (006863B0, fastcall);
// PVehicle::SetAnimating (00686410, thiscall (bool)).
void f_00611670_orig(Cpu *c);
void f_00611670(Cpu *c) {
    if (diag_on) { diag_event("racestart: Game_SetPlayerStartPosition %08X from %08X", arg(c, 0), M32(c->esp)); diag_timeline("racestart: Game_SetPlayerStartPosition"); }
    f_00611670_orig(c);
}
void f_00685240_orig(Cpu *c);
void f_00685240(Cpu *c) {
    uint32_t self = c->ecx, pos = arg(c, 0);
    if (diag_on) { diag_event("racestart: PVehicle::LoadBehaviors %08X pos %.1f %.1f %.1f from %08X", self, MF32(pos), MF32(pos + 4), MF32(pos + 8), M32(c->esp));
                   diag_timeline("racestart: PVehicle::LoadBehaviors %08X pos %.1f %.1f %.1f", self, MF32(pos), MF32(pos + 4), MF32(pos + 8)); }
    f_00685240_orig(c);
}
void f_00685810_orig(Cpu *c);
void f_00685810(Cpu *c) {
    uint32_t self = c->ecx, pos = arg(c, 0), ret = M32(c->esp);
    float p[3] = {MF32(pos), MF32(pos + 4), MF32(pos + 8)};
    f_00685810_orig(c);
    if (diag_on) { diag_event("racestart: PVehicle::SetVehicleOnGround %08X pos %.1f %.1f %.1f -> %u from %08X", self, p[0], p[1], p[2], c->eax & 255, ret);
                   diag_timeline("racestart: SetVehicleOnGround %08X pos %.1f %.1f %.1f -> %u from %08X", self, p[0], p[1], p[2], c->eax & 255, ret); }
}
void f_006863B0_orig(Cpu *c);
void f_006863B0(Cpu *c) {
    if (diag_on) { diag_event("racestart: PVehicle::ReloadBehaviors %08X from %08X", c->ecx, M32(c->esp)); diag_timeline("racestart: ReloadBehaviors %08X", c->ecx); }
    f_006863B0_orig(c);
}
void f_00686410_orig(Cpu *c);
void f_00686410(Cpu *c) {
    if (diag_on) { diag_event("racestart: PVehicle::SetAnimating %08X %u from %08X", c->ecx, arg(c, 0) & 255, M32(c->esp)); diag_timeline("racestart: SetAnimating %08X %u", c->ecx, arg(c, 0) & 255); }
    f_00686410_orig(c);
}
