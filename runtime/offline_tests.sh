#!/bin/sh
# offline_tests.sh [name ...] - every offline test of the port in one command (~2 min on an idle machine): no game launch, no
# game slot, no real audio (null/manual sinks only), no rumble device, nothing written outside a scratch dir. Prints one line
# per test and ends with "OFFLINE PASS (N tests ...)" or "OFFLINE FAIL: <names>". gate.sh runs it as its first step
# after the recompiler build, so a test that rots (a stub missing after a runtime change, a default the user changed) fails the
# gate instead of failing silently until someone runs it. With names: only those (e.g. `offline_tests.sh x360music_test aa_setting`).
#
# What runs (autodiscovered, so a new test joins by existing):
#   inputs_paths_test.py: legacy private-path scan; inputs_preflight_test.py: bounded startup opens + runtime resolver
#   runtime/*_test.py (including roam_online_test.py codecs/loopback relay and roam_session_test.py lifecycle)
#   car85_language_test.py: public packed_display with translated language loader/decoder and old-pointer failure control
#   car_style_fallback_test.py: installed additions' HOOD/ROOF targets plus synthetic STYLE controls (exit 3 without packs)
#   lasterror_test.py: production import/GIL handovers and ReadFile errors, with pinned original control
#   runtime/clipcycle_test.py: Video clip stop/restart, no Finder; pinned original callback fails
#   runtime/replay_test.py: 90-second compressed retention, keyframe decode, cap, AAC preroll and format boundaries
#   runtime/replay_export_test.py: exact H.264/HEVC MP4 edits, decoded markers/AAC and seven broken runs
#   runtime/replay_capture_test.py: Arm/Save/Disarm through Metal/VT; replay_input_test.py and replay_bindings_test.py: shortcuts/career collisions
#   runtime/live_encode_test.py: hardware H.264 CBR, parsed SPS/PPS/IDRs, shared surfaces and isolated failures
#   runtime/live_seg_test.py: in-memory TS boundaries, absolute DTS, ADTS and bounded consumers
#   runtime/livestream_test.py: ordered loopback HTTPS, bounded/cancelled uploads and key privacy
#   runtime/threadhandles_test.py: real pthread close/exit lifetimes and pinned slot-exhaustion control
#   library_libmode_test.py: isolated host archives without recomp/out, with target/early-exit controls
#   library_archive_test.py: exact inventory/arm64/14.5, extra guest-named member and 27.0 controls
#   materials_stub_test.py: every X360 material source with --stub includes, Metal compile and three-source broken control
#   recomp/*_test.py  tools_snapshot/test_*.py  tools/publish/test_*.py  tests/setup/test_*.py
#   recomp/windows_host_test.py: synthetic guest VM/PE/import/TLS/Lua/GIL/thread proof, plus broken controls
#   recomp/windows_hooks_test.py: SDK-free COFF symbols/relocations and hook-removed control (exit 3 without COFF clang)
#   runtime/taskdelay_test.py: production SimTask hook + translated constructor/Run; pinned predecessor must fail
#   the standalone C/ObjC/shell tests listed in run_one below (aa_setting_test.c, settings_live_test.c incl. its LIVE_TEST_BROKEN run, feedback_test.c, padlink_test.c incl. its mutation, framecap_test.c incl. its
#   --broken run, looppace_test.c incl. its --broken run, framelate_test.c incl. its --broken and --stale runs, heap_test.c, settings_look_test.c, note_len_test.c, rumble_test.m, jdlz_test.c, x360abk_test.c g3, lampbatch_test.m, soft_smoke_half_test.m incl. its two broken runs,
#   msaa_resolve_test.m, rectangle_clear_test.m (via rectangle_clear_test.py), dsound_dup_test.sh, wfx_cb_test.sh, defaults_check.py)
#   host/audio/build.sh (builds + runs leveltest, limitertest, limiter_guest_test with their broken controls), dstest (headless)
#   and ctest; host/shaders/build.sh (translator vs reference, Metal compile, GPU vs CPU) last, alone (heavy).
# Tiers (2026-11-03): a whole-suite run is the FAST tier unless OFFLINE_TIER=full: the integration suites listed in
# runtime/offline_tier2.txt (15 names, each 47-289 s; 1,120 of the parallel phase's 2,950 test-seconds in the 711 s reference run)
# are deferred and the PASS line ends with "TIER2 deferred (N)". The full tier is the authority: gate.sh and
                                                                                                                          
