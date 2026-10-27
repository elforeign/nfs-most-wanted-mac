#!/usr/bin/env python3
"""Offline test of the controller change during a Control Type 5 frame (runtime/padswap_test.c).
Run: $TOOLS_PY -I runtime/padswap_test.py   (padbind.c + controltype.c on stub memory)
Result: PASS with the padbind_follow / padbind_swap guard; before it (main at 3d268f5) FAIL, 2 failures: the Type 5
preset saved as the Xbox pad's gas binding, and the old slots restored over the DualSense layout."""
from pathlib import Path
import subprocess, sys, tempfile
R = Path(__file__).resolve().parent
with tempfile.TemporaryDirectory() as d:
    exe = Path(d) / 't'
    (Path(d) / 'funcs.h').write_text('')   # the recompiler's generated header: not needed by the stubs
    subprocess.run(['cc', '-O1', '-Wall', '-Werror', '-I', d, '-I', R, '-I', R.parent / 'recomp', '-o', exe, R / 'padswap_test.c'], check=True)
    r = subprocess.run([exe], capture_output=True, text=True)
    print(r.stdout.strip())
    if r.returncode != 0: sys.exit('padswap_test: FAIL')
print('padswap_test: PASS')
