// main.cpp - the windowed host for the recompiled game, on SDL3.
//
// The guest owns the main thread. That is not a shortcut: the game never
// blocks in GetMessage, it drains its queue with PeekMessageA and spins on
// GetTickCount, so there is no point at which an event loop could take over.
// Instead the host rides on the guest's own clock reads, exactly as the
// headless host does - host/boot.cpp calls back about once a
// millisecond - and that callback is where SDL gets its turn:
//
//     boot_run() -> run_entry() -> ... guest frame loop ...
//                       -> GetTickCount -> boot tick -> pump() -> SDL events
//
// pump() drains the SDL event queue, translates each event into both of the
// input paths the game reads (the DirectInput device state in input.cpp and
// the Win32 message queue). Sealed frames are presented by a dedicated worker.
//
// Nothing here draws the game itself. present_thread.cpp owns the swapchain
// and d3d_render.cpp owns the Direct3D scene; this file owns the window, the
// events and the lifetime.
#if defined(__APPLE__)
#include <TargetConditionals.h>
#endif
#if defined(__APPLE__) && TARGET_OS_OSX
void os_cursor_set_hidden(bool hidden);   // host/sdl/os_cursor_macos.mm (run 27: SDL alone left an idle arrow)
double os_window_max_fps(void *ns_window); // host/sdl/os_display_macos.mm (NFSU1 run 8: 120 Hz screens)
int os_window_notch_pixels(void *ns_window); // host/sdl/os_display_macos.mm (NFSMW run 35: below the notch)
#endif
#include "../../mods/display_settings.h"
#include "game_config.h"
#include "../../runtime/layout.h"
#include "../../platform/os.h"
#include "../audio.h"
#include "../audio/sink.h"
#include "../audio_capture.h"
#include "../boot.h"
#include "../../runtime/display_seam.h"
#include "../d3d_render.h"
#include "../game_path.h"
#include "../launcher/launcher_sdl.h"
#include "../gpu/gpu_factory.h"
#include "../input.h"
#include "../input_gate.h"
#include "../input_touch.h"
#include "../controls/controls_host.h"
#include "../../mods/controls_settings.h"
#include "../controls/gamepad_sdl.h"
#include "../controls/vpad.h"
#include "../script.h"
#include "../../runtime/guest.h"
#include "../../mods/mods_internal.h"
#include "../midi.h"
#include "../present.h"
#include "../../runtime/display_seam.h"
#include "../window_presentation.h"
#include "keymap.h"
#include "platform_ui.h"
#include "version.h"
#include "../../dx/dx.h"
#include "../../dx/host_d9.h"
#include "../../runtime/loader.h"
#include "../../runtime/mods_seam.h"
#include "../../runtime/native_seam.h"
#include "../../runtime/win32.h"

#include <SDL3/SDL.h>
#include <SDL3/SDL_main.h> // on iOS this supplies the UIKit entry point and renames main
#ifdef __EMSCRIPTEN__
#include "../gpu/d3d9_backend.h"
#include <emscripten/emscripten.h>
#include <emscripten/wasmfs.h>
#include <thread>
#endif
#include <SDL3/SDL_vulkan.h>

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <atomic>
#include <chrono>
#include <map>
#include <mutex>
#include <set>
#include <string>
#include <thread>
#include <vector>

// Win32 messages this host delivers.
enum {
    WM_PAINT_ = 0x000f,
    WM_CLOSE_ = 0x0010,
    WM_ACTIVATE_ = 0x0006,
    WM_SETFOCUS_ = 0x0007,
    WM_KILLFOCUS_ = 0x0008,
    WM_ACTIVATEAPP_ = 0x001c,
    WM_KEYDOWN_ = 0x0100,
    WM_KEYUP_ = 0x0101,
    WM_CHAR_ = 0x0102,
    WM_SYSKEYDOWN_ = 0x0104,
    WM_SYSKEYUP_ = 0x0105,
    WM_MOUSEMOVE_ = 0x0200,
    WM_LBUTTONDOWN_ = 0x0201,
    WM_LBUTTONUP_ = 0x0202,
    WM_RBUTTONDOWN_ = 0x0204,
    WM_RBUTTONUP_ = 0x0205,
    WM_MBUTTONDOWN_ = 0x0207,
    WM_MBUTTONUP_ = 0x0208,
    WM_MOUSEWHEEL_ = 0x020a,
};

