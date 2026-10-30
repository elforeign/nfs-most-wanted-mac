#!/usr/bin/env python3
"""offline_tests.sh leaves no watchdog sleep behind after a test finishes (an orphan holds the caller's pipe open)."""
# Proof: fixed runner, 1 test -> 0 leftover sleeps (PASS); --broken runs the old watchdog line -> the pipe is held past
# 60 s and a sleep is left (FAIL, exit 1; measured: 1 sleep + held pipe = 2).
# Leftovers are found by process group, not ps: the runner gets its own session, and anything still alive in that group
                                                                                                            
import os
import signal
import random
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OLD = '''    ( sleep "$LIMIT"; kill "$pid" 2>/dev/null; echo "offline_tests: killed after ${LIMIT}s" >>"$log" ) &\n'''


def alive(group):
    try:
        os.killpg(group, 0)
        return 1
    except ProcessLookupError:
        return 0
    except PermissionError:
        return 1


def main():
    runner = os.path.join(ROOT, 'runtime', 'offline_tests.sh')
    temp = None
    if '--broken' in sys.argv:
        text = open(runner).read()
        start = text.index('    # The watchdog\'s own sleep'); end = text.index('2>&1 &\n', start) + len('2>&1 &\n')
        temp = os.path.join(ROOT, 'runtime', f'.offline_tests_broken_{os.getpid()}.sh')
        open(temp, 'w').write(text[:start] + OLD + text[end:])
        runner = temp
    limit = random.randint(5000, 9000)
    try:
        held = False
        proc = subprocess.Popen(['sh', runner, 'effort_cap_test'], cwd=ROOT, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, start_new_session=True,
                                env=dict(os.environ, OFFLINE_TEST_LIMIT=str(limit)))
        group = proc.pid
        try:   # a leftover sleep keeps this pipe open: the run then never returns within its own time
            proc.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            held = True
        time.sleep(1)
        n = alive(group) + held
    finally:
        if temp:
            os.unlink(temp)
        try:   # stop only what this test started: its own process group
            os.killpg(group, signal.SIGKILL)
        except (ProcessLookupError, NameError):
            pass
    print(f'FAIL watchdog leak: {n} sleep(s) left' if n else 'PASS watchdog leak (0 sleeps left)')
    return 1 if n else 0


if __name__ == '__main__':
    sys.exit(main())
