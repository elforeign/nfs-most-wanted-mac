#!/usr/bin/env python3
"""R20 offline draw contract. No game entry point or Unicorn.
Runs production lifecycle/queue hooks against synthetic guest memory, tests the
world/local and intensity/lane contracts, and compiles production MSL with Metal
using actual PC shader reflection. --mutate lane must fail the scalar check.
Verified scalar max error: 1.11e-16 PASS; --mutate lane error .119508264 FAIL.
--cpu-only exits0 here; full proof exits2 BLOCKED without a Metal device/toolchain.
Split P2 descriptor acceptance: 2 checks pass; ID1-only mutant exits1 at the P2 check.
"""
import argparse
import ast
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
MATERIALS = (ROOT / 'runtime/materials.m').read_text()
# The aux shaders' literal constants are generated from the player's ZZDATA0.BIN (tools/gen_tables.py); the reference
# below takes the same values from there, never from this file.
_LITS_INC = ROOT / 'runtime/gen/x360_shader_lits.inc'
LITS = dict(re.findall(r'#define (\w+) "(.*)"', _LITS_INC.read_text())) if _LITS_INC.exists() else {}
# The shader bodies themselves are translated from the same file at build time (x360_shader_body.inc, X360B_* macros).
_BODY_INC = ROOT / 'runtime/gen/x360_shader_body.inc'
if _BODY_INC.exists():
    for _m in re.finditer(r'^#define (X360B_\w+) ((?:.*\\\n)*.*)$', _BODY_INC.read_text(), re.M):
        LITS[_m.group(1)] = ''.join(ast.literal_eval(_s) for _s in re.findall(r'"(?:[^"\\]|\\.)*"', _m.group(2)))


def lit4(name):
    return [float(x) for x in LITS[name][len('float4('):-1].split(',')]


class Blocked(RuntimeError):
    pass


def run(args, **kwargs):
    r = subprocess.run(args, capture_output=True, text=True, **kwargs)
    if r.returncode:
        raise AssertionError(f'{args[0]} exit {r.returncode}: {r.stdout[-2000:]} {r.stderr[-3500:]}')
    return r.stdout


def cstring(name):
    part = MATERIALS.split(f'static const char {name}[] =\n', 1)[1].split('\n;', 1)[0]
    return ''.join(ast.literal_eval(m.group(0)) if m.group(0).startswith('"') else LITS[m.group(0)]
                   for m in re.finditer(r'"(?:[^"\\]|\\.)*"|\b(?:X360\w+|PCS_\w+)\b', part))


