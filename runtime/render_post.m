// render_post.m - the X360 post passes and the mirror pass (split from render.m; shared state in render_internal.h).
#include "render_internal.h"
#include "hdrclip.h"
#include "post_cuts.h"
#include "gen/x360_shader_lits.inc"   // tools/gen_tables.py: X360S_VT_* (the X360 visualtreatment PS literals, fs_vt)
#include "gen/x360_engine.inc"         // X360E_DROP_* (the lens raindrops, from default.xex)

// ---------------- X360 post-processing (x360_post.m, reference renders) ----------------
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

// Separate curve library: Off never compiles or appends this source.
id<MTLRenderPipelineState> post_cuts_curve_pipeline(id<MTLDevice> device, MTLPixelFormat format) {
    static id<MTLLibrary> lib;
    static id<MTLRenderPipelineState> pipelines[2];
    unsigned slot=format==MTLPixelFormatRGBA16Float;
    if (!pipelines[slot]) {
        NSError *error=nil;
        if (!lib) lib=[device newLibraryWithSource:hdrclip_shader([NSString stringWithUTF8String:post_cuts_curve_source()]) options:nil error:&error];
        if (!lib) return nil;
        MTLRenderPipelineDescriptor *d=[MTLRenderPipelineDescriptor new];
        d.vertexFunction=[lib newFunctionWithName:@"vs_full"];
        d.fragmentFunction=[lib newFunctionWithName:@"fs_tdown"];
        d.colorAttachments[0].pixelFormat=format;
        pipelines[slot]=pipeline_build(d,"post_curve",&error);
    }
    return pipelines[slot];
}

// BEGIN HDR output M post variant (also compiled by hdr_output_test.py).
// The legacy shader source is untouched. Only EDR composition uses this variant:
// bound the polynomial's input, keep radiance in the multiplicative colour, and
// shoulder the complete exposed/bloomed result in linear light above white.
static NSString *x_edr_source(NSString *src) {
    src=[src stringByReplacingOccurrencesOfString:@"float4 kk = k.coeffs[0]"
        withString:@"t = saturate(t); float4 kk = k.coeffs[0]"];
    src=[src stringByReplacingOccurrencesOfString:@"if (k.rx.y > 0.5) {"
        withString:@"if (false) {"];
    src=[src stringByReplacingOccurrencesOfString:@"    return float4(o, c.a);"
        withString:@"    o=max(select(o,float3(0),!isfinite(o)),0.0);\n"
        "    o=select(pow((o+0.055)/1.055,float3(2.4)),o/12.92,o<=0.04045);\n"
        "    float peak=max(o.r,max(o.g,o.b)), H=k.tp.w;\n"
        "    if(peak>1.0)o*=(1.0+(H-1.0)*(1.0-exp(-(peak-1.0)/(H-1.0))))/peak;\n"
        "    o=select(1.055*pow(o,float3(1.0/2.4))-0.055,12.92*o,o<=0.0031308);\n"
        "    return float4(o,clamp(c.a,0.0,1.0));"];
    return src;
}
// END HDR output M post variant
__attribute__((weak,noinline)) float render_hdr_composition_headroom(void) { return 1; }
__attribute__((weak,noinline)) void render_hdr_scene_treated(void) {}
static int x_edr_processing;

// Clean-room depth AO. No imported shader code or assets. All attenuation is
// capped at 8%, including cars already carrying vertex AO (VERSIONS V53).
// The scalar kernel is also executed by ssao_test.py on synthetic depth.
// SSAO2 resume (2026-10-28): half-res four-tap RG16 AO/depth, depth-bilateral
// blur/upsample, and RGB-only multiply into the scene. Coverage is half-res R8
// with four original pixel centers and conservative D16 depth/alpha handling.
// The original half-res/16-tap RGBA16 shader and full-res mask remain the proof.
// Diagnostic observations only, NOT timing evidence: kernel .123, multiply
// .270, coverage .144, depth .103, H .030, V .029 ms; sum .699 (old WIP .803).
// Both held times pass raw/full-chain darkening counts (<4% drift). PARTIAL:
// full-res mask still catches 2 changed pixels at 15 s (0 at 25 s); <=.55 ms
// is unmet. Default Off unchanged. All 12 native runs used. See ssao_test.py.
static const char *ssao_legacy_src =
"struct AK { float4 proj; float4 size; float4 rect; float4 bounds; float4 tune; };\n"
"inline float ao_pair(float nx, float ny, float nz, float dx, float dy, float dz, float radius) {\n"
"    float d2 = dx*dx + dy*dy + dz*dz;\n"
"    if (d2 < 0.000001 || d2 >= radius*radius) return 0.0;\n"
"    return max((nx*dx + ny*dy + nz*dz) / sqrt(d2) - 0.04, 0.0) * max(1.0 - d2/(radius*radius), 0.0);\n"
"}\n"
"inline float ao_z(float d, constant AK &k) { return k.proj.z*k.proj.w/(k.proj.w-d*(k.proj.w-k.proj.z)); }\n"
"inline float ao_depth(depth2d<float> z, float2 uv, constant AK &k) {\n"
"    constexpr sampler pt(coord::normalized, filter::nearest, address::clamp_to_edge);\n"
"    return z.sample(pt, clamp(k.rect.xy + uv*k.rect.zw, k.bounds.xy, k.bounds.zw));\n"
"}\n"
"inline float3 ao_point(float2 uv,float raw,constant AK &k) {float d=ao_z(raw,k);return float3((uv*float2(2,-2)+float2(-1,1))*k.proj.xy*d,d);}\n"
"inline float3 ao_pos(depth2d<float> z, float2 uv, constant AK &k) {return ao_point(uv,ao_depth(z,uv,k),k);}\n"
"inline float3 ao_normal(depth2d<float> z, float2 uv, constant AK &k) {\n"
"    float3 p=ao_pos(z,uv,k); float2 t=k.size.xy;\n"
"    float3 l=p-ao_pos(z,uv-float2(t.x,0),k), r=ao_pos(z,uv+float2(t.x,0),k)-p;\n"
"    float3 u=p-ao_pos(z,uv-float2(0,t.y),k), d=ao_pos(z,uv+float2(0,t.y),k)-p;\n"
"    float3 n=cross(abs(l.z)<abs(r.z)?l:r, abs(u.z)<abs(d.z)?u:d);\n"
"    n *= rsqrt(max(dot(n,n),1e-20)); return dot(n,p)>0 ? -n : n;\n"
"}\n"
"inline float2 ao_encode(float3 n) { n/=max(abs(n.x)+abs(n.y)+abs(n.z),1e-9); return n.z<0 ? (1.0-abs(n.yx))*select(float2(1),float2(-1),n.xy<0) : n.xy; }\n"
"inline float3 ao_decode(float2 e) { float3 n=float3(e,1.0-abs(e.x)-abs(e.y)); if(n.z<0)n.xy=(1.0-abs(n.yx))*select(float2(1),float2(-1),n.xy<0);return normalize(n); }\n"
"constant float2 directions[8]={float2(1,0),float2(0.70710678,0.70710678),float2(0,1),float2(-0.70710678,0.70710678),float2(-1,0),float2(-0.70710678,-0.70710678),float2(0,-1),float2(0.70710678,-0.70710678)};\n"
"fragment float4 fs_ao(VOut i [[stage_in]], depth2d<float> z [[texture(1)]], texture2d<float> mask [[texture(3)]], constant AK &k [[buffer(0)]]) {\n"
"    constexpr sampler pt(coord::normalized, filter::nearest, address::clamp_to_edge);\n"
"    // Snap to the full-res depth pixel that owns this AO sample.\n"
"    float2 uv=(floor(i.uv/k.size.xy)+0.5)*k.size.xy;\n"
"    float raw=ao_depth(z,uv,k);float3 p=ao_point(uv,raw,k); if (raw>=0.999999 || mask.sample(pt,k.rect.xy+uv*k.rect.zw).r<0.999) return float4(0,p.z,ao_encode(ao_normal(z,i.uv,k)));\n"
"    float3 n=ao_normal(z,uv,k); float occ=0;\n"
"    float2 radius=clamp(k.tune.x/(max(p.z,0.01)*abs(k.proj.xy))*0.5, k.size.xy*2.0, k.size.xy*24.0);\n"
"    for (int a=0;a<8;++a) for (int r=1;r<=2;++r) {\n"
"        float2 q=uv+directions[a]*radius*(float(r)*0.5);\n"
"        if (any(q<=0.0)||any(q>=1.0))continue;float qd=ao_depth(z,q,k);if(qd>=0.999999 || mask.sample(pt,k.rect.xy+q*k.rect.zw).r<0.999) continue;\n"
"        float3 v=ao_point(q,qd,k)-p; occ+=ao_pair(n.x,n.y,n.z,v.x,v.y,v.z,k.tune.x);\n"
"    }\n"
"    return float4(occ/16.0,p.z,ao_encode(ao_normal(z,i.uv,k)));\n"
"}\n"
"inline float ao_weight(float3 n, float3 qn, float z, float qz, float spatial) {\n"
"    float w=max(dot(n,qn),0.0);w*=w;w*=w;w*=w;return spatial*exp(-abs(z-qz)/max(0.025,z*0.002))*w;\n"
"}\n"
"fragment float4 fs_ao_blur(VOut i [[stage_in]], texture2d<float> ao [[texture(0)]], depth2d<float> z [[texture(1)]], constant AK &k [[buffer(0)]]) {\n"
"    constexpr sampler pt(coord::normalized, filter::nearest, address::clamp_to_edge);\n"
"    float4 center=ao.sample(pt,i.uv); float3 n=ao_decode(center.zw); float cz=center.y, sum=0, ws=0;\n"
"    for (int j=-2;j<=2;++j) {\n"
"        float2 uv=clamp(i.uv+float(j)*k.tune.zw,k.size.xy*0.5,1.0-k.size.xy*0.5); float4 v=ao.sample(pt,uv);\n"
"        float w=ao_weight(n,ao_decode(v.zw),cz,v.y,exp(-float(j*j)*0.5)); sum+=v.x*w;ws+=w;\n"
"    }\n"
"    return float4(sum/max(ws,1e-6),cz,center.zw);\n"
"}\n"
"fragment float4 fs_ao_apply(VOut i [[stage_in]], texture2d<float> scene [[texture(0)]], depth2d<float> z [[texture(1)]],\n"
"    texture2d<float> ao [[texture(2)]], texture2d<float> mask [[texture(3)]], constant AK &k [[buffer(0)]]) {\n"
"    constexpr sampler pt(coord::normalized, filter::nearest, address::clamp_to_edge);\n"
"    float4 c=scene.sample(pt,i.uv); if (ao_depth(z,i.uv,k)>=0.999999) return c;\n"
"    // Mask is white except where transparent geometry passed its original PS/depth tests.\n"
"    if (mask.sample(pt,k.rect.xy+i.uv*k.rect.zw).r<0.999) return c;\n"
"    float cz=ao_z(ao_depth(z,i.uv,k),k); float sum=0,ws=0;\n"
"    float2 sz=float2(ao.get_width(),ao.get_height()), base=floor(i.uv*sz-0.5), f=fract(i.uv*sz-0.5);\n"
"    float4 taps[4];float peak=0;\n"
"    for(int y=0;y<2;++y)for(int x=0;x<2;++x){taps[y*2+x]=ao.sample(pt,(base+float2(x,y)+0.5)/sz);peak=max(peak,taps[y*2+x].x);}\n"
"    if(peak==0.0)return c; // exact zero-AO fast path; no normals or weights needed\n"
"    float3 n=ao_normal(z,i.uv,k);\n"
"    for (int y=0;y<2;++y) for (int x=0;x<2;++x) {\n"
"        float4 v=taps[y*2+x];\n"
"        float spatial=(x?f.x:1.0-f.x)*(y?f.y:1.0-f.y);\n"
"        float w=ao_weight(n,ao_decode(v.zw),cz,v.y,spatial); sum+=v.x*w;ws+=w;\n"
"    }\n"
"    c.rgb*=1.0-min(k.tune.y,0.6*sum/max(ws,1e-6)); return c;\n"
"}\n";

