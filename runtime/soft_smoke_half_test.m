// Offline proof for half-resolution soft smoke (soft_smoke_half.m): the production depth downsample and upsample
// composite run on a 64x32 frame with a near box (depth 0.30, x < 29, y < 19: odd edges inside a 2x2 block) over a
// far background (0.95). Smoke at depth 0.60 lies behind the box and in front of the background, so the full-size
// truth is smoke on every background pixel and none on the box. The half layer is the smoke against the production
// half depth (the late shader's occlusion rule); the production composite blends it over a black scene. A pixel is
// wrong when it differs from the truth by more than 0.1. Production: 0 wrong (box halo 0, background holes 0).
// Deliberately broken (plain bilinear upsampling, --broken): 97 wrong, all holes on the background side of the
// edges; farthest-depth downsample + bilinear (--broken-far): 92 wrong, all smoke haloed onto the box.
// Build/run: clang -fobjc-arc -DSOFT_SMOKE_HALF_TEST -framework Metal -framework Foundation runtime/soft_smoke_half_test.m runtime/soft_smoke_half.m -o /tmp/x && /tmp/x
#import <Metal/Metal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include "soft_smoke_half.h"

enum { W = 64, H = 32, HW = W / 2, HH = H / 2, EDGE_X = 29, EDGE_Y = 19 };
static const float NEAR_Z = 0.30f, FAR_Z = 0.95f, SMOKE_Z = 0.60f, SMOKE = 0.5f;

id<MTLLibrary> soft_half_test_library(id<MTLDevice> dev, NSString *src) {
    NSError *err = nil; id<MTLLibrary> l = [dev newLibraryWithSource:src options:nil error:&err];
    if (!l) fprintf(stderr, "soft smoke half: %s\n", err.localizedDescription.UTF8String);
    return l;
}
static int in_box(int x, int y) { return x < EDGE_X && y < EDGE_Y; }

int main(int argc, char **argv) {
    @autoreleasepool {
        int broken = 0;
        if (argc > 1 && !strcmp(argv[1], "--broken")) broken = 2;
        else if (argc > 1 && !strcmp(argv[1], "--broken-far")) broken = 3;
        id<MTLDevice> dev = MTLCreateSystemDefaultDevice();
        if (!dev) { printf("soft_smoke_half_test: no Metal device\n"); return 3; }
        id<MTLCommandQueue> q = [dev newCommandQueue];
        MTLTextureDescriptor *d = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatDepth32Float width:W height:H mipmapped:NO];
        d.usage = MTLTextureUsageShaderRead | MTLTextureUsageRenderTarget; d.storageMode = MTLStorageModeShared;
        id<MTLTexture> fullz = [dev newTextureWithDescriptor:d];
        float z[W * H];
        for (int y = 0; y < H; ++y) for (int x = 0; x < W; ++x) z[y * W + x] = in_box(x, y) ? NEAR_Z : FAR_Z;
        [fullz replaceRegion:MTLRegionMake2D(0, 0, W, H) mipmapLevel:0 withBytes:z bytesPerRow:W * 4];
        id<MTLTexture> halfz = soft_half_depth_texture(dev, nil, HW, HH);
        MTLTextureDescriptor *hd = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatDepth32Float width:HW height:HH mipmapped:NO];
        hd.usage = MTLTextureUsageShaderRead | MTLTextureUsageRenderTarget; hd.storageMode = MTLStorageModeShared;
        halfz = [dev newTextureWithDescriptor:hd];   // shared, to read back; same format and size as production's
        id<MTLCommandBuffer> cb = [q commandBuffer];
        soft_half_encode_depth(dev, cb, fullz, halfz, broken == 3);
        [cb commit]; [cb waitUntilCompleted];
        float hz[HW * HH];
        [halfz getBytes:hz bytesPerRow:HW * 4 fromRegion:MTLRegionMake2D(0, 0, HW, HH) mipmapLevel:0];
        // The half layer: premultiplied smoke where the particle is in front of the half depth (late shader rule).
        MTLTextureDescriptor *ld = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:HW height:HH mipmapped:NO];
        ld.usage = MTLTextureUsageShaderRead; ld.storageMode = MTLStorageModeShared;
        id<MTLTexture> layer = [dev newTextureWithDescriptor:ld];
        uint8_t lp[HW * HH * 4];
        for (int i = 0; i < HW * HH; ++i) { uint8_t v = hz[i] > SMOKE_Z ? (uint8_t)lrintf(SMOKE * 255) : 0; memset(lp + i * 4, v, 4); }
        [layer replaceRegion:MTLRegionMake2D(0, 0, HW, HH) mipmapLevel:0 withBytes:lp bytesPerRow:HW * 4];
        MTLTextureDescriptor *sd = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:W height:H mipmapped:NO];
        sd.usage = MTLTextureUsageShaderRead | MTLTextureUsageRenderTarget; sd.storageMode = MTLStorageModeShared;
        id<MTLTexture> scene = [dev newTextureWithDescriptor:sd];
        uint8_t black[W * H * 4];
        for (int i = 0; i < W * H; ++i) { black[i * 4] = black[i * 4 + 1] = black[i * 4 + 2] = 0; black[i * 4 + 3] = 255; }
        [scene replaceRegion:MTLRegionMake2D(0, 0, W, H) mipmapLevel:0 withBytes:black bytesPerRow:W * 4];
        cb = [q commandBuffer];
        soft_half_encode_composite(dev, cb, scene, layer, halfz, fullz, broken ? 2 : 0);
        [cb commit]; [cb waitUntilCompleted];
        if (cb.status != MTLCommandBufferStatusCompleted) { printf("FAIL: command buffer status %ld\n", (long)cb.status); return 1; }
        uint8_t out[W * H * 4];
        [scene getBytes:out bytesPerRow:W * 4 fromRegion:MTLRegionMake2D(0, 0, W, H) mipmapLevel:0];
        int wrong = 0, halo = 0, holes = 0, smoked = 0;
        for (int y = 0; y < H; ++y) for (int x = 0; x < W; ++x) {
            float v = out[(y * W + x) * 4 + 1] / 255.0f, truth = in_box(x, y) ? 0.0f : SMOKE;
            if (v > 0.25f) ++smoked;
            if (fabsf(v - truth) > 0.1f) { ++wrong; if (in_box(x, y)) ++halo; else ++holes; }
        }
        printf("soft_smoke_half_test%s: wrong %d (box halo %d, background holes %d), smoked pixels %d of %d background\n",
               broken == 2 ? " --broken" : broken == 3 ? " --broken-far" : "", wrong, halo, holes, smoked, W * H - EDGE_X * EDGE_Y);
        if (wrong || smoked != W * H - EDGE_X * EDGE_Y) { printf("FAIL\n"); return 1; }
        printf("PASS\n");
        return 0;
    }
}
