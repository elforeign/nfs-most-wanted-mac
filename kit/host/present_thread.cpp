#include "page_overlay.h"
#include "../runtime/display_seam.h"
// Worker-owned swapchain and sealed-frame mailbox, over gpu.h. No window code here.
#include "present.h"
#include "present_frame.h"
#include "present_test.h"
#include "performance_overlay.h"
#include "gpu_time.h"
#include "d3d_render.h"
#include "input_gate.h"
#include "../dx/passes.h"
#include "../dx/ddraw.h"
#include "../runtime/mods_seam.h"
// Test90: the audio output reopens after a display HDR switch (audio/mixer.cpp).
extern "C" void host_audio_restart_output(const char *why); // weak default: runtime/mods_seam.cpp
#if defined(POPM_COMPOSITOR_TEST_UI_DOUBLE)
#include "tests/compositor_ui_double.h"
#elif __has_include("ui_layer.h")
#include "ui_layer.h"
#else
#include "ui_frame_contract.h"
#endif
#include <string.h>
#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cstdio>
#include <deque>
#include <functional>
#include <memory>
#include <mutex>
#include <set>
#include <thread>
#include "../platform/os.h"

namespace {
// Test78 (owner, 1 Oct 2026): the presenter paces at the game's frame rate when the display refreshes at a
// whole multiple of it. On an LG TV at 120 Hz the game still seals a frame every 16.7 ms, but pacing at the
// display rate (wake and minimum duration 8.3 ms, repeats in between) showed new frames for 1, 2, 3 or 4
// refreshes in turn (Development 61, 17:01-17:03: 37-50% of new frames held 25-33 ms): visible judder and
// the overlay's red bars. At the same TV's 60 Hz, and on the laptop panel (whose adaptive mode reads as
// 60), every new frame stayed 16.7 ms. The game's rate is the frame limit (40/60/120) or 60 when it is
// off, the simulation rate. RECOMP_HOST_PRESENT_DISPLAY_RATE=1 keeps display-rate pacing (A/B only).
double pacing_period(double display) {
    if (!(display > 0))
        return 1.0 / 60;
    if (recomp_env("HOST_PRESENT_DISPLAY_RATE"))
        return display;
    const int cap = mods_display_fps();
    const double content = 1.0 / (cap > 0 ? cap : 60);
    const double k = std::floor(content / display + 0.5);
    if (k >= 2 && std::fabs(k * display - content) < 0.0005)
        return k * display;
    return display;
}
} // namespace

// Packed into one atomic so a producer never combines dimensions from two
// renderer restarts. Already sealed frames retain their own target and layout.
static std::atomic<uint64_t> render_resolution{0};
extern "C" void host_set_render_resolution(int w, int h) {
    const uint64_t size = w > 0 && h > 0 && w <= 16384 && h <= 16384
                              ? (uint64_t(uint32_t(w)) << 32) | uint32_t(h)
                              : 0;
    render_resolution.store(size);
    fprintf(stderr, "presenter: render resolution %dx%d (%s)\n", int(size >> 32),
            int(uint32_t(size)), size ? "logical canvas retained" : "automatic");
}

// Minimal hosts can link the service without the app's present.mm. Unknown
// mode keeps first-write acquisition on the service's last guest dimensions.
// App and smoke hosts override this with their current display mode.
extern "C" __attribute__((weak)) void host_present_mode(int *w, int *h, int *bpp) {
    if (w)
        *w = 0;
    if (h)
        *h = 0;
    if (bpp)
        *bpp = 0;
}

static std::atomic<bool> g_present_suspended{false};
static std::mutex g_present_controls_mutex;
static controls::ControlsView g_present_controls; // wanted = false until the host publishes

// T01 evidence probe (host_present_set_sched_probe); null records zeros.
static void (*g_sched_probe)(uint64_t out[8]) = nullptr;

