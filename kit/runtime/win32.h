// win32.h - services shared between the Win32 shim modules, plus the small
// surface the host application (Task 7) and the DirectX shims (later task)
// use to drive the guest.
#pragma once
#include "game_config.h"
#include "guest.h"
#include <string.h>
#include <string>
#include <vector>

// The guest root's own directory name (the last component of RECOMP_GUEST_ROOT), so
// C:\<root>\data\x and \data\x resolve to the same host file.
static inline const char *win32_guest_root_name() {
    const char *root = RECOMP_GUEST_ROOT;
    const char *slash = strrchr(root, '\\');
    return slash ? slash + 1 : root;
}

// ---------------------------------------------------------------------------
// Shared scrollbar bodies (plain Win32 layouts), also used by FlatSB.
void win32_get_scroll_pos(X86 *c);
void win32_set_scroll_pos(X86 *c);
void win32_get_scroll_info(X86 *c);
void win32_set_scroll_info(X86 *c);
void win32_get_scroll_range(X86 *c);
void win32_set_scroll_range(X86 *c);
void win32_show_scroll_bar(X86 *c);
void win32_enable_scroll_bar(X86 *c);
void win32_forget_scrollbars(uint32_t hwnd);

// Process-wide state
// ---------------------------------------------------------------------------
// Called by the loader once the image is mapped. `game_dir` is the host
// directory that backs the guest root RECOMP_GUEST_ROOT (the directory holding the
// loaded EXE).
void win32_init(const std::string &game_dir);
const std::string &win32_game_dir();
// Guest spelling shared by shell folder APIs; creation uses the write overlay.
std::string shell_folder_guest_path(uint32_t csidl, bool create);

// Reserves a process-wide TLS slot for the loader or TlsAlloc; 0xffffffff
// when every slot is in use. win32_init resets the reservation map.
uint32_t tls_reserve_slot();

void set_last_error(uint32_t code);
// Signals a kernel event object by handle (pulse = signal then immediately
// reset). False when the handle is not an event. Used by WINMM's event-mode
// multimedia timers.
bool win32_signal_event(uint32_t handle, bool pulse);
// A kernel event object made by a shim on the guest's behalf (a DirectShow
// graph's completion event), handed to the guest as a handle it can wait on
// and close like one CreateEventA returned.
uint32_t win32_create_event(bool manual_reset, bool signalled);
bool win32_reset_event(uint32_t handle);
uint32_t get_last_error();

// DirectDraw supplies the current accepted mode when linked. The runtime-only
// weak default returns false and leaves the caller's fallback values intact.
extern "C" bool ddraw_display_mode(uint32_t *w, uint32_t *h, uint32_t *bpp);
// Selected DirectDraw mode, otherwise the host virtual desktop.
void win32_display_mode(uint32_t *w, uint32_t *h, uint32_t *bpp);
// Notify an exclusive DirectDraw window after the accepted mode is published,
// including transitions back to an unchanged size after video playback.
void win32_refresh_display_window(X86 *c, uint32_t hwnd, uint32_t w, uint32_t h, uint32_t bpp);
// A fullscreen swap chain's output window is sized to the mode it put the
// display in, and gets its bounds back when the chain leaves fullscreen.
void win32_cover_display(X86 *c, uint32_t hwnd, uint32_t w, uint32_t h);
void win32_uncover_display(X86 *c, uint32_t hwnd);
// A stretch of a guest thread in which the import checkpoints do not hand the
// baton to another thread (a blocking call still does), entered at guest stack
// pointer `esp`. Window creation runs in one: Delphi's VCL passes the control
// being created to its first window message through a global, and a thread
// switch at one of the imports between setting it and reading it gave that
// message another thread's control. A guest exception that unwinds above
// `esp` ends the stretch (sched_atomic_unwind_to_esp), since the matching
// leave never runs.
void sched_atomic_enter(uint32_t esp);
void sched_atomic_leave();
void sched_atomic_unwind_to_esp(uint32_t esp);
// A window with no parent: a program's own top-level window.
bool win32_top_level(uint32_t hwnd);
extern "C" bool ddraw_enum_display_mode(uint32_t index, uint32_t *w, uint32_t *h, uint32_t *bpp);

