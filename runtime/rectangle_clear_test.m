// Production rectangle-clear helpers + render_clear are extracted by rectangle_clear_test.py.
// 64x32 target: top-half case expects 1024 changed and 1024 untouched pixels for each of C/Z/S.
// GPU matrix: 12 cases x 2 sample counts (1/4) x 2 paths (direct/real render-thread replay), 98304 pixels.
// Broken control ignores rect/viewport/scissor: top-half case must have 1024 bad pixels per path/sample count.
// Review CPU: 15 checks pass (four exact SP entry snapshots); unscoped bounds fails check 11.
// Previous GPU evidence below predates the split-only dispatch guard.
                                                                                                                                                                                                                                             
#import <Metal/Metal.h>
#include <mach/mach_time.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "d3d9_internal.h"
#include "rectangle_helpers.inc"

Device dev;
id<MTLDevice> mtl;
id<MTLCommandQueue> queue;
int diag_on;
uint8_t *G;
uint64_t host_ticks(void) { return clock_gettime_nsec_np(CLOCK_UPTIME_RAW); }
void diag_event(const char *fmt, ...) { (void)fmt; }
id<MTLCommandBuffer> rt_command_buffer(void);
extern int rt_on;

enum { W = 64, H = 32 };
static id<MTLTexture> col, depth, col_res, depth_res;
static id<MTLCommandBuffer> cmd;
static id<MTLRenderCommandEncoder> enc;
static uint32_t pending_clear_flags, pending_clear_color, pending_clear_stencil, enc_samples, q_active;
static int soft_depth_valid;
static float pending_clear_z;
static struct { int have; } eb;
static int lamp_n, lamp_flushes, query_restores, pass_count, cpu_entry;
static uint32_t observed[5];
@interface CPUClearTexture:NSObject
- (NSUInteger)width; - (NSUInteger)height;
@end
@implementation CPUClearTexture
- (NSUInteger)width{return W;}
- (NSUInteger)height{return H;}
@end
static void lamp_flush(int why) { (void)why; ++lamp_flushes; lamp_n = 0; }
static void query_segment(void) { ++query_restores; }
static id<MTLTexture> surface_target(uint32_t s, NSUInteger *lv, NSUInteger *sl) { *lv = *sl = 0; return s == 1 ? col : s == 2 ? depth : nil; }
static id<MTLTexture> pass_depth(id<MTLTexture> c, id<MTLTexture> z) { (void)c; return z; }
static void mf_pass_log(const char *s, uint32_t w, uint32_t h) { (void)s; (void)w; (void)h; }
static void end_pass(void) { if (enc) [enc endEncoding]; enc = nil; }
static void begin_pass(void) {
    if(cpu_entry){
        ++pass_count;observed[0]=pending_clear_flags;observed[1]=pending_clear_color;
        memcpy(&observed[2],&pending_clear_z,4);observed[3]=pending_clear_stencil;
        observed[4]=pass_count;pending_clear_flags=0;return;
    }
    if (enc && !pending_clear_flags) return;
    end_pass();
    MTLRenderPassDescriptor *rp = [MTLRenderPassDescriptor renderPassDescriptor];
    rp.colorAttachments[0].texture = col;
    rp.colorAttachments[0].loadAction = pending_clear_flags & 1 ? MTLLoadActionClear : MTLLoadActionLoad;
    uint32_t c = pending_clear_color;
    rp.colorAttachments[0].clearColor = MTLClearColorMake(((c >> 16) & 255)/255., ((c >> 8) & 255)/255., (c & 255)/255., (c >> 24)/255.);
    rp.colorAttachments[0].storeAction = MTLStoreActionStore;
    rp.depthAttachment.texture = depth;
    rp.depthAttachment.loadAction = pending_clear_flags & 2 ? MTLLoadActionClear : MTLLoadActionLoad;
    rp.depthAttachment.clearDepth = pending_clear_z;
    rp.depthAttachment.storeAction = MTLStoreActionStore;
    rp.stencilAttachment.texture = depth;
    rp.stencilAttachment.loadAction = pending_clear_flags & 4 ? MTLLoadActionClear : MTLLoadActionLoad;
    rp.stencilAttachment.clearStencil = pending_clear_stencil;
    rp.stencilAttachment.storeAction = MTLStoreActionStore;
    if (enc_samples > 1) {
        rp.colorAttachments[0].resolveTexture = col_res;
        rp.colorAttachments[0].storeAction = MTLStoreActionStoreAndMultisampleResolve;
        rp.depthAttachment.resolveTexture = depth_res;
        rp.depthAttachment.storeAction = MTLStoreActionStoreAndMultisampleResolve;
        rp.depthAttachment.depthResolveFilter = MTLMultisampleDepthResolveFilterSample0;
        rp.stencilAttachment.resolveTexture = depth_res;
        rp.stencilAttachment.storeAction = MTLStoreActionStoreAndMultisampleResolve;
        rp.stencilAttachment.stencilResolveFilter = MTLMultisampleStencilResolveFilterSample0;
    }
    pending_clear_flags = 0;
    enc = [cmd renderCommandEncoderWithDescriptor:rp]; ++pass_count;
}
#include "rectangle_entry.inc"

