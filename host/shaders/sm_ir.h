// sm_ir.h - decoded D3D9 shader bytecode (internal; shared by the MSL emitter, the
// disassembler and the CPU reference interpreter in tests/).
#pragma once
#include <cstdint>
#include <string>
#include <vector>

namespace sm {

enum RegType : uint32_t {
    R_TEMP = 0, R_INPUT = 1, R_CONST = 2, R_ADDR = 3 /* vs a0 / ps t# */, R_RASTOUT = 4,
    R_ATTROUT = 5, R_TEXCRDOUT = 6 /* vs3: o# */, R_CONSTINT = 7, R_COLOROUT = 8,
    R_DEPTHOUT = 9, R_SAMPLER = 10, R_CONST2 = 11, R_CONST3 = 12, R_CONST4 = 13,
    R_CONSTBOOL = 14, R_LOOP = 15, R_TEMPF16 = 16, R_MISC = 17, R_LABEL = 18, R_PREDICATE = 19,
};

enum Op : uint32_t {
    OP_NOP = 0, OP_MOV, OP_ADD, OP_SUB, OP_MAD, OP_MUL, OP_RCP, OP_RSQ, OP_DP3, OP_DP4,
    OP_MIN, OP_MAX, OP_SLT, OP_SGE, OP_EXP, OP_LOG, OP_LIT, OP_DST, OP_LRP, OP_FRC,
    OP_M4x4, OP_M4x3, OP_M3x4, OP_M3x3, OP_M3x2, OP_CALL, OP_CALLNZ, OP_LOOP, OP_RET,
    OP_ENDLOOP, OP_LABEL, OP_DCL, OP_POW, OP_CRS, OP_SGN, OP_ABS, OP_NRM, OP_SINCOS,
    OP_REP, OP_ENDREP, OP_IF, OP_IFC, OP_ELSE, OP_ENDIF, OP_BREAK, OP_BREAKC, OP_MOVA,
    OP_DEFB, OP_DEFI,
    OP_TEXCOORD = 64, OP_TEXKILL, OP_TEX, OP_TEXBEM, OP_TEXBEML, OP_TEXREG2AR,
    OP_TEXREG2GB, OP_TEXM3x2PAD, OP_TEXM3x2TEX, OP_TEXM3x3PAD, OP_TEXM3x3TEX, OP_RESERVED0,
    OP_TEXM3x3SPEC, OP_TEXM3x3VSPEC, OP_EXPP, OP_LOGP, OP_CND, OP_DEF, OP_TEXREG2RGB,
    OP_TEXDP3TEX, OP_TEXM3x2DEPTH, OP_TEXDP3, OP_TEXM3x3, OP_TEXDEPTH, OP_CMP, OP_BEM,
    OP_DP2ADD, OP_DSX, OP_DSY, OP_TEXLDD, OP_SETP, OP_TEXLDL, OP_BREAKP,
    OP_PHASE = 0xFFFD, OP_COMMENT = 0xFFFE, OP_END = 0xFFFF,
};

enum SrcMod : uint32_t {
    SM_NONE = 0, SM_NEG, SM_BIAS, SM_BIASNEG, SM_SIGN, SM_SIGNNEG, SM_COMP, SM_X2, SM_X2NEG,
    SM_DZ, SM_DW, SM_ABS, SM_ABSNEG, SM_NOT,
};

struct Src {
    uint32_t type = 0, num = 0;
    uint8_t swz[4] = {0, 1, 2, 3};
    uint32_t mod = 0;
    bool rel = false;           // relative addressing
    uint32_t rel_type = R_ADDR; // a0 or aL (SM1: always a0.x)
    uint32_t rel_num = 0;
    uint8_t rel_comp = 0;
    bool is_identity_swz() const { return swz[0] == 0 && swz[1] == 1 && swz[2] == 2 && swz[3] == 3; }
};

struct Dst {
    uint32_t type = 0, num = 0;
    uint8_t mask = 0xF;
    bool sat = false, pp = false, centroid = false;
    int shift = 0;              // signed: 1 = x2, 2 = x4, 3 = x8, -1 = d2, -2 = d4, -3 = d8
    bool rel = false;
    uint32_t rel_type = R_LOOP, rel_num = 0;
    uint8_t rel_comp = 0;
};

struct Instr {
    uint32_t op = 0;
    uint32_t ctrl = 0;          // bits 16..23 of the opcode token (comparison, texld flavour)
    bool coissue = false;       // ps_1_x '+'
    bool predicated = false;
    bool has_dst = false;
    Dst dst;
    Src pred;
    std::vector<Src> src;
    // dcl
    uint32_t usage = 0, usage_index = 0, tex_type = 0;
    // def / defi / defb
    uint32_t defv[4] = {0, 0, 0, 0};
    size_t token_offset = 0;
};

struct CtabEntry {
    std::string name;
    uint32_t set = 0, reg = 0, count = 0, cls = 0, type = 0;
};

struct Shader {
    bool ps = false;
    int major = 0, minor = 0;
    std::vector<Instr> code;
    std::vector<CtabEntry> ctab;
    std::string error;
    size_t end_token = 0;
    int ver() const { return major * 16 + minor; }   // 1.4 -> 0x14, 2.x -> 0x21
};

bool parse(const uint32_t *tok, size_t n, Shader &out);
std::string disassemble(const Shader &s);
const char *op_name(uint32_t op);

// helpers shared by emitter and interpreter
inline bool is_const_type(uint32_t t) { return t == R_CONST || (t >= R_CONST2 && t <= R_CONST4); }
inline uint32_t const_index(uint32_t t, uint32_t num) { return t == R_CONST ? num : num + 2048 * (t - 10); }

}  // namespace sm
