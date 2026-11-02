// Physical spatial routing. Guest presentation parameters remain unchanged.
// Default Off; NFS_UPSCALE=spatial selects ceil(2/3 * output) on each world axis.
// Unsupported devices/depth, split layouts, incompatible bindings and initial
// allocation failures retain native rendering. A rejected composition encode
// uses the ordinary linear quad; HUD still draws at output resolution. Windows
// retains native resolution and does not link this MetalFX route.
#pragma once
#import <Metal/Metal.h>
#import "render_metalfx_spatial.h"

BOOL nfs_upscale_requested(void);
// Half-open rectangles use floor at the first edge and ceil at the last edge.
MTLRegion nfs_upscale_region(MTLRegion region, NSUInteger logicalWidth, NSUInteger logicalHeight,
                             NSUInteger physicalWidth, NSUInteger physicalHeight);
MTLViewport nfs_upscale_viewport(MTLViewport viewport, NSUInteger logicalWidth, NSUInteger logicalHeight,
                                NSUInteger physicalWidth, NSUInteger physicalHeight);

@interface NFSUpscaleCopyTarget : NSObject
@property(nonatomic, readonly) id<MTLTexture> color, multisample;
@end

// One ordered GPU queue owns a route. Resizing replaces this immutable resource
// generation; encoded commands retain the generation until completion.
@interface NFSUpscaleRoute : NSObject
@property(nonatomic, readonly) id<MTLTexture> worldColor, worldMSAA, worldDepth, worldDepthMSAA, composition;
@property(nonatomic, readonly) NSUInteger allocatedBytes;
@property(nonatomic, readonly) NSUInteger samples;
@property(nonatomic, readonly) NSUInteger spatialEncodes;
@property(nonatomic, readonly) NSUInteger cachedCopyBytes;
- (instancetype)initWithDevice:(id<MTLDevice>)device width:(NSUInteger)width height:(NSUInteger)height
                         format:(MTLPixelFormat)format samples:(NSUInteger)samples;
- (BOOL)matchesWidth:(NSUInteger)width height:(NSUInteger)height format:(MTLPixelFormat)format samples:(NSUInteger)samples;
// Reuse both physical sizes across frames. A newly created/reused guest object
// invalidates its cache when its current texture is not one of these targets.
// Both color and MSAA allocation succeed before replacing any bound resource.
- (NFSUpscaleCopyTarget *)copyTargetForKey:(uintptr_t)key current:(id<MTLTexture>)current
                                   width:(NSUInteger)width height:(NSUInteger)height
                                  format:(MTLPixelFormat)format samples:(NSUInteger)samples;
- (void)forgetCopyKey:(uintptr_t)key;
- (BOOL)prepareDepthSource:(id<MTLTexture>)source;
// End all encoders first. Resolved color only. NO adds no commands; the caller
// uses its ordinary scaled quad as the last-resort composition fallback.
- (BOOL)encodeColor:(id<MTLTexture>)source to:(id<MTLTexture>)output buffer:(id<MTLCommandBuffer>)buffer;
// Copies depth AND stencil, selecting the nearest covered MSAA depth sample.
// Both directions preserve pre-world clears and world depth for native post/HUD.
- (void)encodeDepth:(id<MTLTexture>)source to:(id<MTLTexture>)output buffer:(id<MTLCommandBuffer>)buffer;
@end
