// Offline check + timing of the lamp-source pass (render_post.m lp_source): the shipped shader against the old one at
// 3840x2160 BGRA8 (the game's scene and lamp target formats), the scene a noisy bright image, the lamp target empty
// except for N small flare blobs. Expect: identical 960x540 RGBA16F bytes in both modes (glare = Natural, and Glow),
// and a deliberately broken variant (returns 0 where the 16 lamp taps sum < 0.05) differing. Prints GPU ms per pass for each.
// Run: tools/.venv/bin/python -I runtime/lampsrc_bench.py   (extracts the shader sources, builds and runs this)
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
typedef struct { float off[16][4], wgt[16][4], p0[4]; } K;
static id<MTLDevice> gpu; static id<MTLCommandQueue> queue; static id<MTLSamplerState> smp;
static id<MTLRenderPipelineState> pso(NSString *path, int broken) {
    NSString *src = [NSString stringWithContentsOfFile:path encoding:NSUTF8StringEncoding error:nil];
    if (broken) src = [src stringByReplacingOccurrencesOfString:@"lsum == 0.0" withString:@"lsum < 0.05"];
    NSError *err = nil; id<MTLLibrary> lib = [gpu newLibraryWithSource:src options:nil error:&err];
    if (!lib) { fprintf(stderr, "%s\n", err.localizedDescription.UTF8String); exit(1); }
    MTLRenderPipelineDescriptor *d = [MTLRenderPipelineDescriptor new];
    d.vertexFunction = [lib newFunctionWithName:@"vs_full"]; d.fragmentFunction = [lib newFunctionWithName:@"fs_lamp_source"];
    d.colorAttachments[0].pixelFormat = MTLPixelFormatRGBA16Float;
    id<MTLRenderPipelineState> p = [gpu newRenderPipelineStateWithDescriptor:d error:&err];
    if (!p) { fprintf(stderr, "%s\n", err.localizedDescription.UTF8String); exit(1); }
    return p;
}
static id<MTLTexture> tex(MTLPixelFormat f, int w, int h) {
    MTLTextureDescriptor *d = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:f width:w height:h mipmapped:NO];
    d.usage = MTLTextureUsageShaderRead | MTLTextureUsageRenderTarget; d.storageMode = MTLStorageModeShared;
    return [gpu newTextureWithDescriptor:d];
}
static double run(id<MTLRenderPipelineState> p, id<MTLTexture> scene, id<MTLTexture> lamp, id<MTLTexture> out, K *k, int reps) {
    double best = 1e9;
    for (int t = 0; t < 7; ++t) {
        id<MTLCommandBuffer> c = [queue commandBuffer];
        for (int r = 0; r < reps; ++r) {
            MTLRenderPassDescriptor *rp = [MTLRenderPassDescriptor renderPassDescriptor];
            rp.colorAttachments[0].texture = out; rp.colorAttachments[0].loadAction = MTLLoadActionDontCare; rp.colorAttachments[0].storeAction = MTLStoreActionStore;
            id<MTLRenderCommandEncoder> e = [c renderCommandEncoderWithDescriptor:rp];
            [e setRenderPipelineState:p]; [e setFragmentTexture:scene atIndex:0]; [e setFragmentTexture:lamp atIndex:1];
            [e setFragmentSamplerState:smp atIndex:0]; [e setFragmentBytes:k length:sizeof *k atIndex:0];
            [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3]; [e endEncoding];
        }
        [c commit]; [c waitUntilCompleted];
        double ms = (c.GPUEndTime - c.GPUStartTime) * 1000 / reps; if (ms < best) best = ms;
    }
    return best;
}
int main(int argc, char **argv) { @autoreleasepool {
    gpu = MTLCreateSystemDefaultDevice(); queue = [gpu newCommandQueue];
    MTLSamplerDescriptor *sd = [MTLSamplerDescriptor new]; sd.minFilter = sd.magFilter = MTLSamplerMinMagFilterLinear;
    sd.sAddressMode = sd.tAddressMode = MTLSamplerAddressModeClampToEdge; smp = [gpu newSamplerStateWithDescriptor:sd];
    id<MTLRenderPipelineState> old = pso(@(argv[1]), 0), cur = pso(@(argv[2]), 0), bad = pso(@(argv[2]), 1);
    const int W = 3840, H = 2160, ow = 960, oh = 540; int nblobs = argc > 3 ? atoi(argv[3]) : 16;
    id<MTLTexture> scene = tex(MTLPixelFormatBGRA8Unorm, W, H), lamp = tex(MTLPixelFormatBGRA8Unorm, W, H);
    id<MTLTexture> o0 = tex(MTLPixelFormatRGBA16Float, ow, oh), o1 = tex(MTLPixelFormatRGBA16Float, ow, oh), o2 = tex(MTLPixelFormatRGBA16Float, ow, oh);
    uint8_t *sp = malloc((size_t)W * H * 4), *lp = calloc((size_t)W * H * 4, 1); unsigned rng = 7;
    for (size_t i = 0; i < (size_t)W * H; ++i) {
        rng = rng * 1664525u + 1013904223u; int x = i % W, y = i / W; int v = ((x / 64 + y / 64) & 1) ? 150 : 60;
        for (int c = 0; c < 3; ++c) { rng = rng * 1664525u + 1013904223u; int q = v + (int)(rng >> 26) - 32 + c * 10; sp[i * 4 + c] = q < 0 ? 0 : q > 255 ? 255 : q; }
        sp[i * 4 + 3] = 255;
    }
    for (int b = 0; b < nblobs; ++b) {            // flare blobs: bright discs (scene is bright there too, as the flare was added to it)
        rng = rng * 1664525u + 1013904223u; int cx = 100 + (rng >> 8) % (W - 200);
        rng = rng * 1664525u + 1013904223u; int cy = 100 + (rng >> 8) % (H - 200); int rad = 6 + b % 14;
        for (int y = -rad; y <= rad; ++y) for (int x = -rad; x <= rad; ++x) {
            int d2 = x * x + y * y; if (d2 > rad * rad) continue; int a = 255 - 230 * d2 / (rad * rad + 1);
            size_t i = ((size_t)(cy + y) * W + cx + x) * 4; lp[i + 2] = a; lp[i + 1] = a / 3; lp[i] = a / 5;     // BGRA
            for (int c = 0; c < 3; ++c) { int q = sp[i + c] + lp[i + c]; sp[i + c] = q > 255 ? 255 : q; }
        }
    }
    [scene replaceRegion:MTLRegionMake2D(0, 0, W, H) mipmapLevel:0 withBytes:sp bytesPerRow:W * 4];
    [lamp replaceRegion:MTLRegionMake2D(0, 0, W, H) mipmapLevel:0 withBytes:lp bytesPerRow:W * 4];
    int fails = 0;
    for (int glare = 1; glare >= 0; --glare) {
        K k; memset(&k, 0, sizeof k);
        for (int y = 0; y < 4; ++y) for (int x = 0; x < 4; ++x) { k.off[y * 4 + x][0] = (x - 1.5f) / ow; k.off[y * 4 + x][1] = (y - 1.5f) / oh; }
        k.p0[0] = 0.6f; k.p0[1] = glare; k.p0[2] = glare ? 0.35f : 1.2f;
        k.wgt[0][0] = 0.5f; k.wgt[0][1] = 0.3f; k.wgt[0][2] = 0.1f; k.wgt[0][3] = 0.5f;
        double t0 = run(old, scene, lamp, o0, &k, 20), t1 = run(cur, scene, lamp, o1, &k, 20); run(bad, scene, lamp, o2, &k, 1);
        size_t n = (size_t)ow * oh * 8; uint8_t *a = malloc(n), *b = malloc(n), *c = malloc(n);
        [o0 getBytes:a bytesPerRow:ow * 8 fromRegion:MTLRegionMake2D(0, 0, ow, oh) mipmapLevel:0];
        [o1 getBytes:b bytesPerRow:ow * 8 fromRegion:MTLRegionMake2D(0, 0, ow, oh) mipmapLevel:0];
        [o2 getBytes:c bytesPerRow:ow * 8 fromRegion:MTLRegionMake2D(0, 0, ow, oh) mipmapLevel:0];
        size_t dn = 0, db = 0, nz = 0; for (size_t i = 0; i < n; ++i) { dn += a[i] != b[i]; db += a[i] != c[i]; nz += a[i] != 0; }
        printf("%s blobs=%d: old %.3f ms  new %.3f ms | bytes differ new %zu, broken %zu (of %zu, %zu nonzero)\n", glare ? "Natural" : "Glow   ", nblobs, t0, t1, dn, db, n, nz);
        if (glare ? (dn != 0 || db == 0) : dn != 0) fails = 1;
    }
    printf(fails ? "FAIL\n" : "PASS\n"); return fails;
} }
