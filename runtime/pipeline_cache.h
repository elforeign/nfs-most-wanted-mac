// Loading-time replay of observed Metal descriptor recipes. No binary persistence.
#pragma once
#import <Metal/Metal.h>
#include <stdint.h>

void pipeline_register_shader(uint64_t key, id<MTLFunction> function);
void pipeline_prewarm(dispatch_queue_t __strong queues[4], NSUInteger scene_samples);
void pipeline_frame_stats(unsigned long long *count, unsigned long long *ns);
void pipeline_loading_done(void);
void pipeline_diag_tick(void);
// The presentation queue also performs frame-critical object creation.
void pipeline_frame_scope(int active);
id<MTLRenderPipelineState> pipeline_cached(MTLRenderPipelineDescriptor *descriptor,
    uint64_t vertex, uint64_t fragment, const char *pass, NSError **error);
id<MTLRenderPipelineState> pipeline_build(MTLRenderPipelineDescriptor *descriptor, const char *pass, NSError **error);
id<MTLRenderPipelineState> pipeline_build_device(id<MTLDevice> device,
    MTLRenderPipelineDescriptor *descriptor, const char *pass, NSError **error);
