"""Synthetic collision loading -> translated OnLoaded -> translated pixel size.

19 checks: fixed 0 failures; old loader mutant 4 failures.
Missing-radius control: old 0 m / 0 pixels (rejected at minimum 4);
fixed 3 m / 42 pixels. Original bounds/files and authored radii stay exact.
The old loader mutant must fail; no private car bytes or game process needed.
"""
from pathlib import Path
import os
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def function(source, name):
    match = re.search(r'^(?:static )?void ' + name + r'\(Cpu \*[^\n]*\) \{', source, re.M)
    assert match, name
    return source[match.start():source.index('\n}', match.end()) + 2]


HARNESS = r'''
#include "host.h"
#include "funcs.h"
#include <math.h>
#include <stdlib.h>
uint8_t *G;
typedef struct {
    uint32_t model_key, donor_model, collision, collision_size;
    uint8_t *collision_bytes;
    char name[16];
} Addition;
static Addition additions[1];
static unsigned nadditions=1, enabled=1, allocations, fallbacks, failures, checks;
static uint32_t cursor=0x100000;
#define CHECK(x) do { ++checks; if (!(x)) { ++failures; fprintf(stderr,"line %d: %s\n",__LINE__,#x); } } while (0)
static int on(void) { return enabled; }
static uint32_t parts_hash(const char *s) { (void)s; return 0x1234; }
static void fail(const char *s) { fprintf(stderr,"%s\n",s); exit(2); }
uint32_t gmalloc(uint32_t n) { uint32_t p=cursor; cursor=(cursor+n+63)&~63u; ++allocations; return p; }
void f_00681340_orig(Cpu *c) { ++fallbacks; c->eax=0x70000; c->esp+=8; }
PRODUCTION
// Lookup's collection-list traversal is independent of cache initialization.
// Route its argument through the production owned-collection hook.
void f_006813B0(Cpu *c) {
    uint32_t sp=c->esp; f_00681340(c); c->esp=sp+4;
}
void f_005CC240(Cpu *c) { c->eax=additions[0].model_key; c->esp+=4; }
void f_00787580(Cpu *c) { (void)c; abort(); }
void dispatch(Cpu *c, uint32_t p) { (void)c; (void)p; abort(); }
void f_007C4B80(Cpu *c) { c->eax=(uint32_t)(int32_t)ST(0); x87_pop(c); c->esp+=4; }
TRANSLATED
static Cpu cpu(void) { Cpu c={0}; c.esp=0x800000; c.cw=0x37f; return c; }
static unsigned pixels(float radius) {
    const uint32_t view=0x20000, camera=0x30000, position=0x40000;
    M32(view+0x40)=camera; MF32(view+0xc)=100;
    MF32(camera+0x58)=1; MF32(position+8)=10;
    MF32(0x8933d4)=0.5f;
    Cpu c=cpu(); c.ecx=view; M32(c.esp+4)=position; MF32(c.esp+8)=radius;
    f_004FCAC0(&c); CHECK(c.esp==0x80000c); return c.eax;
}
int main(void) {
    G=calloc(1,0xa00000); if (!G) return 2;
    // Three synthetic bounds, one cloud of four points. Millimetre half sizes
    // form a 1/2/2 box (sphere 3), an authored sphere, and a .3/.4/0 box.
    uint8_t raw[16+3*48+16+16+4*16]={0}, original[sizeof raw];
    uint32_t key=0x1234, n=3, one=1, four=4;
    memcpy(raw,&key,4); memcpy(raw+4,&n,4);
    const int16_t dims[3][3]={{1000,2000,2000},{100,100,100},{300,400,0}};
    for (unsigned k=0;k<3;++k) memcpy(raw+16+k*48+16,dims[k],6);
    float authored=.75f; memcpy(raw+16+48+32,&authored,4);
    memcpy(raw+16+3*48,&one,4); memcpy(raw+16+3*48+16,&four,4);
    memcpy(original,raw,sizeof raw);
    Addition *a=additions; a->model_key=key; a->donor_model=0x5678;
    a->collision_bytes=raw; a->collision_size=sizeof raw;
    unsigned before=pixels(0); CHECK(before==0 && before<4);
    Cpu c=cpu(); c.ecx=0x50000; M32(c.esp+4)=key;
    f_00681340(&c); uint32_t loaded=c.eax;
    CHECK(c.esp==0x800008 && loaded==a->collision && loaded!=0);
    CHECK(MF32(loaded+16+32)==3.0f);
    CHECK(MF32(loaded+16+48+32)==authored);
    CHECK(fabsf(MF32(loaded+16+96+32)-.5f)<1e-6f);
    CHECK(!memcmp(raw,original,sizeof raw) && M32(loaded+8)==1);
    for (unsigned k=0;k<3;++k) CHECK(M32(loaded+16+k*48+44)==loaded);
    CHECK(M32(loaded+16+3*48+16+12)==loaded+16+3*48+32);
    // Only runtime caches/back-pointers may differ from the source bytes.
    uint8_t expected[sizeof raw]; memcpy(expected,raw,sizeof raw);
    memcpy(expected+8,G+loaded+8,4);
    for (unsigned k=0;k<3;++k) {
        memcpy(expected+16+k*48+32,G+loaded+16+k*48+32,4);
        memcpy(expected+16+k*48+44,G+loaded+16+k*48+44,4);
    }
    memcpy(expected+16+3*48+16+12,G+loaded+16+3*48+16+12,4);
    CHECK(!memcmp(expected,G+loaded,sizeof raw));
    uint32_t render=0x60000, conn=0x50000;
    M32(conn+0x64)=1; M32(0x9b09d8)=0x70000;
    c=cpu(); c.ecx=conn; M32(c.esp+4)=render;
    MF32(render+0x17a8)=9; f_00738A10(&c);
    CHECK(c.esp==0x800008 && MF32(render+0x17a8)==3);
    unsigned after=pixels(MF32(render+0x17a8)); CHECK(after==42 && after>=4);
    c=cpu(); M32(c.esp+4)=key; f_00681340(&c);
    CHECK(c.eax==loaded && allocations==1 && !fallbacks);
    c=cpu(); M32(c.esp+4)=0x9999; f_00681340(&c);
    CHECK(c.eax==0x70000 && fallbacks==1 && M32(0x800004)==0x9999);
    enabled=0; c=cpu(); M32(c.esp+4)=key; f_00681340(&c);
    CHECK(c.eax==0x70000 && fallbacks==2 && allocations==1);
    CHECK(!memcmp(raw,original,sizeof raw));
    printf("radius: %u checks, %u failures; pixels %u -> %u, minimum 4\n",checks,failures,before,after);
    free(G); return failures!=0;
}
'''


