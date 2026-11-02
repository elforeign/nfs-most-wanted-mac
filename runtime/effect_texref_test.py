#!/usr/bin/env python3
"""Effect parameters hold the textures set on them; a sampler slot never builds a texture from a buffer.

Compiles the production d3dx_effect.c with a counting stand-in for d3d9.m's AddRef/Release (a resource at 0 is freed)
and drives e_SetTexture / e_GetTexture / e_SetValue / e_Release as the game does: set a texture, the app releases its own reference
(texture must live: count 1, not freed), set another (first freed), release the effect (second freed).
Broken control: the ORIGINAL e_SetTexture (pointer stored, no reference; SHA-256 below) in the same harness.
GetTexture must return an owned reference, including when the caller releases it immediately.
Rim mask (realworld_materials.c, stale mask handle after the guest frees it): the ORIGINAL store (SHA below) fails 2 of 2
rim checks (exit 1), the fixed one 0 of 2.
Numbers: fixed 0 failed of 11 checks (exit 0); original SetTexture 7 failed of 11 (exit 1);
original GetTexture 3 failed of 11 (exit 1: missing caller reference frees the effect-held texture early).
render.m is Metal and cannot build offline: its guard is checked as source (gpu_tex refuses a 0 x 0 object, the sampler
loop binds only texture kinds). The ORIGINAL sampler-loop head and gpu_tex head (SHA-256 below) fail the same check.
"""
import hashlib
import re
import subprocess
import sys
import tempfile
from pathlib import Path

RT = Path(__file__).resolve().parent
ORIG_SET = '''static int e_SetTexture(Cpu *c) {              // (this, h, texture)
    Param *p = param_of(FX, arg(c, 1));
    if (!p || p->cls != PC_OBJECT) RET(D3DERR_INVALIDCALL, 3);
    memcpy(p->data, &(uint32_t){arg(c, 2)}, 4);
    touch(p);
    RET(D3D_OK, 3);
}
'''
ORIG_SET_SHA = '939d1d3934f52216c9fd1614672648295ab14d4a0fe2ba14aa9f5aeb02fa931d'
ORIG_SAMPLER = '''        Obj *t = obj(dev.tex[s]);
        RenderTex *g = t ? gpu_tex(t) : NULL;
'''
ORIG_SAMPLER_SHA = 'e25267aecb34e62b72a77de2c62d1d146bf1b0531cb483b727f960015b9e0279'
ORIG_GPU = '''RenderTex *gpu_tex(Obj *t) {
    if (!t) return NULL;
    RenderTex *g = t->gpu;
'''
ORIG_GPU_SHA = '9069d3f4a1fb9c594eda51bb77d5657ca54ae4bd459cf78af8eca3150d34a5ca'

ORIG_GET = 'static int e_GetTexture(Cpu *c) {\n    Param *p = param_of(FX, arg(c, 1));\n    if (!p || p->cls != PC_OBJECT) RET(D3DERR_INVALIDCALL, 3);\n    memcpy(G + arg(c, 2), p->data, 4);\n    RET(D3D_OK, 3);\n}\n'
ORIG_GET_SHA = '37f657e0a347abc2ff42bdfad457983bfd6d715fffced38f909b8b01a4e21d14'

ORIG_RIM = '''    r->painted = painted; r->mask = mask; r->paletted = paletted;
'''
ORIG_RIM_SHA = 'a86cfb9f6c7be0314ba5fb2b027cb42fbdd9a6387ec145578de31ce9adcbdc22'

RIM = r'''
#include <stdlib.h>
#include <stdio.h>
#include "host.h"
#include "traffic_detail.c"
#include "rim_under_test.c"
uint8_t *G;
int x360_post_rolloff(void) { return 0; }
static int refs[16], freed[16];
static void add(uint32_t g) { ++refs[g >> 12]; }
static void rel(uint32_t g) { if (--refs[g >> 12] == 0) freed[g >> 12] = 1; }
int main(void) {
    int bad = 0;
    realworld_materials_set_hold(add, rel);
    refs[1] = refs[2] = 1;
    realworld_materials_rim_texture(17, 1, 0x1000, 1);
    rel(0x1000);                                   // streaming frees the game's own reference
    if (refs[1] != 1 || freed[1]) { ++bad; printf("FAIL: stored rim mask freed under the renderer\n"); }
    realworld_materials_rim_texture(17, 1, 0x2000, 1);
    if (!freed[1] || refs[2] != 2) { ++bad; printf("FAIL: replaced mask not released / new not held\n"); }
    printf("%d failed of 2 rim checks\n", bad);
    return bad ? 1 : 0;
}
'''

