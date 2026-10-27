// sm_interp.cpp - CPU reference interpreter (see sm_interp.h).
#include "sm_interp.h"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <limits>
#include <map>
#include <string>

namespace interp {
using namespace sm;

bool g_trace = false;
int g_ulp_nudge = 0;
static float nudge(float x) { return g_ulp_nudge ? x * (1.0f + (float)g_ulp_nudge * 0x1p-20f) : x; }
static const float INF = std::numeric_limits<float>::infinity();

// ---- textures -----------------------------------------------------------------------
static int texel_index(float u, int n) {
    if (!(u == u)) return 0;
    float f = std::floor(u * (float)n);
    if (f < 0) f = 0;
    if (f > n - 1) f = (float)(n - 1);
    return (int)f;
}

V4 Texture::sample(const float *c) const {
    int x, y, z = 0;
    if (type == SM_TEX_CUBE) {
        float rx = c[0], ry = c[1], rz = c[2];
        float ax = std::fabs(rx), ay = std::fabs(ry), az = std::fabs(rz);
        float sc, tc, ma;
        if (ax >= ay && ax >= az) { ma = ax; if (rx >= 0) { z = 0; sc = -rz; tc = -ry; } else { z = 1; sc = rz; tc = -ry; } }
        else if (ay >= az) { ma = ay; if (ry >= 0) { z = 2; sc = rx; tc = rz; } else { z = 3; sc = rx; tc = -rz; } }
        else { ma = az; if (rz >= 0) { z = 4; sc = rx; tc = -ry; } else { z = 5; sc = -rx; tc = -ry; } }
        float s = 0.5f * (sc / ma + 1.0f), t = 0.5f * (tc / ma + 1.0f);
        x = texel_index(s, w);
        y = texel_index(t, h);
    } else {
        x = texel_index(c[0], w);
        y = texel_index(c[1], h);
        if (type == SM_TEX_3D) z = texel_index(c[2], d);
    }
    const float *p = &texels[(((size_t)z * h + y) * w + x) * 4];
    return {p[0], p[1], p[2], p[3]};
}

float Texture::compare(float u, float v, float ref) const {
    int x = texel_index(u, w), y = texel_index(v, h);
    float t = texels[(size_t)y * w + x];
    float r = std::min(std::max(ref, 0.0f), 1.0f);   // Metal clamps the reference for depth formats
    return r <= t ? 1.0f : 0.0f;
}

// ---- machine --------------------------------------------------------------------------
namespace {

struct Machine {
    const Shader &sh;
    const Context &ctx;
    bool ps, ps1, ps14, vs3, ps3;
    std::string err;

    V4 r[32] = {};
    V4 t[8] = {};
    int a0[4] = {0, 0, 0, 0};
    bool p0[4] = {false, false, false, false};
    V4 o[12] = {};
    V4 v3[10] = {};
    V4 oDepth = {};
    float pad[2] = {0, 0};
    int aL = 0;

    std::map<int, V4> fdef;
    std::map<int, std::array<int, 4>> idef;
    std::map<int, bool> bdef;
    int stype[16] = {0};
    bool shadow[16] = {false};
    std::map<int, std::pair<int, int>> vs3_map;   // o# -> (usage, index)
    int ps3_vmax = 0;

    const VSIn *vin = nullptr;
    VSOut *vout = nullptr;
    const PSIn *pin = nullptr;
    PSOut *pout = nullptr;

    Machine(const Shader &s, const Context &c) : sh(s), ctx(c) {
        ps = s.ps;
        ps1 = ps && s.major == 1;
        ps14 = ps1 && s.minor >= 4;
        vs3 = !ps && s.major >= 3;
        ps3 = ps && s.major >= 3;
    }

    bool fail(const std::string &m) { if (err.empty()) err = m; return false; }

    float mulf(float a, float b) const {
        if (ctx.opt.ieee_mul) return a * b;
        return (a == 0.0f || b == 0.0f) ? 0.0f : a * b;
    }
    float dp(const V4 &a, const V4 &b, int n) const {
        float s = 0, m = 0;
        for (int i = 0; i < n; i++) { s += mulf(a[i], b[i]); m += std::fabs(mulf(a[i], b[i])); }
        // nudged reference: rounding error of a dot product scales with sum |a_i b_i|
        if (g_ulp_nudge && std::isfinite(m)) s += (float)g_ulp_nudge * m * 0x1p-20f;
        return s;
    }
    static V4 splat(float f) { return {f, f, f, f}; }

