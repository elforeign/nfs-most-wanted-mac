// input_gate.cpp - see input_gate.h.
#include "input_gate.h"
#include "game_config.h"
#include "../runtime/mods_seam.h"
#include "../runtime/win32.h"
#include "../runtime/guest.h"
#include "../runtime/display_seam.h"
#include "../runtime/native_seam.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <algorithm>
#include <cmath>
#include <mutex>
#include <utility>
#include "../platform/os.h"

// Drawable pixels at either edge that still count as the edge row/column: an
// iPadOS pointer stops half a point short of the left edge (drawable x 1), and
// the last window point lands a pixel or two short of the last drawable row.
constexpr int kEdgeSlackPx = 3;

namespace {

enum : uint32_t {
    WM_KEYDOWN_ = 0x0100,
    WM_KEYUP_ = 0x0101,
    WM_CHAR_ = 0x0102,
    WM_SYSKEYDOWN_ = 0x0104,
    WM_SYSKEYUP_ = 0x0105,
};

// What the GUEST believes, by virtual key: set only when a message was
// actually posted for it.
bool g_guest_key[256];
// What the KEYBOARD is doing, by virtual key, for the modifier sides only.
// Kept apart from the above so a consumed press still produces a transition
// when it comes up.
bool g_phys_mod[256];
// Shift 1, control 2, alt 4, counting only modifiers the guest was told about.
uint8_t g_guest_modifiers;

void post(uint32_t msg, uint32_t wparam, uint32_t lparam) {
    // Keyboard input is addressed to the focus, not to a window the host
    // picked: the runtime knows which window has it, and in a VCL application
    // the first window created is the invisible application one, which does
    // nothing with a keystroke. Mouse messages from this path carry a guest
    // position, so they are routed by it.
    if (msg >= 0x0100 && msg <= 0x0109) {
        host_post_key_message(msg, wparam, lparam);
        return;
    }
    if (msg >= 0x0200 && msg <= 0x0209) {
        host_post_client_mouse_message(host_main_window(), msg, wparam, int16_t(lparam),
                                       int16_t(lparam >> 16));
        return;
    }
    uint32_t hwnd = host_main_window();
    if (hwnd)
        host_post_message(hwnd, msg, wparam, lparam);
}

// `sys` decides WM_SYSKEYDOWN/UP against WM_KEYDOWN/UP and is about whether
// Alt was down when the keystroke happened; `context` is lParam bit 29 and is
// about whether Alt is down now. Releasing Alt itself is the case where they
// differ: Win32 sends WM_SYSKEYUP, with the context bit clear because Alt is
// no longer held.
void post_key(HostKeyMapping m, bool down, bool sys, bool context, uint32_t character) {
    if (!m.vk)
        return;
    bool was_down = g_guest_key[m.vk];
    g_guest_key[m.vk] = down;
    uint32_t lparam = host_key_lparam(m, down, context, down ? was_down : true);
    post(down ? (sys ? WM_SYSKEYDOWN_ : WM_KEYDOWN_) : (sys ? WM_SYSKEYUP_ : WM_KEYUP_), m.vk,
         lparam);
    // TranslateMessage owns character generation. Posting WM_CHAR here too
    // delivers each character twice to a normal Win32 message loop.
    (void)character;
}

// The modifier bit a scan code contributes: either side counts, which is what
// a game reading GetAsyncKeyState(VK_SHIFT) has to see. Caps lock contributes
// nothing, as host_modifier_bits also has it.
uint8_t bit_for_dik(uint8_t dik) {
    switch (dik) {
    case 0x2a:
    case 0x36:
        return 1; // DIK_LSHIFT, DIK_RSHIFT
    case 0x1d:
    case 0x9d:
        return 2; // DIK_LCONTROL, DIK_RCONTROL
    case 0x38:
    case 0xb8:
        return 4; // DIK_LMENU, DIK_RMENU
    default:
        return 0;
    }
}

// Rebuilt from delivered state rather than accumulated, so it cannot drift.
void recompute_guest_modifiers() {
    uint8_t bits = 0;
    for (int i = 0; i < host_modifier_key_count(); ++i) {
        HostKeyMapping m = host_key_mapping(host_modifier_key(i));
        if (m.vk && g_guest_key[m.vk])
            bits |= bit_for_dik(m.dik);
    }
    g_guest_modifiers = bits;
}

const int kMaxModifiers = 16;

} // namespace

