#ifndef CAR_AUDIO_TMX_H
#define CAR_AUDIO_TMX_H
#include <stddef.h>
#include <stdint.h>

typedef struct {
    uint32_t offset, size, frames, rate, loop;
    float frequency;
    int gin;
} CarAudioTmxTrack;
typedef struct { unsigned curve, input; float low, range, minimum, maximum; } CarAudioTmxControl;
typedef struct { unsigned track, curve; float start, width, gain; } CarAudioTmxEnd;
typedef struct {
    unsigned input, count, pairs, dependencies[4];
    CarAudioTmxEnd ends[6];
} CarAudioTmxBin;
typedef struct {
    unsigned track_count, gin_count, snr_count;
    CarAudioTmxTrack tracks[6];
    CarAudioTmxControl controls[4];
    CarAudioTmxBin bins[4];
} CarAudioTmx;

/* The reviewed v1000 profiles: three to six resident/Ginsu tracks.
 * Borrows no storage, allocates nothing, and never opens an audio device. */
int car_audio_tmx_parse(CarAudioTmx *, const uint8_t *, size_t);
int car_audio_tmx_gains(const CarAudioTmx *, const float inputs[5], float gains[6]);
#endif
