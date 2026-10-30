// render_internal.h - state shared by render.m and the files split from it (render_target.m, render_query.m,
// render_present.m, render_post.m). Not a public interface: host.h has that. Objects stay file-scope in render.m
// (they were `static` there); this header names the ones the other files use.
#pragma once
#import <ImageIO/ImageIO.h>
#import <CoreServices/CoreServices.h>
#include <pthread.h>
#include <stdlib.h>
#include <sys/stat.h>

#include "../host/shaders/sm_translate.h"
#include "materials.h"
#include "d3d9_internal.h"
#include "pipeline_cache.h"

// ---------------- GPU-side objects ----------------
typedef struct {
    id<MTLTexture> tex;
    uint32_t w, h, fmt;
    int rt;                      // created as a render target
    int64_t bytes;               // allocatedSize, for the live counts in stats.log
    uint64_t used;               // serial of the last frame that used it (see frames in flight)
    id<MTLTexture> ms;           // multisampled surfaces: what draws render into; tex is its resolved copy (see MSAA)
    int stale;                   // ms was rendered to since tex was last resolved from it
    int resolve_pass;            // a multisampled texture (render_target_msaa): every pass resolves ms into tex level 0
    uint64_t read_serial;        // msaa_resolve calls (reads of tex): the frame counted, its count, the previous frame's
    uint32_t reads, reads_prev;
} RenderTex;


static int is_depth_pf(MTLPixelFormat f) { return f == MTLPixelFormatDepth32Float_Stencil8 || f == MTLPixelFormatDepth32Float; }

// ---------------- shared state (defined in render.m unless noted) ----------------
extern id<MTLTexture> backbuffer_tex, present_tex, autodepth_tex;
extern RenderTex bb_rt, ad_rt;               // the back buffer and auto depth buffer as render targets (.ms with AA on)
float render_hdr_headroom(void);            // frame-latched ceiling; Off returns 1
float render_hdr_composition_headroom(void); // >1 only while composing a float world/HUD frame
void render_hdr_world_copy(id<MTLTexture> src, id<MTLTexture> dst);
void render_float_scene_present_end(void);
void render_hdr_scene_treated(void);        // host post already applied the complete world shoulder
id<MTLTexture> render_lamp_source(void);      // current frame only; resolves selective flare replay
id<MTLTexture> render_lamp_source_view(uint32_t view); // independently valid IDs 1/2; full atlas, post crops locally
extern id<MTLCommandBuffer> cmd;
// render_thread.m: NFS_RENDER_THREAD=1 records command buffers and replays them on a render thread
id<MTLCommandBuffer> rt_command_buffer(void);
void rt_after_commit(id<MTLCommandBuffer> cb, dispatch_block_t blk);
extern uint8_t enc_resolve[5];               // render_target.m: open encoder's MSAA attachments that resolve on store (ms_attach)
// gpupass.m: BOOT_DIAG_GPUPASS=1 per-encoder GPU timestamps (gpupass.log); gpt_on 0 = nothing recorded
extern int gpt_on;
extern const char *gpt_phase;
void gpt_init(void);
void gpt_render(MTLRenderPassDescriptor *rp, const char *fmt, ...);
void gpt_render_auto(MTLRenderPassDescriptor *rp, const char *what);
void gpt_name(id<MTLCommandEncoder> e);
id<MTLBlitCommandEncoder> gpt_blit(id<MTLCommandBuffer> cb, const char *fmt, ...);
void gpt_frame(id<MTLCommandBuffer> cb, uint64_t serial);
int gpt_split_due(void);
void gpt_draw_note(const char *fmt, ...);
extern id<MTLRenderCommandEncoder> enc;
extern id<MTLBuffer> vis_buf;                // occlusion query counts (render_query.m)
#define VIS_SLOTS 4096u
extern int pass_dirty;
extern uint64_t frame_serial;                // the frame being encoded
int in_flight(uint64_t used);
void wait_serial(uint64_t s);
extern uint64_t done_serial;                 // the last frame whose GPU work completed
extern pthread_mutex_t done_mu;
extern pthread_cond_t done_cv;
extern uint32_t ring_off, ring_idx;          // the upload ring (render.m): Present moves to the next one
void ensure_cmd(void);
void end_pass(void);
FILE *dump_file(void);
RenderTex *gpu_tex(Obj *t);
extern int render_static_made;
void render_ripple_warm(void);
RenderTex *surface_rt(uint32_t s);
id<MTLTexture> surface_target(uint32_t s, NSUInteger *level, NSUInteger *slice);
void quad_into(id<MTLCommandBuffer> cb, id<MTLTexture> src, MTLRegion sr, id<MTLTexture> dst, NSUInteger level, NSUInteger slice, MTLRegion dr, int linear);
void blit_quad(id<MTLTexture> src, MTLRegion sr, id<MTLTexture> dst, NSUInteger level, NSUInteger slice, MTLRegion dr, int linear);
void render_stats_log(void);
int texdump_frame(void);
void texdump(uint32_t guest, id<MTLTexture> src);
void save_png(id<MTLTexture> t, const char *path, int keep_alpha);
// render_target.m (enc_ms, enc_samples, q_active: render.m)
extern RenderTex *enc_ms[5];                 // multisampled attachments of the open encoder (colour 0-3, depth 4)
extern int64_t msaa_bytes;
id<MTLTexture> new_ms_texture(MTLPixelFormat pf, NSUInteger w, NSUInteger h, uint32_t samples);
void drop_ms_texture(RenderTex *r);
extern int resolve_magenta;
void msaa_forget(RenderTex *r);
void msaa_resolve(RenderTex *r);
void ms_attach(MTLRenderPassAttachmentDescriptor *a, RenderTex *r, int slot);
int render_soft_depth_store(void); // 0 original; 1 nearest resolve on store; 2 sampled resolved depth (no resolve attachment)
id<MTLTexture> render_soft_smoke_layer(void);
void render_soft_smoke_layer_applied(void);
void render_soft_smoke_late_finish(id<MTLTexture> scene);
id<MTLTexture> pass_depth(id<MTLTexture> rt0, id<MTLTexture> ds);
// render_query.m
extern uint32_t enc_samples;                 // the open encoder's sample count (occlusion counts are in samples)
extern uint32_t q_active;                    // the occlusion query between Issue(BEGIN) and Issue(END) (guest handle)
void query_segment(void);
void query_stats_log(void);
extern float q_bb[4];                        // diag: the last counted fan's x/y range (render.m writes it)

void render_post_prewarm(void);
