"""G1a rendered-frame lifecycle; actual temporal_state.c + shipping hook bodies.
ASan/UBSan: production 441 checks / 0 failures (exit 0);
disabled-invalidation build 441 checks / 77 failures (exit 1).
Includes same-camera cut, attach/destroy/restart, player replacement/reuse,
pause/menu, skipped/duplicate frames, first-draw freeze, split/mirror isolation,
actual target properties, invalid/discontinuous matrices, CRI reuse/overflow,
all constructor fast paths, and disabled hooks with an inaccessible guest base.
Discovered by offline_tests.sh; no game, GPU, network or private assets.
"""
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent


def body(path, name):
    text = (ROOT / path).read_text()
    return re.search(r'void ' + name + r'\(Cpu \*c\) \{.*?\n}', text, re.S)[0]


def main():
    hooks = (ROOT.parent / 'recomp/hooks.txt').read_text()
    for address in ('004700D0', '00470A10', '00502980', '005029B0', '006FCA30', '006FBF20', '006F8200', '007556C0'):
        assert re.search(r'^' + address + r'\s+temporal_state.c:', hooks, re.M), address
    for path, marker in [('diag.c', 'temporal_guest_frame(frame)'),
                         ('splitworld.c', 'temporal_guest_scene_mode(shadow_split())'),
                         ('build.sh', '    temporal_state.c \\')]:
        assert marker in (ROOT / path).read_text(), path
    with tempfile.TemporaryDirectory(prefix='temporal-state-') as td:
        directory = Path(td)
        (directory / 'temporal_hooks.inc').write_text(body('widescreen.c', 'f_006CF400') + '\n' + body('car85.c', 'f_0075F5D0') + '\n' + body('x360.m', 'f_006308E0'))
        for broken in (False, True):
            exe = directory / ('broken' if broken else 'state')
            command = ['clang', '-std=c11', '-Wall', '-Wextra', '-Werror', '-g', '-O1',
                       '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                       '-I', str(ROOT), '-I', str(ROOT.parent / 'recomp'), '-I', str(directory),
                       str(ROOT / 'temporal_state_test.c'), str(ROOT / 'temporal_state.c'), '-o', str(exe)]
            if broken:
                command.append('-DTEMPORAL_TEST_DISABLE_INVALIDATION')
            p = subprocess.run(command, capture_output=True, text=True)
            assert p.returncode == 0, p.stderr[-6000:]
            p = subprocess.run([str(exe)], capture_output=True, text=True)
            print(('disabled invalidation: ' if broken else 'production: ') + p.stdout.strip(), flush=True)
            assert p.returncode == (1 if broken else 0), p.stderr[-5000:]
            assert 'AddressSanitizer' not in p.stderr and 'runtime error:' not in p.stderr, p.stderr[-5000:]
            counts = re.search(r'(\d+) checks / (\d+) failures', p.stdout)
            assert counts and (int(counts[1]), int(counts[2])) == (441, 77 if broken else 0)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
