#!/usr/bin/env python3
"""Native Controls toggle, live trigger sinks and compatible-vibration isolation.
Default and saved-Off: 3,707 checks / 0 failures; env On/Off: 3,577 / 0.
Overrides On/ON/on/Off/0/1: each 3,577 / 0; legacy parser: On and ON
both FAIL 3/3,577 (locked row, initial output and ongoing output disagree).
On -> Off: 2 -> 0 effect reports, 2 clears; rumble 1/1 identical 48-byte
payloads, 0 device opens. Preview is live; exit saves through the app.txt parser.
SHA-pinned renderer: 16 language IDs x 2 font classes x 17 widths.
Callback / row hook / feedback / callback cache / CJK / width / duplicate
controls fail 2/3707, 4/3603, 3/3707, 1/3739, 32/3707, 483/3707, 1056/9893.
Native page/cancel/defaults/exit proof: controlpages_test.py header.
Uses fake GC/HID sinks, production padfx/feedback/rumble/settings and the real
controlpages integration hook. A removed padfx apply callback must fail at runtime.
"""
from pathlib import Path
import hashlib
import re
import subprocess
import tempfile

R = Path(__file__).resolve().parent


def run(cmd, expected=0):
    result = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if result.returncode != expected:
        print(result.stdout[-7000:])
        raise AssertionError(f'exit {result.returncode}, expected {expected}: {cmd[0]}')
    return result.stdout


def main():
    with tempfile.TemporaryDirectory(prefix='trigopt-') as tmp:
        d = Path(tmp)
        source = R.parent/'recomp/out/speed_22.c'
        if not source.is_file():
            print('trigopt_test: SKIP (translated retail renderer unavailable)')
            raise SystemExit(3)
        renderer = re.search(r'void f_005A4390\(Cpu \*restrict c\) \{.*?\n}', source.read_text(), re.S)
        assert renderer and hashlib.sha256(renderer[0].encode()).hexdigest() == 'dba95c2d828d0406c28e8ee0ab49fe94db334b8b743e94a94ea1afb84de48df2'
        (d/'trigopt_renderer.inc').write_text(renderer[0] + '\n')
        roles = (R/'padbind.c').read_text().split('int padbind_trigger_role(', 1)[1].split('\n}', 1)[0]
        (d/'padfx_roles.inc').write_text('int padbind_trigger_role(' + roles + '\n}\n')
        app = (R/'app.m').read_text()
        prefs = app.split('// BEGIN offline prefs:', 1)[1].split('\n', 1)[1].split('// END offline prefs', 1)[0]
        (d/'trigopt_prefs.inc').write_text(prefs)
        common = ['clang', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function', '-g',
                  '-Wno-unused-label', '-Wno-parentheses-equality', '-fsanitize=address,undefined', '-fobjc-arc', '-I'+str(R.parent/'recomp'),
                  '-I'+str(R), '-I'+str(d)]
        sources = [str(R/'trigopt_test.m'), str(R/'trigopt_test_rumble.m')]
        libs = ['-framework', 'Foundation', '-framework', 'IOKit', '-lz']
        run(common + sources + libs + ['-o', str(d/'fixed')])
        for index, mode in enumerate(('default', 'saved-off', 'env-on', 'env-off', 'override-On', 'override-ON', 'override-on', 'override-Off', 'override-0', 'override-1')):
            profile = d/(str(index) + '-' + mode); profile.mkdir()
            print(run([str(d/'fixed'), mode, str(profile)]).strip())
        mutations = {
            'legacy-parser': (R/'padfx.m', 'int value = setting_bool_parse(getenv("NFS_PAD_TRIGGERS"));',
                              'int value = !getenv("NFS_PAD_TRIGGERS") || env_on("NFS_PAD_TRIGGERS");', 'PADFX_SOURCE'),
            'disconnected-padfx': (R/'trigopt.c', '.apply = padfx_set_triggers', '.apply = NULL', 'TRIGOPT_SOURCE'),
            'missing-row-hook': (R/'controlpages.c', 'trigopt_controls(&q);', '(void)q;', 'CONTROLPAGES_SOURCE'),
            'shared-feedback-stop': (R/'padfx.m', 'if (!on) { want.triggers = 0; post(); }',
                                     'if (!on) { want.triggers = 0; post(); feedback_invalidate_effects(); }', 'PADFX_SOURCE'),
            'uncached-callback': (R/'trigopt.c', 'M32(vt + 4) = slot_draw;', 'M32(vt + 4) = host_fn(draw_slot, "Repeated Draw");', 'TRIGOPT_SOURCE'),
            'cjk-overflow': (R/'trigopt.c', 'scale *= 2;', 'scale *= 1;', 'TRIGOPT_SOURCE'),
            'no-width-limit': (R/'trigopt.c', 'M32(label + 0x74) = M32(vt + 16);', 'M32(label + 0x74) = 0;', 'TRIGOPT_SOURCE'),
            'duplicate-row': (R/'trigopt.c', 'if (last != end && last && is_trigger(last - 4)) return;',
                              '(void)last;', 'TRIGOPT_SOURCE'),
        }
        for name, (source, old, new, macro) in mutations.items():
            text = source.read_text()
            assert text.count(old) == 1, name
            mutant = d/(name + source.suffix)
            mutant.write_text(text.replace(old, new))
            binary = d/name
            run(common + [f'-D{macro}="{mutant}"'] + sources + libs + ['-o', str(binary)])
            modes = ('override-On', 'override-ON') if name == 'legacy-parser' else ('default',)
            for index, mode in enumerate(modes):
                profile = d/(name + '-prefs-' + str(index)); profile.mkdir()
                output = run([str(binary), mode, str(profile)], expected=1)
                print(name + ': ' + output.splitlines()[-1])
    print('trigopt_test: PASS')


if __name__ == '__main__':
    main()
