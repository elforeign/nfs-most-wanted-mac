// Native half of carshadow_sky_test.py: renders the real car paint PS (materials.m) for one rear-panel pixel with a
// Most Wanted free-roam light rig, shadowed and lit, Tunnel shadows Keep (k[7].z 0) and Keep + sky light (k[7].z 1).
// Prints the four Rec.709 lumas and exits 0 only when the shadowed panel lifts >= 1.5x and the lit pixel is unchanged.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

// Rig: the player body draw's VS c11-c16 (LocalColourMatrix columns, LocalDirectionMatrix rows); rear panel N = -x.
static const float C[3][3] = {{.7055f, .747f, .83f}, {.22f, .24f, .40f}, {.2925f, .315f, .45f}};
static const float L[3][3] = {{-.160337f, -.322671f, .932839f}, {-.379277f, .860923f, -.339069f}, {.911971f, .242254f, -.331103f}};
static const float N[3] = {-1, 0, 0};
// u[0] the vertex light v0, u[1] the sun direction, u[2] the normal
static const char kVS[] = "\nvertex In sky_vs(uint id [[vertex_id]], constant float4 *u [[buffer(0)]]) {\n"
    "float2 p = float2((id << 1) & 2, id & 2); In o;\n"
    "o.pos = float4(p * 2 - 1, 0.5, 1); o.color0 = u[0]; o.color1 = float4(0);\n"
    "o.texcoord0 = float4(0, 0, 1, 1); o.texcoord1 = float4(p, 0, 0); o.texcoord2 = float4(.5, .5, .5, 1);\n"
    "o.texcoord3 = u[1]; o.texcoord4 = u[2]; o.texcoord5 = float4(-1, 0, 0, 0); o.texcoord6 = float4(0, 0, 0, .5); return o; }\n";
static id<MTLTexture> texel(id<MTLDevice> device, MTLTextureType type, float r, float g, float b, float a) {
    MTLTextureDescriptor *d = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA32Float width:1 height:1 mipmapped:NO];
    d.textureType = type; d.storageMode = MTLStorageModeShared; d.usage = MTLTextureUsageShaderRead;
    id<MTLTexture> t = [device newTextureWithDescriptor:d];
    const float v[4] = {r, g, b, a};
    int faces = type == MTLTextureTypeCube ? 6 : 1;
    for (int f = 0; f < faces; ++f)
        [t replaceRegion:type == MTLTextureType3D ? MTLRegionMake3D(0, 0, 0, 1, 1, 1) : MTLRegionMake2D(0, 0, 1, 1) mipmapLevel:0 slice:f withBytes:v bytesPerRow:16 bytesPerImage:16];
    return t;
}

