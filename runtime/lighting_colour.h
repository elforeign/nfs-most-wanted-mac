#ifndef LIGHTING_COLOUR_H
#define LIGHTING_COLOUR_H

#include <stdlib.h>
#include <strings.h>

// The explicit startup profile wins over the saved legacy lighting controls.
static inline int lighting_mode_d65(void) {
    static int selected = -1;
    int active = __atomic_load_n(&selected, __ATOMIC_RELAXED);
    if (active < 0) {
        const char *value = getenv("NFS_WORLD_LIGHTING");
        active = value && !strcasecmp(value, "neutral-d65");
        __atomic_store_n(&selected, active, __ATOMIC_RELAXED);
    }
    return active;
}

// Linear Rec.709 Y; no transfer-function decoding or gamut clipping.
static inline float lighting_colour_luminance(const float rgb[3]) {
    return 0.2126f * rgb[0] + 0.7152f * rgb[1] + 0.0722f * rgb[2];
}
// D65 has equal linear Rec.709 primaries. Keep the source luminance.
static inline void lighting_colour_d65(float rgb[3]) {
    float y = lighting_colour_luminance(rgb);
    rgb[0] = rgb[1] = rgb[2] = y;
}

// In-place desaturation toward Y. factor=0 retains colour; factor=1 is grey.
static inline void lighting_colour_desaturate(float rgb[3], float factor) {
    float y = lighting_colour_luminance(rgb);
    for (int i = 0; i < 3; ++i) rgb[i] += factor * (y - rgb[i]);
}

// Authored linear Rec.709 colour: mix 85% source + 15% D65 in XYZ at
// Y=1, then restore the input luminance. Intensity remains separate.
static inline void lighting_colour_adapt(float rgb[3]) {
    float y = lighting_colour_luminance(rgb);
    if (!(y > 0)) return;
    float X = (0.4124564f*rgb[0] + 0.3575761f*rgb[1] + 0.1804375f*rgb[2]) / y;
    float Z = (0.0193339f*rgb[0] + 0.1191920f*rgb[1] + 0.9503041f*rgb[2]) / y;
    X = 0.85f*X + 0.15f*0.95047f;
    Z = 0.85f*Z + 0.15f*1.08883f;
    rgb[0] = 3.2404542f*X - 1.5371385f - 0.4985314f*Z;
    rgb[1] = -0.9692660f*X + 1.8760108f + 0.0415560f*Z;
    rgb[2] = 0.0556434f*X - 0.2040259f + 1.0572252f*Z;
    float scale = y / lighting_colour_luminance(rgb);
    for (int i = 0; i < 3; ++i) rgb[i] *= scale;
}

// Source temperature in kelvin (2222..25000), separate from any sun curve.
// Planckian xy(T), 85% source + 15% D65 XYZ at Y=1, then linear Rec.709
// normalized to Rec.709 Y=1. Output may exceed 1; apply intensity separately.
static inline void lighting_colour_white(float t, float out[3]) {
    float inv = 1 / t, inv2 = inv * inv, inv3 = inv2 * inv;
    float x = t <= 4000 ? -0.2661239e9f * inv3 - 0.2343589e6f * inv2 + 0.8776956e3f * inv + 0.179910f
                         : -3.0258469e9f * inv3 + 2.1070379e6f * inv2 + 0.2226347e3f * inv + 0.240390f;
    float y = t <= 4000 ? -0.9549476f*x*x*x - 1.37418593f*x*x + 2.09137015f*x - 0.16748867f
                         : 3.0817580f*x*x*x - 5.87338670f*x*x + 3.75112997f*x - 0.37001483f;
    float X = 0.85f * x/y + 0.15f * 0.95047f;
    float Z = 0.85f * (1-x-y)/y + 0.15f * 1.08883f;
    out[0] = 3.2404542f*X - 1.5371385f - 0.4985314f*Z;
    out[1] = -0.9692660f*X + 1.8760108f + 0.0415560f*Z;
    out[2] = 0.0556434f*X - 0.2040259f + 1.0572252f*Z;
    float scale = 1 / lighting_colour_luminance(out);
    for (int i = 0; i < 3; ++i) out[i] *= scale;
}

#endif
