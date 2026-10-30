// Production counter control: 1024 cube, 11 mips => 60 completed mip-face passes;
// deliberately omitted generation => 0 (FAIL). Both numbers checked by the driver.
// Default regression: unset => 1024/11; old PC-default mutant => 256/1 (FAIL).
// GPU matrix: software/native, direct/replay. Good: 60/60 mip faces refreshed;
// omitted generation: 60/60 poisoned (exit 1); one stale mip: 1/60 (exit 1).
// A second base-level update must refresh all lower texels in the same buffer.
// Exhaustive day/night/rain pattern comparisons report every face/level maximum.
// BGRA8 comparison: max 0; missing replay opcode with poison: max 252, exit 1.
// Native default: unset 1; default-off mutant 0, exit 1.
#include <stdio.h>
#include <stdarg.h>
#include <time.h>
#import <Metal/Metal.h>
#include "pipeline_cache.h"
id<MTLRenderPipelineState> pipeline_build_device(id<MTLDevice> device, MTLRenderPipelineDescriptor *d, const char *pass, NSError **error) {
    (void)pass; return [device newRenderPipelineStateWithDescriptor:d error:error];
}
static int cpu_counter;
static MTLRenderPassDescriptor *envcube_test_pass(void);
#define ENVCUBE_PASS() envcube_test_pass()
#include "envcube.m"

// Fake encoder counts the actual production render commands without touching a GPU.
@interface EnvAttachment : NSObject
@property id texture;
@property NSUInteger level, slice;
@property MTLLoadAction loadAction;
@property MTLStoreAction storeAction;
@end
@implementation EnvAttachment
@end
@interface EnvPass : NSObject
@property NSArray *colorAttachments;
@end
@implementation EnvPass
@end
static MTLRenderPassDescriptor *envcube_test_pass(void) {
    if (!cpu_counter) return [MTLRenderPassDescriptor renderPassDescriptor];
    EnvPass *p=[EnvPass new]; p.colorAttachments=@[[EnvAttachment new]];
    return (MTLRenderPassDescriptor *)p;
}
@interface EnvCounter : NSObject
@property unsigned count;
@property unsigned blits;
@property NSUInteger mipmapLevelCount;
- (id)renderCommandEncoderWithDescriptor:(id)d;
- (void)setRenderPipelineState:(id)p;
- (void)setFragmentTexture:(id)t atIndex:(NSUInteger)i;
- (void)drawPrimitives:(MTLPrimitiveType)t vertexStart:(NSUInteger)s vertexCount:(NSUInteger)n;
- (void)endEncoding;
- (id)blitCommandEncoder;
- (void)generateMipmapsForTexture:(id)t;
@end
@implementation EnvCounter
- (id)renderCommandEncoderWithDescriptor:(MTLRenderPassDescriptor *)d {
    if (d.colorAttachments[0].level != self.count/6+1 || d.colorAttachments[0].slice != self.count%6 ||
        d.colorAttachments[0].storeAction != MTLStoreActionStore) abort();
    return self;
}
- (void)setRenderPipelineState:(id)p { (void)p; }
- (void)setFragmentTexture:(id)t atIndex:(NSUInteger)i { (void)t; if (i) abort(); }
- (void)drawPrimitives:(MTLPrimitiveType)t vertexStart:(NSUInteger)s vertexCount:(NSUInteger)n {
    if (t != MTLPrimitiveTypeTriangle || s || n != 3) abort();
}
- (void)endEncoding { ++_count; }
- (id)blitCommandEncoder { ++_blits; return self; }
- (void)generateMipmapsForTexture:(id)t { if (t!=self) abort(); }
@end

// render_thread.m's only external dependencies (the test uses the real recorder).
id<MTLCommandQueue> queue;
id<MTLDevice> mtl;
Device dev;
uint8_t *G;
id<MTLCommandBuffer> cmd;
int diag_on;
const char *diag_dir;
int diag_frame_wanted(uint64_t frame, const char *name) { (void)frame; (void)name; return 0; }
uint64_t host_ticks(void) { return clock_gettime_nsec_np(CLOCK_UPTIME_RAW); }
void diag_event(const char *fmt, ...) { (void)fmt; }
id<MTLCommandBuffer> rt_command_buffer(void);
extern int rt_on;
static int sharp_fixture, ended_passes;
static Obj fixture_objects[4];
static RenderTex fixture_gpu;
Obj *obj(uint32_t guest) {
    if (!sharp_fixture) abort();
    if (!guest) return NULL;
    for (unsigned i=0;i<4;++i) if (fixture_objects[i].guest==guest) return &fixture_objects[i];
    return NULL;
}
void end_pass(void) { if (!sharp_fixture) abort(); ++ended_passes; }
void ensure_cmd(void) { if (!sharp_fixture) abort(); }
RenderTex *gpu_tex(Obj *o) { (void)o; if (!sharp_fixture) abort(); return &fixture_gpu; }

