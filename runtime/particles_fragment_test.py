"""Rasterize production Physical smoke into one float RGBA pixel.

Exercise original/resolved/late fragments, production lamp/depth/constant binds,
sun, headlight, shadow, opaque contact and partial contact. No game or artwork.
Production must pass; zero-lamp and clear-depth binding controls must fail the
same pixel oracle. The Metal runner supplies GPU access under the test sandbox.
Proof: 60 RGBA checks, 0 failures; zero lamps 9, clear depth 9 (exit 1).
"""
from pathlib import Path
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from soft_smoke_test import cstring, function, mw_source

ROOT = Path(__file__).resolve().parents[1]
RT = ROOT / 'runtime'

VERTEX = r'''
vertex In fixture_vs(uint v [[vertex_id]], constant float &z [[buffer(0)]]) {
    float2 p[3]={float2(-1,-1),float2(3,-1),float2(-1,3)};
    In o;o.pos=float4(p[v],z,1);o.color0=float4(.5f);
    o.texcoord0=float4(.5f);o.texcoord1=float4(.5f,.5f,.5f,1);return o;
}
'''

NATIVE = r'''
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <math.h>
typedef struct {float k[12][4];} MatConsts;
typedef struct {float k[17][4];} ParticleLamps;
typedef struct {unsigned func;float ref;unsigned coverage,pad;float bump[8][4],lum[8][4];} Aux;
enum {MAT_PS_BUF=4};
static id<MTLTexture> scalar(id<MTLDevice> d,float value){
 MTLTextureDescriptor *td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatR32Float width:1 height:1 mipmapped:NO];
 td.storageMode=MTLStorageModeShared;td.usage=MTLTextureUsageShaderRead;
 id<MTLTexture> t=[d newTextureWithDescriptor:td];[t replaceRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0 withBytes:&value bytesPerRow:4];return t;
}
int main(int argc,char **argv){@autoreleasepool{
 id<MTLDevice> d=MTLCreateSystemDefaultDevice();if(!d){puts("SKIP: no Metal device");return 3;}
 id<MTLCommandQueue> queue=[d newCommandQueue];NSError *error=nil;
 NSString *src=[NSString stringWithContentsOfFile:@(argv[1]) encoding:NSUTF8StringEncoding error:&error];
 id<MTLLibrary> lib=[d newLibraryWithSource:src options:nil error:&error];
 if(!lib){fprintf(stderr,"%s\n",error.localizedDescription.UTF8String);return 2;}
 MTLRenderPipelineDescriptor *pd=[MTLRenderPipelineDescriptor new];
 pd.vertexFunction=[lib newFunctionWithName:@"fixture_vs"];pd.fragmentFunction=[lib newFunctionWithName:@"sm_main"];
 pd.colorAttachments[0].pixelFormat=MTLPixelFormatRGBA32Float;
 id<MTLRenderPipelineState> pipeline=[d newRenderPipelineStateWithDescriptor:pd error:&error];
 if(!pipeline){fprintf(stderr,"%s\n",error.localizedDescription.UTF8String);return 2;}
 MTLSamplerDescriptor *sd=[MTLSamplerDescriptor new];sd.minFilter=sd.magFilter=MTLSamplerMinMagFilterNearest;
 id<MTLSamplerState> sampler=[d newSamplerStateWithDescriptor:sd];
 MTLTextureDescriptor *td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA32Float width:1 height:1 mipmapped:NO];
 td.storageMode=MTLStorageModeShared;td.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;
 id<MTLTexture> target=[d newTextureWithDescriptor:td],tex0=[d newTextureWithDescriptor:td];
 float texel[4]={.5,.5,.5,.5};[tex0 replaceRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0 withBytes:texel bytesPerRow:16];
 int variant=atoi(argv[2]),control=atoi(argv[3]),checks=0,failures=0;
 for(int test=0;test<5;++test){
  MatConsts mk={0};ParticleLamps lamps={0},zero_lamps={0};Aux aux={0};float dummy[4]={0},z=.9f;
  unsigned mk_length=sizeof mk;int late_draw=variant==2;
  mk.k[0][0]=1;mk.k[1][0]=.5;mk.k[1][1]=100;mk.k[1][2]=.6;
  mk.k[2][0]=4;mk.k[2][1]=1;mk.k[3][0]=2;mk.k[3][1]=-2;mk.k[3][3]=1;
  for(int i=0;i<4;++i)mk.k[4+i][i]=1;
  for(int i=0;i<3;++i){mk.k[8][i]=test==1?0:.2;mk.k[9][i]=test==1?0:.5;}
  mk.k[8][3]=.682;mk.k[9][3]=.93;mk.k[10][2]=1;mk.k[11][0]=-1;mk.k[11][1]=1;
  if(test==1){lamps.k[0][0]=1;lamps.k[1][2]=1;lamps.k[1][3]=1;lamps.k[2][2]=-1;lamps.k[2][3]=60;}
  float pz=.5f*100/(100-z*99.5f),scene=1;
  if(test==2)scene=z-.01f;
  if(test==3)scene=(100-.5f*100/(pz+.3f))/99.5f;
  id<MTLTexture> tex1=scalar(d,test==4?.25:1),clear_depth=scalar(d,1),soft_depth=scalar(d,scene);
  struct {id<MTLTexture> tex;} ad_rt;
  id<MTLCommandBuffer> command=[queue commandBuffer];
  if(variant){
   MTLTextureDescriptor *dd=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatDepth32Float width:1 height:1 mipmapped:NO];
   dd.storageMode=MTLStorageModePrivate;dd.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;
   id<MTLTexture> depth=[d newTextureWithDescriptor:dd];
   MTLRenderPassDescriptor *clear=[MTLRenderPassDescriptor renderPassDescriptor];
   clear.depthAttachment.texture=depth;clear.depthAttachment.loadAction=MTLLoadActionClear;clear.depthAttachment.storeAction=MTLStoreActionStore;
   clear.depthAttachment.clearDepth=control==2?1:scene;
   id<MTLRenderCommandEncoder> ce=[command renderCommandEncoderWithDescriptor:clear];[ce endEncoding];
   soft_depth=depth;ad_rt.tex=depth;
  }else ad_rt.tex=soft_depth;
  if(control==1)lamps=zero_lamps;
  if(control==2 && !variant)soft_depth=clear_depth;
  MTLRenderPassDescriptor *pass=[MTLRenderPassDescriptor renderPassDescriptor];
  pass.colorAttachments[0].texture=target;pass.colorAttachments[0].loadAction=MTLLoadActionClear;pass.colorAttachments[0].storeAction=MTLStoreActionStore;
  pass.colorAttachments[0].clearColor=MTLClearColorMake(0,0,0,0);
  id<MTLRenderCommandEncoder> enc=[command renderCommandEncoderWithDescriptor:pass];
  [enc setRenderPipelineState:pipeline];[enc setVertexBytes:&z length:sizeof z atIndex:0];
  [enc setFragmentBytes:dummy length:sizeof dummy atIndex:0];[enc setFragmentBytes:&aux length:sizeof aux atIndex:3];
  [enc setFragmentTexture:tex0 atIndex:0];[enc setFragmentTexture:tex1 atIndex:1];
  [enc setFragmentSamplerState:sampler atIndex:0];[enc setFragmentSamplerState:sampler atIndex:1];
  BINDINGS
  [enc drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[enc endEncoding];
  [command commit];[command waitUntilCompleted];if(command.status!=MTLCommandBufferStatusCompleted)return 2;
  float pixel[4];[target getBytes:pixel bytesPerRow:16 fromRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0];
  float g=.682f,phase=(1-g*g)/powf(1+g*g-2*g,1.5f);
  float expected=.5f*.93f*(.2f+(test==4?0:.5f*phase));
  if(test==1)expected=.5f*.93f*powf(1-.1f/60,2)/(1+.01f/64)*phase;
  float alpha=test==2?0:test==3?.25f:test==4?.25f:.5f;
  if(late_draw && test==2)expected=0;
  for(int c=0;c<4;++c){float want=c==3?alpha:expected;++checks;
   if(!isfinite(pixel[c]) || fabsf(pixel[c]-want)>fabsf(want)*.0005f+.0001f){++failures;
    fprintf(stderr,"variant %d case %d channel %d: %g expected %g\n",variant,test,c,pixel[c],want);}}
 }
 printf("fragment variant=%d control=%d checks=%d failures=%d\n",variant,control,checks,failures);return failures?1:0;
}}
'''


