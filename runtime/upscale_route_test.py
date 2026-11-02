"""Production allocation, phase routing, copies and MetalFX, without game assets.

Positive: 3840x2160 -> 2560x1440 world -> 3840x2160 composition, native
1-pixel HUD ROI: 0/2048 different pixels in 8 SDR/float, 1x/4x, direct/replay
cases. Low-HUD control: 2048/2048 different. Partial copy: 0/96 different;
unmapped control: 10/96. Deferred generation retained before submission: 1,
early-retirement control: 0. Old-size control: 3840x2160 world, expected 2560x1440.
Coverage viewport: 101/3 pixels, double-half control: 101/6. Released level
surface: copy width 3840, child-key control: 2560. EDR=2 adds a native FP16 HUD case.
Off/allocation fallback: 132612 native bytes identical. No git history reads.
Off two-mip float promotion: 1, unconditional eligibility control: 0.
The suite discovers this driver. Bare hosts without Metal exit 3 after CPU checks.
"""
from pathlib import Path
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
SOURCE = (ROOT / 'render.m').read_text()


def section(start, end):
    return SOURCE[SOURCE.index(start):SOURCE.index(end, SOURCE.index(start))]


PRE = r'''
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <stdio.h>
#include <stdarg.h>
#include <unistd.h>
#include <objc/runtime.h>
#include "render_thread.m"
#include "render_upscale_route.h"
id<MTLDevice> mtl;
id<MTLCommandQueue> queue;
int diag_on;
const char *diag_dir;
uint64_t host_ticks(void){return clock_gettime_nsec_np(CLOCK_UPTIME_RAW);}
void diag_event(const char *fmt,...){(void)fmt;}
static int failures,gpt_on,float_scene_on,hdrclip_on,expect_linear_fallback;
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
static id<MTLTexture> shadow_near_surface(uint32_t s){(void)s;return nil;}
static void shadow_near_reset(void){}
RENDER_TEX
static RenderTex bb_rt,ad_rt,fs_world_rt,fs_display_rt,fs_composite_rt,fs_native_depth;
static NFSUpscaleRoute *fs_upscale;
static NSMutableSet<NSNumber *> *fs_screen_copies;
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
static void upscale_finish(void);
static int upscale_copy_active(void);
void render_float_scene_final(int);
void render_float_scene_present_end(void);
'''

