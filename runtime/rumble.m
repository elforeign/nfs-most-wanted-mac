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
typedef struct {
    IOHIDDeviceRef pad, target; // hid_q, borrowed from candidates
    int switching, bluetooth, seq;
    uint8_t sent_heavy, sent_light;
    uint64_t sent_at;
    _Atomic uint64_t want; // connection epoch << 32 | heavy | light << 8
    _Atomic uint32_t motor_generation, selection_generation;
    uint32_t applied_selection;
    _Atomic int queued;
    int neutral_pending, retry_queued;
    uint64_t clear_until, retry_generation;
    dispatch_semaphore_t close_done;
    NSString *input_id;
    NSSet<NSNumber *> *input_services;
    int input_product;
    id last;
    uint64_t retry_at;
    int last_product;
} RumblePad;
static RumblePad rumble_pads[2] = {
    {.sent_heavy = 0xFF, .sent_light = 0xFF},
    {.sent_heavy = 0xFF, .sent_light = 0xFF}
};
static _Atomic int closed, suppressed; // lifecycle holds apply to both pads
static dispatch_source_t sig_term, sig_int;
static int send(RumblePad *r, uint8_t heavy, uint8_t light);
static void flush(RumblePad *r);
static void activate_target(RumblePad *r);
static void route_pad(RumblePad *r);
static void finish_clear(RumblePad *r) {
    if (r->close_done) { dispatch_semaphore_signal(r->close_done); r->close_done = nil; }
}
static void cleared(RumblePad *r) {
    r->neutral_pending = 0; r->clear_until = 0; r->retry_queued = 0; ++r->retry_generation;
    if (r->switching) activate_target(r);
    else finish_clear(r);
}
static void invalidate_motors(RumblePad *r) {                       // hid_q; never mutate the game-thread model here
    feedback_invalidate_slot((unsigned)(r - rumble_pads));
    r->want = 0; ++r->motor_generation;                          // publish the epoch only after invalidating the model
    ++r->retry_generation; r->retry_queued = 0; r->clear_until = 0;
    r->neutral_pending = 1;
    r->sent_heavy = r->sent_light = 0xFF; r->sent_at = 0;
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

static void activate_target(RumblePad *r) {
    r->switching = 0;
    if (r->pad) padlink_detach((void *)r->pad);
    r->pad = r->target;
    invalidate_motors(r);
    if (!r->pad) {
        route_pad(&rumble_pads[r == &rumble_pads[0] ? 1 : 0]);
        finish_clear(r); return;
    }
    CFTypeRef t = IOHIDDeviceGetProperty(r->pad, CFSTR(kIOHIDTransportKey));
    r->bluetooth = t && CFGetTypeID(t) == CFStringGetTypeID() &&
                CFStringFind(t, CFSTR("Bluetooth"), 0).location != kCFNotFound;
    r->seq = 0;
    padlink_hid((void *)r->pad, r->bluetooth);
    flush(r);                                           // neutral before the next feedback publication
                                                                                 
    route_pad(&rumble_pads[r == &rumble_pads[0] ? 1 : 0]);
    if (diag_on) diag_event("rumble: DualSense over %s", r->bluetooth ? "Bluetooth" : "USB");
}

static void route_pad(RumblePad *r) {
    IOHIDDeviceRef match = NULL;
    CFIndex model_matches = 0, n = candidates ? CFArrayGetCount(candidates) : 0;
    for (CFIndex i = 0; i < n; ++i) {
        IOHIDDeviceRef dev = (IOHIDDeviceRef)CFArrayGetValueAtIndex(candidates, i);
        if (r->input_services.count) {
            uint64_t registry_id;
            io_service_t service = IOHIDDeviceGetService(dev);
            if (service && IORegistryEntryGetRegistryEntryID(service, &registry_id) == KERN_SUCCESS &&
                [r->input_services containsObject:@(registry_id)]) { match = dev; break; }
        }
        if (r->input_id && [r->input_id isEqual:(__bridge id)IOHIDDeviceGetProperty(dev, CFSTR(kIOHIDPhysicalDeviceUniqueIDKey))]) {
            match = dev; break;
        }
        if (r->input_product && !r->input_id && !r->input_services.count) {
            id product = (__bridge id)IOHIDDeviceGetProperty(dev, CFSTR(kIOHIDProductIDKey));
            if ([product isEqual:@(r->input_product)]) { ++model_matches; match = dev; }
        }
    }
    if (model_matches > 1) match = NULL;
    RumblePad *other = &rumble_pads[r == &rumble_pads[0] ? 1 : 0];
    if (match && (match == other->target || match == other->pad)) match = NULL;
    if (match == r->target && (r->switching || r->pad == match)) return;
    r->target = match;
    r->switching = r->pad != r->target;
    if (!r->switching) return;
    if (r->pad) {
        invalidate_motors(r);
        flush(r);                                       // clear the old motors before handing off
    } else activate_target(r);
}

static int controller_product(id<RumbleController> controller) {
    NSString *category = controller.productCategory;
    if (![category hasPrefix:@"DualSense"]) return 0;
    return [category rangeOfString:@"Edge" options:NSCaseInsensitiveSearch].location != NSNotFound ? 0x0DF2 : 0x0CE6;
}

static void select_input(RumblePad *r, NSString *physical_id, NSSet<NSNumber *> *services, int product) {
    r->input_id = physical_id;
    r->input_services = services;
    r->input_product = product;
    for (unsigned slot = 0; slot < 2; ++slot) route_pad(&rumble_pads[slot]);
}

void rumble_select_slot(unsigned slot, void *controller) {
    if (slot >= 2) return;
    RumblePad *r = &rumble_pads[slot];
    id ctl = (__bridge id)controller;
    int same = ctl == r->last;
    if (!hid_q || (same && (!r->retry_at || host_ticks() < r->retry_at))) return;
    r->last = ctl;
    r->retry_at = 0;

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
            r->retry_at = host_ticks() + 1000000000ull;    // GameController and HID notifications can arrive in either order
            CFIndex peers = 0;
            for (id<RumbleController> other in [(Class<RumbleController>)NSClassFromString(@"GCController") controllers])
                if (controller_product(other) == product) ++peers;
            if (peers != 1) product = 0;
        }
    }
    if (same && !physical_id && !services.count && product == r->last_product) return;
    r->last_product = product;
    uint32_t selection = ++r->selection_generation;
    r->want = 0;                                           // a queued publication cannot reach the former input pad
    dispatch_async(hid_q, ^{
        r->applied_selection = selection;
        select_input(r, physical_id, services, product);
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
            for (unsigned slot = 0; slot < 2; ++slot) route_pad(&rumble_pads[slot]);
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
        for (unsigned slot = 0; slot < 2; ++slot) {
            RumblePad *r = &rumble_pads[slot];
            if (departing == r->pad) {
                invalidate_motors(r);
                (void)send(r, 0, 0); // best-effort neutral; a removed handle cannot be retried
                ++r->retry_generation; r->retry_queued = 0;
                padlink_detach((void *)r->pad);
                r->pad = NULL;
                r->switching = 0;
            }
            if (departing == r->target) { r->target = NULL; r->switching = 0; }
        }
        if (i != kCFNotFound) CFArrayRemoveValueAtIndex(candidates, i);
        for (unsigned slot = 0; slot < 2; ++slot) {
            route_pad(&rumble_pads[slot]);
            if (!rumble_pads[slot].pad) finish_clear(&rumble_pads[slot]);
        }
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

static int send(RumblePad *r, uint8_t heavy, uint8_t light) {
#if NFS_GAME == NFS_GAME_PRIVATE
    if (carbon_padfx_fake()) return 1;
    carbon_padfx_count(FX_WRITE);
#endif
    uint8_t report[78] = {0};
    uint8_t *c;                                             // the common part: flags, motors, ...
    CFIndex n;
    if (r->bluetooth) { report[0] = 0x31; report[1] = (uint8_t)(r->seq++ << 4 & 0xF0) | 0x02; c = report + 2; n = 78; }
    else { report[0] = 0x02; c = report + 1; n = 48; }
    c[0] = 0x03;                                            // valid flags 0: compatible vibration, rumble motors
    c[2] = light;                                           // right motor
    c[3] = heavy;                                           // left motor
    if (r->bluetooth) {
        uint8_t hdr = 0xA2;
        uLong crc = crc32(crc32(0, &hdr, 1), report, 74);
        report[74] = crc & 0xFF; report[75] = crc >> 8 & 0xFF; report[76] = crc >> 16 & 0xFF; report[77] = crc >> 24 & 0xFF;
    }
    uint64_t link = diag_on ? padlink_token((void *)r->pad) : 0;
    IOReturn ok = IOHIDDeviceSetReport(r->pad, kIOHIDReportTypeOutput, report[0], report, n);
    if (diag_on) padlink_output(link, PADLINK_RUMBLE, ok == kIOReturnSuccess);
    static int logs;
    if (diag_on && (ok != kIOReturnSuccess || logs < 20) && logs++ < 40)
        diag_event("rumble: heavy %u light %u -> %s (0x%X)", heavy, light, ok == kIOReturnSuccess ? "sent" : "failed", ok);
    return ok == kIOReturnSuccess;
}

// A stop cannot depend on another pad read. Retry only neutral, every 20 ms for at most 250 ms.
// A new connection or a successful clear cancels delayed work from the previous attempt.
static void retry_clear(RumblePad *r) {
    uint64_t now = host_ticks();
    if (!r->clear_until) r->clear_until = now + 250000000ull;
    if (now >= r->clear_until) { if (r->switching) activate_target(r); else finish_clear(r); return; }
    if (r->retry_queued) return;
    r->retry_queued = 1;
    uint64_t token = r->retry_generation;
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 20 * NSEC_PER_MSEC), hid_q, ^{
        if (token != r->retry_generation) return;
        r->retry_queued = 0;
        if (host_ticks() >= r->clear_until) { if (r->switching) activate_target(r); else finish_clear(r); return; }
        flush(r);
    });
}

