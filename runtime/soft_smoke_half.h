// soft_smoke_half.h - Physical soft smoke at half resolution (soft_smoke_half.m).
#pragma once
#import <Metal/Metal.h>

// GPU steps, shared with soft_smoke_half_test.m. broken selects a deliberately wrong variant for the test's
// failing control: 1 = farthest instead of nearest depth of each 2x2, 2 = plain bilinear upsampling.
void soft_half_encode_depth(id<MTLDevice> dev, id<MTLCommandBuffer> cb, id<MTLTexture> full, id<MTLTexture> half, int broken);
void soft_half_encode_composite(id<MTLDevice> dev, id<MTLCommandBuffer> cb, id<MTLTexture> scene, id<MTLTexture> layer,
                                id<MTLTexture> half_depth, id<MTLTexture> full_depth, int broken);
id<MTLTexture> soft_half_depth_texture(id<MTLDevice> dev, id<MTLTexture> have, NSUInteger w, NSUInteger h);

#ifndef SOFT_SMOKE_HALF_TEST
int soft_smoke_half_on(void);               // default 1; NFS_SOFT_SMOKE_HALF=0: full-resolution layer
void soft_smoke_half_warm(void);            // load-time pipelines (render_lazy_warm)
// The frame's late smoke lists into the half layer (scaled replay against the half depth).
void soft_smoke_half_replay(NSArray *draws, id<MTLTexture> layer);
// Blend the half layer over the full scene with nearest-depth upsampling.
void soft_smoke_half_composite(id<MTLTexture> scene, id<MTLTexture> layer);
id<MTLTexture> soft_smoke_half_depth(void);  // this frame's half depth (the layer's occlusion and upsample reference)
extern const char soft_half_up_msl[];        // MSL: float4 half_up(layer, half depth, full depth, pixel, plain)
// BOOT_DIAG_SOFT_SMOKE_HALF_AB=1: replay the same lists again (kept) at the other resolution and with the depth fade
// off, into scratch layers, so gpupass.log prices all three on identical draws every frame.
int soft_smoke_half_ab_on(void);
void soft_smoke_half_ab(NSArray *draws, id<MTLTexture> layer, int half);
#endif
