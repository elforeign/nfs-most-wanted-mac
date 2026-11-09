#!/usr/bin/env python3
"""











"""
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCRIPT = (ROOT / 'player_running.sh').read_text()
SUFFIX = '/qwfake/NFSMW.app/Contents/MacOS/NFSMW'


def make_game(d):
    g = Path(d) / 'qwfake/NFSMW.app/Contents/MacOS/NFSMW'
    g.parent.mkdir(parents=True)
    shutil.copy('/bin/sleep', g)
    subprocess.run(['codesign', '-f', '-s', '-', str(g)], capture_output=True)
    return g


def spawn(cmd, orphan):
    """Start cmd; orphan=True reparents it to launchd. Returns its pid."""
    if orphan:
        out = subprocess.run(['sh', '-c', cmd + ' >/dev/null 2>&1 & echo $!'], capture_output=True, text=True).stdout
        return int(out.split()[-1])
    return subprocess.Popen(['sh', '-c', 'exec ' + cmd]).pid


def reported(d, script_text):
    sp = Path(d) / 'port/runtime/player_running.sh'
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(script_text)
    env = dict(os.environ, NFS_RUNS=f'{d}/runs', PLAYER_RUNNING_GAME=SUFFIX)
    return subprocess.run(['sh', str(sp)], capture_output=True, text=True, env=env).returncode == 0


def stop(pid):
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def cases(script_text):
    bad = []
    with tempfile.TemporaryDirectory() as d:
        game = make_game(d)
        (Path(d) / 'runs/slots').mkdir(parents=True)
        pids = []
        try:
            # argv-only: a long sleep whose command line names the bundle path
            p = spawn("sh -c 'sleep 60; :' x/NFSMW.app/Contents/MacOS/NFSMW", True)
            pids.append(p); time.sleep(0.3)
            if reported(d, script_text):
                bad.append('argv-only')
            stop(p)
            # started from a terminal: a child of a shell, in no launcher slot
            p = spawn(f'{game} 60', False); pids.append(p); time.sleep(0.3)
            if not reported(d, script_text):
                bad.append('terminal game')
            (Path(d) / 'runs/slots' / '999998').write_text(f'run {p}\n')
            if reported(d, script_text):
                bad.append('launcher slot child')
            (Path(d) / 'runs/slots/999998').unlink()
            stop(p)
            # orphaned game
            p = spawn(f'{game} 60', True); pids.append(p); time.sleep(0.5)
            if not reported(d, script_text):
                bad.append('orphaned game')
            (Path(d) / 'runs/slots' / '999999').write_text(f'run {p}\n')
            if reported(d, script_text):
                bad.append('launcher slot')
            (Path(d) / 'runs/slots/999999').unlink()
            stop(p)
            time.sleep(0.3)
            if reported(d, script_text):
                bad.append('stopped game')
        finally:
            for p in pids:
                stop(p)
    return bad


good = cases(SCRIPT)
OLD = '#!/bin/sh\n[ -n "$(pgrep -fl NFSMW.app/Contents/MacOS 2>/dev/null)" ]\n'
obad = cases(OLD)
nofilter = SCRIPT.replace('case "$ours" in *" $p "*) continue;; esac', ':')
assert nofilter != SCRIPT
fbad = cases(nofilter)
# the rule before this change: the game's parent had to be launchd
PPID_LINE = '        path=$(ps -p "$p" -o comm= 2>/dev/null) || continue\n'
assert PPID_LINE in SCRIPT
launchd = SCRIPT.replace(PPID_LINE, '        [ "$(ps -p "$p" -o ppid= 2>/dev/null | tr -d " ")" = 1 ] || continue\n' + PPID_LINE)
lbad = cases(launchd)
print(f'player_running good script: {7 - len(good)}/7 cases' + (f' FAILED {good}' if good else ''))
print(f'player_running old-pattern copy: {len(obad)} failed cases {obad}')
print(f'player_running launchd-parent copy: {len(lbad)} failed cases {lbad}')
print(f'player_running filter-removed copy: {len(fbad)} failed cases {fbad}')
ok = (not good and 'argv-only' in obad and lbad == ['terminal game']
      and set(fbad) == {'launcher slot', 'launcher slot child'})
print('player_running_test: PASS' if ok else 'player_running_test: FAIL')
sys.exit(0 if ok else 1)
