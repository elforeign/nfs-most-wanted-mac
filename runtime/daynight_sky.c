// Optional sky replacement; original geometry, scattering and cloud motion survive.
#include "daynight_sky.h"
#include "daynight.h"
#include "host.h"
#include <math.h>
#include <stdlib.h>
#include <strings.h>

static int enabled;
static int sun_context;
static unsigned sun_draws, sun_polys;
static int moon_flare_context;
static unsigned moon_flare_draws[32], moon_flare_polys[32];

__attribute__((constructor)) static void sky_configure(void) {
    const char *e = getenv("NFS_DAYNIGHT");
    enabled = e && (!strcmp(e, "1") || !strcasecmp(e, "on"));
}

// J2 exposes the sampled orbit in the object, but no clock accessor. The phase
// identifies daylight; at its endpoints the opposing light's positive Z gives
// the distance below the horizon. Only the first hour matters to the blend.
float daynight_sky_factor(float phase, float light_z, float tilt) {
    if (!isfinite(phase) || !isfinite(light_z) || !isfinite(tilt)) return 0;
    double hour;
    if (phase > .05f && phase < .95f) hour = 6 + 12 * (phase - .05) / .9;
    else {
        float height = fmaxf(.001f, fabsf(cosf(tilt)));
        double delta = asin(fminf(1, fmaxf(0, light_z / height))) * (12 / 3.14159265358979323846);
        hour = phase <= .05f ? 6 - delta : 18 + delta;
    }
    DaynightSample s;
    daynight_sample(hour, 0, tilt, &s);
    return s.night;
}

static float sky_night(void) {
    if (!enabled || M32(0x00925E90u) != 6) return 0;
    uint32_t tod = M32(0x009B392Cu);
    if (!tod) return 0;
    return daynight_sky_factor(MF32(tod + 8), MF32(tod + 0x188), MF32(tod + 0x10));
}

int daynight_sky_texture(uint32_t hash) { return hash == 0xbc6f56d9u; }

// This producer queues only the two type-21 solar sprites. The night dome
// supplies the moon's disc and glow; lamps and headlights use other producers.
void f_00503830_orig(Cpu *c);
void f_00503830(Cpu *c) {
    if (sky_night() > .001f) { c->esp += 4; return; }
    f_00503830_orig(c);
}

// The screen-space SunInfo streaks are a second solar producer, independent
// of the type-21 world sprites. They run before the visual-treatment chain
// and otherwise cover the night dome's moon with the daylight sun's halo.
void f_006D04E0_orig(Cpu *c);
void f_006D04E0(Cpu *c) {
    if (sky_night() > .001f) { c->esp += 8; return; } // ret 4
    f_006D04E0_orig(c);
}

// The skybox effect's three complete ps_1_1 bytecode programs, including CTAB.
static int sky_pass(uint64_t hash) {
    switch (hash) {
        case 0xd39c842f6c968e98ull: return 1;
        case 0x6208e43ebe37c4d5ull: return 2;
        case 0xf70a7745b3cd5d57ull: return 3;
        default: return 0;
    }
}

// Diagnostic-only context at 00505380's polygon submission; the flare argument
// is in its standard EBP frame. The original solar counter includes every view;
// the separate bearing counter selects primary-view sprite centres, not pixels.
void f_006BE750_orig(Cpu *c);
void flare_streaks_poly(Cpu *c);
void f_006BE750(Cpu *c) {
    flare_streaks_poly(c);   // lamp streaks (flare_streaks.c): world flare polygons only, Off by default
    if (!diag_on) { f_006BE750_orig(c); return; }
    int flare = M32(c->esp) == 0x00505E4Eu;
    int type = flare ? M8(M32(c->ebp + 12) + 0x2c) : -1;
    int solar = type == 21;
    int onmoon = 0;
    uint32_t tod = M32(0x009B392Cu);
    // Render's ePoly has four float4 positions. Compare its centre's view ray
    // with the moon in world space, in the primary view only (five degrees).
    if (flare && type < 32 && c->ecx && M32(c->ecx + 4) == 1 && tod) {
        uint32_t camera = M32(c->ecx + 0x40), poly = M32(c->esp + 4);
        if (camera && poly) {
            float light[3] = {MF32(tod + 0x180), MF32(tod + 0x184), MF32(tod + 0x188)};
            float moon[3], ray[3] = {0};
            daynight_sky_moon(MF32(tod + 8), light, MF32(tod + 0xc), moon);
            float dot = 0, len2 = 0;
            for (int j = 0; j < 3; ++j) {
                for (int i = 0; i < 4; ++i) ray[j] += MF32(poly + i * 16 + j * 4) * .25f;
                ray[j] -= MF32(camera + 0x40 + j * 4);
                dot += ray[j] * moon[j]; len2 += ray[j] * ray[j];
            }
            onmoon = len2 > 0 && dot / (sqrtf(len2) * 83666.0f) > .996194698f;
        }
    }
    if (solar) ++sun_polys;
    if (onmoon) ++moon_flare_polys[type];
    int previous = sun_context;
    int previous_moon = moon_flare_context;
    sun_context = solar;
    moon_flare_context = onmoon ? type + 1 : 0;
    f_006BE750_orig(c);
    sun_context = previous;
    moon_flare_context = previous_moon;
}

