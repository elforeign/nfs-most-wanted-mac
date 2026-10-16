// d3d9_shadergen.cpp - Direct3D 9 shaders as source text for the GPU
// backends: Metal Shading Language (d3d9_msl.h), Vulkan GLSL (d3d9_glsl.h)
// and WGSL (d3d9_wgsl.h). One walk over the decoded program writes each
// language; they differ in spelling, which the Lang switches below choose.
#include "d3d9_glsl.h"
#include "d3d9_msl.h"
#include "d3d9_wgsl.h"
#include "../host/gpu/metal/surface.h"   // Run 15 Wave B WB1b: surf_ps_patch

#include <cctype>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <set>
#include <vector>

using namespace d9sh;

namespace d9msl {
namespace {

enum class Lang { MSL, GLSL, WGSL };

const char *kPrelude = R"MSL(#include <metal_stdlib>
using namespace metal;
struct D9V2F {
    float4 pos [[position]];
    float4 d0;
    float4 d1;
    float4 t0; float4 t1; float4 t2; float4 t3;
    float4 t4; float4 t5; float4 t6; float4 t7;
    float fog;
    float fogz;
};
struct D9VSParams { float4 halfpix; float4 ascale[16]; int4 bconst[4]; };
struct D9PSParams {
    float alpha_ref; int alpha_func; int fog_mode; int pad0;
    float4 fog_color;
    float fog_start; float fog_end; float fog_density; float pad1;
    int4 bconst[4];
};
static inline float4 d9_dz(float4 v) { return float4(v.x / v.z, v.y / v.z, v.z, v.w); }
static inline float4 d9_dw(float4 v) { return float4(v.x / v.w, v.y / v.w, v.z, v.w); }
static inline float4 d9_lit(float4 a) {
    float4 o = float4(1.0, max(a.x, 0.0), 0.0, 1.0);
    if (a.x > 0.0 && a.y > 0.0)
        o.z = pow(a.y, clamp(a.w, -127.9961, 127.9961));
    return o;
}
static inline float4 d9_nrm(float4 a) {
    float l = length(a.xyz);
    return l > 0.0 ? a / l : float4(0.0);
}
static inline bool d9_cmp(int f, float a, float b) {
    switch (f) {
    case 1: return false;
    case 2: return a < b;
    case 3: return a == b;
    case 4: return a <= b;
    case 5: return a > b;
    case 6: return a != b;
    case 7: return a >= b;
    default: return true;
    }
}
)MSL";

const char *kGlslPrelude = R"GLSL(#version 450
vec4 d9_dz(vec4 v) { return vec4(v.x / v.z, v.y / v.z, v.z, v.w); }
vec4 d9_dw(vec4 v) { return vec4(v.x / v.w, v.y / v.w, v.z, v.w); }
vec4 d9_lit(vec4 a) {
    vec4 o = vec4(1.0, max(a.x, 0.0), 0.0, 1.0);
    if (a.x > 0.0 && a.y > 0.0)
        o.z = pow(a.y, clamp(a.w, -127.9961, 127.9961));
    return o;
}
vec4 d9_nrm(vec4 a) {
    float l = length(a.xyz);
    return l > 0.0 ? a / l : vec4(0.0);
}
vec4 d9_round(vec4 v) { return sign(v) * floor(abs(v) + 0.5); }
bool d9_cmp(int f, float a, float b) {
    switch (f) {
    case 1: return false;
    case 2: return a < b;
    case 3: return a == b;
    case 4: return a <= b;
    case 5: return a > b;
    case 6: return a != b;
    case 7: return a >= b;
    default: return true;
    }
}
)GLSL";

const char *kWgslPrelude = R"WGSL(diagnostic(off, derivative_uniformity);
fn d9_dz(v: vec4f) -> vec4f { return vec4f(v.x / v.z, v.y / v.z, v.z, v.w); }
fn d9_dw(v: vec4f) -> vec4f { return vec4f(v.x / v.w, v.y / v.w, v.z, v.w); }
fn d9_lit(a: vec4f) -> vec4f {
    var o = vec4f(1.0, max(a.x, 0.0), 0.0, 1.0);
    if (a.x > 0.0 && a.y > 0.0) {
        o.z = pow(a.y, clamp(a.w, -127.9961, 127.9961));
    }
    return o;
}
fn d9_nrm(a: vec4f) -> vec4f {
    let l = length(a.xyz);
    if (l > 0.0) { return a / l; }
    return vec4f(0.0);
}
fn d9_round(v: vec4f) -> vec4f { return sign(v) * floor(abs(v) + 0.5); }
fn d9_alpha(v: vec4f) -> vec4f { return vec4f(0.0, 0.0, 0.0, v.r); }
fn d9_cmp(f: i32, a: f32, b: f32) -> bool {
    switch (f) {
        case 1: { return false; }
        case 2: { return a < b; }
        case 3: { return a == b; }
        case 4: { return a <= b; }
        case 5: { return a > b; }
        case 6: { return a != b; }
        case 7: { return a >= b; }
        default: { return true; }
    }
}
struct VSP { halfpix: vec4f, ascale: array<vec4f, 16>, bgra: vec4i, bconst: array<vec4i, 4> };
struct PSP {
    alpha_ref: f32, alpha_func: i32, fog_mode: i32, pad0: i32,
    fog_color: vec4f,
    fog_start: f32, fog_end: f32, fog_density: f32, pad1: f32,
    bconst: array<vec4i, 4>,
};
struct V2F {
    @builtin(position) pos: vec4f,
    @location(0) d0: vec4f,
    @location(1) d1: vec4f,
    @location(2) t0: vec4f, @location(3) t1: vec4f, @location(4) t2: vec4f, @location(5) t3: vec4f,
    @location(6) t4: vec4f, @location(7) t5: vec4f, @location(8) t6: vec4f, @location(9) t7: vec4f,
    @location(10) fog: f32,
    @location(11) fogz: f32,
};
)WGSL";

const char kComp[] = "xyzw";

std::string swizzle(uint32_t swz) {
    std::string s = ".";
    for (int k = 0; k < 4; ++k)
        s += kComp[(swz >> (2 * k)) & 3];
    return s;
}

std::string mask_of(uint32_t mask) {
    std::string s = ".";
    for (int k = 0; k < 4; ++k)
        if (mask >> k & 1)
            s += kComp[k];
    return s;
}

struct Gen {
    const Program &p;
    const Lang lang;
    const std::string F4; // the four-float vector type
    std::string body;
    std::string why;
    bool failed = false;
    std::set<uint32_t> stages; // sampler stages read
    uint32_t color_outputs = 0;
    bool writes_depth = false;
    PixelVariant variant;
    int depth = 0;      // open loops and ifs
    int loop_depth = 0; // open rep/loop blocks, for break
    std::string indent() const {
        return std::string(4 + 4 * (size_t)depth, ' ');
    }

    bool glsl() const {
        return lang == Lang::GLSL;
    }
    bool wgsl() const {
        return lang == Lang::WGSL;
    }
    // A four-float vector of one value, as a comparison's right-hand side.
    std::string splat(const char *v) const {
        return glsl()   ? std::string("vec4(") + v + ")"
               : wgsl() ? std::string("vec4f(") + v + ")"
                        : std::string(v);
    }
    std::string saturate(const std::string &v) const {
        return glsl() ? "clamp" + v.substr(0, v.size() - 1) + ", 0.0, 1.0)" : "saturate" + v;
    }
    // `f` where `cond` is false, `t` where it is true; `cond` is written with
    // the vector comparison `op` between `a` and `b`.
    std::string choose(const std::string &f, const std::string &t, const std::string &a,
                       const char *op, const std::string &b) const {
        if (!glsl())
            return "select(" + f + ", " + t + ", " + a + " " + op + " " + b + ")";
        return "mix(" + f + ", " + t + ", " + vector_compare(a, op, b) + ")";
    }
    static const char *glsl_compare(const char *op) {
        std::string o = op;
        return o == "<"    ? "lessThan"
               : o == "<=" ? "lessThanEqual"
               : o == ">"  ? "greaterThan"
               : o == ">=" ? "greaterThanEqual"
               : o == "==" ? "equal"
                           : "notEqual";
    }
    std::string vector_compare(const std::string &a, const char *op, const std::string &b) const {
        return std::string(glsl_compare(op)) + "(" + a + ", " + b + ")";
    }

    // A 3.0 pixel shader's vN, found through its dcl.
    std::string sm3_input(uint32_t index) {
        for (const DclIn &d : p.inputs)
            if (d.type == R_INPUT && d.index == index) {
                char buf[32];
                if (d.usage == 10) { // D3DDECLUSAGE_COLOR
                    snprintf(buf, sizeof buf, "iv[%u]", d.usage_index & 1);
                    return buf;
                }
                if (d.usage == 5) { // TEXCOORD
                    snprintf(buf, sizeof buf, "t[%u]", d.usage_index & 7);
                    return buf;
                }
                fail("pixel shader input usage " + std::to_string(d.usage));
                return F4 + "(0.0)";
            }
        fail("pixel shader input v" + std::to_string(index) + " has no dcl");
        return F4 + "(0.0)";
    }
    // A 3.0 vertex shader's oN, found through its dcl.
    std::string sm3_output(uint32_t index) {
        for (const DclIn &d : p.outputs)
            if (d.index == index) {
                char buf[32];
                switch (d.usage) {
                case 0: // POSITION
                case 9: // POSITIONT
                    return "opos";
                case 10: // COLOR
                    snprintf(buf, sizeof buf, "od[%u]", d.usage_index & 1);
                    return buf;
                case 5: // TEXCOORD
                    snprintf(buf, sizeof buf, "ot[%u]", d.usage_index & 7);
                    return buf;
                case 11: // FOG
                    return "ofog";
                case 4: // PSIZE
                    return "opsize";
                default:
                    fail("vertex shader output usage " + std::to_string(d.usage));
                    return "junk";
                }
            }
        fail("vertex shader output o" + std::to_string(index) + " has no dcl");
        return "junk";
    }

    // GLSL declares as many constant registers as the program reads.
    uint32_t const_limit = 256;

    Gen(const Program &prog, Lang l)
        : p(prog), lang(l), F4(l == Lang::GLSL   ? "vec4"
                               : l == Lang::WGSL ? "vec4f"
                                                 : "float4") {
        if (glsl() || wgsl())
            const_limit = d9glsl::constant_registers(prog);
    }

    void fail(const std::string &w) {
        if (!failed)
            why = w;
        failed = true;
    }

    std::string base(const Src &s) {
        char buf[96];
        switch (s.type) {
        case R_TEMP:
            snprintf(buf, sizeof buf, "r[%u]", s.index & 31);
            return buf;
        case R_INPUT:
            if (!p.pixel) {
                snprintf(buf, sizeof buf, "vv[%u]", s.index & 15);
                return buf;
            }
            if (p.major >= 3)
                return sm3_input(s.index);
            snprintf(buf, sizeof buf, "iv[%u]", s.index & 1);
            return buf;
        case R_CONST:
            if (s.rel && !p.pixel) {
                snprintf(buf, sizeof buf, "c[clamp(%s(a0.x) + %u, 0, %u)]", wgsl() ? "i32" : "int",
                         s.index, glsl() || wgsl() ? const_limit - 1 : 255);
                return buf;
            }
            snprintf(buf, sizeof buf, "c[%u]", s.index);
            return buf;
        case R_ADDR:
            if (!p.pixel)
                return "a0";
            snprintf(buf, sizeof buf, "t[%u]", s.index & 7);
            return buf;
        case R_CONSTINT: {
            // defi values; the integer constants a game sets are not plumbed.
            auto it = p.idefs.find(s.index);
            if (it == p.idefs.end()) {
                fail("integer constant i" + std::to_string(s.index) + " without defi");
                return F4 + "(0.0)";
            }
            snprintf(buf, sizeof buf, "%s(%d.0, %d.0, %d.0, %d.0)", F4.c_str(), it->second[0],
                     it->second[1], it->second[2], it->second[3]);
            return buf;
        }
        case R_CONSTBOOL: {
            auto it = p.bdefs.find(s.index);
            if (it == p.bdefs.end()) {
                if (s.index >= 16) {
                    fail("boolean constant register out of range");
                    return F4 + "(0.0)";
                }
                const std::string v = "P.bconst[" + std::to_string(s.index / 4) + "][" +
                                      std::to_string(s.index % 4) + "]";
                return wgsl() ? F4 + "(select(0.0, 1.0, " + v + " != 0))"
                              : F4 + "((" + v + " != 0) ? 1.0 : 0.0)";
            }
            return it->second ? F4 + "(1.0)" : F4 + "(0.0)";
        }
        case R_LOOP:
            return F4 + (wgsl() ? "(f32(aL))" : "(float(aL))");
        default:
            fail("source register type " + std::to_string(s.type));
            return F4 + "(0.0)";
        }
    }

    std::string src(const Src &s) {
        std::string v = "(" + base(s) + swizzle(s.swz) + ")";
        switch (s.mod) {
        case 0:
            return v;
        case 1:
            return "(-" + v + ")";
        case 2:
            return "(" + v + " - 0.5)";
        case 3:
            return "(-(" + v + " - 0.5))";
        case 4:
            return "(2.0 * " + v + " - 1.0)";
        case 5:
            return "(-(2.0 * " + v + " - 1.0))";
        case 6:
            return "(1.0 - " + v + ")";
        case 7:
            return "(2.0 * " + v + ")";
        case 8:
            return "(-(2.0 * " + v + "))";
        case 9:
            return "d9_dz" + v;
        case 10:
            return "d9_dw" + v;
        case 11:
            return "abs" + v;
        case 12:
            return "(-abs" + v + ")";
        default:
            fail("source modifier " + std::to_string(s.mod));
            return v;
        }
    }

    std::string dst_name(const Dst &d) {
        char buf[48];
        switch (d.type) {
        case R_TEMP:
            snprintf(buf, sizeof buf, "r[%u]", d.index & 31);
            return buf;
        case R_ADDR:
            if (!p.pixel)
                return "a0";
            snprintf(buf, sizeof buf, "t[%u]", d.index & 7);
            return buf;
        case R_RASTOUT:
            if (p.pixel)
                break;
            return d.index == 0 ? "opos" : d.index == 1 ? "ofog" : "opsize";
        case R_ATTROUT:
            if (p.pixel)
                break;
            snprintf(buf, sizeof buf, "od[%u]", d.index & 1);
            return buf;
        case R_TEXCRDOUT:
            if (p.pixel)
                break;
            if (p.major >= 3)
                return sm3_output(d.index);
            snprintf(buf, sizeof buf, "ot[%u]", d.index & 7);
            return buf;
        case R_COLOROUT:
            if (!p.pixel)
                break;
            color_outputs |= 1u << (d.index & 3);
            snprintf(buf, sizeof buf, "oc[%u]", d.index & 3);
            return buf;
        case R_DEPTHOUT:
            if (!p.pixel)
                break;
            writes_depth = true;
            return "odepth";
        default:
            break;
        }
        fail("destination register type " + std::to_string(d.type));
        return "junk";
    }

    void write(const Dst &d, const std::string &value) {
        std::string name = dst_name(d);
        std::string v = "(" + value + ")";
        if (d.shift > 0)
            v = "(" + v + " * " + std::to_string(1 << d.shift) + ".0)";
        else if (d.shift < 0)
            v = "(" + v + " / " + std::to_string(1 << -d.shift) + ".0)";
        if (d.saturate)
            v = saturate(v);
        // The address register holds integers, rounded half away from zero.
        if (!p.pixel && d.type == R_ADDR)
            v = (glsl() || wgsl() ? "d9_round" : "round") + v;
        uint32_t mask = d.mask & 0xf;
        if (mask == 0)
            return;
        if (mask == 0xf) {
            body += indent() + name + " = " + v + ";\n";
        } else if (wgsl()) {
            // WGSL assigns one component at a time.
            std::string line = indent() + "{ let w_ = " + v + ";";
            for (int k = 0; k < 4; ++k)
                if (mask >> k & 1)
                    line += std::string(" ") + name + "." + kComp[k] + " = w_." + kComp[k] + ";";
            body += line + " }\n";
        } else {
            body += indent() + "{ " + F4 + " w_ = " + v + "; " + name + mask_of(mask) + " = w_" +
                    mask_of(mask) + "; }\n";
        }
    }

    // ifc/breakc comparison.
    static const char *comparison(uint32_t ctrl) {
        switch (ctrl) {
        case 1:
            return ">";
        case 2:
            return "==";
        case 3:
            return ">=";
        case 4:
            return "<";
        case 5:
            return "!=";
        case 6:
            return "<=";
        default:
            return nullptr;
        }
    }

    // A sample from stage `stage` at `coord` (a four-float expression).
    std::string sample(uint32_t stage, const std::string &coord, bool projected, bool bias,
                       bool lod = false) {
        stage &= 15;
        stages.insert(stage);
        const std::string n = std::to_string(stage);
        std::string c = projected ? "(" + coord + " / (" + coord + ").w)" : coord;
        if (is_depth(stage))
            return wgsl() ? "d9_shadow(tx" + n + ", sm" + n + ", " + c + ")"
                          : "d9_shadow(tx" + n + ", " + c + ")";
        std::string lookup = kind_of(stage) == S_2D ? "(" + c + ").xy" : "(" + c + ").xyz";
        if (wgsl()) {
            std::string s = lod ? "textureSampleLevel(tx" + n + ", sm" + n + ", " + lookup + ", (" +
                                      coord + ").w)"
                            : bias ? "textureSampleBias(tx" + n + ", sm" + n + ", " + lookup +
                                         ", (" + coord + ").w)"
                                   : "textureSample(tx" + n + ", sm" + n + ", " + lookup + ")";
            return (variant.alpha_mask >> stage & 1) ? "d9_alpha(" + s + ")" : s;
        }
        if (glsl()) {
            if (lod)
                return "textureLod(tx" + n + ", " + lookup + ", (" + coord + ").w)";
            return "texture(tx" + n + ", " + lookup + (bias ? ", (" + coord + ").w)" : ")");
        }
        std::string extra = bias  ? ", bias((" + coord + ").w)"
                            : lod ? ", level((" + coord + ").w)"
                                  : "";
        return "tx" + n + ".sample(sm" + n + ", " + lookup + extra + ")";
    }

    bool is_depth(uint32_t stage) const {
        return kind_of(stage) == S_2D && (variant.depth_mask >> stage & 1);
    }

    uint32_t kind_of(uint32_t stage) const {
        if (p.major >= 2) {
            auto it = p.samplers.find(stage);
            return it != p.samplers.end() ? it->second : (uint32_t)S_2D;
        }
        return (variant.cube_mask >> stage & 1) ? (uint32_t)S_CUBE : (uint32_t)S_2D;
    }

    void matrix_op(const Inst &in) {
        int cols = (in.op == OP_M4X4 || in.op == OP_M4X3) ? 4 : 3;
        int rows = (in.op == OP_M4X4 || in.op == OP_M3X4) ? 4 : in.op == OP_M3X2 ? 2 : 3;
        std::string a = src(in.src[0]);
        std::string v = F4 + "(";
        for (int row = 0; row < 4; ++row) {
            if (row)
                v += ", ";
            if (row >= rows) {
                v += "0.0";
                continue;
            }
            Src m = in.src[1];
            m.index += (uint32_t)row;
            std::string line = src(m);
            v += cols == 4 ? "dot(" + a + ", " + line + ")"
                           : "dot((" + a + ").xyz, (" + line + ").xyz)";
        }
        v += ")";
        write(in.dst, v);
    }

    std::string discard() const {
        return glsl() ? "discard" : "discard_fragment()";
    }

    void instruction(const Inst &in) {
        auto S = [&](int i) { return src(in.src[i]); };
        switch (in.op) {
        case OP_NOP:
            return;
        case OP_MOV:
        case OP_MOVA:
            write(in.dst, S(0));
            return;
        case OP_DSX:
        case OP_DSY: {
            if (!p.pixel) {
                fail("screen derivatives require a pixel shader");
                return;
            }
            // D3D derivatives use adjacent fragment lanes in render-target
            // coordinates, including the top-to-bottom Y direction.
            const bool x = in.op == OP_DSX;
            const char *fn = wgsl()   ? (x ? "dpdx" : "dpdy")
                             : glsl() ? (x ? "dFdx" : "dFdy")
                                      : (x ? "dfdx" : "dfdy");
            write(in.dst, std::string(fn) + "(" + S(0) + ")");
            return;
        }
        case OP_ADD:
            write(in.dst, S(0) + " + " + S(1));
            return;
        case OP_SUB:
            write(in.dst, S(0) + " - " + S(1));
            return;
        case OP_MUL:
            write(in.dst, S(0) + " * " + S(1));
            return;
        case OP_MAD:
            write(in.dst, S(0) + " * " + S(1) + " + " + S(2));
            return;
        case OP_MIN:
            write(in.dst, "min(" + S(0) + ", " + S(1) + ")");
            return;
        case OP_MAX:
            write(in.dst, "max(" + S(0) + ", " + S(1) + ")");
            return;
        case OP_SLT:
            write(in.dst, choose(F4 + "(0.0)", F4 + "(1.0)", S(0), "<", S(1)));
            return;
        case OP_SGE:
            write(in.dst, choose(F4 + "(0.0)", F4 + "(1.0)", S(0), ">=", S(1)));
            return;
        case OP_DP3:
            write(in.dst, F4 + "(dot((" + S(0) + ").xyz, (" + S(1) + ").xyz))");
            return;
        case OP_DP4:
            write(in.dst, F4 + "(dot(" + S(0) + ", " + S(1) + "))");
            return;
        case OP_DP2ADD:
            write(in.dst, F4 + "(dot((" + S(0) + ").xy, (" + S(1) + ").xy) + (" + S(2) + ").x)");
            return;
        case OP_RCP:
            write(in.dst, F4 + "(1.0 / (" + S(0) + ").x)");
            return;
        case OP_RSQ:
            write(in.dst, F4 +
                              (glsl()   ? "(inversesqrt(abs(("
                               : wgsl() ? "(inverseSqrt(abs(("
                                        : "(rsqrt(abs((") +
                              S(0) + ").x)))");
            return;
        case OP_FRC:
            write(in.dst, "fract(" + S(0) + ")");
            return;
        case OP_ABS:
            write(in.dst, "abs(" + S(0) + ")");
            return;
        case OP_EXP:
        case OP_EXPP:
            // D3D assembly exponentials are base 2. vs_1_1 EXPP exposes
            // the integer/fractional decomposition in x/y, not a splat.
            if (in.op == OP_EXPP && !p.pixel && p.major == 1) {
                const std::string x = "(" + S(0) + ").x";
                write(in.dst, F4 + "(exp2(floor(" + x + ")), " + x + " - floor(" + x + "), exp2(" +
                                  x + "), 1.0)");
            } else {
                write(in.dst, F4 + "(exp2((" + S(0) + ").x))");
            }
            return;
        case OP_LOG:
        case OP_LOGP:
            write(in.dst, F4 + "(log2(abs((" + S(0) + ").x)))");
            return;
        case OP_POW:
            write(in.dst, F4 + "(pow(abs((" + S(0) + ").x), (" + S(1) + ").x))");
            return;
        case OP_NRM:
            write(in.dst, "d9_nrm(" + S(0) + ")");
            return;
        case OP_CRS:
            write(in.dst, F4 + "(cross((" + S(0) + ").xyz, (" + S(1) + ").xyz), 0.0)");
            return;
        case OP_SGN:
            write(in.dst, "sign(" + S(0) + ")");
            return;
        case OP_LRP:
            write(in.dst, "mix(" + S(2) + ", " + S(1) + ", " + S(0) + ")");
            return;
        case OP_CMP:
            write(in.dst, choose(S(2), S(1), S(0), ">=", splat("0.0")));
            return;
        case OP_CND:
            if (p.major == 1 && p.minor == 4)
                write(in.dst, choose(S(2), S(1), S(0), ">", splat("0.5")));
            else if (wgsl())
                write(in.dst, "select(" + S(2) + ", " + S(1) + ", (" + S(0) + ").w > 0.5)");
            else
                write(in.dst, "((" + S(0) + ").w > 0.5 ? " + S(1) + " : " + S(2) + ")");
            return;
        case OP_LIT:
            write(in.dst, "d9_lit(" + S(0) + ")");
            return;
        case OP_DST:
            write(in.dst, F4 + "(1.0, (" + S(0) + ").y * (" + S(1) + ").y, (" + S(0) + ").z, (" +
                              S(1) + ").w)");
            return;
        case OP_SINCOS:
            write(in.dst, F4 + "(cos((" + S(0) + ").x), sin((" + S(0) + ").x), 0.0, 0.0)");
            return;
        case OP_M4X4:
        case OP_M4X3:
        case OP_M3X4:
        case OP_M3X3:
        case OP_M3X2:
            matrix_op(in);
            return;
        case OP_TEXCOORD:
            if (in.nsrc) // 1.4 texcrd rN, src
                write(in.dst, F4 + "((" + S(0) + ").xyz, 1.0)");
            else {
                char buf[64];
                snprintf(buf, sizeof buf,
                         glsl()   ? "vec4(clamp(tc[%u].xyz, 0.0, 1.0), 1.0)"
                         : wgsl() ? "vec4f(saturate(tc[%u].xyz), 1.0)"
                                  : "float4(saturate(tc[%u].xyz), 1.0)",
                         in.dst.index & 7);
                write(in.dst, buf);
            }
            return;
        case OP_TEXKILL: {
            Src s;
            s.type = in.dst.type;
            s.index = in.dst.index;
            std::string v = base(s);
            if (wgsl()) {
                if (p.major >= 2)
                    body += indent() + "if (any(" + v + " < vec4f(0.0))) { discard; }\n";
                else
                    body += indent() + "if (any((" + v + ").xyz < vec3f(0.0))) { discard; }\n";
            } else if (glsl()) {
                if (p.major >= 2)
                    body += indent() + "if (any(lessThan(" + v + ", vec4(0.0)))) discard;\n";
                else
                    body += indent() + "if (any(lessThan((" + v + ").xyz, vec3(0.0)))) discard;\n";
            } else if (p.major >= 2) {
                body += indent() + "if (any(" + v + " < 0.0)) discard_fragment();\n";
            } else {
                body += indent() + "if (any((" + v + ").xyz < 0.0)) discard_fragment();\n";
            }
            return;
        }
        case OP_TEX:
            if (p.major >= 2) {
                write(in.dst, sample(in.src[1].index, S(0), in.ctrl == 1, in.ctrl == 2));
            } else if (in.nsrc) { // 1.4 texld rN, src
                uint32_t n = in.dst.index & 7;
                write(in.dst, sample(n, S(0), variant.projected_mask >> n & 1, false));
            } else { // tex tN
                uint32_t n = in.dst.index & 7;
                char buf[32];
                snprintf(buf, sizeof buf, "tc[%u]", n);
                write(in.dst, sample(n, buf, variant.projected_mask >> n & 1, false));
            }
            return;
        case OP_TEXREG2AR:
        case OP_TEXREG2GB:
            // ps_1_x dependent lookup: source alpha/red or green/blue are U/V,
            // and the destination register selects the texture stage.
            write(in.dst,
                  sample(in.dst.index, "(" + S(0) + (in.op == OP_TEXREG2AR ? ").wxxx" : ").yzzz"),
                         false, false));
            return;
        case OP_TEXLDL:
            write(in.dst, sample(in.src[1].index, S(0), false, false, true));
            return;
        case OP_REP: {
            std::string n = "rep" + std::to_string(depth);
            if (wgsl()) {
                body += indent() + "{ let " + n + "_end = clamp(i32((" + S(0) +
                        ").x), 0, 255); for (var " + n + " = 0; " + n + " < " + n + "_end; " + n +
                        "++) {\n";
                ++depth;
                ++loop_depth;
                return;
            }
            body += indent() + "for (int " + n + " = 0, " + n + "_end = clamp(int((" + S(0) +
                    ").x), 0, 255); " + n + " < " + n + "_end; ++" + n + ") {\n";
            ++depth;
            ++loop_depth;
            return;
        }
        case OP_LOOP: {
            // loop aL, iN: iN.x iterations, aL from iN.y in steps of iN.z.
            std::string n = "lp" + std::to_string(depth);
            std::string i = S(1);
            if (wgsl()) {
                body += indent() + "{ let " + n + "_saved = aL; aL = i32((" + i + ").y); let " + n +
                        "_end = clamp(i32((" + i + ").x), 0, 255); let " + n + "_step = i32((" + i +
                        ").z); var " + n + " = 0;\n";
                body += indent() + "loop { if (" + n + " >= " + n + "_end) { break; }\n";
                ++depth;
                ++loop_depth;
                return;
            }
            body += indent() + "{ int " + n + "_saved = aL; aL = int((" + i + ").y);\n";
            body += indent() + "for (int " + n + " = 0, " + n + "_end = clamp(int((" + i +
                    ").x), 0, 255); " + n + " < " + n + "_end; ++" + n + ", aL += int((" + i +
                    ").z)) {\n";
            ++depth;
            ++loop_depth;
            return;
        }
        case OP_ENDREP:
        case OP_ENDLOOP:
            if (loop_depth <= 0 || depth <= 0) {
                fail("unbalanced end of loop");
                return;
            }
            --depth;
            --loop_depth;
            if (wgsl()) {
                const std::string n = "lp" + std::to_string(depth);
                body += indent() + (in.op == OP_ENDLOOP ? "continuing { " + n + "++; aL += " + n +
                                                              "_step; } } aL = " + n + "_saved; }\n"
                                                        : std::string("} }\n"));
                return;
            }
            body += indent() + (in.op == OP_ENDLOOP
                                    ? "} aL = lp" + std::to_string(depth) + "_saved; }\n"
                                    : std::string("}\n"));
            return;
        case OP_IF:
            body += indent() + "if ((" + S(0) + ").x != 0.0) {\n";
            ++depth;
            return;
        case OP_IFC: {
            const char *op = comparison(in.ctrl);
            if (!op) {
                fail("ifc comparison " + std::to_string(in.ctrl));
                return;
            }
            body += indent() + "if ((" + S(0) + ").x " + op + " (" + S(1) + ").x) {\n";
            ++depth;
            return;
        }
        case OP_ELSE:
            if (depth <= 0) {
                fail("else outside if");
                return;
            }
            body += std::string(4 + 4 * (size_t)(depth - 1), ' ') + "} else {\n";
            return;
        case OP_ENDIF:
            if (depth <= 0) {
                fail("unbalanced endif");
                return;
            }
            --depth;
            body += indent() + "}\n";
            return;
        case OP_BREAK:
            if (loop_depth <= 0) {
                fail("break outside a loop");
                return;
            }
            body += indent() + "break;\n";
            return;
        case OP_BREAKC: {
            const char *op = comparison(in.ctrl);
            if (!op || loop_depth <= 0) {
                fail("breakc");
                return;
            }
            body += indent() + "if ((" + S(0) + ").x " + op + " (" + S(1) + ").x) " +
                    (wgsl() ? "{ break; }\n" : "break;\n");
            return;
        }
        case OP_TEXDP3: {
            char buf[48];
            snprintf(buf, sizeof buf, "t[%u]", in.dst.index & 7);
            write(in.dst, F4 + "(dot((" + S(0) + ").xyz, (" + std::string(buf) + ").xyz))");
            return;
        }
        default:
            fail("instruction " + std::to_string(in.op));
            return;
        }
    }
};

