// Native replay of the real texture Init and same-size substitution, followed by
// a 3440x2160 Metal UI draw. uiblur_test.py supplies the translated function and
// production swap body; no emulator or game process is involved.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>
#include "runtime.h"

uint8_t *G;
static uint32_t heap = 0x1000000;
static int diag_on, nhd_tone, x360_hd_creating;
static uint32_t hd_loaded, hd_bytes, hd_tone_loaded, hd_later_applied_mask, hd_later_loaded;
enum { HD_KEEP = 2 };
typedef struct { uint32_t hash; const char *path; int state; uint32_t ww, hh, mips, fmt; } HdTex;
static HdTex hd_tone[1];
typedef struct { uint32_t guest, w, h, levels, fmt, pitch[16], mem[16]; } Uploaded;
static Uploaded upload;

static uint32_t gmalloc(uint32_t n) { uint32_t p = heap; heap += (n + 15) & ~15u; assert(heap < (64u << 20)); return p; }
static void gfree(uint32_t p) { (void)p; }
static uint32_t hd_read(const char *path, uint32_t *size) {
    FILE *f = fopen(path, "rb"); assert(f);
    fseek(f, 0, SEEK_END); *size = (uint32_t)ftell(f); rewind(f);
    uint32_t p = gmalloc(*size); assert(fread(G + p, 1, *size, f) == *size); fclose(f); return p;
}
static int later_header(const uint8_t *p, size_t n) { (void)p; (void)n; return 1; }
static int later_pin(const HdTex *h, const uint8_t *p, size_t n) { (void)h; (void)p; (void)n; return 1; }
static int later_rule(uint32_t h) { (void)h; return 0; }
static void diag_event(const char *s, ...) { (void)s; }
static void ui_diag_upload(uint32_t a, uint32_t b, uint32_t d, uint32_t n) { (void)a; (void)b; (void)d; (void)n; }

static uint32_t arg(Cpu *c, uint32_t i) { return M32(c->esp + 4 + i * 4); }
void guest_divide_error(Cpu *c, uint32_t at) { (void)c; fprintf(stderr, "divide at %08X\n", at); abort(); }
void abi_violation(Cpu *c, uint32_t fn, uint32_t b, uint32_t s, uint32_t d, uint32_t p, uint32_t sp) {
    (void)c; (void)b; (void)s; (void)d; (void)p; (void)sp; fprintf(stderr, "ABI %08X\n", fn); abort();
}
void f_006D99C0(Cpu *c) { (void)c; abort(); }
void f_00460D60(Cpu *c) { c->eax = strncmp((char *)G + arg(c, 0), (char *)G + arg(c, 1), arg(c, 2)); c->esp += 4; }
void f_00652AD0(Cpu *c) { c->eax = gmalloc(arg(c, 0)); c->esp += 4; }
void f_00652B10(Cpu *c) { c->esp += 4; }
void dispatch(Cpu *c, uint32_t target) {
    if (target == 1) { // IDirect3DDevice9::CreateTexture
        upload = (Uploaded){.guest = 0x32000, .w = arg(c, 1), .h = arg(c, 2), .levels = arg(c, 3), .fmt = arg(c, 5)};
        assert(upload.levels && upload.levels <= 16);
        M32(upload.guest) = 0x33000;
        M32(arg(c, 7)) = upload.guest;
        for (uint32_t l = 0; l < upload.levels; ++l) {
            uint32_t w = upload.w >> l, h = upload.h >> l;
            w = w ? w : 1; h = h ? h : 1;
            upload.pitch[l] = upload.fmt == 21 ? w * 4 : ((w + 3) / 4) * (upload.fmt == 0x31545844 ? 8 : 16);
            upload.mem[l] = gmalloc(upload.pitch[l] * (upload.fmt == 21 ? h : (h + 3) / 4));
        }
        c->eax = 0; c->esp += 40;
    } else if (target == 2) { // IDirect3DTexture9::LockRect
        uint32_t l = arg(c, 1), r = arg(c, 2); assert(l < upload.levels);
        M32(r) = upload.pitch[l]; M32(r + 4) = upload.mem[l]; c->eax = 0; c->esp += 24;
    } else if (target == 3) { c->eax = 0; c->esp += 12; }
    else { fprintf(stderr, "unexpected dispatch %08X\n", target); abort(); }
}

