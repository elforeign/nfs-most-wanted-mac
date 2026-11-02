#!/usr/bin/env python3
"""Native managed environment faces retain their CPU loader's dimensions.

Original -> fixed: synthetic 256-square face centre 0 -> 231 (source 231);
CARLOT_YNEG centre 0 -> 255 (source 255).
Six complete private CARLOT faces must equal their source, not only the centre.
The original CreateCubeTexture body is SHA-256 pinned and executed as the broken
control. All other creation descriptors and guest bytes, and every PC creation,
must be byte-identical to that body. Dynamic captures stay 1024 / 11 mips.
Private faces missing: exit 3, after the asset-independent checks.
"""
import ctypes as ct
import hashlib
import itertools
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RT = ROOT / 'runtime'


def function(source, name):
    m = re.search(r'^static [^\n]*\b' + name + r'\([^{};]*\) \{', source, re.M)
    assert m, name
    start = source.index('{', m.start()); depth = 0
    for token in re.finditer(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/|[{}]', source[start:], re.S):
        if token[0] == '{': depth += 1
        elif token[0] == '}':
            depth -= 1
            if not depth: return source[m.start():start + token.end()]
    raise AssertionError(name)


ORIGINAL = '''static int v_CreateCubeTexture(Cpu *c) {       // (this, edge, levels, usage, fmt, pool, pp, shared)
    uint32_t levels = envcube_creation_levels(arg(c, 1), arg(c, 2), arg(c, 3), arg(c, 4));
    Obj *t = make_texture(K_CUBE, vt_cube, arg(c, 1), arg(c, 1), 1, levels, arg(c, 3), arg(c, 4), arg(c, 5));
    envcube_created(t, M32(c->esp), arg(c, 6));
    M32(arg(c, 6)) = t->guest;
    RET(D3D_OK, 8);
}'''

HARNESS = r'''
#include <stdint.h>
#include <string.h>
static uint32_t G[128];
#define M32(p) G[(p)/4]
typedef struct {uint32_t esp;} Cpu;
typedef struct {uint32_t guest,kind,vt,w,h,d,levels,usage,fmt,pool;} Obj;
static Obj result;
static uint32_t args[8];
static uint32_t arg(Cpu *c,int i){(void)c;return args[i];}
static int envcube_sharp;
#define K_CUBE 7
#define vt_cube 0x100
#define D3D_OK 0
#define RET(v,n) do {c->esp+=4*(n+1);return v;} while(0)
static Obj *make_texture(uint32_t k,uint32_t vt,uint32_t w,uint32_t h,uint32_t d,uint32_t l,uint32_t u,uint32_t f,uint32_t p){
 result=(Obj){0x200,k,vt,w,h,d,l,u,f,p};return &result;
}
static void envcube_created(Obj*t,uint32_t caller,uint32_t output){(void)t;(void)caller;(void)output;}
HELPERS
CREATE
void create(int sharp,uint32_t edge,uint32_t levels,uint32_t usage,uint32_t fmt,uint32_t pool,uint32_t caller,uint32_t *out){
 envcube_sharp=sharp;
 for(unsigned i=0;i<128;++i)G[i]=0x76540000+i;
 args[1]=edge;args[2]=levels;args[3]=usage;args[4]=fmt;args[5]=pool;args[6]=64;
 Cpu c={32};M32(c.esp)=caller;v_CreateCubeTexture(&c);
 memcpy(out,&result,sizeof result);memcpy(out+sizeof(result)/4,G,sizeof G);
 out[sizeof(result)/4+128]=c.esp;
}
'''


def main():
    # Pin only the replaced body, independently of moving history.
    assert hashlib.sha256(ORIGINAL.encode()).hexdigest() == '81e3296fdc241a3fb347c53388e78c20147fa416e0783417060137e83b53b6cf'
    env = (RT / 'envcube.m').read_text()
    helpers = '\n'.join(function(env, n) for n in
                        ('envcube_level_count', 'envcube_creation_levels', 'envcube_creation_edge'))
    fixed = function((RT / 'd3d9.m').read_text(), 'v_CreateCubeTexture')
    with tempfile.TemporaryDirectory(prefix='envcube-static-') as td:
        libs = []
        for name, body in (('fixed', fixed), ('original', ORIGINAL)):
            src = Path(td) / (name + '.c'); lib = Path(td) / (name + '.dylib')
            src.write_text(HARNESS.replace('HELPERS', helpers).replace('CREATE', body))
            subprocess.run(['clang', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
                            '-dynamiclib', src, '-o', lib], check=True)
            dll = ct.CDLL(str(lib)); dll.create.argtypes = [ct.c_int] + [ct.c_uint32] * 6 + [ct.POINTER(ct.c_uint32)]
            libs.append(dll)

        def create(lib, sharp, edge=1024, levels=1, usage=0, fmt=21, pool=1, caller=0x006BD529):
            out = (ct.c_uint32 * 139)()
            lib.create(sharp, edge, levels, usage, fmt, pool, caller, out)
            return bytes(out), out[3]

        cases = unchanged = off = 0
        for sharp, edge, levels, usage, fmt, pool, caller in itertools.product(
                (0, 1), (16, 256, 1024, 2048), (1, 3), (0, 1), (21, 22), (0, 1), (0x006BD529, 0x006BD54B, 0x123456)):
            args = sharp, edge, levels, usage, fmt, pool, caller
            good, _ = create(libs[0], *args); old, _ = create(libs[1], *args)
            changed = sharp and edge == 1024 and usage == 0 and fmt == 21 and pool == 1 and caller == 0x006BD529
            if not changed:
                assert good == old, args
                unchanged += 1
            if not sharp: off += 1
            cases += 1
        dynamic, edge = create(libs[0], 1, usage=1, pool=0, caller=0x006BD54B)
        assert edge == 1024 and ct.c_uint32.from_buffer_copy(dynamic, 6*4).value == 11
        print(f'creation bytes: {unchanged}/{cases} unchanged, PC {off}/{off}; capture 1024/11')

        def fill(source, lib):
            _, edge = create(lib, 1)
            dest = bytearray(edge * edge * 4)
            dest[:len(source)] = source  # Native loader: one flat copy, no pitch walk.
            return dest, dest[(edge//2*edge+edge//2)*4]

        source = bytes((231, 123, 57, 255)) * (256*256)
        good, centre = fill(source, libs[0]); old, old_centre = fill(source, libs[1])
        assert good == source and centre == 231
        assert old != source and old_centre == 0
        print(f'synthetic source=231 original={old_centre} fixed={centre}; original rejected')

        game = Path(os.environ.get('NFS_GAME', ROOT.parent / 'Need For Speed Most Wanted Black Edition'))
        path = game / 'GLOBAL/DYNTEX.BIN'
        if not path.is_file():
            print('SKIP: decoded CARLOT faces require GLOBAL/DYNTEX.BIN'); return 3
        sys.dont_write_bytecode = True
        sys.path.insert(0, str(ROOT / 'tools_snapshot'))
        import x360_tex as xt
        faces = {name: (w, h, fmt, pixels) for name, _, w, h, fmt, pixels in xt.textures_in(path.read_bytes(), 0x80)
                 if name.startswith('CARLOT_')}
        expected = {'CARLOT_' + axis + sign for axis in 'XYZ' for sign in ('POS', 'NEG')}
        if not expected.issubset(faces):
            print('SKIP: requires six decoded CARLOT faces'); return 3
        for name in sorted(expected):
            w, h, fmt, pixels = faces[name]
            assert (w, h, fmt) == (256, 256, 21), (name, w, h, fmt)
            pixels = pixels[:w*h*4]; assert len(pixels) == 262144
            good, centre = fill(pixels, libs[0]); old, old_centre = fill(pixels, libs[1])
            assert good == pixels and old != pixels, name
            if name == 'CARLOT_YNEG': assert pixels[(128*256+128)*4] == centre == 255 and old_centre == 0
            print(f'{name} source={pixels[(128*256+128)*4]} original={old_centre} fixed={centre}; full face equal')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
