// Offline X360 L2RA scenery contexts. No guest pointers or streaming state.
#pragma once
#include <stdint.h>

typedef struct AuxLight {
    float position[3];
    float radius;
    float rgb[3];
    float intensity;
} AuxLight;

typedef struct AuxLightContext {
    char name[35];                    // source name[34], plus a host terminator
    uint8_t count;                    // min(source_count, 8), retail upload cap
    uint8_t source_count;             // retain truncation information
    AuxLight lights[8];               // original source space, no basis conversion
} AuxLightContext;

// NFS_AUX_LIGHTS=pc|x360, default PC; F10 may change it live.
int auxlights_enabled(void);
void auxlights_set_enabled(int enabled);
// Verify an invertible row-vector instance transform against the matched world bbox,
// then copy world-space imported lights into mesh-local space. No guest memory.
// Returns 0 on invalid transform/bbox/space, 1 identity, 2 transformed.
int auxlights_local(const AuxLightContext *source, const float world[4][4],
                    const float mesh_bbox[6], const float instance_bbox[6], AuxLightContext *out);
// Exact section id + the instance's original first 24 bytes. Returns NULL in pc
// mode or for an unmatched/ambiguous key. Data has process lifetime. Caller must
// identify the currently loaded section and copy lights into queued descriptors;
// never retain a guest instance pointer across unload/load generations. All keys
// currently belong to L2RA; a future track import must add a track namespace.
const AuxLightContext *auxlights_find(uint32_t section_id, const uint8_t bbox[24]);
