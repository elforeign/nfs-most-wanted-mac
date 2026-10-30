// Offline HID queue regression: actual rumble.m, intercepted IOHIDDeviceSetReport, no device opened.
// cc -Wall -Wextra -Werror -g -fsanitize=address,undefined -fobjc-arc -I recomp runtime/rumble_test.m \
//   -framework Foundation -framework IOKit -lz -o /tmp/rumble_test && /tmp/rumble_test
// Lifecycle proof: PASS 43/43, 56 fake USB/Bluetooth reports, 0 real opens (ASan/UBSan).
// Original transport: FAIL 24/43. Fake detach retains motors; the first clear is rejected;
// only scheduled retries run afterward, with permanent failure capped at 250 ms / 15 reports.
// dsstops_test.py removes detach/attach/cache/retry/epoch guards: 2/10/16/14/2 failures of 43;
// extending the deadline to 2.5 s fails 4/45. Every broken control must exit 1.
#import <Foundation/Foundation.h>
static NSMutableArray *timers;
static void __attribute__((unused)) test_after(dispatch_time_t when, dispatch_queue_t q, dispatch_block_t b) {
    (void)when; (void)q;
    if (!timers) timers = [NSMutableArray new];
    [timers addObject:[b copy]];
}
#define dispatch_after test_after
#define IOHIDDeviceSetReport test_set_report
#define IOHIDDeviceGetProperty test_property
#define IOHIDManagerCreate test_manager_create
#ifndef RUMBLE_SOURCE
#define RUMBLE_SOURCE "rumble.m"
#endif
#include RUMBLE_SOURCE
#undef dispatch_after
#ifdef RUMBLE_LEGACY
// The original transport had no epoch argument; preserve its original publication path.
static uint32_t motor_generation;
static void post_levels_at(uint8_t heavy, uint8_t light, uint32_t token) {
    (void)token; post_levels(heavy, light);
}
#endif