    int rel_value(uint32_t rel_type, uint8_t comp) const { return rel_type == R_LOOP ? aL : a0[comp]; }

    V4 const_read(int idx) const {
        auto it = fdef.find(idx);
        if (it != fdef.end()) return it->second;
        if (idx < 0 || idx >= 256) return {0, 0, 0, 0};
        return {ctx.c[idx * 4], ctx.c[idx * 4 + 1], ctx.c[idx * 4 + 2], ctx.c[idx * 4 + 3]};
    }

    static V4 sat4(V4 v) { for (float &x : v) x = std::min(std::max(x, 0.0f), 1.0f); return v; }

    V4 base(const Src &s) {
        switch (s.type) {
        case R_TEMP: return r[s.num];
        case R_INPUT:
            if (!ps) {
                int i = s.num + (s.rel ? rel_value(s.rel_type, s.rel_comp) : 0);
                return vin->v[std::min(std::max(i, 0), 15)];
            }
            if (ps3) {
                int i = s.num + (s.rel ? rel_value(s.rel_type, s.rel_comp) : 0);
                return v3[std::min(std::max(i, 0), std::max(ps3_vmax - 1, 0))];
            }
            return sat4(pin->col[s.num & 1]);
        case R_CONST: case R_CONST2: case R_CONST3: case R_CONST4: {
            int i = (int)const_index(s.type, s.num) + (s.rel ? rel_value(s.rel_type, s.rel_comp) : 0);
            V4 v = const_read(i);
            if (ps1 && ctx.opt.ps1_clamp_consts)
                for (float &x : v) x = std::min(std::max(x, -1.0f), 1.0f);
            return v;
        }
        case R_ADDR:
            if (ps) return (ps1 && !ps14) ? t[s.num] : pin->tex[s.num];
            return {(float)a0[0], (float)a0[1], (float)a0[2], (float)a0[3]};
        case R_MISC: return s.num == 0 ? pin->vpos : splat(pin->face);
        case R_CONSTINT: {
            auto v = int_reg(s.num);
            return {(float)v[0], (float)v[1], (float)v[2], (float)v[3]};
        }
        case R_CONSTBOOL: return splat(bool_reg(s.num) ? 1.0f : 0.0f);
        case R_LOOP: return splat((float)aL);
        case R_PREDICATE: return {p0[0] ? 1.f : 0.f, p0[1] ? 1.f : 0.f, p0[2] ? 1.f : 0.f, p0[3] ? 1.f : 0.f};
        default: fail("bad src type"); return {0, 0, 0, 0};
        }
    }

    V4 src(const Src &s, bool mods = true) {
        V4 b = base(s), v;
        for (int i = 0; i < 4; i++) v[i] = b[s.swz[i]];
        if (!mods) return v;
        for (float &x : v) {
            switch (s.mod) {
            case SM_NONE: case SM_DZ: case SM_DW: break;
            case SM_NEG: x = -x; break;
            case SM_BIAS: x = x - 0.5f; break;
            case SM_BIASNEG: x = 0.5f - x; break;
            case SM_SIGN: x = 2.0f * x - 1.0f; break;
            case SM_SIGNNEG: x = 1.0f - 2.0f * x; break;
            case SM_COMP: x = 1.0f - x; break;
            case SM_X2: x = 2.0f * x; break;
            case SM_X2NEG: x = -2.0f * x; break;
            case SM_ABS: x = std::fabs(x); break;
            case SM_ABSNEG: x = -std::fabs(x); break;
            default: fail("bad modifier");
            }
        }
        return v;
    }

    std::array<int, 4> int_reg(uint32_t n) const {
        auto it = idef.find(n);
        if (it != idef.end()) return it->second;
        return {ctx.ci[n * 4], ctx.ci[n * 4 + 1], ctx.ci[n * 4 + 2], ctx.ci[n * 4 + 3]};
    }
    bool bool_reg(uint32_t n) const {
        auto it = bdef.find(n);
        if (it != bdef.end()) return it->second;
        return ctx.cb[n] != 0;
    }

