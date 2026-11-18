#!/usr/bin/env python3
"""Exercise Most Wanted's pacing.c scheduler hooks with a fake display and guest state.
Broken startup-only sampling: 60 -> 60 Hz. Refreshed: 60 -> 120 Hz.
Fixed display: 60 -> 60 Hz with identical step and fractional remainder.
"""
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

RT = Path(__file__).resolve().parent
CLOCK = r'''
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "host.h"
static int display = 60;
static uint64_t now;
int diag_on, sign_diag_on;
uint8_t *G;
int user32_display_hz(void) { return display; }
uint64_t host_ticks(void) { return now; }
static void f_006484E0_orig(Cpu *c) { MF32(0x200 + 0x20) = MF32(c->esp + 4); }
void f_0065C6D0_orig(Cpu *c) { (void)c; }
uint32_t call_guest(Cpu *c, uint32_t fn, int n, const uint32_t *args) { abort(); }
void portmenu_pass(Cpu *c) { (void)c; }
void diag_warp_pass(Cpu *c) { (void)c; }
void lanserver_selftest_pass(Cpu *c) { (void)c; }
int portmenu_hold(void) { return 0; }
int sign_diag_hold(void) { return 0; }
int traffic_detail_ab_hold(void) { return 0; }
int postfx_ab_hold(void) { return 0; }
int probe_hold(void) { return 0; }
int carpaint_ab_hold(void) { return 0; }
int detail_ab_hold(void) { return 0; }
int envcube_ab_hold(void) { return 0; }
'''
MAIN = r'''
int main(int argc, char **argv) {
    (void)argc;
    int override = !strcmp(argv[1], "override");
    int original = !strcmp(argv[1], "original");
    int fixed = !strcmp(argv[1], "fixed");
    enum { S = 0x200, SIM = 0x300, TASK = 0x400, PHYSICS = 0x500 };
    G = calloc(1, 0x9C0000);
    assert(G);
    Cpu c = {0};
    f_006484E0(&c);
    int start = override ? 90 : 60;
    float step = 1.0f / start;
    assert(pacing_sim_hz() == start && MF32(S + 0x20) == step);
    M32(SCHEDULER) = S;
    MF32(S + 0x1C) = .375f;                  // 3/8 of an old tick still owed
    MF32(S + 0x18) = 15.f; M32(S + 0x28) = 900;
    M32(0x009885E0u) = SIM;
    MF32(SIM + 0x18) = step; MF32(SIM + 0x1C) = step * .5f;
    M32(0x009885D0u) = TASK;
    MF32(TASK) = .5f; MF32(TASK + 8) = .25f; M32(TASK + 0x14) = PHYSICS;
    MF32(PHYSICS) = 1.f; MF32(PHYSICS + 8) = -4.f;
    display = fixed ? 60 : 120;
    now = 250000000ull; f_0065C6D0(&c);
    assert(pacing_sim_hz() == start);
    now = 1500000000ull; f_0065C6D0(&c);
    int target = pacing_sim_hz(), changed = target != start;
    printf("%s: %d -> %d Hz, scheduler step %.6f, remainder %.3f\n",
           argv[1], start, target, MF32(S + 0x20), MF32(S + 0x1C));
    if (target != (fixed || override || original ? start : 120)) { free(G); return 1; }
    assert(MF32(S + 0x20) == 1.0f / target && MF32(SIM + 0x18) == 1.0f / target);
    assert(MF32(SIM + 0x1C) == .5f / target);
    assert(MF32(S + 0x1C) == (changed ? .75f : .375f));
    assert(MF32(S + 0x18) == 15.f && M32(S + 0x28) == 900);
    assert(MF32(TASK) == (changed ? .25f : .5f) && MF32(TASK + 8) == .25f);
    assert(MF32(PHYSICS) == 1.f && MF32(PHYSICS + 8) == (changed ? -8.f : -4.f));
    free(G);
}
'''


def function(source, name):
    return re.search(rf'^(?:static )?\w+ {name}\([^\n]*\) \{{.*?^\}}', source, re.M | re.S)[0]


def main():
    source_path = Path(sys.argv[1]) if len(sys.argv) > 1 else RT / 'pacing.c'
    pace = source_path.read_text()
    definitions = '\n'.join(re.findall(r'^#define (?:SCHEDULER|Real\w+) [^\n]+', pace, re.M))
    state = pace[pace.index('static int sim_hz;'):pace.index('static uint32_t catching_up;')]
    state += re.search(r'^static uint32_t hud_obj[^\n]+', pace, re.M)[0] + '\n'
    setup = 'int user32_display_hz(void);\n' + pace[pace.index('int pacing_sim_hz(void)'):pace.index('// SimTask::SimTask')]
    hooks = '\n'.join(function(pace, name) for name in ('pace_on', 'pacing_lockstep', 'hold_real_time'))
    hooks += '\n' + re.search(r'^int pacing_lockstep_now.*\n#define lockstep_now[^\n]+', pace, re.M)[0] + '\n'
    boundary = '    refresh_sim_rate(s, lock);'
    advance = function(pace, 'f_0065C6D0')
    dynamic = boundary in advance
    refresh = function(pace, 'refresh_sim_rate') if dynamic else ''
    source = CLOCK + definitions + '\n' + state + hooks + setup + '\n' + refresh + '\n' + advance + MAIN
    env = dict(os.environ)
    for name in ('NFS_FRAMEPACE', 'NFS_SIMHZ', 'NFS_LOCKSTEP'):
        env.pop(name, None)
    with tempfile.TemporaryDirectory() as tmp:
        c = Path(tmp) / 'test.c'
        binary = Path(tmp) / 'test'
        for broken in ((False, True) if dynamic else (False,)):
            c.write_text(source.replace(boundary, '') if broken else source)
            subprocess.run(['clang', '-Werror', '-I', RT, '-I', RT.parent / 'recomp', c, '-o', binary], check=True)
            scenarios = ['moved'] if broken else ['moved', 'fixed', 'override', 'original']
            for mode in scenarios:
                settings = dict(env)
                if mode == 'override':
                    settings['NFS_SIMHZ'] = '90'
                if mode == 'original':
                    settings['NFS_FRAMEPACE'] = 'game'
                run = subprocess.run([binary, mode], capture_output=True, text=True, env=settings)
                print(('startup-only ' if broken else '') + run.stdout.strip())
                assert run.returncode == int(broken), (run.stdout, run.stderr)


if __name__ == '__main__':
    main()