def check_host(tmp):
    hooks = MATERIALS.split('// ---------------- R20: load generations', 1)[1]
    hooks = hooks[hooks.index('typedef struct'):hooks.index('// ---------------- R27 grass:')]
    harness = r'''
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "materials.h"
#include "auxlights.c"
static unsigned char storage[0x1000000];
static unsigned char *G=storage;
#define M32(a) (*(uint32_t *)(G+(uint32_t)(a)))
#define CUR_VIEW 0x00982A20u
typedef struct { uint32_t eax,ecx,edi,esp; } Cpu;
static int diag_on;
static int split_fixture;
static int split_view_rect(uint32_t v,uint32_t *r) { (void)r; return split_fixture && v==0x9196C0; }
static unsigned long long host_ticks(void) { return 1; }
static void diag_event(const char *s,...) { (void)s; }
HOOKS
static unsigned append_count, lit_count;
void f_007311F0_orig(Cpu *c) { c->eax=1; }
void f_007307E0_orig(Cpu *c) { (void)c; }
void f_006E0AB0_orig(Cpu *c) {
    (void)c;unsigned old=M32(0x00982CDC);if(old>=4096)return;
    unsigned q=0x0093E878+old*0x44;
    M32(q)=0x7000;M32(q+16)=0x8000;M32(q+28)=0x9000;
    ++M32(0x00982CDC);
}
void f_006D7660_orig(Cpu *c) { (void)c; }
void materials_glass_clear(void) {}   // called by the f_006D7660 hook since R19/R28; no state to clear here
void materials_trees_clear(void) {}
void mirror_scenery_render(Cpu *c) {
    for(unsigned i=0;i<append_count;i++) { if(i==lit_count) aux_source.valid=0; f_006E0AB0(c); }
}
static void load(Cpu *c,unsigned chunk,unsigned section,unsigned instance) {
    M32(chunk)=0x80034100; M32(chunk+4)=0x58;
    M32(chunk+8)=0x34101; M32(chunk+12)=0x50;
    unsigned h=(chunk+31)&~15u;
    M32(h+0xc)=section; M32(h+0x20)=instance; M32(h+0x24)=1;
    M32(c->esp+4)=chunk; f_007311F0(c);
}
int main(void) {
    auxlights_set_enabled(1);
    const float w[4][4]={{0,1,0,0},{-1,0,0,0},{0,0,1,0},{100,200,30,1}};
    const float mesh[6]={-4,-3,-2,4,3,2}, bbox[6]={97,196,28,103,204,32};
    AuxLightContext ctx={"fixture",2,2,{{{101,202,31},15,{.2,.6,.9},7},{{98,199,30},10,{.8,.3,.4},19}}}, local;
    assert(auxlights_local(&ctx,w,mesh,bbox,&local)==2);
    assert(local.lights[0].position[0]==2 && local.lights[0].position[1]==-1 && local.lights[0].position[2]==1);
    assert(local.lights[1].position[0]==-1 && local.lights[1].position[1]==2 && local.lights[1].position[2]==0);
    float bad[4][4]; memcpy(bad,w,sizeof bad); memcpy(bad[1],bad[0],16);
    assert(!auxlights_local(&ctx,bad,mesh,bbox,&local));
    const float scaled[4][4]={{0,2,0,0},{-3,0,0,0},{0,0,.5,0},{100,200,30,1}};
    const float scaled_bbox[6]={91,192,29,109,208,31};
    AuxLightContext scaled_source=ctx;
    scaled_source.lights[0].position[0]=103;scaled_source.lights[0].position[1]=204;scaled_source.lights[0].position[2]=30.5;
    assert(auxlights_local(&scaled_source,scaled,mesh,scaled_bbox,&local)==2);
    assert(local.lights[0].position[0]==2 && local.lights[0].position[1]==-1 && local.lights[0].position[2]==1);
    float far[6]={1000,1000,1000,1001,1001,1001};
    assert(!auxlights_local(&ctx,w,mesh,far,&local));
    // Imported world positions must agree with the exact instance bbox. Inspect
    // every key using identity mesh bounds, not just the extraction sentinel.
    const float identity[4][4]={{1,0,0,0},{0,1,0,0},{0,0,1,0},{0,0,0,1}};
    unsigned spaces=0, keyidx=0;
    for(unsigned i=0;i<AUX_DATA_ROWS;i++) {
        float bounds[6]; memcpy(bounds,aux_keys[i].bbox,24);
        if(auxlights_local(&aux_contexts[aux_keys[i].context_index],identity,bounds,bounds,&local)) { ++spaces; keyidx=i; }
    }
    printf("direct source-world bbox fits=%u/%u (source-local data requires its verified instance matrix)\n",spaces,AUX_DATA_ROWS);
    // Actual queue hooks: two lit slots, then a fresh unlit slot. Shared mesh,
    // reverse sorted traversal, full queue, unload/reload and instance reuse.
    Cpu c={.esp=0x2000,.edi=0x3000};
    unsigned instance=0x4000,chunk=0x5000;
    memcpy(G+instance,aux_keys[keyidx].bbox,24);
    M32(instance+0x18)=0x200;load(&c,chunk,aux_keys[keyidx].section_id,instance);
    M32(c.edi+8)=instance; M32(c.esp)=0x00724203;
    memcpy(G+0x9000,identity,64);append_count=3;lit_count=2;f_006DA9B0(&c);
    assert(M32(0x00982CDC)==3 && aux_queue[0].valid && aux_queue[1].valid && !aux_queue[2].valid && !aux_source.valid);
    M32(CUR_VIEW)=0x6000; M32(0x6004)=1;
    for(unsigned i=0;i<3;i++) {
        unsigned q=0x0093E878+i*0x44;
        M32(q)=0x7000;M32(q+16)=0x8000;M32(0x8004)=1;M32(q+28)=0x9000;
    }
    memcpy(G+0x7000,aux_keys[keyidx].bbox,24); memcpy(G+0x9000,identity,64);
    materials_aux_record(0x0093E878+0x44); assert(mat_aux_draw.valid);
    materials_aux_record(0x0093E878+2*0x44); assert(!mat_aux_draw.valid);
    materials_aux_record(0x0093E878); assert(mat_aux_draw.valid);
    M32(0x6004)=2;M32(0x919720)=0x6000;split_fixture=1;
    materials_aux_record(0x0093E878);if(!mat_aux_draw.valid)return 1;
    split_fixture=0;materials_aux_record(0x0093E878);if(mat_aux_draw.valid)return 1;M32(0x6004)=1;
    M32(0x6004)=3;materials_aux_record(0x0093E878);assert(!mat_aux_draw.valid);M32(0x6004)=1;
    M32(0x8004)=0;materials_aux_record(0x0093E878);assert(!mat_aux_draw.valid);M32(0x8004)=3;
    unsigned generation=aux_queue[0].generation;
    M32(c.esp+4)=chunk;f_007307E0(&c);materials_aux_record(0x0093E878);assert(!mat_aux_draw.valid);
    M32(instance+0x18)=0x200;load(&c,chunk,aux_keys[keyidx].section_id,instance);
    aux_queue[0].valid=1;aux_queue[0].generation=generation;
    materials_aux_record(0x0093E878);assert(!mat_aux_draw.valid);
    M32(0x00982CDC)=0;append_count=1;lit_count=1;M32(c.esp)=0x00724203;f_006DA9B0(&c);
    assert(aux_queue[0].generation!=generation && aux_queue[1].queue_generation!=aux_queue_generation);
    M32(0x00982CDC)=4096;append_count=0;f_006E0AB0(&c);assert(M32(0x00982CDC)==4096); // wrapper must not write beyond cap
    auxlights_set_enabled(0);materials_aux_record(0x0093E878);assert(!mat_aux_draw.valid);
    for(unsigned i=0;i<AUX_DATA_ROWS;i++) {
        const AuxLightKey *k=&aux_keys[i]; const AuxLightContext *s=&aux_contexts[k->context_index];
        if (strstr(s->name,"TunnelPipe")) {
            float b[6];memcpy(b,k->bbox,24);
            printf("tunnel candidate=%s section=%u bbox_center=(%.3f,%.3f,%.3f) lights=%u\n",s->name,k->section_id,(b[0]+b[3])*.5,(b[1]+b[4])*.5,(b[2]+b[5])*.5,s->count);break;
        }
    }
    puts("host PASS: rigid/scaled inverse, singular/bad space rejection, source-world versus local, checked instance bounds, queue lit/unlit/sorted, view/effect, reload generation, PC zero");
}
'''.replace('HOOKS', hooks)
    source = tmp / 'host.c'
    source.write_text(harness)
    run(['clang', '-O0', '-I', str(ROOT/'runtime'), str(source), '-o', str(tmp/'host')])
    print(run([str(tmp/'host')]), end='')
    old = '!(M32(view+4) == 2 && split_view_rect('
    assert harness.count(old) == 1
    source.write_text(harness.replace(old, '!(M32(view+4) == 99 && split_view_rect('))
    run(['clang', '-O0', '-I', str(ROOT/'runtime'), str(source), '-o', str(tmp/'host-broken')])
    broken = subprocess.run([str(tmp/'host-broken')], capture_output=True, text=True)
    assert broken.returncode == 1, broken.stdout + broken.stderr
    print('host P2 descriptor PASS: 2 checks / 0 failures; ID1-only mutant rejected (exit 1)')


