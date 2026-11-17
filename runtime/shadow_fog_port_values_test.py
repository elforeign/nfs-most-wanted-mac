"""The sun shadow fit and the world fog clamp without the console files: both run on this port's own values and the
Shadow fit row is listed. No game launch and no game bytes; the generators' no-console output and production fragments
are compiled.

Fixed: 10/10 fit numbers are port values, the fit (runtime/x360_shadow_fit.cpp) maps 8/8 receiver points (1-49 m) inside
the map and gives the ground 3-8 m ahead of the camera 22.4 % of the map's rows, the fit is not forced to PC, the row is
listed, and the fog patch raises the PC clamp 2500 to 1e9.
Control (the pinned state before the change): the fit numbers are zero (map size 0) and the fit's matrix is not finite
(0/8 points inside), the fit is forced to PC, the row is hidden and the fog patch leaves the clamp at 2500.
For scale: the same fit with the PC's 0.01 m near plane gives the 3-8 m ground 12.1 % of the rows (port / PC 1.86).
Run: tools/.venv/bin/python -I runtime/shadow_fog_port_values_test.py"""
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

RUNTIME = Path(__file__).resolve().parent
ROOT = RUNTIME.parent
sys.path.insert(0, str(ROOT / "tools_snapshot"))
sys.path.insert(0, str(RUNTIME))
import pubtables_gen as pg  # noqa: E402
import x360_shader_lits as lits  # noqa: E402
import post_port_values_test as ppv  # noqa: E402  (its settings driver)

ROW = "graphics.shadow_fit"
# The state before the change, pinned (the control).
OLD_FIT = "    if (!X360_ENGINE_CONSTS) fit = 0;               // no X360 default.xex: the X360 shadow values are unknown, PC fit\n"
OLD_FIT_SHA = "0216c1d806f0519883bd1435d6910bcc55ad19e487b39474b41a14ab46a106aa"
OLD_GATE = '    {"graphics.shadow_fit", "engine"},\n'
OLD_GATE_SHA = "ecea0133fca1839bfa60871eb899c1083bcd6aa23ec35cc444dcc99f91d61f51"
OLD_FOG = "!fog_x360() || !X360_SHADER_LITS) return NULL;"
OLD_FOG_SHA = "1831be8c0441160cd9a52b71ed094c712850be6fc319a8b78b3fccf3761a85b5"
NEW_FOG = "!fog_x360()) return NULL;"
FIT_ANCHOR = "    if (pending_fit >= 0) fit = 2 - pending_fit;   // F10: x360, x360range, pc\n"
GATE_ANCHOR = "    // (Shadow fit needs neither: without default.xex the fit runs on SHADOW_FALLBACK)\n"

