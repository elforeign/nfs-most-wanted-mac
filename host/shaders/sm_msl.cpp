// sm_msl.cpp - D3D9 shader bytecode -> Metal Shading Language.
//
// Generated source layout (see README.md):
//   helpers (D3D9 float semantics), plain register structs SmVSIn/SmVSOut or SmPSIn/SmPSOut,
//   `static void sm_body(...)` = the translated shader on plain structs,
//   `vertex/fragment sm_main` = Metal stage wrapper (attributes, varyings, half-pixel fix-up,
//   texkill/alpha test), optionally `kernel sm_test_kernel` = the body on flat buffers.
#include "sm_ir.h"
#include "sm_translate.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <set>
#include <string>

namespace sm {
namespace {

const char *COMP = "xyzw";

std::string fmt(const char *f, ...) {
    char buf[1024];
    va_list ap;
    va_start(ap, f);
    vsnprintf(buf, sizeof buf, f, ap);
    va_end(ap);
    return buf;
}

std::string flit(uint32_t bits) {
    float f;
    memcpy(&f, &bits, 4);
    if (!std::isfinite(f)) return fmt("as_type<float>(0x%08xu)", bits);
    char b[64];
    snprintf(b, sizeof b, "%.9g", (double)f);
    std::string s = b;
    if (s.find_first_of(".en") == std::string::npos) s += ".0";
    return s + "f";
}

std::string flit(float f) {
    uint32_t u;
    memcpy(&u, &f, 4);
    return flit(u);
}

const char *HELPERS = R"MSL(#include <metal_stdlib>
using namespace metal;
#pragma clang diagnostic ignored "-Wunused-function"
#pragma clang diagnostic ignored "-Wunused-variable"

// ---- D3D9 arithmetic semantics -------------------------------------------------------
#ifndef SM_IEEE_MUL
// legacy D3D9 multiply: 0 * anything (inf, NaN) = 0
static inline float  sm_mulf(float a, float b)   { return (a == 0.0f || b == 0.0f) ? 0.0f : a * b; }
static inline float4 sm_mul(float4 a, float4 b)  { return select(a * b, float4(0.0f), (a == 0.0f) | (b == 0.0f)); }
#else
static inline float  sm_mulf(float a, float b)   { return a * b; }
static inline float4 sm_mul(float4 a, float4 b)  { return a * b; }
#endif
static inline float4 sm_mad(float4 a, float4 b, float4 c) { return sm_mul(a, b) + c; }
static inline float  sm_dp2(float4 a, float4 b) { return sm_mulf(a.x, b.x) + sm_mulf(a.y, b.y); }
static inline float  sm_dp3(float4 a, float4 b) { return sm_mulf(a.x, b.x) + sm_mulf(a.y, b.y) + sm_mulf(a.z, b.z); }
static inline float  sm_dp4(float4 a, float4 b) { return sm_mulf(a.x, b.x) + sm_mulf(a.y, b.y) + sm_mulf(a.z, b.z) + sm_mulf(a.w, b.w); }
static inline float  sm_rcp(float x) { return x == 0.0f ? INFINITY : 1.0f / x; }
static inline float  sm_rsq(float x) { float a = fabs(x); return a == 0.0f ? INFINITY : rsqrt(a); }
static inline float  sm_log(float x) { float a = fabs(x); return a == 0.0f ? -INFINITY : log2(a); }
static inline float  sm_exp(float x) { return exp2(x); }
static inline float  sm_pow(float x, float y) {
    float a = fabs(x);
    if (y == 0.0f) return 1.0f;
    if (a == 0.0f) return y > 0.0f ? 0.0f : INFINITY;
    return exp2(y * log2(a));
}
static inline float4 sm_min(float4 a, float4 b) { return select(b, a, a < b); }
static inline float4 sm_max(float4 a, float4 b) { return select(b, a, a >= b); }
static inline float4 sm_nrm(float4 a) { float r = sm_rsq(sm_dp3(a, a)); return sm_mul(a, float4(r)); }
static inline float4 sm_lit(float4 s) {
    float4 d = float4(1.0f, 0.0f, 0.0f, 1.0f);
    float p = clamp(s.w, -128.0f, 128.0f);
    if (s.x > 0.0f) {
        d.y = s.x;
        if (s.y > 0.0f) d.z = sm_pow(s.y, p);
    }
    return d;
}
// vs_1_x expp / logp (partial-precision forms with the per-component layout)
static inline float4 sm_expp1(float x) { float f = floor(x); return float4(exp2(f), x - f, exp2(x), 1.0f); }
static inline float4 sm_logp1(float x) {
    float a = fabs(x);
    if (a == 0.0f) return float4(-INFINITY, 1.0f, -INFINITY, 1.0f);
    float e = floor(log2(a));
    return float4(e, a / exp2(e), log2(a), 1.0f);
}
static inline bool sm_alpha_test(uint f, float a, float ref) {
    float qa = rint(saturate(a) * 255.0f), qr = rint(saturate(ref) * 255.0f);
    switch (f) {
    case 1: return false;      case 2: return qa < qr;  case 3: return qa == qr;
    case 4: return qa <= qr;   case 5: return qa > qr;  case 6: return qa != qr;
    case 7: return qa >= qr;   default: return true;
    }
}

struct SmVSAux { float4 pos_fixup; float point_size; float pad0, pad1, pad2; };
struct SmPSAux { uint alpha_func; float alpha_ref; uint pad0, pad1; float4 bumpenv[8]; float4 bumplum[8]; float lodbias[16]; };
)MSL";

struct VsOutMap { int slot; int index; };  // vs_3_0 o# -> canonical slot

class Emitter {
public:
    Emitter(const Shader &s, const SmOptions &o, SmResult &r) : sh(s), opt(o), res(r) {}
    bool run(std::string &msl);
    std::string err;

private:
    const Shader &sh;
    const SmOptions &opt;
    SmResult &res;

    bool ps = false, ps1 = false, ps14 = false, vs3 = false, ps3 = false;
    std::map<int, std::array<uint32_t, 4>> fdef;
    std::map<int, std::array<int32_t, 4>> idef;
    std::map<int, bool> bdef;
    std::set<int> temps, tregs;
    bool uses_a0 = false, uses_p0 = false, uses_crel = false, uses_oDepth = false;
    bool uses_vpos = false, uses_vface = false, writes_psize = false;
    int stype[16] = {0};          // SM_TEX_* per sampler/stage, 0 = unused
    bool sshadow[16] = {false};
    int vs_in_mask[16] = {0};     // dcl'd vs inputs: 1 + mask
    std::map<int, VsOutMap> vs3_out;     // o# -> slot
    std::map<int, int> ps3_in;    // v# -> slot
    int ps3_vmax = 0;
    uint32_t vary_read = 0, vary_written = 0;
    int color_out = 0;

    std::string code;
    int depth = 1;
    int tmp_id = 0;
    std::vector<std::string> closers;  // flow-control stack
    int loop_id = 0;

    void line(const std::string &s) { code += std::string(depth * 4, ' ') + s + "\n"; }
    bool fail(const std::string &m) { if (err.empty()) err = m; return false; }

