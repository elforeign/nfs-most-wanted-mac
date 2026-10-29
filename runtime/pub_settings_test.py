#!/usr/bin/env python3
"""Public-only F10 rows (pub_settings.c): Car reflections registered, default Sharp, a saved Standard exports
NFS_CAR_REFLECTION=pc for envcube.m. Default-Standard mutant: FAIL. No game or GPU; discovered by offline_tests.sh.
"""
import os
from pathlib import Path
import subprocess
import tempfile

RT = Path(__file__).resolve().parent
src = (RT / 'pub_settings.c').read_text()
stubs = (RT / 'settings_look_test.c').read_text()
stubs = stubs[stubs.index('#include <stdio.h>'):stubs.index('static int failures;')]


def run(source, saved):
    with tempfile.TemporaryDirectory(prefix='pub-settings-') as scratch:
        tmp = Path(scratch)
        (tmp / 'pub_settings.c').write_text(source)
        pref = f'    app_pref_set("graphics.car_reflection", "{saved}");\n' if saved else ''
        (tmp / 'main.c').write_text(stubs + '''
int main(void) {
''' + pref + '''    settings_init();
    const Setting *s = settings_find("graphics.car_reflection");
    const char *e = getenv("NFS_CAR_REFLECTION");
    printf("%d %d %s\\n", s != NULL, s ? settings_get("graphics.car_reflection") : -1, e ? e : "-");
    return 0;
}
''')
        exe = tmp / 'pub'
        subprocess.run(['clang', '-w', '-I', str(RT), '-I', str(RT.parent / 'recomp'), str(tmp / 'main.c'),
                        str(tmp / 'pub_settings.c'), str(RT / 'settings.c'), '-o', str(exe)], check=True)
        env = {k: v for k, v in os.environ.items() if k != 'NFS_CAR_REFLECTION'}
        return subprocess.run([str(exe)], capture_output=True, text=True, check=True, env=env).stdout.split()


fresh, saved = run(src, None), run(src, 'pc')
print('fresh', fresh, 'saved Standard', saved)
assert fresh[:2] == ['1', '1'], 'row missing or default not Sharp'
assert saved[1:] == ['0', 'pc'], 'saved Standard does not reach envcube'
broken = run(src.replace('.def = 1', '.def = 0'), None)
assert broken[:2] != ['1', '1'], 'default-Standard mutant not caught'
print('pub_settings_test: PASS (fresh Sharp, saved Standard -> NFS_CAR_REFLECTION=pc; default-Standard mutant fails)')
