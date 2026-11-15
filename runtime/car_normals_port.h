#pragma once
#include <stdint.h>

// Port-authored car normal maps (NFS_CAR_NORMALMAPS=port) for the four families whose `_N` maps only the console ships:
// 0 TREAD, 1 TREADR, 2 DOORLINE, 3 DAMAGE0 (the order of materials.m's carnm_x360[]). Generated from the player's PC files
// (tools_snapshot/car_normals_port.py -> gen/port_car_normals.inc); DAMAGE0 has no map (its scuffs fall below one 8-bit step).
typedef struct { const char *name; uint16_t w, h; const int16_t *xy; } CarNormalsPortMap;   // snorm (x, y), z >= 0 implied
int car_normals_port_has(int family);                 // 1 when the generated table holds this family's map
#ifdef __OBJC__
#import <Metal/Metal.h>
// The family's RGBA16Float map, (n + 1) / 2 with normal-aware mips (each level the renormalised mean of the one above);
// NFS_TEST_CARNM_FLAT=1 (test only) gives every family a flat 1x1 map, the visual negative control under the same shader.
id<MTLTexture> car_normals_port_texture(id<MTLDevice> device, int family);
#endif
