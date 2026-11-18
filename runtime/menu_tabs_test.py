#!/usr/bin/env python3
"""Actual menu tabs: sparse registry draws 4, full registry draws 10.

Good: 195 checks / 0 failures. Forced-true helper: sparse draws 10 against
expected 4, 195 checks / 28 failures, exit 1; good exit 0.
The renderer, keyboard/pad navigation, opening and both mouse paths run without
a game. Source dependencies: portmenu.m, settings.c, guide.m, guide_test.m.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / 'runtime'


def run(base, name, source):
    work = base / name
    work.mkdir()
    for filename in ('guide_test.m', 'menu_tabs_test.m'):
        shutil.copyfile(RUNTIME / filename, work / filename)
    (work / 'portmenu.m').write_text(source)
    binary = work / 'tabs'
    command = ['clang', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
               '-Wno-unused-function', '-fobjc-arc', '-I'+str(ROOT/'recomp'),
               '-I'+str(ROOT/'recomp/out'), '-I'+str(RUNTIME),
               str(work/'menu_tabs_test.m'), str(RUNTIME/'blackbox.c'), str(RUNTIME/'settings.c'),
               str(RUNTIME/'text_fit.c'), str(RUNTIME/'offline_stubs.c'),
               str(RUNTIME/'replay_input.c'), '-framework', 'Cocoa',
               '-framework', 'CoreText', '-framework', 'QuartzCore',
               '-framework', 'Metal', '-Wl,-dead_strip', '-o', str(binary)]
    built = subprocess.run(command, capture_output=True, text=True, timeout=90)
    assert built.returncode == 0, built.stderr[-4000:]
    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
    print(name + ': ' + result.stdout.strip())
    return result


def main():
    source = (RUNTIME / 'portmenu.m').read_text()
    good_helper = 'return rows(cat, r, 128) > 0;'
    assert source.count(good_helper) == 1
    with tempfile.TemporaryDirectory(prefix='menu-tabs-') as scratch:
        base = Path(scratch)
        good = run(base, 'good', source)
        assert good.returncode == 0, good.stderr[-4000:]
        broken = run(base, 'forced-true', source.replace(
            good_helper, '(void)cat; (void)r; return 1;'))
        assert broken.returncode == 1, broken.stderr[-4000:]
        assert 'sparse shown=10 expected=4' in broken.stdout, broken.stdout
    print('menu_tabs_test PASS: good=4 sparse/10 full; forced-true=10 sparse (rejected)')


if __name__ == '__main__':
    main()
