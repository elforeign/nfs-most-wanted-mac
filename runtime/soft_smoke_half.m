// soft_smoke_half.m - Physical soft smoke at half resolution, the default (NFS_SOFT_SMOKE_HALF=0: the full-resolution
// layer, for A/B). Off-screen particles after Cantlay, "High-Speed, Off-Screen Particles",
// GPU Gems 3 ch. 23 (2007): the late smoke lists (render.m soft_late_*) replay into a layer of half the width and
// height, viewports and scissors scaled at replay (rt_render_list_replay_scaled), against a half-size copy of the
// opaque depth that keeps the nearest of each 2x2. A full-size pass then blends the premultiplied layer over the
// scene with nearest-depth upsampling: where the four layer texels around a pixel lie within 10 % of its view depth
// it filters bilinearly, otherwise it takes the texel whose depth is nearest the pixel's, so smoke stays off a
// nearer silhouette and fills the farther side of it. Smoke fill drops to a quarter of the pixels; the depth copy
// and the upsample each touch the full frame once. Full resolution: render.m takes none of these paths.
#import "soft_smoke_half.h"
#import "rt_native.h"
#ifndef SOFT_SMOKE_HALF_TEST
#include "render_internal.h"
#include "materials.h"
extern int postfx_look_variant(const char *v);
#endif

// BEGIN soft smoke half shaders
// The upsample, shared with the contact-shadow multiply (render_post.m), which blends the layer in its own pass.
// Relative view-depth difference from raw depth: view z is proportional to 1/(1-d) when far >> near.
const char soft_half_up_msl[] =
"inline float half_rel(float a, float b) { return abs(a-b)/max(1.0f-b,1e-7f); }\n"
"inline float4 half_up(texture2d<float> c, depth2d<float> lz, depth2d<float> fz, float2 px, uint plain) {\n"
"    float z=fz.read(uint2(px));\n"
"    float2 l=px*float2(c.get_width(),c.get_height())/float2(fz.get_width(),fz.get_height())-0.5f, f=fract(l);\n"
"    int2 b=int2(floor(l)), m=int2(c.get_width()-1,c.get_height()-1);\n"
"    float4 s[4]; float worst=0.0f, near=1e30f; int best=0;\n"
"    for (int j=0;j<4;++j) {\n"
"        uint2 q=uint2(clamp(b+int2(j&1,j>>1),int2(0),m)); s[j]=c.read(q);\n"
"        float e=half_rel(lz.read(q),z); worst=max(worst,e); if (e<near) { near=e; best=j; }\n"
"    }\n"
"    if (plain || worst<=0.1f) return mix(mix(s[0],s[1],f.x),mix(s[2],s[3],f.x),f.y);\n"
"    return s[best];\n"
"}\n";
static const char soft_half_msl[] =
"#include <metal_stdlib>\nusing namespace metal;\n"
"struct V { float4 p [[position]]; };\n"
"vertex V half_vs(uint v [[vertex_id]]) { float2 p[3]={float2(-1,-1),float2(3,-1),float2(-1,3)}; return {float4(p[v],0,1)}; }\n"
"struct Z { float d [[depth(any)]]; };\n"
"fragment Z half_depth_fs(V i [[stage_in]], depth2d<float, access::read> z [[texture(0)]], constant uint &far [[buffer(0)]]) {\n"
"    uint2 m=uint2(z.get_width()-1,z.get_height()-1), b=uint2(i.p.xy)*2;\n"
"    float a=z.read(min(b,m)), c=z.read(min(b+uint2(1,0),m)), d=z.read(min(b+uint2(0,1),m)), e=z.read(min(b+uint2(1,1),m));\n"
"    return {far ? max(max(a,c),max(d,e)) : min(min(a,c),min(d,e))};\n"
"}\n";
static const char soft_half_up_fs_msl[] =
"fragment float4 half_up_fs(V i [[stage_in]], texture2d<float> c [[texture(0)]], depth2d<float> lz [[texture(1)]],\n"
"        depth2d<float> fz [[texture(2)]], constant uint &plain [[buffer(0)]]) { return half_up(c,lz,fz,i.p.xy,plain); }\n";
// END soft smoke half shaders

