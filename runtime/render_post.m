// render_post.m - the X360 post passes and the mirror pass (split from render.m; shared state in render_internal.h).
#include "render_internal.h"
#include "gen/x360_shader_lits.inc"   // tools/gen_tables.py: X360S_VT_* (the X360 visualtreatment PS literals, fs_vt)
#include "gen/x360_engine.inc"         // X360E_DROP_* (the lens raindrops, from default.xex)

// ---------------- X360 post-processing (x360_post.m, ../render_diff/hdr_dof) ----------------
// The X360 composite as host passes: 4x4 downscale (MRT: colour + luminance, bloom source), 64x64 luminance read
// back by the CPU one frame later, bright pass, 5x5 blur, blur-downsample to w/8 and bloom H/V, optional half-res
// DOF downsample, then the visual treatment: radial blur, DOF on scene depth, polynomial tone curve, vignette,
// exposure, bloom x4. 8-bit targets as on X360. Shaders follow x360_postfx_ref.hlsl line by line.
static const char *x360_src =
"#include <metal_stdlib>\n"
"using namespace metal;\n"
// LUM: public luminance weights for Rec. 709 primaries, as published in the W3C SVG 1.0 spec (feColorMatrix
// luminanceToAlpha: 0.2125/0.7154/0.0721; BT.709-5 rounds the same Y row to 0.2126/0.7152/0.0722). The X360's
// LUMINANCE_VECTOR effect default holds these public values too, so a changed ZZDATA0 default is ignored on purpose.
"constant float3 LUM = float3(0.2125, 0.7154, 0.0721);\n"
"struct VOut { float4 pos [[position]]; float2 uv; };\n"
"vertex VOut vs_full(uint vid [[vertex_id]]) {\n"
"    float2 p = float2((vid << 1) & 2, vid & 2); VOut o; o.pos = float4(p * float2(2, -2) + float2(-1, 1), 0, 1); o.uv = p; return o;\n"
"}\n"
"struct K { float4 off[16]; float4 wgt[16]; float4 p0; };\n"
"// glare source weight (NFS_BLOOM_SURFACES=natural): the part of a pixel's light treated as a source that scatters in\n"
"// the eye/lens: only near white (luminance 0.75 -> 1), so diffuse surfaces and the sky keep their light\n"
"inline float gw(float3 c) { return smoothstep(0.75, 1.0, dot(c, LUM)); }\n"
"inline float hash01(uint2 p, uint seed) {   // integer hash of the pixel (static: same pattern every frame)\n"
"    uint h = p.x * 1973u + p.y * 9277u + seed * 26699u; h = (h ^ 61u) ^ (h >> 16); h *= 9u; h ^= h >> 4; h *= 0x27d4eb2du; h ^= h >> 15;\n"
"    return float(h) * (1.0 / 4294967296.0);\n"
"}\n"
"struct MRT { float4 c0 [[color(0)]]; float4 c1 [[color(1)]]; };\n"
"// MRT1, the bloom source: colour x the bloom mask. p0.z = 1: the mask is the scene alpha (PC's shaders write alpha 1\n"
"// where things bloom, the sky; PC's overbright source is rgb x alpha); 0: 1 - alpha (the X360's shaders write 1 = no bloom)\n"
"fragment MRT fs_downscale(VOut i [[stage_in]], texture2d<float> s [[texture(0)]], sampler sm [[sampler(0)]], constant K &k [[buffer(0)]]) {\n"
"    float4 a = 0; for (int n = 0; n < 16; ++n) a += s.sample(sm, i.uv + k.off[n].xy); a *= 0.0625;\n"
"    float m = k.p0.z > 1.5 ? 1.0 : mix(1.0 - a.a, a.a, k.p0.z);\n"
"    MRT o; o.c0 = float4(a.rgb, dot(a.rgb, LUM)); o.c1 = float4(a.rgb * m, a.a);\n"
"    if (k.p0.w > 0.5) {                  // natural glare: the source weighted per tap (as fs_vt weights the full-res pixel)\n"
"        float3 g = 0; for (int n = 0; n < 16; ++n) { float3 t = s.sample(sm, i.uv + k.off[n].xy).rgb; g += t * gw(t); }\n"
"        o.c1 = float4(g * 0.0625, a.a);\n"
"    }\n"
"    return o;\n"
"}\n"
"// r (what X360 reads back): mean luminance of MRT0; g, b (diagnostics only): scene alpha (the bloom mask, 1 = no\n"
"// bloom) and the luminance of the masked bloom source, from MRT1 at the same taps\n"
"fragment float4 fs_lum(VOut i [[stage_in]], texture2d<float> s [[texture(0)]], texture2d<float> s1 [[texture(1)]], sampler sm [[sampler(0)]], constant K &k [[buffer(0)]]) {\n"
"    float l = 0, a = 0, m = 0;\n"
"    for (int n = 0; n < 9; ++n) { l += s.sample(sm, i.uv + k.off[n].xy).a; float4 b = s1.sample(sm, i.uv + k.off[n].xy); a += b.a; m += dot(b.rgb, LUM); }\n"
"    return float4(l, a, m, 9.0) * 0.111111112;\n"
"}\n"
"fragment float4 fs_bright(VOut i [[stage_in]], texture2d<float> s [[texture(0)]], sampler sm [[sampler(0)]], constant K &k [[buffer(0)]]) {\n"
"    float4 c = s.sample(sm, i.uv); float L = dot(c.rgb, LUM);\n"
"    // by luminance (a blue sky has high blue but a diffuse luminance), and only from what is bright in absolute terms too\n"
"    float e = max(L * k.p0.x / (k.p0.y + 0.001) - k.p0.w, 0.0) * smoothstep(0.55, 0.75, L);\n"
"    return float4(c.rgb * (e / max(L, 1e-4)), c.a);\n"
"}\n"
"fragment float4 fs_taps(VOut i [[stage_in]], texture2d<float> s [[texture(0)]], sampler sm [[sampler(0)]], constant K &k [[buffer(0)]]) {\n"
"    float4 a = 0; for (int n = 0; n < 16; ++n) a += s.sample(sm, i.uv + k.off[n].xy) * k.wgt[n]; return a;\n"
"}\n"
"// the tone curve's luminance with the X360's footprint in screen terms (x360_post.m NFS_CURVE_GLOW): a box over the\n"
"// quarter-res luminance, half-width p0.x texels (fractional edge), p0.y = taps each side, p0.zw = texel size\n"
"fragment float4 fs_tdown(VOut i [[stage_in]], texture2d<float> s [[texture(0)]], sampler sm [[sampler(0)]], constant K &k [[buffer(0)]]) {\n"
"    float r = k.p0.x, a = 0, ws = 0; int n = int(k.p0.y);\n"
"    for (int y = -n; y <= n; ++y) for (int x = -n; x <= n; ++x) {\n"
"        float w = min(r + 1.0 - abs(float(x)), 1.0) * min(r + 1.0 - abs(float(y)), 1.0);\n"
"        if (w <= 0.0) continue;\n"
"        a += s.sample(sm, i.uv + float2(x, y) * k.p0.zw).a * w; ws += w;\n"
"    }\n"
"    return float4(0, 0, 0, a / ws);\n"
"}\n"
"fragment float4 fs_dofdown(VOut i [[stage_in]], texture2d<float> s [[texture(0)]], sampler sm [[sampler(0)]], constant K &k [[buffer(0)]]) {\n"
"    float3 a = s.sample(sm, i.uv + k.off[0].xy).rgb + s.sample(sm, i.uv + k.off[0].zw).rgb + s.sample(sm, i.uv + k.off[1].xy).rgb + s.sample(sm, i.uv + k.off[1].zw).rgb;\n"
"    return float4(a, dot(a, LUM)) * 0.25;\n"
"}\n"
"// rear-view mirror (x360_mirror): the same tone curve, desaturation and exposure on the mirror texture, t = the pixel's luminance\n"
"struct MK { float4 coeffs[4]; float4 misc; };   // misc: x desaturation, y debug (1 = magenta signature), z exposure\n"
"fragment float4 fs_mirror(VOut i [[stage_in]], texture2d<float> scene [[texture(0)]], sampler lin [[sampler(0)]], constant MK &k [[buffer(0)]]) {\n"
"    float4 c = scene.sample(lin, i.uv);\n"
"    float t = dot(c.rgb, LUM);\n"
"    float4 kk = k.coeffs[0] + t * (k.coeffs[1] + t * (k.coeffs[2] + t * k.coeffs[3]));\n"
"    float3 sat = t + (c.rgb - t) * k.misc.x;\n"
"    if (k.misc.y > 0.5) return float4(1, 0, 1, c.a);\n"
"    // PC's FE shader (the white mirror quad, 006E7120) doubles RGB after this pass. Cancel that legacy gain.\n"
"    return float4((sat * kk.x + c.rgb * kk.yzw) * k.misc.z * 0.5, c.a);\n"
"}\n"
"struct VTK { float4 coeffs[4]; float4 b; float4 blurParams; float4 dofp; float4 misc; float4 zr; float4 tp; float4 rx; };\n"
"// rx: x natural glare strength s (0: glow/halo), y highlight roll-off on, z deband threshold (0 off; also the dither), w deband radius (px)\n"
"// tp.y: the bloom mask is the scene alpha (diag view 4). tp.x: NFS_TEST_PATCHES, a strip of known colours in place of the scene's bottom 10 % (x < 0.5: grey ramp 0, 0.1 .. 1;\n"
"// x >= 0.5: skin, foliage, sky, red, green, blue), graded like the scene but without blur, DOF, vignette and bloom\n"
"constant float3 PATCH[6] = { float3(0.76, 0.57, 0.45), float3(0.34, 0.42, 0.18), float3(0.38, 0.48, 0.62),\n"
"                             float3(0.70, 0.10, 0.10), float3(0.10, 0.60, 0.10), float3(0.10, 0.20, 0.70) };\n"
"// misc: x desaturation, y vignette amount, z exposure, w vignette scale; zr: near, far, dof on, bloom scale\n"
"fragment float4 fs_vt(VOut i [[stage_in]], texture2d<float> scene [[texture(0)]], texture2d<float> q0 [[texture(1)]],\n"
"                      texture2d<float> vig [[texture(2)]], texture2d<float> bloom [[texture(3)]], texture2d<float> dofTex [[texture(4)]],\n"
"                      depth2d<float> depth [[texture(5)]], texture2d<float> bself [[texture(6)]], sampler lin [[sampler(0)]], sampler vigs [[sampler(1)]],\n"
"                      constant VTK &k [[buffer(0)]]) {\n"
"    float2 uv = i.uv;\n"
"    float4 c = scene.sample(lin, uv);\n"
"    bool patch = k.tp.x > 0.5 && uv.y > 0.9;\n"
"    uint2 px = uint2(i.pos.xy);\n"
"    if (k.rx.z > 0.0 && k.blurParams.z < 0.5) {   // deband (NFS_DEBAND): 4 taps at a random angle and distance; where all are within\n"
"        // the threshold of the pixel (a smooth gradient stored in 8 bits or DXT steps), their mean replaces it\n"
"        float an = 6.2831853 * hash01(px, 1u), r = k.rx.w * (0.5 + 0.5 * hash01(px, 2u));\n"
"        float2 d = float2(cos(an), sin(an)) * r / float2(scene.get_width(), scene.get_height()), dp = float2(-d.y, d.x);\n"
"        float3 s1 = scene.sample(lin, uv + d).rgb, s2 = scene.sample(lin, uv - d).rgb, s3 = scene.sample(lin, uv + dp).rgb, s4 = scene.sample(lin, uv - dp).rgb;\n"
"        float3 dm = max(max(abs(s1 - c.rgb), abs(s2 - c.rgb)), max(abs(s3 - c.rgb), abs(s4 - c.rgb)));\n"
"        if (max(dm.r, max(dm.g, dm.b)) < k.rx.z) c.rgb = (s1 + s2 + s3 + s4) * 0.25;\n"
"    }\n"
"    float4 v = patch ? float4(0, 0, 1, 0) : vig.sample(vigs, float2(uv.x, uv.y * k.misc.w));\n"
"    float2 st = float2(mix(k.b.x, k.b.z, uv.x), mix(k.b.y, k.b.w, uv.y));\n"
"    float3 blur;\n"
"    int taps = int(k.dofp.w);                               // speed-blur taps over the same length (7 = the X360's)\n"
"    if (taps <= 7) {\n"
"        float3 t1 = scene.sample(lin, uv - 1.0 * st).rgb, t2 = scene.sample(lin, uv - 2.0 * st).rgb, t3 = scene.sample(lin, uv - 3.0 * st).rgb;\n"
"        float3 t4 = scene.sample(lin, uv - 4.0 * st).rgb, t5 = scene.sample(lin, uv - 5.0 * st).rgb, t6 = scene.sample(lin, uv - 6.0 * st).rgb;\n"
"        float3 t7 = scene.sample(lin, uv - 7.0 * st).rgb;\n"
"        blur = (c.rgb + t1) * " X360S_VT_W0 " + (t2 + t3 + t4) * " X360S_VT_W1 " + (t5 + t6) * " X360S_VT_W2 " + t7 * " X360S_VT_W3 ";\n"
"    } else {                                                // finer steps: the X360's weights interpolated along the tap position\n"
"        const float W[8] = {" X360S_VT_W0 ", " X360S_VT_W0 ", " X360S_VT_W1 ", " X360S_VT_W1 ", " X360S_VT_W1 ", " X360S_VT_W2 ", " X360S_VT_W2 ", " X360S_VT_W3 "};\n"
"        float step = 7.0 / float(taps), ws = W[0];\n"
"        blur = c.rgb * W[0];\n"
"        for (int j = 1; j <= taps; ++j) {\n"
"            float x = float(j) * step; int x0 = min(int(x), 6); float w = mix(W[x0], W[x0 + 1], x - float(x0));\n"
"            blur += scene.sample(lin, uv - x * st).rgb * w; ws += w;\n"
"        }\n"
"        blur /= ws;\n"
"    }\n"
"    float d = depth.sample(lin, uv);\n"
"    float z = k.zr.x * k.zr.y / (k.zr.y - d * (k.zr.y - k.zr.x));\n"
"    float zlin = z / 0.5;                                   // X360 constants assume near 0.5: zlin = 2 * metres\n"
"    float fade = saturate(" X360S_VT_FADE_A " - zlin * " X360S_VT_FADE_B ");\n"
"    bool dof = k.zr.z != 0.0;\n"
"    uint sk = uint(k.blurParams.w);                         // test skips (NFS_TEST_POST_SKIP): 1 vignette, 2 local curve\n"
"    float amt = dof ? saturate(v.x + k.blurParams.x) * fade + v.w * k.blurParams.y : saturate((v.x + k.blurParams.x) * fade + v.w * k.blurParams.y);\n"
"    float3 col = c.rgb + (blur - c.rgb) * amt;\n"
"    if (dof) { float w = saturate(k.dofp.z * (abs(zlin - k.dofp.x) - k.dofp.y)); col = mix(col, dofTex.sample(lin, uv).rgb, w); }\n"
"    float t = (sk & 2u) != 0u ? dot(c.rgb, LUM) : q0.sample(lin, uv).a;\n"
"    if ((sk & 1u) != 0u) { v.y = 0.0; v.z = 1.0; }\n"
"    if (patch) { col = uv.x < 0.5 ? float3(min(floor(uv.x * 22.0), 10.0) * 0.1) : PATCH[min(int((uv.x - 0.5) * 12.0), 5)]; c.rgb = col; t = dot(col, LUM); }\n"
"    float4 kk = k.coeffs[0] + t * (k.coeffs[1] + t * (k.coeffs[2] + t * k.coeffs[3]));\n"
"    float l = dot(col, LUM);\n"
"    float3 sat = l + (col - l) * k.misc.x;\n"
"    float3 graded = sat * kk.x + col * kk.yzw;\n"
"    graded += v.y * k.misc.y;\n"
"    if (k.blurParams.z > 3.5) { float m = mix(1.0 - c.a, c.a, k.tp.y); return float4(m, m, m, 1); }   // diag: the bloom mask\n"
"    if (k.blurParams.z > 2.5) return float4(1, 0, 1, 1);\n"
"    if (k.blurParams.z > 1.5) return float4(t, t, t, 1);\n"
"    if (k.blurParams.z > 0.5) return float4(c.rgb, 1);\n"
"    // tp.z > 0 (NFS_BLOOM_SURFACES=halo): minus the bloom's own lift of the pixel's area (the 5x5-blurred bright pass x the\n"
"    // Bloom kernels' gain), so an area bigger than the kernel (snow cap, smoke, lit road) keeps its texture and only the\n"
"    // light that spills past its edges is added; 0: the bloom as is (it lifts a large bright area by ~6x its excess)\n"
"    // rx.x > 0 (natural, default): energy-conserving glare, s x (the wide normalised blur of the source B - B here): a\n"
"    // uniform bright area keeps its value and texture, light moves from bright things into their surroundings\n"
"    float3 bl = bloom.sample(lin, uv).rgb;\n"
"    if (k.rx.x > 0.0) bl = k.rx.x * (bl - c.rgb * gw(c.rgb));\n"
"    else { if (k.tp.z > 0.0) bl = max(bl - k.tp.z * bself.sample(lin, uv).rgb, 0.0); bl *= k.zr.w; }\n"
"    if (patch) bl = 0;\n"
"    float3 base = graded * v.z * k.misc.z;\n"
"    if (k.rx.y > 0.5) {                  // roll-off (NFS_HIGHLIGHTS): added light fills the headroom above the pixel with a soft\n"
"        // limit h (1 - e^(-a/h)), slope 1 for small additions, never past white, hue of the added light kept\n"
"        float3 ap = max(bl, 0.0); float a = max(ap.r, max(ap.g, ap.b));\n"
"        float h = max(1.0 - max(base.r, max(base.g, base.b)), 1e-3);\n"
"        if (a > 1e-5) bl = min(bl, 0.0) + ap * (h * (1.0 - exp(-a / h)) / a);\n"
"    }\n"
"    float3 o = base + bl;\n"
"    if (k.rx.z > 0.0) o += (hash01(px, 3u) + hash01(px, 4u) - 1.0) * (1.0 / 255.0);   // triangular dither, +-1 LSB\n"
"    return float4(o, c.a);\n"
"}\n";

