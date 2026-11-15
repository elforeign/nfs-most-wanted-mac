"""Authored ripple production proof, bare (no console or PC assets).

Synthetic reflective road: authored frames change 65,033/65,536 pixels (>0);
suppressed frame generation changes 0 and fails the identical output check.
Rain zero and reflection alpha are byte-equal; 16 slices, 8 mip levels.
Also exercises complete/partial console providers with synthetic sentinels,
16 valid deterministic frames, periodic derivatives, rain-zero identity, menu
availability, startup ordering and unchanged console helper bytes.
"""
import hashlib
import os
from pathlib import Path
import re
import runpy
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RT = ROOT / 'runtime'
GEN = ROOT / 'tools_snapshot/rain_ripples_gen.py'

HARNESS = r'''
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <stdarg.h>
static int diag_on, render_static_made, loads, configured, scenario;
static id<MTLDevice> mtl;
static unsigned char memory[0xA00000];
#define M32(a) (*(uint32_t *)(memory+(a)))
#define F32(a) (*(float *)(memory+(a)))
#define CUR_VIEW 0x00982A20u
#define VIEW_RAIN 0x00919648u
#define SIM_TIME 0x009885D8u
#define PROBE_RIPPLES 1
static unsigned probe_sig;
static void probe_applied(unsigned mask,int present){(void)mask;(void)present;}
typedef struct {float k[52][4];} MatConsts;
static uint64_t host_ticks(void){return 0;}
static void diag_event(const char *format,...){(void)format;}
void x360_texture_configure(void){configured=1;}
uint8_t *x360_texture_rgba(const char *file,uint32_t hash,uint32_t *w,uint32_t *h){
 (void)file;(void)hash; ++loads;
 if(!configured)exit(10);
 if(scenario==0 || (scenario==2 && loads==8))return NULL;
 *w=*h=128;uint8_t *p=malloc(65536);memset(p,37,65536);return p;
}
PRODUCTION
static id<MTLRenderPipelineState> pipeline(const char *vs,const char *ps){
 NSError *error=nil;
 id<MTLLibrary> v=[mtl newLibraryWithSource:@(vs) options:nil error:&error];
 if(!v){fprintf(stderr,"VS %s\n",error.description.UTF8String);exit(11);}
 id<MTLLibrary> p=[mtl newLibraryWithSource:@(ps) options:nil error:&error];
 if(!p){fprintf(stderr,"PS %s\n",error.description.UTF8String);exit(12);}
 MTLRenderPipelineDescriptor *d=[MTLRenderPipelineDescriptor new];
 d.vertexFunction=[v newFunctionWithName:@"sm_main"];d.fragmentFunction=[p newFunctionWithName:@"sm_main"];
 d.colorAttachments[0].pixelFormat=MTLPixelFormatRGBA32Float;
 id<MTLRenderPipelineState> result=[mtl newRenderPipelineStateWithDescriptor:d error:&error];
 if(!result){fprintf(stderr,"pipeline %s\n",error.description.UTF8String);exit(13);}return result;
}
static void draw(id<MTLRenderPipelineState> p,id<MTLTexture> target,id<MTLTexture> refl,float rain,int frame,float *pixels){
 id<MTLCommandQueue> queue=[mtl newCommandQueue];
 id<MTLCommandBuffer> cb=[queue commandBuffer];
 MTLRenderPassDescriptor *pass=[MTLRenderPassDescriptor renderPassDescriptor];
 pass.colorAttachments[0].texture=target;pass.colorAttachments[0].loadAction=MTLLoadActionClear;
 pass.colorAttachments[0].storeAction=MTLStoreActionStore;
 id<MTLRenderCommandEncoder> e=[cb renderCommandEncoderWithDescriptor:pass];
 [e setRenderPipelineState:p];
 float point[4]={0},c[8]={0},mk[16]={rain,(float)frame,0,0};
 c[7]=1;
 [e setVertexBytes:point length:sizeof point atIndex:0];
 [e setFragmentBytes:c length:sizeof c atIndex:0];[e setFragmentBytes:mk length:sizeof mk atIndex:4];
 [e setFragmentTexture:refl atIndex:3];[e setFragmentTexture:ripple_texture() atIndex:15];
 MTLSamplerDescriptor *sd=[MTLSamplerDescriptor new];sd.minFilter=MTLSamplerMinMagFilterLinear;sd.magFilter=MTLSamplerMinMagFilterLinear;
 [e setFragmentSamplerState:[mtl newSamplerStateWithDescriptor:sd] atIndex:3];
 [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[e endEncoding];
 [cb commit];[cb waitUntilCompleted];if(cb.status==MTLCommandBufferStatusError)exit(14);
 [target getBytes:pixels bytesPerRow:64*16 fromRegion:MTLRegionMake2D(0,0,64,64) mipmapLevel:0];
}
int main(int argc,char **argv){@autoreleasepool{
 if(argc!=4)return 2;scenario=atoi(argv[1]);setenv("NFS_RAIN_RIPPLES",argv[2],1);
 materials_ripples_prepare();materials_textures();
 uint32_t w,h;const uint8_t *f=mat_ripple_frame(15,&w,&h);
 int expected=scenario==1 && X360_SHADER_LITS && X360_ENGINE_CONSTS && strcmp(argv[2],"port") ? 1:2;
 if(ripple_provider!=expected || !f || w!=128 || h!=128)return 15;
 if(expected==1 && (f[0]!=37 || loads!=16))return 16;
 if(expected==2 && loads!=0 && loads!=16)return 17;
 int enabled=strcmp(argv[2],"pc") && (expected==1 || !strcmp(argv[2],"port"));
 if(ripples_on()!=enabled)return 18;
 printf("provider=%s frames=16 enabled=%d loads=%d\n",expected==1?"console":"port",ripples_on(),loads);
 M32(CUR_VIEW)=0x1000;M32(0x1004)=0;M32(VIEW_RAIN)=0x2000;F32(0x228C)=.75f;
 F32(SIM_TIME)=.31f;MatConsts constants={0};ripple_constants(NULL,&constants);
 double fps=expected==1?X360E_RIPPLE_FPS:PORTE_RIPPLE_FPS;
 int frame=(int)(fps*fmod((double)F32(SIM_TIME),16/fps));
 if(constants.k[0][0]!=(enabled?.75f:0) || constants.k[0][1]!=frame)return 23;
 materials_set_rain_ripples(0);ripple_constants(NULL,&constants);if(constants.k[0][0]!=0)return 24;
 materials_set_rain_ripples(1);
 if(strcmp(argv[3],"gpu"))return 0;
 mtl=MTLCreateSystemDefaultDevice();if(!mtl){puts("SKIP: no Metal device");return 3;}
 const char *vs=VERTEX,*ps=PIXEL;char *pv=ripple_vs_patch(vs),*pp=ripple_ps_patch(ps);
 if(!pv || !pp)return 19;
 id<MTLRenderPipelineState> original=pipeline(vs,ps),patched=pipeline(pv,pp);
 MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA32Float width:64 height:64 mipmapped:NO];
 d.storageMode=MTLStorageModeShared;d.usage=MTLTextureUsageRenderTarget;
 id<MTLTexture> target=[mtl newTextureWithDescriptor:d];
 d.usage=MTLTextureUsageShaderRead;id<MTLTexture> refl=[mtl newTextureWithDescriptor:d];
 float texels[64*64*4];for(int y=0;y<64;y++)for(int x=0;x<64;x++){
 int i=(y*64+x)*4;texels[i]=.2f+.4f*x/63;texels[i+1]=.2f+.4f*y/63;texels[i+2]=.3f;texels[i+3]=.7f;}
 [refl replaceRegion:MTLRegionMake2D(0,0,64,64) mipmapLevel:0 withBytes:texels bytesPerRow:64*16];
 if(ripple_texture().arrayLength!=16 || ripple_texture().mipmapLevelCount!=8)return 20;
 float baseline[64*64*4],zero[64*64*4],wet[64*64*4];
 draw(original,target,refl,0,0,baseline);draw(patched,target,refl,0,0,zero);
 if(memcmp(baseline,zero,sizeof baseline))return 21;
 unsigned changed=0;
 for(int frame=0;frame<16;frame++){
 draw(patched,target,refl,1,frame,wet);
 for(int i=0;i<64*64;i++){
 if(wet[i*4+3]!=baseline[i*4+3])return 22;
 if(fabsf(wet[i*4]-baseline[i*4])>1e-6f || fabsf(wet[i*4+1]-baseline[i*4+1])>1e-6f || fabsf(wet[i*4+2]-baseline[i*4+2])>1e-6f)++changed;
 }}
 printf("changed_pixels=%u rain_zero_equal=1 alpha_equal=1 slices=16 mips=8\n",changed);
 return changed>0?0:1;
}}
'''


