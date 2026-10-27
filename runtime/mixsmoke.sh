#!/bin/sh
# mixsmoke.sh <dir> [NAME=value ...] - the gate's front end -> free roam audio check with the user's audio settings
# (X360 sound mix on, X360 music on, ITU 5.1, X360 police radio). One guarded launch (~2.5 min, BOOT_DIAG): a new career
# profile is created in the front end, the game loads into free roam (the FE -> game load the BOOT_RACE launches never
# do), then 20 in-game seconds are read from stats.log. FAIL when
#   - any sound bank load is refused (`x360audio: bank load ... result -4`: it did not fit the Audio Memory Pool), or
#   - a bank's resident copy runs past its AI-car bank slot (`SLOT OVERFLOW`: crash 2026-10-25, PC 160 KB slots), or
#   - the in-game mix is silent: median of mixrms FL+FR over the in-game seconds 3..20 below 0.01.
# Why: with the X360 mix the in-game banks are 48 kHz PCM16 (free roam 18 MB vs PC 2.9 MB);
# the PC's 5.4 MB pool refused 8-18 bank loads (engine, music, collision, traffic...) after the FE -> game load and the game
# was silent in free roam while every race launch sounded normal.
# Proof (same build, same route): NFS_TEST_AUDIOPOOL=pc (the PC pool, the broken behaviour) -> FAIL: 18 refused bank
# loads, FL+FR median 0.000; default (+40 MB with the mix) -> PASS: 0 refused, FL+FR median 0.245.
# Prints `MIXSMOKE PASS|FAIL: ...`; exit 1 on FAIL. The diag dir stays in <dir>/mixsmoke.
set -u
cd "$(dirname "$0")/.."
out=${1:?dir}; shift
mkdir -p "$out"
name=mixsmoke; secs=170; d="$out/$name"
keys="seq:skipmovies,wait:450,RETURN,wait:400,RETURN,wait:400,RETURN,wait:300,RETURN,wait:300,LEFT,wait:60,RETURN,wait:300,RETURN,wait:300,RETURN,wait:300,BACK,BACK,BACK,BACK,T,E,S,T,RETURN,wait:300,LEFT,wait:60,RETURN,wait:400,RETURN,wait:400,RETURN,wait:400,RETURN,wait:400,RETURN,wait:120,pkg:Dialog.fng:RETURN,wait:60,LEFT,wait:30,RETURN"
# BEGIN offline cleanup checks
rg=
stop_run() {
    if [ -n "$rg" ]; then
        kill -TERM "$rg" 2>/dev/null
        wait "$rg" 2>/dev/null
        rg=
    fi
}
trap stop_run EXIT
trap 'exit 143' TERM INT HUP
# END offline cleanup checks
runtime/run_guarded.sh "$secs" "$d" NFS_X360_AUDIO=on NFS_X360_MUSIC=on NFS_SPEAKER_LAYOUT=itu NFS_X360_COPSPEECH=x360 \
    NFS_AUDIO_FAKE_DEVICE=1 NFS_DISPLAY_PIXELS=2560x1440 BOOT_REG=g_RacingResolution=0 BOOT_SKIPINTRO=1 BOOT_AUTODRIVE=1 \
    "BOOT_KEYS=$keys" "$@" > "$out/$name.out" 2>&1 &
rg=$!
# stop once 22 in-game seconds are logged (or at the guard)
while kill -0 $rg 2>/dev/null; do
    n=$(grep -c ' flow 6 ' "$d/stats.log" 2>/dev/null); [ "${n:-0}" -ge 22 ] && break
    sleep 1
done
stop_run
ls "$d" 2>/dev/null | grep -q -i -E "crash|hang_|stall_" && { echo "MIXSMOKE FAIL: report $(ls "$d" | grep -i -E 'crash|hang_|stall_' | head -3 | tr '\n' ' ')"; exit 1; }
refused=$(grep -c 'x360audio: bank load .*result -4' "$d/events.log" 2>/dev/null)
overflow=$(grep -c 'SLOT OVERFLOW' "$d/events.log" 2>/dev/null)
[ "${overflow:-0}" = 0 ] || { echo "MIXSMOKE FAIL: $overflow bank slot overflows ($(grep -m1 'SLOT OVERFLOW' "$d/events.log" | cut -c1-160); $d)"; exit 1; }
ingame=$(grep -c ' flow 6 ' "$d/stats.log" 2>/dev/null)
[ "${ingame:-0}" -ge 20 ] || { echo "MIXSMOKE FAIL: reached free roam for ${ingame:-0} s (need 20; $d)"; exit 1; }
med=$(grep ' flow 6 ' "$d/stats.log" | sed -n '3,20p' | sed -E 's/.*mixrms ([0-9.]+) ([0-9.]+) .*/\1 \2/' |
      awk '{print $1 + $2}' | sort -n | awk '{v[NR]=$1} END {printf "%.3f", NR ? v[int((NR+1)/2)] : 0}')
first=$(grep -m1 'x360audio: bank load .*result -4' "$d/events.log" 2>/dev/null | sed -E 's/.*bank load ([^:]*):.*/\1/')
if [ "${refused:-0}" != 0 ] || [ "$(echo "$med < 0.01" | bc)" = 1 ]; then
    echo "MIXSMOKE FAIL: $refused refused bank loads${first:+ (first $first)}, in-game FL+FR median $med (need 0 and >= 0.01; $d)"
    exit 1
fi
echo "MIXSMOKE PASS: FE -> free roam with the X360 mix: 0 refused bank loads, in-game FL+FR median $med"
