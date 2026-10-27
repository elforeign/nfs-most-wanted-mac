"""








"""
from pathlib import Path
import os
import math
import sys
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent



# Synthetic sun/ambient records (not the game's): sun = brightness * (1, .55 + .35 c, .1 + .8 c) for a coolness c,
# ambient = (.15, .2, .3) + t (.1, .1, .15) for an ambient level t; eight (brightness, c, t) triples spanning noon,
# dusk, night and overcast. Live ambient W=4, k=1; RGB is treated as linear Rec.709/D65, before fog/specular/post.
def _synthetic(name, v, c, t):
    sun = tuple(round(x, 4) for x in (v, v * (.55 + .35 * c), v * (.1 + .8 * c)))
    amb = tuple(round(x, 4) for x in (.15 + .1 * t, .2 + .1 * t, .3 + .15 * t))
    return ("synthetic", name, sun, amb)


TOD_RECORDS = tuple(_synthetic("S%d" % (i + 1), v, c, t) for i, (v, c, t) in enumerate((
    (1, 0, 0), (.8, .2, .8), (.25, .9, .3), (.6, .5, .6), (.65, .3, .1), (.75, .1, .2), (.06, .6, .4), (.4, .4, .5))))
Y_WEIGHTS = (.2126, .7152, .0722)


def luminance(rgb):
    return sum(v * w for v, w in zip(rgb, Y_WEIGHTS))


def rgb_xyz(rgb):
    return tuple(sum(v * w for v, w in zip(rgb, row)) for row in (
        (.4124564, .3575761, .1804375),
        (.2126729, .7151522, .0721750),
        (.0193339, .1191920, .9503041)))


def xyz_uv(xyz):
    x, y, z = xyz
    d = x + 15 * y + 3 * z
    return 4 * x / d, 6 * y / d  # CIE 1960 UCS for CCT/Duv


def planck_uv(t):
    # Standard cubic xy approximation to the Planckian locus, 1667..25000 K.
    if t <= 4000:
        x = -.2661239e9 / t**3 - .2343589e6 / t**2 + .8776956e3 / t + .179910
    else:
        x = -3.0258469e9 / t**3 + 2.1070379e6 / t**2 + .2226347e3 / t + .240390
    if t <= 2222:
        y = -1.1063814 * x**3 - 1.34811020 * x**2 + 2.18555832 * x - .20219683
    elif t <= 4000:
        y = -.9549476 * x**3 - 1.37418593 * x**2 + 2.09137015 * x - .16748867
    else:
        y = 3.0817580 * x**3 - 5.87338670 * x**2 + 3.75112997 * x - .37001483
    return xyz_uv((x / y, 1, (1 - x - y) / y))


def cct_duv(rgb):
    u, v = xyz_uv(rgb_xyz(rgb))
    def distance(t):
        pu, pv = planck_uv(t)
        return (pu - u)**2 + (pv - v)**2
    lo, hi = 1667., 25000.
    for _ in range(80):
        a, b = lo + (hi - lo) / 3, hi - (hi - lo) / 3
        if distance(a) < distance(b): hi = b
        else: lo = a
    t = (lo + hi) / 2
    pu, pv = planck_uv(t)
    au, av = planck_uv(t - 1)
    bu, bv = planck_uv(t + 1)
    # Positive above the Planckian locus; Duv is distance in CIE 1960 uv.
    sign = 1 if (bu - au) * (v - pv) - (bv - av) * (u - pu) < 0 else -1
    return t, sign * math.sqrt(distance(t))


def old_neutral(sun, ambient, elevation, percent=0):
    nl = math.sin(math.radians(elevation))
    ambient = [min(1., a) for a in ambient]
    ay = luminance(ambient)
    shade = [(a + percent / 100 * (ay - a)) * 4 * .27 for a in ambient]
    original = [min(1., s) * nl + a for s, a in zip(sun, shade)]
    y = luminance(original)
    lit = [a + min(1., max(0., y - a) / nl) * nl for a in shade]
    return lit, shade



