#!/bin/sh
# Builds the host DirectSound library and its tests.
#   ./build.sh lib [--release]   archives only; release pins arm64/macOS 14.5 and checks members
#   build/libhostaudio.a  DirectSound 8 (dsound_host.cpp)
#   build/libmedia.a      port/media/media.cpp (AudioOutput, AC-3), compiled here unchanged
#   build/dstest          headless tests (./build/dstest; add "device" to also run the muted CoreAudio tests)
#   build/ctest           links the library from plain C
#   build/timedtap_test   timed game-mix capture and numerical baseline/mutation proofs (no audio device)
# The runtime links: build/libhostaudio.a build/libmedia.a + the FFmpeg libs, -lc++ -lz and the frameworks below.
set -e
cd "$(dirname "$0")"
mode=tests
release=0
for arg in "$@"; do
    case "$arg" in
        lib) mode=lib ;;
        --release) release=1 ;;
        *) echo 'usage: build.sh [lib] [--release]' >&2; exit 2 ;;
    esac
done
[ "$release" = 0 ] || [ "$mode" = lib ] || { echo '--release requires lib' >&2; exit 2; }
F=../../third_party/ffmpeg
mkdir -p build
CXX="clang++ -std=c++17 -O2 -Wall -Wextra -Wno-unused-parameter"
[ "$release" = 0 ] || CXX="$CXX -arch arm64 -mmacosx-version-min=14.5"
$CXX \
    -c \
    dsound_host.cpp \
    -o build/dsound_host.o
$CXX \
    -I"$F/include" \
    -c \
    ../../media/media.cpp \
    -o build/media.o
rm -f build/libhostaudio.a build/libmedia.a
ar rcs build/libhostaudio.a build/dsound_host.o
ar rcs build/libmedia.a build/media.o
if [ "$release" = 1 ]; then
    "${TOOLS_PY:-$(cd ../../.. && pwd)/tools/.venv/bin/python}" -I ../../media/check_libraries.py --group audio
fi
[ "$mode" != lib ] || exit 0
$CXX \
    dstest.cpp \
    -o build/dstest \
    build/libhostaudio.a \
    build/libmedia.a \
    "$F/lib/libavformat.a" \
    "$F/lib/libavcodec.a" \
    "$F/lib/libswscale.a" \
    "$F/lib/libswresample.a" \
    "$F/lib/libavutil.a" \
    -lz \
    -framework AudioToolbox \
    -framework CoreAudio \
    -framework CoreFoundation
# The C API must compile and link from plain C (the runtime is C).
clang \
    -std=c11 \
    -Wall \
    -Wextra \
    ctest.c \
    -o build/ctest \
    build/libhostaudio.a \
    build/libmedia.a \
    "$F/lib/libavformat.a" \
    "$F/lib/libavcodec.a" \
    "$F/lib/libswscale.a" \
    "$F/lib/libswresample.a" \
    "$F/lib/libavutil.a" \
    -lc++ \
    -lz \
    -framework AudioToolbox \
    -framework CoreAudio \
    -framework CoreFoundation
# Per-channel level stats (perf.tsv rms_/peak_/clip/dip_): leveltest, and leveltest_broken against a copy whose
# 50 ms sub-window is 500 ms (`build/leveltest --broken` must fail). A leveltest failure fails this build.
LIBS="
    $F/lib/libavformat.a
    $F/lib/libavcodec.a
    $F/lib/libswscale.a
    $F/lib/libswresample.a
    $F/lib/libavutil.a
    -lz
    -framework
    AudioToolbox
    -framework
    CoreAudio
    -framework
    CoreFoundation
"
# Forced dstest timing proofs: restore each original ordering separately; each must fail exactly.
# Fixed: post-stop 0/512, startup 0/256. Broken: post-stop 256/768, startup 64/512.
for control in STOP_BEFORE_SNAPSHOT PLAY_BEFORE_REFILL; do
    $CXX \
        -DDSTEST_$control \
        dstest.cpp \
        -o build/dstest_$control \
        build/libhostaudio.a \
        build/libmedia.a \
        $LIBS
    if NFS_AUDIO=null build/dstest_$control timing > build/dstest_$control.txt 2>&1; then
        echo "error: dstest $control negative control unexpectedly passed"; exit 1
    fi
