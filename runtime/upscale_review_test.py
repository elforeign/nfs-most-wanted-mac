"""Spatial routing integration regressions, executing production renderer fragments.

Paired original -> fixed measurements:
Off, 3 frames: viewport scaling calls 3 -> 0; environment reads 3 -> 0.
Unsupported MetalFX, 3 frames: attempts 3 -> 1, native FP16/MSAA allocations
6 -> 2, subsequent frames reusing both attachment identities 0/2 -> 2/2.
Retained-failure-key control after a successful format change: attempts 2 -> 3,
native FP16 restored 0 -> 1 (the pre-fix branch makes 5 attempts across changes).
SSAO native post-depth coverage/source accepted 0/1 -> 1/1 (world grid 64x36).
Deferred smoke, BGRA8 and FP16: wrong pixels 2880/5184 -> 0/5184 each;
matched-size composition retains all 9216 source bytes in both versions.
Particle center NDC (-0.333333,+0.333333) -> (0,0); fractional offset viewport
(-0.339978,+0.355408) -> (0,0). Unrelated targets retain native constants.
Frozen pre-fix fragments are SHA-256 checked; no git history or private assets.
The suite discovers this driver alongside upscale_route_test (7/7 controls).
"""
from pathlib import Path
import hashlib
import os
import runpy
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
BOUNDS = {'begin': ('void render_float_scene_begin(void) {', 'void render_float_scene_final(int preserve) {'), 'geometry': ('    float vpz0, vpz1;', '    if (!(eb.have & EB_BIAS)'), 'coverage': ('id<MTLTexture> render_ssao_coverage(id<MTLTexture> depth, int allow_cuts) {', 'id<MTLTexture> render_ssao_cpu_reference_coverage'), 'composite': ('static void soft_late_composite(id<MTLTexture> scene) {', 'static MTLRenderPassDescriptor *soft_proof_pass'), 'particle': ('            particles_physical_constants(&mk, particle_view, dev.viewport);', '            if (soft_smoke_cost_enabled()')}

