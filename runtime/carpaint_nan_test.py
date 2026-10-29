#!/usr/bin/env python3
"""Actual car-paint shader: powr(0,0) control 2048 bad pixels -> guarded 0.
GPU: 67,584 finite pixels / 1,351,680 FP32+BGRA8 bytes identical; generated tables.
CPU: Xenos finite LOG_CLAMP * zero -> EXP=1; unguarded log(0)*0 -> NaN.
Native numbers are expectations until recorded in README. Broken guard must fail.
Bare run is autodiscovered by offline_tests.sh; no GPU exits 3 for post-run check.
Race CSV control: 8 changed BGRA8 candidates rejected; final: 0 accepted.
Tint shot control: before/after 0/0; forced visible marker 412,028 pixels.
"""
from pathlib import Path
import re
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parent
if len(sys.argv) == 5 and sys.argv[1] == '--tint-shots':
    from PIL import Image
    import numpy as np
    counts = []
    contrast = []
    for path in sys.argv[2:]:
        pixels = np.asarray(Image.open(path).convert('RGB')).astype(np.int16)
        r, g, b = (pixels[:, :, k] for k in range(3))
        counts.append(int(np.count_nonzero((r > 180) & (g < 80) & (b > 180))))
        contrast.append(int(np.count_nonzero(np.minimum(r, b) - g > 40)))
    assert counts[0] == counts[1] == contrast[0] == contrast[1] == 0, 'visible nonfinite tint'
    assert counts[2] > 0 and contrast[2] > 0, 'tint detector has no positive control'
    print(f'carpaint tint: before={counts[0]} after={counts[1]} positive_control={counts[2]} '
          f'purple_contrast_counts={contrast} PASS')
    sys.exit(0)
# In this diagnostic ONLY, the normally unused world_export/stored row holds:
# total, original_bad, fixed_bad, finite, FP32_recompute_changed, bad_zero_zero,
# changed_BGRA8_rounding, changed_finite_power_bits. It is not a texture read.
if len(sys.argv) == 3 and sys.argv[1] == '--csv':
    import csv
    with Path(sys.argv[2]).open() as f:
        assert 'status=4;' in f.readline(), 'incomplete GPU command buffer'
        rows = list(csv.DictReader(f))
    columns = ('n', 'rgb_hi', 'rgb_lo', 'alpha_hi', 'alpha_lo', 'nonfinite', 'rgb_white', 'alpha_white')
    r = next(r for r in rows if (r['stage'], r['phase']) == ('world_export', 'stored'))
    n, old, new, finite, fp, zero, bgra, power = (int(r[k]) for k in columns)
    assert n > 0 and old > 0 and n == old + finite, 'missing A/B coverage'
    assert new == 0 and old == zero, 'unfixed or unexplained nonfinite output'
    assert bgra == 0 and power == 0, 'finite output changed'
    print(f'carpaint race: original_nonfinite={old} fixed=0 finite={finite} '
          f'changed_BGRA8=0 changed_power_bits=0 FP32_recompute_changed={fp} PASS')
    sys.exit(0)
tables = [root / 'gen' / f for f in ('car_shadow_off.inc', 'pc_shader_lits.inc', 'x360_shader_lits.inc')]
if any(not f.is_file() for f in tables) or '#define X360_SHADER_LITS 1' not in tables[-1].read_text():
    print('carpaint_nan_test: SKIP generated private shader tables missing')
    sys.exit(3)
source = (root / 'materials.m').read_text()
helper = source[source.index('#define CAR_PAINT_POW_MSL'):source.index('\n\nstatic const char car_paint_msl')]
body = source[source.index('static const char car_paint_msl'):source.index('\n// BOOT_DIAG_CARPAINT_AB=1')]
spec_lines = re.findall(r'^"\s+float spec = .*$', body, re.M)
assert len(spec_lines) == 2 and sum(line.count('car_spec_pow(rv,') for line in spec_lines) == 4, \
    'both lobes / both paint paths must use guarded op'
assert 'if (x == 0.0f && e == 0.0f) return 1.0f;' in helper
with tempfile.TemporaryDirectory(prefix='carpaint-nan-') as td:
    td = Path(td)
    c = td / 'extract.c'
    c.write_text('''#include <stdio.h>
#include <math.h>
#include <float.h>
#include "gen/car_shadow_off.inc"
#include "gen/pc_shader_lits.inc"
#include "gen/x360_shader_lits.inc"
''' + helper + '\n' + body + '''
int main(void) {
    volatile float zero=0, neginf=-INFINITY;
    float raw=exp2f(zero*neginf), decoded=exp2f(zero*-FLT_MAX);
    if(!isnan(raw) || decoded!=1.0f || powf(zero,zero)!=1.0f)return 1;
    fprintf(stderr,"carpaint_nan_test: CPU raw=NaN decoded_LOG_CLAMP_EXP=1 pow(0,0)=1 PASS\\n");
    fputs(car_paint_msl,stdout); return 0;
}
''')
    def compile_cmd(args):
        p = subprocess.run(args, capture_output=True, text=True)
        if p.returncode:
            print('\n'.join(p.stderr.splitlines()[-20:]))
            sys.exit(1)
    compile_cmd(['clang', '-O1', '-I', str(root), str(c), '-o', str(td / 'extract')])
    extracted = subprocess.run([str(td / 'extract')], capture_output=True, text=True)
    print(extracted.stderr, end='')
    if extracted.returncode:
        sys.exit(1)
    shader = td / 'paint.metal'
    shader.write_text(extracted.stdout)
    binary = td / 'test'
    compile_cmd(['clang', '-O1', '-fobjc-arc', str(root / 'carpaint_nan_test.m'),
                 '-framework', 'Foundation', '-framework', 'Metal', '-o', str(binary)])
    normal = subprocess.run([str(binary), str(shader)], capture_output=True, text=True)
    print(normal.stderr, end='')
    if normal.returncode == 3:
        sys.exit(3)
    if normal.returncode:
        sys.exit(1)
    broken = subprocess.run([str(binary), str(shader), '--broken'], capture_output=True, text=True)
    print(broken.stderr, end='')
    if broken.returncode != 1 or 'fixed_nonfinite=2048 original_nonfinite=2048' not in broken.stderr:
        sys.exit('FAIL: missing zero-exponent guard was not detected')
    finite = subprocess.run([str(binary), str(shader), '--finite-mutant'], capture_output=True, text=True)
    print(finite.stderr, end='')
    if finite.returncode != 1 or 'finite=67584 changed=67584' not in finite.stderr:
        sys.exit('FAIL: changed finite output was not detected')
    print('carpaint_nan_test: normal=0 broken=1 finite-mutant=1 PASS')
