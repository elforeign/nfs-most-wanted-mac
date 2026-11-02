// Exercise the production recorder: draw -> native compute -> draw, queued frame
// snapshots, and ownership of an object Metal itself never sees. The Python
// driver also builds the pinned original with an after-commit compatibility path.
#include <stdio.h>
#include <stdatomic.h>
#include <time.h>
#include "rt_native.h"
#ifndef RT_RECORDER_SOURCE
#define RT_RECORDER_SOURCE "render_thread.m"
#endif
#include RT_RECORDER_SOURCE

id<MTLDevice> mtl;
id<MTLCommandQueue> queue;
int diag_on;
uint64_t host_ticks(void) { return clock_gettime_nsec_np(CLOCK_UPTIME_RAW); }
void diag_event(const char *fmt, ...) { (void)fmt; }

#ifdef RT_NATIVE_ORIGINAL
// The original offers only after-commit work. Give it the same callable API,
// copied values and retained resources; failure must be pixels, not linkage.
void rt_native_encode(id<MTLCommandBuffer> cb, RTNativeEncode encode,
                      const void *bytes, size_t size, NSArray *resources) {
    NSData *snapshot = [NSData dataWithBytes:bytes length:size];
    NSArray *objects = [resources copy];
    if (![(id)cb isKindOfClass:[RTCmd class]]) {
        encode(cb, snapshot.bytes, snapshot.length, objects);
        [cb addCompletedHandler:^(id<MTLCommandBuffer> done) { (void)done; (void)objects.count; }];
        return;
    }
    rt_after_commit(cb, ^{
        id<MTLCommandBuffer> late = [queue commandBuffer];
        encode(late, snapshot.bytes, snapshot.length, objects);
        [late addCompletedHandler:^(id<MTLCommandBuffer> done) { (void)done; (void)objects.count; }];
        [late commit];
    });
}
#endif

static atomic_int destroyed;
@interface NativeToken : NSObject
@end
@implementation NativeToken
- (void)dealloc { atomic_fetch_add(&destroyed, 1); }
@end

// A fake GPU separates real commit from completion deterministically. No device
// is requested by --cpu. It also keeps the real buffer alive after completion.
@interface NativeBuffer : NSObject
@property NSMutableArray *completions;
@property BOOL committed;
- (void)addCompletedHandler:(MTLCommandBufferHandler)handler;
- (void)commit;
- (void)finish;
@end
@implementation NativeBuffer
- (instancetype)init { if ((self = [super init])) _completions = [NSMutableArray new]; return self; }
- (void)addCompletedHandler:(MTLCommandBufferHandler)handler { [_completions addObject:[handler copy]]; }
- (void)commit { _committed = YES; }
- (void)finish {
    for (MTLCommandBufferHandler handler in _completions) handler((id)self);
    [_completions removeAllObjects];
}
@end
@interface NativeQueue : NSObject
@property NativeBuffer *last;
- (id)commandBuffer;
@end
@implementation NativeQueue
- (id)commandBuffer { _last = [NativeBuffer new]; return _last; }
@end

static int cpu_failures, cpu_calls, cpu_values[3];
#define CPU_CHECK(x) do { if (!(x)) { ++cpu_failures; printf("CPU FAIL %s\n", #x); } } while (0)
static void cpu_encode(id<MTLCommandBuffer> cb, const void *bytes, size_t size, NSArray *resources) {
    CPU_CHECK(![(NativeBuffer *)cb committed]);
    CPU_CHECK(size == sizeof(int));
    CPU_CHECK(resources.count == 1 && [resources[0] isKindOfClass:[NativeToken class]]);
    if (cpu_calls < 3) cpu_values[cpu_calls] = *(const int *)bytes;
    ++cpu_calls;
}