    // D3D9 scalar functions
    static float rcp(float x) { return x == 0.0f ? INF : nudge(1.0f / x); }
    static float rsq(float x) { float a = std::fabs(x); return a == 0.0f ? INF : nudge(1.0f / std::sqrt(a)); }
    static float lg(float x) { float a = std::fabs(x); return a == 0.0f ? -INF : nudge(std::log2(a)); }
    static float pw(float x, float y) {
        float a = std::fabs(x);
        if (y == 0.0f) return 1.0f;
        if (a == 0.0f) return y > 0.0f ? 0.0f : INF;
        return nudge(std::exp2(y * std::log2(a)));
    }

    V4 do_sample(int s, V4 c, int mode /*0 plain, 1 /w, 5 /z*/) {
        const Texture *tx = (s >= 0 && s < 16) ? ctx.tex[s] : nullptr;
        if (!tx) { fail("missing texture"); return {0, 0, 0, 0}; }
        if (shadow[s]) {
            float u = c[0], v = c[1], ref = c[2];
            if (mode == 1) { u /= c[3]; v /= c[3]; ref /= c[3]; }
            if (mode == 5) { u /= c[2]; v /= c[2]; }
            return splat(tx->compare(u, v, ref));
        }
        float cc[3] = {c[0], c[1], c[2]};
        if (mode == 1) for (float &x : cc) x /= c[3];
        if (mode == 5) { cc[0] /= c[2]; cc[1] /= c[2]; }
        return tx->sample(cc);
    }

    // result of one instruction, computed before any write (co-issue)
    struct Pending {
        const Instr *in;
        V4 val;
        bool bval[4];
        bool is_bool = false, has = false;
    };

