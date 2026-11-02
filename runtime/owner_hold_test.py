#!/usr/bin/env python3
"""owner_hold.sh holds unattended checks while the player's game runs and for a grace period after it exits."""
# Proof (a ps shim serves the process list; poll 1 s, grace 3 s):
#   no game -> returns at once; a game started by a test (parent not launchd) -> returns at once;
#   player's game for 2 s -> waits >= 2 s + grace and logs "waits" and "resumes";
#   relaunch 1 s after exit (inside the grace) -> keeps holding until the second exit + grace.
#   Fixed: 0 failures. --broken (grace 0, the old start-only hold's behaviour) -> 1 failure (relaunch case).
                                                                                                                       
import os
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, 'owner_hold.sh')
GAME = '    1 /Applications/My Games/NFSMW.app/Contents/MacOS/NFSMW\n'
CHILD = ' 4242 /tmp/x/NFSMW.app/Contents/MacOS/NFSMW\n'
OTHER = '    1 /usr/libexec/somed\n  311 /bin/zsh\n'


def run(tmp, schedule, grace):
    """schedule: [(seconds from start, process list)]; returns (elapsed s, stderr)."""
    listing = os.path.join(tmp, 'ps.txt')
    with open(listing, 'w') as f:
        f.write(schedule[0][1])
    shim = os.path.join(tmp, 'bin')
    os.makedirs(shim, exist_ok=True)
    with open(os.path.join(shim, 'ps'), 'w') as f:
        f.write('#!/bin/sh\ncat "%s"\n' % listing)
    os.chmod(os.path.join(shim, 'ps'), 0o755)

    def feed():
        t0 = time.monotonic()
        for at, text in schedule[1:]:
            time.sleep(max(0, at - (time.monotonic() - t0)))
            with open(listing + '.new', 'w') as f:
                f.write(text)
            os.replace(listing + '.new', listing)
    feeder = threading.Thread(target=feed)
    env = dict(os.environ, PATH=shim + os.pathsep + os.environ['PATH'], OWNER_HOLD_POLL='1',
               OWNER_HOLD_GRACE=str(grace))
    env.pop('OWNER_HOLD', None)
    env.pop('OWNER_HOLD_GAME', None)
    t0 = time.monotonic()
    feeder.start()
    p = subprocess.run(['/bin/sh', SCRIPT, 'unit'], env=env, capture_output=True, text=True, timeout=60)
    elapsed = time.monotonic() - t0
    feeder.join()
    return elapsed, p.stderr


def main():
    grace = 0 if '--broken' in sys.argv else 3
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        e, err = run(tmp, [(0, OTHER)], grace)
        if e > 1.5 or err:
            failures.append('no game: %.1f s %r' % (e, err))
        e, err = run(tmp, [(0, OTHER + CHILD)], grace)
        if e > 1.5:
            failures.append('test-launched game held %.1f s' % e)
        e, err = run(tmp, [(0, OTHER + GAME), (2, OTHER)], grace)
        if e < 2 + grace - 0.5 or 'waits' not in err or 'resumes' not in err:
            failures.append('player game: %.1f s %r' % (e, err))
        # Exit at 2 s, relaunch at 3 s (inside the grace), exit again at 6 s: hold until >= 6 + 3.
        e, err = run(tmp, [(0, OTHER + GAME), (2, OTHER), (3, OTHER + GAME), (6, OTHER)], grace)
        if e < 6 + 3 - 0.5:
            failures.append('relaunch inside the grace: returned after %.1f s' % e)
    with open(os.path.join(HERE, 'offline_tests.sh')) as f:
        if 'sh runtime/owner_hold.sh "$n"' not in f.read():
            failures.append('offline_tests.sh does not hold before each test')
    for x in failures:
        print('FAIL:', x)
    print('owner_hold %s (%d failures)' % ('FAIL' if failures else 'PASS', len(failures)))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
