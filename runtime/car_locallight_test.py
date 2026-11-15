#!/usr/bin/env python3
"""Independent summed-lamp term; rig bytes preserved for facing/away normals.
Default On: the 'default' run (variable unset) must light like =1; on the old Off default it fails (line 217,
2332 failures); '0' is Off (1 startup read, 0 work). Solo A/B 11-14 (8 runs): a 6.176, a2 6.021, b 6.238, c 6.191 ms.
Two-lamp 30 m walk (4 m height, 0.1 m steps): max direction <3 deg, level <0.01.
Summed: 2.555659 deg / 0.004060; frozen nearest: 150.041458 deg / 0.004633.
Positive-X panel step: summed 0.018124 (<0.02), nearest 0.368148 (fails).
Context queue: 2,301 live entries; frozen ring fails 2,303 assertions, current 0.
Fixture checks gather falloff, local position, summation and a two-lamp walk, unload, Off,
zero RGB, level zero and recycled context. Broken zero control must fail.
819383c4 re-aim control: facing-away fill 0.1 -> 0.0 (must fail preservation);
GPU plain/normal-map/no-shadow: zero/away 983,040 FP32+BGRA8 bytes identical;
single/last-light 49,152/49,152 pixels brighter; one-light shader control 0/1,024
last-light pixels brighter (fails). Shader integration is exercised below.
Material uploads: Off paint/non-car 256/256 bytes, On paint/t0 832/832;
On non-car 256. Frozen d72caac0: Off paint/non-car 832/832 (fails).
Off entry-point regression: before rotate/select/setup/material = 3/1/4/1
extra operations (9 total); current = 0/0/0/0 (limit 0), upload still 256 B.
Diagnostic midnight scene, 54 post-warp sampled frames: calls/frame
rotate 6/58/86, setup 3/17/25, select 114/286/377, material 287/473.5/610
(min/median/max); refresh and context lookup 0. Environment: one startup read,
no per-call reads. Cache-warm isolated -O2 fixture, 3,000,000 calls each:
rotate/select/setup/material before ~1.78/6.76/2.39/3.82 ns, after
~1.03/1.23/1.22/3.90 ns; original stub ~0.96 ns. The fixture substitutes
original engine and material callbacks and excludes diagnostic overhead.
Weighting these before/after deltas by sampled median counts gives about
0.0016 ms/frame; this does not explain the observed ~0.20 ms difference.
About 0.198 ms remains unexplained and needs a solo full-binary profile;
the complete Metal draw/upload path was not microbenchmarked here.
Post-fix midnight Off run: 47 post-warp sampled frames, every feature-work
counter 0 (before: 43,435 work entries across 54 sampled frames). Calls/frame
rotate 6/57/89, setup 3/17/25, material 284/472/622; select/refresh/lookup/
apply/clone all 0. Both runs end at the same parked position. No game timing
claim. Actual Off harness: one startup getenv, no per-call getenv or allocations,
and invalid output/guest-stack pointers remain unread for both unset and 0.
The same check also requires the unused tail to remain untouched and both
soft-smoke replay paths to accept the shortened upload.
"""
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / 'runtime'
SOURCE = RUNTIME / 'car_locallight.c'
HEADER = RUNTIME / 'car_locallight.h'

