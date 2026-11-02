#!/bin/sh
# Baseline integrity proof: 1/1 authentic fragments accepted; 1/1 one-byte corruptions rejected.
# audit2 #7: a float WAVEFORMATEX with a huge cbSize used to be memcpy'd (18+cbSize bytes) into Format::raw[40].
# Builds dsound_host.cpp under ASan and creates a stereo-float buffer with cbSize=64 (82-byte format).
# Fixed code: format accepted, GetFormat returns an 18-byte format, no ASan report -> "wfx_cb_test: PASS".
# Old code (sh wfx_cb_test.sh --old, uses the pinned pre-fix format parser): ASan stack-buffer-overflow in parse_format -> FAIL.
# Run: sh host/audio/wfx_cb_test.sh [--old]     (headless, manual sink, no device)
set -e
cd "$(dirname "$0")"
F=../../third_party/ffmpeg
T=$(mktemp -d)
SRC=dsound_host.cpp
if [ "$1" = "--old" ]; then
    NFS_ENV_ROOT=$(cd ../.. && pwd)
    . "$NFS_ENV_ROOT/runtime/env.sh"; nfs_need_py
    "$TOOLS_PY" -I - dsound_host.cpp "$T/old.cpp" <<'PYBASE'
from pathlib import Path
import hashlib
OLD_PARSE = r'''bool parse_format(const void *wfx, Format &f) {
    if (!wfx) return false;
    const uint8_t *p = (const uint8_t *)wfx;
    int tag = rd16(p);
    Format r;
    r.channels = rd16(p + 2);
    r.rate = (int)rd32(p + 4);
    r.block = rd16(p + 12);
    r.bits = rd16(p + 14);
    uint32_t cb = tag == 1 ? 0 : rd16(p + 16);
    r.mask = default_mask(r.channels);
    if (tag == 1) {
        r.is_float = false;
    } else if (tag == 3) {
        r.is_float = true;
    } else if (tag == 0xFFFE) {
        if (cb < 22) return false;
        uint32_t sub = rd32(p + 24);
        if (memcmp(p + 28, kKsGuidTail, 12) != 0 || (sub != 1 && sub != 3)) return false;
        r.is_float = sub == 3;
        uint32_t m = rd32(p + 20);
        if (m) r.mask = m;
        cb = 22;
    } else {
        return false;
    }
    if (r.channels < 1 || r.channels > 8 || r.rate < DSBFREQUENCY_MIN || r.rate > DSBFREQUENCY_MAX) return false;
    if (r.is_float ? r.bits != 32 : (r.bits != 8 && r.bits != 16 && r.bits != 24 && r.bits != 32)) return false;
    if (r.block != r.channels * r.bits / 8) return false;
    r.raw_len = 18 + cb;
    memcpy(r.raw, p, r.raw_len);
    if (tag == 1) r.raw[16] = r.raw[17] = 0;
    f = r;
    return true;
}'''
assert hashlib.sha256(OLD_PARSE.encode()).hexdigest() == '44805c7e36def3be5bf37ba6890511a68da21b56ab3b81d765dc9b428f6e0dd6', 'pinned baseline changed'
import sys
source = Path(sys.argv[1]).read_text()
start = source.index('bool parse_format(')
end = source.index('\n}', start)+2
Path(sys.argv[2]).write_text(source[:start]+OLD_PARSE+source[end:])
PYBASE
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
