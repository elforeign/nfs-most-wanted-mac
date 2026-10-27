/* ctest.c - links libhostaudio.a from plain C, the way the boot runtime will: create a device, a 6-ch buffer,
 * play it and pull one block through the manual sink.
 * 2026-10-26: BR 0.1831 ok. Broken (dsound_host.cpp int16->float scale 1/16384): BR 0.3662 FAIL. Run by runtime/offline_tests.sh. */
#include <stdio.h>
#include <string.h>
#include "dsound_host.h"

int main(void) {
    DsConfig cfg;
    ds_config_default(&cfg);
    cfg.sink = DS_SINK_MANUAL;
    DsDevice *dev = NULL;
    if (ds_device_create(&cfg, &dev) != DS_OK) return 1;
    unsigned char wfx[18] = {1, 0, 6, 0, 0x80, 0xBB, 0, 0, 0, 0x65, 0x04, 0, 12, 0, 16, 0, 0, 0}; /* 6 ch 48 kHz */
    static short mem[6 * 480];
    for (int i = 0; i < 6 * 480; ++i) mem[i] = (short)(1000 * (i % 6 + 1));
    DsBuffer *buf = NULL;
    if (ds_device_create_buffer(dev, DSBCAPS_GETCURRENTPOSITION2, sizeof mem, wfx, mem, &buf) != DS_OK) return 2;
    ds_buffer_play(buf, DSBPLAY_LOOPING);
    /* The game-mix limiter (peak_limiter.h) delays the output by 88 frames, so the first frames are silence: render past
     * it and read frame 200 (the broken check, out[5] at frame 0, read 0 once the limiter was added). */
    float out[6 * 300];
    ds_device_render(dev, out, 300);
    unsigned play = 0, write = 0;
    ds_buffer_get_current_position(buf, &play, &write);
    float br = out[6 * 200 + 5];
    int ok = br == 6000 / 32768.0f && play == 300 * 12;
    printf("C link test: BR %.4f, play cursor %u bytes: %s\n", br, play, ok ? "ok" : "FAIL");
    ds_buffer_release(buf);
    ds_device_release(dev);
    return ok ? 0 : 3;
}
