// Offline: GPU cost at 3840x2160 xS (BGRA8 + Depth32Float_Stencil8, Private MSAA, like render.m) of
//  (a) one scene encoder; (b) the same draws split in two encoders (store + reload in the middle);
//  (c) b plus a lamp encoder between the halves (lamp colour clear, scene depth/stencil load+store, small quads,
//      StoreAndMultisampleResolve) = the remaining lamp-batch split.
// (d) MRT: the lamp target as colour attachment 1 of the one scene encoder (every scene pipeline declares it).
// Run: clang -fobjc-arc -O2 -framework Metal -framework Foundation
//   runtime/lampsplit_bench.m -o X && X <samples> [lamp variant 0-4: store+resolve, resolve only, store only, no depth, Load colour]
// The second half covers either the whole screen (full) or a small area (small) to show whether untouched tiles skip load/store.
#import <Metal/Metal.h>
#include <stdio.h>
#include <stdlib.h>
static const char *src = "#include <metal_stdlib>\nusing namespace metal;\n"
 "struct V { float4 p [[position]]; float4 c; };\n"
 "vertex V vs(uint vid [[vertex_id]], const device float4 *pos [[buffer(0)]]) { V o; o.p = float4(pos[vid].xy, pos[vid].z, 1); o.c = float4(fract(pos[vid].xyw*7.31), 1); return o; }\n"
 "struct O2 { float4 a [[color(0)]]; float4 b [[color(1)]]; };\n"
 "fragment O2 fs2(V i [[stage_in]]) { O2 o; o.a = float4(fract(i.p.x*0.0137+i.c.x), fract(i.p.y*0.0191+i.c.y), 0.5, 1); o.b = o.a; return o; }\n"
 "fragment float4 fs(V i [[stage_in]]) { return float4(fract(i.p.x*0.0137+i.c.x), fract(i.p.y*0.0191+i.c.y), fract((i.p.x+i.p.y)*0.003+i.c.z), 1); }\n";
static id<MTLDevice> dev; static int W = 3840, H = 2160, S = 2;
static id<MTLTexture> tex(MTLPixelFormat f, int s) {
  MTLTextureDescriptor *d = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:f width:W height:H mipmapped:NO];
  d.textureType = s > 1 ? MTLTextureType2DMultisample : MTLTextureType2D; d.sampleCount = s;
  d.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead; d.storageMode = MTLStorageModePrivate;
  return [dev newTextureWithDescriptor:d]; }
