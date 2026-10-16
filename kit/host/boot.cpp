// boot.cpp - see boot.h. Extracted verbatim in behaviour from the headless
// host, which was the first program to need every part of it.
#include "boot.h"
#include "../runtime/discovery.h"
#include "../runtime/layout.h"
#include "page_overlay.h"

#include "../runtime/guest.h"
#include "../runtime/loader.h"
#include "../runtime/memory.h"
#include "../runtime/native_seam.h"
#include "../runtime/imports.h"
#include "../runtime/win32.h"
#include "../runtime/mods_seam.h"
#include "../runtime/gdi32_internal.h"
#include "../runtime/display_seam.h"
#include "../dx/dx.h"
#include "../dx/host_api.h"
#include "../platform/os.h"

#include <setjmp.h>
#include <stdlib.h>
#include <string.h>

#include <atomic>
#include <vector>

namespace {

BootOptions g_opt;
bool g_loaded = false;
double g_t0 = 0.0;
bool g_activated = false;
bool g_close_posted = false;
double g_close_time = 0.0;
bool g_forced_stop = false;
const char *g_stop_reason = "guest returned from its entry point";

// The last-resort unwind out of guest code. The jmp_buf belongs to boot_run's
// stack, so only the thread that called setjmp may use it: a guest thread that
// reached the deadline must leave the unwinding to that thread and let the
// watchdog stop the run if it never comes back.
jmp_buf g_bail;
bool g_bail_armed = false;
OsThreadId g_guest_thread = 0;
bool g_guest_thread_known = false;

double now_seconds() {
    return (double)os_monotonic_ns() * 1e-9;
}

// The runtime's own clock is not reusable here: host_millis() dispatches to
// whatever source is installed, so chaining onto it would call this back. Boot
// therefore keeps its own monotonic millisecond clock with exactly the
// semantics host_millis() has when no source is installed.
// --- the pinned clock -------------------------------------------------------
//
// RECOMP_PIN_CLOCK=1, or =<start>:<step>, replaces the millisecond clock
// the guest reads with a counter that moves one fixed step per PRESENTED
// FRAME. Nothing else moves it, so two runs of the same script see the same
// time at the same point in the game whatever the machine was doing.
//
// This is the parity fixture's pin (fixture.cpp, start 100 step 50) brought to
// the hosts that run the real game. The fixture could pin the clock trivially
// because it drives the frames itself; a host does not, so the step has to
// hang off the one thing the guest does exactly once per frame.
//
// WHY A FRAME AND NOT A TICK
//
// The obvious alternative is to move the clock every time the guest reads it,
// or on every host tick. Both are counts that differ from run to run, which is
// the thing being removed. A frame is the guest's own unit and it is the same
// number of frames every time for a given sequence of inputs.
//
// THE STALL BREAKER, WHICH TWO ATTEMPTS NEEDED
//
// A frame alone is not enough, and the game proves it inside ten seconds. The
// front end streams a sound and waits for its play cursor to reach the end
// before it draws anything else. The cursor is modelled from this very clock,
// and the wait does not present. Pinned to frames alone the clock stops, the
// cursor stops, the wait never ends, and the run dies on the watchdog with 197
// frames and not one script step. That was measured twice, not imagined.
//
// So a POLL also moves it: any question the guest asks the host whose answer
// depends on time. boot_clock_poll() is that seam. The clock reads go through
// it, and so does anything else a host models on time - the smoke host's play
// cursor is the one that mattered here. After a fixed number of polls with no
// frame between them, the clock takes a step anyway.
//
// The threshold is far above what a frame of this game spends, so during
// ordinary play the frame is the only thing that moves the clock and the model
// above holds exactly. Only a guest that has stopped drawing and started
// waiting reaches it, which is the case that would otherwise hang. Both counts
// come from guest code, so both are the same in two runs of the same script.
//
// The step is also the guest's frame rate as the guest measures it: at 50 ms
// the game believes it is running at 20 frames a second no matter how fast the
// machine really is, so a pinned run is not a timing measurement of anything.
// The counter itself is in the runtime (host_set_time_source_pinned), so that
// the parity fixture and every host pin the SAME clock rather than each
// keeping its own. What stays here is the part that is the host's: reading the
// environment, and the stall breaker below, which is about how a host paces a
// pinned clock and not about what a pinned clock is.
// Polls since the last frame, and how many times a run of them alone had to
// move the clock. A run whose stall count is zero was paced entirely by its
// frames.
// Atomic: the guest reads the clock from every thread it runs, and this counter
// decides when the stall breaker fires. Relaxed is enough - nothing is
// published through it and a count a step behind breaks the stall a poll later.
std::atomic<uint32_t> g_polls_since_frame{0};
uint32_t g_clock_stalls = 0;
// A frame of this game asks a few dozen times. Two hundred and fifty-six is
// far enough above that to be unreachable by drawing and near enough to be
// reached quickly by spinning.
const uint32_t kStallPolls = 256;

void arm_clock_pin() {
    const char *spec = recomp_env("PIN_CLOCK");
    if (!spec || !*spec || !strcmp(spec, "0"))
        return;
    uint32_t start = 100, step = 50;
    if (strcmp(spec, "1") != 0) {
        char *end = nullptr;
        unsigned long a = strtoul(spec, &end, 10);
        unsigned long b = (end && *end == ':') ? strtoul(end + 1, nullptr, 10) : 0;
        if (!end || *end != ':' || !b) {
            fprintf(stderr,
                    "[host] RECOMP_PIN_CLOCK wants 1 or <start>:<step> "
                    "with a step above zero, not \"%s\"; the clock is "
                    "not pinned\n",
                    spec);
            return;
        }
        start = (uint32_t)a;
        step = (uint32_t)b;
    }
    // Installs the counter and the run record's description together, so the
    // two cannot disagree - a record that says "monotonic" about a pinned run
    // makes two incomparable runs look comparable.
    host_set_time_source_pinned(start, step);
    printf("[host] the guest clock is pinned at %u ms and moves %u per "
           "presented frame; this run is repeatable and is not a timing "
           "measurement\n",
           start, step);
    fflush(stdout);
}

uint64_t g_clock_epoch_us = 0;
// The offscreen display has the same 60 Hz mode reported by user32. Pace
// refreshes on its monotonic clock, independently of the guest clock pin:
// each actual present still advances that pin exactly once. Two refresh
// intervals without a primary present let an idle primary become the base
// for GDI again; recent Flips/primary writes own presentation in the meantime.
constexpr uint64_t kWindowPeriodNs = 1000000000ull / 60;
uint64_t g_window_next_ns = 0, g_primary_present_ns = 0;
uint32_t raw_millis() {
    uint64_t us = os_monotonic_ns() / 1000ull;
    if (!g_clock_epoch_us)
        g_clock_epoch_us = us;
    return (uint32_t)((us - g_clock_epoch_us) / 1000ull);
}

void report(FILE *out, bool abnormal) {
    if (g_opt.report)
        g_opt.report(out, abnormal);
}

// ---------------------------------------------------------------------------
// The host's event servicing.
//
// The game never blocks in GetMessage: it drains the queue with PeekMessageA
// (0052a710 / 004b0c10) and otherwise spins on GetTickCount. So the host has
// to do its work from somewhere the guest reaches every frame. GetTickCount
// and timeGetTime both go through the time source, which is exactly the point
// at which a real process yields to the system, so that is the heartbeat.
// ---------------------------------------------------------------------------
// A window has gone from hidden to visible. That transition, not the passage
// of time and not the mere existence of an HWND, is when a window manager
// activates a window and gives it the focus: CreateWindowExA assigns the
// handle long before the window is shown, and a window that is never shown is
// never activated. WM_PAINT is not posted here either - showing the window
// invalidated it, and the guest's own UpdateWindow paints it synchronously.
void boot_window_shown(uint32_t hwnd) {
    if (!g_opt.activate || g_activated)
        return;
    // The same notification serves both ways a window becomes visible:
    // ShowWindow, and WS_VISIBLE in the style CreateWindowExA was given. The
    // second one arrives while the window is still being created, which is
    // before the runtime has finished recording which window is the top-level
    // one, so a zero here means "this is the first, and it is the one".
    uint32_t main_hwnd = host_main_window();
    if (main_hwnd && hwnd != main_hwnd)
        return;
    g_activated = true;
    host_post_message(hwnd, 0x001c /* WM_ACTIVATEAPP */, 1, 0);
    host_post_message(hwnd, 0x0006 /* WM_ACTIVATE */, 1 /* WA_ACTIVE */, 0);
    host_post_message(hwnd, 0x0007 /* WM_SETFOCUS */, 0, 0);
    printf("[host] window %08x shown: activated and focused\n", hwnd);
    fflush(stdout);
}

// Servicing the host posts messages, and posting a message timestamps it with
// host_millis(), which is the time source below. The guard makes that re-entry
// a plain clock read; the rate limit keeps the heartbeat off the guest's
// busy-wait hot path, which reads the clock as fast as the CPU allows.
// Whether the mod teardown finished. The watchdog says so when it ends a run
// that never got there, because a run killed mid-teardown has skipped every
// mod's exit and that is a fact about the run, not a detail.
bool g_mods_torn_down = false;
// Whether the LOADER ran, which since MOD-T10's 8e2f4c1 is exactly when it
// writes the run record - it does so even with nothing to load. So this is the
// question, and "does it have records" is not: an empty directory reaches the
// loader, the loader records, and a host that also wrote a fallback would
// record the same run twice.
bool g_loader_ran = false;
bool g_in_tick = false;
uint32_t g_last_tick_ms = 0xffffffffu;

void boot_tick() {
    if (g_opt.tick)
        g_opt.tick();

    // Registry mutations made off a guest thread are queued, and this is one
    // of the two places they are applied - the other is the scheduler's own
    // checkpoint. It is cheap when the queue is empty, and without it a mod
    // that installs a hook from a worker never has it take effect.
    mods_registry_pump();

    // The guest was asked to close and did not. Unwind out of guest code, but
    // only on the thread whose stack holds the landing pad.
    if (g_close_posted && g_opt.close_unwind_grace > 0.0 &&
        now_seconds() - g_close_time > g_opt.close_unwind_grace && g_bail_armed &&
        os_thread_self() == g_guest_thread) {
        g_forced_stop = true;
        static char unwound[128];
        snprintf(unwound, sizeof unwound,
                 "guest did not act on WM_CLOSE within %.0fs; unwound by the host",
                 g_opt.close_unwind_grace);
        g_stop_reason = unwound;
        fprintf(stderr, "[host] %s\n", g_stop_reason);
        fflush(stderr);
        longjmp(g_bail, 1);
    }
}

uint32_t boot_time_source() {
    boot_clock_poll();
    uint32_t t = host_time_source_is_pinned() ? host_pinned_clock_value() : raw_millis();
    if (!g_in_tick && t != g_last_tick_ms) {
        g_last_tick_ms = t;
        g_in_tick = true;
        boot_tick();
        g_in_tick = false;
    }
    return t;
}

// GetMessageA's wait, and the waiting is not this function's.
//
// runtime/win32.h states the contract in as many words: service the
// host's event loop once and return whether anything is now queued; do not
// loop, do not sleep, do not block. Every one of those is load-bearing:
//
//   - Only GetMessageA knows the filter. "Something is queued" is not
//     "something matches", so a host that blocked until the queue was
//     non-empty would return the instant any message arrived, GetMessageA
//     would find no match, and it would block again on the same unmatched
//     message. That is a spin with the loop moved somewhere the filter is not
//     even visible.
//   - Only the runtime can yield the scheduler baton. A host that blocks
//     inside this callback holds it, so the guest thread that would post the
//     message can never run: the wait would be a deadlock.
//   - GetMessageA owns the loop's exit. WM_QUIT ends a message loop, which is
//     how a message loop is supposed to end, and a host that wants a wait to
//     stop posts a message - which this host already does, posting WM_CLOSE at
//     the cap and letting the game turn it into its own quit.
//
// So this services the host once and answers truthfully.
bool boot_message_waiter() {
    // The same re-entry guard the time source uses: servicing the host posts
    // messages, and posting one reads the clock.
    if (!g_in_tick) {
        g_in_tick = true;
        boot_tick();
        g_in_tick = false;
    }
    return host_messages_pending();
}

// ---------------------------------------------------------------------------
// The watchdog.
//
// The heartbeat rides on the guest's own clock reads, which is enough while
// the guest runs its frame loop but not if it stops calling into the runtime
// at all. A guest spinning inside its own code would then run forever with no
// deadline able to fire. This host thread is the backstop: it knows the
// deadlines independently, prints the report and ends the process. That is a
// reported stop, not a crash, and it is the only way a run can end without the
// guest's cooperation.
// ---------------------------------------------------------------------------
void *watchdog_main(void *) {
    for (;;) {
        os_sleep_us(200 * 1000);
        double elapsed = now_seconds() - g_t0;
        bool over_deadline = g_opt.deadline_seconds > 0.0 &&
                             elapsed >= g_opt.deadline_seconds + g_opt.deadline_grace;
        bool over_close = g_close_posted && g_opt.close_watchdog_grace > 0.0 &&
                          now_seconds() - g_close_time >= g_opt.close_watchdog_grace;
        if (!over_deadline && !over_close)
            continue;
        if (!g_bail_armed)
            return nullptr; // the guest already finished
        g_stop_reason = "the guest stopped calling into the runtime; "
                        "stopped by the host watchdog";
        fprintf(stderr, "\n[host] watchdog: %.0fs elapsed with no response\n", elapsed);
        fflush(stderr);
        sched_watchdog_dump();
        // The guest thread owns the host's counters. Take the report lock so
        // the summary is a consistent snapshot, but only for a second: if the
        // guest is wedged holding it, say so and end the run anyway.
        if (!boot_report_trylock_for(1.0)) {
            // A report read out from under a half-finished update is worse
            // than no report: it would put figures on the record that were
            // never true. Say what happened instead, and end the run.
            fprintf(stderr, "[host] the guest thread still holds the report lock, so "
                            "the run summary would be read mid-update; it is not "
                            "printed\n");
            if (!g_mods_torn_down)
                fprintf(stderr, "[host] the watchdog ended the run before the mod "
                                "teardown: no pop_mod_exit ran\n");
            fflush(stderr);
            os_exit_immediately(4);
        }
        report(stdout, true);
        if (!g_mods_torn_down)
            fprintf(stderr, "[host] the watchdog ended the run before the mod "
                            "teardown: no pop_mod_exit ran\n");
        fflush(stdout);
        fflush(stderr);
        boot_report_unlock();
        os_exit_immediately(4);
    }
}

// ---------------------------------------------------------------------------
// A fault inside guest code is a finding, not a mystery. Recompiled code
// reaches guest memory through g_mem without a bounds check, so a guest that
// runs its stack pointer out of the arena, or follows a wild pointer, faults
// the host process.
//
// Everything here is async-signal-safe: write(2) and hand-formatted digits,
// no stdio, no allocation, no locks, no host report. A signal handler that
// calls printf can deadlock against a printf it interrupted, and one that
// walks the host's frame bookkeeping can walk a container mid-update - either
// turns a diagnosable fault into a hang. The context reported is the faulting
// thread's, not the main thread's: naming the main thread's EIP for a worker's
// fault points at the wrong instruction entirely.
// ---------------------------------------------------------------------------
// strcmp without the library, for the handler's sake.
bool same_text(const char *a, const char *b) {
    while (*a && *a == *b) {
        ++a;
        ++b;
    }
    return *a == *b;
}

void sig_write(const char *s_) {
    size_t n = 0;
    while (s_[n])
        ++n;
    os_write_stderr_raw(s_, n);
}

void sig_write_hex8(uint32_t v) {
    char buf[9];
    for (int i = 7; i >= 0; --i) {
        buf[i] = "0123456789abcdef"[v & 0xf];
        v >>= 4;
    }
    buf[8] = 0;
    sig_write(buf);
}

void sig_write_u32(uint32_t v) {
    char buf[11];
    int i = 10;
    buf[i--] = 0;
    if (!v)
        buf[i--] = '0';
    while (v) {
        buf[i--] = (char)('0' + (v % 10));
        v /= 10;
    }
    sig_write(&buf[i + 1]);
}

void fault_handler(const char *name) {
    // guest_current_context() reads only thread-local state and a vector that
    // is never resized while guest code runs, so it is safe to ask here.
    X86 *c = guest_current_context();
    sig_write("\n[host] ");
    sig_write(name);
    sig_write(" in guest thread ");
    sig_write_u32(guest_current_thread_id());
    sig_write(": EIP=");
    sig_write_hex8(c->eip);
    sig_write(" ESP=");
    sig_write_hex8(c->r[R_ESP]);
    sig_write(" EBP=");
    sig_write_hex8(c->r[R_EBP]);
    sig_write("\n");
    // Where the host was: a fault inside a runtime shim (the scheduler, an
    // import) leaves EIP at the last guest return address, which cannot fault.
    OsFaultInfo fi;
    os_fault_info(&fi);
    if (fi.pc) {
        sig_write("[host] host pc=");
        sig_write_hex8((uint32_t)(fi.pc >> 32));
        sig_write_hex8((uint32_t)fi.pc);
        sig_write(" lr=");
        sig_write_hex8((uint32_t)(fi.lr >> 32));
        sig_write_hex8((uint32_t)fi.lr);
        sig_write(" addr=");
        sig_write_hex8((uint32_t)(fi.addr >> 32));
        sig_write_hex8((uint32_t)fi.addr);
        sig_write(" image=");
        sig_write_hex8((uint32_t)(fi.image_load >> 32));
        sig_write_hex8((uint32_t)fi.image_load);
        sig_write("\n");
    }
    if (c->r[R_ESP] < STACK_LIMIT || c->r[R_ESP] >= STACK_TOP)
        sig_write("[host] the guest stack pointer is outside the main stack: it "
                  "overflowed, lost it, or this is a worker on its own stack\n");
    // Signal-safe by contract: a preformatted string, never built here. It
    // names the mod callback this thread was inside, which is the difference
    // between "the game crashed" and "a mod crashed the game".
    const char *active = mods_active_callback_desc();
    if (active && *active) {
        sig_write("[host] active mod callback: ");
        sig_write(active);
        sig_write("\n");
    }
    sig_write("[host] no run report from a signal handler: printing one is not "
              "signal-safe and could hang instead of exiting\n");
    os_exit_immediately(same_text(name, "an abort from the runtime") ? 6 : 5);
}

} // namespace

