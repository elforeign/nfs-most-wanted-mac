#include "../gpu/metal/label_class.h"
#include <map>
#include "game_config.h"
#include "../fe_layout.h"
#include "../texture_pack.h"
#include "../../runtime/display_seam.h"
#include "../../runtime/native_seam.h"
#include "../../dx/passes.h"
// host_tests.mm - headless tests for the macOS host.
//
// No window is created. Explicit --audio-device (silent) and --audio-channel-test
// (audible identification) probes open devices; ordinary audio tests stay offline. The Direct3D
// tests do run on the real GPU, into a GPU texture the test allocates and
// reads back: offscreen work needs no window, no drawable and no application
// object, and it is the only way to check that a command list turns into the
// pixels it should.
//
// What is covered:
//   * the presenter's pixel maths and its letterbox geometry
//   * the keyboard map and the input state machine, including the consuming
//     read DirectInput requires
//   * the command-list translation: primitive expansion, flat shading, the
//     pre-transformed path, alpha test, blending, depth and textures, each
//     checked against pixels read back from the target
//   * the audio mixer's conversions
#include "../present.h"
#include "../input.h"
#include "../input_gate.h"
#include "../window_presentation.h"
#include "../compositor.h"
#include "../ui_layer.h"
#include "test_frame_builder.h"
#include "../page_overlay.h"
#include "../performance_overlay.h"
#include "../controls/overlay.h"
#include "../controls/router.h"
#include "../../runtime/frame_deadline.h"
#include "../audio_capture.h"
#include "../midi.h"
#include "../script.h"
#include "../smoke_dumpat.h"
#include "../landmark.h"
#include <filesystem>
#include <fstream>
#include "../../platform/os.h"
#include "../audio.h"
#include "../audio/sink.h"
#include "../audio/diagnostics.h"
#include "../audio/dolby.h"
#ifdef RECOMP_HAVE_FFMPEG
extern "C" {
#include <libavcodec/avcodec.h>
#include <libavutil/channel_layout.h>
}
#endif
#include <SDL3/SDL.h>
#include "../game_path.h"
#include "../focus_note.h"
#include "../../runtime/layout.h"
#include "../d3d_render.h"
#include "../../dx/host_api.h"
#include "../../runtime/memory.h"
#include "../../runtime/imports.h"
#include "../../dx/dx.h"
#include "../../dx/d3d11.h"
#include "../../dx/com.h"
#include "../../runtime/loader.h"
#include "../../runtime/win32.h"
#include "../../runtime/mods_seam.h"

#include "../gpu/fake/fake_device.h"
#include "../gpu/gpu_factory.h"
#include "../../dx/host_d9.h"
#include "../../dx/d3d9_shader.h"
#include "../../dx/d3d9_msl.h"
#include "../../dx/d3d9_pipeline.h"
#include "../../dx/dds_volume.h"

#include <math.h>
#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif
#include <stdio.h>
#include <string.h>

#include <vector>
#include <atomic>
#include <thread>

#ifdef __APPLE__
size_t host_d9_metal_fitted_depth_entries(); // d3d9_metal.mm (Test157b)
#endif
static int g_checks = 0, g_failures = 0;

// The host's GPU device: the renderer and the presenter share its queue.
static std::unique_ptr<gpu::Device> g_gpu;
static D3DRenderer *make_renderer() {
    auto *renderer = new D3DRenderer(g_gpu.get());
    if (renderer->ok())
        return renderer;
    delete renderer;
    return nullptr;
}
static gpu::TextureDesc target_desc(D3DRenderer *renderer) {
    return renderer->device()->describe(renderer->colorTarget());
}
static void check(bool ok, const char *what, const char *file, int line) {
    ++g_checks;
    if (!ok) {
        ++g_failures;
        fprintf(stderr, "FAIL %s:%d: %s\n", file, line, what);
    }
}
#define CHECK(x) check((x), #x, __FILE__, __LINE__)
#define CHECK_EQ(a, b)                                                                             \
    do {                                                                                           \
        long long va = (long long)(a), vb = (long long)(b);                                        \
        ++g_checks;                                                                                \
        if (va != vb) {                                                                            \
            ++g_failures;                                                                          \
            fprintf(stderr, "FAIL %s:%d: %s == %s (%lld vs %lld)\n", __FILE__, __LINE__, #a, #b,   \
                    va, vb);                                                                       \
        }                                                                                          \
    } while (0)
#define CHECK_NEAR(a, b, eps)                                                                      \
    do {                                                                                           \
        double va = (double)(a), vb = (double)(b);                                                 \
        ++g_checks;                                                                                \
        if (fabs(va - vb) > (eps)) {                                                               \
            ++g_failures;                                                                          \
            fprintf(stderr, "FAIL %s:%d: %s ~= %s (%f vs %f)\n", __FILE__, __LINE__, #a, #b, va,   \
                    vb);                                                                           \
        }                                                                                          \
    } while (0)

// ===========================================================================
// present.mm
// ===========================================================================
static void test_present_suspend_flag() {
    CHECK(!host_present_suspended());
    host_present_suspend(true);
    CHECK(host_present_suspended());
    host_present_suspend(false);
    CHECK(!host_present_suspended());
}

static void test_palette_expansion() {
    // A 3x2 surface whose pitch is wider than its width, which is what a real
    // driver hands back: the padding must not be read as pixels.
    const int w = 3, h = 2, pitch = 8;
    uint8_t src[pitch * h];
    memset(src, 0xee, sizeof src); // the padding, and a trap
    src[0] = 0;
    src[1] = 1;
    src[2] = 255;
    src[pitch + 0] = 255;
    src[pitch + 1] = 0;
    src[pitch + 2] = 1;

    uint32_t palette[256];
    for (int i = 0; i < 256; ++i)
        palette[i] = 0x00000000u;
    palette[0] = 0x00ff0000u; // red
    palette[1] = 0x0000ff00u; // green
    palette[255] = 0x00123456u;

    uint8_t out[w * h * 4];
    memset(out, 0x7f, sizeof out);
    host_present_expand_indexed(src, w, h, pitch, palette, out);

    CHECK_EQ(out[0], 0xff);
    CHECK_EQ(out[1], 0x00);
    CHECK_EQ(out[2], 0x00);
    // The palette's top byte is padding, not alpha: a presented frame is opaque.
    CHECK_EQ(out[3], 0xff);
    CHECK_EQ(out[4], 0x00);
    CHECK_EQ(out[5], 0xff);
    CHECK_EQ(out[6], 0x00);
    CHECK_EQ(out[8], 0x12);
    CHECK_EQ(out[9], 0x34);
    CHECK_EQ(out[10], 0x56);
    // Row 1 starts at the pitch, not at the width.
    CHECK_EQ(out[12], 0x12);
    CHECK_EQ(out[13], 0x34);
    CHECK_EQ(out[14], 0x56);
    CHECK_EQ(out[16], 0xff);
    CHECK_EQ(out[17], 0x00);
    CHECK_EQ(out[20], 0x00);
    CHECK_EQ(out[21], 0xff);

    // With no palette the host sees indices, and grey is the only honest thing
    // to show: the shim logs that case rather than inventing colours.
    uint8_t grey[w * h * 4];
    host_present_expand_indexed(src, w, h, pitch, nullptr, grey);
    CHECK_EQ(grey[0], 0);
    CHECK_EQ(grey[4], 1);
    CHECK_EQ(grey[8], 255);
}

static void test_rgb565_expansion() {
    const int w = 4, h = 1, pitch = 16;
    uint8_t src[pitch];
    memset(src, 0, sizeof src);
    uint16_t pixels[4] = {0xffff, 0x0000, 0xf800, 0x001f}; // white, black, red, blue
    for (int i = 0; i < 4; ++i) {
        src[i * 2] = (uint8_t)(pixels[i] & 0xff);
        src[i * 2 + 1] = (uint8_t)(pixels[i] >> 8);
    }
    uint8_t out[w * 4];
    host_present_expand_rgb565(src, w, h, pitch, out);
    // Scaled, not shifted: 31 becomes 255, so white is white and not 0xf8f8f8.
    CHECK_EQ(out[0], 255);
    CHECK_EQ(out[1], 255);
    CHECK_EQ(out[2], 255);
    CHECK_EQ(out[3], 255);
    CHECK_EQ(out[4], 0);
    CHECK_EQ(out[5], 0);
    CHECK_EQ(out[6], 0);
    CHECK_EQ(out[8], 255);
    CHECK_EQ(out[9], 0);
    CHECK_EQ(out[10], 0);
    CHECK_EQ(out[12], 0);
    CHECK_EQ(out[13], 0);
    CHECK_EQ(out[14], 255);
    {
        // X8R8G8B8: bytes B, G, R, X. The X byte is ignored and alpha is 255.
        const uint8_t px[8] = {0x10, 0x20, 0x30, 0x00, 0xff, 0x00, 0x80, 0x7f};
        uint8_t rgba[8];
        host_present_expand_xrgb8888(px, 2, 1, 8, rgba);
        CHECK_EQ(rgba[0], 0x30);
        CHECK_EQ(rgba[1], 0x20);
        CHECK_EQ(rgba[2], 0x10);
        CHECK_EQ(rgba[3], 255);
        CHECK_EQ(rgba[4], 0x80);
        CHECK_EQ(rgba[5], 0x00);
        CHECK_EQ(rgba[6], 0xff);
        CHECK_EQ(rgba[7], 255);
    }
}

static void test_window_size() {
    // A mode that fits keeps the whole multiple of points it always had.
    HostWindowSize s = host_window_size_for(800, 600, 1512, 945, 2.0);
    CHECK_EQ(s.w, 800);
    CHECK_EQ(s.h, 600);
    CHECK_EQ(s.min_w, 800);
    s = host_window_size_for(640, 480, 2560, 1415, 1.0);
    CHECK_EQ(s.w, 1280);
    CHECK_EQ(s.h, 960);
    CHECK_EQ(s.min_w, 640);
    CHECK_EQ(s.min_h, 480);
    // 1920x1080 on a 1512x945-point Retina screen: one drawable pixel per guest
    // pixel is 960x540 points, which fits; 1920x1080 points does not.
    s = host_window_size_for(1920, 1080, 1512, 945, 2.0);
    CHECK_EQ(s.w, 960);
    CHECK_EQ(s.h, 540);
    CHECK_EQ(s.min_w, 960);
    CHECK_EQ(s.min_h, 540);
    // No whole multiple fits a 1280x775-point, one-pixel-per-point screen: the
    // largest aspect-preserving size inside 95% of it.
    s = host_window_size_for(1920, 1080, 1280, 775, 1.0);
    CHECK_EQ(s.w, 1216);
    CHECK_EQ(s.h, 684);
    CHECK_EQ(s.min_w, 1216);
    CHECK_EQ(s.min_h, 684);
    // An unknown screen: one point per guest pixel.
    s = host_window_size_for(1920, 1080, 0, 0, 2.0);
    CHECK_EQ(s.w, 1920);
    CHECK_EQ(s.min_h, 1080);
}

static void test_letterbox() {
    // A 1280x960 drawable is exactly two 640x480 frames across.
    HostFit fit = host_present_fit(1280, 960, 640, 480);
    CHECK_NEAR(fit.scale, 2.0, 1e-9);
    CHECK_NEAR(fit.w, 1280, 1e-9);
    CHECK_NEAR(fit.x, 0, 1e-9);

    // A 1400x1000 drawable still takes 2, not 2.1875: whole pixels only.
    fit = host_present_fit(1400, 1000, 640, 480);
    CHECK_NEAR(fit.scale, 2.0, 1e-9);
    CHECK_NEAR(fit.w, 1280, 1e-9);
    CHECK_NEAR(fit.h, 960, 1e-9);
    CHECK_NEAR(fit.x, 60, 1e-9); // centred
    CHECK_NEAR(fit.y, 20, 1e-9);

    // Wider than tall: the aspect ratio decides, and the frame is pillarboxed.
    fit = host_present_fit(2000, 480, 640, 480);
    CHECK_NEAR(fit.scale, 1.0, 1e-9);
    CHECK_NEAR(fit.w, 640, 1e-9);
    CHECK_NEAR(fit.x, 680, 1e-9);

    // Smaller than the guest mode: there is no whole multiple below 1, so the
    // frame is scaled to fit rather than cropped.
    fit = host_present_fit(320, 240, 640, 480);
    CHECK_NEAR(fit.scale, 0.5, 1e-9);
    CHECK_NEAR(fit.w, 320, 1e-9);
    CHECK_NEAR(fit.h, 240, 1e-9);

    fit = host_present_fit(0, 0, 640, 480);
    CHECK_NEAR(fit.scale, 1.0, 1e-9);
}

// ===========================================================================
// input.mm
// ===========================================================================
static void test_scancodes() {
    // Positional, so the letter is the key's place on the board.
    CHECK_EQ(host_dik_from_mac(0x0d), 0x11); // W
    CHECK_EQ(host_dik_from_mac(0x00), 0x1e); // A
    CHECK_EQ(host_dik_from_mac(0x35), 0x01); // Escape
    CHECK_EQ(host_vk_from_mac(0x35), 0x1b);
    CHECK_EQ(host_dik_from_mac(0x31), 0x39); // Space
    CHECK_EQ(host_dik_from_mac(0x24), 0x1c); // Return

    // The arrows are the extended block and are numbered above 0x80, which is
    // what tells them apart from the keypad digits that share their labels.
    CHECK_EQ(host_dik_from_mac(0x7e), 0xc8); // Up
    CHECK_EQ(host_dik_from_mac(0x7d), 0xd0); // Down
    CHECK_EQ(host_dik_from_mac(0x7b), 0xcb); // Left
    CHECK_EQ(host_dik_from_mac(0x7c), 0xcd); // Right
    CHECK_EQ(host_dik_from_mac(0x5b), 0x48); // keypad 8, not Up
    CHECK_EQ(host_vk_from_mac(0x7e), 0x26);

    // Function keys, and F11/F12 which are not contiguous with F1..F10.
    CHECK_EQ(host_dik_from_mac(0x7a), 0x3b); // F1
    CHECK_EQ(host_dik_from_mac(0x6d), 0x44); // F10
    CHECK_EQ(host_dik_from_mac(0x67), 0x57); // F11
    CHECK_EQ(host_dik_from_mac(0x6f), 0x58); // F12

    // Pause. A Mac keyboard has no Pause key; F15 sits where Pause does on a
    // full-size PC layout, so it carries DIK_PAUSE and VK_PAUSE.
    CHECK_EQ(host_dik_from_mac(0x71), 0xc5); // F15 -> DIK_PAUSE
    CHECK_EQ(host_vk_from_mac(0x71), 0x13);  // VK_PAUSE
    CHECK_EQ(host_dik_from_mac(0x47), 0x45); // Clear -> Num Lock
    CHECK_EQ(host_dik_from_mac(0x6b), 0x46); // F14 -> Scroll Lock

    // A key with no PC equivalent maps to nothing rather than to key 0, which
    // is DIK_ESCAPE's neighbour and would be a real key going down.
    CHECK_EQ(host_dik_from_mac(0x37), 0); // Command
    CHECK_EQ(host_vk_from_mac(0x37), 0);

    // No two macOS keys may claim the same scan code, or one would mask the
    // other and the game would see the wrong key.
    const HostKeyMapping *table = host_key_mapping_table();
    int n = host_key_mapping_count();
    int collisions = 0;
    for (int i = 0; i < n; ++i)
        for (int j = i + 1; j < n; ++j) {
            if (table[i].mac == table[j].mac)
                ++collisions;
            if (table[i].dik && table[i].dik == table[j].dik)
                ++collisions;
        }
    CHECK_EQ(collisions, 0);
}

static void test_input_state() {
    host_input_reset();
    HostInputState state;

    host_input_key(0x35, true); // Escape down
    host_input_state(&state);
    CHECK_EQ(state.keys[0x01], 0x80);
    CHECK_EQ(state.keys[0x11], 0x00);
    host_input_key(0x35, false);
    host_input_state(&state);
    CHECK_EQ(state.keys[0x01], 0x00);

    // The modifier flags name the side, which a plain keyDown never would.
    host_input_modifiers(0x00000002u | 0x00020000u); // left shift, and shift
    host_input_state(&state);
    CHECK_EQ(state.keys[0x2a], 0x80);                // DIK_LSHIFT
    CHECK_EQ(state.keys[0x36], 0x00);                // DIK_RSHIFT
    host_input_modifiers(0x00000004u | 0x00020000u); // right shift
    host_input_state(&state);
    CHECK_EQ(state.keys[0x2a], 0x00);
    CHECK_EQ(state.keys[0x36], 0x80);
    // Either side holding the modifier keeps the unsided VK down, which is
    // what a game reading GetAsyncKeyState(VK_SHIFT) has to see. Both sides
    // down, then one released: still down.
    host_input_modifiers(0x00000002u | 0x00000004u | 0x00020000u);
    host_input_state(&state);
    CHECK_EQ(state.keys[0x2a], 0x80);
    CHECK_EQ(state.keys[0x36], 0x80);
    host_input_modifiers(0x00000002u | 0x00020000u); // right released
    host_input_state(&state);
    CHECK_EQ(state.keys[0x2a], 0x80);
    CHECK_EQ(state.keys[0x36], 0x00);
    host_input_modifiers(0);

    // Buttons.
    host_input_button(1, true);
    host_input_state(&state);
    CHECK_EQ(state.mouse_buttons[1], 0x80);
    CHECK_EQ(state.mouse_buttons[0], 0x00);
    host_input_button(1, false);

    // Deltas accumulate between reads and are consumed by the read, because a
    // DirectInput device reports motion since the last poll exactly once.
    host_input_motion(100, 50, 3, -4);
    host_input_motion(103, 46, 2, 1);
    host_input_state(&state);
    CHECK_EQ(state.mouse_x, 103);
    CHECK_EQ(state.mouse_y, 46);
    CHECK_EQ(state.mouse_dx, 5);
    CHECK_EQ(state.mouse_dy, -3);
    host_input_state(&state);
    CHECK_EQ(state.mouse_dx, 0);
    CHECK_EQ(state.mouse_dy, 0);
    CHECK_EQ(state.mouse_x, 103); // position is not consumed

    // Losing the focus releases everything: a key that comes up while another
    // application has the focus is never seen here.
    host_input_key(0x0d, true);
    host_input_button(0, true);
    host_input_release_all();
    host_input_state(&state);
    CHECK_EQ(state.keys[0x11], 0x00);
    CHECK_EQ(state.mouse_buttons[0], 0x00);
    host_input_reset();
}

// The game's DirectInput devices are serviced by two guest threads that wait on
// an event, so input nobody announces is input the game never sees. Every
// mutator has to announce, and the count has to move for every kind of event.
namespace {
int g_notified = 0;
void count_notify() {
    ++g_notified;
}
} // namespace

static void test_input_announces_changes() {
    host_input_reset();
    host_input_set_notify(count_notify);
    uint32_t before = host_input_notify_count();

    g_notified = 0;
    host_input_motion(10, 10, 1, 0);
    CHECK_EQ(g_notified, 1); // a mouse move
    host_input_button(0, true);
    CHECK_EQ(g_notified, 2); // a click
    host_input_button(0, false);
    CHECK_EQ(g_notified, 3);
    host_input_key(0x0d, true);
    CHECK_EQ(g_notified, 4); // a key down
    host_input_key(0x0d, false);
    CHECK_EQ(g_notified, 5);
    host_input_wheel(120);
    CHECK_EQ(g_notified, 6); // the wheel
    // A flags change moves several keys at once and is one event, so it
    // announces once rather than once per key.
    host_input_modifiers(0x00000002u | 0x00020000u);
    CHECK_EQ(g_notified, 7);
    host_input_modifiers(0);
    CHECK_EQ(g_notified, 8);
    // Losing the focus releases everything, which is a change like any other.
    host_input_release_all();
    CHECK_EQ(g_notified, 9);

    // The running total moved with it, which is what the run report prints.
    CHECK_EQ(host_input_notify_count(), before + 9);

    // The state really did change alongside the announcements.
    HostInputState state;
    host_input_state(&state);
    CHECK_EQ(state.mouse_x, 10);
    CHECK_EQ(state.keys[0x11], 0x00);
    CHECK_EQ(state.mouse_buttons[0], 0x00);

    // With no notifier installed the count still moves, and the host says so
    // once rather than letting a forgotten installation look like a dead
    // mouse. That silent case is the one this whole mechanism exists to avoid.
    host_input_set_notify(nullptr);
    uint32_t quiet = host_input_notify_count();
    host_input_motion(11, 11, 1, 1);
    CHECK_EQ(host_input_notify_count(), quiet + 1);

    host_input_reset();
}

// A mouse move has to reach the guest three ways at once: as a DirectInput
// delta, as an absolute cursor position in the guest's own pixels, and as an
// announcement that wakes the threads waiting to read it. Missing any one of
// them is a pointer that does not move while the buttons still work.
static void test_mouse_move_reaches_the_guest() {
    host_input_reset();
    host_input_set_notify(count_notify);
    g_notified = 0;

    // What main.mm does for one mouseMoved: scale the platform delta, keeping
    // the remainder, then feed the mapped position and that delta.
    HostMouseAccumulator remainder = {0.0, 0.0};
    int32_t dx = 0, dy = 0;
    host_input_scale_delta(&remainder, 4.0, -2.0, 1.0, &dx, &dy);
    CHECK_EQ(dx, 4);
    CHECK_EQ(dy, -2);
    host_input_motion(100, 50, dx, dy);
    CHECK_EQ(g_notified, 1);

    HostInputState state;
    host_input_state(&state);
    CHECK_EQ(state.mouse_dx, 4); // the DirectInput delta
    CHECK_EQ(state.mouse_dy, -2);
    CHECK_EQ(state.mouse_x, 100); // the absolute position
    CHECK_EQ(state.mouse_y, 50);
    // Consumed by that read, because a delta is motion since the last one.
    host_input_state(&state);
    CHECK_EQ(state.mouse_dx, 0);
    CHECK_EQ(state.mouse_dy, 0);
    CHECK_EQ(state.mouse_x, 100); // but the position is not

    // Slow movement at a scale below one. Rounding each event on its own gives
    // nothing at all; keeping the remainder gives the motion back whole.
    remainder = HostMouseAccumulator{0.0, 0.0};
    int32_t total_x = 0, total_y = 0;
    for (int i = 0; i < 10; ++i) {
        host_input_scale_delta(&remainder, 1.0, 1.0, 0.5, &dx, &dy);
        total_x += dx;
        total_y += dy;
    }
    CHECK_EQ(total_x, 5); // ten half-pixel steps are five
    CHECK_EQ(total_y, 5);

    // A reversal does not leave a fraction pulling the wrong way.
    remainder = HostMouseAccumulator{0.0, 0.0};
    host_input_scale_delta(&remainder, 1.0, 0.0, 0.5, &dx, &dy);
    CHECK_EQ(dx, 0); // half a pixel, still owed
    host_input_scale_delta(&remainder, -1.0, 0.0, 0.5, &dx, &dy);
    CHECK_EQ(dx, 0); // back where it started
    host_input_scale_delta(&remainder, -2.0, 0.0, 0.5, &dx, &dy);
    CHECK_EQ(dx, -1);

    // A whole-number scale loses nothing and needs no remainder.
    remainder = HostMouseAccumulator{0.0, 0.0};
    host_input_scale_delta(&remainder, 3.0, -4.0, 2.0, &dx, &dy);
    CHECK_EQ(dx, 6);
    CHECK_EQ(dy, -8);

    // Every accumulated move announces, so the guest's threads are woken for
    // each of them rather than for the first.
    g_notified = 0;
    for (int i = 0; i < 5; ++i)
        host_input_motion(100 + i, 50, 1, 0);
    CHECK_EQ(g_notified, 5);
    host_input_state(&state);
    CHECK_EQ(state.mouse_dx, 5); // accumulated across the five
    CHECK_EQ(state.mouse_x, 104);

    host_input_set_notify(nullptr);
    host_input_reset();
}

// A smoke script that is wrong should say so before a game is booted, not
// after one has run for a minute and pressed the wrong things.
// Run 33 (D-183): keyuntil and the fe: claim. A synthetic front end in a fake guest memory: the right screen found
// behind an overlay that has no input, a name matched case-insensitively and in part, the wrong screen refused, no
// screen with input = 0, a claim that is not fe: = -1 (the other metrics answer it).
static std::map<uint32_t, uint8_t> g_fake_fe;
static uint32_t fake_fe_u32(uint32_t a) {
    uint32_t v = 0;
    for (int i = 0; i < 4; ++i) {
        auto it = g_fake_fe.find(a + i);
        v |= uint32_t(it == g_fake_fe.end() ? 0 : it->second) << (8 * i);
    }
    return v;
}
static void fake_fe_put(uint32_t a, uint32_t v) {
    for (int i = 0; i < 4; ++i)
        g_fake_fe[a + i] = uint8_t(v >> (8 * i));
}
static void test_script_keyuntil_and_fe_screen() {
    HostScriptStep steps[8];
    char err[256];
    CHECK_EQ(host_script_parse("keyuntil RETURN fe:MC_Main.fng>0 every 3000 within 90000\n", steps, 8, err, sizeof err),
             1);
    CHECK_EQ(steps[0].op, (int)HOST_SCRIPT_KEYUNTIL);
    CHECK_EQ(steps[0].dik, (uint8_t)0x1c);
    CHECK(!strcmp(steps[0].name, "fe:MC_Main.fng"));
    CHECK_EQ(steps[0].press_ms, 3000u);
    CHECK_EQ(steps[0].timeout_ms, 90000u);
    CHECK_EQ(host_script_parse("keyuntil RIGHT fe:X>=1\n", steps, 8, err, sizeof err), 1);
    CHECK(steps[0].at_least && steps[0].press_ms == 2500u && steps[0].timeout_ms == 60000u);
    struct Bad { const char *text, *fragment; };
    const Bad bad[] = {
        {"keyuntil\n", "needs a key"},
        {"keyuntil RETURN\n", "needs a key"},
        {"keyuntil NOKEY fe:X>0\n", "no such key"},
        {"keyuntil RETURN fe:X\n", "metric>value"},
        {"keyuntil RETURN fe:X>0 every 100\n", "every 400 ms"},
        {"keyuntil RETURN fe:X>0 soon 100\n", "`every` or `within`"},
        {"keyuntil RETURN fe:X>0 every 5000 within 4000\n", "before its second press"},
    };
    for (const auto &b : bad) {
        err[0] = 0;
        CHECK_EQ(host_script_parse(b.text, steps, 8, err, sizeof err), -1);
        CHECK(strstr(err, b.fragment) != nullptr);
    }
    // A key goes up only after the game read its input twice since the press and 160 ms passed; 3 s at most.
    CHECK(!host_script_key_release_ready(1000, 50, 1100, 60));   // too soon
    CHECK(!host_script_key_release_ready(1000, 50, 2200, 51));   // a frozen frame: one read since the press
    CHECK(host_script_key_release_ready(1000, 50, 1160, 52));
    CHECK(host_script_key_release_ready(1000, 50, 4000, 50));    // the game never read: released at 3 s
    CHECK(host_script_key_release_ready(0xffffff00u, 0xfffffffeu, 0x000000a0u, 0));   // both counters wrap
#ifdef RECOMP_GLOBAL_FE_ROOT_ADDR
    // Through the game's own layout (fe_layout.h): list entry -> overlay (no input) -> menu (input) -> popup (input).
    // Walked back (NFSMW) the first with input answers; walked forward (NFSU1) the last with input does.
    g_fake_fe.clear();
    const uint32_t root = 0x01000000u, engine = 0x01000100u, overlay = 0x01000300u, menu = 0x01000200u,
                   popup = 0x01000600u;
    fake_fe_put(RECOMP_GLOBAL_FE_ROOT_ADDR, root);
    fake_fe_put(root + RECOMP_GLOBAL_FE_ROOT_ENGINE, engine);
    fake_fe_put(engine + RECOMP_GLOBAL_FE_ROOT_LIST, overlay);
    fake_fe_put(overlay + RECOMP_GLOBAL_FE_ROOT_CONTROL, 0);
    fake_fe_put(overlay + RECOMP_GLOBAL_FE_ROOT_LINK, menu);
    fake_fe_put(overlay + RECOMP_GLOBAL_FE_ROOT_NAME, 0x01000400u);
    fake_fe_put(menu + RECOMP_GLOBAL_FE_ROOT_CONTROL, 0xffu);
    fake_fe_put(menu + RECOMP_GLOBAL_FE_ROOT_LINK, 0);
    fake_fe_put(menu + RECOMP_GLOBAL_FE_ROOT_NAME, 0x01000500u);
    fake_fe_put(popup + RECOMP_GLOBAL_FE_ROOT_CONTROL, 0xffu);
    fake_fe_put(popup + RECOMP_GLOBAL_FE_ROOT_LINK, 0);
    fake_fe_put(popup + RECOMP_GLOBAL_FE_ROOT_NAME, 0x01000700u);
    const char *over = "PauseOverlay.fng", *name = "MainMenu.fng", *pop = "Popup.fng";
    for (size_t i = 0; i <= strlen(over); ++i) g_fake_fe[0x01000400u + i] = uint8_t(over[i]);
    for (size_t i = 0; i <= strlen(name); ++i) g_fake_fe[0x01000500u + i] = uint8_t(name[i]);
    for (size_t i = 0; i <= strlen(pop); ++i) g_fake_fe[0x01000700u + i] = uint8_t(pop[i]);
    char screen[64];
    CHECK_EQ(host_script_fe_screen(fake_fe_u32, screen, sizeof screen), 1);
    CHECK(!strcmp(screen, "MainMenu.fng"));
    CHECK_EQ(host_script_fe_metric("fe:mainmenu.fng", fake_fe_u32), 1.0);
    CHECK_EQ(host_script_fe_metric("fe:MainMenu", fake_fe_u32), 1.0);
    CHECK_EQ(host_script_fe_metric("fe:Track_Select.fng", fake_fe_u32), 0.0);   // control: the wrong screen
    CHECK_EQ(host_script_fe_metric("fe:PauseOverlay", fake_fe_u32), 0.0);       // control: no input there
    CHECK_EQ(host_script_fe_metric("turn", fake_fe_u32), -1.0);
    fake_fe_put(menu + RECOMP_GLOBAL_FE_ROOT_LINK, popup);   // a second screen with input after it
    CHECK_EQ(host_script_fe_screen(fake_fe_u32, screen, sizeof screen), 1);
    CHECK(!strcmp(screen, RECOMP_GLOBAL_FE_ROOT_FORWARD ? "Popup.fng" : "MainMenu.fng"));
#ifdef RECOMP_GLOBAL_FE_ROOT_SKIP
    {
        // An overlay named in fe_root `skip` keeps input on every screen; the menu still answers (UG2 help bar).
        static const char *const skip[] = RECOMP_GLOBAL_FE_ROOT_SKIP;
        for (size_t i = 0; i <= strlen(skip[0]); ++i) g_fake_fe[0x01000700u + i] = uint8_t(skip[0][i]);
        CHECK_EQ(host_script_fe_screen(fake_fe_u32, screen, sizeof screen), 1);
        CHECK(!strcmp(screen, "MainMenu.fng"));
        for (size_t i = 0; i <= strlen(pop); ++i) g_fake_fe[0x01000700u + i] = uint8_t(pop[i]);
        CHECK(!strcmp(screen, "MainMenu.fng"));
    }
#endif
    fake_fe_put(menu + RECOMP_GLOBAL_FE_ROOT_LINK, 0);
    fake_fe_put(menu + RECOMP_GLOBAL_FE_ROOT_CONTROL, 0);    // no screen has the input
    CHECK_EQ(host_script_fe_screen(fake_fe_u32, screen, sizeof screen), 0);
    CHECK(screen[0] == 0);
    CHECK_EQ(host_script_fe_metric("fe:MainMenu", fake_fe_u32), 0.0);
    fake_fe_put(RECOMP_GLOBAL_FE_ROOT_ADDR, 0);   // no front end yet
    CHECK_EQ(host_script_fe_screen(fake_fe_u32, screen, sizeof screen), 0);
    g_fake_fe.clear();
#endif
}

static void test_script_parsing() {
    HostScriptStep steps[64];
    char err[256];

    CHECK_EQ(host_script_parse("tap 24 8\nwait 2000\ntap 510 90\n", steps, 64, err, sizeof err), 2);
    CHECK_EQ(steps[0].op, HOST_SCRIPT_TAP);
    CHECK_EQ(steps[0].x, 24);
    CHECK_EQ(steps[0].y, 8);
    CHECK_EQ(steps[1].at_ms, 2000);
    CHECK_EQ(steps[1].x, 510);
    CHECK_EQ(host_script_parse("tap_drawable 1210 834\n", steps, 64, err, sizeof err), 1);
    CHECK_EQ(steps[0].op, HOST_SCRIPT_TAP_DRAWABLE);
    CHECK_EQ(steps[0].x, 1210);
    CHECK_EQ(steps[0].y, 834);
    for (const char *bad : {"tap 1\n", "tap x 2\n", "tap -1 2\n", "tap 1 2 extra\n",
                            "tap_drawable -1 2\n", "tap_drawable 1210\n"})
        CHECK_EQ(host_script_parse(bad, steps, 64, err, sizeof err), -1);

    CHECK_EQ(host_script_parse("pad cross down\nwait 200\npad cross up\npad left_x -32767\n"
                               "pad right_trigger 32767\npad right down\n",
                               steps, 64, err, sizeof err),
             5);
    CHECK_EQ(steps[0].op, HOST_SCRIPT_PAD);
    CHECK_EQ(steps[0].button, 0);
    CHECK_EQ(steps[0].x, 1);
    CHECK_EQ(steps[1].x, 0);
    CHECK_EQ(steps[1].at_ms, 200);
    CHECK_EQ(steps[2].button, 17);
    CHECK_EQ(steps[2].x, -32767);
    CHECK_EQ(steps[3].button, 22);
    CHECK_EQ(steps[4].button, 14);
    for (const char *bad :
         {"pad cross\n", "pad cross 1\n", "pad missing down\n", "pad left_x -32768\n",
          "pad right_trigger -1\n", "pad left_y 32768\n", "pad cross up extra\n"})
        CHECK_EQ(host_script_parse(bad, steps, 64, err, sizeof err), -1);

    const char *good = "# a comment, and a blank line follow\n"
                       "\n"
                       "wait 500\n"
                       "move 320 140\n"
                       "click left 320 140\n"
                       "wait 250\n"
                       "key ESCAPE down\n"
                       "key escape up\n"
                       "dump menu\n"
                       "expect textures>0\n"
                       "expect scene_nonblack>0.3\n"
                       "quit\n";
    int n = host_script_parse(good, steps, 64, err, sizeof err);
    CHECK_EQ(n, 8); // wait is not a step, it moves the clock

    // The clock is the sum of the waits before each step, so a script reads as
    // a sequence rather than as timestamps to keep consistent by hand.
    CHECK_EQ(steps[0].op, HOST_SCRIPT_MOVE);
    CHECK_EQ(steps[0].at_ms, 500);
    CHECK_EQ(steps[0].x, 320);
    CHECK_EQ(steps[0].y, 140);
    CHECK_EQ(steps[1].op, HOST_SCRIPT_CLICK);
    CHECK_EQ(steps[1].button, 0);
    CHECK_EQ(steps[1].at_ms, 500);
    CHECK_EQ(steps[2].op, HOST_SCRIPT_KEY);
    CHECK_EQ(steps[2].at_ms, 750);
    CHECK_EQ(steps[2].dik, 0x01); // DIK_ESCAPE
    CHECK_EQ(steps[2].down, 1);
    CHECK_EQ(steps[3].down, 0); // the name is not case sensitive
    CHECK_EQ(steps[3].dik, 0x01);
    CHECK_EQ(steps[4].op, HOST_SCRIPT_DUMP);
    CHECK(strcmp(steps[4].name, "menu") == 0);
    CHECK_EQ(steps[5].op, HOST_SCRIPT_EXPECT);
    CHECK(strcmp(steps[5].name, "textures") == 0);
    CHECK_NEAR(steps[5].threshold, 0.0, 1e-9);
    CHECK_NEAR(steps[6].threshold, 0.3, 1e-9);
    CHECK_EQ(steps[7].op, HOST_SCRIPT_QUIT);

    // Relative motion, which is all a DirectInput mouse reports. A script uses
    // it to pin the game's own pointer to a corner, because the game
    // integrates the deltas itself and started its pointer wherever it chose.
    const char *relative = "moveby -2000 -2000\nmoveby 320 140\n";
    CHECK_EQ(host_script_parse(relative, steps, 64, err, sizeof err), 2);

    CHECK_EQ(steps[0].op, HOST_SCRIPT_MOVEBY);
    CHECK_EQ(steps[0].x, -2000);
    CHECK_EQ(steps[0].y, -2000);
    CHECK_EQ(steps[1].x, 320);
    CHECK_EQ(steps[1].y, 140);

    // A button held across moves: how a drag is scripted. `button` presses or
    // releases without moving the pointer, so the moves between them are the
    // drag; a game that pans on a right-button drag can be driven this way.
    const char *drag = "button right down\n"
                       "move 400 260\n"
                       "wait 100\n"
                       "button right up\n";
    CHECK_EQ(host_script_parse(drag, steps, 64, err, sizeof err), 3);
    CHECK_EQ(steps[0].op, HOST_SCRIPT_BUTTON);
    CHECK_EQ(steps[0].button, 1);
    CHECK_EQ(steps[0].down, 1);
    CHECK_EQ(steps[1].op, HOST_SCRIPT_MOVE);
    CHECK_EQ(steps[2].op, HOST_SCRIPT_BUTTON);
    CHECK_EQ(steps[2].down, 0);
    CHECK_EQ(steps[2].at_ms, 100);
    CHECK_EQ(host_script_parse("button sideways down\n", steps, 64, err, sizeof err), -1);

    // A key a script names has to be one the host can actually press, by the
    // same scan code the real keyboard produces.
    CHECK_EQ(host_script_dik("ESCAPE"), 0x01);
    CHECK_EQ(host_script_dik("RETURN"), 0x1c);
    CHECK_EQ(host_script_dik("DOWN"), 0xd0);
    CHECK_EQ(host_script_dik("nonsense"), 0);
    // And the host has to have a real key with that scan code, or the press
    // would go nowhere.
    CHECK_EQ(host_key_mapping_for_dik(0x01).vk, 0x1b); // VK_ESCAPE
    CHECK_EQ(host_key_mapping_for_dik(0xd0).vk, 0x28); // VK_DOWN
    CHECK_EQ(host_key_mapping_for_dik(0).dik, 0);

    // Every way of being wrong is refused, and says which line.
    struct {
        const char *text;
        const char *fragment;
    } bad[] = {
        {"jump 3\n", "unknown command"},
        {"wait\n", "duration"},
        {"move 1\n", "x and y"},
        {"moveby 5\n", "dx and dy"},
        {"click up 1 2\n", "left, right or middle"},
        {"key NOPE down\n", "no such key"},
        {"key ESCAPE sideways\n", "down or up"},
        {"expect textures\n", "metric>value"},
        {"dump\n", "needs a name"},
        {"move 1 2 3\n", "too many words"},
        {"await picture\n", "metric>value"},
        {"await\n", "metric>value"},
        {"await picture>0.5 for\n", "needs a number after"},
        {"await picture>0.5 soon 10\n", "`for` or `within`"},
        {"await picture>0.5 within 0\n", "timeout above zero"},
        {"await picture>0.5 for 3000 within 1000\n", "before it could hold"},
    };
    for (const auto &b : bad) {
        err[0] = 0;
        CHECK_EQ(host_script_parse(b.text, steps, 64, err, sizeof err), -1);
        CHECK(strstr(err, b.fragment) != nullptr);
        CHECK(strstr(err, "line 1") != nullptr);
    }

    // Reading guest memory and following an entity, which is how a run says
    // what the game thought happened rather than what it drew.
    const char *memory = "peek 0x8e0428 179\n"
                         "peek 4096 16\n"
                         "watch 0 1\n";
    CHECK_EQ(host_script_parse(memory, steps, 64, err, sizeof err), 3);
    CHECK_EQ(steps[0].op, HOST_SCRIPT_PEEK);
    CHECK_EQ(steps[0].addr, 0x8e0428u); // hex, the way every note writes it
    CHECK_EQ(steps[0].len, 179u);
    CHECK_EQ(steps[1].addr, 4096u); // and decimal, the way none of them do
    CHECK_EQ(steps[1].len, 16u);
    CHECK_EQ(steps[2].op, HOST_SCRIPT_WATCH);
    CHECK_EQ(steps[2].owner, 0);
    CHECK_EQ(steps[2].kind, 1);

    // A peek big enough to bury the log is a mistake in the script, and so is
    // an owner or a kind that is not a byte.
    CHECK_EQ(host_script_parse("peek 0x8e0428 4096\n", steps, 64, err, sizeof err), -1);
    CHECK_EQ(host_script_parse("peek 0x8e0428 0\n", steps, 64, err, sizeof err), -1);
    CHECK_EQ(host_script_parse("peek 0x8e0428\n", steps, 64, err, sizeof err), -1);
    CHECK_EQ(host_script_parse("watch 0\n", steps, 64, err, sizeof err), -1);
    CHECK_EQ(host_script_parse("watch 0 999\n", steps, 64, err, sizeof err), -1);

    // `await` waits on the game rather than on the clock, and its hold is what
    // makes it usable on a screen that fades in: a fade passes through every
    // value on its way up, so a claim that is only momentarily true has to be
    // rejected. Both clauses are optional and take either order.
    const char *awaits = "await picture>0.98\n"
                         "await picture>0.98 for 2000 within 45000\n"
                         "await scene_nonblack>0.5 within 30000 for 500\n";
    CHECK_EQ(host_script_parse(awaits, steps, 64, err, sizeof err), 3);
    CHECK_EQ(steps[0].op, HOST_SCRIPT_AWAIT);
    CHECK(strcmp(steps[0].name, "picture") == 0);
    CHECK(steps[0].threshold > 0.979 && steps[0].threshold < 0.981);
    CHECK_EQ(steps[0].hold_ms, 0u);        // no hold asked for
    CHECK_EQ(steps[0].timeout_ms, 60000u); // and a minute to give up in
    CHECK_EQ(steps[1].hold_ms, 2000u);
    CHECK_EQ(steps[1].timeout_ms, 45000u);
    CHECK_EQ(steps[2].hold_ms, 500u);
    CHECK_EQ(steps[2].timeout_ms, 30000u);

    CHECK_EQ(host_script_parse("await entity_body 1815 within 20000\n"
                               "await dumpat_fired>=1 within 120000\n",
                               steps, 64, err, sizeof err),
             2);
    CHECK_EQ(steps[0].op, HOST_SCRIPT_AWAIT);
    CHECK(strcmp(steps[0].name, "entity_body") == 0);
    CHECK_EQ(steps[0].entity_id, 1815);
    CHECK_EQ(steps[0].timeout_ms, 20000u);
    CHECK_EQ(steps[0].threshold, 0.0);
    CHECK(!steps[0].at_least); // missing BODY (zero) never passes
    CHECK_EQ(steps[1].op, HOST_SCRIPT_AWAIT);
    CHECK(strcmp(steps[1].name, "dumpat_fired") == 0);
    CHECK_EQ(steps[1].threshold, 1.0);
    CHECK(steps[1].at_least);

    // --- one numeric rule, and one name rule, for every verb ----------------
    //
    // strtol reports success for "abc" and hands back zero, so before this
    // rule `move abc def` was a move to the top-left corner and a script could
    // be wrong for months without saying so. Every operand goes through a
    // check that the whole token was a number. `dump` takes `dumpc`'s name
    // rule with it, because both names become part of a filename.
    struct {
        const char *text;
        const char *fragment;
    } bad_strict[] = {
        {"move abc def\n", "move x is a number"},
        {"move 10 def\n", "move y is a number"},
        {"moveby 1 x\n", "moveby dy is a number"},
        {"click left 1 y\n", "click y is a number"},
        {"wait 5x\n", "wait needs a number of ms"},
        {"peek 0xzz 4\n", "peek address is a number"},
        {"peek 0x8e0428 4bytes\n", "peek length is a number"},
        {"watch 0 one\n", "watch kind is a byte"},
        {"expect textures>lots\n", "expect needs a number after >"},
        {"await picture>soon\n", "await needs a number after >"},
        {"await entity_body\n", "needs an entity id"},
        {"await entity_body -1\n", "needs an entity id"},
        {"await entity_body 65536\n", "needs an entity id"},
        {"await entity_body 1815x\n", "needs an entity id"},
        {"await entity_body 1815 within 0\n", "timeout above zero"},
        {"await picture>0.5 within abc\n", "await needs a number of ms"},
        {"dump sub/name\n", "letters, digits"},
        {"dump .hidden\n", "cannot start with a dot"},
    };
    for (const auto &b : bad_strict) {
        err[0] = 0;
        CHECK_EQ(host_script_parse(b.text, steps, 64, err, sizeof err), -1);
        if (!strstr(err, b.fragment))
            printf("  strictness gap: %.*s -> \"%s\" (wanted \"%s\")\n", (int)strcspn(b.text, "\n"),
                   b.text, err, b.fragment);
        CHECK(strstr(err, b.fragment) != nullptr);
    }

    // And what the rule must NOT reject: everything the shipped scripts write,
    // including the hex a peek is written in and the decimal a watch is.
    const char *still_fine = "wait 400\n"
                             "move -2000 -2000\n"
                             "moveby -2000 -2000\n"
                             "click left 320 140\n"
                             "peek 0x8e0428 179\n"
                             "peek 4096 16\n"
                             "watch 0 1\n"
                             "dump level_start\n"
                             "expect scene_nonblack>0.50\n"
                             "await picture>0.98 for 2000 within 60000\n";
    err[0] = 0;
    CHECK_EQ(host_script_parse(still_fine, steps, 64, err, sizeof err), 9);
    CHECK_EQ(err[0], 0);

    // --- the display verbs -------------------------------------------------
    //
    // Round trip first: every field a step carries, including the two defaults
    // that are not written down (a mode's twenty-second timeout and a
    // guestclick's sixty-millisecond hold), because a default nobody asserts
    // is a default nobody notices changing.
    const char *display = "mode 800 600 16\n"
                          "mode 1280 960 16\n"
                          "guestclick 320 140\n"
                          "guestclick 12 34 right hold 250\n"
                          "probe 640 360 255 0 0\n"
                          "probe 1 2 3 4 5 8\n"
                          "dumpc widescreen\n"
                          "landmark 41 expect visible\n"
                          "landmark 7 expect hidden within 5000\n"
                          "dumpat turn>=100 ref_t100\n"
                          "dumpat picture>0.5 bright\n"
                          "dumpat ref_c840 command_frame>=840\n";
    CHECK_EQ(host_script_parse(display, steps, 64, err, sizeof err), 12);

    CHECK_EQ(steps[0].op, HOST_SCRIPT_MODE);
    CHECK_EQ(steps[0].w, 800);
    CHECK_EQ(steps[0].h, 600);
    CHECK_EQ(steps[0].bpp, 16);
    // No timeout: `mode` arms rather than waits, because the game applies a
    // mode only when a level starts and recreates its surfaces.
    CHECK_EQ(steps[0].timeout_ms, 0u);
    CHECK_EQ(steps[1].w, 1280);
    CHECK_EQ(steps[1].h, 960);
    CHECK_EQ(steps[1].bpp, 16);
    CHECK_EQ(steps[1].timeout_ms, 0u);

    CHECK_EQ(steps[2].op, HOST_SCRIPT_GUESTCLICK);
    CHECK_EQ(steps[2].x, 320);
    CHECK_EQ(steps[2].y, 140);
    CHECK_EQ(steps[2].button, 0);     // left unless said otherwise
    CHECK_EQ(steps[2].press_ms, 60u); // and the default hold
    CHECK_EQ(steps[3].x, 12);
    CHECK_EQ(steps[3].y, 34);
    CHECK_EQ(steps[3].button, 1);
    CHECK_EQ(steps[3].press_ms, 250u);

    CHECK_EQ(steps[4].op, HOST_SCRIPT_PROBE);
    CHECK_EQ(steps[4].x, 640);
    CHECK_EQ(steps[4].y, 360);
    CHECK_EQ(steps[4].r, 255);
    CHECK_EQ(steps[4].g, 0);
    CHECK_EQ(steps[4].b, 0);
    CHECK_EQ(steps[4].tol, 0); // exact unless asked otherwise
    CHECK_EQ(steps[5].tol, 8);

    CHECK_EQ(steps[6].op, HOST_SCRIPT_DUMPC);
    CHECK(strcmp(steps[6].name, "widescreen") == 0);

    CHECK_EQ(steps[7].op, HOST_SCRIPT_LANDMARK);
    CHECK_EQ(steps[7].entity_id, 41);
    CHECK_EQ(steps[7].want_visible, 1);
    CHECK_EQ(steps[7].timeout_ms, 20000u);
    CHECK_EQ(steps[8].entity_id, 7);
    CHECK_EQ(steps[8].want_visible, 0);
    CHECK_EQ(steps[8].timeout_ms, 5000u);

    // dumpat carries the metric in `name` and the dump's own name in `text`,
    // because it needs both and one field cannot hold two.
    CHECK_EQ(steps[9].op, HOST_SCRIPT_DUMPAT);
    CHECK(strcmp(steps[9].name, "turn") == 0);
    CHECK(strcmp(steps[9].text, "ref_t100") == 0);
    CHECK(steps[9].at_least);
    CHECK(steps[9].threshold > 99.9 && steps[9].threshold < 100.1);
    CHECK_EQ(steps[10].op, HOST_SCRIPT_DUMPAT);
    CHECK(strcmp(steps[10].name, "picture") == 0);
    CHECK(strcmp(steps[10].text, "bright") == 0);
    CHECK(!steps[10].at_least);

    CHECK_EQ(steps[11].op, HOST_SCRIPT_DUMPAT);
    CHECK(strcmp(steps[11].name, "command_frame") == 0);
    CHECK(strcmp(steps[11].text, "ref_c840") == 0);
    CHECK_EQ(steps[11].threshold, 840.0);
    CHECK(steps[11].at_least);
    // This host test binary has no mod runtime/game view linked or loaded.
    const uint32_t saved_turn = rd32(RECOMP_GLOBAL_SIMULATION_TURN_ADDR);
    const uint32_t saved_command = rd32(RECOMP_GLOBAL_COMMAND_FRAME_ADDR);
    wr32(RECOMP_GLOBAL_SIMULATION_TURN_ADDR, 820);
    wr32(RECOMP_GLOBAL_COMMAND_FRAME_ADDR, 839);
    CHECK_EQ(host_script_counter_metric("turn", rd32), 820.0);
    CHECK_EQ(host_script_counter_metric("command_frame", rd32), 839.0);
    CHECK_EQ(host_script_counter_metric("unknown", rd32), -1.0);
    CHECK(!host_dumpat_should_fire(1, 1, host_script_counter_metric(steps[11].name, rd32),
                                   steps[11].threshold, steps[11].at_least));
    // The next completed present can skip the requested command frame.
    for (uint32_t actual : {840u, 842u}) {
        wr32(RECOMP_GLOBAL_COMMAND_FRAME_ADDR, actual);
        CHECK(host_dumpat_should_fire(1, 1, host_script_counter_metric(steps[11].name, rd32),
                                      steps[11].threshold, steps[11].at_least));
    }
    wr32(RECOMP_GLOBAL_SIMULATION_TURN_ADDR, 860);
    CHECK_EQ(host_script_counter_metric("turn", rd32), 860.0);
    wr32(RECOMP_GLOBAL_SIMULATION_TURN_ADDR, saved_turn);
    wr32(RECOMP_GLOBAL_COMMAND_FRAME_ADDR, saved_command);

    // Five malformed forms for each of the five verbs. The categories the
    // brief names are missing operand, non-integer, unknown enum, extra token
    // and negative. Three of the verbs have no enum of their own, so the
    // nearest real rejection stands in its place and is named here rather than
    // quietly dropped: for `mode` a depth DirectDraw does not have, for
    // `probe` a colour component out of range, and for `dumpc`, which takes no
    // number at all, a name that could not be a filename.
    struct {
        const char *text;
        const char *fragment;
    } bad_display[] = {
        {"mode 640 480\n", "width, a height and a depth"},
        {"mode 640 x 8\n", "height is a number"},
        {"mode 640 480 7\n", "8 or 16"},
        {"mode 640 480 24\n", "8 or 16"},
        {"mode 640 480 32\n", "8 or 16"},
        {"mode 640 480 8 within 5000\n", "too many words"},
        {"mode -640 480 8\n", "width is a number"},

        {"guestclick 320\n", "x and y in guest pixels"},
        {"guestclick 320 14o\n", "y is a guest pixel"},
        {"guestclick 320 140 up\n", "left, right or middle"},
        {"guestclick 320 140 left hold 60 9\n", "too many words"},
        {"guestclick -1 140\n", "x is a guest pixel"},

        {"probe 10 20 30 40\n", "x, y and a colour"},
        {"probe 10 20 30 40 blue\n", "blue is 0 to 255"},
        {"probe 10 20 256 0 0\n", "red is 0 to 255"},
        {"probe 1 2 3 4 5 6 7\n", "too many words"},
        {"probe -1 2 3 4 5\n", "x is a drawable pixel"},

        {"dumpc\n", "dumpc needs a name"},
        {"dumpc sub/escape\n", "letters, digits"},
        {"dumpc .hidden\n", "cannot start with a dot"},
        {"dumpc ../escape\n", "cannot start with a dot"},
        {"dumpc wide screen\n", "too many words"},
        {"dumpc aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n",
         "too long"},

        {"landmark 41 expect\n", "id, `expect` and visible|hidden"},
        {"landmark forty expect visible\n", "id is an entity index"},
        {"landmark 41 expect maybe\n", "visible or hidden"},
        {"landmark 41 expect visible within 5000 9\n", "too many words"},
        {"landmark -1 expect visible\n", "id is an entity index"},

        {"dumpat turn>=100\n", "metric>=value and a name"},
        {"dumpat turn 100 name\n", "metric>value or metric>=value"},
        {"dumpat turn>=abc name\n", "needs a number after >"},
        {"dumpat turn>=100 sub/name\n", "letters, digits"},
        {"dumpat turn>=100 ref extra\n", "too many words"},
    };
    for (const auto &b : bad_display) {
        err[0] = 0;
        CHECK_EQ(host_script_parse(b.text, steps, 64, err, sizeof err), -1);
        // Say which line failed and what it actually said. A table of
        // twenty-five entries that reports only "a fragment was missing" is a
        // test that costs more to read than it saves.
        if (!strstr(err, b.fragment))
            printf("  malformed form not rejected as expected: %.*s -> \"%s\" "
                   "(wanted \"%s\")\n",
                   (int)strcspn(b.text, "\n"), b.text, err, b.fragment);
        CHECK(strstr(err, b.fragment) != nullptr);
        CHECK(strstr(err, "line 1") != nullptr);
    }

    // The clause keywords are the other half of `unknown enum`: a verb that
    // takes `within` must not silently accept a word that is not it, or a
    // script would read as if it had asked for a timeout it never got.
    CHECK_EQ(
        host_script_parse("landmark 4 expect visible until 5000\n", steps, 64, err, sizeof err),
        -1);
    CHECK(strstr(err, "landmark takes `within`") != nullptr);
    CHECK_EQ(host_script_parse("landmark 4 wants visible\n", steps, 64, err, sizeof err), -1);
    CHECK(strstr(err, "landmark takes `expect`") != nullptr);
    // And a timeout of zero is a script that means something it cannot have.
    CHECK_EQ(host_script_parse("guestclick 1 2 hold 0\n", steps, 64, err, sizeof err), -1);
    CHECK(strstr(err, "hold above zero") != nullptr);

    // An empty script is not an error; it simply does nothing.
    CHECK_EQ(host_script_parse("", steps, 64, err, sizeof err), 0);
    CHECK_EQ(host_script_parse("# nothing but a comment\n", steps, 64, err, sizeof err), 0);

    // And a script that would not fit is refused rather than truncated.
    std::string many;
    for (int i = 0; i < 70; ++i)
        many += "move 1 1\n";
    CHECK_EQ(host_script_parse(many.c_str(), steps, 64, err, sizeof err), -1);
    CHECK(strstr(err, "too many steps") != nullptr);
}

// The runtime's idle wait returns as soon as input arrives rather than sitting
// out its slice. The wait itself is AppKit and cannot run without a window;
// the rule it turns on is this, and it is checked against the real counter.
static void test_idle_wait_early_return() {
    host_input_reset();
    host_input_set_notify(nullptr);

    // Nothing arrived: wait out the slice.
    uint32_t before = host_input_notify_count();
    CHECK_EQ(host_idle_wait_result(before, host_input_notify_count()), 0);

    // A mouse move during the wait: come back at once.
    host_input_motion(4, 4, 1, 1);
    CHECK_EQ(host_idle_wait_result(before, host_input_notify_count()), 1);

    // A keystroke during the wait, which is the case that would otherwise cost
    // the guest a whole slice of latency for every key.
    before = host_input_notify_count();
    host_input_key(0x35, true);
    CHECK_EQ(host_idle_wait_result(before, host_input_notify_count()), 1);
    host_input_key(0x35, false);

    // Several events in one wait are still one early return.
    before = host_input_notify_count();
    host_input_motion(5, 5, 1, 0);
    host_input_motion(6, 6, 1, 0);
    host_input_button(0, true);
    host_input_button(0, false);
    CHECK_EQ(host_idle_wait_result(before, host_input_notify_count()), 1);

    CHECK_EQ(host_idle_wait_result(7, 7), 0);
    CHECK_EQ(host_idle_wait_result(7, 8), 1);
    host_input_reset();
}

// The mouse has to arrive in the guest's own pixels, through the same
// letterbox the frame is drawn with.
static void test_point_to_guest() {
    int32_t x = -1, y = -1;
    // A 1280x960 drawable is exactly two 640x480 frames, no border.
    host_present_point_to_guest(1280, 960, 640, 480, 0, 0, &x, &y);
    CHECK_EQ(x, 0);
    CHECK_EQ(y, 0);
    host_present_point_to_guest(1280, 960, 640, 480, 640, 480, &x, &y);
    CHECK_EQ(x, 320);
    CHECK_EQ(y, 240);
    host_present_point_to_guest(1280, 960, 640, 480, 1279, 959, &x, &y);
    CHECK_EQ(x, 639);
    CHECK_EQ(y, 479);

    // A 1400x1000 drawable scales by two and centres, so the frame starts at
    // (60, 20) and a click there is the guest's origin.
    host_present_point_to_guest(1400, 1000, 640, 480, 60, 20, &x, &y);
    CHECK_EQ(x, 0);
    CHECK_EQ(y, 0);
    host_present_point_to_guest(1400, 1000, 640, 480, 60 + 200, 20 + 100, &x, &y);
    CHECK_EQ(x, 100);
    CHECK_EQ(y, 50);
    // A click in the letterbox border clamps to the edge rather than being
    // dropped or reported as a negative coordinate.
    host_present_point_to_guest(1400, 1000, 640, 480, 5, 5, &x, &y);
    CHECK_EQ(x, 0);
    CHECK_EQ(y, 0);
    host_present_point_to_guest(1400, 1000, 640, 480, 1399, 999, &x, &y);
    CHECK_EQ(x, 639);
    CHECK_EQ(y, 479);

    // 1024x768 at 1:1, which is the acceptance resolution.
    host_present_point_to_guest(1024, 768, 1024, 768, 512, 384, &x, &y);
    CHECK_EQ(x, 512);
    CHECK_EQ(y, 384);
}

static void test_message_translation() {
    // The wParam of a mouse message: the buttons, and Shift and Control.
    CHECK_EQ(host_mouse_wparam(0, 0), 0);
    CHECK_EQ(host_mouse_wparam(1, 0), 0x0001); // MK_LBUTTON
    CHECK_EQ(host_mouse_wparam(2, 0), 0x0002); // MK_RBUTTON
    CHECK_EQ(host_mouse_wparam(4, 0), 0x0010); // MK_MBUTTON
    CHECK_EQ(host_mouse_wparam(3, 1), 0x0001 | 0x0002 | 0x0004);
    CHECK_EQ(host_mouse_wparam(0, 2), 0x0008); // MK_CONTROL
    CHECK_EQ(host_mouse_wparam(0, 3), 0x0004 | 0x0008);

    // WM_MOUSEMOVE's lParam is the position in the guest's own pixels, x in
    // the low half and y in the high half. A front end that draws its cursor
    // from the Win32 position rather than from DirectInput reads this.
    int32_t mx = 0, my = 0;
    host_present_point_to_guest(1280, 960, 640, 480, 400, 300, &mx, &my);
    CHECK_EQ(mx, 200);
    CHECK_EQ(my, 150);
    uint32_t lparam = ((uint32_t)(my & 0xffff) << 16) | (uint32_t)(mx & 0xffff);
    CHECK_EQ(lparam, 0x00960000u | 200u);

    // The lParam of a key message. W is scan code 0x11 and not extended.
    HostKeyMapping w = host_key_mapping(0x0d);
    CHECK_EQ(host_key_lparam(w, true, false, false), 0x00110001u);
    // A repeat sets the previous-state bit.
    CHECK_EQ(host_key_lparam(w, true, false, true), 0x40110001u);
    // A key up sets the transition and previous-state bits.
    CHECK_EQ(host_key_lparam(w, false, false, true), 0xc0110001u);
    // Alt held sets the context code, which is what tells WM_SYSKEYDOWN apart.
    CHECK_EQ(host_key_lparam(w, true, true, false), 0x20110001u);
    // An extended key carries bit 24 and only the low seven scan-code bits.
    HostKeyMapping up = host_key_mapping(0x7e);
    CHECK_EQ(host_key_lparam(up, true, false, false), 0x01480001u);

    // The modifier bits, from either side.
    CHECK_EQ(host_modifier_bits(0), 0);
    CHECK_EQ(host_modifier_bits(0x00000002u), 1); // left shift
    CHECK_EQ(host_modifier_bits(0x00000004u), 1); // right shift
    CHECK_EQ(host_modifier_bits(0x00002000u), 2); // right control
    CHECK_EQ(host_modifier_bits(0x00000020u), 4); // left option
    CHECK_EQ(host_modifier_bits(0x00000004u | 0x00000001u), 3);

    // Every modifier key is diffed, and each one answers for its own side.
    CHECK(host_modifier_key_count() >= 6);
    CHECK(host_modifier_side_down(0x00000002u, 0x38));  // left shift down
    CHECK(!host_modifier_side_down(0x00000002u, 0x3c)); // right shift up
    CHECK(host_modifier_side_down(0x00010000u, 0x39));  // caps lock
    bool covered_left_shift = false, covered_right_alt = false;
    for (int i = 0; i < host_modifier_key_count(); ++i) {
        if (host_modifier_key(i) == 0x38)
            covered_left_shift = true;
        if (host_modifier_key(i) == 0x3d)
            covered_right_alt = true;
    }
    CHECK(covered_left_shift);
    CHECK(covered_right_alt);
}

// ===========================================================================
// audio.mm
// ===========================================================================
static void test_game_path() {
    char dir[512];
    snprintf(dir, sizeof dir, "%s/pop-gamepath-XXXXXX", os_temp_dir());
    CHECK(os_mkdtemp(dir) == 0);
    os_setenv("RECOMP_PROFILE_DIR", dir);
    os_unsetenv("RECOMP_EXE");
    host_layout_set_exe_path_for_test((std::string(dir) + "/nowhere/exe").c_str()); // no checkout
    // The build's own developer copy (game.toml developer_exe) is for
    // developer runs only: an app with no checkout above it (a package, on
    // any Mac) finds the player's game by its saved path or the launcher.
    OsStat dev_st;
    const bool have_dev = os_stat(RECOMP_DEVELOPER_EXE, &dev_st) == 0;
    const GamePathSource idle = GamePathSource::None;
    GamePath first = game_path_resolve(nullptr);
    CHECK(first.source == idle);
    CHECK(first.exe.empty());
    // A flag wins without any check.
    CHECK(game_path_resolve("/x/D3DPopTB.exe").source == GamePathSource::Flag);
    // A saved path to a wrong file is ignored.
    std::string wrong = std::string(dir) + "/wrong.exe";
    FILE *f = fopen(wrong.c_str(), "wb");
    fputs("not the game", f);
    fclose(f);
    std::string digest;
    CHECK(!game_path_is_supported(wrong, &digest));
    CHECK(digest.size() == 64);
    CHECK(game_path_save(wrong));
    CHECK(game_path_resolve(nullptr).source == idle);
    // The real game, when present, is accepted and remembered.
    if (have_dev) {
        CHECK(game_path_is_supported(RECOMP_DEVELOPER_EXE, nullptr));
        CHECK(game_path_save(RECOMP_DEVELOPER_EXE));
        GamePath g = game_path_resolve(nullptr);
        CHECK(g.exe == RECOMP_DEVELOPER_EXE);
        CHECK(g.source == GamePathSource::Saved);
    }
    os_unsetenv("RECOMP_PROFILE_DIR");
    // A developer run (a checkout above the executable; the kit is one) uses the build's own copy.
    if (have_dev) {
        host_layout_set_exe_path_for_test((std::string(RECOMP_KIT_DIR) + "/build/x/exe").c_str());
        CHECK(game_path_resolve(nullptr).source == GamePathSource::Checkout);
    }
    host_layout_set_exe_path_for_test(nullptr);
}

// A developer build finds the kit's General MIDI bank in the kit's own tree,
// and the MIDI device offers it to a game that ships no bank of its own.
static void test_bundled_general_midi() {
    const std::string bank = host_resource("general-midi.sf2");
    OsStat st;
    CHECK(!bank.empty() && os_stat(bank.c_str(), &st) == 0 && st.is_regular);
    CHECK(bank.find("third_party/soundfonts/generaluser-gs/GeneralUser-GS.sf2") !=
          std::string::npos);
    CHECK_EQ(st.size, 32319396u);
}

// The game's controls layouts: a game repository's layouts/ in a developer
// run, the resources' controls/ in a packaged one.
static void test_controls_layouts_resource() {
    char dir[512];
    snprintf(dir, sizeof dir, "%s/controls-resource-XXXXXX", os_temp_dir());
    CHECK(os_mkdtemp(dir) == 0);
    const std::string root = dir;
    os_mkdir((root + "/build").c_str());
    FILE *f = fopen((root + "/game.toml").c_str(), "wb");
    CHECK(f != nullptr);
    if (f)
        fclose(f);
    host_layout_set_exe_path_for_test((root + "/build/app").c_str());
    CHECK(host_resource("controls") == root + "/layouts");
    remove((root + "/game.toml").c_str());
    os_mkdir((root + "/build/resources").c_str());
    host_layout_set_exe_path_for_test((root + "/build/app").c_str());
    CHECK(host_resource("controls") == root + "/build/resources/controls");
    host_layout_set_exe_path_for_test(nullptr);
    os_rmdir((root + "/build/resources").c_str());
    os_rmdir((root + "/build").c_str());
    os_rmdir(root.c_str());
}

static void test_audio_maths() {
    CHECK_NEAR(host_audio_gain_from_millibels(0), 1.0, 1e-6);
    CHECK_NEAR(host_audio_gain_from_millibels(-10000), 0.0, 1e-6);
    // -600 hundredths of a dB is -6 dB, which is half the amplitude.
    CHECK_NEAR(host_audio_gain_from_millibels(-602), 0.5, 0.01);
    CHECK_NEAR(host_audio_gain_from_millibels(-2000), 0.1, 1e-3);
    CHECK_NEAR(host_audio_gain_from_millibels(500), 1.0, 1e-6); // clamped

    // Pan attenuates one channel and leaves the other exactly as it was: a
    // centred sound is two channels at full scale, not two at 0.707.
    float l = 9, r = 9;
    host_audio_pan_gains(0, &l, &r);
    CHECK_NEAR(l, 1.0, 1e-6);
    CHECK_NEAR(r, 1.0, 1e-6);
    host_audio_pan_gains(10000, &l, &r); // hard right
    CHECK_NEAR(l, 0.0, 1e-6);
    CHECK_NEAR(r, 1.0, 1e-6);
    host_audio_pan_gains(-10000, &l, &r); // hard left
    CHECK_NEAR(l, 1.0, 1e-6);
    CHECK_NEAR(r, 0.0, 1e-6);
    // -6 dB on one side, the other untouched.
    host_audio_pan_gains(602, &l, &r);
    CHECK_NEAR(l, 0.5, 0.01);
    CHECK_NEAR(r, 1.0, 1e-6);
    host_audio_pan_gains(-602, &l, &r);
    CHECK_NEAR(l, 1.0, 1e-6);
    CHECK_NEAR(r, 0.5, 0.01);

    // A first Play uses the buffer's own rate. Nothing may leak a default
    // into it: a 44100 Hz buffer that inherited 22050 plays at half speed.
    CHECK_EQ(host_audio_play_rate(44100, 22050, 0), 44100);
    CHECK_EQ(host_audio_play_rate(11025, 22050, 0), 11025);
    // An explicit SetFrequency displaces it.
    CHECK_EQ(host_audio_play_rate(44100, 8000, 1), 8000);
    // DSBFREQUENCY_ORIGINAL clears the override, so the buffer's rate returns.
    CHECK_EQ(host_audio_play_rate(44100, 0, 1), 44100);
    CHECK_EQ(host_audio_play_rate(0, 0, 0), 22050);

    // The play cursor advances in real time whether or not anything is
    // audible. The game's video player queues a chunk, polls
    // GetCurrentPosition and sleeps until it has moved: a cursor that stops
    // because nothing is playing stops the video with it.
    const uint32_t kTotal = 44100 * 4; // one second, 16-bit stereo
    // Half a second in is half the buffer, on a frame boundary.
    CHECK_EQ(host_audio_wall_clock_bytes(0.5, 44100, 16, 2, 0, kTotal, 0), kTotal / 2);
    CHECK_EQ(host_audio_wall_clock_bytes(0.0, 44100, 16, 2, 0, kTotal, 0), 0u);
    // It starts from where this playback started, not from zero.
    CHECK_EQ(host_audio_wall_clock_bytes(0.25, 44100, 16, 2, 1000, kTotal, 0), 1000u + kTotal / 4);
    // A one-shot ends at the end of the buffer rather than running past it.
    CHECK_EQ(host_audio_wall_clock_bytes(5.0, 44100, 16, 2, 0, kTotal, 0), kTotal);
    // A loop wraps over the whole buffer.
    CHECK_EQ(host_audio_wall_clock_bytes(1.25, 44100, 16, 2, 0, kTotal, 1), kTotal / 4);
    CHECK_EQ(host_audio_wall_clock_bytes(2.0, 44100, 16, 2, 0, kTotal, 1), 0u);
    // Always on a frame boundary, because that is what hardware reports.
    for (int i = 1; i < 40; ++i) {
        uint32_t at = host_audio_wall_clock_bytes(i * 0.0011, 22050, 16, 2, 0, kTotal, 0);
        CHECK_EQ(at % 4u, 0u);
    }
    // And it never goes backwards as the clock moves forwards.
    uint32_t last = 0;
    for (int i = 0; i < 50; ++i) {
        uint32_t at = host_audio_wall_clock_bytes(i * 0.01, 44100, 16, 2, 0, kTotal, 0);
        CHECK(at >= last);
        last = at;
    }
    // A rate of zero cannot divide by anything: it stays where it started.
    CHECK_EQ(host_audio_wall_clock_bytes(1.0, 0, 16, 2, 77, kTotal, 0), 77u);

    CHECK_EQ(host_audio_frame_bytes(8, 1), 1);
    CHECK_EQ(host_audio_frame_bytes(8, 2), 2);
    CHECK_EQ(host_audio_frame_bytes(16, 1), 2);
    CHECK_EQ(host_audio_frame_bytes(16, 2), 4);

    // 8-bit PCM is unsigned with silence at 0x80.
    const uint8_t mono8[4] = {0x80, 0x00, 0xff, 0x40};
    float left[4] = {9, 9, 9, 9}, right[4] = {9, 9, 9, 9};
    CHECK_EQ(host_audio_decode_pcm(mono8, 4, 8, 1, left, right, 4), 4);
    CHECK_NEAR(left[0], 0.0, 1e-6);
    CHECK_NEAR(left[1], -1.0, 1e-6);
    CHECK_NEAR(left[2], 127.0 / 128.0, 1e-6);
    CHECK_NEAR(left[3], -0.5, 1e-6);
    // A mono source feeds both sides rather than only the left.
    CHECK_NEAR(right[1], -1.0, 1e-6);

    // 16-bit is signed little-endian.
    const uint8_t stereo16[8] = {0x00, 0x80, 0xff, 0x7f, 0x00, 0x00, 0x00, 0x40};
    CHECK_EQ(host_audio_decode_pcm(stereo16, 8, 16, 2, left, right, 4), 2);
    CHECK_NEAR(left[0], -1.0, 1e-6);
    CHECK_NEAR(right[0], 32767.0 / 32768.0, 1e-6);
    CHECK_NEAR(left[1], 0.0, 1e-6);
    CHECK_NEAR(right[1], 0.5, 1e-6);

    // The intro's audio is the format most likely to be wrong end to end, so
    // it is decoded here explicitly: 8-bit unsigned mono, which is what a
    // 1998 game's speech and video audio actually is. A silent run has to be
    // told apart from a run that decoded silence, and the decisive number is
    // whether the samples have any amplitude at all.
    uint8_t mono8_ramp[64];
    for (int i = 0; i < 64; ++i)
        mono8_ramp[i] = (uint8_t)(128 + (i - 32) * 3);
    float ramp_l[64] = {0}, ramp_r[64] = {0};
    CHECK_EQ(host_audio_decode_pcm(mono8_ramp, 64, 8, 1, ramp_l, ramp_r, 64), 64);
    float peak = 0;
    for (int i = 0; i < 64; ++i) {
        float v = ramp_l[i] < 0 ? -ramp_l[i] : ramp_l[i];
        if (v > peak)
            peak = v;
        // A mono source has to reach both sides, or half the mix is silent.
        CHECK_NEAR(ramp_l[i], ramp_r[i], 1e-6);
    }
    CHECK(peak > 0.5);
    // And silence really does decode as silence, so the peak means something.
    uint8_t mono8_silence[64];
    memset(mono8_silence, 0x80, sizeof mono8_silence);
    CHECK_EQ(host_audio_decode_pcm(mono8_silence, 64, 8, 1, ramp_l, nullptr, 64), 64);
    for (int i = 0; i < 64; ++i)
        CHECK_NEAR(ramp_l[i], 0.0, 1e-6);

    // Downmix when the destination is mono.
    CHECK_EQ(host_audio_decode_pcm(stereo16, 8, 16, 2, left, nullptr, 4), 2);
    CHECK_NEAR(left[0], (-1.0 + 32767.0 / 32768.0) / 2, 1e-6);

    // The buffer cannot be overrun by a byte count that claims more frames.
    CHECK_EQ(host_audio_decode_pcm(mono8, 4, 8, 1, left, nullptr, 2), 2);

    // GetCurrentPosition: bytes from the start, wrapping only when looping.
    CHECK_EQ(host_audio_position_bytes(0, 0, 1000, 16, 2, 0), 0);
    CHECK_EQ(host_audio_position_bytes(10, 0, 1000, 16, 2, 0), 40);
    CHECK_EQ(host_audio_position_bytes(10, 100, 1000, 16, 2, 0), 140);
    CHECK_EQ(host_audio_position_bytes(1000, 0, 1000, 16, 2, 0), 1000); // clamped
    CHECK_EQ(host_audio_position_bytes(300, 0, 1000, 16, 2, 1), 200);   // wrapped
    // A loop that began part-way through returns to the start of the buffer,
    // not to the offset it began at: at the moment the tail runs out the
    // position is 0.
    CHECK_EQ(host_audio_position_bytes(150, 400, 1000, 16, 2, 1), 0);
    CHECK_EQ(host_audio_position_bytes(160, 400, 1000, 16, 2, 1), 40);
}

static void test_rate_meter() {
    // Sixty frames a second for ten seconds is sixty frames a second.
    HostRateMeter m;
    host_rate_meter_reset(&m);
    double t = 100.0;
    for (int i = 0; i < 600; ++i, t += 1.0 / 60.0)
        host_rate_meter_frame(&m, t);
    CHECK_EQ(m.drawn, 600);
    CHECK_NEAR(m.active_seconds, 599.0 / 60.0, 0.01);
    CHECK_NEAR(m.best_window_fps, 60.0, 1.0);

    // A load screen in the middle is not time the game was running at any
    // frame rate, so it does not count against one.
    host_rate_meter_reset(&m);
    t = 0.0;
    for (int i = 0; i < 120; ++i, t += 1.0 / 60.0)
        host_rate_meter_frame(&m, t);
    t += 30.0; // thirty seconds of nothing
    for (int i = 0; i < 120; ++i, t += 1.0 / 60.0)
        host_rate_meter_frame(&m, t);
    CHECK_EQ(m.drawn, 240);
    // 238 short gaps counted, the one long gap not.
    CHECK_NEAR(m.active_seconds, 238.0 / 60.0, 0.05);
    CHECK(m.active_seconds < 5.0);

    // Two frames a millisecond apart are not a thousand frames a second: a
    // sustained rate needs a full window behind it.
    host_rate_meter_reset(&m);
    host_rate_meter_frame(&m, 0.0);
    host_rate_meter_frame(&m, 0.001);
    CHECK_NEAR(m.best_window_fps, 0.0, 1e-9);

    // A slow stretch after a fast one does not lower the best window.
    host_rate_meter_reset(&m);
    t = 0.0;
    for (int i = 0; i < 600; ++i, t += 1.0 / 60.0)
        host_rate_meter_frame(&m, t);
    double fast = m.best_window_fps;
    for (int i = 0; i < 100; ++i, t += 1.0 / 10.0)
        host_rate_meter_frame(&m, t);
    CHECK_NEAR(m.best_window_fps, fast, 1e-9);
    CHECK(fast > 50.0);

    // A run that never fills a window reports no sustained rate rather than
    // a flattering one.
    host_rate_meter_reset(&m);
    t = 0.0;
    for (int i = 0; i < 30; ++i, t += 1.0 / 60.0)
        host_rate_meter_frame(&m, t);
    CHECK_NEAR(m.best_window_fps, 0.0, 1e-9);
    CHECK(host_rate_window() >= 1.0);
    CHECK(host_rate_active_gap() > 0.0);
}

// The deadlock the first live run hit. AVFoundation drains a node's completion
// handlers from inside [node stop], synchronously, so a completion that takes
// the lock the stopper is holding hangs the process: the guest's audio thread
// never returns, never gives the cooperative baton back, and nothing renders.
//
// The stand-in node calls model that exactly - stop fires the completion
// handler before it returns - so the state machine is driven through the same
// re-entrant path with no audio device involved.
namespace {
int g_fake_stops = 0, g_fake_plays = 0, g_fake_schedules = 0;
uint64_t g_fake_last_generation = 0;
// The generation a completion is still owed for, so stop can fire it the way
// the framework does.
uint64_t g_fake_pending = 0;

void fake_stop(int32_t channel, uint64_t generation) {
    (void)generation;
    ++g_fake_stops;
    // This is the re-entry: inside stop, on another thread in the real thing,
    // the completion handler for whatever was scheduled runs.
    if (g_fake_pending) {
        uint64_t owed = g_fake_pending;
        g_fake_pending = 0;
        host_audio_completed(channel, owed);
    }
}
void fake_play(int32_t channel, uint64_t generation) {
    (void)channel;
    (void)generation;
    ++g_fake_plays;
}
int g_fake_queues = 0;
uint32_t g_fake_queued_bytes = 0;
void fake_queue(int32_t channel, uint32_t bytes) {
    (void)channel;
    ++g_fake_queues;
    g_fake_queued_bytes += bytes;
}
void fake_schedule(int32_t channel, uint64_t generation, uint32_t from, uint32_t to, int loop) {
    (void)channel;
    (void)from;
    (void)to;
    ++g_fake_schedules;
    g_fake_last_generation = generation;
    // A one-shot will complete; a loop will not.
    g_fake_pending = loop ? 0 : generation;
}
} // namespace

static void test_audio_completion_during_stop() {
    HostAudioNodeOps ops = {fake_stop, fake_play, fake_schedule, fake_queue};
    host_audio_set_node_ops(&ops);
    g_fake_stops = g_fake_plays = g_fake_schedules = 0;
    g_fake_pending = 0;
    uint32_t violations_before = host_audio_lock_violations();

    // 16-bit stereo, a tenth of a second at 44100.
    std::vector<uint8_t> pcm(4 * 4410, 0);
    HostAudioPlay play;
    memset(&play, 0, sizeof play);
    play.channel = 3;
    play.pcm = pcm.data();
    play.bytes = (uint32_t)pcm.size();
    play.sample_rate = 44100;
    play.channels = 2;
    play.bits = 16;

    host_audio_play(&play);
    CHECK_EQ(g_fake_schedules, 1);
    CHECK_EQ(g_fake_plays, 1);
    CHECK_EQ(host_audio_is_playing(3), 1);

    // Playing it again interrupts the first. The stop inside that call fires
    // the first sound's completion handler, from inside the same call that is
    // scheduling the second - which is the shape that deadlocked.
    host_audio_play(&play);
    CHECK_EQ(g_fake_schedules, 2);
    // The completion that arrived named the sound that was replaced, so it
    // must not report the replacement as finished.
    CHECK_EQ(host_audio_is_playing(3), 1);

    // The completion for the sound that is actually current does end it.
    host_audio_completed(3, g_fake_last_generation);
    CHECK_EQ(host_audio_is_playing(3), 0);
    // And a finished one-shot reports its cursor at the end of the buffer.
    CHECK_EQ(host_audio_position(3), (uint32_t)pcm.size());

    // A stop while a completion is owed is the same re-entry from the other
    // direction.
    host_audio_play(&play);
    CHECK_EQ(host_audio_is_playing(3), 1);
    host_audio_stop(3);
    CHECK_EQ(host_audio_is_playing(3), 0);

    // A looping buffer is never reported finished by a completion.
    play.loop = 1;
    host_audio_play(&play);
    CHECK_EQ(host_audio_is_playing(3), 1);
    host_audio_completed(3, g_fake_last_generation);
    CHECK_EQ(host_audio_is_playing(3), 1);
    host_audio_stop(3);

    // The whole point: not one node call was made with the channel lock held.
    CHECK_EQ(host_audio_lock_violations(), violations_before);
    host_audio_set_node_ops(nullptr);
}

// A streamed wave is refilled by appending to the sound that is already
// playing. The join has to be sample-accurate, which means the node is never
// stopped and never restarted: a stop would drop what is still queued and a
// restart would reset the render position, and either is an audible seam at
// every refill.
static void test_audio_gapless_queue() {
    HostAudioNodeOps ops = {fake_stop, fake_play, fake_schedule, fake_queue};
    host_audio_set_node_ops(&ops);
    g_fake_stops = g_fake_plays = g_fake_schedules = 0;
    g_fake_queues = 0;
    g_fake_queued_bytes = 0;
    g_fake_pending = 0;

    // 16-bit stereo: four bytes a frame.
    std::vector<uint8_t> pcm(4 * 1000, 0);
    HostAudioPlay play;
    memset(&play, 0, sizeof play);
    play.channel = 5;
    play.pcm = pcm.data();
    play.bytes = (uint32_t)pcm.size();
    play.sample_rate = 22050;
    play.channels = 2;
    play.bits = 16;

    // Nothing may be appended to a channel that was never played: there is no
    // format to append in, and inventing one would invent the sound's rate.
    CHECK_EQ(host_audio_queue(5, pcm.data(), 400), 0);
    CHECK_EQ(host_audio_queued_bytes(5), 0u);

    host_audio_play(&play);
    int stops_after_play = g_fake_stops;
    int plays_after_play = g_fake_plays;
    CHECK_EQ(host_audio_queued_bytes(5), 0u);

    // Two appends, back to back.
    CHECK_EQ(host_audio_queue(5, pcm.data(), 400), 400);
    CHECK_EQ(host_audio_queue(5, pcm.data(), 800), 800);
    CHECK_EQ(g_fake_queues, 2);
    CHECK_EQ(g_fake_queued_bytes, 1200u);
    CHECK_EQ(host_audio_queued_bytes(5), 1200u);

    // The continuity that matters: neither append stopped the node and neither
    // started it again, so the render position was never reset.
    CHECK_EQ(g_fake_stops, stops_after_play);
    CHECK_EQ(g_fake_plays, plays_after_play);
    CHECK_EQ(g_fake_schedules, 1); // the Play's buffer, and no more

    // As each append plays out, the queue drains by exactly what finished.
    host_audio_queue_completed(5, 400);
    CHECK_EQ(host_audio_queued_bytes(5), 800u);
    host_audio_queue_completed(5, 800);
    CHECK_EQ(host_audio_queued_bytes(5), 0u);
    // And it does not go negative if a completion arrives twice.
    host_audio_queue_completed(5, 400);
    CHECK_EQ(host_audio_queued_bytes(5), 0u);

    // A partial frame is refused rather than half-appended: three bytes of a
    // four-byte frame is not a sample.
    CHECK_EQ(host_audio_queue(5, pcm.data(), 3), 0);
    // A byte count that is not a whole number of frames is truncated to one.
    CHECK_EQ(host_audio_queue(5, pcm.data(), 402), 400);
    CHECK_EQ(host_audio_queued_bytes(5), 400u);

    // A streamed channel does not run out: its cursor keeps going where a
    // one-shot's would have stopped at the end of its buffer.
    CHECK_EQ(host_audio_is_playing(5), 1);

    // A fresh Play replaces the stream, and the accounting starts again.
    host_audio_play(&play);
    CHECK_EQ(host_audio_queued_bytes(5), 0u);

    // The lock order held throughout: not one node call under the data lock.
    CHECK_EQ(host_audio_lock_violations(), 0u);
    host_audio_set_node_ops(nullptr);
}

// The game streams its music by playing a looping buffer and refilling it. Two
// things have to be true or the refill is never heard: a re-submitted loop must
// stop and reschedule, because a buffer scheduled with the loop option keeps
// playing the bytes it was given; and a queue behind an endless loop must be
// refused rather than silently appended where nothing will reach it.
static void test_audio_looping_refill() {
    HostAudioNodeOps ops = {fake_stop, fake_play, fake_schedule, fake_queue};
    host_audio_set_node_ops(&ops);
    g_fake_stops = g_fake_plays = g_fake_schedules = 0;
    g_fake_queues = 0;
    g_fake_pending = 0;

    // A looping buffer of silence, which is how the stream starts.
    std::vector<uint8_t> silence(4 * 2048, 0);
    for (size_t i = 0; i < silence.size(); i += 2) {
        silence[i] = 0x00;
        silence[i + 1] = 0x00; // 16-bit zero
    }
    HostAudioPlay play;
    memset(&play, 0, sizeof play);
    play.channel = 9;
    play.pcm = silence.data();
    play.bytes = (uint32_t)silence.size();
    play.sample_rate = 22050;
    play.channels = 2;
    play.bits = 16;
    play.loop = 1;

    host_audio_play(&play);
    CHECK_EQ(host_audio_is_playing(9), 1);
    int stops = g_fake_stops, schedules = g_fake_schedules;

    // A queue on a looping channel is refused: appending behind a buffer that
    // plays for ever would never be reached, and returning 0 tells the caller
    // to re-submit instead.
    CHECK_EQ(host_audio_queue(9, silence.data(), 512), 0);
    CHECK_EQ(g_fake_queues, 0);
    CHECK_EQ(host_audio_queued_bytes(9), 0u);

    // The refill, with real content this time. It has to stop the node and
    // schedule again, or the loop keeps playing the silence it was given.
    std::vector<uint8_t> tone(4 * 2048, 0);
    for (size_t i = 0; i + 1 < tone.size(); i += 2) {
        int16_t v = (int16_t)((i % 64) * 400 - 12000);
        tone[i] = (uint8_t)(v & 0xff);
        tone[i + 1] = (uint8_t)((v >> 8) & 0xff);
    }
    play.pcm = tone.data();
    host_audio_play(&play);
    CHECK(g_fake_stops > stops);         // stopped, so the old loop ends
    CHECK(g_fake_schedules > schedules); // and the new content scheduled
    CHECK_EQ(host_audio_is_playing(9), 1);

    // A one-shot channel still takes a queue, which is the case the streaming
    // caller uses.
    play.channel = 10;
    play.loop = 0;
    host_audio_play(&play);
    CHECK_EQ(host_audio_queue(10, tone.data(), 512), 512);
    CHECK_EQ(host_audio_queued_bytes(10), 512u);

    CHECK_EQ(host_audio_lock_violations(), 0u);
    host_audio_set_node_ops(nullptr);
}

// The intro movie's own pattern, from the Wine trace of the original: a 32 KB
// ring at 22050 Hz stereo 16-bit, played looping while still empty, then locked
// and refilled 3 to 4 KB at a time while a thread polls the cursor. Every step
// below is that sequence, and it is the one that garbled the sound and stalled
// the movie when the host answered any part of it wrongly.
static void test_audio_fmv_ring() {
    HostAudioNodeOps ops = {fake_stop, fake_play, fake_schedule, fake_queue};
    host_audio_set_node_ops(&ops);
    g_fake_stops = g_fake_plays = g_fake_schedules = 0;
    g_fake_queues = 0;
    g_fake_pending = 0;

    const uint32_t kRing = 32768;
    std::vector<uint8_t> ring(kRing, 0); // played empty, as the game does
    HostAudioPlay play;
    memset(&play, 0, sizeof play);
    play.channel = 12;
    play.pcm = ring.data();
    play.bytes = kRing;
    play.sample_rate = 22050;
    play.channels = 2;
    play.bits = 16;
    play.loop = 1;
    host_audio_play(&play);
    CHECK_EQ(host_audio_is_playing(12), 1);

    // While it is a loop, an append cannot be reached and is refused: the
    // caller has to convert first, which is the whole handshake.
    CHECK_EQ(host_audio_queue(12, ring.data(), 3584), 0);

    // The first write into the ring converts it. The conversion continues from
    // the cursor rather than starting again, and says where it resumed.
    int32_t from = host_audio_stream(12);
    CHECK(from >= 0);
    CHECK(from < (int32_t)kRing);
    // Converting again changes nothing and still answers.
    CHECK(host_audio_stream(12) >= 0);

    // Now the refills: 3 to 4 KB at a time, as the trace has them.
    const uint32_t chunks[] = {3584, 3472, 3584, 3472};
    uint32_t appended = 0;
    for (uint32_t c : chunks) {
        int32_t took = host_audio_queue(12, ring.data(), c);
        CHECK_EQ(took, (int32_t)c);
        appended += c;
    }
    CHECK_EQ(host_audio_queued_bytes(12), appended);
    // None of that stopped or restarted the voice, which is what stopping and
    // rescheduling twenty-five times a second sounded like: silence. Two stops
    // in the whole sequence: the initial play, and the single conversion that
    // has to reschedule to stop the buffer looping. The four refills cost none.
    CHECK_EQ(g_fake_stops, 2);
    CHECK_EQ(g_fake_queues, 4);

    // The cursor the writer paces against carries on from where the loop had
    // reached, advances at the sample rate, and never goes backwards.
    uint32_t a = host_audio_played_bytes(12);
    CHECK(a >= (uint32_t)from);
    uint32_t b = host_audio_played_bytes(12);
    CHECK(b >= a);

    // An underrun: everything queued has played and the next chunk is late.
    // The append is still taken - refusing would send the caller back to
    // re-submitting the whole sound at every refill.
    host_audio_queue_completed(12, appended);
    CHECK_EQ(host_audio_queued_bytes(12), 0u);
    CHECK_EQ(host_audio_queue(12, ring.data(), 3584), 3584);
    CHECK_EQ(host_audio_queued_bytes(12), 3584u);
    CHECK_EQ(host_audio_is_playing(12), 1);

    // And a completion for the head buffer does not retire the stream, which
    // is the bug that stopped the intro a third of a second in: the converting
    // buffer is a one-shot, so its completion arrives while appended buffers
    // are still scheduled behind it.
    host_audio_completed(12, g_fake_last_generation);
    CHECK_EQ(host_audio_is_playing(12), 1);
    CHECK_EQ(host_audio_queue(12, ring.data(), 3584), 3584);

    CHECK_EQ(host_audio_lock_violations(), 0u);
    host_audio_set_node_ops(nullptr);
}

static int64_t g_fake_sample_time = 0;
static int fake_sample_time(int32_t, int64_t *frames) {
    *frames = g_fake_sample_time;
    return 1;
}

static void test_audio_negative_player_time() {
    HostAudioNodeOps ops = {fake_stop, fake_play, fake_schedule, fake_queue, fake_sample_time};
    host_audio_set_node_ops(&ops);
    g_fake_stops = g_fake_plays = g_fake_schedules = g_fake_queues = 0;
    g_fake_pending = 0;
    const int32_t ch = 56;
    std::vector<uint8_t> pcm(32768, 0);
    HostAudioPlay play{};
    play.channel = ch;
    play.pcm = pcm.data();
    play.bytes = (uint32_t)pcm.size();
    play.sample_rate = 22050;
    play.channels = 2;
    play.bits = 16;
    for (uint32_t start : {0u, 2048u}) {
        play.start_offset = start;
        for (int64_t sample : {int64_t(-472), int64_t(-1), int64_t(INT64_MIN)}) {
            g_fake_sample_time = sample;
            host_audio_play(&play);
            const int plays = g_fake_plays, stops = g_fake_stops;
            const uint64_t generation = g_fake_last_generation;
            // A newly started node can still report the previous render quantum.
            // Converting that one-shot must not restart it or discard its head.
            CHECK_EQ(host_audio_stream(ch), (int32_t)start);
            CHECK_EQ(g_fake_plays, plays);
            CHECK_EQ(g_fake_stops, stops);
            CHECK_EQ(g_fake_last_generation, generation);
            CHECK_EQ(host_audio_queue(ch, pcm.data(), 4096), 4096);
            host_audio_stop(ch);
            CHECK_EQ(host_audio_position(ch), start);
        }
    }
    play.start_offset = 2048;
    // A real positive position still advances the conversion point.
    g_fake_sample_time = 128;
    host_audio_play(&play);
    int plays = g_fake_plays;
    CHECK_EQ(host_audio_stream(ch), 2560);
    CHECK_EQ(g_fake_plays, plays);
    host_audio_stop(ch);
    // A cursor beyond the buffer still restarts a completed voice.
    g_fake_sample_time = 32768;
    host_audio_play(&play);
    plays = g_fake_plays;
    CHECK_EQ(host_audio_stream(ch), 0);
    CHECK_EQ(g_fake_plays, plays + 1);
    host_audio_stop(ch);
    CHECK_EQ(host_audio_lock_violations(), 0u);
    host_audio_set_node_ops(nullptr);
}

static void test_audio_one_shot_stream_preserves_schedule() {
    HostAudioNodeOps ops = {fake_stop, fake_play, fake_schedule, fake_queue};
    host_audio_set_node_ops(&ops);
    g_fake_stops = g_fake_plays = g_fake_schedules = g_fake_queues = 0;
    g_fake_pending = 0;
    const int32_t ch = 55;
    std::vector<uint8_t> pcm(4 * 48000, 0);
    HostAudioPlay play{};
    play.channel = ch;
    play.pcm = pcm.data();
    play.bytes = (uint32_t)pcm.size();
    play.sample_rate = 48000;
    play.channels = 2;
    play.bits = 16;
    play.start_offset = 2048;
    host_audio_play(&play);
    uint64_t generation = g_fake_last_generation;
    CHECK_EQ(host_audio_stream(ch), 2048);
    CHECK_EQ(g_fake_stops, 1);
    CHECK_EQ(g_fake_plays, 1);
    CHECK_EQ(g_fake_schedules, 1);
    CHECK_EQ(g_fake_last_generation, generation);
    CHECK_EQ(host_audio_queue(ch, pcm.data(), 4096), 4096);
    CHECK_EQ(g_fake_queues, 1);
    host_audio_completed(ch, generation); // the head completes; queued tail continues
    CHECK_EQ(host_audio_is_playing(ch), 1);
    CHECK(host_audio_stream(ch) >= 2048);
    CHECK_EQ(g_fake_plays, 1);
    CHECK_EQ(host_audio_queue(ch, pcm.data(), 4096), 4096);

    // A stopped voice still needs its node scheduled and started.
    host_audio_play(&play);
    host_audio_stop(ch);
    int plays = g_fake_plays, schedules = g_fake_schedules;
    CHECK(host_audio_stream(ch) >= 0);
    CHECK_EQ(g_fake_plays, plays + 1);
    CHECK_EQ(g_fake_schedules, schedules + 1);

    // A completed one-shot is also restarted, even if no is-playing query
    // reconciled its asynchronous completion before the conversion call.
    host_audio_play(&play);
    host_audio_completed(ch, g_fake_last_generation);
    plays = g_fake_plays;
    CHECK(host_audio_stream(ch) >= 0);
    CHECK_EQ(g_fake_plays, plays + 1);
    CHECK_EQ(host_audio_is_playing(ch), 1);
    host_audio_stop(ch);
    CHECK_EQ(host_audio_lock_violations(), 0u);
    host_audio_set_node_ops(nullptr);
}

// What the capture measures, driven by samples a test makes up so the
// measurements can be checked against an answer that is known rather than
// against a recording that has to be listened to.
static void test_audio_capture_metrics() {
    const uint32_t rate = 48000;
    auto sine = [&](std::vector<float> &out, uint32_t frames, float amplitude, uint32_t phase) {
        out.resize(frames);
        for (uint32_t i = 0; i < frames; ++i)
            out[i] =
                amplitude * sinf(2.0f * (float)M_PI * 440.0f * (float)(i + phase) / (float)rate);
    };
    std::vector<float> quiet(9600, 0.0f), tone;

    CHECK_EQ(host_capture_open(nullptr, rate), 1); // measured, not written
    CHECK_EQ(host_capture_active(), 1);

    host_capture_write(quiet.data(), quiet.data(), 4800, 1); // 100 ms, a gap
    sine(tone, 9600, 0.4f, 0);
    host_capture_write(tone.data(), tone.data(), 9600, 1);  // 200 ms of sound
    host_capture_write(quiet.data(), quiet.data(), 480, 1); // 10 ms, a dropout
    sine(tone, 4800, 0.4f, 0);
    host_capture_write(tone.data(), tone.data(), 4800, 1);   // 100 ms of sound
    host_capture_write(quiet.data(), quiet.data(), 9600, 0); // 200 ms, a rest

    HostCaptureStats stats;
    host_capture_close();
    host_capture_stats(&stats);
    CHECK_EQ(stats.rate, rate);
    CHECK_EQ(stats.frames, 29280ull);
    CHECK(stats.seconds > 0.60 && stats.seconds < 0.62);
    // The leading silence is a gap because something was playing; the trailing
    // one is a rest because nothing was.
    CHECK_EQ(stats.silent_gaps, 1u);
    CHECK(stats.longest_gap > 0.09 && stats.longest_gap < 0.11);
    CHECK_EQ(stats.short_dropouts, 1u);
    CHECK_EQ(stats.sound_runs, 2u);
    CHECK(stats.longest_sound > 0.19 && stats.longest_sound < 0.21);
    CHECK(stats.sounding > 0.29 && stats.sounding < 0.31);
    CHECK(stats.peak > 0.39f && stats.peak < 0.41f);
    // A held note passes through zero twice a cycle. Measuring silence sample
    // by sample would have called each of those a break and reported hundreds
    // of stretches instead of two.
    CHECK_EQ(stats.discontinuities, 0u);

    // A click: one jump of more than half of full scale, and only one.
    std::vector<float> high(100, 0.9f), low(100, -0.9f);
    CHECK_EQ(host_capture_open(nullptr, rate), 1);
    host_capture_write(high.data(), high.data(), 100, 1);
    host_capture_write(low.data(), low.data(), 100, 1);
    host_capture_close();
    host_capture_stats(&stats);
    CHECK_EQ(stats.discontinuities, 1u);
    CHECK_EQ(host_capture_active(), 0);
}

// A channel replayed at another format has to be reconnected before anything
// is scheduled on it. The engine converts from whatever rate the node is
// connected at, so a node still connected at 22050 plays an 11025 buffer in
// half the time - the same sound, twice as fast, which is what a wrong pitch
// after a format change sounds like. This plays both on one channel and
// measures how long the output actually lasted.
// The clipper takes the overshoot and nothing else.
//
// Two claims, and the second is the one that matters. A limiter also keeps the
// output under the ceiling; what it also does is pull the whole mix down for a
// while afterwards, which is what a listener heard as squashed and dull. So
// this plays a full-scale sound loud enough to overshoot, checks the output
// came out at or under full scale, and then plays a quiet one straight after
// and checks it arrives at its own level with nothing taken off it.
//
// The overshoot is not manufactured. It is what sample-rate conversion does to
// a signal that is already at the ceiling: the source is 11025 Hz, the device
// is 48000, and interpolating a band-limited waveform between its peaks lands
// above them.
static void test_audio_clipper_takes_only_the_overshoot() {
    // 32000 on purpose. No audio device idles at it, so the rate the engine
    // renders at cannot coincide with the rate a device happens to be in, and
    // the assertion below fails on every machine when the fix is reverted
    // rather than only on the machines whose device sat elsewhere. That
    // coincidence is what made this test flaky rather than failing.
    const double rate = 32000.0;
    if (!host_audio_offline_begin(rate, 4096)) {
        printf("  (this machine has no audio engine, so the clipper is not tested)\n");
        return;
    }

    auto tone = [](std::vector<uint8_t> &pcm, uint32_t hz, double amplitude, double freq,
                   double seconds) {
        uint32_t frames = (uint32_t)(hz * seconds);
        pcm.resize((size_t)frames * 2 * 2); // stereo, 16-bit
        for (uint32_t i = 0; i < frames; ++i) {
            double v = amplitude * sin(2.0 * M_PI * freq * i / hz);
            int32_t rounded = (int32_t)lrint(v * 32767.0);
            if (rounded > 32767)
                rounded = 32767;
            if (rounded < -32768)
                rounded = -32768;
            int16_t sample = (int16_t)rounded;
            for (int c = 0; c < 2; ++c) {
                size_t at = ((size_t)i * 2 + c) * 2;
                pcm[at] = (uint8_t)(sample & 0xff);
                pcm[at + 1] = (uint8_t)((sample >> 8) & 0xff);
            }
        }
    };

    // A quarter of the source rate is four samples a cycle, which is where a
    // resampler overshoots most.
    std::vector<uint8_t> loud, quiet;
    tone(loud, 11025, 1.0, 11025 / 4.0, 0.25);
    tone(quiet, 11025, 0.10, 11025 / 4.0, 0.25);

    HostAudioPlay play;
    memset(&play, 0, sizeof play);
    play.channel = 21;
    play.sample_rate = 11025;
    play.channels = 2;
    play.bits = 16;

    // NOTHING MAY CONVERT AFTER THE CLIPPER, AND THIS IS WHERE THAT IS SAID
    //
    // A hard clip puts a corner in the signal, and a sample-rate conversion
    // applied to a corner rings past it. So the clipper has to run at the rate
    // the engine renders at, and this asserts exactly that rather than
    // inferring it from a peak. It is the assertion the flake needed: with the
    // graph pinned to the audio device's current rate instead of the render
    // rate, this measured 24000 against 48000, the node's own output was
    // exactly 1.0000 and the rendered buffer was 1.0196. Which rate the device
    // happened to be in when the process started decided whether the run
    // passed, so the same build measured 1.0000 and 1.0196 on alternate runs
    // with nothing changed.
    const double clipper_rate = host_audio_clipper_output_rate();
    const double mixer_rate = host_audio_mixer_output_rate();
    const double bus_rate = host_audio_output_bus_rate();
    printf("  (mixer %.0f Hz, clipper %.0f Hz, output bus %.0f Hz, render %.0f Hz)\n", mixer_rate,
           clipper_rate, bus_rate, rate);
    if (clipper_rate > 0.0) { // 0 means no clipper node at all
        CHECK(clipper_rate == rate);
        // The mixer too, or the conversion has merely moved upstream of the
        // clipper's own bus while still sitting inside the chain.
        CHECK(mixer_rate == rate);
        CHECK(bus_rate == rate);
    }

    const uint64_t before = host_audio_clipped_samples();
    play.pcm = loud.data();
    play.bytes = (uint32_t)loud.size();
    host_audio_play(&play);
    float peak = 0.0f;
    // Longer than the sound, so it is over before the quiet one starts and no
    // sample of it can be mistaken for the answer below.
    host_audio_offline_render((uint32_t)(rate * 0.30), &peak);
    const uint64_t after_loud = host_audio_clipped_samples();

    // Nothing leaves the graph above full scale...
    printf("  (loud peak %.4f, %llu samples clipped, worst %.4f)\n", peak,
           (unsigned long long)(after_loud - before), host_audio_worst_overshoot());
    CHECK(peak <= 1.0f);
    CHECK(peak > 0.9f); // and it is not being ducked either
    // ...and the case is real: a run where nothing overshot would prove
    // nothing about the clipper.
    CHECK(after_loud > before);
    CHECK(host_audio_worst_overshoot() > 1.0f);

    // The quiet sound arrives at its own level, straight after the loud one on
    // a channel of its own. A limiter with anything but an instant release
    // would still be holding the mix down here; this has nothing to hold it
    // with.
    play.channel = 22;
    play.pcm = quiet.data();
    play.bytes = (uint32_t)quiet.size();
    host_audio_play(&play);
    peak = 0.0f;
    host_audio_offline_render((uint32_t)(rate * 0.15), &peak);
    printf("  (quiet peak %.4f, wanted about 0.10)\n", peak);
    CHECK(peak > 0.095f);
    CHECK(peak < 0.130f); // room for the resampler's own overshoot
    // And nothing below full scale was touched on the way through.
    CHECK_EQ(host_audio_clipped_samples(), after_loud);

    host_audio_offline_end();
    CHECK_EQ(host_audio_lock_violations(), 0u);
}

// Pinned SDL's actual stream conversion must preserve a six-channel identity
// and retain center/rear/LFE when the endpoint is only stereo.
static void test_audio_sdl_channel_conversion() {
    SDL_AudioSpec input = {SDL_AUDIO_F32, 6, 48000};
    for (int channels : {6, 2, 8}) {
        SDL_AudioSpec output = {SDL_AUDIO_F32, channels, 48000};
        SDL_AudioStream *stream = SDL_CreateAudioStream(&input, &output);
        CHECK(stream != nullptr);
        if (!stream)
            continue;
        for (int active = 0; active < 6; ++active) {
            float samples[6 * 32] = {};
            for (int i = 0; i < 32; ++i)
                samples[i * 6 + active] = 0.25f;
            CHECK(SDL_PutAudioStreamData(stream, samples, sizeof samples));
            CHECK(SDL_FlushAudioStream(stream));
            float result[8 * 32] = {};
            CHECK_EQ(SDL_GetAudioStreamData(stream, result, 32 * channels * sizeof(float)),
                     int(32 * channels * sizeof(float)));
            if (channels == 6) {
                for (int c = 0; c < 6; ++c)
                    CHECK_NEAR(result[16 * 6 + c], c == active ? 0.25f : 0.0f, 1e-6);
            } else if (channels == 2) {
                CHECK(fabsf(result[32]) + fabsf(result[33]) > 0.001f);
            } else {
                // Production SDL conversion, not a detached matrix: dialogue
                // must retain Center and must not be relabeled as LFE in 7.1.
                float energy = 0;
                for (int c = 0; c < 8; ++c)
                    energy += fabsf(result[16 * 8 + c]);
                CHECK(energy > 0.001f);
                if (active == 2 || active == 3) {
                    CHECK(result[16 * 8 + active] > 0.001f);
                    for (int c = 0; c < 8; ++c)
                        if (c != active)
                            CHECK_NEAR(result[16 * 8 + c], 0.0f, 1e-6);
                }
            }
            CHECK(SDL_ClearAudioStream(stream));
        }
        SDL_DestroyAudioStream(stream);
    }
}

// A deliberately reordered input proves that semantic labels, not plane
// indices or a six-channel count, control the output. Invalid layouts fail.
static void test_audio_speaker_layout() {
    using S = audio::Speaker;
    const audio::Layout reordered{
        6, {S::Center, S::LeftSurround, S::Left, S::Right, S::RightSurround, S::Lfe}};
    audio::OutputMap map;
    CHECK(audio::output_map(reordered, map));
    CHECK_EQ(map.count, 6u);
    float values[6] = {0.3f, 0.5f, 0.1f, 0.2f, 0.6f, 0.4f};
    float *planes[6];
    for (unsigned c = 0; c < 6; ++c)
        planes[c] = &values[c];
    float output[6] = {};
    audio::interleave(map, planes, 1, output);
    for (unsigned c = 0; c < 6; ++c)
        CHECK_NEAR(output[c], 0.1f * (c + 1), 1e-6);
    auto bad = reordered;
    bad.speakers[1] = S::Center;
    CHECK(!audio::output_map(bad, map));
    CHECK_EQ(map.count, 0u);
    bad = reordered;
    bad.count = 7;
    CHECK(!audio::output_map(bad, map));
    bad.count = 0;
    CHECK(!audio::output_map(bad, map));
    bad = audio::stereo;
    bad.speakers[1] = S::Center;
    CHECK(!audio::output_map(bad, map));
    CHECK(audio::output_map(audio::Layout{2, {S::Right, S::Left}}, map));
    CHECK_EQ(map.source[0], 1u);
    CHECK_EQ(map.source[1], 0u);
}

// Test53: the presentation applied before interleave - discrete 5.1 with the
// verified centre/LFE exchange, ITU Stereo/Mono folds, saturation, a folded
// two-channel device, and a stereo mix's Mono.
static void test_audio_presentation() {
    auto planes6 = [](float (&v)[6], float **p) {
        for (unsigned c = 0; c < 6; ++c)
            p[c] = &v[c];
    };
    {
        float v[6] = {0.1f, 0.2f, 0.3f, 0.4f, 0.5f, 0.6f};
        float *p[6];
        planes6(v, p);
        CHECK(!audio::present(audio::present_51, 0, false, 6, p, 1));
        CHECK_NEAR(*p[2], 0.3f, 1e-6);
        CHECK(!audio::present(audio::present_51, audio::present_swap_center_lfe, false, 6, p, 1));
        CHECK_NEAR(*p[2], 0.4f, 1e-6); // LFE data now leaves in the centre slot
        CHECK_NEAR(*p[3], 0.3f, 1e-6);
        CHECK_NEAR(*p[0], 0.1f, 1e-6);
        CHECK_NEAR(*p[5], 0.6f, 1e-6);
        planes6(v, p);
        CHECK(!audio::present(audio::present_mixer, 0, false, 6, p, 1));
        CHECK_NEAR(*p[2], 0.3f, 1e-6); // the mixer layout is untouched
    }
    {
        float v[6] = {0.1f, 0.2f, 0.3f, 0.4f, 0.5f, 0.6f};
        float *p[6];
        planes6(v, p);
        CHECK(
            audio::present(audio::present_stereo, audio::present_swap_center_lfe, false, 6, p, 1));
        CHECK_NEAR(v[0], 0.1f + 0.70710678f * (0.3f + 0.5f), 1e-5);
        CHECK_NEAR(v[1], 0.2f + 0.70710678f * (0.3f + 0.6f), 1e-5);
        for (unsigned c = 2; c < 6; ++c)
            CHECK_NEAR(v[c], 0.0f, 1e-6);
    }
    {
        float v[6] = {0.1f, 0.2f, 0.3f, 0.4f, 0.5f, 0.6f};
        float *p[6];
        planes6(v, p);
        CHECK(audio::present(audio::present_mono, 0, false, 6, p, 1));
        const float mono = 0.5f * (0.1f + 0.2f + 0.70710678f * (0.6f + 0.5f + 0.6f));
        CHECK_NEAR(v[0], mono, 1e-5);
        CHECK_NEAR(v[1], mono, 1e-5);
    }
    {
        float v[6] = {1.0f, -1.0f, 1.0f, 0.0f, 1.0f, -1.0f};
        float *p[6];
        planes6(v, p);
        CHECK(audio::present(audio::present_51, 0, true, 6, p, 1)); // 2-channel device folds
        CHECK_NEAR(v[0], 1.0f, 1e-6);                               // saturated, not scaled
        CHECK_NEAR(v[1], -1.0f, 1e-6);
    }
    {
        float l = 0.2f, r = 0.6f;
        float *p[6] = {&l, &r, nullptr, nullptr, nullptr, nullptr};
        CHECK(!audio::present(audio::present_stereo, 0, false, 2, p, 1));
        CHECK_NEAR(l, 0.2f, 1e-6);
        CHECK(!audio::present(audio::present_mono, 0, false, 2, p, 1));
        CHECK_NEAR(l, 0.4f, 1e-6);
        CHECK_NEAR(r, 0.4f, 1e-6);
    }
}

// Dolby Digital output (host/audio/dolby.h, dolby_sink.cpp): the IEC 61937
// burst, the movie bus, and an AC-3 round trip that each discrete role comes
// back in its own channel.
// Test58: an output-transport change requested while a movie output is open
// waits for the last one to close. The platform counts every open output and
// calls the idle callback on the closing thread when the count reaches zero.
static int g_idle_calls = 0;
static int fake_outputs_live = 0;
static void test_audio_outputs_idle() {
#ifdef __APPLE__
    static const OsAudioOutputProvider fake = {
        [](uint32_t, OsAudioLayout) -> void * { ++fake_outputs_live; return &fake_outputs_live; },
        [](void *, const int16_t *, uint32_t) { return 0; },
        [](void *) { return 0; }, [](void *) { return 0; },
        [](void *, float) { return 0; }, [](void *, float *g) { *g = 1; return 0; },
        [](void *, OsAudioClock *) { return 0; }, [](void *) { return 0; },
        [](void *) { --fake_outputs_live; },
        [](void *, OsAudioRoute *) { return 0; }};
    CHECK(os_audio_outputs_open() == 0);
    os_audio_output_set_provider(&fake);
    os_audio_output_set_idle_callback([] { ++g_idle_calls; });
    int err = -1;
    OsAudioOutput *a = os_audio_output_open(48000, OS_AUDIO_51, &err);
    OsAudioOutput *b = os_audio_output_open(48000, OS_AUDIO_STEREO, &err);
    CHECK(a && b && err == 0 && os_audio_outputs_open() == 2 && fake_outputs_live == 2);
    os_audio_output_close(a);
    CHECK(os_audio_outputs_open() == 1 && g_idle_calls == 0);
    os_audio_output_close(b);
    CHECK(os_audio_outputs_open() == 0 && g_idle_calls == 1 && fake_outputs_live == 0);
    os_audio_output_close(nullptr); // no count change, no callback
    CHECK(os_audio_outputs_open() == 0 && g_idle_calls == 1);
    CHECK(!os_audio_output_open(1000, OS_AUDIO_51, &err)); // refused: never counted
    CHECK(os_audio_outputs_open() == 0 && g_idle_calls == 1);
    os_audio_output_set_idle_callback(nullptr);
    os_audio_output_set_provider(nullptr);
#endif
}

static void test_audio_dolby() {
    using namespace audio::dolby;
    {
        std::vector<uint8_t> frame(1792, 0), burst(burst_bytes, 0xAA);
        frame[0] = 0x0B;
        frame[1] = 0x77;
        frame[5] = 0x40 | 3; // bsid bits plus bsmod 3
        for (size_t i = 6; i < frame.size(); ++i)
            frame[i] = uint8_t(i * 7);
        CHECK(wrap_ac3(frame.data(), frame.size(), burst.data()) == burst_bytes);
        auto word = [&](size_t at) { return unsigned(burst[at] | (burst[at + 1] << 8)); };
        CHECK(word(0) == 0xF872 && word(2) == 0x4E1F);
        CHECK(word(4) == (0x0001u | (3u << 8)));
        CHECK(word(6) == 1792u * 8u);
        CHECK(burst[8] == 0x77 && burst[9] == 0x0B); // big-endian words become LE samples
        CHECK(burst[8 + 6] == frame[7] && burst[9 + 6] == frame[6]);
        bool padded = true;
        for (size_t i = 8 + 1792; i < burst_bytes; ++i)
            padded = padded && burst[i] == 0;
        CHECK(padded);
        frame[1] = 0x78;
        CHECK(wrap_ac3(frame.data(), frame.size(), burst.data()) == 0); // not a sync word
        frame[1] = 0x77;
        CHECK(wrap_ac3(frame.data(), burst_bytes, burst.data()) == 0); // cannot fit
        CHECK(wrap_ac3(frame.data(), 7, burst.data()) == burst_bytes); // odd length
        CHECK(burst[8 + 6] == 0 && burst[9 + 6] == frame[6]);
    }
    {
        // A 48 kHz stereo movie: level passes through, the clock trails what
        // was mixed by the sink's lead, backpressure at capacity, reset.
        MovieBus bus(48000, 2);
        std::vector<int16_t> pcm(2048 * 2);
        for (size_t i = 0; i < pcm.size(); i += 2) {
            pcm[i] = 16384;     // L = 0.5
            pcm[i + 1] = -8192; // R = -0.25
        }
        CHECK(bus.start() == -1); // nothing queued yet, like the platform queue
        for (int k = 0; k < 8; ++k)
            CHECK(bus.write(pcm.data(), 2048) == 2048);
        CHECK(bus.write(pcm.data(), 1) == 0); // full: 8 x 2048
        std::vector<float> planes_storage[6];
        float *planes[6];
        for (unsigned c = 0; c < 6; ++c) {
            planes_storage[c].assign(frames_per_burst, 0.0f);
            planes[c] = planes_storage[c].data();
        }
        bus.mix(planes, frames_per_burst); // not started: silent
        CHECK_NEAR(planes[0][100], 0.0f, 1e-6);
        CHECK(bus.start() == 0);
        bus.mix(planes, frames_per_burst);
        CHECK_NEAR(planes[0][100], 0.5f, 1e-3);
        CHECK_NEAR(planes[1][100], -0.25f, 1e-3);
        CHECK_NEAR(planes[2][100], 0.0f, 1e-6);
        OsAudioClock clock{};
        bus.clock(0, 0.0, &clock); // burst 0 (this mix) has not started playing
        CHECK(clock.valid && clock.running && !clock.paused);
        CHECK(clock.enqueued_frames == 8 * 2048);
        CHECK_NEAR(clock.sample_time, 0.0, 1e-9);
        bus.clock(0, 1.0 / 3.0, &clock); // a third of it has played
        CHECK_NEAR(clock.sample_time, 512.0, 1e-6);
        bus.clock(1, 0.0, &clock); // all of it has played
        CHECK_NEAR(clock.sample_time, double(frames_per_burst), 1e-9);
        bus.clock(0, 0.0, &clock); // never moves backwards
        CHECK_NEAR(clock.sample_time, double(frames_per_burst), 1e-9);
        CHECK(clock.returned_frames + 3 >= frames_per_burst &&
              clock.returned_frames <= frames_per_burst);
        CHECK(bus.write(pcm.data(), 1024) == 1024); // space again after consumption
        CHECK(bus.set_gain(0.5f) == 0 && bus.set_gain(2.0f) == -1);
        for (auto &p : planes_storage)
            std::fill(p.begin(), p.end(), 0.0f);
        bus.mix(planes, frames_per_burst, 1);
        CHECK_NEAR(planes[0][100], 0.25f, 1e-3);
        bus.clock(3, 0.0, &clock); // bursts 0 and 1 played; burst 2 had no movie frames
        CHECK_NEAR(clock.sample_time, 2.0 * frames_per_burst, 1e-9);
        CHECK(bus.pause() == 0);
        for (auto &p : planes_storage)
            std::fill(p.begin(), p.end(), 0.0f);
        bus.mix(planes, frames_per_burst, 3);
        CHECK_NEAR(planes[0][100], 0.0f, 1e-6);
        bus.clock(4, 0.5, &clock); // paused: nothing more of this output
        CHECK_NEAR(clock.sample_time, 2.0 * frames_per_burst, 1e-9);
        CHECK(clock.paused && !clock.running);
        CHECK(bus.reset() == 0);
        bus.clock(0, 0.0, &clock);
        CHECK(clock.epoch == 2 && clock.enqueued_frames == 0 && clock.sample_time == 0);
    }
    {
        // A 44.1 kHz 5.1 movie consumes 44100/48000 source frames per output
        // frame; each channel keeps its role; an underrun stops, never repeats.
        MovieBus bus(44100, 6);
        std::vector<int16_t> pcm(2048 * 6, 0);
        for (size_t i = 0; i < 2048; ++i)
            for (unsigned c = 0; c < 6; ++c)
                pcm[i * 6 + c] = int16_t(1000 * (c + 1));
        CHECK(bus.write(pcm.data(), 2048) == 2048);
        CHECK(bus.start() == 0);
        std::vector<float> storage[6];
        float *planes[6];
        for (unsigned c = 0; c < 6; ++c) {
            storage[c].assign(frames_per_burst, 0.0f);
            planes[c] = storage[c].data();
        }
        bus.mix(planes, frames_per_burst);
        for (unsigned c = 0; c < 6; ++c)
            CHECK_NEAR(planes[c][500], 1000.0f * (c + 1) / 32768.0f, 5e-3);
        OsAudioClock clock{};
        bus.clock(1, 0.0, &clock);
        CHECK(std::fabs(double(clock.returned_frames) - 1536.0 * 44100 / 48000) <= 4);
        CHECK_NEAR(clock.sample_time, 1536.0 * 44100 / 48000, 1e-6); // source-rate frames
        for (auto &p : storage)
            std::fill(p.begin(), p.end(), 0.0f);
        bus.mix(planes, frames_per_burst, 1);     // only ~640 source frames remain
        CHECK(std::fabs(planes[0][1535]) < 1e-6); // stopped, not wrapped around
        CHECK(std::fabs(planes[0][600]) > 0.01f); // the tail itself was played
        bus.clock(2, 0.0, &clock);                // a finished tail reports all of it presented
        CHECK(clock.returned_frames == 2048);
        CHECK(clock.sample_time >= 2048.0 && clock.sample_time < 2049.0);
    }
#ifdef RECOMP_HAVE_FFMPEG
    {
        // Encode L R C LFE Ls Rs tones like the sink, decode with FFmpeg's own
        // AC-3 decoder, and check each tone returns in its own channel.
        const AVCodec *enc = avcodec_find_encoder(AV_CODEC_ID_AC3);
        const AVCodec *dec = avcodec_find_decoder(AV_CODEC_ID_AC3);
        CHECK(enc != nullptr && dec != nullptr);
        if (enc && dec) {
            const AVChannelLayout five_one = AV_CHANNEL_LAYOUT_5POINT1;
            AVCodecContext *e = avcodec_alloc_context3(enc), *d = avcodec_alloc_context3(dec);
            e->sample_rate = 48000;
            e->bit_rate = 640000;
            e->sample_fmt = AV_SAMPLE_FMT_FLTP;
            av_channel_layout_copy(&e->ch_layout, &five_one);
            CHECK(avcodec_open2(e, enc, nullptr) == 0 && e->frame_size == 1536);
            d->request_sample_fmt = AV_SAMPLE_FMT_FLTP;
            CHECK(avcodec_open2(d, dec, nullptr) == 0);
            AVFrame *f = av_frame_alloc(), *out = av_frame_alloc();
            AVPacket *pkt = av_packet_alloc();
            for (unsigned role = 0; role < 6; ++role) {
                double energy[8] = {};
                std::vector<uint8_t> burst(burst_bytes);
                for (int n = 0; n < 12; ++n) {
                    f->nb_samples = 1536;
                    f->format = AV_SAMPLE_FMT_FLTP;
                    av_channel_layout_copy(&f->ch_layout, &five_one);
                    CHECK(av_frame_get_buffer(f, 0) == 0);
                    for (unsigned c = 0; c < 6; ++c) {
                        float *p = reinterpret_cast<float *>(f->data[c]);
                        for (int i = 0; i < 1536; ++i)
                            p[i] = c == role
                                       ? 0.3f * float(std::sin(2 * M_PI * (role == 3 ? 50 : 1000) *
                                                               (n * 1536 + i) / 48000.0))
                                       : 0.0f;
                    }
                    CHECK(avcodec_send_frame(e, f) == 0);
                    av_frame_unref(f);
                    while (avcodec_receive_packet(e, pkt) == 0) {
                        CHECK(wrap_ac3(pkt->data, size_t(pkt->size), burst.data()) == burst_bytes);
                        CHECK(avcodec_send_packet(d, pkt) == 0);
                        av_packet_unref(pkt);
                        while (avcodec_receive_frame(d, out) == 0) {
                            const int ch = out->ch_layout.nb_channels;
                            for (int c = 0; c < ch && c < 8 && n > 2; ++c) {
                                const float *p = reinterpret_cast<const float *>(out->data[c]);
                                for (int i = 0; i < out->nb_samples; ++i)
                                    energy[c] += double(p[i]) * p[i];
                            }
                            av_frame_unref(out);
                        }
                    }
                }
                double total = 0;
                for (double v : energy)
                    total += v;
                CHECK(total > 0 && energy[role] / total > 0.99);
            }
            av_packet_free(&pkt);
            av_frame_free(&f);
            av_frame_free(&out);
            avcodec_free_context(&e);
            avcodec_free_context(&d);
        }
    }
#endif
}

// Optional nine-second identification file: one second of modest-level tone,
// then half a second of silence, in L/R/C/LFE/Ls/Rs order. It traverses the
// real offline mixer, the exact sink interleaver, and SDL's six-channel stream.
// No output device is opened. WAVEFORMATEXTENSIBLE declares the speaker mask;
// physical routing remains an owner test with six actual speakers.
static void test_audio_identification(const char *path) {
    constexpr uint32_t rate = 48000, slot = 72000, frames = slot * 6;
    std::vector<int16_t> input(size_t(frames) * 6, 0);
    for (unsigned active = 0; active < 6; ++active) {
        const double hz = active == 3 ? 60.0 : 440.0;
        for (unsigned i = 0; i < rate; ++i) {
            const double fade = std::min(1.0, double(std::min(i, rate - 1 - i)) / 960);
            input[(size_t(active) * slot + i) * 6 + active] =
                int16_t(4000 * fade * sin(2 * M_PI * hz * i / rate));
        }
    }
    CHECK_EQ(host_audio_offline_begin(rate, 4096), 1);
    HostAudioPlay play = {};
    play.channel = 52;
    play.pcm = input.data();
    play.bytes = uint32_t(input.size() * sizeof(int16_t));
    play.sample_rate = rate;
    play.channels = 6;
    play.bits = 16;
    host_audio_play(&play);
    std::vector<float> storage[6];
    float *planes[6];
    for (unsigned c = 0; c < 6; ++c) {
        storage[c].resize(frames);
        planes[c] = storage[c].data();
    }
    CHECK_EQ(host_audio_offline_render_planes(frames, planes), frames);
    host_audio_stop(52);
    host_audio_offline_end();
    audio::OutputMap map;
    CHECK(audio::output_map(audio::surround51, map));
    std::vector<float> interleaved(size_t(frames) * 6), converted(interleaved.size());
    audio::interleave(map, planes, frames, interleaved.data());
    SDL_AudioSpec spec = {SDL_AUDIO_F32, 6, rate};
    SDL_AudioStream *stream = SDL_CreateAudioStream(&spec, &spec);
    CHECK(stream != nullptr);
    if (!stream)
        return;
    const int bytes = int(interleaved.size() * sizeof(float));
    CHECK(SDL_PutAudioStreamData(stream, interleaved.data(), bytes));
    CHECK(SDL_FlushAudioStream(stream));
    CHECK_EQ(SDL_GetAudioStreamData(stream, converted.data(), bytes), bytes);
    SDL_DestroyAudioStream(stream);
    for (unsigned active = 0; active < 6; ++active) {
        for (unsigned c = 0; c < 6; ++c) {
            double energy = 0;
            for (unsigned i = 0; i < slot; ++i) {
                const float value = converted[(size_t(active) * slot + i) * 6 + c];
                energy += value * value;
            }
            if (c == active)
                CHECK(energy > 100.0);
            else
                CHECK(energy < 1e-10);
            printf("speaker-ID %s -> %s energy %.6f\n",
                   audio::speaker_name(audio::surround51.speakers[active]),
                   audio::speaker_name(audio::surround51.speakers[c]), energy);
        }
    }
    std::ofstream wav(path, std::ios::binary);
    CHECK(bool(wav));
    if (!wav)
        return;
    auto u16 = [&](uint16_t v) {
        wav.put(char(v));
        wav.put(char(v >> 8));
    };
    auto u32 = [&](uint32_t v) {
        u16(uint16_t(v));
        u16(uint16_t(v >> 16));
    };
    const uint32_t data_bytes = uint32_t(converted.size() * 2);
    wav.write("RIFF", 4);
    u32(60 + data_bytes);
    wav.write("WAVEfmt ", 8);
    u32(40);
    u16(0xfffe);
    u16(6);
    u32(rate);
    u32(rate * 12);
    u16(12);
    u16(16);
    u16(22);
    u16(16);
    u32(0x3f); // FL/FR/FC/LFE/BL/BR
    const uint8_t pcm_guid[16] = {1, 0, 0, 0, 0, 0, 0x10, 0, 0x80, 0, 0, 0xaa, 0, 0x38, 0x9b, 0x71};
    wav.write(reinterpret_cast<const char *>(pcm_guid), 16);
    wav.write("data", 4);
    u32(data_bytes);
    for (float sample : converted)
        u16(uint16_t(int16_t(lroundf(sample * 32768.0f))));
    wav.close();
    CHECK(bool(wav));
    printf("speaker-ID saved: %s (L 0s, R 1.5s, C 3s, LFE 4.5s, Ls 6s, Rs 7.5s)\n", path);
}

static void test_audio_silent_device() {
    // Exercise the same sink replacement used when a game first emits discrete
    // audio. Diagnostic evidence must survive the stereo -> six-channel change.
    for (const auto &layout : {audio::stereo, audio::surround51}) {
        auto sink = make_sdl_sink();
        std::atomic<unsigned> pulls{0};
        CHECK(sink->start(48000, layout, [&](float *const *out, uint32_t frames) {
            for (unsigned c = 0; c < 6; ++c)
                std::fill(out[c], out[c] + frames, 0.0f);
            pulls.fetch_add(1, std::memory_order_relaxed);
        }));
        CHECK(sink->running());
        os_sleep_us(500000);
        sink->stop();
        CHECK(!sink->running());
        CHECK(pulls.load() > 0);
        printf("silent %u-channel source: %u device pulls; physical speaker layout is not "
               "established\n",
               layout.count, pulls.load());
    }
}

// Pure fixtures: meters neither mutate data nor infer roles for unknown counts;
// bounded storage, nonfinite samples and rate/layout transitions stay explicit.
static void test_audio_diagnostics() {
    using namespace audio::diagnostic;
    Meter meter;
    const float data[] = {0.5f, -0.25f, -0.5f, 0.25f};
    meter.observe(data, 2, 2, 4);
    CHECK_EQ(meter.count, 1u);
    CHECK_EQ(meter.windows[0].frames, 2u);
    CHECK_NEAR(meter.windows[0].peak[0], .5, 0);
    CHECK_NEAR(meter.windows[0].square_sum[1], .125, 0);
    CHECK_EQ(data[0], .5f);
    meter.observe(data, 2, 2, 4);
    meter.observe(data, 2, 2, 8);
    CHECK_EQ(meter.count, 2u);
    CHECK_EQ(meter.windows[1].rate, 8u);
    const float bad[] = {NAN, INFINITY};
    meter.observe(bad, 1, 2, 8);
    CHECK_EQ(meter.windows[1].nonfinite[0], 1u);
    CHECK_EQ(meter.windows[1].nonfinite[1], 1u);
    meter.observe(nullptr, 9, 0, 0);
    CHECK_EQ(meter.omitted_frames, 9u);
    Meter bounded;
    for (unsigned i = 0; i < max_windows + 3; ++i)
        bounded.observe(data, 1, 2, 1);
    CHECK_EQ(bounded.count, max_windows);
    CHECK_EQ(bounded.omitted_frames, 3u);
    CHECK(strcmp(sdl_role(6, 4), "SL") == 0);
    CHECK(strcmp(sdl_role(8, 4), "RL") == 0);
    CHECK(strcmp(sdl_role(8, 6), "SL") == 0);
    CHECK(strcmp(sdl_role(8, -1), "UNKNOWN") == 0);
    CHECK(strcmp(sdl_role(3, 0), "UNKNOWN") == 0);
    CHECK(strcmp(sdl_role(6, 7), "UNKNOWN") == 0);
    std::vector<float> slot(size_t(test_slot_frames) * 8);
    for (unsigned sequence = 0; sequence < 8; ++sequence) {
        channel_test(slot.data(), sequence * test_slot_frames, test_slot_frames);
        double energy[8] = {};
        float peak = 0;
        bool silent_tail = true;
        for (unsigned i = 0; i < test_slot_frames; ++i)
            for (unsigned c = 0; c < 8; ++c) {
                const float sample = slot[size_t(i) * 8 + c];
                energy[c] += sample * sample;
                peak = std::max(peak, fabsf(sample));
                if (i >= test_rate && sample != 0)
                    silent_tail = false;
            }
        for (unsigned c = 0; c < 8; ++c)
            CHECK(c == test_slots[sequence] ? energy[c] > 100 : energy[c] == 0);
        CHECK(peak <= .080001f);
        CHECK(silent_tail);
    }
    channel_test(slot.data(), test_frames, test_slot_frames);
    CHECK(std::all_of(slot.begin(), slot.end(), [](float f) { return f == 0; }));
    // Reporting uses the supplied backend map, never guessed endpoint indices.
    FILE *report = tmpfile();
    CHECK(report != nullptr);
    if (report) {
        meter.report(report, "test", 2, {1, 0, 2, 3, 4, 5, 6, 7});
        rewind(report);
        char text[4096] = {};
        const size_t read = fread(text, 1, sizeof(text) - 1, report);
        CHECK(read > 0);
        CHECK(strstr(text, "\"slot\":0,\"role\":\"FR\"") != nullptr);
        CHECK(strstr(text, "\"nonfinite\":1") != nullptr);
        fclose(report);
    }
}

// Explicit device probe only. It never starts the game, opens profiles or changes
// system configuration. The modest tone has a20ms fade and stops after12seconds.
// SDL/CoreAudio may still convert the configured queue to the physical endpoint;
// an eight-channel endpoint snapshot and an owner's speaker report are required.
static void test_audio_channel_device(const char *path) {
    using namespace audio::diagnostic;
    FILE *file = fopen(path, "wx");
    CHECK(file != nullptr);
    if (!file)
        return;
    if (!SDL_InitSubSystem(SDL_INIT_AUDIO)) {
        CHECK(false);
        fclose(file);
        return;
    }
    SDL_AudioSpec spec{SDL_AUDIO_F32, 8, test_rate};
    SDL_AudioStream *stream =
        SDL_OpenAudioDeviceStream(SDL_AUDIO_DEVICE_DEFAULT_PLAYBACK, &spec, nullptr, nullptr);
    CHECK(stream != nullptr);
    if (!stream) {
        fprintf(stderr, "channel-test open failed: %s\n", SDL_GetError());
        fclose(file);
        SDL_QuitSubSystem(SDL_INIT_AUDIO);
        return;
    }
    const auto device = SDL_GetAudioStreamDevice(stream);
    SDL_AudioSpec actual{};
    int period = 0;
    const bool configured = SDL_GetAudioDeviceFormat(device, &actual, &period);
    CHECK(configured && actual.channels == 8 && actual.freq == int(test_rate));
    fprintf(file,
            "{\"diagnostic\":\"audio-channel-test\",\"requested_channels\":8,"
            "\"requested_rate\":48000,\"configured_channels\":%d,\"configured_rate\":%d,"
            "\"configured_sdl_format\":%u,\"physical_routing_verified\":false}\n",
            actual.channels, actual.freq, unsigned(actual.format));
    struct Probe {
        Meter meter;
        std::atomic<uint64_t> frames{0};
    } probe;
    std::array<int, max_channels> map{0, 1, 2, 3, 4, 5, 6, 7};
    int map_count = 0;
    int *device_map = SDL_GetAudioDeviceChannelMap(device, &map_count);
    if (device_map) {
        for (unsigned i = 0; i < max_channels; ++i)
            map[i] = int(i) < map_count ? device_map[i] : -1;
        SDL_free(device_map);
    }
    const bool callback = SDL_SetAudioPostmixCallback(
        device,
        [](void *userdata, const SDL_AudioSpec *s, float *samples, int bytes) {
            auto *p = static_cast<Probe *>(userdata);
            if (s->channels <= 0 || s->freq <= 0 || bytes < 0)
                return;
            const unsigned frames = unsigned(bytes) / (unsigned(s->channels) * sizeof(float));
            p->meter.observe(samples, frames, unsigned(s->channels), unsigned(s->freq));
            p->frames.fetch_add(frames, std::memory_order_release);
        },
        &probe);
    CHECK(callback);
    if (configured && actual.channels == 8 && actual.freq == int(test_rate) && callback) {
        std::vector<float> samples(size_t(test_frames) * 8);
        channel_test(samples.data(), 0, test_frames);
        const bool queued = SDL_PutAudioStreamData(stream, samples.data(), int(samples.size() * 4));
        const bool flushed = queued && SDL_FlushAudioStream(stream);
        const bool resumed = flushed && SDL_ResumeAudioStreamDevice(stream);
        CHECK(queued && flushed && resumed);
        const uint64_t start = SDL_GetTicksNS();
        unsigned next = 0;
        while (resumed && probe.frames.load(std::memory_order_acquire) < test_frames + test_rate &&
               SDL_GetTicksNS() - start < 16000000000ull) {
            const uint64_t submitted = probe.frames.load(std::memory_order_acquire);
            while (next < 8 && submitted >= uint64_t(next) * test_slot_frames) {
                const char *role = sdl_role(8, int(test_slots[next]));
                printf("audio-channel-test: %s (scheduled SDL output; physical playback not "
                       "measured)\n",
                       role);
                fprintf(file, "{\"active_logical_channel\":\"%s\",\"start_sample\":%u}\n", role,
                        next * test_slot_frames);
                fflush(stdout);
                ++next;
            }
            SDL_Delay(10);
        }
        CHECK(probe.frames.load() >= test_frames);
    }
    SDL_SetAudioPostmixCallback(device, nullptr, nullptr);
    SDL_DestroyAudioStream(stream);
    probe.meter.report(file, "SDL-postmix-before-backend", unsigned(actual.channels), map);
    CHECK(fclose(file) == 0);
    SDL_QuitSubSystem(SDL_INIT_AUDIO);
}

// Six independent values expose channel swaps/leaks and the old mono stride
// collapse. Streaming must keep the same planes and byte-accurate cursor.
static void test_audio_six_channel_planes() {
    CHECK_EQ(host_audio_offline_begin(48000, 4096), 1);
    constexpr uint32_t n = 480;
    std::vector<uint8_t> pcm(n * 12);
    auto fill = [&](int sign) {
        for (uint32_t i = 0; i < n; ++i)
            for (unsigned c = 0; c < 6; ++c) {
                int16_t v = int16_t(sign * int(c + 1) * 1000);
                pcm[i * 12 + c * 2] = uint8_t(v);
                pcm[i * 12 + c * 2 + 1] = uint8_t(uint16_t(v) >> 8);
            }
    };
    fill(1);
    std::vector<float> planes[6];
    float *out[6];
    for (unsigned c = 0; c < 6; ++c) {
        planes[c].resize(n);
        out[c] = planes[c].data();
    }
    CHECK_EQ(host_audio_decode_planes(pcm.data(), 13, 16, 6, out, n), 1u);
    for (unsigned c = 0; c < 6; ++c)
        CHECK_NEAR(out[c][0], float((c + 1) * 1000) / 32768, 1e-6);
    CHECK_EQ(host_audio_decode_planes(pcm.data(), 11, 16, 6, out, n), 0u);
    CHECK_EQ(host_audio_decode_planes(pcm.data(), 12, 16, 3, out, n), 0u);

    HostAudioPlay play = {};
    play.channel = 51;
    play.pcm = pcm.data();
    play.bytes = uint32_t(pcm.size());
    play.sample_rate = 48000;
    play.channels = 6;
    play.bits = 16;
    play.loop = 1;
    host_audio_play(&play);
    CHECK_EQ(host_audio_offline_render_planes(n / 2, out), n / 2);
    CHECK_EQ(host_audio_position(51), n / 2 * 12);
    for (unsigned c = 0; c < 6; ++c)
        CHECK_NEAR(out[c][100], float((c + 1) * 1000) / 32768, 1e-6);
    // First write creates an in-place ring; second write updates all planes.
    CHECK_EQ(host_audio_write(51, pcm.data(), 0, uint32_t(pcm.size())), int32_t(pcm.size()));
    fill(-1);
    CHECK_EQ(host_audio_write(51, pcm.data(), 0, uint32_t(pcm.size())), int32_t(pcm.size()));
    CHECK_EQ(host_audio_offline_render_planes(n, out), n);
    for (unsigned c = 0; c < 6; ++c)
        CHECK_NEAR(out[c][300], -float((c + 1) * 1000) / 32768, 1e-6);
    host_audio_stop(51);
    CHECK_EQ(host_audio_offline_render_planes(n, out), n);
    for (unsigned c = 0; c < 6; ++c)
        CHECK_NEAR(out[c][100], 0, 1e-6);

    // Nonlooping queue retains C/LFE/rears across the segment boundary.
    fill(1);
    play.loop = 0;
    host_audio_play(&play);
    fill(-1);
    CHECK_EQ(host_audio_queue(51, pcm.data(), uint32_t(pcm.size())), int32_t(pcm.size()));
    CHECK_EQ(host_audio_offline_render_planes(n, out), n);
    CHECK_EQ(host_audio_offline_render_planes(n, out), n);
    for (unsigned c = 0; c < 6; ++c)
        CHECK_NEAR(out[c][100], -float((c + 1) * 1000) / 32768, 1e-6);
    host_audio_stop(51);

    // The game supplies 44.1 kHz while the host renders 48 kHz. All planes
    // must resample together and retain their levels through volume changes.
    fill(1);
    play.loop = 1;
    play.sample_rate = 44100;
    host_audio_play(&play);
    host_audio_set_volume(51, -600);
    CHECK_EQ(host_audio_offline_render_planes(n / 2, out), n / 2);
    const float gain = host_audio_gain_from_millibels(-600);
    for (unsigned c = 0; c < 6; ++c) {
        const float expected = gain * float((c + 1) * 1000) / 32768;
        // The existing windowed-sinc kernel has small fractional-phase DC
        // ripple. Check 0.2% level accuracy and much tighter inter-plane ratios.
        CHECK_NEAR(out[c][100], expected, expected * 0.002f);
        CHECK_NEAR(out[c][100], out[0][100] * float(c + 1), 1e-6);
    }
    CHECK_EQ(host_audio_position(51), 220u * 12u);
    host_audio_stop(51);
    host_audio_set_volume(51, 0);

    // Format reuse must retire rear-plane samples; ordinary stereo stays exact.
    const uint8_t stereo[4] = {0, 0x20, 0, 0x40};
    play.pcm = stereo;
    play.bytes = 4;
    play.channels = 2;
    play.sample_rate = 48000;
    play.loop = 1;
    host_audio_play(&play);
    CHECK_EQ(host_audio_offline_render_planes(n, out), n);
    CHECK_NEAR(out[0][100], 0.25, 1e-6);
    CHECK_NEAR(out[1][100], 0.5, 1e-6);
    for (unsigned c = 2; c < 6; ++c)
        CHECK_NEAR(out[c][100], 0, 1e-6);
    host_audio_stop(51);
    host_audio_offline_end();
    CHECK_EQ(host_audio_lock_violations(), 0u);
}

// Test50/A06: SND restarts in process (5.1 -> Mono -> Stereo -> 5.1) replace the
// game's output ring with a 6-, 1-, 2- and 6-channel buffer while the host
// output stays six-channel. Each format must land on its roles: discrete
// planes exact, mono on both fronts, stereo on FL/FR, nothing stale elsewhere.
static std::vector<uint8_t> snd_ring(unsigned channels, uint32_t frames) {
    std::vector<uint8_t> pcm(size_t(frames) * channels * 2);
    for (uint32_t i = 0; i < frames; ++i)
        for (unsigned c = 0; c < channels; ++c) {
            const int16_t v = int16_t((channels == 6 ? int(c + 1) : int(c + 2)) * 1500);
            pcm[(size_t(i) * channels + c) * 2] = uint8_t(v);
            pcm[(size_t(i) * channels + c) * 2 + 1] = uint8_t(uint16_t(v) >> 8);
        }
    return pcm;
}

static void test_audio_snd_mode_sequence() {
    CHECK_EQ(host_audio_offline_begin(48000, 4096), 1);
    constexpr uint32_t n = 960;
    std::vector<float> planes[6];
    float *out[6];
    for (unsigned c = 0; c < 6; ++c) {
        planes[c].resize(n);
        out[c] = planes[c].data();
    }
    HostAudioPlay play = {};
    play.channel = 52;
    play.sample_rate = 48000;
    play.bits = 16;
    play.loop = 1;
    for (unsigned channels : {6u, 1u, 2u, 6u, 2u, 1u}) {
        const auto pcm = snd_ring(channels, n);
        play.pcm = pcm.data();
        play.bytes = uint32_t(pcm.size());
        play.channels = int(channels);
        host_audio_stop(52); // SND releases its old ring before the new one plays
        host_audio_play(&play);
        CHECK_EQ(host_audio_offline_render_planes(n, out), n);
        CHECK_EQ(host_audio_offline_render_planes(n, out), n);
        for (unsigned c = 0; c < 6; ++c) {
            float expected = 0;
            if (channels == 6)
                expected = float((c + 1) * 1500) / 32768;
            else if (channels == 1 && c < 2)
                expected = 2 * 1500.0f / 32768;
            else if (channels == 2 && c < 2)
                expected = float((c + 2) * 1500) / 32768;
            CHECK_NEAR(out[c][500], expected, 1e-6);
        }
    }
    host_audio_stop(52);
    host_audio_offline_end();
    CHECK_EQ(host_audio_lock_violations(), 0u);
}

// Manual (--audio-device-modes DIR): the same sequence through the LIVE mixer,
// the production SDL sink and the real default output device. SDL-postmix
// meters (after conversion to the device's actual format) are written to DIR
// and the device is silenced right after metering, so nothing is audible.
static void test_audio_device_modes(const char *dir) {
    const std::string evidence = std::string(dir) + "/postmix.jsonl";
    os_setenv("RECOMP_AUDIO_DIAGNOSTICS", evidence.c_str());
    os_setenv("RECOMP_AUDIO_DIAGNOSTICS_MUTE", "1");
    FILE *phases = fopen((std::string(dir) + "/phases.jsonl").c_str(), "wx");
    CHECK(phases != nullptr);
    if (!phases)
        return;
    HostAudioPlay play = {};
    play.channel = 53;
    play.sample_rate = 48000;
    play.bits = 16;
    play.loop = 1;
    const uint64_t t0 = os_monotonic_ns();
    for (unsigned channels : {6u, 1u, 2u, 6u, 2u, 1u, 6u}) {
        const auto pcm = snd_ring(channels, 4800);
        play.pcm = pcm.data();
        play.bytes = uint32_t(pcm.size());
        play.channels = int(channels);
        host_audio_stop(53);
        const uint64_t at = os_monotonic_ns();
        host_audio_play(&play);
        fprintf(phases, "{\"channels\":%u,\"start_ms\":%.1f}\n", channels, (at - t0) / 1e6);
        os_sleep_us(3000000);
    }
    host_audio_stop(53);
    os_sleep_us(500000);
    host_audio_shutdown();
    fclose(phases);
    os_unsetenv("RECOMP_AUDIO_DIAGNOSTICS_MUTE");
    os_unsetenv("RECOMP_AUDIO_DIAGNOSTICS");
    FILE *written = fopen(evidence.c_str(), "r");
    CHECK(written != nullptr);
    if (written)
        fclose(written);
}

static void test_audio_format_change() {
    const double rate = 48000.0;
    if (!host_audio_offline_begin(rate, 4096)) {
        printf("  (this machine has no audio engine, so the rate change is not tested)\n");
        return;
    }
    CHECK_EQ(host_capture_open(nullptr, (uint32_t)rate), 1);

    auto tone = [](std::vector<uint8_t> &pcm, uint32_t hz, int channels, double seconds) {
        uint32_t frames = (uint32_t)(hz * seconds);
        pcm.resize((size_t)frames * channels * 2);
        for (uint32_t i = 0; i < frames; ++i) {
            double v = 0.5 * sin(2.0 * M_PI * 440.0 * i / hz);
            int16_t sample = (int16_t)(v * 32000.0);
            for (int c = 0; c < channels; ++c) {
                size_t at = ((size_t)i * channels + c) * 2;
                pcm[at] = (uint8_t)(sample & 0xff);
                pcm[at + 1] = (uint8_t)((sample >> 8) & 0xff);
            }
        }
    };

    std::vector<uint8_t> stereo, mono;
    tone(stereo, 22050, 2, 0.20);
    tone(mono, 11025, 1, 0.30);

    HostAudioPlay play;
    memset(&play, 0, sizeof play);
    play.channel = 9;
    play.pcm = stereo.data();
    play.bytes = (uint32_t)stereo.size();
    play.sample_rate = 22050;
    play.channels = 2;
    play.bits = 16;
    host_audio_play(&play);
    float peak = 0.0f;
    host_audio_offline_render((uint32_t)(rate * 0.25), &peak);
    CHECK(peak > 0.0f);

    // The same channel, at half the rate and half the channels.
    play.pcm = mono.data();
    play.bytes = (uint32_t)mono.size();
    play.sample_rate = 11025;
    play.channels = 1;
    host_audio_play(&play);
    host_audio_offline_render((uint32_t)(rate * 0.35), &peak);
    CHECK(peak > 0.0f);

    HostCaptureStats stats;
    host_capture_stats(&stats);
    host_capture_close();
    host_audio_offline_end();

    // Half a second of sound, give or take the block the render ends on. A
    // node left connected at 22050 would have played the second tone in
    // 0.15s and the total would be nearer 0.35.
    printf("  (%.3fs of sound over %.3fs captured)\n", stats.sounding, stats.seconds);
    CHECK(stats.sounding > 0.46);
    CHECK(stats.sounding < 0.54);
    CHECK_EQ(host_audio_lock_violations(), 0u);
}

// A ring written where it lies, and a queue that reports from the clock. Both
// are about the same thing: a streamed sound must not go quiet at the seam
// between one lap and the next, and the capture is what says whether it did.
static void test_audio_ring_and_clock() {
    const double rate = 48000.0;
    if (!host_audio_offline_begin(rate, 4096)) {
        printf("  (this machine has no audio engine, so the ring is not tested)\n");
        return;
    }
    const uint32_t hz = 22050, ring_bytes = 8820 * 4; // 0.2 s, stereo 16-bit
    std::vector<uint8_t> ring(ring_bytes);
    auto fill = [&](double freq) {
        uint32_t frames = ring_bytes / 4;
        for (uint32_t i = 0; i < frames; ++i) {
            int16_t v = (int16_t)(12000.0 * sin(2.0 * M_PI * freq * i / hz));
            for (int c = 0; c < 2; ++c) {
                ring[(size_t)(i * 2 + c) * 2] = (uint8_t)(v & 0xff);
                ring[(size_t)(i * 2 + c) * 2 + 1] = (uint8_t)((v >> 8) & 0xff);
            }
        }
    };
    fill(440.0);

    HostAudioPlay play;
    memset(&play, 0, sizeof play);
    play.channel = 21;
    play.pcm = ring.data();
    play.bytes = ring_bytes;
    play.sample_rate = (int32_t)hz;
    play.channels = 2;
    play.bits = 16;
    play.loop = 1;
    host_audio_play(&play);

    CHECK_EQ(host_capture_open(nullptr, (uint32_t)rate), 1);
    float peak = 0.0f;
    host_audio_offline_render(4800, &peak); // 100 ms of the loop
    CHECK(peak > 0.0f);

    // Now rewrite the ring under the playing node, a lap at a time, the way a
    // guest rewrites a DirectSound streaming buffer. Nothing may stop.
    for (int lap = 0; lap < 8; ++lap) {
        fill(lap % 2 ? 660.0 : 440.0);
        CHECK_EQ(host_audio_write(21, ring.data(), 0, ring_bytes), (int32_t)ring_bytes);
        host_audio_offline_render(9600, &peak); // 200 ms, one lap
        CHECK(peak > 0.0f);
    }

    HostCaptureStats stats;
    host_capture_stats(&stats);
    host_capture_close();
    // Not one silent window in the whole thing. A lap boundary that stops and
    // reschedules would show as a hole here, which is exactly how the 25 ms
    // holes in the intro were found.
    printf("  (%.2fs captured, %.2fs of sound, %u dropouts, %u gaps)\n", stats.seconds,
           stats.sounding, stats.short_dropouts, stats.silent_gaps);
    CHECK_EQ(stats.short_dropouts, 0u);
    CHECK_EQ(stats.silent_gaps, 0u);
    CHECK_EQ(stats.sound_runs, 1u);
    CHECK_EQ(stats.discontinuities, 0u);

    // And the queue's own answer comes from the clock, not from a completion.
    // A stream that has been playing for a while reports less outstanding than
    // it was handed, without anything having reported a buffer finished.
    play.channel = 22;
    play.loop = 1;
    host_audio_play(&play);
    CHECK(host_audio_stream(22) >= 0);
    CHECK_EQ(host_audio_queue(22, ring.data(), ring_bytes), (int32_t)ring_bytes);
    uint32_t before = host_audio_queued_bytes(22);
    host_audio_offline_render(4800, &peak); // 100 ms goes by
    uint32_t after = host_audio_queued_bytes(22);
    CHECK(before > 0);
    CHECK(after < before);
    host_audio_stop(21);
    host_audio_stop(22);
    host_audio_offline_end();
    CHECK_EQ(host_audio_lock_violations(), 0u);
}

// One channel, two sounds, the way QMixer replaces: play, convert to a stream,
// and 50 ms later do it again with a different sound on the same channel.
//
// The game does this 781 times in a live run, 615 of them on one channel, and
// the report from that run was that most of the effects were not heard. The
// two questions this asks are whether the second sound is audible from its
// own onset and whether the first is really gone afterwards.
static void test_audio_replace_on_one_channel() {
    const double rate = 48000.0;
    if (!host_audio_offline_begin(rate, 4096)) {
        printf("  (this machine has no audio engine, so replacement is not tested)\n");
        return;
    }
    const uint32_t hz = 22050;
    // Two tones far enough apart that counting zero crossings says which is
    // playing without any doubt.
    auto tone = [&](std::vector<uint8_t> &pcm, double freq, double seconds) {
        uint32_t frames = (uint32_t)(hz * seconds);
        pcm.resize((size_t)frames * 4);
        for (uint32_t i = 0; i < frames; ++i) {
            int16_t v = (int16_t)(11000.0 * sin(2.0 * M_PI * freq * i / hz));
            for (int c = 0; c < 2; ++c) {
                pcm[(size_t)(i * 2 + c) * 2] = (uint8_t)(v & 0xff);
                pcm[(size_t)(i * 2 + c) * 2 + 1] = (uint8_t)((v >> 8) & 0xff);
            }
        }
    };
    std::vector<uint8_t> low, high;
    tone(low, 300.0, 1.0);
    tone(high, 3000.0, 1.0);

    const int32_t CH = 30;
    auto play = [&](std::vector<uint8_t> &pcm) {
        HostAudioPlay p;
        memset(&p, 0, sizeof p);
        p.channel = CH;
        p.pcm = pcm.data();
        p.bytes = (uint32_t)pcm.size();
        p.sample_rate = (int32_t)hz;
        p.channels = 2;
        p.bits = 16;
        host_audio_play(&p);
        // Exactly what the QMixer shim does with a streamed wave.
        host_audio_stream(CH);
    };

    // Renders `ms` and reports the peak and how many times the left channel
    // crossed zero, which is the tone's identity.
    auto listen = [&](double ms, float *peak) {
        host_capture_open(nullptr, (uint32_t)rate);
        host_audio_offline_render((uint32_t)(rate * ms / 1000.0), peak);
        HostCaptureStats st;
        host_capture_stats(&st);
        host_capture_close();
        return st;
    };

    play(low);
    float peak = 0.0f;
    HostCaptureStats first = listen(50.0, &peak);
    printf("  (A: peak %.3f over %.3fs, %.2fs of it sounding)\n", peak, first.seconds,
           first.sounding);
    CHECK(peak > 0.05f); // A is audible from its onset

    play(high);
    HostCaptureStats second = listen(200.0, &peak);
    printf("  (B: peak %.3f over %.3fs, %.2fs of it sounding)\n", peak, second.seconds,
           second.sounding);
    CHECK(peak > 0.05f); // B is audible from ITS onset
    // And it really is B: nearly every window of the second block has sound in
    // it, so the channel did not go quiet when it was replaced.
    CHECK(second.sounding > second.seconds * 0.9);

    host_audio_stop(CH);
    host_audio_offline_end();
}

// Every sound the host is told to start is heard where it starts.
//
// A live run played 781 sounds, 615 of them on one channel, and what came back
// was that most of the effects were not there. The counters on both sides said
// everything had been delivered, which is the trouble with counters: they
// count calls, not audio. So the capture now stamps every play against its own
// clock and afterwards looks for energy where each one began, and this drives
// the two cases that matter through it.
static void test_audio_every_sound_started_is_heard() {
    const double rate = 48000.0;
    if (!host_audio_offline_begin(rate, 4096)) {
        printf("  (this machine has no audio engine, so this is not tested)\n");
        return;
    }
    const uint32_t hz = 22050;
    auto tone = [&](std::vector<uint8_t> &pcm, double freq, double seconds, double amp) {
        uint32_t frames = (uint32_t)(hz * seconds);
        pcm.resize((size_t)frames * 4);
        for (uint32_t i = 0; i < frames; ++i) {
            int16_t v = (int16_t)(amp * 32000.0 * sin(2.0 * M_PI * freq * i / hz));
            for (int c = 0; c < 2; ++c) {
                pcm[(size_t)(i * 2 + c) * 2] = (uint8_t)(v & 0xff);
                pcm[(size_t)(i * 2 + c) * 2 + 1] = (uint8_t)((v >> 8) & 0xff);
            }
        }
    };
    auto play = [&](int32_t ch, std::vector<uint8_t> &pcm, int loop) {
        HostAudioPlay p;
        memset(&p, 0, sizeof p);
        p.channel = ch;
        p.pcm = pcm.data();
        p.bytes = (uint32_t)pcm.size();
        p.sample_rate = (int32_t)hz;
        p.channels = 2;
        p.bits = 16;
        p.loop = loop;
        host_audio_play(&p);
    };

    // The live pattern: a 348 ms effect started every 180 ms on one channel,
    // each converted to a stream the way the QMixer shim converts one, with
    // music running beside it so the mix is never empty.
    std::vector<uint8_t> music, effect;
    tone(music, 220.0, 4.0, 0.6);
    tone(effect, 1500.0, 0.348, 0.9);
    float peak = 0.0f;
    host_capture_open(nullptr, (uint32_t)rate);
    play(40, music, 1);
    for (int i = 0; i < 20; ++i) {
        play(41, effect, 0);
        host_audio_stream(41);
        host_audio_offline_render((uint32_t)(rate * 0.180), &peak);
    }
    HostCaptureStats st;
    host_capture_stats(&st);
    host_capture_close();
    printf("  (%u sounds started, %u absent, %u cut short, %.2fs of %.2fs sounding)\n",
           st.plays_noted, st.plays_absent, st.plays_cut_short, st.sounding, st.seconds);
    CHECK_EQ(st.plays_noted, 21u); // the music and the twenty
    CHECK_EQ(st.plays_absent, 0u); // every one of them was heard
    CHECK(st.sounding > st.seconds * 0.9);
    host_audio_stop(40);
    host_audio_stop(41);

    // And the instrument is not simply agreeing: a sound stopped before the
    // mix ever renders it is found absent. The graph is drained first, or the
    // tail of the sounds above would be the energy it finds.
    host_audio_offline_render((uint32_t)(rate * 0.4), &peak);
    host_capture_open(nullptr, (uint32_t)rate);
    host_audio_offline_render((uint32_t)(rate * 0.05), &peak);
    play(42, effect, 0);
    host_audio_stop(42);
    host_audio_offline_render((uint32_t)(rate * 0.2), &peak);
    host_capture_stats(&st);
    host_capture_close();
    CHECK_EQ(st.plays_noted, 1u);
    CHECK_EQ(st.plays_absent, 1u);
    host_audio_offline_end();
}

// The two remaining-bytes questions, which are different questions.
//
// A voice pacing its own refills wants to know what the VOICE has left; a
// ring's writer wants to know what IT appended that has not played. A play
// resets the second and not the first, and the live symptom of asking the
// wrong one was a shim told "queued 0" thirty milliseconds into a
// second-and-a-half sound, refilling immediately, 635 times in one run.
static void test_audio_voice_remaining_versus_queued() {
    const double rate = 48000.0;
    if (!host_audio_offline_begin(rate, 4096)) {
        printf("  (this machine has no audio engine, so this is not tested)\n");
        return;
    }
    const uint32_t hz = 11025; // the rate the game's waves use
    auto tone = [&](std::vector<uint8_t> &pcm, double freq, double seconds) {
        uint32_t frames = (uint32_t)(hz * seconds);
        pcm.resize((size_t)frames * 2); // mono, as those waves are
        for (uint32_t i = 0; i < frames; ++i) {
            int16_t v = (int16_t)(10000.0 * sin(2.0 * M_PI * freq * i / hz));
            pcm[(size_t)i * 2] = (uint8_t)(v & 0xff);
            pcm[(size_t)i * 2 + 1] = (uint8_t)((v >> 8) & 0xff);
        }
    };
    std::vector<uint8_t> first, second;
    tone(first, 400.0, 1.4); // a 1.4 second sound
    tone(second, 1200.0, 0.7);
    const int32_t CH = 27;
    auto play = [&](std::vector<uint8_t> &pcm) {
        HostAudioPlay p;
        memset(&p, 0, sizeof p);
        p.channel = CH;
        p.pcm = pcm.data();
        p.bytes = (uint32_t)pcm.size();
        p.sample_rate = (int32_t)hz;
        p.channels = 1;
        p.bits = 16;
        host_audio_play(&p);
        host_audio_stream(CH); // what the QMixer shim does
    };

    play(first);
    // The voice is holding the whole sound; nothing has been appended to it.
    CHECK_EQ(host_audio_queued_bytes(CH), 0u);
    CHECK_EQ(host_audio_voice_remaining_bytes(CH), (uint32_t)first.size());

    float peak = 0.0f;
    host_audio_offline_render((uint32_t)(rate * 0.080), &peak); // 80 ms in
    CHECK(peak > 0.0f);
    uint32_t left = host_audio_voice_remaining_bytes(CH);
    // 80 ms of a 1.4 second sound: most of it is still to come, and the
    // appended-bytes question still answers zero because nothing was appended.
    CHECK(left > first.size() / 2);
    CHECK(left < first.size());
    CHECK_EQ(host_audio_queued_bytes(CH), 0u);

    // Replaced at 80 ms, exactly as the game replaces on a named channel.
    play(second);
    CHECK_EQ(host_audio_voice_remaining_bytes(CH), (uint32_t)second.size());
    CHECK_EQ(host_audio_queued_bytes(CH), 0u);

    // An append is counted by both, because it is both.
    CHECK_EQ(host_audio_queue(CH, second.data(), 4096), 4096);
    CHECK_EQ(host_audio_queued_bytes(CH), 4096u);
    CHECK_EQ(host_audio_voice_remaining_bytes(CH), (uint32_t)second.size() + 4096u);

    host_audio_stop(CH);
    CHECK_EQ(host_audio_voice_remaining_bytes(CH), 0u); // nothing is playing
    host_audio_offline_end();
}

// The music, which is MIDI through a SoundFont: the one the game ships when
// it is here, else the kit's bundled General MIDI bank, which is always here.
// This plays a note and renders the engine offline: no audio device is opened
// and nothing comes out of the speakers, but what is measured is exactly what
// would have.
static void test_midi_soundfont() {
    std::string bank = "original/gog/Sound/POPFIGHT.SF2";
    if (FILE *f = fopen(bank.c_str(), "rb"))
        fclose(f);
    else
        bank = host_resource("general-midi.sf2");
    CHECK(!bank.empty());
    const char *sf2 = bank.c_str();
    printf("  (the synth plays %s)\n", sf2);

    // Offline first. Opening the synth starts the engine, and an engine that
    // is already in manual rendering mode starts without touching the hardware.
    if (!host_audio_offline_begin(44100.0, 4096)) {
        printf("  (this machine has no audio engine, so the synth is not tested)\n");
        return;
    }

    // Built at startup, before any guest thread exists. Opening afterwards
    // parses nothing: it hands over what is already there.
    CHECK_EQ(host_midi_startup(sf2), 1);
    CHECK_EQ(host_midi_is_open(), 0); // built is not open
    CHECK_EQ(host_midi_open(sf2), 1);
    CHECK_EQ(host_midi_is_open(), 1);
    CHECK(host_midi_synth_name()[0] != 0);

    // A program change on channel 0, then a note. AUMIDISynth loads a patch
    // when it is first asked for one, so the score's own order - choose the
    // instrument, then play - is also the order that gives it time to load.
    host_midi_short(0xc0 | (0u << 8)); // program 0, acoustic piano
    float peak = 0.0f;
    host_audio_offline_render(4410, &peak); // 100 ms to settle

    host_midi_short(0x90 | (60u << 8) | (100u << 16)); // middle C, forte
    CHECK_EQ(host_midi_notes_started(), 1u);

    // Up to a second, in tenths, stopping as soon as the note is audible. A
    // synth that renders nothing at all renders nothing for the whole second.
    float loudest = 0.0f;
    for (int i = 0; i < 10 && loudest <= 0.0f; ++i) {
        float chunk = 0.0f;
        CHECK_EQ(host_audio_offline_render(4410, &chunk), 4410u);
        if (chunk > loudest)
            loudest = chunk;
    }
    printf("  (%s, loudest sample %.4f)\n", host_midi_synth_name(), loudest);
    CHECK(loudest > 0.0f);

    // A note-on with velocity zero is a note-off and is not a new note.
    host_midi_short(0x90 | (60u << 8) | (0u << 16));
    CHECK_EQ(host_midi_notes_started(), 1u);

    // Two-byte messages must not be sent with a third byte, and a status byte
    // that is not one is not sent at all.
    host_midi_short(0xd0 | (64u << 8)); // channel pressure
    host_midi_short(0x00);              // not a status byte
    host_midi_short(0xff);              // system reset, not this path

    // The sysex the game sends is twelve bytes for another maker's synth; the
    // General MIDI reset is the one that means something here. Both are
    // accepted, neither throws.
    const uint8_t creative[12] = {0xf0, 0x42, 0x00, 0x01, 0, 0, 0, 0, 0, 0, 0, 0xf7};
    host_midi_sysex(creative, sizeof creative);
    const uint8_t gm_reset[6] = {0xf0, 0x7e, 0x7f, 0x09, 0x01, 0xf7};
    host_midi_sysex(gm_reset, sizeof gm_reset);
    host_midi_reset();

    host_midi_close();
    CHECK_EQ(host_midi_is_open(), 0);
    // Closed twice is not a crash, and neither is a message to a shut synth.
    host_midi_close();
    host_midi_short(0x90 | (60u << 8) | (100u << 16));
    // The synth survives the close: it is the host's, not the game's, so a
    // reopen costs nothing and gets the same one back.
    CHECK_EQ(host_midi_open(sf2), 1);
    CHECK(strcmp(host_midi_synth_name(), "") != 0);
    host_midi_close();

    host_audio_offline_end();
    CHECK_EQ(host_audio_lock_violations(), 0u);
}

// ===========================================================================
// d3d_render.mm
// ===========================================================================
namespace {

// A command with all 256 render states, the way the shim hands one over.
struct Command {
    HostD3DDraw cmd;
    uint32_t state[256];
    std::vector<uint8_t> vertices;
    std::vector<uint16_t> indices;

    Command() {
        memset(&cmd, 0, sizeof cmd);
        memset(state, 0, sizeof state);
        cmd.render_state = state;
        cmd.render_state_count = 256;
        cmd.vertex_type = 3; // D3DVT_TLVERTEX
        cmd.vertex_stride = 32;
        cmd.primitive_type = 4; // D3DPT_TRIANGLELIST
        cmd.viewport[2] = 64;
        cmd.viewport[3] = 64;
        cmd.viewport_maxz = 1.0f;
    }
    // sx sy sz rhw, colour, specular, tu tv.
    void tlvertex(float x, float y, float z, uint32_t color, float u = 0, float v = 0,
                  uint32_t specular = 0) {
        float f[8] = {x, y, z, 1.0f, 0, 0, u, v};
        uint32_t c[2] = {color, specular};
        uint8_t bytes[32];
        memcpy(bytes, f, 16);
        memcpy(bytes + 16, c, 8);
        memcpy(bytes + 24, f + 6, 8);
        vertices.insert(vertices.end(), bytes, bytes + 32);
        finish();
    }
    void finish() {
        // A copied Command still points at the original's render-state array,
        // so rebinding here is what makes `copy.state[x] = y` mean anything.
        cmd.render_state = state;
        cmd.vertices = vertices.data();
        cmd.vertex_count = (uint32_t)(vertices.size() / 32);
        if (!indices.empty()) {
            cmd.indices = indices.data();
            cmd.index_count = (uint32_t)indices.size();
        }
    }
    void triangle(float z, uint32_t color, uint32_t specular = 0) {
        tlvertex(0, 0, z, color, 0, 0, specular);
        tlvertex(64, 0, z, color, 0, 0, specular);
        tlvertex(0, 64, z, color, 0, 0, specular);
    }
};

struct Readback {
    std::vector<uint8_t> bgra;
    int w = 0, h = 0;
    // The target is BGRA8; the tests ask in the order a person reads it.
    void rgb(int x, int y, int *r, int *g, int *b, int *a) const {
        const uint8_t *p = bgra.data() + ((size_t)y * w + x) * 4;
        *b = p[0];
        *g = p[1];
        *r = p[2];
        *a = p[3];
    }
};

Readback read_target(D3DRenderer *renderer) {
    Readback out;
    int w = 0, h = 0;
    const gpu::TextureDesc target = target_desc(renderer);
    out.bgra.resize((size_t)target.width * target.height * 4);
    if (!renderer->readPixels(out.bgra.data(), &w, &h))
        return out;
    out.w = w;
    out.h = h;
    return out;
}

// A 16-bit 5-6-5 DirectDraw surface living in ordinary memory, which is what a
// render target is from the host's side: pixels it has to write back into.
struct Surface {
    std::vector<uint8_t> pixels;
    HostD3DSurface desc;

    // A surface that dies takes its pixels with it, so the renderer must not
    // be left holding a pointer into them. This is what the shim now does when
    // the guest releases a surface: ask for any pending scene back while the
    // memory is still there to receive it, then stop the device pointing at
    // it. Without it a test that leaves the mirror dirty writes into the next
    // test's heap.
    ~Surface() {
        D3DRenderer *renderer = D3DRenderer::shared();
        renderer->flushSurface(&desc, "test");
        renderer->setRenderTarget(nullptr);
    }
    Surface(uint32_t id, int w, int h) : pixels((size_t)w * h * 2, 0) {
        memset(&desc, 0, sizeof desc);
        desc.id = id;
        desc.pixels = pixels.data();
        desc.width = w;
        desc.height = h;
        desc.pitch = w * 2;
        desc.bpp = 16;
        desc.rmask = 0xf800;
        desc.gmask = 0x07e0;
        desc.bmask = 0x001f;
    }
    void fill(uint16_t value) {
        for (size_t i = 0; i < pixels.size(); i += 2) {
            pixels[i] = (uint8_t)(value & 0xff);
            pixels[i + 1] = (uint8_t)(value >> 8);
        }
    }
    void block(int x0, int y0, int x1, int y1, uint16_t value) {
        for (int y = y0; y < y1; ++y)
            for (int x = x0; x < x1; ++x) {
                uint8_t *p = pixels.data() + ((size_t)y * desc.width + x) * 2;
                p[0] = (uint8_t)(value & 0xff);
                p[1] = (uint8_t)(value >> 8);
            }
    }
    // What SetSurfaceDesc does: point the surface at storage the guest owns,
    // and free whatever it had. The old bytes are handed back so a test can
    // check what reached them before they went away.
    std::vector<uint8_t> replaceStorage(uint16_t fill) {
        std::vector<uint8_t> old = pixels;
        for (size_t i = 0; i < pixels.size(); i += 2) {
            pixels[i] = (uint8_t)(fill & 0xff);
            pixels[i + 1] = (uint8_t)(fill >> 8);
        }
        return old;
    }
    uint16_t value_at(const std::vector<uint8_t> &buf, int x, int y) const {
        const uint8_t *p = buf.data() + ((size_t)y * desc.width + x) * 2;
        return (uint16_t)(p[0] | (p[1] << 8));
    }
    uint16_t at(int x, int y) const {
        const uint8_t *p = pixels.data() + ((size_t)y * desc.width + x) * 2;
        return (uint16_t)(p[0] | (p[1] << 8));
    }
    // The 5-6-5 channels, scaled up the way the renderer scales them.
    void rgb(int x, int y, int *r, int *g, int *b) const {
        uint16_t p = at(x, y);
        *r = ((p >> 11) & 0x1f) * 255 / 31;
        *g = ((p >> 5) & 0x3f) * 255 / 63;
        *b = (p & 0x1f) * 255 / 31;
    }
};

const uint16_t k565_red = 0xf800;
const uint16_t k565_green = 0x07e0;
const uint16_t k565_blue = 0x001f;

} // namespace

static void test_primitive_expansion() {
    // A fan of four vertices is two triangles; a strip of four is two as well,
    // and the second one has its first two vertices swapped.
    Command fan;
    fan.cmd.primitive_type = 6; // D3DPT_TRIANGLEFAN
    fan.tlvertex(0, 0, 0, 0xffff0000);
    fan.tlvertex(10, 0, 0, 0xff00ff00);
    fan.tlvertex(10, 10, 0, 0xff0000ff);
    fan.tlvertex(0, 10, 0, 0xffffffff);
    CHECK_EQ(host_d3d_expand(&fan.cmd, nullptr, 0), 6);
    CHECK_EQ(host_d3d_primitive_kind(&fan.cmd), HOST_D3D_TRIANGLES);

    std::vector<HostD3DVertex> out(8);
    host_d3d_expand(&fan.cmd, out.data(), 6);
    // Every triangle of a fan starts at the first vertex.
    CHECK_NEAR(out[0].r, 1.0, 1e-6);
    CHECK_NEAR(out[3].r, 1.0, 1e-6);
    // The second triangle is (v0, v2, v3).
    CHECK_NEAR(out[4].b, 1.0, 1e-6);

    Command strip;
    strip.cmd.primitive_type = 5; // D3DPT_TRIANGLESTRIP
    strip.tlvertex(0, 0, 0, 0xffff0000);
    strip.tlvertex(10, 0, 0, 0xff00ff00);
    strip.tlvertex(0, 10, 0, 0xff0000ff);
    strip.tlvertex(10, 10, 0, 0xffffffff);
    CHECK_EQ(host_d3d_expand(&strip.cmd, nullptr, 0), 6);
    host_d3d_expand(&strip.cmd, out.data(), 6);
    // The odd triangle is (v2, v1, v3), not (v1, v2, v3).
    CHECK_NEAR(out[3].b, 1.0, 1e-6);
    CHECK_NEAR(out[4].g, 1.0, 1e-6);

    // Points and lines are primitives this renderer draws, not ones it drops.
    Command points;
    points.cmd.primitive_type = 1; // D3DPT_POINTLIST
    points.tlvertex(1, 1, 0, 0xffffffff);
    points.tlvertex(2, 2, 0, 0xffffffff);
    points.tlvertex(3, 3, 0, 0xffffffff);
    CHECK_EQ(host_d3d_primitive_kind(&points.cmd), HOST_D3D_POINTS);
    CHECK_EQ(host_d3d_expand(&points.cmd, nullptr, 0), 3);

    Command lines;
    lines.cmd.primitive_type = 2; // D3DPT_LINELIST
    lines.tlvertex(0, 0, 0, 0xffffffff);
    lines.tlvertex(10, 0, 0, 0xffffffff);
    lines.tlvertex(0, 10, 0, 0xffffffff);
    lines.tlvertex(10, 10, 0, 0xffffffff);
    CHECK_EQ(host_d3d_primitive_kind(&lines.cmd), HOST_D3D_LINES);
    CHECK_EQ(host_d3d_expand(&lines.cmd, nullptr, 0), 4);

    Command strip_lines;
    strip_lines.cmd.primitive_type = 3; // D3DPT_LINESTRIP
    strip_lines.tlvertex(0, 0, 0, 0xffffffff);
    strip_lines.tlvertex(10, 0, 0, 0xffffffff);
    strip_lines.tlvertex(10, 10, 0, 0xffffffff);
    // Three points make two segments, so four vertices.
    CHECK_EQ(host_d3d_expand(&strip_lines.cmd, nullptr, 0), 4);

    // Flat shading copies the first vertex's colour over the triangle.
    Command flat;
    flat.state[9] = 1; // D3DRENDERSTATE_SHADEMODE = FLAT
    flat.tlvertex(0, 0, 0, 0xffff0000);
    flat.tlvertex(10, 0, 0, 0xff00ff00);
    flat.tlvertex(0, 10, 0, 0xff0000ff);
    host_d3d_expand(&flat.cmd, out.data(), 3);
    CHECK_NEAR(out[1].r, 1.0, 1e-6);
    CHECK_NEAR(out[1].g, 0.0, 1e-6);
    CHECK_NEAR(out[2].r, 1.0, 1e-6);

    // Indexed draws address the same vertices through the index array.
    Command indexed;
    indexed.tlvertex(0, 0, 0, 0xffff0000);
    indexed.tlvertex(10, 0, 0, 0xff00ff00);
    indexed.tlvertex(0, 10, 0, 0xff0000ff);
    indexed.indices = {2, 1, 0};
    indexed.finish();
    host_d3d_expand(&indexed.cmd, out.data(), 3);
    CHECK_NEAR(out[0].b, 1.0, 1e-6);
    CHECK_NEAR(out[2].r, 1.0, 1e-6);

    // A vertex type the renderer cannot decode is refused, not guessed at.
    Command bad;
    bad.cmd.vertex_type = 9;
    bad.tlvertex(0, 0, 0, 0xffffffff);
    CHECK_EQ(host_d3d_expand(&bad.cmd, nullptr, 0), -1);

    // The pre-transformed path: screen (0,0) is the top left of the viewport,
    // which in clip space is (-1, +1).
    Command corner;
    corner.tlvertex(0, 0, 0.25f, 0xffffffff);
    corner.tlvertex(64, 64, 0.25f, 0xffffffff);
    corner.tlvertex(32, 0, 0.25f, 0xffffffff);
    host_d3d_expand(&corner.cmd, out.data(), 3);
    CHECK_NEAR(out[0].x, -1.0, 1e-5);
    CHECK_NEAR(out[0].y, 1.0, 1e-5);
    CHECK_NEAR(out[1].x, 1.0, 1e-5);
    CHECK_NEAR(out[1].y, -1.0, 1e-5);
    CHECK_NEAR(out[2].x, 0.0, 1e-5);
    CHECK_NEAR(out[0].z, 0.25, 1e-5);
    CHECK_NEAR(out[0].w, 1.0, 1e-5);

    // The specular alpha is the vertex fog factor and has to survive decoding.
    Command fogged;
    fogged.tlvertex(0, 0, 0, 0xffffffff, 0, 0, 0x80000000u);
    fogged.tlvertex(1, 0, 0, 0xffffffff, 0, 0, 0x80000000u);
    fogged.tlvertex(0, 1, 0, 0xffffffff, 0, 0, 0x80000000u);
    host_d3d_expand(&fogged.cmd, out.data(), 3);
    CHECK_NEAR(out[0].sa, 128.0 / 255.0, 1e-5);
}

static void test_render_clear_and_triangle(D3DRenderer *renderer) {
    Surface surface(1, 64, 64);
    renderer->setRenderTarget(&surface.desc);

    renderer->beginScene();
    renderer->clearFlags(3, nullptr, 0, 0xff0000ffu, 1.0f);
    renderer->endScene();
    Readback rb = read_target(renderer);
    CHECK_EQ(rb.w, 64);
    int r, g, b, a;
    rb.rgb(1, 1, &r, &g, &b, &a);
    CHECK_EQ(r, 0);
    CHECK_EQ(g, 0);
    CHECK_EQ(b, 255);
    rb.rgb(63, 63, &r, &g, &b, &a);
    CHECK_EQ(b, 255);

    // A red triangle over the top-left half, on top of that clear.
    Command tri;
    tri.triangle(0.5f, 0xffff0000);
    renderer->beginScene();
    renderer->draw(&tri.cmd);
    renderer->endScene();
    rb = read_target(renderer);
    rb.rgb(2, 2, &r, &g, &b, &a);
    CHECK_EQ(r, 255);
    CHECK_EQ(g, 0);
    CHECK_EQ(b, 0);
    // Outside the triangle the clear is still there: a draw only touches what
    // it covers.
    rb.rgb(60, 60, &r, &g, &b, &a);
    CHECK_EQ(b, 255);
    CHECK_EQ(r, 0);

    // Gouraud shading interpolates: the middle of a red-to-green edge is not
    // either of them.
    Command gouraud;
    gouraud.state[9] = 2; // SHADEMODE = GOURAUD
    gouraud.tlvertex(0, 0, 0.5f, 0xffff0000);
    gouraud.tlvertex(64, 0, 0.5f, 0xff00ff00);
    gouraud.tlvertex(0, 64, 0.5f, 0xffff0000);
    renderer->beginScene();
    renderer->clearFlags(3, nullptr, 0, 0xff000000u, 1.0f);
    renderer->draw(&gouraud.cmd);
    renderer->endScene();
    rb = read_target(renderer);
    rb.rgb(31, 1, &r, &g, &b, &a);
    CHECK(r > 60 && r < 200);
    CHECK(g > 60 && g < 200);
    CHECK_EQ(b, 0);
}

// The ruling the review made: the device rasterizes into its render-target
// surface, so software drawing and Direct3D drawing have to interleave in that
// surface's own memory, in the order the guest produced them.
static void test_render_target_write_back(D3DRenderer *renderer) {
    Surface surface(2, 64, 64);
    surface.fill(k565_blue);
    renderer->setRenderTarget(&surface.desc);

    // --- draw, then flip. The triangle lands in the surface; the blue the
    // guest had already put there survives outside it.
    Command tri;
    tri.triangle(0.5f, 0xffff0000);
    renderer->beginScene();
    renderer->draw(&tri.cmd);
    renderer->endScene();
    renderer->flushSurface(&surface.desc, "test");
    int r, g, b;
    surface.rgb(2, 2, &r, &g, &b);
    CHECK(r > 200);
    CHECK(b < 40);
    surface.rgb(60, 60, &r, &g, &b);
    CHECK(b > 200);
    CHECK(r < 40);

    // --- draw, then blit, then flip. The blit happens after the flush the
    // shim makes before it, so nothing may overwrite it afterwards.
    surface.block(40, 40, 56, 56, k565_green);
    renderer->flushSurface(&surface.desc, "test"); // nothing new was drawn
    surface.rgb(48, 48, &r, &g, &b);
    CHECK(g > 200);
    CHECK(r < 40);

    // --- blit, then draw, then flip. The draw must leave the blit alone
    // everywhere its triangle does not cover, which it can only do by reading
    // the surface back in first.
    Command second;
    second.tlvertex(0, 0, 0.5f, 0xff0000ff);
    second.tlvertex(20, 0, 0.5f, 0xff0000ff);
    second.tlvertex(0, 20, 0.5f, 0xff0000ff);
    renderer->beginScene();
    renderer->draw(&second.cmd);
    renderer->endScene();
    renderer->flushSurface(&surface.desc, "test");
    // The new triangle is there.
    surface.rgb(2, 2, &r, &g, &b);
    CHECK(b > 200);
    CHECK(r < 40);
    // The green block the guest blitted in is still there.
    surface.rgb(48, 48, &r, &g, &b);
    CHECK(g > 200);
    CHECK(r < 40);
    // And so is the red from the first draw, where neither touched it.
    surface.rgb(30, 5, &r, &g, &b);
    CHECK(r > 200);
    CHECK(b < 40);

    // A flush with nothing drawn since the last one leaves the surface alone,
    // which is what makes the Lock/Blt/Flip calls that happen constantly cheap
    // and safe.
    uint32_t before = host_d3d_total_flushes();
    renderer->flushSurface(&surface.desc, "test");
    CHECK_EQ(host_d3d_total_flushes(), before);

    // A surface that is not the render target is never written to.
    Surface other(3, 64, 64);
    other.fill(k565_red);
    renderer->beginScene();
    renderer->draw(&tri.cmd);
    renderer->endScene();
    renderer->flushSurface(&other.desc, "test");
    other.rgb(2, 2, &r, &g, &b);
    CHECK(r > 200);
    CHECK(b < 40);
    renderer->flushSurface(&surface.desc, "test");
}

// Flip swaps the memory behind a surface, so the host must never be holding a
// pointer to the old one.
// The write-back must touch only what the device rasterized. A pixel the guest
// wrote and the device never covered has to come out of a flush with the exact
// bytes it went in with - not the bytes a trip through RGB would return.
static void test_render_target_lossless(D3DRenderer *renderer) {
    Surface surface(20, 64, 64);
    // Values chosen because a round trip through 8-bit RGB does not return
    // them: a 5-bit channel of 1 scales to 8 and back to 0, and a 6-bit green
    // of 1 scales to 4 and back to 0.
    const uint16_t kFragile[4] = {0x0001, 0x0020, 0x0801, 0x1863};
    for (int y = 0; y < 64; ++y)
        for (int x = 0; x < 64; ++x)
            surface.block(x, y, x + 1, y + 1, kFragile[(x + y) & 3]);
    std::vector<uint8_t> before = surface.pixels;

    renderer->setRenderTarget(&surface.desc);
    // A small triangle in the top-left corner, nowhere near most of the frame.
    Command tri;
    tri.tlvertex(0, 0, 0.5f, 0xffff0000);
    tri.tlvertex(16, 0, 0.5f, 0xffff0000);
    tri.tlvertex(0, 16, 0.5f, 0xffff0000);
    renderer->beginScene();
    renderer->draw(&tri.cmd);
    renderer->endScene();
    renderer->flushSurface(&surface.desc, "test");

    // Everything outside the triangle is bit-identical to what the guest wrote.
    int changed_outside = 0, changed_inside = 0;
    for (int y = 0; y < 64; ++y)
        for (int x = 0; x < 64; ++x) {
            size_t i = ((size_t)y * 64 + x) * 2;
            bool same = before[i] == surface.pixels[i] && before[i + 1] == surface.pixels[i + 1];
            // The triangle covers x + y < 16 in the corner; sample well
            // inside and well outside it rather than arguing about the edge.
            if (x + y < 10) {
                if (!same)
                    ++changed_inside;
            } else if (x + y > 24) {
                if (!same)
                    ++changed_outside;
            }
        }
    CHECK_EQ(changed_outside, 0);
    CHECK(changed_inside > 0);
    // And the fragile values really are still there, not merely unchanged
    // because the test wrote something a round trip preserves.
    CHECK_EQ(surface.at(63, 62), kFragile[(63 + 62) & 3]);
    CHECK_EQ(surface.at(40, 41), kFragile[(40 + 41) & 3]);
}

// A Clear that is still only a pending load action has not happened yet, and a
// Lock or a Blt before EndScene must not read the pixels it was about to
// replace.
static void test_render_clear_before_end_scene(D3DRenderer *renderer) {
    Surface surface(21, 64, 64);
    surface.fill(k565_red);
    renderer->setRenderTarget(&surface.desc);

    renderer->beginScene();
    renderer->clearFlags(3, nullptr, 0, 0xff00ff00u, 1.0f);
    // No endScene: this is the guest locking the surface mid-scene.
    renderer->flushSurface(&surface.desc, "test");
    int r, g, b;
    surface.rgb(32, 32, &r, &g, &b);
    CHECK(g > 200);
    CHECK(r < 40);
    renderer->endScene();
}

// A scene drawn into one target and then abandoned for another belongs to the
// surface it was drawn for. Losing it because the device moved on is what the
// re-review called out: the flush has to happen when the target changes, and a
// later flush naming the old surface has to still find those pixels.
static void test_render_target_switch(D3DRenderer *renderer) {
    Surface a(30, 64, 64);
    Surface b(31, 64, 64);
    a.fill(k565_blue);
    b.fill(k565_blue);

    renderer->setRenderTarget(&a.desc);
    Command red;
    red.triangle(0.5f, 0xffff0000);
    renderer->beginScene();
    renderer->draw(&red.cmd);
    renderer->endScene();

    // No flush: the device simply starts pointing somewhere else, which is
    // what CreateDevice and SetRenderTarget do.
    uint32_t before = host_d3d_total_flushes();
    renderer->setRenderTarget(&b.desc);
    CHECK_EQ(host_d3d_total_flushes(), before + 1);

    int r, g, bl;
    a.rgb(2, 2, &r, &g, &bl);
    CHECK(r > 200); // the scene reached its own surface
    a.rgb(60, 60, &r, &g, &bl);
    CHECK(bl > 200); // and left the rest of it alone
    b.rgb(2, 2, &r, &g, &bl);
    CHECK(bl > 200); // the new target was not written to

    // Drawing into the new target and then asking about the old one writes
    // nothing to the old one: its pixels were already accounted for.
    Command green;
    green.triangle(0.5f, 0xff00ff00);
    renderer->beginScene();
    renderer->draw(&green.cmd);
    renderer->endScene();
    std::vector<uint8_t> a_before = a.pixels;
    renderer->flushSurface(&a.desc, "test");
    CHECK(a_before == a.pixels);
    renderer->flushSurface(&b.desc, "test");
    b.rgb(2, 2, &r, &g, &bl);
    CHECK(g > 200);
}

// dx_reset runs after mem_init has discarded the guest arena, so every pixel
// pointer the renderer holds names memory that is not there. A pending scene
// must be dropped, not written: writing it would put pixels through a dangling
// guest address, into whatever the arena's memory has become.
static void test_render_discard_on_reset(D3DRenderer *renderer) {
    Surface surface(50, 64, 64);
    surface.fill(k565_blue);
    std::vector<uint8_t> before = surface.pixels;
    renderer->setRenderTarget(&surface.desc);

    Command tri;
    tri.triangle(0.5f, 0xffff0000);
    renderer->beginScene();
    renderer->draw(&tri.cmd);
    renderer->endScene();

    // The arena goes. This is host_d3d_discard, which is what d3d_reset calls
    // in place of naming a null render target.
    uint32_t flushes = host_d3d_total_flushes();
    host_d3d_discard();
    CHECK_EQ(host_d3d_total_flushes(), flushes);
    CHECK(before == surface.pixels);

    // Nothing left pointing at the old surface: a later flush or target change
    // writes nothing to it either, however it is asked.
    renderer->flushSurface(&surface.desc, "test");
    CHECK(before == surface.pixels);
    renderer->setRenderTarget(nullptr);
    CHECK(before == surface.pixels);
    CHECK_EQ(host_d3d_total_flushes(), flushes);

    // And the renderer is usable again on the other side of the reset: a new
    // arena means new surfaces, and the first draw into one starts from that
    // surface's own pixels rather than from whatever the mirror still held.
    Surface fresh(51, 64, 64);
    fresh.fill(k565_green);
    renderer->setRenderTarget(&fresh.desc);
    Command blue;
    blue.tlvertex(0, 0, 0.5f, 0xff0000ff);
    blue.tlvertex(16, 0, 0.5f, 0xff0000ff);
    blue.tlvertex(0, 16, 0.5f, 0xff0000ff);
    renderer->beginScene();
    renderer->draw(&blue.cmd);
    renderer->endScene();
    renderer->flushSurface(&fresh.desc, "test");
    int r, g, b;
    fresh.rgb(2, 2, &r, &g, &b);
    CHECK(b > 200);
    // No trace of the discarded scene: the corner the old triangle covered is
    // still the green this surface was filled with, not the old red.
    fresh.rgb(40, 8, &r, &g, &b);
    CHECK(g > 200);
    CHECK(r < 40);
}

// SetSurfaceDesc frees the surface's storage and points it at the guest's own.
// If the device has been rendering into it, the host is holding a scene for the
// buffer that is about to go away: it has to be asked for it back first, and
// told the new pointer afterwards, or a later scene lands in freed storage.
static void test_render_target_surface_desc(D3DRenderer *renderer) {
    Surface surface(60, 64, 64);
    surface.fill(k565_blue);
    renderer->setRenderTarget(&surface.desc);

    Command red;
    red.triangle(0.5f, 0xffff0000);
    renderer->beginScene();
    renderer->draw(&red.cmd);
    renderer->endScene();

    // What the shim now does around the replacement: flush, swap, retarget.
    renderer->flushSurface(&surface.desc, "test");
    std::vector<uint8_t> freed = surface.replaceStorage(k565_green);
    renderer->setRenderTarget(&surface.desc);

    // The scene reached the storage it was drawn for, before that storage went.
    uint16_t old_corner = surface.value_at(freed, 2, 2);
    CHECK(((old_corner >> 11) & 0x1f) > 25); // red
    CHECK((old_corner & 0x1f) < 5);
    // The new storage is exactly what the guest put there.
    CHECK_EQ(surface.at(2, 2), k565_green);
    CHECK_EQ(surface.at(60, 60), k565_green);

    // And the next scene goes to the new storage, not the old.
    Command blue;
    blue.tlvertex(0, 0, 0.5f, 0xff0000ff);
    blue.tlvertex(16, 0, 0.5f, 0xff0000ff);
    blue.tlvertex(0, 16, 0.5f, 0xff0000ff);
    renderer->beginScene();
    renderer->draw(&blue.cmd);
    renderer->endScene();
    std::vector<uint8_t> freed_after = freed;
    renderer->flushSurface(&surface.desc, "test");
    int r, g, b;
    surface.rgb(2, 2, &r, &g, &b);
    CHECK(b > 200);
    CHECK(r < 40);
    // Untouched corners of the new storage keep the guest's green, and the
    // storage that was replaced was not written to again.
    CHECK_EQ(surface.at(60, 60), k565_green);
    CHECK(freed == freed_after);
}

// Nothing may darken on the way to the surface. A scene that renders correctly
// and arrives dim is a conversion bug, and the 5-6-5 round trip is where one
// would live: an earlier review found a channel of 1 becoming 8 becoming 0.
static void test_render_no_darkening(D3DRenderer *renderer) {
    Surface surface(70, 64, 64);
    renderer->setRenderTarget(&surface.desc);

    // White, mid grey and a few exact 5-6-5 values, drawn untextured so the
    // vertex colour is the only thing deciding the pixel.
    struct {
        uint32_t argb;
        int r, g, b;
    } cases[] = {
        {0xffffffff, 255, 255, 255}, {0xff808080, 128, 128, 128}, {0xffff0000, 255, 0, 0},
        {0xff00ff00, 0, 255, 0},     {0xff0000ff, 0, 0, 255},     {0xff212121, 33, 33, 33},
    };
    for (const auto &c : cases) {
        Command tri;
        tri.triangle(0.5f, c.argb);
        renderer->beginScene();
        renderer->clearFlags(3, nullptr, 0, 0xff000000u, 1.0f);
        renderer->draw(&tri.cmd);
        renderer->endScene();
        renderer->flushSurface(&surface.desc, "test");
        int r, g, b;
        surface.rgb(2, 2, &r, &g, &b);
        // 5-6-5 cannot hold every 8-bit value, so the bar is that nothing is
        // lost beyond the format's own step: 8 for red and blue, 4 for green.
        CHECK(r >= c.r - 9 && r <= c.r + 9);
        CHECK(g >= c.g - 5 && g <= c.g + 5);
        CHECK(b >= c.b - 9 && b <= c.b + 9);
    }

    // White specifically has to come out white, not 248 or 247: a run where
    // every frame is a few per cent dark looks exactly like a lighting bug.
    Command white;
    white.triangle(0.5f, 0xffffffff);
    renderer->beginScene();
    renderer->clearFlags(3, nullptr, 0, 0xff000000u, 1.0f);
    renderer->draw(&white.cmd);
    renderer->endScene();
    renderer->flushSurface(&surface.desc, "test");
    CHECK_EQ(surface.at(2, 2), 0xffff); // every bit set
    int r, g, b;
    surface.rgb(2, 2, &r, &g, &b);
    CHECK_EQ(r, 255);
    CHECK_EQ(g, 255);
    CHECK_EQ(b, 255);
}

// An untextured draw, and one naming a texture that was never uploaded, must
// both render the vertex colour. Rendering black instead is a dark scene with
// no other symptom.
static void test_render_untextured_uses_vertex_colour(D3DRenderer *renderer) {
    Surface surface(71, 64, 64);
    renderer->setRenderTarget(&surface.desc);

    // No texture handle at all.
    Command plain;
    plain.state[21] = 2; // TEXTUREMAPBLEND = MODULATE
    plain.triangle(0.5f, 0xff40c080);
    renderer->beginScene();
    renderer->clearFlags(3, nullptr, 0, 0xff000000u, 1.0f);
    renderer->draw(&plain.cmd);
    renderer->endScene();
    Readback rb = read_target(renderer);
    int r, g, b, a;
    rb.rgb(2, 2, &r, &g, &b, &a);
    CHECK(r > 50 && r < 80);
    CHECK(g > 175 && g < 210);
    CHECK(b > 110 && b < 145);

    // A handle the renderer never saw. It is logged once and drawn with the
    // vertex colour rather than black.
    Command missing;
    missing.cmd.texture_handle = 4242;
    missing.state[1] = 4242;
    missing.state[21] = 2;
    missing.triangle(0.5f, 0xff40c080);
    renderer->beginScene();
    renderer->clearFlags(3, nullptr, 0, 0xff000000u, 1.0f);
    renderer->draw(&missing.cmd);
    renderer->endScene();
    rb = read_target(renderer);
    rb.rgb(2, 2, &r, &g, &b, &a);
    CHECK(r > 50 && r < 80);
    CHECK(g > 175 && g < 210);
}

// MODULATE's alpha comes from the texture when the texture has one and from
// the vertex otherwise, which is the documented cascade. The half that was
// missing is the first: with a colour-keyed texture and no alpha test, keyed
// texels were drawn opaque because the vertex alpha won.
static void test_render_modulate_alpha_source(D3DRenderer *renderer) {
    Surface surface(72, 64, 64);
    renderer->setRenderTarget(&surface.desc);

    // An opaque texture with no colour key and no alpha mask.
    uint16_t white565[1] = {0xffff};
    HostD3DTexture opaque;
    memset(&opaque, 0, sizeof opaque);
    opaque.handle = 21;
    opaque.pixels = white565;
    opaque.width = 1;
    opaque.height = 1;
    opaque.pitch = 2;
    opaque.bpp = 16;
    opaque.rmask = 0xf800;
    opaque.gmask = 0x07e0;
    opaque.bmask = 0x001f;
    renderer->uploadTexture(&opaque);

    // A texture with no alpha of its own: the vertex's alpha governs, which is
    // the second half of the cascade and what a fade relies on.
    Command quad;
    quad.cmd.texture_handle = 21;
    quad.state[1] = 21;
    quad.state[21] = 2;              // MODULATE
    quad.state[27] = 1;              // ALPHABLENDENABLE
    quad.state[19] = 5;              // SRCBLEND = SRCALPHA
    quad.state[20] = 6;              // DESTBLEND = INVSRCALPHA
    quad.triangle(0.5f, 0xffff0000); // opaque red
    renderer->beginScene();
    renderer->clearFlags(3, nullptr, 0, 0xff000000u, 1.0f);
    renderer->draw(&quad.cmd);
    renderer->endScene();
    Readback rb = read_target(renderer);
    int r, g, b, a;
    rb.rgb(2, 2, &r, &g, &b, &a);
    CHECK_EQ(r, 255);
    // Half alpha on the vertex halves it, because the texture has none.
    quad.vertices.clear();
    quad.triangle(0.5f, 0x80ff0000);
    renderer->beginScene();
    renderer->clearFlags(3, nullptr, 0, 0xff000000u, 1.0f);
    renderer->draw(&quad.cmd);
    renderer->endScene();
    rb = read_target(renderer);
    rb.rgb(2, 2, &r, &g, &b, &a);
    CHECK(r > 110 && r < 145);

    // A texture that really does carry alpha still governs it: a fully
    // transparent colour-keyed texel draws nothing.
    uint8_t keyed[1] = {0};
    uint32_t palette[256] = {0};
    palette[0] = 0x00ffffffu;
    HostD3DTexture clear_tex;
    memset(&clear_tex, 0, sizeof clear_tex);
    clear_tex.handle = 22;
    clear_tex.pixels = keyed;
    clear_tex.width = 1;
    clear_tex.height = 1;
    clear_tex.pitch = 1;
    clear_tex.bpp = 8;
    clear_tex.palette = palette;
    clear_tex.has_colorkey = 1;
    clear_tex.colorkey_lo = clear_tex.colorkey_hi = 0;
    renderer->uploadTexture(&clear_tex);

    quad.cmd.texture_handle = 22;
    quad.state[1] = 22;
    quad.vertices.clear();
    quad.triangle(0.5f, 0xffff0000); // opaque red vertex this time
    renderer->beginScene();
    renderer->clearFlags(3, nullptr, 0, 0xff0000ffu, 1.0f);
    renderer->draw(&quad.cmd);
    renderer->endScene();
    rb = read_target(renderer);
    rb.rgb(2, 2, &r, &g, &b, &a);
    CHECK_EQ(b, 255); // the clear, through a transparent texel
    CHECK_EQ(r, 0);
    renderer->destroyTexture(21);
    renderer->destroyTexture(22);
}

// The game's own frame, in the order the Wine trace shows it: draw the scene,
// end it, blit the interface panels onto the back buffer, flip. The panels are
// software blits into the surface, and nothing the renderer does afterwards may
// overwrite them - a write-back landing after the blits is a black minimap.
static void test_render_panels_survive_to_present(D3DRenderer *renderer) {
    Surface surface(80, 64, 64);
    surface.fill(k565_blue);
    renderer->setRenderTarget(&surface.desc);

    // The scene: a clear and a triangle over the whole target, as a frame that
    // covers the screen would.
    Command scene;
    scene.tlvertex(0, 0, 0.5f, 0xffff0000);
    scene.tlvertex(64, 0, 0.5f, 0xffff0000);
    scene.tlvertex(0, 64, 0.5f, 0xffff0000);
    scene.tlvertex(64, 64, 0.5f, 0xffff0000);
    scene.cmd.primitive_type = 5; // strip: the whole target
    scene.finish();
    renderer->beginScene();
    renderer->clearFlags(3, nullptr, 0, 0xff000000u, 1.0f);
    renderer->draw(&scene.cmd);
    renderer->endScene();

    // The shim flushes before the blit reads or writes the surface.
    renderer->flushSurface(&surface.desc, "Blt dst");
    int r, g, b;
    surface.rgb(32, 32, &r, &g, &b);
    CHECK(r > 200); // the scene reached the surface

    // The panel: a software blit straight into the surface, as Blt does.
    surface.block(0, 0, 16, 16, k565_green);

    // Flip. The shim flushes again first, and this is the one that must not
    // write anything: nothing has been drawn since the last flush, so the
    // panel is the newest thing in those pixels.
    uint32_t flushes = host_d3d_total_flushes();
    renderer->flushSurface(&surface.desc, "Flip front");
    CHECK_EQ(host_d3d_total_flushes(), flushes);
    surface.rgb(4, 4, &r, &g, &b);
    CHECK(g > 200); // the panel survived to the present
    CHECK(r < 60);
    surface.rgb(32, 32, &r, &g, &b);
    CHECK(r > 200); // and the scene is still under it

    // The next frame redraws the scene over everything, which is what the game
    // does, and the panel is blitted again after it. The panel from the last
    // frame is gone by then, and that is correct: it was not drawn this time.
    renderer->beginScene();
    renderer->clearFlags(3, nullptr, 0, 0xff000000u, 1.0f);
    renderer->draw(&scene.cmd);
    renderer->endScene();
    renderer->flushSurface(&surface.desc, "Blt dst");
    surface.rgb(4, 4, &r, &g, &b);
    CHECK(r > 200); // the scene covered where the panel was
    surface.block(0, 0, 16, 16, k565_green);
    renderer->flushSurface(&surface.desc, "Flip front");
    surface.rgb(4, 4, &r, &g, &b);
    CHECK(g > 200); // and the new panel survives again
}

static void test_render_target_flip(D3DRenderer *renderer) {
    Surface front(4, 64, 64);
    Surface back(4, 64, 64); // the same surface id, new memory
    front.fill(k565_blue);
    back.fill(k565_blue);
    renderer->setRenderTarget(&front.desc);

    Command tri;
    tri.triangle(0.5f, 0xffff0000);
    renderer->beginScene();
    renderer->draw(&tri.cmd);
    renderer->endScene();
    // The shim flushes and then re-states the target at its new memory.
    renderer->flushSurface(&front.desc, "test");
    renderer->setRenderTarget(&back.desc);

    int r, g, b;
    front.rgb(2, 2, &r, &g, &b);
    CHECK(r > 200);
    back.rgb(2, 2, &r, &g, &b);
    CHECK(b > 200); // untouched by the first draw

    Command green;
    green.triangle(0.5f, 0xff00ff00);
    renderer->beginScene();
    renderer->draw(&green.cmd);
    renderer->endScene();
    renderer->flushSurface(&back.desc, "test");
    back.rgb(2, 2, &r, &g, &b);
    CHECK(g > 200);
    // The old memory was not written through a stale pointer.
    front.rgb(2, 2, &r, &g, &b);
    CHECK(r > 200);
    CHECK(g < 100);
}

static bool g_t4_claim_frames = false;
extern "C" int host_d3d_claim_sealed_frame(uint64_t) {
    return g_t4_claim_frames ? 1 : 0;
}
extern "C" void host_d3d_reset_readback_metrics(void);
static void test_readback_noninteger_scale(D3DRenderer *renderer) {
    for (auto size : {std::pair{31, 19}, std::pair{151, 99}, std::pair{3840, 2160}}) {
        renderer->discard();
        host_d3d_reset_coherence();
        g_t4_claim_frames = true;
        renderer->setSceneWidth(size.first, size.second);
        Surface surface(709, 53, 37);
        surface.fill(k565_blue);
        host_d3d_bind_generation(&surface.desc, 1, 7209);
        host_d3d_clear(3, nullptr, 0, 0xff080808, 1);
        Command tri;
        tri.tlvertex(0, 0, .5f, 0xffef7b21);
        tri.tlvertex(53, 0, .5f, 0xff1234ef);
        tri.tlvertex(0, 37, .5f, 0xff6deb17);
        tri.cmd.viewport[2] = 53;
        tri.cmd.viewport[3] = 37;
        renderer->draw(&tri.cmd);
        Readback reference = read_target(renderer);
        CHECK_EQ(reference.w, size.first);
        CHECK_EQ(reference.h, size.second);
        host_d3d_mark_dirty(surface.desc.id, 1, {0, 0, 53, 37});
        HostDirtyRect partial{3, 2, 43, 31};
        for (bool full : {false, true}) {
            host_d3d_reset_readback_metrics();
            CHECK_EQ(
                host_d3d_make_coherent(&surface.desc, 1, full ? nullptr : &partial, HOST_READ_LOCK),
                1);
            auto edge = [](int x, int n, int g) { return (x * n + g - 1) / g; };
            size_t lit = 0;
            for (int y = 0; y < reference.h; ++y)
                for (int x = 0; x < reference.w; ++x) {
                    bool in_partial = x >= edge(partial.x0, reference.w, 53) &&
                                      x < edge(partial.x1, reference.w, 53) &&
                                      y >= edge(partial.y0, reference.h, 37) &&
                                      y < edge(partial.y1, reference.h, 37);
                    // The second read contains only the remaining dirty islands.
                    if (in_partial == full)
                        continue;
                    size_t at = (size_t(y) * reference.w + x) * 4;
                    lit += (reference.bgra[at] > 8 || reference.bgra[at + 1] > 8 ||
                            reference.bgra[at + 2] > 8);
                }
            CHECK_NEAR(host_d3d_peak_nonblack(), double(lit) / (reference.w * reference.h), 1e-12);
            for (int y = 0; y < 37; ++y)
                for (int x = 0; x < 53; ++x) {
                    bool touched = full || (x >= 3 && x < 43 && y >= 2 && y < 31);
                    if (!touched) {
                        CHECK_EQ(surface.at(x, y), k565_blue);
                        continue;
                    }
                    int red, g, b, alpha;
                    reference.rgb((2 * x + 1) * size.first / (2 * 53),
                                  (2 * y + 1) * size.second / (2 * 37), &red, &g, &b, &alpha);
                    uint16_t expected =
                        ((red * 31 / 255) << 11) | ((g * 63 / 255) << 5) | (b * 31 / 255);
                    CHECK_EQ(surface.at(x, y), expected);
                }
        }
        g_t4_claim_frames = false;
        renderer->discard();
        renderer->setSceneWidth(0, 0);
    }
}

// Both kernels must match the independent CPU pixel/count oracle, including
// downscaling, fractional native cells and padded threadgroups at 4K edges.
static void test_readback_kernel_parity(D3DRenderer *original) {
    const char *saved = recomp_env("HOST_READBACK_KERNEL");
    std::string old = saved ? saved : "";
    bool had = saved != nullptr;
    original->discard();
    for (const char *mode : {"fused", "tiled"}) {
        os_setenv("RECOMP_HOST_READBACK_KERNEL", mode);
        D3DRenderer *r = make_renderer();
        CHECK(r != nullptr);
        if (!r)
            continue;
        D3DRenderer::setShared(r);
        test_readback_noninteger_scale(r);
        r->discard();
    }
    if (had)
        os_setenv("RECOMP_HOST_READBACK_KERNEL", old.c_str());
    else
        os_unsetenv("RECOMP_HOST_READBACK_KERNEL");
    D3DRenderer::setShared(original);
    host_d3d_reset_coherence();
}

// Compare the GPU seed against the retained CPU implementation for every
// 16-bit value, palette entries, odd pitches and non-integral 4K scaling.
// Mutate the source before submission to catch a borrowed-buffer upload.
static void test_surface_upload_pixels(D3DRenderer *original) {
    const char *saved = recomp_env("HOST_SURFACE_UPLOAD");
    std::string old = saved ? saved : "";
    bool had = saved != nullptr;
    original->discard();
    for (int format = 0; format < 9; ++format)
        for (bool wide : {false, true}) {
            Readback reference;
            for (const char *mode : {"cpu", "gpu"}) {
                os_setenv("RECOMP_HOST_SURFACE_UPLOAD", mode);
                auto r = make_renderer();
                CHECK(r != nullptr);
                if (!r)
                    continue;
                D3DRenderer::setShared(r);
                const int w = wide ? 53 : 256, h = wide ? 37 : 256;
                Surface surface(711, w, h);
                surface.desc.bpp = format >= 6 ? (format == 7 ? 24 : 32) : format >= 4 ? 8 : 16;
                surface.desc.pitch = w * (surface.desc.bpp > 16 ? 4 : surface.desc.bpp / 8) + 7;
                surface.pixels.assign(surface.desc.pitch * h, 0xcd);
                surface.desc.pixels = surface.pixels.data();
                if (format == 1) {
                    surface.desc.rmask = 0x7c00;
                    surface.desc.gmask = 0x03e0;
                }
                if (format == 2) {
                    surface.desc.rmask = 0x001f;
                    surface.desc.bmask = 0xf800;
                }
                if (format == 3)
                    surface.desc.rmask = surface.desc.gmask = surface.desc.bmask = 0;
                if (format >= 6) {
                    surface.desc.rmask = 0xff0000;
                    surface.desc.gmask = 0xff00;
                    surface.desc.bmask = 0xff;
                }
                if (format == 8)
                    surface.desc.rmask = surface.desc.gmask = surface.desc.bmask = 0;
                uint32_t palette[256];
                for (int i = 0; i < 256; ++i)
                    palette[i] = ((i * 37 & 255) << 16) | ((i * 53 & 255) << 8) | (i * 97 & 255);
                surface.desc.palette = format == 4 ? palette : nullptr;
                for (int y = 0; y < h; ++y)
                    for (int x = 0; x < w; ++x) {
                        unsigned value = y * w + x;
                        auto pixel = surface.pixels.data() + y * surface.desc.pitch +
                                     x * (surface.desc.bpp > 16 ? 4 : surface.desc.bpp / 8);
                        pixel[0] = value & 255;
                        if (surface.desc.bpp == 16)
                            pixel[1] = value >> 8;
                        if (surface.desc.bpp > 16) {
                            pixel[0] = 141;
                            pixel[1] = 73;
                            pixel[2] = 19;
                            pixel[3] = 0;
                        }
                    }
                r->setSceneWidth(wide ? 3840 : 769, wide ? 2160 : 515);
                r->setRenderTarget(&surface.desc);
                // A clipped point opens the render pass without touching a pixel.
                Command d;
                d.cmd.primitive_type = 1;
                d.tlvertex(-20, -20, .5f, 0xffffffff);
                d.finish();
                r->beginScene();
                r->draw(&d.cmd);
                std::fill(surface.pixels.begin(), surface.pixels.end(), 0);
                std::fill(std::begin(palette), std::end(palette), 0);
                auto image = read_target(r);
                CHECK_EQ(image.w, wide ? 3840 : 769);
                CHECK_EQ(image.h, wide ? 2160 : 515);
                if (format >= 6) {
                    int red, green, blue, alpha;
                    image.rgb(20, 20, &red, &green, &blue, &alpha);
                    CHECK_EQ(red, 19);
                    CHECK_EQ(green, 73);
                    CHECK_EQ(blue, 141);
                }
                if (!strcmp(mode, "cpu"))
                    reference = std::move(image);
                else
                    CHECK(image.bgra == reference.bgra);
                r->discard();
            }
        }
    if (had)
        os_setenv("RECOMP_HOST_SURFACE_UPLOAD", old.c_str());
    else
        os_unsetenv("RECOMP_HOST_SURFACE_UPLOAD");
    D3DRenderer::setShared(original);
}

// Identical ordered draws across automatic submission boundaries must keep
// native color, depth rejection and guest coverage/writeback byte-for-byte.
static void test_bounded_submission_pixels(D3DRenderer *original) {
    const char *saved = recomp_env("HOST_D3D_SUBMIT_DRAWS");
    std::string old = saved ? saved : "";
    bool had = saved != nullptr;
    original->discard();
    for (auto size : {std::pair{151, 99}, std::pair{3840, 2160}}) {
        Readback reference;
        std::vector<uint8_t> guest;
        for (int interval : {0, 1, 256}) {
            os_setenv("RECOMP_HOST_D3D_SUBMIT_DRAWS", std::to_string(interval).c_str());
            D3DRenderer *r = make_renderer();
            CHECK(r != nullptr);
            if (!r)
                continue;
            D3DRenderer::setShared(r);
            host_d3d_reset_coherence();
            g_t4_claim_frames = true;
            r->setSceneWidth(size.first, size.second);
            {
                Surface surface(710, 64, 64);
                surface.fill(k565_blue);
                host_d3d_bind_generation(&surface.desc, 1, 7210);
                r->beginScene();
                // Depth clear only: uncovered guest pixels must remain blue.
                host_d3d_clear(2, nullptr, 0, 0, 1);
                for (int i = 0; i < 600; ++i) {
                    Command tri;
                    tri.state[7] = 1;
                    tri.state[14] = 1;
                    tri.state[23] = 4;
                    float x = float(i % 48), y = float((i / 48) % 48);
                    float z = (i % 3 == 0) ? .25f : .75f;
                    uint32_t color = (i % 3 == 0) ? 0xffef7123 : 0xff31df67;
                    tri.tlvertex(x, y, z, color);
                    tri.tlvertex(x + 12, y, z, color);
                    tri.tlvertex(x, y + 12, z, color);
                    r->draw(&tri.cmd);
                }
                // Blending after both automatic prefixes exercises load/store
                // continuation without depending on a particular sample pixel.
                Command blend;
                blend.state[27] = 1;
                blend.state[19] = 5;
                blend.state[20] = 6;
                blend.triangle(.1f, 0x80c02070);
                r->draw(&blend.cmd);
                r->endScene();
                Readback actual = read_target(r);
                host_d3d_mark_dirty(surface.desc.id, 1, {0, 0, 64, 64});
                CHECK_EQ(host_d3d_make_coherent(&surface.desc, 1, nullptr, HOST_READ_LOCK), 1);
                CHECK_EQ(r->commandStorageStats().early_submissions, interval ? 2u : 0u);
                if (!interval) {
                    reference = std::move(actual);
                    guest = surface.pixels;
                } else {
                    CHECK_EQ(actual.w, reference.w);
                    CHECK_EQ(actual.h, reference.h);
                    CHECK(actual.bgra == reference.bgra);
                    CHECK(surface.pixels == guest);
                }
                r->discard();
            }
            g_t4_claim_frames = false;
        }
    }
    if (had)
        os_setenv("RECOMP_HOST_D3D_SUBMIT_DRAWS", old.c_str());
    else
        os_unsetenv("RECOMP_HOST_D3D_SUBMIT_DRAWS");
    D3DRenderer::setShared(original);
    host_d3d_reset_coherence();
}

static void test_parallel_readback_pixels(D3DRenderer *original) {
    const char *saved = recomp_env("HOST_READBACK_WORKERS");
    std::string old = saved ? saved : "";
    bool had = saved != nullptr;
    original->discard();
    for (int bpp : {8, 16}) {
        std::vector<uint8_t> partial_reference, full_reference;
        for (int workers : {1, 4}) {
            os_setenv("RECOMP_HOST_READBACK_WORKERS", std::to_string(workers).c_str());
            D3DRenderer *r = make_renderer();
            CHECK(r != nullptr);
            if (!r)
                continue;
            D3DRenderer::setShared(r);
            host_d3d_reset_coherence();
            g_t4_claim_frames = true;
            r->setSceneWidth(3840, 2160);
            {
                Surface surface(711, 640, 480);
                uint32_t palette[256];
                for (int i = 0; i < 256; ++i)
                    palette[i] = 0xff000000u | i * 0x010101u;
                surface.desc.bpp = bpp;
                surface.desc.pitch = 640 * (bpp / 8);
                surface.pixels.resize(surface.desc.pitch * 480);
                surface.desc.pixels = surface.pixels.data();
                if (bpp == 8)
                    surface.desc.palette = palette;
                for (size_t i = 0; i < surface.pixels.size(); ++i)
                    surface.pixels[i] = uint8_t(i % 251);
                host_d3d_bind_generation(&surface.desc, 1, 7211);
                r->beginScene();
                host_d3d_clear(2, nullptr, 0, 0, 1);
                Command tri;
                tri.cmd.viewport[2] = 640;
                tri.cmd.viewport[3] = 480;
                tri.tlvertex(0, 0, .5f, 0xffe17423);
                tri.tlvertex(640, 0, .5f, 0xff26dd54);
                tri.tlvertex(0, 480, .5f, 0xff613bc1);
                r->draw(&tri.cmd);
                r->endScene();
                host_d3d_mark_dirty(surface.desc.id, 1, {0, 0, 640, 480});
                HostDirtyRect partial{13, 7, 301, 231};
                CHECK_EQ(host_d3d_make_coherent(&surface.desc, 1, &partial, HOST_READ_LOCK), 1);
                CHECK_EQ(r->commandStorageStats().parallel_readbacks, 0u);
                if (workers == 1)
                    partial_reference = surface.pixels;
                else
                    CHECK(surface.pixels == partial_reference);
                // The remaining dirty islands are large enough for worker
                // dispatch. Compare every byte, including uncovered pixels.
                CHECK_EQ(host_d3d_make_coherent(&surface.desc, 1, nullptr, HOST_READ_LOCK), 1);
                CHECK_EQ(r->commandStorageStats().parallel_readbacks, workers == 4 ? 1u : 0u);
                if (workers == 1)
                    full_reference = surface.pixels;
                else
                    CHECK(surface.pixels == full_reference);
                r->discard();
            }
            g_t4_claim_frames = false;
        }
    }
    if (had)
        os_setenv("RECOMP_HOST_READBACK_WORKERS", old.c_str());
    else
        os_unsetenv("RECOMP_HOST_READBACK_WORKERS");
    D3DRenderer::setShared(original);
    host_d3d_reset_coherence();
}

static void test_incremental_scene_readback(D3DRenderer *renderer) {
    // Reset must isolate this observation from earlier renderer/presenter
    // reads, including the fully coloured world/overlay integration test.
    renderer->discard();
    host_d3d_reset_coherence();
    Surface surface(707, 64, 64);
    g_t4_claim_frames = true;
    host_d3d_bind_generation(&surface.desc, 1, 7201);
    host_d3d_clear(3, nullptr, 0, 0xff000000, 1);
    Readback black = read_target(renderer);
    CHECK_EQ(black.w, 64);
    CHECK_EQ(black.h, 64);
    CHECK_EQ(host_d3d_peak_nonblack(), 0.0); // opaque alpha is not scene colour

    Command tri;
    tri.triangle(0.5f, 0xffff0000);
    HostD3DDrawSnapshot d{};
    d.kind = HOST_DRAW_PRIMITIVE;
    d.primitive_type = 4;
    d.fvf = 3;
    d.vertex_stride = 32;
    d.vertex_count = 3;
    d.vertices = tri.vertices.data();
    d.state.viewport[2] = d.state.viewport[3] = 64;
    d.state.viewport_maxz = 1;
    host_d3d_begin_scene();
    host_d3d_draw(&d);
    host_d3d_end_scene();
    host_d3d_mark_dirty(surface.desc.id, 1, {0, 0, 64, 64});
    host_d3d_seal_frame(7201);
    CHECK_EQ(host_readback_count_for_test(), 0u);

    HostDirtyRect tiny{2, 2, 4, 4};
    CHECK_EQ(host_d3d_make_coherent(&surface.desc, 1, &tiny, HOST_READ_LOCK), 1);
    CHECK_EQ(surface.at(2, 2), k565_red);
    CHECK(host_d3d_peak_nonblack() > 0.0);
    CHECK(host_d3d_peak_nonblack() <= 4.0 / (64 * 64));
    // The remaining disjoint islands must be measured together, without
    // counting stale staging pixels in the already-clean hole.
    CHECK_EQ(host_d3d_make_coherent(&surface.desc, 1, nullptr, HOST_READ_LOCK), 1);
    CHECK(host_d3d_peak_nonblack() > 0.45);
    CHECK(host_d3d_peak_nonblack() < 0.55);
    CHECK_EQ(host_readback_count_for_test(), 2u);
    CHECK_EQ(host_d3d_make_coherent(&surface.desc, 1, nullptr, HOST_READ_LOCK), 0);
    CHECK_EQ(host_readback_count_for_test(), 2u);

    // The smoke dump reads the scene texture even when guest memory is clean.
    Readback rb = read_target(renderer);
    CHECK_EQ(rb.w, 64);
    CHECK_EQ(rb.h, 64);
    if (rb.w == 64 && rb.h == 64) {
        int r, g, b, a;
        rb.rgb(4, 4, &r, &g, &b, &a);
        CHECK(r > 200);
        CHECK_EQ(g, 0);
        CHECK_EQ(b, 0);
        rb.rgb(60, 60, &r, &g, &b, &a);
        CHECK_EQ(r, 0);
        CHECK_EQ(g, 0);
        CHECK_EQ(b, 0);
    }

    // A newer leased target is read directly without a guest coherence read.
    // Its larger green triangle must raise the metric from readPixels itself.
    host_d3d_bind_generation(&surface.desc, 1, 7202);
    CHECK(renderer->colorTarget() != renderer->colorTargetForFrame(7201));
    Command green;
    green.tlvertex(-64, 0, 0.5f, 0xff00ff00);
    green.tlvertex(128, 0, 0.5f, 0xff00ff00);
    green.tlvertex(0, 128, 0.5f, 0xff00ff00);
    d.vertices = green.vertices.data();
    host_d3d_draw(&d);
    host_d3d_mark_dirty(surface.desc.id, 1, {0, 0, 64, 64});
    host_d3d_seal_frame(7202);
    rb = read_target(renderer);
    CHECK_EQ(rb.w, 64);
    CHECK_EQ(rb.h, 64);
    if (rb.w == 64 && rb.h == 64) {
        int r, g, b, a;
        rb.rgb(48, 48, &r, &g, &b, &a);
        CHECK_EQ(r, 0);
        CHECK(g > 200);
        CHECK_EQ(b, 0);
    }
    CHECK(host_d3d_peak_nonblack() > 0.9);
    CHECK_EQ(host_readback_count_for_test(), 2u);
    CHECK(host_d3d_dirty_rect(surface.desc.id, 1, nullptr));
    host_d3d_retire_frame(7201);
    host_d3d_retire_frame(7202);
    g_t4_claim_frames = false;
    renderer->discard();
    host_d3d_reset_coherence();
    CHECK_EQ(host_d3d_peak_nonblack(), 0.0);
}

static void test_frame_targets_are_leased(D3DRenderer *renderer) {
    renderer->discard();
    host_d3d_reset_coherence();
    Surface surface(706, 64, 64);
    g_t4_claim_frames = true;
    gpu::Texture textures[4];
    for (int i = 0; i < 4; ++i) {
        uint64_t frame = 7100 + i;
        host_d3d_bind_generation(&surface.desc, 1, frame);
        renderer->clearFlags(3, nullptr, 0, 0xff000000u | ((40u + i * 40u) << 16), 1);
        host_d3d_mark_dirty(706, 1, {0, 0, 64, 64});
        host_d3d_seal_frame(frame);
        textures[i] = renderer->colorTargetForFrame(frame);
        CHECK(bool(textures[i]));
        CHECK(bool(renderer->completionForFrame(frame)));
        for (int j = 0; j < i; ++j)
            CHECK(textures[i] != textures[j]);
    }
    CHECK_EQ(host_readback_count_for_test(), 0u);
    // Every sealed frame still has its own colour, including the dropped
    // frames, although three later clears targeted the same guest generation.
    for (int i = 0; i < 4; ++i) {
        // readback waits for the frame's GPU work before copying.
        uint8_t p[4] = {0, 0, 0, 0};
        CHECK(g_gpu->readback(textures[i], {0, 0, 1, 1}, p, 4));
        CHECK_EQ(p[2], 40 + i * 40);
        CHECK_EQ(p[1], 0);
        CHECK_EQ(p[0], 0);
        host_d3d_retire_frame(7100 + i);
        CHECK(!renderer->colorTargetForFrame(7100 + i));
    }
    g_t4_claim_frames = false;
    renderer->discard();
}

static uint64_t g_t5_legacy_frame = 0;
static HostDrawMapping g_t5_mapping = HOST_MAPPING_SCENE;
extern "C" int host_frame_legacy(HostFrameHandle f) {
    return f.id == g_t5_legacy_frame;
}
extern "C" HostDrawMapping host_frame_draw_mapping(HostFrameHandle, uint32_t) {
    return g_t5_mapping;
}
static void t5_snapshot(const Command &c, HostD3DDrawSnapshot &d, uint32_t seq) {
    d = {};
    d.seq = seq;
    d.kind = HOST_DRAW_PRIMITIVE;
    d.primitive_type = c.cmd.primitive_type;
    d.fvf = c.cmd.vertex_type;
    d.vertex_stride = c.cmd.vertex_stride;
    d.vertices = c.cmd.vertices;
    d.vertex_count = c.cmd.vertex_count;
    d.texture_handle = c.cmd.texture_handle;
    memcpy(d.state.render_state, c.state, sizeof c.state);
    memcpy(d.state.viewport, c.cmd.viewport, sizeof d.state.viewport);
    d.state.viewport_maxz = 1;
}
static void test_same_frame_legacy_pixels(D3DRenderer *renderer) {
    std::vector<uint16_t> reference;
    for (bool legacy : {true, false}) {
        renderer->discard();
        host_d3d_reset_coherence();
        if (legacy)
            os_setenv("RECOMP_LEGACY_WRITEBACK", "1");
        else
            os_unsetenv("RECOMP_LEGACY_WRITEBACK");
        renderer->setSceneWidth(legacy ? 0 : 256, legacy ? 0 : 192);
        Surface surface(805, 128, 96);
        host_d3d_bind_generation(&surface.desc, 1, 8005);
        Command world;
        world.cmd.viewport[2] = 128;
        world.cmd.viewport[3] = 96;
        world.tlvertex(0, 0, 0.5f, 0xffff0000);
        world.tlvertex(128, 0, 0.5f, 0xffff0000);
        world.tlvertex(0, 96, 0.5f, 0xffff0000);
        HostD3DDrawSnapshot w{};
        t5_snapshot(world, w, 0);
        host_d3d_draw(&w);
        host_d3d_flush_surface(&surface.desc, "Blt dst");
        std::vector<uint8_t> coverage(64 * 64, 1);
        std::vector<uint16_t> sprite(64 * 64, 0x07e0);
        coverage[0] = 0; // keyed hole preserves the world pixel
        HostBlitRecord hud{};
        hud.seq = 1;
        hud.dst = 805;
        hud.dst_generation = 1;
        hud.src.surface = HOST_SRC_CPU;
        hud.cpu_bpp = 16;
        hud.cpu_pitch = 128;
        hud.w = hud.h = 64;
        hud.coverage = coverage.data();
        hud.cpu_pixels = (const uint8_t *)sprite.data();
        for (int y = 0; y < 64; ++y)
            for (int x = 0; x < 64; ++x)
                if (coverage[y * 64 + x])
                    ((uint16_t *)surface.desc.pixels)[y * 128 + x] = sprite[y * 64 + x];
        host_d3d_apply_cpu(&surface.desc, &hud);
        std::fill(sprite.begin(), sprite.end(), 0xffff); // temporary payload is already copied
        std::fill(coverage.begin(), coverage.end(), 0);
        int32_t rect[] = {2, 2, 5, 5};
        HostD3DDrawSnapshot clear{};
        clear.kind = HOST_DRAW_CLEAR;
        clear.seq = 2;
        clear.clear_flags = 3;
        clear.clear_rects = rect;
        clear.clear_rect_count = 1;
        clear.clear_color = 0xff123456;
        clear.clear_z = 1;
        host_d3d_draw(&clear);
        Command overlay;
        overlay.cmd.viewport[2] = 128;
        overlay.cmd.viewport[3] = 96;
        overlay.tlvertex(32, 32, 0.2f, 0xff0000ff);
        overlay.tlvertex(96, 32, 0.2f, 0xff0000ff);
        overlay.tlvertex(32, 80, 0.2f, 0xff0000ff);
        HostD3DDrawSnapshot o{};
        t5_snapshot(overlay, o, 3);
        o.in_overlay_pass = 1;
        host_d3d_draw(&o);
        host_d3d_flush_surface(&surface.desc, "Blt dst");
        uint8_t cpu8[] = {255, 17, 19};
        HostBlitRecord finalWrite{};
        finalWrite.seq = 4;
        finalWrite.dst = 805;
        finalWrite.dst_generation = 1;
        finalWrite.src.surface = HOST_SRC_CPU;
        finalWrite.cpu_bpp = 8;
        finalWrite.cpu_pitch = 3;
        finalWrite.dst_x = 100;
        finalWrite.dst_y = 90;
        finalWrite.w = finalWrite.h = 1;
        finalWrite.cpu_pixels = cpu8;
        ((uint16_t *)surface.desc.pixels)[90 * 128 + 100] = 0xffff;
        host_d3d_apply_cpu(&surface.desc, &finalWrite);
        if (legacy)
            host_d3d_flush_surface(&surface.desc, "present");
        else {
            g_t5_legacy_frame = 8005;
            host_d3d_seal_frame(8005); // the production automatic fallback
            CHECK(host_render_legacy_frame_for_test({8005}));
            CHECK(host_render_legacy_frame_for_test({8005})); // idempotent
            CHECK_EQ(target_desc(renderer).width, 128);
            CHECK_EQ(target_desc(renderer).height, 96);
        }
        const auto *pixels = (const uint16_t *)surface.desc.pixels;
        if (legacy)
            reference.assign(pixels, pixels + 128 * 96);
        else
            for (size_t i = 0; i < reference.size(); ++i)
                CHECK_EQ(pixels[i], reference[i]);
        CHECK_EQ(pixels[10 * 128 + 10], 0x07e0);
        CHECK_EQ(pixels[40 * 128 + 40], 0x001f);
        CHECK_EQ(pixels[0], 0xf800);
        CHECK(pixels[2 * 128 + 2] != 0x07e0);
        CHECK_EQ(pixels[90 * 128 + 100], 0xffff);
    }
    g_t5_legacy_frame = 0;
    os_unsetenv("RECOMP_LEGACY_WRITEBACK");
    renderer->setSceneWidth(0, 0);
    renderer->discard();
}

static void test_legacy_checkpoint_and_baked_mapping(D3DRenderer *renderer) {
    renderer->discard();
    host_d3d_reset_coherence();
    Surface surface(806, 64, 64);
    g_t4_claim_frames = true;
    renderer->setSceneWidth(128, 128);
    host_d3d_bind_generation(&surface.desc, 1, 8100);
    HostD3DDrawSnapshot clear{};
    clear.kind = HOST_DRAW_CLEAR;
    clear.clear_flags = 3;
    clear.clear_color = 0xffff0000;
    clear.clear_z = 0.2f;
    host_d3d_draw(&clear);
    host_d3d_mark_dirty(806, 1, {0, 0, 64, 64});
    host_d3d_seal_frame(8100);
    host_d3d_bind_generation(&surface.desc, 1, 8101);
    Command far;
    far.state[7] = 1;
    far.state[14] = 1;
    far.state[23] = 4;
    far.triangle(0.8f, 0xff0000ff);
    HostD3DDrawSnapshot draw{};
    t5_snapshot(far, draw, 0);
    draw.in_overlay_pass = 1;
    g_t5_mapping = HOST_MAPPING_UI;
    host_d3d_draw(&draw);
    g_t5_mapping = HOST_MAPPING_SCENE;
    CHECK_EQ(host_render_draw_mapping_for_test({8101}, 0), HOST_MAPPING_UI);
    g_t5_legacy_frame = 8101;
    // A guest read after discovery must see the replayed prefix, and later
    // primitives must still be replayed at seal with the same depth.
    host_d3d_mark_dirty(806, 1, {0, 0, 64, 64});
    host_d3d_replay_barrier(&surface.desc, 1, 1);
    CHECK_EQ(host_d3d_make_coherent(&surface.desc, 1, nullptr, HOST_READ_LOCK), 1);
    CHECK_EQ(surface.at(2, 2), 0xf800);
    CHECK_EQ(target_desc(renderer).width, 64);
    draw.seq = 1;
    host_d3d_draw(&draw);
    host_d3d_mark_dirty(806, 1, {0, 0, 64, 64});
    host_d3d_seal_frame(8101);
    for (int y = 0; y < 64; ++y)
        for (int x = 0; x < 64; ++x)
            CHECK_EQ(surface.at(x, y), 0xf800);
    CHECK_EQ(host_readback_count_for_test(), 1u);
    host_d3d_retire_frame(8100);
    host_d3d_retire_frame(8101);
    CHECK_EQ(host_render_draw_mapping_for_test({8101}, 0), HOST_MAPPING_SCENE);
    g_t4_claim_frames = false;
    g_t5_legacy_frame = 0;
    renderer->setSceneWidth(0, 0);
    renderer->discard();
}

static void test_prefix_submit_then_continue_keeps_depth_and_content(D3DRenderer *renderer) {
    renderer->discard();
    host_d3d_reset_coherence();
    Surface surface(704, 64, 64);
    host_d3d_bind_generation(&surface.desc, 1, 7004);
    Command a;
    a.state[7] = 1;
    a.state[14] = 1;
    a.state[23] = 4;
    a.tlvertex(0, 0, 0.2f, 0xffff0000);
    a.tlvertex(32, 0, 0.2f, 0xffff0000);
    a.tlvertex(0, 32, 0.2f, 0xffff0000);
    renderer->clearFlags(3, nullptr, 0, 0xff000000, 1);
    renderer->draw(&a.cmd);
    host_d3d_mark_dirty(surface.desc.id, 1, {0, 0, 64, 64});
    CHECK_EQ(host_d3d_make_coherent(&surface.desc, 1, nullptr, HOST_READ_LOCK), 1);
    CHECK_EQ(surface.at(2, 2), 0xf800);
    Command b;
    b.state[7] = 1;
    b.state[14] = 1;
    b.state[23] = 4;
    b.triangle(0.8f, 0xff00ff00);
    renderer->draw(&b.cmd);
    Readback rb = read_target(renderer);
    int r, g, blue, alpha;
    rb.rgb(2, 2, &r, &g, &blue, &alpha);
    CHECK_EQ(r, 255);
    CHECK_EQ(g, 0);
    rb.rgb(40, 2, &r, &g, &blue, &alpha);
    CHECK_EQ(r, 0);
    CHECK_EQ(g, 255);
    renderer->discard();
}

static void test_cpu_write_applied_partially(D3DRenderer *renderer) {
    renderer->discard();
    host_d3d_reset_coherence();
    renderer->setSceneWidth(1280, 960);
    Surface surface(705, 640, 480);
    host_d3d_bind_generation(&surface.desc, 1, 7005);
    // A colour outside RGB565's exact values catches accidental full-surface
    // conversion at a prefix, as well as a misplaced upload.
    renderer->clearFlags(3, nullptr, 0, 0xff172b3f, 1);
    std::vector<uint8_t> pixels(32, 0), coverage(16, 1);
    for (size_t i = 0; i < pixels.size(); i += 2) {
        pixels[i] = 0xe0;
        pixels[i + 1] = 7;
    }
    HostBlitRecord record{};
    record.dst = 705;
    record.dst_generation = 1;
    record.dst_x = record.dst_y = 10;
    record.w = record.h = 4;
    record.src.surface = HOST_SRC_CPU;
    record.cpu_pixels = pixels.data();
    record.cpu_bpp = 16;
    record.cpu_pitch = 8;
    record.coverage = coverage.data();
    host_d3d_apply_cpu(&surface.desc, &record);
    Readback rb = read_target(renderer);
    CHECK_EQ(rb.w, 1280);
    CHECK_EQ(rb.h, 960);
    for (int y = 0; y < rb.h; ++y)
        for (int x = 0; x < rb.w; ++x) {
            int r, g, b, a;
            rb.rgb(x, y, &r, &g, &b, &a);
            bool block = x >= 20 && x < 28 && y >= 20 && y < 28;
            CHECK_EQ(r, block ? 0 : 0x17);
            CHECK_EQ(g, block ? 255 : 0x2b);
            CHECK_EQ(b, block ? 0 : 0x3f);
        }
    renderer->discard();
    renderer->setSceneWidth(0, 0);
}

static void test_full_color_surface_readback(D3DRenderer *renderer) {
    renderer->discard();
    host_d3d_reset_coherence();
    g_t4_claim_frames = true;
    Surface surface(887, 64, 64);
    surface.desc.bpp = 32;
    surface.desc.pitch = 64 * 4 + 8;
    surface.desc.rmask = 0xff0000;
    surface.desc.gmask = 0xff00;
    surface.desc.bmask = 0xff;
    surface.pixels.assign(surface.desc.pitch * 64, 0xab);
    surface.desc.pixels = surface.pixels.data();
    renderer->setSceneWidth(128, 128);
    host_d3d_bind_generation(&surface.desc, 1, 9887);
    renderer->clearFlags(3, nullptr, 0, 0xff13498d, 1);
    HostDirtyRect rect{2, 3, 3, 4};
    CHECK(host_d3d_readback_rects(&surface.desc, 1, &rect, 1));
    const size_t at = 3 * surface.desc.pitch + 2 * 4;
    CHECK_EQ(surface.pixels[at], 141);
    CHECK_EQ(surface.pixels[at + 1], 73);
    CHECK_EQ(surface.pixels[at + 2], 19);
    CHECK_EQ(surface.pixels[at - 1], 0xab);
    CHECK_EQ(surface.pixels[at + 4], 0xab);
    CHECK_EQ(surface.pixels[3 * surface.desc.pitch + 256], 0xab);
    uint8_t color[] = {223, 163, 101, 0}, mask[] = {1};
    HostBlitRecord record{};
    record.dst = 887;
    record.dst_generation = 1;
    record.dst_x = 10;
    record.dst_y = 12;
    record.w = record.h = 1;
    record.src.surface = HOST_SRC_CPU;
    record.cpu_bpp = 32;
    record.cpu_pitch = 4;
    record.cpu_pixels = color;
    record.coverage = mask;
    host_d3d_apply_cpu(&surface.desc, &record);
    auto rb = read_target(renderer);
    int r, g, b, a;
    rb.rgb(20, 24, &r, &g, &b, &a);
    CHECK_EQ(r, 101);
    CHECK_EQ(g, 163);
    CHECK_EQ(b, 223);
    rb.rgb(40, 40, &r, &g, &b, &a);
    CHECK_EQ(r, 19);
    CHECK_EQ(g, 73);
    CHECK_EQ(b, 141);
    renderer->discard();
    renderer->setSceneWidth(0, 0);
    renderer->setRenderTarget(&surface.desc);
    renderer->clearFlags(3, nullptr, 0, 0xff13498d, 1);
    renderer->flushSurface(&surface.desc, "32-bit compatibility readback");
    CHECK_EQ(surface.pixels[0], 141);
    CHECK_EQ(surface.pixels[1], 73);
    CHECK_EQ(surface.pixels[2], 19);
    CHECK_EQ(surface.pixels[256], 0xab);
    renderer->discard();
}

static void test_render_depth(D3DRenderer *renderer) {
    Surface surface(5, 64, 64);
    renderer->setRenderTarget(&surface.desc);

    // A far green triangle drawn after a near red one must not overwrite it
    // when the depth test is on, and must when it is off.
    Command near_red;
    near_red.state[7] = 1;  // ZENABLE
    near_red.state[14] = 1; // ZWRITEENABLE
    near_red.state[23] = 4; // ZFUNC = LESSEQUAL
    near_red.triangle(0.2f, 0xffff0000);

    Command far_green = near_red;
    far_green.vertices.clear();
    far_green.triangle(0.8f, 0xff00ff00);

    renderer->beginScene();
    renderer->clearFlags(3, nullptr, 0, 0xff000000u, 1.0f);
    renderer->draw(&near_red.cmd);
    renderer->draw(&far_green.cmd);
    renderer->endScene();
    Readback rb = read_target(renderer);
    int r, g, b, a;
    rb.rgb(2, 2, &r, &g, &b, &a);
    CHECK_EQ(r, 255);
    CHECK_EQ(g, 0);

    // With the test off the later draw wins, which is what proves the test was
    // doing the work above and not the draw order.
    far_green.state[7] = 0;
    far_green.finish();
    renderer->beginScene();
    renderer->clearFlags(3, nullptr, 0, 0xff000000u, 1.0f);
    renderer->draw(&near_red.cmd);
    renderer->draw(&far_green.cmd);
    renderer->endScene();
    rb = read_target(renderer);
    rb.rgb(2, 2, &r, &g, &b, &a);
    CHECK_EQ(g, 255);
    CHECK_EQ(r, 0);

    // ZENABLE off means no test AND no write. A draw with the test disabled
    // must not leave its depth behind for the next one to trip over.
    Command unbuffered;
    unbuffered.state[7] = 0;  // ZENABLE off
    unbuffered.state[14] = 1; // ZWRITEENABLE on, and ignored
    unbuffered.triangle(0.2f, 0xffff0000);

    Command later;
    later.state[7] = 1;
    later.state[14] = 1;
    later.state[23] = 2; // ZFUNC = LESS
    later.triangle(0.5f, 0xff00ff00);

    renderer->beginScene();
    renderer->clearFlags(3, nullptr, 0, 0xff000000u, 1.0f);
    renderer->draw(&unbuffered.cmd);
    renderer->draw(&later.cmd);
    renderer->endScene();
    rb = read_target(renderer);
    rb.rgb(2, 2, &r, &g, &b, &a);
    CHECK_EQ(g, 255); // 0.5 < 1.0, because 0.2 was not written
    CHECK_EQ(r, 0);

    // A pre-transformed vertex already carries the depth the viewport would
    // have produced. With min 0.5 and max 1.0, a z of 0.2 is still 0.2 and
    // passes a LESS test against a 0.4 clear; applying the range a second time
    // would make it 0.6 and fail.
    Command ranged;
    ranged.state[7] = 1;
    ranged.state[14] = 1;
    ranged.state[23] = 2; // ZFUNC = LESS
    ranged.cmd.viewport_minz = 0.5f;
    ranged.cmd.viewport_maxz = 1.0f;
    ranged.triangle(0.2f, 0xffff0000);
    renderer->beginScene();
    renderer->clearFlags(3, nullptr, 0, 0xff000000u, 0.4f);
    renderer->draw(&ranged.cmd);
    renderer->endScene();
    rb = read_target(renderer);
    rb.rgb(2, 2, &r, &g, &b, &a);
    CHECK_EQ(r, 255);
}

static void test_render_blend_and_alpha_test(D3DRenderer *renderer) {
    Surface surface(6, 64, 64);
    renderer->setRenderTarget(&surface.desc);

    // Half-alpha white over black with SRCALPHA / INVSRCALPHA is mid grey.
    Command blended;
    blended.state[27] = 1; // ALPHABLENDENABLE
    blended.state[19] = 5; // SRCBLEND = SRCALPHA
    blended.state[20] = 6; // DESTBLEND = INVSRCALPHA
    blended.triangle(0.5f, 0x80ffffff);
    renderer->beginScene();
    renderer->clearFlags(3, nullptr, 0, 0xff000000u, 1.0f);
    renderer->draw(&blended.cmd);
    renderer->endScene();
    Readback rb = read_target(renderer);
    int r, g, b, a;
    rb.rgb(2, 2, &r, &g, &b, &a);
    CHECK(r > 110 && r < 145);

    // Alpha test: GREATER than 0x80 rejects a 0x40 alpha entirely.
    Command tested;
    tested.state[15] = 1;    // ALPHATESTENABLE
    tested.state[25] = 5;    // ALPHAFUNC = GREATER
    tested.state[24] = 0x80; // ALPHAREF
    tested.triangle(0.5f, 0x40ff0000);
    renderer->beginScene();
    renderer->clearFlags(3, nullptr, 0, 0xff0000ffu, 1.0f);
    renderer->draw(&tested.cmd);
    renderer->endScene();
    rb = read_target(renderer);
    rb.rgb(2, 2, &r, &g, &b, &a);
    CHECK_EQ(b, 255); // the clear survived: nothing drew
    CHECK_EQ(r, 0);

    // The same draw with a passing alpha does reach the target.
    tested.vertices.clear();
    tested.triangle(0.5f, 0xc0ff0000);
    renderer->beginScene();
    renderer->clearFlags(3, nullptr, 0, 0xff0000ffu, 1.0f);
    renderer->draw(&tested.cmd);
    renderer->endScene();
    rb = read_target(renderer);
    rb.rgb(2, 2, &r, &g, &b, &a);
    CHECK_EQ(r, 255);
}

static void test_render_fog(D3DRenderer *renderer) {
    Surface surface(7, 64, 64);
    renderer->setRenderTarget(&surface.desc);

    // Vertex fog: the factor rides in the specular alpha, and 0 means the
    // fragment is entirely fog. The device advertises FOGVERTEX, so this has
    // to do something.
    Command fogged;
    fogged.state[28] = 1;                           // FOGENABLE
    fogged.state[35] = 0;                           // FOGTABLEMODE = NONE: vertex fog
    fogged.state[34] = 0xff00ff00u;                 // FOGCOLOR = green
    fogged.triangle(0.5f, 0xffff0000, 0x00000000u); // specular alpha 0
    renderer->beginScene();
    renderer->clearFlags(3, nullptr, 0, 0xff000000u, 1.0f);
    renderer->draw(&fogged.cmd);
    renderer->endScene();
    Readback rb = read_target(renderer);
    int r, g, b, a;
    rb.rgb(2, 2, &r, &g, &b, &a);
    CHECK_EQ(g, 255);
    CHECK_EQ(r, 0);

    // A factor of 1 is no fog at all.
    fogged.vertices.clear();
    fogged.triangle(0.5f, 0xffff0000, 0xff000000u); // specular alpha 255
    renderer->beginScene();
    renderer->clearFlags(3, nullptr, 0, 0xff000000u, 1.0f);
    renderer->draw(&fogged.cmd);
    renderer->endScene();
    rb = read_target(renderer);
    rb.rgb(2, 2, &r, &g, &b, &a);
    CHECK_EQ(r, 255);
    CHECK_EQ(g, 0);

    // Table fog, linear in the eye-space distance. A pre-transformed vertex
    // has w = 1, so with start 0 and end 2 the factor is one half.
    Command table;
    float start = 0.0f, end = 2.0f;
    uint32_t start_bits, end_bits;
    memcpy(&start_bits, &start, 4);
    memcpy(&end_bits, &end, 4);
    table.state[28] = 1;           // FOGENABLE
    table.state[35] = 3;           // FOGTABLEMODE = LINEAR
    table.state[34] = 0xff0000ffu; // FOGCOLOR = blue
    table.state[36] = start_bits;
    table.state[37] = end_bits;
    table.triangle(0.5f, 0xffff0000);
    renderer->beginScene();
    renderer->clearFlags(3, nullptr, 0, 0xff000000u, 1.0f);
    renderer->draw(&table.cmd);
    renderer->endScene();
    rb = read_target(renderer);
    rb.rgb(2, 2, &r, &g, &b, &a);
    CHECK(r > 100 && r < 160);
    CHECK(b > 100 && b < 160);
}

static void test_render_texture(D3DRenderer *renderer) {
    Surface surface(8, 64, 64);
    renderer->setRenderTarget(&surface.desc);

    // An 8-bit palettised 2x2 texture with a colour key, which is how this
    // game's sprites carry their transparency.
    uint8_t pixels[4] = {0, 1, 2, 3};
    uint32_t palette[256] = {0};
    palette[0] = 0x00ff0000u; // red
    palette[1] = 0x0000ff00u; // green
    palette[2] = 0x000000ffu; // blue
    palette[3] = 0x00ffffffu; // white, and the keyed index
    HostD3DTexture tex;
    memset(&tex, 0, sizeof tex);
    tex.handle = 7;
    tex.pixels = pixels;
    tex.width = 2;
    tex.height = 2;
    tex.pitch = 2;
    tex.bpp = 8;
    tex.palette = palette;
    tex.has_colorkey = 1;
    tex.colorkey_lo = tex.colorkey_hi = 3;
    renderer->uploadTexture(&tex);

    // A quad over the whole target, sampled with the texture's own colours.
    Command quad;
    quad.cmd.texture_handle = 7;
    quad.state[1] = 7;           // TEXTUREHANDLE, as the shim sets it
    quad.state[21] = 1;          // TEXTUREMAPBLEND = DECAL
    quad.state[15] = 1;          // ALPHATESTENABLE
    quad.state[25] = 5;          // ALPHAFUNC = GREATER
    quad.state[24] = 0x40;       // ALPHAREF: the keyed texel fails
    quad.cmd.primitive_type = 5; // strip
    quad.tlvertex(0, 0, 0.5f, 0xffffffff, 0.0f, 0.0f);
    quad.tlvertex(64, 0, 0.5f, 0xffffffff, 1.0f, 0.0f);
    quad.tlvertex(0, 64, 0.5f, 0xffffffff, 0.0f, 1.0f);
    quad.tlvertex(64, 64, 0.5f, 0xffffffff, 1.0f, 1.0f);

    renderer->beginScene();
    renderer->clearFlags(3, nullptr, 0, 0xff000000u, 1.0f);
    renderer->draw(&quad.cmd);
    renderer->endScene();
    Readback rb = read_target(renderer);
    int r, g, b, a;
    // Texel (0,0) is red, (1,0) green, (0,1) blue, (1,1) the colour key.
    rb.rgb(8, 8, &r, &g, &b, &a);
    CHECK_EQ(r, 255);
    CHECK_EQ(g, 0);
    CHECK_EQ(b, 0);
    rb.rgb(56, 8, &r, &g, &b, &a);
    CHECK_EQ(g, 255);
    CHECK_EQ(r, 0);
    rb.rgb(8, 56, &r, &g, &b, &a);
    CHECK_EQ(b, 255);
    // The keyed texel got alpha 0 and the alpha test threw it away, so the
    // clear shows through.
    rb.rgb(56, 56, &r, &g, &b, &a);
    CHECK_EQ(r, 0);
    CHECK_EQ(g, 0);
    CHECK_EQ(b, 0);

    // COLORKEYENABLE must also reject the keyed texel with alpha testing and
    // blending disabled. Turning the state off restores the opaque texel.
    quad.state[15] = 0;
    quad.state[27] = 0;
    for (uint32_t keyed : {1u, 0u}) {
        quad.state[41] = keyed;
        renderer->beginScene();
        renderer->clearFlags(3, nullptr, 0, 0xff802040u, 1.0f);
        renderer->draw(&quad.cmd);
        renderer->endScene();
        rb = read_target(renderer);
        rb.rgb(56, 56, &r, &g, &b, &a);
        CHECK_EQ(r, keyed ? 128 : 255);
        CHECK_EQ(g, keyed ? 32 : 255);
        CHECK_EQ(b, keyed ? 64 : 255);
        rb.rgb(8, 8, &r, &g, &b, &a);
        CHECK_EQ(r, 255);
        CHECK_EQ(g, 0);
    }

    // MODULATE multiplies the texture by the vertex colour: a half-grey vertex
    // over the red texel is a darker red.
    quad.state[21] = 2; // MODULATE
    quad.state[15] = 0; // no alpha test this time
    quad.vertices.clear();
    quad.tlvertex(0, 0, 0.5f, 0xff808080, 0.0f, 0.0f);
    quad.tlvertex(64, 0, 0.5f, 0xff808080, 1.0f, 0.0f);
    quad.tlvertex(0, 64, 0.5f, 0xff808080, 0.0f, 1.0f);
    quad.tlvertex(64, 64, 0.5f, 0xff808080, 1.0f, 1.0f);
    renderer->beginScene();
    renderer->clearFlags(3, nullptr, 0, 0xff000000u, 1.0f);
    renderer->draw(&quad.cmd);
    renderer->endScene();
    rb = read_target(renderer);
    rb.rgb(8, 8, &r, &g, &b, &a);
    CHECK(r > 110 && r < 145);
    CHECK_EQ(g, 0);

    // A 16-bit 5-6-5 texture, the other format the shims expose.
    uint16_t rgb565[4] = {0xf800, 0x07e0, 0x001f, 0xffff};
    HostD3DTexture wide;
    memset(&wide, 0, sizeof wide);
    wide.handle = 8;
    wide.pixels = rgb565;
    wide.width = 2;
    wide.height = 2;
    wide.pitch = 4;
    wide.bpp = 16;
    wide.rmask = 0xf800;
    wide.gmask = 0x07e0;
    wide.bmask = 0x001f;
    renderer->uploadTexture(&wide);

    quad.cmd.texture_handle = 8;
    quad.state[1] = 8;
    quad.state[21] = 1; // DECAL
    quad.vertices.clear();
    quad.tlvertex(0, 0, 0.5f, 0xffffffff, 0.0f, 0.0f);
    quad.tlvertex(64, 0, 0.5f, 0xffffffff, 1.0f, 0.0f);
    quad.tlvertex(0, 64, 0.5f, 0xffffffff, 0.0f, 1.0f);
    quad.tlvertex(64, 64, 0.5f, 0xffffffff, 1.0f, 1.0f);
    renderer->beginScene();
    renderer->clearFlags(3, nullptr, 0, 0xff000000u, 1.0f);
    renderer->draw(&quad.cmd);
    renderer->endScene();
    rb = read_target(renderer);
    rb.rgb(8, 8, &r, &g, &b, &a);
    CHECK_EQ(r, 255);
    CHECK_EQ(g, 0);
    rb.rgb(56, 56, &r, &g, &b, &a);
    CHECK_EQ(r, 255);
    CHECK_EQ(g, 255);
    CHECK_EQ(b, 255);

    renderer->destroyTexture(7);
    renderer->destroyTexture(8);
}

// A texture that is re-uploaded while draws using the old contents are still
// in flight must not change what those draws sample.

// A frame is composited after the guest has moved on, so a draw has to sample
// the texture revision it was SUBMITTED with, not whichever one the guest has
// uploaded by then. The renderer keeps one copy per revision and a frame holds
// the one its draws named.
//
// The frame itself lives in ddraw.cpp, which this suite does not link, so the
// retain and release here are exactly what ddraw_frame_lease_texture and
// host_frame_release do on its behalf: that seam is tested on the dx side.
// Everything the shim actually sends goes through host_d3d_draw(snapshot).
// Nothing tested that entry, which is where the snapshot's fields are read,
// and it was reading the transform slots at 0, 1, 2 while the shim writes them
// at the D3DTRANSFORMSTATE indices - so every untransformed draw arrived with
// no world matrix and its view and projection shifted by one.
static void test_draw_snapshot_entry_point(D3DRenderer *renderer) {
    Surface surface(22, 64, 64);
    renderer->setRenderTarget(&surface.desc);
    host_render_reset_for_test();

    // D3DVT_LVERTEX: x y z, reserved, diffuse, specular, tu tv. It carries its
    // own colour, which is what makes the readback below about geometry rather
    // than about lighting.
    struct Vtx {
        float x, y, z;
        uint32_t reserved, diffuse, specular;
        float u, v;
    };
    static Vtx verts[3] = {
        {-1.0f, 1.0f, 0.5f, 0, 0xffff0000u, 0, 0, 0},
        {1.0f, 1.0f, 0.5f, 0, 0xffff0000u, 0, 1, 0},
        {-1.0f, -1.0f, 0.5f, 0, 0xffff0000u, 0, 0, 1},
    };
    static uint32_t render_state[HOST_D3D_RENDERSTATE_MAX] = {0};
    render_state[9] = 1; // SHADEMODE = FLAT

    HostD3DDrawSnapshot d;
    memset(&d, 0, sizeof d);
    d.kind = HOST_DRAW_PRIMITIVE;
    d.primitive_type = 4; // TRIANGLELIST
    d.fvf = 2;            // D3DVT_LVERTEX
    d.vertex_stride = sizeof(Vtx);
    d.vertex_count = 3;
    d.vertices = verts;
    memcpy(d.state.render_state, render_state, sizeof render_state);
    d.state.viewport[2] = 64;
    d.state.viewport[3] = 64;
    d.state.viewport_maxz = 1.0f;
    // Identity world and view, and a projection that halves x and y. Placed at
    // the D3DTRANSFORMSTATE indices, which is where the snapshot says they go.
    static const float kIdentity[16] = {1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1};
    static const float kHalf[16] = {0.5f, 0, 0, 0, 0, 0.5f, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1};
    memcpy(d.state.transform[HOST_D3D_TRANSFORM_WORLD], kIdentity, sizeof kIdentity);
    memcpy(d.state.transform[HOST_D3D_TRANSFORM_VIEW], kIdentity, sizeof kIdentity);
    memcpy(d.state.transform[HOST_D3D_TRANSFORM_PROJECTION], kHalf, sizeof kHalf);
    d.state.transform_set[HOST_D3D_TRANSFORM_WORLD] = 1;
    d.state.transform_set[HOST_D3D_TRANSFORM_VIEW] = 1;
    d.state.transform_set[HOST_D3D_TRANSFORM_PROJECTION] = 1;

    renderer->beginScene();
    renderer->clearFlags(3, nullptr, 0, 0xff000000u, 1.0f);
    host_d3d_draw(&d);
    renderer->endScene();

    Readback rb = read_target(renderer);
    int r, g, b, a;
    // The projection halves the triangle, so its corners land at (16,16),
    // (48,16) and (16,48) rather than at the corners of the target.
    rb.rgb(24, 24, &r, &g, &b, &a);
    CHECK(r > 200);
    rb.rgb(20, 20, &r, &g, &b, &a);
    CHECK(r > 200);
    // THIS is the assertion the indexing bug fails. With the transform slots
    // read at 0, 1, 2 the projection is lost - world is absent, view holds
    // world, projection holds view - and the triangle is drawn at full size,
    // covering this pixel.
    rb.rgb(4, 4, &r, &g, &b, &a);
    CHECK_EQ(r, 0);
    rb.rgb(56, 8, &r, &g, &b, &a);
    CHECK_EQ(r, 0);
    rb.rgb(8, 56, &r, &g, &b, &a);
    CHECK_EQ(r, 0);

    // And the CLEAR kind goes through the same entry, so its branch is live.
    HostD3DDrawSnapshot c;
    memset(&c, 0, sizeof c);
    c.kind = HOST_DRAW_CLEAR;
    c.clear_flags = 3;
    c.clear_color = 0xff00ff00u;
    c.clear_z = 1.0f;
    renderer->beginScene();
    host_d3d_draw(&c);
    renderer->endScene();
    rb = read_target(renderer);
    rb.rgb(32, 32, &r, &g, &b, &a);
    CHECK_EQ(g, 255);
    CHECK_EQ(r, 0);
}

static void test_unchanged_uploads_and_command_pool(D3DRenderer *renderer) {
    renderer->discard();
    host_d3d_reset_coherence();
    host_render_reset_for_test();
    g_t4_claim_frames = true;
    Surface surface(917, 640, 480);
    // Pre-upload ALL textures used by the fixture's first frame through the
    // same host callback as the smoke. No lazy first-use upload is excluded.
    uint16_t colors[] = {0xf800, 0x07e0, 0x001f};
    HostD3DTexture textures[3]{};
    for (int i = 0; i < 3; ++i) {
        auto &t = textures[i];
        t.handle = 917 + i;
        t.revision = 100 + i;
        t.width = t.height = 1;
        t.pitch = 2;
        t.bpp = 16;
        t.pixels = &colors[i];
        t.rmask = 0xf800;
        t.gmask = 0x07e0;
        t.bmask = 0x001f;
        host_d3d_texture(&t);
        CHECK(renderer->hasTexture(t.handle, t.revision));
    }
    Command draws[3];
    // >4096 bytes exercises GPU-consumed arguments as well as CPU expansion.
    // Disjoint triangles catch reuse before an earlier GPU reader finishes.
    for (int t = 0; t < 3; ++t) {
        auto &draw = draws[t];
        draw.state[21] = 1;
        draw.cmd.viewport[2] = 640;
        draw.cmd.viewport[3] = 480;
        draw.cmd.texture_handle = textures[t].handle;
        draw.state[1] = textures[t].handle;
        for (int i = 0; i < 32; ++i) {
            draw.tlvertex(t * 200, 0, .5f, 0xffffffff);
            draw.tlvertex(t * 200 + 190, 0, .5f, 0xffffffff);
            draw.tlvertex(t * 200, 480, .5f, 0xffffffff);
        }
    }
    uint32_t uploads = host_d3d_total_textures();
    HostCommandStorageStats warmed{};
    gpu::Texture warmed_targets[4];
    for (int batch = 0; batch < 26; ++batch) {
        gpu::Texture targets[4];
        for (int i = 0; i < 4; ++i) {
            uint64_t f = 17000 + batch * 4 + i;
            // Distinct generations force slot selection, rather than the
            // standalone renderer's same-surface fast path.
            host_d3d_bind_generation(&surface.desc, batch * 4 + i + 1, f);
            targets[i] = renderer->colorTarget();
            if (!batch)
                warmed_targets[i] = targets[i];
            else
                CHECK(targets[i] == warmed_targets[i]);
            for (int j = 0; j < i; ++j)
                CHECK(targets[i] != targets[j]);
            renderer->clearFlags(3, nullptr, 0, 0xff000000, 1);
            for (int t = 0; t < 3; ++t) {
                host_d3d_texture(&textures[t]);
                CHECK(renderer->retainTexture(textures[t].handle, textures[t].revision));
                renderer->draw(&draws[t].cmd, textures[t].revision);
            }
            host_d3d_seal_frame(f);
        }
        for (int i = 0; i < 4; ++i) {
            uint64_t f = 17000 + batch * 4 + i;
            g_gpu->wait(renderer->completionForFrame(f));
            CHECK(g_gpu->status(renderer->completionForFrame(f)) == gpu::CommandStatus::Completed);
            host_d3d_retire_frame(f);
            for (auto &t : textures)
                renderer->releaseTexture(t.handle, t.revision);
        }
        if (!batch)
            warmed = renderer->commandStorageStats();
        else {
            auto now = renderer->commandStorageStats();
            CHECK_EQ(now.cpu_growths, warmed.cpu_growths);
            CHECK_EQ(now.argument_buffers, warmed.argument_buffers);
            CHECK_EQ(now.scene_textures, warmed.scene_textures);
        }
        CHECK_EQ(host_d3d_total_textures(), uploads);
    }
    CHECK(warmed.argument_buffers > 0);
    CHECK(warmed.cpu_growths > 0);
    CHECK(warmed.scene_textures > 0);
    Readback rb = read_target(renderer);
    int r, g, b, a;
    rb.rgb(2, 2, &r, &g, &b, &a);
    CHECK(r > 200);
    CHECK(g < 40);
    CHECK(b < 40);
    rb.rgb(202, 2, &r, &g, &b, &a);
    CHECK(g > 200);
    CHECK(r < 40);
    CHECK(b < 40);
    rb.rgb(402, 2, &r, &g, &b, &a);
    CHECK(b > 200);
    CHECK(r < 40);
    CHECK(g < 40);
    // A changed revision uploads once and keeps the prior leased revision.
    CHECK(renderer->retainTexture(textures[0].handle, textures[0].revision));
    ++textures[0].revision;
    textures[0].pixels = &colors[2];
    host_d3d_texture(&textures[0]);
    CHECK_EQ(host_d3d_total_textures(), uploads + 1);
    CHECK(renderer->hasTexture(textures[0].handle, textures[0].revision - 1));
    renderer->releaseTexture(textures[0].handle, textures[0].revision - 1);
    CHECK(!renderer->hasTexture(textures[0].handle, textures[0].revision - 1));
    host_d3d_texture(&textures[0]);
    CHECK_EQ(host_d3d_total_textures(), uploads + 1);
    // A real size change allocates once; returning to a free slot of that
    // size reuses its resources and does not carry the old depth contents.
    renderer->setSceneWidth(1280, 960);
    host_d3d_bind_generation(&surface.desc, 999, 18000);
    CHECK_EQ(target_desc(renderer).width, 1280);
    CHECK_EQ(target_desc(renderer).height, 960);
    auto resized = renderer->commandStorageStats();
    CHECK(resized.scene_textures > warmed.scene_textures);
    renderer->clearFlags(3, nullptr, 0, 0xff00ff00, 1);
    host_d3d_seal_frame(18000);
    host_d3d_retire_frame(18000);
    host_d3d_bind_generation(&surface.desc, 1000, 18001);
    CHECK_EQ(renderer->commandStorageStats().scene_textures, resized.scene_textures);
    renderer->clearFlags(3, nullptr, 0, 0xff0000ff, 1);
    host_d3d_seal_frame(18001);
    host_d3d_retire_frame(18001);
    rb = read_target(renderer);
    rb.rgb(2, 2, &r, &g, &b, &a);
    CHECK(b > 200);
    CHECK(g < 40);
    renderer->setSceneWidth(0, 0);
    g_t4_claim_frames = false;
    renderer->discard();
}

static void test_texture_revision_leased_through_frame(D3DRenderer *renderer) {
    Surface surface(21, 64, 64);
    renderer->setRenderTarget(&surface.desc);
    host_render_reset_for_test();

    uint16_t red[1] = {0xf800};
    uint16_t blue[1] = {0x001f};
    HostD3DTexture tex;
    memset(&tex, 0, sizeof tex);
    tex.handle = 7;
    tex.width = 1;
    tex.height = 1;
    tex.pitch = 2;
    tex.bpp = 16;
    tex.rmask = 0xf800;
    tex.gmask = 0x07e0;
    tex.bmask = 0x001f;

    tex.revision = 1;
    tex.pixels = red;
    renderer->uploadTexture(&tex);
    CHECK(host_render_texture_revision_alive_for_test(7, 1));

    // The frame takes its lease at submission, before the guest can upload
    // again.
    renderer->retainTexture(7, 1);

    Command quad;
    quad.cmd.texture_handle = 7;
    quad.state[1] = 7;
    quad.state[21] = 1;          // DECAL
    quad.cmd.primitive_type = 5; // strip
    quad.tlvertex(0, 0, 0.5f, 0xffffffff, 0, 0);
    quad.tlvertex(64, 0, 0.5f, 0xffffffff, 1, 0);
    quad.tlvertex(0, 64, 0.5f, 0xffffffff, 0, 1);
    quad.tlvertex(64, 64, 0.5f, 0xffffffff, 1, 1);

    // The guest uploads a second revision over the same handle BEFORE the
    // draw is executed, which is the whole point: a renderer keyed by handle
    // alone would paint this one.
    tex.revision = 2;
    tex.pixels = blue;
    renderer->uploadTexture(&tex);
    CHECK(host_render_texture_revision_alive_for_test(7, 2));

    renderer->beginScene();
    renderer->clearFlags(3, nullptr, 0, 0xff000000u, 1.0f);
    renderer->draw(&quad.cmd, 1);
    renderer->endScene();

    Readback rb = read_target(renderer);
    int r, g, b, a;
    rb.rgb(10, 10, &r, &g, &b, &a);
    CHECK(r > 200); // red, the revision the draw named
    CHECK(b < 60);

    // A draw naming the newer revision gets the newer pixels, so this is a
    // choice the renderer is making rather than a stale table.
    renderer->beginScene();
    renderer->clearFlags(3, nullptr, 0, 0xff000000u, 1.0f);
    renderer->draw(&quad.cmd, 2);
    renderer->endScene();
    rb = read_target(renderer);
    rb.rgb(10, 10, &r, &g, &b, &a);
    CHECK(b > 200);
    CHECK(r < 60);

    // The old revision is alive because the frame holds it, not because the
    // renderer kept every upload: releasing is what drops it.
    CHECK(host_render_texture_revision_alive_for_test(7, 1));
    renderer->releaseTexture(7, 1);
    CHECK(!host_render_texture_revision_alive_for_test(7, 1));
    // The current revision stays, held by nothing: it is what the next draw
    // that names no revision will sample.
    CHECK(host_render_texture_revision_alive_for_test(7, 2));

    // An upload that supersedes an unheld revision drops it on the spot, so a
    // level of texture animation does not accumulate one texture per frame.
    tex.revision = 3;
    tex.pixels = red;
    renderer->uploadTexture(&tex);
    CHECK(!host_render_texture_revision_alive_for_test(7, 2));
    CHECK(host_render_texture_revision_alive_for_test(7, 3));

    // And destroying the handle takes everything that is not held with it.
    renderer->destroyTexture(7);
    CHECK(!host_render_texture_revision_alive_for_test(7, 3));

    // An upload cannot replace a revision a frame is holding. The shim bumps
    // the revision on every path that changes what a texture samples, so
    // arriving here with leases means some path does not - and overwriting
    // would put new pixels under a frame that has not been composited.
    tex.revision = 9;
    tex.pixels = red;
    renderer->uploadTexture(&tex);
    CHECK(renderer->retainTexture(7, 9));
    tex.pixels = blue;
    renderer->uploadTexture(&tex); // same key, while it is held
    Command probe;
    probe.cmd.texture_handle = 7;
    probe.state[1] = 7;
    probe.state[21] = 1; // DECAL
    probe.cmd.primitive_type = 5;
    probe.tlvertex(0, 0, 0.5f, 0xffffffff, 0, 0);
    probe.tlvertex(64, 0, 0.5f, 0xffffffff, 1, 0);
    probe.tlvertex(0, 64, 0.5f, 0xffffffff, 0, 1);
    probe.tlvertex(64, 64, 0.5f, 0xffffffff, 1, 1);
    renderer->beginScene();
    renderer->clearFlags(3, nullptr, 0, 0xff000000u, 1.0f);
    renderer->draw(&probe.cmd, 9);
    renderer->endScene();
    rb = read_target(renderer);
    rb.rgb(10, 10, &r, &g, &b, &a);
    CHECK(r > 200); // still what the frame holds
    CHECK(b < 60);
    renderer->releaseTexture(7, 9);

    // Asking to hold a revision the renderer never received says so, rather
    // than passing silently and leaving a draw naming pixels it does not have.
    CHECK(!renderer->retainTexture(7, 99));
}

// Exercise real GPU output with channels that cannot survive RGB565.
static void test_render_rgba32(D3DRenderer *renderer) {
    Surface surface(811, 64, 64);
    renderer->setRenderTarget(&surface.desc);
    uint8_t pixels[24] = {19, 73, 141, 255, 93,  157, 211, 255, 0, 0, 0, 0,
                          31, 83, 151, 255, 101, 163, 223, 255, 0, 0, 0, 0};
    HostD3DTexture t{};
    t.handle = 811;
    t.pixels = pixels;
    t.width = t.height = 2;
    t.pitch = 12;
    t.bpp = 32;
    t.rmask = 0xff;
    t.gmask = 0xff00;
    t.bmask = 0xff0000;
    t.amask = 0xff000000;
    Command q;
    q.cmd.texture_handle = 811;
    q.state[1] = 811;
    q.state[21] = 1;
    q.cmd.primitive_type = 5;
    q.tlvertex(0, 0, .5f, 0xffffffff, 0, 0);
    q.tlvertex(64, 0, .5f, 0xffffffff, 1, 0);
    q.tlvertex(0, 64, .5f, 0xffffffff, 0, 1);
    q.tlvertex(64, 64, .5f, 0xffffffff, 1, 1);
    for (int bpp : {32, 24}) {
        t.bpp = bpp;
        renderer->uploadTexture(&t);
        renderer->beginScene();
        renderer->clearFlags(3, nullptr, 0, 0xff000000, 1);
        renderer->draw(&q.cmd);
        renderer->endScene();
        auto rb = read_target(renderer);
        int r, g, b, a;
        rb.rgb(8, 8, &r, &g, &b, &a);
        CHECK_EQ(r, 19);
        CHECK_EQ(g, 73);
        CHECK_EQ(b, 141);
        rb.rgb(56, 56, &r, &g, &b, &a);
        CHECK_EQ(r, 101);
        CHECK_EQ(g, 163);
        CHECK_EQ(b, 223);
    }
    // Alpha lives in the previously ignored fourth byte, including nonbinary alpha.
    for (int y = 0; y < 2; ++y)
        for (int x = 0; x < 2; ++x)
            pixels[y * 12 + x * 4 + 3] = 128;
    t.bpp = 32;
    renderer->uploadTexture(&t);
    q.state[27] = 1;
    q.state[19] = 5;
    q.state[20] = 6;
    renderer->beginScene();
    renderer->clearFlags(3, nullptr, 0, 0xff000000, 1);
    renderer->draw(&q.cmd);
    renderer->endScene();
    auto rb = read_target(renderer);
    int r, g, b, a;
    rb.rgb(8, 8, &r, &g, &b, &a);
    CHECK(abs(r - 10) <= 1);
    CHECK(abs(g - 37) <= 1);
    CHECK(abs(b - 71) <= 1);
    renderer->destroyTexture(811);
}

static void test_render_texture_versioning(D3DRenderer *renderer) {
    Surface surface(9, 64, 64);
    renderer->setRenderTarget(&surface.desc);

    uint16_t red[1] = {0xf800};
    uint16_t green[1] = {0x07e0};
    HostD3DTexture tex;
    memset(&tex, 0, sizeof tex);
    tex.handle = 11;
    tex.width = 1;
    tex.height = 1;
    tex.pitch = 2;
    tex.bpp = 16;
    tex.rmask = 0xf800;
    tex.gmask = 0x07e0;
    tex.bmask = 0x001f;

    tex.pixels = red;
    renderer->uploadTexture(&tex);

    Command left;
    left.cmd.texture_handle = 11;
    left.state[1] = 11;
    left.state[21] = 1; // DECAL
    left.cmd.primitive_type = 5;
    left.tlvertex(0, 0, 0.5f, 0xffffffff, 0, 0);
    left.tlvertex(32, 0, 0.5f, 0xffffffff, 1, 0);
    left.tlvertex(0, 64, 0.5f, 0xffffffff, 0, 1);
    left.tlvertex(32, 64, 0.5f, 0xffffffff, 1, 1);

    Command right = left;
    right.vertices.clear();
    right.tlvertex(32, 0, 0.5f, 0xffffffff, 0, 0);
    right.tlvertex(64, 0, 0.5f, 0xffffffff, 1, 0);
    right.tlvertex(32, 64, 0.5f, 0xffffffff, 0, 1);
    right.tlvertex(64, 64, 0.5f, 0xffffffff, 1, 1);

    renderer->beginScene();
    renderer->clearFlags(3, nullptr, 0, 0xff000000u, 1.0f);
    renderer->draw(&left.cmd); // samples red, not yet executed
    tex.pixels = green;
    renderer->uploadTexture(&tex); // the same handle, new contents
    renderer->draw(&right.cmd);    // samples green
    renderer->endScene();

    Readback rb = read_target(renderer);
    int r, g, b, a;
    rb.rgb(8, 32, &r, &g, &b, &a);
    CHECK_EQ(r, 255);
    CHECK_EQ(g, 0); // the earlier draw kept its red
    rb.rgb(56, 32, &r, &g, &b, &a);
    CHECK_EQ(g, 255);
    CHECK_EQ(r, 0);
    renderer->destroyTexture(11);
}

// The device advertises the mip filters, so the levels have to exist: a
// heavily minified checkerboard has to average rather than pick one texel.
static void test_render_mipmaps(D3DRenderer *renderer) {
    Surface surface(10, 64, 64);
    renderer->setRenderTarget(&surface.desc);

    std::vector<uint16_t> checker(64 * 64);
    for (int y = 0; y < 64; ++y)
        for (int x = 0; x < 64; ++x)
            checker[y * 64 + x] = ((x ^ y) & 1) ? 0xf800 : 0x001f; // red / blue
    HostD3DTexture tex;
    memset(&tex, 0, sizeof tex);
    tex.handle = 12;
    tex.pixels = checker.data();
    tex.width = 64;
    tex.height = 64;
    tex.pitch = 128;
    tex.bpp = 16;
    tex.rmask = 0xf800;
    tex.gmask = 0x07e0;
    tex.bmask = 0x001f;
    renderer->uploadTexture(&tex);

    // The whole texture squeezed into 2x2 pixels, with a mip filter.
    Command quad;
    quad.cmd.texture_handle = 12;
    quad.state[1] = 12;
    quad.state[21] = 1; // DECAL
    quad.state[18] = 6; // TEXTUREMIN = LINEARMIPLINEAR
    quad.cmd.primitive_type = 5;
    quad.tlvertex(0, 0, 0.5f, 0xffffffff, 0, 0);
    quad.tlvertex(2, 0, 0.5f, 0xffffffff, 1, 0);
    quad.tlvertex(0, 2, 0.5f, 0xffffffff, 0, 1);
    quad.tlvertex(2, 2, 0.5f, 0xffffffff, 1, 1);

    renderer->beginScene();
    renderer->clearFlags(3, nullptr, 0, 0xff000000u, 1.0f);
    renderer->draw(&quad.cmd);
    renderer->endScene();
    Readback rb = read_target(renderer);
    int r, g, b, a;
    rb.rgb(0, 0, &r, &g, &b, &a);
    // Half red and half blue averaged: both channels present, neither full.
    CHECK(r > 60 && r < 200);
    CHECK(b > 60 && b < 200);
    renderer->destroyTexture(12);
}

static void test_render_lines_and_points(D3DRenderer *renderer) {
    Surface surface(13, 64, 64);
    renderer->setRenderTarget(&surface.desc);

    Command line;
    line.cmd.primitive_type = 2; // D3DPT_LINELIST
    line.tlvertex(0.5f, 32.5f, 0.5f, 0xffffffff);
    line.tlvertex(63.5f, 32.5f, 0.5f, 0xffffffff);
    renderer->beginScene();
    renderer->clearFlags(3, nullptr, 0, 0xff000000u, 1.0f);
    renderer->draw(&line.cmd);
    renderer->endScene();
    Readback rb = read_target(renderer);
    int r, g, b, a;
    int lit = 0;
    for (int y = 31; y <= 33; ++y) {
        rb.rgb(32, y, &r, &g, &b, &a);
        if (r > 200 && g > 200 && b > 200)
            ++lit;
    }
    CHECK(lit >= 1);
    rb.rgb(32, 8, &r, &g, &b, &a);
    CHECK_EQ(r, 0); // nowhere near the line

    Command point;
    point.cmd.primitive_type = 1; // D3DPT_POINTLIST
    point.tlvertex(32.5f, 32.5f, 0.5f, 0xff00ff00);
    renderer->beginScene();
    renderer->clearFlags(3, nullptr, 0, 0xff000000u, 1.0f);
    renderer->draw(&point.cmd);
    renderer->endScene();
    rb = read_target(renderer);
    lit = 0;
    for (int y = 31; y <= 33; ++y)
        for (int x = 31; x <= 33; ++x) {
            rb.rgb(x, y, &r, &g, &b, &a);
            if (g > 200)
                ++lit;
        }
    CHECK(lit >= 1);
}

static void test_render_rect_clear(D3DRenderer *renderer) {
    Surface surface(14, 64, 64);
    renderer->setRenderTarget(&surface.desc);

    // A clear with a rectangle touches that rectangle and nothing else.
    int32_t rect[4] = {0, 0, 32, 32};
    renderer->beginScene();
    renderer->clearFlags(3, nullptr, 0, 0xff000000u, 1.0f);
    renderer->clearFlags(1, rect, 1, 0xff00ff00u, 1.0f);
    renderer->endScene();
    Readback rb = read_target(renderer);
    int r, g, b, a;
    rb.rgb(16, 16, &r, &g, &b, &a);
    CHECK_EQ(g, 255);
    rb.rgb(48, 48, &r, &g, &b, &a);
    CHECK_EQ(g, 0);
}

// The frame-rate acceptance is a claim about playing, so the sustained window
// is fed only by frames the Direct3D device drew into. The signal is the draw
// count since the last present: the front end renders in software and submits
// none at all.
static void test_gameplay_signal(D3DRenderer *renderer) {
    Surface surface(40, 64, 64);
    renderer->setRenderTarget(&surface.desc);
    host_d3d_note_presented();
    CHECK_EQ(host_d3d_draws_since_present(), 0);

    // A present with no device draw behind it is a front-end frame.
    uint8_t pixels[64 * 64 * 2];
    memset(pixels, 0, sizeof pixels);
    host_present(pixels, 4, 2, 16, nullptr, 8);
    CHECK_EQ(host_d3d_draws_since_present(), 0);

    Command tri;
    tri.triangle(0.5f, 0xffff0000);
    renderer->beginScene();
    renderer->draw(&tri.cmd);
    renderer->endScene();
    CHECK_EQ(host_d3d_draws_since_present(), 1);

    // A present consumes the signal: the next frame's draws are the next
    // frame's, and a single gameplay frame does not make every frame after it
    // look like gameplay too.
    host_present(pixels, 4, 2, 16, nullptr, 8);
    CHECK_EQ(host_d3d_draws_since_present(), 0);
}

// The stats line, which is a FORMAT and not only a number: display_compare.py
// parses this text, so a change here that nobody notices is a baseline that
// stops comparing. One formatter serves both hosts for the same reason.
// This executable has no DirectDraw module. Its service tests submit explicit
// synthetic sealed handles; real seal-hook/retirement coverage is in dx_tests.
static HostFrameHandle g_present_test_current{};
extern "C" HostFrameHandle host_frame_current() {
    return g_present_test_current;
}
extern "C" int host_frame_had_draws(HostFrameHandle) {
    return 0;
}
extern "C" void ddraw_set_present_callbacks(void (*)(), void (*)()) {}
extern "C" void ddraw_present_release(HostFrameHandle) {}
extern "C" void ddraw_drain_present_releases() {}

// DISP-T8: deterministic display-link/offscreen substitute. These assertions
// run without a Metal device; the production GPU path is covered separately.
#include "../present_test.h"
#if __has_include("../ui_layer.h")
#include "../ui_layer.h"
#else
#include "compositor_ui_double.h"
#endif
#include <atomic>
#include <chrono>
#include <future>
#include <thread>

// The old tests called tick directly and could never catch a worker that
// ignored seal notifications. Drive the real window wake predicate instead.
static void test_windowed_first_blit_presents_without_prior_completion() {
    host_present_test_begin(false, false);
    CHECK(!host_present_test_window_wake(0));
    test_frame_builder frame;
    frame.screen_class = HOST_SCREEN_MENU;
    frame.screen_class_valid = false;
    frame.revision({4, 1}, 2, 2, 8, {1, 1, 1, 1});
    frame.blit(4, 0, 0, 2, 2);
    g_present_test_current = frame.frame;
    host_present_first_write();
    uint8_t indexed[4] = {1, 1, 1, 1};
    uint32_t palette[256]{};
    palette[1] = 0x002a1307;
    // Production 8-bpp callback, with no Direct3D draws or GPU prefixes.
    host_present(indexed, 2, 2, 8, palette, 2);
    host_frame_seal();
    CHECK(frame.balanced());
    CHECK_EQ(host_present_unique_completed(), 0u);
    // The first frame must be submitted even if the display link never fires.
    CHECK(host_present_test_window_wake(0));
    CHECK_EQ(host_present_test_in_flight(), frame.frame.id);
    CHECK(host_present_test_input_legacy()); // complete staged menu image
    CHECK(!host_present_test_released(frame.frame.id));
    host_present_test_command_done(frame.frame.id);
    CHECK_EQ(host_present_unique_completed(), 0u); // GPU done is not shown
    host_present_test_presented(frame.frame.id, 0.01);
    CHECK_EQ(host_present_unique_completed(), 1u);
    CHECK_EQ(host_present_test_last_pixel(), 42);
    CHECK(!host_present_test_class_valid());
    frame.screen_class_valid = true; // Later producer state cannot relabel a repeat.
    CHECK_EQ(host_present_drops(), 0u);
    // A fake display-link callback then drives the ordinary repeat path.
    CHECK(host_present_test_window_wake(0.02, true));
    CHECK_EQ(host_present_repeats(), 1u);
    host_present_test_command_done(frame.frame.id);
    host_present_test_presented(frame.frame.id, 0.03);
    CHECK_EQ(host_present_unique_completed(), 1u);
    CHECK(!host_present_test_class_valid());
    host_present_stop();
    g_present_test_current = {};
}
// A gpu::FakeDevice exercises the production handler registration / present /
// commit path. A silent fake display link advances only the worker's deadline.
struct PresentFake {
    gpu::FakeDevice device;
    gpu::Swapchain chain;
    PresentFake() {
        device.set_manual_completion(true);
        chain = device.create_swapchain(nullptr, 4, 4);
        host_present_set_device(&device);
    }
    ~PresentFake() {
        host_present_stop();
        host_present_set_device(g_gpu.get());
    }
    void commit() {
        host_present_test_commit_swapchain(chain);
    }
    void complete(bool success = true) {
        device.set_completion_status(success ? gpu::CommandStatus::Completed
                                             : gpu::CommandStatus::Error);
        device.complete_all();
    }
    void presented(double ts) {
        device.fire_presented(ts);
    }
};
static void test_windowed_duration_pacing_selector() {
    const char *saved = recomp_env("HOST_PRESENT_PACING");
    std::string old = saved ? saved : "";
    bool had = saved != nullptr;
    for (bool paced : {false, true}) {
        os_setenv("RECOMP_HOST_PRESENT_PACING", paced ? "duration" : "immediate");
        PresentFake fake;
        host_present_test_begin(false, false);
        host_present_test_seal(1, HOST_SCREEN_MENU, false);
        CHECK(host_present_test_window_wake(0));
        fake.commit();
        CHECK_NEAR(fake.device.last_present_min_duration(), paced ? 1.0 / 60 : 0, 1e-12);
        fake.complete();
        CHECK_EQ(host_present_unique_completed(), 0u);
        // Real queued frames can present later than the old two-refresh
        // timeout. Keep their storage and count their real acknowledgement.
        CHECK(host_present_test_window_wake(2.5 / 60, false, true));
        CHECK_EQ(host_present_unique_completed(), 0u);
        CHECK_EQ(host_present_faults(), 0u);
        CHECK(!host_present_test_released(1));
        fake.presented(2.6 / 60);
        CHECK_EQ(host_present_unique_completed(), 1u);
        host_present_stop();
    }
    if (had)
        os_setenv("RECOMP_HOST_PRESENT_PACING", old.c_str());
    else
        os_unsetenv("RECOMP_HOST_PRESENT_PACING");
}
static void test_windowed_drawable_handler_and_completion_fallback() {
    for (bool missing : {false, true}) {
        PresentFake fake;
        host_present_test_begin(false, false);
        host_present_test_seal(1, HOST_SCREEN_MENU, false);
        CHECK(host_present_test_window_wake(0));
        fake.commit();
        fake.complete(); // production command handler at t=0; a zero presented
                         // time arrives with it and must not acknowledge
        CHECK_EQ(host_present_unique_completed(), 0u);
        if (!missing) {
            fake.presented(0.01);
            CHECK_EQ(host_present_unique_completed(), 1u);
            CHECK_EQ(host_present_faults(), 0u);
        }
        host_present_test_seal(2, HOST_SCREEN_MENU, false);
        if (missing) {
            CHECK(!host_present_test_window_wake(0.01));
            CHECK(host_present_test_window_wake(3.0 / 60 - 0.00001, false, true));
            CHECK_EQ(host_present_unique_completed(), 0u); // no premature acknowledgement
            CHECK(!host_present_test_released(1));
            CHECK(host_present_test_window_wake(3.0 / 60, false, true)); // no link tick
            CHECK_EQ(host_present_unique_completed(), 1u);
            CHECK_EQ(host_present_faults(), 1u);
            char stats[1024];
            host_stats_gameplay_line(stats, sizeof stats);
            CHECK(strstr(stats, " faults=1") != nullptr);
            CHECK_EQ(host_present_test_in_flight(), 2u);
            // Deliver the original registered callback after slot reuse.
            fake.presented(0.06);
            fake.presented(0.06);
            CHECK_EQ(host_present_unique_completed(), 1u);
            CHECK_EQ(host_present_test_in_flight(), 2u);
            host_present_test_command_done(2);
            CHECK(host_present_test_window_wake(6.0 / 60, false, true));
            CHECK_EQ(host_present_unique_completed(), 2u);
            CHECK_EQ(host_present_faults(), 1u); // fallback logs once per service
        } else {
            CHECK(host_present_test_window_wake(0.02));
            host_present_test_presented(2, 0.025); // drawable first, GPU second
            CHECK_EQ(host_present_unique_completed(), 1u);
            host_present_test_command_done(2);
            CHECK_EQ(host_present_unique_completed(), 2u);
        }
        CHECK(host_present_test_released(1));
        CHECK_EQ(host_present_drops(), 0u);
        CHECK_EQ(host_present_waits(), 0u);
        host_present_stop();
    }
    // A deadline cannot release GPU/prefix-owned storage. Slow GPU completion
    // earns a new display grace; its real callback needs no display-link tick.
    for (bool success : {false, true}) {
        PresentFake fake;
        host_present_test_begin(false, false);
        host_present_test_seal(1, HOST_SCREEN_MENU, false, true);
        CHECK(host_present_test_window_wake(0));
        fake.commit();
        CHECK(host_present_test_window_wake(2.0 / 60, false, true));
        CHECK_EQ(host_present_unique_completed(), 0u);
        CHECK(!host_present_test_released(1));
        CHECK(host_present_test_window_wake(1.1, false, true));
        CHECK(host_present_test_faults() != 0);
        CHECK_EQ(host_present_test_in_flight(), 1u);
        fake.complete(success);
        CHECK_EQ(host_present_unique_completed(), 0u);
        CHECK(!host_present_test_released(1));
        host_present_test_prefix_done(1);
        if (success) {
            CHECK(!host_present_test_released(1));
            CHECK_EQ(host_present_unique_completed(), 0u);
            fake.presented(1.11);
        }
        CHECK(host_present_test_released(1));
        CHECK_EQ(host_present_unique_completed(), success ? 1u : 0u);
        CHECK_EQ(host_present_test_in_flight(), 0u);
        host_present_stop();
    }
}
static void test_newest_sealed_wins_and_oldest_drops() {
    host_present_test_begin();
    host_present_test_seal(1);
    host_present_test_seal(2);
    host_present_test_seal(3);
    CHECK_EQ(host_present_drops(), 1u);
    CHECK(host_present_test_released(1));
    host_present_tick_for_test(0);
    CHECK_EQ(host_present_test_last_id(), 3u);
    CHECK_EQ(host_present_drops(), 2u); // B is superseded at the tick as well.
    CHECK(host_present_test_released(2));
    host_present_stop();
}
static void test_repeat_counted_not_completed() {
    host_present_test_begin();
    host_present_test_seal(1);
    host_present_tick_for_test(0);
    host_present_tick_for_test(1.0 / 120);
    CHECK_EQ(host_present_repeats(), 1u);
    CHECK_EQ(host_present_unique_completed(), 1u);
    host_present_stop();
}
static void test_frame_immutable_after_seal() {
    host_present_test_begin();
    uint8_t pixel[4] = {42, 0, 0, 255};
    host_present_stage_rgba(pixel, 1, 1);
    host_present_test_seal(1);
    pixel[0] = 99;
    host_present_stage_rgba(pixel, 1, 1);
    host_present_tick_for_test(0);
    CHECK_EQ(host_present_test_last_pixel(), 42);
    host_present_stop();
}
static void test_release_after_completion_and_presentation() {
    host_present_test_begin(false);
    host_present_test_seal(1);
    host_present_tick_for_test(0);
    host_present_test_command_done(1);
    CHECK(!host_present_test_released(1));
    CHECK_EQ(host_present_unique_completed(), 0u);
    host_present_test_presented(1, 0.01);
    CHECK(host_present_test_released(1));
    CHECK_EQ(host_present_unique_completed(), 1u);
    // Reverse ordering, duplicate notifications, and failed-to-present stalls.
    host_present_test_seal(2);
    host_present_tick_for_test(0.02);
    host_present_test_presented(2, 0.02);
    CHECK(!host_present_test_released(2));
    host_present_test_command_done(2);
    host_present_test_command_done(2);
    CHECK_EQ(host_present_unique_completed(), 2u);
    host_present_stop();
}
static void test_dropped_prefix_lifetime() {
    host_present_test_begin();
    host_present_test_seal(1, HOST_SCREEN_GAMEPLAY, true, true);
    host_present_test_seal(2);
    host_present_test_seal(3);
    CHECK_EQ(host_present_drops(), 1u);
    CHECK(!host_present_test_released(1));
    host_present_test_prefix_done(1);
    CHECK(host_present_test_released(1));
    host_present_stop();
}
static void test_two_display_frames_overlap_and_retire_in_order() {
    host_present_test_begin(false, false, 2);
    uint8_t first[4] = {17, 0, 0, 255}, second[4] = {42, 0, 0, 255};
    host_present_stage_rgba(first, 1, 1);
    host_present_test_seal(1);
    CHECK(host_present_test_window_wake(0));
    host_present_test_command_done(1);
    // The following refresh arrives before the previous presented callback.
    host_present_stage_rgba(second, 1, 1);
    host_present_test_seal(2, HOST_SCREEN_GAMEPLAY, true, true);
    CHECK(host_present_test_window_wake(1.0 / 120, true));
    CHECK_EQ(host_present_test_flight_count(), 2u);
    CHECK_EQ(host_present_unique_completed(), 0u);
    host_present_test_command_done(2);
    host_present_test_presented(2, 2.0 / 120);
    CHECK_EQ(host_present_unique_completed(), 0u); // callbacks can arrive reversed
    CHECK(!host_present_test_released(2));
    host_present_test_presented(1, 1.0 / 120);
    CHECK_EQ(host_present_unique_completed(), 1u);
    CHECK_EQ(host_present_test_last_pixel(), 17);
    CHECK(!host_present_test_released(2)); // GPU prefix still owns the source
    host_present_test_prefix_done(2);
    CHECK_EQ(host_present_unique_completed(), 2u);
    CHECK_EQ(host_present_test_last_id(), 2u);
    CHECK_EQ(host_present_test_last_pixel(), 42);
    CHECK_EQ(host_present_test_flight_count(), 0u);
    host_present_test_presented(1, 3.0 / 120); // late duplicate cannot restore frame 1
    CHECK_EQ(host_present_test_last_id(), 2u);
    CHECK_EQ(host_present_drops(), 0u);

    host_present_test_seal(3);
    CHECK(host_present_test_window_wake(3.0 / 120));
    host_present_test_command_done(3);
    CHECK(host_present_test_window_wake(4.0 / 120, true));
    CHECK_EQ(host_present_test_flight_count(), 1u);
    CHECK_EQ(host_present_repeats(), 0u); // never enqueue cached frame 2 behind 3
    host_present_test_seal(4);
    CHECK(host_present_test_window_wake(4.1 / 120));
    CHECK_EQ(host_present_test_flight_count(), 2u);
    host_present_test_seal(5);
    CHECK(!host_present_test_window_wake(4.2 / 120));
    CHECK_EQ(host_present_test_flight_count(), 2u); // bounded even when GPU stalls
    host_present_stop(); // drains both pending frames without claiming display
    CHECK(host_present_test_released(3));
    CHECK(host_present_test_released(4));
    CHECK(host_present_test_released(5));
    CHECK_EQ(host_present_unique_completed(), 2u);
}
static void test_three_display_frames_overlap_and_retire_in_order() {
    host_present_test_begin(false, false, 3);
    for (uint64_t id = 1; id <= 3; ++id) {
        uint8_t pixel[4] = {uint8_t(id * 17), 0, 0, 255};
        host_present_stage_rgba(pixel, 1, 1);
        host_present_test_seal(id, HOST_SCREEN_GAMEPLAY, true, id == 2);
        CHECK(host_present_test_window_wake((id - 1.0) / 120));
        CHECK_EQ(host_present_test_flight_count(), id);
    }
    host_present_test_seal(4);
    CHECK(!host_present_test_window_wake(2.1 / 120));
    CHECK_EQ(host_present_test_flight_count(), 3u);
    CHECK_EQ(host_present_unique_completed(), 0u);
    // The last frame's callbacks cannot release or publish older GPU sources.
    host_present_test_command_done(3);
    host_present_test_presented(3, 3.0 / 120);
    host_present_test_command_done(2);
    host_present_test_presented(2, 2.0 / 120);
    CHECK_EQ(host_present_unique_completed(), 0u);
    CHECK(!host_present_test_released(2));
    CHECK(!host_present_test_released(3));
    host_present_test_command_done(1);
    host_present_test_presented(1, 1.0 / 120);
    CHECK_EQ(host_present_unique_completed(), 1u);
    CHECK_EQ(host_present_test_last_pixel(), 17);
    CHECK(!host_present_test_released(2)); // independent scene prefix still runs
    CHECK(!host_present_test_released(3));
    host_present_test_prefix_done(2);
    CHECK_EQ(host_present_unique_completed(), 3u);
    CHECK_EQ(host_present_test_last_id(), 3u);
    CHECK_EQ(host_present_test_last_pixel(), 51);
    CHECK_EQ(host_present_test_flight_count(), 0u);
    CHECK(host_present_test_window_wake(3.1 / 120)); // queued fourth frame can run
    CHECK_EQ(host_present_test_flight_count(), 1u);
    CHECK_EQ(host_present_repeats(), 0u);
    host_present_test_presented(1, 4.0 / 120); // duplicate does not regress history
    CHECK_EQ(host_present_test_last_id(), 3u);
    host_present_stop();
    CHECK(host_present_test_released(4));
    CHECK_EQ(host_present_unique_completed(), 3u); // shutdown is not presentation
}

static void test_acquire_waits_for_retained_target() {
    host_present_test_begin(false);
    host_present_test_seal(1);
    host_present_tick_for_test(0);
    host_present_test_seal(2, HOST_SCREEN_GAMEPLAY, true, true);
    host_present_test_seal(3);
    host_present_test_seal(4);
    CHECK_EQ(host_present_drops(), 1u); // completed mailbox frame, not guest writes
    auto writer = std::async(std::launch::async,
                             [] { return host_present_acquire_target(640, 480, 640, 480).w; });
    CHECK(writer.wait_for(std::chrono::milliseconds(20)) == std::future_status::timeout);
    host_present_test_prefix_done(2);
    CHECK_EQ(writer.get(), 640);
    CHECK_EQ(host_present_waits(), 1u);
    CHECK_EQ(host_present_drops(), 1u);
    CHECK_EQ(host_present_test_faults(), 0u);
    host_present_stop();
}

static void test_acquire_faults_when_all_targets_stall() {
    host_present_test_begin(false);
    host_present_test_seal(1);
    host_present_tick_for_test(0);
    host_present_test_seal(2, HOST_SCREEN_GAMEPLAY, true, true);
    host_present_test_seal(3);
    host_present_test_seal(4);
    CHECK_EQ(host_present_drops(), 1u); // 2 is dropped but its prefix still owns a slot
    auto writer = std::async(std::launch::async,
                             [] { return host_present_acquire_target(640, 480, 640, 480).w; });
    bool finished = writer.wait_for(std::chrono::seconds(2)) == std::future_status::ready;
    CHECK(finished);
    if (!finished)
        host_present_test_prefix_done(2); // fail without hanging
    CHECK_EQ(writer.get(), 0);
    CHECK_EQ(host_present_waits(), 1u);
    CHECK_EQ(host_present_drops(), 2u);
    char stats[768];
    CHECK(host_stats_gameplay_line(stats, sizeof stats) > 0);
    CHECK(strstr(stats, "drops=2") != nullptr);
    host_present_test_prefix_done(2);
    CHECK_EQ(host_present_acquire_target(640, 480, 640, 480).w, 0); // latched until seal
    host_present_test_seal(5);
    CHECK_EQ(host_present_drops(), 2u);
    CHECK(host_present_test_released(5));
    CHECK_EQ(host_present_acquire_target(640, 480, 640, 480).w, 640);
    host_present_stop();
}
static void feed_presenter_seconds(int seconds, bool slow = true, int slow_second = 22,
                                   int slow_count = 100, double offset = 0) {
    uint64_t id = 1;
    for (int second = 0; second < seconds; ++second) {
        for (int frame = 0; frame < 120; ++frame) {
            if (!slow || second != slow_second || frame < slow_count)
                host_present_test_seal(id++);
            host_present_tick_for_test(offset + second + frame / 120.0);
        }
    }
    host_present_tick_for_test(offset + seconds);
}
static void test_continuous_metric_needs_40s_and_reports_min_bucket() {
    double minimum = -1, elapsed = -1;
    host_present_test_begin();
    feed_presenter_seconds(30);
    CHECK_EQ(host_metric_continuous(&minimum, &elapsed), 0);
    host_present_stop();
    host_present_test_begin();
    feed_presenter_seconds(45);
    CHECK_EQ(host_metric_continuous(&minimum, &elapsed), 1);
    CHECK_EQ(minimum, 100);
    CHECK_NEAR(elapsed, 45, 0.001);
    CHECK_EQ(host_metric_throughput(&minimum, &elapsed), 0);
    host_present_stop();
    // Display-link alignment is relative to its first timestamp, including a
    // fractional phase. A slow warm-up bucket is outside the measurement.
    host_present_test_begin();
    feed_presenter_seconds(40, true, 5, 17, 0.375);
    CHECK_EQ(host_metric_continuous(&minimum, &elapsed), 1);
    CHECK_EQ(minimum, 120);
    host_present_stop();
    host_present_test_begin(true, true);
    feed_presenter_seconds(45);
    CHECK_EQ(host_metric_continuous(&minimum, &elapsed), 0);
    CHECK_EQ(host_metric_throughput(&minimum, &elapsed), 1);
    CHECK_EQ(minimum, 100);
    host_present_stop();
}
// A renderer restart can select a larger output without shrinking its UI or
// changing input coordinates. Requests affect the next writer, never a lease.
static void test_presenter_render_resolution() {
    host_present_test_begin();
    host_set_render_resolution(3840, 2160);
    auto first = host_present_acquire_target(1280, 720, 0, 0);
    CHECK_EQ(first.w, 3840);
    CHECK_EQ(first.h, 2160);
    host_set_render_resolution(1920, 1080);
    CHECK_EQ(host_present_acquire_target(1280, 720, 0, 0).w, 3840);
    host_present_test_seal(1);
    auto second = host_present_acquire_target(1280, 720, 0, 0);
    CHECK_EQ(second.w, 1920);
    CHECK_EQ(second.h, 1080);
    CHECK(second.world != first.world);
    host_present_test_seal(2);
    // An explicit scene request has precedence over the default output size.
    auto explicit_size = host_present_acquire_target(1280, 720, 2560, 1440);
    CHECK_EQ(explicit_size.w, 2560);
    CHECK_EQ(explicit_size.h, 1440);
    host_present_stop();
    host_set_render_resolution(0, 0);
    host_present_test_begin();
    auto automatic = host_present_acquire_target(1280, 720, 0, 0);
    host_present_stop();
    host_set_render_resolution(20000, 2160);
    host_present_test_begin();
    auto invalid = host_present_acquire_target(1280, 720, 0, 0);
    CHECK_EQ(invalid.w, automatic.w);
    CHECK_EQ(invalid.h, automatic.h);
    host_present_stop();
    host_set_render_resolution(0, 0);
}
static void test_presenter_allocation_halving() {
    host_present_test_begin();
    host_present_test_fail_allocations(1);
    auto target = host_present_acquire_target(640, 480, 3840, 2160);
    CHECK_EQ(target.w, 1920);
    CHECK_EQ(target.h, 1080);
    CHECK_EQ(host_present_test_requested_width(), 3840);
    host_present_stop();
    host_present_test_begin();
    host_present_test_fail_allocations(3);
    target = host_present_acquire_target(640, 480, 3840, 2160);
    CHECK_EQ(target.w, 640);
    CHECK_EQ(target.h, 480);
    host_present_stop();
    host_present_test_begin();
    host_present_test_fail_allocations(4);
    target = host_present_acquire_target(640, 480, 3840, 2160);
    CHECK_EQ(target.w, 0);
    CHECK_EQ(host_present_test_requested_width(), 3840);
    host_present_test_seal(1);
    CHECK_EQ(host_present_drops(), 1u);
    CHECK(host_present_test_released(1));
    host_present_stop();
}
static void test_presenter_layout_and_transitions() {
    host_present_test_begin();
    UiFrame ui{};
    ui.guest_w = 640;
    ui.guest_h = 480;
    UiElement e{};
    e.id = 7;
    e.last_seq = 23;
    e.x = 608;
    e.y = 448;
    e.w = 32;
    e.h = 32;
    ui.elements.push_back(e);
    host_present_acquire_target(640, 480, 640, 480);
    CompositorInput in{};
    in.ui = &ui;
    in.guest_w = 640;
    in.guest_h = 480;
    in.cls = HOST_SCREEN_GAMEPLAY;
    in.scene = {6, 4.5, 0, 0, 640};
    host_present_set_input(&in);
    host_present_resize(3840, 2160);
    host_present_test_seal(1);
    ui.elements.clear();
    host_present_tick_for_test(0);
    LayoutSnapshot snapshot;
    CHECK(host_present_copy_layout(&snapshot));
    CHECK_EQ(snapshot.elements.size(), 1u);
    CHECK_EQ(snapshot.elements[0].drawable.x, 3712);
    CHECK_EQ(snapshot.elements[0].drawable.y, 2032);
    CHECK_EQ(snapshot.elements[0].last_seq, 23);
    CHECK_EQ(snapshot.ui_scale, 4);
    host_present_resize(1920, 1080);
    host_present_tick_for_test(0.5);
    LayoutSnapshot resized;
    CHECK(host_present_copy_layout(&resized));
    CHECK_EQ(resized.drawable_w, 1920);
    CHECK_EQ(resized.ui_scale, 2);
    CHECK_EQ(resized.elements[0].drawable.x, 1856);
    CHECK_EQ(resized.elements[0].drawable.y, 1016);
    CHECK_EQ(host_present_unique_completed(), 1u);
    CHECK_EQ(host_present_repeats(), 1u);
    auto epoch = host_present_transition_epoch();
    host_present_test_seal(2, HOST_SCREEN_MENU);
    host_present_tick_for_test(1);
    CHECK_EQ(host_present_transition_epoch(), epoch + 1);
    CHECK_EQ(snapshot.elements[0].id, 7u); // original value survives retirement
    mods_present_level_end();
    CHECK_EQ(host_present_transition_epoch(), epoch + 2);
    host_present_stop();
}
static void test_presenter_history_keeps_target_lease() {
    host_present_test_begin();
    auto first = host_present_acquire_target(640, 480, 640, 480);
    CompositorInput input{};
    input.cls = HOST_SCREEN_GAMEPLAY;
    input.guest_w = 640;
    input.guest_h = 480;
    input.world = first.world;
    input.overlay = first.overlay;
    host_present_set_input(&input);
    host_present_test_seal(1);
    host_present_tick_for_test(0);
    CHECK(host_present_test_released(1)); // arena can retire while its target is pinned
    auto second = host_present_acquire_target(640, 480, 640, 480);
    CHECK(second.world != first.world);
    input.world = second.world;
    input.overlay = second.overlay;
    host_present_set_input(&input);
    host_present_test_seal(2, HOST_SCREEN_GAMEPLAY, false);
    host_present_tick_for_test(1);
    CHECK_EQ(host_present_scene_reused(), 1u);
    host_present_tick_for_test(2);
    CHECK_EQ(host_present_scene_reused(), 1u);
    auto third = host_present_acquire_target(640, 480, 640, 480);
    CHECK(third.world != first.world);
    host_present_test_seal(3, HOST_SCREEN_MENU);
    host_present_tick_for_test(3);
    auto after_transition = host_present_acquire_target(640, 480, 640, 480);
    CHECK(after_transition.world == first.world);
    host_present_stop();
}
static void test_presenter_shutdown_cancels_without_counting() {
    host_present_test_begin(false);
    host_present_test_seal(1);
    host_present_tick_for_test(0);
    host_present_test_command_done(1);
    CHECK_EQ(host_present_unique_completed(), 0u);
    host_present_stop();
    CHECK(host_present_test_released(1));
    CHECK_EQ(host_present_unique_completed(), 0u);
}
static void test_presenter_migration_cancels_old_drawable() {
    host_present_test_begin(false);
    host_present_test_seal(1);
    host_present_tick_for_test(0);
    host_present_install_surface(nullptr, 1920, 1080);
    host_present_tick_for_test(0.01);
    CHECK_EQ(host_present_drops(), 1u);
    CHECK(!host_present_test_released(1));
    host_present_test_command_done(1);
    CHECK(host_present_test_released(1));
    CHECK_EQ(host_present_unique_completed(), 0u);
    host_present_test_seal(2);
    host_present_tick_for_test(0.02);
    host_present_test_command_done(2);
    host_present_test_presented(2, 0.03);
    CHECK_EQ(host_present_unique_completed(), 1u);
    CHECK_EQ(host_present_test_last_id(), 2u);
    host_present_stop();
}

static void test_presenter_real_offscreen(D3DRenderer *renderer) {
    // Drive the GDI seam through a real offscreen target; source mutation
    // after publication must not change the completed frame.
    host_present_start_offscreen(2, 2);
    uint32_t gdi_pixels[4] = {0xffff0000, 0xff00ff00, 0xff0000ff, 0xffffffff};
    host_display_present_window(gdi_pixels, 2, 2);
    gdi_pixels[0] = 0;
    auto gdi_deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
    while (host_present_unique_completed() == 0 && std::chrono::steady_clock::now() < gdi_deadline)
        std::this_thread::yield();
    CHECK_EQ(host_present_unique_completed(), 1u);
    uint8_t gdi_rgba[16] = {};
    CHECK(host_present_test_read_rgba(gdi_rgba, sizeof gdi_rgba));
    CHECK_EQ(gdi_rgba[0], 255u);
    CHECK_EQ(gdi_rgba[1], 0u);
    CHECK_EQ(gdi_rgba[5], 255u);
    CHECK_EQ(gdi_rgba[10], 255u);
    host_present_stop();
    host_present_start_offscreen(4, 4);
    uint8_t rgba[64];
    for (int i = 0; i < 16; ++i) {
        rgba[i * 4] = 207;
        rgba[i * 4 + 1] = 19;
        rgba[i * 4 + 2] = 51;
        rgba[i * 4 + 3] = 255;
    }
    host_present_stage_rgba(rgba, 4, 4);
    host_present_test_seal(100, HOST_SCREEN_MENU, false);
    memset(rgba, 0, sizeof rgba);
    host_present_stage_rgba(rgba, 4, 4);
    auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
    while (host_present_unique_completed() == 0 && std::chrono::steady_clock::now() < deadline)
        std::this_thread::yield();
    CHECK_EQ(host_present_unique_completed(), 1u);
    uint8_t result[64]{};
    CHECK(host_present_test_read_rgba(result, sizeof result));
    for (int i = 0; i < 16; ++i) {
        CHECK_EQ(result[i * 4], 207);
        CHECK_EQ(result[i * 4 + 1], 19);
        CHECK_EQ(result[i * 4 + 2], 51);
    }
    double minimum = 0, elapsed = 0;
    CHECK_EQ(host_metric_continuous(&minimum, &elapsed), 0);
    host_present_stop();
}

// GPU copies preserve their source byte order, including when a pooled frame
// switches between Direct3D's BGRA and the CPU/2D paths' RGBA pixels.
static void test_presenter_gpu_color_order(D3DRenderer *) {
    host_present_start_offscreen(2, 2);
    const uint8_t rgba[16] = {255, 0, 0, 255, 0, 255, 0, 255, 0, 0, 255, 255, 207, 193, 51, 255};
    uint8_t bgra[16];
    for (int i = 0; i < 4; ++i) {
        bgra[i * 4] = rgba[i * 4 + 2];
        bgra[i * 4 + 1] = rgba[i * 4 + 1];
        bgra[i * 4 + 2] = rgba[i * 4];
        bgra[i * 4 + 3] = 255;
    }
    // Four frames per path exercise every slot, then reuse them at the same
    // dimensions with a different format (a resize alone cannot catch this).
    for (uint64_t id = 1; id <= 16; ++id) {
        const unsigned path = unsigned((id - 1) / 4);
        gpu::Texture source{};
        if (path == 3) {
            host_present_stage_rgba(rgba, 2, 2);
        } else {
            const auto format = path == 1 ? gpu::Format::RGBA8 : gpu::Format::BGRA8;
            source = g_gpu->create_texture({2, 2, format, gpu::UsageSampled | gpu::UsageCpu});
            CHECK(bool(source));
            CHECK(g_gpu->upload(source, {0, 0, 2, 2}, path == 1 ? rgba : bgra, 8));
            auto cb = g_gpu->begin();
            CHECK(host_present_stage_texture(source, 2, 2, 2, 2, cb));
            g_gpu->commit(cb);
            g_gpu->wait(cb);
        }
        const uint64_t before = host_present_unique_completed();
        host_present_test_seal(id, HOST_SCREEN_MENU, false);
        auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
        while (host_present_unique_completed() == before &&
               std::chrono::steady_clock::now() < deadline)
            std::this_thread::yield();
        CHECK_EQ(host_present_unique_completed(), before + 1);
        uint8_t shown[16]{};
        CHECK(host_present_test_read_rgba(shown, sizeof shown));
        for (int i = 0; i < 16; ++i)
            CHECK_EQ(shown[i], rgba[i]);
        if (source)
            g_gpu->destroy(source);
    }
    host_present_stop();
}

// Supersampling changes texture storage, not mouse coordinates. Use the
// tablet's drawable and fractional render scale that previously put its
// centre at guest (1520,855), outside a 1280x720 client area.
static void test_presenter_gpu_logical_coordinates(D3DRenderer *) {
    host_gate_reset();
    host_present_start_offscreen(2420, 1668);
    for (uint64_t id = 1; id <= 2; ++id) {
        const int w = id == 1 ? 3040 : 1280, h = id == 1 ? 1710 : 720;
        auto source =
            g_gpu->create_texture({w, h, gpu::Format::BGRA8, gpu::UsageSampled | gpu::UsageCpu});
        CHECK(bool(source));
        std::vector<uint8_t> pixels(size_t(w) * h * 4, 0xff);
        CHECK(g_gpu->upload(source, {0, 0, w, h}, pixels.data(), w * 4));
        auto cb = g_gpu->begin();
        CHECK(host_present_stage_texture(source, w, h, 1280, 720, cb));
        g_gpu->commit(cb);
        g_gpu->wait(cb);
        const auto before = host_present_unique_completed();
        host_present_test_seal(id, HOST_SCREEN_MENU, false);
        const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
        while (host_present_unique_completed() == before &&
               std::chrono::steady_clock::now() < deadline)
            std::this_thread::yield();
        CHECK_EQ(host_present_unique_completed(), before + 1);
        LayoutSnapshot layout;
        CHECK(host_present_copy_layout(&layout));
        CHECK_EQ(layout.guest_w, 1280);
        CHECK_EQ(layout.guest_h, 720);
        // Input follows the published frame, including the letterbox offset.
        auto centre = host_gate_hit_test(nullptr, 1210, 834);
        CHECK_EQ(centre.gx, 640);
        CHECK_EQ(centre.gy, 360);
        auto corner = host_gate_hit_test(nullptr, 2419, 1513);
        CHECK(corner.gx >= 1278 && corner.gx < 1280);
        CHECK(corner.gy >= 718 && corner.gy < 720);
        g_gpu->destroy(source);
    }
    host_present_stop();
    host_gate_reset();
}

// The Direct3D 11 hardware path's host side on the real device: a texture
// drawn a texel a pixel into a cleared target, then blended; read back, and
// published through the presenter as a window frame.
static void test_gpu2d_pixels(D3DRenderer *) {
    host_present_start_offscreen(4, 4);
    CHECK_EQ(host_gpu2d_available(), 1);
    const uint32_t tex = 0x7001, target = 0x7002;
    const uint8_t texels[16] = {255, 0, 0, 255, 0, 255, 0, 255, 0, 0, 255, 255, 200, 100, 50, 128};
    host_gpu2d_texture(tex, 2, 2, texels, 0, 0, 2, 2);
    const float grey[4] = {0.5f, 0.5f, 0.5f, 1};
    host_gpu2d_clear(target, 4, 4, grey);
    HostGpu2DQuad copy{1, 1, 2, 2, 0, 0, 1, 1, 0, 1, 0, 1, 0};
    CHECK_EQ(host_gpu2d_draw(target, 4, 4, tex, &copy), 1);
    // The bottom-right texel alone, over the top-left pixel, alpha-blended.
    HostGpu2DQuad blend{0, 0, 1, 1, 0.5, 0.5, 0.5, 0.5, 1, 2, 3, 0, 1};
    CHECK_EQ(host_gpu2d_draw(target, 4, 4, tex, &blend), 1);
    CHECK_EQ(host_gpu2d_draw(target, 4, 4, 0x7fff, &copy), 0); // no such texture
    uint8_t out[64] = {};
    CHECK_EQ(host_gpu2d_readback(target, 4, 4, out), 1);
    auto at = [&](int x, int y) { return out + (y * 4 + x) * 4; };
    for (int y = 0; y < 4; ++y)
        for (int x = 0; x < 4; ++x) {
            const uint8_t *p = at(x, y);
            if (x == 0 && y == 0) {
                // 200 * 128/255 + 128 * 127/255, and so on, to within rounding.
                CHECK(std::abs(int(p[0]) - 164) <= 1);
                CHECK(std::abs(int(p[1]) - 114) <= 1);
                CHECK(std::abs(int(p[2]) - 89) <= 1);
                continue;
            }
            const bool inside = x >= 1 && x <= 2 && y >= 1 && y <= 2;
            const uint8_t *want = inside ? texels + ((y - 1) * 2 + (x - 1)) * 4 : nullptr;
            for (int c = 0; c < 3; ++c)
                CHECK(std::abs(int(p[c]) - int(want ? want[c] : 128)) <= (want ? 0 : 1));
        }
    // A window frame straight from the target.
    const uint64_t before = host_present_unique_completed();
    host_display_present_gpu2d(target, 4, 4);
    auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
    while (host_present_unique_completed() == before && std::chrono::steady_clock::now() < deadline)
        std::this_thread::yield();
    CHECK(host_present_unique_completed() > before);
    uint8_t shown[64] = {};
    CHECK(host_present_test_read_rgba(shown, sizeof shown));
    for (int i = 0; i < 16; ++i)
        for (int c = 0; c < 3; ++c)
            CHECK_EQ(shown[i * 4 + c], out[i * 4 + c]);
    // A reset drops every copy and says so.
    const uint32_t generation = host_gpu2d_generation();
    host_gpu2d_reset();
    CHECK(host_gpu2d_generation() != generation);
    CHECK_EQ(host_gpu2d_readback(target, 4, 4, out), 0);
    host_present_stop();
    CHECK_EQ(host_gpu2d_available(), 0);
}

static void test_presenter_menu_ui_and_movie_pixels() {
    for (auto cls : {HOST_SCREEN_MENU, HOST_SCREEN_FMV}) {
        host_present_test_begin();
        test_frame_builder frame;
        frame.screen_class = cls;
        frame.revision({4, 1}, 2, 2, 8, {1, 1, 1, 1});
        frame.blit(4, 10, 20, 2, 2);
        g_present_test_current = frame.frame;
        host_present_first_write();
        uint8_t decoded[16] = {207, 19, 51, 255, 207, 19, 51, 255,
                               207, 19, 51, 255, 207, 19, 51, 255};
        if (cls == HOST_SCREEN_FMV)
            host_present_stage_rgba(decoded, 2, 2);
        host_frame_seal();
        memset(decoded, 0, sizeof decoded); // Publication owns the movie image.
        CHECK(frame.balanced());
        frame.records.clear();
        frame.revisions.clear();
        frame.palettes.clear();
        host_present_tick_for_test(1);
        LayoutSnapshot layout;
        CHECK(host_present_copy_layout(&layout));
        CHECK_EQ(layout.frame_id, frame.frame.id);
        CHECK_EQ(layout.elements.size(), cls == HOST_SCREEN_MENU ? 1u : 0u);
        if (!layout.elements.empty()) {
            CHECK_EQ(layout.elements[0].guest.x, 10);
            CHECK_EQ(layout.elements[0].guest.y, 20);
        }
        CHECK_EQ(host_present_test_last_ui_red(), cls == HOST_SCREEN_MENU ? 255 : 0);
        if (cls == HOST_SCREEN_FMV)
            CHECK_EQ(host_present_test_last_pixel(), 207);
        CHECK_EQ(host_present_unique_completed(), 1u);
        host_present_stop();
        g_present_test_current = {};
    }
}

static int test_classic = 0, test_scale = 0, test_scene_width = 0, test_hd = 0;
static void test_layered_gameplay_skips_unused_compatibility_pixels() {
    const uint8_t red565[2] = {0x00, 0xf8};
    for (unsigned mode = 0; mode < 5; ++mode) {
        host_present_test_begin();
        test_frame_builder frame;
        frame.screen_class = mode == 0   ? HOST_SCREEN_MENU
                             : mode == 1 ? HOST_SCREEN_FMV
                                         : HOST_SCREEN_GAMEPLAY;
        g_present_test_current = frame.frame;
        test_classic = mode == 2;
        g_t5_legacy_frame = mode == 3 ? frame.frame.id : 0;
        CHECK_EQ(host_present_needs_legacy_pixels(), 1); // no acquired writer yet
        host_present_first_write();
        CHECK_EQ(host_present_needs_legacy_pixels(), mode != 4);
        host_present(red565, 1, 1, 16, nullptr, 2);
        host_frame_seal();
        host_present_tick_for_test(0);
        CHECK_EQ(host_present_unique_completed(), 1u);
        CHECK_EQ(host_present_test_last_pixel(), mode == 4 ? 0 : 255);
        CHECK(frame.balanced());
        host_present_stop();
        g_present_test_current = {};
    }
    test_classic = 0;
    g_t5_legacy_frame = 0;
    CHECK_EQ(host_present_needs_legacy_pixels(), 1); // stopped/unknown is conservative
}
static void test_wide_scene_pixels(D3DRenderer *renderer) {
    // Exercise both shipping game modes and return to the first mode. Vertices
    // beyond the selected surface width must survive the real Metal viewport.
    for (int height : {480, 600, 480}) {
        const int width = height * 4 / 3, domain = height == 600 ? 1065 : 852;
        renderer->discard();
        D3DRenderer::setShared(renderer);
        host_d3d_reset_coherence();
        host_present_start_offscreen(domain * 2, height * 2);
        Surface surface(881, width, height);
        host_d3d_bind_generation(&surface.desc, 1, 9002);
        host_present_acquire_target(width, height, domain * 2, height * 2);
        test_scene_width = domain;
        g_t5_mapping = HOST_MAPPING_SCENE;
        HostD3DDrawSnapshot clear{};
        clear.kind = HOST_DRAW_CLEAR;
        clear.clear_flags = 3;
        clear.clear_color = 0xffff0000;
        clear.clear_z = 1;
        host_d3d_draw(&clear);
        Command quad;
        quad.cmd.primitive_type = 5;
        quad.cmd.viewport[2] = width;
        quad.cmd.viewport[3] = height;
        quad.tlvertex(width + 40, 100, .5f, 0xff00ff00);
        quad.tlvertex(width + 140, 100, .5f, 0xff00ff00);
        quad.tlvertex(width + 40, 200, .5f, 0xff00ff00);
        quad.tlvertex(width + 140, 200, .5f, 0xff00ff00);
        HostD3DDrawSnapshot draw{};
        t5_snapshot(quad, draw, 1);
        host_d3d_draw(&draw);
        auto pixels = read_target(renderer);
        int r = 0, g = 0, b = 0, a = 0;
        pixels.rgb((width + 90) * 2, 300, &r, &g, &b, &a);
        CHECK_EQ(g, 255);
        CHECK_EQ(r, 0);
        pixels.rgb((width - 40) * 2, 300, &r, &g, &b, &a);
        CHECK_EQ(r, 255);
        CHECK_EQ(g, 0);
        host_d3d_seal_frame(9002);
        host_present_test_seal(9002, HOST_SCREEN_GAMEPLAY, true);
        auto until = std::chrono::steady_clock::now() + std::chrono::seconds(5);
        while (!host_present_unique_completed() && std::chrono::steady_clock::now() < until)
            std::this_thread::yield();
        CHECK_EQ(host_present_unique_completed(), 1u);
        LayoutSnapshot layout;
        CHECK(host_present_copy_layout(&layout));
        CHECK_EQ(layout.guest_w, width);
        CHECK_EQ(layout.guest_h, height);
        CHECK_EQ(layout.scene.domain_w, domain);
        CHECK_NEAR(layout.scene.scale_x, 2, .001);
        host_present_stop();
        host_d3d_retire_frame(9002);
        test_scene_width = 0;
        renderer->discard();
    }
}
static void test_hd_pack_and_classic_isolation(D3DRenderer *original) {
    char dir[512];
    snprintf(dir, sizeof dir, "%s/pop-hd-test-XXXXXX", os_temp_dir());
    CHECK(os_mkdtemp(dir) == 0);
    const auto file = std::filesystem::path(dir) / "0000000000001234.popt";
    uint8_t header[32]{};
    memcpy(header, "POPRGBA1", 8);
    header[8] = header[12] = 4;
    header[16] = 3;
    header[24] = 0x34;
    header[25] = 0x12;
    {
        std::ofstream out(file, std::ios::binary);
        out.write((char *)header, 32);
        const uint8_t color[] = {19, 73, 141, 255};
        for (int i = 0; i < 21; ++i)
            out.write((const char *)color, 4);
    }
    pop_hd::File parsed;
    CHECK(pop_hd::inspect(file, parsed));
    CHECK_EQ(parsed.bytes, 84);
    // Reject inconsistent mip count and short storage before allocating GPU memory.
    {
        std::fstream out(file, std::ios::binary | std::ios::in | std::ios::out);
        out.seekp(16);
        out.put(4);
    }
    CHECK(!pop_hd::inspect(file, parsed));
    {
        std::fstream out(file, std::ios::binary | std::ios::in | std::ios::out);
        out.seekp(16);
        out.put(3);
    }
    const auto oversized = std::filesystem::path(dir) / "0000000000005678.popt";
    header[8] = header[12] = 0;
    header[9] = header[13] = 16;
    header[16] = 13;
    header[24] = 0x78;
    header[25] = 0x56;
    {
        std::ofstream out(oversized, std::ios::binary);
        out.write((char *)header, 32);
        out.seekp(32 + pop_hd::mip_bytes(4096, 4096, 13) - 1);
        out.put(0);
    }
    CHECK(pop_hd::inspect(oversized, parsed));
    const char *env = recomp_env("TEXTURE_PACK_DIR");
    bool had = env;
    std::string saved = env ? env : "";
    const char *limit = recomp_env("TEXTURE_BUDGET_MB");
    bool hadLimit = limit;
    std::string savedLimit = limit ? limit : "";
    os_setenv("RECOMP_TEXTURE_BUDGET_MB", "32");
    os_setenv("RECOMP_TEXTURE_PACK_DIR", dir);
    auto r = make_renderer();
    CHECK(r != nullptr);
    D3DRenderer::setShared(r);
    for (int variant = 0; r && variant < 5; ++variant) {
        r->discard();
        host_d3d_reset_coherence();
        test_hd = variant != 0;
        test_classic = variant == 2;
        g_t5_legacy_frame = variant == 3 ? 9987 : 0;
        host_present_start_offscreen(128, 128);
        Surface surface(883, 64, 64);
        host_d3d_bind_generation(&surface.desc, 1, 9987);
        uint16_t red = 0xf800;
        HostD3DTexture t{};
        t.handle = 991;
        t.revision = variant + 1;
        t.width = t.height = 1;
        t.pitch = 2;
        t.bpp = 16;
        t.pixels = &red;
        t.content_hash = variant == 4 ? 0x5678 : 0x1234;
        r->uploadTexture(&t);
        Command q;
        q.cmd.primitive_type = 5;
        q.cmd.texture_handle = 991;
        q.state[1] = 991;
        q.state[21] = 1;
        q.tlvertex(0, 0, .5f, 0xffffffff, 0, 0);
        q.tlvertex(64, 0, .5f, 0xffffffff, 1, 0);
        q.tlvertex(0, 64, .5f, 0xffffffff, 0, 1);
        q.tlvertex(64, 64, .5f, 0xffffffff, 1, 1);
        r->clearFlags(3, nullptr, 0, 0xff000000, 1);
        r->draw(&q.cmd, variant + 1);
        auto rb = read_target(r);
        int redc, g, b, a;
        rb.rgb(32, 32, &redc, &g, &b, &a);
        CHECK_EQ(redc, variant == 1 ? 19 : 255);
        CHECK_EQ(g, variant == 1 ? 73 : 0);
        CHECK_EQ(b, variant == 1 ? 141 : 0);
        host_present_stop();
        host_d3d_retire_frame(9987);
        r->discard();
    }
    CHECK(r->hdTextureStats().draws > 0);
    CHECK_EQ(r->hdTextureStats().loads, 1);
    CHECK_EQ(r->hdTextureStats().refused, 1);
    CHECK(r->hdTextureStats().resident_bytes <= r->hdTextureStats().budget_bytes);
    test_hd = test_classic = 0;
    g_t5_legacy_frame = 0;
    D3DRenderer::setShared(original);
    if (had)
        os_setenv("RECOMP_TEXTURE_PACK_DIR", saved.c_str());
    else
        os_unsetenv("RECOMP_TEXTURE_PACK_DIR");
    if (hadLimit)
        os_setenv("RECOMP_TEXTURE_BUDGET_MB", savedLimit.c_str());
    else
        os_unsetenv("RECOMP_TEXTURE_BUDGET_MB");
    std::filesystem::remove_all(dir);
}

static void test_terrain_material_detail(D3DRenderer *original) {
    char dir[512];
    snprintf(dir, sizeof dir, "%s/pop-terrain-detail-XXXXXX", os_temp_dir());
    CHECK(os_mkdtemp(dir) == 0);
    const auto file = std::filesystem::path(dir) / "terrain-detail.popt";
    uint8_t header[32]{};
    memcpy(header, "POPRGBA1", 8);
    header[8] = header[12] = 128;
    header[16] = 8;
    {
        std::ofstream out(file, std::ios::binary);
        out.write((char *)header, 32);
        std::vector<uint8_t> pixels(pop_hd::mip_bytes(128, 128, 8), 255);
        out.write((const char *)pixels.data(), pixels.size());
    }
    // A genuinely larger authored replacement already owns its fine detail.
    header[8] = header[12] = 0;
    header[9] = header[13] = 1;
    header[16] = 9;
    header[24] = 0x56;
    header[25] = 0x34;
    {
        std::ofstream out(std::filesystem::path(dir) / "0000000000003456.popt", std::ios::binary);
        out.write((char *)header, 32);
        const uint16_t color = 0x6c23;
        const uint8_t rgba[] = {uint8_t(((color >> 11) & 31) * 255 / 31),
                                uint8_t(((color >> 5) & 63) * 255 / 63),
                                uint8_t((color & 31) * 255 / 31), 255};
        for (uint64_t i = 0; i < pop_hd::mip_bytes(256, 256, 9) / 4; ++i)
            out.write((const char *)rgba, 4);
    }
    const char *env = recomp_env("TEXTURE_PACK_DIR");
    bool had = env;
    std::string saved = env ? env : "";
    os_setenv("RECOMP_TEXTURE_PACK_DIR", dir);
    auto renderer = make_renderer();
    CHECK(renderer != nullptr);
    D3DRenderer::setShared(renderer);
    for (int variant = 0; renderer && variant < 9; ++variant) {
        renderer->discard();
        host_d3d_reset_coherence();
        test_hd = variant != 1;
        test_classic = variant == 2;
        g_t5_legacy_frame = variant == 3 ? 9988 : 0;
        host_present_start_offscreen(128, 128);
        Surface surface(884, 64, 64);
        host_d3d_bind_generation(&surface.desc, 1, 9988);
        const uint16_t color = variant == 4 ? 0x19b0 : 0x6c23; // blue water / olive land
        std::vector<uint16_t> pixels(16 * 16, color);
        // The sky dome is a 16x16 vertical gradient, opaque 5-6-5 like a
        // terrain tile. Its rows are uniform; only the red rises down the
        // tile, so the sampled green is the flat tile's and must stay so.
        if (variant == 8)
            for (int y = 0; y < 16; ++y)
                for (int x = 0; x < 16; ++x)
                    pixels[y * 16 + x] = uint16_t((color & 0x07ff) | ((y * 2) << 11));
        HostD3DTexture t{};
        t.handle = 992;
        t.revision = variant + 1;
        t.width = t.height = 16;
        t.pitch = 32;
        t.bpp = 16;
        t.rmask = 0xf800;
        t.gmask = 0x7e0;
        t.bmask = 31;
        t.pixels = pixels.data();
        if (variant == 7)
            t.content_hash = 0x3456;
        if (variant == 6)
            t.amask = 0x8000; // a sprite format must remain untouched
        renderer->uploadTexture(&t);
        Command q;
        q.cmd.primitive_type = 5;
        q.cmd.texture_handle = 992;
        q.state[1] = 992;
        q.state[21] = 1;
        q.state[7] = q.state[14] = 1;
        const float hi = variant == 5 ? .5f : 1.f;
        q.tlvertex(0, 0, .5f, 0xffffffff, 0, 0);
        q.tlvertex(64, 0, .5f, 0xffffffff, hi, 0);
        q.tlvertex(0, 64, .5f, 0xffffffff, 0, hi);
        q.tlvertex(64, 64, .5f, 0xffffffff, hi, hi);
        renderer->clearFlags(3, nullptr, 0, 0xff000000, 1);
        renderer->draw(&q.cmd, variant + 1);
        auto rb = read_target(renderer);
        int r, g, b, a;
        rb.rgb(32, 32, &r, &g, &b, &a);
        const int expected_g = ((color >> 5) & 63) * 255 / 63;
        if (variant == 0)
            CHECK(g > expected_g + 20);
        else
            CHECK_NEAR(g, expected_g, 1);
        host_present_stop();
        host_d3d_retire_frame(9988);
        renderer->discard();
    }
    CHECK_EQ(renderer->hdTextureStats().detail_draws,
             2); // land and water; water's shader mask is zero, the gradient is not a tile
    CHECK(renderer->hdTextureStats().resident_bytes <= renderer->hdTextureStats().budget_bytes);
    test_hd = test_classic = 0;
    g_t5_legacy_frame = 0;
    D3DRenderer::setShared(original);
    if (had)
        os_setenv("RECOMP_TEXTURE_PACK_DIR", saved.c_str());
    else
        os_unsetenv("RECOMP_TEXTURE_PACK_DIR");
    std::filesystem::remove_all(dir);
}

static void test_native_tile_borders(D3DRenderer *renderer) {
    // Red/green opposite texture edges expose accidental wrap filtering:
    // a complete native tile must stay red at its left edge. Repeating UVs,
    // explicitly wrapped polygons, and Classic must retain the mixed border.
    for (int variant = 0; variant < 4; ++variant) {
        renderer->discard();
        D3DRenderer::setShared(renderer);
        host_d3d_reset_coherence();
        test_classic = variant == 3;
        host_present_start_offscreen(128, 128);
        Surface surface(882, 64, 64);
        host_d3d_bind_generation(&surface.desc, 1, 9003 + variant);
        uint16_t pixels[4] = {0xf800, 0x07e0, 0xf800, 0x07e0};
        HostD3DTexture tex{};
        tex.handle = 988;
        tex.pixels = pixels;
        tex.width = tex.height = 2;
        tex.pitch = 4;
        tex.bpp = 16;
        renderer->uploadTexture(&tex);
        Command quad;
        quad.cmd.primitive_type = 5;
        quad.cmd.texture_handle = 988;
        quad.state[7] = quad.state[14] = 1;
        quad.state[17] = quad.state[18] = 2;
        quad.state[21] = 1;
        if (variant == 2)
            quad.state[5] = 1;
        float max_u = variant == 1 ? 2 : 1;
        quad.tlvertex(0, 0, .5f, 0xffffffff, 0, 0);
        quad.tlvertex(64, 0, .5f, 0xffffffff, max_u, 0);
        quad.tlvertex(0, 64, .5f, 0xffffffff, 0, 1);
        quad.tlvertex(64, 64, .5f, 0xffffffff, max_u, 1);
        renderer->clearFlags(3, nullptr, 0, 0xff000000, 1);
        renderer->draw(&quad.cmd);
        auto rb = read_target(renderer);
        int r, g, b, a;
        rb.rgb(1, rb.h / 2, &r, &g, &b, &a);
        if (variant == 0) {
            CHECK_EQ(r, 255);
            CHECK_EQ(g, 0);
        } else {
            CHECK(r > 100 && r < 180);
            CHECK(g > 80 && g < 150);
        }
        host_present_stop();
        host_d3d_retire_frame(9003 + variant);
        test_classic = 0;
        renderer->discard();
    }
}
static void test_presenter_incremental_world_and_overlay(D3DRenderer *renderer) {
    renderer->discard();
    D3DRenderer::setShared(renderer);
    host_d3d_reset_coherence();
    host_present_start_offscreen(128, 128);
    Surface surface(880, 64, 64);
    host_d3d_bind_generation(&surface.desc, 1, 9000);
    auto target = host_present_acquire_target(64, 64, 128, 128);
    CHECK(renderer->colorTarget() == target.world);
    CHECK_EQ(target.w, 128);
    HostD3DDrawSnapshot clear{};
    clear.kind = HOST_DRAW_CLEAR;
    clear.clear_flags = 3;
    clear.clear_color = 0xffff0000;
    clear.clear_z = 1;
    g_t5_mapping = HOST_MAPPING_SCENE;
    host_d3d_draw(&clear);
    Command overlay;
    overlay.cmd.primitive_type = 5;
    overlay.tlvertex(0, 0, .5f, 0xff00ff00);
    overlay.tlvertex(16, 0, .5f, 0xff00ff00);
    overlay.tlvertex(0, 16, .5f, 0xff00ff00);
    overlay.tlvertex(16, 16, .5f, 0xff00ff00);
    HostD3DDrawSnapshot draw{};
    t5_snapshot(overlay, draw, 1);
    g_t5_mapping = HOST_MAPPING_UI;
    host_d3d_draw(&draw);
    std::vector<uint8_t> blue(32 * 32 * 2);
    for (size_t i = 0; i < blue.size(); i += 2)
        blue[i] = 31;
    HostBlitRecord cpu{};
    cpu.src.surface = HOST_SRC_CPU;
    cpu.dst = surface.desc.id;
    cpu.dst_generation = 1;
    cpu.w = cpu.h = 32;
    cpu.cpu_pixels = blue.data();
    cpu.cpu_bpp = 16;
    cpu.cpu_pitch = 64;
    cpu.after_first_draw = 1;
    cpu.seq = 2;
    host_d3d_apply_cpu(&surface.desc, &cpu);
    auto world = read_target(renderer);
    int wr = 0, wg = 0, wb = 0, wa = 0;
    world.rgb(48, 48, &wr, &wg, &wb, &wa);
    CHECK_EQ(wr, 255);
    CHECK_EQ(wb, 0); // HUD must not also be baked into world
    host_d3d_seal_frame(9000);
    UiFrame ui{{}, 64, 64};
    UiElement hud{};
    hud.id = 99;
    hud.w = hud.h = 32;
    hud.is_hud = true;
    hud.rgba.resize(32 * 32 * 4);
    hud.mask.resize(32 * 32, 1);
    for (int i = 0; i < 32 * 32; ++i) {
        hud.rgba[i * 4 + 2] = 255;
        hud.rgba[i * 4 + 3] = 255;
    }
    ui.elements.push_back(hud);
    CompositorInput in{};
    in.cls = HOST_SCREEN_GAMEPLAY;
    in.world = target.world;
    in.overlay = target.overlay;
    in.ui = &ui;
    in.guest_w = in.guest_h = 64;
    host_present_set_input(&in);
    host_present_test_seal(9000, HOST_SCREEN_GAMEPLAY, true);
    auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
    while (!host_present_unique_completed() && std::chrono::steady_clock::now() < deadline)
        std::this_thread::yield();
    CHECK_EQ(host_present_unique_completed(), 1u);
    std::vector<uint8_t> pixels(128 * 128 * 4);
    CHECK(host_present_test_read_rgba(pixels.data(), pixels.size()));
    auto at = [&](int x, int y, int channel) { return pixels[(y * 128 + x) * 4 + channel]; };
    CHECK_EQ(at(8, 8, 1), 255);
    CHECK_EQ(at(8, 8, 0), 0); // overlay green
    CHECK_EQ(at(48, 48, 2), 255);
    CHECK_EQ(at(48, 48, 0), 0); // 64-pixel mode auto-scales HUD by 2
    CHECK_EQ(at(80, 80, 0), 255);
    CHECK_EQ(at(80, 80, 1), 0); // world red
    host_present_stop();
    host_d3d_retire_frame(9000);
    g_t5_mapping = HOST_MAPPING_SCENE;
    renderer->discard();

    // Classic keeps the CPU HUD and the subsequent UI-mapped primitive in
    // the same guest-resolution surface, then aspect-fits it at presentation.
    test_classic = 1;
    host_d3d_reset_coherence();
    host_present_start_offscreen(128, 96);
    host_d3d_bind_generation(&surface.desc, 2, 9001);
    target = host_present_acquire_target(64, 64, 128, 96);
    CHECK_EQ(target.w, 64);
    CHECK_EQ(target.h, 64);
    CHECK(renderer->colorTarget() == target.world);
    g_t5_mapping = HOST_MAPPING_SCENE;
    host_d3d_draw(&clear);
    cpu.dst_generation = 2;
    cpu.seq = 1;
    host_d3d_apply_cpu(&surface.desc, &cpu);
    draw.seq = 2;
    g_t5_mapping = HOST_MAPPING_UI;
    host_d3d_draw(&draw);
    host_d3d_seal_frame(9001);
    host_present_test_seal(9001, HOST_SCREEN_GAMEPLAY, true);
    deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
    while (!host_present_unique_completed() && std::chrono::steady_clock::now() < deadline)
        std::this_thread::yield();
    CHECK_EQ(host_present_unique_completed(), 1u);
    HostCompletedComposite classic;
    CHECK(host_present_copy_composite(&classic));
    CHECK_EQ(classic.w, 128);
    CHECK_EQ(classic.h, 96);
    CHECK_EQ(classic.guest_w, 64);
    CHECK_EQ(classic.guest_h, 64);
    CHECK_EQ(classic.cls, HOST_SCREEN_GAMEPLAY);
    if (classic.rgba.size() == 128u * 96u * 4u) {
        auto channel = [&](int x, int y, int c) { return classic.rgba[(y * 128 + x) * 4 + c]; };
        CHECK_EQ(channel(0, 48, 0), 0);
        CHECK_EQ(channel(112, 48, 0), 0);  // bars
        CHECK_EQ(channel(20, 4, 1), 255);  // UI primitive retained in the flat frame
        CHECK_EQ(channel(52, 36, 2), 255); // CPU HUD retained, no UiFrame supplied
        CHECK_EQ(channel(88, 72, 0), 255); // world
    } else
        CHECK(false);
    deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
    while (host_present_repeats() < 2 && std::chrono::steady_clock::now() < deadline)
        std::this_thread::yield();
    CHECK(host_present_repeats() >= 2);
    LayoutSnapshot repeat;
    CHECK(host_present_copy_layout(&repeat));
    CHECK_EQ(repeat.guest_w, 64);
    CHECK_EQ(repeat.scene.domain_w, 64);
    CHECK_NEAR(repeat.scene.scale_x, 1.5, .001);
    host_present_resize(192, 128);
    deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
    do {
        host_present_copy_layout(&repeat);
        std::this_thread::yield();
    } while (repeat.drawable_w != 192 && std::chrono::steady_clock::now() < deadline);
    CHECK_EQ(repeat.drawable_w, 192);
    CHECK_EQ(repeat.guest_w, 64);
    CHECK_NEAR(repeat.scene.scale_x, 2, .001);
    CHECK_NEAR(repeat.scene.offset_x, 32, .001);
    host_present_stop();
    host_d3d_retire_frame(9001);
    test_classic = 0;
    g_t5_mapping = HOST_MAPPING_SCENE;
    renderer->discard();
}

static void test_gdi_window_presentation() {
    host_present_test_begin();
    uint32_t pixels[4] = {0xffff0000, 0xff00ff00, 0xff0000ff, 0xffffffff};
    host_display_present_window(pixels, 2, 2);
    pixels[0] = 0; // The sealed frame owns its copy.
    host_present_tick_for_test(0);
    CHECK_EQ(host_present_unique_completed(), 1u);
    CHECK_EQ(host_present_test_last_pixel(), 255u);
    CHECK(host_present_test_class_valid());
    host_present_stop();
}

static void test_d3d11_sealed_presentation();
static void test_presentation_service() {
    test_gdi_window_presentation();
    test_d3d11_sealed_presentation();
    test_windowed_first_blit_presents_without_prior_completion();
    test_windowed_drawable_handler_and_completion_fallback();
    test_windowed_duration_pacing_selector();
    test_presenter_menu_ui_and_movie_pixels();
    test_newest_sealed_wins_and_oldest_drops();
    test_repeat_counted_not_completed();
    test_frame_immutable_after_seal();
    test_release_after_completion_and_presentation();
    test_dropped_prefix_lifetime();
    test_acquire_waits_for_retained_target();
    test_acquire_faults_when_all_targets_stall();
    test_two_display_frames_overlap_and_retire_in_order();
    test_three_display_frames_overlap_and_retire_in_order();
    test_layered_gameplay_skips_unused_compatibility_pixels();
    test_continuous_metric_needs_40s_and_reports_min_bucket();
    test_presenter_render_resolution();
    test_presenter_allocation_halving();
    test_presenter_layout_and_transitions();
    test_presenter_history_keeps_target_lease();
    test_presenter_shutdown_cancels_without_counting();
    test_presenter_migration_cancels_old_drawable();
}

static void test_stats_line_format() {
    host_stats_reset();
    char line[512];

    // Empty is still well formed. A run that did nothing has to produce a
    // line the comparator can read, or "the run crashed" and "the format
    // changed" become the same result.
    // NOTHING NOTED MEANS NOTHING PRINTED. A measure no code has touched is
    // absent from the line, not zero in it: zero says "this happened no times"
    // and the regression rules then hold it to zero for ever, so printing
    // zeros for measures nothing instruments lays a trap for whichever task
    // makes them real. The committed baseline had exactly that - every phase
    // 0.0, every upload and readback 0, and nothing anywhere calling these.
    size_t n = host_stats_gameplay_line(line, sizeof line);
    CHECK(n > 0);
    CHECK(strncmp(line, "phases: unique=", 15) == 0);
    CHECK(strstr(line, " drops=") != nullptr);
    CHECK(strstr(line, " faults=") != nullptr);
    CHECK(strstr(line, " continuous=") != nullptr);
    CHECK(strstr(line, "guest=") == nullptr);
    CHECK(strstr(line, "upload=") == nullptr);
    CHECK(strstr(line, "readback=") == nullptr);

    host_stats_note_phase(HOST_PHASE_GUEST, 3.0);
    host_stats_note_phase(HOST_PHASE_SHIM, 1.0);
    host_stats_note_upload(4096);
    host_stats_note_readback(HOST_READBACK_LOCK);
    n = host_stats_gameplay_line(line, sizeof line);
    CHECK(n > 0);
    // The line the brief specifies, exactly.
    CHECK(strstr(line, "guest=3.0 shim=1.0 upload=1/4096") != nullptr);
    CHECK(strstr(line, "readback=1[lock=1 getdc=0 src=0 dstkey=0 dup=0 texload=0 flip=0]") !=
          nullptr);

    // Phases accumulate rather than replace.
    host_stats_note_phase(HOST_PHASE_GUEST, 0.5);
    n = host_stats_gameplay_line(line, sizeof line);
    CHECK(strstr(line, "guest=3.5") != nullptr);

    // A measure that IS instrumented and counted nothing is a real zero and
    // stays in the line. Noted, not non-zero, is the distinction.
    host_stats_note_phase(HOST_PHASE_WAIT, 0.0); // rejected: not a duration
    CHECK(strstr(line, "wait=") == nullptr);
    host_stats_note_phase(HOST_PHASE_WAIT, 0.04); // rounds to 0.0 ms, but noted
    n = host_stats_gameplay_line(line, sizeof line);
    CHECK(strstr(line, "wait=0.0") != nullptr);

    // Each reason counts only itself, and the total is their sum.
    host_stats_note_readback(HOST_READBACK_FLIP);
    host_stats_note_readback(HOST_READBACK_FLIP);
    n = host_stats_gameplay_line(line, sizeof line);
    CHECK(strstr(line, "readback=3[lock=1") != nullptr);
    CHECK(strstr(line, "flip=2]") != nullptr);

    // A buffer too small writes NOTHING. Half a line parses as different
    // numbers, which is worse than a line the reader knows is missing.
    char tiny[16];
    memset(tiny, 'x', sizeof tiny);
    CHECK_EQ(host_stats_gameplay_line(tiny, sizeof tiny), (size_t)0);
    CHECK(tiny[0] == 'x');

    // The access line is the same contract.
    n = host_stats_access_line(line, sizeof line);
    CHECK(n > 0);
    CHECK(strstr(line, "access: lock_read=") != nullptr);
    CHECK(strstr(line, "clean_reads=") != nullptr);
    CHECK_EQ(host_stats_access_line(tiny, sizeof tiny), (size_t)0);

    // Reset forgets that anything was noted, not only the numbers, so the line
    // goes back to naming nothing. A reset that left the measures present at
    // zero would put the trap back the moment any test used it.
    host_stats_reset();
    n = host_stats_gameplay_line(line, sizeof line);
    CHECK(strncmp(line, "phases: unique=", 15) == 0);
    CHECK(strstr(line, " drops=") != nullptr);
    CHECK(strstr(line, " faults=") != nullptr);
    CHECK(strstr(line, " continuous=") != nullptr);
}

// `await metric>=N` as well as `metric>N`.
//
// A turn is a counter and the natural way to wait for one is "at least N".
// Without this the parser split `turn>=700` on the '>' and failed with
// "needs a number after >: =700" - which is what it did to the first version
// of display-ref.script, producing a run with no dumps, no expectations, and
// therefore no FAILED lines either. A check that counted unmet expectations
// called that run clean.
static void test_script_await_at_least() {
    HostScriptStep steps[8];
    char err[256];

    int n = host_script_parse("await turn>=700 within 120000\n", steps, 8, err, sizeof err);
    CHECK_EQ(n, 1);
    if (n == 1) {
        CHECK(steps[0].op == HOST_SCRIPT_AWAIT);
        CHECK(strcmp(steps[0].name, "turn") == 0);
        CHECK_EQ(steps[0].threshold, 700.0);
        CHECK(steps[0].at_least);
        CHECK_EQ(steps[0].timeout_ms, 120000u);
    }

    // The plain form still means strictly greater, and says so.
    n = host_script_parse("await picture>0.98 for 2000\n", steps, 8, err, sizeof err);
    CHECK_EQ(n, 1);
    if (n == 1) {
        CHECK(!steps[0].at_least);
        CHECK_EQ(steps[0].threshold, 0.98);
    }

    // A bare `>=` with nothing after it is an error, not a threshold of zero.
    CHECK(host_script_parse("await turn>=\n", steps, 8, err, sizeof err) < 0);
}

// `await ... for <ms>` in presented frames, and the conversion has to be exact
// because every script shipped before the unit changed still says 2000.
//
// The hold exists to outlast a fade. Measured in time it can be satisfied
// without any frames at all: the pinned clock also advances when a guest spins
// on it without drawing, which is what the front end does while it waits for a
// sound cursor, so a hold in milliseconds was answered during a two-frame fade
// plateau and the click landed early. Five pinned runs in twelve failed that
// way, with the same signature as the fixed-wait flake before them.
// Exercise the same body resolver and waiting state used by semantic gestures.
// Missing/shadow/stale evidence must never become an injected click.
static void test_entity_click_wait() {
    HostEntityWait wait;
    using Result = HostEntityWaitResult;
    uint32_t clock = 100;
    CHECK(wait.poll(1000, 50, 1544, 50, false) == Result::pending);
    for (uint32_t ms = 1001; ms < 2000; ++ms)
        CHECK(wait.poll(ms, 50, 1544, 50, false) == Result::pending);
    CHECK(wait.last_frame == 1544); // polling is not a presented frame
    CHECK(wait.poll(2000, 51, 1544, 49, true) == Result::pending); // stale body

    HostD3DDrawSnapshot draw{};
    draw.kind = HOST_DRAW_PRIMITIVE;
    draw.texture_handle = 7;
    draw.texture_revision = 2;
    draw.screen_min_x = 200;
    draw.screen_max_x = 220;
    draw.screen_min_y = 210;
    draw.screen_max_y = 220; // only a shadow at feet
    LandmarkSpriteEvidence sprite{1546, 1815, 7, 2, true};
    int32_t x = -1, y = -1;
    auto body = [&] {
        return landmark_click_point(true, 1815, sprite.frame, true, 210, 210, 640, 480, sprite,
                                    &draw, 1, 0, 0, &x, &y);
    };
    CHECK(!body());
    CHECK(!host_entity_body_ready(52, sprite.frame, 52, body())); // shadow only
    CHECK(wait.poll(2050, 52, sprite.frame, 52, body()) == Result::pending);
    draw.screen_min_y = 180; // a BODY arrives on a later frame
    sprite.frame = 1547;
    CHECK(!host_entity_body_ready(53, 0, 53, body()));            // no frame
    CHECK(!host_entity_body_ready(53, sprite.frame, 51, body())); // stale BODY
    CHECK(host_entity_body_ready(53, sprite.frame, 52, body()));  // preceding completed present
    CHECK(host_entity_body_ready(53, sprite.frame, 53, body()));
    CHECK(wait.poll(2100, 53, sprite.frame, 53, body()) == Result::ready);
    CHECK(x == 210 && y == 195);
    CHECK(wait.last_frame == 1547);
    wait.finish(2100, clock);
    CHECK(!wait.active && clock == 1200); // following 800 ms stays 800 ms
    CHECK(2100 - clock == 1000 - 100);

    // Fresh state for the next gesture; no remembered successful body.
    CHECK(wait.poll(3000, 80, 1600, 80, false) == Result::pending);
    CHECK(wait.poll(8950, 199, 1719, 199, false) == Result::pending);
    CHECK(wait.poll(9000, 200, 1720, 200, false) == Result::timed_out);
    CHECK(wait.last_frame == 1720);
    wait.finish(9000, clock);
    CHECK(clock == 7200 && !wait.active);

    // The final allowed present can satisfy the click, but a later one cannot.
    CHECK(wait.poll(0, 0, 1, 0, false) == Result::pending);
    CHECK(wait.poll(6000, 120, 121, 120, true) == Result::ready);
    wait.finish(6000, clock);
    CHECK(wait.poll(0, 0, 1, 0, false) == Result::pending);
    CHECK(wait.poll(6050, 121, 122, 121, true) == Result::timed_out);
    wait.finish(6050, clock);
    // No usable frame: fail boundedly without reporting stale data as examined.
    CHECK(wait.poll(0, 0, 999, 99, true) == Result::pending);
    CHECK(wait.poll(6000, 120, 999, 99, true) == Result::timed_out);
    CHECK(wait.last_frame == 0);
}

// Synthetic per-entity attribution store, shared with the smoke resolver.
static void test_entity_completed_present_freshness() {
    HostEntityBodyStore store;
    using Result = HostEntityWaitResult;
    uint32_t completed = 2084;
    store.record(1815, {1961, completed, 491, 184});
    auto body = store.get(1815);
    CHECK(body.ready(completed));
    CHECK(body.ready(completed + 1));
    CHECK(!body.ready(completed + 2));
    CHECK(!body.ready(completed - 1));        // in-flight/future record
    CHECK(!store.get(1816).ready(completed)); // another entity is not evidence
    // A completed capture without this entity's BODY retains its own point.
    store.record(1816, {1962, completed + 1, 300, 200});
    store.record(1815, {}); // no BODY cannot overwrite the last attribution
    body = store.get(1815);
    CHECK_EQ(body.frame, 1961u);
    CHECK_EQ(body.present, completed);
    CHECK_EQ(body.x, 491);
    CHECK_EQ(body.y, 184);
    CHECK(body.ready(completed + 1));

    // Assertion and both gestures consume this same record/predicate; fresh
    // evidence must resolve immediately, including at the preceding present.
    for (uint32_t age = 0; age <= 2; ++age) {
        HostEntityWait wait;
        auto result = wait.poll(100, completed + age, body.frame, body.present, true);
        CHECK((result == Result::ready) == body.ready(completed + age));
        CHECK(result == (age <= 1 ? Result::ready : Result::pending));
    }
    // Polling or an in-flight present must not consume the completed budget.
    HostEntityWait wait;
    body = store.get(1900);
    CHECK(wait.poll(0, completed, body.frame, body.present, false) == Result::pending);
    CHECK(wait.poll(50000, completed, body.frame, body.present, false) == Result::pending);
    for (uint32_t n = 1; n < HostEntityWait::max_presents; ++n)
        CHECK(wait.poll(50000 + n, completed + n, body.frame, body.present, false) ==
              Result::pending);
    completed += HostEntityWait::max_presents;
    CHECK(wait.poll(60000, completed, body.frame, body.present, false) == Result::timed_out);
    char diagnostic[256];
    host_entity_body_diagnostic(diagnostic, sizeof diagnostic, 1900, completed, body, false);
    CHECK(strcmp(diagnostic,
                 "entity_body 1900: FAIL no fresh attributed BODY frame 0, "
                 "last record present 0, current completed present 2204 (no record)") == 0);
    body = store.get(1815);
    host_entity_body_diagnostic(diagnostic, sizeof diagnostic, 1815, completed, body, false);
    CHECK(strcmp(diagnostic, "entity_body 1815: FAIL no fresh attributed BODY frame 1961, "
                             "last record present 2084, current completed present 2204") == 0);
    store.record(1815, {2100, completed, 492, 185});
    body = store.get(1815);
    CHECK(body.ready(completed));
    CHECK_EQ(body.x, 492);
    CHECK_EQ(body.y, 185);
}

static void test_script_hold_frames() {
    // The case every shipped script depends on.
    CHECK_EQ(host_script_hold_frames(2000, 50), 40u);
    // Unpinned uses the same nominal step, so a script holds for the same
    // number of frames either way. A conversion that changed with the pin
    // would make a pinned run and an unpinned one test different things.
    CHECK_EQ(host_script_hold_frames(2000, 0), 40u);
    // Another pin, another step: 16 ms is 125 frames for the same 2000.
    CHECK_EQ(host_script_hold_frames(2000, 16), 125u);
    // Exact multiples do not gain a frame.
    CHECK_EQ(host_script_hold_frames(50, 50), 1u);
    CHECK_EQ(host_script_hold_frames(100, 50), 2u);
    // Rounds UP, and never to zero: a hold of one frame is the weakest useful
    // claim, and rounding a short hold down to nothing would silently restore
    // the defect this replaced.
    CHECK_EQ(host_script_hold_frames(51, 50), 2u);
    CHECK_EQ(host_script_hold_frames(1, 50), 1u);
    CHECK_EQ(host_script_hold_frames(49, 50), 1u);
    // No hold asked for is no hold: `await x>1` with no `for` waits for the
    // first frame the claim is true, which is what it meant before.
    CHECK_EQ(host_script_hold_frames(0, 50), 0u);
    CHECK_EQ(host_script_hold_frames(0, 0), 0u);
}

// An INPUT hold has a floor of four frames, and the floor is the whole point.
//
// A click is seen only if the guest polls the device while the button is down,
// and it polls once a frame. The runner held a click for 120 ms, which is two
// and a half frames at a 50 ms step, so a click could go down and come up
// between two polls and never happen. impl-t6 caught it: two pinned runs with
// byte-identical menu frames diverged at the first click, one loading the level
// and the other sitting at turn 0 with no textures, which is the flake
// signature exactly.
// A press and its release must not reach the guest in the same turn: a guest
// that polls its buttons would read the state once, find the button up, and
// the click would never have happened.
static void test_input_batch_limit() {
    const HostInputStep motion{0, 0, 0};
    const HostInputStep down{1, 0, 1};
    const HostInputStep up{1, 0, 0};
    const HostInputStep right_down{1, 1, 1};
    const HostInputStep right_up{1, 1, 0};

    CHECK_EQ(host_input_batch_limit(nullptr, 0), 0u);
    // Nothing to defer: motion, a press on its own, a release whose press was
    // applied in an earlier turn.
    const HostInputStep only_motion[] = {motion, motion};
    CHECK_EQ(host_input_batch_limit(only_motion, 2), 2u);
    const HostInputStep press_only[] = {motion, down};
    CHECK_EQ(host_input_batch_limit(press_only, 2), 2u);
    const HostInputStep release_only[] = {motion, up, motion};
    CHECK_EQ(host_input_batch_limit(release_only, 3), 3u);

    // A whole click in one batch is cut before the release, and the motion
    // that preceded the press still goes with it.
    const HostInputStep click[] = {motion, down, up};
    CHECK_EQ(host_input_batch_limit(click, 3), 2u);
    // The cut is before the release even with events between.
    const HostInputStep held[] = {down, motion, motion, up};
    CHECK_EQ(host_input_batch_limit(held, 4), 3u);
    // A different button's release is not deferred by this button's press.
    const HostInputStep other[] = {down, right_up, motion};
    CHECK_EQ(host_input_batch_limit(other, 3), 3u);
    // Two clicks: only the first release matters, the rest waits its turn.
    const HostInputStep two[] = {down, up, right_down, right_up};
    CHECK_EQ(host_input_batch_limit(two, 4), 1u);
}

static void test_script_input_hold_frames() {
    // The case that was failing: 120 ms is 2.4 frames, floored to 4.
    CHECK_EQ(host_script_input_hold_frames(120, 50), 4u);
    CHECK_EQ(host_script_input_hold_frames(120, 0), 4u);
    // A hold long enough on its own is not shortened to the floor.
    CHECK_EQ(host_script_input_hold_frames(500, 50), 10u);
    // A finer step makes 120 ms more frames, and the floor does not cap it.
    CHECK_EQ(host_script_input_hold_frames(120, 16), 8u);
    // Even a hold of nothing is four frames: an input hold of zero is not a
    // click at all, and every caller here means to click.
    CHECK_EQ(host_script_input_hold_frames(0, 50), 4u);
    CHECK_EQ(host_script_input_hold_frames(1, 50), 4u);
    // It is the await conversion with a floor, not a second rule.
    for (uint32_t ms = 200; ms <= 2000; ms += 200)
        CHECK_EQ(host_script_input_hold_frames(ms, 50), host_script_hold_frames(ms, 50));
}

static void test_present_counts() {
    // There is one layer on the screen now: the DirectDraw surface, which the
    // renderer has already written its result into by the time a present
    // happens. A present just counts and stages it.
    uint32_t before = host_present_count();
    uint8_t pixels[16 * 4 * 2];
    memset(pixels, 0, sizeof pixels);
    uint32_t palette[256] = {0};
    host_present(pixels, 4, 2, 8, palette, 16);
    CHECK_EQ(host_present_count(), before + 1);
    host_present(pixels, 4, 2, 16, nullptr, 16);
    CHECK_EQ(host_present_count(), before + 2);
}

// ===========================================================================

// ---------------------------------------------------------------------------
// The input gate, driven through the PRODUCTION path.
//
// host_key_event is what main.mm runs for every key: the gate, and then the
// host's own delivery. Driving it here rather than reproducing the sequence is
// the point - a test that hand-delivers what it thinks the host would deliver
// stays green when the host stops doing it.
//
// A consumed key must reach NONE of the four paths the guest has:
//   1. the DirectInput immediate state       host_input_peek
//   2. the buffered events the shim derives   IDirectInputDevice::GetDeviceData
//   3. GetAsyncKeyState                       the USER32 shim
//   4. the posted Win32 message               PeekMessageA
//
// The mods module is not linked here, so this file supplies the seam's answer
// itself - mods_input_key is weak in mods_seam.cpp, and a strong definition
// wins - which is what lets one test drive both outcomes. The filter's own
// press/repeat/release pairing is a different question and is proved in
// mods/tests/input_tests.cpp; what is proved here is that whatever
// the filter answers, the host obeys it on every channel.
// ---------------------------------------------------------------------------
static bool g_consume_next = false; // what the callbacks would answer
static bool g_stub_consumed[256];   // by scan code: the filter's memory
static int g_release_all_calls = 0;

// The stub implements the SAME pairing rule as
// mods/input_filter.cpp: a consumed press consumes its repeats and
// its release, and only a release the filter is told about frees the key
// again. Modelling that memory is what makes these tests load-bearing - a host
// that never delivers the release leaves this stuck, and the next press is
// answered as a repeat without asking anybody, which is exactly the bug.
extern "C" bool mods_input_key(uint8_t dik, uint8_t, bool down) {
    if (down) {
        if (g_stub_consumed[dik])
            return true;
        if (g_consume_next) {
            g_stub_consumed[dik] = true;
            return true;
        }
        return false;
    }
    if (g_stub_consumed[dik]) {
        g_stub_consumed[dik] = false;
        return true;
    }
    return false;
}
// The button seam, captured. The mods module is not linked here either, so a
// strong definition wins over the weak one in mods_seam.cpp and lets one test
// see exactly what the gate offered the filter.
static bool g_consume_button = false;
static int g_button_calls = 0;
static int g_button_last_button = -1;
static bool g_button_last_down = false;
static int32_t g_button_last_x = -1, g_button_last_y = -1;

extern "C" bool mods_input_button(int button, bool down, int32_t x, int32_t y) {
    ++g_button_calls;
    g_button_last_button = button;
    g_button_last_down = down;
    g_button_last_x = x;
    g_button_last_y = y;
    return g_consume_button;
}

// Counted, not just accepted: the filter's own state is not linked here, so
// "focus loss clears the filter too" can only be checked by seeing the call.
extern "C" void mods_input_release_all(void) {
    ++g_release_all_calls;
    memset(g_stub_consumed, 0, sizeof g_stub_consumed);
}

// runtime_tests.cpp has a call_import of its own, but it is static there and
// cannot be borrowed.
static uint32_t gate_call_target(X86 *c, uint32_t target, const std::vector<uint32_t> &args) {
    uint32_t before = c->r[R_ESP], esp = before;
    for (size_t i = args.size(); i-- > 0;) {
        esp -= 4;
        wr32(esp, args[i]);
    }
    esp -= 4;
    wr32(esp, 0x00401000u);
    c->r[R_ESP] = esp;
    imports_dispatch(c, target);
    c->r[R_ESP] = before;
    return c->r[R_EAX];
}

static uint32_t gate_call_import(X86 *c, const char *dll, const char *name,
                                 const std::vector<uint32_t> &args) {
    uint32_t tramp = imports_resolve(dll, name);
    if (!tramp) {
        CHECK(tramp != 0);
        return 0;
    }
    return gate_call_target(c, tramp, args);
}

// A COM method, by vtable slot, with the interface pointer as argument zero.
static uint32_t gate_call_method(X86 *c, uint32_t iface, uint32_t slot,
                                 const std::vector<uint32_t> &rest = {}) {
    uint32_t target = rd32(rd32(iface + COM_OFF_vtbl) + slot * 4);
    std::vector<uint32_t> a{iface};
    for (uint32_t v : rest)
        a.push_back(v);
    return gate_call_target(c, target, a);
}

// Scratch inside the guest stack region, below anything this test runs.
static uint32_t gate_scratch(uint32_t off) {
    return STACK_LIMIT + 0x4000u + off;
}
// A real DXGI COM Present must publish a complete owned frame to the host
// mailbox, not merely increment the legacy host_present call counter.
static void test_d3d11_sealed_presentation() {
    mem_init();
    imports_init();
    d3d11_reset();
    com_reset();
    d3d11_register();
    dxgi_register();
    d3dcompiler_register();
    d3dx10_register();
    host_present_test_begin();
    X86 c;
    loader_init_context(&c);
    uint32_t s = gate_scratch(0x2000);
    gm_zero(s, 1024);
    wr32(s, 2);
    wr32(s + 4, 2);
    wr32(s + 16, 28);
    wr32(s + 28, 1);
    wr32(s + 36, 0x20);
    wr32(s + 40, 1);
    CHECK_EQ(gate_call_import(&c, "d3d11.dll", "D3D11CreateDeviceAndSwapChain",
                              {0, 1, 0, 0, 0, 0, 7, s, s + 64, s + 68, s + 72, s + 76}),
             S_OK);
    uint32_t swap = rd32(s + 64), dev = rd32(s + 68), ctx = rd32(s + 76);
    const uint8_t iid[] = {0xf2, 0xaa, 0x15, 0x6f, 0x08, 0xd2, 0x89, 0x4e,
                           0x9a, 0xb4, 0x48, 0x95, 0x35, 0xd3, 0x4f, 0x9c};
    memcpy(gm_ptr(s + 128), iid, 16);
    gate_call_method(&c, swap, 9, {0, s + 128, s + 80});
    uint32_t back = rd32(s + 80);
    gate_call_method(&c, dev, 9, {back, 0, s + 84});
    uint32_t view = rd32(s + 84);
    float colour[] = {1, 0, 0, 1};
    memcpy(gm_ptr(s + 160), colour, 16);
    gate_call_method(&c, ctx, 50, {view, s + 160});
    gate_call_method(&c, swap, 8, {0, 0});
    colour[0] = 0;
    colour[1] = 1;
    memcpy(gm_ptr(s + 160), colour, 16);
    gate_call_method(&c, ctx, 50, {view, s + 160});
    host_present_tick_for_test(0);
    CHECK_EQ(host_present_unique_completed(), 1u);
    CHECK_EQ(host_present_test_last_pixel(), 255u);
    for (uint32_t object : {view, back, ctx, dev, swap})
        gate_call_method(&c, object, 2);
    host_present_stop();
}

static uint32_t gate_put_str(const char *text) {
    static uint32_t cursor = 0;
    uint32_t a = gate_scratch(0x800 + cursor);
    uint32_t n = (uint32_t)strlen(text) + 1;
    memcpy(gm_ptr(a), text, n);
    cursor = (cursor + n + 15u) & ~15u;
    return a;
}

// A window procedure that answers WM_NCCREATE, which a real one must.
static void gate_wndproc(X86 *c) {
    set_eax(c, 1);
}

static uint32_t gate_make_window(X86 *c) {
    if (host_main_window())
        return host_main_window();
    uint32_t wndproc = imports_alloc_trampoline("test", "gate_wndproc", gate_wndproc, 4);
    uint32_t clsname = gate_put_str("PopGateWnd");
    uint32_t wc = gate_scratch(0x100);
    memset(gm_ptr(wc), 0, 40);
    wr32(wc + 0, 3);
    wr32(wc + 4, wndproc);
    wr32(wc + 12, 8);
    wr32(wc + 36, clsname);
    gate_call_import(c, "USER32.dll", "RegisterClassA", {wc});
    return gate_call_import(
        c, "USER32.dll", "CreateWindowExA",
        {0, clsname, gate_put_str("gate"), 0x80000000u, 0, 0, 640, 480, 0, 0, 0x400000, 0});
}

// The next queued message, removed, or 0 when the queue is empty.
static uint32_t gate_next_message(X86 *c, uint32_t *wparam) {
    uint32_t msg = gate_scratch(0x200);
    memset(gm_ptr(msg), 0, 28);
    if (!gate_call_import(c, "USER32.dll", "PeekMessageA", {msg, 0, 0, 0, 1}))
        return 0;
    if (wparam)
        *wparam = rd32(msg + 8);
    return rd32(msg + 4);
}
static void gate_drain(X86 *c) {
    while (gate_next_message(c, nullptr)) {
    }
}

// A buffered keyboard device, set up the way the game sets one up.
static uint32_t gate_make_keyboard(X86 *c, bool mouse = false, bool reset = true) {
    if (reset)
        dinput_reset();
    uint32_t create = imports_resolve("DINPUT.dll", "DirectInputCreateA");
    CHECK(create != 0);
    uint32_t out = gate_scratch(0x300);
    gate_call_target(c, create, {0x400000, 0x0500, out, 0});
    uint32_t di = rd32(out);
    CHECK(di != 0);
    if (!di)
        return 0;

    uint32_t guid = gate_scratch(0x340);
    static const uint8_t KBD[16] = {0x61, 0x2B, 0x1D, 0x6F, 0xA0, 0xD5, 0xCF, 0x11,
                                    0xBF, 0xC7, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00};
    for (int i = 0; i < 16; ++i)
        wr8(guid + (uint32_t)i, KBD[i]);
    if (mouse)
        wr8(guid, 0x60);
    uint32_t devout = gate_scratch(0x360);
    gate_call_method(c, di, 3 /* CreateDevice */, {guid, devout, 0});
    uint32_t kb = rd32(devout);
    CHECK(kb != 0);
    if (!kb)
        return 0;

    uint32_t df = gate_scratch(0x380);
    memset(gm_ptr(df), 0, 24);
    wr32(df + 0, 24);
    wr32(df + 4, 16);
    wr32(df + 12, mouse ? 16 : 256); // c_dfDIKeyboard
    gate_call_method(c, kb, 11 /* SetDataFormat */, {df});
    uint32_t prop = gate_scratch(0x3c0);
    memset(gm_ptr(prop), 0, 20);
    wr32(prop + 0, 20);
    wr32(prop + 4, 16);
    wr32(prop + 12, 0);
    wr32(prop + 16, 32);
    gate_call_method(c, kb, 6 /* SetProperty */, {1 /* DIPROP_BUFFERSIZE */, prop});
    gate_call_method(c, kb, 7 /* Acquire */, {});
    return kb;
}

// How many buffered events the device has for us right now, draining them.
static uint32_t gate_buffered_events(X86 *c, uint32_t kb) {
    dinput_host_input_changed();
    uint32_t count = gate_scratch(0x400), buf = gate_scratch(0x440);
    wr32(count, 8);
    gate_call_method(c, kb, 10 /* GetDeviceData */, {16, buf, count, 0});
    return rd32(count);
}

// Host state, gate state, filter-stub state and the message queue, together:
// a test that reset only some of them would be starting from a state no real
// run is ever in.
static void gate_reset_all(X86 *c) {
    host_gate_reset();
    host_input_reset();
    memset(g_stub_consumed, 0, sizeof g_stub_consumed);
    gate_drain(c);
}

static void test_input_gate() {
    imports_init();
    dinput_register();
    X86 ctx{};
    loader_init_context(&ctx);
    CHECK(gate_make_window(&ctx) != 0);
    uint32_t kb = gate_make_keyboard(&ctx);

    // G, not F10: the settings page reserves DIK 0x44 and would consume it.
    const uint16_t kMacG = 0x05;
    HostKeyMapping m = host_key_mapping(kMacG);
    CHECK(m.dik == 0x22 && m.vk == 'G');

    uint8_t keys[256];
    auto dik_down = [&]() -> uint8_t {
        memset(keys, 0, sizeof keys);
        host_input_peek(nullptr, nullptr, nullptr, nullptr, nullptr, keys);
        return keys[m.dik];
    };
    auto async_down = [&]() -> uint32_t {
        loader_init_context(&ctx);
        return gate_call_import(&ctx, "USER32.dll", "GetAsyncKeyState", {m.vk});
    };

    // ---- not consumed: every channel delivers, exactly as before the gate --
    gate_reset_all(&ctx);
    if (kb)
        gate_buffered_events(&ctx, kb); // drain what setup left
    g_consume_next = false;
    uint32_t notifies = host_input_notify_count();

    CHECK(!host_key_event(kMacG, true, 'G'));
    CHECK(dik_down() == 0x80); // 1. DirectInput state
    if (kb)
        CHECK(gate_buffered_events(&ctx, kb) == 1); // 2. buffered event
    CHECK(async_down() != 0);                       // 3. GetAsyncKeyState
    uint32_t wp = 0;
    CHECK(gate_next_message(&ctx, &wp) == 0x0100); // 4. WM_KEYDOWN
    CHECK(wp == m.vk);
    // A real game loop translates the retrieved key message. There must be
    // no pre-posted character, and exactly one after TranslateMessage.
    CHECK(gate_call_import(&ctx, "USER32.dll", "PeekMessageA",
                           {gate_scratch(0x280), 0, 0x0102, 0x0102, 0}) == 0);
    CHECK(gate_call_import(&ctx, "USER32.dll", "TranslateMessage", {gate_scratch(0x200)}) == 1);
    CHECK(gate_next_message(&ctx, &wp) == 0x0102);
    CHECK(wp == 'g');
    CHECK(gate_next_message(&ctx, nullptr) == 0);
    CHECK(host_input_notify_count() > notifies);
    CHECK(host_guest_key_down(m.vk));

    CHECK(!host_key_event(kMacG, false, 0));
    CHECK(dik_down() == 0);
    CHECK(gate_next_message(&ctx, nullptr) == 0x0101); // WM_KEYUP
    CHECK(!host_guest_key_down(m.vk));

    // ---- consumed: the press, its repeat and its release reach nothing -----
    gate_reset_all(&ctx);
    if (kb)
        gate_buffered_events(&ctx, kb);
    g_consume_next = true;
    notifies = host_input_notify_count();

    CHECK(host_key_event(kMacG, true, 'G')); // the press
    CHECK(host_key_event(kMacG, true, 'G')); // its auto-repeat
    CHECK(host_key_event(kMacG, false, 0));  // and its release
    CHECK(dik_down() == 0);                  // 1. no state
    if (kb)
        CHECK(gate_buffered_events(&ctx, kb) == 0); // 2. nothing to derive
    CHECK(async_down() == 0);                       // 3. nothing to poll
    CHECK(gate_next_message(&ctx, nullptr) == 0);   // 4. nothing posted
    CHECK(host_input_notify_count() == notifies);   // and nothing announced
    CHECK(!host_guest_key_down(m.vk));

    g_consume_next = false;
    gate_reset_all(&ctx);
}

// A consumed MODIFIER is the case the flags-bitmask path nearly gets wrong.
// macOS reports modifiers as a bitmask rather than as key events, so the host
// diffs them, and the diff has to be against the PHYSICAL keyboard: comparing
// against what the guest was told means a consumed press never looks like a
// transition when it comes up, the filter is never told the key was released,
// and every later press is answered as a repeat without asking anybody.
// host_gate_inject_guest_click: what the guest is told, on every path.
//
// The verb exists to place a click where the physical mapping cannot, so what
// has to be checked is that the coordinate the caller gave is the coordinate
// the guest sees - on the filter's offer, on the pointer state and on both
// posted messages - and that a consumed press delivers nothing at all.
// The probe's verdict and the dumpat's firing rule, made without a game.
//
// Both are decisions the smoke host takes on a frame, and both used to be
// buried in the executor where only a full run could reach them. A frame goes
// in and a verdict comes out, so the cases that matter can be stated directly:
// a colour that matches, one that does not, a coordinate off the frame, and no
// frame at all.
// What the scheduler's drain is told, case by case.
//
// The scheduler drains whenever this says yes and asks again at once, so every
// yes has to be work the drain can actually finish. The case that matters most
// is an await: draining runs the tick, the tick looks at a claim that has not
// passed, and nothing changes - so a yes there is a hot spin for the whole
// length of the wait, which on this game's front end is fifteen seconds.
// A run that stopped early is a failed run.
//
// The expectations a truncated run never reached cannot fail, so without this
// rule a script that died halfway reports fewer problems than one that ran to
// the end and failed - which is the wrong way round, and the shape of every
// harness that quietly stops proving things.
static void test_run_unfinished() {
    // Ran every step, ended normally: finished.
    CHECK(!host_script_run_unfinished(0, 43, 43));
    // Stopped early, however it ended.
    CHECK(host_script_run_unfinished(0, 42, 43));
    CHECK(host_script_run_unfinished(0, 0, 43));
    // An abnormal end is unfinished even having run every step: the guest
    // crashed or was cut off after the last one, and what the expectations
    // measured was a run that did not end the way it says it did.
    CHECK(host_script_run_unfinished(1, 43, 43));
    // An empty script is not an unfinished one.
    CHECK(!host_script_run_unfinished(0, 0, 0));
}

static void test_drain_wanted() {
    HostScriptDrainState st;
    auto fresh = [&]() {
        memset(&st, 0, sizeof st);
        st.script_started = 1;
        st.steps_left = 1;
        st.step_due = 1;
    };

    fresh();
    CHECK(host_script_drain_wanted(&st)); // a step is due

    fresh();
    st.step_due = 0;
    CHECK(!host_script_drain_wanted(&st)); // ...and not before it is

    // Before the first tick the clock has no origin yet, so the script has to
    // be looked at once to get one.
    fresh();
    st.script_started = 0;
    st.step_due = 0;
    CHECK(host_script_drain_wanted(&st));

    fresh();
    st.steps_left = 0;
    CHECK(!host_script_drain_wanted(&st)); // nothing left to run

    fresh();
    st.quit_requested = 1;
    CHECK(!host_script_drain_wanted(&st)); // asked to quit

    // THE AWAIT CASE. A step is due and the claim has not passed: this must
    // still be no, or the drain spins for the length of the wait.
    fresh();
    st.await_started = 1;
    CHECK(!host_script_drain_wanted(&st));

    // Another thread is already inside the tick.
    fresh();
    st.ticking = 1;
    CHECK(!host_script_drain_wanted(&st));

    // A held click is work only once its release is owed - the same rule, for
    // the same reason.
    fresh();
    st.holding_button = 1;
    st.hold_reached = 0;
    CHECK(!host_script_drain_wanted(&st));
    st.hold_reached = 1;
    CHECK(host_script_drain_wanted(&st));

    fresh();
    st.guestclick_held = 1;
    st.guestclick_reached = 0;
    CHECK(!host_script_drain_wanted(&st));
    st.guestclick_reached = 1;
    CHECK(host_script_drain_wanted(&st));

    // A recorded path being replayed is the script while it lasts, and an
    // await behind it does not suppress it.
    fresh();
    st.sub_active = 1;
    st.sub_step_due = 1;
    st.await_started = 1;
    CHECK(host_script_drain_wanted(&st));
    st.sub_step_due = 0;
    CHECK(!host_script_drain_wanted(&st));

    CHECK(!host_script_drain_wanted(nullptr));
}

#include "../landmark.h"
#include <algorithm>
static void test_landmark_hidden_evidence() {
    // Simulate the arena disappearing between capture and the landmark
    // executor. The exact JSON consumed by Gate C must retain the own draw.
    HostD3DDrawSnapshot draw{};
    draw.texture_handle = 65673;
    draw.texture_revision = 21979;
    draw.seq = 376;
    draw.screen_min_x = 190;
    draw.screen_min_y = 220;
    draw.screen_max_x = 210;
    draw.screen_max_y = 250;
    LandmarkDrawEvidence record(1828, 1904, draw);
    draw = {};
    char path[512];
    snprintf(path, sizeof path, "%s/pop-landmark-evidence.XXXXXX", os_temp_dir());
    int fd = os_mkstemp(path);
    CHECK(fd >= 0);
    FILE *file = fd >= 0 ? fdopen(fd, "w+") : nullptr;
    CHECK(file != nullptr);
    if (file) {
        record.write(file);
        rewind(file);
        char json[512]{};
        CHECK(fgets(json, sizeof json, file) != nullptr);
        CHECK(strcmp(json, "{\"entity_id\":1828,\"frame\":1904,\"handle\":65673,\"revision\":21979,"
                           "\"seq\":376,\"bounds\":[190,220,210,250]}") == 0);
        fclose(file);
    } else if (fd >= 0)
        os_fd_close(fd);
    if (fd >= 0)
        os_unlink(path);
    LandmarkSpriteEvidence no_sprite{31, 1815, 0, 0, true};
    CHECK(landmark_visibility(true, 1815, 31, true, -80.5f, 230.375f, 540, 480, no_sprite, nullptr,
                              0) == LandmarkVisibility::hidden);
    CHECK(landmark_visibility(false, 1815, 31, true, -80.5f, 230.375f, 540, 480, no_sprite, nullptr,
                              0) == LandmarkVisibility::unavailable);
    CHECK(landmark_visibility(true, 1815, 31, false, -80.5f, 230.375f, 540, 480, no_sprite, nullptr,
                              0) == LandmarkVisibility::unavailable);
    CHECK(landmark_visibility(true, 1815, 31, true, 75.5f, 230.375f, 852, 480, no_sprite, nullptr,
                              0) == LandmarkVisibility::not_drawn);
    HostScriptStep steps[4]{};
    char error[256]{};
    CHECK_EQ(
        host_script_parse("await turn>=861 within 120000\nlandmark 1815 expect hidden within 0\n",
                          steps, 4, error, sizeof error),
        2);
    CHECK_EQ(steps[1].timeout_ms, 0u);
}

static void test_probe_and_dumpat_decisions() {
    // A 4x2 frame: red, green, blue, white on the top row; black beneath.
    const uint8_t frame[4 * 2 * 3] = {
        255, 0, 0, 0, 255, 0, 0, 0, 255, 255, 255, 255, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    };
    int r = -1, g = -1, b = -1, worst = -1;

    // Exact, with no tolerance asked for.
    CHECK_EQ(host_probe_pixel(frame, 4, 2, 0, 0, 255, 0, 0, 0, &r, &g, &b, &worst),
             (int)HOST_PROBE_MATCH);
    CHECK_EQ(r, 255);
    CHECK_EQ(g, 0);
    CHECK_EQ(b, 0);
    CHECK_EQ(worst, 0);

    // Wrong, and it says by how much and what was there.
    CHECK_EQ(host_probe_pixel(frame, 4, 2, 1, 0, 255, 0, 0, 0, &r, &g, &b, &worst),
             (int)HOST_PROBE_MISMATCH);
    CHECK_EQ(r, 0);
    CHECK_EQ(g, 255);
    CHECK_EQ(worst, 255);

    // Within tolerance is a match, and the boundary is inclusive: a channel
    // exactly `tol` out passes and one past it does not.
    CHECK_EQ(
        host_probe_pixel(frame, 4, 2, 3, 0, 247, 247, 247, 8, nullptr, nullptr, nullptr, &worst),
        (int)HOST_PROBE_MATCH);
    CHECK_EQ(worst, 8);
    CHECK_EQ(
        host_probe_pixel(frame, 4, 2, 3, 0, 246, 246, 246, 8, nullptr, nullptr, nullptr, &worst),
        (int)HOST_PROBE_MISMATCH);
    CHECK_EQ(worst, 9);

    // The second row is black, so a probe there is about the frame's layout
    // rather than its first pixel.
    CHECK_EQ(host_probe_pixel(frame, 4, 2, 2, 1, 0, 0, 0, 0, nullptr, nullptr, nullptr, nullptr),
             (int)HOST_PROBE_MATCH);

    // Off the frame in each direction, and a coordinate that would be inside a
    // bigger one.
    CHECK_EQ(host_probe_pixel(frame, 4, 2, 4, 0, 0, 0, 0, 0, nullptr, nullptr, nullptr, nullptr),
             (int)HOST_PROBE_OUTSIDE);
    CHECK_EQ(host_probe_pixel(frame, 4, 2, 0, 2, 0, 0, 0, 0, nullptr, nullptr, nullptr, nullptr),
             (int)HOST_PROBE_OUTSIDE);
    CHECK_EQ(host_probe_pixel(frame, 4, 2, -1, 0, 0, 0, 0, 0, nullptr, nullptr, nullptr, nullptr),
             (int)HOST_PROBE_OUTSIDE);

    // Nothing presented is not the same as a mismatch, and must not read as a
    // pass either: a run whose frame never arrived has not proved anything.
    CHECK_EQ(host_probe_pixel(nullptr, 4, 2, 0, 0, 0, 0, 0, 0, nullptr, nullptr, nullptr, nullptr),
             (int)HOST_PROBE_NO_FRAME);
    CHECK_EQ(host_probe_pixel(frame, 0, 0, 0, 0, 0, 0, 0, 0, nullptr, nullptr, nullptr, nullptr),
             (int)HOST_PROBE_NO_FRAME);

    // ---- the dumpat's firing rule ------------------------------------------
    // Armed, on gameplay, and past the threshold.
    CHECK(host_dumpat_should_fire(1, 1, 101.0, 100.0, 0));
    // Not armed is never.
    CHECK(!host_dumpat_should_fire(0, 1, 101.0, 100.0, 0));
    // NOT on a menu or a movie frame, however well the claim reads. A
    // reference frame is a picture of the simulation; one caught on a menu is
    // a picture of something else, and two runs would not even be comparable.
    CHECK(!host_dumpat_should_fire(1, 0, 101.0, 100.0, 0));
    // `>` and `>=` differ exactly at the threshold, which is the whole reason
    // `await turn>=100` exists rather than turn>99.
    CHECK(!host_dumpat_should_fire(1, 1, 100.0, 100.0, 0));
    CHECK(host_dumpat_should_fire(1, 1, 100.0, 100.0, 1));
    CHECK(!host_dumpat_should_fire(1, 1, 99.0, 100.0, 1));
}

// Exercise the real host_frame_seal factory with a CPU presenter: no Metal,
// guest boot, window, or app. Only guest memory/JSON contents are fixture data.
static HostDumpAt dumpat_request;
static HostDumpAtSample dumpat_sample{true, 0,     821, 839,
                                      1969, 99450, 0,   "pinned start=100 step=50"};
static std::string dumpat_dir;
static unsigned dumpat_factory_calls = 0;
static bool dumpat_fixture_write(const char *name) {
    const uint8_t entities[] = {1, 2, 3, 4}, tribes[] = {5, 6, 7};
    return host_write_simdump(
        dumpat_dir.c_str(), name,
        {{"entities", entities, sizeof entities},
         {"tribes", tribes, sizeof tribes},
         {"turn", &dumpat_sample.turn, 4},
         {"command", &dumpat_sample.command_frame, 4}},
        [](FILE *f) {
            return fprintf(f, "{\"turn\":%u,\"command_frame\":%u,\"entities\":[]}\n",
                           dumpat_sample.turn, dumpat_sample.command_frame) > 0;
        });
}
static bool dumpat_fixture_fire(HostScreenClass cls) {
    dumpat_sample.gameplay = cls == HOST_SCREEN_GAMEPLAY;
    dumpat_sample.value =
        dumpat_request.metric == "turn" ? dumpat_sample.turn : dumpat_sample.command_frame;
    dumpat_sample.frame_id = host_frame_current().id;
    return host_dumpat_fire(dumpat_request, dumpat_sample, dumpat_dir.c_str(), true,
                            dumpat_fixture_write);
}
static HostFrameCapture dumpat_fixture_factory(HostScreenClass cls) {
    ++dumpat_factory_calls;
    if (!dumpat_fixture_fire(cls))
        return {};
    return [](const HostCompletedComposite &) {};
}
static std::string dumpat_read(const std::string &path) {
    std::ifstream f(path, std::ios::binary);
    return {std::istreambuf_iterator<char>(f), std::istreambuf_iterator<char>()};
}
// A late turn await passes without a present. The explicit fired await must
// keep the second request unarmed until the first has actually been captured.
static void test_reference_dump_serialization() {
    char tmp[512];
    snprintf(tmp, sizeof tmp, "%s/pop-reference-dump-test-XXXXXX", os_temp_dir());
    const char *dir = os_mkdtemp(tmp) == 0 ? tmp : nullptr;
    CHECK(dir != nullptr);
    if (!dir)
        return;
    HostScriptStep steps[8]{};
    char err[256]{};
    const char *script = "dumpat turn>=820 ref_t820\n"
                         "await turn>=820 within 120000\nawait dumpat_fired>=1 within 120000\n"
                         "dumpat turn>=860 ref_t860\n"
                         "await turn>=860 within 120000\nawait dumpat_fired>=2 within 120000\n";
    CHECK_EQ(host_script_parse(script, steps, 8, err, sizeof err), 6);
    for (uint32_t initial : {780u, 870u}) {
        HostDumpAt request;
        HostDumpAtSample sample{true, 0, initial, initial + 19, 1900, 95000, 7, "pinned"};
        int next = 0;
        auto tick = [&] {
            while (next < 6) {
                const auto &step = steps[next];
                if (step.op == HOST_SCRIPT_DUMPAT)
                    request.arm(step.name, step.text, step.threshold, step.at_least);
                else {
                    double value = !strcmp(step.name, "turn") ? sample.turn : request.fired;
                    if (value < step.threshold)
                        break;
                }
                ++next;
            }
        };
        tick();
        CHECK_EQ(next, initial < 820 ? 1 : 2);
        CHECK(request.armed && request.name == "ref_t820");
        sample.turn = initial < 820 ? 820 : initial;
        tick(); // the turn passes, but no capture has fired yet
        CHECK_EQ(next, 2);
        CHECK_EQ(request.fired, 0u);
        for (int n = 0; n < 20; ++n)
            tick();
        CHECK_EQ(next, 2);
        CHECK_EQ(request.replaced, 0u);
        auto present = [&] {
            sample.value = sample.turn;
            sample.command_frame = sample.turn + 19;
            ++sample.present;
            sample.guest_ms += 50;
            return host_dumpat_fire(request, sample, dir, false, [](const char *) { return true; });
        };
        CHECK(present());
        CHECK_EQ(request.fired, 1u);
        tick();
        CHECK(request.armed && request.name == "ref_t860");
        sample.turn = initial < 820 ? 860 : initial;
        tick();
        CHECK_EQ(next, 5);
        CHECK(present());
        tick();
        CHECK_EQ(next, 6);
        CHECK_EQ(request.fired, 2u);
        CHECK_EQ(request.unfired(), 0u);
        CHECK_EQ(request.write_failures, 0u);
        const auto provenance = dumpat_read(std::string(dir) + "/smoke_ref_t820_provenance.txt");
        CHECK(provenance.find("armed_on turn>=820\n") != std::string::npos);
        CHECK(provenance.find(initial < 820 ? "turn 820\n" : "turn 870\n") != std::string::npos);
    }
    for (const auto &entry : std::filesystem::directory_iterator(dir))
        std::filesystem::remove(entry.path());
    std::filesystem::remove(dir);
}

static void test_dumpat_present_and_seal() {
    char tmp[512];
    snprintf(tmp, sizeof tmp, "%s/pop-dumpat-test-XXXXXX", os_temp_dir());
    const char *dir = os_mkdtemp(tmp) == 0 ? tmp : nullptr;
    CHECK(dir != nullptr);
    if (!dir)
        return;
    dumpat_dir = dir;
    std::string reference;
    for (bool seal : {false, true}) {
        host_present_test_begin(true, true); // offscreen: capture factory active
        host_present_set_capture_factory(dumpat_fixture_factory);
        dumpat_factory_calls = 0;
        dumpat_request = {};
        test_frame_builder frame;
        g_present_test_current = frame.frame;
        auto present = [&](HostScreenClass cls, uint32_t command, uint32_t turn) {
            frame.screen_class = cls;
            dumpat_sample.command_frame = command;
            dumpat_sample.turn = turn;
            if (seal) {
                host_present_acquire_target(4, 4, 4, 4);
                host_frame_seal(); // actual production factory entry, not test_seal
                host_present_tick_for_test(0);
            } else
                dumpat_fixture_fire(cls);
        };
        dumpat_request.arm("command_frame", "gate_c_t1", 840, true);
        present(HOST_SCREEN_GAMEPLAY, 839, 821);
        CHECK(dumpat_request.armed);
        CHECK(!std::filesystem::exists(dumpat_dir + "/gate_c_t1.command.bin"));
        present(HOST_SCREEN_MENU, 840, 821);
        CHECK(dumpat_request.armed);
        present(HOST_SCREEN_GAMEPLAY, 840, 821);
        CHECK(!dumpat_request.armed);
        CHECK_EQ(dumpat_request.fired, 1u);
        CHECK_EQ(dumpat_request.unfired(), 0u);
        CHECK_EQ(dumpat_request.write_failures, 0u);
        CHECK_EQ(dumpat_read(dumpat_dir + "/gate_c_t1.entities.bin").size(), 4u);
        CHECK_EQ(dumpat_read(dumpat_dir + "/gate_c_t1.tribes.bin").size(), 3u);
        for (const auto &counter : {std::pair{"turn", 821u}, std::pair{"command", 840u}}) {
            auto data = dumpat_read(dumpat_dir + "/gate_c_t1." + counter.first + ".bin");
            CHECK_EQ(data.size(), 4u);
            uint32_t value = 0;
            if (data.size() == 4)
                memcpy(&value, data.data(), 4);
            CHECK_EQ(value, counter.second);
        }
        CHECK(dumpat_read(dumpat_dir + "/gate_c_t1.entities.json").find("\"command_frame\":840") !=
              std::string::npos);
        auto provenance = dumpat_read(dumpat_dir + "/smoke_gate_c_t1_provenance.txt");
        CHECK(provenance.find("armed_on command_frame>=840\nturn 821\ncommand_frame 840\npresent "
                              "1969\nguest_ms 99450\nclock pinned start=100 step=50\n") !=
              std::string::npos);
        // The fixture frame ID differs between iterations; all other evidence is identical.
        auto common = provenance.substr(0, provenance.find("frame_id "));
        if (!seal)
            reference = common;
        else
            CHECK(common == reference);
        present(HOST_SCREEN_GAMEPLAY, 845, 825);
        CHECK_EQ(dumpat_request.fired, 1u);
        CHECK(dumpat_read(dumpat_dir + "/smoke_gate_c_t1_provenance.txt") == provenance);
        dumpat_request.arm("turn", "turn_anchor", 830, true);
        present(HOST_SCREEN_GAMEPLAY, 850, 829);
        CHECK(dumpat_request.armed);
        present(HOST_SCREEN_GAMEPLAY, 853, 832); // first present overshoots anchor
        CHECK_EQ(dumpat_request.fired, 2u);
        CHECK(dumpat_read(dumpat_dir + "/smoke_turn_anchor_provenance.txt")
                  .find("turn 832\ncommand_frame 853\n") != std::string::npos);
        dumpat_request.arm("command_frame", "never", 900, true);
        present(HOST_SCREEN_GAMEPLAY, 899, 880);
        present(HOST_SCREEN_MENU, 901, 882);
        CHECK_EQ(dumpat_request.unfired(), 1u); // production end-of-run failure tally
        CHECK(!std::filesystem::exists(dumpat_dir + "/never.command.bin"));
        dumpat_request.arm("turn", "replacement", 999, true);
        CHECK_EQ(dumpat_request.unfired(), 2u); // replaced plus still armed
        CHECK_EQ(dumpat_request.unfired(), 2u); // tally must be idempotent
        if (seal)
            CHECK_EQ(dumpat_factory_calls, 8u);
        // Evidence write failure must also fail a request that did fire.
        const auto saved_dir = dumpat_dir;
        dumpat_dir += "/missing";
        present(HOST_SCREEN_GAMEPLAY, 1100, 1000);
        CHECK_EQ(dumpat_request.write_failures, 1u);
        dumpat_dir = saved_dir;
        host_present_stop();
        g_present_test_current = {};
        for (const auto &entry : std::filesystem::directory_iterator(dumpat_dir))
            std::filesystem::remove(entry.path());
    }
    std::filesystem::remove(dumpat_dir);
}

static void test_guest_click_injection() {
    X86 ctx;
    memset(&ctx, 0, sizeof ctx);
    loader_init_context(&ctx);
    uint32_t hwnd = gate_make_window(&ctx);
    // A click is routed by hit test, and nothing is under the pointer while
    // the window is hidden. Validating it keeps the update region from
    // answering every peek with WM_PAINT; the window goes back to hidden at
    // the end because the gate tests share it.
    gate_call_import(&ctx, "USER32.dll", "ShowWindow", {hwnd, 5});
    uint32_t ps = gate_scratch(0x280);
    memset(gm_ptr(ps), 0, 64);
    gate_call_import(&ctx, "USER32.dll", "BeginPaint", {hwnd, ps});
    gate_call_import(&ctx, "USER32.dll", "EndPaint", {hwnd, ps});
    gate_drain(&ctx);

    g_consume_button = false;
    g_button_calls = 0;
    const uint32_t notifies = host_input_notify_count();

    // ---- the press ---------------------------------------------------------
    CHECK(!host_gate_inject_guest_click(320, 140, 0, true));
    // What the filter was offered: the button, the direction and the place.
    CHECK_EQ(g_button_calls, 1);
    CHECK_EQ(g_button_last_button, 0);
    CHECK(g_button_last_down);
    CHECK_EQ(g_button_last_x, 320);
    CHECK_EQ(g_button_last_y, 140);
    // The pointer the guest reads, placed and not nudged.
    int32_t px = -1, py = -1;
    host_input_peek(&px, &py, nullptr, nullptr, nullptr, nullptr);
    CHECK_EQ(px, 320);
    CHECK_EQ(py, 140);
    // Two messages, in this order, both carrying the same place: the move
    // first so a window that reads the position from it agrees with the click.
    uint32_t wp = 0;
    CHECK_EQ(gate_next_message(&ctx, &wp), 0x0200u); // WM_MOUSEMOVE
    CHECK_EQ(gate_next_message(&ctx, &wp), 0x0201u); // WM_LBUTTONDOWN
    CHECK_EQ(wp & 1u, 1u);                           // MK_LBUTTON set
    CHECK_EQ(gate_next_message(&ctx, nullptr), 0u);
    CHECK(host_input_notify_count() > notifies);

    // ---- the release -------------------------------------------------------
    CHECK(!host_gate_inject_guest_click(320, 140, 0, false));
    CHECK_EQ(g_button_calls, 2);
    CHECK(!g_button_last_down);
    CHECK_EQ(gate_next_message(&ctx, &wp), 0x0200u); // WM_MOUSEMOVE
    CHECK_EQ(gate_next_message(&ctx, &wp), 0x0202u); // WM_LBUTTONUP
    CHECK_EQ(wp & 1u, 0u);                           // and MK_LBUTTON clear
    CHECK_EQ(gate_next_message(&ctx, nullptr), 0u);

    // ---- the right button, at another place --------------------------------
    CHECK(!host_gate_inject_guest_click(12, 34, 1, true));
    CHECK_EQ(g_button_last_button, 1);
    CHECK_EQ(g_button_last_x, 12);
    CHECK_EQ(g_button_last_y, 34);
    CHECK_EQ(gate_next_message(&ctx, &wp), 0x0200u);
    CHECK_EQ(gate_next_message(&ctx, &wp), 0x0204u); // WM_RBUTTONDOWN
    CHECK_EQ(wp & 2u, 2u);                           // MK_RBUTTON
    host_gate_inject_guest_click(12, 34, 1, false);
    gate_drain(&ctx);

    // ---- consumed: nothing on any path -------------------------------------
    g_consume_button = true;
    const uint32_t before = host_input_notify_count();
    CHECK(host_gate_inject_guest_click(500, 400, 0, true));
    CHECK_EQ(gate_next_message(&ctx, nullptr), 0u); // no message
    CHECK_EQ(host_input_notify_count(), before);    // nothing announced
    host_input_peek(&px, &py, nullptr, nullptr, nullptr, nullptr);
    CHECK_EQ(px, 12); // and not moved
    CHECK_EQ(py, 34);
    g_consume_button = false;
    gate_call_import(&ctx, "USER32.dll", "ShowWindow", {hwnd, 0});
}

static void test_input_gate_modifiers() {
    imports_init();
    X86 ctx{};
    loader_init_context(&ctx);
    CHECK(gate_make_window(&ctx) != 0);

    const uint32_t kLeftShiftFlags = 0x00000002u | 0x00020000u;
    const uint32_t kLeftAltFlags = 0x00000020u | 0x00080000u;
    HostKeyMapping shift = host_key_mapping(host_modifier_key(0));
    CHECK(shift.dik == 0x2a); // DIK_LSHIFT

    uint8_t keys[256];
    auto shift_down = [&]() -> uint8_t {
        memset(keys, 0, sizeof keys);
        host_input_peek(nullptr, nullptr, nullptr, nullptr, nullptr, keys);
        return keys[shift.dik];
    };

    // Consumed press, then release, then a fresh press. The release must reach
    // the filter, so the fresh press is a decision and not a stuck repeat.
    gate_reset_all(&ctx);
    g_consume_next = true;
    host_modifier_event(kLeftShiftFlags);
    CHECK(shift_down() == 0);                     // never delivered
    CHECK(gate_next_message(&ctx, nullptr) == 0); // and never posted
    // A consumed Shift is not in the modifiers the guest knows, so it cannot
    // appear in a mouse message's flags either.
    CHECK((host_guest_modifiers() & 1) == 0);
    host_modifier_event(0); // physically released

    // Now let it through: the filter was told about the release, so this is a
    // fresh press rather than a repeat it already answered.
    g_consume_next = false;
    host_modifier_event(kLeftShiftFlags);
    CHECK(shift_down() == 0x80);
    CHECK((host_guest_modifiers() & 1) != 0);
    uint32_t wp = 0;
    CHECK(gate_next_message(&ctx, &wp) == 0x0100); // WM_KEYDOWN
    CHECK(wp == shift.vk);
    host_modifier_event(0);
    CHECK(shift_down() == 0);
    CHECK((host_guest_modifiers() & 1) == 0);
    CHECK(gate_next_message(&ctx, nullptr) == 0x0101); // WM_KEYUP

    // An UNCONSUMED Alt release is still a WM_SYSKEYUP: Alt was down when the
    // keystroke happened even though it is not down now, which is the one case
    // where the two Alt questions differ.
    gate_drain(&ctx);
    host_modifier_event(kLeftAltFlags);
    CHECK((host_guest_modifiers() & 4) != 0);
    CHECK(gate_next_message(&ctx, nullptr) == 0x0104); // WM_SYSKEYDOWN
    host_modifier_event(0);
    CHECK(gate_next_message(&ctx, nullptr) == 0x0105); // WM_SYSKEYUP
    CHECK((host_guest_modifiers() & 4) == 0);

    // A CONSUMED Alt must not change how the next ordinary key is classified,
    // because the guest never learned Alt was down.
    gate_drain(&ctx);
    g_consume_next = true;
    host_modifier_event(kLeftAltFlags);
    CHECK((host_guest_modifiers() & 4) == 0);
    g_consume_next = false;
    CHECK(!host_key_event(0x05 /* G */, true, 'G'));
    CHECK(gate_next_message(&ctx, nullptr) == 0x0100); // WM_KEYDOWN, not SYS
    host_key_event(0x05, false, 0);
    host_modifier_event(0);

    // FOCUS GAIN WITH A MODIFIER ALREADY HELD.
    //
    // Focus loss clears every layer. macOS then sends no flagsChanged for a
    // modifier that was already down, so the first keystroke after regaining
    // focus would be classified against a state saying Option is up: an
    // ordinary WM_KEYDOWN with a WM_CHAR, where Win32 sends WM_SYSKEYDOWN
    // with the Alt context bit and no character at all.
    gate_reset_all(&ctx);
    g_consume_next = false;
    host_modifier_event(kLeftAltFlags); // held before focus was lost
    CHECK((host_guest_modifiers() & 4) != 0);
    host_gate_release_all(); // the window lost the focus
    CHECK(host_guest_modifiers() == 0);
    gate_drain(&ctx);

    // Focus comes back, Option still physically held, and nothing announced it.
    host_gate_sync_modifiers(kLeftAltFlags);
    CHECK((host_guest_modifiers() & 4) != 0);
    CHECK(gate_next_message(&ctx, &wp) == 0x0104); // the Alt down it missed
    CHECK(gate_next_message(&ctx, nullptr) == 0);  // and nothing else

    // Now type. Alt is down as far as the guest is concerned, so this is a
    // system keystroke with the context bit set and no WM_CHAR behind it.
    CHECK(!host_key_event(0x05 /* G */, true, 'G'));
    uint32_t msg = gate_scratch(0x200);
    memset(gm_ptr(msg), 0, 28);
    CHECK(gate_call_import(&ctx, "USER32.dll", "PeekMessageA", {msg, 0, 0, 0, 1}) != 0);
    CHECK(rd32(msg + 4) == 0x0104); // WM_SYSKEYDOWN
    CHECK(rd32(msg + 8) == 'G');
    CHECK((rd32(msg + 12) & (1u << 29)) != 0);    // lParam bit 29, Alt context
    CHECK(gate_next_message(&ctx, nullptr) == 0); // no WM_CHAR for a sys key

    // Resynchronising again with the same flags is a no-op: no phantom edges.
    host_gate_sync_modifiers(kLeftAltFlags);
    CHECK(gate_next_message(&ctx, nullptr) == 0);
    host_key_event(0x05, false, 0);
    host_modifier_event(0);
    gate_drain(&ctx);

    // Focus loss clears BOTH layers. Without the filter's, a consumed key it
    // still believes is held answers the next press as a repeat.
    gate_reset_all(&ctx);
    g_consume_next = false;
    host_key_event(0x05, true, 'G');
    CHECK(host_guest_key_down('G'));
    int releases = g_release_all_calls;
    host_gate_release_all();
    // The mod filter is told as well. Without this it keeps the key it
    // consumed and answers the next press as a repeat, asking nobody.
    CHECK(g_release_all_calls == releases + 1);
    CHECK(!host_guest_key_down('G'));
    CHECK(host_guest_modifiers() == 0);
    memset(keys, 0, sizeof keys);
    host_input_peek(nullptr, nullptr, nullptr, nullptr, nullptr, keys);
    CHECK(keys[0x22] == 0);
    gate_drain(&ctx);
}

// ---------------------------------------------------------------------------
// The settings page is drawn on a copy, never on the guest's surface.
//
// Every presenter is handed a pointer straight into the guest's DirectDraw
// surface. Drawing the page there would corrupt pixels the game is still
// reading and would make a mod's overlay part of the simulation's own input.
// The mods module is not linked here, so this file supplies mods_page_draw -
// weak in mods_seam.cpp - and has it scribble, which is the only way to tell
// a copy that was drawn on from a copy that was not.
// ---------------------------------------------------------------------------
static bool g_page_draws = false;
extern "C" bool mods_page_visible() {
    return g_page_draws;
}
extern "C" void mods_page_draw(void *pixels, int w, int h, int bpp, int pitch,
                               const uint32_t *palette) {
    (void)w;
    (void)bpp;
    (void)palette;
    if (!g_page_draws || !pixels)
        return;
    uint8_t *p = (uint8_t *)pixels;
    for (int y = 0; y < h; ++y)
        p[(size_t)y * (size_t)pitch] = 0xab;
}

// Run 39 (NFSMW 3.0.4): the tabbed menu's renderer, weak in mods_seam.cpp (0 = hidden). Visible, it draws when its
// generation moved since the caller's (2) and otherwise says unchanged (1), as mods_menu_render does.
static bool g_menu_visible = false;
static uint64_t g_menu_gen = 0;
extern "C" int mods_menu_render(int w, int h, uint64_t *generation, const uint8_t **pixels) {
    if (!g_menu_visible)
        return 0;
    if (*generation == g_menu_gen)
        return 1;
    static std::vector<uint8_t> page;
    page.assign((size_t)w * (size_t)h * 4, 0x40);
    *generation = g_menu_gen;
    *pixels = page.data();
    return 2;
}

// Run 39 (NFSMW 3.0.4, D-202 item 4): while the menu has the guest parked nothing is sealed, so the menu froze on
// screen. host_present_menu_refresh, from the park loop, redraws a changed page and the next repeat composes it over
// the held frame: no new frame id. Controls: an unchanged page and a hidden one cost nothing and recompose nothing.
static void test_menu_redrawn_while_parked() {
    host_page_set_enabled(true);
    g_menu_visible = true;
    g_menu_gen = 1;
    host_present_test_begin();
    host_present_acquire_target(640, 480, 640, 480);
    host_present_test_seal(1);
    host_present_tick_for_test(0.01);
    host_present_test_command_done(1);
    host_present_test_presented(1, 0.02);
    CHECK_EQ(host_present_unique_completed(), 1u);
    const uint64_t pages = host_present_settings_pages();   // the test seal path attaches no page
    // A repeat is in flight (made before the menu changed), as nearly always in the real app.
    host_present_tick_for_test(0.025);
    CHECK_EQ(host_present_repeats(), 1u);
    const uint64_t old_page = host_present_test_page_id();
    // Parked: the cursor moved (a new generation) and no frame is sealed.
    g_menu_gen = 2;
    host_present_menu_refresh();
    CHECK_EQ(host_present_menu_refreshes(), 1u);
    CHECK_EQ(host_present_settings_pages(), pages + 1);
    const uint64_t new_page = host_present_test_page_id();
    CHECK(new_page != old_page);
    // That repeat retires after the refresh: it must not put its older page back (the real-app freeze).
    host_present_test_command_done(1);
    host_present_test_presented(1, 0.028);
    CHECK_EQ(host_present_test_page_id(), new_page);
    host_present_tick_for_test(0.03);
    CHECK_EQ(host_present_repeats(), 2u);
    CHECK_EQ(host_present_test_recomposed(), 1u);  // the repeat composes the new page
    CHECK_EQ(host_present_test_last_id(), 1u);     // the held frame: no new id
    host_present_test_command_done(1);
    host_present_test_presented(1, 0.04);
    CHECK_EQ(host_present_unique_completed(), 1u);
    CHECK_EQ(host_present_test_page_id(), new_page);
    // Control: nothing changed on the page: no texture, the next repeat reuses the composition.
    host_present_menu_refresh();
    CHECK_EQ(host_present_menu_refreshes(), 1u);
    host_present_tick_for_test(0.05);
    CHECK_EQ(host_present_repeats(), 3u);
    CHECK_EQ(host_present_test_recomposed(), 1u);
    host_present_test_command_done(1);
    host_present_test_presented(1, 0.06);
    // Control: the menu closed: nothing drawn.
    g_menu_visible = false;
    g_menu_gen = 3;
    host_present_menu_refresh();
    CHECK_EQ(host_present_menu_refreshes(), 1u);
    host_present_stop();
    host_present_menu_refresh();                   // no presenter: a no-op
    host_page_set_enabled(false);
    g_menu_gen = 0;
}

static void test_page_draws_on_a_copy() {
    host_page_set_enabled(true);
    const int w = 64, h = 32, pitch = 80; // a padded stride, as a lock gives
    std::vector<uint8_t> guest((size_t)pitch * h);
    for (size_t i = 0; i < guest.size(); ++i)
        guest[i] = (uint8_t)(i * 7 + 1);
    const std::vector<uint8_t> before = guest;

    // Page hidden: nothing is drawn and the guest's bytes are untouched.
    g_page_draws = false;
    const void *out = host_page_overlay(guest.data(), w, h, 8, pitch, nullptr);
    CHECK(out != nullptr);
    CHECK(memcmp(guest.data(), before.data(), guest.size()) == 0);
    CHECK(memcmp(out, before.data(), guest.size()) == 0);

    // Page open: the copy carries the page, and the guest's surface does not.
    g_page_draws = true;
    out = host_page_overlay(guest.data(), w, h, 8, pitch, nullptr);
    CHECK(out != guest.data()); // never in place
    CHECK(memcmp(guest.data(), before.data(), guest.size()) == 0);
    const uint8_t *drawn = (const uint8_t *)out;
    bool scribbled = true;
    for (int y = 0; y < h; ++y)
        if (drawn[(size_t)y * pitch] != 0xab)
            scribbled = false;
    CHECK(scribbled);
    // Everything the page did not touch still reads as the frame it copied.
    CHECK(drawn[1] == before[1]);
    CHECK(drawn[(size_t)(h - 1) * pitch + 5] == before[(size_t)(h - 1) * pitch + 5]);

    // A non-positive pitch is the tightly packed case, not a crash.
    g_page_draws = false;
    out = host_page_overlay(guest.data(), w, h, 8, 0, nullptr);
    CHECK(out != nullptr);
    CHECK(memcmp(guest.data(), before.data(), guest.size()) == 0);

    // WITH MODS DISABLED NOTHING HAPPENS AT ALL. Not merely "the page is not
    // drawn": the page must not be registered either, because its key handler
    // consumes F10 unconditionally and every key once open, and a run with
    // mods off has to deliver exactly the input a run without the foundation
    // delivered. The frame comes back as the caller's own pointer, uncopied.
    g_page_draws = true;
    host_page_set_enabled(false);
    out = host_page_overlay(guest.data(), w, h, 8, pitch, nullptr);
    CHECK(out == guest.data());
    CHECK(memcmp(guest.data(), before.data(), guest.size()) == 0);

    g_page_draws = false;
    host_page_set_enabled(false);
}

// ---------------------------------------------------------------------------
// Pointer capture.
//
// The bug this exists for: the guest reads its mouse as DirectInput relative
// deltas AND asks the host where the cursor is. With the OS pointer free those
// two answers come from different places, so moving the pointer out of the
// window leaves the guest's cursor somewhere the host has stopped updating and
// the two never agree again. Captured, the absolute position IS the sum of the
// deltas, clamped to the frame, so there is only one answer.
// ---------------------------------------------------------------------------
static void test_fullscreen_edge_presentation() {
    // A captured pointer is confined in every window mode: a plain window
    // that let the mouse leave parked the guest cursor on the frame's edge,
    // which an edge-scrolling game read as a hand holding it there.
    CHECK(host_pointer_confinement_wanted(true, 0));
    CHECK(host_pointer_confinement_wanted(true, 1));
    CHECK(host_pointer_confinement_wanted(true, 2));
    CHECK(!host_pointer_confinement_wanted(false, 0));
    CHECK(!host_pointer_confinement_wanted(false, 2));

    // A clipped OS pointer must still reach every game edge, at Retina and
    // 4K sizes. Test the last complete point (max - 1), overrun, monotonic
    // motion and the flipped vertical axis; without remapping, the final
    // rows/columns would be unreachable and edge scrolling would still stop.
    const double sizes[][2] = {{1512, 949}, {1920, 1080}, {960, 600}};
    for (auto size : sizes) {
        const HostRect clip = host_pointer_confinement_rect({0, 0, size[0], size[1]});
        CHECK(clip.x > 0 && clip.y > 0);
        CHECK(clip.x + clip.w < size[0] && clip.y + clip.h < size[1]);
        const int w = int(size[0] * 2), h = int(size[1] * 2);
        CHECK_EQ(host_confined_pointer_pixel(clip.x, clip.x, clip.w, w), 0);
        CHECK_EQ(host_confined_pointer_pixel(clip.x + clip.w - 1, clip.x, clip.w, w), w - 1);
        CHECK_EQ(host_confined_pointer_pixel(clip.y, clip.y, clip.h, h, true), h - 1);
        CHECK_EQ(host_confined_pointer_pixel(clip.y + clip.h - 1, clip.y, clip.h, h, true), 0);
        CHECK_EQ(host_confined_pointer_pixel(-100, clip.x, clip.w, w), 0);
        CHECK_EQ(host_confined_pointer_pixel(10000, clip.y, clip.h, h, true), 0);
        int previous = -1;
        for (double x = clip.x; x < clip.x + clip.w; x += 0.5) {
            int pixel = host_confined_pointer_pixel(x, clip.x, clip.w, w);
            CHECK(pixel >= previous && pixel < w);
            previous = pixel;
        }
    }
    CHECK(host_pointer_confinement_rect({0, 0, 0, 0}).empty());
    CHECK_EQ(host_confined_pointer_pixel(5, 0, 0, 0), 0);
}

static void test_pointer_capture() {
    host_gate_reset();
    host_pointer_set_mode(640, 480);

    // Released: a delta belongs to the desktop, not to the guest.
    CHECK(!host_pointer_captured());
    int32_t x = -1, y = -1;
    host_pointer_cursor(&x, &y);
    CHECK(x == 320 && y == 240);
    CHECK(!host_pointer_motion(50, 50));
    host_pointer_cursor(&x, &y);
    CHECK(x == 320 && y == 240); // did not move

    // Captured: motion accumulates.
    host_pointer_capture(true);
    CHECK(host_pointer_captured());
    host_pointer_center();
    CHECK(host_pointer_motion(10, -20));
    host_pointer_cursor(&x, &y);
    CHECK(x == 330 && y == 220);
    CHECK(host_pointer_motion(-30, 5));
    host_pointer_cursor(&x, &y);
    CHECK(x == 300 && y == 225);

    // It cannot leave the frame, however hard it is pushed, and a motion that
    // changes nothing reports that it changed nothing.
    CHECK(host_pointer_motion(-100000, -100000));
    host_pointer_cursor(&x, &y);
    CHECK(x == 0 && y == 0);
    CHECK(!host_pointer_motion(-10, -10)); // already against the corner
    host_pointer_cursor(&x, &y);
    CHECK(x == 0 && y == 0);
    CHECK(host_pointer_motion(100000, 100000));
    host_pointer_cursor(&x, &y);
    CHECK(x == 639 && y == 479); // the far corner, not beyond it
    CHECK(!host_pointer_motion(1, 1));

    // A mode change brings the cursor inside the new frame rather than leaving
    // it outside a smaller one.
    host_pointer_set_mode(320, 200);
    host_pointer_cursor(&x, &y);
    CHECK(x == 319 && y == 199);
    host_pointer_set_mode(640, 480);

    // Releasing keeps the position, so re-capturing does not teleport the
    // guest's cursor to somewhere it never was.
    host_pointer_center();
    CHECK(host_pointer_motion(40, 30));
    host_pointer_capture(false);
    CHECK(!host_pointer_captured());
    CHECK(!host_pointer_motion(500, 500)); // ignored while released
    host_pointer_cursor(&x, &y);
    CHECK(x == 360 && y == 270);
    host_pointer_capture(true);
    host_pointer_cursor(&x, &y);
    CHECK(x == 360 && y == 270); // exactly where it was left

    // Focus loss releases it: the window no longer owns the pointer and the
    // user has to be able to reach everything else.
    CHECK(host_pointer_captured());
    host_gate_release_all();
    CHECK(!host_pointer_captured());
    CHECK(!host_pointer_motion(10, 10));
    host_pointer_cursor(&x, &y);
    CHECK(x == 360 && y == 270);

    host_gate_reset();
}

// Task 9: physical layout mapping, separate from Task 8 presenter tests.
static CompositorInput t9_layout(UiFrame *ui) {
    CompositorInput in{};
    in.cls = HOST_SCREEN_GAMEPLAY;
    in.ui = ui;
    in.guest_w = 640;
    in.guest_h = 480;
    in.drawable_w = 3840;
    in.drawable_h = 2160;
    in.scene = {3840.0f / 853, 4.5f, 0, 0, 853};
    return in;
}
static UiElement t9_element(uint64_t id, int x, int y, int w, int h, uint32_t seq) {
    UiElement e{};
    e.id = id;
    e.x = x;
    e.y = y;
    e.w = w;
    e.h = h;
    e.is_hud = true;
    e.last_seq = seq;
    return e;
}
static void test_t9_hits_and_drag() {
    host_gate_reset();
    UiFrame ui{{t9_element(9, 600, 440, 40, 40, 2)}, 640, 480};
    auto in = t9_layout(&ui);
    auto hit = host_gate_hit_test(&in, 3760, 2080);
    CHECK_EQ(hit.kind, HitResult::HIT_ELEMENT);
    CHECK_EQ(hit.element, 9);
    CHECK_EQ(hit.gx, 620);
    CHECK_EQ(hit.gy, 460);
    ui.elements.push_back(t9_element(10, 600, 440, 40, 40, 1));
    CHECK_EQ(host_gate_hit_test(&in, 3760, 2080).element, 9);
    ui.elements.back().last_seq = 3;
    hit = host_gate_hit_test(&in, 3760, 2080);
    CHECK_EQ(hit.element, 10);
    host_gate_begin_drag(&hit);
    // Ownership is a value: neither frame retirement nor new layouts change it.
    host_gate_set_layout(&in);
    ui.elements.clear();
    in.drawable_w = 1920;
    host_gate_set_layout(&in);
    hit = host_gate_hit_test(nullptr, 3600, 1920);
    CHECK_EQ(hit.kind, HitResult::HIT_ELEMENT);
    CHECK_EQ(hit.element, 10);
    CHECK_EQ(hit.gx, 580);
    CHECK_EQ(hit.gy, 420);
    host_gate_end_drag();
    in = t9_layout(&ui);
    hit = host_gate_hit_test(&in, 1920, 1080);
    CHECK_EQ(hit.kind, HitResult::HIT_SCENE);
    CHECK_EQ(hit.gx, 426);
    CHECK_EQ(hit.gy, 240);
    CHECK_EQ(host_gate_hit_test(&in, -1, 100).kind, HitResult::HIT_NONE);
    host_gate_reset();
}
static void test_t9_snapshot_capture_edges_cursor() {
    host_gate_reset();
    UiFrame ui{{t9_element(9, 600, 440, 40, 40, 2)}, 640, 480};
    auto in = t9_layout(&ui);
    host_gate_set_layout(&in);
    ui.elements.clear(); // Published layout must own no UiFrame storage.
    CHECK_EQ(host_gate_hit_test(nullptr, 3760, 2080).element, 9);
    // More than three publications exercises reuse of every mailbox slot.
    for (int i = 0; i < 8; ++i) {
        in.scene.domain_w = 853 + i;
        host_gate_set_layout(&in);
    }
    in = t9_layout(&ui);
    host_gate_set_layout(&in);
    host_pointer_capture(true);
    host_pointer_center();
    int32_t dx, dy;
    auto hit = host_gate_pointer_event(0, 0, in.scene.scale_x * 10, 9, &dx, &dy);
    CHECK_EQ(hit.gx, 436);
    CHECK_EQ(hit.gy, 242);
    CHECK_EQ(dx, 10);
    CHECK_EQ(dy, 2);
    hit = host_gate_pointer_event(0, 0, 100000, 100000, &dx, &dy);
    CHECK_EQ(hit.gx, 852);
    CHECK_EQ(hit.gy, 479);
    hit = host_gate_pointer_event(0, 0, -100000, -100000, &dx, &dy);
    CHECK_EQ(hit.gx, 0);
    CHECK_EQ(hit.gy, 0);
    CHECK(dx < -10000);
    CHECK(dy < -10000); // Physical overshoot pins the guest.
    host_gate_pointer_event(0, 0, -double(in.scene.scale_x) * 7, 0, &dx, &dy);
    CHECK_EQ(dx, -7);
    // A guest cursor record at stale coordinates must draw at the host pointer.
    ui.elements = {t9_element(99, 100, 100, 8, 8, 100)};
    ui.elements[0].is_cursor = true;
    host_pointer_capture(false);
    hit = host_gate_pointer_event(3760, 2080, 0, 0, &dx, &dy);
    int x, y, w, h;
    CHECK(compositor_element_rect_on_drawable(&in, 99, &x, &y, &w, &h));
    CHECK_EQ(x, 3760);
    CHECK_EQ(y, 2080);
    CHECK_EQ(w, 32);
    CHECK_EQ(h, 32);
    host_gate_set_layout(&in);
    CHECK_EQ(host_gate_hit_test(nullptr, 3760, 2080).kind, HitResult::HIT_SCENE);
    host_gate_reset();
}

static void test_t9_relative_crossing_and_layout_mailbox() {
    host_gate_reset();
    UiFrame ui{{t9_element(9, 600, 440, 40, 40, 2)}, 640, 480};
    auto in = t9_layout(&ui);
    host_gate_set_layout(&in);
    int32_t dx, dy;
    auto scene = host_gate_pointer_event(3600, 1960, 0, 0, &dx, &dy);
    CHECK_EQ(scene.kind, HitResult::HIT_SCENE);
    auto hud = host_gate_pointer_event(3680, 2000, 0, 0, &dx, &dy);
    CHECK_EQ(hud.kind, HitResult::HIT_ELEMENT);
    CHECK_EQ(hud.gx, 600);
    CHECK_EQ(hud.gy, 440);
    // This is the correction main.mm delivers to DirectInput, not a zero-delta
    // guestclick. Crossing the anchor moves the integrated pointer to its rect.
    CHECK_EQ(scene.gx + dx, 600);
    CHECK_EQ(scene.gy + dy, 440);
    host_gate_begin_drag(&hud);
    host_pointer_capture(true);
    auto drag = host_gate_pointer_event(0, 0, -80, -80, &dx, &dy);
    CHECK_EQ(drag.element, 9);
    CHECK_EQ(drag.gx, 580);
    CHECK_EQ(drag.gy, 420);
    CHECK_EQ(dx, -20);
    CHECK_EQ(dy, -20);
    host_gate_release_all();
    CHECK_EQ(host_gate_hit_test(nullptr, 3600, 1960).kind, HitResult::HIT_SCENE);
    host_pointer_capture(true);
    host_pointer_center();
    int total = 0;
    for (int i = 0; i < 100; ++i) {
        host_gate_pointer_event(0, 0, in.scene.scale_x / 10.0, 0, &dx, &dy);
        total += dx;
    }
    CHECK_EQ(total, 10); // Subpixel motion is not rounded away per event.
    host_gate_reset();
    // A real presenter producer and baton consumer: all fields in one hit
    // must come from one immutable publication, even after slot reuse.
    std::atomic<bool> done = false;
    std::thread writer([&] {
        UiFrame frame{{t9_element(1, 600, 440, 40, 40, 1)}, 640, 480};
        auto next = t9_layout(&frame);
        for (int i = 0; i < 1000; ++i) {
            frame.elements[0].id = (i % 2) + 1;
            frame.elements[0].x = i % 2 ? 590 : 600;
            host_gate_set_layout(&next);
        }
        done = true;
    });
    int observed = 0;
    do {
        const auto hit = host_gate_hit_test(nullptr, 3760, 2080);
        if (hit.kind != HitResult::HIT_NONE) {
            ++observed;
            CHECK(hit.element == 1 || hit.element == 2);
            CHECK_EQ(hit.guest.x, hit.element == 1 ? 600 : 590);
            CHECK_EQ(hit.gx, 620);
        }
    } while (!done);
    writer.join();
    CHECK_EQ(host_gate_hit_test(nullptr, 3760, 2080).element, 2);
    (void)observed;
    // Letterboxed scene edges inside the drawable must not trigger scroll.
    ui.elements.clear();
    in = t9_layout(&ui);
    in.legacy = true;
    host_gate_set_layout(&in);
    CHECK_EQ(host_gate_hit_test(nullptr, 480, 1080).gx, 1);
    CHECK_EQ(host_gate_hit_test(nullptr, 3359, 1080).gx, 638);
    CHECK_EQ(host_gate_hit_test(nullptr, 0, 1080).gx, 0);
    CHECK_EQ(host_gate_hit_test(nullptr, 3839, 1080).gx, 639);
    host_gate_reset();
}

// The pair form (NFS Underground: x 0x0070649c, y 0x007064a0, guard = its DirectInput mouse pointer 0x00706498).
static void test_guest_pointer_pair_resolution() {
    std::vector<uint8_t> arena(4096);
    auto put = [&](uint32_t address, uint32_t value) { memcpy(arena.data() + address, &value, sizeof value); };
    auto name = [](HostGuestPointer p) { return std::string(host_guest_pointer_failure_name(p.failure)); };
    auto resolve = [&](int w, int h) { return host_guest_pointer_resolve_pair(arena.data(), arena.size(), 0x100, 0x104, 0x108, w, h); };
    put(0x104, 400);
    put(0x108, 300);
    CHECK(name(resolve(800, 600)) == "object-null"); // the game's mouse does not exist yet
    put(0x100, 0x01234560);
    auto p = resolve(800, 600);
    CHECK(name(p) == "ready");
    CHECK_EQ(p.x, 400);
    CHECK_EQ(p.y, 300);
    CHECK_EQ(p.right, 799);
    CHECK_EQ(p.bottom, 599);
    CHECK_EQ(p.x_addr, 0x104u);
    CHECK_EQ(p.y_addr, 0x108u);
    put(0x104, 800); // the game's clamp keeps the client width itself
    CHECK(name(resolve(800, 600)) == "ready");
    put(0x104, 801);
    CHECK(name(resolve(800, 600)) == "coordinates-outside-bounds");
    put(0x104, uint32_t(-1));
    CHECK(name(resolve(800, 600)) == "coordinates-outside-bounds");
    put(0x104, 10);
    CHECK(name(resolve(0, 600)) == "bounds-invalid");
    CHECK(name(host_guest_pointer_resolve_pair(arena.data(), arena.size(), 0x100, 4094, 0x108, 800, 600)) ==
          "object-outside-arena");
    CHECK(name(host_guest_pointer_resolve_pair(arena.data(), arena.size(), 0, 0x104, 0x108, 800, 600)) ==
          "object-outside-arena");
    CHECK(name(host_guest_pointer_resolve_pair(nullptr, arena.size(), 0x100, 0x104, 0x108, 800, 600)) ==
          "arena-unavailable");
}

static void test_t9_guest_pointer_resolution() {
    // A separate arena proves resolution does not accidentally read g_mem or
    // a cached absolute coordinate address. object models ECX at 0052d430.
    std::vector<uint8_t> arena(4096);
    uint32_t object = 0x100;
    auto put = [&](uint32_t address, uint32_t value) {
        memcpy(arena.data() + address, &value, sizeof value);
    };
    auto resolve = [&] { return host_guest_pointer_resolve(arena.data(), arena.size(), object); };
    auto reason = [&](HostGuestPointer p, const char *expected) {
        CHECK(strcmp(host_guest_pointer_failure_name(p.failure), expected) == 0);
    };
    put(object, RECOMP_HOOK_MOUSE_VTABLE); // 0052cbe0 constructs the object, not a pointer slot.
    put(object + 0x1c, 0x800);
    put(object + 0x20, 123);
    put(object + 0x24, 234);
    put(object + 0x40, 639);
    put(object + 0x44, 479);
    auto p = resolve();
    reason(p, "ready");
    CHECK_EQ(p.object, 0x100);
    CHECK_EQ(p.context, 0x800);
    CHECK_EQ(p.vtable, RECOMP_HOOK_MOUSE_VTABLE);
    CHECK_EQ(p.x, 123);
    CHECK_EQ(p.y, 234);
    // Fresh this pointers and fresh contents on each invocation.
    memcpy(arena.data() + 0x200, arena.data() + object, 0x48);
    object = 0x200;
    put(object + 0x20, 321);
    put(object + 0x24, 432);
    put(object + 0x1c, 0x900);
    p = resolve();
    reason(p, "ready");
    CHECK_EQ(p.object, 0x200);
    CHECK_EQ(p.context, 0x900);
    CHECK_EQ(p.x, 321);
    CHECK_EQ(p.y, 432);
    object = 0;
    p = resolve();
    reason(p, "object-null");
    CHECK_EQ(p.x, 0);
    CHECK_EQ(p.y, 0);
    object = 0xfffffffc;
    reason(resolve(), "object-outside-arena");
    object = arena.size() - 0x47;
    reason(resolve(), "object-outside-arena");
    reason(host_guest_pointer_resolve(nullptr, arena.size(), 0x100), "arena-unavailable");
    reason(host_guest_pointer_resolve(arena.data(), 0, 0x100), "object-outside-arena");
    object = 0x200;
    put(object + 0x1c, 0);
    p = resolve();
    reason(p, "ready");
    CHECK_EQ(p.context, 0);
    CHECK_EQ(p.x, 321);
    CHECK_EQ(p.y, 432);
    CHECK_EQ(p.right, 639);
    // Even an unmapped context is informational and must not be followed.
    put(object + 0x1c, 0xfffffffc);
    p = resolve();
    reason(p, "ready");
    CHECK_EQ(p.context, 0xfffffffc);
    put(object + 0x1c, arena.size() - 0xe3);
    reason(resolve(), "ready");
    put(object + 0x1c, 0);
    put(object, 0);
    reason(resolve(), "vtable-mismatch");
    put(object, 0x00591b70);
    reason(resolve(), "vtable-mismatch");
    put(object, RECOMP_HOOK_MOUSE_VTABLE);
    put(object + 0x40, 0);
    reason(resolve(), "bounds-invalid");
    // The cap is generous, not the boot mode: 800x600 Classic bounds are valid
    // and only an implausible screen is refused.
    put(object + 0x40, 4096);
    reason(resolve(), "bounds-invalid");
    put(object + 0x40, 799);
    put(object + 0x44, 599);
    put(object + 0x20, 700);
    put(object + 0x24, 500);
    reason(resolve(), "ready");
    put(object + 0x40, 639);
    put(object + 0x44, 479);
    put(object + 0x20, 123);
    put(object + 0x24, 234);
    put(object + 0x38, uint32_t(-1));
    reason(resolve(), "bounds-invalid");
    put(object + 0x38, 0);
    put(object + 0x44, 0);
    reason(resolve(), "bounds-invalid");
    put(object + 0x44, 4096);
    reason(resolve(), "bounds-invalid");
    put(object + 0x44, 479);
    put(object + 0x20, 640);
    reason(resolve(), "coordinates-outside-bounds");
    put(object + 0x20, 639);
    put(object + 0x24, 480);
    reason(resolve(), "coordinates-outside-bounds");
    put(object + 0x24, 479);
    reason(resolve(), "ready"); // Inclusive clamp edges.
    // Coordinates can be screen-valid but outside this object's own bounds.
    put(object + 0x38, 100);
    put(object + 0x3c, 50);
    put(object + 0x40, 400);
    put(object + 0x44, 300);
    put(object + 0x20, 401);
    put(object + 0x24, 200);
    reason(resolve(), "coordinates-outside-bounds");
    put(object + 0x20, 99);
    reason(resolve(), "coordinates-outside-bounds");
    put(object + 0x20, 100);
    put(object + 0x24, 49);
    reason(resolve(), "coordinates-outside-bounds");
    put(object + 0x24, 50);
    reason(resolve(), "ready");
}

// Fake the real input object's guest memory, not the host's cursor estimate.
// A screen whose cursor pair never answers our corrections must fall back to
// position differencing rather than driving the cursor toward a dead target.
// A layout rescale must never leave the virtual pointer off the frame, and an
// off-frame pointer must not be corrected from: both produced a target at the
// bottom-right corner, which is what the user saw as the cursor snapping away.
static bool native_place_enabled;
static int native_place_calls, native_place_x, native_place_y, native_place_w, native_place_h;
extern "C" int recomp_pointer_place(int32_t x, int32_t y, int32_t w, int32_t h) {
    if (!native_place_enabled)
        return 0;
    ++native_place_calls;
    native_place_x = x;
    native_place_y = y;
    native_place_w = w;
    native_place_h = h;
    return 1;
}

static void test_native_touch_placement() {
    host_gate_reset();
    host_input_reset();
    host_pointer_set_mode(1280, 720);
    host_gate_fallback_layout(2560, 1600); // 80-pixel top/bottom letterbox
    native_place_enabled = true;
    native_place_calls = 0;
    host_input_motion(900, 180, 300, -50);
    host_input_button(0, true);
    host_input_key(0x00, true); // A
    host_input_wheel(120);
    CHECK(host_gate_pointer_place(1800, 440));
    CHECK_EQ(native_place_calls, 1);
    CHECK_EQ(native_place_x, 900);
    CHECK_EQ(native_place_y, 180);
    CHECK_EQ(native_place_w, 1280);
    CHECK_EQ(native_place_h, 720);
    HostInputState state{};
    host_input_state(&state);
    CHECK_EQ(state.mouse_dx, 0);
    CHECK_EQ(state.mouse_dy, 0);
    CHECK_EQ(state.mouse_dz, 120);
    CHECK_EQ(state.mouse_buttons[0], 0x80);
    CHECK_EQ(state.keys[0x1e], 0x80);
    g_page_draws = true;
    CHECK(!host_gate_pointer_place(300, 300));
    g_page_draws = false;
    CHECK(!host_gate_pointer_place(-100, 300));
    CHECK_EQ(native_place_calls, 1);
    HitResult hit;
    host_gate_window_motion(1600, 600, 0, 0, &hit);
    CHECK_EQ(native_place_calls, 1); // Physical motion stays relative.
    native_place_enabled = false;
    host_gate_reset();
    host_input_reset();
}

// The game's pointer cell for the closed-loop tests that read the result back: the pair globals when [hooks]
// mouse_pair_x is set (NFS Underground), else the object's +0x20/+0x24. arm() makes either form resolve; the
// object's bounds are +0x40/+0x44, the pair's are the display mode the test sets. The destructor restores both.
struct TestPointerCell {
    static constexpr uint32_t base = RECOMP_HOOK_MOUSE_DEVICE_PTR;
#ifdef RECOMP_HOOK_MOUSE_PAIR_X
    static constexpr uint32_t x_addr = RECOMP_HOOK_MOUSE_PAIR_X, y_addr = RECOMP_HOOK_MOUSE_PAIR_Y,
                              guard = RECOMP_HOOK_MOUSE_PAIR_GUARD;
#else
    static constexpr uint32_t x_addr = base + 0x20, y_addr = base + 0x24, guard = 0;
#endif
    uint8_t saved[0x48];
    uint32_t saved_x, saved_y, saved_guard, held_x = 0;
    TestPointerCell() {
        memcpy(saved, gm_ptr(base), sizeof saved);
        saved_x = rd32(x_addr);
        saved_y = rd32(y_addr);
        saved_guard = guard ? rd32(guard) : 0;
    }
    ~TestPointerCell() {
        memcpy(gm_ptr(base), saved, sizeof saved);
        wr32(x_addr, saved_x);
        wr32(y_addr, saved_y);
        if (guard)
            wr32(guard, saved_guard);
    }
    void arm(int right, int bottom) {
        memset(gm_ptr(base), 0, 0x48);
        wr32(base, RECOMP_HOOK_MOUSE_VTABLE);
        wr32(base + 0x40, right);
        wr32(base + 0x44, bottom);
        if (guard)
            wr32(guard, 1); // the game's mouse exists
    }
    void put(int x, int y) {
        wr32(x_addr, x);
        wr32(y_addr, y);
    }
    void move(int32_t dx, int32_t dy) {
        put(int(rd32(x_addr)) + dx, int(rd32(y_addr)) + dy);
    }
    // Make the pointer unreadable the way each form can be: the object's rectangle empty or the pair's mouse
    // missing; then the coordinates outside the object's rectangle (right = 199) or past the pair's frame.
    void fail_bounds() {
        wr32(base + 0x40, 0);
        if (guard)
            wr32(guard, 0);
    }
    void fail_coordinates() {
        wr32(base + 0x40, 199);
        if (guard) {
            wr32(guard, 1);
            held_x = rd32(x_addr);
            wr32(x_addr, 5000);
        }
    }
    void recover(int right) {
        wr32(base + 0x40, right);
        if (guard) {
            wr32(guard, 1);
            wr32(x_addr, held_x);
        }
    }
    // An uninitialised guest: no object, no pair mouse.
    void clear() {
        memset(gm_ptr(base), 0, 0x48);
        if (guard)
            wr32(guard, 0);
    }
    uint32_t x() const { return rd32(x_addr); }
    uint32_t y() const { return rd32(y_addr); }
    TestPointerCell(const TestPointerCell &) = delete;
    TestPointerCell &operator=(const TestPointerCell &) = delete;
};
// A pair cursor's bounds are the frame the game draws (run 8): NFS Underground draws 800x600 through Direct3D 9, which
// reports no display mode, so bounds from the 640x480 default kept the cursor out of a menu's right and bottom.
static void test_pair_pointer_bounds_follow_the_drawn_frame() {
#if defined(RECOMP_HOOK_MOUSE_PAIR_X) && !defined(RECOMP_HOOK_MOUSE_PAIR_SPACE_W)
    TestPointerCell cell;
    host_gate_reset();
    host_input_reset();
    cell.arm(639, 479);
    cell.put(700, 550);
    auto in = t9_layout(nullptr);
    in.cls = HOST_SCREEN_MENU;
    in.guest_w = 800;
    in.guest_h = 600;
    host_gate_set_layout(&in);
    host_gate_hit_test(nullptr, 0, 0); // the guest side takes the published layout
    const HostGuestPointer p = host_guest_pointer_current();
    CHECK(p.failure == HostGuestPointer::None);
    CHECK_EQ(p.right, 799);
    CHECK_EQ(p.bottom, 599);
    host_gate_reset();
    host_input_reset();
#endif
}
// A menu drawn as a 4:3 box centred in a wide frame (NFS Underground, run 9): with the box published
// (host_display_set_ui_box), the pointer maps into the box, so its middle is the cursor space's middle and the bars
// are its edges.
static void test_pair_pointer_maps_into_the_ui_box() {
#ifdef RECOMP_HOOK_MOUSE_PAIR_X
    TestPointerCell cell;
    host_gate_reset();
    host_input_reset();
    cell.arm(639, 479);
    cell.put(10, 10);
    auto in = t9_layout(nullptr);
    in.cls = HOST_SCREEN_MENU;
    in.guest_w = in.drawable_w = 1920;
    in.guest_h = in.drawable_h = 1080;
    in.scene = {1.0f, 1.0f, 0, 0, 1920};
    host_gate_set_layout(&in);
    host_gate_hit_test(nullptr, 0, 0);
    const HostGuestPointer p = host_guest_pointer_current();
    CHECK(p.failure == HostGuestPointer::None);
    host_display_set_ui_box(0.125f, 0.0f, 0.75f, 1.0f);
    CHECK(host_gate_pointer_place(960, 540));
    CHECK(std::abs(int(cell.x()) - (p.right + 1) / 2) <= 1);
    CHECK(std::abs(int(cell.y()) - (p.bottom + 1) / 2) <= 1);
    CHECK(host_gate_pointer_place(100, 540)); // the left bar: the box's left edge
    CHECK_EQ(cell.x(), uint32_t(p.left));
    CHECK(host_gate_pointer_place(1919, 540)); // the right bar
    CHECK_EQ(cell.x(), uint32_t(p.right));
    host_display_set_ui_box(0.0f, 0.0f, 1.0f, 1.0f); // without a box: the whole frame
    CHECK(host_gate_pointer_place(240, 540));
    CHECK(std::abs(int(cell.x()) - int(std::lround(240.0 / 1919 * p.right))) <= 1);
    host_gate_reset();
    host_input_reset();
#endif
}
static void test_t9_pointer_survives_layout_rescale() {
    TestPointerCell cell;
    host_gate_reset();
    host_input_reset();
    cell.arm(639, 479);
    cell.put(320, 240);
    auto big = t9_layout(nullptr);
    big.cls = HOST_SCREEN_MENU;
    big.drawable_w = 3840;
    big.drawable_h = 2160;
    host_gate_set_layout(&big);
    host_pointer_capture(true);
    HitResult hit;
    CHECK(host_gate_window_motion(3000, 1800, 0, 0, &hit));
    // A much smaller layout arrives: the pointer must land inside it.
    auto small = big;
    small.drawable_w = 640;
    small.drawable_h = 480;
    small.scene.scale_x = 1;
    small.scene.scale_y = 1;
    host_gate_set_layout(&small);
    int32_t dx = 0, dy = 0;
    host_input_pointer_correction(&dx, &dy);
    double px = 0, py = 0;
    host_pointer_drawable_position(&px, &py);
    CHECK(px >= 0 && px < small.drawable_w);
    CHECK(py >= 0 && py < small.drawable_h);
    CHECK(std::abs(dx) <= 64 && std::abs(dy) <= 64);
}

static void test_t9_pointer_loop_gives_up_when_unanswered() {
    TestPointerCell cell;
    host_gate_reset();
    host_input_reset();
    cell.arm(639, 479);
    cell.put(100, 100); // never updated below
    auto in = t9_layout(nullptr);
    in.cls = HOST_SCREEN_MENU;
    in.drawable_w = 1280;
    in.drawable_h = 960;
    host_gate_set_layout(&in);
    HitResult hit;
    CHECK(host_gate_window_motion(400, 300, 0, 0, &hit));
    bool gave_up = false;
    for (int i = 0; i < 200 && !gave_up; ++i) {
        int32_t dx = 0, dy = 0;
        host_input_pointer_correction(&dx, &dy);
        // The guest is ignoring us: it stays at (100,100) for ever.
        if (!dx && !dy)
            gave_up = true;
    }
    CHECK(gave_up);
    // The pair moving on its own re-arms the loop.
    cell.put(150, 140);
    int32_t dx = 0, dy = 0;
    host_input_pointer_correction(&dx, &dy);
    // Main's foreign-writer protection first adopts that external movement.
    // Correction resumes on the following poll instead of fighting the write.
    CHECK_EQ(dx, 0);
    CHECK_EQ(dy, 0);
    host_input_pointer_correction(&dx, &dy);
    CHECK(dx || dy);
}

// Within the correction's deadband the cursor is where the user is pointing.
static bool near(uint32_t got, uint32_t want) {
    return got + 4 >= want && want + 4 >= got;
}

static void test_t9_pointer_closed_loop() {
    TestPointerCell cell;
    for (bool captured : {false, true}) {
        host_gate_reset();
        host_input_reset();
        cell.arm(639, 479); // Constructed cursor with no attached context.
        cell.put(100, 100);
        auto in = t9_layout(nullptr);
        in.cls = HOST_SCREEN_MENU;
        in.drawable_w = 1280;
        in.drawable_h = 960;
        host_gate_set_layout(&in);
        host_pointer_capture(captured);
        HitResult hit;
        CHECK(host_gate_window_motion(400, 300, 0, 0, &hit));
        CHECK_EQ(hit.gx, 200);
        CHECK_EQ(hit.gy, 150);
        HostInputState state{};
        host_input_state(&state);
        CHECK_EQ(state.mouse_dx, 50);
        CHECK_EQ(state.mouse_dy, 25); // half of the gap
        // That delta went to the guest: a real guest applies it, and the next
        // correction is owed only once its own coordinates have moved.
        cell.move(state.mouse_dx, state.mouse_dy);
        // Batch multiple OS events while the guest has not applied anything.
        CHECK(host_gate_window_motion(400, 300, 0, 0, &hit));
        CHECK(host_gate_window_motion(400, 300, 0, 0, &hit));
        // Those deliveries carry corrections too; a guest applies what it read.
        host_input_state(&state);
        cell.move(state.mouse_dx, state.mouse_dy);
        int steps = 0, idle = 0;
        for (; steps < 20; ++steps) {
            int32_t dx = 999, dy = 999;
            host_input_pointer_correction(&dx, &dy);
            CHECK(std::abs(dx) <= 64 && std::abs(dy) <= 64);
            if (!dx && !dy) {
                if (++idle >= 2)
                    break;
                continue;
            }
            idle = 0;
            cell.move(dx, dy);
        }
        CHECK(steps <= 6);
        CHECK(near(cell.x(), 200u));
        CHECK(near(cell.y(), 150u));
        // A guest clamp or dropped motion introduces a large divergence.
        // Exactly 300 pixels on both axes: four 64s and a final 44.
        cell.put(500, 450);
        idle = 0;
        for (steps = 0; steps < 30; ++steps) {
            int32_t dx = 0, dy = 0;
            host_input_pointer_correction(&dx, &dy);
            CHECK(std::abs(dx) <= 64 && std::abs(dy) <= 64);
            if (!dx && !dy) {
                if (++idle >= 2)
                    break;
                continue;
            }
            idle = 0;
            CHECK(dx <= 0 && dy <= 0); // never away from the target
            cell.move(dx, dy);
        }
        CHECK(steps < 30);
        CHECK(near(cell.x(), 200u));
        CHECK(near(cell.y(), 150u));
        // Polled far more often than the guest applies motion. Each poll may
        // ask again, but never for more than the poll before and never in the
        // opposite direction, so a guest that lags cannot be sent sailing past
        // the target and back - the "mouse keeps moving" the user reported.
        cell.put(120, 120);
        int32_t prev_x = 1 << 30, prev_y = 1 << 30;
        for (int poll = 0; poll < 8; ++poll) {
            int32_t dx = 999, dy = 999;
            host_input_pointer_correction(&dx, &dy);
            CHECK(dx >= 0 && dy >= 0); // target is up-right
            // A round that asks for nothing is the adopt round after a write
            // the loop did not make; the next request may resume from there.
            if (!dx && !dy) {
                prev_x = 1 << 30;
                prev_y = 1 << 30;
                continue;
            }
            CHECK(dx <= prev_x && dy <= prev_y); // never growing
            prev_x = dx;
            prev_y = dy;
        }
        // With the guest applying what it reads, it lands exactly.
        for (int i = 0, z = 0; i < 40; ++i) {
            int32_t dx = 999, dy = 999;
            host_input_pointer_correction(&dx, &dy);
            if (!dx && !dy) {
                if (++z >= 2)
                    break;
                continue;
            }
            z = 0;
            cell.move(dx, dy);
        }
        CHECK(near(cell.x(), 200u));
        CHECK(near(cell.y(), 150u));
        host_gate_release_all();
        int32_t dx = 7, dy = -3;
        host_input_pointer_correction(&dx, &dy);
        CHECK_EQ(dx, 7);
        CHECK_EQ(dy, -3);
    }
    // Exercise real buffered DI delivery, with a keyboard poll draining the
    // host accumulator and three window events before a single guest read.
    imports_init();
    dinput_register();
    X86 c{};
    loader_init_context(&c);
    uint32_t keyboard = gate_make_keyboard(&c);
    uint32_t mouse = gate_make_keyboard(&c, true, false);
    const uint32_t out = gate_scratch(0x1200), count = gate_scratch(0x1180);
    host_gate_reset();
    host_input_reset();
    cell.arm(639, 479);
    cell.put(100, 100);
    auto buffered_layout = t9_layout(nullptr);
    buffered_layout.cls = HOST_SCREEN_MENU;
    buffered_layout.drawable_w = 640;
    buffered_layout.drawable_h = 480;
    host_gate_set_layout(&buffered_layout);
    HitResult buffered_hit;
    for (int i = 0; i < 3; ++i)
        CHECK(host_gate_window_motion(200, 150, 0, 0, &buffered_hit));
    gate_call_method(&c, keyboard, 9, {256, out});
    for (int delivery = 0; delivery < 4; ++delivery) {
        wr32(count, 10);
        gate_call_method(&c, mouse, 10, {16, out, count, 0});
        int32_t dx = 0, dy = 0;
        for (uint32_t n = 0; n < rd32(count); ++n) {
            if (rd32(out + n * 16) == 0)
                dx += int32_t(rd32(out + n * 16 + 4));
            if (rd32(out + n * 16) == 4)
                dy += int32_t(rd32(out + n * 16 + 4));
        }
        CHECK(std::abs(dx) <= 64 && std::abs(dy) <= 64);
        // The guest drops this one. Whether the loop asks again immediately or
        // adopts what it finds first, it must never ask for the wrong sign.
        if (delivery == 0) {
            CHECK(dx >= 0 && dy >= 0);
            continue;
        }
        cell.move(dx, dy);
    }
    // Damping needs more than four deliveries; drain the rest.
    for (int i = 0, z = 0; i < 40; ++i) {
        wr32(count, 10);
        gate_call_method(&c, mouse, 10, {16, out, count, 0});
        int32_t dx = 0, dy = 0;
        for (uint32_t n = 0; n < rd32(count); ++n) {
            if (rd32(out + n * 16) == 0)
                dx += int32_t(rd32(out + n * 16 + 4));
            if (rd32(out + n * 16) == 4)
                dy += int32_t(rd32(out + n * 16 + 4));
        }
        if (!dx && !dy) {
            if (++z >= 2)
                break;
            continue;
        }
        z = 0;
        cell.move(dx, dy);
    }
    CHECK(near(cell.x(), 200u));
    CHECK(near(cell.y(), 150u));
    // Five actual DirectInput deliveries converge 300 pixels with no context
    // and no further OS events. Exercise both correction signs together.
    cell.put(100, 450);
    CHECK(host_gate_window_motion(400, 150, 0, 0, &buffered_hit));
    CHECK_EQ(buffered_hit.gx, 400);
    CHECK_EQ(buffered_hit.gy, 150);
    // Damped deliveries: each is toward the target, none exceeds the clamp,
    // and the pair lands exactly. Both signs are exercised at once.
    int deliveries = 0, idle = 0;
    for (; deliveries < 40; ++deliveries) {
        wr32(count, 10);
        gate_call_method(&c, mouse, 10, {16, out, count, 0});
        int32_t dx = 0, dy = 0;
        for (uint32_t n = 0; n < rd32(count); ++n) {
            if (rd32(out + n * 16) == 0)
                dx += int32_t(rd32(out + n * 16 + 4));
            if (rd32(out + n * 16) == 4)
                dy += int32_t(rd32(out + n * 16 + 4));
        }
        if (!dx && !dy) {
            if (++idle >= 2)
                break;
            continue;
        }
        idle = 0;
        CHECK(dx >= 0 && dy <= 0);
        CHECK(dx <= 64 && dy >= -64);
        cell.move(dx, dy);
    }
    CHECK(deliveries < 40);
    CHECK(near(cell.x(), 400u));
    CHECK(near(cell.y(), 150u));
    gate_call_method(&c, mouse, 2, {});
    gate_call_method(&c, keyboard, 2, {});
    // Distinct failures on the production delivery path preserve fallback
    // motion. Repeating them must produce only one diagnostic per reason.
    cell.fail_bounds();
    for (int i = 0; i < 2; ++i) {
        int32_t dx = 9, dy = -4;
        host_input_pointer_correction(&dx, &dy);
        CHECK_EQ(dx, 9);
        CHECK_EQ(dy, -4);
    }
    cell.fail_coordinates();
    wr32(TestPointerCell::base + 0x1c, 0xfffffffcu); // an unmapped context (the object form's)
    for (int i = 0; i < 2; ++i) {
        int32_t dx = 9, dy = -4;
        host_input_pointer_correction(&dx, &dy);
        CHECK_EQ(dx, 9);
        CHECK_EQ(dy, -4);
    }
    cell.recover(639);
    {
        int32_t dx = 9, dy = -4;
        host_input_pointer_correction(&dx, &dy);
        CHECK_EQ(dx, 0);
        CHECK_EQ(dy, 0);
    } // Recovers even with an unmapped context.
    // Uninitialised guest uses the old position difference, including fractions.
    host_gate_reset();
    host_input_reset();
    cell.clear();
    auto in = t9_layout(nullptr);
    in.cls = HOST_SCREEN_MENU;
    in.drawable_w = 1280;
    in.drawable_h = 960;
    host_gate_set_layout(&in);
    HitResult hit;
    HostInputState state{};
    CHECK(host_gate_window_motion(400, 300, 0, 0, &hit));
    host_input_state(&state);
    CHECK_EQ(state.mouse_dx, 0);
    CHECK_EQ(state.mouse_dy, 0);
    CHECK(host_gate_window_motion(401, 301, 0, 0, &hit));
    host_input_state(&state);
    CHECK_EQ(state.mouse_dx, 0);
    CHECK_EQ(state.mouse_dy, 0);
    CHECK(host_gate_window_motion(402, 302, 0, 0, &hit));
    host_input_state(&state);
    CHECK_EQ(state.mouse_dx, 1);
    CHECK_EQ(state.mouse_dy, 1);
    int32_t dx = 9, dy = -4;
    host_input_pointer_correction(&dx, &dy);
    CHECK_EQ(dx, 9);
    CHECK_EQ(dy, -4);
    host_gate_reset();
    host_input_reset();
}

// R3 run 17 (D-119): after the window is resized to another aspect ratio the picture sits inside bars (here a 4:3 mode
// in a 16:9 drawable: Classic fits 960x720 at x 160, a menu frame the integer 640x480 at 320,120), and the cursor must land
// where the pointer is ON THE PICTURE. The DirectInput correction used to span the whole drawable: the pointer at
// x 280 (Classic guest 80) drove the cursor to 140, at x 400 (menu guest 80) to 200.
static void test_t9_pointer_letterbox() {
    TestPointerCell cell;
    for (bool classic : {false, true}) {
        host_gate_reset();
        host_input_reset();
        cell.arm(639, 479);
        cell.put(100, 100);
        auto in = t9_layout(nullptr);
        in.cls = classic ? HOST_SCREEN_GAMEPLAY : HOST_SCREEN_MENU;
        in.classic = classic;
        in.drawable_w = 1280;
        in.drawable_h = 720;
        host_gate_set_layout(&in);
        HitResult hit;
        CHECK(host_gate_window_motion(classic ? 280 : 400, 360, 0, 0, &hit));
        CHECK_EQ(hit.gx, 80);
        CHECK_EQ(hit.gy, 240);
        HostInputState state{};
        host_input_state(&state);
        cell.move(state.mouse_dx, state.mouse_dy);
        int idle = 0;
        for (int steps = 0; steps < 30; ++steps) {
            int32_t dx = 0, dy = 0;
            host_input_pointer_correction(&dx, &dy);
            if (!dx && !dy) {
                if (++idle >= 2)
                    break;
                continue;
            }
            idle = 0;
            cell.move(dx, dy);
        }
        CHECK(near(cell.x(), 80u));
        CHECK(near(cell.y(), 240u));
    }
    host_gate_reset();
    host_input_reset();
}

static void test_t9_window_motion_round1() {
    // The exact production mapping/filter/DirectInput path used by main.mm.
    // Repeat at 1x/2x backing and multiple composed sizes, over menu elements,
    // letterboxing, subpixel moves, capture and release. The model guest only
    // integrates deltas and clamps at 640x480, just like the game's menu.
    for (int scale : {1, 2, 4})
        for (bool captured : {false, true}) {
            host_gate_reset();
            host_input_reset();
            UiFrame ui{{t9_element(10, 250, 180, 140, 40, 1)}, 640, 480};
            auto in = t9_layout(&ui);
            in.cls = HOST_SCREEN_MENU;
            in.drawable_w = 800 * scale;
            in.drawable_h = 480 * scale;
            host_gate_set_layout(&in);
            HitResult hit;
            CHECK(host_gate_window_motion(400 * scale, 240 * scale, 0, 0, &hit));
            HostInputState state{};
            host_input_state(&state);
            CHECK_EQ(state.mouse_dx, 0);
            CHECK_EQ(state.mouse_dy, 0);
            host_pointer_capture(captured);
            int gx = 320, gy = 240;
            for (int cycle = 0; cycle < 20; ++cycle)
                for (int direction : {1, -1}) {
                    for (int n = 1; n <= 80; ++n) {
                        const int offset = direction > 0 ? n : 80 - n;
                        // Both capture states difference window positions; the raw
                        // device deltas do not determine cursor gain.
                        CHECK(host_gate_window_motion((400 + offset) * scale,
                                                      (240 - offset) * scale, direction * scale,
                                                      -direction * scale, &hit));
                        host_input_state(&state);
                        CHECK_EQ(state.mouse_dx, direction);
                        CHECK_EQ(state.mouse_dy, -direction);
                        gx = std::clamp(gx + state.mouse_dx, 0, 639);
                        gy = std::clamp(gy + state.mouse_dy, 0, 479);
                        CHECK_EQ(gx, 320 + offset);
                        CHECK_EQ(gy, 240 - offset);
                        CHECK(gx > 0 && gx < 639 && gy > 0 && gy < 479);
                        host_input_state(&state); // A second poll cannot replay motion.
                        CHECK_EQ(state.mouse_dx, 0);
                        CHECK_EQ(state.mouse_dy, 0);
                    }
                }
        }
    // Permission is click-scoped, not an automatic consequence of focus or
    // the menu closing; holding Escape forbids recapture on another click.
    CHECK(host_pointer_can_capture(true, true, true, false, false));
    CHECK(!host_pointer_can_capture(true, true, false, false, false));
    CHECK(!host_pointer_can_capture(false, true, true, false, false));
    CHECK(!host_pointer_can_capture(true, false, true, false, false));
    CHECK(!host_pointer_can_capture(true, true, true, true, false));
    CHECK(!host_pointer_can_capture(true, true, true, false, true));
    CHECK(host_pointer_at_resize_edge(4, 200, 640, 480, 8));
    CHECK(host_pointer_at_resize_edge(637, 200, 640, 480, 8));
    CHECK(host_pointer_at_resize_edge(200, 2, 640, 480, 8));
    CHECK(host_pointer_at_resize_edge(200, 479, 640, 480, 8));
    CHECK(!host_pointer_at_resize_edge(320, 240, 640, 480, 8));
    host_gate_reset();
}

// The real game's buffered reader (0052ceda) sums X/Y events, then
// 0052d1e0 adds those counts with gain 1 and 0052d270 clamps the cursor.
// Exercise the production window gate, host accumulation and DI device reads.
static void test_t9_window_tracking_round2() {
    imports_init();
    dinput_register();
    X86 c{};
    loader_init_context(&c);
    uint32_t keyboard = gate_make_keyboard(&c);
    uint32_t mouse = gate_make_keyboard(&c, true, false);
    const uint32_t out = gate_scratch(0x1200), count = gate_scratch(0x1180);
    for (int scale : {1, 2})
        for (bool captured : {false, true}) {
            host_gate_reset();
            host_input_reset();
            auto in = t9_layout(nullptr);
            in.cls = HOST_SCREEN_MENU;
            in.drawable_w = 640 * scale;
            in.drawable_h = 480 * scale;
            host_gate_set_layout(&in);
            HitResult hit;
            int wx = 320 * scale, wy = 240 * scale, gx = 320, gy = 240;
            CHECK(host_gate_window_motion(wx, wy, 999, -777, &hit));
            host_pointer_capture(captured); // Capture is hide-only; no centre warp.
            for (int step = 0; step < 240; ++step) {
                // Reversals, fractional scene pixels at 2x, and release/recapture.
                if (step == 80)
                    host_pointer_capture(!captured);
                if (step == 160)
                    host_pointer_capture(captured);
                const int direction = (step / 30) % 2 ? -1 : 1;
                wx += direction;
                wy -= direction;
                CHECK(host_gate_window_motion(wx, wy, 1234, -5678, &hit));
                int32_t pending_x, pending_y;
                host_input_peek(nullptr, nullptr, &pending_x, &pending_y, nullptr, nullptr);
                // Every position, including events batched between guest reads.
                CHECK_NEAR(gx + pending_x, double(wx) / scale, 1.0);
                CHECK_NEAR(gy + pending_y, double(wy) / scale, 1.0);
                if (step % 3 != 2)
                    continue;
                // Keyboard reads must preserve the mouse's consumed host deltas.
                gate_call_method(&c, keyboard, 9, {256, out});
                wr32(count, 10);
                gate_call_method(&c, mouse, 10, {16, out, count, 0});
                for (uint32_t n = 0; n < rd32(count); ++n) {
                    uint32_t ofs = rd32(out + n * 16);
                    int32_t delta = int32_t(rd32(out + n * 16 + 4));
                    if (ofs == 0)
                        gx = std::clamp(gx + delta, 0, 639);
                    if (ofs == 4)
                        gy = std::clamp(gy + delta, 0, 479);
                }
                CHECK_NEAR(gx, double(wx) / scale, 1.0);
                CHECK_NEAR(gy, double(wy) / scale, 1.0);
                wr32(count, 10);
                gate_call_method(&c, mouse, 10, {16, out, count, 0});
                CHECK_EQ(rd32(count), 0u);                 // No replay on a second buffered read.
                gate_call_method(&c, mouse, 9, {16, out}); // Independent immediate ledger.
                gate_call_method(&c, mouse, 9, {16, out});
                CHECK_EQ(rd32(out), 0u);
                CHECK_EQ(rd32(out + 4), 0u);
            }
        }
    host_gate_reset();
    host_input_reset();
    dinput_reset();
}

static void test_t9_resize_without_ack_round1() {
    host_gate_reset();
    host_present_test_begin(false);
    UiFrame ui{{t9_element(9, 600, 440, 40, 40, 2)}, 640, 480};
    auto in = t9_layout(&ui);
    host_gate_set_layout(&in);
    host_present_test_seal(1);
    host_present_tick_for_test(0);
    // Leave flight outstanding, never acknowledge it or tick the presenter.
    // Only the latest resize is consumed by the next input event.
    host_present_resize(3000, 1800);
    host_present_resize(2560, 1440);
    host_present_resize(1920, 1080);
    auto hit = host_gate_hit_test(nullptr, 1880, 1040);
    CHECK_EQ(hit.element, 9u);
    CHECK_EQ(hit.gx, 620);
    CHECK_EQ(hit.gy, 460);
    CHECK_EQ(host_present_unique_completed(), 0u);
    auto snapshot = compositor_layout_snapshot(&in);
    host_gate_publish_layout(snapshot); // A late old-size acknowledgement.
    CHECK_EQ(host_gate_hit_test(nullptr, 1880, 1040).gx, 620);
    host_gate_end_drag();
    host_pointer_capture(true);
    host_pointer_center();
    int32_t dx, dy;
    host_present_resize(3840, 2160);
    host_gate_pointer_event(0, 0, 0, 0, &dx, &dy);
    CHECK_EQ(dx, 0);
    CHECK_EQ(dy, 0); // Layout change alone is never motion.
    host_gate_pointer_event(0, 0, in.scene.scale_x * 10, 9, &dx, &dy);
    CHECK_EQ(dx, 10);
    CHECK_EQ(dy, 2);
    host_pointer_capture(false);
    host_gate_pointer_event(1920, 1080, 0, 0, &dx, &dy);
    host_present_resize(1920, 1080);
    // A backing/display change changes absolute coordinates under a stationary
    // OS pointer. It must not invent a centre-to-corner relative displacement.
    host_gate_pointer_event(960, 540, 0, 0, &dx, &dy);
    CHECK_EQ(dx, 0);
    CHECK_EQ(dy, 0);
    host_gate_pointer_event(969, 549, 9, 9, &dx, &dy);
    CHECK_EQ(dx, 3);
    CHECK_EQ(dy, 4);
    host_present_test_command_done(1);
    host_present_test_presented(1, 1);
    host_present_stop();
    host_gate_reset();
}

extern "C" int mods_display_textures() {
    return test_hd;
}
extern "C" int mods_display_classic() {
    return test_classic;
}
extern "C" int mods_display_scale() {
    return test_scale;
}
extern "C" int mods_display_scene_width(int w, int) {
    return test_classic ? w : std::max(w, test_scene_width);
}
static std::string display_offered_modes;
static bool offered_mode(const char *mode) {
    return ("," + display_offered_modes + ",").find(std::string(",") + mode + ",") !=
           std::string::npos;
}
extern "C" int ddraw_add_mode(int w, int h, int bpp) {
    if (!display_offered_modes.empty())
        display_offered_modes += ",";
    display_offered_modes +=
        std::to_string(w) + "x" + std::to_string(h) + "x" + std::to_string(bpp);
    return 1;
}
// Task 18: where the game image goes. Today the presenter composes every
// frame into the whole drawable (present_thread.cpp took `int w = drawable_w,
// h = drawable_h` for the composite and `f->input.drawable_w = drawable_w`),
// and the compositor places the guest image inside that. Landscape must keep
// exactly that, whatever the game size or the safe area.
static HostGameRect old_presenter_rect(int dw, int dh) {
    int w = dw, h = dh; // verbatim: the composite and the compositor's drawable
    return HostGameRect{0, 0, w, h};
}
static void test_game_rect_landscape_is_todays_placement() {
    const int drawables[][2] = {{1920, 1080}, {2560, 1440}, {1334, 750},  // 16:9
                                {1024, 768},  {2048, 1536}, {800, 600},   // 4:3
                                {2560, 1080}, {3440, 1440}, {2532, 1170}, // 21:9 and wider
                                {1000, 1000}};                            // square is landscape
    const int games[][2] = {{640, 480}, {800, 600}, {3840, 2160}};
    for (const auto &d : drawables)
        for (const auto &g : games)
            for (int safe_top : {0, 47, 141}) {
                const HostGameRect now = host_present_game_rect(d[0], d[1], g[0], g[1], safe_top);
                const HostGameRect old = old_presenter_rect(d[0], d[1]);
                CHECK_EQ(now.x, old.x);
                CHECK_EQ(now.y, old.y);
                CHECK_EQ(now.w, old.w);
                CHECK_EQ(now.h, old.h);
            }
}
static void test_game_rect_portrait() {
    // Full width, aspect kept, at the top of the safe area.
    HostGameRect r = host_present_game_rect(1170, 2532, 640, 480, 141);
    CHECK_EQ(r.x, 0);
    CHECK_EQ(r.y, 141);
    CHECK_EQ(r.w, 1170);
    CHECK_EQ(r.h, 878); // lround(877.5)
    r = host_present_game_rect(1170, 2532, 3840, 2160, 0);
    CHECK_EQ(r.y, 0);
    CHECK_EQ(r.w, 1170);
    CHECK_EQ(r.h, 658);
    // Too tall to fit below the safe top: the landscape rule.
    r = host_present_game_rect(1000, 1100, 480, 640, 0);
    CHECK_EQ(r.x, 0);
    CHECK_EQ(r.y, 0);
    CHECK_EQ(r.w, 1000);
    CHECK_EQ(r.h, 1100);
    r = host_present_game_rect(1000, 1100, 640, 480, 400);
    CHECK_EQ(r.y, 0);
    CHECK_EQ(r.h, 1100);
    // No game mode yet: the whole drawable.
    r = host_present_game_rect(1170, 2532, 0, 0, 141);
    CHECK_EQ(r.y, 0);
    CHECK_EQ(r.h, 2532);
    // A drawable point becomes a point in the game image.
    int32_t x = 0, y = 0;
    host_present_point_to_game(HostGameRect{0, 141, 1170, 878}, 585, 141 + 439, &x, &y);
    CHECK_EQ(x, 585);
    CHECK_EQ(y, 439);
    host_present_point_to_game(HostGameRect{0, 0, 1920, 1080}, 7, 9, &x, &y);
    CHECK_EQ(x, 7);
    CHECK_EQ(y, 9);
}
// The presenter composes into the game rectangle and publishes a layout of its
// size, so the gate maps a finger on the image to the guest pixel under it.
static void test_portrait_presenter_maps_through_the_game_rect() {
    host_gate_reset();
    host_present_set_safe_top(141);
    host_present_test_begin(false);
    host_present_resize(1170, 2532);
    host_present_tick_for_test(0);
    auto target = host_present_acquire_target(640, 480, 0, 0);
    HostGameRect r = host_present_current_game_rect();
    CHECK_EQ(r.x, 0);
    CHECK_EQ(r.y, 141);
    CHECK_EQ(r.w, 1170);
    CHECK_EQ(r.h, 878);
    CHECK_EQ(target.w, 1170);
    CHECK_EQ(target.h, 878);
    CHECK_NEAR(host_display_aspect(), 1170.0 / 878.0, 0.00001);
    UiFrame ui{};
    ui.guest_w = 640;
    ui.guest_h = 480;
    CompositorInput in{};
    in.cls = HOST_SCREEN_GAMEPLAY;
    in.ui = &ui;
    in.guest_w = 640;
    in.guest_h = 480;
    in.world = target.world;
    host_present_set_input(&in);
    host_present_test_seal(1);
    host_present_tick_for_test(0.01);
    host_present_test_command_done(1);
    host_present_test_presented(1, 0.02);
    LayoutSnapshot layout;
    CHECK(host_present_copy_layout(&layout));
    CHECK_EQ(layout.drawable_w, 1170);
    CHECK_EQ(layout.drawable_h, 878);
    // A finger in the middle of the image, in drawable pixels, is the middle
    // of the 640x480 frame. (585, 141 + 438) sits at guest row 239.45, which
    // the gate floors; one pixel lower is row 240.
    int32_t x = 0, y = 0;
    host_present_point_to_game(r, 585, 141 + 439, &x, &y);
    HitResult hit = host_gate_hit_test(nullptr, x, y);
    CHECK_EQ(hit.kind, HitResult::HIT_SCENE);
    CHECK_EQ(hit.gx, 320);
    CHECK_EQ(hit.gy, 240);
    // Rotating back to landscape: the whole drawable, as before.
    host_present_resize(2532, 1170);
    r = host_present_current_game_rect();
    CHECK_EQ(r.y, 0);
    CHECK_EQ(r.w, 2532);
    CHECK_EQ(r.h, 1170);
    CHECK_NEAR(host_display_aspect(), 2532.0 / 1170.0, 0.00001);
    host_present_stop();
    host_present_set_safe_top(0);
    host_gate_reset();
}

// A phone held upright still tells the game about a landscape screen.
static void test_landscape_screen_size() {
    int w = 0, h = 0;
    host_landscape_screen_size(390, 844, &w, &h);
    CHECK_EQ(w, 844);
    CHECK_EQ(h, 390);
    host_landscape_screen_size(1920, 1080, &w, &h);
    CHECK_EQ(w, 1920);
    CHECK_EQ(h, 1080);
    host_landscape_screen_size(1000, 1000, &w, &h);
    CHECK_EQ(w, 1000);
    CHECK_EQ(h, 1000);
}

static void test_display_settings_bridge() {
    display_offered_modes.clear();
    CHECK_EQ(host_display_offer_mode(1920, 1080, 16), 1);
    // Bootstrap modes belong to the host. This bridge must offer the requested
    // mode without assuming a particular game's startup resolution or depth.
    CHECK(offered_mode("1920x1080x16"));
    CHECK_NEAR(host_display_aspect(), 4.0 / 3.0, 0.00001);
    host_present_test_begin(false);
    host_present_resize(3840, 2160);
    host_present_tick_for_test(0);
    CHECK_NEAR(host_display_aspect(), 16.0 / 9.0, 0.00001);
    UiFrame ui{};
    ui.guest_w = 640;
    ui.guest_h = 480;
    UiElement e{};
    e.id = 42;
    e.x = 600;
    e.y = 440;
    e.w = e.h = 40;
    ui.elements.push_back(e);
    auto target = host_present_acquire_target(640, 480, 640, 480);
    CompositorInput in{};
    in.cls = HOST_SCREEN_GAMEPLAY;
    in.ui = &ui;
    in.guest_w = 640;
    in.guest_h = 480;
    in.world = target.world;
    in.drawable_w = 3840;
    in.drawable_h = 2160;
    CHECK_EQ(host_display_anchor(42, -1, -1, 0), 0);
    int x, y, w, h;
    CHECK(compositor_element_rect_on_drawable(&in, 42, &x, &y, &w, &h));
    CHECK_EQ(x, 2400);
    CHECK_EQ(host_display_anchor(42, 0, 0, 1), 0);
    CHECK(compositor_element_rect_on_drawable(&in, 42, &x, &y, &w, &h));
    CHECK_EQ(x, 3680);
    host_present_set_input(&in);
    host_present_test_seal(1);
    host_present_tick_for_test(0.01);
    host_present_test_command_done(1);
    host_present_test_presented(1, 0.02);
    uint64_t ids[2] = {0, 99};
    CHECK_EQ(host_display_elements(ids, 1), 1u);
    CHECK_EQ(ids[0], 42u);
    CHECK_EQ(ids[1], 99u);
    CHECK_EQ(host_display_elements(nullptr, 0), 1u);
    // A frame remains in flight while mode messages are posted and coalesced.
    // No acknowledgment is needed for a request to return or be consumed.
    host_present_acquire_target(640, 480, 640, 480);
    host_present_test_seal(2);
    host_present_tick_for_test(0.03);
    for (int mode = 0; mode < 3; ++mode) {
        host_display_request_window(mode);
        CHECK_EQ(host_display_take_window(), mode);
    }
    CHECK_EQ(host_display_take_window(), -1);
    host_display_request_window(1);
    host_display_request_window(2);
    CHECK_EQ(host_display_take_window(), 2);
    host_present_resize(1920, 1080);
    CHECK_NEAR(host_display_aspect(), 16.0 / 9.0, 0.00001);
    CHECK(!host_present_test_released(2));
    host_present_test_command_done(2);
    host_present_test_presented(2, 0.04);
    host_present_stop();
    test_classic = 1;
    test_scale = 3;
    host_present_test_begin();
    auto classic_target = host_present_acquire_target(640, 480, 3840, 2160);
    CHECK_EQ(classic_target.w, 640);
    CHECK_EQ(classic_target.h, 480);
    host_present_test_seal(3);
    host_present_tick_for_test(0.01);
    host_present_stop();
    test_classic = test_scale = 0;
    std::vector<uint8_t> page;
    host_page_set_enabled(true);
    g_page_draws = true;
    CHECK(host_page_rgba(&page));
    CHECK_EQ(page.size(), 640u * 480u * 4u);
    CHECK_EQ(page[3], 255);
    CHECK_EQ(page[7], 0);
    g_page_draws = false;
    CHECK(!host_page_rgba(&page));
    CHECK(page.empty());
    // A window present - a D3D11 renderer's, a film's - carries the open page
    // like a DirectDraw frame does; it used to seal without it, so F10 opened
    // a page nobody could see.
    host_present_test_begin();
    g_page_draws = true;
    const uint8_t window_rgba[4] = {1, 2, 3, 255};
    host_present_stage_rgba(window_rgba, 1, 1);
    host_present_seal_window();
    CHECK_EQ(host_present_settings_pages(), 1u);
    g_page_draws = false;
    host_present_stage_rgba(window_rgba, 1, 1);
    host_present_seal_window();
    CHECK_EQ(host_present_settings_pages(), 1u); // a hidden page rides on nothing
    host_present_stop();
    host_page_set_enabled(false);
    in.classic = true;
    LayoutSnapshot layout = compositor_layout_snapshot(&in);
    CHECK_EQ(layout.elements[0].drawable.x, 3180);
    CHECK_EQ(layout.elements[0].drawable.y, 1980);
    CHECK_NEAR(layout.scene.offset_x, 480, 0.01);
    CHECK_NEAR(layout.scene.scale_x, 4.5, 0.01);
    // Task 14: both framing directions, all screen classes, and native guest
    // dimensions. A Classic menu must not inherit Enhanced's integer UI fit.
    for (auto cls : {HOST_SCREEN_MENU, HOST_SCREEN_FMV, HOST_SCREEN_GAMEPLAY}) {
        in.cls = cls;
        in.guest_w = 1024;
        in.guest_h = 768;
        in.drawable_w = 1920;
        in.drawable_h = 1080;
        layout = compositor_layout_snapshot(&in);
        CHECK_NEAR(layout.scene.scale_x, 1.40625, 0.00001);
        CHECK_NEAR(layout.scene.scale_y, 1.40625, 0.00001);
        CHECK_NEAR(layout.scene.offset_x, 240, 0.00001);
        CHECK_NEAR(layout.scene.offset_y, 0, 0.00001);
        in.guest_w = 1920;
        in.guest_h = 1080;
        in.drawable_w = 1024;
        in.drawable_h = 768;
        layout = compositor_layout_snapshot(&in);
        CHECK_NEAR(layout.scene.scale_x, 1024.0 / 1920, 0.00001);
        CHECK_NEAR(layout.scene.scale_y, 1024.0 / 1920, 0.00001);
        CHECK_NEAR(layout.scene.offset_x, 0, 0.00001);
        CHECK_NEAR(layout.scene.offset_y, 96, 0.00001);
    }
    test_classic = 1;
    host_present_test_begin();
    classic_target = host_present_acquire_target(1920, 1080, 3840, 2160);
    CHECK_EQ(classic_target.w, 1920);
    CHECK_EQ(classic_target.h, 1080);
    host_present_test_seal(4);
    host_present_tick_for_test(0.01);
    host_present_stop();
    test_classic = 0;
    display_offered_modes.clear();
    CHECK_EQ(host_display_offer_mode(2560, 1920, 8), 1);
    CHECK(offered_mode("2560x1920x8"));
    const std::string supported = display_offered_modes;
    CHECK_EQ(host_display_offer_mode(1920, 1080, 32), 0);
    CHECK(display_offered_modes == supported);
}

// 3.0.2: the D3D9 limit on the presenter's refresh slots. A producer that sleeps in whole milliseconds (early or late
// by up to 2 ms, early by up to 0.5 ms, as the scheduler wakes it) never gets two frames into one slot, keeps the display's rate even when
// the display is 59.94 Hz or 120 Hz paced at 60, and a slow frame starts a new phase instead of bursting. Control: the
// separate-clock FrameDeadline at 60 fps against a 59.94 Hz presenter puts two frames into some slots.
static void test_slot_pacer() {
    uint32_t rng = 12345;
    auto rnd = [&](int lo, int hi) { rng = rng * 1103515245u + 12345u; return lo + int((rng >> 8) % uint32_t(hi - lo + 1)); };
    for (double period : {1.0 / 60, 1.0 / 59.94, 2.0 / 120.0}) {
        SlotPacer pacer;
        std::map<int64_t, int> per_slot;
        double now = 1000.123;
        int presented = 0;
        for (int i = 0; i < 1200; ++i) {
            for (int tries = 0; tries < 4; ++tries) {   // as Dev_Present: wait again after an early wake
                const uint32_t ms = pacer.wait_ms(now, period);
                if (!ms)
                    break;
                now += (int(ms) * 1000 + rnd(-500, 2000)) / 1e6;   // wakes up to 0.5 ms early or 2 ms late
            }
            pacer.presented(now, period);
            ++per_slot[SlotPacer::slot(now, period)];
            ++presented;
            now += rnd(2, 14) / 1000.0;   // the next frame's work, inside one slot
        }
        int doubled = 0;
        for (auto &kv : per_slot)
            doubled += kv.second > 1;
        CHECK_EQ(doubled, 0);
        const double span = double(per_slot.rbegin()->first - per_slot.begin()->first + 1);
        CHECK_NEAR(presented / span, 1.0, 0.02);
    }
    {   // slow frames (25 ms of work at 60): every other slot, no wait, no burst after a pause
        SlotPacer pacer;
        double now = 50.0;
        const double period = 1.0 / 60;
        for (int i = 0; i < 100; ++i) {
            CHECK_EQ(pacer.wait_ms(now, period), 0u);
            pacer.presented(now, period);
            now += 0.025;
        }
        now += 5.0;
        now = (std::floor(now / period) + 0.5) * period;   // mid-slot
        CHECK_EQ(pacer.wait_ms(now, period), 0u);
        pacer.presented(now, period);
        CHECK(pacer.wait_ms(now + 0.001, period) > 0);
        CHECK(pacer.wait_ms(now + 0.001, period) <= 17u);
    }
    {   // control: the separate clock at 60 against a 59.94 Hz display beats (two frames in some slots)
        FrameDeadline deadline;
        std::map<int64_t, int> per_slot;
        int64_t ns = 0;
        const double period = 1.0 / 59.94;
        for (int i = 0; i < 60 * 60; ++i) {
            ns += int64_t(deadline.wait_ms(ns)) * 1000000;
            ++per_slot[SlotPacer::slot(ns / 1e9 + 0.0001, period)];
            deadline.begin(60, ns);
            ns += 5000000;
        }
        int doubled = 0;
        for (auto &kv : per_slot)
            doubled += kv.second > 1;
        CHECK(doubled >= 2);
    }
}

static void test_native_frame_metrics() {
    // D3D9 paces at Present: wait on the preceding slot, publish, then
    // reserve the next. Count rendering work once and leave the first
    // presentation immediate. Check both targets across fractional sleeps.
    for (int rate : {60, 120}) {
        FrameDeadline present;
        int64_t now = 1000000000;
        CHECK_EQ(present.wait_ms(now), 0u);
        const int64_t start = now;
        for (int i = 0; i <= rate * 10; ++i) {
            now += int64_t(present.wait_ms(now)) * 1000000;
            if (i == rate * 10)
                CHECK_NEAR(double(now - start) / 1e9, 10, .001);
            present.begin(rate, now);
            now += 2000000;
        }
        now += 5000000000; // loading/focus pause: do not repay missed frames
        CHECK_EQ(present.wait_ms(now), 0u);
        present.begin(rate, now);
        CHECK(present.wait_ms(now) > 0);
        CHECK(present.wait_ms(now) <= uint32_t((1000 + rate - 1) / rate));
        present = FrameDeadline{}; // device reset or cap change
        CHECK_EQ(present.wait_ms(now), 0u);
        present.begin(0, now);
        CHECK_EQ(present.wait_ms(now), 0u);
    }
    // Rounded sleeps must average to the requested cadence, including 120Hz's
    // fractional 8.333ms period. Slow work must not trigger a catch-up burst.
    for (int rate : {40, 60, 120}) {
        FrameDeadline deadline;
        int64_t now = 0;
        for (int i = 0; i < rate * 10; ++i) {
            deadline.begin(rate, now);
            now += 2000000;
            now += int64_t(deadline.wait_ms(now)) * 1000000;
        }
        CHECK_NEAR(double(now) / 1e9, 10, .001);
        now += 1000000000;
        deadline.begin(rate, now);
        CHECK(deadline.wait_ms(now) > 0);
        deadline.begin(0, now);
        CHECK_EQ(deadline.wait_ms(now), 0u);
        deadline.begin(120, now);
        CHECK_EQ(deadline.wait_ms(now), 9u);
        deadline.begin(60, now);
        CHECK_EQ(deadline.wait_ms(now), 17u);
    }
    for (int rate : {40, 60, 120}) {
        FrameDeadline deadline;
        int64_t now = 1000000000;
        // An inactive guest keeps entering the outer loop but skips drawing
        // and both end-of-frame waits. Those passes must not reserve future
        // presentation slots that are then slept through after activation.
        for (int i = 0; i < 50000; ++i) {
            deadline.begin(rate, now);
            now += 100000; // five seconds away, many non-rendering passes
        }
        deadline.begin(rate, now);
        CHECK(deadline.wait_ms(now) <= uint32_t((1000 + rate - 1) / rate));
        const int64_t resumed = now;
        for (int i = 0; i < rate * 2; ++i) {
            deadline.begin(rate, now);
            now += 2000000;
            now += int64_t(deadline.wait_ms(now)) * 1000000;
        }
        CHECK_NEAR(double(now - resumed) / 1e9, 2, .001);
    }
    FramePacing p;
    // 60 new frames with alternate repeats on a 120Hz display.
    for (int i = 0; i <= 240; ++i)
        p.displayed(double(i) / 120, i % 2, 2, 4);
    auto s = p.snapshot(2, 3);
    CHECK_NEAR(s.new_fps, 60, .01);
    CHECK_NEAR(s.display_fps, 120, .01);
    CHECK_NEAR(s.median_ms, 1000.0 / 60, .01);
    CHECK_NEAR(s.p95_ms, 1000.0 / 60, .01);
    CHECK_NEAR(s.repeat_percent, 50, .01);
    CHECK_EQ(s.drops, 3u);
    CHECK_NEAR(s.gpu_ms, 2, .01);
    CHECK_NEAR(s.age_ms, 4, .01);
    CHECK_EQ(p.snapshot(5, 3).new_fps, 0); // a stalled producer cannot keep reporting 60
    CHECK(p.snapshot(5, 3).intervals_ms.empty());
    p.displayed(NAN, false, 0, 0);
    CHECK_EQ(p.snapshot(2, 3).new_frames, s.new_frames);
    p.displayed(2.2, false, 3, 8);
    p.displayed(2.5, false, 3, 8);
    CHECK(p.snapshot(2.5, 3).intervals_ms.back() > 299);
}
static void test_wide_cursor_bound() {
    constexpr uint32_t base = RECOMP_HOOK_MOUSE_DEVICE_PTR;
    constexpr uint32_t right = RECOMP_HOOK_MOUSE_DEVICE_RIGHT;
    uint32_t saved_right = rd32(right);
    uint8_t saved[0x48];
    memcpy(saved, gm_ptr(base), sizeof saved);
    host_gate_reset();
    host_input_reset();
    memset(gm_ptr(base), 0, 0x48);
    wr32(base, RECOMP_HOOK_MOUSE_VTABLE);
    wr32(right, 640);
    wr32(base + 0x44, 480);
    auto in = t9_layout(nullptr);
    in.cls = HOST_SCREEN_GAMEPLAY;
    in.scene.domain_w = 852;
    host_gate_set_layout(&in);
    test_scene_width = 852;
    HitResult hit;
    host_gate_window_motion(400, 300, 0, 0, &hit);
    int32_t dx = 0, dy = 0;
    host_input_pointer_correction(&dx, &dy);
    CHECK_EQ(rd32(right), 852u);
    test_scene_width = 1120;
    host_input_pointer_correction(&dx, &dy);
    CHECK_EQ(rd32(right), 1120u);
    host_gate_reset();
    CHECK_EQ(rd32(right), 640u);
    host_gate_set_layout(&in);
    host_gate_window_motion(400, 300, 0, 0, &hit);
    host_input_pointer_correction(&dx, &dy);
    CHECK_EQ(rd32(right), 1120u);
    // A modal owns its narrower clamp. Never overwrite it or restore over it.
    wr32(right, 300);
    host_input_pointer_correction(&dx, &dy);
    CHECK_EQ(rd32(right), 300u);
    host_gate_reset();
    CHECK_EQ(rd32(right), 300u);

    // A HUD hit in the widened composition names guest x=620, even though
    // scaling the physical pointer across the entire canvas would give x=835.
    // Keep main's damping/foreign-write handling while converging on the hit.
    host_input_reset();
    {
    TestPointerCell cell;
    cell.arm(639, 479); // device geometry used by pointer integration
    wr32(right, 639);
    cell.put(100, 100);
    UiFrame ui{{t9_element(9, 600, 440, 40, 40, 2)}, 640, 480};
    in = t9_layout(&ui);
    test_scene_width = 852;
    host_gate_set_layout(&in);
    CHECK(host_gate_window_motion(3760, 2080, 0, 0, &hit));
    CHECK_EQ(hit.kind, HitResult::HIT_ELEMENT);
    CHECK_EQ(hit.gx, 620);
    CHECK_EQ(hit.gy, 460);
    for (int i = 0; i < 40; ++i) {
        dx = dy = 0;
        host_input_pointer_correction(&dx, &dy);
        cell.move(dx, dy);
    }
    CHECK(near(cell.x(), 620u));
    CHECK(near(cell.y(), 460u));
    }
    host_gate_reset();
    host_input_reset();
    test_scene_width = 0;
    memcpy(gm_ptr(base), saved, sizeof saved);
    wr32(right, saved_right);
}
static void test_pointer_reaches_scrolling_edges() {
#ifdef RECOMP_HOOK_MOUSE_PAIR_SPACE_W
    // A pair in a fixed space ([hooks] mouse_pair_space_w/_h) does not follow the frame size these cases sweep;
    // test_pair_pointer_maps_into_the_ui_box covers it.
    return;
#endif
    TestPointerCell cell;
    const int sizes[][2] = {{640, 480},   {800, 600},   {1024, 768}, {1280, 720},
                            {1920, 1080}, {2560, 1440}, {3840, 2160}};
    for (const auto &size : sizes)
        for (bool classic : {false, true})
            for (int scale : {1, 4})
                for (int inclusive : {0, 1}) {
                    const int width = size[0], height = size[1];
                    for (int edge = 0; edge < 4; ++edge) {
                        host_gate_reset();
                        host_input_reset();
                        host_pointer_set_mode(width, height);
                        cell.arm(width - 1 + inclusive, height - 1 + inclusive);
                        cell.put(width / 2, height / 2);
                        // The bounds the game's pointer form reports: the object's own rectangle, or the
                        // display mode for a pair (whose right edge is width - 1 either way).
                        const HostGuestPointer bounds = host_guest_pointer_current();
                        CHECK(bounds.failure == HostGuestPointer::None);
                        const int right = bounds.right, bottom = bounds.bottom;
                        const int tx = edge == 0   ? 0
                                       : edge == 1 ? (classic ? right : width - 1)
                                                   : width / 2;
                        const int ty = edge == 2   ? 0
                                       : edge == 3 ? (classic ? bottom : height - 1)
                                                   : height / 2;
                        // Inside the former four-pixel deadband: this is near the edge,
                        // but 004adbb0 will not issue its edge-scrolling command here.
                        cell.put(edge == 0 ? 3 : edge == 1 ? width - 4 : tx,
                                 edge == 2 ? 3 : edge == 3 ? height - 4 : ty);
                        auto in = t9_layout(nullptr);
                        in.cls = HOST_SCREEN_GAMEPLAY;
                        in.classic = classic;
                        in.legacy = false;
                        in.guest_w = width;
                        in.guest_h = height;
                        in.drawable_w = width * scale;
                        in.drawable_h = height * scale;
                        in.scene = {float(scale), float(scale), 0, 0, width};
                        host_gate_set_layout(&in);
                        host_pointer_capture(true);
                        test_scene_width = width;
                        const int px = edge == 0   ? 0
                                       : edge == 1 ? in.drawable_w - 1
                                                   : in.drawable_w / 2;
                        const int py = edge == 2   ? 0
                                       : edge == 3 ? in.drawable_h - 1
                                                   : in.drawable_h / 2;
                        HitResult hit;
                        CHECK(host_gate_window_motion(px, py, 0, 0, &hit));
                        HostInputState queued{};
                        host_input_state(&queued);
                        cell.move(queued.mouse_dx, queued.mouse_dy);
                        for (int poll = 0; poll < 20; ++poll) {
                            int32_t dx = 0, dy = 0;
                            host_input_pointer_correction(&dx, &dy);
                            cell.move(dx, dy);
                        }
                        if (edge < 2)
                            CHECK_EQ(cell.x(), uint32_t(tx));
                        else
                            CHECK_EQ(cell.y(), uint32_t(ty));
                        // Pulling away from the edge must still work immediately.
                        CHECK(host_gate_window_motion(in.drawable_w / 2, in.drawable_h / 2, 0, 0,
                                                      &hit));
                        host_input_state(&queued);
                        cell.move(queued.mouse_dx, queued.mouse_dy);
                        for (int poll = 0; poll < 40; ++poll) {
                            // The app can wake the stationary pointer repeatedly before
                            // DirectInput consumes motion. Wakes must not mark a delta as
                            // delivered, or the foreign-writer guard cancels the real poll.
                            host_gate_pointer_tick();
                            int32_t dx = 0, dy = 0;
                            host_input_pointer_correction(&dx, &dy);
                            cell.move(dx, dy);
                        }
                        const int center_x = classic ? int(std::lround(double(in.drawable_w / 2) *
                                                                       right / (in.drawable_w - 1)))
                                                     : hit.gx;
                        const int center_y = classic
                                                 ? int(std::lround(double(in.drawable_h / 2) *
                                                                   bottom / (in.drawable_h - 1)))
                                                 : hit.gy;
                        CHECK(near(cell.x(), uint32_t(center_x)));
                        CHECK(near(cell.y(), uint32_t(center_y)));
                    }
                }
    host_gate_reset();
    host_input_reset();
    test_scene_width = 0;
}
// Match the host's revision-gated publication, then check the actual GPU
// output: dragging moves only the knob, keeping the base and touch zone put.
static void test_native_stick_motion_pixels() {
    struct Sink : controls::ControlsSink {
        void key(int, bool) override {}
        void action(const std::string &) override {}
        void switch_layout(const std::string &) override {}
        void group_visibility_changed() override {}
        void tap() override {}
    } sink;
    auto device = gpu::create_default_device();
    CHECK(device != nullptr);
    if (!device)
        return;
    constexpr int w = 640, h = 480;
    gpu::Texture target = device->create_texture(
        {w, h, gpu::Format::RGBA8, gpu::UsageRenderTarget | gpu::UsageSampled | gpu::UsageCpu, 1});
    const controls::Screen screen{w, h, 1.0, {0, 0, w, h}, {}};
    for (bool floating : {false, true}) {
        controls::Layout layout;
        controls::Control stick;
        stick.kind = controls::Kind::Stick;
        stick.anchor = controls::Anchor::TopLeft;
        stick.x = 100;
        stick.y = 100;
        stick.w = stick.h = 280;
        stick.radius = 70;
        stick.floating = floating;
        controls::Group group;
        group.controls.push_back(stick);
        layout.groups.push_back(group);
        controls::Router router;
        router.set_layout(&layout, sink);
        router.set_screen(screen);
        CHECK(router.finger_down(1, 240, 240, 0, sink));
        controls::ControlsView published;
        controls::Overlay overlay;
        auto render = [&] {
            const auto next = controls::make_view(layout, router, screen, 1.0);
            if (next.revision != published.revision)
                published = next;
            std::vector<uint8_t> pixels(w * h * 4, 40);
            device->upload(target, {0, 0, w, h}, pixels.data(), w * 4);
            auto cb = device->begin();
            overlay.draw(device.get(), cb, target, w, h, published);
            device->commit(cb);
            device->wait(cb);
            CHECK(device->status(cb) == gpu::CommandStatus::Completed);
            device->readback(target, {0, 0, w, h}, pixels.data(), w * 4);
            return pixels;
        };
        auto before = render();
        CHECK(router.finger_motion(1, 310, 240, 1, sink));
        auto after = render();
        int changed = 0, outside_knobs = 0;
        // The knob radius is 0.45 * travel. Give antialiasing two pixels;
        // everything outside the old/new knob bounds must remain identical.
        for (int y = 0; y < h; ++y)
            for (int x = 0; x < w; ++x) {
                const size_t i = (y * w + x) * 4;
                if (memcmp(&before[i], &after[i], 4) == 0)
                    continue;
                ++changed;
                if (y < 206 || y > 274 || x < 206 || x > 344)
                    ++outside_knobs;
            }
        CHECK(changed > 1000);
        CHECK_EQ(outside_knobs, 0);
        CHECK(published.controls[0].knob_x == 1.0);
        CHECK(published.controls[0].base_x == 240);
        CHECK(published.controls[0].base_y == 240);
        CHECK_EQ(published.controls[0].rect.x, 100);
        CHECK_EQ(published.controls[0].rect.y, 100);
        CHECK(router.finger_up(1, 2, sink));
        auto released = render();
        CHECK(released != after);
    }
    device->destroy(target);
}

static void test_native_overlay_pixels() {
    auto device = gpu::create_default_device();
    CHECK(device != nullptr);
    if (!device)
        return;
    gpu::Texture target =
        device->create_texture({640, 480, gpu::Format::RGBA8,
                                gpu::UsageRenderTarget | gpu::UsageSampled | gpu::UsageCpu, 1});
    std::vector<uint8_t> pixels(640 * 480 * 4, 40);
    device->upload(target, {0, 0, 640, 480}, pixels.data(), 640 * 4);
    gpu::CommandBuffer cb = device->begin();
    PerformanceOverlay overlay;
    FramePacingSnapshot snapshot;
    snapshot.new_fps = 60;
    snapshot.display_fps = 120;
    snapshot.intervals_ms = {8, 16, 33, 50};
    overlay.draw(device.get(), cb, target, 640, 480, snapshot, 1, 2, 60);
    device->commit(cb);
    device->wait(cb);
    CHECK(device->status(cb) == gpu::CommandStatus::Completed);
    device->readback(target, {0, 0, 640, 480}, pixels.data(), 640 * 4);
    CHECK_EQ(pixels[(400 * 640 + 20) * 4], 40); // HUD never clears the game below it
    int changed = 0;
    for (int y = 10; y < 146; ++y)
        for (int x = 300; x < 630; ++x)
            changed += pixels[(y * 640 + x) * 4] != 40;
    CHECK(changed > 10000);
    FILE *f = fopen("build/recomp/performance-overlay.ppm", "wb");
    if (f) {
        fprintf(f, "P6\n640 480\n255\n");
        for (size_t i = 0; i < pixels.size(); i += 4)
            fwrite(&pixels[i], 1, 3, f);
        fclose(f);
    }
    auto previous = pixels;
    for (uint32_t state = 0; state < 6; ++state) {
        std::fill(pixels.begin(), pixels.end(), 40);
        device->upload(target, {0, 0, 640, 480}, pixels.data(), 640 * 4);
        cb = device->begin();
        CaptureMarker marker{2,         12345 + state,        987654321000ull + state * 16666667,
                             state,     state == 5 ? 5u : 0u, true,
                             state == 2};
        // Capture status must remain visible even when the FPS overlay is off.
        overlay.draw(device.get(), cb, target, 640, 480, snapshot, 2 + state * .01, 0, 60, marker,
                     54321 + state);
        device->commit(cb);
        device->wait(cb);
        CHECK(device->status(cb) == gpu::CommandStatus::Completed);
        device->readback(target, {0, 0, 640, 480}, pixels.data(), 640 * 4);
        CHECK(pixels != previous);
        previous = pixels;
        CHECK_EQ(pixels[(400 * 640 + 20) * 4], 40);
        char path[128];
        snprintf(path, sizeof path, "build/recomp/capture-overlay-%u.ppm", state);
        FILE *out = fopen(path, "wb");
        CHECK(out != nullptr);
        if (out) {
            fprintf(out, "P6\n640 480\n255\n");
            for (size_t i = 0; i < pixels.size(); i += 4)
                fwrite(&pixels[i], 1, 3, out);
            fclose(out);
        }
    }
}

// ---- The Direct3D 9 GPU renderer --------------------------------------------
// Shader model 1.1, assembled by hand: vs `dcl_position v0; mov oPos, v0;
// mov oD0, c0`, ps `mov r0, v0`.
static std::vector<uint8_t> d9_words(std::initializer_list<uint32_t> w) {
    std::vector<uint8_t> b(w.size() * 4);
    size_t i = 0;
    for (uint32_t x : w) {
        memcpy(&b[i], &x, 4);
        i += 4;
    }
    return b;
}

static void d9_pixel(uint32_t id, uint32_t x, uint32_t y, uint8_t out[4]) {
    std::vector<uint8_t> px(8 * 8 * 4);
    CHECK(host_d9_texture_read(id, 0, 0, px.data(), 8 * 4) != 0);
    memcpy(out, &px[(y * 8 + x) * 4], 4);
}

// A synthetic 3D lookup with eight independent corners distinguishes genuine
// volume sampling from a 2D/array substitute. No game shaders or textures.
static void test_d3d9_volume_renderer() {
    const uint32_t rt_id = 90201, volume_id = 90202;
    HostD9TextureDesc rt{rt_id, HOST_D9_TEX_2D, 8, 8, 1, 21, HOST_D9_USAGE_RENDERTARGET};
    HostD9TextureDesc vol{volume_id, HOST_D9_TEX_VOLUME, 2, 2, 2, 21, 0, 0, 2};
    host_d9_texture_define(&rt);
    host_d9_texture_define(&vol);
    uint8_t slices[2][24]{};
    for (unsigned z = 0; z < 2; ++z) {
        for (unsigned y = 0; y < 2; ++y)
            for (unsigned x = 0; x < 2; ++x) {
                auto *p = &slices[z][y * 12 + x * 4];
                p[0] = z * 255;
                p[1] = y * 255;
                p[2] = x * 255;
                p[3] = 255;
            }
        host_d9_texture_upload(volume_id, z, 0, slices[z], 12);
        uint8_t copy[24]{};
        CHECK(host_d9_texture_read(volume_id, z, 0, copy, 12));
        for (unsigned y = 0; y < 2; ++y)
            CHECK(!memcmp(copy + y * 12, slices[z] + y * 12, 8));
    }
    const uint8_t mip[] = {255, 0, 255, 255};
    host_d9_texture_upload(volume_id, 0, 1, mip, 4);
    auto vs = d9_words({0xfffe0101u, 31u, 0x80000000u, 0x900f0000u, 1u, 0xc00f0000u, 0x90e40000u,
                        1u, 0xe00f0000u, 0xa0e40000u, 0xffffu});
    auto ps = d9_words({0xffff0200u, 0x0200001fu, 0x80000000u, 0xb00f0000u, 0x0200001fu,
                        0xa0000000u, 0xa00f0800u, 0x03000042u, 0x800f0000u, 0xb0e40000u,
                        0xa0e40800u, 0x02000001u, 0x800f0800u, 0x80e40000u, 0xffffu});
    CHECK(d9sh::program_for(ps).ok);
    CHECK_EQ(d9sh::program_for(ps).samplers.at(0), d9sh::S_VOLUME);
    const uint8_t decl[] = {0, 0, 0, 0, 2, 0, 0, 0, 0xff, 0, 0, 0, 17, 0, 0, 0};
    const float triangle[] = {-1, 1, 0.5f, 3, 1, 0.5f, -1, -3, 0.5f};
    D9Pipeline pl;
    pl.rs[7] = pl.rs[27] = 0;
    pl.sampler_state[0][1] = pl.sampler_state[0][2] = pl.sampler_state[0][3] = 3; // clamp
    pl.sampler_state[0][5] = pl.sampler_state[0][6] = 1;                          // point
    pl.sampler_state[0][7] = 0; // no mip interpolation
    float coords[] = {0.25f, 0.25f, 0.25f, 1};
    HostD9Draw d{};
    d.target.color[0] = {rt_id, 0, 0};
    d.viewport[2] = d.viewport[3] = 8;
    d.depth_range[1] = 1;
    d.vs = vs.data();
    d.vs_size = uint32_t(vs.size());
    d.ps = ps.data();
    d.ps_size = uint32_t(ps.size());
    d.vconst = coords;
    d.vconst_count = 1;
    d.decl = decl;
    d.decl_size = sizeof decl;
    d.decl_id = 1;
    d.stream[0].stride = 12;
    d.inline_vertices = reinterpret_cast<const uint8_t *>(triangle);
    d.inline_bytes = sizeof triangle;
    d.primitive = 4;
    d.primitive_count = 1;
    d.sampler_texture[0] = volume_id;
    d.sampler_state = &pl.sampler_state[0][0];
    d.render_state = pl.rs;
    d.render_state_set = reinterpret_cast<const uint8_t *>(pl.rs_set);
    uint8_t pixel[4]{};
    auto sample = [&] {
        host_d9_draw(&d);
        d9_pixel(rt_id, 3, 3, pixel);
    };
    for (unsigned z = 0; z < 2; ++z)
        for (unsigned y = 0; y < 2; ++y)
            for (unsigned x = 0; x < 2; ++x) {
                coords[0] = .25f + .5f * x;
                coords[1] = .25f + .5f * y;
                coords[2] = .25f + .5f * z;
                sample();
                CHECK_EQ(pixel[0], z * 255);
                CHECK_EQ(pixel[1], y * 255);
                CHECK_EQ(pixel[2], x * 255);
            }
    coords[0] = coords[1] = coords[2] = .5f;
    pl.sampler_state[0][5] = pl.sampler_state[0][6] = 2;
    sample();
    for (unsigned k = 0; k < 3; ++k)
        CHECK_NEAR(pixel[k], 128, 1);
    // AddressW must participate independently of U/V.
    pl.sampler_state[0][5] = pl.sampler_state[0][6] = 1;
    coords[0] = coords[1] = .25f;
    coords[2] = 1.25f;
    sample();
    CHECK_EQ(pixel[0], 255);
    pl.sampler_state[0][3] = 1; // wrap W
    sample();
    CHECK_EQ(pixel[0], 0);
    // Shader model3 uses declared v0 for interpolated coordinates and texldl.
    auto ps_lod = d9_words({0xffff0300u, 0x0200001fu, 0x80000005u, 0x900f0000u, 0x0200001fu,
                            0xa0000000u, 0xa00f0800u, 0x0300005fu, 0x800f0000u, 0x90e40000u,
                            0xa0e40800u, 0x02000001u, 0x800f0800u, 0x80e40000u, 0xffffu});
    CHECK(d9sh::program_for(ps_lod).ok);
    d.ps = ps_lod.data();
    d.ps_size = uint32_t(ps_lod.size());
    coords[0] = coords[1] = coords[2] = .5f;
    coords[3] = 1;
    pl.sampler_state[0][7] = 1;
    sample();
    for (unsigned k = 0; k < 4; ++k)
        CHECK_EQ(pixel[k], mip[k]);
    // The full chain is based on all three dimensions, including a long Z axis.
    vol.width = vol.height = 1;
    vol.depth = 4;
    vol.levels = 3;
    host_d9_texture_define(&vol);
    const uint8_t green[] = {0, 255, 0, 255};
    host_d9_texture_upload(volume_id, 0, 2, green, 4);
    coords[3] = 2;
    sample();
    for (unsigned k = 0; k < 4; ++k)
        CHECK_EQ(pixel[k], green[k]);
    uint8_t unchanged[16];
    memset(unchanged, 0x5a, sizeof unchanged);
    CHECK(!host_d9_texture_read(volume_id, 1, 2, unchanged, 4));
    CHECK(!host_d9_texture_read(volume_id, 0, 3, unchanged, 4));
    CHECK(!host_d9_texture_read(volume_id, 0, 2, unchanged, 3));
    for (uint8_t v : unchanged)
        CHECK_EQ(v, 0x5a);
    host_d9_texture_upload(volume_id, 1, 2, unchanged, 4); // invalid slice cannot overwrite mip2
    sample();
    CHECK_EQ(pixel[1], 255);
    // Correctly typed fallback after drop, wrong-kind redefinition and invalid extent.
    host_d9_texture_drop(volume_id);
    sample();
    CHECK_EQ(pixel[0] | pixel[1] | pixel[2], 0);
    HostD9TextureDesc flat{volume_id, HOST_D9_TEX_2D, 1, 1, 1, 21, 0};
    host_d9_texture_define(&flat);
    host_d9_texture_upload(volume_id, 0, 0, green, 4);
    sample();
    CHECK_EQ(pixel[0] | pixel[1] | pixel[2], 0);
    flat.format = 75;
    flat.usage = HOST_D9_USAGE_DEPTH;
    host_d9_texture_define(&flat);
    sample(); // a wrong 2D depth binding must still bind a typed 3D placeholder
    CHECK_EQ(pixel[0] | pixel[1] | pixel[2], 0);
    vol.depth = 0;
    host_d9_texture_define(&vol);
    sample();
    CHECK_EQ(pixel[0] | pixel[1] | pixel[2], 0);
    // Converted formats use each Z slice's source pitch, not BGRA pitch.
    vol.width = vol.height = vol.depth = vol.levels = 1;
    coords[0] = coords[1] = coords[2] = .5f;
    coords[3] = 0;
    for (uint32_t format : {22u, 28u, 50u}) {
        vol.format = format;
        host_d9_texture_define(&vol);
        const uint8_t source[] = {64, 91, 123, 0};
        host_d9_texture_upload(volume_id, 0, 0, source, format == 22 ? 4 : 1);
        sample();
        if (format == 22) {
            CHECK_EQ(pixel[0], 64);
            CHECK_EQ(pixel[1], 91);
            CHECK_EQ(pixel[2], 123);
            CHECK_EQ(pixel[3], 255);
        } else if (format == 28) {
            CHECK_EQ(pixel[0] | pixel[1] | pixel[2], 0);
            CHECK_EQ(pixel[3], 64);
        } else {
            for (unsigned k = 0; k < 3; ++k)
                CHECK_EQ(pixel[k], 64);
            CHECK_EQ(pixel[3], 255);
        }
    }
    // An upload after an encoded draw cannot change that earlier draw's input.
    vol.format = 21;
    host_d9_texture_define(&vol);
    const uint8_t red[] = {0, 0, 255, 255};
    host_d9_texture_upload(volume_id, 0, 0, red, 4);
    host_d9_draw(&d);
    host_d9_texture_upload(volume_id, 0, 0, green, 4);
    d9_pixel(rt_id, 3, 3, pixel);
    CHECK(!memcmp(pixel, red, 4));
    sample();
    CHECK(!memcmp(pixel, green, 4));
    // The same ordering applies to replacement and deletion of a used resource.
    host_d9_draw(&d);
    host_d9_texture_define(&vol);
    host_d9_texture_upload(volume_id, 0, 0, red, 4);
    d9_pixel(rt_id, 3, 3, pixel);
    CHECK(!memcmp(pixel, green, 4));
    sample();
    CHECK(!memcmp(pixel, red, 4));
    host_d9_draw(&d);
    host_d9_texture_drop(volume_id);
    d9_pixel(rt_id, 3, 3, pixel);
    CHECK(!memcmp(pixel, red, 4));
    sample();
    CHECK_EQ(pixel[0] | pixel[1] | pixel[2], 0);
    // Reject unsupported allocation requests before calling Metal.
    for (unsigned bad = 0; bad < 5; ++bad) {
        auto invalid = vol;
        if (bad == 0)
            invalid.width = invalid.height = invalid.depth = 512;
        if (bad == 1)
            invalid.levels = 2;
        if (bad == 2)
            invalid.usage = HOST_D9_USAGE_RENDERTARGET;
        if (bad == 3)
            invalid.format = 0x31545844u;
        if (bad == 4)
            invalid.samples = 4;
        host_d9_texture_define(&invalid);
        sample();
        CHECK_EQ(pixel[0] | pixel[1] | pixel[2], 0);
    }
    host_d9_texture_drop(volume_id);
    host_d9_texture_drop(rt_id);
}

// Optional private-fixture proof: upload every byte, then rasterize each Z/mip
// through the volume sampler. No private file names or bytes enter the source.
static void test_d3d9_dds_volume(const char *path) {
    std::ifstream file(path, std::ios::binary | std::ios::ate);
    const auto size = file.tellg();
    CHECK(file.good() && size >= 128 && size <= 128 * 1024 * 1024 + 128);
    if (!file.good() || size < 128 || size > 128 * 1024 * 1024 + 128)
        return;
    std::vector<uint8_t> bytes(static_cast<size_t>(size));
    file.seekg(0);
    CHECK(bool(file.read(reinterpret_cast<char *>(bytes.data()), size)));
    if (!file)
        return;
    ddsvolume::View v;
    CHECK(ddsvolume::parse(bytes.data(), bytes.size(), v));
    if (!v.levels)
        return;
    const uint32_t rt_id = 90301, volume_id = 90302;
    HostD9TextureDesc volume{
        volume_id, HOST_D9_TEX_VOLUME, v.width, v.height, v.levels, v.format, 0, 0, v.depth};
    host_d9_texture_define(&volume);
    for (uint32_t level = 0; level < v.levels; ++level) {
        const auto &m = v.mip[level];
        std::vector<uint8_t> readback(m.slice_pitch);
        for (uint32_t z = 0; z < m.depth; ++z) {
            const auto *source = bytes.data() + m.offset + z * m.slice_pitch;
            host_d9_texture_upload(volume_id, z, level, source, m.row_pitch);
            CHECK(host_d9_texture_read(volume_id, z, level, readback.data(), m.row_pitch));
            // XRGB's unused source byte is normalized to opaque by the backend.
            bool same = true;
            for (size_t i = 0; i < readback.size(); ++i)
                same &= readback[i] == (v.format == 22 && i % 4 == 3 ? 255 : source[i]);
            CHECK(same);
        }
    }
    auto vs = d9_words({0xfffe0101u, 31u, 0x80000000u, 0x900f0000u, 31u, 0x80000005u, 0x900f0001u,
                        1u, 0xc00f0000u, 0x90e40000u, 1u, 0xe00f0000u, 0x90e40001u, 0xffffu});
    auto ps = d9_words({0xffff0300u, 0x0200001fu, 0x80000005u, 0x900f0000u, 0x0200001fu,
                        0xa0000000u, 0xa00f0800u, 0x0300005fu, 0x800f0000u, 0x90e40000u,
                        0xa0e40800u, 0x02000001u, 0x800f0800u, 0x80e40000u, 0xffffu});
    CHECK(d9sh::program_for(vs).ok && d9sh::program_for(ps).ok);
    const uint8_t decl[] = {0, 0, 0, 0, 2,    0, 0, 0, 0,  0, 12, 0,
                            3, 0, 5, 0, 0xff, 0, 0, 0, 17, 0, 0,  0};
    float triangle[] = {-1, 1, .5f, 0, 0, 0, 0, 3, 1, .5f, 2, 0, 0, 0, -1, -3, .5f, 0, 2, 0, 0};
    D9Pipeline pl;
    pl.rs[7] = pl.rs[27] = 0;
    pl.sampler_state[0][1] = pl.sampler_state[0][2] = pl.sampler_state[0][3] = 3;
    pl.sampler_state[0][5] = pl.sampler_state[0][6] = pl.sampler_state[0][7] = 1;
    HostD9Draw d{};
    d.target.color[0] = {rt_id, 0, 0};
    d.depth_range[1] = 1;
    d.vs = vs.data();
    d.vs_size = uint32_t(vs.size());
    d.ps = ps.data();
    d.ps_size = uint32_t(ps.size());
    d.decl = decl;
    d.decl_size = sizeof decl;
    d.decl_id = 2;
    d.stream[0].stride = 28;
    d.inline_vertices = reinterpret_cast<const uint8_t *>(triangle);
    d.inline_bytes = sizeof triangle;
    d.primitive = 4;
    d.primitive_count = 1;
    d.sampler_texture[0] = volume_id;
    d.sampler_state = &pl.sampler_state[0][0];
    d.render_state = pl.rs;
    d.render_state_set = reinterpret_cast<const uint8_t *>(pl.rs_set);
    uint64_t samples = 0, mismatches = 0;
    for (uint32_t level = 0; level < v.levels; ++level) {
        const auto &m = v.mip[level];
        HostD9TextureDesc rt{
            rt_id, HOST_D9_TEX_2D, m.width, m.height, 1, 21, HOST_D9_USAGE_RENDERTARGET};
        host_d9_texture_define(&rt);
        d.viewport[2] = m.width;
        d.viewport[3] = m.height;
        std::vector<uint8_t> pixels(m.slice_pitch);
        for (uint32_t z = 0; z < m.depth; ++z) {
            for (unsigned vertex = 0; vertex < 3; ++vertex) {
                triangle[vertex * 7 + 5] = (float(z) + .5f) / m.depth;
                triangle[vertex * 7 + 6] = float(level);
            }
            host_d9_clear(&d.target, d.viewport, 0, nullptr, 1, 0xffff00ff, 1, 0);
            host_d9_draw(&d);
            CHECK(host_d9_texture_read(rt_id, 0, 0, pixels.data(), m.row_pitch));
            const auto *source = bytes.data() + m.offset + z * m.slice_pitch;
            uint64_t bad = 0;
            for (size_t i = 0; i < pixels.size(); ++i)
                bad += pixels[i] != (v.format == 22 && i % 4 == 3 ? 255 : source[i]);
            mismatches += bad;
            samples += pixels.size() / 4;
            CHECK(bad == 0);
        }
    }
    host_d9_texture_drop(volume_id);
    host_d9_texture_drop(rt_id);
    printf("volume DDS: %ux%ux%u, %u mips, %llu voxels rasterized, %llu byte mismatches\n", v.width,
           v.height, v.depth, v.levels, (unsigned long long)samples,
           (unsigned long long)mismatches);
}

static void test_d3d9_gpu_renderer() {
    const uint32_t RT = 90001, DEPTH = 90002;
    HostD9TextureDesc rt{RT, HOST_D9_TEX_2D, 8, 8, 1, 21, HOST_D9_USAGE_RENDERTARGET};
    host_d9_texture_define(&rt);
    HostD9TextureDesc dz{DEPTH, HOST_D9_TEX_2D, 8, 8, 1, 75, HOST_D9_USAGE_DEPTH};
    host_d9_texture_define(&dz);
    HostD9Target target{};
    target.color[0] = {RT, 0, 0};
    target.depth = {DEPTH, 0, 0};
    int32_t vp[4] = {0, 0, 8, 8};

    // A clear lands in the target.
    host_d9_clear(&target, vp, 0, nullptr, 7, 0xff00ff00u, 1.0f, 0);
    uint8_t p[4];
    d9_pixel(RT, 3, 3, p);
    CHECK_EQ(p[0], 0);
    CHECK_EQ(p[1], 255);
    CHECK_EQ(p[2], 0);

    std::vector<uint8_t> vs =
        d9_words({0xFFFE0101u, 0x0000001Fu, 0x80000000u, 0x900F0000u, 0x00000001u, 0xC00F0000u,
                  0x90E40000u, 0x00000001u, 0xD00F0000u, 0xA0E40000u, 0x0000FFFFu});
    std::vector<uint8_t> ps =
        d9_words({0xFFFF0101u, 0x00000001u, 0x800F0000u, 0x90E40000u, 0x0000FFFFu});
    const uint8_t decl[16] = {0, 0, 0, 0, 2, 0, 0, 0, 0xff, 0, 0, 0, 17, 0, 0, 0};
    D9Pipeline pl;
    pl.rs[7] = 1; // ZENABLE
    // A clockwise (front-facing) triangle over the whole target.
    const float cw[9] = {-1, 1, 0.5f, 3, 1, 0.5f, -1, -3, 0.5f};
    const float red[4] = {1, 0, 0, 1};
    HostD9Draw d{};
    d.target = target;
    memcpy(d.viewport, vp, sizeof vp);
    d.depth_range[1] = 1.0f;
    d.vs = vs.data();
    d.vs_size = (uint32_t)vs.size();
    d.ps = ps.data();
    d.ps_size = (uint32_t)ps.size();
    d.vconst = red;
    d.vconst_count = 1;
    d.decl = decl;
    d.decl_size = sizeof decl;
    d.decl_id = 1;
    d.stream[0].stride = 12;
    d.inline_vertices = (const uint8_t *)cw;
    d.inline_bytes = sizeof cw;
    d.primitive = 4;
    d.primitive_count = 1;
    d.sampler_state = &pl.sampler_state[0][0];
    d.render_state = pl.rs;
    d.render_state_set = (const uint8_t *)pl.rs_set;
    host_d9_draw(&d);
    d9_pixel(RT, 3, 3, p);
    CHECK_EQ(p[2], 255); // BGRA: red
    CHECK_EQ(p[1], 0);

    // Counter-clockwise is culled by the default CULLMODE.
    const float ccw[9] = {-1, 1, 0.25f, -1, -3, 0.25f, 3, 1, 0.25f};
    const float blue[4] = {0, 0, 1, 1};
    d.inline_vertices = (const uint8_t *)ccw;
    d.vconst = blue;
    host_d9_draw(&d);
    d9_pixel(RT, 3, 3, p);
    CHECK_EQ(p[2], 255);
    CHECK_EQ(p[0], 0);

    // Depth: a nearer triangle wins, a farther one does not.
    pl.rs[22] = 1;                            // CULLMODE none
    d.inline_vertices = (const uint8_t *)ccw; // z 0.25, nearer than 0.5
    host_d9_draw(&d);
    d9_pixel(RT, 3, 3, p);
    CHECK_EQ(p[0], 255);
    const float far_tri[9] = {-1, 1, 0.75f, -1, -3, 0.75f, 3, 1, 0.75f};
    const float white[4] = {1, 1, 1, 1};
    d.inline_vertices = (const uint8_t *)far_tri;
    d.vconst = white;
    host_d9_draw(&d);
    d9_pixel(RT, 3, 3, p);
    CHECK_EQ(p[1], 0); // still blue

    // Occlusion queries count the samples that pass: none for the hidden
    // triangle, all 64 per draw for a nearer one that covers the target.
    // Neither is ready before its frame has run.
    uint32_t count = 99;
    CHECK_EQ(host_d9_query_result(1, &count), -1);
    host_d9_query_begin(1);
    host_d9_draw(&d); // the far triangle again
    host_d9_query_end(1);
    host_d9_query_begin(2);
    const float near_tri[9] = {-1, 1, 0.125f, -1, -3, 0.125f, 3, 1, 0.125f};
    const float near2_tri[9] = {-1, 1, 0.0625f, -1, -3, 0.0625f, 3, 1, 0.0625f};
    d.inline_vertices = (const uint8_t *)near_tri;
    d.vconst = blue;
    host_d9_draw(&d);
    d.inline_vertices = (const uint8_t *)near2_tri;
    host_d9_draw(&d);
    host_d9_query_end(2);
    CHECK_EQ(host_d9_query_result(2, &count), 0);
    d9_pixel(RT, 3, 3, p); // runs the frame
    CHECK_EQ(host_d9_query_result(1, &count), 1);
    CHECK_EQ(count, 0u);
    CHECK_EQ(host_d9_query_result(2, &count), 1);
    CHECK_EQ(count, 128u);
    host_d9_query_drop(1);
    host_d9_query_drop(2);
    CHECK_EQ(host_d9_query_result(1, &count), -1);

    // Additive blending.
    pl.rs[7] = 0;
    pl.rs[27] = 1;
    pl.rs[19] = 2;
    pl.rs[20] = 2;
    const float green[4] = {0, 1, 0, 1};
    d.vconst = green;
    host_d9_draw(&d);
    d9_pixel(RT, 3, 3, p);
    CHECK_EQ(p[0], 255);
    CHECK_EQ(p[1], 255);

    // A depth texture sampled as a shadow map: the lookup compares the
    // coordinate's z with the stored depth, 0.5 here.
    const uint32_t SHADOW = 90003;
    HostD9TextureDesc sd{SHADOW, HOST_D9_TEX_2D, 8, 8, 1, 75, HOST_D9_USAGE_DEPTH};
    host_d9_texture_define(&sd);
    HostD9Target shadow_pass{};
    shadow_pass.color[0] = {RT, 0, 0};
    shadow_pass.depth = {SHADOW, 0, 0};
    host_d9_clear(&shadow_pass, vp, 0, nullptr, 2, 0, 0.5f, 0);
    // vs: mov oPos, v0; mov oD0, c0; mov oT0, c1. ps 1.1: tex t0; mov r0, t0.
    std::vector<uint8_t> vs_t =
        d9_words({0xFFFE0101u, 0x0000001Fu, 0x80000000u, 0x900F0000u, 0x00000001u, 0xC00F0000u,
                  0x90E40000u, 0x00000001u, 0xD00F0000u, 0xA0E40000u, 0x00000001u, 0xE00F0000u,
                  0xA0E40001u, 0x0000FFFFu});
    std::vector<uint8_t> ps_t = d9_words({0xFFFF0101u, 0x00000042u, 0xB00F0000u, 0x00000001u,
                                          0x800F0000u, 0xB0E40000u, 0x0000FFFFu});
    pl.rs[7] = 0;
    pl.rs[27] = 0;
    d.target = HostD9Target{};
    d.target.color[0] = {RT, 0, 0};
    d.vs = vs_t.data();
    d.vs_size = (uint32_t)vs_t.size();
    d.ps = ps_t.data();
    d.ps_size = (uint32_t)ps_t.size();
    d.vs_key = d.ps_key = 0;
    d.inline_vertices = (const uint8_t *)cw;
    d.sampler_texture[0] = SHADOW;
    for (float z : {0.25f, 0.75f}) {
        const float consts[8] = {1, 0, 1, 1, 0.5f, 0.5f, z, 1};
        d.vconst = consts;
        d.vconst_count = 2;
        host_d9_draw(&d);
        d9_pixel(RT, 3, 3, p);
        const uint8_t want = z < 0.5f ? 255 : 0;
        CHECK_EQ(p[0], want);
        CHECK_EQ(p[1], want);
        CHECK_EQ(p[2], want);
    }
    d.sampler_texture[0] = 0;

    // Dependent ps_1_x lookups use alpha/red or green/blue as U/V. Four
    // distinct texels catch channel swaps and sampling the source stage again.
    const uint32_t SOURCE = 90030, LOOKUP = 90031;
    HostD9TextureDesc source_desc{SOURCE, HOST_D9_TEX_2D, 1, 1, 1, 21, 0};
    HostD9TextureDesc lookup_desc{LOOKUP, HOST_D9_TEX_2D, 2, 2, 1, 21, 0};
    host_d9_texture_define(&source_desc);
    host_d9_texture_define(&lookup_desc);
    const uint8_t lookup_texels[] = {0,   0, 255, 255, 0,   255, 0,   255,
                                     255, 0, 0,   255, 255, 255, 255, 255};
    host_d9_texture_upload(LOOKUP, 0, 0, lookup_texels, 8);
    d.vs = vs_t.data();
    d.vs_size = (uint32_t)vs_t.size();
    d.vs_key = d.ps_key = 0;
    const float lookup_consts[8] = {0, 0, 0, 0, 0.5f, 0.5f, 0, 1};
    d.vconst = lookup_consts;
    d.vconst_count = 2;
    d.sampler_texture[0] = SOURCE;
    d.sampler_texture[1] = LOOKUP;
    for (uint32_t op : {69u, 70u}) {
        auto ps_lookup =
            d9_words({op == 69 ? 0xFFFF0101u : 0xFFFF0102u, 66u, 0xB00F0000u, op, 0xB00F0001u,
                      0xB0E40000u, 1u, 0x800F0000u, 0xB0E40001u, 0xFFFFu});
        CHECK_EQ(d9msl::pixel_sampler_mask(d9sh::program_for(ps_lookup)), 3u);
        d.ps = ps_lookup.data();
        d.ps_size = (uint32_t)ps_lookup.size();
        for (unsigned flip = 0; flip < 2; ++flip) {
            const uint8_t lo = flip ? 192 : 64, hi = flip ? 64 : 192;
            const uint8_t source_texel[] = {hi, lo, lo, hi};
            host_d9_texture_upload(SOURCE, 0, 0, source_texel, 4);
            host_d9_draw(&d);
            d9_pixel(RT, 3, 3, p);
            const bool blue = bool(flip) != (op == 70);
            CHECK_EQ(p[0], blue ? 255 : 0);
            CHECK_EQ(p[1], blue ? 0 : 255);
            CHECK_EQ(p[2], 0);
        }
    }
    d.sampler_texture[0] = d.sampler_texture[1] = 0;
    host_d9_texture_drop(SOURCE);
    host_d9_texture_drop(LOOKUP);

    // An 8x8 target spans two clip-coordinate units. A fragment derivative
    // of the interpolated position is +0.25 in X, -0.25 in Y (top-down).
    // Bias by 0.5 so a reversed Y sign cannot disappear in UNORM clipping.
    auto vs_gradient = d9_words({0xFFFE0101u, 31u, 0x80000000u, 0x900F0000u, 1u, 0xC00F0000u,
                                 0x90E40000u, 1u, 0xE00F0000u, 0x90E40000u, 0xFFFFu});
    for (uint32_t op : {91u, 92u}) {
        auto ps_gradient = d9_words(
            {0xFFFF0200u, 0x0200001Fu, 0x80000000u, 0xB00F0000u, 0x05000051u, 0xA00F0000u,
             0x3F000000u, 0x3F000000u, 0x3F000000u, 0x3F000000u, 0x02000000u | op, 0x800F0000u,
             0xB0E40000u, 0x03000002u, 0x800F0800u, 0x80E40000u, 0xA0E40000u, 0xFFFFu});
        d.vs = vs_gradient.data();
        d.vs_size = (uint32_t)vs_gradient.size();
        d.ps = ps_gradient.data();
        d.ps_size = (uint32_t)ps_gradient.size();
        d.vconst = nullptr;
        d.vconst_count = 0;
        host_d9_draw(&d);
        d9_pixel(RT, 3, 3, p);
        CHECK_NEAR(p[2], op == 91 ? 191 : 128, 1);
        CHECK_NEAR(p[1], op == 92 ? 64 : 128, 1);
        CHECK_NEAR(p[0], 128, 1);
    }

    // Runtime Boolean registers select different branches in both stages.
    // Check b0/b15, noncanonical TRUE, local defb precedence, and queue
    // ownership by changing the caller's constants before GPU readback.
    for (bool vertex : {false, true}) {
        for (uint32_t index : {0u, 15u}) {
            for (int defined : {-1, 0, 1}) {
                std::vector<uint8_t> branching = d9_words({vertex ? 0xFFFE0200u : 0xFFFF0200u});
                auto words = [&](std::initializer_list<uint32_t> values) {
                    const auto bytes = d9_words(values);
                    branching.insert(branching.end(), bytes.begin(), bytes.end());
                };
                if (vertex)
                    words({0x0200001Fu, 0x80000000u, 0x900F0000u, 0x02000001u, 0xC00F0000u,
                           0x90E40000u});
                if (defined >= 0)
                    words({0x0200002Fu, 0xE00F0800u | index, uint32_t(defined)});
                const uint32_t out = vertex ? 0xD00F0000u : 0x800F0800u;
                words({0x01000028u, 0xE0E40800u | index, 0x02000001u, out, 0xA0E40001u, 0x0000002Au,
                       0x02000001u, out, 0xA0E40000u, 0x0000002Bu, 0xFFFFu});
                d.vs = vertex ? branching.data() : vs.data();
                d.vs_size = uint32_t(vertex ? branching.size() : vs.size());
                d.ps = vertex ? ps.data() : branching.data();
                d.ps_size = uint32_t(vertex ? ps.size() : branching.size());
                const float colors[8] = {1, 0, 0, 1, 0, 1, 0, 1};
                d.vconst = d.pconst = colors;
                d.vconst_count = d.pconst_count = 2;
                for (int32_t value : {0, -7, 1, 0}) {
                    int32_t *b = vertex ? d.vbool : d.pbool;
                    b[index] = value;
                    host_d9_draw(&d);
                    b[index] = value ? 0 : 1;
                    d9_pixel(RT, 3, 3, p);
                    const bool green = defined >= 0 ? bool(defined) : value != 0;
                    CHECK_EQ(p[0], 0);
                    CHECK_EQ(p[1], green ? 255 : 0);
                    CHECK_EQ(p[2], green ? 0 : 255);
                }
            }
        }
    }
    memset(d.vbool, 0, sizeof d.vbool);
    memset(d.pbool, 0, sizeof d.pbool);
    d.pconst = nullptr;
    d.pconst_count = 0;

    // D3D assembly EXP/EXPP are base 2, unlike HLSL/Metal's exp().
    // vs_1_1 EXPP also has component-specific results, including floor for
    // negative fractional inputs. Check actual GPU pixels, not source text.
    for (uint32_t version : {0xFFFE0101u, 0xFFFE0200u}) {
        for (uint32_t op : {14u, 78u}) {
            const uint32_t len = version == 0xFFFE0200u ? 0x02000000u : 0;
            auto vs_exp =
                d9_words({version, len | 31u, 0x80000000u, 0x900F0000u, len | 1u, 0xC00F0000u,
                          0x90E40000u, len | op, 0xD00F0000u, 0xA0000000u, 0x0000FFFFu});
            auto ps_exp = d9_words({0xFFFF0101u, 1u, 0x800F0000u, 0x90E40000u, 0x0000FFFFu});
            const float exponent[4] = {-1.5f, 0, 0, 0};
            d.vs = vs_exp.data();
            d.vs_size = (uint32_t)vs_exp.size();
            d.ps = ps_exp.data();
            d.ps_size = (uint32_t)ps_exp.size();
            d.vs_key = d.ps_key = 0;
            d.vconst = exponent;
            d.vconst_count = 1;
            host_d9_draw(&d);
            d9_pixel(RT, 3, 3, p);
            CHECK(p[0] >= 89 && p[0] <= 91); // z: 2^-1.5
            if (version == 0xFFFE0101u && op == 78u) {
                CHECK(p[2] >= 63 && p[2] <= 65);   // x: 2^floor(-1.5)
                CHECK(p[1] >= 127 && p[1] <= 129); // y: fractional part
            } else {
                CHECK(p[2] >= 89 && p[2] <= 91);
                CHECK(p[1] >= 89 && p[1] <= 91);
            }
        }
    }

    // Shader model 3.0: outputs named by their dcl, and a rep loop on a defi
    // count. vs: dcl_position o0; dcl_color o1; mov o0, v0; mov o1, c0.
    // ps: defi i0 = 3; def c1 = 0.25; rep i0 { r0 += c1 }; oC0 = r0 (r0 starts at 0).
    std::vector<uint8_t> vs3 =
        d9_words({0xFFFE0300u, 0x0200001Fu, 0x80000000u, 0x900F0000u, 0x0200001Fu, 0x80000000u,
                  0xE00F0000u, 0x0200001Fu, 0x8000000Au, 0xE00F0001u, 0x02000001u, 0xE00F0000u,
                  0x90E40000u, 0x02000001u, 0xE00F0001u, 0xA0E40000u, 0x0000FFFFu});
    std::vector<uint8_t> ps3 = d9_words(
        {0xFFFF0300u, 0x05000030u, 0xF00F0000u, 3u,          0u,          0u,          0u,
         0x05000051u, 0xA00F0001u, 0x3E800000u, 0x3E800000u, 0x3E800000u, 0x3E800000u, 0x0200001Fu,
         0x8000000Au, 0x900F0000u, 0x01000026u, 0xF0E40000u, 0x03000002u, 0x800F0000u, 0x80E40000u,
         0xA0E40001u, 0x00000027u, 0x02000001u, 0x800F0800u, 0x80E40000u, 0x0000FFFFu});
    d.vs = vs3.data();
    d.vs_size = (uint32_t)vs3.size();
    d.ps = ps3.data();
    d.ps_size = (uint32_t)ps3.size();
    d.vs_key = d.ps_key = 0;
    const float sm3_consts[8] = {0, 0, 0, 0, 0, 0, 0, 0};
    d.vconst = sm3_consts;
    d.vconst_count = 1;
    host_d9_draw(&d);
    d9_pixel(RT, 3, 3, p);
    CHECK(p[2] >= 190 && p[2] <= 192); // three passes of 0.25
    CHECK(p[1] >= 190 && p[1] <= 192);
    const d9sh::Program &prog3 = d9sh::program_for(ps3);
    CHECK(prog3.ok && prog3.major == 3 && prog3.idefs.count(0) == 1);

    // Multisampling: a diagonal edge over black leaves partly covered pixels
    // in a 4x target, and none in a single-sampled one.
    for (uint32_t samples : {1u, 4u}) {
        const uint32_t MS = 90010 + samples, MSZ = 90020 + samples;
        HostD9TextureDesc md{MS, HOST_D9_TEX_2D, 8, 8, 1, 21, HOST_D9_USAGE_RENDERTARGET, samples};
        HostD9TextureDesc mz{MSZ, HOST_D9_TEX_2D, 8, 8, 1, 75, HOST_D9_USAGE_DEPTH, samples};
        host_d9_texture_define(&md);
        host_d9_texture_define(&mz);
        HostD9Target mt{};
        mt.color[0] = {MS, 0, 0};
        mt.depth = {MSZ, 0, 0};
        host_d9_clear(&mt, vp, 0, nullptr, 7, 0xff000000u, 1.0f, 0);
        d.target = mt;
        d.vs = vs.data();
        d.vs_size = (uint32_t)vs.size();
        d.ps = ps.data();
        d.ps_size = (uint32_t)ps.size();
        d.vs_key = d.ps_key = 0;
        const float diag[9] = {-1, 1, 0.5f, 1, 1, 0.5f, -1, -1.3f, 0.5f};
        d.inline_vertices = (const uint8_t *)diag;
        d.vconst = red;
        d.vconst_count = 1;
        pl.rs[7] = 1;
        pl.rs[22] = 1;
        host_d9_draw(&d);
        std::vector<uint8_t> px(8 * 8 * 4);
        CHECK(host_d9_texture_read(MS, 0, 0, px.data(), 8 * 4) != 0);
        int partial = 0, full = 0;
        for (int i = 0; i < 64; ++i) {
            uint8_t r = px[i * 4 + 2];
            partial += r > 10 && r < 245;
            full += r >= 245;
        }
        CHECK(full > 10);
        if (samples == 1)
            CHECK_EQ(partial, 0);
        else
            CHECK(partial >= 3);
        host_d9_texture_drop(MS);
        host_d9_texture_drop(MSZ);
    }

#ifdef __APPLE__
    // Test157b: a single-sampled depth buffer under a 4x target gets a fitted depth buffer of the pass's samples.
    // Its cache entry goes with the source: 64 depth textures defined, drawn with and dropped leave the cache at
    // its starting size (it used to grow by one per texture for the rest of the session).
    if (host_d9_metal_fitted_depth_entries() != SIZE_MAX) {
        const uint32_t MS = 90031, Z0 = 90100;
        HostD9TextureDesc md{MS, HOST_D9_TEX_2D, 8, 8, 1, 21, HOST_D9_USAGE_RENDERTARGET, 4};
        host_d9_texture_define(&md);
        std::vector<uint8_t> px(8 * 8 * 4);
        CHECK(host_d9_texture_read(MS, 0, 0, px.data(), 8 * 4) != 0); // waits for the render thread
        const size_t base = host_d9_metal_fitted_depth_entries();
        const float tri[9] = {-1, 1, 0.5f, 3, 1, 0.5f, -1, -3, 0.5f};
        d.inline_vertices = (const uint8_t *)tri;
        d.inline_bytes = sizeof tri;
        pl.rs[7] = 1;
        for (uint32_t i = 0; i < 64; ++i) {
            HostD9TextureDesc z{Z0 + i, HOST_D9_TEX_2D, 8, 8, 1, 75, HOST_D9_USAGE_DEPTH, 1};
            host_d9_texture_define(&z);
            HostD9Target mt{};
            mt.color[0] = {MS, 0, 0};
            mt.depth = {Z0 + i, 0, 0};
            host_d9_clear(&mt, vp, 0, nullptr, 7, 0xff000000u, 1.0f, 0);
            d.target = mt;
            host_d9_draw(&d);
            CHECK(host_d9_texture_read(MS, 0, 0, px.data(), 8 * 4) != 0);
            if (i == 0)
                CHECK_EQ(host_d9_metal_fitted_depth_entries(), base + 1); // the fitted path was taken
            host_d9_texture_drop(Z0 + i);
        }
        CHECK(host_d9_texture_read(MS, 0, 0, px.data(), 8 * 4) != 0);
        CHECK_EQ(host_d9_metal_fitted_depth_entries(), base);
        // a redefined source also drops its entry
        HostD9TextureDesc z{Z0, HOST_D9_TEX_2D, 8, 8, 1, 75, HOST_D9_USAGE_DEPTH, 1};
        host_d9_texture_define(&z);
        HostD9Target mt{};
        mt.color[0] = {MS, 0, 0};
        mt.depth = {Z0, 0, 0};
        d.target = mt;
        host_d9_draw(&d);
        host_d9_texture_define(&z);
        CHECK(host_d9_texture_read(MS, 0, 0, px.data(), 8 * 4) != 0);
        CHECK_EQ(host_d9_metal_fitted_depth_entries(), base);
        printf("fitted depth lifetime: 64 sources defined, drawn and dropped; cache back to %zu entries\n", base);
        host_d9_texture_drop(Z0);
        host_d9_texture_drop(MS);
    }
#endif
    host_d9_texture_drop(SHADOW);
    host_d9_texture_drop(RT);
    host_d9_texture_drop(DEPTH);
}

// Deterministic transient failures in submitted work: the ten-frame history
// must retain bad frames together with their recovered neighbors. No game,
// profiles or visible window are involved; Python checks the emitted artifact.
// R3 run 31 O6: label_class::of answers every prefix exactly as the strncmp it replaced, near misses included.
// NFSU1 387564e in the shared kit: a focus change before the guest has a window is not counted as delivered, so
// the next note (the window now exists) queues it again; once delivered, a repeat is not queued.
static void test_focus_note_waits_for_a_guest_window() {
    FocusNote f;
    CHECK(f.note(true, false)); // focused before CreateWindow: queued, dropped by apply_focus
    CHECK(!f.activated);
    CHECK(f.note(true, true));  // the same focus once the window exists: queued again (was lost before)
    CHECK(f.activated);
    CHECK(!f.note(true, true)); // nothing new
    CHECK(f.note(false, true)); // a real change
    CHECK(f.note(true, true));
    FocusNote old_rule; // control: the earlier rule marked the first note delivered whatever the window
    old_rule.note(true, false);
    old_rule.activated = true;
    CHECK(!old_rule.note(true, true)); // so the activation never reached the guest
}

static void test_label_class_matches_strncmp() {
    const char *labels[] = {nullptr, "", "IDI", "IDI_", "IDI_W", "IDI_WORLD", "IDI_WORLD_FX", "IDI_WORLD_FX/",
                            "IDI_WORLD_FX/p0", "IDI_WORLDX", "IDI_WORLDNORMALMAP_FX", "IDI_WORLDNORMALMAP_FX/a",
                            "IDI_WORLDREFLECT_FX/", "IDI_PARTICLES_FX/", "IDI_PARTICLES_FX", "IDI_PARTICLE_FX/",
                            "IDI_GLOSSYWINDOW_FX/t", "IDI_GLOSSYWINDOW", "IDI_SKYBOX_FX/", "IDI_SKY_FX/",
                            "IDI_RAIN_DROP_FX/", "IDI_RAIN_FX/", "IDI_CAR_FX/x", "IDI_CARS_FX/", "IDI_CAR_FX",
                            "idi_world_fx/", "XIDI_WORLD_FX/", "IDI_WORLD_FX/IDI_CAR_FX/"};
    const uint32_t bits[] = {label_class::WORLD, label_class::WORLD_FX, label_class::WORLDNORMALMAP,
                             label_class::PARTICLES, label_class::GLOSSYWINDOW, label_class::SKYBOX,
                             label_class::RAIN_DROP, label_class::CAR};
    int hits = 0;
    for (const char *l : labels)
        for (uint32_t b : bits) {
            const char *p = label_class::prefix(b);
            const bool want = l && !strncmp(l, p, strlen(p));
            CHECK(((label_class::of(l) & b) != 0) == want);
            hits += want;
        }
    CHECK(hits == 18); // WORLD 9, WORLD_FX 3, one each for the other six
}

static void test_d3d9_trace_capture() {
    const uint32_t rt_id = 91601, depth_id = 91602, vb_id = 91603, ib_id = 91604;
    HostD9TextureDesc rt{rt_id, HOST_D9_TEX_2D, 8, 8, 1, 21, HOST_D9_USAGE_RENDERTARGET};
    HostD9TextureDesc depth{depth_id, HOST_D9_TEX_2D, 8, 8, 1, 75, HOST_D9_USAGE_DEPTH};
    host_d9_texture_define(&rt);
    host_d9_texture_define(&depth);
    auto vs = d9_words({0xFFFE0101u, 0x1Fu, 0x80000000u, 0x900F0000u, 1u, 0xC00F0000u, 0x90E40000u,
                        1u, 0xD00F0000u, 0xA0E40000u, 0xFFFFu});
    auto ps = d9_words({0xFFFF0101u, 1u, 0x800F0000u, 0x90E40000u, 0xFFFFu});
    const uint8_t decl[16] = {0, 0, 0, 0, 2, 0, 0, 0, 0xff, 0, 0, 0, 17, 0, 0, 0};
    const float vertices[9] = {-1, 1, 0.5f, 3, 1, 0.5f, -1, -3, 0.5f};
    const uint16_t indices[3] = {0, 1, 2};
    D9Pipeline pl;
    pl.rs[7] = 1;
    HostD9Draw d{};
    d.target.color[0] = {rt_id, 0, 0};
    d.target.depth = {depth_id, 0, 0};
    d.viewport[2] = d.viewport[3] = 8;
    d.depth_range[1] = 1;
    d.vs = vs.data();
    d.vs_size = (uint32_t)vs.size();
    d.ps = ps.data();
    d.ps_size = (uint32_t)ps.size();
    d.decl = decl;
    d.decl_size = sizeof decl;
    d.decl_id = 1;
    d.stream[0] = {vb_id, 0, 12};
    d.index_size = 2;
    d.sampler_state = &pl.sampler_state[0][0];
    d.render_state = pl.rs;
    d.render_state_set = (const uint8_t *)pl.rs_set;
    d.vconst_count = 1;
    if (recomp_env("D3D9_TRACE_BURST"))
        host_d9_probe_next_frame("burst-contract");
    for (uint32_t frame = 1; frame <= 12; ++frame) {
        if (frame == 9)
            host_d9_buffer_drop(ib_id); // explicit generation change
        host_d9_buffer_upload(vb_id, sizeof vertices, 0, (const uint8_t *)vertices,
                              sizeof vertices);
        host_d9_buffer_upload(ib_id, sizeof indices, 0, (const uint8_t *)indices, sizeof indices);
        host_d9_clear(&d.target, d.viewport, 0, nullptr, 7, 0xff00ff00u, 1, 0);
        char label[] = "trace-test \"triangle\"";
        d.label = label;
        float red[4] = {1, 0, 0, 1};
        d.vconst = red;
        d.index_buffer = frame == 6 && !host_d9_admission_enabled() ? 99991 : ib_id;
        d.stream[0].buffer = frame == 8 && !host_d9_admission_enabled() ? 99992 : vb_id;
        d.primitive_count = frame == 10 ? 0 : 1;
        d.primitive = frame == 11 ? 99 : 4;
        const bool admission = host_d9_admission_enabled();
        if (admission) {
            d.admission.serial = frame + 1;
            host_d9_admission_entry(frame + 1);
            d.admission.api = 82;
            d.admission.caller = 0x1234;
            d.admission.resource_identity[0] = 0x5678;
            d.admission.reason = frame == 7 ? 3 : 0;
        }
        if (admission && frame == 7) {
            HostD9Draw sparse{};
            sparse.admission = d.admission;
            sparse.admission.api = 84;
            sparse.admission.reason = 4;
            memset(sparse.admission.resource_identity, 0,
                   sizeof sparse.admission.resource_identity);
            sparse.stream[0].stride = 12;
            sparse.decl_id = d.decl_id;
            sparse.admission.inline_vertices = 0xfffffff0u;
            sparse.admission.inline_indices = 0xfffffffcu;
            sparse.target = d.target;
            memcpy(sparse.viewport, d.viewport, sizeof sparse.viewport);
            sparse.primitive = d.primitive;
            sparse.primitive_count = d.primitive_count;
            sparse.label = label;
            CHECK(!sparse.vs && !sparse.ps && !sparse.decl && !sparse.vconst);
            CHECK(!sparse.inline_vertices && !sparse.inline_indices && !sparse.vs_key);
            host_d9_rejected_draw(&sparse);
            sparse.admission = {};
        } else
            host_d9_draw(&d);
        d.admission = {}; // queued metadata must already be immutable
        label[0] = 'X';   // label storage must be copied too
        red[0] = 0;       // the queue and trace must already own the old constants
        host_d9_present(rt_id, 8, 8);
        uint8_t pixel[4];
        d9_pixel(rt_id, 3, 3, pixel);
        const bool drawn = (admission || (frame != 6 && frame != 8)) && frame != 10 &&
                           frame != 11 && !(admission && frame == 7);
        CHECK_EQ(pixel[2], drawn ? 255 : 0);
        CHECK_EQ(pixel[1], drawn ? 0 : 255);
    }
    host_d9_probe_next_frame("trace-contract");
    uint8_t pixel[4];
    d9_pixel(rt_id, 3, 3, pixel); // drain the queued flush
    host_d9_buffer_drop(vb_id);
    host_d9_buffer_drop(ib_id);
    host_d9_texture_drop(rt_id);
    host_d9_texture_drop(depth_id);
}

int main(int argc, char **argv) {
    if (argc == 2 && !strcmp(argv[1], "--capture-overlay")) {
        test_native_overlay_pixels();
        printf("capture overlay: %d checks, %d failures\n", g_checks, g_failures);
        return g_failures ? 1 : 0;
    }

    if (argc >= 2 && !strcmp(argv[1], "--d3d9-volume-dds")) {
        if (argc < 3) {
            fprintf(stderr, "--d3d9-volume-dds requires at least one DDS path\n");
            return 2;
        }
        g_gpu = gpu::create_default_device();
        if (!g_gpu || !host_d9_use_device_for_test(g_gpu.get()))
            return 2;
        for (int i = 2; i < argc; ++i)
            test_d3d9_dds_volume(argv[i]);
        printf("volume DDS GPU: %d checks, %d failures\n", g_checks, g_failures);
        return g_failures ? 1 : 0;
    }
    if (argc == 2 && !strcmp(argv[1], "--d3d9-trace")) {
        const char *enabled = recomp_env("D3D9_TRACE");
        if (!enabled || strcmp(enabled, "1")) {
            fprintf(stderr, "--d3d9-trace requires RECOMP_D3D9_TRACE=1\n");
            return 2;
        }
        if (host_d9_admission_enabled()) {
            // Actual unavailable-forward path before any renderer exists.
            HostD9Draw unavailable{};
            unavailable.admission.serial = 1;
            host_d9_admission_entry(1);
            host_d9_draw(&unavailable);
        }
        g_gpu = gpu::create_default_device();
        if (!g_gpu || !host_d9_use_device_for_test(g_gpu.get()))
            return 2;
        test_d3d9_trace_capture();
        printf("d3d9 trace GPU: %d checks, %d failures\n", g_checks, g_failures);
        return g_failures ? 1 : 0;
    }
    if (argc == 3 && !strcmp(argv[1], "--audio-identification")) {
        test_audio_speaker_layout();
        test_audio_identification(argv[2]);
        printf("audio identification: %d checks, %d failures\n", g_checks, g_failures);
        return g_failures ? 1 : 0;
    }
    if (argc == 3 && !strcmp(argv[1], "--audio-channel-test")) {
        test_audio_channel_device(argv[2]);
        printf("audio channel device: %d checks, %d failures\n", g_checks, g_failures);
        return g_failures ? 1 : 0;
    }
    if (argc == 3 && !strcmp(argv[1], "--audio-device-modes")) {
        test_audio_device_modes(argv[2]);
        printf("audio device modes: %d checks, %d failures\n", g_checks, g_failures);
        return g_failures ? 1 : 0;
    }
    if (argc == 2 && !strcmp(argv[1], "--audio-device")) {
        test_audio_silent_device();
        printf("audio device: %d checks, %d failures\n", g_checks, g_failures);
        return g_failures ? 1 : 0;
    }
    if (argc == 2 && !strcmp(argv[1], "--audio-only")) {
        test_audio_diagnostics();
        test_audio_maths();
        test_audio_completion_during_stop();
        test_audio_gapless_queue();
        test_audio_looping_refill();
        test_audio_one_shot_stream_preserves_schedule();
        test_audio_negative_player_time();
        test_audio_fmv_ring();
        test_audio_capture_metrics();
        test_audio_sdl_channel_conversion();
        test_audio_speaker_layout();
        test_audio_presentation();
        test_audio_dolby();
        test_audio_outputs_idle();
        test_audio_six_channel_planes();
        test_audio_snd_mode_sequence();
        test_audio_format_change();
        test_audio_clipper_takes_only_the_overshoot();
        test_audio_ring_and_clock();
        test_audio_replace_on_one_channel();
        test_audio_every_sound_started_is_heard();
        test_audio_voice_remaining_versus_queued();
        printf("audio: %d checks, %d failures\n", g_checks, g_failures);
        return g_failures ? 1 : 0;
    }
    if (argc == 2 && !strcmp(argv[1], "--dumpat-only")) {
        test_dumpat_present_and_seal();
        printf("dumpat integration: %d checks, %d failures\n", g_checks, g_failures);
        return g_failures ? 1 : 0;
    }
    if (argc == 2 && !strcmp(argv[1], "--landmark-only")) {
        test_landmark_hidden_evidence();
        printf("landmark: %d checks, %d failures\n", g_checks, g_failures);
        return g_failures ? 1 : 0;
    }
    if (argc == 2 && !strcmp(argv[1], "--display-only")) {
        test_display_settings_bridge();
        printf("display: %d checks, %d failures\n", g_checks, g_failures);
        return g_failures ? 1 : 0;
    }
    if (argc == 2 && !strcmp(argv[1], "--game-rect-only")) {
        test_game_rect_landscape_is_todays_placement();
        test_game_rect_portrait();
        test_landscape_screen_size();
        test_portrait_presenter_maps_through_the_game_rect();
        printf("game rect: %d checks, %d failures\n", g_checks, g_failures);
        return g_failures ? 1 : 0;
    }
    if (argc > 1 && !strcmp(argv[1], "--presenter-only")) {
        test_stats_line_format();
        test_presentation_service();
        printf("presenter: %d checks, %d failures\n", g_checks, g_failures);
        return g_failures ? 1 : 0;
    }
    if (argc > 1 && !strcmp(argv[1], "--d3d9-gpu")) {
        g_gpu = gpu::create_default_device();
        if (!g_gpu || !host_d9_use_device_for_test(g_gpu.get())) {
            printf("no Metal device: Direct3D 9 GPU test did not run\n");
            return 2;
        }
        test_d3d9_gpu_renderer();
        test_d3d9_volume_renderer();
        printf("d3d9 GPU: %d checks, %d failures\n", g_checks, g_failures);
        return g_failures ? 1 : 0;
    }
    if (argc > 1 && !strcmp(argv[1], "--presenter-gpu")) {
        {
            g_gpu = gpu::create_default_device();
            if (!g_gpu) {
                printf("no Metal device: presenter offscreen pixel test did not run\n");
                return 2;
            }
            host_present_set_device(g_gpu.get());
            D3DRenderer *renderer = make_renderer();
            if (!renderer)
                return 1;
            test_presenter_real_offscreen(renderer);
            test_presenter_incremental_world_and_overlay(renderer);
            test_wide_scene_pixels(renderer);
            test_native_tile_borders(renderer);
            printf("presenter GPU: %d checks, %d failures\n", g_checks, g_failures);
            return g_failures ? 1 : 0;
        }
    }
    mem_init();
    test_fullscreen_edge_presentation();
    test_native_frame_metrics();
    test_slot_pacer();
    test_label_class_matches_strncmp();
    test_focus_note_waits_for_a_guest_window();
    test_wide_cursor_bound();
    test_pointer_reaches_scrolling_edges();
    test_native_overlay_pixels();
    test_native_stick_motion_pixels();
    // Focused headless validation when the sandbox cannot create audio/Metal
    // services. The default suite still runs every test and reports failures.
    if (argc == 2 && !strcmp(argv[1], "--script-only")) {
        test_script_parsing();
        test_script_keyuntil_and_fe_screen();
        test_reference_dump_serialization();
        test_entity_click_wait();
        test_entity_completed_present_freshness();
        test_drain_wanted();
        test_present_suspend_flag();
        printf("script: %d checks, %d failures\n", g_checks, g_failures);
        mem_shutdown();
        return g_failures ? 1 : 0;
    }
    if (argc == 2 && strcmp(argv[1], "--t9-input") == 0) {
        test_native_touch_placement();
        test_t9_guest_pointer_resolution();
        test_guest_pointer_pair_resolution();
        test_t9_hits_and_drag();
        test_t9_snapshot_capture_edges_cursor();
        test_t9_relative_crossing_and_layout_mailbox();
        test_pair_pointer_bounds_follow_the_drawn_frame();
        test_pair_pointer_maps_into_the_ui_box();
        test_t9_pointer_closed_loop();
        test_t9_pointer_letterbox();
        test_t9_pointer_loop_gives_up_when_unanswered();
        test_t9_pointer_survives_layout_rescale();
        test_t9_window_motion_round1();
        test_t9_resize_without_ack_round1();
        test_t9_window_tracking_round2();
        printf("T9 input mapping: %d checks, %d failures\n", g_checks, g_failures);
        return g_failures ? 1 : 0;
    }

    struct {
        const char *name;
        void (*fn)();
    } plain[] = {
        {"game path", test_game_path},
        {"bundled General MIDI bank", test_bundled_general_midi},
        {"controls layouts resource", test_controls_layouts_resource},
        {"display settings bridge", test_display_settings_bridge},
        {"game rect: landscape is today's placement", test_game_rect_landscape_is_todays_placement},
        {"game rect: portrait", test_game_rect_portrait},
        {"landscape screen size on a phone", test_landscape_screen_size},
        {"game rect: portrait presenter and gate",
         test_portrait_presenter_maps_through_the_game_rect},
        {"presentation service", test_presentation_service},
        {"palette expansion", test_palette_expansion},
        {"5-6-5 expansion", test_rgb565_expansion},
        {"letterbox geometry", test_letterbox},
        {"window size for a guest mode", test_window_size},
        {"scan code map", test_scancodes},
        {"input state", test_input_state},
        {"input gate", test_input_gate},
        {"input gate modifiers", test_input_gate_modifiers},
        {"guest click injection", test_guest_click_injection},
        {"landmark hidden evidence", test_landmark_hidden_evidence},
        {"probe and dumpat", test_probe_and_dumpat_decisions},
        {"dumpat present and seal", test_dumpat_present_and_seal},
        {"reference dump serialization", test_reference_dump_serialization},
        {"entity completed present freshness", test_entity_completed_present_freshness},
        {"drain wanted", test_drain_wanted},
        {"run unfinished", test_run_unfinished},
        {"page draws on a copy", test_page_draws_on_a_copy},
        {"menu redrawn while parked", test_menu_redrawn_while_parked},
        {"pointer capture", test_pointer_capture},
        {"T9 hits and drag", test_t9_hits_and_drag},
        {"T9 capture edges cursor", test_t9_snapshot_capture_edges_cursor},
        {"T9 relative and mailbox", test_t9_relative_crossing_and_layout_mailbox},
        {"native touch placement", test_native_touch_placement},
        {"T9 guest pointer resolution", test_t9_guest_pointer_resolution},
        {"guest pointer pair resolution", test_guest_pointer_pair_resolution},
        {"T9 pointer closed loop", test_t9_pointer_closed_loop},
        {"T9 window motion round1", test_t9_window_motion_round1},
        {"T9 resize without ack round1", test_t9_resize_without_ack_round1},
        {"T9 window tracking round2", test_t9_window_tracking_round2},
        {"message translation", test_message_translation},
        {"input announcements", test_input_announces_changes},
        {"idle wait early out", test_idle_wait_early_return},
        {"smoke script", test_script_parsing},
        {"script keyuntil, fe screen", test_script_keyuntil_and_fe_screen},
        {"mouse move path", test_mouse_move_reaches_the_guest},
        {"point to guest", test_point_to_guest},
        {"audio arithmetic", test_audio_maths},
        {"frame rate meter", test_rate_meter},
        {"audio stop re-entry", test_audio_completion_during_stop},
        {"gapless queue", test_audio_gapless_queue},
        {"looping refill", test_audio_looping_refill},
        {"one-shot stream schedule", test_audio_one_shot_stream_preserves_schedule},
        {"negative player time", test_audio_negative_player_time},
        {"FMV ring", test_audio_fmv_ring},
        {"MIDI SoundFont", test_midi_soundfont},
        {"capture metrics", test_audio_capture_metrics},
        {"SDL channel conversion", test_audio_sdl_channel_conversion},
        {"speaker layout", test_audio_speaker_layout},
        {"presentation (fold, mono, centre/LFE exchange)", test_audio_presentation},
        {"audio diagnostic meters and sequence", test_audio_diagnostics},
        {"six-channel planes", test_audio_six_channel_planes},
        {"format change", test_audio_format_change},
        {"clipper", test_audio_clipper_takes_only_the_overshoot},
        {"ring and clock", test_audio_ring_and_clock},
        {"replace on a channel", test_audio_replace_on_one_channel},
        {"every sound heard", test_audio_every_sound_started_is_heard},
        {"voice vs queued", test_audio_voice_remaining_versus_queued},
        {"primitive expansion", test_primitive_expansion},
        {"present counts", test_present_counts},
        {"stats line format", test_stats_line_format},
        {"await at least", test_script_await_at_least},
        {"await hold in frames", test_script_hold_frames},
        {"entity click waits", test_entity_click_wait},
        {"a click is not applied in one turn", test_input_batch_limit},
        {"input hold in frames", test_script_input_hold_frames},
    };
    bool gpu_only = argc == 2 && !strcmp(argv[1], "--gpu-only");
    for (auto &t : plain) {
        if (gpu_only)
            break;
        int before = g_failures;
        t.fn();
        printf("%-26s %s\n", t.name, g_failures == before ? "ok" : "FAILED");
    }

    {
        g_gpu = gpu::create_default_device();
        if (!g_gpu) {
            printf("\nno Metal device: the renderer tests did not run\n");
            printf("%d checks, %d failures\n", g_checks, g_failures);
            return g_failures ? 1 : 2;
        }
        host_present_set_device(g_gpu.get());
        D3DRenderer *renderer = make_renderer();
        if (!renderer) {
            fprintf(stderr, "FAIL: the renderer would not initialise\n");
            return 1;
        }
        D3DRenderer::setShared(renderer);

        struct {
            const char *name;
            void (*fn)(D3DRenderer *);
        } gpu[] = {
            {"offscreen presenter", test_presenter_real_offscreen},
            {"presenter GPU color order", test_presenter_gpu_color_order},
            {"presenter GPU logical coordinates", test_presenter_gpu_logical_coordinates},
            {"Direct3D 11 hardware path", test_gpu2d_pixels},
            {"presenter world and overlay", test_presenter_incremental_world_and_overlay},
            {"wide scene clipping", test_wide_scene_pixels},
            {"native tile borders", test_native_tile_borders},
            {"incremental scene read", test_incremental_scene_readback},
            {"scaled readback parity", test_readback_kernel_parity},
            {"GPU surface upload pixels", test_surface_upload_pixels},
            {"bounded submission pixels", test_bounded_submission_pixels},
            {"parallel readback pixels", test_parallel_readback_pixels},
            {"clear and triangle", test_render_clear_and_triangle},
            {"render target", test_render_target_write_back},
            {"lossless write-back", test_render_target_lossless},
            {"panels reach present", test_render_panels_survive_to_present},
            {"clear before end", test_render_clear_before_end_scene},
            {"render target switch", test_render_target_switch},
            {"gameplay signal", test_gameplay_signal},
            {"no darkening", test_render_no_darkening},
            {"untextured colour", test_render_untextured_uses_vertex_colour},
            {"modulate alpha", test_render_modulate_alpha_source},
            {"discard on reset", test_render_discard_on_reset},
            {"surface desc swap", test_render_target_surface_desc},
            {"render target flip", test_render_target_flip},
            {"depth test", test_render_depth},
            {"same-frame legacy pixels", test_same_frame_legacy_pixels},
            {"legacy checkpoint/mapping", test_legacy_checkpoint_and_baked_mapping},
            {"prefix depth/content", test_prefix_submit_then_continue_keeps_depth_and_content},
            {"four leased targets", test_frame_targets_are_leased},
            {"partial native upload", test_cpu_write_applied_partially},
            {"32-bit surface roundtrip", test_full_color_surface_readback},
            {"blend and alpha test", test_render_blend_and_alpha_test},
            {"fog", test_render_fog},
            {"HD pack / Classic isolation", test_hd_pack_and_classic_isolation},
            {"terrain material detail", test_terrain_material_detail},
            {"textures", test_render_texture},
            {"32-bit texture + alpha", test_render_rgba32},
            {"texture versioning", test_render_texture_versioning},
            {"unchanged uploads / pools", test_unchanged_uploads_and_command_pool},
            {"texture revisions", test_texture_revision_leased_through_frame},
            {"draw snapshot entry", test_draw_snapshot_entry_point},
            {"mipmaps", test_render_mipmaps},
            {"lines and points", test_render_lines_and_points},
            {"rectangle clear", test_render_rect_clear},
        };
        for (auto &t : gpu) {
            int before = g_failures;
            t.fn(renderer);
            printf("%-26s %s\n", t.name, g_failures == before ? "ok" : "FAILED");
        }
    }

    printf("\n%d checks, %d failures\n", g_checks, g_failures);
    if (g_failures == 0)
        printf("all host tests passed\n");
    return g_failures ? 1 : 0;
}
