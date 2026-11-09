#!/bin/sh
# clip_smoke.sh <scratch dir> [NAME=value ...] - unattended clip-recorder smoke: one guarded race (15.1.1, autodrive),
# the recorder started at sim 10 and stopped 15 s later (BOOT_KEYS step `cliprec`, the F10 Video clip row's action),
                                                                                                               
# refuses to run if that would be the real home), then runtime/clip_check.py on it: H.264/HEVC + AAC 48 kHz stereo,
# duration within one frame of the recorded span and 1 s of the window (start-up preparation takes ~0.4 s), tracks starting within 20 ms, non-silent
# audio; and no crash/hang/stall report. Prints `CLIPSMOKE PASS ...` or `CLIPSMOKE FAIL: <why>`.
# Guarded runs keep the window behind others, where Metal reports every drawable as not displayed (presentedTime 0,
# perf.tsv present_dropped = all frames), so the recorder would keep no frame; NFS_TEST_CLIP_HOSTTIME=1 (test only)
# stamps those frames with their handler's host time. NFS_AUDIO_FAKE_DEVICE=1 runs the mixer callbacks the recorder
# captures (NFS_AUDIO=null has none).
# Proof (same build): normal -> CLIPSMOKE PASS: 14.639 s HEVC + 1 audio (RMS 0.0924); 830 frames, drops 0/0/0/850;
#   NFS_TEST_CLIP_HOSTTIME=0 (the original state) -> CLIPSMOKE FAIL: not saved, 0 frames, 1706 unshown, "No presented
#   video frames". The removed-audio, rate, channel, skew, silence and duration failures are clip_check_test.py's.
# Regression proof (fake runner, no game): original catches 0/10 shell failures; fixed catches 10/10.
# clip_check_test.py also catches 6/6 per-track duration defects (original 0/6); 27/27 total checks pass.
# --replay <scratch dir>: Arm at sim 5, Save at sim 45, exactly one Replay MP4,
# checked at 30 seconds +/- one frame (including audio; no AAC startup allowance).
# Offline: 36/36 checks; two-export fault rejected (guard removed: accepted),
# 29.900-second replay rejected (recorder startup slack restored: accepted).
set -u
. "$(dirname "$0")/env.sh"; nfs_need_py
cd "$(dirname "$0")/.."
py=$TOOLS_PY
fail() { echo "CLIPSMOKE FAIL: $*"; exit 1; }
replay=0
if [ "${1:-}" = --replay ]; then replay=1; shift; fi
out=${1:?scratch dir}; shift
for a in "$@"; do
    case $a in HOME=*|CFFIXED_USER_HOME=*) fail "${a%%=*} is set by the smoke";; esac
done
mkdir -p "$out" && out=$(cd "$out" && pwd -P) || fail "cannot resolve scratch"
real=$(cd "$HOME" && pwd -P) || fail "cannot resolve real home"
# Resolve each level before creating the next or deleting anything. A physical scratch root may have
# symlinked ancestors (e.g. /tmp); neither home nor Movies may redirect outside that root.
mkdir -p "$out/home" && home=$(cd "$out/home" && pwd -P) || fail "cannot resolve scratch home"
case $home in "$real"|"$real/Movies"|"$real/Movies/"*) fail "scratch home resolves into the real home";; esac
[ "$home" = "$out/home" ] || fail "scratch home contains a symlink"
mkdir -p "$home/Movies" && movies=$(cd "$home/Movies" && pwd -P) || fail "cannot resolve scratch Movies"
case $movies in "$real"|"$real/Movies"|"$real/Movies/"*) fail "scratch Movies resolves into the real home";; esac
[ "$movies" = "$home/Movies" ] || fail "scratch Movies contains a symlink"
movies_before=$(ls -la "$real/Movies/NFSMW" 2>&1)
name=clip; secs=70; window=15
prefix=cliprec; glob='Clip-*.mp4'; exact=
keys=seq:sim:10,cliprec,wait:$((window * 60)),cliprec
if [ "$replay" = 1 ]; then
    name=replay; secs=100; window=30; prefix=replay; glob='Replay-*.mp4'; exact=--replay
    keys=seq:sim:5,replayarm,sim:45,replaysave