// ---------------------------------------------------------------------------

// RECOMP_EXTRA_CODE names libraries of translated code compiled after this
// build: what a run discovered, turned into a module by tools/lazy_static.py.
// Each registers itself with the runtime's module table from a constructor as
// it loads, so nothing here does more than open it. The image's own table is
// consulted first, so a module can only answer for addresses the build did
// not carry. Desktop only: iOS runs no code that was not signed into the app.
void load_extra_code() {
    const char *list = recomp_env("EXTRA_CODE");
    if (!list || !*list)
        return;
    std::string paths(list);
    size_t at = 0;
    while (at <= paths.size()) {
        size_t sep = paths.find(':', at); // one path, or several as a PATH
        std::string path = paths.substr(at, sep == std::string::npos ? sep : sep - at);
        if (!path.empty()) {
            uint32_t before = recomp_module_count();
            if (!os_dlopen(path.c_str()))
                LOGW("RECOMP_EXTRA_CODE: cannot load %s: %s", path.c_str(), os_dlerror());
            else if (recomp_module_count() == before)
                LOGW("RECOMP_EXTRA_CODE: %s registered no translated code", path.c_str());
            else
                LOGW("loaded %s: %u translated function%s", path.c_str(),
                     recomp_module_at(recomp_module_count() - 1)->func_count,
                     recomp_module_at(recomp_module_count() - 1)->func_count == 1 ? "" : "s");
        }
        if (sep == std::string::npos)
            break;
        at = sep + 1;
    }
}