#include "lampglow_shader.h"

typedef struct { float off[16][4], wgt[16][4], p0[4]; } XK;
static id<MTLRenderPipelineState> xp_down, xp_lum, xp_bright, xp_taps, xp_dofdown, xp_tdown, xp_vt_bgra, xp_vt_ms;
static id<MTLLibrary> x_lib;
static id<MTLSamplerState> xs_lin, xs_vig, xs_point;
static id<MTLTexture> xt_q0, xt_q1, xt_bright, xt_bblur, xt_lum, xt_bloomA, xt_bloomB, xt_dof, xt_white, xt_t;
static id<MTLRenderPipelineState> lp_source, lp_taps, lp_vt[9];
static id<MTLTexture> lt_source, lt_blur, lt_a, lt_b;
static id<MTLLibrary> lp_lib;
static id<MTLBuffer> xb_lum;
static uint32_t xw, xh;
static int x_lum_pending;
static uint64_t x_lum_serial;              // the frame that wrote xb_lum
float x360_post_alpha = -1, x360_post_masked = -1;   // last readback's mean scene alpha and masked bloom-source luminance

static id<MTLRenderPipelineState> x_pso(id<MTLLibrary> lib, NSString *fs, MTLPixelFormat f0, MTLPixelFormat f1) {
    MTLRenderPipelineDescriptor *pd = [MTLRenderPipelineDescriptor new];
    pd.vertexFunction = [lib newFunctionWithName:@"vs_full"];
    pd.fragmentFunction = [lib newFunctionWithName:fs];
    pd.colorAttachments[0].pixelFormat = f0;
    if (f1) pd.colorAttachments[1].pixelFormat = f1;
    NSError *err = nil;
    id<MTLRenderPipelineState> p = [mtl newRenderPipelineStateWithDescriptor:pd error:&err];
    if (!p) fprintf(stderr, "boot: x360 post: pipeline %s: %s\n", fs.UTF8String, err.localizedDescription.UTF8String);
    return p;
}
static id<MTLTexture> x_rt(uint32_t w, uint32_t h, int shared) {
    MTLTextureDescriptor *d = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm width:w ? w : 1 height:h ? h : 1 mipmapped:NO];
    d.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead;
    d.storageMode = shared ? MTLStorageModeShared : MTLStorageModePrivate;
    return [mtl newTextureWithDescriptor:d];
}
static int x_init(uint32_t w, uint32_t h) {
    if (!xp_vt_bgra) {
        NSError *err = nil;
        id<MTLLibrary> lib = x_lib = [mtl newLibraryWithSource:[NSString stringWithUTF8String:x360_src] options:nil error:&err];
        if (!lib) { fprintf(stderr, "boot: x360 post: %s\n", err.localizedDescription.UTF8String); return 0; }
        MTLPixelFormat f = MTLPixelFormatRGBA8Unorm;
        xp_down = x_pso(lib, @"fs_downscale", f, f);
        xp_lum = x_pso(lib, @"fs_lum", f, 0);
        xp_bright = x_pso(lib, @"fs_bright", f, 0);
        xp_taps = x_pso(lib, @"fs_taps", f, 0);
        xp_dofdown = x_pso(lib, @"fs_dofdown", f, 0);
        xp_tdown = x_pso(lib, @"fs_tdown", f, 0);
        xp_vt_bgra = x_pso(lib, @"fs_vt", MTLPixelFormatBGRA8Unorm, 0);
        MTLSamplerDescriptor *sd = [MTLSamplerDescriptor new];
        sd.minFilter = sd.magFilter = MTLSamplerMinMagFilterLinear;
        sd.sAddressMode = sd.tAddressMode = MTLSamplerAddressModeClampToEdge;
        xs_lin = [mtl newSamplerStateWithDescriptor:sd];
        sd.tAddressMode = MTLSamplerAddressModeRepeat;                        // UVESVIGNETTE: CLAMP / WRAP
        xs_vig = [mtl newSamplerStateWithDescriptor:sd];
        sd.minFilter = sd.magFilter = MTLSamplerMinMagFilterNearest;
        sd.tAddressMode = MTLSamplerAddressModeClampToEdge;
        xs_point = [mtl newSamplerStateWithDescriptor:sd];
        xt_white = x_rt(1, 1, 1);
        uint32_t white = 0xFFFFFFFF;
        [xt_white replaceRegion:MTLRegionMake2D(0, 0, 1, 1) mipmapLevel:0 withBytes:&white bytesPerRow:4];
        xb_lum = [mtl newBufferWithLength:64 * 64 * 4 options:MTLResourceStorageModeShared];
        if (!xp_down || !xp_lum || !xp_bright || !xp_taps || !xp_dofdown || !xp_tdown || !xp_vt_bgra) return 0;
    }
    w &= ~7u; h &= ~7u;                                                      // X360 rounds the chain to multiples of 8
    if (w != xw || h != xh) {
        xw = w; xh = h;
        xt_q0 = x_rt(w / 4, h / 4, 0); xt_q1 = x_rt(w / 4, h / 4, 0);
        xt_bright = x_rt(w / 4, h / 4, 0); xt_bblur = x_rt(w / 4, h / 4, 0);
        xt_lum = x_rt(64, 64, 0);
        xt_bloomA = x_rt(w / 8, h / 8, 0); xt_bloomB = x_rt(w / 8, h / 8, 0);
        xt_dof = x_rt(w / 2, h / 2, 0);
        xt_t = nil;                                                          // (made at its size when first needed)
        x_lum_pending = 0;
    }
    return 1;
}
static const char *x_tag = "";   // diag label suffix (BOOT_DIAG_GPUPASS): which logical pass a 960x540 encoder is
static void x_pass(id<MTLRenderPipelineState> pso, id<MTLTexture> dst, id<MTLTexture> dst1, id<MTLTexture> src, id<MTLSamplerState> smp, const XK *k, id<MTLTexture> src1) {
    MTLRenderPassDescriptor *rp = [MTLRenderPassDescriptor renderPassDescriptor];
    rp.colorAttachments[0].texture = dst; rp.colorAttachments[0].loadAction = MTLLoadActionDontCare; rp.colorAttachments[0].storeAction = MTLStoreActionStore;
    if (dst1) { rp.colorAttachments[1].texture = dst1; rp.colorAttachments[1].loadAction = MTLLoadActionDontCare; rp.colorAttachments[1].storeAction = MTLStoreActionStore; }
    if (gpt_on) gpt_render(rp, "x360 pass %lux%lu%s", (unsigned long)dst.width, (unsigned long)dst.height, x_tag);
    id<MTLRenderCommandEncoder> e = [cmd renderCommandEncoderWithDescriptor:rp];
    if (gpt_on) gpt_name(e);
    [e setRenderPipelineState:pso];
    [e setFragmentTexture:src atIndex:0];
    if (src1) [e setFragmentTexture:src1 atIndex:1];
    [e setFragmentSamplerState:smp atIndex:0];
    [e setFragmentBytes:k length:sizeof *k atIndex:0];
    [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
    [e endEncoding];
}
// The visual treatment into a multisampled back buffer (AA on)
static id<MTLRenderPipelineState> x_vt_ms(NSUInteger samples) {
    static NSUInteger have;
    if (!xp_vt_ms || have != samples) {
        have = samples;
        MTLRenderPipelineDescriptor *pd = [MTLRenderPipelineDescriptor new];
        pd.vertexFunction = [x_lib newFunctionWithName:@"vs_full"];
        pd.fragmentFunction = [x_lib newFunctionWithName:@"fs_vt"];
        pd.colorAttachments[0].pixelFormat = MTLPixelFormatBGRA8Unorm;
        pd.rasterSampleCount = samples;
        xp_vt_ms = [mtl newRenderPipelineStateWithDescriptor:pd error:nil];
    }
    return xp_vt_ms;
}
// Optional pipelines are compiled only when the lamp supplement is used. The
// original fs_vt and every Off pass remain byte-for-byte the original source.
static int lamp_post_init(uint32_t w, uint32_t h, NSUInteger samples) {
    if (!lp_lib) {
        NSString *src = [NSString stringWithUTF8String:x360_src];
        src = [src stringByReplacingOccurrencesOfString:@"fragment float4 fs_vt(" withString:@"fragment float4 fs_vt_lamp("];
        src = [src stringByReplacingOccurrencesOfString:@"texture2d<float> bself [[texture(6)]]" withString:@"texture2d<float> bself [[texture(6)]], texture2d<float> lamp [[texture(7)]]"];
        src = [src stringByReplacingOccurrencesOfString:@"    if (patch) bl = 0;" withString:@"    bl += lamp.sample(lin, uv).rgb;\n    if (patch) bl = 0;"];
        src = [src stringByAppendingString:[NSString stringWithUTF8String:lampglow_src]];
        NSError *err = nil;
        lp_lib = [mtl newLibraryWithSource:src options:nil error:&err];
        if (!lp_lib) { fprintf(stderr, "boot: lampglow shader: %s\n", err.localizedDescription.UTF8String); return 0; }
        lp_source = x_pso(lp_lib, @"fs_lamp_source", MTLPixelFormatRGBA16Float, 0);
        lp_taps = x_pso(lp_lib, @"fs_taps", MTLPixelFormatRGBA16Float, 0);
    }
    if (samples > 8 || !lp_source || !lp_taps) return 0;
    if (!lp_vt[samples]) {
        MTLRenderPipelineDescriptor *d = [MTLRenderPipelineDescriptor new];
        d.vertexFunction = [lp_lib newFunctionWithName:@"vs_full"];
        d.fragmentFunction = [lp_lib newFunctionWithName:@"fs_vt_lamp"];
        d.colorAttachments[0].pixelFormat = MTLPixelFormatBGRA8Unorm; d.rasterSampleCount = samples;
        lp_vt[samples] = [mtl newRenderPipelineStateWithDescriptor:d error:nil];
    }
    if (!lt_source || lt_source.width != w / 4 || lt_source.height != h / 4) {
        MTLTextureDescriptor *d = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA16Float width:w / 4 height:h / 4 mipmapped:NO];
        d.usage = MTLTextureUsageShaderRead | MTLTextureUsageRenderTarget; d.storageMode = MTLStorageModePrivate;
        lt_source = [mtl newTextureWithDescriptor:d]; lt_blur = [mtl newTextureWithDescriptor:d];
        d.width = w / 8; d.height = h / 8;
        lt_a = [mtl newTextureWithDescriptor:d]; lt_b = [mtl newTextureWithDescriptor:d];
    }
    return lp_vt[samples] && lt_source && lt_blur && lt_a && lt_b;
}

