"""Pure row-to-bars regression using the bounds hook and translated bar arithmetic.

The retail slow endpoint is zero, so this checks the required all-car fallback.
Old retail-only bounds: 0/6 interior below-retail bars positive (exit 1).
Original all-car bounds: 6/6 positive and strictly ordered (exit 0).
Retail-only and entirely in-range additions retain bit-identical bar floats.
The absolute weakest car remains zero, as the original linear mapping requires.
The pinned predecessor is compiled and rejected on every run, without history.
No game process, private assets, emulator or GPU. Generated output is required.
"""
import hashlib
import math
from pathlib import Path
import random
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]

# Pre-fix bounds hook, pinned independently of the implementation under test.
PREDECESSOR = r'''
void f_006794E0(Cpu *c) {
    if (!performance_allocations) { f_006794E0_orig(c); return; }
    uint32_t head=M32(c->ecx+4);
    float direction=MF32(c->esp+4);
    uint32_t dest=M32(c->esp+8);
    float bounds[3]={0,0,0};
    unsigned seen=0;
    for (uint32_t row=M32(head);row!=head;row=M32(row)) {
        unsigned j=0;
        while (j<nadditions && additions[j].key!=M32(row+0x2c)) ++j;
        if (j<nadditions) continue;
        for (unsigned i=0;i<3;++i) {
            float value=MF32(row+8+4*i);
            if (!seen || direction*bounds[i]<direction*value) bounds[i]=value;
        }
        ++seen;
    }
    memcpy(G+dest,bounds,sizeof bounds);
    c->esp+=12;
}
'''
PREDECESSOR_SHA256 = '9ba38ef114b9cf0e7156cbf77eb4de1c8c98fcbc4381acbe06cdb62796956fb5'

HARNESS = r'''
#include "runtime.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>

uint8_t *G;
static unsigned nadditions;
static struct { uint32_t key; } additions[256];
static int performance_allocations;
enum { LIST=0x8000, HEAD=0x10000, ROWS=0x11000, STACK=0x20000 };

TRANSLATED_BOUNDS
PRODUCTION_HOOK
PREDECESSOR_HOOK

/* Only the original stock-row arithmetic and its three saturation branches.
 * Bounds and row construction are the pure mapping's inputs. */
static void normalize(Cpu *c) {
    uint8_t cf=0,pf=0,af=0,zf=0,sf=0,of=0;
    (void)cf; (void)pf; (void)af; (void)zf; (void)sf; (void)of;
TRANSLATED_BARS
    return;
TRANSLATED_SATURATION
}

int main(int argc,char **argv) {
    G=calloc(1,0xA00000);
    assert(G);
    unsigned count,active;
    while (scanf("%u %u",&count,&active)==2) {
        assert(count<=256);
        memset(G,0,0xA00000);
        nadditions=0;
        performance_allocations=active;
        M32(LIST+4)=HEAD;
        M32(0x92c4e8)=HEAD;
        M32(HEAD)=count ? ROWS : HEAD;
        MF32(0x890968)=0;
        MF32(0x89096c)=1;
        MF32(0x8a92d4)=1e-6f;
        for (unsigned i=0;i<count;++i) {
            unsigned pack;
            float time,speed,handling;
            assert(scanf("%u %f %f %f",&pack,&time,&speed,&handling)==4);
            uint32_t row=ROWS+0x40*i;
            M32(row)=i+1<count ? row+0x40 : HEAD;
            M32(row+0x2c)=i+1;
            MF32(row+8)=time;
            MF32(row+12)=speed;
            MF32(row+16)=handling;
            if (pack) additions[nadditions++].key=i+1;
        }
        for (unsigned maximum=0;maximum<2;++maximum) {
            Cpu c={.ecx=LIST,.esp=STACK,.cw=0x37f,
                   .ebx=123,.esi=456,.edi=789,.ebp=1011};
            MF32(STACK+4)=maximum ? 1 : -1;
            M32(STACK+8)=maximum ? 0x92619c : 0x92c578;
            if (argc>1 && !strcmp(argv[1],"--predecessor")) predecessor(&c);
            else if (argc>1 && !strcmp(argv[1],"--original")) f_006794E0_orig(&c);
            else f_006794E0(&c);
            assert(c.esp==STACK+12);
            assert(c.ebx==123 && c.esi==456 && c.edi==789 && c.ebp==1011);
            assert(c.top==0);
        }
        printf("B");
        for (unsigned bound=0;bound<2;++bound)
            for (unsigned metric=0;metric<3;++metric)
                printf(" %a",MF32((bound ? 0x92619c : 0x92c578)+4*metric));
        puts("");
        if (count) {
            Cpu c={.ecx=ROWS,.esp=STACK,.ebp=HEAD,.cw=0x37f};
            normalize(&c);
            assert(c.top==0);
        }
        for (unsigned i=0;i<count;++i) {
            uint32_t row=ROWS+0x40*i;
            printf("R %a %a %a\n",MF32(row+0x14),MF32(row+0x18),MF32(row+0x1c));
        }
    }
    free(G);
    return 0;
}
'''


def function(text, name):
    match = re.search(r'^void ' + name + r'\(Cpu \*(?:restrict )?c\) \{.*?\n}', text, re.M | re.S)
    assert match, name
    return match[0]


