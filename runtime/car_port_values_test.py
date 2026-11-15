"""Car materials without console files; no assets or game launch required.

Plan: author hotspot/normal lighting scalars, generate glass by PC identities,
lift their runtime/menu gates, keep flake and console texture families gated.
Paint lobe at R.V=.95, power=16: fixed .515175, pinned base PC .880253.
Normals, wrapped-light response across two tilted normals: fixed 0.60,
pinned base flat 0.00. Glass windscreen opacity at N.V=1: fixed 0.25,
pinned base PC 0.475 (loader .3 + setter .25*(1-.3)); matching records 6 versus 0.
Each subfeature has its own original gate/stub control. Production getters,
shader arithmetic and generated glass records are compiled, with PC-only
settings rows. The suite autodiscovers this file in runtime/offline_tests.sh.
"""
import hashlib
import math
import os
from pathlib import Path
import re
import runpy
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RT = ROOT / "runtime"
sys.path.insert(0, str(ROOT / "tools_snapshot"))
sys.path.insert(0, str(RT))
import pubtables_gen as pg
import x360_shader_lits as lits
from materials_stub_test import function

OLD_PAINT = '    if (car_paint < 0) { const char *e = getenv("NFS_CAR_PAINT"); car_paint = !(e && !strcmp(e, "pc")) && X360_SHADER_LITS; }'
OLD_NORMALS = '    if (carnm < 0) { const char *e = getenv("NFS_CAR_NORMALMAPS"); carnm = !(e && !strcmp(e, "pc")) && X360_SHADER_LITS; }'
OLD_GLASS = 'static const GlassX360 glass_x360[1];\n#define GLASS_X360_COUNT 0\n'
PINS = (
    (OLD_PAINT, "3cf6d117c3efcda154b33430484b758a7a45fff48bc567d1e033b322069b61a5"),
    (OLD_NORMALS, "506253190d47e40ae314432ff716f6622c268e3c43aafd7b76ef13c5dd69c72e"),
    (OLD_GLASS, "5fab1e4d7b7eaaf8ee9b1ee20314e7e6947d83e5ccc9aa534719affcce275577"),
)
ROWS = ("graphics.car_paint", "graphics.car_normals", "graphics.car_glass")


def run(args, **kw):
    p = subprocess.run(list(map(str, args)), capture_output=True, text=True, **kw)
    assert p.returncode == 0, p.stdout[-2000:] + p.stderr[-4000:]
    return p.stdout


def pc_fixture(d):
    """Independent PC identities; values deliberately differ from fallback recipes."""
    names = ("WINDOWMASK", "WINDSHIELD", "WINDSHIELD_TINT_L1_BLACK",
             "HEADLIGHTGLASS", "CLEARPLASTIC", "MOLDINGS", "PAINT")
    data = bytearray()
    for name in names:
        b = bytearray(0xB0)
        h = 0xFFFFFFFF
        for c in name.encode():
            h = (h * 33 + c) & 0xFFFFFFFF
        struct.pack_into("<I", b, 12, h)
        b[0x14:0x14 + len(name)] = name.encode()
        for off, value in ((0x50, 0.4), (0x54, 0.4), (0x80, 3.0), (0x90, 0.5)):
            struct.pack_into("<f", b, off, value)
        data += struct.pack("<II", 0x00135200, len(b)) + b
    (d / "GLOBAL").mkdir()
    (d / "GLOBAL/GLOBALB.BUN").write_bytes(data)


def shader_line(source, needle):
    """Decode only a production MSL line, including its scalar macro splices."""
    lines = [line for line in source.splitlines() if needle in line]
    assert len(lines) == 1, needle
    return lines[0]


