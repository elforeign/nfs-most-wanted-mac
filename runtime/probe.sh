#!/bin/sh
# probe.sh - the screen-reach probe (BOOT_DIAG_PROBE, probe.m) for the opt-in render features: one guarded game run
# for all of them (the mirror in a second one), then a PASS/FAIL table. A render feature counts as verified only when its probe passes.
#
#   runtime/probe.sh <out dir> [features...]        (default: postfx msaa hdtex shadows carpaint mirror raindrops roadrefl ripples)
#   PROBE_SPLIT=1 runtime/probe.sh ...              one game run per feature instead
#
# The mirror feature runs in its own launch (run `mirror`): it needs the hood camera (three presses of C from 10 s in)
# and the probe from sim 30 s, which would make the other features' frames hood-cam frames at 24 s and the hold and
# shadows checks fragile. The others share one launch (`all`) with the chase camera and the probe from sim 10 s.
#
# One run: BOOT_RACE (default 15.1.1) with BOOT_AUTODRIVE, NFS_AUDIO=null NFS_RUMBLE=off NFS_PAD_TRIGGERS=off NFS_PAD_LIGHT=off NFS_FULLSCREEN=0
# BOOT_DIAG_SHOT_EVERY=0, a fresh BOOT_USERDATA in <out>/<run>/userdata and the union of the features' settings
# (postfx: NFS_POSTFX=x360, msaa: BOOT_REG=g_FSAALevel=2, hdtex: the default X360 set, shadows: BOOT_REG=g_ShadowDetail=2, carpaint: the default NFS_CAR_PAINT=x360, raindrops: the default NFS_RAINDROPS=x360 and the race's own rain, roadrefl: BOOT_REG=g_RoadReflectionEnable=2,g_RainEnable=1 (wet roads in 15.1.1 by 10 s), ripples: the default NFS_RAIN_RIPPLES=x360 and roadrefl's settings, mirror: NFS_POSTFX=x360, the hood camera and the probe from sim 30 s, in its own run).
# probe.m probes the features one after another, each signature pass applying only its own feature, so the verdicts
# match one run per feature (checked 2026-10-20); the numbers don't all: with the X360 post chain and MSAA on in every
# pass, hdtex's tint (3.5 % vs 22.8 %) and shadows' darkening (30 % vs 64 %) come out weaker, still >= 3x their 1 %
# thresholds. PROBE_SPLIT=1 is the old one-run-per-feature mode, for telling an interaction from a broken feature. <run> is `all` (without the mirror), `mirror`, or the feature name when split.
# It is stopped once probe.txt says done, or after PROBE_SECS (default 240): SIGTERM, then SIGKILL after 5 s.
# BOOT_BIN picks the binary (default runtime/boot); other variables (BOOT_DIAG_PROBE_AT...) pass through.
# <out>/<run>/ keeps diag/probe.txt, the run's output and, on a FAIL, the three captures; delete <out> afterwards.
set -u
cd "$(dirname "$0")/.."
. runtime/game_slot.sh                                      # a machine-wide game slot per launch
out=${1:?out dir}; shift
feats=${*:-postfx msaa hdtex shadows carpaint mirror raindrops roadrefl ripples}
game="${NFS_GAME:-$(cd .. && pwd)/Need For Speed Most Wanted Black Edition}"
secs=${PROBE_SECS:-240}
pid=
trap '[ -n "$pid" ] && kill -9 $pid 2>/dev/null; slot_release' EXIT INT TERM
mkdir -p "$out"
: > "$out/table.txt"

# run_game <run name> <features...>: one guarded game run probing those features; appends their lines to the table
run_game() {
    name=$1; shift
    list=; envs=; regs=
    for f in "$@"; do
        case $f in
            postfx) envs="$envs NFS_POSTFX=x360";;
            msaa) regs="$regs,g_FSAALevel=2";;
            hdtex) ;;
            shadows) regs="$regs,g_ShadowDetail=2";;
            carpaint) ;;
            raindrops) ;;                                    # the default NFS_RAINDROPS=x360; 15.1.1 rains from the start
            roadrefl) regs="$regs,g_RoadReflectionEnable=2,g_RainEnable=1";;
            ripples) regs="$regs,g_RoadReflectionEnable=2,g_RainEnable=1";;   # the default NFS_RAIN_RIPPLES=x360; wet roads with rain
            mirror) envs="$envs NFS_POSTFX=x360 BOOT_KEYS=seq:wait:600,C,wait:300,C,wait:300,C BOOT_DIAG_PROBE_AT=${BOOT_DIAG_PROBE_AT:-30}";;   # C: change camera; the hood camera shows the mirror
            *) echo "unknown feature $f (postfx msaa hdtex shadows carpaint mirror raindrops roadrefl ripples)"; continue;;
        esac
        list="$list,$f"
    done
    [ -n "$list" ] || return 0
    [ -n "$regs" ] && envs="$envs BOOT_REG=${regs#,}"
    d="$out/$name"
    slot_note=$d; slot_acquire probe
    rm -rf "$d"; mkdir -p "$d/userdata"
    env $envs BOOT_BACKGROUND=1 NFS_AUDIO=null NFS_RUMBLE=off NFS_PAD_TRIGGERS=off NFS_PAD_LIGHT=off NFS_FULLSCREEN=0 BOOT_DIAG_SHOT_EVERY=0 BOOT_USERDATA="$d/userdata" \
        BOOT_DEADLINE=$((secs + 8)) BOOT_RACE="${BOOT_RACE:-15.1.1}" BOOT_AUTODRIVE=1 BOOT_DIAG="$d/diag" BOOT_DIAG_PROBE=${list#,} \
        "${BOOT_BIN:-runtime/boot}" "$game" > "$d/run.out" 2>&1 &
    pid=$!
    slot_started $pid
    t=0
    while kill -0 $pid 2>/dev/null && [ $t -lt "$secs" ] && ! grep -q '^done' "$d/diag/probe.txt" 2>/dev/null; do sleep 1; t=$((t + 1)); done
    kill $pid 2>/dev/null
    s=0
    while kill -0 $pid 2>/dev/null && [ $s -lt 5 ]; do sleep 1; s=$((s + 1)); done
    kill -9 $pid 2>/dev/null
    wait $pid 2>/dev/null
    slot_release
    pid=
    rm -f "$d/diag/events.log"                               # large; probe.txt has the result
    for f in "$@"; do
        line=$(grep "^$f " "$d/diag/probe.txt" 2>/dev/null | head -1)
        [ -n "$line" ] || line="$f FAIL (no result after ${t}s: race time not reached$( [ -f "$d/diag/crash.txt" ] && echo ", CRASH: $(head -1 "$d/diag/crash.txt")"))"
        echo "$line" >> "$out/table.txt"
        echo "$line"
    done
}

if [ -n "${PROBE_SPLIT:-}" ]; then
    for f in $feats; do run_game "$f" "$f"; done
else
    others=; mir=
    for f in $feats; do [ "$f" = mirror ] && mir=mirror || others="$others $f"; done
    [ -n "$others" ] && run_game all $others
    [ -n "$mir" ] && run_game mirror mirror
fi
echo
echo "feature  result"
awk '{ printf "%-8s %s\n", $1, $2 }' "$out/table.txt"
! grep -q ' FAIL' "$out/table.txt"
