"""The post chain without the console files: it runs on this port's own constants and its menu rows are listed.
No game launch and no game bytes; the generators' no-console output and three production fragments are compiled.

Fixed: 26/26 chain constants resolve to finite, non-zero port values, exposure x1.00 at the neutral scene and x3.96 at
the floor, the chain is on, 4/4 chain rows are listed (the lens drops row is listed too: no row stays hidden).
Control (the pinned state before the change): 26/26 constants are zero, the exposure is not a finite number, the chain
is off and 0/4 rows are listed.
With an executable the table still comes from it: a synthetic image of 1.5s gives 1.5 for 20/20 post constants.
Run: tools/.venv/bin/python -I runtime/post_port_values_test.py"""
import hashlib
import math
import os
from pathlib import Path
import re
import runpy
import subprocess
import sys
import tempfile

RUNTIME = Path(__file__).resolve().parent
ROOT = RUNTIME.parent
sys.path.insert(0, str(ROOT / "tools_snapshot"))
import pubtables_gen as pg  # noqa: E402
import x360_shader_lits as lits  # noqa: E402

ROWS = ["graphics.postfx", "graphics.tone_curve", "graphics.speed_blur", "graphics.curve_glow"]
STILL_HIDDEN = []      # rows that still need the console's textures / shader constants (none: drops and flake have port versions)

# The state before the change, pinned (the control): the nine gate entries of runtime/settings.c, the chain's switch in
# runtime/x360_post.m and the stubs' zeros.
OLD_GATE = ('    {"graphics.postfx", "shaders"}, {"graphics.tone_curve", "shaders"}, {"graphics.speed_blur", "shaders"},\n'
            '    {"graphics.curve_glow", "shaders"},\n'
            '    {"graphics.postfx", "engine"}, {"graphics.tone_curve", "engine"}, {"graphics.speed_blur", "engine"},\n'
            '    {"graphics.curve_glow", "engine"},\n')
OLD_GATE_SHA = "d22cd972b28498913b6c314c25d4e6447cf773ded7f4f01ff8fcfa73480b89d9"
OLD_CHAIN = "postfx = x360 && !postfx_ab() && X360_SHADER_LITS && X360_ENGINE_CONSTS;"
OLD_CHAIN_SHA = "dba624cbb6ac6dd92063b88e6aca650b18246bed053672c7601dcc57968f2607"
NEW_CHAIN = "postfx = x360 && !postfx_ab();"
NEW_GATE_NOTE = "    // (the post chain's rows, Post-processing, Tone curve, Speed blur and Tone curve glow, need neither"

