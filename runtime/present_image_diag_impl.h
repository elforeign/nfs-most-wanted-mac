// Included by the game-layer presenter. The overlay has a separate drawable.
#include "present_image_diag.h"

enum { IMAGE_OFF, IMAGE_LIVE, IMAGE_HOLD };
static int image_mode;
static id<MTLComputePipelineState> image_pipeline;
static atomic_bool image_hold;
static pthread_mutex_t image_mu = PTHREAD_MUTEX_INITIALIZER;
static struct {
    uint64_t tick, changed, hash, frame, drawable, shown, encoded, failed;
    uint64_t peak;
    double shown_time;
    bool stale;
} image_diag;

// Sample an evenly spaced 64x64 grid of visible RGB in the final drawable.
// Two position-dependent hashes reduce to eight bytes in shared memory.
static NSString *const image_shader = @
"#include <metal_stdlib>\n"
"using namespace metal;\n"
"kernel void fingerprint(texture2d<float, access::read> image [[texture(0)]],\n"
"                        device uint2 &output [[buffer(0)]],\n"
"                        uint lane [[thread_index_in_threadgroup]]) {\n"
"    threadgroup uint2 partial[256];\n"
"    uint2 size(image.get_width(), image.get_height());\n"
"    uint2 h(lane + size.x * 257u, lane + size.y * 65537u);\n"
"    for (uint i = lane; i < 4096; i += 256) {\n"
"        uint2 pixel = (uint2(i % 64, i / 64) * 2 + 1) * size / 128;\n"
"        uint3 rgb = as_type<uint4>(image.read(pixel)).xyz;\n"
"        h = (h << 11) | (h >> 21);\n"
"        h = (h ^ rgb.r) * uint2(16777619u, 2246822519u);\n"
"        h = (h ^ rgb.g) * uint2(16777619u, 2246822519u);\n"
"        h = (h ^ rgb.b) * uint2(16777619u, 2246822519u);\n"
"    }\n"
"    partial[lane] = h ^ (h >> 16);\n"
"    for (uint span = 128; span; span >>= 1) {\n"
"        threadgroup_barrier(mem_flags::mem_threadgroup);\n"
"        if (lane < span) partial[lane] =\n"
"            partial[lane] * uint2(16777619u, 2246822519u) ^ partial[lane + span];\n"
"    }\n"
"    if (!lane) output = partial[0];\n"
"}\n";

void present_image_diag_init(CAMetalLayer *layer) {
    if (!layer) return;
    const char *mode = getenv("BOOT_DIAG_PRESENT_IMAGE");
    if (mode && !strcmp(mode, "off")) return;
    NSError *error = nil;
    id<MTLLibrary> library = [layer.device newLibraryWithSource:image_shader options:nil error:&error];
    id<MTLFunction> function = [library newFunctionWithName:@"fingerprint"];
    if (function) image_pipeline = [layer.device newComputePipelineStateWithFunction:function error:&error];
    if (!image_pipeline || image_pipeline.maxTotalThreadsPerThreadgroup < 256) {
        image_pipeline = nil;
        diag_event("present-image unavailable: %s", error.localizedDescription.UTF8String ?: "compute pipeline");
        return;
    }
    layer.framebufferOnly = NO;
    image_mode = mode && !strcmp(mode, "hold") ? IMAGE_HOLD : IMAGE_LIVE;
}

typedef struct { uint64_t hash, shown_tick; double shown_time; bool gpu_done; } ImageSample;

// Both handlers can run first. Call under image_mu once either half arrives.
static void present_image_accept(ImageSample sample, uint64_t frame, uint64_t drawable) {
    if (!sample.gpu_done || !(sample.shown_time > image_diag.shown_time)) return;
    if (!image_diag.shown || sample.hash != image_diag.hash) {
        image_diag.changed = sample.shown_tick;
        image_diag.stale = false;
    }
    image_diag.hash = sample.hash;
    image_diag.shown_time = sample.shown_time;
    image_diag.frame = frame;
    image_diag.drawable = drawable;
    ++image_diag.shown;
}

// The broken control holds actual final pixels while the game still renders and presents.
static void present_image_hold(id<MTLCommandBuffer> cb, id<MTLTexture> drawable) {
    static id<MTLTexture> held;
    if (!atomic_load_explicit(&image_hold, memory_order_relaxed)) { held = nil; return; }
    bool capture = held.width != drawable.width || held.height != drawable.height || held.pixelFormat != drawable.pixelFormat;
    if (capture) {
        MTLTextureDescriptor *desc = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:drawable.pixelFormat
                                                          width:drawable.width height:drawable.height mipmapped:NO];
        desc.storageMode = MTLStorageModePrivate;
        held = [drawable.device newTextureWithDescriptor:desc];
        if (!held) return;
    }
    id<MTLBlitCommandEncoder> blit = [cb blitCommandEncoder];
    [blit copyFromTexture:(capture ? drawable : held) toTexture:(capture ? held : drawable)];
    [blit endEncoding];
}

