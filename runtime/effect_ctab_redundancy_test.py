#!/usr/bin/env python3
"""Live CTAB comparison and exact constant dirtiness (design ranks 2 and 3).

Executes production upload_ctab/BeginPass/CommitChanges, CONST_FN, the live-register
API, and bind_floats with a memory-backed ring and counting encoder (no GPU).
Pinned originals prove redundant scratch/COM work and version bumps; a parameter-
stamp skip mutant must fail A->B->A and direct-overwrite restoration. Separate
controls cover intercepted setters, signed zero/NaN bits, initial versions, wrap,
changed ranges, and new-frame ring lifetime. Numbers are printed for every run.
The stale parameter skip must fail A->B->A restoration. All broken runs exit 1.
Measured: fixed 0/262 failed; pinned CTAB original 25/262; setter original 51/262;
stale parameter skip 37/262. Six identical CTAB uploads: original 6 scratch/6 COM,
fixed 0/0; six identical direct uploads: original 6 version bumps, fixed 0.
"""
import hashlib
import re
import subprocess
import sys
import tempfile
from pathlib import Path

RT = Path(__file__).resolve().parent
sys.path.insert(0, str(RT))
from save_file_fixture import mw_source

# SHA-256-checked production fragments, embedded below; no history dependency.
ORIGINAL_CTAB = '        uint32_t buf = scratch(16 * nreg);\n        if (rs == 2) {\n            memcpy(G + buf, regs, 16 * nreg);\n            uint32_t a[3] = {ri, buf, nreg};\n            dev_call(c, fx, ps ? DEV_SetPixelShaderConstantF : DEV_SetVertexShaderConstantF, 3, a);\n        } else {\n            for (uint32_t r = 0; r < nreg; ++r) for (int cc = 0; cc < 4; ++cc) M32(buf + 16 * r + 4 * cc) = (uint32_t)(int32_t)regs[r][cc];\n            if (rs == 0) { for (uint32_t r = 0; r < nreg; ++r) M32(buf + 4 * r) = regs[r][0] != 0; }\n            uint32_t a[3] = {ri, buf, nreg};\n            dev_call(c, fx, rs == 1 ? (ps ? DEV_SetPixelShaderConstantI : DEV_SetVertexShaderConstantI)\n                                    : (ps ? DEV_SetPixelShaderConstantB : DEV_SetVertexShaderConstantB), 3, a);\n        }'
ORIGINAL_CTAB_SHA = '43af670e3324648a36924298fdee140b7e7760f2cad86e4b7473c89f4fbca23c'
ORIGINAL_WRITE = '        memcpy(&dev.arr[s], G + arg(c, 2), (size_t)n * (w));                                         \\\n        if (!++dev.ver_##arr) dev.ver_##arr = 1;                                                     \\\n'
ORIGINAL_WRITE_SHA = 'a475c05077e1bbaf8ace3b63d507d79d70f782d64af0172eb815b5dcc929fef5'

PREFIX = r'''
#import <Foundation/Foundation.h>
#include <stdlib.h>
#include "host.h"
#define D3D_OK 0
#define D3DERR_INVALIDCALL 0x8876086Cu
typedef struct { uint32_t guest; } Obj;
static int scratch_calls, com_calls, checks, bad, intercepted_slot;
static int repeated_scratch, repeated_com, redundant_versions;
#define CHECK(c, what) do { ++checks; if (!(c)) { ++bad; printf("FAIL: %s\n", what); } } while (0)
'''