    bool compute(size_t idx, Pending &p) {
        const Instr &in = sh.code[idx];
        p.in = &in;
        p.has = true;
        auto S = [&](int k) -> V4 {
            if (k >= (int)in.src.size()) { fail("missing operand"); return {0, 0, 0, 0}; }
            return src(in.src[k]);
        };
        V4 &d = p.val;
        switch (in.op) {
        case OP_NOP: case OP_PHASE: p.has = false; return true;
        case OP_MOV: case OP_MOVA: d = S(0); break;
        case OP_ADD: { V4 a = S(0), b = S(1); for (int i = 0; i < 4; i++) d[i] = a[i] + b[i]; break; }
        case OP_SUB: { V4 a = S(0), b = S(1); for (int i = 0; i < 4; i++) d[i] = a[i] - b[i]; break; }
        case OP_MUL: { V4 a = S(0), b = S(1); for (int i = 0; i < 4; i++) d[i] = mulf(a[i], b[i]); break; }
        case OP_MAD: { V4 a = S(0), b = S(1), c = S(2); for (int i = 0; i < 4; i++) d[i] = mulf(a[i], b[i]) + c[i]; break; }
        case OP_RCP: d = splat(rcp(S(0)[3])); break;
        case OP_RSQ: d = splat(rsq(S(0)[3])); break;
        case OP_EXP: d = splat(nudge(std::exp2(S(0)[3]))); break;
        case OP_LOG: d = splat(lg(S(0)[3])); break;
        case OP_EXPP: {
            float x = S(0)[3];
            if (sh.major < 2) { float f = std::floor(x); d = {std::exp2(f), x - f, std::exp2(x), 1.0f}; }
            else d = splat(std::exp2(x));
            break;
        }
        case OP_LOGP: {
            float x = S(0)[3];
            if (sh.major < 2) {
                float a = std::fabs(x);
                if (a == 0.0f) d = {-INF, 1.0f, -INF, 1.0f};
                else { float e = std::floor(std::log2(a)); d = {e, a / std::exp2(e), std::log2(a), 1.0f}; }
            } else d = splat(lg(x));
            break;
        }
        case OP_FRC: { V4 a = S(0); for (int i = 0; i < 4; i++) { d[i] = a[i] - std::floor(a[i]); if (d[i] >= 1.0f) d[i] = 0x1.fffffep-1f; } break; }
        case OP_DP3: d = splat(dp(S(0), S(1), 3)); break;
        case OP_DP4: d = splat(dp(S(0), S(1), 4)); break;
        case OP_DP2ADD: { V4 a = S(0), b = S(1), c = S(2); d = splat(mulf(a[0], b[0]) + mulf(a[1], b[1]) + c[0]); break; }
        case OP_MIN: { V4 a = S(0), b = S(1); for (int i = 0; i < 4; i++) d[i] = a[i] < b[i] ? a[i] : b[i]; break; }
        case OP_MAX: { V4 a = S(0), b = S(1); for (int i = 0; i < 4; i++) d[i] = a[i] >= b[i] ? a[i] : b[i]; break; }
        case OP_SLT: { V4 a = S(0), b = S(1); for (int i = 0; i < 4; i++) d[i] = a[i] < b[i] ? 1.0f : 0.0f; break; }
        case OP_SGE: { V4 a = S(0), b = S(1); for (int i = 0; i < 4; i++) d[i] = a[i] >= b[i] ? 1.0f : 0.0f; break; }
        case OP_LIT: {
            V4 a = S(0);
            d = {1.0f, 0.0f, 0.0f, 1.0f};
            float pwr = std::min(std::max(a[3], -128.0f), 128.0f);
            if (a[0] > 0.0f) { d[1] = a[0]; if (a[1] > 0.0f) d[2] = pw(a[1], pwr); }
            break;
        }
        case OP_DST: { V4 a = S(0), b = S(1); d = {1.0f, mulf(a[1], b[1]), a[2], b[3]}; break; }
        case OP_LRP: { V4 a = S(0), b = S(1), c = S(2); for (int i = 0; i < 4; i++) d[i] = c[i] + a[i] * (b[i] - c[i]); break; }
        case OP_POW: d = splat(pw(S(0)[3], S(1)[3])); break;
        case OP_CRS: { V4 a = S(0), b = S(1); d = {a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0], 0.0f}; break; }
        case OP_SGN: { V4 a = S(0); for (int i = 0; i < 4; i++) d[i] = a[i] > 0 ? 1.0f : a[i] < 0 ? -1.0f : 0.0f; break; }
        case OP_ABS: { V4 a = S(0); for (int i = 0; i < 4; i++) d[i] = std::fabs(a[i]); break; }
        case OP_NRM: { V4 a = S(0); float f = rsq(dp(a, a, 3)); for (int i = 0; i < 4; i++) d[i] = mulf(a[i], f); break; }
        case OP_SINCOS: { float x = S(0)[3]; d = {std::cos(x), std::sin(x), 0.0f, 0.0f}; break; }
        case OP_CMP: { V4 a = S(0), b = S(1), c = S(2); for (int i = 0; i < 4; i++) d[i] = a[i] >= 0.0f ? b[i] : c[i]; break; }
        case OP_CND: {
            V4 a = S(0), b = S(1), c = S(2);
            for (int i = 0; i < 4; i++) d[i] = (ps14 ? a[i] : a[3]) > 0.5f ? b[i] : c[i];
            break;
        }
        case OP_M4x4: case OP_M4x3: case OP_M3x4: case OP_M3x3: case OP_M3x2: {
            int rows = in.op == OP_M4x4 || in.op == OP_M3x4 ? 4 : in.op == OP_M3x2 ? 2 : 3;
            int n = in.op == OP_M4x4 || in.op == OP_M4x3 ? 4 : 3;
            V4 a = S(0);
            d = {0, 0, 0, 0};
            for (int k = 0; k < rows; k++) {
                Src m = in.src[1];
                m.num += k;
                d[k] = dp(a, src(m), n);
            }
            break;
        }
        case OP_SETP: {
            V4 a = S(0), b = S(1);
            p.is_bool = true;
            for (int i = 0; i < 4; i++) {
                float x = a[i], y = b[i];
                bool c = false;
                switch (in.ctrl & 7) {
                case 1: c = x > y; break; case 2: c = x == y; break; case 3: c = x >= y; break;
                case 4: c = x < y; break; case 5: c = x != y; break; case 6: c = x <= y; break;
                }
                p.bval[i] = c;
            }
            break;
        }
        case OP_BEM: {
            V4 a = S(0), b = S(1);
            const float *m = ctx.aux.bumpenv[in.dst.num & 7];
            d = {a[0] + m[0] * b[0] + m[2] * b[1], a[1] + m[1] * b[0] + m[3] * b[1], 0, 0};
            break;
        }
        case OP_TEX: {
            if (!ps) { V4 c = S(0); d = do_sample(in.src[1].num, c, 0); break; }
            if (ps1 && !ps14) {
                int n = in.dst.num;
                int pj = n < 8 ? ctx.opt.tex_projected[n] : 0;
                d = do_sample(n, pin->tex[n], pj == 4 ? 1 : pj == 3 ? 5 : 0);
                break;
            }
            if (ps14) {
                const Src &c = in.src[0];
                V4 cv = src(c, false);
                d = do_sample(in.dst.num, cv, c.mod == SM_DW ? 1 : c.mod == SM_DZ ? 5 : 0);
                break;
            }
            V4 c = S(0);
            int s = in.src[1].num;
            V4 raw = do_sample(s, c, (in.ctrl & 3) == 1 ? 1 : 0);   // bias does not matter with 1 mip
            for (int i = 0; i < 4; i++) d[i] = raw[in.src[1].swz[i]];
            break;
        }
        case OP_TEXLDL: d = do_sample(in.src[1].num, S(0), 0); break;
        case OP_TEXLDD: d = do_sample(in.src[1].num, S(0), 0); break;
        case OP_TEXCOORD:
            if (ps14) {
                const Src &c = in.src[0];
                V4 e = src(c, false);
                if (c.mod == SM_DW) d = {e[0] / e[3], e[1] / e[3], e[2] / e[3], 1.0f};
                else if (c.mod == SM_DZ) d = {e[0] / e[2], e[1] / e[2], 1.0f, 1.0f};
                else d = e;
            } else {
                V4 tc = sat4(pin->tex[in.dst.num]);
                d = {tc[0], tc[1], tc[2], 1.0f};
            }
            break;
        case OP_TEXKILL: {
            V4 e;
            int mask;
            if (ps1 && !ps14) { e = pin->tex[in.dst.num]; mask = 7; }
            else {
                Src t;
                t.type = in.dst.type;
                t.num = in.dst.num;
                e = base(t);
                mask = sh.major < 2 ? 7 : in.dst.mask;
            }
            for (int i = 0; i < 4; i++) if ((mask >> i) & 1 && e[i] < 0.0f) pout->killed = true;
            p.has = false;
            return true;
        }
        case OP_TEXBEM: case OP_TEXBEML: {
            int n = in.dst.num;
            V4 s0 = S(0), tc = pin->tex[n];
            const float *m = ctx.aux.bumpenv[n & 7];
            V4 uv = {tc[0] + m[0] * s0[0] + m[2] * s0[1], tc[1] + m[1] * s0[0] + m[3] * s0[1], 0, 1};
            d = do_sample(n, uv, 0);
            if (in.op == OP_TEXBEML) {
                float l = s0[2] * ctx.aux.bumplum[n & 7][0] + ctx.aux.bumplum[n & 7][1];
                l = std::min(std::max(l, 0.0f), 1.0f);
                for (float &x : d) x *= l;
            }
            break;
        }
        case OP_TEXREG2AR: { V4 a = S(0); d = do_sample(in.dst.num, {a[3], a[0], 0, 1}, 0); break; }
        case OP_TEXREG2GB: { V4 a = S(0); d = do_sample(in.dst.num, {a[1], a[2], 0, 1}, 0); break; }
        case OP_TEXREG2RGB: { V4 a = S(0); d = do_sample(in.dst.num, {a[0], a[1], a[2], 1}, 0); break; }
        case OP_TEXM3x2PAD: case OP_TEXM3x3PAD: {
            int row = (in.op == OP_TEXM3x3PAD && idx > 0 && sh.code[idx - 1].op == OP_TEXM3x3PAD) ? 1 : 0;
            pad[row] = dp(pin->tex[in.dst.num], S(0), 3);
            p.has = false;
            return true;
        }
        case OP_TEXM3x2TEX: case OP_TEXDP3TEX: case OP_TEXDP3: case OP_TEXM3x2DEPTH: {
            float x = dp(pin->tex[in.dst.num], S(0), 3);
            if (in.op == OP_TEXM3x2TEX) d = do_sample(in.dst.num, {pad[0], x, 0, 1}, 0);
            else if (in.op == OP_TEXDP3TEX) d = do_sample(in.dst.num, {x, 0, 0, 1}, 0);
            else if (in.op == OP_TEXDP3) d = splat(x);
            else { oDepth = splat(x == 0.0f ? 1.0f : pad[0] / x); p.has = false; return true; }
            break;
        }
        case OP_TEXM3x3TEX: case OP_TEXM3x3: case OP_TEXM3x3SPEC: case OP_TEXM3x3VSPEC: {
            int n = in.dst.num;
            V4 nv = {pad[0], pad[1], dp(pin->tex[n], S(0), 3), 1.0f};
            if (in.op == OP_TEXM3x3) { d = nv; break; }
            if (in.op == OP_TEXM3x3TEX) { d = do_sample(n, nv, 0); break; }
            V4 e = in.op == OP_TEXM3x3SPEC ? S(1)
                 : V4{pin->tex[std::max(n - 2, 0)][3], pin->tex[std::max(n - 1, 0)][3], pin->tex[n][3], 0};
            float ne = nv[0] * e[0] + nv[1] * e[1] + nv[2] * e[2];
            float nn = nv[0] * nv[0] + nv[1] * nv[1] + nv[2] * nv[2];
            V4 rf = {2 * nv[0] * ne / nn - e[0], 2 * nv[1] * ne / nn - e[1], 2 * nv[2] * ne / nn - e[2], 1};
            d = do_sample(n, rf, 0);
            break;
        }
        case OP_TEXDEPTH: {
            V4 v = r[in.dst.num];
            oDepth = splat(v[1] == 0.0f ? 1.0f : v[0] / v[1]);
            p.has = false;
            return true;
        }
        default:
            return fail(std::string("interp: unsupported op ") + op_name(in.op));
        }
        return err.empty();
    }