namespace {
// The refresh rate of the offscreen presenter's synthetic display link.
constexpr double kOffscreenHz = 120.0;
using Clock = std::chrono::steady_clock;
template <class T> struct AtomicShared {
    std::shared_ptr<T> value;
    std::shared_ptr<T> load() const {
        return std::atomic_load(&value);
    }
    void store(std::shared_ptr<T> p) {
        std::atomic_store(&value, std::move(p));
    }
    std::shared_ptr<T> exchange(std::shared_ptr<T> p) {
        return std::atomic_exchange(&value, std::move(p));
    }
    bool compare_exchange_weak(std::shared_ptr<T> &expected, std::shared_ptr<T> desired) {
        return std::atomic_compare_exchange_weak(&value, &expected, std::move(desired));
    }
};
struct Metric {
    double origin = -1, latest = -1;
    // 10 warm-up seconds followed by the binding 30 second observation.
    std::array<uint64_t, 30> buckets{};
    void tick(double ts) {
        if (origin < 0)
            origin = ts;
        latest = std::max(latest, ts);
    }
    void complete(double ts) {
        if (origin < 0 || ts < origin)
            return;
        const int bucket = int(std::floor(ts - origin + 1e-7));
        if (bucket >= 10 && bucket < 40)
            ++buckets[bucket - 10];
    }
    int read(double *minimum, double *elapsed) const {
        const double span = origin < 0 ? 0 : latest - origin;
        if (elapsed)
            *elapsed = span;
        if (minimum)
            *minimum = span >= 40 ? *std::min_element(buckets.begin(), buckets.end()) : 0;
        return span >= 40;
    }
};
struct Service : std::enable_shared_from_this<Service> {
    HostCaptureFactory capture_factory = nullptr;
    std::mutex mutex;
    std::condition_variable wake, completed;
    std::thread worker;
    bool stop = false, fake = false, automatic = true, offscreen = false;
    bool duration_pacing = true;
    bool tick_pending = false, seal_pending = false;
    double last_link_tick = -1, window_started = -1;
    unsigned faults = 0, logged_faults = 0;
    double frame_period = 1.0 / 60, fake_now = 0;
    void fault(unsigned bit, const char *reason) { // mutex held
        if (logged_faults & bit)
            return;
        logged_faults |= bit;
        ++faults;
        fprintf(stderr, "presenter: FAULT: %s (unique=%llu drops=%llu flight=%llu mailbox=%zu)\n",
                reason, (unsigned long long)unique, (unsigned long long)drops,
                (unsigned long long)(flights.empty() ? 0 : flights.front()->frame_id),
                mailbox.size());
    }
    bool window_ready() const { // mutex held; first submission needs no completion or link tick
        return tick_pending || message.load() != nullptr ||
               (flights.size() < flight_limit && seal_pending && !mailbox.empty());
    }
    void signal_tick(double ts) {
        std::lock_guard lock(mutex);
        last_link_tick = timestamp = ts;
        tick_pending = true;
        wake.notify_one();
    }
    double timestamp = 0;
    // A queued drawable can take several refreshes to reach the display. The
    // old two-refresh deadline retired real frames before their positive
    // presented callbacks arrived, making the native FPS counter undercount.
    double acknowledgement_grace() const {
        return (flight_limit + 2) * frame_period;
    }
    AtomicShared<Message> message;
    std::atomic<int> drawable_w{640}, drawable_h{480};
    int guest_w = 640, guest_h = 480, requested_w = 0;
    // The game rectangle's size as last told to the input gate; zero until a
    // resize first publishes one.
    int gate_rect_w = 0, gate_rect_h = 0;
    // Mutex held. Where this frame is composed (present.h): the whole drawable
    // in landscape, the top of it in portrait. A portrait rectangle follows the
    // guest mode, so a mode change re-publishes its size to the input gate the
    // way a resize does; in landscape it never differs from what resize sent.
    // `placed` is set when the rectangle is not the whole drawable.
    HostGameRect game_rect(bool *placed = nullptr) {
        const int dw = drawable_w, dh = drawable_h;
        const HostGameRect r =
            host_present_game_rect(dw, dh, guest_w, guest_h, host_present_safe_top());
        if (placed)
            *placed = r.x != 0 || r.y != 0 || r.w != dw || r.h != dh;
        if (gate_rect_w > 0 && (r.w != gate_rect_w || r.h != gate_rect_h))
            publish_gate_size(r);
        published_rect.store(pack_rect(r), std::memory_order_relaxed);
        return r;
    }
    // The last rectangle game_rect() computed, packed into one word so the
    // window host reads it without the mutex (guest threads hold that across
    // GPU allocation) and can never see half of an update.
    std::atomic<uint64_t> published_rect{0};
    static uint64_t pack_rect(const HostGameRect &r) {
        const auto field = [](int v) { return uint64_t(std::clamp(v, 0, 0xffff)); };
        return field(r.x) << 48 | field(r.y) << 32 | field(r.w) << 16 | field(r.h);
    }
    static HostGameRect unpack_rect(uint64_t v) {
        return {int(v >> 48 & 0xffff), int(v >> 32 & 0xffff), int(v >> 16 & 0xffff),
                int(v & 0xffff)};
    }
    void publish_gate_size(const HostGameRect &r) { // mutex held
        gate_rect_w = r.w;
        gate_rect_h = r.h;
        host_gate_publish_drawable_size(r.w, r.h);
    }
    unsigned fail_allocations = 0;
    gpu::Device *device = nullptr;
    gpu::Swapchain chain;
    void *surface = nullptr;
    // The display pacer: a thread that wakes the worker once per refresh, in
    // place of the CVDisplayLink the AppKit host used.
    std::thread pacer;
    bool pacer_stop = false;
    std::condition_variable pacer_wake;
    uint64_t fake_texture_ids = 0x100000000ull; // fake mode: scene handles with no device
    double now() const {                        // mutex held
        return fake ? fake_now : device ? device->now_seconds() : 0.0;
    }
    // Up to three display submissions, two mailbox frames, a writer and history.
    // Offscreen and the single-flight control still use only four targets.
    std::array<std::shared_ptr<Target>, 7> targets;
    size_t flight_limit = 1;
    std::shared_ptr<Frame> writing;
    std::deque<std::shared_ptr<Frame>> flights;
    std::deque<std::shared_ptr<Frame>> mailbox, retiring;
    std::shared_ptr<Target> history_lease;
    CompositorSceneHistory history;
    uint64_t history_epoch = 0;
    std::shared_ptr<Composite> cached;
    std::shared_ptr<Composite> cached_settings;
    UiFrame cached_ui{};
#ifdef POPM_PRESENT_HAS_UI_LAYER
    UiFrame previous_ui{}; // guest thread only, including sealed frames that were dropped
    uint64_t previous_ui_epoch = 0;
#endif
    CompositorInput cached_input{};
    std::shared_ptr<Target> cached_target;
    uint8_t last_pixel = 0;
    CaptureMarker cached_diagnostic;
    uint64_t unique = 0, repeats = 0, drops = 0, waits = 0, last_id = 0;
    uint64_t settings_pages = 0; // sealed frames that carried the settings page
    // NFSMW 3.0.4 (run 39): the menu redrawn while the guest is parked (host_present_menu_refresh); the next repeat
    // composes again instead of reusing the cached composition, so the new page shows over the held frame.
    bool recompose_pending = false;
    uint64_t menu_refreshes = 0, recomposed_repeats = 0;
    int fake_menu_w = 0, fake_menu_h = 0; // a fake service keeps no texture: the size its menu_generation is for
    double cached_sealed_ts = 0;
    bool cached_screen_class_valid = true;
    uint64_t epoch = 0;
    bool class_known = false;
    HostScreenClass last_class = HOST_SCREEN_MENU;
    Metric metric;
    FramePacing pacing;
    // Test86 HDR output: the setting (core.nfsmw/hdr_output, hdr_highlights) read every 30 compositions; the
    // swapchain goes RGBA16F extended linear sRGB only while the display has EDR headroom (macOS HDR on).
    bool hdr_wanted = false, hdr_logged = false;
    uint64_t hdr_polls = 0;
    double hdr_target = 1.0, hdr_current_smooth = 0.0, hdr_usable_logged = 0.0;
    bool hdr_upscale_logged = false;
    float hdr_desat = 0.3f;   // PLAN_R2 item 10: the mode-3 shoulder's whitening of hot cores
    // Test90: the display's own HDR mode (the TV's HDR signal), and the audio reopen a switch calls for.
    int hdr_display_logged = -9;
    unsigned hdr_switches_seen = 0;
    double hdr_audio_reopen_at = 0;
    void hdr_poll() {
        if (++hdr_polls % 30 != 1 || !chain)
            return;
        const bool setting = mods_setting_value("core.nfsmw/hdr_output", 0) != 0;
        // Test91: 4 = and at 60 Hz while in HDR (the owner's HDMI link cannot carry 4K 120 Hz HDR cleanly).
        const bool at_60 = mods_setting_value("core.nfsmw/hdr_refresh_60", 1) != 0;
        const int display_mode = device->display_hdr_mode(setting ? (at_60 ? 1 | 4 : 1) : 0);
        if (display_mode != hdr_display_logged && display_mode != 2) {
            static const char *const names[] = {"not available", "SDR", "HDR"};
            printf("presenter: display HDR mode %s (hdr_output %s)\n", names[std::clamp(display_mode, -1, 1) + 1],
                   setting ? "on" : "off");
            fflush(stdout);
            hdr_display_logged = display_mode;
        }
        const unsigned switches = device->display_hdr_switches();
        if (switches != hdr_switches_seen) {
            hdr_switches_seen = switches;
            hdr_audio_reopen_at = now() + 3.0;   // let the HDMI link settle first
        }
        if (hdr_audio_reopen_at > 0 && now() >= hdr_audio_reopen_at) {
            hdr_audio_reopen_at = 0;
            // Reopening blocks for a moment: not on the presenter thread.
            std::thread([] { host_audio_restart_output("the display's HDR mode changed"); }).detach();
        }
        const double strength = std::clamp(double(mods_setting_value("core.nfsmw/hdr_highlights", 60)), 0.0, 100.0) / 100;
        // R2 #10 (review/r2close/hdr-design.md 3.5): `want` follows the display's POTENTIAL headroom with hysteresis
        // (on above 1.10, off below 1.02) so a display at the edge does not flap; the highlight ceiling follows the
        // CURRENT headroom (macOS clips the layer there; it is lower at low panel brightness), smoothed over ~1 s of
        // polls and never above the potential. Logged when it moves by more than 0.05.
        const double potential = device->edr_headroom(true), current = device->edr_headroom(false);
        const bool want = setting && (hdr_wanted ? potential > 1.02 : potential > 1.10);
        hdr_current_smooth = hdr_current_smooth > 0 ? hdr_current_smooth + (current - hdr_current_smooth) * 0.5 : current;
        const double usable = std::min(potential, std::max(1.0, hdr_current_smooth));
        hdr_target = 1.0 + (usable - 1.0) * strength;
        if (want && std::fabs(usable - hdr_usable_logged) > 0.05) {
            hdr_usable_logged = usable;
            printf("presenter: HDR headroom current %.2f (smoothed %.2f), potential %.2f: highlights to x%.2f of SDR white\n",
                   current, hdr_current_smooth, potential, hdr_target);
            fflush(stdout);
        }
        {   // PLAN_R2 item 10: the game's frame in float while HDR is really on (RECOMP_TEST_HDR_RENDER=0: today's
            // expansion of the 8-bit frame); RECOMP_TEST_HDR_FLOAT=1 forces it, also on an SDR swapchain (the job's
            // float dumps then carry a synthetic headroom of 4). RECOMP_TEST_HDR_DESAT: the shoulder's whitening (0.3).
            static const char *render_env = recomp_env("TEST_HDR_RENDER"), *force_env = recomp_env("TEST_HDR_FLOAT"),
                              *desat_env = recomp_env("TEST_HDR_DESAT");
            const bool render = !render_env || std::atoi(render_env) != 0;
            const bool force = force_env && std::atoi(force_env) != 0;
            hdr_desat = desat_env ? std::clamp(float(std::atof(desat_env)), 0.0f, 1.0f) : 0.3f;
            const bool fl = force || (want && render);
            g_host_hdr_headroom.store(float(hdr_target), std::memory_order_relaxed);
            // R2 #10 (hdr-design.md 3.7): MetalFX spatial in Perceptual mode with values above 1 is unverified (probe P4,
            // after R2); until then HDR + upscaling only says so (upscale is off by default).
            const bool upscaled = mods_setting_value("core.nfsmw/upscale", 0) != 0;
            if (fl && upscaled != hdr_upscale_logged) {
                hdr_upscale_logged = upscaled;
                if (upscaled) {
                    printf("presenter: HDR with upscaling: highlights may clip in the MetalFX scaler (probe pending)\n");
                    fflush(stdout);
                }
            }
            if (fl != g_host_hdr_float.load(std::memory_order_relaxed)) {
                g_host_hdr_float.store(fl, std::memory_order_relaxed);
                printf("presenter: HDR float frame %s (hdr_output %s, headroom potential %.2f%s)\n", fl ? "on" : "off",
                       setting ? "on" : "off", potential, force ? ", TEST forced" : !render ? ", TEST hdr_render 0" : "");
                fflush(stdout);
            }
        }
        if (want != hdr_wanted || (setting && !want && !hdr_logged)) {
            if (want != hdr_wanted)
                device->set_swapchain_hdr(chain, want);
            hdr_wanted = want;
            hdr_logged = true;
            printf("presenter: HDR output %s (setting %s, EDR headroom potential %.2f, highlights to x%.2f of SDR white)\n",
                   want ? "requested" : "off", setting ? "on" : "off", potential, hdr_target);
            fflush(stdout);
        }
    }
    // Test83: the game's GPU time per game frame (gpu_time.h), for the overlay; the CSV keeps the presenter's.
    uint64_t game_gpu_us_seen = 0, game_gpu_frames_seen = 0;
    double game_gpu_ms = -1;
    double overlay_gpu_ms(double presenter_ms) {
        const uint64_t us = g_host_game_gpu_us.load(std::memory_order_relaxed);
        const uint64_t frames = g_host_game_gpu_frames.load(std::memory_order_relaxed);
        if (frames > game_gpu_frames_seen && us >= game_gpu_us_seen) {
            game_gpu_ms = double(us - game_gpu_us_seen) / 1000.0 / double(frames - game_gpu_frames_seen);
            game_gpu_us_seen = us;
            game_gpu_frames_seen = frames;
        }
        return game_gpu_ms >= 0 ? game_gpu_ms : presenter_ms;   // no D3D9 renderer: the presenter's
    }
    // R2 close perf pass (review/r2close/perf-design.md 3.1): frames.csv game_gpu_ms = the game's GPU ms per game frame
    // finished since the previous row (same counters as the overlay, own cursor), -1 when none finished.
    uint64_t csv_gpu_us_seen = 0, csv_gpu_frames_seen = 0;
    double csv_game_gpu_ms() {
        const uint64_t us = g_host_game_gpu_us.load(std::memory_order_relaxed);
        const uint64_t frames = g_host_game_gpu_frames.load(std::memory_order_relaxed);
        if (frames <= csv_gpu_frames_seen || us < csv_gpu_us_seen)
            return -1;
        const double ms = double(us - csv_gpu_us_seen) / 1000.0 / double(frames - csv_gpu_frames_seen);
        csv_gpu_us_seen = us;
        csv_gpu_frames_seen = frames;
        return ms;
    }
    FILE *timings = nullptr;
    unsigned timing_rows = 0;
    FILE *ack_trace = nullptr;
    // The tabbed settings menu's last page and the generation it shows (mutex held).
    std::shared_ptr<Composite> menu_page;
    uint64_t menu_generation = 0;
    ~Service() {
        if (timings)
            fclose(timings);
        if (ack_trace)
            fclose(ack_trace);
    }
    void trace_ack(const Frame &f, const char *event, double ts) { // mutex held
        if (!ack_trace)
            return;
        const double now = this->now();
        fprintf(ack_trace, "%llu,%d,%d,%s,%.9f,%.9f,%.9f,%.9f,%.9f,%d,%d,%d,%d\n",
                (unsigned long long)f.frame_id, int(f.cls), int(f.repeat), event, now, ts,
                f.submitted_ts, f.gpu_ts, f.acknowledgement_deadline, int(f.shown),
                int(f.completion_fallback), int(f.released), int(f.screen_class_valid));
    }
    PerformanceOverlay performance_overlay;
    controls::Overlay controls_overlay;
    std::array<LayoutSnapshot, 3> layouts;
    unsigned layout_slot = 0;
    bool layout_valid = false;
    std::set<uint64_t> test_released;
    unsigned pending_commands = 0;

