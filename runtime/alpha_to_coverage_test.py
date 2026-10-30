"""Exercise production draw selection, pipeline bytes/flags and translated PS output.

Controls remove the key bit, disable the PSO flag, or restore the old discard.
The GPU case measures fractional edges, opaque RGB and integrated silhouette
width; equality/disabled/blended/1x paths retain the original alpha test.
Observed: 79 CPU checks pass; missing key bit gives 1 failure. GPU: 224
configurations pass, 79 fractional pixels; disabled PSO gives 94 failures,
old discard gives 1197. Edge error <=0.251 px; thin-wire error <=0.5 px
per phase and <=0.125 px averaged over the four subpixel positions.
"""
from pathlib import Path
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def run(args, expected=0):
    p = subprocess.run([str(a) for a in args], capture_output=True, text=True,
                       env={**os.environ, 'NFS_ALPHA_TO_COVERAGE': 'off'})
    print((p.stdout + p.stderr)[-3500:], end='', flush=True)
    assert p.returncode == expected, (args, p.returncode)
    return p.stdout


src = (ROOT / 'runtime/render.m').read_text()
key = src.split('    int a2c = alpha_to_coverage_draw(rs,', 1)[1]
key = '    int a2c = alpha_to_coverage_draw(rs,' + key.split('    static typeof(pk) last_pk;', 1)[0]
flag = '        pd.alphaToCoverageEnabled = a2c;'
assert flag in src
aux = src.split('    pa.alpha_func = rs[15]', 1)[1].split('    for (int s = 0; s < 8;', 1)[0]
aux = '    pa.alpha_func = rs[15]' + aux
assert 'pa.alpha_to_coverage = 0; // blended fringe' in src
replacement = next(line for line in src.splitlines() if 'pd.alphaToCoverageEnabled = !ssao_replay' in line)
assert 'alpha_blend || rs[27]' in replacement

with tempfile.TemporaryDirectory(prefix='a2c-test-') as tmp:
    tmp = Path(tmp)
    inc = tmp / 'pipeline.inc'
    inc.write_text(key + '\n' + flag + '\n' + aux)
    # Build the library directly: this check also runs bare without game assets.
    objects = []
    for name in ('sm_parse', 'sm_msl'):
        obj = tmp / (name + '.o')
        run(['clang++', '-std=c++17', '-O1', '-c', ROOT / 'host/shaders' / (name + '.cpp'), '-o', obj])
        objects.append(obj)
    exe = tmp / 'check'
    command = ['clang', '-O1', '-Wall', '-Wextra', '-Werror', '-fobjc-arc',
               '-I', tmp, '-I', ROOT / 'runtime', '-I', ROOT / 'host/shaders',
               ROOT / 'runtime/alpha_to_coverage_test.m', *objects, '-lc++',
               '-framework', 'Foundation', '-framework', 'Metal', '-o', exe]
    run(command)
    run([exe, '--cpu'])
    inc.write_text((key + '\n' + flag + '\n' + aux).replace('(blend | (a2c << 1))', 'blend'))
    run(command)
    run([exe, '--cpu'], expected=1)
    print('PASS negative control: missing coverage key bit rejected', flush=True)
    inc.write_text(key + '\n' + flag + '\n' + aux)
    run(command)
    if os.environ.get('CODEX_GPU_CHECK') == '1' or os.environ.get('NFS_GPU_SANDBOX') == '1':
        proof = run([exe])
        proof += 'Disabled pipeline control:\n' + run([exe, '--broken-pipeline'], expected=1)
        proof += 'Original discard control:\n' + run([exe, '--broken-discard'], expected=1)
        cache = ROOT / '.metal-cache'
        cache.mkdir(exist_ok=True)
        (cache / 'a2c-test-proof.txt').write_text(proof)
        print('PASS GPU controls: disabled PSO and original discard rejected', flush=True)
    else:
        print('GPU PENDING: run_tests alpha_to_coverage_test', flush=True)