HARNESS = r'''
#include "host.h"
#include "auxlights.h"
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
uint8_t *G;
int diag_on=0;
void f_00502030_orig(Cpu *c) { c->eax=123; }
static uint64_t frame;
uint64_t diag_current_frame(void) { return frame; }
void diag_event(const char *f,...) { (void)f; }
int diag_shot_due(uint64_t f) { (void)f; return 0; }
int postfx_look_variant(const char *v) { (void)v; return 0; }
static AuxLightContext fixture[4];   // plain, turned, misplaced, dense
static void light(int context, float x, float y, float z, float r, float g, float b) {
    AuxLightContext *c = &fixture[context];
    c->lights[c->count++] = (AuxLight){{x, y, z}, 15, {r, g, b}, 15};
}
enum { INSTANCES=0x4000 };
int auxlights_enabled(void) { return 1; }
const AuxLightContext *auxlights_find(uint32_t section, const uint8_t *bbox) {
    uint32_t i = (uint32_t)(bbox - G - INSTANCES) / 64;
    return section == 7 && i < 4 ? &fixture[i] : NULL;
}
static unsigned serial = 1, loaded = 1;
unsigned materials_aux_serial(void) { return serial; }
int materials_aux_section(unsigned slot, uint32_t *id, uint32_t *instances, uint32_t *count) {
    if (slot != 5 || !loaded) return 0;
    *id = 7; *instances = INSTANCES; *count = 4;
    return 1;
}
static void (*registered)(uint32_t, uint32_t, uint32_t, float);
void daynight_register_car_light(void (*f)(uint32_t, uint32_t, uint32_t, float)) { registered = f; }
static void instance(int i, float x0, float y0, float z0, float x1, float y1, float z1, int transformed) {
    uint32_t a = INSTANCES + 64 * i;
    float b[6] = {x0, y0, z0, x1, y1, z1};
    memcpy(G + a, b, sizeof b);
    M32(a + 0x18) = transformed ? 0 : 0x200;
    if (transformed) {                       // rows (0,1,0), (-1,0,0), (0,0,1), moved to (7,0,0)
        int16_t basis[9] = {0, 8192, 0, -8192, 0, 0, 0, 0, 8192};
        memcpy(G + a + 0x2c, basis, sizeof basis);
        MF32(a + 0x20) = 7;
    }
}
static int failures;
static unsigned environment_reads, allocations;
static char *feature_getenv(const char *name) { ++environment_reads; return getenv(name); }
static void *feature_calloc(size_t n,size_t size) { ++allocations; return calloc(n,size); }
static void *feature_realloc(void *p,size_t size) { ++allocations; return realloc(p,size); }
#define X360LC_09C0DE10_C255 "float4(0.5,0.25,0,1)"
#define getenv feature_getenv
#define calloc feature_calloc
#define realloc feature_realloc
#include TEST_SOURCE
#undef getenv
#undef calloc
#undef realloc
#define CHECK(x) do { if(!(x)) { ++failures; printf("FAIL line %d: %s\n",__LINE__,#x); } } while(0)
#define NEAR(a,b) (fabsf((a)-(b)) < 1e-5f+1e-3f*fabsf(b))
enum { CTX=0x1000, POS=0x2000, MAT=0x3000 };
static void context(void) {
    memset(G+CTX,0,0x130);
    for (int l=0;l<3;++l) for (int k=0;k<3;++k) MF32(CTX+0x10+0x10*l+4*k)=l?.1f:.8f;
    MF32(CTX+0x50)=1; MF32(CTX+0x64)=-1; MF32(CTX+0x78)=1;   // light 0 +x, light 1 -y, light 2 +z
}
static void matrix(int turned) {
    memset(G+MAT,0,64);
    if (turned) { MF32(MAT+4)=-1; MF32(MAT+16)=1; } else { MF32(MAT)=1; MF32(MAT+20)=1; }
    MF32(MAT+40)=1; MF32(MAT+60)=1;
}
int main(int argc,char **argv) {
    G=calloc(1,0x920000);   // up to the flare-pack list head at 0091757C
    light(0, 10,0,5, 1,.5f,.25f);
    light(1, 0,-3,5, 1,.5f,.25f); light(1, 0,-93,0, 1,1,1);
    light(2, 500,0,0, 1,1,1);
    for (int i=0;i<8;++i) light(3, 300,.3f*i-1,1, 1,1,1);   // eight white lights 0.3 m apart
    instance(0, 0,-10,0, 20,10,10, 0); instance(1, 5,-5,0, 105,5,10, 1); instance(2, 0,0,0, 1,1,1, 0); instance(3, 290,-10,0, 310,10,10, 0);
    uint8_t before[0x130];
    int off = argc>1;
    float origin[3]={0,0,0};
    CarLocalLight g={0};
    car_locallight_gather(origin,NULL,&g);
    if (off) {
        CHECK(g.lamps==0 && !registered && !built && !context_used);
        CHECK(environment_reads==1 && !allocations);
        // Off entry points must not touch outputs, contexts, or the guest stack.
        memset(&selected,0xa5,sizeof selected);
        CarLocalLight untouched=selected;
        car_locallight_select(UINT32_MAX);
        CHECK(!memcmp(&selected,&untouched,sizeof selected));
        Cpu off_cpu={.esp=UINT32_MAX}; f_00502030(&off_cpu);
        CHECK(off_cpu.eax==123);
        car_locallight_gather(NULL,NULL,NULL);
        car_locallight_constants(NULL);
        car_locallight_clone(UINT32_MAX,UINT32_MAX,UINT32_MAX);
        car_locallight_apply(UINT32_MAX,UINT32_MAX,UINT32_MAX,1);
        CHECK(!built && !context_used);
        context(); matrix(0); memcpy(before,G+CTX,sizeof before);
        car_locallight_apply(CTX,POS,MAT,1);
        CHECK(!memcmp(before,G+CTX,sizeof before));
        car_locallight_select(CTX);
        float constants[CAR_LOCAL_LIGHT_CONSTANTS][4]={{0}},zero[4]={0};
        float *lp=constants[12], *rgb=constants[13]; car_locallight_constants(constants);
        CHECK(!memcmp(lp,zero,sizeof lp) && !memcmp(rgb,zero,sizeof rgb));
        CHECK(environment_reads==1 && !allocations);
        printf("off failures=%d getenv_startup=%u getenv_calls=0 allocations=%u\n",failures,environment_reads,allocations);
        return failures!=0;
    }
    float w=.75f*(45/265.f)*(1-125.f/225);
    CHECK(registered==car_locallight_apply);
    CHECK(g.lamps==1 && NEAR(g.nearest,sqrtf(125)) && lamp_count==10 && misplaced==1);
    CHECK(NEAR(g.lights[0].rgb[0],w) && NEAR(g.lights[0].rgb[1],.25f*w) && NEAR(g.lights[0].rgb[2],.0625f*w));
    CHECK(NEAR(g.lights[0].position[0],10) && NEAR(g.lights[0].position[1],0) && NEAR(g.lights[0].position[2],5));
    float turned[3][3]={{0,-1,0},{1,0,0},{0,0,1}};
    car_locallight_gather(origin,turned,&g);
    CHECK(NEAR(g.lights[0].position[0],0) && NEAR(g.lights[0].position[1],10) && NEAR(g.lights[0].position[2],5));

    context(); matrix(1); memcpy(before,G+CTX,sizeof before);
    car_locallight_apply(CTX,POS,MAT,1);
    car_locallight_select(CTX);
    float constants[CAR_LOCAL_LIGHT_CONSTANTS][4];
    float *lp=constants[12], *rgb=constants[13]; car_locallight_constants(constants);
    printf("lamp_green=%.4f\n",rgb[1]);
    CHECK(!memcmp(before,G+CTX,sizeof before));
    CHECK(NEAR(rgb[1],.25f*w) && rgb[3]==1 && NEAR(lp[1],10));
    context(); matrix(1);
    car_locallight_apply(CTX,POS,MAT,.5f);
    car_locallight_select(CTX); car_locallight_constants(constants);
    CHECK(NEAR(rgb[1],.125f*w));
    car_locallight_select(CTX+1); car_locallight_constants(constants);
    CHECK(rgb[3]==0 && rgb[1]==0 && lp[1]==0);
    ++frame; car_locallight_select(CTX); car_locallight_constants(constants);
    CHECK(rgb[3]==0 && rgb[1]==0);

    // The world rig is cloned into part-local contexts before drawing.
    car_locallight_apply(CTX,POS,MAT,1);
    MF32(MAT+48)=2; MF32(MAT+52)=3; MF32(MAT+56)=1;
    Cpu clone_cpu={0}; clone_cpu.esp=0x8000;
    M32(clone_cpu.esp+4)=CTX+0x200; M32(clone_cpu.esp+8)=CTX; M32(clone_cpu.esp+12)=MAT;
    f_00502030(&clone_cpu);
    CHECK(clone_cpu.eax==123);
    car_locallight_select(CTX+0x200); car_locallight_constants(constants);
    CHECK(rgb[3]==1 && NEAR(lp[0],3) && NEAR(lp[1],8) && NEAR(lp[2],4));
    CHECK(!memcmp(before,G+CTX,sizeof before));
    car_locallight_clone(CTX+0x200,CTX+0x400,MAT);
    car_locallight_select(CTX+0x200); car_locallight_constants(constants);
    CHECK(rgb[3]==0 && lp[0]==0);
    context(); matrix(1); memcpy(before,G+CTX,sizeof before);
    car_locallight_apply(CTX,POS,MAT,0);
    car_locallight_select(CTX); car_locallight_constants(constants);
    CHECK(rgb[3]==0 && rgb[1]==0 && lp[1]==0);
    CHECK(!memcmp(before,G+CTX,sizeof before));
    MF32(POS)=50;
    car_locallight_apply(CTX,POS,MAT,1);
    car_locallight_select(CTX); car_locallight_constants(constants);
    CHECK(rgb[3]==0 && rgb[1]==0 && lp[1]==0);
    CHECK(!memcmp(before,G+CTX,sizeof before));
    float dense[3]={300,0,0};
    car_locallight_gather(dense,NULL,&g);
    CHECK(g.lamps==8 && g.nearest < 1.01f && NEAR(light_luma(&g),.8f));
    // One flare pack: a lamp-post flare at (700,5,12) and a flare of another type on top of the car.
    enum { HEAD=0x0091757C, PACK=0x5000, LAMP=0x6000, OTHER=0x6040 };
    M32(HEAD)=PACK; M32(PACK)=HEAD; M16(PACK+0x50)=2;
    M32(PACK+0x58)=LAMP; M32(LAMP)=OTHER; M32(OTHER)=PACK+0x58;
    MF32(LAMP+0x10)=700; MF32(LAMP+0x14)=5; MF32(LAMP+0x18)=12; M8(LAMP+0x2c)=13;
    MF32(OTHER+0x10)=700; MF32(OTHER+0x14)=0; MF32(OTHER+0x18)=1; M8(OTHER+0x2c)=5;
    float street[3]={700,0,0}, sw=.75f*(1-169/484.f);
    car_locallight_gather(street,NULL,&g);
    CHECK(g.lamps==1 && street_count==1 && NEAR(g.nearest,13));
    CHECK(NEAR(g.lights[0].rgb[0],182/255.f*sw) && NEAR(g.lights[0].rgb[2],240/255.f*sw));
    CHECK(NEAR(g.lights[0].position[1],5) && NEAR(g.lights[0].position[2],12));
    M32(HEAD)=HEAD;                                              // the pack unloads; no section changed
    car_locallight_gather(street,NULL,&g);
    CHECK(g.lamps==0 && street_count==0);
    float far[3]={500,0,0};
    car_locallight_gather(far,NULL,&g);
    CHECK(g.lamps==0);                                         // the misplaced light lights nothing
    loaded=0; ++serial;                                        // the section unloads
    car_locallight_gather(origin,NULL,&g);
    CHECK(g.lamps==0 && lamp_count==0);

    // More than the old ring's 1024 contexts must survive queued draws.
    loaded=1; ++serial; ++frame; MF32(POS)=0; matrix(0);
    car_locallight_apply(CTX,POS,MAT,1);
    for(unsigned i=0;i<2300;i++) car_locallight_clone(0x100000+64*i,CTX,MAT);
    CHECK(context_used==2301 && context_room>=8192);
    for(unsigned i=0;i<2300;i++) {
        car_locallight_select(0x100000+64*i);car_locallight_constants(constants);
        CHECK(NEAR(rgb[1],.25f*w) && rgb[3]==1);
    }
    car_locallight_select(CTX);car_locallight_constants(constants);CHECK(rgb[3]==1);
    ++frame;
    car_locallight_select(CTX);car_locallight_constants(constants);CHECK(rgb[3]==0);
    car_locallight_apply(CTX+4,POS,MAT,1);CHECK(context_used==1);
    car_locallight_select(CTX+4);car_locallight_constants(constants);CHECK(rgb[3]==1);
    car_locallight_clone(CTX+4,0xdead,MAT);
    car_locallight_select(CTX+4);car_locallight_constants(constants);CHECK(rgb[3]==0);
    // All retained lamps are independent points across nested part clones.
    MF32(POS)=300;car_locallight_apply(CTX,POS,0,1);
    MF32(MAT+48)=300;MF32(MAT+52)=2;
    car_locallight_clone(CTX+8,CTX,MAT);car_locallight_clone(CTX+12,CTX+8,MAT);
    car_locallight_select(CTX+12);car_locallight_constants(constants);
    CHECK(rgb[3]==8 && NEAR(lp[0],0));
    for(unsigned i=1;i<8;i++) {
        unsigned j=14+2*i;
        CHECK(constants[j+1][3]==1 && NEAR(constants[j][0],0) && NEAR(constants[j][1],.3f*i-3));
    }
    printf("context capacity=%u live=%u (2301 queued entries retained); stale/clear/nested PASS\n",context_room,context_used);

    Lamp many[CAR_LOCAL_LIGHT_MAX+1];
    Lamp *saved_lamps=lamps;unsigned saved_count=lamp_count;
    for(unsigned i=0;i<CAR_LOCAL_LIGHT_MAX+1;i++)
        many[i]=(Lamp){.position={0,0,4},.radius=22,.rgb={1,1,1}};
    lamps=many;lamp_count=CAR_LOCAL_LIGHT_MAX+1;
    car_locallight_gather(origin,NULL,&g);
    CHECK(g.lamps==CAR_LOCAL_LIGHT_MAX && NEAR(light_luma(&g),.8f));
    float edge[3]={0,0,26};car_locallight_gather(edge,NULL,&g);CHECK(g.lamps==0);
    edge[2]=25.999f;car_locallight_gather(edge,NULL,&g);CHECK(light_luma(&g)<.002f);
    lamps=saved_lamps;lamp_count=saved_count;


    printf("checks failures=%d\n",failures);
    return failures!=0;
}
'''