static int cpu_test(void) {
#ifndef RT_NATIVE_ORIGINAL
    NativeQueue *fake = [NativeQueue new]; queue = (id)fake;
    for (int recorded = 0; recorded < 2; ++recorded) {
        cpu_calls = 0;
        __weak NativeToken *weak;
        id<MTLCommandBuffer> cb;
        @autoreleasepool {
            rt_on = recorded;
            cb = rt_command_buffer();
            NativeToken *token = [NativeToken new]; weak = token;
            NSMutableArray *resources = [NSMutableArray arrayWithObject:token];
            for (int i = 0; i < 3; ++i) {
                int value = 100 + i;
                rt_native_encode(cb, cpu_encode, &value, sizeof value, resources);
                value = -1;
            }
            [resources removeAllObjects]; token = nil;
            CPU_CHECK(cpu_calls == (recorded ? 0 : 3));
            if (recorded) replay((RTCmd *)cb); else [cb commit];
        }
        CPU_CHECK(cpu_calls == 3);
        CPU_CHECK(cpu_values[0] == 100 && cpu_values[1] == 101 && cpu_values[2] == 102);
        CPU_CHECK(weak != nil);
        [fake.last finish];
        CPU_CHECK(weak == nil);
        cb = nil;
    }
    // An unused recorder has neither native storage nor a GPU completion hook.
    rt_on = 1;
    RTCmd *empty = (id)rt_command_buffer();
    CPU_CHECK(empty->owned.count == 0 && empty->n == 0);
    replay(empty);
    CPU_CHECK(fake.last.completions.count == 0);
    CPU_CHECK(atomic_load(&destroyed) == 2);
    printf("CPU failures=%d calls=6 unused_handlers=%lu destroyed=%d/2\n",
           cpu_failures, (unsigned long)fake.last.completions.count, atomic_load(&destroyed));
    return cpu_failures != 0;
#else
    return 2;
#endif
}

enum { WIDTH = 32, HEIGHT = 16, FRAMES = 3 };
typedef struct { float gain, bias; uint32_t frame, padding; } NativeParams;
static atomic_int gpu_calls;
static uintptr_t native_buffers[FRAMES];
static void transform(id<MTLCommandBuffer> cb, const void *bytes, size_t size, NSArray *resources) {
    if (size != sizeof(NativeParams) || resources.count != 4) abort();
    const NativeParams *p = bytes;
    if (p->frame >= FRAMES) abort();
    native_buffers[p->frame] = (uintptr_t)(__bridge void *)cb;
    id<MTLComputeCommandEncoder> e = [cb computeCommandEncoder];
    [e setComputePipelineState:resources[2]];
    [e setTexture:resources[0] atIndex:0];
    [e setTexture:resources[1] atIndex:1];
    [e setBytes:p length:sizeof *p atIndex:0];
    [e dispatchThreads:MTLSizeMake(WIDTH, HEIGHT, 1) threadsPerThreadgroup:MTLSizeMake(8, 8, 1)];
    [e endEncoding];
    atomic_fetch_add(&gpu_calls, 1);
}

static id<MTLTexture> texture(void) {
    MTLTextureDescriptor *d = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm
                      width:WIDTH height:HEIGHT mipmapped:NO];
    d.storageMode = MTLStorageModeShared;
    d.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead | MTLTextureUsageShaderWrite;
    id<MTLTexture> t = [mtl newTextureWithDescriptor:d];
    unsigned char poison[WIDTH * HEIGHT * 4];
    for (unsigned i = 0; i < sizeof poison; i += 4) {
        poison[i] = 3; poison[i+1] = 5; poison[i+2] = 7; poison[i+3] = 255;
    }
    [t replaceRegion:MTLRegionMake2D(0, 0, WIDTH, HEIGHT) mipmapLevel:0 withBytes:poison bytesPerRow:WIDTH*4];
    return t;
}