done
grep -q 'forced post-stop tap: 256 bad of 768' build/dstest_STOP_BEFORE_SNAPSHOT.txt
grep -q 'forced startup refill: 64 bad of 512' build/dstest_PLAY_BEFORE_REFILL.txt
NFS_AUDIO=null build/dstest timing > build/dstest_timing.txt 2>&1 || { tail -30 build/dstest_timing.txt; exit 1; }
$CXX \
    leveltest.cpp \
    -o build/leveltest \
    build/libhostaudio.a \
    build/libmedia.a \
    $LIBS
sed -e 's/++d.sub_frames == 2400)/++d.sub_frames == 24000)/' -e 's|d.sub_sumsq\[q\] / 2400.0|d.sub_sumsq[q] / 24000.0|' dsound_host.cpp > build/dsound_host_broken.cpp
grep -q "== 24000)" build/dsound_host_broken.cpp && grep -q "/ 24000.0" build/dsound_host_broken.cpp
$CXX \
    -I. \
    -c \
    build/dsound_host_broken.cpp \
    -o build/dsound_host_broken.o
$CXX \
    leveltest.cpp \
    -o build/leveltest_broken \
    build/dsound_host_broken.o \
    build/libmedia.a \
    $LIBS
build/leveltest > build/leveltest.txt || { cat build/leveltest.txt; exit 1; }
# dstest fake snddrop (old issue #8): its broken control detaches a stalled output after 5.0 s instead of 0.5 s, so the
# game's write cursor freezes past the 60-poll limit (334-343 polls measured; fixed: 34-36); it must fail. Fake device only, no CoreAudio.
sed -e 's/d.out->stalled(0.5)/d.out->stalled(5.0)/' dsound_host.cpp > build/dsound_host_stall_broken.cpp
grep -q 'stalled(5.0)' build/dsound_host_stall_broken.cpp
$CXX \
    -I. \
    -c \
    build/dsound_host_stall_broken.cpp \
    -o build/dsound_host_stall_broken.o
$CXX \
    dstest.cpp \
    -o build/dstest_snddrop_broken \
    build/dsound_host_stall_broken.o \
    build/libmedia.a \
    $LIBS
if build/dstest_snddrop_broken fake snddrop > build/dstest_snddrop_broken.txt 2>&1; then
    echo 'error: dstest fake snddrop negative control unexpectedly passed'; exit 1
fi
grep -q 'FAIL.*write cursor stuck for' build/dstest_snddrop_broken.txt
# Linked game-mix limiter, manual sink only; negative control must fail with measurable clipping.
$CXX \
    limitertest.cpp \
    -o build/limitertest \
    build/libhostaudio.a \
    build/libmedia.a \
    $LIBS
$CXX \
    -DNFS_LIMITER_BROKEN \
    -c \
    dsound_host.cpp \
    -o build/dsound_host_limiter_broken.o
$CXX \
    -DNFS_LIMITER_BROKEN \
    limitertest.cpp \
    -o build/limitertest_broken \
    build/dsound_host_limiter_broken.o \
    build/libmedia.a \
    $LIBS
build/limitertest > build/limitertest.txt 2>&1 || { tail -30 build/limitertest.txt; exit 1; }
if build/limitertest --broken > build/limitertest_broken.txt 2>&1; then
    echo 'error: limiter negative control unexpectedly passed'; exit 1
fi
grep -q 'FAIL: On zero full-scale/clamped samples' build/limitertest_broken.txt
# Extract only the real translated converter for the pre-int16 proof (no game entry point or device).
"${TOOLS_PY:-$(cd ../../.. && pwd)/tools/.venv/bin/python}" -I - <<'PY'
from pathlib import Path
import re
source = Path('../../recomp/out/speed_55.c').read_text()
match = re.search(r'void f_00825957(?:_orig)?\(Cpu \*restrict c\) \{.*?\n\}', source, re.S)
assert match, 'translated Snd converter missing; run recomp/build_all.py'
body = re.sub(r'f_00825957(?:_orig)?\(', 'f_00825957_orig(', match[0], count=1)
Path('build/limiter_guest_original.c').write_text('#include "runtime.h"\nvoid dispatch(Cpu *, uint32_t);\n' + body + '\n')
PY
clang \
    -O2 \
    -I../../recomp \
    -c \
    build/limiter_guest_original.c \
    -o build/limiter_guest_original.o
clang \
    -O2 \
    -I../../recomp \
    -c \
    ../../runtime/audio_limiter.c \
    -o build/audio_limiter.o
clang \
    -O2 \
    -I../../recomp \
    limiter_guest_test.c \
    -o build/limiter_guest_test \
    build/audio_limiter.o \
    build/limiter_guest_original.o \
    build/libhostaudio.a \
    build/libmedia.a \
    $LIBS \
    -lc++