fi
rm -rf "$out/$name" "$out/$name.out" "$out/$name.user" "$out/$name.guard.out" "$movies/NFSMW"
runtime/run_guarded.sh "$secs" "$out/$name" BOOT_RACE=15.1.1 BOOT_AUTODRIVE=1 NFS_AUDIO_FAKE_DEVICE=1 \
    NFS_TEST_CLIP_HOSTTIME=1 CFFIXED_USER_HOME="$home" \
    BOOT_KEYS="$keys" "$@" > "$out/$name.guard.out" 2>&1 &
rg=$!
while kill -0 $rg 2>/dev/null && [ ! -e "$out/$name.out" ]; do sleep 0.5; done   # waiting for a game slot
i=0   # the recorder prints one `cliprec:` line when the clip is saved or abandoned
while ! grep -q "^$prefix: " "$out/$name.out" 2>/dev/null && [ $i -lt $((secs * 2)) ] && kill -0 $rg 2>/dev/null; do sleep 0.5; i=$((i + 1)); done
sleep 1
# Snapshot only this runner's children, then stop those recorded pids directly.
children=$(pgrep -P "$rg" 2>/dev/null || :)
# The slot records which child is the game. Stopping only a surviving guard must not excuse a game crash.
game=$(awk '$1 == "run" && $2 ~ /^[0-9]+$/ { print $2 }' "$NFS_RUNS/slots/$rg" 2>/dev/null)
stopped=0
for child in $children; do
    if kill -TERM "$child" 2>/dev/null && [ "$child" = "$game" ]; then stopped=1; fi
done
wait "$rg" 2>/dev/null
# The wrapper's own status is not the game's status: use its explicit exit record.
# SIGTERM is expected only when this smoke sent its stop; spontaneous exits must be zero.
status=$(sed -n 's/^run_guarded: exit \([0-9][0-9]*\) (.*/\1/p' "$out/$name.guard.out")
case $status in
    0) ;;
    143) [ "$stopped" = 1 ] || fail "game exit $status before smoke stop";;
    *) fail "game exit ${status:-record missing}";;
esac
[ "$(ls -la "$real/Movies/NFSMW" 2>&1)" = "$movies_before" ] || fail "the real Movies/NFSMW folder changed"
ls "$out/$name" 2>/dev/null | grep -q -i -E "crash|hang_|stall_" && fail "report $(ls "$out/$name" | grep -i -E 'crash|hang_|stall_' | head -3 | tr '\n' ' ')"
line=$(grep "^$prefix: " "$out/$name.out" | head -1)
[ -n "$line" ] || fail "no $prefix line (capture never finished)"
grep -q "^$prefix: .*; error:" "$out/$name.out" && fail "recorder error: $(grep "^$prefix: .*; error:" "$out/$name.out" | head -1)"
case $line in "$prefix: saved "*) ;; *) fail "$line";; esac
count=$(find "$home/Movies/NFSMW" -maxdepth 1 -name "$glob" -type f | wc -l | tr -d ' ')
[ "$count" = 1 ] || fail "expected one export, found $count"
clip=$(find "$home/Movies/NFSMW" -maxdepth 1 -name "$glob" -type f | head -1)
[ -n "$clip" ] || fail "clip not under the scratch home ($line)"
recorded=$(echo "$line" | sed -n 's/.* video \([0-9.]*\) s,.*/\1/p')
[ "$replay" = 0 ] || recorded=30
r=$("$py" -I runtime/clip_check.py "$clip" "$recorded" "$window" $exact)
case $r in "clip PASS"*) echo "CLIPSMOKE PASS: ${r#clip PASS: }; ${line#* video }";; *) fail "${r#clip FAIL: }; $line";; esac
