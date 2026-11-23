// Optional sky texture blend, solar sprite suppression and diagnostics.
#pragma once
#include <stdint.h>
#include "../host/shaders/sm_translate.h"

float daynight_sky_factor(float phase, float light_z, float tilt);
int daynight_sky_texture(uint32_t hash);
char *daynight_sky_patch(const char *source, int pass);
char *daynight_sky_patch_vertex(const char *source);
char *daynight_fog_shader(const char *source, const uint32_t *tokens, uint32_t count, int vertex);
void daynight_sky_moon(float phase, const float light[3], float azimuth, float out[3]);
char *daynight_sky_shader(const uint32_t *tokens, uint32_t n, const SmResult *refl);
#ifdef __OBJC__
#import <Metal/Metal.h>
typedef id<MTLSamplerState> (*DaynightSkySampler)(int stage, int shadow);
int daynight_sky_bind(id<MTLRenderCommandEncoder> encoder, id<MTLDevice> device, uint64_t hash, const char *source, uint64_t frame, id<MTLTexture> fallback, DaynightSkySampler fallback_sampler);
#endif
