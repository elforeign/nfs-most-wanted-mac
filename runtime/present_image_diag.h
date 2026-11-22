#pragma once
#include <stdint.h>

void present_image_diag_tick(uint32_t ticks, uint32_t flow);
void present_image_diag_second(uint64_t frame, uint32_t flow);

#ifdef __OBJC__
@class CAMetalLayer;
void present_image_diag_init(CAMetalLayer *layer);
#endif