// ---------------------------------------------------------------------------
// File system, and the overlay seam.
//
// Guest paths are case-insensitive, use '\' separators and live under
// the guest root. win32_host_path_op answers a specific OPERATION, because where
// a path resolves depends on what is about to happen to it: a read may come
// from any overlay tier, while a write, a delete or a rename must only ever
// reach the writable tier. Classifying every call site is what stops a write
// from opening an original asset.
//
// `relative` reaches the resolver normalised: '/' separators, no drive, no
// leading root component, no "." or ".." components. It returns non-zero having
// written a host path, or zero for "no answer", in which case the shim falls
// back to the game directory exactly as an unmodded build does.
// ---------------------------------------------------------------------------
enum Win32FileOp {
    WIN32_FILE_READ = 0,
    WIN32_FILE_WRITE,
    WIN32_FILE_DELETE,
    WIN32_FILE_RENAME_SRC,
    WIN32_FILE_RENAME_DST,
    WIN32_FILE_LIST,
};

std::string win32_host_path_op(const std::string &guest_path, int op);

// A C++ throw as RaiseException sees it: the MSVC record (code 0xe06d7363)
// carries the object and its throw info, which names the type; the object of
// a std::exception-derived class carries its message at +4. Returned as text
// for the shim to log, since the runtime cannot unwind the throw.
std::string win32_describe_cxx_throw(uint32_t code, uint32_t nargs, uint32_t args);
// Return addresses inside the image along the EBP chain from `ebp`, at most `max`.
std::vector<uint32_t> win32_return_chain(uint32_t ebp, size_t max);
// If `value` points at an object whose vtable carries MSVC RTTI, the class's
// mangled name; otherwise an empty string.
std::string win32_describe_pointer(uint32_t value);
// Forget the command line handed out so far (tests change RECOMP_GUEST_ARGS).
void win32_reset_command_line_for_test();
// Dwords on the stack from `esp` over `bytes` that lie in [lo, hi) and sit right
// after a CALL instruction: the return addresses a frame-pointer-less chain
// hides. Newest first; at most `max`.
std::vector<uint32_t> win32_stack_return_candidates(uint32_t esp, uint32_t bytes, uint32_t lo,
                                                    uint32_t hi, size_t max);
// Kept for existing callers: for_create=false is a read, true is a write.
std::string win32_host_path(const std::string &guest_path, bool for_create = false);
// Reverse mapping used by GetModuleFileNameA/GetFullPathNameA.
std::string win32_guest_path(const std::string &host_path);
// Query an open file without moving its guest descriptor. False for non-file
// or closed handles and failed position queries; outputs are then unchanged.
bool win32_file_handle_position(uint32_t handle, std::string *host_path, int64_t *offset);
void win32_invalidate_dir_cache();

// The lister reports one (name, host_path) pair per file, already merged and
// de-duplicated across tiers, so FindFirstFileA can fill metadata from the
// tier a match actually came from.
void win32_set_file_ops(
    int (*resolve)(const char *relative, int op, char *out, size_t out_len),
    void (*list)(const char *relative_dir,
                 void (*emit)(void *ctx, const char *name, const char *host_path), void *ctx));

