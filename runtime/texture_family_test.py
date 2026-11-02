#!/usr/bin/env python3
"""Stale texture family: execute production reference stores and pre-Metal guards.

The C harness extracts the real functions / bind heads, with counting D3D
references and a stand-in at the Metal payload boundary. No Metal device is
needed: refusal must happen before that boundary. Baselines are original,
SHA-256-pinned fragments, never repository history.
Counts (failed/total, original -> fixed): GPU 7/15 -> 0/15; bone ownership
6/8 -> 0/8; each bone (2D and cube), normal and vertex bind 2/5 -> 0/5.
All original C runs
exit 1 and fixed runs exit 0; lifecycle source checks fail 2/2 -> 0/2.
The sibling effect_texref_test covers GetTexture ownership separately.
Device reset / final Release hooks are checked independently, with pinned
original hook fragments as negative controls.
"""
import hashlib
from pathlib import Path
import re
import subprocess
import tempfile

RT = Path(__file__).resolve().parent

ORIGINAL = {
    'bone': '''void mat_bone_car_cube(uint32_t tex) {
    uint32_t fx = M32(CUR_EFFECT);
    if (fx && M32(fx + 4) == 4) bone_cube = tex;
}
''',
    'gpu': '''RenderTex *gpu_tex(Obj *t) {
    if (!t) return NULL;
    if (!t->gpu && (!t->w || !t->h)) return NULL;   // Metal aborts on a 0-sized descriptor
''',
    'bone_bind': '''    Obj *t = obj(guest);
    RenderTex *g = t ? gpu_tex(t) : NULL;
''',
    'normal_bind': '''        Obj *nt = obj(mat_carnm_texture());
        RenderTex *ng = nt ? gpu_tex(nt) : NULL;
''',
    'vertex_bind': '''        Obj *t = obj(dev.tex[16 + s]);
        RenderTex *g = t ? gpu_tex(t) : NULL;
''',
    'reset': '''static int v_Reset(Cpu *c) {
    memcpy(dev.pp, G + arg(c, 1), sizeof dev.pp);
''',
    'release': '''    if (--o->refs == 0 && o != dev.self && (o->kind == K_TEXTURE || o->kind == K_CUBE || o->kind == K_VOLUME || o->kind == K_VB ||
''',
}
SHA = {
    'bone': '58294e19c7ba23e5a944cc1c06236f8c678583b74a2c22bf5c281907e56077ff',
    'gpu': 'e86d892330731618afb1006dcfca7a79b890b07e86b553be0ccb6741be1392b8',
    'bone_bind': '6ead9cfe95de2120eaba4f36a6dc89fa4ebb245da05d44a162a63bb32d032fc6',
    'normal_bind': 'cf87b42ea6a9ef4b5c43e02a6601eade9c0d6c0adb5ad46acfc707a4b9010eee',
    'vertex_bind': 'b0101304ebdf3a184922c6e7ef5fe357a194253a41a1fec03b11a72d094aa243',
    'reset': '40b36be8ae0176a16aae481cbb20e37be004f5a616005848ebf288bf476c3f3d',
    'release': '78c64c1e38f1c057b26187091f1767e77e701ab50e420019a1240844c8be74af',
}


def function(text, name):
    start = text.index(name)
    start = text.rfind('\n', 0, start) + 1
    brace = text.index('{', start)
    depth = 1
    end = brace + 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end] + '\n'


def bind_head(text, marker, end):
    start = text.index(marker)
    start = text.rfind('\n', 0, start) + 1
    stop = text.index(end, start) + len(end)
    return text[start:stop] + '\n'


