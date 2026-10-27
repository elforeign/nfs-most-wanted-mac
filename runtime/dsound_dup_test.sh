#!/bin/sh
# Builds and runs runtime/dsound_dup_test.c (see header). --old tests the dsound.c from OLD_REF (default main).
set -e
cd "$(dirname "$0")"
F=../third_party/ffmpeg
T=$(mktemp -d)
cp dsound_dup_test.c "$T/"
if [ "$1" = "--old" ]; then git show "${OLD_REF:-main}:runtime/dsound.c" > "$T/dsound.c"; else cp dsound.c "$T/"; fi
sed -i '' 's|"\.\./host/audio/dsound_host.h"|"dsound_host.h"|' "$T/dsound.c"
LIBS="$F/lib/libavformat.a $F/lib/libavcodec.a $F/lib/libswscale.a $F/lib/libswresample.a $F/lib/libavutil.a -lc++ -lz -framework AudioToolbox -framework CoreAudio -framework CoreFoundation"
clang -std=gnu11 -O1 -g -w -fsanitize=address -I"$T" -I. -I../recomp -I../host/audio "$T/dsound_dup_test.c" ../host/audio/build/libhostaudio.a ../host/audio/build/libmedia.a $LIBS -o "$T/t"
rc=0; "$T/t" 2>&1 | grep -v "^dsound:\|^audio:" || rc=$?
rm -rf "$T"