def luminance(path, box):
    import numpy as np
    from PIL import Image
    x, y, w, h = box
    pixels = np.asarray(Image.open(path).convert('RGB'), dtype=np.float64)[y:y + h, x:x + w]
    assert pixels.size, 'rectangle outside the still: ' + str(path)
    mean = pixels.reshape(-1, 3).mean(axis=0)
    return float(.2126 * mean[0] + .7152 * mean[1] + .0722 * mean[2]), mean


def proof(on, off, zero, box):
    box = [int(v) for v in box.split(',')]
    assert len(box) == 4
    rows = {name: luminance(path, box) for name, path in (('on', on), ('off', off), ('zero', zero))}
    for name, (y, rgb) in rows.items():
        print('%-4s Y=%.2f rgb=%.1f,%.1f,%.1f' % (name, y, *rgb))
    gain, control = rows['on'][0] - rows['off'][0], rows['zero'][0] - rows['off'][0]
    print('on-off=%.2f zero-off=%.2f' % (gain, control))
    assert abs(control) < .25, 'zeroed lights still change the car'
    assert gain >= 1, 'lights do not brighten the panel'
    print('PASS car_locallight still pair')


def street_proof(on, off, zero):
    """Fixed baseline rectangles; the zero control must fail the A/C gains."""
    import numpy as np
    from PIL import Image
    boxes = ((1112,1532,36,56), (2272,1532,56,36), (1620,1600,220,80), (1490,1810,90,60))
    rows = [[luminance(path, box)[0] for box in boxes] for path in (off, on, zero)]
    for label, values in zip(('Off', 'On', 'Zero'), rows):
        print(label, ' '.join('%s=%.3f' % (key, value) for key, value in zip('ABCD', values)))
    expected = ((29.16,36.89,30.55,17.13), (47.78,36.89,42.84,17.13))
    for measured, target in zip(rows[:2], expected):
        assert all(abs(a-b) <= 2 for a, b in zip(measured, target)), (measured, target)
    a, b, c, d = rows[1]
    assert all(abs(a-b) <= .25 for a, b in zip(rows[0], rows[2]))
    assert rows[2][0] < 46.66 and rows[2][2] < 36.66, 'broken control meets A/C gains'
    assert np.array_equal(np.asarray(Image.open(off)), np.asarray(Image.open(zero))), 'zero differs from Off'
    print('PASS street-lamp acceptance; A/B=%.4f; zero whole-frame maximum pixel difference=0' % (a/b))