    void release(const std::shared_ptr<Frame> &f) { // mutex held; never guest data access
        if (f->released)
            return;
        f->released = true;
        if (!f->repeat) {
            if (fake)
                test_released.insert(f->frame_id);
            else
                ddraw_present_release({f->frame_id});
        }
        // The target history lease is separate from the retired frame arena.
        f->target.reset();
        f->scene_lease.reset();
        completed.notify_all();
    }
    // Retire completed submissions in order and release their retained frame resources.
    // Late completion callbacks must never replace the cached image with an older frame.
    void sweep() {
        for (auto it = retiring.begin(); it != retiring.end();) {
            if ((*it)->prefix->done && (*it)->pending_prefixes == 0 && (*it)->gpu) {
                release(*it);
                it = retiring.erase(it);
            } else
                ++it;
        }
        // Retire in submission order even if callbacks arrive out of order.
        // An older acknowledgement must never replace a newer cached image.
        while (!flights.empty() && flights.front()->gpu && flights.front()->shown &&
               flights.front()->prefix->done && flights.front()->pending_prefixes == 0) {
            auto f = flights.front();
            if (f->success && f->prefix->success && !stop) {
                if (!f->repeat)
                    ++unique;
                if (!offscreen && !f->completion_fallback)
                    pacing.displayed(f->presented_ts, f->repeat, overlay_gpu_ms(f->gpu_ms),
                                     (f->presented_ts - f->sealed_ts) * 1000);
                if (!f->repeat && !f->completion_fallback && f->cls == HOST_SCREEN_GAMEPLAY &&
                    f->epoch == epoch)
                    metric.complete(std::max(f->presented_ts, f->gpu_ts));
                if (timings) {
                    fprintf(
                        timings,
                        "%llu,%d,%d,%.9f,%.9f,%.9f,%.3f,%llu,%d,%d,%d,%d,%llu,%llu,%llu,%d,%.3f,"
                        "%.3f,%.3f,%.3f,%.3f,%.3f,%.3f,%.3f,%.3f,%.3f,%.3f\n",
                        (unsigned long long)f->frame_id, int(f->cls), int(f->repeat), f->sealed_ts,
                        f->submitted_ts, f->presented_ts, f->gpu_ms, (unsigned long long)drops,
                        int(!offscreen && !f->completion_fallback), f->input.drawable_w,
                        f->input.drawable_h, int(f->screen_class_valid),
                        (unsigned long long)f->diagnostic.render_frame,
                        (unsigned long long)f->diagnostic.monotonic_ns,
                        (unsigned long long)f->diagnostic.id, int(f->diagnostic.detailed),
                        f->slot_wait_ms, f->guest_yield_ms, f->guest_blocked_ms, f->guest_late_ms,
                        f->guest_max_starve_ms, f->guest_cpu_ms, f->guest_sleep_req_ms,
                        f->guest_sleep_over_ms, f->guest_wait_ms, f->guest_over_others_ms,
                        csv_game_gpu_ms());
                    // Keep diagnostics live without a synchronous write on
                    // every display callback while holding the queue mutex.
                    // fclose flushes the remaining rows during shutdown.
                    if (++timing_rows % 60 == 0)
                        fflush(timings);
                }
                cached_diagnostic = f->diagnostic;
                cached = f->composed;
                // Run 39: only a sealed frame brings a new menu page. A repeat carries the page it was made with, and
                // a menu refresh (host_present_menu_refresh, guest parked) may have replaced it since: a retiring
                // repeat must not put the older page back, or the next repeat composes the old menu (the paused
                // menu stayed frozen on screen while a repeat was always in flight).
                const gpu::Texture page = cached_input.settings_page;
                const int page_w = cached_input.settings_page_w, page_h = cached_input.settings_page_h;
                if (!f->repeat)
                    cached_settings = f->settings_page;
                last_id = f->frame_id;
                cached_sealed_ts = f->sealed_ts;
                cached_screen_class_valid = f->screen_class_valid;
                cached_ui = f->ui;
                cached_input = f->input;
                cached_input.ui = &cached_ui;
                if (f->repeat) {
                    cached_input.settings_page = page;
                    cached_input.settings_page_w = page_w;
                    cached_input.settings_page_h = page_h;
                }
                if (!f->repeat)
                    cached_target = f->input.legacy ? f->target : f->scene_lease;
                if (fake && f->target && !f->target->test_pixels.empty())
                    last_pixel = f->target->test_pixels[0];
                layout_slot = (layout_slot + 1) % layouts.size();
                layouts[layout_slot] = f->layout;
                layout_valid = true;
                host_gate_publish_layout(f->layout);
                if (f->capture && f->composed && device) {
                    HostCompletedComposite result;
                    result.w = f->composed->w;
                    result.h = f->composed->h;
                    result.guest_w = f->input.guest_w;
                    result.guest_h = f->input.guest_h;
                    result.cls = f->cls;
                    result.frame_id = f->frame_id;
                    result.layout = f->layout;
                    result.rgba.resize(size_t(result.w) * result.h * 4);
                    device->readback(f->composed->texture, {0, 0, result.w, result.h},
                                     result.rgba.data(), result.w * 4);
                    f->capture(result);
                    f->capture = {};
                }
                if (f->composed && device && !f->repeat)
                    queue_composed_float(*f);   // under the lock: only queued (the readback waits for the GPU)
            }
            release(f);
            flights.pop_front();
            wake.notify_one();
        }
    }
    // R2 #10 TEST (review/r2close/hdr-design.md 7.5; RECOMP_DUMP_COMPOSED_FLOAT=N): every Nth new frame whose composed
    // texture is RGBA16F (what the HDR drawable shows: after the shoulder, HUD composited) as raw RGBA half plus a json
    // (size, the highlight ceiling H, frame id, mode) into RECOMP_HOST_DUMP_DIR. sweep() runs under `mutex` inside GPU
    // completion callbacks, and a readback waits for the GPU (whose callbacks take `mutex`): so sweep only queues the
    // frame (the shared_ptr keeps its texture from reuse) and the presenter thread writes it with the lock released
    // (flush_composed_floats, after hdr_poll). Test only.
    struct ComposedDump {
        std::shared_ptr<Composite> composed;
        uint64_t frame_id;
        float headroom;
        bool hdr_float, hdr_scene, pattern;
    };
    std::vector<ComposedDump> composed_pending;   // guarded by mutex
    unsigned composed_dumps = 0;
    void queue_composed_float(const Frame &f) {
        static const int every = [] { const char *e = recomp_env("DUMP_COMPOSED_FLOAT"); return e ? std::max(0, std::atoi(e)) : 0; }();
        static const char *dir = recomp_env("HOST_DUMP_DIR");
        if (every <= 0 || !dir || f.frame_id % uint64_t(every) != 0 || f.composed->format != gpu::Format::RGBA16F ||
            composed_pending.size() >= 4)
            return;
        composed_pending.push_back({f.composed, f.frame_id, f.input.hdr_headroom, f.input.hdr_float, f.input.hdr_scene,
                                    f.input.hdr_pattern});
    }
    void flush_composed_floats() {
        std::vector<ComposedDump> todo;
        {
            std::lock_guard lock(mutex);
            if (composed_pending.empty())
                return;
            todo.swap(composed_pending);
        }
        static const char *dir = recomp_env("HOST_DUMP_DIR");
        for (const ComposedDump &d : todo) {
            const int w = d.composed->w, h = d.composed->h;
            if (!dir || w <= 0 || h <= 0)
                continue;
            std::vector<uint16_t> px(size_t(w) * h * 4);
            if (!device->readback(d.composed->texture, {0, 0, w, h}, px.data(), w * 8))
                continue;
            char path[1024];
            snprintf(path, sizeof path, "%s/composed-%06llu.rgba16f", dir, (unsigned long long)d.frame_id);
            if (FILE *o = fopen(path, "wb")) {
                fwrite(px.data(), 2, px.size(), o);
                fclose(o);
            }
            snprintf(path, sizeof path, "%s/composed-%06llu.json", dir, (unsigned long long)d.frame_id);
            if (FILE *o = fopen(path, "w")) {
                fprintf(o, "{\"w\": %d, \"h\": %d, \"headroom\": %.4f, \"frame_id\": %llu, \"hdr_float\": %d, "
                           "\"hdr_scene\": %d, \"pattern\": %d}\n",
                        w, h, d.headroom, (unsigned long long)d.frame_id, int(d.hdr_float), int(d.hdr_scene), int(d.pattern));
                fclose(o);
            }
            if (++composed_dumps == 1) {
                printf("presenter: TEST composed float dumps (%dx%d, H %.2f) into %s\n", w, h, d.headroom, dir);
                fflush(stdout);
            }
        }
    }
    void drop(const std::shared_ptr<Frame> &f) {
        ++drops;
        f->dropped = true;
        f->gpu = true;
        retiring.push_back(f);
        sweep();
    }
    void completion_fallback(const std::shared_ptr<Frame> &f, double ts) { // mutex held
        if (!f || f->released || f->dropped || f->shown || !f->gpu || !f->success ||
            ts < f->acknowledgement_deadline)
            return;
        fault(4,
              "drawable acknowledgement exceeded queue grace; using command completion fallback");
        f->shown = true;
        f->completion_fallback = true;
        f->presented_ts = f->gpu_ts;
        trace_ack(*f, "timeout", ts);
    }
    void ack_command(const std::shared_ptr<Frame> &f, bool success, double ts) {
        std::lock_guard lock(mutex);
        if (f->released)
            return;
        f->gpu = true;
        f->success = f->success && success;
        f->gpu_ts = ts;
        // Slow GPU work is not a lost display callback. Give the ready
        // drawable two more refreshes without releasing its target early.
        f->acknowledgement_deadline = std::max(f->acknowledgement_deadline, ts + 2 * frame_period);
        trace_ack(*f, "gpu", ts);
        if (offscreen || !success || stop) {
            f->shown = true;
            f->presented_ts = ts;
        } else
            completion_fallback(f, ts);
        sweep();
        completed.notify_all();
        wake.notify_one();
    }
    void ack_presented(const std::shared_ptr<Frame> &f, double ts) {
        std::lock_guard lock(mutex);
        trace_ack(*f, "presented", ts);
        if (!(ts > 0) && !fake)
            return; // zero presentedTime is not presentation
        if (f->released || f->dropped || f->shown)
            return;
        f->shown = true;
        f->presented_ts = ts;
        sweep();
    }
    // Shared by the real device and the fake-device test. Register both
    // acknowledgements before present, and enqueue present before commit. The
    // shared pointer is captured by value: callbacks run after the worker's
    // stack frame is gone.
    void commit_present(std::shared_ptr<Frame> f, gpu::Texture drawable, gpu::CommandBuffer cb) {
        auto self = shared_from_this();
        std::weak_ptr<Service> weak = self;
        double period;
        {
            std::lock_guard lock(mutex);
            ++pending_commands;
            if (f->repeat)
                ++repeats;
            period = frame_period;
        }
        device->on_complete(cb, [self, f](gpu::CommandStatus status, double gpu_ms) {
            double ts;
            {
                std::lock_guard lock(self->mutex);
                ts = self->now();
                f->gpu_ms = self->fake ? 0 : std::max(0.0, gpu_ms);
            }
            self->ack_command(f, status == gpu::CommandStatus::Completed, ts);
            std::lock_guard lock(self->mutex);
            --self->pending_commands;
            self->completed.notify_all();
        });
        if (drawable) {
            // Keep each drawable for a refresh. Combined with queue-aware
            // acknowledgement grace, this avoids retiring real displayed
            // frames on timeout without slowing the pipeline to one flight.
            device->present(cb, chain, drawable, duration_pacing ? period : 0.0,
                            [weak, f](double ts) {
                                if (auto service = weak.lock()) {
                                    if (ts > 0)
                                        service->ack_presented(f, ts);
                                    else {
                                        std::lock_guard lock(service->mutex);
                                        service->trace_ack(*f, "presented", ts);
                                    }
                                }
                            });
        }
        {
            std::lock_guard lock(mutex);
            f->command_id = cb.id;
            f->command_commit_ts = now();
        }
        device->commit(cb);
    }
    uint64_t sched_prev[8] = {0, 0, 0, 0, 0, 0, 0, 0};
    bool sched_primed = false;
    uint64_t cpu_prev = 0;
    std::thread::id cpu_thread{};
    void seal(uint64_t id, HostScreenClass cls, bool had_draws, bool prefix_pending = false) {
        std::lock_guard lock(mutex);
        if (stop || !writing)
            return;
        auto f = std::move(writing);
        f->frame_id = id;
        f->cls = cls;
        f->had_draws = had_draws;
        f->sealed_ts = now();
        if (timings) {
            const uint64_t cpu = os_thread_cpu_ns();
            const std::thread::id self = std::this_thread::get_id();
            f->guest_cpu_ms = cpu_prev && self == cpu_thread && cpu >= cpu_prev
                                  ? double(cpu - cpu_prev) / 1e6 : -1.0;
            cpu_prev = cpu;
            cpu_thread = self;
        }
        if (timings && g_sched_probe) {
            uint64_t t[8] = {0, 0, 0, 0, 0, 0, 0, 0};
            g_sched_probe(t);
            if (sched_primed) {
                f->guest_yield_ms = double(t[0] - sched_prev[0]) / 1e6;
                f->guest_blocked_ms = double(t[1] - sched_prev[1]) / 1e6;
                f->guest_late_ms = double(t[2] - sched_prev[2]) / 1e6;
                f->guest_max_starve_ms = double(t[3]) / 1e6;
                f->guest_sleep_req_ms = double(t[4] - sched_prev[4]) / 1e6;
                f->guest_sleep_over_ms = double(t[5] - sched_prev[5]) / 1e6;
                f->guest_wait_ms = double(t[6] - sched_prev[6]) / 1e6;
                f->guest_over_others_ms = double(t[7] - sched_prev[7]) / 1e6;
            }
            for (int i : {0, 1, 2, 4, 5, 6, 7})
                sched_prev[i] = t[i];
            sched_primed = true;
        }
        if (class_known && last_class != cls) {
            ++epoch;
            metric = {};
        }
        class_known = true;
        last_class = cls;
        f->epoch = epoch;
        // This frame was produced with the old mode. Apply live rendering
        // changes only after taking its snapshot, for the next producer frame.
        f->input.classic = mods_display_classic() != 0;
        f->input.narrow = mods_display_wide() == 0;
        mods_display_transition(epoch, cls);
        // CPU-only Classic classes use the entire guest surface, including
        // menu backgrounds that Enhanced extracts as individual UI records.
        if (f->input.classic && cls != HOST_SCREEN_GAMEPLAY && f->staged_pixels) {
            f->input.legacy = true;
            f->input.legacy_frame = f->target->pixels;
            f->input.guest_w = guest_w;
            f->input.guest_h = guest_h;
            f->input.world = {};
            f->input.overlay = {};
            f->supplied = true;
        }
        f->input.scale_override = mods_display_scale();
        if (prefix_pending)
            f->prefix->done = false;
        if (f->dropped) {
            f->gpu = true;
            retiring.push_back(f);
            sweep();
            return;
        }
        f->input.cls = cls;
        const HostGameRect rect = game_rect();
        f->input.drawable_w = rect.w;
        f->input.drawable_h = rect.h;
        if (f->input.world && f->input.guest_w > 0 && f->input.guest_h > 0) {
            const int domain =
                f->input.scene.domain_w > 0 ? f->input.scene.domain_w : f->input.guest_w;
            f->input.scene = {float(rect.w) / domain, float(rect.h) / f->input.guest_h, 0, 0,
                              domain};
        }
        if (!f->supplied) {
            // Explicit CPU-staged/legacy compatibility input. Incremental
            // gameplay and the UI extractor supply their layered inputs.
            f->input.legacy = true;
            f->input.legacy_frame = f->target->pixels;
            f->input.guest_w = guest_w;
            f->input.guest_h = guest_h;
        }
        f->input.ui = &f->ui;
        if (!f->target->scene.w || (!fake && !f->supplied && !f->target->pixels)) {
            drop(f);
            return;
        }
        if (mailbox.size() == 2) {
            auto old = std::find_if(mailbox.begin(), mailbox.end(),
                                    [](const auto &v) { return !v->capture; });
            if (old == mailbox.end()) {
                drop(f);
                return;
            }
            auto discarded = *old;
            mailbox.erase(old);
            drop(discarded);
        }
        mailbox.push_back(std::move(f));
        seal_pending = true;
        wake.notify_one();
    }
    gpu::Texture texture(int w, int h, gpu::Format format = gpu::Format::RGBA8) {
        if (!device || w <= 0 || h <= 0)
            return {};
        return device->create_texture(
            {w, h, format, gpu::UsageSampled | gpu::UsageRenderTarget | gpu::UsageCpu, 1});
    }
    std::shared_ptr<Composite> composite(int w, int h, gpu::Format format) {
        gpu::Texture t = texture(w, h, format);
        if (!t)
            return nullptr;
        auto c = std::make_shared<Composite>();
        c->device = device;
        c->texture = t;
        c->w = w;
        c->h = h;
        c->format = format;
        return c;
    }
    // Apply backpressure before the first write. Dropping an unrendered frame
    // loses persistent surface updates (dirty HUD rectangles, cursor erasure),
    // even if later whole-scene frames can still be displayed. Completed frames
    // may be discarded by the mailbox after their surface contents are retained.
    HostSceneTarget acquire(int gw, int gh, int rw, int rh) {
        std::unique_lock lock(mutex);
        if (stop || gw <= 0 || gh <= 0)
            return {};
        if (writing)
            return writing->target ? writing->target->scene : HostSceneTarget{};
        guest_w = gw;
        guest_h = gh;
        const HostGameRect rect = game_rect();
        const uint64_t resolution = render_resolution.load();
        if (rw <= 0 && rh <= 0 && resolution) {
            rw = int(resolution >> 32);
            rh = int(uint32_t(resolution));
        }
        if (rw <= 0)
            rw = rect.w;
        if (rh <= 0)
            rh = rect.h;
        if (mods_display_classic()) {
            rw = gw;
            rh = gh;
        }
        requested_w = rw;
        auto free_slot = [&]() -> int {
            for (size_t i = 0; i < (flight_limit == 1 ? 4 : flight_limit + 4); ++i)
                if (!targets[i] || targets[i].use_count() == 1)
                    return int(i);
            return -1;
        };
        int slot = free_slot();
        double slot_wait_ms = 0;
        if (slot < 0) {
            ++waits;
            wake.notify_one();
            const double wait_start = now();
            // Keep a stalled GPU/worker from trapping the guest forever. This
            // is a fault deadline, not the normal frame pacing mechanism.
            completed.wait_for(lock, std::chrono::milliseconds(250), [&] {
                slot = free_slot();
                return stop || slot >= 0;
            });
            slot_wait_ms = (now() - wait_start) * 1000;
            if (stop)
                return {};
        }
        if (slot < 0) {
            // Latch the drop until seal: later draws/CPU staging in this same
            // guest frame must neither retry acquisition nor count it twice.
            ++drops;
            fault(1, "target pool stalled for 250ms; guest frame dropped, check "
                     "worker/GPU/presentation progress");
            writing = std::make_shared<Frame>();
            writing->dropped = true;
            return {};
        }
        int w = std::max(gw, rw), h = std::max(gh, rh);
        auto target = targets[slot];
        if (!target)
            target = std::make_shared<Target>();
        target->device = fake ? nullptr : device;
        const gpu::TextureDesc overlay_desc = !fake && target->scene.overlay
                                                  ? device->describe(target->scene.overlay)
                                                  : gpu::TextureDesc{};
        if (target->scene.w != w || target->scene.h != h ||
            (!fake && (overlay_desc.width != gw || overlay_desc.height != gh))) {
            target->release_scene();
            while (true) {
                bool failed = fail_allocations > 0;
                if (failed)
                    --fail_allocations;
                gpu::Texture world, overlay;
                if (!fake && !failed) {
                    world = texture(w, h, gpu::Format::BGRA8);
                    overlay = texture(gw, gh, gpu::Format::BGRA8);
                    failed = !world || !overlay;
                    if (failed) {
                        if (world)
                            device->destroy(world);
                        if (overlay)
                            device->destroy(overlay);
                    }
                }
                if (!failed) {
                    if (fake) {
                        world = {fake_texture_ids++};
                        overlay = {fake_texture_ids++};
                    }
                    target->scene = {world, overlay, w, h};
                    break;
                }
                if (w == gw && h == gh) {
                    fprintf(stderr,
                            "presenter: allocation failed at guest size %dx%d; frame target "
                            "unavailable\n",
                            gw, gh);
                    target->scene = {};
                    break; // reserve a failed writer too; its GPU prefixes still retire safely
                }
                w = std::max(gw, w / 2);
                h = std::max(gh, h / 2);
                fprintf(
                    stderr,
                    "presenter: scene allocation failed; retry %dx%d (requested %dx%d unchanged)\n",
                    w, h, rw, rh);
            }
        }
        targets[slot] = target;
        writing = std::make_shared<Frame>();
        writing->target = target;
        writing->slot_wait_ms = slot_wait_ms;
        return target->scene;
    }
    void publish_layout(Frame &f) {
        f.layout = compositor_layout_snapshot(&f.input);
        f.layout.frame_id = f.frame_id;
    }