def check_metal(tmp):
    # The production source generator and constant packer, with the same
    # sm_translate -> mat_aux_shader -> newLibraryWithSource path as render.m.
    # from mat_aux_constants to the closing brace of mat_aux_shader (materials.m grows after it with unrelated features)
    start = MATERIALS.index('int mat_aux_constants(')
    at = MATERIALS.index('char *mat_aux_shader(')
    depth, end = 0, MATERIALS.index('{', at)
    while True:
        depth += (MATERIALS[end] == '{') - (MATERIALS[end] == '}')
        end += 1
        if depth == 0: break
    shader = MATERIALS[start:end]
    reg = MATERIALS[MATERIALS.index('static int grass_reg('):MATERIALS.index('int mat_grass_constants(')]
    source = r'''
#import <Metal/Metal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
#include "materials.h"
#include "gen/x360_shader_lits.inc"
#include "gen/x360_shader_body.inc"
MatAuxDraw mat_aux_draw;
REG
SHADER
int main(int argc,char **argv) { @autoreleasepool {
    id<MTLDevice> device=MTLCreateSystemDefaultDevice();
    if(!device) puts("Metal device unavailable; using offline Metal compiler, GPU PSO link pending");
    int compiled=0;
    for(int f=1;f<argc;f++) {
        FILE *input=fopen(argv[f],"rb"); assert(input); fseek(input,0,SEEK_END);long size=ftell(input);rewind(input);
        uint32_t *tokens=malloc(size);assert(fread(tokens,1,size,input)==size);fclose(input);
        SmResult pc={0};SmOptions opt={0};assert(!sm_translate(tokens,(uint32_t)size/4,&opt,&pc));free(tokens);
        char *msl=mat_aux_shader(&pc,pc.shader_type,0,0);
        if(!msl) { sm_result_free(&pc);continue; } // legacy road/world shaders without tangent explicitly fall back
        if(device) {
            NSError *error=nil;MTLCompileOptions *co=[MTLCompileOptions new];co.mathMode=MTLMathModeRelaxed;
            id<MTLLibrary> library=[device newLibraryWithSource:[NSString stringWithUTF8String:msl] options:co error:&error];
            if(!library || ![library newFunctionWithName:@"sm_main"]) { fprintf(stderr,"%s: %s\n",argv[f],error.localizedDescription.UTF8String);return 1; }
        } else {
            char path[128];snprintf(path,sizeof path,"aux_%d.metal",compiled);
            FILE *out=fopen(path,"w");assert(out);fputs(msl,out);fclose(out);
            printf("SOURCE %s\n",path);
        }
        free(msl);++compiled;
        if(pc.shader_type==SM_VERTEX) {
            memset(&mat_aux_draw,0,sizeof mat_aux_draw);mat_aux_draw.valid=1;mat_aux_draw.context.count=2;
            mat_aux_draw.context.lights[0]=(AuxLight){{2,-1,1},15,{.2,.6,.9},7};
            mat_aux_draw.context.lights[1]=(AuxLight){{-1,2,0},10,{.8,.3,.4},19};
            float vsc[256][4]={{0}},k[52][4];
            assert(mat_aux_constants(&pc,vsc,k));
            assert(k[7][2]==.2f*7*3 && k[11][2]==.8f*7*3 && k[10][2]==7 && k[14][2]==19);
            assert(k[39][0]==1 && k[40][0]==1 && k[41][0]==0 && k[6][0]==-1);
        }
        sm_result_free(&pc);
    }
    assert(compiled>2);printf("%s: %d production VS/PS variants; constant intensity/cap/UV contract PASS\n",device ? "Metal compile PASS" : "MSL generation PASS",compiled);
} }
'''.replace('REG',reg).replace('SHADER',shader)
    path=tmp/'metal.m';path.write_text(source)
    run(['clang','-fobjc-arc','-I',str(ROOT/'runtime'),str(path),str(ROOT/'host/shaders/build/libsmtranslate.a'),'-lc++','-framework','Metal','-framework','Foundation','-o',str(tmp/'metal')])
    files=[]
    for effect in ('worldnormalmap','worldreflect'):
        files.extend(str(p) for p in sorted((ROOT/'host/shaders/testdata'/effect).glob('*.bin')))
    output=run([str(tmp/'metal'),*files],cwd=tmp)
    sources=re.findall(r'^SOURCE (.+)$',output,re.M)
    unique={}
    for source in sources:
        path=tmp/source
        unique.setdefault(path.read_text(),path)
    cache = os.environ.get('NFS_METAL_CACHE') or subprocess.check_output(
        ['sh', '-c', 'NFS_ENV_ROOT=$1; . "$1/runtime/env.sh" || exit; printf "%s" "${NFS_METAL_CACHE:-}"',
         'metal-cache', str(ROOT)], text=True)
    cache_args = [f'-fmodules-cache-path={cache}'] if cache else []
    for path in unique.values():
        result=subprocess.run([*(os.environ.get('NFS_METAL') and [os.environ['NFS_METAL']] or ['xcrun','-sdk','macosx','metal']),*cache_args,'-std=metal3.1','-c',str(path),'-o',str(path.with_suffix('.air'))],capture_output=True,text=True)
        if result.returncode and 'missing Metal Toolchain' in result.stderr:
            print('\n'.join(line for line in output.splitlines() if not line.startswith('SOURCE ')))
            raise Blocked('shader compile proof: no Metal device; Xcode reports missing Metal Toolchain. Production source generation/PC reflection/constants passed; GPU compile and PSO link pending.')
        assert result.returncode==0,result.stderr[-3500:]
    print('\n'.join(line for line in output.splitlines() if not line.startswith('SOURCE ')))
    if sources:print(f'offline Metal compiler PASS: {len(unique)} unique production sources, {len(sources)} PC-reflected variants')