// ---------------------------------------------------------------------------
// Scheduler coordination for the mod foundation.
//
// A hook-table mutation is only safe from a thread the scheduler is currently
// letting run, because the baton is what makes guest threads mutually
// exclusive. A mutation from a host thread is queued instead and applied by
// whichever guest thread next passes a checkpoint.
// ---------------------------------------------------------------------------
// True only on a thread the scheduler registered as a guest thread. This is
// recorded, never inferred: t_self is a __thread size_t defaulting to 0, so an
// unregistered host thread would otherwise claim to be threads()[0].
bool sched_is_guest_thread();
// Registers or unregisters the calling thread as a guest thread. Only
// run_entry needs this: every other guest thread is registered by the
// scheduler when it starts.
void sched_set_guest_thread(bool yes);
// True when this thread is the one the scheduler is currently letting run,
// which is the only state in which the dispatch tables may be changed.
//
// LOCK ORDER. This answers by taking the scheduler's mutex, which is the same
// non-recursive mutex sched_registry_lock() takes. Calling it while holding
// the registry lock deadlocks the thread against itself, so a caller that is
// already under that lock must use sched_holds_baton_locked() instead. The
// hazard is easy to miss because the function returns false at its
// guest-thread check before it ever reaches the lock, so a host thread asking
// wrongly gets an answer and only a guest thread hangs.
bool sched_holds_baton();
// The same question, for a caller that ALREADY holds the scheduler lock -
// which sched_registry_lock() is. Takes no lock of its own, so it is the only
// form that is safe under it, and it is unsafe without it: the fields it reads
// belong to the scheduler. Use sched_holds_baton() everywhere else.
bool sched_holds_baton_locked();
// The deadline watchdog's look at the scheduler: every guest thread, what it waits on (each object's kind, state and
// owner; a critical section's owner and count), its saved EIP/ESP and the return address on its stack, and the last
// hand-offs. Called from the watchdog's host thread; takes the scheduler lock for at most a second and says so when it
// cannot. NFSU2 run 3: a race load sat on its loading screen with the main thread parked, and the run said nothing
// about on what.
void sched_watchdog_dump();
// True once every spawned guest thread has finished, which is the precondition
// for unloading plugins.
bool sched_guest_threads_stopped();
// The run thread has left guest code for good - run_entry returned, or the
// host unwound out of it. Clearing the thread-local registration is not
// enough: the scheduler's baton may still name this thread, and a worker
// parked waiting for it would wait forever for a thread that will never run
// guest code again. This marks it finished, runs the same exit cleanup a
// worker gets (including the mod-invocation unwind) and hands the baton on,
// so the remaining workers can run to completion.
//
// Call it only from the thread that registered itself with
// sched_set_guest_thread(true), once it has stopped. Idempotent, and a no-op
// from any other thread.
void sched_run_thread_finished();
// Unwinds this thread's abandoned mod invocations, unconditionally and without
// touching the scheduler. For a teardown path whose thread never registered
// with the scheduler, which sched_run_thread_finished cannot recognise.
void sched_run_thread_unwind_frames();
// Drives the scheduler until every spawned guest thread has stopped or the
// timeout elapses; true when they stopped.
//
// It does it by JOINING the scheduler, not by acting on it from outside. The
// calling thread is the run thread coming back as itself - registered again,
// taking the baton through the ordinary handoff and giving it up through the
// ordinary holder yield, which is the same code a guest Sleep runs. Nothing
// here inspects a thread that might be running or moves the baton from
// outside the yield path, because a worker sets `blocked` and then releases
// the scheduler mutex before it parks, and anything that reads that as
// "parked" hands the baton to a thread that is still on its way there.
//
// Call it from the thread that ran the guest, after sched_run_thread_finished.
//
// It returns STILL REGISTERED and holding the baton, because the mod exit
// handlers the caller runs next are ordinary mod code and need it: an exit
// that removes its own hook would otherwise be queued on the one thread left
// to apply the queue. Call sched_drive_release() when they are done.
bool sched_drive_until_stopped(double timeout_seconds);
// True while the run thread is parked inside a host idle slice.
//
// The scheduler releases its lock across that slice so the host can be
// serviced, which means ANOTHER guest thread may hold the baton and be running
// guest code throughout it. Anything the host does during a slice that would
// be visible to the guest - delivering input, and above all dispatching a mod
// callback, which captures a view of guest memory and touches the settings,
// heap and event registries - would run concurrently with that thread's hooks.
// A host asks this and defers such work until it next holds the baton.
bool sched_in_idle_slice();
// Input the host has decoded during an idle slice but not yet applied.
//
// A host may not apply input from a slice - another guest thread may hold the
// baton - so it queues. That queue is WORK, and the scheduler has to treat it
// as such: a guest blocked on an event that an input message would signal
// cannot read its own clock, so the host's tick never runs, so the queue never
// drains, so the event is never signalled. The thread would wait out its
// timeout for input already in hand. Registering these lets the scheduler
// drain the queue itself, from a point where nothing else is in guest code,
// before it decides to sleep.
void sched_set_input_queue(bool (*pending)(), void (*drain)());
// Called by the host the moment it queues input. A guest thread that already
// found the queue empty is parked with a deadline of up to a second and would
// otherwise sleep it out with the input waiting; this wakes it to look again.
// Safe from any thread, and free when nothing is parked.
void sched_input_arrived();
// Ends the drive: puts the roster back as it was, hands the baton to a worker
// that can use it or leaves it free if none can, and deregisters. A baton
// parked on this thread - retired, and never going to yield again - would
// strand every worker still blocked behind it.
void sched_drive_release(void);
// The same answer for a caller that already holds the registry lock, so a
// decision and the mutation it authorises can be taken without releasing it.
bool sched_guest_threads_stopped_locked();