static void present_image_encode(id<MTLCommandBuffer> cb, id<CAMetalDrawable> drawable, uint64_t frame) {
    if (image_mode == IMAGE_OFF) return;
    if (image_mode == IMAGE_HOLD) present_image_hold(cb, drawable.texture);
    id<MTLBuffer> result = [drawable.texture.device newBufferWithLength:sizeof(uint64_t) options:MTLResourceStorageModeShared];
    id<MTLComputeCommandEncoder> compute = result ? [cb computeCommandEncoder] : nil;
    pthread_mutex_lock(&image_mu);
    if (compute) ++image_diag.encoded;
    else ++image_diag.failed;
    pthread_mutex_unlock(&image_mu);
    if (!compute) return;
    compute.label = @"present image fingerprint";
    [compute setComputePipelineState:image_pipeline];
    [compute setTexture:drawable.texture atIndex:0];
    [compute setBuffer:result offset:0 atIndex:0];
    [compute dispatchThreadgroups:MTLSizeMake(1, 1, 1) threadsPerThreadgroup:MTLSizeMake(256, 1, 1)];
    [compute endEncoding];

    __block ImageSample sample = {0};
    uint64_t identifier = drawable.drawableID;
    [cb addCompletedHandler:^(id<MTLCommandBuffer> done) {
        pthread_mutex_lock(&image_mu);
        if (done.status == MTLCommandBufferStatusCompleted) {
            memcpy(&sample.hash, result.contents, sizeof sample.hash);
            sample.gpu_done = true;
            present_image_accept(sample, frame, identifier);
        } else ++image_diag.failed;
        pthread_mutex_unlock(&image_mu);
    }];
    [drawable addPresentedHandler:^(id<MTLDrawable> shown) {
        pthread_mutex_lock(&image_mu);
        sample.shown_time = shown.presentedTime;
        sample.shown_tick = image_diag.tick;
        present_image_accept(sample, frame, identifier);
        pthread_mutex_unlock(&image_mu);
    }];
}

void present_image_diag_tick(uint32_t ticks, uint32_t flow) {
    if (image_mode == IMAGE_OFF) return;
    if (image_mode == IMAGE_HOLD) {
        static uint64_t race_ticks;
        race_ticks = flow == 6 ? race_ticks + ticks : 0;
        atomic_store_explicit(&image_hold, race_ticks >= 600, memory_order_relaxed);
    }
    pthread_mutex_lock(&image_mu);
    image_diag.tick += ticks;
    uint64_t age = image_diag.shown ? image_diag.tick - image_diag.changed : 0;
    if (age > image_diag.peak) image_diag.peak = age;
    bool crossed = age > 30 && !image_diag.stale;
    if (crossed) image_diag.stale = true;
    uint64_t frame = image_diag.frame, drawable = image_diag.drawable, hash = image_diag.hash;
    pthread_mutex_unlock(&image_mu);
    if (crossed) {
        const char *message = "present-image stale: sim_ticks=%llu shown_frame=%llu drawable=%llu hash=%016llx";
        diag_event(message, (unsigned long long)age, (unsigned long long)frame,
                   (unsigned long long)drawable, (unsigned long long)hash);
        report_note(message, (unsigned long long)age, (unsigned long long)frame,
                    (unsigned long long)drawable, (unsigned long long)hash);
    }
}

void present_image_diag_second(uint64_t frame, uint32_t flow) {
    pthread_mutex_lock(&image_mu);
    uint64_t age = image_diag.shown ? image_diag.tick - image_diag.changed : 0;
    uint64_t peak = image_diag.peak, encoded = image_diag.encoded, shown = image_diag.shown, failed = image_diag.failed;
    image_diag.peak = age;
    pthread_mutex_unlock(&image_mu);
    diag_event("present-image frame=%llu flow=%u hold=%d sim_ticks=%llu peak=%llu encoded=%llu shown=%llu failed=%llu",
               (unsigned long long)frame, flow, atomic_load_explicit(&image_hold, memory_order_relaxed),
               (unsigned long long)age, (unsigned long long)peak, (unsigned long long)encoded,
               (unsigned long long)shown, (unsigned long long)failed);
}