clang \
    -O2 \
    -I../../recomp \
    limiter_guest_test.c \
    -o build/limiter_guest_test_broken \
    build/audio_limiter.o \
    build/limiter_guest_original.o \
    build/dsound_host_limiter_broken.o \
    build/libmedia.a \
    $LIBS \
    -lc++
build/limiter_guest_test > build/limiter_guest_test.txt 2>&1 || { tail -30 build/limiter_guest_test.txt; exit 1; }
if build/limiter_guest_test_broken > build/limiter_guest_test_broken.txt 2>&1; then
    echo 'error: pre-int16 limiter negative control unexpectedly passed'; exit 1
fi
grep -q '^FAIL.*clips(on, SAMPLES)' build/limiter_guest_test_broken.txt
echo ok

# Callback/parking and large-period PCM fixture: synthetic callbacks, fake device only, with/without diagnostics.
# Fixed: 144 checks / 0 failures each; detached-output mutant: 34 checks / 2 failures.
# Old 1024-frame floor: 12 failures, 48 underruns/mode, 72.8% zero fill.
$CXX \
    -DMEDIA_AUDIO_CALLBACK_TEST \
    -I"$F/include" \
    dstest.cpp \
    -o build/audio_callback_test \
    $LIBS
BOOT_DIAG=1 build/audio_callback_test > build/audio_callback_test.txt 2>&1 || { tail -30 build/audio_callback_test.txt; exit 1; }
env -u BOOT_DIAG build/audio_callback_test > build/audio_callback_nodiag_test.txt 2>&1 || { tail -30 build/audio_callback_nodiag_test.txt; exit 1; }
# Reintroduce the original detached-output state before the synthetic PCM pulls.
sed 's/^        device->out->stop_for_test();$/        detach(*device, "synthetic default-device change");/' dstest.cpp > build/audio_detached_broken.cpp
$CXX \
    -DMEDIA_AUDIO_CALLBACK_TEST \
    -I. \
    -I"$F/include" \
    build/audio_detached_broken.cpp \
    -o build/audio_detached_broken \
    $LIBS
if BOOT_DIAG=1 build/audio_detached_broken > build/audio_detached_broken.txt 2>&1; then
    echo 'error: detached-output negative control unexpectedly passed'; exit 1
fi
grep -q '^FAIL: large-period PCM output remains attached' build/audio_detached_broken.txt
sed 's/target = std::max(target, d.out->minimum_queue_frames())/target = target/' dsound_host.cpp > build/dsound_host_period_broken.cpp
# Include the mutated mixer from the generated fixture while retaining the real media implementation.
sed -e 's|#include "dsound_host.cpp"|#include "build/dsound_host_period_broken.cpp"|' dstest.cpp > build/audio_period_broken.cpp
$CXX \
    -DMEDIA_AUDIO_CALLBACK_TEST \
    -I. \
    -I"$F/include" \
    build/audio_period_broken.cpp \
    -o build/audio_period_broken \
    $LIBS
if BOOT_DIAG=1 build/audio_period_broken > build/audio_period_broken.txt 2>&1; then
    echo 'error: large-period PCM negative control unexpectedly passed'; exit 1
fi
grep -q 'FAIL: large-period PCM has no zero-fill or underruns' build/audio_period_broken.txt

# Timing-change negative control: retain the open-time floor but ignore HAL notifications.
# Fixed: target 8320, 0 shortage callbacks; cached: target 1024, 1 shortage callback (3072/4096 frames zero).
sed 's/if (s->timing_active \&\& device == s->opened_on) s->refresh_pcm_timing();/if (s->timing_active \&\& device == s->opened_on) (void)0;/' ../../media/media.cpp > build/media_timing_broken.cpp
grep -q 'device == s->opened_on) (void)0;' build/media_timing_broken.cpp
sed 's|#include "../../media/media.cpp"|#include "build/media_timing_broken.cpp"|' dstest.cpp > build/audio_timing_broken.cpp
$CXX \
    -DMEDIA_AUDIO_CALLBACK_TEST \
    -I. \
    -I../../media \
    -I"$F/include" \
    build/audio_timing_broken.cpp \
    -o build/audio_timing_broken \
    $LIBS
if BOOT_DIAG=1 build/audio_timing_broken > build/audio_timing_broken.txt 2>&1; then
    echo 'error: cached PCM timing negative control unexpectedly passed'; exit 1