bool boot_load(const BootOptions &opts) {
    g_opt = opts;
    g_window_next_ns = g_primary_present_ns = 0;

    mem_init();
    const char *exe = g_opt.exe;
    if (!exe)
        exe = recomp_env("EXE");
    if (!loader_load(exe))
        return false;
    load_extra_code();
    dx_register_shims();

    // GetTickCount and timeGetTime keep telling the truth; installing the
    // source only adds the host's heartbeat to a call the guest already makes
    // every frame.
    // Before the source is installed, because arming it is what decides what
    // the source will answer.
    arm_clock_pin();
    // RECOMP_HOST_TIMING_TRACE=<path> records the cadence: how often the guest
    // asks the time, and how often anything ticks. Wired here rather than in
    // each host because every host boots through this function, and a trace
    // that only some hosts could produce would be a trace nobody could
    // compare. See host_set_cadence_trace in the runtime for the format.
    if (const char *trace = recomp_env("HOST_TIMING_TRACE"))
        if (*trace)
            host_set_cadence_trace(trace);
    host_set_time_source(boot_time_source);
    host_set_message_waiter(boot_message_waiter);
    // The runtime calls this on the run thread whenever the guest is about to
    // block, so a host that has an event loop gets to run it instead of the
    // guest simply sleeping. Installed only when the host has one: with no
    // waiter the runtime's condition variable is the right thing and costs
    // nothing.
    if (g_opt.idle_wait)
        host_set_idle_waiter(g_opt.idle_wait);
    host_set_window_shown_callback(boot_window_shown);

    loader_init_context(loader_context());

    // Mods load HERE: after the image is mapped, so symbols and guest memory
    // are real, and before the entry point, so a hook is installed before the
    // code it hooks can run.
    const bool mods_enabled = g_opt.load_mods && !recomp_env("NO_MODS");
    bool page_enabled = false;
    if (mods_enabled) {
        // The host-only APIs belong to the thread that boots.
        mods_host_set_main_thread();
        // What applies a queued registry mutation from inside the scheduler.
        // Without it a mutation made off a guest thread is queued and never
        // applied, and the hook silently never fires.
        sched_set_checkpoint(mods_registry_pump);
        if (!mods_load_all())
            LOGW("boot: the mod loader reported a failure");
        // The host display rows exist even when the built-in mod root is
        // empty. RECOMP_NO_MODS still leaves the page and its input unregistered.
        page_enabled = true;
        g_loader_ran = true;
    }
    // The presenters ask this before they touch the page.
    host_page_set_enabled(page_enabled);

    g_loaded = true;
    return true;
}

