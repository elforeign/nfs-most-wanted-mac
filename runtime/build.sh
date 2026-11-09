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
    "$TOOLS_PY" -I ../recomp/hook_coverage.py --host mac --binary "${BOOT_OUT:-boot}" --report ../recomp/out/hook-coverage.json
    echo 'runtime: production inputs and output unchanged'
    exit 0
fi
# cliprec.mm owns the AAC writer input and timed capture consumer. Its capture
# and fold-down symbols come from the host/media archives below; build those
# before this link (never rebuild shared archives from parallel test runners).
FF=../third_party/ffmpeg/lib
# The build id in problem reports and the F10 menu: commit (+dirty), build time.
# Reused artifacts keep the id/time of their actual production build.
BUILD_ID="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)$(git diff --quiet HEAD 2>/dev/null || echo +)$(date +' %Y-%m-%d %H:%M')"
# NFS_PUBLIC=1: the public build. Extra EA Trax (owner-only) is not compiled: extratrax_public.c replaces extratrax.c and
# extratrax_codec.c, so its code, catalog, F10 row and owner paths are absent (not merely off).
# Public imported cars use the trusted catalog and independent save admission.
if [ "${NFS_PUBLIC:-0}" = 1 ]; then
    PUB_FLAGS="-DNFS_PUBLIC=1"
    XT_SRC="
        extratrax_public.c
        pub_discord.c
        pub_update.m
        pub_settings.c
        report_github.m
        report_submit.m
    "
    CARS_SRC="
        private_cars_public.c
        car_catalog.c
        carsave_public.c
    "
else
    PUB_FLAGS=""
    XT_SRC="
        extratrax.c
        extratrax_codec.c
    "
    CARS_SRC="
        private_cars.c
        car_physics.c
    "
fi
SERVER_FLAGS=""; SERVER_LIB=""
if [ -f ../recomp/out/server/server.a ]; then
    SERVER_FLAGS="-DNFS_SERVER_LINKED=1"; SERVER_LIB=../recomp/out/server/server.a
fi
clang \
    -O2 \
    -g \
    -w \
    -DSAVE_TIMING_ENABLED \
    $PUB_FLAGS \
    $SERVER_FLAGS \
    -I ../recomp \
    -I ../recomp/out \
    -I ../third_party/ffmpeg/include \
    -fobjc-arc \
    -DPORT_BUILD_ID="\"$BUILD_ID\"" \
    music_balance.c \
    trigopt.c \
    car_audio.c \
    car_audio_runtime.c \
    car_audio_csis.c \
    car_audio_engine_class.c \
    car_audio_player.c \
    car_audio_backend.c \
    car_audio_filter.c \
    boot.c \
    cash_cheat.c \
    underglow_ui.c \
    underglow_menu.c \
    underglow_render.m \
    underglow_light.m \
    underglow.c \
    underglow_store.c \
    underglow_photometry.c \
    car85.c \
    bmw_availability.c \
    contact_mass.c \
    $CARS_SRC \
    server_loader.c \
    lanserver_selftest.c \
    app.m \
    pub_update_launch.m \
    heap.c \
    d3dx.c \
    d3dx_effect.c \
    threads.c \
    user32.m \
    d3d9.m \
    render.m \
    sign_diag.m \
    pipeline_cache.m \
    render_target.m \
    render_query.m \
    render_present.m \
    cliprec.mm \
    live_encode.mm \
    live_seg.mm \
    livestream.mm \
    stream_key.m \
    live_fake.m \
    clip_packets.mm \
    replay.mm \
    replay_export.mm \
    replay_input.c \
    replay_bindings.c \
    render_post.m \
    post_cuts.c \
    render_thread.m \
    soft_smoke_half.m \
    gpupass.m \
    dsound.c \
    audio_limiter.c \
    posaudio.c \
    dinput.c \
    ws2_32.c \
    interp.c \
    diag.c \
    save_timing.c \
    breaker_diag.c \
    diag_car.c \
    imports.c \
    movies.c \
    x360.m \
    x360_post.m \
    x360_textures.m \
    padprompts.c \
    maxperf_career.c \
    padbind.c \
    controlpages.c \
    controltype.c \
    pedals.c \
    steerresp.c \
    chasecam.c \
    vinyls.m \
    widescreen.c \
    padguest.c \
    qrsplit.c \
    splitworld.c \
    splitcapture.c \
    mirror.c \
    lampglow.c \
    aa_setting.c \
    live_picture.c \
    live_picture_sweep.c \
    alpha_to_coverage.c \
    decal_depth.m \
    lampbatch.m \
    detail.c \
    streaming.c \
    bootflow.c \
    pacing.c \
    rumble.m \
    padfx.m \
    padlink.c \
    feedback.c \
    overlay.m \
    probe.m \
    materials.m \
    particles_physical.c \
    realworld_materials.c \
    traffic_detail.c \
    traffic_pilot.c \
    roadfilm.m \
    lighting.c \
    settings.c \
    text_fit.c \
    roam_auth.c \
    roam_moderation.c \
    settings_port.c \
    portmenu.m \
    guide.m \
    report.m \
    onscreen_keyboard.c \
    gpuclients.m \
    slowlog.m \
    gameplay_opts.c \
    fe_console.c \
    attrib_x360.c \
    trees.c \
    auxlights.c \
    x360audio.c \
    reverbfade.c \
    $XT_SRC \
    x360abk.c \
    x360copspeech.c \
    x360nisaudio.c \
    x360_shadow_fit.cpp \
    roam_game.c \
    roam_hud.c \
    roam_core.c \
    roam_relay.c \
    roam_server.c \
    roam_online.c \
    roam_code.c \
    natmap.c \
    natpmp.c \
    pcp.c \
    upnp.c \
    ../recomp/out/speed.a \
    $SERVER_LIB \
    ../host/audio/build/libhostaudio.a \
    ../host/audio/build/libmedia.a \
    ../host/shaders/build/libsmtranslate.a \
    $FF/libavformat.a \
    $FF/libavcodec.a \
    $FF/libswscale.a \
    $FF/libswresample.a \
    $FF/libavutil.a \
    -lc++ \
    -lz \
    -framework AudioToolbox \
    -framework CoreAudio \
    -framework CoreFoundation \
    -framework AVFoundation \
    -framework VideoToolbox \
    -framework CoreVideo \
    -framework CoreMedia \
    -framework Cocoa \
    -framework QuartzCore \
    -framework Metal \
    -framework ImageIO \
    -framework CoreGraphics \
    -framework CoreText \
    -framework CoreServices \
    -framework GameController \
    -framework IOKit \
    -framework Security \
    -framework SystemConfiguration \
    -o "${BOOT_OUT:-boot}"
"$TOOLS_PY" -I ../recomp/hook_coverage.py --host mac --binary "${BOOT_OUT:-boot}" --report ../recomp/out/hook-coverage.json
"$TOOLS_PY" -I ../recomp/build_inputs.py --runtime-record "${BOOT_OUT:-boot}" "$build_key"