// Walks the program; false with the reason when it cannot be translated.
bool generate(Gen &g, std::string *why) {
    for (const Inst &in : g.p.code)
        g.instruction(in);
    if (!g.failed && g.depth != 0)
        g.fail("unclosed loop or if");
    if (g.failed) {
        if (why)
            *why = g.why;
        return false;
    }
    return true;
}

// The input registers a vertex shader declares, in declaration order.
std::vector<uint32_t> input_registers(const Program &p) {
    std::vector<uint32_t> order;
    std::set<uint32_t> seen;
    for (const DclIn &d : p.inputs)
        if (d.type == R_INPUT && seen.insert(d.index & 15).second)
            order.push_back(d.index & 15);
    return order;
}

// Remaster run 10 fused pool shader: the pool varyings, added to D9V2F in pool variants only (the struct text of
// every other shader is unchanged).
const char *kPoolStructEnd = "    float fogz;\n};\n";
const char *kPoolStructEndPool = "    float fogz;\n    float4 pool_p; float4 pool_n; float4 pool_uv; float4 pool_t;\n};\n";
void pool_varyings(std::string *s) {
    const size_t at = s->find(kPoolStructEnd);
    if (at != std::string::npos)
        s->replace(at, strlen(kPoolStructEnd), kPoolStructEndPool);
}
// The input registers of POSITION0, NORMAL0, TEXCOORD0 and (n == 4) TANGENT0 (false when one is not declared).
bool pool_inputs(const Program &p, uint32_t reg[4], int n) {
    static const uint32_t usage[4] = {0, 3, 5, 6};
    for (int k = 0; k < n; ++k) {
        reg[k] = 99;
        for (const DclIn &d : p.inputs)
            if (d.type == R_INPUT && d.usage == usage[k] && d.usage_index == 0) {
                reg[k] = d.index & 15;
                break;
            }
        if (reg[k] == 99)
            return false;
    }
    return true;
}

} // namespace

bool vertex_source(const Program &p, std::string *out, std::string *why) {
    return vertex_source(p, VertexVariant{}, out, why);
}

// Remaster run 13 front-end floor shadows (review/run13/fe-shadows-design.md): the receiver analysis
// (vertex_clip_shadow) and the floor's two patches (VertexVariant / PixelVariant fefloor).
static bool fef_vs_body(const std::string &src, std::string *body) {
    size_t b = src.rfind("* P.ascale[");
    const size_t e = src.find("    D9V2F o;\n");
    if (b == std::string::npos || e == std::string::npos || e < b)
        return false;
    b = src.find('\n', b);
    if (b == std::string::npos || b > e)
        return false;
    *body = src.substr(b + 1, e - b - 1);
    return true;
}
struct FefRows {
    int in = -1;   // the input register of the dot lines (-2: more than one)
    int base[4] = {-1, -1, -1, -1};
    int writes = 0;   // every write to the register, dot lines or not
};
// Remaster run 14 foundation F1: the parse shared by fef_clip_shadow and vertex_clip_regs (the dot lines per output).
static bool fef_parse_rows(const std::string &src, std::map<std::string, FefRows> *out) {
    std::string body;
    if (!fef_vs_body(src, &body))
        return false;
    std::map<std::string, FefRows> &w = *out;   // "opos", "ot[3]"
    for (size_t pos = 0; pos < body.size();) {
        size_t nl = body.find('\n', pos);
        if (nl == std::string::npos)
            nl = body.size();
        const std::string line = body.substr(pos, nl - pos);
        pos = nl + 1;
        for (const char *tok : {"opos", "ot["}) {
            for (size_t at = line.find(tok); at != std::string::npos; at = line.find(tok, at + 1)) {
                const char prev = at ? line[at - 1] : ' ';
                if (isalnum((unsigned char)prev) || prev == '_' || prev == '.')
                    continue;
                std::string name;
                size_t q;
                if (tok[1] == 'p') {
                    name = "opos";
                    q = at + 4;
                } else {
                    const size_t r = line.find(']', at);
                    if (r == std::string::npos)
                        continue;
                    name = line.substr(at, r - at + 1);
                    q = r + 1;
                }
                if (q < line.size() && line[q] == '.')
                    for (++q; q < line.size() && strchr("xyzw", line[q]); ++q) {
                    }
                if (line.compare(q, 3, " = ") == 0)
                    ++w[name].writes;
            }
        }
        unsigned vi = 0, ci = 0;
        char dst[64] = "", comp = 0;
        int n = -1;
        if (sscanf(line.c_str(), " { float4 w_ = (float4(dot((vv[%u].xyzw), (c[%u].xyzw)))); %63[^ ] = w_.%c; }%n", &vi,
                   &ci, dst, &comp, &n) == 4 &&
            n == (int)line.size()) {
            const std::string d = dst;
            const size_t dot = d.rfind('.');
            static const char kXyzw[] = "xyzw";
            const char *k = comp ? strchr(kXyzw, comp) : nullptr;
            if (dot == std::string::npos || d.size() != dot + 2 || d[dot + 1] != comp || !k || !comp)
                continue;
            FefRows &r = w[d.substr(0, dot)];
            r.in = r.in == -1 ? (int)vi : r.in == (int)vi ? r.in : -2;
            r.base[k - kXyzw] = (int)ci;
        }
    }
    return true;
}
static int fef_rows(const FefRows &r) {
    if (r.in < 0 || r.writes != 4)
        return -1;
    for (int k = 1; k < 4; ++k)
        if (r.base[0] < 0 || r.base[k] != r.base[0] + k)
            return -1;
    return r.base[0];
}
static bool fef_clip_shadow(const std::string &src, uint32_t *clip, uint32_t *shadow) {
    std::map<std::string, FefRows> w;
    if (!fef_parse_rows(src, &w))
        return false;
    auto rows = fef_rows;
    const auto o = w.find("opos");
    if (o == w.end())
        return false;
    const int a = rows(o->second);
    int b = -1, found = 0;
    for (const auto &kv : w)
        if (kv.first.compare(0, 3, "ot[") == 0 && kv.second.in == o->second.in) {
            const int x = rows(kv.second);
            if (x >= 0) {
                b = x;
                ++found;
            }
        }
    if (a < 0 || found != 1)
        return false;
    *clip = (uint32_t)a;
    *shadow = (uint32_t)b;
    return true;
}
bool vertex_clip_shadow(const Program &p, uint32_t *clip, uint32_t *shadow) {
    std::string src, why;
    return p.ok && !p.pixel && vertex_source(p, VertexVariant{}, &src, &why) && fef_clip_shadow(src, clip, shadow);
}
// Remaster run 14 foundation F1 (review/run14/foundation-design.md 1): the generic half of vertex_clip_shadow - oPos
// written as 4 dp4 of one input with c[clip..clip+3] (consecutive registers, nothing else writes oPos). False for
// palettes / skinning (relative addressing never matches the dot-line form) and anything else.
bool vertex_clip_regs(const Program &p, uint32_t *clip) {
    std::string src, why;
    std::map<std::string, FefRows> w;
    if (!p.ok || p.pixel || !vertex_source(p, VertexVariant{}, &src, &why) || !fef_parse_rows(src, &w))
        return false;
    const auto o = w.find("opos");
    const int a = o == w.end() ? -1 : fef_rows(o->second);
    if (a < 0)
        return false;
    *clip = (uint32_t)a;
    return true;
}
// t7 = Mclip x opos (the D3D clip position before the half-pixel fixup); Mclip rows at buffer(18).
static bool fefloor_vs_patch(std::string *s) {
    static const char kSig[] = "                     constant D9VSParams &P [[buffer(17)]]) {\n";
    static const char kOut[] = "    D9V2F o;\n    o.pos = opos;\n";
    std::string body;
    if (!fef_vs_body(*s, &body) || body.find("ot[7]") != std::string::npos)
        return false;
    const size_t a = s->find(kSig), b = s->find(kOut);
    if (a == std::string::npos || b == std::string::npos || b < a || s->find(kSig, a + 1) != std::string::npos ||
        s->find(kOut, b + 1) != std::string::npos)
        return false;
    s->insert(b, "    ot[7] = float4(dot(opos, fef[0]), dot(opos, fef[1]), dot(opos, fef[2]), dot(opos, fef[3]));\n");
    s->replace(a, strlen(kSig),
               "                     constant D9VSParams &P [[buffer(17)]],\n"
               "                     constant float4 *fef [[buffer(18)]]) {\n");
    return true;
}
// The colour before the alpha test: darkened by the front-end shadow map (texture 18) at vin.t7, projected.
static bool fefloor_ps_patch(std::string *s, uint8_t mode) {
    static const char kMain[] = "fragment D9PSOut ps_main(";
    static const char kOpen[] = ") {\n    float4 r[32] = {};\n";
    static const char kAt[] = "    (void)aL;\n";
    const size_t f = s->find(kMain);
    if (f == std::string::npos || mode < 1 || mode > 4)
        return false;
    const size_t o = s->find(kOpen, f), at = s->find(kAt, f), col = s->find("    float4 col = ", f);
    if (o == std::string::npos || at == std::string::npos || col == std::string::npos || !(o < col && col < at) ||
        s->find(kAt, at + 1) != std::string::npos || s->find("static float4 d9_shadow(") == std::string::npos ||
        s->find("t[7]", o) != std::string::npos || s->find("tc[7]", o) != std::string::npos ||
        s->find("d9_fef", 0) != std::string::npos)
        return false;
    std::string blk =
        "    {   // run 13 front-end floor shadows\n"
        "        const float4 fef_q = vin.t7;\n"
        "        float fef_lit = 1.0;\n"
        "        bool fef_in = false;\n"
        "        if (fef_q.w > 1.0e-6) {\n"
        "            const float3 q = fef_q.xyz / fef_q.w;\n"
        "            fef_in = q.x > 0.0 && q.x < 1.0 && q.y > 0.0 && q.y < 1.0 && q.z > 0.0 && q.z < 1.0;\n"
        "            if (fef_in)\n"
        "                fef_lit = d9_shadow(d9_fef, float4(q, 1.0)).r;\n"
        "        }\n";
    blk += mode == 1   ? "        col.rgb *= 1.0 - P.pad1 * (1.0 - fef_lit);\n"
           : mode == 2 ? "        (void)fef_lit;\n"
           : mode == 3 ? "        col.rgb = fef_in ? float3(0.0, 0.25 + 0.75 * fef_lit, 0.0) : float3(1.0, 0.0, 0.0);\n"
                       // run 15 TEST ONLY: the map depth m against the floor's own depth z (red 0.5 + 20 (z - m):
                       // bright = behind the map = shadowed), map depth bands (green), floor depth bands (blue)
                       : "        if (fef_in) {\n"
                         "            constexpr sampler fef_s(coord::normalized, filter::nearest, address::clamp_to_edge);\n"
                         "            const float3 q = fef_q.xyz / fef_q.w;\n"
                         "            const float m = d9_fef.sample(fef_s, q.xy);\n"
                         "            col.rgb = float3(saturate(0.5 + 20.0 * (q.z - m)), fract(m * 10.0), fract(q.z * 10.0));\n"
                         "        } else\n"
                         "            col.rgb = float3(1.0, 0.0, 1.0);\n";
    blk += "    }\n";
    s->insert(at, blk);
    s->insert(o, ",\n                         depth2d<float> d9_fef [[texture(18)]]");
    return true;
}


// Remaster run 13 car reflections (owner 7 Oct: car paint "at a real intensity"; review/run13/car-env-design.md).
// The car effect's vertex shader makes the environment-map strength as EnvmapMin + pow(N.V, EnvmapPower) x
// EnvmapRange (lit(N.V, -, power).z x Range + Min), then doubles it with the baked vertex colour; the colour
// interpolator clamps it to 1. Paint (Min 3.5, Range -3.3, Power 0.15) reaches 1 already at N.V ~0.35, so a hood seen
// from the driver's seat is a full-strength mirror of the sky. The patch keeps each material's face-on value
// (Min + Range) and its grazing limit (the larger of Min and Min + Range, at most 1) and joins them with Schlick's
// Fresnel curve: s = f + (g - f) (1 - N.V)^5. Chrome (Range 0) and matte (0, 0) keep their constant; only the shape of
// the paint's rise changes. Gain RECOMP_REMASTER_CARENV_GAIN (TEST ONLY, default 1). The anchors, by structure: the
// unique lit(r[A].xxzz) -> r[L] line and the unique (r[L].zzzz) x r[X].w + c[M].x -> r[D].w line after it.
struct CarEnvAnchors {
    size_t pos = 0, len = 0;
    int a = -1, x = -1, m = -1, d = -1;
};
int creg(const std::string &s, size_t p, char kind) {   // the number in "<kind>[<n>]" at p, or -1
    if (p + 1 >= s.size() || s[p] != kind || s[p + 1] != '[')
        return -1;
    int v = 0, n = 0;
    for (p += 2; p < s.size() && s[p] >= '0' && s[p] <= '9'; ++p, ++n)
        v = v * 10 + (s[p] - '0');
    return n && p < s.size() && s[p] == ']' ? v : -1;
}
bool carenv_find(const std::string &s, CarEnvAnchors *a) {
    const std::string lit = " = (d9_lit((r[";
    size_t lp = std::string::npos;
    int count = 0;
    for (size_t p = s.find(lit); p != std::string::npos; p = s.find(lit, p + 1)) {
        const size_t e = s.find(".xxzz)));\n", p);
        if (e != std::string::npos && e < s.find('\n', p)) {
            ++count;
            lp = p;
        }
    }
    if (count != 1)
        return false;
    const size_t ls = s.rfind("    r[", lp);
    const int L = ls == std::string::npos ? -1 : creg(s, ls + 4, 'r');
    a->a = creg(s, lp + lit.size() - 2, 'r');
    if (L < 0 || a->a < 0)
        return false;
    const std::string head = "    { float4 w_ = ((r[" + std::to_string(L) + "].zzzz) * (r[";
    const size_t hp = s.find(head, lp);
    if (hp == std::string::npos || s.find(head, hp + 1) != std::string::npos)
        return false;
    const size_t le = s.find('\n', hp);
    if (le == std::string::npos)
        return false;
    const std::string line = s.substr(hp, le + 1 - hp);
    a->x = creg(line, head.size() - 2, 'r');
    const size_t cm = line.find(".wwww) + (c[");
    a->m = cm == std::string::npos ? -1 : creg(line, cm + 10, 'c');
    const size_t dd = line.find(".xxxx)); r[");
    a->d = dd == std::string::npos ? -1 : creg(line, dd + 9, 'r');
    if (a->x < 0 || a->m < 0 || a->d < 0)
        return false;
    const std::string want = head + std::to_string(a->x) + "].wwww) + (c[" + std::to_string(a->m) + "].xxxx)); r[" +
                             std::to_string(a->d) + "].w = w_.w; }\n";
    if (line != want)
        return false;
    a->pos = hp;
    a->len = line.size();
    return true;
}
bool carenv_patch(std::string *s) {
    CarEnvAnchors a;
    if (!carenv_find(*s, &a))
        return false;
    static const float gain = [] {
        float g = 1.0f;
        if (const char *v = std::getenv("RECOMP_REMASTER_CARENV_GAIN"); v && *v) {
            const float f = std::strtof(v, nullptr);
            if (f >= 0.0f && f <= 4.0f)
                g = f;
        }
        return g;
    }();
    char k[32];
    snprintf(k, sizeof k, "%d.%04d", int(gain), int((gain - float(int(gain))) * 10000.0f + 0.5f) % 10000);
    const std::string A = std::to_string(a.a), X = std::to_string(a.x), M = std::to_string(a.m),
                      D = std::to_string(a.d);
    s->replace(a.pos, a.len,
               "    { float ce_nv = saturate(r[" + A + "].x);\n"
               "      float ce_face = c[" + M + "].x + r[" + X + "].w;\n"
               "      float ce_f = saturate(ce_face), ce_g = saturate(max(c[" + M + "].x, ce_face));\n"
               "      r[" + D + "].w = (ce_f + (ce_g - ce_f) * pow(1.0 - ce_nv, 5.0)) * " + k + "; }\n");
    return true;
}
// Remaster run 13 Xbox 360 car paint (owner 8 Oct: our own version of the 360 shaders; review/run13/x360-shader-map.md
// CAR). The PC car pixel shader (IDI_CAR_FX, census 8 Oct: ps 2cd941.. with 8 shadow taps, 393123.. without) already
// has the 360's per-pixel highlight shape: R = 2 (N.L) N - L, s = 2 sat(R.V)^16 x specular colour x shadow, plus
// env cube x strength. It lacks three 360 paint layers, added here in our own code: (1) the metallic flake: the
// normal feeding R is tilted by an own trilinear value noise (cells of 1/64 object unit at the object position t6.xyz)
// times sat(1.2 - 0.25 d) x 0.04 x RECOMP_REMASTER_CARENV_FLAKE (TEST ONLY, default 1; d = |eye - position| in t6.w);
// (2) the hot spot pow(R.V, 80) x RECOMP_REMASTER_CARENV_HOTSPOT (TEST ONLY, default 2) added to s (so under the
// specular colour and the shadow); (3) the environment bloom env += m x env x 2 x RECOMP_REMASTER_CARENV_BLOOM (TEST
// ONLY, default 1), m = our own bright mask smoothstep(0.75, 1, max(env.rgb)) (the PC's dynamic cube alpha is not a
// bloom tag; RECOMP_REMASTER_CARENV_BLOOMALPHA=1 uses env.a as the 360 did). Anchors by structure: the unique
// saturated dot of a register with a t[] input (R.V, -> r[S].x); the reflect MAD "(r[a].wwww) * (r[R].xyzw) +
// (-(t[L].xyzw))" before it; the normal copy "((t[N].xyzw)); r[R].xyz" before that; the doubling line four lines
// after the dot; the unique cube sample at a t[] coordinate (.xyz).
struct CarPaintAnchors {
    size_t ncopy = 0, ncopy_len = 0, dbl = 0, dbl_len = 0, env_end = 0;
    int r = -1, n = -1, s = -1, h = -1, e = -1;
};
static std::string line_at(const std::string &s, size_t p) {
    const size_t e = s.find('\n', p);
    return e == std::string::npos ? std::string() : s.substr(p, e + 1 - p);
}
bool carpaint_find(const std::string &s, CarPaintAnchors *a) {
    const std::string dotk = "    { float4 w_ = saturate(float4(dot(((r[";
    size_t dp = std::string::npos;
    int count = 0;
    for (size_t p = s.find(dotk); p != std::string::npos; p = s.find(dotk, p + 1))
        if (line_at(s, p).find(".xyz, ((t[") != std::string::npos) {
            ++count;
            dp = p;
        }
    if (count != 1)
        return false;
    const std::string dl = line_at(s, dp);
    a->r = creg(dl, dotk.size() - 2, 'r');
    const size_t tq = dl.find(".xyz, ((t[");
    const int v = creg(dl, tq + 8, 't');
    const size_t sx = dl.find(".xyz))); r[");
    a->s = sx == std::string::npos ? -1 : creg(dl, sx + 9, 'r');
    if (a->r < 0 || v < 0 || a->s < 0 ||
        dl != dotk + std::to_string(a->r) + "].xyzw)).xyz, ((t[" + std::to_string(v) + "].xyzw)).xyz))); r[" +
                  std::to_string(a->s) + "].x = w_.x; }\n")
        return false;
    const std::string R = std::to_string(a->r);
    // the reflect MAD: the last line before the dot that writes r[R].xyz; it must be the reflection
    const size_t mw = s.rfind("); r[" + R + "].xyz = w_.xyz; }\n", dp);
    const size_t mp = mw == std::string::npos ? std::string::npos : s.rfind("    { float4 w_ = ", mw);
    if (mp == std::string::npos || dp - mp > 600)
        return false;
    const std::string ml = line_at(s, mp);
    if (ml.find(".wwww) * (r[" + R + "].xyzw) + (-(t[") == std::string::npos ||
        ml.find("].xyzw))); r[" + R + "].xyz = w_.xyz; }\n") == std::string::npos)
        return false;
    // the normal copy: the last "((t[N].xyzw)); r[R].xyz" line before the MAD, with no other write of r[R] between
    const std::string tail = "].xyzw)); r[" + R + "].xyz = w_.xyz; }\n";
    size_t np = mp;
    for (;;) {
        np = s.rfind("    { float4 w_ = ((t[", np - 1);
        if (np == std::string::npos || mp - np > 2000)
            return false;
        const std::string nl = line_at(s, np);
        if (nl.size() > tail.size() && nl.compare(nl.size() - tail.size(), tail.size(), tail) == 0) {
            a->n = creg(nl, 20, 't');
            if (a->n < 0 || nl != "    { float4 w_ = ((t[" + std::to_string(a->n) + tail)
                return false;
            a->ncopy = np;
            a->ncopy_len = nl.size();
            break;
        }
    }
    {
        const std::string mid = s.substr(np + a->ncopy_len, mp - np - a->ncopy_len);
        if (mid.find("); r[" + R + "]") != std::string::npos || mid.find("    r[" + R + "] =") != std::string::npos)
            return false;
    }
    // four squaring lines, then the doubling line
    size_t q = dp + dl.size();
    for (int i = 0; i < 4; ++i) {
        const std::string sl = line_at(s, q);
        if (sl.empty() || sl.find(") * (r[") == std::string::npos || sl.find(".w = w_.w; }\n") == std::string::npos)
            return false;
        q += sl.size();
    }
    {
        const std::string bl = line_at(s, q);
        const int x = creg(bl, 20, 'r');
        const size_t hx = bl.find(".wwww)); r[");
        a->h = hx == std::string::npos ? -1 : creg(bl, hx + 9, 'r');
        if (x < 0 || a->h < 0 ||
            bl != "    { float4 w_ = ((r[" + std::to_string(x) + "].wwww) + (r[" + std::to_string(x) + "].wwww)); r[" +
                      std::to_string(a->h) + "].w = w_.w; }\n")
            return false;
        a->dbl = q;
        a->dbl_len = bl.size();
    }
    // the cube sample
    count = 0;
    for (size_t p = s.find("    r["); p != std::string::npos; p = s.find("    r[", p + 1)) {
        const std::string el = line_at(s, p);
        if (el.find(".sample(sm") == std::string::npos || el.find(".xyzw)).xyz));\n") == std::string::npos)
            continue;
        const int e = creg(el, 4, 'r');
        const size_t tp = el.find("(tx");
        if (e < 0 || tp == std::string::npos)
            continue;
        const int k = atoi(el.c_str() + tp + 3);
        const size_t cp = el.find("((t[");
        const int j = cp == std::string::npos ? -1 : creg(el, cp + 2, 't');
        if (j < 0 || el != "    r[" + std::to_string(e) + "] = (tx" + std::to_string(k) + ".sample(sm" +
                                std::to_string(k) + ", ((t[" + std::to_string(j) + "].xyzw)).xyz));\n")
            continue;
        if (s.find("texturecube<float> tx" + std::to_string(k) + " ") == std::string::npos)
            continue;
        ++count;
        a->e = e;
        a->env_end = p + el.size();
    }
    if (count != 1 || a->env_end > a->ncopy || s.find("t[6]") != std::string::npos || s.find("t[7]") != std::string::npos)
        return false;
    return a->ncopy < a->dbl;
}
static std::string cp_num(const char *env, float def, float lo, float hi) {
    float f = def;
    if (const char *v = std::getenv(env); v && *v) {
        const float x = std::strtof(v, nullptr);
        if (x >= lo && x <= hi)
            f = x;
    }
    char k[32];
    snprintf(k, sizeof k, "%.4f", f);
    return k;
}
bool carpaint_patch(std::string *s) {
    CarPaintAnchors a;
    if (!carpaint_find(*s, &a))
        return false;
    static const std::string flake = cp_num("RECOMP_REMASTER_CARENV_FLAKE", 1.0f, 0.0f, 50.0f),
                             hot = cp_num("RECOMP_REMASTER_CARENV_HOTSPOT", 2.0f, 0.0f, 10.0f),
                             bloom = cp_num("RECOMP_REMASTER_CARENV_BLOOM", 1.0f, 0.0f, 4.0f);
    static const bool bloom_alpha = [] {
        const char *v = std::getenv("RECOMP_REMASTER_CARENV_BLOOMALPHA");
        return v && *v == '1';
    }();
    const std::string R = std::to_string(a.r), N = std::to_string(a.n), S = std::to_string(a.s),
                      H = std::to_string(a.h), E = std::to_string(a.e);
    // back to front: doubling line, normal copy, env sample end
    s->insert(a.dbl + a.dbl_len, "    r[" + H + "].w += pow(r[" + S + "].x, 80.0) * " + hot + ";\n");
    s->replace(a.ncopy, a.ncopy_len,
               "    { float3 cp_p = t[6].xyz * 64.0, cp_i = floor(cp_p), cp_t = cp_p - cp_i;\n"
               "      cp_t = cp_t * cp_t * (3.0 - 2.0 * cp_t);\n"
               "      float3 cp_c[8];\n"
               "      for (int k = 0; k < 8; ++k) {\n"
               "          int3 q = int3(cp_i) + int3(k & 1, (k >> 1) & 1, k >> 2);\n"
               "          uint h = uint(q.x) * 73856093u ^ uint(q.y) * 19349663u ^ uint(q.z) * 83492791u;\n"
               "          h = (h ^ (h >> 16)) * 0x7feb352du; h = (h ^ (h >> 15)) * 0x846ca68bu; h ^= h >> 16;\n"
               "          cp_c[k] = float3(h & 1023u, (h >> 10) & 1023u, (h >> 20) & 1023u) * (1.0 / 1023.0);\n"
               "      }\n"
               "      float3 cp_x0 = mix(mix(cp_c[0], cp_c[1], cp_t.x), mix(cp_c[2], cp_c[3], cp_t.x), cp_t.y);\n"
               "      float3 cp_x1 = mix(mix(cp_c[4], cp_c[5], cp_t.x), mix(cp_c[6], cp_c[7], cp_t.x), cp_t.y);\n"
               "      float3 cp_fl = (mix(cp_x0, cp_x1, cp_t.z) - 0.5) * 2.0;\n"
               "      float cp_k = saturate(1.2 - 0.25 * t[6].w) * 0.04 * " + flake + ";\n"
               "      r[" + R + "].xyz = normalize(normalize(t[" + N + "].xyz) + cp_fl * cp_k); }\n");
    s->insert(a.env_end, std::string("    r[") + E + "].xyz += " +
                             (bloom_alpha ? "r[" + E + "].w" : "smoothstep(0.75, 1.0, max(r[" + E + "].x, max(r[" + E +
                                                               "].y, r[" + E + "].z)))") +
                             " * r[" + E + "].xyz * (2.0 * " + bloom + ");\n");
    return true;
}
bool pixel_is_carpaint(const Program &p) {
    std::string src, why;
    if (!p.pixel || p.major < 2 || !pixel_source(p, PixelVariant{}, &src, &why))
        return false;
    CarPaintAnchors a;
    return carpaint_find(src, &a);
}
bool vertex_is_carenv(const Program &p) {
    std::string src, why;
    if (p.pixel || !vertex_source(p, VertexVariant{}, &src, &why))
        return false;
    CarEnvAnchors a;
    return carenv_find(src, &a);
}

