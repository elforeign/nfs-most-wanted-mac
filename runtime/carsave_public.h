#ifndef NFS_CARSAVE_PUBLIC_H
#define NFS_CARSAVE_PUBLIC_H
#include <stddef.h>
#include "runtime.h"
#include "car_catalog.h"

#define CARSAVE_PUBLIC_SIZE 0xF848u
#define CARSAVE_PUBLIC_FE_SIZE 0x8CC8u
#define CARSAVE_PUBLIC_MASK_SIZE 12

/* No music dependency and no hook-address definitions. The public hook source
 * forwards 005ACB70 and 0058FD10 here. Both secondary and backup retail callers
 * reach the former entry. Bind the file hooks below in the userdata file layer,
 * including write/truncate/close of writers opened before a refusal.
 * Init once before loading profiles. No environment/default profile fallback.
 * Refusal is process-sticky; changing userdata never clears it.
 * Optional reconciliation runs ONLY on a staged FE block after provenance and
 * reference validation. It must preserve existing rows and linked pools, insert
 * only verified selected stock additions, and leave a full FE block unchanged.
 * With NULL reconciliation, existing profiles load without inserting stock rows.
 */
typedef const char *(*CarSavePublicReconcile)(Cpu *, unsigned staged_fe);
void carsave_public_init(const char *userdata, CarSavePublicReconcile reconcile);
void carsave_public_load(Cpu *c);
void carsave_public_serialize(Cpu *c);
int carsave_public_save_allowed(void);
const char *carsave_public_error(void);

/* NULL means success; these helpers never modify a profile or latch refusal.
 * Used bits are immutable inventory SLOT indices, never selected-order indices.
 * A zero-dependency serialization preserves all retail bytes and makes no I/O.
 */
const char *carsave_public_verify(const void *buffer, size_t size);
const char *carsave_public_stage(void *buffer, size_t size,
                               const unsigned char used[CARSAVE_PUBLIC_MASK_SIZE]);
const char *carsave_public_admit(const void *buffer, size_t size,
                               const unsigned char used[CARSAVE_PUBLIC_MASK_SIZE]);
int carsave_public_save_prepare(const char *staged);
int carsave_public_backup_before_write(const char *destination, const char *staged);
void carsave_public_save_complete(int committed);
#endif