def check_materials(d, control=None):
    source = (RT / "materials.m").read_text()
    shader = lits.text(None, "no console")
    # Old no-console shader scalars were zero. Independent of PORT_SCALARS' keys.
    if control in ("paint", "normals"):
        prefix = "X360S_CAR_HOT_" if control == "paint" else "X360S_CARNM_"
        shader = re.sub(r'(#define ' + prefix + r'\w+ )"[^"]+"', r'\1"0.0"', shader)
    (d / "lits.inc").write_text(shader)
    paint, normals = function(source, "car_paint_on"), function(source, "carnm_on")
    if control == "paint":
        paint = re.sub(r'    if \(car_paint < 0\).*', lambda _: OLD_PAINT, paint)
    if control == "normals":
        normals = re.sub(r'    if \(carnm < 0\).*', lambda _: OLD_NORMALS, normals)
    glass = OLD_GLASS if control == "glass" else pg.glass_port(d)
    # Keep the production scalar-bearing expression; C evaluates its numeric
    # body after macro/string concatenation, using the same pow and clamp.
    spec = shader_line(source, '    float spec = car_spec_pow(rv, sp) + ')
    wrap = shader_line(source, '    float w0 = saturate(dot(n, L0) + ')
    spec = spec.replace('m.k[0].y', 'hotspot')
    wrap = wrap.replace('dot(n, L0)', 'ndl').replace('dot(n, L1)', 'ndl')
    emitter = '#include <stdio.h>\n#include "lits.inc"\nint main(void) { puts(' + spec.rstrip() + '); puts(' + wrap.rstrip() + '); }\n'
    (d / "emit.c").write_text(emitter)
    run(["clang", d / "emit.c", "-o", d / "emit"])
    lines = run([d / "emit"]).splitlines()
    # No duplicated production equation: replace only shader intrinsics/types.
    math_lines = "\n".join(lines).replace('car_spec_pow', 'powf').replace('saturate', 'sat')
    harness = r'''
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include "lits.inc"
static int car_paint=-1, carnm=-1, diag_on;
static const char *car_paint_msl;
static int carpaint_nan_test(const char *s,int mode) { (void)s; (void)mode; return 0; }
static void diag_event(const char *fmt,...) { (void)fmt; }
typedef struct { uint32_t hash; float min_a,max_a,env_min,env_max; } GlassX360;
%s
%s
%s
static float sat(float x) { return fminf(1,fmaxf(0,x)); }
static float sample(float ndl) {
 float rv=1,sp=16,hotspot=1,lit=1;
%s
 return carnm_on() ? w0 : 0.0f;
}
int main(void) {
 float rv=.95f,sp=16,hotspot=1,lit=1,ndl=0.4f;
%s
 float alpha=0.475f; // PC loader .3/1, setter scales the range by .25.
 for (unsigned i=0;i<GLASS_X360_COUNT;i++)
   if (glass_x360[i].hash==0x471a1dcau) alpha=glass_x360[i].max_a; // N.V=1
 printf("paint %%.6f\nnormals %%.6f\nglass %%.6f\nrecords %%u\n",
        car_paint_on()?spec:2*powf(rv,16),sample(.7f)-sample(.1f),alpha,GLASS_X360_COUNT);
}
''' % (paint, normals, glass, math_lines, math_lines)
    (d / "check.c").write_text(harness)
    run(["clang", "-w", d / "check.c", "-o", d / "check"])
    env = dict(PATH=os.environ["PATH"])
    return {k: float(v) for k, v in (line.split() for line in run([d / "check"], env=env).splitlines())}


def check_rows(d):
    driver = runpy.run_path(str(RT / "post_port_values_test.py"))["DRIVER"]
    add = "".join('static Setting r%d={.id="%s",.type=SET_CHOICE,.choices=kXP,.values=kXPV,.nchoices=2};settings_add(&r%d);\n'
                  % (i, name, i) for i, name in enumerate(ROWS + ("graphics.car_flake",)))
    show = "".join('printf("%%d\\n",r%d.hidden||r%d.locked);\n' % (i, i) for i in range(4))
    (d / "rows.c").write_text(driver % (add, show))
    run(["clang", "-w", "-DNFS_PUBLIC=1", "-I", RT, "-I", ROOT / "recomp", d / "rows.c", RT / "settings.c", "-o", d / "rows"])
    (d / "roots.txt").write_text("pc_root=%s\n" % d)
    env = dict(PATH=os.environ["PATH"], HOME=str(d / "home"), NFS_ROOTS=str(d / "roots.txt"))
    assert run([d / "rows"], env=env).splitlines() == ["0", "0", "0", "0"]


def main():
    for fragment, digest in PINS:
        assert hashlib.sha256(fragment.encode()).hexdigest() == digest
    with tempfile.TemporaryDirectory(prefix="car-port-") as tmp:
        d = Path(tmp)
        pc_fixture(d)
        good = check_materials(d)
        expected = {"paint": .515175, "normals": .6, "glass": .25}
        def check(feature, result):
            assert math.isclose(result[feature], expected[feature], abs_tol=1e-6), result
        for feature in expected:
            check(feature, good)
        assert good["records"] == 6, good  # PAINT excluded; six glass identities
        for feature, value in (("paint", .880253), ("normals", 0.0), ("glass", .475)):
            bad = check_materials(d, feature)
            assert math.isclose(bad[feature], value, abs_tol=1e-6), bad
            try:
                check(feature, bad)
            except AssertionError:
                pass
            else:
                raise AssertionError("original %s gate/stub escaped its check" % feature)
            print("PASS %s: port %.6f; pinned base %.6f" % (feature, good[feature], bad[feature]))
        check_rows(d)
        print("PASS: 3/3 PC-only rows listed; flake hidden; console-only normal textures remain unavailable")
    # No accidental enable of features whose values/textures are out of scope.
    stub = lits.text(None, "no console")
    macros = dict(re.findall(r'^#define (X360S_\w+) "([^"]+)"', stub, re.M))
    assert all(float(v) == 0 for n, v in macros.items() if n.startswith(("X360S_CAR_FLAKE", "X360S_CAR_FADE")) or n == "X360S_CARNM_FLAKE_T")
    source = (RT / "materials.m").read_text()
    assert '&& X360_SHADER_LITS' in function(source, "flake_on")
    assert 'else if (X360_SHADER_LITS)' in function(source, "carnm_find")


if __name__ == "__main__":
    main()
