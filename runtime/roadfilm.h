// Optional water Fresnel on the existing worldreflect contribution.
#pragma once
#include <stdint.h>
#include "../host/shaders/sm_translate.h"

enum { MAT_VARIANT_ROAD_FILM = 0x109 };
int roadfilm_enabled(void);
void roadfilm_set(int enabled);
int roadfilm_shader_hash(uint64_t hash);
int roadfilm_draw(uint64_t hash, float wetness);
char *roadfilm_patch(const char *source);
char *mat_roadfilm_shader(const uint32_t *tokens, uint32_t n, const SmResult *refl, int *id);