FIT_DRIVER = r'''
#include <math.h>
#include <stdio.h>
#include "gen/x360_engine.inc"
void x360_shadow_fit(const float player_view[16], float focus_x, float focus_y, const float light_dir[3],
                     float focus_near, float focus_far, float proj_out[16]);
static int fit = 2;
int main(void) {
%(fit_line)s
    /* camera 1.5 m above the road looking along world +x (world z up): view x = world y, y = z, z = x */
    float v[16] = {0, 0, 1, 0,  1, 0, 0, 0,  0, 1, 0, 0,  0, -1.5f, 0, 1}, out[16];
    float light[3] = {0.61237244f, 0.61237244f, 0.5f};          /* sun 30 deg up, 45 deg off the view */
    float fx = 1.0f / tanf(0.5f * 75.0f * 3.14159265f / 180.0f), fy = fx * 16.0f / 9.0f;
    x360_shadow_fit(v, fx, fy, light, NEAR, X360E_SHADOW_FOCUS_RACE, out);
    int inside = 0, finite = 1;
    for (int i = 0; i < 16; i++) finite &= isfinite(out[i]) != 0;
    for (int i = 0; i < 8; i++) {   /* receivers: 1 and 49 m ahead, at 0.9 of the view's half extents */
        float z = i & 1 ? 49.0f : 1.0f, x = (i & 2 ? 0.9f : -0.9f) * z / fx, y = (i & 4 ? 0.9f : -0.9f) * z / fy;
        float p[4] = {z, x, y + 1.5f, 1}, q[4] = {0};   /* world (forward, right, up) from view (x, y, z) */
        for (int c = 0; c < 4; c++) for (int r = 0; r < 4; r++) q[c] += (r == 0 ? p[0] : r == 1 ? p[1] : r == 2 ? p[2] : 1) * out[4 * r + c];
        float X = q[0] / q[3], Y = q[1] / q[3], Z = q[2] / q[3];
        inside += q[3] > 0 && fabsf(X) <= 1.0001f && fabsf(Y) <= 1.0001f && Z >= -0.0001f && Z <= 1.0001f;
    }
    float row[2];
    for (int k = 0; k < 2; k++) {   /* the road straight ahead at 3 and 8 m */
        float d = k ? 8.0f : 3.0f, q[4] = {0};
        for (int c = 0; c < 4; c++) q[c] = d * out[c] + out[12 + c];
        row[k] = q[1] / q[3];
    }
    printf("finite %%d\ninside %%d\nshare %%.4f\nfit %%d\nsize %%d\n", finite, inside, fabsf(row[1] - row[0]) / 2.0f, fit, X360E_SHADOW_SIZE);
    return 0;
}
'''

