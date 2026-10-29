#!/bin/sh
# offline_tests.sh [name ...] - every offline test of the port in one command (~2 min on an idle machine): no game launch, no
# game slot, no real audio (null/manual sinks only), no rumble device, nothing written outside a scratch dir. Prints one line
# per test and ends with "OFFLINE PASS (N tests ...)" or "OFFLINE FAIL: <names>". gate.sh runs it as its first step
# after the recompiler build, so a test that rots (a stub missing after a runtime change, a default the user changed) fails the
# gate instead of failing silently until someone runs it. With names: only those (e.g. `offline_tests.sh x360music_test aa_setting`).
#
# What runs (autodiscovered, so a new test joins by existing):
#   runtime/*_test.py  recomp/*_test.py  tools_snapshot/test_*.py  tools/publish/test_*.py      the Python-driven tests (each compiles its C/ObjC harness)
#   the standalone C/ObjC/shell tests listed in run_one below (aa_setting_test.c, feedback_test.c, framecap_test.c incl. its
#   --broken run, heap_test.c, settings_look_test.c, rumble_test.m, jdlz_test.c, x360abk_test.c g3, lampbatch_test.m,
#   msaa_resolve_test.m, rectangle_clear_test.m (via rectangle_clear_test.py), dsound_dup_test.sh, wfx_cb_test.sh, defaults_check.py)
#   host/audio/build.sh (builds + runs leveltest, limitertest, limiter_guest_test with their broken controls), dstest (headless)
#   and ctest; host/shaders/build.sh (translator vs reference, Metal compile, GPU vs CPU) last, alone (heavy).
# Run through host_audio: dsound_dup_test.c (via dsound_dup_test.sh), ctest.c, dstest.cpp, leveltest.cpp, limitertest.cpp,
# limiter_guest_test.c. Not run: media/mediatest.cpp (needs a movie argument; it is a diagnostic tool), roots_test.sh (two guarded game launches; run by hand). Asset-bound tests (X360 extract, game folder) print SKIP when the
# input is missing, and the summary lists them; a development checkout with the inputs has all of them.
# A test whose source file is not mentioned in this script (or paired with a .py driver) fails the run as "unregistered", so a
# new standalone test cannot be forgotten.
# Security-fixture sandbox guard: harmless fixture skips (exit 3, 0 executions); broken pattern runs it (exit 0,
# 1 execution). Publication fixture check, 2026-10-28; no game process involved.
# Offline-tested by itself: break any test (e.g. flip an assertion in runtime/framecap_test.c) -> OFFLINE FAIL: framecap.
. "$(dirname "$0")/env.sh"; nfs_need_py
cd "$(dirname "$0")/.." || exit 2
here=$PWD; self=$here/runtime/offline_tests.sh
[ "${1:-}" = --one ] || "$TOOLS_PY" -I tools/gen_tables.py --if-stale || { echo "OFFLINE FAIL: gen_tables"; exit 1; }   # runtime/gen/*.inc
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
    *_test|test_*|defaults_check)       # Python-driven (runtime/ recomp/ tools_snapshot/)
        for f in runtime/$n.py recomp/$n.py tools_snapshot/$n.py tools/publish/$n.py; do [ -f "$f" ] && break; done
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
        test_x360_fe_textures)          # needs the X360 ISO, the game folder and the generated HD set
            iso="$NFS_PARENT/Need for Speed - Most Wanted x360 (USA).iso"
            [ -f "$iso" ] && [ -d "$NFS_GAME" ] && [ -d "$NFS_PARENT/x360/hd_textures_mips_alpha" ] || { echo "SKIP: needs the X360 ISO, game folder and x360/hd_textures_mips_alpha"; return 3; }
            exec "$py" -I "$f" "$iso" "$NFS_GAME" "$NFS_PARENT/x360/hd_textures_mips_alpha" ;;
        esac
        exec "$py" -I "$f" ;;
    aa_setting)  clang $CF -I recomp runtime/aa_setting_test.c runtime/aa_setting.c runtime/settings.c runtime/offline_stubs.c -o "$d/t" && exec "$d/t" ;;
    feedback)    clang $CF -I recomp runtime/feedback_test.c -o "$d/t" && exec "$d/t" ;;
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
    log=$OT_DIR/$n.log; s=$(date +%s)
    ( run_one "$n" "$OT_DIR/work/$n" ) >"$log" 2>&1 &
    pid=$!
    ( sleep "$LIMIT"; kill "$pid" 2>/dev/null; echo "offline_tests: killed after ${LIMIT}s" >>"$log" ) &
    wd=$!
    wait "$pid"; rc=$?
    kill "$wd" 2>/dev/null; wait "$wd" 2>/dev/null
    case $rc in 0) r=PASS ;; 3) r=SKIP ;; *) r=FAIL ;; esac
    echo "$r" >"$OT_DIR/$n.result"
    printf '%-34s %s %ss\n' "$n" "$r" "$(( $(date +%s) - s ))"
    exit 0
