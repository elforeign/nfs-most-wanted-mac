"""Building glass on the port's own reflection V (NFS_BUILDING_GLASS=port): V = UV0.y.

1. GPU, no private assets: a synthetic facade whose UV0.y runs from -.25 (top) to .75 (bottom) goes through the
   production VS edit (glass_port.m) of each glossywindow vertex shader and is drawn with the t1 pixel shader over a
   reflection with 64 colour rows (red = 4 x row, repeat addressing). Observable: the largest error, in reflection
   rows (wrapped), between the row drawn down the facade's centre column and the row V = UV0.y selects.
       V = UV0.y (this rule)          max error 0 rows (t1 and t2; t0 does not pair with the t1 PS)
       .5*UV0.y control (k[27].w = 1) max error 24 rows
       unedited PC VS                 max error 24 rows
   Required: <= 1 with the rule (nearest-sample edges); >= 8 for both controls. Broken run (glass_port.m without the
   doubling, i.e. the PC rule): rule t1 fails.
2. Console measuring stick, only when the player's generated console table (gen/glass_uv1.inc, X360 UV1.y per PC
   glass vertex) and the PC STREAML2RA.BUN are present: the mean wrapped |V - console V| over the matched vertices.
       V = UV0.y (this rule)                         .054 (74.8 % exact, wrapped)
       whole-solid height V (the previous Port rule) .190 (measured at 47e55156 with its generator)
       PC .5*UV0.y                                   .264
   Required: at most half the previous rule's (.095) and below PC's. The table is read, never copied or shipped.
3. Default without console files (NFS_BUILDING_GLASS unset, and the F10 row): Port. The real provider selector from
   materials.m and the real settings.c with the production row, compiled with no console files:
       unset, no console files   old default (PC): provider 1, menu value 0   new: provider 2, menu value 2
       unset, console files      provider 1 (console, unchanged); pc, port: 1, 2 (unchanged)
   The old default fails both checks (unset gave provider 1 and menu 0, wanted 2 and 2); the new passes both.
"""
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile

import numpy as np

RT = Path(__file__).resolve().parent
ROOT = RT.parent
sys.path.insert(0, str(ROOT / 'tools_snapshot'))
import x360_glass_uv1 as pcread   # noqa: E402

TESTDATA = ROOT / 'host/shaders/testdata/glossywindow'
VS = ['t0_world_p0_vs_1_1.bin', 't1_world_p0_vs_1_1.bin', 't2_world_p0_vs_3_0.bin']
PS = 't1_world_p0_ps_2_0.bin'
TOP, BOTTOM = -0.25, 0.75          # facade UV0.y at z = 10 and z = 0
PREVIOUS = 0.190                   # mean wrapped V error of the whole-solid height rule (header)
# Every PC glossywindow VS writes the reflection V as .5 x TEXCOORD0.y (v[3]); the rule doubles it.
HALF_V = re.compile(r'\{[^}\n]*si\.v\[3\]\.yyyy[^}\n]*sm_mul[^}\n]*(so\.tex\[1\]|o\[4\])\.y = ')


def port_v(uv0):
    """the rule glass_port.m puts in the shader (the GPU part checks the shader against it)"""
    return uv0[:, 1]


def run(args, expected=0, **kw):
    p = subprocess.run([str(a) for a in args], capture_output=True, text=True, **kw)
    assert p.returncode == expected, ' '.join(map(str, args)) + '\n' + p.stdout[-2000:] + p.stderr[-4000:]
    return p


def facade():
    """two triangles of a vertical facade at y = 0 facing -y, x and z 0..10, UV0.y linear in z"""
    corners = [(0, 0), (10, 0), (10, 10), (0, 0), (10, 10), (0, 10)]
    return b''.join(struct.pack('<6fI2f', x, 0, z, 0, -1, 0, 0xFFFFFFFF, 0.3, BOTTOM + (TOP - BOTTOM) * z / 10)
                    for x, z in corners)


def row_error(stdout):
    """largest wrapped row error down the centre column against V = UV0.y"""
    col = [int(x) for x in re.search(r'col=(\S+)', stdout).group(1).split(',')]
    h, worst, n = len(col), 0, 0
    for y, red in enumerate(col):
        if red < 0:
            continue
        z = (2 - 2 * (y + 0.5) / h) * 5                    # WorldViewProj: z 0..10 -> clip y -1..1, row 0 on top
        want = int(np.floor((port_v(np.array([[0.3, BOTTOM + (TOP - BOTTOM) * z / 10]]))[0] % 1.0) * 64)) % 64
        d = abs(round(red / 4) - want)
        worst, n = max(worst, min(d, 64 - d)), n + 1
    assert n >= 32, f'facade covers {n} pixels of the column'
    return worst


EMIT = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "glass_port.h"
#include "../host/shaders/sm_translate.h"
static void *slurp(const char *p, long *n) { FILE *f = fopen(p, "rb"); if (!f) return NULL; fseek(f, 0, SEEK_END); *n = ftell(f);
    rewind(f); void *b = malloc(*n); if (fread(b, 1, *n, f) != (size_t)*n) { fclose(f); return NULL; } fclose(f); return b; }
