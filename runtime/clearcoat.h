// Optional lacquer layer over car paint; separate shader variants preserve Off.
#pragma once
#include <stddef.h>
#include <stdint.h>
#include "../host/shaders/sm_translate.h"

enum { MAT_VARIANT_CLEARCOAT = 0x10c };
int clearcoat_enabled(void);
int clearcoat_shader_hash(uint64_t hash);
int clearcoat_is_paint(const char *name, size_t size);
char *clearcoat_patch(const char *source, int enabled);
char *clearcoat_vertex(const char *source);
char *clearcoat_t0_patch(const char *source);
char *mat_clearcoat_shader(const uint32_t *tokens, uint32_t n, const SmResult *refl, int *id);
