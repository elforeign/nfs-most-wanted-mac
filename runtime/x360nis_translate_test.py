#!/usr/bin/env python3
"""Offline proof for audit2 #3 (NIS stream switch early): production nis_translate on a synthetic map shaped like retail
(stream 0 is 100 bytes longer in the X360 BIG than its PC extent). Sequential reads of stream 0 across the next PC stream's
start must stay on stream 0; a real jump to that start begins stream 1.
Result: PASS (fixed). `--old` (x360nisaudio.c from git HEAD~ before the fix): FAIL (1), the read at PC 1000 switches to stream 1.
Run: tools/.venv/bin/python -I runtime/x360nis_translate_test.py [--old]"""
from pathlib import Path
import shutil, subprocess, sys, tempfile
ROOT = Path(__file__).resolve().parents[1]
old = '--old' in sys.argv
with tempfile.TemporaryDirectory(prefix='nfsmw-nistr-') as d:
    d = Path(d)
    shutil.copy(ROOT / 'runtime/x360nis_translate_test.c', d)
    if old:
        src = subprocess.run(['git', '-C', str(ROOT), 'show', 'HEAD~1:runtime/x360nisaudio.c'], capture_output=True, text=True, check=True).stdout
        (d / 'x360nisaudio.c').write_text(src)
    else:
        shutil.copy(ROOT / 'runtime/x360nisaudio.c', d)
    r = subprocess.run(['clang', '-std=c11', '-D_DARWIN_C_SOURCE', '-O1', '-g', '-w', '-fsanitize=address,undefined'] + (['-DOLD_SIG'] if old else []) +
        ['-I', str(d), '-I', str(ROOT / 'runtime'), '-I', str(ROOT / 'recomp'), '-I', str(ROOT / 'recomp/out'),
         str(d / 'x360nis_translate_test.c'), str(ROOT / 'runtime/x360copspeech.c'), str(ROOT / 'runtime/settings.c'), str(ROOT / 'runtime/offline_stubs.c'), '-lz', '-o', str(d / 't')],
        capture_output=True, text=True)
    if r.returncode: print(r.stderr[-3000:]); sys.exit(2)
    r = subprocess.run([str(d / 't')], capture_output=True, text=True)
    print(r.stdout, r.stderr[-1500:], end='')
    sys.exit(r.returncode)
