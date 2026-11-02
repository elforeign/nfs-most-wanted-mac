#ifndef NFS_CAR_GRIP_STEERING_H
#define NFS_CAR_GRIP_STEERING_H
#include <stddef.h>

/* macOS port only; these dormant helpers leave Windows behavior unchanged. */
/* Selected yaw/friction stages only. No input steering, body torque, car/mode
 * selection, guest writes or state transitions. Failure clears the output.
 * values is an already selected YAW_CONTROL array (1..4 dimensionless gains).
 * speed and reference_speed use the same units (m/s). A missing transmission
 * selects element zero; a negative reference speed returns zero. At zero
 * positive-zero reference speed the source selects the last element, including
 * speed zero. Negative zero is rejected when interpolation would be required.
 */
int car_grip_steering_yaw_limit(const float *values, size_t count, float speed,
                               int transmission, float reference_speed, float *out);

typedef struct {
    float low_speed, high_speed, low_boost, high_boost;
    float max_bonus, ebrake_threshold, angle_threshold;
} CarGripYawTuning;

typedef struct {
    float slip_radians, ebrake, speed, limit, attitude_component, blend;
    int rear;
} CarGripYawFriction;

/* angle_threshold is radians; speed is m/s. attitude_component is the existing
 * signed state component passed by the caller, not a tyre coefficient.
 * blend is the caller's already selected assist envelope, in [0,1].
 * Tuning is explicit: this helper never imports whole-vehicle tuning defaults.
 * The speed interpolation is intentionally NOT clamped; bonus has only an
 * upper cap. Front wheels return 1. Rear wheels return the blended boost.
 */
int car_grip_steering_yaw_friction(const CarGripYawFriction *in,
                                  const CarGripYawTuning *tuning, float *out);
#endif
