#!/bin/sh
# Builds mediatest against the static FFmpeg in port/third_party/ffmpeg.
set -e
cd "$(dirname "$0")"
F=../third_party/ffmpeg
clang++ -std=c++17 -O2 -Wall -I"$F/include" media.cpp mediatest.cpp -o mediatest \
  "$F/lib/libavformat.a" "$F/lib/libavcodec.a" "$F/lib/libswscale.a" "$F/lib/libswresample.a" "$F/lib/libavutil.a" \
  -lz -framework AudioToolbox -framework CoreAudio -framework CoreFoundation
