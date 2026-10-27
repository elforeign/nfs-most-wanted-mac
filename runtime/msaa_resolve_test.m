// msaa_resolve_test.m - offline proof for render_target.m's resolve on store: two 8x passes
// (colour + D32S8) resolving at the end of each pass give the same resolved colour and depth, bit for bit, as plain
// stores followed by the old separate load + resolve passes. Build/run: clang -fobjc-arc -framework Metal
// -framework Foundation runtime/msaa_resolve_test.m -o /tmp/mrt && MTL_DEBUG_LAYER=1 /tmp/mrt
// PASS: 'colour identical, depth identical' exit 0. Broken (second pass stores without resolving): 'colour DIFFER', exit 1.
// Second check: a single-sample copy into an MSAA target whose pass resolves on store. Mirrors render.m's
// bookkeeping: render_stretch marks dst stale, blit_quad's end_pass clears the mark of a resolve-on-store attachment, the
// copy then writes the samples. Fixed order marks stale after the copy; `mrt old` marks before (the bug): the read returns
// the pre-copy pixels, 'stretch read FAIL', exit 1. PASS: 'stretch read ok'.
// Also shows Metal's rule: MTLStoreActionUnknown with a resolve texture may only become a resolve action.
#import <Metal/Metal.h>
#include <stdio.h>
#include <string.h>
static const char *src = "#include <metal_stdlib>\nusing namespace metal;\n"
"struct V { float4 p [[position]]; float4 c; };\n"
"vertex V vs(uint id [[vertex_id]], constant float4 *v [[buffer(0)]]) { V o; o.p = float4(v[id].xy, v[id].z, 1); o.c = float4(fract(v[id].xy*7.3+0.5), v[id].w, 1); return o; }\n"
"fragment float4 fs(V i [[stage_in]]) { return i.c; }\n";
int main(int argc, char **argv) {
  id<MTLDevice> d = MTLCreateSystemDefaultDevice(); id<MTLCommandQueue> q = [d newCommandQueue];
  NSError *e = nil; id<MTLLibrary> lib = [d newLibraryWithSource:@(src) options:nil error:&e];
  int W = 512, H = 288, S = 8;
  MTLRenderPipelineDescriptor *pd = [MTLRenderPipelineDescriptor new];
  pd.vertexFunction = [lib newFunctionWithName:@"vs"]; pd.fragmentFunction = [lib newFunctionWithName:@"fs"];
  pd.colorAttachments[0].pixelFormat = MTLPixelFormatBGRA8Unorm; pd.depthAttachmentPixelFormat = MTLPixelFormatDepth32Float_Stencil8;
  pd.stencilAttachmentPixelFormat = MTLPixelFormatDepth32Float_Stencil8; pd.rasterSampleCount = S;
  id<MTLRenderPipelineState> pso = [d newRenderPipelineStateWithDescriptor:pd error:&e];
  MTLDepthStencilDescriptor *dd = [MTLDepthStencilDescriptor new]; dd.depthCompareFunction = MTLCompareFunctionLess; dd.depthWriteEnabled = YES;
  id<MTLDepthStencilState> dss = [d newDepthStencilStateWithDescriptor:dd];
  MTLTextureDescriptor *t = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:W height:H mipmapped:NO];
  t.textureType = MTLTextureType2DMultisample; t.sampleCount = S; t.usage = MTLTextureUsageRenderTarget; t.storageMode = MTLStorageModePrivate;
  id<MTLTexture> ms[2], zs[2], res[2], zres[2];
  for (int k = 0; k < 2; ++k) { t.pixelFormat = MTLPixelFormatBGRA8Unorm; ms[k] = [d newTextureWithDescriptor:t]; t.pixelFormat = MTLPixelFormatDepth32Float_Stencil8; zs[k] = [d newTextureWithDescriptor:t]; }
  MTLTextureDescriptor *r = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:W height:H mipmapped:NO];
  r.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead; r.storageMode = MTLStorageModePrivate;
  for (int k = 0; k < 2; ++k) { r.pixelFormat = MTLPixelFormatBGRA8Unorm; res[k] = [d newTextureWithDescriptor:r]; r.pixelFormat = MTLPixelFormatDepth32Float_Stencil8; zres[k] = [d newTextureWithDescriptor:r]; }
  float tri[3 * 4 * 40]; unsigned s = 1; for (int i = 0; i < 480; ++i) { s = s * 1103515245 + 12345; tri[i] = ((s >> 8) & 0xFFFF) / 32768.0f - 1.0f; if (i % 4 == 2) tri[i] = (tri[i] + 1) * 0.5f; }
  id<MTLCommandBuffer> cb = [q commandBuffer];
  for (int k = 0; k < 2; ++k) {
    for (int pass = 0; pass < 2; ++pass) {          // two passes: the first plain store, the second resolves
      MTLRenderPassDescriptor *rp = [MTLRenderPassDescriptor renderPassDescriptor];
      rp.colorAttachments[0].texture = ms[k]; rp.colorAttachments[0].loadAction = pass ? MTLLoadActionLoad : MTLLoadActionClear;
      rp.depthAttachment.texture = zs[k]; rp.depthAttachment.loadAction = pass ? MTLLoadActionLoad : MTLLoadActionClear; rp.depthAttachment.clearDepth = 1;
      rp.stencilAttachment.texture = zs[k]; rp.stencilAttachment.loadAction = pass ? MTLLoadActionLoad : MTLLoadActionClear; rp.stencilAttachment.storeAction = MTLStoreActionStore;
      if (k == 1) { rp.colorAttachments[0].storeAction = MTLStoreActionStoreAndMultisampleResolve; rp.colorAttachments[0].resolveTexture = res[k];
                    rp.depthAttachment.storeAction = MTLStoreActionStoreAndMultisampleResolve; rp.depthAttachment.resolveTexture = zres[k]; }
      else { rp.colorAttachments[0].storeAction = MTLStoreActionStore; rp.depthAttachment.storeAction = MTLStoreActionStore; }
      id<MTLRenderCommandEncoder> en = [cb renderCommandEncoderWithDescriptor:rp];
      [en setRenderPipelineState:pso]; [en setDepthStencilState:dss];
      [en setVertexBytes:tri + pass * 240 length:240 * 4 atIndex:0]; [en drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:60];

      [en endEncoding];
    }
    if (k == 0) {                                     // the old separate resolve passes
      for (int z = 0; z < 2; ++z) {
        MTLRenderPassDescriptor *rp = [MTLRenderPassDescriptor renderPassDescriptor];
        MTLRenderPassAttachmentDescriptor *a = z ? (MTLRenderPassAttachmentDescriptor *)rp.depthAttachment : (MTLRenderPassAttachmentDescriptor *)rp.colorAttachments[0];
        a.texture = z ? zs[0] : ms[0]; a.loadAction = MTLLoadActionLoad; a.storeAction = MTLStoreActionStoreAndMultisampleResolve; a.resolveTexture = z ? zres[0] : res[0];
        [[cb renderCommandEncoderWithDescriptor:rp] endEncoding];
      }
    }
  }
  id<MTLBuffer> out[4]; for (int i = 0; i < 4; ++i) out[i] = [d newBufferWithLength:W * H * 4 options:MTLResourceStorageModeShared];
  id<MTLBlitCommandEncoder> b = [cb blitCommandEncoder];
  for (int k = 0; k < 2; ++k) {
    [b copyFromTexture:res[k] sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(W,H,1) toBuffer:out[k] destinationOffset:0 destinationBytesPerRow:W*4 destinationBytesPerImage:W*H*4];
    [b copyFromTexture:zres[k] sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(W,H,1) toBuffer:out[2+k] destinationOffset:0 destinationBytesPerRow:W*4 destinationBytesPerImage:W*H*4 options:MTLBlitOptionDepthFromDepthStencil];
  }
  [b endEncoding]; [cb commit]; [cb waitUntilCompleted];
  if (cb.error) { printf("cb error %s\n", cb.error.localizedDescription.UTF8String); return 2; }
  int dc = memcmp(out[0].contents, out[1].contents, W * H * 4), dz = memcmp(out[2].contents, out[3].contents, W * H * 4);
  const uint8_t *p = out[0].contents; long nonbg = 0; for (int i = 0; i < W * H; ++i) nonbg += p[4*i] | p[4*i+1] | p[4*i+2] ? 1 : 0;
  const float *zz = out[2].contents; long zn = 0; for (int i = 0; i < W * H; ++i) zn += zz[i] < 1;
  printf("colour %s, depth %s (covered px %ld, depth<1 px %ld of %d)\n", dc ? "DIFFER" : "identical", dz ? "DIFFER" : "identical", nonbg, zn, W * H);
  // ---- stretch-into-attached-MSAA bookkeeping (model of render_stretch / end_pass / msaa_resolve) ----
  int old = argc > 1 && !strcmp(argv[1], "old"), stale = 0, resolve_on_store = 1, fail;
  id<MTLTexture> sms = ms[1], sres = res[1];
  id<MTLCommandBuffer> c2 = [q commandBuffer];
  { MTLRenderPassDescriptor *rp = [MTLRenderPassDescriptor renderPassDescriptor];          // the open game pass: clear red
    rp.colorAttachments[0].texture = sms; rp.colorAttachments[0].loadAction = MTLLoadActionClear; rp.colorAttachments[0].clearColor = MTLClearColorMake(1, 0, 0, 1);
    rp.colorAttachments[0].storeAction = MTLStoreActionStoreAndMultisampleResolve; rp.colorAttachments[0].resolveTexture = sres;
    [[c2 renderCommandEncoderWithDescriptor:rp] endEncoding]; stale = 1; }                    // ms_attach: stale = 1
  if (old) stale = 1;                                                                        // render_stretch (old): mark first
  if (resolve_on_store) stale = 0;                                                           // blit_quad -> end_pass
  { MTLRenderPassDescriptor *rp = [MTLRenderPassDescriptor renderPassDescriptor];          // the copy: writes green samples
    rp.colorAttachments[0].texture = sms; rp.colorAttachments[0].loadAction = MTLLoadActionClear; rp.colorAttachments[0].clearColor = MTLClearColorMake(0, 1, 0, 1);
    rp.colorAttachments[0].storeAction = MTLStoreActionStore;
    [[c2 renderCommandEncoderWithDescriptor:rp] endEncoding]; }
  if (!old) stale = 1;                                                                       // render_stretch (fixed): mark after
  if (stale) { MTLRenderPassDescriptor *rp = [MTLRenderPassDescriptor renderPassDescriptor]; // msaa_resolve on the read
    rp.colorAttachments[0].texture = sms; rp.colorAttachments[0].loadAction = MTLLoadActionLoad;
    rp.colorAttachments[0].storeAction = MTLStoreActionStoreAndMultisampleResolve; rp.colorAttachments[0].resolveTexture = sres;
    [[c2 renderCommandEncoderWithDescriptor:rp] endEncoding]; }
  id<MTLBuffer> ob = [d newBufferWithLength:W * H * 4 options:MTLResourceStorageModeShared];
  b = [c2 blitCommandEncoder];
  [b copyFromTexture:sres sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(W,H,1) toBuffer:ob destinationOffset:0 destinationBytesPerRow:W*4 destinationBytesPerImage:W*H*4];
  [b endEncoding]; [c2 commit]; [c2 waitUntilCompleted];
  const uint8_t *px = ob.contents; fail = !(px[0] == 0 && px[1] == 255 && px[2] == 0);        // BGRA: green
  printf("stretch read %s (%s order, first px B%d G%d R%d)\n", fail ? "FAIL" : "ok", old ? "old" : "fixed", px[0], px[1], px[2]);
  return dc || dz || fail;
}