// ---------------------------------------------------------------------------
// Pointer capture: the policy and the arithmetic. See input_gate.h.
// ---------------------------------------------------------------------------
namespace {
bool g_captured = false;
int g_mode_w = 640, g_mode_h = 480;
int32_t g_cursor_x = 320, g_cursor_y = 240;

// Presenter writes one slot; baton takes a value copy of the newest slot.
// The short mutex protects copies only, never callbacks, GPU or AppKit work.
std::mutex g_layout_mutex;
LayoutSnapshot g_layout_slots[3], g_layout;
uint64_t g_layout_serial = 0, g_layout_seen = 0;
HitResult g_drag;
double g_drawable_x = 0, g_drawable_y = 0;
double g_motion_remainder_x = 0, g_motion_remainder_y = 0;
int g_resize_w = 0, g_resize_h = 0;
uint64_t g_resize_serial = 0, g_resize_seen = 0;
bool g_drawable_valid = false;
bool g_window_valid = false;
int32_t g_window_x = 0, g_window_y = 0;
bool g_pointer_target_valid = false;
int32_t g_target_window_x = 0, g_target_window_y = 0;
// Closed-loop health: the pair we last read, how many corrections it has left
// unanswered, and whether this screen is currently on the fallback path.
constexpr int kLoopPatience = 30;
int32_t g_loop_last_x = -1, g_loop_last_y = -1;
int g_loop_unanswered = 0;
bool g_loop_disabled = false;
// Where our last correction should have left the pair, so a value written by
// anyone else is recognisable rather than fought.
bool g_loop_expect_valid = false;
int32_t g_loop_expect_x = 0, g_loop_expect_y = 0;

int g_saved_cursor_right = 0, g_installed_cursor_right = 0;

// A pointer position as a fraction of the frame, mapped into the box where the game draws its menus
// (host_display_ui_box; the whole frame unless a mod set one): NFS Underground draws a 4:3 menu centred in a wide
// frame, and its cursor lives in that menu's coordinates (run 9).
double ui_fraction(double f, int axis) {
    float box[4];
    host_display_ui_box(box);
    const double o = box[axis], w = box[axis + 2];
    return std::clamp((f - o) / (w > 0 ? w : 1.0), 0.0, 1.0);
}

// Compute a damped delta toward the mapped guest pointer target. Resolve guest state
// on every delivery so asynchronous input service and layout changes cannot reuse stale coordinates.
bool pointer_correction(const HitResult &hit, int32_t *dx, int32_t *dy, bool delivery = true) {
    // 00526dd2 is MOV ECX,0xd0595c (B9 immediate), NOT MOV ECX,[...].
    // Resolve a fresh snapshot each delivery; neither the context pointer nor
    // the coordinate values survive into the next delivery.
    const auto p = host_guest_pointer_current();
    if (p.failure != HostGuestPointer::None) {
        const char *reason = host_guest_pointer_failure_name(p.failure);
        const std::string key = std::string("host.pointer.") + reason;
        log_once(key.c_str(),
                 "host: guest pointer unavailable: reason=%s arena=%s size=0x%08x "
                 "object=0x%08x vtable=0x%08x context=0x%08x xy=(%d,%d) bounds=(%d,%d,%d,%d); "
                 "using window position differences (unread fields are zero)",
                 reason, p.arena_available ? "present" : "null", p.arena_size, p.object, p.vtable,
                 p.context, p.x, p.y, p.left, p.top, p.right, p.bottom);
        return false;
    }
    log_once("host.pointer.ready",
             "host: guest pointer ready: reason=ready arena=present size=0x%08x "
             "object=0x%08x pair=0x%08x vtable=0x%08x context=0x%08x "
             "xy=(%d,%d) bounds=(%d,%d,%d,%d); using closed-loop correction",
             p.arena_size, p.object, p.x_addr, p.vtable, p.context, p.x, p.y, p.left, p.top,
             p.right, p.bottom);
    // DAMPED correction. The guest applies our delta with its own gain and on
    // its own schedule (0052ceda waits up to 200 ms) while DirectInput is
    // polled every frame, so asking for the whole remaining difference on
    // every poll overshoots, then overshoots back: the cursor a user sees
    // "constantly moving". Asking for half of it converges for any guest gain
    // below two and cannot oscillate, and a minimum of one pixel toward the
    // target means it still arrives exactly rather than stalling a few pixels
    // short. Nothing is asked for once the two agree.
    auto step = [](int64_t want) {
        if (want == 0)
            return int64_t(0);
        const int64_t half = want / 2; // truncates toward zero
        const int64_t moved = half != 0 ? half : (want > 0 ? 1 : -1);
        return std::clamp(moved, int64_t(-64), int64_t(64));
    };
    // The hit's guest coordinate belongs to the layout's own domain, which for
    // the compatibility full-frame input the app currently publishes is the
    // DRAWABLE, not the game's 640x480-class screen. The game's cursor lives
    // in its screen's coordinates, so normalise the target into that space
    // before differencing, or every window pixel beyond the mode reads as a
    // target below and right of the cursor and the correction drives it into
    // the bottom-right corner - the drift the user saw.
    // The target is the PHYSICAL pointer, scaled once into the game's screen.
    // Deriving it from the hit instead makes it depend on whatever element the
    // pointer is over: a HUD hit reports an absolute guest coordinate while the
    // compatibility full-frame scene reports a window pixel, and alternating
    // between the two on successive polls is what made the cursor jump about.
    // One monotone mapping of one position cannot disagree with itself.
    const int32_t screen_w = p.right - p.left + 1, screen_h = p.bottom - p.top + 1;
    // An out-of-frame virtual pointer cannot name a target: correcting from it
    // would aim at whichever edge it has run past.
    if (!std::isfinite(g_drawable_x) || !std::isfinite(g_drawable_y) || g_drawable_x < 0 ||
        g_drawable_y < 0 || g_drawable_x > g_layout.drawable_w ||
        g_drawable_y > g_layout.drawable_h) {
        log_once("host.pointer.offframe",
                 "host: the window pointer is outside the frame (%.0f,%.0f of %dx%d); "
                 "no correction until it returns",
                 g_drawable_x, g_drawable_y, g_layout.drawable_w, g_layout.drawable_h);
        return false;
    }
    // R3 run 17 (D-119, the pointer offset after a resize to another aspect ratio): through the layout's scene
    // rectangle, not the whole drawable. A Classic or letterboxed layout draws the game inside bars once the window's
    // aspect differs from the mode's; spanning the whole drawable aimed the cursor at the window fraction while the
    // picture (and WM_MOUSEMOVE, from the same scene) sat inside the bars. The compatibility full-frame layout
    // (scale 1, offset 0, domain = the drawable) gives exactly the old physical mapping.
    const SceneMapping &sc = g_layout.scene;
    const bool scene_ok = std::isfinite(sc.scale_x) && sc.scale_x > 0 && std::isfinite(sc.scale_y) &&
                          sc.scale_y > 0 && std::isfinite(sc.offset_x) && std::isfinite(sc.offset_y);
    const double dom_w = scene_ok && sc.domain_w > 0 ? sc.domain_w : g_layout.drawable_w;
    const double dom_h = scene_ok && g_layout.guest_h > 0 ? g_layout.guest_h : g_layout.drawable_h;
    const double px = scene_ok ? (g_drawable_x - sc.offset_x) / sc.scale_x : g_drawable_x;
    const double py = scene_ok ? (g_drawable_y - sc.offset_y) / sc.scale_y : g_drawable_y;
    const double span_x = std::max(1.0, dom_w - 1);
    const double span_y = std::max(1.0, dom_h - 1);
    int64_t tx = p.left + int64_t(std::lround(ui_fraction(std::clamp(px, 0.0, span_x) / span_x, 0) * (screen_w - 1)));
    int64_t ty = p.top + int64_t(std::lround(ui_fraction(std::clamp(py, 0.0, span_y) / span_y, 1) * (screen_h - 1)));
    // Enhanced composition publishes an explicit scene/HUD input domain.
    // Use that inverse mapping so the expanded world and fixed sidebar agree;
    // Classic retains main's physical-pointer mapping and loop health checks.
    if (!g_layout.classic && g_layout.cls == HOST_SCREEN_GAMEPLAY) {
        tx = hit.gx;
        ty = hit.gy;
    }
    tx = std::clamp(tx, int64_t(p.left), int64_t(p.right));
    ty = std::clamp(ty, int64_t(p.top), int64_t(p.bottom));
    // The loop is only meaningful while the game is integrating OUR deltas
    // into the pair we read. On some screens it is not: the front end draws a
    // cursor from a different variable, and then correcting toward a pair that
    // never answers drives the cursor wherever the target happens to lie. So
    // watch for that: if the guest pair does not move while corrections are
    // being asked for, hand the screen back to plain position differencing and
    // re-arm the moment the pair moves by itself.
    // ANOTHER WRITER. The front end moves its cursor from the mouse messages
    // the host posts, not only from DirectInput deltas, so the pair can jump
    // to a value we never asked for. Correcting against that in the same
    // instant means two writers fighting, which is what a user sees as the
    // cursor snapping about. When the pair did not land where our last
    // correction would have put it, adopt what we find and correct nothing
    // this round.
    const bool foreign = g_loop_expect_valid && (std::abs(p.x - g_loop_expect_x) > 2 ||
                                                 std::abs(p.y - g_loop_expect_y) > 2);
    // Keep the small deadband for ordinary pointing, but reach actual screen
    // edges exactly. The original camera input (004adbb0) requires y<1 for
    // upward scrolling and y>=screen_height-1 for downward scrolling; stopping
    // at row 4 leaves the pointer visibly at the top without ever panning.
    const bool scene_edge =
        g_layout.cls == HOST_SCREEN_GAMEPLAY && hit.kind == HitResult::HIT_SCENE;
    const bool exact_x =
        scene_edge && (g_drawable_x <= kEdgeSlackPx || g_drawable_x >= span_x - kEdgeSlackPx);
    const bool exact_y =
        scene_edge && (g_drawable_y <= kEdgeSlackPx || g_drawable_y >= span_y - kEdgeSlackPx);
    const bool close =
        std::abs(tx - p.x) <= (exact_x ? 0 : 4) && std::abs(ty - p.y) <= (exact_y ? 0 : 4);
    if (!delivery) {
        // An idle wake only asks whether DirectInput should sample again.
        // Recording an expected position here would pretend its delta was
        // delivered: the real poll then sees the unchanged cursor as a foreign
        // write and cancels the motion. Keep wakeups observational.
        if (g_loop_disabled && p.x == g_loop_last_x && p.y == g_loop_last_y)
            return false;
        *dx = close ? 0 : int32_t(step(tx - p.x));
        *dy = close ? 0 : int32_t(step(ty - p.y));
        return true;
    }
    if (foreign || close) {
        g_loop_expect_valid = true;
        g_loop_expect_x = p.x;
        g_loop_expect_y = p.y;
        g_loop_last_x = p.x;
        g_loop_last_y = p.y;
        g_loop_unanswered = 0;
        g_loop_disabled = false;
        *dx = *dy = 0;
        return true;
    }
    const int32_t want_x = int32_t(step(tx - p.x)), want_y = int32_t(step(ty - p.y));
    g_loop_expect_valid = true;
    g_loop_expect_x = p.x + want_x;
    g_loop_expect_y = p.y + want_y;
    if (p.x != g_loop_last_x || p.y != g_loop_last_y) {
        g_loop_last_x = p.x;
        g_loop_last_y = p.y;
        g_loop_unanswered = 0;
        g_loop_disabled = false;
    } else if (want_x || want_y) {
        if (++g_loop_unanswered >= kLoopPatience && !g_loop_disabled) {
            g_loop_disabled = true;
            log_once("host.pointer.unanswered",
                     "host: the guest pointer pair at 0x%08x did not move after %d "
                     "corrections (still (%d,%d)); this screen keeps window position "
                     "differences until it answers again",
                     p.object, kLoopPatience, p.x, p.y);
        }
    }
    if (g_loop_disabled)
        return false;
    *dx = want_x;
    *dy = want_y;
    return true;
}

// Adopt the latest published layout/resize snapshot on the input side. Rescale the
// virtual pointer only when drawable dimensions change, not when scene mapping changes.
void take_layout() {
    LayoutSnapshot next;
    int w, h;
    {
        std::lock_guard lock(g_layout_mutex);
        if (g_layout_serial == g_layout_seen && g_resize_serial == g_resize_seen)
            return;
        next = g_layout_serial ? g_layout_slots[g_layout_serial % 3] : g_layout;
        w = g_resize_w;
        h = g_resize_h;
        g_layout_seen = g_layout_serial;
        g_resize_seen = g_resize_serial;
    }
    if (w > 0 && h > 0 && (next.drawable_w != w || next.drawable_h != h))
        next = compositor_resize_layout(next, w, h);
    // Geometry changes must not masquerade as mouse motion. The virtual pointer
    // lives in DRAWABLE pixels, so only a change of drawable SIZE moves it, and
    // then in proportion. Rescaling by the scene mapping instead made it jump
    // whenever the presenter alternated between a scene layout and the
    // full-frame compatibility layout, since those differ by the scene scale:
    // the pointer bounced between two positions and dragged the game's cursor
    // with it. A scene change with the same drawable leaves the pointer alone.
    if (g_captured && g_drawable_valid && g_layout.drawable_w > 1 && g_layout.drawable_h > 1 &&
        next.drawable_w > 1 && next.drawable_h > 1 &&
        (next.drawable_w != g_layout.drawable_w || next.drawable_h != g_layout.drawable_h)) {
        g_drawable_x = g_drawable_x / (g_layout.drawable_w - 1) * (next.drawable_w - 1);
        g_drawable_y = g_drawable_y / (g_layout.drawable_h - 1) * (next.drawable_h - 1);
    }
    if (next.drawable_w != g_layout.drawable_w || next.drawable_h != g_layout.drawable_h) {
        g_window_valid = false;
        g_pointer_target_valid = false;
        if (!g_captured)
            g_drawable_valid = false; // Old backing coordinates cannot seed a delta.
    }
    g_layout = std::move(next);
    // A rescale between layouts must not move the pointer outside the frame:
    // a stale or extreme scale otherwise walks it off the drawable, and every
    // target computed from it then reads as the bottom-right corner.
    if (next.drawable_w > 0 && next.drawable_h > 0) {
        g_drawable_x = std::clamp(g_drawable_x, 0.0, double(next.drawable_w - 1));
        g_drawable_y = std::clamp(g_drawable_y, 0.0, double(next.drawable_h - 1));
    }
    if (!std::isfinite(g_drawable_x) || !std::isfinite(g_drawable_y)) {
        g_drawable_valid = false;
        g_drawable_x = g_drawable_y = 0;
    }
}
int32_t pixel(double v) {
    return int32_t(std::clamp(std::floor(v), double(INT32_MIN), double(INT32_MAX)));
}
bool contains(const LayoutRect &r, double x, double y) {
    return x >= r.x && y >= r.y && x < double(r.x) + r.w && y < double(r.y) + r.h;
}
HitResult map_owner(HitResult hit, double x, double y) {
    if (hit.kind == HitResult::HIT_ELEMENT) {
        hit.gx = pixel(hit.guest.x + (x - hit.drawable.x) * hit.guest.w / hit.drawable.w);
        hit.gy = pixel(hit.guest.y + (y - hit.drawable.y) * hit.guest.h / hit.drawable.h);
    } else if (hit.kind == HitResult::HIT_SCENE) {
        hit.gx = pixel(
            std::clamp((x - hit.scene.offset_x) / hit.scene.scale_x, 0.0, double(hit.guest.w - 1)));
        hit.gy = pixel(
            std::clamp((y - hit.scene.offset_y) / hit.scene.scale_y, 0.0, double(hit.guest.h - 1)));
    }
    return hit;
}
HitResult hit_layout(const LayoutSnapshot &layout, double x, double y) {
    if (g_drag.kind != HitResult::HIT_NONE)
        return map_owner(g_drag, x, y);
    if (layout.drawable_w <= 0 || layout.drawable_h <= 0 || x < 0 || y < 0 ||
        x >= layout.drawable_w || y >= layout.drawable_h)
        return {};
    const LayoutElement *top = nullptr;
    for (const auto &e : layout.elements)
        if (!e.is_cursor && contains(e.drawable, x, y) && (!top || e.last_seq >= top->last_seq))
            top = &e;
    if (top) {
        HitResult h;
        h.kind = HitResult::HIT_ELEMENT;
        h.element = top->id;
        h.guest = top->guest;
        h.drawable = top->drawable;
        return map_owner(h, x, y);
    }
    HitResult h;
    h.kind = HitResult::HIT_SCENE;
    h.scene = layout.scene;
    h.guest = {0, 0, layout.scene.domain_w, layout.guest_h};
    h.drawable = {0, 0, layout.drawable_w, layout.drawable_h};
    h = map_owner(h, x, y);
    // The final drawable pixels must reach the guest edge even at 4x scale.
    // Pillarbox boundaries inside the drawable are not scrolling boundaries.
    // A pointer or finger at the window's last point lands a pixel or two
    // short of the drawable's last row once scaled (1666 of 1668 on an
    // iPad), so the last kEdgeSlackPx pixels all count as the edge.
    if (x <= kEdgeSlackPx)
        h.gx = 0;
    else if (x >= layout.drawable_w - 1 - kEdgeSlackPx)
        h.gx = h.guest.w - 1;
    else if (h.guest.w > 2)
        h.gx = std::clamp(h.gx, 1, h.guest.w - 2);
    if (y <= kEdgeSlackPx)
        h.gy = 0;
    else if (y >= layout.drawable_h - 1 - kEdgeSlackPx)
        h.gy = h.guest.h - 1;
    else if (h.guest.h > 2)
        h.gy = std::clamp(h.gy, 1, h.guest.h - 2);
    return h;
}

int32_t clamp_to(int32_t v, int32_t hi) {
    if (v < 0)
        return 0;
    if (v > hi)
        return hi;
    return v;
}
} // namespace

