// input_gate.h - whether the guest sees an input, and the delivery that
// follows when it does.
//
// TWO LAYERS, DELIBERATELY NOT ONE.
//
// host_gate_* is the decision on its own: true means the mod layer consumed
// the input. host_key_event and host_modifier_event are the decision AND the
// host's own delivery, which is what a window actually runs. The delivery
// lives here rather than in sdl/main.cpp so a headless test can drive exactly
// the path the window drives; sdl/main.cpp is left with event decoding and
// nothing else, and smoke_main.mm keeps its own scripted delivery.
//
// PHYSICAL STATE IS NOT GUEST STATE. The keyboard's real transitions decide
// what is offered to the filter, and the guest's delivered state decides what
// the messages say. Conflating them is what makes a consumed press stick: its
// release never looks like a transition, so the filter is never told the key
// came up and every later press is answered as a repeat.
//
// Consuming one decision covers all four paths the guest has, because each is
// fed downstream of this call: the DirectInput immediate state, the buffered
// events the shim derives by diffing that state, the posted Win32 message and
// GetAsyncKeyState.
#pragma once
#include <stdint.h>
#include "input.h"

// The decision, on its own. True = the mod layer consumed it.
bool host_gate_key(uint16_t mac_keycode, bool down);
bool host_gate_button(int button, bool down, int32_t x, int32_t y);
bool host_gate_motion(int32_t x, int32_t y, int32_t dx, int32_t dy);
bool host_gate_wheel(int32_t dz);
// The tabbed settings menu takes the pointer while it is open: pointing chooses a row, a press
// acts on a page name, a row, its < > arrows or Close, the wheel moves the choice. (x, y) are
// drawable pixels; button -1 is motion or wheel. True = consumed: nothing goes to the game.
bool host_gate_menu_pointer(int32_t x, int32_t y, int button, bool down, int32_t wheel);

// One ordinary key, gated and then delivered: the DirectInput state, the
// posted key message; TranslateMessage generates WM_CHAR. `character` is
// the unmodified character the key produced, or 0 when it produced none.
// Returns true when the mod layer consumed it, in which case nothing was
// delivered on any path.
bool host_key_event(uint16_t mac_keycode, bool down, uint32_t character);

// A click at GUEST coordinates, with no physical mapping in the way.
//
// The scripted `click` moves a mouse: it sends relative motion, and the game
// integrates that into a pointer of its own that started wherever it chose, so
// a coordinate is a request rather than a placement. That is right for
// anything a person would do, and wrong for driving a screen whose layout is
// known in guest pixels while the pointer's own history is not.
//
// This places the guest's pointer AT the coordinate - the absolute position
// with no delta, which is what the windowed host's own button path does - and
// delivers the button there, through the same four paths every other input
// takes: the DirectInput immediate state, the buffered events derived from it,
// the posted Win32 message and the button mask those messages carry.
//
// Returns true when the mod layer consumed it, in which case nothing was
// delivered on any path.
//
// CALL IT UNDER THE BATON. It touches the guest's input state, posts to the
// guest's message queue and asks the mod layer, which is a callback into
// guest-visible state. A host that called it from the scheduler's idle slice
// would be racing whichever guest thread holds the baton, which is the fault
// smoke_main.mm's idle waiter was moved off.
//
// The mask it carries is the GATE's, which is the guest's, and not any host's
// idea of which physical buttons are down - the same separation this file
// keeps for keys. A script that mixes `click` and `guestclick` therefore has
// two masks in play; nothing needs that today and whoever does will have to
// say which one the message should carry.
//
// WHAT THIS GAME DOES WITH IT, MEASURED
//
// It delivers. It does not place. A guestclick at (320, 140) on the main menu
// - New Game's row - selected MULTIPLAYER instead, the row under the game's
// own cursor at the time, and the run came back showing "no multiplayer
// services available". The press and release went out on all four paths and
// the guest read them; what it ignored was the position, on both the
// DirectInput immediate state and the message's lParam.
//
// So Populous hit-tests its menus against the pointer IT integrates from
// relative motion, and nothing a host places absolutely moves that pointer.
// A script that needs a particular menu item still has to walk the game's
// cursor there with relative motion, which is what `click` and the corner pin
// in level1.script do. This verb is for a screen that reads the position it
// is given, and whether any screen in this game does is a question for
// whoever drives one.
bool host_gate_inject_guest_click(int32_t gx, int32_t gy, int button, bool down);

