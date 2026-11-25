// Live numeric shader registers, shared with the effect uploader without Metal types.
#pragma once
#include <stdint.h>

// CTAB register sets: 0 bool, 1 int4, 2 float4. False also means unknown device,
// intercepted setter or invalid range: the caller must use the device's COM method.
int d3d9_constants_equal(uint32_t device, int ps, uint32_t set, uint32_t start,
                         uint32_t count, const void *data);