static int LV; // lamp variant: 0 store+resolve, 1 resolve only, 2 store only, 3 no depth, 4 load (not clear) colour
int main(int argc, char **argv) { @autoreleasepool {
  if (argc > 2) LV = atoi(argv[2]);
  if (argc > 1) S = atoi(argv[1]);
  dev = MTLCreateSystemDefaultDevice(); NSError *err = nil;
  id<MTLLibrary> lib = [dev newLibraryWithSource:@(src) options:nil error:&err];
  MTLRenderPipelineDescriptor *pd = [MTLRenderPipelineDescriptor new];
  pd.vertexFunction = [lib newFunctionWithName:@"vs"]; pd.fragmentFunction = [lib newFunctionWithName:@"fs"];
  pd.rasterSampleCount = S; pd.colorAttachments[0].pixelFormat = MTLPixelFormatBGRA8Unorm;
  pd.depthAttachmentPixelFormat = pd.stencilAttachmentPixelFormat = MTLPixelFormatDepth32Float_Stencil8;
  id<MTLRenderPipelineState> pso = [dev newRenderPipelineStateWithDescriptor:pd error:&err];
  pd.colorAttachments[0].blendingEnabled = YES; pd.colorAttachments[0].destinationRGBBlendFactor = MTLBlendFactorOne;
  id<MTLRenderPipelineState> psoadd = [dev newRenderPipelineStateWithDescriptor:pd error:&err];
  pd.depthAttachmentPixelFormat = pd.stencilAttachmentPixelFormat = MTLPixelFormatInvalid;
  id<MTLRenderPipelineState> psoadd_nz = [dev newRenderPipelineStateWithDescriptor:pd error:&err];
  pd.depthAttachmentPixelFormat = pd.stencilAttachmentPixelFormat = MTLPixelFormatDepth32Float_Stencil8;
  MTLDepthStencilDescriptor *dd = [MTLDepthStencilDescriptor new]; dd.depthCompareFunction = MTLCompareFunctionLessEqual; dd.depthWriteEnabled = YES;
  id<MTLDepthStencilState> dsw = [dev newDepthStencilStateWithDescriptor:dd]; dd.depthWriteEnabled = NO;
  id<MTLDepthStencilState> dst = [dev newDepthStencilStateWithDescriptor:dd];
  // MRT: every scene pipeline declares the lamp attachment 1; scene PSOs leave it alone (writeMask none), the flare replay writes only 1
  pd.depthAttachmentPixelFormat = pd.stencilAttachmentPixelFormat = MTLPixelFormatDepth32Float_Stencil8;
  pd.colorAttachments[1].pixelFormat = MTLPixelFormatBGRA8Unorm; pd.colorAttachments[1].writeMask = MTLColorWriteMaskNone;
  pd.colorAttachments[0].blendingEnabled = NO;
  id<MTLRenderPipelineState> pso_m = [dev newRenderPipelineStateWithDescriptor:pd error:&err];
  pd.colorAttachments[0].blendingEnabled = YES;
  id<MTLRenderPipelineState> psoadd_m = [dev newRenderPipelineStateWithDescriptor:pd error:&err];
  pd.fragmentFunction = [lib newFunctionWithName:@"fs2"]; pd.colorAttachments[0].writeMask = MTLColorWriteMaskNone;
  pd.colorAttachments[1].writeMask = MTLColorWriteMaskAll; pd.colorAttachments[1].blendingEnabled = YES; pd.colorAttachments[1].destinationRGBBlendFactor = MTLBlendFactorOne;
  id<MTLRenderPipelineState> psolamp_m = [dev newRenderPipelineStateWithDescriptor:pd error:&err];
  if (!pso_m || !psolamp_m) { fprintf(stderr, "%s\n", err.localizedDescription.UTF8String); return 2; }
  id<MTLTexture> c = tex(MTLPixelFormatBGRA8Unorm, S), z = tex(MTLPixelFormatDepth32Float_Stencil8, S);
  id<MTLTexture> lp = tex(MTLPixelFormatBGRA8Unorm, S), lpr = tex(MTLPixelFormatBGRA8Unorm, 1);
  // geometry: 2000 random triangles covering the screen (scene), 2000 more for the second half (full), or 40 small (small); 16 small flare quads
  int NT = 4000; id<MTLBuffer> vb = [dev newBufferWithLength:(NT + 200) * 3 * 16 options:MTLResourceStorageModeShared];
  float *v = vb.contents; srand(7);
  for (int t = 0; t < NT; ++t) { float cx = rand() / (float)RAND_MAX * 2 - 1, cy = rand() / (float)RAND_MAX * 2 - 1, zz = rand() / (float)RAND_MAX;
    for (int k = 0; k < 3; ++k) { v[(t*3+k)*4] = cx + (rand() / (float)RAND_MAX - .5f) * .6f; v[(t*3+k)*4+1] = cy + (rand() / (float)RAND_MAX - .5f) * .6f; v[(t*3+k)*4+2] = zz; v[(t*3+k)*4+3] = t; } }
  float *sm = v + NT * 12;   // 200 small triangles near one corner
  for (int t = 0; t < 200; ++t) for (int k = 0; k < 3; ++k) { sm[(t*3+k)*4] = -0.9f + (rand() / (float)RAND_MAX) * .15f; sm[(t*3+k)*4+1] = -0.9f + (rand() / (float)RAND_MAX) * .15f; sm[(t*3+k)*4+2] = 0.3f; sm[(t*3+k)*4+3] = t; }
  id<MTLCommandQueue> q = [dev newCommandQueue];
  MTLRenderPassDescriptor *(^pass)(id<MTLTexture>, id<MTLTexture>, int, int) = ^(id<MTLTexture> col, id<MTLTexture> res, int clear, int lamp) {
    MTLRenderPassDescriptor *rp = [MTLRenderPassDescriptor renderPassDescriptor];
    rp.colorAttachments[0].texture = col; rp.colorAttachments[0].resolveTexture = res;
    rp.colorAttachments[0].loadAction = clear ? MTLLoadActionClear : MTLLoadActionLoad;
    rp.colorAttachments[0].storeAction = res ? MTLStoreActionStoreAndMultisampleResolve : MTLStoreActionStore;
    rp.depthAttachment.texture = z; rp.depthAttachment.loadAction = clear && !lamp ? MTLLoadActionClear : MTLLoadActionLoad; rp.depthAttachment.storeAction = MTLStoreActionStore; rp.depthAttachment.clearDepth = 1;
    rp.stencilAttachment.texture = z; rp.stencilAttachment.loadAction = clear && !lamp ? MTLLoadActionClear : MTLLoadActionLoad; rp.stencilAttachment.storeAction = MTLStoreActionStore;
    if (lamp) { if (LV == 1) rp.colorAttachments[0].storeAction = MTLStoreActionMultisampleResolve;
      if (LV == 2) { rp.colorAttachments[0].storeAction = MTLStoreActionStore; rp.colorAttachments[0].resolveTexture = nil; }
      if (LV == 3) { rp.depthAttachment.texture = nil; rp.stencilAttachment.texture = nil; }
      if (LV == 4) rp.colorAttachments[0].loadAction = MTLLoadActionLoad; }
    return rp; };
  // mode: 0 one encoder, 1 split, 2 split + lamp encoder; half2: 0 full, 1 small
  double (^run)(int, int) = ^double(int mode, int half2) {
    id<MTLCommandBuffer> cb = [q commandBuffer];
    id<MTLRenderCommandEncoder> e = [cb renderCommandEncoderWithDescriptor:pass(c, nil, 1, 0)];
    [e setRenderPipelineState:pso]; [e setDepthStencilState:dsw]; [e setVertexBuffer:vb offset:0 atIndex:0];
    [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:2000 * 3];
    [e setRenderPipelineState:psoadd]; [e setDepthStencilState:dst];
    [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:NT * 3 vertexCount:16 * 3];   // flares in the scene
    if (mode >= 1) { [e endEncoding];
      if (mode == 2) { id<MTLRenderCommandEncoder> l = [cb renderCommandEncoderWithDescriptor:pass(lp, lpr, 1, 1)];
        [l setRenderPipelineState:LV == 3 ? psoadd_nz : psoadd]; if (LV != 3) [l setDepthStencilState:dst]; [l setVertexBuffer:vb offset:0 atIndex:0];
        [l drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:NT * 3 vertexCount:16 * 3]; [l endEncoding]; }
      e = [cb renderCommandEncoderWithDescriptor:pass(c, nil, 0, 0)]; [e setVertexBuffer:vb offset:0 atIndex:0]; }
    [e setRenderPipelineState:pso]; [e setDepthStencilState:dsw];
    if (half2) [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:NT * 3 + 16 * 3 vertexCount:40 * 3];
    else [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:2000 * 3 vertexCount:2000 * 3];
    [e endEncoding]; [cb commit]; [cb waitUntilCompleted];
    return (cb.GPUEndTime - cb.GPUStartTime) * 1e3; };
  double (^runm)(int) = ^double(int half2) {
    id<MTLCommandBuffer> cb = [q commandBuffer];
    MTLRenderPassDescriptor *rp = pass(c, nil, 1, 0);
    rp.colorAttachments[1].texture = lp; rp.colorAttachments[1].resolveTexture = lpr; rp.colorAttachments[1].loadAction = MTLLoadActionClear;
    rp.colorAttachments[1].storeAction = MTLStoreActionStoreAndMultisampleResolve;
    id<MTLRenderCommandEncoder> e = [cb renderCommandEncoderWithDescriptor:rp];
    [e setRenderPipelineState:pso_m]; [e setDepthStencilState:dsw]; [e setVertexBuffer:vb offset:0 atIndex:0];
    [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:2000 * 3];
    [e setRenderPipelineState:psoadd_m]; [e setDepthStencilState:dst];
    [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:NT * 3 vertexCount:16 * 3];
    [e setRenderPipelineState:psolamp_m];
    [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:NT * 3 vertexCount:16 * 3];
    [e setRenderPipelineState:pso_m]; [e setDepthStencilState:dsw];
    if (half2) [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:NT * 3 + 16 * 3 vertexCount:40 * 3];
    else [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:2000 * 3 vertexCount:2000 * 3];
    [e endEncoding]; [cb commit]; [cb waitUntilCompleted];
    return (cb.GPUEndTime - cb.GPUStartTime) * 1e3; };
  for (int h2 = 0; h2 < 2; ++h2) { enum { N = 61 }; double t[N]; for (int i = 0; i < N; ++i) t[i] = runm(h2);
    for (int i = 0; i < N; ++i) for (int j = i + 1; j < N; ++j) if (t[j] < t[i]) { double x = t[i]; t[i] = t[j]; t[j] = x; }
    printf("4K x%d, second half %s: MRT one encoder with lamp attachment %.3f ms\n", S, h2 ? "small" : "full", t[N / 2]); }
  for (int w = 0; w < 20; ++w) run(w % 3, w & 1);
  for (int h2 = 0; h2 < 2; ++h2) {
    double med[3]; enum { N = 61 };
    for (int m = 0; m < 3; ++m) { double t[N]; for (int i = 0; i < N; ++i) t[i] = run(m, h2);
      for (int i = 0; i < N; ++i) for (int j = i + 1; j < N; ++j) if (t[j] < t[i]) { double x = t[i]; t[i] = t[j]; t[j] = x; }
      med[m] = t[N / 2]; }
    printf("4K x%d, second half %s: one encoder %.3f ms, split %.3f (+%.3f), split+lamp %.3f (+%.3f, lamp enc %.3f)\n", S, h2 ? "small" : "full",
           med[0], med[1], med[1] - med[0], med[2], med[2] - med[0], med[2] - med[1]);
  }
}}
