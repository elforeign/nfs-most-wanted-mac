// render.m - Direct3D 9 draw calls on Metal.
//
// Shaders: the game's SM1-3 bytecode is translated to MSL by port/host/shaders (sm_translate) and compiled once
// per bytecode + options. Vertex input comes from the vertex declaration (or the FVF), matched to the shader's
// dcl usages; attributes the declaration lacks read a zero buffer. Vertex and index data are copied per draw into
// a per-frame upload ring (D3D's DISCARD/NOOVERWRITE locks rewrite the same guest memory between draws). Textures
// upload from guest memory when dirty. Render passes start on target changes and clears; the back buffer is an
// offscreen texture that Present scales into the window's drawable.
//
// Split by owner (ARCHITECTURE.md, renderer file map): render_target.m (MSAA, resolves), render_present.m (Present, frame
// timing, diag capture encoding), render_post.m (X360 post + mirror), render_query.m (occlusion queries); what they
// share is declared in render_internal.h. This file keeps the draw, pipeline, sampler and texture core.
#include "render_internal.h"
#include "lampbatch.h"
#include "alpha_to_coverage.h"
#include "roadfilm.h"
#define HDRCLIP_COPY_TEST 1
#include "hdrclip.m" // diagnostic counter module, owned by this translation unit
#include "gen/x360_shader_lits.inc"   // X360F_KMAXDIST_WORLD (the X360 fog distance, from the player's ZZDATA0.BIN)

// Pipelines and depth-stencil states by 64-bit key: open addressing, objects retained for the process lifetime.
typedef struct { uint64_t key; void *obj; } MapSlot;
typedef struct { MapSlot *s; uint32_t cap, n; } Map;
static void *map_get(Map *m, uint64_t key) {
    if (!m->cap) return NULL;
    for (uint32_t i = (uint32_t)(key ^ key >> 29) & (m->cap - 1);; i = (i + 1) & (m->cap - 1)) {
        if (!m->s[i].obj) return NULL;
        if (m->s[i].key == key) return m->s[i].obj;
    }
}
static void map_put(Map *m, uint64_t key, id o) {
    if (2 * (m->n + 1) > m->cap) {
        Map g = {calloc(m->cap ? 2 * m->cap : 1024, sizeof(MapSlot)), m->cap ? 2 * m->cap : 1024, 0};
        for (uint32_t i = 0; i < m->cap; ++i)
            if (m->s[i].obj) {
                uint32_t j = (uint32_t)(m->s[i].key ^ m->s[i].key >> 29) & (g.cap - 1);
                while (g.s[j].obj) j = (j + 1) & (g.cap - 1);
                g.s[j] = m->s[i]; ++g.n;
            }
        free(m->s); *m = g;
    }
    uint32_t i = (uint32_t)(key ^ key >> 29) & (m->cap - 1);
    while (m->s[i].obj && m->s[i].key != key) i = (i + 1) & (m->cap - 1);
    if (!m->s[i].obj) ++m->n;
    else CFRelease(m->s[i].obj);
    m->s[i].key = key; m->s[i].obj = (void *)CFBridgingRetain(o);
}

// ---------------- GPU-side objects ----------------
typedef struct {
    uint64_t key;
    id<MTLFunction> fn;
    SmResult refl;
    int ok;
    volatile int pending;        // being compiled on the background queue
    dispatch_semaphore_t ready;
    uint32_t *tokens, ntokens;   // own copy (the game may release the shader meanwhile)
    SmOptions opt;
    int mat;                     // materials.m replacement (table index + 1), 0 = the translation
    uint32_t variant;             // R27: 0 PC; 1..5 Grass/Shell0..4; 6..10 GrassTransition/Shell0..4
} ShaderEntry;

id<MTLTexture> backbuffer_tex, present_tex, autodepth_tex;
static id<MTLTexture> zero_tex2d, zero_texcube, zero_tex3d, zero_depth, probe_depth, probe_magenta;
RenderTex bb_rt, ad_rt;               // the back buffer and auto depth buffer as render targets (.ms with AA on)
static id<MTLBuffer> ring[3];
uint32_t ring_off, ring_idx;
#define RING_SIZE (128u << 20)
static uint32_t ring_size = RING_SIZE;
static id<MTLBuffer> zero_buf;
id<MTLCommandBuffer> cmd;
id<MTLRenderCommandEncoder> enc;
id<MTLBuffer> vis_buf;                 // occlusion query counts (see occlusion queries)
#define VIS_SLOTS 4096u
int pass_dirty = 1;
#define MAX_SAMPLERS 256     // was 64: later combinations were rebuilt on every bind (audit 1)
static id<MTLSamplerState> samplers[MAX_SAMPLERS];
static uint32_t sampler_keys[MAX_SAMPLERS][4];
static int nsamplers;
// audit 1 correctness counters (diag only, printed every 600 frames as "audit1:")
static unsigned long long stat_sync_pso, stat_sync_pso_ns, stat_sync_grass, stat_sync_grass_ns;   // diag: draw-time pipeline builds on the game thread
static struct { unsigned long long bf_draws, bc_stale, ds_calls, ds_mismatch, glass_draws, glass_stale_mid, glass_rebased, samp_miss_full, mrt_draws; } ad1;
static uint64_t ad1_ds_key[512]; static uint8_t ad1_ds_has[512]; static int ad1_nds;
static Map pipelines, dsstates;
static ShaderEntry **shaders;
static int nshaders, capshaders;
static dispatch_queue_t shader_workers[4];
static unsigned shader_worker_next;
static unsigned long long stat_sync_shader;
static id<MTLRenderPipelineState> blit_pso;
static Map blit_pipelines; // destination format AND sample count
static id<MTLLibrary> blit_lib;
static id<MTLSamplerState> blit_linear, blit_point;
static uint64_t stat_ff_skipped, stat_ff_drawn, stat_draws, stat_fail;
static uint32_t pending_clear_flags, pending_clear_color, pending_clear_stencil;
static float pending_clear_z;
static RenderTex fs_world_rt, fs_display_rt, fs_composite_rt;
static int fs_active, fs_composing, fs_hud, fs_toned;
static float fs_headroom = 1;
static uint64_t fs_serial;
void render_float_scene_final(int preserve);
// Exact flare replay needs the scene's depth dimensions and sample count. This
// full-resolution scratch attachment is reduced to quarter-res before blur;
// attaching full-res scene depth to a quarter-res colour target is invalid Metal.
static RenderTex lamp_rt;
static int lamp_replay;
// SSAO coverage is conservative: any passing transparent fragment excludes the
// pixel. Original translated shaders retain alpha-test/discard and depth tests.
extern id rt_render_list_new(void);
extern unsigned rt_render_list_ops(id);
extern void rt_render_list_replay(id,id<MTLRenderCommandEncoder>);
extern int x360_post_ssao_enabled(void);
extern int x360_post_chain_on(void);
static RenderTex ssao_mask,ssao_reference_mask,ssao_cpu_reference_mask;
static id<MTLTexture> ssao_half_depth;
static id ssao_reference_rec,ssao_cpu_reference_rec;
static int ssao_cpu_reference_record;
static int ssao_reference_record;
static uint64_t ssao_half_serial;
extern int postfx_look_variant(const char *v);
static int soft_smoke = -1, soft_smoke_encoding;
static int soft_depth_valid;
static uint64_t soft_depth_frame;
static id<MTLTexture> soft_depth;
static id<MTLTexture> soft_copy_depth;
static uint64_t soft_late_done;
static int soft_smoke_late_on(void) {
    static int early=-1;if(early<0)early=getenv("NFS_TEST_SOFT_SMOKE_EARLY")!=NULL;
    static int split=-1;if(split<0){const char *p=getenv("NFS_PADS");split=p&&!strcmp(p,"2");}
    return !early && !split && x360_post_chain_on() && M32(0x00925E90u)==6 && M32(0x00901828u) && M32(0x00982AF0u) && M8(0x008F9B5Cu);
}
static int soft_smoke_on(void) {
    if (diag_on) {
        if (postfx_look_variant("softoff")) return 0;
        if (postfx_look_variant("softon") || postfx_look_variant("softbroken")) return 1;
    }
    if (soft_smoke < 0) { const char *e = getenv("NFS_SOFT_SMOKE"); soft_smoke = !e || strcmp(e, "off"); }
    return soft_smoke;
}
int render_soft_depth_store(void) {
    if(!soft_smoke_on() || M32(0x00925E90u)!=6 || surface_rt(dev.rt[0])!=&bb_rt)return 0;
    if(soft_smoke_late_on())return soft_late_done==frame_serial?0:1;
    return soft_depth_valid && soft_depth_frame==frame_serial ? 2 : 1;
}
static int soft_smoke_cost_enabled(void) {
    if(!diag_on)return 0;
    static int enabled=-1;if(enabled<0)enabled=getenv("BOOT_DIAG_SOFT_SMOKE_COST")!=NULL;
    return enabled;
}
static void soft_smoke_cost_note(int material) {
    if(!soft_smoke_cost_enabled() || !mat_is_particle(material) || !mat_soft_particle_view())return;
    static uint64_t noted;if(noted==frame_serial)return;noted=frame_serial;
    diag_event("soft smoke cost: frame %llu sim %.6f on %d held %d",(unsigned long long)frame_serial,MF32(0x009885D8u),soft_smoke_on(),
        postfx_look_variant("softon")||postfx_look_variant("softoff"));
}
// Separate target: never sample a depth attachment while it is being rendered.
// MSAA keeps the nearest sample, avoiding sample-0 holes along opaque silhouettes.
static const char soft_depth_msl[] =
"#include <metal_stdlib>\nusing namespace metal;\n"
"struct SDV { float4 p [[position]]; };\n"
"vertex SDV smoke_depth_vs(uint v [[vertex_id]]) { float2 p[3]={float2(-1,-1),float2(3,-1),float2(-1,3)}; return {float4(p[v],0,1)}; }\n"
"#if SMOKE_MSAA\n"
"fragment float smoke_depth_fs(SDV i [[stage_in]], depth2d_ms<float, access::read> z [[texture(0)]]) {\n"
"    float d=1.0f; for (uint s=0;s<z.get_num_samples();++s) d=min(d,z.read(uint2(i.p.xy),s)); return d; }\n"
"#else\n"
"fragment float smoke_depth_fs(SDV i [[stage_in]], depth2d<float, access::read> z [[texture(0)]]) { return z.read(uint2(i.p.xy)); }\n"
"#endif\n";
                                                                               
// replay and post separately, then remove the measured source without changing draws.
// All clocks and per-frame output are BOOT_DIAG-only; these are diagnostic samples.
int ssao_cpu_diag; // BOOT_DIAG_SSAO_CPU=0 disables these clocks for a diagnostic A/B.
uint64_t ssao_diag_post_ns;
static uint64_t ssao_diag_record_ns,ssao_diag_setup_ns,ssao_diag_replay_ns,ssao_diag_draw_ns,ssao_diag_draws;
static void ssao_diag_frame(void) {
    static uint64_t serial;
    static FILE *f;
    if (serial==frame_serial) return;
    if (!f&&diag_dir) {
        char path[1024];snprintf(path,sizeof path,"%s/ssao_cpu.csv",diag_dir);
        f=fopen(path,"w");
        if(f)fprintf(f,"frame,sim,draw_ms,setup_ms,record_ms,replay_ms,post_ms,coverage_draws\n");
    }
    if (f&&serial) {
        fprintf(f,"%llu,%.4f,%.6f,%.6f,%.6f,%.6f,%.6f,%llu\n",
            (unsigned long long)serial,MF32(0x009885D8u),ssao_diag_draw_ns/1e6,
            ssao_diag_setup_ns/1e6,ssao_diag_record_ns/1e6,ssao_diag_replay_ns/1e6,
            ssao_diag_post_ns/1e6,(unsigned long long)ssao_diag_draws);
        if(serial%120==0)fflush(f);
    }
    serial=frame_serial;
    ssao_diag_record_ns=ssao_diag_setup_ns=ssao_diag_replay_ns=ssao_diag_draw_ns=ssao_diag_post_ns=ssao_diag_draws=0;
}
static int ssao_proof(void) { return diag_on && (postfx_look_variant("ssaon")||postfx_look_variant("ssaobroken")); }
static RenderTex *ssao_target(void) { return ssao_cpu_reference_record?&ssao_cpu_reference_mask:ssao_reference_record?&ssao_reference_mask:&ssao_mask; }
static id<MTLTexture> ssao_depth;
static uint64_t ssao_serial;
static int ssao_replay;
static id ssao_rec;
static uint64_t ssao_cleared;
static int ssao_tree_fringe;
static id<MTLTexture> ssao_coverage_z;

static uint64_t lamp_serial;
static uint64_t lamp_valid[2];             // this frame contains accepted replays for the selected player
static int lamp_prepare(void) {
    NSUInteger lv, sl;
    id<MTLTexture> t = surface_target(dev.rt[0], &lv, &sl);
    id<MTLTexture> z = surface_target(dev.ds, &lv, &sl);
    if (!t || !z || t.width != z.width || t.height != z.height || t.sampleCount != z.sampleCount ||
        (t.pixelFormat != MTLPixelFormatBGRA8Unorm && t.pixelFormat != MTLPixelFormatRGBA16Float) || q_active || !dev.rs[7] ||
        !dev.rs[27] || dev.rs[20] != 2 || (dev.rs[19] != 2 && dev.rs[19] != 5) || (dev.rs[171] & ~1u)) return 0;   // blend op unset (0) or ADD (1)
    if (!lamp_rt.tex || lamp_rt.w != t.width || lamp_rt.h != t.height || (lamp_rt.ms ? lamp_rt.ms.sampleCount : 1) != t.sampleCount) {
        end_pass(); drop_ms_texture(&lamp_rt);
        MTLTextureDescriptor *d = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:t.width height:t.height mipmapped:NO];
        d.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead; d.storageMode = MTLStorageModePrivate;
        lamp_rt.tex = [mtl newTextureWithDescriptor:d]; lamp_rt.w = (uint32_t)t.width; lamp_rt.h = (uint32_t)t.height;
        if (t.sampleCount > 1) lamp_rt.ms = new_ms_texture(d.pixelFormat, t.width, t.height, (uint32_t)t.sampleCount);
        lamp_serial = 0;
        memset(lamp_valid, 0, sizeof lamp_valid);
    }
    return lamp_rt.tex && (t.sampleCount == 1 || lamp_rt.ms);
}
static void lamp_flush(int why);
static unsigned lamp_n;
// BOOT_DIAG_LAMP_HASH=1 (diag): FNV-1a hash and channel sum of the resolved lamp target each frame it is read,
// `lamphash:` lines (logged one read later, from the completed command buffer): held look variants
// (BOOT_DIAG_LOOK_AB=...,lampsplit,...) compare batched and immediate replays of the same scene exactly.
static int lamp_batch(void);
static void lamp_hash_diag(void) {
    enum { NS = 8 };
    static int on = -1;
    static id<MTLBuffer> buf[NS];
    static struct { uint64_t serial, hash, sum; int batched; } done[NS];
    static uint64_t printed;
    if (on < 0) on = diag_on && getenv("BOOT_DIAG_LAMP_HASH") != NULL;
    if (!on) return;
    for (;;) {                       // completed reads, oldest first
        int best = -1;
        for (int i = 0; i < NS; ++i) {
            uint64_t sr = __atomic_load_n(&done[i].serial, __ATOMIC_ACQUIRE);
            if (sr > printed && (best < 0 || sr < done[best].serial)) best = i;
        }
        if (best < 0) break;
        printed = done[best].serial;
        diag_event("lamphash: serial %llu %s hash %016llX sum %llu", (unsigned long long)printed, done[best].batched ? "batched" : "split",
                   (unsigned long long)done[best].hash, (unsigned long long)done[best].sum);
    }
    NSUInteger w = lamp_rt.tex.width, h = lamp_rt.tex.height;
    int k = (int)(frame_serial % NS);
    if (!buf[k] || buf[k].length < w * h * 4) buf[k] = [mtl newBufferWithLength:w * h * 4 options:MTLResourceStorageModeShared];
    end_pass(); ensure_cmd();
    id<MTLBlitCommandEncoder> b = [cmd blitCommandEncoder];
    [b copyFromTexture:lamp_rt.tex sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0, 0, 0) sourceSize:MTLSizeMake(w, h, 1)
              toBuffer:buf[k] destinationOffset:0 destinationBytesPerRow:w * 4 destinationBytesPerImage:w * h * 4];
    [b endEncoding];
    id<MTLBuffer> bb = buf[k]; uint64_t serial = frame_serial; size_t n = w * h * 4; int batched = lamp_batch();
    [cmd addCompletedHandler:^(id<MTLCommandBuffer> c) {
        const uint8_t *p = bb.contents; uint64_t hsh = 1469598103934665603ull, sum = 0;
        for (size_t i = 0; i < n; ++i) { hsh = (hsh ^ p[i]) * 1099511628211ull; sum += p[i]; }
        done[k].hash = hsh; done[k].sum = sum; done[k].batched = batched; __atomic_store_n(&done[k].serial, serial, __ATOMIC_RELEASE);
    }];
}
id<MTLTexture> render_lamp_source_view(uint32_t view) {
    if (lamp_n) lamp_flush(3);
    unsigned owner = view ? M32(view + 4) : 1;
    if (owner < 1 || owner > 2 || lamp_serial != frame_serial || lamp_valid[owner - 1] != frame_serial) return nil;
    if (diag_on && view && frame_serial % 120 == 0)
        diag_event("lampview: owner %u atlas %ux%u samples %u valid %d/%d serial %llu", owner, lamp_rt.w, lamp_rt.h,
            lamp_rt.ms ? (unsigned)lamp_rt.ms.sampleCount : 1, lamp_valid[0] == frame_serial, lamp_valid[1] == frame_serial,
            (unsigned long long)frame_serial);
    msaa_resolve(&lamp_rt);
    lamp_hash_diag();
    return lamp_rt.tex;
}
id<MTLTexture> render_lamp_source(void) { return render_lamp_source_view(0); }

static void shader_cache_warm(void);
static void ff_shader_warm(void);
static int ssao_depth_warm(void);
static void dump_consts(FILE *df, const char *stage, const SmResult *r, float (*k)[4], int nk);
static void depthdump(uint32_t guest, id<MTLTexture> src);

// ---------------- frames in flight ----------------
// Present commits the frame and only waits for the frame before it, so the CPU encodes frame N+1 while the GPU
// runs frame N. Every frame has a serial; resources remember the serial of the last frame that read them, and a
// texture or buffer the game rewrites while a frame that reads it is still pending (or earlier in the frame being
// encoded) gets new storage instead (renaming, as D3D drivers do), so no draw ever sees data written after it.
uint64_t frame_serial = 1;                 // the frame being encoded
uint64_t done_serial;                      // the last frame whose GPU work completed
pthread_mutex_t done_mu = PTHREAD_MUTEX_INITIALIZER;
pthread_cond_t done_cv = PTHREAD_COND_INITIALIZER;
int in_flight(uint64_t used) { return used > __atomic_load_n(&done_serial, __ATOMIC_ACQUIRE); }
void wait_serial(uint64_t s) {
    if (__atomic_load_n(&done_serial, __ATOMIC_ACQUIRE) >= s) return;
    pthread_mutex_lock(&done_mu);
    while (__atomic_load_n(&done_serial, __ATOMIC_ACQUIRE) < s) pthread_cond_wait(&done_cv, &done_mu);
    pthread_mutex_unlock(&done_mu);
}

static uint64_t fnv(const void *p, size_t n, uint64_t h) {
    const uint8_t *b = p;
    for (size_t i = 0; i < n; ++i) h = (h ^ b[i]) * 1099511628211ull;
    return h;
}

// ---------------- formats ----------------
// D3DFORMAT -> Metal pixel format; *conv set when the data needs CPU conversion to BGRA8 on upload.
static MTLPixelFormat mtl_format(uint32_t f, int *conv, MTLTextureSwizzleChannels *sw) {
    *conv = 0;
    *sw = MTLTextureSwizzleChannelsMake(MTLTextureSwizzleRed, MTLTextureSwizzleGreen, MTLTextureSwizzleBlue, MTLTextureSwizzleAlpha);
    switch (f) {
    case 21: return MTLPixelFormatBGRA8Unorm;                                   // A8R8G8B8
    case 22: sw->alpha = MTLTextureSwizzleOne; return MTLPixelFormatBGRA8Unorm; // X8R8G8B8
    case 32: return MTLPixelFormatRGBA8Unorm;                                   // A8B8G8R8
    case 33: sw->alpha = MTLTextureSwizzleOne; return MTLPixelFormatRGBA8Unorm;
    case 0x31545844: return MTLPixelFormatBC1_RGBA;
    case 0x32545844: case 0x33545844: return MTLPixelFormatBC2_RGBA;
    case 0x34545844: case 0x35545844: return MTLPixelFormatBC3_RGBA;
    case 28: return MTLPixelFormatA8Unorm;                                      // A8
    case 50: *sw = MTLTextureSwizzleChannelsMake(MTLTextureSwizzleRed, MTLTextureSwizzleRed, MTLTextureSwizzleRed, MTLTextureSwizzleOne); return MTLPixelFormatR8Unorm;   // L8
    case 51: *sw = MTLTextureSwizzleChannelsMake(MTLTextureSwizzleRed, MTLTextureSwizzleRed, MTLTextureSwizzleRed, MTLTextureSwizzleGreen); return MTLPixelFormatRG8Unorm; // A8L8
    case 34: return MTLPixelFormatRG16Unorm;                                    // G16R16
    case 36: return MTLPixelFormatRGBA16Unorm;
    case 111: return MTLPixelFormatR16Float;
    case 112: return MTLPixelFormatRG16Float;
    case 113: return MTLPixelFormatRGBA16Float;
    case 114: return MTLPixelFormatR32Float;
    case 115: return MTLPixelFormatRG32Float;
    case 116: return MTLPixelFormatRGBA32Float;
    case 75: case 77: case 79: case 71: case 80: case 70: case 83: return MTLPixelFormatDepth32Float_Stencil8;
    case 23: case 24: case 25: case 26: case 30: case 20: case 31: case 35: *conv = 1; return MTLPixelFormatBGRA8Unorm;
    }
    *conv = 1;
    return MTLPixelFormatBGRA8Unorm;
}
static int is_depth_fmt(uint32_t f) { return f == 75 || f == 77 || f == 79 || f == 71 || f == 80 || f == 70 || f == 83; }
// 16/24-bit formats -> BGRA8 (one row)
static void convert_row(uint32_t f, const uint8_t *src, uint8_t *dst, uint32_t w) {
    for (uint32_t x = 0; x < w; ++x) {
        uint32_t r = 0, g = 0, b = 0, a = 255;
        if (f == 20) { b = src[3 * x]; g = src[3 * x + 1]; r = src[3 * x + 2]; }
        else if (f == 31 || f == 35) {
            uint32_t v; memcpy(&v, src + 4 * x, 4);
            uint32_t c0 = (v >> 20) & 1023, c1 = (v >> 10) & 1023, c2 = v & 1023;
            if (f == 35) { r = c0 >> 2; g = c1 >> 2; b = c2 >> 2; } else { b = c0 >> 2; g = c1 >> 2; r = c2 >> 2; }
            a = (v >> 30) * 85;
        } else {
            uint16_t v; memcpy(&v, src + 2 * x, 2);
            switch (f) {
            case 23: r = (v >> 11) & 31; g = (v >> 5) & 63; b = v & 31; r = r * 255 / 31; g = g * 255 / 63; b = b * 255 / 31; break;
            case 24: case 25: r = (v >> 10) & 31; g = (v >> 5) & 31; b = v & 31; r = r * 255 / 31; g = g * 255 / 31; b = b * 255 / 31; a = f == 25 ? ((v >> 15) ? 255 : 0) : 255; break;
            case 26: case 30: r = ((v >> 8) & 15) * 17; g = ((v >> 4) & 15) * 17; b = (v & 15) * 17; a = f == 26 ? (v >> 12) * 17 : 255; break;
            }
        }
        dst[4 * x] = (uint8_t)b; dst[4 * x + 1] = (uint8_t)g; dst[4 * x + 2] = (uint8_t)r; dst[4 * x + 3] = (uint8_t)a;
    }
}

// The GPU texture of a texture object (or surface), created/uploaded on demand.
static int64_t live_tex, live_buf, live_tex_bytes, live_buf_bytes;   // GPU copies of game resources (stats.log)
void render_live_stats(int64_t *tex, int64_t *tex_mb, int64_t *buf, int64_t *buf_mb) {
    *tex = live_tex; *tex_mb = live_tex_bytes >> 20; *buf = live_buf; *buf_mb = live_buf_bytes >> 20;
}
static uint64_t stat_renamed, stat_uploads;
static int64_t live_rt, live_rt_bytes;                               // the render targets among live_tex (diag mem.log)
// diag mem.log (diag.c): render.m's share of the Metal allocations and its caches
void render_mem_stats(int64_t v[16]) {
    v[0] = live_tex - live_rt; v[1] = live_tex_bytes - live_rt_bytes; v[2] = live_rt; v[3] = live_rt_bytes;
    v[4] = live_buf; v[5] = live_buf_bytes; v[6] = (int64_t)stat_renamed; v[7] = (int64_t)stat_uploads;
    v[8] = nshaders; v[9] = (int64_t)pipelines.n; v[10] = (int64_t)dsstates.n; v[11] = nsamplers;
    v[12] = mtl ? (int64_t)mtl.currentAllocatedSize : 0; v[13] = 0; v[14] = 0; v[15] = 0;
}
static MTLTextureDescriptor *texture_descriptor(id<MTLTexture> t) {
    MTLTextureDescriptor *d = [MTLTextureDescriptor new];
    d.textureType = t.textureType; d.pixelFormat = t.pixelFormat;
    d.width = t.width; d.height = t.height; d.depth = t.depth; d.arrayLength = t.arrayLength;
    d.mipmapLevelCount = t.mipmapLevelCount; d.usage = t.usage; d.storageMode = t.storageMode; d.swizzle = t.swizzle;
    return d;
}
RenderTex *gpu_tex(Obj *t) {
    if (!t) return NULL;
    RenderTex *g = t->gpu;
    int is_rt = (t->usage & 1) != 0, is_ds = (t->usage & 2) != 0 || is_depth_fmt(t->fmt);
    if (!g) {
        g = calloc(1, sizeof *g);
        int conv;
        MTLTextureSwizzleChannels sw;
        MTLPixelFormat pf = mtl_format(t->fmt, &conv, &sw);
        MTLTextureDescriptor *d = [MTLTextureDescriptor new];
        d.textureType = t->kind == K_CUBE ? MTLTextureTypeCube : t->kind == K_VOLUME ? MTLTextureType3D : MTLTextureType2D;
        d.pixelFormat = pf;
        d.width = t->w; d.height = t->h; d.depth = t->kind == K_VOLUME ? t->d : 1;
        d.mipmapLevelCount = t->levels ? t->levels : 1;
        d.usage = MTLTextureUsageShaderRead | ((is_rt || is_ds) ? MTLTextureUsageRenderTarget : 0) | MTLTextureUsagePixelFormatView;
        d.storageMode = (is_rt || is_ds) ? MTLStorageModePrivate : MTLStorageModeShared;
        if (!is_ds) d.swizzle = sw;
        g->tex = [mtl newTextureWithDescriptor:d];
        g->w = t->w; g->h = t->h; g->fmt = t->fmt; g->rt = is_rt || is_ds;
        if (t->samples > 1 && g->rt && (t->kind == K_SURFACE || (t->kind == K_TEXTURE && d.mipmapLevelCount == 1))) {
            g->ms = new_ms_texture(pf, t->w, t->h, t->samples);
            g->resolve_pass = t->kind == K_TEXTURE;   // sampled directly by draws: resolved at the end of each pass
        }
        g->bytes = (int64_t)g->tex.allocatedSize;
        ++live_tex; live_tex_bytes += g->bytes;
        if (g->rt) { ++live_rt; live_rt_bytes += g->bytes; }
        t->gpu = g;
        t->dirty = !g->rt;
    }
    if (t->hd_swap && t->hd_tinted != !!(probe_sig & PROBE_HDTEX)) t->dirty = 1;   // BOOT_DIAG_PROBE hdtex: (un)tint
    if (t->dirty && !g->rt) {
        int tint = t->hd_swap && (probe_sig & PROBE_HDTEX);
        int conv;
        MTLTextureSwizzleChannels sw;
        mtl_format(t->fmt, &conv, &sw);
        if (in_flight(g->used)) {                // a pending draw still reads the old contents: new storage
            g->tex = [mtl newTextureWithDescriptor:texture_descriptor(g->tex)];
            ++stat_renamed;
        }
        int faces = t->kind == K_CUBE ? 6 : 1;
        for (int f = 0; f < faces; ++f)
            for (uint32_t l = 0; l < t->levels; ++l) {
                uint32_t m = t->mem[f * 16 + l];
                if (!m) continue;
                uint32_t w = mipdim(t->w, l), h = mipdim(t->h, l), dd = t->kind == K_VOLUME ? mipdim(t->d, l) : 1;
                uint32_t pitch = t->pitch[l], rows = level_rows(t->fmt, h);
                MTLRegion r = MTLRegionMake3D(0, 0, 0, w, h, dd);
                if (!conv) {
                    if (is_dxt(t->fmt) && (w < 4 || h < 4)) { r.size.width = (w + 3) & ~3u; r.size.height = (h + 3) & ~3u; if (w < 4 && h < 4) { r.size.width = w < 4 ? 4 : w; r.size.height = h < 4 ? 4 : h; } }
                    if (is_dxt(t->fmt)) { r.size.width = w; r.size.height = h; }
                    uint8_t *tinted = NULL;
                    if (tint && (tinted = malloc((size_t)pitch * rows * dd))) {
                        memcpy(tinted, G + m, (size_t)pitch * rows * dd);
                        probe_tint(t->fmt, tinted, (size_t)pitch * rows * dd, t->fmt == 21 || t->fmt == 22);
                    }
                    [g->tex replaceRegion:r mipmapLevel:l slice:(NSUInteger)f withBytes:tinted ? tinted : G + m bytesPerRow:pitch bytesPerImage:pitch * rows];
                    free(tinted);
                } else {
                    uint8_t *tmp = malloc((size_t)w * h * dd * 4);
                    for (uint32_t z = 0; z < dd; ++z)
                        for (uint32_t y = 0; y < h; ++y) convert_row(t->fmt, G + m + (z * rows + y) * pitch, tmp + ((size_t)z * h + y) * w * 4, w);
                    if (tint) probe_tint(t->fmt, tmp, (size_t)w * h * dd * 4, 1);
                    [g->tex replaceRegion:r mipmapLevel:l slice:(NSUInteger)f withBytes:tmp bytesPerRow:w * 4 bytesPerImage:(NSUInteger)w * h * 4];
                    free(tmp);
                }
            }
        t->dirty = 0;
        ++stat_uploads;
        if (t->hd_swap) { t->hd_tinted = (uint8_t)tint; if (tint) probe_applied(PROBE_HDTEX, 1); }
    }
    g->used = frame_serial;
    return g;
}

// Static vertex/index buffers (no D3DUSAGE_DYNAMIC) live in a Metal buffer, re-uploaded after Unlock.
typedef struct { id<MTLBuffer> buf; uint64_t used; } RenderBuf;
static id<MTLBuffer> gpu_buf(Obj *b) {
    if (!b || (b->usage & 0x200)) return nil;
    RenderBuf *g = b->gpu;
    if (!g) { g = calloc(1, sizeof *g); b->gpu = g; b->dirty = 1; }
    if (b->dirty || !g->buf) {
        if (!g->buf || g->buf.length < b->size || in_flight(g->used)) {
            if (g->buf && g->buf.length >= b->size) ++stat_renamed;
            if (g->buf) live_buf_bytes -= (int64_t)g->buf.length; else ++live_buf;
            g->buf = [mtl newBufferWithLength:b->size ? b->size : 16 options:MTLResourceStorageModeShared];
            live_buf_bytes += (int64_t)g->buf.length;
        }
        memcpy(g->buf.contents, G + b->mem[0], b->size);
        b->dirty = 0;
    }
    g->used = frame_serial;
    return g->buf;
}

void render_release(Obj *o) {
    if (o->gpu && (o->kind == K_VB || o->kind == K_IB)) {
        RenderBuf *g = o->gpu;
        if (g->buf) { --live_buf; live_buf_bytes -= (int64_t)g->buf.length; }
        g->buf = nil;
        free(g);
        o->gpu = NULL;
        return;
    }
    if (o->gpu && (o->kind == K_TEXTURE || o->kind == K_CUBE || o->kind == K_VOLUME || o->kind == K_SURFACE)) {
        RenderTex *g = o->gpu;
        if (g->ms) { msaa_forget(g); drop_ms_texture(g); }
        if (g->tex) { --live_tex; live_tex_bytes -= g->bytes; if (g->rt) { --live_rt; live_rt_bytes -= g->bytes; } }
        g->tex = nil;
        free(g);
        o->gpu = NULL;
    }
}

// The RenderTex of a surface that has its own GPU texture (not a texture level), or of the multisampled texture whose
// level 0 it is (render_target_msaa): where MSAA state lives.
RenderTex *surface_rt(uint32_t s) {
    if (!s) return NULL;
    if (s == dev.backbuffer) return &bb_rt;
    if (s == dev.autodepth) return &ad_rt;
    Obj *o = obj(s);
    if (o && o->parent) {
        Obj *t = obj(o->parent);
        RenderTex *g = t ? t->gpu : NULL;
        return g && g->ms && o->level == 0 && o->face == 0 ? g : NULL;
    }
    return o ? o->gpu : NULL;
}
// Render-target view of a surface: (texture, level, slice). For a multisampled surface, its multisampled texture.
id<MTLTexture> surface_target(uint32_t s, NSUInteger *level, NSUInteger *slice) {
    *level = 0; *slice = 0;
    if (!s) return nil;
    if (s == dev.backbuffer) return bb_rt.ms ? bb_rt.ms : backbuffer_tex;
    if (s == dev.autodepth) return ad_rt.ms ? ad_rt.ms : autodepth_tex;
    Obj *o = obj(s);
    if (!o) return nil;
    if (o->parent) {
        Obj *t = obj(o->parent);
        RenderTex *g = gpu_tex(t);
        *level = o->level; *slice = o->face;
        if (g && g->ms && !o->level && !o->face) return g->ms;   // a multisampled texture (render_target_msaa)
        return g ? g->tex : nil;
    }
    if (!o->usage) o->usage = is_depth_fmt(o->fmt) ? 2 : 1;
    RenderTex *g = gpu_tex(o);
    return g ? (g->ms ? g->ms : g->tex) : nil;
}

// ---------------- setup ----------------
static const char *blit_src =
    "#include <metal_stdlib>\nusing namespace metal;\n"
    "struct V { float4 pos [[position]]; float2 uv; };\n"
    "vertex V blit_vs(uint vid [[vertex_id]], constant float4 *r [[buffer(0)]]) {\n"
    "  float2 p = float2(vid & 1, vid >> 1); V o; o.pos = float4(mix(r[0].xy, r[0].zw, p), 0, 1); o.uv = mix(r[1].xy, r[1].zw, p); return o; }\n"
    "fragment float4 blit_fs(V i [[stage_in]], texture2d<float> t [[texture(0)]], sampler s [[sampler(0)]]) { return t.sample(s, i.uv); }\n";

// Startup-only hidden EDR transport; SDR composition remains the default.
void render_hdr_output_init(void);
int render_hdr_output_enabled(void);