# in-job run_tests and hand runs get the fast tier. Named runs ignore the tier. A tier-2 name that is not a registered
# test fails the run (list rot). Proof: runtime/offline_tier_test.py (plan-mode fast vs full differ by exactly the 15 names;
# an unknown tier-2 name -> OFFLINE FAIL: tier2; the two authorities grep as full).
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
# Deadline tests (short wall-clock polling/waits) run serially after the parallel batch:
#   private tool tests listed in runtime/offline_serial.txt (1-3 s stub, polling and shutdown deadlines; load caused a false timeout)
#   replay_capture_test (bounded two-second encoder watermarks)
#   pub_update_flow_test (120 s disk-image prepare: 30-75 s in the parallel batch, 130 s timeout in one gate, 10 s alone)
#   roam_admission_test: synthetic cookie times also depend on the real five-second key clock
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
# Bound private-source access before tests, builds or cached gate results.
if [ -f tools/inputs.py ] && [ "${OFFLINE_TESTS_PLAN:-0}" != 1 ] && [ "${1:-}" != --one ]; then
    "$TOOLS_PY" -I tools/inputs.py
    inputs_rc=$?
    case $inputs_rc in 0|3) ;; *) echo "INPUTS FAIL: preflight"; exit 1 ;; esac
fi
# A whole-suite run (no test names) takes one of OFFLINE_SUITE_SLOTS (2) machine-wide slots; named-test runs do not.
if [ $# -eq 0 ] && [ "${OFFLINE_TESTS_PLAN:-0}" != 1 ]; then . "$here/runtime/suite_slot.sh"; suite_slot_acquire; fi
[ "${1:-}" = --one ] || [ "${OFFLINE_TESTS_PLAN:-0}" = 1 ] || "$TOOLS_PY" -I tools/gen_tables.py --if-stale || { echo "OFFLINE FAIL: gen_tables"; exit 1; }   # runtime/gen/*.inc
export NFS_RUMBLE=off NFS_PAD_TRIGGERS=off NFS_PAD_LIGHT=off NFS_PAD_PLAYERS=off NFS_PAD_ROAD=off NFS_HDR_OUTPUT=${NFS_HDR_OUTPUT:-off}
py=$TOOLS_PY
X360=$NFS_PARENT/x360/files
LIMIT=${OFFLINE_TEST_LIMIT:-420}      # seconds per test
CF="-std=c11 -Wall -Wextra -Werror -g -fsanitize=address,undefined"
FF=$here/third_party/ffmpeg

# Named release-critical tests: strict mode rejects their missing inputs/devices.
release_critical="maxperf_career_test maxperf_checkout_test padprompts_shop_test cliprec clip_present_test fsauto_test nativefs_test contact_mass_test test_car_physics soft_smoke_test particles_review_test particles_physical_test junkman_test roam_budget_test realworld_materials_test traffic_detail_test traffic_role_test pedals_test controltype_test padlayout_test"

# ---- one test: run_one <name> <dir for its scratch files>; exit 0 pass, 3 skip, else fail
run_one() {
    n=$1; d=$2; mkdir -p "$d"
    case $n in
    ws2_policy|ws2_policy_test) clang -O1 -Iruntime -Irecomp -Irecomp/out runtime/ws2_policy_test.c -o "$d/t" && exec "$d/t" ;;
    settings_look|settings_look_test) clang $CF -I recomp runtime/settings_look_test.c runtime/settings.c -o "$d/t" && exec "$d/t" ;;
    note_len|note_len_test) clang $CF -I recomp -I runtime runtime/note_len_test.c runtime/text_fit.c -o "$d/t" && exec "$d/t" ;;
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
        # These list the host's processes; the proof sandbox refuses /bin/ps (all three failed there, exit 126).
        case $n in player_running_test|solo_matrix_test|*_runner_test)   # a private test, matched by class
            if [ -n "${CODEX_SANDBOX:-}${NFS_GPU_SANDBOX:-}" ] && ! /bin/ps -p $$ >/dev/null 2>&1; then
                echo "SKIP: PENDING host run (process table refused in this sandbox): runtime/offline_tests.sh $n"; return 3
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
        [ "$n" != gate_skip_test ] || exec "$py" -u -I "$f"
        exec "$py" -I "$f" ;;
    settings_live) clang $CF -I recomp runtime/settings_live_test.c runtime/settings.c runtime/offline_stubs.c -o "$d/t" || return 1
                 "$d/t" || return 1
                 clang -DLIVE_TEST_BROKEN $CF -I recomp runtime/settings_live_test.c runtime/settings.c runtime/offline_stubs.c -o "$d/broken" || return 1
                 if "$d/broken" >/dev/null 2>&1; then echo "settings_live broken run unexpectedly passed (live_env no longer matters)"; return 1; fi ;;
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
                 sed '/^void padfx_set_triggers(int on)/,/^}/c\
void padfx_set_triggers(int on) { trig_sw = on != 0; padfx_stop(); }' runtime/padfx.m >"$d/trigger-stop.m"
                 for broken in late-stop stale-sink default-off pedal-curves trigger-stop; do
                     cmp -s runtime/padfx.m "$d/$broken.m" && { echo "padfx $broken mutation did not apply"; return 1; }
                     clang -Wall -Wextra -Werror -g -fsanitize=address,undefined -fobjc-arc -I recomp -I runtime -I "$d" \
                         -DPADFX_SOURCE="\"$d/$broken.m\"" runtime/padfx_test.m -framework Foundation -o "$d/$broken" || return 1
                     "$d/$broken" >"$d/$broken.log" 2>&1; rc=$?
                     tail -1 "$d/$broken.log"
                     [ "$rc" = 1 ] || { echo "padfx $broken unexpectedly passed/crashed"; return 1; }
                 done ;;
    padlink)     clang $CF -I recomp -I runtime runtime/padlink_test.c -o "$d/t" || return 1
                 "$d/t" || return 1
                 # Old output hooks carried neither controller nor connection identity.
                 sed 's/if (!l->id || l->token != token) continue;/if (!l->id) continue;/; s/if (links\[i\].id == id) token/if (links[i].id) token/' runtime/padlink.c >"$d/identity.c"
                 # Old disconnect retained the previous transport for the next connection.
                 sed 's/memset(l, 0, sizeof \*l); l->transport = -1;/l->id = NULL; l->token = 0;/' runtime/padlink.c >"$d/transport.c"
                 for variant in identity transport; do
                     cmp -s runtime/padlink.c "$d/$variant.c" && { echo "padlink $variant mutation did not apply"; return 1; }
                 done
                 sed 's/roll(\&links\[i\], i, now, force);/if (0) roll(\&links[i], i, now, force);/' runtime/padlink.c >"$d/idle.c"
                 sed 's/if (!diag_on) return;/if (0) return;/' runtime/padlink.c >"$d/off.c"
                 for variant in idle off; do
                     cmp -s runtime/padlink.c "$d/$variant.c" && { echo "padlink $variant mutation did not apply"; return 1; }
                     clang $CF -I recomp -I runtime -DPADLINK_SOURCE="\"$d/$variant.c\"" runtime/padlink_test.c -o "$d/$variant" || return 1
                     "$d/$variant" >"$d/$variant.log" 2>&1; rc=$?
                     tail -1 "$d/$variant.log"
                     [ "$rc" = 1 ] || { echo "padlink $variant unexpectedly passed/crashed"; return 1; }
                 done
                 sed -n '/^int padbind_trigger_role(/,/^}/p' runtime/padbind.c >"$d/padfx_roles.inc"
                 for proof in fx-identity hid-identity hid-transport; do
                     case $proof in
                         fx-*) source=runtime/padfx_test.m; broken="$d/identity.c"; frameworks="-framework Foundation" ;;
                         hid-identity) source=runtime/rumble_test.m; broken="$d/identity.c"; frameworks="-framework Foundation -framework IOKit -lz" ;;
                         hid-transport) source=runtime/rumble_test.m; broken="$d/transport.c"; frameworks="-framework Foundation -framework IOKit -lz" ;;
                     esac
                     clang $CF -fobjc-arc -I recomp -I runtime -I "$d" -DPADLINK_SOURCE="\"$broken\"" "$source" $frameworks -o "$d/$proof" || return 1
                     "$d/$proof" >"$d/$proof.log" 2>&1; rc=$?
                     tail -1 "$d/$proof.log"
                     [ "$rc" = 1 ] || { echo "padlink $proof old behavior unexpectedly passed/crashed"; return 1; }
                 done ;;
    feedback)    clang $CF -I recomp runtime/feedback_test.c -o "$d/t" || return 1
                 "$d/t" || return 1
                 "$d/t" --broken-padfx >"$d/broken.log" 2>&1; rc=$?
                 tail -1 "$d/broken.log"
                 [ "$rc" = 1 ] ;;
    heap)        clang -O2 -DHEAP_TEST -o "$d/t" runtime/heap_test.c runtime/heap.c || return 1
                 "$d/t" || return 1
                 # Restore the pinned unchecked-rounding path without consulting repository history.
                 sed '/if (n > h->npages \* PAGE) goto out;/d' runtime/heap.c >"$d/heap-unchecked.c"
                 cmp -s runtime/heap.c "$d/heap-unchecked.c" && { echo 'heap bounds mutation did not apply'; return 1; }
                 clang -O2 -DHEAP_TEST -Iruntime -o "$d/broken" runtime/heap_test.c "$d/heap-unchecked.c" || return 1
                 "$d/broken" >"$d/broken.log" 2>&1; rc=$?
                 tail -1 "$d/broken.log"
                 [ "$rc" = 1 ] && grep -q '^heap_test: 12 FAILURES$' "$d/broken.log" &&
                     grep -q '^realloc bounds: 48 cases, 36 rejected, 36 preserved, 12 oversized writes, largest 4294917295 bytes$' "$d/broken.log" ;;
    rumble)      clang -Wall -Wextra -Werror -g -fsanitize=address,undefined -fobjc-arc -I recomp runtime/rumble_test.m \
                     -framework Foundation -framework IOKit -lz -o "$d/t" && exec "$d/t" ;;
    framecap)    clang -std=c11 -Wall -Wextra -Werror runtime/framecap_test.c -o "$d/t" || return 1
                 "$d/t" || return 1
                 if "$d/t" --broken >/dev/null 2>&1; then echo "framecap --broken unexpectedly passed (the check no longer fails when the limit is off)"; return 1; fi ;;
    looppace)    clang -std=c11 -Wall -Wextra -Werror runtime/looppace_test.c -o "$d/t" || return 1
                 "$d/t" || return 1
                 if "$d/t" --broken >/dev/null 2>&1; then echo "looppace --broken unexpectedly passed (the check no longer fails when the limit leaves the loop at the tick rate)"; return 1; fi ;;
    pacewait)    clang -std=c11 -Wall -Wextra -Werror runtime/pacewait_test.c -o "$d/t" || return 1
                 "$d/t" || return 1
                 # Restore the original single-wait behavior; signals must end it early.
                 sed 's/while (mach_wait_until/if (mach_wait_until/' runtime/pacewait.h >"$d/pacewait.h"
                 cmp -s runtime/pacewait.h "$d/pacewait.h" && { echo 'pacewait mutation did not apply'; return 1; }
                 cp runtime/pacewait_test.c "$d/pacewait_test.c"
                 clang -std=c11 -Wall -Wextra -Werror "$d/pacewait_test.c" -o "$d/broken" || return 1
                 "$d/broken" >"$d/broken.log" 2>&1; rc=$?
                 cat "$d/broken.log"
                 [ "$rc" = 1 ] && grep -q '^PACEWAIT FAIL$' "$d/broken.log" ;;
    framelate)   clang -std=c11 -Wall -Wextra -Werror runtime/framelate_test.c -o "$d/t" || return 1
                 "$d/t" || return 1
                 if "$d/t" --broken >/dev/null 2>&1; then echo "framelate --broken unexpectedly passed (the check no longer fails when a frame lands between the monitor's reads)"; return 1; fi
                 if "$d/t" --stale >/dev/null 2>&1; then echo "framelate --stale unexpectedly passed (the check no longer fails when the stamp goes stale before the clock read)"; return 1; fi ;;
    jdlz)        [ -d "$X360/CARS" ] || { echo "SKIP: no $X360/CARS"; return 3; }
                 clang -O1 -o "$d/t" runtime/jdlz_test.c && exec "$d/t" "$X360/CARS" ;;
    x360abk)     clang -O1 -g -fsanitize=address,undefined -I third_party/ffmpeg/include runtime/x360abk_test.c runtime/x360abk.c third_party/ffmpeg/lib/libavcodec.a \
                     third_party/ffmpeg/lib/libavutil.a -lz -o "$d/t" || return 1
                 "$d/t" bounds || return 1
                 [ -d "$X360/SOUND" ] && [ -d "$NFS_GAME/SOUND" ] || { echo "SKIP: needs the X360 extract and the game folder"; return 3; }
                 "$d/t" parsehash "$X360/SOUND" "$NFS_GAME/SOUND" || return 1
                 exec "$d/t" g3 "$X360/SOUND" "$NFS_GAME/SOUND" ;;
    cliprec)    clang++ -std=c++17 -O1 -fobjc-arc -Wno-deprecated-declarations -S runtime/cliprec.mm -o "$d/cliprec.s" || return 1
                 "$TOOLS_PY" -I - "$d/cliprec.s" <<'PYOFF' || return 1