static id<MTLLibrary> half_lib(id<MTLDevice> dev) {
    static id<MTLLibrary> l;
    if (!l) {
        NSString *src = [NSString stringWithFormat:@"%s%s%s", soft_half_msl, soft_half_up_msl, soft_half_up_fs_msl];
#ifdef SOFT_SMOKE_HALF_TEST
        id<MTLLibrary> soft_half_test_library(id<MTLDevice> dev, NSString *src);   // the test's own compile
        l = soft_half_test_library(dev, src);
#else
        (void)dev; l = pipeline_library_source(src, "soft_smoke_half", NULL);
#endif
    }
    return l;
}
static id<MTLRenderPipelineState> half_build(id<MTLDevice> dev, MTLRenderPipelineDescriptor *pd) {
#ifdef SOFT_SMOKE_HALF_TEST
    NSError *err = nil; id<MTLRenderPipelineState> p = [dev newRenderPipelineStateWithDescriptor:pd error:&err];
    if (!p) fprintf(stderr, "soft smoke half: %s\n", err.localizedDescription.UTF8String);
    return p;
#else
    (void)dev; return pipeline_build(pd, "soft_smoke_half", NULL);
#endif
}
static id<MTLRenderPipelineState> half_depth_pso(id<MTLDevice> dev) {
    static id<MTLRenderPipelineState> p;
    if (!p) {
        id<MTLLibrary> l = half_lib(dev); if (!l) return nil;
        MTLRenderPipelineDescriptor *pd = [MTLRenderPipelineDescriptor new];
        pd.vertexFunction = [l newFunctionWithName:@"half_vs"]; pd.fragmentFunction = [l newFunctionWithName:@"half_depth_fs"];
        pd.depthAttachmentPixelFormat = MTLPixelFormatDepth32Float;
        p = half_build(dev, pd);
    }
    return p;
}
// Premultiplied layer over the scene, colour only (the scene's alpha carries the bloom mask): as soft_late_pipeline.
static id<MTLRenderPipelineState> half_up_pso(id<MTLDevice> dev, MTLPixelFormat f) {
    static id<MTLRenderPipelineState> p[4]; static MTLPixelFormat pf[4];
    for (int i = 0; i < 4; ++i) if (p[i] && pf[i] == f) return p[i];
    id<MTLLibrary> l = half_lib(dev); if (!l) return nil;
    MTLRenderPipelineDescriptor *pd = [MTLRenderPipelineDescriptor new];
    pd.vertexFunction = [l newFunctionWithName:@"half_vs"]; pd.fragmentFunction = [l newFunctionWithName:@"half_up_fs"];
    MTLRenderPipelineColorAttachmentDescriptor *a = pd.colorAttachments[0];
    a.pixelFormat = f; a.writeMask = MTLColorWriteMaskRed | MTLColorWriteMaskGreen | MTLColorWriteMaskBlue;
    a.blendingEnabled = YES; a.sourceRGBBlendFactor = MTLBlendFactorOne; a.destinationRGBBlendFactor = MTLBlendFactorOneMinusSourceAlpha;
    id<MTLRenderPipelineState> s = half_build(dev, pd);
    for (int i = 0; i < 4 && s; ++i) if (!p[i]) { p[i] = s; pf[i] = f; break; }
    return s;
}
id<MTLTexture> soft_half_depth_texture(id<MTLDevice> dev, id<MTLTexture> have, NSUInteger w, NSUInteger h) {
    if (have && have.width == w && have.height == h) return have;
    MTLTextureDescriptor *d = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatDepth32Float width:w height:h mipmapped:NO];
    d.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead; d.storageMode = MTLStorageModePrivate;
    return [dev newTextureWithDescriptor:d];
}
static id<MTLDepthStencilState> half_always(id<MTLDevice> dev) {
    static id<MTLDepthStencilState> s;
    if (!s) { MTLDepthStencilDescriptor *d = [MTLDepthStencilDescriptor new]; d.depthCompareFunction = MTLCompareFunctionAlways; d.depthWriteEnabled = YES; s = [dev newDepthStencilStateWithDescriptor:d]; }
    return s;
}
static void half_label(MTLRenderPassDescriptor *rp, const char *what) {
#ifndef SOFT_SMOKE_HALF_TEST
    if (gpt_on) gpt_render_auto(rp, what);
#else
    (void)rp; (void)what;
#endif
}
static void half_name(id<MTLRenderCommandEncoder> e) {
#ifndef SOFT_SMOKE_HALF_TEST
    if (gpt_on) gpt_name(e);
#else
    (void)e;
#endif
}
void soft_half_encode_depth(id<MTLDevice> dev, id<MTLCommandBuffer> cb, id<MTLTexture> full, id<MTLTexture> half, int broken) {
    id<MTLRenderPipelineState> p = half_depth_pso(dev);
    if (!p || !full || !half) return;
    MTLRenderPassDescriptor *rp = [MTLRenderPassDescriptor renderPassDescriptor];
    rp.depthAttachment.texture = half; rp.depthAttachment.loadAction = MTLLoadActionDontCare; rp.depthAttachment.storeAction = MTLStoreActionStore;
    half_label(rp, "soft smoke half depth");
    id<MTLRenderCommandEncoder> e = [cb renderCommandEncoderWithDescriptor:rp]; half_name(e);
    uint32_t far = broken == 1;
    [e setRenderPipelineState:p]; [e setDepthStencilState:half_always(dev)]; [e setFragmentTexture:full atIndex:0];
    [e setFragmentBytes:&far length:sizeof far atIndex:0];
    [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3]; [e endEncoding];
}
void soft_half_encode_composite(id<MTLDevice> dev, id<MTLCommandBuffer> cb, id<MTLTexture> scene, id<MTLTexture> layer,
                                id<MTLTexture> half_depth, id<MTLTexture> full_depth, int broken) {
    id<MTLRenderPipelineState> p = half_up_pso(dev, scene.pixelFormat);
    if (!p || !layer || !half_depth || !full_depth) return;
    MTLRenderPassDescriptor *rp = [MTLRenderPassDescriptor renderPassDescriptor];
    rp.colorAttachments[0].texture = scene; rp.colorAttachments[0].loadAction = MTLLoadActionLoad; rp.colorAttachments[0].storeAction = MTLStoreActionStore;
    half_label(rp, "soft smoke half composite");
    id<MTLRenderCommandEncoder> e = [cb renderCommandEncoderWithDescriptor:rp]; half_name(e);
    uint32_t plain = broken == 2;
    [e setRenderPipelineState:p]; [e setFragmentTexture:layer atIndex:0]; [e setFragmentTexture:half_depth atIndex:1];
    [e setFragmentTexture:full_depth atIndex:2]; [e setFragmentBytes:&plain length:sizeof plain atIndex:0];
    [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3]; [e endEncoding];
}