#include "uiblur_generated.h"

typedef struct { float x, y, u, v; } Vertex;
static int draw_crop(const char *vertices, const char *out) {
    FILE *f = fopen(vertices, "rb"); assert(f);
    uint32_t n, crop[4]; assert(fread(&n, 4, 1, f) == 1 && n && n <= 4096);
    assert(fread(crop, 4, 4, f) == 4);
    Vertex *v = calloc(n, sizeof *v); assert(fread(v, sizeof *v, n, f) == n); fclose(f);
    assert(crop[0] + crop[2] <= 3440 && crop[1] + crop[3] <= 2160);
    id<MTLDevice> dev = MTLCreateSystemDefaultDevice();
    if (!dev) { free(v); puts("SKIP: PENDING Metal device for full-resolution UI replay"); return 3; }
    NSError *error = nil;
    NSString *source = @"#include <metal_stdlib>\nusing namespace metal;\n"
        "struct V{float2 p;float2 uv;};struct O{float4 p[[position]];float2 uv;};"
        "vertex O vs(uint i[[vertex_id]],constant V*v[[buffer(0)]]){return O{float4(v[i].p,0,1),v[i].uv};}"
        "fragment float4 ps(O i[[stage_in]],texture2d<float>t[[texture(0)]],sampler s[[sampler(0)]]){return t.sample(s,i.uv);}";
    id<MTLLibrary> lib = [dev newLibraryWithSource:source options:nil error:&error];
    if (!lib) { fprintf(stderr, "%s\n", error.description.UTF8String); return 1; }
    MTLRenderPipelineDescriptor *pd = [MTLRenderPipelineDescriptor new];
    pd.vertexFunction = [lib newFunctionWithName:@"vs"]; pd.fragmentFunction = [lib newFunctionWithName:@"ps"];
    pd.colorAttachments[0].pixelFormat = MTLPixelFormatRGBA8Unorm;
    pd.colorAttachments[0].blendingEnabled = YES;
    pd.colorAttachments[0].sourceRGBBlendFactor = MTLBlendFactorSourceAlpha;
    pd.colorAttachments[0].destinationRGBBlendFactor = MTLBlendFactorOneMinusSourceAlpha;
    pd.colorAttachments[0].sourceAlphaBlendFactor = MTLBlendFactorOne;
    pd.colorAttachments[0].destinationAlphaBlendFactor = MTLBlendFactorZero;
    id<MTLRenderPipelineState> ps = [dev newRenderPipelineStateWithDescriptor:pd error:&error]; assert(ps);
    MTLPixelFormat fmt = upload.fmt == 21 ? MTLPixelFormatBGRA8Unorm : upload.fmt == 0x33545844 ? MTLPixelFormatBC2_RGBA : MTLPixelFormatBC3_RGBA;
    MTLTextureDescriptor *td = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:fmt width:upload.w height:upload.h mipmapped:NO];
    td.mipmapLevelCount = upload.levels; td.storageMode = MTLStorageModeShared;
    id<MTLTexture> tex = [dev newTextureWithDescriptor:td]; assert(tex);
    for (uint32_t l = 0; l < upload.levels; ++l) {
        uint32_t w = upload.w >> l, h = upload.h >> l; w = w ? w : 1; h = h ? h : 1;
        [tex replaceRegion:MTLRegionMake2D(0, 0, w, h) mipmapLevel:l withBytes:G + upload.mem[l] bytesPerRow:upload.pitch[l]];
    }
    MTLSamplerDescriptor *sd = [MTLSamplerDescriptor new];
    sd.minFilter = sd.magFilter = MTLSamplerMinMagFilterLinear; sd.mipFilter = MTLSamplerMipFilterLinear;
    sd.sAddressMode = sd.tAddressMode = MTLSamplerAddressModeRepeat; sd.lodMinClamp = 0;
    id<MTLSamplerState> sampler = [dev newSamplerStateWithDescriptor:sd];
    td = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm width:3440 height:2160 mipmapped:NO];
    td.storageMode = MTLStorageModeShared; td.usage = MTLTextureUsageRenderTarget;
    id<MTLTexture> target = [dev newTextureWithDescriptor:td]; assert(target);
    MTLRenderPassDescriptor *pass = [MTLRenderPassDescriptor new];
    pass.colorAttachments[0].texture = target; pass.colorAttachments[0].loadAction = MTLLoadActionClear;
    pass.colorAttachments[0].storeAction = MTLStoreActionStore;
    id<MTLCommandQueue> q = [dev newCommandQueue]; id<MTLCommandBuffer> cb = [q commandBuffer];
    id<MTLRenderCommandEncoder> enc = [cb renderCommandEncoderWithDescriptor:pass];
    [enc setRenderPipelineState:ps]; [enc setVertexBytes:v length:n * sizeof *v atIndex:0];
    [enc setFragmentTexture:tex atIndex:0]; [enc setFragmentSamplerState:sampler atIndex:0];
    [enc drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:n]; [enc endEncoding];
    [cb commit]; [cb waitUntilCompleted]; assert(cb.status == MTLCommandBufferStatusCompleted);
    size_t size = (size_t)crop[2] * crop[3] * 4; void *pixels = malloc(size);
    [target getBytes:pixels bytesPerRow:crop[2] * 4 fromRegion:MTLRegionMake2D(crop[0], crop[1], crop[2], crop[3]) mipmapLevel:0];
    f = fopen(out, "wb"); assert(f); assert(fwrite(pixels, 1, size, f) == size); fclose(f);
    free(pixels); free(v); return 0;
}

