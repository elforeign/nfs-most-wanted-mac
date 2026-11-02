#include "car_grip_force.h"
#include <math.h>
#include <string.h>

static int finite_nonnegative(float value) {
    return isfinite(value) && value >= 0.0f;
}
static int boolean(int value) { return value == 0 || value == 1; }

int car_grip_force_sliding(const CarGripForceSliding *in, CarGripForceVector *out) {
    if (!out) return -1;
    memset(out, 0, sizeof *out);
    if (!in || !isfinite(in->load) || !isfinite(in->lateral_velocity) ||
        !isfinite(in->forward_velocity) || !isfinite(in->wheel_slip) ||
        !isfinite(in->speed) || !finite_nonnegative(in->dynamic_grip) ||
        !finite_nonnegative(in->grip_multiplier) || !boolean(in->speed_sensitive) ||
        !boolean(in->forward_rotation) || !boolean(in->locked)) return -1;
    if (in->load <= 0.0f) return 0;
    /* The source checks both norms; the earlier solver checks the slip norm
     * and nonzero road velocity. Use the source guard, not offset equivalence. */
    double slip = hypot((double)in->lateral_velocity, in->wheel_slip);
    double road = hypot((double)in->lateral_velocity, in->forward_velocity);
    if (slip <= 1e-6 || road <= 1e-6) return 0;
    double gain = 1.0;
    if (in->speed_sensitive && in->forward_rotation && !in->locked) {
        double t = ((double)in->speed - 13.4109f) * 0.111849315f;
        t = fmin(1.0, fmax(0.0, t));
        gain = 0.85f + 0.14999998f * t;
    }
    double mu = (double)in->dynamic_grip * in->grip_multiplier * gain;
    /* At less than one mph the original removes mu when mu > 0.1. */
    if (in->speed < 0.44703f && mu > 0.1f) mu = 1.0;
    double scale = mu * in->load / slip;
    CarGripForceVector candidate = {
        (float)(-in->lateral_velocity * scale), (float)(in->wheel_slip * scale)
    };
    if (!isfinite(candidate.lateral) || !isfinite(candidate.longitudinal)) return -1;
    *out = candidate;
    return 0;
}

int car_grip_force_drift_axes(float coefficient, float throttle, float speed, int rear,
                             CarGripForceAxes *out) {
    if (!out) return -1;
    memset(out, 0, sizeof *out);
    if (!finite_nonnegative(coefficient) || !isfinite(throttle) ||
        throttle < 0.0f || throttle > 1.0f || !isfinite(speed) || !boolean(rear)) return -1;
    /* The source ramp clamps signed speed/10 to [0,1], then squares it. */
    double t = fmin(1.0, fmax(0.0, (double)speed / 10.0));
    double ramp = t * t;
    double front = 0.2f * ramp + 2.0 * (1.0 - ramp);
    double back = coefficient * throttle * ramp +
        (1.0 - ramp * throttle) *
        (3.0 * coefficient + (1.0 - throttle) * (1.0 - ramp));
    CarGripForceAxes candidate = {
        (float)fmax(0.001f, rear ? back : front), 1.5f
    };
    if (!isfinite(candidate.lateral) || !isfinite(candidate.longitudinal)) return -1;
    *out = candidate;
    return 0;
}

int car_grip_force_limit(const CarGripForceLimit *in, CarGripForceResult *out) {
    if (!out) return -1;
    memset(out, 0, sizeof *out);
    if (!in || !isfinite(in->demand.lateral) || !isfinite(in->demand.longitudinal) ||
        !isfinite(in->load) || !finite_nonnegative(in->static_grip) ||
        !finite_nonnegative(in->grip_multiplier) || !finite_nonnegative(in->speed_gain) ||
        !finite_nonnegative(in->axes.lateral) || !finite_nonnegative(in->axes.longitudinal) ||
        !boolean(in->ellipse) || (in->ellipse && in->axes.lateral == 0.0f)) return -1;
    CarGripForceResult candidate = { .force = in->demand, .saturation = 1.0f };
    if (in->load <= 0.0f) {
        candidate.force = (CarGripForceVector){0};
        candidate.saturation = hypot(in->demand.lateral, in->demand.longitudinal) > 0.001f ? 0.0f : 1.0f;
        *out = candidate;
        return 0;
    }
    double base = (double)in->static_grip * in->grip_multiplier * in->load * in->speed_gain;
    double lateral = in->demand.lateral, longitudinal = in->demand.longitudinal;
    double magnitude = hypot(lateral, longitudinal);
    double scale = 1.0;
    if (!in->ellipse) {
        double cap = base * in->axes.lateral;
        if (magnitude > cap && magnitude > 0.001f) scale = cap / magnitude;
    } else {
        double lateral_cap = base * in->axes.lateral;
        double longitudinal_cap = base * in->axes.longitudinal;
        double target;
        if (longitudinal < 1e-6 || longitudinal_cap < 1e-6) {
            /* Deliberately asymmetric source fallback, including zero demand. */
            target = lateral_cap;
        } else {
            double x = lateral_cap /
                hypot(lateral_cap / longitudinal_cap, lateral / longitudinal);
            double y = (lateral / longitudinal) * x;
            target = hypot(x, y);
        }
        if (magnitude > target && magnitude * magnitude > 1e-6) scale = target / magnitude;
    }
    candidate.force.lateral = (float)(lateral * scale);
    candidate.force.longitudinal = (float)(longitudinal * scale);
    candidate.saturation = (float)scale;
    if (!isfinite(candidate.force.lateral) || !isfinite(candidate.force.longitudinal) ||
        !isfinite(candidate.saturation)) return -1;
    *out = candidate;
    return 0;
}
