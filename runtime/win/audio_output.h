#pragma once
#include <stddef.h>
#include <stdint.h>
#include "../../host/audio/dsound_host.h"
#ifdef __cplusplus
extern "C" {
#endif
// Endpoints consume six interleaved float channels, FL FR FC LFE BL BR.
// write returns success only when the whole block was accepted. The output
// retains ownership of samples until write returns; an async endpoint copies.
typedef struct WinPcmEndpoint {
    void *user;
    int (*open)(void *, unsigned rate, unsigned channels, uint32_t mask);
    int (*write)(void *, const float *, size_t frames);
    size_t (*queued)(void *);
    void (*close)(void *);
} WinPcmEndpoint;
typedef struct WinAudioOutput WinAudioOutput;
WinPcmEndpoint win_pcm_endpoint(void);
WinPcmEndpoint win_pcm_null_endpoint(void);
WinAudioOutput *win_audio_output_create(DsDevice *, const WinPcmEndpoint *, int threaded);
// Manual pump: same render/write path as the output thread. No wall clock.
DsResult win_audio_output_pump(WinAudioOutput *, size_t frames);
void win_audio_output_destroy(WinAudioOutput *);
#ifdef __cplusplus
}
#endif