    void tick(double ts) {
        if (!std::isfinite(ts) || ts < 0)
            return;
        if (auto m = message.exchange(nullptr)) {
            if (m->install) {
                if (device && chain) {
                    device->destroy(chain);
                    chain = {};
                }
                surface = m->surface;
                if (device && surface && !fake)
                    chain = device->create_swapchain(surface, m->w, m->h);
                std::lock_guard lock(mutex);
                if (!flights.empty()) {
                    // A drawable on the detached layer may never present. Cancel
                    // that presentation, retaining its slot until its GPU work ends.
                    for (auto &f : flights) {
                        ++drops;
                        f->dropped = true;
                        retiring.push_back(f);
                    }
                    flights.clear();
                    sweep();
                }
            } else if (!fake && device && chain)
                device->resize(chain, m->w, m->h);
        }
        std::shared_ptr<Frame> f;
        HostGameRect rect{};
        bool placed = false;
        {
            std::lock_guard lock(mutex);
            if (stop)
                return;
            if (fake)
                fake_now = ts;
            rect = game_rect(&placed);
            if (class_known && last_class == HOST_SCREEN_GAMEPLAY)
                metric.tick(ts);
            sweep();
            if (!offscreen) {
                if (window_started < 0)
                    window_started = ts;
                if ((!flights.empty() || !mailbox.empty()) &&
                    ts - std::max(window_started, last_link_tick) >= 1.0)
                    fault(2, "display link is not firing; worker is using timed fallback");
                for (auto &pending : flights) {
                    completion_fallback(pending, ts);
                    if (!pending->gpu && ts - pending->submitted_ts >= 1.0) {
                        if (!(logged_faults & 16))
                            fprintf(stderr,
                                    "presenter: GPU timeout evidence frame=%llu cb=%llu "
                                    "class=%d repeat=%d submit_age_ms=%.3f commit_age_ms=%.3f\n",
                                    (unsigned long long)pending->frame_id,
                                    (unsigned long long)pending->command_id, int(pending->cls),
                                    int(pending->repeat), (ts - pending->submitted_ts) * 1000.0,
                                    pending->command_id ? (ts - pending->command_commit_ts) * 1000.0
                                                        : -1.0);
                        fault(16, "GPU completion timed out; retaining target until completion");
                    }
                }
                sweep();
            }
            if (flights.size() >= flight_limit)
                return;
            if (mailbox.empty()) {
                // cached is the last acknowledged image. Repeating it behind
                // a newer queued image would visibly move the game backwards.
                if (!flights.empty())
                    return;
                if (!last_id)
                    return;
                f = std::make_shared<Frame>();
                f->repeat = true;
                f->frame_id = last_id;
                f->diagnostic = cached_diagnostic;
                f->sealed_ts = cached_sealed_ts;
                f->screen_class_valid = cached_screen_class_valid;
                f->cls = cached_input.cls;
                f->epoch = epoch;
                f->input = cached_input;
                f->ui = cached_ui;
                f->input.ui = &f->ui;
                f->target = cached_target;
                // Run 39: the repeat holds the page it shows (a refresh may replace cached_settings while this frame
                // is in flight; the Composite owns the texture), and takes a pending recompose.
                f->settings_page = cached_settings;
                f->recompose = recompose_pending;
                recomposed_repeats += recompose_pending;
                recompose_pending = false;
                // Retain the original guest domain and source texture. The
                // cached composite is already drawable-sized; declaring that
                // size to be guest coordinates corrupts picking on repeats.
                // cached_target keeps the original texture out of the pool.
                if (cached_input.drawable_w > 0 && cached_input.drawable_h > 0) {
                    float sx = float(rect.w) / cached_input.drawable_w;
                    float sy = float(rect.h) / cached_input.drawable_h;
                    f->input.scene.scale_x *= sx;
                    f->input.scene.offset_x *= sx;
                    f->input.scene.scale_y *= sy;
                    f->input.scene.offset_y *= sy;
                }
            } else {
                auto requested = std::find_if(mailbox.begin(), mailbox.end(),
                                              [](const auto &v) { return bool(v->capture); });
                if (requested != mailbox.end()) {
                    f = *requested;
                    mailbox.erase(requested);
                } else {
                    f = mailbox.back();
                    mailbox.pop_back();
                }
                for (auto it = mailbox.begin(); it != mailbox.end();) {
                    if ((*it)->capture) {
                        ++it;
                        continue;
                    }
                    auto old = *it;
                    it = mailbox.erase(it);
                    drop(old);
                }
            }
            flights.push_back(f);
            seal_pending = !mailbox.empty();
            f->submitted_ts = ts;
            f->acknowledgement_deadline = ts + acknowledgement_grace();
            f->input.drawable_w = rect.w;
            f->input.drawable_h = rect.h;
            // R3 run 17 (D-119): a world frame sealed before a resize and presented after it is drawn over the NEW
            // rectangle, so its scene mapping must be the new one too (as at seal); it published the old scale with
            // the new drawable, and the pointer hit-tested against it for that frame. Repeats are rescaled above.
            if (!f->repeat && f->input.world && f->input.guest_w > 0 && f->input.guest_h > 0) {
                const int domain = f->input.scene.domain_w > 0 ? f->input.scene.domain_w : f->input.guest_w;
                f->input.scene = {float(rect.w) / domain, float(rect.h) / f->input.guest_h, 0, 0, domain};
            }
        }
        auto prepare = [&] {
            std::lock_guard lock(mutex);
            if (!f->repeat) {
                if (history_epoch != f->epoch) {
                    history.world = {};
                    history.overlay = {};
                    history_lease.reset();
                    history_epoch = f->epoch;
                }
                bool reused = compositor_resolve_scene(&history, &f->input, f->had_draws);
                if (!reused)
                    history_lease = history.world ? f->target : nullptr;
                f->scene_lease = history_lease;
            }
            publish_layout(*f);
        };
        if (fake) {
            prepare();
            {
                std::lock_guard lock(mutex);
                if (f->repeat)
                    ++repeats;
            }
            if (automatic) {
                ack_command(f, true, ts);
                if (!offscreen)
                    ack_presented(f, ts);
            }
            return;
        }
        {
            // The worker is the only caller of acquire and present.
            // Suspended (iOS background): compose and complete, but never touch a drawable.
            gpu::Texture drawable = offscreen || !chain || g_present_suspended.load()
                                        ? gpu::Texture{}
                                        : device->acquire(chain);
            if (!offscreen && !drawable) {
                if (f) {
                    std::lock_guard lock(mutex);
                    flights.erase(std::find(flights.begin(), flights.end(), f));
                    fault(8, "swapchain returned no drawable; retrying newest frame");
                    // Keep only a newest unpresented frame. A repeat has no arena.
                    if (f->repeat)
                        release(f);
                    else if (!mailbox.empty())
                        drop(f);
                    else
                        mailbox.push_front(f);
                }
                return;
            }
            int w = rect.w, h = rect.h;
            const gpu::TextureDesc drawable_desc =
                drawable ? device->describe(drawable) : gpu::TextureDesc{};
            const gpu::Format out_format = offscreen ? gpu::Format::RGBA8 : drawable_desc.format;
            bool reuse_composition = f->repeat && !f->recompose && cached && cached->w == w && cached->h == h &&
                                     cached->format == out_format;
            std::shared_ptr<Composite> out =
                reuse_composition ? cached : composite(w, h, out_format);
            gpu::CommandBuffer cb = device->begin();
            if (!cb || !out) {
                if (drawable)
                    device->release_drawable(chain, drawable);
                if (f) {
                    std::lock_guard lock(mutex);
                    flights.erase(std::find(flights.begin(), flights.end(), f));
                    drop(f);
                }
                return;
            }
            auto start = Clock::now();
            prepare();
            hdr_poll();
            flush_composed_floats();   // R2 #10 TEST: outside the lock
            f->input.hdr_scene = out_format == gpu::Format::RGBA16F && f->hdr_scene_mask;
            f->input.hdr_headroom = float(hdr_target);
            f->input.hdr_float = out_format == gpu::Format::RGBA16F && f->hdr_float;   // item 10: mode 3/4
            f->input.hdr_desat = hdr_desat;
            static const bool hdr_pattern = [] { const char *e = recomp_env("TEST_HDR_PATTERN"); return e && std::atoi(e) != 0; }();
            f->input.hdr_pattern = hdr_pattern;   // R2 #10 TEST: the bars (compositor mode 5)
            {   // 3.0.4 Brightness (core mod brightness, -5..+5): read every 30 compositions, as the HDR settings are
                static uint64_t polls = 0;
                static float exponent = 1.0f;
                if (polls++ % 30 == 0) {
                    const int64_t b = std::clamp<int64_t>(mods_core_setting("brightness", 0), -5, 5);
                    exponent = std::exp2(-0.12f * float(b));
                }
                f->input.brightness = exponent;
            }
            if (!reuse_composition)
                compositor_compose(device, &f->input, out->texture, cb);
            f->composed = out;
            if (drawable) {
                if (out->w == drawable_desc.width && out->h == drawable_desc.height &&
                    out->format == drawable_desc.format) {
                    device->blit(cb, out->texture, {0, 0, out->w, out->h}, drawable, 0, 0);
                } else if (placed && out->w == rect.w && out->h == rect.h &&
                           rect.x + rect.w <= drawable_desc.width &&
                           rect.y + rect.h <= drawable_desc.height &&
                           out->format == drawable_desc.format) {
                    // Portrait: the game sits at its rectangle. The rest takes
                    // the controls area's colour, drawn or not.
                    gpu::RenderPass clear;
                    clear.color_count = 1;
                    clear.color[0].texture = drawable;
                    clear.color[0].load = gpu::Load::Clear;
                    clear.color[0].store = gpu::Store::Store;
                    clear.color[0].clear[0] = 12 / 255.0f;
                    clear.color[0].clear[1] = 14 / 255.0f;
                    clear.color[0].clear[2] = 18 / 255.0f;
                    clear.color[0].clear[3] = 1;
                    device->begin_render_pass(cb, clear);
                    device->end_render_pass(cb);
                    device->blit(cb, out->texture, {0, 0, out->w, out->h}, drawable, rect.x,
                                 rect.y);
                } else {
                    CompositorInput in{};
                    in.legacy = true;
                    in.hdr_linear = out->format == gpu::Format::RGBA16F; // Test86: already linear
                    in.legacy_frame = out->texture;
                    in.guest_w = out->w;
                    in.guest_h = out->h;
                    in.drawable_w = drawable_desc.width;
                    in.drawable_h = drawable_desc.height;
                    compositor_compose(device, &in, drawable, cb);
                }
                FramePacingSnapshot snapshot;
                {
                    std::lock_guard lock(mutex);
                    snapshot = pacing.snapshot(ts, drops);
                }
                performance_overlay.draw(device, cb, drawable, drawable_desc.width,
                                         drawable_desc.height, snapshot, ts, mods_display_overlay(),
                                         mods_display_fps(), f->diagnostic, f->frame_id);
                const controls::ControlsView controls_view = host_present_controls();
                if (controls_view.wanted)
                    controls_overlay.draw(device, cb, drawable, drawable_desc.width,
                                          drawable_desc.height, controls_view);
            }
            host_stats_note_phase(
                HOST_PHASE_COMPOSITE,
                std::chrono::duration<double, std::milli>(Clock::now() - start).count());
            commit_present(f, drawable, cb);
        }
    }
    // One wake per refresh, on the device clock. Replaces CVDisplayLink: it
    // only schedules work, never acknowledges a submitted drawable.
    void start_pacer() {
        pacer = std::thread([this] {
            std::unique_lock lock(mutex);
            while (!pacer_stop && !stop) {
                const double period = chain ? pacing_period(device->refresh_period(chain)) : frame_period;
                frame_period = period > 0 ? period : 1.0 / 60;
                const double now = device->now_seconds();
                const double next = (std::floor(now / frame_period) + 1) * frame_period;
                pacer_wake.wait_for(lock, std::chrono::duration<double>(std::max(0.0, next - now)),
                                    [&] { return pacer_stop || stop; });
                if (pacer_stop || stop)
                    break;
                last_link_tick = timestamp = device->now_seconds();
                tick_pending = true;
                wake.notify_one();
            }
        });
    }
    void stop_pacer() {
        {
            std::lock_guard lock(mutex);
            pacer_stop = true;
            pacer_wake.notify_all();
        }
        if (pacer.joinable())
            pacer.join();
    }
    void run() {
        {
            auto synthetic_start = Clock::now();
            double synthetic_origin = device ? device->now_seconds() : 0.0;
            uint64_t synthetic_index = 0;
            while (true) {
                double ts;
                {
                    std::unique_lock lock(mutex);
                    if (offscreen) {
                        // A synthetic link, tied to monotonic host time and independent
                        // of the pinned simulation clock. The guest never waits here.
                        auto elapsed =
                            std::chrono::duration<double>(Clock::now() - synthetic_start).count();
                        synthetic_index =
                            std::max(synthetic_index + 1, uint64_t(elapsed * kOffscreenHz) + 1);
                        auto deadline =
                            synthetic_start +
                            std::chrono::duration_cast<Clock::duration>(
                                std::chrono::duration<double>(synthetic_index / kOffscreenHz));
                        wake.wait_until(lock, deadline, [&] { return stop; });
                        ts = synthetic_origin + synthetic_index / kOffscreenHz;
                    } else {
                        // Seal is a bootstrap event, not an acknowledgement. A
                        // bounded wait also services a stopped link and checks
                        // lost presented callbacks without blocking the guest.
                        double now = this->now();
                        double delay = 0.016;
                        for (auto &pending : flights)
                            if (!pending->shown &&
                                (pending->gpu || now < pending->acknowledgement_deadline))
                                delay = std::max(
                                    0.0, std::min(delay, pending->acknowledgement_deadline - now));
                        // Recompute after every completion notification, even
                        // without a display-link callback or a new guest seal.
                        if (!window_ready())
                            wake.wait_for(lock, std::chrono::duration<double>(delay));
                        ts = this->now();
                        tick_pending = false;
                    }
                    if (stop)
                        break;
                }
                tick(ts);
            }
        }
    }
};
AtomicShared<Service> active;
gpu::Device *g_device = nullptr;

std::shared_ptr<Service> begin(bool fake, bool automatic, bool offscreen) {
    host_present_stop();
    auto s = std::make_shared<Service>();
    s->fake = fake;
    s->automatic = automatic;
    s->offscreen = offscreen;
    s->device = g_device;
    if (!fake && !s->device) {
        fprintf(stderr, "presenter: no GPU device installed; call host_present_set_device first\n");
        abort();
    }
    if (const char *mode = recomp_env("HOST_PRESENT_PACING"))
        s->duration_pacing = strcmp(mode, "immediate") != 0;
    if (!fake)
        if (const char *path = recomp_env("PRESENT_ACK_TRACE")) {
            s->ack_trace = fopen(path, "w");
            if (s->ack_trace)
                fprintf(
                    s->ack_trace,
                    "frame_id,screen_class,repeat,event,observed_s,event_s,"
                    "submitted_s,gpu_s,deadline_s,shown,fallback,released,screen_class_valid\n");
            else
                fprintf(stderr, "presenter: cannot open acknowledgement trace %s\n", path);
        }
    if (!fake)
        if (const char *path = recomp_env("FRAME_TIMINGS")) {
            s->timings = fopen(path, "w");
            if (s->timings) {
                setvbuf(s->timings, nullptr, _IOFBF, 65536);
                fprintf(s->timings,
                        "frame_id,screen_class,repeat,sealed_s,submitted_s,presented_s,"
                        "gpu_ms,drops,display_ack,drawable_w,drawable_h,screen_class_valid,render_"
                        "frame,render_monotonic_ns,capture_id,detail_sample,slot_wait_ms,"
                        "guest_yield_ms,guest_blocked_ms,guest_late_ms,guest_max_starve_ms,guest_cpu_ms,"
                        "guest_sleep_req_ms,guest_sleep_over_ms,guest_wait_ms,guest_over_others_ms,game_gpu_ms\n");
                fflush(s->timings);
            } else
                fprintf(stderr, "presenter: cannot open frame timings %s\n", path);
        }
    active.store(s);
    if (!fake)
        ddraw_set_present_callbacks(host_present_first_write, host_frame_seal);
    return s;
}
void test_ack(uint64_t id, int kind, double ts) {
    auto s = active.load();
    if (!s)
        return;
    std::shared_ptr<Frame> f;
    {
        std::lock_guard lock(s->mutex);
        if (kind == 2) {
            for (auto &a : s->retiring)
                if (a->frame_id == id)
                    a->prefix->done = true;
            for (auto &a : s->mailbox)
                if (a->frame_id == id)
                    a->prefix->done = true;
            for (auto &a : s->flights)
                if (a->frame_id == id)
                    a->prefix->done = true;
            s->sweep();
            return;
        }
        for (auto &candidate : s->flights)
            if (candidate->frame_id == id) {
                f = candidate;
                break;
            }
        if (!f)
            for (auto &candidate : s->retiring)
                if (candidate->frame_id == id) {
                    f = candidate;
                    break;
                }
    }
    if (!f)
        return;
    if (kind == 0)
        s->ack_command(f, true, ts);
    else
        s->ack_presented(f, ts);
}
} // namespace

