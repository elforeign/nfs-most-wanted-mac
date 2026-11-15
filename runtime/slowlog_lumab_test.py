#!/usr/bin/env python3
"""Fake-time production controller/getter/wait-loop proof; no game or GPU.

The synthetic plant follows the actual cached getter, not the requested arm.
Fixed: 010101, 6 rows, 30 waits; on 1214.4 MHz / 61.5 fps, off 338 / 30;
recovery confirmed at 5 s, 3 on / 0 off. P-only residency: P1 30%, P13 70%.
Pinned original getter ignoring setter: 13 failures, 000000, 60 waits,
all 338 / 30, 0 on / 0 off recoveries; fixed: 0 failures.
Getter default (unset/0/1): fixed 1/0/1 PASS; pinned original unset 0 (the check FAILs on it).
"""
import csv
import hashlib
import os
from pathlib import Path
import re
import subprocess
import tempfile

import sys as _source_sys
_source_sys.path.insert(0, str(Path(__file__).resolve().parent))
from save_file_fixture import mw_source

RT = Path(__file__).resolve().parent
RENDER = mw_source(RT / 'render_post.m')
# Pin only the original getter, not history or the whole production file.
ORIGINAL = '''static int x_lum_nowait(void) {
    static int on = -1;
    if (on < 0) { const char *e=getenv("NFS_LUM_NOWAIT"); on=e && !strcmp(e,"1"); }
    return on;
}
'''
ORIGINAL_SHA = 'eed06b221e0d404c62876b2b475667e155430fd0692f889548a6daf757fb7d55'
GETTER = RENDER[RENDER.index('static int x_lum_nowait_on'):RENDER.index('struct XLumSlot')]
SNAPSHOT = RENDER[RENDER.index('SlowlogLumSnapshot render_lum_snapshot('):RENDER.index('#define xt_q0')]
WAIT = RENDER[RENDER.index('    uint64_t lum_wait_ns=0;'):RENDER.index('    uint64_t completed =', RENDER.index('    uint64_t lum_wait_ns=0;'))]