def before_review():
    print("| Data / record | Elevation | Original lit K / Duv | Before Neutral lit K / Duv | Shade, both K / Duv | Before lit Y error |")
    print("|---|---:|---:|---:|---:|---:|")
    worst = 0
    for side, key, sun, amb in TOD_RECORDS:
        shade = [a * 4 * .27 for a in amb]
        for elevation in (5, 15, 30, 62):
            nl = math.sin(math.radians(elevation))
            original = [a + s * nl for a, s in zip(shade, sun)]
            lit, _ = old_neutral(sun, amb, elevation)
            delta = 100 * (luminance(lit) / luminance(original) - 1)
            worst = max(worst, abs(delta))
            cells = ["%.0f / %+.5f" % cct_duv(c) for c in (original, lit, shade)]
            print(f"| {side} {key} | {elevation} | " + " | ".join(cells) + f" | {delta:+.3f}% |")
    print(f"\nMaximum before Neutral luminance error: {worst:.3f}%.")



def sun_white(elevation):
                                                                              
    # a universal atmospheric fit. Kang 2002 Planckian xy + 15% adaptation.
    knots = ((5, 3500), (15, 4500), (30, 5300), (62, 5800), (90, 5800))
    t = 3500.
    for (a, ta), (b, tb) in zip(knots, knots[1:]):
        if elevation >= a:
            t = ta + (tb-ta)*min(1., (elevation-a)/(b-a))
    u, v = planck_uv(t)
    x = 3*u/(2*v)
    z = (6/v-x-15)/3
    xyz = tuple(.85*a+.15*b for a,b in zip((x,1.,z),(.95047,1.,1.08883)))
    rgb = tuple(sum(a*b for a,b in zip(xyz,row)) for row in (
        (3.2404542,-1.5371385,-.4985314),
        (-.9692660,1.8760108,.0415560),
        (.0556434,-.2040259,1.0572252)))
    return t, tuple(a/luminance(rgb) for a in rgb)


def check_targets(rows, review=False):
    failures = []
    worst = 0.
    worst_label = ""
    nominal_worst = 0.
    max_cct_error = 0.
    if review:
        print("| Data / record | Elevation | Direct K / Duv | Lit K / Duv | Shade K / Duv | Lit Y error |")
        print("|---|---:|---:|---:|---:|---:|---:|")
    for index,elevation,k,sun,ambient,lit,shade,direct in rows:
        label = f"{index} {elevation}deg k={k}"
        original = [min(1.,max(0.,a))*1.08+min(1.,max(0.,s*k))*math.sin(math.radians(elevation))
                    for a,s in zip(ambient,sun)]
        dt, dd = cct_duv(direct)
        lt, _ = cct_duv(lit)
        st, sd = cct_duv(shade)
        source_t, white = sun_white(elevation)
        target_t, _ = cct_duv(white)
        max_cct_error = max(max_cct_error,abs(dt-target_t))
        if abs(dt-source_t) > 300: failures.append(f"{label}: direct {dt:.0f} K, source curve {source_t:.0f} K")
        if abs(dt-target_t) > 300: failures.append(f"{label}: direct {dt:.0f} K, adapted curve {target_t:.0f} K")
        if abs(dd) > .006: failures.append(f"{label}: direct Duv {dd:+.5f}")
        if st > 10000+10 or not -0.00002 <= sd <= .006:
            failures.append(f"{label}: shade {st:.0f} K / {sd:+.5f} Duv")
        if lt > st+2: failures.append(f"{label}: lit {lt:.0f} K bluer than shade {st:.0f} K")
        if not all(-1e-7 <= c <= 1+1e-7 for c in (*shade,*direct)):
            failures.append(f"{label}: light channels outside [0,1]")
        if abs(luminance(shade)-luminance(ambient)*1.08) > 2e-6:
            failures.append(f"{label}: shade luminance changed")
        # Every direct channel must share one scalar (hue survives saturation).
        scales = [a/b for a,b in zip(direct,white)]
        if max(scales)-min(scales) > 3e-5: failures.append(f"{label}: direct hue rotated")
        # Preserving ambient Y leaves exactly the original direct energy. Its
        # scalar must be maximal subject to the brightest direct channel <=1.
        wanted_y = luminance([min(1.,max(0.,v*k)) for v in sun])
        feasible_y = min(wanted_y,1/max(white))
        if abs(luminance(direct)-feasible_y) > 3e-5:
            failures.append(f"{label}: feasible direct luminance not retained")
        delta = 100*(luminance(lit)/luminance(original)-1)
        if abs(delta) > worst: worst, worst_label = abs(delta), label
        if k == 1: nominal_worst = max(nominal_worst,abs(delta))
        if delta > .001: failures.append(f"{label}: lit luminance increased {delta:.4f}%")
        if review and index < 8 and k == 1 and elevation in (5,15,30,62):
            side,key,_,_ = TOD_RECORDS[index]
            cells = ["%.0f / %+.5f" % cct_duv(c) for c in (direct,lit,shade)]
            print(f"| {side} {key} | {elevation} | "+" | ".join(cells)+f" | {delta:+.3f}% |")
    if failures:
        for line in failures[:12]: print("FAIL: "+line)
        raise AssertionError(f"{len(failures)} failures in {len(rows)} real-hook cases")
    print(f"PASS: {len(rows)} real-hook cases; direct CCT within 300 K of adapted curve, shade <=10000 K / Duv [0,.006], lit never bluer, hue and shade Y preserved")
    print(f"Maximum lit-ground luminance shortfall: {worst:.3f}% ({worst_label}); k=1: {nominal_worst:.3f}%; max adapted CCT error {max_cct_error:.3f} K")


