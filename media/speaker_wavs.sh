#!/bin/sh
# Writes the test files for `mediatest speakers <dir>`: 0.wav .. 5.wav, mono 16-bit 48 kHz. Spoken names from macOS
# `say` (to a file; nothing is played) for L R C Ls Rs, and a 1 s 45 Hz tone for the LFE (played three times when only that channel is asked for).
#   media/speaker_wavs.sh <dir>
set -e
[ -n "$1" ] || { echo "usage: $0 <dir>" >&2; exit 2; }
here="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$1"
i=0
for name in "front left" "front right" "centre" "" "surround left" "surround right"; do
  if [ $i -eq 3 ]; then
    "$here/mediatest" tone "$1/3.wav" 45 1 0.5 > /dev/null
  else
    say -o "$1/$i.wav" --file-format=WAVE --data-format=LEI16@48000 "$name"
  fi
  i=$((i + 1))
done
echo "ok: $1"