// Has any thread declared itself a guest thread yet? Monotone for the life of
// the process: guest entry is not something that un-happens. A caller that
// wants to know whether the dispatch tables are unattended must ask this as
// well, because the main guest thread is not "spawned" and so does not appear
// in sched_guest_threads_stopped().
bool sched_guest_entry_begun_locked();
#ifdef POPM_TESTING
// Forgets that, for a process that tears one guest down and starts another.
// Compiled ONLY into a test binary: it resets a safety latch, and a latch that
// production code can reset is not one. A real run enters the guest once.
void sched_forget_guest_entry();
#endif
// The scheduler's own mutex, taken for a registry mutation only. Nothing may
// call a mod callback or run guest code while holding it.
void sched_registry_lock();
void sched_registry_unlock();
void sched_set_checkpoint(void (*fn)());
void sched_set_thread_exit_observer(void (*fn)());
// Called at every point the baton can move, with no lock held.
void sched_run_checkpoint();

// ---------------------------------------------------------------------------
// Timing. One monotonic millisecond clock backs GetTickCount and timeGetTime.
// The host may pin it so a frame sees a stable time.
// ---------------------------------------------------------------------------
uint32_t host_millis();
void host_set_time_source(uint32_t (*fn)());
// NFSMW 3.0.4 (D-202 item 4, guest_pause.h): the settings menu pausing the game. The guest clock hold stops
// host_millis() (so GetTickCount, timeGetTime, QueryPerformanceCounter and the multimedia timers) where it is and
// lets it carry on from there on release; a pinned clock is never held. host_guest_clock_us maps the monotonic
// clock in microseconds to the same held time (QueryPerformanceCounter's microsecond form).
void host_guest_clock_hold(bool on);
bool host_guest_clock_held();
uint64_t host_guest_clock_us(uint64_t mono_us);
// The park point, called by Present after each frame: parks the main guest thread while the menu wants the game
// paused. The host gives it its mixer pause, and cancels it for good when the game is asked to close.
void host_guest_pause_point();
void host_guest_pause_set_audio(void (*fn)(bool paused));
void host_guest_pause_cancel();
// What that clock IS, in the host's own words, for whatever has to record what
// a run ran on. Installing a time source is not the same as pinning one - the
// boot hosts install one that reads the real clock - so this cannot be derived
// from whether a source is set, and only the host that installed it knows.
// Defaults to "monotonic", which is what an unpinned run has.
void host_set_clock_description(const char *text);
const char *host_clock_description();

// The pinned clock, in the runtime so that everything which pins one pins the
// SAME one. The parity fixture has had a counter like this since parity began
// - 100 ms, stepping 50 per frame - and the boot hosts grew their own later;
// two counters with one name is how a run ends up described as pinned while
// something reads the wall.
//
// It reads start_ms and moves step_ms when host_pinned_clock_advance() is
// called, and at no other time. Installing it also sets the clock description,
// so a run record can never say "monotonic" about a pinned run or disagree
// with the pin's own numbers: the description is derived here rather than
// written by the caller.
void host_set_time_source_pinned(uint32_t start_ms, uint32_t step_ms);
// One step. Does nothing when no pin is installed, so a host may call it from
// its frame path unconditionally.
void host_pinned_clock_advance();
bool host_time_source_is_pinned();
// Take any installed time source away and go back to the real clock, naming it
// "monotonic" again. A pinned clock left installed by whoever pinned it is a
// clock that never moves for everyone after them: nothing advances it, every
// timed wait becomes eternal, and a busy-wait on it never ends. That is not
// hypothetical - it hung this repository's runtime suite for fifteen minutes
// while it held the build lock.
void host_clear_time_source();
// The pin's step, or 0 when nothing is pinned. A caller that has to express a
// duration in frames needs it: at a 50 ms step, 2000 ms is 40 frames.
uint32_t host_pinned_clock_step();
// The counter itself, for a host that layers its own time source over this one
// and still wants the pinned value - boot.cpp does, because its source also
// drives the host heartbeat.
uint32_t host_pinned_clock_value();

