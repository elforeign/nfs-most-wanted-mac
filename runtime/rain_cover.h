/* Local precipitation shelter. Heights and positions use game world metres. */
#pragma once
#include <stdint.h>
#include "runtime.h"
#include "../host/shaders/sm_translate.h"

enum { RAIN_COVER_SIZE = 512 };
#define RAIN_COVER_SPAN 128.0f
#define RAIN_COVER_CLEARANCE 0.35f
/* Particle reach plus streak room; scheduling separately reserves build travel. */
#define RAIN_COVER_REACH 20.0f
/* Standalone synchronous core refresh threshold. */
#define RAIN_COVER_MARGIN 32.0f
typedef struct RainCoverMap {
    float *height;
    float x, y;
    uint64_t frame, generation, allocations, triangles;
    uint32_t view, camera, world_detail;
    int valid;
} RainCoverMap;
int rain_cover_begin(RainCoverMap *map, float x, float y, uint64_t frame);
void rain_cover_triangle(RainCoverMap *map, const float p[3][3]);
int rain_cover_sample(const RainCoverMap *map, float x, float y, float z);
extern int rain_cover_enabled; // -1 until the first switch read; 0 is the hot Off path
int rain_cover_on(void);
void rain_cover_set(int on);
void rain_cover_render(Cpu *c, void (*render)(Cpu *));
int rain_cover_curtain(void);
int rain_cover_lens(uint32_t rain);
void rain_cover_constants(float out[4]);
enum { MAT_VARIANT_RAIN_COVER = 0x110, MAT_VARIANT_RAIN_COVER_FILM = 0x111 };
int rain_cover_ripple(uint64_t pixel_hash);
char *rain_cover_shader(const uint32_t *tokens, uint32_t n, const SmResult *refl, int *id, int film);
#ifdef __OBJC__
#import <Metal/Metal.h>
id<MTLTexture> rain_cover_texture(void);
void rain_cover_bind(id<MTLRenderCommandEncoder> encoder, const SmResult *vs);
#endif