static void sharp_hooks(int native) {
    setenv("NFS_CUBE_BLITMIPS",native ? "1":"0",1);
    sharp_fixture=1; diag_on=1; setenv("NFS_CAR_REFLECTION","sharp",1); envcube_configure();
    if (M32(0x008F8FF4u)!=1024 || envcube_creation_levels(1024,1,1,21)!=11 ||
        envcube_creation_levels(1024,1,0,21)!=1 || envcube_creation_levels(256,1,1,21)!=1) abort();
    // Main, split P1 and split P2: arbitrary output addresses must all be tracked.
    memset(&dev,0,sizeof dev);
    for (unsigned i=0;i<3;++i) {
        Obj *t=&fixture_objects[i]; t->kind=K_CUBE; t->guest=0x10000+i*16;
        t->w=t->h=1024; t->levels=11; t->usage=1; t->fmt=21;
        envcube_created(t,0x1234,0x5678);
        EnvCubeState *state=envcube_find(t->guest); if (!state) abort();
        EnvCounter *c=[EnvCounter new]; c.mipmapLevelCount=11;
        envcube_pso=(id)c; fixture_gpu.tex=(id)c; cmd=(id)c;
        NSMutableArray *views=[NSMutableArray new]; for (unsigned j=0;j<60;++j) [views addObject:c];
        state->views=views;
        Obj *surface=&fixture_objects[3]; surface->kind=K_SURFACE; surface->guest=0x11000;
        surface->parent=t->guest;
        dev.rt[0]=surface->guest;
        for (unsigned f=0;f<6;++f) {
            surface->face=f; envcube_draw(); envcube_draw_end();
            // Changing faces within one parent must not generate early.
            envcube_target_change(surface->guest,surface->guest);
            if (c.count) abort();
        }
        envcube_target_change(surface->guest,0);
        if (c.count!=(native ? 1u:60u) || c.blits!=(unsigned)native || state->generations!=1 ||
            state->mip_passes!=(native ? 0u:60u) || state->mip_blits!=(unsigned)native || state->pending) abort();
        dev.rt[0]=0; dev.tex[1]=t->guest;
        M32(0x00982C80u)=0x990000; M32(0x990004u)=4;
        for (unsigned s=0;s<20;++s) for (unsigned k=0;k<14;++k) dev.ss[s][k]=0xABC00000u+k;
        uint32_t saved[20][14]; memcpy(saved,dev.ss,sizeof saved);
        envcube_draw();
        if (dev.ss[1][5]!=2 || dev.ss[1][6]!=2 || dev.ss[1][7]!=2 || dev.ss[1][8] || dev.ss[1][9]) abort();
        envcube_draw_end();
        if (memcmp(saved,dev.ss,sizeof saved)) abort();
        // Non-car draws retain the exact guest filters even with a capture cube bound.
        M32(0x990004u)=3; envcube_draw(); envcube_draw_end();
        if (memcmp(saved,dev.ss,sizeof saved)) abort();
        M32(0x00982C80u)=0; envcube_forget(t); if (envcube_find(t->guest)) abort();
    }
    if (ended_passes!=3) abort(); ended_passes=0;
    printf("SHARP PASS main/P1/P2: 3 captures, encoders=%u native=%d; scoped sampler restored\n",native ? 3:180,native);
    fixture_gpu.tex=nil; cmd=nil; envcube_pso=nil; sharp_fixture=0; diag_on=0;
}