const char *host_guest_pointer_failure_name(HostGuestPointer::Failure failure) {
    switch (failure) {
    case HostGuestPointer::None:
        return "ready";
    case HostGuestPointer::ArenaUnavailable:
        return "arena-unavailable";
    case HostGuestPointer::ObjectNull:
        return "object-null";
    case HostGuestPointer::ObjectOutsideArena:
        return "object-outside-arena";
    case HostGuestPointer::VtableMismatch:
        return "vtable-mismatch";
    case HostGuestPointer::BoundsInvalid:
        return "bounds-invalid";
    case HostGuestPointer::CoordinatesOutsideBounds:
        return "coordinates-outside-bounds";
    }
    return "unknown-failure";
}

HostGuestPointer host_guest_pointer_resolve(const uint8_t *arena, uint32_t size, uint32_t object) {
    HostGuestPointer p;
    p.arena_available = arena != nullptr;
    p.arena_size = size;
    p.object = object;
    auto fail = [&](HostGuestPointer::Failure why) {
        p.failure = why;
        return p;
    };
    auto valid = [&](uint32_t address, uint32_t length) {
        return address < size && length <= size - address;
    };
    if (!arena)
        return fail(HostGuestPointer::ArenaUnavailable);
    if (!object)
        return fail(HostGuestPointer::ObjectNull);
    if (!valid(object, 0x48))
        return fail(HostGuestPointer::ObjectOutsideArena);
    auto read = [&](uint32_t offset) {
        uint32_t value;
        memcpy(&value, arena + object + offset, sizeof value);
        return value;
    };
    // Read all relevant fields inside the checked object range for diagnostics.
    // +0x1c is optional: report its value without following or validating it.
    p.vtable = read(0);
    p.context = read(0x1c);
    p.x = int32_t(read(0x20));
    p.y = int32_t(read(0x24));
    p.left = int32_t(read(0x38));
    p.top = int32_t(read(0x3c));
    p.right = int32_t(read(0x40));
    p.bottom = int32_t(read(0x44));
    // The bounds are the game's own screen rectangle, which follows the display
    // mode: 640x480 today, 800x600 in Classic. Cap generously rather than at the
    // boot mode, or Classic falls back to position differencing for the run.
    // 0052cbe0 installs this vtable. This is a structural startup guard, not
    // a correctness gate on input attachment or the game's current state.
    p.x_addr = object + 0x20;
    p.y_addr = object + 0x24;
    if (p.vtable != RECOMP_HOOK_MOUSE_VTABLE)
        return fail(HostGuestPointer::VtableMismatch);
    if (!(p.left >= 0 && p.top >= 0 && p.right > p.left && p.bottom > p.top && p.right < 4096 &&
          p.bottom < 4096))
        return fail(HostGuestPointer::BoundsInvalid);
    if (!(p.x >= p.left && p.x <= p.right && p.y >= p.top && p.y <= p.bottom))
        return fail(HostGuestPointer::CoordinatesOutsideBounds);
    return p;
}

