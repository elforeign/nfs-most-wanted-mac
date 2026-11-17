"""Wall and kerb depth and the X360 particle shading/size without the console files: both run on this port's own values,
on by default (the X360 particle choice and the X360 wall depth). No game launch and no game bytes; the generator's no-console output
and production fragments of materials.m and settings.c are compiled.

Fixed: the parallax patch is applied (6 worldnormalmap PS), the walls' offset at full height on a 60 degree view is
0.0173 texture units and the self-shadow ramp reaches 1 at a sun 7.2 degrees above the surface; the row is listed with
default X360; the X360 choice turns the parallax on, the particle X360 branch on (lit 1.00, shadowed 0.50 of the PC's lit
output) and the size clamp at 1.0; Retail/Physical/PC keep the PC branch; unset takes the X360 one.
Control (the pinned state before the change): patch off, offset 0, row hidden, and every particle choice gives the PC
branch (the X360 choice too) with shading 0 and the clamp at 0.
Run: tools/.venv/bin/python -I runtime/parallax_particles_port_values_test.py"""
import hashlib
import math
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
import x360_shader_lits as lits  # noqa: E402
import post_port_values_test as ppv  # noqa: E402  (its settings driver)

ROW = "graphics.parallax"
# The state before the change, pinned (the control).
OLD_PAR = ('static int parallax_on(void) { return X360_SHADER_LITS; }\n'
           'static int parallax_mode(void) { if (parallax_live < 0) { const char *e = getenv("NFS_PARALLAX"); parallax_live = !(e && !strcmp(e, "pc")); } return parallax_live; }\n')
OLD_PAR_SHA = "471070197e3ddcd2134dc570ab189fb55ed6616104a7f3ebe233bbe1c1453f4d"
OLD_PART = ('static int particles_on(void) {\n'
            '    if (particles_x360 < 0) { const char *e = getenv("NFS_PARTICLES"); particles_x360 = !(e && !strcmp(e, "pc")); }\n'
            '    return particles_x360;\n}\n'
            'void materials_set_particles(int x360) {   // the F10 menu: live (the shader reads the mode per draw, the clamp per particle)\n'
            '    particles_x360 = x360 != 0;\n'
            '    if (diag_on) diag_event("materials: particles now %s", particles_x360 ? "x360" : "pc");\n}\n')
OLD_PART_SHA = "a00104811125df305460a52f6e4edf27169449518ab5f4e8931297f1c81ec133"
# the per-draw branch select (particle_constants): X360 branch when this expression is true
OLD_K = "    o->k[0][0] = X360_SHADER_LITS && particles_on() ? 1.0f : 0.0f;   // no X360 shader constants: the PC output\n"
OLD_K_SHA = "47a212dffe37e640369f87ab6d5d74d610531435bb815201e06e10b2f29a33fa"
OLD_GATE = '    {"graphics.parallax", "shaders"}, {"graphics.x360_arrest", "shaders"},\n'
OLD_GATE_SHA = "0e57f35566f7b740419491d6375017777d3a1264bc43678a39d108fd376a19b0"
OLD_SCALARS = ("X360S_PARALLAX_SCALE2", "X360S_PARALLAX_BIAS", "X360S_PARALLAX_SELFSHADOW", "X360S_PARTICLE_HALF")

DRIVER = r'''
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "gen/x360_shader_lits.inc"
static int diag_on;
static int roots_x360_has(const char *f) { (void)f; return 0; }   // no console copy
static void diag_event(const char *f, ...) { (void)f; }
static int postfx_look_variant(const char *v) { (void)v; return 0; }
static int particles_x360 = -1;
%(code)s
static float num(const char *s) { return strtof(s, NULL); }
int main(int argc, char **argv) {
    const char *choice = argc > 1 ? argv[1] : "";
    if (!strcmp(choice, "x360") || !strcmp(choice, "pc")) setenv("NFS_PARALLAX", choice, 1);   /* the row has only these */
    if (*choice) setenv("NFS_PARTICLES", choice, 1);
    /* the parallax sample offset at full height (h = 1), eye 60 degrees from the normal: (S h - B) |E.xy| */
    float off = (num(X360S_PARALLAX_SCALE2) - num(X360S_PARALLAX_BIAS)) * sinf(60.0f * 3.14159265f / 180.0f);
    float k = num(X360S_PARALLAX_SELFSHADOW);
    int on = parallax_on(), mode = parallax_mode();
    int style = branch();
    float h = num(X360S_PARTICLE_HALF);
    printf("patch %%d\nparallax %%d\noffset %%.4f\nramp_deg %%.2f\n", on, mode, on && mode ? off : 0.0f,
           k > 0 ? asinf(1.0f / k) * 180.0f / 3.14159265f : 90.0f);
    printf("x360branch %%d\nlit %%.3f\nshadowed %%.3f\nmaxd %%.3f\n", style, style ? 2 * h : 1.0f, style ? h : 0.5f,
           style ? (float)X360F_PARTICLE_MAXD : 0.0f);
    return 0;
}
'''


def sha(s):
    return hashlib.sha256(s.encode()).hexdigest()


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, check=True, **kw).stdout


def fragment(src, start, end):
    i = src.index(start)
    return src[i:src.index(end, i) + len(end)]