class V:
    def __init__(self,*values):
        self.v=tuple(float(x) for value in values for x in (value.v if isinstance(value,V) else (value,)))
    def __getattr__(self,name):
        if all(c in 'xyzw' for c in name):
            items=tuple(self.v['xyzw'.index(c)] for c in name)
            return items[0] if len(items)==1 else V(*items)
        raise AttributeError(name)
    def __setattr__(self,name,value):
        if name=='v':object.__setattr__(self,name,value);return
        values=value.v if isinstance(value,V) else (value,)*len(name)
        result=list(self.v)
        for c,x in zip(name,values): result['xyzw'.index(c)]=float(x)
        object.__setattr__(self,'v',tuple(result))
    def op(self,other,fn):
        values=other.v if isinstance(other,V) else (other,)*len(self.v)
        return V(*(fn(a,b) for a,b in zip(self.v,values)))
    def __add__(self,o):return self.op(o,lambda a,b:a+b)
    __radd__=__add__
    def __sub__(self,o):return self.op(o,lambda a,b:a-b)
    def __rsub__(self,o):return (-self)+o
    def __mul__(self,o):return self.op(o,lambda a,b:a*b)
    __rmul__=__mul__
    def __neg__(self):return self*-1
    def __gt__(self,o):return self.op(o,lambda a,b:a>b)


