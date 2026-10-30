#!/usr/bin/env python3
"""Guide loader, CoreText layout, shortcut and real F10 input/pause tests (no GPU/game).

Proof (checks/failures): good 1700/0. Broken production source controls:
missing 1700/2; empty 1700/1; UTF-8 3140/1; format 2420/10; clamp 1700/10;
resize/cache 1700/1 each; wrap 620/120; clip 1700/1; priority 1696/88;
ordering 1700/4; tap 1700/9; release 1700/4; hidden 1700/2; Back 1698/32;
F10 1700/5; pause 1696/6; opening 1700/4; scale 1700/6; old fonts 2420/6;
old step 1700/6; modified release 1700/1; wheel 1700/2; page/repeat 1700/4.
25 controls rejected. Large panel 1960x1280; body 10, step 14; 28 lines
versus 14 in each previous layout, for both Guide and What to test.
Each control changes code in a scratch copy; a nonzero test exit is required.
"""
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / 'runtime'

# Source changes, not fixture changes: these checks must detect a broken implementation.
MUTATIONS = {
    'missing': ('guide.m', 'if (!source) source=@"This document could not be read. Press Back to return to Help.";',
                'if (!source) source=@"Unavailable";'),
    'empty': ('guide.m', 'source=@"This document is empty. Press Back to return to Help.";', 'source=@"Unavailable";'),
    'utf8': ('guide.m', 'initWithData:data encoding:NSUTF8StringEncoding', 'initWithData:data encoding:NSISOLatin1StringEncoding'),
    'format': ('guide.m', 'document=format(source);', 'document=[[NSAttributedString alloc] initWithString:source attributes:attributes(20,0)];'),
    'clamp': ('guide.m', 'offset=fmax(0,fmin(offset,fmax(0,content_height-viewport_height)));', '(void)offset;'),
    'resize': ('guide.m', '    clamp_scroll();\n}\nvoid guide_scroll', '}\nvoid guide_scroll'),
    'cache': ('guide.m', 'if (!lines || layout_width!=width)', 'if (1)'),
    'wrap': ('guide.m', 'CTTypesetterSuggestLineBreak(typesetter,pos,fmax(1,width))', 'CTTypesetterSuggestLineBreak(typesetter,pos,1000000)'),
    'clip': ('guide.m', 'CGContextClipToRect(ctx,clip);', '(void)clip;'),
    'priority': ('guide.m', 'if (enabled && r3)', 'if (0 && enabled && r3)'),
    'ordering': ('guide.m', 's->ordered=enabled && !(s->last & R3) && !(edge & R3);', 's->ordered=enabled;'),
    'tap': ('guide.m', 'r.inject=CREATE;', 'r.inject=0;'),
    'release': ('guide.m', 'r.hide |= s->consumed;', 'r.hide |= 0;'),
    'hidden': ('guide.m', 'testing_row.hidden = ![NSFileManager.defaultManager fileExistsAtPath:resource(1) ?: @""];', 'testing_row.hidden = 0;'),
    'back': ('guide.m', 'case GUIDE_BACK: guide_close();', 'case GUIDE_BACK: break;'),
    'f10': ('portmenu.m', 'if (!o) guide_close();', 'if (0) guide_close();'),
    'pause': ('portmenu.m', 'return is_open && M32(GAMEFLOW_STATE) == 6;', 'return 0;'),
    'opening': ('portmenu.m', 'if (trig) { last_phys = phys; return ~0u; }', 'if (trig && 0) { last_phys = phys; return ~0u; }'),
    'scale': ('guide.m', 'return active ? 2 : 1;', 'return 1;'),
    'old_fonts': ('guide.m', 'NSDictionary *normal=attributes(heading==1 ? 14 : heading==2 ? 12 : 10', 'NSDictionary *normal=attributes(heading==1 ? 28 : heading==2 ? 24 : 20'),
    'old_step': ('guide.m', 'fmax(14,ceil(ascent+descent+2))', 'fmax(28,ceil(ascent+descent+4))'),
    'modified_release': ('guide.m', 'r.hide |= s->modified;', 'r.hide |= 0;'),
    'wheel': ('portmenu.m', 'if (kind == 4) guide_scroll(-dy);', 'if (kind == 4) guide_scroll(dy);'),
    'page': ('portmenu.m', 'if (edge & B_R1) guide_command(GUIDE_PAGE_DOWN);', 'if (edge & B_R1) guide_command(GUIDE_DOWN);'),
    'repeat': ('portmenu.m', 'now + (dir != last_dir ? 0.40 : 0.09)', 'now + 1.0'),
}


