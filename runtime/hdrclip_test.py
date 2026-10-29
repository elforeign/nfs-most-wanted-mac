#!/usr/bin/env python3
"""Native counter + disabled-counter mutant. Bare input, no private assets required.
GPU proof: normal 2688 rgb_hi, mutant 0 must fail; Off bytes identical.
A game_run runs the identical check with BOOT_DIAG_HDR_CLIP_TEST=1.

Observed race proof (not pinned test expectations): game_run 4-hdrclip-raster-counts,
frame 2079, sim 15.008190, 3440x2160, MSAA 4; GPU command status 4 (completed).
Exports are pre-depth/blend invocations; stored white endpoints are censored.
Stage               export_n    rgb_hi  rgb_lo alpha_hi nonfinite stored_n white_rgb
world_export       251564888   9075540       4   957329    828027        0         0
other_export        30069449   2292614       0   239334         0        0         0
msaa_samples              0         0       0        0         0 29721600        18
scene_resolve             0         0       0        0         0  7430400         4
scene_copy                0         0       0        0         0  7430400         4
downscale            464400         0       0        0         0   464400         0
downscale_mask       464400         0       0        0         0   464400         0
luminance              4096         0       0        0         0     4096        40
blur5                464400         0       0        0         0   464400         0
bloom_down           116100         0       0        0         0   116100         0
bloom_h              116100         0       0     3747         0   116100         0
bloom_v              116100         0       0     5223         0   116100         0
bloom_h2             116100         0       0     9450         0   116100         0
bloom_v2             116100         0       0    13280         0   116100         0
curve_luminance       51660         0       0        0         0    51660         0
final              29721600         0   25620        0         0 29721600         0
final_resolve             0         0       0        0         0  7430400         0
Bright, DOF, lamps and mirror: n=0, not executed in this default Natural race frame.
All stored bounds/nonfinite, and all alpha_lo counts: zero.
CSV negative control: downscale export n=0 instead of 464400 is rejected.
Native negative control: rgb_hi disabled -> 0 instead of 2688, test returns 1.
"""
from pathlib import Path
import csv
import copy
import subprocess
import sys
import tempfile
def check_rows(rows):
    data = {(r['stage'], r['phase']): {k: int(v) for k, v in r.items()
            if k not in ('stage', 'phase')} for r in rows}
    assert len(data) == 50, 'missing/duplicate stage rows'
    required = [('world_export', 'export')] + [(s, 'stored') for s in
                ('msaa_samples', 'scene_resolve', 'scene_copy', 'final', 'final_resolve')]
    core = ('downscale', 'downscale_mask', 'luminance', 'blur5', 'bloom_down', 'bloom_h', 'bloom_v')
    for stage in core:
        required.extend(((stage, 'export'), (stage, 'stored')))
    for key in required:
        assert data[key]['n'] > 0, f'{key}: missing counter'
    for (stage, phase), r in data.items():
        assert all(v >= 0 for v in r.values())
        assert r['rgb_hi'] <= r['n'] * 3 and r['rgb_lo'] <= r['n'] * 3
        assert r['alpha_hi'] <= r['n'] and r['nonfinite'] <= r['n']
        if phase == 'export' and stage in (*core, 'bright', 'dof', 'curve_luminance',
                                           'bloom_h2', 'bloom_v2') and r['n']:
            assert data[(stage, 'stored')]['n'] == r['n'], f'{stage}: store coverage'
    assert data[('scene_copy', 'stored')]['n'] == data[('scene_resolve', 'stored')]['n']
    assert data[('final_resolve', 'stored')]['n'] == data[('scene_resolve', 'stored')]['n']
    return data

if len(sys.argv) == 3 and sys.argv[1] == '--csv':
    with Path(sys.argv[2]).open() as f:
        metadata = f.readline()
        assert 'status=4;' in metadata, 'GPU command buffer did not complete'
        rows = list(csv.DictReader(f))
    data = check_rows(rows)
    broken = copy.deepcopy(rows)
    next(r for r in broken if r['stage'] == 'downscale' and r['phase'] == 'export')['n'] = '0'
    try:
        check_rows(broken)
    except AssertionError:
        print(f"hdrclip CSV: PASS 50 rows; disabled-stage mutant REJECTED (0 vs {data[('downscale', 'export')]['n']})")
        sys.exit(0)
    sys.exit('FAIL: disabled-stage mutant survived')

root = Path(__file__).resolve().parent
with tempfile.TemporaryDirectory(prefix='hdrclip-test-') as td:
    binary = Path(td) / 'test'
    log = Path(td) / 'build.log'
    with log.open('w') as f:
        rc = subprocess.run(['clang', '-O1', '-fobjc-arc', str(root / 'hdrclip_test.m'),
                             '-framework', 'Foundation', '-framework', 'Metal', '-o', str(binary)],
                            stdout=f, stderr=subprocess.STDOUT).returncode
    if rc:
        print('\n'.join(log.read_text().splitlines()[-20:]))
        sys.exit(1)
    good = subprocess.run([str(binary)], capture_output=True, text=True)
    print(good.stderr, end='')
    if good.returncode == 3:
        sys.exit(3)
    if good.returncode:
        sys.exit(1)
    bad = subprocess.run([str(binary), '--mutant'], capture_output=True, text=True)
    print(bad.stderr, end='')
    if bad.returncode != 1 or 'rgb_hi=0 expected=2688' not in bad.stderr:
        sys.exit('FAIL: disabled-counter mutant was not detected')
    print('hdrclip_test: PASS; normal=0 mutant=1')
