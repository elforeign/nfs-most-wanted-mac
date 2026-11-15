#!/usr/bin/env python3
"""Clock-drop evidence (slowclock.c), no assets or game; discovered by offline_tests.sh.

Compiles the production unit against a filled frame ring and drives five seconds of GPU states:
P13, P13, P1 (entry), P2 (stays), P13 (exit). Expects eight values in every row, clock_low 0 0 1 1 0,
one clock_1.tsv with the ring's 1023 frames newest first, one "entered" and one "left" note.
Broken control: the same sequence with the low states renamed P4/P5 writes no file and no note.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

RT = Path(__file__).resolve().parent
HARNESS = r'''
#include <stdarg.h>
#include <stdio.h>
#include <time.h>
#include "host.h"
#include "slowclock.h"
PerfFrame perf_frames[PERF_RING];
volatile uint64_t perf_nframes;
float perf_gpu_ms[PERF_RING];
volatile uint64_t perf_gpu_serial;
static void note(const char *fmt, ...) { va_list a; va_start(a, fmt); fputs("NOTE ", stderr); vfprintf(stderr, fmt, a); va_end(a); fputc('\n', stderr); }
int main(int argc, char **argv) {
    const char *low1 = argc > 2 ? "P4" : "P1", *low2 = argc > 2 ? "P5" : "P2";
    for (int i = 0; i < 2000; ++i) {
        PerfFrame *p = &perf_frames[i % PERF_RING];
        p->frame_ms = 10 + i; p->busy_ms = 7; p->idle_ms[IDLE_GPU] = 3; p->draws = (uint32_t)i; p->ticks = 1;
        perf_gpu_ms[(i + 1) % PERF_RING] = (float)i; perf_gpu_serial = (uint64_t)i + 1; perf_nframes = (uint64_t)i + 1;
    }
    char st[5][64];
    snprintf(st[0], 64, "OFF:10.00,P1:1.50,P13:88.50"); snprintf(st[1], 64, "P13:100.00");
    snprintf(st[2], 64, "OFF:2.00,%s:61.00,%s:37.00", low1, low2); snprintf(st[3], 64, "%s:40.00,%s:60.00", low1, low2);
    snprintf(st[4], 64, "P12:20.00,P13:80.00");
    volatile double burn = 0;
    for (int s = 0; s < 5; ++s) {
        for (int i = 0; i < 3000000; ++i) burn += i;
        printf("ROW");
        slowclock_second(stdout, 1, 100 * (uint64_t)(s + 1), st[s], 1, argv[1], 10.0 + s, note);
        putchar('\n');
    }
    printf("ROW"); slowclock_second(stdout, 0, 600, st[2], 1, argv[1], 15, note); putchar('\n');
    struct timespec t0, t1;
    FILE *sink = fopen("/dev/null", "w");
    clock_gettime(CLOCK_MONOTONIC, &t0);
    for (int i = 0; i < 2000; ++i) slowclock_second(sink, 1, 700 + (uint64_t)i, st[1], 1, argv[1], 20, note);
    clock_gettime(CLOCK_MONOTONIC, &t1);
    printf("COST %.1f\n", ((t1.tv_sec - t0.tv_sec) * 1e9 + (t1.tv_nsec - t0.tv_nsec)) / 2000 / 1000);
    printf("LOW %d %d %d %d\n", slowclock_low("OFF:60,P1:40"), slowclock_low("P3:51,P13:49"), slowclock_low("-"), slowclock_low("P13:100"));
    return 0;
}
'''


def main():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / 'h.c').write_text(HARNESS)
        subprocess.run(['clang', '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-O1', '-I', RT, '-I', RT.parent / 'recomp',
                        tmp / 'h.c', RT / 'slowclock.c', '-o', tmp / 'h'], check=True)
        results = []
        for broken in (0, 1):
            d = tmp / ('run%d' % broken)
            d.mkdir()
            done = subprocess.run([tmp / 'h', d] + (['broken'] if broken else []), check=True, text=True, capture_output=True)
            out = done.stdout
            rows = [l.split('\t')[1:] for l in out.splitlines() if l.startswith('ROW')]
            notes = [l for l in done.stderr.splitlines() if l.startswith('NOTE')]
            files = sorted(p.name for p in d.iterdir())
            results.append((rows, notes, files, out, d))
        rows, notes, files, out, d = results[0]
        assert all(len(r) == 8 for r in rows), rows
        assert [r[7] for r in rows[:5]] == ['0', '0', '1', '1', '0'], rows
        assert rows[5] == ['-'] * 8, rows[5]
        assert float(rows[1][0]) > 0 and float(rows[1][1]) > 0, rows[1]          # the burn loop used CPU time
        assert rows[0][2] == '-' and 0 <= float(rows[1][2]) <= 100 and float(rows[1][3]) > 0.3, rows
        assert int(rows[1][4]) >= 0 and 0 <= float(rows[1][5]) <= 100 and rows[1][6] in ('1', '2', '4'), rows[1]
        assert files == ['clock_1.tsv'], files
        assert len(notes) == 2 and 'entered P1-P3' in notes[0] and 'clock_1.tsv' in notes[0] and 'left P1-P3 after 2 s' in notes[1], notes
        lines = (d / 'clock_1.tsv').read_text().splitlines()
        assert lines[0].startswith('# GPU clock entered P1-P3 at t=12.0 s, frame 300') and len(lines) == 2 + 1023, (lines[0], len(lines))
        newest = lines[2].split('\t')
        assert newest[0] == '0' and newest[7] == '1999' and newest[9] == '1999.00' and newest[6] == '3.00', newest
        assert 'LOW 0 1 0 0' in out, out
        cost = float([l for l in out.splitlines() if l.startswith('COST')][0].split()[1])
        assert cost < 1000, cost                                                    # microseconds per once-a-second call
        brows, bnotes, bfiles, _, _ = results[1]
        assert [r[7] for r in brows[:5]] == ['0'] * 5 and not bnotes and not bfiles, (brows, bnotes, bfiles)
        print('PASS slowclock: clock_low 0 0 1 1 0, 1 frame file of 1023 rows, 2 notes; broken control (P4/P5): 0 low rows, 0 files, 0 notes; one call costs %.1f microseconds' % cost)


if __name__ == '__main__':
    sys.exit(main())