// A modifier flags change, diffed per side and delivered per key. macOS
// reports modifiers as a bitmask rather than as key events, so this is the
// only place those key messages can be made.
void host_modifier_event(uint32_t ns_event_modifier_flags);

// The modifier bits AS THE GUEST KNOWS THEM - shift 1, control 2, alt 4 - for
// the mouse messages that carry them. A consumed modifier is not in here,
// because the guest was never told about it.
uint8_t host_guest_modifiers(void);

// Whether the guest believes this virtual key is down. Test seam and the
// answer post_key's repeat bit is built from.
bool host_guest_key_down(uint8_t vk);

// Resynchronise the physical modifier state from an event's flags, delivering
// whatever edges that implies through the gate.
//
// macOS sends a flagsChanged only when the flags CHANGE. A modifier that was
// already held when the window regained focus produces no event at all, and
// focus loss has just cleared everything, so without this the guest is told
// Option is up while the user is holding it down - and the next keystroke is
// classified as an ordinary WM_KEYDOWN with a WM_CHAR instead of the
// WM_SYSKEYDOWN Win32 would have sent. Call it on focus gain and on every key
// event: it is a diff, so it costs nothing and emits nothing when the state
// already agrees.
void host_gate_sync_modifiers(uint32_t ns_event_modifier_flags);

// The window lost the focus. Releases the host's input state AND the mod
// filter's, because a filter still holding a consumed key would answer the
// next press as a repeat and never ask its callbacks again.
void host_gate_release_all(void);

// Drops every layer this file owns, for a test that wants a clean slate.
void host_gate_reset(void);

// ---------------------------------------------------------------------------
// Pointer capture.
//
// The window keeps the OS pointer associated in both capture states and hides
// it while captured. Window positions map to a target for bounded feedback
// against the game's integrated pointer. Until that pointer is readable,
// successive positions divided by scene scale supply motion. No capture warp.
// host_pointer_motion and host_gate_pointer_event retain the relative-motion
// One queued input event, reduced to what the batching rule needs to know.
struct HostInputStep {
    uint8_t is_button; // a press or a release, rather than motion or a wheel
    uint8_t button;    // 0 left, 1 right, 2 middle
    uint8_t down;      // 1 press, 0 release
};

// How many of a queued batch may be applied in one guest turn.
//
// A guest that polls its buttons cannot see a press and its release applied
// between two polls: it reads the state once and finds the button up again, so
// the click never happened. That is what the DirectInput shim's buffered
// events are built from, and what a window's click handling comes down to. The
// batch is cut before the release of a button pressed within it, and the rest
// waits for the next turn. Every other ordering is preserved, because order is
// what keeps a focus loss behind the presses it must follow.
//
// Returns `count` when nothing needs deferring, and never 0 for a non-empty
// batch: the first event is always applicable.
uint32_t host_input_batch_limit(const struct HostInputStep *steps, uint32_t count);

// arithmetic for non-window callers; window events use host_gate_window_pointer.
// ---------------------------------------------------------------------------

// The guest's current mode, which is what the cursor is clamped to.
void host_pointer_set_mode(int w, int h);
// Capture or release. Releasing keeps the cursor where it was, so re-capturing
// does not teleport the guest's pointer.
void host_pointer_capture(bool captured);
bool host_pointer_captured(void);
// Feeds one relative motion. Returns true when the guest's cursor moved, and
// false when it did not - clamped against an edge, or received while released,
// which is not the guest's motion to see.
bool host_pointer_motion(int32_t dx, int32_t dy);
// Where the guest's cursor is, in guest pixels.
void host_pointer_cursor(int32_t *x, int32_t *y);
// Puts it in the middle of the frame, which is where capture starts.
void host_pointer_center(void);