def execute_register(code,env):
    execute=True
    for raw in code.splitlines():
        line=raw.split('//',1)[0].strip()
        if not line:continue
        if line=='if (p0)':execute=bool(env['p0']);continue
        if line=='{':continue
        if line=='}':execute=True;continue
        if not execute:continue
        line=re.sub(r'^(float[234]?|bool) ','',line).rstrip(';')
        if ' -= ' in line:
            lhs,rhs=line.split(' -= ',1);line=f'{lhs} = {lhs} - {rhs}'
        lhs,rhs=line.split(' = ',1)
        rhs=re.sub(r'(?<=\d)f\b','',rhs)
        if '?' in rhs:
            cond,rest=rhs.split('?',1);yes,no=rest.split(':',1);rhs=f'({yes} if {cond} else {no})'
        result=eval(rhs,{'__builtins__':{}},env)
        if '.' in lhs:
            obj,swizzle=lhs.split('.');setattr(env[obj],swizzle,result)
        else:env[lhs]=result
    return env


def check_scalar(mutation):
    from types import SimpleNamespace
    def unary(fn,x):return V(*(fn(a) for a in x.v)) if isinstance(x,V) else fn(x)
    def vmax(a,b):return a.op(b,max) if isinstance(a,V) else max(a,b)
    def clamp(x,a,b):return unary(lambda v:max(a,min(b,v)),x)
    def vec(n):
        def make(*a):
            v=V(*a)
            if len(v.v)==1:return V(*(v.v*n))
            return V(*v.v[:n])
        return make
    def dot(a,b):return sum(x*y for x,y in zip(a.v,b.v))
    def normalized(v):return v*(1/math.sqrt(dot(v,v)))
    # These are the checked inverse-instance fixture positions (90 degree turn
    # plus translation 100,200,30), with unequal intensities 7 and 19.
    lights=[(V(2,-1,1),V(.2,.6,.9),7),(V(-1,2,0),V(.8,.3,.4),19)]
    position=V(.25,-.5,-1);eye=V(1,4,5);normal=normalized(V(.2,-.1,.97))
    diffuse=V(.7,.5,.3);specular=V(.4,.6,.8)
    class Texture:
        def __init__(self,v):self.value=V(v,1)
        def sample(self,*a):return self.value
    k=[V(0,0,0,0) for _ in range(52)]
    for i,(p,c,intensity) in enumerate(lights):
        for j in range(3):k[7+4*i+j]=V(p.v[j],c.v[j],c.v[j]*7*3,0)
        k[7+4*i+3]=V(15,intensity,intensity,0);k[39+i]=V(1,0,0,0)
    P254,P255=lit4('X360LC_09C0DE10_C254'),lit4('X360LC_09C0DE10_C255')   # c254.y spec power; c255.xy lane gains, .w spec scale
    env=dict(a=SimpleNamespace(k=k),c254=V(*P254),c255=V(*P255),
             iTexCoord0=V(.1,.2,1,-1),iTexCoord1=V(eye,0),iTexCoord2=V(position,1),oC0=V(0,0,0,0),
             td=Texture(diffuse),tn=Texture((normal+1)*.5),ts=Texture(specular),wrap=0,bias=lambda x:x,
             dot=dot,clamp=clamp,fabs=lambda x:unary(abs,x),max=vmax,saturate=lambda x:clamp(x,0,1),
             rsqrt=lambda x:float('inf') if x==0 else 1/math.sqrt(x),
             log2=lambda x:-float('inf') if x==0 else math.log2(x),exp2=lambda x:2**x,
             float2=vec(2),float3=vec(3),float4=vec(4),false=False)
    for i in range(6):env[f'iColor{i}']=V(lights[i][0]-position,0) if i<2 else V(0,0,0,0)
    code=cstring('aux_ps_body')
    code=code[code.index('\tfloat4 r0 ='):code.index('if (a.k[48]')]
    if mutation:env['c255']=V(P255[0],P255[0],P255[2],P255[3])
    execute_register(code,env)
    actual=env['oC0'].xyz
    expected=V(0,0,0);unweighted=V(0,0,0);wrong_own_gain=V(0,0,0)
    n=normal;e=normalized(eye)
    for p,c,intensity in lights:
        delta=p-position;l=normalized(delta);ndl=dot(n,l)
        reflected=n*(2*ndl)-l
        brdf=diffuse*max(0,min(1,ndl))+specular*(P255[3]*max(0,min(1,dot(reflected,e)))**P254[1])
        denominator=2*dot(delta,delta)+intensity
        expected+=c*V(1,P255[0],P255[1])*(7*3/denominator)*brdf
        unweighted+=c*(7*3/denominator)*brdf
        wrong_own_gain+=c*V(1,P255[0],P255[1])*(intensity*3/denominator)*brdf
    error=max(abs(a-b) for a,b in zip(actual.v,expected.v))
    assert error<1e-7,f'lane scalar mismatch {actual.v} expected {expected.v} error={error}'
    assert max(abs(a-b) for a,b in zip(actual.v,unweighted.v))>.01
    assert max(abs(a-b) for a,b in zip(actual.v,wrong_own_gain.v))>.01
    assert env['oC0'].w==1
    print(f'scalar PASS: exact register PS vs independent RGB lane/BRDF, two unequal lights, rotated/translated fixture; RGB={actual.v}, max_error={error:.3g}')
    # Execute the actual replacement VS register body: all six light vectors,
    # handed TBN, PC normal axes, UV scroll, local eye and absolute clip bias.
    k[0]=V(2,0,0,3);k[1]=V(0,3,0,-2);k[2]=V(0,0,.5,4);k[3]=V(0,0,.25,1)
    k[5]=V(2,4,5,1);k[6]=V(-1,0,0,0);k[47]=V(.03,.05,0,0)
    for i in range(6):
        for j,value in enumerate((i+1,i-2,i+.5)):k[7+4*i+j]=V(value,0,0,0)
        k[39+i]=V(1,0,0,0)
    venv=env.copy();venv.update(c255=V(*lit4('X360LC_09C0C3B4_C255')),iPosition0=V(position,1),iNormal0=V(0,0,1,0),iTangent0=V(1,0,0,1),iTexCoord0=V(.1,.2,0,0),oPos=V(0,0,0,0))
    for name in ['oTexCoord0','oTexCoord1','oTexCoord2',*[f'oColor{i}' for i in range(6)]]:venv[name]=V(0,0,0,0)
    vs_code=cstring('aux_vs_body');vs_code=vs_code[vs_code.index('\tfloat4 r0 ='):vs_code.index('AuxOut o;')]
    execute_register(vs_code,venv)
    for i in range(6):
        expected_vector=V(i+1,i-2,i+.5)-position
        assert max(abs(a-b) for a,b in zip(venv[f'oColor{i}'].xyz.v,expected_vector.v))<1e-7
    assert abs(venv['oTexCoord0'].x-.13)<1e-7 and abs(venv['oTexCoord0'].y-.25)<1e-7
    nudge=float(LITS['X360S_AUX_DEPTH_NUDGE'])   # the retail VS's depth nudge (its c255.x), generated from the player's ZZDATA0
    assert abs(venv['oPos'].z-(dot(V(position,1),k[2])-nudge))<1e-7
    # Turn light5 off; its dedicated varying alone must go to zero.
    k[44]=V(0,0,0,0);execute_register(vs_code,venv)
    assert venv['oColor5'].xyz.v==(0,0,0) and any(venv['oColor4'].xyz.v)
    print('VS scalar PASS: all six explicit varyings, light5 off, PC TBN orientation, UV scroll, current nonidentity WVP and clip-depth bias')