ORIGINAL = {
    'begin': ('b60c6e3b1b6ae7c717bb7c8071e4e30d43dd9b0c9d131160bbfc24b992e3536d', r'''void render_float_scene_begin(void) {
    if(fs_active || fs_serial==frame_serial)return;
    int floating=float_scene_on && !(diag_on && postfx_look_variant("floatoff"));
    if(!floating && !nfs_upscale_requested())return;
    // Split views keep native dimensions until their independent view lifetimes
    // can select a route. An unsupported device/allocation also stays native.
    const char *pads=getenv("NFS_PADS");
    int spatial=nfs_upscale_requested() && !(pads && !strcmp(pads,"2")) &&
        dev.rt[0]==dev.backbuffer && dev.ds==dev.autodepth &&
        bb_rt.tex.width==ad_rt.tex.width && bb_rt.tex.height==ad_rt.tex.height &&
        (bb_rt.ms?bb_rt.ms.sampleCount:1)==(ad_rt.ms?ad_rt.ms.sampleCount:1);
    MTLPixelFormat format=floating?MTLPixelFormatRGBA16Float:MTLPixelFormatBGRA8Unorm;
    NSUInteger samples=bb_rt.ms?bb_rt.ms.sampleCount:1;
    if(spatial && ![fs_upscale matchesWidth:bb_rt.tex.width height:bb_rt.tex.height format:format samples:samples]) {
        if(!fs_upscale)drop_ms_texture(&fs_world_rt);
        fs_world_rt=(RenderTex){0};
        fs_upscale=[[NFSUpscaleRoute alloc] initWithDevice:mtl width:bb_rt.tex.width height:bb_rt.tex.height format:format samples:samples];
        if(fs_upscale && ![fs_upscale prepareDepthSource:ad_rt.ms?:ad_rt.tex])fs_upscale=nil;
        if(diag_on)diag_event("spatial route: output %lux%lu world %lux%lu samples %lu allocated %lu fallback %d",
            (unsigned long)bb_rt.tex.width,(unsigned long)bb_rt.tex.height,
            (unsigned long)fs_upscale.worldColor.width,(unsigned long)fs_upscale.worldColor.height,
            (unsigned long)samples,(unsigned long)fs_upscale.allocatedBytes,fs_upscale==nil);
    }
    if(!spatial && fs_upscale){fs_upscale=nil;fs_world_rt=(RenderTex){0};}
    if(!floating && !fs_upscale)return;
    fs_headroom=floating?render_hdr_headroom():1;
    fs_hud=0;
    end_pass();
    if(fs_upscale) {
        fs_world_rt=(RenderTex){.tex=fs_upscale.worldColor,.ms=fs_upscale.worldMSAA};
        ensure_cmd();
        [fs_upscale encodeDepth:ad_rt.ms?:ad_rt.tex to:fs_upscale.worldDepthMSAA?:fs_upscale.worldDepth buffer:cmd];
        fs_native_depth=ad_rt;
        ad_rt=(RenderTex){.tex=fs_upscale.worldDepth,.ms=fs_upscale.worldDepthMSAA,.stale=fs_upscale.worldDepthMSAA!=nil};
        autodepth_tex=ad_rt.tex;
        if(!fs_screen_copies)fs_screen_copies=[NSMutableSet new];
        [fs_screen_copies removeAllObjects];
    }
    if(!fs_world_rt.tex) {
        MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA16Float width:bb_rt.tex.width height:bb_rt.tex.height mipmapped:NO];
        d.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;d.storageMode=MTLStorageModePrivate;
        fs_world_rt.tex=[mtl newTextureWithDescriptor:d];
        if(bb_rt.ms)fs_world_rt.ms=new_ms_texture(d.pixelFormat,d.width,d.height,(uint32_t)bb_rt.ms.sampleCount);
    }
    if(!fs_world_rt.tex || (bb_rt.ms && !fs_world_rt.ms))abort();
    msaa_resolve(&bb_rt);fs_display_rt=bb_rt;
    // The game's frame clear and pre-world FE pass have already run. Seed their
    // contents rather than dropping that clear or changing depth contents.
    id<MTLTexture> target=fs_world_rt.ms?fs_world_rt.ms:fs_world_rt.tex;
    MTLRegion r=MTLRegionMake2D(0,0,target.width,target.height);
    MTLRegion source=MTLRegionMake2D(0,0,bb_rt.tex.width,bb_rt.tex.height);
    blit_quad(bb_rt.tex,source,target,0,0,r,fs_upscale!=nil);if(fs_world_rt.ms)fs_world_rt.stale=1;
    bb_rt=fs_world_rt;backbuffer_tex=bb_rt.tex;
    fs_active=1;fs_serial=frame_serial;pass_dirty=1;
}
'''),
    'geometry': ('3b28b43de835694d4d7e5c9e379efcfd5ded2846ed09f30866725fef3835f160', r'''    float vpz0, vpz1;
    memcpy(&vpz0, &dev.viewport[4], 4); memcpy(&vpz1, &dev.viewport[5], 4);
    MTLViewport vp = {dev.viewport[0], dev.viewport[1], dev.viewport[2], dev.viewport[3], vpz0, vpz1};
    NSUInteger logical_w,logical_h;
    upscale_logical_size(dev.rt[0],rt0,&logical_w,&logical_h);
    NSUInteger physical_w=rt0.width,physical_h=rt0.height;
    // Coverage replay applies its own half-resolution transform below. Map
    // guest coordinates to the world first, so that transform runs exactly once.
    if(fs_upscale && fs_active && ssao_replay && !ssao_reference_record && dev.rt[0]==dev.backbuffer) {
        physical_w=fs_upscale.worldColor.width;physical_h=fs_upscale.worldColor.height;
    }
    vp=nfs_upscale_viewport(vp,logical_w,logical_h,physical_w,physical_h);
    MTLViewport physical_vp=vp;
    if(ssao_replay&&!ssao_reference_record){vp.originX*=.5;vp.originY*=.5;vp.width*=.5;vp.height*=.5;}
    if (!(eb.have & EB_VIEWPORT) || memcmp(&eb.vp, &vp, sizeof vp)) { [enc setViewport:vp]; eb.vp = vp; eb.have |= EB_VIEWPORT; }
    MTLScissorRect sc = {0, 0, rt0.width, rt0.height};
    if (rs[174]) {
        int32_t x0 = (int32_t)dev.scissor[0], y0 = (int32_t)dev.scissor[1], x1 = (int32_t)dev.scissor[2], y1 = (int32_t)dev.scissor[3];
        if(logical_w!=physical_w || logical_h!=physical_h) {
            x0=MAX(0,MIN(x0,(int32_t)logical_w));y0=MAX(0,MIN(y0,(int32_t)logical_h));
            x1=MAX(x0,MIN(x1,(int32_t)logical_w));y1=MAX(y0,MIN(y1,(int32_t)logical_h));
            MTLRegion mapped=nfs_upscale_region(MTLRegionMake2D(x0,y0,x1-x0,y1-y0),logical_w,logical_h,physical_w,physical_h);
            x0=(int32_t)mapped.origin.x;y0=(int32_t)mapped.origin.y;
            x1=x0+(int32_t)mapped.size.width;y1=y0+(int32_t)mapped.size.height;
        }
        if(ssao_replay&&!ssao_reference_record){x0=(int32_t)floor(x0*.5);y0=(int32_t)floor(y0*.5);x1=(int32_t)ceil(x1*.5);y1=(int32_t)ceil(y1*.5);}
        if (x0 < 0) x0 = 0;
        if (y0 < 0) y0 = 0;
        if (x1 > (int32_t)rt0.width) x1 = (int32_t)rt0.width;
        if (y1 > (int32_t)rt0.height) y1 = (int32_t)rt0.height;
        if (x1 <= x0 || y1 <= y0) return;
        sc = (MTLScissorRect){(NSUInteger)x0, (NSUInteger)y0, (NSUInteger)(x1 - x0), (NSUInteger)(y1 - y0)};
    }
    if (!(eb.have & EB_SCISSOR) || memcmp(&eb.sc, &sc, sizeof sc)) { [enc setScissorRect:sc]; eb.sc = sc; eb.have |= EB_SCISSOR; }
'''),
    'coverage': ('3ddab3c81dfb70906b2cdf313376839174fb891e2042bbe075ec2a47a4e4fe04', r'''id<MTLTexture> render_ssao_coverage(id<MTLTexture> depth, int allow_cuts) {
    if (!x360_post_ssao_enabled() || !ssao_mask.tex || ssao_serial!=frame_serial || ssao_depth!=depth) return nil;
    uint64_t t0=ssao_cpu_diag?host_ticks():0;
    if (!ssao_depth_prepare(depth)) return nil;
    if (ssao_cleared!=frame_serial || rt_render_list_ops(ssao_rec)) {
        ssao_open(ssao_half_depth,allow_cuts);rt_render_list_replay(ssao_rec,enc);end_pass();memset(&ssao_eb,0,sizeof ssao_eb);
    }
    if(ssao_proof()&&ssao_reference_rec){ssao_reference_record=1;ssao_open(depth,0);lamprec_replay(ssao_reference_rec,enc);end_pass();ssao_reference_record=0;memset(&ssao_reference_eb,0,sizeof ssao_reference_eb);}
    if(ssao_proof()&&ssao_cpu_reference_rec){ssao_cpu_reference_record=1;ssao_open(ssao_half_depth,0);lamprec_replay(ssao_cpu_reference_rec,enc);end_pass();ssao_cpu_reference_record=0;memset(&ssao_cpu_reference_eb,0,sizeof ssao_cpu_reference_eb);}
    if(ssao_cpu_diag)ssao_diag_replay_ns+=host_ticks()-t0;
    return ssao_mask.tex;
}
'''),
    'composite': ('0548f0dc29fb8d893ebd9b0a8e0bc74a702ffd42f8f43f7be2f4761a76033e43', r'''static void soft_late_composite(id<MTLTexture> scene) {
    static id<MTLRenderPipelineState> states[2];unsigned slot=scene.pixelFormat==MTLPixelFormatRGBA16Float;
    id<MTLRenderPipelineState> pso=states[slot];
    if(!pso){
        NSString *s=[@"#define SMOKE_MSAA 0\n" stringByAppendingString:[NSString stringWithUTF8String:soft_copy_msl]];
        id<MTLLibrary> l=[mtl newLibraryWithSource:s options:nil error:nil];MTLRenderPipelineDescriptor *pd=[MTLRenderPipelineDescriptor new];
        pd.vertexFunction=[l newFunctionWithName:@"copy_vs"];pd.fragmentFunction=[l newFunctionWithName:@"copy_fs"];
        MTLRenderPipelineColorAttachmentDescriptor *a=pd.colorAttachments[0];a.pixelFormat=scene.pixelFormat;a.writeMask=MTLColorWriteMaskRed|MTLColorWriteMaskGreen|MTLColorWriteMaskBlue;
        a.blendingEnabled=YES;a.sourceRGBBlendFactor=MTLBlendFactorOne;a.destinationRGBBlendFactor=MTLBlendFactorOneMinusSourceAlpha;
        pso=states[slot]=pipeline_build(pd, __func__, NULL);if(!pso){fprintf(stderr,"soft smoke composite pipeline failed\n");abort();}
    }
    MTLRenderPassDescriptor *rp=[MTLRenderPassDescriptor renderPassDescriptor];rp.colorAttachments[0].texture=scene;
    rp.colorAttachments[0].loadAction=MTLLoadActionLoad;rp.colorAttachments[0].storeAction=MTLStoreActionStore;
    if(gpt_on)gpt_render_auto(rp,"soft smoke composite 1x");
    id<MTLRenderCommandEncoder> e=[cmd renderCommandEncoderWithDescriptor:rp];if(gpt_on)gpt_name(e);
    [e setRenderPipelineState:pso];[e setFragmentTexture:soft_late_layer atIndex:0];[e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[e endEncoding];
}
'''),
    'particle': ('ea8ab02bc7d4463566fd90e49d92b5ff780b3707c85cb6b59c0ec9133dea5662', r'''            particles_physical_constants(&mk, particle_view, dev.viewport);
'''),
}

