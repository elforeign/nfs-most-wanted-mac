#!/usr/bin/env python3
"""No game: production launcher, fixture diag logs, fake process and isolated slots.

Parked proof (moving 800 m / parked 3 m / missing distance, 40 s in flow 6):
ORIGINAL 2/3 failures (no parked reports, exit 0); fixed 0/3 (parked exit 125).
The pinned original deadline block below reproduces the missing watch without git history.
Also checks 39 s, 100 m, race resets, last distance, explicit opt-out, Carbon
selection and race-only defaults. Short fake-process deadlines: run serially.
"""
import hashlib
import os
from pathlib import Path
import signal
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL_GUARD = '''# A stoppable guard: terminating just a shell running foreground sleep would leave that sleep orphaned.
(
    timer=
    trap '[ -z "$timer" ] || { kill "$timer" 2>/dev/null; wait "$timer" 2>/dev/null; }; exit 0' TERM INT HUP
    sleep "$secs" & timer=$!; wait "$timer"; timer=
    kill -TERM "$pid" 2>/dev/null
    sleep 5 & timer=$!; wait "$timer"; timer=
    kill -KILL "$pid" 2>/dev/null
) &
guard=$!'''
assert hashlib.sha256(ORIGINAL_GUARD.encode()).hexdigest() == 'de9091eb958cfe0cc95700ad53f34bfcd5a80c0801332d3bc23926d61a2da1b9'


def executable(path, text):
    path.write_text('#!/bin/sh\n' + text)
    path.chmod(0o700)


def setup(root, original=False):
    rt = root / 'runtime'
    rt.mkdir(parents=True)
    source = (ROOT / 'runtime/run_guarded.sh').read_text()
    if original:
        begin = source.index('# BEGIN Carbon driving policy')
        end = source.index('# END Carbon driving policy') + len('# END Carbon driving policy')
        source = source[:begin] + source[end:]
        begin = source.index('# A stoppable guard:')
        end = source.index('guard=$!', begin) + len('guard=$!')
        source = source[:begin] + ORIGINAL_GUARD + source[end:]
        source = source.replace('if [ -f "$diag/parked_1.txt" ]; then rc=125; fi\n', '')
        source = source.replace('[ "$rc" != 125 ] || exit 125\n', '')
    (rt / 'run_guarded.sh').write_text(source)
    (rt / 'run_guarded_diag.sh').write_text((ROOT / 'runtime/run_guarded_diag.sh').read_text())
    executable(rt / 'player_running.sh', 'exit 1\n')
    (rt / 'game_slot.sh').write_text('''slot_acquire() { :; }
slot_started() { :; }
slot_release() { [ -z "$1" ] || echo "$1" > "$CAPTURE.rc"; }
''')
    executable(root / 'fake_carbon', '''env > "$CAPTURE"
cp "$FIXTURE/stats" "$BOOT_DIAG/stats.log"
cp "$FIXTURE/events" "$BOOT_DIAG/events.log"
timer=
trap '[ -z "$timer" ] || { kill "$timer" 2>/dev/null; wait "$timer" 2>/dev/null; }; echo TERM > "$CAPTURE.term"; exit 143' TERM
sleep 2 & timer=$!; wait "$timer"
exit 0
''')


def launch(root, name, stats='', events='', args=(), inherited=None, carbon=True):
    fixture = root / (name + '-fixture')
    fixture.mkdir()
    (fixture / 'stats').write_text(stats)
    (fixture / 'events').write_text(events)
    diag = root / name
    capture = root / (name + '.env')
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('BOOT_', 'NFS_', 'RUN_GUARDED_'))}
    binary = root / ('fake_carbon' if carbon else 'fake_mw')
    if not carbon:
        binary.write_text((root / 'fake_carbon').read_text())
        binary.chmod(0o700)
    env.update(BOOT_BIN=str(binary), NFS_GAME='fixture-input',
               FIXTURE=str(fixture), CAPTURE=str(capture))
    env.update(inherited or {})
    with tempfile.TemporaryFile('w+') as output:
        process = subprocess.Popen(['sh', str(root / 'runtime/run_guarded.sh'), '15', str(diag), *args],
                                   env=env, stdout=output, stderr=output, start_new_session=True)
        try:
            process.wait(timeout=12)
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        output.seek(0)
        log = output.read()
    values = dict(line.split('=', 1) for line in capture.read_text().splitlines())
    marker = diag / 'parked_1.txt'
    return process.returncode, marker.read_text() if marker.exists() else '', values, capture, log


def stats(*samples):
    return ''.join(f't {t:.1f} frame 60 fps 60.0 car 0.0 flow {flow} vinyls 0\n' for t, flow in samples)