RING = r'''
@protocol MTLBuffer @end
@interface CountingEncoder : NSObject
@end
@implementation CountingEncoder
- (void)setFragmentBuffer:(id)b offset:(NSUInteger)o atIndex:(NSUInteger)i {}
- (void)setVertexBuffer:(id)b offset:(NSUInteger)o atIndex:(NSUInteger)i {}
- (void)setFragmentBufferOffset:(NSUInteger)o atIndex:(NSUInteger)i {}
- (void)setVertexBufferOffset:(NSUInteger)o atIndex:(NSUInteger)i {}
@end
static CountingEncoder *enc;
static id<MTLBuffer> ring_buffer;
static uint64_t frame_serial = 1;
static struct { void *cbuf[2]; uint32_t coff[2]; } eb;
static float ring_data[16384];
static unsigned ring_used, ring_calls;
static float *ring_alloc(NSUInteger bytes, uint32_t *off, __unsafe_unretained id<MTLBuffer> *buffer) {
    ++ring_calls; *off = ring_used; *buffer = ring_buffer;
    float *p = (float *)((uint8_t *)ring_data + ring_used);
    ring_used += bytes;
    if (ring_used > sizeof ring_data) abort();
    return p;
}
'''

HARNESS = r'''
uint8_t *G; int boot_trace;
uint32_t galloc(uint32_t n) { static uint32_t top = 0x40000; uint32_t a = top; top += (n + 15) & ~15u; return a; }
uint32_t gstr(const char *s) { return 0; }
uint32_t host_fn(HostFn f, const char *n) { return 0; }
void d3d9_addref_guest(uint32_t g) {}
void d3d9_release_guest(uint32_t g) {}
void widescreen_drop_offset(Cpu *c, uint32_t a) {}
float lighting_inv_shadow_strength(float f) { return f; }
int blackbox_enabled(void) { return 0; }
uint16_t blackbox_render_pass_id(const char *a,const char *b,const char *c) { return 0; }
void blackbox_render_pass_begin(uint16_t i) {}
void blackbox_render_pass_end(void) {}
static HostFn setters[2][3] = {
    {v_SetVertexShaderConstantB, v_SetVertexShaderConstantI, v_SetVertexShaderConstantF},
    {v_SetPixelShaderConstantB, v_SetPixelShaderConstantI, v_SetPixelShaderConstantF}};
static HostFn getters[2][3] = {
    {v_GetVertexShaderConstantB, v_GetVertexShaderConstantI, v_GetVertexShaderConstantF},
    {v_GetPixelShaderConstantB, v_GetPixelShaderConstantI, v_GetPixelShaderConstantF}};
static void *bank(int ps, int set) {
    return set == 2 ? (void *)(ps ? dev.psc : dev.vsc)
         : set == 1 ? (void *)(ps ? dev.psi : dev.vsi) : (void *)(ps ? dev.psb : dev.vsb);
}
static uint32_t *version(int ps, int set) {
    return set == 2 ? (ps ? &dev.ver_psc : &dev.ver_vsc)
         : set == 1 ? (ps ? &dev.ver_psi : &dev.ver_vsi) : (ps ? &dev.ver_psb : &dev.ver_vsb);
}
static uint32_t invoke(HostFn f, uint32_t a, uint32_t b, uint32_t d, uint32_t e) {
    Cpu c = {0}; c.esp = 0x8000;
    M32(c.esp + 4) = a; M32(c.esp + 8) = b; M32(c.esp + 12) = d; M32(c.esp + 16) = e;
    f(&c); return c.eax;
}
static uint32_t direct(int ps, int set, uint32_t start, uint32_t n, const void *data) {
    size_t bytes = (set ? 16 : 4) * (size_t)n;
    if (bytes <= 4096) memcpy(G + 0x10000, data, bytes);
    return invoke(setters[ps][set], dev.self->guest, start, 0x10000, n);
}
uint32_t call_guest(Cpu *c, uint32_t f, int n, const uint32_t *a) {
    if (f == 0xffff) f = intercepted_slot;
    for (int ps = 0; ps < 2; ++ps) for (int set = 0; set < 3; ++set)
        if (f == constant_slots[ps][set]) {
            ++com_calls;
            return invoke(setters[ps][set], a[0], a[1], a[2], a[3]);
        }
    return 0; // Shader/sampler state methods are outside the constant fixture.
}
static void constants(void) {
    uint32_t bits[12], wanted[12], zeros[12] = {0};
    for (int i = 0; i < 12; ++i) bits[i] = i + 0x3f800000u;
    for (int ps = 0; ps < 2; ++ps) for (int set = 0; set < 3; ++set) {
        unsigned width = set ? 16 : 4, words = width / 4, limit = set == 2 ? (ps ? 224 : 256) : 16;
        uint32_t *ver = version(ps, set), initial = *ver;
        CHECK(initial == 1, "initial zero registers have nonzero version sentinel");
        CHECK(direct(ps, set, 2, 3, zeros) == D3D_OK && *ver == initial, "identical initial zeros do not dirty");
        CHECK(direct(ps, set, 2, 3, bits) == D3D_OK && *ver == initial + 1, "changed range advances version");
        CHECK(!memcmp((uint8_t *)bank(ps,set) + 2*width, bits, 3*width), "changed range copies exact bits");
        uint32_t v = *ver;
        direct(ps, set, 2, 3, bits);
        redundant_versions += *ver != v;
        CHECK(*ver == v, "identical direct upload preserves version");
        memcpy(wanted, bits, 3*width); memcpy(wanted + words, zeros, width);
        direct(ps, set, 3, 1, zeros);
        CHECK(*ver == v+1 && !memcmp((uint8_t *)bank(ps,set)+2*width,wanted,3*width), "overlapping partial write preserves neighbours");
        direct(ps, set, 2, 3, bits);
        CHECK(!memcmp((uint8_t *)bank(ps,set)+2*width,bits,3*width), "direct A->B->A restores A");
        CHECK(invoke(getters[ps][set],dev.self->guest,2,0x12000,3) == D3D_OK && !memcmp(G+0x12000,bits,3*width), "getter returns exact range");
        v = *ver;
        CHECK(invoke(setters[ps][set],dev.self->guest,limit,0,0) == D3D_OK && *ver == v, "empty endpoint write is a no-op");
        CHECK(invoke(getters[ps][set],dev.self->guest,limit,0x12000,0) == D3D_OK, "empty endpoint read succeeds");
        CHECK(direct(ps,set,limit-1,1,bits) == D3D_OK, "last register is writable");
        v = *ver;
        CHECK(invoke(setters[ps][set],0,limit-1,0,2) == D3DERR_INVALIDCALL && *ver == v, "crossing range fails without touching state");
        CHECK(invoke(getters[ps][set],0,limit-1,0,2) == D3DERR_INVALIDCALL, "crossing getter fails");
        CHECK(invoke(setters[ps][set],0,UINT32_MAX,0,2) == D3DERR_INVALIDCALL && *ver == v, "overflowing write rejected before source access");
        CHECK(invoke(getters[ps][set],0,UINT32_MAX,0,2) == D3DERR_INVALIDCALL, "overflowing getter rejected");
        CHECK(invoke(setters[ps][set],0,limit+1,0,0) == D3DERR_INVALIDCALL && *ver == v, "out-of-bounds empty write rejected");
        CHECK(d3d9_constants_equal(dev.self->guest,ps,set,2,3,bits), "live equality reads current register bytes");
        CHECK(!d3d9_constants_equal(0x2000,ps,set,2,3,bits), "foreign device cannot skip COM");
        CHECK(!d3d9_constants_equal(dev.self->guest,ps,set,UINT32_MAX,2,bits), "live equality rejects overflow");
        CHECK(!d3d9_constants_equal(dev.self->guest,ps,set,limit-1,2,bits), "live equality rejects crossing range");
        intercepted_slot = constant_slots[ps][set]; M32(0x3000+4*intercepted_slot) = 0xffff;
        CHECK(!d3d9_constants_equal(dev.self->guest,ps,set,2,3,bits), "in-place intercepted setter cannot skip");
        M32(0x3000+4*intercepted_slot) = intercepted_slot;
        *ver = UINT32_MAX; direct(ps,set,2,3,bits);
        CHECK(*ver == UINT32_MAX, "unchanged range does not wrap version");
        direct(ps,set,2,3,zeros);
        CHECK(*ver == 1, "changed range wraps to nonzero sentinel");
    }
    CHECK(!d3d9_constants_equal(0x1000,2,2,0,1,bits) && !d3d9_constants_equal(0x1000,0,3,0,1,bits), "invalid stage/register set rejected");
    for (int ps = 0; ps < 2; ++ps) {
        uint32_t v, raw[4] = {0}; direct(ps,2,0,1,raw); v = *version(ps,2);
        raw[0] = 0x80000000; direct(ps,2,0,1,raw);
        CHECK(*version(ps,2) == v+1 && !memcmp(bank(ps,2),raw,16), "negative zero differs in bits");
        raw[0] = 0; direct(ps,2,0,1,raw);
        CHECK(*version(ps,2) == v+2 && !memcmp(bank(ps,2),raw,16), "positive zero restores exact bits");
        raw[1] = 0x7fc01234; direct(ps,2,0,1,raw); v = *version(ps,2); direct(ps,2,0,1,raw);
        CHECK(*version(ps,2) == v, "identical NaN payload skips");
        raw[1] = 0x7fc01235; direct(ps,2,0,1,raw);
        CHECK(*version(ps,2) == v+1 && !memcmp(bank(ps,2),raw,16), "different NaN payload changes");
    }
}
static Param *numeric(int cls, int type, int rows, int cols) {
    Param *p = calloc(1,sizeof *p); p->cls=cls;p->type=type;p->rows=rows;p->cols=cols;
    p->bytes=4*rows*cols;p->data=data_alloc(p->bytes);p->handle=new_handle(1,p);
    return p;
}
static State shader(Param *p, int ps, int set, int start, int count, int layout) {
    static uint32_t code = 0xffff; static Param object = {.cls=PC_OBJECT};
    State s = {.op=OP_SHADER+ps,.kind=1,.shader=0x5000,.code=&code,.code_n=1,.ctab_done=1,.nctab=1,.value=&object};
    s.ctab=calloc(1,sizeof *s.ctab); *s.ctab=(struct CtabEntry){p,set,start,count,layout};return s;
}
static void upload(Effect *fx, State *s, int ps) { Cpu c={0}; upload_ctab(&c,fx,s,ps,0); }
static void ctab(void) {
    Effect fx = {.device=0x1000}; float av[4]={-3,2,0,7}, bv[4]={0,8,1,6};
    for (int ps=0;ps<2;++ps) for (int set=0;set<3;++set) {
        Param *p=numeric(PC_VECTOR,set==2?PT_FLOAT:set==1?PT_INT:PT_BOOL,1,4);
        Param *q=numeric(PC_VECTOR,p->type,1,4);
        set_floats(p,av,4);set_floats(q,bv,4);
        State a=shader(p,ps,set,6,1,PC_VECTOR), b=shader(q,ps,set,6,1,PC_VECTOR);
        unsigned width=set?16:4; uint32_t expected[4]={0};
        if (set==2) memcpy(expected,av,16);
        else if (set==1) {expected[0]=(uint32_t)-3;expected[1]=2;expected[3]=7;}
        else expected[0]=1;
        upload(&fx,&a,ps);
        CHECK(!memcmp((uint8_t *)bank(ps,set)+6*width,expected,width), "CTAB F/I/B final packed bytes");
        scratch_calls=com_calls=0;upload(&fx,&a,ps);
        repeated_scratch+=scratch_calls;repeated_com+=com_calls;
        CHECK(!scratch_calls && !com_calls, "identical CTAB skips scratch and COM");
        upload(&fx,&b,ps);upload(&fx,&a,ps);
        CHECK(!memcmp((uint8_t *)bank(ps,set)+6*width,expected,width), "CTAB A->B->A restores A");
        uint32_t overwrite[4]={0};direct(ps,set,6,1,overwrite);upload(&fx,&a,ps);
        CHECK(!memcmp((uint8_t *)bank(ps,set)+6*width,expected,width), "CTAB restores direct-device overwrite");
        // Same-valued effect Set* advances its stamp; CommitChanges must restore live state.
        Pass pass={.nstates=1,.states=&a}; Technique tech={.npasses=1,.passes=&pass};fx.cur=&tech;fx.guest=0x9000;
        Effect *list[]={&fx};effects=list;neffects=1;M32(fx.guest+4)=0;M32(fx.guest+8)=0x0EFFEC70;
        invoke(e_BeginPass,fx.guest,0,0,0);uint64_t stamp=pstamp(p), applied=pass.applied;
        direct(ps,set,6,1,overwrite);set_floats(p,av,4);
        CHECK(pstamp(p)>stamp && pstamp(p)>applied,"same-valued Set* retains change stamps");
        invoke(e_CommitChanges,fx.guest,0,0,0);
        CHECK(!memcmp((uint8_t *)bank(ps,set)+6*width,expected,width),"same-valued CommitChanges restores overwritten registers");
        direct(ps,set,6,1,overwrite);com_calls=0;invoke(e_CommitChanges,fx.guest,0,0,0);
        CHECK(!com_calls && !memcmp((uint8_t *)bank(ps,set)+6*width,overwrite,width),"unchanged CommitChanges retains stamp filtering");
        invoke(e_BeginPass,fx.guest,0,0,0);
        CHECK(!memcmp((uint8_t *)bank(ps,set)+6*width,expected,width),"BeginPass reconsiders unchanged parameters");
        // Foreign COM object using the same vtable, then in-place and copied-vtable interception.
        fx.device=0x2000;M32(fx.device)=0x3000;scratch_calls=com_calls=0;upload(&fx,&a,ps);
        CHECK(com_calls==1 && scratch_calls==1,"foreign device always receives CTAB upload");fx.device=0x1000;
        intercepted_slot=constant_slots[ps][set];M32(0x3000+4*intercepted_slot)=0xffff;com_calls=0;upload(&fx,&a,ps);
        CHECK(com_calls==1,"intercepted COM setter still observes identical upload");M32(0x3000+4*intercepted_slot)=intercepted_slot;
        memcpy(G+0x6000,G+0x3000,512);M32(0x1000)=0x6000;M32(0x6000+4*intercepted_slot)=0xffff;com_calls=0;upload(&fx,&a,ps);
        CHECK(com_calls==1,"copied intercepted vtable also falls back");M32(0x1000)=0x3000;
    }
    effects=NULL;neffects=0;
    for (int ps=0;ps<2;++ps) for (int param_cols=0;param_cols<2;++param_cols) for (int shader_cols=0;shader_cols<2;++shader_cols) {
        Param *p=numeric(param_cols?PC_MATRIX_COLUMNS:PC_MATRIX_ROWS,PT_FLOAT,2,3);
        float logical[16]={0};for(int r=0;r<2;++r) for(int c=0;c<3;++c) logical[r*4+c]=1+r*3+c;
        set_matrix(p,logical,0);State a=shader(p,ps,2,10,shader_cols?3:2,shader_cols?PC_MATRIX_COLUMNS:PC_MATRIX_ROWS);
        float expected[3][4]={{0}};for(int r=0;r<2;++r)for(int c=0;c<3;++c)expected[shader_cols?c:r][shader_cols?r:c]=logical[r*4+c];
        upload(&fx,&a,ps);
        CHECK(!memcmp((uint8_t *)bank(ps,2)+160,expected,(shader_cols?3:2)*16),"matrix parameter/shader row-column layout and padding");
        scratch_calls=com_calls=0;upload(&fx,&a,ps);CHECK(!scratch_calls&&!com_calls,"matrix duplicate skips only after final layout");
    }
    for (int ps=0;ps<2;++ps) for (int set=0;set<3;++set) {
        Param *p=calloc(1,sizeof *p);p->elements=p->nmembers=3;p->members=calloc(3,sizeof *p->members);
        uint32_t expected[3][4]={{0}};
        for(int i=0;i<3;++i) {
            p->members[i]=*numeric(PC_SCALAR,set==2?PT_FLOAT:set==1?PT_INT:PT_BOOL,1,1);
            float v=i==1?0:(float)(i+2);set_floats(&p->members[i],&v,1);
            if(set==2)memcpy(&expected[i][0],&v,4);else expected[i][0]=set==1?(uint32_t)v:v!=0;
        }
        uint32_t bools[3]={1,0,1};State a=shader(p,ps,set,9,3,PC_SCALAR);upload(&fx,&a,ps);
        CHECK(!memcmp((uint8_t *)bank(ps,set)+9*(set?16:4),set?(void*)expected:(void*)bools,set?48:12),"numeric arrays preserve register padding / compact bool packing");
        scratch_calls=com_calls=0;upload(&fx,&a,ps);CHECK(!scratch_calls&&!com_calls,"array duplicates skip");
        com_calls=0;uint64_t since=fx_epoch;float v=5;set_floats(&p->members[1],&v,1);Cpu c={0};upload_ctab(&c,&fx,&a,ps,since);
        CHECK(com_calls==1,"array member stamp triggers CommitChanges upload");
    }
    Param *p=numeric(PC_VECTOR,PT_FLOAT,1,4),*q=numeric(PC_VECTOR,PT_FLOAT,1,4);
    p->flags=q->flags=1;strcpy(p->name,"shared");strcpy(q->name,"shared");
    Effect other={.device=0x1000,.params=q,.nparams=1};fx.params=p;fx.nparams=1;share_params(&fx);share_params(&other);
    CHECK(p->data==q->data,"shared effects use pool data");
    set_floats(p,av,4);State a=shader(p,0,2,20,1,PC_VECTOR), b=shader(q,0,2,20,1,PC_VECTOR);upload(&fx,&a,0);
    uint64_t since=fx_epoch;set_floats(q,bv,4);Cpu c={0};upload_ctab(&c,&fx,&a,0,since);
    CHECK(!memcmp(dev.vsc[20],bv,16)&&pstamp(p)==pstamp(q),"shared effect change and stamp seen by other effect");
    scratch_calls=com_calls=0;upload(&other,&b,0);CHECK(!scratch_calls&&!com_calls,"shared identical data skips against live device");
    for(int ps=0;ps<2;++ps) {
        Param *p=numeric(PC_VECTOR,PT_FLOAT,1,4);uint32_t raw[4]={0x80000000,0x7fc01234,0,0};
        memcpy(p->data,raw,16);touch(p);State a=shader(p,ps,2,24,1,PC_VECTOR);upload(&fx,&a,ps);
        CHECK(!memcmp((uint8_t*)bank(ps,2)+24*16,raw,16),"CTAB preserves signed zero and NaN payload");
        scratch_calls=com_calls=0;upload(&fx,&a,ps);CHECK(!scratch_calls&&!com_calls,"identical CTAB NaN skips by bits");
        raw[0]=0;raw[1]=0x7fc01235;memcpy(p->data,raw,16);touch(p);upload(&fx,&a,ps);
        CHECK(com_calls==1&&!memcmp((uint8_t*)bank(ps,2)+24*16,raw,16),"CTAB changes signed zero and NaN payload by bits");
    }
}
static void ring_lifetime(void) {
    enc=[CountingEncoder new];ring_buffer=(id<MTLBuffer>)[NSObject new];
    uint32_t bits[4]={0x3f800000,0,0,0};direct(0,2,0,1,bits);
    CHECK(bind_floats(0,dev.vsc[0],dev.ver_vsc,1,256)&&ring_calls==1,"first draw uploads constants to ring");
    direct(0,2,0,1,bits);bind_floats(0,dev.vsc[0],dev.ver_vsc,1,256);
    CHECK(ring_calls==1,"identical direct upload reuses same-frame ring bytes");
    ++frame_serial;uint32_t old=eb.coff[0];bind_floats(0,dev.vsc[0],dev.ver_vsc,1,256);
    CHECK(ring_calls==2&&eb.coff[0]!=old,"new frame allocates fresh ring space despite same version");
    bits[0]=0x40000000;direct(0,2,0,1,bits);bind_floats(0,dev.vsc[0],dev.ver_vsc,1,256);
    CHECK(ring_calls==3&&!memcmp((uint8_t*)ring_data+eb.coff[0],bits,16),"changed registers always upload to ring");
    bind_floats(0,dev.vsc[0],dev.ver_vsc,2,256);CHECK(ring_calls==4,"larger required prefix uploads even with same version");
    bind_floats(0,dev.vsc[0],0,1,256);bind_floats(0,dev.vsc[0],0,1,256);
    CHECK(ring_calls==6,"version zero retains synthesized-constant semantics");
}
int main(void) { @autoreleasepool {
    G=calloc(1,1<<20);static Obj device={.guest=0x1000};dev.self=&device;M32(device.guest)=0x3000;
    for(int ps=0;ps<2;++ps)for(int set=0;set<3;++set)M32(0x3000+4*constant_slots[ps][set])=constant_slots[ps][set];
    uint32_t vt_device=0x3000;
    INIT_SETTERS
    constants();ctab();ring_lifetime();printf("%d failed of %d checks; repeated scratch=%d COM=%d versions=%d ring=%u\n",bad,checks,repeated_scratch,repeated_com,redundant_versions,ring_calls);
    return bad?1:0;
} }
'''