void host_present_set_sched_probe(void (*probe)(uint64_t out[8])) {
    g_sched_probe = probe;
}

void host_present_set_device(gpu::Device *device) {
    g_device = device;
}
gpu::Device *host_present_device() {
    return g_device;
}
void host_present_start(void *native_surface, int w, int h) {
    auto s = begin(false, true, false);
    // Display acknowledgements can arrive three refreshes after submission.
    // Match the swapchain's three drawables so that delay does not cap a 120 Hz
    // producer near 80 FPS. Keep smaller queues available for comparisons.
    s->flight_limit = 3;
    if (const char *value = recomp_env("HOST_PRESENT_FRAMES")) {
        if (!strcmp(value, "1"))
            s->flight_limit = 1;
        else if (!strcmp(value, "2"))
            s->flight_limit = 2;
    }
    s->surface = native_surface;
    s->drawable_w = w;
    s->drawable_h = h;
    s->chain =
        native_surface ? s->device->create_swapchain(native_surface, w, h) : gpu::Swapchain{};
    fprintf(stderr, "presenter: %dx%d drawable, at most %zu display submissions in flight\n",
            int(s->drawable_w), int(s->drawable_h), s->flight_limit);
    if (!s->chain || w <= 0 || h <= 0) {
        fprintf(stderr, "presenter: requires an attached, sized surface on the renderer device\n");
        abort();
    }
    s->frame_period = pacing_period(s->device->refresh_period(s->chain));
    fprintf(stderr, "presenter: display refresh %.2f Hz, paced at %.2f Hz\n", 1.0 / s->device->refresh_period(s->chain),
            1.0 / s->frame_period);
#ifdef __EMSCRIPTEN__
    // The browser's main loop owns the GPU and calls host_present_pump once
    // per animation frame: no worker, no pacer.
    (void)0;
#else
    s->start_pacer();
    s->worker = std::thread([s] { s->run(); });
#endif
}
// One presenter turn on the calling thread: for hosts whose GPU belongs to a
// loop they do not own (the browser's main thread).
void host_present_pump(void) {
    auto s = active.load();
    if (!s)
        return;
    const double ts = s->device ? s->device->now_seconds() : 0.0;
    {
        std::lock_guard lock(s->mutex);
        if (s->stop)
            return;
        s->last_link_tick = s->timestamp = ts;
        s->tick_pending = false;
    }
    s->tick(ts);
}
void host_present_start_offscreen(int w, int h) {
    auto s = begin(false, true, true);
    s->drawable_w = w;
    s->drawable_h = h;
    // The synthetic link below ticks at this rate; a Present that waits for a
    // refresh has to wait for the same one.
    s->frame_period = 1.0 / kOffscreenHz;
    s->worker = std::thread([s] { s->run(); });
}
void host_present_resize(int w, int h) {
    auto s = active.load();
    if (!s || w <= 0 || h <= 0)
        return;
    {
        // Both sizes change together under the mutex, so game_rect() never
        // sees (and publishes) a new width with an old height.
        std::lock_guard lock(s->mutex);
        s->drawable_w = w;
        s->drawable_h = h;
        s->publish_gate_size(s->game_rect());
    }
    auto m = std::make_shared<Message>();
    m->w = w;
    m->h = h;
    // Preserve an undelivered migration when a resize follows it in the same pump.
    auto old = s->message.load();
    do {
        m->install = old && old->install;
        m->surface = m->install ? old->surface : nullptr;
    } while (!s->message.compare_exchange_weak(old, m));
    s->wake.notify_one();
}
void host_present_install_surface(void *native_surface, int w, int h) {
    auto s = active.load();
    if (!s)
        return;
    {
        // Both sizes change together under the mutex, so game_rect() never
        // sees (and publishes) a new width with an old height.
        std::lock_guard lock(s->mutex);
        s->drawable_w = w;
        s->drawable_h = h;
        s->publish_gate_size(s->game_rect());
    }
    auto m = std::make_shared<Message>();
    m->surface = native_surface;
    m->w = w;
    m->h = h;
    m->install = true;
    s->message.store(m);
    s->wake.notify_one();
}
extern "C" void host_present_stop() {
    auto s = active.load();
    if (!s)
        return;
    host_gpu2d_release_device();
    {
        std::lock_guard lock(s->mutex);
        s->stop = true;
        s->wake.notify_all();
        s->completed.notify_all();
    }
    s->stop_pacer();
    if (s->worker.joinable())
        s->worker.join();
    {
        std::unique_lock lock(s->mutex);
        // Cancellation does not invent a presentedTime. On shutdown GPU completion
        // suffices for safe retirement, and cancelled frames never count unique.
        for (auto &f : s->flights) {
            f->shown = true;
            if (s->fake) {
                f->gpu = true;
                f->prefix->done = true;
            }
        }
        for (auto &f : s->mailbox) {
            f->gpu = true;
            s->retiring.push_back(f);
        }
        s->mailbox.clear();
        if (s->fake)
            for (auto &f : s->retiring)
                f->prefix->done = true;
        s->sweep();
        s->completed.wait(lock, [&] {
            s->sweep();
            return s->flights.empty() && s->retiring.empty() && s->pending_commands == 0;
        });
        s->writing.reset();
        s->history_lease.reset();
        s->cached_target.reset();
        s->history.world = {};
        s->history.overlay = {};
        s->cached.reset();
        s->cached_settings.reset();
        s->menu_page.reset();
        s->recompose_pending = false;
        s->cached_input = {};
        s->cached_ui = {};
        s->targets = {};
        if (s->device && s->chain) {
            s->device->destroy(s->chain);
            s->chain = {};
        }
    }
    if (!s->fake) {
        ddraw_set_present_callbacks(nullptr, nullptr);
        ddraw_drain_present_releases();
    }
    // Retain counters for the final report. begin() replaces this stopped service.
}
void host_present_drop_current() {
    auto s = active.load();
    if (!s)
        return;
    std::lock_guard lock(s->mutex);
    if (s->writing && !s->writing->dropped) {
        s->writing->dropped = true;
        ++s->drops;
    }
}
bool host_present_running() {
    auto s = active.load();
    if (!s)
        return false;
    std::lock_guard lock(s->mutex);
    return !s->stop && !s->fake;
}
std::shared_ptr<void> host_present_target_lease(gpu::Texture world) {
    auto s = active.load();
    if (!s || !world)
        return {};
    std::lock_guard lock(s->mutex);
    for (auto &t : s->targets)
        if (t && t->scene.world == world)
            return t;
    return {};
}
HostSceneTarget host_present_acquire_target(int gw, int gh, int w, int h) {
    auto s = active.load();
    return s ? s->acquire(gw, gh, w, h) : HostSceneTarget{};
}
extern "C" void host_present_first_write() {
    auto s = active.load();
    if (!s)
        return;
    host_d3d_collect_present_targets();
    int w = 0, h = 0, bpp = 0;
    host_present_mode(&w, &h, &bpp);
    s->acquire(w > 0 ? w : s->guest_w, h > 0 ? h : s->guest_h, 0, 0);
}
extern "C" int host_present_needs_legacy_pixels() {
#ifdef POPM_PRESENT_HAS_UI_LAYER
    auto s = active.load();
    if (!s || mods_display_classic())
        return 1;
    {
        std::lock_guard lock(s->mutex);
        if (s->stop || !s->writing)
            return 1;
    }
    const auto frame = host_frame_current();
    if (frame.id && host_frame_class(frame) == HOST_SCREEN_GAMEPLAY && !host_frame_legacy(frame))
        return 0;
#endif
    return 1;
}
extern "C" void host_present_stage_rgba(const uint8_t *rgba, int w, int h) {
    auto s = active.load();
    if (!s || !rgba || w <= 0 || h <= 0)
        return;
    s->acquire(w, h, 0, 0);
    std::lock_guard lock(s->mutex);
    if (s->stop || !s->writing || !s->writing->target)
        return;
    s->guest_w = w;
    s->guest_h = h;
    s->writing->diagnostic = {};
    s->writing->staged_pixels = true;
    s->writing->hdr_float = false;   // item 10: CPU-staged pixels are 8-bit
    auto &t = *s->writing->target;
    if (s->fake)
        t.test_pixels.assign(rgba, rgba + size_t(w) * h * 4);
    else {
        t.device = s->device;
        if (!t.pixels || t.pixels_w != w || t.pixels_h != h ||
            t.pixels_format != gpu::Format::RGBA8) {
            if (t.pixels)
                s->device->destroy(t.pixels);
            t.pixels = s->texture(w, h);
            t.pixels_format = gpu::Format::RGBA8;
            t.pixels_w = w;
            t.pixels_h = h;
        }
        if (t.pixels)
            s->device->upload(t.pixels, {0, 0, w, h}, rgba, w * 4);
    }
}
// Whether a real device is presenting, so a guest-side GPU path has a queue to
// put its work on and a frame to stage it into.
bool host_present_gpu_ready() {
    auto s = active.load();
    return s && !s->fake && s->device && !s->stop;
}
// The GPU counterpart of host_present_stage_rgba: the frame's pixels are a copy
// of `src`, encoded into `cb`. The caller commits `cb` before it seals, and the
// one queue runs that copy before anything composes the frame.
bool host_present_stage_texture(gpu::Texture src, int w, int h, int guest_w, int guest_h,
                                gpu::CommandBuffer cb, const CaptureMarker *capture) {
    auto s = active.load();
    if (!s || s->fake || !src || !cb || w <= 0 || h <= 0 || guest_w <= 0 || guest_h <= 0)
        return false;
    // A GPU blit copies bytes, not colors. Keep the source format so BGRA
    // backbuffers are not sampled as RGBA, including when a frame slot is reused.
    const auto format = s->device->describe(src).format;
    s->acquire(guest_w, guest_h, 0, 0);
    std::lock_guard lock(s->mutex);
    if (s->stop || !s->writing || !s->writing->target)
        return false;
    auto &t = *s->writing->target;
    t.device = s->device;
    if (!t.pixels || t.pixels_w != w || t.pixels_h != h || t.pixels_format != format) {
        if (t.pixels)
            s->device->destroy(t.pixels);
        t.pixels = s->texture(w, h, format);
        t.pixels_format = format;
        t.pixels_w = w;
        t.pixels_h = h;
    }
    if (!t.pixels)
        return false;
    // Supersampled pixels do not enlarge the guest's client area. Publishing
    // their dimensions here sends touches beyond that area and hides cursors.
    s->guest_w = guest_w;
    s->guest_h = guest_h;
    s->writing->diagnostic = capture ? *capture : CaptureMarker{};
    s->writing->staged_pixels = true;
    s->writing->hdr_scene_mask = g_host_hdr_scene_mask.load(std::memory_order_relaxed); // Test86
    s->writing->hdr_float = format == gpu::Format::RGBA16F;   // PLAN_R2 item 10: the renderer's float back buffer
    s->device->blit(cb, src, {0, 0, w, h}, t.pixels, 0, 0);
    return true;
}
// The drawable the presenter composes into, in pixels. False before one exists.
bool host_present_drawable(int *w, int *h) {
    auto s = active.load();
    if (!s)
        return false;
    std::lock_guard lock(s->mutex);
    if (s->drawable_w <= 0 || s->drawable_h <= 0)
        return false;
    *w = s->drawable_w;
    *h = s->drawable_h;
    return true;
}
void host_present_set_input(const CompositorInput *input) {
    auto s = active.load();
    if (!s || !input)
        return;
    std::lock_guard lock(s->mutex);
    if (!s->writing || !s->writing->target)
        return;
    auto &f = *s->writing;
    if ((input->world && input->world != f.target->scene.world) ||
        (input->overlay && input->overlay != f.target->scene.overlay)) {
        fprintf(stderr,
                "presenter: compositor input does not belong to the acquired scene target\n");
        return;
    }
    f.input = *input;
    f.supplied = true;
    if (input->ui)
        f.ui = *input->ui;
    else
        f.ui = {};
    f.input.ui = &f.ui;
}
namespace {
struct PrefixHandle {
    std::shared_ptr<Service> service;
    std::shared_ptr<Fence> fence;
    std::shared_ptr<Frame> frame;
};
} // namespace
void *host_present_prefix_begin() {
    auto s = active.load();
    if (!s)
        return nullptr;
    auto fence = std::make_shared<Fence>();
    fence->done = false;
    std::shared_ptr<Frame> frame;
    {
        std::lock_guard lock(s->mutex);
        if (!s->writing)
            return nullptr;
        frame = s->writing;
        frame->prefix = fence;
        ++frame->pending_prefixes;
        ++s->pending_commands;
    }
    return new PrefixHandle{s, fence, frame};
}
void host_present_prefix_done(void *handle, bool success) {
    std::unique_ptr<PrefixHandle> h(static_cast<PrefixHandle *>(handle));
    if (!h)
        return;
    std::lock_guard lock(h->service->mutex);
    h->fence->done = true;
    h->fence->success = success;
    h->frame->success = h->frame->success && h->fence->success;
    --h->frame->pending_prefixes;
    --h->service->pending_commands;
    h->service->sweep();
    h->service->completed.notify_all();
}
void host_present_track_command(gpu::CommandBuffer cb) {
    auto s = active.load();
    if (!s || !cb || !s->device)
        return;
    void *handle = host_present_prefix_begin();
    if (!handle)
        return;
    s->device->on_complete(cb, [handle](gpu::CommandStatus status, double) {
        host_present_prefix_done(handle, status == gpu::CommandStatus::Completed);
    });
}
// Seal guest-owned frame state and extract UI before publishing to the presentation worker.
// Only immutable snapshots cross that boundary; guest surface leases are resolved here.
// The open settings page rides on the frame about to be sealed, drawn by the
// compositor in host UI space. Every seal path takes it: a window present - a
// D3D11 renderer, a film - as much as a DirectDraw frame, or F10 opens a page
// nobody sees.
// The tabbed menu is drawn at the game rectangle's own size, so its text is sharp at any
// resolution, and only when something on it changed: the last texture rides on every frame
// in between. False when it is not showing, and the 640x480 page above it is asked instead.
// The menu page at the game rectangle's size (4K at most), drawn only when it changed since the texture the
// service holds: host_page_menu's 0 hidden, 1 unchanged, 2 drawn into *pixels. Called without s->mutex.
int draw_menu_page(const std::shared_ptr<Service> &s, int *pw, int *ph, uint64_t *generation, const uint8_t **pixels) {
    HostGameRect r = Service::unpack_rect(s->published_rect.load(std::memory_order_relaxed));
    int w = r.w, h = r.h;
    if (w <= 0 || h <= 0)
        w = s->drawable_w, h = s->drawable_h;
    if (w > 3840 || h > 2160) { // beyond 4K the compositor scales a 4K page up
        const double k = std::min(3840.0 / w, 2160.0 / h);
        w = std::max(1, int(w * k)), h = std::max(1, int(h * k));
    }
    *generation = 0;
    {
        std::lock_guard lock(s->mutex);
        if ((s->menu_page && s->menu_page->w == w && s->menu_page->h == h) ||
            (s->fake && s->fake_menu_w == w && s->fake_menu_h == h))
            *generation = s->menu_generation;
    }
    *pw = w, *ph = h;
    *pixels = nullptr;
    return host_page_menu(w, h, generation, pixels);
}