# Frozen 819383c4 fill mutation, not a reconstructed approximation.
BASELINE_REAIM = r'''
    for (int s = 0; s < 2; ++s) {
        float y = luma(g.rgb[s]) * level;
        if (!(y > 0)) continue;
        uint32_t colour = context + 0x20 + 0x10 * s, direction = context + 0x54 + 4 * s;
        float fill[3], v[3], n = 0;
        for (int k = 0; k < 3; ++k) fill[k] = MF32(colour + 4 * k);
        float fy = fmaxf(0, luma(fill));
        for (int k = 0; k < 3; ++k) {
            v[k] = MF32(direction + 0x10 * k) * fy + g.dir[s][k] * level;
            n += v[k] * v[k];
        }
        n = sqrtf(n);
        if (n > 1e-6f) for (int k = 0; k < 3; ++k) MF32(direction + 0x10 * k) = v[k] / n;
        for (int k = 0; k < 3; ++k) MF32(colour + 4 * k) = fill[k] + g.rgb[s][k] * level;
        sum += y;
    }
'''
BASELINE_REAIM_SHA256 = 'fe98a4c36620c96ef696e5d48496c378292a791240db47dad8b9dc2b96baaf04'

# Frozen original upload producer and draw binding, independently of git history.
BASELINE_UPLOAD = '''void mat_constants(int id, const float (*vsc)[4], MatConsts *out) {
    memset(out, 0, sizeof *out);
    if (id == MAT_REAL_T0 || id == MAT_REAL_SHADOWED) { realworld_materials_constants(id, vsc, out); return; }
    if (id > 0 && id <= (int)(sizeof table / sizeof *table) && table[id - 1].constants) table[id - 1].constants(vsc, out);
    if (id > 0 && id <= (int)(sizeof table / sizeof *table) &&
        (table[id - 1].msl == car_paint_msl || table[id - 1].patch == car_t0_ps_patch))
        car_locallight_constants(out->k);
    if (out->k[13][3] > 0 && table[id - 1].patch == car_t0_ps_patch) {
        memcpy(out->k[14], vsc[14], sizeof out->k[14]);
        memcpy(out->k[15], vsc[15], sizeof out->k[15]);
    }
}
        mat_constants(pse->mat, (const float (*)[4])dev.vsc, &mk);
        [enc setFragmentBytes:&mk length:sizeof mk atIndex:MAT_PS_BUF];
'''
BASELINE_UPLOAD_SHA256 = '9ae2b3b503ad892c113688ee917675051cf88ce8e44ffd8c943d7a3e57e37d77'


def upload_checks(temp):
    import hashlib
    import re
    import runpy
    assert hashlib.sha256(BASELINE_UPLOAD.encode()).hexdigest() == BASELINE_UPLOAD_SHA256
    function = runpy.run_path(str(RUNTIME / 'car_lamp_public_test.py'))['function']
    source = (RUNTIME / 'materials.m').read_text()
    render = (RUNTIME / 'render.m').read_text()
    # Use the real table and registration macros, not a parallel list of lamp IDs.
    table = source[source.index('typedef struct MatShader {'):source.index('// Independent lamp term,')]
    pre = '''#include <stdio.h>
#include <string.h>
#include "materials.h"
#include "realworld_materials.h"
static int on, lamp_calls;
static int diag_on;
#define CAR_LOCAL_MATERIAL 2
static void car_locallight_diag(unsigned entry,int work){}
static int car_locallight_enabled(void){return on;}
static void car_locallight_constants(float k[52][4]){
 ++lamp_calls; if(on){k[13][3]=1;k[51][0]=123;}
}
'''
    for name in ('car_paint_msl', 'particles_msl'):
        pre += f'static const char {name}[]="{name}";\n'
    for name in ('car_paint_on', 'ripples_on', 'parallax_on', 'particles_always', 'car_vs_nm_on', 'car_t0_on'):
        pre += f'static int {name}(void){{return 1;}}\n'
    for name in ('particle_t0_patch', 'parallax_ps_patch', 'car_vs_cover_patch', 'car_vs_nm_patch',
                 'car_t0_ps_patch', 'ripple_vs_patch', 'ripple_ps_patch'):
        pre += f'static char *{name}(const char *s){{return (char *)s;}}\n'
    for name in ('particle_constants', 'parallax_constants', 'car_paint_constants', 'carbon_lamp_constants', 'ripple_constants'):
        pre += f'static void {name}(const float (*v)[4],MatConsts *k){{k->k[0][0]=7;}}\n'
    pre += 'void realworld_materials_constants(int id,const float (*v)[4],MatConsts *k){k->k[0][0]=7;}\n' + table
    harness = r'''
static unsigned draw(int id,int enabled,int *untouched){
 struct {int mat;} entry={id},*pse=&entry;
 struct {float vsc[256][4];} dev={0};
 for(int j=0;j<4;j++){dev.vsc[14][j]=14+j;dev.vsc[15][j]=15+j;}
 MatConsts mk;memset(&mk,0xa5,sizeof mk);on=enabled;lamp_calls=0;
 DRAW_CALL
 unsigned length=DRAW_LENGTH;
 *untouched=1;
 for(unsigned j=256;j<sizeof mk;j++)if(((unsigned char *)&mk)[j]!=0xa5)*untouched=0;
 return length;
}
int main(void){
 int failures=0,paint=0,t0=0,other=1,untouched=0;
 for(int i=0;i<sizeof table/sizeof *table;i++){
  if(table[i].msl==car_paint_msl)paint=i+1;
  if(table[i].patch==car_t0_ps_patch)t0=i+1;
 }
 if(!paint||!t0)return 2;
 for(int enabled=0;enabled<2;enabled++){
  int ids[]={paint,t0,other,MAT_REAL_T0,MAT_REAL_SHADOWED};
  for(int i=0;i<5;i++){
   unsigned length=draw(ids[i],enabled,&untouched),expected=enabled&&i<2?832:256;
   printf("upload %s %s=%u bytes tail_untouched=%d lamp_calls=%d\n",
      enabled?"On":"Off",i==0?"paint":i==1?"t0":"non-car",length,untouched,lamp_calls);
   if(length!=expected || (expected==256&&(!untouched||lamp_calls)))failures++;
  }
 }
 return failures?1:0;
}
'''
    call_pattern = r'^.*mat_constants\(pse->mat[^\n]+;'
    upload_pattern = r'\[enc setFragmentBytes:&mk length:([^\n]+?) atIndex:MAT_PS_BUF\];'
    for label, producer, draw in (('current', function(source, 'mat_constants'), render),
                                  ('d72caac0', function(BASELINE_UPLOAD, 'mat_constants'), BASELINE_UPLOAD)):
        call = re.search(call_pattern, draw, re.M)[0].strip()
        length = re.search(upload_pattern, draw)[1]
        code = pre + producer.replace('mat_constants(', 'upload_constants(') + '\n'
        code += harness.replace('DRAW_CALL', call.replace('mat_constants(', 'upload_constants(')).replace('DRAW_LENGTH', length)
        (temp / 'upload.c').write_text(code)
        subprocess.run(['clang', '-w', '-I' + str(RUNTIME), str(temp / 'upload.c'), '-o', str(temp / 'upload')],
                       check=True, capture_output=True)
        result = subprocess.run([str(temp / 'upload')], capture_output=True, text=True)
        print(label + ': ' + result.stdout.strip().replace('\n', ' | '))
        assert result.returncode == (0 if label == 'current' else 1), result.stdout
        if label == 'd72caac0':
            assert 'Off paint=832 bytes' in result.stdout and 'Off non-car=832 bytes' in result.stdout
    # Both replay consumers must preserve the payload size and still modify k[1].w.
    tee = render[render.index('@implementation SoftSmokeTee'):render.index('static RenderTex soft_proof_rt')]
    assert 'length==MAT_CONST_BASE_BYTES || length==sizeof(MatConsts)' in tee
    assert 'memcpy(&k,bytes,length)' in tee and 'length:length atIndex:index' in tee
    assert 'MAT_PS_BUF, MAT_CONST_BASE_BYTES, offsetof(MatConsts, k[1][3])' in (RUNTIME / 'soft_smoke_half.m').read_text()