void render_init(void) {
    if (!mtl) return;
    render_hdr_output_init();
    hdrclip_init();
    const char *test_ring = getenv("NFS_TEST_RING_SIZE");   // test only, bytes; force spills without a huge scene
    if (test_ring) {
        unsigned long n = strtoul(test_ring, NULL, 10);
        if (n >= 256 && n <= RING_SIZE) ring_size = (uint32_t)n & ~255u;
    }
    for (int i = 0; i < 3; ++i) ring[i] = [mtl newBufferWithLength:ring_size options:MTLResourceStorageModeShared];
    zero_buf = [mtl newBufferWithLength:256 options:MTLResourceStorageModeShared];
    vis_buf = [mtl newBufferWithLength:VIS_SLOTS * 8 options:MTLResourceStorageModeShared];
    gpt_init();                                        // BOOT_DIAG_GPUPASS=1 (gpupass.m)
    MTLTextureDescriptor *d = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:1 height:1 mipmapped:NO];
    zero_tex2d = [mtl newTextureWithDescriptor:d];
    uint32_t black = 0xFF000000;
    [zero_tex2d replaceRegion:MTLRegionMake2D(0, 0, 1, 1) mipmapLevel:0 withBytes:&black bytesPerRow:4];
    probe_magenta = [mtl newTextureWithDescriptor:d];  // the roadrefl signature of BOOT_DIAG_PROBE (probe.m)
    uint32_t magenta = 0xFFFF00FF;
    [probe_magenta replaceRegion:MTLRegionMake2D(0, 0, 1, 1) mipmapLevel:0 withBytes:&magenta bytesPerRow:4];
    d.textureType = MTLTextureTypeCube;
    zero_texcube = [mtl newTextureWithDescriptor:d];
    for (int f = 0; f < 6; ++f) [zero_texcube replaceRegion:MTLRegionMake2D(0, 0, 1, 1) mipmapLevel:0 slice:(NSUInteger)f withBytes:&black bytesPerRow:4 bytesPerImage:4];
    d.textureType = MTLTextureType3D;
    zero_tex3d = [mtl newTextureWithDescriptor:d];
    [zero_tex3d replaceRegion:MTLRegionMake3D(0, 0, 0, 1, 1, 1) mipmapLevel:0 slice:0 withBytes:&black bytesPerRow:4 bytesPerImage:4];
    // a shadow sampler with no depth texture bound reads depth 1 (fully lit); probe_depth reads 0 (in shadow: the
    // shadows signature of BOOT_DIAG_PROBE, probe.m)
    MTLTextureDescriptor *dd = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatDepth32Float_Stencil8 width:1 height:1 mipmapped:NO];
    dd.usage = MTLTextureUsageShaderRead | MTLTextureUsageRenderTarget;
    dd.storageMode = MTLStorageModePrivate;
    zero_depth = [mtl newTextureWithDescriptor:dd];
    probe_depth = [mtl newTextureWithDescriptor:dd];
    for (int k = 0; k < 2; ++k) {
        id<MTLTexture> t = k ? probe_depth : zero_depth;
        MTLRenderPassDescriptor *rp = [MTLRenderPassDescriptor renderPassDescriptor];
        rp.depthAttachment.texture = t; rp.depthAttachment.loadAction = MTLLoadActionClear; rp.depthAttachment.clearDepth = k ? 0.0 : 1.0;
        rp.depthAttachment.storeAction = MTLStoreActionStore;
        rp.stencilAttachment.texture = t; rp.stencilAttachment.loadAction = MTLLoadActionClear; rp.stencilAttachment.storeAction = MTLStoreActionStore;
        id<MTLCommandBuffer> cb = [queue commandBuffer];
        [[cb renderCommandEncoderWithDescriptor:rp] endEncoding];
        [cb commit];
    }
    NSError *err = nil;
    id<MTLLibrary> lib = blit_lib = [mtl newLibraryWithSource:[NSString stringWithUTF8String:blit_src] options:nil error:&err];
    MTLRenderPipelineDescriptor *pd = [MTLRenderPipelineDescriptor new];
    pd.vertexFunction = [lib newFunctionWithName:@"blit_vs"];
    pd.fragmentFunction = [lib newFunctionWithName:@"blit_fs"];
    pd.colorAttachments[0].pixelFormat = MTLPixelFormatBGRA8Unorm;
    blit_pso = pipeline_build(pd, __func__, &err);
    if (!blit_pso) fprintf(stderr, "boot: render: blit pipeline: %s\n", err.localizedDescription.UTF8String);
    MTLSamplerDescriptor *sd = [MTLSamplerDescriptor new];
    sd.minFilter = sd.magFilter = MTLSamplerMinMagFilterLinear;
    blit_linear = [mtl newSamplerStateWithDescriptor:sd];
    sd.minFilter = sd.magFilter = MTLSamplerMinMagFilterNearest;
    blit_point = [mtl newSamplerStateWithDescriptor:sd];
    render_resize();
    if(diag_on && getenv("BOOT_DIAG_FLOAT_SCENE_TEST")) {
        int good=float_scene_test(0),bad=float_scene_test(1);
        diag_event("float_scene_test: normal=%d mutant=%d expected=0/1 %s",good,bad,good==0&&bad==1?"PASS":"FAIL");
    }
    uint64_t pipeline_load_start = diag_on ? host_ticks() : 0;
    shader_cache_warm();
    ff_shader_warm();
    render_contact_warm();
    dispatch_async(shader_workers[0], ^{ @autoreleasepool { render_post_prewarm(); }});
    const char *prewarm=getenv("NFS_PSO_PREWARM");
    if(prewarm && atoi(prewarm)) dispatch_async(shader_workers[1], ^{ @autoreleasepool { if(!ssao_depth_warm() && diag_on)diag_event("pipeline_prewarm: failed=ao_depth"); }});
    // Device construction is the load boundary, before the first scene frame.
    // Join the eager catalogue here; no draw waits for a compiler or drops known
                                                                                  
    for (int i = 0; i < 4; ++i) dispatch_sync(shader_workers[i], ^{});
    for (int i = 0; i < nshaders; ++i) pipeline_register_shader(shaders[i]->key, shaders[i]->fn);
    pipeline_prewarm(shader_workers, bb_rt.ms ? bb_rt.ms.sampleCount : 1);
    for (int i = 0; i < 4; ++i) dispatch_sync(shader_workers[i], ^{});
    if (diag_on) diag_event("pipeline_prewarm: loading_ms=%.3f", (host_ticks()-pipeline_load_start)/1e6);
    pipeline_loading_done();
    if (diag_on) diag_event("shader_compile: load_ready=%d frame_sync=%llu", nshaders, stat_sync_shader);
}

void render_resize(void) {
    if (!mtl) return;
    if(fs_active)render_float_scene_final(0);
    render_float_scene_present_end();
    drop_ms_texture(&fs_composite_rt);fs_composite_rt=(RenderTex){0};
    drop_ms_texture(&fs_world_rt);fs_world_rt=(RenderTex){0};fs_display_rt=(RenderTex){0};fs_serial=0;
    uint32_t w = dev.pp[0], h = dev.pp[1];
    MTLTextureDescriptor *d = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:w height:h mipmapped:NO];
    d.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead;
    d.storageMode = MTLStorageModePrivate;
    backbuffer_tex = [mtl newTextureWithDescriptor:d];
    present_tex = [mtl newTextureWithDescriptor:d];
    d.pixelFormat = MTLPixelFormatDepth32Float_Stencil8;
    d.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead;   // the X360 post passes read scene depth
    autodepth_tex = [mtl newTextureWithDescriptor:d];
    msaa_forget(&bb_rt); msaa_forget(&ad_rt);
    drop_ms_texture(&bb_rt); drop_ms_texture(&ad_rt);
    bb_rt.tex = backbuffer_tex; ad_rt.tex = autodepth_tex;
    uint32_t samples = render_msaa_count(dev.pp[4], dev.pp[5]);
    if (samples > 1) {
        bb_rt.ms = new_ms_texture(MTLPixelFormatBGRA8Unorm, w, h, samples);
        if (hdrclip_on) { // Only the diagnostic reads individual scene samples.
            msaa_bytes -= bb_rt.ms.allocatedSize;
            MTLTextureDescriptor *hd=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:w height:h mipmapped:NO];
            hd.textureType=MTLTextureType2DMultisample;hd.sampleCount=samples;
            hd.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;hd.storageMode=MTLStorageModePrivate;
            bb_rt.ms=[mtl newTextureWithDescriptor:hd];msaa_bytes+=bb_rt.ms.allocatedSize;
        }
        if (dev.pp[9]) ad_rt.ms = new_ms_texture(MTLPixelFormatDepth32Float_Stencil8, w, h, samples);
        fprintf(stderr, "boot: render: %ux MSAA back buffer and depth, %lld MB\n", samples, (long long)(msaa_bytes >> 20));
    }
    pass_dirty = 1;
}

// BEGIN float scene routing (float_scene_test.py executes these production helpers).
// bb_rt always describes the currently bound phase. End encoders before swapping
// it so enc_ms pointers, on-store resolves, pending clears and read histories keep
// their original contract. Auto depth is shared, never reformatted or cleared here.
void render_float_scene_begin(void) {
    if(!float_scene_on || fs_active || fs_serial==frame_serial)return;
    if(diag_on && postfx_look_variant("floatoff"))return;
    fs_headroom=render_hdr_headroom();
    fs_hud=0;
    end_pass();
    if(!fs_world_rt.tex) {
        MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA16Float width:bb_rt.tex.width height:bb_rt.tex.height mipmapped:NO];
        d.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;d.storageMode=MTLStorageModePrivate;
        fs_world_rt.tex=[mtl newTextureWithDescriptor:d];
        if(bb_rt.ms)fs_world_rt.ms=new_ms_texture(d.pixelFormat,d.width,d.height,(uint32_t)bb_rt.ms.sampleCount);
    }
    if(!fs_world_rt.tex || (bb_rt.ms && !fs_world_rt.ms))abort();
    msaa_resolve(&bb_rt);fs_display_rt=bb_rt;
    // The game's frame clear and pre-world FE pass have already run. Seed their
    // contents rather than dropping that clear or changing the shared depth.
    id<MTLTexture> target=fs_world_rt.ms?fs_world_rt.ms:fs_world_rt.tex;
    MTLRegion r=MTLRegionMake2D(0,0,target.width,target.height);
    blit_quad(bb_rt.tex,r,target,0,0,r,0);if(fs_world_rt.ms)fs_world_rt.stale=1;
    bb_rt=fs_world_rt;backbuffer_tex=bb_rt.tex;
    fs_active=1;fs_serial=frame_serial;pass_dirty=1;
}
void render_float_scene_final(int preserve) {
    if(preserve && fs_composing) {
        if(!fs_toned) {
            // A guest/untreated fallback also finishes its world tone before
            // HUD. Use the retired world surface to avoid reading/writing the
            // same attachment, then retain the original composition samples.
            end_pass();msaa_resolve(&bb_rt);
            render_hdr_world_copy(bb_rt.tex,fs_world_rt.tex);
            MTLRegion r=MTLRegionMake2D(0,0,bb_rt.tex.width,bb_rt.tex.height);
            blit_quad(fs_world_rt.tex,r,bb_rt.ms?:bb_rt.tex,0,0,r,0);
            if(bb_rt.ms)bb_rt.stale=1;
            fs_toned=1;
        }
        fs_hud=1;
    }
    if(!fs_active)return;
    end_pass();msaa_resolve(&bb_rt);
    fs_world_rt=bb_rt;bb_rt=fs_display_rt;backbuffer_tex=bb_rt.tex;fs_active=0;pass_dirty=1;
    if(fs_headroom>1) {
        if(!fs_composite_rt.tex) {
            MTLTextureDescriptor *d=texture_descriptor(fs_world_rt.tex);
            fs_composite_rt.tex=[mtl newTextureWithDescriptor:d];
            if(bb_rt.ms)fs_composite_rt.ms=new_ms_texture(d.pixelFormat,d.width,d.height,(uint32_t)bb_rt.ms.sampleCount);
        }
        if(!fs_composite_rt.tex || (bb_rt.ms && !fs_composite_rt.ms))abort();
        bb_rt=fs_composite_rt;backbuffer_tex=bb_rt.tex;fs_composing=1;
        fs_toned=0;
        fs_hud=preserve!=0;
        // With no treatment, tone the raw world before the first HUD write.
        // Otherwise the EDR post variant supplies all composition pixels.
        if(preserve) {
            render_hdr_world_copy(fs_world_rt.tex,bb_rt.ms?:bb_rt.tex);
            fs_toned=1;
            if(bb_rt.ms)bb_rt.stale=1;
        }
        return;
    }
    // No treatment this frame: retain the raw world behind the HUD/FE. The usual
    // post path supplies every destination pixel itself and needs no extra copy.
    if(preserve) {
        id<MTLTexture> d=bb_rt.ms?bb_rt.ms:bb_rt.tex;
        MTLRegion r=MTLRegionMake2D(0,0,d.width,d.height);
        blit_quad(fs_world_rt.tex,r,d,0,0,r,0);
        if(bb_rt.ms)bb_rt.stale=1;
    }
}
float render_hdr_composition_headroom(void) { return fs_composing?fs_headroom:1; }
void render_hdr_scene_treated(void) { if(fs_composing)fs_toned=1; }
void render_float_scene_present_end(void) {
    if(!fs_composing)return;
    fs_composite_rt=bb_rt;bb_rt=fs_display_rt;backbuffer_tex=bb_rt.tex;
    fs_composing=fs_hud=0;pass_dirty=1;
}
static unsigned float_scene_store_policy(MTLPixelFormat format,uint32_t target) {
    if(format!=MTLPixelFormatRGBA16Float)return 0;
    // Preserve the legacy UNORM source range for FE/HUD/movie shaders (some
    // include a gain of two), while the already-toned world remains extended.
    return fs_hud && target==dev.backbuffer?2:1;
}
// Only actual pre-post screen-copy destinations are promoted, on their first
// write from the float scene. Parent texture storage is what sampling observes.
static void float_scene_copy(uint32_t dst,MTLPixelFormat format) {
    Obj *o=obj(dst);if(!o || dst==dev.backbuffer)return;
    Obj *t=o->parent?obj(o->parent):o;
    if(!t || (t->kind!=K_TEXTURE && t->kind!=K_SURFACE) ||
       (t->fmt!=21 && t->fmt!=22 && t->fmt!=32 && t->fmt!=33))return;
    if(format==MTLPixelFormatBGRA8Unorm && (t->fmt==32 || t->fmt==33))format=MTLPixelFormatRGBA8Unorm;
    RenderTex *g=t?gpu_tex(t):NULL;
    if(!g || !g->rt || g->tex.width!=dev.pp[0] || g->tex.height!=dev.pp[1] || g->tex.pixelFormat==format)return;
    if(g->tex.pixelFormat!=MTLPixelFormatBGRA8Unorm && g->tex.pixelFormat!=MTLPixelFormatRGBA8Unorm && g->tex.pixelFormat!=MTLPixelFormatRGBA16Float)return;
    end_pass();msaa_resolve(g);
    id<MTLTexture> old=g->tex;
    MTLTextureDescriptor *d=texture_descriptor(old);d.pixelFormat=format;
    g->tex=[mtl newTextureWithDescriptor:d];
    uint32_t samples=(uint32_t)g->ms.sampleCount;
    msaa_forget(g);drop_ms_texture(g);
    if(samples>1)g->ms=new_ms_texture(format,d.width,d.height,samples);
    int64_t delta=(int64_t)g->tex.allocatedSize-g->bytes;
    g->bytes+=delta;live_tex_bytes+=delta;live_rt_bytes+=delta;
    // Seed uncovered pixels as well (partial StretchRects preserve their outside).
    MTLRegion r=MTLRegionMake2D(0,0,d.width,d.height);
    blit_quad(old,r,g->ms?g->ms:g->tex,0,0,r,0);
    if(g->ms)g->stale=1;
}
void render_float_scene_post_target(uint32_t surface) {
    if(float_scene_on)float_scene_copy(surface,fs_composing?MTLPixelFormatRGBA16Float:MTLPixelFormatBGRA8Unorm);
}
// END float scene routing

// Diagnostic transport sentinel, after the world and before its first screen
// copy. One bottom row: [0,8] in 1/128 steps and a NaN/Inf pixel. Actual MSAA,
// resolve and StretchRect follow; this is separate from the unmodified race pair.
static void float_scene_ramp(void) {
    if(!diag_on || !hdrclip_on || !fs_active || !getenv("BOOT_DIAG_FLOAT_SCENE_RAMP") || hc_select(HC_WORLD)>=HC_STAGES)return;
    static uint64_t done;static id<MTLRenderPipelineState> pso;static NSUInteger samples;
    if(done==frame_serial)return;done=frame_serial;
    id<MTLTexture> t=bb_rt.ms?bb_rt.ms:bb_rt.tex;
    if(t.width<1026)return;
    end_pass();ensure_cmd();
    if(!pso || samples!=t.sampleCount) {
        NSString *src=@"#include <metal_stdlib>\nusing namespace metal;\nvertex float4 vs(uint v [[vertex_id]]){float2 p=float2((v<<1)&2,v&2);return float4(p*2-1,0,1);}\nfragment float4 fs(float4 p [[position]]){float x=floor(p.x)/128.0f;if(p.x>1025)return as_type<float4>(uint4(0x7fc00000u,0x7f800000u,0xff800000u,0x7fc00000u));return float4(x,x,x,1);}";
        NSError *error=nil;id<MTLLibrary> lib=[mtl newLibraryWithSource:hdrclip_world_source(src,UINT64_MAX,0,0) options:nil error:&error];
        MTLRenderPipelineDescriptor *pd=[MTLRenderPipelineDescriptor new];pd.vertexFunction=[lib newFunctionWithName:@"vs"];pd.fragmentFunction=[lib newFunctionWithName:@"fs"];pd.colorAttachments[0].pixelFormat=t.pixelFormat;pd.rasterSampleCount=t.sampleCount;
        pso=pipeline_build(pd, __func__, &error);samples=t.sampleCount;
        if(!pso){diag_event("float scene ramp compile: %s",error.localizedDescription.UTF8String);return;}
    }
    MTLRenderPassDescriptor *rp=[MTLRenderPassDescriptor renderPassDescriptor];rp.colorAttachments[0].texture=t;rp.colorAttachments[0].loadAction=MTLLoadActionLoad;rp.colorAttachments[0].storeAction=MTLStoreActionStore;
    id<MTLRenderCommandEncoder> e=[cmd renderCommandEncoderWithDescriptor:rp];[e setRenderPipelineState:pso];
    [e setViewport:(MTLViewport){0,t.height-1,1026,1,0,1}];[e setScissorRect:(MTLScissorRect){0,t.height-1,1026,1}];
    unsigned store=1;[e setFragmentBytes:&store length:4 atIndex:27];hdrclip_bind(e,HC_WORLD);
    [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[e endEncoding];
    if(bb_rt.ms)bb_rt.stale=1;pass_dirty=1;
}

// ---------------- command stream ----------------
// What the current render encoder has bound, so state that didn't change since the last draw isn't set again.
// Reset whenever a new encoder starts. Object pointers are only compared while the encoder (whose command buffer
// retains everything bound) is alive.
enum { EB_STENCILREF = 1, EB_CULL = 2, EB_VIEWPORT = 4, EB_SCISSOR = 8, EB_BIAS = 16, EB_VSI = 32, EB_VSB = 64, EB_PSI = 128,
       EB_PSB = 256, EB_VSAUX = 512, EB_PSAUX = 1024, EB_BLENDC = 2048 };
static struct {
    uint32_t have;
    void *pso, *dss, *ftex[16], *fsamp[16], *vtex[4], *vsamp[4], *cbuf[2];
    uint32_t stencil_ref, cull, bias, slope, blend_color, coff[2];
    uint32_t ver_vsi, ver_vsb, ver_psi, ver_psb;
    MTLViewport vp;
    MTLScissorRect sc;
    SmVSAux va;
    SmPSAux pa;
} eb;
void ensure_cmd(void) {
    if (!cmd) { cmd = rt_command_buffer(); }
}
// Multisampled attachments of the open encoder (colour 0-3, depth 4).
RenderTex *enc_ms[5];
uint32_t enc_samples = 1;    // the open encoder's sample count (occlusion counts are in samples)
uint32_t q_active;           // the occlusion query between Issue(BEGIN) and Issue(END) (guest handle)
// Police lamp-glow batching. An accepted cop flare's replay into lamp_rt is recorded
// (lamp_record: enc is a lampbatch.m recorder, lamp_replay 2) while the scene encoder stays open, and every
// recorded replay is invoked in order on one lamp encoder (lamp_flush) before anything can change the depth or
// stencil the replays test against: a scene draw that writes depth/stencil (render_draw), any encoder change
// (end_pass: target switch, clear, blit, present, lamp_rt resize), another depth target, the bloom reading
// lamp_rt. So each replay sees the same depth/stencil and blends in the same order as an immediate replay, with
// two encoders per frame instead of ~3 per flare at 4K MSAA. NFS_LAMP_BATCH=0: the immediate replay (lamp_replay 1).
static id lamp_rec;
static int lamp_flushing;
static __strong id<MTLTexture> lamp_z;        // their depth target
static unsigned lamp_owner;
static uint32_t lamp_viewport[6], lamp_scissor[4], lamp_scissor_on;
static __strong id<MTLRenderCommandEncoder> lamp_saved_enc;   // the scene encoder while a replay records
static typeof(eb) lamp_eb, lamp_saved_eb;
static typeof(eb) ssao_eb,ssao_reference_eb,ssao_cpu_reference_eb,ssao_saved_eb;
static id<MTLRenderCommandEncoder> ssao_saved_enc;
static RenderTex *ssao_saved_ms[5];
static uint8_t ssao_saved_resolve[5];
static int ssao_saved_dirty;
static uint32_t ssao_saved_samples;
static RenderTex *lamp_saved_ms[5];
static uint8_t lamp_saved_resolve[5];
static int lamp_saved_dirty;
static uint32_t lamp_saved_samples;
void end_pass(void) {
    if (ssao_replay==2) {
        if (ssao_saved_enc) { [ssao_saved_enc endEncoding];ssao_saved_enc=nil; }
        for (int i=0;i<5;++i) if (ssao_saved_resolve[i]&&ssao_saved_ms[i]) ssao_saved_ms[i]->stale=0;
        memset(ssao_saved_ms,0,sizeof ssao_saved_ms);memset(ssao_saved_resolve,0,sizeof ssao_saved_resolve);
        ssao_saved_dirty=1;return;
    }
    if (lamp_replay == 2) {          // an encoder change while a replay records: end the real (scene) encoder
        if (lamp_saved_enc) { [lamp_saved_enc endEncoding]; lamp_saved_enc = nil; }
        for (int i = 0; i < 5; ++i) if (lamp_saved_resolve[i] && lamp_saved_ms[i]) lamp_saved_ms[i]->stale = 0;
        memset(lamp_saved_ms, 0, sizeof lamp_saved_ms); memset(lamp_saved_resolve, 0, sizeof lamp_saved_resolve);
        lamp_saved_dirty = 1;
        return;
    }
    if (enc) { [enc endEncoding]; enc = nil; }
    for (int i = 0; i < 5; ++i) if (enc_resolve[i] && enc_ms[i]) enc_ms[i]->stale = 0;   // resolved on store (ms_attach)
    memset(enc_ms, 0, sizeof enc_ms); memset(enc_resolve, 0, sizeof enc_resolve);
    pass_dirty = 1;
    if (lamp_n && !lamp_flushing) lamp_flush(0);
}
// The lamp target's encoder: lamp_rt colour (cleared by the frame's first lamp pass), the scene depth/stencil z.
static void lamp_open(id<MTLTexture> z, unsigned owner) {
        end_pass(); ensure_cmd();
        MTLRenderPassDescriptor *rp = [MTLRenderPassDescriptor renderPassDescriptor];
        rp.colorAttachments[0].texture = lamp_rt.ms ? lamp_rt.ms : lamp_rt.tex;
        rp.colorAttachments[0].loadAction = lamp_serial == frame_serial ? MTLLoadActionLoad : MTLLoadActionClear;
        rp.colorAttachments[0].storeAction = MTLStoreActionStore;
        if (lamp_rt.ms) ms_attach(rp.colorAttachments[0], &lamp_rt, 0);
        rp.depthAttachment.texture = z; rp.depthAttachment.loadAction = MTLLoadActionLoad; rp.depthAttachment.storeAction = MTLStoreActionStore;
        if (z.pixelFormat == MTLPixelFormatDepth32Float_Stencil8) {
            rp.stencilAttachment.texture = z; rp.stencilAttachment.loadAction = MTLLoadActionLoad; rp.stencilAttachment.storeAction = MTLStoreActionStore;
        }
        if (gpt_on) gpt_render_auto(rp, "lamp");
        enc = [cmd renderCommandEncoderWithDescriptor:rp];
        if (gpt_on) gpt_name(enc);
        lamp_serial = frame_serial; enc_samples = (uint32_t)rp.colorAttachments[0].texture.sampleCount;
        if (owner >= 1 && owner <= 2) lamp_valid[owner - 1] = frame_serial;
        memset(&eb, 0, sizeof eb); pass_dirty = 0;
}
static void lamp_flush(int why) {
    if (!lamp_n || lamp_flushing || lamp_replay) return;
    lamp_flushing = 1;
    lampglow_batch(why, lamp_n, lamprec_refused(lamp_rec));
    lamp_open(lamp_z, lamp_owner);
    lamprec_replay(lamp_rec, enc);
    end_pass();
    lamp_n = 0; lamp_z = nil;
    lamp_flushing = 0;
}
static int ssao_can_record(void) {
    return x360_post_ssao_enabled() && x360_post_chain_on() && M32(0x00925E90u)==6 &&
        M32(0x00901828u) && M32(0x00982AF0u) && M8(0x008F9B5Cu);
}
static const MTLSamplePosition ssao_samples[4]={{.25f,.25f},{.75f,.25f},{.25f,.75f},{.75f,.75f}};
// A half-res four-sample grid retains the original 2x2 pixel centers. D16
// depth is padded by one quantization step to keep rejection conservative.
// Replay skips coarse alpha clipping; the full-res proof retains original alpha.
static const char *ssao_depth_src=
"#include <metal_stdlib>\nusing namespace metal;\n"
"vertex float4 coverage_vs(uint v [[vertex_id]]) {float2 p=float2((v<<1)&2,v&2);return float4(p*float2(2,-2)+float2(-1,1),0,1);}\n"
"struct CoverageDepth {float value [[depth(any)]];};\n"
"fragment CoverageDepth coverage_fs(float4 p [[position]],uint s [[sample_id]],depth2d<float> z [[texture(0)]]) {\n"
" const uint2 offsets[4]={uint2(0,0),uint2(1,0),uint2(0,1),uint2(1,1)};\n"
" return CoverageDepth{min(1.0,z.read(min(uint2(p.xy)*2+offsets[s],uint2(z.get_width()-1,z.get_height()-1)))+1.0/65535.0)};}\n";
static int ssao_prepare(id<MTLTexture> z) {
    if (!z || z.width != backbuffer_tex.width || z.height != backbuffer_tex.height) return 0;
    NSUInteger w=(z.width+1)/2,h=(z.height+1)/2;
    if (!ssao_mask.tex || ssao_mask.w != w || ssao_mask.h != h) {
        end_pass();ssao_mask.ms=nil;ssao_mask.stale=0;
        MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatR8Unorm width:w height:h mipmapped:NO];
        d.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;d.storageMode=MTLStorageModePrivate;
        ssao_mask.tex=[mtl newTextureWithDescriptor:d];ssao_mask.w=(uint32_t)w;ssao_mask.h=(uint32_t)h;
        d.textureType=MTLTextureType2DMultisample;d.sampleCount=4;ssao_mask.ms=[mtl newTextureWithDescriptor:d];
        d.pixelFormat=MTLPixelFormatDepth16Unorm;d.usage=MTLTextureUsageRenderTarget;
        ssao_half_depth=[mtl newTextureWithDescriptor:d];ssao_serial=ssao_half_serial=0;
    }
    if (ssao_proof()&&(!ssao_reference_mask.tex||ssao_reference_mask.w!=z.width||ssao_reference_mask.h!=z.height)) {
        MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatR8Unorm width:z.width height:z.height mipmapped:NO];
        d.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;d.storageMode=MTLStorageModePrivate;
        ssao_reference_mask.tex=[mtl newTextureWithDescriptor:d];ssao_reference_mask.w=(uint32_t)z.width;ssao_reference_mask.h=(uint32_t)z.height;
    }
    if(ssao_proof()&&(!ssao_cpu_reference_mask.tex||ssao_cpu_reference_mask.w!=w||ssao_cpu_reference_mask.h!=h)){
        MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatR8Unorm width:w height:h mipmapped:NO];
        d.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;d.storageMode=MTLStorageModePrivate;
        ssao_cpu_reference_mask.tex=[mtl newTextureWithDescriptor:d];ssao_cpu_reference_mask.w=(uint32_t)w;ssao_cpu_reference_mask.h=(uint32_t)h;
    }
    // Proof-only baseline resolves the original NSInvocation list separately.
    if(ssao_proof())ssao_cpu_reference_mask.ms=ssao_mask.ms;
    return ssao_mask.tex&&ssao_mask.ms&&ssao_half_depth&&(!ssao_proof()||ssao_reference_mask.tex);
}
static id<MTLRenderPipelineState> ssao_depth_pipeline;
static id<MTLDepthStencilState> ssao_write_depth;
static int ssao_depth_warm(void) {
    if (!ssao_depth_pipeline) {
        NSError *error=nil;id<MTLLibrary> library=[mtl newLibraryWithSource:[NSString stringWithUTF8String:ssao_depth_src] options:nil error:&error];
        MTLRenderPipelineDescriptor *d=[MTLRenderPipelineDescriptor new];d.vertexFunction=[library newFunctionWithName:@"coverage_vs"];d.fragmentFunction=[library newFunctionWithName:@"coverage_fs"];
        d.rasterSampleCount=4;d.depthAttachmentPixelFormat=MTLPixelFormatDepth16Unorm;
        ssao_depth_pipeline=pipeline_build(d, "ao_depth", &error);
        MTLDepthStencilDescriptor *dd=[MTLDepthStencilDescriptor new];dd.depthCompareFunction=MTLCompareFunctionAlways;dd.depthWriteEnabled=YES;ssao_write_depth=[mtl newDepthStencilStateWithDescriptor:dd];
        if (!ssao_depth_pipeline) {fprintf(stderr,"boot: SSAO depth downsample: %s\n",error.localizedDescription.UTF8String);return 0;}
    }
    return ssao_depth_pipeline != nil;
}
static int ssao_depth_prepare(id<MTLTexture> z) {
    if (!ssao_depth_warm()) return 0;
    end_pass();ensure_cmd();
    MTLRenderPassDescriptor *rp=[MTLRenderPassDescriptor renderPassDescriptor];
    rp.depthAttachment.texture=ssao_half_depth;rp.depthAttachment.loadAction=MTLLoadActionDontCare;rp.depthAttachment.storeAction=MTLStoreActionStore;
    [rp setSamplePositions:ssao_samples count:4];
    if(gpt_on)gpt_render_auto(rp,"ssao half depth");
    id<MTLRenderCommandEncoder> e=[cmd renderCommandEncoderWithDescriptor:rp];if(gpt_on)gpt_name(e);
    [e setRenderPipelineState:ssao_depth_pipeline];[e setDepthStencilState:ssao_write_depth];[e setFragmentTexture:z atIndex:0];
    [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[e endEncoding];ssao_half_serial=frame_serial;return 1;
}
static void ssao_open(id<MTLTexture> z) {
    end_pass();ensure_cmd();RenderTex *target=ssao_target();
    MTLRenderPassDescriptor *rp=[MTLRenderPassDescriptor renderPassDescriptor];
    rp.colorAttachments[0].texture=target->ms?target->ms:target->tex;
    rp.colorAttachments[0].loadAction=ssao_reference_record||ssao_cpu_reference_record||ssao_cleared!=frame_serial?MTLLoadActionClear:MTLLoadActionLoad;
    rp.colorAttachments[0].clearColor=MTLClearColorMake(1,1,1,1);rp.colorAttachments[0].storeAction=MTLStoreActionStore;
    if(target->ms){rp.colorAttachments[0].resolveTexture=target->tex;rp.colorAttachments[0].storeAction=MTLStoreActionStoreAndMultisampleResolve;[rp setSamplePositions:ssao_samples count:4];}
    rp.depthAttachment.texture=z;rp.depthAttachment.loadAction=MTLLoadActionLoad;rp.depthAttachment.storeAction=MTLStoreActionStore;
    if(z.pixelFormat==MTLPixelFormatDepth32Float_Stencil8){rp.stencilAttachment.texture=z;rp.stencilAttachment.loadAction=MTLLoadActionDontCare;rp.stencilAttachment.storeAction=MTLStoreActionDontCare;}
    if(gpt_on)gpt_render_auto(rp,ssao_reference_record?"ssao reference coverage":"ssao half coverage");
    enc=[cmd renderCommandEncoderWithDescriptor:rp];if(gpt_on)gpt_name(enc);enc_samples=(uint32_t)rp.colorAttachments[0].texture.sampleCount;
    memset(&eb,0,sizeof eb);pass_dirty=0;if(!ssao_reference_record)ssao_cleared=frame_serial;
}
id<MTLTexture> render_ssao_coverage(id<MTLTexture> depth) {
    if (!x360_post_ssao_enabled() || !ssao_mask.tex || ssao_serial!=frame_serial || ssao_depth!=depth) return nil;
    uint64_t t0=ssao_cpu_diag?host_ticks():0;
    if (!ssao_depth_prepare(depth)) return nil;
    if (ssao_cleared!=frame_serial || rt_render_list_ops(ssao_rec)) {
        ssao_open(ssao_half_depth);rt_render_list_replay(ssao_rec,enc);end_pass();memset(&ssao_eb,0,sizeof ssao_eb);
    }
    if(ssao_proof()&&ssao_reference_rec){ssao_reference_record=1;ssao_open(depth);lamprec_replay(ssao_reference_rec,enc);end_pass();ssao_reference_record=0;memset(&ssao_reference_eb,0,sizeof ssao_reference_eb);}
    if(ssao_proof()&&ssao_cpu_reference_rec){ssao_cpu_reference_record=1;ssao_open(ssao_half_depth);lamprec_replay(ssao_cpu_reference_rec,enc);end_pass();ssao_cpu_reference_record=0;memset(&ssao_cpu_reference_eb,0,sizeof ssao_cpu_reference_eb);}
    if(ssao_cpu_diag)ssao_diag_replay_ns+=host_ticks()-t0;
    return ssao_mask.tex;
}
id<MTLTexture> render_ssao_cpu_reference_coverage(void){return ssao_proof()?ssao_cpu_reference_mask.tex:nil;}
id<MTLTexture> render_ssao_reference_coverage(void){return ssao_proof()?ssao_reference_mask.tex:nil;}
static void begin_pass(void) {
    if (ssao_replay==2) return;

    if (lamp_replay == 2) return;    // recording: enc is the recorder (lamp_record)
    if (lamp_replay) { NSUInteger lv, sl; lamp_open(surface_target(dev.ds, &lv, &sl), lampglow_owner()); return; }
    if (enc && !pass_dirty && !pending_clear_flags) return;
    end_pass();
    ensure_cmd();
    MTLRenderPassDescriptor *rp = [MTLRenderPassDescriptor renderPassDescriptor];
    id<MTLTexture> rt0 = nil;
    for (int i = 0; i < 4; ++i) {
        NSUInteger lv, sl;
        id<MTLTexture> t = surface_target(dev.rt[i], &lv, &sl);
        if (!t) continue;
        if (!rt0) rt0 = t;
        rp.colorAttachments[i].texture = t;
        rp.colorAttachments[i].level = lv;
        rp.colorAttachments[i].slice = sl;
        rp.colorAttachments[i].storeAction = MTLStoreActionStore;
        if (t.sampleCount > 1) ms_attach(rp.colorAttachments[i], surface_rt(dev.rt[i]), i);
        if (i == 0 && (pending_clear_flags & 1)) {
            uint32_t c = pending_clear_color;
            rp.colorAttachments[i].loadAction = MTLLoadActionClear;
            rp.colorAttachments[i].clearColor = MTLClearColorMake(((c >> 16) & 255) / 255.0, ((c >> 8) & 255) / 255.0, (c & 255) / 255.0, (c >> 24) / 255.0);
        } else {
            rp.colorAttachments[i].loadAction = MTLLoadActionLoad;
        }
    }
    NSUInteger lv, sl;
    id<MTLTexture> ds = pass_depth(rt0, surface_target(dev.ds, &lv, &sl));
    if (ds) {
        rp.depthAttachment.texture = ds;
        rp.depthAttachment.storeAction = MTLStoreActionStore;
        if (ds.sampleCount > 1) ms_attach(rp.depthAttachment, surface_rt(dev.ds), 4);
        rp.depthAttachment.loadAction = (pending_clear_flags & 2) ? MTLLoadActionClear : MTLLoadActionLoad;
        rp.depthAttachment.clearDepth = pending_clear_z;
        rp.stencilAttachment.texture = ds;
        rp.stencilAttachment.storeAction = MTLStoreActionStore;
        rp.stencilAttachment.loadAction = (pending_clear_flags & 4) ? MTLLoadActionClear : MTLLoadActionLoad;
        rp.stencilAttachment.clearStencil = pending_clear_stencil;
    }
    pending_clear_flags = 0;
    rp.visibilityResultBuffer = vis_buf;           // occlusion queries (below); slots a pass doesn't count into are left alone
    if (gpt_on) gpt_render_auto(rp, soft_smoke_encoding ? "soft smoke" : gpt_phase);
    enc = [cmd renderCommandEncoderWithDescriptor:rp];
    if (gpt_on) gpt_name(enc);
    enc_samples = rt0 ? (uint32_t)rt0.sampleCount : ds ? (uint32_t)ds.sampleCount : 1;
    memset(&eb, 0, sizeof eb);
    pass_dirty = 0;
    if (q_active) query_segment();
}
// Called by the device when render targets change.
void render_targets_changed(void) { pass_dirty = 1; soft_depth_valid = 0; }

static int soft_smoke_snapshot_copy(id<MTLTexture> z) {
    // Materialize pending clears before reading any depth. End/store all attachments.
    begin_pass(); end_pass(); ensure_cmd();
    static id<MTLRenderPipelineState> pso[2];
    static int attempted[2];
    int ms = z.sampleCount > 1;
    if (!pso[ms]) {
        if (attempted[ms]++) return 0;
        NSError *error = nil;
        NSString *src = [[NSString stringWithFormat:@"#define SMOKE_MSAA %d\n", ms]
            stringByAppendingString:[NSString stringWithUTF8String:soft_depth_msl]];
        id<MTLLibrary> lib = [mtl newLibraryWithSource:src options:nil error:&error];
        MTLRenderPipelineDescriptor *pd = [MTLRenderPipelineDescriptor new];
        pd.vertexFunction = [lib newFunctionWithName:@"smoke_depth_vs"];
        pd.fragmentFunction = [lib newFunctionWithName:@"smoke_depth_fs"];
        pd.colorAttachments[0].pixelFormat = MTLPixelFormatR32Float;
        if (lib) pso[ms] = pipeline_build(pd, __func__, &error);
        if (!pso[ms]) { if (diag_on) diag_event("soft smoke: depth pipeline failed: %s", error.localizedDescription.UTF8String); return 0; }
    }
    if (!soft_copy_depth || soft_copy_depth.width != z.width || soft_copy_depth.height != z.height) {
        MTLTextureDescriptor *d = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatR32Float width:z.width height:z.height mipmapped:NO];
        d.storageMode = MTLStorageModePrivate; d.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead;
        soft_copy_depth = [mtl newTextureWithDescriptor:d];
        if (!soft_copy_depth) return 0;
    }
    soft_depth=soft_copy_depth;
    MTLRenderPassDescriptor *rp = [MTLRenderPassDescriptor renderPassDescriptor];
    rp.colorAttachments[0].texture = soft_depth;
    rp.colorAttachments[0].loadAction = MTLLoadActionDontCare;
    rp.colorAttachments[0].storeAction = MTLStoreActionStore;
    if (gpt_on) gpt_render_auto(rp, "soft smoke depth nearest");
    id<MTLRenderCommandEncoder> e = [cmd renderCommandEncoderWithDescriptor:rp];
    if (gpt_on) gpt_name(e);
    [e setRenderPipelineState:pso[ms]]; [e setFragmentTexture:z atIndex:0];
    [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3]; [e endEncoding];
    soft_depth_valid = 1; soft_depth_frame = frame_serial;
    if (diag_on) {
        static unsigned reports;
        if (reports++ < 16) diag_event("soft smoke: snapshot frame %llu draw %llu depth %lux%lu samples %lu nearest", (unsigned long long)frame_serial,
            (unsigned long long)stat_draws, (unsigned long)z.width, (unsigned long)z.height, (unsigned long)z.sampleCount);
    }
    return 1;
}
static int soft_smoke_snapshot(id<MTLTexture> z) {
    if(soft_depth_valid && soft_depth_frame==frame_serial)return 1;
    if(z==ad_rt.ms && z.sampleCount>1){
        begin_pass();end_pass();
        if(ad_rt.stale)return 0; // the opened opaque pass must have resolved on store
        soft_depth=ad_rt.tex;soft_depth_valid=1;soft_depth_frame=frame_serial;
        if(diag_on){static unsigned reports;if(reports++<16)diag_event("soft smoke: snapshot frame %llu depth %lux%lu samples %lu nearest on-store",(unsigned long long)frame_serial,(unsigned long)z.width,(unsigned long)z.height,(unsigned long)z.sampleCount);}
        return 1;
    }
    return soft_smoke_snapshot_copy(z);
}

