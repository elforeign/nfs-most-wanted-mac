#!/usr/bin/env python3
"""Draw preparation regression using production fragments, no assets or GPU.

Repeated identical draws: original/current declaration parses 1000/1,
layout preparations 1000/1, sampler linear probes 32000/0, static VB binds 1000/1.
Correctness mismatches: current 0. Broken controls: declaration reuse 7,
FVF 2, final VS 1, UP mode 1, UP stride 1, stream stride 2, integer key 2,
POSITIONT 1, missing input 2, sampler key 20, renamed VB 3, offset 1, ring 1.
Encoder reset / SSAO state / lamp state removal: 1 invariant violation each.
Each of the four recorder reset removals: 1 invariant violation (current 0).
Covers object/handle reuse, FVF, integer formats, POSITIONT, missing inputs,
final VS variants, UP/strides, complete sampler keys, renamed buffers, ring
restoration, every encoder/material reset, and independent SSAO/lamp states.
Autodiscovered by runtime/offline_tests.sh.
"""
from pathlib import Path
import hashlib
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent
SOURCE = (ROOT / 'runtime/render.m').read_text()

ORIGINAL_LAYOUT = r'''    DrawAttr attr[16];
    int nattr = 0;
    uint32_t used_streams = 0;
    int need_zero = 0;
    // vkey/pkey: FNV over the bytes below, in this order (FNV is sequential, so hashing the concatenation equals the
    // chained per-field hashing it replaced); the last draw's bytes and keys are kept, as consecutive draws mostly
    // repeat them. Game thread only.
    uint8_t vb[16 * (1 + sizeof(Elem)) + 16 * 4];
    size_t vn = 0;
    uint64_t vkey = 0;
    for (int k = 0; k < vse->refl.n_inputs && nattr < 16; ++k) {
        SmInput *in = &vse->refl.inputs[k];
        int found = -1;
        for (int i = 0; i < nel; ++i) if (el[i].usage == in->usage && el[i].index == in->usage_index) { found = i; break; }
        if (found < 0 && in->usage == SM_USAGE_POSITION)        // POSITIONT data feeding a vs position input
            for (int i = 0; i < nel; ++i) if (el[i].usage == SM_USAGE_POSITIONT) { found = i; break; }
        if (found < 0) {
            attr[nattr++] = (typeof(attr[0])){(uint8_t)in->reg, 0, 30, 1, 0};
            need_zero = 1;
            vb[vn++] = (uint8_t)in->reg;
            continue;
        }
        uint8_t b = up_vertices ? 0 : el[found].stream;
        attr[nattr++] = (typeof(attr[0])){(uint8_t)in->reg, el[found].type, b, 0, el[found].offset};
        used_streams |= 1u << b;
        vb[vn++] = (uint8_t)in->reg;
        memcpy(vb + vn, &el[found], sizeof(Elem)); vn += sizeof(Elem);
    }
    uint32_t strides[16] = {0};
    for (int s = 0; s < 16; ++s) {
        if (!(used_streams & (1u << s))) continue;
        uint32_t stride = up_vertices ? up_stride : dev.stream_stride[s];
        if (!stride) stride = 4;
        strides[s] = stride;
        memcpy(vb + vn, &stride, 4); vn += 4;
    }
    static uint8_t last_vb[sizeof vb];
    static size_t last_vn = SIZE_MAX;
    static uint64_t last_vkey;
    if (vn == last_vn && !memcmp(vb, last_vb, vn)) vkey = last_vkey;
    else { vkey = fnv(vb, vn, 0); memcpy(last_vb, vb, vn); last_vn = vn; last_vkey = vkey; }

'''
ORIGINAL_SAMPLER = r'''static id<MTLSamplerState> sampler_state(int stage, int compare) {
    uint32_t *ss = dev.ss[stage], canonical[14];
    if (diag_on && sign_diag_on && !compare && sign_diag_canonical((unsigned)stage)) {
        memcpy(canonical, ss, sizeof canonical); ss = canonical;
        ss[5] = ss[6] = 3; ss[7] = 2; ss[8] = ss[9] = 0; ss[10] = 16;
    }
    int white_border = compare == 1 && x360_shadow_border();
    uint32_t key[4] = {ss[1] | ss[2] << 4 | ss[3] << 8 | ss[5] << 12 | ss[6] << 16 | ss[7] << 20 | (uint32_t)compare << 24, ss[10], ss[4], ss[9]};
    key[0] |= (uint32_t)white_border << 28;   // F10 shadow fit can change this while the sampler cache stays alive
    for (int i = 0; i < nsamplers; ++i) if (!memcmp(sampler_keys[i], key, sizeof key)) return samplers[i];
    MTLSamplerDescriptor *d = [MTLSamplerDescriptor new];
    MTLSamplerAddressMode am[6] = {MTLSamplerAddressModeRepeat, MTLSamplerAddressModeRepeat, MTLSamplerAddressModeMirrorRepeat,
                                   MTLSamplerAddressModeClampToEdge, MTLSamplerAddressModeClampToBorderColor, MTLSamplerAddressModeMirrorClampToEdge};
    d.sAddressMode = am[ss[1] < 6 ? ss[1] : 1]; d.tAddressMode = am[ss[2] < 6 ? ss[2] : 1]; d.rAddressMode = am[ss[3] < 6 ? ss[3] : 1];
    d.magFilter = ss[5] >= 2 ? MTLSamplerMinMagFilterLinear : MTLSamplerMinMagFilterNearest;
    d.minFilter = ss[6] >= 2 ? MTLSamplerMinMagFilterLinear : MTLSamplerMinMagFilterNearest;
    d.mipFilter = ss[7] == 0 ? MTLSamplerMipFilterNotMipmapped : ss[7] == 1 ? MTLSamplerMipFilterNearest : MTLSamplerMipFilterLinear;
    if (ss[5] == 3 || ss[6] == 3) d.maxAnisotropy = ss[10] < 1 ? 1 : ss[10] > 16 ? 16 : ss[10];
    d.borderColor = (ss[4] >> 24) > 127 ? MTLSamplerBorderColorOpaqueBlack : MTLSamplerBorderColorTransparentBlack;
    if (ss[4] == 0xFFFFFFFF) d.borderColor = MTLSamplerBorderColorOpaqueWhite;
    d.lodMinClamp = (float)ss[9];
    if (compare) d.compareFunction = MTLCompareFunctionLessEqual;
    static const char *test_cmp = (const char *)1;      // test only: NFS_TEST_SHADOW_COMPARE=never|always|greater
    if (test_cmp == (const char *)1) test_cmp = getenv("NFS_TEST_SHADOW_COMPARE");
    if (compare && test_cmp) d.compareFunction = !strcmp(test_cmp, "never") ? MTLCompareFunctionNever : !strcmp(test_cmp, "always") ?
                                                MTLCompareFunctionAlways : MTLCompareFunctionGreater;
    if (white_border) { // X360 shadow sampler: outside the map reads depth 1 (lit)
        d.sAddressMode = d.tAddressMode = MTLSamplerAddressModeClampToBorderColor;
        d.borderColor = MTLSamplerBorderColorOpaqueWhite;
    }
    // The probe's map is one texel: through a border sampler, linear PCF blends it with the white border by the distance
    // from the map's centre (up to 75 % lit at the corners), so receivers the real map shadows came out brighter.
    if (compare == 2) d.sAddressMode = d.tAddressMode = MTLSamplerAddressModeClampToEdge;
    id<MTLSamplerState> s = [mtl newSamplerStateWithDescriptor:d];
    if (diag_on && nsamplers >= MAX_SAMPLERS) ++ad1.samp_miss_full;
    if (nsamplers < MAX_SAMPLERS) { memcpy(sampler_keys[nsamplers], key, sizeof key); samplers[nsamplers++] = s; }
    return s;
}'''
ORIGINAL_BIND = '[enc setVertexBuffer:gpu_buf(obj(dev.stream[s])) offset:dev.stream_off[s] atIndex:(NSUInteger)s];'
ORIGINAL_DECL = 'int nel = decl_elems(el);'
ORIGINAL_SHA = '1289f0f57324ada2e52a84413c17ad3af30907e7e9356a30b75597486a628e15'
RESET_COUNT = 19


