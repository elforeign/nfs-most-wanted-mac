"""Offline test: with a roots.txt that has only pc_root (no X360 copy) the Road reflection and Control type rows are
listed (not hidden, not locked) and keep their own values, because neither reads console data (road reflection is a
render-target size, the control presets are authored in controltype_buttons.inc). Uses runtime/settings.c as built and
checks the outputs the player gets: the reflection target size from x360.m's own sizing code (x360_reflection_dimensions,
3440x2160 view: 1720x1080 wanted, 320x240 is the PC size) and Control Type 3's gas binding from controltype.c's own
ct_map (a non-PC control wanted, none = the port's bindings).
Control: the same test on settings.c with the two old gate lines put back must fail (rows hidden, 320x240, Type 1).
Numbers: new code 0 failures; old gate lines 6 failures (2 rows hidden, env forced to pc / 1, reflection 320x240, gas binding none).
MW retail presets preserve the enabled catalog: old 0/2, fixed 2/2.
PC-only lens default and reset match authored optics: old 0/2, fixed 2/2.
Run: tools/.venv/bin/python -I runtime/settings_pconly_rows_test.py   (exit 3 when clang is missing)"""
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

RUNTIME = Path(__file__).resolve().parent
C = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define CONTROLTYPE_TEST 1
#include "controltype.c"
#include "settings.h"
void graphics_reset_request(void) {}
@@REFL@@
int diag_on = 0;
const char *game_dir;
static char car_pref[8]="on";
const char *app_pref(const char *key) { return !strcmp(key,"cars.later_games") ? car_pref : NULL; }
void app_pref_set(const char *key, const char *value) {
    if (!strcmp(key,"cars.later_games")) snprintf(car_pref,sizeof car_pref,"%s",value);
}
void app_pref_batch_begin(void) {}
void app_pref_batch_end(void) {}
void report_note(const char *fmt, ...) { (void)fmt; }
void diag_event(const char *fmt, ...) { (void)fmt; }
static const char *const kRefl[] = {"X360 (half screen)", "PC (320x240)"}, *const kReflV[] = {"x360", "pc"};
static Setting s_refl = {.id = "graphics.road_reflection", .category = SET_WORLD, .order = 90, .label = "Road reflection",
    .type = SET_CHOICE, .choices = kRefl, .values = kReflV, .nchoices = 2, .env = "NFS_ROAD_REFLECTION"};
static const char *const kType[] = {"1","2","3","4","5","6","7","8","9","10"}, *const kTypeV[] = {"1","2","3","4","5","6","7","8","9","10"};
static Setting s_type = {.id = "controls.control_type", .category = SET_CONTROLS, .order = 105, .label = "Control type",
    .type = SET_CHOICE, .choices = kType, .values = kTypeV, .nchoices = 10, .def = 0, .env = "NFS_CONTROL_TYPE"};
static void x360_post_set_raindrops(int mode) { (void)mode; }
@@DROPS@@
@@LATER@@
@@MODE@@
int main(int argc,char **argv) {
    settings_add(&s_refl); settings_add(&s_type);
    settings_add(&s_drops); settings_add(&s_later_cars);
    settings_init();
    int bad = 0;
    if (argc==2 && !strcmp(argv[1],"presets")) {
        const int presets[]={PRESET_PC,PRESET_X360};
        for (int i=0;i<2;++i) {
            settings_set(&s_later_cars,1);
            settings_look_apply(presets[i]);
            bad += s_later_cars.value!=1 || strcmp(car_pref,"on");
        }
        printf("catalog preserved=%d/2 failures %d\n",2-bad,bad);
        return bad!=0;
    }
    if (argc==2 && !strcmp(argv[1],"lens")) {
        int boot_mode=lens_drops_mode(getenv(s_drops.env));
        bad += s_drops.value!=0 || s_drops.boot_value!=0 || boot_mode!=2;
        settings_set(&s_drops,2); settings_reset(&s_drops);
        bad += s_drops.value!=0;
        printf("lens authored default/reset=%d/2 failures %d\n",2-bad,bad);
        return bad!=0;
    }
    Setting *rows[2] = {&s_refl, &s_type};
    for (int i = 0; i < 2; ++i) {
        const char *e = getenv(rows[i]->env);
        int hidden = rows[i]->hidden;   /* an env value locks a row: not the gate */
        printf("%s hidden=%d env=%s\n", rows[i]->id, hidden, e ? e : "(unset)");
        bad += hidden;
        if (e && (!strcmp(e, "pc") || (i == 1 && !strcmp(e, "1")))) ++bad;   // the gate's forced PC / Type 1 value
    }
    uint32_t rw, rh;
    x360_reflection_dimensions(3440, 2160, &rw, &rh);
    float low;
    const char *gas = ct_map(settings_get("controls.control_type") + 1, CT_GAS, &low);
    printf("reflection target %ux%u, control type %d gas binding %s\n", rw, rh, settings_get("controls.control_type") + 1, gas ? gas : "(port bindings)");
    if (rw != 1720 || rh != 1080) ++bad;
    if (!gas) ++bad;
    printf("failures %d\n", bad);
    return bad != 0;
}
'''
OLD_LINES = ('    {"controls.control_type", "world"},\n    {"graphics.road_reflection", "world"},\n')
OLD_CATALOG = '    {"cars.later_games", "off", "off"},\n'
OLD_LENS = '''    // Generated optics remain opt-in; preserve the PC default without console art.
    if (!strcmp(s->id, "graphics.raindrops") && !roots_x360_has("world")) {
        s->def = 1;
        if (!s->saved && !s->locked) s->value = s->boot_value = s->def;
    }