// Same-input proof only. Tee complete immutable bindings into four typed lists;
// replay original / Off / On / disabled-fade against identical scene samples.
// Off uses the unchanged original PS; no rendering ticks or random emitters are
// rerun between these images. This machinery is absent unless explicitly enabled.
@interface SoftSmokeTee : NSProxy {
@public
    id target;
    NSArray *lists;
    id<MTLRenderPipelineState> original;
    id<MTLRenderPipelineState> reference;
    id<MTLTexture> reference_depth;
    int late_control;
}
@end
@implementation SoftSmokeTee
- (NSMethodSignature *)methodSignatureForSelector:(SEL)s { return [(target?:lists[2]) methodSignatureForSelector:s]; }
- (void)forwardInvocation:(NSInvocation *)inv {
    for (unsigned i=0;i<5;++i) {
        id<MTLRenderCommandEncoder> e=lists[i];
        if (inv.selector==@selector(setRenderPipelineState:) && i<2) [e setRenderPipelineState:original];
        else if (inv.selector==@selector(setRenderPipelineState:) && i==4) [e setRenderPipelineState:reference];
        else if(inv.selector==@selector(setFragmentTexture:atIndex:) && i==4){
            NSUInteger index;[inv getArgument:&index atIndex:3];
            if(index==15)[e setFragmentTexture:reference_depth atIndex:index];else [inv invokeWithTarget:e];
        }
        else if (inv.selector==@selector(setFragmentBytes:length:atIndex:)) {
            const void *bytes;NSUInteger length,index;
            [inv getArgument:&bytes atIndex:2];[inv getArgument:&length atIndex:3];[inv getArgument:&index atIndex:4];
            if(index==MAT_PS_BUF && length==sizeof(MatConsts)) {
                MatConsts k;memcpy(&k,bytes,sizeof k);k.k[1][3]=i==3 || (late_control && i<2);
                [e setFragmentBytes:&k length:sizeof k atIndex:index];
            } else [inv invokeWithTarget:e];
        } else [inv invokeWithTarget:e];
    }
    if(target)[inv invokeWithTarget:target];
}
@end
static RenderTex soft_proof_rt[5];
static uint64_t soft_proof_frame;
static id<MTLTexture> soft_proof_z;
static id<MTLTexture> soft_proof_reference_depth;
static int soft_proof_active;
static Map soft_proof_errors;
static void soft_proof_failure(int unused) { (void)unused; }
static const char soft_copy_msl[] =
"#include <metal_stdlib>\nusing namespace metal;\n"
"struct V { float4 p [[position]]; };\n"
"vertex V copy_vs(uint v [[vertex_id]]) { float2 p[3]={float2(-1,-1),float2(3,-1),float2(-1,3)}; return {float4(p[v],0,1)}; }\n"
"#if SMOKE_MSAA\n"
"fragment float4 copy_fs(V i [[stage_in]], uint s [[sample_id]], texture2d_ms<float, access::read> t [[texture(0)]]) { return t.read(uint2(i.p.xy),s); }\n"
"#else\n"
"fragment float4 copy_fs(V i [[stage_in]], texture2d<float, access::read> t [[texture(0)]]) { return t.read(uint2(i.p.xy)); }\n"
"#endif\n";
static id<MTLTexture> soft_late_layer;
static NSMutableArray *soft_late_draws,*soft_late_broken;
static uint64_t soft_late_serial;
static int soft_late_merge_ready;
static void soft_late_composite(id<MTLTexture> scene);
id<MTLTexture> render_soft_smoke_layer(void){return soft_late_merge_ready && soft_late_serial==frame_serial?soft_late_layer:nil;}
void render_soft_smoke_layer_applied(void){soft_late_merge_ready=0;}
void render_soft_smoke_late_finish(id<MTLTexture> scene){if(render_soft_smoke_layer()){soft_late_composite(scene);soft_late_merge_ready=0;}}
static int soft_late_prepare(NSUInteger w,NSUInteger h) {
    if(soft_late_serial!=frame_serial){soft_late_draws=[NSMutableArray new];soft_late_broken=[NSMutableArray new];soft_late_serial=frame_serial;}
    if(!soft_late_layer || soft_late_layer.width!=w || soft_late_layer.height!=h){
        MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:w height:h mipmapped:NO];
        d.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;d.storageMode=MTLStorageModePrivate;soft_late_layer=[mtl newTextureWithDescriptor:d];
    }
    return soft_late_layer!=nil;
}
static void soft_late_replay(NSArray *draws) {
    MTLRenderPassDescriptor *rp=[MTLRenderPassDescriptor renderPassDescriptor];rp.colorAttachments[0].texture=soft_late_layer;
    rp.colorAttachments[0].loadAction=MTLLoadActionClear;rp.colorAttachments[0].clearColor=MTLClearColorMake(0,0,0,0);rp.colorAttachments[0].storeAction=MTLStoreActionStore;
    if(gpt_on)gpt_render_auto(rp,"soft smoke particles 1x");
    id<MTLRenderCommandEncoder> e=[cmd renderCommandEncoderWithDescriptor:rp];if(gpt_on)gpt_name(e);
    for(id list in draws)rt_render_list_replay(list,e);[e endEncoding];
}
static void soft_late_composite(id<MTLTexture> scene) {
    static id<MTLRenderPipelineState> states[2];unsigned slot=scene.pixelFormat==MTLPixelFormatRGBA16Float;
    id<MTLRenderPipelineState> pso=states[slot];
    if(!pso){
        NSString *s=[@"#define SMOKE_MSAA 0\n" stringByAppendingString:[NSString stringWithUTF8String:soft_copy_msl]];
        id<MTLLibrary> l=[mtl newLibraryWithSource:s options:nil error:nil];MTLRenderPipelineDescriptor *pd=[MTLRenderPipelineDescriptor new];
        pd.vertexFunction=[l newFunctionWithName:@"copy_vs"];pd.fragmentFunction=[l newFunctionWithName:@"copy_fs"];
        MTLRenderPipelineColorAttachmentDescriptor *a=pd.colorAttachments[0];a.pixelFormat=scene.pixelFormat;a.writeMask=MTLColorWriteMaskRed|MTLColorWriteMaskGreen|MTLColorWriteMaskBlue;
        a.blendingEnabled=YES;a.sourceRGBBlendFactor=MTLBlendFactorOne;a.destinationRGBBlendFactor=MTLBlendFactorOneMinusSourceAlpha;
        pso=states[slot]=pipeline_build(pd, __func__, NULL);if(!pso){fprintf(stderr,"soft smoke composite pipeline failed\n");abort();}
    }
    MTLRenderPassDescriptor *rp=[MTLRenderPassDescriptor renderPassDescriptor];rp.colorAttachments[0].texture=scene;
    rp.colorAttachments[0].loadAction=MTLLoadActionLoad;rp.colorAttachments[0].storeAction=MTLStoreActionStore;
    if(gpt_on)gpt_render_auto(rp,"soft smoke composite 1x");
    id<MTLRenderCommandEncoder> e=[cmd renderCommandEncoderWithDescriptor:rp];if(gpt_on)gpt_name(e);
    [e setRenderPipelineState:pso];[e setFragmentTexture:soft_late_layer atIndex:0];[e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[e endEncoding];
}
static MTLRenderPassDescriptor *soft_proof_pass(RenderTex *r,int load,int depth) {
    MTLRenderPassDescriptor *rp=[MTLRenderPassDescriptor renderPassDescriptor];
    rp.colorAttachments[0].texture=r->ms?r->ms:r->tex;
    rp.colorAttachments[0].loadAction=load?MTLLoadActionLoad:MTLLoadActionDontCare;
    rp.colorAttachments[0].storeAction=MTLStoreActionStore;
    if(r->ms){rp.colorAttachments[0].resolveTexture=r->tex;rp.colorAttachments[0].storeAction=MTLStoreActionStoreAndMultisampleResolve;}
    if(depth){
        rp.depthAttachment.texture=soft_proof_z;rp.depthAttachment.loadAction=MTLLoadActionLoad;rp.depthAttachment.storeAction=MTLStoreActionStore;
        if(soft_proof_z.pixelFormat==MTLPixelFormatDepth32Float_Stencil8){rp.stencilAttachment.texture=soft_proof_z;rp.stencilAttachment.loadAction=MTLLoadActionLoad;rp.stencilAttachment.storeAction=MTLStoreActionStore;}
    }
    return rp;
}
static void soft_proof_shot(id<MTLTexture> t,const char *name,uint64_t frame) {
    int floating=t.pixelFormat==MTLPixelFormatRGBA16Float;
    NSUInteger w=t.width,h=t.height,pitch=w*(floating?8:4);
    id<MTLBuffer> b=[mtl newBufferWithLength:pitch*h options:MTLResourceStorageModeShared];if(!b)return;
    id<MTLBlitCommandEncoder> e=gpt_blit(cmd,"soft smoke proof image");
    [e copyFromTexture:t sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(w,h,1) toBuffer:b destinationOffset:0 destinationBytesPerRow:pitch destinationBytesPerImage:pitch*h];[e endEncoding];
    NSString *path=[NSString stringWithFormat:@"%s/soft_pair_%llu_%s.png",diag_dir,(unsigned long long)frame,name];
    static dispatch_queue_t io;
    if(!io)io=dispatch_queue_create("nfsmw.soft-smoke-proof",DISPATCH_QUEUE_SERIAL);
    [cmd addCompletedHandler:^(id<MTLCommandBuffer> done){if(done.status==MTLCommandBufferStatusCompleted)dispatch_async(io,^{
        if(!floating){diag_png(path.UTF8String,b.contents,(uint32_t)w,(uint32_t)h,(uint32_t)pitch,0);return;}
        uint8_t *px=malloc(w*h*4);if(!px)return;
        for(NSUInteger i=0;i<w*h;i++){__fp16 v[4];memcpy(v,(const uint8_t *)b.contents+i*8,8);for(unsigned c=0;c<4;c++)px[i*4+(c<3?2-c:c)]=(uint8_t)lrintf(fminf(1,fmaxf(0,(float)v[c]))*255);}
        diag_png(path.UTF8String,px,(uint32_t)w,(uint32_t)h,(uint32_t)w*4,0);free(px);
    });}];
}
static int soft_proof_begin(id<MTLTexture> scene,id<MTLTexture> z) {
    if(soft_proof_active)return soft_proof_frame==frame_serial;
    if(!diag_on||!getenv("BOOT_DIAG_SOFT_SMOKE")||!postfx_look_variant("softon"))return 0;
    end_pass();ensure_cmd();
    static id<MTLRenderPipelineState> states[2][9];NSUInteger samples=scene.sampleCount;
    id<MTLRenderPipelineState> __strong *pso=states[scene.pixelFormat==MTLPixelFormatRGBA16Float];
    if(samples>8)return 0;
    if(!pso[samples]){
        NSString *s=[[NSString stringWithFormat:@"#define SMOKE_MSAA %d\n",samples>1] stringByAppendingString:[NSString stringWithUTF8String:soft_copy_msl]];
        NSError *error=nil;id<MTLLibrary> l=[mtl newLibraryWithSource:s options:nil error:&error];
        MTLRenderPipelineDescriptor *pd=[MTLRenderPipelineDescriptor new];pd.vertexFunction=[l newFunctionWithName:@"copy_vs"];pd.fragmentFunction=[l newFunctionWithName:@"copy_fs"];
        pd.colorAttachments[0].pixelFormat=scene.pixelFormat;pd.rasterSampleCount=samples;
        if(l)pso[samples]=pipeline_build(pd, __func__, &error);
        if(!pso[samples]){diag_event("soft smoke proof: copy pipeline failed %s",error.localizedDescription.UTF8String);return 0;}
    }
    soft_proof_z=z;
    for(int i=0;i<5;++i){
        RenderTex *r=&soft_proof_rt[i];
        if(!r->tex||r->w!=scene.width||r->h!=scene.height||r->tex.pixelFormat!=scene.pixelFormat||(r->ms?r->ms.sampleCount:1)!=samples){
            MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:scene.pixelFormat width:scene.width height:scene.height mipmapped:NO];
            d.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;d.storageMode=MTLStorageModePrivate;
            r->tex=[mtl newTextureWithDescriptor:d];r->ms=nil;r->w=(uint32_t)scene.width;r->h=(uint32_t)scene.height;
            if(samples>1){d.textureType=MTLTextureType2DMultisample;d.sampleCount=samples;r->ms=[mtl newTextureWithDescriptor:d];}
        }
        if(!r->tex||(samples>1&&!r->ms))return 0;
        MTLRenderPassDescriptor *rp=soft_proof_pass(r,0,0);
        if(gpt_on)gpt_render_auto(rp,"soft smoke proof sample copy");
        id<MTLRenderCommandEncoder> e=[cmd renderCommandEncoderWithDescriptor:rp];if(gpt_on)gpt_name(e);
        [e setRenderPipelineState:pso[samples]];[e setFragmentTexture:scene atIndex:0];[e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[e endEncoding];
        if(i==0)soft_proof_shot(r->tex,"scene",frame_serial);
    }
    soft_proof_frame=frame_serial;soft_proof_active=1;return 1;
}
void render_soft_smoke_proof_finish(void) {
    if(!soft_proof_active)return;
    end_pass();ensure_cmd();
    const char *names[5]={"original","off","on","broken","reference"};
    for(int i=0;i<5;++i)soft_proof_shot(soft_proof_rt[i].tex,names[i],soft_proof_frame);
    soft_proof_active=0;soft_proof_z=nil;
}
void render_soft_smoke_late(id<MTLTexture> scene) {
    soft_late_done=frame_serial;
    if(soft_late_serial!=frame_serial || !soft_late_draws.count)return;
    end_pass();ensure_cmd();int proof=soft_late_broken.count!=0;
    if(proof){
        for(int i=0;i<4;++i){
            RenderTex *r=&soft_proof_rt[i];r->ms=nil;
            if(!r->tex || r->tex.width!=scene.width || r->tex.height!=scene.height){
                MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:scene.pixelFormat width:scene.width height:scene.height mipmapped:NO];
                d.usage=MTLTextureUsageShaderRead|MTLTextureUsageRenderTarget;d.storageMode=MTLStorageModePrivate;r->tex=[mtl newTextureWithDescriptor:d];
            }
            quad_into(cmd,scene,MTLRegionMake2D(0,0,scene.width,scene.height),r->tex,0,0,MTLRegionMake2D(0,0,scene.width,scene.height),0);
        }
        soft_proof_shot(scene,"scene",frame_serial);
        diag_event("soft smoke late proof: frame %llu opaque samples %lu particles 1",(unsigned long long)frame_serial,(unsigned long)(ad_rt.ms?ad_rt.ms.sampleCount:1));
    }
    soft_late_replay(soft_late_draws);
    if(proof)soft_late_composite(soft_proof_rt[2].tex);
    if(!proof && x360_post_ssao_enabled() && !postfx_look_variant("ssaon") && !postfx_look_variant("ssaobroken"))soft_late_merge_ready=1;
    else soft_late_composite(scene);
    if(proof){
        soft_late_replay(soft_late_broken);
        for(int i=0;i<4;++i)if(i!=2)soft_late_composite(soft_proof_rt[i].tex);
        const char *names[4]={"original","off","on","broken"};
        for(int i=0;i<4;++i)soft_proof_shot(soft_proof_rt[i].tex,names[i],frame_serial);
    }
    [soft_late_draws removeAllObjects];[soft_late_broken removeAllObjects];
}


// BEGIN rectangle clear helpers (extracted verbatim by rectangle_clear_test.py).
// All coordinates are half-open; widen before adding unsigned viewport extents.
typedef struct { int64_t x0, y0, x1, y1; } ClearRect;
static ClearRect clear_intersect(ClearRect a, ClearRect b) {
    return (ClearRect){MAX(a.x0, b.x0), MAX(a.y0, b.y0), MIN(a.x1, b.x1), MIN(a.y1, b.y1)};
}
static int clear_nonempty(ClearRect r) { return r.x1 > r.x0 && r.y1 > r.y0; }
static ClearRect clear_bounds(uint32_t w, uint32_t h, const uint32_t *vp, int scissor, const uint32_t *sc) {
    ClearRect r = clear_intersect((ClearRect){0, 0, w, h},
        (ClearRect){vp[0], vp[1], (int64_t)vp[0] + vp[2], (int64_t)vp[1] + vp[3]});
    if (scissor) r = clear_intersect(r, (ClearRect){(int32_t)sc[0], (int32_t)sc[1], (int32_t)sc[2], (int32_t)sc[3]});
    return r;
}
static ClearRect clear_rect(ClearRect bounds, const int32_t *rect) {
    return rect ? clear_intersect(bounds, (ClearRect){rect[0], rect[1], rect[2], rect[3]}) : bounds;
}
static int clear_full(ClearRect r, uint32_t w, uint32_t h) { return r.x0 == 0 && r.y0 == 0 && r.x1 == w && r.y1 == h; }

// One library per attachment mask: omit outputs for absent MRT slots, including depth-only passes.
static NSString *clear_source(unsigned mask) {
    NSMutableString *s = [NSMutableString stringWithString:
        @"#include <metal_stdlib>\nusing namespace metal;\n"
         "struct ClearVertex { float4 p [[position]]; };\n"
         "vertex ClearVertex clear_vs(uint i [[vertex_id]], constant float &z [[buffer(0)]]) {\n"
         "float2 p[3] = {float2(-1,-1), float2(3,-1), float2(-1,3)}; ClearVertex o; o.p = float4(p[i],z,1); return o; }\n"];
    if (!mask) { [s appendString:@"fragment void clear_fs() {}\n"]; return s; }
    [s appendString:@"struct ClearOut {\n"];
    for (unsigned i = 0; i < 4; ++i) if (mask & (1u << i)) [s appendFormat:@"float4 c%u [[color(%u)]];\n", i, i];
    [s appendString:@"};\nfragment ClearOut clear_fs(constant float4 &c [[buffer(0)]]) { ClearOut o;\n"];
    for (unsigned i = 0; i < 4; ++i) if (mask & (1u << i)) [s appendFormat:@"o.c%u = c;\n", i];
    [s appendString:@"return o; }\n"];
    return s;
}
typedef struct ClearPipeline {
    MTLPixelFormat color[4], depth;
    uint32_t samples, flags;
    id<MTLRenderPipelineState> pso;
    id<MTLDepthStencilState> dss;
    struct ClearPipeline *next;
} ClearPipeline;
static ClearPipeline *clear_pipelines;
static ClearPipeline *clear_pipeline(id<MTLDevice> device, const MTLPixelFormat *color, MTLPixelFormat depth, uint32_t samples, uint32_t flags) {
    for (ClearPipeline *p = clear_pipelines; p; p = p->next)
        if (!memcmp(p->color, color, sizeof p->color) && p->depth == depth && p->samples == samples && p->flags == flags) return p;
    static id<MTLLibrary> libs[16];
    unsigned mask = 0;
    for (unsigned i = 0; i < 4; ++i) if (color[i]) mask |= 1u << i;
    NSError *err = nil;
    if (!libs[mask]) libs[mask] = [device newLibraryWithSource:clear_source(mask) options:nil error:&err];
    MTLRenderPipelineDescriptor *pd = [MTLRenderPipelineDescriptor new];
    pd.vertexFunction = [libs[mask] newFunctionWithName:@"clear_vs"];
    pd.fragmentFunction = [libs[mask] newFunctionWithName:@"clear_fs"];
    pd.rasterSampleCount = samples;
    for (unsigned i = 0; i < 4; ++i) {
        pd.colorAttachments[i].pixelFormat = color[i];
        pd.colorAttachments[i].writeMask = (flags & 1) ? MTLColorWriteMaskAll : MTLColorWriteMaskNone;
    }
    pd.depthAttachmentPixelFormat = depth;
    int has_stencil = depth == MTLPixelFormatDepth32Float_Stencil8;
    pd.stencilAttachmentPixelFormat = has_stencil ? depth : MTLPixelFormatInvalid;
    id<MTLRenderPipelineState> pso = libs[mask] ? pipeline_build_device(device, pd, __func__, &err) : nil;
    if (!pso) { fprintf(stderr, "boot: rectangle clear pipeline: %s\n", err.localizedDescription.UTF8String); abort(); }
    MTLDepthStencilDescriptor *dd = [MTLDepthStencilDescriptor new];
    dd.depthCompareFunction = MTLCompareFunctionAlways;
    dd.depthWriteEnabled = depth != MTLPixelFormatInvalid && (flags & 2) != 0;
    if (has_stencil && (flags & 4)) {
        MTLStencilDescriptor *sd = [MTLStencilDescriptor new];
        sd.stencilCompareFunction = MTLCompareFunctionAlways;
        sd.depthStencilPassOperation = MTLStencilOperationReplace;
        sd.readMask = sd.writeMask = 255;
        dd.frontFaceStencil = sd; dd.backFaceStencil = sd;
    }
    ClearPipeline *p = calloc(1, sizeof *p);
    memcpy(p->color, color, sizeof p->color); p->depth = depth; p->samples = samples; p->flags = flags;
    p->pso = pso; p->dss = [device newDepthStencilStateWithDescriptor:dd];
    p->next = clear_pipelines; clear_pipelines = p;
    return p;
}
static void clear_draw(id<MTLRenderCommandEncoder> e, ClearPipeline *p, uint32_t w, uint32_t h,
                       ClearRect r, uint32_t color, float z, uint32_t stencil) {
    float rgba[4] = {((color >> 16) & 255) / 255.f, ((color >> 8) & 255) / 255.f, (color & 255) / 255.f, (color >> 24) / 255.f};
    [e setRenderPipelineState:p->pso]; [e setDepthStencilState:p->dss];
    [e setViewport:(MTLViewport){0, 0, w, h, 0, 1}];
    [e setScissorRect:(MTLScissorRect){r.x0, r.y0, r.x1 - r.x0, r.y1 - r.y0}];
    [e setCullMode:MTLCullModeNone]; [e setDepthBias:0 slopeScale:0 clamp:0];
    [e setStencilReferenceValue:stencil & 255];
    [e setVertexBytes:&z length:sizeof z atIndex:0];
    [e setFragmentBytes:rgba length:sizeof rgba atIndex:0];
    [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
}
// END rectangle clear helpers

static void mf_pass_log(const char *what, uint32_t w, uint32_t h);
void render_clear(uint32_t count, uint32_t rects, uint32_t flags, uint32_t color, float z, uint32_t stencil) {
    if (!mtl) return;
    if (flags & 2) soft_depth_valid = 0;
    flags &= 7;
    NSUInteger lv, sl;
    id<MTLTexture> rt0 = surface_target(dev.rt[0], &lv, &sl);
    uint32_t w = rt0 ? (uint32_t)MAX(1, rt0.width >> lv) : 0;
    uint32_t h = rt0 ? (uint32_t)MAX(1, rt0.height >> lv) : 0;
    if (!rt0) {
        id<MTLTexture> ds = surface_target(dev.ds, &lv, &sl);
        if (ds) { w = (uint32_t)ds.width; h = (uint32_t)ds.height; }
    }
    if (!w || !h || !flags) return;
    // Keep the pre-split single-player full-target clear, including partial VP/scissor.
    const char *pads=getenv("NFS_PADS");
    int bounded=pads && !strcmp(pads,"2");
    if (!bounded) count=0;
    ClearRect bounds = bounded ? clear_bounds(w, h, dev.viewport, dev.rs[174], dev.scissor) : (ClearRect){0,0,w,h};
    if (!clear_nonempty(bounds)) return;
    int full = !count && clear_full(bounds, w, h);
    // Read guest rectangles now; replay records the resulting scissors and copies clear constants inline.
    for (uint32_t i = 0; i < count && !full; ++i)
        full = clear_full(clear_rect(bounds, (const int32_t *)(G + rects + (size_t)i * 16)), w, h);
    if (!full) {
        int any = !count;
        for (uint32_t i = 0; i < count && !any; ++i)
            any = clear_nonempty(clear_rect(bounds, (const int32_t *)(G + rects + (size_t)i * 16)));
        if (!any) return;
        if (lamp_n) lamp_flush(0);  // queued flares must see the pre-clear depth/stencil
        begin_pass();
        MTLPixelFormat formats[4] = {0};
        for (unsigned i = 0; i < 4; ++i) {
            id<MTLTexture> t = surface_target(dev.rt[i], &lv, &sl);
            if (t) formats[i] = t.pixelFormat;
        }
        id<MTLTexture> ds = pass_depth(rt0, surface_target(dev.ds, &lv, &sl));
        ClearPipeline *p = clear_pipeline(mtl, formats, ds ? ds.pixelFormat : MTLPixelFormatInvalid, enc_samples, flags);
        [enc setVisibilityResultMode:MTLVisibilityResultModeDisabled offset:0];
        for (uint32_t i = 0; i < (count ? count : 1); ++i) {
            ClearRect r = clear_rect(bounds, count ? (const int32_t *)(G + rects + (size_t)i * 16) : NULL);
            if (clear_nonempty(r)) clear_draw(enc, p, w, h, r, color, z, stencil);
        }
        memset(&eb, 0, sizeof eb); // next game draw restores all overwritten state/buffer bindings
        if (q_active) query_segment();
        return;
    }
    // Full-target fast path: the same encoder end/start and load actions as before.
    if (rt0) { char what[64]; snprintf(what, sizeof what, "CLEAR flags %u", flags & 7); mf_pass_log(what, w, h); }
    end_pass();
    pending_clear_flags = flags & 7;
    pending_clear_color = color;
    pending_clear_z = z;
    pending_clear_stencil = stencil;
    begin_pass();
}

// BEGIN offline upload test: auditfix_test.py compiles this allocator with memory-backed Metal doubles.
// A 512-byte ring, two 256-byte uploads and a 768-byte spill preserve both earlier uploads; wrapping fails.
static void *ring_alloc(NSUInteger n, uint32_t *off, id<MTLBuffer> __unsafe_unretained *buffer) {
    n = (n + 255) & ~(NSUInteger)255;
    if (n > ring_size || ring_off > ring_size - n) {
        static uint64_t warned_frame = UINT64_MAX;
        if (warned_frame != frame_serial) {
            fprintf(stderr, "boot: render: upload ring full this frame %llu (%u bytes); using separate buffers\n",
                    (unsigned long long)frame_serial, ring_size);
            warned_frame = frame_serial;
        }
        id<MTLBuffer> spill = [mtl newBufferWithLength:n options:MTLResourceStorageModeShared];
        *off = 0; *buffer = spill;
        if (!spill) return NULL;                       // caller skips the draw; never overwrite live uploads
        ensure_cmd();
        // Capture strongly until GPU completion, including cached constants reused by later draws this frame.
        [cmd addCompletedHandler:^(id<MTLCommandBuffer> done) { (void)[spill length]; }];
        return spill.contents;
    }
    *off = ring_off;
    ring_off += (uint32_t)n;
    *buffer = ring[ring_idx];
    return (uint8_t *)(*buffer).contents + *off;
}
// END offline upload test

// ---------------- shaders ----------------
// Shader variants are compiled once, always off the drawing thread (including FE/loading).
// - USERDATA/shadercache.bin records the bytecode and options of every variant compiled; at start-up they're compiled
//   on a background queue, so a later run finds them ready.
// - Embedded effects are prewarmed even on an empty cache. Demand misses never wait on unrelated compiles.
static dispatch_queue_t shader_q;
static uint64_t stat_deferred;
// BEGIN offline shader options test
// Ignore bindings the shader never reads. This lets the load-time catalogue match
// draw-time options without compiling permutations of unrelated bound textures.
typedef struct ShaderShape {
    uint64_t hash;
    uint16_t samplers, inputs, shadow;
    uint8_t types[16], projected;
    struct ShaderShape *next;
} ShaderShape;
static ShaderShape *shader_shapes[256]; // game thread only, immutable after insertion
static ShaderShape *shader_shape(const uint32_t *t, uint32_t n, uint64_t hash) {
    unsigned bucket = (unsigned)hash & 255;
    for (ShaderShape *s = shader_shapes[bucket]; s; s = s->next) if (s->hash == hash) return s;
    SmOptions opt; sm_default_options(&opt);
    SmResult r;
    if (sm_translate(t, n, &opt, &r)) { sm_result_free(&r); return NULL; }
    ShaderShape *s = calloc(1, sizeof *s); s->hash = hash;
    for (int i = 0; i < r.n_samplers; ++i) {
        int st = r.samplers[i].reg;
        s->samplers |= 1u << st; s->types[st] = r.samplers[i].type;
    }
    for (int i = 0; i < r.n_inputs; ++i) s->inputs |= 1u << r.inputs[i].reg;
    for (int i = 0; i < r.n_constants; ++i)
        if (r.constants[i].set == 3 && !strcmp(r.constants[i].name, "shadow_sampler"))
            s->shadow |= 1u << r.constants[i].reg;
    s->projected = r.shader_type == SM_PIXEL && r.major == 1 && r.minor < 4;
    sm_result_free(&r);
    s->next = shader_shapes[bucket]; shader_shapes[bucket] = s;
    return s;
}
static SmOptions shader_options(const ShaderShape *s, const SmOptions *original) {
    SmOptions opt = *original;
    if (!s) return opt;
    for (int st = 0; st < 16; ++st) {
        opt.sampler_type[st] = s->samplers & (1u << st) ? (opt.sampler_type[st] ? opt.sampler_type[st] : s->types[st]) : 0;
        if (st < 8 && (!s->projected || !(s->samplers & (1u << st)))) opt.tex_projected[st] = 0;
        if (opt.sampler_type[st] != SM_TEX_2D) opt.shadow_samplers &= ~(1u << st);
    }
    opt.vs_input_int &= s->inputs; opt.vs_input_uint &= s->inputs;
    return opt;
}
// END offline shader options test
// The performance overlay's render line (overlay.m, game thread): shaders compiling in the background, shader
// variants and pipelines so far, and the cumulative texture uploads, deferred draws and renamed resources.
void render_overlay_stats(int *compiling, int *nshader, int *npipe, uint64_t *uploads, uint64_t *deferred, uint64_t *renamed) {
    int n = 0;
    for (int i = 0; i < nshaders; ++i) n += __atomic_load_n(&shaders[i]->pending, __ATOMIC_ACQUIRE) != 0;
    *compiling = n; *nshader = nshaders; *npipe = (int)pipelines.n;
    *uploads = stat_uploads; *deferred = stat_deferred; *renamed = stat_renamed;
}
// report.m, every frame (three loads): cumulative deferred draws, shader variants and pipelines, so the session log
// can say which frame compiled something.
void render_frame_counters(uint64_t *deferred, int *nshader, int *npipe) {
    *deferred = stat_deferred; *nshader = nshaders; *npipe = (int)pipelines.n + render_static_made;   // + the static libraries/pipelines/arrays made on first use
}
// The rain ripple frames (materials.m, X360 RAINSPLASH00-15) as one 2D array texture with box-filtered mips, made at
// the first draw that samples them; a 1x1 black slice when the frames aren't there (materials.m then passes rain 0).
int render_static_made;                    // render_post.m, below: one-off Metal objects made after start-up (counted with the pipelines)
static id<MTLTexture> ripple_texture(void) {
    static id<MTLTexture> t;
    if (t) return t;
    uint64_t t0 = host_ticks();
    uint32_t w, h;
    int n = mat_ripple_frame(15, &w, &h) ? 16 : 0;
    if (!n) w = h = 1;
    MTLTextureDescriptor *d = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm width:w height:h mipmapped:n > 0];
    d.textureType = MTLTextureType2DArray;
    d.arrayLength = n ? (NSUInteger)n : 1;
    t = [mtl newTextureWithDescriptor:d];
    if (!n) { uint32_t black = 0xFF000000u; [t replaceRegion:MTLRegionMake2D(0, 0, 1, 1) mipmapLevel:0 slice:0 withBytes:&black bytesPerRow:4 bytesPerImage:4]; return t; }
    uint8_t *a = malloc((size_t)w * h * 4), *b = malloc((size_t)w * h * 4);
    for (int i = 0; i < n; ++i) {
        memcpy(a, mat_ripple_frame(i, &w, &h), (size_t)w * h * 4);
        uint32_t lw = w, lh = h;
        for (NSUInteger lv = 0; lv < t.mipmapLevelCount; ++lv) {
            [t replaceRegion:MTLRegionMake2D(0, 0, lw, lh) mipmapLevel:lv slice:(NSUInteger)i withBytes:a bytesPerRow:lw * 4 bytesPerImage:lw * lh * 4];
            uint32_t nw = lw > 1 ? lw / 2 : 1, nh = lh > 1 ? lh / 2 : 1;
            for (uint32_t y = 0; y < nh; ++y)                // 2x2 box (wraps like the sampler on odd sizes: none here)
                for (uint32_t x = 0; x < nw; ++x)
                    for (int c = 0; c < 4; ++c) {
                        uint32_t x0 = 2 * x % lw, x1 = (2 * x + 1) % lw, y0 = 2 * y % lh, y1 = (2 * y + 1) % lh;
                        b[4 * (y * nw + x) + c] = (uint8_t)((a[4 * (y0 * lw + x0) + c] + a[4 * (y0 * lw + x1) + c] + a[4 * (y1 * lw + x0) + c] + a[4 * (y1 * lw + x1) + c] + 2) / 4);
                    }
            uint8_t *tmp = a; a = b; b = tmp;
            lw = nw; lh = nh;
        }
    }
    free(a); free(b);
    ++render_static_made;
    if (diag_on) diag_event("render: rain ripple array %.1f ms", (host_ticks() - t0) / 1e6);
    return t;
}
void render_ripple_warm(void) { if (mtl) ripple_texture(); }   // the first composite (render_x360_raindrops_warm), not the first wet draw
// NFS_FOG=x360|pc (default x360; restart): every fogged PC vertex shader (11) clamps the radial fog distance
// with `min r, r, cN.c` where `def cN` holds the literal 2500; the X360 clamps view depth at kMaxDist
// (render_diff x360_port_candidates #2; X360F_KMAXDIST_WORLD, generated). x360 raises the literal to it in a copy of the bytecode before sm_translate:
// distant haze and the skyline fog deeper, near fog is unchanged. The mirror runs the same vertex shaders.
static int fog_x360(void) {
    static int v = -1;
    if (v < 0) { const char *e = getenv("NFS_FOG"); v = !(e && !strcmp(e, "pc")); }
    return v;
}
// The 11 shaders' literal sits in different registers and components (c14-c19, .y/.z/.w; D3DX defs carry the
// .xyzw write mask: dst token 0xA00F00nn), and 10 of them are vs_1_1, whose opcode tokens have no length field, so
// the walk only trusts lengths from SM2 on; in SM1 it reads the leading comments, dcls and defs (D3DX emits every def
// before the first ALU instruction) and stops at the first other instruction. Every component that is exactly 2500.0f
// in a float-constant def is raised: in the PC's VSs 2500 is only ever the fog clamp (render_diff pc_effects).
static uint32_t *fog_patch(const uint32_t *t, uint32_t n) {
    if (n < 2 || (t[0] >> 16) != 0xFFFEu || !fog_x360() || !X360_SHADER_LITS) return NULL;   // no ZZDATA0: PC fog
    int sm1 = ((t[0] >> 8) & 0xFFu) < 2;
    uint32_t *c = NULL;
    for (uint32_t i = 1; i < n && t[i] != 0x0000FFFFu;) {
        uint32_t op = t[i] & 0xFFFFu, len = (t[i] >> 24) & 0xFu;
        if (op == 0xFFFEu) len = (t[i] >> 16) & 0x7FFFu;     // comment: length in bits 16-30
        else if (sm1 && op == 0x51u) len = 5;                // def: dst + 4 floats
        else if (sm1 && op == 0x1Fu) len = 2;                // dcl: usage + dst
        else if (sm1) break;                                 // first ALU instruction: no defs follow
        if (op == 0x51u && len == 5 && i + 5 < n) {
            uint32_t d = t[i + 1], type = ((d >> 28) & 7u) | ((d >> 8) & 0x18u);
            for (uint32_t k = 0; type == 2u && k < 4; k++) {   // D3DSPR_CONST, any register, any component
                float v; memcpy(&v, &t[i + 2 + k], 4);
                if (v != 2500.0f) continue;
                if (!c) { c = malloc(4 * (size_t)n); memcpy(c, t, 4 * (size_t)n); }
                float x = X360F_KMAXDIST_WORLD; memcpy(&c[i + 2 + k], &x, 4);
            }
        }
        i += 1 + len;
    }
    return c;
}
// A grass/aux source can be identical for several PC techniques/options. Share
// that exact library, including in-flight requests, without changing its source.
// All callers use the same Relaxed compile options, so source is the full identity.
typedef struct ShaderLibrary {
    NSString *source;
    id<MTLLibrary> library;
    NSError *error;
    int pending;
    struct ShaderLibrary *next;
} ShaderLibrary;
static ShaderLibrary *shader_libraries;
static pthread_mutex_t shader_library_mu = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t shader_library_done = PTHREAD_COND_INITIALIZER;
// BOOT_DIAG only: count source compiles on the drawing thread, including FE/loading.
static id<MTLLibrary> shader_library(NSString *source, MTLCompileOptions *options, NSError **error) {
    pthread_mutex_lock(&shader_library_mu);
    ShaderLibrary *s = shader_libraries;
    while (s && ![s->source isEqualToString:source]) s = s->next;
    if (s) {
        while (s->pending) pthread_cond_wait(&shader_library_done, &shader_library_mu);
        id<MTLLibrary> library = s->library; *error = s->error;
        pthread_mutex_unlock(&shader_library_mu);
        return library;
    }
    s = calloc(1, sizeof *s); s->source = source; s->pending = 1;
    s->next = shader_libraries; shader_libraries = s;
    pthread_mutex_unlock(&shader_library_mu);
    if (diag_on && pthread_main_np()) {
        diag_event("shader_compile: frame_sync=%llu frame=%llu", ++stat_sync_shader, (unsigned long long)frame_serial);
    }
    id<MTLLibrary> library = [mtl newLibraryWithSource:source options:options error:error];
    pthread_mutex_lock(&shader_library_mu);
    s->library = library; s->error = *error; s->pending = 0;
    pthread_cond_broadcast(&shader_library_done);
    pthread_mutex_unlock(&shader_library_mu);
    return library;
}
static pthread_mutex_t shader_source_mu = PTHREAD_MUTEX_INITIALIZER;
static char *shader_replacement(ShaderEntry *e) {
    // Material source generators have lazy compile-time switches shared with the
    // load-time eligibility checks. Serialize preparation, not Metal compilation.
    pthread_mutex_lock(&shader_source_mu);
    char *source = e->variant == MAT_VARIANT_AUX ? mat_aux_shader(&e->refl, e->refl.shader_type, e->opt.vs_input_int, e->opt.vs_input_uint)
                   : e->variant == MAT_VARIANT_WATER ? mat_water_shader(&e->refl, e->refl.shader_type, e->opt.vs_input_int, e->opt.vs_input_uint)
                   : e->variant == MAT_VARIANT_GLASS ? mat_glass_shader(e->tokens, e->ntokens, &e->refl, e->refl.shader_type, e->opt.vs_input_int, e->opt.vs_input_uint)
                   : e->variant == MAT_VARIANT_TREES ? mat_trees_shader(e->tokens, e->ntokens, &e->refl, e->refl.shader_type, e->opt.vs_input_int, e->opt.vs_input_uint)
                   : e->variant == MAT_VARIANT_BONE ? mat_bone_shader(e->tokens, e->ntokens, &e->refl, e->refl.shader_type, e->opt.vs_input_int, e->opt.vs_input_uint)
                   : e->variant == MAT_VARIANT_SOFT_SMOKE ? mat_soft_smoke_shader(e->tokens, e->ntokens, &e->refl, &e->mat)
                   : e->variant == MAT_VARIANT_SOFT_SMOKE_RESOLVED ? mat_soft_smoke_resolved_shader(e->tokens, e->ntokens, &e->refl, &e->mat)
                   : e->variant == MAT_VARIANT_SOFT_SMOKE_LATE ? mat_soft_smoke_late_shader(e->tokens, e->ntokens, &e->refl, &e->mat)
                   : e->variant == MAT_VARIANT_HEADLAMP_CLEAR ? mat_headlamp_shader(e->tokens, e->ntokens, &e->refl, &e->mat)
                   : e->variant == MAT_VARIANT_CARBON_LAMP ? mat_carbon_lamp_shader(e->tokens, e->ntokens, &e->refl, &e->mat)
                   : e->variant == MAT_VARIANT_ROAD_FILM ? mat_roadfilm_shader(e->tokens, e->ntokens, &e->refl, &e->mat)
                   : e->variant ? mat_grass_shader(&e->refl, e->refl.shader_type, (int)e->variant - 1, e->opt.vs_input_int, e->opt.vs_input_uint)
                               : mat_replace(e->tokens, e->ntokens, &e->refl, &e->mat);
    pthread_mutex_unlock(&shader_source_mu);
    return source;
}
static void compile_entry(ShaderEntry *e) {
    uint64_t t0 = host_ticks();
    uint32_t *fogged = fog_patch(e->tokens, e->ntokens);
    int rc = sm_translate(fogged ? fogged : e->tokens, e->ntokens, &e->opt, &e->refl);
    if (fogged && diag_on) diag_event("render: shader %016llX: fog clamp 2500 -> %g", (unsigned long long)e->key, (double)X360F_KMAXDIST_WORLD);
    free(fogged);
    if (rc != 0) {
        fprintf(stderr, "boot: render: shader translation failed: %s\n", e->refl.error);
    } else {
        NSError *err = nil;
        MTLCompileOptions *co = [MTLCompileOptions new];
        co.mathMode = MTLMathModeRelaxed;
        // material-shader layer: a replacement for this shader, bound through the same reflection (materials.m)
        char *rep = shader_replacement(e);
        id<MTLLibrary> lib = nil;
        if (rep) {
            lib = shader_library(hdrclip_world_source([NSString stringWithUTF8String:rep],e->key,e->mat,e->variant), co, &err);
            if (!lib) { fprintf(stderr, "boot: render: replacement MSL compile failed, translation kept: %s\n", err.localizedDescription.UTF8String); e->mat = 0; }
            else { free(e->refl.msl); e->refl.msl = rep; e->refl.msl_len = strlen(rep); rep = NULL; }
            free(rep);
        }
        // A failed grass variant must never masquerade as a successful grass shader:
        // the caller falls back to the complete variant-0 PC draw, with PC bindings.
        if (!lib && !e->variant) lib = shader_library(hdrclip_world_source([NSString stringWithUTF8String:e->refl.msl],e->key,e->mat,e->variant), co, &err);
        if (!lib) fprintf(stderr, "boot: render: MSL compile failed: %s\n", err.localizedDescription.UTF8String);
        else { e->fn = [lib newFunctionWithName:@"sm_main"]; e->ok = e->fn != nil; }
        if (diag_on && getenv("BOOT_DIAG_MSL")) {        // <diag>/msl/<key>.metal: the translated source of every variant
            char path[1024];
            snprintf(path, sizeof path, "%s/msl", diag_dir); mkdir(path, 0755);
            snprintf(path, sizeof path, "%s/msl/%016llX.metal", diag_dir, (unsigned long long)e->key);
            FILE *f = fopen(path, "w");
            if (f) { fputs(e->refl.msl, f); fclose(f); }
        }
    }
    if (e->variant == MAT_VARIANT_WATER && !e->ok) mat_water_failure(0);
    if (e->variant == MAT_VARIANT_GLASS && !e->ok) mat_glass_failure(0);
    if (e->variant == MAT_VARIANT_TREES && !e->ok) mat_trees_failure(0);
    if (e->variant == MAT_VARIANT_BONE && !e->ok) mat_bone_failure(0);
    uint64_t dt = host_ticks() - t0;
    if (diag_on && dt > 20000000ull) diag_event("render: shader %016llX translated and compiled in %.0f ms%s", (unsigned long long)e->key, dt / 1e6,
                                                pthread_main_np() ? "" : " (background)");
    __atomic_store_n(&e->pending, 0, __ATOMIC_RELEASE);
    dispatch_semaphore_signal(e->ready);
}
static ShaderEntry *new_entry(uint64_t key, const uint32_t *tokens, uint32_t n, const SmOptions *opt, uint32_t variant) {
    if (nshaders == capshaders) { capshaders = capshaders ? capshaders * 2 : 256; shaders = realloc(shaders, (size_t)capshaders * sizeof *shaders); }
    ShaderEntry *e = calloc(1, sizeof *e);
    e->ready = dispatch_semaphore_create(0);
    shaders[nshaders++] = e;
    e->key = key;
    e->tokens = malloc(4 * (size_t)n);
    memcpy(e->tokens, tokens, 4 * (size_t)n);
    e->ntokens = n;
    e->opt = *opt;
    e->variant = variant;
    return e;
}
// BEGIN offline shader scheduling test
static void shader_submit(ShaderEntry *e) {
    e->pending = 1; // published before dispatch, and cleared with release only after fn/refl/ok are final
    dispatch_queue_t q = shader_workers[shader_worker_next++ % 4];
    dispatch_async(q, ^{ @autoreleasepool { compile_entry(e); } });
}
// END offline shader scheduling test
// BEGIN offline shader readiness test
static int shader_pending(ShaderEntry *e) {
    if (!__atomic_load_n(&e->pending, __ATOMIC_ACQUIRE)) return 0;
    // Optional material shaders already retain the complete base draw while
    // pending. Only an unexpected base miss may wait, for this entry alone;
    // all such waits together get at most 1 ms of a frame, including FE/loading.
    if (e->variant) return 1;
    static uint64_t serial, spent;
    if (serial != frame_serial) { serial = frame_serial; spent = 0; }
    if (spent < 1000000) {
        uint64_t t0 = host_ticks();
        dispatch_semaphore_wait(e->ready, dispatch_time(DISPATCH_TIME_NOW, (int64_t)(1000000 - spent)));
        spent += host_ticks() - t0;
    }
    return __atomic_load_n(&e->pending, __ATOMIC_ACQUIRE) != 0;
}
// END offline shader readiness test
static void cache_path(char *out, size_t n) {
    snprintf(out, n, "%s/shadercache.bin", userdata_dir());
}
static void cache_append(const ShaderEntry *e) {
    char path[1024];
    cache_path(path, sizeof path);
    // The record-size header distinguishes legacy options-only records. Warm can
    // read both; the first new append migrates the scratch cache to variant records.
    uint32_t hdr[2] = {0}, record_size = (uint32_t)sizeof(SmOptions) + 4;
    FILE *f = fopen(path, "rb");
    int current = f && fread(hdr, 4, 2, f) == 2 && hdr[0] == 0x4353464Eu && hdr[1] == record_size;
    if (f) fclose(f);
    f = fopen(path, current ? "ab" : "wb");
    if (!f) return;
    if (!current) { hdr[0] = 0x4353464Eu; hdr[1] = record_size; fwrite(hdr, 4, 2, f); }
    fwrite(&e->opt, sizeof e->opt, 1, f);
    fwrite(&e->variant, 4, 1, f);
    fwrite(&e->ntokens, 4, 1, f);
    fwrite(e->tokens, 4, e->ntokens, f);
    fclose(f);
}
static void shader_catalogue_warm(void);
static void shader_cache_warm(void) {
    shader_q = dispatch_queue_create("nfsmw.shaders", DISPATCH_QUEUE_SERIAL);
    for (int i = 0; i < 4; ++i) {
        char name[64]; snprintf(name, sizeof name, "nfsmw.shader.%d", i);
        shader_workers[i] = dispatch_queue_create(name, DISPATCH_QUEUE_SERIAL);
    }
    char path[1024];
    cache_path(path, sizeof path);
    FILE *f = fopen(path, "rb");
    if (!f) { shader_catalogue_warm(); return; }
    uint32_t hdr[2], n;
    int count = 0;
    if (fread(hdr, 4, 2, f) == 2 && hdr[0] == 0x4353464Eu && (hdr[1] == sizeof(SmOptions) || hdr[1] == sizeof(SmOptions) + 4)) {
        SmOptions opt;
        uint32_t variant;
        while (fread(&opt, sizeof opt, 1, f) == 1) {
            variant = 0;
            if (hdr[1] != sizeof(SmOptions) && (fread(&variant, 4, 1, f) != 1 || (variant > 10 && variant != MAT_VARIANT_WATER && variant != MAT_VARIANT_GLASS && variant != MAT_VARIANT_TREES && variant != MAT_VARIANT_BONE && variant != MAT_VARIANT_CARBON_LAMP && variant != MAT_VARIANT_HEADLAMP_CLEAR && variant != MAT_VARIANT_AUX && variant != MAT_VARIANT_SOFT_SMOKE && variant != MAT_VARIANT_SOFT_SMOKE_RESOLVED && variant != MAT_VARIANT_SOFT_SMOKE_LATE && variant != MAT_VARIANT_ROAD_FILM))) break;
            if (fread(&n, 4, 1, f) != 1 || !n || n >= (1u << 20)) break;
            uint32_t *t = malloc(4 * (size_t)n);
            if (fread(t, 4, n, f) != n) { free(t); break; }
            uint64_t hash = fnv(t, 4 * (size_t)n, 1469598103934665603ull);
            opt = shader_options(shader_shape(t, n, hash), &opt);
            uint64_t key = fnv(&opt, sizeof opt, hash);
            if (variant) key = fnv(&variant, 4, key);
            int dup = 0;
            for (int i = 0; i < nshaders && !dup; ++i) dup = shaders[i]->key == key;
            if (!dup) {
                ShaderEntry *e = new_entry(key, t, n, &opt, variant);
                shader_submit(e);
                ++count;
            }
            free(t);
        }
    }
    fclose(f);
    fprintf(stderr, "boot: render: %d cached shader variants compiling in the background\n", count);
    shader_catalogue_warm();
}
// Lookup cache in front of get_shader_variant: (shader object, options, variant) -> entry, direct-mapped, word-wise
// hash. The per-object memo keeps two keys, but a grass draw asks one object for its base and up to five variants, so
// the memo thrashes and every lookup paid the byte-wise FNV plus a linear scan of shaders[] (0.25 ms/frame self at
// 3,557 draws). Entries are only ever appended to shaders[] and never freed, so a cached pointer stays valid; an object
// is identified by a serial that is never reused (a freed Obj's address can come back for another shader).
typedef struct { uint32_t serial, variant; uint8_t opt[48]; ShaderEntry *e; } VariantCache;
static VariantCache vcache[4096];
static uint32_t vcache_serial;
static inline VariantCache *vcache_slot(Obj *s, const SmOptions *opt, uint32_t variant) {
    if (!s->vc_serial) s->vc_serial = ++vcache_serial;
    uint64_t h = ((uint64_t)s->vc_serial << 32 | variant) * 0x9E3779B97F4A7C15ull;
    const uint8_t *b = (const uint8_t *)opt;
    size_t i = 0;
    for (; i + 8 <= sizeof *opt; i += 8) { uint64_t w; memcpy(&w, b + i, 8); h = (h ^ w) * 0x9E3779B97F4A7C15ull; h ^= h >> 31; }
    for (; i < sizeof *opt; ++i) { h = (h ^ b[i]) * 0x100000001B3ull; }
    return &vcache[(h ^ h >> 29) & 4095];
}
static ShaderEntry *get_shader_variant(Obj *s, const SmOptions *opt, uint32_t variant) {
    // Called two or three times per draw: the bytecode hash is computed once per shader object.
    // The key is a pure function of (bytecode, options, variant): the last two per shader object are memoized
    // (a VS is looked up with and without integer inputs, a shader with its grass/material variants).
    _Static_assert(sizeof(SmOptions) <= sizeof s->memo_opt[0], "memo_opt too small");
    _Static_assert(sizeof(SmOptions) <= sizeof vcache[0].opt, "vcache opt too small");
    VariantCache *vc = vcache_slot(s, opt, variant);
    if (vc->e && vc->serial == s->vc_serial && vc->variant == variant && !memcmp(vc->opt, opt, sizeof *opt)) {
        ShaderEntry *e = vc->e;
        return e;
    }
    // Preserve the raw-options fast lookup; normalize only its misses.
    const SmOptions *requested = opt;
    if (!s->tokens_hash) s->tokens_hash = fnv(s->tokens, 4 * (size_t)s->ntokens, 1469598103934665603ull);
    SmOptions normalized = shader_options(shader_shape(s->tokens, s->ntokens, s->tokens_hash), opt);
    opt = &normalized;
    uint64_t key = 0;
    for (int m = 0; m < 2 && !key; ++m)
        if (s->memo_key[m] && s->memo_variant[m] == variant && !memcmp(s->memo_opt[m], opt, sizeof *opt)) key = s->memo_key[m];
    if (!key) {
        key = fnv(opt, sizeof *opt, s->tokens_hash);
        if (variant) key = fnv(&variant, 4, key);
        int m = s->memo_next; s->memo_next ^= 1;
        memcpy(s->memo_opt[m], opt, sizeof *opt); s->memo_variant[m] = variant; s->memo_key[m] = key;
    }
    ShaderEntry *e = NULL;
    if (s->last_key == key && s->last_idx < nshaders && shaders[s->last_idx]->key == key) e = shaders[s->last_idx];
    for (int i = 0; i < nshaders && !e; ++i) if (shaders[i]->key == key) { s->last_key = key; s->last_idx = i; e = shaders[i]; }
    if (e) {
        vc->serial = s->vc_serial; vc->variant = variant; memcpy(vc->opt, requested, sizeof *requested); vc->e = e;
        return e;
    }
    s->last_key = key; s->last_idx = nshaders;
    e = new_entry(key, s->tokens, s->ntokens, opt, variant);
    vc->serial = s->vc_serial; vc->variant = variant; memcpy(vc->opt, requested, sizeof *requested); vc->e = e;
    shader_submit(e);
    cache_append(e);
    return e;
}
static ShaderEntry *get_shader(Obj *s, const SmOptions *opt) { return get_shader_variant(s, opt, 0); }

