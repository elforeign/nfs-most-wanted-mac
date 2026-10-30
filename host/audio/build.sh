#!/bin/sh
# Builds the host DirectSound library and its tests.
#   build/libhostaudio.a  DirectSound 8 (dsound_host.cpp)
#   build/libmedia.a      port/media/media.cpp (AudioOutput, AC-3), compiled here unchanged
#   build/dstest          headless tests (./build/dstest; add "device" to also run the muted CoreAudio tests)
#   build/ctest           links the library from plain C
# The runtime links: build/libhostaudio.a build/libmedia.a + the FFmpeg libs, -lc++ -lz and the frameworks below.
set -e
cd "$(dirname "$0")"
F=../../third_party/ffmpeg
mkdir -p build
CXX="clang++ -std=c++17 -O2 -Wall -Wextra -Wno-unused-parameter"
$CXX -c dsound_host.cpp -o build/dsound_host.o
$CXX -I"$F/include" -c ../../media/media.cpp -o build/media.o
rm -f build/libhostaudio.a build/libmedia.a
ar rcs build/libhostaudio.a build/dsound_host.o
ar rcs build/libmedia.a build/media.o
$CXX dstest.cpp -o build/dstest build/libhostaudio.a build/libmedia.a \
  "$F/lib/libavformat.a" "$F/lib/libavcodec.a" "$F/lib/libswscale.a" "$F/lib/libswresample.a" "$F/lib/libavutil.a" \
  -lz -framework AudioToolbox -framework CoreAudio -framework CoreFoundation
# The C API must compile and link from plain C (the runtime is C).
clang -std=c11 -Wall -Wextra ctest.c -o build/ctest build/libhostaudio.a build/libmedia.a \
  "$F/lib/libavformat.a" "$F/lib/libavcodec.a" "$F/lib/libswscale.a" "$F/lib/libswresample.a" "$F/lib/libavutil.a" \
  -lc++ -lz -framework AudioToolbox -framework CoreAudio -framework CoreFoundation
# Per-channel level stats (perf.tsv rms_/peak_/clip/dip_): leveltest, and leveltest_broken against a copy whose
# 50 ms sub-window is 500 ms (`build/leveltest --broken` must fail). A leveltest failure fails this build.
LIBS="$F/lib/libavformat.a $F/lib/libavcodec.a $F/lib/libswscale.a $F/lib/libswresample.a $F/lib/libavutil.a -lz -framework AudioToolbox -framework CoreAudio -framework CoreFoundation"
$CXX leveltest.cpp -o build/leveltest build/libhostaudio.a build/libmedia.a $LIBS
sed -e 's/++d.sub_frames == 2400)/++d.sub_frames == 24000)/' -e 's|d.sub_sumsq\[q\] / 2400.0|d.sub_sumsq[q] / 24000.0|' dsound_host.cpp > build/dsound_host_broken.cpp
grep -q "== 24000)" build/dsound_host_broken.cpp && grep -q "/ 24000.0" build/dsound_host_broken.cpp
$CXX -I. -c build/dsound_host_broken.cpp -o build/dsound_host_broken.o
$CXX leveltest.cpp -o build/leveltest_broken build/dsound_host_broken.o build/libmedia.a $LIBS
build/leveltest > build/leveltest.txt || { cat build/leveltest.txt; exit 1; }
# dstest fake snddrop (old issue #8): its broken control detaches a stalled output after 5.0 s instead of 0.5 s, so the
# game's write cursor freezes past the 60-poll limit (334-343 polls measured; fixed: 34-36); it must fail. Fake device only, no CoreAudio.
sed -e 's/d.out->stalled(0.5)/d.out->stalled(5.0)/' dsound_host.cpp > build/dsound_host_stall_broken.cpp
grep -q 'stalled(5.0)' build/dsound_host_stall_broken.cpp
$CXX -I. -c build/dsound_host_stall_broken.cpp -o build/dsound_host_stall_broken.o
$CXX dstest.cpp -o build/dstest_snddrop_broken build/dsound_host_stall_broken.o build/libmedia.a $LIBS
if build/dstest_snddrop_broken fake snddrop > build/dstest_snddrop_broken.txt 2>&1; then
    echo 'error: dstest fake snddrop negative control unexpectedly passed'; exit 1
