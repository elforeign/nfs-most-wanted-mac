// sm_parse.cpp - D3D9 shader bytecode decoder, CTAB reader and disassembler.
//
// Token format (D3D9 shader bytecode, see the public "Direct3D shader codes" docs):
//   version   0xFFFEmmnn (vs) / 0xFFFFmmnn (ps)
//   opcode    bits 0..15 opcode, 16..23 control, 24..27 length (SM2+ only), 28 predicated,
//             30 co-issue (ps_1_x)
//   dst param bits 0..10 number, 11..12 + 28..30 register type, 13 relative, 16..19 mask,
//             20..23 result modifier (1 sat, 2 pp, 4 centroid), 24..27 shift
//   src param bits 0..10 number, 11..12 + 28..30 type, 13 relative, 16..23 swizzle,
//             24..27 source modifier
//   comment   0xFFFE + length << 16, end 0x0000FFFF
#include "sm_ir.h"
#include "sm_translate.h"

#include <cmath>
#include <cstdio>
#include <cstring>

namespace sm {

static uint32_t reg_type(uint32_t t) { return ((t >> 28) & 7) | ((t >> 8) & 0x18); }

struct OpInfo { const char *name; int dst; int nsrc; };

// nsrc is used only for SM1, where the opcode token carries no length.
static OpInfo op_info(uint32_t op) {
    switch (op) {
    case OP_NOP: return {"nop", 0, 0};      case OP_MOV: return {"mov", 1, 1};
    case OP_ADD: return {"add", 1, 2};      case OP_SUB: return {"sub", 1, 2};
    case OP_MAD: return {"mad", 1, 3};      case OP_MUL: return {"mul", 1, 2};
    case OP_RCP: return {"rcp", 1, 1};      case OP_RSQ: return {"rsq", 1, 1};
    case OP_DP3: return {"dp3", 1, 2};      case OP_DP4: return {"dp4", 1, 2};
    case OP_MIN: return {"min", 1, 2};      case OP_MAX: return {"max", 1, 2};
    case OP_SLT: return {"slt", 1, 2};      case OP_SGE: return {"sge", 1, 2};
    case OP_EXP: return {"exp", 1, 1};      case OP_LOG: return {"log", 1, 1};
    case OP_LIT: return {"lit", 1, 1};      case OP_DST: return {"dst", 1, 2};
    case OP_LRP: return {"lrp", 1, 3};      case OP_FRC: return {"frc", 1, 1};
    case OP_M4x4: return {"m4x4", 1, 2};    case OP_M4x3: return {"m4x3", 1, 2};
    case OP_M3x4: return {"m3x4", 1, 2};    case OP_M3x3: return {"m3x3", 1, 2};
    case OP_M3x2: return {"m3x2", 1, 2};    case OP_CALL: return {"call", 0, 1};
    case OP_CALLNZ: return {"callnz", 0, 2}; case OP_LOOP: return {"loop", 0, 2};
    case OP_RET: return {"ret", 0, 0};      case OP_ENDLOOP: return {"endloop", 0, 0};
    case OP_LABEL: return {"label", 0, 1};  case OP_DCL: return {"dcl", 0, 0};
    case OP_POW: return {"pow", 1, 2};      case OP_CRS: return {"crs", 1, 2};
    case OP_SGN: return {"sgn", 1, 3};      case OP_ABS: return {"abs", 1, 1};
    case OP_NRM: return {"nrm", 1, 1};      case OP_SINCOS: return {"sincos", 1, 3};
    case OP_REP: return {"rep", 0, 1};      case OP_ENDREP: return {"endrep", 0, 0};
    case OP_IF: return {"if", 0, 1};        case OP_IFC: return {"if", 0, 2};
    case OP_ELSE: return {"else", 0, 0};    case OP_ENDIF: return {"endif", 0, 0};
    case OP_BREAK: return {"break", 0, 0};  case OP_BREAKC: return {"break", 0, 2};
    case OP_MOVA: return {"mova", 1, 1};    case OP_DEFB: return {"defb", 0, 0};
    case OP_DEFI: return {"defi", 0, 0};
    case OP_TEXCOORD: return {"texcoord", 1, 0}; case OP_TEXKILL: return {"texkill", 1, 0};
    case OP_TEX: return {"tex", 1, 0};      case OP_TEXBEM: return {"texbem", 1, 1};
    case OP_TEXBEML: return {"texbeml", 1, 1}; case OP_TEXREG2AR: return {"texreg2ar", 1, 1};
    case OP_TEXREG2GB: return {"texreg2gb", 1, 1}; case OP_TEXM3x2PAD: return {"texm3x2pad", 1, 1};
    case OP_TEXM3x2TEX: return {"texm3x2tex", 1, 1}; case OP_TEXM3x3PAD: return {"texm3x3pad", 1, 1};
    case OP_TEXM3x3TEX: return {"texm3x3tex", 1, 1}; case OP_TEXM3x3SPEC: return {"texm3x3spec", 1, 2};
    case OP_TEXM3x3VSPEC: return {"texm3x3vspec", 1, 1}; case OP_EXPP: return {"expp", 1, 1};
    case OP_LOGP: return {"logp", 1, 1};    case OP_CND: return {"cnd", 1, 3};
    case OP_DEF: return {"def", 0, 0};      case OP_TEXREG2RGB: return {"texreg2rgb", 1, 1};
    case OP_TEXDP3TEX: return {"texdp3tex", 1, 1}; case OP_TEXM3x2DEPTH: return {"texm3x2depth", 1, 1};
    case OP_TEXDP3: return {"texdp3", 1, 1}; case OP_TEXM3x3: return {"texm3x3", 1, 1};
    case OP_TEXDEPTH: return {"texdepth", 1, 0}; case OP_CMP: return {"cmp", 1, 3};
    case OP_BEM: return {"bem", 1, 2};      case OP_DP2ADD: return {"dp2add", 1, 3};
    case OP_DSX: return {"dsx", 1, 1};      case OP_DSY: return {"dsy", 1, 1};
    case OP_TEXLDD: return {"texldd", 1, 4}; case OP_SETP: return {"setp", 1, 2};
    case OP_TEXLDL: return {"texldl", 1, 2}; case OP_BREAKP: return {"break", 0, 1};
    case OP_PHASE: return {"phase", 0, 0};
    default: return {nullptr, 0, 0};
    }
}

const char *op_name(uint32_t op) {
    OpInfo i = op_info(op);
    return i.name ? i.name : "?";
}

static Src decode_src(uint32_t t) {
    Src s;
    s.type = reg_type(t);
    s.num = t & 0x7FF;
    for (int i = 0; i < 4; i++) s.swz[i] = (t >> (16 + 2 * i)) & 3;
    s.mod = (t >> 24) & 0xF;
    s.rel = (t >> 13) & 1;
    return s;
}

static Dst decode_dst(uint32_t t) {
    Dst d;
    d.type = reg_type(t);
    d.num = t & 0x7FF;
    d.mask = (t >> 16) & 0xF;
    uint32_t mod = (t >> 20) & 0xF;
    d.sat = mod & 1;
    d.pp = mod & 2;
    d.centroid = mod & 4;
    int sh = (t >> 24) & 0xF;
    d.shift = sh >= 8 ? sh - 16 : sh;
    d.rel = (t >> 13) & 1;
    return d;
}

static void parse_ctab(const uint8_t *b, size_t len, Shader &out) {
    auto rd32 = [&](size_t o) -> uint32_t {
        uint32_t v = 0;
        if (o + 4 <= len) memcpy(&v, b + o, 4);
        return v;
    };
    auto rd16 = [&](size_t o) -> uint32_t {
        uint16_t v = 0;
        if (o + 2 <= len) memcpy(&v, b + o, 2);
        return v;
    };
    if (len < 28) return;
    uint32_t nconst = rd32(12), cinfo = rd32(16);
    for (uint32_t i = 0; i < nconst && cinfo + 20 * (i + 1) <= len; i++) {
        size_t o = cinfo + 20 * i;
        CtabEntry e;
        uint32_t name = rd32(o);
        e.set = rd16(o + 4);
        e.reg = rd16(o + 6);
        e.count = rd16(o + 8);
        uint32_t ti = rd32(o + 12);
        e.cls = rd16(ti);
        e.type = rd16(ti + 2);
        for (size_t k = name; k < len && b[k]; k++) e.name += (char)b[k];
        out.ctab.push_back(e);
    }
}

bool parse(const uint32_t *tok, size_t n, Shader &out) {
    out = Shader();
    if (n < 2) { out.error = "too short"; return false; }
    uint32_t ver = tok[0];
    if ((ver >> 16) == 0xFFFE) out.ps = false;
    else if ((ver >> 16) == 0xFFFF) out.ps = true;
    else { out.error = "bad version token"; return false; }
    out.major = (ver >> 8) & 0xFF;
    out.minor = ver & 0xFF;
    if (out.major < 1 || out.major > 3) { out.error = "unsupported shader model"; return false; }
    bool sm1 = out.major < 2;
    size_t i = 1;
    bool ended = false;
    while (i < n) {
        uint32_t t = tok[i];
        uint32_t op = t & 0xFFFF;
        if (op == OP_COMMENT) {
            uint32_t ln = (t >> 16) & 0x7FFF;
            if (i + 1 + ln > n) { out.error = "truncated comment"; return false; }
            if (ln >= 1 && tok[i + 1] == 0x42415443u /* 'CTAB' */)
                parse_ctab((const uint8_t *)(tok + i + 2), (ln - 1) * 4, out);
            i += 1 + ln;
            continue;
        }
        if (op == OP_END) { ended = true; out.end_token = i; break; }
        OpInfo info = op_info(op);
        if (!info.name) {
            char m[96];
            snprintf(m, sizeof m, "unknown opcode %u at token %zu", op, i);
            out.error = m;
            return false;
        }
        size_t len;
        if (!sm1) {
            len = (t >> 24) & 0xF;
        } else if (op == OP_DCL) {
            len = 2;
        } else if (op == OP_DEF || op == OP_DEFI) {
            len = 5;
        } else if (op == OP_DEFB) {
            len = 2;
        } else {
            len = info.dst + info.nsrc;
            if (out.ps && (op == OP_TEX || op == OP_TEXCOORD) && out.minor >= 4) len = 2;
        }
        if (op == OP_PHASE) len = 0;
        if (i + 1 + len > n) { out.error = "truncated instruction"; return false; }
        const uint32_t *p = tok + i + 1;
        Instr in;
        in.op = op;
        in.ctrl = (t >> 16) & 0xFF;
        in.coissue = sm1 && out.ps && (t & 0x40000000);
        in.predicated = !sm1 && (t & 0x10000000);
        in.token_offset = i;
        if (op == OP_DCL) {
            if (len != 2) { out.error = "bad dcl"; return false; }
            in.usage = p[0] & 0x1F;
            in.usage_index = (p[0] >> 16) & 0xF;
            in.tex_type = (p[0] >> 27) & 0xF;
            in.has_dst = true;
            in.dst = decode_dst(p[1]);
        } else if (op == OP_DEF || op == OP_DEFI) {
            if (len != 5) { out.error = "bad def"; return false; }
            in.has_dst = true;
            in.dst = decode_dst(p[0]);
            for (int k = 0; k < 4; k++) in.defv[k] = p[1 + k];
        } else if (op == OP_DEFB) {
            if (len != 2) { out.error = "bad defb"; return false; }
            in.has_dst = true;
            in.dst = decode_dst(p[0]);
            in.defv[0] = p[1];
        } else if (len) {
            size_t k = 0;
            if (info.dst) {
                in.has_dst = true;
                in.dst = decode_dst(p[k++]);
                if (in.dst.rel && !sm1) {
                    if (k >= len) { out.error = "missing dst relative token"; return false; }
                    Src r = decode_src(p[k++]);
                    in.dst.rel_type = r.type;
                    in.dst.rel_num = r.num;
                    in.dst.rel_comp = r.swz[0];
                }
            }
            if (in.predicated) {
                if (k >= len) { out.error = "missing predicate token"; return false; }
                in.pred = decode_src(p[k++]);
            }
            while (k < len) {
                Src s = decode_src(p[k++]);
                if (s.rel) {
                    if (sm1) {
                        s.rel_type = R_ADDR;
                        s.rel_num = 0;
                        s.rel_comp = 0;
                    } else {
                        if (k >= len) { out.error = "missing relative token"; return false; }
                        Src r = decode_src(p[k++]);
                        s.rel_type = r.type;
                        s.rel_num = r.num;
                        s.rel_comp = r.swz[0];
                    }
                }
                in.src.push_back(s);
            }
        }
        out.code.push_back(in);
        i += 1 + len;
    }
    if (!ended) { out.error = "missing end token"; return false; }
    return true;
}

// ---------------------------------------------------------------------------------
// disassembler (fxc-like; matches tools/scripts/extract_pc_effects.py output so the
// decoder can be checked against the reference .asm files)
// ---------------------------------------------------------------------------------

static const char *COMP = "xyzw";

static std::string fmt_float(float f) {
    if (f != f) return "NaN";
    if (std::isinf(f)) return f > 0 ? "INF" : "-INF";
    char buf[64];
    for (int p = 1; p < 10; p++) {
        snprintf(buf, sizeof buf, "%.*g", p, (double)f);
        if ((float)strtod(buf, nullptr) == f) break;
    }
    std::string s = buf;
    if (s.find('e') != std::string::npos && fabs(f) >= 1e-5 && fabs(f) < 1e8) {
        char b2[64];
        snprintf(b2, sizeof b2, "%.12f", strtod(buf, nullptr));
        std::string s2 = b2;
        while (!s2.empty() && s2.back() == '0') s2.pop_back();
        if (!s2.empty() && s2.back() == '.') s2.pop_back();
        if ((float)strtod(s2.c_str(), nullptr) == f) s = s2;
    }
    return s;
}

static std::string reg_name(const Shader &sh, uint32_t type, uint32_t num) {
    char b[32];
    switch (type) {
    case R_TEMP: snprintf(b, sizeof b, "r%u", num); break;
    case R_INPUT: snprintf(b, sizeof b, "v%u", num); break;
    case R_CONST: case R_CONST2: case R_CONST3: case R_CONST4:
        snprintf(b, sizeof b, "c%u", const_index(type, num)); break;
    case R_ADDR: snprintf(b, sizeof b, sh.ps ? "t%u" : "a%u", num); break;
    case R_RASTOUT: return num == 0 ? "oPos" : num == 1 ? "oFog" : "oPts";
    case R_ATTROUT: snprintf(b, sizeof b, "oD%u", num); break;
    case R_TEXCRDOUT: snprintf(b, sizeof b, (!sh.ps && sh.major >= 3) ? "o%u" : "oT%u", num); break;
    case R_CONSTINT: snprintf(b, sizeof b, "i%u", num); break;
    case R_COLOROUT: snprintf(b, sizeof b, "oC%u", num); break;
    case R_DEPTHOUT: return "oDepth";
    case R_SAMPLER: snprintf(b, sizeof b, "s%u", num); break;
    case R_CONSTBOOL: snprintf(b, sizeof b, "b%u", num); break;
    case R_LOOP: return "aL";
    case R_TEMPF16: snprintf(b, sizeof b, "half%u", num); break;
    case R_MISC: return num == 0 ? "vPos" : "vFace";
    case R_LABEL: snprintf(b, sizeof b, "l%u", num); break;
    case R_PREDICATE: snprintf(b, sizeof b, "p%u", num); break;
    default: snprintf(b, sizeof b, "?%u_%u", type, num);
    }
    return b;
}

static std::string swz_str(const uint8_t *s) {
    if (s[0] == 0 && s[1] == 1 && s[2] == 2 && s[3] == 3) return "";
    if (s[0] == s[1] && s[1] == s[2] && s[2] == s[3]) return std::string(".") + COMP[s[0]];
    int n = 4;
    while (n > 1 && s[n - 1] == s[n - 2]) n--;
    std::string r = ".";
    for (int i = 0; i < n; i++) r += COMP[s[i]];
    return r;
}

static std::string dst_str(const Shader &sh, const Dst &d) {
    std::string r = reg_name(sh, d.type, d.num);
    if (d.rel) r += "[" + reg_name(sh, d.rel_type, d.rel_num) + "." + COMP[d.rel_comp] + "]";
    if (d.mask != 0xF) {
        r += ".";
        for (int i = 0; i < 4; i++)
            if (d.mask & (1 << i)) r += COMP[i];
    }
    return r;
}

static std::string dst_mods(const Dst &d) {
    std::string s;
    switch (d.shift) {
    case 1: s += "_x2"; break; case 2: s += "_x4"; break; case 3: s += "_x8"; break;
    case -1: s += "_d2"; break; case -2: s += "_d4"; break; case -3: s += "_d8"; break;
    default: break;
    }
    if (d.sat) s += "_sat";
    if (d.pp) s += "_pp";
    if (d.centroid) s += "_centroid";
    return s;
}

static std::string src_str(const Shader &sh, const Src &s) {
    std::string name = reg_name(sh, s.type, s.num);
    if (s.rel) {
        std::string rs = reg_name(sh, s.rel_type, s.rel_num) + "." + COMP[s.rel_comp];
        if (is_const_type(s.type)) {
            char b[64];
            if (sh.major < 2) snprintf(b, sizeof b, "c[%s + %u]", rs.c_str(), const_index(s.type, s.num));
            else snprintf(b, sizeof b, "c%u[%s]", const_index(s.type, s.num), rs.c_str());
            name = b;
        } else {
            name += "[" + rs + "]";
        }
    }
    name += swz_str(s.swz);
    switch (s.mod) {
    case SM_NONE: return name;
    case SM_NEG: return "-" + name;
    case SM_BIAS: return name + "_bias";
    case SM_BIASNEG: return "-" + name + "_bias";
    case SM_SIGN: return name + "_bx2";
    case SM_SIGNNEG: return "-" + name + "_bx2";
    case SM_COMP: return "1-" + name;
    case SM_X2: return name + "_x2";
    case SM_X2NEG: return "-" + name + "_x2";
    case SM_DZ: return name + "_dz";
    case SM_DW: return name + "_dw";
    case SM_ABS: return name + "_abs";
    case SM_ABSNEG: return "-" + name + "_abs";
    case SM_NOT: return "!" + name;
    default: return name + "_mod" + std::to_string(s.mod);
    }
}

static const char *cmp_suffix(uint32_t c) {
    static const char *t[8] = {"_cmp0", "_gt", "_eq", "_ge", "_lt", "_ne", "_le", "_cmp7"};
    return t[c & 7];
}

std::string disassemble(const Shader &sh) {
    std::string out;
    char b[160];
    if (sh.major == 2 && sh.minor == 1) snprintf(b, sizeof b, "%s_2_x\n", sh.ps ? "ps" : "vs");
    else snprintf(b, sizeof b, "%s_%d_%d\n", sh.ps ? "ps" : "vs", sh.major, sh.minor);
    out += b;
    static const char *usages[] = {"position", "blendweight", "blendindices", "normal", "psize",
                                   "texcoord", "tangent", "binormal", "tessfactor", "positiont",
                                   "color", "fog", "depth", "sample"};
    static const char *stypes[] = {"", "_1d", "_2d", "_cube", "_volume"};
    for (const Instr &in : sh.code) {
        std::string line = in.coissue ? "+" : "";
        if (in.op == OP_DCL) {
            if (in.dst.type == R_SAMPLER) line += std::string("dcl") + (in.tex_type < 5 ? stypes[in.tex_type] : "_t?");
            else if (sh.ps && sh.major < 3) line += "dcl";
            else {
                line += std::string("dcl_") + (in.usage < 14 ? usages[in.usage] : "usage?");
                if (in.usage_index) line += std::to_string(in.usage_index);
            }
            line += dst_mods(in.dst) + " " + dst_str(sh, in.dst);
        } else if (in.op == OP_DEF) {
            line += "def " + dst_str(sh, in.dst) + ", ";
            for (int k = 0; k < 4; k++) {
                float f;
                memcpy(&f, &in.defv[k], 4);
                line += (k ? ", " : "") + fmt_float(f);
            }
        } else if (in.op == OP_DEFI) {
            line += "defi " + dst_str(sh, in.dst) + ", ";
            for (int k = 0; k < 4; k++) line += (k ? ", " : "") + std::to_string((int32_t)in.defv[k]);
        } else if (in.op == OP_DEFB) {
            line += "defb " + dst_str(sh, in.dst) + ", " + (in.defv[0] ? "true" : "false");
        } else {
            std::string name = op_name(in.op);
            if (in.op == OP_TEX && sh.ps && sh.major >= 2)
                name = (in.ctrl & 3) == 1 ? "texldp" : (in.ctrl & 3) == 2 ? "texldb" : "texld";
            else if (in.op == OP_TEX && sh.ps && sh.minor >= 4) name = "texld";
            else if (in.op == OP_TEXCOORD && sh.ps && sh.major == 1 && sh.minor >= 4) name = "texcrd";
            else if (in.op == OP_IFC || in.op == OP_BREAKC || in.op == OP_SETP) name += cmp_suffix(in.ctrl);
            if (in.predicated) line += "(" + src_str(sh, in.pred) + ") ";
            line += name;
            std::vector<std::string> ops;
            if (in.has_dst) {
                line += dst_mods(in.dst);
                ops.push_back(dst_str(sh, in.dst));
            }
            for (const Src &s : in.src) ops.push_back(src_str(sh, s));
            for (size_t k = 0; k < ops.size(); k++) line += (k ? ", " : " ") + ops[k];
        }
        out += line + "\n";
    }
    return out;
}

}  // namespace sm

extern "C" char *sm_disassemble(const uint32_t *tokens, size_t n) {
    sm::Shader s;
    if (!sm::parse(tokens, n, s)) return nullptr;
    std::string t = sm::disassemble(s);
    char *r = (char *)malloc(t.size() + 1);
    memcpy(r, t.c_str(), t.size() + 1);
    return r;
}

extern "C" size_t sm_token_count(const uint32_t *tokens, size_t max_tokens) {
    sm::Shader s;
    if (!sm::parse(tokens, max_tokens, s)) return 0;
    return s.end_token + 1;
}