// The game's fx_2_0 resources contain the shaders for every technique, including
// techniques not selected until a race. Validate bytecode with the translator's
// parser; skip complete programs (including CTAB comments) rather than mistaking
// instruction operands for another version token. No game shader objects are made.
static void shader_preload_variant(Obj *s, const SmOptions *opt, uint32_t variant) {
    ShaderEntry e = {0}; e.tokens = s->tokens; e.ntokens = s->ntokens;
    e.opt = shader_options(shader_shape(s->tokens, s->ntokens, s->tokens_hash), opt); e.variant = variant;
    if (!sm_translate(e.tokens, e.ntokens, &e.opt, &e.refl)) {
        char *source = shader_replacement(&e);
        if (source) get_shader_variant(s, opt, variant);
        free(source);
    }
    sm_result_free(&e.refl);
}
static void shader_preload_blob(const uint32_t *words, uint32_t count, const char *name) {
    if (count < 2 || words[0] != 0xFEFF0901u) return;
    int world = !strcmp(name, "IDI_WORLD_FX") || !strcmp(name, "IDI_WORLDNORMALMAP_FX");
    for (uint32_t i = 2; i < count; ++i) {
        uint32_t v = words[i];
        if ((v >> 16 != 0xFFFE && v >> 16 != 0xFFFF) || ((v >> 8) & 255) < 1 || ((v >> 8) & 255) > 3 || (v & 255) > 4) continue;
        uint32_t n = (uint32_t)sm_token_count(words + i, count - i);
        if (!n) continue;
        Obj s = {0}; s.tokens = (uint32_t *)words + i; s.ntokens = n;
        s.tokens_hash = fnv(s.tokens, 4 * (size_t)n, 1469598103934665603ull);
        ShaderShape *shape = shader_shape(s.tokens, n, s.tokens_hash);
        if (shape) {
            SmOptions opt; sm_default_options(&opt);
            for (int shadow = 0; shadow <= (shape->shadow != 0); ++shadow) {
                opt.shadow_samplers = shadow ? shape->shadow : 0;
                get_shader(&s, &opt);
                if (world) for (uint32_t shell = 1; shell <= 10; ++shell) shader_preload_variant(&s, &opt, shell);
                if (world) shader_preload_variant(&s, &opt, MAT_VARIANT_AUX);
                if (world) shader_preload_variant(&s, &opt, MAT_VARIANT_WATER);
                if (roadfilm_shader_hash(s.tokens_hash) && roadfilm_enabled())
                    shader_preload_variant(&s, &opt, MAT_VARIANT_ROAD_FILM);
                if (!strcmp(name, "IDI_CAR_FX")) { shader_preload_variant(&s, &opt, MAT_VARIANT_CARBON_LAMP); shader_preload_variant(&s, &opt, MAT_VARIANT_HEADLAMP_CLEAR); }
                if (!strcmp(name, "IDI_GLOSSYWINDOW_FX")) shader_preload_variant(&s, &opt, MAT_VARIANT_GLASS);
                if (!strcmp(name, "IDI_TREE_FX")) shader_preload_variant(&s, &opt, MAT_VARIANT_TREES);
                if (!strcmp(name, "IDI_WORLDBONE_FX")) shader_preload_variant(&s, &opt, MAT_VARIANT_BONE);
                if (!strcmp(name, "IDI_PARTICLES_FX")) {
                    shader_preload_variant(&s, &opt, MAT_VARIANT_SOFT_SMOKE);
                    shader_preload_variant(&s, &opt, MAT_VARIANT_SOFT_SMOKE_RESOLVED);
                    shader_preload_variant(&s, &opt, MAT_VARIANT_SOFT_SMOKE_LATE);
                }
            }
        }
        i += n - 1;
    }
}
static void shader_catalogue_warm(void) {
    // Same trusted PE resource walk as d3dx_effect.c: RT_RCDATA -> name -> language -> data.
    uint32_t pe = 0x400000 + M32(0x40003C);
    uint32_t rsrc = 0x400000 + M32(pe + 24 + 96 + 2 * 8);
    if (rsrc == 0x400000) return;
    int before = nshaders;
    uint32_t count = M16(rsrc + 12) + M16(rsrc + 14);
    for (uint32_t i = 0; i < count; ++i) {
        uint32_t id = M32(rsrc + 16 + 8 * i), off = M32(rsrc + 20 + 8 * i);
        if (id != 10 || !(off & 0x80000000)) continue;
        uint32_t d2 = rsrc + (off & 0x7FFFFFFF), n2 = M16(d2 + 12) + M16(d2 + 14);
        for (uint32_t k = 0; k < n2; ++k) {
            uint32_t nid = M32(d2 + 16 + 8 * k), o2 = M32(d2 + 20 + 8 * k);
            if (!(nid & 0x80000000)) continue;
            uint32_t str = rsrc + (nid & 0x7FFFFFFF), len = M16(str);
            char name[128]; uint32_t q = 0;
            for (; q < len && q < sizeof name - 1; ++q) name[q] = (char)M16(str + 2 + 2 * q);
            name[q] = 0;
            uint32_t d3 = rsrc + (o2 & 0x7FFFFFFF);
            if (o2 & 0x80000000) d3 = rsrc + (M32(d3 + 20) & 0x7FFFFFFF);
            uint32_t va = 0x400000 + M32(d3), bytes = M32(d3 + 4);
            shader_preload_blob((const uint32_t *)(G + va), bytes / 4, name);
        }
    }
    if (diag_on) diag_event("shader_compile: preloaded=%d frame_sync=%llu", nshaders - before, stat_sync_shader);
}

// ---------------- vertex input ----------------
typedef struct { uint8_t stream, type, usage, index; uint16_t offset; } Elem;
static int decl_elems(Elem *out) {
    int n = 0;
    if (dev.decl) {
        Obj *d = obj(dev.decl);
        for (uint32_t i = 0; d && i + 1 < d->decl_n && n < 32; ++i) {
            uint32_t e = d->decl_mem + 8 * i;
            if (M16(e) == 0xFF) break;
            out[n].stream = (uint8_t)M16(e); out[n].offset = M16(e + 2); out[n].type = M8(e + 4);
            out[n].usage = M8(e + 6); out[n].index = M8(e + 7);
            ++n;
        }
        return n;
    }
    // FVF -> elements (stream 0)
    uint32_t f = dev.fvf, off = 0;
    uint32_t pos = f & 0x400E;
    if (pos == 0x002 || pos == 0x004) { out[n++] = (Elem){0, pos == 4 ? 3 : 2, pos == 4 ? SM_USAGE_POSITIONT : SM_USAGE_POSITION, 0, (uint16_t)off}; off += pos == 4 ? 16 : 12; }
    else if (pos >= 0x006 && pos <= 0x00E) { out[n++] = (Elem){0, 2, SM_USAGE_POSITION, 0, (uint16_t)off}; off += 12 + 4 * ((pos - 4) / 2); }
    if (f & 0x010) { out[n++] = (Elem){0, 2, SM_USAGE_NORMAL, 0, (uint16_t)off}; off += 12; }
    if (f & 0x020) { off += 4; }                                                      // PSIZE
    if (f & 0x040) { out[n++] = (Elem){0, 4, SM_USAGE_COLOR, 0, (uint16_t)off}; off += 4; }
    if (f & 0x080) { out[n++] = (Elem){0, 4, SM_USAGE_COLOR, 1, (uint16_t)off}; off += 4; }
    uint32_t ntex = (f >> 8) & 15;
    for (uint32_t t = 0; t < ntex; ++t) {
        uint32_t fmt = (f >> (16 + 2 * t)) & 3;                                          // 0: 2 floats, 1: 3, 2: 4, 3: 1
        uint8_t type = fmt == 0 ? 1 : fmt == 1 ? 2 : fmt == 2 ? 3 : 0;
        out[n++] = (Elem){0, type, SM_USAGE_TEXCOORD, (uint8_t)t, (uint16_t)off};
        off += 4 * (type + 1);
    }
    return n;
}
static uint32_t fvf_stride(uint32_t f) {
    Elem e[16];
    uint32_t save = dev.fvf, saved = dev.decl;
    dev.fvf = f; dev.decl = 0;
    int n = decl_elems(e);
    dev.fvf = save; dev.decl = saved;
    uint32_t end = 0;
    static const uint8_t sz[] = {4, 8, 12, 16, 4, 4, 4, 8, 4, 4, 8, 4, 8, 4, 4, 4, 8};
    for (int i = 0; i < n; ++i) { uint32_t x = e[i].offset + sz[e[i].type < 17 ? e[i].type : 0]; if (x > end) end = x; }
    if (f & 0x020) end += 4;
    return end;
}
static MTLVertexFormat vfmt(uint8_t t, int *as_int, int *as_uint) {
    *as_int = *as_uint = 0;
    switch (t) {
    case 0: return MTLVertexFormatFloat;
    case 1: return MTLVertexFormatFloat2;
    case 2: return MTLVertexFormatFloat3;
    case 3: return MTLVertexFormatFloat4;
    case 4: return MTLVertexFormatUChar4Normalized_BGRA;
    case 5: *as_uint = 1; return MTLVertexFormatUChar4;
    case 6: *as_int = 1; return MTLVertexFormatShort2;
    case 7: *as_int = 1; return MTLVertexFormatShort4;
    case 8: return MTLVertexFormatUChar4Normalized;
    case 9: return MTLVertexFormatShort2Normalized;
    case 10: return MTLVertexFormatShort4Normalized;
    case 11: return MTLVertexFormatUShort2Normalized;
    case 12: return MTLVertexFormatUShort4Normalized;
    case 14: return MTLVertexFormatInt1010102Normalized;
    case 15: return MTLVertexFormatHalf2;
    case 16: return MTLVertexFormatHalf4;
    }
    return MTLVertexFormatFloat4;
}

// ---------------- states ----------------
static MTLBlendFactor blend_factor(uint32_t b) {
    switch (b) {
    case 1: return MTLBlendFactorZero; case 2: return MTLBlendFactorOne;
    case 3: return MTLBlendFactorSourceColor; case 4: return MTLBlendFactorOneMinusSourceColor;
    case 5: return MTLBlendFactorSourceAlpha; case 6: return MTLBlendFactorOneMinusSourceAlpha;
    case 7: return MTLBlendFactorDestinationAlpha; case 8: return MTLBlendFactorOneMinusDestinationAlpha;
    case 9: return MTLBlendFactorDestinationColor; case 10: return MTLBlendFactorOneMinusDestinationColor;
    case 11: return MTLBlendFactorSourceAlphaSaturated;
    case 14: return MTLBlendFactorBlendColor; case 15: return MTLBlendFactorOneMinusBlendColor;
    }
    return MTLBlendFactorOne;
}
static MTLBlendOperation blend_op(uint32_t o) {
    switch (o) { case 2: return MTLBlendOperationSubtract; case 3: return MTLBlendOperationReverseSubtract;
                 case 4: return MTLBlendOperationMin; case 5: return MTLBlendOperationMax; }
    return MTLBlendOperationAdd;
}
static MTLCompareFunction cmp_fn(uint32_t f) {
    switch (f) { case 1: return MTLCompareFunctionNever; case 2: return MTLCompareFunctionLess; case 3: return MTLCompareFunctionEqual;
                 case 4: return MTLCompareFunctionLessEqual; case 5: return MTLCompareFunctionGreater; case 6: return MTLCompareFunctionNotEqual;
                 case 7: return MTLCompareFunctionGreaterEqual; }
    return MTLCompareFunctionAlways;
}
static MTLStencilOperation stencil_op(uint32_t o) {
    switch (o) { case 2: return MTLStencilOperationZero; case 3: return MTLStencilOperationReplace; case 4: return MTLStencilOperationIncrementClamp;
                 case 5: return MTLStencilOperationDecrementClamp; case 6: return MTLStencilOperationInvert; case 7: return MTLStencilOperationIncrementWrap;
                 case 8: return MTLStencilOperationDecrementWrap; }
    return MTLStencilOperationKeep;
}
static id<MTLDepthStencilState> depth_state_for(const uint32_t *rs) {
    int zen = rs[7] != 0 && dev.ds;
    uint64_t key = (uint64_t)zen | (uint64_t)(rs[14] != 0) << 1 | (uint64_t)(rs[23] & 15) << 2 | (uint64_t)(rs[52] != 0 && dev.ds) << 6 |
                   (uint64_t)(rs[56] & 15) << 7 | (uint64_t)(rs[53] & 15) << 11 | (uint64_t)(rs[54] & 15) << 15 | (uint64_t)(rs[55] & 15) << 19 |
                   (uint64_t)(rs[58] & 255) << 23 | (uint64_t)(rs[59] & 255) << 31;
    id<MTLDepthStencilState> s = (__bridge id<MTLDepthStencilState>)map_get(&dsstates, key);
    if (diag_on && rs[52]) {      // does the cached state's stencil presence match this call's?
        ++ad1.ds_calls;
        int i = 0; while (i < ad1_nds && ad1_ds_key[i] != key) ++i;
        if (i == ad1_nds && ad1_nds < 512) { ad1_ds_key[ad1_nds] = key; ad1_ds_has[ad1_nds++] = dev.ds != 0; }
        else if (i < ad1_nds && ad1_ds_has[i] != (dev.ds != 0)) ++ad1.ds_mismatch;
    }
    if (s) return s;
    MTLDepthStencilDescriptor *d = [MTLDepthStencilDescriptor new];
    d.depthCompareFunction = zen ? cmp_fn(rs[23]) : MTLCompareFunctionAlways;
    d.depthWriteEnabled = zen && rs[14] != 0;
    if (rs[52] && dev.ds) {
        MTLStencilDescriptor *st = [MTLStencilDescriptor new];
        st.stencilCompareFunction = cmp_fn(rs[56]);
        st.stencilFailureOperation = stencil_op(rs[53]);
        st.depthFailureOperation = stencil_op(rs[54]);
        st.depthStencilPassOperation = stencil_op(rs[55]);
        st.readMask = rs[58] & 255; st.writeMask = rs[59] & 255;
        d.frontFaceStencil = st; d.backFaceStencil = st;
    }
    s = [mtl newDepthStencilStateWithDescriptor:d];
    map_put(&dsstates, key, s);
    return s;
}
static id<MTLDepthStencilState> depth_state(void) { return depth_state_for(dev.rs); }
// compare: a hardware shadow map (depth texture) lookup, LESSEQUAL against the reference depth, filtered like NVIDIA PCF;
// 2 = the same clamped to the edge (never the border): the shadows probe's lookups into its 1x1 depth-0 map (below)
static unsigned long long stat_minfilt[3], stat_aniso16, stat_mipbelow, stat_lodbias;   // diag: min filter point/linear/anisotropic
static id<MTLSamplerState> sampler_state(int stage, int compare) {
    uint32_t *ss = dev.ss[stage];
    int white_border = compare == 1 && x360_shadow_border();
    uint32_t key[4] = {ss[1] | ss[2] << 4 | ss[3] << 8 | ss[5] << 12 | ss[6] << 16 | ss[7] << 20 | (uint32_t)compare << 24, ss[10], ss[4], ss[9]};
    key[0] |= (uint32_t)white_border << 28;   // F10 shadow fit can change this while the sampler cache stays alive
    for (int i = 0; i < nsamplers; ++i) if (!memcmp(sampler_keys[i], key, sizeof key)) return samplers[i];
    MTLSamplerDescriptor *d = [MTLSamplerDescriptor new];
    MTLSamplerAddressMode am[6] = {MTLSamplerAddressModeRepeat, MTLSamplerAddressModeRepeat, MTLSamplerAddressModeMirrorRepeat,
                                   MTLSamplerAddressModeClampToEdge, MTLSamplerAddressModeClampToBorderColor, MTLSamplerAddressModeMirrorClampToEdge};
    d.sAddressMode = am[ss[1] < 6 ? ss[1] : 1]; d.tAddressMode = am[ss[2] < 6 ? ss[2] : 1]; d.rAddressMode = am[ss[3] < 6 ? ss[3] : 1];
    d.magFilter = ss[5] >= 2 ? MTLSamplerMinMagFilterLinear : MTLSamplerMinMagFilterNearest;
    d.minFilter = ss[6] >= 2 ? MTLSamplerMinMagFilterLinear : MTLSamplerMinMagFilterNearest;
    d.mipFilter = ss[7] == 0 ? MTLSamplerMipFilterNotMipmapped : ss[7] == 1 ? MTLSamplerMipFilterNearest : MTLSamplerMipFilterLinear;
    if (ss[5] == 3 || ss[6] == 3) d.maxAnisotropy = ss[10] < 1 ? 1 : ss[10] > 16 ? 16 : ss[10];
    d.borderColor = (ss[4] >> 24) > 127 ? MTLSamplerBorderColorOpaqueBlack : MTLSamplerBorderColorTransparentBlack;
    if (ss[4] == 0xFFFFFFFF) d.borderColor = MTLSamplerBorderColorOpaqueWhite;
    d.lodMinClamp = (float)ss[9];
    if (compare) d.compareFunction = MTLCompareFunctionLessEqual;
    static const char *test_cmp = (const char *)1;      // test only: NFS_TEST_SHADOW_COMPARE=never|always|greater
    if (test_cmp == (const char *)1) test_cmp = getenv("NFS_TEST_SHADOW_COMPARE");
    if (compare && test_cmp) d.compareFunction = !strcmp(test_cmp, "never") ? MTLCompareFunctionNever : !strcmp(test_cmp, "always") ?
                                                MTLCompareFunctionAlways : MTLCompareFunctionGreater;
    if (white_border) { // X360 shadow sampler: outside the map reads depth 1 (lit)
        d.sAddressMode = d.tAddressMode = MTLSamplerAddressModeClampToBorderColor;
        d.borderColor = MTLSamplerBorderColorOpaqueWhite;
    }
    // The probe's map is one texel: through a border sampler, linear PCF blends it with the white border by the distance
    // from the map's centre (up to 75 % lit at the corners), so receivers the real map shadows came out brighter.
    if (compare == 2) d.sAddressMode = d.tAddressMode = MTLSamplerAddressModeClampToEdge;
    id<MTLSamplerState> s = [mtl newSamplerStateWithDescriptor:d];
    if (diag_on && nsamplers >= MAX_SAMPLERS) ++ad1.samp_miss_full;
    if (nsamplers < MAX_SAMPLERS) { memcpy(sampler_keys[nsamplers], key, sizeof key); samplers[nsamplers++] = s; }
    return s;
}

// ---------------- draw ----------------
static MTLPrimitiveType prim_type(uint32_t t) {
    switch (t) { case 1: return MTLPrimitiveTypePoint; case 2: return MTLPrimitiveTypeLine; case 3: return MTLPrimitiveTypeLineStrip;
                 case 5: return MTLPrimitiveTypeTriangleStrip; }
    return MTLPrimitiveTypeTriangle;
}
static uint32_t prim_vertices(uint32_t t, uint32_t n) {
    switch (t) { case 1: return n; case 2: return 2 * n; case 3: return n + 1; case 4: return 3 * n; case 5: case 6: return n + 2; }
    return 0;
}



