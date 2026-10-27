// Offline proof for police lamp-glow batching (lampbatch.m, render.m lamp_record/lamp_flush).
// A 4x MSAA scene (BGRA8 + Depth32Float_Stencil8) gets depth-writing quads and 24 overlapping additive flares
// (depth-tested, no depth write, texture-less, colour/positions from set*Bytes and an upload buffer reused at
// new offsets). The lamp target is built two ways:
//   split:   every flare ends the scene encoder, draws into the lamp target in its own encoder (the old path);
//   batched: every flare is recorded through LampRec while the scene encoder stays open, recordings invoked on
//            one lamp encoder before the next depth writer and at the end (render.m's flush points).
// The resolved lamp targets must be byte-identical. The broken variant (flush only at the end, ignoring the
// depth writer between flares) must differ: the check fails when the flush point is missing.
// Build/run: clang -fobjc-arc -framework Metal -framework Foundation runtime/lampbatch_test.m runtime/lampbatch.m -o /tmp/x && /tmp/x
#import <Metal/Metal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "lampbatch.h"

static const char *src =
    "#include <metal_stdlib>\nusing namespace metal;\n"
    "struct V { float4 p [[position]]; };\n"
    "vertex V vs(uint vid [[vertex_id]], const device float4 *pos [[buffer(0)]], constant float4 &ofs [[buffer(1)]]) {\n"
    "  V o; o.p = pos[vid] + float4(ofs.xy, 0, 0); o.p.z = ofs.z; return o; }\n"
    "fragment float4 fs(constant float4 &c [[buffer(0)]]) { return c; }\n";

enum { W = 256, H = 256, S = 4, NFLARE = 24 };
static id<MTLDevice> dev;
static id<MTLRenderPipelineState> pso_scene, pso_flare;
static id<MTLDepthStencilState> ds_write, ds_test;
static id<MTLBuffer> upload;
static uint32_t upload_off;

static id<MTLTexture> tex(MTLPixelFormat f, int samples) {
    MTLTextureDescriptor *d = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:f width:W height:H mipmapped:NO];
    d.textureType = samples > 1 ? MTLTextureType2DMultisample : MTLTextureType2D; d.sampleCount = samples;
    d.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead; d.storageMode = MTLStorageModePrivate;
    return [dev newTextureWithDescriptor:d];
}
static uint32_t rnd_state = 12345;
static float rnd(void) { rnd_state = rnd_state * 1664525u + 1013904223u; return (rnd_state >> 8) / 16777216.0f; }

typedef struct { float x0, y0, x1, y1, z, r, g, b, a; } Quad;
static void draw_quad(id<MTLRenderCommandEncoder> e, const Quad *q, int flare) {
    float *p = (float *)((char *)upload.contents + upload_off);   // the "ring": a new offset per draw
    float v[6][4] = {{q->x0, q->y0, 0, 1}, {q->x1, q->y0, 0, 1}, {q->x0, q->y1, 0, 1}, {q->x1, q->y0, 0, 1}, {q->x1, q->y1, 0, 1}, {q->x0, q->y1, 0, 1}};
    memcpy(p, v, sizeof v);
    [e setRenderPipelineState:flare ? pso_flare : pso_scene];
    [e setDepthStencilState:flare ? ds_test : ds_write];
    [e setViewport:(MTLViewport){0, 0, W, H, 0, 1}];
    [e setScissorRect:(MTLScissorRect){0, 0, W, H}];
    [e setCullMode:MTLCullModeNone];
    [e setVertexBuffer:upload offset:upload_off atIndex:0];
    float ofs[4] = {0, 0, q->z, 0};
    [e setVertexBytes:ofs length:sizeof ofs atIndex:1];
    float c[4] = {q->r, q->g, q->b, q->a};
    [e setFragmentBytes:c length:sizeof c atIndex:0];
    // clobber the stack copies: a recorder that kept the pointers would replay garbage
    memset(ofs, 0x7f, sizeof ofs); memset(c, 0x7f, sizeof c);
    [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:6];
    upload_off += 256;
}
static MTLRenderPassDescriptor *pass(id<MTLTexture> col, id<MTLTexture> res, id<MTLTexture> z, int clear_col, int clear_z) {
    MTLRenderPassDescriptor *rp = [MTLRenderPassDescriptor renderPassDescriptor];
    rp.colorAttachments[0].texture = col; rp.colorAttachments[0].resolveTexture = res;
    rp.colorAttachments[0].loadAction = clear_col ? MTLLoadActionClear : MTLLoadActionLoad;
    rp.colorAttachments[0].clearColor = MTLClearColorMake(0, 0, 0, 0);
    rp.colorAttachments[0].storeAction = MTLStoreActionStoreAndMultisampleResolve;
    rp.depthAttachment.texture = z; rp.depthAttachment.loadAction = clear_z ? MTLLoadActionClear : MTLLoadActionLoad;
    rp.depthAttachment.clearDepth = 1; rp.depthAttachment.storeAction = MTLStoreActionStore;
    rp.stencilAttachment.texture = z; rp.stencilAttachment.loadAction = clear_z ? MTLLoadActionClear : MTLLoadActionLoad;
    rp.stencilAttachment.storeAction = MTLStoreActionStore;
    return rp;
}

