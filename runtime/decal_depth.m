// decal_depth.m - glow layers drawn coplanar behind an opaque sign (always),
// and the car ground shadow kept out of the soft-particle depth fade (always).
//
// The Bank of Rockport sign (opaque, z-write on, LESSEQUAL) and the LED panel behind it (additive SRCALPHA/ONE,
// z-write off, LESSEQUAL, drawn after the sign) lie in one plane within depth resolution, with different world
// matrices: per-vertex rounding decides LESSEQUAL, so the LED shows through the whole sign when still and flips on and
// off with a few centimetres of camera movement. Retail PC draws them the same way.
// Retail marks no "background layer": FUN_006c68b0 turns z-write off for every non-zero TextureInfo blend type (+0x56,
// 2 = additive), which also covers additive decals meant to lie ON a surface; pushing those back would hide them. So
// the push-back is keyed on the panel's texture name hashes, tagged on the D3D texture when Init creates it.
#include "decal_depth.h"
#include "render_internal.h"

// Push-back in D3D depth-bias units of 2^-24 (render.m scales by 2^24), plus a slope term for oblique views.
enum { BACK_UNITS = 16 };
static const float kBackSlope = 1.0f;

// SGN_NEONROLLING_A_LL (128x128 DXT3, blend type 2): the LED dots behind SGN_BANKOFROCKPORT_EA.
static const uint32_t kBackHashes[] = {0x452ECD84u};
// CARSHADOW: the car's ground shadow. CarRenderInfo::DrawAmbientShadow 00743EA0 draws it as an exBeginStrip strip on
// the road, which reaches the particle pixel shader; the soft-particle depth fade (0.6 m against the opaque depth)
// would fade a strip lying on the road to nothing, so it stays a plain decal.
static const uint32_t kGroundHash = 0xE43D65FBu;

void decal_depth_texture(uint32_t ti, uint32_t pi) {
    if (!pi) return;
    uint32_t hash = M32(ti + 0x24);
    Obj *o = obj(M32(pi + 0x18));
    if (!o) return;
    o->decal_back = 0;
    o->ground_decal = hash == kGroundHash;
    for (unsigned i = 0; i < sizeof kBackHashes / sizeof *kBackHashes; ++i) if (kBackHashes[i] == hash) o->decal_back = 1;
    if (diag_on && o->ground_decal) diag_event("decal_depth: ground shadow %08X %.24s tex %08X", hash, (const char *)G + ti + 0x0C, o->guest);
    if (diag_on && o->decal_back) diag_event("decal_depth: tagged %08X %.24s tex %08X", hash, (const char *)G + ti + 0x0C, o->guest);
}

int decal_depth_tagged(uint32_t tex0) {
    Obj *o = obj(tex0);
    return o && o->decal_back;
}

int decal_depth_ground(uint32_t tex0) {
    Obj *o = obj(tex0);
    return o && o->ground_decal;
}

void decal_depth_offset(float *bias, float *slope) {
    *bias += BACK_UNITS / 16777216.0f;
    *slope += kBackSlope;
}