// ---- BOOT_DIAG: the GPU side of the mirror's cop lightbar flares (types 5/6 drawn inside eRenderLightFlare for the mirror view) ----
// mirror_flare_cop() (mirror.c) is true while 00505380 runs for such a flare. Per second: draw_impl calls (entered), draws
// that reached a Metal draw (encoded; the rest returned early: shader still compiling, no target, empty scissor, ...), and
// for the encoded ones the pixels that passed the depth/stencil test and the shader (a Metal visibility count of that one
// draw, divided by the pass's sample count, read once its frame has finished on the GPU) in bins 0 / 1-3 / 4-15 / 16+.
// BOOT_DIAG_FLARE_DEPTHAB=1: every other second the mirror's cop flares draw with the depth test off (the paired
// comparison of depth-tested and untested pixel counts; those seconds are marked "nodepth" in the line).
int mirror_flare_cop(void);
void mirror_flare_info(int *type, float *intensity);
static struct { unsigned entered, encoded, bin[4], zoff; double pix; unsigned last[12]; } mfs[2];     // [0] depth as the game set, [1] forced off
static struct { uint32_t slot, samples; uint64_t serial; int ab; int type; float intensity, sim, v[8]; uint32_t col; } mf_pend[1024];
static int mf_npend, mf_active, mf_encoded, mf_nodepth, mf_ab_on = -1;
// Draw order inside the mirror's render target (diag): mirrorpass.txt lists, for ~40 frames a while into the run, every
// draw and clear into a target of the flares' size with "F" for a cop flare, so the flares' place in the pass shows.
static uint32_t mf_rt_w, mf_rt_h; static uint64_t mf_first_serial;
#define rs_z(i) (dev.rs[i])
static void mf_pass_log(const char *what, uint32_t w, uint32_t h) {
    if (!diag_on || !mf_rt_w || w != mf_rt_w || h != mf_rt_h || frame_serial < mf_first_serial + 900 || frame_serial > mf_first_serial + 940) return;
    static FILE *f; static int tried; static uint64_t last_serial; static unsigned idx;
    if (!f && !tried++ && diag_dir) { char path[600]; snprintf(path, sizeof path, "%s/mirrorpass.txt", diag_dir); f = fopen(path, "w"); }
    if (!f) return;
    if (last_serial != frame_serial) { last_serial = frame_serial; idx = 0; fprintf(f, "frame %llu\n", (unsigned long long)frame_serial); }
    fprintf(f, "%u %s\n", idx++, what);
}
static void mf_collect(void) {
    int n = 0;
    for (int i = 0; i < mf_npend; ++i) {
        if (in_flight(mf_pend[i].serial)) { mf_pend[n++] = mf_pend[i]; continue; }
        uint64_t v = ((uint64_t *)vis_buf.contents)[mf_pend[i].slot] / (mf_pend[i].samples ? mf_pend[i].samples : 1);
        int ab = mf_pend[i].ab;
        static FILE *ff; static int ff_tried;
        if (!ff && !ff_tried++ && diag_dir) { char path[600]; snprintf(path, sizeof path, "%s/flaredraws.txt", diag_dir); ff = fopen(path, "w"); }
        if (ff) fprintf(ff, "%llu sim %.4f type %d nodepth %d intensity %.4f col %08X px %llu v0 %.4g %.4g %.4g %.4g %.4g %.4g %.4g %.4g\n", (unsigned long long)mf_pend[i].serial,
                        mf_pend[i].sim, mf_pend[i].type, ab, mf_pend[i].intensity, mf_pend[i].col, (unsigned long long)v,
                        mf_pend[i].v[0], mf_pend[i].v[1], mf_pend[i].v[2], mf_pend[i].v[3], mf_pend[i].v[4], mf_pend[i].v[5], mf_pend[i].v[6], mf_pend[i].v[7]);
        mfs[ab].pix += (double)v;
        ++mfs[ab].bin[v == 0 ? 0 : v < 4 ? 1 : v < 16 ? 2 : 3];
    }
    mf_npend = n;
}
void render_flare_diag_second(void) {
    if (!diag_on) return;
    mf_collect();
    static unsigned parity;
    ++parity;
    if (mfs[0].entered || mfs[1].entered) {
        char line[500]; int k = 0;
        for (int ab = 0; ab < 2; ++ab) {
            if (!mfs[ab].entered) continue;
            unsigned *l = mfs[ab].last;
            k += snprintf(line + k, sizeof line - k, " %s entered=%u encoded=%u pixels0/1-3/4-15/16+=%u/%u/%u/%u mean_px=%.1f | last: z%u zw%u zf%u blend%u(%u,%u) vp %ux%u@%u,%u rt %ux%u",
                          ab ? "nodepth" : "depth", mfs[ab].entered, mfs[ab].encoded, mfs[ab].bin[0], mfs[ab].bin[1], mfs[ab].bin[2], mfs[ab].bin[3],
                          mfs[ab].encoded ? mfs[ab].pix / mfs[ab].encoded : 0.0, l[0], l[1], l[2], l[3], l[4], l[5], l[6], l[7], l[8], l[9], l[10], l[11]);
        }
        diag_event("flaredraw:%s", line);
    }
    memset(mfs, 0, sizeof mfs);
    if (mf_ab_on < 0) mf_ab_on = getenv("BOOT_DIAG_FLARE_DEPTHAB") != NULL;
    mf_nodepth = mf_ab_on && (parity & 1);
}

static void draw_impl(uint32_t type, uint32_t prims, int indexed, int32_t base_vertex, uint32_t min_index, uint32_t num_vertices,
                      uint32_t start, uint32_t up_vertices, uint32_t up_stride, uint32_t up_indices, uint32_t up_index_fmt);
static void drawshot(void);
// NFS_LAMP_BATCH=0: immediate replays; =alt (BOOT_DIAG A/B in one run): lampglow.c flips the mode every diag
// second and logs it; look variant `lampsplit` (BOOT_DIAG_LOOK_AB): the held pass replays immediately.
int lamp_batch_alt;
static int lamp_batch(void) {
    static int on = -1;
    if (on < 0) { const char *e = getenv("NFS_LAMP_BATCH"); on = !(e && e[0] == '0'); lamp_batch_alt = diag_on && e && !strcmp(e, "alt"); }
    extern int postfx_look_variant(const char *v);
    return on && !postfx_look_variant("lampsplit") && !(lamp_batch_alt & 2);
}
// This draw writes depth or stencil (depth_state_for's rules): recorded lamp replays must run before it.
static int lamp_writes_depth_stencil(void) {
    const uint32_t *rs = dev.rs;
    if (!dev.ds) return 0;
    if (rs[7] && rs[14]) return 1;
    return rs[52] && (rs[59] & 255) && (rs[53] != 1 || rs[54] != 1 || rs[55] != 1);   // an op other than KEEP
}
// Record this flare's replay into lamp_rt (lamp_flush invokes it); the scene encoder and its bindings stay as
// they are. draw_impl runs exactly as for an immediate replay (lamp_replay != 0: no depth/stencil writes, no aux).
static void lamp_record(uint32_t type, uint32_t prims, int indexed, int32_t base_vertex, uint32_t min_index, uint32_t num_vertices,
                        uint32_t start, uint32_t up_vertices, uint32_t up_stride, uint32_t up_indices, uint32_t up_index_fmt) {
    NSUInteger lv, sl;
    id<MTLTexture> z = surface_target(dev.ds, &lv, &sl);
    unsigned owner = lampglow_owner();
    if (lamp_n && (z != lamp_z || owner != lamp_owner || lamp_scissor_on != dev.rs[174] ||
        memcmp(lamp_viewport, dev.viewport, sizeof lamp_viewport) || memcmp(lamp_scissor, dev.scissor, sizeof lamp_scissor))) lamp_flush(2);
    if (!lamp_rec) lamp_rec = lamprec_new();
    if (!lamp_n) memset(&lamp_eb, 0, sizeof lamp_eb);
    lamp_saved_enc = enc; lamp_saved_eb = eb; lamp_saved_dirty = pass_dirty; lamp_saved_samples = enc_samples;
    memcpy(lamp_saved_ms, enc_ms, sizeof enc_ms); memcpy(lamp_saved_resolve, enc_resolve, sizeof enc_resolve);
    enc = (id<MTLRenderCommandEncoder>)lamp_rec; eb = lamp_eb; pass_dirty = 0;
    memset(enc_ms, 0, sizeof enc_ms); memset(enc_resolve, 0, sizeof enc_resolve);
    enc_ms[0] = lamp_rt.ms ? &lamp_rt : NULL;          // as ms_attach in lamp_open
    enc_samples = lamp_rt.ms ? (uint32_t)lamp_rt.ms.sampleCount : 1;
    lamp_replay = 2;
    draw_impl(type, prims, indexed, base_vertex, min_index, num_vertices, start, up_vertices, up_stride, up_indices, up_index_fmt);
    lamp_replay = 0;
    lamp_eb = eb;
    enc = lamp_saved_enc; eb = lamp_saved_eb; pass_dirty = lamp_saved_dirty; enc_samples = lamp_saved_samples;
    memcpy(enc_ms, lamp_saved_ms, sizeof enc_ms); memcpy(enc_resolve, lamp_saved_resolve, sizeof enc_resolve);
    lamp_saved_enc = nil;
    lamp_z = z; lamp_owner = owner;
    memcpy(lamp_viewport, dev.viewport, sizeof lamp_viewport); memcpy(lamp_scissor, dev.scissor, sizeof lamp_scissor);
    lamp_scissor_on = dev.rs[174]; ++lamp_n;
}
// Record coverage against the final scene depth; retain the scene encoder and
// its bindings. The recorder already retains buffer/texture/constant lifetimes.
static void ssao_record(uint32_t type,uint32_t prims,int indexed,int32_t base_vertex,uint32_t min_index,uint32_t num_vertices,
                        uint32_t start,uint32_t up_vertices,uint32_t up_stride,uint32_t up_indices,uint32_t up_index_fmt) {
    uint64_t t0=ssao_cpu_diag?host_ticks():0;
    MatTreesDraw saved_tree=mat_trees_draw;
    ssao_saved_enc=enc;ssao_saved_eb=eb;ssao_saved_dirty=pass_dirty;ssao_saved_samples=enc_samples;
    memcpy(ssao_saved_ms,enc_ms,sizeof enc_ms);memcpy(ssao_saved_resolve,enc_resolve,sizeof enc_resolve);
    uint32_t saved_alpha=dev.rs[15];
    for(int reference=0;reference<=(ssao_proof()?2:0);++reference){
    // Each replay must prepare the same tree material. The previous replay
    // consumed its skip state; without restoring it the proof used PC trees.
    mat_trees_draw=saved_tree;if(ssao_tree_fringe)mat_trees_draw.skip=TREES_NO_DRAW;
    dev.rs[15]=reference==1?saved_alpha:0;
    ssao_reference_record=reference==1;ssao_cpu_reference_record=reference==2;ssao_coverage_z=reference==1?ssao_depth:ssao_half_depth;
    enc=(id<MTLRenderCommandEncoder>)(reference==2?ssao_cpu_reference_rec:reference?ssao_reference_rec:ssao_rec);eb=reference==2?ssao_cpu_reference_eb:reference?ssao_reference_eb:ssao_eb;pass_dirty=0;
    memset(enc_ms,0,sizeof enc_ms);memset(enc_resolve,0,sizeof enc_resolve);
    RenderTex *target=ssao_target();enc_samples=(uint32_t)(target->ms?target->ms.sampleCount:1);ssao_replay=2;
    draw_impl(type,prims,indexed,base_vertex,min_index,num_vertices,start,up_vertices,up_stride,up_indices,up_index_fmt);
    if(reference==2)ssao_cpu_reference_eb=eb;else if(reference)ssao_reference_eb=eb;else ssao_eb=eb;
    }
    dev.rs[15]=saved_alpha;ssao_reference_record=ssao_cpu_reference_record=0;ssao_coverage_z=ssao_half_depth;ssao_replay=0;enc=ssao_saved_enc;eb=ssao_saved_eb;pass_dirty=ssao_saved_dirty;enc_samples=ssao_saved_samples;
    memcpy(enc_ms,ssao_saved_ms,sizeof enc_ms);memcpy(enc_resolve,ssao_saved_resolve,sizeof enc_resolve);ssao_saved_enc=nil;mat_trees_draw=saved_tree;
    if(ssao_cpu_diag){ssao_diag_record_ns+=host_ticks()-t0;++ssao_diag_draws;}
}
void render_draw(uint32_t type, uint32_t prims, int indexed, int32_t base_vertex, uint32_t min_index, uint32_t num_vertices,
                 uint32_t start, uint32_t up_vertices, uint32_t up_stride, uint32_t up_indices, uint32_t up_index_fmt) {
    static int diag_init;
    if(diag_on&&!diag_init){
        diag_init=1;const char *e=getenv("BOOT_DIAG_SSAO_CPU");ssao_cpu_diag=!(e&&*e=='0');
    }
    if(ssao_cpu_diag)ssao_diag_frame();
    uint64_t draw_t0=ssao_cpu_diag?host_ticks():0;
    ssao_tree_fringe=0;
    if (lamp_n && lamp_writes_depth_stencil()) lamp_flush(1);
    mf_active = diag_on && mirror_flare_cop();
    mf_encoded = 0;
    int mf_n0 = mf_npend;
    draw_impl(type, prims, indexed, base_vertex, min_index, num_vertices, start, up_vertices, up_stride, up_indices, up_index_fmt);
    if (mf_active) {
        ++mfs[mf_nodepth].entered; mfs[mf_nodepth].encoded += mf_encoded; mf_active = 0;
        if (mf_encoded && mf_npend == mf_n0 + 1 && up_vertices && !indexed) {         // the flare's identity, for flaredraws.txt
            typeof(mf_pend[0]) *e = &mf_pend[mf_npend - 1];
            int ty; float it; mirror_flare_info(&ty, &it);
            e->type = ty; e->intensity = it; e->sim = MF32(0x009885D8u);
            for (int k = 0; k < 8; ++k) e->v[k] = MF32(up_vertices + 4 * k);
            Elem el[32]; int n = decl_elems(el);
            for (int k = 0; k < n; ++k) if (el[k].usage == SM_USAGE_COLOR && !el[k].index && el[k].type == 4) { e->col = M32(up_vertices + el[k].offset); break; }
        }
    }
    if (lampglow_type() && up_vertices && !indexed && type == 6 && prims == 2) {
        if (diag_on) {
            Elem el[32]; int n = decl_elems(el);
            for (int k = 0; k < n; ++k) if (el[k].usage == SM_USAGE_COLOR && !el[k].index && el[k].type == 4) {
                float sum = 0;
                for (int v = 0; v < 4; ++v) { uint32_t c = M32(up_vertices + v * up_stride + el[k].offset); sum += ((c & 255) + ((c >> 8) & 255) + ((c >> 16) & 255)) * (c >> 24) / (255.0f * 255.0f); }
                lampglow_draw(sum * 0.25f); break;
            }
        }
        int lamp_ok = lampglow_enabled() ? lamp_prepare() : -1;
        if (lamp_ok >= 0) lampglow_replayed(lamp_ok);
        if (lamp_ok > 0 && lamp_batch()) lamp_record(type, prims, indexed, base_vertex, min_index, num_vertices, start, up_vertices, up_stride, up_indices, up_index_fmt);
        else if (lamp_ok > 0) {
            end_pass(); lamp_replay = 1;
            draw_impl(type, prims, indexed, base_vertex, min_index, num_vertices, start, up_vertices, up_stride, up_indices, up_index_fmt);
            end_pass(); lamp_replay = 0;
        }
    }
    if(ssao_cpu_diag)ssao_diag_draw_ns+=host_ticks()-draw_t0;
    uint64_t setup_t0=ssao_cpu_diag?host_ticks():0,record_before=ssao_diag_record_ns;
    if (!ssao_can_record()) {
        if (ssao_rec) { rt_render_list_replay(ssao_rec,nil);ssao_rec=nil;ssao_serial=ssao_cleared=0; }
    } else if (!lamp_replay) {
        NSUInteger lv,sl;
        id<MTLTexture> color=surface_target(dev.rt[0],&lv,&sl),z=surface_target(dev.ds,&lv,&sl);
        RenderTex *zr=surface_rt(dev.ds);
        if (color && z && zr && color.width==z.width && color.height==z.height && color.sampleCount==z.sampleCount &&
            (color.pixelFormat==MTLPixelFormatBGRA8Unorm || color.pixelFormat==MTLPixelFormatRGBA16Float) && ssao_prepare(z)) {
            // Initialise once even when this scene contains no transparent draws.
            if (ssao_serial!=frame_serial || ssao_depth!=zr->tex) {
                ssao_depth=zr->tex;ssao_coverage_z=ssao_half_depth;ssao_serial=frame_serial;ssao_cleared=0;
                ssao_rec=rt_render_list_new();memset(&ssao_eb,0,sizeof ssao_eb);
                ssao_reference_rec=ssao_proof()?lamprec_new():nil;memset(&ssao_reference_eb,0,sizeof ssao_reference_eb);
                ssao_cpu_reference_rec=ssao_proof()?lamprec_new():nil;memset(&ssao_cpu_reference_eb,0,sizeof ssao_cpu_reference_eb);
            }
            if ((dev.rs[27] || ssao_tree_fringe) && (dev.rs[168]&7) && !q_active) {
                ssao_record(type,prims,indexed,base_vertex,min_index,num_vertices,start,up_vertices,up_stride,up_indices,up_index_fmt);
            }
        }
    }
    if(ssao_cpu_diag)ssao_diag_setup_ns+=host_ticks()-setup_t0-(ssao_diag_record_ns-record_before);
    materials_grass_clear();           // consume once, including draw_impl's early returns and both buffer paths
    if (mtl && prims && texdump_frame()) drawshot();
    if (diag_on && q_active && up_vertices && type == 6 && !indexed)   // the occlusion test quad (a fan): where it is
        for (uint32_t i = 0; i < prims + 2; ++i) {
            float x = MF32(up_vertices + i * up_stride), y = MF32(up_vertices + i * up_stride + 4);
            if (!i) { q_bb[0] = q_bb[2] = x; q_bb[1] = q_bb[3] = y; }
            q_bb[0] = fminf(q_bb[0], x); q_bb[1] = fminf(q_bb[1], y); q_bb[2] = fmaxf(q_bb[2], x); q_bb[3] = fmaxf(q_bb[3], y);
        }
}
// ---------------- fixed function ----------------
// Draws with no shaders (the effects' *_fixed techniques, e.g. world_fixed for the environment-map and reflection
// renders) get equivalent vs_1_1/ps_1_1 bytecode built from the fixed-function state, which then goes through the
// translator like any game shader. Supported: no lighting, untransformed positions, stage 0 combining TEXTURE /
// DIFFUSE / TFACTOR with DISABLE, SELECTARG1/2, MODULATE(2X/4X) or ADD, stage 1 disabled, no texture coordinate
// generation or transforms. Anything else is still skipped (and counted). The VS reads its own constants:
// c0-c3 world*view*projection (columns), c4 white (default diffuse), c5 zero; the PS reads c0 = TEXTUREFACTOR.
enum { RT_TEMP = 0, RT_INPUT = 1, RT_CONST = 2, RT_TEXTURE = 3, RT_RASTOUT = 4, RT_ATTROUT = 5, RT_TEXCRDOUT = 6 };
static uint32_t ff_dst(int type, int reg, int mask, int shift) { return 0x80000000u | (uint32_t)reg | ((uint32_t)(type & 7) << 28) | ((uint32_t)(type >> 3) << 11) | ((uint32_t)mask << 16) | ((uint32_t)(shift & 15) << 24); }
static uint32_t ff_src(int type, int reg) { return 0x80000000u | (uint32_t)reg | ((uint32_t)(type & 7) << 28) | ((uint32_t)(type >> 3) << 11) | (0xE4u << 16); }
typedef struct { uint64_t key; Obj vs, ps; } FFShader;
static FFShader ff_cache[64];
static int nff_cache;
static void ff_build(FFShader *f, int has_col, int has_tc, int tc_set,
                     uint32_t cop, uint32_t ca1, uint32_t ca2, uint32_t aop, uint32_t aa1, uint32_t aa2) {
    uint32_t v[64], n = 0, q[64], m = 0;
    v[n++] = 0xFFFE0101u;
    v[n++] = 31; v[n++] = 0x80000000u | SM_USAGE_POSITION; v[n++] = ff_dst(RT_INPUT, 0, 15, 0);
    if (has_col) { v[n++] = 31; v[n++] = 0x80000000u | SM_USAGE_COLOR; v[n++] = ff_dst(RT_INPUT, 1, 15, 0); }
    if (has_tc) { v[n++] = 31; v[n++] = 0x80000000u | SM_USAGE_TEXCOORD | (uint32_t)tc_set << 16; v[n++] = ff_dst(RT_INPUT, 2, 15, 0); }
    for (int r = 0; r < 4; ++r) { v[n++] = 9; v[n++] = ff_dst(RT_RASTOUT, 0, 1 << r, 0); v[n++] = ff_src(RT_INPUT, 0); v[n++] = ff_src(RT_CONST, r); }   // dp4 oPos.r, v0, c[r]
    v[n++] = 1; v[n++] = ff_dst(RT_ATTROUT, 0, 15, 0); v[n++] = has_col ? ff_src(RT_INPUT, 1) : ff_src(RT_CONST, 4);   // mov oD0
    v[n++] = 1; v[n++] = ff_dst(RT_TEXCRDOUT, 0, 15, 0); v[n++] = has_tc ? ff_src(RT_INPUT, 2) : ff_src(RT_CONST, 5);  // mov oT0
    v[n++] = 0x0000FFFFu;
    // ps_1_1: args TEXTURE = t0, DIFFUSE/CURRENT = v0, TFACTOR = c0
    q[m++] = 0xFFFF0101u;
    int uses_tex = (cop != 1 && (ca1 == 2 || ca2 == 2)) || (aop != 1 && (aa1 == 2 || aa2 == 2));
    if (uses_tex) { q[m++] = 66; q[m++] = ff_dst(RT_TEXTURE, 0, 15, 0); }
    for (int half = 0; half < 2; ++half) {
        uint32_t op = half ? aop : cop, a1 = half ? aa1 : ca1, a2 = half ? aa2 : ca2;
        int mask = half ? 8 : 7;
        uint32_t s1 = a1 == 2 ? ff_src(RT_TEXTURE, 0) : a1 == 3 ? ff_src(RT_CONST, 0) : ff_src(RT_INPUT, 0);
        uint32_t s2 = a2 == 2 ? ff_src(RT_TEXTURE, 0) : a2 == 3 ? ff_src(RT_CONST, 0) : ff_src(RT_INPUT, 0);
        uint32_t co = half ? 0x40000000u : 0;
        if (op == 1) { q[m++] = 1 | co; q[m++] = ff_dst(RT_TEMP, 0, mask, 0); q[m++] = ff_src(RT_INPUT, 0); }          // DISABLE: diffuse
        else if (op == 2 || op == 3) { q[m++] = 1 | co; q[m++] = ff_dst(RT_TEMP, 0, mask, 0); q[m++] = op == 2 ? s1 : s2; }
        else if (op == 7) { q[m++] = 2 | co; q[m++] = ff_dst(RT_TEMP, 0, mask, 0); q[m++] = s1; q[m++] = s2; }
        else { q[m++] = 5 | co; q[m++] = ff_dst(RT_TEMP, 0, mask, op == 5 ? 1 : op == 6 ? 2 : 0); q[m++] = s1; q[m++] = s2; }
    }
    q[m++] = 0x0000FFFFu;
    f->vs.tokens = malloc(4 * n); memcpy(f->vs.tokens, v, 4 * n); f->vs.ntokens = n;
    f->ps.tokens = malloc(4 * m); memcpy(f->ps.tokens, q, 4 * m); f->ps.ntokens = m;
}
static void ff_shader_warm(void) {
    // The effects' fixed world/environment passes use stage-0 texture/diffuse
    // modulation/selection. Use the same builder, without occupying ff_cache.
    const uint32_t ops[][2] = {{4,4}, {5,4}, {6,4}, {4,2}, {2,2}, {1,1}};
    SmOptions opt; sm_default_options(&opt);
    for (int col = 0; col <= 1; ++col) for (int tc = -1; tc < 8; ++tc) {
        for (unsigned i = 0; i < sizeof ops / sizeof *ops; ++i) {
            FFShader f = {0};
            ff_build(&f, col, tc >= 0, tc >= 0 ? tc : 0, ops[i][0], 2, 1, ops[i][1], 2, 1);
            get_shader(&f.vs, &opt); get_shader(&f.ps, &opt);
            free(f.vs.tokens); free(f.ps.tokens);
        }
    }
}
// Returns 0 when the state is outside the supported subset.
static int ff_shaders(Obj **vs_out, Obj **ps_out, float (*vsc)[4], float (*psc)[4]) {
    uint32_t *rs = dev.rs, (*tss)[33] = dev.tss;
    if (rs[137]) return 0;                                             // LIGHTING
    Elem el[32];
    int nel = decl_elems(el), has_pos = 0, has_col = 0, tc_set = (int)(tss[0][11] & 0xFFFF), has_tc = 0;
    if (tss[0][11] >> 16 || (tss[0][24] & 255)) return 0;              // texcoord generation, texture transform
    for (int i = 0; i < nel; ++i) {
        if (el[i].usage == SM_USAGE_POSITIONT) return 0;
        if (el[i].usage == SM_USAGE_POSITION && el[i].index == 0) has_pos = 1;
        if (el[i].usage == SM_USAGE_COLOR && el[i].index == 0) has_col = 1;
        if (el[i].usage == SM_USAGE_TEXCOORD && el[i].index == tc_set) has_tc = 1;
    }
    if (!has_pos) return 0;
    uint32_t cop = tss[0][1], ca1 = tss[0][2], ca2 = tss[0][3], aop = tss[0][4], aa1 = tss[0][5], aa2 = tss[0][6];
    if (cop != 1 && tss[1][1] != 1) return 0;                          // stage 1 must be disabled
    if (cop > 7 || aop > 7 || (cop != 1 && cop < 2) || ca1 > 3 || ca2 > 3 || aa1 > 3 || aa2 > 3) return 0;
    if (cop == 1) { aop = 1; }
    uint64_t key = (uint64_t)has_col | (uint64_t)has_tc << 1 | (uint64_t)tc_set << 2 | (uint64_t)cop << 8 | (uint64_t)ca1 << 12 | (uint64_t)ca2 << 16
                 | (uint64_t)aop << 20 | (uint64_t)aa1 << 24 | (uint64_t)aa2 << 28;
    FFShader *f = NULL;
    for (int i = 0; i < nff_cache; ++i) if (ff_cache[i].key == key) f = &ff_cache[i];
    if (!f) {
        if (nff_cache == 64) return 0;
        f = &ff_cache[nff_cache++];
        memset(f, 0, sizeof *f);
        f->key = key;
        ff_build(f, has_col, has_tc, tc_set, cop, ca1, ca2, aop, aa1, aa2);
    }
    // constants: c0-c3 = columns of world * view * projection (row vectors, as D3D9 transforms them)
    float *W = dev.transform[256], *V = dev.transform[2], *P = dev.transform[3], wv[16], wvp[16];
    for (int r = 0; r < 4; ++r) for (int c = 0; c < 4; ++c) { wv[r * 4 + c] = 0; for (int k = 0; k < 4; ++k) wv[r * 4 + c] += W[r * 4 + k] * V[k * 4 + c]; }
    for (int r = 0; r < 4; ++r) for (int c = 0; c < 4; ++c) { wvp[r * 4 + c] = 0; for (int k = 0; k < 4; ++k) wvp[r * 4 + c] += wv[r * 4 + k] * P[k * 4 + c]; }
    memset(vsc, 0, 16 * 8);
    for (int i = 0; i < 4; ++i) for (int k = 0; k < 4; ++k) vsc[i][k] = wvp[k * 4 + i];
    for (int k = 0; k < 4; ++k) vsc[4][k] = 1.0f;
    uint32_t tf = rs[60];
    psc[0][0] = ((tf >> 16) & 255) / 255.0f; psc[0][1] = ((tf >> 8) & 255) / 255.0f; psc[0][2] = (tf & 255) / 255.0f; psc[0][3] = (tf >> 24) / 255.0f;
    *vs_out = &f->vs; *ps_out = &f->ps;
    return 1;
}
// Float constants of stage 0 (vertex, buffer 16) or 1 (pixel, buffer 0): `n` registers from `src` (the device
// array, version `ver`; ver 0 = synthesized fixed-function constants, always uploaded). An upload is reused while
// the array is unchanged in this frame and covers n registers; `lim` registers exist, the rest read as zero.
static int bind_floats(int st, const float *src, uint32_t ver, uint32_t n, uint32_t lim) {
    static struct { uint32_t ver, n, off; uint64_t serial; } up[2];
    static id<MTLBuffer> buffers[2];
    if (n < 1) n = 1;
    if (!ver || up[st].ver != ver || up[st].n < n || up[st].serial != frame_serial) {
        uint32_t off;
        __unsafe_unretained id<MTLBuffer> b;
        float *p = ring_alloc(16 * (NSUInteger)n, &off, &b);
        if (!p) return 0;
        uint32_t k = n < lim ? n : lim;
        memcpy(p, src, 16 * (size_t)k);
        if (n > k) memset(p + 4 * k, 0, 16 * (size_t)(n - k));
        up[st] = (typeof(up[0])){ver, n, off, frame_serial};
        if (buffers[st] != b) buffers[st] = b;
    }
    id<MTLBuffer> b = buffers[st];
    if (eb.cbuf[st] != (__bridge void *)b) {
        if (st) [enc setFragmentBuffer:b offset:up[st].off atIndex:0]; else [enc setVertexBuffer:b offset:up[st].off atIndex:16];
        eb.cbuf[st] = (__bridge void *)b;
    } else if (eb.coff[st] != up[st].off) {
        if (st) [enc setFragmentBufferOffset:up[st].off atIndex:0]; else [enc setVertexBufferOffset:up[st].off atIndex:16];
    }
    eb.coff[st] = up[st].off;
    return 1;
}
// ---------------- R27 grass: assets, pipelines and shared geometry emission ----------------
// The original PC entries/reflection remain available. A complete set of grass resources
// and pipelines is prepared before emitting anything; any failure emits exactly one PC draw.
// This section never changes dev.* and never uploads or scans geometry a second time.
typedef struct { uint8_t reg, fmt_type, buf, zero; uint16_t offset; } DrawAttr;
typedef struct {
    int n;
    __unsafe_unretained id<MTLRenderPipelineState> pso[5];
    __unsafe_unretained id<MTLDepthStencilState> depth[2];
    __unsafe_unretained id<MTLTexture> noise, volume, shadow, normal, specular, opacity;
    float k[64][4];
} GrassDraw;
static id<MTLTexture> grass_noise_texture, grass_volume_texture;
static int grass_upload(void) {
    if (grass_noise_texture && grass_volume_texture) return 1;
    uint32_t w, h, bytes;
    const uint8_t *noise = mat_grass_noise(&w, &h), *volume = mat_grass_volume(&bytes);
    if (!noise || !volume || bytes != 149796) return 0;
    MTLTextureDescriptor *nd = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm width:w height:h mipmapped:YES];
    id<MTLTexture> nt = [mtl newTextureWithDescriptor:nd];
    MTLTextureDescriptor *vd = [MTLTextureDescriptor new];
    vd.textureType = MTLTextureType3D; vd.pixelFormat = MTLPixelFormatRGBA8Unorm;
    vd.width = vd.height = vd.depth = 32; vd.mipmapLevelCount = 6; vd.usage = MTLTextureUsageShaderRead;
    id<MTLTexture> vt = [mtl newTextureWithDescriptor:vd];
    uint8_t *a = malloc((size_t)w * h * 4), *b = malloc((size_t)w * h * 4);
    if (!nt || !vt || !a || !b) { free(a); free(b); return 0; }
    memcpy(a, noise, (size_t)w * h * 4);
    for (NSUInteger level = 0; level < nd.mipmapLevelCount; ++level) {
        [nt replaceRegion:MTLRegionMake2D(0, 0, w, h) mipmapLevel:level withBytes:a bytesPerRow:w * 4];
        if (w == 1 && h == 1) break;
        uint32_t nw = w > 1 ? w / 2 : 1, nh = h > 1 ? h / 2 : 1;
        for (uint32_t y = 0; y < nh; ++y) for (uint32_t x = 0; x < nw; ++x) for (int c = 0; c < 4; ++c) {
            uint32_t x1 = 2 * x + (w > 1), y1 = 2 * y + (h > 1);
            b[4 * (y * nw + x) + c] = (uint8_t)((a[4 * (2 * y * w + 2 * x) + c] + a[4 * (2 * y * w + x1) + c] +
                a[4 * (y1 * w + 2 * x) + c] + a[4 * (y1 * w + x1) + c] + 2) / 4);
        }
        uint8_t *swap = a; a = b; b = swap; w = nw; h = nh;
    }
    free(a); free(b);
    uint32_t off = 0;
    for (NSUInteger level = 0, size = 32; level < 6; ++level, size >>= 1) {
        [vt replaceRegion:MTLRegionMake3D(0, 0, 0, size, size, size) mipmapLevel:level slice:0 withBytes:volume + off
              bytesPerRow:size * 4 bytesPerImage:size * size * 4];
        off += (uint32_t)(size * size * size * 4);
    }
    grass_noise_texture = nt; grass_volume_texture = vt;
    return 1;
}
// Grass pipelines are built on shader_q like the shaders: the first grass draw of a race built them on the game thread
// (a 93 ms frame, 17 of 21 samples). Same descriptor, same key; the draw stays PC (as while its shaders
// compile) until the game thread publishes the result into `pipelines` at the next grass_prepare.
typedef struct { uint64_t key; void *pso; } GrassPsoDone;
static pthread_mutex_t grass_pso_mu = PTHREAD_MUTEX_INITIALIZER;
static GrassPsoDone grass_pso_done[16]; static int grass_pso_ndone;
static uint64_t grass_pso_pend[16]; static int grass_pso_npend;            // game thread only
static uint64_t grass_pso_bad[16]; static int grass_pso_nbad;
static int grass_pso_failed(uint64_t key) { for (int i = 0; i < grass_pso_nbad; ++i) if (grass_pso_bad[i] == key) return 1; return 0; }
static void grass_pso_drain(void) {
    if (!grass_pso_npend) return;
    pthread_mutex_lock(&grass_pso_mu);
    for (int d = 0; d < grass_pso_ndone; ++d) {
        id<MTLRenderPipelineState> pso = (__bridge_transfer id<MTLRenderPipelineState>)grass_pso_done[d].pso;
        uint64_t key = grass_pso_done[d].key;
        for (int i = 0; i < grass_pso_npend; ++i) if (grass_pso_pend[i] == key) { grass_pso_pend[i] = grass_pso_pend[--grass_pso_npend]; break; }
        if (pso) map_put(&pipelines, key, pso);
        else if (grass_pso_nbad < 16) grass_pso_bad[grass_pso_nbad++] = key;
    }
    grass_pso_ndone = 0;
    pthread_mutex_unlock(&grass_pso_mu);
}
static int grass_pso_async(uint64_t key, MTLRenderPipelineDescriptor *pd) {
    static int on = -1;
    if (on < 0) on = !(getenv("NFS_ASYNC_SHADERS") && !strcmp(getenv("NFS_ASYNC_SHADERS"), "0")) && !getenv("NFS_TEST_GRASS_SYNC");
    if (!on || !shader_q) return 0;
    for (int i = 0; i < grass_pso_npend; ++i) if (grass_pso_pend[i] == key) return 1;
    if (grass_pso_npend >= 16) return 0;
    grass_pso_pend[grass_pso_npend++] = key;
    dispatch_async(shader_q, ^{ @autoreleasepool {
        NSError *err = nil;
        id<MTLRenderPipelineState> pso = pipeline_build(pd, __func__, &err);
        if (!pso) fprintf(stderr, "boot: render: grass pipeline failed: %s\n", err.localizedDescription.UTF8String);
        pthread_mutex_lock(&grass_pso_mu);
        if (grass_pso_ndone < 16) grass_pso_done[grass_pso_ndone++] = (GrassPsoDone){key, (__bridge_retained void *)pso};
        pthread_mutex_unlock(&grass_pso_mu);
    } });
    return 1;
}
static void grass_prepare(GrassDraw *g, Obj *vs, Obj *ps, const SmOptions *vo, const SmOptions *po, const ShaderEntry *pcvs, const ShaderEntry *pcps,
                               const DrawAttr *attr, int nattr, const uint32_t *strides, uint32_t streams, int need_zero,
                               uint64_t vkey, id<MTLTexture> rt, id<MTLTexture> ds) {
    // Filled in place: zeroing (and returning) the 1.1 KB struct for every draw cost ~0.04 us/draw. Everything but
    // k is zeroed (grass_emit reads nil normal/specular/opacity as "use zero_tex2d"); k is only read when n != 0,
    // and mat_grass_constants clears all of it before n can be set.
    _Static_assert(offsetof(GrassDraw, k) + sizeof(((GrassDraw *)0)->k) == sizeof(GrassDraw), "k must be last");
    memset(g, 0, offsetof(GrassDraw, k));
    if (!mat_grass_draw.mesh || mat_grass_draw.skip == GRASS_VIEW || mat_grass_draw.skip == GRASS_EFFECT || mat_grass_draw.skip == GRASS_TRANSFORM) return;
    mat_grass_draw.skip = GRASS_STATE;
    if (!dev.rs[7] || !ds || is_depth_pf(rt.pixelFormat) || (dev.rs[168] & 7) != 7 || q_active) return;
    mat_grass_draw.skip = GRASS_TRANSITION;
    if (!mat_grass_draw.textures[0] || !mat_grass_draw.textures[4]) return;
    if (mat_grass_draw.transition) {
        int tangent = 0;
        for (int i = 0; i < pcvs->refl.n_inputs; ++i) tangent |= pcvs->refl.inputs[i].usage == SM_USAGE_TANGENT && !pcvs->refl.inputs[i].usage_index;
        mat_grass_draw.skip = GRASS_INPUT;
        if (!tangent) return;
    }
    mat_grass_draw.skip = GRASS_CONSTANTS;
    int n = mat_grass_constants(&pcvs->refl, &pcps->refl, (const float (*)[4])dev.vsc, (const float (*)[4])dev.psc, g->k);
    if (n < 1 || n > 5) return;
    mat_grass_draw.skip = GRASS_PC;
    if (!mat_grass_enabled()) return;
    mat_grass_draw.skip = GRASS_DIFFUSE;
    Obj *diffuse = obj(dev.tex[0]);
    if (!diffuse || diffuse->kind != K_TEXTURE) return;
    mat_grass_draw.skip = GRASS_ASSETS;
    if (!grass_upload()) return;
    g->noise = grass_noise_texture; g->volume = grass_volume_texture;
    mat_grass_draw.skip = GRASS_TRANSITION;
    if (mat_grass_draw.transition) {
        Obj *normal = obj(mat_grass_texture(1)), *specular = obj(mat_grass_texture(3)), *opacity = obj(mat_grass_texture(4));
        if (!normal || !specular || !opacity || normal->kind != K_TEXTURE || specular->kind != K_TEXTURE || opacity->kind != K_TEXTURE) return;
        RenderTex *nt = gpu_tex(normal), *wt = gpu_tex(specular), *ot = gpu_tex(opacity);
        g->normal = nt ? nt->tex : nil; g->specular = wt ? wt->tex : nil; g->opacity = ot ? ot->tex : nil;
        if (!g->normal || !g->specular || !g->opacity) return;
    }
    // World uses shadow s1, worldnormalmap s3. Resolve from PC reflection rather
    // than confusing the normal map with shadow depth. Replacement slots are 0..6.
    int ss = mat_grass_shadow_slot(&pcps->refl);
    Obj *shadow = ss >= 0 ? obj(dev.tex[ss]) : NULL;
    RenderTex *st = shadow ? gpu_tex(shadow) : NULL;
    int depth = ss >= 0 && (po->shadow_samplers & (1u << ss)) != 0;
    g->shadow = st ? st->tex : depth ? zero_depth : zero_tex2d;
    mat_grass_draw.skip = GRASS_SHADOW;
    if ((depth && !is_depth_pf(g->shadow.pixelFormat)) || (!depth && is_depth_pf(g->shadow.pixelFormat))) return;
    // No-shadow t0 has no ShadowTransform. The otherwise-unused s1 can be nil.
    ShaderEntry *v[5], *p[5];
    int ready = 1;
    for (int i = 0; i < n; ++i) {
        uint32_t variant = (uint32_t)i + 1 + (mat_grass_draw.transition ? 5 : 0);
        v[i] = get_shader_variant(vs, vo, variant);
        p[i] = get_shader_variant(ps, po, variant);
        if (__atomic_load_n(&v[i]->pending, __ATOMIC_ACQUIRE) || __atomic_load_n(&p[i]->pending, __ATOMIC_ACQUIRE)) ready = 0;
        else if (!v[i]->ok || !p[i]->ok) ready = 0;
    }
    mat_grass_draw.skip = GRASS_SHADER;
    if (!ready) return;
    mat_grass_draw.skip = GRASS_PIPELINE;
    MTLVertexDescriptor *vd = [MTLVertexDescriptor vertexDescriptor];
    for (int k = 0; k < nattr; ++k) {
        int ai, au;
        vd.attributes[attr[k].reg].format = attr[k].zero ? MTLVertexFormatFloat4 : vfmt(attr[k].fmt_type, &ai, &au);
        vd.attributes[attr[k].reg].offset = attr[k].offset; vd.attributes[attr[k].reg].bufferIndex = attr[k].buf;
    }
    for (int s = 0; s < 16; ++s) if (streams & (1u << s)) { vd.layouts[s].stride = strides[s]; vd.layouts[s].stepFunction = MTLVertexStepFunctionPerVertex; }
    if (need_zero) { vd.layouts[30].stride = 16; vd.layouts[30].stepFunction = MTLVertexStepFunctionConstant; vd.layouts[30].stepRate = 0; }
    int missing = 0;
    grass_pso_drain();
    for (int i = 0; i < n; ++i) {
        uint32_t effective[5] = {(uint32_t)rt.pixelFormat, (uint32_t)ds.pixelFormat, (uint32_t)rt.sampleCount, dev.rs[168] & 15, i != 0};
        uint64_t key = fnv(effective, sizeof effective, fnv(&v[i]->key, 8, fnv(&p[i]->key, 8, vkey)));
        id<MTLRenderPipelineState> pso = (__bridge id<MTLRenderPipelineState>)map_get(&pipelines, key);
        if (!pso && grass_pso_failed(key)) return;
        if (!pso) {
            MTLRenderPipelineDescriptor *pd = [MTLRenderPipelineDescriptor new];
            pd.vertexFunction = v[i]->fn; pd.fragmentFunction = p[i]->fn; pd.vertexDescriptor = vd;
            pd.rasterSampleCount = rt.sampleCount; pd.colorAttachments[0].pixelFormat = rt.pixelFormat;
            pd.colorAttachments[0].writeMask = (MTLColorWriteMask)effective[3];
            // D3D's bit order is R,G,B,A; Metal's is A,B,G,R.
            pd.colorAttachments[0].writeMask = ((effective[3] & 1) ? MTLColorWriteMaskRed : 0) |
                ((effective[3] & 2) ? MTLColorWriteMaskGreen : 0) | ((effective[3] & 4) ? MTLColorWriteMaskBlue : 0) |
                ((effective[3] & 8) ? MTLColorWriteMaskAlpha : 0);
            pd.colorAttachments[0].blendingEnabled = i != 0;
            pd.colorAttachments[0].sourceRGBBlendFactor = MTLBlendFactorSourceAlpha;
            pd.colorAttachments[0].destinationRGBBlendFactor = MTLBlendFactorOne;
            pd.colorAttachments[0].sourceAlphaBlendFactor = MTLBlendFactorSourceAlpha;
            pd.colorAttachments[0].destinationAlphaBlendFactor = MTLBlendFactorOne;
            pd.depthAttachmentPixelFormat = ds.pixelFormat;
            if (ds.pixelFormat == MTLPixelFormatDepth32Float_Stencil8) pd.stencilAttachmentPixelFormat = ds.pixelFormat;
            if (grass_pso_async(key, pd)) { missing = 1; continue; }    // built off the frame; this draw stays PC until it lands
            NSError *err = nil;
            uint64_t t0 = diag_on ? host_ticks() : 0;
            pso = pipeline_build(pd, __func__, &err);
            if (diag_on) { ++stat_sync_grass; stat_sync_grass_ns += host_ticks() - t0; }
            if (!pso) return;
            map_put(&pipelines, key, pso);
        }
        g->pso[i] = pso;
    }
    if (missing) return;
    uint32_t state[256]; memcpy(state, dev.rs, sizeof state);
    state[23] = 4; state[14] = 1; g->depth[0] = depth_state_for(state); // LEQUAL, shell0 writes depth
    state[14] = 0; g->depth[1] = depth_state_for(state);
    mat_grass_draw.skip = GRASS_DEPTH;
    if (!g->depth[0] || !g->depth[1]) return;
    mat_grass_draw.skip = GRASS_NO_GEOMETRY;
    g->n = n;
}