def regression_control(temp):
    import hashlib
    assert hashlib.sha256(BASELINE_REAIM.encode()).hexdigest() == BASELINE_REAIM_SHA256
    baseline = r"""
#include "host.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
uint8_t *G;
static float luma(const float *c){return .2126f*c[0]+.7152f*c[1]+.0722f*c[2];}
int main(void){
 G=calloc(1,4096);uint32_t context=64;float level=1,sum=0;
 struct {float rgb[2][3],dir[2][3];}g={.rgb={{.8f,.8f,.8f},{0}},.dir={{0,.8f,0},{0}}};
 MF32(context+0x64)=-1;
 for(int k=0;k<3;k++)MF32(context+0x20+4*k)=.1f;
 float before=.1f;
""" + BASELINE_REAIM + r"""
 float after=MF32(context+0x24)*fminf(1,fmaxf(0,-MF32(context+0x64)+.1f));
 printf("frozen re-aim away: before=%.4f after=%.4f preservation=%s\n",before,after,after>=before?"PASS":"FAIL");
 return after>=before?0:1;
}
"""
    (temp / 'reaim.c').write_text(baseline)
    subprocess.run(['clang', '-w', '-I' + str(RUNTIME), '-I' + str(ROOT / 'recomp'), str(temp / 'reaim.c'), '-o', str(temp / 'reaim')], check=True, capture_output=True)
    control = subprocess.run([str(temp / 'reaim')], capture_output=True, text=True)
    print(control.stdout, end='')
    assert control.returncode == 1 and 'after=0.0000' in control.stdout, 'original re-aim escaped preservation check'


def shader_checks(temp):
    # The lamp constant layout and its material buffer must agree.
    assert 'CAR_LOCAL_LIGHT_MAX = 19, CAR_LOCAL_LIGHT_CONSTANTS = 52' in HEADER.read_text()
    import hashlib
    import runpy
    helpers = runpy.run_path(str(RUNTIME / 'car_lamp_public_test.py'))
    function, initializer, macro = (helpers[k] for k in ('function', 'initializer', 'macro'))
    source = (RUNTIME / 'materials.m').read_text()
    native = initializer(source, 'car_paint_msl')
    assert hashlib.sha256(native.encode()).hexdigest() == '9e0f38f83f93f1c851993bdd2d0d7fbda0d935ce816b4032c07cb5870840e9f3', 'native paint source changed'
    assert '00502030    car_locallight.c:' in (ROOT / 'recomp/hooks.txt').read_text()
    assert 'car_locallight_select(p && !M32(p) ? p : 0);' in function(source, 'f_006C7420')
    assert 'car_locallight_constants(out->k);' in function(source, 'mat_constants')
    dispatch = function(source, 'mat_replace_profile')
    assert 'src = car_local_shader(src, e->stage == SM_VERTEX, cover_vs || e->patch == car_t0_ps_patch);' in dispatch
    assert 'float k[52][4]' in (RUNTIME / 'materials.h').read_text()
    pre = '#include <stdio.h>\n#include <stdlib.h>\n#include <string.h>\n#include "../host/shaders/sm_coverage.h"\n'
    for name in ('car_shadow_off', 'pc_shader_lits', 'x360_shader_lits'):
        pre += f'#include "gen/{name}.inc"\n'
    pre += macro(source, 'CAR_PAINT_POW_MSL') + '\n'
    pre += 'static const char car_paint_msl[]=' + native
    pre += 'static const char car_local_math[]=' + initializer(source, 'car_local_math')
    pre += 'static int on; static int car_locallight_enabled(void){return on;}\nstatic int car_light_hdr(void){return 1;}\n'
    for name in ('dup_replace', 'edit', 'car_vs_patch', 'car_vs_nm_patch', 'car_t0_ps_patch', 'car_local_shader'):
        pre += function(source, name) + '\n'
    pre += r"""
int main(int argc,char **argv){
 if(argc!=5)return 2;FILE*f=fopen(argv[1],"rb");fseek(f,0,SEEK_END);long n=ftell(f);rewind(f);char*s=calloc(n+1,1);fread(s,1,n,f);fclose(f);
 int mode=atoi(argv[3]);char *base=NULL;
 if(mode==0)base=car_t0_ps_patch(s);
 if(mode==1)base=car_vs_patch(s);
 if(mode==2)base=car_vs_nm_patch(s);
 if(mode==3)base=strdup(car_paint_msl);
 if(!base)return 3;
 on=0;char*off=car_local_shader(strdup(base),mode==1||mode==2,mode<2);if(!off||strcmp(off,base))return 4;free(off);
 f=fopen(argv[4],"w");fputs(base,f);fclose(f);
 on=1;char*out=car_local_shader(base,mode==1||mode==2,mode<2);if(!out)return 5;
 f=fopen(argv[2],"w");fputs(out,f);fclose(f);free(out);free(s);return 0;
}
"""
    (temp / 'shader.c').write_text(pre)
    subprocess.run(['clang', '-w', '-I' + str(RUNTIME), str(temp / 'shader.c'), '-o', str(temp / 'shader')], check=True, capture_output=True)
    shader_dir = ROOT / 'host/shaders'
    # Translate the original car fixtures rather than trusting cached MSL.
    (temp / 'translate.cpp').write_text(helpers['TRANSLATOR'])
    subprocess.run(['clang++', '-std=c++17', '-O1', '-I', str(shader_dir), str(temp / 'translate.cpp'), str(shader_dir / 'sm_parse.cpp'), str(shader_dir / 'sm_msl.cpp'), '-o', str(temp / 'translate')], check=True, capture_output=True)
    for mode, stem in enumerate(('t0_car_p0_ps_1_1', 't0_car_p0_vs_1_1', 't1_car_p0_vs_1_1', 't1_car_p0_ps_2_0')):
        msl = temp / (stem + '.metal')
        subprocess.run([str(temp / 'translate'), str(shader_dir / 'testdata/car' / (stem + '.bin')), str(msl)], check=True, capture_output=True)
        subprocess.run([str(temp / 'shader'), str(msl), str(temp / f'on{mode}.metal'), str(mode), str(temp / f'off{mode}.metal')], check=True, capture_output=True)
        if mode != 3:
            result = subprocess.run(['xcrun', '-sdk', 'macosx', 'metal', '-fmodules-cache-path=' + str(ROOT / '.metal-cache'), '-c', str(temp / f'on{mode}.metal'), '-o', str(temp / f'on{mode}.air')], capture_output=True, text=True)
            assert result.returncode == 0, result.stderr[-4000:]
    subprocess.run(['clang', '-O1', '-fobjc-arc', str(RUNTIME / 'car_locallight_test.m'), '-framework', 'Foundation', '-framework', 'Metal', '-o', str(temp / 'gpu')], check=True, capture_output=True)
    for mode, no_shadow in ((3, 0), (0, 1)):
        result = subprocess.run([str(temp / 'gpu'), str(temp / f'off{mode}.metal'), str(temp / f'on{mode}.metal'), str(no_shadow)], capture_output=True, text=True)
        print(result.stderr[-4000:], end='')
        if result.returncode == 3:
            return 3
        assert result.returncode == 0, 'production lamp shader GPU check failed'
        mutant = temp / f'nearest{mode}.metal'
        patched = (temp / f'on{mode}.metal').read_text()
        assert 'min(uint(k[13].w), 19u)' in patched
        mutant.write_text(patched.replace('min(uint(k[13].w), 19u)', 'min(uint(k[13].w), 1u)'))
        broken = subprocess.run([str(temp / 'gpu'), str(temp / f'off{mode}.metal'), str(mutant), str(no_shadow)], capture_output=True, text=True)
        assert broken.returncode == 1 and 'brighter=0' in broken.stderr, 'one-light shader escaped last-light check: rc=%s %s' % (broken.returncode, broken.stderr[-1500:])
        print('GPU one-light control: ' + broken.stderr.strip().splitlines()[-1])
    print('PASS native paint hash; Off shader bytes exact (t0/t1 vertex, t0/plain/normal PS); zero/away GPU bytes exact')
    return 0


