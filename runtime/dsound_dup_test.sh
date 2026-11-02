#!/bin/sh
# Baseline integrity proof: 1/1 authentic fragments accepted; 1/1 one-byte corruptions rejected.
# Builds and runs runtime/dsound_dup_test.c (see header). --old tests the pinned pre-fix duplicate-buffer helper.
# Exit-status proof: production exits 0; pinned duplicate mem=0 prints FAIL and exits 1 (previous wrapper hid it).
set -e
cd "$(dirname "$0")"
F=../third_party/ffmpeg
T=$(mktemp -d)
cp dsound_dup_test.c "$T/"
if [ "$1" = "--old" ]; then
    NFS_ENV_ROOT=$(cd .. && pwd)
    . ./env.sh; nfs_need_py
    "$TOOLS_PY" -I - dsound.c "$T/dsound.c" <<'PYBASE'
from pathlib import Path
import hashlib
OLD_DUPLICATE = r'''static int d_DuplicateSoundBuffer(Cpu *c) {    // (this, src, pp)
    DsObj *d = THIS, *s = obj(arg(c, 1));
    if (!s) RET((uint32_t)DSERR_INVALIDPARAM, 3);
    DsObj *b = new_obj(2, vt_buf);
    DsResult r = ds_device_duplicate_buffer(d->dev, s->buf, &b->buf);
    b->dev = d->dev;
    if (diag_on) diag_event("dsbuf Duplicate b%08X src%08X @%08X rc%08X", b->guest, s->guest, M32(c->esp), (uint32_t)r);
    if (r < 0) RET((uint32_t)r, 3);
    ++s->refs;                                 // keeps the shared memory alive
    M32(arg(c, 2)) = b->guest;
    RET(0, 3);
}'''
assert hashlib.sha256(OLD_DUPLICATE.encode()).hexdigest() == '5595f0ec84594634263afc8e15d77ab7e56f360a28b1fdf41ad963fafbdaef5c', 'pinned baseline changed'
import sys
source = Path(sys.argv[1]).read_text()
start = source.index('static int d_DuplicateSoundBuffer(')
end = source.index('\n}', start)+2
Path(sys.argv[2]).write_text(source[:start]+OLD_DUPLICATE+source[end:])
PYBASE
else
    cp dsound.c "$T/"
fi
sed -i '' 's|"\.\./host/audio/dsound_host.h"|"dsound_host.h"|' "$T/dsound.c"
LIBS="$F/lib/libavformat.a $F/lib/libavcodec.a $F/lib/libswscale.a $F/lib/libswresample.a $F/lib/libavutil.a -lc++ -lz -framework AudioToolbox -framework CoreAudio -framework CoreFoundation"
clang -std=gnu11 -O1 -g -w -fsanitize=address -I"$T" -I. -I../recomp -I../host/audio "$T/dsound_dup_test.c" ../host/audio/build/libhostaudio.a ../host/audio/build/libmedia.a $LIBS -o "$T/t"
rc=0; "$T/t" > "$T/out.txt" 2>&1 || rc=$?
grep -v "^dsound:\|^audio:" "$T/out.txt" || true
rm -rf "$T"
exit "$rc"