def case(tmp, old):
    d = Path(tmp) / ("old" if old else "new")
    (d / "gen").mkdir(parents=True)
    shader = lits.text(None, "test")
    if old:
        for name in OLD_SCALARS:
            shader, n = re.subn(r'^#define %s "[^"]*"$' % name, '#define %s "0.0"' % name, shader, flags=re.M)
            assert n == 1, name
        shader, n = re.subn(r"^#define X360F_PARTICLE_MAXD \S+$", "#define X360F_PARTICLE_MAXD 0.0f", shader, flags=re.M)
        assert n == 1
    (d / "gen/x360_shader_lits.inc").write_text(shader)
    mat = (RUNTIME / "materials.m").read_text()
    par = fragment(mat, "static int parallax_live = -1;\n", "    return parallax_live && !(diag_on && postfx_look_variant(\"parallaxpc\"));\n}\n")
    part = fragment(mat, "static int particles_on(void) {\n", "\n}\n")
    if "static int particles_style(void)" in mat:
        part += fragment(mat, "static int particles_style(void)", "\n")
    part += fragment(mat, "void materials_set_particles(int x360) {", "\n}\n")
    pc = mat[mat.index("static void particle_constants("):]
    k = fragment(pc, "    o->k[0][0] = ", "\n")
    settings = (RUNTIME / "settings.c").read_text()
    anchor = '    {"graphics.x360_arrest", "shaders"},\n'
    assert settings.count(anchor) == 1 and mat.count("PARALLAX_PS(\"worldnormalmap t") == 6
    if old:
        for text, want in ((OLD_PAR, OLD_PAR_SHA), (OLD_PART, OLD_PART_SHA), (OLD_K, OLD_K_SHA), (OLD_GATE, OLD_GATE_SHA)):
            assert sha(text) == want, (text, sha(text))
        par, part, k = "static int parallax_live = -1;\n" + OLD_PAR, OLD_PART, OLD_K
        settings = settings.replace(anchor, OLD_GATE)
    # the branch the shader takes, from particle_constants' own line
    sel = "static int branch(void) { return %s; }\n" % re.match(r"    o->k\[0\]\[0\] = (.*) \? 1\.0f : 0\.0f;", k).group(1)
    (d / "m.c").write_text(DRIVER % dict(code=par + part + sel))
    run(["clang", "-w", "-I", str(d), str(d / "m.c"), "-o", str(d / "m")])
    got = {}
    for choice in ("", "x360", "physical", "retail", "pc"):
        env = dict(PATH=os.environ["PATH"])
        got[choice or "unset"] = {k: float(v) for k, v in (ln.split() for ln in run([str(d / "m"), choice], env=env).splitlines())}
    # the row, in a public build with a PC-only manifest
    (d / "settings.c").write_text(settings)
    for f in ("roots.h", "bundle_roots.h"):
        (d / f).write_bytes((RUNTIME / f).read_bytes())
    add = '    static Setting row0 = {.id="%s", .type=SET_CHOICE, .choices=kXP, .values=kXPV, .nchoices=2, .env="NFS_PARALLAX"}; settings_add(&row0);\n' % ROW
    show = '    printf("hidden %d\\ndefault %d\\n", row0.hidden || row0.locked, row0.value);\n'
    (d / "driver.c").write_text(ppv.DRIVER % (add, show))
    run(["clang", "-DNFS_PUBLIC=1", "-w", "-I", str(d), "-I", str(RUNTIME), "-I", str(ROOT / "recomp"),
         str(d / "driver.c"), str(d / "settings.c"), "-o", str(d / "driver")])
    (d / "pc").mkdir()
    (d / "roots.txt").write_text("pc_root=%s\n" % (d / "pc"))
    env = dict(PATH=os.environ["PATH"], HOME=str(d / "home"), NFS_ROOTS=str(d / "roots.txt"))
    got["row"] = {k: float(v) for k, v in (ln.split() for ln in run([str(d / "driver")], env=env).splitlines())}
    return got


def main():
    if not (ROOT / "tools_snapshot/x360_shader_lits.py").exists():
        print("SKIP: no generator")
        return 3
    with tempfile.TemporaryDirectory(prefix="parallax-particles-port-") as tmp:
        new, old = case(tmp, False), case(tmp, True)
    for name, got in (("port", new), ("control", old)):
        print("%s: %s" % (name, " | ".join("%s %s" % (c, v) for c, v in got.items())))
    x, u = new["x360"], new["unset"]
    # the X360 choice: parallax on with a visible offset and a steep self-shadow ramp; particles on the X360 branch
    assert x["patch"] == 1 and x["parallax"] == 1 and 0.01 < x["offset"] < 0.05 and 0 < x["ramp_deg"] < 30, x
    assert x["x360branch"] == 1 and x["lit"] == 1.0 and x["shadowed"] == 0.5 and x["maxd"] == 1.0, x
    # unset (the default without console files): both looks on; Retail, Physical and PC keep the PC look
    assert u["patch"] == 1 and u["parallax"] == 1 and u["offset"] == x["offset"] and u["x360branch"] == 1, u
    assert all(new[c]["x360branch"] == 0 for c in ("physical", "retail", "pc")), new
    assert new["row"] == {"hidden": 0, "default": 0}, new["row"]
    # control: nothing runs, the row is hidden
    assert all(old[c]["patch"] == 0 and old[c]["x360branch"] == 0 for c in ("unset", "x360", "physical", "pc")), old
    assert old["x360"]["maxd"] == 0 and old["row"]["hidden"] == 1, old
    print("PASS parallax_particles_port_values")
    return 0


if __name__ == "__main__":
    sys.exit(main())