namespace {

SDL_Window *g_window = nullptr;
void *g_surface = nullptr; // the native surface the presenter draws into
std::unique_ptr<gpu::Device> g_gpu;
bool g_close_requested = false; // the user asked to close
bool g_guest_activated = false; // WM_ACTIVATEAPP(1) has been delivered
bool g_focused = false;
int g_mode_w = 640, g_mode_h = 480;
bool g_mode_dirty = false;
int g_window_mode = 0, g_wanted_window_mode = 0;
bool g_fullscreen_transition = false;
HostRect g_pointer_confinement; // window points; main thread only
bool g_pointer_sample_valid = false;
int32_t g_pointer_sample_x = 0, g_pointer_sample_y = 0;
int g_pointer_sample_w = 0, g_pointer_sample_h = 0;
bool g_borderless_frame_dirty = true;
int g_windowed_x = 0, g_windowed_y = 0, g_windowed_w = 0, g_windowed_h = 0;
uint32_t g_pending_mode_w = 0, g_pending_mode_h = 0;
uint8_t g_buttons = 0; // for the MK_ bits in a mouse message

uint32_t make_lparam(int32_t x, int32_t y) {
    return ((uint32_t)(y & 0xffff) << 16) | (uint32_t)(x & 0xffff);
}
// The wParam every mouse message carries: which buttons are down and whether
// Shift or Control is held. A game that reads it and finds Shift never set
// cannot tell a shift-click from a click.
int32_t g_cursor_x = 0, g_cursor_y = 0;

// The modifiers AS THE GUEST KNOWS THEM: a consumed Shift is not in here,
// so it cannot appear in a mouse message the guest does see.
uint32_t mouse_wparam() {
    return host_mouse_wparam(g_buttons, host_guest_modifiers());
}
void post(uint32_t msg, uint32_t wparam, uint32_t lparam) {
    // Mouse input is addressed to a point, not to a window: the runtime
    // hit-tests it, activates what it lands on, and converts the position into
    // that window's client coordinates - which is what Windows does and what
    // any windowed UI reads. Posting it to the main window instead delivered
    // every click to whichever window happened to be created first, carrying a
    // screen position the recipient read as its own client one.
    if (msg >= 0x0100 && msg <= 0x0109) {
        host_post_key_message(msg, wparam, lparam);
        return;
    }
    if (msg >= 0x200 && msg <= 0x209) {
        host_post_client_mouse_message(host_main_window(), msg, wparam, int16_t(lparam),
                                       int16_t(lparam >> 16));
        return;
    }
    uint32_t hwnd = host_main_window();
    if (hwnd)
        host_post_message(hwnd, msg, wparam, lparam);
}

uint32_t current_modifier_flags() {
    return host_modifier_flags_from_sdl(SDL_GetModState());
}

// The window for a guest mode on the screen this window is on or opening on,
// so a 640x480 mode is not a postage stamp on a 5K display and a 1920x1080
// mode does not open a window larger than a laptop's screen.
HostWindowSize window_size_for(int gw, int gh) {
    SDL_Rect visible = {0, 0, 0, 0};
    SDL_DisplayID display = g_window ? SDL_GetDisplayForWindow(g_window) : SDL_GetPrimaryDisplay();
    if (!SDL_GetDisplayUsableBounds(display, &visible))
        visible.w = visible.h = 0;
    const float density =
        g_window ? SDL_GetWindowPixelDensity(g_window) : SDL_GetDisplayContentScale(display);
    return host_window_size_for(gw, gh, visible.w, visible.h, density > 0 ? density : 1.0);
}

// Publish display pixels, not SDL window coordinates (points on Retina).
// Use the desktop mode even while fullscreen: a notch-safe window or a lower
// selected game mode must not hide the display's own resolution from menus.
// SDL video queries stay on the main thread; guest threads read only this cache.
std::atomic<int> g_screen_w{0}, g_screen_h{0};
// NFSMW run 35 (PR #10): the pixel rows a notched MacBook Pro keeps above a full-screen window (0 elsewhere); the
// mods' "your screen" size is the area below it, which a full-screen game fills 1:1.
std::atomic<int> g_screen_notch{0};
// NFSU1 run 8: the highest refresh of the screen the window is on, for the presenter's pacing (refresh_period):
// AppKit's maximumFramesPerSecond (120 on a ProMotion panel, which CoreGraphics reports as 0), else SDL's mode rate.
std::atomic<double> g_window_hz{0};
void note_window_refresh(SDL_DisplayID display) {
    double hz = 0;
#if defined(__APPLE__) && !TARGET_OS_IPHONE
    if (g_window)
        hz = os_window_max_fps(SDL_GetPointerProperty(SDL_GetWindowProperties(g_window),
                                                      SDL_PROP_WINDOW_COCOA_WINDOW_POINTER, nullptr));
#endif
    if (!(hz > 1))
        if (const SDL_DisplayMode *mode = SDL_GetDesktopDisplayMode(display))
            hz = mode->refresh_rate;
    if (hz > 1 && std::fabs(hz - g_window_hz.load()) > 0.5)
        fprintf(stderr, "[host] display refresh: %.0f Hz (the window's screen)\n", hz);
    if (hz > 1)
        g_window_hz.store(hz);
}

void note_screen_size() {
    int w = 0, h = 0;
    SDL_DisplayID display = g_window ? SDL_GetDisplayForWindow(g_window) : SDL_GetPrimaryDisplay();
    if (const SDL_DisplayMode *mode = SDL_GetDesktopDisplayMode(display)) {
        const double density = mode->pixel_density > 0 ? mode->pixel_density : 1.0;
        w = int(std::lround(mode->w * density));
        h = int(std::lround(mode->h * density));
    }
    if ((w <= 0 || h <= 0) && g_window && (SDL_GetWindowFlags(g_window) & SDL_WINDOW_FULLSCREEN)) {
        SDL_GetWindowSizeInPixels(g_window, &w, &h);
    }
    if (w <= 0 || h <= 0) {
        SDL_Rect bounds = {0, 0, 0, 0};
        if (SDL_GetDisplayBounds(display, &bounds)) {
            const double density = g_window ? SDL_GetWindowPixelDensity(g_window) : 1.0;
            w = int(std::lround(bounds.w * density));
            h = int(std::lround(bounds.h * density));
        }
    }
    note_window_refresh(display);
    if (w <= 0 || h <= 0)
        return;
    int notch = 0;
#if defined(__APPLE__) && !TARGET_OS_IPHONE
    notch = os_window_notch_pixels(
        g_window ? SDL_GetPointerProperty(SDL_GetWindowProperties(g_window), SDL_PROP_WINDOW_COCOA_WINDOW_POINTER, nullptr)
                 : nullptr);
#endif
    if (notch < 0 || notch >= h / 4)
        notch = 0;
    if (notch != g_screen_notch.load() || w != g_screen_w.load() || h != g_screen_h.load()) {
        if (notch)
            fprintf(stderr, "[host] display below the notch: %dx%d\n", w, h - notch);
    }
    g_screen_notch.store(notch);
    if (w != g_screen_w.load() || h != g_screen_h.load())
        fprintf(stderr, "[host] display pixel size: %dx%d\n", w, h);
    g_screen_w.store(w);
    g_screen_h.store(h);
}

// The window's size in points and in drawable pixels.
void window_sizes(int *bw, int *bh, int *dw, int *dh) {
    *bw = *bh = *dw = *dh = 0;
    if (!g_window)
        return;
    SDL_GetWindowSize(g_window, bw, bh);
    SDL_GetWindowSizeInPixels(g_window, dw, dh);
}

// The presenter's game rectangle for a drawable of dw x dh (present.h): in
// landscape always the whole drawable, as it has always been; in portrait the
// rectangle the presenter composes the game into, when it fits this drawable.
HostGameRect game_rect_for(int dw, int dh) {
    const HostGameRect whole = {0, 0, dw, dh};
    if (dh <= dw)
        return whole;
    const HostGameRect r = host_present_current_game_rect();
    if (r.w <= 0 || r.h <= 0 || r.x < 0 || r.y < 0 || r.x + r.w > dw || r.y + r.h > dh)
        return whole;
    return r;
}

// Decode window points into game-rectangle pixels only: drawable pixels, less
// the rectangle's origin, with the rectangle's size as the extent (the whole
// drawable in landscape). Layout selection, capture, drag ownership and guest
// motion are applied later under the guest baton.
void view_point_to_drawable(double px, double py, int32_t *out_x, int32_t *out_y, int *width,
                            int *height) {
    *out_x = 0;
    *out_y = 0;
    *width = 0;
    *height = 0;
    int bw, bh, dw, dh;
    window_sizes(&bw, &bh, &dw, &dh);
    if (bw <= 0 || bh <= 0)
        return;
    const HostGameRect rect = game_rect_for(dw, dh);
    int32_t x, y;
    if (!g_pointer_confinement.empty()) {
        x = host_confined_pointer_pixel(px, g_pointer_confinement.x, g_pointer_confinement.w, dw);
        y = host_confined_pointer_pixel(py, g_pointer_confinement.y, g_pointer_confinement.h, dh);
    } else {
        x = (int32_t)floor(px / bw * dw);
        y = (int32_t)floor(py / bh * dh);
    }
    host_present_point_to_game(rect, x, y, out_x, out_y);
    *width = rect.w;
    *height = rect.h;
}

// The game rectangle in window points, for the paths that work in points.
// `whole` says it is the entire window (landscape).
struct PointRect {
    double x, y, w, h;
    bool whole;
};
PointRect game_rect_points() {
    int bw, bh, dw, dh;
    window_sizes(&bw, &bh, &dw, &dh);
    const HostGameRect r = game_rect_for(dw, dh);
    if (bw <= 0 || dw <= 0 || (r.x == 0 && r.y == 0 && r.w == dw && r.h == dh))
        return {0, 0, double(bw), double(bh), true};
    const double scale = double(dw) / bw;
    return {r.x / scale, r.y / scale, r.w / scale, r.h / scale, false};
}

// ---------------------------------------------------------------------------
// Input is DECODED when the event arrives and APPLIED when the baton is ours.
//
// The runtime services this host from inside a scheduler idle slice, with its
// lock down and another guest thread free to hold the baton and run guest code
// for the whole slice. Applying input there would deliver to the guest, and
// dispatch mod callbacks, concurrently with that thread's hooks - callbacks
// that capture a view of guest memory and touch the settings, heap and event
// registries. So an event that arrives while parked is decoded into the queue
// below and applied from pump(), which runs on a thread that holds the baton.
// ---------------------------------------------------------------------------
struct PendingInput {
    enum Kind { MOTION, BUTTON, WHEEL, KEY, MODIFIERS, FOCUS, RELEASE_CAPTURE, PLACE } kind;
    int32_t x = 0, y = 0, dz = 0;
    double dx = 0, dy = 0; // Drawable deltas preserve subpixel motion in the queue.
    int drawable_w = 0, drawable_h = 0;
    int button = 0;
    bool down = false;
    bool inside = false, edge = false;
    uint16_t key = 0;
    uint32_t character = 0;
    uint32_t flags = 0;
};
std::vector<PendingInput> g_pending_input;
// The queue is FILLED on the host thread inside an idle slice, with the
// scheduler's lock down, and DRAINED by whichever guest thread holds the baton
// - the tick, or the scheduler itself when nothing else can run. Those are two
// different threads running at the same time, so the queue needs its own lock.
// It is held only across the push and the swap, never across an apply: a mod
// callback must not run with a host lock held.
std::mutex g_pending_input_m;

void apply_input(const PendingInput &e);
void apply_focus(bool focused, uint32_t modifier_flags);

// True when the event was held back rather than applied.
bool queue_or_apply(const PendingInput &e) {
#ifdef __EMSCRIPTEN__
    // The browser's main thread handles events; the game applies them.
    const bool queue = true;
#else
    const bool queue = sched_in_idle_slice();
#endif
    if (queue) {
        {
            std::lock_guard<std::mutex> held(g_pending_input_m);
            g_pending_input.push_back(e);
        }
        // Announced AFTER the input is in the queue, because a thread woken by
        // this goes straight to the emptiness check - and OUTSIDE the queue
        // lock, because the scheduler asks input_pending() while holding its
        // own mutex. Taking the two in the other order here would be a
        // lock-ordering inversion between them.
        sched_input_arrived();
        return true;
    }
    apply_input(e);
    return false;
}

// Called from pump(), which the guest reaches through its own clock read, so
// the calling thread holds the baton.
void deliver_pending_input() {
    std::vector<PendingInput> batch;
    {
        std::lock_guard<std::mutex> held(g_pending_input_m);
        if (g_pending_input.empty())
            return;
        batch.swap(g_pending_input);
    }
    // A press and its release must not be applied in the same guest turn, or a
    // guest that polls its buttons never sees the button down at all. The rule
    // is in input_gate.cpp, where it is tested without a window.
    std::vector<HostInputStep> steps;
    steps.reserve(batch.size());
    for (const PendingInput &e : batch)
        steps.push_back(
            {uint8_t(e.kind == PendingInput::BUTTON), uint8_t(e.button), uint8_t(e.down ? 1 : 0)});
    const uint32_t limit = host_input_batch_limit(steps.data(), uint32_t(steps.size()));
    // Applied outside the lock, in arrival order. Order is the whole point: a
    // focus loss sits in this queue among the presses it must follow, so
    // replaying a press after it cannot leave a key stuck down.
    for (uint32_t i = 0; i < limit; ++i)
        apply_input(batch[i]);
    if (limit >= batch.size())
        return;
    {
        std::lock_guard<std::mutex> held(g_pending_input_m);
        g_pending_input.insert(g_pending_input.begin(), batch.begin() + limit, batch.end());
    }
    // The deferred remainder is input the guest has not seen yet, so the
    // scheduler must come back for it rather than park until something else
    // arrives.
    sched_input_arrived();
}

// ---------------------------------------------------------------------------
// The platform half of pointer capture. The policy and the arithmetic are in
// input_gate.cpp, where they can be tested without a window; what is left here
// is what only a window can do: hide and confine the associated system cursor.
// Window positions remain the motion source in both capture states.
// ---------------------------------------------------------------------------
bool g_pointer_hidden = false;
// Test80 (owner, 1 Oct 2026: the arrow sat in the middle of the picture while racing with the controller):
// the OS cursor also hides itself while the pointer is idle - 2.5 s after the last real mouse or trackpad
// motion, or at once on controller input - and comes back on the next motion or click. Not while the
// settings page (which is navigated with a real cursor) is up. g_os_cursor_hidden is what SDL was told.
bool g_idle_hidden = false, g_os_cursor_hidden = false;
uint64_t g_pointer_active_ms = 0;
void reconcile_os_cursor() {
    const bool hide = g_pointer_hidden || g_idle_hidden;
    if (hide == g_os_cursor_hidden)
        return;
    if (hide)
        SDL_HideCursor();
    else
        SDL_ShowCursor();
#if defined(__APPLE__) && TARGET_OS_OSX
    ::os_cursor_set_hidden(hide);
#endif
    g_os_cursor_hidden = hide;
}
std::atomic<bool> g_escape_held{false};
std::atomic<bool> g_platform_capture_requested{false};
void update_platform_pointer_capture();

// Host-side state transitions, stamped on the frame-timings clock so they
// can be laid beside the presenter's acknowledgement trace.
static void trace_state(const char *what) {
    static const bool trace = recomp_env("TRACE_POINTER") != nullptr;
    if (trace)
        fprintf(stderr, "[state %.3f] %s\n", double(os_monotonic_ns()) / 1e9, what);
}
void apply_pointer_capture(bool want) {
    // No pointer to capture on a touch platform: fingers are placed absolutely.
    want = want && platform_ui_pointer_capture_supported();
    host_pointer_capture(want);
    // Queue draining can run on a guest worker. All native cursor/window work
    // belongs to the main thread, which observes this at the next pump.
    g_platform_capture_requested = want;
    if (SDL_IsMainThread())
        update_platform_pointer_capture();
}

void update_idle_cursor() {
    const bool idle = SDL_GetTicks() - g_pointer_active_ms > 2500;
    // Only once the game shows its own picture: over a launcher or settings form it draws no cursor of its own.
    // Test128: a Direct3D 9 game (NFSMW) never has a DirectDraw primary, so the arrow never hid; the GPU renderer
    // running (the device took the game's draws) counts as the game's picture too (RC5 launcher log, 2 Oct 2026).
    const bool primary = ddraw_gdi_primary_active() || host_d9_active() != 0, focus = g_window && (SDL_GetWindowFlags(g_window) & SDL_WINDOW_INPUT_FOCUS),
               page = mods_page_visible() || mods_controls_editing();
    const bool want = idle && primary && focus && !page && !g_close_requested;
    if (want != g_idle_hidden) {
        g_idle_hidden = want;
        reconcile_os_cursor();
        // Test128 (owner's RC4 session, 2 Oct 2026: the arrow stayed on screen): each change and why, once.
        printf("[host] cursor: %s (idle %d, game picture %d, window focus %d, settings page %d)\n",
               want ? "hidden" : "shown", int(idle), int(primary), int(focus), int(page));
        fflush(stdout);
    } else if (!want && idle) {
        // Idle but kept visible: say why at most once a minute.
        static uint64_t last_ms = 0;
        const uint64_t now = SDL_GetTicks();
        if (!last_ms || now - last_ms > 60000) {
            last_ms = now;
            printf("[host] cursor: idle but shown (game picture %d, window focus %d, settings page %d, closing %d)\n",
                   int(primary), int(focus), int(page), int(g_close_requested));
            fflush(stdout);
        }
    }
}

bool window_minimized_or_hidden() {
    const SDL_WindowFlags flags = g_window ? SDL_GetWindowFlags(g_window) : 0;
    return (flags & (SDL_WINDOW_MINIMIZED | SDL_WINDOW_HIDDEN)) != 0;
}
bool window_focused() {
    return g_window && (SDL_GetWindowFlags(g_window) & SDL_WINDOW_INPUT_FOCUS) != 0;
}
bool window_fullscreen() {
    return g_window && (SDL_GetWindowFlags(g_window) & SDL_WINDOW_FULLSCREEN) != 0;
}

// Capture is for a window that is focused, showing, and not displaying the mod
// settings page - the page is navigated with a real cursor. It is not tied to
// what the guest is doing: an FMV reads the mouse exactly as gameplay does.
bool pointer_capture_wanted() {
    if (!platform_ui_pointer_capture_supported())
        return false;
    // Nothing to stand in for the system pointer until the guest has a display
    // surface of its own: while it is still showing plain windows - a launcher,
    // a settings form - it draws no cursor, so hiding the host one would leave
    // the player with nothing to aim.
    if (!ddraw_gdi_primary_active())
        return false;
    if (!g_focused || !g_window || !window_focused() || g_escape_held)
        return false;
    if (window_minimized_or_hidden())
        return false;
    if (mods_page_visible() || mods_controls_editing())
        return false;
    return true;
}

void update_platform_pointer_capture() {
    const bool want = g_platform_capture_requested && pointer_capture_wanted() &&
                      !g_close_requested && !g_fullscreen_transition;
    if (want && !g_pointer_hidden) {
        trace_state("capture ON: hiding the OS cursor");
        g_pointer_hidden = true;
        reconcile_os_cursor();
    }
    if (!want && g_pointer_hidden) {
        trace_state("capture OFF: showing the OS cursor");
        g_pointer_hidden = false;
        reconcile_os_cursor();
    }

    // A regular window confines too; hiding alone lets the OS pointer leave
    // the frame. Dragging into the resize margin releases capture so the
    // window's resize edges remain reachable.
    int bw, bh, dw, dh;
    window_sizes(&bw, &bh, &dw, &dh);
    HostRect rect = host_pointer_confinement_wanted(want, g_window_mode) && g_window
                        ? host_pointer_confinement_rect({0, 0, double(bw), double(bh)})
                        : HostRect{};
    if (g_window && (rect.x != g_pointer_confinement.x || rect.y != g_pointer_confinement.y ||
                     rect.w != g_pointer_confinement.w || rect.h != g_pointer_confinement.h)) {
        if (rect.empty())
            SDL_SetWindowMouseRect(g_window, nullptr);
        else {
            SDL_Rect clip{int(rect.x), int(rect.y), int(rect.w), int(rect.h)};
            SDL_SetWindowMouseRect(g_window, &clip);
        }
        fprintf(stderr, "[host] pointer confinement: %.1f,%.1f %.1fx%.1f points (window mode %d)\n",
                rect.x, rect.y, rect.w, rect.h, g_window_mode);
        g_pointer_confinement = rect;
        g_pointer_sample_valid = false;
    }
}

bool window_has_resize_edges() {
    return g_window_mode == 0 && !g_fullscreen_transition && !window_fullscreen();
}

bool app_script_due();
bool input_pending() {
    if (app_script_due()) // Remaster U4: a due script step ends an idle wait like input does
        return true;
    std::lock_guard<std::mutex> held(g_pending_input_m);
    return !g_pending_input.empty();
}
void drain_input() {
    deliver_pending_input();
}

void apply_motion(int32_t x, int32_t y, double drawable_dx, double drawable_dy) {
    if (host_gate_menu_pointer(x, y, -1, false, 0)) {
        int32_t gx, gy; // still where the pointer is, for when the menu closes
        host_gate_window_pointer(x, y, &gx, &gy);
        return;
    }
    HitResult hit;
    const bool delivered = host_gate_window_motion(x, y, drawable_dx, drawable_dy, &hit);
    static const bool trace = recomp_env("TRACE_POINTER") != nullptr;
    static double last_trace = 0;
    const double now = (double(os_monotonic_ns()) / 1e9);
    if (trace && now - last_trace >= 0.1) {
        last_trace = now;
        const auto guest =
            host_guest_pointer_resolve(g_mem, GUEST_SIZE, RECOMP_HOOK_MOUSE_DEVICE_PTR);
        fprintf(stderr,
                "[pointer-game] drawable %d,%d hit %d at %d,%d delivered %d guest %d,%d bounds "
                "%d,%d,%d,%d\n",
                x, y, int(hit.kind), hit.gx, hit.gy, delivered, guest.x, guest.y, guest.left,
                guest.top, guest.right, guest.bottom);
    }
    if (host_pointer_captured() && g_buttons && g_window && window_has_resize_edges()) {
        double px, py;
        host_pointer_drawable_position(&px, &py);
        int bw, bh, dw, dh;
        window_sizes(&bw, &bh, &dw, &dh);
        const double margin = 8 * (bw > 0 ? double(dw) / bw : 1.0);
        // The pointer is in game-rectangle pixels.
        const HostGameRect game = game_rect_for(dw, dh);
        if (host_pointer_at_resize_edge(px, py, game.w, game.h, margin))
            apply_pointer_capture(false);
    }
    {
        static int last_kind = -99;
        static bool last_delivered = false;
        if (int(hit.kind) != last_kind || delivered != last_delivered) {
            char what[96];
            snprintf(what, sizeof what, "hit kind %d -> %d, delivered %d -> %d", last_kind,
                     int(hit.kind), int(last_delivered), int(delivered));
            trace_state(what);
            last_kind = int(hit.kind);
            last_delivered = delivered;
        }
    }
    if (!delivered)
        return;
    x = hit.gx;
    y = hit.gy;
    g_cursor_x = x;
    g_cursor_y = y;
    post(WM_MOUSEMOVE_, mouse_wparam(), make_lparam(x, y));
}

// Where the pointer is right now, in window points.
bool pointer_in_window_points(double *px, double *py) {
    float gx, gy;
    SDL_GetGlobalMouseState(&gx, &gy);
    int wx, wy;
    if (!g_window || !SDL_GetWindowPosition(g_window, &wx, &wy))
        return false;
    *px = gx - wx;
    *py = gy - wy;
    return true;
}

void sample_captured_pointer() {
    if (g_pointer_confinement.empty())
        return;
    double px, py;
    if (!pointer_in_window_points(&px, &py))
        return;
    PendingInput e;
    e.kind = PendingInput::MOTION;
    view_point_to_drawable(px, py, &e.x, &e.y, &e.drawable_w, &e.drawable_h);
    if (g_pointer_sample_valid && e.x == g_pointer_sample_x && e.y == g_pointer_sample_y &&
        e.drawable_w == g_pointer_sample_w && e.drawable_h == g_pointer_sample_h)
        return;
    g_pointer_sample_valid = true;
    g_pointer_sample_x = e.x;
    g_pointer_sample_y = e.y;
    g_pointer_sample_w = e.drawable_w;
    g_pointer_sample_h = e.drawable_h;
    queue_or_apply(e);
}

// The strips the system keeps along the window's edges (a status bar), in
// points: a hardware pointer stops at their inner side.
void system_strip_insets(double *top, double *bottom) {
    *top = *bottom = 0;
    int w = 0, h = 0;
    SDL_Rect safe{};
    if (!g_window || !SDL_GetWindowSafeArea(g_window, &safe))
        return;
    SDL_GetWindowSize(g_window, &w, &h);
    *top = safe.y > 0 ? safe.y : 0;
    const int below = h - (safe.y + safe.h);
    *bottom = below > 0 ? below : 0;
}

void handle_mouse_move(const SDL_MouseMotionEvent &motion) {
    // A real pointer move (not one the touch path or the binding itself
    // synthesized, which both arrive as SDL_TOUCH_MOUSEID): feed the mapped
    // binding's Cursor stick mode. A touch's own placement reaches it through
    // the kTouchPlaceEvent handler below instead, in window points.
    if (motion.which != SDL_TOUCH_MOUSEID)
        controls::host_pointer_moved(motion.x, motion.y);
    PendingInput e;
    e.kind = PendingInput::MOTION;
    // A pointer resting against a system strip means the edge behind it.
    double strip_top = 0, strip_bottom = 0;
    system_strip_insets(&strip_top, &strip_bottom);
    static PointerStripLatch strip_latch;
    // Only a strip the game image reaches: in portrait the image starts below it.
    const PointRect game = game_rect_points();
    const double strip = game.whole ? strip_top : std::max(0.0, strip_top - game.y);
    const float y = (float)strip_latch.apply(motion.y, strip);
    view_point_to_drawable(motion.x, y, &e.x, &e.y, &e.drawable_w, &e.drawable_h);
    static const bool trace = recomp_env("TRACE_POINTER") != nullptr;
    static double last_trace = 0;
    const double now = (double(os_monotonic_ns()) / 1e9);
    // Every event along the top strip, where an edge scroll is decided; one in
    // ten elsewhere.
    if (trace && (now - last_trace >= 0.1 || motion.y < strip_top + 32)) {
        last_trace = now;
        fprintf(stderr,
                "[pointer] event %.1f,%.1f drawable %d,%d/%d,%d clip %d delta %.1f,%.1f which %u\n",
                motion.x, motion.y, e.x, e.y, e.drawable_w, e.drawable_h,
                !g_pointer_confinement.empty(), motion.xrel, motion.yrel, (unsigned)motion.which);
    }
    if (!g_pointer_confinement.empty())
        sample_captured_pointer();
    else
        queue_or_apply(e);
}

void apply_button(int button, bool down, int32_t x, int32_t y, bool inside, bool edge) {
    if (down && (!inside || edge)) {
        apply_pointer_capture(false);
        return;
    }
    if (host_gate_menu_pointer(x, y, button, down, 0))
        return;
    // A click inside the window is how the pointer is taken back after it was
    // released - by focus loss, or by the settings page closing.
    int32_t dx, dy;
    auto hit = host_gate_window_pointer(x, y, &dx, &dy);
    if (hit.kind == HitResult::HIT_NONE) {
        if (down)
            return;
        // An outside release must still release a guest button held during
        // an edge drag, even though the outside position has no hit owner.
        hit.gx = g_cursor_x;
        hit.gy = g_cursor_y;
    }
    if (!host_pointer_captured() &&
        host_pointer_can_capture(pointer_capture_wanted(), inside, down, g_escape_held,
                                 mods_page_visible() || mods_controls_editing()))
        apply_pointer_capture(true);
    x = hit.gx;
    y = hit.gy;
    if (!down && !(g_buttons & ~(1u << button)))
        host_gate_end_drag();
    static const bool trace_buttons = recomp_env("TRACE_POINTER") != nullptr;
    const bool consumed = host_gate_button(button, down, x, y);
    if (trace_buttons)
        fprintf(stderr, "[pointer-button] button %d %s at %d,%d consumed %d captured %d\n", button,
                down ? "down" : "up", x, y, int(consumed), int(host_pointer_captured()));
    if (consumed)
        return;
    if (down)
        host_gate_begin_drag(&hit);
    g_cursor_x = x;
    g_cursor_y = y;
    // Absolute placement alone does not move Populous's integrated cursor.
    host_input_motion(x, y, dx, dy);
    post(WM_MOUSEMOVE_, mouse_wparam(), make_lparam(x, y));
    if (down)
        g_buttons |= (uint8_t)(1u << button);
    else
        g_buttons &= (uint8_t)~(1u << button);
    host_input_button(button, down);
    static const uint32_t msgs[3][2] = {
        {WM_LBUTTONUP_, WM_LBUTTONDOWN_},
        {WM_RBUTTONUP_, WM_RBUTTONDOWN_},
        {WM_MBUTTONUP_, WM_MBUTTONDOWN_},
    };
    if (button < 3)
        post(msgs[button][down ? 1 : 0], mouse_wparam(), make_lparam(x, y));
}

void handle_button(const SDL_MouseButtonEvent &event, int button, bool down) {
    PendingInput e;
    e.kind = PendingInput::BUTTON;
    e.button = button;
    e.down = down;
    int bw, bh, dw, dh;
    window_sizes(&bw, &bh, &dw, &dh);
    e.inside = event.x >= 0 && event.y >= 0 && event.x < bw && event.y < bh;
    e.edge = window_has_resize_edges() && host_pointer_at_resize_edge(event.x, event.y, bw, bh, 8);
    view_point_to_drawable(event.x, event.y, &e.x, &e.y, &e.drawable_w, &e.drawable_h);
    queue_or_apply(e);
}

// The key paths live in input_gate.cpp, which owns the decision, the delivery
// and the two kinds of key state they need. What is left here is the SDL
// event decoding.
void apply_wheel(int32_t dz, int32_t x, int32_t y) {
    if (host_gate_menu_pointer(x, y, -1, false, dz))
        return;
    if (host_gate_wheel(dz))
        return;
    int32_t dx, dy;
    auto hit = host_gate_window_pointer(x, y, &dx, &dy);
    if (hit.kind == HitResult::HIT_NONE)
        return;
    x = hit.gx;
    y = hit.gy;
    host_input_motion(x, y, dx, dy);
    host_input_wheel(dz);
    post(WM_MOUSEWHEEL_, ((uint32_t)dz << 16) | mouse_wparam(), make_lparam(x, y));
}

void apply_key(uint16_t key, bool down, uint32_t character, uint32_t flags) {
    // Every key event carries the modifier flags, and this is the only place
    // a modifier held across a focus change can be noticed. A diff, so it
    // emits nothing when the state already agrees.
    host_gate_sync_modifiers(flags);
    host_key_event(key, down, character);
}

// A modifier key is an ordinary key event to SDL; the host diffs modifier
// state as a whole, so it arrives as the flags after the change.
bool is_modifier_scancode(SDL_Scancode sc) {
    return sc == SDL_SCANCODE_LSHIFT || sc == SDL_SCANCODE_RSHIFT || sc == SDL_SCANCODE_LCTRL ||
           sc == SDL_SCANCODE_RCTRL || sc == SDL_SCANCODE_LALT || sc == SDL_SCANCODE_RALT ||
           sc == SDL_SCANCODE_LGUI || sc == SDL_SCANCODE_RGUI || sc == SDL_SCANCODE_CAPSLOCK;
}

void handle_key(const SDL_KeyboardEvent &event, bool down) {
    // Opt-in diagnostics use the same complete-frame probe as the smoke host.
    // SDL input runs on the guest thread, so the request is ordered with draws.
    // Keep the normal F8 binding untouched outside a diagnostic launch.
    if (event.scancode == SDL_SCANCODE_F8 && recomp_env("D3D9_PROBE_HOTKEY")) {
        if (down && !event.repeat) {
            static unsigned capture = 0;
            char tag[48];
            snprintf(tag, sizeof tag, "interactive-%03u", ++capture);
            fprintf(stderr, "[host] capturing graphics diagnostic %s\n", tag);
            host_d9_probe_next_frame(tag);
        }
        return;
    }
    if (is_modifier_scancode(event.scancode)) {
        PendingInput e;
        e.kind = PendingInput::MODIFIERS;
        e.flags = host_modifier_flags_from_sdl(event.mod);
        queue_or_apply(e);
        return;
    }
    if (event.scancode == SDL_SCANCODE_ESCAPE) {
        g_escape_held = down;
        if (down) {
            // Release before queuing guest input: it must work even while the
            // scheduler cannot grant the guest baton.
            g_platform_capture_requested = false;
            update_platform_pointer_capture();
            PendingInput release;
            release.kind = PendingInput::RELEASE_CAPTURE;
            queue_or_apply(release);
        }
    }
    const uint16_t key = host_keycode_from_scancode(event.scancode);
    if (key == 0xffff)
        return;
    PendingInput e;
    e.kind = PendingInput::KEY;
    e.key = key;
    e.down = down;
    // The character the key produces with no modifier held, which is what a
    // WM_CHAR for it carries; keycodes above the Unicode range are not characters.
    const SDL_Keycode plain = SDL_GetKeyFromScancode(event.scancode, SDL_KMOD_NONE, false);
    e.character = plain < 0x40000000 && plain >= 0x20 ? (uint32_t)plain : 0u;
    e.flags = host_modifier_flags_from_sdl(event.mod);
    queue_or_apply(e);
}

// Called from the event pump, including inside an idle slice. It decides that
// the focus changed and records WHEN, relative to the input around it; nothing
// guest-visible happens here.
void note_focus(bool focused) {
    if (focused == g_focused && g_guest_activated)
        return;
    g_focused = focused;
    g_guest_activated = true;
    PendingInput e;
    e.kind = PendingInput::FOCUS;
    e.down = focused;
    // Read now, not at apply time: what is held at the moment focus returns is
    // what the guest missed, and by the time the queue drains it may differ.
    e.flags = focused ? current_modifier_flags() : 0u;
    queue_or_apply(e);
}

void apply_input(const PendingInput &e) {
    if (e.kind == PendingInput::MOTION || e.kind == PendingInput::BUTTON ||
        e.kind == PendingInput::WHEEL)
        host_gate_fallback_layout(e.drawable_w, e.drawable_h);
    switch (e.kind) {
    case PendingInput::MOTION:
        apply_motion(e.x, e.y, e.dx, e.dy);
        break;
    case PendingInput::BUTTON:
        apply_button(e.button, e.down, e.x, e.y, e.inside, e.edge);
        break;
    case PendingInput::WHEEL:
        apply_wheel(e.dz, e.x, e.y);
        break;
    case PendingInput::KEY:
        apply_key(e.key, e.down, e.character, e.flags);
        break;
    case PendingInput::MODIFIERS:
        host_modifier_event(e.flags);
        break;
    case PendingInput::FOCUS:
        apply_focus(e.down, e.flags);
        break;
    case PendingInput::RELEASE_CAPTURE:
        apply_pointer_capture(false);
        break;
    case PendingInput::PLACE:
        host_gate_pointer_place(e.x, e.y);
        break;
    }
}

// The whole of what a focus change does to the guest, including the two paths
// that reach a mod callback: the modifier resync on gain and the release on
// loss. Runs under the baton, from the queue, in arrival order with the key
// and button events around it - which is the point.
void apply_focus(bool focused, uint32_t modifier_flags) {
    if (!host_main_window())
        return; // the guest has no window yet
    recomp_media_focus(focused ? 1 : 0);
    post(WM_ACTIVATEAPP_, focused ? 1 : 0, 0);
    post(WM_ACTIVATE_, focused ? 1 : 0, 0);
    post(focused ? WM_SETFOCUS_ : WM_KILLFOCUS_, 0, 0);
    if (focused)
        post(WM_PAINT_, 0, 0);
    // Whatever is held right now became held while another application had
    // the focus, so no event announced it and the gate's state says it is up.
    if (focused)
        host_gate_sync_modifiers(modifier_flags);
    if (!focused) {
        // The pointer goes back to the user BEFORE the state is cleared:
        // host_gate_release_all drops the capture flag, and the platform
        // effects are keyed off that flag.
        apply_pointer_capture(false);
        // Nothing that was down can be seen coming up while another
        // application has the focus, so it all comes up now - including the
        // host's own copy of the button and key state.
        host_gate_release_all();
        g_buttons = 0;
    }
}

void post_drawable_size();

TouchMapper g_touch;

// A touch names a place. The motion event moves the host's idea of the
// pointer; the PLACE event that follows writes the game's own cursor there
// (host_gate_pointer_place) so the click after it hit-tests where the finger
// is, with no convergence to wait for. All three travel the SDL queue in order.
constexpr Sint32 kTouchPlaceEvent = 0x70756c63; // 'pulc'
void push_touch_action_now(const TouchAction &a);

void push_touch_actions(const std::vector<TouchAction> &actions) {
    for (const TouchAction &a : actions)
        push_touch_action_now(a);
}

void push_touch_action_now(const TouchAction &a) {
    const SDL_WindowID ours = g_window ? SDL_GetWindowID(g_window) : 0;
    {
        SDL_Event e{};
        e.common.timestamp = SDL_GetTicksNS();
        switch (a.kind) {
        case TouchAction::Motion: {
            e.type = SDL_EVENT_MOUSE_MOTION;
            e.motion.windowID = ours;
            e.motion.which = SDL_TOUCH_MOUSEID;
            e.motion.x = (float)a.x;
            e.motion.y = (float)a.y;
            SDL_PushEvent(&e);
            if (!a.place)
                return;
            SDL_Event place{};
            place.type = SDL_EVENT_USER;
            place.user.windowID = ours;
            place.user.code = kTouchPlaceEvent;
            place.user.data1 = (void *)(intptr_t)lround(a.x * 16);
            place.user.data2 = (void *)(intptr_t)lround(a.y * 16);
            SDL_PushEvent(&place);
            return;
        }
        case TouchAction::Button:
            e.type = a.down ? SDL_EVENT_MOUSE_BUTTON_DOWN : SDL_EVENT_MOUSE_BUTTON_UP;
            e.button.windowID = ours;
            e.button.which = SDL_TOUCH_MOUSEID;
            e.button.button = a.button == 1   ? SDL_BUTTON_RIGHT
                              : a.button == 2 ? SDL_BUTTON_MIDDLE
                                              : SDL_BUTTON_LEFT;
            e.button.down = a.down;
            e.button.clicks = 1;
            e.button.x = (float)a.x;
            e.button.y = (float)a.y;
            break;
        case TouchAction::Key:
            e.type = a.down ? SDL_EVENT_KEY_DOWN : SDL_EVENT_KEY_UP;
            e.key.windowID = ours;
            e.key.scancode = (SDL_Scancode)a.scancode;
            e.key.key = SDL_GetKeyFromScancode((SDL_Scancode)a.scancode, SDL_KMOD_NONE, false);
            e.key.down = a.down;
            break;
        case TouchAction::Wheel:
            e.type = SDL_EVENT_MOUSE_WHEEL;
            e.wheel.windowID = ours;
            e.wheel.mouse_x = (float)a.x;
            e.wheel.mouse_y = (float)a.y;
            e.wheel.y = (float)a.wheel;
            // handle_event() flips a FLIPPED wheel's sign; NORMAL passes a.wheel through as-is.
            e.wheel.direction = SDL_MOUSEWHEEL_NORMAL;
            break;
        }
        SDL_PushEvent(&e);
    }
}

void push_touch_key(int scancode, bool down) {
    TouchAction a;
    a.kind = TouchAction::Key;
    a.scancode = scancode;
    a.down = down;
    push_touch_action_now(a);
}

// The on-screen controls' hooks.
void toggle_system_keyboard() {
    if (!g_window)
        return;
    if (SDL_TextInputActive(g_window))
        SDL_StopTextInput(g_window);
    else
        SDL_StartTextInput(g_window);
}
void open_settings_page() {
    (void)mods_page_open(nullptr);
}

// Focus loss or backgrounding: every finger is gone, every key and modifier up.
void touch_release_all() {
    std::vector<TouchAction> actions;
    g_touch.cancel_all(&actions);
    push_touch_actions(actions);
    controls::host_release_all();
}

// A finger's position in drawable pixels, where the controls are laid out.
void finger_to_drawable(const SDL_TouchFingerEvent &f, double *px, double *py) {
    int bw, bh, dw, dh;
    window_sizes(&bw, &bh, &dw, &dh);
    *px = f.x * dw;
    *py = f.y * dh;
}

TouchPoint touch_point(const SDL_TouchFingerEvent &f) {
    int w = 0, h = 0;
    if (g_window)
        SDL_GetWindowSize(g_window, &w, &h);
    // The gesture mapper's edges are the game image's (portrait: the image's
    // rectangle inside the window).
    const PointRect game = game_rect_points();
    if (game.whole) {
        g_touch.set_bounds(w, h);
        g_touch.set_origin(0, 0);
    } else {
        g_touch.set_bounds(game.w, game.h);
        g_touch.set_origin(game.x, game.y);
    }
    // The strips the system keeps (a status bar, a gesture zone) never deliver
    // a finger, so a finger "on" that edge arrives at the strip's inner side.
    // Only the part of a strip that overlaps the game image counts.
    SDL_Rect safe{0, 0, w, h};
    if (g_window && SDL_GetWindowSafeArea(g_window, &safe)) {
        double l = safe.x, t = safe.y;
        double r = w - (safe.x + safe.w), b = h - (safe.y + safe.h);
        if (!game.whole) {
            l = safe.x - game.x;
            t = safe.y - game.y;
            r = (game.x + game.w) - (safe.x + safe.w);
            b = (game.y + game.h) - (safe.y + safe.h);
        }
        g_touch.set_edge_insets(l > 0 ? l : 0, t > 0 ? t : 0, r > 0 ? r : 0, b > 0 ? b : 0);
        static bool logged = false;
        if (!logged) {
            logged = true;
            fprintf(stderr, "[touch] window %dx%d points, safe area insets %g,%g,%g,%g\n", w, h, l,
                    t, r, b);
        }
    }
    return {(int64_t)f.fingerID, f.x * w, f.y * h};
}

// A window point in drawable pixels, where the on-screen controls and the
// editor are laid out (unlike view_point_to_drawable, which lands in the
// game image's own coordinates).
void window_point_to_drawable(double px, double py, double *dx, double *dy) {
    int bw, bh, dw, dh;
    window_sizes(&bw, &bh, &dw, &dh);
    *dx = bw > 0 ? px / bw * dw : 0;
    *dy = bh > 0 ? py / bh * dh : 0;
}

// While the layout editor is open it owns the keyboard, the mouse and the
// wheel: nothing here reaches the game, so the simulation sees frozen input
// for as long as the editor is up (the F10 page freezes it the same way, by
// consuming keys). Fingers go to the editor through host_finger_*, which the
// normal path already calls first. True: the event is spent.
bool handle_editor_event(const SDL_Event &event) {
    if (!controls::host_editing())
        return false;
    switch (event.type) {
    case SDL_EVENT_MOUSE_MOTION: {
        double dx, dy;
        window_point_to_drawable(event.motion.x, event.motion.y, &dx, &dy);
        controls::host_editor_pointer(dx, dy, 0);
        return true;
    }
    case SDL_EVENT_MOUSE_BUTTON_DOWN:
    case SDL_EVENT_MOUSE_BUTTON_UP: {
        if (event.button.button != SDL_BUTTON_LEFT)
            return true;
        double dx, dy;
        window_point_to_drawable(event.button.x, event.button.y, &dx, &dy);
        controls::host_editor_pointer(dx, dy, event.type == SDL_EVENT_MOUSE_BUTTON_DOWN ? 1 : -1);
        return true;
    }
    case SDL_EVENT_MOUSE_WHEEL:
        controls::host_editor_wheel(
            event.wheel.direction == SDL_MOUSEWHEEL_FLIPPED ? -event.wheel.y : event.wheel.y);
        return true;
    case SDL_EVENT_TEXT_INPUT:
        controls::host_editor_text(event.text.text);
        return true;
    case SDL_EVENT_KEY_UP:
        // No key-up is ever swallowed, Escape's included: a key the player was
        // holding when the editor opened has to be released to the game, and
        // Escape also has host state behind it (g_escape_held, the pointer
        // capture) that only its key-up clears. The editor takes key presses
        // alone, so an Escape release with no press it knows about is the one
        // the game and the host both still need.
        return false;
    case SDL_EVENT_KEY_DOWN:
        if (event.key.scancode == SDL_SCANCODE_ESCAPE)
            controls::host_editor_escape(); // Escape is Done
        else if (event.key.scancode == SDL_SCANCODE_RETURN ||
                 event.key.scancode == SDL_SCANCODE_KP_ENTER)
            controls::host_editor_text_done();
        return true;
    default:
        return false;
    }
}

// SDL's text input follows the editor's rename prompt.
void update_editor_text_input() {
    static bool on = false;
    const bool want = controls::host_editor_text_wanted();
    if (want == on || !g_window)
        return;
    on = want;
    if (want)
        SDL_StartTextInput(g_window);
    else
        SDL_StopTextInput(g_window);
}

// Test128 (owner's RC3 and Development 61 sessions, 2 Oct 2026): when the Mac sleeps, the TV's HDMI audio device
// disappears and comes back on wake as a new device (#15 removed, #39 'LG TV' added). The output - the Dolby Digital
// bitstream or PCM - stayed on the vanished device, so the game was silent until it was restarted, and the game's
// streaming sound kept restarting its buffer (thousands of times). The playback device that was the default when it
// disappeared is remembered by name; when a device of that name comes back, the output reopens once the link has
// settled (the same reopen as after an HDR switch: host_audio_restart_output). Other devices (a controller's
// speaker) coming and going change nothing.
namespace {
std::map<SDL_AudioDeviceID, std::string> g_audio_device_names;
std::string g_audio_default_name, g_audio_lost_name;
std::string audio_device_name(SDL_AudioDeviceID id) {
    const char *n = SDL_GetAudioDeviceName(id);
    return n ? n : "";
}
void note_audio_device_event(uint32_t type, SDL_AudioDeviceID which) {
    if (type == SDL_EVENT_AUDIO_DEVICE_REMOVED) {
        auto it = g_audio_device_names.find(which);
        if (it != g_audio_device_names.end()) {
            if (!it->second.empty() && it->second == g_audio_default_name) {
                g_audio_lost_name = it->second;
                printf("[host] audio: the output device '%s' went away; the output reopens when it is back\n",
                       g_audio_lost_name.c_str());
                fflush(stdout);
            }
            g_audio_device_names.erase(it);
        }
        return;
    }
    const std::string name = audio_device_name(which);
    if (!name.empty())
        g_audio_device_names[which] = name;
    if (type == SDL_EVENT_AUDIO_DEVICE_ADDED && !g_audio_lost_name.empty() && name == g_audio_lost_name) {
        printf("[host] audio: the output device '%s' is back (#%u); the output reopens once it is the system "
               "output again\n", name.c_str(), unsigned(which));
        fflush(stdout);
        g_audio_lost_name.clear();
        // Review (2 Oct): no resume across the loss and no fixed delay alone - the reopen builds a fresh session
        // (the Dolby sink resolves the default device, its streams, the AC-3 format and hog mode anew, and a new
        // IOProc). It waits until macOS has made the returned device the system output again (on wake it briefly
        // shows the built-in speakers), checked every 250 ms for at most 15 s, then 1 s for the HDMI link.
        std::thread([name] {
            bool back = false;
            for (int i = 0; i < 60 && !back; ++i) {
                std::this_thread::sleep_for(std::chrono::milliseconds(250));
                back = audio_device_name(SDL_AUDIO_DEVICE_DEFAULT_PLAYBACK) == name;
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(1000));
            if (!back)
                printf("[host] audio: '%s' is not the system output after 15 s; reopening on the current output\n",
                       name.c_str());
            host_audio_restart_output("the output device came back (sleep/wake or the TV reconnected)");
        }).detach();
        // The returned device is the output from here on. Not the momentary system default: on wake that is the
        // built-in speakers, and remembering it lost the second sleep (owner's RC4 session, 2 Oct 2026).
        g_audio_default_name = name;
        return;
    }
    const std::string def = audio_device_name(SDL_AUDIO_DEVICE_DEFAULT_PLAYBACK);
    if (!def.empty() && g_audio_lost_name.empty())
        g_audio_default_name = def;
}
// Test135 (the review's RC5 follow-up): when the macOS audio service (coreaudiod) restarts, every device, stream, hog
// claim and IOProc the process held is gone, like after a sleep. The same fresh reopen, after 2 s for the service to
// enumerate the devices again. The listener runs on a CoreAudio thread; the work runs on its own thread.
void audio_service_restarted() {
    std::thread([] {
        printf("[host] audio: the macOS audio service restarted; the output reopens\n");
        fflush(stdout);
        std::this_thread::sleep_for(std::chrono::milliseconds(2000));
        host_audio_restart_output("the macOS audio service restarted");
    }).detach();
}
} // namespace


// One SDL event, translated into both of the input paths the game reads: the
// DirectInput device state, and the Win32 message queue.
void handle_event(const SDL_Event &event) {
    if (platform_ui_handle_lifecycle(event))
        return;
    if (handle_editor_event(event))
        return;
    const SDL_WindowID ours = g_window ? SDL_GetWindowID(g_window) : 0;
    switch (event.type) {
    case SDL_EVENT_MOUSE_MOTION:
        if (event.motion.xrel != 0.0f || event.motion.yrel != 0.0f)
            g_pointer_active_ms = SDL_GetTicks();
        if (event.motion.windowID == ours || !g_pointer_confinement.empty())
            handle_mouse_move(event.motion);
        break;
    case SDL_EVENT_WINDOW_MOUSE_ENTER:
    case SDL_EVENT_WINDOW_MOUSE_LEAVE: {
        static const bool trace = recomp_env("TRACE_POINTER") != nullptr;
        if (trace)
            trace_state(event.type == SDL_EVENT_WINDOW_MOUSE_ENTER ? "pointer entered the window"
                                                                   : "pointer left the window");
        break;
    }
    case SDL_EVENT_WINDOW_OCCLUDED:
        trace_state("window OCCLUDED");
        break;
    case SDL_EVENT_WINDOW_EXPOSED:
        trace_state("window exposed");
        break;
    case SDL_EVENT_WINDOW_HIDDEN:
        trace_state("window HIDDEN");
        break;
    case SDL_EVENT_WINDOW_SHOWN:
        trace_state("window shown");
        break;
    case SDL_EVENT_WINDOW_MINIMIZED:
        trace_state("window MINIMIZED");
        break;
    case SDL_EVENT_WINDOW_RESTORED:
        trace_state("window restored");
        break;
    case SDL_EVENT_MOUSE_BUTTON_DOWN:
    case SDL_EVENT_MOUSE_BUTTON_UP:
        g_pointer_active_ms = SDL_GetTicks();
        if (event.button.windowID == ours) {
            const int button = event.button.button == SDL_BUTTON_LEFT     ? 0
                               : event.button.button == SDL_BUTTON_RIGHT  ? 1
                               : event.button.button == SDL_BUTTON_MIDDLE ? 2
                                                                          : -1;
            if (button >= 0)
                handle_button(event.button, button, event.type == SDL_EVENT_MOUSE_BUTTON_DOWN);
        }
        break;
    case SDL_EVENT_MOUSE_WHEEL:
        if (event.wheel.windowID == ours) {
            PendingInput e;
            e.kind = PendingInput::WHEEL;
            const double steps =
                event.wheel.direction == SDL_MOUSEWHEEL_FLIPPED ? -event.wheel.y : event.wheel.y;
            e.dz = (int32_t)lround(steps * 120.0);
            // WM_MOUSEWHEEL carries the position of the wheel event itself.
            view_point_to_drawable(event.wheel.mouse_x, event.wheel.mouse_y, &e.x, &e.y,
                                   &e.drawable_w, &e.drawable_h);
            queue_or_apply(e);
        }
        break;
    case SDL_EVENT_KEY_DOWN:
    case SDL_EVENT_KEY_UP:
        // Command combinations belong to the application, not the game:
        // Command-Q quits through SDL_EVENT_QUIT and Command-H hides.
        if (event.key.mod & SDL_KMOD_GUI)
            break;
        handle_key(event.key, event.type == SDL_EVENT_KEY_DOWN);
        break;
    case SDL_EVENT_WINDOW_FOCUS_LOST:
        trace_state("focus LOST");
        g_platform_capture_requested = false;
        update_platform_pointer_capture();
        g_escape_held = false;
        touch_release_all();
        note_focus(false);
        break;
    case SDL_EVENT_WINDOW_FOCUS_GAINED:
        trace_state("focus gained");
        note_focus(true);
        break;
    case SDL_EVENT_WINDOW_PIXEL_SIZE_CHANGED:
    case SDL_EVENT_WINDOW_RESIZED:
        note_screen_size();
        post_drawable_size();
        break;
    case SDL_EVENT_AUDIO_DEVICE_ADDED:
    case SDL_EVENT_AUDIO_DEVICE_REMOVED:
    case SDL_EVENT_AUDIO_DEVICE_FORMAT_CHANGED:
        // A TV/receiver waking or eARC renegotiating moves the game's stream
        // to another route without telling the mixer; record where it went.
        if (!event.adevice.recording) {
            sdl_audio_log_route(event.type == SDL_EVENT_AUDIO_DEVICE_ADDED     ? "device added"
                                : event.type == SDL_EVENT_AUDIO_DEVICE_REMOVED ? "device removed"
                                                                               : "format changed",
                                event.adevice.which);
            note_audio_device_event(event.type, event.adevice.which);
        }
        break;
    case SDL_EVENT_WINDOW_DISPLAY_CHANGED:
    case SDL_EVENT_WINDOW_DISPLAY_SCALE_CHANGED:
        note_screen_size();
        g_borderless_frame_dirty = true;
        post_drawable_size();
        break;
    case SDL_EVENT_WINDOW_ENTER_FULLSCREEN:
        // The green button or the View menu: the player chose fullscreen, so
        // it becomes the setting instead of being undone on the next frame.
        // A touch platform's window is always fullscreen and has no
        // "windowed" mode to save - platform_ui_pointer_capture_supported()
        // is false there - so a rotation raising this same event (no player
        // choice behind it) never rewrites the saved window-mode setting.
        if (!g_fullscreen_transition && platform_ui_pointer_capture_supported()) {
            g_wanted_window_mode = 2;
            (void)mods_display_set(DISPLAY_WINDOW, 2);
        }
        g_fullscreen_transition = false;
        g_window_mode = 2;
        note_screen_size();
        post_drawable_size();
        update_platform_pointer_capture();
        break;
    case SDL_EVENT_WINDOW_LEAVE_FULLSCREEN:
        if (!g_fullscreen_transition && g_wanted_window_mode == 2 &&
            platform_ui_pointer_capture_supported()) {
            g_wanted_window_mode = 0;
            (void)mods_display_set(DISPLAY_WINDOW, 0);
        }
        g_fullscreen_transition = false;
        g_window_mode = 0;
        note_screen_size();
        post_drawable_size();
        update_platform_pointer_capture();
        break;
    case SDL_EVENT_USER:
        if (event.user.code == kTouchPlaceEvent) {
            const double px = double((intptr_t)event.user.data1) / 16.0;
            const double py = double((intptr_t)event.user.data2) / 16.0;
            controls::host_pointer_moved(px, py);
            PendingInput e;
            e.kind = PendingInput::PLACE;
            view_point_to_drawable(px, py, &e.x, &e.y, &e.drawable_w, &e.drawable_h);
            queue_or_apply(e);
        }
        break;
    case SDL_EVENT_FINGER_CANCELED: {
        // The system took the finger (a gesture, a call): whatever it held lets go.
        const int64_t finger = (int64_t)event.tfinger.fingerID;
        if (controls::host_finger_cancel(finger))
            break;
        std::vector<TouchAction> actions;
        g_touch.finger_cancel(finger, &actions);
        push_touch_actions(actions);
        break;
    }
    case SDL_EVENT_FINGER_DOWN:
    case SDL_EVENT_FINGER_UP:
    case SDL_EVENT_FINGER_MOTION: {
        std::vector<TouchAction> actions;
        const uint64_t now = SDL_GetTicksNS();
        const int64_t finger = (int64_t)event.tfinger.fingerID;
        // A finger the on-screen controls own never reaches the gesture mapper.
        double px = 0, py = 0;
        finger_to_drawable(event.tfinger, &px, &py);
        if (event.type == SDL_EVENT_FINGER_DOWN) {
            if (controls::host_finger_down(finger, px, py, now))
                break;
            g_touch.finger_down(touch_point(event.tfinger), now, &actions);
        } else if (event.type == SDL_EVENT_FINGER_UP) {
            if (controls::host_finger_up(finger, now))
                break;
            g_touch.finger_up(touch_point(event.tfinger), now, &actions);
        } else {
            if (controls::host_finger_motion(finger, px, py, now))
                break;
            g_touch.finger_motion(touch_point(event.tfinger), now, &actions);
        }
        push_touch_actions(actions);
        static bool text_input = false;
        if (g_touch.text_input_wanted() != text_input) {
            text_input = g_touch.text_input_wanted();
            if (text_input)
                SDL_StartTextInput(g_window);
            else
                SDL_StopTextInput(g_window);
        }
        break;
    }
    case SDL_EVENT_GAMEPAD_ADDED:
    case SDL_EVENT_GAMEPAD_REMOVED:
    case SDL_EVENT_GAMEPAD_BUTTON_DOWN:
    case SDL_EVENT_GAMEPAD_BUTTON_UP:
    case SDL_EVENT_GAMEPAD_AXIS_MOTION: {
        if (event.type == SDL_EVENT_GAMEPAD_BUTTON_DOWN ||
            (event.type == SDL_EVENT_GAMEPAD_AXIS_MOTION && (event.gaxis.value > 12000 || event.gaxis.value < -12000)))
            g_pointer_active_ms = 0;   // playing with the controller: hide the idle cursor now
        // The settings page, when it is up, is driven by the pad too. Its keys go through the
        // same filter the keyboard's do; the game meanwhile sees a pad with nothing held.
        // Open or close the settings page from the controller: the DualSense touchpad click (the game
        // has no use for it), or Start while Back/View is held on any pad. Toggled before the pad state
        // is published, so the game never sees that Start press.
        if (event.type == SDL_EVENT_GAMEPAD_BUTTON_DOWN) {
            SDL_Gamepad *pad = SDL_GetGamepadFromID(event.gbutton.which);
            const bool touchpad = event.gbutton.button == SDL_GAMEPAD_BUTTON_TOUCHPAD;
            const bool chord = event.gbutton.button == SDL_GAMEPAD_BUTTON_START && pad &&
                               SDL_GetGamepadButton(pad, SDL_GAMEPAD_BUTTON_BACK);
            if (touchpad || chord) {
                (void)mods_input_key(0x44, 0, true); // F10
                (void)mods_input_key(0x44, 0, false);
                controls::gamepad_mask_until_release(event.gbutton.button);
                controls::gamepad_handle_event(event);
                break;
            }
        }
        // A release goes only where its press went.
        static bool sent[256] = {};
        bool down = false;
        const int dik = controls::gamepad_menu_key(event, &down);
        if (dik && down && mods_page_visible()) {
            sent[dik] = true;
            // The page's press stays the page's: when it closes the page (East = Escape), the game
            // must not see the button still held.
            if (event.type == SDL_EVENT_GAMEPAD_BUTTON_DOWN)
                controls::gamepad_mask_until_release(event.gbutton.button);
            (void)mods_input_key(uint8_t(dik), 0, true);
        } else if (dik && !down && sent[dik]) {
            sent[dik] = false;
            (void)mods_input_key(uint8_t(dik), 0, false);
        }
        controls::gamepad_handle_event(event);
        break;
    }
    case SDL_EVENT_WINDOW_CLOSE_REQUESTED:
    case SDL_EVENT_QUIT:
        // The guest closes itself: WM_CLOSE runs its own shutdown path, and
        // the window stays up until it is done so the last frame does not
        // vanish mid-teardown.
        if (!g_close_requested)
            fprintf(stderr, "[host] %s: asking the game to close\n",
                    event.type == SDL_EVENT_QUIT ? "quit requested" : "window close requested");
        g_close_requested = true;
        break;
    default:
        break;
    }
}

// Drains the event queue, waiting up to `seconds` for the first one. Zero
// means take what is already there and return; a real wait is a genuine sleep
// inside SDL, which is what stops the host spinning while the guest waits.
// Returns true when something changed the input state.
int service(double seconds) {
    uint32_t before = host_input_notify_count();
    size_t queued_before = 0;
    {
        std::lock_guard<std::mutex> held(g_pending_input_m);
        queued_before = g_pending_input.size();
    }
    SDL_Event event;
    bool first = true;
    for (;;) {
        bool got;
        if (first && seconds > 0.0)
            got = SDL_WaitEventTimeout(&event, int(seconds * 1000.0));
        else
            got = SDL_PollEvent(&event);
        first = false;
        if (!got)
            break;
        handle_event(event);
    }
    // Input that was only QUEUED still counts as input having arrived: the
    // guest should re-check its condition and come back through the tick,
    // which is where the queue is applied.
    {
        std::lock_guard<std::mutex> held(g_pending_input_m);
        if (g_pending_input.size() != queued_before)
            return 1;
    }
    return host_idle_wait_result(before, host_input_notify_count());
}

// The drawable, its scale and its safe area in drawable pixels, for the
// on-screen controls.
controls::Screen controls_screen() {
    int bw, bh, dw, dh;
    window_sizes(&bw, &bh, &dw, &dh);
    controls::Screen s;
    s.dw = dw;
    s.dh = dh;
    s.scale = bw > 0 ? double(dw) / bw : 1.0;
    s.safe = {0, 0, dw, dh};
    SDL_Rect safe;
    if (g_window && SDL_GetWindowSafeArea(g_window, &safe))
        s.safe = {int(lround(safe.x * s.scale)), int(lround(safe.y * s.scale)),
                  int(lround(safe.w * s.scale)), int(lround(safe.h * s.scale))};
    // Portrait pins the game image below the top strip.
    host_present_set_safe_top(s.safe.y);
    return s;
}

// The housekeeping every turn does once the events are in.
void after_events() {
    {
        // Time-based gestures (a long press) fire from the clock, not an event,
        // and deferred clicks go out once the game's cursor has caught up and
        // the game has presented frames that sampled the press.
        std::vector<TouchAction> actions;
        g_touch.frames_presented(host_present_count());
        g_touch.tick(SDL_GetTicksNS(), &actions);
        push_touch_actions(actions);
        // The controls follow the hardware keyboard (attached: key layouts
        // hidden), a physical controller (connected: pad layouts hidden and
        // its state fed to the pad) and the settings rows; the view is
        // published only when it changed.
        // The controls going away (a keyboard arriving, unless RECOMP_KEYPAD
        // forces them) lets go of every finger, as the keypad did.
        static const bool force = recomp_env("KEYPAD") != nullptr;
        static bool wanted = false;
        const bool absent = platform_ui_keypad_wanted();
        if (wanted && !(absent || force))
            touch_release_all();
        wanted = absent || force;
        const controls::Screen screen = controls_screen();
        const HostGameRect game = game_rect_for(screen.dw, screen.dh);
        controls::host_set_screen(screen, controls::Rect{game.x, game.y, game.w, game.h},
                                  screen.dh - (screen.safe.y + screen.safe.h));
        controls::gamepad_poll();
        controls::host_set_wanted(absent, controls::gamepad_connected());
        controls::host_pump(SDL_GetTicksNS());
        update_editor_text_input();
    }
    // A shell-launched process does not always come forward on its own, and a
    // window that never gained focus receives no key events at all. Ask again,
    // briefly, rather than once at startup and never afterwards.
    if (g_window && !window_focused() && boot_elapsed() < 3.0)
        SDL_RaiseWindow(g_window);
    note_focus(window_focused());

    // Capture follows the window and the page. Released whenever the window
    // is not focused, is minimised or is showing the settings page; taken back
    // by a click, not automatically.
    if (host_pointer_captured() && !pointer_capture_wanted())
        apply_pointer_capture(false);
    update_platform_pointer_capture();
    // Fullscreen chrome can reroute or omit motion events at an edge.
    // Captured input owns the pointer: sample its current position even
    // without an event for our window, and deliver only changes.
    sample_captured_pointer();

    if (g_close_requested && !boot_close_requested())
        boot_request_close("the window was closed");
}

// Borderless is a desktop window: keep the entire drawable inside the usable
// area of its display, below the menu bar and outside the Dock.
void fit_borderless_window() {
    SDL_Rect usable;
    if (!g_window || !SDL_GetDisplayUsableBounds(SDL_GetDisplayForWindow(g_window), &usable))
        return;
    g_borderless_frame_dirty = false;
    int x, y, w, h;
    SDL_GetWindowPosition(g_window, &x, &y);
    SDL_GetWindowSize(g_window, &w, &h);
    if (x == usable.x && y == usable.y && w == usable.w && h == usable.h)
        return;
    SDL_SetWindowPosition(g_window, usable.x, usable.y);
    SDL_SetWindowSize(g_window, usable.w, usable.h);
    post_drawable_size();
    fprintf(stderr, "[host] borderless usable frame: %d,%d %dx%d points\n", usable.x, usable.y,
            usable.w, usable.h);
}

// Consume a posted setting on the main thread. Fullscreen completes through
// window events; another setting can supersede it meanwhile.
void apply_window_mode() {
    int request = host_display_take_window();
    if (request >= 0)
        g_wanted_window_mode = request;
    if (!g_window || g_fullscreen_transition)
        return;
    if (window_fullscreen()) {
        if (g_wanted_window_mode != 2) {
            g_fullscreen_transition = true;
            update_platform_pointer_capture();
            SDL_SetWindowFullscreen(g_window, false);
        }
        return;
    }
    if (g_window_mode == g_wanted_window_mode) {
        if (g_window_mode == 1 && g_borderless_frame_dirty)
            fit_borderless_window();
        return;
    }
    if (g_window_mode == 0) {
        SDL_GetWindowPosition(g_window, &g_windowed_x, &g_windowed_y);
        SDL_GetWindowSize(g_window, &g_windowed_w, &g_windowed_h);
    }
    if (g_wanted_window_mode == 1) {
        SDL_SetWindowBordered(g_window, false);
        g_window_mode = 1;
        fit_borderless_window();
    } else {
        SDL_SetWindowBordered(g_window, true);
        if (g_windowed_w > 0) {
            SDL_SetWindowSize(g_window, g_windowed_w, g_windowed_h);
            SDL_SetWindowPosition(g_window, g_windowed_x, g_windowed_y);
        }
        g_window_mode = 0;
        if (g_wanted_window_mode == 2) {
            g_fullscreen_transition = true;
            update_platform_pointer_capture();
            // Borderless desktop fullscreen: no display mode change.
            SDL_SetWindowFullscreenMode(g_window, nullptr);
            SDL_SetWindowFullscreen(g_window, true);
        }
    }
    post_drawable_size();
}

// The display mode changed under us, which resizes the window.
void apply_mode_change() {
    if (!g_mode_dirty)
        return;
    g_mode_dirty = false;
    host_pointer_set_mode((int)g_pending_mode_w, (int)g_pending_mode_h);
    g_mode_w = (int)g_pending_mode_w;
    g_mode_h = (int)g_pending_mode_h;
    const HostWindowSize size = window_size_for(g_mode_w, g_mode_h);
    // The minimum first, since a window cannot shrink below the one it had. It
    // is the guest's frame when that fits the screen, so integer scaling always
    // has a whole multiple to take, and never more than the window.
    SDL_SetWindowMinimumSize(g_window, size.min_w, size.min_h);
    if (g_window_mode == 0 && !g_fullscreen_transition) {
        SDL_SetWindowSize(g_window, size.w, size.h);
        SDL_SetWindowPosition(g_window, SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED);
        post_drawable_size();
    }
    host_set_client_size(host_main_window(), g_mode_w, g_mode_h);
}

// ---------------------------------------------------------------------------
// Remaster U4 (TEST ONLY; review/2026-10-06-u4-app-script-runner-design.md after challenge C-U4): a script runner
// for the real app, so the harness and the A5 self-benchmark measure SpeedRecomp itself (window, display link,
// audio, the 4K present). Only with RECOMP_TEST_SCRIPTS=1 and --script <file>; the Launch script never sets either.
// It runs the subset the harness scripts use - wait, key, pad, poke, quit - on the script clock (at_ms), from
// pump() on the host thread holding the baton. Keys arrive as the KEY input real keys produce; the pad is the
// virtual pad's own test source (the touch router rewrites the touch source every frame here). Other verbs (dump included) are logged once and skipped.
// ---------------------------------------------------------------------------
struct AppScript {
    std::vector<HostScriptStep> steps;
    size_t next = 0;
    bool on = false, started = false;
    uint32_t start_ms = 0;
    controls::PadState pad{};
    // keyuntil (run 33): the step in progress
    bool ku_started = false, ku_down = false;
    uint32_t ku_since = 0, ku_pressed_at = 0, ku_presses = 0, ku_pressed_reads = 0;
    // run 33: the last key press (time, the game's input reads) and since when its release has waited for a read
    uint32_t key_down_ms = 0, key_down_reads = 0, release_waiting_since = 0;
} g_app_script;

uint32_t app_guest_u32(uint32_t a) {
    if (!gm_valid(a, 4))
        return 0;
    const uint8_t *p = gm_ptr(a);
    return uint32_t(p[0]) | uint32_t(p[1]) << 8 | uint32_t(p[2]) << 16 | uint32_t(p[3]) << 24;
}

void app_script_key(const HostScriptStep &step, bool down) {
    const HostKeyMapping m = host_key_mapping_for_dik(step.dik);
    if (!m.dik)
        return;
    PendingInput e;
    e.kind = PendingInput::KEY;
    e.key = m.mac;
    e.down = down;
    e.flags = current_modifier_flags();
    apply_input(e);
}

// Run 33: keyuntil in the app runner, as in pop_smoke: 0 still pressing, 1 the claim holds, -1 it never did.
int app_script_keyuntil(AppScript &s, const HostScriptStep &step, uint32_t now) {
    if (!s.ku_started) {
        s.ku_started = true;
        s.ku_down = false;
        s.ku_since = now;
        s.ku_pressed_at = now - step.press_ms;
        s.ku_presses = 0;
    }
    if (s.ku_down) {
        if (host_script_key_release_ready(s.ku_pressed_at, s.ku_pressed_reads, now, host_input_read_count())) {
            app_script_key(step, false);
            s.ku_down = false;
        }
        return 0;
    }
    double v = host_script_fe_metric(step.name, app_guest_u32);
    const bool passed = v >= 0 && (step.at_least ? v >= step.threshold : v > step.threshold);
    if (!passed && now - s.ku_since < step.timeout_ms) {
        if (now - s.ku_pressed_at >= step.press_ms) {
            app_script_key(step, true);
            s.ku_down = true;
            s.ku_pressed_at = now;
            s.ku_pressed_reads = host_input_read_count();
            ++s.ku_presses;
        }
        return 0;
    }
    fprintf(stderr, "[app-script] keyuntil %s: %s after %u presses and %.1fs\n", step.name,
            passed ? "reached" : "did NOT reach", s.ku_presses, (now - s.ku_since) / 1000.0);
    s.ku_started = false;
    return passed ? 1 : -1;
}

// Each change of the front-end screen that has the input, in the log (run 33; scripted runs only).
void app_script_log_fe_screen(uint32_t now) {
    static char last[64] = "";
    static uint32_t polled_at = 0;
    if (now - polled_at < 100)
        return;
    polled_at = now;
    char screen[64];
    host_script_fe_screen(app_guest_u32, screen, sizeof screen);
    if (strcmp(screen, last)) {
        snprintf(last, sizeof last, "%s", screen);
        fprintf(stderr, "[app-script] fe screen: %s (at %.1fs)\n", screen[0] ? screen : "(none)", now / 1000.0);
    }
}

bool app_script_load(const char *path) {
    FILE *f = fopen(path, "rb");
    if (!f)
        return false;
    std::string text;
    char buf[4096];
    size_t n;
    while ((n = fread(buf, 1, sizeof buf, f)) > 0)
        text.append(buf, n);
    fclose(f);
    g_app_script.steps.resize(8192);
    char err[256] = "";
    const int count = host_script_parse(text.c_str(), g_app_script.steps.data(), (int)g_app_script.steps.size(), err,
                                        sizeof err);
    if (count < 0) {
        fprintf(stderr, "[app-script] %s: %s\n", path, err);
        return false;
    }
    g_app_script.steps.resize((size_t)count);
    g_app_script.on = true;
    fprintf(stderr, "[app-script] %s: %d steps (TEST ONLY)\n", path, count);
    return true;
}

bool app_script_due() {
    if (!g_app_script.on || g_app_script.next >= g_app_script.steps.size())
        return false;
    if (!g_app_script.started)
        return true;
    return boot_guest_millis() - g_app_script.start_ms >= g_app_script.steps[g_app_script.next].at_ms;
}

void app_script_tick() {
    AppScript &s = g_app_script;
    if (!s.on || s.next >= s.steps.size())
        return;
    const uint32_t now = boot_guest_millis();
    if (!s.started) {
        s.started = true;
        s.start_ms = now;
    }
    app_script_log_fe_screen(now);
    while (s.next < s.steps.size() && now - s.start_ms >= s.steps[s.next].at_ms) {
        if (s.steps[s.next].op == HOST_SCRIPT_KEYUNTIL) {
            const uint32_t since = s.ku_started ? s.ku_since : now;
            const int r = app_script_keyuntil(s, s.steps[s.next], now);
            if (r == 0)
                return;   // one key edge per tick; the steps after it wait
            if (r < 0) {
                s.next = s.steps.size();
                boot_request_close("script readiness assertion timed out");
                return;
            }
            s.start_ms += now - since;   // the steps after it keep their spacing
            ++s.next;
            return;
        }
        if (s.steps[s.next].op == HOST_SCRIPT_KEY && !s.steps[s.next].down) {
            // run 33: a release waits until the game has read the press; the script clock waits with it
            if (!host_script_key_release_ready(s.key_down_ms, s.key_down_reads, now, host_input_read_count())) {
                if (!s.release_waiting_since)
                    s.release_waiting_since = now;
                return;
            }
            if (s.release_waiting_since) {
                if (now - s.key_down_ms >= 250)   // only when the game was slower than the usual press
                    fprintf(stderr, "[app-script] key %s held %u ms until the game read it\n", s.steps[s.next].name,
                            now - s.key_down_ms);
                s.start_ms += now - s.release_waiting_since;
                s.release_waiting_since = 0;
            }
        }
        if (s.steps[s.next].op == HOST_SCRIPT_KEY && s.steps[s.next].down) {
            s.key_down_ms = now;
            s.key_down_reads = host_input_read_count();
        }
        const HostScriptStep &step = s.steps[s.next++];
        switch (step.op) {
        case HOST_SCRIPT_WAIT:
            break;
        case HOST_SCRIPT_KEY:
            app_script_key(step, step.down != 0);
            break;
        case HOST_SCRIPT_PAD: {
            const int index = step.button;
            if (index < 13) {
                const uint16_t bit = uint16_t(1u << index);
                s.pad.buttons = step.x ? s.pad.buttons | bit : s.pad.buttons & ~bit;
                if (index == 6)
                    s.pad.l2 = float(step.x);
                if (index == 7)
                    s.pad.r2 = float(step.x);
            } else if (index < 17) {
                const uint8_t bit = uint8_t(1u << (index - 13));
                s.pad.hat = step.x ? s.pad.hat | bit : s.pad.hat & ~bit;
            } else if (index < 23) {
                float *axes[] = {&s.pad.lx, &s.pad.ly, &s.pad.rx, &s.pad.ry, &s.pad.l2, &s.pad.r2};
                *axes[index - 17] = step.x / 32767.0f;
            }
            controls::vpad().set_source(controls::kPadSourceTest, s.pad);
            break;
        }
        case HOST_SCRIPT_POKE:
            if (gm_valid(step.addr, step.len)) {
                const uint32_t v = uint32_t(int64_t(step.threshold));
                uint8_t *p = gm_ptr(step.addr);
                for (uint32_t i = 0; i < step.len; ++i)
                    p[i] = uint8_t(v >> (8 * i));
                fprintf(stderr, "[app-script] poke %08x = 0x%x (%u bytes; test)\n", step.addr, v, step.len);
            }
            break;
        case HOST_SCRIPT_MOVE: {   // R3 run 17 TEST (D-119): the pointer to window point x,y, as a real motion event
            if (!g_window)
                break;
            static float last_x = -1, last_y = -1;
            SDL_MouseMotionEvent m{};
            m.type = SDL_EVENT_MOUSE_MOTION;
            m.windowID = SDL_GetWindowID(g_window);
            m.which = 0;
            m.x = float(step.x);
            m.y = float(step.y);
            m.xrel = last_x < 0 ? 0.0f : m.x - last_x;
            m.yrel = last_y < 0 ? 0.0f : m.y - last_y;
            last_x = m.x;
            last_y = m.y;
            handle_mouse_move(m);
            int bw, bh, dw, dh;
            window_sizes(&bw, &bh, &dw, &dh);
            fprintf(stderr, "[app-script] move %d,%d points (window %dx%d points, %dx%d pixels)\n", step.x, step.y, bw, bh,
                    dw, dh);
            break;
        }
        case HOST_SCRIPT_WINDOW:   // R3 run 17 TEST (D-119): a resize to another aspect ratio, as a user's drag does
            if (g_window) {
                // A maximized or full-screen window ignores a size: leave those states first, as a user's drag does.
                const SDL_WindowFlags fl = SDL_GetWindowFlags(g_window);
                if (fl & SDL_WINDOW_FULLSCREEN)
                    SDL_SetWindowFullscreen(g_window, false);
                if (fl & SDL_WINDOW_MAXIMIZED)
                    SDL_RestoreWindow(g_window);
                SDL_SetWindowSize(g_window, step.x, step.y);
                SDL_SyncWindow(g_window);
                int bw, bh, dw, dh;
                window_sizes(&bw, &bh, &dw, &dh);
                fprintf(stderr, "[app-script] window %dx%d points requested: now %dx%d points, %dx%d pixels (was%s%s)\n",
                        step.x, step.y, bw, bh, dw, dh, (fl & SDL_WINDOW_FULLSCREEN) ? " full screen" : "",
                        (fl & SDL_WINDOW_MAXIMIZED) ? " maximized" : "");
            }
            break;
        case HOST_SCRIPT_DUMPC: {   // R3 run 17 TEST (D-119): the composited frame as shown, to RECOMP_HOST_DUMP_DIR
            const char *dir = recomp_env("HOST_DUMP_DIR");
            HostCompletedComposite frame;
            if (!dir || !*dir || !host_present_copy_composite(&frame)) {
                fprintf(stderr, "[app-script] dumpc %s: unavailable (no dump dir, or no RGBA8/BGRA8 composite)\n", step.name);
                break;
            }
            std::vector<uint8_t> rgb(size_t(frame.w) * frame.h * 3);
            for (size_t i = 0; i < rgb.size() / 3; ++i)
                memcpy(rgb.data() + i * 3, frame.rgba.data() + i * 4, 3);
            char path[1024];
            snprintf(path, sizeof path, "%s/app_%s_composite.ppm", dir, step.name);
            const bool ok = host_write_ppm(path, rgb.data(), frame.w, frame.h);
            const auto &sc = frame.layout.scene;
            fprintf(stderr, "[app-script] dumpc %s %s: frame %llu class %d guest %dx%d drawable %dx%d scene %.4f,%.4f +%.1f,%.1f "
                            "domain %d classic %d legacy %d\n",
                    step.name, ok ? "written" : "FAILED", (unsigned long long)frame.frame_id, int(frame.cls), frame.guest_w,
                    frame.guest_h, frame.w, frame.h, sc.scale_x, sc.scale_y, sc.offset_x, sc.offset_y, sc.domain_w,
                    int(frame.layout.classic), int(frame.layout.legacy));
            break;
        }
        case HOST_SCRIPT_QUIT:
            fprintf(stderr, "[app-script] quit\n");
            boot_request_close("the test script asked to quit");
            break;
        default: {
            static bool warned[64];
            if (step.op >= 0 && step.op < 64 && !warned[step.op]) {
                warned[step.op] = true;
                fprintf(stderr, "[app-script] verb %d is not supported in the app runner; skipped\n", step.op);
            }
            break;
        }
        }
    }
}

// True when this thread may touch the window at all.
bool on_host_thread() {
    if (boot_on_run_thread() && SDL_IsMainThread())
        return true;
    static bool warned = false;
    if (!warned) {
        warned = true;
        fprintf(stderr, "[host] a guest worker thread reached the host; the window is "
                        "serviced only on the main thread\n");
    }
    return false;
}

// One turn of the host's event loop, called from inside guest code.
//
// The tick can arrive on any guest thread: the runtime's cooperative scheduler
// hands the baton to real pthreads and whichever one holds it reads the clock.
// The window belongs to the thread that called boot_run, so a tick on any
// other thread does nothing at all and hands control straight back.
void pump() {
    if (!on_host_thread())
        return;
    // First: anything an idle slice decoded but could not apply. This thread
    // reached here through the guest's own clock read, so it holds the baton
    // and a mod callback dispatched from here is serialised against every
    // other guest thread, which is the whole point.
    deliver_pending_input();
    app_script_tick(); // Remaster U4: TEST ONLY, nothing unless RECOMP_TEST_SCRIPTS=1 and --script
    apply_window_mode();
    apply_mode_change();
    service(0.0);
    after_events();
    update_idle_cursor();
    host_gate_pointer_tick();
}

// The runtime's idle wait, called on the run thread when the guest is about to
// block. Returns 1 when input arrived, so the caller can re-check its own
// condition at once.
// The runtime's deadline ticker (any thread): end an SDL event wait now, because a
// guest deadline fell due during it. A user event with this code is otherwise ignored.
constexpr Sint32 kSchedWakeEvent = 0x5ced;
void sdl_idle_wake() {
    SDL_Event e{};
    e.type = SDL_EVENT_USER;
    e.user.code = kSchedWakeEvent;
    SDL_PushEvent(&e);
}

int idle_wait(double seconds) {
    if (!on_host_thread())
        return 0;
    if (seconds < 0.0)
        seconds = 0.0;
    apply_window_mode();
    apply_mode_change();
    int changed = service(seconds);
    after_events();
    update_idle_cursor();
    return changed;
}

void on_mode_change(int w, int h, int bpp) {
    (void)bpp;
    if (w <= 0 || h <= 0)
        return;
    g_pending_mode_w = (uint32_t)w;
    g_pending_mode_h = (uint32_t)h;
    g_mode_dirty = true;
}

// The watchdog takes the report lock before calling this and main takes it
// around its own call, so everything below reads a consistent snapshot.
void report(FILE *out, bool abnormal) {
    fprintf(out, "\n== windowed run ==\n");
    fprintf(out, "stopped:            %s\n", boot_stop_reason());
    fprintf(out, "elapsed:            %.1fs\n", boot_elapsed());
    fprintf(out, "presented frames:   %u\n", host_present_count());
    // Per display mode, because a rate averaged over a whole run mixes the
    // menu and the loading screen in with gameplay.
    for (int i = 0; i < host_present_rate_count(); ++i) {
        HostPresentRate r;
        memset(&r, 0, sizeof r);
        host_present_rate(i, &r);
        fprintf(out, "  %4dx%-4d %2dbpp:  %u guest presents; %.1fs at this mode\n", r.w, r.h, r.bpp,
                r.presents, r.seconds);
    }
    // The two lines the display baseline is made of, in the same words the
    // smoke host uses, because display_compare.py parses the text.
    {
        char line[768];
        if (host_stats_gameplay_line(line, sizeof line))
            fprintf(out, "gameplay: %s\n", line);
        if (host_stats_access_line(line, sizeof line))
            fprintf(out, "%s\n", line);
    }
    fprintf(out, "input changes:      %u announced to the DirectInput shim\n",
            host_input_notify_count());
    fprintf(out,
            "Direct3D:           %u draws, %u textures, %u write-backs into the "
            "render target\n",
            host_d3d_total_draws(), host_d3d_total_textures(), host_d3d_total_flushes());
    // What the mixer actually produced, when the run was asked to capture it.
    host_capture_print(out);
    boot_print_dx_objects(out);
    boot_print_exit_code(out);
    boot_print_undeliverable(out);
    fflush(out);
    boot_print_import_stats(out, abnormal);
}

// The .app can be started from anywhere, and the guest's file system is rooted
// at the directory holding the EXE. Walking up from the executable finds the
// checkout whichever way the app was launched.
std::string classic_modes_path() {
    std::string p = host_resource("classic-modes.json");
    return p.empty() ? "tools/recomp/baseline/classic-modes.json" : p;
}

void post_drawable_size() {
    int bw, bh, dw, dh;
    window_sizes(&bw, &bh, &dw, &dh);
    static int last_w = 0, last_h = 0;
    if (dw <= 0 || dh <= 0 || (last_w == dw && last_h == dh))
        return;
    // A flip between portrait and landscape (a phone rotating) moves every
    // touch control out from under whatever fingers were holding it.
    if (last_w > 0 && last_h > 0 && (last_w > last_h) != (dw > dh))
        touch_release_all();
    last_w = dw;
    last_h = dh;
    host_present_resize(dw, dh);
}

} // namespace