// Remaster run 13 Xbox 360 sky, our own version (owner 8 Oct; review/run13/x360-shader-map.md SKY). The PC sky effect
// (IDI_SKYBOX_FX) draws the same layers as the 360 sky in passes: p0 = the scattering in-scatter base (per-vertex, t3)
// under whispy layer A (stages 0 and 2: one texture at the scrolled and the shifted coordinate) and its cap (stage 1);
// later passes blend the overcast layer and its cap by alpha, already alpha x CloudIntensity.y / .z as on the 360. The
// 360 order differs in p0 only: base -> A (alpha A x A' x 2 x CI.x) -> cap (alpha cap x CI.x), each a lerp, then
// x Brightness (2) x 0.5 x vertex colour; the PC mixes (A + cap) / 2 by the A alpha. sky_ps_patch rewrites p0's
// combine to the 360 order. sky_vs_patch gives the in-scatter the 360 horizon distance (10000 x (2 - height / 3300))
// instead of the PC's one fixed distance. Anchors by structure; both refuse anything else.
namespace {
bool sky_line(const std::string &s, const std::string &line) { return s.find(line) != std::string::npos; }
} // namespace
bool sky_ps_patch(std::string *s) {
    static const char *const kSamples[] = {
        "    t[0] = (tx0.sample(sm0, (tc[0]).xy));\n", "    t[1] = (tx1.sample(sm1, (tc[1]).xy));\n",
        "    t[2] = (tx2.sample(sm2, (tc[2]).xy));\n", "    t[3] = (float4(saturate(tc[3].xyz), 1.0));\n"};
    for (const char *l : kSamples)
        if (!sky_line(*s, l))
            return false;
    const size_t body = s->find(kSamples[3]) + strlen(kSamples[3]);
    const size_t end = s->find("    float4 col = saturate(r[0]);\n", body);
    if (end == std::string::npos || s->find("tx3") != std::string::npos || s->find("iv[1]") != std::string::npos)
        return false;
    // The PC combine: the cloud-intensity selector dot(c[D].xyz, c[C].xyz) (D = the literal (1, 0, 0, 1)), the doubled
    // alpha product, the half-sum of stages 0 and 1, and the output alpha c[D].w. Nothing else may be read.
    const std::string pc = s->substr(body, end - body);
    unsigned d = 0, c = 0;
    const size_t dp = pc.find("(float4(dot(((c[");
    if (dp == std::string::npos ||
        sscanf(pc.c_str() + dp, "(float4(dot(((c[%u].xyzw)).xyz, ((c[%u].xyzw)).xyz)))", &d, &c) != 2 || d > 31 || c > 31)
        return false;
    if (pc.find("(((r[0].wwww) * (r[1].wwww)) * 2.0)") == std::string::npos ||
        pc.find("(((t[1].xyzw) + (t[0].xyzw)) / 2.0)") == std::string::npos ||
        pc.find("{ float4 w_ = ((c[" + std::to_string(d) + "].wwww)); r[0].w = w_.w; }") == std::string::npos ||
        pc.find("tx") != std::string::npos || pc.find("vin.") != std::string::npos)
        return false;
    const std::string D = std::to_string(d), C = std::to_string(c);
    s->replace(body, end - body,
               "    { float sk_ci = dot(c[" + D + "].xyz, c[" + C + "].xyz);   // CloudIntensity.x: the whispy layer\n"
               "      float sk_a = saturate(t[0].w * t[2].w * 2.0 * sk_ci);   // layer A x its shifted copy\n"
               "      float3 sk_c = mix(t[3].xyz, t[0].xyz, sk_a);            // over the scattering in-scatter\n"
               "      sk_c = mix(sk_c, t[1].xyz, saturate(t[1].w * sk_ci));   // the cap\n"
               "      r[0] = float4(sk_c * (2.0 * 0.5) * iv[0].xyz, c[" + D + "].w); }\n");
    return true;
}
bool sky_vs_patch(std::string *s) {
    // The eye vector line gives the position register; the in-scatter's extinction exponent is the line after
    // r[R] = c[J] that scales c[B] (Fog_Br_Plus_Bm) by r[R].w (the fixed distance, pre-multiplied by log2 e).
    const size_t ep = s->find("{ float4 w_ = ((-(vv[");
    unsigned pos = 0, eye = 0, er = 0;
    if (ep == std::string::npos ||
        sscanf(s->c_str() + ep, "{ float4 w_ = ((-(vv[%u].xyzw)) + (c[%u].xyzw)); r[%u].xyz", &pos, &eye, &er) != 3)
        return false;
    for (size_t at = s->find("    r["); at != std::string::npos; at = s->find("    r[", at + 1)) {
        unsigned r = 0, j = 0, r2 = 0, b = 0, x = 0;
        const size_t next = s->find('\n', at) + 1, stop = s->find('\n', next);   // whole lines only
        if (next == 0 || stop == std::string::npos)
            continue;
        const std::string l1 = s->substr(at, next - at), l2 = s->substr(next, stop + 1 - next);
        char tail1[8] = {}, tail2[8] = {};
        if (sscanf(l1.c_str(), "    r[%u] = ((c[%u].xyzw))%2s", &r, &j, tail1) != 3 || strcmp(tail1, ";") != 0 ||
            sscanf(l2.c_str(), "    { float4 w_ = ((r[%u].wwww) * (c[%u].xyzw)); r[%u].xyz = w_.xyz; %2s", &r2, &b, &x,
                   tail2) != 4 ||
            strcmp(tail2, "}") != 0 || r2 != r)
            continue;
        const int m = int(l2.size());
        // The exponent must feed three exp2 lines and the clamped in-scatter output (ot[3] on the PC sky).
        size_t e2 = 0, ex = 0;
        for (size_t q = s->find("exp2(", next); q != std::string::npos; q = s->find("exp2(", q + 1))
            ++e2;
        if (e2 < 3 || (ex = s->find("ot[3].xyz = w_.xyz", next)) == std::string::npos)
            return false;
        const std::string P = std::to_string(pos), B = std::to_string(b), X = std::to_string(x);
        s->replace(next, size_t(m),
                   "    { float4 w_ = ((-(10000.0 * (2.0 - vv[" + P + "].z * 0.000303030305)) * 1.44269504) * (c[" + B +
                       "].xyzw)); r[" + X + "].xyz = w_.xyz; }   // run 13 sky: the 360 horizon distance\n");
        return true;
    }
    return false;
}
// R3 run 17 Xbox 360 lens drops, our own version (D-124; review/r3-design/visible-track.md B). The PC on-screen drop
// samples its 16x16 displacement map (RAINDROPPC: yz the refraction, w a hard disc) at t0, offsets the scene copy by
// yz x c0.zw + c0.xy, fades w by c1.x and brightens the scene x c2.x (1.2): one hard, bright disc. The 360 draws a soft
// lens from a 4-shape atlas: the scene refracted and shaded, a darker rim, alpha = shape x fade (reference only; the
// 360 code is not used). Ours keeps the game's refraction and fade and computes the lens: t0 spans the quad 0..1 (the
// PC disc fills it), one of four shapes per drop picked by a hash of the drop's screen centre (stable while it lives),
// a soft edge, a bright core and a darker rim. No x1.2, no hard mask, no texture asset. Anchored on the six PC lines.
static const char *const kDropLines =
    "    r[0] = (tx1.sample(sm1, ((t[0].xyzw)).xy));\n"
    "    { float4 w_ = ((r[0].yyyy) * (c[0].zzzz) + (c[0].xxxx)); r[0].x = w_.x; }\n"
    "    { float4 w_ = ((r[0].zzzz) * (c[0].wwww) + (c[0].yyyy)); r[0].y = w_.y; }\n"
    "    { float4 w_ = ((r[0].wwww) * (c[1].xxxx)); r[0].w = w_.w; }\n"
    "    r[1] = (tx0.sample(sm0, ((r[0].xyzw)).xy));\n"
    "    { float4 w_ = ((r[1].xyzw) * (c[2].xxxx)); r[0].xyz = w_.xyz; }\n";
bool raindrop_patch(std::string *s) {
    const size_t at = s->find(kDropLines);
    if (at == std::string::npos || s->find(kDropLines, at + 1) != std::string::npos)
        return false;
    s->replace(at, strlen(kDropLines),
               "    {   // R3 run 17: Xbox 360 lens drop, our own version (the game's refraction and fade, a computed lens)\n"
               "        const float4 rd_m = tx1.sample(sm1, t[0].xy);   // the PC map: yz refraction (w, its hard disc, unused)\n"
               "        const float2 rd_d = float2(dfdx(t[0].x), dfdy(t[0].y));\n"
               "        const float2 rd_c = vin.pos.xy - (t[0].xy - 0.5) / select(rd_d, float2(1.0), abs(rd_d) < 1e-6);\n"
               "        const float rd_h = fract(sin(dot(floor(rd_c / 16.0), float2(12.9898, 78.233))) * 43758.5453);\n"
               "        const int rd_k = min(int(rd_h * 4.0), 3);   // one of four shapes\n"
               "        const float rd_a = (fract(rd_h * 7.31) - 0.5) * 0.5;   // a small tilt\n"
               "        float2 q = t[0].xy * 2.0 - 1.0;\n"
               "        q = float2(q.x * cos(rd_a) - q.y * sin(rd_a), q.x * sin(rd_a) + q.y * cos(rd_a));\n"
               "        if (rd_k == 1) q *= float2(0.9, 1.1);                              // wide\n"
               "        else if (rd_k == 2) q.x *= 1.0 + 0.4 * saturate(-q.y);            // a teardrop: narrow on top\n"
               "        else if (rd_k == 3) q *= 1.18;                                     // small\n"
               "        const float rd_r = length(q);\n"
               "        const float rd_o = 1.0 - smoothstep(0.72, 1.0, rd_r);             // a soft edge\n"
               "        const float rd_s = 1.08 - 0.46 * smoothstep(0.5, 0.97, rd_r);     // bright core, darker rim\n"
               "        r[1] = tx0.sample(sm0, float2(rd_m.y * c[0].z + c[0].x, rd_m.z * c[0].w + c[0].y));\n"
               "        r[0] = float4(r[1].xyz * rd_s, rd_o * c[1].x);\n"
               "    }\n");
    return true;
}
bool pixel_is_raindrop(const Program &p) {
    std::string src, why;
    if (!p.pixel || !pixel_source(p, PixelVariant{}, &src, &why))
        return false;
    return raindrop_patch(&src);
}
bool pixel_is_sky(const Program &p) {
    std::string src, why;
    if (!p.pixel || !pixel_source(p, PixelVariant{}, &src, &why))
        return false;
    return sky_ps_patch(&src);
}
bool vertex_is_sky(const Program &p) {
    std::string src, why;
    if (p.pixel || !vertex_source(p, VertexVariant{}, &src, &why))
        return false;
    return sky_vs_patch(&src);
}

bool vertex_source(const Program &p, const VertexVariant &vv, std::string *out, std::string *why) {
    uint32_t pool_reg[4] = {};
    if (vv.pool && !pool_inputs(p, pool_reg, vv.pool == 1 ? 4 : 3)) {
        if (why)
            *why = "pool variant: an input (POSITION0, NORMAL0, TEXCOORD0, TANGENT0) is not declared";
        return false;
    }
    if (!p.ok || p.pixel) {
        if (why)
            *why = p.ok ? "not a vertex shader" : p.why;
        return false;
    }
    Gen g(p, Lang::MSL);
    if (!generate(g, why))
        return false;
    std::string s = kPrelude;
    s += "struct D9VIn {\n";
    const std::vector<uint32_t> order = input_registers(p);
    const std::set<uint32_t> seen(order.begin(), order.end());
    for (uint32_t i : order)
        s += "    float4 v" + std::to_string(i) + " [[attribute(" + std::to_string(i) + ")]];\n";
    if (seen.empty())
        s += "    float4 v0 [[attribute(0)]];\n";
    s += "};\n";
    s += "vertex D9V2F vs_main(D9VIn vin [[stage_in]], constant float4 *c [[buffer(16)]],\n"
         "                     constant D9VSParams &P [[buffer(17)]]) {\n"
         "    float4 r[32] = {};\n"
         "    float4 a0 = float4(0.0);\n"
         "    int aL = 0;\n"
         "    float4 opos = float4(0.0), ofog = float4(1.0), opsize = float4(1.0);\n"
         "    float4 od[2] = { float4(1.0), float4(0.0) };\n"
         "    float4 ot[8] = {};\n"
         "    float4 vv[16] = {};\n";
    for (uint32_t i : seen)
        s += "    vv[" + std::to_string(i) + "] = vin.v" + std::to_string(i) + " * P.ascale[" +
             std::to_string(i) + "];\n";
    s += g.body;
    if (vv.xfog && g.body.find("ofog") != std::string::npos) {   // UG1 run 6: the fog interpolant must be free
        if (why)
            *why = "xfog variant: the program writes oFog";
        return false;
    }
    if (vv.smoke) {   // Remaster run 14 lit smoke: the world position (particle quads are world space) for the PS
        uint32_t sreg[4] = {};
        if (!pool_inputs(p, sreg, 1) || g.body.find("ot[7]") != std::string::npos) {
            if (why)
                *why = "smoke variant: no POSITION0 input, or t7 already written";
            return false;
        }
        s += "    ot[7] = float4(vv[" + std::to_string(sreg[0]) + "].xyz, 1.0);   // run 14 lit smoke: world position\n";
    }
    if (vv.glass) {   // Remaster run 13 natural glass: normal and the eye vector for a per-pixel Fresnel
        const GlassFlow gf = glass_vs_flow(p);
        if (!gf.ok) {
            if (why)
                *why = "glass variant: no single NORMAL input / eye term, or t5/t6 already written";
            return false;
        }
        s += "    ot[5] = float4(vv[" + std::to_string(gf.normal) + "].xyz, 0.0);\n"
             "    ot[6] = float4(c[" + std::to_string(gf.eye) + "].xyz - vv[" + std::to_string(gf.position) + "].xyz, 0.0);\n";
        if (vv.glass == 2) {   // Xbox 360 glass mode: the sun direction for the per-pixel pow(R.V, 100)
            if (gf.light < 0) {
                if (why)
                    *why = "glass 360 variant: no sun constant (DP3 and MAD -c with the NORMAL input) or t7 written";
                return false;
            }
            s += "    ot[7] = float4(c[" + std::to_string(gf.light) + "].xyz, 0.0);\n";
        }
    }
    if (vv.carenv == 2) {   // Remaster run 13 Xbox 360 car paint: object position and eye distance for the flake
        uint32_t cpos = 0, ceye = 0;
        if (!car_vs_flow(p, &cpos, &ceye)) {
            if (why)
                *why = "carenv 360 variant: no single eye term c[E] - POSITION, or t6 already written";
            return false;
        }
        const std::string P = "vv[" + std::to_string(cpos) + "].xyz", E = "c[" + std::to_string(ceye) + "].xyz";
        s += "    ot[6] = float4(" + P + ", length(" + E + " - " + P + "));\n";
    }
    if (vv.pbr) {   // Remaster run 12 pilot: normal, eye-relative position, sun direction (vertex_sun_flow)
        const SunFlow f = vertex_sun_flow(p);
        if (!f.ok || !f.pbr_ok) {
            if (why)
                *why = "pbr variant: " + (f.ok ? f.pbr_why : f.why);
            return false;
        }
        s += "    ot[4] = float4(vv[" + std::to_string(f.normal) + "].xyz, 0.0);\n"
             "    ot[5] = float4(vv[" + std::to_string(f.position) + "].xyz - c[" + std::to_string(f.eye) +
             "].xyz, 0.0);\n"
             "    ot[6] = float4(c[" + std::to_string(f.light) + "].xyz, 0.0);\n";
    }
    if (vv.surf) {   // Run 15 Wave B WB1b: the world normal for S0 (NORMAL0 x the object -> world rows at buffer(20))
        uint32_t sreg[4] = {};
        const std::string slot = "ot[" + std::to_string(vv.surf) + "]";
        const char kVsSig[] = "                     constant D9VSParams &P [[buffer(17)]]) {\n";
        const size_t sig = s.find(kVsSig);
        if (vv.surf < 4 || vv.surf > 7 || vv.fefloor || !pool_inputs(p, sreg, 2) ||
            g.body.find(slot + " =") != std::string::npos || g.body.find(slot + ".") != std::string::npos ||
            s.find(slot + " = ", s.find("vv[")) != std::string::npos || sig == std::string::npos) {
            if (why)
                *why = "surf variant: no NORMAL0 input, the slot is written, or a variant that edits the signature";
            return false;
        }
        s.replace(sig, sizeof kVsSig - 1,
                  "                     constant D9VSParams &P [[buffer(17)]],\n"
                  "                     constant float4 *d9surf [[buffer(20)]]) {\n");
        const std::string n = "vv[" + std::to_string(sreg[1]) + "]";
        s += "    " + slot + " = float4(" + n + ".x * d9surf[0].xyz + " + n + ".y * d9surf[1].xyz + " + n +
             ".z * d9surf[2].xyz, 0.0);   // Wave B WB1b\n";
    }
    s += "    D9V2F o;\n"
         "    o.pos = opos;\n"
         "    o.pos.xy += P.halfpix.xy * opos.w;\n"
         "    o.d0 = " +
         std::string(p.major >= 3 ? "od[0]" : "saturate(od[0])") +
         ";\n"
         "    o.d1 = " +
         std::string(p.major >= 3 ? "od[1]" : "saturate(od[1])") +
         ";\n"
         "    o.t0 = ot[0]; o.t1 = ot[1]; o.t2 = ot[2]; o.t3 = ot[3];\n"
         "    o.t4 = ot[4]; o.t5 = ot[5]; o.t6 = ot[6]; o.t7 = ot[7];\n" +
         std::string(vv.xfog ? "    o.fog = opos.y;   // UG1 run 6 xfog: clip y for the screen-height falloff\n"
                             : "    o.fog = ofog.x;\n") +
         "    o.fogz = opos.z;\n";
    if (vv.pool) {
        s += "    o.pool_p = vv[" + std::to_string(pool_reg[0]) + "]; o.pool_n = vv[" + std::to_string(pool_reg[1]) +
             "]; o.pool_uv = vv[" + std::to_string(pool_reg[2]) + "]; o.pool_t = " +
             (vv.pool == 1 ? "vv[" + std::to_string(pool_reg[3]) + "]" : std::string("float4(0.0)")) + ";\n";
        pool_varyings(&s);
    }
    s += "    return o;\n"
         "}\n";
    if (vv.fefloor && !fefloor_vs_patch(&s)) {   // run 13 front-end floor shadows: never "shadowed", drawn plain
        if (why)
            *why = "fefloor: t7 already written or the output lines are not as expected";
        return false;
    }
    if (vv.sky && !sky_vs_patch(&s)) {   // run 13 Xbox 360 sky (ours): never "counted as sky, drawn plain"
        if (why)
            *why = "sky: the in-scatter distance lines are not as expected";
        return false;
    }
    if (vv.carenv && !(vv.carenv == 2 ? [&] { CarEnvAnchors ca; return carenv_find(s, &ca); }() : carenv_patch(&s))) {   // run 13: never "counted as calibrated, drawn plain"
        if (why)
            *why = "carenv: the environment strength lines are not as expected";
        return false;
    }
    *out = s;
    return true;
}

// Remaster R2 wet roads (owner 6 Oct; review/2026-10-06-r2-wetroad-c1-design.md). The PC road effect's translation
// has three fixed lines: R samples the projected planar reflection, M sets its mask (diffuse alpha x vertex alpha,
// whose vertex alpha holds the baked wheel-path bands), A adds reflection x c1.w to the lit road. The patch replaces
// them with our own wet-road shading: a vertically blurred and stretched reflection, a damp film with puddles in
// place of the bands, a view-depth Fresnel and wet darkening, all scaled by c1.w (so dry stays as it was).
const char *kWetR = "    r[0] = (tx3.sample(sm3, (((t[1].xyzw) / ((t[1].xyzw)).w)).xy));\n";
const char *kWetM = "    { float4 w_ = ((r[1].wwww) * (iv[0].wwww)); r[0].w = w_.w; }\n";
const char *kWetA = "    { float4 w_ = ((r[0].xyzw) * (c[1].wwww) + (r[1].xyzw)); r[0].xyz = w_.xyz; }\n";
// UG1 run 6 Xbox headlight distance fade (review/run06/research-headlight.md). The PC world-headlight program
// (worldheadlight.efx ps_1_1: tex t0..t2, then t1 x t2 x HeadlightColor + v0, x t0 x 2) samples the headlight
// projector at t1 (projected: tc[1].w is the depth along the beam, metres) and the 64x1 clip ramp at t2. The fade
// scales the t[2] sample right after its line, so it multiplies the headlight light and nothing else.
// headlight_find: the end of the one t[2] sample line, after one t[1] sample, with t[1] and t[2] read later.
static bool headlight_find(const std::string &src, size_t *at) {
    const std::string l1 = "    t[1] = (tx1.sample(sm1, ", l2 = "    t[2] = (tx2.sample(sm2, ";
    const size_t a = src.find(l1), b = src.find(l2);
    if (a == std::string::npos || b == std::string::npos || b < a || src.find(l1, a + 1) != std::string::npos ||
        src.find(l2, b + 1) != std::string::npos)
        return false;
    const size_t e = src.find(";\n", b);
    if (e == std::string::npos || src.find('\n', b) != e + 1)
        return false;
    const std::string rest = src.substr(e);
    if (rest.find("t[1].") == std::string::npos || rest.find("t[2].") == std::string::npos)
        return false;
    *at = e + 2;
    return true;
}

float hlfade_range() {
    static const float r = [] {
        const char *e = getenv("RECOMP_UG1_HEADLIGHT_FADE_M");
        float v = e && *e ? (float)atof(e) : 100.0f;
        return v < 20.0f ? 20.0f : v > 400.0f ? 400.0f : v;
    }();
    return r;
}

static bool headlight_patch(std::string *s, int mode) {
    size_t at = 0;
    if ((mode != 1 && mode != 2) || !headlight_find(*s, &at))
        return false;
    char k[32];
    snprintf(k, sizeof k, "%.6g", 1.0 / hlfade_range());
    std::string add;
    if (mode == 1)   // the Xbox fade on top of the PC clip ramp
        add = std::string("    t[2] *= saturate(1.0 - tc[1].w * ") + k + ");   // UG1 run 6 headlight fade 1\n";
    else   // the Xbox profile: the PC near cut, no flat stretch, the Xbox headlight colour ratio (0.9/0.8, 0.95/0.9)
        add = std::string("    t[2] = float4(saturate((tc[1].w - 1.6) * 0.32) * saturate(1.0 - tc[1].w * ") + k +
              "));   // UG1 run 6 headlight fade 2\n"
              "    t[2].xyz *= float3(1.125, 1.125, 1.056);\n";
    s->insert(at, add);
    return true;
}

bool pixel_is_headlight(const Program &p) {
    std::string src, why;
    if (!p.pixel || p.major != 1 || !pixel_source(p, PixelVariant{}, &src, &why))
        return false;
    size_t at = 0;
    return headlight_find(src, &at);
}

