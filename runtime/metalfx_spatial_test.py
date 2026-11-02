"""Standalone MetalFX spatial proof, no game assets or renderer routing.

Pinned analytic fixture v1: identical scene evaluated at 2560x1440 and
3840x2160 pixel centers, including flat swatches, gradients, periodic detail,
circles and antialiased diagonal edges. Gate: SDR RGB PSNR >=32 dB and local
luma SSIM (11x11 Gaussian, sigma 1.5) >=0.97. Edge/flat results are separate.
The native-only original must fail the input-size/invocation gate even though
its image is perfect. The skipped-SDK-encode mutation must fail both invocation
and readback gates. Initial actual proof: SDR 48.36 dB / 0.9960, edge 40.59 dB /
0.9850, flat 73.99 dB / 0.9999; skipped 4.72 dB / 0.8903 and 0/2 invocations.
GPU milliseconds cover only this fixture's scaler command buffer, never game
performance. Python driver is autodiscovered by the suite.

Cross-device command-buffer regression (direct + recorded, simulated foreign
device identity on one GPU): original rejects 0/2, preserves 0/2 outputs,
records 1 packet and invokes SDK 2 times; fixed rejects 2/2, preserves 2/2,
records 0 packets and invokes SDK 0 times (failures: original 7, fixed 0).
The original validation function is embedded and SHA-256 checked.
"""
from pathlib import Path
import hashlib
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / 'runtime'
GPU = os.environ.get('CODEX_GPU_CHECK') == '1' or os.environ.get('NFS_GPU_SANDBOX') == '1'
REPORTS = []

# Frozen validation before the command-buffer device guard.
ORIGINAL_ENCODE = r'''BOOL nfs_spatial_encode(id<MTLCommandBuffer> buffer, NFSSpatialPipeline *pipeline,
                        id<MTLTexture> input, id<MTLTexture> output) {
    if (!buffer || !pipeline || !input || !output || input == output) return NO;
    NFSSpatialConfig c = pipeline.config;
    if (input.device != pipeline->device || output.device != pipeline->device ||
        input.textureType != MTLTextureType2D || output.textureType != MTLTextureType2D ||
        input.sampleCount != 1 || output.sampleCount != 1 ||
        input.width != c.inputWidth || input.height != c.inputHeight ||
        output.width != c.outputWidth || output.height != c.outputHeight ||
        input.pixelFormat != c.inputFormat || output.pixelFormat != c.outputFormat ||
        (input.usage & pipeline.inputUsage) != pipeline.inputUsage ||
        (output.usage & pipeline.outputUsage) != pipeline.outputUsage ||
        input.hazardTrackingMode == MTLHazardTrackingModeUntracked ||
        output.hazardTrackingMode == MTLHazardTrackingModeUntracked ||
        input.storageMode == MTLStorageModeMemoryless || output.storageMode != MTLStorageModePrivate)
        return NO;
    const SpatialPacket packet = {c.inputWidth, c.inputHeight};
    rt_native_encode(buffer, spatial_encode_packet, &packet, sizeof packet, @[pipeline, input, output]);
    return YES;
}
'''
ORIGINAL_ENCODE_SHA256 = '995212f150097c9cde234616f9aa7271450bf85c3a68e543e86579c59fa55659'