    bool write(const Pending &p) {
        if (!p.has) return true;
        const Instr &in = *p.in;
        const Dst &dd = in.dst;
        V4 v = p.val;
        bool sat = dd.sat;
        if (!p.is_bool) {
            float sc = 1.0f;
            switch (dd.shift) {
            case 1: sc = 2; break; case 2: sc = 4; break; case 3: sc = 8; break;
            case -1: sc = 0.5f; break; case -2: sc = 0.25f; break; case -3: sc = 0.125f; break;
            }
            for (float &x : v) x *= sc;
        }
        V4 *target = nullptr;
        switch (dd.type) {
        case R_TEMP: target = &r[dd.num]; break;
        case R_ADDR:
            if (ps) target = &t[dd.num];
            else {
                for (int i = 0; i < 4; i++)
                    if (dd.mask & (1 << i)) a0[i] = (int)std::floor(v[i] + (sh.major >= 2 ? 0.5f : 0.0f));
                return true;
            }
            break;
        case R_RASTOUT: target = dd.num == 0 ? &vout->pos : dd.num == 1 ? &vout->fog : &vout->psize; break;
        case R_ATTROUT: target = &vout->col[dd.num]; if (sh.major < 3) sat = true; break;
        case R_TEXCRDOUT:
            if (vs3) {
                int i = dd.num + (dd.rel ? rel_value(dd.rel_type, dd.rel_comp) : 0);
                target = &o[std::min(std::max(i, 0), 11)];
            } else target = &vout->tex[dd.num];
            break;
        case R_COLOROUT: target = &pout->oc[dd.num]; break;
        case R_DEPTHOUT: target = &oDepth; break;
        case R_PREDICATE:
            for (int i = 0; i < 4; i++)
                if (dd.mask & (1 << i)) {
                    bool en = true;
                    if (in.predicated) { en = p0[in.pred.swz[i]]; if (in.pred.mod == SM_NOT) en = !en; }
                    if (en) p0[i] = p.bval[i];
                }
            return true;
        default: return fail("bad dst type");
        }
        if (sat) v = sat4(v);
        bool pm[4] = {true, true, true, true};
        if (in.predicated)
            for (int i = 0; i < 4; i++) { pm[i] = p0[in.pred.swz[i]]; if (in.pred.mod == SM_NOT) pm[i] = !pm[i]; }
        for (int i = 0; i < 4; i++)
            if ((dd.mask & (1 << i)) && pm[i]) (*target)[i] = v[i];
        return true;
    }