bool attach_menu_page(const std::shared_ptr<Service> &s) {
    int w = 0, h = 0;
    uint64_t generation = 0;
    const uint8_t *pixels = nullptr;
    const int drawn = draw_menu_page(s, &w, &h, &generation, &pixels);
    std::lock_guard lock(s->mutex);
    if (drawn == 0) {
        s->menu_page.reset();
        return false;
    }
    if (!s->writing)
        return true;
    ++s->settings_pages;
    if (s->fake)
        return true;
    if (drawn == 2 && pixels) {
        auto texture = s->composite(w, h, gpu::Format::RGBA8);
        s->menu_page = texture;
        s->menu_generation = texture ? generation : 0;
        if (texture)
            s->device->upload(texture->texture, {0, 0, w, h}, pixels, w * 4);
    }
    if (s->menu_page) {
        s->writing->settings_page = s->menu_page;
        s->writing->input.settings_page = s->menu_page->texture;
        s->writing->input.settings_page_w = s->menu_page->w;
        s->writing->input.settings_page_h = s->menu_page->h;
    }
    return true;
}

void attach_settings_page(const std::shared_ptr<Service> &s) {
    if (attach_menu_page(s))
        return;
    std::vector<uint8_t> page;
    if (!host_page_rgba(&page))
        return;
    std::lock_guard lock(s->mutex);
    if (!s->writing)
        return;
    ++s->settings_pages;
    if (s->fake)
        return;
    auto texture = s->composite(640, 480, gpu::Format::RGBA8);
    if (texture) {
        s->device->upload(texture->texture, {0, 0, 640, 480}, page.data(), 640 * 4);
        s->writing->settings_page = texture;
        s->writing->input.settings_page = texture->texture;
        s->writing->input.settings_page_w = 640;
        s->writing->input.settings_page_h = 480;
    }
}

