// Offline HID queue regression: actual rumble.m, intercepted IOHIDDeviceSetReport, no device opened.
// cc -Wall -Wextra -Werror -g -fsanitize=address,undefined -fobjc-arc -I recomp runtime/rumble_test.m \
//   -framework Foundation -framework IOKit -lz -o /tmp/rumble_test && /tmp/rumble_test
// Result: PASS, 15 checks, 0 failures, 13 fake USB/Bluetooth reports (ASan/UBSan).
#define IOHIDDeviceSetReport test_set_report
#include "rumble.m"

int diag_on;
static int reports, nonzero, failures, checks;
static uint8_t motor_h, motor_l;
static float model_h = 0.8f, model_l = 0.4f;
static uint64_t clock_ns = 1000000000;
uint64_t host_ticks(void) { return clock_ns; }
void feedback_levels(float *h, float *l) { *h = model_h; *l = model_l; }
void diag_event(const char *fmt, ...) { (void)fmt; }
void padfx_close(void) {}
IOReturn test_set_report(IOHIDDeviceRef device, IOHIDReportType type, CFIndex id, const uint8_t *bytes, CFIndex n) {
    (void)device; (void)type;
    // Both USB and Bluetooth encode light then heavy in the common payload.
    const uint8_t *c = bytes + (id == 0x31 ? 2 : 1);
    if (n != (id == 0x31 ? 78 : 48)) abort();
    motor_l = c[2]; motor_h = c[3]; ++reports;
    nonzero += motor_h != 0 || motor_l != 0;
    return kIOReturnSuccess;
}
#define CHECK(x) do { ++checks; if (!(x)) { ++failures; fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); } } while (0)
static void drain(void) { dispatch_sync(hid_q, ^{}); }
static void update(void) { clock_ns += 1100000000; rumble_update(); drain(); }
int main(void) {
    setenv("NFS_RUMBLE", "off", 1);
    rumble_update(); rumble_suppress(1); rumble_suppress(0);
    CHECK(!manager && !hid_q && reports == 0); // unattended/off never discovers or writes a controller
    unsetenv("NFS_RUMBLE"); rumble_off = -1;
    // Fake references: rumble_open / found / removed / rumble_close are never called in this harness.
    manager = (IOHIDManagerRef)(uintptr_t)1;
    pad = (IOHIDDeviceRef)(uintptr_t)1;
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
    }
    rumble_close();
    CHECK(motor_h == 0 && motor_l == 0);
    update(); CHECK(motor_h == 0 && motor_l == 0);
    pad = NULL; manager = NULL;
    printf("rumble_test: %s, %d checks, %d failures, %d fake reports (USB + Bluetooth)\n",
           failures ? "FAIL" : "PASS", checks, failures, reports);
    return failures != 0;
}
