#!/bin/sh
# offline_tests.sh [name ...] - every offline test of the port in one command (~2 min on an idle machine): no game launch, no
# game slot, no real audio (null/manual sinks only), no rumble device, nothing written outside a scratch dir. Prints one line
# per test and ends with "OFFLINE PASS (N tests ...)" or "OFFLINE FAIL: <names>". gate.sh runs it as its first step
# after the recompiler build, so a test that rots (a stub missing after a runtime change, a default the user changed) fails the
# gate instead of failing silently until someone runs it. With names: only those (e.g. `offline_tests.sh x360music_test aa_setting`).
#
# What runs (autodiscovered, so a new test joins by existing):
#   runtime/*_test.py (including roam_online_test.py codecs/loopback relay and roam_session_test.py lifecycle)
#   recomp/*_test.py  tools_snapshot/test_*.py  tools/publish/test_*.py  tests/setup/test_*.py
#   the standalone C/ObjC/shell tests listed in run_one below (aa_setting_test.c, feedback_test.c, framecap_test.c incl. its
#   --broken run, heap_test.c, settings_look_test.c, rumble_test.m, jdlz_test.c, x360abk_test.c g3, lampbatch_test.m,
#   msaa_resolve_test.m, rectangle_clear_test.m (via rectangle_clear_test.py), dsound_dup_test.sh, wfx_cb_test.sh, defaults_check.py)
#   host/audio/build.sh (builds + runs leveltest, limitertest, limiter_guest_test with their broken controls), dstest (headless)
#   and ctest; host/shaders/build.sh (translator vs reference, Metal compile, GPU vs CPU) last, alone (heavy).
# Run through host_audio: dsound_dup_test.c (via dsound_dup_test.sh), ctest.c, dstest.cpp, leveltest.cpp, limitertest.cpp,
# limiter_guest_test.c. Run through host_shaders: host/shaders/tests/*. Not run: media/mediatest.cpp (needs a movie argument;
# it is a diagnostic tool). Asset-bound tests (X360 extract, game folder) print SKIP when the
# input is missing, and the summary lists them; a development checkout with the inputs has all of them.
# Repo-wide scan: every tracked or new (not ignored) test_*.py / *_test.* anywhere in the checkout must be run by this script
# (a .py in the list below; anything else named here, paired with a .py driver, or named by host/audio or host/shaders
# build.sh) or sit on the notrun list with its reason; otherwise the run fails as "unregistered:<path>".
# Scan proof: tests/scanproof/test_scanproof.py (new folder) -> OFFLINE FAIL: unregistered:tests/scanproof/test_scanproof.py. Clean tree: 0 unregistered.
# Security-fixture sandbox guard: harmless fixture skips (exit 3, 0 executions); broken pattern runs it (exit 0,
# 1 execution). Publication fixture check, 2026-10-28; no game process involved.
# Deadline tests (<5s wall-clock polling/waits) run serially after the parallel batch:
#   private tool tests listed in runtime/offline_serial.txt (1-3 s stub, polling and shutdown deadlines; load caused a false timeout)
#   test_backend 3s ready-file polling deadline
# Offline-tested by itself: break any test (e.g. flip an assertion in runtime/framecap_test.c) -> OFFLINE FAIL: framecap.
# Discovery proof: 140 registered names (7 Setup), 0 collisions, exit 0; injecting a duplicate test_app exits 1.
# test_release_artifact is autodiscovered: builds packaged Setup with bundled Python
# and scratch HOME/CLT (no game launch); exit 3 without the Setup artifact or PC input.
# Artifact scan proof: packaged source + Python added 319 spurious test paths;
# excluding generated root build/dist output leaves 0 artifact paths in the scan.
. "$(dirname "$0")/env.sh"; nfs_need_py
cd "$(dirname "$0")/.." || exit 2
here=$PWD; self=$here/runtime/${0##*/}
[ "${1:-}" = --one ] || [ "${OFFLINE_TESTS_PLAN:-0}" = 1 ] || "$TOOLS_PY" -I tools/gen_tables.py --if-stale || { echo "OFFLINE FAIL: gen_tables"; exit 1; }   # runtime/gen/*.inc
export NFS_RUMBLE=off NFS_PAD_TRIGGERS=off NFS_PAD_LIGHT=off NFS_PAD_PLAYERS=off NFS_PAD_ROAD=off NFS_HDR_OUTPUT=${NFS_HDR_OUTPUT:-off}
py=$TOOLS_PY
X360=$NFS_PARENT/x360/files
LIMIT=${OFFLINE_TEST_LIMIT:-420}      # seconds per test
CF="-std=c11 -Wall -Wextra -Werror -g -fsanitize=address,undefined"
FF=$here/third_party/ffmpeg

# ---- one test: run_one <name> <dir for its scratch files>; exit 0 pass, 3 skip, else fail
run_one() {
    n=$1; d=$2; mkdir -p "$d"
    case $n in
    ws2_policy|ws2_policy_test) clang -O1 -Iruntime -Irecomp -Irecomp/out runtime/ws2_policy_test.c -o "$d/t" && exec "$d/t" ;;
    settings_look|settings_look_test) clang $CF -I recomp runtime/settings_look_test.c runtime/settings.c -o "$d/t" && exec "$d/t" ;;
    *_test|test_*|defaults_check)       # Python-driven tests
        for f in runtime/$n.py recomp/$n.py tools_snapshot/$n.py tools/publish/$n.py tests/setup/$n.py; do [ -f "$f" ] && break; done
        case $f in tests/setup/*)
            # Swift's default module cache is outside the suite's scratch dir.
            export CLANG_MODULE_CACHE_PATH="$d/modules" SWIFT_MODULECACHE_PATH="$d/modules" ;;
        esac
        # Unicorn can crash at initialization in restricted sandboxes. Without an explicitly
        # supplied compatible library, leave these tests for an unrestricted host run.
        if [ -n "${CODEX_SANDBOX:-}" ] && [ -z "${LIBUNICORN_PATH:-}" ] && grep -q difftest "$f"; then
            echo "SKIP: PENDING host run (Unicorn crashes in this sandbox): runtime/offline_tests.sh $n"; return 3
        fi
        # Private security fixtures are omitted from publication and need an unrestricted host.
        # Match the fixture class so the public script does not name an absent tool.
        case $n in *_game_mcp_test)
            if [ -n "${CODEX_SANDBOX:-}${NFS_GPU_SANDBOX:-}" ]; then
                echo "SKIP: PENDING host run (host-only security test): runtime/offline_tests.sh $n"; return 3
            fi ;;
        esac
        case $n in
        test_release_artifact)
            version=$(sed -n 's/^PUBLIC_VERSION = "\([^"]*\)".*/\1/p' tools/setup_kit/backend.py)
            [ -n "$version" ] || { echo 'missing current Setup version'; return 1; }
            exec "$py" -I "$f" --expected-version "$version" ;;
        test_x360_fe_textures)          # needs the X360 ISO, the game folder and the generated HD set
            iso="$NFS_PARENT/Need for Speed - Most Wanted x360 (USA).iso"
            [ -f "$iso" ] && [ -d "$NFS_GAME" ] && [ -d "$NFS_PARENT/x360/hd_textures_mips_alpha" ] || { echo "SKIP: needs the X360 ISO, game folder and x360/hd_textures_mips_alpha"; return 3; }
            exec "$py" -I "$f" "$iso" "$NFS_GAME" "$NFS_PARENT/x360/hd_textures_mips_alpha" ;;
        esac
        exec "$py" -I "$f" ;;
    aa_setting)  clang $CF -I recomp runtime/aa_setting_test.c runtime/aa_setting.c runtime/settings.c runtime/offline_stubs.c -o "$d/t" && exec "$d/t" ;;
    padfx)       sed -n '/^int padbind_trigger_role(/,/^}/p' runtime/padbind.c >"$d/padfx_roles.inc"
                 clang -Wall -Wextra -Werror -g -fsanitize=address,undefined -fobjc-arc -I recomp -I "$d" \
                     runtime/padfx_test.m -framework Foundation -o "$d/t" || return 1
                 "$d/t" || return 1
                 for broken in 1 2 3 4 5; do
                     "$d/t" "$broken" >"$d/broken-$broken.log" 2>&1; rc=$?
                     tail -1 "$d/broken-$broken.log"
                     [ "$rc" = 1 ] || { echo "padfx broken $broken unexpectedly passed/crashed"; return 1; }
                 done
                 sed 's/if (applied_generation < token) release_all();/(void)token; release_all();/' runtime/padfx.m >"$d/late-stop.m"
                 sed 's/{ invalidate_locked(); release_all();/{ release_all();/' runtime/padfx.m >"$d/stale-sink.m"
                 sed -E 's/!getenv\("(NFS_PAD_[A-Z]+)"\) \|\| env_on\("NFS_PAD_[A-Z]+"\)/env_on("\1")/g' runtime/padfx.m >"$d/default-off.m"
                 sed 's/0.10f + 0.15f \* travel/0.20f/; s/0.30f + 1.25f \* (travel - 0.60f)/0.15f + 0.45f * travel/' runtime/padfx.m >"$d/pedal-curves.m"
                 for broken in late-stop stale-sink default-off pedal-curves; do
                     cmp -s runtime/padfx.m "$d/$broken.m" && { echo "padfx $broken mutation did not apply"; return 1; }
                     clang -Wall -Wextra -Werror -g -fsanitize=address,undefined -fobjc-arc -I recomp -I runtime -I "$d" \
                         -DPADFX_SOURCE="\"$d/$broken.m\"" runtime/padfx_test.m -framework Foundation -o "$d/$broken" || return 1
                     "$d/$broken" >"$d/$broken.log" 2>&1; rc=$?
                     tail -1 "$d/$broken.log"
                     [ "$rc" = 1 ] || { echo "padfx $broken unexpectedly passed/crashed"; return 1; }
                 done ;;
    feedback)    clang $CF -I recomp runtime/feedback_test.c -o "$d/t" || return 1
                 "$d/t" || return 1
                 "$d/t" --broken-padfx >"$d/broken.log" 2>&1; rc=$?
                 tail -1 "$d/broken.log"
                 [ "$rc" = 1 ] ;;
    heap)        clang -O2 -DHEAP_TEST -o "$d/t" runtime/heap_test.c runtime/heap.c && exec "$d/t" ;;
    rumble)      clang -Wall -Wextra -Werror -g -fsanitize=address,undefined -fobjc-arc -I recomp runtime/rumble_test.m \
                     -framework Foundation -framework IOKit -lz -o "$d/t" && exec "$d/t" ;;
    framecap)    clang -std=c11 -Wall -Wextra -Werror runtime/framecap_test.c -o "$d/t" || return 1
                 "$d/t" || return 1
                 if "$d/t" --broken >/dev/null 2>&1; then echo "framecap --broken unexpectedly passed (the check no longer fails when the limit is off)"; return 1; fi ;;
    jdlz)        [ -d "$X360/CARS" ] || { echo "SKIP: no $X360/CARS"; return 3; }
                 clang -O1 -o "$d/t" runtime/jdlz_test.c && exec "$d/t" "$X360/CARS" ;;
    x360abk)     [ -d "$X360/SOUND" ] && [ -d "$NFS_GAME/SOUND" ] || { echo "SKIP: needs the X360 extract and the game folder"; return 3; }
                 clang -O1 -I third_party/ffmpeg/include runtime/x360abk_test.c runtime/x360abk.c third_party/ffmpeg/lib/libavcodec.a \
                     third_party/ffmpeg/lib/libavutil.a -lz -o "$d/t" && exec "$d/t" g3 "$X360/SOUND" "$NFS_GAME/SOUND" ;;
    lampbatch)   clang -fobjc-arc -framework Metal -framework Foundation runtime/lampbatch_test.m runtime/lampbatch.m -o "$d/t" && exec "$d/t" ;;
    msaa_resolve) clang -fobjc-arc -framework Metal -framework Foundation runtime/msaa_resolve_test.m -o "$d/t" && MTL_DEBUG_LAYER=1 exec "$d/t" ;;
    dsound_dup)  exec sh runtime/dsound_dup_test.sh ;;
    wfx_cb)      exec sh host/audio/wfx_cb_test.sh ;;
    host_audio)  # build.sh builds and runs leveltest/limitertest/limiter_guest_test and their broken controls (set -e); then the headless tests
                 sh host/audio/build.sh >"$d/build.txt" 2>&1 || { tail -30 "$d/build.txt"; return 1; }
                 (cd host/audio && NFS_AUDIO=null ./build/dstest) || return 1
                 (cd host/audio && ./build/ctest) ;;
    host_shaders) # build.sh exits nonzero on any failed step and prints BUILD_SH PASS last; require both
                 sh host/shaders/build.sh >"$d/out.txt" 2>&1; rc=$?; cat "$d/out.txt"
                 [ $rc = 0 ] && tail -1 "$d/out.txt" | grep -q '^BUILD_SH PASS$' ;;
    *) echo "offline_tests: unknown test $n"; return 2 ;;
    esac
}

