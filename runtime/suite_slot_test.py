#!/usr/bin/env python3
"""suite_slot.sh (kernel flock on fd 9) limits whole offline suites to OFFLINE_SUITE_SLOTS at once, frees the slot the
moment the holder and its children are gone (even SIGKILL), exempts nested suites, picks its directory by the
documented rule, and never hangs on an unusable directory."""
# Proof: PASS 16/16 (per-test launches close fd 9; FAILS when either `9>&-` is removed). With flock replaced by a
# command that always succeeds (--broken) 4 checks fail (12/16): 3 hold at t+2.5 s, no acquisition is logged,
# the orphan-child case admits a third suite, and the stress run reaches 6 at once.
# The earlier symlink/.lock version failed the review's release-between-ln-and-check, legacy-directory and
# orphaned-nested cases; this kernel-lock version has no such window.
# Solo/suite proof: 21/21; retro fixed gate / old other; held suite refuses solo (75), removed guard admits (0), ledger 1/1; quiet 0/0.
# Acquisition log proof: one waited holder emits one acquisition line; immediate holders emit none.
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

RT = Path(__file__).resolve().parent


def main():
    broken = '--broken' in sys.argv
    bad = []
    n = 0

    def check(ok, msg):
        nonlocal n
        n += 1
        if not ok:
            bad.append(msg)

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        slots = base / 'slots'
        env = dict(os.environ, SUITE_SLOT_DIR=str(slots), OFFLINE_SUITE_SLOTS='2',
                   TOOLS_PY='/usr/bin/true' if broken else sys.executable)
        env.pop('OFFLINE_SUITE_SLOT_HELD', None)

        def holder(name, hold=5, e=None, log=None):
            mark = f'echo + >> {log}; ' if log else ''
            unmark = f'echo - >> {log}; ' if log else ''
            code = (f'. {RT}/suite_slot.sh; suite_slot_acquire; {mark}echo held > {base}/{name}; sleep {hold}; {unmark}')
            return subprocess.Popen(['sh', '-c', code], env=e or env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, start_new_session=True)

        def killgroup(p):
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:
                pass
            p.wait()

        procs = [holder(k) for k in 'abc']
        time.sleep(2.5)
        held = sorted(k for k in 'abc' if (base / k).exists())
        check(len(held) == 2, f'holding at t+2.5 s: {held}')
        logs = [p.communicate(timeout=30)[1] for p in procs]
        check(all((base / k).exists() for k in 'abc'), 'a waiter never got a slot')
        check(sum(err.count('offline_tests: whole-suite slot acquired after ') for err in logs) == 1,
              f'expected one acquisition message for the waited holder: {logs!r}')
        # SIGKILL of the whole holder (sh and its children): the slot is free at once
        a, b = holder('k1', 30), holder('k2', 30)
        time.sleep(2)
        killgroup(a)
        d = holder('k3', 1)
        time.sleep(3)
        check((base / 'k3').exists(), 'killed holder kept its slot')
        killgroup(b); d.wait()
        # an orphaned child (the suite parent died, a nested suite still runs) keeps the slot
        a, b = holder('o1', 30), holder('o2', 30)
        time.sleep(2)
        a.kill(); a.wait()          # only the shell: its sleep child still holds fd 9
        d = holder('o3', 1)
        time.sleep(3)
        check(not (base / 'o3').exists(), 'slot freed while a child of the holder still ran')
        killgroup(a); killgroup(b); killgroup(d)
        # nesting and the exported marker
        p = holder('n1', 30); p2 = holder('n2', 30); time.sleep(2)
        p3 = holder('nested', 1, e=dict(env, OFFLINE_SUITE_SLOT_HELD='1'))
        time.sleep(1.5)
        check((base / 'nested').exists(), 'nested suite waited for a slot')
        killgroup(p); killgroup(p2); killgroup(p3)
        out = subprocess.run(['sh', '-c', f'. {RT}/suite_slot.sh; suite_slot_acquire; echo "m=$OFFLINE_SUITE_SLOT_HELD"'],
                             env=env, capture_output=True, text=True).stdout
        check('m=1' in out, f'marker not exported: {out!r}')
        # stress: 6 short holders, 2 slots
        log = base / 'conc.log'
        ps = [holder(f's{i}', 1, log=log) for i in range(6)]
        for q in ps:
            q.wait(timeout=90)
        cur = peak = 0
        for c in log.read_text().split():
            cur += 1 if c == '+' else -1
            peak = max(peak, cur)
        check(peak <= 2, f'stress peak {peak}: {log.read_text().split()}')
        check(len(log.read_text().split()) == 12, 'stress: a holder never ran')
        # unusable directories: one line, no hang
        blocker = base / 'afile'; blocker.write_text('x')
        t0 = time.time()
        p = holder('nodir', 0, e=dict(env, SUITE_SLOT_DIR=str(blocker / 'sub')))
        out, err = p.communicate(timeout=20)
        check((base / 'nodir').exists() and 'without a whole-suite slot' in err and time.time() - t0 < 10,
              f'file-as-parent directory: {err!r}')
        ro = base / 'ro'; ro.mkdir(); ro.chmod(0o555)
        p = holder('rodir', 0, e=dict(env, SUITE_SLOT_DIR=str(ro)))
        out, err = p.communicate(timeout=20)
        ro.chmod(0o755)
        check((base / 'rodir').exists() and 'without a whole-suite slot' in err, f'read-only directory: {err!r}')
        # a flock error (here: python missing) runs without a slot, once
        p = holder('noflock', 0, e=dict(env, TOOLS_PY=str(base / 'nope')))
        out, err = p.communicate(timeout=20)
        check((base / 'noflock').exists() and 'flock unavailable' in err, f'flock error: {err!r}')
        # directory rule: an existing writable NFS_RUNS wins, else TMPDIR/nfsmw-suite-slots
        e2 = {k: v for k, v in env.items() if k != 'SUITE_SLOT_DIR'}
        runs = base / 'runs'; runs.mkdir()
        tmpd = base / 'tmpdir'; tmpd.mkdir()
        probe = f'. {RT}/suite_slot.sh; echo "$suite_slot_dir"'
        got = subprocess.run(['sh', '-c', probe], env=dict(e2, NFS_RUNS=str(runs), TMPDIR=str(tmpd)),
                             capture_output=True, text=True).stdout.strip()
        check(got == f'{runs}/suite_slots', f'dir with existing NFS_RUNS: {got}')
        got = subprocess.run(['sh', '-c', probe], env=dict(e2, NFS_RUNS=str(base / 'absent'), TMPDIR=str(tmpd)),
                             capture_output=True, text=True).stdout.strip()
        check(got == f'{tmpd}/nfsmw-suite-slots', f'dir with absent NFS_RUNS: {got}')
        src = (RT / 'offline_tests.sh').read_text()
        check('[ $# -eq 0 ]' in src and 'suite_slot_acquire' in src, 'offline_tests.sh does not take a slot for whole runs')
        # Review 3: a test that leaves a child running (test_apply's signal.pause child) pinned the slot forever; each test
        # and its watchdog start with fd 9 closed. Pin: removing either `9>&-` fails this check.
        check(src.count('9>&-') >= 2 and 'run_one "$n" "$OT_DIR/work/$n" ) 8>&- 9>&-' in src, 'tests or their watchdog inherit the slot fd')
        # Solo/suite exclusion: held suite -> 75, old admission -> 0 with ledger 1/1.
        e = dict(env, NFS_RUNS=str(base/'runs'), NFS_SLOT='solo', NFS_SLOT_WAIT='0', TOOLS_PY=sys.executable)
        h = subprocess.Popen(['sh', '-c', f'. {RT}/suite_slot.sh; suite_slot_acquire; echo held; read release'],
                             env=e, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        assert h.stdout.readline().strip() == 'held'
        original = (RT/'game_slot.sh').read_text()
        mutant = base/'game_slot.sh'; mutant.write_text(original.replace('[ "$slot_suites" = 0 ] && suite_slot_guard exclusive', 'true'))
        for script, want in ((RT/'game_slot.sh', 75), (mutant, 0)):
            p = subprocess.run(['sh', '-c', f'. {script}; slot_games() {{ :; }}; slot_note=gate.fixture; slot_acquire fixture; slot_release 0'],
                               cwd=RT.parent, env=e, capture_output=True, text=True, timeout=20)
            check(p.returncode == want, f'solo beside suite: {script.name} rc={p.returncode}, expected {want}: {p.stderr}')
        check((base/'runs/ledger.tsv').read_text().splitlines()[-1].split('\t')[-2:] == ['1','1'], 'suite start/end counts absent')
        kind=next(line.split(' = ',1)[1] for line in (RT/'retro.py').read_text().splitlines() if line.lstrip().startswith('k = ')); row=(base/'runs/ledger.tsv').read_text().splitlines()[-1].split('\t')
        check(eval(kind,dict(r=row)) == 'gate' and eval(kind.replace('r[10]','r[-1]'),dict(r=row)) == 'other', 'retro loses diagnostics with suite columns')
        h.communicate('release\n', timeout=10)
        p = subprocess.run(['sh', '-c', f'. {RT}/game_slot.sh; slot_games() {{ :; }}; slot_acquire fixture; sh -c "exec 8>&-; . {RT}/suite_slot.sh; suite_slot_guard shared" && exit 3; slot_release 0'],
                           cwd=RT.parent, env=e, capture_output=True, text=True, timeout=20)
        check(p.returncode == 0 and (base/'runs/ledger.tsv').read_text().splitlines()[-1].split('\t')[-2:] == ['0','0'], 'solo admission or reverse exclusion failed')
    print(f'suite_slot_test: FAIL {n - len(bad)}/{n}: ' + '; '.join(bad) if bad else f'suite_slot_test: PASS {n}/{n}')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
