// Internal post-pass cuts. The legacy shader source remains unchanged.
#pragma once
#include <stdint.h>
int post_cuts_enabled(void);
int post_cuts_full_target(int split, unsigned level, unsigned slice,
                          uint32_t width, uint32_t height, uint32_t out_width, uint32_t out_height);
int post_cuts_curve(uint32_t width, uint32_t height, int split);
enum { POST_CUTS_AO_DEPTH = 1, POST_CUTS_AO_MS = 2 };
unsigned post_cuts_ao(int proof, int ordinary_depth, int reuse, int empty);
const char *post_cuts_curve_source(void);
#ifdef __OBJC__
#import <Metal/Metal.h>
id<MTLRenderPipelineState> post_cuts_curve_pipeline(id<MTLDevice> device, MTLPixelFormat format);
#endif