HostGuestPointer host_guest_pointer_resolve_pair(const uint8_t *arena, uint32_t size, uint32_t guard, uint32_t x_addr,
                                                 uint32_t y_addr, int32_t mode_w, int32_t mode_h) {
    HostGuestPointer p;
    p.arena_available = arena != nullptr;
    p.arena_size = size;
    p.object = guard;
    p.x_addr = x_addr;
    p.y_addr = y_addr;
    auto fail = [&](HostGuestPointer::Failure why) {
        p.failure = why;
        return p;
    };
    auto valid = [&](uint32_t address) { return address && address < size && 4 <= size - address; };
    auto read = [&](uint32_t address) {
        uint32_t value;
        memcpy(&value, arena + address, sizeof value);
        return value;
    };
    if (!arena)
        return fail(HostGuestPointer::ArenaUnavailable);
    if (!valid(guard) || !valid(x_addr) || !valid(y_addr))
        return fail(HostGuestPointer::ObjectOutsideArena);
    p.vtable = read(guard); // the guard's value, reported where the object form reports its vtable
    p.x = int32_t(read(x_addr));
    p.y = int32_t(read(y_addr));
    if (!p.vtable)
        return fail(HostGuestPointer::ObjectNull); // the game has not made its mouse yet
    if (!(mode_w > 1 && mode_h > 1 && mode_w < 4096 && mode_h < 4096))
        return fail(HostGuestPointer::BoundsInvalid);
    p.right = mode_w - 1;
    p.bottom = mode_h - 1;
    if (!(p.x >= 0 && p.x <= mode_w && p.y >= 0 && p.y <= mode_h))
        return fail(HostGuestPointer::CoordinatesOutsideBounds);
    return p;
}

HostGuestPointer host_guest_pointer_current() {
#ifdef RECOMP_HOOK_MOUSE_PAIR_X
    // The pair's bounds are the frame the game draws, as the presenter last published it: a Direct3D 9 game reports
    // no display mode, so g_mode stayed 640x480 while NFS Underground draws 800x600 and the cursor could not reach
    // the right or bottom of a menu (run 8). The display mode until the first frame.
    const bool drawn = g_layout.guest_w > 1 && g_layout.guest_h > 1;
#ifdef RECOMP_HOOK_MOUSE_PAIR_SPACE_W
    // A game whose cursor lives in its own fixed space whatever it draws ([hooks] mouse_pair_space_w/_h): NFS
    // Underground's menus read the pair as 640x480 units at every resolution (run 9: at 800x600 the cursor value
    // 440,260 is drawn at pixel 555,330), so frame-pixel bounds ran the cursor off the menu's right and bottom.
    (void)drawn;
    return host_guest_pointer_resolve_pair(g_mem, GUEST_SIZE, RECOMP_HOOK_MOUSE_PAIR_GUARD, RECOMP_HOOK_MOUSE_PAIR_X,
                                           RECOMP_HOOK_MOUSE_PAIR_Y, RECOMP_HOOK_MOUSE_PAIR_SPACE_W,
                                           RECOMP_HOOK_MOUSE_PAIR_SPACE_H);
#else
    return host_guest_pointer_resolve_pair(g_mem, GUEST_SIZE, RECOMP_HOOK_MOUSE_PAIR_GUARD, RECOMP_HOOK_MOUSE_PAIR_X,
                                           RECOMP_HOOK_MOUSE_PAIR_Y, drawn ? g_layout.guest_w : g_mode_w,
                                           drawn ? g_layout.guest_h : g_mode_h);
#endif
#else
    return host_guest_pointer_resolve(g_mem, GUEST_SIZE, RECOMP_HOOK_MOUSE_DEVICE_PTR);
#endif
}