void wetroad_patch(std::string *s) {
    const size_t r = s->find(kWetR), m = s->find(kWetM), a = s->find(kWetA);
    if (r == std::string::npos || m == std::string::npos || a == std::string::npos)
        return;
    std::string out = *s;
    // C1c (owner 6 Oct: "the reflection looks too bright"): the game adds the reflection on top of the fully lit road,
    // so wet roads came out brighter than dry ones (a frosty, glittery sheen). Here the lit road darkens with wetness
    // (wet asphalt is darker) and the reflection is calmer (0.6), still Fresnel-shaped (eye vector t[4], C-WR1 1) and
    // never above the game's own amount. c1.w is the wet amount (0.000 dry, 0.995 wet in the road-draw log), so dry
    // roads are unchanged.
    out.replace(out.find(kWetA), strlen(kWetA),
                "    { float wr_cos = saturate(normalize(t[4].xyz).z);\n"
                "      float wr_f = 0.04 + 0.96 * pow(1.0 - wr_cos, 5.0);\n"
                "      float wr_k = min(1.0, 0.3 + 1.7 * wr_f) * 0.6;\n"
                "      float wr_wet = saturate(c[1].w);\n"
                "      float4 w_ = ((r[0].xyzw) * (c[1].wwww * wr_k) + (r[1].xyzw) * (1.0 - 0.30 * wr_wet));\n"
                "      r[0].xyz = w_.xyz; }\n");
    // M: art zeros stay zero; the wheel-path bands flattened (C-WR1 3); the fine speckle's contrast reduced (it turned
    // the sky reflection into glitter); puddles (faded with distance, C-WR1 5) are smooth and mirror-like.
    out.replace(out.find(kWetM), strlen(kWetM),
                "    { float wr_v = iv[0].w;\n"
                "      float wr_flat = smoothstep(0.0, 0.12, wr_v) * (0.78 + 0.22 * wr_v);\n"
                "      float wr_pud = wr_puddle(t[0].xy) * saturate(1.5 - t[1].w / 40.0);\n"
                "      float wr_mask = mix(0.30, r[1].w, 0.35) * wr_flat;\n"
                "      r[0].w = mix(wr_mask, 0.9 * wr_flat, wr_pud); }\n");
    // R: blur in reflection texels, stretched and longer with distance (C-WR1 6); rain disturbance: a two-scale moving
    // distortion of the lookup (time from the host in P.pad1), strongest in puddles (the 360's rain layer, our own code).
    out.replace(out.find(kWetR), strlen(kWetR),
                "    { float2 wr_uv = (((t[1].xyzw) / ((t[1].xyzw)).w)).xy;\n"
                "      float wr_t = P.pad1;\n"
                "      float2 wr_q = fmod(t[0].xy, 512.0);\n"
                "      float2 wr_d = float2(wr_noise(wr_q * 9.0 + float2(wr_t * 1.7, 0.0)) - 0.5,\n"
                "                           wr_noise(wr_q * 9.0 + float2(5.3, -wr_t * 1.3)) - 0.5) * 0.6 +\n"
                "                    float2(wr_noise(wr_q * 23.0 + float2(0.0, wr_t * 2.9)) - 0.5,\n"
                "                           wr_noise(wr_q * 23.0 + float2(-wr_t * 2.3, 9.1)) - 0.5) * 0.4;\n"
                "      float wr_pd = wr_puddle(t[0].xy);\n"
                "      wr_uv += wr_d * (0.0015 + 0.004 * wr_pd);\n"
                "      float wr_px = (1.0 + 0.05 * max(t[1].w, 0.0)) / float(tx3.get_height());\n"
                "      float4 wr_acc = float4(0.0); float wr_ws = 0.0;\n"
                "      for (int wr_i = -2; wr_i <= 5; ++wr_i) {\n"
                "          float wr_g = exp(-0.08 * float(wr_i * wr_i));\n"
                "          wr_acc += wr_g * tx3.sample(sm3, wr_uv + float2(0.0, wr_px * float(wr_i)));\n"
                "          wr_ws += wr_g;\n"
                "      }\n"
                "      r[0] = wr_acc / wr_ws; }\n");
    const size_t f = out.find("fragment D9PSOut ps_main(");
    if (f == std::string::npos)
        return;
    out.insert(f, "static float wr_hash(float2 p) { float3 q = fract(float3(p.xyx) * 0.1031); q += dot(q, q.yzx + 33.33);\n"
                  "    return fract((q.x + q.y) * q.z); }\n"
                  "static float wr_noise(float2 p) {\n"
                  "    float2 i = floor(p), f = fract(p); f = f * f * (3.0 - 2.0 * f);\n"
                  "    return mix(mix(wr_hash(i), wr_hash(i + float2(1, 0)), f.x),\n"
                  "               mix(wr_hash(i + float2(0, 1)), wr_hash(i + float2(1, 1)), f.x), f.y);\n"
                  "}\n"
                  "static float wr_puddle(float2 uv) {\n"
                  "    float2 p = fmod(uv, 512.0) * 0.37;\n"
                  "    float n = wr_noise(p) * 0.6 + wr_noise(p * 2.13 + 7.1) * 0.4;\n"
                  "    return smoothstep(0.62, 0.78, n);\n"
                  "}\n");
    *s = out;
}

// Remaster run 13 Xbox 360 wet road (owner 8 Oct: our own version of the 360 shaders; x360-shader-map.md WORLDREFLECT).
// The 360 road adds a rain-splash layer: a 16-frame splash animation sampled at two scales (1x, 1.5x) offsets the
// planar reflection lookup by splash x rain intensity, and the reflection is scaled by (1 - 0.6 rain). Ours: no asset,
// an analytic 16-frame drop/ring field (one drop per cell, random phase, the ring radius and fade stepped in 16 frames)
// whose ring slope is the 2D offset; time P.pad1 (the wet-road clock), rain intensity = c1.w (wetness, the proxy).
// Applied on top of the Natural wet road (wetroad_patch output, anchored on its own lines); mode 1 is untouched.
bool wetroad_x360_patch(std::string *s) {
    const char *uvl = "      wr_uv += wr_d * (0.0015 + 0.004 * wr_pd);\n";
    const char *kal = "      float4 w_ = ((r[0].xyzw) * (c[1].wwww * wr_k) + (r[1].xyzw) * (1.0 - 0.30 * wr_wet));\n";
    const char *fn = "fragment D9PSOut ps_main(";
    for (const char *k : {uvl, kal, fn}) {
        const size_t p = s->find(k);
        if (p == std::string::npos || s->find(k, p + 1) != std::string::npos)
            return false;
    }
    s->replace(s->find(kal), strlen(kal),
               "      float4 w_ = ((r[0].xyzw) * (c[1].wwww * wr_k * (1.0 - 0.6 * wr_wet)) + (r[1].xyzw) * (1.0 - 0.30 * wr_wet));\n");
    s->insert(s->find(uvl) + strlen(uvl),
              "      wr_uv += (wr_splash(t[0].xy * 4.0, wr_t) + wr_splash(t[0].xy * 6.0 + 17.3, wr_t * 1.13)) *\n"
              "               (0.006 * saturate(c[1].w));\n");
    s->insert(s->find(fn),
              "static float2 wr_splash(float2 uv, float t) {   // run 13: own 16-frame rain splash, ring slope as offset\n"
              "    float2 q = fmod(uv, 2048.0), c = floor(q), f = q - c - 0.5;\n"
              "    float h = wr_hash(c * 1.37 + 3.1), g = wr_hash(c * 2.11 + 9.7);\n"
              "    float2 o = (float2(h, g) - 0.5) * 0.5;\n"
              "    float ph = fract(t * (0.9 + 0.4 * h) + g);\n"
              "    float fr = floor(ph * 16.0) / 16.0;\n"
              "    float2 d = f - o;\n"
              "    float r = length(d), rad = 0.05 + 0.40 * fr;\n"
              "    float ring = exp(-pow((r - rad) * 18.0, 2.0)) * (1.0 - fr) * (1.0 - fr);\n"
              "    return r > 1e-4 ? d / r * ring : float2(0.0);\n"
              "}\n");
    return true;
}
// Remaster run 13 wall parallax (owner 8 Oct; x360-shader-map.md WORLDNORMALMAP: the 360's parallax, X360Stuff
// DoParallax as the look reference; our own code). The PC WORLDNORMALMAP program samples base, normal and spec maps at
// t[0] and lights in tangent space with L = nrm(t[l]) and V = nrm(t[v]). The patch offsets that uv by a height read
// from the normal map's alpha: uv += V.xy x (a x 2 x 0.075 - 0.0375). Anchors by structure: the t[0] samples (every
// t[0] read must be one); the normal map = the t[0] sample whose register is next biased by a constant (+ c[k].x); L =
// the normalized t input subtracted in the reflection MAD; V = the other normalized t input. The host applies it only
// where the bound normal map stores alpha (DXT3/DXT5/A8R8G8B8).
struct ParallaxAnchors {
    int stage = -1, v = -1;
    size_t first = std::string::npos;
};
bool parallax_find(const std::string &s, ParallaxAnchors *a) {
    const std::string at0 = "((t[0].xyzw)).xy));\n";
    size_t nt0 = 0, nsamp = 0;
    for (size_t q = s.find("t[0]"); q != std::string::npos; q = s.find("t[0]", q + 1))
        ++nt0;
    for (size_t q = s.find(at0); q != std::string::npos; q = s.find(at0, q + 1)) {
        const size_t ls = s.rfind("\n", q) + 1;
        const std::string l = line_at(s, ls);
        const int r = creg(l, 4, 'r');
        const size_t tp = l.find("(tx");
        if (r < 0 || tp == std::string::npos)
            return false;
        const int k = atoi(l.c_str() + tp + 3);
        if (l != "    r[" + std::to_string(r) + "] = (tx" + std::to_string(k) + ".sample(sm" + std::to_string(k) + ", " +
                     at0)
            return false;
        ++nsamp;
        if (a->first == std::string::npos)
            a->first = ls;
        // the next line that reads r[r]: the normal map's bias
        const std::string R = "r[" + std::to_string(r) + "]";
        size_t nx = s.find(R, ls + l.size());
        if (nx == std::string::npos)
            continue;
        const std::string nl = line_at(s, s.rfind("\n", nx) + 1);
        if (nl.rfind("    { float4 w_ = ((" + R + ".xyzw) + (c[", 0) == 0 && nl.find(".xxxx)); " + R + ".xyz = w_.xyz; }\n") != std::string::npos) {
            if (a->stage >= 0)
                return false;
            a->stage = k;
        }
    }
    if (a->stage < 0 || nsamp < 2 || nt0 != nsamp)
        return false;
    // the two normalized t inputs
    std::vector<std::pair<int, int>> nrm;   // (t index, destination register)
    std::vector<size_t> nrm_at;
    const std::string nk = "    { float4 w_ = (d9_nrm((t[";
    for (size_t q = s.find(nk); q != std::string::npos; q = s.find(nk, q + 1)) {
        nrm_at.push_back(q);
        const std::string l = line_at(s, q);
        const int t = creg(l, nk.size() - 2, 't');
        const size_t dp = l.find(".xyzw))); r[");
        const int d = dp == std::string::npos ? -1 : creg(l, dp + 10, 'r');
        if (t < 0 || d < 0)
            return false;
        nrm.push_back({t, d});
    }
    if (nrm.size() != 2 || nrm[0].first == nrm[1].first)
        return false;
    int l = -1;
    for (int i = 0; i < 2; ++i)   // L: negated in the reflection MAD while it still holds that input
        if (const size_t m = s.find(" + (-(r[" + std::to_string(nrm[i].second) + "].xyzw))); r[", nrm_at[i]);
            m != std::string::npos && (i == 1 || m < nrm_at[1] || nrm[1].second != nrm[0].second)) {
            if (l >= 0)
                return false;
            l = i;
        }
    if (l < 0)
        return false;
    a->v = nrm[1 - l].first;
    return a->v != 0;
}
bool parallax_patch(std::string *s) {
    ParallaxAnchors a;
    if (!parallax_find(*s, &a))
        return false;
    const std::string K = std::to_string(a.stage);
    const std::string from = "((t[0].xyzw)).xy));\n", to = "(float4(px_uv, 0.0, 0.0)).xy));\n";
    for (size_t q = s->find(from); q != std::string::npos; q = s->find(from, q + to.size()))
        s->replace(q, from.size(), to);
    s->insert(a.first, "    float2 px_uv = t[0].xy + normalize(t[" + std::to_string(a.v) + "].xyz).xy * (tx" + K +
                           ".sample(sm" + K + ", t[0].xy).w * 2.0 * 0.075 - 0.0375);\n");
    return true;
}
int pixel_parallax_stage(const Program &p) {
    std::string src, why;
    if (!p.pixel || p.major < 2 || !pixel_source(p, PixelVariant{}, &src, &why))
        return -1;
    ParallaxAnchors a;
    return parallax_find(src, &a) ? a.stage : -1;
}
// Remaster run 13 road sheen (owner 7 Oct: road sun glare "at a real intensity"; review/run13/road-sheen-design.md).
// The PC road effect adds pow(sat(R.E), SpecularPower) x SpecularColour x 2 x spec map to the lit road: a golden
// highlight driven per pixel by the sharp normal and spec maps, which glitters at low sun (R0: the Xbox and HD road
// textures double its high-frequency energy). The patch replaces only that sum (line S) with a measured, physically
// based sun highlight: GGX with a material roughness (dry asphalt; smoother when wet, c1.w), Smith visibility, Schlick
// Fresnel from a dielectric F0 of 0.04 scaled by a low-passed spec map, the map normal blended toward the surface
// normal, and geometric specular anti-aliasing from the normal's screen derivatives. Lit by the sun colour of the
// diffuse term (c0). The planar reflection, its mask, the diffuse term and the wet shading are untouched.
// Settings read once (TEST ONLY tuning; baked into the patch text): RECOMP_REMASTER_ROADSHEEN_ROUGH (dry alpha, 0.55),
// _WET (wet alpha, 0.2), _NMIX (map normal share, 0.5), _BIAS (spec map mip bias, 2), _GAIN (1), _DEBUG (1 normal,
// 2 game highlight, 3 sheen, 4 alpha and AA term, written as the output colour).
// Remaster run 13 natural glass (owner 7 Oct: glass "at a real intensity"; review/run13/x360-study-design.md 3(d)).
// The PC building-glass effect (IDI_GLOSSYWINDOW_FX, census 8 Oct: ps f77fce200b154764 / 0919fce65f853375) adds a 2D
// reflection map r[R] = tx2(t1) times the window mask (the lit colour's alpha) to the lit colour, at full strength
// whatever the angle: facing windows read as grey mirrors. The patch weights it per pixel with Schlick's Fresnel for
// glass (F0 0.04, capped, RECOMP_REMASTER_GLASS_CAP, default 0.6) and lets the see-through colour give way to it:
// col = col (1 - F a) + refl F a. N.V from t5 (the NORMAL input) and t6 (eye - position), two-sided.
struct GlassAnchors {
    size_t pos = 0, len = 0;
    int r = -1, b = -1;
};
bool glass_find(const std::string &s, GlassAnchors *a) {
    const char *sp = "] = (tx2.sample(sm2, ((t[1].xyzw)).xy));\n";
    size_t p = s.find(sp);
    if (p == std::string::npos || s.find(sp, p + 1) != std::string::npos)
        return false;
    const size_t ls = s.rfind("    r[", p);
    if (ls == std::string::npos || p - ls > 10)
        return false;
    a->r = atoi(s.c_str() + ls + 6);
    const std::string head = "    { float4 w_ = ((r[" + std::to_string(a->r) + "].xyzw) * (r[";
    const size_t hp = s.find(head, p);
    if (hp == std::string::npos || s.find(head, hp + 1) != std::string::npos)
        return false;
    a->b = atoi(s.c_str() + hp + head.size());
    const std::string B = std::to_string(a->b);
    const std::string want = head + B + "].wwww) + (r[" + B + "].xyzw)); r[" + B + "].xyz = w_.xyz; }\n";
    if (s.compare(hp, want.size(), want) != 0 || s.find("t[5]") != std::string::npos ||
        s.find("t[6]") != std::string::npos)
        return false;
    a->pos = hp;
    a->len = want.size();
    return true;
}
// Remaster run 16 glass pop: the building-glass effect draws the windows of the FAR building models only; the near
// models' windows are another effect that natural glass leaves as the game's. Natural glass therefore fades in with
// the eye distance: the game's reflection up to the near edge, the natural weighting from the far edge on
// (RECOMP_REMASTER_GLASS_NEAR="near,far" in world units; defaults below; "0,0" = everywhere, as run 13).
// The weight k = smoothstep(near, far, |eye - position|) uses t6 (eye - position, VertexVariant glass).
static std::string glass_k_line() {
    static const std::string line = [] {
        float n = 60.0f, f = 200.0f;   // run 16: street-level far-window draws start at ~60 m (r16gl-dist)
        if (const char *v = std::getenv("RECOMP_REMASTER_GLASS_NEAR"); v && *v) {
            float a = 0, b = 0;
            if (sscanf(v, "%f,%f", &a, &b) == 2 && a >= 0.0f && b >= a && b <= 5000.0f) {
                n = a;
                f = b;
            }
        }
        if (f <= 0.0f)
            return std::string("      float gl_k = 1.0;\n");
        char t[160];
        snprintf(t, sizeof t, "      float gl_k = smoothstep(%d.0, %d.0, length(t[6].xyz));\n", int(n), int(f > n ? f : n + 1.0f));
        return std::string(t);
    }();
    return line;
}
bool glass_patch(std::string *s) {
    GlassAnchors a;
    if (!glass_find(*s, &a))
        return false;
    static const float cap = [] {
        float c = 0.6f;
        if (const char *v = std::getenv("RECOMP_REMASTER_GLASS_CAP"); v && *v) {
            const float f = std::strtof(v, nullptr);
            if (f >= 0.04f && f <= 1.0f)
                c = f;
        }
        return c;
    }();
    char k[32];
    snprintf(k, sizeof k, "%d.%04d", int(cap), int((cap - float(int(cap))) * 10000.0f + 0.5f) % 10000);
    const std::string R = std::to_string(a.r), B = std::to_string(a.b);
    s->replace(a.pos, a.len,
               "    { float gl_nv = saturate(abs(dot(normalize(t[5].xyz), normalize(t[6].xyz))));\n"
               "      float gl_f = min(0.04 + 0.96 * pow(1.0 - gl_nv, 5.0), " + std::string(k) + ") * r[" + B + "].w;\n" +
               glass_k_line() +
               "      r[" + B + "].xyz = r[" + B + "].xyz * (1.0 - gl_f * gl_k) + r[" + R + "].xyz * mix(r[" + B + "].w, gl_f, gl_k); }\n");
    return true;
}
// Remaster run 13 Xbox 360 glass mode (owner 8 Oct: our own version of the 360 shaders; review/run13/x360-shader-map.md
// GLASS). The 360 window: F = sat(0.2 + 0.4 (1 - N.V)) weights the reflection, sun = pow(R.V, 100) x mask x shadow x 2
// is added, alpha = 1 - sun x mask (bloom tag). The PC program has the same shape with a per-vertex pow(R.V, 10) in t2
// (times the mask, doubled twice, under the shadow). Anchors by structure: the reflection add (glass_find); the unique
// read of t[2], "(r[A].wwww) * (t[2].xyzw)" (A = the base sample, its alpha the window mask), whose t2 becomes the
// per-pixel sun x 0.5 (net x2); the output line. N t5, V t6, L t7 (VertexVariant glass 2).
bool glass_patch_x360(std::string *s) {
    GlassAnchors a;
    if (!glass_find(*s, &a) || s->find("t[7]") != std::string::npos)
        return false;
    const std::string R = std::to_string(a.r), B = std::to_string(a.b);
    std::string refl = "    { float gl_nv = saturate(abs(dot(normalize(t[5].xyz), normalize(t[6].xyz))));\n"
                       "      float gl_f = saturate(0.2 + 0.4 * (1.0 - gl_nv));\n" + glass_k_line() +
                       "      r[" + B + "].xyz = r[" + R + "].xyz * (r[" + B + "].w * mix(1.0, gl_f, gl_k)) + r[" + B + "].xyz; }\n";
    const char *t2 = " * (t[2].xyzw)); r[";
    size_t p = s->find(t2);
    if (p == std::string::npos || s->find(t2, p + 1) != std::string::npos)
        return false;
    {   // t[2] read exactly once after the prelude's t[] array
        size_t n = 0;
        for (size_t q = s->find("t[2]"); q != std::string::npos; q = s->find("t[2]", q + 1))
            ++n;
        if (n != 1)
            return false;
    }
    const size_t ls = s->rfind("    { float4 w_ = ((r[", p);
    if (ls == std::string::npos || p - ls > 40)
        return false;
    const int A = atoi(s->c_str() + ls + 22);
    const std::string head = "    { float4 w_ = ((r[" + std::to_string(A) + "].wwww)";
    if (s->compare(ls, head.size(), head) != 0 || ls + head.size() != p || a.pos < p)
        return false;
    const char *out = "    o.c0 = col;\n";
    if (s->find(out) == std::string::npos || s->find(out, s->find(out) + 1) != std::string::npos)
        return false;
    const char *decl = "    int aL = 0;\n";
    if (s->find(decl) == std::string::npos || s->find(decl, s->find(decl) + 1) != std::string::npos)
        return false;
    // Apply back to front so earlier offsets stay valid: output, reflection add, t2 read, declarations.
    s->replace(s->find(out), strlen(out),
               "    o.c0 = float4(col.rgb, saturate(1.0 - gl_s * gl_a));\n");
    s->replace(a.pos, a.len, refl);
    s->replace(p, strlen(" * (t[2].xyzw))"), " * float4(gl_s * 0.5))");
    s->insert(ls, "    gl_a = r[" + std::to_string(A) + "].w;\n"
                  "    gl_s = pow(saturate(dot(reflect(-normalize(t[7].xyz), normalize(t[5].xyz)), normalize(t[6].xyz))), "
                  "100.0);\n");
    s->insert(s->find(decl) + strlen(decl), "    float gl_s = 0.0, gl_a = 0.0;\n");
    return true;
}
// Remaster run 16 glass pop (owner 9 Oct: "windows/reflections pop in on basically all buildings"). The building-glass
// effect has a third pixel program, the ps_1_x one (census ps a8d19febb9579b63, vs eaa7aabc9baf009b), drawn in the main
// view next to the ps_2_0 one (0919fce6) on other window meshes: no shadow term, the same reflection map (7e16a782) in
// stage 1, added at full strength: col += base.a x tx1(t1) ("{ float4 w_ = ((t[M].wwww) * (t[N].xyzw) + (r[B].xyzw));
// r[B].xyz = w_.xyz; }", M and N plain 2D stage reads). Natural glass weighted only the ps_2_0 programs, so a window
// switching program read as a pop (grey mirror <-> see-through). Same formula as glass_patch (mode 1) and as the
// reflection half of glass_patch_x360 (mode 2; the per-vertex sun t2 of the PC program stays). N t5, V t6.
struct Glass1Anchors {
    size_t pos = 0, len = 0;
    int m = -1, n = -1, b = -1;
};
static bool glass1_find(const std::string &s, Glass1Anchors *a) {
    if (s.find("t[5]") != std::string::npos || s.find("t[6]") != std::string::npos || s.find("t[7]") != std::string::npos)
        return false;
    int hits = 0;
    for (size_t p = s.find("    { float4 w_ = ((t["); p != std::string::npos; p = s.find("    { float4 w_ = ((t[", p + 1)) {
        int m = -1, n = -1, b = -1, b2 = -1;
        if (sscanf(s.c_str() + p, "    { float4 w_ = ((t[%d].wwww) * (t[%d].xyzw) + (r[%d].xyzw)); r[%d].xyz", &m, &n, &b,
                   &b2) != 4)
            continue;
        const std::string M = std::to_string(m), N = std::to_string(n), B = std::to_string(b);
        const std::string want = "    { float4 w_ = ((t[" + M + "].wwww) * (t[" + N + "].xyzw) + (r[" + B + "].xyzw)); r[" +
                                 B + "].xyz = w_.xyz; }\n";
        if (m == n || b != b2 || m < 0 || m > 3 || n < 0 || n > 3 || s.compare(p, want.size(), want) != 0)
            continue;
        const std::string sm = "    t[" + M + "] = (tx" + M + ".sample(sm" + M + ", (tc[" + M + "]).xy));\n";
        const std::string sn = "    t[" + N + "] = (tx" + N + ".sample(sm" + N + ", (tc[" + N + "]).xy));\n";
        if (s.find(sm) == std::string::npos || s.find(sn) == std::string::npos || s.find(sm) > p || s.find(sn) > p)
            continue;
        a->pos = p;
        a->len = want.size();
        a->m = m;
        a->n = n;
        a->b = b;
        ++hits;
    }
    return hits == 1;
}
static bool glass1_patch(std::string *s, int mode) {
    Glass1Anchors a;
    if (!glass1_find(*s, &a))
        return false;
    static const float cap = [] {   // as glass_patch
        float c = 0.6f;
        if (const char *v = std::getenv("RECOMP_REMASTER_GLASS_CAP"); v && *v) {
            const float f = std::strtof(v, nullptr);
            if (f >= 0.04f && f <= 1.0f)
                c = f;
        }
        return c;
    }();
    char k[32];
    snprintf(k, sizeof k, "%d.%04d", int(cap), int((cap - float(int(cap))) * 10000.0f + 0.5f) % 10000);
    const std::string M = std::to_string(a.m), N = std::to_string(a.n), B = std::to_string(a.b);
    std::string txt = "    { float gl_nv = saturate(abs(dot(normalize(t[5].xyz), normalize(t[6].xyz))));\n";
    if (mode == 2)
        txt += "      float gl_f = saturate(0.2 + 0.4 * (1.0 - gl_nv));\n" + glass_k_line() +
               "      r[" + B + "].xyz = t[" + N + "].xyz * (t[" + M + "].w * mix(1.0, gl_f, gl_k)) + r[" + B + "].xyz; }\n";
    else
        txt += "      float gl_f = min(0.04 + 0.96 * pow(1.0 - gl_nv, 5.0), " + std::string(k) + ") * t[" + M + "].w;\n" +
               glass_k_line() +
               "      r[" + B + "].xyz = r[" + B + "].xyz * (1.0 - gl_f * gl_k) + t[" + N + "].xyz * mix(t[" + M + "].w, gl_f, gl_k); }\n";
    static const bool tint = [] { const char *e = std::getenv("RECOMP_TEST_GLASS_TINT"); return e && *e == '1'; }();
    if (tint)   // TEST ONLY: ps_1_x glass windows magenta (by the window mask)
        txt += "    r[" + B + "].xyz = mix(r[" + B + "].xyz, float3(1.0, 0.0, 1.0), t[" + M + "].w);\n";
    s->replace(a.pos, a.len, txt);
    return true;
}
static bool glass_patch_mode(std::string *s, int mode) {
    GlassAnchors a;
    if (glass_find(*s, &a)) {   // the ps_2_0 programs: exactly as before
        if (!(mode == 2 ? glass_patch_x360(s) : glass_patch(s)))
            return false;
        static const char tint = [] { const char *e = std::getenv("RECOMP_TEST_GLASS_TINT"); return e && *e ? *e : '0'; }();
        const char *out = "    float4 col = oc[0];\n";
        if (tint == '1' && s->find(out) != std::string::npos)   // TEST ONLY: ps_2_0 glass windows cyan (window mask)
            s->insert(s->find(out) + strlen(out), "    col.rgb = mix(col.rgb, float3(0.0, 1.0, 1.0), saturate(col.a));\n");
        if (tint == '2' && s->find(out) != std::string::npos)   // TEST ONLY: eye distance, R = d/250, G = d/1000
            s->insert(s->find(out) + strlen(out), "    col.rgb = mix(col.rgb, float3(saturate(length(t[6].xyz) / 250.0), "
                                                  "saturate(length(t[6].xyz) / 1000.0), 0.0), saturate(col.a));\n");
        return true;
    }
    return glass1_patch(s, mode);
}
struct RoadSheenEnv {
    float rough = 0.55f, wet = 0.2f, nmix = 0.5f, bias = 2.0f, gain = 1.0f;
    float bump = 4.0f, cav = 1.0f;   // R3 run 22 mode 3 (TEST envs _BUMP, _CAV): micro-normal strength, cavity exponent;
                                     // run 27: 4 (was 3), the owner's road eye check (D-154, panel 8)
    int taps = 2;   // run 23 mode 3: the gradient from 2 forward taps + the centre (run 27 default, D-154 panels 4 and 8;
                    // TEST env _TAPS=4 brings back the 4 central taps)
    int debug = 0;
    int wetpct = 100;   // run 14: the sheen on a fully wet road, % (100 = the patch text as before)
};
const RoadSheenEnv &sheen_env() {
    static const RoadSheenEnv e = [] {
        RoadSheenEnv x;
        auto num = [](const char *name, float lo, float hi, float *out) {
            if (const char *v = std::getenv(name); v && *v) {
                const float f = std::strtof(v, nullptr);
                if (f >= lo && f <= hi)
                    *out = f;
            }
        };
        num("RECOMP_REMASTER_ROADSHEEN_ROUGH", 0.02f, 1.0f, &x.rough);
        num("RECOMP_REMASTER_ROADSHEEN_WET", 0.02f, 1.0f, &x.wet);
        num("RECOMP_REMASTER_ROADSHEEN_NMIX", 0.0f, 1.0f, &x.nmix);
        num("RECOMP_REMASTER_ROADSHEEN_BIAS", 0.0f, 8.0f, &x.bias);
        num("RECOMP_REMASTER_ROADSHEEN_GAIN", 0.0f, 8.0f, &x.gain);
        num("RECOMP_REMASTER_ROADSHEEN_BUMP", 0.0f, 8.0f, &x.bump);
        num("RECOMP_REMASTER_ROADSHEEN_CAV", 0.0f, 4.0f, &x.cav);
        if (const char *t = std::getenv("RECOMP_REMASTER_ROADSHEEN_TAPS"); t && (t[0] == '2' || t[0] == '4') && !t[1])
            x.taps = t[0] - '0';
        if (const char *d = std::getenv("RECOMP_REMASTER_ROADSHEEN_DEBUG"); d && *d >= '1' && *d <= '5')
            x.debug = *d - '0';
        if (const char *w = std::getenv("RECOMP_REMASTER_ROADSHEEN_WETPCT"); w && *w) {
            const long p = std::strtol(w, nullptr, 10);
            if (p >= 0 && p <= 100)
                x.wetpct = int(p);
        }
        return x;
    }();
    return e;
}
// The anchors, found by structure (challenge 8 Oct: the road programs use three register layouts):
//   N   the map normal: the d9_nrm(r[a]) -> r[B] line just before the unique d9_nrm(t[3]) (the light) line;
//   SP  the spec map: the unique r[SP] = tx2 sample at t[0] (a 2D sample, so the low-passed lookup is valid);
//   H   the highlight colour: the unique (r[x].wwww) * c[1] -> r[H] line (pow(.., c[2].x) x SpecularColour);
//   S   line S: the unique (r[H] * r[SP]) -> r[OUT] line after H (highlight x 2 x spec map; SP was doubled in place);
//   the unique output line.
const char *kSheenOut = "    oc[0] = ((r[0].xyzw));\n";
struct SheenAnchors {
    size_t n_end = 0, s_pos = 0, s_len = 0;
    int nreg = -1, hreg = -1, spreg = -1, outreg = -1;
};
bool unique_at(const std::string &s, const std::string &needle, size_t *at) {
    const size_t p = s.find(needle);
    if (p == std::string::npos || s.find(needle, p + 1) != std::string::npos)
        return false;
    *at = p;
    return true;
}
// The register in "r[<n>]" at p (p at 'r'), or -1.
int reg_at(const std::string &s, size_t p) {
    if (s.compare(p, 2, "r[") != 0)
        return -1;
    int v = 0, digits = 0;
    for (p += 2; p < s.size() && s[p] >= '0' && s[p] <= '9'; ++p, ++digits)
        v = v * 10 + (s[p] - '0');
    return digits && p < s.size() && s[p] == ']' ? v : -1;
}
bool roadsheen_find(const std::string &s, SheenAnchors *a) {
    size_t l = 0, out = 0;
    if (!unique_at(s, "    { float4 w_ = (d9_nrm((t[3].xyzw))); r[", &l) || l < 2 || !unique_at(s, kSheenOut, &out))
        return false;
    const size_t ls = s.rfind('\n', l - 2);   // the line before (s[l - 1] ends it)
    if (ls == std::string::npos)
        return false;
    const std::string prev = s.substr(ls + 1, l - ls - 2);   // without its newline
    const char *np = "    { float4 w_ = (d9_nrm((r[";
    if (prev.compare(0, strlen(np), np) != 0)
        return false;
    const size_t eq = prev.find("))); r[");
    if (eq == std::string::npos || prev.size() < 16 || prev.compare(prev.size() - 16, 16, ".xyz = w_.xyz; }") != 0)
        return false;
    a->nreg = reg_at(prev, eq + 5);
    a->n_end = l;
    // SP
    size_t sp = 0, from = 0, count = 0;
    const char *spn = "] = (tx2.sample(sm2, ((t[0].xyzw)).xy));\n";
    for (size_t p = s.find(spn); p != std::string::npos; p = s.find(spn, p + 1)) {
        ++count;
        sp = p;
    }
    if (count != 1)
        return false;
    from = s.rfind("    r[", sp);
    if (from == std::string::npos)
        return false;
    a->spreg = reg_at(s, from + 4);
    // H
    const char *hn = ".wwww) * (c[1].xyzw)); r[";
    count = 0;
    size_t h = 0;
    for (size_t p = s.find(hn); p != std::string::npos; p = s.find(hn, p + 1)) {
        ++count;
        h = p;
    }
    if (count != 1 || s.rfind("    { float4 w_ = ((r[", h) == std::string::npos)
        return false;
    a->hreg = reg_at(s, h + strlen(hn) - 2);
    if (a->nreg < 0 || a->spreg < 0 || a->hreg < 0)
        return false;
    // S
    const std::string sn = "    { float4 w_ = ((r[" + std::to_string(a->hreg) + "].xyzw) * (r[" + std::to_string(a->spreg) +
                           "].xyzw)); r[";
    size_t sl = 0;
    if (!unique_at(s, sn, &sl) || sl < h || sl < l)
        return false;
    const size_t se = s.find('\n', sl);
    a->outreg = reg_at(s, sl + sn.size() - 2);
    if (se == std::string::npos || a->outreg < 0 ||
        s.substr(sl, se + 1 - sl) != sn + std::to_string(a->outreg) + "].xyz = w_.xyz; }\n")
        return false;
    a->s_pos = sl;
    a->s_len = se + 1 - sl;
    return true;
}
bool roadsheen_lines(const std::string &s) {
    SheenAnchors a;
    return roadsheen_find(s, &a);
}
// Locale-independent number text for the baked constants (challenge: %f could print a decimal comma).
std::string sheen_num(float v) {
    char b[32];
    snprintf(b, sizeof b, "%d.%04d", int(v), int((v - float(int(v))) * 10000.0f + 0.5f) % 10000);
    return b;
}
// R3 run 18 material sets (D-131; review/run18/c-design-check.md 9.4): mode 2 = the dry roughness comes from the
// material set's ORM map (d9_orm, texture 19, G = perceptual roughness, sampled with sm0 at t[0] as the diffuse): GGX
// alpha = r^2 (the constant 0.55 of mode 1 is alpha, = 0.7416^2). The wet alpha and everything else stay as mode 1.
bool roadsheen_patch(std::string *s, int mode) {
    SheenAnchors a;
    if (!roadsheen_find(*s, &a))
        return false;
    const RoadSheenEnv &e = sheen_env();
    const std::string N = std::to_string(a.nreg), H = std::to_string(a.hreg), SP = std::to_string(a.spreg),
                      O = std::to_string(a.outreg);
    // R3 run 22 mode 3 (D-136; review/run22/c-cavity-design.md): mode 2 + cavity (the ORM's R as specular occlusion)
    // + micro normals (the diffuse tx0's luminance gradient at its own mip tilts the map normal); needs the program's
    // plain diffuse lookup at t0 (else refused: the host falls back to mode 2).
    if (mode == 3 && s->find("tx0.sample(sm0, ((t[0].xyzw)).xy)") == std::string::npos)
        return false;
    std::string b = "    { float3 rs_game = ((r[" + H + "].xyzw) * (r[" + SP + "].xyzw)).xyz;\n";
    if (mode == 2)
        b += "      const float rs_og = d9_orm.sample(sm0, t[0].xy).y;\n";
    if (mode == 3)
        b += "      const float2 rs_orm = d9_orm.sample(sm0, t[0].xy).xy;\n"
             "      const float rs_og = rs_orm.y, rs_cav = rs_orm.x;\n"
             "      const float3 rs_lw = float3(0.2126, 0.7152, 0.0722);\n"
             + std::string(e.taps == 2
                   // run 23 (G-M5 cost): forward differences x 2 ~ the central difference's span; the centre is the
                   // texel the program itself just read (a cache hit), so two new taps instead of four
                   ? "      float3 rs_c0 = tx0.sample(sm0, t[0].xy).xyz;\n"
                     "      float rs_hx = 2.0 * dot(tx0.sample(sm0, t[0].xy, int2(1, 0)).xyz - rs_c0, rs_lw);\n"
                     "      float rs_hy = 2.0 * dot(tx0.sample(sm0, t[0].xy, int2(0, 1)).xyz - rs_c0, rs_lw);\n"
                   : "      float rs_hx = dot(tx0.sample(sm0, t[0].xy, int2(1, 0)).xyz - tx0.sample(sm0, t[0].xy, int2(-1, 0)).xyz, rs_lw);\n"
                     "      float rs_hy = dot(tx0.sample(sm0, t[0].xy, int2(0, 1)).xyz - tx0.sample(sm0, t[0].xy, int2(0, -1)).xyz, rs_lw);\n") +
             "      float2 rs_tilt = clamp(float2(-rs_hx, -rs_hy) * " + sheen_num(0.5f * e.bump) + ", -0.35, 0.35);\n"
             "      float3 rs_nb = normalize(rs_nm + float3(rs_tilt, 0.0));\n";
    b += "      const float rs_rough = " + (mode >= 2 ? std::string("rs_og * rs_og") : sheen_num(e.rough)) +
                    ", rs_wetr = " + sheen_num(e.wet) +
                    ", rs_nmix = " + sheen_num(e.nmix) + ", rs_bias = " + sheen_num(e.bias) + ", rs_gain = " +
                    sheen_num(e.gain) + ";\n";
    b += "      float3 rs_l = normalize(t[3].xyz), rs_v = normalize(t[4].xyz);\n";
    // mode 3: the specular AA keeps reading the un-tilted normal (the micro tilt must not turn into roughness)
    if (mode == 3)
        b += "      float3 rs_n0 = normalize(mix(float3(0.0, 0.0, 1.0), rs_nm, rs_nmix));\n"
             "      float3 rs_n = normalize(mix(float3(0.0, 0.0, 1.0), rs_nb, rs_nmix));\n"
             "      float rs_a0 = mix(rs_rough, rs_wetr, saturate(c[1].w));\n"
             "      float3 rs_dx = dfdx(rs_n0), rs_dy = dfdy(rs_n0);\n";
    else
        b += "      float3 rs_n = normalize(mix(float3(0.0, 0.0, 1.0), rs_nm, rs_nmix));\n"
             "      float rs_a0 = mix(rs_rough, rs_wetr, saturate(c[1].w));\n"
             "      float3 rs_dx = dfdx(rs_n), rs_dy = dfdy(rs_n);\n";
    b += "      float rs_aa = min(dot(rs_dx, rs_dx) + dot(rs_dy, rs_dy), 0.18);\n"
         "      float rs_a2 = clamp(rs_a0 * rs_a0 + rs_aa, 1.0e-4, 1.0);\n"
         "      float3 rs_h = normalize(rs_l + rs_v);\n"
         "      float rs_nl = saturate(dot(rs_n, rs_l)), rs_nv = clamp(dot(rs_n, rs_v), 1.0e-3, 1.0);\n"
         "      float rs_nh = saturate(dot(rs_n, rs_h)), rs_vh = saturate(dot(rs_v, rs_h));\n"
         "      float rs_dd = rs_nh * rs_nh * (rs_a2 - 1.0) + 1.0;\n"
         "      float rs_d = rs_a2 / (3.14159265 * rs_dd * rs_dd);\n"
         "      float rs_vis = 0.5 / max(rs_nl * sqrt(rs_nv * rs_nv * (1.0 - rs_a2) + rs_a2) +\n"
         "                              rs_nv * sqrt(rs_nl * rs_nl * (1.0 - rs_a2) + rs_a2), 1.0e-5);\n"
         "      float rs_f0 = 0.04 * clamp(2.0 * tx2.sample(sm2, t[0].xy, bias(rs_bias)).x, 0.0, 2.0);\n"
         "      float rs_f = rs_f0 + (1.0 - rs_f0) * pow(1.0 - rs_vh, 5.0);\n"
         "      float3 rs_s = c[0].xyz * (3.14159265 * rs_d * rs_vis * rs_f * rs_nl * rs_gain);\n";
    // Run 14 (owner 8 Oct: rain reflections "a bit strong"): the sheen on a fully wet road scaled to wetpct %, lerped
    // by the road's wetness (c1.w, as rs_a0). At 100 the patch text is unchanged.
    if (e.wetpct != 100)
        b += "      rs_s *= mix(1.0, " + sheen_num(float(e.wetpct) / 100.0f) + ", saturate(c[1].w));\n";
    // mode 3 cavity: specular occlusion of the sheen only, fading out as the road gets wet (water fills the pits)
    if (mode == 3)
        b += "      rs_s *= mix(pow(rs_cav, " + sheen_num(e.cav) + "), 1.0, saturate(c[1].w));\n";
    switch (e.debug) {
    case 1: b += "      rs_dbg = rs_n * 0.5 + 0.5;\n"; break;
    case 2: b += "      rs_dbg = rs_game;\n"; break;
    case 3: b += "      rs_dbg = rs_s;\n"; break;
    case 4: b += "      rs_dbg = float3(sqrt(rs_a2), rs_aa * 5.0, rs_f * 4.0);\n"; break;
    case 5: b += mode == 3 ? "      rs_dbg = rs_nb * 0.5 + 0.5;\n" : "      rs_dbg = rs_n * 0.5 + 0.5;\n"; break;
    default: break;
    }
    b += "      r[" + O + "].xyz = rs_s; }\n";
    std::string out = *s;
    out.replace(a.s_pos, a.s_len, b);   // S comes after the N line, so a.n_end stays valid
    out.insert(a.n_end, "    float3 rs_nm = r[" + N + "].xyz; float3 rs_dbg = float3(0.0);\n");
    if (e.debug)
        out.replace(out.find(kSheenOut), strlen(kSheenOut), "    oc[0] = float4(rs_dbg, r[0].w);\n");
    *s = out;
    return true;
}