'''
assert hashlib.sha256(OLD_CATALOG.encode()).hexdigest() == '40431f6f62339812cd977b863f866725ebcdc0184870684283ae183bec231d54'
assert hashlib.sha256(OLD_LENS.encode()).hexdigest() == '3c07a3cd5f6e016ae595efe6c2834eeee6dcf36b19d2e23d17c6e149bb23a6da'


def refl_fragment():
    """x360.m's reflection sizing code, cut out whole (no copy to drift): from refl_x360 to the creation hook."""
    src = (RUNTIME / "x360.m").read_text()
    a = src.index("static int refl_x360 = -1;")
    b = src.index("void f_006BCDC0_orig(Cpu *c);")
    return "#include <stdint.h>\n" + src[a:b]


def build_and_run(d, settings_src, tag, case=()):
    d = Path(d)
    rows = (RUNTIME/'settings_port.c').read_text()
    drops = rows[rows.index('static const char *const kDrops[]'):rows.index('static const char *const kSpeedBlur[]')]
    later = rows[rows.index('static Setting s_later_cars'):rows.index('static Setting s_traffic_pilot')].split('\n#if !defined')[0]
    drops_source = (RUNTIME/'lens_drops.c').read_text()
    mode = drops_source[drops_source.index('int lens_drops_mode('):drops_source.index('int lens_drops_provider(')]
    (d / f"{tag}.c").write_text(C.replace("@@REFL@@", refl_fragment()).replace('@@DROPS@@',drops).replace('@@LATER@@',later).replace('@@MODE@@',mode))
    sc = d / f"{tag}_settings.c"
    sc.write_text(settings_src)
    exe = d / tag
    subprocess.run(["clang", "-std=c11", "-g", "-I", str(RUNTIME), "-I", str(RUNTIME.parent / "recomp"), "-I", str(RUNTIME / "gen"), str(d / f"{tag}.c"),
                    str(sc), "-o", str(exe)], check=True)
    roots = d / "roots.txt"
    roots.write_text("pc_root=/nonexistent/pc\n")
    env = {"PATH": os.environ["PATH"], "HOME": str(d), "NFS_ROOTS": str(roots), "NFS_CONTROL_TYPE": "3"}
    r = subprocess.run([str(exe), *case], env=env, capture_output=True, text=True)
    sys.stdout.write(f"[{tag}]\n{r.stdout}{r.stderr}")
    return int(r.stdout.rsplit("failures", 1)[1].split()[0])


def main():
    if not shutil.which("clang"):
        print("SKIP: no clang")
        return 3
    if not (RUNTIME / "gen" / "controltype_buttons.inc").exists():
        print("SKIP: runtime/gen/controltype_buttons.inc not generated")
        return 3
    src = (RUNTIME / "settings.c").read_text()
    assert OLD_LINES not in src
    marker = '    {"graphics.look_data", "look"},\n'
    assert marker in src
    old = src.replace(marker, OLD_LINES + marker)
    with tempfile.TemporaryDirectory(prefix="pconly-rows-") as d:
        new_bad = build_and_run(d, src, "new")
        old_bad = build_and_run(d, old, "old")
        for case, old_src in [('presets',src.replace('    {"cars.traffic_pilot",',OLD_CATALOG+'    {"cars.traffic_pilot",',1)),
                              ('lens',src.replace('    if (!x360_unavailable(s))',OLD_LENS+'    if (!x360_unavailable(s))',1))]:
            assert old_src!=src
            assert build_and_run(d,src,case+'-new',(case,))==0
            assert build_and_run(d,old_src,case+'-old',(case,))==2
    print(f"new gate: {new_bad} failures (want 0); old gate lines back: {old_bad} failures (want > 0)")
    ok = new_bad == 0 and old_bad > 0
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
