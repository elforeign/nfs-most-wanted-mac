#ifndef CAR_AUDIO_BACKEND_H
#define CAR_AUDIO_BACKEND_H
#include "car_audio_player.h"
#include "car_audio_filter.h"

/* Dormant Mac PCM backend. No registration, device, guest-memory access or
 * global pool. The embedding mixer serializes ALL calls and owns storage.
 * Mono finite float PCM is borrowed until retire; retire may be NULL. A
 * successful resolve transfers exactly one borrow, even on admission failure.
 */
typedef struct {
    const float *pcm;
    size_t frames;
    float rate;
    int loop;
    void (*retire)(void *);
    void *owner;
} CarAudioPCM;
typedef int (*CarAudioResolve)(void *, uint64_t bank, int16_t sample, CarAudioPCM *);

typedef struct {
    CarAudioPCM sample;
    CarAudioFilter filter;
    double cursor;
    uint64_t age;
    int32_t handle;
    float priority, pitch, volume, dry, wet;
    float pan[2];
    uint16_t base_angle;
    uint8_t owned, ended, paused;
} CarAudioVoice;
typedef struct {
    CarAudioVoice *voices;
    size_t capacity;
    float rate;
    CarAudioResolve resolve;
    void *context;
    uint64_t clock;
    uint32_t next_handle;
} CarAudioBackend;

/* Initialization requires fresh, unattached storage, capacity > 0. Destroy
 * before reinitializing. Handle zero is valid; handles never recycle within
 * a pool lifetime (exhaustion fails). No allocation in callbacks or render.
 */
int car_audio_backend_init(CarAudioBackend *, CarAudioVoice *, size_t capacity,
                           float output_rate, CarAudioResolve, void *context);
/* Call once after each source command flush (008993FA), independently of
 * PCM block size. Voices created in a flush share its epoch. No implicit tick
 * occurs in render. Returns -1 on uninitialized/exhausted epoch, else zero. */
int car_audio_backend_tick(CarAudioBackend *);
void car_audio_backend_destroy(CarAudioBackend *);
const CarAudioPlayerBackend *car_audio_backend_callbacks(void);

/* Stock PCM enters the SAME pool and allocator as source players. The caller
 * transfers sample ownership only on success. priority uses authored 0..100
 * units. Lowest priority wins, then source epoch age (preceding epoch maps
 * to zero), then insertion order for equal ages;
 * incoming priority does not gate resource-pressure stealing (008B007F).
 * There is no automatic attachment to the native guest pool in this helper.
 */
int car_audio_backend_stock(CarAudioBackend *, const CarAudioPCM *, uint8_t priority,
                            int32_t *handle);

/* Add interleaved stereo dry and wet SEND PCM to disjoint caller buffers;
 * either may be NULL to discard that bus. They are not cleared or clipped.
 * A wet send is not a reverb implementation. Rendering advances each voice
 * once even if both outputs are NULL. count==0 accepts NULL outputs.
 * Resampling is linear; output-rate biquad follows resampling, before sends.
 */
void car_audio_backend_render(CarAudioBackend *, float *dry, float *wet, size_t frames);
#endif