int main(int argc, const char **argv) { @autoreleasepool {
    if (argc < 2) return 2;
    NSString *ps = [NSString stringWithContentsOfFile:@(argv[1]) encoding:NSUTF8StringEncoding error:nil];
    if (!ps) return 2;
    id<MTLDevice> device = MTLCreateSystemDefaultDevice();
    if (!device) { fprintf(stderr, "carshadow_sky_test: SKIP no Metal device\n"); return 3; }
    float v0[3] = {0};   // the PC VS: AO 1 x DiffuseMin 1.2 x sum sat(N.L + 0.1) x colour
    for (int l = 0; l < 3; ++l) {
        float w = fminf(1, fmaxf(0, N[0] * L[l][0] + N[1] * L[l][1] + N[2] * L[l][2] + .1f));
        for (int i = 0; i < 3; ++i) v0[i] += 1.2f * w * C[l][i];
    }
    NSString *vs = @(kVS);
    NSString *source = [NSString stringWithFormat:@"#define MAT_VARIANT 4\n#define MAT_SHADOW_HW 0\n#define MAT_CAR_HDR 0\n"
        "#define MAT_PAINT_NAN_TINT 0\n#define MAT_PAINT_FINITE_AB 0\n#define MAT_PAINT_POW_MUTANT 0\n%@%@", ps, vs];
    MTLCompileOptions *opt = [MTLCompileOptions new]; opt.mathMode = MTLMathModeRelaxed;
    NSError *err = nil;
    id<MTLLibrary> lib = [device newLibraryWithSource:source options:opt error:&err];
    if (!lib) { fprintf(stderr, "carshadow_sky_test: MSL %s\n", err.localizedDescription.UTF8String); return 1; }
    MTLRenderPipelineDescriptor *pd = [MTLRenderPipelineDescriptor new];
    pd.vertexFunction = [lib newFunctionWithName:@"sky_vs"]; pd.fragmentFunction = [lib newFunctionWithName:@"sm_main"];
    pd.colorAttachments[0].pixelFormat = MTLPixelFormatRGBA32Float;
    id<MTLRenderPipelineState> pso = [device newRenderPipelineStateWithDescriptor:pd error:&err];
    if (!pso) { fprintf(stderr, "carshadow_sky_test: PSO %s\n", err.localizedDescription.UTF8String); return 1; }
    // s0 diffuse grey, s1 black cube (no reflection), s2 shadow depth (0 caster / 1 clear), s3 mask 0 (map valid)
    id<MTLTexture> diffuse = texel(device, MTLTextureType2D, .5f, .5f, .5f, 1), cube = texel(device, MTLTextureTypeCube, 0, 0, 0, 0);
    id<MTLTexture> caster = texel(device, MTLTextureType2D, 0, 0, 0, 0), clear = texel(device, MTLTextureType2D, 1, 1, 1, 1);
    id<MTLTexture> mask = texel(device, MTLTextureType2D, 0, 0, 0, 0), flat = texel(device, MTLTextureType2D, .5f, .5f, 1, 1);
    id<MTLTexture> vol = texel(device, MTLTextureType3D, 0, 0, 0, 0);
    MTLSamplerDescriptor *sd = [MTLSamplerDescriptor new]; sd.minFilter = sd.magFilter = MTLSamplerMinMagFilterNearest;
    id<MTLSamplerState> smp = [device newSamplerStateWithDescriptor:sd];
    MTLTextureDescriptor *td = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA32Float width:4 height:4 mipmapped:NO];
    td.storageMode = MTLStorageModeShared; td.usage = MTLTextureUsageRenderTarget;
    id<MTLTexture> target = [device newTextureWithDescriptor:td];
    id<MTLCommandQueue> queue = [device newCommandQueue];
    float out[2][2][4];   // [sky][lit]
    for (int sky = 0; sky < 2; ++sky) for (int lit = 0; lit < 2; ++lit) {
        float c[2][4] = {{.1f}, {1.0f / 1600}}, m[12][4] = {{0}};
        unsigned char aux[16 + 16 * 16] = {0};
        m[0][0] = 1;
        for (int i = 0; i < 3; ++i) { m[1][i] = C[0][i]; m[2][1 + i] = L[1][i]; m[3][i] = C[1][i]; m[4][i] = L[2][i]; m[5][i] = C[2][i]; }
        m[7][2] = (float)sky; m[11][3] = -1;
        id<MTLCommandBuffer> cb = [queue commandBuffer];
        MTLRenderPassDescriptor *rp = [MTLRenderPassDescriptor renderPassDescriptor];
        rp.colorAttachments[0].texture = target; rp.colorAttachments[0].loadAction = MTLLoadActionClear; rp.colorAttachments[0].storeAction = MTLStoreActionStore;
        id<MTLRenderCommandEncoder> e = [cb renderCommandEncoderWithDescriptor:rp];
        [e setRenderPipelineState:pso];
        [e setFragmentBytes:c length:sizeof c atIndex:0]; [e setFragmentBytes:aux length:sizeof aux atIndex:3]; [e setFragmentBytes:m length:sizeof m atIndex:4];
        id<MTLTexture> bound[4] = {diffuse, cube, lit ? clear : caster, mask};
        for (int i = 0; i < 4; ++i) { [e setFragmentTexture:bound[i] atIndex:i]; [e setFragmentSamplerState:smp atIndex:i]; }
        [e setFragmentTexture:vol atIndex:14]; [e setFragmentTexture:flat atIndex:15];
        float u[3][4] = {{v0[0], v0[1], v0[2], 1}, {L[0][0], L[0][1], L[0][2], 1}, {N[0], N[1], N[2], 0}};
        [e setVertexBytes:u length:sizeof u atIndex:0];
        [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3]; [e endEncoding];
        [cb commit]; [cb waitUntilCompleted];
        if (cb.status != MTLCommandBufferStatusCompleted) return 1;
        [target getBytes:out[sky][lit] bytesPerRow:64 fromRegion:MTLRegionMake2D(0, 0, 1, 1) mipmapLevel:0];
    }
    float y[2][2];
    for (int s = 0; s < 2; ++s) for (int l = 0; l < 2; ++l) y[s][l] = .2126f * out[s][l][0] + .7152f * out[s][l][1] + .0722f * out[s][l][2];
    float ratio = y[1][0] / fmaxf(y[0][0], 1e-6f);
    int same = !memcmp(out[0][1], out[1][1], sizeof out[0][1]);
    fprintf(stderr, "carshadow_sky_test: shadow keep %.4f sky %.4f (x%.2f) | lit keep %.4f sky %.4f (%s)\n",
            y[0][0], y[1][0], ratio, y[0][1], y[1][1], same ? "identical" : "CHANGED");
    return ratio >= 1.5f && same && y[1][0] <= y[1][1] ? 0 : 1;
} }
