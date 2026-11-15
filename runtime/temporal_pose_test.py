"""Queued rigid-part poses: production temporal_state.c and exact shipping hooks.
ASan/UBSan: production 551 checks / 0 failures (exit 0);
stale generation accepted 551 checks / 10 failures (exit 1);
commit each draw 551 checks / 26 failures (exit 1).
Destructible left static: 551 checks / 17 failures (exit 1).
Controls accept stale slot/batch generations and commit each draw, respectively.
Covers queue shuffle, shared models/wheels, final submitted transforms, LOD,
traffic/cop lifetime reuse, reflection-first, split P0/P1, missing/unencoded
frames, resets, overflow, skipped records and a disabled-path entry counter.
Autodiscovered by offline_tests.sh; no game, assets, Metal or sockets required.
"""
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent


def function(source, name):
    match = re.search(r'^[^\n;]*\b' + name + r'\([^\n]*\) \{.*?\n}', source, re.M | re.S)
    assert match, name
    return match[0]


def main():
    materials = (ROOT / 'materials.m').read_text()
    wide = (ROOT / 'widescreen.c').read_text()
    hooks = (ROOT.parent / 'recomp/hooks.txt').read_text()
    for address in ('0074E160', '007387E0', '006DA9B0', '006E0AB0', '006D7660', '006E0200'):
        assert re.search(r'^' + address + r'\s+materials.m', hooks, re.M), address
    diag = (ROOT / 'diag.c').read_text()
    for name in ('materials_car_diagnostics', 'materials_car_part_diagnostics'):
        body = function(diag, name)
        assert ('f_0074E160_orig(c)' if name == 'materials_car_diagnostics' else 'f_007387E0_orig(c)') in body
    for name, delegate in [('f_0074E160', 'materials_car_diagnostics'),
                           ('f_007387E0', 'materials_car_part_diagnostics'),
                           ('f_006D7660', 'materials_queue_record'),
                           ('f_006E0200', 'materials_queue_flush')]:
        assert f'if (!temporal_state_requested) {{ {delegate}(c); return; }}' in function(materials, name)
    assert 'if (temporal_state_requested) temporal_materials_pose_append(old);' in function(materials, 'f_006E0AB0')
    names = ['f_0074E160', 'f_007387E0', 'temporal_materials_model_render',
             'temporal_materials_pose_append', 'f_006DA9B0', 'f_006E0AB0',
             'materials_queue_record', 'f_006D7660', 'materials_queue_flush', 'f_006E0200']
    destructible = ['f_0073B9C0', 'f_00749BE0', 'f_00753650', 'f_0075A130',
                   'f_0075A200', 'f_0075A330', 'f_0075A400', 'f_0075A520']
    for name in destructible:
        assert re.search(r'^' + name[2:] + r'\s+materials.m', hooks, re.M), name
    names = destructible + names
    shipping = '\n'.join(function(materials, n) for n in names) + '\n' + function(wide, 'f_006C8000')
    variants = [('production', None, 0, 0), ('stale generation accepted', 'TEMPORAL_TEST_STALE_GENERATION', 1, 10),
                ('commit each draw', 'TEMPORAL_TEST_COMMIT_EACH_DRAW', 1, 26),
                ('destructible left static', 'TEMPORAL_TEST_STATIC_DESTRUCTIBLE', 1, 17)]
    with tempfile.TemporaryDirectory(prefix='temporal-pose-') as td:
        directory = Path(td)
        (directory / 'temporal_pose_hooks.inc').write_text(shipping)
        for label, mutation, rc, failures in variants:
            exe = directory / label.replace(' ', '-')
            command = ['clang', '-std=c11', '-Wall', '-Wextra', '-Werror', '-g', '-O1',
                       '-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-finstrument-functions',
                       '-I', str(ROOT), '-I', str(ROOT.parent / 'recomp'), '-I', str(directory),
                       str(ROOT / 'temporal_pose_test.c'), str(ROOT / 'temporal_state.c'), '-o', str(exe)]
            if mutation:
                command.append('-D' + mutation)
            p = subprocess.run(command, capture_output=True, text=True)
            assert p.returncode == 0, p.stderr[-6000:]
            p = subprocess.run([str(exe)], capture_output=True, text=True)
            print(label + ': ' + p.stdout.strip(), flush=True)
            assert p.returncode == rc, p.stderr[-6000:]
            assert 'AddressSanitizer' not in p.stderr and 'runtime error:' not in p.stderr, p.stderr[-6000:]
            counts = re.search(r'(\d+) checks / (\d+) failures', p.stdout)
            assert counts and (int(counts[1]), int(counts[2])) == (551, failures)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
