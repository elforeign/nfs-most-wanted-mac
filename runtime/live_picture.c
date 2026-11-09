// live_picture.c - picture settings that take effect mid-race through the game's own device Reset (F10 rows with
// .live_env, no restart). live_picture_request() does what the Video options' Accept does: LoadedFlagMaybe, so the
// next eDisplayFrame reloads the effects and runs the device Reset (~0.4 s, one frame). v_Reset (d3d9.m) then calls
// live_picture_on_reset() before it rebuilds the swap chain: the render state that was read once at start-up is read
// again there, so the game recreates its targets (capture cube) with the new state.
// The checker is live_picture_check.sh; NFS_TEST_LIVE_PICTURE_BROKEN=1 (diag runs only) makes the request a no-op.
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include "runtime.h"
#include "settings.h"
#include "live_picture.h"

extern int diag_on;
void diag_event(const char *fmt, ...);
void envcube_live_reconfigure(void);            // envcube.m: NFS_CAR_REFLECTION re-read, capture edge set or restored
// Weak no-ops: a shading switch read once and cached defines its reload function next to the cache (render.m, x360_post.m)
// and replaces these; while they read the environment per call there is nothing to invalidate.
__attribute__((weak)) void render_soft_smoke_reload(void) {}
__attribute__((weak)) void x360_post_ssao_reload(void) {}
int x360_post_ssao_enabled(void);               // x360_post.m
void user32_fullscreen_mode_changed(void);      // user32.m
uint32_t call_guest(Cpu *c, uint32_t fn, int n, const uint32_t *args);

#define LOADED_FLAG 0x00982C39u                  // LoadedFlagMaybe: the next frame runs the full device Reset
static unsigned requests, resets;
static int rebuild_pending;

void live_picture_request(const char *why) {
    if (diag_on && getenv("NFS_TEST_LIVE_PICTURE_BROKEN")) {
        diag_event("live picture: reset NOT requested (%s): broken control", why);
        return;
    }
    G[LOADED_FLAG] = 1;
    rebuild_pending = 1;
    ++requests;
    if (diag_on) diag_event("live picture: reset requested (%s) #%u", why, requests);
}
void live_picture_on_reset(Cpu *c) {            // game thread, inside IDirect3DDevice9::Reset, before the swap chain is rebuilt
    ++resets;
    if (rebuild_pending) {
        rebuild_pending = 0;
        // 006BD620 releases the capture targets but skips this managed backup. 006BD4B0 overwrites it on recreate.
        // Drop our live rebuild's old reference through IUnknown::Release; ordinary game resets keep their lifetime.
        uint32_t cube = M32(0x00982A64u);
        if (cube) {
            Cpu saved = *c;
            M32(0x00982A64u) = 0;
            call_guest(c, M32(M32(cube) + 8), 1, &cube);
            *c = saved;
        }
    }
    envcube_live_reconfigure();
    if (diag_on) diag_event("live picture: reset %u (requests %u)", resets, requests);
}

// Row handlers (settings_port.c, pub_settings.c). settings_set has already exported the new value to the switch.
void live_picture_apply_reset(int v) { (void)v; live_picture_request("setting"); }     // car reflections: rebuilt in on_reset
void live_picture_apply_reload(int v) {          // contact shadows, soft smoke
    (void)v;
    render_soft_smoke_reload(); x360_post_ssao_reload();
    if (diag_on) { const char *e = getenv("NFS_SOFT_SMOKE"); diag_event("live picture: contact shadows=%d soft smoke switch=%s", x360_post_ssao_enabled(), e ? e : "unset"); }
}
void live_picture_apply_fullscreen(int v) { (void)v; user32_fullscreen_mode_changed(); }   // window only, no device Reset