// ---------------------------------------------------------------------------

#ifdef __EMSCRIPTEN__
// ---------------------------------------------------------------------------
// The web. The browser's main thread may not block, and it alone owns the
// canvas, the input events and WebGPU, so the game runs on a thread of its
// own and the main thread drives everything else from requestAnimationFrame:
// events into the input queue, the game's queued Direct3D 9 work onto the
// GPU, and one presenter turn. The page (tools/web) imports the game into the
// origin-private file system and passes its path as --exe.
// ---------------------------------------------------------------------------
namespace {

std::string g_web_exe;
std::atomic<bool> g_web_guest_done{false};
int g_web_dw = 0, g_web_dh = 0;

void web_tick() {
    deliver_pending_input();
}

int web_idle_wait(double seconds) {
    {
        std::lock_guard<std::mutex> held(g_pending_input_m);
        if (!g_pending_input.empty())
            return 1;
    }
    const double wait = std::min(std::max(seconds, 0.0), 0.004);
    if (wait > 0)
        os_sleep_us((uint64_t)(wait * 1e6));
    std::lock_guard<std::mutex> held(g_pending_input_m);
    return g_pending_input.empty() ? 0 : 1;
}

void web_frame() {
    host_d9_frame(true);
    service(0.0);
    after_events();
    int bw, bh, dw, dh;
    window_sizes(&bw, &bh, &dw, &dh);
    if (dw > 0 && dh > 0 && (dw != g_web_dw || dh != g_web_dh)) {
        g_web_dw = dw;
        g_web_dh = dh;
        host_present_resize(dw, dh);
        host_display_set_screen(dw, dh);
    }
    host_d9_pump();
    host_present_pump();
    host_d9_frame(false);
    if (g_web_guest_done.load())
        emscripten_cancel_main_loop();
}

// The game's thread: the game's files are read from here, never from the
// main thread, which may not wait for the file system.
void web_guest() {
    backend_t opfs = wasmfs_create_opfs_backend();
    if (!opfs || wasmfs_create_directory("/opfs", 0777, opfs) != 0)
        fprintf(stderr, RECOMP_APP_NAME ": the browser's private file system is unavailable\n");
    std::string dir = g_web_exe.substr(0, g_web_exe.find_last_of('/'));
    if (!dir.empty() && os_chdir(dir.c_str()) != 0)
        fprintf(stderr, "[host] could not enter %s\n", dir.c_str());
    BootOptions options;
    options.name = "web";
    options.exe = g_web_exe.c_str();
    options.activate = true;
    options.tick = web_tick;
    sched_set_input_queue(input_pending, drain_input);
    options.idle_wait = web_idle_wait;
    options.report = report;
    options.deadline_seconds = 0.0;
    if (!boot_load(options)) {
        fprintf(stderr, RECOMP_APP_NAME ": %s\n", loader_error());
        g_web_guest_done.store(true);
        return;
    }
    printf(RECOMP_APP_NAME ": %s, entry %08x\n", loader_exe_path().c_str(), loader_entry_point());
    fflush(stdout);
    host_midi_startup(win32_midi_soundfont_path().c_str());
    boot_run();
    g_web_guest_done.store(true);
}

int web_main(int argc, char **argv) {
    for (int i = 1; i < argc; ++i) {
        if (strncmp(argv[i], "--exe=", 6) == 0)
            g_web_exe = argv[i] + 6;
        else if (strcmp(argv[i], "--exe") == 0 && i + 1 < argc)
            g_web_exe = argv[++i];
    }
    if (g_web_exe.empty()) {
        fprintf(stderr, RECOMP_APP_NAME ": the page gave no --exe\n");
        return 2;
    }
    if (!SDL_Init(SDL_INIT_VIDEO | SDL_INIT_EVENTS)) {
        fprintf(stderr, RECOMP_APP_NAME ": SDL_Init failed: %s\n", SDL_GetError());
        return 3;
    }
    g_gpu = gpu::create_default_device();
    if (!g_gpu) {
        fprintf(stderr, RECOMP_APP_NAME ": this browser gave the page no WebGPU device\n");
        return 3;
    }
    g_window = SDL_CreateWindow(RECOMP_GAME_NAME, g_mode_w, g_mode_h,
                                SDL_WINDOW_RESIZABLE | SDL_WINDOW_HIGH_PIXEL_DENSITY);
    if (!g_window) {
        fprintf(stderr, RECOMP_APP_NAME ": SDL_CreateWindow failed: %s\n", SDL_GetError());
        return 3;
    }
    g_surface = gpu::native_surface_for_window(g_window);
    fprintf(stderr, "GPU backend: %s\n", gpu::default_backend_name());
    int bw, bh, dw, dh;
    window_sizes(&bw, &bh, &dw, &dh);
    if (dw <= 0 || dh <= 0) {
        dw = 1280;
        dh = 720;
    }
    g_web_dw = dw;
    g_web_dh = dh;
    host_display_set_screen(dw, dh);

    D3DRenderer *renderer = new D3DRenderer(g_gpu.get());
    if (!renderer->ok())
        return 3;
    D3DRenderer::setShared(renderer);
    host_present_set_device(g_gpu.get());
    host_present_set_sched_probe(recomp_sched_main_totals);
    host_set_idle_wake(sdl_idle_wake);
    mods_display_live_defaults();
    mods_display_default_overlay(platform_ui_default_overlay());
    mods_display_load_modes(classic_modes_path().c_str());
    host_present_on_mode_change(on_mode_change);
    host_input_set_notify(dinput_host_input_changed);
    host_present_start(g_surface, dw, dh);
    if (!host_d9_web_start(g_gpu.get()))
        fprintf(stderr,
                RECOMP_APP_NAME ": no WebGPU Direct3D 9 renderer; the game draws on the CPU\n");

    const char *capture = recomp_env("HOST_AUDIO_CAPTURE");
    if (capture && *capture)
        host_audio_capture_begin(capture);

    std::thread(web_guest).detach();
    emscripten_set_main_loop(web_frame, 0, false);
    return 0; // the runtime stays alive for the main loop and the game's thread
}

} // namespace
#endif

