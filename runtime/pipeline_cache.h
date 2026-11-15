// Background replay of observed Metal descriptors and of a per-user history of demand builds.
#pragma once
#import <Metal/Metal.h>
#include <stdint.h>

void pipeline_register_shader(uint64_t key, id<MTLFunction> function);
// USERDATA directory of the demand-build history; call before pipeline_prewarm. Never called: no history.
void pipeline_history_open(const char *dir);
// Call after shader publication; only enqueues work and must not be joined by frames.
void pipeline_prewarm(dispatch_queue_t __strong queues[4], NSUInteger scene_samples);
void pipeline_frame_stats(unsigned long long *count, unsigned long long *ns);
void pipeline_loading_done(void);
void pipeline_diag_tick(void);
// The presentation queue also performs frame-critical object creation.
void pipeline_frame_scope(int active);
id<MTLLibrary> pipeline_library_source(NSString *source, const char *pass, NSError **error);
id<MTLRenderPipelineState> pipeline_cached(MTLRenderPipelineDescriptor *descriptor,
    uint64_t vertex, uint64_t fragment, const char *pass, NSError **error);
// Embedded libraries have no translated shader keys. Retain their identity for this load.
id<MTLRenderPipelineState> pipeline_cached_library(MTLRenderPipelineDescriptor *descriptor,
    id<MTLLibrary> library, const char *pass, NSError **error);
id<MTLRenderPipelineState> pipeline_build(MTLRenderPipelineDescriptor *descriptor, const char *pass, NSError **error);
id<MTLRenderPipelineState> pipeline_build_device(id<MTLDevice> device,
    MTLRenderPipelineDescriptor *descriptor, const char *pass, NSError **error);