// Night recipe decoded from the later game's sky effect (pixel program
// ps_3_0; constants c4..c13 are quoted as literals below). Its inputs are the
// dome-local position and a dome texture coordinate. Here both come from the
// view ray (texcoord4, written by the patched vertex program) on an ellipsoid
// with the decoded zenith height 7000 and the horizon radius at which the
// decoded cloud fade reaches zero (sqrt(14700^2 - 7000^2)); the planar cloud
// coordinate is this port's choice. One texture carries three planes:
// alpha = cloud density, red = cloud shading, green = the moon's face.
// dn[0] = night factor and night sky colour, dn[1] = moon position and gain.
// Clouds retain the decoded half level; the moon has its own emission gain.
static const char *const sky_night_msl =
    "    if (dn[0].x > 0.0f) {\n"
    "        float3 dv = pi.texcoord4.xyz; float dl = length(dv);\n"
    "        float3 V = dl > 0.0f ? dv / dl : float3(0.0f, 0.0f, 1.0f);\n"
    "        V.z = max(V.z, 0.0f); V = normalize(V + float3(0.0f, 0.0f, 1e-4f));\n"
    "        float3 P = V * rsqrt((V.x * V.x + V.y * V.y) / (12926.33f * 12926.33f) + V.z * V.z / (7000.0f * 7000.0f));\n"
    "        float3 toM = dn[1].xyz - P; float dist = length(toM); float3 D = toM / dist;\n"
    "        float3 T1 = normalize(cross(D, float3(0.0f, 0.0995037f, 0.995037f)));\n"
    "        float3 T2 = normalize(cross(D, T1));\n"
    "        float3 Q = P + V * (dist - dot(V, D)) - dn[1].xyz;\n"
    "        float2 muv = (float2(dot(T1, Q), dot(T2, Q)) * float2(1.21f, 1.0f) + 3200.0f) * 0.00015625f;\n"
    "        float face = dn_tex.sample(dn_smp, clamp(muv, 0.002f, 0.998f), level(0.0f)).g;\n"
    "        float glow = min(1.0f / pow(length(Q) * 0.000477f, 0.57f), 0.915f);\n"
    // The face plane is white outside its disc. Bound its emission before
    // the highlight shoulder, and resolve its shallow markings in clear sky.
    // Retain the decoded lunar contribution under native overcast cover.
    "        float rho = length(2.0f * (muv - 0.5f));\n"
    "        float disc = (0.10f + 0.75f * saturate((face - 0.90f) * 10.0f)) * (1.0f - smoothstep(0.98f, 1.02f, rho));\n"
    "        float halo = 0.025f * exp(-max(rho - 1.0f, 0.0f) * 8.0f);\n"
    "        float lunar = mix((disc + halo) * (dn[1].w / 3.2f), face * pow(glow, 1.7f) * dn[1].w, saturate(c[0].z * 1.25f));\n"
    "        float3 sky = (1.0f - P.z * 0.000142857f) * dn[0].yzw + lunar * float3(0.99f, 1.0f, 0.92f);\n"
    "        float e = saturate(length(float3(0.0f, 0.0f, 7000.0f) - P) * 6.80272e-05f); e *= e;\n"
    "        float4 cl = dn_tex.sample(dn_smp, (P.xy * (0.5f / 12926.33f) + 0.5f) * 2.77f + pi.texcoord4.w * float2(-0.14f, -0.28f));\n"
    "        float a = (1.0f - e * e) * cl.a * saturate(c[0].z * 1.25f);\n"
    "        float g = saturate(glow * 0.357143f);\n"
    "        float rim = g * g * (3.0f - 2.0f * g) * 200.0f * pow(a * (1.0f - a), 3.07f);\n"
    "        float gap = 1.0f - glow; gap *= gap;\n"
    "        float body = a * cl.r * cl.r * 0.34f * gap * gap;\n"
    "        float3 night = sky * (1.0f - a) + (rim + body) * (12.5f * dn[0].z);\n"
    "        float peak = max(night.r, max(night.g, night.b));\n"
    "        if (peak > 0.7f) night *= (0.7f + 0.25f * (1.0f - exp(-(peak - 0.7f) * 4.0f))) / peak;\n"
    "        r.c0.rgb = mix(r.c0.rgb, night, dn[0].x);\n"
    "    }\n";

