#ifndef CAR_AUDIO_H
#define CAR_AUDIO_H
#include "host.h"

/* Optional Mac sidecar. Calls run under the guest lock, including packet
 * callbacks. Off returns before resource access or guest writes. */
int car_audio_enabled(void);
int car_audio_load(uint32_t key, uint64_t vault, const char *name, const char *filename);
int car_audio_load_stream(uint32_t key, uint64_t vault, const char *name, unsigned version, FILE *stream);
void car_audio_unload(uint64_t vault);
int car_audio_open(const char *name);
int car_audio_dispatch(Cpu *, uint32_t target);
int car_audio_code_region(uint32_t address);
void car_audio_tick(Cpu *, uint32_t object);
#endif
