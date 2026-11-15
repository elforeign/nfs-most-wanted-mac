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
/* Checks the hash of the exact bytes parsed, so an in-place write after an
 * earlier descriptor integrity check cannot substitute unpinned coefficients.
 * sha256 is the catalog's required lowercase digest, not a neighboring file.
 */
int car_grip_read_pinned(int fd, const char *source, const char *donor, int mode,
                          const char *sha256, uint64_t size, CarGrip *out);
int car_grip_load(const char *path, const char *source, const char *donor, int mode, CarGrip *out);
/* Only classified shared fields are available. tier is an authored reference
 * ordinal, never an upgrade level. Source-only evidence returns NULL.
 */
const CarGripField *car_grip_field(const CarGrip *grip, unsigned tier, uint32_t key);

/* Versioned dormant selection contract, independent of the GRP1 wire version.
 * Only a future player bridge may construct this from the live ISimable and its
 * current pvehicle. Neither authored tier ordinals nor upgrade_max are live
 * upgrade levels. No function here reads or writes guest memory.
 */
enum { CAR_GRIP_SELECTION_VERSION = 1, CAR_GRIP_SELECTED_FIELDS = 5 };
typedef struct {
    uint32_t version, key, vault;
    uint64_t generation;
    const CarGrip *grip;
} CarGripBinding;
typedef struct {
    uint32_t version, key, vault, simable, local_simable;
    uint64_t generation;
} CarGripOwner;
typedef struct { uint32_t key; float multiplier; } CarGripScale;
typedef struct {
    int level, max_level, junkman;
    /* Decoded installed tyre-part scales (not a reference ordinal). Junkman
     * requires a resolved table (non-NULL, even when empty); missing parts
     * refuse. With Junkman off any nonempty scale table refuses.
     * The bridge must obtain these and level/max_level from owner's pvehicle.
     */
    const CarGripScale *scales;
    size_t scale_count;
} CarGripLive;
typedef struct { CarGripField fields[CAR_GRIP_SELECTED_FIELDS]; } CarGripSelected;
/* Bind only an admitted addition, after its owned pvehicle collection is live.
 * Retiring the vault must clear this token before any address can be reused.
 */
void car_grip_bind(CarGripBinding *binding, const CarGrip *grip,
                   uint32_t key, uint32_t vault, uint64_t generation);
/* 1 selected, 0 stock fallback; failure always clears out. Requires exact local
 * player identity and vault lifetime. C51 is available ONLY through this v1
 * contract; every other source-only field, STEERING and YAW_SPEED stay absent.
 * Native upgrade rule: stock=ref0; positive level blends refs0/1, or refs1/2
 * for a three-reference record, by live level/max_level. A single reference
 * stays constant. Missing array elements blend as zero, as in BlendParts_float.
 */
int car_grip_select(const CarGripBinding *binding, const CarGripOwner *owner,
                    const CarGripLive *live, CarGripSelected *out);
const CarGripField *car_grip_selected_field(const CarGripSelected *selected, uint32_t key);
/* Dormant addition lookups; never called by a gameplay hook in this stage.
 * owner snapshots the current token for a live pvehicle key/vault pair. It must
 * be acquired from the current local ISimable, not cached by type or folder.
 * select rechecks the lifetime, so unload/reload invalidates copied tokens.
 */
int car85_grip_owner(uint32_t key, uint32_t vault, uint32_t simable,
                     uint32_t local_simable, CarGripOwner *out);
int car85_grip_select(const CarGripOwner *owner, const CarGripLive *live, CarGripSelected *out);
#endif
