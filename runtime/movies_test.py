#!/usr/bin/env python3
"""Run the production movie feeder with a simulated DirectSound write cursor."""
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    if not (ROOT / 'recomp/out/funcs.h').is_file():
        print('SKIP: recompiled guest function declarations absent')
        return 3
    with tempfile.TemporaryDirectory(prefix='movie-feeder-') as scratch:
        binary = Path(scratch) / 'movies_test'
        subprocess.run(['clang', '-O1', '-Wl,-dead_strip', '-I', 'recomp', '-I', 'recomp/out',
                        'runtime/movies_test.c', '-o', str(binary)], cwd=ROOT, check=True)
        return subprocess.run([str(binary)]).returncode


if __name__ == '__main__':
    raise SystemExit(main())
