#!/bin/sh
# Links the boot runtime against the recompiled game (run port/recomp/build_all.py,
# port/host/audio/build.sh and port/host/shaders/build.sh first).
set -e
. "$(dirname "$0")/env.sh"; nfs_need_py
cd "$(dirname "$0")"
# The game-data tables (gen/*.inc) are generated from the player's own files, never kept in the repository; a no-op
# (one stat per input) when nothing changed since the last run.
"$TOOLS_PY" -I ../tools/gen_tables.py --if-stale
build_key=$("$TOOLS_PY" -I ../recomp/build_inputs.py --runtime-key)
if "$TOOLS_PY" -I ../recomp/build_inputs.py --runtime-fresh "${BOOT_OUT:-boot}" "$build_key"; then
    echo 'runtime: production inputs and output unchanged'
    exit 0
fi
FF=../third_party/ffmpeg/lib
# The build id in problem reports and the F10 menu: commit (+dirty), build time.
# Reused artifacts keep the id/time of their actual production build.
BUILD_ID="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)$(git diff --quiet HEAD 2>/dev/null || echo +)$(date +' %Y-%m-%d %H:%M')"
# NFS_PUBLIC=1: the public build. Extra EA Trax (owner-only) is not compiled: extratrax_public.c replaces extratrax.c and
# extratrax_codec.c, so its code, catalog, F10 row and owner paths are absent (not merely off).
# The prepared-car catalog likewise uses a no-I/O public stub; discovery is private.
if [ "${NFS_PUBLIC:-0}" = 1 ]; then
    PUB_FLAGS="-DNFS_PUBLIC=1"; XT_SRC="extratrax_public.c pub_discord.c pub_update.m pub_settings.c report_github.m"; CARS_SRC=private_cars_public.c
else
    PUB_FLAGS=""; XT_SRC="extratrax.c extratrax_codec.c"; CARS_SRC=private_cars.c
fi
SERVER_FLAGS=""; SERVER_LIB=""
if [ -f ../recomp/out/server/server.a ]; then
    SERVER_FLAGS="-DNFS_SERVER_LINKED=1"; SERVER_LIB=../recomp/out/server/server.a
fi
clang -O1 -g -w $PUB_FLAGS $SERVER_FLAGS -I ../recomp -I ../recomp/out -I ../third_party/ffmpeg/include -fobjc-arc -DPORT_BUILD_ID="\"$BUILD_ID\"" \
    boot.c car85.c $CARS_SRC server_loader.c lanserver_selftest.c app.m pub_update_launch.m heap.c d3dx.c d3dx_effect.c threads.c user32.m d3d9.m render.m pipeline_cache.m render_target.m render_query.m render_present.m render_post.m post_cuts.c render_thread.m gpupass.m dsound.c audio_limiter.c posaudio.c dinput.c ws2_32.c interp.c diag.c diag_car.c imports.c movies.c x360.m x360_post.m x360_textures.m padprompts.c padbind.c controltype.c pedals.c steerresp.c chasecam.c vinyls.m widescreen.c padguest.c qrsplit.c splitworld.c splitcapture.c mirror.c lampglow.c aa_setting.c alpha_to_coverage.c lampbatch.m detail.c streaming.c bootflow.c pacing.c rumble.m padfx.m feedback.c overlay.m probe.m materials.m roadfilm.m lighting.c settings.c settings_port.c portmenu.m guide.m report.m onscreen_keyboard.c gpuclients.m gameplay_opts.c fe_console.c attrib_x360.c trees.c auxlights.c x360audio.c $XT_SRC x360abk.c x360copspeech.c x360nisaudio.c x360_shadow_fit.cpp roam_game.c roam_hud.c roam_core.c roam_relay.c roam_server.c roam_online.c roam_code.c natmap.c natpmp.c pcp.c upnp.c ../recomp/out/speed.a $SERVER_LIB \
    ../host/audio/build/libhostaudio.a ../host/audio/build/libmedia.a ../host/shaders/build/libsmtranslate.a \
    $FF/libavformat.a $FF/libavcodec.a $FF/libswscale.a $FF/libswresample.a $FF/libavutil.a \
    -lc++ -lz -framework AudioToolbox -framework CoreAudio -framework CoreFoundation \
    -framework Cocoa -framework QuartzCore -framework Metal -framework ImageIO -framework CoreGraphics -framework CoreText -framework CoreServices -framework GameController -framework IOKit -framework SystemConfiguration -o "${BOOT_OUT:-boot}"
"$TOOLS_PY" -I ../recomp/build_inputs.py --runtime-record "${BOOT_OUT:-boot}" "$build_key"
