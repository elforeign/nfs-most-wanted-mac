// Production presenter/quad proof, no game or window.
// Auto potential 1: SDR; 7: HDR startup/status (0 failures); old request: 1 failure.
// Expected linear readback:
// EDR [0, .21398926 (FP16 .214041), 1, 2, 4], above_one=2, lost=0.
// Broken BGRA8 intermediate [0, .21569824, 1, 1, 1], above_one=0, lost=2 (exit 1).
// Direct BGRA8 negative control bytes [0,55,255,255,255], lost=2.
// SDR baseline: 2 ramps x 256x4 pixels x 4 bytes = 8192 identical bytes.
// M expected: 1025-point [0,8] ramp, H=4: above_one=896, peak=4, HUD=1;
// midpoint .5 -> .214041140482 (FP16 .21398926). Both 1x/4x MSAA; bloom/DOF on/off.
// Continuous gamma exponents .8/1/1.2 keep HUD=1; gain-two HUD export clamps to 1.
// Off/H=1 BGRA8 match 8192 bytes each; H=1 drawable sRGB roundtrip matches 8192.
// Broken HUD HDR scaling / omitted source bound: HUD=4, exit 1 (normal HUD=1/exit 0).
// Broken H=1 missing LUT: graded mismatches=2016, exit 1 (normal mismatches=0).
                                                                                   
// Observed CPU: 5 unset mode/layer checks PASS (0); forced-On control fails (1).
// Observed CPU: 9 power exponents + midpoint/ceiling PASS; constant-power control fails (1).
// Frozen legacy post source: 13018 identical bytes / SHA-256 verified before GPU discovery.
// Lifecycle CPU observed: H=1/4/1 x32, 96 samples + 64 stable-frame checks,
// stale=0; frozen H fails 1 vs 4, live getter fails 4 vs 1 (exit 1 each).
// Native restart sequence expected: On/Off/On x8 = 24/24 matching source/layer/
// drawable formats; forced stale BGRA8 layer fails 80 vs expected 115 (exit 1).
// Capture H=1/Off expected 8192 identical bytes each; omitted capture LUT fails
// with 2016 differing bytes (exit 1). Native observations PENDING host GPU check.
// Native probe: 20/20 converted bytes + 4/4 unchanged SDR bytes; zeroed readback
// control differs in 17/20 converted bytes (exit 1); native observations pending.
#import <Cocoa/Cocoa.h>
#import <Metal/Metal.h>
#import <QuartzCore/CAMetalLayer.h>
#include <math.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "hdr_output.h"

static id<MTLDevice> mtl;
static unsigned pipeline_edr_builds;
static int pipeline_hold_test;
static id<MTLRenderPipelineState> pipeline_build(MTLRenderPipelineDescriptor *d, const char *pass, NSError **e) {
    if (!strcmp(pass,"edr")) ++pipeline_edr_builds;
    return [mtl newRenderPipelineStateWithDescriptor:d error:e];
}
static id<MTLCommandQueue> queue;
static id<MTLCommandBuffer> cmd;
static CAMetalLayer *layer;
static id<MTLTexture> backbuffer_tex, present_tex;
@interface HDRTestScreen : NSObject
@property CGFloat maximumExtendedDynamicRangeColorComponentValue;
@property CGFloat maximumPotentialExtendedDynamicRangeColorComponentValue;
@property CGFloat maximumReferenceExtendedDynamicRangeColorComponentValue;
@end
@implementation HDRTestScreen
@end
@interface HDRTestWindow : NSObject
@property(nonatomic,strong) NSScreen *screen;
@end
@implementation HDRTestWindow
@end
@interface HDRTestView : NSView <CALayerDelegate>
@property(nonatomic,strong) NSWindow *testWindow;
@end
@implementation HDRTestView
- (NSWindow *)window { return self.testWindow; }
@end
static HDRTestScreen *test_screen;
static HDRTestView *test_view;
static int diag_on=1, gpt_on, float_scene_on;
static id<MTLLibrary> blit_lib;
static id<MTLRenderPipelineState> blit_pso;
static id<MTLSamplerState> blit_linear, blit_point;
static dispatch_queue_t present_q;
static _Atomic int presenting;
#define gpt_render_auto(...) ((void)0)
#define gpt_name(...) ((void)0)
#define gpt_blit(cb, ...) [cb blitCommandEncoder]
static int user32_window_occluded(void) { return 0; }
static void brightness_init(void) {}
static uint32_t mipdim(uint32_t x, uint32_t l) { return MAX(x>>l,1u); }
static void diag_event(const char *fmt, ...) {
    va_list ap; va_start(ap,fmt); vfprintf(stderr,fmt,ap); va_end(ap); fputc('\n',stderr);
}
CGColorSpaceRef srgb_colorspace(void) {
    static CGColorSpaceRef cs; if(!cs)cs=CGColorSpaceCreateWithName(kCGColorSpaceSRGB); return cs;
}
static float gamma_lut[768];
static int x_edr_processing;
static float render_hdr_composition_headroom(void);
static void ensure_cmd(void) {}
static int gamma_active, present_graded;
#include "presenter.inc"
#include "probe.inc"
#include "quad.inc"
#include "legacy.inc"
typedef struct { id<MTLTexture> tex,ms; int stale; } RenderTex;
static RenderTex bb_rt,fs_world_rt,fs_display_rt,fs_composite_rt;
static int fs_active,fs_composing,fs_hud,fs_toned;
static float fs_headroom=1;
static uint64_t frame_serial=1,fs_serial;
static void end_pass(void) {}
static int pass_dirty;
static struct {uint32_t backbuffer;} dev={1};
static int postfx_look_variant(const char *name){(void)name;return 0;}
static id<MTLTexture> new_ms_texture(MTLPixelFormat pf,NSUInteger w,NSUInteger h,uint32_t samples) {
    MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:pf width:w height:h mipmapped:NO];
    d.textureType=MTLTextureType2DMultisample;d.sampleCount=samples;d.storageMode=MTLStorageModePrivate;d.usage=MTLTextureUsageRenderTarget;
    return [mtl newTextureWithDescriptor:d];
}
static void msaa_resolve(RenderTex *rt) {
    if(!rt->ms || !rt->stale)return;
    MTLRenderPassDescriptor *p=[MTLRenderPassDescriptor renderPassDescriptor];
    p.colorAttachments[0].texture=rt->ms;p.colorAttachments[0].resolveTexture=rt->tex;
    p.colorAttachments[0].loadAction=MTLLoadActionLoad;p.colorAttachments[0].storeAction=MTLStoreActionStoreAndMultisampleResolve;
    id<MTLRenderCommandEncoder> e=[cmd renderCommandEncoderWithDescriptor:p];[e endEncoding];rt->stale=0;
}
static void blit_quad(id<MTLTexture> s,MTLRegion sr,id<MTLTexture> d,NSUInteger l,NSUInteger sl,MTLRegion dr,int linear) {
    quad_into(cmd,s,sr,d,l,sl,dr,linear);
}
#include "routing.inc"
static void frame_boundary_test(void) {
#include "frame_end.inc"
}
enum {HC_STAGES=25,HC_DOWN=5};
static int hdrclip_on;
#include "guard.inc"
#include "gen/x360_shader_lits.inc"
#define HDRCLIP_ACTIVE 0
#define hdrclip_shader(s) (s)
static void hdrclip_bind(id<MTLRenderCommandEncoder> e,unsigned n){(void)e;(void)n;}
static void hdrclip_scan(id<MTLTexture> t,unsigned n){(void)t;(void)n;}
static void gpt_render(MTLRenderPassDescriptor *p,const char *s,...){(void)p;(void)s;}
#include "post.inc"
static const char *hud_test_src=
"#include <metal_stdlib>\nusing namespace metal;\nvertex float4 hud_vs(uint v [[vertex_id]]){float2 p=float2((v<<1)&2,v&2);return float4(p*float2(2,-2)+float2(-1,1),0,1);}\nfragment float4 hud_fs(float4 p [[position]]){return float4(2,2,2,1);}";

