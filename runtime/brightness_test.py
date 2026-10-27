"""Offline Brightness checks; no game launch. Run with tools/.venv/bin/python -I.

2026-10-23: 27,509 FE/math assertions pass under ASan/UBSan; disabling the
Brightness Act vtable guard fails at `bright.changed && !tod.changed`.
Renderer checks execute the actual render_set_gamma body without Metal.
"""
from pathlib import Path
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / 'runtime'


def run(args, **kw):
    return subprocess.run(args, check=True, text=True, **kw)


def main():
    with tempfile.TemporaryDirectory(prefix='brightness-test-') as directory:
        scratch = Path(directory)
        binary = scratch / 'brightness_test'
        flags = ['clang', '-std=c11', '-Wall', '-Wextra', '-Werror', '-O1', '-g',
                 '-fsanitize=address,undefined', '-I', str(ROOT / 'recomp'), '-I', str(RUNTIME)]
        sources = [str(RUNTIME / 'brightness_test.c'), str(RUNTIME / 'settings.c'), str(RUNTIME / 'offline_stubs.c')]
        run(flags + sources + ['-o', str(binary)])
        run([str(binary), str(scratch / 'app.txt')])
        source = (RUNTIME / 'fe_console.c').read_text()
        marker = 'void f_0051B870(Cpu *c)'
        start = source.index(marker)
        guard = 'if (brightness_vtable && M32(c->ecx) == brightness_vtable)'
        broken = source[:start] + source[start:].replace(guard, 'if (0)', 1)
        assert broken != source
        mutant = scratch / 'fe_console_broken.c'
        mutant.write_text(broken)
        run(flags + [f'-DFE_CONSOLE_SOURCE="{mutant}"'] + sources + ['-o', str(binary)])
        result = subprocess.run([str(binary), str(scratch / 'app.txt')], text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        assert result.returncode != 0 and 'bright.changed && !tod.changed' in result.stdout, result.stdout
        print('PASS broken Act guard rejected: ' + result.stdout.strip().splitlines()[-1])

        renderer = (RUNTIME / 'render_present.m').read_text()
        function = renderer[renderer.index('void render_set_gamma('):renderer.index('static const char *const gamma_src')]
        c = scratch / 'gamma_test.c'
        c.write_text('''#include <assert.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
static float gamma_lut[768];
static int gamma_active;
''' + function + '''
int main(int argc, char **argv) {
    assert(argc == 2);
    double gamma = atof(argv[1]);
    uint16_t input[768];
    for (int pass = 0; pass < 2; ++pass) {
        for (int i = 0; i < 768; ++i) input[i] = pass ? 0 : (i & 255) * 257;
        render_set_gamma(input);
        assert(gamma_active == (gamma > 0 || pass != 0));
        for (int i = 0; i < 768; ++i) {
            uint16_t expected = gamma > 0 ? (uint16_t)(65535 * pow((i & 255) / 255.0, 1 / gamma) + .5) : input[i];
            assert(gamma_lut[i] == expected / 65535.0f);
        }
    }
    puts("PASS render_set_gamma: 1536 LUT entries, override survives subsequent slider/guest ramp");
}
''')
        run(flags + [str(c), '-o', str(binary)])
        clean_env = dict(os.environ)
        clean_env.pop('NFS_TEST_GAMMA', None)
        run([str(binary), '0'], env=clean_env)
        for gamma in ['0.6', '1.6']:
            run([str(binary), gamma], env=dict(clean_env, NFS_TEST_GAMMA=gamma))


if __name__ == '__main__':
    main()
