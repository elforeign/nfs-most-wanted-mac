"""Asset-free MW F10 loading and fade proof.
Main and removed-tap control: 0/1 F10 reports identify all three fields. Fixed: 1/1.
Covers runtime/blackbox_mw_fe.c, runtime/blackbox_mw.c, runtime/blackbox.c,
runtime/blackbox.h, runtime/blackbox_mw_fe.h, runtime/docs/blackbox.md,
runtime/build.sh and runtime/runtime_sources.json. Reuses the runtime/blackbox_test.py
fixture hooks from runtime/report.m and runtime/portmenu.m.
"""
import os
from pathlib import Path
import runpy
import statistics
import subprocess
import tempfile

R = Path(__file__).resolve().parent
CORE = runpy.run_path(str(R / 'blackbox_test.py'))
PRELUDE = (CORE['PRELUDE'].split('static _Atomic int running;')[0]
           .replace('typedef struct { int unused; } Cpu;', '#include "runtime.h"')
           .replace('static uint64_t host_ticks', 'uint64_t host_ticks'))

FIXTURE = r'''
#include "runtime.h"
#include "blackbox_mw_fe.h"
static uint8_t memory[0x00990000];
uint8_t *G = memory;
extern unsigned guest_reads;

static void seed(void) {
    M32(0x0091CADC) = 0x1000; M32(0x1000) = 0x2000; M32(0x20E4) = 0x3000;
    M32(0x20E0) = 0x4000; M32(0x4004) = 0x3000;
    M32(0x3008) = 0x4000; M32(0x300C) = 0x5000; M32(0x3020) = 1;
    M32(0x400C) = 0x5100; M32(0x4010) = 0x89246F98; M32(0x404C) = 0x6000;
    strcpy((char *)G + 0x5000, "Loading_Controller.fng");
    strcpy((char *)G + 0x5100, "FadeScreen.fng");
    M32(0x6018) = 5; M32(0x6064) = 0x6100;
    M32(0x6110) = 0x027FF2DC; M32(0x6154) = 0x7000;
    M32(0x7030) = 0x5079C8F8;
    M32(0x0091CAE0) = 0x8000; M8(0x8001) = 1; M8(0x8002) = 2;
    M8(0x8009) = 3; M32(0x800C) = 0x5000; M32(0x801C) = 3;
    M8(0x8024) = 1; M8(0x0091CAE4) = 1;
    M32(0x00925E70) = 0x006669A0; M32(0x00925E78) = 0x5200;
    strcpy((char *)G + 0x5200, "WaitForSimulation");
    M32(0x008F4318) = 0x5000; M8(0x00988E1D) = 1; M8(0x008F3F38) = 1;
}

static void dump_at_f10(unsigned flow) {
    fixture_menu(1);
    virtual_ns += 80000000000ull;
    fixture_frame(2701, flow);
    fixture_f10(stdout);
    fixture_menu(0);
}

static void cycles(void) {
    /* The two package directions loop independently. The group's child
     * points back to the group. No direction has a null terminator. */
    M32(0x4008) = 0x3000; M32(0x3004) = 0x4000;
    M32(0x6064) = 0x6000;
    fixture_frame(1, 5);
    unsigned before = guest_reads;
    fixture_frame(2, 5);
    printf("cyclic steady frame reads=%u\n", guest_reads - before);
    assert(guest_reads - before <= 400);
    blackbox_export(stdout, BB_F10);
    M32(0x4010) = 0; /* Exhaust the forward package lookup too. */
    virtual_ns += 20000000;
    before = guest_reads;
    fixture_frame(3, 5);
    printf("cyclic forward search reads=%u\n", guest_reads - before);
    assert(guest_reads - before <= 400);
    blackbox_export(stdout, BB_F10);
}

#ifdef BENCH
#define blackbox_game_frame frame_without_tap
#include "baseline.inc"
#undef blackbox_game_frame
static double time_frames(void (*adapter)(uint64_t, uint32_t, const BlackboxTiming *)) {
    const unsigned frames_per_batch = 500000;
    uint64_t start = clock_gettime_nsec_np(CLOCK_UPTIME_RAW);
    for (unsigned i = 0; i < frames_per_batch; ++i) {
        virtual_ns += 4000000;
        adapter(i, 5, NULL);
    }
    return (double)(clock_gettime_nsec_np(CLOCK_UPTIME_RAW) - start) / frames_per_batch;
}

static void bench(void) {
    time_frames(frame_without_tap);
    time_frames(blackbox_game_frame);
    for (unsigned i = 0; i < 12; ++i) {
        double base, tap;
        if (i & 1) {
            tap = time_frames(blackbox_game_frame);
            base = time_frames(frame_without_tap);
        } else {
            base = time_frames(frame_without_tap);
            tap = time_frames(blackbox_game_frame);
        }
        printf("%.3f %.3f\n", base, tap);
    }
}
#endif

int main(int argc, char **argv) {
    assert(argc == 2);
    seed();
    if (!strcmp(argv[1], "cycles")) {
        cycles();
        return 0;
    }
    if (!strcmp(argv[1], "off")) {
        blackbox_game_frame(1, 5, NULL);
        assert(guest_reads == 0);
        blackbox_export(stdout, BB_F10);
        return 0;
    }
    if (!strcmp(argv[1], "fade-change")) {
        /* The script can change in place without a change to any package. */
        fixture_frame(1, 5);
        M32(0x6154) = 0x7100;
        M32(0x7130) = 0x12345678;
        virtual_ns += 20000000;
        fixture_frame(2, 4);
        blackbox_export(stdout, BB_F10);
        return 0;
    }
    if (!strcmp(argv[1], "idle-fade")) {
        M32(0x20E0) = 0x3000; M32(0x20F0) = 0x4000; M32(0x4004) = 0;
        M32(0x6064) = 0x6050; M32(0x6068) = 5; M32(0x60B4) = 0x6100;
        M32(0x800C) = 0x5300;
        strcpy((char *)G + 0x5300, "Private-name.fng");
        fixture_frame(1, 5);
        /* Detach an ancestor while the old object and script remain readable. */
        M32(0x6064) = 0;
        virtual_ns += 20000000;
        fixture_frame(2, 5);
        blackbox_export(stdout, BB_F10);
        return 0;
    }
#ifdef BENCH
    if (!strcmp(argv[1], "bench")) {
        bench();
        return 0;
    }
#endif
    assert(!strcmp(argv[1], "f10"));
    for (unsigned frame = 1; frame <= 2700; ++frame) {
        virtual_ns += 20000000;
        fixture_frame(frame, 5);
    }
    dump_at_f10(6);
    return 0;
}
'''

