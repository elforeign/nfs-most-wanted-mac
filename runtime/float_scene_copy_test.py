#!/usr/bin/env python3
"""Native float-scene StretchRect promotes the owner of a nonzero mip surface.

Recovered native two-mip fixture: 257x129 BGRA8 -> FP16, levels=2.
Production: format=115 (FP16), failures=0; single-level-eligibility mutant:
format=80 (BGRA8), failures=1. Both retain 2 mip levels; exits 0/1.
The full offline suite autodiscovers this driver. Metal unavailable exits 3.
No private assets or spatial fixtures required.
"""
from pathlib import Path
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent


def section(source, start, end):
    return source[source.index(start):source.index(end, source.index(start))]


def checked_run(args):
    result = subprocess.run(list(map(str, args)), capture_output=True, text=True, timeout=180)
    if result.returncode:
        raise RuntimeError(f'exit {result.returncode}\n{result.stdout[-2000:]}{result.stderr[-5000:]}')
    return result


PRE = r'''
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <stdio.h>
#include <stdarg.h>
#include <unistd.h>
#include "d3d9_internal.h"

id<MTLDevice> mtl;
id<MTLCommandQueue> queue;
int diag_on;
const char *diag_dir;
uint64_t host_ticks(void){return clock_gettime_nsec_np(CLOCK_UPTIME_RAW);}
void diag_event(const char *fmt,...){(void)fmt;}
static int failures,gpt_on,float_scene_on,hdrclip_on;

static float fixture_headroom=1;
#define CHECK(x,label) do{if(!(x)){fprintf(stderr,"FAIL %s line=%d\n",label,__LINE__);++failures;}}while(0)
#define gpt_render_auto(...) ((void)0)
#define gpt_name(...) ((void)0)
#define hdrclip_scan(...) ((void)0)
#define hdrclip_pair(...) ((void)0)
#undef MF32
#define MF32(a) 0.0f
static uint32_t guest[64];
#undef M32
#define M32(a) guest[(a)/4]
static id<MTLCommandBuffer> cmd;
static id<MTLTexture> backbuffer_tex,present_tex,autodepth_tex;
static id<MTLLibrary> blit_lib;
static id<MTLRenderPipelineState> blit_pso;
static id<MTLSamplerState> blit_linear,blit_point;
uint32_t mipdim(uint32_t w,uint32_t l){return MAX(w>>l,1u);}
static id<MTLRenderPipelineState> pipeline_build(MTLRenderPipelineDescriptor *p,const char *name,NSError **e){(void)name;return [mtl newRenderPipelineStateWithDescriptor:p error:e];}
static void end_pass(void){}
static void ensure_cmd(void){}
int postfx_look_variant(const char *v){(void)v;return 0;}
static float render_hdr_headroom(void){return fixture_headroom;}
static void float_scene_ramp(void){}
// The routed surfaces are retail ones with the near-shadow experiment Off.
RENDER_TEX
static RenderTex bb_rt,ad_rt,fs_world_rt,fs_display_rt,fs_composite_rt;


static int fs_active,fs_composing,fs_hud,fs_toned,pass_dirty;
static float fs_headroom=1;
static uint64_t fs_serial,frame_serial=1;
static int64_t msaa_bytes,live_tex_bytes,live_rt_bytes;
Device dev={.backbuffer=1,.autodepth=2,.rt={1},.ds=2};
static Obj objects[8];
static RenderTex copies[8];
Obj *obj(uint32_t h){return h>2 && h<8?&objects[h]:NULL;}
static RenderTex *gpu_tex(Obj *o){return o?o->gpu:NULL;}
static int is_depth_fmt(uint32_t f){return f==75;}
static RenderTex *enc_ms[5];
static uint8_t enc_resolve[5];
static void msaa_forget(RenderTex *r){(void)r;}
static void drop_ms_texture(RenderTex *r){msaa_bytes-=r->ms.allocatedSize;r->ms=nil;r->stale=0;}
static id<MTLTexture> texture(NSUInteger w,NSUInteger h,MTLPixelFormat f,NSUInteger samples){
 MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:f width:w height:h mipmapped:NO];
 d.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead|MTLTextureUsagePixelFormatView;
 d.storageMode=MTLStorageModePrivate;
 if(samples>1){d.textureType=MTLTextureType2DMultisample;d.sampleCount=samples;}
 return [mtl newTextureWithDescriptor:d];
}
static id<MTLTexture> new_ms_texture(MTLPixelFormat f,NSUInteger w,NSUInteger h,uint32_t samples){
 id<MTLTexture> t=texture(w,h,f,samples);msaa_bytes+=t.allocatedSize;return t;
}
uint32_t render_msaa_count(uint32_t type,uint32_t quality){(void)quality;return type>1?type:1;}
static void msaa_resolve(RenderTex *r){
 if(!r || !r->ms || !r->stale)return;
 MTLRenderPassDescriptor *p=[MTLRenderPassDescriptor renderPassDescriptor];
 MTLRenderPassAttachmentDescriptor *a=r->tex.pixelFormat==MTLPixelFormatDepth32Float_Stencil8?(id)p.depthAttachment:(id)p.colorAttachments[0];
 a.texture=r->ms;a.resolveTexture=r->tex;a.loadAction=MTLLoadActionLoad;a.storeAction=MTLStoreActionStoreAndMultisampleResolve;
 id<MTLRenderCommandEncoder> e=[cmd renderCommandEncoderWithDescriptor:p];[e endEncoding];r->stale=0;
}
static void blit_quad(id<MTLTexture>,MTLRegion,id<MTLTexture>,NSUInteger,NSUInteger,MTLRegion,int);
static void render_hdr_world_copy(id<MTLTexture> s,id<MTLTexture> d){blit_quad(s,MTLRegionMake2D(0,0,s.width,s.height),d,0,0,MTLRegionMake2D(0,0,d.width,d.height),0);}
static void float_scene_copy(uint32_t,MTLPixelFormat);


void render_float_scene_final(int);
void render_float_scene_present_end(void);
'''

