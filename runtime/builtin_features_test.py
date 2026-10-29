#!/usr/bin/env python3
"""Built-in features: 3 absent registry ids, 3 obsolete keys ignored silently.
Restored-row mutant: 1 failure vs 0; SSAO default-Off mutant: 3/6 vs 0/6.
No game or GPU; compiles the real settings loader and production SSAO selector.
"""
import os
from pathlib import Path
import re
import subprocess
import tempfile

RT = Path(__file__).resolve().parent
IDS = ('graphics.car_reflection', 'graphics.carbon_lampglass', 'graphics.contact_shadows')
ENVS = ('NFS_CAR_REFLECTION', 'NFS_CARBON_LAMPGLASS', 'NFS_SSAO')
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
        profile = '\n'.join(f'    app_pref_set("{key}", "{value}");' for key, value in zip(IDS, ('pc', 'off', 'off')))
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
    printf("registry failures=%d; obsolete keys=3; live=%d\\n", failures, settings_get("test.live"));
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
