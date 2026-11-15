#!/usr/bin/env python3
"""Tunnel shadows "Keep + sky light": a car in a cast shadow loses only the sun, not its two fill lights.
Renders the real car paint PS (materials.m car_paint_msl) for one rear-panel pixel with the free-roam spawn's light
rig (carshadow_sky_test.m). Normal: shadowed panel luma Keep 0.0562 -> sky 0.1060 (x1.89), lit pixel identical.
Broken control (the sky block disabled, as on the code before it): x1.00, must fail. No GPU or tables: exit 3.
"""
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parent
tables = [root / 'gen' / f for f in ('car_shadow_off.inc', 'pc_shader_lits.inc', 'x360_shader_lits.inc')]
if any(not f.is_file() for f in tables) or '#define X360_SHADER_LITS 1' not in tables[-1].read_text():
    print('carshadow_sky_test: SKIP generated private shader tables missing')
    sys.exit(3)
source = (root / 'materials.m').read_text()
helper = source[source.index('#define CAR_PAINT_POW_MSL'):source.index('\n\nstatic const char car_paint_msl')]
body = source[source.index('static const char car_paint_msl'):source.index('\n// BOOT_DIAG_CARPAINT_AB=1')]
flag = '"    if (m.k[7].z > 0.5f) {'
assert body.count(flag) == 1, 'sky block missing from the car paint PS'


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    print(p.stderr, end='')
    return p


with tempfile.TemporaryDirectory(prefix='carshadow-sky-') as td:
    td = Path(td)
    c = td / 'extract.c'
    c.write_text('#include <stdio.h>\n#include "../host/shaders/sm_coverage.h"\n#include "gen/car_shadow_off.inc"\n'
                 '#include "gen/pc_shader_lits.inc"\n#include "gen/x360_shader_lits.inc"\n' + helper + '\n' + body +
                 '\nint main(void) { fputs(car_paint_msl, stdout); return 0; }\n')
    if run(['clang', '-O1', '-I', str(root), str(c), '-o', str(td / 'extract')]).returncode:
        sys.exit(1)
    shader = subprocess.run([str(td / 'extract')], capture_output=True, text=True, check=True).stdout
    (td / 'sky.metal').write_text(shader)
    (td / 'broken.metal').write_text(shader.replace('    if (m.k[7].z > 0.5f) {', '    if (false) {', 1))
    if run(['clang', '-O1', '-fobjc-arc', str(root / 'carshadow_sky_test.m'), '-framework', 'Foundation',
            '-framework', 'Metal', '-o', str(td / 'test')]).returncode:
        sys.exit(1)
    normal = run([str(td / 'test'), str(td / 'sky.metal')])
    if normal.returncode == 3:
        sys.exit(3)
    if normal.returncode:
        sys.exit('FAIL: the shadowed panel does not keep its fill lights, or a lit pixel changed')
    broken = run([str(td / 'test'), str(td / 'broken.metal')])
    if broken.returncode != 1:
        sys.exit('FAIL: the broken control (no sky block) passed')
    print('carshadow_sky_test: normal PASS, broken control fails as required')
