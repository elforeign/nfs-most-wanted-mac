"""History-free native proof: original driver 1 failure (exit 3), fixed 0.

Run the real CPU/GPU driver with Git unavailable, as in a public checkout.
The GPU run must reach the original regression and all five broken controls.
"""
from pathlib import Path
import os
import runpy
import subprocess
from unittest.mock import patch


driver = Path(__file__).with_name('rt_native_test.py')
real_run = subprocess.run
attempts = []


def without_git(args, *positional, **kwargs):
    if Path(args[0]).name == 'git':
        attempts.append(args)
        raise subprocess.CalledProcessError(128, args, stderr=b'no repository history')
    return real_run(args, *positional, **kwargs)


with patch.object(subprocess, 'run', without_git):
    try:
        result = runpy.run_path(str(driver), run_name='__main__')
    except SystemExit as error:
        raise AssertionError(f'history-free driver exited {error.code}') from error
assert not attempts, f'proof requested Git history: {attempts}'
if os.environ.get('CODEX_GPU_CHECK') == '1' or os.environ.get('NFS_GPU_SANDBOX') == '1':
    assert len(result['REPORTS']) == 12, 'GPU regression or mutation proofs did not finish'
print('PASS history-free native driver: 0 Git calls, all enabled proofs completed')
