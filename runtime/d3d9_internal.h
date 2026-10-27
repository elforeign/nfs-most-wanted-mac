// d3d9_internal.h - state shared by d3d9.m (COM objects, device state) and render.m (Metal rendering).
#pragma once
#import <Metal/Metal.h>
#import <QuartzCore/CAMetalLayer.h>

#include "host.h"

enum Kind { K_D3D = 1, K_DEVICE, K_TEXTURE, K_CUBE, K_VOLUME, K_SURFACE, K_VB, K_IB, K_DECL, K_VS, K_PS, K_STATEBLOCK, K_QUERY, K_SWAPCHAIN };
typedef struct Obj {
    int kind, refs;
    uint32_t guest;              // this
    // textures / surfaces / buffers
    uint32_t w, h, d, levels, usage, fmt, pool, fvf, size;
    uint32_t mem[16 * 6];        // per level (and face) guest memory; buffers use mem[0]
    uint32_t pitch[16];
    uint32_t parent, level, face; // surface of a texture level
    uint32_t ms_type, ms_quality, samples;   // multisampled surfaces: D3DMULTISAMPLE_TYPE, quality, Metal sample count (0/1 = none)
    uint32_t surfaces[16 * 6];   // cached surface objects per level/face
    int dirty;
    uint8_t hd_swap, hd_tinted;  // an X360 HD texture (x360_textures.m); uploaded with the probe's signature (probe.m)
                                 // (hd_swap 2: an X360 arrest-scene character texture, its draws counted)
    // shaders / declarations
    uint32_t *tokens, ntokens;
    uint64_t tokens_hash;        // render.m: FNV of the bytecode (0 = not computed yet)
    uint64_t last_key; int last_idx;   // render.m: the shader-cache entry of the last lookup
    // render.m: the keys of the last two (options, variant) lookups, so a repeated lookup skips the byte-wise FNV
    uint8_t memo_opt[2][48]; uint32_t memo_variant[2]; uint64_t memo_key[2]; int memo_next;
    uint32_t vc_serial;          // render.m: never-reused id for the shader-variant lookup cache (0 = not assigned)
    uint32_t decl_mem, decl_n;
    // query
    uint32_t qtype;
    int q_state, q_cached;                   // render_query.m, occlusion: q_state 1 counting, 2 ended; q_cached: q_result read
    uint64_t q_serial;                       // the frame whose command buffer holds the counts
    uint32_t q_slot[8], q_samples[8], q_nseg, q_result;   // visibility-buffer slot and sample count per pass it spanned
    // Metal side (render.m)
    void *gpu;                   // RenderTex / RenderShader, owned by render.m
} Obj;

typedef struct {
    Obj *self;
    uint32_t hwnd, behavior;
    uint32_t pp[14];                    // D3DPRESENT_PARAMETERS
    uint32_t rs[256];                   // render states
    uint32_t tss[8][33], ss[20][14];    // texture stage / sampler states
    uint32_t tex[20];
    uint32_t stream[16], stream_off[16], stream_stride[16], stream_freq[16];
    uint32_t indices, decl, fvf, vs, ps;
    float vsc[256][4], psc[224][4];
    int32_t vsi[16][4], psi[16][4];
    uint32_t vsb[16], psb[16];
    uint32_t ver_vsc, ver_psc, ver_vsi, ver_psi, ver_vsb, ver_psb;   // bumped by every Set*ShaderConstant* (render.m uploads on change)
    float transform[512][16];
    uint32_t viewport[6], scissor[4];
    uint32_t rt[4], ds;
    uint32_t backbuffer, autodepth;
    float clear_rgba[4];
    uint64_t frames, draws, frame_draws;
    uint64_t t0;
} Device;

extern Device dev;
extern id<MTLDevice> mtl;
extern id<MTLCommandQueue> queue;
extern CAMetalLayer *layer;
Obj *obj(uint32_t g);
int is_dxt(uint32_t f);
uint32_t fmt_bpp(uint32_t f);
uint32_t level_rows(uint32_t f, uint32_t h);
uint32_t mipdim(uint32_t v, uint32_t l);

// render.m
void render_init(void);
void render_resize(void);
void render_draw(uint32_t type, uint32_t prims, int indexed, int32_t base_vertex, uint32_t min_index, uint32_t num_vertices,
                 uint32_t start, uint32_t up_vertices, uint32_t up_stride, uint32_t up_indices, uint32_t up_index_fmt);
void render_clear(uint32_t count, uint32_t rects, uint32_t flags, uint32_t color, float z, uint32_t stencil);
void render_present(void);
void render_set_gamma(const uint16_t ramp[768]);   // render_present.m: the game's gamma ramp (Brightness), applied at present
void render_stretch(uint32_t src, uint32_t src_rect, uint32_t dst, uint32_t dst_rect, uint32_t filter);
void render_readback(uint32_t src, uint32_t dst);
void render_release(Obj *o);
void render_targets_changed(void);
void render_query_issue(Obj *q, uint32_t flags);     // occlusion queries: Issue(END 1 / BEGIN 2)
int render_query_result(Obj *q, uint32_t *pixels);   // 1 when its frame has completed
uint32_t render_msaa_count(uint32_t type, uint32_t quality);   // Metal sample count for a D3D multisample type/quality (1 = none)
uint32_t render_msaa_levels(uint32_t *counts);                 // supported sample counts above 1, ascending
int render_msaa_samples(void);                                 // the back buffer's current sample count (1 = AA off)
int64_t render_msaa_bytes(void);                               // memory of the multisampled targets