def check_source_space():
    import collections
    import importlib.util
    import struct
    spec=importlib.util.spec_from_file_location('import_aux',ROOT/'tools_snapshot/x360_auxlights.py')
    aux=importlib.util.module_from_spec(spec);spec.loader.exec_module(aux)
    records=[];original=aux.scenery
    def scenery(read,begin,end):
        result=original(read,begin,end);raw=[]
        for kind,p,stop in aux.chunks(read,begin,end):
            if kind==0x34103:
                for at in range((p+15)&~15,stop,64):raw.append(read(at,64))
        records.extend((s,b,i,d) for (s,b,i),d in zip(result,raw))
        return result
    aux.scenery=scenery
    contexts=aux.extract_contexts(aux.DEFAULT_X360/'STREAML2RA_PERM.BUN')
    source=aux.x360_instances(aux.DEFAULT_X360/'STREAML2RA.BUN',contexts)
    raw_source=records[:];records.clear()
    target=aux.pc_instances(aux.DEFAULT_PC/'STREAML2RA.BUN');raw_target=records[:]
    rows,stats=aux.match_instances(contexts,source,target)
    src=collections.defaultdict(list);dst=collections.defaultdict(list)
    for s,b,i,d in raw_source:src[s,b].append(d)
    for s,b,i,d in raw_target:dst[s,b].append(d)
    parity=0;different=0;baked=0;transformed=0;near=0;rigid=0
    for s,b,i in rows:
        for sd in src[s,b]:
            for pd in dst[s,b]:
                sb=bool(struct.unpack_from('<I',sd,24)[0]&0x200);pb=bool(struct.unpack_from('<I',pd,24)[0]&0x200)
                same=sb==pb and (sb or sd[32:62]==pd[32:62])
                parity+=same;different+=not same;baked+=sb;transformed+=not sb
                basis=[[1,0,0],[0,1,0],[0,0,1]] if sb else [list(x/8192 for x in struct.unpack_from('<3h',sd,44+r*6)) for r in range(3)]
                translation=(0,0,0) if sb else struct.unpack_from('<3f',sd,32)
                valid_rigid=all(abs(sum(basis[r][k]*basis[c][k] for k in range(3))-(r==c))<=.002 for r in range(3) for c in range(3))
                rigid+=valid_rigid
                bbox=struct.unpack('<6f',b);close=True
                for light in contexts[i][1][:8]:
                    values=struct.unpack('<8f',light)
                    position=[translation[c]+sum(values[r]*basis[r][c] for r in range(3)) for c in range(3)]
                    distance2=sum(max(bbox[c]-position[c],0,position[c]-bbox[c+3])**2 for c in range(3))
                    close &= distance2 <= (4*values[3])**2
                near+=close
    assert len(rows)==999 and parity==1003 and not different,(parity,different)
    print(f'source/PC transform parity PASS: {parity} references, {len(rows)} keys; baked={baked} transformed={transformed}, rigid={rigid}, world light/bbox validation={near}')
    # Choose a matched tunnel context near an imported source Road context; the
    # road is a locator, even when its recut geometry is one of the unmatched232.
    roads=[(s,struct.unpack('<6f',b),contexts[i][0]) for s,b,i in source if i>=0 and 'Road' in contexts[i][0]]
    candidates=[]
    for s,b,i in rows:
        name=contexts[i][0]
        if 'Tunnel' not in name:continue
        bounds=struct.unpack('<6f',b);center=[(bounds[c]+bounds[c+3])*.5 for c in range(3)]
        for road_section,road,road_name in roads:
            rc=[(road[c]+road[c+3])*.5 for c in range(3)]
            distance=math.sqrt(sum((center[c]-rc[c])**2 for c in range(3)))
            candidates.append((distance,name,s,center,road_name,road_section,rc))
    if candidates:
        d,name,s,center,rn,rs,rc=min(candidates)
        print(f'capture locator: context={name} section={s} centre={tuple(round(v,3) for v in center)}; nearby source road={rn} section={rs} position={tuple(round(v,3) for v in rc)} distance={d:.2f}m (camera/occlusion unverified)')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--mutate',choices=['lane']);parser.add_argument('--cpu-only',action='store_true');args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='r20-draw-') as temp:
        tmp=Path(temp)
        check_host(tmp)
        check_scalar(args.mutate)
        check_source_space()
        if not args.cpu_only: check_metal(tmp)
    print('R20 offline CPU draw PASS; Metal compile pending' if args.cpu_only else 'R20 offline draw PASS')


if __name__=='__main__':
    try:
        main()
    except Blocked as error:
        print(f'BLOCKED: {error}')
        raise SystemExit(2)
