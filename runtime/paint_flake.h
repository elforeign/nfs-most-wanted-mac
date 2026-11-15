#pragma once
#include <stdint.h>

enum { PAINT_FLAKE_BYTES = 149796 };
// Linear RGBA8, tight 32^3 through 1^3 mips. Independent of grass resources.
const uint8_t *paint_flake_volume(uint32_t *bytes);
const uint8_t *paint_flake_noise(uint32_t *width, uint32_t *height);
float paint_flake_fade_b(void);
