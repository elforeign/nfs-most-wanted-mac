#pragma once
#include <stddef.h>
#include <stdint.h>
#define UG_MAX_CARS 200
#define UG_MAX_SNAPSHOTS 16
#define UG_SAVE_BYTES 0xF848u
/* Stable retail identity: row handle, frontend/pvehicle keys, customization
 * handle, ownership kind. Customization contents may change without a new car. */
typedef struct {
    uint32_t handle, frontend, vehicle, customization, kind;
} UGIdentity;
typedef struct { UGIdentity car; uint32_t preset; } UGEntry;
typedef struct {
    unsigned char digest[32];
    uint32_t count;
    UGEntry entries[UG_MAX_CARS];
} UGSnapshot;
int ug_identity_equal(const UGIdentity *a, const UGIdentity *b);
int ug_save_digest(const void *bytes, size_t size, unsigned char digest[32]);
/* Caller supplies a directory; only underglow.bin and its sibling temporary
 * are accessed. Rejection clears output; cosmetics never gate retail saves. */
int ug_store_load(const char *directory, const unsigned char digest[32], UGSnapshot *out);
int ug_store_publish(const char *directory, const UGSnapshot *snapshot);
