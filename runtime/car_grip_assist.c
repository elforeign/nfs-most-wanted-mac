#include "car_grip_assist.h"
#include <float.h>
#include <math.h>
#include <stdint.h>

/* Source FUN_006F8FA0. Instruction ranges and independent controls are pinned
 * in car_grip_assist_test.py. This module is intentionally unhooked.
 */
static int result(double value, float *out)
{
    if (!isfinite(value) || fabs(value) > FLT_MAX) return -1;
    *out = (float)value;
    return 0;
}

static int flag(int value) { return value == 0 || value == 1; }
static int unit(float value) { return isfinite(value) && value >= 0 && value <= 1; }
static int steer(float value) { return isfinite(value) && value >= -1 && value <= 1; }
static int nonnegative(float value) { return isfinite(value) && value >= 0; }
static int table(const float *values, size_t count)
{
    if (!values || !count || count > 65535) return 0;
    for (size_t i = 0; i < count; ++i) if (!isfinite(values[i])) return 0;
    return 1;
}

int car_grip_assist_factors(const CarGripAssistMode *mode, float speed,
                           CarGripAssistFactors *out)
{
    if (!out) return -1;
    *out = (CarGripAssistFactors){0};
    if (!mode || !nonnegative(speed) || mode->driving_style < 0 ||
        !flag(mode->drift_disabled) || !flag(mode->alternate_drift) ||
        !flag(mode->canyon) || mode->player_selector < 0) return -1;
    int a = mode->driving_style == 2 && !mode->drift_disabled;
    int b = !a && mode->alternate_drift;
    *out = (CarGripAssistFactors){
        .a = a, .b = b, .d = a || b,
        .speed_threshold = a ? 10 : 30,
        .canyon_yaw = mode->canyon ? .7 * (1 - .5 * fmin(speed / 100.0, 1)) : 1,
        .alternate_yaw = b ? fmin(speed / 15.0, 1) : 1,
        .player_yaw = mode->player_selector == 1 || mode->player_selector == 2 ? .45 : 1,
        .maintain = mode->canyon ? 1.4 : b ? .4 : 1,
        .lateral = mode->canyon ? .05 : b ? .4 : 1,
        .power_gain = mode->canyon ? 200 : 20,
        .power_exponent = mode->canyon ? .5 : .3,
        .rotation_gain = mode->canyon ? .8 : .6
    };
    return 0;
}

int car_grip_assist_yaw_control(const float *values, size_t count, float slip,
                               float *out)
{
    if (!out) return -1;
    *out = 0;
    if (!isfinite(slip) || !table(values, count)) return -1;
    /* 006F95A0-006F962E: actual count, absolute turns, truncate, no lerp. */
    double position = count * fabs((double)slip);
    if (position >= count) return -1;
    return result(values[(size_t)position], out);
}

int car_grip_assist_additional_yaw(const CarGripAssistMode *mode, float speed,
                                  float slip, float base, const float *values,
                                  size_t count, float *out)
{
    if (!out) return -1;
    *out = 0;
    CarGripAssistFactors f;
    if (car_grip_assist_factors(mode, speed, &f) || !isfinite(slip) || !isfinite(base)) return -1;
    if (!f.d) return result(base, out);
    if (!table(values, count)) return -1;
    /* 006F9648-006F976E: convert degrees BEFORE integer division by five. */
    double degrees = 360 * fabs((double)slip);
    if (degrees > UINT32_MAX) return -1;
    size_t index = (uint32_t)degrees / 5;
    if (index >= count) index = count - 1;
    double fraction = fmin(speed / (double)f.speed_threshold, 1);
    return result(f.player_yaw * (base + f.canyon_yaw * f.alternate_yaw *
                  fraction * fraction * values[index]), out);
}

int car_grip_assist_buildup(const CarGripAssistMode *mode, int active,
                           float steering, float slip, float old, float rate,
                           float dt, float *out)
{
    if (!out) return -1;
    *out = 0;
    CarGripAssistFactors f;
    if (car_grip_assist_factors(mode, 0, &f) || !flag(active) || !steer(steering) ||
        !isfinite(slip) || !nonnegative(old) || !nonnegative(rate) || !nonnegative(dt)) return -1;
    if (!f.d || !active || (double)steering * slip <= 0) return 0;
    /* 006F941E-006F947E: saturation tests OLD state, not this tick's sum. */
    return result(old >= 1 ? 1 : old + (double)rate * dt, out);
}

