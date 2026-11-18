"""Game-thread CPU profiles (runtime/diag_profile.h) with real signal sampling, without game assets.

A harness game thread draws 6 ms frames in cprof_hot, one 300 ms busy frame (cprof_slow) and one 200 ms sleep
(cprof_sleep) while the production sampler runs; the old profilers are pinned below and must fail the same checks.
Gap 1 (a slow frame writes profile_N.txt): fixed >= 2 files naming cprof_slow / cprof_sleep; old Carbon profiler: 0.
Sampling must not end a sleep early: fixed 200 ms sleep 204 ms; old MW (SIGUSR2) sampler cut it to 101 ms.
Gap 2 (whole-session profile): fixed profile_session.txt with ~95 samples, cprof_hot >= 55% inclusive (70 measured);
old MW profiler: no profile_session.txt (0 samples).
FP bounds: fp=0xfffffffffffffff0, hi=0xfffffffffffffff8: old accepts 1 invalid frame, fixed accepts 0
(compiled 64-bit expressions); the same check guards saved next pointers on the following loop iteration.
Also checks that both images' diagnostics include the shared sampler and that Carbon's hang monitor,
diag_frame and diag_init drive it, and that an F10 report copies the session profile.
Dependencies: diag_profile.h, diag_threads.h, diag.c, report.m, recomp/private_runtime/game_diagnostics.h.
"""
from pathlib import Path
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
OLD_FP_CHECK = 'fp >= lo && fp + 16 <= hi && !(fp & 7)'
OLD_FP_CHECK_SHA = 'e809f17f27eac2f713ac8841a504c02d7fa03dd150e41bdd27f1d1d88544c74e'
OLD_MW = r'''static pthread_t game_thread;
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
'''
OLD_MW_SHA = '3315e9361cdff48a81e99a50c1af4b3198e971c6f151681dc34cd15c7b9e8852'
OLD_CARBON = r'''static pthread_t game_thread;
static int have_game_thread;
#define PROF_SAMPLES 4096
#define PROF_DEPTH 48
static void *prof_pcs[PROF_SAMPLES][PROF_DEPTH];
static uint8_t prof_depth[PROF_SAMPLES];
static int prof_n, prof_files, prof_max = 20;   // BOOT_DIAG_PROFILE_MAX: how many profile_N.txt (default 20)
static uint64_t prof_frame_ns, prof_frame_no;
static void prof_name(void *pc, char *buf, size_t n) { /* Unsupported engine diagnostic. */ }
typedef struct { void *pc; int incl, self; } ProfFn;
static int cmp_incl(const void *a, const void *b) { /* Unsupported engine diagnostic. */ return (int){0}; }
static int cmp_self(const void *a, const void *b) { /* Unsupported engine diagnostic. */ return (int){0}; }
static void prof_write(uint64_t end_ns) { /* Unsupported engine diagnostic. */ }
// Sleeps ~100 ms in 2 ms steps, sampling the game thread while its frame is late.
static void profile_wait_100ms(void) { /* Unsupported engine diagnostic. */ }
'''
OLD_CARBON_SHA = 'cf70788297f9d6620c9434fec9605aa530847416fab1a24776ed09eeec70ce1e'