#ifndef SOFT_SMOKE_HALF_TEST
// NFS_SOFT_SMOKE_HALF=0 (or off) selects the full-resolution layer for A/B; read once.
int soft_smoke_half_on(void) {
    if (diag_on) {                                  // held BOOT_DIAG_LOOK_AB variants for same-frame pairs
        if (postfx_look_variant("smokehalf")) return 1;
        if (postfx_look_variant("smokefull")) return 0;
    }
    static int on = -1;
    if (on < 0) { const char *e = getenv("NFS_SOFT_SMOKE_HALF"); on = !e || (strcmp(e, "0") && strcmp(e, "off")); }
    return on;
}
void soft_smoke_half_warm(void) {
    if (!half_depth_pso(mtl) || !half_up_pso(mtl, MTLPixelFormatBGRA8Unorm) || !half_up_pso(mtl, MTLPixelFormatRGBA16Float))
        if (diag_on) diag_event("pipeline_prewarm: failed=soft_smoke_half");
}
static id<MTLTexture> half_z;
static void half_replay(NSArray *draws, id<MTLTexture> layer, const RTListScale *s, int keep, const char *what) {
    MTLRenderPassDescriptor *rp = [MTLRenderPassDescriptor renderPassDescriptor];
    rp.colorAttachments[0].texture = layer; rp.colorAttachments[0].loadAction = MTLLoadActionClear;
    rp.colorAttachments[0].clearColor = MTLClearColorMake(0, 0, 0, 0); rp.colorAttachments[0].storeAction = MTLStoreActionStore;
    half_label(rp, what);
    id<MTLRenderCommandEncoder> e = [cmd renderCommandEncoderWithDescriptor:rp]; half_name(e);
    for (id list in draws) rt_render_list_replay_scaled(list, e, s, keep);
    [e endEncoding];
}
static void half_depth_now(NSUInteger w, NSUInteger h) {
    half_z = soft_half_depth_texture(mtl, half_z, w, h);
    soft_half_encode_depth(mtl, cmd, ad_rt.tex, half_z, 0);
}
void soft_smoke_half_replay(NSArray *draws, id<MTLTexture> layer) {
    half_depth_now(layer.width, layer.height);
    RTListScale s = {2, (uint32_t)layer.width, (uint32_t)layer.height, (__bridge void *)ad_rt.tex, (__bridge void *)half_z, 0, 0, 0, 0};
    half_replay(draws, layer, &s, 0, "soft smoke particles half");
}
id<MTLTexture> soft_smoke_half_depth(void) { return half_z; }
void soft_smoke_half_composite(id<MTLTexture> scene, id<MTLTexture> layer) {
    soft_half_encode_composite(mtl, cmd, scene, layer, half_z, ad_rt.tex, 0);
}
int soft_smoke_half_ab_on(void) {
    static int on = -1;
    if (on < 0) on = diag_on && getenv("BOOT_DIAG_SOFT_SMOKE_HALF_AB") != NULL;
    return on;
}
void soft_smoke_half_ab(NSArray *draws, id<MTLTexture> layer, int half) {
    static id<MTLTexture> other;
    NSUInteger w = half ? ad_rt.tex.width : (layer.width + 1) / 2, h = half ? ad_rt.tex.height : (layer.height + 1) / 2;
    if (!other || other.width != w || other.height != h) {
        MTLTextureDescriptor *d = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:layer.pixelFormat width:w height:h mipmapped:NO];
        d.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead; d.storageMode = MTLStorageModePrivate;
        other = [mtl newTextureWithDescriptor:d];
    }
    if (!other) return;
    // The other resolution, then the frame's own resolution with the depth fade off (k[1].w = 1: occlusion kept).
    if (!half) half_depth_now(w, h);
    RTListScale o = {half ? 1 : 2, (uint32_t)w, (uint32_t)h, half ? NULL : (__bridge void *)ad_rt.tex, half ? NULL : (__bridge void *)half_z, 0, 0, 0, 0};
    half_replay(draws, other, &o, 1, half ? "soft smoke A/B full" : "soft smoke A/B half");
    if (half) half_depth_now(layer.width, layer.height);
    RTListScale n = {half ? 2 : 1, (uint32_t)layer.width, (uint32_t)layer.height, half ? (__bridge void *)ad_rt.tex : NULL,
        half ? (__bridge void *)half_z : NULL, MAT_PS_BUF, sizeof(MatConsts), offsetof(MatConsts, k[1][3]), 1.0f};
    half_replay(draws, layer, &n, 1, half ? "soft smoke A/B nofade half" : "soft smoke A/B nofade full");
}
#endif
