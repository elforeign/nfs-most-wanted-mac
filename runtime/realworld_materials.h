#pragma once
#include <stdint.h>
#include "materials.h"
enum { MAT_VARIANT_REAL_GLASS = 0x10a, MAT_VARIANT_REAL_CHROME = 0x10b };
enum { MAT_REAL_T0 = 0x200, MAT_REAL_SHADOWED = 0x201 };
void realworld_materials_constants(int id, const float (*vsc)[4], MatConsts *out);
void realworld_materials_set(int mode);
int realworld_materials_mode(void);
void realworld_materials_capture(uint32_t hash, int car, uint32_t flags);
void realworld_materials_texture(uint32_t texture);
void realworld_materials_lamp_texture(uint32_t texture);   // a lamp-state texture name hash (keeps the retail shader on chrome)
void realworld_materials_rim_texture(uint32_t texture, int painted, uint32_t mask, int paletted);
uint32_t realworld_materials_rim_mask(void);
void realworld_materials_set_hold(void (*add)(uint32_t), void (*release)(uint32_t));   // reference hooks for stored rim masks
int realworld_materials_draw(uint64_t ps, uint64_t vs);
int realworld_materials_window_mask(void);
int realworld_materials_role(uint32_t hash, int car, int mode, uint32_t flags);
// Returns an allocated VLT array for the supplied vertex range; caller frees it.
float *realworld_materials_panes(const void *vertices, uint32_t nv, uint32_t stride, uint32_t position,
                               uint32_t normal, const void *indices, uint32_t ni, uint32_t isz, uint32_t first, int mask);
char *realworld_materials_vertex(const char *source, int unshadowed, int chrome);
char *realworld_materials_pixel(const char *source, int variant, int shadow, int chrome);
char *mat_realworld_shader(const uint32_t *tokens, uint32_t n, const SmResult *refl, int *id, int chrome);