def distance(m):
    return f't 900 autodrive: speed=1.000_mps mph=2.24 distance={m:.3f}_m top_mph=2.24\n'


def proof(root, original=False):
    failures = 0
    for name, events, parked in [('moving', distance(800), False),
                                  ('parked', distance(3), True), ('missing', '', True)]:
        rc, marker, values, capture, log = launch(root, name, stats((100, 6), (140, 6)), events,
                                                 args=['BOOT_RACE=1'])
        good = rc == (125 if parked else 0) and bool(marker) == parked
        if not original:
            assert values['BOOT_AUTODRIVE'] == '1'
            if parked:
                assert marker == f'flow 6\nseconds_in_race 40.0\nlast_distance_m {"3.000" if name == "parked" else "missing"}\n'
                assert capture.with_suffix('.env.term').read_text().strip() == 'TERM'
                assert Path(str(capture) + '.rc').read_text().strip() == '125'
        failures += not good
        if not original:
            assert good, (name, rc, marker, log)
    assert failures == (2 if original else 0), failures
    print(f'{"ORIGINAL" if original else "fixed"}: {failures}/3 failures', flush=True)


def boundaries(root):
    for name, samples, events, parked in [
        ('early', [(100, 6), (139, 6)], distance(3), False),
        ('threshold', [(100, 6), (140, 6)], distance(100), False),
        ('reset', [(100, 6), (139, 3), (140, 6)], distance(3), False),
        ('menu', [(100, 3), (200, 3)], '', False),
        ('last-moving', [(100, 6), (140, 6)], distance(3) + distance(800), False),
        ('last-parked', [(100, 6), (140, 6)], distance(800) + distance(3), True),
    ]:
        rc, marker, _, _, log = launch(root, name, stats(*samples), events, args=['BOOT_RACE=1'])
        assert (rc, bool(marker)) == (125 if parked else 0, parked), (name, log)
    print('boundaries: 6/6 PASS', flush=True)


def defaults(root):
    # Last argument wins; inherited values (including empty) count as explicit settings.
    def check(name, args=(), inherited=None, carbon=True, expected=None):
        rc, marker, values, _, log = launch(root, 'default-' + name, args=args,
                                           inherited=inherited, carbon=carbon)
        assert rc == 0 and not marker and values.get('BOOT_AUTODRIVE') == expected, (name, values.get('BOOT_AUTODRIVE'), log)

    check('menu')
    check('race', ['BOOT_RACE=1'], expected='1')
    check('skipfe', ['NFS_CARBON_SKIPFE=1'], expected='1')
    check('keyfile', ['BOOT_KEYFILE=route.keys'], expected='1')
    check('inherited-keyfile', inherited={'BOOT_KEYFILE': 'route.keys'}, expected='1')
    check('zero', ['BOOT_RACE=1', 'BOOT_AUTODRIVE=0'], expected='0')
    check('inherited-zero', ['BOOT_RACE=1'], {'BOOT_AUTODRIVE': '0'}, expected='0')
    check('override-zero', ['BOOT_RACE=1', 'BOOT_AUTODRIVE=1'], {'BOOT_AUTODRIVE': '0'}, expected='1')
    check('override-one', ['BOOT_RACE=1', 'BOOT_AUTODRIVE=0'], {'BOOT_AUTODRIVE': '1'}, expected='0')
    check('empty', ['BOOT_RACE=1'], {'BOOT_AUTODRIVE': ''}, expected='')
    check('other-game', ['BOOT_RACE=1'], carbon=False)
    check('game-path', ['BOOT_RACE=1'], {'NFS_GAME': 'fixture/Carbon/pc'}, carbon=False, expected='1')
    # Only explicit zero disables the watch, including on route-based runs.
    for i, (args, inherited) in enumerate([
        (['BOOT_KEYFILE=route.keys', 'BOOT_AUTODRIVE=0'], {}),
        (['BOOT_KEYFILE=route.keys'], {'BOOT_AUTODRIVE': '0'}),
    ]):
        rc, marker, _, _, log = launch(root, f'optout-{i}', stats((100, 6), (140, 6)), distance(3),
                                       args, inherited)
        assert rc == 0 and not marker, log
    print('defaults/opt-out: 14/14 PASS', flush=True)


def main():
    with tempfile.TemporaryDirectory(prefix='guarded-parked-') as temp:
        root = Path(temp)
        setup(root / 'old', original=True)
        proof(root / 'old', original=True)
        setup(root / 'fixed')
        proof(root / 'fixed')
        boundaries(root / 'fixed')
        defaults(root / 'fixed')
    print('run_guarded_parked_test PASS', flush=True)


if __name__ == '__main__':
    main()