// Run the mapped guest entry point inside the host exit/fault boundary. Shutdown first
// revokes mod entry, then lets guest workers stop before unloading their code and state.
void boot_run() {
    if (!g_loaded) {
        fprintf(stderr, "[host] boot_run without a loaded image\n");
        return;
    }
    if (g_opt.signal_handlers && !os_install_fault_handlers(fault_handler))
        fprintf(stderr, "[host] fault reporting is not available on this platform\n");

    g_t0 = now_seconds();
    os_thread_prefer_performance();
    g_guest_thread = os_thread_self();
    g_guest_thread_known = true;
    g_bail_armed = true;
    if (g_opt.deadline_seconds > 0.0 || g_opt.close_watchdog_grace > 0.0) {
        OsThread *watchdog = os_thread_create(watchdog_main, nullptr, 0);
        if (watchdog)
            os_thread_detach(watchdog);
        else
            fprintf(stderr, "[host] could not start the watchdog; a wedged guest will hang\n");
    }
    if (setjmp(g_bail) == 0)
        run_entry(loader_context());
    g_bail_armed = false;

    if (process_exited())
        g_stop_reason = "guest called ExitProcess";

    // Shutdown is TWO steps, because the teardown cannot run while a guest
    // worker is still going and a single call would simply drop it: no
    // pop_mod_exit, no reclamation, no unload. Ask first - that revokes every
    // mod at once, so nothing a surviving worker does can reach a plugin -
    // then let the workers finish and keep asking.
    //
    // Nothing can end a guest worker from outside; they are cooperative and
    // they run until their own code returns. The run thread has released the
    // baton by now, so waiting is exactly what gives them the chance to. The
    // bound is a bound and not a promise: a worker that never returns leaves
    // the teardown undone, and that is said out loud rather than hidden.
    // EVERY run records. With mods disabled the loader never ran and never
    // will, so nothing else would write one, and a run whose metadata is
    // absent cannot be told from a run that was never made. With mods loaded
    // the loader's own shutdown writes it, and writing here as well would
    // record the set twice.
    if (!g_loader_ran)
        mods_write_run_record(host_state_file("mods/run.json").c_str());

    mods_shutdown_request();
    // The abandoned frames go FIRST, and unconditionally: this thread has left
    // guest code, so any mod invocation still recorded for it was abandoned by
    // a longjmp - a guest ExitProcess inside a hooked call is the case that
    // matters - and the teardown waits on that count reaching zero. This form
    // touches only this thread's frames and says nothing about the baton, so
    // it works even on a thread the scheduler never registered.
    sched_run_thread_unwind_frames();
    // Then the ending itself, so the scheduler stops offering this thread the
    // baton and skips it when handing off.
    sched_run_thread_finished();
    // Then DRIVE. A polling loop cannot finish this on its own: with the run
    // thread retired nothing advances a timed wait, so a worker sleeping
    // twenty milliseconds would sleep for ever while the poll counted down its
    // bound. This expires deadlines and hands the baton to a thread that can
    // actually run, until every worker has stopped or the bound elapses.
    sched_drive_until_stopped(2.0);
    bool torn_down = mods_shutdown_complete();
    g_mods_torn_down = torn_down;
    // Every host returns from here before ending audio or other host state.
    // Only close players once no guest worker can still access them.
    if (sched_guest_threads_stopped()) {
        recomp_media_shutdown();
        bink_shutdown();
        waveout_shutdown();
    }
    // The drive left this thread registered and holding the baton on purpose:
    // the exits above are mod code and need it. This is where it goes back.
    sched_drive_release();
    if (!torn_down)
        LOGW("boot: %s; mod exits and reclamation did not run",
             sched_guest_threads_stopped() ? "a mod callback is still on some thread's stack"
                                           : "a guest worker outlived the run");
}

