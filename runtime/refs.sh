#!/bin/sh
# refs.sh - the reference-frame regression set (runtime/README.md, "Reference frames"): five deterministic frames,
# compared with the pictures in runtime/refs/ so a visual change that no probe checks fails the gate.
#
#   runtime/refs.sh check [dir]      capture the frames (two guarded launches, ~1 min), compare, print one line per frame;
#                                    exit 1 and write diff images (<dir>/refs_diff_<name>.jpg) on a change. Default dir: a new $TMPDIR/refs.*
#   runtime/refs.sh accept           capture and store the frames as the new references (after an intended visual change;
#                                    commit runtime/refs/ with the change that caused it)
#   runtime/refs.sh capture <dir>    only capture, into <dir>/{chase,hood,rain,title,menu}.png
#   runtime/refs.sh noise <n>        capture n times and print the difference between the runs (how the tolerance was set)
#
# The frames (same settings everywhere: fresh scratch profile, audio null, rumble off, windowed, the display faked to
# 2560x1440 so the 720p slot is 1280x720 on any Mac (NFS_DISPLAY_PIXELS), g_RacingResolution=0, NFS_SIMHZ=60: the sim
# ticks and the frames come at exactly 60 per second, so a sim time or a frame number is the same picture; NFS_LOCKSTEP=1:
# in the race every frame is exactly one tick whatever the wall clock does, so a slow frame (a shader compile, another
# game) is not caught up with extra ticks that change the autodrive car's path (pacing.c; REFS_LOCKSTEP=0 turns it off)):
#   chase  race 15.1.1, no opponents (BOOT_RACE_AI=0, whose random colours made frames differ), autodrive, at sim time 12 s
#          (just after the start, past the exposure flash at 10 s): chase camera, shadows, X360 post chain, car paint, HD textures, HUD
#   hood   the same race at sim time 25 s with the hood camera (C three times from sim 20 s): the mirror glass only (2026-10-22: limit 5 % of its blocks; NFS_MIRROR_FOG=off changes 73 %; before, the whole frame, limit 2 %: run to run
#          0.24-0.55 %, also with a 60 ms frame at sim 20.7 s; the mirror with NFS_MIRROR_FOG=off 4.3 %)
#   rain   the same race at sim time 11.5 s (rain ~0.8 until ~13 s, roads wet ~0.9 by 10 s, after the start flash): judged on the
#          road rows only, in 16 px blocks and 4 levels (refs_compare.py REGION), where the wet-road reflection and the X360 rain
#          ripples show; the lens drops and streaks (sparse, move run to run) average out there and are the raindrops probe's.
#          Measured 2026-10-21 (limit 4 %): run to run 0.3 %; NFS_RAIN_RIPPLES=pc 14.8 % (refs.sh check, RACE_ENV=NFS_RAIN_RIPPLES=pc: chase 0.9 % passes); no road reflection
#          (g_RoadReflectionEnable=0) 35 % of the road's blocks changed (chase, same settings, sees 1.5 % / 9.8 %).
#          Night or tunnel: none; no race id or autodrive stretch known to be dark/in a tunnel at a fixed sim time without a second launch.
#   title  the title screen at frame 900 (X360 layout and "Press START to begin")
#   menu   the main menu at frame 1800 (UI textures, prompts)
# The race frames come from the gate's single race launch (race_launch below; ~1.5 min): besides the two frames it runs
# the screen-reach probe's features (runtime/probe.m, probe.txt in the race diag dir) and the sim to 45 s for the crash,
# hang and stall check (runtime/gate.sh reads those; REFS_KEEP=1 keeps the race diag dir). All of it is keyed to sim time:
#   sim 11.5 rain frame; 12 chase frame; 14-19 the probe's features (postfx msaa hdtex shadows carpaint raindrops roadrefl ripples: each three
#   held passes); 20 C x3 (hood camera); 25 hood frame; 27 the mirror probe; 45 end (simshot_045 is the stop marker).
# The settings are the probe's union (X360 post chain, MSAA 2x, shadows 2, wet roads with rain), not the defaults.
# The front-end launch ends when frame 1800 is written: after 2 s for the encoder.
# A check that fails is repeated once only if REFS_RETRY=1 (another session's game running at the same time can make a
# front-end launch miss its frames; the race keeps its ticks with NFS_LOCKSTEP). Not exact from run to run (rain, lens drops, the title prompt's
# pulse), hence runtime/refs_compare.py's tolerance.
# BOOT_BIN picks the binary (default runtime/boot); NFS_GAME as in run_guarded.sh.
set -u
. "$(dirname "$0")/env.sh"; nfs_need_py
cd "$(dirname "$0")/.."
py=$TOOLS_PY
mode=${1:-check}; [ $# -gt 0 ] && shift

# run <name> <last file> <seconds> NAME=value...: one guarded launch in $out/<name>, ended once <last file> exists
run() {
    name=$1; last=$2; secs=$3; shift 3
    rm -f "$out/$name.out"
    runtime/run_guarded.sh "$secs" "$out/$name" NFS_SIMHZ=60 NFS_LOCKSTEP=${REFS_LOCKSTEP:-1} NFS_DISPLAY_PIXELS=2560x1440 "$@" > /dev/null 2>&1 &
    rg=$!
    while kill -0 $rg 2>/dev/null && [ ! -e "$out/$name.out" ]; do sleep 0.5; done   # waiting for a game slot
    i=0
    while [ ! -s "$out/$name/$last" ] && [ $i -lt $((secs * 2)) ] && kill -0 $rg 2>/dev/null; do sleep 0.5; i=$((i + 1)); done
    sleep 2
    pkill -TERM -P $rg 2>/dev/null
    wait $rg 2>/dev/null
}
# the single race launch (see the header); BOOT_REG as probe.sh's union of the features' settings
race_launch() {
    run race simshot_045.00.png ${RACE_SECS:-120} BOOT_REG=g_RacingResolution=0,g_FSAALevel=2,g_ShadowDetail=2,g_RoadReflectionEnable=2,g_RainEnable=1 \
        NFS_POSTFX=x360 BOOT_RACE=15.1.1 BOOT_RACE_AI=0 BOOT_AUTODRIVE=1 BOOT_DIAG_SIMSHOTS=11.5,12,25,45 \
        BOOT_DIAG_PROBE=postfx,msaa,hdtex,shadows,carpaint,raindrops,roadrefl,ripples,mirror@27 BOOT_DIAG_PROBE_AT=14 \
        BOOT_KEYS=seq:sim:20,C,wait:30,C,wait:30,C ${RACE_ENV:-}
}
capture() {
    out=$1; mkdir -p "$out"
    race_launch
    run ui frame_01800.png 60 BOOT_REG=g_RacingResolution=0 BOOT_DIAG_SHOTS=900,1800 \
        BOOT_KEYS=seq:skipmovies,wait:120,RETURN,wait:240,RETURN,wait:240,RETURN,wait:240
    cp "$out/race/simshot_012.00.png" "$out/chase.png" 2>/dev/null
    cp "$out/race/simshot_025.00.png" "$out/hood.png" 2>/dev/null
    cp "$out/race/simshot_011.50.png" "$out/rain.png" 2>/dev/null
    cp "$out/ui/frame_00900.png" "$out/title.png" 2>/dev/null
    cp "$out/ui/frame_01800.png" "$out/menu.png" 2>/dev/null
    for f in chase hood rain title menu; do
        [ -s "$out/$f.png" ] || { echo "REFS FAIL: $f not captured (see $out/race.out, $out/ui.out)"; return 1; }
    done
    [ -n "${REFS_KEEP:-}" ] || rm -rf "$out/race" "$out/race.user"
    rm -rf "$out/ui" "$out/ui.user"
}

# BEGIN offline comparator (auditfix_test.py: reference copies 5 x 0.00%, corrupt/missing image -> exit 1)
compare() {
    "$py" -I runtime/refs_compare.py check runtime/refs "$1" "$1" > "$1/refs.txt" 2>&1
    compare_rc=$?
    [ "$compare_rc" = 0 ] || { echo "REFS FAIL: comparator exit $compare_rc" >> "$1/refs.txt"; return 1; }
    comparisons=$(grep -c -E '^(chase|hood|rain|title|menu) +[0-9.]+ % of blocks' "$1/refs.txt")
    [ "$comparisons" = 5 ] || { echo "REFS FAIL: expected 5 frame comparisons, got $comparisons" >> "$1/refs.txt"; return 1; }
    ! grep -q '^REFS FAIL' "$1/refs.txt"
}
# END offline comparator
case $mode in
    capture) capture "${1:?dir}" ;;
    check)
        dir=${1:-$(mktemp -d "${TMPDIR:-/tmp}/refs.XXXXXX")}
        capture "$dir" || exit 1
        if ! compare "$dir"; then
            [ "${REFS_RETRY:-0}" = 1 ] || { cat "$dir/refs.txt"; exit 1; }
            echo "refs: first capture differs, capturing again ($(pgrep -f 'runtime/boot' | wc -l | tr -d ' ') game processes running now)"
            mkdir -p "$dir/first"; cp "$dir/refs.txt" "$dir/first/refs.txt"
            mv "$dir"/refs_diff_*.jpg "$dir/first/" 2>/dev/null
            capture "$dir" || exit 1
            compare "$dir" || { cat "$dir/refs.txt"; exit 1; }
        fi
        cat "$dir/refs.txt"
        [ -n "${1:-}" ] || rm -rf "$dir"
        echo "REFS PASS" ;;
    accept)
        dir=$(mktemp -d "${TMPDIR:-/tmp}/refs.XXXXXX")
        capture "$dir" || exit 1
        "$py" -I runtime/refs_compare.py accept "$dir" runtime/refs && rm -rf "$dir" ;;
    noise)
        n=${1:-3}; dirs=""; k=1
        base=$(mktemp -d "${TMPDIR:-/tmp}/refs.XXXXXX")
        while [ $k -le "$n" ]; do capture "$base/$k" || exit 1; dirs="$dirs $base/$k"; k=$((k + 1)); done
        "$py" -I runtime/refs_compare.py noise $dirs; rm -rf "$base" ;;
    *) echo "usage: runtime/refs.sh check|accept|capture <dir>|noise <n>"; exit 2 ;;
esac
