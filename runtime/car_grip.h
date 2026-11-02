#ifndef NFS_CAR_GRIP_H
#define NFS_CAR_GRIP_H
#include <stddef.h>
#include <stdint.h>

enum { CAR_GRIP_DONOR = 0, CAR_GRIP_SHARED = 1 };
enum { CAR_GRIP_MAX_TIERS = 8, CAR_GRIP_FIELDS = 17, CAR_GRIP_MAX_VALUES = 40 };
typedef struct {
    uint32_t key, semantics, count;
    float values[CAR_GRIP_MAX_VALUES];
} CarGripField;
typedef struct {
    uint32_t key;
    CarGripField fields[CAR_GRIP_FIELDS];
} CarGripTier;
typedef struct {
    uint32_t present, source, donor, tier_count, upgrade_max;
    CarGripTier tiers[CAR_GRIP_MAX_TIERS];
} CarGrip;

/* macOS port only; these dormant helpers leave Windows behavior unchanged. */
/* 0: valid or missing, -1: invalid present metadata/mode. Failure clears out.
 * Donor mode skips all file admission. These functions never write guest data.
 * Paths are optional: NULL means missing; a present symlink is rejected.
 */
int car_grip_read(int fd, const char *source, const char *donor, int mode, CarGrip *out);
int car_grip_load(const char *path, const char *source, const char *donor, int mode, CarGrip *out);
/* Only classified shared fields are available. tier is an authored reference
 * ordinal, never an upgrade level. Source-only evidence returns NULL.
 */
const CarGripField *car_grip_field(const CarGrip *grip, unsigned tier, uint32_t key);
#endif