PRELUDE = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
static int bad, checks;
#define CHECK(c, what) do { ++checks; if (!(c)) { ++bad; printf("FAIL: %s\n", what); } } while (0)
static int diag_on = 1;
static uint32_t bad_tex_binds;
typedef struct { int unused; } RenderTex;
static RenderTex payload;
static Obj input;
static int payload_reads, unsafe_calls;
static Obj *obj(uint32_t g) { return g ? &input : NULL; }
static uint32_t mat_carnm_texture(void) { return 1; }
static struct { uint32_t tex[20]; } dev;
'''

GUARD_MAIN = r'''
int main(void) {
    CHECK(!gpu_tex(NULL), "null object refused");
    const int valid[] = {K_TEXTURE, K_CUBE, K_VOLUME, K_SURFACE};
    for (unsigned i = 0; i < sizeof valid / sizeof *valid; ++i) {
        input = (Obj){.kind=valid[i], .w=8, .h=8, .d=8, .gpu=&payload};
        CHECK(gpu_tex(&input) == &payload, "valid cached texture accepted");
    }
    for (int kind = K_VB; kind <= K_IB; ++kind) {
        input = (Obj){.kind=kind, .w=8, .h=8, .gpu=&payload};
        int reads = payload_reads;
        CHECK(!gpu_tex(&input) && reads == payload_reads, "buffer refused before GPU payload access");
    }
    for (int cached = 0; cached < 2; ++cached) {
        for (int dimension = 0; dimension < 3; ++dimension) {
            input = (Obj){.kind=K_VOLUME, .w=8, .h=8, .d=8, .gpu=cached ? &payload : NULL};
            if (dimension == 0) input.w = 0;
            if (dimension == 1) input.h = 0;
            if (dimension == 2) input.d = 0;
            int reads = payload_reads;
            CHECK(!gpu_tex(&input) && reads == payload_reads, "empty object refused with or without GPU payload");
        }
    }
    CHECK(bad_tex_binds == 8, "invalid and empty lookups counted once");
    diag_on = 0;
    input.kind = K_VB;
    uint32_t count = bad_tex_binds;
    (void)gpu_tex(&input);
    CHECK(count == bad_tex_binds, "diagnostic counter idle when disabled");
    printf("%d failed of %d GPU checks\n", bad, checks);
    return bad ? 1 : 0;
}
'''

BIND_MAIN = r'''
int main(void) {
    dev.tex[16] = 1;
    for (int kind = K_VB; kind <= K_IB; ++kind) {
        input = (Obj){.kind=kind, .w=8, .h=8, .d=8, .gpu=&payload};
        int calls = unsafe_calls;
        CHECK(!lookup() && unsafe_calls == calls, "bind refuses buffer before gpu_tex");
    }
    input = (Obj){.kind=K_TEXTURE, .w=8, .h=8, .d=8, .gpu=&payload};
    CHECK(lookup() == &payload, "bind accepts a live texture");
    input.w = 0;
    CHECK(!lookup(), "bind refuses empty cached texture");
    diag_on = 0;
    input.kind = K_VB;
    uint32_t count = bad_tex_binds;
    (void)lookup();
    CHECK(count == bad_tex_binds, "bind counter idle when disabled");
    printf("%d failed of %d bind checks\n", bad, checks);
    return bad ? 1 : 0;
}
'''

BONE_MAIN = r'''
static uint8_t *G;
#define M32(a) (*(uint32_t *)(G + (a)))
#define CUR_EFFECT 0x00982C80u
static uint32_t bone_cube;
static int refs[4], adds, releases;
void d3d9_addref_guest(uint32_t g) { ++refs[g]; ++adds; }
void d3d9_release_guest(uint32_t g) { --refs[g]; ++releases; }
__FUNCTIONS__
int main(void) {
    G = calloc(1, 1 << 24);
    M32(CUR_EFFECT) = 0x100; M32(0x104) = 4;
    refs[1] = refs[2] = 1;
    mat_bone_car_cube(1);
    CHECK(refs[1] == 2 && bone_cube == 1, "cache takes a guest reference");
    int before = adds;
    mat_bone_car_cube(1);
    CHECK(adds == before && refs[1] == 2, "same texture does not acquire twice");
    d3d9_release_guest(1);
    CHECK(refs[1] == 1, "cube survives app release across frames");
    M32(0x104) = 2; mat_bone_car_cube(2);
    CHECK(bone_cube == 1 && refs[2] == 1, "non-car draw does not replace cache");
    M32(0x104) = 4; mat_bone_car_cube(2);
    CHECK(refs[1] == 0 && refs[2] == 2, "replace releases old and holds new cube");
    mat_bone_reset();
    CHECK(!bone_cube && refs[2] == 1, "teardown clears and releases cube");
    before = releases; mat_bone_reset();
    CHECK(releases == before, "repeated teardown does not release twice");
    mat_bone_car_cube(2); mat_bone_car_cube(0);
    CHECK(!bone_cube && refs[2] == 1, "NULL replacement releases cache hold");
    free(G);
    printf("%d failed of %d bone checks\n", bad, checks);
    return bad ? 1 : 0;
}
'''


def run(tmp, name, source, expected):
    c = tmp / (name + '.c')
    exe = tmp / name
    c.write_text(source)
    result = subprocess.run(['clang', '-std=c11', '-O0', '-g', '-fsanitize=address,undefined',
                             '-Wall', '-Wextra', '-Wno-unused-function', '-Wno-unused-variable',
                             str(c), '-o', str(exe)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[:4000]
    result = subprocess.run([str(exe)], capture_output=True, text=True)
    print(f'{name}: exit {result.returncode}: ' + result.stdout.strip().replace('\n', ' | '))
    assert result.returncode == expected, result.stderr[:4000]


def main():
    assert set(SHA) == set(ORIGINAL)
    for name, fragment in ORIGINAL.items():
        assert hashlib.sha256(fragment.encode()).hexdigest() == SHA[name], name
    material = (RT / 'materials.m').read_text()
    render = (RT / 'render.m').read_text()
    d3d = (RT / 'd3d9.m').read_text()
    header = (RT / 'd3d9_internal.h').read_text()
    types = re.search(r'enum Kind \{.*?\};', header, re.S).group()
    types += '\n' + re.search(r'typedef struct Obj \{.*?\} Obj;', header, re.S).group()
    prefix = '#include <stdint.h>\n' + types + '\n' + PRELUDE
    gpu = render[render.index('RenderTex *gpu_tex(Obj *t) {'):render.index('    RenderTex *g = t->gpu;')]
    # No payload dereference is necessary: reaching this line with a RenderBuf
    # is itself the error, before Metal messages / the RenderTex.used write.
    tail = '    ++payload_reads; return t->gpu ? t->gpu : &payload;\n}\n'
    reset = function(material, 'void mat_bone_reset(')
    store = function(material, 'void mat_bone_car_cube(')
    heads = {
        'bone_bind': bind_head(render[render.index('static id<MTLTexture> bone_tex('):], 'Obj *t = obj(guest);', 'RenderTex *g = t ? gpu_tex(t) : NULL;'),
        'normal_bind': bind_head(render, 'Obj *nt = obj(mat_carnm_texture());', 'RenderTex *ng = nt ? gpu_tex(nt) : NULL;'),
        'vertex_bind': bind_head(render, 'Obj *t = obj(dev.tex[16 + s]);', 'RenderTex *g = t ? gpu_tex(t) : NULL;'),
    }
    with tempfile.TemporaryDirectory(prefix='texture-family-') as directory:
        tmp = Path(directory)
        run(tmp, 'gpu_original', prefix + ORIGINAL['gpu'] + tail + GUARD_MAIN, 1)
        run(tmp, 'gpu_fixed', prefix + gpu + tail + GUARD_MAIN, 0)
        run(tmp, 'bone_original', prefix + BONE_MAIN.replace('__FUNCTIONS__', ORIGINAL['bone'] + reset), 1)
        run(tmp, 'bone_fixed', prefix + BONE_MAIN.replace('__FUNCTIONS__', store + reset), 0)
        # Instrument the call boundary independently of the gpu_tex guard:
        # a local missing kind check must fail even when gpu_tex is defensive.
        checked_gpu = gpu.replace('gpu_tex(', 'checked_gpu(') + tail
        checked_gpu += '''static RenderTex *gpu_tex(Obj *t) {
    if (t && (t->kind == K_VB || t->kind == K_IB)) ++unsafe_calls;
    return checked_gpu(t);
}
'''
        for name, head in heads.items():
            returned = 'ng' if name == 'normal_bind' else 'g'
            for suffix, fragment, status in [('original', ORIGINAL[name], 1), ('fixed', head, 0)]:
                for cube in ([0, 1] if name == 'bone_bind' else [0]):
                    wrapper = f'static RenderTex *lookup(void) {{ int cube = {cube}, s = 0; uint32_t guest = 1;\n'
                    wrapper += fragment + f'return {returned};\n}}\n'
                    main = BIND_MAIN.replace('.kind=K_TEXTURE,', '.kind=K_CUBE,') if cube else BIND_MAIN
                    run(tmp, name + ('_cube' if cube else '') + '_' + suffix, prefix + checked_gpu + wrapper + main, status)
    # Exercise both lifecycle hook checks against their pinned predecessor.
    def hooks(source):
        return ('mat_bone_reset();' in function(source, 'static int v_Reset('),
                bool(re.search(r'if \(--o->refs == 0 && o == dev.self\) mat_bone_reset\(\);', source)))
    assert hooks(d3d) == (True, True)
    reset_fn = function(d3d, 'static int v_Reset(')
    broken_reset = d3d.replace(reset_fn, ORIGINAL['reset'] + reset_fn.split('    memcpy(dev.pp, G + arg(c, 1), sizeof dev.pp);\n', 1)[1])
    assert hooks(broken_reset) == (False, True)
    broken_release = re.sub(r'    if \(--o->refs == 0 && o == dev.self\) mat_bone_reset\(\);\n    if \(o->refs == 0[^\n]*\n', ORIGINAL['release'], d3d, count=1)
    assert hooks(broken_release) == (True, False)
    assert 'void mat_bone_reset(void);' in (RT / 'materials.h').read_text()
    print('PASS: texture family; reset/release hooks fixed 2/2, originals 0/2')


if __name__ == '__main__':
    main()
