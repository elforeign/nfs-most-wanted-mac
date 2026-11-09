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
# There is no configure switch for just the arm64 AC-3 DSP optimizations.
# FFmpeg 7.1.1 omits GPL guards on these two files. Patch only the extracted
# sources: keep the pinned archive intact and the LGPL C DSP implementation.
# The call-site guard is necessary too: omitting the objects alone cannot link.
"${TOOLS_PY:-$(cd "$root/.." && pwd)/tools/.venv/bin/python}" -I - <<'PY'
from pathlib import Path

def replace_once(path, old, new):
    text = path.read_text()
    if text.count(new) == 1 and old not in text.replace(new, ''):
        return  # Repeated Setup builds use the same extracted tree.
    if text.count(old) != 1:
        raise SystemExit(f'Unexpected FFmpeg source: {path}: GPL guard not applied')
    path.write_text(text.replace(old, new))

makefile = Path('libavcodec/aarch64/Makefile')
for entry in (
    'OBJS-$(CONFIG_AC3DSP)                   += aarch64/ac3dsp_init_aarch64.o',
    'NEON-OBJS-$(CONFIG_AC3DSP)              += aarch64/ac3dsp_neon.o',
):
    replace_once(makefile, entry + '\n', 'ifdef CONFIG_GPL\n' + entry + '\nendif\n')
replace_once(Path('libavcodec/ac3dsp.c'),
             '#if ARCH_AARCH64\n    ff_ac3dsp_init_aarch64(c);',
             '#if ARCH_AARCH64 && CONFIG_GPL\n    ff_ac3dsp_init_aarch64(c);')
PY
# Remove stale members on incremental builds: Make does not notice removed
# prerequisites. Also discard the excluded objects and their dependency output.
rm -f libavcodec/libavcodec.a libavcodec/aarch64/ac3dsp_init_aarch64.o \
  libavcodec/aarch64/ac3dsp_init_aarch64.d libavcodec/aarch64/ac3dsp_neon.o \
  libavcodec/aarch64/ac3dsp_neon.d
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
# mpegts below selects adts/latm muxers and H.264/HEVC/VVC mp4toannexb + aac_adtstoasc bitstream filters; network stays disabled.
prefix="$T/ffmpeg"
[ "$release" = 0 ] || prefix=/ffmpeg  # Do not embed the build machine's path in shipped metadata.
./configure --prefix="$prefix" --enable-static --disable-shared --disable-doc --disable-network --disable-autodetect \
  --enable-zlib --disable-everything \
  --enable-decoder='wmv3,wmapro,xma1,wmav2,vp6,vp6f,vp6a,adpcm_ea,adpcm_ea_r1,adpcm_ea_r2,adpcm_ea_r3,adpcm_ea_xas,pcm_s16le,pcm_f32le,ac3,eac3,truehd,mp3' \
  --enable-demuxer='asf,ea,wav,ac3,eac3,spdif' \
  --enable-encoder='ac3,eac3,truehd,pcm_s16le,png,rawvideo' \
  --enable-muxer='spdif,wav,ac3,eac3,image2,rawvideo,null,mpegts' \
  --enable-parser='ac3,mlp,vc1' --enable-protocol='file,pipe' \
  --enable-filter='aresample,scale,format,null,anull,aformat,pan' \
  --enable-swscale --enable-swresample --enable-ffmpeg --enable-ffprobe "$@" > "$T/ffmpeg-configure.log"
make -j"${JOBS:-6}" > "$T/ffmpeg-make.log" 2>&1
if [ "$release" = 1 ]; then
  # FFmpeg enables compiler debug data by default. Remove build-machine paths
  # from both the archives and their matching objects before the source audit.
  # Component selection and the actual configure line remain unchanged.
  find libavcodec libavdevice libavfilter libavformat libavutil libswresample libswscale \
    -type f \( -name '*.o' -o -name '*.a' \) -exec strip -S {} +
  make install DESTDIR="$T" >> "$T/ffmpeg-make.log" 2>&1
else
  make install >> "$T/ffmpeg-make.log" 2>&1
fi
"${TOOLS_PY:-$(cd "$root/.." && pwd)/tools/.venv/bin/python}" -I "$root/media/check_ffmpeg_sources.py" \
  > "$T/ffmpeg-sources.log" 2>&1 || { tail -30 "$T/ffmpeg-sources.log"; exit 1; }
if [ "$release" = 1 ]; then
  "${TOOLS_PY:-$(cd "$root/.." && pwd)/tools/.venv/bin/python}" -I "$root/media/check_libraries.py" --group ffmpeg
fi
echo "ok: $T/ffmpeg"
