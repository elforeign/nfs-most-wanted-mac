"""Underglow colour chain: each native menu label -> stored preset -> light duties -> Rec.709 emitter and pool RGB
must land on the label's named hue (within 5 degrees, saturation >= .9; White saturation <= .02; Off is black).
Measured and Stronger each 16/16 labels (8 emitter + 8 pool). Pinned predecessor (display-order labels over RGB bit-mask presets, equal
channel duties, aeaae142) fails 10/16: Blue drew 46.3 deg, Cyan 238.7, Magenta 280.5, Yellow 212.6, White saturation .58,
each on both paths. Labels fixed but equal duties fails 8/16 (Yellow 46.3, Magenta 280.5, Cyan 212.6, White). Production C: underglow_menu.c, underglow_photometry.c, and the
emitter statement plus rgb_lux() extracted from underglow_light.m.
"""
import colorsys
import hashlib
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RT = ROOT / 'runtime'
HUES = {'Red': 0, 'Yellow': 60, 'Green': 120, 'Cyan': 180, 'Blue': 240, 'Magenta': 300}

OLD_NAMES = 'static const char *k_names[8] = {"Off", "Red", "Green", "Blue", "Cyan", "Magenta", "Yellow", "White"};'
OLD_EMITTER = '    double duties[3],xyz[3],rgb[3];ug_preset_rgb(s->preset,duties);ug_mix_xyz(duties,xyz);double error;ug_xyz_rec709(xyz,rgb,&error);'
OLD_LUX = '''static void rgb_lux(unsigned preset,const UGStrip *strips,UGVec3 p,double rgb[3]) {
    double duties[3],xyz[3]={0};ug_preset_rgb(preset,duties);
    for(int c=0;c<3;++c) if(duties[c]) {
        double e=0;
        for(int i=0;i<UG_STRIPS;++i) { double v=0;ug_segment_lux(ug_primaries[c].flux,strips[i].a,strips[i].b,strips[i].normal,p,(UGVec3){0,0,1},&v);e+=v; }
        double x=ug_primaries[c].x,y=ug_primaries[c].y;
        xyz[0]+=e*x/y;xyz[1]+=e;xyz[2]+=e*(1-x-y)/y;
    }
    double error;ug_xyz_rec709(xyz,rgb,&error);
}'''
PINS = {OLD_NAMES: 'bc918dc6e25509485ddf8b13afbeeb5de28666bf41677fb6719ed43eb333590a',
        OLD_EMITTER: '7601f695bc8899b840c819487104590fc3bb075b743de0d712195c1f6859a29a',
        OLD_LUX: 'c742383183dce1e2de17483f08433161000be7c9016149ae6825cf6d289ef473'}

PRE = r'''
#include "host.h"
#include "underglow_render.h"
#include <stdlib.h>
uint8_t *G;
int diag_on = 0;
const char *app_pref(const char *key){return NULL;}
void app_pref_set(const char *key,const char *value){}
uint32_t galloc(uint32_t n) { static uint32_t top = 0xA00000; uint32_t r = top; top += (n + 15) & ~15u; return r; }
uint32_t gstr(const char *s) { static uint32_t top = 0xE00000; uint32_t r = top; strcpy((char *)G + r, s); top += 64; return r; }
void diag_event(const char *fmt, ...) {}
static unsigned preset_of[256], light_of[256];
unsigned underglow_get(uint32_t p, uint32_t h) { return preset_of[h & 255]; }
int underglow_set(uint32_t p, uint32_t h, unsigned v) { preset_of[h & 255] = v; return 1; }
unsigned underglow_light_get(uint32_t p, uint32_t h) { return light_of[h & 255]; }
int underglow_light_set(uint32_t p, uint32_t h, unsigned on) { light_of[h & 255] = on; return 1; }
int underglow_owned(uint32_t p, uint32_t h) { return 1; }
void underglow_adopt(uint32_t p) {}
void underglow_menu_option_added(uint32_t opt);
static uint32_t options[32], labels[32]; static int adds;
uint32_t call_guest(Cpu *c, uint32_t fn, int n, const uint32_t *a) {
    if (fn == 0x007BB560u) { uint32_t o = 0xC00000 + 0x100 * adds; M32(o + 0x50) = (0x803u << 16) | a[3];
        labels[adds] = a[2]; options[adds++] = o; underglow_menu_option_added(o); return 0; }
    return 0;
}
void f_007BB560_orig(Cpu *c) {}
void f_007BFBD0_orig(Cpu *c) {}
'''
POST = r'''
static void emitter(unsigned preset, double out[3]) {
    struct { unsigned preset; } S = {preset}, *s = &S;
@EMITTER@
    memcpy(out, rgb, sizeof rgb); (void)error;
}
static UGStrip strip(double ax, double ay, double bx, double by) {
    UGStrip s = {{ax, ay, .1}, {bx, by, .1}, {0, 0, -1}, hypot(bx - ax, by - ay), .01};
    return s;
}
int main(void) {
    G = calloc(1, 16 << 20); if (!G) return 2;
    uint32_t self = 0x2000, db = 0x3000, profile = 0x4000;
    M32(0x0091CF90u) = db; M32(db + 0x10) = profile; M32(0x009B9EC4u) = 0x5000; M32(0x5000) = 82;
    M32(self + 0x2C) = 0x6000; M32(0x6000 + 0x40) = 0x40; M32(self) = 0x7000; M32(0x7000 + 0xC) = 0xC;
    M32(self + 0x150) = 0x308; Cpu c = {0}; c.ecx = self; c.esp = 0x1000; f_007BFBD0(&c);
    /* A rectangle of four 2.4/1.6 m strips 10 cm above the receiver plane, as car_mounts lays them out. */
    UGStrip strips[UG_STRIPS];
    for (int k = 0; k < UG_STRIPS; ++k) {
        double side = k & 1 ? 1 : -1;
        strips[k] = k < 2 ? strip(-1.2, .8 * side, 1.2, .8 * side) : strip(1.5 * side, -.6, 1.5 * side, .6);
    }
    for (unsigned i = 0; i < 8; ++i) {
        M32(self + 0x38) = options[i]; underglow_menu_notify(self, 0x5A928018u);
        unsigned preset = preset_of[82];
        double e[3], p[3]; emitter(preset, e); rgb_lux(preset_duties(preset), strips, (UGVec3){.3, .5, 0}, p);
        printf("%s|%u|%.9g %.9g %.9g|%.9g %.9g %.9g\n", (char *)G + underglow_menu_text(labels[i]), preset, e[0], e[1], e[2], p[0], p[1], p[2]);
    }
    underglow_menu_notify(self, 0xE1FDE1D1u);
    return 0;
}
'''