HARNESS = r'''
#include <stdio.h>
#include <time.h>
#include <objc/runtime.h>
#include "render_thread.m"
#include SPATIAL_SOURCE
id<MTLDevice> mtl;
id<MTLCommandQueue> queue;
int diag_on;
uint64_t host_ticks(void) { return clock_gettime_nsec_np(CLOCK_UPTIME_RAW); }
void diag_event(const char *fmt, ...) { (void)fmt; }
static int failures, sdk_calls;
static NSUInteger observed_width, observed_height;
#define CHECK(x) do { if (!(x)) { fprintf(stderr,"FAIL line=%d %s\n",__LINE__,#x); ++failures; } } while(0)

static NFSSpatialConfig config(NSUInteger iw, NSUInteger ih, NSUInteger ow, NSUInteger oh) {
    return (NFSSpatialConfig){iw,ih,ow,oh,MTLPixelFormatRGBA8Unorm,MTLPixelFormatRGBA8Unorm,NFSSpatialPerceptual};
}
static BOOL unsupported(id self, SEL cmd, id device) { (void)self;(void)cmd;(void)device; return NO; }
static id failed_creation(id self, SEL cmd, id device) { (void)self;(void)cmd;(void)device; return nil; }

// Only device identity is simulated. Accepted work still runs on the real GPU,
// allowing the original validation to produce measurable destination damage.
@interface ForeignBuffer : NSObject {
@public
    id<MTLCommandBuffer> backing;
    id<MTLDevice> foreignDevice;
}
- (id<MTLDevice>)device;
- (void)addCompletedHandler:(MTLCommandBufferHandler)handler;
@end
@implementation ForeignBuffer
- (id<MTLDevice>)device { return foreignDevice; }
- (void)addCompletedHandler:(MTLCommandBufferHandler)handler { [backing addCompletedHandler:handler]; }
@end
@interface ForeignRecorder : RTCmd {
@public
    id<MTLDevice> foreignDevice;
}
@end
@implementation ForeignRecorder
- (id<MTLDevice>)device { return foreignDevice; }
@end

static int cpu_test(void) {
    NFSSpatialCache *cache = [[NFSSpatialCache alloc] initWithDevice:nil];
    NFSSpatialConfig c = config(2560,1440,3840,2160);
    CHECK(![NFSSpatialCache supportsDevice:nil]);
    CHECK(![cache pipelineForConfig:c]); CHECK(![cache pipelineForConfig:c]);
    CHECK(cache.creationAttempts == 0);
    CHECK(!nfs_spatial_encode(nil,nil,nil,nil));
    // Validate bounded descriptors without asking Metal for a GPU.
    id<MTLDevice> fake = (id)[NSObject new];
    CHECK(valid_config(c,fake));
    NFSSpatialConfig bad = c; bad.inputWidth = 0; CHECK(!valid_config(bad,fake));
    bad = c; bad.outputHeight = 100; CHECK(!valid_config(bad,fake));
    bad = c; bad.outputWidth = 16385; CHECK(!valid_config(bad,fake));
    bad = c; bad.inputFormat = MTLPixelFormatDepth32Float; CHECK(!valid_config(bad,fake));
    bad = c; bad.colorMode = (NFSSpatialColorMode)3; CHECK(!valid_config(bad,fake));
    bad = c; bad.colorMode = NFSSpatialHDR; CHECK(!valid_config(bad,fake));
    bad.inputFormat = bad.outputFormat = MTLPixelFormatRGBA16Float; CHECK(valid_config(bad,fake));
    CHECK(!same_config(c,bad));
    printf("CPU failures=%d nil_fallback=1 bounded_descriptors=8\n",failures);
    return failures != 0;
}

static id<MTLTexture> texture(NSUInteger w, NSUInteger h, MTLPixelFormat f,
                             MTLTextureUsage usage, MTLStorageMode storage) {
    MTLTextureDescriptor *d = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:f width:w height:h mipmapped:NO];
    d.usage = usage; d.storageMode = storage;
    id<MTLTexture> t = [mtl newTextureWithDescriptor:d]; CHECK(t != nil); return t;
}
static id<MTLRenderPipelineState> scene_pipeline(MTLPixelFormat format) {
    NSString *source = @"#include <metal_stdlib>\nusing namespace metal;\n"
      "struct V { float4 p [[position]]; };\n"
      "vertex V vert(uint i [[vertex_id]]) { float2 p[3]={float2(-1,-1),float2(3,-1),float2(-1,3)}; return {float4(p[i],0,1)}; }\n"
      "fragment float4 scene(V v [[stage_in]], constant float4 &s [[buffer(0)]]) {\n"
      " float2 p=v.p.xy*s.xy; float aa=s.x; float3 c;\n"
      " if(p.y<1080 && p.x<1920) { uint2 tile=uint2(p/float2(480,360)); c=float3(0.12+0.18*tile.x,0.2+0.22*tile.y,0.68-0.1*tile.x); }\n"
      " else if(p.y<1080) { float wave=sin(p.x*0.17)*sin(p.y*0.13); c=float3(0.5+0.18*wave,0.4+0.14*sin(p.x*0.07),0.3+0.1*cos(p.y*0.09)); }\n"
      " else if(p.x<1920) { float d=length(p-float2(960,1620))-340; float circle=1-smoothstep(-aa,aa,d); float line=1-smoothstep(7-aa,7+aa,abs(p.y-1200-0.35*p.x)); c=mix(float3(0.18,0.24,0.3),float3(0.7,0.5,0.2),circle); c=mix(c,float3(0.85),line); }\n"
      " else { float2 q=(p-float2(1920,1080))/float2(1920,1080); c=float3(0.1+0.7*q.x,0.2+0.5*q.y,0.2+0.3*q.x*q.y); }\n"
      " return float4(c*s.z+s.w,1); }\n";
    NSError *error = nil;
    id<MTLLibrary> lib = [mtl newLibraryWithSource:source options:nil error:&error];
    if (!lib) { fprintf(stderr,"shader: %s\n",error.description.UTF8String); exit(2); }
    MTLRenderPipelineDescriptor *d = [MTLRenderPipelineDescriptor new];
    d.vertexFunction = [lib newFunctionWithName:@"vert"];
    d.fragmentFunction = [lib newFunctionWithName:@"scene"];
    d.colorAttachments[0].pixelFormat = format;
    id<MTLRenderPipelineState> p = [mtl newRenderPipelineStateWithDescriptor:d error:&error];
    if (!p) { fprintf(stderr,"pipeline: %s\n",error.description.UTF8String); exit(2); } return p;
}
static void draw(id<MTLCommandBuffer> cb, id<MTLRenderPipelineState> p, id<MTLTexture> t, BOOL hdr) {
    MTLRenderPassDescriptor *d = [MTLRenderPassDescriptor renderPassDescriptor];
    d.colorAttachments[0].texture = t;
    d.colorAttachments[0].loadAction = MTLLoadActionDontCare;
    d.colorAttachments[0].storeAction = MTLStoreActionStore;
    id<MTLRenderCommandEncoder> e = [cb renderCommandEncoderWithDescriptor:d];
    float constants[4] = {3840.f/t.width,2160.f/t.height,hdr ? 3.f : 1.f,hdr ? 0.1f : 0.f};
    [e setRenderPipelineState:p]; [e setFragmentBytes:constants length:sizeof constants atIndex:0];
    [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3]; [e endEncoding];
}
static void finish(id<MTLCommandBuffer> cb) {
    [cb commit]; [cb waitUntilCompleted];
    id<MTLCommandBuffer> real = [(id)cb isKindOfClass:[RTCmd class]] ? ((RTCmd *)(id)cb)->real : cb;
    CHECK(real.status == MTLCommandBufferStatusCompleted);
    if (real.error) fprintf(stderr,"GPU: %s\n",real.error.description.UTF8String);
}
static NSData *readback(id<MTLTexture> t, NSUInteger bpp) {
    NSUInteger stride = t.width*bpp;
    id<MTLBuffer> b = [mtl newBufferWithLength:stride*t.height options:MTLResourceStorageModeShared];
    id<MTLCommandBuffer> cb = [queue commandBuffer];
    id<MTLBlitCommandEncoder> e = [cb blitCommandEncoder];
    [e copyFromTexture:t sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0)
       sourceSize:MTLSizeMake(t.width,t.height,1) toBuffer:b destinationOffset:0
       destinationBytesPerRow:stride destinationBytesPerImage:stride*t.height];
    [e endEncoding]; finish(cb);
    return [NSData dataWithBytes:b.contents length:stride*t.height];
}
// Count the actual SDK entry, so an accepted packet or native output cannot
// masquerade as a scaler invocation. The skipped mutation still has this hook.
static IMP real_sdk_encode;
static void counted_encode(id self, SEL cmd, id cb) {
    ++sdk_calls;
    id<MTLFXSpatialScaler> s = self;
    observed_width = s.colorTexture.width; observed_height = s.colorTexture.height;
    CHECK(observed_width == s.inputWidth && observed_height == s.inputHeight);
    CHECK(s.inputContentWidth == observed_width && s.inputContentHeight == observed_height);
    // The foreign direct buffer forwards actual GPU work to its real backing.
    if ([cb isKindOfClass:[ForeignBuffer class]]) cb = ((ForeignBuffer *)cb)->backing;
    ((void (*)(id,SEL,id))real_sdk_encode)(self,cmd,cb);
}
static void install_counter(NFSSpatialPipeline *p) {
    Method m = class_getInstanceMethod([p->scaler class], @selector(encodeToCommandBuffer:));
    if (!real_sdk_encode) real_sdk_encode = method_setImplementation(m,(IMP)counted_encode);
}

static int device_test(void) API_AVAILABLE(macos(13.0));
static int device_test(void) {
    mtl = MTLCreateSystemDefaultDevice();
    CHECK(mtl && [NFSSpatialCache supportsDevice:mtl]); if (failures) return 1;
    queue = [mtl newCommandQueue];
    setenv("NFS_RENDER_THREAD","1",1); rt_init();
    NFSSpatialCache *cache = [[NFSSpatialCache alloc] initWithDevice:mtl];
    NFSSpatialConfig c = config(320,180,480,270);
    NFSSpatialPipeline *p = [cache pipelineForConfig:c]; CHECK(p != nil); if (!p) return 1;
    install_counter(p);
    id<MTLRenderPipelineState> scene = scene_pipeline(c.inputFormat);
    id<MTLTexture> input = texture(320,180,c.inputFormat,p.inputUsage|MTLTextureUsageRenderTarget,MTLStorageModePrivate);
    id<MTLTexture> output = texture(480,270,c.outputFormat,p.outputUsage|MTLTextureUsageRenderTarget,MTLStorageModePrivate);
    id<MTLDevice> foreign = (id)[NSObject new];
    CHECK(foreign != mtl && input.device == mtl && output.device == mtl);
    int rejected = 0, unchanged = 0;
    size_t recordedWords = 0;
    for (int recorded=0;recorded<2;++recorded) {
        id<MTLCommandBuffer> prep = [queue commandBuffer];
        draw(prep,scene,input,NO);
        MTLRenderPassDescriptor *d = [MTLRenderPassDescriptor renderPassDescriptor];
        d.colorAttachments[0].texture = output;
        d.colorAttachments[0].loadAction = MTLLoadActionClear;
        d.colorAttachments[0].storeAction = MTLStoreActionStore;
        d.colorAttachments[0].clearColor = MTLClearColorMake(1,0,1,1);
        [[prep renderCommandEncoderWithDescriptor:d] endEncoding]; finish(prep);
        NSData *before = readback(output,4);
        id<MTLCommandBuffer> cb, completion;
        ForeignRecorder *recorder = nil;
        if (recorded) {
            recorder = [ForeignRecorder new]; recorder->foreignDevice = foreign;
            recorder->handlers = [NSMutableArray new]; recorder->owned = [NSMutableArray new];
            recorder->after = [NSMutableArray new];
            cb = completion = (id<MTLCommandBuffer>)recorder;
        } else {
            ForeignBuffer *direct = [ForeignBuffer new];
            direct->foreignDevice = foreign; direct->backing = [queue commandBuffer];
            cb = (id<MTLCommandBuffer>)direct; completion = direct->backing;
        }
        CHECK(cb.device == foreign);
        int callsBefore = sdk_calls;
        BOOL accepted = nfs_spatial_encode(cb,p,input,output);
        rejected += !accepted; CHECK(!accepted);
        if (recorded) {
            recordedWords = recorder->n;
            CHECK(recorder->n == 0 && recorder->owned.count == 0);
        }
        finish(completion);
        CHECK(sdk_calls == callsBefore);
        BOOL same = [before isEqualToData:readback(output,4)];
        unchanged += same; CHECK(same);
    }
    printf("device_guard rejected=%d/2 unchanged=%d/2 recorded_words=%zu SDK_invocations=%d failures=%d\n",
           rejected,unchanged,recordedWords,sdk_calls,failures);
    return failures != 0;
}

static int gpu_test(NSString *directory, BOOL original) API_AVAILABLE(macos(13.0));
static int gpu_test(NSString *directory, BOOL original) {
    mtl = MTLCreateSystemDefaultDevice();
    CHECK(mtl && [NFSSpatialCache supportsDevice:mtl]); if (failures) return 1;
    queue = [mtl newCommandQueue];
    // Start replay once before alternating direct/recorded test buffers.
    setenv("NFS_RENDER_THREAD","1",1); rt_init();
    NFSSpatialCache *cache = [[NFSSpatialCache alloc] initWithDevice:mtl];
    NFSSpatialConfig c = config(2560,1440,3840,2160);
    NFSSpatialPipeline *p = [cache pipelineForConfig:c]; CHECK(p != nil); if (!p) return 1;
    install_counter(p);
    CHECK(p.config.inputWidth == 2560 && p.config.outputWidth == 3840);
    for (int i=0;i<120;++i) CHECK([cache pipelineForConfig:c] == p);
    CHECK(cache.creationAttempts == 1);
    printf("SDK device=%s input_usage=%lu output_usage=%lu cached_frames=120 creations=%lu\n",
       mtl.name.UTF8String,(unsigned long)p.inputUsage,(unsigned long)p.outputUsage,(unsigned long)cache.creationAttempts);

    id<MTLRenderPipelineState> scene = scene_pipeline(c.inputFormat);
    id<MTLTexture> input = texture(c.inputWidth,c.inputHeight,c.inputFormat,p.inputUsage|MTLTextureUsageRenderTarget,MTLStorageModePrivate);
    id<MTLTexture> native = texture(c.outputWidth,c.outputHeight,c.outputFormat,MTLTextureUsageRenderTarget,MTLStorageModePrivate);
    id<MTLCommandBuffer> prep = [queue commandBuffer];
    draw(prep,scene,input,NO); draw(prep,scene,native,NO); finish(prep);
    [readback(native,4) writeToFile:[directory stringByAppendingPathComponent:@"native.rgba"] atomically:NO];
    // A deterministic sentinel makes an omitted invocation a readback failure.
    NSData *reference = nil;
    for (int recorded=0;recorded<2;++recorded) {
        rt_on = recorded;
        id<MTLTexture> output = texture(c.outputWidth,c.outputHeight,c.outputFormat,p.outputUsage|MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead,MTLStorageModePrivate);
        id<MTLCommandBuffer> clear = [queue commandBuffer];
        MTLRenderPassDescriptor *d = [MTLRenderPassDescriptor renderPassDescriptor];
        d.colorAttachments[0].texture=output; d.colorAttachments[0].loadAction=MTLLoadActionClear;
        d.colorAttachments[0].clearColor=MTLClearColorMake(1,0,1,1); d.colorAttachments[0].storeAction=MTLStoreActionStore;
        [[clear renderCommandEncoderWithDescriptor:d] endEncoding]; finish(clear);
        id<MTLCommandBuffer> cb = rt_command_buffer();
        if (original) {
            // Original renderer: full-size native rendering, no low-res input
            // consumed by a scaler. A perfect image must still fail this job.
            draw(cb,scene,output,NO);
            observed_width = output.width; observed_height = output.height;
        } else CHECK(nfs_spatial_encode(cb,p,input,output));
        finish(cb);
        id<MTLCommandBuffer> real = recorded ? ((RTCmd *)(id)cb)->real : cb;
        printf("fixture_only recorded=%d scaler_buffer_gpu_ms=%.4f\n",recorded,(real.GPUEndTime-real.GPUStartTime)*1000);
        NSData *pixels = readback(output,4);
        [pixels writeToFile:[directory stringByAppendingPathComponent:recorded ? @"recorded.rgba" : @"direct.rgba"] atomically:NO];
        if (recorded) CHECK([reference isEqualToData:pixels]); else reference = pixels;
    }
    CHECK(sdk_calls == 2);
    CHECK(observed_width == 2560 && observed_height == 1440);
    CHECK(cache.creationAttempts == 1);
    printf("observed_input=%lux%lu output=3840x2160 SDK_invocations=%d required=2 original=%d\n",
       (unsigned long)observed_width,(unsigned long)observed_height,sdk_calls,original);

    // Every descriptor field is part of the key. Formats/modes get actual SDK
    // creation, not just struct comparisons. Resize occurs before old replay.
    NFSSpatialConfig small = config(320,180,480,270);
    NFSSpatialPipeline *old = [cache pipelineForConfig:small]; CHECK(old != nil);
    rt_on = 1;
    id<MTLTexture> a = texture(320,180,small.inputFormat,old.inputUsage|MTLTextureUsageRenderTarget,MTLStorageModePrivate);
    id<MTLTexture> b = texture(480,270,small.outputFormat,old.outputUsage,MTLStorageModePrivate);
    id<MTLCommandBuffer> queued = rt_command_buffer(); draw(queued,scene,a,NO);
    CHECK(nfs_spatial_encode(queued,old,a,b));
    __weak NFSSpatialPipeline *weakOld = old;
    __weak id<MTLTexture> weakInput = a;
    NFSSpatialConfig hdr = config(640,360,960,540);
    hdr.inputFormat = hdr.outputFormat = MTLPixelFormatRGBA16Float; hdr.colorMode = NFSSpatialHDR;
    NFSSpatialPipeline *hp = [cache pipelineForConfig:hdr]; CHECK(hp != nil);
    old = nil; a = nil; small.inputWidth = 1;
    CHECK(weakOld && weakInput); finish(queued);
    NSData *resized = readback(b,4); const unsigned char *bytes = resized.bytes;
    CHECK(bytes[0] > 15 && bytes[0] < 55 && bytes[1] > 30 && bytes[1] < 75);
    queued = nil;
    // Release completion blocks before examining weak ownership.
    id<MTLCommandBuffer> drain = [queue commandBuffer]; finish(drain);
    if (hp) {
        NSUInteger before = cache.creationAttempts;
        for (int i=0;i<120;++i) CHECK([cache pipelineForConfig:hdr] == hp);
        CHECK(cache.creationAttempts == before);
        id<MTLTexture> hi = texture(640,360,hdr.inputFormat,hp.inputUsage|MTLTextureUsageRenderTarget,MTLStorageModePrivate);
        id<MTLTexture> ho = texture(960,540,hdr.outputFormat,hp.outputUsage,MTLStorageModePrivate);
        id<MTLCommandBuffer> hc = rt_command_buffer(); draw(hc,scene_pipeline(hdr.inputFormat),hi,YES);
        CHECK(nfs_spatial_encode(hc,hp,hi,ho)); finish(hc);
        NSData *floats = readback(ho,8);
        [floats writeToFile:[directory stringByAppendingPathComponent:@"hdr.rgba16f"] atomically:NO];
    }
    printf("resize_packet=320x180->480x270 HDR=640x360->960x540 cache_attempts=%lu\n",(unsigned long)cache.creationAttempts);

    // Perturb each key field independently. Inspect the actual SDK descriptor
    // as well as the wrapper so an omitted assignment cannot pass this proof.
    for (unsigned field=0;field<7;++field) {
        NFSSpatialConfig key = config(320,180,480,270);
        NFSSpatialPipeline *base = [cache pipelineForConfig:key];
        switch (field) {
        case 0: key.inputWidth += 2; break;
        case 1: key.inputHeight += 2; break;
        case 2: key.outputWidth += 2; break;
        case 3: key.outputHeight += 2; break;
        case 4: key.inputFormat = MTLPixelFormatBGRA8Unorm; break;
        case 5: key.outputFormat = MTLPixelFormatBGRA8Unorm; break;
        case 6: key.colorMode = NFSSpatialLinear; break;
        }
        NSUInteger beforeKey = cache.creationAttempts;
        NFSSpatialPipeline *different = [cache pipelineForConfig:key];
        CHECK(different && different != base && same_config(different.config,key));
        CHECK(cache.creationAttempts == beforeKey+1);
        if (different) {
            id<MTLFXSpatialScaler> s = different->scaler;
            CHECK(s.inputWidth == key.inputWidth && s.inputHeight == key.inputHeight);
            CHECK(s.outputWidth == key.outputWidth && s.outputHeight == key.outputHeight);
            CHECK(s.colorTextureFormat == key.inputFormat && s.outputTextureFormat == key.outputFormat);
            CHECK((NSUInteger)s.colorProcessingMode == key.colorMode);
        }
    }
    printf("descriptor_key_fields=7 individually_verified=7\n");

    // Rejections leave commands/output alone; destination bytes must survive.
    id<MTLTexture> sentinel = texture(3840,2160,c.outputFormat,p.outputUsage|MTLTextureUsageRenderTarget,MTLStorageModePrivate);
    id<MTLCommandBuffer> fc = rt_command_buffer(); draw(fc,scene,sentinel,NO); finish(fc);
    NSData *before = readback(sentinel,4);
    fc = rt_command_buffer();
    CHECK(!nfs_spatial_encode(fc,nil,input,sentinel));
    CHECK(!nfs_spatial_encode(fc,p,b,sentinel));
    CHECK(!nfs_spatial_encode(fc,p,input,input));
    id<MTLTexture> shared = texture(3840,2160,c.outputFormat,p.outputUsage,MTLStorageModeShared);
    CHECK(!nfs_spatial_encode(fc,p,input,shared));
    id<MTLTexture> missing = texture(2560,1440,c.inputFormat,MTLTextureUsageRenderTarget,MTLStorageModePrivate);
    if (p.inputUsage & ~MTLTextureUsageRenderTarget) CHECK(!nfs_spatial_encode(fc,p,missing,sentinel));
    id<MTLTexture> missingOutput = texture(3840,2160,c.outputFormat,MTLTextureUsageRenderTarget,MTLStorageModePrivate);
    if (p.outputUsage & ~MTLTextureUsageRenderTarget) CHECK(!nfs_spatial_encode(fc,p,input,missingOutput));
    MTLTextureDescriptor *ud = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:c.inputFormat width:2560 height:1440 mipmapped:NO];
    ud.storageMode = MTLStorageModePrivate; ud.usage = p.inputUsage;
    ud.hazardTrackingMode = MTLHazardTrackingModeUntracked;
    id<MTLTexture> untracked = [mtl newTextureWithDescriptor:ud]; CHECK(untracked != nil);
    CHECK(!nfs_spatial_encode(fc,p,untracked,sentinel));
    finish(fc); CHECK([before isEqualToData:readback(sentinel,4)]);

    Method support = class_getClassMethod([MTLFXSpatialScalerDescriptor class],@selector(supportsDevice:));
    IMP savedSupport = method_setImplementation(support,(IMP)unsupported);
    NFSSpatialCache *unsupportedCache = [[NFSSpatialCache alloc] initWithDevice:mtl];
    CHECK(![unsupportedCache pipelineForConfig:c]); CHECK(unsupportedCache.creationAttempts == 0);
    method_setImplementation(support,savedSupport);
    Method create = class_getInstanceMethod([MTLFXSpatialScalerDescriptor class],@selector(newSpatialScalerWithDevice:));
    IMP savedCreate = method_setImplementation(create,(IMP)failed_creation);
    NFSSpatialCache *failed = [[NFSSpatialCache alloc] initWithDevice:mtl];
    for (int i=0;i<120;++i) CHECK(![failed pipelineForConfig:c]);
    CHECK(failed.creationAttempts == 1);
    method_setImplementation(create,savedCreate);
    printf("GPU failures=%d unsupported_attempts=%lu failed_creation_attempts=%lu fallback_output_unchanged=1\n",
       failures,(unsigned long)unsupportedCache.creationAttempts,(unsigned long)failed.creationAttempts);
    return failures != 0;
}
int main(int argc, char **argv) { @autoreleasepool {
    if (argc==2 && !strcmp(argv[1],"--cpu")) return cpu_test();
    if (@available(macOS 13.0, *)) {
        if (argc==2 && !strcmp(argv[1],"--device")) return device_test();
        if (argc>=3 && !strcmp(argv[1],"--gpu")) return gpu_test(@(argv[2]),argc==4);
    }
    return 2;
} }
'''


