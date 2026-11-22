#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <QuartzCore/CAMetalLayer.h>
#include <assert.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdbool.h>

static unsigned events, notes;
static void diag_event(const char *fmt, ...) {
    if (!strncmp(fmt, "present-image stale:", 20)) ++events;
    else if (!strncmp(fmt, "present-image unavailable:", 26)) fprintf(stderr, "%s\n", fmt);
}
static void report_note(const char *fmt, ...) {
    assert(!strncmp(fmt, "present-image stale:", 20));
    ++notes;
}

#include "present_image_diag.h"
#include "present_image_diag_impl.h"

@interface TestDrawable : NSObject
@property(nonatomic, strong) id<MTLTexture> texture;
@property(nonatomic) double presentedTime;
@property(nonatomic) NSUInteger drawableID;
@property(nonatomic, copy) MTLDrawablePresentedHandler callback;
- (void)addPresentedHandler:(MTLDrawablePresentedHandler)callback;
@end

@implementation TestDrawable
- (void)addPresentedHandler:(MTLDrawablePresentedHandler)callback { self.callback = callback; }
@end

static id<MTLCommandQueue> commands;
static TestDrawable *draw(uint64_t frame, MTLPixelFormat format, double red) {
    TestDrawable *d = [TestDrawable new];
    d.drawableID = frame % 3;
    MTLTextureDescriptor *desc = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:format
                                                      width:96 height:64 mipmapped:NO];
    desc.storageMode = MTLStorageModePrivate;
    desc.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead;
    d.texture = [commands.device newTextureWithDescriptor:desc];
    assert(d.texture);
    id<MTLCommandBuffer> cb = [commands commandBuffer];
    MTLRenderPassDescriptor *pass = [MTLRenderPassDescriptor renderPassDescriptor];
    pass.colorAttachments[0].texture = d.texture;
    pass.colorAttachments[0].loadAction = MTLLoadActionClear;
    pass.colorAttachments[0].storeAction = MTLStoreActionStore;
    pass.colorAttachments[0].clearColor = MTLClearColorMake(red, 0, 0, 1);
    [[cb renderCommandEncoderWithDescriptor:pass] endEncoding];
    present_image_encode(cb, (id<CAMetalDrawable>)d, frame);
    if (d.callback && frame == 1) {
        d.presentedTime = 1;
        d.callback((id<MTLDrawable>)d); // Callback first. No completed GPU sample yet.
        assert(!image_diag.shown);
        present_image_diag_tick(1, 6);
    }
    [cb commit];
    [cb waitUntilCompleted];
    assert(cb.status == MTLCommandBufferStatusCompleted);
    return d;
}

static void show(TestDrawable *d, double time) {
    assert(d.callback);
    d.presentedTime = time;
    d.callback((id<MTLDrawable>)d);
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        assert(argc == 2);
        CAMetalLayer *layer = [CAMetalLayer layer];
        layer.device = MTLCreateSystemDefaultDevice();
        assert(layer.device);
        layer.framebufferOnly = YES;
        commands = [layer.device newCommandQueue];
        setenv("BOOT_DIAG_PRESENT_IMAGE", argv[1], 1);
        present_image_diag_init(layer);
        TestDrawable *first = draw(1, MTLPixelFormatBGRA8Unorm, 0);
        if (!strcmp(argv[1], "off")) {
            present_image_diag_tick(60, 6);
            assert(layer.framebufferOnly && !image_pipeline && !first.callback && !image_diag.encoded);
            puts("Off encoded=0");
            return 0;
        }
        assert(!layer.framebufferOnly && image_diag.encoded == 1 && image_diag.shown == 1);
        assert(image_diag.changed == 0 && image_diag.tick == 1);
        uint64_t initial = image_diag.hash;
        if (!strcmp(argv[1], "hold")) {
            // Start a fresh hold at the current tick, then run 60 more ticks.
            present_image_diag_tick(600, 6);
            assert(atomic_load(&image_hold));
            show(draw(2, MTLPixelFormatBGRA8Unorm, .25), 2);
            assert(image_diag.hash != initial && image_diag.changed == image_diag.tick);
            uint64_t held = image_diag.hash;
            image_diag.peak = events = notes = 0;
            present_image_diag_tick(30, 6);
            show(draw(3, MTLPixelFormatBGRA8Unorm, .75), 3);
            assert(!events && image_diag.hash == held && image_diag.shown == 3);
            present_image_diag_tick(1, 6);
            present_image_diag_tick(29, 6);
            assert(events == 1 && notes == 1 && image_diag.peak == 60);
            assert(image_diag.tick - image_diag.changed == 60 && !image_diag.failed);
            puts("held peak=60, events=1, report notes=1");
            return 0;
        }
        uint64_t frame = 1;
        MTLPixelFormat formats[] = {MTLPixelFormatBGRA8Unorm, MTLPixelFormatRGBA16Float};
        for (unsigned format = 0; format < 2; ++format) {
            for (unsigned i = 0; i < 32; ++i) {
                ++frame;
                show(draw(frame, formats[format], (i + 1) / 32.0), frame);
                assert(image_diag.changed == image_diag.tick && !image_diag.failed);
                present_image_diag_tick(1, 6);
            }
        }
        assert(image_diag.peak == 1 && !events && !notes);
        puts("live peak=1, events=0, SDR and EDR");
        uint64_t last = image_diag.hash;
        present_image_diag_tick(31, 6);
        TestDrawable *dropped = draw(frame + 1, MTLPixelFormatBGRA8Unorm, 0);
        show(dropped, 0);
        show(dropped, 2); // A late older presentation also leaves the current image alone.
        assert(image_diag.hash == last && image_diag.shown == frame && events == 1);
    }
}