def fragments(menu, light):
    start = light.index('static const double *preset_duties(')
    end = light.index('static void rgb_lux(', start)
    lux = light[start:light.index('\n}\n', end) + 2]
    emit = [l for l in light.splitlines() if 'ug_mix_xyz(duties,xyz);double error;ug_xyz_rec709(xyz,rgb,&error);' in l]
    assert len(emit) == 1, emit
    menu = menu.replace('#include "host.h"\n', '').replace('#include "underglow.h"\n', '')
    return menu, emit[0], lux


def judge(rgb):
    top = max(rgb)
    if top <= 0: return None, 0.0
    h, s, _ = colorsys.rgb_to_hsv(*(v / top for v in rgb))
    return h * 360, s


def run(tmp, name, menu, emit, lux, stronger=False):
    src = tmp / (name + '.c')
    post = POST.replace('@EMITTER@', emit)
    if stronger:
        post=post.replace('rgb_lux(preset_duties(preset), strips, (UGVec3){.3, .5, 0}, p);',
            'rgb_lux(preset_duties(preset), strips, (UGVec3){.3, .5, 0}, p);\n'
            '        double b=ug_pool_broad(strips,(UGVec3){.3,.5,0});for(int k=0;k<3;++k)p[k]+=e[k]*b;')
    if 'static const double *preset_duties(' not in lux:
        post = post.replace('rgb_lux(preset_duties(preset),', 'rgb_lux(preset,')
    src.write_text(PRE + menu + '\n#include "underglow_photometry.c"\n#include "underglow_pool.h"\n' + lux + post)
    exe = tmp / name
    cp = subprocess.run(['clang', '-O1', '-I' + str(RT), '-I' + str(ROOT / 'recomp'), str(src), '-o', str(exe)],
                        capture_output=True, text=True)
    assert cp.returncode == 0, cp.stderr[-3000:]
    out = subprocess.run([str(exe)], capture_output=True, text=True, check=True).stdout.split('\n')
    good = bad = 0
    for line in filter(None, out):
        label, preset, e, p = line.split('|')
        for path, rgb in (('emitter', [float(v) for v in e.split()]), ('pool', [float(v) for v in p.split()])):
            hue, sat = judge(rgb)
            if label == 'Off': ok = hue is None
            elif label == 'White': ok = hue is not None and sat <= .02
            else: ok = hue is not None and sat >= .9 and min(abs(hue - HUES[label]), 360 - abs(hue - HUES[label])) <= 5
            good += ok; bad += not ok
            if not ok: print(f'  {name}: {label} (preset {preset}) {path} drew hue {hue if hue is None else round(hue, 1)} saturation {sat:.3f}')
    print(f'{name}: {good}/{good + bad} labels draw their named colour')
    return bad


def main():
    for text, digest in PINS.items():
        assert hashlib.sha256(text.encode()).hexdigest() == digest, 'pinned predecessor fragment changed'
    menu, emit, lux = fragments((RT / 'underglow_menu.c').read_text(), (RT / 'underglow_light.m').read_text())
    names = [l for l in menu.splitlines() if l.startswith('static const char *k_names[8]')]
    assert len(names) == 1
    old_menu = menu.replace(names[0], OLD_NAMES)
    with tempfile.TemporaryDirectory(prefix='ug-colour-') as d:
        d = Path(d)
        assert run(d, 'good', menu, emit, lux) == 0
        assert run(d, 'stronger', menu, emit, lux, stronger=True) == 0
        assert run(d, 'predecessor', old_menu, OLD_EMITTER, OLD_LUX) == 10
        assert run(d, 'predecessor_stronger', old_menu, OLD_EMITTER, OLD_LUX, stronger=True) == 10
        assert run(d, 'equal_duties', menu, OLD_EMITTER, OLD_LUX) == 8


if __name__ == '__main__':
    main()
