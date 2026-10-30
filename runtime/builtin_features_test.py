#!/usr/bin/env python3
"""Built-in features: 7 absent registry ids, 7 obsolete keys ignored silently.
Old DualSense registry: 3 failures vs production 0; restored-row mutant: 1 vs 0.
SSAO default-Off mutant: 3/6 vs 0/6.
Retail hidden overrides: 0/6 failures; filter-omission mutant: 8 failures.
My picks snapshot/override-restore mutants: 26/3 failures vs production 0.
No game or GPU; compiles the real settings loader and production selectors.
"""
import os
from pathlib import Path
import re
import subprocess
import tempfile

RT = Path(__file__).resolve().parent
IDS = ('graphics.car_reflection', 'graphics.carbon_lampglass', 'graphics.contact_shadows', 'controls.pad_triggers',
       'controls.pad_light', 'controls.pad_players', 'controls.pad_road')
ENVS = ('NFS_CAR_REFLECTION', 'NFS_CARBON_LAMPGLASS', 'NFS_SSAO',
        'NFS_PAD_TRIGGERS', 'NFS_PAD_LIGHT', 'NFS_PAD_PLAYERS', 'NFS_PAD_ROAD')
port = (RT / 'settings_port.c').read_text()
post = (RT / 'x360_post.m').read_text()
stubs = (RT / 'settings_look_test.c').read_text()
stubs = stubs[stubs.index('#include <stdio.h>'):stubs.index('static int failures;')]


def registry(source):
    # Project the production constructors onto ids: callbacks and menu labels
    # do not affect whether settings_init can load/export an obsolete key.
    declarations = dict(re.findall(r'Setting\s+(\w+)\s*=\s*\{\s*\.id\s*=\s*"([^"]+)"', source))
    names = re.findall(r'REG\((\w+)\)|settings_add\(&(\w+)\)', source)
    return [declarations[a or b] for a, b in names if (a or b) in declarations]