def build(work):
    bounds = (ROOT / 'recomp/out/speed_35.c').read_text()
    init = function((ROOT / 'recomp/out/speed_36.c').read_text(), 'f_00683EB0_orig')
    bars = init.split('L_00684260:;', 1)[1].split('L_00684397:;', 1)[0]
    saturate = init.split('L_00684511:;', 1)[1].split('L_00684532:;', 1)[0]
    hook = function((ROOT / 'runtime/car_performance_attrib_impl.h').read_text(), 'f_006794E0')
    assert hashlib.sha256(PREDECESSOR.encode()).hexdigest() == PREDECESSOR_SHA256
    source = (HARNESS.replace('TRANSLATED_BOUNDS', function(bounds, 'f_006794E0_orig'))
              .replace('PRODUCTION_HOOK', hook)
              .replace('PREDECESSOR_HOOK', PREDECESSOR.replace('f_006794E0(Cpu', 'predecessor(Cpu'))
              .replace('TRANSLATED_BARS', 'L_00684260:;' + bars)
              .replace('TRANSLATED_SATURATION', 'L_00684511:;' + saturate))
    src, binary = work / 'bars.c', work / 'bars'
    src.write_text(source)
    result = subprocess.run(['clang', '-std=c11', '-O1', '-Wall', '-Wextra', '-Werror',
                             '-fsanitize=address,undefined', '-I', str(ROOT / 'recomp'),
                             str(src), '-o', str(binary)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-3000:]
    return binary


def map_cases(binary, cases, mode=''):
    """Pure normalization: (pack flag, time, speed, handling) rows -> bounds/bars."""
    request = ''.join(f'{len(rows)} {active}\n' + ''.join(
        ' '.join(str(v) for v in row) + '\n' for row in rows) for rows, active in cases)
    result = subprocess.run([str(binary), *([mode] if mode else [])], input=request,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-3000:]
    output = iter(result.stdout.splitlines())
    answers = []
    for rows, _ in cases:
        bound = next(output).split()
        assert bound.pop(0) == 'B'
        bars = []
        for _ in rows:
            row = next(output).split()
            assert row.pop(0) == 'R'
            bars.append(tuple(float.fromhex(v) for v in row))
        answers.append((tuple(float.fromhex(v) for v in bound), bars))
    assert next(output, None) is None
    return answers


def check(binary, mode=''):
    # Raw input order is time, speed, handling; displayed order is speed, handling, acceleration.
    retail = [(0, 1.5, 110, 100), (0, 11.5, 74, 36), (0, 7, 90, 70)]
    in_range = [(1, 8, 85, 60), (1, 6, 95, 80)]
    below = [(1, 16, 60, 15), (1, 14, 65, 25), (1, 12, 70, 30)]
    above = [(1, 1, 120, 105), (1, .5, 130, 110)]
    cases = [(retail, 1), (retail + in_range, 1), (retail + below, 1),
             (retail + above, 1), (retail + below, 0), ([], 1),
             ([(0, 5, 80, 50)] * 2, 1), (below, 1)]
    rng = random.Random(6794)
    for _ in range(24):
        rows = retail + [(1, rng.uniform(.5, 25), rng.uniform(40, 150), rng.uniform(10, 120))
                         for _ in range(12)]
        rng.shuffle(rows)
        cases.append((rows, 1))
    actual = map_cases(binary, cases, mode)
    original = map_cases(binary, cases, '--original')
    old = map_cases(binary, cases, '--predecessor')
    positive = sum(v > 0 for row in actual[2][1][-2:] for v in row)
    print(f'{mode or "current"}: below-retail positive={positive}/6')
    assert positive == 6, f'below-retail positive={positive}/6'
    assert actual == original, 'all-car bounds/bars differ from the translated original'
    assert actual[:2] == old[:2], 'retail-only or in-range-only bars moved'
    assert actual[4] == old[4], 'disabled source performance changed'
    assert actual[2][1][-3] == (0, 0, 0), 'absolute weakest row should retain the native zero'
    assert actual[6][1] == [(0, 0, 1)] * 2, 'degenerate ranges changed'
    for index in (2, 3, 7, *range(8, len(cases))):
        rows, _ = cases[index]
        bars = actual[index][1]
        for raw_index, bar_index, sign in ((2, 0, 1), (3, 1, 1), (1, 2, -1)):
            ordered = sorted(zip(rows, bars), key=lambda pair: sign * pair[0][raw_index])
            for (raw_a, a), (raw_b, b) in zip(ordered, ordered[1:]):
                assert 0 <= a[bar_index] <= b[bar_index] <= 1
                if raw_a[raw_index] != raw_b[raw_index]:
                    assert a[bar_index] < b[bar_index], 'distinct raw values collapsed'
    assert all(math.isfinite(v) for _, bars in actual for row in bars for v in row)
    print(f'PASS: {len(cases)} mapping cases; original floats, ordering, Off, empty and degenerate bounds')


def main():
    if not all((ROOT / f'recomp/out/speed_{shard}.c').exists() for shard in (35, 36)):
        print('SKIP: needs generated recompiler output')
        return 3
    with tempfile.TemporaryDirectory(prefix='performance-bars-') as tmp:
        binary = build(Path(tmp))
        if '--predecessor' in sys.argv:
            check(binary, '--predecessor')
            return 0
        check(binary)
        try:
            check(binary, '--predecessor')
        except AssertionError as error:
            assert str(error) == 'below-retail positive=0/6', str(error)
            print('PASS: pinned old clamp rejected (0/6; exit 1 control)')
        else:
            raise AssertionError('old clamp unexpectedly passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
