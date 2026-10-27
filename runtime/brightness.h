// Port Brightness: X360 slider position, independent of sun phase and post processing.
#pragma once
#include <math.h>
#include <stdint.h>

#include "gen/x360_engine.inc"   // X360E_BRIGHT_LOW / _HIGH: the slider's gamma exponent at 1 and at 0 (the player's default.xex)
// The slider position where the exponent is 1 (no change).
#define BRIGHTNESS_IDENTITY ((float)((X360E_BRIGHT_HIGH - 1.0) / (X360E_BRIGHT_HIGH - X360E_BRIGHT_LOW)))
#define BRIGHTNESS_IDENTITY_PCT ((int)(BRIGHTNESS_IDENTITY * 100.0f + 0.5f))   // the F10 percentage that selects identity
static inline double brightness_exponent(float s) { return s * X360E_BRIGHT_LOW + (1.0 - s) * X360E_BRIGHT_HIGH; }
static inline void brightness_ramp(float s, uint16_t ramp[768]) {
    double exponent = brightness_exponent(s);
    for (int i = 0; i < 256; ++i) {
        uint16_t v = (uint16_t)(pow(i / 255.0, exponent) * 65535.0 + 0.5);
        ramp[i] = ramp[256 + i] = ramp[512 + i] = v;
    }
}
float brightness_get(void);
void brightness_preview(float s);
void brightness_save(void);
void brightness_init(void);               // first present: apply the saved value (default identity)