HARNESS = r'''
#include <dlfcn.h>
#include <execinfo.h>
#include <pthread.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
typedef struct Cpu Cpu;
int diag_on = 1;
const char *diag_dir;
static uint64_t frame_now, last_frame_ns;
static int idle_now;
uint64_t host_ticks(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return (uint64_t)t.tv_sec * 1000000000ull + (uint64_t)t.tv_nsec; }
const char *diag_symbol(uint32_t a, uint32_t *off) { (void)a; if (off) *off = 0; return NULL; }
int threads_list(pthread_t *ths, Cpu **cpus, uint32_t *tids, int cap) { (void)ths; (void)cpus; (void)tids; (void)cap; return 0; }
#include "diag_threads.h"
PROFILER
volatile double sink;
__attribute__((noinline)) void cprof_hot(void) { uint64_t end = host_ticks() + 6000000ull; while (host_ticks() < end) sink += 1.0; }
__attribute__((noinline)) void cprof_slow(void) { uint64_t end = host_ticks() + 300000000ull; while (host_ticks() < end) sink += 1.0; }
static uint64_t sleep_ns;
__attribute__((noinline)) void cprof_sleep(void) { uint64_t t = host_ticks(); usleep(200000); sleep_ns = host_ticks() - t; }
static volatile int done;
static void *sampler(void *p) { (void)p; while (!done) profile_wait_100ms(); return NULL; }
int main(int argc, char **argv) {
    (void)argc;
    diag_dir = argv[1];
    signal(SIGUSR2, bt_signal);
    game_thread = pthread_self(); have_game_thread = 1;
    __atomic_store_n(&last_frame_ns, host_ticks(), __ATOMIC_RELEASE);
    pthread_t t;
    pthread_create(&t, NULL, sampler, NULL);
    for (int f = 1; f <= 330; ++f) {
        if (f == 60) cprof_slow(); else if (f == 120) cprof_sleep(); else cprof_hot();
        frame_now = (uint64_t)f;
        __atomic_store_n(&last_frame_ns, host_ticks(), __ATOMIC_RELEASE);
    }
    usleep(250000);                        // the sampler sees the last frame end
    done = 1;
    pthread_join(t, NULL);
#ifdef SESSION
    diag_profile_session_write();          // as diag_begin_exit does
#endif
    FILE *f = fopen(argv[2], "w");
    if (f) { fprintf(f, "%.1f\n", sleep_ns / 1e6); fclose(f); }
    return 0;
}
'''


def body(text, signature):
    start = text.index(signature)
    i = text.index('{', start) + 1
    level = 1
    while level:
        level += (text[i] == '{') - (text[i] == '}')
        i += 1
    return text[start:i]


def run(profiler, session, work):
    work.mkdir()
    (work / 'h.c').write_text(HARNESS.replace('PROFILER', profiler))
    exe = work / 'h'
    cmd = ['xcrun', 'clang', '-O1', '-fno-omit-frame-pointer', '-w', '-fno-optimize-sibling-calls', '-I', str(ROOT / 'runtime'),
           str(work / 'h.c'), '-o', str(exe)] + (['-DSESSION'] if session else [])
    subprocess.run(cmd, check=True)
    out = work / 'diag'
    out.mkdir()
    subprocess.run([str(exe), str(out), str(work / 'sleep.txt')], check=True, timeout=60)
    sleep_ms = float((work / 'sleep.txt').read_text())
    profiles = sorted(out.glob('profile_[0-9]*.txt'))
    sess = out / 'profile_session.txt'
    samples = 0
    text = sess.read_text() if sess.exists() else ''
    m = re.search(r'^(\d+) samples over', text, re.M)
    if m:
        samples = int(m.group(1))
    return profiles, text, samples, sleep_ms


def incl(text, fn):
    """Inclusive percent of a function in a profile's first table (0 when absent)."""
    table = text.split('-- inclusive')[1].split('-- self')[0]
    m = re.search(r'^\s*\d+\s+([\d.]+)%\s+' + re.escape(fn) + r'$', table, re.M)
    return float(m.group(1)) if m else 0.0