// mode 0 split, 1 batched (flush before depth writers), 2 broken batched (flush only at the end)
static void run(int mode, const Quad *scene, int nscene, const Quad *fl, const int *writer_after, uint8_t *out, unsigned *encoders) {
    id<MTLTexture> sc = tex(MTLPixelFormatBGRA8Unorm, S), scr = tex(MTLPixelFormatBGRA8Unorm, 1);
    id<MTLTexture> z = tex(MTLPixelFormatDepth32Float_Stencil8, S);
    id<MTLTexture> lp = tex(MTLPixelFormatBGRA8Unorm, S), lpr = tex(MTLPixelFormatBGRA8Unorm, 1);
    id<MTLCommandQueue> q = [dev newCommandQueue];
    id<MTLCommandBuffer> cb = [q commandBuffer];
    upload_off = 0;
    *encoders = 0;
    int scene_cleared = 0, lamp_cleared = 0;
    id rec = lamprec_new();
    unsigned pending = 0;
    id<MTLRenderCommandEncoder> e = nil;
#define OPEN_SCENE() do { if (!e) { e = [cb renderCommandEncoderWithDescriptor:pass(sc, scr, z, !scene_cleared, !scene_cleared)]; scene_cleared = 1; ++*encoders; } } while (0)
#define CLOSE() do { if (e) { [e endEncoding]; e = nil; } } while (0)
#define FLUSH() do { if (pending) { CLOSE(); id<MTLRenderCommandEncoder> l = [cb renderCommandEncoderWithDescriptor:pass(lp, lpr, z, !lamp_cleared, 0)]; \
        lamp_cleared = 1; ++*encoders; lamprec_replay(rec, l); [l endEncoding]; pending = 0; } } while (0)
    OPEN_SCENE();
    for (int i = 0; i < nscene; ++i) draw_quad(e, &scene[i], 0);
    for (int i = 0; i < NFLARE; ++i) {
        OPEN_SCENE();
        draw_quad(e, &fl[i], 1);                                   // the flare in the scene
        if (mode == 0) {
            CLOSE();
            id<MTLRenderCommandEncoder> l = [cb renderCommandEncoderWithDescriptor:pass(lp, lpr, z, !lamp_cleared, 0)];
            lamp_cleared = 1; ++*encoders;
            draw_quad(l, &fl[i], 1);
            [l endEncoding];
        } else { draw_quad((id<MTLRenderCommandEncoder>)rec, &fl[i], 1); ++pending; }
        if (writer_after[i] >= 0) {                                // a depth writer between flares
            if (mode == 1) FLUSH();
            OPEN_SCENE();
            draw_quad(e, &scene[writer_after[i]], 0);
        }
    }
    CLOSE();
    FLUSH();
    if (lamprec_refused(rec)) { fprintf(stderr, "recorder refused messages\n"); exit(2); }
    id<MTLBuffer> rb = [dev newBufferWithLength:W * H * 4 options:MTLResourceStorageModeShared];
    id<MTLBlitCommandEncoder> b = [cb blitCommandEncoder];
    [b copyFromTexture:lpr sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0, 0, 0) sourceSize:MTLSizeMake(W, H, 1)
              toBuffer:rb destinationOffset:0 destinationBytesPerRow:W * 4 destinationBytesPerImage:W * H * 4];
    [b endEncoding];
    [cb commit]; [cb waitUntilCompleted];
    if (cb.error) { fprintf(stderr, "command buffer error: %s\n", cb.error.localizedDescription.UTF8String); exit(2); }
    memcpy(out, rb.contents, W * H * 4);
}