// Remaster run 12 (PBR step 2, review/run12/PLAN.md). The PC WORLD effect's lighting, as translated: line D is
// base x d1 (the per-vertex sun), line C adds it times the shadow to base x d0 (the baked radiosity), line B scales by
// the brightness constant and adds the in-scatter t[2].
const char *kPbrD = "    { float4 w_ = ((r[0].xyzw) * (iv[1].xyzw)); r[1].xyz = w_.xyz; }\n";
const char *kPbrC = "    { float4 w_ = ((r[1].xyzw) * (r[1].wwww) + (r[0].xyzw)); r[0].xyz = w_.xyz; }\n";
const char *kPbrBHead = "    { float4 w_ = ((c[";
const char *kPbrBTail = ") * (r[0].xyzw) + (t[2].xyzw)); r[0].xyz = w_.xyz; }\n";
// The brightness line after C: its position and the brightness expression (e.g. "c[3].wwww"), or npos.
size_t pbr_find_b(const std::string &s, size_t from, std::string *bexpr, size_t *len) {
    for (size_t at = s.find(kPbrBHead, from); at != std::string::npos; at = s.find(kPbrBHead, at + 1)) {
        const size_t eol = s.find('\n', at);
        if (eol == std::string::npos)
            return std::string::npos;
        const std::string line = s.substr(at, eol + 1 - at);
        const size_t tail = line.find(kPbrBTail);
        if (tail != std::string::npos && tail + strlen(kPbrBTail) == line.size()) {
            const size_t e0 = strlen(kPbrBHead) - 2;   // "c[" starts here
            if (bexpr)
                *bexpr = line.substr(e0, tail - e0);
            if (len)
                *len = line.size();
            return at;
        }
    }
    return std::string::npos;
}

// Remaster run 12 PBR step 2 pilot settings, read once (TEST ONLY switches; baked into the patch text, so the
// parameter blocks do not change): strength (RECOMP_REMASTER_PBRWORLD_STRENGTH, default 1), exact flat override
// (_FLAT=1) and debug counters (_DEBUG=1, 2 = with the ramp-u table, 3 = ramp-v).
struct PbrWorldEnv {
    float strength = 1.0f;
    bool flat = false;
    int debug = 0;
};
const PbrWorldEnv &pbr_env() {
    static const PbrWorldEnv e = [] {
        PbrWorldEnv x;
        if (const char *v = std::getenv("RECOMP_REMASTER_PBRWORLD_STRENGTH"); v && *v)
            x.strength = std::strtof(v, nullptr);
        if (!(x.strength >= 0.0f && x.strength <= 8.0f))
            x.strength = 1.0f;
        const char *f = std::getenv("RECOMP_REMASTER_PBRWORLD_FLAT");
        x.flat = f && *f == '1';
        if (const char *d = std::getenv("RECOMP_REMASTER_PBRWORLD_DEBUG"); d && *d >= '1' && *d <= '3')
            x.debug = *d - '0';
        return x;
    }();
    return e;
}
const char *kPbrPrelude = "    float4 odepth = float4(vin.pos.z);\n";
// The pilot patch (review/run12/patch-plan.md, challenge points 4-7). Only line D changes: base x sun becomes
// base x sun x k, k = (Np.L) / ((Np.Nv) (Nv.L)) faded in over Nv.L 0.03..0.15, clamped 0..2, 1 wherever the frame
// or the inputs are unusable. Np is the slot normal (texture 17, at t[0] with sm0) in the cotangent frame of the
// eye-relative position t5 and the uv, sign-corrected by N.(dp1 x dp2) (Metal's dfdy runs down). Every derivative and
// the slot sample come first, in uniform control flow. False if the source does not have the expected lines.
bool pbrworld_patch(std::string *s, std::string *why) {
    const PbrWorldEnv &e = pbr_env();
    const size_t pre = s->find(kPbrPrelude), d = s->find(kPbrD), c = s->find(kPbrC);
    std::string bexpr;
    size_t blen = 0;
    const size_t b = c == std::string::npos ? c : pbr_find_b(*s, c, &bexpr, &blen);
    if (pre == std::string::npos || d == std::string::npos || c == std::string::npos || b == std::string::npos ||
        d > c || s->find(kPbrD, d + 1) != std::string::npos || s->find(kPbrC, c + 1) != std::string::npos) {
        if (why)
            *why = "pbrworld: the WORLD lighting lines (D, C, brightness) are not all present once";
        return false;
    }
    char sv[32];
    std::snprintf(sv, sizeof sv, "%.6f", e.strength);
    const std::string flat = e.flat ? "true" : "false";
    std::string out = *s;
    // From the end backwards, so earlier positions stay valid.
    if (e.debug) {
        std::string dbg =
            "    { const float3 pw_w = float3(0.2126, 0.7152, 0.0722);\n"
            "      uint2 pw_px = uint2(vin.pos.xy);\n"
            "      if (((pw_px.x | pw_px.y) & 7u) == 0u) {\n"
            "        float pw_b = max((" + bexpr + ").x, 0.0);\n"
            "        float pw_yn = dot(max(r[0].xyz, 0.0), pw_w);\n"
            "        float pw_yo = pw_yn - pw_b * dot(pw_sun0 * (pw_k - 1.0) * pw_sh, pw_w);\n"
            "        float pw_dy = pw_yo > 1.0e-4 ? (pw_yn - pw_yo) / pw_yo : 0.0;\n"
            "        atomic_fetch_add_explicit(&pwq[39], 1u, memory_order_relaxed);\n"
            "        if (!pw_ok) atomic_fetch_add_explicit(&pwq[38], 1u, memory_order_relaxed);\n"
            "        if (pw_jok) atomic_fetch_add_explicit(&pwq[pw_jsign > 0.0 ? 36 : 37], 1u, memory_order_relaxed);\n"
            "        atomic_fetch_add_explicit(&pwq[44], 1u, memory_order_relaxed);\n"
            "        atomic_fetch_add_explicit(&pwq[pw_dy >= 0.0 ? 45 : 46], uint(abs(pw_dy) * 256.0 + 0.5), memory_order_relaxed);\n"
            "        if (pw_sh >= 0.5 && pw_ndl >= 0.05) {\n"
            "          atomic_fetch_add_explicit(&pwq[min(uint(pw_k / 2.0 * 16.0), 15u)], 1u, memory_order_relaxed);\n"
            "          atomic_fetch_add_explicit(&pwq[16u + uint(clamp((pw_dy + 0.4) / 0.05, 0.0, 15.0))], 1u, memory_order_relaxed);\n"
            "          atomic_fetch_add_explicit(&pwq[32], 1u, memory_order_relaxed);\n"
            "          atomic_fetch_add_explicit(&pwq[pw_dy >= 0.0 ? 33 : 34], uint(abs(pw_dy) * 256.0 + 0.5), memory_order_relaxed);\n"
            "          atomic_fetch_add_explicit(&pwq[35], uint(pw_k * 64.0 + 0.5), memory_order_relaxed);\n";
        if (e.debug >= 2)
            dbg += "          if (pw_jok && pw_ndl >= 0.15 && abs(pw_rc) >= 0.05)\n"
                   "            atomic_fetch_add_explicit(&pwq[40 + (pw_rc > 0.0 ? 0 : 2) + (pw_k > 1.0 ? 0 : 1)], 1u, memory_order_relaxed);\n";
        dbg += "        }\n"
               "      }\n"
               "    }\n";
        out.insert(b + blen, dbg);
        out.insert(c, "    float pw_sh = r[1].w;\n");
    }
    std::string dl =
        "    float pw_k = 1.0;\n"
        "    float3 pw_sun0 = ((r[0].xyzw) * (iv[1].xyzw)).xyz;\n"
        "    float pw_ndl = 0.0, pw_rc = 0.0, pw_jsign = 0.0;\n"
        "    bool pw_ok = false, pw_jok = false;\n"
        "    {\n"
        "        float pw_l4 = dot(t[4].xyz, t[4].xyz);\n"
        "        float3 pw_nv = t[4].xyz * rsqrt(max(pw_l4, 1.0e-12));\n"
        "        float3 pw_l = t[6].xyz * rsqrt(max(dot(t[6].xyz, t[6].xyz), 1.0e-12));\n"
        "        float pw_sg = dot(pw_nv, cross(pw_dp1, pw_dp2)) < 0.0 ? -1.0 : 1.0;\n"
        "        float3 pw_a = cross(pw_dp2, pw_nv), pw_b = cross(pw_nv, pw_dp1);\n"
        "        float3 pw_t = (pw_a * pw_du1.x + pw_b * pw_du2.x) * pw_sg;\n"
        "        float3 pw_bt = (pw_a * pw_du1.y + pw_b * pw_du2.y) * pw_sg;\n"
        "        float pw_m = max(dot(pw_t, pw_t), dot(pw_bt, pw_bt));\n"
        "        pw_ok = isfinite(pw_m) && pw_m > 1.0e-20 && pw_l4 > 1.0e-8 && dot(t[6].xyz, t[6].xyz) > 1.0e-8;\n"
        "        float pw_inv = pw_ok ? rsqrt(pw_m) : 0.0;\n"
        "        float3 pw_nt = pw_tex.xyz * 2.0 - 1.0;\n"
        "        pw_nt.xy *= " + std::string(sv) + ";\n"
        "        float3 pw_np = (" + flat + " || !pw_ok) ? pw_nv\n"
        "                                     : normalize(pw_t * (pw_inv * pw_nt.x) + pw_bt * (pw_inv * pw_nt.y) + pw_nv * pw_nt.z);\n"
        "        pw_ndl = dot(pw_nv, pw_l);\n"
        // Mean kept by construction (run 12, after P12A G3): each pixel divides by its own tilt cosine Np.Nv, so only
        // the directional part of the relief (t . L, which averages to zero at any mip level and strength) changes the
        // sun term; k is clamped symmetrically to 0..2 around 1, so a grazing sun cannot shift the mean either. (The
        // per-key ecos of the plan overcompensated distant walls, whose filtered mips are flatter: +2..5 % at d1/d2.)
        "        float pw_nz = " + std::string(e.flat ? "1.0" : "max(dot(pw_np, pw_nv), 0.2)") + ";\n"
        "        float pw_r = precise::divide(dot(pw_np, pw_l), pw_nz * max(pw_ndl, 1.0e-3));\n"
        "        float pw_kk = clamp(mix(1.0, pw_r, smoothstep(0.03, 0.15, pw_ndl)), 0.0, 2.0);\n"
        "        pw_k = (pw_ok && isfinite(pw_kk)) ? pw_kk : 1.0;\n";
    if (e.debug)
        dl += "        float pw_det = pw_du1.x * pw_du2.y - pw_du2.x * pw_du1.y;\n"
              "        pw_jok = pw_ok && abs(pw_det) > 1.0e-12;\n"
              "        float3 pw_dpu = (pw_dp1 * pw_du2.y - pw_dp2 * pw_du1.y) / (pw_jok ? pw_det : 1.0);\n"
              "        float3 pw_dpv = (pw_dp2 * pw_du1.x - pw_dp1 * pw_du2.x) / (pw_jok ? pw_det : 1.0);\n"
              "        pw_jsign = dot(pw_t, pw_dpu);\n" +
              std::string(e.debug == 3 ? "        float3 pw_rd = pw_dpv;\n" : "        float3 pw_rd = pw_dpu;\n") +
              "        pw_rc = dot(pw_rd * rsqrt(max(dot(pw_rd, pw_rd), 1.0e-20)), pw_l);\n";
    dl += "    }\n"
          "    r[1].xyz = pw_sun0 * pw_k;\n";
    out.replace(d, strlen(kPbrD), dl);
    out.insert(pre + strlen(kPbrPrelude),
               "    float3 pw_p = t[5].xyz;\n"
               "    float2 pw_uv = t[0].xy;\n"
               "    float3 pw_dp1 = dfdx(pw_p), pw_dp2 = dfdy(pw_p);\n"
               "    float2 pw_du1 = dfdx(pw_uv), pw_du2 = dfdy(pw_uv);\n"
               "    float4 pw_tex = d9_pbrn.sample(sm0, pw_uv);\n");
    *s = out;
    return true;
}

