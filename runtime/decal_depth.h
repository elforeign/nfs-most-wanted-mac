// decal_depth.h - push coplanar background glow layers behind the surface in front of them (decal_depth.m).
#pragma once
#include <stdint.h>

// TextureInfoPlatInterface::Init hook (x360_textures.m): tags the D3D texture of a listed TextureInfo.
void decal_depth_texture(uint32_t ti, uint32_t pi);
// render.m, per draw while enabled: does stage 0 hold a tagged texture; then adds the push-back to the D3D bias/slope.
int decal_depth_tagged(uint32_t tex0);
// render.m, soft particles: stage 0 holds the car ground shadow, a decal on the road that must not be depth-faded.
int decal_depth_ground(uint32_t tex0);
void decal_depth_offset(float *bias, float *slope);
