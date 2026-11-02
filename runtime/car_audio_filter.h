#ifndef CAR_AUDIO_FILTER_H
#define CAR_AUDIO_FILTER_H

#include <stddef.h>

/* Dormant Mac helper for one source mono voice; no mixer/hook registration.
 * Windows and existing donor audio have no dependency on this helper.
 * Caller owns the state and serializes all calls. Fields are read-only to the
 * caller after init; there is no allocation, device access or global state.
 */
typedef struct {
    float sample_rate, cutoff_hz, omega;
    float a1, a2, b0, b1, b2;
    float x1, x2, y1, y2;
    int active;
} CarAudioFilter;

/* Finite, positive sample rate in Hz. Initializes zero history and the source
 * default cutoff of 1,000,000 Hz (bypass at ordinary audio sample rates).
 * init/set/process return 0 on success, -1 on invalid arguments; errors leave
 * state and output untouched. Reinitialization deliberately drops history.
 */
int car_audio_filter_init(CarAudioFilter *, float sample_rate);

/* Finite cutoff in Hz, including zero/negative values (source minimum clamp).
 * omega = (cutoff/sample_rate)*float32(2*pi); minimum .003141593 radians.
 * At omega >= 3.1384511, bypass. History clears when the previous cached angle
 * is below that bound (a just-below active angle can round up to the bound).
 * Active coefficient changes retain history; bypass never accumulates it.
 */
int car_audio_filter_set_cutoff(CarAudioFilter *, float cutoff_hz);

/* Clear the four history samples, retaining rate/cutoff/coefficients and mode.
 * NULL is a no-op. Use on voice reuse/discontinuity, not on every block/update.
 */
void car_audio_filter_reset(CarAudioFilter *);

/* Finite float PCM, no clipping. Exact in-place operation or disjoint buffers
 * are supported; other overlap is outside the contract. count==0 is a no-op
 * and permits NULL buffers. A valid initialized state is always required.
 * Arbitrary block boundaries retain history; a bypass copies PCM unchanged.
 */
int car_audio_filter_process(CarAudioFilter *, const float *input,
                             float *output, size_t count);

#endif
