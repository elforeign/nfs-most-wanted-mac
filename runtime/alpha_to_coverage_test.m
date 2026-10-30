#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include "sm_translate.h"
#include "alpha_to_coverage.c"
#include <math.h>
#include <stdio.h>

static Setting *registered;
void settings_add(Setting *s) { registered = s; }
static int failures, checks;
#define CHECK(c) do { ++checks; if (!(c)) { ++failures; printf("FAIL %s\n", #c); } } while (0)

@interface A2CTestTarget : NSObject
@property unsigned pixelFormat, sampleCount;
@end
@implementation A2CTestTarget
@end
static uint64_t fnv(const void *data, size_t n, uint64_t h) {
    const unsigned char *p = data;
    while (n--) h = (h ^ *p++) * 1099511628211ull;
    return h;
}
static uint64_t pipeline(uint32_t *rs, unsigned samples, MTLRenderPipelineDescriptor *pd, SmPSAux *out) {
    A2CTestTarget *rt0 = [A2CTestTarget new], *dst = nil;
    rt0.pixelFormat = MTLPixelFormatRGBA32Float; rt0.sampleCount = samples;
    struct { uint64_t key; } vs = {17}, ps = {29}, *vse = &vs, *pse = &ps;
    int blend = rs[27] != 0;
    SmPSAux pa = {0};
    #include "pipeline.inc"
    *out = pa;
    return fnv(&pk, sizeof pk, 31);
}
static void cpu(void) {
    uint32_t rs[256] = {0}; rs[15] = 1; rs[25] = 5; rs[24] = 128; rs[168] = 15;
    MTLRenderPipelineDescriptor *pd = [MTLRenderPipelineDescriptor new]; SmPSAux pa;
    CHECK(registered && registered->category == SET_WORLD && registered->def == 0);
    CHECK(!strcmp(registered->label, "Smooth cut-out edges"));
    uint64_t off = pipeline(rs, 2, pd, &pa);
    CHECK(!pd.alphaToCoverageEnabled && !pa.alpha_to_coverage);
    registered->apply(1);
    uint64_t on = pipeline(rs, 2, pd, &pa);
    CHECK(off != on && pd.alphaToCoverageEnabled && pa.alpha_to_coverage == 1);
    CHECK(pa.alpha_func == 5 && fabsf(pa.alpha_ref - 128.f / 255) < 1e-6f);
    for (unsigned samples = 1; samples <= 8; samples *= 2) {
        for (unsigned f = 0; f <= 8; ++f) for (unsigned blend = 0; blend <= 1; ++blend) {
            rs[25] = f; rs[27] = blend;
            int want = samples > 1 && !blend && (f == 2 || f == 4 || f == 5 || f == 7);
            pipeline(rs, samples, pd, &pa);
            CHECK(pd.alphaToCoverageEnabled == want && pa.alpha_to_coverage == (unsigned)want);
        }
    }
    rs[25] = 5; rs[27] = 0; rs[15] = 0;
    pipeline(rs, 2, pd, &pa); CHECK(!pd.alphaToCoverageEnabled && !pa.alpha_func);
    registered->apply(0); rs[15] = 1;
    CHECK(pipeline(rs, 2, pd, &pa) == off && !pd.alphaToCoverageEnabled);
    printf("CPU: %d checks, %d failures\n", checks, failures);
}

