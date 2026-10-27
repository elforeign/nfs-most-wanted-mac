#!/bin/sh
# smoke.sh <dir> [NAME=value ...] - the gate's audio and pad smoke launch (runtime/README.md, "Audio and pad smoke"): one
# short guarded race (~25 s) with nothing real attached, then runtime/smoke_check.py on its diag dir (<dir>/smoke).
# Guarded runs keep NFS_AUDIO=null and no pad, which left the sound and controller paths unchecked (the 2026-10-21
# regressions found in play: garbled career audio, a DualSense that wouldn't accelerate, one plugged in
# after launch not recognised). This launch runs both paths whole:
#   pad    BOOT_PAD=sim:dualsense@6: a simulated DualSense connects 6 s after start-up, after the game's single
#          gamepad enumeration (dinput.c, ~frame 2), in the race intro. No BOOT_AUTODRIVE: only the pad can move the car.
#          From race sim 9 (GO is at ~10) Cross is held 210 ticks (the PlayStation binding set's Accelerate); then the
#          pad becomes an Xbox pad (padtype:xbox, padbind.c swaps to the Xbox binding set) and A is held 100 ticks (the
#          PS2 layout's gas). Then switch this run to Xbox layout: physical trigger pairs (before layout) prove
#          RT acceleration and exact pre-Fetch pedal values, including partial/both/release, then DualSense Cross
#          drives again. Ends at sim 18; numeric pedal checks follow the initial speed proofs. No extra launch.
#          Old code fails the 0/0.30 sample: cleared pressures/digital gas give 0/0 or 1/0, not 0.30/0.
#          Also measure negatives restoring combined Z, pressure clearing, and old Rz binding/rest.
#   audio  NFS_AUDIO_FAKE_DEVICE=1: the automatic output choice on media's fake HDMI TV, the full mixer -> encoder ->
#          IEC 61937 path, and the fake TV's receiver (media.cpp FakeRx: fake_tv.log) decoding what arrives and
#          comparing it with what the mixer wrote. Nothing reaches CoreAudio or a controller.
# Prints smoke_check.py's lines (SMOKE pad/audio PASS|FAIL ...); exit 1 if one fails. The diag dir stays in <dir>/smoke.
set -u
. "$(dirname "$0")/env.sh"; nfs_need_py
cd "$(dirname "$0")/.."
py=$TOOLS_PY
out=${1:?dir}; shift
mkdir -p "$out"
name=smoke; last=simshot_018.00.png; secs=60
rm -f "$out/$name.out"
runtime/run_guarded.sh "$secs" "$out/$name" NFS_SIMHZ=60 NFS_DISPLAY_PIXELS=2560x1440 BOOT_REG=g_RacingResolution=0 \
    BOOT_RACE=15.1.1 BOOT_RACE_AI=0 NFS_PAD_LAYOUT=playstation NFS_AUDIO_FAKE_DEVICE=1 BOOT_PAD=sim:dualsense@6 BOOT_DIAG_SIMSHOTS=18 \
    BOOT_KEYS=seq:sim:9,pad:1:210,padtype:xbox,wait:20,pad:1:100,padlayout:xbox,wait:8,padtriggers:0/1:45,padtriggers:0/0:8,padtriggers:0/0.20:8,padtriggers:0/0.30:8,padtriggers:1/0:8,padtriggers:0.50/0.75:8,padtriggers:1/1:8,padtriggers:0/0:8,padtype:dualsense,wait:8,pad:1:45 "$@" > /dev/null 2>&1 &
rg=$!
while kill -0 $rg 2>/dev/null && [ ! -e "$out/$name.out" ]; do sleep 0.5; done   # waiting for a game slot
i=0
while [ ! -s "$out/$name/$last" ] && [ $i -lt $((secs * 2)) ] && kill -0 $rg 2>/dev/null; do sleep 0.5; i=$((i + 1)); done
sleep 1
pkill -TERM -P $rg 2>/dev/null
wait $rg 2>/dev/null
"$py" -I runtime/smoke_check.py "$out/$name"
