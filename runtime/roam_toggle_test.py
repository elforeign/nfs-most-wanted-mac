"""Ghost-mode toggle input (roam_core.c roam_toggle_press / roam_toggle_allowed, runtime/roam_toggle_test.c).

normal: must print TOGGLE PASS (exit 0).
broken: a generated copy whose gate ignores the menu flag (roam_toggle_allowed without `&& !menu_open`) must fail the
        gate checks; a second copy without the edge detection (fires every frame held) must fail the edge check.
Recorded 2026-10-29: normal 0 failures; menu-ungated copy 2 FAIL lines (F10 menu refused, press in a menu fired);
no-edge copy 3 FAIL lines (a 6-frame hold fired 6 times, the second press, the hold fired on leaving a menu).
"""
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent
RT = ROOT / 'runtime'
GATE = 'return welcomed && has_car && driving && !menu_open;'
EDGE = 'int edge = down && !t->held;'
CF = ['clang', '-std=c11', '-Wall', '-Wextra', '-Werror', '-g', '-fsanitize=address,undefined', '-I', str(RT)]


def run(td, name, core):
    exe = Path(td) / ('toggle_' + name)
    p = subprocess.run(CF + [str(RT / 'roam_toggle_test.c'), str(core), str(RT / 'roam_relay.c'), '-o', str(exe)],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr[-2500:]
    p = subprocess.run([str(exe)], capture_output=True, text=True, timeout=60)
    nfail = p.stdout.count('FAIL: ')
    print(name + ':', p.stdout.strip().splitlines()[-1], '(%d FAIL lines)' % nfail, flush=True)
    return p.returncode, p.stdout


def main():
    src = (RT / 'roam_core.c').read_text()
    assert src.count(GATE) == 1 and src.count(EDGE) == 1, 'broken-control anchor moved'
    with tempfile.TemporaryDirectory(prefix='roamtoggle-') as td:
        rc, out = run(td, 'normal', RT / 'roam_core.c')
        assert rc == 0 and 'TOGGLE PASS' in out, out[-2000:]
        g = Path(td) / 'core_gate.c'
        g.write_text(src.replace(GATE, 'return welcomed && has_car && driving && (menu_open >= 0);'))
        rc, out = run(td, 'gate', g)
        assert rc == 1 and 'FAIL: gate' in out, ('gate control did not fail', rc, out)
        e = Path(td) / 'core_edge.c'
        e.write_text(src.replace(EDGE, 'int edge = down;'))
        rc, out = run(td, 'edge', e)
        assert rc == 1 and 'FAIL: edge' in out, ('edge control did not fail', rc, out)
    print('ROAM TOGGLE TEST PASS: normal passes, ungated menu fails, edgeless press fails')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