static const char *ssao_src =
"constant float AO_GAIN=0.612;\n"
"struct AK { float4 proj; float4 size; float4 rect; float4 bounds; float4 tune; };\n"
"inline float ao_pair(float nx, float ny, float nz, float dx, float dy, float dz, float radius) {\n"
"    float d2 = dx*dx + dy*dy + dz*dz;\n"
"    if (d2 < 0.000001 || d2 >= radius*radius) return 0.0;\n"
"    return max((nx*dx + ny*dy + nz*dz) * rsqrt(d2) - 0.04, 0.0) * max(1.0 - d2/(radius*radius), 0.0);\n"
"}\n"
"inline float ao_z(float d, constant AK &k) { return k.proj.z*k.proj.w/(k.proj.w-d*(k.proj.w-k.proj.z)); }\n"
"inline float ao_depth(depth2d<float> z, float2 uv, constant AK &k) {\n"
"    constexpr sampler pt(coord::normalized, filter::nearest, address::clamp_to_edge);\n"
"    return z.sample(pt, clamp(k.rect.xy + uv*k.rect.zw, k.bounds.xy, k.bounds.zw));\n"
"}\n"
"inline float3 ao_point(float2 uv,float raw,constant AK &k) {float d=ao_z(raw,k);return float3((uv*float2(2,-2)+float2(-1,1))*k.proj.xy*d,d);}\n"
"inline float3 ao_pos(depth2d<float> z, float2 uv, constant AK &k) {return ao_point(uv,ao_depth(z,uv,k),k);}\n"
"inline float3 ao_normal(depth2d<float> z, float2 uv, constant AK &k) {\n"
"    float3 p=ao_pos(z,uv,k); float2 t=k.size.xy;\n"
"    float3 l=p-ao_pos(z,uv-float2(t.x,0),k), r=ao_pos(z,uv+float2(t.x,0),k)-p;\n"
"    float3 u=p-ao_pos(z,uv-float2(0,t.y),k), d=ao_pos(z,uv+float2(0,t.y),k)-p;\n"
"    float3 n=cross(abs(l.z)<abs(r.z)?l:r, abs(u.z)<abs(d.z)?u:d);\n"
"    n *= rsqrt(max(dot(n,n),1e-20)); return dot(n,p)>0 ? -n : n;\n"
"}\n"
"inline float2 ao_encode(float3 n) { n/=max(abs(n.x)+abs(n.y)+abs(n.z),1e-9); return n.z<0 ? (1.0-abs(n.yx))*select(float2(1),float2(-1),n.xy<0) : n.xy; }\n"
"inline float3 ao_decode(float2 e) { float3 n=float3(e,1.0-abs(e.x)-abs(e.y)); if(n.z<0)n.xy=(1.0-abs(n.yx))*select(float2(1),float2(-1),n.xy<0);return normalize(n); }\n"
"constant float2 directions[8]={float2(1,0),float2(0.70710678,0.70710678),float2(0,1),float2(-0.70710678,0.70710678),float2(-1,0),float2(-0.70710678,-0.70710678),float2(0,-1),float2(0.70710678,-0.70710678)};\n"
"fragment float2 fs_ao(VOut i [[stage_in]], depth2d<float> z [[texture(1)]], texture2d<float> mask [[texture(3)]], constant AK &k [[buffer(0)]]) {\n"
"    constexpr sampler pt(coord::normalized, filter::nearest, address::clamp_to_edge);\n"
"    // Snap to the full-res depth pixel that owns this AO sample.\n"
"    float2 uv=(floor(i.uv/k.size.xy)+0.5)*k.size.xy;\n"
"    float raw=ao_depth(z,uv,k);float3 p=ao_point(uv,raw,k); if (raw>=0.999999 || mask.sample(pt,k.rect.xy+uv*k.rect.zw).r<0.999) return float2(0,p.z);\n"
"    float3 n=ao_normal(z,uv,k); float occ=0;\n"
"    float2 radius=clamp(k.tune.x/(max(p.z,0.01)*abs(k.proj.xy))*0.5, k.size.xy*2.0, k.size.xy*24.0);\n"
"    #pragma clang loop unroll(full)\n"
"    for (int tap=0;tap<4;++tap) {\n"
"        int a=(int(i.pos.x)&1)+2*tap;int r=1+(((int(i.pos.x)>>1)+int(i.pos.y))&1);\n"
"        float2 q=uv+directions[a]*radius*(float(r)*0.5);\n"
"        if (any(q<=0.0)||any(q>=1.0))continue;float qd=ao_depth(z,q,k);if(qd>=0.999999 || mask.sample(pt,k.rect.xy+q*k.rect.zw).r<0.999) continue;\n"
"        float3 v=ao_point(q,qd,k)-p; occ+=ao_pair(n.x,n.y,n.z,v.x,v.y,v.z,k.tune.x);\n"
"    }\n"
"    return float2(occ/4.0,p.z);\n"
"}\n"
"inline float ao_depth_weight(float z,float qz,float spatial) {return spatial*fast::exp(-abs(z-qz)/max(0.025,z*0.002));}\n"
"fragment float2 fs_ao_blur(VOut i [[stage_in]],texture2d<float> ao [[texture(0)]],constant AK &k [[buffer(0)]]) {\n"
"    constexpr sampler pt(coord::normalized,filter::nearest,address::clamp_to_edge);\n"
"    float2 center=ao.sample(pt,i.uv).xy;float sum=0,ws=0;\n"
"    for(int j=-2;j<=2;++j){float2 uv=clamp(i.uv+float(j)*k.tune.zw,k.size.xy*0.5,1.0-k.size.xy*0.5);float2 v=ao.sample(pt,uv).xy;\n"
"        float w=ao_depth_weight(center.y,v.y,exp(-float(j*j)*0.5));sum+=v.x*w;ws+=w;}\n"
"    return float2(sum/max(ws,1e-6),center.y);\n"
"}\n"
"inline float ao_coverage(texture2d<float> mask,float2 uv) {\n"
"    constexpr sampler pt(coord::normalized,filter::nearest,address::clamp_to_edge);\n"
"    float4 c=mask.gather(pt,uv);return min(min(c.x,c.y),min(c.z,c.w));\n"
"}\n"
"inline float ao_at(float2 uv,depth2d<float> z,texture2d<float> ao,texture2d<float> mask,constant AK &k) {\n"
"    constexpr sampler pt(coord::normalized,filter::nearest,address::clamp_to_edge);\n"
"    float raw=ao_depth(z,uv,k);if(raw>=0.999999||ao_coverage(mask,k.rect.xy+uv*k.rect.zw)<0.999)return 1.0;\n"
"    float cz=ao_z(raw,k),sum=0,ws=0;float2 sz=float2(ao.get_width(),ao.get_height()),base=floor(uv*sz-0.5),f=fract(uv*sz-0.5);\n"
"    float2 taps[4];float peak=0;\n"
"    for(int y=0;y<2;++y)for(int x=0;x<2;++x){int j=y*2+x;taps[j]=ao.sample(pt,(base+float2(x,y)+0.5)/sz).xy;peak=max(peak,taps[j].x);}\n"
"    if(peak==0.0)return 1.0;\n"
"    for(int y=0;y<2;++y)for(int x=0;x<2;++x){float2 v=taps[y*2+x];float spatial=(x?f.x:1.0-f.x)*(y?f.y:1.0-f.y);float w=ao_depth_weight(cz,v.y,spatial);sum+=v.x*w;ws+=w;}\n"
"    return 1.0-min(k.tune.y,AO_GAIN*sum/max(ws,1e-6));\n"
"}\n"
"fragment float4 fs_ao_apply(VOut i [[stage_in]], texture2d<float> scene [[texture(0)]], depth2d<float> z [[texture(1)]],\n"
"    texture2d<float> ao [[texture(2)]], texture2d<float> mask [[texture(3)]], constant AK &k [[buffer(0)]]) {\n"
"    constexpr sampler pt(coord::normalized, filter::nearest, address::clamp_to_edge);\n"
"    float4 c=scene.sample(pt,i.uv); c.rgb*=ao_at(i.uv,z,ao,mask,k);return c;\n"
"}\n"
"fragment float4 fs_ao_multiply(VOut i [[stage_in]], depth2d<float> z [[texture(1)]], texture2d<float> ao [[texture(2)]], texture2d<float> mask [[texture(3)]], constant AK &k [[buffer(0)]]) {return float4(0,0,0,ao_at(i.uv,z,ao,mask,k));}\n";

typedef struct { float proj[4],size[4],rect[4],bounds[4],tune[4]; } AOParams;

