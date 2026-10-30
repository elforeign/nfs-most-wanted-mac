// rumble.m - controller rumble as the PS2 game had it: a heavy (left) and a light (right) motor.
//
// feedback.c runs the X360's rumble model on the game's IFeedback calls and gives the two motor levels. A DualSense plays them
// through its "compatible vibration" mode, which emulates the DualShock's two rumble motors, set with an HID output
// report: USB report 0x02 (48 bytes), Bluetooth report 0x31 (78 bytes, CRC-32 over 0xA2 + the report). Valid flags 0
// bit 0 enables the emulated motors and bit 1 selects them over the haptic actuators; byte 3 is the right (light)
// motor, byte 4 the left (heavy) one. (CoreHaptics through GameController.framework would need a signed app the
// haptics server registers; plain HID works for this process.)
#import <Foundation/Foundation.h>
#import <IOKit/hid/IOHIDManager.h>
#include <math.h>
#include <stdatomic.h>
#include <signal.h>
#include <unistd.h>
#include <zlib.h>

#include "host.h"


// Reports go out on their own serial queue: IOHIDDeviceSetReport waits for the controller (milliseconds over USB, more
// over Bluetooth or when it's slow to answer), so the game thread only posts its latest levels and never waits.
static IOHIDManagerRef manager;
static dispatch_queue_t hid_q;
static IOHIDDeviceRef pad;                                  // hid_q (the match/removal callbacks hop onto it)
static int bluetooth, seq;
static uint8_t sent_heavy = 0xFF, sent_light = 0xFF;        // hid_q: what the controller plays
static uint64_t sent_at;
static _Atomic uint64_t want;                               // connection epoch << 32 | heavy | light << 8
static _Atomic uint32_t motor_generation;
static _Atomic int queued, closed;                          // closed: the game is quitting, the motors stay off
static _Atomic int suppressed;                              // menu / pause / focus / app holds
static dispatch_source_t sig_term, sig_int;                 // kept alive: a released source leaves the signal ignored
static int neutral_pending, retry_queued;                   // hid_q: neutral must succeed before gameplay
static uint64_t clear_until, retry_generation;
static dispatch_semaphore_t close_done;
static void flush(void);
static void finish_clear(void) {
    if (close_done) { dispatch_semaphore_signal(close_done); close_done = nil; }
}
static void cleared(void) {
    neutral_pending = 0; clear_until = 0; retry_queued = 0; ++retry_generation;
    finish_clear();
}
static void invalidate_motors(void) {                       // hid_q; never mutate the game-thread model here
    feedback_invalidate_effects();
    want = 0; ++motor_generation;                          // publish the epoch only after invalidating the model
    ++retry_generation; retry_queued = 0; clear_until = 0;
    neutral_pending = 1;
    sent_heavy = sent_light = 0xFF; sent_at = 0;
}

static void found(void *ctx, IOReturn r, void *sender, IOHIDDeviceRef dev) {
    (void)ctx; (void)r; (void)sender;
    CFRetain(dev);
    dispatch_async(hid_q, ^{
        if (pad) { CFRelease(dev); return; }
        pad = dev;
        CFTypeRef t = IOHIDDeviceGetProperty(dev, CFSTR(kIOHIDTransportKey));
        bluetooth = t && CFGetTypeID(t) == CFStringGetTypeID() && CFStringFind(t, CFSTR("Bluetooth"), 0).location != kCFNotFound;
        invalidate_motors();
        flush();                                           // neutral before the next feedback publication
        if (diag_on) diag_event("rumble: DualSense over %s", bluetooth ? "Bluetooth" : "USB");
    });
}
static void removed(void *ctx, IOReturn r, void *sender, IOHIDDeviceRef dev) {
    (void)ctx; (void)r; (void)sender;
    dispatch_async(hid_q, ^{
        if (dev == pad) { invalidate_motors(); CFRelease(pad); pad = NULL; finish_clear(); }
    });
}
static void rumble_open(void) {
    hid_q = dispatch_queue_create("nfsmw.rumble", DISPATCH_QUEUE_SERIAL);
    manager = IOHIDManagerCreate(kCFAllocatorDefault, kIOHIDOptionsTypeNone);
    NSArray *match = @[@{@kIOHIDVendorIDKey: @0x054C, @kIOHIDProductIDKey: @0x0CE6},     // DualSense
                       @{@kIOHIDVendorIDKey: @0x054C, @kIOHIDProductIDKey: @0x0DF2}];    // DualSense Edge
    IOHIDManagerSetDeviceMatchingMultiple(manager, (__bridge CFArrayRef)match);
    IOHIDManagerRegisterDeviceMatchingCallback(manager, found, NULL);
    IOHIDManagerRegisterDeviceRemovalCallback(manager, removed, NULL);
    IOHIDManagerScheduleWithRunLoop(manager, CFRunLoopGetMain(), kCFRunLoopDefaultMode);
    IOHIDManagerOpen(manager, kIOHIDOptionsTypeNone);
}