HARNESS = r'''
#include <stdlib.h>
#include <stdio.h>
#include "d3dx_effect_under_test.c"
uint8_t *G; int boot_trace;
uint32_t galloc(uint32_t n) { static uint32_t top = 0x40000; uint32_t a = top; top += (n + 15) & ~15u; return a; }
uint32_t gstr(const char *s) { (void)s; return 0; }
uint32_t host_fn(HostFn f, const char *n) { (void)f; (void)n; return 0; }
uint32_t call_guest(Cpu *c, uint32_t f, int n, const uint32_t *a) { (void)c; (void)f; (void)n; (void)a; return 0; }
void widescreen_drop_offset(Cpu *c, uint32_t a) { (void)c; (void)a; }
float lighting_inv_shadow_strength(float f) { return f; }
static int refs[16], freed[16];                     // stand-in for d3d9.m: texture k lives at 0x1000 * k
#define K(g) ((g) >> 12)
void d3d9_addref_guest(uint32_t g) { if (g) ++refs[K(g)]; }
void d3d9_release_guest(uint32_t g) { if (g && --refs[K(g)] == 0) freed[K(g)] = 1; }
static Cpu cpu;
static void call(int (*fn)(Cpu *), int n, uint32_t a0, uint32_t a1, uint32_t a2) {
    cpu.esp = 0x8000; M32(cpu.esp) = 0; M32(cpu.esp + 4) = a0; M32(cpu.esp + 8) = a1; M32(cpu.esp + 12) = a2; (void)n;
    fn(&cpu);
}
static int bad;
#define CHECK(cond, what) do { if (!(cond)) { ++bad; printf("FAIL: %s\n", what); } } while (0)
int main(void) {
    G = calloc(1, 1 << 20);
    Effect *fx = calloc(1, sizeof *fx);
    fx->refs = 1; fx->nparams = 1; fx->params = calloc(1, sizeof(Param));
    Param *p = &fx->params[0];
    p->cls = PC_OBJECT; p->type = PT_TEXTURE2D; p->fx = fx; p->data = data_alloc(4);
    uint32_t h = new_handle(1, p);
    effects = malloc(sizeof *effects); effects[0] = fx; neffects = 1;
    fx->guest = 0x2000 * 0 + 0x9000; M32(fx->guest + 4) = 0; M32(fx->guest + 8) = 0x0EFFEC70;
    const uint32_t A = 0x1000, B = 0x2000, C = 0x3000;
    refs[1] = refs[2] = refs[3] = 1;                // each held only by the app
    call(e_SetTexture, 3, fx->guest, h, A);
    CHECK(refs[1] == 2, "set: effect takes a reference (count 2)");
    call(e_GetTexture, 3, fx->guest, h, 0x7100);
    CHECK(M32(0x7100) == A && refs[1] == 3, "GetTexture returns an owned reference");
    d3d9_release_guest(M32(0x7100));
    CHECK(refs[1] == 2 && !freed[1], "caller releases GetTexture result; effect hold survives");
    d3d9_release_guest(A);                           // the app drops its own reference (texture streamed out)
    CHECK(refs[1] == 1 && !freed[1], "app release: texture alive with count 1");
    call(e_SetTexture, 3, fx->guest, h, B);
    CHECK(freed[1], "next set: the first texture is released and freed");
    d3d9_release_guest(B);
    CHECK(refs[2] == 1 && !freed[2], "second texture alive with count 1");
    M32(0x7000) = C;
    call(e_SetValue, 4, fx->guest, h, 0x7000);       // SetValue(h, &texture, 4): same rule
    CHECK(refs[3] == 2 && freed[2], "SetValue: new texture held, previous freed");
    call(e_SetTexture, 3, fx->guest, h, 0);
    CHECK(refs[3] == 1 && !freed[3], "set NULL releases the hold only");
    call(e_GetTexture, 3, fx->guest, h, 0x7100);
    CHECK(M32(0x7100) == 0 && refs[3] == 1, "GetTexture NULL adds no reference");
    call(e_SetTexture, 3, fx->guest, h, B + 0x0);    // B is dead already: model a stale pointer set again, then effect death
    refs[2] = 1; freed[2] = 0;
    call(e_Release, 1, fx->guest, 0, 0);
    CHECK(refs[2] == 0 && freed[2] == 1, "effect release: held texture freed");
    call(e_Release, 1, fx->guest, 0, 0);
    CHECK(refs[2] == 0, "second effect release: no double release");
    printf("%d failed of 11 checks\n", bad);
    return bad ? 1 : 0;
}
'''


def sha(s):
    return hashlib.sha256(s.encode()).hexdigest()


