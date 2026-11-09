// Fake HID sink for trigopt_test: production compatible-vibration transport,
// production feedback_levels supplied by the main test, no hardware discovery.
#import <Foundation/Foundation.h>
#define IOHIDDeviceSetReport trigopt_hid_report
#define IOHIDManagerCreate trigopt_hid_open
#define rumble_signals unused_rumble_signals
#include "rumble.m"
#undef rumble_signals
static unsigned reports, nonzero_reports, opens;
static uint8_t last_report[78];
static CFIndex last_size;
IOReturn trigopt_hid_report(IOHIDDeviceRef d, IOHIDReportType type, CFIndex id, const uint8_t *p, CFIndex n) {
    (void)d; (void)type; (void)id;
    ++reports; nonzero_reports += p[3] != 0 || p[4] != 0;
    memcpy(last_report, p, (size_t)n); last_size = n;
    return kIOReturnSuccess;
}
IOHIDManagerRef trigopt_hid_open(CFAllocatorRef a, IOOptionBits b) { (void)a; (void)b; ++opens; abort(); }
void rumble_signals(void) {}
void trigopt_rumble_init(void) {
    manager = (IOHIDManagerRef)(uintptr_t)1;
    pad = (IOHIDDeviceRef)CFRetain(CFSTR("test-pad"));
    hid_q = dispatch_queue_create("trigopt.test", DISPATCH_QUEUE_SERIAL);
    rumble_off = 0;
}
unsigned trigopt_rumble_sample(uint8_t *bytes) {
    // Force one identical-state report each sample, independently of dedup's
    // one-second heartbeat. Every payload byte still comes from rumble.m.
    sent_heavy = sent_light = 255;
    rumble_update(); dispatch_sync(hid_q, ^{});
    memcpy(bytes, last_report, (size_t)last_size);
    return reports;
}
unsigned trigopt_rumble_nonzero(void) { return nonzero_reports; }
unsigned trigopt_rumble_opens(void) { return opens; }
