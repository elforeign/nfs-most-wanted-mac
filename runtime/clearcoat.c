// Smooth lacquer over the complete existing paint finish.
#include "clearcoat.h"
#include "settings.h"
#include <stdlib.h>
#include <string.h>

int postfx_look_variant(const char *variant);
static int enabled = -1;
static void apply(int on) { enabled = on != 0; }
int clearcoat_enabled(void) {
    if (postfx_look_variant("coatoff")) return 0;
    if (postfx_look_variant("coaton")) return 1;
    if (enabled < 0) {
        const char *e = getenv("NFS_CLEARCOAT");   // unset or empty: On (the shipped default)
        enabled = !(e && *e) || !strcmp(e, "on");
    }
    return enabled;
}
int clearcoat_shader_hash(uint64_t hash) {
    switch (hash) {
        case 0x393123DA8B3EC17FULL: case 0x2CD941122737DDF5ULL:
        case 0xE3C84E78B9847542ULL: case 0x6F1A7154236357C9ULL:
        case 0xA8F9086D6BE6DB85ULL: case 0x89E9A52CCEE09BCFULL:
        case 0x1BEA38904139377AULL: return 1;
        default: return 0;
    }
}
int clearcoat_is_paint(const char *name, size_t size) {
    // Bounded material names from eLightMaterial. Do not lacquer glass/rubber.
    static const char *const prefixes[] = {"REGPAINT", "METPAINT", "PEARL"};
    for (size_t i = 0; i < sizeof prefixes / sizeof *prefixes; ++i) {
        size_t n = strlen(prefixes[i]);
        if (size >= n && !memcmp(name, prefixes[i], n)) return 1;
    }
    return 0;
}

// Burley, Physically Based Shading at Disney, sections 5.4-5.5:
// polyurethane IOR 1.5, Schlick F0=((1.5-1)/(1.5+1))^2=0.04.
// https://www.disneyanimation.com/publications/physically-based-shading-at-disney/
// A bounded mirror-power lobe (256, about 4.2 degree half width) approximates
// smooth lacquer; it is not a measured roughness or a full microfacet BRDF.
// Two interfaces attenuate the complete authored finish by (1-F(N.V))^2.
// The sun and smooth environment share F: the direct lobe cannot count the
// same reflection twice. Only their addition can consume available headroom;
// never normalize the base's authored HDR hotspot or reflection. Per channel,
// output <= max(1, base, env, sun); a unit-radiance base remains bounded by 1.
// Legacy paint above 1 is radiance, not a unit-reflectance energy integral.
static const char coat_math[] =
"static float3 coat_unit(float3 x) {\n"
"    float q = dot(x, x);\n"
"    return q > 1e-20f ? x * rsqrt(q) : float3(0, 0, 1);\n"
"}\n"
"static float coat_fresnel(float cosine) {\n"
"    float t = 1.0f - saturate(cosine), t2 = t * t;\n"
"    return 0.04f + 0.96f * t2 * t2 * t;\n"
"}\n"
"static float3 coat_weights(float3 normal, float3 eye, float3 light, float visible) {\n"
"    float3 N = coat_unit(normal), V = coat_unit(eye), L = coat_unit(light);\n"
"    float nv = saturate(dot(N, V)), nl = saturate(dot(N, L));\n"
"    float F = coat_fresnel(nv);\n"
"    float rv = saturate(dot(reflect(-V, N), L));\n"
"    float peak = powr(rv, 256.0f) * float(nv > 0.0f && nl > 0.0f);\n"
"    float direct = min(F, coat_fresnel(dot(V, coat_unit(V + L))) * peak) * saturate(visible);\n"
"    return float3((1.0f - F) * (1.0f - F), F - direct, direct);\n"
"}\n"
"static float3 coat_layer(float3 base, float3 env, float3 sun, float3 N, float3 V, float3 L, float visible) {\n"
"    float3 w = coat_weights(N, V, L, visible);\n"
"    float3 under = w.x * base;\n"
"    float3 limit = max(float3(1.0f), max(base, max(env, sun)));\n"
"    float3 addition = w.y * env + w.z * sun;\n"
"    return under + min(addition, max(limit - under, 0.0f));\n"
"}\n";

