// water_port.m - the port's own sea and harbour water (NFS_WATER=port; see water_port.h).
//
// The PC world shader keeps everything it does (diffuse, lighting, shadows, extinction and in-scatter). The vertex
// shader also hands on the local position, the clip position and the PC's own per-vertex extinction; the pixel shader
// then adds a JONSWAP wave normal from two generated maps (tools_snapshot/port_water_gen.py: two tiling layers of eight
// bands, 128 m and 16 m, evolving with omega = sqrt(g |k|) in simulation time) and an exact air/water Fresnel reflection of the game's own
// reflection target plus a sun glint whose width comes from the slope variance the pixel cannot resolve:
//   out = inscatter + (1 - F) (pc - inscatter) + extinction (F reflection + glint sun).
#include "water_port.h"
#include "gen/port_water.inc"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

// *s (malloc'd): `with` inserted after the first `what` at or after `from` (NULL: the start). 0 when not found.
static int insert_after(char **s, const char *from, const char *what, const char *with) {
    char *base = from ? strstr(*s, from) : *s;
    char *at = base ? strstr(base, what) : NULL;
    if (!at) return 0;
    size_t pre = (size_t)(at - *s) + strlen(what), n = strlen(*s), w = strlen(with);
    char *r = malloc(n + w + 1);
    memcpy(r, *s, pre); memcpy(r + pre, with, w); memcpy(r + pre + w, *s + pre, n - pre + 1);
    free(*s); *s = r;
    return 1;
}

// The wave maps (render.m, once per simulation step while water is drawn): a 256 x 256 x 4 RGBA16F array, slices
// coast 128 m, coast 16 m, harbour 128 m, harbour 16 m, each holding (dh/dx, dh/dy, (dh/dx)^2, (dh/dy)^2) with mips:
// a mip's average keeps the resolved slope and its second moments the slope variance the pixel cannot resolve.
static const char kernel_src[] =
"#include <metal_stdlib>\n"
"using namespace metal;\n"
"constant float4 port_water_coast[" "16" "] = { " PORTW_COAST " };\n"
"constant float4 port_water_harbour[" "16" "] = { " PORTW_HARBOUR " };\n"
"kernel void port_water_waves(texture2d_array<float, access::write> out [[texture(0)]], constant float &t [[buffer(0)]],\n"
"                             uint3 id [[thread_position_in_grid]]) {\n"
"    uint layer = id.z & 1u;\n"
"    float tile = layer ? 16.0f : 128.0f;\n"
"    float2 xy = (float2(id.xy) + 0.5f) * (tile / 256.0f), s = float2(0.0f);\n"
"    for (uint i = layer * 8u; i < layer * 8u + 8u; ++i) {\n"
"        float4 w = id.z >= 2u ? port_water_harbour[i] : port_water_coast[i];\n"
"        s -= w.xy * (w.z * sin(dot(w.xy, xy) - sqrt(" PORTW_G "f * length(w.xy)) * t + w.w));   // dh = -A k sin\n"
"    }\n"
"    out.write(float4(s, s * s), id.xy, id.z);\n"
"}\n";
const char *water_port_kernel(void) { return kernel_src; }