import re, sys
body = open(sys.argv[1]).read().split('_cliprec_present:', 1)[1].split('.cfi_endproc', 1)[0]
def off_ok(code):
    return not re.search(r'^\s*bl\s|\bsp\b', code, re.M) and bool(re.search(r'^\s*ret\s*$', code, re.M))
assert off_ok(body), 'inactive hook has calls or a stack frame'
assert not off_ok(body.replace('ret', 'bl _objc_retain\n\tret')), 'broken Off path escaped check'
print('cliprec Off proof: 0 calls / 0 stack frame; injected retain: 1 rejected call')
PYOFF
                 # Compile private objects here: host_audio runs in parallel and replaces its archives.
                 clang++ -std=c++17 -O1 -Wall -Wextra -Werror -Wno-unused-parameter -Wno-deprecated-declarations -fobjc-arc \
                     -I third_party/ffmpeg/include runtime/cliprec_test.mm runtime/live_encode.mm runtime/live_seg.mm runtime/livestream.mm runtime/clip_packets.mm runtime/replay.mm runtime/replay_export.mm host/audio/dsound_host.cpp media/media.cpp \
                     "$FF/lib/libavformat.a" "$FF/lib/libavcodec.a" "$FF/lib/libswscale.a" "$FF/lib/libswresample.a" "$FF/lib/libavutil.a" \
                     -lz -framework AudioToolbox -framework CoreAudio -framework CoreFoundation -framework Cocoa -framework Metal -framework QuartzCore -framework AVFoundation \
                     -framework VideoToolbox -framework CoreVideo -framework CoreMedia -o "$d/t" || return 1
                 mkdir -p "${NFS_METAL_CACHE:-.metal-cache}"
                 clip_scratch=$(mktemp -d "${NFS_METAL_CACHE:-.metal-cache}/cliprec-fixture.XXXXXX") || return 1
                 exec "$d/t" "${NFS_METAL_CACHE:-.metal-cache}/cliprec-proof.log" "$clip_scratch" ;;
    lampbatch)   clang -fobjc-arc -framework Metal -framework Foundation runtime/lampbatch_test.m runtime/lampbatch.m -o "$d/t" && exec "$d/t" ;;
    soft_smoke_half) clang -fobjc-arc -DSOFT_SMOKE_HALF_TEST -framework Metal -framework Foundation runtime/soft_smoke_half_test.m runtime/soft_smoke_half.m -o "$d/t" || return 1
                 "$d/t" || return 1; ! "$d/t" --broken >/dev/null && ! "$d/t" --broken-far >/dev/null ;;
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
    # Both artifact checks perform complete builds with the bundled toolchain.
    case "$n" in test_release_artifact|test_prebuilt_install) LIMIT=${OFFLINE_TEST_LIMIT:-1800} ;; esac
    case "$n" in prebuilt_relink_test) LIMIT=${OFFLINE_TEST_LIMIT:-600} ;; esac
    log=$OT_DIR/$n.log
    sh runtime/owner_hold.sh "$n"   # never beside the player's game: tests load the GPU and CoreAudio like a game
    s=$(date +%s)
    # Tests and their watchdog never inherit the suite locks (fds 8/9): a test that leaves a child running would pin it.
    ( run_one "$n" "$OT_DIR/work/$n" ) 8>&- 9>&- >"$log" 2>&1 &
    pid=$!
    # The watchdog's own sleep must die with it: an orphaned sleep kept the caller's output pipe open for $LIMIT s
    # (362 leftover sleeps after one morning of suites; a `| tail` caller waited on them).
    ( trap 'kill "$sp" 2>/dev/null; exit 0' TERM; bytes=$(wc -c <"$log")
      while :; do
          sleep "$LIMIT" & sp=$!; wait "$sp"
          next=$(wc -c <"$log")
          [ "$n" = gate_skip_test ] && [ "$next" -gt "$bytes" ] || break
          bytes=$next
      done
      kill "$pid" 2>/dev/null; echo "offline_tests: killed after ${LIMIT}s" >>"$log" ) 8>&- 9>&- </dev/null >/dev/null 2>&1 &
    wd=$!
    wait "$pid"; rc=$?
    kill "$wd" 2>/dev/null; wait "$wd" 2>/dev/null
    # Release hosts opt in; missing development inputs remain skips by default.
    if [ "$rc" = 3 ] && [ "${NFS_TESTS_NOSKIP:-0}" = 1 ]; then
        case " $release_critical " in *" $n "*)
            echo "FAIL: release-critical test $n skipped (NFS_TESTS_NOSKIP=1)" >>"$log"
            rc=1 ;;
        esac
    fi
    case $rc in 0) r=PASS ;; 3) r=SKIP ;; *) r=FAIL ;; esac
    echo "$r" >"$OT_DIR/$n.result"
    secs=$(( $(date +%s) - s ))
    printf '%-34s %s %ss\n' "$n" "$r" "$secs"
    # Per-test seconds persist (append-only) so slow tests are found from data, not from one printed run.
    [ -z "${OT_TIMES:-}" ] || printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "${OFFLINE_TIER:-fast}" "$n" "$secs" "$r" "${OFFLINE_JOBS:-12}" >>"$OT_TIMES" 2>/dev/null
    exit 0