// GaussBlur5x5 (82222418) and Bloom (822227C0) kernels, as hdr_dof_ref.cpp
static void x_gauss5(uint32_t w, uint32_t h, XK *k) {
    memset(k, 0, sizeof *k);
    float tu = 1.0f / w, tv = 1.0f / h, total = 0;
    int n = 0;
    for (int x = -2; x <= 2; ++x)
        for (int y = -2; y <= 2; ++y) {
            if (abs(x) + abs(y) > 2) continue;
            k->off[n][0] = x * tu; k->off[n][1] = y * tv;
            float g = expf((x * x + y * y) * -0.5f) / sqrtf(6.2831854820251465f);
            k->wgt[n][0] = g; total += g; ++n;
        }
    for (int i = 0; i < n; ++i) { float g = k->wgt[i][0] / total; for (int c = 0; c < 4; ++c) k->wgt[i][c] = g; }
}
static void x_bloomk(uint32_t size, int horizontal, XK *k, int normalise) {
    memset(k, 0, sizeof *k);
    float t = 1.0f / size, dev = 3.0f, mult = 2.0f;
    float c = mult * expf(0) / sqrtf(dev * dev * 6.2831854820251465f);
    k->wgt[0][0] = k->wgt[0][1] = k->wgt[0][2] = c; k->wgt[0][3] = 1.0f;
    for (int i = 1; i < 8; ++i) {
        float g = expf(i * i * (-1.0f / (2.0f * dev * dev))) / sqrtf(2.0f * 3.14159265f * dev * dev) * mult;
        k->wgt[i][0] = k->wgt[i][1] = k->wgt[i][2] = g; k->wgt[i][3] = 1.0f;
        k->off[i][horizontal ? 0 : 1] = i * t;
    }
    for (int i = 8; i < 15; ++i) { memcpy(k->wgt[i], k->wgt[i - 7], 16); for (int c = 0; c < 4; ++c) k->off[i][c] = -k->off[i - 7][c]; }
    if (normalise) {                                                         // natural glare: sum 1 (8-bit targets never clip)
        float sum = 0; for (int i = 0; i < 15; ++i) sum += k->wgt[i][0];
        for (int i = 0; i < 15; ++i) for (int c = 0; c < 4; ++c) k->wgt[i][c] /= sum;
    }
}
// what the bloom chain makes of a uniform field of 1 in the bright pass: the 5x5 gauss and the downsample sum to 1,
// the Bloom kernels H and V to ~1.96 each (2 x a 15-tap gaussian, dev 3): ~3.85 (NFS_BLOOM_SURFACES=halo subtracts it)
static float x_bloom_gain(void) {
    static float g;
    if (g == 0) { XK k; x_bloomk(64, 1, &k, 0); float s = 0; for (int i = 0; i < 15; ++i) s += k.wgt[i][0]; g = s * s; }
    return g;
}

