#include "traffic_pilot.h"
#include <stddef.h>
#if !defined(NFS_PUBLIC) || !NFS_PUBLIC
#include <stdlib.h>
#include <string.h>
#include "private_cars.h"
int car85_profile_scratch(void);
#endif

static int pinned, enabled, available;
static TrafficPilotForce forced;
static const char *error = "";

int traffic_pilot_quota_allows(const TrafficPilotQuota *q) {
    if (!q) return 0;
    uint64_t count = (uint64_t)q->native_count + q->pilot_count;
    if (q->fixed_limit && count >= q->fixed_limit) return 0;
    if (q->percent_limit) {
        uint64_t cap = (uint64_t)q->population * q->percent_limit / 100;
        if (!cap) cap = 1;
        if (count >= cap) return 0;
    }
    return 1;
}

TrafficPilotChoice traffic_pilot_choose(const TrafficPilotInput *in) {
    TrafficPilotChoice out = {in ? in->original_key : 0, TRAFFIC_PILOT_UNCHANGED};
#if defined(NFS_PUBLIC) && NFS_PUBLIC
    return out;
#else
    if (!in) return out;
    if (!in->enabled && in->force == TRAFFIC_PILOT_NORMAL) return out;
    if (in->force < TRAFFIC_PILOT_NORMAL || in->force > TRAFFIC_PILOT_FORCE_PILOT) {
        out.key = 0; out.status = TRAFFIC_PILOT_DIAGNOSTIC_FAILURE; return out;
    }
    int native = in->native_key && in->native_available;
    int pilot = in->enabled && in->pilot_key && in->pilot_available;
    if ((in->force == TRAFFIC_PILOT_FORCE_NATIVE && !native) ||
        (in->force == TRAFFIC_PILOT_FORCE_PILOT && !pilot)) {
        out.key = 0;
        out.status = TRAFFIC_PILOT_DIAGNOSTIC_FAILURE;
        return out;
    }
    if (!in->original_key) return out;
    if (in->force == TRAFFIC_PILOT_NORMAL &&
        in->original_key != in->native_key && in->original_key != in->pilot_key) return out;
    if (in->native_key && in->native_key == in->pilot_key) {
        out.key = 0;
        out.status = in->force == TRAFFIC_PILOT_NORMAL ? TRAFFIC_PILOT_REFUSED : TRAFFIC_PILOT_DIAGNOSTIC_FAILURE;
        return out;
    }
    out.key = 0; out.status = TRAFFIC_PILOT_REFUSED;
    if (!traffic_pilot_quota_allows(&in->quota)) return out;
    if (in->force == TRAFFIC_PILOT_FORCE_NATIVE) out.key = in->native_key;
    else if (in->force == TRAFFIC_PILOT_FORCE_PILOT) out.key = in->pilot_key;
    else if (native && pilot) out.key = (in->choice & 1) ? in->pilot_key : in->native_key;
    else if (native) out.key = in->native_key;
    else if (pilot) out.key = in->pilot_key;
    if (out.key) out.status = TRAFFIC_PILOT_CHOSEN;
    return out;
#endif
}

int traffic_pilot_boot(int setting) {
    if (pinned) return error[0] ? -1 : 0;
    pinned = 1;
#if defined(NFS_PUBLIC) && NFS_PUBLIC
    (void)setting;
#else
    enabled = !!setting;
    const char *request = getenv("BOOT_DIAG_TRAFFIC_CAR");
    if (request) {
        const char *diag = getenv("BOOT_DIAG");
        if (!diag || !*diag || !car85_profile_scratch()) {
            error = "BOOT_DIAG_TRAFFIC_CAR requires diagnostics and guarded scratch userdata";
            return -1;
        }
        if (!strcmp(request, "traf4dseda")) forced = TRAFFIC_PILOT_FORCE_NATIVE;
        else if (!strcmp(request, "trafficsedan")) forced = TRAFFIC_PILOT_FORCE_PILOT;
        else {
            error = "BOOT_DIAG_TRAFFIC_CAR must be traf4dseda or trafficsedan";
            return -1;
        }
    }
    /* The inventory getter is immutable and performs no I/O. Off skips it. */
    if (enabled) {
        const PrivateCar *car = private_cars_traffic_get();
        available = car && car->selected && car->id == TRAFFIC_PILOT_TYPE &&
                    !strcmp(car->name, TRAFFIC_PILOT_NAME) && !strcmp(car->donor, TRAFFIC_PILOT_DONOR);
        if (!available && getenv("NFS_TEST_ADDCARS")) available = car85_traffic_scratch_eligible();
    }
    if (forced == TRAFFIC_PILOT_FORCE_PILOT && !available) {
        error = "BOOT_DIAG_TRAFFIC_CAR requested unavailable TRAFFICSEDAN (pilot Off or approved assets absent)";
        return -1;
    }
#endif
    return 0;
}

int traffic_pilot_enabled(void) { return enabled; }
int traffic_pilot_available(void) { return available; }
TrafficPilotForce traffic_pilot_force(void) { return forced; }
const char *traffic_pilot_error(void) { return error; }
