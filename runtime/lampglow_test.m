// Offline GPU unit test for the actual lamp accumulation shader.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <string.h>
typedef struct { float off[16][4], wgt[16][4], p0[4]; } K;
static id<MTLDevice> gpu;
static id<MTLCommandQueue> queue;
static id<MTLSamplerState> sampler;
static unsigned checks;
static void check(int ok, const char *what) { ++checks; if (!ok) { fprintf(stderr, "FAIL %s\n", what); exit(1); } }
static id<MTLTexture> texture(MTLPixelFormat pf, int size) {
    MTLTextureDescriptor *d = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:pf width:size height:size mipmapped:NO];
    d.usage = MTLTextureUsageShaderRead | MTLTextureUsageRenderTarget; d.storageMode = MTLStorageModeShared;
    return [gpu newTextureWithDescriptor:d];
}
static void fill(id<MTLTexture> t, float r, float g, float b, float a) {
    float px[32 * 32 * 4];
    for (int i = 0; i < 32 * 32; ++i) { px[i * 4] = r; px[i * 4 + 1] = g; px[i * 4 + 2] = b; px[i * 4 + 3] = a; }
    [t replaceRegion:MTLRegionMake2D(0, 0, 32, 32) mipmapLevel:0 withBytes:px bytesPerRow:32 * 16];
}
static id<MTLRenderPipelineState> pipeline(id<MTLLibrary> lib, NSString *name, MTLPixelFormat pf, int mrt) {
    MTLRenderPipelineDescriptor *d = [MTLRenderPipelineDescriptor new];
    d.vertexFunction = [lib newFunctionWithName:@"vs_full"]; d.fragmentFunction = [lib newFunctionWithName:name];
    d.colorAttachments[0].pixelFormat = pf; if (mrt) d.colorAttachments[1].pixelFormat = pf;
    NSError *err = nil;
    id<MTLRenderPipelineState> p = [gpu newRenderPipelineStateWithDescriptor:d error:&err];
    if (!p) fprintf(stderr, "%s\n", err.localizedDescription.UTF8String);
    check(p != nil, name.UTF8String); return p;
}
static void pass(id<MTLRenderPipelineState> p, id<MTLTexture> a, id<MTLTexture> b, id<MTLTexture> out, id<MTLTexture> out1, K *k) {
    id<MTLCommandBuffer> c = [queue commandBuffer];
    MTLRenderPassDescriptor *rp = [MTLRenderPassDescriptor renderPassDescriptor];
    rp.colorAttachments[0].texture = out; rp.colorAttachments[0].loadAction = MTLLoadActionDontCare; rp.colorAttachments[0].storeAction = MTLStoreActionStore;
    if (out1) { rp.colorAttachments[1].texture = out1; rp.colorAttachments[1].loadAction = MTLLoadActionDontCare; rp.colorAttachments[1].storeAction = MTLStoreActionStore; }
    id<MTLRenderCommandEncoder> e = [c renderCommandEncoderWithDescriptor:rp];
    [e setRenderPipelineState:p]; [e setFragmentTexture:a atIndex:0]; [e setFragmentTexture:b atIndex:1];
    [e setFragmentSamplerState:sampler atIndex:0]; [e setFragmentBytes:k length:sizeof *k atIndex:0];
    [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3]; [e endEncoding]; [c commit]; [c waitUntilCompleted];
    check(c.status == MTLCommandBufferStatusCompleted, "GPU pass completed");
}
static float value(id<MTLTexture> t, int channel) {
    _Float16 data[8 * 8 * 4]; [t getBytes:data bytesPerRow:8 * 8 fromRegion:MTLRegionMake2D(0, 0, 8, 8) mipmapLevel:0];
    return data[channel];
}
int main(int argc, const char **argv) { @autoreleasepool {
    gpu = MTLCreateSystemDefaultDevice(); if (!gpu) return 77; queue = [gpu newCommandQueue];
    MTLSamplerDescriptor *sd = [MTLSamplerDescriptor new]; sd.minFilter = sd.magFilter = MTLSamplerMinMagFilterLinear;
    sd.sAddressMode = sd.tAddressMode = MTLSamplerAddressModeClampToEdge; sampler = [gpu newSamplerStateWithDescriptor:sd];
    NSError *err = nil;
    id<MTLLibrary> lib = [gpu newLibraryWithSource:[NSString stringWithContentsOfFile:@(argv[1]) encoding:NSUTF8StringEncoding error:nil] options:nil error:&err];
    if (!lib) fprintf(stderr, "%s\n", err.localizedDescription.UTF8String);
    check(lib != nil, "lamp Metal source compiles");
    id<MTLLibrary> old = [gpu newLibraryWithSource:[NSString stringWithContentsOfFile:@(argv[2]) encoding:NSUTF8StringEncoding error:nil] options:nil error:&err];
    check(old != nil, "baseline source compiles");
    id<MTLRenderPipelineState> source = pipeline(lib, @"fs_lamp_source", MTLPixelFormatRGBA16Float, 0);
    id<MTLRenderPipelineState> taps = pipeline(lib, @"fs_taps", MTLPixelFormatRGBA16Float, 0);
    id<MTLTexture> scene = texture(MTLPixelFormatRGBA32Float, 32), lamps = texture(MTLPixelFormatRGBA32Float, 32);
    id<MTLTexture> input = texture(MTLPixelFormatRGBA16Float, 8), blurred = texture(MTLPixelFormatRGBA16Float, 8);
    float red[2], blue[2];
    for (int natural = 0; natural < 2; ++natural) {
        K k = {0}; k.p0[0] = .5f; k.p0[1] = natural; k.p0[2] = natural ? .2f : 7.7f;
        k.wgt[0][0] = .14f; k.wgt[0][1] = .26f; k.wgt[0][2] = .25f; k.wgt[0][3] = 2;
        for (int color = 0; color < 2; ++color) {
            fill(scene, color ? 0 : .5f, 0, color ? .5f : 0, 1);
            fill(lamps, color ? 0 : .5f, 0, color ? .5f : 0, 1);
            pass(source, scene, lamps, input, nil, &k);
            float v = value(input, color ? 2 : 0); (color ? blue : red)[natural] = v;
            check(fabsf(v - .25f) < .001f, "full burst yields nonzero bounded emission");
            K blur = {0}; for (int n = 0; n < 16; ++n) { blur.wgt[n][0] = blur.wgt[n][1] = blur.wgt[n][2] = blur.wgt[n][3] = 1.0f / 16; }
            pass(taps, input, nil, blurred, nil, &blur);
            check(fabsf(value(blurred, color ? 2 : 0) - v) < .001f, "normalized blur preserves bounded gain");
        }
        fill(scene, .5f, 0, 0, 1); fill(lamps, 0, 0, 0, 0);
        pass(source, scene, lamps, input, nil, &k); check(value(input, 0) == 0, "red surface without flare / zero alpha gives zero");
        fill(scene, 0, 0, 0, 1); pass(source, scene, lamps, input, nil, &k); check(value(input, 0) == 0, "off flash gives zero");
        fill(scene, 1, 1, 1, 1); fill(lamps, .5f, .5f, .5f, 1); k.p0[2] = 10;
        pass(source, scene, lamps, input, nil, &k); check(value(input, 0) == 0, "already admitted lamp gets no duplicate source");
        k.p0[0] = 0; fill(scene, .5f, 0, 0, 1); fill(lamps, .5f, 0, 0, 1);
        pass(source, scene, lamps, input, nil, &k); check(value(input, 0) == 0, "deliberately disabled gain fails to glow");
    }
    // Byte comparison of actual first-downsample bloom inputs with Off: both
    // Natural and Glow, using a reproducible nonuniform coloured scene.
    float pixels[32 * 32 * 4]; unsigned rng = 123;
    for (int i = 0; i < 32 * 32 * 4; ++i) { rng = rng * 1664525u + 1013904223u; pixels[i] = (rng >> 24) / 255.0f; }
    [scene replaceRegion:MTLRegionMake2D(0, 0, 32, 32) mipmapLevel:0 withBytes:pixels bytesPerRow:32 * 16];
    id<MTLRenderPipelineState> down = pipeline(lib, @"fs_downscale", MTLPixelFormatRGBA8Unorm, 1), prev = pipeline(old, @"fs_downscale", MTLPixelFormatRGBA8Unorm, 1);
    id<MTLTexture> a = texture(MTLPixelFormatRGBA8Unorm, 8), b = texture(MTLPixelFormatRGBA8Unorm, 8), c = texture(MTLPixelFormatRGBA8Unorm, 8), d = texture(MTLPixelFormatRGBA8Unorm, 8);
    for (int mode = 0; mode < 2; ++mode) {
        K k = {0}; k.p0[2] = 2; k.p0[3] = mode;
        for (int y = 0; y < 4; ++y) for (int x = 0; x < 4; ++x) { k.off[y * 4 + x][0] = (x - 1.5f) / 8; k.off[y * 4 + x][1] = (y - 1.5f) / 8; }
        pass(prev, scene, nil, a, b, &k); pass(down, scene, nil, c, d, &k);
        unsigned char x[256], y[256];
        [b getBytes:x bytesPerRow:32 fromRegion:MTLRegionMake2D(0, 0, 8, 8) mipmapLevel:0];
        [d getBytes:y bytesPerRow:32 fromRegion:MTLRegionMake2D(0, 0, 8, 8) mipmapLevel:0];
        check(!memcmp(x, y, sizeof x), "Off bloom input byte-identical to baseline");
    }
    printf("PASS %u GPU checks; Glow/Natural red %.5f/%.5f blue %.5f/%.5f; Off inputs 512/512 bytes identical\n", checks, red[0], red[1], blue[0], blue[1]);
} return 0; }