// Runs the chain from the guest texture `src` into the guest surface `dst`. Returns the luminance measured from the
// previous frame's 64x64 target (HDR_AverageLuminanceCPU), -1 when there is none yet, -2 when nothing was drawn.
// The rear-view mirror texture `tex` (a render target the mirror view drew into; the HUD pass draws it as a quad)
// through the chain's tone curve, desaturation and `exposure`, in place (copy source); half RGB cancels PC FE x2. 1 = drawn.
int render_x360_mirror(uint32_t tex, const X360PostParams *p, float exposure, int signature) {
    Obj *o = mtl ? obj(tex) : NULL;
    RenderTex *g = o ? gpu_tex(o) : NULL;
    if (!g || !g->tex || g->tex.sampleCount > 1 || !(g->tex.usage & MTLTextureUsageRenderTarget)) return 0;
    id<MTLTexture> t = g->tex;
    if (!x_init(xw ? xw : 64, xh ? xh : 64)) return 0;                       // (library and samplers)
    static id<MTLRenderPipelineState> pso;
    static MTLPixelFormat pso_fmt;
    static id<MTLTexture> copy;
    if (!pso || pso_fmt != t.pixelFormat) {
        MTLRenderPipelineDescriptor *pd = [MTLRenderPipelineDescriptor new];
        pd.vertexFunction = [x_lib newFunctionWithName:@"vs_full"];
        pd.fragmentFunction = [x_lib newFunctionWithName:@"fs_mirror"];
        pd.colorAttachments[0].pixelFormat = t.pixelFormat;
        pso = [mtl newRenderPipelineStateWithDescriptor:pd error:nil];
        pso_fmt = t.pixelFormat;
        if (!pso) return 0;
    }
    if (!copy || copy.width != t.width || copy.height != t.height || copy.pixelFormat != t.pixelFormat) {
        MTLTextureDescriptor *d = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:t.pixelFormat width:t.width height:t.height mipmapped:NO];
        d.usage = MTLTextureUsageShaderRead; d.storageMode = MTLStorageModePrivate;
        copy = [mtl newTextureWithDescriptor:d];
    }
    end_pass();
    ensure_cmd();
    id<MTLBlitCommandEncoder> b = gpt_blit(cmd, "blit colourfilter copy");
    [b copyFromTexture:t sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0, 0, 0) sourceSize:MTLSizeMake(t.width, t.height, 1)
             toTexture:copy destinationSlice:0 destinationLevel:0 destinationOrigin:MTLOriginMake(0, 0, 0)];
    [b endEncoding];
    struct { float coeffs[4][4], misc[4]; } mk;
    memcpy(mk.coeffs, p->coeffs, sizeof mk.coeffs);
    mk.misc[0] = p->desaturation; mk.misc[1] = signature ? 1.0f : 0.0f; mk.misc[2] = exposure; mk.misc[3] = 0;
    MTLRenderPassDescriptor *rp = [MTLRenderPassDescriptor renderPassDescriptor];
    rp.colorAttachments[0].texture = t;
    rp.colorAttachments[0].loadAction = MTLLoadActionDontCare; rp.colorAttachments[0].storeAction = MTLStoreActionStore;
    if (gpt_on) gpt_render_auto(rp, "colourfilter");
    id<MTLRenderCommandEncoder> e = [cmd renderCommandEncoderWithDescriptor:rp];
    if (gpt_on) gpt_name(e);
    [e setRenderPipelineState:pso];
    [e setFragmentTexture:copy atIndex:0];
    [e setFragmentSamplerState:xs_point atIndex:0];
    [e setFragmentBytes:&mk length:sizeof mk atIndex:0];
    [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
    [e endEncoding];
    pass_dirty = 1;
    return 1;
}

