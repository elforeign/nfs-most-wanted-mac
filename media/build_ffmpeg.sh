#!/bin/sh
# Builds the static FFmpeg 7.1.1 that port/media links (into port/third_party/ffmpeg).
#   media/build_ffmpeg.sh            configure + make + install (a few minutes)
# Source: port/third_party/dl/ffmpeg-7.1.1.tar.xz, SHA-256 below (unpacked into port/third_party/src once).
# Only the components the port uses are enabled. Since the Dolby Digital Plus output (media.h Ac3Spdif::open_eac3) the eac3
# encoder and decoder/demuxer are in too, and since the TrueHD output (TrueHdMat) the truehd encoder and decoder (the decoders
# only for mediatest's offline round trips).
# The xma1 decoder (inside wmaprodec.c) decodes the X360 sound banks at load time (runtime/x360abk.c).
set -e
cd "$(dirname "$0")/.."
T="$PWD/third_party"
SHA=733984395e0dbbe5c046abda2dc49a5544e7e0e1e2366bba849222ae9e3a03b1
ARCHIVE="$T/dl/ffmpeg-7.1.1.tar.xz"
# Check even when an extracted source directory exists: it is not a substitute
# for the pinned dependency shipped in Setup.
[ -f "$ARCHIVE" ] || {
  echo "FFmpeg source missing: $ARCHIVE. Download Setup again (no dependency is downloaded by this script)." >&2
  exit 1
}
echo "$SHA  $ARCHIVE" | shasum -a 256 -c - >/dev/null 2>&1 || {
  echo "Dependency checksum failed: FFmpeg 7.1.1. Download Setup again." >&2
  exit 1
}
[ -d "$T/src/ffmpeg-7.1.1" ] || {
  mkdir -p "$T/src" && tar -xJf "$ARCHIVE" -C "$T/src"
}
cd "$T/src/ffmpeg-7.1.1"
./configure --prefix="$T/ffmpeg" --enable-static --disable-shared --disable-doc --disable-network --disable-autodetect \
  --enable-zlib --disable-everything \
  --enable-decoder='wmv3,wmapro,xma1,wmav2,vp6,vp6f,vp6a,adpcm_ea,adpcm_ea_r1,adpcm_ea_r2,adpcm_ea_r3,adpcm_ea_xas,pcm_s16le,pcm_f32le,ac3,eac3,truehd,mp3' \
  --enable-demuxer='asf,ea,wav,ac3,eac3,spdif' \
  --enable-encoder='ac3,eac3,truehd,pcm_s16le,png,rawvideo' \
  --enable-muxer='spdif,wav,ac3,eac3,image2,rawvideo,null' \
  --enable-parser='ac3,mlp,vc1' --enable-protocol='file,pipe' \
  --enable-filter='aresample,scale,format,null,anull,aformat,pan' \
  --enable-swscale --enable-swresample --enable-ffmpeg --enable-ffprobe > "$T/ffmpeg-configure.log"
make -j"${JOBS:-6}" > "$T/ffmpeg-make.log" 2>&1
make install >> "$T/ffmpeg-make.log" 2>&1
echo "ok: $T/ffmpeg"