    bool analyse();
    bool emit_all();
    bool emit_instr(size_t idx, std::string &pre, std::string &post);
    std::string src_expr(const Src &s, bool apply_mods = true);
    std::string src_base(const Src &s);
    std::string rel_index(uint32_t rel_type, uint32_t rel_num, uint8_t comp, int offset);
    std::string scalar_src(const Src &s, const char *comp = "w") { return "(" + src_expr(s) + ")." + comp; }
    bool write_dst(const Instr &in, const std::string &val, std::string &out, bool is_bool = false);
    std::string sample(int stage, const std::string &coord, int mode, const std::string &extra = "",
                       const std::string &extra2 = "");
    std::string tex_coord_in(int n) { mark_vary(SM_VARY_TEXCOORD0 + n); return fmt("si.tex[%d]", n); }
    void mark_vary(int slot) { vary_read |= 1u << slot; }
    int stage_type(int s);
    std::string int_src(const Src &s);
    std::string bool_src(const Src &s);
    std::string cmp_op(uint32_t c);
    std::string tex_param_decls(bool attrs);
    std::string tex_param_args();
};

int Emitter::stage_type(int s) {
    if (s < 0 || s > 15) return SM_TEX_2D;
    if (opt.sampler_type[s]) return opt.sampler_type[s];
    if (stype[s]) return stype[s];
    for (const CtabEntry &e : sh.ctab)
        if (e.set == 3 && s >= (int)e.reg && s < (int)(e.reg + e.count)) {
            if (e.type == 14) return SM_TEX_CUBE;
            if (e.type == 13) return SM_TEX_3D;
            return SM_TEX_2D;
        }
    return SM_TEX_2D;
}

bool Emitter::analyse() {
    ps = sh.ps;
    ps1 = ps && sh.major == 1;
    ps14 = ps1 && sh.minor >= 4;
    vs3 = !ps && sh.major >= 3;
    ps3 = ps && sh.major >= 3;
    auto use_stage = [&](int s) {
        if (s < 0 || s > 15) return;
        if (!stype[s]) stype[s] = -1;   // resolved below
    };
    for (const Instr &in : sh.code) {
        if (in.op == OP_DEF) { std::array<uint32_t, 4> v; for (int k = 0; k < 4; k++) v[k] = in.defv[k]; fdef[const_index(in.dst.type, in.dst.num)] = v; continue; }
        if (in.op == OP_DEFI) { std::array<int32_t, 4> v; for (int k = 0; k < 4; k++) v[k] = (int32_t)in.defv[k]; idef[in.dst.num] = v; continue; }
        if (in.op == OP_DEFB) { bdef[in.dst.num] = in.defv[0] != 0; continue; }
        if (in.op == OP_DCL) {
            const Dst &d = in.dst;
            if (d.type == R_SAMPLER) {
                int t = in.tex_type == 3 ? SM_TEX_CUBE : in.tex_type == 4 ? SM_TEX_3D : SM_TEX_2D;
                if (d.num < 16) stype[d.num] = t;
            } else if (!ps && d.type == R_INPUT) {
                if (d.num >= 16) return fail("vs input register out of range");
                vs_in_mask[d.num] = 1 + d.mask;
                SmInput &si = res.inputs[res.n_inputs++];
                si.reg = d.num; si.usage = in.usage; si.usage_index = in.usage_index; si.mask = d.mask;
            } else if (vs3 && d.type == R_TEXCRDOUT) {
                int slot;
                if (in.usage == SM_USAGE_POSITION && in.usage_index == 0) slot = SM_VARY_POSITION;
                else if (in.usage == SM_USAGE_COLOR && in.usage_index < 2) slot = SM_VARY_COLOR0 + in.usage_index;
                else if (in.usage == SM_USAGE_TEXCOORD && in.usage_index < 8) slot = SM_VARY_TEXCOORD0 + in.usage_index;
                else if (in.usage == SM_USAGE_FOG) slot = SM_VARY_FOG;
                else if (in.usage == SM_USAGE_PSIZE) slot = SM_VARY_PSIZE;
                else return fail(fmt("vs_3_0 output usage %u%u not supported", in.usage, in.usage_index));
                if (d.num >= 12) return fail("vs_3_0 output register out of range");
                vs3_out[d.num] = {slot, (int)in.usage_index};
            } else if (ps3 && d.type == R_INPUT) {
                int slot;
                if (in.usage == SM_USAGE_COLOR && in.usage_index < 2) slot = SM_VARY_COLOR0 + in.usage_index;
                else if (in.usage == SM_USAGE_TEXCOORD && in.usage_index < 8) slot = SM_VARY_TEXCOORD0 + in.usage_index;
                else if (in.usage == SM_USAGE_FOG) slot = SM_VARY_FOG;
                else return fail(fmt("ps_3_0 input usage %u%u not supported", in.usage, in.usage_index));
                if (d.num >= 10) return fail("ps_3_0 input register out of range");
                ps3_in[d.num] = slot;
                ps3_vmax = std::max(ps3_vmax, (int)d.num + 1);
                SmInput &si = res.inputs[res.n_inputs++];
                si.reg = d.num; si.usage = in.usage; si.usage_index = in.usage_index; si.mask = d.mask;
            } else if (d.type == R_MISC) {
                if (d.num == 0) uses_vpos = true; else uses_vface = true;
            }
            continue;
        }
        res.n_instructions++;
        auto see_src = [&](const Src &s) {
            if (s.type == R_TEMP) temps.insert(s.num);
            if (s.type == R_ADDR && ps1 && !ps14) tregs.insert(s.num);
            if (s.rel && s.rel_type == R_ADDR) uses_a0 = true;
            if (s.type == R_PREDICATE) uses_p0 = true;
            if (s.type == R_MISC) { if (s.num == 0) uses_vpos = true; else uses_vface = true; }
            if (is_const_type(s.type)) {
                if (s.rel) { uses_crel = true; res.float_rel = 1; res.float_max = 256; }
                else {
                    uint32_t ci = const_index(s.type, s.num);
                    if (ci < 256 && !fdef.count(ci)) res.float_used[ci / 32] |= 1u << (ci % 32);
                    res.float_max = std::max(res.float_max, (int)ci + 1);
                }
            }
            if (s.type == R_CONSTINT && !idef.count(s.num)) res.int_used |= 1u << s.num;
            if (s.type == R_CONSTBOOL && !bdef.count(s.num)) res.bool_used |= 1u << s.num;
        };
        for (const Src &s : in.src) see_src(s);
        if (in.predicated) see_src(in.pred);
        if (in.has_dst) {
            const Dst &d = in.dst;
            if (d.type == R_TEMP) temps.insert(d.num);
            if (d.type == R_ADDR && ps1 && !ps14) tregs.insert(d.num);
            if (d.type == R_ADDR && !ps) uses_a0 = true;
            if (d.type == R_PREDICATE) uses_p0 = true;
            if (d.type == R_DEPTHOUT) uses_oDepth = true;
            if (d.type == R_COLOROUT) color_out |= 1 << d.num;
            if (d.type == R_RASTOUT && d.num == 2) writes_psize = true;
            if (!ps) {
                if (d.type == R_RASTOUT) vary_written |= 1u << (d.num == 0 ? SM_VARY_POSITION : d.num == 1 ? SM_VARY_FOG : SM_VARY_PSIZE);
                if (d.type == R_ATTROUT && d.num < 2) vary_written |= 1u << (SM_VARY_COLOR0 + d.num);
                if (d.type == R_TEXCRDOUT && !vs3 && d.num < 8) vary_written |= 1u << (SM_VARY_TEXCOORD0 + d.num);
                if (d.type == R_TEXCRDOUT && vs3) {
                    if (d.rel) { for (auto &kv : vs3_out) vary_written |= 1u << kv.second.slot; }
                    else if (vs3_out.count(d.num)) vary_written |= 1u << vs3_out[d.num].slot;
                    else return fail(fmt("vs_3_0 writes undeclared o%u", d.num));
                }
            }
        }
        // texture stages used by ps_1_x texture ops / samplers referenced by SM2+ texld
        if (ps1) {
            switch (in.op) {
            case OP_TEX: case OP_TEXBEM: case OP_TEXBEML: case OP_TEXREG2AR: case OP_TEXREG2GB:
            case OP_TEXREG2RGB: case OP_TEXM3x2TEX: case OP_TEXM3x3TEX: case OP_TEXM3x3SPEC:
            case OP_TEXM3x3VSPEC: case OP_TEXDP3TEX:
                use_stage(in.dst.num);
                break;
            default: break;
            }
            if (in.op == OP_TEXDEPTH || in.op == OP_TEXM3x2DEPTH) uses_oDepth = true;
        } else if (in.op == OP_TEX || in.op == OP_TEXLDL || in.op == OP_TEXLDD) {
            if (in.src.size() < 2) return fail("texld without sampler");
            int s = in.src[1].num;
            if (s >= 16) return fail("sampler out of range");
            if (!stype[s]) stype[s] = -1;
        }
        if (in.op == OP_CALL || in.op == OP_CALLNZ || in.op == OP_LABEL)
            return fail("subroutines (call/callnz/label) are not supported");
        if (in.op == OP_TEXKILL) res.uses_kill = 1;
    }
    for (int s = 0; s < 16; s++) {
        if (!stype[s]) continue;
        int dcl = stype[s] > 0 ? stype[s] : 0;
        stype[s] = 0;
        stype[s] = opt.sampler_type[s] ? opt.sampler_type[s] : dcl ? dcl : stage_type(s);
        sshadow[s] = (opt.shadow_samplers >> s) & 1;
        if (sshadow[s] && stype[s] != SM_TEX_2D) sshadow[s] = false;
        SmSampler &rs = res.samplers[res.n_samplers++];
        rs.reg = s; rs.type = stype[s]; rs.shadow = sshadow[s];
    }
    // ps_1_1..1_3: r0 is the output, ps_1_4 likewise
    if (ps1) { temps.insert(0); color_out |= 1; }
    if (ps && !color_out) color_out = 1;
    return true;
}

std::string Emitter::rel_index(uint32_t rel_type, uint32_t rel_num, uint8_t comp, int offset) {
    std::string b;
    if (rel_type == R_LOOP) b = "aL";
    else { b = fmt("a0.%c", COMP[comp]); (void)rel_num; }
    return offset ? fmt("(%s + %d)", b.c_str(), offset) : b;
}

std::string Emitter::src_base(const Src &s) {
    switch (s.type) {
    case R_TEMP: return fmt("r%u", s.num);
    case R_INPUT:
        if (!ps) {
            if (s.rel) return fmt("si.v[clamp(%s, 0, 15)]", rel_index(s.rel_type, s.rel_num, s.rel_comp, s.num).c_str());
            return fmt("si.v[%u]", s.num);
        }
        if (ps3) {
            if (s.rel) return fmt("v[clamp(%s, 0, %d)]", rel_index(s.rel_type, s.rel_num, s.rel_comp, s.num).c_str(), std::max(ps3_vmax - 1, 0));
            return fmt("v[%u]", s.num);
        }
        if (s.num < 2) mark_vary(SM_VARY_COLOR0 + s.num);
        return fmt("v%u", s.num);
    case R_CONST: case R_CONST2: case R_CONST3: case R_CONST4: {
        uint32_t ci = const_index(s.type, s.num);
        if (s.rel) {
            std::string e = fmt("sm_cload(c, %s)", rel_index(s.rel_type, s.rel_num, s.rel_comp, ci).c_str());
            return ps1 && opt.ps1_clamp_consts ? "clamp(" + e + ", -1.0f, 1.0f)" : e;
        }
        if (fdef.count(ci)) return fmt("k%u", ci);
        std::string e = fmt("c[%u]", ci);
        return ps1 && opt.ps1_clamp_consts ? "clamp(" + e + ", -1.0f, 1.0f)" : e;
    }
    case R_ADDR:
        if (ps) {
            if (ps1 && !ps14) return fmt("t%u", s.num);
            return tex_coord_in(s.num);
        }
        return "float4(a0)";
    case R_MISC:
        if (s.num == 0) return "si.vpos";
        return "float4(si.face)";
    case R_CONSTINT: return "float4(" + int_src(s) + ")";
    case R_CONSTBOOL: return "float4(" + bool_src(s) + " ? 1.0f : 0.0f)";
    case R_LOOP: return "float4(float(aL))";
    case R_PREDICATE: return "float4(p0)";
    default:
        fail(fmt("unsupported source register type %u", s.type));
        return "float4(0.0f)";
    }
}

std::string Emitter::src_expr(const Src &s, bool apply_mods) {
    std::string e = src_base(s);
    if (!s.is_identity_swz()) {
        e += ".";
        for (int i = 0; i < 4; i++) e += COMP[s.swz[i]];
    }
    if (!apply_mods) return e;
    switch (s.mod) {
    case SM_NONE: case SM_DZ: case SM_DW: return e;
    case SM_NEG: return "(-" + e + ")";
    case SM_BIAS: return "(" + e + " - 0.5f)";
    case SM_BIASNEG: return "(0.5f - " + e + ")";
    case SM_SIGN: return "(2.0f * " + e + " - 1.0f)";
    case SM_SIGNNEG: return "(1.0f - 2.0f * " + e + ")";
    case SM_COMP: return "(1.0f - " + e + ")";
    case SM_X2: return "(2.0f * " + e + ")";
    case SM_X2NEG: return "(-2.0f * " + e + ")";
    case SM_ABS: return "abs(" + e + ")";
    case SM_ABSNEG: return "(-abs(" + e + "))";
    default:
        fail(fmt("unsupported source modifier %u", s.mod));
        return e;
    }
}

std::string Emitter::int_src(const Src &s) {
    if (idef.count(s.num)) {
        auto &v = idef[s.num];
        return fmt("int4(%d, %d, %d, %d)", v[0], v[1], v[2], v[3]);
    }
    return fmt("ci[%u]", s.num);
}

std::string Emitter::bool_src(const Src &s) {
    if (bdef.count(s.num)) return bdef[s.num] ? "true" : "false";
    return fmt("(cb[%u] != 0)", s.num);
}

std::string Emitter::cmp_op(uint32_t c) {
    switch (c & 7) {
    case 1: return ">"; case 2: return "=="; case 3: return ">=";
    case 4: return "<"; case 5: return "!="; case 6: return "<=";
    default: fail("bad comparison"); return "==";
    }
}

// mode: 0 plain, 1 projected (/w), 2 bias (w), 3 lod (w), 4 gradient (extra, extra2),
//       5 projected by z (ps_1_x D3DTTFF_COUNT3 | PROJECTED)
std::string Emitter::sample(int s, const std::string &coord, int mode, const std::string &gx, const std::string &gy) {
    std::string t = fmt("tex%d", s), sm = fmt("smp%d", s);
    int type = stype[s] ? stype[s] : SM_TEX_2D;
    bool vtx = !ps;
    if (vtx && mode == 0) mode = 3;
    if (vtx && mode == 1) mode = 3;
    std::string c = "(" + coord + ")";
    if (sshadow[s]) {
        std::string uv = c + ".xy", ref = c + ".z";
        if (mode == 1) { uv = c + ".xy / " + c + ".w"; ref = c + ".z / " + c + ".w"; }
        if (mode == 5) { uv = c + ".xy / " + c + ".z"; }
        return fmt("float4(%s.sample_compare(%s, %s, %s))", t.c_str(), sm.c_str(), uv.c_str(), ref.c_str());
    }
    std::string cc;
    const char *sw = type == SM_TEX_2D ? ".xy" : ".xyz";
    if (mode == 1) cc = c + sw + " / " + c + ".w";
    else if (mode == 5) cc = c + sw + " / " + c + ".z";
    else cc = c + sw;
    std::string opt_arg;
    // D3DSAMP_MIPMAPLODBIAS (aux.lodbias) on the pixel shader's implicit-LOD lookups, added to texldb's own bias
    if (mode == 2) opt_arg = ps ? fmt(", bias(%s.w + aux.lodbias[%d])", c.c_str(), s) : ", bias(" + c + ".w)";
    else if (ps && (mode == 0 || mode == 1 || mode == 5)) opt_arg = fmt(", bias(aux.lodbias[%d])", s);
    if (mode == 3) opt_arg = ", level(" + c + ".w)";
    if (mode == 4) {
        const char *g = type == SM_TEX_2D ? "gradient2d" : type == SM_TEX_CUBE ? "gradientcube" : "gradient3d";
        opt_arg = fmt(", %s((%s)%s, (%s)%s)", g, gx.c_str(), sw, gy.c_str(), sw);
    }
    return fmt("%s.sample(%s, %s%s)", t.c_str(), sm.c_str(), cc.c_str(), opt_arg.c_str());
}

// value: float4 (or bool4) expression; out: code that stores it into the destination
bool Emitter::write_dst(const Instr &in, const std::string &val, std::string &out, bool is_bool) {
    const Dst &d = in.dst;
    std::string target;
    bool sat = d.sat, is_a0 = false;
    switch (d.type) {
    case R_TEMP: target = fmt("r%u", d.num); break;
    case R_ADDR:
        if (ps) {
            if (!(ps1 && !ps14)) return fail("write to t# in ps >= 1.4");
            target = fmt("t%u", d.num);
        } else { target = "a0"; is_a0 = true; }
        break;
    case R_RASTOUT:
        if (ps) return fail("rastout in ps");
        target = d.num == 0 ? "so.pos" : d.num == 1 ? "so.fog" : "so.psize";
        break;
    case R_ATTROUT:
        if (d.num >= 2) return fail("oD# out of range");
        target = fmt("so.col[%u]", d.num);
        if (sh.major < 3) sat = true;   // vs_1/2 colour outputs are clamped to [0,1]
        break;
    case R_TEXCRDOUT:
        if (vs3) target = d.rel ? fmt("o[clamp(%s, 0, 11)]", rel_index(d.rel_type, d.rel_num, d.rel_comp, d.num).c_str()) : fmt("o[%u]", d.num);
        else { if (d.num >= 8) return fail("oT# out of range"); target = fmt("so.tex[%u]", d.num); }
        break;
    case R_COLOROUT:
        if (d.num >= 4) return fail("oC# out of range");
        target = fmt("so.oc[%u]", d.num);
        break;
    case R_DEPTHOUT: target = "oDepth"; break;
    case R_PREDICATE: target = "p0"; break;
    default: return fail(fmt("unsupported destination register type %u", d.type));
    }
    std::string v = val;
    if (!is_bool) {
        switch (d.shift) {
        case 0: break;
        case 1: v = "(" + v + ") * 2.0f"; break;
        case 2: v = "(" + v + ") * 4.0f"; break;
        case 3: v = "(" + v + ") * 8.0f"; break;
        case -1: v = "(" + v + ") * 0.5f"; break;
        case -2: v = "(" + v + ") * 0.25f"; break;
        case -3: v = "(" + v + ") * 0.125f"; break;
        default: return fail("bad shift");
        }
        if (sat) v = "saturate(" + v + ")";
    }
    std::string tn = fmt("d%d", tmp_id++);
    out += (is_bool ? "bool4 " : "float4 ") + tn + " = " + v + "; ";
    std::string mask;
    for (int i = 0; i < 4; i++) if (d.mask & (1 << i)) mask += COMP[i];
    if (mask.empty()) return true;
    if (is_a0) {
        bool round = sh.major >= 2;   // vs_2_0+ mova rounds to nearest, vs_1_1 mov a0 floors
        for (char ch : mask)
            out += fmt("a0.%c = int(floor(%s.%c%s)); ", ch, tn.c_str(), ch, round ? " + 0.5f" : "");
        return true;
    }
    if (in.predicated) {
        std::string pm = "p0";
        bool id = in.pred.is_identity_swz();
        if (!id) { pm += "."; for (int i = 0; i < 4; i++) pm += COMP[in.pred.swz[i]]; }
        if (in.pred.mod == SM_NOT) pm = "!" + pm;
        std::string pn = fmt("pm%d", tmp_id++);
        out += "bool4 " + pn + " = " + pm + "; ";
        for (char ch : mask) out += fmt("if (%s.%c) %s.%c = %s.%c; ", pn.c_str(), ch, target.c_str(), ch, tn.c_str(), ch);
        return true;
    }
    if (mask == "xyzw") out += target + " = " + tn + "; ";
    else out += target + "." + mask + " = " + tn + "." + mask + "; ";
    return true;
}

bool Emitter::emit_instr(size_t idx, std::string &pre, std::string &post) {
    const Instr &in = sh.code[idx];
    auto S = [&](int k) -> std::string {
        if (k >= (int)in.src.size()) { fail(fmt("%s: missing operand", op_name(in.op))); return "float4(0.0f)"; }
        return src_expr(in.src[k]);
    };
    // operands into named temporaries
    std::string ids = fmt("_%zu", idx);
    auto decl_srcs = [&](int n) {
        for (int k = 0; k < n; k++) pre += fmt("float4 s%d%s = %s; ", k, ids.c_str(), S(k).c_str());
    };
    auto s = [&](int k) { return fmt("s%d%s", k, ids.c_str()); };
    std::string v;
    switch (in.op) {
    case OP_NOP: return true;
    case OP_MOV: case OP_MOVA: decl_srcs(1); v = s(0); break;
    case OP_ADD: decl_srcs(2); v = s(0) + " + " + s(1); break;
    case OP_SUB: decl_srcs(2); v = s(0) + " - " + s(1); break;
    case OP_MUL: decl_srcs(2); v = "sm_mul(" + s(0) + ", " + s(1) + ")"; break;
    case OP_MAD: decl_srcs(3); v = "sm_mad(" + s(0) + ", " + s(1) + ", " + s(2) + ")"; break;
    case OP_RCP: decl_srcs(1); v = "float4(sm_rcp(" + s(0) + ".w))"; break;
    case OP_RSQ: decl_srcs(1); v = "float4(sm_rsq(" + s(0) + ".w))"; break;
    case OP_EXP: decl_srcs(1); v = "float4(sm_exp(" + s(0) + ".w))"; break;
    case OP_LOG: decl_srcs(1); v = "float4(sm_log(" + s(0) + ".w))"; break;
    case OP_EXPP: decl_srcs(1); v = sh.major < 2 ? "sm_expp1(" + s(0) + ".w)" : "float4(sm_exp(" + s(0) + ".w))"; break;
    case OP_LOGP: decl_srcs(1); v = sh.major < 2 ? "sm_logp1(" + s(0) + ".w)" : "float4(sm_log(" + s(0) + ".w))"; break;
    case OP_FRC: decl_srcs(1); v = "fract(" + s(0) + ")"; break;
    case OP_DP3: decl_srcs(2); v = "float4(sm_dp3(" + s(0) + ", " + s(1) + "))"; break;
    case OP_DP4: decl_srcs(2); v = "float4(sm_dp4(" + s(0) + ", " + s(1) + "))"; break;
    case OP_DP2ADD: decl_srcs(3); v = "float4(sm_dp2(" + s(0) + ", " + s(1) + ") + " + s(2) + ".x)"; break;
    case OP_MIN: decl_srcs(2); v = "sm_min(" + s(0) + ", " + s(1) + ")"; break;
    case OP_MAX: decl_srcs(2); v = "sm_max(" + s(0) + ", " + s(1) + ")"; break;
    case OP_SLT: decl_srcs(2); v = "select(float4(0.0f), float4(1.0f), " + s(0) + " < " + s(1) + ")"; break;
    case OP_SGE: decl_srcs(2); v = "select(float4(0.0f), float4(1.0f), " + s(0) + " >= " + s(1) + ")"; break;
    case OP_LIT: decl_srcs(1); v = "sm_lit(" + s(0) + ")"; break;
    case OP_DST: decl_srcs(2); v = "float4(1.0f, sm_mulf(" + s(0) + ".y, " + s(1) + ".y), " + s(0) + ".z, " + s(1) + ".w)"; break;
    case OP_LRP: decl_srcs(3); v = s(2) + " + " + s(0) + " * (" + s(1) + " - " + s(2) + ")"; break;
    case OP_POW: decl_srcs(2); v = "float4(sm_pow(" + s(0) + ".w, " + s(1) + ".w))"; break;
    case OP_CRS: decl_srcs(2); v = "float4(cross(" + s(0) + ".xyz, " + s(1) + ".xyz), 0.0f)"; break;
    case OP_SGN: decl_srcs(1); v = "sign(" + s(0) + ")"; break;
    case OP_ABS: decl_srcs(1); v = "abs(" + s(0) + ")"; break;
    case OP_NRM: decl_srcs(1); v = "sm_nrm(" + s(0) + ")"; break;
    case OP_SINCOS: decl_srcs(1); v = "float4(cos(" + s(0) + ".w), sin(" + s(0) + ".w), 0.0f, 0.0f)"; break;
    case OP_CMP: decl_srcs(3); v = "select(" + s(2) + ", " + s(1) + ", " + s(0) + " >= 0.0f)"; break;
    case OP_CND:
        decl_srcs(3);
        if (ps14) v = "select(" + s(2) + ", " + s(1) + ", " + s(0) + " > 0.5f)";
        else v = "(" + s(0) + ".w > 0.5f ? " + s(1) + " : " + s(2) + ")";
        break;
    case OP_DSX: decl_srcs(1); v = "dfdx(" + s(0) + ")"; break;
    case OP_DSY: decl_srcs(1); v = "dfdy(" + s(0) + ")"; break;
    case OP_M4x4: case OP_M4x3: case OP_M3x4: case OP_M3x3: case OP_M3x2: {
        int rows = in.op == OP_M4x4 || in.op == OP_M3x4 ? 4 : in.op == OP_M3x2 ? 2 : 3;
        bool four = in.op == OP_M4x4 || in.op == OP_M4x3;
        decl_srcs(1);
        if (in.src.size() < 2) return fail("matrix op without matrix");
        std::string comps;
        for (int r = 0; r < 4; r++) {
            if (r < rows) {
                Src m = in.src[1];
                m.num += r;
                comps += fmt("%s(%s, %s)", four ? "sm_dp4" : "sm_dp3", s(0).c_str(), src_expr(m).c_str());
            } else comps += "0.0f";
            if (r < 3) comps += ", ";
        }
        v = "float4(" + comps + ")";
        break;
    }
    case OP_SETP:
        decl_srcs(2);
        v = s(0) + " " + cmp_op(in.ctrl) + " " + s(1);
        return write_dst(in, v, post, true);
    case OP_BEM: {
        decl_srcs(2);
        std::string m = fmt("aux.bumpenv[%u]", in.dst.num & 7);
        v = fmt("float4(%s.x + %s.x * %s.x + %s.z * %s.y, %s.y + %s.y * %s.x + %s.w * %s.y, 0.0f, 0.0f)",
                s(0).c_str(), m.c_str(), s(1).c_str(), m.c_str(), s(1).c_str(),
                s(0).c_str(), m.c_str(), s(1).c_str(), m.c_str(), s(1).c_str());
        break;
    }

    // ---- texture ops
    case OP_TEX: {
        if (!ps) {  // texld in vs_3_0 behaves like texldl with lod 0
            decl_srcs(1);
            v = sample(in.src[1].num, "float4(" + s(0) + ".xyz, 0.0f)", 3);
            break;
        }
        if (ps1 && !ps14) {      // tex tN
            int n = in.dst.num;
            int pj = n < 8 ? opt.tex_projected[n] : 0;
            v = sample(n, tex_coord_in(n), pj == 4 ? 1 : pj == 3 ? 5 : 0);
            break;
        }
        if (ps14) {              // texld rN, src
            if (in.src.empty()) return fail("texld without source");
            const Src &c = in.src[0];
            std::string e = src_expr(c, false);
            int mode = c.mod == SM_DW ? 1 : c.mod == SM_DZ ? 5 : 0;
            v = sample(in.dst.num, e, mode);
            break;
        }
        decl_srcs(1);
        int mode = (in.ctrl & 3) == 1 ? 1 : (in.ctrl & 3) == 2 ? 2 : 0;
        v = sample(in.src[1].num, s(0), mode);
        if (!in.src[1].is_identity_swz()) {
            v = "(" + v + ").";
            for (int i = 0; i < 4; i++) v += COMP[in.src[1].swz[i]];
        }
        break;
    }
    case OP_TEXLDL:
        decl_srcs(1);
        v = sample(in.src[1].num, s(0), 3);
        break;
    case OP_TEXLDD:
        if (in.src.size() < 4) return fail("texldd operands");
        pre += fmt("float4 s0%s = %s; float4 s2%s = %s; float4 s3%s = %s; ", ids.c_str(), S(0).c_str(),
                   ids.c_str(), S(2).c_str(), ids.c_str(), S(3).c_str());
        v = sample(in.src[1].num, s(0), 4, s(2), s(3));
        break;
    case OP_TEXCOORD:
        if (ps14) {              // texcrd rN, tM[_dz|_dw]
            if (in.src.empty()) return fail("texcrd without source");
            const Src &c = in.src[0];
            std::string e = "(" + src_expr(c, false) + ")";
            if (c.mod == SM_DW) v = "float4(" + e + ".xyz / " + e + ".w, 1.0f)";
            else if (c.mod == SM_DZ) v = "float4(" + e + ".xy / " + e + ".z, 1.0f, 1.0f)";
            else v = e;
        } else {
            v = "float4(saturate(" + tex_coord_in(in.dst.num) + ".xyz), 1.0f)";
        }
        break;
    case OP_TEXKILL: {
        std::string e;
        std::string mask;
        if (ps1 && !ps14) { e = tex_coord_in(in.dst.num); mask = "xyz"; }
        else {
            Src t;
            t.type = in.dst.type;
            t.num = in.dst.num;
            e = src_base(t);
            if (sh.major < 2) mask = "xyz";
            else for (int i = 0; i < 4; i++) if (in.dst.mask & (1 << i)) mask += COMP[i];
        }
        std::string cond;
        for (char ch : mask) cond += (cond.empty() ? "" : " || ") + fmt("(%s).%c < 0.0f", e.c_str(), ch);
        if (!cond.empty()) post += "if (" + cond + ") so.killed = true; ";
        return true;
    }
    case OP_TEXBEM: case OP_TEXBEML: {
        if (in.src.empty()) return fail("texbem operand");
        int n = in.dst.num;
        std::string src = src_expr(in.src[0]);
        std::string m = fmt("aux.bumpenv[%d]", n & 7);
        pre += fmt("float4 s0%s = %s; float4 tc%s = %s; ", ids.c_str(), src.c_str(), ids.c_str(), tex_coord_in(n).c_str());
        std::string uv = fmt("float4(tc%s.x + %s.x * s0%s.x + %s.z * s0%s.y, tc%s.y + %s.y * s0%s.x + %s.w * s0%s.y, 0.0f, 1.0f)",
                             ids.c_str(), m.c_str(), ids.c_str(), m.c_str(), ids.c_str(),
                             ids.c_str(), m.c_str(), ids.c_str(), m.c_str(), ids.c_str());
        v = sample(n, uv, 0);
        if (in.op == OP_TEXBEML)
            v = fmt("(%s) * saturate(s0%s.z * aux.bumplum[%d].x + aux.bumplum[%d].y)", v.c_str(), ids.c_str(), n & 7, n & 7);
        break;
    }
    case OP_TEXREG2AR: decl_srcs(1); v = sample(in.dst.num, "float4(" + s(0) + ".w, " + s(0) + ".x, 0.0f, 1.0f)", 0); break;
    case OP_TEXREG2GB: decl_srcs(1); v = sample(in.dst.num, "float4(" + s(0) + ".y, " + s(0) + ".z, 0.0f, 1.0f)", 0); break;
    case OP_TEXREG2RGB: decl_srcs(1); v = sample(in.dst.num, "float4(" + s(0) + ".xyz, 1.0f)", 0); break;
    case OP_TEXM3x2PAD: case OP_TEXM3x3PAD: {
        decl_srcs(1);
        // first or second pad row: count preceding pads of the same matrix op
        int row = 0;
        if (in.op == OP_TEXM3x3PAD && idx > 0 && sh.code[idx - 1].op == OP_TEXM3x3PAD) row = 1;
        pre += fmt("sm_pad%d = sm_dp3(%s, %s); ", row, tex_coord_in(in.dst.num).c_str(), s(0).c_str());
        return true;
    }
    case OP_TEXM3x2TEX: case OP_TEXM3x2DEPTH: case OP_TEXDP3TEX: case OP_TEXDP3: {
        decl_srcs(1);
        std::string d = fmt("sm_dp3(%s, %s)", tex_coord_in(in.dst.num).c_str(), s(0).c_str());
        if (in.op == OP_TEXM3x2TEX) v = sample(in.dst.num, "float4(sm_pad0, " + d + ", 0.0f, 1.0f)", 0);
        else if (in.op == OP_TEXDP3TEX) v = sample(in.dst.num, "float4(" + d + ", 0.0f, 0.0f, 1.0f)", 0);
        else if (in.op == OP_TEXDP3) v = "float4(" + d + ")";
        else {   // texm3x2depth: depth = z / w
            pre += fmt("float w%s = %s; ", ids.c_str(), d.c_str());
            post += fmt("oDepth = float4(w%s == 0.0f ? 1.0f : sm_pad0 / w%s); ", ids.c_str(), ids.c_str());
            return true;
        }
        break;
    }
    case OP_TEXM3x3TEX: case OP_TEXM3x3: case OP_TEXM3x3SPEC: case OP_TEXM3x3VSPEC: {
        decl_srcs(in.op == OP_TEXM3x3SPEC ? 2 : 1);
        int n = in.dst.num;
        pre += fmt("float4 nv%s = float4(sm_pad0, sm_pad1, sm_dp3(%s, %s), 1.0f); ", ids.c_str(),
                   tex_coord_in(n).c_str(), s(0).c_str());
        std::string nv = "nv" + ids;
        if (in.op == OP_TEXM3x3) v = nv;
        else if (in.op == OP_TEXM3x3TEX) v = sample(n, nv, 0);
        else {
            std::string e = in.op == OP_TEXM3x3SPEC ? s(1) :
                fmt("float4(si.tex[%d].w, si.tex[%d].w, si.tex[%d].w, 0.0f)", n - 2 < 0 ? 0 : n - 2, n - 1 < 0 ? 0 : n - 1, n);
            pre += fmt("float3 e%s = (%s).xyz; float3 nn%s = %s.xyz; ", ids.c_str(), e.c_str(), ids.c_str(), nv.c_str());
            pre += fmt("float3 rf%s = 2.0f * nn%s * dot(nn%s, e%s) / dot(nn%s, nn%s) - e%s; ", ids.c_str(), ids.c_str(),
                       ids.c_str(), ids.c_str(), ids.c_str(), ids.c_str(), ids.c_str());
            v = sample(n, "float4(rf" + ids + ", 1.0f)", 0);
        }
        break;
    }
    case OP_TEXDEPTH:
        post += fmt("oDepth = float4(r%u.y == 0.0f ? 1.0f : r%u.x / r%u.y); ", in.dst.num, in.dst.num, in.dst.num);
        return true;
    case OP_PHASE: return true;
    default:
        return fail(fmt("unsupported instruction %s (%u)", op_name(in.op), in.op));
    }
    if (!err.empty()) return false;
    return write_dst(in, v, post);
}

std::string Emitter::tex_param_decls(bool attrs) {
    std::string r;
    for (int s = 0; s < 16; s++) {
        if (!stype[s]) continue;
        const char *tt = sshadow[s] ? "depth2d<float>" : stype[s] == SM_TEX_CUBE ? "texturecube<float>" :
                         stype[s] == SM_TEX_3D ? "texture3d<float>" : "texture2d<float>";
        if (attrs) r += fmt(",\n    %s tex%d [[texture(%d)]], sampler smp%d [[sampler(%d)]]", tt, s, s, s, s);
        else r += fmt(", %s tex%d, sampler smp%d", tt, s, s);
    }
    return r;
}

std::string Emitter::tex_param_args() {
    std::string r;
    for (int s = 0; s < 16; s++)
        if (stype[s]) r += fmt(", tex%d, smp%d", s, s);
    return r;
}

bool Emitter::emit_all() {
    // flow control and instructions
    for (size_t i = 0; i < sh.code.size(); i++) {
        const Instr &in = sh.code[i];
        if (in.op == OP_DCL || in.op == OP_DEF || in.op == OP_DEFI || in.op == OP_DEFB) continue;
        switch (in.op) {
        case OP_REP: {
            if (in.src.empty()) return fail("rep operand");
            int id = loop_id++;
            line(fmt("for (int rc%d = %s.x; rc%d > 0; --rc%d) {", id, int_src(in.src[0]).c_str(), id, id));
            depth++;
            closers.push_back("}");
            continue;
        }
        case OP_LOOP: {
            if (in.src.size() < 2) return fail("loop operands");
            int id = loop_id++;
            line(fmt("{ int4 li%d = %s; int aL = li%d.y;", id, int_src(in.src[1]).c_str(), id));
            line(fmt("for (int lc%d = li%d.x; lc%d > 0; --lc%d, aL += li%d.z) {", id, id, id, id, id));
            depth++;
            closers.push_back("} }");
            continue;
        }
        case OP_ENDREP: case OP_ENDLOOP: case OP_ENDIF:
            if (closers.empty()) return fail("unbalanced flow control");
            depth--;
            line(closers.back());
            closers.pop_back();
            continue;
        case OP_IF: {
            if (in.src.empty()) return fail("if operand");
            const Src &c = in.src[0];
            std::string cond;
            if (c.type == R_PREDICATE) cond = std::string(c.mod == SM_NOT ? "!" : "") + "p0." + COMP[c.swz[0]];
            else cond = bool_src(c);
            line("if (" + cond + ") {");
            depth++;
            closers.push_back("}");
            continue;
        }
        case OP_IFC:
            if (in.src.size() < 2) return fail("ifc operands");
            line("if (" + scalar_src(in.src[0]) + " " + cmp_op(in.ctrl) + " " + scalar_src(in.src[1]) + ") {");
            depth++;
            closers.push_back("}");
            continue;
        case OP_ELSE:
            depth--;
            line("} else {");
            depth++;
            continue;
        case OP_BREAK: line("break;"); continue;
        case OP_BREAKC:
            if (in.src.size() < 2) return fail("breakc operands");
            line("if (" + scalar_src(in.src[0]) + " " + cmp_op(in.ctrl) + " " + scalar_src(in.src[1]) + ") break;");
            continue;
        case OP_BREAKP: {
            if (in.src.empty()) return fail("breakp operand");
            const Src &c = in.src[0];
            line(std::string("if (") + (c.mod == SM_NOT ? "!" : "") + "p0." + COMP[c.swz[0]] + ") break;");
            continue;
        }
        case OP_RET:
            if (!closers.empty()) return fail("ret inside flow control is not supported");
            continue;
        default: break;
        }
        // ordinary instruction, possibly with a co-issued partner (ps_1_x)
        std::string pre, post;
        size_t j = i;
        if (!emit_instr(i, pre, post)) return false;
        while (j + 1 < sh.code.size() && sh.code[j + 1].coissue) {
            j++;
            if (!emit_instr(j, pre, post)) return false;
        }
        std::string dis;
        {
            Shader one;
            one.ps = sh.ps; one.major = sh.major; one.minor = sh.minor;
            for (size_t k = i; k <= j; k++) one.code.push_back(sh.code[k]);
            std::string d = disassemble(one);
            d = d.substr(d.find('\n') + 1);
            while (!d.empty() && d.back() == '\n') d.pop_back();
            for (char &ch : d) if (ch == '\n') ch = ' ';
            dis = d;
        }
        line("// " + dis);
        line("{ " + pre + post + "}");
        i = j;
    }
    if (!closers.empty()) return fail("unterminated flow control");
    return true;
}

bool Emitter::run(std::string &msl) {
    if (!analyse()) return false;
    if (!emit_all()) return false;
    std::string o = HELPERS;
    if (opt.ieee_mul) o = "#define SM_IEEE_MUL 1\n" + o;

    // register structs
    if (!ps) {
        o += "\nstruct SmVSIn  { float4 v[16]; };\n";
        o += "struct SmVSOut { float4 pos; float4 col[2]; float4 tex[8]; float4 fog; float4 psize; };\n";
    } else {
        o += "\nstruct SmPSIn  { float4 col[2]; float4 tex[8]; float4 fog; float4 vpos; float face; };\n";
        o += "struct SmPSOut { float4 oc[4]; float depth; bool killed; };\n";
    }
    // relative constant loader (def'd constants override the buffer)
    if (uses_crel) {
        o += "\nstatic inline float4 sm_cload(constant float4 *c, int i) {\n    switch (i) {\n";
        for (auto &kv : fdef)
            o += fmt("    case %d: return float4(%s, %s, %s, %s);\n", kv.first, flit(kv.second[0]).c_str(),
                     flit(kv.second[1]).c_str(), flit(kv.second[2]).c_str(), flit(kv.second[3]).c_str());
        o += "    default: return (uint)i < 256u ? c[i] : float4(0.0f);\n    }\n}\n";
    }

    // ---- body
    std::string sig = ps ? "thread const SmPSIn &si, thread SmPSOut &so" : "thread const SmVSIn &si, thread SmVSOut &so";
    sig += ", constant float4 *c, constant int4 *ci, constant int *cb";
    if (ps) sig += ", constant SmPSAux &aux";
    sig += tex_param_decls(false);
    o += "\nstatic void sm_body(" + sig + ") {\n";
    o += "    (void)c; (void)ci; (void)cb;\n";
    for (auto &kv : fdef) {
        float v[4];
        memcpy(v, kv.second.data(), 16);
        if (ps1 && opt.ps1_clamp_consts)
            for (float &x : v) x = std::fmin(1.0f, std::fmax(-1.0f, x));
        o += fmt("    const float4 k%d = float4(%s, %s, %s, %s);\n", kv.first, flit(v[0]).c_str(), flit(v[1]).c_str(),
                 flit(v[2]).c_str(), flit(v[3]).c_str());
    }
    for (int r : temps) o += fmt("    float4 r%d = float4(0.0f);\n", r);
    for (int t : tregs) o += fmt("    float4 t%d = float4(0.0f);\n", t);
    if (uses_a0) o += "    int4 a0 = int4(0);\n";
    if (uses_p0) o += "    bool4 p0 = bool4(false);\n";
    if (ps1) o += "    float sm_pad0 = 0.0f, sm_pad1 = 0.0f; (void)sm_pad0; (void)sm_pad1;\n";
    if (ps && !ps3) {
        for (int k = 0; k < 2; k++)
            if (vary_read & (1u << (SM_VARY_COLOR0 + k))) o += fmt("    float4 v%d = saturate(si.col[%d]);\n", k, k);
    }
    if (ps3) {
        o += fmt("    float4 v[%d];\n", std::max(ps3_vmax, 1));
        for (int k = 0; k < std::max(ps3_vmax, 1); k++) {
            if (!ps3_in.count(k)) { o += fmt("    v[%d] = float4(0.0f);\n", k); continue; }
            int slot = ps3_in[k];
            mark_vary(slot);
            if (slot == SM_VARY_FOG) o += fmt("    v[%d] = si.fog;\n", k);
            else if (slot >= SM_VARY_TEXCOORD0) o += fmt("    v[%d] = si.tex[%d];\n", k, slot - SM_VARY_TEXCOORD0);
            else o += fmt("    v[%d] = si.col[%d];\n", k, slot - SM_VARY_COLOR0);
        }
    }
    if (!ps) {
        o += "    so.pos = float4(0.0f); so.col[0] = so.col[1] = float4(0.0f);\n";
        o += "    for (int k = 0; k < 8; k++) so.tex[k] = float4(0.0f);\n";
        o += "    so.fog = float4(1.0f, 0.0f, 0.0f, 0.0f); so.psize = float4(1.0f, 0.0f, 0.0f, 0.0f);\n";
        if (vs3) o += "    float4 o[12]; for (int k = 0; k < 12; k++) o[k] = float4(0.0f);\n";
    } else {
        o += "    for (int k = 0; k < 4; k++) so.oc[k] = float4(0.0f);\n    so.depth = 0.0f; so.killed = false;\n";
        if (uses_oDepth) o += "    float4 oDepth = float4(si.vpos.z);\n";
    }
    o += code;
    if (vs3) {
        for (auto &kv : vs3_out) {
            int slot = kv.second.slot;
            if (slot == SM_VARY_POSITION) o += fmt("    so.pos = o[%d];\n", kv.first);
            else if (slot == SM_VARY_FOG) o += fmt("    so.fog = o[%d];\n", kv.first);
            else if (slot == SM_VARY_PSIZE) { o += fmt("    so.psize = o[%d];\n", kv.first); writes_psize = true; }
            else if (slot >= SM_VARY_TEXCOORD0) o += fmt("    so.tex[%d] = o[%d];\n", slot - SM_VARY_TEXCOORD0, kv.first);
            else o += fmt("    so.col[%d] = o[%d];\n", slot - SM_VARY_COLOR0, kv.first);
        }
    }
    if (ps1) o += "    so.oc[0] = r0;\n";
    if (ps && uses_oDepth) o += "    so.depth = oDepth.x;\n";
    o += "}\n";

    // ---- stage wrapper
    if (!ps) {
        bool any_in = false;
        std::string attr = "\nstruct SmVSAttr {\n";
        for (int k = 0; k < 16; k++) {
            if (!vs_in_mask[k]) continue;
            any_in = true;
            const char *t = (opt.vs_input_int >> k) & 1 ? "int4" : (opt.vs_input_uint >> k) & 1 ? "uint4" : "float4";
            attr += fmt("    %s v%d [[attribute(%d)]];\n", t, k, k);
        }
        attr += "};\n";
        if (any_in) o += attr;
        o += "\nstruct SmVSStageOut {\n    float4 pos [[position]];\n";
        o += "    float4 color0 [[user(color0)]];\n    float4 color1 [[user(color1)]];\n";
        for (int k = 0; k < 8; k++) o += fmt("    float4 texcoord%d [[user(texcoord%d)]];\n", k, k);
        o += "    float4 fog [[user(fog)]];\n";
        o += "    float psize [[point_size]];\n";
        o += "};\n\n";
        o += "vertex SmVSStageOut sm_main(";
        if (any_in) o += "SmVSAttr va [[stage_in]],\n    ";
        o += fmt("constant float4 *c [[buffer(%d)]], constant int4 *ci [[buffer(%d)]], constant int *cb [[buffer(%d)]],\n"
                 "    constant SmVSAux &aux [[buffer(%d)]]", SM_VS_BUF_FLOAT, SM_VS_BUF_INT, SM_VS_BUF_BOOL, SM_VS_BUF_AUX);
        o += tex_param_decls(true) + ") {\n";
        o += "    SmVSIn si;\n    for (int k = 0; k < 16; k++) si.v[k] = float4(0.0f, 0.0f, 0.0f, 1.0f);\n";
        for (int k = 0; k < 16; k++)
            if (vs_in_mask[k]) o += fmt("    si.v[%d] = float4(va.v%d);\n", k, k);
        o += "    SmVSOut so;\n    sm_body(si, so, c, ci, cb" + tex_param_args() + ");\n";
        o += "    SmVSStageOut r;\n    r.pos = so.pos;\n    r.pos.xy += aux.pos_fixup.xy * so.pos.w;\n";
        o += "    r.color0 = so.col[0]; r.color1 = so.col[1];\n";
        for (int k = 0; k < 8; k++) o += fmt("    r.texcoord%d = so.tex[%d];\n", k, k);
        o += "    r.fog = so.fog;\n";
        o += writes_psize ? "    r.psize = so.psize.x;\n" : "    r.psize = aux.point_size;\n";
        o += "    return r;\n}\n";
    } else {
        std::map<int, std::string> centroid;
        for (const Instr &in : sh.code)
            if (in.op == OP_DCL && in.dst.centroid) {
                int slot = -1;
                if (ps3 && ps3_in.count(in.dst.num)) slot = ps3_in[in.dst.num];
                else if (!ps3 && in.dst.type == R_ADDR) slot = SM_VARY_TEXCOORD0 + in.dst.num;
                else if (!ps3 && in.dst.type == R_INPUT) slot = SM_VARY_COLOR0 + in.dst.num;
                if (slot >= 0) centroid[slot] = ", centroid_perspective";
            }
        o += "\nstruct SmPSStageIn {\n    float4 pos [[position]];\n";
        for (int k = 0; k < 2; k++)
            if (vary_read & (1u << (SM_VARY_COLOR0 + k)))
                o += fmt("    float4 color%d [[user(color%d)%s]];\n", k, k, centroid[SM_VARY_COLOR0 + k].c_str());
        for (int k = 0; k < 8; k++)
            if (vary_read & (1u << (SM_VARY_TEXCOORD0 + k)))
                o += fmt("    float4 texcoord%d [[user(texcoord%d)%s]];\n", k, k, centroid[SM_VARY_TEXCOORD0 + k].c_str());
        if (vary_read & (1u << SM_VARY_FOG)) o += "    float4 fog [[user(fog)]];\n";
        o += "};\n\nstruct SmPSStageOut {\n";
        for (int k = 0; k < 4; k++)
            if (color_out & (1 << k)) o += fmt("    float4 c%d [[color(%d)]];\n", k, k);
        if (uses_oDepth) o += "    float depth [[depth(any)]];\n";
        o += "};\n\n";
        o += "fragment SmPSStageOut sm_main(SmPSStageIn pi [[stage_in]]";
        if (uses_vface) o += ", bool front [[front_facing]]";
        o += fmt(",\n    constant float4 *c [[buffer(%d)]], constant int4 *ci [[buffer(%d)]], constant int *cb [[buffer(%d)]],\n"
                 "    constant SmPSAux &aux [[buffer(%d)]]", SM_PS_BUF_FLOAT, SM_PS_BUF_INT, SM_PS_BUF_BOOL, SM_PS_BUF_AUX);
        o += tex_param_decls(true) + ") {\n";
        o += "    SmPSIn si;\n    si.col[0] = si.col[1] = float4(0.0f);\n    for (int k = 0; k < 8; k++) si.tex[k] = float4(0.0f);\n";
        o += "    si.fog = float4(0.0f);\n";
        for (int k = 0; k < 2; k++)
            if (vary_read & (1u << (SM_VARY_COLOR0 + k))) o += fmt("    si.col[%d] = pi.color%d;\n", k, k);
        for (int k = 0; k < 8; k++)
            if (vary_read & (1u << (SM_VARY_TEXCOORD0 + k))) o += fmt("    si.tex[%d] = pi.texcoord%d;\n", k, k);
        if (vary_read & (1u << SM_VARY_FOG)) o += "    si.fog = pi.fog;\n";
        o += "    si.vpos = float4(pi.pos.xy - 0.5f, pi.pos.z, pi.pos.w);\n";
        o += std::string("    si.face = ") + (uses_vface ? "front ? 1.0f : -1.0f" : "1.0f") + ";\n";
        o += "    SmPSOut so;\n    sm_body(si, so, c, ci, cb, aux" + tex_param_args() + ");\n";
        o += "    if (so.killed) discard_fragment();\n";
        o += "    if (aux.alpha_func != 0u && !sm_alpha_test(aux.alpha_func, so.oc[0].w, aux.alpha_ref)) discard_fragment();\n";
        o += "    SmPSStageOut r;\n";
        for (int k = 0; k < 4; k++)
            if (color_out & (1 << k)) o += fmt("    r.c%d = so.oc[%d];\n", k, k);
        if (uses_oDepth) o += "    r.depth = so.depth;\n";
        o += "    return r;\n}\n";
    }

    // ---- optional compute test kernel: flat float4 in/out
    if (opt.emit_test_kernel) {
        o += "\nkernel void sm_test_kernel(device const float4 *tin [[buffer(0)]], device float4 *tout [[buffer(1)]],\n"
             "    constant float4 *c [[buffer(2)]], constant int4 *ci [[buffer(3)]], constant int *cb [[buffer(4)]],\n"
             "    constant SmPSAux &aux [[buffer(5)]]";
        o += tex_param_decls(true) + ",\n    uint tid [[thread_position_in_grid]]) {\n";
        if (!ps) {
            o += "    (void)aux;\n    SmVSIn si;\n    for (int k = 0; k < 16; k++) si.v[k] = tin[tid * 16 + k];\n";
            o += "    SmVSOut so;\n    sm_body(si, so, c, ci, cb" + tex_param_args() + ");\n";
            o += "    device float4 *d = tout + tid * 13;\n    d[0] = so.pos; d[1] = so.col[0]; d[2] = so.col[1];\n";
            o += "    for (int k = 0; k < 8; k++) d[3 + k] = so.tex[k];\n    d[11] = so.fog; d[12] = so.psize;\n";
        } else {
            o += "    SmPSIn si;\n    device const float4 *s = tin + tid * 12;\n";
            o += "    si.col[0] = s[0]; si.col[1] = s[1];\n    for (int k = 0; k < 8; k++) si.tex[k] = s[2 + k];\n";
            o += "    si.fog = s[10]; si.vpos = s[11]; si.face = 1.0f;\n";
            o += "    SmPSOut so;\n    sm_body(si, so, c, ci, cb, aux" + tex_param_args() + ");\n";
            o += "    device float4 *d = tout + tid * 6;\n    for (int k = 0; k < 4; k++) d[k] = so.oc[k];\n";
            o += "    d[4] = float4(so.depth); d[5] = float4(so.killed ? 1.0f : 0.0f);\n";
        }
        o += "}\n";
    }

    res.varyings_read = vary_read;
    res.varyings_written = vary_written;
    res.color_outputs = ps ? color_out : 0;
    res.writes_depth = uses_oDepth;
    for (auto &kv : fdef)
        if (kv.first < 256) res.float_defined[kv.first / 32] |= 1u << (kv.first % 32);
    msl = o;
    return err.empty();
}

}  // namespace
}  // namespace sm