if [ "$1" = "--one" ]; then      # internal: run one test with a watchdog, log + result in $OT_DIR
    n=$2
    # A fresh player build includes FFmpeg and the complete recompiler/runtime.
    [ "$n" != test_release_artifact ] || LIMIT=${OFFLINE_TEST_LIMIT:-1800}
    log=$OT_DIR/$n.log; s=$(date +%s)
    ( run_one "$n" "$OT_DIR/work/$n" ) >"$log" 2>&1 &
    pid=$!
    # The watchdog's own sleep must die with it: an orphaned sleep kept the caller's output pipe open for $LIMIT s
    # (362 leftover sleeps after one morning of suites; a `| tail` caller waited on them).
    ( trap 'kill "$sp" 2>/dev/null; exit 0' TERM; sleep "$LIMIT" & sp=$!; wait "$sp"
      kill "$pid" 2>/dev/null; echo "offline_tests: killed after ${LIMIT}s" >>"$log" ) </dev/null >/dev/null 2>&1 &
    wd=$!
    wait "$pid"; rc=$?
    kill "$wd" 2>/dev/null; wait "$wd" 2>/dev/null
    case $rc in 0) r=PASS ;; 3) r=SKIP ;; *) r=FAIL ;; esac
    echo "$r" >"$OT_DIR/$n.result"
    printf '%-34s %s %ss\n' "$n" "$r" "$(( $(date +%s) - s ))"
    exit 0
