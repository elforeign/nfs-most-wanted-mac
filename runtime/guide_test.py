#!/usr/bin/env python3
"""Guide loader, CoreText layout, shortcut and real F10 input/pause tests (no GPU/game).

Licences: wrong document 1917/4; omitted row 1917/9; good 1917/0.
Proof (checks/failures): good 1917/0. Broken production source controls:
missing 1917/2; empty 1917/1; UTF-8 3357/1; format 2637/10; clamp 1917/10;
resize/cache 1917/1 each; wrap 837/120; clip 1917/1; priority 1913/88;
ordering 1917/4; tap 1917/9; release 1917/4; hidden 1917/2; Back 1915/32;
F10 1917/4; pause 1913/6; opening 1917/4; scale 1917/6; old fonts 2637/6;
old step 1917/6; modified release 1917/1; wheel 1917/2; page/repeat 1917/4.
31 controls rejected; replay key/pad/focus/disconnect: 1917 checks, 8/5/4/2 failures. Large panel 1960x1280; body 10, step 14; 28 lines
versus 14 in each previous layout, for both Guide and What to test.
Streaming input adds 10 checks: good 1921/0; repeat/editor-pad/close faults have 6/1/1 failures (1921 checks).
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
PROOF=[]

# Source changes, not fixture changes: these checks must detect a broken implementation.
MUTATIONS = {
    'licences': ('guide.m', 'testing == 2 ? @"Licences.md"', 'testing == 2 ? @"Guide.md"'),
    'licences_row': ('guide.m', 'settings_add(&licences_row);', ''),
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
    (resources / 'Licences.md').write_text('\n\n'.join((ROOT / name).read_text() for name in
        ('NOTICE', 'LICENSE', 'media/PREBUILT.md', 'media/licenses/LGPL-2.1.txt', 'media/licenses/FFmpeg-notices.txt')))
    command = ['clang', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
               '-fsanitize=address,undefined', '-fobjc-arc', '-I' + str(ROOT / 'recomp'),
               '-I' + str(ROOT / 'recomp/out'), '-I' + str(RUNTIME), str(work / 'guide_test.m'),
               str(RUNTIME / 'settings.c'), str(RUNTIME / 'text_fit.c'), str(RUNTIME / 'offline_stubs.c'), str(RUNTIME / 'replay_input.c'),
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
    PROOF.append(f'{name}: {checks} checks, {failures} failures')
    if mutation:
        assert result.returncode != 0 and failures > 0, name + ' mutation escaped detection'
    else:
        assert result.returncode == 0 and failures == 0, result.stderr[-5000:]


def check_online_guide(text):
    assert '## Online (beta)' in text
    for phrase in ('early beta', 'Online races are not included', 'different internet connections',
                   'Create room', 'Access is free', 'R-XXXX-XXXXX', 'identify rooms rather than passwords',
                   "contain no player's internet address", 'Keep the firewall enabled',
                   'no shared relay configured', 'Relay unavailable', 'Reconnecting',
                   'Old endpoint codes are unsupported', 'Remove join codes',
                   '60 fps', 'six nearby remote cars', 'UDP is unencrypted'):
        assert phrase in text, phrase
    for phrase in ('Public IPv4 (optional)', 'choose Host', 'forward **UDP 47800**',
                   'Join codes contain the host', 'No internet relay fallback'):
        assert phrase not in text, phrase



def main():
    guide = (RUNTIME / 'docs/guide.md').read_text()
    check_online_guide(guide)
    old = guide.split('## Online (beta)')[0] + '## Audio' + guide.split('## Audio', 1)[1]
    try:
        check_online_guide(old)
    except AssertionError:
        print('Online guide: current 22 requirements pass; old section rejected', flush=True)
    else:
        raise AssertionError('old guide escaped detection')
    with tempfile.TemporaryDirectory(prefix='guide-check-') as scratch:
        base = Path(scratch)
        build_and_run(base, 'good')
        for name, mutation in MUTATIONS.items():
            build_and_run(base, name, mutation)
        for name,mutation in [
            ('stream-repeat',('portmenu.m','(!repeat && !was_down)','((void)was_down, !repeat)')),
            ('stream-editor-pad',('portmenu.m','if(stream_key_editing()) { last_phys=phys; hold_after_close=phys; return ~0u; }','if(0) { last_phys=phys; hold_after_close=phys; return ~0u; }')),
            ('stream-close',('portmenu.m','if (!o) stream_key_close();','(void)o;')),
            ('replay-key',('portmenu.m','if(replaySave) replay_save_action(0);','if(0 && replaySave) replay_save_action(0);')),
            ('replay-pad',('portmenu.m','if(replayPad.save) replay_save_action(0);','if(0 && replayPad.save) replay_save_action(0);')),
            ('replay-focus',('portmenu.m','replay_input_cancel(&replayInput);','(void)0;')),
            ('replay-disconnect',('portmenu.m','replay_input_disconnect(&replayInput);','(void)0;')),
        ]:
            build_and_run(base,name,mutation)
    cache=ROOT/'.metal-cache/guide'; cache.mkdir(parents=True,exist_ok=True)
    (cache/'proof.log').write_text('\n'.join(PROOF)+'\n')
    print('guide_test PASS')


if __name__ == '__main__':
    main()