static void grass_emit(GrassDraw *g, void (^geometry)(void)) {
    if (!g->n) { geometry(); return; }
    [enc setFragmentTexture:g->volume atIndex:1]; [enc setFragmentTexture:g->noise atIndex:2]; [enc setFragmentTexture:g->shadow atIndex:3];
    [enc setFragmentTexture:g->normal ?: zero_tex2d atIndex:4];
    [enc setFragmentTexture:g->specular ?: zero_tex2d atIndex:5];
    [enc setFragmentTexture:g->opacity ?: zero_tex2d atIndex:6];
    for (int i = 0; i < g->n; ++i) {
        g->k[63][0] = (float)i; g->k[63][1] = (probe_sig & PROBE_GRASS) != 0;
        [enc setVertexBytes:g->k length:sizeof g->k atIndex:MAT_GRASS_VS_BUF];
        [enc setFragmentBytes:g->k length:sizeof g->k atIndex:MAT_GRASS_PS_BUF];
        [enc setRenderPipelineState:g->pso[i]]; [enc setDepthStencilState:g->depth[i != 0]];
        geometry();
        if (i) probe_applied(PROBE_GRASS, 1);
    }
    mat_grass_diag_emit(g->n, g->k);
    memset(&eb, 0, sizeof eb);           // host texture/pipeline bindings must not leak through the D3D cache
}
// ---------------- End R27 grass ----------------

// ---------------- R20 auxiliary: one additive draw using the same geometry closure ----------------
typedef struct {
    __unsafe_unretained id<MTLRenderPipelineState> pso;
    __unsafe_unretained id<MTLDepthStencilState> depth;
    __unsafe_unretained id<MTLTexture> textures[3];
    float k[52][4];
} AuxDraw;
static Map aux_pipeline_errors;
static AuxDraw aux_prepare(Obj *vs, Obj *ps, const SmOptions *vo, const SmOptions *po, const ShaderEntry *pcvs,
                          const DrawAttr *attr, int nattr, const uint32_t *strides, uint32_t streams, int need_zero,
                          uint64_t vkey, id<MTLTexture> rt, id<MTLTexture> ds) {
    AuxDraw a = {0};
    if (!auxlights_enabled() || !mat_aux_draw.valid || mat_aux_draw.emitted || mat_aux_ab_held()) return a;
    int reason = AUX_STATE;
    if (!dev.rs[7] || !dev.rs[14] || dev.rs[27] || dev.rs[8] != 3 || !ds || is_depth_pf(rt.pixelFormat) ||
        (dev.rs[168]&7) != 7 || q_active || lamp_replay) { mat_aux_fallback(reason); return (AuxDraw){0}; }
    reason = AUX_INPUT;
    const int usage[4] = {SM_USAGE_POSITION,SM_USAGE_NORMAL,SM_USAGE_TEXCOORD,SM_USAGE_TANGENT};
    for (int j = 0; j < 4; ++j) {
        int found = 0;
        for (int k = 0; k < pcvs->refl.n_inputs; ++k) {
            const SmInput *in = &pcvs->refl.inputs[k];
            if (in->usage != usage[j] || in->usage_index) continue;
            for (int t = 0; t < nattr; ++t) if (attr[t].reg == in->reg && !attr[t].zero) {
                // PC float normals/tangents; no unsigned packed-normal guesses.
                int f = attr[t].fmt_type;
                found = j == 2 ? (f == 1 || f == 3 || f == 15 || f == 16) : j == 3 ? f == 3 : (f == 2 || f == 3);
            }
        }
        if (!found) { mat_aux_fallback(reason); return (AuxDraw){0}; }
    }
    reason = AUX_TEXTURE;
    const int slots[3] = {0,1,3}; // material TextureInfo, NOT worldreflect's reflection sampler
    for (int i = 0; i < 3; ++i) {
        uint32_t ti = mat_aux_draw.textures[slots[i]], plat = ti ? M32(ti) : 0;
        if (i == 1 && ti && M32(ti+0x24) == 0xE2C7C5E2u) { mat_aux_fallback(reason); return (AuxDraw){0}; } // WHITE32X32 is not a normal map
        Obj *o = plat ? obj(M32(plat+0x18)) : NULL;
        if (!o || o->kind != K_TEXTURE) { mat_aux_fallback(reason); return (AuxDraw){0}; }
        RenderTex *t = gpu_tex(o);
        if (!t || !t->tex || t->tex == rt || t->tex.textureType != MTLTextureType2D || is_depth_pf(t->tex.pixelFormat)) { mat_aux_fallback(reason); return (AuxDraw){0}; }
        a.textures[i] = t->tex;
    }
    reason = AUX_CONSTANTS;
    if (!mat_aux_constants(&pcvs->refl,dev.vsc,a.k)) { mat_aux_fallback(reason); return (AuxDraw){0}; }
    reason = AUX_SHADER;
    ShaderEntry *v = get_shader_variant(vs,vo,MAT_VARIANT_AUX), *p = get_shader_variant(ps,po,MAT_VARIANT_AUX);
    if (__atomic_load_n(&v->pending, __ATOMIC_ACQUIRE) || __atomic_load_n(&p->pending, __ATOMIC_ACQUIRE) || !v->ok || !p->ok) { mat_aux_fallback(reason); return (AuxDraw){0}; }
    reason = AUX_PIPELINE;
    // Every effective PSO property is in this key. Blend is always ONE/ONE ADD.
    uint32_t effective[8] = {(uint32_t)rt.pixelFormat,(uint32_t)ds.pixelFormat,(uint32_t)rt.sampleCount,dev.rs[168]&15,1,1,1,1};
    uint64_t key = fnv(effective,sizeof effective,fnv(&v->key,8,fnv(&p->key,8,vkey)));
    a.pso = (__bridge id<MTLRenderPipelineState>)map_get(&pipelines,key);
    if (!a.pso) {
        if (map_get(&aux_pipeline_errors,key)) { mat_aux_fallback(reason); return (AuxDraw){0}; }
        MTLVertexDescriptor *vd = [MTLVertexDescriptor vertexDescriptor];
        for (int k = 0; k < nattr; ++k) {
            int ai,au;
            vd.attributes[attr[k].reg].format = attr[k].zero ? MTLVertexFormatFloat4 : vfmt(attr[k].fmt_type,&ai,&au);
            vd.attributes[attr[k].reg].offset = attr[k].offset; vd.attributes[attr[k].reg].bufferIndex = attr[k].buf;
        }
        for (int s = 0; s < 16; ++s) if (streams&(1u<<s)) { vd.layouts[s].stride = strides[s]; vd.layouts[s].stepFunction = MTLVertexStepFunctionPerVertex; }
        if (need_zero) { vd.layouts[30].stride = 16; vd.layouts[30].stepFunction = MTLVertexStepFunctionConstant; vd.layouts[30].stepRate = 0; }
        MTLRenderPipelineDescriptor *pd = [MTLRenderPipelineDescriptor new];
        pd.vertexFunction = v->fn; pd.fragmentFunction = p->fn; pd.vertexDescriptor = vd; pd.rasterSampleCount = rt.sampleCount;
        MTLRenderPipelineColorAttachmentDescriptor *ca = pd.colorAttachments[0];
        ca.pixelFormat = rt.pixelFormat; ca.blendingEnabled = YES;
        ca.rgbBlendOperation = ca.alphaBlendOperation = MTLBlendOperationAdd;
        ca.sourceRGBBlendFactor = ca.destinationRGBBlendFactor = MTLBlendFactorOne;
        ca.sourceAlphaBlendFactor = ca.destinationAlphaBlendFactor = MTLBlendFactorOne;
        ca.writeMask = ((effective[3]&1) ? MTLColorWriteMaskRed : 0) | ((effective[3]&2) ? MTLColorWriteMaskGreen : 0) |
            ((effective[3]&4) ? MTLColorWriteMaskBlue : 0) | ((effective[3]&8) ? MTLColorWriteMaskAlpha : 0);
        pd.depthAttachmentPixelFormat = ds.pixelFormat;
        if (ds.pixelFormat == MTLPixelFormatDepth32Float_Stencil8) pd.stencilAttachmentPixelFormat = ds.pixelFormat;
        NSError *err = nil;
        id<MTLRenderPipelineState> pso = pipeline_cached(pd, v->key, p->key, "aux", &err);   // strong local: a.pso is unretained
        if (!pso) {
            if (diag_on) diag_event("auxlights: pipeline failed: %s",err.localizedDescription.UTF8String);
            map_put(&aux_pipeline_errors,key,@YES); { mat_aux_fallback(reason); return (AuxDraw){0}; }
        }
        map_put(&pipelines,key,pso);   // owned by the pipelines map for the process lifetime
        a.pso = pso;
    }
    uint32_t state[256]; memcpy(state,dev.rs,sizeof state);
    state[23] = 4; state[14] = 0; // LEQUAL, no depth writes; keep stencil testing but no stencil writes
    state[59] = 0;
    a.depth = depth_state_for(state);
    if (!a.depth) { mat_aux_fallback(reason); return (AuxDraw){0}; }
    return a;
}
static void aux_emit(AuxDraw *a, void (^geometry)(void)) {
    if (!a->pso || mat_aux_draw.emitted) return;
    [enc setRenderPipelineState:a->pso]; [enc setDepthStencilState:a->depth];
    if (hdrclip_on) hdrclip_bind(enc, HC_WORLD);
    if(float_scene_on){unsigned store=float_scene_store_policy(surface_target(dev.rt[0],&(NSUInteger){0},&(NSUInteger){0}).pixelFormat,dev.rt[0]);[enc setFragmentBytes:&store length:sizeof store atIndex:27];}
    [enc setVertexBytes:a->k length:sizeof a->k atIndex:MAT_AUX_VS_BUF];
    [enc setFragmentBytes:a->k length:sizeof a->k atIndex:MAT_AUX_PS_BUF];
    for (int i = 0; i < 3; ++i) [enc setFragmentTexture:a->textures[i] atIndex:i];
    geometry();
    mat_aux_emit();
    memset(&eb,0,sizeof eb); // restore all guest bindings on the next ordinary draw
}

// ---------------- R18 water: one opaque world draw, the existing depth state ----------------
typedef struct {
    int matched;
    __unsafe_unretained id<MTLRenderPipelineState> pso;
    __unsafe_unretained id<MTLTexture> normal, reflection;
    float k[32][4];
} WaterDraw;
static id<MTLTexture> water_normal_texture;
static Map water_pipeline_errors;       // immutable failures: retain PC without recompiling every water draw
static id<MTLTexture> water_normal_upload(void) {
    if (water_normal_texture) return water_normal_texture;
    uint32_t w, h;
    const uint8_t *rgba = mat_water_normal(&w, &h);
    if (!rgba || !w || !h) return nil;
    MTLTextureDescriptor *td = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm width:w height:h mipmapped:NO];
    td.usage = MTLTextureUsageShaderRead;
    water_normal_texture = [mtl newTextureWithDescriptor:td];
    [water_normal_texture replaceRegion:MTLRegionMake2D(0, 0, w, h) mipmapLevel:0 withBytes:rgba bytesPerRow:w * 4];
    return water_normal_texture;
}
static WaterDraw water_prepare(Obj *vs, Obj *ps, const SmOptions *vo, const SmOptions *po, const ShaderEntry *pcvs,
                               const DrawAttr *attr, int nattr, const uint32_t *strides, uint32_t streams, int need_zero,
                               uint64_t pc_key, id<MTLTexture> rt, id<MTLTexture> ds) {
    WaterDraw w = {.matched = mat_water_draw.mesh != 0};
    if (!w.matched || !mat_water_enabled() || dev.rs[27] || !dev.rs[7] || !dev.rs[14]) return w;
    // No reflection recursion or stale offscreen target: records are main-view
    // world/watersea only. First visible frame uses PC while demand is latched.
    if (!mat_water_reflection_requested() || !mat_water_constants(&pcvs->refl, dev.vsc, w.k)) return w;
    Obj *diffuse = obj(dev.tex[0]); RenderTex *d = diffuse ? gpu_tex(diffuse) : NULL;
    Obj *reflection = obj(M32(0x00982A6Cu)); RenderTex *r = reflection ? gpu_tex(reflection) : NULL;
    if (!d || !d->tex || !r || !r->tex || r->tex.width <= 1 || r->tex.height <= 1 ||
        r->tex == rt || r->tex.textureType != MTLTextureType2D || !M8(0x009197A8u) || !M32(0x0093DAE4u)) return w;
    w.normal = water_normal_upload(); w.reflection = r->tex;
    if (!w.normal) return w;
    ShaderEntry *v = get_shader_variant(vs, vo, MAT_VARIANT_WATER), *p = get_shader_variant(ps, po, MAT_VARIANT_WATER);
    if (__atomic_load_n(&v->pending, __ATOMIC_ACQUIRE) || __atomic_load_n(&p->pending, __ATOMIC_ACQUIRE) || !v->ok || !p->ok) return w;
    uint64_t key = fnv(&v->key, 8, fnv(&p->key, 8, pc_key));
    w.pso = (__bridge id<MTLRenderPipelineState>)map_get(&pipelines, key);
    if (w.pso) return w;
    if (map_get(&water_pipeline_errors, key)) return w;
    MTLVertexDescriptor *vd = [MTLVertexDescriptor vertexDescriptor];
    for (int k = 0; k < nattr; ++k) {
        int ai, au;
        vd.attributes[attr[k].reg].format = attr[k].zero ? MTLVertexFormatFloat4 : vfmt(attr[k].fmt_type, &ai, &au);
        vd.attributes[attr[k].reg].offset = attr[k].offset;
        vd.attributes[attr[k].reg].bufferIndex = attr[k].buf;
    }
    for (int i = 0; i < 16; ++i) if (streams & (1u << i)) { vd.layouts[i].stride = strides[i]; vd.layouts[i].stepFunction = MTLVertexStepFunctionPerVertex; }
    if (need_zero) { vd.layouts[30].stride = 16; vd.layouts[30].stepFunction = MTLVertexStepFunctionConstant; vd.layouts[30].stepRate = 0; }
    MTLRenderPipelineDescriptor *pd = [MTLRenderPipelineDescriptor new];
    pd.vertexFunction = v->fn; pd.fragmentFunction = p->fn; pd.vertexDescriptor = vd;
    pd.rasterSampleCount = rt.sampleCount; pd.colorAttachments[0].pixelFormat = rt.pixelFormat;
    uint32_t mask = dev.rs[168];
    pd.colorAttachments[0].writeMask = ((mask & 1) ? MTLColorWriteMaskRed : 0) | ((mask & 2) ? MTLColorWriteMaskGreen : 0) |
        ((mask & 4) ? MTLColorWriteMaskBlue : 0) | ((mask & 8) ? MTLColorWriteMaskAlpha : 0);
    // Opaque replacement only. Explicit valid ADD operations, even with blending
    // disabled (lampglow lesson); no D3D render state or depth state is invented.
    pd.colorAttachments[0].blendingEnabled = NO;
    pd.colorAttachments[0].rgbBlendOperation = pd.colorAttachments[0].alphaBlendOperation = MTLBlendOperationAdd;
    if (ds) {
        pd.depthAttachmentPixelFormat = ds.pixelFormat;
        if (ds.pixelFormat == MTLPixelFormatDepth32Float_Stencil8) pd.stencilAttachmentPixelFormat = ds.pixelFormat;
    }
    NSError *err = nil;
    // A strong local: w.pso is __unsafe_unretained, so assigning the new (+1) pipeline straight to it let ARC free it
    // at once and map_put retained a dead object (crash in the first X360-water race, 2026-10-24).
    id<MTLRenderPipelineState> pso = pipeline_cached(pd, v->key, p->key, "water", &err);
    if (!pso) {
        mat_water_failure(1);
        if (diag_on) diag_event("water: pipeline: %s", err.localizedDescription.UTF8String);
        map_put(&water_pipeline_errors, key, err ? (id)err : @YES);
    } else map_put(&pipelines, key, pso);
    w.pso = pso;   // now owned by the pipelines map for the process lifetime
    return w;
}
// ---------------- R19 building glass: one draw, the PC material's own blend/depth/alpha-test state ----------------
// Replacement pipeline = the PC draw's pipeline (vertex layout, blend, write mask, formats) with the glassreflect
// functions. Textures/samplers/aux stay the PC draw's own bindings (diffuse s0, shadow s1, WINDOWREFLECTION s2/s1).
// host_material_pipeline is shared with R28 trees (same contract: the PC draw's state, the replacement's functions).
// The X360 UV1.y side stream (materials.m mat_glass_uv1) is one float per vertex at MAT_GLASS_UV1_SLOT, built once per
// vertex buffer content (checked by its size and first/last 64 bytes on every draw, so a reloaded section's new
// buffer at the same address gets its own row); buffers without a row get GLASS_UV1_NONE everywhere.
@interface GlassUV1 : NSObject { @public uint64_t check, full; int row; uint32_t n, matched; id<MTLBuffer> buf; }
@end
@implementation GlassUV1
@end
static struct { __unsafe_unretained id<MTLRenderPipelineState> pso; __unsafe_unretained id<MTLBuffer> uv1; NSUInteger uv1_off;
                int blended; float k[32][4]; } glass_now;
static Map glass_pipeline_errors, glass_uv1_cache;
static GlassUV1 *glass_uv1_for(Obj *vb) {
    if (!vb || !vb->size || vb->size % 36) return nil;
    const uint8_t *b = G + vb->mem[0];
    uint32_t edge = vb->size < 64 ? vb->size : 64;
    uint64_t check = fnv(b + vb->size - edge, edge, fnv(b, edge, vb->size));
    GlassUV1 *u = (__bridge GlassUV1 *)map_get(&glass_uv1_cache, vb->guest);
    uint64_t full = diag_on ? fnv(b, vb->size, 1469598103934665603ull) : 0;
    if (u && u->check == check) { if (diag_on && u->full != full) ++ad1.glass_stale_mid; return u; }
    u = [GlassUV1 new];
    u->check = check; u->full = full; u->n = vb->size / 36;
    const float *y = NULL;
    u->row = mat_glass_uv1(b, vb->size, &y, &u->matched);
    u->buf = [mtl newBufferWithLength:u->n * sizeof(float) options:MTLResourceStorageModeShared];
    float *f = u->buf.contents;
    for (uint32_t i = 0; i < u->n; ++i) f[i] = y ? y[i] : GLASS_UV1_NONE;
    map_put(&glass_uv1_cache, vb->guest, u);
    return u;
}
static id<MTLRenderPipelineState> host_material_pipeline(const ShaderEntry *v, const ShaderEntry *p, const DrawAttr *attr, int nattr,
                          const uint32_t *strides, uint32_t streams, int need_zero, uint64_t pc_key, id<MTLTexture> rt, id<MTLTexture> ds,
                          Map *errors, void (*failure)(int), const char *tag, int alpha_blend, int uv1) {
    uint64_t key = fnv(&v->key, 8, fnv(&p->key, 8, pc_key ^ (alpha_blend ? 0xB1E7D5A1C0FFEE01ull : 0) ^ (uv1 ? 0x6C455551ull : 0)));
    id<MTLRenderPipelineState> pso = (__bridge id<MTLRenderPipelineState>)map_get(&pipelines, key);
    if (!pso && !map_get(errors, key)) {
        const uint32_t *rs = dev.rs;
        MTLVertexDescriptor *vd = [MTLVertexDescriptor vertexDescriptor];
        for (int k = 0; k < nattr; ++k) {
            int ai, au;
            vd.attributes[attr[k].reg].format = attr[k].zero ? MTLVertexFormatFloat4 : vfmt(attr[k].fmt_type, &ai, &au);
            vd.attributes[attr[k].reg].offset = attr[k].offset; vd.attributes[attr[k].reg].bufferIndex = attr[k].buf;
        }
        for (int i = 0; i < 16; ++i) if (streams & (1u << i)) { vd.layouts[i].stride = strides[i]; vd.layouts[i].stepFunction = MTLVertexStepFunctionPerVertex; }
        if (need_zero) { vd.layouts[30].stride = 16; vd.layouts[30].stepFunction = MTLVertexStepFunctionConstant; vd.layouts[30].stepRate = 0; }
        if (uv1) {
            vd.attributes[MAT_GLASS_UV1_SLOT].format = MTLVertexFormatFloat; vd.attributes[MAT_GLASS_UV1_SLOT].offset = 0;
            vd.attributes[MAT_GLASS_UV1_SLOT].bufferIndex = MAT_GLASS_UV1_SLOT;
            vd.layouts[MAT_GLASS_UV1_SLOT].stride = 4; vd.layouts[MAT_GLASS_UV1_SLOT].stepFunction = MTLVertexStepFunctionPerVertex;
        }
        MTLRenderPipelineDescriptor *pd = [MTLRenderPipelineDescriptor new];
        pd.vertexFunction = v->fn; pd.fragmentFunction = p->fn; pd.vertexDescriptor = vd;
        pd.rasterSampleCount = rt.sampleCount; pd.colorAttachments[0].pixelFormat = rt.pixelFormat;
        pd.colorAttachments[0].writeMask = ((rs[168] & 1) ? MTLColorWriteMaskRed : 0) | ((rs[168] & 2) ? MTLColorWriteMaskGreen : 0) |
            ((rs[168] & 4) ? MTLColorWriteMaskBlue : 0) | ((rs[168] & 8) ? MTLColorWriteMaskAlpha : 0);
        pd.alphaToCoverageEnabled = !ssao_replay && alpha_to_coverage_draw(rs, (unsigned)rt.sampleCount, alpha_blend || rs[27]);
        // The material's blend (824513F0 modes on X360, BlendState on PC): never forced by the effect name.
        // alpha_blend (R28 trees' second pass): SRCALPHA/INVSRCALPHA, ADD, whatever the PC draw set.
        pd.colorAttachments[0].blendingEnabled = alpha_blend || rs[27] != 0;
        pd.colorAttachments[0].sourceRGBBlendFactor = blend_factor(alpha_blend ? 5 : rs[19]);
        pd.colorAttachments[0].destinationRGBBlendFactor = blend_factor(alpha_blend ? 6 : rs[20]);
        pd.colorAttachments[0].rgbBlendOperation = blend_op(alpha_blend ? 1 : rs[171]);
        int sep = !alpha_blend && rs[206] != 0;
        pd.colorAttachments[0].sourceAlphaBlendFactor = blend_factor(sep ? rs[207] : alpha_blend ? 5 : rs[19]);
        pd.colorAttachments[0].destinationAlphaBlendFactor = blend_factor(sep ? rs[208] : alpha_blend ? 6 : rs[20]);
        pd.colorAttachments[0].alphaBlendOperation = blend_op(sep ? rs[209] : alpha_blend ? 1 : rs[171]);
        if (ssao_replay) {
            pd.colorAttachments[0].blendingEnabled=YES;
            pd.colorAttachments[0].sourceRGBBlendFactor=pd.colorAttachments[0].destinationRGBBlendFactor=MTLBlendFactorZero;
            pd.colorAttachments[0].sourceAlphaBlendFactor=pd.colorAttachments[0].destinationAlphaBlendFactor=MTLBlendFactorZero;
        }
        if (ds) { pd.depthAttachmentPixelFormat = ds.pixelFormat; pd.stencilAttachmentPixelFormat = ds.pixelFormat==MTLPixelFormatDepth32Float_Stencil8?ds.pixelFormat:MTLPixelFormatInvalid; }
        NSError *err = nil;
        pso = pipeline_cached(pd, v->key, p->key, ssao_replay ? "ao_material" : "material", &err);
        if (!pso) {
            failure(1);
            if (diag_on) diag_event("%s: pipeline: %s", tag, err.localizedDescription.UTF8String);
            map_put(errors, key, err ? (id)err : @YES);
        } else map_put(&pipelines, key, pso);
    }
    return pso;
}
static void glass_prepare(Obj *vs, Obj *ps, const SmOptions *vo, const SmOptions *po, const ShaderEntry *pcvs, const ShaderEntry *pcps,
                          const DrawAttr *attr, int nattr, const uint32_t *strides, uint32_t streams, int need_zero,
                          uint64_t pc_key, id<MTLTexture> rt, id<MTLTexture> ds) {
    glass_now.pso = nil;
    if (!mat_glass_draw.mesh || mat_glass_draw.skip == GLASS_VIEW) return;
    mat_glass_draw.skip = GLASS_PC;
    if (!mat_glass_enabled()) return;
    mat_glass_draw.skip = GLASS_STATE;
    if (q_active || is_depth_pf(rt.pixelFormat)) return;
    mat_glass_draw.skip = GLASS_CONSTANTS;
    if (!mat_glass_constants(&pcvs->refl, &pcps->refl, (const float (*)[4])dev.vsc, (const float (*)[4])dev.psc, glass_now.k)) return;
    mat_glass_draw.skip = GLASS_SHADER;
    ShaderEntry *v = get_shader_variant(vs, vo, MAT_VARIANT_GLASS), *p = get_shader_variant(ps, po, MAT_VARIANT_GLASS);
    if (__atomic_load_n(&v->pending, __ATOMIC_ACQUIRE) || __atomic_load_n(&p->pending, __ATOMIC_ACQUIRE) || !v->ok || !p->ok) return;
    mat_glass_draw.skip = GLASS_PIPELINE;
    // UV1.y side stream: the vertex buffer feeding the 36-byte glass layout (stream 0); without one the draw stays PC.
    GlassUV1 *u = (streams & 1) && strides[0] == 36 ? glass_uv1_for(obj(dev.stream[0])) : nil;
    if (!u) return;
    id<MTLRenderPipelineState> pso = host_material_pipeline(v, p, attr, nattr, strides, streams, need_zero, pc_key, rt, ds,
                                                            &glass_pipeline_errors, mat_glass_failure, "glass", 0, 1);
    if (!pso) return;
    glass_now.pso = pso; glass_now.blended = dev.rs[27] != 0;
    if (diag_on) ++ad1.glass_draws;
    glass_now.uv1 = u->buf; glass_now.uv1_off = dev.stream_off[0] / 36 * sizeof(float);
    mat_glass_uv_note(u->row >= 0, u->n, u->matched);
}
static int glass_emit(void (^geometry)(void)) {
    if (!glass_now.pso) return 0;
    [enc setRenderPipelineState:glass_now.pso];
    [enc setVertexBytes:glass_now.k length:sizeof glass_now.k atIndex:MAT_GLASS_VS_BUF];
    [enc setFragmentBytes:glass_now.k length:sizeof glass_now.k atIndex:MAT_GLASS_PS_BUF];
    [enc setVertexBuffer:glass_now.uv1 offset:glass_now.uv1_off atIndex:MAT_GLASS_UV1_SLOT];
    geometry();
    memset(&eb, 0, sizeof eb);           // host pipeline must not leak through the D3D binding cache
    mat_glass_emit(glass_now.blended);
    glass_now.pso = nil; glass_now.uv1 = nil;
    return 1;
}
// ---------------- End R19 glass ----------------
// ---------------- R28 tree shading: the PC tree draw's own state with the billboardshader functions ----------------
static struct { __unsafe_unretained id<MTLRenderPipelineState> pso, pso1; float k[32][4]; } trees_now;
static Map trees_pipeline_errors;
static void trees_prepare(Obj *vs, Obj *ps, const SmOptions *vo, const SmOptions *po, const ShaderEntry *pcvs,
                          const DrawAttr *attr, int nattr, const uint32_t *strides, uint32_t streams, int need_zero,
                          uint64_t pc_key, id<MTLTexture> rt, id<MTLTexture> ds) {
    trees_now.pso = trees_now.pso1 = nil;
    if (!mat_trees_draw.mesh || mat_trees_draw.skip != TREES_NO_DRAW) return;   // unmatched / other view: PC
    mat_trees_draw.skip = TREES_PC;
    if (!mat_trees_enabled()) return;
    mat_trees_draw.skip = TREES_STATE;
    if (q_active || is_depth_pf(rt.pixelFormat)) return;
    mat_trees_draw.skip = TREES_CONSTANTS;
    if (!mat_trees_constants(&pcvs->refl, (const float (*)[4])dev.vsc, trees_now.k)) return;
    mat_trees_draw.skip = TREES_SHADER;
    ShaderEntry *v = get_shader_variant(vs, vo, MAT_VARIANT_TREES), *p = get_shader_variant(ps, po, MAT_VARIANT_TREES);
    if (__atomic_load_n(&v->pending, __ATOMIC_ACQUIRE) || __atomic_load_n(&p->pending, __ATOMIC_ACQUIRE) || !v->ok || !p->ok) return;
    mat_trees_draw.skip = TREES_PIPELINE;
    trees_now.pso = host_material_pipeline(v, p, attr, nattr, strides, streams, need_zero, pc_key, rt, ds,
                                           &trees_pipeline_errors, mat_trees_failure, "trees", 0, 0);
    // X360 8244F680 pass 1 (the PC draw has no such pass); without it the draw stays one pass as before.
    if (trees_now.pso && mat_trees_draw.alpha_ref)
        trees_now.pso1 = host_material_pipeline(v, p, attr, nattr, strides, streams, need_zero, pc_key, rt, ds,
                                                &trees_pipeline_errors, mat_trees_failure, "trees", 1, 0);
}
static int trees_emit(void (^geometry)(void)) {
    if (!trees_now.pso) return 0;
    [enc setRenderPipelineState:trees_now.pso];
    [enc setVertexBytes:trees_now.k length:sizeof trees_now.k atIndex:MAT_TREES_VS_BUF];
    [enc setFragmentBytes:trees_now.k length:sizeof trees_now.k atIndex:MAT_TREES_PS_BUF];
    int blended = trees_now.pso1 != nil;
    if (blended) {                       // 8244F680 pass 0: alpha test >= ref (D3DCMP_GREATEREQUAL), the PC draw's depth write
        SmPSAux pa = eb.pa;              // the PC draw's PS aux block, bound just before (> 95 on PC)
        pa.alpha_func = 7; pa.alpha_ref = mat_trees_draw.alpha_ref / 255.0f;
        [enc setFragmentBytes:&pa length:sizeof pa atIndex:3];
        eb.pa = pa;
    }
    if (!ssao_replay || !blended || dev.rs[27]) geometry();
    if (blended) {                       // pass 1: SRCALPHA/INVSRCALPHA, alpha test < ref (D3DCMP_LESS), no depth write, same cull/depth test
        uint32_t lr[256]; memcpy(lr, dev.rs, sizeof lr); lr[14] = 0;
        if (ssao_replay) { lr[52]=lr[59]=0;if(lr[23]==2)lr[23]=4; }
        SmPSAux pa = eb.pa;
        pa.alpha_func = ssao_replay&&!ssao_reference_record?0:2;
        pa.alpha_to_coverage = 0; // blended fringe retains its original alpha and discard
        float k1[32][4]; memcpy(k1, trees_now.k, sizeof k1); k1[20][1] = 1;
        [enc setRenderPipelineState:trees_now.pso1];
        [enc setDepthStencilState:depth_state_for(lr)];
        [enc setFragmentBytes:&pa length:sizeof pa atIndex:3];
        [enc setFragmentBytes:k1 length:sizeof k1 atIndex:MAT_TREES_PS_BUF];
        geometry();
    }
    memset(&eb, 0, sizeof eb);           // host pipeline must not leak through the D3D binding cache
    if (!ssao_replay) { ssao_tree_fringe=blended;mat_trees_emit(blended); }
    trees_now.pso = trees_now.pso1 = nil;
    return 1;
}
// ---------------- End R28 trees ----------------
// ---------------- X360 worldbone on the X360 arrest characters (materials.m) ----------------
static struct { __unsafe_unretained id<MTLRenderPipelineState> pso; __unsafe_unretained id<MTLTexture> tex[3]; float k[MAT_BONE_K][4]; } bone_now;
static Map bone_pipeline_errors;
static id<MTLTexture> bone_tex(uint32_t guest, int cube) {
    Obj *t = obj(guest);
    RenderTex *g = t ? gpu_tex(t) : NULL;
    MTLTextureType want = cube ? MTLTextureTypeCube : MTLTextureType2D;
    return g && g->tex && g->tex.textureType == want ? g->tex : cube ? zero_texcube : zero_tex2d;
}
static void bone_prepare(Obj *vs, Obj *ps, const SmOptions *vo, const SmOptions *po,
                         const DrawAttr *attr, int nattr, const uint32_t *strides, uint32_t streams, int need_zero,
                         uint64_t pc_key, id<MTLTexture> rt, id<MTLTexture> ds) {
    bone_now.pso = nil;
    Obj *c1 = obj(dev.tex[1]);
    if (c1 && c1->kind == K_CUBE) mat_bone_car_cube(dev.tex[1]);   // the car env map, for the X360 worldbone's ENVIROMAP
    uint32_t tex[3];
    int skip = BONE_NO_DRAW;            // stays so for draws that are not normal-mapped worldbone draws (not counted)
    if (!mat_bone_draw(tex, &skip)) { if (skip != BONE_NO_DRAW) mat_bone_skip(skip); return; }
    skip = BONE_STATE;
    if (q_active || is_depth_pf(rt.pixelFormat)) { mat_bone_skip(skip); return; }
    mat_bone_constants((const float (*)[4])dev.vsc, bone_now.k);
    skip = BONE_SHADER;
    ShaderEntry *v = get_shader_variant(vs, vo, MAT_VARIANT_BONE), *p = get_shader_variant(ps, po, MAT_VARIANT_BONE);
    if (__atomic_load_n(&v->pending, __ATOMIC_ACQUIRE) || __atomic_load_n(&p->pending, __ATOMIC_ACQUIRE) || !v->ok || !p->ok) { mat_bone_skip(skip); return; }
    bone_now.pso = host_material_pipeline(v, p, attr, nattr, strides, streams, need_zero, pc_key, rt, ds,
                                          &bone_pipeline_errors, mat_bone_failure, "arrestnm", 0, 0);
    if (!bone_now.pso) { mat_bone_skip(BONE_PIPELINE); return; }
    bone_now.tex[0] = bone_tex(tex[0], 0); bone_now.tex[1] = bone_tex(tex[1], 0); bone_now.tex[2] = bone_tex(tex[2], 1);
}
static int bone_emit(void (^geometry)(void)) {
    if (!bone_now.pso) return 0;
    [enc setRenderPipelineState:bone_now.pso];
    [enc setVertexBytes:bone_now.k length:sizeof bone_now.k atIndex:MAT_BONE_VS_BUF];
    [enc setFragmentBytes:bone_now.k length:sizeof bone_now.k atIndex:MAT_BONE_PS_BUF];
    for (int i = 0; i < 3; ++i) [enc setFragmentTexture:bone_now.tex[i] atIndex:MAT_BONE_TEX + i];
    geometry();
    memset(&eb, 0, sizeof eb);           // host pipeline must not leak through the D3D binding cache
    mat_bone_emit();
    bone_now.pso = nil;
    return 1;
}
// ---------------- End X360 arrest worldbone ----------------
static void material_emit(GrassDraw *g, WaterDraw *w, AuxDraw *a, void (^geometry)(void)) {
    if (ssao_replay) { if (!trees_emit(geometry)) geometry();return; }
    if (glass_emit(geometry)) { aux_emit(a, geometry); return; }   // the glass draw is the base draw
    if (trees_emit(geometry)) { aux_emit(a, geometry); return; }   // R28: the tree draw is the base draw
    if (bone_emit(geometry)) { aux_emit(a, geometry); return; }    // X360 arrest worldbone: the character draw is the base draw
    if (w->pso) {
        [enc setRenderPipelineState:w->pso];
        [enc setVertexBytes:w->k length:sizeof w->k atIndex:MAT_WATER_VS_BUF];
        [enc setFragmentBytes:w->k length:sizeof w->k atIndex:MAT_WATER_PS_BUF];
        [enc setFragmentTexture:w->normal atIndex:15]; [enc setFragmentTexture:w->reflection atIndex:14];
        geometry();
        memset(&eb, 0, sizeof eb);
    } else grass_emit(g, geometry);
    if (w->matched) mat_water_emit(w->pso != nil);
    aux_emit(a,geometry); // exactly once, after the ordinary base draw; static and ring paths share it
}