// R3 run 29 facade glint settings, read once (baked into the patch text): micro-normal strength
// RECOMP_REMASTER_FACADEGLINT_BUMP (default 2, the walls' built-in value; the roads' D-154 value is 4; 0..8) and the
// highlight gain RECOMP_REMASTER_FACADEGLINT_GAIN (default 1; 0..8). TEST ONLY switches.
struct GlintEnv {
    float bump = 2.0f, gain = 1.0f;
};
const GlintEnv &glint_env() {
    static const GlintEnv e = [] {
        GlintEnv x;
        if (const char *v = std::getenv("RECOMP_REMASTER_FACADEGLINT_BUMP"); v && *v)
            x.bump = std::strtof(v, nullptr);
        if (!(x.bump >= 0.0f && x.bump <= 8.0f))
            x.bump = 2.0f;
        if (const char *v = std::getenv("RECOMP_REMASTER_FACADEGLINT_GAIN"); v && *v)
            x.gain = std::strtof(v, nullptr);
        if (!(x.gain >= 0.0f && x.gain <= 8.0f))
            x.gain = 1.0f;
        return x;
    }();
    return e;
}
// R3 run 29 facade glint (review/run29/c-facade-design.md 2): the sun's specular on walls, from the material set. The
// GGX is the road sheen's (mode 3: alpha = r^2 from the ORM's G, specular AA on the un-tilted normal, the two-tap
// micro normals from tx0's luminance, cavity as specular occlusion), written apart so the road programs' text never
// changes. N = the vertex normal t4 tilted in the cotangent frame of t5 and the uv (pbrworld's frame); L = t6, V = -t5.
// The sun colour x N.L is the per-vertex d1 (iv[1]); the tilted N.L only masks the lobe (no glint from behind). Added
// to r[1] just before line C, so the shadow (r[1].w) and the brightness constant apply; the diffuse is unchanged
// (D-136: the texture detail stays). Every derivative comes before any lookup that depends on it. False if the source
// lacks line C once or the plain stage-0 lookup at t0.
bool facadeglint_patch(std::string *s, std::string *why) {
    const GlintEnv &e = glint_env();
    const size_t c = s->find(kPbrC);
    if (c == std::string::npos || s->find(kPbrC, c + 1) != std::string::npos ||
        s->find("tx0.sample(sm0, ((t[0].xyzw)).xy)") == std::string::npos || s->find(kPbrPrelude) == std::string::npos) {
        if (why)
            *why = "facadeglint: line C once and the plain stage-0 lookup are not both present";
        return false;
    }
    const std::string b =
        "    { float3 fg_p = t[5].xyz;\n"
        "      float2 fg_uv = t[0].xy;\n"
        "      float3 fg_dp1 = dfdx(fg_p), fg_dp2 = dfdy(fg_p);\n"
        "      float2 fg_du1 = dfdx(fg_uv), fg_du2 = dfdy(fg_uv);\n"
        "      float3 fg_nv = t[4].xyz * rsqrt(max(dot(t[4].xyz, t[4].xyz), 1.0e-12));\n"
        "      float3 fg_ndx = dfdx(fg_nv), fg_ndy = dfdy(fg_nv);\n"
        "      float3 fg_l = t[6].xyz * rsqrt(max(dot(t[6].xyz, t[6].xyz), 1.0e-12));\n"
        "      float3 fg_v = -fg_p * rsqrt(max(dot(fg_p, fg_p), 1.0e-12));\n"
        "      float fg_sg = dot(fg_nv, cross(fg_dp1, fg_dp2)) < 0.0 ? -1.0 : 1.0;\n"
        "      float3 fg_a = cross(fg_dp2, fg_nv), fg_b = cross(fg_nv, fg_dp1);\n"
        "      float3 fg_t = (fg_a * fg_du1.x + fg_b * fg_du2.x) * fg_sg;\n"
        "      float3 fg_bt = (fg_a * fg_du1.y + fg_b * fg_du2.y) * fg_sg;\n"
        "      float fg_m = max(dot(fg_t, fg_t), dot(fg_bt, fg_bt));\n"
        "      bool fg_ok = isfinite(fg_m) && fg_m > 1.0e-20;\n"
        "      float fg_inv = fg_ok ? rsqrt(fg_m) : 0.0;\n"
        "      float4 fg_orm = d9_orm.sample(sm0, fg_uv);\n"
        "      const float3 fg_lw = float3(0.2126, 0.7152, 0.0722);\n"
        "      float3 fg_c0 = tx0.sample(sm0, fg_uv).xyz;\n"
        "      float fg_hx = 2.0 * dot(tx0.sample(sm0, fg_uv, int2(1, 0)).xyz - fg_c0, fg_lw);\n"
        "      float fg_hy = 2.0 * dot(tx0.sample(sm0, fg_uv, int2(0, 1)).xyz - fg_c0, fg_lw);\n"
        "      float2 fg_tilt = clamp(float2(-fg_hx, -fg_hy) * " + sheen_num(0.5f * e.bump) + ", -0.35, 0.35);\n"
        "      float3 fg_n = fg_ok ? normalize(fg_nv + (fg_t * fg_tilt.x + fg_bt * fg_tilt.y) * fg_inv) : fg_nv;\n"
        "      float fg_r = fg_orm.y * fg_orm.y;\n"
        "      float fg_aa = min(dot(fg_ndx, fg_ndx) + dot(fg_ndy, fg_ndy), 0.18);\n"
        "      float fg_a2 = clamp(fg_r * fg_r + fg_aa, 1.0e-4, 1.0);\n"
        "      float3 fg_h = normalize(fg_l + fg_v);\n"
        "      float fg_nl = saturate(dot(fg_n, fg_l)), fg_nw = clamp(dot(fg_n, fg_v), 1.0e-3, 1.0);\n"
        "      float fg_nh = saturate(dot(fg_n, fg_h)), fg_vh = saturate(dot(fg_v, fg_h));\n"
        "      float fg_dd = fg_nh * fg_nh * (fg_a2 - 1.0) + 1.0;\n"
        "      float fg_d = fg_a2 / (3.14159265 * fg_dd * fg_dd);\n"
        "      float fg_vis = 0.5 / max(fg_nl * sqrt(fg_nw * fg_nw * (1.0 - fg_a2) + fg_a2) +\n"
        "                               fg_nw * sqrt(fg_nl * fg_nl * (1.0 - fg_a2) + fg_a2), 1.0e-5);\n"
        "      float3 fg_f0 = mix(float3(0.04), saturate(fg_c0), saturate(fg_orm.z));\n"
        "      float3 fg_f = fg_f0 + (1.0 - fg_f0) * pow(1.0 - fg_vh, 5.0);\n"
        "      float3 fg_s = max(iv[1].xyz, 0.0) * fg_f * (3.14159265 * fg_d * fg_vis * " + sheen_num(e.gain) +
        " * smoothstep(0.0, 0.05, fg_nl) * saturate(fg_orm.x));\n"
        "      r[1].xyz += (fg_ok && isfinite(fg_s.x + fg_s.y + fg_s.z)) ? fg_s : float3(0.0); }\n";
    s->insert(c, b);
    return true;
}

// R3 run 29 O1 (review/run29/opt-design.md): the run-27 scenery fade-in patch (was applied to every pixel shader at
// compile time in d3d9_metal.mm d9_msl_source): before the epilogue, a fragment is discarded while an ordered 4x4 dither
// value is at or above P.fade.x (render state 243 / 255); the parameter block gains "float4 fade" as its last member.
void fade_patch(std::string *s) {
    static const char kAt[] = "    (void)aL;\n";
    static const char kPs[] = "float pad1;\n    int4 bconst[4];\n};";
    const size_t at = s->find(kAt), ps = s->find(kPs);
    if (at == std::string::npos || ps == std::string::npos || ps >= at)
        return;
    s->insert(at, "    if (P.fade.x > 0.0) { uint2 fq = uint2(vin.pos.xy) & 3u; "
                  "const uint fb[16] = {0u, 8u, 2u, 10u, 12u, 4u, 14u, 6u, 3u, 11u, 1u, 9u, 15u, 7u, 13u, 5u}; "
                  "if ((float(fb[fq.y * 4u + fq.x]) + 0.5) / 16.0 >= P.fade.x) discard_fragment(); }\n");
    s->insert(ps + sizeof kPs - 1 - 2, "    float4 fade;\n");
}

// ---- Remaster run 13 natural water (review/run13/x360-study-design.md 3(c), census 8 Oct) ----------------------------
// PC water is a WORLD program with an ANM_WATERA_ flipbook frame at stage 0 (the host selects by key): base = stage 0
// at tc[0], d0 = baked light x extinction, d1 = N.L x sun colour, t2 = in-scatter (fog add). Census 8 Oct: half the
// main-view water draws use each of two forms, every anchor exactly once and in order, no read of t4..t7 in the body
// (the pbr vertex variant writes them):
//   form 1 (ps 1.x, ps 8abb0769bddd4158): T0 the stage-0 sample; T2 the in-scatter texcoord; L the lit line
//     2 x (base x d1 + base x d0) -> r0.rgb; A the in-scatter add; the ps 1.x output line;
//   form 2 (ps 2.0 shadowed, ps cc1387a106b0820e): the stage-0 sample -> r0; D base x d1 -> r1.rgb; C r1 x shadow (r1.w)
//     + base x d0 -> r0.rgb; B brightness c3.w x r0 + in-scatter; the oC0 line.
//   form 3 (ps 2.0 shadow receiver, ps 629cd2e4c94c69f8; run 13 census: most bay water): one shadow-map compare
//     instead of form 2's four, otherwise form 2's lines with the brightness in c1.z: the stage-0 sample -> r0; D base
//     x d1 -> r1.rgb; S the sun visibility r1.w = sat(r1.w + c0.x); C r1 x r1.w + base x d0 -> r0.rgb; B c1.z x r0 +
//     in-scatter; the oC0 line. (Form 2 also has an S line, with c1.x; it is matched first by its own B.)
// The patch inserts one line after L (form 1) or C (forms 2, 3): r0.rgb = d9w_water(lit, ...); forms 2 and 3 pass the
// shadow (r1.w, already saturated by S) and 1 / brightness (c3.w, c1.z: the scale B applies after the insert), so the
// sky and the sun come out unscaled as in form 1; the light is ambient d0 + shadowed sun d1 x r1.w, the in-scatter is
// the t2 texcoord (B adds t2 unsaturated). Alpha, the in-scatter and fog unchanged.
const char *kWaterT0 = "    t[0] = (tx0.sample(sm0, (tc[0]).xy));\n";
const char *kWaterT2 = "    t[2] = (float4(saturate(tc[2].xyz), 1.0));\n";
const char *kWaterL = "    { float4 w_ = (((t[0].xyzw) * (iv[1].xyzw) + (r[0].xyzw)) * 2.0); r[0].xyz = w_.xyz; }\n";
const char *kWaterA = "    { float4 w_ = ((r[0].xyzw) + (t[2].xyzw)); r[0].xyz = w_.xyz; }\n";
const char *kWaterOut = "    float4 col = saturate(r[0]);\n";
const char *kWaterBody = "    float4 odepth = float4(vin.pos.z);\n";
const char *kWater2T0 = "    r[0] = (tx0.sample(sm0, ((t[0].xyzw)).xy));\n";
const char *kWater2D = "    { float4 w_ = ((r[0].xyzw) * (iv[1].xyzw)); r[1].xyz = w_.xyz; }\n";
const char *kWater2C = "    { float4 w_ = ((r[1].xyzw) * (r[1].wwww) + (r[0].xyzw)); r[0].xyz = w_.xyz; }\n";
const char *kWater2B = "    { float4 w_ = ((c[3].wwww) * (r[0].xyzw) + (t[2].xyzw)); r[0].xyz = w_.xyz; }\n";
const char *kWater2Out = "    oc[0] = ((r[0].xyzw));\n";
const char *kWater3S = "    { float4 w_ = saturate((r[1].wwww) + (c[0].xxxx)); r[1].w = w_.w; }\n";
const char *kWater3B = "    { float4 w_ = ((c[1].zzzz) * (r[0].xyzw) + (t[2].xyzw)); r[0].xyz = w_.xyz; }\n";
// The insertion point and the line to insert, or false.
bool water_find(const std::string &s, size_t *l_end, std::string *call) {
    size_t t0, t2, l, a, out, body;
    if (!unique_at(s, kWaterBody, &body))
        return false;
    const char *anchor = nullptr;
    if (unique_at(s, kWaterT0, &t0) && unique_at(s, kWaterT2, &t2) && unique_at(s, kWaterL, &l) &&
        unique_at(s, kWaterA, &a) && unique_at(s, kWaterOut, &out) && body < t0 && t0 < l && t2 < a && l < a &&
        a < out) {
        anchor = kWaterL;
        *call = "    r[0].xyz = d9w_water(r[0].xyz, tc[0].xy, vin.t4.xyz, vin.t5.xyz, vin.t6.xyz, iv[1].xyz, t[2].xyz, "
                "P.pad1, 1.0, 1.0, 2.0 * (iv[0].xyz + iv[1].xyz), D9W_MODE, D9W_TINT);\n";
    } else if (unique_at(s, kWater2T0, &t0) && unique_at(s, kWater2D, &t2) && unique_at(s, kWater2C, &l) &&
               unique_at(s, kWater2B, &a) && unique_at(s, kWater2Out, &out) && body < t0 && t0 < t2 && t2 < l &&
               l < a && a < out) {
        anchor = kWater2C;
        *call = "    r[0].xyz = d9w_water(r[0].xyz, tc[0].xy, vin.t4.xyz, vin.t5.xyz, vin.t6.xyz, iv[1].xyz, tc[2].xyz, "
                "P.pad1, saturate(r[1].w), 1.0 / max(c[3].w, 1.0e-3), iv[1].xyz * saturate(r[1].w) + iv[0].xyz, "
                "D9W_MODE, D9W_TINT);\n";
    } else if (size_t sv; unique_at(s, kWater2T0, &t0) && unique_at(s, kWater2D, &t2) && unique_at(s, kWater3S, &sv) &&
               unique_at(s, kWater2C, &l) && unique_at(s, kWater3B, &a) && unique_at(s, kWater2Out, &out) &&
               body < t0 && t0 < t2 && t2 < sv && sv < l && l < a && a < out) {
        anchor = kWater2C;   // form 3: form 2's call with the brightness c1.z (see above)
        *call = "    r[0].xyz = d9w_water(r[0].xyz, tc[0].xy, vin.t4.xyz, vin.t5.xyz, vin.t6.xyz, iv[1].xyz, tc[2].xyz, "
                "P.pad1, saturate(r[1].w), 1.0 / max(c[1].z, 1.0e-3), iv[1].xyz * saturate(r[1].w) + iv[0].xyz, "
                "D9W_MODE, D9W_TINT);\n";
    } else {
        return false;
    }
    const std::string b = s.substr(body, out - body);
    for (const char *r : {"t[4]", "t[5]", "t[6]", "t[7]", "vin.t4", "vin.t5", "vin.t6", "vin.t7"})
        if (b.find(r) != std::string::npos)
            return false;
    *l_end = l + strlen(anchor);
    return true;
}
// The water shading, one function before ps_main. Inputs: lit = the game's lit colour (line L), uv = the stage-0
// coordinate, n_in/pe/l_in = t4..t6 of the pbr vertex variant (NORMAL, POSITION - eye, sun direction), d1 = N.L x sun
// colour, fogadd = the in-scatter, time = P.pad1 seconds (the host wraps it at 1000 s; every rate below is n/1000 Hz,
// so the wrap is seamless). Every derivative first, in uniform control flow.
const char *kWaterFn =
    "constant float4 d9w_wave[4] = { float4(0.8, 0.6, 3.0, 0.050), float4(-0.6, 0.8, 4.7, 0.040),\n"
    "                                float4(0.28, -0.96, 7.9, 0.030), float4(-0.92, -0.39, 11.3, 0.022) };\n"
    "constant float d9w_rate[4] = { 0.35, -0.48, 0.61, -0.83 };\n"
    "static float3 d9w_n(float3 v, float3 f) { float l = dot(v, v); return l > 1.0e-12 ? v * rsqrt(l) : f; }\n"
    "static float3 d9w_water(float3 lit, float2 uv, float3 n_in, float3 pe, float3 l_in, float3 d1, float3 fogadd,\n"
    "                        float time, float shadow, float post, float3 light, int mode, float3 tint) {\n"
    "    float3 dp1 = dfdx(pe), dp2 = dfdy(pe);\n"
    "    float2 du1 = dfdx(uv), du2 = dfdy(uv);\n"
    "    float3 v = d9w_n(-pe, float3(0.0, 0.0, 1.0));\n"
    "    float3 ng = d9w_n(n_in, v);\n"
    "    if (dot(ng, v) < 0.0) ng = -ng;\n"
    "    float3 l = d9w_n(l_in, ng);\n"
    // the surface gradients of u and v (cotangent frame, sign-corrected: Metal's dfdy runs down), unit length
    "    float3 dp2p = cross(dp2, ng), dp1p = cross(ng, dp1);\n"
    "    float sg = dot(dp1, dp2p) < 0.0 ? -1.0 : 1.0;\n"
    "    float3 tf = d9w_n(cross(abs(ng.z) < 0.9 ? float3(0.0, 0.0, 1.0) : float3(1.0, 0.0, 0.0), ng), float3(1.0, 0.0, 0.0));\n"
    "    float3 tu = d9w_n(sg * (dp2p * du1.x + dp1p * du2.x), tf);\n"
    "    float3 tv = d9w_n(sg * (dp2p * du1.y + dp1p * du2.y), cross(ng, tu));\n"
    "    float fw = max(length(du1), length(du2));\n"
    // ripples: two pairs of directional sines scrolling in opposite directions (slopes; a wave finer than about two
    // pixels fades out instead of aliasing)
    "    float2 sl = float2(0.0);\n"
    "    for (int i = 0; i < 4; ++i) {\n"
    "        float4 w = d9w_wave[i];\n"
    "        float ph = 6.2831853 * (w.z * dot(w.xy, uv) + d9w_rate[i] * time);\n"
    "        sl += w.xy * (w.w * saturate(1.0 - 2.0 * w.z * fw) * cos(ph));\n"
    "    }\n"
    "    float3 n = d9w_n(ng - sl.x * tu - sl.y * tv, ng);\n"
    "    float nv = clamp(dot(n, v), 1.0e-4, 1.0);\n"
    "    float f = 0.02 + 0.98 * pow(1.0 - nv, 5.0);\n"
    // the sun colour from d1 (= N.L x sun colour per vertex); the sky: a tint of it, brightened toward the horizon and
    // never darker there than the in-scatter (no planar reflection exists on PC water)
    "    float ngl = dot(ng, l);\n"
    "    float3 sunc = saturate(d1) / max(ngl, 0.1);\n"
    "    float3 rv = reflect(-v, n);\n"
    "    float hz = 1.0 - saturate(dot(rv, ng));\n"
    "    float3 zen = sunc * float3(0.20, 0.30, 0.48);\n"
    "    float3 hor = max(sunc * float3(0.50, 0.58, 0.68), fogadd * 2.0);\n"
    "    float3 sky = mix(zen, hor, hz * hz);\n"
    // the sun glint: GGX alpha 0.06, height-correlated Smith visibility, Schlick at V.H, faded as the sun sets
    "    float3 h = d9w_n(v + l, n);\n"
    "    float nl = saturate(dot(n, l)), nh = saturate(dot(n, h)), vh = saturate(dot(v, h));\n"
    "    const float a2 = 0.06 * 0.06;\n"
    "    float dd = nh * nh * (a2 - 1.0) + 1.0;\n"
    "    float dg = a2 / (3.14159265 * dd * dd);\n"
    "    float vis = 0.5 / max(nl * sqrt(nv * nv * (1.0 - a2) + a2) + nv * sqrt(nl * nl * (1.0 - a2) + a2), 1.0e-4);\n"
    "    float fh = 0.02 + 0.98 * pow(1.0 - vh, 5.0);\n"
    "    if (mode == 2) {\n"
    // Xbox 360 mode (our own code after the 360 WATERSEASHADER maths, x360-shader-map.md WATER): no Fresnel; a constant
    // water tint lit by the game's light; reflectance 0.2 + 0.4 x the reflection (here the sky, whose lookup direction
    // carries the ripple normal as the 360's 0.1 x N.xy offset does); sun pow(R.L, 30) x shadow
    "        float rk = 0.2 + 0.4 * saturate(dot(sky, float3(0.2126, 0.7152, 0.0722)));\n"
    "        float3 sun2 = sunc * (pow(saturate(dot(rv, l)), 30.0) * shadow * saturate(ngl * 4.0));\n"
    "        return tint * light * (1.0 - rk) + (sky * rk + sun2) * post;\n"
    "    }\n"
    "    float3 sun = min(sunc * (dg * vis * fh * nl * saturate(ngl * 4.0) * shadow), float3(4.0));\n"
    "    return lit * (0.6 * (1.0 - f)) + (sky * f + sun) * post;\n"
    "}\n";
// mode 1 = Definitive, 2 = Xbox 360 (PixelVariant.water); the 360 tint from RECOMP_REMASTER_WATER_TINT "r,g,b"
// (default 0.28,0.30,0.32), baked into the source.
bool water_patch(std::string *s, int mode) {
    size_t at;
    std::string call;
    if ((mode != 1 && mode != 2) || !water_find(*s, &at, &call))
        return false;
    float tr = 0.28f, tg = 0.30f, tb = 0.32f;
    if (const char *e = getenv("RECOMP_REMASTER_WATER_TINT"); e && *e) {
        float a, b, c;
        if (sscanf(e, "%f,%f,%f", &a, &b, &c) == 3 && a >= 0.0f && b >= 0.0f && c >= 0.0f && a <= 4.0f && b <= 4.0f &&
            c <= 4.0f)
            tr = a, tg = b, tb = c;
    }
    char tint[96];
    snprintf(tint, sizeof tint, "float3(%.4f, %.4f, %.4f)", tr, tg, tb);
    for (size_t p; (p = call.find("D9W_MODE")) != std::string::npos;)
        call.replace(p, 8, mode == 2 ? "2" : "1");
    for (size_t p; (p = call.find("D9W_TINT")) != std::string::npos;)
        call.replace(p, 8, tint);
    s->insert(at, call);
    const size_t f = s->find("fragment D9PSOut ps_main(");
    if (f == std::string::npos)
        return false;
    s->insert(f, kWaterFn);
    return true;
}
// ---- end natural water ----------------------------------------------------------------------------------------------

// PLAN_R2 item 10 real HDR (review/run14/hdr-design.md s2.2): a float target does not clamp what the shader writes,
// Direct3D 9 does (to the 8-bit target's range, before blending). The clamp, inserted after every patch, right before the
// output struct: alpha to [0,1] (blend factors, as D3D9), rgb to [0, ceiling] (1: as PC; 2: 64, light keeps its excess).
static bool hdr_epilogue(std::string *s, uint8_t cls) {
    const std::string at = "    D9PSOut o;\n";
    const size_t pos = s->rfind(at);
    if (pos == std::string::npos || s->find(at) != pos)
        return false;
    s->insert(pos, std::string("    col.a = saturate(col.a);   // item 10: the D3D9 output clamp on the float frame\n"
                               "    col.rgb = clamp(col.rgb, 0.0, ") + (cls == 2 ? "64.0" : "1.0") + ");\n");
    return true;
}

// Remaster run 14 lit smoke (review/run14/smoke-realism-design.md 4 stage 1, 7): the relight of `col` for one kind
// (smoke_light.h: 1 tyre, 2 red flare, 3 stack steam, 4 stack soot, 5 fog/mist, 6 vent steam, 7 green flare). SP =
// D9SmokeParams (buffer(5)); world position in vin.t7; stage 0 = the sprite at t[0]; `shadow1`: stage 1 is the sun
// shadow map the particle program already reads at t[1] (projected), used as the sun's visibility. The maths has a
// C++ twin in host/gpu/metal/smoke_light.h (smoke_relight), unit-tested against the same reference.
static std::string smoke_epilogue(uint32_t kind, bool shadow1) {
    const bool flat = kind == 5, flare = kind == 2 || kind == 7;
    std::string s = "    {   // run 14 lit smoke, kind " + std::to_string(kind) + (flat ? " (flat: fog/mist)" : "") +
                    "\n"
                    "        const float3 smk_p = vin.t7.xyz;\n"
                    "        const float3 smk_v = normalize(SP.eye.xyz - smk_p);\n"
                    "        const float3 smk_l = SP.sun.xyz;\n"
                    "        const float smk_a = saturate(col.a);\n"
                    "        float smk_vis = saturate(SP.sun.w);\n";
    if (shadow1)
        s += "        smk_vis *= d9_shadow(tx1, t[1] / t[1].w).r;\n";
    if (flat)
        s += "        const float smk_wrap = 0.5;\n";
    else
        s += "        // sphere normal from the sprite uv, tilted by the alpha gradient (alpha as height), in world space via\n"
             "        // the uv/position derivatives (the sprite's spin and handedness come with them)\n"
             "        const float2 smk_uv = t[0].xy;\n"
             "        const float2 smk_q = smk_uv * 2.0 - 1.0;\n"
             "        const float2 smk_h = 1.0 / float2(float(max(tx0.get_width(), 1u)), float(max(tx0.get_height(), 1u)));\n"
             "        const float smk_gx = tx0.sample(sm0, smk_uv + float2(smk_h.x, 0.0)).a - tx0.sample(sm0, smk_uv - float2(smk_h.x, 0.0)).a;\n"
             "        const float smk_gy = tx0.sample(sm0, smk_uv + float2(0.0, smk_h.y)).a - tx0.sample(sm0, smk_uv - float2(0.0, smk_h.y)).a;\n"
             "        const float2 smk_g2 = float2(smk_gx / (2.0 * smk_h.x), smk_gy / (2.0 * smk_h.y)) * SP.k2.x;\n"
             "        const float3 smk_ns = float3(smk_q - smk_g2, sqrt(saturate(1.0 - dot(smk_q, smk_q))) + 0.25);\n"
             "        const float3 smk_d1 = dfdx(smk_p), smk_d2 = dfdy(smk_p);\n"
             "        const float2 smk_u1 = dfdx(smk_uv), smk_u2 = dfdy(smk_uv);\n"
             "        float3 smk_f = cross(smk_d1, smk_d2);\n"
             "        const float smk_fl = length(smk_f);\n"
             "        smk_f = smk_fl > 1e-12 ? smk_f / smk_fl : smk_v;\n"
             "        smk_f = dot(smk_f, smk_v) < 0.0 ? -smk_f : smk_f;\n"
             "        const float3 smk_p2 = cross(smk_d2, smk_f), smk_p1 = cross(smk_f, smk_d1);\n"
             "        float3 smk_t = smk_p2 * smk_u1.x + smk_p1 * smk_u2.x, smk_b = smk_p2 * smk_u1.y + smk_p1 * smk_u2.y;\n"
             "        const float smk_im = rsqrt(max(max(dot(smk_t, smk_t), dot(smk_b, smk_b)), 1e-30));\n"
             "        const float3 smk_n = normalize(smk_t * (smk_im * smk_ns.x) + smk_b * (smk_im * smk_ns.y) + smk_f * smk_ns.z);\n"
             "        const float smk_wrap = saturate(dot(smk_n, smk_l) * 0.5 + 0.5);\n";
    s += "        // half-Lambert x alpha self-shadow + (1 - alpha)^p transmission x Henyey-Greenstein (4 pi normalised)\n"
         "        const float smk_mu = -dot(smk_l, smk_v);\n"
         "        const float smk_g = clamp(SP.k.y, -0.95, 0.95);\n"
         "        const float smk_hg = (1.0 - smk_g * smk_g) / pow(max(1.0 + smk_g * smk_g - 2.0 * smk_g * smk_mu, 1e-4), 1.5);\n"
         "        const float smk_tr = pow(max(1.0 - smk_a, 0.0), SP.k2.y);\n"
         "        const float smk_dt = smk_wrap * (1.0 - SP.k.w * smk_a) + SP.k2.z * smk_tr * smk_hg;\n"
         "        const float smk_lt = SP.amb.x + smk_vis * SP.k.z * smk_dt;\n"
         "        const float smk_lr = SP.amb.x + smk_vis * SP.k.z * SP.amb.y;\n"
         "        const float smk_r = clamp(smk_lt / max(smk_lr, 1e-4), 0.35, 3.0);\n"
         "        col.rgb *= mix(1.0, smk_r, saturate(SP.k.x));\n";
    if (flat)   // fog in shade: soft grey-blue (the sky's fill), none where the sun reaches it
        s += "        col.rgb = mix(col.rgb, col.rgb * float3(0.90, 0.96, 1.08), SP.amb.z * (1.0 - smk_vis));\n";
    if (flare)   // the burning flare lights its own smoke from inside: strong at the flare, gone a few metres away
        s += "        const float3 smk_fd = smk_p - SP.light.xyz;\n"
             "        col.rgb += SP.lcol.rgb * (SP.light.w / (1.0 + dot(smk_fd, smk_fd) / max(SP.lcol.w * SP.lcol.w, 1e-4)));\n";
    if (kind == 6)   // vent steam evaporates: the thin edges go first
        s += "        col.a *= smoothstep(0.0, max(SP.amb.w, 1e-3), smk_a);\n";
    s += "        if (SP.nearf.y > 0.0)   // near-camera fade: no pop when the camera passes through\n"
         "            col.a *= saturate((1.0 / vin.pos.w - SP.nearf.x) * SP.nearf.y);\n"
         "        if (SP.nearf.z == 1.0) col.rgb = float3(0.5 * smk_r);\n"
         "        else if (SP.nearf.z == 2.0) col.rgb = float3(" +
         std::string(kind == 1 ? "0.9, 0.9, 0.9" : flare ? "1.0, 0.1, 0.1" : kind == 3 ? "0.2, 0.6, 1.0" : kind == 4 ? "0.3, 0.2, 0.1" : kind == 5 ? "0.2, 1.0, 0.3" : "1.0, 0.0, 1.0") +
         ");\n"
         "    }\n";
    return s;
}