def run(args, **kwargs):
    return subprocess.run([str(a) for a in args], capture_output=True, text=True, timeout=90, **kwargs)


def main():
    if not (RT / 'gen/pc_shader_lits.inc').is_file():
        print('SKIP: generated particle shader constants absent')
        return 3
    materials = (RT / 'materials.m').read_text()
    physical = (RT / 'particles_physical.c').read_text()
    renderer = mw_source(RT / 'render.m')
    bindings = '\n'.join(line.strip() for line in renderer.splitlines() if line.strip().startswith((
        '[enc setFragmentBytes:&lamps ', '[enc setFragmentTexture:late_draw?', '[enc setFragmentBytes:&mk ')))
    assert len(bindings.splitlines()) == 3, 'production particle bindings changed'
    prelude = ('#include <stdint.h>\n#include <stdlib.h>\n#include <stdio.h>\n#include <string.h>\n'
               '#include "roots.h"\n#include "../host/shaders/sm_coverage.h"\n#include "gen/pc_shader_lits.inc"\n'
               'typedef struct {int unused;} SmResult;\n')
    dump = prelude + cstring(materials, 'particles_msl') + cstring(materials, 'soft_smoke_math')
    dump += function(materials, 'edit') + '\n'
    dump += 'char *mat_replace(const uint32_t *t,uint32_t n,const SmResult *r,int *id){*id=1;return strdup(particles_msl);}\n'
    dump += 'int mat_is_particle(int id){return id==1;}\n' + cstring(physical, 'particles_phase_msl')
    for text, name in ((physical, 'particle_edit'), (physical, 'particles_physical_shader'),
                       (materials, 'mat_soft_smoke_shader'), (materials, 'mat_soft_smoke_resolved_shader'),
                       (materials, 'mat_soft_smoke_late_shader')):
        dump += function(text, name) + '\n'
    dump += ('int main(int argc,char **argv){int id=0,v=atoi(argv[1]);'
             'char *s=v==2?mat_soft_smoke_late_shader(0,0,0,&id):v?mat_soft_smoke_resolved_shader(0,0,0,&id):mat_soft_smoke_shader(0,0,0,&id);'
             'if(!s||id!=1)return 1;puts(s);free(s);return 0;}\n')
    with tempfile.TemporaryDirectory(prefix='particle-fragment-') as scratch:
        tmp = Path(scratch)
        (tmp / 'dump.c').write_text(dump)
        result = run(['clang', '-I', RT, tmp / 'dump.c', '-o', tmp / 'dump'])
        assert result.returncode == 0, result.stderr[-2000:]
        (tmp / 'native.m').write_text(NATIVE.replace('BINDINGS', bindings))
        result = run(['clang', '-fobjc-arc', tmp / 'native.m', '-framework', 'Foundation', '-framework', 'Metal', '-o', tmp / 'native'])
        assert result.returncode == 0, result.stderr[-2000:]
        for variant in range(3):
            result = run([tmp / 'dump', variant])
            assert result.returncode == 0, result.stderr[-2000:]
            shader = tmp / 'fragment.metal'
            shader.write_text('#define MAT_SHADOW_HW 0\n' + result.stdout + VERTEX)
            for control in range(3):
                result = run([tmp / 'native', shader, variant, control], env=dict(os.environ, MTL_DEBUG_LAYER='1'))
                print(result.stdout.strip())
                if result.returncode == 3:
                    return 3
                assert result.returncode == int(control != 0), (variant, control, result.returncode, result.stderr[-2000:])
                count = re.search(r'checks=(\d+) failures=(\d+)', result.stdout)
                expected = 0 if not control else 3 if control == 1 else 5 if variant == 2 else 2
                assert count and tuple(map(int, count.groups())) == (20, expected), result.stdout
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
