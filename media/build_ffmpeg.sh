#!/bin/sh
# Builds the static FFmpeg 7.1.1 that port/media links (into port/third_party/ffmpeg).
#   media/build_ffmpeg.sh            configure + make + install (a few minutes)
#   media/build_ffmpeg.sh --release  clean rebuild for arm64/macOS 14.5, then check every archive
# Source: port/third_party/dl/ffmpeg-7.1.1.tar.xz, SHA-256 below (unpacked into port/third_party/src once).
# Only the components the port uses are enabled. Since the Dolby Digital Plus output (media.h Ac3Spdif::open_eac3) the eac3
# encoder and decoder/demuxer are in too, and since the TrueHD output (TrueHdMat) the truehd encoder and decoder (the decoders
# only for mediatest's offline round trips).
# The xma1 decoder (inside wmaprodec.c) decodes the X360 sound banks at load time (runtime/x360abk.c).
set -e
cd "$(dirname "$0")/.."
root=$PWD
release=0
case "${1:-}" in
  '') [ "$#" = 0 ] || exit 2 ;;
  --release) [ "$#" = 1 ] || exit 2; release=1 ;;
  *) echo 'usage: media/build_ffmpeg.sh [--release]' >&2; exit 2 ;;
esac
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
# Release must never reuse objects built for the invoking machine's default target.
# The default Setup build retains its existing configuration and incremental behavior.
set --
if [ "$release" = 1 ]; then
  [ ! -f ffbuild/config.mak ] || make distclean > "$T/ffmpeg-clean.log" 2>&1
  set -- --arch=aarch64 --target-os=darwin \
    '--cc=clang -arch arm64 -mmacosx-version-min=14.5' \
    '--cxx=clang++ -arch arm64 -mmacosx-version-min=14.5' \
    '--extra-cflags=-arch arm64 -mmacosx-version-min=14.5' \
    '--extra-ldflags=-arch arm64 -mmacosx-version-min=14.5'
fi
./configure --prefix="$T/ffmpeg" --enable-static --disable-shared --disable-doc --disable-network --disable-autodetect \
  --enable-zlib --disable-everything \
  --enable-decoder='wmv3,wmapro,xma1,wmav2,vp6,vp6f,vp6a,adpcm_ea,adpcm_ea_r1,adpcm_ea_r2,adpcm_ea_r3,adpcm_ea_xas,pcm_s16le,pcm_f32le,ac3,eac3,truehd,mp3' \
  --enable-demuxer='asf,ea,wav,ac3,eac3,spdif' \
  --enable-encoder='ac3,eac3,truehd,pcm_s16le,png,rawvideo' \
  --enable-muxer='spdif,wav,ac3,eac3,image2,rawvideo,null' \
  --enable-parser='ac3,mlp,vc1' --enable-protocol='file,pipe' \
  --enable-filter='aresample,scale,format,null,anull,aformat,pan' \
  --enable-swscale --enable-swresample --enable-ffmpeg --enable-ffprobe "$@" > "$T/ffmpeg-configure.log"
make -j"${JOBS:-6}" > "$T/ffmpeg-make.log" 2>&1
make install >> "$T/ffmpeg-make.log" 2>&1
if [ "$release" = 1 ]; then
  "${TOOLS_PY:-$(cd "$root/.." && pwd)/tools/.venv/bin/python}" -I "$root/media/check_libraries.py" --group ffmpeg
fi
echo "ok: $T/ffmpeg"