// R3 run 22 (TEST): the texture mip bias under upscaling (PixelVariant::mipbias). Every tx<n>.sample(sm<n>, uv) call
// with two arguments gets a third, bias(b); one whose third argument is bias(x) gets bias(b + x), one whose third is a
// texel offset int2(..) gets bias(b) before it (MSL order); level(), gradient
// and min_lod_clamp lookups are left alone (an explicit LOD is the program's own choice).
bool mipbias_patch(std::string *s, uint8_t code, uint32_t *count) {
    if (count)
        *count = 0;
    if (!code)
        return true;
    char b[32];
    snprintf(b, sizeof b, "%.6f", mipbias_value(code));
    const std::string bs = b;
    std::string r = *s;
    uint32_t n = 0;
    size_t pos = 0;
    while ((pos = r.find(".sample(sm", pos)) != std::string::npos) {
        size_t k = pos;
        while (k > 0 && std::isdigit((unsigned char)r[k - 1]))
            --k;
        const bool tx = k < pos && k >= 2 && r.compare(k - 2, 2, "tx") == 0 &&
                        (k == 2 || !(std::isalnum((unsigned char)r[k - 3]) || r[k - 3] == '_'));
        const size_t open = pos + 7;
        size_t close = std::string::npos;
        size_t commas[3] = {0, 0, 0};
        int nc = 0, depth = 0;
        for (size_t i = open; i < r.size(); ++i) {
            const char c = r[i];
            if (c == '(') {
                ++depth;
            } else if (c == ')') {
                if (--depth == 0) {
                    close = i;
                    break;
                }
            } else if (c == ',' && depth == 1 && nc < 3) {
                commas[nc++] = i;
            }
        }
        if (close == std::string::npos)
            return false;
        if (!tx) {
            pos = open;
            continue;
        }
        if (nc == 1) {
            r.insert(close, ", bias(" + bs + ")");
            ++n;
        } else if (nc == 2) {
            size_t a = commas[1] + 1;
            while (a < close && r[a] == ' ')
                ++a;
            if (r.compare(a, 5, "bias(") == 0) {
                r.insert(a + 5, bs + " + ");
                ++n;
            } else if (r.compare(a, 5, "int2(") == 0) {   // a texel offset: MSL takes the bias before it
                r.insert(a, "bias(" + bs + "), ");
                ++n;
            }
        }
        pos = open;
    }
    *s = r;
    if (count)
        *count = n;
    return true;
}

bool pixel_source(const Program &p, const PixelVariant &v, std::string *out, std::string *why) {
    if (!p.ok || !p.pixel) {
        if (why)
            *why = p.ok ? "not a pixel shader" : p.why;
        return false;
    }
    Gen g(p, Lang::MSL);
    g.variant = v;
    if (!generate(g, why))
        return false;
    if (v.pool) {   // Remaster run 10: the pool term samples stage 0 (diffuse) and, for pool 1, stage 1 (normal map) as 2D
        const uint32_t last = v.pool == 1 ? 1 : 0;
        for (uint32_t st = 0; st <= last; ++st)
            if (p.major < 2 || g.is_depth(st) || g.kind_of(st) != S_2D) {
                if (why)
                    *why = "pool variant: stage 0 or 1 is not plain 2D";
                return false;
            }
        for (uint32_t st = 0; st <= last; ++st)
            g.stages.insert(st);
    }
    if (v.smoke && (v.smoke > 7 || !g.stages.count(0) || g.is_depth(0) || g.kind_of(0) != S_2D)) {
        if (why)   // run 14 lit smoke: never "lit", drawn plain (the host falls back)
            *why = "smoke variant: stage 0 is not a plain 2D sprite, or the kind is unknown";
        return false;
    }
    std::string s = kPrelude;
    bool shadow = false;
    for (uint32_t st : g.stages)
        shadow |= g.is_depth(st);
    shadow |= v.fefloor != 0;   // run 13 front-end floor shadows: the floor samples the map at texture 18 (d9_shadow)
    if (shadow && v.shadowpcf)   // Remaster R2 softer sun shadows: our own 12-tap filter (Poisson disc, fixed pattern)
        s += "constexpr sampler d9_shadow_sampler(coord::normalized, address::clamp_to_edge, "
             "filter::linear,\n"
             "                                    compare_func::less_equal);\n"
             "constant float2 d9_pcf_k[8] = {float2(-0.613,0.617), float2(0.170,-0.040), float2(-0.299,0.792),\n"
             "    float2(0.645,0.493), float2(-0.651,-0.717), float2(0.421,-0.027), float2(-0.817,-0.271),\n"
             "    float2(0.977,-0.108)};\n"
             "static float4 d9_shadow(depth2d<float> t, float4 c) {\n"
             "    const float2 ts = 2.6 / float2(t.get_width(), t.get_height());\n"
             "    const float z = saturate(c.z) - 0.0004;\n"
             "    float r = 0.0;\n"
             "    for (int i = 0; i < 8; ++i)\n"
             "        r += t.sample_compare(d9_shadow_sampler, c.xy + d9_pcf_k[i] * ts, z, level(0));\n"
             "    r *= (1.0 / 8.0);\n"
             "    return float4(r, r, r, 1.0);\n"
             "}\n";
    else if (shadow)
        s += "constexpr sampler d9_shadow_sampler(coord::normalized, address::clamp_to_edge, "
             "filter::linear,\n"
             "                                    compare_func::less_equal);\n"
             "static float4 d9_shadow(depth2d<float> t, float4 c) {\n"
             "    float r = t.sample_compare(d9_shadow_sampler, c.xy, saturate(c.z), level(0));\n"
             "    return float4(r, r, r, 1.0);\n"
             "}\n";
    bool depth = g.writes_depth;
    uint32_t colors = p.major >= 2 ? (g.color_outputs | 1) : 1;
    if (v.smoke)   // run 14 lit smoke: D9SmokeParams (d3d9_msl.h), eight float4
        s += "struct D9SmokeParams { float4 sun; float4 eye; float4 k; float4 k2; float4 amb; float4 light; float4 lcol; "
             "float4 nearf; };\n";
    s += "struct D9PSOut {\n";
    for (uint32_t i = 0; i < 4; ++i)
        if (colors >> i & 1)
            s += "    float4 c" + std::to_string(i) + " [[color(" + std::to_string(i) + ")]];\n";
    if (depth)
        s += "    float depth [[depth(any)]];\n";
    if (v.a2c)
        s += "    uint mask [[sample_mask]];\n";
    s += "};\n";
    s += "fragment D9PSOut ps_main(D9V2F vin [[stage_in]], constant float4 *c [[buffer(0)]],\n"
         "                         constant D9PSParams &P [[buffer(1)]]";
    for (uint32_t st : g.stages) {
        const char *type = g.kind_of(st) == S_CUBE     ? "texturecube<float>"
                           : g.kind_of(st) == S_VOLUME ? "texture3d<float>"
                           : g.is_depth(st)            ? "depth2d<float>"
                                                       : "texture2d<float>";
        s += ",\n                         " + std::string(type) + " tx" + std::to_string(st) +
             " [[texture(" + std::to_string(st) + ")]], sampler sm" + std::to_string(st) +
             " [[sampler(" + std::to_string(st) + ")]]";
    }
    if (v.soft)
        s += ",\n                         texture2d<float> d9_soft [[texture(16)]]";
    if (v.smoke)   // run 14 lit smoke
        s += ",\n                         constant D9SmokeParams &SP [[buffer(5)]]";
    if (v.pool)
        s += ",\n                         constant float4 *pb [[buffer(2)]]";
    if (v.roadsheen >= 2 || v.facadeglint)   // R3 run 18 material sets: the ORM map (run 22: mode 3; run 29: walls)
        s += ",\n                         texture2d<float> d9_orm [[texture(19)]]";
    if (v.pbrworld) {
        s += ",\n                         texture2d<float> d9_pbrn [[texture(17)]]";
        if (pbr_env().debug)
            s += ",\n                         device atomic_uint *pwq [[buffer(3)]]";
    }
    s += ") {\n"
         "    float4 r[32] = {};\n"
         "    float4 tc[8] = { vin.t0, vin.t1, vin.t2, vin.t3, vin.t4, vin.t5, vin.t6, vin.t7 };\n"
         "    float4 t[8] = { vin.t0, vin.t1, vin.t2, vin.t3, vin.t4, vin.t5, vin.t6, vin.t7 };\n"
         "    float4 iv[2] = { vin.d0, vin.d1 };\n"
         "    float4 oc[4] = {};\n"
         "    int aL = 0;\n"
         "    float4 odepth = float4(vin.pos.z);\n";
    s += g.body;
    s += p.major >= 2 ? "    float4 col = oc[0];\n" : "    float4 col = saturate(r[0]);\n";
    if (v.smoke)   // run 14 lit smoke: before the alpha test and the fog (the soft fade stays after the fog)
        s += smoke_epilogue(v.smoke, g.stages.count(1) && g.is_depth(1));
    // Remaster run 10: the pool term (PS_PoolsReflect of tools/fx/clean-F1/clean-pools.hlsl), computed before the
    // alpha test (its samples need the whole quad) and added after fog: the pool pass added it unfogged.
    if (v.pool == 2)   // PS_Pools (WORLD): diffuse only
        s += "    float3 pool_add;\n"
             "    {\n"
             "        float4 palb = tx0.sample(sm0, vin.pool_uv.xy + pb[17].xy);\n"
             "        pool_add = d9_pools(vin.pool_p.xyz, d9p_norm(vin.pool_n.xyz), palb.rgb, pb);\n"
             "    }\n";
    else if (v.pool)
        s += "    float3 pool_add;\n"
             "    {\n"
             "        float2 puv = vin.pool_uv.xy + pb[17].xy;\n"
             "        float4 palb = tx0.sample(sm0, puv);\n"
             "        float3 ng = d9p_norm(vin.pool_n.xyz);\n"
             "        float3 nm = tx1.sample(sm1, puv).xyz * 2.0 - 1.0;\n"
             "        float3 tng = vin.pool_t.xyz;\n"
             "        float3 btg = cross(ng, tng) * vin.pool_t.w;\n"
             "        float3 nb = d9p_norm(tng * nm.x + btg * nm.y + ng * nm.z);\n"
             "        float k = saturate(pb[18].w);\n"
             "        float3 n = k > 0.0 ? d9p_norm(ng + k * (nb - ng)) : ng;\n"
             "        pool_add = d9_pools(vin.pool_p.xyz, n, palb.rgb, pb);\n"
             "    }\n";
    s += "    (void)aL;\n";
    if (v.a2c)   // Remaster A3 step A: the test edge, sharpened to about a pixel, becomes the sample coverage
        s += "    float a2c_a = saturate((saturate(col.a) - P.alpha_ref) / max(fwidth(col.a), 1e-4) + 0.5);\n"
             "    uint a2c_n = uint(a2c_a * float(P.pad0) + 0.5);\n"
             "    if (a2c_n == 0u) discard_fragment();\n"
             "    uint a2c_mask = a2c_n >= uint(P.pad0) ? 0xffffffffu : ((1u << a2c_n) - 1u);\n";
    else
        s += "    if (P.alpha_func != 8 &&\n"
             "        !d9_cmp(P.alpha_func, floor(saturate(col.a) * 255.0 + 0.5), floor(P.alpha_ref * "
             "255.0 + 0.5)))\n"
             "        discard_fragment();\n";
    if (v.xfog)   // UG1 run 6 Xbox-style fog (d3d9_msl.h PixelVariant xfog): our formula, the Xbox values
        s += "    {\n"
             "        float fd = clamp((1.0 / vin.pos.w - P.fog_start) * P.fog_end, 0.0, P.fog_color.a);\n"
             "        float f = fd * saturate(1.0 - abs(vin.fog * vin.pos.w) * P.fog_density);\n"
             "        col.rgb = mix(col.rgb, P.fog_color.rgb, f);\n"
             "    }\n";
    else
        s += "    if (" +
             std::string(p.major >= 3 ? "false && " : "") +
             "P.fog_mode != 0) {\n"
             "        float f;\n"
             "        float z = vin.fogz;\n"
             "        if (P.fog_mode == 1) f = vin.fog;\n"
             "        else if (P.fog_mode == 2) f = (P.fog_end - z) / (P.fog_end - P.fog_start);\n"
             "        else if (P.fog_mode == 3) f = exp(-P.fog_density * z);\n"
             "        else f = exp(-(P.fog_density * z) * (P.fog_density * z));\n"
             "        col.rgb = mix(P.fog_color.rgb, col.rgb, saturate(f));\n"
             "    }\n";
    if (v.pool)
        s += "    col.rgb += pool_add;\n";
    // Remaster S3 soft particles: view depth from the D3D projection z = A + B / w with A taken as 1 (far plane much
    // further than the near one); the fragment's own z and w give -B, the copy gives the scene's z. The alpha fades
    // over P.pad1's inverse distance as the scene comes within it behind the particle. Sky (z 1) never fades.
    if (v.alphak)   // run 16 smokeperf: fewer tyre-smoke particles, the same cover (d3d9_msl.h PixelVariant alphak)
        s += "    col.a = 1.0 - pow(saturate(1.0 - col.a), " + std::to_string(v.alphak / 16.0f) + ");\n";
    if (v.soft)
        s += "    {\n"
             "        float zs = d9_soft.read(uint2(vin.pos.xy)).r;\n"
             "        float wf = 1.0 / vin.pos.w;\n"
             "        float nb = wf * (1.0 - vin.pos.z);\n"
             "        if (nb > 0.0 && zs < 1.0) col.a *= saturate((nb / (1.0 - zs) - wf) * P.pad1);\n" +
             // Run 15 tunnel2: plain fog (2) and vent steam (3) also get the lit path's near-camera fade (smoke_light.h
             // kind_defaults near0 / near_range: fog 1 m over 3 m, steam 0.5 m over 1.5 m). 1: unchanged source.
             std::string(v.soft == 2 ? "        col.a *= saturate((wf - 1.0) * (1.0 / 3.0));\n"
                         : v.soft == 3 ? "        col.a *= saturate((wf - 0.5) * (1.0 / 1.5));\n" : "") +
             "    }\n";
    s += "    D9PSOut o;\n"
         "    o.c0 = col;\n";
    for (uint32_t i = 1; i < 4; ++i)
        if (colors >> i & 1)
            s += "    o.c" + std::to_string(i) + " = oc[" + std::to_string(i) + "];\n";
    if (depth)
        s += "    o.depth = odepth.x;\n";
    if (v.a2c)
        s += "    o.mask = a2c_mask;\n";
    s += "    return o;\n"
         "}\n";
    if (v.hlfade && !headlight_patch(&s, v.hlfade)) {   // UG1 run 6: never "counted as faded, drawn plain"
        if (why)
            *why = "hlfade: the headlight samples are not as expected";
        return false;
    }
    if (v.wetroad)
        wetroad_patch(&s);
    if (v.wetroad == 2 && !wetroad_x360_patch(&s)) {   // run 13 Xbox 360 rain splash: never "counted as 360", drawn plain
        if (why)
            *why = "wetroad 360: the wet-road lines are not as expected";
        return false;
    }
    if (v.parallax && !parallax_patch(&s)) {   // run 13 wall parallax: never "counted as parallax, drawn plain"
        if (why)
            *why = "parallax: the normal-map samples are not as expected";
        return false;
    }
    if (v.carpaint && !carpaint_patch(&s)) {   // run 13 Xbox 360 car paint: never "counted as 360 paint, drawn plain"
        if (why)
            *why = "carpaint: the car highlight / cube lines are not as expected";
        return false;
    }
    if (v.glass && !glass_patch_mode(&s, v.glass)) {   // run 13: never "counted as glass, drawn plain" (run 16: + ps_1_x)
        if (why)
            *why = "glass: the reflection add is not as expected";
        return false;
    }
    if (v.sky && !sky_ps_patch(&s)) {   // run 13 Xbox 360 sky (ours): never "counted as sky, drawn plain"
        if (why)
            *why = "sky: the sky combine is not as expected";
        return false;
    }
    // run 17 360 lens drops (ours): never "counted as 360, drawn plain"; raindrop 2 = TEST the forced failure
    if (v.raindrop && (v.raindrop == 2 || !raindrop_patch(&s))) {
        if (why)
            *why = "raindrop: the PC drop lines are not as expected";
        return false;
    }
    if (v.roadsheen && (v.roadsheen > 3 || !roadsheen_patch(&s, v.roadsheen))) {   // never "counted as sheen, drawn plain"
        if (why)
            *why = "roadsheen: the road highlight lines are not as expected";
        return false;
    }
    if (v.water && !water_patch(&s, v.water)) {   // run 13 natural water: never "counted as water, drawn plain"
        if (why)
            *why = "water: the WORLD lit, in-scatter and output lines are not as expected";
        return false;
    }
    if (v.fefloor && !fefloor_ps_patch(&s, v.fefloor)) {   // run 13 front-end floor shadows: never "shadowed", drawn plain
        if (why)
            *why = "fefloor: the colour lines are not as expected or t7 is read";
        return false;
    }
    if (v.pbrworld && !pbrworld_patch(&s, why))   // never "counted as pbr, drawn plain"
        return false;
    if (v.facadeglint && !facadeglint_patch(&s, why))   // R3 run 29: never "counted as glint, drawn plain"
        return false;
    if (v.pool) {   // Remaster run 10: the pool varyings and the pool light (clean-pools.hlsl PoolLight / PoolsShade)
        pool_varyings(&s);
        const size_t f = s.find("fragment D9PSOut ps_main(");
        if (f != std::string::npos)
            s.insert(f, "static float3 d9p_norm(float3 v) { return v * rsqrt(max(dot(v, v), 1.0e-8)); }\n"
                        "static float3 d9p_light(float4 lp, float4 lc, float3 p, float3 n, float index, float count) {\n"
                        "    float3 v = lp.xyz - p;\n"
                        "    float d2 = max(dot(v, v), 0.0001);\n"
                        "    float r = max(lp.w, 0.01);\n"
                        "    float x = d2 / (r * r);\n"
                        "    float win = saturate(1.0 - x * x);\n"
                        "    win *= win;\n"
                        "    float d0 = (lc.w > 0.0) ? lc.w : 0.25 * r;\n"
                        "    float d02 = d0 * d0;\n"
                        "    float soft = d02 / (d2 + d02);\n"
                        "    float ndl = dot(n, v) * rsqrt(d2);\n"
                        "    float diffuse = saturate((ndl + 0.15) / (1.0 + 0.15));\n"
                        "    float active = (count > index + 0.5) ? 1.0 : 0.0;\n"
                        "    return max(lc.rgb, 0.0) * (win * soft * diffuse * active);\n"
                        "}\n"
                        "static float3 d9_pools(float3 p, float3 n, float3 albedo, constant float4 *pb) {\n"
                        "    float3 sum = float3(0.0);\n"
                        "    for (int i = 0; i < 8; ++i)\n"
                        "        sum += d9p_light(pb[2 * i], pb[2 * i + 1], p, n, float(i), pb[16].x);\n"
                        "    float3 e = pb[19].xyz - p;\n"
                        "    float e2 = max(dot(e, e), 0.000001);\n"
                        "    float dist = e2 * rsqrt(e2);\n"
                        "    float t = saturate((dist - pb[18].x) / max(pb[18].y - pb[18].x, 0.01));\n"
                        "    float fade = 1.0 - t * t * (3.0 - 2.0 * t);\n"
                        "    return albedo * sum * (fade * max(pb[18].z, 0.0));\n"
                        "}\n");
    }
    if (v.hdr && !hdr_epilogue(&s, v.hdr)) {   // item 10: never "drawn to the float frame unclamped"
        if (why)
            *why = "hdr: the output lines are not as expected";
        return false;
    }
    if ((v.surf || v.reactive) && !remaster_surf::surf_ps_patch(&s, v.surf, v.reactive)) {   // Run 15 Wave B WB1b
        if (why)
            *why = "surf: the output struct / signature / return are not as expected";
        return false;
    }
    if (v.mipbias && !mipbias_patch(&s, v.mipbias)) {   // R3 run 22 (TEST): last, after every text-matching patch
        if (why)
            *why = "mipbias: a texture lookup's parentheses do not close";
        return false;
    }
    if (v.fade)   // R3 run 29 O1: the scenery fade-in's discard, on the fading draws only (text as run 27)
        fade_patch(&s);
    *out = s;
    return true;
}

// Run 15 Wave B WB1b: a varying slot for the surface normal (7 first: the fewest programs use it).
int surf_slot(const Program &vs, const Program &ps, const VertexVariant &vv, const PixelVariant &pv) {
    if (vv.fefloor)
        return -1;
    std::string vsrc, psrc, why;
    if (!vertex_source(vs, VertexVariant{}, &vsrc, &why) || !pixel_source(ps, PixelVariant{}, &psrc, &why))
        return -1;
    uint32_t used = 0;
    if (vv.pbr || pv.pbrworld || pv.water)
        used |= 0x70;
    if (vv.glass || pv.glass)
        used |= vv.glass == 2 || pv.glass == 2 ? 0xe0 : 0x60;
    if (vv.carenv == 2 || pv.carpaint)
        used |= 0x40;
    if (vv.smoke || pv.smoke)
        used |= 0x80;
    for (int k = 7; k >= 4; --k) {
        const std::string o = "ot[" + std::to_string(k) + "]", t = "t[" + std::to_string(k) + "]";
        if (used >> k & 1)
            continue;
        if (vsrc.find(o + " =") != std::string::npos || vsrc.find(o + ".") != std::string::npos)
            continue;
        if (psrc.find(t) != std::string::npos || psrc.find("tc[" + std::to_string(k) + "]") != std::string::npos ||
            psrc.find("vin.t" + std::to_string(k) + ".") != std::string::npos)
            continue;
        return k;
    }
    return -1;
}

bool pixel_is_wetroad(const Program &p) {
    std::string src, why;
    if (!p.pixel || !pixel_source(p, PixelVariant{}, &src, &why))
        return false;
    return src.find(kWetR) != std::string::npos && src.find(kWetM) != std::string::npos &&
           src.find(kWetA) != std::string::npos;
}

bool pixel_is_glass(const Program &p) {
    std::string src, why;
    if (!p.pixel || p.major < 2 || !pixel_source(p, PixelVariant{}, &src, &why))
        return false;
    GlassAnchors a;
    return glass_find(src, &a);
}
bool pixel_is_glass1(const Program &p) {   // Remaster run 16: the ps_1_x building-glass program (glass1_patch)
    std::string src, why;
    if (!p.pixel || p.major != 1 || !pixel_source(p, PixelVariant{}, &src, &why))
        return false;
    Glass1Anchors a;
    return glass1_find(src, &a);
}

bool pixel_is_roadsheen(const Program &p) {
    std::string src, why;
    if (!p.pixel || !pixel_source(p, PixelVariant{}, &src, &why))
        return false;
    return src.find(kWetR) != std::string::npos && src.find(kWetA) != std::string::npos && roadsheen_lines(src);
}

bool pixel_is_water(const Program &p) {   // Remaster run 13 natural water
    std::string src, why;
    std::string call;
    size_t at;
    if (!p.pixel || !pixel_source(p, PixelVariant{}, &src, &why))
        return false;
    return water_find(src, &at, &call);
}

bool pixel_is_pbrworld(const Program &p) {
    std::string src, why;
    if (!p.pixel || p.major < 2 || !pixel_source(p, PixelVariant{}, &src, &why))
        return false;
    const size_t d = src.find(kPbrD), c = src.find(kPbrC);
    if (d == std::string::npos || c == std::string::npos || d > c || src.find(kPbrD, d + 1) != std::string::npos ||
        src.find(kPbrC, c + 1) != std::string::npos)
        return false;
    if (pbr_find_b(src, c, nullptr, nullptr) == std::string::npos)
        return false;
    const size_t body = src.find("    float4 odepth = ");   // the end of the prelude (which copies vin.t4..t7)
    // The shadow lookup is stage 1 (a depth texture at draw time: the host checks the variant's depth mask).
    if (body == std::string::npos || src.find("tx1.sample(sm1, ", body) == std::string::npos ||
        src.find("tx0.sample(sm0, ((t[0].xyzw)).xy)", body) == std::string::npos)
        return false;
    for (const char *t : {"t[4]", "t[5]", "t[6]", "tc[4]", "tc[5]", "tc[6]"})
        if (src.find(t, body) != std::string::npos)
            return false;
    return true;
}