def function(text, name):
    start = text.index(name + '(')
    start = text.rfind('\n', 0, start) + 1
    body = text.index('{', start)
    end, depth = body + 1, 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end]


PRELUDE = r'''
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <stdio.h>
#include <string.h>
#include "runtime/d3d9_internal.h"
#include "host/shaders/sm_translate.h"
uint8_t *G;
Device dev;
id<MTLDevice> mtl;
static id<MTLRenderCommandEncoder> enc;
static Obj objects[4];
Obj *obj(uint32_t g) { return g && g < 4 ? &objects[g] : NULL; }
static uint32_t vcache_serial;
typedef struct { SmResult refl; } ShaderEntry;
int diag_on;
static int sign_diag_on, border, canonical_on;
static int sign_diag_canonical(unsigned stage) { return canonical_on && stage == 0; }
int x360_shadow_border(void) { return border; }
#define MAX_SAMPLERS 512
static id<MTLSamplerState> samplers[MAX_SAMPLERS];
static uint32_t sampler_keys[MAX_SAMPLERS][4];
static int nsamplers;
static struct { int samp_miss_full; } ad1;
static int parses, layouts, probes, binds, resolutions, failures;
static int sampler_compare(const void *a, const void *b, size_t n) { ++probes; return memcmp(a,b,n); }
static id buffers[4];
static id<MTLBuffer> gpu_buf(Obj *o) { ++resolutions; return o ? (id)buffers[o-objects] : nil; }
@interface DeviceSpy : NSObject @end
@implementation DeviceSpy
- (id)newSamplerStateWithDescriptor:(MTLSamplerDescriptor *)d { return [d copy]; }
@end
@interface EncoderSpy : NSObject { @public id bound[32]; NSUInteger offsets[32]; } @end
@implementation EncoderSpy
- (void)setVertexBuffer:(id)b offset:(NSUInteger)off atIndex:(NSUInteger)s {
    ++binds; bound[s]=b; offsets[s]=off;
}
@end
static EncoderSpy *spy;
#define CHECK(condition) do { if (!(condition)) { ++failures; fprintf(stderr,"line %d: %s\n",__LINE__,#condition); } } while(0)
'''