static int counter(int broken) {
    cpu_counter=1;
    // Every runtime entry hook is a byte-identical PC no-op, including the
    // guest edge and the complete device state. No GPU stub may be called.
    G=calloc(1,0xA00000); M32(0x008F8FF4u)=256;
    const char *options[]={NULL,"","sharp","garbage","pc"};
    for (unsigned i=0;i<5;++i) {
        if (options[i]) setenv("NFS_CAR_REFLECTION",options[i],1);
        else unsetenv("NFS_CAR_REFLECTION");
        M32(0x008F8FF4u)=256; envcube_configure();
        unsigned edge=M32(0x008F8FF4u), levels=envcube_creation_levels(edge,1,1,21);
        unsigned expected_edge=i==4 ? 256:1024, expected_levels=i==4 ? 1:11;
        printf("DEFAULT %s option=%s edge=%u levels=%u expected=%u/%u\n",
               edge==expected_edge && levels==expected_levels ? "PASS":"FAIL",
               options[i] ? options[i]:"unset",edge,levels,expected_edge,expected_levels);
        if (edge!=expected_edge || levels!=expected_levels) { free(G); G=NULL; return 1; }
    }
    memset(&dev,0xA5,sizeof dev); Device saved=dev;
    uint8_t *guest_before=malloc(0xA00000); memcpy(guest_before,G,0xA00000);
    setenv("NFS_CAR_REFLECTION","pc",1); envcube_configure();
    Obj fixture={0}; fixture.kind=K_CUBE; fixture.w=256; fixture.levels=1; fixture.usage=1; fixture.fmt=21;
    envcube_created(&fixture,0x006BD54Bu,0x00982A30u);
    if (envcube_creation_levels(256,1,1,21)!=1 || envcube_creation_levels(2048,3,1,21)!=3) abort();
    envcube_target_change(1,2); envcube_draw(); envcube_draw_end(); envcube_frame(); envcube_forget(&fixture);
    if (memcmp(&dev,&saved,sizeof dev) || memcmp(G,guest_before,0xA00000)) abort();
    free(guest_before);
    printf("PC PASS guest_bytes=10485760 device_bytes=%zu unchanged; no GPU calls\n",sizeof dev);
    sharp_hooks(0); sharp_hooks(1); unsetenv("NFS_CUBE_BLITMIPS"); envcube_configure();
    int default_ok=envcube_blitmips && envcube_blit_option(NULL) && envcube_blit_option("1") &&
        !envcube_blit_option("0") && !envcube_blit_option("garbage");
    printf("BLIT DEFAULT %s unset=%d expected=1\n",default_ok ? "PASS":"FAIL",envcube_blitmips);
    if (!default_ok) { free(G); G=NULL; return 1; }
    free(G); G=NULL;
    uint32_t ss[14], before[14];
    for (unsigned i=0;i<14;++i) ss[i]=0xABC00000u+i;
    memcpy(before,ss,sizeof ss); envcube_filter(ss,0);
    if (memcmp(before,ss,sizeof ss) || !envcube_option(NULL) || envcube_option("pc") || !envcube_option("garbage") || !envcube_option("sharp")) abort();
    envcube_filter(ss,1);
    for (unsigned i=0;i<14;++i) if (ss[i] != (i>=5 && i<=7 ? 2 : i==8 || i==9 ? 0 : before[i])) abort();
    if (envcube_level_count(1024)!=11 || envcube_bytes(1024,11)!=33554424 || envcube_bytes(256,1)!=1572864) abort();
    EnvCounter *c=[EnvCounter new]; c.mipmapLevelCount=11;
    NSMutableArray *views=[NSMutableArray new]; for (int i=0;i<60;++i) [views addObject:c];
    unsigned n=envcube_generate((id)c,(id)c,views,(id)c,broken);
    printf("COUNTER %s generated=%u completed=%u expected=60; PC filter bytes unchanged=56\n", n==60 && c.count==60 ? "PASS":"FAIL",n,c.count);
    unsigned nb=envcube_generate_blit((id)c,(id)c,broken);
    unsigned expected=broken ? 0:61;
    printf("BLIT COUNTER %s generated=%u blits=%u encoders=%u expected=1\n",nb==60 && c.blits==1 ? "PASS":"FAIL",nb,c.blits,c.count);
    return n==60 && nb==60 && c.count==expected && c.blits==1 ? 0:1;
}
static int gpu(int broken, int native, int stale) {
    id<MTLDevice> device=MTLCreateSystemDefaultDevice();
    mtl=device;
    if (!device) { puts("GPU unavailable"); return 1; }
    queue=[device newCommandQueue];
    MTLTextureDescriptor *d=[MTLTextureDescriptor textureCubeDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm size:1024 mipmapped:YES];
    d.storageMode=MTLStorageModePrivate; d.usage=MTLTextureUsageShaderRead|MTLTextureUsageRenderTarget|MTLTextureUsagePixelFormatView;
    id<MTLTexture> cube=[device newTextureWithDescriptor:d];
    NSArray *views=envcube_views(cube);
    id<MTLRenderPipelineState> pipeline=envcube_pipeline(device,cube.pixelFormat);
    id<MTLCommandBuffer> cb=rt_command_buffer();
    // Seed every lower mip with magenta, so skipped work fails deterministically.
    for (unsigned l=1;l<11;++l) for (unsigned f=0;f<6;++f) {
        MTLRenderPassDescriptor *p=[MTLRenderPassDescriptor renderPassDescriptor];
        p.colorAttachments[0].texture=cube; p.colorAttachments[0].level=l; p.colorAttachments[0].slice=f;
        p.colorAttachments[0].loadAction=MTLLoadActionClear; p.colorAttachments[0].clearColor=MTLClearColorMake(1,0,1,1);
        p.colorAttachments[0].storeAction=MTLStoreActionStore;
        [[cb renderCommandEncoderWithDescriptor:p] endEncoding];
    }
    MTLTextureDescriptor *sd=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:1024 height:1024 mipmapped:NO];
    sd.storageMode=MTLStorageModeShared;
    id<MTLTexture> source=nil;
    uint32_t *pixels=malloc(1024*1024*4);
    id<MTLBlitCommandEncoder> upload=[cb blitCommandEncoder];
    // One shared upload image per face, retained by the real render-thread recorder.
    for (unsigned f=0;f<6;++f) {
        source=[device newTextureWithDescriptor:sd];
        for (unsigned y=0;y<1024;++y) for (unsigned x=0;x<1024;++x)
            pixels[y*1024+x]=0xFF000000u | (f*32u)<<16 | (((x+y)&1) ? 0xFFFFu:0);
        [source replaceRegion:MTLRegionMake2D(0,0,1024,1024) mipmapLevel:0 withBytes:pixels bytesPerRow:4096];
        [upload copyFromTexture:source sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(1024,1024,1) toTexture:cube destinationSlice:f destinationLevel:0 destinationOrigin:MTLOriginMake(0,0,0)];
    }
    [upload endEncoding]; free(pixels);
    unsigned n=native ? envcube_generate_blit(cb,cube,broken) : envcube_generate(cb,cube,views,pipeline,broken);
    id<MTLBuffer> first=envcube_readback(device,cb,cube);
    // Change every source texel, then generate again. A first-use-only cache or
    // an opcode reordered ahead of the second write leaves the checker stale.
    for (unsigned f=0;f<6;++f) {
        MTLRenderPassDescriptor *p=[MTLRenderPassDescriptor renderPassDescriptor];
        p.colorAttachments[0].texture=cube; p.colorAttachments[0].slice=f;
        p.colorAttachments[0].loadAction=MTLLoadActionClear;
        p.colorAttachments[0].clearColor=MTLClearColorMake(f*32.0/255.0,64.0/255.0,192.0/255.0,1);
        p.colorAttachments[0].storeAction=MTLStoreActionStore;
        [[cb renderCommandEncoderWithDescriptor:p] endEncoding];
    }
    n=native ? envcube_generate_blit(cb,cube,broken) : envcube_generate(cb,cube,views,pipeline,broken);
    if (stale) {
        // Restore a poison value in exactly one lower level, independently of
        // the generation counter: this must fail even though n remains 60.
        MTLRenderPassDescriptor *p=[MTLRenderPassDescriptor renderPassDescriptor];
        p.colorAttachments[0].texture=cube; p.colorAttachments[0].level=7; p.colorAttachments[0].slice=3;
        p.colorAttachments[0].loadAction=MTLLoadActionClear; p.colorAttachments[0].clearColor=MTLClearColorMake(1,0,1,1);
        p.colorAttachments[0].storeAction=MTLStoreActionStore;
        [[cb renderCommandEncoderWithDescriptor:p] endEncoding];
    }
    id<MTLBuffer> readback=envcube_readback(device,cb,cube);
    [cb commit]; [cb waitUntilCompleted];
    if (!rt_on && cb.error) { fprintf(stderr,"GPU command failed: %s\n",cb.error.localizedDescription.UTF8String); return 1; }
    unsigned bad=0,initial_bad=0;
    NSUInteger off=1024*1024*4*6;
    for (unsigned l=1;l<11;++l) {
        NSUInteger edge=1024>>l,row=envcube_row(edge);
        for (unsigned f=0;f<6;++f) {
            unsigned wrong=0,initial_wrong=0;
            for (NSUInteger y=0;y<edge;++y) for (NSUInteger x=0;x<edge;++x) {
                uint8_t *p=(uint8_t *)readback.contents+off+y*row+x*4;
                uint8_t *q=(uint8_t *)first.contents+off+y*row+x*4;
                wrong|=p[0]!=192 || p[1]!=64 || p[2]!=f*32 || p[3]!=255;
                initial_wrong|=abs((int)q[0]-128)>1 || abs((int)q[1]-128)>1 || q[2]!=f*32 || q[3]!=255;
            }
            bad+=wrong; initial_bad+=initial_wrong; off+=row*edge;
        }
    }
    printf("GPU %s generated=%u bad_samples=%u/60 initial_bad=%u/60 native=%d stale=%d\n", n==60 && !bad && !initial_bad ? "PASS":"FAIL",n,bad,initial_bad,native,stale);
    return n==60 && !bad && !initial_bad ? 0:1;
}