typedef struct { float off[16][4], wgt[16][4], p0[4]; } XK;
static id<MTLRenderPipelineState> xp_down, xp_lum, xp_bright, xp_taps, xp_dofdown, xp_tdown, xp_vt_bgra, xp_vt_ms;
static id<MTLLibrary> x_lib;
static id<MTLSamplerState> xs_lin, xs_vig, xs_point;
// Resource ownership follows the source player, including odd-height scratch sizes.
#define X_LUM_SLOTS 3
static int x_lum_nowait(void) {
    static int on = -1;
    if (on < 0) { const char *e=getenv("NFS_LUM_NOWAIT"); on=e && !strcmp(e,"1"); }
    return on;
}
struct XLumSlot { id<MTLBuffer> buffer; uint64_t serial; uint32_t generation; int pending, floating; };
typedef struct {
    id<MTLTexture> q0,q1,bright,bblur,lum,bloomA,bloomB,dof,t,scene[2],lamp,drop;
    id<MTLTexture> ao[2],ao_reference[2],ao_scene,ao_reference_scene,ao_original,ao_cpu_scene;
    int ao_ready;
    id<MTLTexture> lsource,lblur,la,lb;
    uint32_t w,h,generation,epoch,mode_generation;
    struct XLumSlot readback[X_LUM_SLOTS];
    int lum_ready;
    float alpha,masked;
} XPostResources;
static XPostResources x_resources[3];
static XPostResources *xr = &x_resources[0];
static uint32_t x_mode_generation=1;
void render_x360_post_reset(void) { ++x_mode_generation; }
#define xt_q0 (xr->q0)
#define xt_q1 (xr->q1)
#define xt_bright (xr->bright)
#define xt_bblur (xr->bblur)
#define xt_lum (xr->lum)
#define xt_bloomA (xr->bloomA)
#define xt_bloomB (xr->bloomB)
#define xt_dof (xr->dof)
#define xt_t (xr->t)
#define xw (xr->w)
#define xh (xr->h)
#define lt_source (xr->lsource)
#define lt_blur (xr->lblur)
#define lt_a (xr->la)
#define lt_b (xr->lb)
static id<MTLTexture> xt_white;
static id<MTLRenderPipelineState> lp_source, lp_taps, lp_vt[2][9];
static id<MTLLibrary> lp_libraries[2];
static id<MTLLibrary> lp_lib;
float x360_post_alpha = -1, x360_post_masked = -1;   // last readback's mean scene alpha and masked bloom-source luminance

static id<MTLRenderPipelineState> x_pso(id<MTLLibrary> lib, NSString *fs, MTLPixelFormat f0, MTLPixelFormat f1) {
    MTLRenderPipelineDescriptor *pd = [MTLRenderPipelineDescriptor new];
    pd.vertexFunction = [lib newFunctionWithName:@"vs_full"];
    pd.fragmentFunction = [lib newFunctionWithName:fs];
    pd.colorAttachments[0].pixelFormat = f0;
    if (f1) pd.colorAttachments[1].pixelFormat = f1;
    NSError *err = nil;
    id<MTLRenderPipelineState> p = pipeline_build(pd, __func__, &err);
    if (!p) fprintf(stderr, "boot: x360 post: pipeline %s: %s\n", fs.UTF8String, err.localizedDescription.UTF8String);
    return p;
}
static id<MTLRenderPipelineState> x_edr_pipeline(NSString *fs, NSUInteger samples, int lamp) {
    static id<MTLLibrary> libraries[2];
    static NSMutableDictionary *states;
    if(!states)states=[NSMutableDictionary new];
    NSString *key=[NSString stringWithFormat:@"%@/%lu/%d",fs,(unsigned long)samples,lamp];
    id<MTLRenderPipelineState> p=states[key];if(p)return p;
    if(!libraries[lamp]) {
        NSString *src=[NSString stringWithUTF8String:x360_src];
        if(lamp) {
            src=[src stringByReplacingOccurrencesOfString:@"texture2d<float> bself [[texture(6)]]" withString:@"texture2d<float> bself [[texture(6)]], texture2d<float> lamp [[texture(7)]]"];
            src=[src stringByReplacingOccurrencesOfString:@"    if (patch) bl = 0;" withString:@"    bl += lamp.sample(lin,uv).rgb;\n    if (patch) bl = 0;"];
        }
        NSError *error=nil;
        libraries[lamp]=[mtl newLibraryWithSource:hdrclip_shader(x_edr_source(src)) options:nil error:&error];
        if(!libraries[lamp]) { fprintf(stderr,"boot: EDR post: %s\n",error.localizedDescription.UTF8String);return nil; }
    }
    MTLRenderPipelineDescriptor *d=[MTLRenderPipelineDescriptor new];
    d.vertexFunction=[libraries[lamp] newFunctionWithName:@"vs_full"];
    d.fragmentFunction=[libraries[lamp] newFunctionWithName:fs];
    d.colorAttachments[0].pixelFormat=MTLPixelFormatRGBA16Float;
    if([fs isEqualToString:@"fs_downscale"])d.colorAttachments[1].pixelFormat=MTLPixelFormatRGBA16Float;
    d.rasterSampleCount=samples;
    p=pipeline_build(d, "edr", NULL);if(p)states[key]=p;
    return p;
}
static int x_pipeline_init(void);
static id<MTLRenderPipelineState> x_contact_smoke_multiply(MTLPixelFormat format);
// All EDR source and PSO work completes at device loading, including both lamp variants.
void render_post_prewarm(void) {
    const char *setting=getenv("NFS_PSO_PREWARM");
    if (!setting || !atoi(setting)) return;
    int failed=!x_pipeline_init(); // the EDR chain also uses the original post setup
    failed+=!x_contact_smoke_multiply(MTLPixelFormatBGRA8Unorm);
    failed+=!x_contact_smoke_multiply(MTLPixelFormatRGBA16Float);
    for (int lamp=0;lamp<2;++lamp) {
        for (NSString *fs in @[@"fs_downscale", @"fs_lum", @"fs_bright", @"fs_taps", @"fs_dofdown", @"fs_tdown"])
            failed+=!x_edr_pipeline(fs,1,lamp);
        for (NSUInteger samples=1;samples<=8;samples*=2)
            if ([mtl supportsTextureSampleCount:samples]) failed+=!x_edr_pipeline(@"fs_vt",samples,lamp);
    }
    if(failed && diag_on)diag_event("pipeline_prewarm: failed=post count=%d",failed);
}
static id<MTLTexture> x_rt_format(uint32_t w, uint32_t h, int shared, MTLPixelFormat format) {
    MTLTextureDescriptor *d = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:format width:w ? w : 1 height:h ? h : 1 mipmapped:NO];
    d.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead;
    d.storageMode = shared ? MTLStorageModeShared : MTLStorageModePrivate;
    return [mtl newTextureWithDescriptor:d];
}
static id<MTLTexture> x_rt(uint32_t w, uint32_t h, int shared) {
    return x_rt_format(w,h,shared,x_edr_processing&&!shared?MTLPixelFormatRGBA16Float:MTLPixelFormatRGBA8Unorm);
}
static int x_pipeline_init(void) {
    if (!xp_vt_bgra) {
        NSError *err = nil;
        id<MTLLibrary> lib = x_lib = [mtl newLibraryWithSource:hdrclip_shader([NSString stringWithUTF8String:x360_src]) options:nil error:&err];
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
        if (!xp_down || !xp_lum || !xp_bright || !xp_taps || !xp_dofdown || !xp_tdown || !xp_vt_bgra) return 0;
    }
    return 1;
}
static int x_init(uint32_t w, uint32_t h) {
    if (!x_pipeline_init()) return 0;
    w = w<8 ? 8 : w&~7u; h = h<8 ? 8 : h&~7u;                                                      // X360 rounds the chain to multiples of 8
    if (w != xw || h != xh || xt_q0.pixelFormat!=(x_edr_processing?MTLPixelFormatRGBA16Float:MTLPixelFormatRGBA8Unorm)) {
        xw = w; xh = h;
        xt_q0 = x_rt(w / 4, h / 4, 0); xt_q1 = x_rt(w / 4, h / 4, 0);
        xt_bright = x_rt(w / 4, h / 4, 0); xt_bblur = x_rt(w / 4, h / 4, 0);
        xt_lum = x_rt(64, 64, 0);
        xt_bloomA = x_rt(w / 8, h / 8, 0); xt_bloomB = x_rt(w / 8, h / 8, 0);
        xt_dof = x_rt(w / 2, h / 2, 0);
        xt_t = nil;                                                          // (made at its size when first needed)

    }
    return xt_q0 && xt_q1 && xt_bright && xt_bblur && xt_lum && xt_bloomA && xt_bloomB && xt_dof;
}
static const char *x_tag = "";   // diag label suffix (BOOT_DIAG_GPUPASS): which logical pass a 960x540 encoder is
static void x_pass(id<MTLRenderPipelineState> pso, id<MTLTexture> dst, id<MTLTexture> dst1, id<MTLTexture> src, id<MTLSamplerState> smp, const XK *k, id<MTLTexture> src1, unsigned hc_stage) {
    if(x_edr_processing) {
        NSString *fs=pso==xp_down?@"fs_downscale":pso==xp_lum?@"fs_lum":pso==xp_bright?@"fs_bright":pso==xp_taps?@"fs_taps":pso==xp_dofdown?@"fs_dofdown":pso==xp_tdown?@"fs_tdown":nil;
        if(fs)pso=x_edr_pipeline(fs,1,0);
        if(!pso)abort();
    }
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
    if (HDRCLIP_ACTIVE) hdrclip_bind(e, hc_stage);
    [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
    [e endEncoding];
    if (HDRCLIP_ACTIVE) { hdrclip_scan(dst,hc_stage); if(dst1)hdrclip_scan(dst1,hc_stage+1); }
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
        xp_vt_ms = pipeline_build(pd, __func__, NULL);
    }
    return xp_vt_ms;
}
// Keep the single-player shader byte-identical; only the view variant maps bounded atlas depth.
static NSString *x_view_source(NSString *src) {
    src=[src stringByReplacingOccurrencesOfString:@"float4 tp; float4 rx; };"
        withString:@"float4 tp; float4 rx; float4 depth_uv; float4 depth_bounds; };"];
    return [src stringByReplacingOccurrencesOfString:@"float d = depth.sample(lin, uv);"
        withString:@"float d = depth.sample(lin, clamp(k.depth_uv.xy + uv * k.depth_uv.zw, k.depth_bounds.xy, k.depth_bounds.zw));"];
}
static id<MTLRenderPipelineState> x_view_pso(NSUInteger samples) {
    static id<MTLLibrary> lib;
    static id<MTLRenderPipelineState> pipelines[9];
    if (samples>8) return nil;
    if (!lib) lib=[mtl newLibraryWithSource:hdrclip_shader(x_view_source([NSString stringWithUTF8String:x360_src])) options:nil error:nil];
    if (!pipelines[samples]) {
        MTLRenderPipelineDescriptor *d=[MTLRenderPipelineDescriptor new];
        d.vertexFunction=[lib newFunctionWithName:@"vs_full"];d.fragmentFunction=[lib newFunctionWithName:@"fs_vt"];
        d.colorAttachments[0].pixelFormat=MTLPixelFormatBGRA8Unorm;d.rasterSampleCount=samples;
        pipelines[samples]=pipeline_build(d, __func__, NULL);
    }
    return pipelines[samples];
}
// Optional pipelines are compiled only when the lamp supplement is used. The
// original fs_vt and every Off pass remain byte-for-byte the original source.
static int lamp_post_init(uint32_t w, uint32_t h, NSUInteger samples, int view) {
    lp_lib=lp_libraries[view];
    if (!lp_lib) {
        NSString *src = [NSString stringWithUTF8String:x360_src];
        if (view) src=x_view_source(src);
        src = [src stringByReplacingOccurrencesOfString:@"fragment float4 fs_vt(" withString:@"fragment float4 fs_vt_lamp("];
        src = [src stringByReplacingOccurrencesOfString:@"texture2d<float> bself [[texture(6)]]" withString:@"texture2d<float> bself [[texture(6)]], texture2d<float> lamp [[texture(7)]]"];
        src = [src stringByReplacingOccurrencesOfString:@"    if (patch) bl = 0;" withString:@"    bl += lamp.sample(lin, uv).rgb;\n    if (patch) bl = 0;"];
        src = [src stringByAppendingString:[NSString stringWithUTF8String:lampglow_src]];
        NSError *err = nil;
        lp_lib = [mtl newLibraryWithSource:hdrclip_shader(src) options:nil error:&err];
        if (!lp_lib) { fprintf(stderr, "boot: lampglow shader: %s\n", err.localizedDescription.UTF8String); return 0; }
        lp_libraries[view]=lp_lib;
        lp_source = x_pso(lp_lib, @"fs_lamp_source", MTLPixelFormatRGBA16Float, 0);
        lp_taps = x_pso(lp_lib, @"fs_taps", MTLPixelFormatRGBA16Float, 0);
    }
    if (samples > 8 || !lp_source || !lp_taps) return 0;
    if (!lp_vt[view][samples]) {
        MTLRenderPipelineDescriptor *d = [MTLRenderPipelineDescriptor new];
        d.vertexFunction = [lp_lib newFunctionWithName:@"vs_full"];
        d.fragmentFunction = [lp_lib newFunctionWithName:@"fs_vt_lamp"];
        d.colorAttachments[0].pixelFormat = MTLPixelFormatBGRA8Unorm; d.rasterSampleCount = samples;
        lp_vt[view][samples] = pipeline_build(d, __func__, NULL);
    }
    if (!lt_source || lt_source.width != w / 4 || lt_source.height != h / 4) {
        MTLTextureDescriptor *d = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA16Float width:w / 4 height:h / 4 mipmapped:NO];
        d.usage = MTLTextureUsageShaderRead | MTLTextureUsageRenderTarget; d.storageMode = MTLStorageModePrivate;
        lt_source = [mtl newTextureWithDescriptor:d]; lt_blur = [mtl newTextureWithDescriptor:d];
        d.width = w / 8; d.height = h / 8;
        lt_a = [mtl newTextureWithDescriptor:d]; lt_b = [mtl newTextureWithDescriptor:d];
    }
    return lp_vt[view][samples] && lt_source && lt_blur && lt_a && lt_b;
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
        pso = pipeline_build(pd, __func__, NULL);
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
    if (HDRCLIP_ACTIVE) hdrclip_bind(e,HC_MIRROR);
    [e setFragmentTexture:copy atIndex:0];
    [e setFragmentSamplerState:xs_point atIndex:0];
    [e setFragmentBytes:&mk length:sizeof mk atIndex:0];
    [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
    [e endEncoding];
    pass_dirty = 1;
    return 1;
}

