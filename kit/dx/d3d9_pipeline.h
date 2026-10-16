// d3d9_pipeline.h - what a Direct3D 9 device has bound for its next draw.
//
// Two paths fill it. The device's own setters (SetTexture, SetRenderState,
// SetSamplerState, the shader-constant setters) write it directly, and the
// D3DX effect framework writes it when a pass begins or its changes are
// committed: the pass's shader bytecode, the constant registers its shaders
// read, and the texture behind each sampler. A renderer reads only this, so
// it does not care which path a game used.
#pragma once
#include <cstdint>
#include <cstring>
#include <memory>
#include <string>
#include <vector>

// Shader bytecode shared with whoever owns it (an effect's shader object):
// binding a program is a pointer copy, which matters when passes of
// different effects alternate every draw.
struct D9ShaderBytes {
    std::shared_ptr<const std::vector<uint8_t>> bytes;
    const uint8_t *data() const {
        return bytes ? bytes->data() : nullptr;
    }
    size_t size() const {
        return bytes ? bytes->size() : 0;
    }
    bool empty() const {
        return !bytes || bytes->empty();
    }
    const std::vector<uint8_t> &vec() const {
        static const std::vector<uint8_t> none;
        return bytes ? *bytes : none;
    }
};

struct D9Pipeline {
    D9ShaderBytes vs, ps;                   // bound shader bytecode, empty for none
    uint64_t vs_key = 0, ps_key = 0;        // d9sh::code_key of each, 0 for none
    float vconst[256][4] = {};              // vertex shader float registers
    float pconst[224][4] = {};              // pixel shader float registers (ps_3_0: 224; Remaster R2, was 32)
    int32_t vbool[16] = {}, pbool[16] = {}; // shader Boolean registers (D3D BOOL)
    // Test75: the effect (COM id) and shader (code key) whose constants the float and Boolean registers
    // of each stage hold, and the D3DX parameter stamp of that bind. The next CommitChanges of the same
    // effect and shader rewrites only the parameters written after it. A device constant setter clears
    // it (fx 0), and so does anything that rebuilds the pipeline: then every parameter is written again.
    struct ConstBinder {
        uint32_t fx = 0;
        uint64_t key = 0, stamp = 0;
    };
    ConstBinder vbinder, pbinder;
    // Test75: the decoded programs (d9sh::Program, never freed) of the last draw's shader keys, so a
    // draw with the same shaders skips the locked program-cache lookup.
    uint64_t vs_prog_key = 0, ps_prog_key = 0;
    const void *vs_prog = nullptr, *ps_prog = nullptr;
    uint32_t sampler_tex[16] = {};          // COM object id of the texture per sampler
    uint32_t sampler_state[16][14] = {};    // D3DSAMPLERSTATETYPE, indexed as the enum
    uint32_t rs[256] = {};                  // render states, by D3DRENDERSTATETYPE
    bool rs_set[256] = {};
    // Changes whenever sampler_state, rs or rs_set may have: a draw passes it
    // on so the host can skip copying state it already holds. 0: unknown.
    uint64_t state_version = 0;
    // Every writer of sampler_state, rs or rs_set calls one of the two below.
    void rs_changed(uint32_t) {
        bump_state_version();
    }
    void ss_changed(uint32_t, uint32_t) {
        bump_state_version();
    }
    void bump_state_version() {
        static uint64_t next = 0; // global, so versions are unique across pipelines
        state_version = ++next;
    }
    uint32_t viewport[4] = {};          // X, Y, Width, Height
    float viewport_z[2] = {0.0f, 1.0f}; // MinZ, MaxZ
    bool viewport_set = false;          // false: the whole render target
    float transform[32][16] = {};       // D3DTS_* below 24; WORLD..WORLD3 at 24-27
    uint32_t tss[8][33] = {};           // texture stage states
    // What the device draws from and into, by object id.
    struct Stream {
        uint32_t vb = 0, offset = 0, stride = 0;
    };
    Stream stream[8];
    uint32_t index_buffer = 0;
    uint32_t declaration = 0;
    uint32_t vs_obj = 0, ps_obj = 0; // device-bound shader objects (Set*Shader), held by the device
    uint32_t color_target[4] = {}; // render target 0 lives on the device too
    int32_t scissor[4] = {};       // left, top, right, bottom
    const char *label = nullptr;   // effect/technique/pass that bound the shaders, for diagnostics
    // Remaster run 10 fused pool shader: a pool-copy effect's pass 0 applied with a fuse request (the mod set
    // CleanPoolCount.y = 1 for this entry): the pool block (CleanPools[16], CleanPoolCount, TextureOffset,
    // CleanPoolParams, LocalEyePos) and the effect and programs it belongs to. Every apply_pass sets or clears it,
    // and so does the owner's EndPass; gpu_draw fuses only while the bound programs are still the ones recorded.
    uint32_t pool_fuse = 0, pool_fx = 0;   // pool_fuse: 0 none, else the pool form (d9msl::PixelVariant::pool)
    const void *pool_vs = nullptr, *pool_ps = nullptr;   // pl.vs/ps bytes at the request
    float pool_block[20][4] = {};