MAIN = r'''
static ShaderEntry vs, variant;
static void declaration(unsigned handle, const Elem *el, unsigned count) {
    Obj *o=&objects[handle]; o->kind=K_DECL; o->decl_mem=handle*512; o->decl_n=count+1;
    for(unsigned i=0;i<count;i++) {
        unsigned p=o->decl_mem+8*i;
        M16(p)=el[i].stream; M16(p+2)=el[i].offset; M8(p+4)=el[i].type;
        M8(p+6)=el[i].usage; M8(p+7)=el[i].index;
    }
    M16(o->decl_mem+8*count)=0xff;
}
static const DrawInputLayout *layout(int up, unsigned stride) {
    return draw_input_layout(draw_declaration(),&vs,up,stride);
}
static void sampler_defaults(void) {
    memset(dev.ss,0,sizeof dev.ss);
    for(unsigned s=0;s<20;s++) { dev.ss[s][1]=dev.ss[s][2]=dev.ss[s][3]=1; dev.ss[s][5]=dev.ss[s][6]=2; }
}
static MTLSamplerDescriptor *sample(int stage,int compare) { return (id)sampler_state(stage,compare); }
static int performance(void) {
    dev.fvf=0x002; dev.stream_stride[0]=12;
    vs.refl.n_inputs=1; vs.refl.inputs[0]=(SmInput){.reg=0,.usage=SM_USAGE_POSITION};
    for(int i=0;i<1000;i++) layout(0,0);
    int p=parses,l=layouts;
    sampler_defaults();
    for(unsigned i=0;i<32;i++) { dev.ss[0][9]=i; sample(0,0); }
    probes=0; for(int i=0;i<1000;i++) sample(0,0);
    dev.stream[0]=3; buffers[3]=[NSObject new];
    for(int i=0;i<1000;i++) bind_static_vertex_buffer(0);
    printf("parses=%d layouts=%d sampler_probes=%d vb_binds=%d resolutions=%d\n",p,l,probes,binds,resolutions);
    CHECK(p==1); CHECK(l==1); CHECK(probes==0); CHECK(binds==1); CHECK(resolutions==1000);
    return failures!=0;
}
static void correctness(void) {
    Elem pos={0,2,SM_USAGE_POSITION,0,0};
    declaration(1,&pos,1); dev.decl=1; dev.stream_stride[0]=12; dev.stream_stride[2]=12;
    vs.refl.n_inputs=1; vs.refl.inputs[0]=(SmInput){.reg=0,.usage=SM_USAGE_POSITION};
    const DrawInputLayout *l=layout(0,0); CHECK(l->attr[0].offset==0 && l->strides[0]==12);
    // Reuse both the Obj address and guest handle with a new immutable declaration.
    memset(&objects[1],0,sizeof objects[1]); pos.offset=8; declaration(1,&pos,1);
    l=layout(0,0); CHECK(l->attr[0].offset==8);
    pos.offset=4; declaration(2,&pos,1); dev.decl=2;
    l=layout(0,0); CHECK(l->attr[0].offset==4);
    dev.decl=0; dev.fvf=0x002; l=layout(0,0); CHECK(l->attr[0].fmt_type==2);
    dev.fvf=0x004; l=layout(0,0); CHECK(l->attr[0].fmt_type==3 && !l->need_zero); // POSITIONT fallback
    dev.fvf=0; l=layout(0,0); CHECK(l->need_zero && l->attr[0].buf==30 && l->used_streams==0);
    // Signed and unsigned integer inputs use reflected registers, and float declarations clear both masks.
    Elem ints[]={{0,6,SM_USAGE_POSITION,0,0},{0,5,SM_USAGE_COLOR,0,4},{0,7,SM_USAGE_TEXCOORD,1,8}};
    memset(&objects[1],0,sizeof objects[1]); declaration(1,ints,3); dev.decl=1;
    vs.refl.n_inputs=3;
    vs.refl.inputs[0]=(SmInput){.reg=2,.usage=SM_USAGE_POSITION};
    vs.refl.inputs[1]=(SmInput){.reg=5,.usage=SM_USAGE_COLOR};
    vs.refl.inputs[2]=(SmInput){.reg=6,.usage=SM_USAGE_TEXCOORD,.usage_index=1};
    SmOptions vo={0}; draw_integer_inputs(draw_declaration(),&vs,&vo);
    CHECK(vo.vs_input_int==((1<<2)|(1<<6)) && vo.vs_input_uint==(1<<5));
    variant=vs; variant.refl.inputs[0].reg=1;
    draw_integer_inputs(draw_declaration(),&variant,&vo); CHECK(vo.vs_input_int==((1<<1)|(1<<6)));
    pos=(Elem){2,2,SM_USAGE_POSITION,0,0}; memset(&objects[1],0,sizeof objects[1]); declaration(1,&pos,1);
    draw_integer_inputs(draw_declaration(),&variant,&vo); CHECK(vo.vs_input_int==0 && vo.vs_input_uint==0);
    l=draw_input_layout(draw_declaration(),&vs,0,0); CHECK(l->need_zero && l->used_streams==4);
    uint64_t key=l->vkey;
    l=draw_input_layout(draw_declaration(),&variant,0,0); CHECK(l->attr[0].reg==1 && l->vkey!=key);
    l=layout(0,0); key=l->vkey;
    l=layout(1,12); CHECK(l->used_streams==1 && l->attr[0].buf==0 && l->strides[0]==12 && l->vkey!=key);
    l=layout(1,28); CHECK(l->strides[0]==28);
    l=layout(0,0); CHECK(l->used_streams==4 && l->strides[2]==12);
    dev.stream_stride[2]=24; l=layout(0,0); CHECK(l->strides[2]==24);
    dev.stream_stride[2]=0; l=layout(0,0); CHECK(l->strides[2]==4);
    // The complete existing sampler key and stage-local canonicalization remain authoritative.
    sampler_defaults();
    MTLSamplerDescriptor *d=sample(0,0); CHECK(d.compareFunction==MTLCompareFunctionNever);
    d=sample(0,1); CHECK(d.compareFunction==MTLCompareFunctionLessEqual);
    border=1; d=sample(0,1); CHECK(d.borderColor==MTLSamplerBorderColorOpaqueWhite && d.sAddressMode==MTLSamplerAddressModeClampToBorderColor);
    d=sample(0,2); CHECK(d.sAddressMode==MTLSamplerAddressModeClampToEdge);
    border=0; d=sample(0,1); CHECK(d.sAddressMode==MTLSamplerAddressModeRepeat);
    dev.ss[0][1]=2; d=sample(0,0); CHECK(d.sAddressMode==MTLSamplerAddressModeMirrorRepeat);
    dev.ss[0][2]=3; d=sample(0,0); CHECK(d.tAddressMode==MTLSamplerAddressModeClampToEdge);
    dev.ss[0][3]=4; d=sample(0,0); CHECK(d.rAddressMode==MTLSamplerAddressModeClampToBorderColor);
    dev.ss[0][5]=1; d=sample(0,0); CHECK(d.magFilter==MTLSamplerMinMagFilterNearest);
    dev.ss[0][6]=1; d=sample(0,0); CHECK(d.minFilter==MTLSamplerMinMagFilterNearest);
    dev.ss[0][7]=1; d=sample(0,0); CHECK(d.mipFilter==MTLSamplerMipFilterNearest);
    dev.ss[0][7]=2; d=sample(0,0); CHECK(d.mipFilter==MTLSamplerMipFilterLinear);
    dev.ss[0][5]=3; dev.ss[0][10]=8; d=sample(0,0); CHECK(d.maxAnisotropy==8);
    dev.ss[0][10]=16; d=sample(0,0); CHECK(d.maxAnisotropy==16);
    dev.ss[0][4]=0xff000000; d=sample(0,0); CHECK(d.borderColor==MTLSamplerBorderColorOpaqueBlack);
    dev.ss[0][4]=0xffffffff; d=sample(0,0); CHECK(d.borderColor==MTLSamplerBorderColorOpaqueWhite);
    dev.ss[0][9]=3; d=sample(0,0); CHECK(d.lodMinClamp==3);
    dev.ss[0][9]=0; d=sample(0,0); CHECK(d.lodMinClamp==0);
    diag_on=sign_diag_on=canonical_on=1; d=sample(0,0);
    CHECK(d.lodMinClamp==0 && d.mipFilter==MTLSamplerMipFilterLinear && d.minFilter==MTLSamplerMinMagFilterLinear);
    diag_on=sign_diag_on=canonical_on=0; d=sample(0,0); CHECK(d.minFilter==MTLSamplerMinMagFilterNearest);
    dev.ss[19][9]=7; d=sample(19,0); CHECK(d.lodMinClamp==7);
    // A full table must not memoize an uncached result as the last valid slot.
    for(unsigned attempt=0;nsamplers<MAX_SAMPLERS && attempt<MAX_SAMPLERS;attempt++) {
        dev.ss[1][9]=nsamplers+100; sample(1,0);
    }
    CHECK(nsamplers==MAX_SAMPLERS);
    dev.ss[1][9]=900; d=sample(1,0); CHECK(d.lodMinClamp==900);
    dev.ss[1][9]=901; d=sample(1,0); CHECK(d.lodMinClamp==901);
    // Always resolve dirty buffers before comparison; compare actual Metal identities and offsets.
    memset(&eb,0,sizeof eb); dev.stream[0]=3; buffers[3]=[NSObject new];
    bind_static_vertex_buffer(0); int b=binds; bind_static_vertex_buffer(0); CHECK(binds==b);
    buffers[3]=[NSObject new]; bind_static_vertex_buffer(0); CHECK(binds==b+1 && spy->bound[0]==buffers[3]);
    dev.stream_off[0]=8; bind_static_vertex_buffer(0); CHECK(binds==b+2 && spy->offsets[0]==8);
    id<MTLBuffer> ring=(id)[NSObject new];
    ring_binding(ring,16,0); bind_static_vertex_buffer(0); CHECK(spy->bound[0]==buffers[3] && spy->offsets[0]==8);
    reset_bindings(); independent_bindings();
}
int main(int argc,char **argv) { @autoreleasepool {
    (void)argv; G=calloc(4096,1); spy=[EncoderSpy new]; enc=(id)spy; mtl=(id)[DeviceSpy new];
    if(argc>1)return performance();
    correctness(); printf("mismatches=%d\n",failures); free(G); return failures!=0;
} }
'''


