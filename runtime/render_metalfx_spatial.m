#import "render_metalfx_spatial.h"
#import "rt_native.h"
#import <MetalFX/MetalFX.h>

@interface NFSSpatialPipeline () {
@public
    id<MTLFXSpatialScaler> scaler API_AVAILABLE(macos(13.0));
    id<MTLDevice> device;
}
- (instancetype)initWithConfig:(NFSSpatialConfig)config device:(id<MTLDevice>)device
                        scaler:(id<MTLFXSpatialScaler>)scaler API_AVAILABLE(macos(13.0));
@end
@implementation NFSSpatialPipeline
- (instancetype)initWithConfig:(NFSSpatialConfig)c device:(id<MTLDevice>)d
                        scaler:(id<MTLFXSpatialScaler>)s {
    if ((self = [super init])) {
        _config = c; device = d; scaler = s;
        _inputUsage = s.colorTextureUsage;
        _outputUsage = s.outputTextureUsage;
    }
    return self;
}
@end

static BOOL same_config(NFSSpatialConfig a, NFSSpatialConfig b) {
    return a.inputWidth == b.inputWidth && a.inputHeight == b.inputHeight &&
        a.outputWidth == b.outputWidth && a.outputHeight == b.outputHeight &&
        a.inputFormat == b.inputFormat && a.outputFormat == b.outputFormat &&
        a.colorMode == b.colorMode;
}
// These formats cover resolved SDR and FP16 color without implicit conversion.
// The SDK creation result remains authoritative for each device/descriptor.
static BOOL color_format(MTLPixelFormat f) {
    return f == MTLPixelFormatBGRA8Unorm || f == MTLPixelFormatRGBA8Unorm ||
        f == MTLPixelFormatRGBA16Float;
}
static BOOL valid_config(NFSSpatialConfig c, id<MTLDevice> d) {
    // Spatial MetalFX supports 2D textures. Current macOS Metal families have
    // a 16384 texel limit; reject oversized descriptors before SDK validation.
    return d && c.inputWidth && c.inputHeight &&
        c.outputWidth >= c.inputWidth && c.outputHeight >= c.inputHeight &&
        c.outputWidth <= 16384 && c.outputHeight <= 16384 &&
        color_format(c.inputFormat) && color_format(c.outputFormat) &&
        c.colorMode <= NFSSpatialHDR &&
        (c.colorMode != NFSSpatialHDR ||
         (c.inputFormat == MTLPixelFormatRGBA16Float && c.outputFormat == MTLPixelFormatRGBA16Float));
}

@implementation NFSSpatialCache {
    id<MTLDevice> _device;
    NFSSpatialConfig _last;
    BOOL _hasLast;
    NFSSpatialPipeline *_pipeline;
}
+ (BOOL)supportsDevice:(id<MTLDevice>)device {
    if (@available(macOS 13.0, *)) {
        return device && [MTLFXSpatialScalerDescriptor class] &&
            [MTLFXSpatialScalerDescriptor supportsDevice:device];
    }
    return NO;
}
- (instancetype)initWithDevice:(id<MTLDevice>)device {
    if ((self = [super init])) _device = device;
    return self;
}
- (NFSSpatialPipeline *)pipelineForConfig:(NFSSpatialConfig)c {
    if (_hasLast && same_config(c, _last)) return _pipeline;
    _hasLast = YES; _last = c; _pipeline = nil;
    if (!valid_config(c, _device) || ![NFSSpatialCache supportsDevice:_device]) return nil;
    if (@available(macOS 13.0, *)) {
        MTLFXSpatialScalerDescriptor *d = [MTLFXSpatialScalerDescriptor new];
        d.inputWidth = c.inputWidth; d.inputHeight = c.inputHeight;
        d.outputWidth = c.outputWidth; d.outputHeight = c.outputHeight;
        d.colorTextureFormat = c.inputFormat; d.outputTextureFormat = c.outputFormat;
        d.colorProcessingMode = (MTLFXSpatialScalerColorProcessingMode)c.colorMode;
        ++_creationAttempts;
        id<MTLFXSpatialScaler> s = [d newSpatialScalerWithDevice:_device];
        if (s) _pipeline = [[NFSSpatialPipeline alloc] initWithConfig:c device:_device scaler:s];
    }
    return _pipeline;
}
@end

typedef struct { NSUInteger width, height; } SpatialPacket;
static void spatial_encode_packet(id<MTLCommandBuffer> buffer, const void *bytes,
                                  size_t size, NSArray *resources) {
    if (size != sizeof(SpatialPacket) || resources.count != 3) abort();
    const SpatialPacket *p = bytes;
    NFSSpatialPipeline *pipeline = resources[0];
    if (@available(macOS 13.0, *)) {
        // A cache hit may be recorded while a previous frame replays. Only this
        // callback touches the mutable SDK bindings, under an encoding lock.
        @synchronized (pipeline) {
            id<MTLFXSpatialScaler> s = pipeline->scaler;
            s.colorTexture = resources[1]; s.outputTexture = resources[2];
            s.inputContentWidth = p->width; s.inputContentHeight = p->height;
            [s encodeToCommandBuffer:buffer];
            s.colorTexture = nil; s.outputTexture = nil;
        }
    }
}

BOOL nfs_spatial_encode(id<MTLCommandBuffer> buffer, NFSSpatialPipeline *pipeline,
                        id<MTLTexture> input, id<MTLTexture> output) {
    if (!buffer || !pipeline || !input || !output || input == output) return NO;
    NFSSpatialConfig c = pipeline.config;
    if (buffer.device != pipeline->device ||
        input.device != pipeline->device || output.device != pipeline->device ||
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
