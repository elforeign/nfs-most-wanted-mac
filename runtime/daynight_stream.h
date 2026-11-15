// Optional linear lamp colours for original static world buffers.
#pragma once
#include "../host/shaders/sm_translate.h"
typedef char *(*DaynightStreamProfile)(const uint32_t *, uint32_t, const SmResult *, int *, int);
char *daynight_stream_shader(const uint32_t *tokens, uint32_t n, const SmResult *refl, int *id, DaynightStreamProfile profile);
#ifdef __OBJC__
#import <Metal/Metal.h>
#include "d3d9_internal.h"
void daynight_stream_bind(id<MTLRenderCommandEncoder> encoder, id<MTLDevice> device,
                         Obj *vb, id<MTLBuffer> identity, uint32_t stride, uint32_t offset,
                         uint64_t shader_hash, uint64_t frame);
#endif