// hid_q: send the latest levels when they changed (and a refresh every second, in case the controller reconnected).
static void flush(RumblePad *r) {
    r->queued = 0;
    if (!r->pad) { finish_clear(r); return; }
    uint64_t request = r->want;
    uint32_t w = closed || suppressed || r->neutral_pending || r->applied_selection != r->selection_generation ||
                 (request >> 32) != r->motor_generation ? 0 : (uint32_t)request;
    uint8_t heavy = w & 0xFF, light = w >> 8 & 0xFF;
    uint64_t now = host_ticks();
    if (heavy == r->sent_heavy && light == r->sent_light && now - r->sent_at < 1000000000ull) { if (!w) cleared(r); return; }
    if (send(r, heavy, light)) {
        r->sent_heavy = heavy; r->sent_light = light; r->sent_at = now;
        if (!w) cleared(r);
    } else if (!w) { r->neutral_pending = 1; retry_clear(r); }
}
static void post_levels_at(RumblePad *r, uint8_t heavy, uint8_t light, uint32_t token) {
    r->want = (uint64_t)token << 32 | heavy | (uint32_t)light << 8;
    if (!atomic_exchange(&r->queued, 1)) dispatch_async(hid_q, ^{ flush(r); });
}

// The game stops reading the pad while it's paused (focus lost): the motors would keep its last levels.
void rumble_stop_slot(unsigned slot) {
    if (slot >= 2) return;
    RumblePad *r = &rumble_pads[slot];
    if (!hid_q) return;
    r->want = (uint64_t)r->motor_generation << 32;
    dispatch_async(hid_q, ^{
        r->neutral_pending = 1; r->clear_until = 0;
        ++r->retry_generation; r->retry_queued = 0;
        flush(r);
    });
}
void rumble_select(void *controller) { rumble_select_slot(0, controller); }
void rumble_stop(void) { for (unsigned slot = 0; slot < 2; ++slot) rumble_stop_slot(slot); }
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
    dispatch_time_t deadline = dispatch_time(DISPATCH_TIME_NOW, 300 * NSEC_PER_MSEC);
    dispatch_async(hid_q, ^{
        for (unsigned slot = 0; slot < 2; ++slot) {
            RumblePad *r = &rumble_pads[slot];
            r->close_done = done; r->clear_until = 0; flush(r);
        }
    });
    long late = dispatch_semaphore_wait(done, deadline);
    late |= dispatch_semaphore_wait(done, deadline);
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
void rumble_update_slot(unsigned slot) {
    if (slot >= 2) return;
    RumblePad *r = &rumble_pads[slot];
#if NFS_GAME == NFS_GAME_PRIVATE
    if (carbon_padfx_fake()) {
        float heavy, light;
        feedback_levels_slot(slot, &heavy, &light);
        if (heavy > 0 || light > 0) carbon_padfx_count(FX_MOTOR);
        return;
    }
#endif
    int off = rumble_off;
    if (off < 0) rumble_off = off = setting_bool_parse(getenv("NFS_RUMBLE")) == 0;
    if (off) { float hf, lf; feedback_levels_slot(slot, &hf, &lf); return; }
    if (!manager) {
        rumble_open();
        atexit(rumble_close);
        rumble_signals();
    }
    uint32_t token = r->motor_generation;
    float hf, lf;
    feedback_levels_slot(slot, &hf, &lf);                              // the model advances with or without a DualSense
    if (!closed) post_levels_at(r, (uint8_t)lroundf(hf * 255), (uint8_t)lroundf(lf * 255), token);
}

void rumble_update(void) { rumble_update_slot(0); }
