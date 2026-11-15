// glass_port_test.m - draws a synthetic facade through a glossywindow VS/PS pair (glass_port_test.py) and prints the
// reflection's red channel down the centre column (8-bit levels, -1 off the facade).
// usage: glass_port_test <vs.metal> <ps.metal> <vertices.bin> <v.bin> <uv0half 0|1>
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <stdio.h>

static NSData *file(const char *p) { return [NSData dataWithContentsOfFile:[NSString stringWithUTF8String:p]]; }
static id<MTLFunction> fn(id<MTLDevice> d, const char *path) {
    NSError *e = nil;
    NSString *src = [[NSString alloc] initWithData:file(path) encoding:NSUTF8StringEncoding];
    id<MTLLibrary> l = src ? [d newLibraryWithSource:src options:nil error:&e] : nil;
    if (!l) { fprintf(stderr, "compile %s: %s\n", path, e.localizedDescription.UTF8String); return nil; }
    return [l newFunctionWithName:@"sm_main"];
}
int main(int argc, char **argv) {
    @autoreleasepool {
        if (argc != 6) return 2;
        id<MTLDevice> d = MTLCreateSystemDefaultDevice();
        if (!d) { fprintf(stderr, "no Metal device\n"); return 3; }
        id<MTLFunction> vs = fn(d, argv[1]), ps = fn(d, argv[2]);
        NSData *verts = file(argv[3]), *vrow = file(argv[4]);
        if (!vs || !ps || !verts || !vrow) return 1;
        unsigned nv = (unsigned)(verts.length / 36);
        if (vrow.length != nv * 4) return 1;
        MTLVertexDescriptor *vd = [MTLVertexDescriptor vertexDescriptor];
        const MTLVertexFormat fmt[4] = {MTLVertexFormatFloat3, MTLVertexFormatFloat3, MTLVertexFormatUChar4Normalized_BGRA, MTLVertexFormatFloat2};
        const unsigned off[4] = {0, 12, 24, 28};
        for (int i = 0; i < 4; ++i) { vd.attributes[i].format = fmt[i]; vd.attributes[i].offset = off[i]; vd.attributes[i].bufferIndex = 0; }
        vd.layouts[0].stride = 36;
        vd.attributes[29].format = MTLVertexFormatFloat; vd.attributes[29].offset = 0; vd.attributes[29].bufferIndex = 29;
        vd.layouts[29].stride = 4;
        MTLRenderPipelineDescriptor *pd = [MTLRenderPipelineDescriptor new];
        pd.vertexFunction = vs; pd.fragmentFunction = ps; pd.vertexDescriptor = vd;
        pd.colorAttachments[0].pixelFormat = MTLPixelFormatRGBA8Unorm;
        NSError *e = nil;
        id<MTLRenderPipelineState> pso = [d newRenderPipelineStateWithDescriptor:pd error:&e];
        if (!pso) { fprintf(stderr, "pipeline: %s\n", e.localizedDescription.UTF8String); return 1; }
        enum { W = 64, H = 64 };
        MTLTextureDescriptor *td = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm width:W height:H mipmapped:NO];
        td.usage = MTLTextureUsageRenderTarget; td.storageMode = MTLStorageModeShared;
        id<MTLTexture> rt = [d newTextureWithDescriptor:td];
        // Inputs: diffuse black with alpha 1 (the PC scales the reflection by it), white shadow, and a reflection with
        // vertical colour bands (red = 4 x row, 64 rows, constant across U).
        id<MTLTexture> tex[3];
        for (int t = 0; t < 3; ++t) {
            unsigned h = t == 2 ? 64 : 1;
            MTLTextureDescriptor *x = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm width:4 height:h mipmapped:NO];
            tex[t] = [d newTextureWithDescriptor:x];
            uint8_t px[4 * 4 * 64];
            for (unsigned y = 0; y < h; ++y) for (unsigned u = 0; u < 4; ++u) {
                uint8_t *p = px + 4 * (y * 4 + u);
                if (t == 0) { p[0] = p[1] = p[2] = 0; p[3] = 255; }
                else if (t == 1) p[0] = p[1] = p[2] = p[3] = 255;
                else { p[0] = (uint8_t)(4 * y); p[1] = 0; p[2] = (uint8_t)(255 - 4 * y); p[3] = 255; }
            }
            [tex[t] replaceRegion:MTLRegionMake2D(0, 0, 4, h) mipmapLevel:0 withBytes:px bytesPerRow:16];
        }
        MTLSamplerDescriptor *sd = [MTLSamplerDescriptor new];
        sd.minFilter = sd.magFilter = MTLSamplerMinMagFilterNearest;
        sd.sAddressMode = sd.tAddressMode = MTLSamplerAddressModeRepeat;
        id<MTLSamplerState> smp = [d newSamplerStateWithDescriptor:sd];
        float c[256][4] = {{0}};
        // WorldViewProj (c4..c7): object x 0..10 -> clip x, z 0..10 -> clip y; eye in front of the facade (c17).
        c[4][0] = .2f; c[4][3] = -1; c[5][2] = .2f; c[5][3] = -1; c[6][3] = .5f; c[7][3] = 1;
        c[17][0] = 5; c[17][1] = -20; c[17][2] = 5; c[3][3] = 1;   // ShadowTransform w = 1
        float k[32][4] = {{0}}; k[27][3] = (float)atoi(argv[5]);
        float aux[8] = {0};
        float pc[32][4] = {{0}};
        uint32_t psaux[64] = {0};
        MTLRenderPassDescriptor *rp = [MTLRenderPassDescriptor renderPassDescriptor];
        rp.colorAttachments[0].texture = rt; rp.colorAttachments[0].loadAction = MTLLoadActionClear;
        rp.colorAttachments[0].clearColor = MTLClearColorMake(0, 1, 0, 1); rp.colorAttachments[0].storeAction = MTLStoreActionStore;
        id<MTLCommandQueue> q = [d newCommandQueue];
        id<MTLCommandBuffer> cb = [q commandBuffer];
        id<MTLRenderCommandEncoder> en = [cb renderCommandEncoderWithDescriptor:rp];
        [en setRenderPipelineState:pso];
        [en setVertexBytes:verts.bytes length:verts.length atIndex:0];
        [en setVertexBytes:vrow.bytes length:vrow.length atIndex:29];
        [en setVertexBytes:c length:sizeof c atIndex:16];
        int ci[16][4] = {{0}}; int cbool[16] = {0};
        [en setVertexBytes:ci length:sizeof ci atIndex:17]; [en setVertexBytes:cbool length:sizeof cbool atIndex:18];
        [en setVertexBytes:aux length:sizeof aux atIndex:19];
        [en setVertexBytes:k length:sizeof k atIndex:20];
        [en setFragmentBytes:pc length:sizeof pc atIndex:0];
        [en setFragmentBytes:ci length:sizeof ci atIndex:1]; [en setFragmentBytes:cbool length:sizeof cbool atIndex:2];
        [en setFragmentBytes:psaux length:sizeof psaux atIndex:3];
        for (int t = 0; t < 3; ++t) { [en setFragmentTexture:tex[t] atIndex:t]; [en setFragmentSamplerState:smp atIndex:t]; }
        [en drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:nv];
        [en endEncoding];
        [cb commit]; [cb waitUntilCompleted];
        uint8_t out[W * H * 4];
        [rt getBytes:out bytesPerRow:W * 4 fromRegion:MTLRegionMake2D(0, 0, W, H) mipmapLevel:0];
        int covered = 0;
        printf("col=");
        for (int y = 0; y < H; ++y) {                        // top to bottom; -1 where the clear colour shows
            const uint8_t *p = out + 4 * (y * W + W / 2);
            int bg = p[1] == 255 && p[0] == 0;
            covered += !bg;
            printf("%s%d", y ? "," : "", bg ? -1 : p[0]);
        }
        printf("\ncovered=%d\n", covered);
        return covered ? 0 : 1;
    }
}