static int x_rect_valid(const uint32_t r[4], id<MTLTexture> t) {
    return t && r[2] && r[3] && r[0] <= t.width && r[1] <= t.height &&
           r[2] <= t.width-r[0] && r[3] <= t.height-r[1];
}
static void x_destination(id<MTLRenderCommandEncoder> e, const uint32_t r[4]) {
    [e setViewport:(MTLViewport){r[0],r[1],r[2],r[3],0,1}];
    [e setScissorRect:(MTLScissorRect){r[0],r[1],r[2],r[3]}];
}
// Crop before any filtered tap. ClampToEdge now clamps to this player's texel centres.
static id<MTLTexture> x_crop(id<MTLTexture> src, const uint32_t r[4], id<MTLTexture> __strong *cache) {
    if (!x_rect_valid(r,src) || src.sampleCount != 1) return nil;
    if (!*cache || (*cache).width != r[2] || (*cache).height != r[3] || (*cache).pixelFormat != src.pixelFormat) {
        MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:src.pixelFormat width:r[2] height:r[3] mipmapped:NO];
        d.usage=MTLTextureUsageShaderRead; d.storageMode=MTLStorageModePrivate;
        *cache=[mtl newTextureWithDescriptor:d];
    }
    if (!*cache) return nil;
    id<MTLBlitCommandEncoder> b=gpt_blit(cmd,"blit post view crop");
    [b copyFromTexture:src sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(r[0],r[1],0)
        sourceSize:MTLSizeMake(r[2],r[3],1) toTexture:*cache destinationSlice:0 destinationLevel:0 destinationOrigin:MTLOriginMake(0,0,0)];
    [b endEncoding]; return *cache;
}
// Only available after the optional renderer coverage hook has captured this
// frame. Missing coverage fails closed, never darkening transparency by guess.
extern int x360_post_ssao_enabled(void);
extern id<MTLTexture> render_ssao_coverage(id<MTLTexture> depth, int allow_cuts);
extern id<MTLTexture> render_ssao_reference_coverage(void);
extern id<MTLTexture> render_ssao_cpu_reference_coverage(void);
extern int postfx_look_variant(const char *v);
static int x_contact_pair(void) { return diag_on && (postfx_look_variant("ssaon")||postfx_look_variant("ssaobroken")); }
static dispatch_queue_t x_contact_io(void) {
    static dispatch_queue_t queue;static dispatch_once_t once;
    dispatch_once(&once,^{queue=dispatch_queue_create("nfs.ssao.proof",
        dispatch_queue_attr_make_with_qos_class(DISPATCH_QUEUE_SERIAL,QOS_CLASS_UTILITY,0));});
    return queue;
}
// Held depth sidecar avoids HUD/rain refraction contaminating edge measurement.
// AOZ2: width, height, near, far, inverse projection x/y (28 bytes), float32 depth.
static void x_contact_depth(id<MTLTexture> depth,const X360PostParams *p,const X360PostView *view) {
    if (!diag_on||(!x_contact_pair()&&p->debug!=5)||!depth) return;
    uint32_t w=view?view->depth[2]:(uint32_t)depth.width,h=view?view->depth[3]:(uint32_t)depth.height;
    NSUInteger pitch=((w*4+255)/256)*256;
    id<MTLBuffer> buffer=[mtl newBufferWithLength:pitch*h options:MTLResourceStorageModeShared];
    if (!buffer) return;
    id<MTLBlitCommandEncoder> b=gpt_blit(cmd,"ssao diagnostic depth");
    [b copyFromTexture:depth sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(view?view->depth[0]:0,view?view->depth[1]:0,0)
        sourceSize:MTLSizeMake(w,h,1) toBuffer:buffer destinationOffset:0 destinationBytesPerRow:pitch destinationBytesPerImage:pitch*h
        options:depth.pixelFormat==MTLPixelFormatDepth32Float_Stencil8?MTLBlitOptionDepthFromDepthStencil:MTLBlitOptionNone];
    [b endEncoding];
    uint32_t platform=view?M32(view->source_view):0x009842D0u;
    struct { char magic[4];uint32_t w,h;float zn,zf,px,py; } header={{'A','O','Z','2'},w,h,p->znear,p->zfar,1/MF32(platform+0x40),1/MF32(platform+0x54)};
    _Static_assert(sizeof header==28,"AO depth header layout");
    NSString *path=[NSString stringWithFormat:@"%s/ssao_depth_%llu_%u.aodz",diag_dir,(unsigned long long)frame_serial,view?view->source_view:0];
    [cmd addCompletedHandler:^(id<MTLCommandBuffer> done) {
        if (done.status!=MTLCommandBufferStatusCompleted) return;
        dispatch_async(x_contact_io(),^{ @autoreleasepool {
        FILE *f=fopen(path.UTF8String,"wb");if (!f) return;
        fwrite(&header,sizeof header,1,f);
        for (uint32_t y=0;y<h;++y) fwrite((const uint8_t *)buffer.contents+y*pitch,4,w,f);
        fclose(f);
        }});
    }];
}
static void x_contact_shot(id<MTLTexture> texture,const char *variant,const char *suffix) {
    int floating=texture.pixelFormat==MTLPixelFormatRGBA16Float;
    NSUInteger w=texture.width,h=texture.height,pitch=((w*(floating?8:4)+255)/256)*256;
    id<MTLBuffer> buffer=[mtl newBufferWithLength:pitch*h options:MTLResourceStorageModeShared];
    if (!buffer) return;
    id<MTLBlitCommandEncoder> b=gpt_blit(cmd,"ssao diagnostic paired image");
    [b copyFromTexture:texture sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(w,h,1)
        toBuffer:buffer destinationOffset:0 destinationBytesPerRow:pitch destinationBytesPerImage:pitch*h];[b endEncoding];
    NSString *path=[NSString stringWithFormat:@"%s/ssao_pair_%llu_%s_%s.png",diag_dir,(unsigned long long)frame_serial,
        variant,suffix];
    int rgba=texture.pixelFormat==MTLPixelFormatRGBA8Unorm;int r8=texture.pixelFormat==MTLPixelFormatR8Unorm;
    [cmd addCompletedHandler:^(id<MTLCommandBuffer> done) {
        if (done.status!=MTLCommandBufferStatusCompleted) return;
        dispatch_async(x_contact_io(),^{ @autoreleasepool {
        uint8_t *px=malloc(w*h*4);if (!px) return;
        for (NSUInteger y=0;y<h;++y) {
            if(floating){
                for(NSUInteger x=0;x<w;x++){__fp16 v[4];memcpy(v,(const uint8_t *)buffer.contents+y*pitch+x*8,8);for(unsigned c=0;c<4;c++)px[(y*w+x)*4+(c<3?2-c:c)]=(uint8_t)lrintf(fminf(1,fmaxf(0,(float)v[c]))*255);}
            } else if (r8) {
                for(NSUInteger x=0;x<w;++x){uint8_t v=((const uint8_t *)buffer.contents)[y*pitch+x];uint8_t *q=px+(y*w+x)*4;q[0]=q[1]=q[2]=v;q[3]=255;}
            } else memcpy(px+y*w*4,(const uint8_t *)buffer.contents+y*pitch,w*4);
            if (rgba) for (NSUInteger x=0;x<w;++x) { uint8_t *q=px+(y*w+x)*4;uint8_t t=q[0];q[0]=q[2];q[2]=t; }
        }
        diag_png(path.UTF8String,px,(uint32_t)w,(uint32_t)h,(uint32_t)w*4,0);free(px);
        }});
    }];
}
static id<MTLRenderPipelineState> x_contact_multiply(id<MTLLibrary> library,MTLPixelFormat format) {
    MTLRenderPipelineDescriptor *d=[MTLRenderPipelineDescriptor new];
    d.vertexFunction=[library newFunctionWithName:@"vs_full"];d.fragmentFunction=[library newFunctionWithName:@"fs_ao_multiply"];
    d.colorAttachments[0].pixelFormat=format;d.colorAttachments[0].blendingEnabled=YES;
    d.colorAttachments[0].sourceRGBBlendFactor=MTLBlendFactorZero;d.colorAttachments[0].destinationRGBBlendFactor=MTLBlendFactorSourceAlpha;
    d.colorAttachments[0].writeMask=MTLColorWriteMaskRed|MTLColorWriteMaskGreen|MTLColorWriteMaskBlue;
    return pipeline_build(d, __func__, NULL);
}
static id<MTLRenderPipelineState> x_contact_smoke_multiply(MTLPixelFormat format) {
    static id<MTLRenderPipelineState> pso[3];unsigned n=format==MTLPixelFormatRGBA16Float?2:format==MTLPixelFormatBGRA8Unorm;
    if(!pso[n]){
        NSString *src=[[[NSString stringWithUTF8String:x360_src] stringByAppendingString:[NSString stringWithUTF8String:ssao_src]] stringByAppendingString:
            @"\nfragment float4 fs_ao_smoke(VOut i [[stage_in]], depth2d<float> z [[texture(1)]], texture2d<float> ao [[texture(2)]], texture2d<float> mask [[texture(3)]], texture2d<float> smoke [[texture(4)]], constant AK &k [[buffer(0)]]) { constexpr sampler pt(coord::normalized,filter::nearest,address::clamp_to_edge); float4 p=smoke.sample(pt,i.uv);float a=ao_at(i.uv,z,ao,mask,k);return float4(p.rgb*a,(1.0f-p.a)*a); }\n"];
        id<MTLLibrary> lib=[mtl newLibraryWithSource:src options:nil error:nil];
        MTLRenderPipelineDescriptor *d=[MTLRenderPipelineDescriptor new];d.vertexFunction=[lib newFunctionWithName:@"vs_full"];d.fragmentFunction=[lib newFunctionWithName:@"fs_ao_smoke"];
        d.colorAttachments[0].pixelFormat=format;d.colorAttachments[0].blendingEnabled=YES;d.colorAttachments[0].sourceRGBBlendFactor=MTLBlendFactorOne;d.colorAttachments[0].destinationRGBBlendFactor=MTLBlendFactorSourceAlpha;
        d.colorAttachments[0].writeMask=MTLColorWriteMaskRed|MTLColorWriteMaskGreen|MTLColorWriteMaskBlue;
        if(lib)pso[n]=pipeline_build(d, __func__, NULL);
    }
    return pso[n];
}
static struct { id<MTLRenderPipelineState> kernel,blur,apply,multiply_rgba,multiply_bgra,multiply_float; } contact_pso;
static int contact_state; // 0 not requested, 1 compiling, 2 ready, 3 failed
// Contact-shadow library and pipelines, built on Metal's compiler queue; published once complete.
void render_contact_warm(void) {
    int expected=0;
    if (!mtl || !__atomic_compare_exchange_n(&contact_state,&expected,1,0,__ATOMIC_ACQ_REL,__ATOMIC_ACQUIRE)) return;
    NSString *src=[[NSString stringWithUTF8String:x360_src] stringByAppendingString:[NSString stringWithUTF8String:ssao_src]];
    [mtl newLibraryWithSource:src options:nil completionHandler:^(id<MTLLibrary> lib, NSError *err) {
        if (!lib) { fprintf(stderr,"boot: SSAO shader: %s\n",err.localizedDescription.UTF8String); __atomic_store_n(&contact_state,3,__ATOMIC_RELEASE); return; }
        contact_pso.kernel=x_pso(lib,@"fs_ao",MTLPixelFormatRG16Float,0);
        contact_pso.blur=x_pso(lib,@"fs_ao_blur",MTLPixelFormatRG16Float,0);
        contact_pso.apply=x_pso(lib,@"fs_ao_apply",MTLPixelFormatRGBA8Unorm,0);
        contact_pso.multiply_rgba=x_contact_multiply(lib,MTLPixelFormatRGBA8Unorm);
        contact_pso.multiply_bgra=x_contact_multiply(lib,MTLPixelFormatBGRA8Unorm);
        contact_pso.multiply_float=x_contact_multiply(lib,MTLPixelFormatRGBA16Float);
        __atomic_store_n(&contact_state,2,__ATOMIC_RELEASE);
    }];
}
extern uint64_t ssao_diag_post_ns;
extern int ssao_cpu_diag;
static id<MTLTexture> x_contact_scene_impl(id<MTLTexture>,id<MTLTexture>,const X360PostParams *,const X360PostView *);
static id<MTLTexture> x_contact_scene(id<MTLTexture> scene,id<MTLTexture> depth,const X360PostParams *p,const X360PostView *view) {
    uint64_t t0=ssao_cpu_diag?host_ticks():0;
    id<MTLTexture> result=x_contact_scene_impl(scene,depth,p,view);
    if(ssao_cpu_diag)ssao_diag_post_ns+=host_ticks()-t0;
    return result;
}
static id<MTLTexture> x_contact_scene_impl(id<MTLTexture> scene, id<MTLTexture> depth,
                                    const X360PostParams *p, const X360PostView *view) {
    xr->ao_ready=0;
    if (!x360_post_ssao_enabled() || !depth) return scene;
    id<MTLTexture> mask=render_ssao_coverage(depth,!view && scene.width==depth.width && scene.height==depth.height);
    if (!mask) {
        static int warned;
        if (diag_on && !warned) { warned=1;diag_event("ssao: On requested, skipped without current transparency coverage"); }
        return scene;
    }
    uint32_t platform=view ? M32(view->source_view) : 0x009842D0u;
    float px=MF32(platform+0x40), py=MF32(platform+0x54);
    if (!isfinite(px)||!isfinite(py)||fabsf(px)<0.01f||fabsf(py)<0.01f) return scene;
    static id<MTLRenderPipelineState> legacy_kernel,legacy_blur,legacy_apply;
    // Compiled off the frame (first use entering free roam stalled 500+ ms under load); until ready the frame skips AO.
    if (__atomic_load_n(&contact_state,__ATOMIC_ACQUIRE)!=2) { render_contact_warm(); return scene; }
    id<MTLRenderPipelineState> kernel=contact_pso.kernel,blur=contact_pso.blur,apply=contact_pso.apply;
    id<MTLRenderPipelineState> multiply=scene.pixelFormat==MTLPixelFormatRGBA16Float?contact_pso.multiply_float:scene.pixelFormat==MTLPixelFormatBGRA8Unorm?contact_pso.multiply_bgra:contact_pso.multiply_rgba;
    if (!kernel||!blur||!apply||!multiply) return scene;
    NSUInteger hw=(scene.width+1)/2, hh=(scene.height+1)/2;
    int pair=x_contact_pair();
    if (!xr->ao[0]||xr->ao[0].width!=hw||xr->ao[0].height!=hh) {
        MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRG16Float width:hw height:hh mipmapped:NO];
        d.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead; d.storageMode=MTLStorageModePrivate;
        xr->ao[0]=[mtl newTextureWithDescriptor:d];xr->ao[1]=[mtl newTextureWithDescriptor:d];
    }
    if (pair && (!xr->ao_scene||xr->ao_scene.width!=scene.width||xr->ao_scene.height!=scene.height))
    {
        xr->ao_scene=x_rt_format((uint32_t)scene.width,(uint32_t)scene.height,0,MTLPixelFormatRGBA8Unorm);
        if (pair) xr->ao_reference_scene=x_rt_format((uint32_t)scene.width,(uint32_t)scene.height,0,MTLPixelFormatRGBA8Unorm);
    }
    if (pair&&(!xr->ao_reference_scene||xr->ao_reference_scene.width!=scene.width||xr->ao_reference_scene.height!=scene.height))
        xr->ao_reference_scene=x_rt_format((uint32_t)scene.width,(uint32_t)scene.height,0,MTLPixelFormatRGBA8Unorm);
    id<MTLTexture> applied=scene;
    if (!xr->ao[0]||!xr->ao[1]||!applied||(pair&&(!xr->ao_scene||!xr->ao_reference_scene))) return scene;
    AOParams k={.proj={1/px,1/py,p->znear,p->zfar},.size={1.0f/scene.width,1.0f/scene.height,0,0},
        .rect={0,0,1,1},.bounds={0.5f/depth.width,0.5f/depth.height,1-0.5f/depth.width,1-0.5f/depth.height},.tune={0.75f,0.08f,0,0}};
    // Keep the original two-pixel bilateral sigma despite the coarser AO grid.
    k.size[2]=0.25f*(scene.width/(float)hw)*(scene.width/(float)hw);
    k.size[3]=0.25f*(scene.height/(float)hh)*(scene.height/(float)hh);
    if (view) for (int j=0;j<2;++j) {
        float sz=j?depth.height:depth.width;
        k.rect[j]=view->depth[j]/sz;k.rect[j+2]=view->depth[j+2]/sz;
        k.bounds[j]=(view->depth[j]+0.5f)/sz;k.bounds[j+2]=(view->depth[j]+view->depth[j+2]-0.5f)/sz;
    }
    const char *labels[]={"ssao kernel","ssao bilateral H","ssao bilateral V","ssao apply"};
    // Negative control and normal AO consume exactly the same held scene/depth.
    // Finish with normal AO so diagnostics cannot change the displayed look.
    id<MTLTexture> __strong *reference=xr->ao_reference;
    if (pair) {
        MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA16Float width:(scene.width+1)/2 height:(scene.height+1)/2 mipmapped:NO];
        d.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;d.storageMode=MTLStorageModePrivate;
        reference[0]=[mtl newTextureWithDescriptor:d];reference[1]=[mtl newTextureWithDescriptor:d];
        if (!reference[0]||!reference[1]) return scene;
        if(!xr->ao_original||xr->ao_original.width!=scene.width||xr->ao_original.height!=scene.height||xr->ao_original.pixelFormat!=scene.pixelFormat){
            MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:scene.pixelFormat width:scene.width height:scene.height mipmapped:NO];d.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;d.storageMode=MTLStorageModePrivate;xr->ao_original=[mtl newTextureWithDescriptor:d];
        }
        id<MTLBlitCommandEncoder> original=gpt_blit(cmd,"ssao diagnostic original");
        [original copyFromTexture:scene sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(scene.width,scene.height,1) toTexture:xr->ao_original destinationSlice:0 destinationLevel:0 destinationOrigin:MTLOriginMake(0,0,0)];[original endEncoding];
        x_contact_shot(scene,"skipped","on"); // deliberately broken: the apply is absent
    }
    if (pair&&!legacy_kernel) {
        NSError *error=nil;id<MTLLibrary> lib=[mtl newLibraryWithSource:[[NSString stringWithUTF8String:x360_src] stringByAppendingString:[NSString stringWithUTF8String:ssao_legacy_src]] options:nil error:&error];
        legacy_kernel=x_pso(lib,@"fs_ao",reference[0].pixelFormat,0);legacy_blur=x_pso(lib,@"fs_ao_blur",reference[0].pixelFormat,0);legacy_apply=x_pso(lib,@"fs_ao_apply",MTLPixelFormatRGBA8Unorm,0);
        if (!legacy_kernel||!legacy_blur||!legacy_apply) return scene;
    }
    id<MTLTexture> reference_mask=pair?render_ssao_reference_coverage():nil;
    id<MTLTexture> cpu_mask=pair?render_ssao_cpu_reference_coverage():nil;
    if(pair&&(!reference_mask||!cpu_mask))return scene;
    static id<MTLTexture> uncovered;
    if(pair){
        if(!uncovered){MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatR8Unorm width:1 height:1 mipmapped:NO];d.storageMode=MTLStorageModeShared;d.usage=MTLTextureUsageShaderRead;uncovered=[mtl newTextureWithDescriptor:d];uint8_t white=255;[uncovered replaceRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0 withBytes:&white bytesPerRow:1];}
        if(!xr->ao_cpu_scene||xr->ao_cpu_scene.width!=scene.width||xr->ao_cpu_scene.height!=scene.height||xr->ao_cpu_scene.pixelFormat!=scene.pixelFormat){MTLTextureDescriptor*d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:scene.pixelFormat width:scene.width height:scene.height mipmapped:NO];d.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;d.storageMode=MTLStorageModePrivate;xr->ao_cpu_scene=[mtl newTextureWithDescriptor:d];}
    }
    if (pair&&!view) {x_contact_shot(mask,"normal","coverage");x_contact_shot(cpu_mask,"cpu_reference","coverage");x_contact_shot(reference_mask,"legacy","coverage");}
    for (int variant=pair?4:0;variant>=0;--variant) {
        k.tune[0]=0.75f*(variant==1?8.0f:1.0f);
        const char *name=variant==4?"uncovered":variant==3?"cpu_reference":variant==2?"legacy":variant?"broken":"normal";
        int multiplied=variant==0||variant>=3;
        if(variant>=3){id<MTLBlitCommandEncoder>b=gpt_blit(cmd,"ssao proof original copy");[b copyFromTexture:scene toTexture:xr->ao_cpu_scene];[b endEncoding];}
        if (pair) x_contact_shot(scene,name,"off");
        for (int pass=0;pass<4;++pass) {
            id<MTLTexture> __strong *chain=variant==2?reference:xr->ao;
            id<MTLTexture> target=pass==3?(variant>=3?xr->ao_cpu_scene:variant==2?xr->ao_reference_scene:variant==1?xr->ao_scene:applied):chain[pass==1];
            id<MTLTexture> input=pass==1?chain[0]:pass==2?chain[1]:scene;
            k.tune[2]=pass==1?1.0f/chain[0].width:0;k.tune[3]=pass==2?1.0f/chain[0].height:0;
            MTLRenderPassDescriptor *rp=[MTLRenderPassDescriptor renderPassDescriptor];
            rp.colorAttachments[0].texture=target;rp.colorAttachments[0].loadAction=pass==3&&multiplied?MTLLoadActionLoad:MTLLoadActionDontCare;rp.colorAttachments[0].storeAction=MTLStoreActionStore;
            id<MTLTexture> smoke=pass==3 && variant==0 && !view?render_soft_smoke_layer():nil;
            id<MTLRenderPipelineState> smoke_pso=smoke?x_contact_smoke_multiply(target.pixelFormat):nil;
            if (gpt_on) gpt_render(rp,"%s",smoke_pso?"ssao multiply + soft smoke":pass==3&&variant==0?"ssao multiply":labels[pass]);
            id<MTLRenderCommandEncoder> e=[cmd renderCommandEncoderWithDescriptor:rp];
            if (gpt_on) gpt_name(e);
            [e setRenderPipelineState:smoke_pso?:variant==2?(pass==0?legacy_kernel:pass==3?legacy_apply:legacy_blur):(pass==0?kernel:pass==3?(multiplied?multiply:apply):blur)];
            if(smoke_pso){[e setFragmentTexture:smoke atIndex:4];render_soft_smoke_layer_applied();}
            if(pass!=3||!multiplied)[e setFragmentTexture:input atIndex:0];[e setFragmentTexture:depth atIndex:1];
            if (pass==0||pass==3) [e setFragmentTexture:variant==4?uncovered:variant==3?cpu_mask:variant==2?reference_mask:mask atIndex:3];
            if (pass==3) [e setFragmentTexture:chain[0] atIndex:2];
            [e setFragmentBytes:&k length:sizeof k atIndex:0];
            [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[e endEncoding];
        }
        if (pair) x_contact_shot(variant>=3?xr->ao_cpu_scene:variant==2?xr->ao_reference_scene:variant==1?xr->ao_scene:applied,name,"on");
    }
    xr->ao_ready=1;
    return applied;
}