def build_and_run(base, name, mutation=None):
    work = base / name
    work.mkdir()
    for rel in ('guide.m', 'guide.h', 'guide_test.m', 'portmenu.m'):
        shutil.copyfile(RUNTIME / rel, work / rel)
    if mutation:
        filename, old, new = mutation
        path = work / filename
        source = path.read_text()
        assert source.count(old) == 1, (name, source.count(old))
        path.write_text(source.replace(old, new))
    app = work / 'GuideCheck.app'
    binary = app / 'Contents/MacOS/GuideCheck'
    binary.parent.mkdir(parents=True)
    resources = app / 'Contents/Resources'
    resources.mkdir()
    # A real NSBundle, resolved without consulting the process working directory.
    with (app / 'Contents/Info.plist').open('wb') as file:
        plistlib.dump({'CFBundleExecutable': 'GuideCheck', 'CFBundleIdentifier': 'test.guide',
                      'CFBundlePackageType': 'APPL'}, file)
    shutil.copyfile(RUNTIME / 'docs/guide.md', resources / 'Guide.md')
    command = ['clang', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
               '-fsanitize=address,undefined', '-fobjc-arc', '-I' + str(ROOT / 'recomp'),
               '-I' + str(ROOT / 'recomp/out'), '-I' + str(RUNTIME), str(work / 'guide_test.m'),
               str(RUNTIME / 'settings.c'), str(RUNTIME / 'offline_stubs.c'),
               '-framework', 'Cocoa', '-framework', 'CoreText', '-framework', 'QuartzCore',
               '-framework', 'Metal', '-Wl,-dead_strip', '-o', str(binary)]
    compiled = subprocess.run(command, capture_output=True, text=True)
    if compiled.returncode:
        raise RuntimeError(name + ' compile failed:\n' + compiled.stderr[-5000:])
    result = subprocess.run([str(binary)], cwd='/private/tmp', capture_output=True, text=True,
                            env=dict(os.environ, ASAN_OPTIONS='detect_leaks=0'), timeout=60)
    counts = re.search(r'guide: (\d+) checks, (\d+) failures', result.stdout)
    if not counts:
        raise RuntimeError(name + ' did not finish:\n' + (result.stderr + result.stdout)[-5000:])
    checks, failures = map(int, counts.groups())
    print(f'{name}: {checks} checks, {failures} failures')
    if mutation:
        assert result.returncode != 0 and failures > 0, name + ' mutation escaped detection'
    else:
        assert result.returncode == 0 and failures == 0, result.stderr[-5000:]


def check_online_guide(text):
    assert '## Online (beta)' in text
    for phrase in ('early beta', 'Online races are not included', 'different internet connections',
                   'choose **Allow**', 'Keep the firewall enabled', 'Local Network', 'join code privately',
                   'not passwords', 'UDP 47800', 'swapping hosts', 'does not open a port',
                   'No internet relay fallback', 'Remove join codes'):
        assert phrase in text, phrase


def main():
    guide = (RUNTIME / 'docs/guide.md').read_text()
    check_online_guide(guide)
    old = guide.split('## Online (beta)')[0] + '## Audio' + guide.split('## Audio', 1)[1]
    try:
        check_online_guide(old)
    except AssertionError:
        print('Online guide: current 14 requirements pass; old section rejected', flush=True)
    else:
        raise AssertionError('old guide escaped detection')
    with tempfile.TemporaryDirectory(prefix='guide-check-') as scratch:
        base = Path(scratch)
        build_and_run(base, 'good')
        for name, mutation in MUTATIONS.items():
            build_and_run(base, name, mutation)
    print('guide_test PASS')


if __name__ == '__main__':
    main()
