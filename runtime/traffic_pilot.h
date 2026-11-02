#ifndef NFS_TRAFFIC_PILOT_H
#define NFS_TRAFFIC_PILOT_H
#include <stdint.h>

/* Frozen identity for registration and spawn adapters. Never a player slot. */
#define TRAFFIC_PILOT_NAME "TRAFFICSEDAN"
#define TRAFFIC_PILOT_TYPE 160u
#define TRAFFIC_PILOT_DONOR "TRAF4DSEDA"
#define TRAFFIC_PILOT_DONOR_TYPE 25u

typedef enum {
    TRAFFIC_PILOT_NORMAL, TRAFFIC_PILOT_FORCE_NATIVE, TRAFFIC_PILOT_FORCE_PILOT
} TrafficPilotForce;
typedef enum {
    TRAFFIC_PILOT_UNCHANGED, TRAFFIC_PILOT_CHOSEN,
    TRAFFIC_PILOT_REFUSED, TRAFFIC_PILOT_DIAGNOSTIC_FAILURE
} TrafficPilotStatus;
typedef struct {
    uint32_t native_count, pilot_count;
    uint32_t fixed_limit;       /* zero means unlimited, as in NextSpawn */
    uint32_t percent_limit;     /* zero means unlimited */
    uint32_t population;        /* decoded NextSpawn population incl. reserve */
} TrafficPilotQuota;
typedef struct {
    uint32_t original_key;      /* zero is a retail refusal: never replace it */
    uint32_t native_key, pilot_key; /* decoded vehicle keys, NOT type indices */
    uint32_t choice;            /* existing selection ordinal; no new RNG */
    int enabled, native_available, pilot_available;
    TrafficPilotForce force;
    TrafficPilotQuota quota;
} TrafficPilotInput;
typedef struct { uint32_t key; TrafficPilotStatus status; } TrafficPilotChoice;

/* Pure helpers: no guest access, state changes, allocation, RNG or I/O.
 * Percentage cap is max(1, floor(population * percent_limit / 100)).
 * Both sedan counts consume each cap. Caller decodes population and limits;
 * caller must count BOTH identities before retail NextSpawn can refuse.
 * Normal policy widens only successful sedan choices; other keys are untouched.
 * Even/odd existing ordinals retain native/pilot when both are available.
 * Forced choices may use any successful traffic opportunity, obeying sedan quotas;
 * zero/refusal still stays zero. Diagnostic failure
 * must be surfaced by the adapter, never silently substituted.
 */
int traffic_pilot_quota_allows(const TrafficPilotQuota *quota);
TrafficPilotChoice traffic_pilot_choose(const TrafficPilotInput *input);

/* Startup-only, first call pins the setting. Call after private catalog init.
 * Public builds stay disabled and do not inspect environment or inventory.
 * A diagnostic subject requires BOOT_DIAG and guarded scratch approval.
 * Returns -1 with an explicit error for invalid/unavailable requested subjects.
 * Registration readiness is still checked by the spawn adapter via Input.
 */
int traffic_pilot_boot(int enabled);
/* Registration exports: keys are zero unless both donor vault lifetimes are
 * ready. Scratch eligibility validates a guarded traffic manifest at startup. */
int car85_traffic_ready(void);
uint32_t car85_traffic_key(void);
uint32_t car85_traffic_donor_key(void);
int car85_traffic_scratch_eligible(void);
int traffic_pilot_enabled(void);
int traffic_pilot_available(void);
TrafficPilotForce traffic_pilot_force(void);
const char *traffic_pilot_error(void);
#endif
