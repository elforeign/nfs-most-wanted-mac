#!/bin/sh
# gate.sh [seconds] [NAME=value ...] - the merge gate for this checkout, as one command:
# build (the recompiler only when recomp/ differs from main, recomp/out is missing or older than recomp/), recomp/difftest.py --synth when
# recomp/ changed, a ledger check, then two guarded launches via runtime/refs.sh (~1.5 min): ONE race launch (15.1.1,
# autodrive, to sim 45 s: crash/hang/stall reports, fps/p99 for information, all nine probe features, the chase and
# hood reference frames at fixed sim times) and one short front-end launch (title and menu frames). [seconds] is the
# race launch's time guard (default 120); extra NAME=value pass through to the race. Ends with one line: "GATE PASS ..."
# or "GATE FAIL: <stage>: <why>" (stages: build, offline, difftest, race, probe, refs, pad, triggers, audio, mixsmoke). Then the audio and pad smoke
# launch (runtime/smoke.sh, ~25 s: a simulated DualSense plugged in after launch drives the car, then as an Xbox pad;
# the automatic output on media's fake HDMI TV, its stream decoded and compared with the mix; smoke_check.py's limits and
# the negative runs in runtime/README.md, "Audio and pad smoke"), and host/audio's `dstest fake` (the output-device
# decisions on the fake TV, headless, alongside the launches) and runtime/mixsmoke.sh (front end -> free roam with the user's
# audio settings, ~2.5 min in the background beside the refs launches: refused bank loads, slot overflow, silence). Logs, and the evidence of a failure: $TMPDIR/gate.*
# Offline-tested by auditfix_test.py: all three smoke PASS lines -> 0; trigger-only FAIL -> 1.
# Shader stage (2026-10-23): race.out of R27 run 3 (80 "MSL compile failed") -> FAIL; run 4 (0) -> pass.
# Five reference metric lines -> 0; four lines or comparator status 7 -> 1.
# BEGIN offline result checks (the test sources only these functions; no gate/game launch)
check_refs_result() {
    [ "$2" = 0 ] || fail "refs: comparator/capture exit $2: $(grep 'REFS FAIL' "$1" | head -2 | tr '\n' ' ')(runtime/refs.sh accept if intended)"
    nr=$(grep -c -E '^(chase|hood|rain|title|menu) +[0-9.]+ % of blocks' "$1")
    [ "$nr" = 5 ] || fail "refs: expected 5 frame comparisons, got $nr"
    for frame in chase hood rain title menu; do
        [ "$(grep -c -E "^$frame +[0-9.]+ % of blocks" "$1")" = 1 ] || fail "refs: expected one $frame comparison"
    done
    ! grep -q '^REFS FAIL' "$1" || fail "refs: $(grep -m1 '^REFS FAIL' "$1")"
}
check_smoke_result() {
    for stage in pad triggers audio; do
        grep -q "^SMOKE $stage PASS:" "$1" || fail "$stage: $(grep -m1 "^SMOKE $stage" "$1" | cut -c1-300)"
        ! grep -q "^SMOKE $stage FAIL" "$1" || fail "$stage: $(grep -m1 "^SMOKE $stage FAIL" "$1" | cut -c1-300)"
    done
}
# END offline result checks
secs=${1:-120}; [ $# -gt 0 ] && shift
case $secs in ""|*[!0-9]*) echo "gate.sh: the first argument is the race length in seconds (got '$secs'); settings follow it: gate.sh 120 NAME=value" >&2; exit 2 ;; esac
. "$(dirname "$0")/env.sh"; nfs_need_py
cd "$(dirname "$0")/.."; here=$PWD
py=$TOOLS_PY
log=$(mktemp -d "${TMPDIR:-/tmp}/gate.XXXXXX")
# BEGIN offline cleanup checks (gate_cleanup_test.py sources this block; never launches the gate/game)
dstest_pid= mix_pid=
cleanup() {
    trap '' TERM INT HUP
    # mixsmoke forwards TERM and waits for run_guarded to stop its game/guard and release the slot.
    for job in "$mix_pid" "$dstest_pid"; do
        [ -z "$job" ] || kill -TERM "$job" 2>/dev/null
    done
    for job in "$mix_pid" "$dstest_pid"; do
        [ -z "$job" ] || wait "$job" 2>/dev/null
    done
    mix_pid= dstest_pid=
}
trap cleanup EXIT
trap 'exit 143' TERM INT HUP
fail() { echo "GATE FAIL: $* (logs $log)"; exit 1; }
# END offline cleanup checks
base=$(git merge-base HEAD refs/heads/main 2>/dev/null || echo HEAD)
changed() { git diff --name-only "$base" -- "$@" | grep -q . || [ -n "$(git status --porcelain -- "$@")" ]; }
# also when a recomp/ file is newer than its output (a merge of main that changed recomp/hooks.txt into a worktree whose
# recomp/out was cloned from main before it: "changed" sees no difference from main, but the output is stale)
stale() { [ ! -f recomp/out/speed.a ] || [ -n "$(find recomp -path recomp/out -prune -o -type f -newer recomp/out/speed.a -print | head -1)" ]; }
if changed recomp || [ ! -d recomp/out ] || stale; then (cd "$NFS_PARENT" && "$py" -I "$here/recomp/build_all.py") > "$log/recomp.log" 2>&1 || fail "build: recompiler"; fi
# offline tests (runtime/offline_tests.sh, ~2 min, no game, no audio device): the first check after the recompiler build, so a test that
# rotted (stub missing after a runtime change, a default that moved) fails the gate here instead of staying red unnoticed. Proof
# (2026-10-26): a flipped assertion in runtime/framecap_test.c -> GATE FAIL: offline: OFFLINE FAIL: framecap; restored -> passes.
runtime/offline_tests.sh > "$log/offline.txt" 2>&1 || fail "offline: $(grep -m1 '^OFFLINE FAIL' "$log/offline.txt" | cut -c1-300)"
# a library older than any of its sources (a worktree cloned before main moved): rebuilt as recomp's stale() does
stale_lib() { lib=$1; shift; [ ! -f "$lib" ] || [ -n "$(find "$@" -path '*/build' -prune -o -type f \( -name '*.[chm]' -o -name '*.cpp' -o -name '*.mm' -o -name '*.sh' \) -newer "$lib" -print | head -1)" ]; }
if changed host/audio media || stale_lib host/audio/build/libhostaudio.a host/audio || stale_lib host/audio/build/libmedia.a media; then (host/audio/build.sh && media/build.sh) > "$log/host.log" 2>&1 || fail "build: host/media"; fi
runtime/build.sh > "$log/runtime.log" 2>&1 || fail "build: runtime"
note=""
if changed recomp; then
    (cd "$NFS_PARENT" && "$py" -I "$here/recomp/difftest.py" --synth) > "$log/difftest.log" 2>&1
    grep -q " 0 fail" "$log/difftest.log" || fail "difftest: $(grep -m1 'differential' "$log/difftest.log")"
    note="$note difftest $(grep -o '[0-9]* pass' "$log/difftest.log")"