def fragments(source):
    elem = re.search(r'typedef struct \{ uint8_t stream, type, usage, index; uint16_t offset; \} Elem;', source).group()
    begin = source.index('typedef struct { uint8_t reg, fmt_type, buf, zero;')
    end = source.index('\n}', source.index('static void bind_static_vertex_buffer(', begin)) + 2
    setup = source[begin:end]
    setup = setup.replace('last.nel = decl_elems(last.el);', '++parses; last.nel = decl_elems(last.el);')
    setup = setup.replace('last = (DrawInputLayout){', '++layouts; last = (DrawInputLayout){')
    eb = re.search(r'static struct \{\n    uint32_t have;.*?\} eb;', source, re.S).group()
    sampler = function(source, 'sampler_state').replace('memcmp(sampler_keys[i], key, sizeof key)', 'sampler_compare(sampler_keys[i], key, sizeof key)')
    ring = re.search(r'\[enc setVertexBuffer:b offset:off atIndex:\(NSUInteger\)s\];\s*eb\.vbuf\[s\] = NULL;', source)
    assert ring, 'ring must invalidate the cached static binding'
    resets = re.findall(r'memset\(&eb,\s*0,\s*sizeof eb\)', source)
    assert len(resets) == RESET_COUNT, 'an existing encoder/material reset was removed'
    reset_body = '\n'.join('b=binds; '+r+'; bind_static_vertex_buffer(0); CHECK(binds==b+1);' for r in resets)
    recorder_resets = []
    for name in ('lamp_eb', 'ssao_eb', 'ssao_reference_eb', 'ssao_cpu_reference_eb'):
        found = re.findall(r'memset\(&'+name+r',\s*0,\s*sizeof '+name+r'\)', source)
        assert len(found) == (1 if name == 'lamp_eb' else 2), f'{name} recorder reset lost'
        for reset in found:
            recorder_resets.append('{ typeof(eb) saved=eb; '+name+'=eb; '+reset+'; eb='+name+
                                   '; b=binds; bind_static_vertex_buffer(0); CHECK(binds==b+1); eb=saved; }')
    # Execute the production save/swap/restore assignments, with distinct encoder binding state for every recorder.
    assignments = ('lamp_saved_eb = eb;', 'eb = lamp_eb;', 'lamp_eb = eb;', 'eb = lamp_saved_eb;',
                   'ssao_saved_eb=eb;', 'eb=reference==2?ssao_cpu_reference_eb:reference?ssao_reference_eb:ssao_eb;',
                   'if(reference==2)ssao_cpu_reference_eb=eb;else if(reference)ssao_reference_eb=eb;else ssao_eb=eb;',
                   'eb=ssao_saved_eb;')
    for assignment in assignments:
        assert assignment in source, f'independent binding state lost: {assignment}'
    independent = '''static void independent_bindings(void) {
        static typeof(eb) lamp_eb,lamp_saved_eb,ssao_eb,ssao_reference_eb,ssao_cpu_reference_eb,ssao_saved_eb;
        int b=binds;
        LAMP_SAVE LAMP_SWAP bind_static_vertex_buffer(0); CHECK(binds==b+1);
        LAMP_COMMIT LAMP_RESTORE b=binds; bind_static_vertex_buffer(0); CHECK(binds==b);
        for(int reference=0;reference<3;reference++) {
            b=binds; SSAO_SAVE SSAO_SWAP bind_static_vertex_buffer(0); CHECK(binds==b+1);
            SSAO_COMMIT SSAO_RESTORE b=binds; bind_static_vertex_buffer(0); CHECK(binds==b);
        }
        RECORDER_RESETS
    }'''
    independent = independent.replace('RECORDER_RESETS', '\n'.join(recorder_resets))
    for marker, assignment in zip(('LAMP_SAVE','LAMP_SWAP','LAMP_COMMIT','LAMP_RESTORE','SSAO_SAVE','SSAO_SWAP','SSAO_COMMIT','SSAO_RESTORE'), assignments):
        independent = independent.replace(marker, assignment)
    # Cache lookup must happen after all final VS substitutions, including optional headlight variants.
    draw = source[source.rindex('static void draw_impl('):]
    assert draw.index('draw_input_layout(declaration, vse,') > draw.index('vse=hv;pse=hp;')
    assert 'bind_static_vertex_buffer((unsigned)s)' in draw
    assert 'draw_integer_inputs(declaration, vse, &vo)' in draw
    return '\n'.join((PRELUDE, eb, elem, function(source,'decl_elems'), function(source,'fnv'), setup, sampler,
                      'static void ring_binding(id<MTLBuffer> b,uint32_t off,int s) { '+ring.group()+' }',
                      'static void reset_bindings(void) { int b; '+reset_body+' }', independent, MAIN))


