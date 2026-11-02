#pragma once
#include <stdint.h>
/* Optional cosmetics only: these functions never write guest memory. */
void underglow_profile_default(uint32_t profile);
void underglow_profile_load(uint32_t profile, uint32_t buffer, uint32_t size);
void underglow_save_begin(void);
void underglow_profile_save(uint32_t profile, uint32_t buffer, uint32_t size);
void underglow_save_complete(int committed);
/* Explicit owned-car API for later controls/rendering. Invalid context is Off. */
unsigned underglow_get(uint32_t profile, uint32_t handle);
int underglow_set(uint32_t profile, uint32_t handle, unsigned preset);