MAIN = r'''
@interface GeometryProbe : NSObject
@property MTLViewport viewport;
@property MTLScissorRect scissorRect;
@end
@implementation GeometryProbe
@end
static void geometry_check(void){
 GeometryProbe *probe=[GeometryProbe new];
 dev.viewport[0]=3;dev.viewport[1]=5;dev.viewport[2]=101;dev.viewport[3]=51;
 dev.scissor[0]=(uint32_t)-5;dev.scissor[1]=3;dev.scissor[2]=9;dev.scissor[3]=11;dev.rs[174]=1;
 memset(&eb,0,sizeof eb);configure_geometry((id)probe,bb_rt.ms?:bb_rt.tex);
 CHECK(fabs(probe.viewport.originX-2)<1e-9 && fabs(probe.viewport.width-101.*2/3)<1e-9,"production viewport mapping");
 CHECK(probe.scissorRect.x==0 && probe.scissorRect.y==2 && probe.scissorRect.width==6 && probe.scissorRect.height==6,"production clipped scissor");
 CHECK(dev.viewport[0]==3 && dev.viewport[2]==101 && dev.scissor[0]==(uint32_t)-5,"guest dimensions unchanged");
 ssao_replay=2;memset(&eb,0,sizeof eb);
 id<MTLTexture> half=texture((bb_rt.tex.width+1)/2,(bb_rt.tex.height+1)/2,MTLPixelFormatR8Unorm,1);
 configure_geometry((id)probe,half);
 printf("GPU coverage_viewport_width=%.9f\n",probe.viewport.width);
 CHECK(fabs(probe.viewport.originX-1)<1e-9 && fabs(probe.viewport.width-101./3)<1e-9,"replay half-resolution viewport");
 CHECK(probe.scissorRect.x==0 && probe.scissorRect.y==1 && probe.scissorRect.width==3 && probe.scissorRect.height==3,"replay half-resolution scissor");
 ssao_replay=0;
 dev.rs[174]=0;
}
static void init_blit(void){
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
static void draw(id<MTLTexture> color,id<MTLTexture> depth,int hud){
 NSString *src=@"#include <metal_stdlib>\nusing namespace metal;\nvertex float4 vs(uint i [[vertex_id]]){return float4(float2((i<<1)&2,i&2)*2-1,.25,1);}\n"
 "fragment float4 fs(float4 p [[position]],constant uint &hud [[buffer(0)]]){if(hud){if(p.x<64||p.x>=128||p.y<48||p.y>=80)discard_fragment();uint b=(uint(p.x)+uint(p.y))&1;return float4(b,1-b,1,1);}return float4(.2+.3*fract(p.x/71),.3,.6,1);}";
 NSError *error=nil;id<MTLLibrary> lib=[mtl newLibraryWithSource:src options:nil error:&error];
 MTLRenderPipelineDescriptor *p=[MTLRenderPipelineDescriptor new];p.vertexFunction=[lib newFunctionWithName:@"vs"];p.fragmentFunction=[lib newFunctionWithName:@"fs"];p.colorAttachments[0].pixelFormat=color.pixelFormat;p.rasterSampleCount=color.sampleCount;
 if(depth)p.depthAttachmentPixelFormat=p.stencilAttachmentPixelFormat=depth.pixelFormat;
 id<MTLRenderPipelineState> pipeline=[mtl newRenderPipelineStateWithDescriptor:p error:&error];CHECK(pipeline,"world/HUD pipeline");
 MTLRenderPassDescriptor *rp=[MTLRenderPassDescriptor renderPassDescriptor];rp.colorAttachments[0].texture=color;rp.colorAttachments[0].loadAction=MTLLoadActionLoad;rp.colorAttachments[0].storeAction=MTLStoreActionStore;
 if(depth){rp.depthAttachment.texture=rp.stencilAttachment.texture=depth;rp.depthAttachment.loadAction=rp.stencilAttachment.loadAction=MTLLoadActionLoad;rp.depthAttachment.storeAction=rp.stencilAttachment.storeAction=MTLStoreActionStore;}
 id<MTLRenderCommandEncoder> e=[cmd renderCommandEncoderWithDescriptor:rp];[e setRenderPipelineState:pipeline];
 if(depth){MTLDepthStencilDescriptor *ds=[MTLDepthStencilDescriptor new];ds.depthCompareFunction=MTLCompareFunctionAlways;ds.depthWriteEnabled=YES;MTLStencilDescriptor *st=[MTLStencilDescriptor new];st.stencilCompareFunction=MTLCompareFunctionAlways;st.depthStencilPassOperation=MTLStencilOperationReplace;ds.frontFaceStencil=ds.backFaceStencil=st;[e setDepthStencilState:[mtl newDepthStencilStateWithDescriptor:ds]];[e setStencilReferenceValue:99];}
 [e setViewport:(MTLViewport){0,0,color.width,color.height,0,1}];[e setFragmentBytes:&hud length:4 atIndex:0];
 [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[e endEncoding];
}
static id<MTLBuffer> snapshot(id<MTLTexture> t){
 NSUInteger pixelBytes=t.pixelFormat==MTLPixelFormatRGBA16Float?8:4;
 NSUInteger pitch=(t.width*pixelBytes+255)&~255ul;id<MTLBuffer> b=[mtl newBufferWithLength:pitch*t.height options:MTLResourceStorageModeShared];
 id<MTLBlitCommandEncoder> e=[cmd blitCommandEncoder];
 [e copyFromTexture:t sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(t.width,t.height,1) toBuffer:b destinationOffset:0 destinationBytesPerRow:pitch destinationBytesPerImage:pitch*t.height];[e endEncoding];return b;
}
static id<MTLBuffer> depth_snapshot(id<MTLTexture> depth){
 NSString *src=[NSString stringWithFormat:@"#include <metal_stdlib>\nusing namespace metal;\n#define MS %lu\n"
 "vertex float4 vs(uint i [[vertex_id]]){return float4(float2((i<<1)&2,i&2)*2-1,0,1);}\n"
 "#if MS>1\nfragment float4 fs(float4 p [[position]],depth2d_ms<float,access::read> z [[texture(0)]],texture2d_ms<uint,access::read> s [[texture(1)]]){uint2 q=uint2(p.xy);return float4(z.read(q,0),float(s.read(q,0).x)/255.,0,1);}\n"
 "#else\nfragment float4 fs(float4 p [[position]],depth2d<float,access::read> z [[texture(0)]],texture2d<uint,access::read> s [[texture(1)]]){uint2 q=uint2(p.xy);return float4(z.read(q),float(s.read(q).x)/255.,0,1);}\n#endif\n",(unsigned long)depth.sampleCount];
 NSError *error=nil;id<MTLLibrary> lib=[mtl newLibraryWithSource:src options:nil error:&error];
 MTLRenderPipelineDescriptor *p=[MTLRenderPipelineDescriptor new];p.vertexFunction=[lib newFunctionWithName:@"vs"];p.fragmentFunction=[lib newFunctionWithName:@"fs"];p.colorAttachments[0].pixelFormat=MTLPixelFormatBGRA8Unorm;
 id<MTLRenderPipelineState> pipeline=[mtl newRenderPipelineStateWithDescriptor:p error:&error];CHECK(pipeline,"depth probe pipeline");
 id<MTLTexture> out=texture(depth.width,depth.height,MTLPixelFormatBGRA8Unorm,1);
 MTLRenderPassDescriptor *rp=[MTLRenderPassDescriptor renderPassDescriptor];rp.colorAttachments[0].texture=out;rp.colorAttachments[0].loadAction=MTLLoadActionDontCare;rp.colorAttachments[0].storeAction=MTLStoreActionStore;
 id<MTLRenderCommandEncoder> e=[cmd renderCommandEncoderWithDescriptor:rp];[e setRenderPipelineState:pipeline];[e setFragmentTexture:depth atIndex:0];[e setFragmentTexture:[depth newTextureViewWithPixelFormat:MTLPixelFormatX32_Stencil8] atIndex:1];
 [e setViewport:(MTLViewport){0,0,out.width,out.height,0,1}];[e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[e endEncoding];return snapshot(out);
}
static BOOL completed(id<MTLCommandBuffer> b){return [(id)b isKindOfClass:[RTCmd class]]?((RTCmd *)b)->real.status==MTLCommandBufferStatusCompleted:b.status==MTLCommandBufferStatusCompleted;}
static void finish(void){[cmd commit];[cmd waitUntilCompleted];CHECK(completed(cmd),"GPU completion");}
static void setup(NSUInteger w,NSUInteger h,NSUInteger samples){
 dev.pp[0]=(uint32_t)w;dev.pp[1]=(uint32_t)h;dev.pp[4]=samples>1?(uint32_t)samples:0;dev.pp[9]=1;
 render_resize();++frame_serial;
 for(int i=3;i<8;i++){drop_ms_texture(&copies[i]);copies[i]=(RenderTex){0};objects[i]=(Obj){0};}
 objects[3]=(Obj){.kind=K_SURFACE,.parent=4,.fmt=21,.w=(uint32_t)w,.h=(uint32_t)h};
 objects[4]=(Obj){.kind=K_TEXTURE,.fmt=21,.w=(uint32_t)w,.h=(uint32_t)h,.levels=1,.gpu=&copies[4]};
 copies[4]=(RenderTex){.tex=texture(w,h,MTLPixelFormatBGRA8Unorm,1),.rt=1};copies[4].bytes=copies[4].tex.allocatedSize;
 if(samples>1){copies[4].ms=new_ms_texture(MTLPixelFormatBGRA8Unorm,w,h,(uint32_t)samples);copies[4].resolve_pass=1;}
}
static int cpu(void){
 MTLViewport v=nfs_upscale_viewport((MTLViewport){3,5,101,51,.2,.7},103,57,69,38);
 CHECK(fabs(v.originX-3.*69/103)<1e-9 && v.znear==.2 && v.zfar==.7,"fractional viewport");
 MTLRegion r=nfs_upscale_region(MTLRegionMake2D(1,1,3,3),17,11,12,8);
 CHECK(r.origin.x==0 && r.origin.y==0 && r.size.width==3 && r.size.height==3,"partial half-open edges");
 r=nfs_upscale_region(MTLRegionMake2D(16,10,20,20),17,11,12,8);
 CHECK(r.origin.x==11 && r.origin.y==7 && r.size.width==1 && r.size.height==1,"clipped last pixel");
 r=nfs_upscale_region(MTLRegionMake2D(0,0,17,11),17,11,12,8);CHECK(r.size.width==12 && r.size.height==8,"odd full region");
 r=nfs_upscale_region(MTLRegionMake2D(NSUIntegerMax,0,10,1),17,11,12,8);CHECK(!r.size.width,"overflow bounded");
 r=nfs_upscale_region(MTLRegionMake2D(1,1,0,3),17,11,12,8);CHECK(!r.size.width && !r.size.height,"empty rectangles stay empty");
 CHECK(![[NFSUpscaleRoute alloc] initWithDevice:nil width:3840 height:2160 format:MTLPixelFormatBGRA8Unorm samples:1],"unsupported native fallback");
 printf("CPU rectangles=6 unsupported_fallback=1 failures=%d\n",failures);return failures!=0;
}
static int fixture(int samples,int replay,int hud_mutant,int floating){@autoreleasepool{
 float_scene_on=floating!=0;fixture_headroom=floating==2?2:1;rt_on=replay;setup(3840,2160,samples);cmd=rt_command_buffer();clear_native();
 id<MTLTexture> native=bb_rt.tex;id<MTLTexture> nativeDepth=ad_rt.tex;
 render_float_scene_begin();
 CHECK(fs_upscale && fs_active,"physical route selected");
 CHECK(bb_rt.tex.width==2560 && bb_rt.tex.height==1440,"world dimensions 2560x1440");
 CHECK(ad_rt.tex.width==2560 && ad_rt.tex.height==1440,"matched world depth");
 CHECK((bb_rt.ms?:bb_rt.tex).sampleCount==(ad_rt.ms?:ad_rt.tex).sampleCount,"MSAA compatibility");
 CHECK(fs_upscale.composition.width==3840 && fs_upscale.composition.height==2160,"native composition dimensions");
 NSUInteger worldWidth=bb_rt.tex.width,worldHeight=bb_rt.tex.height;
 geometry_check();
 id<MTLBuffer> seededDepth=depth_snapshot(ad_rt.ms?:ad_rt.tex);
 draw(bb_rt.ms?:bb_rt.tex,ad_rt.ms?:ad_rt.tex,0);bb_rt.stale=bb_rt.ms!=nil;
 render_stretch(1,0,3,0,1);
 CHECK(copies[4].tex.width==2560 && copies[4].tex.height==1440,"physical screen copy");
 id<MTLTexture> firstCopy=copies[4].tex;render_stretch(1,0,3,0,1);CHECK(copies[4].tex==firstCopy,"copy target reuse");
 if(hud_mutant){draw(bb_rt.ms?:bb_rt.tex,nil,1);bb_rt.stale=bb_rt.ms!=nil;}
 NSUInteger bytes=fs_upscale.allocatedBytes,encodes=fs_upscale.spatialEncodes;
 Obj releasedSurface=objects[3];objects[3]=(Obj){0};
 render_float_scene_final(1);
 CHECK(expect_linear_fallback?fs_upscale.spatialEncodes==encodes:fs_upscale.spatialEncodes>=encodes+2,"M03 accepted or explicit linear fallback");
 CHECK((floating==2?(fs_composing && bb_rt.tex.width==native.width && bb_rt.tex.pixelFormat==MTLPixelFormatRGBA16Float):bb_rt.tex==native) && ad_rt.tex==nativeDepth,"native phase restored");
 CHECK(copies[4].tex.width==3840 && copies[4].tex.height==2160,"screen copy composition");
 objects[3]=releasedSurface;
 id<MTLTexture> composedCopy=copies[4].tex;NSUInteger cachedBytes=fs_upscale.cachedCopyBytes;
 if(!hud_mutant)draw(bb_rt.ms?:bb_rt.tex,nil,1);
 bb_rt.stale=bb_rt.ms!=nil;msaa_resolve(&bb_rt);
 id<MTLBuffer> actual=snapshot(bb_rt.tex);
 NSUInteger pixelBytes=bb_rt.tex.pixelFormat==MTLPixelFormatRGBA16Float?8:4,rowBytes=3840*pixelBytes;
 id<MTLTexture> reference=texture(3840,2160,bb_rt.tex.pixelFormat,1);draw(reference,nil,1);id<MTLBuffer> expected=snapshot(reference);
 // Depth AND stencil survive native -> physical -> native with nondefault values.
 id<MTLBuffer> ds=depth_snapshot(ad_rt.ms?:ad_rt.tex);
 finish();
 unsigned char *a=actual.contents,*b=expected.contents;NSUInteger different=0;
 for(NSUInteger y=48;y<80;y++)for(NSUInteger x=64;x<128;x++)different+=memcmp(a+y*rowBytes+x*pixelBytes,b+y*rowBytes+x*pixelBytes,pixelBytes)!=0;
 CHECK(!different,"HUD ROI byte-identical");
 unsigned char *seeded=seededDepth.contents;CHECK(seeded[1]==37 && abs((int)seeded[2]-96)<=1,"seeded world depth/stencil .375/37");
 unsigned char *depthBytes=ds.contents;CHECK(depthBytes[1]==99 && abs((int)depthBytes[2]-64)<=1,"world depth/stencil reaches native .25/99");
 printf("GPU samples=%d replay=%d float=%d world=%lux%lu output=3840x2160 copy=%lux%lu route_bytes=%lu cached_copy_bytes=%lu spatial_encodes=%lu HUD_bad=%lu/2048\n",samples,replay,floating,(unsigned long)worldWidth,(unsigned long)worldHeight,(unsigned long)copies[4].tex.width,(unsigned long)copies[4].tex.height,(unsigned long)bytes,(unsigned long)cachedBytes,(unsigned long)(fs_upscale.spatialEncodes-encodes),(unsigned long)different);
 render_float_scene_present_end();
 id<MTLTexture> reused=fs_upscale.worldColor;++frame_serial;cmd=rt_command_buffer();render_float_scene_begin();CHECK(fs_upscale.worldColor==reused,"world target reuse");render_stretch(1,0,3,0,1);CHECK(copies[4].tex==firstCopy,"physical copy reused across frames");render_float_scene_final(1);CHECK(copies[4].tex==composedCopy && fs_upscale.cachedCopyBytes==cachedBytes,"composition copy reused without allocations");finish();render_float_scene_present_end();
 [fs_upscale forgetCopyKey:(uintptr_t)&copies[4]];CHECK(!fs_upscale.cachedCopyBytes,"released target cache retired");
 return failures!=0;
}}
static int odd_and_retirement(void){
 __weak NFSUpscaleRoute *retired;
 __weak id<MTLTexture> retiredCopy;
 BOOL retainedBefore=NO;
 @autoreleasepool{
 float_scene_on=0;fixture_headroom=1;rt_on=1;setup(17,11,4);cmd=rt_command_buffer();clear_native();render_float_scene_begin();
 CHECK(bb_rt.tex.width==12 && bb_rt.tex.height==8 && ad_rt.tex.width==12,"odd physical allocation");
 MTLRenderPassDescriptor *seed=[MTLRenderPassDescriptor renderPassDescriptor];seed.colorAttachments[0].texture=copies[4].ms?:copies[4].tex;seed.colorAttachments[0].loadAction=MTLLoadActionClear;seed.colorAttachments[0].storeAction=MTLStoreActionStore;seed.colorAttachments[0].clearColor=MTLClearColorMake(.75,.5,.25,1);copies[4].stale=copies[4].ms!=nil;
 id<MTLRenderCommandEncoder> seedEncoder=[cmd renderCommandEncoderWithDescriptor:seed];[seedEncoder endEncoding];
 guest[4]=1;guest[5]=1;guest[6]=4;guest[7]=4;render_stretch(1,16,3,16,1);
 CHECK(copies[4].tex.width==12 && copies[4].tex.height==8,"odd partial copy allocation");
 msaa_resolve(&copies[4]);id<MTLBuffer> partial=snapshot(copies[4].tex);
 retiredCopy=copies[4].tex;
 render_float_scene_final(1);retired=fs_upscale;id<MTLCommandBuffer> pending=cmd;
 [fs_upscale forgetCopyKey:(uintptr_t)&copies[4]];CHECK(retiredCopy!=nil,"evicted copy retained before submit");
 // Replace allocation before recording has even been submitted.
 setup(19,13,4);retainedBefore=retired!=nil;CHECK(retainedBefore,"retirement retained before submit");
 cmd=rt_command_buffer();clear_native();render_float_scene_begin();CHECK(bb_rt.tex.width==13 && bb_rt.tex.height==9,"resized physical allocation");render_float_scene_final(1);
 [pending commit];[cmd commit];[pending waitUntilCompleted];[cmd waitUntilCompleted];CHECK(completed(pending) && completed(cmd),"retired GPU work completed");pending=nil;cmd=nil;
 unsigned char *pixels=partial.contents;NSUInteger partialBad=0;
 for(NSUInteger y=0;y<8;y++)for(NSUInteger x=0;x<12;x++){
     unsigned char *v=pixels+y*256+x*4;BOOL covered=x<3 && y<3;
     partialBad+=abs((int)v[0]-(covered?128:64))>1 || abs((int)v[1]-(covered?64:128))>1 || abs((int)v[2]-(covered?32:191))>1;
 }
 CHECK(!partialBad,"partial outside pixels preserved");printf("GPU partial_bad=%lu/96\n",(unsigned long)partialBad);
 }
 for(int i=0;i<100 && (retired || retiredCopy);i++){@autoreleasepool{usleep(10000);}}
 CHECK(!retired,"retirement released after completion");
 CHECK(!retiredCopy,"evicted copy released after completion");
 printf("GPU odd=17x11->12x8 resized=19x13->13x9 partial=1 asynchronous_retirement=%d failures=%d\n",retainedBefore && retired==nil,failures);
 return failures!=0;
}
static int off_fixture(void){@autoreleasepool{
 setup(257,129,1);cmd=rt_command_buffer();clear_native();id<MTLTexture> color=bb_rt.tex,depth=ad_rt.tex;
 render_float_scene_begin();CHECK(!fs_active && !fs_upscale && bb_rt.tex==color && ad_rt.tex==depth,"Off native ownership");
 draw(bb_rt.tex,ad_rt.tex,0);draw(bb_rt.tex,nil,1);render_float_scene_final(1);id<MTLBuffer> actual=snapshot(bb_rt.tex);
 id<MTLTexture> reference=texture(257,129,MTLPixelFormatBGRA8Unorm,1);draw(reference,nil,0);draw(reference,nil,1);id<MTLBuffer> expected=snapshot(reference);finish();
 NSUInteger pitch=(257*4+255)&~255ul,different=0;for(NSUInteger y=0;y<129;y++)different+=memcmp((char *)actual.contents+y*pitch,(char *)expected.contents+y*pitch,257*4)!=0;
 CHECK(!different,"Off byte-identical native pixels");printf("GPU Off identity_bytes=%d different_rows=%lu failures=%d\n",257*129*4,(unsigned long)different,failures);return failures!=0;
}}
static int off_copy_fixture(void){@autoreleasepool{
 float_scene_on=1;rt_on=0;setup(257,129,1);
 MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:257 height:129 mipmapped:YES];
 d.mipmapLevelCount=2;d.usage=MTLTextureUsageShaderRead|MTLTextureUsageRenderTarget;d.storageMode=MTLStorageModePrivate;
 copies[4].tex=[mtl newTextureWithDescriptor:d];copies[4].bytes=copies[4].tex.allocatedSize;objects[4].levels=2;objects[3].level=1;
 cmd=rt_command_buffer();clear_native();render_float_scene_begin();render_stretch(1,0,3,0,1);
 CHECK(!fs_upscale && copies[4].tex.pixelFormat==MTLPixelFormatRGBA16Float && copies[4].tex.mipmapLevelCount==2,"Off preserves float mip-surface promotion");
 render_float_scene_final(1);finish();printf("GPU Off float_mip_levels=%lu format=%lu failures=%d\n",(unsigned long)copies[4].tex.mipmapLevelCount,(unsigned long)copies[4].tex.pixelFormat,failures);return failures!=0;
}}
static IMP original_new_texture;
static BOOL reject_color(id route,SEL selector,id source,id output,id buffer){(void)route;(void)selector;(void)source;(void)output;(void)buffer;return NO;}
static BOOL fail_world_allocation;
static id fail_texture(id device,SEL selector,MTLTextureDescriptor *d){
 if(fail_world_allocation && d.width==172 && d.height==86)return nil;
 return ((id(*)(id,SEL,id))original_new_texture)(device,selector,d);
}
static int allocation_fallback(void){@autoreleasepool{
 float_scene_on=0;rt_on=0;setup(257,129,1);cmd=rt_command_buffer();clear_native();
 id<MTLTexture> native=bb_rt.tex,depth=ad_rt.tex;
 Method method=class_getInstanceMethod(object_getClass(mtl),@selector(newTextureWithDescriptor:));
 original_new_texture=method_setImplementation(method,(IMP)fail_texture);fail_world_allocation=YES;
 render_float_scene_begin();fail_world_allocation=NO;method_setImplementation(method,original_new_texture);
 CHECK(!fs_upscale && !fs_active && bb_rt.tex==native && ad_rt.tex==depth,"allocation failure keeps native attachments");
 draw(bb_rt.tex,ad_rt.tex,0);draw(bb_rt.tex,nil,1);id<MTLBuffer> actual=snapshot(bb_rt.tex);
 id<MTLTexture> reference=texture(257,129,MTLPixelFormatBGRA8Unorm,1);draw(reference,nil,0);draw(reference,nil,1);id<MTLBuffer> expected=snapshot(reference);finish();
 NSUInteger pitch=(257*4+255)&~255ul,different=0;for(NSUInteger y=0;y<129;y++)different+=memcmp((char *)actual.contents+y*pitch,(char *)expected.contents+y*pitch,257*4)!=0;
 CHECK(!different,"allocation fallback native pixels");printf("GPU allocation_fallback_native_bytes=%d different_rows=%lu\n",257*129*4,(unsigned long)different);return failures!=0;
}}
int main(int argc,const char **argv){@autoreleasepool{
 if(cpu())return 1;
 mtl=MTLCreateSystemDefaultDevice();if(!mtl){puts("GPU PENDING: Metal device unavailable");return 3;}
 if(![NFSSpatialCache supportsDevice:mtl]){puts("GPU PENDING: MetalFX unavailable");return 3;}
 queue=[mtl newCommandQueue];init_blit();rt_init();
 if(argc>1 && !strcmp(argv[1],"--off"))return off_fixture();
 if(argc>1 && !strcmp(argv[1],"--off-copy"))return off_copy_fixture();
 if(argc>1 && !strcmp(argv[1],"--hud-mutant"))return fixture(1,0,1,0);
 if(argc>1 && !strcmp(argv[1],"--dimensions-mutant"))return fixture(1,0,0,0);
 if(argc>1 && !strcmp(argv[1],"--retirement-mutant"))return odd_and_retirement();
 if(argc>1 && !strcmp(argv[1],"--partial-mutant"))return odd_and_retirement();
 if(argc>1 && !strcmp(argv[1],"--geometry-mutant"))return fixture(1,0,0,0);
 if(argc>1 && !strcmp(argv[1],"--owner-mutant"))return fixture(1,0,0,0);
 if(argc>1 && !strcmp(argv[1],"--encode-fallback")){
     expect_linear_fallback=1;Method method=class_getInstanceMethod(NFSUpscaleRoute.class,@selector(encodeColor:to:buffer:));
     IMP original=method_setImplementation(method,(IMP)reject_color);int result=fixture(4,1,0,1);method_setImplementation(method,original);return result;
 }
 for(int floating=0;floating<2;floating++)for(int replay=0;replay<2;replay++)for(int samples=1;samples<=4;samples+=3)if(fixture(samples,replay,0,floating))return 1;
 if(fixture(4,1,0,2))return 1;
 if(odd_and_retirement())return 1;
 return allocation_fallback();
}}
'''