static int send(uint8_t heavy, uint8_t light) {
    uint8_t r[78] = {0};
    uint8_t *c;                                             // the common part: flags, motors, ...
    CFIndex n;
    if (bluetooth) { r[0] = 0x31; r[1] = (uint8_t)(seq++ << 4 & 0xF0) | 0x02; c = r + 2; n = 78; }
    else { r[0] = 0x02; c = r + 1; n = 48; }
    c[0] = 0x03;                                            // valid flags 0: compatible vibration, rumble motors
    c[2] = light;                                           // right motor
    c[3] = heavy;                                           // left motor
    if (bluetooth) {
        uint8_t hdr = 0xA2;
        uLong crc = crc32(crc32(0, &hdr, 1), r, 74);
        r[74] = crc & 0xFF; r[75] = crc >> 8 & 0xFF; r[76] = crc >> 16 & 0xFF; r[77] = crc >> 24 & 0xFF;
    }
    IOReturn ok = IOHIDDeviceSetReport(pad, kIOHIDReportTypeOutput, r[0], r, n);
    static int logs;
    if (diag_on && (ok != kIOReturnSuccess || logs < 20) && logs++ < 40)
        diag_event("rumble: heavy %u light %u -> %s (0x%X)", heavy, light, ok == kIOReturnSuccess ? "sent" : "failed", ok);
    return ok == kIOReturnSuccess;
}

// A stop cannot depend on another pad read. Retry only neutral, every 20 ms for at most 250 ms.
// A new connection or a successful clear cancels delayed work from the previous attempt.
static void retry_clear(void) {
    uint64_t now = host_ticks();
    if (!clear_until) clear_until = now + 250000000ull;
    if (now >= clear_until) { finish_clear(); return; }
    if (retry_queued) return;
    retry_queued = 1;
    uint64_t token = retry_generation;
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 20 * NSEC_PER_MSEC), hid_q, ^{
        if (token != retry_generation) return;
        retry_queued = 0;
        if (host_ticks() >= clear_until) { finish_clear(); return; }
        flush();
    });
}

// hid_q: send the latest levels when they changed (and a refresh every second, in case the controller reconnected).
static void flush(void) {
    queued = 0;
    if (!pad) { finish_clear(); return; }
    uint64_t request = want;
    uint32_t w = closed || suppressed || neutral_pending || (request >> 32) != motor_generation ? 0 : (uint32_t)request;
    uint8_t heavy = w & 0xFF, light = w >> 8 & 0xFF;
    uint64_t now = host_ticks();
    if (heavy == sent_heavy && light == sent_light && now - sent_at < 1000000000ull) { if (!w) cleared(); return; }
    if (send(heavy, light)) {
        sent_heavy = heavy; sent_light = light; sent_at = now;
        if (!w) cleared();
    } else if (!w) { neutral_pending = 1; retry_clear(); }
}
static void post_levels_at(uint8_t heavy, uint8_t light, uint32_t token) {
    want = (uint64_t)token << 32 | heavy | (uint32_t)light << 8;
    if (!atomic_exchange(&queued, 1)) dispatch_async(hid_q, ^{ flush(); });
}

// The game stops reading the pad while it's paused (focus lost): the motors would keep its last levels.
void rumble_stop(void) {
    if (!hid_q) return;
    want = (uint64_t)motor_generation << 32;
    dispatch_async(hid_q, ^{
        neutral_pending = 1; clear_until = 0;
        ++retry_generation; retry_queued = 0;
        flush();
    });
}
void rumble_suppress(int on) {
    suppressed = on;
    rumble_stop();                         // clear want on BOTH edges: resume never replays a queued old level
}

// Quitting: the pad isn't read during the game's shutdown (a few seconds), or after the process ends, so the motors
// would keep the last levels. They stop when a quit starts (user32_request_close), at exit, and on SIGTERM / SIGINT;
// the last report is waited for (up to 300 ms) so it goes out before the process ends.
void rumble_close(void) {
    if (!hid_q || atomic_exchange(&closed, 1)) return;
    dispatch_semaphore_t done = dispatch_semaphore_create(0);
    dispatch_async(hid_q, ^{ close_done = done; clear_until = 0; flush(); });
    long late = dispatch_semaphore_wait(done, dispatch_time(DISPATCH_TIME_NOW, 300 * NSEC_PER_MSEC));
    if (diag_on) diag_event("rumble: close clear wait %s", late ? "expired (300 ms)" : "finished");
}
static dispatch_source_t close_on_signal(int sig) {
    signal(sig, SIG_IGN);                                   // the dispatch source takes it instead
    dispatch_source_t s = dispatch_source_create(DISPATCH_SOURCE_TYPE_SIGNAL, (uintptr_t)sig, 0, dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0));
    dispatch_source_set_event_handler(s, ^{ rumble_close(); padfx_close(); fflush(NULL); _exit(128 + sig); });   // logs kept
    dispatch_resume(s);
    return s;
}
void rumble_signals(void) {                                 // once, for whichever output reaches the controller first
    static int done;
    if (done++) return;
    sig_term = close_on_signal(SIGTERM);
    sig_int = close_on_signal(SIGINT);
}

// Each frame (from pad_read): the model's motor levels for the controller.
static int rumble_off = -1;                                  // NFS_RUMBLE=off: the controller isn't touched (unattended runs)
void rumble_set_enabled(int on) {                           // the F10 menu (portmenu.m): off stops the motors at once
    if (!on && rumble_off != 1) rumble_stop();
    rumble_off = !on;
    if (diag_on) diag_event("rumble: now %s", on ? "on" : "off");
}
void rumble_update(void) {
    int off = rumble_off;
    if (off < 0) { const char *e = getenv("NFS_RUMBLE"); rumble_off = off = e && !strcmp(e, "off"); }
    if (off) { float hf, lf; feedback_levels(&hf, &lf); return; }
    if (!manager) {
        rumble_open();
        atexit(rumble_close);
        rumble_signals();
    }
    uint32_t token = motor_generation;
    float hf, lf;
    feedback_levels(&hf, &lf);                              // the model advances with or without a DualSense
    if (!closed) post_levels_at((uint8_t)lroundf(hf * 255), (uint8_t)lroundf(lf * 255), token);
}