def run_case(tmp, label, text, perf=False):
    path, exe = tmp/(label+'.m'), tmp/label
    path.write_text(text)
    build = subprocess.run(['clang','-O1','-fobjc-arc','-I',str(ROOT),'-I',str(ROOT/'runtime'),
                            '-I',str(ROOT/'recomp'),'-I',str(ROOT/'recomp/out'), str(path),'-framework','Foundation','-framework','Metal',
                            '-o',str(exe)],capture_output=True,text=True,timeout=60)
    assert build.returncode == 0, build.stderr[-4000:]
    run = subprocess.run([str(exe)]+(['perf'] if perf else []),capture_output=True,text=True,timeout=15)
    return run.returncode, run.stdout.strip(), run.stderr


def main():
    assert hashlib.sha256((ORIGINAL_LAYOUT+ORIGINAL_SAMPLER+ORIGINAL_BIND+ORIGINAL_DECL).encode()).hexdigest()==ORIGINAL_SHA
    text=fragments(SOURCE)
    with tempfile.TemporaryDirectory(prefix='draw-setup-') as directory:
        tmp=Path(directory)
        for perf in (False,True):
            rc,out,err=run_case(tmp,'current',text,perf)
            assert rc==0,(out,err)
            print('current:',out,flush=True)
        # The exact pinned original draw setup and sampler perform redundant work on the same 1000-draw fixture.
        original='''static const DrawInputLayout *draw_input_layout(const DrawDeclaration *decl,const ShaderEntry *vse,int up_vertices,uint32_t up_stride) {
            const Elem *el=decl->el; int nel=decl->nel; ++layouts;
        '''+ORIGINAL_LAYOUT+'''
            static DrawInputLayout out; out.nattr=nattr; out.need_zero=need_zero; out.used_streams=used_streams; out.vkey=vkey;
            memcpy(out.attr,attr,sizeof attr); memcpy(out.strides,strides,sizeof strides); return &out;
        }'''
        before=text.replace(function(text,'draw_input_layout'),original)
        before=before.replace(function(before,'draw_declaration'),
                              'static const DrawDeclaration *draw_declaration(void) { '
                              'static DrawDeclaration out; Elem el[32]; ++parses; '+ORIGINAL_DECL+
                              ' out.nel=nel; memcpy(out.el,el,nel*sizeof *el); return &out; }')
        before=before.replace(function(before,'sampler_state'),ORIGINAL_SAMPLER.replace('memcmp(sampler_keys[i], key, sizeof key)','sampler_compare(sampler_keys[i], key, sizeof key)'))
        before=before.replace(function(before,'bind_static_vertex_buffer'),
                              'static void bind_static_vertex_buffer(unsigned s) { '+ORIGINAL_BIND+' }')
        rc,out,err=run_case(tmp,'original',before,True)
        assert rc==1 and 'parses=1000 layouts=1000 sampler_probes=32000 vb_binds=1000 resolutions=1000'==out,(out,err)
        print('original:',out,'(rejected)',flush=True)
        mutants={
            'declaration_reuse': ('if (d && !d->vc_serial) d->vc_serial = ++vcache_serial;', 'if (d) d->vc_serial = 1;'),
            'fvf': ('last.fvf != dev.fvf', '0'),
            'final_vs': ('last.vs == vs', '1'),
            'up_mode': ('last.up == up', '1'),
            'up_stride': ('last.up_stride == up_stride', '1'),
            'stream_stride': ('!memcmp(last.source_strides, dev.stream_stride, sizeof last.source_strides)', '1'),
            'integer_key': ('last_vs != vs || serial != decl->serial', '0 || 0'),
            'positiont': ('if (found < 0 && in->usage == SM_USAGE_POSITION)', 'if (0)'),
            'zero_input': ('last.need_zero = 1;', 'last.need_zero = 0;'),
            'sampler_key': ('!memcmp(sampler_keys[previous], key, sizeof key)', '1'),
            'buffer_identity': ('eb.vbuf[s] != (__bridge void *)b', '0'),
            'buffer_offset': ('eb.voff[s] != dev.stream_off[s]', '0'),
            'ring_restore': ('eb.vbuf[s] = NULL;', '(void)0;'),
        }
        for label,(old,new) in mutants.items():
            assert old in text,old
            broken=text.replace(old,new,1)
            rc,out,err=run_case(tmp,label,broken)
            assert rc==1,(label,out,err)
            print(label+':',out,'(rejected)',flush=True)
        state_controls = [('encoder_reset','memset(&eb, 0, sizeof eb)'),
                          ('ssao_state','eb=reference==2?ssao_cpu_reference_eb:reference?ssao_reference_eb:ssao_eb;'),
                          ('lamp_state','eb = lamp_eb;')]
        for name in ('lamp_eb','ssao_eb','ssao_reference_eb','ssao_cpu_reference_eb'):
            reset = re.search(r'memset\(&'+name+r',\s*0,\s*sizeof '+name+r'\)', SOURCE).group()
            state_controls.append((name+'_reset', reset))
        for label,old in state_controls:
            assert old in SOURCE
            try:
                fragments(SOURCE.replace(old,'(void)0;',1))
            except AssertionError:
                print(label+': invariant violations=1 (rejected)',flush=True)
            else:
                raise AssertionError(label+' was not rejected')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