int main(int argc, char **argv) { @autoreleasepool {
    assert(argc == 7); // PC fixture, DDS, vertices, output crop, pc|fixed|broken, quality drop
    G = calloc(1, 64u << 20); assert(G);
    uint32_t fields[6]; FILE *f = fopen(argv[1], "rb"); assert(f && fread(fields, 4, 6, f) == 6);
    uint32_t ti = 0x20000, pi = ti + 0x100;
    M32(ti) = pi; memcpy(G + ti + 12, "UI_REPLAY", 10);
    M16(ti + 0x44) = fields[0]; M16(ti + 0x46) = fields[1]; M32(pi + 0x14) = fields[2];
    M8(ti + 0x4E) = fields[3]; M32(ti + 0x38) = M32(ti + 0x40) = fields[4];
    M32(ti + 0x70) = gmalloc(fields[4]); assert(fread(G + M32(ti + 0x70), 1, fields[4], f) == fields[4]);
    if (fields[5]) { M32(ti + 0x74) = gmalloc(fields[5]); assert(fread(G + M32(ti + 0x74), 1, fields[5], f) == fields[5]); }
    fclose(f);
    M32(0x982AE0) = (uint32_t)atoi(argv[6]); M32(0x982BDC) = 0x30000; M32(0x30000) = 0x31000;
    M32(0x3105C) = 1; M32(0x3304C) = 2; M32(0x33050) = 3; M32(0x987EC4) = 0x987EC0;
    memcpy(G + 0x8AFFEC, "SFX_TRACKBARRIER", 17);
    uint32_t raw_size, raw = hd_read(argv[2], &raw_size); uint32_t *d = (uint32_t *)(G + raw);
    HdTex h = {.path = argv[2], .ww = d[4], .hh = d[3], .mips = d[7], .fmt = d[21] ? d[21] : 21};
    Cpu c = {.ecx = ti, .esp = 0xF00000};
    if (!strcmp(argv[5], "pc")) f_006DDD90_orig(&c);
    else { diag_on = !strcmp(argv[5], "broken"); if (diag_on) setenv("BOOT_DIAG_UI_ADDED_MIPS", "1", 1); replay_swap(&c, &h, 0); }
    assert(c.esp == 0xF00004 && M32(0x982AE0) == (uint32_t)atoi(argv[6]));
    printf("upload %ux%u levels=%u fmt=%08X\n", upload.w, upload.h, upload.levels, upload.fmt);
    fflush(stdout);
    int rc = !strcmp(argv[4], "-") ? 0 : draw_crop(argv[3], argv[4]);
    free(G); return rc;
} }
