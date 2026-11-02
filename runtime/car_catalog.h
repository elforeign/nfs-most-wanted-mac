#ifndef NFS_CAR_CATALOG_H
#define NFS_CAR_CATALOG_H
#include <stddef.h>
#include <stdint.h>

/* Version 1 wire contract: runtime/docs/added-car-catalog.md.
 * Startup only, on one thread, before registration. Init is idempotent, including
 * failures and disabled operation. No environment variables or source-tree reads.
 * Packagers may define CAR_CATALOG_APPROVAL_HEADER to a generated C header that
 * defines CAR_CATALOG_POLICY_JSON (canonical JSON string literal) and
 * CAR_CATALOG_POLICY_SHA256 (independently reviewed lowercase digest literal).
 * With no compiled policy there are no approvals. Never fill these from a local
 * pointer, catalog, neighboring policy file, command line or environment.
 */
#define CAR_CATALOG_SLOTS 90
#define CAR_CATALOG_ADDITIONS 76
#define CAR_CATALOG_OUTPUTS 7
typedef enum {
    CAR_CATALOG_GEOMETRY, CAR_CATALOG_TEXTURES, CAR_CATALOG_SOLID_MAP,
    CAR_CATALOG_WHEEL_FIT, CAR_CATALOG_COLLISION, CAR_CATALOG_PAINT,
    CAR_CATALOG_PHYSICS
} CarCatalogOutputKind;

typedef struct {
    unsigned type;
    size_t first, count; /* indices into the slot's ordered part_keys */
} CarCatalogPart;

typedef struct {
    int present;
    unsigned version; /* zero for opaque geometry/texture packs */
    uint64_t size;
    char sha256[65];
    /* Anonymous verified snapshot, retained for process lifetime. This path is
     * independent of the installed pathname, even after replacement or writes.
     * Do not derive sibling paths, close its descriptor or write through it.
     * Existing fopen/read consumers may use this path. Use open_output for a
     * caller-owned read descriptor positioned at zero. Descriptor paths can
     * share a file offset: serialize streams for one output, or use pread.
     */
    char path[64];
} CarCatalogOutput;

typedef struct {
    unsigned slot, id;
    int swap_index; /* -1 for additions; 0..13 for absent swap reservations */
    int ready, selected;
    char identity[65], compatibility[65];
    char source_game[16], source_platform[8], source_folder[32];
    char folder[32], pvehicle[32], frontend[32], ecar[32];
    char model_donor[32], price_donor[32];
    char display_name[513], manufacturer[513]; /* UTF-8, up to 128 code points */
    unsigned converter_revision;
    char converter_policy[65];
    size_t part_count, part_key_count;
    const CarCatalogPart *parts;
    const char (*part_keys)[32];
    CarCatalogOutput outputs[CAR_CATALOG_OUTPUTS]; /* explicit absent metadata */
} CarCatalogSlot;

/* root is the catalog directory, not userdata. Returns selected count or -1.
 * Disabled, absent and explicitly disabled pointer states return zero. All
 * errors expose zero inventory and zero selected rows. Successful inventory
 * includes every reservation: absent slots never compact. All returned data
 * remains valid and immutable until process exit; accessors perform no I/O.
 */
int car_catalog_init(const char *root, int enabled);
size_t car_catalog_count(void);
const CarCatalogSlot *car_catalog_get(size_t selected_index);
size_t car_catalog_inventory_count(void);
const CarCatalogSlot *car_catalog_inventory_get(size_t slot);
const char *car_catalog_generation(void);
const char *car_catalog_compatibility(void);
const char *car_catalog_error(void);
const char *car_catalog_output_name(CarCatalogOutputKind kind);
/* Never opens an installed path. Caller closes returned fd. Returns -1 for an
 * absent output or invalid index. Output access is allowed for deselected ready
 * slots as well (inventory/save admission consumers need their exact identity).
 */
int car_catalog_open_output(unsigned slot, CarCatalogOutputKind kind);
#endif
