#!/usr/bin/env python3
"""Real bitmap equivalence and raster counts. Old refresh/full-keyboard controls fail.

The optional --reference FILE compares every emitted bitmap digest with an earlier
portmenu.m. Normal runs compare incremental/cached pixels with clean full rasters.
Proof (checks/failures): good 8361/0, original 4176/0; 3828 identical pixel states.
Broken controls: old-refresh 9/3, old-keyboard 9/3, hidden-guide 9/1,
hidden-row 9/4, no-restore 120/1. Unchanged and Guide idle: 0 full / 10 seconds
(old: 10); keyboard: 0 full + 100 partial / 100 moves (old: 100 full).
"""
from pathlib import Path
import os
import plistlib
import shutil
import subprocess
import tempfile
import sys

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / 'runtime'


def run(base, name, source, original=False, budgets=False):
    work = base / name
    work.mkdir()
    for filename in ('guide.m', 'guide.h', 'guide_test.m', 'menu_raster_test.m'):
        shutil.copyfile(RUNTIME / filename, work / filename)
    (work / 'portmenu.m').write_text(source)
    binary = work / 'Raster.app/Contents/MacOS/Raster'
    binary.parent.mkdir(parents=True)
    resources = work / 'Raster.app/Contents/Resources'
    resources.mkdir()
    for src, dst in [('guide.md', 'Guide.md'), ('testing.md', 'Testing.md')]:
        path = RUNTIME / 'docs' / src
        shutil.copyfile(path if path.exists() else RUNTIME / 'docs/guide.md', resources / dst)
    with (work / 'Raster.app/Contents/Info.plist').open('wb') as f:
        plistlib.dump({'CFBundleExecutable': 'Raster', 'CFBundleIdentifier': 'test.raster',
                      'CFBundlePackageType': 'APPL'}, f)
    command = ['clang', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
               '-fobjc-arc', '-I'+str(ROOT/'recomp'), '-I'+str(ROOT/'recomp/out'), '-I'+str(RUNTIME),
               str(work/'menu_raster_test.m'), str(RUNTIME/'settings.c'), str(RUNTIME/'offline_stubs.c'),
               '-framework', 'Cocoa', '-framework', 'CoreText', '-framework', 'QuartzCore',
               '-framework', 'Metal', '-Wl,-dead_strip', '-o', str(binary)]
    if original:
        command.insert(1, '-DMENU_ORIGINAL=1')
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-5000:]
    result = subprocess.run([str(binary)] + (["budgets"] if budgets else []), capture_output=True, text=True, timeout=240)
    output = result.stdout.splitlines()
    print(name+': '+ '\n'.join(line for line in output if not line.startswith('pixels ')))
    return result, [line for line in output if line.startswith('pixels ')]


def main():
    source = (RUNTIME/'portmenu.m').read_text()
    with tempfile.TemporaryDirectory(prefix='menu-raster-') as scratch:
        base = Path(scratch)
        good, pixels = run(base, 'good', source)
        assert good.returncode == 0, good.stderr[-5000:]
        print(f'pixel states: {len(pixels)}')
        if len(sys.argv) == 3 and sys.argv[1] == '--reference':
            old, old_pixels = run(base, 'original', Path(sys.argv[2]).read_text(), original=True)
            assert old.returncode == 0, old.stderr[-5000:]
            mismatches = [(a,b) for a,b in zip(pixels,old_pixels) if a!=b]
            assert len(pixels)==len(old_pixels) and not mismatches, f'pixel mismatch {len(mismatches)}: {mismatches[:8]}'
            print(f'original pixel comparison: {len(pixels)} states identical')
        mutations = {
            'old-refresh': ('if (readout_rows[i] != r[i] || strcmp(readout_text[i], b)) dirty = 1;', 'dirty = 1;'),
            'old-keyboard': ('int full = !keyboard_ctx ||', 'int full = 1 || !keyboard_ctx ||'),
            'hidden-guide': ('if (!guide_active() && !report_github_active()) {', 'if (1) {'),
            'hidden-row': ('i < n && i < top + visible_rows(); ++i)', 'i < n; ++i)'),
            'no-restore': ('if (!full) keyboard_restore(ctx, CGRectMake(xx - 2, y - 2, width + 4, 42));', '(void)xx;'),
        }
        for name, (old,new) in mutations.items():
            assert source.count(old)==1, name
            broken, _ = run(base, name, source.replace(old,new), budgets=name != "no-restore")
            assert broken.returncode == 1 and 'failures' in broken.stdout, name+' escaped detection: '+broken.stderr[-500:]
    print('menu_raster_test PASS')


if __name__ == '__main__':
    main()