int main(int argc, char **argv) {
#ifdef __EMSCRIPTEN__
    return web_main(argc, argv);
#endif
    // --version and --probe-layout answer before SDL or the GPU come up, so a
    // packaged build can be checked on a machine with neither a display nor
    // the game.
    const char *exe_flag = nullptr;
    for (int i = 1; i < argc; ++i) {
        if (strcmp(argv[i], "--version") == 0) {
            printf(RECOMP_APP_NAME " %s (%s)\n", POP_RECOMP_VERSION, gpu::default_backend_name());
            return 0;
        }
        if (strcmp(argv[i], "--probe-layout") == 0) {
            const HostLayout &l = host_layout();
            printf("resources_dir=%s\nprofile_dir=%s\ndeveloper=%d\n", l.resources_dir.c_str(),
                   l.profile_dir.c_str(), int(l.developer));
            return 0;
        }
        if (strcmp(argv[i], "--exe") == 0 && i + 1 < argc)
            exe_flag = argv[++i];
        else if (strncmp(argv[i], "--exe=", 6) == 0)
            exe_flag = argv[i] + 6;
        else if (strcmp(argv[i], "--launcher") == 0)
            continue; // launcher::requested reads it
        else if (strcmp(argv[i], "--script") == 0 && i + 1 < argc) {
            // Remaster U4 (TEST ONLY): honoured only with RECOMP_TEST_SCRIPTS=1.
            const char *on = getenv("RECOMP_TEST_SCRIPTS");
            if (on && !strcmp(on, "1")) {
                if (!app_script_load(argv[i + 1]))
                    return 2;
            } else {
                fprintf(stderr, RECOMP_APP_NAME ": --script needs RECOMP_TEST_SCRIPTS=1 (test only); ignored\n");
            }
            ++i;
        } else {
            fprintf(stderr, "usage: " RECOMP_APP_NAME " [--exe <" RECOMP_EXECUTABLE
                            ">] [--launcher] [--version] [--probe-layout]\n");
            return 2;
        }
    }
    std::string game_error;
#ifdef __ANDROID__
    platform_ui_init_hints(); // stdout and stderr reach logcat from here on
    (void)exe_flag;           // The mobile app always uses its own external data root.
    // SDL's Java glue has initialized the app-specific external files path
    // before SDL_main. Resolve it before host_layout caches a profile path.
    const char *external = SDL_GetAndroidExternalStoragePath();
    GamePath game;
    if (!external || !*external) {
        game_error =
            "Android external files storage is unavailable: " + std::string(SDL_GetError());
    } else {
        const std::string data_root = external;
        recomp_env_apply_file((data_root + "/switches.txt").c_str());
        const char *profile = recomp_env("PROFILE_DIR");
        if (!profile || !*profile)
            os_setenv("RECOMP_PROFILE_DIR", (data_root + "/profile").c_str());
        // No executable path can lead to this app's resources (the process is
        // the system's app_process), so name the data root the activity
        // unpacked the APK's assets into: host_resource("controls") is
        // <data root>/controls. A switches.txt override still wins.
        const char *resources = recomp_env("RESOURCES_DIR");
        if (!resources || !*resources)
            os_setenv("RECOMP_RESOURCES_DIR", data_root.c_str());
        game = game_path_resolve(nullptr, data_root.c_str());
        // Missing data is the launcher's to explain and import.
        if (!game.exe.empty())
            SDL_Log("[android] game data: %s", game.exe.c_str());
    }
#else
    GamePath game = platform_ui_resolve_game(exe_flag, &game_error);
#endif
    if (!game_error.empty()) {
#ifdef __ANDROID__
        // The kit page needs a running presenter and initialized mod rows;
        // before boot, log the actionable path instead of opening a picker.
        SDL_LogError(SDL_LOG_CATEGORY_APPLICATION, "%s", game_error.c_str());
        SDL_Quit();
        platform_ui_process_exit(2);
#else
        // Before SDL_Init on purpose: the message box brings up what it needs,
        // and on a device with no console this is the only place the reason shows.
        fprintf(stderr, RECOMP_APP_NAME ": %s\n", game_error.c_str());
        SDL_ShowSimpleMessageBox(SDL_MESSAGEBOX_ERROR, RECOMP_APP_NAME, game_error.c_str(),
                                 nullptr);
#endif
        return 2;
    }
    if (game.source == GamePathSource::Checkout &&
        os_chdir(host_layout().checkout_root.c_str()) != 0)
        fprintf(stderr, "[host] could not enter %s\n", host_layout().checkout_root.c_str());

    platform_ui_init_hints();
    if (!SDL_Init(SDL_INIT_VIDEO | SDL_INIT_EVENTS | SDL_INIT_GAMEPAD)) {
        fprintf(stderr, RECOMP_APP_NAME ": SDL_Init failed: %s\n", SDL_GetError());
        return 3;
    }
    os_audio_on_service_restart(audio_service_restarted);
    // While the settings page is up it owns the pad; the game sees nothing held.
    controls::gamepad_set_suppressed_query([] { return mods_page_visible(); });

    g_gpu = gpu::create_default_device();
    if (!g_gpu) {
        fprintf(stderr, RECOMP_APP_NAME ": no GPU device is available (%s)\n",
                gpu::default_backend_name());
        // The launcher still runs, drawn in software: the game can be
        // imported, and the player is told why it cannot start here.
        int mode = 0;
        const HostWindowSize ws = window_size_for(g_mode_w, g_mode_h);
        g_window =
            platform_ui_create_window(RECOMP_GAME_NAME, ws.w, ws.h, ws.min_w, ws.min_h, 0, &mode);
        if (g_window) {
            auto platform = launcher::make_platform(g_window);
            launcher::RunOptions options;
            options.known = game.exe;
            options.unplayable =
                std::string("This device has no usable ") + gpu::default_backend_name() +
                " graphics, which the game needs" +
                (strcmp(gpu::default_backend_name(), "vulkan") == 0 ? " (Vulkan 1.1)." : ".");
            if (const char *keys = recomp_env("LAUNCHER_KEYS"))
                options.keys = keys;
            if (const char *dump = recomp_env("LAUNCHER_DUMP")) {
                options.dump_path = dump;
                const char *home = getenv("HOME");
                if (!options.dump_path.empty() && options.dump_path[0] != '/' && home && *home)
                    options.dump_path = std::string(home) + "/" + options.dump_path;
            }
            launcher::run(g_window, nullptr, nullptr, *platform, options);
        }
        return 3;
    }

    const HostWindowSize size = window_size_for(g_mode_w, g_mode_h);
    const bool vulkan = strcmp(gpu::default_backend_name(), "vulkan") == 0;
    if (vulkan && !SDL_Vulkan_LoadLibrary(gpu::vulkan_loader_path()))
        fprintf(stderr, RECOMP_APP_NAME ": SDL_Vulkan_LoadLibrary: %s\n", SDL_GetError());
    const SDL_WindowFlags surface_flag = vulkan ? SDL_WINDOW_VULKAN : SDL_WINDOW_METAL;
    g_window = platform_ui_create_window(RECOMP_GAME_NAME, size.w, size.h, size.min_w, size.min_h,
                                         surface_flag, &g_window_mode);
    if (!g_window) {
        fprintf(stderr, RECOMP_APP_NAME ": SDL_CreateWindow failed: %s\n", SDL_GetError());
        return 3;
    }
    note_screen_size();
    g_surface = gpu::native_surface_for_window(g_window);
    if (!g_surface) {
        fprintf(stderr, RECOMP_APP_NAME ": no %s surface for the window: %s\n",
                gpu::default_backend_name(), SDL_GetError());
        return 3;
    }
    fprintf(stderr, "GPU backend: %s\n", gpu::default_backend_name());
    // The launcher: when the game is missing or not the supported build, or the
    // player asked for it. Mobile shows it briefly even when the game is ready,
    // so a touch can open it.
    {
        const bool mobile = !platform_ui_pointer_capture_supported();
        const bool asked = launcher::requested(argc, argv);
        if (game.exe.empty() || asked || mobile) {
            auto platform = launcher::make_platform(g_window);
            launcher::RunOptions options;
            options.known = game.exe;
            options.auto_play = !game.exe.empty() && !asked && mobile ? 1.5 : 0;
            // A relative dump path is under the app's home (a device's container).
            if (const char *dump = recomp_env("LAUNCHER_DUMP")) {
                options.dump_path = dump;
                const char *home = getenv("HOME");
                if (!options.dump_path.empty() && options.dump_path[0] != '/' && home && *home)
                    options.dump_path = std::string(home) + "/" + options.dump_path;
            }
            if (const char *keys = recomp_env("LAUNCHER_KEYS"))
                options.keys = keys;
            const std::string chosen =
                launcher::run(g_window, g_gpu.get(), g_surface, *platform, options);
            if (chosen.empty())
                return 2;
            if (chosen != game.exe && !game_path_save(chosen))
                fprintf(stderr, RECOMP_APP_NAME ": could not remember the game path\n");
            game.exe = chosen;
        }
    }
    std::string exe = game.exe;

    // The renderer first: the presenter shares its device, so a present
    // cannot run ahead of the scene it is showing.
    D3DRenderer *renderer = new D3DRenderer(g_gpu.get());
    if (!renderer->ok())
        return 3;
    D3DRenderer::setShared(renderer);
    host_present_set_device(g_gpu.get());
    host_present_set_sched_probe(recomp_sched_main_totals);
    host_set_idle_wake(sdl_idle_wake);

    fprintf(stderr, "Mouse capture: click inside to capture; hold Escape to release; drag to "
                    "the window edge to resize.\n");
    mods_display_live_defaults();
    mods_display_default_overlay(platform_ui_default_overlay());
    mods_display_load_modes(classic_modes_path().c_str());
    host_present_on_mode_change(on_mode_change);
    // Every change to the input state wakes the guest's DirectInput threads,
    // which wait on an event rather than polling. Installed here rather than
    // referenced from input.cpp, which links none of the shims.
    host_input_set_notify(dinput_host_input_changed);
    // Before the first presented frame, where mods_page_init reads the
    // layout names this registers.
    {
        controls::HostHooks hooks;
        hooks.key = push_touch_key;
        hooks.touch_actions = push_touch_actions;
        hooks.system_keyboard = toggle_system_keyboard;
        hooks.open_settings = open_settings_page;
        controls::host_init(hooks);
    }

    SDL_ShowWindow(g_window);
    SDL_RaiseWindow(g_window);
    SDL_PumpEvents();
    int bw, bh, dw, dh;
    window_sizes(&bw, &bh, &dw, &dh);
    if (dw <= 0 || dh <= 0) {
        fprintf(stderr, RECOMP_APP_NAME ": the window has no drawable size\n");
        return 3;
    }
    host_present_start(g_surface, dw, dh);
    // Said out loud, because "the keyboard does nothing" and "the window
    // never gained focus" look identical from the outside.
    printf(RECOMP_APP_NAME ": window %dx%d points, %dx%d pixels, focus %s\n", bw, bh, dw, dh,
           window_focused() ? "yes" : "no");
    fflush(stdout);

    BootOptions options;
    options.name = "windowed";
    options.exe = exe.c_str();
    // This host has a real window with real focus, so activation is
    // delivered from the events that carry it rather than synthesised.
    options.activate = false;
    options.tick = pump;
    // The scheduler drains the queue itself when nothing is runnable, so
    // a guest blocked on an event an input message signals is not waiting
    // for a pump that only its own clock read would trigger.
    sched_set_input_queue(input_pending, drain_input);
    options.idle_wait = idle_wait;
    options.report = report;
    // No run deadline: the run ends when the user closes the window. The
    // watchdog still backs the close, so a guest that ignores WM_CLOSE
    // cannot leave a window on screen with nothing behind it.
    options.deadline_seconds = 0.0;
    options.close_unwind_grace = 15.0;
    options.close_watchdog_grace = 30.0;

    if (!boot_load(options)) {
        host_present_stop();
        fprintf(stderr, RECOMP_APP_NAME ": %s\n", loader_error());
        return 2;
    }
    printf(RECOMP_APP_NAME ": %s, entry %08x\n", loader_exe_path().c_str(), loader_entry_point());
    fflush(stdout);

    // The music's synth, built here and not later. midiOutOpen arrives on a
    // guest thread holding the cooperative scheduler baton, which stops
    // every other guest thread until it returns, so a SoundFont parsed
    // there would freeze the game at the moment the music starts.
    host_midi_startup(win32_midi_soundfont_path().c_str());

    // RECOMP_HOST_AUDIO_CAPTURE=<path.wav> writes the mixer's own output for
    // the whole run.
    const char *capture = recomp_env("HOST_AUDIO_CAPTURE");
    if (capture && *capture)
        host_audio_capture_begin(capture);

    boot_run();
    if (!sched_guest_threads_stopped()) {
        fprintf(stderr,
                "presenter: guest workers outlived shutdown; refusing unsafe presenter join\n");
        fflush(nullptr);
        _Exit(4);
    }
    host_present_stop(); // scheduler has stopped; join outside the guest baton
    host_audio_capture_end();
    boot_report_lock();
    report(stdout, false);
    boot_report_unlock();
    // Whatever else happens, the user gets their cursor back.
    apply_pointer_capture(false);
    SDL_HideWindow(g_window);
    gpu::release_window_surface(g_surface);
    SDL_DestroyWindow(g_window);
    // The audio device goes before the SDL it runs on, not in the exit
    // handlers after it: there its stream is destroyed through a freed mutex.
    host_audio_shutdown();
    SDL_Quit();
    platform_ui_process_exit(0);
    return 0;
}

extern "C" int host_display_screen_size(int *w, int *h) {
    const int sw = g_screen_w.load(), notch = g_screen_notch.load();
    const int sh = g_screen_h.load() - (notch > 0 ? notch : 0);
    if (!w || !h || sw <= 0 || sh <= 0)
        return 0;
    // Never a portrait screen: the game's image goes above the controls.
    host_landscape_screen_size(sw, sh, w, h);
    return 1;
}

// NFSU1 run 8: the presenter's pacing reads the window screen's refresh (metal_surface.mm refresh_period; a weak
// default there answers 0 in binaries without this host).
extern "C" double host_window_refresh_hz(void) {
    return g_window_hz.load();
}
