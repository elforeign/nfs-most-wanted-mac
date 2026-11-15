#include "car_grip_force.h"
#include <float.h>
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

static int unit(float x) { return isfinite(x) && x >= 0 && x <= 1; }
static int fits(double x) { return isfinite(x) && fabs(x) <= FLT_MAX; }

int car_grip_force_ordinary_demand(const CarGripForceDemand *s,
                                  CarGripForceVector *out) {
    if (!out) return -1;
    memset(out, 0, sizeof *out);
    if (!s || !unit(s->prior_saturation) || !boolean(s->locked) ||
        !isfinite(s->sliding.lateral) || !isfinite(s->sliding.longitudinal) ||
        !isfinite(s->lateral_velocity) || !finite_nonnegative(s->curve_force) ||
        !finite_nonnegative(s->lateral_gain) || !isfinite(s->wheel_torque) ||
        !isfinite(s->radius) || s->radius <= 0 ||
        !isfinite(s->angular_acceleration) || !isfinite(s->road_acceleration) ||
        !isfinite(s->inertia) || s->inertia <= 0) return -1;
    double longitudinal = (double)s->wheel_torque / s->radius;
    double lateral;
    if (s->prior_saturation < 1 || s->locked) {
        double sliding = s->sliding.longitudinal;
        if (longitudinal * sliding > 0 && fabs(longitudinal) < fabs(sliding)) {
            sliding = longitudinal;
        }
        longitudinal = sliding;
        lateral = s->sliding.lateral;
    } else {
        lateral = s->lateral_velocity > 0 ? -s->curve_force : s->curve_force;
    }
    lateral *= s->lateral_gain;
    if (s->prior_saturation >= 1 && !s->locked) {
        longitudinal += ((double)s->angular_acceleration -
                         (double)s->road_acceleration / s->radius) * s->inertia;
    }
    if (!fits(lateral) || !fits(longitudinal)) return -1;
    *out = (CarGripForceVector){(float)lateral, (float)longitudinal};
    return 0;
}

int car_grip_force_ordinary_finish(const CarGripForceOrdinary *s,
                                  CarGripForceOrdinaryResult *out) {
    if (!out) return -1;
    memset(out, 0, sizeof *out);
    if (!s) return -1;
    const float finite[] = {s->demand.lateral, s->demand.longitudinal, s->load,
        s->speed, s->slip, s->lateral_velocity, s->forward_velocity,
        s->slip_angle_turns, s->angle_correction, s->wheel_torque, s->omega,
        s->angular_acceleration, s->road_acceleration, s->previous_feedback};
    const float positive[] = {s->radius, s->inertia, s->dt};
    const float nonnegative[] = {s->static_grip, s->grip_multiplier, s->speed_gain,
        s->static_gain, s->slip_threshold, s->surface_lateral,
        s->surface_longitudinal, s->grip_scale, s->rolling_friction, s->surface_rolling};
    for (unsigned i = 0; i < sizeof finite / sizeof *finite; ++i)
        if (!isfinite(finite[i])) return -1;
    for (unsigned i = 0; i < sizeof positive / sizeof *positive; ++i)
        if (!isfinite(positive[i]) || positive[i] <= 0) return -1;
    for (unsigned i = 0; i < sizeof nonnegative / sizeof *nonnegative; ++i)
        if (!finite_nonnegative(nonnegative[i])) return -1;
    if (!unit(s->throttle) || !unit(s->brake) || !unit(s->ebrake) ||
        !boolean(s->locked) || (s->forward_velocity > 1 && s->grip_scale == 0)) return -1;

    /* 006EE920 ordinary branch. Full-throttle launch:
     * signed speed < 2, throttle > .999999, both brakes < 1e-6, gear >= 2.
     * This replaces the earlier solver's torque-direction 1.5 / 2/3 terms.
     */
    int launch = s->speed < 2.0f && s->throttle > 0.999999f &&
                 s->brake < 1e-6f && s->ebrake < 1e-6f && s->gear >= 2;
    double capacity = (double)s->static_gain * s->grip_multiplier * s->static_grip *
                      fmax(0, s->load) * s->speed_gain;
    double stored_slip = s->slip;
    if (launch) {
        capacity *= 0.2f;
        stored_slip *= 10.0;
    }
    double lateral = s->demand.lateral, longitudinal = s->demand.longitudinal;
    double magnitude = hypot(lateral, longitudinal);
    double cap_scale = 1, saturation = 1, threshold = s->slip_threshold;
    if (magnitude > capacity && magnitude > 0.001f) {
        cap_scale = capacity / magnitude;
        saturation = cap_scale;
        if (launch) saturation *= 0.2f;
        lateral *= cap_scale;
        longitudinal *= cap_scale;
        if (launch) longitudinal *= 6.0;
        threshold *= cap_scale * cap_scale;
    }
    if (threshold < fabs((double)s->slip)) {
        saturation *= threshold / fabs((double)s->slip);
    }
    /* Slip fraction precedes surfaces; torque feedback below follows surfaces
     * AND the signed road-speed / angle correction.
     */
    lateral *= s->surface_lateral;
    longitudinal *= s->surface_longitudinal;
    if (s->forward_velocity <= 1.0f) {
        lateral *= fmin(1.0, fabs((double)s->lateral_velocity));
    } else {
        longitudinal -= ((double)s->angle_correction / s->grip_scale) *
            sin((double)s->slip_angle_turns * 6.2831855f) * lateral;
    }
    double feedback = s->previous_feedback;
    double acceleration = s->angular_acceleration;
    if (s->locked) {
        acceleration = 0;
    } else {
        if (saturation < 1) {
            feedback = ((double)s->wheel_torque - longitudinal * s->radius + feedback) * 0.5;
            acceleration = (feedback - (double)s->rolling_friction *
                            s->surface_rolling * s->omega) / s->inertia -
                           stored_slip * saturation / ((double)s->dt * s->radius);
        }
        acceleration += ((double)s->road_acceleration / s->radius - acceleration) * saturation;
    }
    if (!fits(lateral) || !fits(longitudinal) || !fits(cap_scale) ||
        !fits(saturation) || !fits(stored_slip) || !fits(feedback) || !fits(acceleration)) return -1;
    CarGripForceOrdinaryResult result = {
        .force = {(float)lateral, (float)longitudinal}, .cap_scale = (float)cap_scale,
        .saturation = (float)saturation, .stored_slip = (float)stored_slip,
        .feedback = (float)feedback, .angular_acceleration = (float)acceleration,
        .launch = launch
    };
    *out = result;
    return 0;
}
