/* Numeric renderer observations; no guest calls or GPU waits. */
#ifndef NFS_BLACKBOX_RENDER_H
#define NFS_BLACKBOX_RENDER_H
#include "blackbox.h"
typedef struct { uint16_t id, flags; uint32_t draws; } BlackboxPass;
typedef struct {
    uint64_t serial;
    uint32_t attempted, deferred, failed, unattributed, overflow, count;
    BlackboxPass pass[32];
} BlackboxRenderFrame;
enum {
    BB_RENDER_SUBMIT = 1, BB_RENDER_GPU, BB_PRESENT_GPU, BB_PRESENT_SHOWN,
    BB_PRESENT_MISSING, BB_HOST_ENCODER, BB_PRESENT_SUPPRESSED, BB_PRESENT_TICKET_LOST
};
enum {
    BB_HOST_POST = 1, BB_HOST_COPY, BB_HOST_RESOLVE, BB_HOST_FINAL,
    BB_HOST_COLOURFILTER, BB_HOST_CROP, BB_HOST_SSAO, BB_HOST_LUMINANCE,
    BB_HOST_TREATMENT, BB_HOST_LENS, BB_HOST_SMOKE, BB_HOST_DEPTH,
    BB_HOST_QUAD, BB_HOST_GAMMA, BB_HOST_HDR, BB_HOST_COVERAGE, BB_HOST_MOTION
};
uint16_t blackbox_render_pass_id(const char *effect, const char *technique, const char *pass);
void blackbox_render_pass_begin(uint16_t id);
void blackbox_render_pass_end(void);
/* Weak defaults let the core run without a renderer (e.g. asset-free fixtures). */
int blackbox_render_take(BlackboxRenderFrame *out);
void blackbox_render_definitions(FILE *f);
void blackbox_record_present(uint64_t serial, uint32_t stage, uint32_t status,
                             uint64_t a, uint64_t b, uint64_t c);
void blackbox_render_complete(uint64_t serial, uint32_t stage, uint32_t status,
                              double start, double end, int64_t error);
void blackbox_render_host(uint64_t serial, uint32_t id);
void blackbox_render_host_stage(uint64_t serial, uint32_t id, uint32_t detail);
void blackbox_render_present(uint64_t serial, uint32_t stage, uint32_t status,
                             double start, double end, double shown);
#ifdef __OBJC__
#import <Metal/Metal.h>
void blackbox_render_final(id<MTLCommandBuffer> buffer, uint64_t serial);
#endif
#endif