BASELINE_NEAREST = r'''
void car_locallight_gather(const float position[3], const float (*rows)[3], CarLocalLight *out) {
    memset(out, 0, sizeof *out);
    refresh();
    for (unsigned i = 0; i < lamp_count; ++i) {
        const Lamp *l = &lamps[i];
        float d[3], d2 = 0;
        for (int k = 0; k < 3; ++k) { d[k] = l->position[k] - position[k]; d2 += d[k] * d[k]; }
        if (!(d2 < l->radius * l->radius)) continue;
        float w = (l->gain > 0 ? l->gain / (2 * d2 + l->soft) : 1) * (1 - d2 / (l->radius * l->radius));
        float distance = sqrtf(d2);
        if (!out->lamps || distance < out->nearest) {
            out->nearest = distance;
            for (int k = 0; k < 3; ++k) {
                out->position[k] = rows ? d[0] * rows[k][0] + d[1] * rows[k][1] + d[2] * rows[k][2] : d[k];
                out->rgb[k] = l->rgb[k] * w;
                out->world[k] = l->position[k];
            }
        }
        ++out->lamps;
    }
    float total = luma(out->rgb), gain = LIGHT_GAIN;
    if (total * gain > TOTAL_LUMINANCE) gain = TOTAL_LUMINANCE / total;
    for (int k = 0; k < 3; ++k) out->rgb[k] *= gain;
}
'''
BASELINE_NEAREST_SHA256 = 'd18d4ff54638d47d716759441c0b21e055ef29f5047cf374910c5ed7b45f2ce8'

WALK = r'''

typedef struct {float rgb[3], position[3], world[3];unsigned lamps;float nearest;} OldLight;
BASELINE_FUNCTION
static int walk(int baseline) {
    G=calloc(1,0x920000); car_locallight_on=1; built=1; serial_seen=serial; packs_seen=pack_signature();
    Lamp pair[2]={{{0,0,4},22,{1,1,1},0,0},{{30,0,4},22,{1,1,1},0,0}};
    lamps=pair; lamp_count=2;
    float previous[3]={0},previous_level=0,max_angle=0,max_level=0,max_panel=0,prev_panel=0;
    for(int i=0;i<=300;i++) {
        float p[3]={i*.1f,0,0};CarLocalLight g;
        if(baseline) {
            OldLight old;baseline_gather(p,NULL,&old);memset(&g,0,sizeof g);g.lamps=old.lamps?1:0;
            memcpy(g.lights[0].rgb,old.rgb,sizeof old.rgb);memcpy(g.lights[0].position,old.position,sizeof old.position);
        } else car_locallight_gather(p,NULL,&g);
        // First angular moment describes the field, not a substitute point light:
        // shader-facing +X panel independently sums each positive cosine.
        float dir[3]={0},level=0,panel=0;
        for(unsigned j=0;j<g.lamps;j++) {
            const CarLocalLamp *l=&g.lights[j];float y=luma(l->rgb);
            float len=sqrtf(l->position[0]*l->position[0]+l->position[1]*l->position[1]+l->position[2]*l->position[2]);
            for(int k=0;k<3;k++)dir[k]+=y*l->position[k]/len;
            level+=y;panel+=y*fmaxf(0,l->position[0]/len);
        }
        float len=sqrtf(dir[0]*dir[0]+dir[1]*dir[1]+dir[2]*dir[2]);
        for(int k=0;k<3;k++)dir[k]/=len;
        float dot=previous[0]*dir[0]+previous[1]*dir[1]+previous[2]*dir[2];
        float angle=i?acosf(fmaxf(-1,fminf(1,dot)))*180/3.14159265f:0;
        if(angle>max_angle)max_angle=angle;
        if(i&&fabsf(level-previous_level)>max_level)max_level=fabsf(level-previous_level);
        if(i&&fabsf(panel-prev_panel)>max_panel)max_panel=fabsf(panel-prev_panel);
        printf("walk x=%.1f direction=%.6f,%.6f,%.6f level=%.6f panel=%.6f step_deg=%.6f\n",p[0],dir[0],dir[1],dir[2],level,panel,angle);
        memcpy(previous,dir,sizeof dir);previous_level=level;prev_panel=panel;
    }
    printf("%s max_direction_deg=%.6f max_level=%.6f max_panel=%.6f limits=3deg,0.01,0.02\n",baseline?"nearest":"sum",max_angle,max_level,max_panel);
    return !(max_angle<3 && max_level<.01f && max_panel<.02f);
}
int main(int argc,char **argv) {return walk(argc>1);}
'''