fi

# ---- the list
# live_picture_sweep_test is discovered below: production table inventory, batching,
# diagnostic driver and refs-tolerance undo comparison, including a skipped-undo seed.
pyfiles() { for f in runtime/*_test.py recomp/*_test.py tools_snapshot/test_*.py tools/publish/test_*.py tests/setup/test_*.py runtime/defaults_check.py; do [ -f "$f" ] && echo "$f"; done; }
pylist() { pyfiles | sed 's|.*/||; s|\.py$||'; }
standalone="cliprec note_len aa_setting settings_live feedback padfx padlink ws2_policy settings_look heap rumble framecap looppace pacewait framelate jdlz x360abk lampbatch soft_smoke_half msaa_resolve dsound_dup wfx_cb host_audio"
# Timing-sensitive tests: audio requires zero wall-clock underruns; short-deadline public tests are here,
# private tool tests in runtime/offline_serial.txt (absent in public trees).
# Live encoder mutation builds exceeded the suite watchdog under parallel load.
deadline_tests="live_encode_test host_audio pub_update_flow_test pacewait test_backend report_lock_test roam_loopback_fixture_test roam_admission_test windows_sync_test replay_capture_test livestream_test stream_live_test $(cat runtime/offline_serial.txt 2>/dev/null | tr '\n' ' ')"
serial=${OFFLINE_SERIAL_TESTS:-$deadline_tests}
duplicates=$({ pylist; printf '%s\n' $standalone host_shaders; } | sort | uniq -d)
[ -z "$duplicates" ] || { echo "OFFLINE FAIL: duplicate test names: $duplicates"; exit 1; }
if [ $# -gt 0 ]; then names="$*"; else names="$(pylist | tr '\n' ' ')$standalone"; fi
tier2_note=
if [ $# -eq 0 ] && [ "${OFFLINE_TIER:-fast}" != full ]; then    # fast tier: defer runtime/offline_tier2.txt (see the header)
    tier2=$(sed 's/#.*//' "${OFFLINE_TIER2_FILE:-runtime/offline_tier2.txt}" 2>/dev/null | awk 'NF { print $1 }' | tr '\n' ' ')
    kept=; ntier2=0
    for n in $tier2; do
        case " $names " in *" $n "*) ;; *) echo "OFFLINE FAIL: tier2: $n is not a registered test (runtime/offline_tier2.txt)"; exit 1 ;; esac
        ntier2=$((ntier2 + 1))
    done
    for n in $names; do case " $tier2 " in *" $n "*) ;; *) kept="$kept $n" ;; esac; done
    names=${kept# }
    [ $ntier2 -eq 0 ] || tier2_note="; TIER2 deferred ($ntier2; OFFLINE_TIER=full runs them):$(printf ' %s' $tier2)"
fi

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

# Per-test seconds of whole-suite runs: date, tier, test, seconds, result, jobs (OFFLINE_TIMES=- turns it off).
OT_TIMES=${OFFLINE_TIMES-${NFS_RUNS:-$NFS_PARENT/.runs}/offline_times.tsv}; [ "$OT_TIMES" != - ] || OT_TIMES=; [ $# -eq 0 ] || OT_TIMES=; export OT_TIMES
OT_DIR=$(mktemp -d "${TMPDIR:-/tmp}/offline.XXXXXX"); export OT_DIR
mkdir -p "$OT_DIR/work"
OT_TOOL_TMPDIR=${TMPDIR:-/tmp/}; export OT_TOOL_TMPDIR   # xcrun resolves downloaded toolchains via its original temp cache
export PYTHONUNBUFFERED=1             # stdout and stderr interleave in order, so a traceback lands in the tail
export TMPDIR=$OT_DIR/work              # tests' mkdtemp scratch lands here and goes with it (reverb keeps 10 s WAVs)
t0=$(date +%s)
[ "$names" = "$*" ] || :
# shellcheck disable=SC2086
[ -z "$par" ] || printf '%s\n' $par | xargs -P "${OFFLINE_JOBS:-12}" -I{} sh "$self" --one {}
for n in $selected_serial; do sh "$self" --one "$n"; done
[ -z "$heavy" ] || sh "$self" --one "$heavy"

fails= nskip= skipped= nrun=0
for n in $par $selected_serial $heavy; do
    r=$(cat "$OT_DIR/$n.result" 2>/dev/null)
    case $r in
    PASS) nrun=$((nrun + 1)) ;;
    SKIP) skipped="$skipped $n"
          echo "---- $n (skip reason)"
          reason=$(sed -n '/SKIP:/p' "$OT_DIR/$n.log" | tail -3 | cut -c1-240)
          echo "${reason:-SKIP: exit 3 without a reported reason}" ;;
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
echo "OFFLINE PASS ($nrun tests ${secs}s${skipped:+; SKIPPED:$skipped}$tier2_note)"
