// Opt-in native-resolution motion. No temporal consumer is enabled here.
#pragma once
#import <Metal/Metal.h>
#include "temporal_state.h"
#include "../host/shaders/sm_translate.h"

typedef struct {
    id<MTLTexture> raster, resolved, coverage, depth, source;
    id objects[TEMPORAL_MIRROR];
    SmTemporalAux views[TEMPORAL_MIRROR];
    unsigned view_mask;
    uint64_t drawn_instances[TEMPORAL_MIRROR]; // BOOT_DIAG only
    uint64_t frame, generation, cleared_frame;
} TemporalMotionTarget;
void temporal_render_request(int enabled);
TemporalMotionTarget *temporal_render_prepare(id<MTLDevice> device,
    id<MTLTexture> colour, id<MTLTexture> depth, uint64_t frame);
int temporal_render_constants(const TemporalViewState *view, const TemporalPartPose *pose,
    int rigid_supported, const float raster[2], SmTemporalAux *out);
// Resolve/stage pose history before this test. Skip only when the additional
// object displacement is bounded by 1/16 pixel per axis in the visible frustum.
int temporal_render_moved(const TemporalViewState *view, const TemporalPartPose *pose,
    const uint32_t viewport[4]);
void temporal_render_view(TemporalMotionTarget *target, TemporalViewId view,
    const TemporalViewState *state, const uint32_t viewport[4]);
// An objects-only PSO: RG16F + R8 coverage, one sample, no scene attachments.
int temporal_render_pipeline(id<MTLDevice> device, MTLRenderPipelineDescriptor *pipeline,
    NSString *vertex, NSString *fragment, int scene_samples);
void temporal_render_bind(id<MTLRenderCommandEncoder> encoder, const SmTemporalAux *aux);
// Forward the real draw unchanged and retain its object replay's bindings.
// Caller restores the real encoder and invalidates its binding cache afterward.
id<MTLRenderCommandEncoder> temporal_render_record(id<MTLRenderCommandEncoder> real,
    TemporalMotionTarget *target, TemporalViewId view, id<MTLRenderPipelineState> motion,
    const SmTemporalAux *aux);
// Shared camera/selection shader source for TAA (and later consumers). Include
// it in the consumer library; bind views[view] as TemporalCameraAux. It reads
// resolved raw depth only where coverage is zero. Motion is previous-current UV.
const char *temporal_render_camera_msl(void);
int temporal_render_objects(id<MTLCommandBuffer> cb, TemporalMotionTarget *target,
    id<MTLTexture> resolved_depth, TemporalViewId view);
// After scene MSAA resolve: clear coverage and replay moving objects
// once at pixel centres against resolved depth. Camera motion is evaluated
// inside the consumer; uncovered vectors are undefined. Returning the object
// atlas requires no motion resolve.
id<MTLTexture> temporal_render_resolve(id<MTLDevice> device, id<MTLCommandBuffer> cb,
    TemporalMotionTarget *target, id<MTLTexture> resolved_depth, TemporalViewId view);
#ifndef TEMPORAL_RENDER_TEST
int temporal_render_scene(id<MTLTexture> colour, id<MTLTexture> depth, uint64_t frame,
    const uint32_t viewport[4], int eligible);
extern TemporalMotionTarget *temporal_draw_target;
extern TemporalViewId temporal_draw_view;
#else
id<MTLLibrary> temporal_render_test_library(id<MTLDevice> device, NSString *source, NSError **error);
#endif