void host_pointer_set_mode(int w, int h) {
    if (w > 0)
        g_mode_w = w;
    if (h > 0)
        g_mode_h = h;
    // A mode change can leave the cursor outside the new frame.
    g_cursor_x = clamp_to(g_cursor_x, g_mode_w - 1);
    g_cursor_y = clamp_to(g_cursor_y, g_mode_h - 1);
}

void host_pointer_capture(bool captured) {
    if (g_captured == captured)
        return;
    g_captured = captured;
    if (!captured)
        g_drawable_valid = false;
    // Capture changes ownership, not the fractional motion still owed.
    if (!captured)
        host_gate_end_drag();
}
bool host_pointer_captured() {
    return g_captured;
}

void host_pointer_center() {
    g_motion_remainder_x = g_motion_remainder_y = 0;
    g_cursor_x = g_mode_w / 2;
    g_cursor_y = g_mode_h / 2;
    take_layout();
    g_drawable_valid = g_layout.drawable_w > 0 && g_layout.drawable_h > 0;
    if (g_drawable_valid) {
        g_drawable_x = g_layout.drawable_w / 2.0;
        g_drawable_y = g_layout.drawable_h / 2.0;
        auto h = hit_layout(g_layout, g_drawable_x, g_drawable_y);
        g_cursor_x = h.gx;
        g_cursor_y = h.gy;
        compositor_set_pointer_position(true, pixel(g_drawable_x), pixel(g_drawable_y));
    }
}

void host_pointer_cursor(int32_t *x, int32_t *y) {
    if (x)
        *x = g_cursor_x;
    if (y)
        *y = g_cursor_y;
}

bool host_pointer_motion(int32_t dx, int32_t dy) {
    // A delta that arrives while released belongs to the desktop, not to the
    // guest: the pointer is the user's again and moving the guest's cursor
    // with it is the desync this exists to stop.
    if (!g_captured)
        return false;
    int32_t nx = clamp_to(g_cursor_x + dx, g_mode_w - 1);
    int32_t ny = clamp_to(g_cursor_y + dy, g_mode_h - 1);
    if (nx == g_cursor_x && ny == g_cursor_y)
        return false; // against an edge
    g_cursor_x = nx;
    g_cursor_y = ny;
    return true;
}

bool host_gate_key(uint16_t mac, bool down) {
    HostKeyMapping m = host_key_mapping(mac);
    return mods_input_key(m.dik, m.vk, down);
}
uint32_t host_input_batch_limit(const HostInputStep *steps, uint32_t count) {
    if (!steps || count == 0)
        return 0;
    uint8_t pressed = 0;
    for (uint32_t i = 0; i < count; ++i) {
        if (!steps[i].is_button || steps[i].button > 2)
            continue;
        const uint8_t bit = uint8_t(1u << steps[i].button);
        if (steps[i].down) {
            pressed |= bit;
            continue;
        }
        if (pressed & bit)
            return i; // its press is in this batch: the release waits a turn
    }
    return count;
}

bool host_gate_button(int button, bool down, int32_t x, int32_t y) {
    return mods_input_button(button, down, x, y);
}
bool host_gate_motion(int32_t x, int32_t y, int32_t dx, int32_t dy) {
    return mods_input_motion(x, y, dx, dy);
}
bool host_gate_wheel(int32_t dz) {
    return mods_input_wheel(dz);
}

// Presses the menu took: their releases are the menu's too, even when the press closed it.
static uint8_t g_menu_buttons = 0;
bool host_gate_menu_pointer(int32_t x, int32_t y, int button, bool down, int32_t wheel) {
    int px = 0, py = 0, pw = 0, ph = 0;
    float u = -1, v = -1;
    if (compositor_settings_page_drawn(&px, &py, &pw, &ph)) {
        u = float(x - px) / float(pw);
        v = float(y - py) / float(ph);
    }
    const bool taken = mods_menu_pointer(u, v, button, down ? 1 : 0, wheel) != 0;
    if (button < 0 || button >= 8)
        return taken;
    const uint8_t bit = uint8_t(1u << button);
    if (down) {
        if (taken) {
            g_menu_buttons |= bit;
            printf("settings menu: press at %.3f,%.3f of the page\n", u, v);
            fflush(stdout);
        }
        return taken;
    }
    const bool held = (g_menu_buttons & bit) != 0;
    g_menu_buttons &= uint8_t(~bit);
    return taken || held;
}

uint8_t host_guest_modifiers() {
    return g_guest_modifiers;
}
bool host_guest_key_down(uint8_t vk) {
    return g_guest_key[vk];
}

// The guest's own button mask, for the same reason g_guest_key exists: a
// consumed press must not leave the guest believing a button is down.
uint8_t g_guest_buttons = 0;

bool host_gate_inject_guest_click(int32_t gx, int32_t gy, int button, bool down) {
    if (button < 0 || button > 2)
        return false;
    if (gx < 0)
        gx = 0;
    if (gy < 0)
        gy = 0;
    // The decision first, and once: a gate that were asked for the motion and
    // the button separately could consume one and deliver the other.
    if (host_gate_button(button, down, gx, gy))
        return true;
    // Absolute, with no delta. A delta would be relative motion, which is the
    // physical mapping this exists to bypass.
    host_input_motion(gx, gy, 0, 0);
    if (down)
        g_guest_buttons |= (uint8_t)(1u << button);
    else
        g_guest_buttons &= (uint8_t)~(1u << button);
    host_input_button(button, down);
    static const uint32_t kMouseMove = 0x0200;
    static const uint32_t msgs[3][2] = {
        {0x0202, 0x0201}, // WM_LBUTTONUP,   WM_LBUTTONDOWN
        {0x0205, 0x0204}, // WM_RBUTTONUP,   WM_RBUTTONDOWN
        {0x0208, 0x0207}, // WM_MBUTTONUP,   WM_MBUTTONDOWN
    };
    const uint32_t lparam = ((uint32_t)(gy & 0xffff) << 16) | (uint32_t)(gx & 0xffff);
    const uint32_t wparam = host_mouse_wparam(g_guest_buttons, host_guest_modifiers());
    // The move first, so a window that reads the position from the move rather
    // than from the click's lParam sees the same place.
    post(kMouseMove, wparam, lparam);
    post(msgs[button][down ? 1 : 0], wparam, lparam);
    return false;
}