def walk_checks(temp):
    import hashlib
    assert hashlib.sha256(BASELINE_NEAREST.encode()).hexdigest() == BASELINE_NEAREST_SHA256
    original = BASELINE_NEAREST.replace('void car_locallight_gather(', 'void baseline_gather(').replace('CarLocalLight *out', 'OldLight *out')
    harness = HARNESS.split('int main(int argc,char **argv)')[0] + WALK.replace('BASELINE_FUNCTION', original)
    (temp / 'walk.c').write_text(harness)
    subprocess.run(['clang', '-w', '-std=c11', '-I' + str(RUNTIME), '-I' + str(ROOT / 'recomp'),
                    '-DTEST_SOURCE="' + str(SOURCE) + '"', str(temp / 'walk.c'), '-o', str(temp / 'walk')], check=True)
    for label, args, expected in (('sum', [], 0), ('nearest', ['nearest'], 1)):
        result = subprocess.run([str(temp / 'walk')] + args, capture_output=True, text=True)
        (temp / (label + '-walk.log')).write_text(result.stdout)
        print(result.stdout.splitlines()[-1])
        assert result.returncode == expected, label + ' walk check did not prove continuity/failure'


BASELINE_RING = r'''
static CarLampContext *find_context(uint32_t context) {
    for (unsigned i = 0; i < sizeof contexts / sizeof *contexts; ++i)
        if (contexts[i].context == context) return &contexts[i];
    return NULL;
}
static CarLampContext *write_context(uint32_t context) {
    CarLampContext *slot = find_context(context);
    if (!slot) slot = &contexts[next_context++ % (sizeof contexts / sizeof *contexts)];
    *slot = (CarLampContext){.context = context, .frame = diag_current_frame()};
    return slot;
}
'''
BASELINE_RING_SHA256 = '2858842937d0e6792aff79489bd1cbbaa44adf8902ca429038c92a7470c88551'

def context_control(temp):
    import hashlib
    assert hashlib.sha256(BASELINE_RING.encode()).hexdigest() == BASELINE_RING_SHA256
    text = SOURCE.read_text()
    text = text.replace('static CarLampContext initial_contexts[1024];\nstatic CarLampContext *contexts = initial_contexts;',
                        'static CarLampContext contexts[1024];\nstatic unsigned next_context;')
    begin = text.index('static unsigned context_hash(')
    end = text.index('void car_locallight_select(', begin)
    text = text[:begin] + BASELINE_RING + '\nstatic CarLampContext *lookup_context(uint32_t c,int write){return write?write_context(c):find_context(c);}\n' + text[end:]
    (temp / 'ring.c').write_text(text)
    subprocess.run(['clang', '-w', '-std=c11', '-I' + str(RUNTIME), '-I' + str(ROOT / 'recomp'),
                    '-DTEST_SOURCE="' + str(temp / 'ring.c') + '"', str(temp / 'harness.c'), '-o', str(temp / 'ring')], check=True)
    result = subprocess.run([str(temp / 'ring')], capture_output=True, text=True, env={'NFS_CAR_LOCALLIGHT':'1'})
    failures = result.stdout.count('FAIL')
    assert result.returncode == 1 and failures >= 1277, 'original ring escaped live-entry preservation check'
    print('frozen ring: 2301 queued contexts, preservation failures=%d; current: 0' % failures)


# Frozen Off entry points before the cached guards. No history is read by the check.
BASELINE_OFF = r'''
void f_00502030(Cpu *c) {
    uint32_t target = arg(c, 0), source = arg(c, 1), matrix = arg(c, 2);
    f_00502030_orig(c);
    car_locallight_clone(target, source, matrix);
}

void car_locallight_select(uint32_t context) {
    memset(&selected, 0, sizeof selected);
    if (!car_locallight_enabled()) return;
    CarLampContext *slot = lookup_context(context, 0);
    if (slot && slot->frame == diag_current_frame()) selected = slot->light;
}

void f_00502130(Cpu *c) {
    uint32_t context = arg(c, 0), rig = arg(c, 1), position = arg(c, 2), local_world = arg(c, 3);
    rig_by_hour(c);
    // Nearby scenery lights (car_locallight.c): only while the clock drives, at its night level.
    if (car_local_light && enabled && clock_state.ready && rig == CAR_RIG && (int32_t)c->eax == 1 && M32(FLOW) == 6)
        car_local_light(context, position, local_world, daynight_lamp_level());
}

unsigned mat_constants(int id, const float (*vsc)[4], MatConsts *out) {
    int lamps = id > 0 && id <= (int)(sizeof table / sizeof *table) &&
        (table[id - 1].msl == car_paint_msl || table[id - 1].patch == car_t0_ps_patch) && car_locallight_enabled();
    unsigned length = lamps ? sizeof *out : MAT_CONST_BASE_BYTES;
    memset(out, 0, length);
    if (id == MAT_REAL_T0 || id == MAT_REAL_SHADOWED) { realworld_materials_constants(id, vsc, out); return length; }
    if (id > 0 && id <= (int)(sizeof table / sizeof *table) && table[id - 1].constants) table[id - 1].constants(vsc, out);
    if (lamps) {
        car_locallight_constants(out->k);
        if (out->k[13][3] > 0 && table[id - 1].patch == car_t0_ps_patch) {
            memcpy(out->k[14], vsc[14], sizeof out->k[14]);
            memcpy(out->k[15], vsc[15], sizeof out->k[15]);
        }
    }
    return length;
}
'''
BASELINE_OFF_SHA256 = '3a71b8c51a7c0b98ad0970af3fe264c93c6d035f0d80434104b3e07301f942d3'