// NFSMW 3.0.4 (run 39, D-202 item 4): while the settings menu has the guest parked (runtime host_guest_pause_point)
// nothing is sealed, so the page above never ran and the menu froze on screen although it took keys. The parked
// main guest thread - the thread that seals, so host_page_menu keeps one caller - asks here each slice; when the page
// changed, its new texture replaces the cached one and the next repeat composes it over the held frame. No new frame
// id, nothing of the game's runs. Unchanged or hidden: no texture, no recompose.
extern "C" void host_present_menu_refresh() {
    auto s = active.load();
    if (!s)
        return;
    int w = 0, h = 0;
    uint64_t generation = 0;
    const uint8_t *pixels = nullptr;
    const int drawn = draw_menu_page(s, &w, &h, &generation, &pixels);
    std::lock_guard lock(s->mutex);
    if (drawn != 2 || !pixels)
        return;
    ++s->settings_pages;
    ++s->menu_refreshes;
    if (!s->fake) {
        auto texture = s->composite(w, h, gpu::Format::RGBA8);
        if (!texture)
            return;
        s->device->upload(texture->texture, {0, 0, w, h}, pixels, w * 4);
        s->menu_page = texture;
        s->menu_generation = generation;
        s->cached_settings = texture;
        s->cached_input.settings_page = texture->texture;
        s->cached_input.settings_page_w = w;
        s->cached_input.settings_page_h = h;
    } else {
        s->menu_generation = generation;
        s->fake_menu_w = w, s->fake_menu_h = h;
        // No texture in a fake: a handle made from the page's generation stands for it (tests).
        s->cached_input.settings_page.id = 0xfa4e000000000000ull | (generation & 0xffffffffffffull);
        s->cached_input.settings_page_w = w;
        s->cached_input.settings_page_h = h;
    }
    s->recompose_pending = true;
    s->tick_pending = true;
    s->wake.notify_one();
}

extern "C" void host_frame_seal() {
    auto s = active.load();
    if (!s)
        return;
    auto f = host_frame_current();
    if (!f.id)
        return;
    if (s->offscreen && s->capture_factory) {
        bool eligible = false;
        {
            std::lock_guard lock(s->mutex);
            eligible = s->writing && !s->writing->dropped && s->writing->target &&
                       s->writing->target->scene.w > 0;
        }
        // Pool exhaustion is not a completed present. Leave the request armed
        // for the next eligible frame; the runner still rejects a late turn.
        if (eligible) {
            auto capture = s->capture_factory(host_frame_class(f));
            std::lock_guard lock(s->mutex);
            s->writing->capture = std::move(capture);
        }
    }
    host_d3d_seal_commands();
#ifdef POPM_PRESENT_HAS_UI_LAYER
    // Extraction reads shim leases on the guest thread before publication.
    // Only immutable value types pass to the worker.
    if (s->previous_ui_epoch != s->epoch ||
        (s->class_known && s->last_class != host_frame_class(f)))
        s->previous_ui = {};
    UiFrame ui{};
    ui_layer_extract(f, s->guest_w, s->guest_h, &s->previous_ui, &ui);
    s->previous_ui = ui;
    {
        std::lock_guard lock(s->mutex);
        if (s->writing && s->writing->target) {
            s->writing->ui = std::move(ui);
            if (host_frame_class(f) != HOST_SCREEN_GAMEPLAY) {
                auto &writer = *s->writing;
                // Blit/Flip-only front-end presents already supplied a complete
                // image. Sparse UI records need not cover unchanged menu pixels.
                writer.supplied = true;
                writer.input.legacy = writer.staged_pixels;
                writer.input.legacy_frame =
                    writer.staged_pixels ? writer.target->pixels : gpu::Texture{};
                writer.input.guest_w = writer.staged_pixels ? s->guest_w : writer.ui.guest_w;
                writer.input.guest_h = writer.staged_pixels ? s->guest_h : writer.ui.guest_h;
            }
        }
    }
#endif
    {
        std::lock_guard lock(s->mutex);
        if (s->writing && s->writing->target && !s->writing->supplied) {
            auto &in = s->writing->input;
            in.guest_w = s->guest_w;
            in.guest_h = s->guest_h;
            const HostGameRect rect = s->game_rect();
            in.drawable_w = rect.w;
            in.drawable_h = rect.h;
            if (host_frame_class(f) == HOST_SCREEN_GAMEPLAY && !host_frame_legacy(f)) {
                s->writing->supplied = true;
                in.legacy = false; // compositor supplies the last scene on a no-draw frame
            }
        }
    }
    attach_settings_page(s);
    {
        std::lock_guard lock(s->mutex);
        if (s->writing)
            s->writing->screen_class_valid = host_frame_class_valid(f) != 0;
    }
    s->seal(f.id, host_frame_class(f), host_frame_had_draws(f) != 0);
#ifdef POPM_PRESENT_HAS_UI_LAYER
    s->previous_ui_epoch = s->epoch;
#endif
}
extern "C" void host_present_tick_for_test(double ts) {
    auto s = active.load();
    if (!s || !s->fake)
        return;
    s->signal_tick(ts);
    {
        std::lock_guard lock(s->mutex);
        s->tick_pending = false;
    }
    s->tick(ts);
}
#define PRESENT_COUNTER(name, field)                                                               \
    extern "C" uint64_t name() {                                                                   \
        auto s = active.load();                                                                    \
        if (!s)                                                                                    \
            return 0;                                                                              \
        std::lock_guard lock(s->mutex);                                                            \
        return s->field;                                                                           \
    }