float render_x360_post(uint32_t src, uint32_t dst, uint32_t vignette, const X360PostParams *p) {
    if (!mtl) return -2;
    Obj *so = obj(src);
    RenderTex *sg = so ? gpu_tex(so) : NULL;
    NSUInteger dl, dsl;
    static id<MTLTexture> scratch;                                           // dst 0: BOOT_DIAG_POSTFX_AB's side run
    id<MTLTexture> out = nil;
    if (dst) out = surface_target(dst, &dl, &dsl);
    else if (sg && sg->tex) {
        if (!scratch || scratch.width != sg->tex.width || scratch.height != sg->tex.height) {
            MTLTextureDescriptor *d = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:sg->tex.width height:sg->tex.height mipmapped:NO];
            d.usage = MTLTextureUsageRenderTarget; d.storageMode = MTLStorageModePrivate;
            scratch = [mtl newTextureWithDescriptor:d];
        }
        out = scratch; dl = dsl = 0;
    }
    if (!sg || !sg->tex || !out || out.pixelFormat != MTLPixelFormatBGRA8Unorm || !x_init((uint32_t)sg->tex.width, (uint32_t)sg->tex.height)) {
        static int n;
        if (diag_on && n++ < 3) diag_event("x360 post: not drawn (source %d, target %d, format %lu)", sg && sg->tex, out != nil, (unsigned long)out.pixelFormat);
        return -2;
    }
    msaa_resolve(&ad_rt);                                                    // AA on: DOF reads the resolved scene depth
    if (diag_on) {                                                           // the chain's sizes, when they change
        static NSUInteger last_w, last_h, last_ow;
        if (sg->tex.width != last_w || sg->tex.height != last_h || out.width != last_ow) {
            last_w = sg->tex.width; last_h = sg->tex.height; last_ow = out.width;
            diag_event("x360 post: input %lux%lu (%lu samples) -> output %lux%lu (%lu samples); chain %ux%u: downscale/bright/blur %ux%u, bloom %ux%u, dof %ux%u, luminance 64x64; depth %lux%lu",
                       (unsigned long)sg->tex.width, (unsigned long)sg->tex.height, (unsigned long)sg->tex.sampleCount, (unsigned long)out.width, (unsigned long)out.height,
                       (unsigned long)out.sampleCount, xw, xh, xw / 4, xh / 4, xw / 8, xh / 8, xw / 2, xh / 2,
                       (unsigned long)(autodepth_tex ? autodepth_tex.width : 0), (unsigned long)(autodepth_tex ? autodepth_tex.height : 0));
        }
    }
    if (out.sampleCount > 1) surface_rt(dst)->stale = 1;
    float measured = -1;
    if (x_lum_pending) {                                                     // last frame's readback
        uint64_t w0 = host_ticks();
        wait_serial(x_lum_serial);                                           // usually done already (one frame in flight)
        diag_idle_ns[IDLE_GPU] += host_ticks() - w0;
        const uint32_t *t = xb_lum.contents;
        double sum = 0, sa = 0, sm = 0;
        for (int i = 0; i < 4096; ++i) { sum += t[i] & 0xFF; sa += t[i] >> 8 & 0xFF; sm += t[i] >> 16 & 0xFF; }   // r as X360's low byte
        measured = (float)(sum * 9.536743e-07);
        x360_post_alpha = (float)(sa / (4096 * 255.0)); x360_post_masked = (float)(sm / (4096 * 255.0));
    }
    end_pass();
    ensure_cmd();
    uint32_t w = xw, h = xh;
    XK k;
    // 1. downscale 4x4 into the two quarter-res targets
    memset(&k, 0, sizeof k);
    for (int y = 0; y < 4; ++y) for (int x = 0; x < 4; ++x) { k.off[y * 4 + x][0] = (x - 1.5f) / (w / 4); k.off[y * 4 + x][1] = (y - 1.5f) / (h / 4); }
    k.p0[2] = (float)p->bloom_mask_pc; k.p0[3] = p->glare > 0 ? 1.0f : 0.0f;
    x_pass(xp_down, xt_q0, xt_q1, sg->tex, xs_lin, &k, nil);
    // 2. 64x64 luminance, copied for the CPU (read next frame)
    memset(&k, 0, sizeof k);
    for (int i = -1, n = 0; i <= 1; ++i) for (int j = -1; j <= 1; ++j, ++n) { k.off[n][0] = i / 192.0f; k.off[n][1] = j / 192.0f; }
    x_pass(xp_lum, xt_lum, nil, xt_q0, xs_lin, &k, xt_q1);
    id<MTLBlitCommandEncoder> b = gpt_blit(cmd, "blit x360 lum readback");
    [b copyFromTexture:xt_lum sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0, 0, 0) sourceSize:MTLSizeMake(64, 64, 1)
              toBuffer:xb_lum destinationOffset:0 destinationBytesPerRow:256 destinationBytesPerImage:16384];
    [b endEncoding];
    x_lum_pending = 1;
    x_lum_serial = frame_serial;
    // 3. bright pass, 5x5 blur, blur-downsample to w/8, bloom horizontal then vertical
    memset(&k, 0, sizeof k);
    k.p0[0] = p->middle_gray; k.p0[1] = p->adapted_lum; k.p0[3] = p->bloom_thresh;
    int glare = p->glare > 0;                                                // natural: the downscale wrote the weighted source
    if (!glare) x_pass(xp_bright, xt_bright, nil, xt_q1, xs_lin, &k, nil);
    x_gauss5(w / 4, h / 4, &k);
    x_pass(xp_taps, xt_bblur, nil, glare ? xt_q1 : xt_bright, xs_lin, &k, nil);
    x_pass(xp_taps, xt_bloomA, nil, xt_bblur, xs_lin, &k, nil);
    for (int pass = 0; pass < (glare ? 2 : 1); ++pass) {                     // natural: twice, normalised (a wider, sqrt 2 x dev, glare)
        x_bloomk(w / 8, 1, &k, glare);
        x_pass(xp_taps, xt_bloomB, nil, xt_bloomA, xs_lin, &k, nil);
        x_bloomk(h / 8, 0, &k, glare);
        x_pass(xp_taps, xt_bloomA, nil, xt_bloomB, xs_lin, &k, nil);
    }
    id<MTLTexture> lamp = p->lamp_glow > 0 ? render_lamp_source() : nil;
    if (lamp && lamp.width == sg->tex.width && lamp.height == sg->tex.height && lamp_post_init(w, h, out.sampleCount)) {
        memset(&k, 0, sizeof k);
        for (int y = 0; y < 4; ++y) for (int x = 0; x < 4; ++x) {
            k.off[y * 4 + x][0] = (x - 1.5f) / (w / 4); k.off[y * 4 + x][1] = (y - 1.5f) / (h / 4);
        }
        k.p0[0] = p->lamp_glow; k.p0[1] = glare;
        k.p0[2] = glare ? p->glare : p->bloom_scale * x_bloom_gain();
        k.wgt[0][0] = p->middle_gray; k.wgt[0][1] = p->adapted_lum;
        k.wgt[0][2] = p->bloom_thresh; k.wgt[0][3] = p->bloom_mask_pc;
        x_tag = " lamp-source";
        x_pass(lp_source, lt_source, nil, sg->tex, xs_lin, &k, lamp);
        x_gauss5(w / 4, h / 4, &k);
        x_tag = " lamp-blur5";
        x_pass(lp_taps, lt_blur, nil, lt_source, xs_lin, &k, nil);
        x_tag = " lamp-bloom";
        x_pass(lp_taps, lt_a, nil, lt_blur, xs_lin, &k, nil);
        for (int pass = 0; pass < (glare ? 2 : 1); ++pass) {
            x_bloomk(w / 8, 1, &k, 1); x_pass(lp_taps, lt_b, nil, lt_a, xs_lin, &k, nil);
            x_bloomk(h / 8, 0, &k, 1); x_pass(lp_taps, lt_a, nil, lt_b, xs_lin, &k, nil);
        }
        x_tag = "";
    } else lamp = nil;
    // 4. DOF half-res blur source
    if (p->dof) {
        memset(&k, 0, sizeof k);
        float tu = 1.0f / (w / 2), tv = 1.0f / (h / 2);
        k.off[0][0] = -tu; k.off[0][1] = -tv; k.off[0][2] = tu; k.off[0][3] = -tv;
        k.off[1][0] = -tu; k.off[1][1] = tv; k.off[1][2] = tu; k.off[1][3] = tv;
        x_pass(xp_dofdown, xt_dof, nil, sg->tex, xs_lin, &k, nil);
    }
    // 4b. the tone curve's luminance (x360_post.m NFS_CURVE_GLOW): the quarter-res MRT0 alpha averages 16x16 pixels,
    // which on the X360's 1280x720 is 1.25 % of the screen; at a larger back buffer the same 16 pixels are a smaller
    // part of it and the curve's glow shrinks to a tight halo on every edge. Scaled (0, default): averaged again over
    // 2 (s - 1) quarter-res texels each side, s = height / 720, into a target 1/s that size, so the footprint is 16 s
    // pixels and the field as smooth as the X360's. 720p (s = 1) uses MRT0 as before.
    id<MTLTexture> tsrc = xt_q0;
    float s = h / 720.0f;
    if (p->curve_glow == 0 && s > 1.0001f) {
        uint32_t tw = (uint32_t)lroundf(w / 4 / s), th = (uint32_t)lroundf(h / 4 / s);
        if (!xt_t || xt_t.width != tw || xt_t.height != th) xt_t = x_rt(tw, th, 0);
        memset(&k, 0, sizeof k);
        k.p0[0] = 2.0f * (s - 1.0f); k.p0[1] = ceilf(k.p0[0] - 1e-4f); k.p0[2] = 1.0f / (w / 4); k.p0[3] = 1.0f / (h / 4);
        x_pass(xp_tdown, xt_t, nil, xt_q0, xs_lin, &k, nil);
        tsrc = xt_t;
    }
    // speed-blur taps: the X360's 7 steps at 720p; more over the same length at higher resolutions so the steps stay
    // ~2-6 pixels apart instead of separate ghost copies (NFS_TEST_SPEEDBLUR_TAPS=<n> forces a count, 7 = the X360's)
    static int taps_env = -1;
    if (taps_env < 0) { const char *e = getenv("NFS_TEST_SPEEDBLUR_TAPS"); taps_env = e ? atoi(e) : 0; }
    int taps = (p->skip & 4) ? 7 : taps_env > 0 ? taps_env : (int)lroundf(7.0f * fmaxf(s, 1.0f));   // (skip 4: test, the X360's 7)
    if (diag_on) {
        static int last_taps, last_glow = -1; static NSUInteger last_tw;
        if (taps != last_taps || p->curve_glow != last_glow || tsrc.width != last_tw) {
            last_taps = taps; last_glow = p->curve_glow; last_tw = tsrc.width;
            diag_event("x360 post: speed-blur taps %d, tone-curve luminance %lux%lu (glow %d)", taps, (unsigned long)tsrc.width, (unsigned long)tsrc.height, p->curve_glow);
        }
    }
    // 5. visual treatment into the game's output surface
    Obj *vo = obj(vignette);
    RenderTex *vg = vo ? gpu_tex(vo) : NULL;
    struct { float coeffs[4][4], b[4], blurParams[4], dofp[4], misc[4], zr[4], tp[4], rx[4]; } vk;
    memcpy(vk.coeffs, p->coeffs, sizeof vk.coeffs);
    memcpy(vk.b, p->blur, 16);
    vk.blurParams[0] = p->blur_params[0]; vk.blurParams[1] = p->blur_params[1]; vk.blurParams[2] = (float)p->debug; vk.blurParams[3] = (float)((p->skip & 3) | (p->curve_glow == 2 ? 2 : 0));
    memcpy(vk.dofp, p->dof_params, 16);
    vk.dofp[3] = (float)(taps < 7 ? 7 : taps > 64 ? 64 : taps);
    vk.misc[0] = p->desaturation; vk.misc[1] = p->vignette; vk.misc[2] = p->exposure; vk.misc[3] = 1.0f;
    static int patches = -1;
    if (patches < 0) { const char *e = getenv("NFS_TEST_PATCHES"); patches = e && *e == '1'; }
    vk.tp[0] = patches ? 1.0f : 0.0f; vk.tp[1] = p->bloom_mask_pc == 1 ? 1.0f : 0.0f; vk.tp[2] = p->bloom_self * x_bloom_gain(); vk.tp[3] = 0;
    vk.rx[0] = p->glare; vk.rx[1] = p->rolloff ? 1.0f : 0.0f; vk.rx[2] = p->deband; vk.rx[3] = 16.0f * fmaxf(s, 1.0f) * (720.0f / 1080.0f);   // radius 16 px at 1080p
    vk.zr[0] = p->znear; vk.zr[1] = p->zfar; vk.zr[2] = p->dof ? 1.0f : 0.0f; vk.zr[3] = p->bloom_scale;
    MTLRenderPassDescriptor *rp = [MTLRenderPassDescriptor renderPassDescriptor];
    rp.colorAttachments[0].texture = out; rp.colorAttachments[0].level = dl; rp.colorAttachments[0].slice = dsl;
    rp.colorAttachments[0].loadAction = MTLLoadActionLoad; rp.colorAttachments[0].storeAction = MTLStoreActionStore;
    if (gpt_on) gpt_render_auto(rp, lamp ? "x360 vt+lamp" : "x360 vt");
    id<MTLRenderCommandEncoder> e = [cmd renderCommandEncoderWithDescriptor:rp];
    if (gpt_on) { gpt_name(e); gpt_phase = "after-vt"; }
    [e setRenderPipelineState:lamp ? lp_vt[out.sampleCount] : (out.sampleCount > 1 ? x_vt_ms(out.sampleCount) : xp_vt_bgra)];
    [e setFragmentTexture:sg->tex atIndex:0];
    [e setFragmentTexture:tsrc atIndex:1];
    [e setFragmentTexture:vg && vg->tex ? vg->tex : xt_white atIndex:2];
    [e setFragmentTexture:xt_bloomA atIndex:3];
    [e setFragmentTexture:xt_dof atIndex:4];
    [e setFragmentTexture:autodepth_tex atIndex:5];
    [e setFragmentTexture:xt_bblur atIndex:6];
    if (lamp) [e setFragmentTexture:lt_a atIndex:7];
    [e setFragmentSamplerState:xs_lin atIndex:0];
    [e setFragmentSamplerState:xs_vig atIndex:1];
    [e setFragmentBytes:&vk length:sizeof vk atIndex:0];
    [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
    [e endEncoding];
    pass_dirty = 1;
    static int vig_dumped;                                                   // diag: UVESVIGNETTE once (x blur mask, y additive, z brightness, w speed-effect mask)
    if ((texdump_frame() || (diag_on && !vig_dumped++)) && vg && vg->tex) texdump(vignette, vg->tex);   // (src is still being rendered in this command buffer)
    return measured;
}