fi

# ---- the list
pyfiles() { for f in runtime/*_test.py recomp/*_test.py tools_snapshot/test_*.py tools/publish/test_*.py tests/setup/test_*.py runtime/defaults_check.py; do [ -f "$f" ] && echo "$f"; done; }
pylist() { pyfiles | sed 's|.*/||; s|\.py$||'; }
standalone="aa_setting feedback padfx ws2_policy settings_look heap rumble framecap jdlz x360abk lampbatch msaa_resolve dsound_dup wfx_cb host_audio"
# Short-deadline tests: public ones here, private tool tests in runtime/offline_serial.txt (absent in public trees).
deadline_tests="test_backend roam_loopback_fixture_test $(cat runtime/offline_serial.txt 2>/dev/null | tr '\n' ' ')"
serial=${OFFLINE_SERIAL_TESTS:-$deadline_tests}
duplicates=$({ pylist; printf '%s\n' $standalone host_shaders; } | sort | uniq -d)
[ -z "$duplicates" ] || { echo "OFFLINE FAIL: duplicate test names: $duplicates"; exit 1; }
if [ $# -gt 0 ]; then names="$*"; else names="$(pylist | tr '\n' ' ')$standalone"; fi

# Keep every short-deadline integration test out of the parallel batch.
for n in $deadline_tests; do
    case " $serial " in *" $n "*) ;; *) echo "OFFLINE FAIL: short-deadline test is not serial: $n"; exit 1 ;; esac
