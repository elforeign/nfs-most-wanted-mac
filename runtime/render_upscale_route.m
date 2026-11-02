#import "render_upscale_route.h"
#include <math.h>
#include <stdlib.h>
#include <string.h>

BOOL nfs_upscale_requested(void) {
    static int enabled=-1;
    if(enabled<0) {
        const char *s=getenv("NFS_UPSCALE");
        enabled=s && !strcmp(s,"spatial");
    }
    return enabled;
}

MTLRegion nfs_upscale_region(MTLRegion r, NSUInteger lw, NSUInteger lh, NSUInteger pw, NSUInteger ph) {
    if(!lw || !lh || !r.size.width || !r.size.height)return MTLRegionMake2D(0,0,0,0);
    NSUInteger x=(NSUInteger)fmin(pw,floor((double)r.origin.x*pw/lw));
    NSUInteger y=(NSUInteger)fmin(ph,floor((double)r.origin.y*ph/lh));
    NSUInteger ex=(NSUInteger)fmin(pw,ceil(((double)r.origin.x+r.size.width)*pw/lw));
    NSUInteger ey=(NSUInteger)fmin(ph,ceil(((double)r.origin.y+r.size.height)*ph/lh));
    return MTLRegionMake2D(x,y,ex>x?ex-x:0,ey>y?ey-y:0);
}
MTLViewport nfs_upscale_viewport(MTLViewport v, NSUInteger lw, NSUInteger lh, NSUInteger pw, NSUInteger ph) {
    if(lw && lh) {v.originX*= (double)pw/lw;v.width*= (double)pw/lw;
                  v.originY*= (double)ph/lh;v.height*= (double)ph/lh;}
    return v;
}