// ---------------- lens raindrops, X360 order and look (x360_post.m, ../render_diff/x360_port_candidates.md #4) ----------------
// After the composite, as the X360 draws them (Function_82448000 from the post composite): each drop is a quad
// (FUN_8245C4E0: r = 0.05 size, x .. x + r / aspect by y - r .. y in screen uv, so round on any aspect) drawn with
// the particles effect's onscreen_distort (PS 09beae34): d = DIFFUSEMAP(uv).yz (ONSCREENRAINDROP, 32x32, wrap),
// scene = MISCMAP1(d * LOCALCENTER.zw + LOCALCENTER.xy) (the composited frame, clamp), a = OPACITYMAP(u / 4 +
// BASEALPHAREF.y, v) (ONSCREENRAINDROPALPHA, 4 variants side by side), rgb = scene * a.g, alpha = BASEALPHAREF.x * a.r,
// with LOCALCENTER = (x - r, y + r, r, -r) and BASEALPHAREF = (3 life, variant / 4). Blended source alpha over the
// frame, colour only (as PC's rain_drop pass; the X360 code sets its blend state outside the effect).
static const char *drops_src =
"#include <metal_stdlib>\n"
"using namespace metal;\n"
"struct Drop { float4 rect; float4 lc; float4 ba; };   // rect: NDC x0, y0 (bottom), x1, y1 (top)\n"
"struct DV { float4 pos [[position]]; float2 uv; float4 lc [[flat]]; float4 ba [[flat]]; };\n"
"vertex DV vs_drop(uint vid [[vertex_id]], uint iid [[instance_id]], constant Drop *d [[buffer(0)]]) {\n"
"    Drop q = d[iid]; float2 c = float2(vid & 1, vid >> 1);   // strip: (0,0) (1,0) (0,1) (1,1)\n"
"    DV o; o.pos = float4(mix(q.rect.x, q.rect.z, c.x), mix(q.rect.y, q.rect.w, c.y), 0, 1);\n"
"    o.uv = float2(c.x, 1.0 - c.y); o.lc = q.lc; o.ba = q.ba; return o;\n"
"}\n"
"fragment float4 fs_drop(DV i [[stage_in]], texture2d<float> disp [[texture(0)]], texture2d<float> scene [[texture(1)]],\n"
"                        texture2d<float> opac [[texture(2)]], sampler wrap [[sampler(0)]], sampler clamp [[sampler(1)]]) {\n"
"    float2 d = disp.sample(wrap, i.uv).yz;\n"
"    float3 s = scene.sample(clamp, d * i.lc.zw + i.lc.xy).rgb;\n"
"    float2 a = opac.sample(clamp, float2(i.uv.x * 0.25 + i.ba.y, i.uv.y)).yx;\n"
"    if (i.ba.z > 0.5) return float4(1, 0, 1, a.y > 0.1 ? 1.0 : 0.0);   // BOOT_DIAG_PROBE raindrops: the drops' cover, magenta\n"
"    return float4(s * a.x, saturate(i.ba.x * a.y));\n"
"}\n";