def between(text, start, end):
    return text[text.index(start):text.index(end, text.index(start))]


def build_run(tmp, name, device, effect, ring, init):
    # Only instrumentation: count production scratch calls; packing/COM paths stay intact.
    effect = effect.replace('static uint32_t scratch(uint32_t n) {',
                            'static uint32_t scratch(uint32_t n) { ++scratch_calls;')
    source = PREFIX + device + effect + RING + ring + HARNESS.replace('INIT_SETTERS', init)
    path = tmp / (name + '.m')
    path.write_text(source)
    exe = tmp / name
    cmd = ['clang', '-x', 'objective-c', '-std=gnu11', '-O1', '-g', '-w', '-fobjc-arc',
           '-fsanitize=address,undefined', '-I', str(RT), '-I', str(RT.parent / 'recomp'),
           str(path), '-framework', 'Foundation', '-o', str(exe)]
    compiled = subprocess.run(cmd, capture_output=True, text=True)
    if compiled.returncode:
        raise RuntimeError(compiled.stderr[-6000:])
    ran = subprocess.run([str(exe)], capture_output=True, text=True)
    lines = ran.stdout.strip().splitlines()
    print(f'{name}: exit {ran.returncode}: ' + (lines[-1] if lines else 'no output'))
    if ran.returncode == 0 and any(line.startswith('FAIL:') for line in lines):
        raise RuntimeError('fixture reported failure with exit zero')
    if ran.stderr:
        raise RuntimeError(ran.stderr[-3000:])
    return ran.returncode, ran.stdout