float render_x360_post(uint32_t src, uint32_t dst, uint32_t vignette, const X360PostParams *p) {
    return render_x360_post_view(src,dst,vignette,p,NULL);
}
float render_x360_post_view(uint32_t src, uint32_t dst, uint32_t vignette, const X360PostParams *p, const X360PostView *view) {
    if (!mtl) return -2;
    extern void render_soft_smoke_proof_finish(void);
    if (diag_on) render_soft_smoke_proof_finish();
    Obj *so = obj(src);
    RenderTex *sg = view ? surface_rt(view->source_surface) : (so ? gpu_tex(so) : NULL);
    xr = &x_resources[view ? (view->source_view == 0x009196C0u ? 2 : 1) : 0];
    uint32_t generation=view ? view->generation:0;
    if (xr->generation != generation || xr->mode_generation != x_mode_generation) {
        xr->generation = generation;xr->mode_generation=x_mode_generation;++xr->epoch;
        xr->alpha=xr->masked=-1;
        xr->lum_ready=0;
        // Keep in-flight buffers retired until completion; generation rejects their samples.
    }
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
    x_edr_processing=!view && dst==dev.backbuffer && render_hdr_composition_headroom()>1 && out.pixelFormat==MTLPixelFormatRGBA16Float;
    if (!sg || !sg->tex || !out || (out.pixelFormat != MTLPixelFormatBGRA8Unorm && !x_edr_processing) || !x_init(view ? view->source[2] : (uint32_t)sg->tex.width, view ? view->source[3] : (uint32_t)sg->tex.height)) {
        static int n;
        if (diag_on && n++ < 3) diag_event("x360 post: not drawn (source %d, target %d, format %lu)", sg && sg->tex, out != nil, (unsigned long)out.pixelFormat);
        return -2;
    }
    id<MTLRenderPipelineState> composite=x_edr_processing?x_edr_pipeline(@"fs_vt",out.sampleCount,0):view ? x_view_pso(out.sampleCount) : (out.sampleCount>1 ? x_vt_ms(out.sampleCount):xp_vt_bgra);
    if (!composite) return -2;
    RenderTex *depth_rt = view ? surface_rt(view->depth_surface) : &ad_rt;
    id<MTLTexture> depth_tex = depth_rt ? depth_rt->tex : nil;
    if (view && (!x_rect_valid(view->source,sg->tex) || !x_rect_valid(view->destination,out) || !x_rect_valid(view->depth,depth_tex))) return -2;
    msaa_resolve(sg);
    if (depth_rt) msaa_resolve(depth_rt);                                                    // AA on: DOF reads the resolved scene depth
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
    // Keep the previous-frame value until this epoch has a scene measurement:
    // black loading samples cannot initialize the caller's exposure snap.
    // Thereafter the opt-in SP path shares split's completed-only selection.
    // Present bounds outstanding work to two frames: three slots leave a spare.
    uint64_t lum_wait_ns=0;
    if (!view && (!x_lum_nowait() || !xr->lum_ready)) {
        for (unsigned i=0;i<X_LUM_SLOTS;++i) {
            struct XLumSlot *slot=&xr->readback[i];
            if (slot->pending) {
                uint64_t w0=host_ticks();
                wait_serial(slot->serial);
                uint64_t waited=host_ticks()-w0;
                diag_idle_ns[IDLE_GPU]+=waited;
                if (diag_on) lum_wait_ns+=waited;
            }
        }
    }
    uint64_t completed = __atomic_load_n(&done_serial, __ATOMIC_ACQUIRE);
    struct XLumSlot *newest = NULL, *writable = NULL;
    for (unsigned i=0;i<X_LUM_SLOTS;++i) {
        struct XLumSlot *slot=&xr->readback[i];
        if (slot->pending && slot->serial < frame_serial && slot->serial <= completed) {
            if (slot->generation == xr->epoch && (!newest || slot->serial > newest->serial)) newest=slot;
        }
    }
    if (newest) {
        const uint32_t *t=newest->buffer.contents;
        double sum=0,sa=0,sm=0;
        if(newest->floating) {
            const __fp16 *f=newest->buffer.contents;
            for(int i=0;i<4096;++i){sum+=(float)f[4*i];sa+=(float)f[4*i+1];sm+=(float)f[4*i+2];}
            measured=(float)(sum/4096);xr->alpha=(float)(sa/4096);xr->masked=(float)(sm/4096);
        } else {
            for (int i=0;i<4096;++i) { sum+=t[i]&255;sa+=t[i]>>8&255;sm+=t[i]>>16&255; }
            measured=(float)(sum*9.536743e-07);
            xr->alpha=(float)(sa/(4096*255.0)); xr->masked=(float)(sm/(4096*255.0));
        }
        if (measured >= 0.002f) xr->lum_ready=1;
    }
    for (unsigned i=0;i<X_LUM_SLOTS;++i) {
        struct XLumSlot *slot=&xr->readback[i];
        if (slot->pending && slot->serial < frame_serial && slot->serial <= completed) slot->pending=0;
        if (!slot->pending && !writable) writable=slot;
    }
    x360_post_alpha=xr->alpha; x360_post_masked=xr->masked;
    // No completed sample returns -1; the caller retains its previous measured
    // luminance. Saturation skips a copy rather than overwriting pending data.
    if (diag_on && !view) {
        static FILE *trace;
        if (!trace) {
            char path[1024];snprintf(path,sizeof path,"%s/lumread.tsv",diag_dir);
            trace=fopen(path,"w");
            if (trace) fprintf(trace,"frame\tsample\tcompleted\tmeasured\texposure\twait_ns\twritable\n");
        }
        if (trace) fprintf(trace,"%llu\t%llu\t%llu\t%.9g\t%.9g\t%llu\t%d\n",
            (unsigned long long)frame_serial,(unsigned long long)(newest?newest->serial:0),
            (unsigned long long)completed,measured,p->exposure,(unsigned long long)lum_wait_ns,writable!=NULL);
    }
    end_pass();
    ensure_cmd();
    id<MTLTexture> scene=view ? x_crop(sg->tex,view->source,&xr->scene[frame_serial&1]) : sg->tex;
    if (!scene) return -2;
    extern void render_soft_smoke_late(id<MTLTexture> scene);
    if(!view)render_soft_smoke_late(scene);
    id<MTLTexture> original_scene=scene;
    x_contact_depth(depth_tex,p,view);
    scene=x_contact_scene(scene,depth_tex,p,view);
    if(!view)render_soft_smoke_late_finish(scene);
    if(x_contact_pair()&&xr->ao_ready)original_scene=xr->ao_original;
    // Each proof renders the complete chain on the same held input. The final
    // iteration restores ordinary On output; diagnostic work cannot set the look.
    int proof_pair=x_contact_pair()&&!view&&xr->ao_ready;
    id<MTLTexture> input_scene=original_scene,shaded_scene=scene,proof_color=nil,proof_resolved=nil;
    if (proof_pair) {
        MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:out.pixelFormat width:out.width height:out.height mipmapped:NO];
        d.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;d.storageMode=MTLStorageModePrivate;
        proof_resolved=[mtl newTextureWithDescriptor:d];
        if (out.sampleCount>1) { d.textureType=MTLTextureType2DMultisample;d.sampleCount=out.sampleCount; }
        proof_color=out.sampleCount>1?[mtl newTextureWithDescriptor:d]:proof_resolved;
        if (!proof_color||!proof_resolved) proof_pair=0;
    }
    for (int proof=proof_pair?0:4;proof<=4;++proof) {
    scene=proof==0?input_scene:proof==1?xr->ao_reference_scene:proof==2?xr->ao_cpu_scene:shaded_scene;
    uint32_t w = xw, h = xh;
    XK k;
    // 1. downscale 4x4 into the two quarter-res targets
    memset(&k, 0, sizeof k);
    for (int y = 0; y < 4; ++y) for (int x = 0; x < 4; ++x) { k.off[y * 4 + x][0] = (x - 1.5f) / (w / 4); k.off[y * 4 + x][1] = (y - 1.5f) / (h / 4); }
    k.p0[2] = (float)p->bloom_mask_pc; k.p0[3] = p->glare > 0 ? 1.0f : 0.0f;
    x_pass(xp_down, xt_q0, xt_q1, scene, xs_lin, &k, nil, HC_DOWN);
    // 2. 64x64 luminance, copied for the CPU (read next frame)
    memset(&k, 0, sizeof k);
    for (int i = -1, n = 0; i <= 1; ++i) for (int j = -1; j <= 1; ++j, ++n) { k.off[n][0] = i / 192.0f; k.off[n][1] = j / 192.0f; }
    x_pass(xp_lum, xt_lum, nil, xt_q0, xs_lin, &k, xt_q1, HC_LUM);
    if (writable) {
        unsigned pixel_bytes=x_edr_processing?8:4;
        if (!writable->buffer || writable->floating!=x_edr_processing) writable->buffer=[mtl newBufferWithLength:64*64*pixel_bytes options:MTLResourceStorageModeShared];
        if (writable->buffer) {
            id<MTLBlitCommandEncoder> b=gpt_blit(cmd,"blit x360 lum readback");
            [b copyFromTexture:xt_lum sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(64,64,1)
                toBuffer:writable->buffer destinationOffset:0 destinationBytesPerRow:64*pixel_bytes destinationBytesPerImage:4096*pixel_bytes];
            [b endEncoding]; writable->serial=frame_serial; writable->generation=xr->epoch; writable->pending=1;writable->floating=x_edr_processing;
        }
    }
    // 3. bright pass, 5x5 blur, blur-downsample to w/8, bloom horizontal then vertical
    memset(&k, 0, sizeof k);
    k.p0[0] = p->middle_gray; k.p0[1] = p->adapted_lum; k.p0[3] = p->bloom_thresh;
    int glare = p->glare > 0;                                                // natural: the downscale wrote the weighted source
    if (!glare) x_pass(xp_bright, xt_bright, nil, xt_q1, xs_lin, &k, nil, HC_BRIGHT);
    x_gauss5(w / 4, h / 4, &k);
    x_pass(xp_taps, xt_bblur, nil, glare ? xt_q1 : xt_bright, xs_lin, &k, nil, HC_BLUR);
    x_pass(xp_taps, xt_bloomA, nil, xt_bblur, xs_lin, &k, nil, HC_BLOOM_DOWN);
    for (int pass = 0; pass < (glare ? 2 : 1); ++pass) {                     // natural: twice, normalised (a wider, sqrt 2 x dev, glare)
        x_bloomk(w / 8, 1, &k, glare);
        x_pass(xp_taps, xt_bloomB, nil, xt_bloomA, xs_lin, &k, nil, pass ? HC_BLOOM_H2:HC_BLOOM_H);
        x_bloomk(h / 8, 0, &k, glare);
        x_pass(xp_taps, xt_bloomA, nil, xt_bloomB, xs_lin, &k, nil, pass ? HC_BLOOM_V2:HC_BLOOM_V);
    }
    id<MTLTexture> lamp = p->lamp_glow > 0 ? render_lamp_source_view(view ? view->source_view : 0) : nil;
    if (lamp && view) lamp=x_crop(lamp,view->lamp,&xr->lamp);
    if (lamp && lamp.width == scene.width && lamp.height == scene.height && lamp_post_init(w, h, out.sampleCount, view!=NULL)) {
        memset(&k, 0, sizeof k);
        for (int y = 0; y < 4; ++y) for (int x = 0; x < 4; ++x) {
            k.off[y * 4 + x][0] = (x - 1.5f) / (w / 4); k.off[y * 4 + x][1] = (y - 1.5f) / (h / 4);
        }
        k.p0[0] = p->lamp_glow; k.p0[1] = glare;
        k.p0[2] = glare ? p->glare : p->bloom_scale * x_bloom_gain();
        k.wgt[0][0] = p->middle_gray; k.wgt[0][1] = p->adapted_lum;
        k.wgt[0][2] = p->bloom_thresh; k.wgt[0][3] = p->bloom_mask_pc;
        x_tag = " lamp-source";
        x_pass(lp_source, lt_source, nil, scene, xs_lin, &k, lamp, HC_LAMP_SOURCE);
        x_gauss5(w / 4, h / 4, &k);
        x_tag = " lamp-blur5";
        x_pass(lp_taps, lt_blur, nil, lt_source, xs_lin, &k, nil, HC_LAMP_BLUR);
        x_tag = " lamp-bloom";
        x_pass(lp_taps, lt_a, nil, lt_blur, xs_lin, &k, nil, HC_LAMP_DOWN);
        for (int pass = 0; pass < (glare ? 2 : 1); ++pass) {
            x_bloomk(w / 8, 1, &k, 1); x_pass(lp_taps, lt_b, nil, lt_a, xs_lin, &k, nil, HC_LAMP_H);
            x_bloomk(h / 8, 0, &k, 1); x_pass(lp_taps, lt_a, nil, lt_b, xs_lin, &k, nil, HC_LAMP_V);
        }
        x_tag = "";
    } else lamp = nil;
    // 4. DOF half-res blur source
    if (p->dof) {
        memset(&k, 0, sizeof k);
        float tu = 1.0f / (w / 2), tv = 1.0f / (h / 2);
        k.off[0][0] = -tu; k.off[0][1] = -tv; k.off[0][2] = tu; k.off[0][3] = -tv;
        k.off[1][0] = -tu; k.off[1][1] = tv; k.off[1][2] = tu; k.off[1][3] = tv;
        x_pass(xp_dofdown, xt_dof, nil, scene, xs_lin, &k, nil, HC_DOF);
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
        id<MTLRenderPipelineState> curve=xp_tdown;
        if (post_cuts_curve((uint32_t)scene.width,(uint32_t)scene.height,view!=NULL)) {
            id<MTLRenderPipelineState> cut=post_cuts_curve_pipeline(mtl,xt_t.pixelFormat);
            if (cut) curve=cut;
        }
        x_pass(curve, xt_t, nil, xt_q0, xs_lin, &k, nil, HC_CURVE);
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
    struct { float coeffs[4][4], b[4], blurParams[4], dofp[4], misc[4], zr[4], tp[4], rx[4], depth_uv[4], depth_bounds[4]; } vk;
    memcpy(vk.coeffs, p->coeffs, sizeof vk.coeffs);
    memcpy(vk.b, p->blur, 16);
    vk.blurParams[0] = p->blur_params[0]; vk.blurParams[1] = p->blur_params[1]; vk.blurParams[2] = p->debug==5?1:(float)p->debug; vk.blurParams[3] = (float)((p->skip & 3) | (p->curve_glow == 2 ? 2 : 0));
    memcpy(vk.dofp, p->dof_params, 16);
    vk.dofp[3] = (float)(taps < 7 ? 7 : taps > 64 ? 64 : taps);
    vk.misc[0] = p->desaturation; vk.misc[1] = p->vignette; vk.misc[2] = p->exposure; vk.misc[3] = 1.0f;
    static int patches = -1;
    if (patches < 0) { const char *e = getenv("NFS_TEST_PATCHES"); patches = e && *e == '1'; }
    vk.tp[0] = patches ? 1.0f : 0.0f; vk.tp[1] = p->bloom_mask_pc == 1 ? 1.0f : 0.0f; vk.tp[2] = p->bloom_self * x_bloom_gain(); vk.tp[3] = 0;
    if(x_edr_processing)vk.tp[3]=render_hdr_composition_headroom();
    vk.rx[0] = p->glare; vk.rx[1] = p->rolloff ? 1.0f : 0.0f; vk.rx[2] = p->deband; vk.rx[3] = 16.0f * fmaxf(s, 1.0f) * (720.0f / 1080.0f);   // radius 16 px at 1080p
    vk.zr[0] = p->znear; vk.zr[1] = p->zfar; vk.zr[2] = p->dof ? 1.0f : 0.0f; vk.zr[3] = p->bloom_scale;
    vk.depth_uv[0]=vk.depth_uv[1]=0; vk.depth_uv[2]=vk.depth_uv[3]=1;
    vk.depth_bounds[0]=vk.depth_bounds[1]=0; vk.depth_bounds[2]=vk.depth_bounds[3]=1;
    if (view) for (int j=0;j<2;++j) {
        float size=j ? depth_tex.height : depth_tex.width;
        vk.depth_uv[j]=view->depth[j]/size; vk.depth_uv[j+2]=view->depth[j+2]/size;
        vk.depth_bounds[j]=(view->depth[j]+0.5f)/size;
        vk.depth_bounds[j+2]=(view->depth[j]+view->depth[j+2]-0.5f)/size;
    }
    MTLRenderPassDescriptor *rp = [MTLRenderPassDescriptor renderPassDescriptor];
    rp.colorAttachments[0].texture = proof<4?proof_color:out; rp.colorAttachments[0].level = proof<4?0:dl; rp.colorAttachments[0].slice = proof<4?0:dsl;
    rp.colorAttachments[0].loadAction = proof<4 || post_cuts_full_target(view!=NULL,(unsigned)dl,(unsigned)dsl,w,h,(uint32_t)out.width,(uint32_t)out.height)?MTLLoadActionDontCare:MTLLoadActionLoad;
    rp.colorAttachments[0].storeAction = MTLStoreActionStore;
    if (proof<4&&out.sampleCount>1) {
        rp.colorAttachments[0].resolveTexture=proof_resolved;
        rp.colorAttachments[0].storeAction=MTLStoreActionMultisampleResolve;
    }
    if (gpt_on) gpt_render_auto(rp, lamp?"x360 vt+lamp":"x360 vt");
    id<MTLRenderCommandEncoder> e = [cmd renderCommandEncoderWithDescriptor:rp];
    if (gpt_on) { gpt_name(e); gpt_phase = "after-vt"; }
    if (view) x_destination(e,view->destination);
    if(x_edr_processing && lamp)composite=x_edr_pipeline(@"fs_vt",out.sampleCount,1);
    [e setRenderPipelineState:x_edr_processing?composite:lamp ? lp_vt[view!=NULL][out.sampleCount] : composite];
    if (HDRCLIP_ACTIVE) hdrclip_bind(e,HC_FINAL);
    [e setFragmentTexture:scene atIndex:0];
    [e setFragmentTexture:tsrc atIndex:1];
    [e setFragmentTexture:vg && vg->tex ? vg->tex : xt_white atIndex:2];
    [e setFragmentTexture:xt_bloomA atIndex:3];
    [e setFragmentTexture:xt_dof atIndex:4];
    [e setFragmentTexture:depth_tex atIndex:5];
    [e setFragmentTexture:xt_bblur atIndex:6];
    if (lamp) [e setFragmentTexture:lt_a atIndex:7];
    [e setFragmentSamplerState:xs_lin atIndex:0];
    [e setFragmentSamplerState:xs_vig atIndex:1];
    [e setFragmentBytes:&vk length:sizeof vk atIndex:0];
    [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
    [e endEncoding];
    if(x_edr_processing && proof==4)render_hdr_scene_treated();
    if (HDRCLIP_ACTIVE && proof==4) {
        hdrclip_scan(out,HC_FINAL);
        if (out.sampleCount>1) { RenderTex *r=surface_rt(dst); msaa_resolve(r); hdrclip_scan(r->tex,HC_FINAL_RESOLVE); }
        else hdrclip_scan(out,HC_FINAL_RESOLVE);
        hdrclip_finish();
    }
    if (proof<4) x_contact_shot(proof_resolved,"final",proof==0?"off":proof==1?"reference":proof==2?"cpu_reference":"on");
    pass_dirty = 1;
    static int vig_dumped;                                                   // diag: UVESVIGNETTE once (x blur mask, y additive, z brightness, w speed-effect mask)
    if ((texdump_frame() || (diag_on && !vig_dumped++)) && vg && vg->tex) texdump(vignette, vg->tex);   // (src is still being rendered in this command buffer)
    } // complete-chain proof iterations
    return measured;
}

// ---------------- lens raindrops, X360 order and look (x360_post.m, reference-render candidate #4) ----------------
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
static int raindrops_prepare(NSUInteger samples, MTLPixelFormat format) {
    static id<MTLLibrary> lib;
    static NSUInteger pso_samples;
    static MTLPixelFormat pso_format;
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
    if (!drops_pso || pso_samples != samples || pso_format != format) {
        uint64_t t0 = host_ticks();
        MTLRenderPipelineDescriptor *pd = [MTLRenderPipelineDescriptor new];
        pd.vertexFunction = [lib newFunctionWithName:@"vs_drop"];
        pd.fragmentFunction = [lib newFunctionWithName:@"fs_drop"];
        MTLRenderPipelineColorAttachmentDescriptor *ca = pd.colorAttachments[0];
        ca.pixelFormat = format;
        ca.blendingEnabled = YES;
        ca.sourceRGBBlendFactor = ca.sourceAlphaBlendFactor = MTLBlendFactorSourceAlpha;
        ca.destinationRGBBlendFactor = ca.destinationAlphaBlendFactor = MTLBlendFactorOneMinusSourceAlpha;
        ca.writeMask = MTLColorWriteMaskRed | MTLColorWriteMaskGreen | MTLColorWriteMaskBlue;
        pd.rasterSampleCount = samples;
        NSError *err = nil;
        drops_pso = pipeline_build(pd, __func__, &err);
        pso_samples = samples;
        pso_format = format;
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
    static MTLPixelFormat warm_format;
    if (!mtl || getenv("NFS_NO_RAINWARM")) return;
    NSUInteger dl, dsl;
    id<MTLTexture> out = surface_target(dst, &dl, &dsl);
    if (!out || (out.pixelFormat != MTLPixelFormatBGRA8Unorm && out.pixelFormat != MTLPixelFormatRGBA16Float) || (warm_samples == out.sampleCount && warm_format == out.pixelFormat)) return;
    Obj *o0 = obj(disp_tex), *o1 = obj(alpha_tex);
    uint64_t t0 = host_ticks();
    if (o0 && o1) { gpu_tex(o0); gpu_tex(o1); }
    if (diag_on) diag_event("render: raindrops textures %.1f ms", (host_ticks() - t0) / 1e6);
    if (raindrops_prepare(out.sampleCount,out.pixelFormat)) { render_ripple_warm(); warm_samples = out.sampleCount; warm_format = out.pixelFormat; }
}
int render_x360_raindrops(uint32_t dst, const RainDrop *d, int n, uint32_t disp_tex, uint32_t alpha_tex, int signature) {
    return render_x360_raindrops_view(dst,d,n,disp_tex,alpha_tex,signature,NULL);
}
int render_x360_raindrops_view(uint32_t dst, const RainDrop *d, int n, uint32_t disp_tex, uint32_t alpha_tex, int signature, const X360PostView *view) {
    if (!mtl || n <= 0) return 0;
    Obj *o0 = obj(disp_tex), *o1 = obj(alpha_tex);
    RenderTex *g0 = o0 ? gpu_tex(o0) : NULL, *g1 = o1 ? gpu_tex(o1) : NULL;
    RenderTex *r = surface_rt(dst);
    // PC split composites into a non-MSAA texture level. surface_rt deliberately
    // exposes only standalone/MSAA surfaces; sample this level's parent instead.
    if (!r && view) {
        Obj *surface=obj(dst);
        if (surface && surface->parent && !surface->level && !surface->face)
            r=gpu_tex(obj(surface->parent));
    }
    NSUInteger dl, dsl;
    id<MTLTexture> out = surface_target(dst, &dl, &dsl);
    if (!g0 || !g0->tex || !g1 || !g1->tex || !r || !r->tex || !out || (out.pixelFormat != MTLPixelFormatBGRA8Unorm && out.pixelFormat != MTLPixelFormatRGBA16Float)) return 0;
    xr=&x_resources[view ? (view->source_view==0x009196C0u ? 2:1):0];
    if (!raindrops_prepare(out.sampleCount,out.pixelFormat)) return 0;
    id<MTLRenderPipelineState> pso = drops_pso;
    // the source: the composited frame (resolved when multisampled; else a copy, since the pass draws into it)
    id<MTLTexture> src = r->tex;
    if (r->ms) msaa_resolve(r);
    end_pass();
    ensure_cmd();
    uint32_t full[4]={0,0,(uint32_t)src.width,(uint32_t)src.height};
    if (view || !r->ms) {
        src=x_crop(src,view ? view->destination:full,&xr->drop);
        if (!src) return 0;
    }
    struct { float rect[4], lc[4], ba[4]; } q[64];
    if (n > 64) n = 64;
    float aspect = view ? (float)view->destination[2]/view->destination[3] : (float)out.width/(float)out.height;
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
    if (view) x_destination(e,view->destination);
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