def run(args, env=None):
    return subprocess.run([str(a) for a in args], capture_output=True, text=True, env=env, timeout=180)


def checked(result):
    if result.returncode:
        raise RuntimeError(f'exit {result.returncode}\n{result.stdout[-4000:]}{result.stderr[-5000:]}')
    print(result.stdout[-4000:], end='', flush=True)


def make_harness(source=SOURCE, main=MAIN):
    def section(start, end):
        return source[source.index(start):source.index(end, source.index(start))]
    internal = (ROOT / 'render_internal.h').read_text()
    render_tex = internal[internal.index('typedef struct {'):internal.index('} RenderTex;') + len('} RenderTex;')]
    maps = section('typedef struct { uint64_t key; void *obj; } MapSlot;', '// ---------------- GPU-side objects')
    blit_source = section('static const char *blit_src =', '// Startup-only hidden EDR transport')
    quad = section('void quad_into(id<MTLCommandBuffer> cb,', 'void render_stretch(')
    routing = section('// BEGIN float scene routing', '// Diagnostic transport sentinel')
    geometry = ('enum{EB_VIEWPORT=4,EB_SCISSOR=8};\n'
                'static struct{unsigned have;MTLViewport vp;MTLScissorRect sc;} eb;\n'
                'static int ssao_replay,ssao_reference_record;\n'
                'static void configure_geometry(id<MTLRenderCommandEncoder> enc,id<MTLTexture> rt0){const uint32_t *rs=dev.rs;\n' +
                section('    float vpz0, vpz1;', '    if (!(eb.have & EB_BIAS)') + '}\n')
    harness = (PRE.replace('RENDER_TEX', render_tex) + maps + '\nstatic Map blit_pipelines;\n' + blit_source +
               quad + '\nstatic void blit_quad(id<MTLTexture> s,MTLRegion sr,id<MTLTexture> d,NSUInteger l,NSUInteger sl,MTLRegion dr,int linear){quad_into(cmd,s,sr,d,l,sl,dr,linear);}\n' +
               section('RenderTex *surface_rt(', '// ---------------- setup') +
               section('static MTLTextureDescriptor *texture_descriptor(', 'RenderTex *gpu_tex(') +
               section('void render_resize(void) {', '// BEGIN float scene routing') + routing +
               section('void render_stretch(', 'void render_readback(') + geometry + main)
    return harness