double boot_elapsed() {
    return g_t0 ? now_seconds() - g_t0 : 0.0;
}
uint32_t boot_millis() {
    return raw_millis();
}

uint32_t boot_guest_millis() {
    return host_time_source_is_pinned() ? host_pinned_clock_value() : raw_millis();
}

void boot_note_primary_present() {
    g_primary_present_ns = os_monotonic_ns();
}

void boot_present_windows() {
    const uint64_t now = os_monotonic_ns();
    if (now < g_window_next_ns)
        return;
    // Coalesce missed refreshes rather than bursting stale frames after a
    // long guest call. No mutable guest surface is read off the baton.
    g_window_next_ns = now + kWindowPeriodNs;
    if (ddraw_gdi_primary_active() && g_primary_present_ns &&
        now - g_primary_present_ns < 2 * kWindowPeriodNs)
        return;
    gdi_present_windows(true);
}

void boot_clock_advance() {
    if (!host_time_source_is_pinned())
        return;
    g_polls_since_frame.store(0, std::memory_order_relaxed);
    host_pinned_clock_advance();
}

void boot_clock_poll() {
    if (!host_time_source_is_pinned())
        return;
    if (g_polls_since_frame.fetch_add(1, std::memory_order_relaxed) + 1 < kStallPolls)
        return;
    g_polls_since_frame.store(0, std::memory_order_relaxed);
    ++g_clock_stalls;
    host_pinned_clock_advance();
}

