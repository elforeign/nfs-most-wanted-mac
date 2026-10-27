// R20: immutable source data, exact lookup and checked world -> mesh conversion.
#include "auxlights.h"
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

typedef struct AuxLightKey {
    uint32_t section_id;
    uint8_t bbox[24];
    uint16_t context_index;
} AuxLightKey;

#include "gen/x360_shader_lits.inc"   // X360_SHADER_LITS (tools/gen_tables.py): the aux-light shader's constants exist
#include "gen/auxlights_data.inc"   // tools/gen_tables.py, from the player's files

_Static_assert(sizeof(float) == 4, "aux lights require IEEE 32-bit floats");
_Static_assert(sizeof(AuxLight) == 32, "aux light source rows must remain contiguous");
_Static_assert(sizeof(aux_keys) / sizeof(aux_keys[0]) == AUX_DATA_ROWS, "aux key count");
_Static_assert(sizeof(aux_contexts) / sizeof(aux_contexts[0]) == AUX_DATA_CONTEXTS, "aux context count");

static int enabled = -1;

// Lazy: settings_init (which carries a saved PC choice here as NFS_AUX_LIGHTS=pc) runs after the constructors. X360 by default.
static void auxlights_init(void) {
    const char *mode = getenv("NFS_AUX_LIGHTS");
    enabled = !(mode && !strcmp(mode, "pc")) && X360_SHADER_LITS;   // 0: no X360 ZZDATA0.BIN, the aux shader's constants
    if (getenv("BOOT_DIAG")) {
        fprintf(stderr, "auxlights: mode=%s contexts=%u source_lights=%u keys=%u "
                "lit_instances=%u matched=%u unmatched=%u collisions=%u invalid=%u\n",
                enabled ? "x360" : "pc", AUX_DATA_CONTEXTS, AUX_DATA_LIGHTS, AUX_DATA_ROWS,
                AUX_DATA_LIT_INSTANCES, AUX_DATA_MATCHED, AUX_DATA_UNMATCHED,
                AUX_DATA_COLLISIONS, AUX_DATA_INVALID);
    }
}

int auxlights_enabled(void) { if (enabled < 0) auxlights_init(); return enabled; }
void auxlights_set_enabled(int value) { enabled = value != 0; }

int auxlights_local(const AuxLightContext *source, const float w[4][4],
                    const float mesh[6], const float bbox[6], AuxLightContext *out) {
    if (!source || !source->count || source->count > 8) return 0;
    for (int r = 0; r < 4; ++r) for (int c = 0; c < 4; ++c) if (!isfinite(w[r][c])) return 0;
    for (int i = 0; i < 6; ++i) if (!isfinite(mesh[i]) || !isfinite(bbox[i])) return 0;
    if (fabsf(w[3][3]-1) > .001f || fabsf(w[0][3])+fabsf(w[1][3])+fabsf(w[2][3]) > .001f) return 0;
    // Source and PC instance matrices also contain scale. A transpose would
    // misplace 762 matched references; use the full affine inverse. Attenuation
    // stays in source/PC mesh-local units, exactly as the retail shader uses it.
    float inverse[3][3];
    for (int r = 0; r < 3; ++r) for (int c = 0; c < 3; ++c) {
        int r1 = (c+1)%3, r2 = (c+2)%3, c1 = (r+1)%3, c2 = (r+2)%3;
        inverse[r][c] = w[r1][c1]*w[r2][c2]-w[r1][c2]*w[r2][c1];
    }
    float determinant = w[0][0]*inverse[0][0]+w[0][1]*inverse[1][0]+w[0][2]*inverse[2][0];
    if (!isfinite(determinant) || fabsf(determinant) < 1e-8f) return 0;
    for (int r = 0; r < 3; ++r) for (int c = 0; c < 3; ++c) inverse[r][c] /= determinant;
    float lo[3] = {INFINITY,INFINITY,INFINITY}, hi[3] = {-INFINITY,-INFINITY,-INFINITY};
    for (int i = 0; i < 8; ++i) for (int c = 0; c < 3; ++c) {
        float v = w[3][c];
        for (int r = 0; r < 3; ++r) v += mesh[r+((i>>r)&1)*3]*w[r][c];
        lo[c] = fminf(lo[c],v); hi[c] = fmaxf(hi[c],v);
    }
    for (int c = 0; c < 3; ++c) {
        if (mesh[c] > mesh[c+3] || bbox[c] > bbox[c+3]) return 0;
        // LOD mesh bounds can be smaller than the scenery instance bounds.
        float tolerance = 2.f + .05f*(bbox[c+3]-bbox[c]);
        if (lo[c] < bbox[c]-tolerance || hi[c] > bbox[c+3]+tolerance ||
            hi[c] < bbox[c]-tolerance || lo[c] > bbox[c+3]+tolerance) return 0;
    }
    *out = *source;
    for (int i = 0; i < source->count; ++i) {
        const AuxLight *l = &source->lights[i];
        float distance2 = 0;
        if (!isfinite(l->radius) || l->radius <= 0 || !isfinite(l->intensity) || l->intensity <= 0) return 0;
        for (int c = 0; c < 3; ++c) {
            if (!isfinite(l->position[c]) || !isfinite(l->rgb[c])) return 0;
            float d = fmaxf(bbox[c]-l->position[c], fmaxf(0,l->position[c]-bbox[c+3]));
            distance2 += d*d;
            float v = 0;
            for (int k = 0; k < 3; ++k) v += (l->position[k]-w[3][k])*inverse[k][c];
            if (!isfinite(v)) return 0;
            out->lights[i].position[c] = v;
        }
        // Prerequisite space check; never turn terrain absolute coordinates into
        // another world transform. This is a validation bound, not a PS cutoff.
        if (distance2 > (4*l->radius)*(4*l->radius)) return 0;
    }
    int identity = 1;
    for (int r = 0; r < 4; ++r) for (int c = 0; c < 4; ++c) identity &= fabsf(w[r][c]-(r == c)) < .00001f;
    return identity ? 1 : 2;
}

const AuxLightContext *auxlights_find(uint32_t section_id, const uint8_t bbox[24]) {
    if (!auxlights_enabled() || !bbox) return NULL;
    size_t low = 0, high = AUX_DATA_ROWS;
    while (low < high) {
        size_t mid = low + (high - low) / 2;
        const AuxLightKey *key = &aux_keys[mid];
        int cmp = section_id < key->section_id ? -1 : section_id > key->section_id ? 1 :
                  memcmp(bbox, key->bbox, 24);
        if (cmp < 0) high = mid;
        else if (cmp > 0) low = mid + 1;
        else return &aux_contexts[key->context_index];
    }
    return NULL;
}