HARNESS = r'''
#include "slowlog_lumab.h"
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
static double now;
static unsigned clock_calls;
static int fake_clock(clockid_t id, struct timespec *t) {
    if (id != CLOCK_MONOTONIC) abort();
    ++clock_calls; t->tv_sec=(time_t)now; t->tv_nsec=(long)llround((now-t->tv_sec)*1e9); return 0;
}
static unsigned setters, opens, flushes, snapshots, waits;
static int fail_open, fail_flush;
static FILE *counted_open(const char *p, const char *m) { ++opens; return fail_open ? NULL : fopen(p,m); }
static int counted_flush(FILE *f) { ++flushes; return fail_flush ? EOF : fflush(f); }
#define render_lum_set_nowait production_setter
GETTER
#undef render_lum_set_nowait
void render_lum_set_nowait(int on) { ++setters; production_setter(on); }
typedef struct { int lum_ready; uint32_t mode_generation, epoch; } XPostResources;
static XPostResources x_resources[3];
static typeof(x_resources[0]) *xr = &x_resources[0];
static uint32_t x_mode_generation = 1;
#define render_lum_snapshot production_snapshot
SNAPSHOT
#undef render_lum_snapshot
SlowlogLumSnapshot render_lum_snapshot(void) { ++snapshots; return production_snapshot(); }
#define fopen counted_open
#define fflush counted_flush
#define clock_gettime fake_clock
#include "slowlog_lumab.c"
#undef clock_gettime
#undef fopen
#undef fflush
#define X_LUM_SLOTS 3
#define IDLE_GPU 0
static uint64_t diag_idle_ns[1];
static uint64_t host_ticks(void) { return 1; }
static void wait_serial(uint64_t serial) { (void)serial; ++waits; }
void slowlog_lum_wait(uint64_t ns) { (void)ns; }
static void pending_wait(void) {
    struct XLumSlot { int pending; uint64_t serial; };
    struct { int lum_ready; struct XLumSlot readback[3]; } r = {1, {{1,1},{0,0},{0,0}}}, *xr = &r;
    int view = 0;
WAIT
}
static double sample_age;
static uint64_t frame, serial;
static const char *directory;
static int numeric = 1, labels = 1, active = 1, occluded, x360 = 1;
static int weighted;
static uint32_t flow = 6;
static SlowlogSample sample;
static void tick(void) { slowlog_lumab_tick(frame,flow,5000,35,active,occluded,x360,&sample,directory); }
static void interval(double dt, int high, double gpu, double draws) {
    now += dt; frame += (uint64_t)llround(dt * (high ? 75 : 30));
    double stamp=now-sample_age;
    time_t whole=(time_t)stamp;
    sample.time = whole + llround((stamp-whole)*1e9)/1e9;
    sample.interval = dt; sample.serial = ++serial;
    sample.gpu.dev_util = 95; sample.gpu.other_ms = 0;
    snprintf(sample.gpu.mhz,sizeof sample.gpu.mhz,"%s",numeric ? (high ? "1590" : "338") : (high ? "state=P13" : "state=P1"));
    snprintf(sample.gpu.states,sizeof sample.gpu.states,"%s:100",labels ? (high ? "P13" : "P1") : (high ? "1590_MHz" : "338_MHz"));
    if (weighted) snprintf(sample.gpu.states,sizeof sample.gpu.states,"%s:%d,OFF:%d",high ? "P13" : "P1",high ? 100 : 50,high ? 0 : 50);
    strcpy(sample.gpu.clock_status,numeric ? "decoded_mhz" : "decoded_residency");
    slowlog_lumab_tick(frame,flow,draws,gpu,active,occluded,x360,&sample,directory);
}
static void warm(void) {
    for (int i=0;i<30;++i) interval(1,1,10,5000);
}
static void qualify(void) {
    for (int i=0;i<5;++i) interval(1,0,35,5000);
}
static void train(int jitter, int load, int thermal) {
    for (int b=0;b<6;++b) {
        for (int i=0;i<10;++i) {
            pending_wait();
            // Three low intervals followed by seven high, only when the getter
            // actually bypasses the wait. No reference to requested controller state.
            int high = x_lum_nowait() && i>=3;
            if (thermal && i==5) sample.thermal = 1;
            interval(jitter ? 1.1 : 1,high,high ? 10 : 35,load ? 8000 : 5000);
        }
    }
}
int main(int argc, char **argv) {
    if (argc != 3) return 2;
    const char *mode = argv[1]; directory = argv[2];
    numeric = strcmp(mode,"p-only") != 0; labels = strcmp(mode,"numeric-only") != 0;
    weighted = !strcmp(mode,"weighted");
    sample.enabled = 1; sample.thermal = sample.low_power = 0; strcpy(sample.power,"AC");
    x_resources[0].lum_ready = 1; x_resources[0].epoch = 1; x_resources[0].mode_generation = 1;
    if (!strcmp(mode,"disabled")) unsetenv("NFS_SLOWLOG_LUM_AB");
    else if (!strcmp(mode,"off")) setenv("NFS_SLOWLOG_LUM_AB","0",1);
    else setenv("NFS_SLOWLOG_LUM_AB","1",1);
    if (!strcmp(mode,"baseline-on")) setenv("NFS_LUM_NOWAIT","1",1);
    else setenv("NFS_LUM_NOWAIT","0",1);
    if (!strcmp(mode,"slowlog-off")) sample.enabled = 0;
    if (!strcmp(mode,"no-ready")) x_resources[0].lum_ready = 0;
    if (!strcmp(mode,"split")) xr = &x_resources[1];
    if (!strcmp(mode,"open-fail")) fail_open = 1;
    if (!strcmp(mode,"header-fail")) fail_flush = 1;
    if (!strcmp(mode,"disabled") || !strcmp(mode,"off") || !strcmp(mode,"baseline-on") ||
        !strcmp(mode,"slowlog-off") || !strcmp(mode,"no-ready") || !strcmp(mode,"split") ||
        !strcmp(mode,"open-fail") || !strcmp(mode,"header-fail")) {
        warm(); qualify();
    } else if (!strcmp(mode,"duplicates") || !strcmp(mode,"stale") || !strcmp(mode,"unknown")) {
        warm(); interval(1,0,35,5000);
        for (int i=0;i<20;++i) {
            now += 1; frame += 30;
            if (!strcmp(mode,"unknown")) { sample.serial = ++serial; sample.time = now; strcpy(sample.gpu.clock_status,"not_decoded_yet:samples"); }
            tick();
        }
    } else if (!strcmp(mode,"overlap-qualify")) {
        warm();
        for (int i=0;i<20;++i) {
            now += 1; frame += 30; sample.time=now; sample.interval=2; sample.serial=++serial;
            strcpy(sample.gpu.mhz,"338"); strcpy(sample.gpu.states,"P1:100"); tick();
        }
    } else if (!strcmp(mode,"draw-peer") || !strcmp(mode,"no-slow-gpu") || !strcmp(mode,"no-busy")) {
        warm();
        // A dissimilar draw group has its own >=10 peers: no false slow stretch.
        if (!strcmp(mode,"draw-peer")) for (int i=0;i<12;++i) interval(1,1,35,9000);
        for (int i=0;i<8;++i) {
            if (!strcmp(mode,"no-busy")) { now+=1; sample.time=now; sample.serial=++serial; sample.gpu.dev_util=89; strcpy(sample.gpu.mhz,"338"); strcpy(sample.gpu.states,"P1:100"); tick(); }
            else interval(1,0,!strcmp(mode,"no-slow-gpu") ? 10 : 35,!strcmp(mode,"draw-peer") ? 9000 : 5000);
        }
    } else {
        warm(); qualify();
        if (!strncmp(mode,"abort-",6) || !strcmp(mode,"write-fail")) {
            for (int i=0;i<10;++i) { pending_wait(); interval(1,0,35,5000); }
            // The second block is On, so every abort must restore a real override.
            if (!strcmp(mode,"abort-path")) x360 = 0;
            if (!strcmp(mode,"abort-flow")) flow = 3;
            if (!strcmp(mode,"abort-epoch")) ++x_resources[0].epoch;
            if (!strcmp(mode,"abort-mode")) ++x_mode_generation;
            if (!strcmp(mode,"abort-visible")) occluded = 1;
            if (!strcmp(mode,"abort-inactive")) active = 0;
            if (!strcmp(mode,"abort-power")) sample.low_power = 1;
            if (!strcmp(mode,"abort-source")) strcpy(sample.power,"battery");
            if (!strcmp(mode,"abort-unknown")) strcpy(sample.gpu.clock_status,"not_decoded_yet:samples");
            if (!strcmp(mode,"write-fail")) fail_flush = 1;
            if (!strcmp(mode,"abort-gap")) now += 4;
            if (!strcmp(mode,"abort-stale")) { now += 3; tick(); }
            else if (!strcmp(mode,"abort-unknown")) { now += 1; tick(); }
            else interval(1,0,35,5000);
            if (!strcmp(mode,"write-fail")) { for (int i=0;i<10;++i) interval(1,0,35,5000); }
        } else if (!strcmp(mode,"duplicate-blocks")) {
            for (int i=0;i<60;++i) {
                pending_wait(); now+=1; frame+=30;
                if (i%2) { sample.time=now; sample.interval=2; sample.serial=++serial; }
                tick();
            }
        } else {
            if (!strcmp(mode,"boundary")) sample_age=.25;
            if (!strcmp(mode,"coverage")) sample_age=2.5;
            train(!strcmp(mode,"jitter"),!strcmp(mode,"load"),!strcmp(mode,"thermal"));
            if (!strcmp(mode,"balanced")) {
                qualify();
                train(0,0,0);
            }
        }
    }
    printf("waits=%u setters=%u opens=%u flushes=%u snapshots=%u clocks=%u effective=%d\n",waits,setters,opens,flushes,snapshots,clock_calls,x_lum_nowait());
    return 0;
}
'''