uint32_t boot_clock_stalls() {
    return g_clock_stalls;
}

bool boot_clock_pinned() {
    return host_time_source_is_pinned();
}
bool boot_activated() {
    return g_activated;
}
bool boot_on_run_thread() {
    return g_guest_thread_known && os_thread_self() == g_guest_thread;
}
bool boot_abnormal_exit() {
    return g_forced_stop || (process_exited() && process_exit_code() != 0);
}
bool boot_close_requested() {
    return g_close_posted;
}
extern "C" int host_close_requested(void) {
    return boot_close_requested();
}
bool boot_forced_stop() {
    return g_forced_stop;
}
const char *boot_stop_reason() {
    return g_stop_reason;
}
void boot_set_stop_reason(const char *reason) {
    if (reason)
        g_stop_reason = reason;
}

void boot_request_close(const char *reason) {
    if (g_close_posted)
        return;
    g_close_posted = true;
    g_close_time = now_seconds();
    host_guest_pause_cancel();   // 3.0.4: a game parked under the settings menu must run to act on the close
    if (reason)
        g_stop_reason = reason;
    // The caller says why in its own words; boot only delivers the message,
    // which is the one thing closing a real window does.
    uint32_t hwnd = host_main_window();
    if (hwnd)
        host_post_message(hwnd, 0x0010 /* WM_CLOSE */, 0, 0);
}