fi
grep -q 'FAIL.*write cursor stuck for' build/dstest_snddrop_broken.txt
# Linked game-mix limiter, manual sink only; negative control must fail with measurable clipping.
$CXX limitertest.cpp -o build/limitertest build/libhostaudio.a build/libmedia.a $LIBS
$CXX -DNFS_LIMITER_BROKEN -c dsound_host.cpp -o build/dsound_host_limiter_broken.o
$CXX -DNFS_LIMITER_BROKEN limitertest.cpp -o build/limitertest_broken build/dsound_host_limiter_broken.o build/libmedia.a $LIBS
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
clang -O2 -I../../recomp -c build/limiter_guest_original.c -o build/limiter_guest_original.o
clang -O2 -I../../recomp -c ../../runtime/audio_limiter.c -o build/audio_limiter.o
clang -O2 -I../../recomp limiter_guest_test.c -o build/limiter_guest_test build/audio_limiter.o build/limiter_guest_original.o build/libhostaudio.a build/libmedia.a $LIBS -lc++
clang -O2 -I../../recomp limiter_guest_test.c -o build/limiter_guest_test_broken build/audio_limiter.o build/limiter_guest_original.o build/dsound_host_limiter_broken.o build/libmedia.a $LIBS -lc++
build/limiter_guest_test > build/limiter_guest_test.txt 2>&1 || { tail -30 build/limiter_guest_test.txt; exit 1; }
if build/limiter_guest_test_broken > build/limiter_guest_test_broken.txt 2>&1; then
    echo 'error: pre-int16 limiter negative control unexpectedly passed'; exit 1
fi
grep -q '^FAIL.*clips(on, SAMPLES)' build/limiter_guest_test_broken.txt
echo ok

# Callback/parking and large-period PCM fixture: synthetic callbacks, fake device only, with/without diagnostics.
# Fixed: 141 checks / 0 failures each; old 1024-frame floor: 12 failures, 48 underruns/mode, 72.8% zero fill.
$CXX -DMEDIA_AUDIO_CALLBACK_TEST -I"$F/include" dstest.cpp -o build/audio_callback_test $LIBS
BOOT_DIAG=1 build/audio_callback_test > build/audio_callback_test.txt 2>&1 || { tail -30 build/audio_callback_test.txt; exit 1; }
env -u BOOT_DIAG build/audio_callback_test > build/audio_callback_nodiag_test.txt 2>&1 || { tail -30 build/audio_callback_nodiag_test.txt; exit 1; }
sed 's/target = std::max(target, d.out->minimum_queue_frames())/target = target/' dsound_host.cpp > build/dsound_host_period_broken.cpp
# Include the mutated mixer from the generated fixture while retaining the real media implementation.
sed -e 's|#include "dsound_host.cpp"|#include "build/dsound_host_period_broken.cpp"|' dstest.cpp > build/audio_period_broken.cpp
$CXX -DMEDIA_AUDIO_CALLBACK_TEST -I. -I"$F/include" build/audio_period_broken.cpp -o build/audio_period_broken $LIBS
if BOOT_DIAG=1 build/audio_period_broken > build/audio_period_broken.txt 2>&1; then
    echo 'error: large-period PCM negative control unexpectedly passed'; exit 1
fi
grep -q 'FAIL: large-period PCM has no zero-fill or underruns' build/audio_period_broken.txt

# Timing-change negative control: retain the open-time floor but ignore HAL notifications.
# Fixed: target 8320, 0 shortage callbacks; cached: target 1024, 1 shortage callback (3072/4096 frames zero).
sed 's/if (s->timing_active \&\& device == s->opened_on) s->refresh_pcm_timing();/if (s->timing_active \&\& device == s->opened_on) (void)0;/' ../../media/media.cpp > build/media_timing_broken.cpp
grep -q 'device == s->opened_on) (void)0;' build/media_timing_broken.cpp
sed 's|#include "../../media/media.cpp"|#include "build/media_timing_broken.cpp"|' dstest.cpp > build/audio_timing_broken.cpp
$CXX -DMEDIA_AUDIO_CALLBACK_TEST -I. -I../../media -I"$F/include" build/audio_timing_broken.cpp -o build/audio_timing_broken $LIBS
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
$CXX -I. build/limitertest_stats_broken.cpp build/dsound_host_stats_broken.cpp -o build/limitertest_stats_broken build/libmedia.a $LIBS
if build/limitertest_stats_broken > build/limitertest_stats_broken.txt 2>&1; then
    echo 'error: limiter minimum negative control unexpectedly passed'; exit 1
fi
grep -q 'FAIL: host stats report linked minimum next to limited samples' build/limitertest_stats_broken.txt