bool host_key_event(uint16_t mac, bool down, uint32_t character) {
    if (host_gate_key(mac, down))
        return true; // no state, no message
    HostKeyMapping m = host_key_mapping(mac);
    host_input_key(mac, down);
    // Alt AS THE GUEST KNOWS IT. An ordinary key event does not change the
    // modifiers, so "was Alt down" and "is Alt down" are the same question
    // here - but a consumed Alt never reached the guest, and must not change
    // how this key is classified.
    bool alt = (g_guest_modifiers & 4) != 0;
    post_key(m, down, alt, alt, character);
    return false;
}

// Translate physical modifier transitions through the input filter into guest key events.
// Track physical and delivered states separately so consumed presses still receive releases.
void host_modifier_event(uint32_t flags) {
    const int n =
        host_modifier_key_count() < kMaxModifiers ? host_modifier_key_count() : kMaxModifiers;
    HostKeyMapping map[kMaxModifiers];
    bool down_now[kMaxModifiers], deliver[kMaxModifiers];

    // Pass 1: PHYSICAL transitions decide what is offered, and the filter
    // decides what the guest gets. Physical, not delivered: a consumed press
    // has to be seen coming up too, or the filter is never told the key was
    // let go and answers every later press as a repeat.
    for (int i = 0; i < n; ++i) {
        uint16_t mac = host_modifier_key(i);
        map[i] = host_key_mapping(mac);
        down_now[i] = host_modifier_side_down(flags, mac);
        deliver[i] = false;
        if (!map[i].vk || g_phys_mod[map[i].vk] == down_now[i])
            continue;
        g_phys_mod[map[i].vk] = down_now[i];
        deliver[i] = !host_gate_key(mac, down_now[i]);
    }

    // The guest's Alt before this event and after it, counting only what it
    // will actually be told. Releasing Alt is where the two differ, and it is
    // why that release is a WM_SYSKEYUP rather than a plain WM_KEYUP.
    bool alt_before = (g_guest_modifiers & 4) != 0;
    bool alt_now = false;
    for (int i = 0; i < n; ++i) {
        if (!map[i].vk || bit_for_dik(map[i].dik) != 4)
            continue;
        bool after = deliver[i] ? down_now[i] : g_guest_key[map[i].vk];
        if (after)
            alt_now = true;
    }

    // Pass 2: deliver, one key at a time. host_input_modifiers sets every
    // modifier at once and a consumed one must not be among them, which is
    // why the diff is here rather than inside it.
    for (int i = 0; i < n; ++i) {
        if (!deliver[i])
            continue;
        host_input_key(host_modifier_key(i), down_now[i]);
        post_key(map[i], down_now[i], alt_before || alt_now, alt_now, 0);
    }
    recompute_guest_modifiers();
}

// The same diff host_modifier_event runs. Named separately because the reason
// to call it is different - recovering state nobody told us about, rather than
// reacting to a change we were told about - and the call sites should say
// which they are doing.
void host_gate_sync_modifiers(uint32_t flags) {
    host_modifier_event(flags);
}

void host_gate_release_all() {
    // Nothing that was down can be seen coming up while another application
    // has the focus, so it all comes up now - on BOTH sides. A filter left
    // holding a consumed key would answer the next press as a repeat and
    // never ask its callbacks again.
    host_input_release_all();
    mods_input_release_all();
    memset(g_guest_key, 0, sizeof g_guest_key);
    memset(g_phys_mod, 0, sizeof g_phys_mod);
    g_guest_modifiers = 0;
    // Focus loss releases the pointer as well: the window no longer owns it,
    // and the cursor has to come back so the user can reach anything else.
    g_captured = false;
    g_window_valid = false;
    g_pointer_target_valid = false;
    g_drawable_valid = false;
    g_motion_remainder_x = g_motion_remainder_y = 0;
    g_guest_buttons = 0;
    host_gate_end_drag();
    compositor_set_pointer_position(false, 0, 0);
}

void host_gate_reset() {
    if (g_installed_cursor_right && g_mem && gm_valid(RECOMP_HOOK_MOUSE_DEVICE_RIGHT, 4) &&
        int32_t(rd32(RECOMP_HOOK_MOUSE_DEVICE_RIGHT)) == g_installed_cursor_right)
        wr32(RECOMP_HOOK_MOUSE_DEVICE_RIGHT, g_saved_cursor_right);
    g_saved_cursor_right = g_installed_cursor_right = 0;
    memset(g_guest_key, 0, sizeof g_guest_key);
    memset(g_phys_mod, 0, sizeof g_phys_mod);
    g_guest_modifiers = 0;
    g_captured = false;
    g_mode_w = 640;
    g_mode_h = 480;
    {
        std::lock_guard lock(g_layout_mutex);
        for (auto &slot : g_layout_slots)
            slot = {};
        g_layout_serial = g_layout_seen = 0;
        g_resize_w = g_resize_h = 0;
        g_resize_serial = g_resize_seen = 0;
    }
    g_layout = {};
    g_drag = {};
    g_drawable_valid = false;
    g_window_valid = false;
    g_pointer_target_valid = false;
    g_guest_buttons = 0;
    // A reset starts a new session: nothing is owed and nothing is stale.
    g_loop_expect_valid = false;
    g_loop_expect_x = g_loop_expect_y = 0;
    g_loop_last_x = g_loop_last_y = -1;
    g_loop_unanswered = 0;
    g_loop_disabled = false;
    compositor_set_pointer_position(false, 0, 0);
    host_pointer_center();
}

