#include <atomic>
#include "../mods/pop_mod_api.h"
// mods_seam.cpp - the weak defaults. Each one is what an unmodded build does.
#include "mods_seam.h"
#include "display_seam.h"
#include "native_seam.h"

extern "C" {

__attribute__((weak)) void mods_present_level_end(void) {}
// Test86: without the mods module there are no settings.
__attribute__((weak)) int64_t mods_setting_value(const char *, int64_t fallback) {
    return fallback;
}
// A host with no display reports none.
__attribute__((weak)) int host_display_screen_size(int *, int *) {
    return 0;
}
// And a build without DirectDraw offers no modes.
__attribute__((weak)) int ddraw_add_mode(int, int, int) {
    return 0;
}
__attribute__((weak)) int recomp_pointer_place(int32_t, int32_t, int32_t, int32_t) {
    return 0;
}
// Native cursor adapters can also be linked into hosts with no input devices.
__attribute__((weak)) void dinput_discard_mouse_motion(uint32_t) {}
__attribute__((weak)) void recomp_media_focus(int) {}
__attribute__((weak)) void recomp_media_shutdown(void) {}

__attribute__((weak)) bool mods_load_all(void) {
    return true;
}
__attribute__((weak)) void mods_shutdown(void) {}
__attribute__((weak)) void mods_shutdown_request(void) {}
// True: with no module there is nothing to tear down, so a host
// polling this finishes at once instead of spinning out its bound.
__attribute__((weak)) bool mods_shutdown_complete(void) {
    return true;
}
__attribute__((weak)) void mods_host_set_main_thread(void) {}
__attribute__((weak)) uint32_t mods_record_count(void) {
    return 0;
}
__attribute__((weak)) bool mods_write_run_record(const char *) {
    return false;
}
__attribute__((weak)) void mods_registry_pump(void) {}
__attribute__((weak)) const char *mods_active_callback_desc(void) {
    return "";
}
__attribute__((weak)) void mods_hooks_unwind_to_esp(uint32_t) {}
__attribute__((weak)) uint32_t mods_hook_depth(void) {
    return 0;
}

__attribute__((weak)) bool mods_input_key(uint8_t, uint8_t, bool) {
    return false;
}
__attribute__((weak)) bool mods_input_button(int, bool, int32_t, int32_t) {
    return false;
}
__attribute__((weak)) bool mods_input_motion(int32_t, int32_t, int32_t, int32_t) {
    return false;
}
__attribute__((weak)) bool mods_input_wheel(int32_t) {
    return false;
}
__attribute__((weak)) void mods_input_release_all(void) {}

__attribute__((weak)) void mods_page_init(void) {}
__attribute__((weak)) bool mods_page_visible(void) {
    return false;
}
__attribute__((weak)) void mods_page_draw(void *, int, int, int, int, const uint32_t *) {}
__attribute__((weak)) int mods_menu_render(int, int, uint64_t *, const uint8_t **) {
    return 0;
}
__attribute__((weak)) int mods_menu_pointer(float, float, int, int, int) {
    return 0;
}

__attribute__((weak)) int mods_texture_override(uint64_t, int32_t, int32_t, int32_t, uint8_t **,
                                                uint32_t *) {
    return 0;
}

// Preserve old strong seams in minimal/test hosts.
__attribute__((weak)) int mods_texture_override_ex(uint64_t hash, int32_t w, int32_t h,
                                                   int32_t format, PopTextureReplacement *out) {
    uint8_t *pixels = nullptr;
    uint32_t bytes = 0;
    if (!out || w <= 0 || h <= 0 || w > 4096 || h > 4096 ||
        !mods_texture_override(hash, w, h, format, &pixels, &bytes) || !pixels ||
        uint64_t(bytes) < uint64_t(w) * h * 4)
        return 0;
    *out = {sizeof(*out), w, h, w * 4, pixels, bytes};
    return 1;
}

} // extern "C"

extern "C" {
__attribute__((weak)) int32_t host_display_anchor(uint64_t, int8_t, int8_t, int) {
    return -5;
}
__attribute__((weak)) uint32_t host_display_elements(uint64_t *, uint32_t) {
    return 0;
}
__attribute__((weak)) float host_display_aspect() {
    return 4.0f / 3.0f;
}
// The screen is recorded by whichever host knows it; nothing needs a lock
// beyond the atomics, since it is written before the guest starts.
static std::atomic<int32_t> g_screen_w{0}, g_screen_h{0};
void host_display_set_screen(int32_t w, int32_t h) {
    g_screen_w.store(w);
    g_screen_h.store(h);
}
int host_display_screen(int32_t *w, int32_t *h) {
    if (g_screen_w.load() <= 0 || g_screen_h.load() <= 0) {
        // NFSU1 run 8: only a game's display module publishes the screen here; otherwise ask the host, which knows
        // the screen the window is on (host_display_screen_size; the weak default answers 0).
        int sw = 0, sh = 0;
        if (!host_display_screen_size(&sw, &sh) || sw <= 0 || sh <= 0)
            return 0;
        *w = sw;
        *h = sh;
        return 1;
    }
    *w = g_screen_w.load();
    *h = g_screen_h.load();
    return 1;
}
__attribute__((weak)) uint64_t host_display_epoch() {
    return 0;
}
__attribute__((weak)) int host_display_offer_mode(int, int, int) {
    return 0;
}
__attribute__((weak)) void host_display_request_window(int) {}
__attribute__((weak)) int host_display_take_window() {
    return -1;
}
__attribute__((weak)) void mods_display_transition(uint64_t, int) {}
__attribute__((weak)) int mods_display_classic() {
    return 0;
}
__attribute__((weak)) int mods_display_scale() {
    return 0;
}
__attribute__((weak)) int mods_display_wide() {
    return 1;
}
__attribute__((weak)) void mods_display_scene_domain(int, int) {}
__attribute__((weak)) void mods_display_texture_pack(uint32_t, int) {}
__attribute__((weak)) int mods_display_scene_width(int w, int) {
    return w;
}
__attribute__((weak)) int mods_display_fps() {
    return 0;
}
__attribute__((weak)) int mods_display_overlay() {
    return 0;
}
}

extern "C" __attribute__((weak)) uint64_t host_sprite_frame_id() {
    return 0;
}
extern "C" __attribute__((weak)) uint32_t host_sprite_texture_revision(uint32_t) {
    return 0;
}

struct HostD3DDrawSnapshot;
extern "C" __attribute__((weak)) void host_sprite_record_draw(const HostD3DDrawSnapshot *) {}

extern "C" __attribute__((weak)) void mods_options_request() {}

extern "C" __attribute__((weak)) int mods_display_textures() {
    return 0;
}
extern "C" __attribute__((weak)) int mods_display_filtering() {
    return 0;
}

extern "C" __attribute__((weak)) void host_display_present_window(const uint32_t *, int, int) {}
extern "C" __attribute__((weak)) bool ddraw_gdi_primary_active() {
    return false;
}
extern "C" __attribute__((weak)) uint32_t ddraw_gdi_begin_primary() {
    return 0;
}
extern "C" __attribute__((weak)) void ddraw_gdi_end_primary(uint32_t) {}
// Test90: the presenter reopens the audio output after a display HDR switch; without the audio module, nothing.
extern "C" __attribute__((weak)) void host_audio_restart_output(const char *) {}