@interface NFSUpscaleRoute ()
- (id<MTLRenderPipelineState>)depthPipelineFrom:(NSUInteger)sourceSamples to:(NSUInteger)targetSamples;
@end
@interface NFSUpscaleCopyTarget ()
@property(nonatomic, strong) id<MTLTexture> color, multisample;
@end
@implementation NFSUpscaleCopyTarget
@end
@implementation NFSUpscaleRoute {
    id<MTLDevice> _device;
    NFSSpatialCache *_cache;
    NSMutableDictionary<NSNumber *,id<MTLRenderPipelineState>> *_depthPipelines;
    id<MTLDepthStencilState> _depthState;
    NSMutableDictionary<NSNumber *,NSMutableArray<NFSUpscaleCopyTarget *> *> *_copies;
    NSMutableDictionary<NSValue *,id<MTLTexture>> *_stencilViews;
}
static id<MTLTexture> route_texture(id<MTLDevice> device, MTLPixelFormat format,
                                   NSUInteger w, NSUInteger h, NSUInteger samples, MTLTextureUsage extra) {
    MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:format width:w height:h mipmapped:NO];
    d.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead|extra;
    d.storageMode=MTLStorageModePrivate;
    if(samples>1){d.textureType=MTLTextureType2DMultisample;d.sampleCount=samples;}
    return [device newTextureWithDescriptor:d];
}
- (instancetype)initWithDevice:(id<MTLDevice>)device width:(NSUInteger)w height:(NSUInteger)h
                         format:(MTLPixelFormat)format samples:(NSUInteger)samples {
    if(!(self=[super init]))return nil;
    if(!w || !h || w>16384 || h>16384 || w<3 || h<3 || !samples ||
       ![NFSSpatialCache supportsDevice:device] || ![device supportsTextureSampleCount:samples])return nil;
    _device=device;_samples=samples;
    _copies=[NSMutableDictionary new];
    _stencilViews=[NSMutableDictionary new];
    _cache=[[NFSSpatialCache alloc] initWithDevice:device];
    NSUInteger iw=(w*2+2)/3,ih=(h*2+2)/3;
    NFSSpatialConfig c={iw,ih,w,h,format,format,format==MTLPixelFormatRGBA16Float?NFSSpatialHDR:NFSSpatialPerceptual};
    NFSSpatialPipeline *pipeline=[_cache pipelineForConfig:c];
    if(!pipeline)return nil;
    _worldColor=route_texture(device,format,iw,ih,1,pipeline.inputUsage);
    _composition=route_texture(device,format,w,h,1,pipeline.outputUsage);
    _worldDepth=route_texture(device,MTLPixelFormatDepth32Float_Stencil8,iw,ih,1,MTLTextureUsagePixelFormatView);
    if(samples>1) {
        _worldMSAA=route_texture(device,format,iw,ih,samples,0);
        _worldDepthMSAA=route_texture(device,MTLPixelFormatDepth32Float_Stencil8,iw,ih,samples,MTLTextureUsagePixelFormatView);
    }
    if(!_worldColor || !_composition || !_worldDepth || (samples>1 && (!_worldMSAA || !_worldDepthMSAA)))return nil;
    _depthPipelines=[NSMutableDictionary new];
    MTLDepthStencilDescriptor *ds=[MTLDepthStencilDescriptor new];
    ds.depthCompareFunction=MTLCompareFunctionAlways;ds.depthWriteEnabled=YES;
    MTLStencilDescriptor *st=[MTLStencilDescriptor new];
    st.stencilCompareFunction=MTLCompareFunctionAlways;st.depthStencilPassOperation=MTLStencilOperationReplace;
    ds.frontFaceStencil=ds.backFaceStencil=st;
    _depthState=[device newDepthStencilStateWithDescriptor:ds];
    // Compile both directions before selecting lower-resolution rendering.
    if(!_depthState || ![self depthPipelineFrom:samples to:samples] ||
       ![self prepareDepthSource:_worldDepthMSAA?:_worldDepth])return nil;
    _allocatedBytes=_worldColor.allocatedSize+_composition.allocatedSize+_worldDepth.allocatedSize+
                    _worldMSAA.allocatedSize+_worldDepthMSAA.allocatedSize;
    return self;
}
- (BOOL)matchesWidth:(NSUInteger)w height:(NSUInteger)h format:(MTLPixelFormat)format samples:(NSUInteger)samples {
    return _composition.width==w && _composition.height==h && _composition.pixelFormat==format && _samples==samples;
}
- (NFSUpscaleCopyTarget *)copyTargetForKey:(uintptr_t)key current:(id<MTLTexture>)current
                                   width:(NSUInteger)w height:(NSUInteger)h
                                  format:(MTLPixelFormat)format samples:(NSUInteger)samples {
    NSMutableArray<NFSUpscaleCopyTarget *> *entries=_copies[@(key)];
    BOOL known=NO;for(NFSUpscaleCopyTarget *entry in entries)if(entry.color==current)known=YES;
    if(!known){entries=[NSMutableArray new];_copies[@(key)]=entries;}
    for(NFSUpscaleCopyTarget *entry in entries)
        if(entry.color.width==w && entry.color.height==h && entry.color.pixelFormat==format &&
           (entry.multisample?entry.multisample.sampleCount:1)==samples)return entry;
    NFSUpscaleCopyTarget *entry=[NFSUpscaleCopyTarget new];
    entry.color=route_texture(_device,format,w,h,1,MTLTextureUsageShaderWrite|MTLTextureUsagePixelFormatView);
    if(samples>1)entry.multisample=route_texture(_device,format,w,h,samples,0);
    if(!entry.color || (samples>1 && !entry.multisample))return nil;
    [entries addObject:entry];return entry;
}
- (NSUInteger)cachedCopyBytes {
    NSUInteger bytes=0;
    for(NSArray<NFSUpscaleCopyTarget *> *entries in _copies.allValues)
        for(NFSUpscaleCopyTarget *entry in entries)bytes+=entry.color.allocatedSize+entry.multisample.allocatedSize;
    return bytes;
}
- (void)forgetCopyKey:(uintptr_t)key { [_copies removeObjectForKey:@(key)]; }
- (BOOL)prepareDepthSource:(id<MTLTexture>)source {
    if(source.device!=_device || source.pixelFormat!=MTLPixelFormatDepth32Float_Stencil8 ||
       source.sampleCount!=_samples || !(source.usage&MTLTextureUsageShaderRead))return NO;
    NSValue *key=[NSValue valueWithPointer:(__bridge const void *)source];
    if(_stencilViews[key])return YES;
    id<MTLTexture> view=[source newTextureViewWithPixelFormat:MTLPixelFormatX32_Stencil8];
    if(!view)return NO;
    _stencilViews[key]=view;return YES;
}
- (id<MTLRenderPipelineState>)depthPipelineFrom:(NSUInteger)sourceSamples to:(NSUInteger)targetSamples {
    NSNumber *key=@(sourceSamples*32+targetSamples);
    id<MTLRenderPipelineState> p=_depthPipelines[key];if(p)return p;
    NSString *src=[NSString stringWithFormat:
        @"#include <metal_stdlib>\nusing namespace metal;\n#define MS %lu\n"
         "vertex float4 route_vs(uint i [[vertex_id]]){return float4(float2((i<<1)&2,i&2)*2-1,0,1);}\n"
         "struct D{float z [[depth(any)]];uint s [[stencil]];};\n"
         "#if MS>1\n"
         "fragment D route_fs(float4 p [[position]],depth2d_ms<float,access::read> z [[texture(0)]],texture2d_ms<uint,access::read> s [[texture(1)]],constant float2 &scale [[buffer(0)]]){uint2 q=min(uint2(p.xy*scale),uint2(z.get_width()-1,z.get_height()-1));float d=z.read(q,0);uint v=s.read(q,0).x;for(uint i=1;i<MS;i++){float n=z.read(q,i);if(n<d){d=n;v=s.read(q,i).x;}}return {d,v};}\n"
         "#else\n"
         "fragment D route_fs(float4 p [[position]],depth2d<float,access::read> z [[texture(0)]],texture2d<uint,access::read> s [[texture(1)]],constant float2 &scale [[buffer(0)]]){uint2 q=min(uint2(p.xy*scale),uint2(z.get_width()-1,z.get_height()-1));return {z.read(q),s.read(q).x};}\n"
         "#endif\n",(unsigned long)sourceSamples];
    NSError *error=nil;id<MTLLibrary> lib=[_device newLibraryWithSource:src options:nil error:&error];
    if(!lib)return nil;
    MTLRenderPipelineDescriptor *d=[MTLRenderPipelineDescriptor new];
    d.vertexFunction=[lib newFunctionWithName:@"route_vs"];d.fragmentFunction=[lib newFunctionWithName:@"route_fs"];
    d.depthAttachmentPixelFormat=d.stencilAttachmentPixelFormat=MTLPixelFormatDepth32Float_Stencil8;
    d.rasterSampleCount=targetSamples;p=[_device newRenderPipelineStateWithDescriptor:d error:&error];
    if(p)_depthPipelines[key]=p;
    return p;
}
- (BOOL)encodeColor:(id<MTLTexture>)source to:(id<MTLTexture>)output buffer:(id<MTLCommandBuffer>)buffer {
    NFSSpatialConfig c={source.width,source.height,output.width,output.height,source.pixelFormat,output.pixelFormat,
        source.pixelFormat==MTLPixelFormatRGBA16Float?NFSSpatialHDR:NFSSpatialPerceptual};
    BOOL accepted=nfs_spatial_encode(buffer,[_cache pipelineForConfig:c],source,output);
    if(accepted) {
        ++_spatialEncodes;
        [buffer addCompletedHandler:^(id<MTLCommandBuffer> done){(void)done;(void)self.allocatedBytes;}];
    }
    return accepted;
}
- (void)encodeDepth:(id<MTLTexture>)source to:(id<MTLTexture>)output buffer:(id<MTLCommandBuffer>)buffer {
    id<MTLRenderPipelineState> p=[self depthPipelineFrom:source.sampleCount to:output.sampleCount];
    id<MTLTexture> stencil=_stencilViews[[NSValue valueWithPointer:(__bridge const void *)source]];
    NSCAssert(p && stencil,@"Spatial depth descriptor changed after preflight");
    MTLRenderPassDescriptor *rp=[MTLRenderPassDescriptor renderPassDescriptor];
    rp.depthAttachment.texture=rp.stencilAttachment.texture=output;
    rp.depthAttachment.loadAction=rp.stencilAttachment.loadAction=MTLLoadActionDontCare;
    rp.depthAttachment.storeAction=rp.stencilAttachment.storeAction=MTLStoreActionStore;
    id<MTLRenderCommandEncoder> e=[buffer renderCommandEncoderWithDescriptor:rp];
    [e setRenderPipelineState:p];[e setDepthStencilState:_depthState];
    [e setFragmentTexture:source atIndex:0];[e setFragmentTexture:stencil atIndex:1];
    float scale[2]={(float)source.width/output.width,(float)source.height/output.height};
    [e setFragmentBytes:scale length:sizeof scale atIndex:0];
    [e setViewport:(MTLViewport){0,0,output.width,output.height,0,1}];
    [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[e endEncoding];
    [buffer addCompletedHandler:^(id<MTLCommandBuffer> done){(void)done;(void)self.allocatedBytes;}];
}
@end