static void draw_impl(uint32_t type, uint32_t prims, int indexed, int32_t base_vertex, uint32_t min_index, uint32_t num_vertices,
                      uint32_t start, uint32_t up_vertices, uint32_t up_stride, uint32_t up_indices, uint32_t up_index_fmt) {
    if (!mtl || !prims) return;
    if (dev.rs[7] && dev.rs[14]) {
        if(soft_depth_valid && soft_depth_frame==frame_serial && soft_depth==ad_rt.tex && surface_rt(dev.ds)==&ad_rt)end_pass();
        soft_depth_valid = 0;
    }
    ++stat_draws;
    Obj *vs = obj(dev.vs), *ps = obj(dev.ps);
    static float ff_vsc[256][4], ff_psc[224][4];
    int ff = 0;
    if (!vs && !ps && ff_shaders(&vs, &ps, ff_vsc, ff_psc)) { ff = 1; ++stat_ff_drawn; }
    if (!vs || !ps) {                                     // fixed-function state outside the supported subset
        ++stat_ff_skipped;
        FILE *df = dump_file();
        if (df) {
            extern char diag_fx_pass[];
            Obj *t0 = obj(dev.tex[0]);
            fprintf(df, "draw %llu: FIXED FUNCTION, SKIPPED: prim %u x%u %s count %u%s | vs %08X ps %08X fvf %X decl %08X | rt %08X | pass %s\n"
                        "  stage0 colorop %u args %u,%u alphaop %u args %u,%u texcoordindex %X ttf %u | s0 tex %08X fmt %X %ux%u | lighting %u zenable %u blend %u (%u,%u) alphatest %u\n",
                    (unsigned long long)stat_draws, type, prims, indexed ? "indexed" : "plain", indexed ? num_vertices : prim_vertices(type, prims), up_vertices ? " UP" : "",
                    dev.vs, dev.ps, dev.fvf, dev.decl, dev.rt[0], diag_fx_pass[0] ? diag_fx_pass : "(no effect pass)",
                    dev.tss[0][1], dev.tss[0][2], dev.tss[0][3], dev.tss[0][4], dev.tss[0][5], dev.tss[0][6], dev.tss[0][11], dev.tss[0][24],
                    dev.tex[0], t0 ? t0->fmt : 0, t0 ? t0->w : 0, t0 ? t0->h : 0, dev.rs[137], dev.rs[7], dev.rs[27], dev.rs[19], dev.rs[20], dev.rs[15]);
        }
        return;
    }
    if (!dev.rt[0]) return;
    if (diag_on) ad1.mrt_draws += (dev.rt[1] | dev.rt[2] | dev.rt[3]) != 0;
    // shaders (options from the current texture/sampler state)
    SmOptions vo, po;
    sm_default_options(&vo);
    sm_default_options(&po);
    Elem el[32];
    int nel = decl_elems(el);
    for (int i = 0; i < nel; ++i) {
        if (el[i].type == 6 || el[i].type == 7 || el[i].type == 5) { /* filled below once we know the attribute register */ }
    }
    ShaderEntry *vse = get_shader(vs, &vo);
    if (shader_pending(vse)) { ++stat_deferred; return; }
    if (!vse->ok) { ++stat_fail; return; }
    // integer attribute formats need the VS compiled with them declared as ints
    uint16_t want_int = 0, want_uint = 0;
    for (int k = 0; k < vse->refl.n_inputs; ++k) {
        SmInput *in = &vse->refl.inputs[k];
        for (int i = 0; i < nel; ++i)
            if (el[i].usage == in->usage && el[i].index == in->usage_index) {
                if (el[i].type == 6 || el[i].type == 7) want_int |= (uint16_t)(1u << in->reg);
                if (el[i].type == 5) want_uint |= (uint16_t)(1u << in->reg);
            }
    }
    if (want_int || want_uint) {
        vo.vs_input_int = want_int; vo.vs_input_uint = want_uint;
        vse = get_shader(vs, &vo);
        if (shader_pending(vse)) { ++stat_deferred; return; }
        if (!vse->ok) { ++stat_fail; return; }
    }
    for (int st = 0; st < 8; ++st) {
        Obj *t = obj(dev.tex[st]);
        if (t) po.sampler_type[st] = t->kind == K_CUBE ? SM_TEX_CUBE : t->kind == K_VOLUME ? SM_TEX_3D : SM_TEX_2D;
        if (t && t->kind == K_TEXTURE && is_depth_fmt(t->fmt)) po.shadow_samplers |= (uint16_t)(1u << st);   // hardware shadow map
        uint32_t ttff = dev.tss[st][24];
        if (ttff & 256) po.tex_projected[st] = (uint8_t)((ttff & 255) == 3 ? 3 : 4);
    }
    ShaderEntry *pse = get_shader(ps, &po);
    if (roadfilm_draw(ps->tokens_hash, dev.psc[1][3])) {
        ShaderEntry *film = get_shader_variant(ps, &po, MAT_VARIANT_ROAD_FILM);
        if (!__atomic_load_n(&film->pending, __ATOMIC_ACQUIRE) && film->ok) pse = film;
    }
    if (mat_carbon_lamp_draw()) {
        int clear = mat_headlamp_clear_draw();
        ShaderEntry *lamp = get_shader_variant(ps, &po, clear ? MAT_VARIANT_HEADLAMP_CLEAR : MAT_VARIANT_CARBON_LAMP);
        ShaderEntry *cover_vs = clear ? get_shader_variant(vs, &vo, MAT_VARIANT_HEADLAMP_CLEAR) : NULL;
        if (!__atomic_load_n(&lamp->pending, __ATOMIC_ACQUIRE) && lamp->ok &&
            (!clear || (!__atomic_load_n(&cover_vs->pending, __ATOMIC_ACQUIRE) && cover_vs->ok))) {
            pse = lamp;
            if (clear) vse = cover_vs;
        }
    }
    if (shader_pending(pse)) { ++stat_deferred; return; }
    if (!pse->ok) { ++stat_fail; return; }

    // vertex input: which element feeds each shader input (the MTLVertexDescriptor is only built for a new pipeline)
    DrawAttr attr[16];
    int nattr = 0;
    uint32_t used_streams = 0;
    int need_zero = 0;
    // vkey/pkey: FNV over the bytes below, in this order (FNV is sequential, so hashing the concatenation equals the
    // chained per-field hashing it replaced); the last draw's bytes and keys are kept, as consecutive draws mostly
    // repeat them. Game thread only.
    uint8_t vb[16 * (1 + sizeof(Elem)) + 16 * 4];
    size_t vn = 0;
    uint64_t vkey = 0;
    for (int k = 0; k < vse->refl.n_inputs && nattr < 16; ++k) {
        SmInput *in = &vse->refl.inputs[k];
        int found = -1;
        for (int i = 0; i < nel; ++i) if (el[i].usage == in->usage && el[i].index == in->usage_index) { found = i; break; }
        if (found < 0 && in->usage == SM_USAGE_POSITION)        // POSITIONT data feeding a vs position input
            for (int i = 0; i < nel; ++i) if (el[i].usage == SM_USAGE_POSITIONT) { found = i; break; }
        if (found < 0) {
            attr[nattr++] = (typeof(attr[0])){(uint8_t)in->reg, 0, 30, 1, 0};
            need_zero = 1;
            vb[vn++] = (uint8_t)in->reg;
            continue;
        }
        uint8_t b = up_vertices ? 0 : el[found].stream;
        attr[nattr++] = (typeof(attr[0])){(uint8_t)in->reg, el[found].type, b, 0, el[found].offset};
        used_streams |= 1u << b;
        vb[vn++] = (uint8_t)in->reg;
        memcpy(vb + vn, &el[found], sizeof(Elem)); vn += sizeof(Elem);
    }
    uint32_t strides[16] = {0};
    for (int s = 0; s < 16; ++s) {
        if (!(used_streams & (1u << s))) continue;
        uint32_t stride = up_vertices ? up_stride : dev.stream_stride[s];
        if (!stride) stride = 4;
        strides[s] = stride;
        memcpy(vb + vn, &stride, 4); vn += 4;
    }
    static uint8_t last_vb[sizeof vb];
    static size_t last_vn = SIZE_MAX;
    static uint64_t last_vkey;
    if (vn == last_vn && !memcmp(vb, last_vb, vn)) vkey = last_vkey;
    else { vkey = fnv(vb, vn, 0); memcpy(last_vb, vb, vn); last_vn = vn; last_vkey = vkey; }

    // pipeline
    NSUInteger lv, sl;
    id<MTLTexture> rt0 = surface_target(dev.rt[0], &lv, &sl);
    if(lamp_replay)rt0=lamp_rt.ms?lamp_rt.ms:lamp_rt.tex;
    id<MTLTexture> dst = pass_depth(rt0, surface_target(dev.ds, &lv, &sl));
    if (!rt0) return;
    if (diag_on && !mf_active && rt0.width == mf_rt_w && rt0.height == mf_rt_h) { char w[160]; snprintf(w, sizeof w, "d prim%u x%u z%u zw%u blend%u(%u,%u) ps %016llX", type, prims, rs_z(7), rs_z(14), rs_z(27), rs_z(19), rs_z(20), (unsigned long long)pse->key); mf_pass_log(w, (uint32_t)rt0.width, (uint32_t)rt0.height); }
    uint32_t *rs = dev.rs;
    uint32_t coverage_rs[256];
    if (ssao_replay) {
        memcpy(coverage_rs,rs,sizeof coverage_rs);rs=coverage_rs;
        rs[27]=1;rs[19]=rs[20]=1;rs[171]=1;rs[206]=0;rs[168]=15;
        RenderTex *target=ssao_target();rt0=target->ms?target->ms:target->tex;dst=ssao_coverage_z;
    }
    int smoke_draw = 0;
    ShaderEntry *smoke_original = pse;
    if(diag_on && gpt_on==2)soft_smoke_cost_note(pse->mat);
    if (diag_on && !ssao_replay && soft_smoke_on() && mat_is_particle(pse->mat)) {
        static unsigned reports;
        if (reports++ < 12) diag_event("soft smoke: candidate view %d z %u write %u blend %u(%u,%u) mainrt %d maindepth %d vp %08X %08X",
            mat_soft_particle_view(), rs[7], rs[14], rs[27], rs[19], rs[20], rt0 == (bb_rt.ms ? bb_rt.ms : bb_rt.tex),
            dst == (ad_rt.ms ? ad_rt.ms : ad_rt.tex), dev.viewport[4], dev.viewport[5]);
    }
    if (!ssao_replay && !lamp_replay && !q_active && soft_smoke_on() && !soft_smoke_late_on() && mat_is_particle(pse->mat) && mat_soft_particle_view() &&
        rs[7] == 1 && !rs[14] && rs[27] && rs[19] == 5 && rs[20] == 6 &&
        dev.viewport[4] == 0 && dev.viewport[5] == 0x3F800000u &&
        rt0 == (bb_rt.ms ? bb_rt.ms : bb_rt.tex) && dst == (ad_rt.ms ? ad_rt.ms : ad_rt.tex) && dst) {
        ShaderEntry *sp = get_shader_variant(ps, &po, dst==ad_rt.ms?MAT_VARIANT_SOFT_SMOKE_RESOLVED:MAT_VARIANT_SOFT_SMOKE);
        if (!__atomic_load_n(&sp->pending, __ATOMIC_ACQUIRE) && sp->ok && soft_smoke_snapshot(dst)) { pse = sp; smoke_draw = 1; }
    }
    int blend = rs[27] != 0;
    int late_draw=0;uint32_t late_rs[256];
    if(!ssao_replay && !lamp_replay && !q_active && soft_smoke_on() && soft_smoke_late_on() && mat_is_particle(pse->mat) &&
        rs[7]==1 && !rs[14] && !rs[52] && rs[27] && (rs[19]==2 || rs[19]==5) && (rs[20]==2 || rs[20]==6) &&
        (rs[23]==2 || rs[23]==4) && !rs[195] && !rs[175] && dev.viewport[4]==0 && dev.viewport[5]==0x3F800000u &&
        rt0==(bb_rt.ms?bb_rt.ms:bb_rt.tex) && dst==(ad_rt.ms?ad_rt.ms:ad_rt.tex) && dst){
        ShaderEntry *lp=get_shader_variant(ps,&po,MAT_VARIANT_SOFT_SMOKE_LATE);
        if(!__atomic_load_n(&lp->pending, __ATOMIC_ACQUIRE) && lp->ok && soft_late_prepare(rt0.width,rt0.height)){
            late_draw=smoke_draw=1;pse=lp;rt0=soft_late_layer;dst=nil;memcpy(late_rs,rs,sizeof late_rs);rs=late_rs;
            rs[7]=rs[14]=rs[52]=0;rs[168]=15;rs[206]=1;rs[207]=rs[20]==6?2:1;rs[208]=rs[20]==6?6:2;rs[209]=1;
        }
    }
    if (diag_on) { extern void mat_cardraw_note(const uint32_t *rs); mat_cardraw_note(rs); }
    int a2c = alpha_to_coverage_draw(rs, (unsigned)rt0.sampleCount, blend);
    struct { uint64_t ps, vs; uint32_t fmts[4], bstate[8]; } pk = {pse->key, vse->key,
        {(uint32_t)rt0.pixelFormat, dst ? (uint32_t)dst.pixelFormat : 0, rs[168] & 15, (uint32_t)rt0.sampleCount},
        {(uint32_t)(blend | (a2c << 1)), rs[19], rs[20], rs[171], rs[206], rs[207], rs[208], rs[209]}};
    _Static_assert(sizeof pk == 64, "pipeline key bytes must have no padding");
    static typeof(pk) last_pk;
    static uint64_t last_pk_vkey, last_pkey;
    static int have_pk;
    uint64_t pkey;
    if (have_pk && last_pk_vkey == vkey && !memcmp(&pk, &last_pk, sizeof pk)) pkey = last_pkey;
    else { pkey = fnv(&pk, sizeof pk, vkey); last_pk = pk; last_pk_vkey = vkey; last_pkey = pkey; have_pk = 1; }
    id<MTLRenderPipelineState> pso = (__bridge id<MTLRenderPipelineState>)map_get(&pipelines, pkey);
    if (!pso) {
        MTLVertexDescriptor *vd = [MTLVertexDescriptor vertexDescriptor];
        for (int k = 0; k < nattr; ++k) {
            MTLVertexAttributeDescriptor *a = vd.attributes[attr[k].reg];
            int ai, au;
            a.format = attr[k].zero ? MTLVertexFormatFloat4 : vfmt(attr[k].fmt_type, &ai, &au);
            a.offset = attr[k].offset;
            a.bufferIndex = attr[k].buf;
        }
        for (int s = 0; s < 16; ++s)
            if (used_streams & (1u << s)) { vd.layouts[s].stride = strides[s]; vd.layouts[s].stepFunction = MTLVertexStepFunctionPerVertex; }
        if (need_zero) { vd.layouts[30].stride = 16; vd.layouts[30].stepFunction = MTLVertexStepFunctionConstant; vd.layouts[30].stepRate = 0; }
        MTLRenderPipelineDescriptor *pd = [MTLRenderPipelineDescriptor new];
        pd.vertexFunction = vse->fn;
        pd.fragmentFunction = pse->fn;
        pd.vertexDescriptor = vd;
        pd.rasterSampleCount = rt0.sampleCount;
        pd.alphaToCoverageEnabled = a2c;
        pd.colorAttachments[0].pixelFormat = rt0.pixelFormat;
        MTLColorWriteMask wm = 0;
        if (rs[168] & 1) wm |= MTLColorWriteMaskRed;
        if (rs[168] & 2) wm |= MTLColorWriteMaskGreen;
        if (rs[168] & 4) wm |= MTLColorWriteMaskBlue;
        if (rs[168] & 8) wm |= MTLColorWriteMaskAlpha;
        pd.colorAttachments[0].writeMask = wm;
        if (blend) {
            pd.colorAttachments[0].blendingEnabled = YES;
            pd.colorAttachments[0].sourceRGBBlendFactor = blend_factor(rs[19]);
            pd.colorAttachments[0].destinationRGBBlendFactor = blend_factor(rs[20]);
            pd.colorAttachments[0].rgbBlendOperation = blend_op(rs[171]);
            int sep = rs[206] != 0;
            pd.colorAttachments[0].sourceAlphaBlendFactor = blend_factor(sep ? rs[207] : rs[19]);
            pd.colorAttachments[0].destinationAlphaBlendFactor = blend_factor(sep ? rs[208] : rs[20]);
            pd.colorAttachments[0].alphaBlendOperation = blend_op(sep ? rs[209] : rs[171]);
        }
        if (dst) { pd.depthAttachmentPixelFormat = dst.pixelFormat; pd.stencilAttachmentPixelFormat = dst.pixelFormat==MTLPixelFormatDepth32Float_Stencil8?dst.pixelFormat:MTLPixelFormatInvalid; }
        NSError *err = nil;
        uint64_t t0 = host_ticks();
        pso = pipeline_cached(pd, vse->key, pse->key, ssao_replay ? "ao" : "draw", &err);
        uint64_t dt = host_ticks() - t0;

        if (diag_on && dt > 20000000ull) {
            extern char diag_fx_pass[];
            diag_event("render: pipeline %016llX took %.0f ms (%s)", (unsigned long long)pkey, dt / 1e6, diag_fx_pass[0] ? diag_fx_pass : "no effect pass");
        }
        if (!pso) { fprintf(stderr, "boot: render: pipeline failed: %s\n", err.localizedDescription.UTF8String); ++stat_fail; return; }
        map_put(&pipelines, pkey, pso);
        if (diag_on && rs[15]) {
            extern char diag_fx_pass[];
            diag_event("a2c: pipeline %016llX samples %u enabled %d func %u ref %u blend %d pass %s",
                (unsigned long long)pkey, (unsigned)rt0.sampleCount, a2c, rs[25], rs[24] & 255, blend, diag_fx_pass);
        }
    }

    GrassDraw grass;
    if (!ssao_replay) grass_prepare(&grass, vs, ps, &vo, &po, vse, pse, attr, nattr, strides, used_streams, need_zero, vkey, rt0, dst);
    WaterDraw water = ssao_replay ? (WaterDraw){0} : water_prepare(vs, ps, &vo, &po, vse, attr, nattr, strides, used_streams, need_zero, pkey, rt0, dst);
    AuxDraw aux = ssao_replay ? (AuxDraw){0} : aux_prepare(vs, ps, &vo, &po, vse, attr, nattr, strides, used_streams, need_zero, vkey, rt0, dst);
    if (!ssao_replay) glass_prepare(vs, ps, &vo, &po, vse, pse, attr, nattr, strides, used_streams, need_zero, pkey, rt0, dst);
    trees_prepare(vs, ps, &vo, &po, vse, attr, nattr, strides, used_streams, need_zero, pkey, rt0, dst);
    if (!ssao_replay) bone_prepare(vs, ps, &vo, &po, attr, nattr, strides, used_streams, need_zero, pkey, rt0, dst);
    id<MTLRenderPipelineState> proof_original = nil;
    id<MTLRenderPipelineState> proof_reference = nil;
    if (diag_on && smoke_draw && postfx_look_variant("softon")) {
        static unsigned reports;
        if (reports++ < 4) diag_event("soft smoke proof: flags %d stencil %u ops %u/%u/%u mask %u", getenv("BOOT_DIAG_SOFT_SMOKE") != NULL, rs[52],rs[53],rs[54],rs[55],rs[168]);
    }
    if (diag_on && smoke_draw && !late_draw && getenv("BOOT_DIAG_SOFT_SMOKE") && postfx_look_variant("softon") &&
        (!rs[52] || !rs[59] || (rs[53]==1 && rs[54]==1 && rs[55]==1 && (!rs[185] || (rs[186]==1 && rs[187]==1 && rs[188]==1))))) {
        ShaderEntry *copy=get_shader_variant(ps,&po,MAT_VARIANT_SOFT_SMOKE);
        if(!__atomic_load_n(&copy->pending, __ATOMIC_ACQUIRE)&&copy->ok){
            id<MTLTexture> saved=soft_depth;
            if(!soft_proof_active){
                if(dst==ad_rt.ms)soft_smoke_snapshot_copy(dst);
                soft_proof_reference_depth=soft_depth;soft_depth=saved;
            }
            proof_reference=host_material_pipeline(vse,copy,attr,nattr,strides,used_streams,need_zero,pkey,rt0,dst,
                &soft_proof_errors,soft_proof_failure,"soft smoke proof reference",0,0);
        }
    }
    if (proof_reference && soft_proof_begin(rt0, dst))
        proof_original = host_material_pipeline(vse, smoke_original, attr, nattr, strides, used_streams, need_zero, pkey, rt0, dst,
            &soft_proof_errors, soft_proof_failure, "soft smoke proof original", 0, 0);
    if (gpt_on == 2 && gpt_split_due()) pass_dirty = 1;     // BOOT_DIAG_GPUPASS=2: a sampled frame splits passes every N draws
    soft_smoke_encoding = smoke_draw && !late_draw;
    begin_pass();
    soft_smoke_encoding = 0;
    id<MTLRenderCommandEncoder> proof_real = enc;
    SoftSmokeTee *proof_tee = nil;
    id late_record=nil;
    if(late_draw){
        late_record=rt_render_list_new();enc=(id<MTLRenderCommandEncoder>)late_record;memset(&eb,0,sizeof eb);
        if(diag_on && getenv("BOOT_DIAG_SOFT_SMOKE") && postfx_look_variant("softon")){
            proof_tee=[SoftSmokeTee alloc];proof_tee->target=nil;proof_tee->original=proof_tee->reference=pso;proof_tee->reference_depth=ad_rt.tex;proof_tee->late_control=1;
            proof_tee->lists=@[rt_render_list_new(),rt_render_list_new(),late_record,rt_render_list_new(),rt_render_list_new()];enc=(id<MTLRenderCommandEncoder>)proof_tee;
        }
    }else if (proof_original) {
        proof_tee = [SoftSmokeTee alloc];proof_tee->target=enc;proof_tee->original=proof_original;
        proof_tee->reference=proof_reference;proof_tee->reference_depth=soft_proof_reference_depth;
        proof_tee->lists=@[rt_render_list_new(),rt_render_list_new(),rt_render_list_new(),rt_render_list_new(),rt_render_list_new()];
        enc=(id<MTLRenderCommandEncoder>)proof_tee;memset(&eb,0,sizeof eb);
    }
    @try {
    if (gpt_on == 2 && !soft_smoke_cost_enabled()) {
        extern char diag_fx_pass[];
        Obj *t0 = obj(dev.tex[0]);
        gpt_draw_note("%s prim%u x%u blend%d(%u,%u) z%u zw%u ps %016llX vs %016llX tex0 %ux%u fmt%X", diag_fx_pass[0] ? diag_fx_pass : "-", type, prims, blend, rs[19], rs[20],
                      rs[7], rs[14], (unsigned long long)pse->key, (unsigned long long)vse->key, t0 ? t0->w : 0, t0 ? t0->h : 0, t0 ? t0->fmt : 0);
    }
    if (eb.pso != (__bridge void *)pso) { [enc setRenderPipelineState:pso]; eb.pso = (__bridge void *)pso; }
    if (hdrclip_on) hdrclip_bind(enc, !lamp_replay && !ssao_replay && surface_rt(dev.rt[0])==&bb_rt ? HC_WORLD:HC_OTHER);
    if(float_scene_on){unsigned store=float_scene_store_policy(rt0.pixelFormat,dev.rt[0]);[enc setFragmentBytes:&store length:sizeof store atIndex:27];}
    id<MTLDepthStencilState> dss = depth_state();
    if(late_draw)dss=depth_state_for(rs);
    if (lamp_replay || ssao_replay) {
        uint32_t lr[256]; memcpy(lr, rs, sizeof lr); lr[14] = 0; lr[59] = 0;
        if (ssao_replay) { lr[52]=0;if (lr[23]==2) lr[23]=4; } // conservative coverage; original draw may have written z/stencil
        dss = depth_state_for(lr);   // no second depth/stencil write
    }
    if (mf_active && mf_nodepth) { uint32_t lr[256]; memcpy(lr, rs, sizeof lr); lr[7] = 0; dss = depth_state_for(lr); ++mfs[1].zoff; }
    if (eb.dss != (__bridge void *)dss) { [enc setDepthStencilState:dss]; eb.dss = (__bridge void *)dss; }
    if (!(eb.have & EB_STENCILREF) || eb.stencil_ref != (rs[57] & 255)) { [enc setStencilReferenceValue:rs[57] & 255]; eb.stencil_ref = rs[57] & 255; eb.have |= EB_STENCILREF; }
    uint32_t cull = rs[22];
    if (!(eb.have & EB_CULL) || eb.cull != cull) {
        if (!(eb.have & EB_CULL)) [enc setFrontFacingWinding:MTLWindingClockwise];
        [enc setCullMode:cull == 1 ? MTLCullModeNone : cull == 2 ? MTLCullModeFront : MTLCullModeBack];
        eb.cull = cull; eb.have |= EB_CULL;
    }
    float vpz0, vpz1;
    memcpy(&vpz0, &dev.viewport[4], 4); memcpy(&vpz1, &dev.viewport[5], 4);
    MTLViewport vp = {dev.viewport[0], dev.viewport[1], dev.viewport[2], dev.viewport[3], vpz0, vpz1};
    if(ssao_replay&&!ssao_reference_record){vp.originX*=.5;vp.originY*=.5;vp.width*=.5;vp.height*=.5;}
    if (!(eb.have & EB_VIEWPORT) || memcmp(&eb.vp, &vp, sizeof vp)) { [enc setViewport:vp]; eb.vp = vp; eb.have |= EB_VIEWPORT; }
    MTLScissorRect sc = {0, 0, rt0.width, rt0.height};
    if (rs[174]) {
        int32_t x0 = (int32_t)dev.scissor[0], y0 = (int32_t)dev.scissor[1], x1 = (int32_t)dev.scissor[2], y1 = (int32_t)dev.scissor[3];
        if(ssao_replay&&!ssao_reference_record){x0=(int32_t)floor(x0*.5);y0=(int32_t)floor(y0*.5);x1=(int32_t)ceil(x1*.5);y1=(int32_t)ceil(y1*.5);}
        if (x0 < 0) x0 = 0;
        if (y0 < 0) y0 = 0;
        if (x1 > (int32_t)rt0.width) x1 = (int32_t)rt0.width;
        if (y1 > (int32_t)rt0.height) y1 = (int32_t)rt0.height;
        if (x1 <= x0 || y1 <= y0) return;
        sc = (MTLScissorRect){(NSUInteger)x0, (NSUInteger)y0, (NSUInteger)(x1 - x0), (NSUInteger)(y1 - y0)};
    }
    if (!(eb.have & EB_SCISSOR) || memcmp(&eb.sc, &sc, sizeof sc)) { [enc setScissorRect:sc]; eb.sc = sc; eb.have |= EB_SCISSOR; }
    if (!(eb.have & EB_BIAS) || eb.bias != rs[195] || eb.slope != rs[175]) {
        float bias, slope;
        memcpy(&bias, &rs[195], 4); memcpy(&slope, &rs[175], 4);
        [enc setDepthBias:bias * 16777216.0f slopeScale:slope clamp:0];
        eb.bias = rs[195]; eb.slope = rs[175]; eb.have |= EB_BIAS;
    }
    // The blend colour only matters to factors 14/15. Restore it to 0 too (it stayed at the last nonzero value): `have`
    // marks it known, since replacement emitters clear eb while the encoder keeps its colour.
    int bc_used = rs[27] && (rs[19] == 14 || rs[19] == 15 || rs[20] == 14 || rs[20] == 15 || (rs[206] && (rs[207] == 14 || rs[207] == 15 || rs[208] == 14 || rs[208] == 15)));
    if (diag_on && bc_used) { ++ad1.bf_draws; ad1.bc_stale += !rs[193] && eb.blend_color; }
    if ((rs[193] || bc_used) && (!(eb.have & EB_BLENDC) || eb.blend_color != rs[193])) {
        uint32_t b = rs[193];
        [enc setBlendColorRed:((b >> 16) & 255) / 255.0f green:((b >> 8) & 255) / 255.0f blue:(b & 255) / 255.0f alpha:(b >> 24) / 255.0f];
        eb.blend_color = b; eb.have |= EB_BLENDC;
    }

    // constants: float constants go through the upload ring, only the registers the shader reads, and only when
    // they changed since the last upload; small arrays and the aux blocks are set when they differ from the bound ones
    if (!bind_floats(0, ff ? ff_vsc[0] : dev.vsc[0], ff ? 0 : dev.ver_vsc, vse->refl.float_rel ? 256 : vse->refl.float_max, 256) ||
        !bind_floats(1, ff ? ff_psc[0] : dev.psc[0], ff ? 0 : dev.ver_psc, pse->refl.float_rel ? 256 : pse->refl.float_max, 224)) return;
    if (!(eb.have & EB_VSI) || eb.ver_vsi != dev.ver_vsi) { [enc setVertexBytes:dev.vsi length:sizeof dev.vsi atIndex:17]; eb.ver_vsi = dev.ver_vsi; eb.have |= EB_VSI; }
    if (!(eb.have & EB_VSB) || eb.ver_vsb != dev.ver_vsb) { [enc setVertexBytes:dev.vsb length:sizeof dev.vsb atIndex:18]; eb.ver_vsb = dev.ver_vsb; eb.have |= EB_VSB; }
    if (!(eb.have & EB_PSI) || eb.ver_psi != dev.ver_psi) { [enc setFragmentBytes:dev.psi length:sizeof dev.psi atIndex:1]; eb.ver_psi = dev.ver_psi; eb.have |= EB_PSI; }
    if (!(eb.have & EB_PSB) || eb.ver_psb != dev.ver_psb) { [enc setFragmentBytes:dev.psb length:sizeof dev.psb atIndex:2]; eb.ver_psb = dev.ver_psb; eb.have |= EB_PSB; }
    SmVSAux va = {{1.0f / dev.viewport[2], -1.0f / dev.viewport[3], 0, 0}, 1.0f, {0, 0, 0}};
    // Into a multisampled texture (the mirror, render_target_msaa) no half-pixel move: with it the game's full-target
    // quads (the mirror's RVM_MASK alpha) leave the samples of the first row and column that lie outside the moved
    // quad with the scene's alpha, a 1-pixel line at the mirror's edge after the resolve. The scene moves half a texel.
    if (enc_ms[0] && enc_ms[0]->resolve_pass) va.pos_fixup[0] = va.pos_fixup[1] = 0;
    memcpy(&va.point_size, &rs[154], 4);
    if (!(eb.have & EB_VSAUX) || memcmp(&eb.va, &va, sizeof va)) { [enc setVertexBytes:&va length:sizeof va atIndex:19]; eb.va = va; eb.have |= EB_VSAUX; }
    SmPSAux pa;
    memset(&pa, 0, sizeof pa);
    pa.alpha_func = rs[15] ? rs[25] : 0;
    pa.alpha_ref = (rs[24] & 255) / 255.0f;
    pa.alpha_to_coverage = a2c;
    for (int s = 0; s < 8; ++s) {
        memcpy(&pa.bumpenv[s][0], &dev.tss[s][7], 4); memcpy(&pa.bumpenv[s][1], &dev.tss[s][8], 4);
        memcpy(&pa.bumpenv[s][2], &dev.tss[s][9], 4); memcpy(&pa.bumpenv[s][3], &dev.tss[s][10], 4);
        memcpy(&pa.bumplum[s][0], &dev.tss[s][22], 4); memcpy(&pa.bumplum[s][1], &dev.tss[s][23], 4);
    }
    for (int s = 0; s < 16; ++s) memcpy(&pa.lodbias[s], &dev.ss[s][8], 4);   // D3DSAMP_MIPMAPLODBIAS (a float), in the shader's lookups
    if (!(eb.have & EB_PSAUX) || memcmp(&eb.pa, &pa, sizeof pa)) { [enc setFragmentBytes:&pa length:sizeof pa atIndex:3]; eb.pa = pa; eb.have |= EB_PSAUX; }
    if (pse->mat) {                      // a material-shader replacement's extra constants (materials.m)
        MatConsts mk;
        mat_constants(pse->mat, (const float (*)[4])dev.vsc, &mk);
        if (smoke_draw) {
            static float range;
            if (!(range > 0)) { const char *e = getenv("NFS_SOFT_SMOKE_RANGE"); range = e ? strtof(e, NULL) : 0.6f; if (!(range >= 0.05f && range <= 4.0f)) range = 0.6f; }
            uint32_t cam = M32(0x00919690u);
            float zn = cam ? MF32(cam + 0xBC) : 0.5f, zf = cam ? MF32(cam + 0xC0) : 10000.0f;
            if (!(zn > 0.01f && zn < 10.0f)) zn = 0.5f;
            if (!(zf > zn * 2 && zf < 1e6f)) zf = 10000.0f;
            mk.k[1][0] = zn; mk.k[1][1] = zf; mk.k[1][2] = range;
            mk.k[1][3] = diag_on && postfx_look_variant("softbroken");
            mk.k[2][0]=dev.rs[23];mk.k[2][1]=dev.rs[19]==5 && dev.rs[20]==6;
            [enc setFragmentTexture:late_draw?ad_rt.tex:soft_depth atIndex:15];
        }
        [enc setFragmentBytes:&mk length:sizeof mk atIndex:MAT_PS_BUF];
    }

    // textures and samplers
    for (int k = 0; k < pse->refl.n_samplers; ++k) {
        int s = pse->refl.samplers[k].reg;
        if (diag_on) split_capture_receiver_texture((unsigned)s, dev.tex[s]);
        Obj *t = obj(dev.tex[s]);
        RenderTex *g = t ? gpu_tex(t) : NULL;
        id<MTLTexture> tx = g ? g->tex : nil;
        if (diag_on && s == 0) {                // test only: NFS_TEST_CARDRAW_MISS=1 drops sampler 0 of car draws, to show the cardraw missing-texture counter fires
            static int force = -1;
            if (force < 0) force = getenv("NFS_TEST_CARDRAW_MISS") != NULL;
            uint32_t cfx = M32(0x00982C80u);
            if (force && cfx && M32(cfx + 4) == 4) { g = NULL; tx = nil; }
        }
        int ty = pse->refl.samplers[k].type;
        int shadow = pse->refl.samplers[k].shadow;
        if (shadow) {
            if (!tx || !is_depth_fmt(t->fmt)) tx = zero_depth;
            if (probe_sig & PROBE_SHADOWS) { tx = probe_depth; shadow = 2; probe_applied(PROBE_SHADOWS, 1); }   // depth 0: in shadow
        }
        else if ((probe_sig & PROBE_ROADREFL) && tx && x360_reflection_target(t->guest, t->w, t->h)) {
            tx = probe_magenta; probe_applied(PROBE_ROADREFL, 1);    // the X360-size road reflection reads magenta
        }
        else if (!tx || (ty == SM_TEX_CUBE) != (tx.textureType == MTLTextureTypeCube) || (ty == SM_TEX_3D) != (tx.textureType == MTLTextureType3D)) {
            if (diag_on) { extern void mat_cardraw_missing(int kind, int sampler, uint32_t guest, uint32_t fmt); mat_cardraw_missing(!t ? 1 : !g ? 2 : 3, s, dev.tex[s], t ? t->fmt : 0); }
            tx = ty == SM_TEX_CUBE ? zero_texcube : ty == SM_TEX_3D ? zero_tex3d : zero_tex2d;
        }
        else if (diag_on && t && !t->mem[0] && !g->rt) { extern void mat_cardraw_missing(int kind, int sampler, uint32_t guest, uint32_t fmt); mat_cardraw_missing(4, s, dev.tex[s], t->fmt); }
        if (eb.ftex[s] != (__bridge void *)tx) { [enc setFragmentTexture:tx atIndex:(NSUInteger)s]; eb.ftex[s] = (__bridge void *)tx; }
        id<MTLSamplerState> smp = sampler_state(s, shadow);
        if (diag_on && !shadow && t && t->levels > 1) {          // mipmapped textures' filtering (g_TextureFiltering), stats below
            const uint32_t *q = dev.ss[s];
            ++stat_minfilt[q[6] == 3 ? 2 : q[6] >= 2 ? 1 : 0];
            stat_aniso16 += q[6] == 3 && q[10] >= 16; stat_mipbelow += q[7] < 2; stat_lodbias += q[8] != 0;
        }
        if (eb.fsamp[s] != (__bridge void *)smp) { [enc setFragmentSamplerState:smp atIndex:(NSUInteger)s]; eb.fsamp[s] = (__bridge void *)smp; }
        if (texdump_frame() && g) { id<MTLRenderCommandEncoder> e = enc; enc = nil; if (shadow) depthdump(dev.tex[s], g->tex); else texdump(dev.tex[s], g->tex); enc = e; }
    }
    if (pse->mat && mat_extra_texture(pse->mat) == MAT_TEX_RIPPLES) {   // materials.m: the X360 rain ripple frames
        id<MTLTexture> tx = ripple_texture();
        if (eb.ftex[MAT_TEX_SLOT] != (__bridge void *)tx) { [enc setFragmentTexture:tx atIndex:MAT_TEX_SLOT]; eb.ftex[MAT_TEX_SLOT] = (__bridge void *)tx; }
    }
    if (pse->mat && mat_extra_texture(pse->mat) == MAT_TEX_CARNM) {      // materials.m R26: this car draw's normal map (zero texture: none)
        Obj *nt = obj(mat_carnm_texture());
        RenderTex *ng = nt ? gpu_tex(nt) : NULL;
        id<MTLTexture> tx = ng && ng->tex && ng->tex.textureType == MTLTextureType2D ? ng->tex : zero_tex2d;
        if (eb.ftex[MAT_TEX_SLOT] != (__bridge void *)tx) { [enc setFragmentTexture:tx atIndex:MAT_TEX_SLOT]; eb.ftex[MAT_TEX_SLOT] = (__bridge void *)tx; }
    }
    if (pse->mat && mat_extra_texture(pse->mat) == MAT_TEX_CARNM) {      // materials.m R30: the flake's noise volume (R27's), a neutral 1^3 when it is not loaded
        static id<MTLTexture> flat3d;
        id<MTLTexture> tx = mat_flake_volume_ok() && grass_upload() ? grass_volume_texture : nil;
        if (!tx) {
            if (!flat3d) {
                MTLTextureDescriptor *d = [MTLTextureDescriptor new];
                d.textureType = MTLTextureType3D; d.pixelFormat = MTLPixelFormatRGBA8Unorm; d.width = d.height = d.depth = 1; d.usage = MTLTextureUsageShaderRead;
                flat3d = [mtl newTextureWithDescriptor:d];
                const uint8_t px[4] = {128, 128, 128, 128};
                [flat3d replaceRegion:MTLRegionMake3D(0, 0, 0, 1, 1, 1) mipmapLevel:0 slice:0 withBytes:px bytesPerRow:4 bytesPerImage:4];
            }
            tx = flat3d;
        }
        if (eb.ftex[14] != (__bridge void *)tx) { [enc setFragmentTexture:tx atIndex:14]; eb.ftex[14] = (__bridge void *)tx; }
    }
    for (int k = 0; k < vse->refl.n_samplers; ++k) {
        int s = vse->refl.samplers[k].reg;
        Obj *t = obj(dev.tex[16 + s]);
        RenderTex *g = t ? gpu_tex(t) : NULL;
        id<MTLTexture> tx = g ? g->tex : zero_tex2d;
        if (eb.vtex[s & 3] != (__bridge void *)tx) { [enc setVertexTexture:tx atIndex:(NSUInteger)s]; eb.vtex[s & 3] = (__bridge void *)tx; }
        id<MTLSamplerState> smp = sampler_state(16 + s, 0);
        if (eb.vsamp[s & 3] != (__bridge void *)smp) { [enc setVertexSamplerState:smp atIndex:(NSUInteger)s]; eb.vsamp[s & 3] = (__bridge void *)smp; }
    }

    if (texdump_frame()) {
        FILE *df = dump_file();
        if (df) {
            uint32_t *r = dev.rs;
            fprintf(df, "draw %llu: prim %u x%u %s base %d first %u count %u start %u%s | vs %016llX v%d.%d ps %016llX v%d.%d | rt %08X ds %08X vp %u,%u %ux%u\n",
                    (unsigned long long)stat_draws, type, prims, indexed ? "indexed" : "plain", base_vertex, indexed ? min_index : start, indexed ? num_vertices : prim_vertices(type, prims), start, up_vertices ? " UP" : "",
                    (unsigned long long)vse->key, vse->refl.major, vse->refl.minor, (unsigned long long)pse->key, pse->refl.major, pse->refl.minor,
                    dev.rt[0], dev.ds, dev.viewport[0], dev.viewport[1], dev.viewport[2], dev.viewport[3]);
            fprintf(df, "  states: z %u zwrite %u zfunc %u cull %u blend %u (%u,%u op %u) alphatest %u ref %u func %u colorwrite %X scissor %u\n",
                    r[7], r[14], r[23], r[22], r[27], r[19], r[20], r[171], r[15], r[24], r[25], r[168], r[174]);
            for (int i = 0; i < nel; ++i) fprintf(df, "  elem s%u +%u type %u usage %u/%u\n", el[i].stream, el[i].offset, el[i].type, el[i].usage, el[i].index);
            extern uint32_t x360_ui_diag_hash(uint32_t guest);
            uint32_t ui_hash = x360_ui_diag_hash(dev.tex[0]);
            if (ui_hash && up_vertices && !indexed && type == 6 && prims == 2) {
                int po = -1, uv = -1;
                for (int i = 0; i < nel; ++i) if (!el[i].stream && !el[i].index) {
                    if (el[i].usage == 0 && el[i].type == 2) po = el[i].offset;
                    if (el[i].usage == 5 && el[i].type == 1) uv = el[i].offset;
                }
                if (po >= 0 && uv >= 0) for (unsigned v = 0; v < 4; ++v) {
                    uint32_t a = up_vertices + v * up_stride;
                    fprintf(df, "  ui %08X v%u: %.9g %.9g %.9g uv %.9g %.9g\n", ui_hash, v,
                            MF32(a + po), MF32(a + po + 4), MF32(a + po + 8), MF32(a + uv), MF32(a + uv + 4));
                }
            }
            for (int k = 0; k < vse->refl.n_inputs; ++k) fprintf(df, "  vs in v%u usage %u/%u\n", vse->refl.inputs[k].reg, vse->refl.inputs[k].usage, vse->refl.inputs[k].usage_index);
            for (int s2 = 0; s2 < 16; ++s2)
                if (used_streams & (1u << s2)) {
                    Obj *vb = obj(dev.stream[s2]);
                    fprintf(df, "  stream %d: vb %08X usage %X size %u off %u stride %u\n", s2, dev.stream[s2], vb ? vb->usage : 0, vb ? vb->size : 0, dev.stream_off[s2], strides[s2]);
                }
            for (int i = 0; i < nel; ++i) {      // vertex colours (D3DCOLOR) over the drawn range: the world's baked prelight
                if (el[i].usage != 10 || el[i].type != 4 || up_vertices) continue;
                Obj *vb = obj(dev.stream[el[i].stream]);
                uint32_t st = strides[el[i].stream], v0 = indexed ? (uint32_t)(base_vertex + (int)min_index) : start;
                uint32_t nv = indexed ? num_vertices : prim_vertices(type, prims);
                if (!vb || !vb->mem[0] || !st) continue;
                double sum[3] = {0}; int mn[3] = {255, 255, 255}, mx[3] = {0}; uint32_t n = 0;
                for (uint32_t v = v0; v < v0 + nv; ++v) {
                    uint32_t off = dev.stream_off[el[i].stream] + v * st + el[i].offset;
                    if (off + 4 > vb->size) break;
                    const uint8_t *p = G + vb->mem[0] + off;      // B G R A
                    int rgb[3] = {p[2], p[1], p[0]};
                    for (int k = 0; k < 3; ++k) { sum[k] += rgb[k]; if (rgb[k] < mn[k]) mn[k] = rgb[k]; if (rgb[k] > mx[k]) mx[k] = rgb[k]; }
                    ++n;
                }
                if (n) fprintf(df, "  vcolor %u/%u: n %u mean %.1f %.1f %.1f warm %.1f min %d %d %d max %d %d %d\n", el[i].usage, el[i].index, n,
                               sum[0] / n, sum[1] / n, sum[2] / n, (sum[0] + sum[1]) / (2.0 * n) - sum[2] / n, mn[0], mn[1], mn[2], mx[0], mx[1], mx[2]);
            }
            if (indexed && !up_indices) { Obj *ib = obj(dev.indices); fprintf(df, "  ib %08X usage %X fmt %u size %u\n", dev.indices, ib ? ib->usage : 0, ib ? ib->fmt : 0, ib ? ib->size : 0); }
            for (int k = 0; k < pse->refl.n_samplers; ++k) {
                int sm = pse->refl.samplers[k].reg;
                Obj *t = obj(dev.tex[sm]);
                fprintf(df, "  s%d: tex %08X kind %d fmt %X %ux%u levels %u usage %X | addr %u,%u filt %u/%u/%u\n", sm, dev.tex[sm], t ? t->kind : 0, t ? t->fmt : 0,
                        t ? t->w : 0, t ? t->h : 0, t ? t->levels : 0, t ? t->usage : 0, dev.ss[sm][1], dev.ss[sm][2], dev.ss[sm][5], dev.ss[sm][6], dev.ss[sm][7]);
                float bias; memcpy(&bias, &dev.ss[sm][8], sizeof bias);
                RenderTex *gt = t ? t->gpu : NULL;
                fprintf(df, "    lod: bias %.4f min %u GPU levels %lu format %lu\n", bias, dev.ss[sm][9],
                        (unsigned long)(gt ? gt->tex.mipmapLevelCount : 0), (unsigned long)(gt ? gt->tex.pixelFormat : 0));
            }
            extern char diag_fx_pass[];
            fprintf(df, "  pass: %s\n", diag_fx_pass[0] ? diag_fx_pass : "(no effect pass)");
            if (ff) fprintf(df, "  fixed function: synthesized shaders\n");
            dump_consts(df, "vs", &vse->refl, ff ? ff_vsc : dev.vsc, 256);
            dump_consts(df, "ps", &pse->refl, ff ? ff_psc : dev.psc, 224);
        }
    }
    // Fast path: every stream and the index buffer are static -> bind them as they are (no copies, no index scan).
    static int nofast = -1;
    if (nofast < 0) nofast = getenv("BOOT_NOFAST") != NULL;
    if (!up_vertices && type != 6 && !nofast) {
        int all_static = 1;
        for (int s = 0; s < 16 && all_static; ++s)
            if (used_streams & (1u << s)) { Obj *vb = obj(dev.stream[s]); if (!vb || (vb->usage & 0x200)) all_static = 0; }
        Obj *ib = indexed ? obj(dev.indices) : NULL;
        if (indexed && (!ib || (ib->usage & 0x200))) all_static = 0;
        if (all_static) {
            for (int s = 0; s < 16; ++s)
                if (used_streams & (1u << s)) [enc setVertexBuffer:gpu_buf(obj(dev.stream[s])) offset:dev.stream_off[s] atIndex:(NSUInteger)s];
            if (need_zero) [enc setVertexBuffer:zero_buf offset:0 atIndex:30];
            uint32_t nv = prim_vertices(type, prims);
            if (!indexed) { material_emit(&grass, &water, &aux, ^{ [enc drawPrimitives:prim_type(type) vertexStart:start vertexCount:nv]; }); return; }
            uint32_t isz = ib->fmt == 102 ? 4 : 2;
            material_emit(&grass, &water, &aux, ^{
            [enc drawIndexedPrimitives:prim_type(type) indexCount:nv indexType:isz == 4 ? MTLIndexTypeUInt32 : MTLIndexTypeUInt16
                           indexBuffer:gpu_buf(ib) indexBufferOffset:start * isz instanceCount:1 baseVertex:base_vertex baseInstance:0];
            });
            return;
        }
    }
    // vertex data: copy the referenced range of each stream into the ring. For indexed draws the range comes from
    // the indices themselves: minIndex/numVertices are only hints in D3D9 (used for software vertex processing).
    uint32_t nverts = prim_vertices(type, prims);
    uint32_t first = indexed ? min_index : start, count = indexed ? num_vertices : nverts;
    if (indexed) {
        uint32_t ib_fmt = up_indices ? up_index_fmt : (obj(dev.indices) ? obj(dev.indices)->fmt : 101);
        uint32_t isz = ib_fmt == 102 ? 4 : 2, base = up_indices ? up_indices : (obj(dev.indices) ? obj(dev.indices)->mem[0] : 0);
        if (!base) return;
        base += start * isz;
        uint32_t lo = 0xFFFFFFFF, hi = 0;
        for (uint32_t k = 0; k < nverts; ++k) {
            uint32_t v = isz == 4 ? M32(base + 4 * k) : M16(base + 2 * k);
            if (v < lo) lo = v;
            if (v > hi) hi = v;
        }
        if (lo > hi) return;
        first = lo;
        count = hi - lo + 1;
        num_vertices = count;
        min_index = lo;
    }
    if (up_vertices && !indexed) first = 0;
    for (int s = 0; s < 16; ++s) {
        if (!(used_streams & (1u << s))) continue;
        uint32_t src, stride = strides[s];
        if (up_vertices) src = up_vertices;
        else {
            Obj *vb = obj(dev.stream[s]);
            if (!vb) return;
            src = vb->mem[0] + dev.stream_off[s];
        }
        int64_t from = (int64_t)(indexed ? (int64_t)base_vertex + first : first) * stride;
        uint32_t bytes = (count + 1) * stride;
        uint32_t off;
        __unsafe_unretained id<MTLBuffer> b;
        void *p = ring_alloc(bytes, &off, &b);
        if (!p) return;
        memcpy(p, G + (uint32_t)(src + from), bytes);
        // the buffer is bound so that index 0 is vertex `first` (base vertex applied in the copy)
        [enc setVertexBuffer:b offset:off atIndex:(NSUInteger)s];
    }
    if (glass_now.pso) {     // the copied streams start at vertex base_vertex+first: so does the UV1 side stream (audit 1)
        int64_t rb = indexed ? (int64_t)base_vertex + first : first;
        if (diag_on) ad1.glass_rebased += rb != 0;
        if (rb > 0) glass_now.uv1_off += (NSUInteger)(rb * (int64_t)sizeof(float));
    }
    if (need_zero) [enc setVertexBuffer:zero_buf offset:0 atIndex:30];

    MTLPrimitiveType pt = prim_type(type);
    if (!indexed && type != 6) {
        material_emit(&grass, &water, &aux, ^{ [enc drawPrimitives:pt vertexStart:0 vertexCount:nverts]; });
        return;
    }
    // indices (fans become lists), rebased to `first`
    uint32_t idx_bytes = (indexed ? ((up_indices ? up_index_fmt : obj(dev.indices) ? obj(dev.indices)->fmt : 101) == 102 ? 4 : 2) : 2);
    uint32_t ilist = type == 6 ? 3 * prims : nverts;
    uint32_t off;
    __unsafe_unretained id<MTLBuffer> ibuffer;
    uint32_t *out = ring_alloc(4 * (NSUInteger)ilist, &off, &ibuffer);
    if (!out) return;
    uint32_t isrc = 0;
    if (indexed) {
        if (up_indices) isrc = up_indices;
        else { Obj *ib = obj(dev.indices); if (!ib) return; isrc = ib->mem[0]; }
        isrc += start * idx_bytes;
    }
    for (uint32_t k = 0; k < ilist; ++k) {
        uint32_t src_k = type == 6 ? (k % 3 == 0 ? 0 : k / 3 + k % 3) : k;
        uint32_t v = indexed ? (idx_bytes == 4 ? M32(isrc + 4 * src_k) : M16(isrc + 2 * src_k)) : src_k;
        out[k] = indexed ? v - first : v;
    }
    int mf_slot = -1;
    if (mf_active && mf_npend < 1024) {      // diag: count this flare's passing samples in a fresh visibility slot
        static uint32_t mf_next;
        mf_slot = (int)(VIS_SLOTS - 1 - (mf_next++ % 1024u));       // the top 1024 slots; sun queries count up from slot 0
        ((uint64_t *)vis_buf.contents)[mf_slot] = 0;
        [enc setVisibilityResultMode:MTLVisibilityResultModeCounting offset:(NSUInteger)mf_slot * 8];
    }
    material_emit(&grass, &water, &aux, ^{
    [enc drawIndexedPrimitives:pt == MTLPrimitiveTypeTriangleStrip && type == 6 ? MTLPrimitiveTypeTriangle : pt indexCount:ilist
                     indexType:MTLIndexTypeUInt32 indexBuffer:ibuffer indexBufferOffset:off];
    });
    if (mf_active) {
        ++mf_encoded;
        unsigned *l = mfs[mf_nodepth].last;
        l[0] = rs[7]; l[1] = rs[14]; l[2] = rs[23]; l[3] = rs[27]; l[4] = rs[19]; l[5] = rs[20];
        l[6] = (unsigned)dev.viewport[2]; l[7] = (unsigned)dev.viewport[3]; l[8] = (unsigned)dev.viewport[0]; l[9] = (unsigned)dev.viewport[1];
        l[10] = (unsigned)rt0.width; l[11] = (unsigned)rt0.height;
        if (!mf_rt_w) { mf_rt_w = l[10]; mf_rt_h = l[11]; mf_first_serial = frame_serial; }
        mf_pass_log("F", l[10], l[11]);
        if (mf_slot >= 0) {
            mf_pend[mf_npend++] = (typeof(mf_pend[0])){(uint32_t)mf_slot, enc_samples, frame_serial, mf_nodepth};
            if (q_active) query_segment(); else [enc setVisibilityResultMode:MTLVisibilityResultModeDisabled offset:0];
        }
    }
    } @finally {
        if(late_draw){
            enc=proof_real;[soft_late_draws addObject:late_record];
            if(proof_tee)[soft_late_broken addObject:proof_tee->lists[3]];
            memset(&eb,0,sizeof eb);
        }else if(proof_tee){
            enc=proof_real;end_pass();
            for(int i=0;i<5;++i){
                MTLRenderPassDescriptor *rp=soft_proof_pass(&soft_proof_rt[i],1,1);
                if(gpt_on)gpt_render_auto(rp,"soft smoke proof replay");
                id<MTLRenderCommandEncoder> e=[cmd renderCommandEncoderWithDescriptor:rp];if(gpt_on)gpt_name(e);
                rt_render_list_replay(proof_tee->lists[i],e);[e endEncoding];
            }
            memset(&eb,0,sizeof eb);
        }
    }
}

