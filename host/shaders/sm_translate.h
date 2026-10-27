/*
 * sm_translate.h - Direct3D 9 shader bytecode (vs_1_1..vs_3_0, ps_1_1..ps_3_0) -> Metal
 * Shading Language translator. Plain C API; implementation is C++17 (sm_parse.cpp,
 * sm_msl.cpp). No third-party code.
 *
 * Usage (host D3D9 layer, CreateVertexShader / CreatePixelShader):
 *
 *     SmOptions opt; sm_default_options(&opt);
 *     SmResult r;
 *     if (sm_translate(tokens, ntokens, &opt, &r) != 0) { log(r.error); }
 *     ... newLibraryWithSource(r.msl), function r.entry ...
 *     sm_result_free(&r);
 *
 * Binding interface of the generated functions (see README.md for the full contract):
 *
 *   vertex  "sm_main": stage_in attributes [[attribute(n)]] = input register v<n>
 *                      buffer(SM_VS_BUF_FLOAT=16)  float4 c[256]   (SetVertexShaderConstantF as-is)
 *                      buffer(SM_VS_BUF_INT  =17)  int4   i[16]    (SetVertexShaderConstantI)
 *                      buffer(SM_VS_BUF_BOOL =18)  int    b[16]    (SetVertexShaderConstantB, BOOL)
 *                      buffer(SM_VS_BUF_AUX  =19)  SmVSAux         (half-pixel fix-up)
 *                      texture(n)/sampler(n) = D3DVERTEXTEXTURESAMPLERn (vs_3_0 texldl)
 *   fragment "sm_main": buffer(0..2) c/i/b as above (pixel constants), buffer(3) SmPSAux
 *                      texture(n)/sampler(n) = sampler s<n> (texture stage n for ps_1_x)
 *
 *   Varyings are a fixed canonical set so any VS links with any PS (see SmVarying).
 */
#ifndef SM_TRANSLATE_H
#define SM_TRANSLATE_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

enum { SM_VERTEX = 0, SM_PIXEL = 1 };

enum {
    SM_VS_BUF_FLOAT = 16, SM_VS_BUF_INT = 17, SM_VS_BUF_BOOL = 18, SM_VS_BUF_AUX = 19,
    SM_PS_BUF_FLOAT = 0,  SM_PS_BUF_INT = 1,  SM_PS_BUF_BOOL = 2,  SM_PS_BUF_AUX = 3,
};

/* Sampler / texture dimensionality. */
enum { SM_TEX_NONE = 0, SM_TEX_2D = 2, SM_TEX_CUBE = 3, SM_TEX_3D = 4 };

/* D3DDECLUSAGE values (dcl_* usage). */
enum {
    SM_USAGE_POSITION = 0, SM_USAGE_BLENDWEIGHT, SM_USAGE_BLENDINDICES, SM_USAGE_NORMAL,
    SM_USAGE_PSIZE, SM_USAGE_TEXCOORD, SM_USAGE_TANGENT, SM_USAGE_BINORMAL,
    SM_USAGE_TESSFACTOR, SM_USAGE_POSITIONT, SM_USAGE_COLOR, SM_USAGE_FOG, SM_USAGE_DEPTH,
    SM_USAGE_SAMPLE
};

/* Canonical varying slots shared by every translated VS and PS. The VS always writes all of
 * them (unwritten ones get (0,0,0,0); fog gets (1,0,0,0) = no fog), the PS declares only those
 * it reads. MSL names: color0/1, texcoord0..7, fog ([[user(...)]]). */
enum {
    SM_VARY_POSITION = 0, SM_VARY_COLOR0 = 1, SM_VARY_COLOR1 = 2,
    SM_VARY_TEXCOORD0 = 3, /* .. SM_VARY_TEXCOORD0 + 7 */
    SM_VARY_FOG = 11, SM_VARY_PSIZE = 12, SM_VARY_COUNT = 13
};

/* Host-filled auxiliary state (layouts mirrored in the MSL). */
typedef struct SmVSAux {
    float pos_fixup[4];      /* clip.xy += pos_fixup.xy * clip.w: D3D9 puts pixel centres on
                                integer coordinates, Metal on +0.5, so geometry moves half a pixel
                                right/down: (+1/viewport_width, -1/viewport_height, 0, 0) */
    float point_size;        /* D3DRS_POINTSIZE, used when the shader does not write oPts */
    float pad[3];
} SmVSAux;

typedef struct SmPSAux {
    uint32_t alpha_func;     /* D3DCMPFUNC (1 NEVER .. 8 ALWAYS); 0 = alpha test off */
    float    alpha_ref;      /* D3DRS_ALPHAREF / 255 */
    uint32_t pad0, pad1;
    float    bumpenv[8][4];  /* per stage: BUMPENVMAT00, 01, 10, 11 (texbem/bem) */
    float    bumplum[8][4];  /* per stage: BUMPENVLSCALE, BUMPENVLOFFSET, 0, 0 (texbeml) */
    float    lodbias[16];    /* per sampler: D3DSAMP_MIPMAPLODBIAS, added to implicit-LOD lookups (and texldb's bias) */
} SmPSAux;