def main():
    runtime = (ROOT / 'runtime/car85.c').read_text()
    hook = function(runtime, 'f_00681340')
    # Reproduce the old implementation while retaining current relocations.
    mutant, count = re.subn(r'                    if \(MF32\(bound \+ 32\) == 0\.0f\) \{.*?\n                    }',
                           '', hook, flags=re.S)
    assert count == 1
    translated = []
    for chunk, name in [('46', 'f_00738A10'), ('34', 'f_0066A690'), ('15', 'f_004FCAC0')]:
        path = ROOT / f'recomp/out/speed_{chunk}.c'
        if not path.is_file():
            print('SKIP: build translated functions first')
            return 3
        translated.append(function(path.read_text(), name))
    with tempfile.TemporaryDirectory(prefix='car-radius-') as td:
        tmp = Path(td)
        for label, production in [('actual', hook), ('old-zero-radius', mutant)]:
            source = HARNESS.replace('PRODUCTION', production).replace('TRANSLATED', '\n'.join(translated))
            unit, binary = tmp / 'test.c', tmp / label
            unit.write_text(source)
            build = subprocess.run(['clang', '-O1', '-g', '-fsanitize=address,undefined', '-Iruntime',
                                    '-Irecomp', '-Irecomp/out', str(unit), '-o', str(binary)],
                                   cwd=ROOT, capture_output=True, text=True)
            assert build.returncode == 0, build.stderr[-3000:]
            run = subprocess.run([str(binary)], capture_output=True, text=True,
                                 env={**os.environ, 'ASAN_OPTIONS': 'detect_leaks=0'})
            print(label, run.stdout.strip())
            assert run.returncode == (0 if label == 'actual' else 1), run.stderr[-3000:]
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