static id<MTLTexture> texture(MTLPixelFormat pf, unsigned samples) {
    MTLTextureDescriptor *d = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:pf width:W height:H mipmapped:NO];
    d.sampleCount = samples; d.textureType = samples > 1 ? MTLTextureType2DMultisample : MTLTextureType2D;
    d.storageMode = MTLStorageModePrivate; d.usage = MTLTextureUsageRenderTarget;
    return [mtl newTextureWithDescriptor:d];
}
static int cpu_checks(void) {
    uint32_t vp[4] = {0, 0, W, H}, sc[4] = {0, 0, W, H};
    int n = 0;
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "CPU check %d failed\n", n + 1); return 1; } ++n; } while (0)
    ClearRect r = clear_bounds(W,H,vp,0,sc);
    CHECK(clear_full(r,W,H));
    int32_t rect[4] = {-10,-10,W+10,H/2}; r = clear_rect(r,rect);
    CHECK(r.x0 == 0 && r.y0 == 0 && r.x1 == W && r.y1 == H/2);
    CHECK(!clear_full(r,W,H));
    vp[1] = H/2; vp[3] = H/2; r = clear_bounds(W,H,vp,0,sc);
    CHECK(r.y0 == H/2 && r.y1 == H);
    CHECK(!clear_nonempty(clear_rect(r,rect)));
    vp[0] = UINT32_MAX; vp[2] = 10;
    CHECK(!clear_nonempty(clear_bounds(W,H,vp,0,sc)));
    vp[0] = 0; vp[2] = W; sc[0] = (uint32_t)-4; sc[1] = H/2+2; sc[2] = W/2; sc[3] = H+10;
    r = clear_bounds(W,H,vp,1,sc);
    CHECK(r.x0 == 0 && r.x1 == W/2 && r.y0 == H/2+2 && r.y1 == H);
    sc[2] = 0; CHECK(!clear_nonempty(clear_bounds(W,H,vp,1,sc)));
    rect[0]=12; rect[2]=8; CHECK(!clear_nonempty(clear_rect((ClearRect){0,0,W,H},rect)));
    rect[0]=0; rect[2]=W; rect[1]=rect[3]=4; CHECK(!clear_nonempty(clear_rect((ClearRect){0,0,W,H},rect)));
    cpu_entry=1;mtl=(id)[NSObject new];col=(id)[CPUClearTexture new];G=calloc(1,128);dev.rt[0]=1;
    dev.viewport[0]=1;dev.viewport[1]=1;dev.viewport[2]=W-2;dev.viewport[3]=H-2;
    dev.rs[174]=1;memset(dev.scissor,0,16); // empty scissor would skip the clear under split semantics
    uint32_t expected[5]={7,0xff12ab34,0x3e800000,19,1};
    for(unsigned mode=0;mode<4;++mode){
        if(!mode)unsetenv("NFS_PADS");else setenv("NFS_PADS",mode==1?"1":mode==2?"invalid":"02",1);
        pass_count=0;memset(observed,0,sizeof observed);
        render_clear(2,16,7,expected[1],.25f,19);
        CHECK(!memcmp(expected,observed,sizeof expected));
    }
    setenv("NFS_PADS","2",1);pass_count=0;render_clear(0,0,7,0,.25f,19);CHECK(pass_count==0);
    cpu_entry=0;mtl=nil;col=nil;free(G);G=NULL;
    printf("CPU PASS %d clipping/full/empty/overflow and SP clear snapshots\n", n);
    return 0;
}