def run(args, expected=0):
    p = subprocess.run(list(map(str, args)), capture_output=True, text=True)
    if p.returncode == 3:
        print(p.stdout.strip())
        raise SystemExit(3)
    assert p.returncode == expected, f'{args[0]}: {p.returncode}\n{p.stdout[-1500:]}\n{p.stderr[-4000:]}'
    return p.stdout.strip()


def main():
    gen = runpy.run_path(str(GEN))
    a, b = gen['frames'](), gen['frames']()
    assert a == b and len(a) == 16*128*128*4
    assert all(a[i] == 255 for i in range(3, len(a), 4))
    assert max(a[::4]) <= 8 and max(a[1::4]) > 0 and max(a[2::4]) > 0
    assert len({hashlib.sha256(a[i*65536:(i+1)*65536]).digest() for i in range(16)}) == 16
    drops = gen['events']()
    grad, period, tile = gen['gradient'], gen['PERIOD'], gen['TILE']
    error = 0
    for x, y, t in ((.01,.03,.13),(.2,.4,.5),(.499,.001,0)):
        base = grad(x, y, t, drops)
        for point in ((x+tile,y,t),(x,y+tile,t),(x,y,t+period)):
            error = max(error, max(abs(u-v) for u,v in zip(base,grad(*point,drops))))
    assert error < 1e-12, error
    assert abs(float(gen['constants']().split('PORTE_RIPPLE_FPS ')[1].split('f')[0]) - 16/period) < 1e-4
    print(f'16 deterministic frames; events={len(drops)} periodic_error={error:.3g}')
    material = (RT/'materials.m').read_text()
    render = (RT/'render.m').read_text()
    assert render.index('materials_ripples_prepare();') < render.index('    shader_cache_warm();')
    settings = (RT/'settings.c').read_text().split('static int x360_unavailable',1)[0]
    assert 'graphics.rain_ripples' not in settings
    assert '"x360", "pc", "port"' in (RT/'settings_port.c').read_text()
    fixtures = runpy.run_path(str(RT/'rain_cover_gpu_test.py'))
    function = fixtures['function']
    # Original console helper is frozen independently of generated table contents.
    helper = material.split('static const char ripple_ps_helpers[] =',1)[1].split('static const char port_ripple_ps_helpers[] =',1)[0]
    assert hashlib.sha256(helper.encode()).hexdigest() == CONSOLE_HELPER_SHA
    provider = material[material.index('#include "gen/port_rain_ripples.inc"'):material.index('static void ripple_constants')]
    helpers = material[material.index('static const char ripple_ps_helpers[] ='):material.index('// Edits *s')]
    upload = function(render,'ripple_texture')
    production = provider.replace('#include "gen/port_rain_ripples.inc"','#include "frames.inc"') + helpers
    production += '\n'.join(function(material,n) for n in ('edit','count','ripple_ps_patch','ripple_vs_patch','ripple_constants')) + upload
    vertex = fixtures['VERTEX'].replace('si.v[0]=point;', 'si.v[0]=float4(corners[id]*.25f,0,1);')
    # A reflective road strip whose projective coordinates vary over the image.
    pixel = fixtures['PIXEL'].replace('float4(.5,.5,0,1)', 'float4(pi.pos.xy/64,0,1)')
    import json
    harness = HARNESS.replace('PRODUCTION',production).replace('VERTEX',json.dumps(vertex)).replace('PIXEL',json.dumps(pixel))
    with tempfile.TemporaryDirectory(prefix='rain-ripples-') as directory:
        tmp = Path(directory)
        nopc = tmp/'nopc'
        command = [sys.executable,'-I',ROOT/'tools/gen_tables.py','--no-pc','--no-x360','--out',nopc]
        run(command)
        assert (nopc/'port_rain_ripples.inc').read_text() == gen['header'](a)
        assert 'up to date' in run(command+['--if-stale'])
        table_tools = runpy.run_path(str(ROOT/'tools/gen_tables.py'))
        assert 'tools_snapshot/rain_ripples_gen.py' in table_tools['GENERATORS']
        before = table_tools['outputs_text'](nopc)
        (nopc/'port_rain_ripples.inc').write_text('suppressed')
        assert table_tools['outputs_text'](nopc) != before
        (tmp/'fixture.m').write_text('#include "sentinels.inc"\n'+harness)
        # Synthetic console constants deliberately differ from all authored ones.
        (tmp/'sentinels.inc').write_text('#define X360_SHADER_LITS 1\n#define X360_ENGINE_CONSTS 1\n#define X360E_RIPPLE_FPS 7.0f\n' + '\n'.join(
            '#define X360S_RIPPLE_'+key+' "'+value+'"' for key,value in {'TILE':'.2','SCALE2':'1.5','GLINT':'.25','GLINT_T':'.25','REFL':'.5','REFL_T':'.5'}.items()))
        def build(data, name):
            (tmp/'frames.inc').write_text(gen['header'](data))
            run(['clang','-O1','-fobjc-arc','-Wall','-Wextra','-Wno-unused-variable','-Wno-unused-function',tmp/'fixture.m',
                 '-framework','Foundation','-framework','Metal','-o',tmp/name])
            return tmp/name
        binary = build(a,'fixed')
        for scenario, mode in ((0,'port'),(0,'x360'),(0,'pc'),(1,'x360'),(1,'port'),(1,'pc'),(2,'x360'),(2,'port')):
            print(run([binary,scenario,mode,'cpu']))
        sentinel = (tmp/'sentinels.inc').read_text()
        for shader, engine in ((1,0),(0,1),(0,0)):
            (tmp/'sentinels.inc').write_text(sentinel.replace('X360_SHADER_LITS 1','X360_SHADER_LITS '+str(shader)).replace('X360_ENGINE_CONSTS 1','X360_ENGINE_CONSTS '+str(engine)))
            binary = build(a,'bare')
            print(run([binary,1,'x360','cpu']))
        fixed = run([binary,0,'port','gpu'])
        print('fixed: '+fixed)
        # A build in which generation supplied no effect channels; alpha retained.
        black = bytes((0,0,0,255))*(16*128*128)
        broken = run([build(black,'suppressed'),0,'port','gpu'],expected=1)
        assert 'changed_pixels=0 ' in broken
        print('suppressed frame generation: '+broken)
        proof = ROOT/'.metal-cache/ripples-proof'
        proof.mkdir(parents=True, exist_ok=True)
        (proof/'offline-visible.txt').write_text('fixed: '+fixed+'\nsuppressed: '+broken+'\n')


CONSOLE_HELPER_SHA = 'e7c7d942a571070969378b9d5796665bef883b513dd347a222b878c7e1e08dc2'
if __name__ == '__main__':
    main()
