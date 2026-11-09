#!/usr/bin/env python3
"""Offline log-checker proof: 8 row changes, 2 queued resets / 4 successful Resets.
Normal: PASS (0 findings); broken reset requests: FAIL (2 findings).
Runs only the checker's embedded Python against synthetic scratch logs; no launch.
"""
from pathlib import Path
import subprocess
import sys
import tempfile

RT = Path(__file__).resolve().parent


def main():
    script = (RT / 'live_picture_check.sh').read_text()
    checker = script.split("<<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
    events = ['IDirect3D9::CreateDevice -> 0']
    for name, state in (
        ('graphics.look_contact_shadows=off', '0 soft smoke switch=unset'),
        ('graphics.look_contact_shadows=on', '1 soft smoke switch=unset'),
        ('graphics.look_soft_smoke=off', '1 soft smoke switch=off'),
        ('graphics.look_soft_smoke=on', '1 soft smoke switch=unset'),
    ):
        events += ['live picture: contact shadows=' + state, 'script: set:' + name + ' -> 1']
    for value, edge, levels in (('pc', 256, 1), ('sharp', 1024, 11)):
        events += ['script: set:graphics.look_reflections=' + value + ' -> 1',
                   'live picture: reset requested (setting)',
                   'IDirect3DDevice9::Reset -> 0', 'IDirect3DDevice9::Reset -> 0',
                   f'envcube: create usage=1 edge={edge} levels={levels}']
    events += ['script: set:display.fullscreen=native -> 1', 'script: set:display.fullscreen=borderless -> 1']
    with tempfile.TemporaryDirectory(prefix='live-picture-check-') as folder:
        out = Path(folder) / 'logs'
        out.mkdir()
        for n in range(2): (out / f'frame_{n}.png').touch()
        Path(str(out) + '.out').write_text('fullscreen: mode=Native\nfullscreen: mode=Borderless\n')
        for broken in (False, True):
            lines = [e for e in events if not broken or ('reset requested' not in e and '::Reset' not in e)]
            (out / 'events.log').write_text('\n'.join(lines))
            run = subprocess.run([sys.executable, '-I', '-c', checker, str(out)], capture_output=True, text=True)
            assert run.returncode == int(broken), run.stdout + run.stderr
            assert ('FAIL:' if broken else 'PASS') in run.stdout, run.stdout
            if broken:
                assert run.stdout.count('want') == 2, run.stdout
                # Two count assertions reject missing resets; the reflection assertions stay live.
            print(f'broken={int(broken)}: {run.stdout.strip()}')
    print('live_picture_check_test: PASS; missing-reset control rejected')


if __name__ == '__main__':
    main()