INSTRUMENT = r'''
static int pads_lookups,viewport_calls;
static char *counted_getenv(const char *name){if(!strcmp(name,"NFS_PADS"))++pads_lookups;return getenv(name);}
static MTLViewport counted_viewport(MTLViewport v,NSUInteger lw,NSUInteger lh,NSUInteger pw,NSUInteger ph){
 ++viewport_calls;return nfs_upscale_viewport(v,lw,lh,pw,ph);
}
#define getenv counted_getenv
#define nfs_upscale_viewport counted_viewport
'''

EXTRA = r'''
static RenderTex ssao_mask;
static uint64_t ssao_serial,ssao_cleared;
static id<MTLTexture> ssao_depth,ssao_half_depth,prepared_depth;
static id ssao_rec,ssao_reference_rec,ssao_cpu_reference_rec;
static int ssao_cpu_diag,ssao_cpu_reference_record;
static uint64_t ssao_diag_replay_ns;
static char ssao_eb[8],ssao_reference_eb[8],ssao_cpu_reference_eb[8];
static id<MTLRenderCommandEncoder> enc;
static int x360_post_ssao_enabled(void){return 1;}
static int ssao_proof(void){return 0;}
static int ssao_depth_prepare(id<MTLTexture> z){prepared_depth=z;return 1;}
static void ssao_open(id<MTLTexture> z,int cuts){(void)z;(void)cuts;ssao_cleared=frame_serial;}
static void lamprec_replay(id rec,id encoder){(void)rec;(void)encoder;}
COVERAGE
static id<MTLTexture> soft_late_layer;
COPY_MSL
COMPOSITE

// Use the real particle inverse, viewport constants and scene setup. The fixture
// supplies an identity camera and fixed scene illumination, without guest assets.
#undef M32
#undef MF32
#define M32(a) (*(uint32_t *)(G+(uint32_t)(a)))
#define MF32(a) (*(float *)(G+(uint32_t)(a)))
uint8_t *G;
void lighting_particle_scene(float *a,float *s,float *d){
 for(int i=0;i<3;i++){a[i]=.2f;s[i]=.5f;d[i]=i==2;}
}
#include "particles_physical.c"
static MatConsts draw_particle_constants(MTLViewport physical_vp,NSUInteger logical_w,NSUInteger logical_h,NSUInteger physical_w,NSUInteger physical_h){
 (void)physical_vp;(void)logical_w;(void)logical_h;(void)physical_w;(void)physical_h;
 MatConsts mk={0};unsigned particle_view=1;
PARTICLE
 return mk;
}

static int off_check(void){
 float_scene_on=1;setup(96,54,4);pads_lookups=viewport_calls=0;
 GeometryProbe *probe=[GeometryProbe new];
 dev.viewport[0]=3;dev.viewport[1]=5;dev.viewport[2]=81;dev.viewport[3]=42;dev.viewport[5]=0x3f800000;
 for(int frame=0;frame<3;frame++){
  cmd=rt_command_buffer();clear_native();render_float_scene_begin();
  memset(&eb,0,sizeof eb);configure_geometry((id)probe,bb_rt.ms?:bb_rt.tex);
  CHECK(probe.viewport.originX==3 && probe.viewport.originY==5 && probe.viewport.width==81 && probe.viewport.height==42,"Off viewport unchanged");
  render_float_scene_final(1);finish();render_float_scene_present_end();++frame_serial;
 }
 printf("Off frames=3 viewport_calls=%d pads_lookups=%d\n",viewport_calls,pads_lookups);
 CHECK(!viewport_calls,"Off no viewport scaling calls");CHECK(!pads_lookups,"Off no environment lookups");
 return failures!=0;
}
static int unsupported_calls,native_allocations;
static IMP real_texture;
static BOOL reject_device(id self,SEL selector,id device){(void)self;(void)selector;(void)device;++unsupported_calls;return NO;}
static id count_texture(id self,SEL selector,MTLTextureDescriptor *d){
 if(d.pixelFormat==MTLPixelFormatRGBA16Float && d.width==96 && d.height==54)++native_allocations;
 return ((id(*)(id,SEL,id))real_texture)(self,selector,d);
}
static int fallback_check(void){
 float_scene_on=1;setup(96,54,4);
 Method support=class_getClassMethod(NFSSpatialCache.class,@selector(supportsDevice:));
 IMP original=method_setImplementation(support,(IMP)reject_device);
 Method allocation=class_getInstanceMethod(object_getClass(mtl),@selector(newTextureWithDescriptor:));
 real_texture=method_setImplementation(allocation,(IMP)count_texture);
 id<MTLTexture> first=nil,firstMS=nil;int reused=0;
 for(int frame=0;frame<3;frame++){
  cmd=rt_command_buffer();clear_native();render_float_scene_begin();
  CHECK(!fs_upscale && fs_active && bb_rt.tex.width==96,"unsupported native float path");
  if(!frame){first=bb_rt.tex;firstMS=bb_rt.ms;}
  else reused+=bb_rt.tex==first && bb_rt.ms==firstMS;
  render_float_scene_final(1);finish();render_float_scene_present_end();++frame_serial;
 }
 printf("Fallback frames=3 attempts=%d native_allocations=%d reused=%d/2\n",unsupported_calls,native_allocations,reused);
 CHECK(unsupported_calls==1,"unsupported route attempted once");CHECK(native_allocations==2,"native FP16/MSAA allocated once");CHECK(reused==2,"native FP16/MSAA identities stable");
 // A resize changes the cache key and permits one new attempt.
 setup(99,57,4);cmd=rt_command_buffer();clear_native();render_float_scene_begin();
 CHECK(unsupported_calls==2,"resize invalidates failed route key");render_float_scene_final(1);finish();
 // A successful UNORM route must retire the old failed FP16 configuration.
 // Otherwise returning to FP16 could retain the incompatible UNORM route.
 method_setImplementation(support,original);
 if([NFSSpatialCache supportsDevice:mtl]) {
 float_scene_on=0;++frame_serial;
 cmd=rt_command_buffer();clear_native();render_float_scene_begin();
 CHECK(fs_upscale && bb_rt.tex.pixelFormat==MTLPixelFormatBGRA8Unorm,"format change selects successful route");
 render_float_scene_final(0);finish();
 method_setImplementation(support,(IMP)reject_device);float_scene_on=1;++frame_serial;
 cmd=rt_command_buffer();clear_native();render_float_scene_begin();
 CHECK(!fs_upscale && bb_rt.tex.pixelFormat==MTLPixelFormatRGBA16Float,"returning format restores native float fallback");
 printf("Fallback resize_and_format_attempts=%d native_float=%d\n",unsupported_calls,bb_rt.tex.pixelFormat==MTLPixelFormatRGBA16Float);
 CHECK(unsupported_calls==3,"failed route key invalidated after successful format change");
 render_float_scene_final(0);finish();
 }
 method_setImplementation(allocation,real_texture);method_setImplementation(support,original);
 return failures!=0;
}
static int coverage_check(void){
 setup(96,54,4);cmd=rt_command_buffer();clear_native();render_float_scene_begin();
 CHECK(fs_upscale && fs_active,"coverage route selected");
 id<MTLTexture> world=ad_rt.tex;
 ssao_depth=world;ssao_serial=frame_serial;ssao_mask.tex=texture(32,18,MTLPixelFormatR8Unorm,1);
 render_float_scene_final(0);
 id<MTLTexture> mask=render_ssao_coverage(ad_rt.tex,0);
 printf("Coverage returned=%d world_source=%d world=%lux%lu native=%lux%lu\n",mask!=nil,prepared_depth==world,(unsigned long)world.width,(unsigned long)world.height,(unsigned long)ad_rt.tex.width,(unsigned long)ad_rt.tex.height);
 CHECK(mask==ssao_mask.tex,"native post depth accepts world coverage");
 CHECK(prepared_depth==world,"coverage downsamples matching world depth");
 CHECK(!render_ssao_coverage(texture(96,54,MTLPixelFormatDepth32Float_Stencil8,1),0),"unrelated depth rejected");
 ++frame_serial;CHECK(!render_ssao_coverage(ad_rt.tex,0),"stale coverage rejected");
 finish();return failures!=0;
}
static void clear_texture(id<MTLTexture> t,MTLClearColor color){
 MTLRenderPassDescriptor *rp=[MTLRenderPassDescriptor renderPassDescriptor];rp.colorAttachments[0].texture=t;
 rp.colorAttachments[0].loadAction=MTLLoadActionClear;rp.colorAttachments[0].clearColor=color;rp.colorAttachments[0].storeAction=MTLStoreActionStore;
 [[cmd renderCommandEncoderWithDescriptor:rp] endEncoding];
}
static int smoke_check(void){
 for(int floating=0;floating<2;floating++){
  cmd=rt_command_buffer();soft_late_layer=texture(64,36,MTLPixelFormatBGRA8Unorm,1);
  id<MTLTexture> scene=texture(96,54,floating?MTLPixelFormatRGBA16Float:MTLPixelFormatBGRA8Unorm,1);
  clear_texture(soft_late_layer,MTLClearColorMake(.5,0,0,.5));clear_texture(scene,MTLClearColorMake(0,0,1,1));
  soft_late_composite(scene);id<MTLBuffer> result=snapshot(scene);finish();
  NSUInteger bad=0,pitch=(96*(floating?8:4)+255)&~255ul;
  for(NSUInteger y=0;y<54;y++)for(NSUInteger x=0;x<96;x++){
   float r,b,a;
   if(floating){const __fp16 *p=(const __fp16 *)((const char *)result.contents+y*pitch+x*8);r=p[0];b=p[2];a=p[3];}
   else{const uint8_t *p=(const uint8_t *)result.contents+y*pitch+x*4;r=p[2]/255.f;b=p[0]/255.f;a=p[3]/255.f;}
   bad+=fabs(r-.5)>.01 || fabs(b-.5)>.01 || a!=1;
  }
  printf("Smoke format=%s scene=96x54 layer=64x36 bad=%lu/5184\n",floating?"FP16":"BGRA8",(unsigned long)bad);
  CHECK(!bad,"smoke covers native scene with premultiplied blend and intact alpha");
 }
 // The unchanged pixel-addressed path must remain exact at matching sizes.
 cmd=rt_command_buffer();soft_late_layer=texture(64,36,MTLPixelFormatBGRA8Unorm,1);
 id<MTLTexture> native=texture(64,36,MTLPixelFormatBGRA8Unorm,1);
 draw(soft_late_layer,nil,0);clear_texture(native,MTLClearColorMake(0,0,0,1));
 soft_late_composite(native);
 id<MTLBuffer> expected=snapshot(soft_late_layer),actual=snapshot(native);finish();
 NSUInteger native_bad=0;
 for(NSUInteger i=0;i<9216;i++)native_bad+=((const uint8_t *)expected.contents)[i]!=((const uint8_t *)actual.contents)[i];
 printf("Smoke native identity bad_bytes=%lu/9216\n",(unsigned long)native_bad);
 CHECK(!native_bad,"matched-size smoke composition byte-identical");
 return failures!=0;
}
static int particle_check(void){
 setup(384,216,1);cmd=rt_command_buffer();clear_native();render_float_scene_begin();
 CHECK(fs_upscale && fs_active,"particle route selected");
 G=calloc(0xa00000,1);M32(0x009195E0+0x70)=0x2000;for(int i=0;i<4;i++)MF32(0x2080+20*i)=1;
 particles_physical_set(1);GeometryProbe *probe=[GeometryProbe new];
 int bad=0;
 for(int offset=0;offset<2;offset++){
  dev.viewport[0]=offset?3:0;dev.viewport[1]=offset?5:0;dev.viewport[2]=offset?301:384;dev.viewport[3]=offset?151:216;dev.viewport[5]=0x3f800000;
  memset(&eb,0,sizeof eb);configure_geometry((id)probe,bb_rt.tex);MTLViewport v=probe.viewport;
  MatConsts k=draw_particle_constants(v,384,216,256,144);
  float center[2]={(float)(v.originX+v.width*.5),(float)(v.originY+v.height*.5)};
  float x=center[0]*k.k[3][0]+k.k[11][0],y=center[1]*k.k[3][1]+k.k[11][1];
  printf("Particle offset=%d center_ndc=%.6f,%.6f\n",offset,x,y);
  bad+=fabs(x)>1e-6 || fabs(y)>1e-6;
 }
 CHECK(!bad,"particle physical screen center reconstructs NDC zero");
 // Independent reflection targets retain the guest viewport constants.
 MatConsts native=draw_particle_constants((MTLViewport){3,5,301,151,0,1},64,64,64,64);
 CHECK(fabs(native.k[3][0]-2.f/301)<1e-7 && fabs(native.k[11][0]-(-1-3*2.f/301))<1e-7,"independent viewport constants unchanged");
 free(G);G=NULL;render_float_scene_final(0);finish();return failures!=0;
}
int main(int argc,const char **argv){@autoreleasepool{
 if(argc!=2)return 2;
 mtl=MTLCreateSystemDefaultDevice();if(!mtl){puts("GPU PENDING: Metal unavailable");return 3;}
 queue=[mtl newCommandQueue];init_blit();rt_init();
 if(!strcmp(argv[1],"off"))return off_check();
 if(!strcmp(argv[1],"fallback"))return fallback_check();
 if(!strcmp(argv[1],"smoke"))return smoke_check();
 if(![NFSSpatialCache supportsDevice:mtl]){puts("GPU PENDING: MetalFX unavailable");return 3;}
 if(!strcmp(argv[1],"coverage"))return coverage_check();
 if(!strcmp(argv[1],"particle"))return particle_check();
 return 2;
}}
'''


