#!/bin/sh
# Links the boot runtime against the recompiled game (run port/recomp/build_all.py,
# port/host/audio/build.sh and port/host/shaders/build.sh first).
set -e
. "$(dirname "$0")/env.sh"; nfs_need_py
cd "$(dirname "$0")"
# The game-data tables (gen/*.inc) are generated from the player's own files, never kept in the repository; a no-op
# (one stat per input) when nothing changed since the last run.
"$TOOLS_PY" -I ../tools/gen_tables.py --if-stale
FF=../third_party/ffmpeg/lib
# The build id in problem reports and the F10 menu: commit (+dirty), build time.
BUILD_ID="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)$(git diff --quiet HEAD 2>/dev/null || echo +)$(date +' %Y-%m-%d %H:%M')"
# NFS_PUBLIC=1: the public build. Extra EA Trax (owner-only) is not compiled: extratrax_public.c replaces extratrax.c and
# extratrax_codec.c, so its code, catalog, F10 row and owner paths are absent (not merely off).
# The prepared-car catalog likewise uses a no-I/O public stub; discovery is private.
if [ "${NFS_PUBLIC:-0}" = 1 ]; then
    PUB_FLAGS="-DNFS_PUBLIC=1"; XT_SRC="extratrax_public.c pub_discord.c pub_update.m pub_settings.c"; CARS_SRC=private_cars_public.c
else
    PUB_FLAGS=""; XT_SRC="extratrax.c extratrax_codec.c"; CARS_SRC=private_cars.c
fi
SERVER_FLAGS=""; SERVER_LIB=""
if [ -f ../recomp/out/server/server.a ]; then
    SERVER_FLAGS="-DNFS_SERVER_LINKED=1"; SERVER_LIB=../recomp/out/server/server.a
fi
clang -O1 -g -w $PUB_FLAGS $SERVER_FLAGS -I ../recomp -I ../recomp/out -I ../third_party/ffmpeg/include -fobjc-arc -DPORT_BUILD_ID="\"$BUILD_ID\"" \
    boot.c car85.c $CARS_SRC server_loader.c lanserver_selftest.c app.m heap.c d3dx.c d3dx_effect.c threads.c user32.m d3d9.m render.m render_target.m render_query.m render_present.m render_post.m render_thread.m gpupass.m dsound.c audio_limiter.c posaudio.c dinput.c ws2_32.c interp.c diag.c imports.c movies.c x360.m x360_post.m x360_textures.m padprompts.c padbind.c controltype.c steerresp.c chasecam.c vinyls.m widescreen.c padguest.c qrsplit.c splitworld.c splitcapture.c mirror.c lampglow.c aa_setting.c lampbatch.m detail.c streaming.c bootflow.c pacing.c rumble.m padfx.m feedback.c overlay.m probe.m materials.m lighting.c settings.c settings_port.c portmenu.m report.m onscreen_keyboard.c gpuclients.m gameplay_opts.c fe_console.c attrib_x360.c trees.c auxlights.c x360audio.c $XT_SRC x360abk.c x360copspeech.c x360nisaudio.c x360_shadow_fit.cpp ../recomp/out/speed.a $SERVER_LIB \
    ../host/audio/build/libhostaudio.a ../host/audio/build/libmedia.a ../host/shaders/build/libsmtranslate.a \
    $FF/libavformat.a $FF/libavcodec.a $FF/libswscale.a $FF/libswresample.a $FF/libavutil.a \
    -lc++ -lz -framework AudioToolbox -framework CoreAudio -framework CoreFoundation \
    -framework Cocoa -framework QuartzCore -framework Metal -framework ImageIO -framework CoreGraphics -framework CoreText -framework CoreServices -framework GameController -framework IOKit -o "${BOOT_OUT:-boot}"