DRIVER = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "settings.h"
int diag_on = 0;
const char *game_dir;
const char *app_pref(const char *key) { (void)key; return NULL; }
void app_pref_set(const char *key, const char *value) { (void)key; (void)value; }
void app_pref_batch_begin(void) {}
void app_pref_batch_end(void) {}
void report_note(const char *fmt, ...) { (void)fmt; }
void diag_event(const char *fmt, ...) { (void)fmt; }
static const char *const kXP[] = {"X360", "PC"}, *const kXPV[] = {"x360", "pc"};
int main(void) {
%s
    settings_init();
%s
    return 0;
}
'''

# The chain's own lines, taken from runtime/x360_post.m by their text and run with the generated constants.
CHAIN = r'''
#include <math.h>
#include <stdio.h>
#include "gen/x360_shader_lits.inc"
#include "gen/x360_engine.inc"
static int postfx = -1, diag_on;
static int postfx_ab(void) { return 0; }
static void diag_event(const char *fmt, ...) { (void)fmt; }
%(set_chain)s
static float step(float adapted, float measured, float dt30) {
    float d = measured - adapted;
    if (0) ;
%(adapt)s
    return adapted;
}
int main(void) {
    struct { float middle_gray, exposure; } p;
    float pulse = 0, adapted;
%(values)s
    p.middle_gray = X360E_MIDDLE_GRAY;
    adapted = 2.0f * p.middle_gray;
%(exposure)s
    printf("neutral %%.9g\n", p.exposure);
    adapted = X360E_ADAPT_FLOOR;
%(exposure)s
    printf("floor %%.9g\n", p.exposure);
    printf("brighter %%.9g\n", step(0.2f, 0.4f, 1.0f));
    printf("darker %%.9g\n", step(0.4f, 0.2f, 1.0f));
    printf("below %%.9g\n", step(0.0f, 0.4f, 1.0f));
    printf("weights %%.9g\n", 2 * atof(X360S_VT_W0) + 3 * atof(X360S_VT_W1) + 2 * atof(X360S_VT_W2) + atof(X360S_VT_W3));
    x360_post_set_chain(1);
    printf("chain %%d\n", postfx);
    return 0;
}
'''


def chain_macros():
    """The constants the chain's two sources use, read from those sources (not from the generators' tables)."""
    names = set()
    for f in ("x360_post.m", "render_post.m"):
        names |= set(re.findall(r"\bX360(?:E_[A-Z0-9_]+|S_VT_[A-Z0-9_]+)\b", (RUNTIME / f).read_text()))
    # prefixes in comments end in '_'; the lens drops' two constants belong to the console's drop textures
    return sorted(n for n in names if not n.endswith("_") and not n.startswith("X360E_DROP_"))


def line_with(text, needle):
    hits = [ln for ln in text.splitlines() if needle in ln]
    assert len(hits) >= 1, needle
    return hits[0]


def build_gen(d, old):
    """The no-console tables as the generators write them (old: every chain constant zero, as before the change)."""
    (d / "gen").mkdir(parents=True, exist_ok=True)
    engine, shader = pg.ENGINE_STUB, lits.text(None, "test")
    if old:
        for name in pg.POST_FALLBACK:
            engine, n = re.subn(r"^#define %s \S+$" % name, "#define %s 0.0f" % name, engine, flags=re.M)
            assert n == 1, name
        for name in lits.PORT_SCALARS:
            shader, n = re.subn(r'^#define %s "\S+"$' % name, '#define %s "0.0"' % name, shader, flags=re.M)
            assert n == 1, name
    (d / "gen/x360_engine.inc").write_text(engine)
    (d / "gen/x360_shader_lits.inc").write_text(shader)


def run_case(tmp, old, drops_gate=False):
    d = Path(tmp) / ("drops-gate" if drops_gate else "old" if old else "new")
    build_gen(d, old)
    post = (RUNTIME / "x360_post.m").read_text()
    settings = (RUNTIME / "settings.c").read_text()
    if old:
        assert hashlib.sha256(OLD_GATE.encode()).hexdigest() == OLD_GATE_SHA
        assert hashlib.sha256(OLD_CHAIN.encode()).hexdigest() == OLD_CHAIN_SHA
        assert post.count(NEW_CHAIN) == 1 and settings.count(NEW_GATE_NOTE) == 1
        post = post.replace(NEW_CHAIN, OLD_CHAIN)
        settings = settings.replace(NEW_GATE_NOTE, OLD_GATE + NEW_GATE_NOTE)
    if drops_gate:
        settings = settings.replace("kNeedsX360[] = {", 'kNeedsX360[] = {\n    {"graphics.raindrops", "world"}, {"graphics.raindrops", "engine"},')
    # 1. the constants and the chain's own arithmetic
    names = chain_macros()
    values = "".join('    printf("%s %%.9g\\n", (double)%s);\n' % (n, "atof(%s)" % n if n.startswith("X360S_") else n) for n in names)
    adapt = "\n".join(line_with(post, needle) for needle in
                      ("else if (adapted < X360E_ADAPT_FLOOR)", "else adapted += (d > 0 ? X360E_ADAPT_UP"))
    src = CHAIN % dict(set_chain=line_with(post, "void x360_post_set_chain(int x360)"), adapt=adapt, values=values,
                       exposure=line_with(post, "p.exposure = 2.0f * p.middle_gray / (adapted + X360E_EXPOSURE_EPS)"))
    (d / "chain.c").write_text("#include <stdlib.h>\n" + src)
    subprocess.run(["clang", "-w", "-I", str(d), "-I", str(RUNTIME), str(d / "chain.c"), "-o", str(d / "chain")], check=True)
    out = dict(ln.split() for ln in subprocess.run([str(d / "chain")], capture_output=True, text=True, check=True).stdout.splitlines())
    got = {k: float(v) for k, v in out.items()}
    # 2. the rows, in a public build with a PC-only manifest
    (d / "settings.c").write_text(settings)
    for f in ("roots.h", "bundle_roots.h"):
        (d / f).write_bytes((RUNTIME / f).read_bytes())
    rows = ROWS + ["graphics.raindrops"] + STILL_HIDDEN
    add = "".join('    static Setting row%d = {.id="%s", .type=SET_CHOICE, .choices=kXP, .values=kXPV, .nchoices=2}; settings_add(&row%d);\n'
                  % (i, n, i) for i, n in enumerate(rows))
    show = "".join('    printf("%s %%d\\n", row%d.hidden || row%d.locked);\n' % (n, i, i) for i, n in enumerate(rows))
    (d / "driver.c").write_text(DRIVER % (add, show))
    subprocess.run(["clang", "-DNFS_PUBLIC=1", "-w", "-I", str(d), "-I", str(RUNTIME), "-I", str(ROOT / "recomp"),
                    str(d / "driver.c"), str(d / "settings.c"), "-o", str(d / "driver")], check=True)
    (d / "pc").mkdir(exist_ok=True)
    (d / "roots.txt").write_text("pc_root=%s\n" % (d / "pc"))
    env = dict(PATH=os.environ["PATH"], HOME=str(d / "home"), NFS_ROOTS=str(d / "roots.txt"))
    hidden = dict(ln.split() for ln in subprocess.run([str(d / "driver")], env=env, capture_output=True, text=True, check=True).stdout.splitlines())
    return names, got, {k: int(v) for k, v in hidden.items()}


def with_executable():
    """An executable present: every post constant is the file's, never the fallback."""
    engine = runpy.run_path(str(ROOT / "tools_snapshot/test_x360_engine.py"))
    macros = engine["macros"](pg.engine_text(bytes(engine["synthetic_image"]()), "synthetic"))
    assert all(macros[name] == "1.5f" for name in pg.POST_FALLBACK), {n: macros[n] for n in pg.POST_FALLBACK}
    assert "#define X360_ENGINE_CONSTS 1" in pg.engine_text(bytes(engine["synthetic_image"]()), "synthetic")
    return len(pg.POST_FALLBACK)


def main():
    with tempfile.TemporaryDirectory(prefix="post-port-") as tmp:
        names, got, hidden = run_case(tmp, old=False)
        assert len(names) == 26, names
        live = [n for n in names if math.isfinite(got[n]) and got[n] > 0]
        assert live == names, sorted(set(names) - set(live))
        assert abs(got["neutral"] - 1.0) < 0.01, got["neutral"]                  # a scene at 2 x middle gray: the PC's exposure
        assert 3.5 < got["floor"] <= 4.0, got["floor"]                           # never more than two stops
        assert 0.2 < got["brighter"] < 0.4 and 0.2 < got["darker"] < 0.4         # both directions step toward the scene
        assert got["brighter"] - 0.2 > 0.4 - got["darker"] > 0                   # and toward light faster than toward dark
        assert abs(got["below"] - got["X360E_ADAPT_FLOOR"]) < 1e-6               # the floor holds
        assert abs(got["weights"] - 1.0) < 1e-6, got["weights"]                  # the blur neither darkens nor brightens
        assert got["X360E_BLUR_SPEED_MIN"] < got["X360E_BLUR_SPEED_FULL"]
        full = (got["X360E_BLUR_SPEED_FULL"] - got["X360E_BLUR_SPEED_MIN"]) * got["X360E_BLUR_RAMP"]
        assert abs(full - got["X360E_BLUR_MAX"]) < 1e-4, full                    # the ramp reaches the cap at the full speed
        assert got["chain"] == 1
        assert [hidden[r] for r in ROWS] == [0, 0, 0, 0], hidden
        assert [hidden[r] for r in STILL_HIDDEN] == [], hidden
        assert hidden["graphics.raindrops"] == 0, hidden
        print("PASS: no console files: %d/%d chain constants are port values, exposure x%.2f neutral / x%.2f at the floor, "
              "chain on, %d/%d rows listed" % (len(live), len(names), got["neutral"], got["floor"], 4 - sum(hidden[r] for r in ROWS), len(ROWS)))

        _, _, broken_hidden = run_case(tmp, old=False, drops_gate=True)
        assert broken_hidden["graphics.raindrops"] == 1
        print("PASS: lens-drop row listed without console files; restored world/engine gate rejects it (1 availability failure)")

        names, got, hidden = run_case(tmp, old=True)
        zero = [n for n in names if got[n] == 0]
        assert len(zero) == 26, sorted(set(names) - set(zero))
        assert not math.isfinite(got["neutral"]) or got["neutral"] == 0, got["neutral"]
        assert got["chain"] == 0
        assert [hidden[r] for r in ROWS] == [1, 1, 1, 1], hidden
        print("PASS: control, the state before the change: %d/%d constants zero, exposure %s, chain off, %d/%d rows listed"
              % (len(zero), len(names), got["neutral"], 4 - sum(hidden[r] for r in ROWS), len(ROWS)))
    print("PASS: with an executable the table is the file's: %d/%d post constants read from a synthetic image" %
          (with_executable(), len(pg.POST_FALLBACK)))


if __name__ == "__main__":
    main()