// The cadence trace: how often the guest asks the time, and how often anything
// that ticks actually ticks. Writes "kind,ms" lines, one per interval, where
// ms is the gap since the previous event of that kind measured on the guest's
// own clock - so under a pinned clock the intervals are the pin's steps, which
// is what makes two runs' cadence comparable at all.
//
// Five kinds. Two of them this game never uses, and that is worth knowing:
// an empty column means the game never did it, not that nobody instrumented it.
//
//   timeGetTime   WINMM's clock. THIS GAME BARELY USES IT: six calls in a whole
//                 run, one gap of 39 seconds. The first version traced only
//                 this, which made the headline number - how often the guest
//                 asks the time - describe a call the game does not make.
//   GetTickCount  the one it does. The frame limiter spins here, so the
//                 interval between these is the shape of the main loop.
//   QueryPerformanceCounter
//                 the high-resolution clock, traced for the same reason: a
//                 guest that moved to it would otherwise go quiet with nothing
//                 to say it had.
//   WM_TIMER      a window timer message reaching the queue.
//   mm_callback   a multimedia timer firing its guest callback.
//
// Passing NULL closes the trace. Opening one twice closes the first.
void host_set_cadence_trace(const char *path);
// One event of a kind, for the seams that are not in this file. The interval
// is computed here, so a caller only has to say that the thing happened.
void host_note_cadence(const char *kind);

// ---------------------------------------------------------------------------
// USER32 bridge for the host layer
// ---------------------------------------------------------------------------
void host_post_message(uint32_t hwnd, uint32_t msg, uint32_t wparam, uint32_t lparam);
// Queue host mouse input in virtual-screen coordinates, on the guest baton.
void host_post_mouse_message(uint32_t msg, uint32_t mk, int32_t x, int32_t y);
// The same routed event, starting in a specific window's client pixels.
void host_post_client_mouse_message(uint32_t hwnd, uint32_t msg, uint32_t mk, int32_t x, int32_t y);
// Post a keyboard message to the window that has the focus, which is where
// Windows sends one. Not the same as host_main_window: in a VCL application
// that is the invisible application window.
void host_post_key_message(uint32_t msg, uint32_t wparam, uint32_t lparam);
uint32_t host_main_window();              // first created top-level HWND, or 0
uint32_t host_window_proc(uint32_t hwnd); // guest WNDPROC address, or 0
bool host_window_rect(uint32_t hwnd, int32_t *x, int32_t *y, int32_t *w, int32_t *h);
void host_set_client_size(uint32_t hwnd, int32_t w, int32_t h);
// Host input is in client pixels; GetCursorPos/MSG.pt are screen pixels.
void host_set_client_cursor_pos(uint32_t hwnd, int32_t x, int32_t y);
void host_set_key_state(int vk, bool down); // feeds GetAsyncKeyState
void host_set_cursor_pos(int32_t x, int32_t y);
// Installs the host's event-loop pump. GetMessageA calls it each time round
// its wait, and the contract is deliberately narrow:
//
//   SERVICE THE HOST'S EVENT LOOP ONCE AND RETURN WHETHER ANYTHING IS NOW
//   QUEUED. Do not loop, do not sleep, do not block.
//
// The waiting belongs to GetMessageA, for three reasons, and a host that waits
// as well ends up with two nested waits that fight each other:
//
//   - Only GetMessageA knows the filter. "Something is queued" is not
//     "something matches", so a host that blocks until the queue is non-empty
//     returns as soon as any message arrives, GetMessageA finds no match, and
//     the host blocks again. That is a busy loop with the spin moved.
//   - Only the runtime can yield the scheduler baton. A host that blocks
//     inside this callback holds it, so the guest thread that would post the
//     message can never run. Use host_guest_yield() if you must wait at all.
//   - GetMessageA owns the loop's exit: WM_QUIT ends it, which is how a
//     message loop is supposed to end. A host that wants a wait to stop posts
//     a message, exactly as a real system does.
//
// Without a waiter installed GetMessageA reports -1, because nothing could
// ever post a message and blocking would be a hang with no way out. That is
// the documented error rather than a fabricated message.
void host_set_message_waiter(bool (*fn)());
// True when the guest message queue has anything in it. A message waiter uses
// this to answer truthfully rather than guessing.
bool host_messages_pending();
// Blocks the calling guest thread for `ms` and lets the other guest threads
// run. GetMessageA uses it so waiting for a message is a real wait.
void guest_sleep_ms(uint32_t ms);
// WaitForMultipleObjects for the calling guest thread: WAIT_OBJECT_0 + i,
// WAIT_ABANDONED_0 + i, WAIT_TIMEOUT (0x102) or WAIT_FAILED. A zero timeout
// answers from the current state, taking what it reports as Windows does.
uint32_t guest_wait_objects(const uint32_t *handles, uint32_t count, bool wait_all,
                            uint32_t timeout_ms);