PRESENT_COUNTER(host_present_unique_completed, unique)
PRESENT_COUNTER(host_present_repeats, repeats)
PRESENT_COUNTER(host_present_drops, drops)
PRESENT_COUNTER(host_present_waits, waits)
PRESENT_COUNTER(host_present_settings_pages, settings_pages)
PRESENT_COUNTER(host_present_menu_refreshes, menu_refreshes)
PRESENT_COUNTER(host_present_faults, faults)
PRESENT_COUNTER(host_present_scene_reused, history.scene_reused)
PRESENT_COUNTER(host_present_transition_epoch, epoch)
#undef PRESENT_COUNTER
extern "C" void mods_present_level_end() {
    auto s = active.load();
    if (!s)
        return;
    std::lock_guard lock(s->mutex);
    ++s->epoch;
    s->metric = {};
    s->class_known = false;
    mods_display_transition(s->epoch, s->last_class);
}
extern "C" int host_metric_continuous(double *minimum, double *elapsed) {
    auto s = active.load();
    if (!s || s->offscreen) {
        if (minimum)
            *minimum = 0;
        if (elapsed)
            *elapsed = 0;
        return 0;
    }
    std::lock_guard lock(s->mutex);
    return s->metric.read(minimum, elapsed);
}
extern "C" int host_metric_throughput(double *minimum, double *elapsed) {
    auto s = active.load();
    if (!s || !s->offscreen) {
        if (minimum)
            *minimum = 0;
        if (elapsed)
            *elapsed = 0;
        return 0;
    }
    std::lock_guard lock(s->mutex);
    return s->metric.read(minimum, elapsed);
}
bool host_present_copy_layout(LayoutSnapshot *out) {
    auto s = active.load();
    if (!s || !out)
        return false;
    std::lock_guard lock(s->mutex);
    if (!s->layout_valid)
        return false;
    *out = s->layouts[s->layout_slot];
    return true;
}
void host_present_test_begin(bool automatic, bool offscreen, unsigned display_frames) {
    auto s = begin(true, automatic, offscreen);
    s->flight_limit = !offscreen && display_frames >= 2 && display_frames <= 3 ? display_frames : 1;
}
unsigned host_present_test_flight_count() {
    auto s = active.load();
    std::lock_guard lock(s->mutex);
    return unsigned(s->flights.size());
}
void host_present_test_seal(uint64_t id, HostScreenClass cls, bool had_draws, bool prefix_pending) {
    auto s = active.load();
    if (!s)
        return;
    s->acquire(s->guest_w, s->guest_h, 0, 0);
    s->seal(id, cls, had_draws, prefix_pending);
}
void host_present_test_command_done(uint64_t id) {
    test_ack(id, 0, 0);
}
void host_present_test_presented(uint64_t id, double ts) {
    test_ack(id, 1, ts);
}
void host_present_test_prefix_done(uint64_t id) {
    test_ack(id, 2, 0);
}
bool host_present_test_released(uint64_t id) {
    auto s = active.load();
    if (!s)
        return false;
    std::lock_guard lock(s->mutex);
    return s->test_released.count(id) != 0;
}
uint64_t host_present_test_page_id() {
    auto s = active.load();
    std::lock_guard lock(s->mutex);
    return s->cached_input.settings_page.id;
}
uint64_t host_present_test_recomposed() {
    auto s = active.load();
    std::lock_guard lock(s->mutex);
    return s->recomposed_repeats;
}
uint64_t host_present_test_last_id() {
    auto s = active.load();
    std::lock_guard lock(s->mutex);
    return s->last_id;
}
bool host_present_test_class_valid() {
    auto s = active.load();
    std::lock_guard lock(s->mutex);
    return s->cached_screen_class_valid;
}
uint8_t host_present_test_last_pixel() {
    auto s = active.load();
    std::lock_guard lock(s->mutex);
    return s->last_pixel;
}
void host_present_test_fail_allocations(unsigned n) {
    auto s = active.load();
    std::lock_guard lock(s->mutex);
    s->fail_allocations = n;
}
int host_present_test_requested_width() {
    auto s = active.load();
    std::lock_guard lock(s->mutex);
    return s->requested_w;
}

bool host_present_test_read_rgba(uint8_t *out, size_t bytes) {
    auto s = active.load();
    if (!s || !out || !s->device)
        return false;
    std::shared_ptr<Composite> texture;
    {
        std::lock_guard lock(s->mutex);
        texture = s->cached;
    }
    if (!texture || texture->format != gpu::Format::RGBA8 ||
        bytes < size_t(texture->w) * texture->h * 4)
        return false;
    // cached is published only after completion, so no GPU wait is needed.
    return s->device->readback(texture->texture, {0, 0, texture->w, texture->h}, out,
                               texture->w * 4);
}

bool host_present_copy_composite(HostCompletedComposite *out) {
    auto s = active.load();
    if (!s || !out || !s->device)
        return false;
    HostCompletedComposite result;
    std::shared_ptr<Composite> texture;
    {
        std::lock_guard lock(s->mutex);
        texture = s->cached;
        // R3 run 17: BGRA8 composites too (the real app's), returned as RGBA like the RGBA8 ones.
        if (!texture || (texture->format != gpu::Format::RGBA8 && texture->format != gpu::Format::BGRA8))
            return false;
        result.w = texture->w;
        result.h = texture->h;
        result.guest_w = s->cached_input.guest_w;
        result.guest_h = s->cached_input.guest_h;
        result.cls = s->cached_input.cls;
        result.frame_id = s->last_id;
        result.layout = s->layouts[s->layout_slot];
    }
    result.rgba.resize(size_t(result.w) * result.h * 4);
    if (!s->device->readback(texture->texture, {0, 0, result.w, result.h}, result.rgba.data(),
                             result.w * 4))
        return false;
    if (texture->format == gpu::Format::BGRA8)
        for (size_t i = 0; i < result.rgba.size(); i += 4)
            std::swap(result.rgba[i], result.rgba[i + 2]);
    *out = std::move(result);
    return true;
}

void host_present_set_capture_factory(HostCaptureFactory factory) {
    auto s = active.load();
    if (!s)
        return;
    std::lock_guard lock(s->mutex);
    s->capture_factory = factory;
}

uint8_t host_present_test_last_ui_red() {
    auto s = active.load();
    if (!s)
        return 0;
    std::lock_guard lock(s->mutex);
    return s->cached_ui.elements.empty() || s->cached_ui.elements[0].rgba.empty()
               ? 0
               : s->cached_ui.elements[0].rgba[0];
}

// Deterministic fake link uses the production worker predicate and tick state
// machine. No worker thread, layer, drawable or Metal device is created.
bool host_present_test_window_wake(double ts, bool display_tick, bool timeout) {
    auto s = active.load();
    if (!s || !s->fake || s->offscreen)
        return false;
    if (display_tick)
        s->signal_tick(ts);
    {
        std::lock_guard lock(s->mutex);
        if (!timeout && !s->window_ready())
            return false;
        s->tick_pending = false;
    }
    s->tick(ts);
    return true;
}
uint64_t host_present_test_in_flight() {
    auto s = active.load();
    std::lock_guard lock(s->mutex);
    return s->flights.empty() ? 0 : s->flights.front()->frame_id;
}
unsigned host_present_test_faults() {
    auto s = active.load();
    std::lock_guard lock(s->mutex);
    return s->faults;
}
bool host_present_test_input_legacy() {
    auto s = active.load();
    std::lock_guard lock(s->mutex);
    return !s->flights.empty() && s->flights.front()->input.legacy;
}
void host_present_test_commit_swapchain(gpu::Swapchain chain) {
    auto s = active.load();
    if (!s || !s->fake || !s->device)
        return;
    std::shared_ptr<Frame> f;
    {
        std::lock_guard lock(s->mutex);
        if (!s->flights.empty())
            f = s->flights.back();
    }
    if (!f)
        return;
    s->chain = chain;
    gpu::Texture drawable = s->device->acquire(chain);
    gpu::CommandBuffer cb = s->device->begin();
    s->commit_present(f, drawable, cb);
}
extern "C" int32_t host_display_anchor(uint64_t id, int8_t h, int8_t v, int clear) {
    if (clear)
        compositor_clear_anchor(id);
    else
        compositor_set_anchor(id, {h, v});
    return 0;
}
extern "C" uint32_t host_display_elements(uint64_t *ids, uint32_t max) {
    LayoutSnapshot layout;
    if (!host_present_copy_layout(&layout))
        return 0;
    if (ids)
        for (size_t i = 0; i < std::min(size_t(max), layout.elements.size()); ++i)
            ids[i] = layout.elements[i].id;
    return uint32_t(layout.elements.size());
}
extern "C" float host_display_aspect() {
    auto s = active.load();
    if (!s)
        return 4.0f / 3.0f;
    std::lock_guard lock(s->mutex);
    const HostGameRect rect = s->game_rect();
    return rect.w > 0 && rect.h > 0 ? float(rect.w) / rect.h : 4.0f / 3.0f;
}
extern "C" uint64_t host_display_epoch() {
    return host_present_transition_epoch();
}

void host_present_suspend(bool suspended) {
    g_present_suspended.store(suspended);
    if (!suspended)
        return;
    // Going to the background: the GPU completes nothing there, so any command
    // buffer still in flight when the process is frozen stays in flight, and
    // on resume the worker would wait on completions that never come. Give the
    // worker up to a second to see the flag and let the in-flight work finish.
    auto s = active.load();
    if (!s)
        return;
    std::unique_lock lock(s->mutex);
    s->wake.notify_all();
    s->completed.wait_for(lock, std::chrono::seconds(1), [&] {
        s->sweep();
        return s->flights.empty() && s->pending_commands == 0;
    });
}

extern "C" HostGameRect host_present_current_game_rect() {
    auto s = active.load();
    if (!s)
        return {0, 0, 0, 0};
    return Service::unpack_rect(s->published_rect.load(std::memory_order_relaxed));
}

void host_present_set_controls(const controls::ControlsView &view) {
    std::lock_guard lock(g_present_controls_mutex);
    g_present_controls = view;
}
controls::ControlsView host_present_controls(void) {
    std::lock_guard lock(g_present_controls_mutex);
    return g_present_controls;
}
bool host_present_suspended(void) {
    return g_present_suspended.load();
}

// Present with a sync interval returns at a vertical blank on Windows, and a
// renderer that asks for one paces its whole loop by it. This is how long
// until the presenter's next refresh boundary, `intervals` refreshes on, on the
// device clock the pacer keeps. The caller waits it out in the scheduler: a
// host sleep here would hold the guest baton for the whole wait and freeze
// every other guest thread with it. A headless presenter has no display to
// wait for, so a smoke still runs as fast as it can.
extern "C" double host_present_refresh_delay(int intervals) {
    auto s = active.load();
    if (!s || s->fake || !s->device || intervals <= 0)
        return 0.0;
    double period, now;
    {
        std::lock_guard lock(s->mutex);
        period = s->frame_period > 0 ? s->frame_period : 1.0 / 60;
        now = s->device->now_seconds();
    }
    const double next = (std::floor(now / period) + intervals) * period;
    return next > now ? next - now : 0.0;
}

// 3.0.2: the presenter's clock (device seconds) and paced period, for a frame limit that follows the refresh slots
// (SlotPacer). 0 on a headless presenter or one without a device: the caller keeps its own clock then.
extern "C" int host_present_slot_clock(double *now, double *period) {
    auto s = active.load();
    if (!s || s->fake || !s->device)
        return 0;
    std::lock_guard lock(s->mutex);
    *period = s->frame_period > 0 ? s->frame_period : 1.0 / 60;
    *now = s->device->now_seconds();
    return 1;
}

extern "C" void host_present_seal_window() {
    auto s = active.load();
    if (!s)
        return;
    // A separate sequence avoids collisions with DirectDraw's frame leases.
    static uint64_t next = uint64_t(1) << 63;
    attach_settings_page(s);
    s->seal(next++, HOST_SCREEN_MENU, false);
}