void host_gate_set_layout(const CompositorInput *in) {
    auto snapshot = compositor_layout_snapshot(in);
    host_gate_publish_layout(snapshot);
}
void host_gate_publish_layout(const LayoutSnapshot &snapshot) {
    std::lock_guard lock(g_layout_mutex);
    g_layout_slots[(g_layout_serial + 1) % 3] = snapshot;
    ++g_layout_serial;
}
void host_gate_fallback_layout(int w, int h) {
    take_layout();
    if (g_layout_seen || w <= 0 || h <= 0)
        return;
    double scale = std::min(double(w) / g_mode_w, double(h) / g_mode_h);
    g_layout.drawable_w = w;
    g_layout.drawable_h = h;
    g_layout.guest_w = g_mode_w;
    g_layout.guest_h = g_mode_h;
    g_layout.scene = {float(scale), float(scale), float((w - g_mode_w * scale) / 2),
                      float((h - g_mode_h * scale) / 2), g_mode_w};
}
HitResult host_gate_hit_test(const CompositorInput *in, int32_t x, int32_t y) {
    if (in)
        return hit_layout(compositor_layout_snapshot(in), x, y);
    take_layout();
    return hit_layout(g_layout, x, y);
}
void host_gate_begin_drag(const HitResult *owner) {
    if (g_drag.kind != HitResult::HIT_NONE)
        return;
    if (owner && owner->kind != HitResult::HIT_NONE && owner->guest.w > 0 && owner->guest.h > 0 &&
        owner->drawable.w > 0 && owner->drawable.h > 0)
        g_drag = *owner;
}
void host_gate_end_drag() {
    g_drag = {};
}
// Map native pointer motion through the current layout to guest input and compositor state.
// Captured motion uses relative deltas; free motion uses positions within the drawable.
HitResult host_gate_pointer_event(int32_t x, int32_t y, double dx, double dy, int32_t *guest_dx,
                                  int32_t *guest_dy) {
    *guest_dx = *guest_dy = 0;
    take_layout();
    if (g_layout.drawable_w <= 0 || g_layout.drawable_h <= 0)
        return {};
    if (!g_captured && (x < 0 || y < 0 || x >= g_layout.drawable_w || y >= g_layout.drawable_h)) {
        g_drawable_valid = false;
        return {};
    }
    const bool first = !g_drawable_valid;
    if (first) {
        g_drawable_x =
            g_captured ? g_cursor_x * double(g_layout.scene.scale_x) + g_layout.scene.offset_x : x;
        g_drawable_y =
            g_captured ? g_cursor_y * double(g_layout.scene.scale_y) + g_layout.scene.offset_y : y;
        g_drawable_valid = true;
    }
    const double old_x = g_drawable_x, old_y = g_drawable_y;
    const auto before = hit_layout(g_layout, old_x, old_y);
    // Free motion comes from positions; captured motion uses the caller delta
    // (window callers derive it from positions). Never difference against g_cursor:
    // that position may belong to a previous layout, clamp or hit owner.
    dx = g_captured ? (std::isfinite(dx) ? dx : 0) : (first ? 0 : x - old_x);
    dy = g_captured ? (std::isfinite(dy) ? dy : 0) : (first ? 0 : y - old_y);
    g_drawable_x =
        std::clamp(g_captured ? old_x + dx : double(x), 0.0, double(g_layout.drawable_w - 1));
    g_drawable_y =
        std::clamp(g_captured ? old_y + dy : double(y), 0.0, double(g_layout.drawable_h - 1));
    auto hit = hit_layout(g_layout, g_drawable_x, g_drawable_y);
    if (hit.kind == HitResult::HIT_NONE)
        return hit;
    double motion_x = dx / g_layout.scene.scale_x, motion_y = dy / g_layout.scene.scale_y;
    const bool anchored =
        g_layout.cls == HOST_SCREEN_GAMEPLAY && !g_layout.legacy && !g_layout.classic;
    if (anchored && before.kind == HitResult::HIT_ELEMENT && hit.kind == HitResult::HIT_ELEMENT &&
        before.element == hit.element) {
        motion_x = dx * hit.guest.w / hit.drawable.w;
        motion_y = dy * hit.guest.h / hit.drawable.h;
    } else if (anchored && (dx != 0 || dy != 0) && before.kind != HitResult::HIT_NONE &&
               (before.kind == HitResult::HIT_ELEMENT || hit.kind == HitResult::HIT_ELEMENT)) {
        // Anchored gameplay HUDs deliberately move between guest regions.
        // Compute that crossing in ONE current layout, never across frames.
        motion_x = double(hit.gx) - before.gx;
        motion_y = double(hit.gy) - before.gy;
    }
    auto accumulate = [](double value, double &remainder) {
        value += remainder;
        // Cancel floating-point noise at an integer boundary (e.g. ten
        // 0.1-pixel moves), without rounding genuine fractional movement.
        const double nearest = std::round(value);
        if (std::abs(value - nearest) < 1e-9)
            value = nearest;
        const double whole = std::trunc(value);
        remainder = value - whole;
        return pixel(whole);
    };
    *guest_dx = accumulate(motion_x, g_motion_remainder_x);
    *guest_dy = accumulate(motion_y, g_motion_remainder_y);
    // Retain the old subpixel ledger for unreadable/startup fallback. The
    // closed-loop target is already an integer guest pixel: carrying its
    // residual forward would count the same unapplied error twice.
    pointer_correction(hit, guest_dx, guest_dy);
    g_cursor_x = hit.gx;
    g_cursor_y = hit.gy;
    compositor_set_pointer_position(true, pixel(g_drawable_x), pixel(g_drawable_y));
    return hit;
}

void host_gate_publish_drawable_size(int w, int h) {
    if (w <= 0 || h <= 0)
        return;
    std::lock_guard lock(g_layout_mutex);
    if (w == g_resize_w && h == g_resize_h)
        return;
    g_resize_w = w;
    g_resize_h = h;
    ++g_resize_serial;
}
bool host_pointer_can_capture(bool key, bool inside, bool click, bool escape, bool page) {
    return key && inside && click && !escape && !page;
}
bool host_pointer_confinement_wanted(bool captured, int window_mode) {
    (void)window_mode;
    return captured;
}
bool host_pointer_at_resize_edge(double x, double y, double w, double h, double margin) {
    return w > 0 && h > 0 && (x < margin || y < margin || x >= w - margin || y >= h - margin);
}
void host_pointer_drawable_position(double *x, double *y) {
    *x = g_drawable_x;
    *y = g_drawable_y;
}

HitResult host_gate_window_pointer(int32_t x, int32_t y, int32_t *dx, int32_t *dy) {
    take_layout();
    // Both capture states use the OS-accelerated window pointer. Device counts
    // are a different gain and must never be mixed with position differences.
    if (g_layout.drawable_w <= 0 || g_layout.drawable_h <= 0) {
        *dx = *dy = 0;
        return {};
    }
    if (!g_captured && (x < 0 || y < 0 || x >= g_layout.drawable_w || y >= g_layout.drawable_h)) {
        g_window_valid = false;
        g_pointer_target_valid = false;
        return host_gate_pointer_event(x, y, 0, 0, dx, dy);
    }
    x = std::clamp(x, 0, g_layout.drawable_w - 1);
    y = std::clamp(y, 0, g_layout.drawable_h - 1);
    // Release does not discard the previous position. Re-seed the mapping
    // after capture changes without delivering the seed as a second motion.
    if (!g_captured && !g_drawable_valid && g_window_valid) {
        int32_t ignored_x, ignored_y;
        host_gate_pointer_event(g_window_x, g_window_y, 0, 0, &ignored_x, &ignored_y);
    }
    // Captured remains position-based for window callers. Map the native
    // position, including the first event and after host/guest edge clamps.
    if (g_captured) {
        g_drawable_x = g_window_valid ? g_window_x : x;
        g_drawable_y = g_window_valid ? g_window_y : y;
        g_drawable_valid = true;
    }
    auto hit = host_gate_pointer_event(x, y, g_window_valid ? x - g_window_x : 0,
                                       g_window_valid ? y - g_window_y : 0, dx, dy);
    g_window_x = x;
    g_window_y = y;
    g_window_valid = true;
    // Remember physical intent even while a page consumes the event. Every
    // delivery is filtered below, so closing the page can reconcile without
    // requiring the user to move the OS pointer again.
    g_pointer_target_valid = hit.kind != HitResult::HIT_NONE;
    g_target_window_x = x;
    g_target_window_y = y;
    return hit;
}

