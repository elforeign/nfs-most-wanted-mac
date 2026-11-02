#include "car_grip_steering.h"
#include <float.h>
#include <math.h>

static int result(double value, float *out)
{
    if (!isfinite(value) || fabs(value) > FLT_MAX) return -1;
    *out = (float)value;
    return 0;
}

int car_grip_steering_yaw_limit(const float *values, size_t count, float speed,
                               int transmission, float reference_speed, float *out)
{
    if (!out) return -1;
    *out = 0;
    if (!values || count < 1 || count > 4 || !isfinite(speed) ||
        !isfinite(reference_speed) || (transmission != 0 && transmission != 1)) return -1;
    for (size_t i = 0; i < count; ++i) if (!isfinite(values[i])) return -1;
    if (transmission && reference_speed < 0) return 0;
    if (!transmission || count == 1) return result(values[0], out);
    if (reference_speed == 0 && signbit(reference_speed)) return -1;
    double fraction = reference_speed == 0 ? 1 : fmin(1, fabs((double)speed) / reference_speed);
    double position = fraction * (count - 1);
    size_t lower = (size_t)position;
    size_t upper = lower + 1 < count ? lower + 1 : count - 1;
    return result(values[lower] + (values[upper] - (double)values[lower]) *
                  (position - lower), out);
}

int car_grip_steering_yaw_friction(const CarGripYawFriction *in,
                                  const CarGripYawTuning *tuning, float *out)
{
    if (!out) return -1;
    *out = 0;
    if (!in || !tuning) return -1;
    if (!isfinite(in->slip_radians) || !isfinite(in->ebrake) || !isfinite(in->speed) ||
        !isfinite(in->limit) || !isfinite(in->attitude_component) || !isfinite(in->blend) ||
        !isfinite(tuning->low_speed) || !isfinite(tuning->high_speed) ||
        !isfinite(tuning->low_boost) || !isfinite(tuning->high_boost) ||
        !isfinite(tuning->max_bonus) || !isfinite(tuning->ebrake_threshold) ||
        !isfinite(tuning->angle_threshold) || tuning->high_speed <= tuning->low_speed ||
        tuning->max_bonus < 0 || tuning->angle_threshold < 0 ||
        in->blend < 0 || in->blend > 1 || (in->rear != 0 && in->rear != 1)) return -1;
    if (!in->rear) return result(1, out);
    double bonus = 0;
    if (!(in->ebrake > tuning->ebrake_threshold &&
          fabs((double)in->slip_radians) < tuning->angle_threshold)) {
        double gain = tuning->low_boost +
            ((double)in->speed - tuning->low_speed) /
            ((double)tuning->high_speed - tuning->low_speed) *
            ((double)tuning->high_boost - tuning->low_boost);
        bonus = fmin(tuning->max_bonus, gain * fabs((double)in->slip_radians) * in->limit);
    }
    return result(1 + (fabs((double)in->attitude_component) + bonus) * in->blend, out);
}
