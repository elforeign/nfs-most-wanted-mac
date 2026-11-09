// Per-second system evidence. Registry, IOPS and IOReport work stays on a utility queue.
#import <Foundation/Foundation.h>
#include <IOKit/ps/IOPowerSources.h>
#include <IOKit/ps/IOPSKeys.h>
#include <dispatch/dispatch.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "slowlog.h"

static int enabled;
static dispatch_source_t sampler_timer;
static double seconds(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec / 1e9;
}
static void empty(SlowlogSample *s) {
    memset(s, 0, sizeof *s);
    s->enabled = enabled;
    s->battery_pct = s->gpu.dev_util = s->gpu.self_ms = s->gpu.other_ms = NAN;
    s->thermal = s->low_power = -1;
    strcpy(s->power, "unknown");
    strcpy(s->gpu.top, "-"); strcpy(s->gpu.mhz, "-"); strcpy(s->gpu.states, "-");
    strcpy(s->gpu.clock_status, enabled ? "pending" : "disabled");
}

// SPSC queue: the producer cannot overwrite an unread wait, even if sampling stalls.
// The consumer drains a fixed head, so neither side spins or waits for the other.
#define WAITS 4096u
static uint64_t waits[WAITS], wait_head, wait_tail, dropped;
_Static_assert(__atomic_always_lock_free(sizeof(uint64_t), 0), "64-bit counters must be lock-free");
void slowlog_lum_wait(uint64_t ns) {
    if (!enabled) return;
    uint64_t h = __atomic_load_n(&wait_head, __ATOMIC_RELAXED);
    if (h - __atomic_load_n(&wait_tail, __ATOMIC_ACQUIRE) == WAITS) {
        __atomic_fetch_add(&dropped, 1, __ATOMIC_RELAXED);
        return;
    }
    waits[h % WAITS] = ns;
    __atomic_store_n(&wait_head, h + 1, __ATOMIC_RELEASE);
}
static void drain_waits(SlowlogSample *s) {
    uint64_t h = __atomic_load_n(&wait_head, __ATOMIC_ACQUIRE);
    uint64_t t = __atomic_load_n(&wait_tail, __ATOMIC_RELAXED), sum = 0, max = 0;
    s->lum_count = h - t;
    for (; t < h; ++t) {
        uint64_t ns = waits[t % WAITS];
        sum += ns;
        if (ns > max) max = ns;
    }
    __atomic_store_n(&wait_tail, t, __ATOMIC_RELEASE);
    s->lum_sum_ms = sum / 1e6; s->lum_max_ms = max / 1e6;
    s->lum_dropped = __atomic_exchange_n(&dropped, 0, __ATOMIC_RELAXED);
}

static void power_sample(SlowlogSample *s) {
    NSProcessInfo *p = NSProcessInfo.processInfo;
    NSInteger thermal = p.thermalState;
    s->thermal = thermal >= 0 && thermal <= 3 ? (int)thermal : -1;
    s->low_power = p.lowPowerModeEnabled ? 1 : 0;
    CFTypeRef info = IOPSCopyPowerSourcesInfo();
    if (!info) return;
    CFStringRef type = IOPSGetProvidingPowerSourceType(info);
    if (type && CFEqual(type, CFSTR(kIOPMACPowerKey))) strcpy(s->power, "AC");
    else if (type && CFEqual(type, CFSTR(kIOPMBatteryPowerKey))) strcpy(s->power, "battery");
    else if (type && CFEqual(type, CFSTR(kIOPMUPSPowerKey))) strcpy(s->power, "UPS");
    CFArrayRef sources = IOPSCopyPowerSourcesList(info);
    double current = 0, capacity = 0;
    for (CFIndex i = 0; sources && i < CFArrayGetCount(sources); ++i) {
        CFDictionaryRef d = IOPSGetPowerSourceDescription(info, CFArrayGetValueAtIndex(sources, i));
        if (!d) continue;
        // Do not mix a UPS's capacity with the internal battery's charge.
        CFStringRef transport = CFDictionaryGetValue(d, CFSTR(kIOPSTypeKey));
        if (!transport || !CFEqual(transport, CFSTR(kIOPSInternalBatteryType))) continue;
        CFNumberRef c = CFDictionaryGetValue(d, CFSTR(kIOPSCurrentCapacityKey));
        CFNumberRef m = CFDictionaryGetValue(d, CFSTR(kIOPSMaxCapacityKey));
        double cv = 0, mv = 0;
        if (c && m && CFGetTypeID(c) == CFNumberGetTypeID() && CFGetTypeID(m) == CFNumberGetTypeID() &&
            CFNumberGetValue(c, kCFNumberDoubleType, &cv) && CFNumberGetValue(m, kCFNumberDoubleType, &mv) &&
            cv >= 0 && mv > 0 && cv <= mv) { current += cv; capacity += mv; }
    }
    if (capacity > 0) s->battery_pct = 100 * current / capacity;
    if (sources) CFRelease(sources);
    CFRelease(info);
}