done
selected_serial=
for n in $names; do case " $serial " in *" $n "*) selected_serial="$selected_serial $n" ;; esac; done
par= heavy=
for n in $names; do
    case $n in
        host_shaders) heavy=host_shaders ;;
        *) case " $serial " in *" $n "*) ;; *) par="$par $n" ;; esac ;;
    esac
done
[ $# -eq 0 ] && heavy=host_shaders
if [ "${OFFLINE_TESTS_PLAN:-0}" = 1 ]; then
    echo "PARALLEL:$par"
    echo "SERIAL:$selected_serial"
    exit 0
fi

# unregistered test sources (repo-wide, see the header). Not scanned: hidden dot-folders (local tooling, not port code).
# Deliberately not run:
#   runtime/roots_test.sh         two guarded game launches; run by hand
notrun=" runtime/roots_test.sh "
orphans=
if [ $# -eq 0 ]; then
    ran=$(pyfiles)
    # Installer output contains another source tree and Python's own tests; these
    # are artifact contents, not additional tests belonging to this checkout.
    files=$(git ls-files --cached --others --exclude-standard -- . ':!build/**' ':!dist/**' 2>/dev/null) ||
        files=$(find . \( -name .git -o -path ./build -o -path ./dist \) -prune -o -type f -print | sed 's|^\./||')   # not a git checkout
    for f in $(printf '%s\n' "$files" | grep -vE '(^|/)\.' | grep -E '(^|/)(test_[^/]*\.py|[^/]*_test\.[^/]+)$'); do
        [ -f "$f" ] || continue                                 # deleted, still in the index
        case $notrun in *" $f "*) continue ;; esac
        b=${f##*/}
        case $b in *.py) printf '%s\n' "$ran" | grep -qxF "$f" || orphans="$orphans $f"; continue ;; esac
        [ -f "${f%.*}.py" ] && continue                         # harness of a Python-driven test
        grep -qF "$b" "$self" host/audio/build.sh host/shaders/build.sh || orphans="$orphans $f"
    done
fi

OT_DIR=$(mktemp -d "${TMPDIR:-/tmp}/offline.XXXXXX"); export OT_DIR
mkdir -p "$OT_DIR/work"
OT_TOOL_TMPDIR=${TMPDIR:-/tmp/}; export OT_TOOL_TMPDIR   # xcrun resolves downloaded toolchains via its original temp cache
export PYTHONUNBUFFERED=1             # stdout and stderr interleave in order, so a traceback lands in the tail
export TMPDIR=$OT_DIR/work              # tests' mkdtemp scratch lands here and goes with it (reverb keeps 10 s WAVs)
t0=$(date +%s)
[ "$names" = "$*" ] || :
# shellcheck disable=SC2086
[ -z "$par" ] || printf '%s\n' $par | xargs -P "${OFFLINE_JOBS:-6}" -I{} sh "$self" --one {}
for n in $selected_serial; do sh "$self" --one "$n"; done
[ -z "$heavy" ] || sh "$self" --one "$heavy"

fails= nskip= skipped= nrun=0
for n in $par $selected_serial $heavy; do
    r=$(cat "$OT_DIR/$n.result" 2>/dev/null)
    case $r in
    PASS) nrun=$((nrun + 1)) ;;
    SKIP) skipped="$skipped $n" ;;
    *)    fails="$fails $n"; echo "---- $n (last lines of its log)"; tail -12 "$OT_DIR/$n.log" | cut -c1-240
          # The scratch dir is removed below; keep the whole failing log (tracebacks sit above the tail).
          keep=${OFFLINE_FAIL_LOGS:-${OT_TOOL_TMPDIR%/}/offline-fail}; kept=$keep/$n.$(date +%Y%m%d-%H%M%S).log
          mkdir -p "$keep" && cp "$OT_DIR/$n.log" "$kept" && echo "full log: $kept" ;;
    esac
done
for o in $orphans; do fails="$fails unregistered:$o"; done
secs=$(( $(date +%s) - t0 ))
rm -rf "$OT_DIR"
if [ -n "$fails" ]; then echo "OFFLINE FAIL:$fails (${secs}s)"; exit 1; fi
echo "OFFLINE PASS ($nrun tests ${secs}s${skipped:+; SKIPPED (input missing):$skipped})"