typedef struct SmOptions {
    /* ps_1_x has no sampler declarations: dimensionality of each stage. 0 = use the CTAB
       sampler type if present, else 2D. For ps_2_0+ the dcl wins unless this is set. */
    uint8_t  sampler_type[16];
    /* ps_1_1..1_3 `tex`: D3DTTFF_PROJECTED per stage: 0 = off, 3 = divide by .z, 4 = by .w */
    uint8_t  tex_projected[8];
    /* samplers bound to depth textures (D3DFMT_D24S8/D16 "hardware shadow maps"): sampled
       with sample_compare (ref = coord.z, projected by w for texldp), result splatted.
       Host must create the MTLSamplerState with compareFunction = LessEqual. */
    uint16_t shadow_samplers;
    /* vertex attributes the host feeds as integer formats (Metal cannot convert integer
       vertex formats to float attributes): declared int4 / uint4 and converted. */
    uint16_t vs_input_int;   /* SHORT2/SHORT4 (non-normalized) */
    uint16_t vs_input_uint;  /* UBYTE4 */
    /* 1 = plain IEEE multiply. 0 (default) = D3D9 legacy rule 0 * anything = 0 in
       mul, mad, dp2/3/4, lit, nrm, which D3D9 content relies on (rsq(0) * 0 etc.). */
    uint8_t  ieee_mul;
    /* 1 = clamp ps_1_x constant reads to [-1,1] like D3D9 hardware (default 1). */
    uint8_t  ps1_clamp_consts;
    /* 1 = also emit a compute kernel "sm_test_kernel" running the shader body on flat
       float4 buffers (used by the numeric tests; not needed by the host). */
    uint8_t  emit_test_kernel;
    uint8_t  reserved[5];
} SmOptions;

typedef struct SmInput {      /* VS: one dcl'd v# register; PS 3.0: one dcl'd v# input */
    uint8_t reg;              /* v<reg> == [[attribute(reg)]] for a VS */
    uint8_t usage;            /* SM_USAGE_* */
    uint8_t usage_index;
    uint8_t mask;             /* dcl write mask (bit0 = x) */
} SmInput;

typedef struct SmSampler {
    uint8_t reg;              /* s<reg> == texture(reg)/sampler(reg) */
    uint8_t type;             /* SM_TEX_* */
    uint8_t shadow;           /* emitted as depth texture + sample_compare */
    uint8_t pad;
} SmSampler;

typedef struct SmConstant {   /* from the CTAB comment, if any (D3DX names) */
    char     name[64];
    uint8_t  set;             /* 0 bool, 1 int4, 2 float4, 3 sampler */
    uint8_t  type;            /* D3DXPARAMETER_TYPE (e.g. 12 sampler2D, 14 samplerCUBE) */
    uint16_t reg;
    uint16_t count;
} SmConstant;

typedef struct SmResult {
    int      shader_type;     /* SM_VERTEX / SM_PIXEL */
    int      major, minor;    /* 2.1 = vs_2_x / ps_2_x */
    char    *msl;             /* NUL-terminated MSL source (malloc'd) */
    size_t   msl_len;
    const char *entry;        /* "sm_main" */

    /* constant usage (bitmasks: bit n = register n read by the shader, def'd ones excluded) */
    uint32_t float_used[8];   /* c0..c255 */
    uint16_t int_used, bool_used;
    int      float_rel;       /* 1 = relative addressing c[a0.x + n] (whole range read) */
    int      float_max;       /* highest c# referenced + 1, incl. relative bases */
    uint32_t float_defined[8];/* c# overridden by def inside the shader */

    int        n_inputs;      /* VS: v# with usage; PS 3.0: v# with usage */
    SmInput    inputs[16];
    uint32_t   varyings_read; /* PS: bit SM_VARY_* read */
    uint32_t   varyings_written; /* VS: bit SM_VARY_* written by the shader itself */
    int        n_samplers;
    SmSampler  samplers[16];
    int        color_outputs; /* PS: bitmask of oC# / r0 written */
    int        writes_depth;  /* PS: oDepth / texdepth */
    int        uses_kill;     /* PS: texkill */
    int        n_constants;
    SmConstant *constants;    /* CTAB entries (malloc'd) */
    int        n_instructions;/* executable instructions (excl. dcl/def) */
    char       error[256];
} SmResult;

void sm_default_options(SmOptions *opt);

/* Translates one shader. tokens: the bytecode as passed to CreateVertexShader /
 * CreatePixelShader (version token first, terminated by 0x0000FFFF). n: token count
 * upper bound (the end token stops parsing). Returns 0 on success, -1 on failure
 * (out->error says why; out->msl is NULL). Always call sm_result_free. */
int  sm_translate(const uint32_t *tokens, size_t n, const SmOptions *opt, SmResult *out);
void sm_result_free(SmResult *r);

/* Disassembly in fxc-like syntax (one instruction per line, no comments), malloc'd.
 * Returns NULL on parse error. */
char *sm_disassemble(const uint32_t *tokens, size_t n);

/* Number of tokens up to and including the end token, or 0 if malformed. */
size_t sm_token_count(const uint32_t *tokens, size_t max_tokens);

#ifdef __cplusplus
}
#endif
#endif