static void gpu(int broken_pipeline, int broken_discard) {
    enum { W = 64, H = 8 };
    id<MTLDevice> device = MTLCreateSystemDefaultDevice();
    if (!device) { fprintf(stderr, "Metal device unavailable\n"); ++failures; return; }
    id<MTLCommandQueue> queue = [device newCommandQueue];
    // ps_2_0 mov oC0, v0: real translator fragment wrapper, no private fixtures.
    uint32_t code[] = {0xffff0200u, 0x02000001u, 0x800f0800u, 0x90e40000u, 0x0000ffffu};
    SmOptions options; sm_default_options(&options); SmResult result = {0};
    CHECK(sm_translate(code, sizeof code / 4, &options, &result) == 0);
    if (!result.msl) return;
    NSString *source = [NSString stringWithUTF8String:result.msl];
    CHECK([source containsString:@"if (aux.alpha_to_coverage != 0u)"]);
    if (broken_discard) source = [source stringByReplacingOccurrencesOfString:@"if (aux.alpha_to_coverage != 0u)" withString:@"if (false)"];
    NSError *error = nil;
    id<MTLLibrary> ps = [device newLibraryWithSource:source options:nil error:&error];
    if (!ps) { fprintf(stderr, "%s\n", error.localizedDescription.UTF8String); ++failures; sm_result_free(&result); return; }
    const char *vertex =
        "#include <metal_stdlib>\nusing namespace metal;\n"
        "struct V {float4 p [[position]]; float4 c [[user(color0)]];};\n"
        "vertex V vs(uint id [[vertex_id]], constant float4 &params [[buffer(0)]]) {\n"
        "float2 p[3] = {float2(-1,-1),float2(3,-1),float2(-1,3)};\n"
        "float mid=params.x/32.0f;\n"
        "float2 wire[12]={float2(-1,-1),float2(mid,-1),float2(-1,1),float2(mid,-1),float2(mid,1),float2(-1,1),\n"
        "float2(mid,-1),float2(1,-1),float2(mid,1),float2(1,-1),float2(1,1),float2(mid,1)};\n"
        "float2 xy=params.y>0 ? wire[id] : p[id];\n"
        "float a=params.y>0 ? 128.5f/255.0f+params.y/128.0f-abs(xy.x-mid)*.5f : (xy.x+1)*.5f-params.x/64.0f;\n"
        "return {float4(xy,0,1),float4(.2f,.4f,.8f,a)};}\n";
    id<MTLLibrary> vs = [device newLibraryWithSource:[NSString stringWithUTF8String:vertex] options:nil error:&error];
    if (!vs) { fprintf(stderr, "%s\n", error.localizedDescription.UTF8String); ++failures; sm_result_free(&result); return; }
    unsigned partial_total = 0; double max_width_error = 0, max_wire_error = 0, max_wire_mean_error = 0;
    unsigned configs = 0, wires = 0;
    registered->apply(1);
    for (unsigned samples = 1; samples <= 8; samples *= 2) {
        if (![device supportsTextureSampleCount:samples]) continue;
        for (unsigned shape = 0; shape < 4; ++shape) for (unsigned f = 2; f <= 7; ++f) {
            if (f == 3 || f == 6) continue;
            if (shape && f != 5) continue;
            for (int smooth = 0; smooth <= 1; ++smooth) {
                uint32_t rs[256] = {0}; rs[15] = 1; rs[25] = f; rs[24] = 128; rs[168] = 15;
                registered->apply(smooth);
                MTLRenderPipelineDescriptor *pd = [MTLRenderPipelineDescriptor new]; SmPSAux pa;
                pipeline(rs, samples, pd, &pa);
                if (broken_pipeline) pd.alphaToCoverageEnabled = NO;
                pd.vertexFunction = [vs newFunctionWithName:@"vs"]; pd.fragmentFunction = [ps newFunctionWithName:@"sm_main"];
                pd.colorAttachments[0].pixelFormat = MTLPixelFormatRGBA32Float; pd.rasterSampleCount = samples;
                id<MTLRenderPipelineState> pso = [device newRenderPipelineStateWithDescriptor:pd error:&error];
                if (!pso) { fprintf(stderr, "%s\n", error.localizedDescription.UTF8String); ++failures; continue; }
                MTLTextureDescriptor *td = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA32Float width:W height:H mipmapped:NO];
                td.usage = MTLTextureUsageRenderTarget; td.storageMode = MTLStorageModeShared;
                id<MTLTexture> resolved = [device newTextureWithDescriptor:td], target = resolved;
                if (samples > 1) {
                    td.textureType = MTLTextureType2DMultisample; td.sampleCount = samples; td.storageMode = MTLStorageModePrivate;
                    target = [device newTextureWithDescriptor:td];
                }
                // Four subpixel phases exercise coverage while crossing both sample thresholds.
                double wire_error_sum = 0;
                for (unsigned phase_index = 0; phase_index < 4; ++phase_index) {
                    float phase = phase_index * .25f;
                    const float widths[] = {0, 2.5f, 4.5f, 8.5f};
                    float params[] = {phase, widths[shape], 0, 0};
                    MTLRenderPassDescriptor *rp = [MTLRenderPassDescriptor renderPassDescriptor];
                    rp.colorAttachments[0].texture = target; rp.colorAttachments[0].loadAction = MTLLoadActionClear;
                    rp.colorAttachments[0].storeAction = samples > 1 ? MTLStoreActionMultisampleResolve : MTLStoreActionStore;
                    if (samples > 1) rp.colorAttachments[0].resolveTexture = resolved;
                    id<MTLCommandBuffer> cb = [queue commandBuffer]; id<MTLRenderCommandEncoder> en = [cb renderCommandEncoderWithDescriptor:rp];
                    [en setRenderPipelineState:pso]; [en setVertexBytes:params length:sizeof params atIndex:0];
                    float constants[4] = {0}; int ints[4] = {0};
                    [en setFragmentBytes:constants length:sizeof constants atIndex:0];
                    [en setFragmentBytes:ints length:sizeof ints atIndex:1]; [en setFragmentBytes:ints length:sizeof ints atIndex:2];
                    [en setFragmentBytes:&pa length:sizeof pa atIndex:3];
                    [en drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:shape ? 12 : 3]; [en endEncoding];
                    [cb commit]; [cb waitUntilCompleted]; CHECK(cb.error == nil);
                    float pixels[H][W][4];
                    [resolved getBytes:pixels bytesPerRow:W * 16 fromRegion:MTLRegionMake2D(0,0,W,H) mipmapLevel:0];
                    double width = 0; unsigned partial = 0;
                    for (unsigned x = 0; x < W; ++x) {
                        float coverage = pixels[H/2][x][0] / .2f;
                        width += coverage; partial += coverage > .01f && coverage < .99f;
                        if ((!shape && ((f >= 5 && x > 48) || (f < 5 && x < 16))) ||
                            (shape && fabsf((x + .5f) - (32 + phase)) < .5f)) {
                            CHECK(fabsf(pixels[H/2][x][0] - .2f) < 1e-5f);
                            CHECK(fabsf(pixels[H/2][x][2] - .8f) < 1e-5f);
                        }
                    }
                    if (smooth && samples > 1) {
                        partial_total += partial;
                        float edge = (128.f + ((f == 5 || f == 4) ? .5f : -.5f))/255.f;
                        double ideal = shape ? widths[shape] : f >= 5 ? W * (1-edge)-phase : W * edge+phase;
                        double e = fabs(width-ideal);
                        if (shape) { ++wires; wire_error_sum += width-ideal; if (e > max_wire_error) max_wire_error = e; }
                        else if (e > max_width_error) max_width_error = e;
                        CHECK(e <= (shape ? 1.02 : .51) / samples + .01);
                    } else CHECK(partial == 0);
                    ++configs;
                }
                if (shape && smooth && samples > 1) {
                    double e = fabs(wire_error_sum/4);
                    if (e > max_wire_mean_error) max_wire_mean_error = e;
                    CHECK(e <= .26 / samples + .001);
                }
            }
        }
    }
    CHECK(partial_total > 0);
    printf("GPU: %u configurations, %u fractional edge pixels, max width error %.4f px, %d failures\n", configs, partial_total, max_width_error, failures);
    printf("Thin wires: %u phases at 2.5/4.5/8.5 px, max width error %.4f px, phase-mean error %.4f px\n",
        wires, max_wire_error, max_wire_mean_error);
    sm_result_free(&result);
}

int main(int argc, char **argv) { @autoreleasepool {
    cpu();
    if (!failures && !(argc > 1 && !strcmp(argv[1], "--cpu")))
        gpu(argc > 1 && !strcmp(argv[1], "--broken-pipeline"), argc > 1 && !strcmp(argv[1], "--broken-discard"));
    return failures ? 1 : 0;
} }