// Read-only guest-memory seam, also used with a synthetic arena in host tests.
// object is the guest this pointer, not the address of a pointer slot. In the
// pinned executable 00526dd0 selects the static object 0x00d0595c with MOV ECX,
// immediate; 0052d430 returns object+0x20. Call under the guest baton.
// Readiness checks only the object's constructor identity and cursor/bounds.
// The optional input context at +0x1c is informational, never dereferenced.
struct HostGuestPointer {
    enum Failure {
        None,
        ArenaUnavailable,
        ObjectNull,
        ObjectOutsideArena,
        VtableMismatch,
        BoundsInvalid,
        CoordinatesOutsideBounds
    } failure = None;
    bool arena_available = false;
    uint32_t arena_size = 0, object = 0, vtable = 0, context = 0;
    int32_t x = 0, y = 0, left = 0, top = 0, right = 0, bottom = 0;
    uint32_t x_addr = 0, y_addr = 0; // where x and y live (placement writes them): object+0x20/+0x24, or the pair
};
HostGuestPointer host_guest_pointer_resolve(const uint8_t *arena, uint32_t size, uint32_t object);
// A game whose cursor is a plain x/y pair of globals ([hooks] mouse_pair_x / mouse_pair_y, NFS Underground run 2):
// ready once the guard dword is non-zero (the game's mouse exists); the bounds are the display mode, 0..w-1 x 0..h-1,
// and the coordinates may sit on w or h (the game clamps to its client rectangle inclusively).
HostGuestPointer host_guest_pointer_resolve_pair(const uint8_t *arena, uint32_t size, uint32_t guard, uint32_t x_addr,
                                                 uint32_t y_addr, int32_t mode_w, int32_t mode_h);
// The game's pointer by whichever form its hooks name (the pair when [hooks] mouse_pair_x is set, else the object).
HostGuestPointer host_guest_pointer_current(void);
const char *host_guest_pointer_failure_name(HostGuestPointer::Failure failure);

// Task 9 compositor mapping. Except set_layout (presenter publication), these
// functions run under the guest baton. nullptr hit-test uses the newest value
// snapshot; a non-null input is a synchronous test/construction convenience.
#include "compositor.h"
struct HitResult {
    enum Kind { HIT_NONE, HIT_ELEMENT, HIT_SCENE } kind = HIT_NONE;
    uint64_t element = 0;
    int32_t gx = 0, gy = 0;
    // Captured by value so a drag survives layout replacement/frame retirement.
    LayoutRect guest{}, drawable{};
    SceneMapping scene{};
};
HitResult host_gate_hit_test(const CompositorInput *layout, int32_t dx, int32_t dy);
void host_gate_begin_drag(const HitResult *owner);
void host_gate_end_drag(void);
void host_gate_set_layout(const CompositorInput *layout);
// Used only until the presenter publishes its first layout. Called under baton.
void host_gate_fallback_layout(int drawable_w, int drawable_h);
// Decoded positions/deltas are drawable pixels, never window points. Produces
// the relative correction as well as the absolute position: Populous ignores
// absolute placement and integrates DirectInput motion for its hit tests.
HitResult host_gate_pointer_event(int32_t x, int32_t y, double dx, double dy, int32_t *guest_dx,
                                  int32_t *guest_dy);

// Wake the guest mouse reader for remaining correction, under the baton.
void host_gate_pointer_tick();
// Put the game's own cursor at drawable position (x, y) now, by writing the
// pair the game integrates (the same pair the closed loop reads). Touch has
// no pointer to converge from: a finger names a place, and the click that
// follows must hit-test there. Returns false when the pair is not readable.
// Call under the guest baton, from the input queue, in order with the motion
// before it and the button after it.
bool host_gate_pointer_place(int32_t x, int32_t y);

// Window motion production path: map, filter and deliver DirectInput once.
// x/y are backing pixels. Device dx/dy are deliberately ignored: the window
// pointer already includes OS acceleration. sdl/main.cpp posts WM_MOUSEMOVE only
// when this returns true. Buttons and wheel use the same position mapping.
HitResult host_gate_window_pointer(int32_t x, int32_t y, int32_t *dx, int32_t *dy);
bool host_gate_window_motion(int32_t x, int32_t y, double dx, double dy, HitResult *hit);

// A resize is a latest-value mailbox independent of presenter acknowledgement.
// The next input event rebuilds layout geometry at this size, including when
// an older frame is acknowledged after the resize.
void host_gate_publish_drawable_size(int w, int h);
// Capture requires an inside click, and Escape must remain a release while held.
bool host_pointer_can_capture(bool key, bool inside, bool click, bool escape, bool page);
// Capture confines the OS pointer in every window mode, including a plain window.
bool host_pointer_confinement_wanted(bool captured, int window_mode);
bool host_pointer_at_resize_edge(double x, double y, double w, double h, double margin);
void host_pointer_drawable_position(double *x, double *y);

// Publish the acknowledged presenter mapping by value.
void host_gate_publish_layout(const LayoutSnapshot &snapshot);