TAP = r'''
#include "runtime.h"
unsigned guest_reads;
#ifndef BENCH
static uint32_t read_guest(unsigned address, unsigned width) {
    assert(address <= 0x00990000u - width);
    ++guest_reads;
    return width == 1 ? M8(address) : M32(address);
}
#undef M8
#undef M32
#define M8(address) read_guest(address, 1)
#define M32(address) read_guest(address, 4)
#endif
#include "blackbox_mw_fe.c"
'''


def main():
    env = dict(os.environ, BOOT_DIAG='0')
    env.pop('NFS_BLACKBOX', None)
    adapter = (R / 'blackbox_mw.c').read_text()
    hook = '    blackbox_mw_fe(frame, flow, now);\n'
    assert adapter.count(hook) == 1
    baseline = adapter.replace(hook, '')
    with tempfile.TemporaryDirectory() as scratch:
        work = Path(scratch)
        (work / 'baseline.inc').write_text(baseline)
        (work / 'hooks.inc').write_text(CORE['hooks']('mw'))
        source = work / 'fixture.c'
        source.write_text(PRELUDE + FIXTURE)
        tap = work / 'tap.c'
        tap.write_text('#include <assert.h>\n' + TAP)
        binary = work / 'fixture'
        cmd = ['clang', '-std=gnu11', '-O2', '-Wall', '-Wextra', '-Werror',
               '-Wno-unused-function', '-Wno-unused-variable', '-I' + str(R),
               '-I' + str(R.parent / 'recomp'), str(source), str(tap),
               str(R / 'blackbox_mw_cinematics.c'), '-o', str(binary)]

        def run(case, **overrides):
            return subprocess.check_output([binary, case], env=dict(env, **overrides), text=True, timeout=15)

        sentinels = ('MW LOAD state=5 loading=1', "MW PACKAGE control='Loading_Controller.fng'",
                     'fade_on=1 fade_script=5079C8F8')
        for label, code, expected in [('removed tap', baseline, 0), ('fixed', adapter, 1)]:
            (work / 'adapter.inc').write_text(code)
            subprocess.run(cmd + ['-fsanitize=address,undefined'], check=True)
            f10 = run('f10')
            hits = int(all(value in f10 for value in sentinels))
            print(f'{label}: {hits}/1 F10 reports identify loading state 5, controlling package and active fade script')
            assert hits == expected
            assert 'trigger=F10 snapshot=menu snapshot_age=80.000s' in f10 and 'OBSERVED f2700 flow=5' in f10

        assert all(value in f10 for value in
                   ("stage='WaitForSimulation'", "loading_package='Loading_Controller.fng'",
                    'seen=1 tips_done=1', 'flags=0/1 controller_errors=0300000000000002',
                    "requested='Loading_Controller.fng'", 'garage_type=3 boot=1 pause_request=1'))
        cyclic = run('cycles')
        assert 'count=24 engine_ready=1 truncated=1' in cyclic and 'fade_on=unknown' in cyclic
        assert 'limits=5' in cyclic and 'limits=3' in cyclic
        print('\n'.join(line for line in cyclic.splitlines() if line.startswith('cyclic')))
        assert 'fade_on=0 fade_script=12345678' in run('fade-change')
        idle = run('idle-fade')
        assert 'fade_on=1 fade_script=5079C8F8 fade_package=00004000' in idle
        assert 'fade_on=0 fade_script=00000000 fade_package=00004000' in idle
        assert "requested='<unknown>'" in idle and 'Private-name.fng' not in idle
        assert 'disabled NFS_BLACKBOX=0' in run('off', NFS_BLACKBOX='0')

        subprocess.run(cmd + ['-DBENCH'], check=True)
        pairs = [tuple(map(float, line.split())) for line in run('bench').splitlines()]
        delta = [tap_ns - base_ns for base_ns, tap_ns in pairs]
        print(f'ns/frame, medians of {len(pairs)} paired batches: '
              f'base {statistics.median(base for base, _ in pairs):.1f}, '
              f'tap {statistics.median(tap for _, tap in pairs):.1f}, '
              f'added {statistics.median(delta):+.1f} (range {min(delta):+.1f} to {max(delta):+.1f})')


if __name__ == '__main__':
    main()