static const char ps_helper[] =
"static float port_water_fresnel(float c) {   // exact unpolarised dielectric, n = " PORTW_IOR " (F0 " PORTW_F0 ")\n"
"    const float n = " PORTW_IOR "f;\n"
"    float g = sqrt(n * n - 1.0f + c * c), a = (g - c) / (g + c), b = (c * (g + c) - 1.0f) / (c * (g - c) + 1.0f);\n"
"    return 0.5f * a * a * (1.0f + b * b);\n"
"}\n"
"// wk[15..17]: local to world columns, wk[18]: local light direction, wk[19]: local eye, wk[22]: the PC's sun colour.\n"
"// Water above sea level (creek, ponds, reservoirs) is sheltered: the harbour sea state; the open sea is at z 0.\n"
"static float3 port_water(float3 pc, float3 inscatter, float4 p, float4 clip, float4 ext, constant float4 *wk,\n"
"                         texture2d<float> refl, texture2d_array<float> waves) {\n"
"    constexpr sampler wrap(coord::normalized, address::repeat, filter::linear, mip_filter::linear, max_anisotropy(8));\n"
"    float2 xy = float2(dot(p, wk[15]), dot(p, wk[16]));\n"
"    bool harbour = dot(p, wk[17]) > 1.0f;\n"
"    uint base = harbour ? 2u : 0u;\n"
"    float4 m0 = waves.sample(wrap, xy / 128.0f, base), m1 = waves.sample(wrap, xy / 16.0f, base + 1u);\n"
"    float2 s = -(m0.xy + m1.xy);   // -dh/dx, -dh/dy\n"
"    // Slope variance below the shortest band plus what the mip level averaged away: it widens the sun lobe.\n"
"    float var = (harbour ? " PORTW_HARBOUR_FLOOR "f : " PORTW_COAST_FLOOR "f) +\n"
"        max(m0.z - m0.x * m0.x + m0.w - m0.y * m0.y, 0.0f) + max(m1.z - m1.x * m1.x + m1.w - m1.y * m1.y, 0.0f);\n"
"    float3 n = normalize(float3(s, 1.0f)), v = normalize(wk[19].xyz - p.xyz);\n"
"    float f = port_water_fresnel(max(dot(n, v), 1e-4f));\n"
"    float2 uv = float2(clip.x / clip.w * 0.5f + 0.5f, 0.5f - clip.y / clip.w * 0.5f) + s * " PORTW_DISTORT "f;\n"
"    constexpr sampler edge(coord::normalized, address::clamp_to_edge, filter::linear);\n"
"    float3 r = refl.sample(edge, uv).rgb;\n"
"    // Sun glint: a normalised Phong lobe about the mirror direction, exponent 1 / (2 slope variance).\n"
"    float3 l = normalize(wk[18].xyz), h = normalize(v + l);\n"
"    float e = 0.5f / var;\n"
"    float glint = l.z > 0.0f ? port_water_fresnel(saturate(dot(v, h))) * pow(saturate(dot(reflect(-v, n), l)), e) * (e + 2.0f) * 0.5f : 0.0f;\n"
"    return inscatter + (1.0f - f) * (pc - inscatter) + saturate(ext.rgb) * (f * r + glint * wk[22].rgb);\n"
"}\n";

char *water_port_shader(const char *pc_msl, int vertex) {
    if (!pc_msl || PORTW_WAVES != 16) return NULL;
    char *s = strdup(pc_msl);
    int ok;
    if (vertex) {
        // The PC's clamped extinction is r1 when oD0 = r1 * colour is written (every world VS).
        const char *ext = "    // mul oD0.xyz, r1, r0\n";
        ok = strstr(s, ext) && !strstr(s, "water_p") &&
             insert_after(&s, "struct SmVSOut {", " float4 psize;", " float4 water_e;") &&
             insert_after(&s, ext, "}\n", "    so.water_e = r1;\n") &&
             insert_after(&s, "struct SmVSStageOut {", "    float4 pos [[position]];\n",
                          "    float4 water_p [[user(water_p)]];\n    float4 water_c [[user(water_c)]];\n    float4 water_e [[user(water_e)]];\n") &&
             insert_after(&s, "vertex SmVSStageOut sm_main(", "    r.pos = so.pos;\n",
                          "    r.water_p = si.v[0]; r.water_c = so.pos; r.water_e = so.water_e;\n");
    } else {
        // The PC pixel shader ends with `+ t2`: the in-scatter, added after the surface's extinction.
        ok = strstr(s, "r0, t2\n") && strstr(s, "si.tex[2] = pi.texcoord2;\n") && !strstr(s, "water_p") &&
             strstr(s, "\nstruct SmPSStageIn {") &&
             insert_after(&s, "struct SmPSStageIn {", "    float4 pos [[position]];\n",
                          "    float4 water_p [[user(water_p)]];\n    float4 water_c [[user(water_c)]];\n    float4 water_e [[user(water_e)]];\n") &&
             insert_after(&s, NULL, "fragment SmPSStageOut sm_main(SmPSStageIn pi [[stage_in]],\n",
                          "    constant float4 *wk [[buffer(5)]], texture2d<float> wrefl [[texture(14)]], texture2d_array<float> wwaves [[texture(15)]],\n") &&
             insert_after(&s, "    sm_body(si, so, ", ");\n",
                          "    so.oc[0].rgb = port_water(so.oc[0].rgb, si.tex[2].xyz, pi.water_p, pi.water_c, pi.water_e, wk, wrefl, wwaves);\n");
        if (ok) {     // the helper goes just before the stage-in struct (after sm_body)
            char *at = strstr(s, "\nstruct SmPSStageIn {");
            size_t pre = (size_t)(at - s) + 1, n = strlen(s), h = strlen(ps_helper);
            char *r = malloc(n + h + 1);
            memcpy(r, s, pre); memcpy(r + pre, ps_helper, h); memcpy(r + pre + h, s + pre, n - pre + 1);
            free(s); s = r;
        }
    }
    if (!ok) { free(s); return NULL; }
    return s;
}

void water_port_constants(float out[32][4], const float sun[4], float sim_time) {
    out[10][0] = 0;                     // the console path's fog distance: unused here
    out[21][0] = sim_time; out[21][1] = out[21][2] = out[21][3] = 0;
    for (int i = 0; i < 4; ++i) out[22][i] = sun[i];
}