// Expected regions are deliberately specified independently of the production clipping functions.
static int inside(int test, int x, int y) {
    switch (test) {
    case 0: case 5: case 6: case 7: return y < H/2;
    case 1: return y >= H/2;
    case 2: return x >= 8 && x < W-8 && y >= 3 && y < H/2;
    case 3: return (x < W/4 && y < H/4) || (x >= W*3/4 && y >= H*3/4);
    case 4: return 0;
    case 8: return 1;
    case 9: return x < W/2 && y < H/2;
    case 10: return 0;
    case 11: return y < H/2;
    }
    abort();
}
static unsigned gpu_case(int test, unsigned samples, int replay) {
    col = texture(MTLPixelFormatBGRA8Unorm,samples); depth = texture(MTLPixelFormatDepth32Float_Stencil8,samples);
    col_res = samples > 1 ? texture(MTLPixelFormatBGRA8Unorm,1) : col;
    depth_res = samples > 1 ? texture(MTLPixelFormatDepth32Float_Stencil8,1) : depth;
    if (!col || !depth || !col_res || !depth_res) abort();
    cmd = replay ? rt_command_buffer() : [queue commandBuffer];
    enc_samples = samples; pass_count = 0;
    memset(&dev,0,sizeof dev); dev.rt[0]=1; dev.ds=2;
    dev.viewport[2]=W; dev.viewport[3]=H;
    render_clear(0,0,7,0xffff0000,.75f,3); // red + far depth + stencil sentinel
    int initial_passes = pass_count;
    int32_t *rect = (int32_t *)(G+16);
    memcpy(rect,(int32_t[]){0,0,W,H/2},16);
    unsigned count=1, flags = test == 5 ? 1 : test == 6 ? 2 : test == 7 ? 4 : 7;
    if (test == 1) { count=0; dev.viewport[1]=H/2; dev.viewport[3]=H/2; }
    if (test == 2) {
        memcpy(rect,(int32_t[]){-9,-9,W+9,H+9},16);
        dev.viewport[0]=4; dev.viewport[1]=2; dev.viewport[2]=W-8; dev.viewport[3]=H-4;
        dev.rs[174]=1; memcpy(dev.scissor,(uint32_t[]){8,3,W-8,H/2},16);
    }
    if (test == 3) { count=3; memcpy(rect,(int32_t[]){0,0,W/4,H/4, W*3/4,H*3/4,W+5,H+5, 10,10,4,4},48); }
    if (test == 4) memcpy(rect,(int32_t[]){W+1,0,W+8,H},16);
    if (test == 8) count=0;
    if (test == 9) { count=0; dev.rs[174]=1; memcpy(dev.scissor,(uint32_t[]){0,0,W/2,H/2},16); }
    if (test == 10) memcpy(rect,(int32_t[]){0,0,0,H},16);
    // Exercise inline constant copies, pending-lamp ordering and encoder cache invalidation.
    lamp_n = test == 11; lamp_flushes = query_restores = 0; q_active = test == 11;
    eb.have=123;
    render_clear(count,16,flags,0xff00ff00,.25f,9);
    if (test == 11 && (lamp_flushes != 1 || query_restores != 1 || eb.have)) abort();
    if (test == 8 && pass_count != initial_passes+1) abort(); // full clear still ends/starts the pass
    memset(G+16,0x7f,64); // recorder must own scissors and both constant byte payloads
    q_active=0;
    end_pass();
    id<MTLBuffer> cb = [mtl newBufferWithLength:W*H*4 options:MTLResourceStorageModeShared];
    id<MTLBuffer> zb = [mtl newBufferWithLength:W*H*4 options:MTLResourceStorageModeShared];
    id<MTLBuffer> sb = [mtl newBufferWithLength:W*H*4 options:MTLResourceStorageModeShared];
    id<MTLBlitCommandEncoder> b = [cmd blitCommandEncoder];
    [b copyFromTexture:col_res sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(W,H,1) toBuffer:cb destinationOffset:0 destinationBytesPerRow:W*4 destinationBytesPerImage:W*H*4];
    [b copyFromTexture:depth_res sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(W,H,1) toBuffer:zb destinationOffset:0 destinationBytesPerRow:W*4 destinationBytesPerImage:W*H*4 options:MTLBlitOptionDepthFromDepthStencil];
    [b copyFromTexture:depth_res sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(W,H,1) toBuffer:sb destinationOffset:0 destinationBytesPerRow:256 destinationBytesPerImage:256*H options:MTLBlitOptionStencilFromDepthStencil];
    [b endEncoding];
    __block NSError *error = nil;
    [cmd addCompletedHandler:^(id<MTLCommandBuffer> done) { error = done.error; }];
    [cmd commit]; [cmd waitUntilCompleted];
    if (error) { fprintf(stderr,"GPU error: %s\n",error.localizedDescription.UTF8String); abort(); }
    unsigned bad=0;
    const uint8_t *c=cb.contents, *s=sb.contents; const float *z=zb.contents;
    for (int y=0;y<H;++y) for (int x=0;x<W;++x) {
        int i=y*W+x, hit=inside(test,x,y);
        int cg=hit && (flags&1), zg=hit && (flags&2), sg=hit && (flags&4);
        bad += c[4*i] != 0 || c[4*i+1] != (cg?255:0) || c[4*i+2] != (cg?0:255) || c[4*i+3] != 255 || z[i] != (zg?.25f:.75f) || s[y*256+x] != (sg?9:3);
    }
    if (bad) printf("GPU FAIL case=%d samples=%u replay=%d bad_pixels=%u/%d\n",test,samples,replay,bad,W*H);
    return bad;
}
int main(int argc, char **argv) { @autoreleasepool {
    if (argc == 3 && !strcmp(argv[1],"--sources")) {
        for (unsigned i=0;i<16;++i) [clear_source(i) writeToFile:[NSString stringWithFormat:@"%s/clear%u.metal",argv[2],i] atomically:YES encoding:NSUTF8StringEncoding error:NULL];
        return 0;
    }
    if (!(argc == 2 && !strcmp(argv[1],"--broken")) && cpu_checks()) return 1;
    if (argc == 2 && !strcmp(argv[1],"--cpu")) return 0;
    setenv("NFS_PADS","2",1);
    mtl = MTLCreateSystemDefaultDevice(); if (!mtl) { fprintf(stderr,"GPU unavailable\n"); return 2; }
    queue = [mtl newCommandQueue]; G=calloc(1,128);
    unsigned bad=0, cases=0;
    for (int replay=0;replay<2;++replay) for (unsigned samples=1;samples<=4;samples+=3) {
        if (![mtl supportsTextureSampleCount:samples]) { fprintf(stderr,"required sample count unavailable\n"); return 2; }
        for (int test=0;test<(argc == 2 && !strcmp(argv[1],"--broken") ? 1 : 12);++test) { bad+=gpu_case(test,samples,replay); ++cases; }
    }
    printf("GPU %s %u cases, %u pixels, bad_pixels=%u (colour/depth/stencil; direct/replay; 1x/4x)\n",bad?"FAIL":"PASS",cases,cases*W*H,bad);
    return bad != 0;
} }