static char *replace_once(const char *source, const char *old, const char *replacement) {
    const char *at = strstr(source, old);
    if (!at || strstr(at + strlen(old), old)) return NULL;
    size_t prefix = (size_t)(at - source), n = strlen(source) - strlen(old) + strlen(replacement) + 1;
    char *out = malloc(n);
    if (!out) return NULL;
    memcpy(out, source, prefix);
    memcpy(out + prefix, replacement, strlen(replacement));
    strcpy(out + prefix + strlen(replacement), at + strlen(old));
    return out;
}
char *clearcoat_patch(const char *source, int on) {
    if (!on) return strdup(source);
    // Match only the two production paint outputs; fail closed on source drift.
    const char *ordinary = "    o.c0 = float4((v0.rgb * tex.rgb + s) * shade + env.rgb * v1.w * v0.w * 0.5f * (1.0f + 2.0f * m.k[2].x * v0.w), tex.a * v0.w);\n";
    const char *normalmap = "        o.c0 = float4((D * dif * tex.rgb + s2) * shade + e2, tex.a * v0.w);\n";
    char *a = replace_once(source, ordinary,
        "    o.c0 = float4((v0.rgb * tex.rgb + s) * shade + env.rgb * v1.w * v0.w * 0.5f * (1.0f + 2.0f * m.k[2].x * v0.w), tex.a * v0.w);\n"
        "    if (m.k[10].w > 0.5f && m.k[0].w == 0.0f)\n"
        "        o.c0.rgb = coat_layer(o.c0.rgb, env.rgb * saturate(v1.w * v0.w), m.k[1].rgb * v0.w, pi.texcoord4.xyz, v, l, lit);\n");
    if (!a) return NULL;
    char *b = replace_once(a, normalmap,
        "        o.c0 = float4((D * dif * tex.rgb + s2) * shade + e2, tex.a * v0.w);\n"
        "        if (m.k[10].w > 0.5f && m.k[0].w == 0.0f)\n"
        "            o.c0.rgb = coat_layer(o.c0.rgb, tex1.sample(smp1, float3(-Vt.xy, Vt.z)).rgb * saturate(v0.w * pi.texcoord6.w), m.k[1].rgb * v0.w, N, V, L0, lit);\n");
    free(a);
    if (!b) return NULL;
    const char *ns = "using namespace metal;\n";
    char *helpers = malloc(strlen(ns) + strlen(coat_math) + 1);
    if (!helpers) { free(b); return NULL; }
    strcpy(helpers, ns);
    strcat(helpers, coat_math);
    char *result = replace_once(b, ns, helpers);
    free(helpers);
    free(b);
    return result;
}

// t0 keeps its PC base and cube s0/diffuse s1 layout. Its original interpolators
// contain N.H rather than N/V; carry smooth object-space directions separately.
char *clearcoat_vertex(const char *source) {
    char *a = replace_once(source, "    r.texcoord4 = so.tex[4];\n",
        "    r.texcoord4 = float4(si.v[1].xyz, 0);\n");
    if (!a) return NULL;
    char *b = replace_once(a, "    r.texcoord5 = so.tex[5];\n",
        "    r.texcoord5 = float4(c[13].xyz - si.v[0].xyz, 0);\n");
    free(a);
    if (!b) return NULL;
    char *out = replace_once(b, "    r.texcoord6 = so.tex[6];\n",
        "    r.texcoord6 = float4(c[10].xyz, 0);\n");
    free(b);
    return out;
}
char *clearcoat_t0_patch(const char *source) {
    const char *ns = "using namespace metal;\n";
    size_t size = strlen(ns) + strlen(coat_math) + 64;
    char *helpers = malloc(size);
    if (!helpers) return NULL;
    strcpy(helpers, ns);
    strcat(helpers, coat_math);
    strcat(helpers, "struct MatConsts { float4 k[12]; };\n");
    char *a = replace_once(source, ns, helpers);
    free(helpers);
    if (!a) return NULL;
    const char *fragment = strstr(a, "fragment SmPSStageOut sm_main(");
    const char *end = fragment ? strstr(fragment, ") {\n") : NULL;
    if (!end) { free(a); return NULL; }
    // Limit this replacement to the entry point (helper signatures also end here).
    const char *argument = ",\n    constant MatConsts &mk [[buffer(4)]]) {\n";
    size_t prefix = (size_t)(end - a);
    char *b = malloc(strlen(a) + strlen(argument) + 1);
    if (!b) { free(a); return NULL; }
    memcpy(b, a, prefix); strcpy(b + prefix, argument);
    strcat(b, end + strlen(") {\n"));
    free(a);
    a = replace_once(b, "    float4 texcoord3 [[user(texcoord3)]];\n",
        "    float4 texcoord3 [[user(texcoord3)]];\n"
        "    float4 texcoord4 [[user(texcoord4)]];\n"
        "    float4 texcoord5 [[user(texcoord5)]];\n"
        "    float4 texcoord6 [[user(texcoord6)]];\n");
    free(b);
    if (!a) return NULL;
    const char *alpha = "    if (aux.alpha_to_coverage != 0u";
    char *out = replace_once(a, alpha,
        "    if (mk.k[10].w > 0.5f) {\n"
        "        float alpha = saturate(pi.color0.a);\n"
        "        float3 env = tex0.sample(smp0, pi.texcoord0.xyz, bias(aux.lodbias[0])).rgb * saturate(pi.color1.a) * alpha;\n"
        "        so.oc[0].rgb = coat_layer(so.oc[0].rgb, env, mk.k[1].rgb * alpha, pi.texcoord4.xyz, pi.texcoord5.xyz, pi.texcoord6.xyz, 1.0f);\n"
        "    }\n"
        "    if (aux.alpha_to_coverage != 0u");
    free(a);
    return out;
}

static const char *const choices[] = {"Off", "On"};
static const char *const values[] = {"off", "on"};
static Setting setting = {
    .id = "graphics.clearcoat", .category = SET_CARS, .order = 36,
    .label = "Clear coat",
    .help = "Adds a smooth lacquer sheen above the paint and its flake, including mirrors. Shadowed views need X360 car paint. Off keeps the original finish.",
    .type = SET_CHOICE, .choices = choices, .values = values, .nchoices = 2,
    .def = 1, .env = "NFS_CLEARCOAT", .apply = apply
};
__attribute__((constructor)) static void register_setting(void) { settings_add(&setting); }
