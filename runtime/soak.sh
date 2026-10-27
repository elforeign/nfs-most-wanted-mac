#!/bin/sh
# soak.sh - back-to-back AI-driven SkipFE races in diagnostic mode, with a summary at the end.
#
#   runtime/soak.sh <out dir> <races> <seconds per race> [race IDs...]
#
# Each race runs in its own process (BOOT_RACE, BOOT_AUTODRIVE; unless set: NFS_AUDIO=null, NFS_RUMBLE=off, NFS_PAD_TRIGGERS=off, NFS_PAD_LIGHT=off, windowed
# NFS_FULLSCREEN=0 and a fresh scratch profile BOOT_USERDATA=<out>/race_N.user, as run_guarded.sh; settings with
# BOOT_REG=name=value, which wins over the first-run detection) with its own BOOT_DIAG dir <out>/race_N, and is
# stopped after <seconds>. Race IDs cycle through the list given
# (default 15.1.1). The summary (<out>/summary.txt) lists, per race: how it ended (timeout = survived,
# crash/hang report, or exit code), frames reached, the race-start frame, fps and frame-time p99 over the
# race, the game thread's busy ms per frame (CPU time without its sleeps and waits), and the first/last/peak memory
# numbers from stats.log, so growth after the first load stands out, and sim time against wall time over the race
# (frames.log): from the race start to the last frame sim time advanced, so a race that finishes inside the soak (sim
# time stops on the post-race screen, original behaviour) doesn't read as a slow clock, and only while the game runs at
# full speed (frames.log simspeed 1; the finish cinematic, Speedbreaker and other game slow-mo are reported apart); a
# stop more than 2 s before the end is reported (race finished, or paused). 1.000: game time kept real time.
# BOOT_BIN picks the binary (default runtime/boot; copy it to runtime/boot.soak to keep rebuilding during a soak).
# Environment variables (BOOT_RACE_CAR, BOOT_RACE_LAPS, BOOT_DIAG_SHOT_EVERY...) pass through.
set -u
cd "$(dirname "$0")/.."
NFS_SLOT=${NFS_SLOT:-solo}                                  # soaks time things: alone on the machine (game_slot.sh)
. runtime/game_slot.sh
trap 'slot_release' EXIT
out=${1:?out dir}; races=${2:?races}; secs=${3:?seconds}; shift 3
ids=${*:-15.1.1}
game="${NFS_GAME:-$(cd .. && pwd)/Need For Speed Most Wanted Black Edition}"
mkdir -p "$out"
: > "$out/summary.txt"
i=1
while [ "$i" -le "$races" ]; do
    set -- $ids
    k=$(( (i - 1) % $# + 1 )); eval id=\${$k}
    d="$out/race_$i"
    slot_note=$d; slot_acquire soak
    rm -rf "$d" "$d.user"; mkdir -p "$d.user"
    BOOT_BACKGROUND=1 NFS_AUDIO=${NFS_AUDIO:-null} NFS_RUMBLE=${NFS_RUMBLE:-off} NFS_PAD_TRIGGERS=${NFS_PAD_TRIGGERS:-off} NFS_PAD_LIGHT=${NFS_PAD_LIGHT:-off} NFS_FULLSCREEN=${NFS_FULLSCREEN:-0} BOOT_USERDATA=${BOOT_USERDATA:-$d.user} BOOT_DEADLINE=$((secs + 8)) BOOT_RACE=$id BOOT_AUTODRIVE=1 BOOT_DIAG="$d" "${BOOT_BIN:-runtime/boot}" "$game" > "$out/race_$i.out" 2>&1 &
    pid=$!
    slot_started $pid
    t=0
    while kill -0 $pid 2>/dev/null && [ $t -lt "$secs" ]; do sleep 1; t=$((t + 1)); done
    if kill -0 $pid 2>/dev/null; then kill $pid; wait $pid 2>/dev/null; end="survived ${secs}s"; slot_release survived
    else wait $pid; rc=$?; end="exited rc=$rc after ${t}s"; slot_release $rc; fi
    [ -f "$d/crash.txt" ] && end="$end, CRASH: $(head -1 "$d/crash.txt")"
    for h in "$d"/hang_*.txt; do [ -f "$h" ] && end="$end, HANG: $(head -1 "$h")"; done
    # events.log is large and not needed once the race survived
    case "$end" in survived*) [ -f "$d/crash.txt" ] || ls "$d"/hang_*.txt >/dev/null 2>&1 || rm -f "$d/events.log";; esac
    race=$(awk '/^gameflow .* -> 6 /{print $NF; exit}' "$d/frames.log" 2>/dev/null)
    simwall=$(awk -v rf="${race:-}" '
        /^frame / { for (j = 1; j < NF; ++j) v[$j] = $(j + 1)
                    if (rf == "" || v["frame"] + 0 < rf + 0 || v["simtime"] == "") next
                    n++; t[n] = v["t"]; s[n] = v["simtime"]; sp[n] = v["simspeed"] == "" ? 1 : v["simspeed"]
                    if (n == 1 || s[n] > s[last] + 1e-6) last = n }
        END { if (n < 2 || last < 2) { print "sim/wall n/a"; exit }
              for (k = 2; k <= last; ++k) if (sp[k - 1] >= 0.999) { ds += s[k] - s[k - 1]; dt += t[k] - t[k - 1] } else slow += t[k] - t[k - 1]
              if (dt <= 0) { print "sim/wall n/a"; exit }
              printf "sim/wall %.3f over %.0f s of race", ds / dt, dt
              if (slow > 0.5) printf " (+%.0f s slowed by the game: Speedbreaker, cinematics)", slow
              if (t[n] - t[last] > 2) printf ", sim time stopped %.0f s before the end (race finished or paused)", t[n] - t[last] }
    ' "$d/frames.log" 2>/dev/null)
    awk -v id="$id" -v n="$i" -v end="$end" -v race="${race:-none}" -v simwall="${simwall:-sim/wall n/a}" '
        { for (j = 1; j < NF; ++j) v[$j] = $(j + 1)
          if (!seen++) { rss0 = v["rss"]; bump0 = v["bump"]; hp0 = v["heap"]; gm0 = v["gmalloc"]; gt0 = v["gputex"]; gb0 = v["gpubuf"] }
          if (v["flow"] == 6) { fps += v["fps"]; busy += v["busy"]; ns++; if (v["p99"] > p99) p99 = v["p99"]; if (!rssr) { rssr = v["rss"]; bumpr = v["bump"]; hpr = v["heap"]; gmr = v["gmalloc"]; gtr = v["gputex"]; gbr = v["gpubuf"] } }
          rss = v["rss"]; bump = v["bump"]; hp = v["heap"]; gm = v["gmalloc"]; gt = v["gputex"]; gb = v["gpubuf"]; fr = v["frame"] }
        END { printf "race %d (%s): %s; frames %s, race start frame %s, race fps %.1f, busy %.2f ms/frame, worst p99 %.1f ms\n", n, id, end, fr, race, ns ? fps / ns : 0, ns ? busy / ns : 0, p99
              printf "    rss MB first %s / race start %s / last %s;  bump KB %s / %s / %s;  gmalloc KB %s / %s / %s;  heap KB %s / %s / %s\n", rss0, rssr, rss, bump0, bumpr, bump, gm0, gmr, gm, hp0, hpr, hp
              printf "    gpu textures %s / %s / %s;  gpu buffers %s / %s / %s;  %s\n", gt0, gtr, gt, gb0, gbr, gb, simwall }
    ' "$d/stats.log" >> "$out/summary.txt" 2>/dev/null || echo "race $i ($id): $end; no stats.log" >> "$out/summary.txt"
    tail -2 "$out/summary.txt"
    i=$((i + 1))
done
echo "summary: $out/summary.txt"
