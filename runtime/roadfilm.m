// Air/water Fresnel experiment; legacy wetness, coverage, lighting and fog stay intact.
#include "roadfilm.h"
#include <stdlib.h>
#include <string.h>

int postfx_look_variant(const char *variant);
static int film = -1;
void roadfilm_set(int enabled) { film = enabled != 0; }
int roadfilm_enabled(void) {
    if (postfx_look_variant("filmoff")) return 0;
    if (postfx_look_variant("filmon")) return 1;
    if (film < 0) {
        const char *e = getenv("NFS_ROAD_FILM");
        film = e && !strcmp(e, "1");
    }
    return film;
}
int roadfilm_shader_hash(uint64_t hash) {
    // Only the six decoded worldreflect pixel programs; no unrelated materials.
    switch (hash) {
        case 0xE5F37CD552B4BD5FULL: case 0xD2D9B9D538A06AEEULL:
        case 0xCB861F5D715664A3ULL: case 0x268CB5F65C872238ULL:
        case 0xCCAACC20F85F0BA7ULL: case 0x242B92C6A96DE029ULL: return 1;
        default: return 0;
    }
}
int roadfilm_draw(uint64_t hash, float wetness) {
    // Dry draws use the native program too: no optimizer-induced rounding drift.
    return wetness > 0.0f && roadfilm_shader_hash(hash) && roadfilm_enabled();
}

static const char roadfilm_math[] =
"// Exact unpolarized air/water Fresnel, n=1.33, F(1)=0.0200593122.\n"
"static float roadfilm_fresnel(float ci) {\n"
"    ci = clamp(ci, 0.0f, 1.0f);\n"
"    const float n = 1.33f;\n"
"    float ct = sqrt(1.0f - (1.0f - ci * ci) / (n * n));\n"
"    float rs = (ci - n * ct) / (ci + n * ct);\n"
"    float rp = (n * ci - ct) / (n * ci + ct);\n"
"    return 0.5f * (rs * rs + rp * rp);\n"
"}\n"
"static float4 roadfilm_reflection(float4 r, float4 eye, float wet) {\n"
"    if (!(wet > 0.0f)) return r;\n"
"    // t4 is the existing tangent-space eye vector; geometric N is (0,0,1).\n"
"    float ci = eye.z * rsqrt(max(dot(eye.xyz, eye.xyz), 1.0e-20f));\n"
"    return float4(r.rgb * roadfilm_fresnel(ci), r.a);\n"
"}\n";

char *roadfilm_patch(const char *source) {
    // Compose after the ripple patch, or directly over the original lookup.
    const char *at = strstr(source, "mat_refl(tex3, smp3, ");
    if (!at) at = strstr(source, "tex3.sample(smp3, (");
    if (!at || !strstr(source, "si.tex[4] = pi.texcoord4;") ||
        !strstr(source, "static void sm_body(")) return NULL;
    if (strstr(at + 1, "mat_refl(tex3, smp3, ") || strstr(at + 1, "tex3.sample(smp3, (")) return NULL;
    const char *end = at;
    int depth = 0;
    do {
        if (*end == '(') ++depth;
        if (*end == ')' && --depth == 0) { ++end; break; }
        ++end;
    } while (*end);
    if (depth || end == at) return NULL;
    const char *body = strstr(source, "static void sm_body(");
    if (body >= at) return NULL;
    const char *open = "roadfilm_reflection(", *close = ", si.tex[4], c[1].w)";
    size_t total = strlen(source) + strlen(roadfilm_math) + strlen(open) + strlen(close) + 1;
    char *out = malloc(total);
    if (!out) return NULL;
    char *p = out;
#define APPEND(s, n) do { size_t len_ = (n); memcpy(p, (s), len_); p += len_; } while (0)
    APPEND(source, (size_t)(body - source));
    APPEND(roadfilm_math, strlen(roadfilm_math));
    APPEND(body, (size_t)(at - body));
    APPEND(open, strlen(open));
    APPEND(at, (size_t)(end - at));
    APPEND(close, strlen(close));
    strcpy(p, end);
#undef APPEND
    return out;
}