def main():
    with tempfile.TemporaryDirectory(prefix='upscale-route-') as scratch:
        scratch = Path(scratch)
        harness = make_harness()
        source = scratch / 'fixture.m'
        source.write_text(harness)
        executable = scratch / 'fixture'
        build = ['clang', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function', '-Wno-unused-variable',
                 '-fobjc-arc', '-I', ROOT, '-I', ROOT.parent / 'recomp', source,
                 ROOT / 'render_upscale_route.m', ROOT / 'render_metalfx_spatial.m',
                 '-framework', 'Foundation', '-framework', 'Metal', '-weak_framework', 'MetalFX', '-o', executable]
        checked(run(build))
        env = dict(os.environ, NFS_UPSCALE='spatial', NFS_PADS='1')
        positive = run([executable], env)
        if positive.returncode == 3:
            print(positive.stdout, end='')
            raise SystemExit(3)
        checked(positive)
        linear = run([executable, '--encode-fallback'], env)
        checked(linear)
        off = run([executable, '--off'], dict(env, NFS_UPSCALE='off'))
        checked(off)
        off_copy = run([executable, '--off-copy'], dict(env, NFS_UPSCALE='off'))
        checked(off_copy)
        split = run([executable, '--off'], dict(env, NFS_PADS='2'))
        checked(split)
        hud = run([executable, '--hud-mutant'], env)
        assert hud.returncode == 1 and 'FAIL HUD ROI byte-identical' in hud.stderr and 'HUD_bad=2048/2048' in hud.stdout, hud.stdout + hud.stderr
        print(hud.stdout[-1300:], end='')
        # Freeze the original allocation defect in a scratch source, never git history.
        route = (ROOT / 'render_upscale_route.m').read_text()
        old_size = route.replace('NSUInteger iw=(w*2+2)/3,ih=(h*2+2)/3;', 'NSUInteger iw=w,ih=h;')
        assert old_size != route
        mutant = scratch / 'old_size.m'
        mutant.write_text(old_size)
        mutant_build = [mutant if a == ROOT / 'render_upscale_route.m' else a for a in build]
        checked(run(mutant_build))
        dimensions = run([executable, '--dimensions-mutant'], env)
        assert dimensions.returncode == 1 and 'FAIL world dimensions 2560x1440' in dimensions.stderr and 'world=3840x2160' in dimensions.stdout, dimensions.stdout + dimensions.stderr
        print(dimensions.stdout[-1300:], end='')
        no_hold = route.replace('[buffer addCompletedHandler:^(id<MTLCommandBuffer> done){(void)done;(void)self.allocatedBytes;}];', '(void)buffer;')
        assert no_hold != route
        mutant.write_text(no_hold)
        checked(run(mutant_build))
        retirement = run([executable, '--retirement-mutant'], env)
        assert retirement.returncode == 1 and 'FAIL retirement retained before submit' in retirement.stderr and 'asynchronous_retirement=0' in retirement.stdout, retirement.stdout + retirement.stderr
        print(retirement.stdout[-1300:], end='')
        bad_partial = harness.replace('dr=nfs_upscale_region(dr,lw,lh,dw,dh);', '(void)lw;(void)lh;')
        assert bad_partial != harness
        source.write_text(bad_partial)
        checked(run(build))
        partial = run([executable, '--partial-mutant'], env)
        assert partial.returncode == 1 and 'FAIL partial outside pixels preserved' in partial.stderr and 'partial_bad=10/96' in partial.stdout, partial.stdout + partial.stderr
        print(partial.stdout[-1300:], end='')
        double_half = harness.replace('if(fs_upscale && fs_active && ssao_replay && !ssao_reference_record && dev.rt[0]==dev.backbuffer) {', 'if(0) {')
        assert double_half != harness
        source.write_text(double_half)
        checked(run(build))
        geometry_bad = run([executable, '--geometry-mutant'], env)
        assert geometry_bad.returncode == 1 and 'FAIL replay half-resolution viewport' in geometry_bad.stderr, geometry_bad.stdout + geometry_bad.stderr
        surface_owner = harness.replace('[fs_screen_copies addObject:@(o->parent?:dst)];', '[fs_screen_copies addObject:@(dst)];')
        assert surface_owner != harness
        source.write_text(surface_owner)
        checked(run(build))
        owner_bad = run([executable, '--owner-mutant'], env)
        assert owner_bad.returncode == 1 and 'FAIL screen copy composition' in owner_bad.stderr, owner_bad.stdout + owner_bad.stderr
        bad_off = harness.replace('if(fs_upscale) {\n        if(t->w!=dev.pp[0]', 'if(1) {\n        if(t->w!=dev.pp[0]')
        assert bad_off != harness
        source.write_text(bad_off)
        checked(run(build))
        off_bad = run([executable, '--off-copy'], dict(env, NFS_UPSCALE='off'))
        assert off_bad.returncode == 1 and 'FAIL Off preserves float mip-surface promotion' in off_bad.stderr, off_bad.stdout + off_bad.stderr
        report = Path(os.environ.get('NFS_METAL_CACHE', ROOT.parent / '.metal-cache')) / 'upscale-route-test.log'
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(positive.stdout + 'REJECTED ENCODE LINEAR FALLBACK\n' + linear.stdout + off.stdout + off_copy.stdout + 'SPLIT NATIVE FALLBACK\n' + split.stdout +
                          'LOW-HUD CONTROL exit=1\n' + hud.stdout + hud.stderr +
                          'OLD-SIZE CONTROL exit=1\n' + dimensions.stdout + dimensions.stderr +
                          'EARLY-RETIREMENT CONTROL exit=1\n' + retirement.stdout + retirement.stderr +
                          'UNMAPPED-PARTIAL CONTROL exit=1\n' + partial.stdout + partial.stderr +
                          'DOUBLE-HALF CONTROL exit=1\n' + geometry_bad.stdout + geometry_bad.stderr +
                          'RELEASED-SURFACE OWNER CONTROL exit=1\n' + owner_bad.stdout + owner_bad.stderr +
                          'OFF-MIP PROMOTION CONTROL exit=1\n' + off_bad.stdout + off_bad.stderr)
        print('upscale_route_test: PASS production dimensions/pixels/lifetimes; 7/7 negative controls rejected; native/linear fallbacks PASS', flush=True)


if __name__ == '__main__':
    main()