namespace {
// The inputs, constants and normal dot products an output component set depends on (backward slice over the
// instruction list, component-wise for per-component ops, whole-swizzle otherwise). Flow control, relative addressing
// or an unknown source register makes it unusable (bad).
struct VsSlice {
    std::map<uint32_t, uint32_t> inputs;   // input register -> components
    std::set<uint32_t> consts;
    bool normal_dot = false;               // a dp3/dp4 of a constant with the NORMAL input (or a temp derived from it)
    bool bad = false;
    int raw_dots = 0;                      // dp3 of c[light] with the raw NORMAL input (no modifier, xyz)
    uint32_t raw_normal = 0, raw_light = 0;
};
uint32_t usage_of(const Program &p, uint32_t reg) {
    for (const DclIn &d : p.inputs)
        if (d.index == reg && d.type == R_INPUT)
            return d.usage;
    return ~0u;
}
VsSlice vs_slice(const Program &p, uint32_t type, uint32_t index, uint32_t mask) {
    VsSlice sl;
    std::map<uint64_t, uint32_t> live;   // (type << 32 | index) -> components still needed
    std::map<uint64_t, bool> from_normal;
    live[(uint64_t)type << 32 | index] = mask;
    for (size_t i = p.code.size(); i-- > 0;) {
        const Inst &in = p.code[i];
        switch (in.op) {
        case OP_LOOP: case OP_ENDLOOP: case OP_REP: case OP_ENDREP: case OP_IF: case OP_IFC: case OP_ELSE:
        case OP_ENDIF: case OP_BREAK: case OP_BREAKC:
            sl.bad = true;
            return sl;
        case OP_DCL: case OP_DEF: case OP_DEFI: case OP_DEFB: case OP_COMMENT: case OP_END: case OP_NOP:
            continue;
        default:
            break;
        }
        if (!in.has_dst)
            continue;
        auto it = live.find((uint64_t)in.dst.type << 32 | in.dst.index);
        if (it == live.end())
            continue;
        const uint32_t hit = it->second & in.dst.mask;
        if (!hit)
            continue;
        it->second &= ~hit;
        if (!it->second)
            live.erase(it);
        const bool percomp = in.op == OP_MOV || in.op == OP_ADD || in.op == OP_SUB || in.op == OP_MUL ||
                             in.op == OP_MAD || in.op == OP_MIN || in.op == OP_MAX || in.op == OP_SLT ||
                             in.op == OP_SGE || in.op == OP_FRC || in.op == OP_ABS || in.op == OP_CMP ||
                             in.op == OP_LRP;
        bool has_const = false, has_normal = false;
        for (uint32_t k = 0; k < in.nsrc && k < 4; ++k) {
            const Src &src = in.src[k];
            uint32_t need = 0;
            if (in.op == OP_DP3)
                for (int ch = 0; ch < 3; ++ch)
                    need |= 1u << ((src.swz >> (2 * ch)) & 3);
            else if (percomp) {
                for (int ch = 0; ch < 4; ++ch)
                    if (hit >> ch & 1)
                        need |= 1u << ((src.swz >> (2 * ch)) & 3);
            } else
                for (int ch = 0; ch < 4; ++ch)
                    need |= 1u << ((src.swz >> (2 * ch)) & 3);
            if (src.rel) {
                sl.bad = true;
                return sl;
            }
            if (src.type == R_CONST) {
                sl.consts.insert(src.index);
                has_const = true;
            } else if (src.type == R_INPUT) {
                sl.inputs[src.index] |= need;
                if (usage_of(p, src.index) == 3)   // D3DDECLUSAGE_NORMAL
                    has_normal = true;
            } else if (src.type == R_TEMP) {
                live[(uint64_t)R_TEMP << 32 | src.index] |= need;
            } else {
                sl.bad = true;
                return sl;
            }
        }
        if ((in.op == OP_DP3 || in.op == OP_DP4) && has_const && has_normal)
            sl.normal_dot = true;
        if (in.op == OP_DP3 && in.nsrc == 2) {
            for (int a = 0; a < 2; ++a) {
                const Src &cs = in.src[a], &ns = in.src[1 - a];
                if (cs.type == R_CONST && !cs.mod && ns.type == R_INPUT && !ns.mod && (ns.swz & 0x3f) == 0x24 &&
                    (cs.swz & 0x3f) == 0x24 && usage_of(p, ns.index) == 3) {
                    ++sl.raw_dots;
                    sl.raw_normal = ns.index;
                    sl.raw_light = cs.index;
                }
            }
        }
    }
    return sl;
}
bool slice_reads_usage(const Program &p, const VsSlice &sl, uint32_t usage) {
    for (const auto &kv : sl.inputs)
        if (usage_of(p, kv.first) == usage)
            return true;
    return false;
}
} // namespace

SunFlow vertex_sun_flow(const Program &p) {
    SunFlow f;
    if (!p.ok || p.pixel || p.major >= 3) {
        f.why = p.major >= 3 ? "vs 3.0 (outputs by usage; not handled)" : "not a vertex shader";
        return f;
    }
    const VsSlice d1 = vs_slice(p, R_ATTROUT, 1, 7), d0 = vs_slice(p, R_ATTROUT, 0, 7);
    if (d1.bad || d0.bad) {
        f.why = "flow control or relative addressing";
        return f;
    }
    for (uint32_t c : d1.consts)
        f.consts.push_back(c);
    if (!d1.normal_dot)
        f.why = "d1 has no constant . NORMAL";
    else if (slice_reads_usage(p, d1, 10))
        f.why = "d1 reads COLOR (swapped: d1 is the radiosity)";
    else if (!slice_reads_usage(p, d0, 10))
        f.why = "d0 does not read COLOR";
    else
        f.ok = true;
    // Run 12 pilot: the registers the pbr vertex variant writes from.
    std::set<std::pair<uint32_t, uint32_t>> eye_terms;   // (POSITION input, constant)
    bool writes_t456 = false;
    for (const Inst &in : p.code) {
        if (in.has_dst && in.dst.type == R_TEXCRDOUT && in.dst.index >= 4 && in.dst.index <= 6)
            writes_t456 = true;
        if (in.op == OP_ADD && in.nsrc == 2)
            for (int a = 0; a < 2; ++a) {
                const Src &ps = in.src[a], &cs = in.src[1 - a];
                if (ps.type == R_INPUT && ps.mod == 1 && !ps.rel && usage_of(p, ps.index) == 0 && cs.type == R_CONST &&
                    !cs.mod && !cs.rel)
                    eye_terms.insert({ps.index, cs.index});
            }
    }
    if (d1.raw_dots != 1)
        f.pbr_why = "d1 does not dot a constant with the raw NORMAL input exactly once";
    else if (eye_terms.size() != 1)
        f.pbr_why = eye_terms.empty() ? "no eye term (-POSITION + constant)" : "more than one eye term (-POSITION + constant)";
    else if (writes_t456)
        f.pbr_why = "the shader writes t4..t6";
    else {
        f.position = eye_terms.begin()->first;
        f.eye = eye_terms.begin()->second;
        f.normal = d1.raw_normal;
        f.light = d1.raw_light;
        f.pbr_ok = true;
    }
    return f;
}

// Xbox 360 glass mode: the sun constant of a building-glass vertex shader, found by structure: the one c[K] that is
// dotted (DP3) with the NORMAL input and subtracted from a multiple of it (MAD x, N, -c[K]: the reflection vector
// 2 (N.L) N - L). -1 when absent, not unique, or t7 is already written.
int glass_vs_light(const Program &p, uint32_t normal) {
    std::set<uint32_t> dotted, reflected;
    for (const Inst &in : p.code) {
        if (in.has_dst && in.dst.type == R_TEXCRDOUT && in.dst.index == 7)
            return -1;
        if (in.op == OP_DP3 && in.nsrc == 2)
            for (int a = 0; a < 2; ++a)
                if (in.src[a].type == R_INPUT && in.src[a].index == normal && !in.src[a].rel &&
                    in.src[1 - a].type == R_CONST && !in.src[1 - a].rel && !in.src[1 - a].mod)
                    dotted.insert(in.src[1 - a].index);
        if (in.op == OP_MAD && in.nsrc == 3 && in.src[2].type == R_CONST && in.src[2].mod == 1 && !in.src[2].rel)
            for (int a = 0; a < 2; ++a)
                if (in.src[a].type == R_INPUT && in.src[a].index == normal && !in.src[a].rel)
                    reflected.insert(in.src[2].index);
    }
    int k = -1;
    for (uint32_t c : reflected)
        if (dotted.count(c)) {
            if (k >= 0)
                return -1;
            k = int(c);
        }
    return k;
}
bool car_vs_flow(const Program &p, uint32_t *position, uint32_t *eye) {
    if (!p.ok || p.pixel)
        return false;
    std::set<std::pair<uint32_t, uint32_t>> eye_terms;   // (POSITION input, constant), as glass_vs_flow
    for (const Inst &in : p.code) {
        if (in.has_dst && in.dst.type == R_TEXCRDOUT && in.dst.index == 6)
            return false;
        if (in.op == OP_ADD && in.nsrc == 2)
            for (int a = 0; a < 2; ++a) {
                const Src &ps = in.src[a], &cs = in.src[1 - a];
                if (ps.type == R_INPUT && ps.mod == 1 && !ps.rel && usage_of(p, ps.index) == 0 && cs.type == R_CONST &&
                    !cs.mod && !cs.rel)
                    eye_terms.insert({ps.index, cs.index});
            }
    }
    if (eye_terms.size() != 1)
        return false;
    *position = eye_terms.begin()->first;
    *eye = eye_terms.begin()->second;
    return true;
}
GlassFlow glass_vs_flow(const Program &p) {
    GlassFlow g;
    if (!p.ok || p.pixel)
        return g;
    std::set<std::pair<uint32_t, uint32_t>> eye_terms;   // (POSITION input, constant), as vertex_sun_flow
    std::set<uint32_t> normals;
    bool writes_t56 = false;
    for (const Inst &in : p.code) {
        if (in.has_dst && in.dst.type == R_TEXCRDOUT && (in.dst.index == 5 || in.dst.index == 6))
            writes_t56 = true;
        for (int a = 0; a < (int)in.nsrc; ++a)
            if (in.src[a].type == R_INPUT && !in.src[a].rel && usage_of(p, in.src[a].index) == 3)
                normals.insert(in.src[a].index);
        if (in.op == OP_ADD && in.nsrc == 2)
            for (int a = 0; a < 2; ++a) {
                const Src &ps = in.src[a], &cs = in.src[1 - a];
                if (ps.type == R_INPUT && ps.mod == 1 && !ps.rel && usage_of(p, ps.index) == 0 && cs.type == R_CONST &&
                    !cs.mod && !cs.rel)
                    eye_terms.insert({ps.index, cs.index});
            }
    }
    if (writes_t56 || eye_terms.size() != 1 || normals.size() != 1)
        return g;
    g.position = eye_terms.begin()->first;
    g.eye = eye_terms.begin()->second;
    g.normal = *normals.begin();
    g.ok = true;
    g.light = glass_vs_light(p, g.normal);
    return g;
}

uint32_t pixel_sampler_mask(const Program &p) {
    uint32_t mask = 0;
    for (const Inst &in : p.code)
        if (in.op == OP_TEXREG2AR || in.op == OP_TEXREG2GB) {
            mask |= 1u << (in.dst.index & 7);
        } else if (in.op == OP_TEX || in.op == OP_TEXLDL) {
            if (p.major >= 2)
                mask |= 1u << (in.src[1].index & 15);
            else
                mask |= 1u << (in.dst.index & 7);
        }
    return mask;
}

uint32_t pixel_color_outputs(const Program &p) {
    if (p.major < 2)
        return 1;
    uint32_t mask = 1;
    for (const Inst &in : p.code)
        if (in.has_dst && in.dst.type == R_COLOROUT)
            mask |= 1u << (in.dst.index & 3);
    return mask;
}

} // namespace d9msl

// ---- GLSL -------------------------------------------------------------------

namespace d9glsl {

uint32_t constant_registers(const d9sh::Program &p) {
    if (p.relative)
        return kConstantRegisters;
    return p.max_const ? (p.max_const < kConstantRegisters ? p.max_const : kConstantRegisters) : 1;
}

using d9msl::Gen;
using d9msl::Lang;

namespace {
// The interpolants every vertex shader writes and every pixel shader reads.
const char *kVaryingsOut = "layout(location = 0) out vec4 o_d0;\n"
                           "layout(location = 1) out vec4 o_d1;\n"
                           "layout(location = 2) out vec4 o_t[8];\n"
                           "layout(location = 10) out float o_fog;\n"
                           "layout(location = 11) out float o_fogz;\n";
const char *kVaryingsIn = "layout(location = 0) in vec4 i_d0;\n"
                          "layout(location = 1) in vec4 i_d1;\n"
                          "layout(location = 2) in vec4 i_t[8];\n"
                          "layout(location = 10) in float i_fog;\n"
                          "layout(location = 11) in float i_fogz;\n";
const char *kZeroes = "    for (int i_ = 0; i_ < 32; ++i_) r[i_] = vec4(0.0);\n";
} // namespace

bool vertex_source(const d9sh::Program &p, std::string *out, std::string *why) {
    if (!p.ok || p.pixel) {
        if (why)
            *why = p.ok ? "not a vertex shader" : p.why;
        return false;
    }
    Gen g(p, Lang::GLSL);
    if (!d9msl::generate(g, why))
        return false;
    std::string s = d9msl::kGlslPrelude;
    s += "layout(std140, set = 0, binding = 0) uniform VSC { vec4 c[" +
         std::to_string(g.const_limit) +
         "]; };\n"
         "layout(std140, set = 0, binding = 1) uniform VSP { vec4 halfpix; vec4 ascale[16]; ivec4 "
         "bgra; ivec4 bconst[4]; } P;\n";
    const std::vector<uint32_t> order = d9msl::input_registers(p);
    const std::set<uint32_t> seen(order.begin(), order.end());
    for (uint32_t i : seen)
        s += "layout(location = " + std::to_string(i) + ") in vec4 v" + std::to_string(i) + ";\n";
    s += kVaryingsOut;
    s += "void main() {\n"
         "    vec4 r[32];\n";
    s += kZeroes;
    s += "    vec4 a0 = vec4(0.0);\n"
         "    int aL = 0;\n"
         "    vec4 opos = vec4(0.0), ofog = vec4(1.0), opsize = vec4(1.0);\n"
         "    vec4 od[2] = vec4[2](vec4(1.0), vec4(0.0));\n"
         "    vec4 ot[8];\n"
         "    for (int i_ = 0; i_ < 8; ++i_) ot[i_] = vec4(0.0);\n"
         "    vec4 vv[16];\n"
         "    for (int i_ = 0; i_ < 16; ++i_) vv[i_] = vec4(0.0);\n";
    for (uint32_t i : seen) {
        const std::string n = std::to_string(i);
        s += "    vv[" + n + "] = (((P.bgra.x >> " + n + ") & 1) != 0 ? v" + n + ".bgra : v" + n +
             ") * P.ascale[" + n + "];\n";
    }
    s += g.body;
    s += "    gl_Position = opos;\n"
         "    gl_Position.xy += P.halfpix.xy * opos.w;\n"
         "    gl_PointSize = 1.0;\n"
         "    o_d0 = " +
         std::string(p.major >= 3 ? "od[0]" : "clamp(od[0], 0.0, 1.0)") +
         ";\n"
         "    o_d1 = " +
         std::string(p.major >= 3 ? "od[1]" : "clamp(od[1], 0.0, 1.0)") +
         ";\n"
         "    o_t = ot;\n"
         "    o_fog = ofog.x;\n"
         "    o_fogz = opos.z;\n"
         "}\n";
    *out = s;
    return true;
}

bool pixel_source(const d9sh::Program &p, const d9msl::PixelVariant &v, std::string *out,
                  std::string *why) {
    if (!p.ok || !p.pixel) {
        if (why)
            *why = p.ok ? "not a pixel shader" : p.why;
        return false;
    }
    Gen g(p, Lang::GLSL);
    g.variant = v;
    if (!d9msl::generate(g, why))
        return false;
    std::string s = d9msl::kGlslPrelude;
    s += "layout(std140, set = 0, binding = 2) uniform PSC { vec4 c[" +
         std::to_string(g.const_limit) +
         "]; };\n"
         "layout(std140, set = 0, binding = 3) uniform PSP {\n"
         "    float alpha_ref; int alpha_func; int fog_mode; int pad0;\n"
         "    vec4 fog_color;\n"
         "    float fog_start; float fog_end; float fog_density; float pad1;\n"
         "    ivec4 bconst[4];\n"
         "} P;\n";
    bool shadow = false;
    for (uint32_t st : g.stages) {
        const char *type = g.kind_of(st) == d9sh::S_CUBE     ? "samplerCube"
                           : g.kind_of(st) == d9sh::S_VOLUME ? "sampler3D"
                           : g.is_depth(st)                  ? "sampler2DShadow"
                                                             : "sampler2D";
        shadow |= g.is_depth(st);
        s += "layout(set = 0, binding = " + std::to_string(kSamplerBinding + st) + ") uniform " +
             type + " tx" + std::to_string(st) + ";\n";
    }
    if (shadow)
        s += "vec4 d9_shadow(sampler2DShadow t, vec4 c) {\n"
             "    float r = textureLod(t, vec3(c.xy, clamp(c.z, 0.0, 1.0)), 0.0);\n"
             "    return vec4(r, r, r, 1.0);\n"
             "}\n";
    s += kVaryingsIn;
    const bool depth = g.writes_depth;
    const uint32_t colors = p.major >= 2 ? (g.color_outputs | 1) : 1;
    for (uint32_t i = 0; i < 4; ++i)
        if (colors >> i & 1)
            s += "layout(location = " + std::to_string(i) + ") out vec4 o_c" + std::to_string(i) +
                 ";\n";
    s += "void main() {\n"
         "    vec4 r[32];\n";
    s += kZeroes;
    s += "    vec4 tc[8] = i_t;\n"
         "    vec4 t[8] = i_t;\n"
         "    vec4 iv[2] = vec4[2](i_d0, i_d1);\n"
         "    vec4 oc[4] = vec4[4](vec4(0.0), vec4(0.0), vec4(0.0), vec4(0.0));\n"
         "    int aL = 0;\n"
         "    vec4 odepth = vec4(gl_FragCoord.z);\n";
    s += g.body;
    s += p.major >= 2 ? "    vec4 col = oc[0];\n" : "    vec4 col = clamp(r[0], 0.0, 1.0);\n";
    s += "    if (P.alpha_func != 8 &&\n"
         "        !d9_cmp(P.alpha_func, floor(clamp(col.a, 0.0, 1.0) * 255.0 + 0.5), "
         "floor(P.alpha_ref * 255.0 + 0.5)))\n"
         "        discard;\n"
         "    if (" +
         std::string(p.major >= 3 ? "false && " : "") +
         "P.fog_mode != 0) {\n"
         "        float f;\n"
         "        float z = i_fogz;\n"
         "        if (P.fog_mode == 1) f = i_fog;\n"
         "        else if (P.fog_mode == 2) f = (P.fog_end - z) / (P.fog_end - P.fog_start);\n"
         "        else if (P.fog_mode == 3) f = exp(-P.fog_density * z);\n"
         "        else f = exp(-(P.fog_density * z) * (P.fog_density * z));\n"
         "        col.rgb = mix(P.fog_color.rgb, col.rgb, clamp(f, 0.0, 1.0));\n"
         "    }\n"
         "    o_c0 = col;\n";
    for (uint32_t i = 1; i < 4; ++i)
        if (colors >> i & 1)
            s += "    o_c" + std::to_string(i) + " = oc[" + std::to_string(i) + "];\n";
    if (depth)
        s += "    gl_FragDepth = odepth.x;\n";
    s += "}\n";
    *out = s;
    return true;
}

} // namespace d9glsl

// ---- WGSL -------------------------------------------------------------------

namespace d9wgsl {

using d9msl::Gen;
using d9msl::Lang;

bool vertex_source(const d9sh::Program &p, std::string *out, std::string *why) {
    if (!p.ok || p.pixel) {
        if (why)
            *why = p.ok ? "not a vertex shader" : p.why;
        return false;
    }
    Gen g(p, Lang::WGSL);
    if (!d9msl::generate(g, why))
        return false;
    const std::string n = std::to_string(g.const_limit);
    std::string s = d9msl::kWgslPrelude;
    s += "struct VC { c: array<vec4f, " + n +
         "> };\n"
         "@group(0) @binding(0) var<uniform> VCB: VC;\n"
         "@group(0) @binding(1) var<uniform> P: VSP;\n";
    const std::vector<uint32_t> order = d9msl::input_registers(p);
    const std::set<uint32_t> seen(order.begin(), order.end());
    s += "struct VIn {\n";
    for (uint32_t i : seen)
        s += "    @location(" + std::to_string(i) + ") v" + std::to_string(i) + ": vec4f,\n";
    if (seen.empty())
        s += "    @builtin(vertex_index) vid: u32,\n";
    s += "};\n"
         "@vertex fn vs_main(vin: VIn) -> V2F {\n"
         "    let c = &VCB.c;\n"
         "    _ = c[0];\n"
         "    var r: array<vec4f, 32>;\n"
         "    var a0 = vec4f(0.0);\n"
         "    var aL: i32 = 0;\n"
         "    var opos = vec4f(0.0);\n"
         "    var ofog = vec4f(1.0);\n"
         "    var opsize = vec4f(1.0);\n"
         "    var od = array<vec4f, 2>(vec4f(1.0), vec4f(0.0));\n"
         "    var ot: array<vec4f, 8>;\n"
         "    var vv: array<vec4f, 16>;\n";
    for (uint32_t i : seen) {
        const std::string k = std::to_string(i);
        s += "    vv[" + k + "] = vin.v" + k + " * P.ascale[" + k + "];\n";
    }
    s += g.body;
    s += "    var o: V2F;\n"
         "    o.pos = vec4f(opos.xy + P.halfpix.xy * opos.w, opos.zw);\n"
         "    o.d0 = " +
         std::string(p.major >= 3 ? "od[0]" : "saturate(od[0])") +
         ";\n"
         "    o.d1 = " +
         std::string(p.major >= 3 ? "od[1]" : "saturate(od[1])") +
         ";\n"
         "    o.t0 = ot[0]; o.t1 = ot[1]; o.t2 = ot[2]; o.t3 = ot[3];\n"
         "    o.t4 = ot[4]; o.t5 = ot[5]; o.t6 = ot[6]; o.t7 = ot[7];\n"
         "    o.fog = ofog.x;\n"
         "    o.fogz = opos.z;\n"
         "    return o;\n"
         "}\n";
    *out = s;
    return true;
}

bool pixel_source(const d9sh::Program &p, const d9msl::PixelVariant &v, std::string *out,
                  std::string *why) {
    if (!p.ok || !p.pixel) {
        if (why)
            *why = p.ok ? "not a pixel shader" : p.why;
        return false;
    }
    Gen g(p, Lang::WGSL);
    g.variant = v;
    if (!d9msl::generate(g, why))
        return false;
    const std::string n = std::to_string(g.const_limit);
    std::string s = d9msl::kWgslPrelude;
    s += "struct PC { c: array<vec4f, " + n +
         "> };\n"
         "@group(0) @binding(2) var<uniform> PCB: PC;\n"
         "@group(0) @binding(3) var<uniform> P: PSP;\n";
    bool shadow = false;
    for (uint32_t st : g.stages) {
        const std::string k = std::to_string(st);
        const bool depth = g.is_depth(st);
        shadow |= depth;
        const char *type = g.kind_of(st) == d9sh::S_CUBE     ? "texture_cube<f32>"
                           : g.kind_of(st) == d9sh::S_VOLUME ? "texture_3d<f32>"
                           : depth                           ? "texture_depth_2d"
                                                             : "texture_2d<f32>";
        s += "@group(0) @binding(" + std::to_string(kTextureBinding + st) + ") var tx" + k + ": " +
             type + ";\n";
        s += "@group(0) @binding(" + std::to_string(kSamplerBinding + st) + ") var sm" + k + ": " +
             (depth ? "sampler_comparison" : "sampler") + ";\n";
    }
    if (shadow)
        s += "fn d9_shadow(t: texture_depth_2d, sm: sampler_comparison, c: vec4f) -> vec4f {\n"
             "    let r = textureSampleCompareLevel(t, sm, c.xy, saturate(c.z));\n"
             "    return vec4f(r, r, r, 1.0);\n"
             "}\n";
    const bool depth = g.writes_depth;
    const uint32_t colors = p.major >= 2 ? (g.color_outputs | 1) : 1;
    s += "struct FOut {\n";
    for (uint32_t i = 0; i < 4; ++i)
        if (colors >> i & 1)
            s += "    @location(" + std::to_string(i) + ") c" + std::to_string(i) + ": vec4f,\n";
    if (depth)
        s += "    @builtin(frag_depth) depth: f32,\n";
    s += "};\n"
         "@fragment fn ps_main(vin: V2F) -> FOut {\n"
         "    let c = &PCB.c;\n"
         "    _ = c[0];\n"
         "    var r: array<vec4f, 32>;\n"
         "    var tc = array<vec4f, 8>(vin.t0, vin.t1, vin.t2, vin.t3, vin.t4, vin.t5, vin.t6, "
         "vin.t7);\n"
         "    var t = tc;\n"
         "    var iv = array<vec4f, 2>(vin.d0, vin.d1);\n"
         "    var oc: array<vec4f, 4>;\n"
         "    var aL: i32 = 0;\n"
         "    var odepth = vec4f(vin.pos.z);\n";
    s += g.body;
    s += p.major >= 2 ? "    var col = oc[0];\n" : "    var col = saturate(r[0]);\n";
    s += "    if (P.alpha_func != 8 &&\n"
         "        !d9_cmp(P.alpha_func, floor(saturate(col.a) * 255.0 + 0.5), floor(P.alpha_ref * "
         "255.0 + 0.5))) {\n"
         "        discard;\n"
         "    }\n"
         "    if (" +
         std::string(p.major >= 3 ? "false && " : "") +
         "P.fog_mode != 0) {\n"
         "        var f = 1.0;\n"
         "        let z = vin.fogz;\n"
         "        if (P.fog_mode == 1) { f = vin.fog; }\n"
         "        else if (P.fog_mode == 2) { f = (P.fog_end - z) / (P.fog_end - P.fog_start); }\n"
         "        else if (P.fog_mode == 3) { f = exp(-P.fog_density * z); }\n"
         "        else { f = exp(-(P.fog_density * z) * (P.fog_density * z)); }\n"
         "        col = vec4f(mix(P.fog_color.rgb, col.rgb, saturate(f)), col.a);\n"
         "    }\n"
         "    var o: FOut;\n"
         "    o.c0 = col;\n";
    for (uint32_t i = 1; i < 4; ++i)
        if (colors >> i & 1)
            s += "    o.c" + std::to_string(i) + " = oc[" + std::to_string(i) + "];\n";
    if (depth)
        s += "    o.depth = odepth.x;\n";
    s += "    return o;\n"
         "}\n";
    *out = s;
    return true;
}

} // namespace d9wgsl
