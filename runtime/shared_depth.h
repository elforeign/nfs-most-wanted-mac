// Spatial pre-treatment data, owned independently by SP, split P0 and split P1.
// Mirror views deliberately receive no set. Textures are read-only to consumers.
// At 3840x2160 per view: RGBA32F normal.xyz/view-depth.w = 33.1776 MB;
// RG32F min/max chain (1920x1080 through 1x1, ceil halves) = 22.119216 MB.
// Total ~55.30 MB (52.74 MiB), excluding allocator alignment. Resolved full depth
// is borrowed (0 additional MB); existing AO targets are unchanged. Float32
// normals preserve the original AO arithmetic without quantization. No history.
#pragma once
#import <Metal/Metal.h>
#include <stdint.h>

enum { SHARED_DEPTH_VIEWS=3, SHARED_DEPTH_LEVELS=16 };
typedef struct {
    float proj[4], size[4], rect[4], bounds[4]; // same local UV/projection convention as AO
} SharedDepthParams;
typedef struct {
    uint64_t frame;
    uint32_t width, height, samples, hdr, generation, mode, camera, player;
    SharedDepthParams params;
} SharedDepthInput;
typedef struct {
    id<MTLTexture> resolved;       // full atlas; params.rect/bounds select this view
    id<MTLTexture> depth_normal;   // half local resolution: camera-facing unit normal.xyz, linear +Z.w
    id<MTLTexture> minmax[SHARED_DEPTH_LEVELS]; // linear +Z bounds, sky included at far
    unsigned levels;
    uint64_t frame, epoch;
    SharedDepthParams params;
} SharedDepthSet;

// A future consumer requests before the pre-treatment hook, then reads after it.
// Requests expire each frame. AO passes its live enabled state directly to prepare.
void shared_depth_request(unsigned view, uint64_t frame);
const SharedDepthSet *shared_depth_get(unsigned view, uint64_t frame);
const SharedDepthSet *shared_depth_prepare(id<MTLDevice> device, id<MTLCommandBuffer> cb,
    unsigned view, id<MTLTexture> resolved, const SharedDepthInput *input, int ao_on);

#include "host.h"
void render_shared_depth_pre_treatment(id<MTLTexture> scene, id<MTLTexture> resolved,
    unsigned samples, unsigned hdr, const X360PostParams *params, const X360PostView *view, unsigned mode);