static int compare_patterns(void) {
    mtl=MTLCreateSystemDefaultDevice(); if (!mtl) return 1;
    queue=[mtl newCommandQueue];
    const char *scenes[]={"day","night","rain"};
    for (unsigned scene=0;scene<3;++scene) { @autoreleasepool {
        MTLTextureDescriptor *d=[MTLTextureDescriptor textureCubeDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm size:1024 mipmapped:YES];
        d.storageMode=MTLStorageModePrivate; d.usage=MTLTextureUsageShaderRead|MTLTextureUsageRenderTarget|MTLTextureUsagePixelFormatView;
        id<MTLTexture> cube=[mtl newTextureWithDescriptor:d];
        id<MTLRenderPipelineState> pso=envcube_pipeline(mtl,cube.pixelFormat);
        NSArray *views=envcube_views(cube);
        id<MTLCommandBuffer> cb=rt_command_buffer();
        MTLTextureDescriptor *sd=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:cube.pixelFormat width:1024 height:1024 mipmapped:NO];
        sd.storageMode=MTLStorageModeShared;
        uint8_t *pixels=malloc(1024*1024*4);
        id<MTLBlitCommandEncoder> upload=[cb blitCommandEncoder];
        uint32_t random=0x12345678;
        for (unsigned face=0;face<6;++face) {
            id<MTLTexture> source=[mtl newTextureWithDescriptor:sd];
            for (unsigned y=0;y<1024;++y) for (unsigned x=0;x<1024;++x) for (unsigned c=0;c<4;++c) {
                random=random*1664525+1013904223;
                unsigned v=random>>24;
                if (scene==1) v/=16;
                if (scene==2) v=(x%31<2) ? 255:v/2;
                pixels[(y*1024+x)*4+c]=(uint8_t)(c==3 ? 255:v);
            }
            [source replaceRegion:MTLRegionMake2D(0,0,1024,1024) mipmapLevel:0 withBytes:pixels bytesPerRow:4096];
            [upload copyFromTexture:source sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(1024,1024,1) toTexture:cube destinationSlice:face destinationLevel:0 destinationOrigin:MTLOriginMake(0,0,0)];
        }
        [upload endEncoding]; free(pixels);
        if (envcube_generate(cb,cube,views,pso,0)!=60) return 1;
        id<MTLBuffer> old=envcube_readback(mtl,cb,cube);
        envcube_poison_mips(cb,cube);
        if (envcube_generate_blit(cb,cube,0)!=60) return 1;
        id<MTLBuffer> native=envcube_readback(mtl,cb,cube);
        [cb commit]; [cb waitUntilCompleted];
        unsigned maxdiff=0; NSUInteger off=0;
        for (unsigned l=0;l<11;++l) {
            NSUInteger edge=1024>>l,row=envcube_row(edge);
            for (unsigned f=0;f<6;++f) {
                unsigned diff=envcube_max_difference((const uint8_t *)old.contents+off,(const uint8_t *)native.contents+off,edge,row);
                printf("COMPARE scene=%s format=BGRA8 face=%u mip=%u max=%u\n",scenes[scene],f,l,diff);
                if (!l && diff) return 1;
                if (diff>maxdiff) maxdiff=diff;
                off+=row*edge;
            }
        }
        printf("COMPARE %s scene=%s max=%u texels=8388606\n",maxdiff<=1 ? "PASS":"FAIL",scenes[scene],maxdiff);
        if (maxdiff>1) return 1;
    } }
    return 0;
}
int main(int argc,char **argv) { @autoreleasepool {
    int broken=0,native=0,stale=0;
    for (int i=2;i<argc;++i) { broken|=!strcmp(argv[i],"--broken"); native|=!strcmp(argv[i],"--blit"); stale|=!strcmp(argv[i],"--stale"); }
    if (argc>1 && !strcmp(argv[1],"--compare")) return compare_patterns();
    return argc>1 && !strcmp(argv[1],"--gpu") ? gpu(broken,native,stale) : counter(broken);
} }
