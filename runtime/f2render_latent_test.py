"""Latent render fixes: (1) matrix ops count every constant row they read, (2) CheckDeviceFormat advertises only
formats the converter decodes (V8U8 60 and V16U16 64 uploaded black).
(1) vs_1_1 m4x4 oPos, v0, c4 reflects float_max 8 and c4..c7 used with the fix; the original (no implicit rows) reports
5 and c4 only. m3x3 c10 -> 13 (original 11). (2) Every advertised format has a case in render.m's format switch:
fixed 0 unconverted formats; with 60/64 put back, 2 (60 64).
"""
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SH = ROOT / 'host/shaders'

DRIVER = r'''
#include "sm_translate.h"
#include <stdio.h>
static int run(const uint32_t *t, size_t n) {
    SmOptions o; sm_default_options(&o); SmResult r;
    if (sm_translate(t, n, &o, &r) != 0) { printf("error: %s\n", r.error); return 1; }
    printf("max=%d used=0x%x\n", r.float_max, r.float_used[0]);
    sm_result_free(&r); return 0;
}
#define VS11 (0xFFFE0000u | 0x0101u)   /* vs_1_1 version token, built so the bytes are not a literal */
int main(void) {
    uint32_t a[] = {VS11, 0x02000014, 0xC00F0000, 0x90E40000, 0xA0E40004, 0x0000FFFF};   /* m4x4 oPos, v0, c4 */
    uint32_t b[] = {VS11, 0x02000017, 0xC0070000, 0x90E40000, 0xA0E4000A, 0x0000FFFF};   /* m3x3 oPos.xyz, v0, c10 */
    return run(a, 6) | run(b, 6);
}
'''
FIX = '''        if ((in.op == OP_M4x4 || in.op == OP_M4x3 || in.op == OP_M3x4 || in.op == OP_M3x3 || in.op == OP_M3x2) && in.src.size() > 1) {
            // a matrix op reads one constant row per output component, not just the register it names
            int rows = in.op == OP_M4x4 || in.op == OP_M3x4 ? 4 : in.op == OP_M3x2 ? 2 : 3;
            for (int r = 1; r < rows; r++) { Src m = in.src[1]; m.num += r; see_src(m); }
        }
'''


def reflect(d, name, msl):
    (d / (name + '.cpp')).write_text(msl)
    c = d / 'drv.cpp'
    c.write_text(DRIVER)
    exe = d / name
    subprocess.run(['clang++', '-std=c++17', '-O1', '-I', SH, '-I', SH / 'tests', '-I', d, d / (name + '.cpp'),
                    SH / 'sm_parse.cpp', c, '-o', exe], check=True, capture_output=True)
    r = subprocess.run([str(exe)], capture_output=True, text=True)
    print(name, r.stdout.strip().replace('\n', ' | '), flush=True)
    return r.stdout.split()


with tempfile.TemporaryDirectory(prefix='f2render-') as d:
    d = Path(d)
    src = (SH / 'sm_msl.cpp').read_text()
    assert src.count(FIX) == 1, 'matrix row counting missing'
    # includes are relative to host/shaders
    def prep(s): return s.replace('#include "', '#include "' + str(SH) + '/')
    new = reflect(d, 'fixed', prep(src))
    assert new == ['max=8', 'used=0xf0', 'max=13', 'used=0x1c00'], new
    old = reflect(d, 'original', prep(src.replace(FIX, '')))
    assert old == ['max=5', 'used=0x10', 'max=11', 'used=0x400'], old

# (2) advertised formats vs converter
d3d = (ROOT / 'runtime/d3d9.m').read_text()
ren = (ROOT / 'runtime/render.m').read_text()


def unconverted(d3d_text):
    table = re.search(r'static const uint32_t ok\[\] = \{(.*?)\};', d3d_text, re.S).group(1)
    fmts = [int(x, 0) for x in re.findall(r'0x[0-9a-fA-F]+|\d+', table)]
    body = ren[ren.index('case 21: return MTLPixelFormatBGRA8Unorm'):]
    body = body[:body.index('static int is_depth_fmt')]
    cases = {int(x, 0) for x in re.findall(r'case (0x[0-9a-fA-F]+|\d+):', body)}
    return [f for f in fmts if f not in cases]


assert unconverted(d3d) == [], unconverted(d3d)
broken = d3d.replace('36, 50, 51, 111', '36, 50, 51, 60, 64, 111')
assert unconverted(broken) == [60, 64], unconverted(broken)
print('PASS matrix rows counted (max 8/13 vs original 5/11); advertised formats all converted (original 60, 64)')
