#!/bin/sh
# Baseline integrity proof: 1/1 authentic fragments accepted; 1/1 one-byte corruptions rejected.
# Builds and runs runtime/dsound_dup_test.c (see header). The controls restore SHA-256-pinned pre-fix fragments.
# Exit-status proof: production exits 0; pinned duplicate mem=0 prints FAIL and exits 1 (previous wrapper hid it).
set -e
cd "$(dirname "$0")"
F=../third_party/ffmpeg
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
cp dsound_dup_test.c "$T/"
host=../host/audio/build/libhostaudio.a
game_headers=.
game_define=
if [ "${1:-}" = "--old" ]; then
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
elif [ "${1:-}" = "--f1-control" ]; then
    NFS_ENV_ROOT=$(cd .. && pwd)
    . ./env.sh; nfs_need_py
    "$TOOLS_PY" -I - dsound.c ../host/audio/dsound_host.cpp "$T" <<'PYBASE'
from pathlib import Path
import hashlib
import sys

OLD_NEW_OBJ = r'''static DsObj *new_obj(int kind, uint32_t vt) {
    DsObj *o = calloc(1, sizeof *o);
    o->kind = kind;
    o->refs = 1;
    o->guest = galloc(16);
    M32(o->guest) = vt; M32(o->guest + 4) = nobjs; M32(o->guest + 8) = 0x0D50D0B1;
    objs = realloc(objs, (nobjs + 1) * sizeof *objs);
    objs[nobjs++] = o;
    return o;
}'''
OLD_RELEASE = r'''static int d_Release(Cpu *c) {
    DsObj *o = THIS;
    if (!o) RET(0, 1);
    if (--o->refs > 0) RET(o->refs, 1);
    if (diag_on) diag_event("dsbuf Release obj%08X kind%d t%.3f @%08X size%u",
                           o->guest, o->kind, host_ticks() / 1e9, M32(c->esp), o->bytes);
    if (o->kind == 1 && o->dev) { ds_device_release(o->dev); o->dev = NULL; }
    if (o->kind == 2 && o->buf) { ds_buffer_release(o->buf); o->buf = NULL; if (o->mem && !o->owner) gfree(o->mem); o->mem = 0; }
    if (o->kind == 2 && o->owner) {            // a duplicate drops its hold on the source (taken in Duplicate);
        DsObj *s = obj(o->owner);              // the last one out frees the source the game already released
        o->owner = 0;
        if (s && --s->refs == 0 && s->buf) { ds_buffer_release(s->buf); s->buf = NULL; if (s->mem) gfree(s->mem); s->mem = 0; }
    }
    if (o->kind == 3) { DsObj *b = obj(o->owner); if (b) --b->refs; }
    RET(0, 1);
}'''
OLD_UNLOCK = r'''DsResult ds_buffer_unlock(DsBuffer *b, uint32_t off1, uint32_t len1, uint32_t off2, uint32_t len2) {
    if (DsResult r = check_live(b)) return r;
    // The mixer reads the memory directly, so there is nothing to commit; just check the regions.
    if (b->primary) return DSERR_INVALIDCALL;
    if ((uint64_t)off1 + len1 > b->bytes || (len2 && (uint64_t)off2 + len2 > b->bytes)) return DSERR_INVALIDPARAM;
    return DS_OK;
}'''
OLD_READ = 'const uint8_t *s = b.mem + (size_t)i * b.fmt.block;'

def checked(fragment, digest):
    assert hashlib.sha256(fragment.encode()).hexdigest() == digest, 'pinned baseline changed'
    return fragment

def restore(source, fragment, digest):
    start = source.index(fragment.splitlines()[0])
    end = source.index('\n}', start) + 2
    return source[:start] + checked(fragment, digest) + source[end:]

src, host, out = map(Path, sys.argv[1:])
runtime = restore(src.read_text(), OLD_NEW_OBJ, 'f9af66b24bbd1be243a9b8b2f1799b3b2f0c6dd4cd63666e0404243ff99c5629')
runtime = restore(runtime, OLD_RELEASE, '75bb451b2a0417631506984c43f5d4650f78605a71ca6e4e5ab5a711f1998f0c')
audio = restore(host.read_text(), OLD_UNLOCK, '6810c2635bf400ab7476ac4ab89a5af865bb991602f6cf21544774c5ce2825b0')
read = 'const uint8_t *s = b.committed + (size_t)i * b.fmt.block;'
assert audio.count(read) == 1
audio = audio.replace(read, checked(OLD_READ, 'b6910f7330594e71653e2ab017159629bb213b77710cf3a168071b45e3e27195'))
(out / src.name).write_text(runtime)
(out / host.name).write_text(audio)
PYBASE
    host="$T/dsound_host.o"
    clang++ -std=c++17 -O2 -I../host/audio -c "$T/dsound_host.cpp" -o "$host"
elif [ "${1:-}" = "--carbon" ]; then
    game_headers=../recomp/out/carbon/runtime
    game_define=-DNFS_GAME=2
    cp "$game_headers/dsound.c" "$T/"
else
    cp dsound.c "$T/"
fi
sed -i '' 's|"\.\./host/audio/dsound_host.h"|"dsound_host.h"|' "$T/dsound.c"
LIBS="$F/lib/libavformat.a $F/lib/libavcodec.a $F/lib/libswscale.a $F/lib/libswresample.a $F/lib/libavutil.a -lc++ -lz -framework AudioToolbox -framework CoreAudio -framework CoreFoundation"
clang ${game_define:+"$game_define"} -std=gnu11 -O1 -g -w -fsanitize=address -I"$T" -I"$game_headers" -I. -I../recomp -I../host/audio "$T/dsound_dup_test.c" "$host" ../host/audio/build/libmedia.a $LIBS -o "$T/t"
if [ "${1:-}" = "--carbon" ]; then shift; fi
rc=0; "$T/t" "$@" > "$T/out.txt" 2>&1 || rc=$?
grep -v "^dsound:\|^audio:" "$T/out.txt" || true
exit "$rc"