FOG_DRIVER = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include "gen/x360_shader_lits.inc"
static int fog_x360(void) { return 1; }
%(fog)s
int main(void) {
    float k[4] = {1, 0, 2500, 1.442695f};
    uint32_t t[9] = {0xFFFE0101u, 0x00000051u, 0xA00F000Fu, 0, 0, 0, 0, 0x00000001u, 0x0000FFFFu};
    memcpy(&t[3], k, 16);
    uint32_t *c = fog_patch(t, 9);
    float v = 2500; if (c) memcpy(&v, &c[5], 4);
    printf("clamp %%g\n", v);
    return 0;
}
'''


def sha(s):
    return hashlib.sha256(s.encode()).hexdigest()


def gen(d, old):
    (d / "gen").mkdir(parents=True, exist_ok=True)
    engine, shader = pg.ENGINE_STUB, lits.text(None, "test")
    if old:
        for name in pg.SHADOW_FALLBACK:
            engine, n = re.subn(r"^#define %s \S+$" % name, "#define %s 0.0f" % name, engine, flags=re.M)
            assert n == 1, name
        engine, n = re.subn(r"^#define X360E_SHADOW_SIZE \S+$", "#define X360E_SHADOW_SIZE 0", engine, flags=re.M)
        assert n == 1
        shader, n = re.subn(r"^#define X360F_KMAXDIST_WORLD \S+$", "#define X360F_KMAXDIST_WORLD 0.0f", shader, flags=re.M)
        assert n == 1
    (d / "gen/x360_engine.inc").write_text(engine)
    (d / "gen/x360_shader_lits.inc").write_text(shader)


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, check=True, **kw).stdout


def case(tmp, old, near=None):
    d = Path(tmp) / ("old" if old else "new" if near is None else "near")
    gen(d, old)
    x360 = (RUNTIME / "x360.m").read_text()
    settings = (RUNTIME / "settings.c").read_text()
    render = (RUNTIME / "render.m").read_text()
    assert x360.count(FIT_ANCHOR) == 1 and settings.count(GATE_ANCHOR) == 1 and render.count(NEW_FOG) == 1
    if old:
        for text, want in ((OLD_FIT, OLD_FIT_SHA), (OLD_GATE, OLD_GATE_SHA), (OLD_FOG, OLD_FOG_SHA)):
            assert sha(text) == want, (text, sha(text))
        x360 = x360.replace(FIT_ANCHOR, FIT_ANCHOR + OLD_FIT)
        settings = settings.replace(GATE_ANCHOR, OLD_GATE + GATE_ANCHOR)
        render = render.replace(NEW_FOG, OLD_FOG)
    assert x360.count("fit = 0;") == (1 if old else 0)
    # 1. the fit with the generated numbers, and x360.m's own override line (if any)
    fit_line = "\n".join(ln for ln in x360.splitlines() if "X360_ENGINE_CONSTS) fit = 0" in ln)
    (d / "fit.c").write_text("#define NEAR %s\n" % (near or "X360E_SHADOW_FOCUS_NEAR") + FIT_DRIVER % dict(fit_line=fit_line))
    # a copy beside this case's tables: a quoted include looks in the source's own folder first (runtime/gen)
    (d / "x360_shadow_fit.cpp").write_bytes((RUNTIME / "x360_shadow_fit.cpp").read_bytes())
    run(["clang++", "-std=c++17", "-w", "-c", "-I", str(d), str(d / "x360_shadow_fit.cpp"), "-o", str(d / "f.o")])
    run(["clang", "-w", "-I", str(d), "-c", str(d / "fit.c"), "-o", str(d / "d.o")])
    run(["clang++", str(d / "f.o"), str(d / "d.o"), "-o", str(d / "fit")])
    got = dict(ln.split() for ln in run([str(d / "fit")]).splitlines())
    # 2. the fog patch, taken from render.m by its text
    m = re.search(r"static uint32_t \*fog_patch\(const uint32_t \*t, uint32_t n\) \{\n.*?\n\}\n", render, re.S)
    (d / "fog.c").write_text(FOG_DRIVER % dict(fog=m.group(0)))
    run(["clang", "-w", "-I", str(d), "-I", str(RUNTIME), str(d / "fog.c"), "-o", str(d / "fog")])
    got.update(dict(ln.split() for ln in run([str(d / "fog")]).splitlines()))
    # 3. the row, in a public build with a PC-only manifest
    (d / "settings.c").write_text(settings)
    for f in ("roots.h", "bundle_roots.h"):
        (d / f).write_bytes((RUNTIME / f).read_bytes())
    add = '    static Setting row0 = {.id="%s", .type=SET_CHOICE, .choices=kXP, .values=kXPV, .nchoices=2}; settings_add(&row0);\n' % ROW
    show = '    printf("hidden %d\\n", row0.hidden || row0.locked);\n'
    (d / "driver.c").write_text(ppv.DRIVER % (add, show))
    run(["clang", "-DNFS_PUBLIC=1", "-w", "-I", str(d), "-I", str(RUNTIME), "-I", str(ROOT / "recomp"),
         str(d / "driver.c"), str(d / "settings.c"), "-o", str(d / "driver")])
    (d / "pc").mkdir(exist_ok=True)
    (d / "roots.txt").write_text("pc_root=%s\n" % (d / "pc"))
    env = dict(PATH=os.environ["PATH"], HOME=str(d / "home"), NFS_ROOTS=str(d / "roots.txt"))
    got.update(dict(ln.split() for ln in run([str(d / "driver")], env=env).splitlines()))
    return {k: float(v) for k, v in got.items()}


def main():
    with tempfile.TemporaryDirectory(prefix="shadow-fog-port-") as tmp:
        new, old, pcnear = case(tmp, False), case(tmp, True), case(tmp, False, near="0.01f")
    print("port: %s\ncontrol: %s\nPC near: share %.4f" % (new, old, pcnear["share"]))
    assert new["finite"] == 1 and new["inside"] == 8 and new["fit"] == 2 and new["size"] == 1024, new
    assert new["share"] > pcnear["share"] * 1.5, (new["share"], pcnear["share"])   # the near-car ground gets the rows
    assert new["hidden"] == 0 and new["clamp"] == 1e9, new
    assert old["size"] == 0 and old["inside"] == 0 and old["fit"] == 0 and old["hidden"] == 1 and old["clamp"] == 2500, old
    print("PASS shadow_fog_port_values")


if __name__ == "__main__":
    main()