fi
# ledger: a new or changed NFS_ switch in code needs VERSIONS.md or runtime/README.md touched in the same branch
if git diff "$base" -- runtime host media | grep -E '^\+.*getenv\("NFS_' >/dev/null && ! changed VERSIONS.md runtime/README.md; then
    note="$note LEDGER-WARN(NFS_ switch changed, VERSIONS.md/README untouched)"
fi
# the race part: ONE guarded launch (runtime/refs.sh race_launch: 15.1.1, autodrive, sim to 45 s) that also runs every
# probe feature and captures the chase and hood reference frames at fixed sim times, plus one short front-end launch
# for the title and menu frames. Evidence of a failure stays in $log/refs (race/ = the race's diag dir with probe_*.png,
# diff images refs_diff_*.jpg beside it); refs.sh and probe.sh also run alone.
[ -d runtime/refs ] || fail "runtime/refs missing (runtime/refs.sh accept)"
host/audio/build/dstest fake > "$log/dstest.txt" 2>&1 &   # headless (NFS_AUDIO_FAKE_DEVICE): no device, no game slot
dstest_pid=$!
# MIXSMOKE: front end -> free roam with the player's audio settings (X360 mix/music, ITU 5.1, X360 police
# radio), runtime/mixsmoke.sh (~2.5 min), in the background beside the refs race/front-end launches (two game slots; the smoke
# launch below takes the slot the refs front end frees). Extra NAME=value args reach it as well as the race (the gate
# forwards `gate.sh 120 NAME=value` to the race through RACE_ENV and to mixsmoke as its own args).
# Proof (same build): gate.sh 120 NFS_TEST_AUDIOPOOL=pc -> GATE FAIL: mixsmoke: MIXSMOKE FAIL: report stall_1.txt (14 refused bank loads in events.log; 2:53); normal gate -> GATE PASS with mixsmoke 0 refused bank loads, FL+FR median 0.243 (2:15)
runtime/mixsmoke.sh "$log/mix" "$@" > "$log/mixsmoke.txt" 2>&1 &
mix_pid=$!
RACE_ENV="$*" RACE_SECS=$secs REFS_KEEP=1 runtime/refs.sh check "$log/refs" > "$log/refs.txt" 2>&1; refs_rc=$?
r="$log/refs/race"
ls "$r" | grep -q -i -E "crash|hang_|stall_" && fail "race: report $(ls "$r" | grep -i -E 'crash|hang_|stall_' | head -3 | tr '\n' ' ')"
last=$(grep "flow 6" "$r/stats.log" 2>/dev/null | tail -1)
[ -n "$last" ] || fail "race: never started (no flow 6 in stats.log)"
[ -s "$r/simshot_045.00.png" ] || fail "race: did not reach sim time 45 s ($r)"
fps=$(echo "$last" | sed -E 's/.* fps ([0-9.]+) .*/\1/'); p99=$(echo "$last" | sed -E 's/.* p99 ([0-9.]+) .*/\1/')
# probe: all nine features must have PASSed (probe_<feature>_{base,sig,after}.png in $r for a failed one)
np=0; bad=""
for f in postfx msaa hdtex shadows carpaint raindrops roadrefl ripples mirror; do
    if grep -q "^$f  *PASS" "$r/probe.txt" 2>/dev/null; then np=$((np + 1)); else bad="$bad $f"; fi
