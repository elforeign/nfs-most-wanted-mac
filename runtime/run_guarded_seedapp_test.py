#!/usr/bin/env python3
"""No game: BOOT_SEED_APP=<file> puts that file in the scratch profile as app.txt and is not passed to the game.

Proof (fake game, production launcher): ORIGINAL (seed block removed) 2 failures (no app.txt, setting leaks into the game env);
fixed 0 failures; a missing file exits 2 before any game starts.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_guarded_parked_test as base

SEED = 'controls.pads = 2\n'


def run(root, original):
    base.setup(root)
    if original:
        sh = root / 'runtime/run_guarded.sh'
        text = sh.read_text()
        b = text.index('        BOOT_SEED_APP=*)')
        e = text.index('continue ;;', b) + len('continue ;;\n')
        text = text[:b] + text[e:]
        b = text.index('if [ -n "$seed_app" ]; then')
        e = text.index('fi\n', b) + 3
        sh.write_text(text[:b] + text[e:])
    seed = root / 'seed_app.txt'
    seed.write_text(SEED)
    rc, _, values, capture, log = base.launch(root, 'seeded', args=['BOOT_SEED_APP=' + str(seed)])
    app = Path(str(root / 'seeded') + '.user/app.txt')
    failures = (not app.exists() or app.read_text() != SEED) + ('BOOT_SEED_APP' in values)
    return failures, rc, log


def missing(root):
    base.setup(root)
    import subprocess, os
    env = {k: v for k, v in os.environ.items() if not k.startswith(('BOOT_', 'NFS_'))}
    p = subprocess.run(['sh', str(root / 'runtime/run_guarded.sh'), '5', str(root / 'm'), 'BOOT_SEED_APP=' + str(root / 'nope')],
                       env=env, capture_output=True, text=True, timeout=20)
    assert p.returncode == 2 and not (root / 'm').exists() and 'missing' in p.stderr, (p.returncode, p.stderr)


def main():
    with tempfile.TemporaryDirectory(prefix='guarded-seedapp-') as t:
        root = Path(t)
        old, _, _ = run(root / 'old', True)
        new, rc, log = run(root / 'new', False)
        assert old == 2 and new == 0 and rc == 0, (old, new, rc, log)
        missing(root / 'miss')
    print(f'ORIGINAL: {old}/2 failures; fixed: {new}/2; missing file exit 2\nrun_guarded_seedapp_test PASS')


if __name__ == '__main__':
    main()