extern "C" void sm_default_options(SmOptions *opt) {
    memset(opt, 0, sizeof *opt);
    opt->ps1_clamp_consts = 1;
}

extern "C" int sm_translate(const uint32_t *tokens, size_t n, const SmOptions *opt_in, SmResult *out) {
    memset(out, 0, sizeof *out);
    out->entry = "sm_main";
    SmOptions opt;
    if (opt_in) opt = *opt_in; else sm_default_options(&opt);
    sm::Shader sh;
    if (!sm::parse(tokens, n, sh)) {
        snprintf(out->error, sizeof out->error, "parse: %s", sh.error.c_str());
        return -1;
    }
    out->shader_type = sh.ps ? SM_PIXEL : SM_VERTEX;
    out->major = sh.major;
    out->minor = sh.minor;
    if (!sh.ctab.empty()) {
        out->constants = (SmConstant *)calloc(sh.ctab.size(), sizeof(SmConstant));
        out->n_constants = (int)sh.ctab.size();
        for (size_t i = 0; i < sh.ctab.size(); i++) {
            SmConstant &c = out->constants[i];
            snprintf(c.name, sizeof c.name, "%s", sh.ctab[i].name.c_str());
            c.set = (uint8_t)sh.ctab[i].set;
            c.type = (uint8_t)sh.ctab[i].type;
            c.reg = (uint16_t)sh.ctab[i].reg;
            c.count = (uint16_t)sh.ctab[i].count;
        }
    }
    std::string msl;
    sm::Emitter em(sh, opt, *out);
    if (!em.run(msl)) {
        snprintf(out->error, sizeof out->error, "translate: %s", em.err.c_str());
        return -1;
    }
    out->msl = (char *)malloc(msl.size() + 1);
    memcpy(out->msl, msl.c_str(), msl.size() + 1);
    out->msl_len = msl.size();
    return 0;
}

extern "C" void sm_result_free(SmResult *r) {
    if (!r) return;
    free(r->msl);
    free(r->constants);
    r->msl = nullptr;
    r->constants = nullptr;
}