void boot_print_exit_code(FILE *out) {
    if (process_exited())
        fprintf(out, "guest exit code:    %u\n", process_exit_code());
}

void boot_print_dx_objects(FILE *out) {
    // The COM inventory says which interfaces the game actually created, which
    // is the difference between "no Direct3D draws because it renders through
    // DirectDraw" and "no Direct3D draws because the device never existed".
    fprintf(out, "--- DirectX objects ---\n");
    dx_dump(out);
    fprintf(out, "-----------------------\n");
}

void boot_print_undeliverable(FILE *out) {
    // Every one of these is a translator bug that the guest then ran past with
    // the wrong answer, so the run says so rather than leaving it in the log.
    const std::vector<UnknownCall> &unknown = recomp_unknown_calls();
    if (unknown.empty()) {
        fprintf(out, "undeliverable calls: none\n");
        return;
    }
    fprintf(out, "undeliverable calls: %zu distinct target%s, each a missing entry point\n",
            unknown.size(), unknown.size() == 1 ? "" : "s");
    for (const UnknownCall &u : unknown)
        fprintf(out, "    target %08x  called from the instruction before %08x\n", u.target, u.ret);
    // With RECOMP_DISCOVERY set, these addresses are also on disk in the form
    // tools/recomp/translate.py --discovered reads, so the next regeneration
    // carries the code this run reached. See runtime/discovery.h.
    if (recomp_env("DISCOVERY"))
        fprintf(out, "  %u of them recorded in %s for --discovered\n", discovery_count(),
                recomp_env("DISCOVERY"));
}

void boot_print_import_stats(FILE *out, bool abnormal) {
    (void)out;
    // The loader registers an atexit hook that prints this on a normal exit,
    // so only the paths that leave through _exit have to print it themselves.
    if (abnormal && recomp_env("IMPORT_STATS"))
        imports_dump_report(stderr);
}