def run(cmd, **kwargs):
    result = subprocess.run(list(map(str, cmd)), text=True, capture_output=True, **kwargs)
    assert result.returncode == 0, (result.stdout + result.stderr)[-4000:]
    return result.stdout


def build(tmp, getter, name):
    text = HARNESS.replace('GETTER', getter).replace('SNAPSHOT', SNAPSHOT).replace('WAIT\n', WAIT + '\n')
    src, exe = tmp / (name + '.c'), tmp / name
    src.write_text(text)
    run(['clang', '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-O1', '-g',
         '-fsanitize=address,undefined', '-I', RT, src, '-o', exe])
    return exe


def experiment(exe, tmp, mode):
    folder = tmp / (exe.name + '-' + mode)
    folder.mkdir()
    result = run([exe, mode, folder])
    counts = {k: int(v) for k, v in re.findall(r'(\w+)=(\d+)', result)}
    path = folder / 'lum_ab.tsv'
    rows = list(csv.DictReader(path.open(), delimiter='\t')) if path.exists() else []
    return counts, rows


def plant_failures(counts, rows):
    failures = int(counts['waits'] != 30) + int(len(rows) != 6)
    for b, r in enumerate(rows):
        on = b % 2
        failures += int(int(r['nowait_effective']) != on)
        failures += int(abs(float(r['clock_mhz_mean']) - (1214.4 if on else 338)) > .001)
        failures += int(abs(float(r['fps']) - (61.5 if on else 30)) > .001)
        failures += int(r['recover_s'] != ('5.000' if on else '-'))
    return failures