def section(source, start, end):
    return source[source.index(start):source.index(end, source.index(start))]


def checked(args, **kwargs):
    result = subprocess.run(list(map(str, args)), capture_output=True, text=True, timeout=180, **kwargs)
    if result.returncode:
        raise RuntimeError(f'exit {result.returncode}\n{result.stdout[-2000:]}{result.stderr[-5000:]}')
    return result


def main():
    route = runpy.run_path(str(ROOT / 'upscale_route_test.py'))
    source = (ROOT / 'render.m').read_text()
    for digest, fragment in ORIGINAL.values():
        assert hashlib.sha256(fragment.encode()).hexdigest() == digest
    helpers = route['MAIN'].split('int main(int argc,const char **argv)')[0]
    def harness(render):
        extra = EXTRA.replace('COVERAGE', section(render, *BOUNDS['coverage']))
        extra = extra.replace('COPY_MSL', section(render, 'static const char soft_copy_msl[] =', 'static id<MTLTexture> soft_late_layer;'))
        extra = extra.replace('COMPOSITE', section(render, *BOUNDS['composite']))
        extra = extra.replace('PARTICLE', section(render, *BOUNDS['particle']))
        value = route['make_harness'](render, helpers + extra)
        return value.replace('static int failures,gpt_on,', INSTRUMENT + '\nstatic int failures,gpt_on,')
    env = dict(os.environ, NFS_UPSCALE='spatial', NFS_PADS='1')
    reports = []
    with tempfile.TemporaryDirectory(prefix='upscale-review-') as directory:
        tmp = Path(directory)
        fixture = tmp / 'fixture.m'
        executable = tmp / 'fixture'
        build = ['clang', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function', '-Wno-unused-variable',
                 '-Wno-incompatible-pointer-types', '-fobjc-arc', '-I', ROOT, '-I', ROOT.parent / 'recomp', fixture,
                 ROOT / 'render_upscale_route.m', ROOT / 'render_metalfx_spatial.m',
                 '-framework', 'Foundation', '-framework', 'Metal', '-weak_framework', 'MetalFX', '-o', executable]
        fixture.write_text(harness(source))
        checked(build)
        for name in ('off', 'fallback', 'coverage', 'smoke', 'particle'):
            result = subprocess.run([str(executable), name], capture_output=True, text=True,
                                    env=dict(env, NFS_UPSCALE='off') if name == 'off' else env, timeout=120)
            if result.returncode == 3:
                print(result.stdout, end='')
                raise SystemExit(3)
            assert result.returncode == 0, name + result.stdout + result.stderr
            reports.append('FIXED ' + result.stdout)
            print('FIXED ' + result.stdout, end='', flush=True)
        for name, keys, failure in (
            ('off', ('begin', 'geometry'), 'Off no viewport scaling calls'),
            ('fallback', ('begin',), 'unsupported route attempted once'),
            ('coverage', ('coverage',), 'native post depth accepts world coverage'),
            ('smoke', ('composite',), 'smoke covers native scene'),
            ('particle', ('particle',), 'particle physical screen center'),
        ):
            old = source
            for key in keys:
                start, end = BOUNDS[key]
                old = old.replace(section(old, start, end), ORIGINAL[key][1])
            fixture.write_text(harness(old))
            checked(build)
            result = subprocess.run([str(executable), name], capture_output=True, text=True,
                                    env=dict(env, NFS_UPSCALE='off') if name == 'off' else env, timeout=120)
            assert result.returncode == 1 and failure in result.stderr, name + result.stdout + result.stderr
            reports.append('ORIGINAL ' + result.stdout + result.stderr)
            print('ORIGINAL ' + result.stdout, end='', flush=True)
        retained = source.replace('if(fs_upscale){failed_device=nil;drop_ms_texture(&fs_world_rt);',
                                  'if(fs_upscale){drop_ms_texture(&fs_world_rt);')
        assert retained != source
        fixture.write_text(harness(retained))
        checked(build)
        result = subprocess.run([str(executable), 'fallback'], capture_output=True, text=True,
                                env=env, timeout=120)
        assert result.returncode == 1 and 'returning format restores native float fallback' in result.stderr, result.stdout + result.stderr
        reports.append('RETAINED FAILURE KEY ' + result.stdout + result.stderr)
        print('RETAINED FAILURE KEY ' + result.stdout, end='', flush=True)
    report = Path(os.environ.get('NFS_METAL_CACHE', ROOT.parent / '.metal-cache')) / 'upscale-review-test.log'
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(''.join(reports))
    print('upscale_review_test: PASS five regressions; 5/5 pre-fix controls and retained-failure-key control rejected')


if __name__ == '__main__':
    main()