// The drops' library, samplers and pipeline for the target's sample count: made by the first composite (the _warm
// call below, so during loading) instead of the first rain frame, where the Metal compile cost a 119 ms frame.
static id<MTLRenderPipelineState> drops_pso;
static id<MTLSamplerState> s_wrap, s_clamp;
static int raindrops_prepare(NSUInteger samples) {
    static id<MTLLibrary> lib;
    static NSUInteger pso_samples;
    if (!lib) {
        uint64_t t0 = host_ticks();
        NSError *err = nil;
        lib = [mtl newLibraryWithSource:[NSString stringWithUTF8String:drops_src] options:nil error:&err];
        if (!lib) { fprintf(stderr, "boot: raindrops: %s\n", err.localizedDescription.UTF8String); return 0; }
        MTLSamplerDescriptor *sd = [MTLSamplerDescriptor new];
        sd.minFilter = sd.magFilter = MTLSamplerMinMagFilterLinear;
        sd.sAddressMode = sd.tAddressMode = MTLSamplerAddressModeRepeat;
        s_wrap = [mtl newSamplerStateWithDescriptor:sd];
        sd.sAddressMode = sd.tAddressMode = MTLSamplerAddressModeClampToEdge;
        s_clamp = [mtl newSamplerStateWithDescriptor:sd];
        ++render_static_made;
        if (diag_on) diag_event("render: raindrops library %.1f ms", (host_ticks() - t0) / 1e6);
    }
    if (!drops_pso || pso_samples != samples) {
        uint64_t t0 = host_ticks();
        MTLRenderPipelineDescriptor *pd = [MTLRenderPipelineDescriptor new];
        pd.vertexFunction = [lib newFunctionWithName:@"vs_drop"];
        pd.fragmentFunction = [lib newFunctionWithName:@"fs_drop"];
        MTLRenderPipelineColorAttachmentDescriptor *ca = pd.colorAttachments[0];
        ca.pixelFormat = MTLPixelFormatBGRA8Unorm;
        ca.blendingEnabled = YES;
        ca.sourceRGBBlendFactor = ca.sourceAlphaBlendFactor = MTLBlendFactorSourceAlpha;
        ca.destinationRGBBlendFactor = ca.destinationAlphaBlendFactor = MTLBlendFactorOneMinusSourceAlpha;
        ca.writeMask = MTLColorWriteMaskRed | MTLColorWriteMaskGreen | MTLColorWriteMaskBlue;
        pd.rasterSampleCount = samples;
        NSError *err = nil;
        drops_pso = [mtl newRenderPipelineStateWithDescriptor:pd error:&err];
        pso_samples = samples;
        if (!drops_pso) { fprintf(stderr, "boot: raindrops: pipeline: %s\n", err.localizedDescription.UTF8String); return 0; }
        ++render_static_made;
        if (diag_on) diag_event("render: raindrops pipeline (%u samples) %.1f ms", (unsigned)samples, (host_ticks() - t0) / 1e6);
    }
    return 1;
}
// The composite's call (x360_post.m), every frame it runs: once per target sample count, builds the drop pass's
// pipeline, uploads the two drop textures and the ripple array, so the first rain frame draws with everything made.
void render_x360_raindrops_warm(uint32_t dst, uint32_t disp_tex, uint32_t alpha_tex) {
    static NSUInteger warm_samples;
    if (!mtl || getenv("NFS_NO_RAINWARM")) return;
    NSUInteger dl, dsl;
    id<MTLTexture> out = surface_target(dst, &dl, &dsl);
    if (!out || out.pixelFormat != MTLPixelFormatBGRA8Unorm || warm_samples == out.sampleCount) return;
    Obj *o0 = obj(disp_tex), *o1 = obj(alpha_tex);
    uint64_t t0 = host_ticks();
    if (o0 && o1) { gpu_tex(o0); gpu_tex(o1); }
    if (diag_on) diag_event("render: raindrops textures %.1f ms", (host_ticks() - t0) / 1e6);
    if (raindrops_prepare(out.sampleCount)) { render_ripple_warm(); warm_samples = out.sampleCount; }
}
int render_x360_raindrops(uint32_t dst, const RainDrop *d, int n, uint32_t disp_tex, uint32_t alpha_tex, int signature) {
    if (!mtl || n <= 0) return 0;
    Obj *o0 = obj(disp_tex), *o1 = obj(alpha_tex);
    RenderTex *g0 = o0 ? gpu_tex(o0) : NULL, *g1 = o1 ? gpu_tex(o1) : NULL;
    RenderTex *r = surface_rt(dst);
    NSUInteger dl, dsl;
    id<MTLTexture> out = surface_target(dst, &dl, &dsl);
    if (!g0 || !g0->tex || !g1 || !g1->tex || !r || !r->tex || !out || out.pixelFormat != MTLPixelFormatBGRA8Unorm) return 0;
    static id<MTLTexture> copy;
    if (!raindrops_prepare(out.sampleCount)) return 0;
    id<MTLRenderPipelineState> pso = drops_pso;
    // the source: the composited frame (resolved when multisampled; else a copy, since the pass draws into it)
    id<MTLTexture> src = r->tex;
    if (r->ms) msaa_resolve(r);
    end_pass();
    ensure_cmd();
    if (!r->ms) {
        if (!copy || copy.width != src.width || copy.height != src.height || copy.pixelFormat != src.pixelFormat) {
            MTLTextureDescriptor *td = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:src.pixelFormat width:src.width height:src.height mipmapped:NO];
            td.usage = MTLTextureUsageShaderRead; td.storageMode = MTLStorageModePrivate;
            copy = [mtl newTextureWithDescriptor:td];
        }
        id<MTLBlitCommandEncoder> b = gpt_blit(cmd, "blit lensdrops copy");
        [b copyFromTexture:src sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0, 0, 0) sourceSize:MTLSizeMake(src.width, src.height, 1)
                 toTexture:copy destinationSlice:0 destinationLevel:0 destinationOrigin:MTLOriginMake(0, 0, 0)];
        [b endEncoding];
        src = copy;
    }
    struct { float rect[4], lc[4], ba[4]; } q[64];
    if (n > 64) n = 64;
    float aspect = (float)out.width / (float)out.height;
    for (int i = 0; i < n; ++i) {
        float rr = X360E_DROP_RADIUS * d[i].size, x0 = d[i].x * 2.0f - 1.0f, y0 = 1.0f - d[i].y * 2.0f;
        q[i].rect[0] = x0; q[i].rect[1] = y0; q[i].rect[2] = x0 + rr / aspect * 2.0f; q[i].rect[3] = y0 + rr * 2.0f;
        q[i].lc[0] = d[i].x - rr; q[i].lc[1] = d[i].y + rr; q[i].lc[2] = rr; q[i].lc[3] = -rr;
        q[i].ba[0] = d[i].life * X360E_DROP_LIFE; q[i].ba[1] = (float)(d[i].variant & 3) * 0.25f; q[i].ba[2] = signature ? 1.0f : 0.0f; q[i].ba[3] = 0;
    }
    MTLRenderPassDescriptor *rp = [MTLRenderPassDescriptor renderPassDescriptor];
    rp.colorAttachments[0].texture = out; rp.colorAttachments[0].level = dl; rp.colorAttachments[0].slice = dsl;
    rp.colorAttachments[0].loadAction = MTLLoadActionLoad; rp.colorAttachments[0].storeAction = MTLStoreActionStore;
    if (gpt_on) gpt_render_auto(rp, "lensdrops");
    id<MTLRenderCommandEncoder> e = [cmd renderCommandEncoderWithDescriptor:rp];
    if (gpt_on) gpt_name(e);
    [e setRenderPipelineState:pso];
    [e setVertexBytes:q length:sizeof q[0] * (NSUInteger)n atIndex:0];
    [e setFragmentTexture:g0->tex atIndex:0];
    [e setFragmentTexture:src atIndex:1];
    [e setFragmentTexture:g1->tex atIndex:2];
    [e setFragmentSamplerState:s_wrap atIndex:0];
    [e setFragmentSamplerState:s_clamp atIndex:1];
    [e drawPrimitives:MTLPrimitiveTypeTriangleStrip vertexStart:0 vertexCount:4 instanceCount:(NSUInteger)n];
    [e endEncoding];
    if (r->ms) r->stale = 1;
    pass_dirty = 1;
    return n;
}