fi
grep -q 'FAIL: running mixer follows same-device period growth' build/audio_timing_broken.txt
grep -q 'FAIL: grown period callback has no zero fill' build/audio_timing_broken.txt

# Diagnostic minimum negative control: remove only the per-frame minimum update, preserving limiter audio.
# Fixed: 48 checks / 0 failures; minimum update removed: 3 failures (minimum reported as unity).
sed 's/if constexpr (Diag) min_gain_ = std::min(min_gain_, gain_);/if constexpr (Diag) (void)0;/' peak_limiter.h > build/peak_limiter_stats_broken.h
sed 's|"peak_limiter.h"|"build/peak_limiter_stats_broken.h"|' dsound_host.cpp > build/dsound_host_stats_broken.cpp
sed 's|"peak_limiter.h"|"build/peak_limiter_stats_broken.h"|' limitertest.cpp > build/limitertest_stats_broken.cpp
$CXX \
    -I. \
    build/limitertest_stats_broken.cpp \
    build/dsound_host_stats_broken.cpp \
    -o build/limitertest_stats_broken \
    build/libmedia.a \
    $LIBS
if build/limitertest_stats_broken > build/limitertest_stats_broken.txt 2>&1; then
    echo 'error: limiter minimum negative control unexpectedly passed'; exit 1
fi
grep -q 'FAIL: host stats report linked minimum next to limited samples' build/limitertest_stats_broken.txt

# Timed post-limiter capture: manual/null/offline outputs only, and numerical old-code/mutation controls.
# The pinned baseline adapter deliberately supplies PCM with zero metadata; it must link and fail numerically.
$CXX \
    -I. \
    timedtap_test.cpp \
    -o build/timedtap_test \
    build/libmedia.a \
    $LIBS
build/timedtap_test > build/timedtap_test.txt 2>&1 || { tail -30 build/timedtap_test.txt; exit 1; }
$CXX \
    -I. \
    -DTIMEDTAP_BASELINE \
    timedtap_test.cpp \
    -o build/timedtap_baseline \
    build/libmedia.a \
    $LIBS
if build/timedtap_baseline > build/timedtap_baseline.txt 2>&1; then
    echo 'error: timed capture baseline unexpectedly passed'; exit 1
fi
grep -q 'pcm_samples=3510 metadata_blocks=0/3 distinct_channels=18/18' build/timedtap_baseline.txt
grep -q 'FAIL: timed metadata present at actual rate' build/timedtap_baseline.txt
# Each independent mutation must fail its named numeric contract assertion.
sed 's/b.first_sample_host_time = d.manual_host_time + frame_ticks(d.written - d.manual_frame, b.sample_rate);/b.first_sample_host_time = mach_absolute_time();/' dsound_host.cpp > build/timedtap_arrival_source.cpp
sed 's/frame_ticks(ahead, b.sample_rate)/frame_ticks(0, b.sample_rate)/' dsound_host.cpp > build/timedtap_queue_source.cpp
sed 's/d.out->queued_frames() + d.out->pending_frames()/d.out->queued_frames()/' dsound_host.cpp > build/timedtap_pending_source.cpp
sed 's/while (c.gate.load(std::memory_order_acquire) \& 2u) std::this_thread::yield();/(void)0;/' dsound_host.cpp > build/timedtap_lifetime_source.cpp
sed 's/capture_deliver(timing, pcm);/auto late = timing; late.sample_rate = d.cfg.mix_rate; capture_deliver(late, pcm);/' dsound_host.cpp > build/timedtap_snapshot_source.cpp
for variant in arrival queue pending lifetime snapshot; do
    $CXX \
        -I. \
        -DTIMEDTAP_SOURCE=\"build/timedtap_${variant}_source.cpp\" \
        timedtap_test.cpp \
        -o build/timedtap_$variant \
        build/libmedia.a \
        $LIBS
    if build/timedtap_$variant > build/timedtap_$variant.txt 2>&1; then
        echo "error: timed capture $variant mutant unexpectedly passed"; exit 1
    fi
done
grep -q 'FAIL: manual sample clock <= one input sample' build/timedtap_arrival.txt
grep -q 'FAIL: 4096-frame playback lead <= one input sample error' build/timedtap_queue.txt
grep -q 'FAIL: pending encoder input contributes to playback lead' build/timedtap_pending.txt
grep -q 'FAIL: stop waits for in-flight callback' build/timedtap_lifetime.txt
grep -q 'FAIL: delivery retains PCM rate and clock snapshot across concurrent rebase' build/timedtap_snapshot.txt