    // Direct3D 9's documented defaults. ZENABLE also depends on whether the
    // device was made with a depth buffer; CreateDevice and Reset set it.
    D9Pipeline() {
        auto f = [](float x) {
            uint32_t b;
            memcpy(&b, &x, 4);
            return b;
        };
        rs[8] = 3;                    // FILLMODE solid
        rs[9] = 2;                    // SHADEMODE gouraud
        rs[14] = 1;                   // ZWRITEENABLE
        rs[16] = 1;                   // LASTPIXEL
        rs[19] = 2;                   // SRCBLEND one
        rs[20] = 1;                   // DESTBLEND zero
        rs[22] = 3;                   // CULLMODE ccw
        rs[23] = 4;                   // ZFUNC lessequal
        rs[25] = 8;                   // ALPHAFUNC always
        rs[26] = 0;                   // DITHERENABLE
        rs[38] = f(1.0f);             // FOGDENSITY
        rs[37] = f(1.0f);             // FOGEND
        rs[53] = rs[54] = rs[55] = 1; // stencil ops keep
        rs[56] = 8;                   // STENCILFUNC always
        rs[58] = rs[59] = 0xffffffffu;
        rs[60] = 0xffffffffu;              // TEXTUREFACTOR
        rs[136] = 1;                       // CLIPPING
        rs[137] = 1;                       // LIGHTING
        rs[141] = 1;                       // COLORVERTEX
        rs[142] = 1;                       // LOCALVIEWER
        rs[145] = 1;                       // DIFFUSEMATERIALSOURCE color1
        rs[146] = 2;                       // SPECULARMATERIALSOURCE color2
        rs[154] = f(1.0f);                 // POINTSIZE
        rs[155] = f(1.0f);                 // POINTSIZE_MIN
        rs[158] = f(1.0f);                 // POINTSCALE_A
        rs[162] = 0xffffffffu;             // MULTISAMPLEMASK
        rs[166] = f(64.0f);                // POINTSIZE_MAX
        rs[168] = 0xf;                     // COLORWRITEENABLE
        rs[171] = 1;                       // BLENDOP add
        rs[178] = f(1.0f);                 // MINTESSELLATIONLEVEL
        rs[179] = f(1.0f);                 // MAXTESSELLATIONLEVEL
        rs[183] = f(1.0f);                 // ADAPTIVETESS_W
        rs[186] = rs[187] = rs[188] = 1;   // ccw stencil ops keep
        rs[189] = 8;                       // CCW_STENCILFUNC always
        rs[190] = rs[191] = rs[192] = 0xf; // COLORWRITEENABLE1..3
        rs[193] = 0xffffffffu;             // BLENDFACTOR
        rs[207] = 2;                       // SRCBLENDALPHA one
        rs[208] = 1;                       // DESTBLENDALPHA zero
        rs[209] = 1;                       // BLENDOPALPHA add
        for (auto &s : sampler_state) {
            s[1] = s[2] = s[3] = 1; // ADDRESSU/V/W wrap
            s[5] = s[6] = 1;        // MAGFILTER, MINFILTER point
            s[10] = 1;              // MAXANISOTROPY
        }
        for (int i = 0; i < 8; ++i) {
            tss[i][1] = i == 0 ? 4 : 1; // COLOROP modulate / disable
            tss[i][2] = 2;              // COLORARG1 texture
            tss[i][3] = 1;              // COLORARG2 current
            tss[i][4] = i == 0 ? 2 : 1; // ALPHAOP selectarg1 / disable
            tss[i][5] = 2;
            tss[i][6] = 1;
            tss[i][11] = (uint32_t)i; // TEXCOORDINDEX
        }
    }
};

// The record for one device, created on first use and kept for its lifetime.
D9Pipeline &d9_pipeline(uint32_t device_id);
// Remaster run 10: the two-pass fallback for a fuse request the draw cannot fuse (d3dx9.cpp): applies the pool-copy
// effect's clean_pools pass, calls draw(ctx), then re-applies pass 0, all natively (the game's own pass loop is not
// involved). False when the effect or its pool pass is gone (nothing drawn).
bool d3dx9_pool_second_pass(D9Pipeline &pl, void (*draw)(void *), void *ctx);
