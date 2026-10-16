// d3d9_msl.h - Direct3D 9 shaders as Metal Shading Language source.
//
// A decoded program (d3d9_shader.h) becomes one MSL function, `vs_main` or
// `ps_main`, in a source string of its own. Every vertex shader writes the same
// interpolant struct and every pixel shader reads it, so any vertex shader
// links with any pixel shader, as in Direct3D.
//
// Resource slots the generated functions use:
//
//   vertex   [[stage_in]] attribute(i)  the dcl'd input register v<i>, as float4
//            buffer(16)                 float4 constants c0..
//            buffer(17)                 D9VSParams
//   fragment buffer(0)                  float4 constants c0..
//            buffer(1)                  D9PSParams
//            texture(i), sampler(i)     sampler stage i
//
// The host fills both parameter blocks with the layouts below; they are part
// of the generated source's contract.
#pragma once
#include "d3d9_shader.h"

#include <cstdint>
#include <string>
#include <vector>

struct D9VSParams {
    float halfpix[4];     // added to xy, times w: D3D's half-pixel offset in clip space
    float ascale[16][4];  // per input register: undoes Metal's normalization of integer formats
    int32_t bconst[4][4]; // packed Boolean registers, matching uniform-buffer alignment
};
struct D9PSParams {
    float alpha_ref;    // 0..1
    int32_t alpha_func; // D3DCMPFUNC; 8 (always) means no test
    int32_t fog_mode;   // 0 off, 1 vertex factor, 2 linear, 3 exp, 4 exp2
    int32_t pad0;       // Remaster A3 step A: MSAA sample count for alpha-to-coverage variants
    float fog_color[4];
    float fog_start, fog_end, fog_density, pad1;
    int32_t bconst[4][4];
    float fade[4];      // Remaster run 27 scenery fade-in (D-153): [0] = dither threshold 0..1, 0 = off; read only by
                        // shaders patched at compile time (d3d9_metal.mm d9_fade_patch), others ignore the extra bytes
};
// Remaster run 14 lit smoke (PixelVariant.smoke): the draw's fragment buffer(5). World space (z up, metres). The host
// fills it from the core mod's per-draw marker and the kind table (host/gpu/metal/smoke_light.h, which also holds the
// C++ twin of the lighting maths).
struct D9SmokeParams {
    float sun[4];   // xyz: unit direction toward the sun; w: sun strength 0..1 (height x clear sky; 0 = unchanged)
    float eye[4];   // xyz: the camera (from the particle draw's own clip rows); w: 0
    float k[4];     // relight strength s, Henyey-Greenstein g, albedo, alpha self-shadow k_ss
    float k2[4];    // normal tilt by the alpha gradient, transmission power p, forward-glow weight, 0
    float amb[4];   // ambient level, the side-lit reference term, shade tint (fog), steam edge erosion
    float light[4]; // flare: xyz the flare (world), w intensity x flicker (0 = none)
    float lcol[4];  // flare: rgb light colour, w radius (m)
    float nearf[4]; // near-camera fade start (m), 1 / its range (0 = none), debug view (0 off, 1 ratio, 2 kind), 0
};

