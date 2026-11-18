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
#include "game_select.h"
#include "setting_bool.h"
#if NFS_GAME == NFS_GAME_PRIVATE
#include "carbon_padfx_output.h"
/* GameController also declares the socket send API. Keep the local HID helper. */
#define send carbon_rumble_send
#endif


// Reports go out on their own serial queue: IOHIDDeviceSetReport waits for the controller (milliseconds over USB, more
// over Bluetooth or when it's slow to answer), so the game thread only posts its latest levels and never waits.
static IOHIDManagerRef manager;
static dispatch_queue_t hid_q;
static CFMutableArrayRef candidates;                        // hid_q, retains every connected HID handle
static IOHIDDeviceRef pad, target;                           // hid_q, borrowed from candidates
static int switching;
static int bluetooth, seq;
static uint8_t sent_heavy = 0xFF, sent_light = 0xFF;        // hid_q: what the controller plays
static uint64_t sent_at;
static _Atomic uint64_t want;                               // connection epoch << 32 | heavy | light << 8
static _Atomic uint32_t motor_generation;
static _Atomic uint32_t selection_generation;
static uint32_t applied_selection;                         // hid_q
static _Atomic int queued, closed;                          // closed: the game is quitting, the motors stay off
static _Atomic int suppressed;                              // menu / pause / focus / app holds
static dispatch_source_t sig_term, sig_int;                 // kept alive: a released source leaves the signal ignored
static int neutral_pending, retry_queued;                   // hid_q: neutral must succeed before gameplay
static uint64_t clear_until, retry_generation;
static dispatch_semaphore_t close_done;
static void flush(void);
static void activate_target(void);
static void finish_clear(void) {
    if (close_done) { dispatch_semaphore_signal(close_done); close_done = nil; }
}
static void cleared(void) {
    neutral_pending = 0; clear_until = 0; retry_queued = 0; ++retry_generation;
    if (switching) activate_target();
    else finish_clear();
}
static void invalidate_motors(void) {                       // hid_q; never mutate the game-thread model here
    feedback_invalidate_effects();
    want = 0; ++motor_generation;                          // publish the epoch only after invalidating the model
    ++retry_generation; retry_queued = 0; clear_until = 0;
    neutral_pending = 1;
    sent_heavy = sent_light = 0xFF; sent_at = 0;
}

// GameController has no public HID handle. Where available, match its physical device ID
// or HID service IDs to IOKit. Model alone is usable only when both frameworks see one.
@protocol RumbleController
@property(readonly) NSString *productCategory;
@property(readonly) NSString *physicalDeviceUniqueID;
@property(readonly) NSArray *hidServices;
+ (NSArray *)controllers;
@end
@protocol RumbleHIDService
@property(readonly) NSNumber *registryID;
@end
static NSString *input_id;
static NSSet<NSNumber *> *input_services;
static int input_product;

static void activate_target(void) {
    switching = 0;
    if (pad) padlink_detach((void *)pad);
    pad = target;
    invalidate_motors();
    if (!pad) { finish_clear(); return; }
    CFTypeRef t = IOHIDDeviceGetProperty(pad, CFSTR(kIOHIDTransportKey));
    bluetooth = t && CFGetTypeID(t) == CFStringGetTypeID() &&
                CFStringFind(t, CFSTR("Bluetooth"), 0).location != kCFNotFound;
    seq = 0;
    padlink_hid((void *)pad, bluetooth);
    flush();                                           // neutral before the next feedback publication
    if (diag_on) diag_event("rumble: DualSense over %s", bluetooth ? "Bluetooth" : "USB");
}

static void route_pad(void) {
    IOHIDDeviceRef match = NULL;
    CFIndex model_matches = 0, n = candidates ? CFArrayGetCount(candidates) : 0;
    for (CFIndex i = 0; i < n; ++i) {
        IOHIDDeviceRef dev = (IOHIDDeviceRef)CFArrayGetValueAtIndex(candidates, i);
        if (input_services.count) {
            uint64_t registry_id;
            io_service_t service = IOHIDDeviceGetService(dev);
            if (service && IORegistryEntryGetRegistryEntryID(service, &registry_id) == KERN_SUCCESS &&
                [input_services containsObject:@(registry_id)]) { match = dev; break; }
        }
        if (input_id && [input_id isEqual:(__bridge id)IOHIDDeviceGetProperty(dev, CFSTR(kIOHIDPhysicalDeviceUniqueIDKey))]) {
            match = dev; break;
        }
        if (input_product && !input_id && !input_services.count) {
            id product = (__bridge id)IOHIDDeviceGetProperty(dev, CFSTR(kIOHIDProductIDKey));
            if ([product isEqual:@(input_product)]) { ++model_matches; match = dev; }
        }
    }
    if (model_matches > 1) match = NULL;
    if (match == target && (switching || pad == match)) return;
    target = match;
    switching = pad != target;
    if (!switching) return;
    if (pad) {
        invalidate_motors();
        flush();                                       // clear the old motors before handing off
    } else activate_target();
}

static int controller_product(id<RumbleController> controller) {
    NSString *category = controller.productCategory;
    if (![category hasPrefix:@"DualSense"]) return 0;
    return [category rangeOfString:@"Edge" options:NSCaseInsensitiveSearch].location != NSNotFound ? 0x0DF2 : 0x0CE6;
}

static void select_input(NSString *physical_id, NSSet<NSNumber *> *services, int product) {
    input_id = physical_id;
    input_services = services;
    input_product = product;
    route_pad();
}

