#ifndef NFS_PRIVATE_CARS_H
#define NFS_PRIVATE_CARS_H
#include <stddef.h>
/* Startup-only immutable inventory; WO2 supplies userdata root and settings.
 * Repeated init returns the first result without I/O. No routing/registration.
 * Pending IDs must never be compacted. Original swaps use uppercase MW names.
 * Failure is atomic: count=0 and an error. Public always returns 0 without I/O. */
typedef struct {
    unsigned id; /* fixed addition ID; zero for own-slot swap */
    int swap, selected;
    char name[32], donor[32], source[64], price_donor[32];
    char geometry[4096], textures[4096];
} PrivateCar;
int private_cars_init(const char *root, int enabled, const char *const *original_swaps, size_t original_count);
size_t private_cars_count(void);
const PrivateCar *private_cars_get(size_t selected_index);
size_t private_cars_inventory_count(void);
const PrivateCar *private_cars_inventory_get(size_t index);
const char *private_cars_generation(void);
const char *private_cars_compatibility(void);
const char *private_cars_error(void);
#define PRIVATE_CARS_MASK_SIZE 12
#if !defined(NFS_PUBLIC) || !NFS_PUBLIC
/* Private retail v1.3 admission, MD5 checked before any retail loader writes. */
const char *private_cars_verify_save(const void *buffer, size_t size);
/* Frozen inventory indices (76 additions, then 14 swaps), not selected order. */
void private_cars_metadata_userdata(const char *root);
const char *private_cars_metadata_stage(void *buffer, size_t size, const unsigned char used[PRIVATE_CARS_MASK_SIZE]);
const char *private_cars_metadata_admit(const void *buffer, size_t size, const unsigned char used[PRIVATE_CARS_MASK_SIZE]);
/* File-layer prepare precedes save rename; completion finalizes its journal. */
int private_cars_save_prepare(const char *staged_save);
void private_cars_save_complete(int committed);
#endif
#endif