def getter_default(tmp):
    # Unset is wait-free; only an explicit 0 restores the waiting readback.
    got = {}
    for name, getter in [('old', ORIGINAL), ('new', GETTER)]:
        src, exe = tmp / (name + '-getter.c'), tmp / (name + '-getter')
        src.write_text('#include <stdio.h>\n#include <stdlib.h>\n#include <string.h>\n' + getter +
                       '\nint main(void) { printf("%d", x_lum_nowait()); return 0; }\n')
        run(['clang', '-O1', src, '-o', exe])
        for label, value in [('unset', None), ('0', '0'), ('1', '1')]:
            env = {k: v for k, v in os.environ.items() if k != 'NFS_LUM_NOWAIT'}
            if value is not None:
                env['NFS_LUM_NOWAIT'] = value
            got[name, label] = subprocess.run([str(exe)], capture_output=True, text=True, env=env).stdout
    assert got['old', 'unset'] == '0', got
    assert [got['new', k] for k in ('unset', '0', '1')] == ['1', '0', '1'], got
    print('getter default: unset/0/1 -> 1/0/1 (pinned original unset -> 0)')


def main():
    assert hashlib.sha256(ORIGINAL.encode()).hexdigest() == ORIGINAL_SHA
    report = (RT / 'report.m').read_text()
    assert report.count('slowlog_lumab_tick(') == 1
    assert report.index('if (now < next) return;') < report.index('slowlog_lumab_tick(')
    assert report.index('s->gpu = gn ? gs / gn : 0;') < report.index('slowlog_lumab_tick(')
    assert 'slowlog_lumab.c \\' in (RT / 'build.sh').read_text()
    with tempfile.TemporaryDirectory(prefix='lum-ab-') as folder:
        tmp = Path(folder)
        getter_default(tmp)
        fixed = build(tmp, GETTER, 'fixed')
        counts, rows = experiment(fixed, tmp, 'plant')
        assert plant_failures(counts, rows) == 0, (counts, rows)
        assert counts['setters'] == 7 and counts['effective'] == 0 and counts['opens'] == 1
        assert ''.join(r['nowait_effective'] for r in rows) == '010101'
        assert all(float(r['valid_s']) == 10 and float(r['dt']) == 10 for r in rows)
        assert [int(r['eligible']) for r in rows] == [1, 1, 0, 1, 0, 1]
        print('fixed: 0 failures; 010101, 6 rows, 30 waits; on 1214.4 MHz / 61.5 fps, off 338 / 30; recovery 3/0 at 5 s')
        # Keep the new setter but restore the original getter's private cache:
        # requests still flip while the production wait loop remains blocking.
        broken_getter = GETTER[:GETTER.index('static int x_lum_nowait(void)')] + ORIGINAL + GETTER[GETTER.index('void render_lum_set_nowait'):]
        broken = build(tmp, broken_getter, 'original-cache')
        bc, br = experiment(broken, tmp, 'plant')
        failures = plant_failures(bc, br)
        assert failures == 13 and bc['waits'] == 60
        assert ''.join(r['nowait_effective'] for r in br) == '000000'
        assert all(r['clock_mhz_mean'] == '338.000' and r['fps'] == '30.000' and r['recover_s'] == '-' for r in br)
        print(f'original cache: {failures} failures; 000000, 60 waits, all 338 MHz / 30 fps; recovery 0/0')
        for mode in ('p-only', 'numeric-only'):
            c, rs = experiment(fixed, tmp, mode)
            assert c['waits'] == 30 and len(rs) == 6
            if mode == 'p-only':
                assert all(r['clock_mhz_mean'] == '-' for r in rs)
                assert all(dict(part.split(':') for part in r['pstates'].split(',')) == {'P1':'30.00', 'P13':'70.00'} for r in rs[1::2])
            else:
                assert plant_failures(c, rs) == 0
        c, rs = experiment(fixed, tmp, 'weighted')
        assert len(rs) == 6
        assert all(abs(float(r['clock_mhz_mean']) - (338 * .5 * 3 + 1590 * 7) / (.5 * 3 + 7)) < .001 for r in rs[1::2])
        for mode in ('disabled', 'off', 'baseline-on', 'slowlog-off', 'no-ready', 'split', 'duplicates', 'stale', 'unknown', 'draw-peer', 'no-slow-gpu', 'no-busy', 'overlap-qualify'):
            c, rs = experiment(fixed, tmp, mode)
            assert c['setters'] == c['opens'] == 0 and not rs, (mode, c, rs)
            if mode in ('disabled', 'off'):
                assert c['snapshots'] == c['flushes'] == c['clocks'] == 0
        for mode in ('open-fail', 'header-fail'):
            c, rs = experiment(fixed, tmp, mode)
            assert c['opens'] == 1 and c['setters'] == 0 and c['effective'] == 0
        for reason in ('path', 'flow', 'epoch', 'mode', 'visible', 'inactive', 'power', 'source', 'gap', 'stale', 'unknown'):
            c, rs = experiment(fixed, tmp, 'abort-' + reason)
            assert len(rs) == 2 and rs[-1]['status'].startswith('abort:') and rs[-1]['eligible'] == '0', (reason, c, rs)
            assert c['effective'] == 0 and c['setters'] == 3, (reason, c)
        c, rs = experiment(fixed, tmp, 'write-fail')
        assert c['effective'] == 0 and c['setters'] == 3
        c, rs = experiment(fixed, tmp, 'balanced')
        assert len(rs) == 12 and ''.join(r['nowait_effective'] for r in rs) == '010101101010'
        assert c['effective'] == 0
        c, rs = experiment(fixed, tmp, 'jitter')
        assert len(rs) == 6 and all(abs(float(r['dt']) - 11) < .001 for r in rs), (c, rs)
        for mode, valid in [('boundary', 9), ('coverage', 7), ('duplicate-blocks', 10)]:
            c, rs = experiment(fixed, tmp, mode)
            assert len(rs) == 6 and all(float(r['valid_s']) == valid for r in rs), (mode, c, rs)
            if mode == 'coverage':
                assert all(r['eligible'] == '0' for r in rs)
        for mode in ('load', 'thermal'):
            c, rs = experiment(fixed, tmp, mode)
            assert len(rs) == 6 and all(r['eligible'] == '0' for r in rs), (mode, rs)
        print('P-only/numeric-only, balanced episodes, actual deadlines, disabled zero calls, evidence/peer guards and 11 restore aborts: PASS')


if __name__ == '__main__':
    main()