// Hands the baton to whatever else can run and takes it back; false means
// nothing else could have run. A host that blocks inside a callback the guest
// made - a message waiter, most obviously - must yield rather than spin or
// sleep: a cooperative thread holds the baton for as long as it sits there, so
// the thread that would satisfy the wait could never run.
bool host_guest_yield();

// Signals a guest event object from a host thread - an AppKit event handler,
// an audio callback, anything not running guest code. Safe to call while a
// guest thread holds the scheduler baton: the request is queued and applied by
// the next thread to enter the scheduler, so the handle table is still only
// ever touched by one thread. A thread waiting on the event is released as
// soon as the signal lands, not when its timeout expires. A null or
// INVALID_HANDLE_VALUE handle is ignored.
void guest_event_signal_from_host(uint32_t handle);
// Test seam (runtime_tests --sched-race): from a host thread, walk the guest
// thread list and the handle table under the scheduler lock, as a woken worker
// does; returns live threads + event handles.
uint32_t sched_census_locked_walk();

// ---------------------------------------------------------------------------
// What the run thread does instead of parking in the scheduler.
//
// The run thread - the one that called run_entry - is also, on a windowed
// host, the thread that services the window system. Every time it waits in the
// scheduler, for the baton or for the earliest deadline, nothing pumps events
// and nothing draws: the window stops responding and the cursor spins while
// the guest is perfectly healthy.
//
// A host installs a waiter here. The contract:
//
//   - Called ONLY on the run thread, the one that called run_entry. A worker
//     must never service the window system, so it is never asked to.
//   - `seconds` is how long it MAY wait, a duration rather than an absolute
//     time so the host needs no agreement with the runtime about which clock.
//     Returning sooner is always safe; returning later costs latency.
//   - No runtime lock is held. The host may signal a guest event, notify
//     DirectInput, or call anything else here without deadlocking.
//   - The return value is advisory: 0 for "the time was up", non-zero for
//     "something arrived, look again". The scheduler re-checks its own
//     conditions after every call either way, so a non-zero return is a hint
//     about latency, never an assertion that any particular wait is satisfied.
//     An early return with the condition still false simply waits again.
//   - It must not block indefinitely or execute guest code: another guest
//     thread may hold the baton for the whole call.
//
// Waiting for the baton while another guest thread runs passes zero: poll host
// events without sleeping. The runtime then waits on its condition variable,
// which the returning worker wakes immediately, with a 2 ms cap to keep host
// events responsive even if the worker keeps running.
// Waiting until the earliest deadline with nothing else runnable, the slice is
// the whole remaining time up to 50 ms: nobody is going to hand anything back,
// so the host should block properly rather than be woken five hundred times a
// second to be told there is nothing to do. Returning early when input arrives
// is what keeps latency below that cap.
//
// With no waiter installed the scheduler uses a condition variable, which is
// what a host with nothing to service wants: it costs nothing and a signal
// wakes it at once.
// ---------------------------------------------------------------------------
void host_set_idle_waiter(int (*fn)(double seconds));
// Thread-safe: makes the idle waiter return early. Called by the runtime's deadline
// ticker when a guest deadline falls due during an idle slice (Test58 T01).
void host_set_idle_wake(void (*fn)());
// T01 evidence: cumulative ns the MAIN guest thread spent without the baton:
// [0] runnable while another guest thread ran (checkpoint yields), [1] in its
// own sleeps/waits, [2] runnable again after a wait but still without the
// baton; [3] the longest single [0]/[2] stretch since the last call (reset);
// [4] plain-Sleep time requested, [5] how far those sleeps overran, [6] time in
// object/critical-section waits; [7] the part of [5] while another guest thread
// held the baton.
void recomp_sched_main_totals(uint64_t out[8]);
// Called when a window goes from hidden to visible, which is the transition a
// window manager reacts to: it is when activation, focus and the first paint
// happen. Not called for a window that was already visible.
void host_set_window_shown_callback(void (*fn)(uint32_t hwnd));
bool host_window_visible(uint32_t hwnd);

