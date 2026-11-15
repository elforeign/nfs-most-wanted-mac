#include "car_grip_wheel.h"
#include <float.h>
#include <math.h>
#include <string.h>

static int overlay_valid(const CarGripWheelOverlay *s) {
    if (!s || s->yaw_count < 1 || s->yaw_count > 4) return 0;
    for (unsigned i = 0; i < 2; ++i) {
        if (!isfinite(s->dynamic_grip[i]) || s->dynamic_grip[i] < 0 ||
            !isfinite(s->static_grip[i]) || s->static_grip[i] < 0 ||
            !isfinite(s->grip_scale[i]) || s->grip_scale[i] < 0) return 0;
    }
    for (size_t i = 0; i < s->yaw_count; ++i)
        if (!isfinite(s->yaw_control[i])) return 0;
    return 1;
}

int car_grip_wheel_overlay(const CarGripWheelTyre *native,
                           const CarGripWheelOverlay *source, CarGripWheelTyre *out) {
    if (!out) return -1;
    /* Snapshot before clearing: in-place overlay on an owned shadow is valid. */
    CarGripWheelTyre saved = {0};
    CarGripWheelOverlay replacement = {0};
    if (native) saved = *native;
    if (source) replacement = *source;
    memset(out, 0, sizeof *out);
    if (!native || !overlay_valid(&replacement) ||
        !isfinite(saved.steering[0]) || !isfinite(saved.steering[1]) ||
        !isfinite(saved.yaw_speed)) return -1;
    saved.grip = replacement;
    for (size_t i = replacement.yaw_count; i < 4; ++i) saved.grip.yaw_control[i] = 0;
    *out = saved;
    return 0;
}

int car_grip_wheel_ordinary(const CarGripWheelOverlay *source,
                            const CarGripWheelInput *in, CarGripForceOrdinaryResult *out) {
    if (!out) return -1;
    memset(out, 0, sizeof *out);
    if (!overlay_valid(source) || !in || in->axle > 1 ||
        !isfinite(in->curve_force_per_grip) || in->curve_force_per_grip < 0) return -1;
    const CarGripForceSliding *s = &in->sliding;
    const CarGripForceDemand *d = &in->demand;
    const CarGripForceOrdinary *f = &in->finish;
    if (s->load != f->load || s->lateral_velocity != d->lateral_velocity ||
        s->lateral_velocity != f->lateral_velocity || s->forward_velocity != f->forward_velocity ||
        s->wheel_slip != f->slip || s->speed != f->speed ||
        s->grip_multiplier != f->grip_multiplier || d->locked != f->locked ||
        d->radius != f->radius || d->inertia != f->inertia ||
        d->wheel_torque != f->wheel_torque || d->angular_acceleration != f->angular_acceleration ||
        d->road_acceleration != f->road_acceleration) return -1;
    /* Use the same gain for the dynamic and static stages. This duplicated
     * scalar ramp is checked against the independent G2 sliding oracle.
     */
    double gain = 1;
    if (s->speed_sensitive && s->forward_rotation && !s->locked) {
        double t = fmin(1, fmax(0, ((double)s->speed - 13.4109f) * 0.111849315f));
        gain = 0.85f + 0.14999998f * t;
    }
    if (!isfinite(f->speed_gain) || fabs((double)f->speed_gain - gain) > 1e-7) return -1;
    CarGripForceSliding sliding = *s;
    sliding.dynamic_grip = source->dynamic_grip[in->axle];
    CarGripForceDemand demand = *d;
    if (car_grip_force_sliding(&sliding, &demand.sliding)) return -1;
    double curve = (double)in->curve_force_per_grip * source->grip_scale[in->axle];
    if (!isfinite(curve) || curve > FLT_MAX) return -1;
    demand.curve_force = (float)curve;
    CarGripForceOrdinary finish = *f;
    finish.static_grip = source->static_grip[in->axle];
    finish.grip_scale = source->grip_scale[in->axle];
    if (car_grip_force_ordinary_demand(&demand, &finish.demand)) return -1;
    return car_grip_force_ordinary_finish(&finish, out);
}