def check_fp_bounds(work):
    """Compile the actual loop guard, without dereferencing deliberately corrupt pointers."""
    src = (ROOT / 'runtime/diag_profile.h').read_text()
    check = re.search(r'while \(n < max && (.*?)\) \{', src).group(1)
    assert hashlib.sha256(OLD_FP_CHECK.encode()).hexdigest() == OLD_FP_CHECK_SHA
    harness = r'''
#include <stdint.h>
#include <stdio.h>
static int accepts(uintptr_t fp, uintptr_t lo, uintptr_t hi) { return CHECK; }
int main(void) {
    _Static_assert(sizeof(uintptr_t) == 8, "64-bit frame pointers required");
    int wrap = accepts(UINT64_C(0xfffffffffffffff0), 0x1000, UINT64_C(0xfffffffffffffff8));
    printf("wrapped frame accepted: %d\n", wrap);
    int errors = wrap;
    errors += !accepts(0x1000, 0x1000, 0x2000);  // first complete frame
    errors += !accepts(0x1ff0, 0x1000, 0x2000);  // last complete frame
    errors += accepts(0xff8, 0x1000, 0x2000);    // below stack
    errors += accepts(0x1ff8, 0x1000, 0x2000);   // only one word remains
    errors += accepts(0x1001, 0x1000, 0x2000);   // unaligned
    errors += accepts(0, 0, 8);                 // subtraction must not underflow
    return errors ? 1 : 0;
}
'''
    for name, expr in (('old', OLD_FP_CHECK), ('fixed', check)):
        c = work / f'fp_{name}.c'
        exe = work / f'fp_{name}'
        c.write_text(harness.replace('CHECK', expr))
        subprocess.run(['xcrun', 'clang', '-O1', str(c), '-o', str(exe)], check=True)
        result = subprocess.run([str(exe)], capture_output=True, text=True, check=False)
        print(f'FP bounds {name}: rc={result.returncode}, {result.stdout.strip()}')
        assert result.returncode == (1 if name == 'old' else 0), result
        assert result.stdout.strip() == f'wrapped frame accepted: {1 if name == "old" else 0}'


def main():
    if not shutil.which('xcrun'):
        print('SKIP: no xcrun'); return 3
    assert hashlib.sha256(OLD_MW.encode()).hexdigest() == OLD_MW_SHA
    assert hashlib.sha256(OLD_CARBON.encode()).hexdigest() == OLD_CARBON_SHA
    mw = (ROOT / 'runtime/diag.c').read_text()
    carbon = (ROOT / 'recomp/private_runtime/game_diagnostics.h').read_text()
    report = (ROOT / 'runtime/report.m').read_text()
    # both images build the same sampler, and Carbon's diagnostics drive it
    for name, src in (('mw', mw), ('carbon', carbon)):
        assert src.count('#include "diag_profile.h"') == 1, name
        assert 'profile_wait_100ms();' in body(src, 'static void *hang_monitor(void *p)'), name
        assert 'diag_profile_session_write();' in body(src, 'void diag_begin_exit(void)'), name
    assert 'game_thread = pthread_self()' in body(carbon, 'void diag_frame(uint64_t frame, uint64_t draws)')
    assert 'prof_max = atoi' in body(carbon, 'void diag_init(void)')
    store = body(report, 'static int report_store(ReportSnapshot *r, char *dir, size_t n)')
    assert 'diag_profile_session_write();' in store and 'profile.txt' in store
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        check_fp_bounds(tmp)
        profiles, text, samples, sleep_ms = run('#include "diag_profile.h"', True, tmp / 'fixed')
        print(f'fixed: {len(profiles)} profile_N.txt, session samples {samples}, 200 ms sleep took {sleep_ms:.1f} ms')
        assert sleep_ms >= 199, sleep_ms                # sampling must not end the game thread's sleeps early
        # the 300 ms busy frame and the 200 ms sleeping one (a loaded machine can make a 6 ms frame slow too)
        texts = [p.read_text() for p in profiles]
        assert any(re.search(r'took 3\d\d\.\d ms', t) and incl(t, 'cprof_slow') >= 90 for t in texts), texts
        assert any(incl(t, 'cprof_sleep') >= 90 for t in texts), texts
        assert samples >= 60, samples               # >= 2.3 s of drawing at one sample per 20 ms
        assert incl(text, 'cprof_hot') >= 55 and incl(text, 'main') >= 95, text[:2000]
        assert ' self (innermost function) --' in text
        # control 1: the old Carbon profiler (stubs) writes no slow-frame profile
        profiles, _, _, _ = run(OLD_CARBON, False, tmp / 'old_carbon')
        print(f'old Carbon: {len(profiles)} profile_N.txt')
        assert len(profiles) == 0
        # control 2: the old MW profiler has slow frames only, no whole-session profile
        profiles, text, samples, sleep_ms = run(OLD_MW, False, tmp / 'old_mw')
        print(f'old MW: {len(profiles)} profile_N.txt, session samples {samples}, 200 ms sleep took {sleep_ms:.1f} ms')
        assert len(profiles) >= 1 and samples == 0 and not text and sleep_ms < 199
    print('PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