int main(void) {
    @autoreleasepool {
        dev = MTLCreateSystemDefaultDevice();
        if (!dev) { fprintf(stderr, "no Metal device\n"); return 2; }
        NSError *err = nil;
        id<MTLLibrary> lib = [dev newLibraryWithSource:@(src) options:nil error:&err];
        if (!lib) { fprintf(stderr, "%s\n", err.localizedDescription.UTF8String); return 2; }
        MTLRenderPipelineDescriptor *pd = [MTLRenderPipelineDescriptor new];
        pd.vertexFunction = [lib newFunctionWithName:@"vs"]; pd.fragmentFunction = [lib newFunctionWithName:@"fs"];
        pd.rasterSampleCount = S; pd.colorAttachments[0].pixelFormat = MTLPixelFormatBGRA8Unorm;
        pd.depthAttachmentPixelFormat = pd.stencilAttachmentPixelFormat = MTLPixelFormatDepth32Float_Stencil8;
        pso_scene = [dev newRenderPipelineStateWithDescriptor:pd error:&err];
        pd.colorAttachments[0].blendingEnabled = YES;                 // D3D SRCALPHA, ONE, ADD (the flare state)
        pd.colorAttachments[0].sourceRGBBlendFactor = pd.colorAttachments[0].sourceAlphaBlendFactor = MTLBlendFactorSourceAlpha;
        pd.colorAttachments[0].destinationRGBBlendFactor = pd.colorAttachments[0].destinationAlphaBlendFactor = MTLBlendFactorOne;
        pso_flare = [dev newRenderPipelineStateWithDescriptor:pd error:&err];
        MTLDepthStencilDescriptor *dd = [MTLDepthStencilDescriptor new];
        dd.depthCompareFunction = MTLCompareFunctionLessEqual; dd.depthWriteEnabled = YES;
        ds_write = [dev newDepthStencilStateWithDescriptor:dd];
        dd.depthWriteEnabled = NO;
        ds_test = [dev newDepthStencilStateWithDescriptor:dd];
        upload = [dev newBufferWithLength:1 << 20 options:MTLResourceStorageModeShared];

        Quad scene[8], fl[NFLARE];
        int writer_after[NFLARE];
        for (int i = 0; i < 8; ++i) {
            float x = rnd() * 1.6f - 1, y = rnd() * 1.6f - 1;
            scene[i] = (Quad){x, y, x + 0.2f + rnd() * 0.5f, y + 0.2f + rnd() * 0.5f, 0.2f + rnd() * 0.7f, rnd(), rnd(), rnd(), 1};
        }
        for (int i = 0; i < NFLARE; ++i) {
            float x = rnd() * 1.4f - 0.9f, y = rnd() * 1.4f - 0.9f, s = 0.15f + rnd() * 0.5f;
            fl[i] = (Quad){x, y, x + s, y + s, 0.1f + rnd() * 0.8f, rnd(), rnd() * 0.3f, rnd(), 0.2f + rnd() * 0.8f};
            writer_after[i] = -1;
        }
        writer_after[7] = 4; writer_after[15] = 5; writer_after[20] = 6;   // depth writers after flares 7, 15, 20
        static uint8_t a[W * H * 4], bt[W * H * 4], br[W * H * 4];
        unsigned ea, eb_, ebr;
        run(0, scene, 4, fl, writer_after, a, &ea);
        run(1, scene, 4, fl, writer_after, bt, &eb_);
        run(2, scene, 4, fl, writer_after, br, &ebr);
        unsigned diff_b = 0, diff_broken = 0, lit = 0;
        for (int i = 0; i < W * H * 4; ++i) { diff_b += a[i] != bt[i]; diff_broken += a[i] != br[i]; }
        for (int i = 0; i < W * H; ++i) lit += a[4 * i] | a[4 * i + 1] | a[4 * i + 2] ? 1 : 0;
        printf("lampbatch_test: %d flares, 3 depth writers between them; lit lamp px %u of %u\n", NFLARE, lit, W * H);
        printf("  split   %3u encoders\n  batched %3u encoders, bytes differing from split %u\n  broken  %3u encoders (flush only at end), bytes differing %u\n",
               ea, eb_, diff_b, ebr, diff_broken);
        int ok = lit > 1000 && diff_b == 0 && diff_broken > 0 && eb_ < ea;
        printf("%s\n", ok ? "PASS" : "FAIL");
        return ok ? 0 : 1;
    }
}