    float scalar(const Src &s) { return src(s)[3]; }
    bool cmp(uint32_t c, float x, float y) {
        switch (c & 7) {
        case 1: return x > y; case 2: return x == y; case 3: return x >= y;
        case 4: return x < y; case 5: return x != y; case 6: return x <= y;
        }
        return false;
    }

    bool run() {
        for (const Instr &in : sh.code) {
            if (in.op == OP_DEF) { V4 v; memcpy(v.data(), in.defv, 16); fdef[const_index(in.dst.type, in.dst.num)] = v; }
            if (in.op == OP_DEFI) idef[in.dst.num] = {(int)in.defv[0], (int)in.defv[1], (int)in.defv[2], (int)in.defv[3]};
            if (in.op == OP_DEFB) bdef[in.dst.num] = in.defv[0] != 0;
            if (in.op == OP_DCL && vs3 && in.dst.type == R_TEXCRDOUT) vs3_map[in.dst.num] = {(int)in.usage, (int)in.usage_index};
            if (in.op == OP_DCL && ps3 && in.dst.type == R_INPUT) {
                ps3_vmax = std::max(ps3_vmax, (int)in.dst.num + 1);
                const V4 *src = nullptr;
                if (in.usage == SM_USAGE_COLOR) src = &pin->col[in.usage_index & 1];
                else if (in.usage == SM_USAGE_TEXCOORD) src = &pin->tex[in.usage_index & 7];
                else if (in.usage == SM_USAGE_FOG) src = &pin->fog;
                if (src) v3[in.dst.num] = *src;
            }
        }
        for (int s = 0; s < 16; s++) shadow[s] = (ctx.opt.shadow_samplers >> s) & 1 && ctx.tex[s] && ctx.tex[s]->depth;
        // structured control flow: precompute matching instructions
        size_t n = sh.code.size();
        std::vector<size_t> match(n, 0), stack;
        for (size_t i = 0; i < n; i++) {
            uint32_t op = sh.code[i].op;
            if (op == OP_REP || op == OP_LOOP || op == OP_IF || op == OP_IFC) stack.push_back(i);
            else if (op == OP_ELSE) { match[stack.back()] = i; stack.back() = i; }
            else if (op == OP_ENDREP || op == OP_ENDLOOP || op == OP_ENDIF) {
                if (stack.empty()) return fail("unbalanced");
                size_t b = stack.back();
                stack.pop_back();
                match[b] = i;
                match[i] = b;
            }
        }
        struct Loop { size_t begin; int count; int saved_aL; int step; bool is_loop; };
        std::vector<Loop> loops;
        // find loop start of a REP/LOOP from its end: match[end]
        for (size_t pc = 0; pc < n;) {
            const Instr &in = sh.code[pc];
            switch (in.op) {
            case OP_DCL: case OP_DEF: case OP_DEFI: case OP_DEFB: case OP_RET: pc++; continue;
            case OP_REP: case OP_LOOP: {
                auto iv = int_reg(in.src[in.op == OP_REP ? 0 : 1].num);
                Loop l{pc, iv[0], aL, iv[2], in.op == OP_LOOP};
                if (l.count <= 0) { pc = match[pc] + 1; continue; }
                if (l.is_loop) aL = iv[1];
                loops.push_back(l);
                pc++;
                continue;
            }
            case OP_ENDREP: case OP_ENDLOOP: {
                Loop &l = loops.back();
                if (--l.count > 0) { if (l.is_loop) aL += l.step; pc = l.begin + 1; continue; }
                if (l.is_loop) aL = l.saved_aL;
                loops.pop_back();
                pc++;
                continue;
            }
            case OP_IF: case OP_IFC: {
                bool c;
                if (in.op == OP_IFC) c = cmp(in.ctrl, scalar(in.src[0]), scalar(in.src[1]));
                else if (in.src[0].type == R_PREDICATE) { c = p0[in.src[0].swz[0]]; if (in.src[0].mod == SM_NOT) c = !c; }
                else c = bool_reg(in.src[0].num);
                if (c) pc++;
                else pc = match[pc] + 1;   // else+1 or endif+1
                continue;
            }
            case OP_ELSE: pc = match[pc] + 1; continue;   // end of the taken branch
            case OP_ENDIF: pc++; continue;
            case OP_BREAK: case OP_BREAKC: case OP_BREAKP: {
                bool c = true;
                if (in.op == OP_BREAKC) c = cmp(in.ctrl, scalar(in.src[0]), scalar(in.src[1]));
                if (in.op == OP_BREAKP) { c = p0[in.src[0].swz[0]]; if (in.src[0].mod == SM_NOT) c = !c; }
                if (!c) { pc++; continue; }
                Loop l = loops.back();
                loops.pop_back();
                if (l.is_loop) aL = l.saved_aL;
                pc = match[l.begin] + 1;
                continue;
            }
            default: break;
            }
            // instruction group (co-issue)
            size_t j = pc;
            while (j + 1 < n && sh.code[j + 1].coissue) j++;
            std::vector<Pending> ps_(j - pc + 1);
            for (size_t k = pc; k <= j; k++)
                if (!compute(k, ps_[k - pc])) return false;
            for (auto &p : ps_)
                if (!write(p)) return false;
            if (g_trace)
                for (auto &p : ps_) {
                    if (!p.has) continue;
                    Shader one; one.ps = sh.ps; one.major = sh.major; one.minor = sh.minor; one.code.push_back(*p.in);
                    std::string d = disassemble(one); d = d.substr(d.find('\n') + 1); d.pop_back();
                    printf("  %-40s = %.9g %.9g %.9g %.9g\n", d.c_str(), p.val[0], p.val[1], p.val[2], p.val[3]);
                }
            pc = j + 1;
        }
        return err.empty();
    }
};

}  // namespace