// ---------------- copies ----------------
void blit_quad(id<MTLTexture> src, MTLRegion sr, id<MTLTexture> dst, NSUInteger level, NSUInteger slice, MTLRegion dr, int linear) {
    end_pass();
    ensure_cmd();
    quad_into(cmd, src, sr, dst, level, slice, dr, linear);
}
void quad_into(id<MTLCommandBuffer> cb, id<MTLTexture> src, MTLRegion sr, id<MTLTexture> dst, NSUInteger level, NSUInteger slice, MTLRegion dr, int linear) {
    MTLRenderPassDescriptor *rp = [MTLRenderPassDescriptor renderPassDescriptor];
    rp.colorAttachments[0].texture = dst;
    rp.colorAttachments[0].level = level;
    rp.colorAttachments[0].slice = slice;
    rp.colorAttachments[0].loadAction = MTLLoadActionLoad;
    rp.colorAttachments[0].storeAction = MTLStoreActionStore;
    if (gpt_on && cb == cmd) gpt_render_auto(rp, "quad");
    id<MTLRenderCommandEncoder> e = [cb renderCommandEncoderWithDescriptor:rp];
    if (gpt_on && cb == cmd) gpt_name(e);
    if(!float_scene_on && dst.pixelFormat!=MTLPixelFormatBGRA8Unorm && dst.pixelFormat!=MTLPixelFormatRGBA16Float){[e endEncoding];return;}
    id<MTLRenderPipelineState> pso = blit_pso;
    if (dst.pixelFormat!=MTLPixelFormatBGRA8Unorm || dst.sampleCount!=1) {
        uint64_t key=((uint64_t)dst.pixelFormat<<32)|dst.sampleCount;
        // The presenter now uses this cache too. A game-thread insertion/rehash
        // must not race a float drawable lookup on nfsmw.present.
        @synchronized (blit_lib) {
            pso=(__bridge id<MTLRenderPipelineState>)map_get(&blit_pipelines,key);
            if (!pso) {
                MTLRenderPipelineDescriptor *pd = [MTLRenderPipelineDescriptor new];
                pd.vertexFunction = [blit_lib newFunctionWithName:@"blit_vs"];
                pd.fragmentFunction = [blit_lib newFunctionWithName:@"blit_fs"];
                pd.colorAttachments[0].pixelFormat = dst.pixelFormat;
                pd.rasterSampleCount = dst.sampleCount;
                NSError *error = nil;
                pso = pipeline_build(pd, __func__, &error);
                if(pso)map_put(&blit_pipelines,key,pso);
                else if(diag_on)diag_event("quad: pipeline format=%lu samples=%lu error=%s",(unsigned long)dst.pixelFormat,(unsigned long)dst.sampleCount,error.localizedDescription.UTF8String);
            }
        }
    }
    if(!pso){[e endEncoding];abort();}
    [e setRenderPipelineState:pso];
    float dw = (float)mipdim((uint32_t)dst.width, (uint32_t)level), dh = (float)mipdim((uint32_t)dst.height, (uint32_t)level);
    float r[8] = {
        dr.origin.x / dw * 2 - 1, 1 - dr.origin.y / dh * 2, (dr.origin.x + dr.size.width) / dw * 2 - 1, 1 - (dr.origin.y + dr.size.height) / dh * 2,
        sr.origin.x / (float)src.width, sr.origin.y / (float)src.height, (sr.origin.x + sr.size.width) / (float)src.width, (sr.origin.y + sr.size.height) / (float)src.height};
    [e setVertexBytes:r length:sizeof r atIndex:0];
    [e setFragmentTexture:src atIndex:0];
    [e setFragmentSamplerState:linear ? blit_linear : blit_point atIndex:0];
    [e drawPrimitives:MTLPrimitiveTypeTriangleStrip vertexStart:0 vertexCount:4];
    [e endEncoding];
}
void render_stretch(uint32_t src, uint32_t src_rect, uint32_t dst, uint32_t dst_rect, uint32_t filter) {
    if (!mtl) return;
    if(hdrclip_on && src==dev.backbuffer)float_scene_ramp();
    NSUInteger sl0, ss0, dl, ds_;
    id<MTLTexture> s = surface_target(src, &sl0, &ss0);
    if(float_scene_on && fs_active && s.pixelFormat==MTLPixelFormatRGBA16Float)float_scene_copy(dst,MTLPixelFormatRGBA16Float);
    else if(float_scene_on && !fs_active && src==dev.backbuffer)float_scene_copy(dst,fs_composing?MTLPixelFormatRGBA16Float:MTLPixelFormatBGRA8Unorm);
    id<MTLTexture> d = surface_target(dst, &dl, &ds_);
    if (hdrclip_on && src==dev.backbuffer) hdrclip_scan(s, HC_MSAA);
    if(hdrclip_on && src==dev.backbuffer && MF32(0x009885D8u)>=15.0f){static int logged;if(!logged++){diag_event("float scene copy: active=%d source_format=%lu target_format=%lu",fs_active,(unsigned long)s.pixelFormat,(unsigned long)d.pixelFormat);}}
    id<MTLTexture> samples=s;
    if (s && s.sampleCount > 1) { RenderTex *r = surface_rt(src); msaa_resolve(r); s = r->tex; }   // from a multisampled surface
    if(hdrclip_on && src==dev.backbuffer && samples.sampleCount>1)hdrclip_pair(samples,s,0);
    Obj *so = obj(src);
    if (!s && so && so->mem[0]) {                             // a system-memory surface: upload it first
        if (!so->usage) so->usage = 0;
        RenderTex *g = gpu_tex(so);
        s = g ? g->tex : nil;
    }
    if (!s || !d || is_depth_fmt(obj(dst) ? obj(dst)->fmt : 0)) return;
    uint32_t sw = mipdim((uint32_t)s.width, (uint32_t)sl0), sh = mipdim((uint32_t)s.height, (uint32_t)sl0);
    uint32_t dw = mipdim((uint32_t)d.width, (uint32_t)dl), dh = mipdim((uint32_t)d.height, (uint32_t)dl);
    MTLRegion sr = src_rect ? MTLRegionMake2D(M32(src_rect), M32(src_rect + 4), M32(src_rect + 8) - M32(src_rect), M32(src_rect + 12) - M32(src_rect + 4)) : MTLRegionMake2D(0, 0, sw, sh);
    MTLRegion dr = dst_rect ? MTLRegionMake2D(M32(dst_rect), M32(dst_rect + 4), M32(dst_rect + 8) - M32(dst_rect), M32(dst_rect + 12) - M32(dst_rect + 4)) : MTLRegionMake2D(0, 0, dw, dh);
    RenderTex *dr_rt = d.sampleCount > 1 ? surface_rt(dst) : NULL;
    int attached = 0;                                          // dst's samples are in the open pass (resolve on store)
    if (dr_rt) for (int i = 0; i < 5; ++i) if (enc_ms[i] == dr_rt && enc_resolve[i]) attached = 1;
    if (hdrclip_on && src==dev.backbuffer) hdrclip_scan(s, HC_RESOLVE);
    blit_quad(s, sr, d, dl, ds_, dr, filter >= 2);
    if(hdrclip_on && src==dev.backbuffer && !src_rect && !dst_rect)hdrclip_pair(s,d,1);
    if (hdrclip_on && src==dev.backbuffer) hdrclip_scan(d, HC_COPY);
    // Marked after the copy: blit_quad's end_pass clears the stale mark of a resolve-on-store attachment, which would
    // lose this copy's samples (a read before the next draw returned the pre-copy pixels).
    if (dr_rt) {
        dr_rt->stale = 1;
        if (diag_on && diag_dir) {                             // stretch_ms.log: every StretchRect into a multisampled surface
            char path[1024]; snprintf(path, sizeof path, "%s/stretch_ms.log", diag_dir);
            FILE *df = fopen(path, "a");
            if (df) { fprintf(df, "frame %llu %lux%lu attached %d\n", (unsigned long long)dev.frames, (unsigned long)d.width, (unsigned long)d.height, attached); fclose(df); }
        }
    }
}
void render_readback(uint32_t src, uint32_t dst) {
    (void)src; (void)dst;   // GetRenderTargetData: not needed for the front end yet
}

void save_png(id<MTLTexture> t, const char *path, int keep_alpha) {
    size_t n = t.width * t.height * 4;
    void *p = malloc(n);
    [t getBytes:p bytesPerRow:t.width * 4 fromRegion:MTLRegionMake2D(0, 0, t.width, t.height) mipmapLevel:0];
    diag_png(path, p, (uint32_t)t.width, (uint32_t)t.height, (uint32_t)t.width * 4, keep_alpha);
    free(p);
}

// BOOT_TEXDUMP=frame[:dir]: every texture sampled during that frame is saved as <dir>/tex_<guest>.png
static uint32_t dumped[4096];
static int ndumped;
// Every float constant a shader reads, with the D3DX parameter name from its CTAB when it has one.
static void dump_consts(FILE *df, const char *stage, const SmResult *r, float (*k)[4], int nk) {
    for (int i = 0; i < nk; ++i) {
        if (!(r->float_used[i >> 5] & (1u << (i & 31))) || (r->float_defined[i >> 5] & (1u << (i & 31)))) continue;
        const char *name = "";
        int idx = 0;
        for (int j = 0; j < r->n_constants; ++j)
            if (r->constants[j].set == 2 && i >= r->constants[j].reg && i < r->constants[j].reg + r->constants[j].count) { name = r->constants[j].name; idx = i - r->constants[j].reg; }
        fprintf(df, "  %s c%d = %g %g %g %g  %s", stage, i, k[i][0], k[i][1], k[i][2], k[i][3], name);
        if (*name && idx) fprintf(df, "[+%d]", idx);
        fputc('\n', df);
    }
}
int texdump_frame(void) { return diag_on && diag_frame_wanted(dev.frames, "BOOT_DIAG_DRAWS"); }
// The draw-dump file of the current frame (BOOT_DIAG_DRAWS), or NULL.
FILE *dump_file(void) {
    static FILE *df;
    static uint64_t df_frame = ~0ull;
    if (!texdump_frame()) return NULL;
    if (df_frame != dev.frames) {
        if (df) fclose(df);
        char path[1024];
        snprintf(path, sizeof path, "%s/draws_%05llu.txt", diag_dir, (unsigned long long)dev.frames);
        df = fopen(path, "w");
        df_frame = dev.frames;
    }
    return df;
}
int render_dump_active(void) { return texdump_frame(); }
// Every COM call of a dumped frame goes into its draw dump too (render targets, copies, clears, effect calls).
void render_dump_call(const char *line) {
    FILE *df = dump_file();
    if (df) fprintf(df, "call %s\n", line);
}
void texdump(uint32_t guest, id<MTLTexture> src) {
    if (!src || src.textureType != MTLTextureType2D || ndumped >= 4096) return;
    for (int i = 0; i < ndumped; ++i) if (dumped[i] == guest) return;
    dumped[ndumped++] = guest;
    char path[1024];
    snprintf(path, sizeof path, "%s/tex_%05llu", diag_dir, (unsigned long long)dev.frames);
    mkdir(path, 0755);
    snprintf(path, sizeof path, "%s/tex_%05llu/tex_%08X_%lux%lu_fmt%X.png", diag_dir, (unsigned long long)dev.frames, guest,
             (unsigned long)src.width, (unsigned long)src.height, obj(guest) ? obj(guest)->fmt : 0);
    MTLTextureDescriptor *d = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:src.width height:src.height mipmapped:NO];
    d.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead;
    d.storageMode = MTLStorageModeShared;
    id<MTLTexture> dst = [mtl newTextureWithDescriptor:d];
    id<MTLCommandBuffer> save = cmd;
    id<MTLRenderCommandEncoder> save_enc = enc;
    cmd = rt_command_buffer(); enc = nil;
    int lamp_hold = lamp_flushing; lamp_flushing = 1;   // recorded lamp replays belong after this frame's scene, not in this early buffer
    blit_quad(src, MTLRegionMake2D(0, 0, src.width, src.height), dst, 0, 0, MTLRegionMake2D(0, 0, src.width, src.height), 0);
    [cmd commit];
    [cmd waitUntilCompleted];
    cmd = save; enc = save_enc; lamp_flushing = lamp_hold;
    save_png(dst, path, !(obj(guest) && (obj(guest)->usage & 1)));   // render-target textures: colour only
}

// A sampled depth texture (hardware shadow map) as grey levels stretched over its depth range (black = nearest); it
// holds what was committed before this frame's command buffer, i.e. the previous frame's map.
static void depthdump(uint32_t guest, id<MTLTexture> src) {
    if (!src || src.textureType != MTLTextureType2D || ndumped >= 4096) return;
    for (int i = 0; i < ndumped; ++i) if (dumped[i] == guest) return;
    dumped[ndumped++] = guest;
    size_t w = src.width, h = src.height;
    id<MTLBuffer> b = [mtl newBufferWithLength:w * h * 4 options:MTLResourceStorageModeShared];
    id<MTLCommandBuffer> cb = rt_command_buffer();
    id<MTLBlitCommandEncoder> be = [cb blitCommandEncoder];
    [be copyFromTexture:src sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0, 0, 0) sourceSize:MTLSizeMake(w, h, 1)
               toBuffer:b destinationOffset:0 destinationBytesPerRow:w * 4 destinationBytesPerImage:w * h * 4
                options:MTLBlitOptionDepthFromDepthStencil];
    [be endEncoding]; [cb commit]; [cb waitUntilCompleted];
    const float *z = b.contents;
    float lo = 1, hi = 0;
    size_t n1 = 0;
    for (size_t i = 0; i < w * h; ++i) { if (z[i] < 1) { if (z[i] < lo) lo = z[i]; if (z[i] > hi) hi = z[i]; } else ++n1; }
    uint8_t *px = malloc(w * h * 4);
    for (size_t i = 0; i < w * h; ++i) {
        uint8_t v = z[i] >= 1 ? 255 : (uint8_t)(hi > lo ? 230.0f * (z[i] - lo) / (hi - lo) : 0);
        px[4 * i] = px[4 * i + 1] = px[4 * i + 2] = v; px[4 * i + 3] = 255;
    }
    char path[1024];
    snprintf(path, sizeof path, "%s/tex_%05llu", diag_dir, (unsigned long long)dev.frames);
    mkdir(path, 0755);
    snprintf(path, sizeof path, "%s/tex_%05llu/depth_%08X_%zux%zu_z%.5f-%.5f_cleared%.0fpct.png", diag_dir, (unsigned long long)dev.frames,
             guest, w, h, lo, hi, 100.0 * n1 / (w * h));
    diag_png(path, px, (uint32_t)w, (uint32_t)h, (uint32_t)w * 4, 0);
    free(px);
}

// In the frames of BOOT_DIAG_DRAWS, render target 0 is saved after every BOOT_DIAG_DRAWSHOT-th draw (default 25,
// 0 = off) as <dir>/shots_NNNNN/draw_<draw number>.png, so the draw that changes the picture can be found.
static void drawshot(void) {
    static int every = -1;
    if (every < 0) every = getenv("BOOT_DIAG_DRAWSHOT") ? atoi(getenv("BOOT_DIAG_DRAWSHOT")) : 25;
    static uint64_t frame = ~0ull;
    static unsigned n;
    if (frame != dev.frames) { frame = dev.frames; n = 0; }
    if (every <= 0 || ++n % (unsigned)every) return;
    NSUInteger lv, sl;
    id<MTLTexture> t = surface_target(dev.rt[0], &lv, &sl);
    if (t && t.sampleCount > 1) { RenderTex *r = surface_rt(dev.rt[0]); msaa_resolve(r); t = r->tex; }
    if (!t || t.textureType != MTLTextureType2D) return;
    char path[1024];
    snprintf(path, sizeof path, "%s/shots_%05llu", diag_dir, (unsigned long long)dev.frames);
    mkdir(path, 0755);
    snprintf(path, sizeof path, "%s/shots_%05llu/draw_%llu_rt%08X.png", diag_dir, (unsigned long long)dev.frames, (unsigned long long)stat_draws, dev.rt[0]);
    MTLTextureDescriptor *d = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:t.width height:t.height mipmapped:NO];
    d.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead;
    d.storageMode = MTLStorageModeShared;
    id<MTLTexture> dst = [mtl newTextureWithDescriptor:d];
    blit_quad(t, MTLRegionMake2D(0, 0, t.width, t.height), dst, 0, 0, MTLRegionMake2D(0, 0, t.width, t.height), 0);   // ends the pass
    [cmd commit];
    [cmd waitUntilCompleted];
    cmd = nil;                                                   // the next draw starts a new command buffer and a loading pass
    save_png(dst, path, 0);
}
// stderr, every 300 frames: the draw and pipeline counts.
void render_stats_log(void) {
    pipeline_diag_tick();
    mat_grass_diag_frame();
    mat_aux_frame();
    mat_glass_frame();
    mat_trees_frame();
    mat_bone_frame();
    if (diag_on && dev.frames % 600 == 0 && dev.frames) {
        diag_event("shader_compile: frame_sync=%llu", stat_sync_shader);
        pipeline_frame_stats(&stat_sync_pso, &stat_sync_pso_ns);
        extern uint64_t fx_epoch_now(void);
        diag_event("audit1: frame %llu | blend-factor14/15 draws %llu, stale blend colour %llu | stencil-state calls %llu, ds-presence mismatch %llu | glass draws %llu, stale (middle edit) %llu, rebased %llu | sampler builds past 64: %llu | MRT1-3 draws %llu | fx epoch %llu | sync PSO builds %llu (%.1f ms) grass %llu (%.1f ms)",
                   (unsigned long long)dev.frames, ad1.bf_draws, ad1.bc_stale, ad1.ds_calls, ad1.ds_mismatch, ad1.glass_draws, ad1.glass_stale_mid, ad1.glass_rebased,
                   ad1.samp_miss_full, ad1.mrt_draws, (unsigned long long)fx_epoch_now(), stat_sync_pso, stat_sync_pso_ns / 1e6, stat_sync_grass, stat_sync_grass_ns / 1e6);
        diag_event("samplers: mipmapped texture binds in 600 frames: anisotropic %llu (16x %llu), linear %llu, point %llu; mip filter below linear %llu; LOD bias set %llu",
                   stat_minfilt[2], stat_aniso16, stat_minfilt[1], stat_minfilt[0], stat_mipbelow, stat_lodbias);
        memset(stat_minfilt, 0, sizeof stat_minfilt); stat_aniso16 = stat_mipbelow = stat_lodbias = 0;
    }
    if (dev.frames % 300 == 0 && dev.frames)
        fprintf(stderr, "boot: render: %llu draws, %llu fixed-function (synthesized shaders), %llu fixed-function skipped, %llu deferred (shader compiling), %llu failed, %d shaders, %lu pipelines, %llu renamed\n",
                (unsigned long long)stat_draws, (unsigned long long)stat_ff_drawn, (unsigned long long)stat_ff_skipped, (unsigned long long)stat_deferred, (unsigned long long)stat_fail, nshaders, (unsigned long)pipelines.n, (unsigned long long)stat_renamed);
}