def build_run(tmp, name, text):
    tmp = Path(tmp)
    (tmp / 'd3dx_effect_under_test.c').write_text(text)
    (tmp / (name + '.c')).write_text(HARNESS)
    exe = tmp / name
    r = subprocess.run(['clang', '-std=gnu11', '-O0', '-g', '-fsanitize=address,undefined', '-w', '-I', str(RT), '-I', str(RT.parent / 'recomp'),
                        '-I', str(tmp), str(tmp / (name + '.c')), '-o', str(exe)], capture_output=True, text=True)
    if r.returncode:
        print(r.stderr); sys.exit(2)
    r = subprocess.run([str(exe)], capture_output=True, text=True)
    print(f'{name}: exit {r.returncode}: ' + r.stdout.strip().replace('\n', ' | '))
    return r.returncode


def build_rim(tmp, name, text):
    tmp = Path(tmp)
    (tmp / 'rim_under_test.c').write_text(text)
    (tmp / (name + '.c')).write_text(RIM)
    r = subprocess.run(['clang', '-std=gnu11', '-O0', '-w', '-I', str(RT), '-I', str(RT.parent / 'recomp'), '-I', str(tmp),
                        str(tmp / (name + '.c')), '-o', str(tmp / name)], capture_output=True, text=True)
    if r.returncode:
        print(r.stderr); sys.exit(2)
    r = subprocess.run([str(tmp / name)], capture_output=True, text=True)
    print(f'{name}: exit {r.returncode}: ' + r.stdout.strip().replace('\n', ' | '))
    return r.returncode


def render_guarded(text):
    gpu = text.split('RenderTex *gpu_tex(Obj *t) {', 1)[1][:400]
    loop = text.split('Obj *t = obj(dev.tex[s]);', 1)[1].split('RenderTex *g = t ? gpu_tex(t)', 1)[0]
    return '!t->w || !t->h' in gpu and 'K_TEXTURE' in loop and 'K_VOLUME' in loop and 'K_CUBE' in loop and 'K_SURFACE' in loop


def main():
    for s, h in ((ORIG_SET, ORIG_SET_SHA), (ORIG_SAMPLER, ORIG_SAMPLER_SHA), (ORIG_GPU, ORIG_GPU_SHA), (ORIG_GET, ORIG_GET_SHA)):
        assert sha(s) == h, 'pinned original changed'
    fx = (RT / 'd3dx_effect.c').read_text()
    a, b = fx.index('static int e_SetTexture'), fx.index('static int e_GetTexture')
    assert 'set_object' in fx[a:b], 'e_SetTexture no longer holds the texture'
    broken = fx[:a] + ORIG_SET + fx[b:]
    with tempfile.TemporaryDirectory(prefix='efftexref-') as d:
        if build_run(d, 'broken', broken) != 1:
            print('FAIL: the original e_SetTexture must fail the checks'); return 1
        ga = fx.index('static int e_GetTexture'); gb = fx.index('static int e_SetArrayRange', ga)
        if build_run(d, 'get_broken', fx[:ga] + ORIG_GET + fx[gb:]) != 1:
            print('FAIL: the original GetTexture must fail the ownership checks'); return 1
        if build_run(d, 'fixed', fx) != 0:
            return 1
        assert sha(ORIG_RIM) == ORIG_RIM_SHA, 'pinned original changed'
        rw = (RT / 'realworld_materials.c').read_text()
        a = rw.index('    if (r->mask != mask) {'); b = rw.index('    r->painted = painted; r->mask = mask;', a)
        e = rw.index('\n', b) + 1
        if build_rim(d, 'rim_broken', rw[:a] + ORIG_RIM + rw[e:]) != 1:
            print('FAIL: the original rim-mask store must fail'); return 1
        if build_rim(d, 'rim_fixed', rw) != 0:
            return 1
    r = (RT / 'render.m').read_text()
    assert render_guarded(r), 'render.m: gpu_tex or the sampler loop lacks the non-texture / 0x0 guard'
    g0, g1 = r.index('RenderTex *gpu_tex(Obj *t) {'), r.index('    RenderTex *g = t->gpu;')
    l0, l1 = r.index('        Obj *t = obj(dev.tex[s]);\n        if (t && t->kind'), r.index('        RenderTex *g = t ? gpu_tex(t) : NULL;\n        id<MTLTexture> tx')
    old = r[:g0] + ORIG_GPU + r[g1 + len('    RenderTex *g = t->gpu;\n'):l0] + ORIG_SAMPLER + r[l1 + len('        RenderTex *g = t ? gpu_tex(t) : NULL;\n'):]
    assert not render_guarded(old), 'guard check does not fail without the guard'
    print('PASS: originals fail, fixed hold/release (effect textures, rim mask); render guard present and detected missing when removed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