namespace d9msl {

// What a pixel shader cannot say about its own samplers: for shader model
// 1.x the texture kinds and projection, for every model the stages bound to a
// depth texture. Direct3D 9 drivers sample a depth texture as a shadow map:
// the lookup compares the coordinate's z with the stored depth (LESSEQUAL,
// filtered) and returns the result in r, g and b.
struct PixelVariant {
    uint16_t cube_mask = 0;      // stages whose bound texture is a cube map
    uint16_t projected_mask = 0; // stages with D3DTTFF_PROJECTED
    uint16_t depth_mask = 0;     // stages whose bound texture holds depth
    uint16_t alpha_mask = 0;     // stages stored as red that sample as alpha (WGSL only)
    // Remaster A3 step A: an alpha-tested, unblended draw into an MSAA target turns its alpha test into coverage
    // (MSL only): the sample count, or 0 for the ordinary discard.
    uint8_t a2c = 0;
    // Remaster S3 soft-particles trial (MSL only): 1 = fade the alpha where the particle nears the scene depth
    // (texture 16, single-sample R32F copy; P.pad1 = 1 / fade distance in view units).
    uint8_t soft = 0;
    // Remaster R2 wet roads (MSL only): 1 = replace the road shader's reflection sample, wetness mask and reflection
    // add (the three translated lines of the PC road effect) with the native wet-road shading; unchanged if absent.
    // 2 (Xbox 360 mode, run 13) = the same plus the 360 rain-splash layer (wetroad_x360_patch; pixel_source fails if
    // it does not apply).
    uint8_t wetroad = 0;
    // Remaster R2 softer sun shadows (MSL only): 1 = every shadow-map lookup is an 8-tap Poisson filter of
    // hardware 2x2 compares (radius ~2.6 texels) instead of one compare; unchanged source when 0.
    uint8_t shadowpcf = 0;
    // Remaster run 10 fused pool shader (MSL only): add the clean pool light of the effect's clean_pools pass to the
    // output, from the pool varyings and the pool block in buffer(2): 1 = the normal-mapped form (PS_PoolsReflect:
    // WORLDNORMALMAP props, stages 0 and 1), 2 = the diffuse-only form (PS_Pools: WORLD streets, stage 0). Pair it with
    // the same VertexVariant pool value. Unchanged source when 0.
    uint8_t pool = 0;
    // Remaster run 12 PBR step 2 pilot (MSL only; review/run12/patch-plan.md): 1 = line D (base x sun) is scaled by
    // the normal-mapped sun ratio k, with the slot normal map at texture 17 (sampled with sm0 at t[0]) and the
    // key's packed manifest values in P.pad1 (unused since the per-pixel tilt cosine keeps the mean). Pair it with VertexVariant pbr (normal, eye-relative position and
    // light direction in t4..t6). Unchanged source when 0; pixel_source fails if the patch does not apply.
    uint8_t pbrworld = 0;
    // Remaster run 13 road sheen (MSL only; review/run13/road-sheen-design.md): 1 = the road effect's sun highlight
    // (line S) becomes the measured GGX sheen (pixel_is_roadsheen programs). Unchanged source when 0; pixel_source
    // fails if the patch does not apply. R3 run 18 material sets: 2 = the same with the dry roughness from the ORM map
    // at texture 19 (G = perceptual roughness r, alpha = r^2). R3 run 22: 3 = mode 2 + cavity (ORM R as the sheen's specular
    // occlusion, faded out when wet) + micro normals (tx0's luminance gradient at its own mip tilts the map normal;
    // the specular AA keeps the un-tilted one); refused when the program has no plain tx0 lookup at t0.
    uint8_t roadsheen = 0;
    // R3 run 29 facade glint (MSL only; review/run29/c-facade-design.md): 1 = a GGX sun highlight is added to the WORLD
    // program's sun term before line C (so the shadow and the brightness constant apply to it as to the sun's diffuse):
    // roughness = the ORM map's G and cavity its R (texture 19, sampled with sm0 at t[0]), F0 = mix(0.04, albedo,
    // ORM B), the vertex normal tilted by tx0's luminance gradient (micro normals) in the cotangent frame of t5 and the
    // uv, sun colour x N.L = the per-vertex d1 (iv[1]). Pair it with VertexVariant pbr. Unchanged source when 0;
    // pixel_source fails if the patch does not apply.
    uint8_t facadeglint = 0;
    // R3 run 29 (D-167, review/run29/opt-design.md O1): the scenery fade-in's ordered-dither discard (run 27) as a
    // variant: only draws whose instance is fading (private render state 243) get it, so every other draw keeps early
    // depth on Apple GPUs (the run-27 global patch put a discard in every pixel shader). The text is run 27's. Applied
    // last (after mipbias); a program without the anchors stays unpatched (as before).
    uint8_t fade = 0;
    // Remaster run 13 natural glass (MSL only): 1 = the building-glass effect's reflection add (reflection x mask +
    // lit colour) becomes Schlick-weighted per pixel (N from t5, V from t6: pair it with VertexVariant glass). Unchanged
    // source when 0; pixel_source fails if the patch does not apply. 2 (Xbox 360 mode, glass_patch_x360) = the 360 window
    // formula per pixel: F = sat(0.2 + 0.4 (1 - N.V)) on the reflection add, the per-vertex sun t2 replaced by
    // pow(R.V, 100) (net x2 after the program's doublings; L from t7: pair it with VertexVariant glass 2), and the output
    // alpha = 1 - sun x mask (the 360 bloom tag).
    uint8_t glass = 0;
    // Remaster run 13 natural water (MSL only; review/run13/x360-study-design.md 3(c)): 1 (Definitive) or 2 (Xbox 360:
    // tint x light, reflectance 0.2 + 0.4 x refl, sun pow 30, no Fresnel) = the WORLD lit colour
    // (pixel_is_water programs) becomes water: procedural ripple normal, Schlick F0 0.02 sky
    // reflection, GGX sun glint (time in P.pad1; pair it with VertexVariant pbr). Unchanged source when 0;
    // pixel_source fails if the patch does not apply.
    uint8_t water = 0;
    // Remaster run 13 front-end floor shadows (MSL only; review/run13/fe-shadows-design.md): the colour before the alpha
    // test is darkened by the front-end shadow map (depth texture 18, d9_shadow with the same compare/filter as the
    // receivers) at the projected coordinate vin.t7 (pair it with VertexVariant fefloor): 1 = rgb x (1 - K (1 - lit)),
    // K in P.pad1; 2 (TEST ONLY) = the same lookup, colour unchanged (must be neutral); 3 (TEST ONLY) = coordinate paint
    // (green inside the map, darker where shadowed; red outside). Outside the map: lit. Unchanged source when 0.
    uint8_t fefloor = 0;
    // Remaster run 13 Xbox 360 car paint (MSL only; RECOMP_REMASTER_CARENV=2, carpaint_patch): 1 = the car effect's
    // pixel shader (pixel_is_carpaint) gets the 360 paint layers the PC program lacks: a metallic flake normal (own
    // trilinear hash noise at the object position t6.xyz, falloff sat(1.2 - 0.25 d) x 0.04, d = t6.w), a hot spot
    // pow(R.V, 80) on top of the program's pow(R.V, 16) highlight (under its shadow), and an environment bloom
    // env + mask x env x 2. Pair it with VertexVariant carenv 2. Unchanged source when 0.
    uint8_t carpaint = 0;
    // Remaster run 13 wall parallax (MSL only; RECOMP_REMASTER_PARALLAX=1, parallax_patch): 1 = the WORLDNORMALMAP
    // program's t[0] samples read at uv + V.xy x (normal-map alpha x 2 x 0.075 - 0.0375). Unchanged source when 0;
    // pixel_source fails if the patch does not apply.
    uint8_t parallax = 0;
    // Remaster run 13 Xbox 360 sky, our own version (review/run13/x360-shader-map.md SKY): 1 = the PC sky pass 0
    // combine (pixel_is_sky programs) becomes the 360 order: in-scatter base, whispy layer lerp, cap lerp, x 2 x 0.5 x
    // vertex colour. Unchanged source when 0; pixel_source fails if the patch does not apply.
    uint8_t sky = 0;
    // R3 run 17 Xbox 360 lens drops, our own version (MSL only; D-124, review/r3-design/visible-track.md B): 1 = the PC
    // on-screen drop (pixel_is_raindrop programs) keeps the game's refraction offset and fade but draws a soft lens: one
    // of four computed shapes per drop, a bright core and a darker rim, the refracted scene shaded, no x1.2 and no hard
    // mask. Unchanged source when 0; pixel_source fails if the six PC lines are not found.
    uint8_t raindrop = 0;
    // PLAN_R2 item 10 real HDR (MSL only; review/run14/hdr-design.md s2.2): the draw goes to the float back buffer, so
    // the output is clamped as Direct3D 9 clamps it for its 8-bit target before blending: alpha to [0,1], rgb to
    // [0, ceiling], 1 = ceiling 1.0 (opaque world, decals, HUD: overbright art clips as on PC), 2 = ceiling 64 (additive
    // light, sky, car paint, glass, road: light keeps its excess). Applied last (hdr_epilogue). Unchanged source when 0.
    uint8_t hdr = 0;
    // Remaster run 14 lit smoke (MSL only; review/run14/smoke-realism-design.md 4 stage 1 and 7): 1..7 = the particle
    // kind the core mod marked on this emitter's draw (smoke_light.h kinds): the colour is relit before the alpha test
    // and fog (sprite sphere normal tilted by the stage-0 alpha gradient, sun half-Lambert with alpha self-shadow,
    // (1 - alpha) transmission x Henyey-Greenstein forward glow, relative to the side-lit reference so the game's colour
    // and grade stay the base); fog (5) is flat-lit with a grey-blue shade tint, flares (2, 7) add their inner light.
    // Reads the world position in t7 (pair it with VertexVariant smoke), stage 0 at t[0] and D9SmokeParams at buffer(5).
    // Unchanged source when 0; pixel_source fails if stage 0 is not a plain 2D sampler.
    uint8_t smoke = 0;
    // Run 15 Wave B WB1b surface attachments (MSL only; host/gpu/metal/surface.h surf_ps_patch, applied last): surf
    // 1 = also write S0 [[color(4)]] / S1 [[color(5)]] with the depth-derivative normal, 4..7 = with the world normal
    // in t<surf> (pair it with VertexVariant surf = the same slot); reactive 1..3 = also write S2 [[color(6)]] (1 alpha,
    // 2 max rgb, 3 max rgb x alpha). Values per draw at buffer(6) (D9SurfParams). Unchanged source when both are 0.
    uint8_t surf = 0;
    uint8_t reactive = 0;
    // Run 16 smokeperf (MSL only): tyre smoke drawn with fewer particles keeps its density: the output alpha becomes
    // 1 - (1 - a)^(alphak / 16) (n layers of alpha a cover as n / k layers of that alpha), before the soft fade. The core
    // mod marks the draw (private render state 240 = k x 16, smoke.h). Unchanged source when 0.
    uint8_t alphak = 0;
    // R3 run 22 texture mip bias under upscaling (MSL only; TEST RECOMP_REMASTER_UPSCALE_MIPBIAS): every plain
    // tx<n>.sample(sm<n>, uv) of the program gets bias(b) (an existing bias(x) becomes bias(x + b), a texel offset
    // gets bias(b) before it; level() and gradient lookups are left alone), b = mipbias_value(code), applied after every other patch so their text
    // matches stay as they are. The draw side sets it only on draws into the jittered scene backing while upscaling.
    // Unchanged source when 0.
    uint8_t mipbias = 0;
    // UG1 run 6 Xbox-style fog (MSL only; review/run06/research-fog.md, our formula with the Xbox values): 1 = the
    // generic D3D fog block becomes depth x screen-height fog: f = clamp((d - start) x inv, 0, max) x
    // sat(1 - |y_ndc| x falloff), d = 1 / pos.w (view depth), y_ndc = vin.fog x pos.w (clip y from the VertexVariant
    // xfog), col.rgb = mix(col.rgb, colour, f), alpha kept. P.fog_start = start, P.fog_end = inv = 1 / (end - start),
    // P.fog_density = falloff, P.fog_color = (rgb, max). Pair it with VertexVariant xfog. Unchanged source when 0.
    uint8_t xfog = 0;
    // UG1 run 6 Xbox headlight distance fade (MSL only; review/run06/research-headlight.md, headlight_patch): the
    // headlight clip sample t[2] of the world-headlight programs (pixel_is_headlight) is faded with the depth along the
    // beam (tc[1].w, metres): 1 = t[2] x sat(1 - w / R) on top of the PC clip ramp; 2 = the Xbox profile, the PC near
    // cut x sat(1 - w / R) and the Xbox headlight colour ratio. R = hlfade_range() metres. Unchanged source when 0;
    // pixel_source fails if the patch does not apply.
    uint8_t hlfade = 0;
    uint64_t key() const {
        return (xfog ? 0x2f6b8c1e5d3a9047ull * xfog : 0) ^ (hlfade ? 0x6c8e9cf570932bd5ull * hlfade : 0) ^
               (mipbias ? 0x6f4a3c2b1d0e9f87ull * mipbias : 0) ^ (raindrop ? 0x5851f42d4c957f2dull * raindrop : 0) ^ (alphak ? 0x3b9aca07d1b54a33ull * alphak : 0) ^ (surf ? 0x7f4a7c159e3779b9ull * surf : 0) ^ (reactive ? 0x2c1b3c6d58f1a3e5ull * reactive : 0) ^
               (smoke ? 0x4cf5ad432745937full * smoke : 0) ^ (hdr ? 0x7c4a7d9f3e1b2c55ull * hdr : 0) ^((uint64_t)sky * 0x3956c25bb9b0e2d3ull) ^ ((uint64_t)parallax * 0x2545f4914f6cdd1dull) ^ ((uint64_t)carpaint * 0x8cb92ba72f3d8dd7ull) ^ ((uint64_t)alpha_mask << 48 | (uint64_t)depth_mask << 32 | (uint64_t)cube_mask << 16 |
                projected_mask) ^
               ((uint64_t)a2c * 0x9e3779b97f4a7c15ull) ^ ((uint64_t)soft * 0xc2b2ae3d27d4eb4full) ^
               ((uint64_t)wetroad * 0x165667b19e3779f9ull) ^ ((uint64_t)shadowpcf * 0x27d4eb2f165667c5ull) ^
               ((uint64_t)pool * 0x85ebca77c2b2ae63ull) ^
               ((uint64_t)pbrworld * 0xd6e8feb86659fd93ull) ^ ((uint64_t)roadsheen * 0xa0761d6478bd642full) ^
               ((uint64_t)facadeglint * 0x9fb21c651e98df25ull) ^ ((uint64_t)fade * 0x7a5b9c3e2d1f4e6bull) ^ ((uint64_t)glass * 0xe7037ed1a0b428dbull) ^
               ((uint64_t)water * 0x1d8e4e27c47d124full) ^ ((uint64_t)fefloor * 0x510e527fade682d1ull);
    }
};
// Remaster run 10: also write the pool varyings (object-space position, normal, uv, tangent: the raw POSITION0,
// NORMAL0, TEXCOORD0 and, for pool 1, TANGENT0 inputs, which the program must declare). Unchanged source when 0.
struct VertexVariant {
    uint8_t pool = 0;
    // Remaster run 12: also write t4 = the NORMAL input, t5 = POSITION - eye (c[E]), t6 = the sun direction c[K]
    // (vertex_sun_flow finds N, P, E and K; vertex_source fails if it cannot). Unchanged source when 0.
    uint8_t pbr = 0;
    // Remaster run 13 car reflections: 1 = the car effect's environment strength follows Schlick's curve between the
    // material's face-on value and its grazing limit (vertex_is_carenv programs). Unchanged source when 0.
    // 2 (Xbox 360 mode) = the program's own environment strength (the 360 formula) and t6 = (POSITION, |eye - POSITION|)
    // for the car paint flake (car_vs_flow); vertex_source fails if it cannot.
    uint8_t carenv = 0;
    // Remaster run 13 natural glass: also write t5 = the NORMAL input and t6 = eye constant - POSITION (glass_vs_flow);
    // 2 (Xbox 360 mode) also writes t7 = the sun direction c[light] (glass_vs_flow light >= 0).
    uint8_t glass = 0;
    // Remaster run 13 front-end floor shadows: also write t7 = Mclip x the D3D clip position (before the half-pixel
    // fixup), Mclip = 4 rows at vertex buffer(18): the shadow-map coordinate. vertex_source fails if t7 is written.
    uint8_t fefloor = 0;
    // Remaster run 13 Xbox 360 sky (ours): 1 = the sky in-scatter uses the 360 horizon distance (vertex_is_sky).
    uint8_t sky = 0;
    // Remaster run 14 lit smoke: 1 = also write t7 = the raw POSITION0 input (particle quads are built in world space,
    // 006c1160). vertex_source fails if the body writes t7 or POSITION0 is not declared.
    uint8_t smoke = 0;
    // Run 15 Wave B WB1b surface attachments: 4..7 = also write t<surf> = the NORMAL0 input x the object -> world rows
    // at vertex buffer(20) (three float4, surface.h normal_rows). vertex_source fails if t<surf> is written or there is
    // no NORMAL0 input. Unchanged source when 0.
    uint8_t surf = 0;
    // UG1 run 6 Xbox-style fog: 1 = the fog interpolant carries the clip-space y (o.fog = opos.y) for PixelVariant
    // xfog. vertex_source fails if the program writes oFog. Unchanged source when 0.
    uint8_t xfog = 0;
    uint64_t key() const {
        return (xfog ? 0x4d2c6dfc5ac42aedull : 0) ^ (surf ? 0x3a8f05c5d1b2e4a7ull * surf : 0) ^ (smoke ? 0x8f1bbcdcbfa53e0bull : 0) ^ (sky ? 0x59f111f1923f82a4ull : 0) ^(pool ? 0x6a09e667f3bcc908ull + pool : 0) ^ (pbr ? 0xbb67ae8584caa73bull : 0) ^
               (carenv ? 0x3c6ef372fe94f82bull : 0) ^ (carenv == 2 ? 0x5be0cd19137e2179ull : 0) ^ (glass ? 0xa54ff53a5f1d36f1ull : 0) ^ (glass == 2 ? 0x1f83d9abfb41bd6bull : 0) ^
               (fefloor ? 0x9b05688c2b3e6c1full : 0);
    }
};

// False, with the reason, for a program the translator does not handle.
bool vertex_source(const d9sh::Program &p, std::string *out, std::string *why);
bool vertex_source(const d9sh::Program &p, const VertexVariant &v, std::string *out, std::string *why);
// Run 15 Wave B WB1b: the varying slot (7 down to 4) a surface normal can use for this program pair with these other
// variants (not written by the vertex program or variant, not read by the pixel program or variant), or -1.
int surf_slot(const d9sh::Program &vs, const d9sh::Program &ps, const VertexVariant &vv, const PixelVariant &pv);
// Remaster R2: true when the default translation of `p` contains the road effect's reflection lines (wet roads).
bool pixel_is_wetroad(const d9sh::Program &p);
// UG1 run 6: true when the default translation of `p` is a world-headlight pixel program (ps 1.x: one t[1] and one
// t[2] sample, and t[1] x t[2] used later), the lines the headlight fade patch needs (headlight_find).
bool pixel_is_headlight(const d9sh::Program &p);
// UG1 run 6: the headlight fade range in metres, RECOMP_UG1_HEADLIGHT_FADE_M (default 100, the Xbox value; 20..400).
float hlfade_range();
// Remaster run 13 front-end floor shadows: true when the default translation of vertex shader `p` writes the clip
// position as four dot products of one input with c[clip..clip+3] and exactly one texture coordinate as four dot
// products of the same input with c[shadow..shadow+3] (the WORLD receivers: WVP and the shadow matrix W x S).
bool vertex_clip_shadow(const d9sh::Program &p, uint32_t *clip, uint32_t *shadow);
// Remaster run 14 foundation F1: true when the default translation of vertex shader `p` writes the clip position as four
// dot products of one input with c[clip..clip+3] (the generic half of vertex_clip_shadow; the camera VP probe).
bool vertex_clip_regs(const d9sh::Program &p, uint32_t *clip);
// Remaster run 13: true when the default translation of `p` is a road program (the wet-road lines) whose sun highlight
// lines the road sheen patch replaces (roadsheen_lines).
bool pixel_is_roadsheen(const d9sh::Program &p);
// Remaster run 13 natural water: true when the default translation of `p` is one of the two WORLD water forms (ps 1.x
// 2 x base x (d0 + d1), or ps 2.0 base x (d1 x shadow + d0) then brightness) with the in-scatter add and no t4..t7
// reads, the lines the water patch needs (water_find).
bool pixel_is_water(const d9sh::Program &p);
// Remaster run 13: true when the default translation of `p` (a vertex shader) has the car effect's environment
// strength lines (lit(N.V, -, EnvmapPower) x EnvmapRange + EnvmapMin) that the car reflection patch replaces.
bool vertex_is_carenv(const d9sh::Program &p);
// Remaster run 13 Xbox 360 car paint: the POSITION input and the eye constant of the one c[eye] - POSITION term, with
// no t6 write (VertexVariant carenv 2); and the car pixel shaders the carpaint patch applies to (carpaint_find).
bool car_vs_flow(const d9sh::Program &p, uint32_t *position, uint32_t *eye);
bool pixel_is_carpaint(const d9sh::Program &p);
// Remaster run 13 wall parallax: the sampler stage of the normal map in a parallax-capable pixel shader, or -1.
int pixel_parallax_stage(const d9sh::Program &p);
// Remaster run 13 natural glass: the vertex inputs/constant the glass VertexVariant writes from (one NORMAL input, one
// eye term c[eye] - POSITION; no t5/t6 writes), and the pixel shaders whose reflection add the glass patch replaces.
struct GlassFlow {
    bool ok = false;
    uint32_t normal = 0, position = 0, eye = 0;
    // Xbox 360 glass mode: the sun constant K (one c[K] that is both dotted with the NORMAL input and subtracted in a MAD
    // with it: the program's own reflection vector 2 (N.L) N - L), and no t7 write; -1 if absent.
    int light = -1;
};
GlassFlow glass_vs_flow(const d9sh::Program &p);
bool pixel_is_glass(const d9sh::Program &p);
// Remaster run 16 glass pop: the ps_1_x building-glass program (no shadow; reflection base.a x tx1 added) that
// PixelVariant glass also patches (glass1_patch, both modes). Not part of pixel_is_glass (HDR family unchanged).
bool pixel_is_glass1(const d9sh::Program &p);
// Remaster run 13 Xbox 360 sky (ours): true when the default translation of `p` is the PC sky pass 0 combine (three
// stage reads, the in-scatter t3, the cloud selector) that sky_ps_patch rewrites, or (vertex) the sky in-scatter whose
// distance sky_vs_patch replaces.
bool pixel_is_sky(const d9sh::Program &p);
bool vertex_is_sky(const d9sh::Program &p);
// R3 run 17 Xbox 360 lens drops (ours): true when the default translation of `p` is the PC on-screen drop (the
// displacement map at tx1 from t0, the two refraction MADs on c0, the fade w x c1.x, the scene at tx0, x c2.x) that
// raindrop_patch rewrites. raindrop_patch is exposed for offline tests.
bool pixel_is_raindrop(const d9sh::Program &p);
bool raindrop_patch(std::string *s);
// R3 run 22 (TEST): the PixelVariant mipbias code of a bias b (clamped to [-2, 2), 1/64 steps; 0 = off) and back.
// mipbias_patch adds b to every plain tx<n>.sample of an MSL source (see PixelVariant::mipbias); false only when a
// lookup's parentheses do not close (the source is left as it was). *count (optional) = the lookups biased. Exposed for
// offline tests.
inline uint8_t mipbias_code(float b) {
    const int c = 128 + (int)(b * 64.0f + (b < 0.0f ? -0.5f : 0.5f));
    return (uint8_t)(c < 1 ? 1 : c > 255 ? 255 : c);
}
inline float mipbias_value(uint8_t c) { return c ? ((int)c - 128) / 64.0f : 0.0f; }
bool mipbias_patch(std::string *s, uint8_t code, uint32_t *count = nullptr);
// Remaster run 12 (PBR step 2, challenge 1 and 9): true when the default translation of `p` is a WORLD lighting pixel
// shader: line D (base x d1) and line C (sun x shadow + base x d0) exactly once, the brightness-plus-in-scatter line
// after C, a stage-1 lookup (the shadow map: the host also requires a depth texture there), and no read of t[4..6]
// after the prelude.
bool pixel_is_pbrworld(const d9sh::Program &p);
// Remaster run 12 (challenge 1): the data flow of a vertex shader's colour outputs. ok = oD1 depends on a dot product of
// a constant with the NORMAL input and not on the COLOR input, and oD0 depends on COLOR (the sun in d1, the baked
// radiosity in d0; a shader with them swapped is not ok). consts: the constant registers oD1 reads.
struct SunFlow {
    bool ok = false;
    std::string why;
    std::vector<uint32_t> consts;
    // Run 12 pilot (ok is also required): pbr_ok = the dot reads the raw NORMAL input vv[normal] with c[light]; an ADD
    // of -POSITION (vv[position]) and c[eye] exists; the body writes no t4..t6. Otherwise pbr_why says what is missing.
    bool pbr_ok = false;
    std::string pbr_why;
    uint32_t normal = 0, position = 0, eye = 0, light = 0;
};
SunFlow vertex_sun_flow(const d9sh::Program &p);
// The pilot's debug counters (RECOMP_REMASTER_PBRWORLD_DEBUG, buffer(3)), uint each:
// [0,16) k histogram (sunfacing; 16 bins over 0..2), [16,32) dY/Y histogram of the final colour (sunfacing; 16 bins
// over -0.4..0.4, clamped), 32 sunfacing count, 33/34 dY/Y positive/negative sums x256 (sunfacing), 35 k sum x64
// (sunfacing), 36/37 frame sign agrees/disagrees with the direct Jacobian solve, 38 degenerate frame (ok false),
// 39 every sampled fragment, [40,44) ramp table (expected brighter & k>1, expected brighter & k<1, expected darker &
// k>1, expected darker & k<1; sunfacing, dead band |cos| < 0.05), 44 sampled count for 45/46, 45/46 dY/Y
// positive/negative sums x256 over every sampled pilot fragment (lit or not).
constexpr uint32_t kPbrWorldCounters = 48;
bool pixel_source(const d9sh::Program &p, const PixelVariant &v, std::string *out,
                  std::string *why);

// The stages a pixel shader samples, for the host's texture binding.
uint32_t pixel_sampler_mask(const d9sh::Program &p);
// Which colour outputs a 2.0 pixel shader writes (bit i: oC<i>).
uint32_t pixel_color_outputs(const d9sh::Program &p);

} // namespace d9msl
