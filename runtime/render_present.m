// render_present.m - Present, frame timing and diag capture encoding (split from render.m; shared state in render_internal.h).
#include "render_internal.h"
#include "brightness.h"
#import <Cocoa/Cocoa.h>
#include "settings.h"
#include "framecap.h"

// ---------------- present ----------------
// The game thread has no run loop, so nothing drained its autorelease pool: every frame's render pass descriptors,
// encoders and command buffers stayed alive (~11 MB a minute). A pool now spans each frame, from Present to Present.
void *objc_autoreleasePoolPush(void);
void objc_autoreleasePoolPop(void *pool);
static void frame_pool_cycle(void) {
    static __thread void *pool;
    if (pool) objc_autoreleasePoolPop(pool);
    pool = objc_autoreleasePoolPush();
}
// Per-frame GPU timing for stats.log: how long Present waits for the GPU (for the previous frame), and the GPU
// time of each frame's last command buffer (from its completion handler).
static uint64_t wait_ns, gpu_ns, timed_frames;
static uint64_t wall_acc_ns, wall_acc_n;   // perf.tsv gpu_wall: kernelStart (queued to the GPU) to GPUEnd, per completed command buffer
// Intervals between frames actually shown, by length: <12 ms, 12-20.5, 20.5-29, longer; [4] = dropped drawables.
static uint64_t present_hist[5];
void render_present_hist(uint64_t out[5]) { for (int i = 0; i < 5; ++i) out[i] = __atomic_exchange_n(&present_hist[i], 0, __ATOMIC_RELAXED); }
static pthread_mutex_t presentation_mu = PTHREAD_MUTEX_INITIALIZER;
static RenderPresentation presentation;
static double last_presented;
static double gpu_average;              // seconds; completion-thread EMA, only used by opt-in adaptive scheduling
// Called on the game thread. This is presentation policy, never a sim clock.
void render_presentation_stats(RenderPresentation *out) {
    static double next_display, refresh_ms = 1000.0 / 60, min_ms, max_ms, granularity_ms;
    static int vrr, native_fullscreen;
    double now = host_ticks() / 1e9;
    if (now >= next_display) {
        next_display = now + 1;
        NSWindow *window = [layer.delegate isKindOfClass:NSView.class] ? ((NSView *)layer.delegate).window : nil;
        NSScreen *s = window.screen ?: NSScreen.mainScreen;
        double hz = s.maximumFramesPerSecond;
        refresh_ms = 1000.0 / (hz > 0 ? hz : 60);
        min_ms = max_ms = granularity_ms = 0;
        if (@available(macOS 12.0, *)) {
            min_ms = s.minimumRefreshInterval * 1000;
            max_ms = s.maximumRefreshInterval * 1000;
            granularity_ms = s.displayUpdateGranularity * 1000;
            if (min_ms > 0) refresh_ms = min_ms;
        }
        vrr = min_ms > 0 && max_ms > min_ms + 0.01 && granularity_ms == 0;
        native_fullscreen = !!(window.styleMask & NSWindowStyleMaskFullScreen);
    }
    static const int limits[] = {0, 120, 60, 40};
    int choice = settings_get("graphics.frame_rate_limit");
    int limit = choice >= 0 && choice < 4 ? limits[choice] : 0;
    pthread_mutex_lock(&presentation_mu);
    presentation.limit = limit;
    presentation.refresh_ms = refresh_ms;
    presentation.min_ms = min_ms; presentation.max_ms = max_ms; presentation.granularity_ms = granularity_ms;
    presentation.vrr_available = vrr; presentation.native_fullscreen = native_fullscreen;
    presentation.vrr_requested = settings_get("graphics.variable_refresh") == 1;
    presentation.adaptive_scheduling = presentation.vrr_requested && vrr && native_fullscreen &&
        (!limit || 1000.0 / limit <= max_ms);
    presentation.display_sync = layer ? layer.displaySyncEnabled : 1;
    presentation.duration_ms = framecap_duration(1000.0 / refresh_ms, limit) * 1000;
    if (presentation.adaptive_scheduling) {
        double target = limit ? 1000.0 / limit : gpu_average * 1000;
        presentation.duration_ms = fmax(min_ms, fmin(max_ms, target));
    }
    *out = presentation;
    pthread_mutex_unlock(&presentation_mu);
}
static void presented(id<MTLDrawable> drawable) {
    // Metal reports actual display time here, not submission or GPU completion.
    double t = drawable.presentedTime;
    pthread_mutex_lock(&presentation_mu);
    if (t <= 0) {
        ++presentation.shown[4];
        __atomic_fetch_add(&present_hist[4], 1, __ATOMIC_RELAXED);
    } else if (t > last_presented) {
        if (last_presented > 0) {
            double ms = (t - last_presented) * 1000;
            ++presentation.shown[framecap_interval_bucket(ms)];
            __atomic_fetch_add(&present_hist[ms < 12 ? 0 : ms < 20.5 ? 1 : ms < 29 ? 2 : 3], 1, __ATOMIC_RELAXED);
        }
        last_presented = t;
    }
    pthread_mutex_unlock(&presentation_mu);
}
void render_frame_timing(double *wait_ms, double *gpu_ms) {
    uint64_t n = __atomic_exchange_n(&timed_frames, 0, __ATOMIC_RELAXED), g = __atomic_exchange_n(&gpu_ns, 0, __ATOMIC_RELAXED);
    *wait_ms = n ? wait_ns / 1e6 / n : 0;
    *gpu_ms = n ? g / 1e6 / n : 0;
    wait_ns = 0;
}
// perf.tsv's gpu_wall: mean ms from the command buffer reaching the GPU queue (kernelStartTime) to its GPU end since the last call.
// Beside gpu_mean (GPUStart..GPUEnd) it separates a GPU that runs the work slower from one that starts it late (waiting behind
// another client's work or a clock ramp).
double render_gpu_wall_ms(void) {
    uint64_t n = __atomic_exchange_n(&wall_acc_n, 0, __ATOMIC_RELAXED), w = __atomic_exchange_n(&wall_acc_ns, 0, __ATOMIC_RELAXED);
    return n ? w / 1e6 / n : 0;
}
// ---------------- diag captures, encoded off the game thread ----------------
// Full-size PNGs take ~100-300 ms to encode at 4K: done on the game thread (or in a command buffer's completion
// handler, which holds back every later frame's completion) that stalled the game and, at 120 Hz, went past the
// 100 ms catch-up limit, so diag runs lost game time. The frame's copy is a shared buffer filled by the GPU; once
// its command buffer completes, a background queue encodes it. At most 8 single shots wait (a 4K frame is 33 MB);
// a BOOT_DIAG_SEQ burst (already held in memory) is queued whole.
static dispatch_queue_t png_q;
static dispatch_semaphore_t png_slots;
static void png_drain(void) { if (png_q) dispatch_sync(png_q, ^{}); }   // at exit: finish the files
static void png_after(id<MTLCommandBuffer> cb, id<MTLBuffer> buf, const char *path, uint32_t w, uint32_t h, int bounded) {
    if (!png_q) {
        png_q = dispatch_queue_create("nfsmw.diagpng", DISPATCH_QUEUE_SERIAL);
        png_slots = dispatch_semaphore_create(8);
        atexit(png_drain);
    }
    if (bounded) dispatch_semaphore_wait(png_slots, DISPATCH_TIME_FOREVER);   // earlier shots are on committed frames
    char *p = strdup(path);
    [cb addCompletedHandler:^(id<MTLCommandBuffer> b) {
        (void)b;
        dispatch_async(png_q, ^{
            diag_png(p, buf.contents, w, h, w * 4, 0);
            free(p);
            if (bounded) dispatch_semaphore_signal(png_slots);
        });
    }];
}
// ---------------- presenting ----------------
// The finished frame is copied to present_tex in the frame's command buffer; a serial queue then takes a drawable
// from the layer and shows the copy. While the compositor isn't showing the window (covered, hidden, another
// Space) the layer hands out no drawables and nextDrawable blocks for up to a second. That wait happens on the
// presenter queue and frames finished meanwhile aren't shown, so the game never waits for the compositor.
// Order on the GPU: the presenter's command buffer is committed after the frame's, and the next copy into
// present_tex is only encoded once the presenter has committed, so the queue never overwrites a frame being shown.
static dispatch_queue_t present_q;
static _Atomic int presenting;
void overlay_present(id<MTLCommandBuffer> cb);
// The game's gamma ramp (SetGammaRamp, its Brightness option), as D3D9 applies it to a fullscreen swap chain: per channel
// out = ramp[in] / 65535 for the 8-bit value, on the finished frame (post chain and HUD). Applied in the copy to
// present_tex, so the F10 menu (its own window) and the performance overlay (its own layer) are not graded. The identity
// ramp (the default) keeps the plain blit.
static float gamma_lut[768];
static int gamma_active, present_graded;   // present_graded: this frame's present_tex has the ramp (diag shots then read it)
void render_set_gamma(const uint16_t ramp[768]) {
    // Test-only override wins over boot preferences, slider previews and guest SetGammaRamp calls.
    static int test_checked, test_active;
    static uint16_t test_ramp[768];
    if (!test_checked) {
        test_checked = 1;
        const char *e = getenv("NFS_TEST_GAMMA");
        double gamma = e ? atof(e) : 0;
        test_active = isfinite(gamma) && gamma > 0;
        if (test_active)
            for (int i = 0; i < 768; ++i)
                test_ramp[i] = (uint16_t)(65535.0 * pow((i & 255) / 255.0, 1.0 / gamma) + 0.5);
    }
    if (test_active) ramp = test_ramp;
    int identity = 1;
    for (int i = 0; i < 768; ++i) {
        gamma_lut[i] = ramp[i] / 65535.0f;
        if (ramp[i] != (i & 255) * 257) identity = 0;
    }
    gamma_active = !identity;
}
static const char *const gamma_src =
"#include <metal_stdlib>\nusing namespace metal;\n"
"struct VOut { float4 pos [[position]]; float2 uv; };\n"
"vertex VOut gamma_vs(uint vid [[vertex_id]]) { float2 p = float2((vid << 1) & 2, vid & 2); VOut o; o.pos = float4(p * float2(2, -2) + float2(-1, 1), 0, 1); o.uv = p; return o; }\n"
"fragment float4 gamma_fs(VOut i [[stage_in]], texture2d<float> s [[texture(0)]], constant float *lut [[buffer(0)]]) {\n"
"    constexpr sampler pt(filter::nearest, address::clamp_to_edge);\n"
"    float4 c = s.sample(pt, i.uv); uint3 n = uint3(clamp(c.rgb, 0.0, 1.0) * 255.0 + 0.5);\n"
"    return float4(lut[n.r], lut[256 + n.g], lut[512 + n.b], c.a);\n"
"}\n";
static void gamma_blit(void) {
    static id<MTLRenderPipelineState> pso;
    if (!pso) {
        NSError *err = nil;
        id<MTLLibrary> lib = [mtl newLibraryWithSource:[NSString stringWithUTF8String:gamma_src] options:nil error:&err];
        MTLRenderPipelineDescriptor *pd = [MTLRenderPipelineDescriptor new];
        pd.vertexFunction = [lib newFunctionWithName:@"gamma_vs"];
        pd.fragmentFunction = [lib newFunctionWithName:@"gamma_fs"];
        pd.colorAttachments[0].pixelFormat = present_tex.pixelFormat;
        pso = [mtl newRenderPipelineStateWithDescriptor:pd error:&err];
        if (!pso) { fprintf(stderr, "boot: gamma ramp pipeline: %s\n", err.localizedDescription.UTF8String); gamma_active = 0; }
    }
    if (!pso) return;
    MTLRenderPassDescriptor *rp = [MTLRenderPassDescriptor renderPassDescriptor];
    rp.colorAttachments[0].texture = present_tex; rp.colorAttachments[0].loadAction = MTLLoadActionDontCare; rp.colorAttachments[0].storeAction = MTLStoreActionStore;
    if (gpt_on) gpt_render_auto(rp, "gamma");
    id<MTLRenderCommandEncoder> e = [cmd renderCommandEncoderWithDescriptor:rp];
    if (gpt_on) gpt_name(e);
    [e setRenderPipelineState:pso];
    [e setFragmentTexture:backbuffer_tex atIndex:0];
    [e setFragmentBytes:gamma_lut length:sizeof gamma_lut atIndex:0];
    [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
    [e endEncoding];
}
static int present_copy(void) {
    static int brightness_started;
    if (!brightness_started) { brightness_started = 1; brightness_init(); }
    if (!layer || presenting || user32_window_occluded()) return 0;   // occluded: nothing would be shown
    if (!present_q) present_q = dispatch_queue_create("nfsmw.present", DISPATCH_QUEUE_SERIAL);
    present_graded = gamma_active && backbuffer_tex.width == present_tex.width && backbuffer_tex.height == present_tex.height;
    if (present_graded) gamma_blit();
    else {
        id<MTLBlitCommandEncoder> b = gpt_blit(cmd, "blit present copy");
        [b copyFromTexture:backbuffer_tex toTexture:present_tex];
        [b endEncoding];
    }
    presenting = 1;
    return 1;
}
static void present_show(void) {
    id<MTLTexture> src = present_tex;
    CAMetalLayer *l = layer;
    RenderPresentation policy;
    render_presentation_stats(&policy);
    double duration = policy.duration_ms / 1000;
    dispatch_queue_t q = present_q;
    rt_after_commit(cmd, ^{                  // behind the frame's commit (render_thread.m replays it later)
        dispatch_async(q, ^{
            @autoreleasepool {
                id<CAMetalDrawable> d = [l nextDrawable];
                if (d) {
                    [d addPresentedHandler:^(id<MTLDrawable> dr) { presented(dr); }];
                    id<MTLCommandBuffer> cb = [queue commandBuffer];
                    quad_into(cb, src, MTLRegionMake2D(0, 0, src.width, src.height), d.texture, 0, 0,
                              MTLRegionMake2D(0, 0, d.texture.width, d.texture.height), 1);
                    overlay_present(cb);         // the performance overlay, on its own layer above (overlay.m)
                    // Metal anchors this to the previous actual presentation, including
                    // after a late frame. No CPU sleep/deadline and no change to pacing.c.
                    if (duration > 0) {
                        [cb presentDrawable:d afterMinimumDuration:duration];
                    } else [cb presentDrawable:d];
                    [cb commit];
                }
                presenting = 0;
            }
        });
    });
}

void render_present(void) {
    if (!mtl) return;
    frame_pool_cycle();
    if (bb_rt.ms && (probe_sig & PROBE_MSAA)) bb_rt.stale = resolve_magenta = 1;   // BOOT_DIAG_PROBE msaa (probe.m)
    msaa_resolve(&bb_rt);                 // AA on: backbuffer_tex from the multisampled back buffer (see MSAA)
    resolve_magenta = 0;
    end_pass();
    ensure_cmd();
    void probe_present(id<MTLCommandBuffer> cb, id<MTLTexture> shown);
    probe_present(cmd, backbuffer_tex);   // BOOT_DIAG_PROBE: what is shown, captured (probe.m)
    int show = present_copy();
    // BOOT_SHOT=frame:path.png[,frame:path.png...] saves the back buffer (debugging without a screen)
    id<MTLBuffer> shot = nil;
    char shot_path[512] = "";
    const char *spec = getenv("BOOT_SHOT");
    for (const char *p = spec; p && *p;) {
        unsigned long long fr = strtoull(p, (char **)&p, 10);
        if (*p != ':') break;
        const char *e = strchr(++p, ',');
        size_t n = e ? (size_t)(e - p) : strlen(p);
        if (fr == dev.frames && n < sizeof shot_path) { memcpy(shot_path, p, n); shot_path[n] = 0; }
        p = e ? e + 1 : p + n;
    }
    int diag_simshot_due(char *path, size_t n);
    if (!shot_path[0]) diag_simshot_due(shot_path, sizeof shot_path);
    int postfx_ab_shot(char *path, size_t n);                                 // x360_post.m: same-picture PC / X360 pairs
    postfx_ab_shot(shot_path, sizeof shot_path);
    int carpaint_ab_shot(char *path, size_t n);                               // materials.m: same-frame X360 / PC car paint
    carpaint_ab_shot(shot_path, sizeof shot_path);
    int detail_ab_shot(char *path, size_t n);                                 // detail.c: same-frame detail distances
    detail_ab_shot(shot_path, sizeof shot_path);
    int report_shot(char *path, size_t n);                                    // report.m: the frame when the F10 menu opened
    report_shot(shot_path, sizeof shot_path);
    int due = shot_path[0] ? 0 : diag_shot_due(dev.frames);
    if (due) snprintf(shot_path, sizeof shot_path, "%s/frame_%05llu.%s", diag_dir, (unsigned long long)dev.frames, due == 2 ? "png" : "jpg");
    if (due == 2) postfx_ab_shot(shot_path, sizeof shot_path);              // a full-size frame shot also starts a postfx A/B set (front end)
    if (shot_path[0]) {
        id<MTLTexture> shot_src = show && present_graded ? present_tex : backbuffer_tex;   // with the gamma ramp, as shown
        shot = [mtl newBufferWithLength:backbuffer_tex.width * backbuffer_tex.height * 4 options:MTLResourceStorageModeShared];
        id<MTLBlitCommandEncoder> b = gpt_blit(cmd, "blit diag shot");
        [b copyFromTexture:shot_src sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0, 0, 0)
                sourceSize:MTLSizeMake(backbuffer_tex.width, backbuffer_tex.height, 1) toBuffer:shot destinationOffset:0
       destinationBytesPerRow:backbuffer_tex.width * 4 destinationBytesPerImage:backbuffer_tex.width * backbuffer_tex.height * 4];
        [b endEncoding];
    }
    // BOOT_DIAG_SEQ=<frame or race+N>:<count>: consecutive back buffers kept in memory and written as PNGs after the
    // last one (seq_NNNNN.png), so frame pacing and what moves between frames can be studied without the PNG
    // encoding slowing those frames down.
    static id<MTLBuffer> seq_buf[64];
    static uint64_t seq_frame[64];
    static int seq_n = -1, seq_have;
    static uint64_t seq_start;
    if (seq_n < 0) {
        seq_n = 0;
        const char *e = getenv("BOOT_DIAG_SEQ");
        const char *colon = e ? strchr(e, ':') : NULL;
        if (colon && diag_on) { seq_n = atoi(colon + 1); if (seq_n > 64) seq_n = 64; }
    }
    if (seq_n > 0 && seq_have < seq_n) {
        if (!seq_start && diag_frame_wanted(dev.frames, "BOOT_DIAG_SEQ")) seq_start = dev.frames;
        if (seq_start && dev.frames >= seq_start) {
            NSUInteger w = backbuffer_tex.width, h = backbuffer_tex.height;
            id<MTLBuffer> sb = [mtl newBufferWithLength:w * h * 4 options:MTLResourceStorageModeShared];
            id<MTLBlitCommandEncoder> b = gpt_blit(cmd, "blit diag shot");
            [b copyFromTexture:backbuffer_tex sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0, 0, 0) sourceSize:MTLSizeMake(w, h, 1)
                      toBuffer:sb destinationOffset:0 destinationBytesPerRow:w * 4 destinationBytesPerImage:w * h * 4];
            [b endEncoding];
            seq_frame[seq_have] = dev.frames;
            seq_buf[seq_have++] = sb;
            if (seq_have == seq_n)
                for (int i = 0; i < seq_n; ++i) {
                    char path[1024];
                    snprintf(path, sizeof path, "%s/seq_%05llu.png", diag_dir, (unsigned long long)seq_frame[i]);
                    png_after(cmd, seq_buf[i], path, (uint32_t)w, (uint32_t)h, 0);
                    seq_buf[i] = nil;
                }
        }
    }
    if (shot) png_after(cmd, shot, shot_path, (uint32_t)backbuffer_tex.width, (uint32_t)backbuffer_tex.height, 1);
    uint64_t serial = frame_serial;
    gpt_frame(cmd, serial);
    gpt_phase = "game";
    [cmd addCompletedHandler:^(id<MTLCommandBuffer> b) {
        pthread_mutex_lock(&presentation_mu);
        double gpu_time = b.GPUEndTime - b.GPUStartTime;
        if (gpu_time > 0) gpu_average = gpu_average > 0 ? 0.25 * gpu_time + 0.75 * gpu_average : gpu_time;
        pthread_mutex_unlock(&presentation_mu);
        __atomic_fetch_add(&gpu_ns, (uint64_t)((b.GPUEndTime - b.GPUStartTime) * 1e9), __ATOMIC_RELAXED);
        perf_gpu_done(serial, (b.GPUEndTime - b.GPUStartTime) * 1e3);
        __atomic_fetch_add(&timed_frames, 1, __ATOMIC_RELAXED);
        if (b.kernelStartTime > 0 && b.GPUEndTime > b.kernelStartTime) {
            __atomic_fetch_add(&wall_acc_ns, (uint64_t)((b.GPUEndTime - b.kernelStartTime) * 1e9), __ATOMIC_RELAXED);
            __atomic_fetch_add(&wall_acc_n, 1, __ATOMIC_RELAXED);
        }
        if (b.error) fprintf(stderr, "boot: render: command buffer error: %s\n", b.error.localizedDescription.UTF8String);
        pthread_mutex_lock(&done_mu);
        if (serial > done_serial) __atomic_store_n(&done_serial, serial, __ATOMIC_RELEASE);
        pthread_cond_broadcast(&done_cv);
        pthread_mutex_unlock(&done_mu);
    }];
    [cmd commit];
    if (show) present_show();
    cmd = nil;
    // One frame in flight: wait for the previous frame, not this one. The upload ring of the next frame was last
    // used two frames ago, so it is free.
    uint64_t w0 = host_ticks();
    // The frame/encoders are closed and committed, cmd is nil, and serial is a host value. Move on to the next frame
    // (its ring was freed by the previous present's wait) before unlocking, so a D3D call from another guest thread
    // during the wait never writes into the ring the GPU may still be reading.
    ++frame_serial;
    ring_idx = (ring_idx + 1) % 3;
    ring_off = 0;
    gil_release();
    wait_serial(serial - 1);
    uint64_t woke = host_ticks();
    gil_acquire();
    uint64_t acquired = host_ticks();
    wait_ns += woke - w0;
    diag_idle_ns[IDLE_GPU] += woke - w0;
    diag_idle_ns[IDLE_GIL] += acquired - woke;
    if (diag_on) {
        static uint64_t since, max_wait, max_gil;
        if (!since) since = w0;
        if (woke - w0 > max_wait) max_wait = woke - w0;
        if (acquired - woke > max_gil) max_gil = acquired - woke;
        if (acquired - since >= 1000000000ull) {
            diag_event("gpu-gil t=%.3f wait_ms=%.3f gil_ms=%.3f unlocked=1", acquired / 1e9, max_wait / 1e6, max_gil / 1e6);
            since = acquired; max_wait = max_gil = 0;
        }
    }
    render_stats_log();
    query_stats_log();
    void rt_stats_log(void);
    rt_stats_log();                       // NFS_RENDER_THREAD latency (render_thread.m)
}