int car_grip_assist_reduction(const CarGripAssistMode *mode, float steering,
                             float slip, float buildup, float coefficient,
                             float *out)
{
    if (!out) return -1;
    *out = 0;
    CarGripAssistFactors f;
    if (car_grip_assist_factors(mode, 0, &f) || !steer(steering) || !isfinite(slip) ||
        !nonnegative(buildup) || !isfinite(coefficient)) return -1;
    if (!f.d || (double)steering * slip <= 0) return result(1, out);
    /* 006F976E-006F97B0: preserve overshoot, including a negative divisor. */
    return result((1 - (double)buildup) * coefficient + buildup, out);
}

int car_grip_assist_history(const CarGripAssistMode *mode, float speed,
                           float history, float *out)
{
    if (!out) return -1;
    *out = 0;
    CarGripAssistFactors f;
    if (car_grip_assist_factors(mode, speed, &f) || !nonnegative(history)) return -1;
    if (!f.d) return result(history, out);
    /* 006F9B1A-006F9B89; the lower branch wins when both bounds are zero. */
    if (speed <= .6 * history) return result(.75 * history + .25 * speed, out);
    if (speed >= 1.2 * history) return result(.5 * history + .5 * speed, out);
    return result(history, out);
}

int car_grip_assist_yaw_torque(const CarGripAssistMode *mode, float speed,
                              int gear, float throttle, float steering,
                              float inertia_y, float envelope, float q,
                              float divisor, float *out)
{
    if (!out) return -1;
    *out = 0;
    CarGripAssistFactors f;
    if (car_grip_assist_factors(mode, speed, &f) || !unit(throttle) || !steer(steering) ||
        !nonnegative(inertia_y) || !unit(envelope) || !isfinite(q) || !isfinite(divisor)) return -1;
    if (!gear || !(speed > f.speed_threshold || throttle > 1e-6f)) return 0;
    if (divisor == 0) return -1;
    return result(inertia_y * (f.a ? 1 : (double)envelope) * steering * q / divisor, out);
}

int car_grip_assist_maintain_speed(const CarGripAssistMode *mode, float speed,
                                  float history, float throttle, float ebrake,
                                  float coefficient, float *out)
{
    if (!out) return -1;
    *out = 0;
    CarGripAssistFactors f;
    if (car_grip_assist_factors(mode, speed, &f) || !nonnegative(history) || !unit(throttle) ||
        !unit(ebrake) || !isfinite(coefficient)) return -1;
    if (!f.d || throttle <= 1e-6f || speed >= history || ebrake > .5f) return 0;
    /* 006F9BE8-006F9C4E: body force, with no extra mass or tyre-load factor. */
    return result(f.maintain * (double)coefficient * throttle * (1.2 * history - speed), out);
}

int car_grip_assist_lateral(const CarGripAssistMode *mode, float speed,
                            float slip, float throttle, float coefficient,
                            float *out)
{
    if (!out) return -1;
    *out = 0;
    CarGripAssistFactors f;
    if (car_grip_assist_factors(mode, speed, &f) || !isfinite(slip) || !unit(throttle) ||
        !isfinite(coefficient)) return -1;
    if (!f.d || fabs((double)slip) <= 1e-6f) return 0;
    /* 006F9CF8-006F9DA3; 006F9D21/24 load throttle then exponent for pow. */
    double power = pow(throttle, f.power_exponent);
    return result(-(slip < 0 ? -1 : 1) * f.lateral * (double)coefficient *
                  (3 * (double)throttle + .4) * (f.power_gain * (1 - power) + 1) *
                  fabs((double)slip) * speed * speed, out);
}

int car_grip_assist_rotate(const CarGripAssistMode *mode, float slip,
                          const CarGripAssistVector *in, CarGripAssistVector *out)
{
    if (!out) return -1;
    *out = (CarGripAssistVector){0};
    CarGripAssistFactors f;
    if (car_grip_assist_factors(mode, 0, &f) || !isfinite(slip) || !in ||
        !isfinite(in->x) || !isfinite(in->y) || !isfinite(in->z)) return -1;
    if (!f.d) { *out = *in; return 0; }
    /* 006F9ABA-006F9B0C -> 00606230/00401B50, row-vector world-Y rotation. */
    double angle = f.rotation_gain * (double)slip * 6.2831853071795864769;
    double c = cos(angle), s = sin(angle);
    CarGripAssistVector v = {0};
    if (result(c * in->x + s * in->z, &v.x) ||
        result(in->y, &v.y) || result(-s * in->x + c * in->z, &v.z)) return -1;
    *out = v;
    return 0;
}