MAIN = r'''static void init_blit(void){
 NSError *error=nil;blit_lib=[mtl newLibraryWithSource:[NSString stringWithUTF8String:blit_src] options:nil error:&error];
 MTLRenderPipelineDescriptor *p=[MTLRenderPipelineDescriptor new];p.vertexFunction=[blit_lib newFunctionWithName:@"blit_vs"];p.fragmentFunction=[blit_lib newFunctionWithName:@"blit_fs"];p.colorAttachments[0].pixelFormat=MTLPixelFormatBGRA8Unorm;
 blit_pso=[mtl newRenderPipelineStateWithDescriptor:p error:&error];CHECK(blit_pso,"blit pipeline");
 MTLSamplerDescriptor *s=[MTLSamplerDescriptor new];s.minFilter=s.magFilter=MTLSamplerMinMagFilterNearest;blit_point=[mtl newSamplerStateWithDescriptor:s];
 s.minFilter=s.magFilter=MTLSamplerMinMagFilterLinear;blit_linear=[mtl newSamplerStateWithDescriptor:s];
}
static void clear_native(void){
 MTLRenderPassDescriptor *p=[MTLRenderPassDescriptor renderPassDescriptor];p.colorAttachments[0].texture=bb_rt.ms?:bb_rt.tex;
 p.colorAttachments[0].loadAction=MTLLoadActionClear;p.colorAttachments[0].storeAction=MTLStoreActionStore;p.colorAttachments[0].clearColor=MTLClearColorMake(.125,.25,.5,1);
 p.depthAttachment.texture=p.stencilAttachment.texture=ad_rt.ms?:ad_rt.tex;
 p.depthAttachment.loadAction=p.stencilAttachment.loadAction=MTLLoadActionClear;
 p.depthAttachment.storeAction=p.stencilAttachment.storeAction=MTLStoreActionStore;
 p.depthAttachment.clearDepth=.375;p.stencilAttachment.clearStencil=37;
 id<MTLRenderCommandEncoder> e=[cmd renderCommandEncoderWithDescriptor:p];[e endEncoding];bb_rt.stale=bb_rt.ms!=nil;ad_rt.stale=ad_rt.ms!=nil;
}
static void finish(void){[cmd commit];[cmd waitUntilCompleted];CHECK(cmd.status==MTLCommandBufferStatusCompleted,"GPU completion");}

static void setup(NSUInteger w,NSUInteger h,NSUInteger samples){
 (void)samples;dev.pp[0]=(uint32_t)w;dev.pp[1]=(uint32_t)h;
 bb_rt=(RenderTex){.tex=texture(w,h,MTLPixelFormatBGRA8Unorm,1)};
 ad_rt=(RenderTex){.tex=texture(w,h,MTLPixelFormatDepth32Float_Stencil8,1)};
 backbuffer_tex=bb_rt.tex;autodepth_tex=ad_rt.tex;
 objects[3]=(Obj){.kind=K_SURFACE,.parent=4,.fmt=21,.w=(uint32_t)w,.h=(uint32_t)h};
 objects[4]=(Obj){.kind=K_TEXTURE,.fmt=21,.w=(uint32_t)w,.h=(uint32_t)h,.gpu=&copies[4]};
 copies[4].rt=1;
}
static int native_copy_fixture(void){@autoreleasepool{
 float_scene_on=1;setup(257,129,1);
 MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:257 height:129 mipmapped:YES];
 d.mipmapLevelCount=2;d.usage=MTLTextureUsageShaderRead|MTLTextureUsageRenderTarget;d.storageMode=MTLStorageModePrivate;
 copies[4].tex=[mtl newTextureWithDescriptor:d];copies[4].bytes=copies[4].tex.allocatedSize;objects[4].levels=2;objects[3].level=1;
 cmd=[queue commandBuffer];clear_native();render_float_scene_begin();render_stretch(1,0,3,0,1);
 CHECK(copies[4].tex.pixelFormat==MTLPixelFormatRGBA16Float && copies[4].tex.mipmapLevelCount==2,"native preserves float mip-surface promotion");
 render_float_scene_final(1);finish();printf("GPU native float_mip_levels=%lu format=%lu failures=%d\n",(unsigned long)copies[4].tex.mipmapLevelCount,(unsigned long)copies[4].tex.pixelFormat,failures);return failures!=0;
}}

int main(void){@autoreleasepool{
 mtl=MTLCreateSystemDefaultDevice();if(!mtl){puts("GPU PENDING: Metal unavailable");return 3;}
 queue=[mtl newCommandQueue];init_blit();return native_copy_fixture();
}}
'''

