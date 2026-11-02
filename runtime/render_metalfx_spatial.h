// Standalone resolved-color spatial backend. No renderer hook: default Off.
// Windows has no MetalFX backend and keeps native resolution.
#pragma once
#import <Metal/Metal.h>

typedef NS_ENUM(NSUInteger, NFSSpatialColorMode) {
    // Caller supplies gamma-encoded color, linear [0,1], or FP16 HDR color.
    // This backend does not select the renderer's color conversion or placement.
    NFSSpatialPerceptual, NFSSpatialLinear, NFSSpatialHDR
};
typedef struct {
    NSUInteger inputWidth, inputHeight, outputWidth, outputHeight;
    MTLPixelFormat inputFormat, outputFormat;
    NFSSpatialColorMode colorMode;
} NFSSpatialConfig;

// Immutable descriptor and SDK scaler. Create on the recording thread; encode
// only through nfs_spatial_encode. Use one ordered GPU queue per cache. Textures
// for overlapping frames must be distinct (or explicitly synchronized).
@interface NFSSpatialPipeline : NSObject
@property(nonatomic, readonly) NFSSpatialConfig config;
@property(nonatomic, readonly) MTLTextureUsage inputUsage;
@property(nonatomic, readonly) MTLTextureUsage outputUsage;
- (instancetype)init NS_UNAVAILABLE;
@end

// One-entry descriptor cache, including failed creation. A changed device needs
// a new cache. A resize replaces the entry; queued packets retain the old one.
@interface NFSSpatialCache : NSObject
@property(nonatomic, readonly) NSUInteger creationAttempts;
+ (BOOL)supportsDevice:(id<MTLDevice>)device;
- (instancetype)init NS_UNAVAILABLE;
- (instancetype)initWithDevice:(id<MTLDevice>)device;
- (NFSSpatialPipeline *)pipelineForConfig:(NFSSpatialConfig)config;
@end

// End the active encoder first. YES means the operation was accepted, not GPU
// completion. NO adds no commands and leaves output untouched: the caller must
// retain its native rendering fallback. Rejects unresolved/mismatched textures,
// missing usage bits, untracked hazards, nonprivate output, identical textures
// and cross-device command buffers or resources. Avoid overlapping heap allocations or views.
// Resource ownership and copied dimensions last through completion, including
// deferred replay and cache eviction. This function never commits or waits.
BOOL nfs_spatial_encode(id<MTLCommandBuffer> buffer, NFSSpatialPipeline *pipeline,
                        id<MTLTexture> input, id<MTLTexture> output);