done
[ -z "$bad" ] || fail "probe:$bad not PASS ($(grep -h -E "^($(echo $bad | tr ' ' '|')) " "$r/probe.txt" 2>/dev/null | cut -c1-90 | head -2 | tr '\n' ';'))"
check_refs_result "$log/refs.txt" "$refs_rc"
# Any Metal shader that fails to compile falls back silently (R27 grass: 80 failures, feature invisible, 2026-10-23).
nmsl=$(grep -c 'MSL compile failed' "$log/refs/race.out" 2>/dev/null)
[ "${nmsl:-0}" = 0 ] || fail "race: $nmsl Metal shader compile failures ($(grep -m1 'MSL compile failed' "$log/refs/race.out" | cut -c1-160))"
# Retain the small spill log after reference diagnostics are cleaned, so a capped-ring gate proves it overflowed.
grep '^boot: render: upload ring full this frame ' "$log/refs/race.out" > "$log/upload-spills.txt"
if [ -s "$log/upload-spills.txt" ]; then note="$note upload spill frames $(wc -l < "$log/upload-spills.txt" | tr -d ' ');"; fi
rm -rf "$log/refs"
# audio and pad: the smoke launch (runtime/smoke.sh), then dstest fake's result
runtime/smoke.sh "$log/smoke" > "$log/smoke.txt" 2>&1; smoke_rc=$?
s="$log/smoke/smoke"
ls "$s" 2>/dev/null | grep -q -i -E "crash|hang_|stall_" && fail "pad: smoke launch report $(ls "$s" | grep -i -E 'crash|hang_|stall_' | head -3 | tr '\n' ' ')"
check_smoke_result "$log/smoke.txt"
[ "$smoke_rc" = 0 ] || fail "pad: smoke launch/check exit $smoke_rc"
wait "$dstest_pid"; dstest_pid=
grep -q " 0 failed" "$log/dstest.txt" || fail "audio: dstest fake: $(grep -m1 -E 'FAIL|failed' "$log/dstest.txt")"
wait "$mix_pid"; mix_rc=$?; mix_pid=
grep -q '^MIXSMOKE PASS' "$log/mixsmoke.txt" && [ "$mix_rc" = 0 ] || fail "mixsmoke: $(grep -m1 '^MIXSMOKE' "$log/mixsmoke.txt" | cut -c1-300)"
mix=$(grep -m1 '^MIXSMOKE PASS' "$log/mixsmoke.txt" | sed -E 's/^MIXSMOKE PASS: //')
smoke=$(grep '^SUMMARY' "$log/smoke.txt" | cut -c9-); nd=$(grep -o '^[0-9]* checks' "$log/dstest.txt")
rm -rf "$log/smoke" "$log/mix"
echo "GATE PASS $(git rev-parse --short HEAD): race fps $fps p99 ${p99} ms (info), no crash/hang/stall to sim 45 s; probe $np/9 PASS; refs $nr frames ok; $smoke; dstest fake $nd ok; mixsmoke $mix;$note"