fi

# ---- the list
pylist() { for f in runtime/*_test.py recomp/*_test.py tools_snapshot/test_*.py tools/publish/test_*.py runtime/defaults_check.py; do [ -f "$f" ] && basename "$f" .py; done; }
standalone="aa_setting feedback ws2_policy settings_look heap rumble framecap jdlz x360abk lampbatch msaa_resolve dsound_dup wfx_cb host_audio"
if [ $# -gt 0 ]; then names="$*"; else names="$(pylist | tr '\n' ' ')$standalone"; fi

# unregistered test sources: anything *_test.* / test_*.py in the test directories that this script neither runs nor
# names (a .py driver of the same base name counts). Comments above name the ones it deliberately leaves out.
orphans=
if [ $# -eq 0 ]; then
    for f in runtime/*_test.* recomp/*_test.* host/audio/*test.* media/*test.* tools_snapshot/test_*.py; do
        [ -f "$f" ] || continue
        b=$(basename "$f"); base=${b%.*}
        case $b in *.py) continue ;; esac                       # Python tests are all run
        [ -f "${f%.*}.py" ] && continue                         # harness of a Python-driven test
        grep -q "$b" "$self" || orphans="$orphans $b"
    done
fi

OT_DIR=$(mktemp -d "${TMPDIR:-/tmp}/offline.XXXXXX"); export OT_DIR
mkdir -p "$OT_DIR/work"
OT_TOOL_TMPDIR=${TMPDIR:-/tmp/}; export OT_TOOL_TMPDIR   # xcrun resolves downloaded toolchains via its original temp cache
export TMPDIR=$OT_DIR/work              # tests' mkdtemp scratch lands here and goes with it (reverb keeps 10 s WAVs)
t0=$(date +%s)
heavy=
par=
for n in $names; do case $n in host_shaders) heavy=host_shaders ;; *) par="$par $n" ;; esac; done
[ "$names" = "$*" ] || :
# host_shaders joins the list last and runs alone
[ $# -eq 0 ] && heavy=host_shaders
# shellcheck disable=SC2086
printf '%s\n' $par | xargs -P "${OFFLINE_JOBS:-6}" -I{} sh "$self" --one {}
[ -z "$heavy" ] || sh "$self" --one "$heavy"

fails= nskip= skipped= nrun=0
for n in $par $heavy; do
    r=$(cat "$OT_DIR/$n.result" 2>/dev/null)
    case $r in
    PASS) nrun=$((nrun + 1)) ;;
    SKIP) skipped="$skipped $n" ;;
    *)    fails="$fails $n"; echo "---- $n (last lines of its log)"; tail -12 "$OT_DIR/$n.log" | cut -c1-240 ;;
    esac
done
for o in $orphans; do fails="$fails unregistered:$o"; done
secs=$(( $(date +%s) - t0 ))
rm -rf "$OT_DIR"
if [ -n "$fails" ]; then echo "OFFLINE FAIL:$fails (${secs}s)"; exit 1; fi
echo "OFFLINE PASS ($nrun tests ${secs}s${skipped:+; SKIPPED (input missing):$skipped})"
