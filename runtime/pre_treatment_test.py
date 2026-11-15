#!/usr/bin/env python3
"""







"""
from pathlib import Path
import os
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
proof = []
proof_path = ROOT/'.metal-cache/pre-treatment-proof.log'
proof_path.parent.mkdir(exist_ok=True)
with tempfile.TemporaryDirectory(prefix='pre-treatment-') as temporary:
    exe = Path(temporary)/'fixture'
    p = subprocess.run(['clang', '-O2', '-Wall', '-Wextra', '-Werror', '-fobjc-arc',
                        '-I', str(ROOT/'runtime'), '-I', str(ROOT/'recomp'),
                        str(ROOT/'runtime/pre_treatment_test.m'), str(ROOT/'runtime/pre_treatment.m'),
                        '-framework', 'Foundation', '-framework', 'Metal', '-o', str(exe)],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr[-4000:]
    for mode in ([], ['--wrong-order'], ['--gpu']):
        p = subprocess.run([exe, *mode], capture_output=True, text=True, env={**os.environ, 'MTL_DEBUG_LAYER': '1'})
        proof.append((' '.join(mode) or 'graph') + ': ' + (p.stdout+p.stderr).strip())
        proof_path.write_text('\n'.join(proof) + '\n')
        print((' '.join(mode) or 'graph') + ': ' + (p.stdout+p.stderr).strip()[-4000:], flush=True)
        if mode == ['--gpu'] and p.returncode == 3:
            raise SystemExit(3)
        assert p.returncode == (1 if mode == ['--wrong-order'] else 0), p.returncode
        assert re.search(r'failures=[1-9]\d*', p.stdout) if mode == ['--wrong-order'] else 'failures=0' in p.stdout
    # Break the empty dispatch itself. Recording objects count allocation,
    # encoder and binding calls without needing a Metal device or private data.
    module = (ROOT/'runtime/pre_treatment.m').read_text()
    broken = module.replace('if (!graph->count) return scene;', '''if (!graph->count) {
        [(id)scene newTextureWithDescriptor:(id)scene];
        [(id)scene renderCommandEncoderWithDescriptor:(id)scene];
        [(id)scene setFragmentTexture:nil atIndex:0];return scene;
    }''')
    assert module != broken
    source = Path(temporary)/'broken-off.m'
    source.write_text(broken)
    p = subprocess.run(['clang', '-O2', '-Wall', '-Wextra', '-Werror', '-fobjc-arc',
                        '-I', str(ROOT/'runtime'), '-I', str(ROOT/'recomp'),
                        str(ROOT/'runtime/pre_treatment_test.m'), str(source),
                        '-framework', 'Foundation', '-framework', 'Metal', '-o', str(exe)],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr[-4000:]
    p = subprocess.run([exe], capture_output=True, text=True)
    proof.append('broken-off: ' + (p.stdout+p.stderr).strip())
    proof_path.write_text('\n'.join(proof) + '\n')
    print(proof[-1], flush=True)
    assert p.returncode == 1 and re.search(r'failures=[1-9]\d*', p.stdout), (p.returncode,p.stdout,p.stderr)