static char *splice(const char *src, const char *at, size_t cut, const char *text) {
    char *out = malloc(strlen(src) + strlen(text) + 1);
    if (!out) return NULL;
    size_t a = at - src;
    memcpy(out, src, a); strcpy(out + a, text); strcat(out, at + cut);
    return out;
}

// The dome's vertex program leaves texcoord4 unused: carry the view ray
// (dome-local position minus eye) and the game's own cloud scroll in it.
char *daynight_sky_patch_vertex(const char *src) {
    static const char old[] = "r.texcoord4 = so.tex[4];";
    const char *at = src ? strstr(src, old) : NULL;
    if (!at || !strstr(src, "si.v[0]") || !strstr(src, "constant float4 *c ")) return NULL;
    return splice(src, at, sizeof old - 1, "r.texcoord4 = float4(si.v[0].xyz - c[10].xyz, c[11].x);");
}

char *daynight_sky_patch(const char *src, int pass) {
    if (!src || pass < 1 || pass > 3) return NULL;
    const char *in = strstr(src, "struct SmPSStageIn {");
    const char *member = in ? strstr(in, "[[position]];") : NULL;
    const char *stage = strstr(src, "fragment SmPSStageOut sm_main(");
    const char *args = stage ? strstr(stage, ") {\n") : NULL;
    const char *ret = args ? strstr(args, "    return r;\n") : NULL;
    if (!ret || !member || member > stage) return NULL;
    char *a = splice(src, ret, 0, pass == 1 ? sky_night_msl : "    r.c0.a *= 1.0f - dn[0].x;\n");
    char *b = a ? splice(a, a + (args - src), 0, ", constant float4 *dn [[buffer(6)]], texture2d<float> dn_tex [[texture(14)]], sampler dn_smp [[sampler(14)]]") : NULL;
    free(a);
    if (!b || pass != 1) return b;
    char *c = splice(b, b + (member - src) + 13, 0, "\n    float4 texcoord4 [[user(texcoord4)]];");
    free(b);
    return c;
}

// Moon position in the decoded dome's units: the decoded effect fixes it at
// (-50000, 60000, 30000); here the lighting model's moon direction replaces
// the fixed bearing at the same distance, so disc, glow and shadows agree.
void daynight_sky_moon(float phase, const float light[3], float azimuth, float out[3]) {
    float len = sqrtf(light[0] * light[0] + light[1] * light[1] + light[2] * light[2]);
    if (!isfinite(len) || len < 1e-6f) { out[0] = -50000; out[1] = 60000; out[2] = 30000; return; }
    // Rounding can pin phase to an endpoint on either side of the handover.
    // There the sun points along its horizon bearing and the moon opposes it.
    // A half-length margin excludes the midnight projection's rounding noise.
    float horizon = (cosf(azimuth) * light[0] + sinf(azimuth) * light[1]) *
                    (phase <= .05f ? -1 : 1);
    int day = (phase > .05f && phase < .95f) ||
              horizon > len * .5f;
    float k = (day ? -83666.0f : 83666.0f) / len;
    for (int i = 0; i < 3; ++i) out[i] = light[i] * k;
}

#if defined(__OBJC__) && !defined(DN_SKY_CORE_ONLY)
#if !defined(NFS_PUBLIC) && __has_include("daynight_sky_private.inc")
#include "daynight_sky_private.inc"
#endif

char *daynight_sky_shader(const uint32_t *tokens, uint32_t n, const SmResult *refl) {
#ifdef DN_SKY_PIXELS
    if (!enabled || !daynight_sky_texture(DN_SKY_HASH)) return NULL;
    uint64_t hash = 1469598103934665603ull;
    for (size_t i = 0; i < 4 * (size_t)n; ++i) hash = (hash ^ ((const uint8_t *)tokens)[i]) * 1099511628211ull;
    // The base pass's complete vs_1_1 program (it pairs only with pass 1).
    if (refl->shader_type == SM_VERTEX) return hash == 0x43afa2430c786f3dull ? daynight_sky_patch_vertex(refl->msl) : NULL;
    return refl->shader_type == SM_PIXEL ? daynight_sky_patch(refl->msl, sky_pass(hash)) : NULL;
#else
    (void)tokens; (void)n; (void)refl;
    return NULL;
#endif
}