bool run_vs(const Shader &s, const Context &ctx, const VSIn &in, VSOut &out, std::string *err) {
    Machine m(s, ctx);
    m.vin = &in;
    m.vout = &out;
    out = VSOut();
    out.fog = {1, 0, 0, 0};
    out.psize = {1, 0, 0, 0};
    bool ok = m.run();
    if (ok && m.vs3) {
        for (auto &kv : m.vs3_map) {
            int u = kv.second.first, ix = kv.second.second;
            const V4 &v = m.o[kv.first];
            if (u == SM_USAGE_POSITION) out.pos = v;
            else if (u == SM_USAGE_COLOR) out.col[ix & 1] = v;
            else if (u == SM_USAGE_TEXCOORD) out.tex[ix & 7] = v;
            else if (u == SM_USAGE_FOG) out.fog = v;
            else if (u == SM_USAGE_PSIZE) out.psize = v;
        }
    }
    if (!ok && err) *err = m.err;
    return ok;
}

bool run_ps(const Shader &s, const Context &ctx, const PSIn &in, PSOut &out, std::string *err) {
    Machine m(s, ctx);
    m.pin = &in;
    m.pout = &out;
    out = PSOut();
    m.oDepth = {in.vpos[2], in.vpos[2], in.vpos[2], in.vpos[2]};
    bool ok = m.run();
    if (ok && m.ps1) out.oc[0] = m.r[0];
    bool writes_depth = false;
    for (const Instr &i : s.code)
        if ((i.has_dst && i.dst.type == R_DEPTHOUT) || i.op == OP_TEXDEPTH || i.op == OP_TEXM3x2DEPTH) writes_depth = true;
    out.depth = writes_depth ? m.oDepth[0] : 0.0f;
    if (!ok && err) *err = m.err;
    return ok;
}

}  // namespace interp