static int spit(const char *p, const void *b, size_t n) { FILE *f = fopen(p, "wb"); if (!f) return 0; fwrite(b, 1, n, f); fclose(f); return 1; }
int main(int argc, char **argv) {
    long n;
    if (argc == 5 && !strcmp(argv[1], "msl")) {          // msl vs|pcvs|ps <shader.bin> <out.metal>
        uint32_t *t = slurp(argv[3], &n);
        if (!t) return 3;
        SmOptions o; sm_default_options(&o); SmResult r = {0};
        if (sm_translate(t, (uint32_t)n / 4, &o, &r)) return 3;
        char *s = !strcmp(argv[2], "vs") ? glass_port_vs(r.msl, 20) : strdup(r.msl);
        if (!s) return 5;
        if (!strcmp(argv[2], "vs") && glass_port_vs("not a shader", 20)) return 6;
        return spit(argv[4], s, strlen(s)) ? 0 : 1;
    }
    return 2;
}
'''


def gpu_part():
    with tempfile.TemporaryDirectory(prefix='glassport-') as temp:
        td = Path(temp)
        (td / 'emit.c').write_text(EMIT)
        run(['clang', '-O1', '-Wall', '-Wextra', '-Werror', '-I', RT, '-c', RT / 'glass_port.m', '-o', td / 'gp.o'])
        run(['clang', '-O1', '-Wall', '-Wextra', '-I', RT, '-c', td / 'emit.c', '-o', td / 'emit.o'])
        run(['clang++', td / 'emit.o', td / 'gp.o', ROOT / 'host/shaders/build/libsmtranslate.a', '-o', td / 'emit'])
        raw = facade()
        (td / 'facade.bin').write_bytes(raw)
        (td / 'none.bin').write_bytes(struct.pack('<f', -1e30) * (len(raw) // 36))   # render.m's empty side row
        for f in VS:
            run([td / 'emit', 'msl', 'vs', TESTDATA / f, td / (f + '.metal')])
            run([td / 'emit', 'msl', 'pcvs', TESTDATA / f, td / (f + '.pc.metal')])
            text = (td / (f + '.pc.metal')).read_text()
            assert len(HALF_V.findall(text)) == 1 and '0.5f' in text, f + ': reflection V is not .5 x TEXCOORD0.y'
        run([td / 'emit', 'msl', 'ps', TESTDATA / PS, td / 'ps.metal'])
        binary = td / 'draw'
        run(['clang', '-O1', '-Wall', '-Wextra', '-Werror', '-fobjc-arc', RT / 'glass_port_test.m',
             '-framework', 'Foundation', '-framework', 'Metal', '-o', binary])
        err = {}
        for f in VS:            # every edited glossywindow VS compiles; those that pair with the t1 PS are measured
            p = subprocess.run([str(binary), td / (f + '.metal'), td / 'ps.metal', td / 'facade.bin', td / 'none.bin', '0'],
                               capture_output=True, text=True)
            if 'compile' in p.stderr or p.returncode not in (0, 1):
                raise AssertionError(f + ': ' + p.stdout + p.stderr)
            if p.returncode == 0:
                err['rule ' + f[:2]] = row_error(p.stdout)
        assert 'rule t1' in err, 'the t1 pair did not draw'
        for name, shader in (('uv0half', VS[1] + '.metal'), ('pc_vs', VS[1] + '.pc.metal')):
            p = run([binary, td / shader, td / 'ps.metal', td / 'facade.bin', td / 'none.bin', '1' if name == 'uv0half' else '0'])
            err[name] = row_error(p.stdout)
    print('glass_port_test: GPU max row error ' + ', '.join(f'{k} {v}' for k, v in err.items()))
    for k, v in err.items():
        if k.startswith('rule'):
            assert v <= 1, k + ': the edited VS does not reflect at V = UV0.y'
        else:
            assert v >= 8, k + ' control matches V = UV0.y'


def console_part():
    inc = RT / 'gen/glass_uv1.inc'
    game = Path(os.environ.get('NFS_GAME', ROOT.parent / 'Need For Speed Most Wanted Black Edition'))
    bundle = game / 'TRACKS/STREAML2RA.BUN'
    text = inc.read_text() if inc.is_file() else ''
    m = re.search(r'#define GLASS_UV1_VERTICES (\d+)u', text)
    if not m or not int(m.group(1)) or not bundle.is_file():
        print('glass_port_test: console comparison SKIP (no generated console table or PC track bundle)')
        return

    def block(name):
        return re.search(name + r'\[\] = \{(.*?)\};', text, re.S).group(1)
    values = np.array([float.fromhex(x.strip().rstrip('f')) for x in block('glass_uv1_values').split(',') if x.strip()])
    table = {int(h, 16): (int(n), int(f)) for h, n, f in re.findall(r'\{0x([0-9A-F]+)ull, (\d+)u, (\d+)u\}', block('glass_uv1_table'))}
    index = np.array([int(x) for x in block('glass_uv1_index').split(',') if x.strip()], np.int64)
    pd = bundle.read_bytes()
    seen, uv0, console = set(), [], []
    for a, b in pcread.top(pd, [(0, len(pd))]):
        for s in pcread.solids(pd, a, b):
            for raw, pv in pcread.pc_glass(pd, s):
                h = pcread.fnv64(raw)
                if h in seen or h not in table:
                    continue
                seen.add(h)
                n, first = table[h]
                assert n == len(pv), 'console row length'
                i = index[first:first + n]
                keep = i != 0xFFFF
                uv0.append(pv[keep, 6:8])
                console.append(values[i[keep]])
    uv0, console = np.concatenate(uv0), np.concatenate(console)

    def wrapped(v):
        return np.abs((v - console + 0.5) % 1.0 - 0.5)
    rule, pc = wrapped(port_v(uv0)), wrapped(0.5 * uv0[:, 1])
    print(f'glass_port_test: console V error over {len(console)} vertices: rule mean {rule.mean():.3f} '
          f'p90 {np.percentile(rule, 90):.3f} exact {np.mean(rule < 0.003):.3f}; PC .5*UV0.y mean {pc.mean():.3f} '
          f'p90 {np.percentile(pc, 90):.3f}; previous height rule {PREVIOUS:.3f}')
    assert rule.mean() <= PREVIOUS / 2, "not half the previous rule's error"
    assert rule.mean() < pc.mean(), "not below PC's error"


def default_part():
    """no GPU: the provider selector and the menu row with and without console files"""
    src = (RT / 'materials.m').read_text()
    i = src.index('static int glass_provider_get(void) {')
    fn = src[i:src.index('\n}\n', i) + 3]
    with tempfile.TemporaryDirectory(prefix='glass-default-') as tmp:
        d = Path(tmp)
        for lits in (0, 1):
            (d / f'sel{lits}.c').write_text('#include <stdio.h>\n#include <stdlib.h>\n#include <string.h>\n'
                f'#define X360_SHADER_LITS {lits}\n#define X360_ENGINE_CONSTS {lits}\nstatic int glass_provider;\n{fn}\n'
                'int main(void){printf("%d\\n",glass_provider_get());return 0;}\n')
            run(['clang', '-Werror', d / f'sel{lits}.c', '-o', d / f'sel{lits}'])
            for value, want in ((None, 2 if not lits else 1), ('pc', 1), ('x360', 1), ('port', 2)):
                env = dict(PATH=os.environ['PATH'])
                if value:
                    env['NFS_BUILDING_GLASS'] = value
                got = run([d / f'sel{lits}'], env=env).stdout.strip()
                assert got == str(want), f'console files {lits}, NFS_BUILDING_GLASS={value}: provider {got}, wanted {want}'
        port = (RT / 'settings_port.c').read_text()
        a = port.index('static void bglass_apply')
        row = port[a:port.index('};', port.index('static Setting s_bglass', a)) + 2]
        sys.path.insert(0, str(RT))
        from post_port_values_test import DRIVER
        driver = DRIVER % ('settings_add(&s_bglass);', 'printf("%d\\n",s_bglass.value);')
        driver = driver.replace('int main(void)', 'void materials_set_building_glass(int x){(void)x;}\n' + row + '\nint main(void)')
        for name in ('settings.c', 'roots.h', 'bundle_roots.h'):
            (d / name).write_bytes((RT / name).read_bytes())
        (d / 'gen').mkdir()
        (d / 'gen/x360_shader_lits.inc').write_text('#define X360_SHADER_LITS 0\n')
        (d / 'gen/x360_engine.inc').write_text('#define X360_ENGINE_CONSTS 0\n')
        (d / 'menu.c').write_text(driver)
        (d / 'roots.txt').write_text('pc_root=%s\n' % d)
        run(['clang', '-Werror', '-DNFS_PUBLIC=1', '-I', RT, '-I', ROOT / 'recomp', d / 'menu.c', d / 'settings.c', '-o', d / 'menu'])
        env = dict(PATH=os.environ['PATH'], HOME=str(d / 'home'), NFS_ROOTS=str(d / 'roots.txt'))
        for value, want in ((None, '2'), ('pc', '0'), ('port', '2')):
            if value:
                env['NFS_BUILDING_GLASS'] = value
            got = run([d / 'menu'], env=env).stdout.strip()
            assert got == want, f'menu, NFS_BUILDING_GLASS={value}: value {got}, wanted {want}'
    print('glass_port_test: default without console files = Port (selector 8/8, menu 3/3)')


def main():
    default_part()
    lib = ROOT / 'host/shaders/build/libsmtranslate.a'
    if not lib.is_file() or any(not (TESTDATA / f).is_file() for f in VS + [PS]):
        print('glass_port_test: SKIP shader translator or glossywindow fixtures missing')
        return 3
    gpu_part()
    console_part()
    print('glass_port_test: PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
