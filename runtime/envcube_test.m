// Production counter control: 1024 cube, 11 mips => 60 completed mip-face passes;
// deliberately omitted generation => 0 (FAIL). Both numbers checked by the driver.
// Default regression: unset => 1024/11; old PC-default mutant => 256/1 (FAIL).
                                                                                     
// must average the base checkerboard; broken generation leaves 60 magenta samples.
#include <stdio.h>
#include <stdarg.h>
#include <time.h>
#import <Metal/Metal.h>
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
@property NSUInteger mipmapLevelCount;
- (id)renderCommandEncoderWithDescriptor:(id)d;
- (void)setRenderPipelineState:(id)p;
- (void)setFragmentTexture:(id)t atIndex:(NSUInteger)i;
- (void)drawPrimitives:(MTLPrimitiveType)t vertexStart:(NSUInteger)s vertexCount:(NSUInteger)n;
- (void)endEncoding;
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
@end

// render_thread.m's only external dependencies (the test uses the real recorder).
id<MTLCommandQueue> queue;
id<MTLDevice> mtl;
Device dev;
uint8_t *G;
id<MTLCommandBuffer> cmd;
int diag_on;
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

static void sharp_hooks(void) {
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
        if (c.count!=60 || state->generations!=1 || state->mip_passes!=60 || state->pending) abort();
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
    if (ended_passes!=3) abort();
    puts("SHARP PASS main/P1/P2: 3 completed captures, 180 mip-face passes; scoped sampler restored");
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
    sharp_hooks(); free(G); G=NULL;
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
    return n==60 && c.count==60 ? 0:1;
}
static int gpu(int broken) {
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
    unsigned n=envcube_generate(cb,cube,views,pipeline,broken);
    id<MTLBuffer> readback=[device newBufferWithLength:60*256 options:MTLResourceStorageModeShared];
    id<MTLBlitCommandEncoder> b=[cb blitCommandEncoder];
    for (unsigned l=1;l<11;++l) for (unsigned f=0;f<6;++f)
        [b copyFromTexture:cube sourceSlice:f sourceLevel:l sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(1,1,1) toBuffer:readback destinationOffset:((l-1)*6+f)*256 destinationBytesPerRow:256 destinationBytesPerImage:256 options:MTLBlitOptionNone];
    [b endEncoding]; [cb commit]; [cb waitUntilCompleted];
    if (!rt_on && cb.error) { fprintf(stderr,"GPU command failed: %s\n",cb.error.localizedDescription.UTF8String); return 1; }
    unsigned bad=0;
    for (unsigned i=0;i<60;++i) {
        uint8_t *p=(uint8_t *)readback.contents+i*256;
        bad += abs((int)p[0]-128)>1 || abs((int)p[1]-128)>1 || p[2]!=(i%6)*32 || p[3]!=255;
    }
    printf("GPU %s generated=%u bad_samples=%u/60\n", n==60 && !bad ? "PASS":"FAIL",n,bad);
    return n==60 && !bad ? 0:1;
}
int main(int argc,char **argv) { @autoreleasepool {
    int broken=argc>2 && !strcmp(argv[2],"--broken");
    return argc>1 && !strcmp(argv[1],"--gpu") ? gpu(broken) : counter(broken);
} }