int diag_on;
static int reports, nonzero, failures, checks;
static uint8_t motor_h, motor_l;
static float model_h = 0.8f, model_l = 0.4f;
static uint64_t clock_ns = 1000000000;
static int reject_zeros, rejected, detached, invalidations;
static int real_opens;
IOHIDManagerRef test_manager_create(CFAllocatorRef allocator, IOOptionBits options) {
    (void)allocator; (void)options; ++real_opens; abort(); // forbidden discovery trap, never a real HID manager
}
uint64_t host_ticks(void) { return clock_ns; }
void feedback_levels(float *h, float *l) { *h = model_h; *l = model_l; }
void feedback_invalidate_effects(void) { model_h = model_l = 0; ++invalidations; }
CFTypeRef test_property(IOHIDDeviceRef d, CFStringRef key) { (void)d; (void)key; return bluetooth ? CFSTR("Bluetooth") : CFSTR("USB"); }
void diag_event(const char *fmt, ...) { (void)fmt; }
void padfx_close(void) {}
IOReturn test_set_report(IOHIDDeviceRef device, IOHIDReportType type, CFIndex id, const uint8_t *bytes, CFIndex n) {
    (void)device; (void)type;
    // Both USB and Bluetooth encode light then heavy in the common payload.
    const uint8_t *c = bytes + (id == 0x31 ? 2 : 1);
    if (n != (id == 0x31 ? 78 : 48)) abort();
    ++reports;
    if (detached || ((!c[2] && !c[3]) && reject_zeros)) {
        if (reject_zeros > 0) --reject_zeros;
        ++rejected; return kIOReturnError;
    }
    motor_l = c[2]; motor_h = c[3];
    nonzero += motor_h != 0 || motor_l != 0;
    return kIOReturnSuccess;
}
#define CHECK(x) do { ++checks; if (!(x)) { ++failures; fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); } } while (0)
static void drain(void) { dispatch_sync(hid_q, ^{}); }
static void retry(void) {
    for (int i = 0; i < 30; ++i) {
        __block BOOL pending;
        dispatch_sync(hid_q, ^{
            pending = timers.count != 0;
            if (!pending) return;
            dispatch_block_t b = timers[0]; [timers removeObjectAtIndex:0];
            clock_ns += 20000000; b();
        });
        if (!pending) return;
    }
    CHECK(0); // a failed transport must not retry forever
}
static void update(void) { clock_ns += 1100000000; rumble_update(); drain(); }
int main(void) {
    setenv("NFS_RUMBLE", "off", 1);
    rumble_update(); rumble_suppress(1); rumble_suppress(0);
    CHECK(!manager && !hid_q && reports == 0); // unattended/off never discovers or writes a controller
    unsetenv("NFS_RUMBLE"); rumble_off = -1;
    // A CFString stands in for a retained HID handle; discovery is never opened.
    manager = (IOHIDManagerRef)(uintptr_t)1;
    pad = (IOHIDDeviceRef)CFRetain(CFSTR("fake-pad"));
    hid_q = dispatch_queue_create("nfsmw.rumble.test", DISPATCH_QUEUE_SERIAL);
    for (int transport = 0; transport < 2; ++transport) {
        bluetooth = transport;
        update(); CHECK(motor_h > 0 && motor_l > 0);
        dispatch_suspend(hid_q);
        rumble_update();                     // a nonzero report is pending when F10 opens
        rumble_suppress(1);
        int old_nonzero = nonzero;
        dispatch_resume(hid_q); drain();
        CHECK(motor_h == 0 && motor_l == 0);
        CHECK(nonzero == old_nonzero);       // the pending report was muted before it reached the device
        for (int i = 0; i < 3; ++i) update(); // even a stale/nonzero producer cannot override the queue gate
        CHECK(motor_h == 0 && motor_l == 0 && nonzero == old_nonzero);
        rumble_suppress(0); drain();
        CHECK(motor_h == 0 && motor_l == 0 && nonzero == old_nonzero);
        update(); CHECK(motor_h > 0 && motor_l > 0); // only the next update takes over
        detached = 1;
        removed(NULL, 0, NULL, pad); drain();
        CHECK(!pad && want == 0 && model_h == 0 && model_l == 0);
        detached = 0;
        found(NULL, 0, NULL, (IOHIDDeviceRef)CFSTR("fake-pad")); drain();
        CHECK(motor_h == 0 && motor_l == 0); // attach clears before any fresh pad read
        dispatch_sync(hid_q, ^{ flush(); });
        CHECK(motor_h == 0 && motor_l == 0);
        model_h = .8f; model_l = .4f; update();
        reject_zeros = 1; int before_reject = rejected;
        rumble_stop(); drain();
        CHECK(rejected == before_reject + 1 && motor_h > 0 && motor_l > 0);
        CHECK(timers.count == 1); // only the scheduled retry can complete the clear, without a pad read
        retry();
        CHECK(rejected == before_reject + 1 && motor_h == 0 && motor_l == 0);
        CHECK(sent_heavy == 0 && sent_light == 0);
        model_h = .8f; model_l = .4f; update();
        reject_zeros = -1; int before_reports = reports;
        rumble_stop(); drain(); retry();
        CHECK(reports > before_reports && reports - before_reports <= 15);
        CHECK(motor_h > 0 && sent_heavy > 0); // a rejected clear never enters the success cache
        reject_zeros = 0; rumble_stop(); drain(); retry();
        CHECK(motor_h == 0 && motor_l == 0);
        // Old publications cannot regain ownership after a detach/attach cycle.
        model_h = .8f; model_l = .4f; update();
        uint32_t old_token = motor_generation;
        removed(NULL, 0, NULL, pad); drain();
        reject_zeros = 1;
        found(NULL, 0, NULL, (IOHIDDeviceRef)CFSTR("fake-pad")); drain();
        CHECK(motor_h > 0 && timers.count == 1); // attachment neutral was rejected
        retry();
        CHECK(motor_h == 0 && motor_l == 0);
        post_levels_at(204, 102, old_token); drain();
        CHECK(motor_h == 0 && motor_l == 0);
        model_h = .8f; model_l = .4f;
    }
    rumble_close();
    CHECK(motor_h == 0 && motor_l == 0);
    update(); CHECK(motor_h == 0 && motor_l == 0);
    CFRelease(pad); pad = NULL; manager = NULL;
    CHECK(invalidations >= 2);
    CHECK(real_opens == 0);
    printf("rumble_test: %s, %d checks, %d failures, %d fake reports (USB + Bluetooth)\n",
           failures ? "FAIL" : "PASS", checks, failures, reports);
    return failures != 0;
}
