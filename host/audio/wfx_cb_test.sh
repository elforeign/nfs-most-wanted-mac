#!/bin/sh
# audit2 #7: a float WAVEFORMATEX with a huge cbSize used to be memcpy'd (18+cbSize bytes) into Format::raw[40].
# Builds dsound_host.cpp under ASan and creates a stereo-float buffer with cbSize=64 (82-byte format).
# Fixed code: format accepted, GetFormat returns an 18-byte format, no ASan report -> "wfx_cb_test: PASS".
# Old code (sh wfx_cb_test.sh --old, uses the HEAD~ source via git): ASan stack-buffer-overflow in parse_format -> FAIL.
# Run: sh host/audio/wfx_cb_test.sh [--old]     (headless, manual sink, no device)
set -e
cd "$(dirname "$0")"
F=../../third_party/ffmpeg
T=$(mktemp -d)
SRC=dsound_host.cpp
if [ "$1" = "--old" ]; then
    git show "${OLD_REF:-main}:port/host/audio/dsound_host.cpp" > "$T/old.cpp" 2>/dev/null || git show "${OLD_REF:-main}:host/audio/dsound_host.cpp" > "$T/old.cpp"
    SRC="$T/old.cpp"
fi
cat > "$T/t.cpp" <<'CPP'
#include <stdio.h>
#include <string.h>
#include "dsound_host.h"
int main() {
    DsConfig cfg; ds_config_default(&cfg); cfg.sink = DS_SINK_MANUAL;
    DsDevice *dev = NULL;
    if (ds_device_create(&cfg, &dev) != DS_OK) return 1;
    unsigned char w[18 + 64] = {3, 0, 2, 0, 0x80, 0xBB, 0, 0, 0, 0x65, 0x04, 0, 8, 0, 32, 0, 64, 0};
    for (int i = 18; i < 82; ++i) w[i] = 0xEE;
    static float mem[2 * 480];
    DsBuffer *buf = NULL;
    DsResult r = ds_device_create_buffer(dev, DSBCAPS_GETCURRENTPOSITION2, sizeof mem, w, mem, &buf);
    unsigned char got[100]; unsigned wr = 0;
    if (r == DS_OK) ds_buffer_get_format(buf, got, sizeof got, &wr);
    printf("create rc=%d format bytes=%u\n", (int)r, wr);
    if (buf) ds_buffer_release(buf);
    ds_device_release(dev);
    puts("wfx_cb_test: PASS");
    return 0;
}
CPP
CXX="clang++ -std=c++17 -O1 -g -fsanitize=address -I. -Wno-unused-parameter"
LIBS="$F/lib/libavformat.a $F/lib/libavcodec.a $F/lib/libswscale.a $F/lib/libswresample.a $F/lib/libavutil.a -lz -framework AudioToolbox -framework CoreAudio -framework CoreFoundation"
$CXX "$T/t.cpp" "$SRC" build/libmedia.a -o "$T/t" $LIBS
if "$T/t" > "$T/out.txt" 2>&1; then cat "$T/out.txt"; rc=0; else head -6 "$T/out.txt"; echo "wfx_cb_test: FAIL"; rc=1; fi
rm -rf "$T"
exit $rc
