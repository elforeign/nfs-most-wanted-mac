#!/usr/bin/env python3
"""Offline test of the tunnel reverb hold (runtime/tunnelhold.h). Run: $TOOLS_PY -I runtime/tunnelhold_test.py
Hold 0.3 s must pass; hold 0 (retail) must fail (the broken run); the owner-blind hold (another controller ending or
taking the player's hold) fails the other-controller scenario."""
from pathlib import Path
import subprocess, sys, tempfile
R = Path(__file__).resolve().parent
with tempfile.TemporaryDirectory() as d:
    exe = Path(d) / 't'
    subprocess.run(['cc', '-O1', '-Wall', '-Werror', '-o', exe, R / 'tunnelhold_test.c'], check=True)
    ok = subprocess.run([exe, '0.3'], capture_output=True, text=True)
    bad = subprocess.run([exe, '0'], capture_output=True, text=True)
    print(ok.stdout.strip()); print(bad.stdout.strip())
    if ok.returncode != 0 or bad.returncode == 0: sys.exit('tunnelhold_test: FAIL')
print('tunnelhold_test: PASS (hold passes, retail broken run fails)')
