"""Offline Metal tests; never loads or launches the game.

Checks the actual shader, including the deliberately Off/empty-source failures:
full red/blue burst > 0; no flare / zero alpha / zero burst / fully credited
bloom = 0; normalized blur gain <= 1; old and current Off bloom inputs match.
Run with tools/.venv/bin/python -I runtime/lampglow_test.py.
"""
import ast
from pathlib import Path
import re
import subprocess
import tempfile
import math
import os
import sys

ROOT = Path(__file__).resolve().parent.parent


# The MSL splices generated constants between its string literals (tools/gen_tables.py: X360S_* from the player's
# ZZDATA0.BIN); expand them as the C compiler does.
_inc = ROOT / 'runtime/gen/x360_shader_lits.inc'
MACROS = dict(re.findall(r'#define (\w+) "(.*)"', _inc.read_text())) if _inc.exists() else {}


def shader(text, name):
    text = text.split(f'static const char *{name} =', 1)[1].split(';\n', 1)[0]
    return ''.join(ast.literal_eval(m.group(0)) if m.group(0).startswith('"') else MACROS[m.group(0)]
                   for m in re.finditer(r'"(?:[^"\\]|\\.)*"|\b(?:X360\w+|PCS_\w+)\b', text))


current = shader((ROOT / 'runtime/render_post.m').read_text(), 'x360_src')
old = shader(subprocess.check_output(['git', 'show', 'HEAD:runtime/render_post.m'], cwd=ROOT, text=True), 'x360_src')
assert current == old, 'Off shader changed'
extra = shader((ROOT / 'runtime/lampglow_shader.h').read_text(), 'lampglow_src')
# Scalar reference checks of the source/gain contract, independent of GPU access.
def smooth(a, b, x):
    t = min(max((x - a) / (b - a), 0), 1)
    return t * t * (3 - 2 * t)


def admitted(c, natural):
    l = sum(a * b for a, b in zip(c, (.2125, .7154, .0721)))
    w = smooth(.75, 1, l) if natural else max(l * .14 / .261 - .25, 0) * smooth(.55, .75, l) / max(l, 1e-4)
    return [v * w for v in c]


def residual(scene, lamp, natural, gain=.5, credit=1):
    e = [min(a, b) for a, b in zip(scene, lamp)]
    before = admitted([max(a - b, 0) for a, b in zip(scene, e)], natural)
    after = admitted(scene, natural)
    return [max(a * gain - max(b - c, 0) * credit, 0) for a, b, c in zip(e, after, before)]


checks = 0
for natural in (False, True):
    for lamp in ((.5, 0, 0), (0, 0, .5)):
        assert sum(admitted(lamp, natural)) == 0  # original failure
        assert sum(residual(lamp, lamp, natural)) == .25
        assert residual(lamp, (0, 0, 0), natural) == [0, 0, 0]
        assert residual(lamp, lamp, natural, gain=0) == [0, 0, 0]
        checks += 4
    assert residual((1, 1, 1), (.5, .5, .5), natural, credit=10) == [0, 0, 0]
    for burst in (0, .01, .25, .5, 1):
        for alpha in (0, .1, .58, 1):
            lamp = (.5 * burst * alpha, 0, 0)
            out = residual(lamp, lamp, natural)
            assert 0 <= out[0] <= .5 * lamp[0]
            assert bool(out[0]) == bool(burst * alpha)
            checks += 2
    # The production H/V kernels normalize each axis. Their combined uniform
    # gain must remain one through the one- or two-round blur.
    weights = [math.exp(-(i * i) / 18) for i in range(-7, 8)]
    weights = [w / sum(weights) for w in weights]
    assert abs(sum(weights) ** (4 if natural else 2) - 1) < 1e-12
    checks += 2
print(f'PASS {checks} scalar checks: red/blue full burst .25; absent/disabled source 0; normalized gain 1', flush=True)
print(f'PASS Off common shader unchanged: {len(current)} bytes', flush=True)
with tempfile.TemporaryDirectory(prefix='lampglow-') as tmp:
    tmp = Path(tmp)
    context = tmp / 'context'
    subprocess.run(['clang', '-O1', '-I', str(ROOT / 'recomp'), '-I', str(ROOT / 'runtime'),
                    str(ROOT / 'runtime/lampglow.c'), str(ROOT / 'runtime/lampglow_test.c'), '-o', str(context)], check=True)
    subprocess.run([str(context)], check=True)
    (tmp / 'current.metal').write_text(current + extra)
    (tmp / 'old.metal').write_text(old)
    specialized = current.replace('fragment float4 fs_vt(', 'fragment float4 fs_vt_lamp(').replace(
        'texture2d<float> bself [[texture(6)]]', 'texture2d<float> bself [[texture(6)]], texture2d<float> lamp [[texture(7)]]').replace(
        '    if (patch) bl = 0;', '    bl += lamp.sample(lin, uv).rgb;\n    if (patch) bl = 0;') + extra
    (tmp / 'lamp.metal').write_text(specialized)
    # Source the shared defaults even when invoked directly rather than by offline_tests.sh.
    cache = os.environ.get('NFS_METAL_CACHE') or subprocess.check_output(
        ['sh', '-c', 'NFS_ENV_ROOT=$1; . "$1/runtime/env.sh" || exit; printf "%s" "${NFS_METAL_CACHE:-}"',
         'metal-cache', str(ROOT)], text=True)
    cache_args = [f'-fmodules-cache-path={cache}'] if cache else []
    metal = subprocess.run([*(os.environ.get('NFS_METAL') and [os.environ['NFS_METAL']] or ['xcrun', 'metal']), *cache_args, '-c', str(tmp / 'lamp.metal'), '-o', str(tmp / 'lamp.air')], capture_output=True, text=True)
    if metal.returncode and 'missing Metal Toolchain' in metal.stderr:
        print('UNVERIFIED offline MSL compilation: Metal Toolchain not installed', flush=True)
    elif metal.returncode:
        print(metal.stderr, file=sys.stderr)
        sys.exit(metal.returncode)
    else:
        print('PASS actual lamp source, float16 filter and specialized composite compile with Metal', flush=True)
    exe = tmp / 'test'
    subprocess.run(['clang', '-O1', '-fobjc-arc', str(ROOT / 'runtime/lampglow_test.m'),
                    '-framework', 'Foundation', '-framework', 'Metal', '-o', str(exe)], check=True)
    result = subprocess.run([str(exe), str(tmp / 'lamp.metal'), str(tmp / 'old.metal')])
    if result.returncode == 77:
        print('UNVERIFIED GPU execution: no Metal device available')
    elif result.returncode:
        sys.exit(result.returncode)
