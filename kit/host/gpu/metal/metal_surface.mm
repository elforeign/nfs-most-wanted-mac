// metal_surface.mm - the CAMetalLayer side of the swapchain: refresh rate and
// the layer a test uses when it has no window.
#include "metal_device.h"

#import <TargetConditionals.h>
#if TARGET_OS_IPHONE
#import <UIKit/UIKit.h>
#else
#import <AppKit/AppKit.h>
#import <QuartzCore/CATransaction.h>
#import <CoreGraphics/CoreGraphics.h>
#endif
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <ctime>
#include <mutex>
#include <string>
#include <thread>
#include <dlfcn.h>
#include <SDL3/SDL.h>
#include <SDL3/SDL_metal.h>

namespace gpu {

#if !TARGET_OS_IPHONE
// Test95 (RC lifecycle check): the system-level events macOS keeps apart - system sleep/wake, display sleep/wake and
// the user session going inactive/active (screen lock, fast user switching) - logged with the monotonic clock, so a
// session log shows which one happened and when. Window focus is logged by the SDL host. Logging only.
namespace {
std::once_flag g_lifecycle_once;
void lifecycle_observe() {
    std::call_once(g_lifecycle_once, [] {
        dispatch_async(dispatch_get_main_queue(), ^{
          NSNotificationCenter *nc = [[NSWorkspace sharedWorkspace] notificationCenter];
          NSDictionary<NSNotificationName, NSString *> *names = @{
              NSWorkspaceWillSleepNotification : @"system will sleep",
              NSWorkspaceDidWakeNotification : @"system woke",
              NSWorkspaceScreensDidSleepNotification : @"displays slept",
              NSWorkspaceScreensDidWakeNotification : @"displays woke",
              NSWorkspaceSessionDidResignActiveNotification : @"session inactive (locked or switched)",
              NSWorkspaceSessionDidBecomeActiveNotification : @"session active",
          };
          for (NSNotificationName name in names) {
              NSString *what = names[name];
              [nc addObserverForName:name
                              object:nil
                               queue:nil
                          usingBlock:^(NSNotification *) {
                            const double t = std::chrono::duration<double>(
                                                 std::chrono::steady_clock::now().time_since_epoch())
                                                 .count();
                            NSScreen *screen = [NSScreen mainScreen];
                            const CGSize px = screen ? [screen convertRectToBacking:screen.frame].size : CGSizeZero;
                            fprintf(stderr, "[host] lifecycle: %s (monotonic %.3f s, main display %.0fx%.0f)\n",
                                    what.UTF8String, t, px.width, px.height);
                          }];
          }
          fprintf(stderr, "[host] lifecycle: watching system sleep/wake, display sleep/wake and session changes\n");
        });
    });
}
} // namespace
#endif

// NFSU1 run 8: the refresh of the screen the game window is on (host/sdl/main.cpp, AppKit's maximumFramesPerSecond:
// 120 on a ProMotion panel or a TV set to 120 Hz). 0 when no host reports it (tests, other hosts).
extern "C" __attribute__((weak)) double host_window_refresh_hz(void) {
    return 0;
}

double MetalDevice::refresh_period(Swapchain s) {
#if !TARGET_OS_IPHONE
    lifecycle_observe();
#endif
    {
        std::lock_guard lock(mutex_);
        if (swapchains_.find(s.id) == swapchains_.end())
            return 1.0 / 60;
    }
    double hz = 0;
#if TARGET_OS_IPHONE
    hz = [UIScreen mainScreen].maximumFramesPerSecond;
#else
    // The layer does not know its display; the main display's rate is what the
    // AppKit host's CVDisplayLink reported for a single-display machine. Apple
    // laptops report 0 for an adaptive panel, which reads as 60.
    hz = host_window_refresh_hz();
    if (hz > 1.0)
        return 1.0 / hz;
    if (CGDisplayModeRef mode = CGDisplayCopyDisplayMode(CGMainDisplayID())) {
        hz = CGDisplayModeGetRefreshRate(mode);
        CGDisplayModeRelease(mode);
    }
#endif
    return hz > 1.0 ? 1.0 / hz : 1.0 / 60;
}

// Test86: HDR output. The layer and the screen belong to AppKit's main thread, so the presenter asks and the
// main queue does the work; the drawables acquired afterwards show the format in effect (swapchain_format
// reads the layer). The headroom is refreshed the same way and read from atomics.
namespace {
std::atomic<double> g_edr_current{1.0}, g_edr_potential{1.0};
std::atomic<bool> g_edr_queued{false};
} // namespace

bool MetalDevice::set_swapchain_hdr(Swapchain s, bool on) {
    CAMetalLayer *layer = nil;
    {
        std::lock_guard lock(mutex_);
        auto it = swapchains_.find(s.id);
        if (it == swapchains_.end())
            return false;
        layer = it->second.layer;
    }
#if TARGET_OS_IPHONE
    (void)layer;
    return false;
#else
    dispatch_async(dispatch_get_main_queue(), ^{
      [CATransaction begin];
      [CATransaction setDisableActions:YES];
      if (on) {
          CGColorSpaceRef cs = CGColorSpaceCreateWithName(kCGColorSpaceExtendedLinearSRGB);
          layer.wantsExtendedDynamicRangeContent = YES;
          layer.colorspace = cs;
          layer.pixelFormat = MTLPixelFormatRGBA16Float;
          CGColorSpaceRelease(cs);
      } else {
          layer.wantsExtendedDynamicRangeContent = NO;
          layer.colorspace = nil;
          layer.pixelFormat = MTLPixelFormatBGRA8Unorm;
      }
      [CATransaction commit];
      fprintf(stderr, "[gpu] Metal presentation: HDR output %s (EDR headroom now %.2f, potential %.2f)\n",
              on ? "ON: RGBA16F extended linear sRGB" : "OFF: BGRA8", g_edr_current.load(), g_edr_potential.load());
    });
    return true;
#endif
}

double MetalDevice::edr_headroom(bool potential) {
#if !TARGET_OS_IPHONE
    if (!g_edr_queued.exchange(true))
        dispatch_async(dispatch_get_main_queue(), ^{
          NSScreen *screen = [NSScreen mainScreen];
          if (screen) {
              g_edr_current.store(std::max(1.0, double(screen.maximumExtendedDynamicRangeColorComponentValue)));
              g_edr_potential.store(
                  std::max(1.0, double(screen.maximumPotentialExtendedDynamicRangeColorComponentValue)));
          }
          g_edr_queued.store(false);
        });
#endif
    return potential ? g_edr_potential.load() : g_edr_current.load();
}

#if !TARGET_OS_IPHONE
// Test90: the display's own HDR mode. A TV puts its picture into HDR when the Mac sends it an HDR signal, and the
// Mac sends one only while that display's High Dynamic Range setting (System Settings > Displays) is on. With
// hdr_output on, the game turns it on for the main display, the one the game is on; it turns it back off when the
// game quits or hdr_output goes off, and only when it was the game that turned it on. The WindowServer calls can
// take seconds (10 s when the display is asleep), so a worker thread makes them and the presenter reads atomics.
// A marker file in the profile folder records a switch, so a crash does not leave the TV in HDR for good: the next
// launch treats that switch as its own and puts it back (at once with hdr_output off, at quit otherwise).
namespace {
typedef bool (*HdrQuery)(CGDirectDisplayID);
typedef int (*HdrSet)(CGDirectDisplayID, bool, int, int);
HdrQuery g_sls_supports = nullptr, g_sls_enabled = nullptr;
HdrSet g_sls_set = nullptr;
std::once_flag g_sls_once, g_hdr_atexit_once;
std::atomic<int> g_hdr_mode{-2};        // -2 not looked at yet, else what display_hdr_mode returns
std::atomic<bool> g_hdr_busy{false};
std::atomic<uint32_t> g_hdr_ours{0};    // the display this process switched to HDR; 0 none
std::atomic<unsigned> g_hdr_switches{0};
std::atomic<int> g_hdr_failures{0};
std::atomic<double> g_hdr_next_look{0};
std::atomic<bool> g_hdr_marker_checked{false};
// Test91: HDR at 60 Hz. At 4K 120 Hz the HDR signal (10-bit) is at the limit of some HDMI links:
// sparkles in a fixed column, gone at 60 Hz. The game runs at 60 fps, so with hdr_refresh_60 on the display
// drops to the same size at 60 Hz while in HDR. The change is made for this process only
// (kCGConfigureForAppOnly): macOS puts the display's own rate back when the game quits, even after a crash.
// Test96b (owner's abnormal-exit test): switching HDR with SkyLight saves the display's CURRENT mode as its permanent
// setting. Lowering the rate before HDR on (or turning HDR off while still lowered) therefore made 60 Hz the owner's
// saved rate, and "back to the display's own rate" became 60 Hz. Order now: HDR on, then the rate; on the way out the
// rate back first, then HDR off. Verified with a standalone probe (verification/test96-appearance/probe-p*.out).
std::atomic<bool> g_hdr_want_60{true};
std::atomic<bool> g_hdr_refresh_ours{false};
std::atomic<bool> g_hdr_refresh_tried{false};   // once per HDR period: never fights a rate the owner picks

bool sls_ok() {
    std::call_once(g_sls_once, [] {
        if (void *sky = dlopen("/System/Library/PrivateFrameworks/SkyLight.framework/SkyLight", RTLD_LAZY)) {
            g_sls_supports = (HdrQuery)dlsym(sky, "SLSDisplaySupportsHDRMode");
            g_sls_enabled = (HdrQuery)dlsym(sky, "SLSDisplayIsHDRModeEnabled");
            g_sls_set = (HdrSet)dlsym(sky, "SLSDisplaySetHDRModeEnabled");
        }
        if (!g_sls_supports || !g_sls_enabled || !g_sls_set)
            fprintf(stderr, "[gpu] display HDR mode: this macOS has no HDR-mode switch the game can use\n");
    });
    return g_sls_supports && g_sls_enabled && g_sls_set;
}

double hdr_clock() {
    return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count();
}

std::string hdr_marker_path() {
    const char *dir = getenv("RECOMP_PROFILE_DIR");
    return dir && *dir ? std::string(dir) + "/display-hdr-switched.txt" : std::string();
}
// Test96 (RC review): the marker names the display by identity (vendor, model, serial; display numbers can change
// when a TV reconnects) with the time of the switch. At the next launch the switch is put back only on that same
// display, only while it is still in HDR (if the user has already turned HDR off, nothing is overwritten), and a
// marker older than 7 days or naming an absent display is left alone (the latter until the display is back).
void hdr_marker_write(uint32_t display) {
    const std::string p = hdr_marker_path();
    if (FILE *f = p.empty() ? nullptr : fopen(p.c_str(), "w")) {
        fprintf(f, "%u %u %u %u %lld\n", display, CGDisplayVendorNumber(display), CGDisplayModelNumber(display),
                CGDisplaySerialNumber(display), (long long)time(nullptr));
        fclose(f);
    }
}
// The display the marker names, as currently numbered; 0 when it is absent, the marker is stale or unreadable.
uint32_t hdr_marker_read() {
    const std::string p = hdr_marker_path();
    unsigned d = 0, vendor = 0, model = 0, serial = 0;
    long long when = 0;
    int fields = 0;
    if (FILE *f = p.empty() ? nullptr : fopen(p.c_str(), "r")) {
        fields = fscanf(f, "%u %u %u %u %lld", &d, &vendor, &model, &serial, &when);
        fclose(f);
    }
    if (fields < 1)
        return 0;
    if (fields < 5)          // a Test90 marker: the display number only
        return d;
    if (time(nullptr) - when > 7 * 24 * 3600) {
        fprintf(stderr, "[gpu] display HDR mode: switch marker older than 7 days; left alone\n");
        return 0;
    }
    uint32_t n = 0;
    CGDirectDisplayID ids[16];
    if (CGGetOnlineDisplayList(16, ids, &n) != kCGErrorSuccess)
        return 0;
    for (uint32_t i = 0; i < n; ++i)
        if (CGDisplayVendorNumber(ids[i]) == vendor && CGDisplayModelNumber(ids[i]) == model &&
            CGDisplaySerialNumber(ids[i]) == serial)
            return ids[i];
    fprintf(stderr, "[gpu] display HDR mode: the display a previous run switched is not connected; marker kept\n");
    return 0;
}
void hdr_marker_clear() {
    const std::string p = hdr_marker_path();
    if (!p.empty())
        std::remove(p.c_str());
}

// Switches `display` and reports whether it is in the mode asked for afterwards.
bool hdr_switch(uint32_t display, bool on) {
    const double t0 = hdr_clock();
    const int err = g_sls_set(display, on, 0, 0);
    const bool now_on = g_sls_enabled(display);
    fprintf(stderr, "[gpu] display HDR mode: display %u asked for %s, now %s (result %d, %.2f s)\n", display,
            on ? "HDR" : "SDR", now_on ? "HDR" : "SDR", err, hdr_clock() - t0);
    if (now_on == on)
        g_hdr_switches.fetch_add(1);
    return now_on == on;
}

// Lowers `display` to 60 Hz at its current size; true when it did. False when it already runs at 60 Hz or less
// (or is adaptive), or has no 60 Hz mode at this size.
bool hdr_refresh_lower(uint32_t display) {
    CGDisplayModeRef cur = CGDisplayCopyDisplayMode(display);
    if (!cur)
        return false;
    const double hz = CGDisplayModeGetRefreshRate(cur);
    const size_t w = CGDisplayModeGetWidth(cur), h = CGDisplayModeGetHeight(cur);
    const size_t pw = CGDisplayModeGetPixelWidth(cur), ph = CGDisplayModeGetPixelHeight(cur);
    CGDisplayModeRelease(cur);
    if (!(hz > 61))
        return false;
    const void *keys[] = {kCGDisplayShowDuplicateLowResolutionModes};
    const void *values[] = {kCFBooleanTrue};
    CFDictionaryRef opts = CFDictionaryCreate(nullptr, keys, values, 1, &kCFTypeDictionaryKeyCallBacks,
                                              &kCFTypeDictionaryValueCallBacks);
    CFArrayRef modes = CGDisplayCopyAllDisplayModes(display, opts);
    CFRelease(opts);
    CGDisplayModeRef best = nullptr;
    double best_err = 1.1;   // 59.94 or 60.00, nothing else
    for (CFIndex i = 0, n = modes ? CFArrayGetCount(modes) : 0; i < n; ++i) {
        CGDisplayModeRef m = (CGDisplayModeRef)CFArrayGetValueAtIndex(modes, i);
        if (CGDisplayModeGetWidth(m) != w || CGDisplayModeGetHeight(m) != h || CGDisplayModeGetPixelWidth(m) != pw ||
            CGDisplayModeGetPixelHeight(m) != ph || !CGDisplayModeIsUsableForDesktopGUI(m))
            continue;
        const double err = std::fabs(CGDisplayModeGetRefreshRate(m) - 60.0);
        if (err < best_err) {
            best_err = err;
            best = m;
        }
    }
    bool done = false;
    if (best) {
        CGDisplayConfigRef cfg = nullptr;
        if (CGBeginDisplayConfiguration(&cfg) == kCGErrorSuccess) {
            CGConfigureDisplayWithDisplayMode(cfg, display, best, nullptr);
            const CGError e = CGCompleteDisplayConfiguration(cfg, kCGConfigureForAppOnly);
            done = e == kCGErrorSuccess;
            fprintf(stderr, "[gpu] display HDR mode: display %u %zux%zu at %.2f Hz -> %.2f Hz for HDR (result %d)\n",
                    display, pw, ph, hz, CGDisplayModeGetRefreshRate(best), int(e));
        }
    } else {
        fprintf(stderr, "[gpu] display HDR mode: display %u has no 60 Hz mode at %zux%zu; stays at %.2f Hz\n", display,
                pw, ph, hz);
    }
    if (modes)
        CFRelease(modes);
    return done;
}
void hdr_refresh_restore() {
    if (!g_hdr_refresh_ours.exchange(false))
        return;
    CGRestorePermanentDisplayConfiguration();
    fprintf(stderr, "[gpu] display HDR mode: display refresh back to its own setting\n");
}

void hdr_restore_at_exit() {
    const uint32_t d = g_hdr_ours.exchange(0);
    if (!d) {
        hdr_refresh_restore();
        return;
    }
    if (g_hdr_busy.load())   // a switch in flight: give it a moment rather than racing it
        for (int i = 0; i < 100 && g_hdr_busy.load(); ++i)
            std::this_thread::sleep_for(std::chrono::milliseconds(50));
    hdr_refresh_restore();   // the rate back before HDR off, which saves the current mode (Test96b)
    if (hdr_switch(d, false))
        hdr_marker_clear();
}

void hdr_take_ownership(uint32_t d) {
    g_hdr_ours.store(d);
    std::call_once(g_hdr_atexit_once, [] { std::atexit(hdr_restore_at_exit); });
}

// Worker for request 1: look, and switch the main display to HDR when it is not.
void hdr_turn_on() {
    const uint32_t d = CGMainDisplayID();
    int state = 0;
    double next = hdr_clock() + 10;    // look again in 10 s (the TV or the owner may change it)
    if (!g_sls_supports(d)) {
        state = -1;
        next = hdr_clock() + 60;
    } else if (g_sls_enabled(d)) {
        state = 1;
        if (g_hdr_want_60.load() && !g_hdr_refresh_ours.load() && !g_hdr_refresh_tried.exchange(true))
            g_hdr_refresh_ours.store(hdr_refresh_lower(d));
    } else if (CGDisplayIsAsleep(d)) {
        next = hdr_clock() + 5;        // nothing can switch a sleeping display; not a failure
    } else if (g_hdr_failures.load() < 3) {
        // HDR first, then the rate (Test96b): the HDR switch saves the current mode as the display's own setting.
        hdr_marker_write(d);
        if (hdr_switch(d, true)) {
            hdr_take_ownership(d);
            g_hdr_failures.store(0);
            state = 1;
            if (g_hdr_want_60.load() && !g_hdr_refresh_ours.load() && !g_hdr_refresh_tried.exchange(true))
                g_hdr_refresh_ours.store(hdr_refresh_lower(d));
        } else {
            if (g_hdr_ours.load() != d)
                hdr_marker_clear();
            if (g_hdr_failures.fetch_add(1) + 1 >= 3)
                fprintf(stderr, "[gpu] display HDR mode: display %u did not switch after 3 tries; turn High "
                                "Dynamic Range on in System Settings > Displays instead\n", d);
            next = hdr_clock() + 30;
        }
    }
    if (!g_hdr_want_60.load())
        hdr_refresh_restore();      // hdr_refresh_60 turned off: the display's own rate, still in HDR
    else if (state != 1 && !g_hdr_ours.load()) {
        hdr_refresh_restore();      // no HDR after all: nothing to lower the rate for; lower again next try
        g_hdr_refresh_tried.store(false);
    }
    g_hdr_mode.store(state);
    g_hdr_next_look.store(next);
    g_hdr_busy.store(false);
}

// Worker for request 0: put back a switch this process made.
void hdr_turn_back() {
    const uint32_t d = g_hdr_ours.load();
    if (d)
        hdr_refresh_restore();   // the rate back before HDR off, which saves the current mode (Test96b)
    if (d && hdr_switch(d, false)) {
        g_hdr_ours.store(0);
        hdr_marker_clear();
    } else if (d) {
        g_hdr_next_look.store(hdr_clock() + 30);
    }
    if (!g_hdr_ours.load()) {
        hdr_refresh_restore();
        g_hdr_refresh_tried.store(false);
    }
    g_hdr_mode.store(d && g_hdr_ours.load() ? 1 : 0);
    g_hdr_failures.store(0);
    g_hdr_busy.store(false);
}
} // namespace
#endif

int MetalDevice::display_hdr_mode(int request) {
#if TARGET_OS_IPHONE
    (void)request;
    return -1;
#else
    if (getenv("RECOMP_NO_DISPLAY_HDR_SWITCH") || !sls_ok())
        return -1;
    if (!g_hdr_marker_checked.exchange(true))
        if (const uint32_t d = hdr_marker_read()) {
            if (g_sls_enabled(d)) {
                fprintf(stderr, "[gpu] display HDR mode: a previous run switched display %u to HDR and did not "
                                "put it back; the game will\n", d);
                hdr_take_ownership(d);
            } else {
                fprintf(stderr, "[gpu] display HDR mode: display %u is already back in SDR; nothing to put back\n", d);
                hdr_marker_clear();
            }
        }
    if (g_hdr_busy.load())
        return 2;
    // Test91: bit 4 asks for HDR at 60 Hz (hdr_refresh_60); a change is acted on at once.
    const bool want_60 = (request & 4) != 0;
    request &= 3;
    if (request == 1 && g_hdr_want_60.exchange(want_60) != want_60) {
        g_hdr_refresh_tried.store(false);
        g_hdr_next_look.store(0);
    }
    const int state = g_hdr_mode.load();
    if (request == 1 && hdr_clock() >= g_hdr_next_look.load()) {
        g_hdr_busy.store(true);
        std::thread(hdr_turn_on).detach();
        return 2;
    }
    if (request == 0 && (g_hdr_ours.load() || g_hdr_refresh_ours.load()) && hdr_clock() >= g_hdr_next_look.load()) {
        g_hdr_busy.store(true);
        std::thread(hdr_turn_back).detach();
        return 2;
    }
    return state == -2 ? 0 : state;
#endif
}

unsigned MetalDevice::display_hdr_switches() {
#if TARGET_OS_IPHONE
    return 0;
#else
    return g_hdr_switches.load();
#endif
}

void *metal_native_surface_for_window(void *sdl_window) {
    SDL_MetalView view = SDL_Metal_CreateView(static_cast<SDL_Window *>(sdl_window));
    return view ? SDL_Metal_GetLayer(view) : nullptr;
}

void metal_release_window_surface(void *) {
    // The view is owned by the window; SDL_DestroyWindow releases it.
}

void *metal_test_native_surface(int w, int h) {
    CAMetalLayer *layer = [CAMetalLayer layer];
    layer.drawableSize = CGSizeMake(w, h);
    return (__bridge_retained void *)layer;
}

} // namespace gpu