with tempfile.TemporaryDirectory(prefix='builtin-features-') as scratch:
    tmp = Path(scratch)
    cases = [('production', port + post, 0)]
    for i, key in enumerate(IDS):
        cases.append((f'restored_row_{i}', port + post +
                      f'\nstatic Setting s_old = {{.id = "{key}"}}; REG(s_old);', 1))
    for label, source, expected in cases:
        ids = registry(source)
        assert len(ids) > 20 and 'graphics.bloom' in ids and 'graphics.colour_filter' in ids
        rows = '\n'.join(f'    settings_add(&(Setting){{.id="{key}", .type=SET_HEADING}});' for key in ids)
        profile = '\n'.join(f'    app_pref_set("{key}", "{value}");' for key, value in zip(IDS, ('pc',) + ('off',) * (len(IDS) - 1)))
        checks = '\n'.join(f'    failures += settings_find("{key}") != NULL;' for key in IDS)
        env_checks = '\n'.join(f'    failures += getenv("{key}") != NULL;' for key in ENVS)
        # A live row proves the loader ran successfully alongside the old keys.
        harness = stubs + '''
int main(void) {
    int failures = 0;
''' + profile + rows + '''
    Setting live = {.id="test.live", .type=SET_TOGGLE, .def=1};
    settings_add(&live); settings_init();
    failures += settings_get("test.live") != 1;
''' + checks + env_checks + '''
    printf("registry failures=%d; obsolete keys=7; live=%d\\n", failures, settings_get("test.live"));
    return failures;
}
'''
        c = tmp / f'{label}.c'
        c.write_text(harness)
        exe = tmp / label
        subprocess.run(['clang', '-I', str(RT), '-I', str(RT.parent / 'recomp'), str(c),
                        str(RT / 'settings.c'), '-o', str(exe)], check=True)
        env = {k: v for k, v in os.environ.items() if k not in ENVS}
        result = subprocess.run([exe], env=env, capture_output=True, text=True)
        assert result.returncode == expected and not result.stderr, result.stdout + result.stderr
        print(label, result.stdout.strip(), flush=True)

    selector = re.search(r'int x360_post_ssao_enabled\(void\) \{.*?\n\}', post, re.S)[0]
    for label, body, expected in (
            ('ssao', selector, 0),
            ('ssao_default_off', selector.replace('return !e || strcasecmp(e, "off");',
                                                   'return e && !strcasecmp(e, "on");'), 3)):
        c = tmp / f'{label}.c'
        c.write_text('#include <stdlib.h>\n#include <strings.h>\n'
                     'static int look_var_is(const char *v) { (void)v; return 0; }\n' + body +
                     '\nint main(void) { return x360_post_ssao_enabled() ? 1 : 0; }\n')
        exe = tmp / label
        subprocess.run(['clang', '-Wall', '-Wextra', '-Werror', str(c), '-o', str(exe)], check=True)
        failures = 0
        for value, wanted in ((None, 1), ('', 1), ('on', 1), ('off', 0), ('OFF', 0), ('garbage', 1)):
            env = {k: v for k, v in os.environ.items() if k != 'NFS_SSAO'}
            if value is not None:
                env['NFS_SSAO'] = value
            failures += subprocess.run([exe], env=env).returncode != wanted
        assert failures == expected, (label, failures)
        print(f'{label}: {failures}/6 failures', flush=True)

    # Compile the actual hidden rows with the production selectors: obsolete keys
    # remain ignored, while the look's own keys export Off before renderer startup.
    smoke_source = (RT / 'render.m').read_text()
    smoke = re.search(r'static int soft_smoke_on\(void\) \{.*?\n\}', smoke_source, re.S)[0]
    hidden = '\n'.join(re.search(r'static Setting ' + name + r' = \{.*?\};', port, re.S)[0]
                       for name in ('s_look_contact', 's_look_smoke'))
    c = tmp / 'retail_hidden.c'
    c.write_text(stubs + '\n' + hidden + '\n' +
                 'static int look_var_is(const char *v) { (void)v; return 0; }\n'
                 'static int postfx_look_variant(const char *v) { (void)v; return 0; }\n'
                 'static int soft_smoke = -1;\n' + selector + '\n' + smoke + r'''
int main(int argc, char **argv) {
    (void)argv;
    app_pref_set("graphics.contact_shadows", "off");
    app_pref_set("graphics.soft_smoke", "off");
    if (argc > 1) {
        app_pref_set("graphics.look_contact_shadows", "off");
        app_pref_set("graphics.look_soft_smoke", "off");
    }
    settings_add(&s_look_contact); settings_add(&s_look_smoke); settings_init();
    Setting *rows[8];
    int failures = settings_rows(SET_PICTURE, rows, 8) != 0;
    failures += x360_post_ssao_enabled() != (argc == 1);
    failures += soft_smoke_on() != (argc == 1);
    printf("retail hidden: %d failures; contact=%d smoke=%d rows=%d\n", failures,
           x360_post_ssao_enabled(), soft_smoke_on(), settings_rows(SET_PICTURE, rows, 8));
    return failures;
}
''')
    exe = tmp / 'retail_hidden'
    subprocess.run(['clang', '-Wall', '-Wextra', '-Werror', '-I', str(RT), '-I', str(RT.parent / 'recomp'),
                    str(c), str(RT / 'settings.c'), '-o', str(exe)], check=True)
    env = {k: v for k, v in os.environ.items() if k not in ('NFS_SSAO', 'NFS_SOFT_SMOKE')}
    for args in ([], ['retail']):
        result = subprocess.run([exe, *args], env=env, text=True, capture_output=True)
        assert result.returncode == 0, result.stdout + result.stderr
        print(result.stdout.strip(), flush=True)

    # A real regression: remove only Colour filter from the actual preset table.
    # The expanded C test must reject the incomplete look, including after restart.
    settings = (RT / 'settings.c').read_text()
    target = '    {"graphics.colour_filter", "100", "100"},\n'
    assert settings.count(target) == 1
    c = tmp / 'settings_no_filter.c'
    c.write_text(settings.replace(target, ''))
    exe = tmp / 'no_filter'
    subprocess.run(['clang', '-std=c11', '-Wall', '-Wextra', '-Werror', '-I', str(RT),
                    '-I', str(RT.parent / 'recomp'), str(RT / 'settings_look_test.c'), str(c), '-o', str(exe)], check=True)
    result = subprocess.run([exe], capture_output=True, text=True)
    if result.returncode == 3:
        print('SKIP: retail filter mutant needs generated X360 constants', flush=True)
        raise SystemExit(3)
    assert result.returncode != 0 and 'graphics.colour_filter' in result.stderr, result.stdout + result.stderr
    print('retail filter mutant: rejected; failures=' + str(result.stderr.count('FAIL line')), flush=True)

    # Omit the edit snapshot while retaining the My picks label. Selecting a
    # different preset and then My picks must expose the lost values.
    start = settings.index('static void look_row_edited(Setting *s) {')
    end = settings.index('int settings_look_apply(int which)', start)
    edited = settings[start:end]
    assert edited.count('    look_save_picks();') == 1
    c = tmp / 'settings_lost_edit.c'
    c.write_text(settings[:start] + edited.replace('    look_save_picks();', '') + settings[end:])
    exe = tmp / 'lost_edit'
    subprocess.run(['clang', '-std=c11', '-Wall', '-Wextra', '-Werror', '-I', str(RT),
                    '-I', str(RT.parent / 'recomp'), str(RT / 'settings_look_test.c'), str(c), '-o', str(exe)], check=True)
    result = subprocess.run([exe], capture_output=True, text=True)
    assert result.returncode != 0 and 'FAIL line' in result.stderr, result.stdout + result.stderr
    print('My picks lost-edit mutant: rejected; failures=' + str(result.stderr.count('FAIL line')), flush=True)

    # The displayed My picks label under an override must not suppress Restore.
    target = 'if (v == current(s) && (strcmp(s->id, "graphics.look_preset") || v == settings_look_choice())) return 0;'
    assert settings.count(target) == 1
    c = tmp / 'settings_no_restore.c'
    c.write_text(settings.replace(target, 'if (v == current(s)) return 0;'))
    exe = tmp / 'no_restore'
    subprocess.run(['clang', '-std=c11', '-Wall', '-Wextra', '-Werror', '-I', str(RT),
                    '-I', str(RT.parent / 'recomp'), str(RT / 'settings_look_test.c'), str(c), '-o', str(exe)], check=True)
    result = subprocess.run([exe], capture_output=True, text=True)
    assert result.returncode != 0 and 'FAIL line' in result.stderr, result.stdout + result.stderr
    print('My picks override-restore mutant: rejected; failures=' + str(result.stderr.count('FAIL line')), flush=True)