void rumble_select(void *controller) {
    static id last;
    static uint64_t retry_at;
    static int last_product;
    id ctl = (__bridge id)controller;
    int same = ctl == last;
    if (!hid_q || (same && (!retry_at || host_ticks() < retry_at))) return;
    last = ctl;
    retry_at = 0;

    id<RumbleController> gc = ctl;
    int product = controller_product(gc);
    NSString *physical_id = nil;
    NSMutableSet<NSNumber *> *services = nil;
    if (product) {
        if ([ctl respondsToSelector:@selector(physicalDeviceUniqueID)]) {
            NSString *value = gc.physicalDeviceUniqueID;
            if ([value isKindOfClass:NSString.class] && value.length) physical_id = [value copy];
        }
        if ([ctl respondsToSelector:@selector(hidServices)]) {
            services = [NSMutableSet new];
            for (id service in gc.hidServices) {
                NSNumber *registry = [service isKindOfClass:NSNumber.class] ? service :
                    [service respondsToSelector:@selector(registryID)] ? [(id<RumbleHIDService>)service registryID] : nil;
                if ([registry isKindOfClass:NSNumber.class]) [services addObject:registry];
            }
        }
        if (!physical_id && !services.count) {
            retry_at = host_ticks() + 1000000000ull;    // GameController and HID notifications can arrive in either order
            CFIndex peers = 0;
            for (id<RumbleController> other in [(Class<RumbleController>)NSClassFromString(@"GCController") controllers])
                if (controller_product(other) == product) ++peers;
            if (peers != 1) product = 0;
        }
    }
    if (same && !physical_id && !services.count && product == last_product) return;
    last_product = product;
    uint32_t selection = ++selection_generation;
    want = 0;                                           // a queued publication cannot reach the former input pad
    dispatch_async(hid_q, ^{
        applied_selection = selection;
        select_input(physical_id, services, product);
    });
}

static void found(void *ctx, IOReturn r, void *sender, IOHIDDeviceRef dev) {
    (void)ctx; (void)r; (void)sender;
    CFRetain(dev);
    dispatch_async(hid_q, ^{
        if (IOHIDDeviceGetProperty(dev, CFSTR("GCSyntheticDevice")) != kCFBooleanTrue) {
            if (!candidates) candidates = CFArrayCreateMutable(NULL, 0, &kCFTypeArrayCallBacks);
            if (!CFArrayContainsValue(candidates, CFRangeMake(0, CFArrayGetCount(candidates)), dev))
                CFArrayAppendValue(candidates, dev);
            route_pad();
        }
        CFRelease(dev);
    });
}
static void removed(void *ctx, IOReturn r, void *sender, IOHIDDeviceRef dev) {
    (void)ctx; (void)r; (void)sender;
    CFRetain(dev);
    dispatch_async(hid_q, ^{
        CFIndex i = candidates ? CFArrayGetFirstIndexOfValue(candidates, CFRangeMake(0, CFArrayGetCount(candidates)), dev) : kCFNotFound;
        IOHIDDeviceRef departing = i == kCFNotFound ? dev : (IOHIDDeviceRef)CFArrayGetValueAtIndex(candidates, i);
        if (departing == pad) {
            invalidate_motors();
            padlink_detach((void *)pad);
            pad = NULL;
            switching = 0;
        }
        if (departing == target) { target = NULL; switching = 0; }
        if (i != kCFNotFound) CFArrayRemoveValueAtIndex(candidates, i);
        route_pad();
        if (!pad) finish_clear();
        CFRelease(dev);
    });
}
static void rumble_open(void) {
#if NFS_GAME == NFS_GAME_PRIVATE
    if (carbon_padfx_fake()) return;
    carbon_padfx_count(FX_OPEN);
#endif
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
#if NFS_GAME == NFS_GAME_PRIVATE
    if (carbon_padfx_fake()) return 1;
    carbon_padfx_count(FX_WRITE);
#endif
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
    uint64_t link = diag_on ? padlink_token((void *)pad) : 0;
    IOReturn ok = IOHIDDeviceSetReport(pad, kIOHIDReportTypeOutput, r[0], r, n);
    if (diag_on) padlink_output(link, PADLINK_RUMBLE, ok == kIOReturnSuccess);
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
    if (now >= clear_until) { if (switching) activate_target(); else finish_clear(); return; }
    if (retry_queued) return;
    retry_queued = 1;
    uint64_t token = retry_generation;
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 20 * NSEC_PER_MSEC), hid_q, ^{
        if (token != retry_generation) return;
        retry_queued = 0;
        if (host_ticks() >= clear_until) { if (switching) activate_target(); else finish_clear(); return; }
        flush();
    });
}

// hid_q: send the latest levels when they changed (and a refresh every second, in case the controller reconnected).
static void flush(void) {
    queued = 0;
    if (!pad) { finish_clear(); return; }
    uint64_t request = want;
    uint32_t w = closed || suppressed || neutral_pending || applied_selection != selection_generation ||
                 (request >> 32) != motor_generation ? 0 : (uint32_t)request;
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
static int rumble_off = -1;                                  // NFS_RUMBLE false: the controller isn't touched (unattended runs)
void rumble_set_enabled(int on) {                           // the F10 menu (portmenu.m): off stops the motors at once
    if (!on && rumble_off != 1) rumble_stop();
    rumble_off = !on;
    if (diag_on) diag_event("rumble: now %s", on ? "on" : "off");
}
void rumble_update(void) {
#if NFS_GAME == NFS_GAME_PRIVATE
    if (carbon_padfx_fake()) {
        float heavy, light;
        feedback_levels(&heavy, &light);
        if (heavy > 0 || light > 0) carbon_padfx_count(FX_MOTOR);
        return;
    }
#endif
    int off = rumble_off;
    if (off < 0) rumble_off = off = setting_bool_parse(getenv("NFS_RUMBLE")) == 0;
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
