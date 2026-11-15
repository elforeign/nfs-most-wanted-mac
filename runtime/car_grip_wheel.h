#ifndef NFS_CAR_GRIP_WHEEL_H
#define NFS_CAR_GRIP_WHEEL_H
#include "car_grip_force.h"
#include <stddef.h>

/* Dormant host-side adapter; no guest reads/writes, identity admission, hooks,
 * TLS, mode selection or pointer lifetime management. The bridge must admit a
 * source endpoint before constructing this overlay. These are owned values,
 * NOT a guest tyre layout. Axle 0 is front, 1 rear.
 */
typedef struct {
    float dynamic_grip[2], static_grip[2], grip_scale[2];
    float yaw_control[4];
    size_t yaw_count;
} CarGripWheelOverlay;

typedef struct {
    CarGripWheelOverlay grip;
    float steering[2], yaw_speed;
} CarGripWheelTyre;

/* Replace only DYNAMIC_GRIP / STATIC_GRIP / GRIP_SCALE / YAW_CONTROL.
 * Preserve native STEERING and YAW_SPEED. Actual yaw count is 1..4; unused
 * slots are zero. No interpolation or reference ordinal selection occurs here.
 * A separate guest-layout shadow is still required by the eventual bridge.
 */
int car_grip_wheel_overlay(const CarGripWheelTyre *native,
                           const CarGripWheelOverlay *source, CarGripWheelTyre *out);

typedef struct {
    unsigned axle;
    /* Speed-gain predicates and slip are from BEFORE the brake-lock check,
     * matching the source ordering. demand/finish.locked are AFTER that check.
     */
    CarGripForceSliding sliding; /* dynamic_grip is replaced from the overlay */
    CarGripForceDemand demand;   /* sliding and curve_force are replaced below */
    CarGripForceOrdinary finish; /* demand/static_grip/grip_scale are replaced */
    /* Shared ordinary curve sampled at the selected load and slip angle,
     * including its native wheel gain and table scalar, EXCLUDING GRIP_SCALE.
     * Curve tables are equivalent; sample with the native curve routine.
     * demand.lateral_gain includes native 0068EF10 YawFrictionBoost, evaluated
     * with native 0069EBF0 CalcYawControlLimit on the owned source overlay.
     * This shim calls neither native function nor the dormant G3 oracles.
     */
    float curve_force_per_grip;
} CarGripWheelInput;

/* Prepared-wheel seam: braking, slip/angle, speed gain, lock check and road
 * acceleration are provided by the bridge. All repeated wheel inputs must
 * agree (inconsistent snapshots fail closed). finish.speed_gain is the same
 * source speed gain as sliding's predicates imply. No donor longitudinal
 * demand/static-limit axes or old torque-direction boost may be pre-applied.
 * Output includes feedback state for native omega integration / CheckSign.
 */
int car_grip_wheel_ordinary(const CarGripWheelOverlay *source,
                            const CarGripWheelInput *in, CarGripForceOrdinaryResult *out);
#endif
