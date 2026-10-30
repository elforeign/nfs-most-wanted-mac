#!/usr/bin/env python3
"""Capture clock: compile the real timestep hook and script-clock expressions.

Proof: good 9/9 checks; wall-timestep mutant 3 failures; wall-script and
movie-bypass mutants 1 each; diag-guard mutant 2. No game, GPU, assets or emulation.
Offline suite autodiscovers it.
"""
from pathlib import Path
import subprocess
import tempfile
import sys

ROOT = Path(__file__).resolve().parent
pace = (ROOT / 'pacing.c').read_text()
hook = 'void f_00661280(Cpu *c) {' + pace.split('void f_00661280(Cpu *c) {', 1)[1].split('\n}\n', 1)[0] + '\n}\n'
user = (ROOT / 'user32.m').read_text()
clock = user.split('    // script clock:', 1)[1].split('    if (release_at', 1)[0]
clock = '    // script clock:' + clock
init = (ROOT / 'diag.c').read_text().split('void diag_init(void) {', 1)[1].split('    mkdir(diag_dir', 1)[0]
HARNESS = r'''
#include <stdint.h>
#include <stdio.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>
typedef struct { uint32_t esp; } Cpu;
static float mem[8], received;
#define MF32(a) mem[(a)/4]
static int diag_fixed_step, sim_hz = 60;
static uint32_t presents;
static uint64_t now;
static int diag_on, has_diag, has_fixed;
static const char *diag_dir;
static uint32_t skipmovies;
#define M32(a) skipmovies
static char *clock_getenv(const char *name) {
    return !strcmp(name, "BOOT_DIAG") ? (has_diag ? "capture" : NULL) : (has_fixed ? "1" : NULL);
}
#define getenv clock_getenv
#define signal(a, b) ((void)0)
static void diag_car_init(void) {}  // unrelated diagnostic initialization
static void init_clock(void) {
DIAG_INIT
}
#undef getenv
static int lockstep_now(void) { return 0; }
int pacing_lockstep_now(void) { return 0; }
int pacing_sim_hz(void) { return 60; }
uint64_t host_ticks(void) { return now; }
static int lock_tick;
static void f_00661280_orig(Cpu *c) { received = MF32(c->esp + 4); }
HOOK
static uint64_t script_clock(uint64_t real_frame) {
CLOCK
    return frame;
}
static int failures, checks;
#define CHECK(x) do { ++checks; if (!(x)) { ++failures; puts(#x); } } while (0)
int main(void) {
    has_fixed = 1; init_clock();
    CHECK(!diag_on && !diag_fixed_step && !skipmovies);
    has_diag = 1; has_fixed = 0; init_clock();
    CHECK(diag_on && !diag_fixed_step && !skipmovies);
    has_fixed = 1; init_clock();
    CHECK(diag_fixed_step && skipmovies == 1);
    diag_fixed_step = 0;
    Cpu c = {0};
    MF32(4) = 0.123f; f_00661280(&c);
    CHECK(received == 0.123f);                 // off: preserve the original wall delta
    diag_fixed_step = 1;
    double elapsed = 0;
    for (unsigned frame = 1; frame <= 1800; ++frame) {
        presents = frame;
        MF32(4) = 0.75f; f_00661280(&c); elapsed += received;
        // Loading may run extra main-loop passes without presenting anything.
        for (int pass = 0; pass < (int)(frame % 4); ++pass) {
            MF32(4) = 0.019f; f_00661280(&c); elapsed += received;
        }
    }
    CHECK(fabs(elapsed - 30.0) < 0.00001);    // animation phase depends only on images
    MF32(4) = 0.019f; f_00661280(&c);
    CHECK(received == 0.0f);                  // an extra loading pass gets no time
    presents++;
    MF32(4) = 9.0f; f_00661280(&c);
    CHECK(fabsf(received - 1.0f/60.0f) < 1e-7f);
    now = 1000000000ull; (void)script_clock(17);
    now += 777000000ull;
    CHECK(script_clock(18) == 18);            // scripted waits ignore load/wall time
    diag_fixed_step = 0;
    now += 1000000000ull;
    CHECK(script_clock(19) != 19);            // ordinary scripts retain wall time
    printf("%d/%d checks\n", checks - failures, checks);
    return failures ? 1 : 0;
}
'''


def check(h, c, i=init):
    with tempfile.TemporaryDirectory(prefix='refs-clock-') as directory:
        d = Path(directory)
        source = d / 'clock.c'
        source.write_text(HARNESS.replace('HOOK', h).replace('CLOCK', c).replace('DIAG_INIT', i))
        subprocess.run(['clang', '-std=c11', '-O1', str(source), '-o', str(d / 'clock')], check=True)
        return subprocess.run([str(d / 'clock')], capture_output=True, text=True)


good = check(hook, clock)
print('good:', good.stdout.strip())
ok = good.returncode == 0
for name, h, c, i in [('wall-timestep', hook.replace('if (diag_fixed_step)', 'if (0)', 1), clock, init),
                      ('wall-script', hook, clock.replace('if (diag_fixed_step) frame = real_frame;', ''), init),
                      ('movie-bypass', hook, clock, init.replace('if (diag_fixed_step) M32(0x00926144u) = 1;', '')),
                      ('diag-guard', hook, clock, init.replace('if (!diag_dir)', 'if (0)'))]:
    bad = check(h, c, i)
    print(name + ':', bad.stdout.strip())
    ok = ok and bad.returncode != 0
print('refs_clock_test: ' + ('PASS' if ok else 'FAIL'))
sys.exit(0 if ok else 1)