def make_harness(source):
    internal = (ROOT / 'render_internal.h').read_text()
    render_tex = section(internal, 'typedef struct {', '} RenderTex;') + '} RenderTex;'
    return (PRE.replace('RENDER_TEX', render_tex) +
            section(source, 'typedef struct { uint64_t key; void *obj; } MapSlot;', '// ---------------- GPU-side objects') +
            '\nstatic Map blit_pipelines;\n' +
            section(source, 'static const char *blit_src =', '// Startup-only hidden EDR transport') +
            section(source, 'void quad_into(id<MTLCommandBuffer> cb,', 'void render_stretch(') +
            '\nstatic void blit_quad(id<MTLTexture> s,MTLRegion sr,id<MTLTexture> d,NSUInteger l,NSUInteger sl,MTLRegion dr,int linear){quad_into(cmd,s,sr,d,l,sl,dr,linear);}\n' +
            section(source, 'RenderTex *surface_rt(', '// ---------------- setup') +
            section(source, 'static MTLTextureDescriptor *texture_descriptor(', 'uint32_t bad_tex_binds;') +
            section(source, '// BEGIN float scene routing', '// Diagnostic transport sentinel') +
            section(source, 'void render_stretch(', 'void render_readback(') + MAIN)


def mutate(harness):
    # Reintroduce the deleted control's unconditional single-level eligibility.
    needle = '    if(g->tex.width!=dev.pp[0] || g->tex.height!=dev.pp[1])return;'
    assert harness.count(needle) == 1
    return harness.replace(needle, '    if(t->w!=dev.pp[0] || t->h!=dev.pp[1] || t->levels>1 || (o->parent && (o->level || o->face)))return;')


PREFIX = 'float-scene-copy-'
FAILURE = 'FAIL native preserves float mip-surface promotion'


def main():
    source = (ROOT / 'render.m').read_text()
    good = make_harness(source)
    broken = mutate(good)
    assert broken != good, 'mutation did not apply'
    with tempfile.TemporaryDirectory(prefix=PREFIX) as directory:
        tmp = Path(directory)
        fixture, executable = tmp / 'fixture.m', tmp / 'fixture'
        build = ['clang', '-O1', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
                 '-Wno-unused-variable', '-Wno-incompatible-pointer-types', '-fobjc-arc',
                 '-I', ROOT, '-I', ROOT.parent / 'recomp', fixture,
                 '-framework', 'Foundation', '-framework', 'Metal', '-o', executable]
        fixture.write_text(good)
        checked_run(build)
        result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=120)
        print('FIXED ' + result.stdout + result.stderr, end='', flush=True)
        if result.returncode == 3:
            raise SystemExit(3)
        assert result.returncode == 0, result.stdout + result.stderr
        proof = 'FIXED ' + result.stdout + result.stderr
        fixture.write_text(broken)
        checked_run(build)
        result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=120)
        print('MUTANT ' + result.stdout + result.stderr, end='', flush=True)
        assert result.returncode == 1 and FAILURE in result.stderr, result.stdout + result.stderr
        proof += 'MUTANT ' + result.stdout + result.stderr
        report = Path(os.environ.get('NFS_METAL_CACHE', ROOT.parent / '.metal-cache')) / (Path(__file__).stem + '.log')
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(proof)
        print(PREFIX + 'PASS fixed=0 mutant=1')


if __name__ == '__main__':
    main()
