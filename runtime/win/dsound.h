#pragma once
#include "host.h"
#include "audio_output.h"
int win_dsound_register(void);
// Install before creating guest devices. NULL selects the native PCM endpoint.
// A supplied endpoint and manual=1 never create a thread or touch a device.
void win_dsound_set_endpoint(const WinPcmEndpoint *, int manual);
DsResult win_dsound_render(uint32_t guest_device, size_t frames);
