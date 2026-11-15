#pragma once
#include <stdint.h>
#include "headlight_profile.h"
#include "../host/shaders/sm_translate.h"

enum { HL_RECEIVER = 0x10000, HL_CASTER = 0x20000, HL_VARIANTS = 0x30000,
       HL_VS_BUFFER = 23, /* 22 is the rain cover's world buffer */
       /* Fragment buffers: translator 0-3, materials 4-5, lamps/sky 6,
        * temporal 7, rain cover 8. Guest textures occupy 0-15 (including
        * material overrides); temporal depth occupies 31. */
       HL_PS_BUFFER = 9, HL_SHADOW_SLOT = 16, HL_MAP_SIZE = 1024 };
/* All matrices are columns, matching the translated shader constant contract. */
typedef struct {
    float position[4], right[4], up[4], forward[4], vp[4][4];
} HLGpuLamp;
typedef struct {
    HLGpuLamp lamps[2];
    float world[4][4], normal[3][4];
    float fog[4], local_eye[4];
    uint32_t frame[4]; /* current, prepared, valid, caster index */
} HLConstants;
typedef struct { uint64_t allocations, passes, caster_draws, receiver_draws, traversals; } HLCounters;
int headlights_enabled(void);
/* Shared admission contract. Wrong frame/view/target, blending, sky and glass fail closed. */
int headlights_admit(int enabled, uint64_t frame, uint64_t prepared, int valid,
                     unsigned view, int main_target, int opaque, int material);
int headlights_material(uint64_t pixel_hash); /* 1 world, 2 normal, 3 road, 4 car, 5 car t0 */
char *headlights_shader(const char *source, const SmResult *reflection, int caster, int material);
const char *headlights_msl(void);
void headlights_pack_lamp(const HLLamp *lamp, HLGpuLamp *out);
void headlights_counters(HLCounters *out);
#ifndef HEADLIGHTS_CORE_ONLY
#include "runtime.h"
void headlights_prepare(Cpu *cpu, uint32_t view, uint32_t flags);
int headlights_casting(void);
int headlights_draw(const SmResult *vs, uint64_t pixel_hash, int main_target, HLConstants *out);
void headlights_missed(void);
void headlights_drawn(int receiver);
#ifdef __OBJC__
#import <Metal/Metal.h>
id<MTLTexture> headlights_colour(void);
id<MTLTexture> headlights_depth(void);
id<MTLRenderCommandEncoder> headlights_encoder(id<MTLCommandBuffer> command);
void headlights_bind(id<MTLRenderCommandEncoder> encoder, const HLConstants *constants, int receiver);
#endif
#endif