def main():
    for source, digest in ((ORIGINAL_CTAB, ORIGINAL_CTAB_SHA), (ORIGINAL_WRITE, ORIGINAL_WRITE_SHA)):
        assert hashlib.sha256(source.encode()).hexdigest() == digest, 'pinned original changed'
    d3d = (RT / 'd3d9.m').read_text()
    header = (RT / 'd3d9_constant_state.h').read_text()
    assert 'int d3d9_constants_equal(' in header, 'live-register API declaration missing'
    internal = (RT / 'd3d9_internal.h').read_text()
    device = between(internal, 'typedef struct {\n    Obj *self;', '\nextern Device dev;')
    device += re.search(r'^Device dev[^;]*;', d3d, re.M)[0] + '\n'
    device += between(d3d, '#define CONST_FN(', 'static int v_SetStreamSource(')
    init = re.search(r'    for \(int ps = 0; ps < 2; \+\+ps\) for \(int set = 0; set < 3; \+\+set\)\n'
                     r'        constant_setters\[ps\]\[set\] = M32\(vt_device \+ 4 \* constant_slots\[ps\]\[set\]\);', d3d)[0]
    effect = mw_source(RT / 'd3dx_effect.c')
    ring = between(mw_source(RT / 'render.m'), 'static int bind_floats(', '// ---------------- R27 grass:')
    live = 'if (d3d9_constants_equal(fx->device, ps, rs, ri, nreg, data)) continue;'
    assert live in effect
    current_write = re.search(r'        if \(n && memcmp.*?\n        }[^\n]*\n', device, re.S)[0]
    stale = '''static Param *seen[128]; static uint64_t stamps[128]; static int used;
        int old = 0;
        for (int i=0;i<used;++i) if (seen[i]==p) { old=stamps[i]==pstamp(p); stamps[i]=pstamp(p); goto cached; }
        if (used<128) { seen[used]=p; stamps[used++]=pstamp(p); }
        cached: if (old) continue;'''
    numeric = '''static int numeric_diff(const void *a,const void *b,size_t n) {
        for(size_t i=0;i<n;i+=4) { float x,y;memcpy(&x,(const char*)a+i,4);memcpy(&y,(const char*)b+i,4);if(x!=y)return 1;}return 0;
    }\n'''
    variants = [
        ('fixed', device, effect, ring, 0),
        ('original_ctab', device, effect.replace(between(effect, '        uint32_t packed[', '\n    }\n}\n\n// commit:'), ORIGINAL_CTAB), ring, 1),
        ('original_write', device.replace(current_write, ORIGINAL_WRITE), effect, ring, 1),
        ('stale_parameter', device, effect.replace(live, stale), ring, 1),
        ('interception_ignored', device.replace('M32(vt + 4 * constant_slots[ps][set]) != constant_setters[ps][set]', '0'), effect, ring, 1),
        ('numeric_equality', numeric + device.replace('n && memcmp(', 'n && numeric_diff('), effect, ring, 1),
        ('zero_initial_version', device.replace(re.search(r'Device dev[^;]*;', device)[0], 'Device dev;'), effect, ring, 1),
        ('zero_wrap_version', device.replace('if (!++dev.ver_##arr) dev.ver_##arr = 1;', '++dev.ver_##arr;'), effect, ring, 1),
        ('changed_write_lost', device.replace('if (n && memcmp(', 'if (0 && memcmp('), effect, ring, 1),
        ('frame_lifetime_lost', device, effect, ring.replace(' || up[st].serial != frame_serial', ''), 1),
    ]
    with tempfile.TemporaryDirectory(prefix='effect-ctab-') as directory:
        for name, d, e, r, expected in variants:
            rc, output = build_run(Path(directory), name, d, e, r, init)
            assert rc == expected and re.search(r'\d+ failed of \d+ checks', output), f'{name}: expected exit {expected}'
            if name == 'stale_parameter':
                assert output.count('FAIL: CTAB A->B->A restores A') == 6, 'stale skip must fail F/I/B restoration in both stages'
    print('PASS: constant uploads, live CTAB restoration, and nine broken controls')
    return 0


if __name__ == '__main__':
    sys.exit(main())