def off_work_checks(temp):
    """Count extra work, then benchmark the same entry points without counters."""
    import hashlib
    import runpy
    assert hashlib.sha256(BASELINE_OFF.encode()).hexdigest() == BASELINE_OFF_SHA256
    function = runpy.run_path(str(RUNTIME / 'car_lamp_public_test.py'))['function']
    current = '\n\n'.join(function(path.read_text(), name) for path, name in (
        (SOURCE, 'rotate_lit'), (SOURCE, 'rotate_diag'),
        (SOURCE, 'f_00502030'), (SOURCE, 'car_locallight_select'),
        (RUNTIME / 'daynight.c', 'setup_local_light'), (RUNTIME / 'daynight.c', 'setup_local_diag'),
        (RUNTIME / 'daynight.c', 'f_00502130'), (RUNTIME / 'materials.m', 'mat_constants')))
    pre = r'''
#include "host.h"
#include "car_locallight.h"
#include "materials.h"
#include "realworld_materials.h"
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
uint8_t *G;
int diag_on;
int enabled=1, car_locallight_on;
static unsigned work, original_calls, clone_calls, selected_calls, level_calls;
CarLocalLight selected;
typedef struct { uint64_t frame; CarLocalLight light; } CarLampContext;
static CarLampContext *lookup_context(uint32_t c,int write){++work;return NULL;}
uint64_t diag_current_frame(void){return 1;}
void diag_event(const char *f,...){(void)f;}
__attribute__((noinline)) void car_locallight_diag(unsigned e,int w){__asm__ volatile("" :: "r"(e),"r"(w) : "memory");}
__attribute__((noinline)) void f_00502030_orig(Cpu *c){++original_calls;c->eax=1;__asm__ volatile("" ::: "memory");}
__attribute__((noinline)) static void rig_by_hour(Cpu *c){++original_calls;c->eax=1;__asm__ volatile("" ::: "memory");}
__attribute__((noinline)) void car_locallight_clone(uint32_t a,uint32_t b,uint32_t m){++clone_calls;__asm__ volatile("" :: "r"(a),"r"(b),"r"(m) : "memory");}
static void apply(uint32_t a,uint32_t b,uint32_t m,float level){++selected_calls;}
void (*car_local_light)(uint32_t,uint32_t,uint32_t,float);
static struct {int ready;} clock_state={1};
#define CAR_RIG 0x8f2e70u
#define FLOW 0x100u
float daynight_lamp_level(void){++level_calls;return 1;}
static const char car_paint_msl[]="paint";
static char *car_t0_ps_patch(const char *s){return (char *)s;}
static void fill(const float (*v)[4],MatConsts *o){o->k[0][0]=7;}
static const struct {const char *msl;char *(*patch)(const char *);void (*constants)(const float (*)[4],MatConsts *);} table[]={
 {car_paint_msl,NULL,fill},{NULL,car_t0_ps_patch,fill},{NULL,NULL,fill}};
void realworld_materials_constants(int id,const float (*v)[4],MatConsts *o){fill(v,o);}
void car_locallight_constants(float k[52][4]){++work;}
static uint32_t checked_arg(Cpu *c,unsigned n){++work;return arg(c,n);}
static void *checked_clear(void *p,int n,size_t size){++work;return memset(p,n,size);}
'''
    main = r'''
static double seconds(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec+t.tv_nsec*1e-9;}
int main(int argc,char **argv){
 G=calloc(1,0x10000);Cpu c={.esp=0x1000};
 M32(c.esp+4)=0x2000;M32(c.esp+8)=CAR_RIG;M32(c.esp+12)=0x3000;M32(c.esp+16)=0x4000;M32(FLOW)=6;
 float v[256][4]={{0}};MatConsts out;
 // The old constructor registered the provider even when Off.
 car_local_light=BASELINE?apply:NULL;
 work=0;f_00502030(&c);unsigned rotate=work;
 work=0;car_locallight_select(0x2000);unsigned select=work;
 work=0;f_00502130(&c);unsigned setup=work;
 work=0;unsigned length=mat_constants(1,v,&out);unsigned material=work;
 printf("Off work rotate=%u select=%u setup=%u material=%u total=%u upload=%u clone_calls=%u level_calls=%u\n",
 rotate,select,setup,material,rotate+select+setup+material,length,clone_calls,level_calls);
 if(argc==1)return rotate+select+setup+material?1:0;
 unsigned n=3000000;
 for(int candidate=0;candidate<5;candidate++){
  double start=seconds();
  for(unsigned i=0;i<n;i++){
   switch(candidate){
    case 0:f_00502030(&c);break;
    case 1:car_locallight_select(0x2000);break;
    case 2:f_00502130(&c);break;
    case 3:mat_constants(1+(i%3),v,&out);break;
    case 4:rig_by_hour(&c);break;
   }
   __asm__ volatile("" ::: "memory");
  }
  printf("%s %.3f ns/call (%u iterations)\n",(const char*[]){"rotate","select","setup","material","original"}[candidate],(seconds()-start)*1e9/n,n);
 }
 free(G);return 0;
}
'''
    for label, bodies in (('before', BASELINE_OFF), ('current', current)):
        for instrument in (True, False):
            code = bodies
            # Count only eligibility reads introduced by the feature, not the
            # pre-existing material's constant callback lookup.
            if instrument:
                expression = '(table[id - 1].msl == car_paint_msl || table[id - 1].patch == car_t0_ps_patch)'
                assert expression in code
                code = code.replace(expression, '(++work, ' + expression + ')')
                code = code.replace('arg(c,', 'checked_arg(c,').replace('memset(&selected,', 'checked_clear(&selected,')
            # Keep the ABI boundary visible; the production build has no LTO.
            for signature in ('void f_00502030(', 'void car_locallight_select(', 'void f_00502130(', 'unsigned mat_constants('):
                code = code.replace(signature, '__attribute__((noinline)) ' + signature)
            code = pre + '\n' + code + '\n' + main.replace('BASELINE', '1' if label == 'before' else '0')
            path = temp / 'off-work.c'
            path.write_text(code)
            binary = temp / 'off-work'
            subprocess.run(['clang', '-w', '-O2', '-I' + str(RUNTIME), '-I' + str(ROOT / 'recomp'),
                            str(path), '-o', str(binary)], check=True, capture_output=True)
            result = subprocess.run([str(binary)] + ([] if instrument else ['bench']), capture_output=True, text=True, env={'NFS_CAR_LOCALLIGHT': '0'})
            print(label + (' counted: ' if instrument else ' benchmark: ') + result.stdout.strip().replace('\n', ' | '))
            expected = 1 if instrument and label == 'before' else 0
            assert result.returncode == expected, result.stdout
            if instrument and label == 'before':
                assert 'total=9 ' in result.stdout, result.stdout


def run():
    with tempfile.TemporaryDirectory(prefix='carlight-') as tmp:
        temp = Path(tmp)
        (temp / 'harness.c').write_text(HARNESS)
        cmd = ['clang', '-w', '-std=c11', '-I' + str(RUNTIME), '-I' + str(ROOT / 'recomp'), str(temp / 'harness.c'),
               '-DTEST_SOURCE="' + str(SOURCE) + '"']
        results = {}
        for label, flags, args, env in (('normal', [], [], '1'), ('default', [], [], None), ('off', [], ['off'], '0'),
                                        ('broken', ['-DCAR_LOCALLIGHT_BROKEN'], [], '1')):
            binary = temp / label
            subprocess.run(cmd + flags + ['-o', str(binary)], check=True)
            environment = {'NFS_CAR_LOCALLIGHT': env} if env else {}
            results[label] = subprocess.run([str(binary)] + args, capture_output=True, text=True, env=environment)
            print(label, ' | '.join(results[label].stdout.strip().splitlines()[-2:]))
        for label in ('normal', 'default', 'off'):
            if results[label].returncode:
                raise AssertionError(results[label].stdout)
        assert results['broken'].returncode, 'zeroed coefficients passed'
        assert 'lamp_green=0.0000' in results['broken'].stdout
        off_work_checks(temp)
        context_control(temp)
        walk_checks(temp)
        regression_control(temp)
        upload_checks(temp)
        if shader_checks(temp) == 3:
            return 3
    print('PASS car_locallight: weight, de-duplication, car axes, summed lamps, rig/Off/level 0 untouched; broken control fails')
    return 0


if __name__ == '__main__':
    if sys.argv[1:] == ['--off-work']:
        with tempfile.TemporaryDirectory(prefix='carlight-off-') as tmp:
            off_work_checks(Path(tmp))
    elif len(sys.argv) == 5 and sys.argv[1] == '--street-proof':
        street_proof(*sys.argv[2:])
    elif len(sys.argv) == 6 and sys.argv[1] == '--proof':
        proof(*sys.argv[2:])
    else:
        raise SystemExit(run())