int daynight_sky_bind(id<MTLRenderCommandEncoder> encoder, id<MTLDevice> device, uint64_t hash, const char *source, uint64_t frame, id<MTLTexture> fallback, DaynightSkySampler fallback_sampler) {
    static uint64_t last;
    static unsigned sky_draws, night_draws;
    if (diag_on) {
        if (frame != last) {
            if (sky_draws && (last % 30 == 0 || diag_frame_wanted(last, "BOOT_DIAG_SHOTS"))) {
                diag_event("daynight_sky: frame=%llu n=%.6f sun_draws=%u sun_polys=%u sky_draws=%u night_draws=%u", (unsigned long long)last, sky_night(), sun_draws, sun_polys, sky_draws, night_draws);
                unsigned draws = 0, polys = 0;
                for (int i = 0; i < 32; ++i) {
                    draws += moon_flare_draws[i]; polys += moon_flare_polys[i];
                    if (moon_flare_polys[i]) diag_event("moon_flare: frame=%llu type=%d draws=%u polys=%u", (unsigned long long)last, i, moon_flare_draws[i], moon_flare_polys[i]);
                }
                diag_event("moon_flare: frame=%llu all_draws=%u all_polys=%u", (unsigned long long)last, draws, polys);
            }
            last = frame; sky_draws = night_draws = sun_draws = sun_polys = 0;
            memset(moon_flare_draws, 0, sizeof moon_flare_draws);
            memset(moon_flare_polys, 0, sizeof moon_flare_polys);
        }
        if (sun_context) ++sun_draws;
        if (moon_flare_context) ++moon_flare_draws[moon_flare_context - 1];
        if (sky_pass(hash)) ++sky_draws;
    }
#ifdef DN_SKY_PIXELS
    if (!enabled || !daynight_sky_texture(DN_SKY_HASH) || !sky_pass(hash)) return 0;
    // A replacement compile failure falls back to the translation. Count and
    // bind only when the selected, successfully compiled source has our inputs.
    if (!source || !strstr(source, "dn_tex [[texture(14)]]")) return 0;
    static id<MTLTexture> texture;
    static id<MTLSamplerState> sampler;
    static int attempted;
    if (!attempted) {
        attempted = 1;
        MTLTextureDescriptor *desc = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm width:DN_SKY_WIDTH height:DN_SKY_HEIGHT mipmapped:NO];
        texture = [device newTextureWithDescriptor:desc];
        if (texture) [texture replaceRegion:MTLRegionMake2D(0, 0, DN_SKY_WIDTH, DN_SKY_HEIGHT) mipmapLevel:0 withBytes:dn_sky_pixels bytesPerRow:DN_SKY_WIDTH * 4];
        MTLSamplerDescriptor *sd = [MTLSamplerDescriptor new];
        sd.minFilter = sd.magFilter = MTLSamplerMinMagFilterLinear;
        sd.sAddressMode = sd.tAddressMode = MTLSamplerAddressModeRepeat;
        sampler = [device newSamplerStateWithDescriptor:sd];
    }
    // The decoded dome colour is 0.5 x (0.02, 0.04, 0.07) at the horizon; the
    // night sky colour takes its place. Cloud emission stays at half level in
    // the shader; amplify the low-contrast face independently, with a shoulder
    // above 0.7 that approaches 0.95 without clipping the scene target.
    DaynightRecord night;
    daynight_night_record(&night);
    float k[8] = {texture && sampler ? sky_night() : 0, night.sky[0], night.sky[1], night.sky[2], -50000, 60000, 30000, 3.2f * night.sky[1] / 0.04f};
    uint32_t tod = M32(0x009B392Cu);
    if (k[0] > 0 && tod) {
        float light[3] = {MF32(tod + 0x180), MF32(tod + 0x184), MF32(tod + 0x188)};
        daynight_sky_moon(MF32(tod + 8), light, MF32(tod + 0xc), k + 4);
    }
    [encoder setFragmentBytes:k length:sizeof k atIndex:6];
    [encoder setFragmentTexture:texture ?: fallback atIndex:14];
    [encoder setFragmentSamplerState:sampler ?: fallback_sampler(0, 0) atIndex:14];
    if (diag_on && k[0] > 0 && sky_pass(hash) == 1) ++night_draws;
    return 1;
#else
    (void)encoder; (void)device; (void)hash; (void)source; (void)fallback; (void)fallback_sampler;
    return 0;
#endif
}
#endif
