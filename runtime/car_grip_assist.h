#ifndef NFS_CAR_GRIP_ASSIST_H
#define NFS_CAR_GRIP_ASSIST_H
#include <stddef.h>

#define CAR_GRIP_DRIFT_YAW_CONTROL            0xC51EB1A1u
#define CAR_GRIP_DRIFT_ADDITIONAL_YAW_CONTROL 0xF4BBE085u
#define CAR_GRIP_DRIFT_COUNTER_STEER_BUILDUP  0x7DB55397u
#define CAR_GRIP_DRIFT_COUNTER_STEER_REDUCTION 0x0D255C03u
#define CAR_GRIP_DRIFT_MAINTAIN_SPEED_FORCE   0xC141848Au
#define CAR_GRIP_DRIFT_LATERAL_FORCE          0xECEFA5B3u

/* Dormant scalar source-assist stages, not tyre friction or game hooks.
 * Pure functions: no guest memory, global tuning, metadata selection or owned
 * history. Caller resolves the source selectors and supplies selected fields.
 * Source state +C8 is driving style; +CA (driver class) is NOT a mode selector.
 * drift_disabled and alternate_drift represent the two source global flags;
 * canyon is the resolved source race flag, player_selector the resolved chain
 * result (1/2 select .45). No live interface/object offsets are copied here.
 */
typedef struct {
    int driving_style, drift_disabled, alternate_drift, canyon, player_selector;
} CarGripAssistMode;

typedef struct {
    int a, b, d;
    float speed_threshold, canyon_yaw, alternate_yaw, player_yaw;
    float maintain, lateral, power_gain, power_exponent, rotation_gain;
} CarGripAssistFactors;
typedef struct { float x, y, z; } CarGripAssistVector;

/* All scalar inputs finite; speed/history/dt/buildup >= 0, throttle/envelope in
 * [0,1], steering in [-1,1], flags 0/1. Selected coefficients may be signed
 * except buildup rate (>=0). Arrays contain 1..65535 finite binary32 samples.
 * Slip is in TURNS, speed in m/s, dt in seconds. Double intermediates and float
 * outputs model the equations, not bit-exact x87/lookup-table trigonometry.
 * Return 0 on success, -1 on invalid input/overflow, clearing output on failure.
 * Inputs and output must not alias. No default-data-area fallback is invented.
 */
int car_grip_assist_factors(const CarGripAssistMode *mode, float speed,
                           CarGripAssistFactors *out);
/* q = values[trunc(actual_count * abs(slip))]. Out-of-range rejects rather
 * than clamping to the last sample. This is the only authored road term.
 */
int car_grip_assist_yaw_control(const float *values, size_t count, float slip,
                               float *out);
/* D: p*(base + canyon_yaw*alternate_yaw*min(speed/u0,1)^2*values[j]);
 * j=min(trunc(360*abs(slip))/5,count-1), with INTEGER division by five.
 * !D returns base; the unused additional table may be NULL/count zero.
 */
int car_grip_assist_additional_yaw(const CarGripAssistMode *mode, float speed,
                                  float slip, float base, const float *values,
                                  size_t count, float *out);
/* !D, inactive or steering*slip<=0: 0. Otherwise old>=1 ? 1 : old+k*dt.
 * The update may exceed 1 for ONE tick; never post-clamp it.
 */
int car_grip_assist_buildup(const CarGripAssistMode *mode, int active,
                           float steering, float slip, float old, float rate,
                           float dt, float *out);
/* D && steering*slip>0: (1-r)*coefficient+r; otherwise exactly 1. */
int car_grip_assist_reduction(const CarGripAssistMode *mode, float steering,
                             float slip, float buildup, float coefficient,
                             float *out);
/* D: speed<=.6H -> .75H+.25speed; speed>=1.2H -> .5H+.5speed;
 * otherwise hold H. !D holds H. History belongs in separate caller storage.
 */
int car_grip_assist_history(const CarGripAssistMode *mode, float speed,
                           float history, float *out);
/* Scalar along body up: Iy*(A?1:envelope)*steering*q/divisor, admitted only
 * for nonzero gear and (speed>u0 || throttle>1e-6). Iy is state +A4.
 * A zero divisor fails only if torque is admitted. Negative divisors retained.
 */
int car_grip_assist_yaw_torque(const CarGripAssistMode *mode, float speed,
                              int gear, float throttle, float steering,
                              float inertia_y, float envelope, float q,
                              float divisor, float *out);
/* Scalar along R(forward): mF*k*throttle*(1.2H-speed), only under D,
 * throttle>1e-6, speed<H, ebrake<=.5. Pass the UPDATED history H.
 */
int car_grip_assist_maintain_speed(const CarGripAssistMode *mode, float speed,
                                  float history, float throttle, float ebrake,
                                  float coefficient, float *out);
/* Scalar along R(right): -sign(slip)*mL*k*(3a+.4)*
 * [K*(1-pow(a,e))+1]*abs(slip)*speed^2; D && abs(slip)>1e-6.
 * Nonzero at zero throttle. !D returns zero for both linear-force terms.
 */
int car_grip_assist_lateral(const CarGripAssistMode *mode, float speed,
                            float slip, float throttle, float coefficient,
                            float *out);
/* R rotates a caller-supplied forward/right vector about WORLD Y by
 * (canyon?.8:.6)*slip turns: (c*x+s*z, y, -s*x+c*z). !D is identity.
 * Torque uses body up directly and must not use this force rotation.
 */
int car_grip_assist_rotate(const CarGripAssistMode *mode, float slip,
                          const CarGripAssistVector *in, CarGripAssistVector *out);
#endif