def run(args, expected=0):
    p = subprocess.run([str(x) for x in args], capture_output=True, text=True,
                       timeout=120, env={**os.environ, 'MTL_DEBUG_LAYER': '1'})
    if p.stdout:
        REPORTS.append(p.stdout.strip())
        print(p.stdout.strip(), flush=True)
    assert p.returncode == expected, f'exit={p.returncode} expected={expected}\n{p.stdout[-2000:]}{p.stderr[-5000:]}'
    return p.stdout


def quality(directory):
    import numpy as np
    shape = (2160, 3840, 4)
    native = np.fromfile(directory / 'native.rgba', dtype=np.uint8).reshape(shape)[..., :3].astype(np.float32) / 255
    result = np.fromfile(directory / 'recorded.rgba', dtype=np.uint8).reshape(shape)[..., :3].astype(np.float32) / 255
    mse = np.mean((native-result)**2, axis=2)
    a = native @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    b = result @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)

    def blur(x):
        # Separable valid Gaussian; discard only the five-pixel image border.
        weights = np.exp(-np.arange(-5, 6, dtype=np.float32)**2 / (2*1.5**2))
        weights /= weights.sum()
        h = sum(w*x[:, i:i+x.shape[1]-10] for i, w in enumerate(weights))
        return sum(w*h[i:i+h.shape[0]-10, :] for i, w in enumerate(weights))

    ma, mb = blur(a), blur(b)
    va, vb = np.maximum(blur(a*a)-ma*ma, 0), np.maximum(blur(b*b)-mb*mb, 0)
    bound = np.sqrt(va*vb)
    cov = np.clip(blur(a*b)-ma*mb, -bound, bound)
    ssim_map = ((2*ma*mb+0.01**2)*(2*cov+0.03**2) /
                ((ma*ma+mb*mb+0.01**2)*(va+vb+0.03**2)))
    # Scene-derived masks, independent of scaler output. Report all detected
    # edges and quiet areas across the fixture, not selected favorable crops.
    gradient = np.maximum(np.abs(a[5:-5, 6:-4]-a[5:-5, 4:-6]),
                          np.abs(a[6:-4, 5:-5]-a[4:-6, 5:-5]))
    edge = gradient > 0.015
    flat = va < 1e-7
    interior_mse = mse[5:-5, 5:-5]
    psnr = float(-10*np.log10(max(float(mse.mean()), 1e-15)))
    ssim = float(ssim_map.mean())
    line = f'gate full_RGB_PSNR={psnr:.4f}dB local_luma_SSIM={ssim:.6f}'
    REPORTS.append(line)
    print(line, flush=True)
    for name, mask in [('all', np.ones_like(edge)), ('edge', edge), ('flat', flat)]:
        assert mask.any(), name
        region_psnr = float(-10*np.log10(max(float(interior_mse[mask].mean()), 1e-15)))
        line = f'{name} pixels={int(mask.sum())} PSNR={region_psnr:.4f}dB SSIM={float(ssim_map[mask].mean()):.6f}'
        REPORTS.append(line)
        print(line, flush=True)
    return psnr, ssim


