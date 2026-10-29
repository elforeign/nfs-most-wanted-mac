#!/usr/bin/env python3
"""



"""
import os
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='envcube-test-') as scratch:
    exe = Path(scratch) / 'envcube'
    def compile_test(source, output):
        subprocess.run(['clang', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
                    '-fobjc-arc', '-I', str(root / 'recomp'),
                    '-I', str(root / 'runtime'), str(source), str(root / 'runtime/render_thread.m'),
                    '-framework', 'Metal', '-framework', 'Foundation', '-o', str(output)], check=True)
    compile_test(root / 'runtime/envcube_test.m', exe)
    for mode in ['--cpu'] + (['--gpu'] if os.environ.get('CODEX_GPU_CHECK') == '1' else []):
        paths = ['0', '1'] if mode == '--gpu' else ['0']
        for replay in paths:
            env = {**os.environ, 'NFS_RENDER_THREAD': replay, 'MTL_DEBUG_LAYER': '1'}
            good = subprocess.run([exe, mode], env=env, capture_output=True, text=True)
            assert good.returncode == 0, good.stdout + good.stderr
            print(good.stdout.strip(), 'replay=' + replay, flush=True)
            bad = subprocess.run([exe, mode, '--broken'], env=env, capture_output=True, text=True)
            assert bad.returncode == 1, bad.stdout + bad.stderr
            assert 'generated=0' in bad.stdout, bad.stdout
            assert ('bad_samples=60/60' if mode == '--gpu' else 'completed=0') in bad.stdout, bad.stdout
            print('PASS negative control:', bad.stdout.strip(), 'replay=' + replay, flush=True)
    original = (root / 'runtime/envcube.m').read_text()
    mutant = original.replace('return !value || strcmp(value, "pc");',
                              'return value && !strcmp(value, "sharp");')
    assert mutant != original
    mutant_source = Path(scratch) / 'envcube_mutant.m'
    mutant_source.write_text(mutant)
    harness = Path(scratch) / 'envcube_mutant_test.m'
    harness.write_text((root / 'runtime/envcube_test.m').read_text().replace(
        '#include "envcube.m"', f'#include "{mutant_source}"'))
    mutant_exe = Path(scratch) / 'envcube_mutant'
    compile_test(harness, mutant_exe)
    bad = subprocess.run([mutant_exe, '--cpu'], capture_output=True, text=True)
    assert bad.returncode == 1 and 'DEFAULT FAIL option=unset edge=256 levels=1' in bad.stdout, bad.stdout + bad.stderr
    print('PASS PC-default negative control:', bad.stdout.strip(), flush=True)
    if os.environ.get('CODEX_GPU_CHECK') != '1':
        print('GPU PENDING: run on a Metal-capable host: CODEX_GPU_CHECK=1 runtime/offline_tests.sh envcube_test')
