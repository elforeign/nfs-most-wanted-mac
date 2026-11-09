#ifndef CAR_AUDIO_H
#define CAR_AUDIO_H
#include "host.h"

/* Optional Mac sidecar. Calls run under the guest lock, including packet
 * callbacks. Off returns before resource access or guest writes. */
int car_audio_enabled(void);
int car_audio_load(uint32_t key, const char *name, const char *filename);
void car_audio_unload(uint32_t key);
int car_audio_open(const char *name);
int car_audio_dispatch(Cpu *, uint32_t target);
int car_audio_code_region(uint32_t address);
void car_audio_tick(Cpu *, uint32_t object);
#endif
