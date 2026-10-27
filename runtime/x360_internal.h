// x360_internal.h - what the X360 renderer features (x360.m, x360_post.m, x360_textures.m) share.
#pragma once
#include <string.h>

#include "d3d9_internal.h"

#define GAMEFLOW_STATE     0x00925E90u   // 3 front end, 6 racing

static inline void put_float(uint32_t a, float v) { memcpy(G + a, &v, 4); }
static inline float F32(uint32_t a) { float f; memcpy(&f, G + a, 4); return f; }
