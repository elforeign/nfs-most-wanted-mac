// D3D9 IR -> HLSL. Subset: ordinary and masked front-end SM1.1 pairs.
#include "sm_ir.h"
#include <stddef.h>
#include <stdarg.h>
extern "C" int vsnprintf(char *, size_t, const char *, va_list);

namespace sm {
namespace {
void append(std::string &out, const char *format, ...) {
    char text[512];
    va_list args;
    va_start(args, format);
    vsnprintf(text, sizeof text, format, args);
    va_end(args);
    out += text;
}
bool reg(const Shader &s, unsigned type, unsigned num, bool dst, std::string &out) {
    if (type == R_TEMP && num < (s.ps ? 2u : 12u)) append(out, "r%u", num);
    else if (type == R_INPUT && num < (s.ps ? 1u : 4u)) append(out, "v%u", num);
    else if (!s.ps && type == R_CONST && num < 256 && !dst) append(out, "c[%u]", num);
    else if (s.ps && type == R_ADDR && num < 2) append(out, "t%u", num);
    else if (!s.ps && dst && type == R_RASTOUT && num == 0) out += "o.position";
    else if (!s.ps && dst && type == R_ATTROUT && num == 0) out += "o.color";
    else if (!s.ps && dst && type == R_TEXCRDOUT && num < 2) { out += "o.texcoord"; if (num) append(out, "%u", num); }
    else return false;
    return !(dst && type == R_INPUT);
}
bool src(const Shader &s, const Src &r, std::string &out) {
    if (r.rel || (r.mod != SM_NONE && r.mod != SM_NEG)) return false;
    if (r.mod == SM_NEG) out += "-";
    if (!reg(s, r.type, r.num, false, out)) return false;
    out += ".";
    for (auto c : r.swz) { if (c > 3) return false; char a[] = {"xyzw"[c], 0}; out += a; }
    return true;
}
}
bool emit_hlsl(const Shader &s, std::string &source, std::string &error) {
    source.clear(); error.clear();
    auto fail = [&](const char *why) { source.clear(); error = why; return false; };
    if (!s.error.empty() || s.major != 1 || s.minor != 1 || s.code.empty())
        return fail("HLSL backend requires decoded SM1.1");
    // Derive the interface before emitting executable instructions. Only the
    // POSITION0/COLOR0/TEXCOORD0..1 surface used by these SM1.1 pairs is supported.
    const char *fields[4] = {};
    unsigned declarations = 0, semantics = 0, textures = 0, texcoord[2] = {}, position = 0, color = 0;
    bool executable = false;
    for (const auto &i : s.code) {
        if (i.op == OP_DCL) {
            unsigned semantic = i.usage == 0 && !i.usage_index ? 0 :
                i.usage == 10 && !i.usage_index ? 1 :
                i.usage == 5 && i.usage_index < 2 ? 2 + i.usage_index : 4;
            if (s.ps || executable || !i.has_dst || i.dst.type != R_INPUT || i.dst.num >= 4 ||
                i.dst.mask != 15 || i.dst.rel || i.dst.sat || i.dst.shift || i.dst.pp || i.dst.centroid ||
                i.predicated || i.coissue || i.ctrl || i.tex_type || !i.src.empty() || semantic == 4 ||
                (declarations & (1u << i.dst.num)) || (semantics & (1u << semantic)))
                return fail("unsupported HLSL input declaration");
            const char *names[] = {"position", "color", "texcoord", "texcoord1"};
            fields[i.dst.num] = names[semantic];
            declarations |= 1u << i.dst.num; semantics |= 1u << semantic;
        } else if (i.op != OP_END && i.op != OP_NOP) {
            executable = true;
            if (!s.ps && i.has_dst && i.dst.type == R_TEXCRDOUT && i.dst.num < 2)
                textures |= 1u << i.dst.num;
            if (s.ps && i.op == OP_TEX && i.has_dst && i.dst.type == R_ADDR && i.dst.num < 2)
                textures |= 1u << i.dst.num;
        }
    }
    if (!(textures & 1) || (!s.ps && (semantics & 7) != 7))
        return fail("incomplete HLSL SM1.1 interface");
    std::string out = "// Generated from D3D9 IR; entry sm_main.\n"
        "float sm_mulf(float a, float b) { return (a == 0 || b == 0) ? 0 : a * b; }\n"
        "float4 sm_mul(float4 a, float4 b) { return float4(sm_mulf(a.x,b.x), sm_mulf(a.y,b.y), sm_mulf(a.z,b.z), sm_mulf(a.w,b.w)); }\n"
        "float4 sm_dp4(float4 a, float4 b) { float4 m = sm_mul(a,b); return (m.x + m.y + m.z + m.w).xxxx; }\n"
        "struct Varying { float4 position : SV_Position; float4 color : COLOR0; float4 texcoord : TEXCOORD0;";
    if (textures & 2) out += " float4 texcoord1 : TEXCOORD1;";
    out += " };\n";
    if (s.ps) {
        for (unsigned t = 0; t < 2; ++t) if (textures & (1u << t))
            append(out, "Texture2D<float4> tex%u : register(t%u);\nSamplerState samp%u : register(s%u);\n", t,t,t,t);
        out += "float4 sm_main(Varying input) : SV_Target0 {\n"
            "  float4 v0 = input.color, v1 = 0, t0 = input.texcoord;\n";
        if (textures & 2) out += "  float4 t1 = input.texcoord1;\n";
        out += "  float4 r0 = 0, r1 = 0;\n";
    } else {
        out += "cbuffer VSConstants : register(b0) { float4 c[256]; };\nstruct VSInput {";
        const char *semantic_names[] = {"POSITION0", "COLOR0", "TEXCOORD0", "TEXCOORD1"};
        for (const auto &i : s.code) if (i.op == OP_DCL) {
            unsigned semantic = i.usage == 0 ? 0 : i.usage == 10 ? 1 : 2 + i.usage_index;
            append(out, " float4 %s : %s;", fields[i.dst.num], semantic_names[semantic]);
        }
        out += " };\nVarying sm_main(VSInput input) {\n  Varying o = (Varying)0;\n";
        for (unsigned n = 0; n < 4; ++n) if (fields[n])
            append(out, "  float4 v%u = input.%s;\n", n, fields[n]);
        out += "  float4 r0=0,r1=0,r2=0,r3=0,r4=0,r5=0,r6=0,r7=0,r8=0,r9=0,r10=0,r11=0;\n";
    }
    unsigned index = 0, sampled = 0, pixel_output = 0;
    const Instr *prev = nullptr;
    for (const auto &i : s.code) {
        if (i.op == OP_END || i.op == OP_NOP) continue;
        if (i.op == OP_DCL) continue;
        if (!i.has_dst || i.predicated || i.ctrl || i.dst.rel || i.dst.shift || i.dst.pp || i.dst.centroid ||
            !i.dst.mask || i.dst.mask > 15) return fail("unsupported HLSL instruction modifier");
        std::string target;
        if (!reg(s, i.dst.type, i.dst.num, true, target)) return fail("unsupported HLSL destination");
        // Coissued SM1 instructions read the state before their partner. Sequential emission
        // is safe only when their source components do not overlap the partner's writes.
        if (i.coissue) {
            if (!s.ps || !prev || prev->coissue ||
                (prev->op != OP_MOV && prev->op != OP_ADD && prev->op != OP_MUL) ||
                (i.op != OP_MOV && i.op != OP_MUL)) return fail("unsupported coissue group");
            for (const auto &r : i.src) for (unsigned lane = 0; lane < 4; ++lane)
                if (r.swz[lane] > 3) return fail("unsupported HLSL source");
            for (const auto &r : i.src) if (r.type == prev->dst.type && r.num == prev->dst.num)
                for (unsigned lane = 0; lane < 4; ++lane)
                    if ((i.dst.mask & (1u << lane)) && (prev->dst.mask & (1u << r.swz[lane])))
                        return fail("coissue reads modified component");
            if (i.dst.type == prev->dst.type && i.dst.num == prev->dst.num && (i.dst.mask & prev->dst.mask))
                return fail("coissue writes overlap");
        }
        unsigned arity = i.op == OP_MOV ? 1 : (i.op == OP_ADD || i.op == OP_MUL || i.op == OP_DP4) ? 2 : 0;
        std::string value, args[2];
        if (i.op == OP_TEX) {
            if (!s.ps || i.dst.type != R_ADDR || i.dst.num >= 2 || i.dst.mask != 15 || !i.src.empty() || i.coissue)
                return fail("unsupported HLSL texture instruction");
            append(value, "tex%u.Sample(samp%u, t%u.xy)", i.dst.num, i.dst.num, i.dst.num); sampled |= 1u << i.dst.num;
        } else {
            if (!arity || i.src.size() != arity) return fail("unsupported HLSL opcode or arity");
            for (unsigned k = 0; k < arity; ++k)
                if (!src(s, i.src[k], args[k]) ||
                    (!s.ps && i.src[k].type == R_INPUT && !(declarations & (1u << i.src[k].num))) ||
                    (s.ps && i.src[k].type == R_ADDR && !(sampled & (1u << i.src[k].num))))
                    return fail("unsupported HLSL source");
            if (i.op == OP_MOV) value = args[0];
            else if (i.op == OP_ADD) { value = "("; value += args[0]; value += " + "; value += args[1]; value += ")"; }
            else { value = i.op == OP_DP4 ? "sm_dp4(" : "sm_mul("; value += args[0]; value += ", "; value += args[1]; value += ")"; }
        }
        bool clamp = i.dst.sat || (!s.ps && i.dst.type == R_ATTROUT);
        append(out, "  float4 d%u = %s", index, clamp ? "saturate(" : "");
        out += value;
        out += clamp ? ");\n  " : ";\n  ";
        out += target; out += ".";
        std::string mask;
        for (unsigned k = 0; k < 4; ++k) if (i.dst.mask & (1u << k)) { char a[] = {"xyzw"[k],0}; mask += a; }
        out += mask; append(out, " = d%u.", index++); out += mask; out += ";\n";
        if (!s.ps && i.dst.type == R_RASTOUT) position |= i.dst.mask;
        if (!s.ps && i.dst.type == R_ATTROUT) color |= i.dst.mask;
        if (!s.ps && i.dst.type == R_TEXCRDOUT) texcoord[i.dst.num] |= i.dst.mask;
        if (s.ps && i.dst.type == R_TEMP && i.dst.num == 0) pixel_output |= i.dst.mask;
        prev = &i;
    }
    if ((!s.ps && (position != 15 || color != 15 || (texcoord[0] & 3) != 3 || ((textures & 2) && (texcoord[1] & 3) != 3))) || (s.ps && (sampled != textures || pixel_output != 15)))
        return fail("incomplete HLSL SM1.1 interface");
    out += s.ps ? "  return saturate(r0);\n}\n" : "  return o;\n}\n";
    source = out;
    return true;
}
}