// Triple-buffer ownership: producer, consumer and middle each own one slot.
// Exchanging ownership is the only synchronization; memcpy never races a writer.
static SlowlogSample slots[3];
static unsigned middle = 1, back = 2, front;
static void publish(const SlowlogSample *s) {
    slots[back] = *s;
    back = __atomic_exchange_n(&middle, back | 4u, __ATOMIC_ACQ_REL) & 3u;
}
void slowlog_cached(SlowlogSample *s) {
    empty(s);
    if (!enabled) return;
    if (__atomic_load_n(&middle, __ATOMIC_ACQUIRE) & 4u)
        front = __atomic_exchange_n(&middle, front, __ATOMIC_ACQ_REL) & 3u;
    if (slots[front].serial && seconds() - slots[front].time < 3) *s = slots[front];
}
void slowlog_start(void) {
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        const char *v = getenv("NFS_SLOWLOG");
        enabled = !(v && !strcmp(v, "0"));
        if (!enabled) return;
        dispatch_queue_t q = dispatch_queue_create("nfs.slowlog", dispatch_queue_attr_make_with_qos_class(DISPATCH_QUEUE_SERIAL, QOS_CLASS_UTILITY, 0));
        dispatch_async(q, ^{
            GPUClients *gpu = gpuclients_create();
            GPUClientsSample baseline;
            gpuclients_sample(gpu, &baseline);
            __block double previous = seconds();
            __block uint64_t serial = 0;
            sampler_timer = dispatch_source_create(DISPATCH_SOURCE_TYPE_TIMER, 0, 0, q);
            dispatch_source_set_timer(sampler_timer, dispatch_time(DISPATCH_TIME_NOW, NSEC_PER_SEC), NSEC_PER_SEC, 50 * NSEC_PER_MSEC);
            dispatch_source_set_event_handler(sampler_timer, ^{
                @autoreleasepool {
                    SlowlogSample s; empty(&s);
                    s.time = seconds(); s.interval = s.time - previous; previous = s.time;
                    s.serial = ++serial;
                    drain_waits(&s);
                    gpuclients_sample(gpu, &s.gpu);
                    power_sample(&s);
                    gpuclients_publish(&s.gpu); // fullscreen transition notes use the legacy cache
                    publish(&s);
                }
            });
            dispatch_resume(sampler_timer); // process-lifetime source; handler owns the sampler
        });
    });
}
void slowlog_print(FILE *f, const SlowlogSample *s, const char *post_path) {
    // A cached sample may span a post-path switch and be printed more than once.
    // Waits belong to its collection interval; emit each interval's deltas once.
    static uint64_t printed_serial;
    int fresh = s->serial && s->serial != printed_serial;
    if (s->serial) printed_serial = s->serial;
    fprintf(f, "\t%d\t%llu\t%.3f\t", s->enabled, (unsigned long long)s->serial, s->interval);
    if (s->serial) fprintf(f, "%.1f", (seconds() - s->time) * 1000); else fputc('-', f);
    fprintf(f, "\t%s\t%.6f\t%.6f\t%llu\t%llu\t%s\t", post_path, fresh ? s->lum_sum_ms : 0, fresh ? s->lum_max_ms : 0,
            (unsigned long long)(fresh ? s->lum_count : 0), (unsigned long long)(fresh ? s->lum_dropped : 0), s->power);
    if (isfinite(s->battery_pct)) fprintf(f, "%.1f", s->battery_pct); else fputc('-', f);
    fprintf(f, "\t%s\t%s", s->gpu.states, s->gpu.clock_status);
}
