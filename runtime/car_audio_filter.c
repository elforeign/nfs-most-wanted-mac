#include "car_audio_filter.h"

#include <float.h>
#include <math.h>

/* Float constants and coefficient stores decoded at 008AC86B/008AC8DD.
 * Double intermediates approximate x87's extended arithmetic; stored
 * coefficients/history are float32. This is not a bit-exact x87 emulator.
 */
static const float TAU = 6.2831854820251465f;
static const float MIN_OMEGA = 0.0031415929552167654f;
static const float BYPASS_OMEGA = 3.138451099395752f;

void car_audio_filter_reset(CarAudioFilter *f) {
    if (!f) return;
    f->x1 = f->x2 = f->y1 = f->y2 = 0.0f;
}

int car_audio_filter_set_cutoff(CarAudioFilter *f, float cutoff_hz) {
    if (!f || !isfinite(f->sample_rate) || f->sample_rate <= 0.0f ||
        !isfinite(cutoff_hz)) return -1;

    double angle = ((double)cutoff_hz / f->sample_rate) * TAU;
    f->cutoff_hz = cutoff_hz;
    /* The source compares the unrounded angle to the upper bound first. */
    if (angle >= BYPASS_OMEGA) {
        /* Reset follows the previous cached angle, including rounding at the
         * upper boundary; it does not simply follow the prior active flag. */
        if (f->omega < BYPASS_OMEGA) car_audio_filter_reset(f);
        f->active = 0;
        f->omega = angle > FLT_MAX ? INFINITY : (float)angle;
        return 0;
    }
    float omega = angle < -FLT_MAX ? -INFINITY : (float)angle;
    if (omega < MIN_OMEGA) omega = MIN_OMEGA;
    if (!f->active || omega != f->omega) {
        double cosine = (float)cos((double)omega);
        double half_sine = 0.5 * sin((double)omega);
        double denominator = 1.0 + half_sine;
        double reciprocal = 1.0 / denominator;
        /* 008AC8B3-B6 subtracts the stored cosine, then FST m32 keeps
         * the full numerator on the x87 stack for b0/b2. Only b1 reloads
         * that rounded float copy (008AC8C6). */
        double numerator = 1.0 - cosine;
        f->a1 = (float)(-2.0 * cosine * reciprocal);
        f->a2 = (float)((1.0 - (float)half_sine) * reciprocal);
        f->b0 = (float)(numerator / (2.0 * (float)denominator));
        f->b1 = (float)((float)numerator * reciprocal);
        f->b2 = f->b0;
        /* Retain history across active coefficient changes. */
    }
    f->omega = omega;
    f->active = 1;
    return 0;
}

int car_audio_filter_init(CarAudioFilter *f, float sample_rate) {
    if (!f || !isfinite(sample_rate) || sample_rate <= 0.0f) return -1;
    *f = (CarAudioFilter){.sample_rate = sample_rate};
    return car_audio_filter_set_cutoff(f, 1000000.0f);
}

int car_audio_filter_process(CarAudioFilter *f, const float *input,
                             float *output, size_t count) {
    if (!f || !isfinite(f->sample_rate) || f->sample_rate <= 0.0f ||
        (count && (!input || !output))) return -1;
    for (size_t i = 0; i < count; ++i) {
        float x = input[i];
        if (!f->active) {
            output[i] = x;
            continue;
        }
        /* 008AC988 adds this tiny DC bias to suppress denormals. */
        double y = ((double)f->x1 * f->b1 + (double)x * f->b0 +
                    (double)f->x2 * f->b2 + 1.000000045813705e-18f) -
                   (double)f->y1 * f->a1 - (double)f->y2 * f->a2;
        float sample = (float)y;
        output[i] = sample;
        f->x2 = f->x1;
        f->x1 = x;
        f->y2 = f->y1;
        f->y1 = sample;
    }
    return 0;
}