// ---------------------------------------------------------------------------
// The register file of whichever guest thread is running right now, and its
// thread id. A fault handler must report the faulting thread's context: the
// main thread's would name the wrong instruction for a worker's fault.
// ---------------------------------------------------------------------------
X86 *guest_current_context();
uint32_t guest_current_thread_id();
bool host_cursor_visible();
bool host_cursor_clip(int32_t out[4]);

// Calls the window's WNDPROC through recomp_call.
uint32_t host_dispatch_to_wndproc(X86 *c, uint32_t hwnd, uint32_t msg, uint32_t wparam,
                                  uint32_t lparam);

// ---------------------------------------------------------------------------
// Something to run on the main guest thread between frames, from the guest's
// own message loop, where a shim may call back into translated code because
// the caller holds the scheduler baton.
//
// The audio shims register here and it is not a convenience. A streamed sound
// is refilled by calling a guest callback, and real QMixer runs its own mixing
// thread, so nothing in the game ever drives the mixer by hand: QSWaveMixPump
// is called zero times in a whole run. Without a tick from somewhere a
// streamed wave plays the chunks it was given at the start and then stops,
// which is what a menu with no music sounds like.
//
// Passing null removes it. Only one is kept; the last registration wins.
// ---------------------------------------------------------------------------
// Register a callback for the once-a-frame seam. More than one subsystem can:
// the calls run in registration order, registering the same function twice
// registers it once, and passing NULL removes them all. See misc.cpp for why
// this is a list rather than the single slot it started as.
void host_set_frame_pump(void (*fn)(X86 *c));

// ---------------------------------------------------------------------------
// MIDI. The game's music is MIDI played through a SoundFont: it looks for a
// midiOut device whose name begins with "SoundFont" (0x575e40 walks the
// devices calling midiOutGetDevCapsA and compares nine characters against the
// literal at 0x5eb5b0), opens it with no callback, and then drives it note by
// note with midiOutShortMsg. It tries SFMAN32.DLL first, which this build
// reports as missing, so the plain midiOut path is the one that runs.
//
// The host provides a synth; these are the four things the shim needs from it.
// Weak no-op defaults live in misc.cpp, so a build with no host links and
// simply reports no MIDI device, which is what the game already copes with.
//
// `sf2_path` is a host filesystem path to the SoundFont bank, already resolved
// through the file shim, or null when it could not be found. Returns non-zero
// if the synth is open.
// ---------------------------------------------------------------------------
// Where the game's bank is, resolved through the file shim, or empty when it
// is not there. A host loads the SoundFont before the guest runs rather than
// inside midiOutOpen, which arrives on a guest thread holding the baton, so it
// needs to ask for the path itself.
std::string win32_midi_soundfont_path();

extern "C" {
int host_midi_open(const char *sf2_path);
// A packed short message: status in bits 0-7, the two data bytes above it,
// exactly as midiOutShortMsg is given it.
void host_midi_short(uint32_t msg);
void host_midi_sysex(const void *data, uint32_t bytes);
void host_midi_reset(void);
void host_midi_close(void);
}

// ---------------------------------------------------------------------------
// WINMM multimedia timers: the host fires due callbacks between frames, on the
// main thread, through the guest callback registered by timeSetEvent.
// ---------------------------------------------------------------------------
void host_pump_timers(X86 *c);

// ---------------------------------------------------------------------------
// Registry: backed by build/recomp/registry.json (path overridable with
// RECOMP_REGISTRY). Flushed on write.
// ---------------------------------------------------------------------------
void registry_load();
void registry_flush();
std::string registry_path();

// ---------------------------------------------------------------------------
// Undeliverable calls. recomp_call could not resolve the target to a
// translated function or a trampoline, so the guest was given EAX = 0 and the
// pushed return address was consumed. Every entry is a translator bug: a
// missing entry point, or a call target inside a block nothing recovered.
// Recorded once per distinct target so a run can report them.
// ---------------------------------------------------------------------------
struct UnknownCall {
    uint32_t target, ret;
};
const std::vector<UnknownCall> &recomp_unknown_calls();

// ---------------------------------------------------------------------------
// Process exit. ExitProcess/TerminateProcess longjmp to the buffer returned by
// process_exit_jmp(); run_entry-style hosts should wrap the guest call in
// `if (setjmp(*process_exit_jmp()) == 0) run_entry(c);`.
// ---------------------------------------------------------------------------
#include <setjmp.h>
jmp_buf *process_exit_jmp();
bool process_exited();
uint32_t process_exit_code();