static void draw(id<MTLCommandBuffer> cb, id<MTLRenderPipelineState> pipeline,
                 id<MTLTexture> target, id<MTLTexture> input, const float *color) {
    MTLRenderPassDescriptor *d = [MTLRenderPassDescriptor renderPassDescriptor];
    d.colorAttachments[0].texture = target;
    d.colorAttachments[0].loadAction = MTLLoadActionDontCare;
    d.colorAttachments[0].storeAction = MTLStoreActionStore;
    id<MTLRenderCommandEncoder> e = [cb renderCommandEncoderWithDescriptor:d];
    [e setRenderPipelineState:pipeline];
    if (input) [e setFragmentTexture:input atIndex:0];
    else [e setFragmentBytes:color length:4*sizeof(float) atIndex:0];
    [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
    [e endEncoding];
}

static id<MTLRenderPipelineState> pipeline(id<MTLLibrary> library, NSString *fragment) {
    MTLRenderPipelineDescriptor *d = [MTLRenderPipelineDescriptor new];
    d.vertexFunction = [library newFunctionWithName:@"vertex_main"];
    d.fragmentFunction = [library newFunctionWithName:fragment];
    d.colorAttachments[0].pixelFormat = MTLPixelFormatRGBA8Unorm;
    NSError *error = nil;
    id<MTLRenderPipelineState> result = [mtl newRenderPipelineStateWithDescriptor:d error:&error];
    if (!result) { fprintf(stderr, "pipeline: %s\n", error.description.UTF8String); exit(2); }
    return result;
}

static int gpu_test(const char *output_path, int pixels_only) {
    mtl = MTLCreateSystemDefaultDevice();
    if (!mtl) { fprintf(stderr, "GPU unavailable\n"); return 2; }
    queue = [mtl newCommandQueue];
    NSString *source = @"#include <metal_stdlib>\nusing namespace metal;\n"
        "struct V { float4 p [[position]]; };\n"
        "vertex V vertex_main(uint i [[vertex_id]]) { float2 p[3]={float2(-1,-1),float2(3,-1),float2(-1,3)}; return {float4(p[i],0,1)}; }\n"
        "fragment float4 solid(V v [[stage_in]], constant float4 &c [[buffer(0)]]) { return c; }\n"
        "fragment float4 compose(V v [[stage_in]], texture2d<float> t [[texture(0)]]) { float4 c=t.read(uint2(v.p.xy)); return float4(c.b,c.r,c.g,1); }\n"
        "struct P { float gain,bias; uint frame,padding; };\n"
        "kernel void transform_main(texture2d<float,access::read> a [[texture(0)]], texture2d<float,access::write> b [[texture(1)]], constant P &p [[buffer(0)]], uint2 xy [[thread_position_in_grid]]) { float4 c=a.read(xy); b.write(float4(c.rgb*p.gain+p.bias,1),xy); }\n";
    NSError *error = nil;
    id<MTLLibrary> library = [mtl newLibraryWithSource:source options:nil error:&error];
    if (!library) { fprintf(stderr, "library: %s\n", error.description.UTF8String); return 2; }
    id<MTLRenderPipelineState> solid = pipeline(library, @"solid"), compose = pipeline(library, @"compose");
    id<MTLComputePipelineState> compute = [mtl newComputePipelineStateWithFunction:[library newFunctionWithName:@"transform_main"] error:&error];
    if (!compute) return 2;

    // Hold the GPU before all fixtures while the render thread remains free.
    // A token never bound to Metal must remain alive after replay and commit.
    id<MTLSharedEvent> gate = [mtl newSharedEvent];
    id<MTLCommandBuffer> blocker = [queue commandBuffer];
    [blocker encodeWaitForEvent:gate value:1]; [blocker commit];
    id<MTLCommandBuffer> frames[FRAMES];
    id<MTLTexture> outputs[FRAMES];
    __weak NativeToken *tokens[FRAMES];
    dispatch_semaphore_t committed = dispatch_semaphore_create(0);
    dispatch_semaphore_t completed = dispatch_semaphore_create(0);
    atomic_int *same_buffer_bad = calloc(1, sizeof *same_buffer_bad);
    for (unsigned frame = 0; frame < FRAMES; ++frame) {
        @autoreleasepool {
            id<MTLCommandBuffer> cb = rt_command_buffer(); frames[frame] = cb;
            id<MTLTexture> input = texture(), middle = texture(); outputs[frame] = texture();
            NativeToken *token = [NativeToken new]; tokens[frame] = token;
            NSMutableArray *resources = [NSMutableArray arrayWithArray:@[input, middle, compute, token]];
            float color[4] = {(32+16*frame)/255.f, (64+16*frame)/255.f, (96+16*frame)/255.f, 1};
            NativeParams p = {frame == 1 ? 2 : 1, (16+8*frame)/255.f, frame, 0};
            draw(cb, solid, input, nil, color);
            rt_native_encode(cb, transform, &p, sizeof p, resources);
            // Mutate caller-owned values AND resource container before replay.
            p.gain = p.bias = 0; [resources removeAllObjects]; token = nil;
            draw(cb, compose, outputs[frame], middle, NULL);
            [cb addCompletedHandler:^(id<MTLCommandBuffer> real) {
                if (native_buffers[frame] != (uintptr_t)(__bridge void *)real)
                    atomic_fetch_add(same_buffer_bad, 1);
                dispatch_semaphore_signal(completed);
            }];
            rt_after_commit(cb, ^{ dispatch_semaphore_signal(committed); });
            [cb commit];
        }
    }
    // A later replay proves the earlier replay scopes and autorelease pools
    // have drained. An after-commit signal alone precedes recorder cleanup.
    id<MTLCommandBuffer> replay_fence = rt_command_buffer();
    rt_after_commit(replay_fence, ^{ dispatch_semaphore_signal(committed); });
    [replay_fence commit];
    int timeouts = 0;
    for (unsigned frame = 0; frame < FRAMES + 1; ++frame)
        if (dispatch_semaphore_wait(committed, dispatch_time(DISPATCH_TIME_NOW, 10*NSEC_PER_SEC))) ++timeouts;
    int lifetime_bad = 0;
    for (unsigned frame = 0; frame < FRAMES; ++frame) if (!tokens[frame]) ++lifetime_bad;
    gate.signaledValue = 1;
    for (unsigned frame = 0; frame < FRAMES; ++frame) {
        [frames[frame] waitUntilCompleted];
        if (dispatch_semaphore_wait(completed, dispatch_time(DISPATCH_TIME_NOW, 10*NSEC_PER_SEC))) ++timeouts;
    }
    // Include the original's late buffers before checking final ownership.
    id<MTLCommandBuffer> drain = [queue commandBuffer]; [drain commit]; [drain waitUntilCompleted];
    unsigned char all[FRAMES * WIDTH * HEIGHT * 4];
    unsigned bad = 0, max_error = 0;
    int status_bad = 0, retained = 0;
    for (unsigned frame = 0; frame < FRAMES; ++frame) {
        unsigned char *pixels = all + frame * WIDTH * HEIGHT * 4;
        [outputs[frame] getBytes:pixels bytesPerRow:WIDTH*4 fromRegion:MTLRegionMake2D(0,0,WIDTH,HEIGHT) mipmapLevel:0];
        unsigned gain = frame == 1 ? 2 : 1, bias = 16+8*frame;
        unsigned expected[4] = {(96+16*frame)*gain+bias, (32+16*frame)*gain+bias, (64+16*frame)*gain+bias, 255};
        for (unsigned pixel = 0; pixel < WIDTH*HEIGHT; ++pixel) {
            unsigned mismatch = 0;
            for (unsigned channel = 0; channel < 4; ++channel) {
                unsigned diff = (unsigned)abs((int)pixels[pixel*4+channel] - (int)expected[channel]);
                if (diff > max_error) max_error = diff;
                if (diff > 1) mismatch = 1;
            }
            bad += mismatch;
        }
        id<MTLCommandBuffer> real = [(id)frames[frame] isKindOfClass:[RTCmd class]]
            ? ((RTCmd *)(id)frames[frame])->real : frames[frame];
        if (real.status != MTLCommandBufferStatusCompleted) ++status_bad;
        if (tokens[frame]) ++retained;
    }
    FILE *file = fopen(output_path, "wb");
    if (!file || fwrite(all, 1, sizeof all, file) != sizeof all) return 2;
    fclose(file);
    int calls = atomic_load(&gpu_calls), buffer_bad = atomic_load(same_buffer_bad);
    printf("GPU bad_pixels=%u/%u max_error=%u calls=%d/3 lifetime_bad=%d/3 retained=%d/3 same_buffer_bad=%d timeouts=%d status_bad=%d\n",
           bad, FRAMES*WIDTH*HEIGHT, max_error, calls, lifetime_bad, retained, buffer_bad, timeouts, status_bad);
    free(same_buffer_bad);
    return bad || calls != FRAMES || timeouts || status_bad || (!pixels_only && (lifetime_bad || retained || buffer_bad));
}

int main(int argc, char **argv) {
    @autoreleasepool {
        if (argc == 2 && !strcmp(argv[1], "--cpu")) return cpu_test();
        if (argc >= 3 && !strcmp(argv[1], "--gpu")) return gpu_test(argv[2], argc == 4 && !strcmp(argv[3], "--pixels-only"));
        return 2;
    }
}
