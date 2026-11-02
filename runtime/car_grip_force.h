#ifndef NFS_CAR_GRIP_FORCE_H
#define NFS_CAR_GRIP_FORCE_H

/* macOS port only; these dormant helpers leave Windows behavior unchanged. */
/* Pure force-stage helpers, not a complete tyre solver or a metadata mode.
 * Inputs are already selected for one axle/wheel. No upgrade interpolation,
 * torque, braking, wheel integration, contact mass or guest state is owned here.
 * Units: N, m/s, dimensionless coefficients. All functions clear output on error.
 */
typedef struct { float lateral, longitudinal; } CarGripForceVector;

typedef struct {
    float load, lateral_velocity, forward_velocity, wheel_slip, speed;
    float dynamic_grip, grip_multiplier;
    int speed_sensitive, forward_rotation, locked;
} CarGripForceSliding;

/* Sliding branch before torque clamp, surface factors and static saturation.
 * forward_rotation includes zero angular velocity. speed is signed, as supplied
 * to the original speed-gain function; forward_velocity is also signed.
 */
int car_grip_force_sliding(const CarGripForceSliding *in, CarGripForceVector *out);

typedef struct { float lateral, longitudinal; } CarGripForceAxes;
/* Selected D15B2BDE element; throttle in [0,1], signed speed in m/s.
 * rear selects the axle (0 front / 1 rear). Returns source drift static-limit
 * multipliers, each floored to 0.001.
 * Does not select the drift branch or sample the authored coefficient array.
 */
int car_grip_force_drift_axes(float coefficient, float throttle, float speed, int rear,
                             CarGripForceAxes *out);

typedef struct {
    CarGripForceVector demand;
    float load, static_grip, grip_multiplier, speed_gain;
    CarGripForceAxes axes;
    int ellipse;
} CarGripForceLimit;

typedef struct {
    CarGripForceVector force;
    float saturation;
} CarGripForceResult;

/* Demand is the pre-limit force, after wheel torque/inertia and lateral gain.
 * Circle: axes.longitudinal is unused, axes.lateral is the static-limit gain.
 * Ellipse: the source positive-longitudinal branch and fallback are preserved;
 * zero lateral axes are rejected. This is NOT a symmetric ellipse projection.
 * Surface gains and slip feedback remain outside this stage.
 * An unloaded wheel with valid inputs returns zero force.
 */
int car_grip_force_limit(const CarGripForceLimit *in, CarGripForceResult *out);
#endif
