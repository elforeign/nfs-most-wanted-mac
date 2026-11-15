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

/* Ordinary-road slices of source 006EE920. These start AFTER the shared
 * brake/drive torque preparation, slip-angle calculation and brake-lock check.
 * Callers must supply that check's updated lock and a lateral curve sample
 * with the selected GRIP_SCALE. Sliding forces retain pre-check slip and speed
 * gain, as in the source. No drift mode or guest layout is used.
 * Double intermediates/binary32 outputs; scalar equivalence, not x87 identity.
 */
typedef struct {
    CarGripForceVector sliding;
    float prior_saturation, lateral_velocity, curve_force, lateral_gain;
    float wheel_torque, radius, angular_acceleration, road_acceleration, inertia;
    int locked;
} CarGripForceDemand;

/* Select sliding when prior_saturation < 1 or locked. Clamp its longitudinal
 * component to same-sign torque/radius. Otherwise use signed curve_force and
 * torque/radius. Apply lateral_gain, then the unlocked adhesion inertia term.
 */
int car_grip_force_ordinary_demand(const CarGripForceDemand *in,
                                  CarGripForceVector *out);

typedef struct {
    CarGripForceVector demand;
    float load, static_grip, grip_multiplier, speed_gain, static_gain;
    float speed, throttle, brake, ebrake;
    int gear, locked;
    float slip, slip_threshold;
    float surface_lateral, surface_longitudinal;
    float lateral_velocity, forward_velocity, slip_angle_turns;
    float angle_correction, grip_scale;
    float wheel_torque, radius, omega, angular_acceleration, road_acceleration;
    float previous_feedback, rolling_friction, surface_rolling, inertia, dt;
} CarGripForceOrdinary;

typedef struct {
    CarGripForceVector force;
    float cap_scale;       /* radial force scale, BEFORE launch/feedback terms */
    float saturation;      /* wheel feedback fraction, NOT cap_scale */
    float stored_slip;     /* launch branch scales only this stored value */
    float feedback, angular_acceleration;
    int launch;
} CarGripForceOrdinaryResult;

/* Source ordinary circle cap, launch correction, slip feedback fraction,
 * surfaces, signed low-road-speed lateral damping / GRIP_SCALE angle correction,
 * and filtered wheel feedback. No torque preparation, brake lock, wheel omega
 * integration or sign-history transition. Keep those native in the bridge.
 * Saturation uses original slip; acceleration uses stored_slip. Feedback uses
 * the final surface/angle-corrected force. Invalid inputs clear all output.
 * grip_scale may be zero only on the forward_velocity <= 1 damping branch.
 */
int car_grip_force_ordinary_finish(const CarGripForceOrdinary *in,
                                  CarGripForceOrdinaryResult *out);
#endif
