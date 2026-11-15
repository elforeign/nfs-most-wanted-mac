#!/usr/bin/env python3
"""Car lamps by hour: daynight_carlamps.c's flare scale and _OFF texture swap, without a game launch.

Fixture: lamp level 1 / .5 / 0 on headlight, unbraked/braked brake, reverse and non-car flares, and the
shot-frame diagnostic sum. Normal: 0 failures; broken control (level
fixed at 1, as retail) 5 failures (headlight .5 -> 1, drawn at level 0, ...).
--proof NOON_EVENTS NOON_FRAME MIDNIGHT_EVENTS MIDNIGHT_FRAME reads each run's shot-frame BOOT_DIAG line (sum
of headlight flare strengths): noon head_out must be 0 with head_in > 0; midnight head_out must equal head_in.
Game, race 15.1.1, mustanggt in look-back (front to the camera), shot key+240: noon frame 2946 head_in 4.0000
head_out 0.0000; midnight frame 2947 4.0000/4.0000; broken control (level fixed at 1) noon frame 2946 head_out
4.0000 (exit 1).
"""
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / 'runtime'
SOURCE = RUNTIME / 'daynight_carlamps.c'

HARNESS = r'''
#include "host.h"
#include <stdarg.h>
#include <stdlib.h>
uint8_t *G;
int diag_on=1;
static uint64_t frame_now=59;
uint64_t diag_current_frame(void) { return frame_now; }
static float level=1;
float daynight_lamp_level(void) { return level; }
static char last[512];
void diag_event(const char *f,...) { va_list ap; va_start(ap,f); vsnprintf(last,sizeof last,f,ap); va_end(ap); }
int diag_shot_due(uint64_t f) { return f==60 ? 2 : 0; }
static int failures;
#include TEST_SOURCE
#define CHECK(x) do { if(!(x)) { ++failures; printf("FAIL line %d: %s\n",__LINE__,#x); } } while(0)
static int flare(uint32_t ret,uint32_t hash,float s,float *out) {
    Cpu c={.esp=0x1000};
    M32(0x1000)=ret; M32(0x1008)=0x3000; M32(0x3008)=hash; MF32(0x1010)=s;
    int skip=daynight_carlamps_flare(&c); *out=MF32(0x1010); return skip;
}
int main(void) {
    G=calloc(1,0x10000); float s;
    const uint32_t ret=0x0074323Au, LH=0x9DB90133u, RH=0xD09091C6u, LB=0x31A66786u, RB=0xBF700A79u, REV=0x7A5B2F25u;
    level=1;
    CHECK(!flare(ret,LH,1,&s) && s==1); CHECK(!flare(ret,LB,.5f,&s) && s==.5f);
    level=.5f;
    CHECK(!flare(ret,RH,1,&s) && s==.5f); CHECK(!flare(ret,RB,.5f,&s) && s==.25f);
    CHECK(!flare(ret,LB,1,&s) && s==1); CHECK(!flare(ret,REV,1,&s) && s==1);
    CHECK(!flare(0x00505F3Du,LH,1,&s) && s==1);
    level=0;
    CHECK(flare(ret,LH,1,&s)); CHECK(flare(ret,LB,.5f,&s)); CHECK(!flare(ret,RB,1,&s) && s==1);
    frame_now=60; flare(ret,LH,1,&s);
    CHECK(strstr(last,"frame=60 ") && strstr(last,"head_in=3.0000 head_out=1.5000"));
    printf("checks failures=%d\n",failures);
    return failures!=0;
}
'''


def game_proof(noon, noon_frame, midnight, midnight_frame):
    rows = []
    for path, frame in ((noon, noon_frame), (midnight, midnight_frame)):
        with Path(path).open() as stream:
            matches = [line for line in stream if f'daynight_carlamps: frame={frame} ' in line]
        assert len(matches) == 1, 'missing/duplicate shot-frame diagnostic: ' + str(path)
        rows.append({k: float(v) for k, v in re.findall(r'(\w+)=([0-9.]+)', matches[0])})
    day, night = rows
    print('noon head_in=%.4f head_out=%.4f; midnight head_in=%.4f head_out=%.4f' %
          (day['head_in'], day['head_out'], night['head_in'], night['head_out']))
    assert day['head_in'] > 0 and day['head_out'] == 0, 'noon headlights drawn'
    assert night['head_in'] > 0 and night['head_out'] == night['head_in'], 'midnight differs from retail'
    print('PASS daynight_carlamps game frames %d/%d' % (noon_frame, midnight_frame))


def run():
    with tempfile.TemporaryDirectory(prefix='carlamps-') as tmp:
        temp = Path(tmp)
        (temp / 'harness.c').write_text(HARNESS)
        broken = temp / 'broken.c'
        text = SOURCE.read_text()
        broken.write_text(text.replace('float level = daynight_lamp_level(), in', 'float level = 1, in'))
        assert broken.read_text() != text
        cmd = ['clang', '-w', '-std=c11', '-I' + str(RUNTIME), '-I' + str(ROOT / 'recomp'), str(temp / 'harness.c')]
        results = {}
        for label, source in (('normal', SOURCE), ('broken', broken)):
            binary = temp / label
            subprocess.run(cmd + ['-DTEST_SOURCE="' + str(source) + '"', '-o', str(binary)], check=True)
            results[label] = subprocess.run([str(binary)], capture_output=True, text=True)
            print(label, results[label].stdout.strip().splitlines()[-1])
        if results['normal'].returncode:
            raise AssertionError(results['normal'].stdout)
        assert results['broken'].returncode, 'level fixed at 1 passed'
    print('PASS daynight_carlamps: flare scale, day skip, shot-frame sum; broken control fails')
    return 0


if __name__ == '__main__':
    if len(sys.argv) == 6 and sys.argv[1] == '--proof':
        game_proof(sys.argv[2], int(sys.argv[3]), sys.argv[4], int(sys.argv[5]))
    else:
        raise SystemExit(run())