def main():
    effect = (ROOT / "runtime/d3dx_effect.c").read_text()
    setter = re.search(r"static int e_SetFloat\(Cpu \*c\) \{.*?\n\}", effect, re.S).group()
    # The actual recompiled instructions prove both uploads call vtable +0x78
    # (ID3DXEffect::SetFloat), using the same parameter-handle offset +0x86C.
    generated = "\n".join(p.read_text() for p in (ROOT / "recomp/out").glob("speed_*.c"))
    for address in ("006D76", "006E04"):
        match = re.search(r"/\* " + address + r"[0-9A-F]{2}: fsub dword ptr \[0x8fae68\].*?call dword ptr \[edx \+ 0x78\]", generated, re.S)
        assert match and len(match.group()) < 4000
        assert "0x86Cu" in match.group() and "0x982BA8u" in match.group()
    assert '006C6450    lighting.c:' in (ROOT / "recomp/hooks.txt").read_text()
    source = r'''
#include <assert.h>
#include <stdarg.h>
#include "runtime/lighting.c"
#include "runtime/settings.c"
static uint8_t memory[0xA00000];
uint8_t *G = memory;
int diag_on = 1;
static int option_lines, ambient_calls, sun_calls;
static float uploaded[3], solved[3];
static char saved_tunnel[16];
const char *app_pref(const char *key) {
    return !strcmp(key, "graphics.tunnel_shadows") ? saved_tunnel : "50"; // stale shade preference
}
void app_pref_set(const char *key, const char *v) {
    assert(!strcmp(key, "graphics.tunnel_shadows"));
    snprintf(saved_tunnel, 16, "%s", v);
}
void report_note(const char *fmt, ...) { }
int postfx_look_variant(const char *v) { return 0; }
uint64_t host_ticks(void) { return 10000000000ull; }
void diag_event(const char *fmt, ...) {
    if (!strncmp(fmt, "lighting_options:", 17)) ++option_lines;
}
void f_006C6450_orig(Cpu *c) {
    ++ambient_calls;
    for (int i = 0; i < 3; ++i) uploaded[i] = rf(0x1000 + 0x30 + 4*i);
    assert(rf(0x103C) == 4);
}
void f_006C6300_orig(Cpu *c) {
    ++sun_calls;
    for (int i = 0; i < 3; ++i) solved[i] = rf(0x1000 + 0x20 + 4*i);
}
// Minimal COM parameter scaffolding; the setter below is copied verbatim from runtime.
typedef struct { char name[64]; float value; } Param;
static Param parameter;
#define FX NULL
#define D3D_OK 0
#define D3DERR_INVALIDCALL 0x8876086Cu
static Param *param_of(void *fx, uint32_t handle) { return &parameter; }
static void set_floats(Param *p, const float *f, int n) { p->value = *f; }
SETTER
static int close3(const float a[3], const float b[3]) {
    for (int i=0; i<3; ++i) if (fabsf(a[i]-b[i]) > 1e-6f) return 0;
    return 1;
}
int main(int argc, char **argv) {
    int saved = argc > 1 && !strcmp(argv[1], "saved");
    if (saved) strcpy(saved_tunnel, "pc");   // Keep is the default, so the saved value is the non-default Original
    settings_init();
    assert(s_tunnel.def == 1 && s_tunnel.value == !saved);
    Cpu cpu = {0};
    cpu.esp = 0x4000;
    wf(0x008FAE68u, .8f);
    strcpy(parameter.name, "InvShadowStrength");
    wf(cpu.esp+12, 1.2f);
    s_tunnel.value = 1; e_SetFloat(&cpu);
    assert(fabsf(parameter.value-.2f) < 1e-6f);
    s_tunnel.value = 0; cpu.esp=0x4000;
    e_SetFloat(&cpu); assert(parameter.value == 1.2f);
    s_tunnel.value = 1; cpu.esp=0x4000;
    strcpy(parameter.name, "OtherFloat"); e_SetFloat(&cpu);
    assert(parameter.value == 1.2f);
    if (!saved) {
        s_tunnel.value=1;
        assert(settings_set(&s_tunnel,0) && !strcmp(saved_tunnel,"pc"));
        settings_reset(&s_tunnel);
        assert(s_tunnel.value==1 && !*saved_tunnel);
    }
    M32(TIME_OF_DAY_PTR)=0x1000; M32(GAMEFLOW_STATE)=6;
    wf(0x103C,4);
    // the synthetic sun/ambient records, defined once in the Python above
    const float records[8][6] = {RECORDS};
    int count=0;
    // Eight records and all pairwise 25/50/75% blends. Both data
    // sources, cloud transitions and k != 1 use the actual setters.
    for (int r=0; r<8+28*3; ++r) {
        float rec[6];
        if (r<8) memcpy(rec,records[r],sizeof(rec));
        else {
            int pair=(r-8)/3, a=0,b=1;
            for (int n=0;n<pair;++n) { if (++b==8) b=++a+1; }
            float t=((r-8)%3+1)*.25f;
            for (int i=0;i<6;++i) rec[i]=records[a][i]+t*(records[b][i]-records[a][i]);
        }
        for (int e=5;e<=90;++e) for (int ki=0;ki<3;++ki) {
            float k=ki==0 ? .2f : ki==1 ? 1.f : 2.f;
            float angle=e*.017453292519943295f;
            wf(SUN_SCALE,k); wf(0x1180,cosf(angle)); wf(0x1184,0); wf(0x1188,sinf(angle));
            for (int i=0;i<3;++i) { wf(0x1020+4*i,rec[i]); wf(0x1030+4*i,rec[i+3]); }
            f_006C6450(&cpu); f_006C6300(&cpu);
            float lit[3],shade[3],direct[3];
            for (int i=0;i<3;++i) {
                assert(rf(0x1020+4*i)==rec[i] && rf(0x1030+4*i)==rec[i+3]);
                direct[i]=fminf(1.f,solved[i]*k);
                shade[i]=uploaded[i]*4*PRELIGHT;
                lit[i]=shade[i]+direct[i]*sinf(angle);
            }
            if (!saved) {
                printf("%d %d %.9g",r,e,k);
                for (int i=0;i<6;++i) printf(" %.9g",rec[i]);
                for (int i=0;i<3;++i) printf(" %.9g",lit[i]);
                for (int i=0;i<3;++i) printf(" %.9g",shade[i]);
                for (int i=0;i<3;++i) printf(" %.9g",direct[i]);
                puts("");
            }
            ++count;
        }
    }
    assert(ambient_calls==count && sun_calls==count && option_lines==1);
    // Both hooks must retain authored RGB in Original and in the front end.
    float a[3],d[3];
    for (int i=0;i<3;++i) { a[i]=rf(0x1030+4*i); d[i]=rf(0x1020+4*i); }
    mode=0; f_006C6450(&cpu); f_006C6300(&cpu);
    assert(close3(uploaded,a) && close3(solved,d));
    mode=1; M32(GAMEFLOW_STATE)=3; f_006C6450(&cpu); f_006C6300(&cpu);
    assert(close3(uploaded,a) && close3(solved,d));
    M32(GAMEFLOW_STATE)=6; f_006C6450(&cpu); assert(option_lines==2);
    fprintf(stderr,"PASS: Tunnel shadows defaults/SetFloat/persistence/reset; Original/FE guards; %d TOD restores; once/load diagnostics\n",count);
}

'''.replace("SETTER", setter).replace("RECORDS", ",".join(
        "{%s}" % ",".join("%.4ff" % v for v in sun + amb) for _, _, sun, amb in TOD_RECORDS))
    with tempfile.TemporaryDirectory(prefix="lighting-options-") as temp:
        src = Path(temp) / "test.c"
        exe = Path(temp) / "test"
        if "--baseline-head" in sys.argv:
            baseline = Path(temp)/"lighting_before.c"
            baseline.write_text(subprocess.run(["git","show","HEAD:runtime/lighting.c"],cwd=ROOT,
                                                text=True,capture_output=True,check=True).stdout)
            source = source.replace('#include "runtime/lighting.c"', f'#include "{baseline}"')
            source = source.replace(': "50"; // stale shade preference', ': ""; // baseline default')
        src.write_text(source)
        subprocess.run(["clang", "-O1", "-I", str(ROOT), "-I", str(ROOT / "recomp"), "-I", str(ROOT / "runtime"), str(src), str(ROOT / "runtime/offline_stubs.c"), "-o", str(exe)], check=True)
        env = {k: v for k, v in os.environ.items() if k not in ("NFS_SHADE_DESAT", "NFS_TUNNEL_SHADOWS", "NFS_WORLD_LIGHTING")}
        # Removed env/key must not affect Neutral or acquire a menu row.
        measured = subprocess.run([str(exe)], env=env, text=True, capture_output=True)
        if measured.returncode: sys.exit(f"harness failed ({measured.returncode}): {measured.stderr[-2000:]}")
        print(measured.stderr.strip())
        rows = []
        for line in measured.stdout.splitlines():
            v = list(map(float,line.split()))
            rows.append((int(v[0]),int(v[1]),v[2],v[3:6],v[6:9],v[9:12],v[12:15],v[15:18]))
        assert len(rows) == 92*86*3
        check_targets(rows, review="--review-after" in sys.argv)
        legacy_env = dict(env, NFS_SHADE_DESAT="100")
        ignored = subprocess.run([str(exe)],env=legacy_env,check=True,capture_output=True,text=True)
        assert ignored.stdout == measured.stdout
        subprocess.run([str(exe), "saved"],env=legacy_env,check=True,capture_output=True)
        lighting = (ROOT/"runtime/lighting.c").read_text()
        assert "NFS_SHADE_DESAT" not in lighting and "graphics.shade_colour" not in lighting
        print("PASS: removed Shade colour registry/env; stale preference/env ignored")



if __name__ == "__main__":
    if "--review-before" in sys.argv:
        before_review()
    else:
        main()
