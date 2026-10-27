// sm_interp.h - CPU reference interpreter for decoded D3D9 shaders (tests only).
// Executes sm::Shader directly with the D3D9 semantics documented in README.md, so the
// translated MSL can be compared against it numerically.
#pragma once
#include "../sm_ir.h"
#include "../sm_translate.h"
#include <array>
#include <vector>

namespace interp {

typedef std::array<float, 4> V4;

struct Texture {          // RGBA32F, nearest sampling, clamp-to-edge, 1 mip level
    int type = SM_TEX_2D; // SM_TEX_2D / CUBE / 3D
    int w = 1, h = 1, d = 1;  // cube: w == h, d = 6 faces
    bool depth = false;   // 1 channel (shadow map), compared with LessEqual
    std::vector<float> texels;  // w*h*d*4 (or w*h for depth)
    V4 sample(const float *coord) const;
    float compare(float u, float v, float ref) const;
};

struct Context {
    const float *c = nullptr;    // 256 float4
    const int *ci = nullptr;     // 16 int4
    const int *cb = nullptr;     // 16 int
    SmPSAux aux = {};
    const Texture *tex[16] = {};
    SmOptions opt = {};
};

extern bool g_trace;   // print every instruction result to stdout
// -1/0/+1: scale the results of rcp/rsq/exp/log/pow by (1 + k*2^-20) (about +-8 ulp) and offset
// dot products by k*2^-20*sum|a_i*b_i|, to
// tell genuine translation errors from ill-conditioned inputs that amplify the GPU's last-ulp
// differences in those functions
extern int g_ulp_nudge;

struct VSIn { V4 v[16]; };
struct VSOut { V4 pos, col[2], tex[8], fog, psize; };
struct PSIn { V4 col[2], tex[8], fog, vpos; float face = 1.0f; };
struct PSOut { V4 oc[4]; float depth = 0; bool killed = false; };

// sampler_types: SM_TEX_* per s#/stage as resolved by the translator (SmResult.samplers)
bool run_vs(const sm::Shader &s, const Context &ctx, const VSIn &in, VSOut &out, std::string *err = nullptr);
bool run_ps(const sm::Shader &s, const Context &ctx, const PSIn &in, PSOut &out, std::string *err = nullptr);

}  // namespace interp