with tempfile.TemporaryDirectory(prefix='metalfx-spatial-') as folder:
    scratch = Path(folder)
    harness = scratch / 'fixture.m'
    harness.write_text(HARNESS)
    source = RUNTIME / 'render_metalfx_spatial.m'

    def compile_fixture(label, implementation):
        exe = scratch / label
        run(['clang', '-O1', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
             '-fobjc-arc', '-mmacosx-version-min=12.0', '-I', RUNTIME, '-I', ROOT / 'recomp',
             f'-DSPATIAL_SOURCE="{implementation}"', harness, '-framework', 'Metal',
             '-weak_framework', 'MetalFX', '-framework', 'Foundation', '-o', exe])
        return exe

    good = compile_fixture('good', source)
    assert 'CPU failures=0' in run([good, '--cpu'])
    # Linkage is part of this job; no hard dependency on MetalFX at launch.
    linkage = subprocess.check_output(['otool', '-l', good], text=True)
    assert any('LC_LOAD_WEAK_DYLIB' in block and 'MetalFX.framework' in block
               for block in linkage.split('Load command'))
    print('PASS macOS 12 build, guarded availability and weak MetalFX linkage', flush=True)
    build = (RUNTIME / 'build.sh').read_text()
    assert 'render_metalfx_spatial.m' in build and '-weak_framework MetalFX' in build
    if GPU:
        report = run([good, '--device'])
        assert 'rejected=2/2 unchanged=2/2 recorded_words=0 SDK_invocations=0 failures=0' in report
        assert hashlib.sha256(ORIGINAL_ENCODE.encode()).hexdigest() == ORIGINAL_ENCODE_SHA256
        production = source.read_text()
        prefix, current_encode = production.split('BOOL nfs_spatial_encode(', 1)
        original_source = scratch / 'original-device.m'
        original_source.write_text(prefix + ORIGINAL_ENCODE)
        original_device = compile_fixture('original-device', original_source)
        report = run([original_device, '--device'], expected=1)
        assert 'rejected=0/2 unchanged=0/2 recorded_words=2 SDK_invocations=2 failures=7' in report
        print('PASS device guard: fixed 0 failures; pinned original 7 failures', flush=True)
        run([good, '--gpu', scratch])
        psnr, ssim = quality(scratch)
        assert psnr >= 32 and ssim >= 0.97, (psnr, ssim)
        import numpy as np
        hdr = np.fromfile(scratch / 'hdr.rgba16f', dtype=np.float16).reshape(540, 960, 4)
        assert np.isfinite(hdr).all() and hdr[..., :3].max() > 1.5
        line = f'HDR finite_values={hdr.size} max_rgb={float(hdr[..., :3].max()):.4f}'
        REPORTS.append(line)
        print(line, flush=True)
        # Baseline exercises a functioning native renderer but never invokes a
        # scaler. Its UHD native output cannot meet the required low-res input.
        report = run([good, '--gpu', scratch, '--original'], expected=1)
        assert 'observed_input=3840x2160' in report and 'SDK_invocations=0 required=2 original=1' in report
        bad_psnr, bad_ssim = quality(scratch)
        assert bad_psnr >= 100 and bad_ssim >= 0.999, (bad_psnr, bad_ssim)
        production = source.read_text()
        anchor = '            [s encodeToCommandBuffer:buffer];'
        assert production.count(anchor) == 1
        broken = scratch / 'skipped.m'
        broken.write_text(production.replace(anchor, '            (void)buffer;'))
        skipped = compile_fixture('skipped', broken)
        report = run([skipped, '--gpu', scratch], expected=1)
        assert 'SDK_invocations=0 required=2 original=0' in report
        bad_psnr, bad_ssim = quality(scratch)
        assert bad_psnr < 32 and bad_ssim < 0.97, (bad_psnr, bad_ssim)
        print('PASS actual SDK readback, native original and skipped-encode controls', flush=True)
    else:
        print('GPU PENDING: CODEX_GPU_CHECK=1 runtime/offline_tests.sh metalfx_spatial_test', flush=True)
    if os.environ.get('NFS_METAL_CACHE'):
        proof = Path(os.environ['NFS_METAL_CACHE']) / 'metalfx-spatial-proof.txt'
        proof.parent.mkdir(parents=True, exist_ok=True)
        proof.write_text('\n'.join(REPORTS) + '\n')