bool host_gate_window_motion(int32_t x, int32_t y, double dx, double dy, HitResult *hit) {
    int32_t gx, gy;
    *hit = host_gate_window_pointer(x, y, &gx, &gy);
    if (hit->kind == HitResult::HIT_NONE || host_gate_motion(hit->gx, hit->gy, gx, gy))
        return false;
    host_input_motion(hit->gx, hit->gy, gx, gy);
    return true;
}

// DirectInput calls this at mouse delivery, not at keyboard refresh. Replace
// queued motion rather than adding feedback once per NSEvent. Re-reading here
// also converges while the OS pointer is stationary or a guest dropped a delta.
extern "C" void host_input_pointer_correction(int32_t *dx, int32_t *dy) {
    // The original cursor integrator clamps before hit testing. A wide scene
    // must be reachable even beyond x=639; restore the normal bound in menus.
    // Only extend a recognized full-surface bound, never a game's modal clamp.
    auto &saved_right = g_saved_cursor_right;
    auto &installed_right = g_installed_cursor_right;
    if (g_mem && gm_valid(RECOMP_HOOK_MOUSE_DEVICE_PTR, 0x48) &&
        rd32(RECOMP_HOOK_MOUSE_DEVICE_PTR) == RECOMP_HOOK_MOUSE_VTABLE) {
        int right = int32_t(rd32(RECOMP_HOOK_MOUSE_DEVICE_RIGHT));
        if (installed_right && right != installed_right)
            saved_right = installed_right = 0;
        const int wanted = mods_display_scene_width(g_mode_w, g_mode_h);
        const bool gameplay = g_layout.cls == HOST_SCREEN_GAMEPLAY && !g_layout.classic;
        if (gameplay && wanted > g_mode_w &&
            (right == g_mode_w || right == g_mode_w - 1 || installed_right)) {
            if (!installed_right)
                saved_right = right;
            wr32(RECOMP_HOOK_MOUSE_DEVICE_RIGHT, wanted);
            installed_right = wanted;
        } else if (installed_right) {
            wr32(RECOMP_HOOK_MOUSE_DEVICE_RIGHT, saved_right);
            saved_right = installed_right = 0;
        }
    }
    if (!g_pointer_target_valid)
        return;
    const auto hit = host_gate_hit_test(nullptr, g_target_window_x, g_target_window_y);
    if (!g_pointer_target_valid || hit.kind == HitResult::HIT_NONE)
        return;
    int32_t cx, cy;
    if (!pointer_correction(hit, &cx, &cy))
        return;
    if (host_gate_motion(hit.gx, hit.gy, cx, cy))
        cx = cy = 0;
    *dx = cx;
    *dy = cy;
}

bool host_gate_pointer_place(int32_t x, int32_t y) {
    if (!g_mem)
        return false;
    const auto mapped = host_gate_hit_test(nullptr, x, y);
    if (mapped.kind == HitResult::HIT_NONE || mods_page_visible() ||
        host_gate_motion(mapped.gx, mapped.gy, 0, 0))
        return false;
    if (recomp_pointer_place(mapped.gx, mapped.gy, g_layout.guest_w, g_layout.guest_h)) {
        // The adapter placed the cursor directly; replaying its relative
        // motion on the next DirectInput read would move it a second time.
        host_input_discard_motion();
        g_loop_expect_valid = false;
        g_pointer_target_valid = false;
        return true;
    }
    const auto p = host_guest_pointer_current();
    if (p.failure != HostGuestPointer::None)
        return false;
    x = std::clamp(x, 0, std::max(0, g_layout.drawable_w - 1));
    y = std::clamp(y, 0, std::max(0, g_layout.drawable_h - 1));
    // A finger the mapper put on the window's last point lands a pixel or two
    // short of the drawable's last row or column once scaled (1666 of 1668 on
    // an iPad), and the hit test hands only that last pixel the guest's edge,
    // which is the row the game scrolls from. Treat the last few pixels as it.
    if (x <= kEdgeSlackPx)
        x = 0;
    else if (x >= g_layout.drawable_w - 1 - kEdgeSlackPx)
        x = std::max(0, g_layout.drawable_w - 1);
    if (y <= kEdgeSlackPx)
        y = 0;
    else if (y >= g_layout.drawable_h - 1 - kEdgeSlackPx)
        y = std::max(0, g_layout.drawable_h - 1);
    // The same mapping pointer_correction() converges toward: the drawable
    // position scaled into the game's screen, or in enhanced gameplay the
    // layout's own guest coordinate for the scene and sidebar.
    const auto hit = host_gate_hit_test(nullptr, x, y);
    int64_t tx, ty;
    if (!g_layout.classic && g_layout.cls == HOST_SCREEN_GAMEPLAY &&
        hit.kind != HitResult::HIT_NONE) {
        tx = hit.gx;
        ty = hit.gy;
    } else {
        const int32_t screen_w = p.right - p.left + 1, screen_h = p.bottom - p.top + 1;
        const double span_x = std::max(1, g_layout.drawable_w - 1);
        const double span_y = std::max(1, g_layout.drawable_h - 1);
        tx = p.left + int64_t(std::lround(ui_fraction(x / span_x, 0) * (screen_w - 1)));
        ty = p.top + int64_t(std::lround(ui_fraction(y / span_y, 1) * (screen_h - 1)));
    }
    tx = std::clamp(tx, int64_t(p.left), int64_t(p.right));
    ty = std::clamp(ty, int64_t(p.top), int64_t(p.bottom));
    static const bool trace = recomp_env("TRACE_POINTER") != nullptr;
    if (trace)
        fprintf(stderr,
                "[place] drawable %d,%d of %dx%d hit %d at %d,%d -> pair %lld,%lld bounds "
                "%d,%d,%d,%d classic %d cls %d\n",
                x, y, g_layout.drawable_w, g_layout.drawable_h, int(hit.kind), hit.gx, hit.gy,
                (long long)tx, (long long)ty, p.left, p.top, p.right, p.bottom,
                int(g_layout.classic), int(g_layout.cls));
    wr32(p.x_addr, uint32_t(tx));
    wr32(p.y_addr, uint32_t(ty));
    // Tell the closed loop where the pair now is, so it neither fights the
    // write nor reads it as a foreign writer.
    g_loop_expect_valid = true;
    g_loop_expect_x = int32_t(tx);
    g_loop_expect_y = int32_t(ty);
    g_pointer_target_valid = false;
    return true;
}

void host_gate_pointer_tick() {
    if (!g_pointer_target_valid)
        return;
    const auto hit = host_gate_hit_test(nullptr, g_target_window_x, g_target_window_y);
    if (!g_pointer_target_valid || hit.kind == HitResult::HIT_NONE)
        return;
    int32_t dx = 0, dy = 0;
    if (!pointer_correction(hit, &dx, &dy, false))
        return;
    // The reader normally waits up to 200 ms (0052ceda). A clock tick wakes
    // it without queuing another delta; delivery will sample the guest again.
    if (dx || dy)
        host_input_motion(g_cursor_x, g_cursor_y, 0, 0);
}