#define CHECK(c, ...) do { if(!(c)){fprintf(stderr,"FAIL: " __VA_ARGS__);fputc('\n',stderr);return 1;} }while(0)
static id<MTLTexture> texture(MTLPixelFormat pf, NSUInteger w, NSUInteger h) {
    MTLTextureDescriptor *td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:pf width:w height:h mipmapped:NO];
    td.usage=MTLTextureUsageShaderRead|MTLTextureUsageRenderTarget;
    td.storageMode=MTLStorageModeShared;
    return [mtl newTextureWithDescriptor:td];
}
static int finish(void) {
    [cmd commit];[cmd waitUntilCompleted];
    CHECK(cmd.status==MTLCommandBufferStatusCompleted && !cmd.error,"command buffer: %s",cmd.error.localizedDescription.UTF8String);
    cmd=nil;presenting=0;return 0;
}
static int sources(const char *directory) {
    const char *names[]={"hdr","gamma","blit","post","post_edr","hud"};
    NSString *edr=x_edr_source([NSString stringWithUTF8String:x360_src]);
    float_scene_on=1;NSString *hud=hdrclip_world_source([NSString stringWithUTF8String:hud_test_src],1,0,0);float_scene_on=0;
    const char *sources[]={hdr_output_src,gamma_src,blit_src,x360_src,edr.UTF8String,hud.UTF8String};
    for(int i=0;i<6;i++) {
        char path[1024];snprintf(path,sizeof path,"%s/%s.metal",directory,names[i]);
        FILE *f=fopen(path,"w");CHECK(f,"shader source file");fputs(sources[i],f);fclose(f);
    }
    return 0;
}
static void fixture_screen(float h);
static int cpu(void) {
    CHECK(!getenv("NFS_HDR_OUTPUT"),"CPU switch must be unset");
    layer=[CAMetalLayer layer];
    fixture_screen(1);
    test_screen.maximumPotentialExtendedDynamicRangeColorComponentValue=1;
    MTLPixelFormat pf=layer.pixelFormat;
    CGColorSpaceRef cs=layer.colorspace;
    BOOL edr=layer.wantsExtendedDynamicRangeContent;
    render_hdr_output_init();
    CHECK(!hdr_output_requested && !hdr_output_on,"unset switch changed mode");
    CHECK(layer.pixelFormat==pf && layer.colorspace==cs && layer.wantsExtendedDynamicRangeContent==edr,"unset switch changed layer");
    CHECK(!strcmp(render_hdr_output_status(),"Auto: SDR (no HDR at launch; restart to re-check)"),"Auto SDR reason");
    puts("hdr_output_test: Auto potential 1 SDR; status reason PASS");
    const double exponents[]={.8,1,1.2};
    for(int p=0;p<3;++p) {
        uint16_t ramp[768];
        for(int i=0;i<768;++i)ramp[i]=(uint16_t)lround(65535*pow((i&255)/255.,exponents[p]));
        render_set_gamma(ramp);
        for(unsigned c=0;c<3;++c)CHECK(fabs(hdr_brightness_exponent(c)-exponents[p])<.00004,"continuous brightness exponent");
    }
    double mid=pow((.5+.055)/1.055,2.4);
    CHECK(fabs(mid-.21404114048223255)<1e-14,"CPU sRGB midpoint");
    CHECK(hdr_output_ceiling(1)==1 && hdr_output_ceiling(NAN)==1 && hdr_output_ceiling(INFINITY)==1,"invalid/SDR headroom");
    CHECK(hdr_output_ceiling(1.1f)<=1.1f && hdr_output_ceiling(1.1f)>1,"fractional FP16 ceiling");
    printf("hdr_output_test: CPU brightness 9 exponents PASS midpoint=%.12f\n",mid);return 0;
}
static double encode(double x);
static void fixture_screen(float h) {
    test_screen=[HDRTestScreen new];test_screen.maximumExtendedDynamicRangeColorComponentValue=h;
    HDRTestWindow *window=[HDRTestWindow new];window.screen=(NSScreen *)test_screen;
    if(!test_view)test_view=[HDRTestView new];
    test_view.testWindow=(NSWindow *)window;layer.delegate=test_view;
}
// Auto startup: 0 failures; old explicit-On-only request: 1 failure (exit 1).
static int auto_hdr(void) {
    test_screen.maximumPotentialExtendedDynamicRangeColorComponentValue=7;
    render_hdr_output_init();
    CHECK(hdr_output_requested && hdr_output_on && layer.pixelFormat==MTLPixelFormatRGBA16Float &&
          layer.wantsExtendedDynamicRangeContent && !strncmp(render_hdr_output_status(),"Auto: ",6),
          "Auto potential 7 startup must enable HDR and explain why");
    puts("hdr_output_test: Auto potential 7 startup HDR; 0 failures PASS");return 0;
}
static int lifecycle_cpu(void) {
    setenv("NFS_HDR_OUTPUT","edr",1);
    layer=[CAMetalLayer layer];layer.pixelFormat=MTLPixelFormatRGBA16Float;
    hdr_output_requested=hdr_output_on=1;fixture_screen(1);
    unsigned samples=0,stable=0;
    for(int n=0;n<32;++n) {
        // H=1 -> 4 on the same screen, then move to a different H=1 screen.
        // A changed screen or headroom cannot alter the frame already in flight.
        frame_boundary_test();CHECK(render_hdr_headroom()==1,"initial H=1");++samples;
        test_screen.maximumExtendedDynamicRangeColorComponentValue=4;
        CHECK(render_hdr_headroom()==1,"headroom changed inside frame");++stable;
        frame_boundary_test();CHECK(render_hdr_headroom()==4,"H=4 boundary sample");++samples;
        fixture_screen(1);
        CHECK(render_hdr_headroom()==4,"screen move changed inside frame");++stable;
        frame_boundary_test();CHECK(render_hdr_headroom()==1,"H=1 boundary fallback");++samples;
        CHECK(layer.pixelFormat==MTLPixelFormatRGBA16Float,"stale lifecycle format");
    }
    const float unavailable[]={0,NAN,INFINITY,-1};
    for(unsigned i=0;i<sizeof unavailable/sizeof *unavailable;++i) {
        test_screen.maximumExtendedDynamicRangeColorComponentValue=unavailable[i];
        frame_boundary_test();CHECK(render_hdr_headroom()==1,"wake/unavailable fallback");
    }
    CHECK(!strcmp(render_hdr_output_status(),"SDR fallback"),"fallback status");
    test_screen.maximumExtendedDynamicRangeColorComponentValue=4;hdr_output_frame_boundary();
    CHECK(!strcmp(render_hdr_output_status(),"EDR active"),"active status");
    hdr_output_on=hdr_output_requested=0;
    CHECK(render_hdr_headroom()==1 && !strcmp(render_hdr_output_status(),"Off"),"Off status/headroom");
    printf("hdr_output_test: CPU lifecycle H=1/4/1 samples=%u/96 stable=%u/64 stale_formats=0 unavailable=4 PASS\n",samples,stable);
    return 0;
}
static int startup(void) {
    int on=nfs_hdr_output_requested();
    const char *pads=getenv("NFS_PADS");int split=pads && !strcmp(pads,"2");
    MTLPixelFormat expected=on && !split ? MTLPixelFormatRGBA16Float : MTLPixelFormatBGRA8Unorm;
    render_hdr_output_init();
    CHECK(layer.pixelFormat==expected,"startup layer format %lu expected %lu",(unsigned long)layer.pixelFormat,(unsigned long)expected);
    CHECK(hdr_output_on==(on && !split),"startup mode");
    // Environment changes cannot turn a pending preference into half a live
    // switch. Only a fresh renderer/process may rebuild the matching shaders.
    setenv("NFS_HDR_OUTPUT",on?"off":"edr",1);render_hdr_output_init();
    CHECK(hdr_output_requested==on && layer.pixelFormat==expected,"mid-run request changed renderer");
    layer.drawableSize=CGSizeMake(16,1);
    id<CAMetalDrawable> drawable=[layer nextDrawable];CHECK(drawable,"startup drawable");
    CHECK(drawable.texture.pixelFormat==expected,"stale startup drawable format");
    backbuffer_tex=texture(MTLPixelFormatBGRA8Unorm,16,1);present_tex=texture(MTLPixelFormatBGRA8Unorm,16,1);
    uint8_t input[64];for(int i=0;i<64;++i)input[i]=i%4==3?255:i*3;
    [backbuffer_tex replaceRegion:MTLRegionMake2D(0,0,16,1) mipmapLevel:0 withBytes:input bytesPerRow:64];
    cmd=[queue commandBuffer];CHECK(present_copy(),"startup present copy");
    id<MTLTexture> source=hdr_output_on?hdr_present_tex:present_tex;
    CHECK(source.pixelFormat==drawable.texture.pixelFormat,"stale startup source format");
    quad_into(cmd,source,MTLRegionMake2D(0,0,16,1),drawable.texture,0,0,MTLRegionMake2D(0,0,16,1),0);
    CHECK(!finish(),"startup frame finish");
    printf("hdr_output_test: restart request=%s split=%d layer/source/drawable=%lu/%lu/%lu stale=0 PASS\n",on?"On":"Off",split,
        (unsigned long)layer.pixelFormat,(unsigned long)source.pixelFormat,(unsigned long)drawable.texture.pixelFormat);
    return 0;
}
static int sdr(int fallback) {
    CHECK(fallback || !strcmp(getenv("NFS_HDR_OUTPUT") ?: "", "off"),"SDR switch must be off");
    render_hdr_output_init();
    CHECK(fallback ? hdr_output_on : !hdr_output_on && layer.pixelFormat==MTLPixelFormatBGRA8Unorm,"SDR mode");
    enum {W=256,H=4,N=W*H*4};
    uint8_t input[N],actual[N],expected[N],captured[N];
    __fp16 transported[N];
    id<CAMetalDrawable> drawable=nil;
    id<MTLBuffer> native=nil;
    if(fallback) {
        CHECK(render_hdr_headroom()==1,"H=1 screen fixture");
        layer.drawableSize=CGSizeMake(W,H);drawable=[layer nextDrawable];CHECK(drawable,"H=1 native drawable");
        native=[mtl newBufferWithLength:W*H*8 options:MTLResourceStorageModeShared];
    }
    for(int i=0;i<N;i+=4){input[i]=i/4;input[i+1]=255-i/4;input[i+2]=(i/4)*37;input[i+3]=255;}
    backbuffer_tex=texture(MTLPixelFormatBGRA8Unorm,W,H);
    [backbuffer_tex replaceRegion:MTLRegionMake2D(0,0,W,H) mipmapLevel:0 withBytes:input bytesPerRow:W*4];
    // Exercise the actual phase switch, including the H=1 early SDR route.
    id<MTLTexture> display=backbuffer_tex;bb_rt=(RenderTex){.tex=display};
    float_scene_on=1;cmd=[queue commandBuffer];render_float_scene_begin();
    CHECK(fs_active && backbuffer_tex.pixelFormat==MTLPixelFormatRGBA16Float,"SDR floating world phase");
    render_float_scene_final(0);CHECK(!finish(),"SDR route finish");
    CHECK(backbuffer_tex==display && render_hdr_composition_headroom()==1,"SDR composition phase");
    unsigned compared=0,capture_compared=0;
    for(int grade=0;grade<2;grade++) {
        uint16_t ramp[768];
        for(int i=0;i<768;i++)ramp[i]=grade?(uint16_t)lround(65535*pow((i&255)/255.,.8+(.2*(i/256)))):(i&255)*257;
        render_set_gamma(ramp);
        CHECK(gamma_active==grade,"gamma control %d",grade);
        id<MTLTexture> outputs[2]={texture(MTLPixelFormatBGRA8Unorm,W,H),texture(MTLPixelFormatBGRA8Unorm,W,H)};
        for(int legacy=0;legacy<2;legacy++) {
            present_tex=texture(MTLPixelFormatBGRA8Unorm,W,H);cmd=[queue commandBuffer];
            CHECK(legacy?legacy_present_copy():present_copy(),"SDR present copy");
            MTLRegion r=MTLRegionMake2D(0,0,W,H);
            if(legacy)legacy_quad_into(cmd,present_tex,r,outputs[legacy],0,0,r,1);
            else quad_into(cmd,present_tex,r,outputs[legacy],0,0,r,1);
            id<MTLTexture> capture=nil;
            if(!legacy) {
                // The production selector used by diagnostic and F10 report shots.
                capture=hdr_shot_source(cmd,1);
                CHECK(capture.pixelFormat==MTLPixelFormatBGRA8Unorm,"H=1/Off capture format");
                CHECK(hdr_capture_source(cmd,backbuffer_tex)==backbuffer_tex,"SDR sequence bypass");
            }
            if(fallback && !legacy) {
                quad_into(cmd,hdr_present_tex,r,drawable.texture,0,0,r,1);
                id<MTLBlitCommandEncoder> b=[cmd blitCommandEncoder];
                [b copyFromTexture:drawable.texture sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(W,H,1)
                    toBuffer:native destinationOffset:0 destinationBytesPerRow:W*8 destinationBytesPerImage:W*H*8];[b endEncoding];
            }
            CHECK(!finish(),"SDR finish");
            if(!legacy)[capture getBytes:captured bytesPerRow:W*4 fromRegion:r mipmapLevel:0];
            if(fallback && !legacy)memcpy(transported,native.contents,sizeof transported);
        }
        [outputs[0] getBytes:actual bytesPerRow:W*4 fromRegion:MTLRegionMake2D(0,0,W,H) mipmapLevel:0];
        [outputs[1] getBytes:expected bytesPerRow:W*4 fromRegion:MTLRegionMake2D(0,0,W,H) mipmapLevel:0];
        unsigned differences=0;for(int i=0;i<N;++i)differences+=actual[i]!=expected[i];
        CHECK(!differences,"SDR baseline bytes differ grade=%d mismatches=%u",grade,differences);
        unsigned capture_differences=0;for(int i=0;i<N;++i)capture_differences+=captured[i]!=expected[i];
        CHECK(!capture_differences,"SDR capture bytes differ grade=%d mismatches=%u",grade,capture_differences);
        capture_compared+=N;
        if(fallback)for(int i=0;i<N;i+=4)for(int c=0;c<4;++c) {
            double v=transported[i+(c<3?2-c:c)];
            int byte=(int)lround(255*(c<3?encode(v):v));
            CHECK(byte==expected[i+c],"H=1 drawable roundtrip byte %d grade %d: %d expected %d",i+c,grade,byte,expected[i+c]);
        }
        compared+=N;
    }
    printf("hdr_output_test: SDR %s bytes_identical=%u/8192 capture_bytes=%u/8192 drawable_roundtrip=%u gamma=identity/nonidentity PASS\n",fallback?"H=1":"unset",compared,capture_compared,fallback?compared:0);return 0;
}
static int edr(int broken) {
    CHECK(nfs_hdr_output_requested(),"EDR switch");
    render_hdr_output_init();
    CHECK(hdr_output_on,"EDR initialization failed");
    CHECK(layer.pixelFormat==MTLPixelFormatRGBA16Float && layer.wantsExtendedDynamicRangeContent,"EDR layer properties");
    CGColorSpaceRef expected_cs=CGColorSpaceCreateWithName(kCGColorSpaceExtendedLinearSRGB);
    int matching_cs=CFEqual(layer.colorspace,expected_cs);CGColorSpaceRelease(expected_cs);
    CHECK(matching_cs,"linear sRGB tag");
    id<CAMetalDrawable> drawable=[layer nextDrawable];
    if(!drawable){++hdr_nil_drawables;hdr_output_diag("offline-nil-drawable",MTLPixelFormatInvalid);}
    CHECK(drawable,"offline float CAMetalLayer returned nil drawable");
    hdr_output_diag("offline-drawable",drawable.texture.pixelFormat);
    CHECK(drawable.texture.pixelFormat==MTLPixelFormatRGBA16Float,"actual drawable format %lu",(unsigned long)drawable.texture.pixelFormat);
    const float patches[]={0,.214041f,1,2,4};
    __fp16 input[5][4];
    for(int i=0;i<5;i++){for(int j=0;j<3;j++)input[i][j]=patches[i];input[i][3]=1;}
    backbuffer_tex=texture(MTLPixelFormatRGBA16Float,5,1);
    [backbuffer_tex replaceRegion:MTLRegionMake2D(0,0,5,1) mipmapLevel:0 withBytes:input bytesPerRow:40];
    present_tex=texture(broken?MTLPixelFormatBGRA8Unorm:MTLPixelFormatRGBA16Float,5,1);
    // Deliberately zero LUT: linear patches must bypass the clamping brightness path.
    memset(gamma_lut,0,sizeof gamma_lut);gamma_active=1;cmd=[queue commandBuffer];
    CHECK(hdr_output_blit(cmd,backbuffer_tex,present_tex,0,0),"EDR transport copy");
    MTLRegion r=MTLRegionMake2D(0,0,5,1);
    quad_into(cmd,present_tex,r,drawable.texture,0,0,r,1);
    // Read back via a blit, including the native drawable (framebufferOnly=NO).
    id<MTLBuffer> read=[mtl newBufferWithLength:256 options:MTLResourceStorageModeShared];
    id<MTLBlitCommandEncoder> b=[cmd blitCommandEncoder];
    [b copyFromTexture:drawable.texture sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(5,1,1)
               toBuffer:read destinationOffset:0 destinationBytesPerRow:256 destinationBytesPerImage:256];[b endEncoding];
    CHECK(!finish(),"EDR finish");
    const __fp16 *values=read.contents;int above=0,lost=0,bad=0;
    fprintf(stderr,"hdr_output_test: %s patches=",broken?"BROKEN":"EDR");
    for(int i=0;i<5;i++){
        float value=values[i*4];fprintf(stderr,"%s%.8f",i?",":"",value);
        above+=value>1;lost+=patches[i]>1 && value<=1;
        for(int j=0;j<3;j++)bad+=!isfinite((float)values[i*4+j])||fabs((float)values[i*4+j]-patches[i])>.002;
    }
    fprintf(stderr," above_one=%d lost=%d bad_channels=%d\n",above,lost,bad);
    CHECK(above==2 && lost==0 && bad==0,"PATCH above_one=%d lost=%d bad_channels=%d",above,lost,bad);
    // Negative control: unchanged linear data into BGRA8 must lose both highlights.
    id<MTLTexture> control=texture(MTLPixelFormatBGRA8Unorm,5,1);cmd=[queue commandBuffer];
    quad_into(cmd,present_tex,r,control,0,0,r,1);CHECK(!finish(),"BGRA8 control finish");
    uint8_t bytes[20];[control getBytes:bytes bytesPerRow:20 fromRegion:r mipmapLevel:0];
    CHECK(bytes[0]==0 && bytes[4]==55 && bytes[8]==255 && bytes[12]==255 && bytes[16]==255,"BGRA8 negative control bytes");
    puts("hdr_output_test: BGRA8 negative bytes=0,55,255,255,255 lost=2 PASS");
    // Both four-byte capture paths share this exact production conversion.
    cmd=[queue commandBuffer];id<MTLTexture> cap=hdr_capture_source(cmd,present_tex);
    id<MTLBuffer> probe_bytes=probe_capture(cmd,present_tex);
    CHECK(cap.pixelFormat==MTLPixelFormatBGRA8Unorm,"capture format");
    b=[cmd blitCommandEncoder];
    [b copyFromTexture:cap sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(5,1,1)
               toBuffer:read destinationOffset:0 destinationBytesPerRow:256 destinationBytesPerImage:256];[b endEncoding];
    CHECK(!finish(),"capture finish");memcpy(bytes,read.contents,sizeof bytes);
    CHECK(probe_bytes.length==sizeof bytes,"probe SDR buffer length");
    unsigned probe_differences=0;for(unsigned i=0;i<sizeof bytes;++i)probe_differences+=((const uint8_t *)probe_bytes.contents)[i]!=bytes[i];
    CHECK(!probe_differences,"probe SDR bytes differ mismatches=%u",probe_differences);
    for(int i=0;i<5;i++){
        double x=patches[i];if(x>.75)x=.75+.25*(1-exp(-(x-.75)/.25));
        int expected=(int)lround(255*(x<=.0031308?12.92*x:1.055*pow(x,1/2.4)-.055));
        CHECK(abs(bytes[i*4]-expected)<=1 && bytes[i*4]==bytes[i*4+1] && bytes[i*4]==bytes[i*4+2] && bytes[i*4+3]==255,"SDR capture patch %d byte %d expected %d",i,bytes[i*4],expected);
    }
    // Gamma-space 128 decodes once, while captures of existing SDR bypass conversion.
    id<MTLTexture> encoded=texture(MTLPixelFormatBGRA8Unorm,1,1),linear=texture(MTLPixelFormatRGBA16Float,1,1);
    uint8_t gray[]={128,128,128,255};[encoded replaceRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0 withBytes:gray bytesPerRow:4];
    cmd=[queue commandBuffer];CHECK(hdr_output_blit(cmd,encoded,linear,0,0),"SDR decode");CHECK(hdr_capture_source(cmd,encoded)==encoded,"SDR capture bypass");
    id<MTLBuffer> sdr_probe=probe_capture(cmd,encoded);
    CHECK(!finish(),"decode finish");__fp16 pixel[4];[linear getBytes:pixel bytesPerRow:8 fromRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0];
    CHECK(sdr_probe.length==4 && !memcmp(sdr_probe.contents,gray,4),"SDR probe bytes changed");
    puts("hdr_output_test: probe converted_bytes=20/20 SDR_bytes=4/4 PASS");
    CHECK(fabs((float)pixel[0]-.2158605)<.0002,"decode once: %.8f",(float)pixel[0]);
    puts("hdr_output_test: float LUT bypass, SDR decode once, SDR capture tone-map/sRGB encode PASS");return 0;
}
static double decode(double x) { return x<=.04045?x/12.92:pow((x+.055)/1.055,2.4); }
static double encode(double x) { return x<=.0031308?12.92*x:1.055*pow(x,1/2.4)-.055; }
static double shoulder(double x,double h) { return x<=1?x:1+(h-1)*(1-exp(-(x-1)/(h-1))); }
static int composition(unsigned samples) {
    enum { W=1026, H=8, PITCH=(W*8+255)&~255 };
    render_hdr_output_init();CHECK(hdr_output_on,"composition EDR mode");
    layer.drawableSize=CGSizeMake(W,H);
    id<CAMetalDrawable> drawable=[layer nextDrawable];CHECK(drawable,"composition drawable");
    CHECK(drawable.texture.pixelFormat==MTLPixelFormatRGBA16Float,"composition drawable format");
    id<MTLTexture> world=texture(MTLPixelFormatRGBA16Float,W,H),copy=texture(MTLPixelFormatRGBA16Float,W,H);
    __fp16 input[H][W][4];
    for(int y=0;y<H;++y)for(int x=0;x<W;++x){for(int c=0;c<3;++c)input[y][x][c]=x<=1024?x/128.0f:0;input[y][x][3]=1;}
    [world replaceRegion:MTLRegionMake2D(0,0,W,H) mipmapLevel:0 withBytes:input bytesPerRow:W*8];
    id<MTLTexture> display=texture(MTLPixelFormatBGRA8Unorm,W,H);
    backbuffer_tex=display;bb_rt=(RenderTex){.tex=display};present_tex=texture(MTLPixelFormatBGRA8Unorm,W,H);
    if(samples>1)bb_rt.ms=new_ms_texture(MTLPixelFormatBGRA8Unorm,W,H,samples);
    id<MTLTexture> hud=texture(MTLPixelFormatBGRA8Unorm,1,1);
    uint8_t white[4]={255,255,255,255};[hud replaceRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0 withBytes:white bytesPerRow:4];
    NSString *hud_source=[NSString stringWithUTF8String:hud_test_src];
    MTLTextureDescriptor *dd=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatDepth32Float width:W height:H mipmapped:NO];
    dd.storageMode=MTLStorageModePrivate;dd.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;
    id<MTLTexture> depth=[mtl newTextureWithDescriptor:dd];
    id<MTLBuffer> read=[mtl newBufferWithLength:PITCH options:MTLResourceStorageModeShared];
    test_screen.maximumExtendedDynamicRangeColorComponentValue=4;
    hdr_output_frame_boundary();
    float_scene_on=1;cmd=[queue commandBuffer];render_float_scene_begin();
    MTLRenderPassDescriptor *dp=[MTLRenderPassDescriptor renderPassDescriptor];
    dp.depthAttachment.texture=depth;dp.depthAttachment.loadAction=MTLLoadActionClear;dp.depthAttachment.clearDepth=0;dp.depthAttachment.storeAction=MTLStoreActionStore;
    id<MTLRenderCommandEncoder> de=[cmd renderCommandEncoderWithDescriptor:dp];[de endEncoding];
    CHECK(fs_active && fs_headroom==4,"layer screen headroom sampling");
    MTLRegion full=MTLRegionMake2D(0,0,W,H);
    quad_into(cmd,world,full,bb_rt.ms?:backbuffer_tex,0,0,full,0);bb_rt.stale=bb_rt.ms!=nil;
    render_float_scene_final(0);CHECK(!finish(),"HDR route finish");
    CHECK(fs_composing && backbuffer_tex.pixelFormat==MTLPixelFormatRGBA16Float,"HDR composition route");
    NSError *hud_error=nil;
    id<MTLLibrary> hud_library=[mtl newLibraryWithSource:hdrclip_world_source(hud_source,1,0,0) options:nil error:&hud_error];
    CHECK(hud_library,"HUD guard: %s",hud_error.localizedDescription.UTF8String);
    MTLRenderPipelineDescriptor *hd=[MTLRenderPipelineDescriptor new];
    hd.vertexFunction=[hud_library newFunctionWithName:@"hud_vs"];hd.fragmentFunction=[hud_library newFunctionWithName:@"hud_fs"];
    hd.colorAttachments[0].pixelFormat=MTLPixelFormatRGBA16Float;hd.rasterSampleCount=samples;
    id<MTLRenderPipelineState> hud_pipeline=[mtl newRenderPipelineStateWithDescriptor:hd error:&hud_error];CHECK(hud_pipeline,"HUD pipeline");
    copy=fs_world_rt.tex;
    x_edr_processing=1;CHECK(x_init(W,H),"floating post init");
    CHECK(xt_q0.pixelFormat==MTLPixelFormatRGBA16Float && xt_lum.pixelFormat==MTLPixelFormatRGBA16Float && xt_dof.pixelFormat==MTLPixelFormatRGBA16Float,"post precision");
    id<MTLRenderPipelineState> direct_post=nil;
    if(pipeline_hold_test) {
        NSError *error=nil;
        id<MTLLibrary> lib=[mtl newLibraryWithSource:hdrclip_shader(x_edr_source([NSString stringWithUTF8String:x360_src])) options:nil error:&error];
        MTLRenderPipelineDescriptor *d=[MTLRenderPipelineDescriptor new];
        d.vertexFunction=[lib newFunctionWithName:@"vs_full"];d.fragmentFunction=[lib newFunctionWithName:@"fs_vt"];
        d.colorAttachments[0].pixelFormat=MTLPixelFormatRGBA16Float;d.rasterSampleCount=samples;
        direct_post=[mtl newRenderPipelineStateWithDescriptor:d error:&error];CHECK(direct_post,"direct held reference");
    }
    for(int effects=0;effects<2;++effects)for(int grade=0;grade<3;++grade) {
      NSData *held_reference=nil;
      for(int held=0;held<(pipeline_hold_test?2:1);++held) {
        double exponent=grade==1?.8:grade==2?1.2:1;
        uint16_t ramp[768];for(int i=0;i<768;++i)ramp[i]=(uint16_t)lround(65535*pow((i&255)/255.,exponent));
        render_set_gamma(ramp);cmd=[queue commandBuffer];
        MTLRegion full=MTLRegionMake2D(0,0,W,H);
        quad_into(cmd,world,full,copy,0,0,full,0);
        XK k={0};k.p0[2]=1;
        x_pass(xp_down,xt_q0,xt_q1,copy,xs_lin,&k,nil,0);
        memset(&k,0,sizeof k);x_pass(xp_lum,xt_lum,nil,xt_q0,xs_lin,&k,xt_q1,0);
        k.p0[0]=k.p0[1]=.5;k.p0[3]=.75;
        x_pass(xp_bright,xt_bright,nil,xt_q1,xs_lin,&k,nil,0);
        memset(&k,0,sizeof k);for(int c=0;c<4;++c)k.wgt[0][c]=1;
        x_pass(xp_taps,xt_bblur,nil,xt_bright,xs_lin,&k,nil,0);
        x_pass(xp_taps,xt_bloomA,nil,xt_bblur,xs_lin,&k,nil,0);
        x_pass(xp_taps,xt_bloomB,nil,xt_bloomA,xs_lin,&k,nil,0);
        x_pass(xp_taps,xt_bloomA,nil,xt_bloomB,xs_lin,&k,nil,0);
        memset(&k,0,sizeof k);x_pass(xp_dofdown,xt_dof,nil,copy,xs_lin,&k,nil,0);
        // Same consumed constant layout as the production composite.
        struct {float coeffs[4][4],b[4],blurParams[4],dofp[4],misc[4],zr[4],tp[4],rx[4];} vk={0};
        vk.coeffs[0][0]=1;vk.misc[0]=vk.misc[2]=vk.misc[3]=1;vk.dofp[3]=7;
        vk.zr[0]=.5;vk.zr[1]=100;vk.zr[2]=effects;vk.zr[3]=effects?.1:0;vk.dofp[2]=1;vk.tp[3]=render_hdr_composition_headroom();
        MTLRenderPassDescriptor *rp=[MTLRenderPassDescriptor renderPassDescriptor];
        id<MTLTexture> out=bb_rt.ms?:backbuffer_tex;
        rp.colorAttachments[0].texture=out;rp.colorAttachments[0].loadAction=MTLLoadActionDontCare;rp.colorAttachments[0].storeAction=MTLStoreActionStore;
        id<MTLRenderCommandEncoder> e=[cmd renderCommandEncoderWithDescriptor:rp];
        [e setRenderPipelineState:direct_post && held==0 ? direct_post : x_edr_pipeline(@"fs_vt",samples,0)];
        id<MTLTexture> textures[]={copy,xt_q0,xt_white,xt_bloomA,xt_dof,depth,xt_bblur};
        for(int i=0;i<7;++i)[e setFragmentTexture:textures[i] atIndex:i];
        [e setFragmentSamplerState:xs_lin atIndex:0];[e setFragmentSamplerState:xs_vig atIndex:1];
        [e setFragmentBytes:&vk length:sizeof vk atIndex:0];[e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[e endEncoding];
        render_hdr_scene_treated();
        // The production phase/policy guards an actual gain-two FE/HUD export.
        render_float_scene_final(1);
        unsigned policy=float_scene_store_policy(out.pixelFormat,dev.backbuffer);CHECK(policy==2,"HUD source policy");
        rp.colorAttachments[0].loadAction=MTLLoadActionLoad;e=[cmd renderCommandEncoderWithDescriptor:rp];
        [e setRenderPipelineState:hud_pipeline];[e setViewport:(MTLViewport){W-1,0,1,H,0,1}];
        [e setFragmentBytes:&policy length:sizeof policy atIndex:27];
        [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[e endEncoding];
        bb_rt.stale=bb_rt.ms!=nil;msaa_resolve(&bb_rt);
        CHECK(present_copy(),"composition present");
        quad_into(cmd,hdr_present_tex,full,drawable.texture,0,0,full,0);
        id<MTLBlitCommandEncoder> b=[cmd blitCommandEncoder];
        [b copyFromTexture:drawable.texture sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(W,1,1)
            toBuffer:read destinationOffset:0 destinationBytesPerRow:PITCH destinationBytesPerImage:PITCH];[b endEncoding];
        CHECK(!finish(),"composition finish");
        NSData *pixels=[NSData dataWithBytes:read.contents length:W*8];
        if(held==0)held_reference=pixels;
        else CHECK([pixels isEqualToData:held_reference],"prewarmed held pixels differ");
        const __fp16 *v=read.contents;int above=0,bad=0;float peak=0;
        float hud_white=v[(W-1)*4];CHECK(hud_white==1,"HUD paper white %.8f",hud_white);
        for(int x=0;x<=1024;++x) {
            above+=v[x*4]>1;peak=fmaxf(peak,v[x*4]);
            for(int c=0;c<3;++c)bad+=!isfinite((float)v[x*4+c])||v[x*4+c]<0||v[x*4+c]>4;
            if(!effects) {
                double want=decode(pow(encode(shoulder(decode(x/128.),4)),hdr_brightness_exponent(0)));want=fmin(want,4);
                CHECK(fabs(v[x*4]-want)<.009,"ramp x=%d actual %.8f expected %.8f",x,(float)v[x*4],want);
            }
        }
        printf("hdr_output_test: composition samples=%u effects=%d gamma=%.1f above_one=%d peak=%.8f HUD=%.8f bad=%d midpoint=%.8f\n",samples,effects,exponent,above,peak,hud_white,bad,(float)v[64*4]);
        CHECK(hud_white==1,"HUD paper white %.8f",hud_white);
        CHECK(!bad && above>800,"composition range above=%d bad=%d",above,bad);
        if(!effects && !grade)CHECK(above==896 && fabs(v[64*4]-.21404114)<.0002,"midpoint/ramp decode");
      }
    }
    if(pipeline_hold_test)printf("hdr_output_test: prewarm held samples=%u frames=6 bytes=49248 identical\n",samples);
    render_float_scene_present_end();
    CHECK(backbuffer_tex==display && !fs_composing,"restore SDR before next clear/FE/movie frame");
    // No-treatment path, and a new screen headroom on the next frame. Its
    // shoulder still precedes HUD; previous-frame state cannot select H=4.
    ++frame_serial;test_screen.maximumExtendedDynamicRangeColorComponentValue=2;
    hdr_output_frame_boundary();
    uint16_t identity[768];for(int i=0;i<768;++i)identity[i]=(i&255)*257;render_set_gamma(identity);
    cmd=[queue commandBuffer];render_float_scene_begin();
    quad_into(cmd,world,full,bb_rt.ms?:backbuffer_tex,0,0,full,0);bb_rt.stale=bb_rt.ms!=nil;
    render_float_scene_final(1);CHECK(render_hdr_composition_headroom()==2,"next-frame headroom");
    quad_into(cmd,hud,MTLRegionMake2D(0,0,1,1),bb_rt.ms?:backbuffer_tex,0,0,MTLRegionMake2D(W-1,0,1,H),0);
    bb_rt.stale=bb_rt.ms!=nil;msaa_resolve(&bb_rt);CHECK(present_copy(),"untreated present");
    quad_into(cmd,hdr_present_tex,full,drawable.texture,0,0,full,0);
    id<MTLBlitCommandEncoder> rb=[cmd blitCommandEncoder];
    [rb copyFromTexture:drawable.texture sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(W,1,1)
        toBuffer:read destinationOffset:0 destinationBytesPerRow:PITCH destinationBytesPerImage:PITCH];[rb endEncoding];
    CHECK(!finish(),"untreated finish");const __fp16 *raw=read.contents;
    CHECK(raw[(W-1)*4]==1 && fabs(raw[256*4]-shoulder(decode(2),2))<.005,"untreated tone/HUD");
    for(int x=0;x<=1024;++x)CHECK(isfinite((float)raw[x*4]) && raw[x*4]>=0 && raw[x*4]<=2,"H=2 bound");
    render_float_scene_present_end();
    puts("hdr_output_test: scene/copy/float post/HUD/continuous brightness/native drawable PASS");return 0;
}
// Exercise every EDR recipe before a held composition frame. This is also run
// with a deliberately omitted downscale recipe (two frame-path builds).
static int prewarm(unsigned samples) {
    setenv("NFS_PSO_PREWARM","1",1);
    dispatch_queue_t warm_queue=dispatch_queue_create("post-prewarm-test",DISPATCH_QUEUE_SERIAL);
    dispatch_semaphore_t ready=dispatch_semaphore_create(0);
    dispatch_async(warm_queue,^{@autoreleasepool{render_post_prewarm();dispatch_semaphore_signal(ready);}});
    dispatch_semaphore_wait(ready,DISPATCH_TIME_FOREVER);
    unsigned loaded=pipeline_edr_builds;
    for(int lamp=0;lamp<2;++lamp) {
        for(NSString *fs in @[@"fs_downscale", @"fs_lum", @"fs_bright", @"fs_taps", @"fs_dofdown", @"fs_tdown"])
            CHECK(x_edr_pipeline(fs,1,lamp),"EDR pass");
        for(NSUInteger samples=1;samples<=8;samples*=2)if([mtl supportsTextureSampleCount:samples])
            CHECK(x_edr_pipeline(@"fs_vt",samples,lamp),"EDR composite");
    }
    unsigned misses=pipeline_edr_builds-loaded;
    printf("hdr_output_test: prewarm loaded=%u frame_builds=%u\n",loaded,misses);
    CHECK(!misses,"EDR prewarm missed %u descriptors",misses);
    // The existing composition proof exercises the actual half-float post chain.
    pipeline_hold_test=1;
    int result=composition(samples);
    CHECK(pipeline_edr_builds==loaded+misses,"held composition built an EDR pipeline");
    return result || misses ? 1 : 0;
}
// The lamp variant has a different fragment signature. Render both variants from
// independently created libraries and compare every stored half-float byte.
static int prewarm_lamps(void) {
    setenv("NFS_PSO_PREWARM","1",1);
    dispatch_queue_t q=dispatch_queue_create("post-lamp-prewarm-test",DISPATCH_QUEUE_SERIAL);
    dispatch_sync(q,^{@autoreleasepool{render_post_prewarm();}});
    unsigned loaded=pipeline_edr_builds;
    const NSUInteger w=16,h=8;
    MTLTextureDescriptor *td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA16Float width:w height:h mipmapped:NO];
    td.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;td.storageMode=MTLStorageModeShared;
    id<MTLTexture> input=[mtl newTextureWithDescriptor:td];
    MTLTextureDescriptor *zd=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatDepth16Unorm width:w height:h mipmapped:NO];
    zd.usage=MTLTextureUsageShaderRead;zd.storageMode=MTLStorageModeShared;
    id<MTLTexture> depth=[mtl newTextureWithDescriptor:zd];uint16_t z[16*8];for(unsigned i=0;i<16*8;++i)z[i]=32768;
    [depth replaceRegion:MTLRegionMake2D(0,0,w,h) mipmapLevel:0 withBytes:z bytesPerRow:w*2];
    __fp16 values[16*8*4];for(unsigned i=0;i<16*8;++i){values[4*i]=.125;values[4*i+1]=.25;values[4*i+2]=.5;values[4*i+3]=1;}
    [input replaceRegion:MTLRegionMake2D(0,0,w,h) mipmapLevel:0 withBytes:values bytesPerRow:w*8];
    unsigned pairs=0,bytes=0;
    for(int lamp=0;lamp<2;++lamp)for(NSUInteger samples=1;samples<=4;samples*=4) {
        NSError *error=nil;NSString *src=[NSString stringWithUTF8String:x360_src];
        if(lamp) {
            src=[src stringByReplacingOccurrencesOfString:@"texture2d<float> bself [[texture(6)]]" withString:@"texture2d<float> bself [[texture(6)]], texture2d<float> lamp [[texture(7)]]"];
            src=[src stringByReplacingOccurrencesOfString:@"    if (patch) bl = 0;" withString:@"    bl += lamp.sample(lin,uv).rgb;\n    if (patch) bl = 0;"];
        }
        id<MTLLibrary> library=[mtl newLibraryWithSource:hdrclip_shader(x_edr_source(src)) options:nil error:&error];CHECK(library,"held lamp library");
        MTLRenderPipelineDescriptor *d=[MTLRenderPipelineDescriptor new];d.vertexFunction=[library newFunctionWithName:@"vs_full"];d.fragmentFunction=[library newFunctionWithName:@"fs_vt"];
        d.colorAttachments[0].pixelFormat=td.pixelFormat;d.rasterSampleCount=samples;
        id<MTLRenderPipelineState> direct=[mtl newRenderPipelineStateWithDescriptor:d error:&error];CHECK(direct,"held lamp direct PSO");
        NSData *reference=nil;
        for(int held=0;held<2;++held) {
            id<MTLTexture> out=[mtl newTextureWithDescriptor:td],target=out;
            if(samples>1) {
                MTLTextureDescriptor *ms=[td copy];ms.textureType=MTLTextureType2DMultisample;ms.sampleCount=samples;ms.storageMode=MTLStorageModePrivate;
                target=[mtl newTextureWithDescriptor:ms];
            }
            MTLRenderPassDescriptor *rp=[MTLRenderPassDescriptor renderPassDescriptor];rp.colorAttachments[0].texture=target;
            rp.colorAttachments[0].loadAction=MTLLoadActionDontCare;rp.colorAttachments[0].storeAction=samples>1?MTLStoreActionMultisampleResolve:MTLStoreActionStore;
            if(samples>1)rp.colorAttachments[0].resolveTexture=out;
            id<MTLCommandBuffer> cb=[queue commandBuffer];id<MTLRenderCommandEncoder> e=[cb renderCommandEncoderWithDescriptor:rp];
            [e setRenderPipelineState:held?x_edr_pipeline(@"fs_vt",samples,lamp):direct];
            for(unsigned i=0;i<8;++i)[e setFragmentTexture:i==5?depth:input atIndex:i];
            [e setFragmentSamplerState:xs_lin atIndex:0];[e setFragmentSamplerState:xs_vig atIndex:1];
            struct {float coeffs[4][4],b[4],blurParams[4],dofp[4],misc[4],zr[4],tp[4],rx[4];} k={0};
            k.b[0]=1;k.b[3]=1;k.misc[0]=1;k.misc[2]=1;k.misc[3]=1;
            k.zr[0]=.5;k.zr[1]=10000;k.rx[3]=1;k.tp[3]=1;
            k.coeffs[0][0]=1;
            [e setFragmentBytes:&k length:sizeof k atIndex:0];[e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[e endEncoding];
            [cb commit];[cb waitUntilCompleted];CHECK(cb.status==MTLCommandBufferStatusCompleted,"held lamp command buffer");
            __fp16 pixels[16*8*4];[out getBytes:pixels bytesPerRow:w*8 fromRegion:MTLRegionMake2D(0,0,w,h) mipmapLevel:0];
            NSData *data=[NSData dataWithBytes:pixels length:sizeof pixels];
            if(!held)reference=data;
            else {
                unsigned differing=0;const unsigned char *a=data.bytes,*b=reference.bytes;
                for(unsigned i=0;i<sizeof pixels;++i)differing+=a[i]!=b[i];
                CHECK(!differing,"held lamp pixels differ lamp=%d samples=%lu differing=%u/%lu",lamp,(unsigned long)samples,differing,(unsigned long)sizeof pixels);
                ++pairs;bytes+=sizeof pixels;
            }
        }
    }
    CHECK(pipeline_edr_builds==loaded,"held lamp render built a pipeline");
    printf("hdr_output_test: prewarm lamp pairs=%u bytes=%u identical\n",pairs,bytes);
    return 0;
}
int main(int argc,const char **argv) {@autoreleasepool{
    if(argc==3 && !strcmp(argv[1],"--sources"))return sources(argv[2]);
    if(argc==2 && !strcmp(argv[1],"--cpu"))return cpu();
    if(argc==2 && !strcmp(argv[1],"--cpu-lifecycle"))return lifecycle_cpu();
    mtl=MTLCreateSystemDefaultDevice();
    if(!mtl){puts("GPU PENDING: run on a Metal-capable host: runtime/offline_tests.sh hdr_output_test");return 3;}
    queue=[mtl newCommandQueue];
    layer=[CAMetalLayer layer];layer.device=mtl;layer.pixelFormat=MTLPixelFormatBGRA8Unorm;
    // A detached view/window/screen fixture supplies deterministic H to the
    // production layer.delegate -> window.screen sampler. No native window.
    fixture_screen(1);
    layer.drawableSize=CGSizeMake(5,1);layer.framebufferOnly=NO;
    INIT_BLIT
    if(argc==2 && !strcmp(argv[1],"--auto-hdr"))return auto_hdr();
    if(argc==2 && !strcmp(argv[1],"--startup"))return startup();
    if(argc==2 && !strcmp(argv[1],"--sdr"))return sdr(0);
    if(argc==2 && !strcmp(argv[1],"--fallback"))return sdr(1);
    if(argc==2 && !strcmp(argv[1],"--prewarm"))return prewarm(1);
    if(argc==2 && !strcmp(argv[1],"--prewarm-msaa"))return prewarm(4);
    if(argc==2 && !strcmp(argv[1],"--prewarm-lamps"))return prewarm_lamps();
    if(argc==2 && !strcmp(argv[1],"--composition"))return composition(1);
    if(argc==2 && !strcmp(argv[1],"--composition-msaa"))return composition(4);
    return edr(argc==2 && !strcmp(argv[1],"--broken"));
}}
