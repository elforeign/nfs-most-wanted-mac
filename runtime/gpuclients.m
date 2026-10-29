// AGX counters are cumulative per registry client, not per PID. Baseline by registry ID so
// a newly seen client cannot contribute its lifetime total. Never called on the render thread.
#import <Foundation/Foundation.h>
#include <IOKit/IOKitLib.h>
#include <dispatch/dispatch.h>
#include <dlfcn.h>
#include <math.h>
#include <pthread.h>
#include <time.h>
#include <unistd.h>
#include "gpuclients.h"

struct GPUClients {
    NSMutableDictionary *previous;
    double time;
    void *lib;
    CFDictionaryRef channels, subscription, before;
    CFDictionaryRef (*copy)(CFStringRef, CFStringRef, uint64_t, uint64_t, uint64_t);
    CFDictionaryRef (*subscribe)(void *, CFMutableDictionaryRef, CFMutableDictionaryRef *, uint64_t, CFTypeRef);
    CFDictionaryRef (*samples)(CFTypeRef, CFDictionaryRef, CFTypeRef);
    CFDictionaryRef (*delta)(CFDictionaryRef, CFDictionaryRef, CFTypeRef);
    int (*iterate)(CFDictionaryRef, int (^)(CFDictionaryRef));
    int (*count)(CFDictionaryRef);
    CFStringRef (*name)(CFDictionaryRef, int);
    int64_t (*residency)(CFDictionaryRef, int);
};

static double monotime(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec / 1e9;
}
static void empty_sample(GPUClientsSample *out) {
    *out = (GPUClientsSample){.dev_util=NAN, .self_ms=NAN, .other_ms=NAN};
    strcpy(out->top, "-"); strcpy(out->mhz, "-");
}
static NSString *safe_name(NSString *name) {
    // Keep TSV and the name:ms,other:ms grammar unambiguous even for unusual process names.
    NSCharacterSet *bad = [NSCharacterSet characterSetWithCharactersInString:@"\t\r\n,: "];
    return [[name componentsSeparatedByCharactersInSet:bad] componentsJoinedByString:@"_"];
}
static NSDictionary *decode_client(id creator, id usage) {
    if (![creator isKindOfClass:NSString.class] || ![usage isKindOfClass:NSArray.class]) return nil;
    // UTF8String is NULL for a name with an unpaired surrogate (review 2026-10-28): treat as unreadable.
    const char *text = [creator UTF8String];
    int pid = -1, offset = 0;
    if (!text || sscanf(text, "pid %d, %n", &pid, &offset) != 1 || offset <= 0 || pid < 0) return nil;
    uint64_t ns = 0; BOOL have = NO;
    for (id item in usage) {
        if (![item isKindOfClass:NSDictionary.class]) continue;
        id value = item[@"accumulatedGPUTime"];
        if (![value isKindOfClass:NSNumber.class]) continue;
        // A negative counter would wrap to ~UINT64_MAX and fake contention: skip it.
        if ([value longLongValue] < 0) continue;
        uint64_t v = [value unsignedLongLongValue];
        if (UINT64_MAX - ns < v) continue;
        ns += v; have = YES;
    }
    NSString *name = safe_name([NSString stringWithUTF8String:text + offset] ?: @"unknown");
    return have ? @{@"ns":@(ns), @"pid":@(pid), @"name":name.length ? name : @"unknown"} : nil;
}
static NSDictionary *read_clients(void) {
    // User clients are never registered services, so IOServiceGetMatchingServices finds none (live proof 2026-10-28:
    // self 0 while ioreg listed 70). Walk each accelerator's children in the service plane instead.
    io_iterator_t accels = 0;
    if (IOServiceGetMatchingServices(kIOMainPortDefault, IOServiceMatching("AGXAccelerator"), &accels)) return nil;
    NSMutableDictionary *clients = [NSMutableDictionary dictionary];
    io_service_t accel;
    while ((accel = IOIteratorNext(accels))) {
    io_iterator_t iter = 0;
    kern_return_t it_rc = IORegistryEntryGetChildIterator(accel, kIOServicePlane, &iter);   // clients are direct children; a recursive walk cost 6 ms
    IOObjectRelease(accel);
    if (it_rc) continue;
    io_service_t entry;
    while ((entry = IOIteratorNext(iter))) {
        if (!IOObjectConformsTo(entry, "AGXDeviceUserClient")) { IOObjectRelease(entry); continue; }
        uint64_t entry_id = 0;
        NSString *creator = CFBridgingRelease(IORegistryEntryCreateCFProperty(entry, CFSTR("IOUserClientCreator"), kCFAllocatorDefault, 0));
        NSArray *usage = CFBridgingRelease(IORegistryEntryCreateCFProperty(entry, CFSTR("AppUsage"), kCFAllocatorDefault, 0));
        kern_return_t id_rc = IORegistryEntryGetRegistryEntryID(entry, &entry_id);
        IOObjectRelease(entry);
        NSDictionary *decoded = decode_client(creator, usage);
        if (!id_rc && decoded) clients[@(entry_id)] = decoded;
    }
    IOObjectRelease(iter);
    }
    IOObjectRelease(accels);
    return clients.count ? clients : nil; // no readable AGX clients means unknown, not zero GPU usage
}
static double read_util(void) {
    io_iterator_t iter = 0;
    if (IOServiceGetMatchingServices(kIOMainPortDefault, IOServiceMatching("AGXAccelerator"), &iter)) return NAN;
    double result = NAN;
    io_service_t entry;
    while ((entry = IOIteratorNext(iter))) {
        NSDictionary *stats = CFBridgingRelease(IORegistryEntryCreateCFProperty(entry, CFSTR("PerformanceStatistics"), kCFAllocatorDefault, 0));
        IOObjectRelease(entry);
        if (![stats isKindOfClass:NSDictionary.class]) continue;
        id v = stats[@"Device Utilization %"];
        if ([v isKindOfClass:NSNumber.class]) result = [v doubleValue];
    }
    IOObjectRelease(iter);
    return result;
}
static void diff_clients(NSDictionary *before, NSDictionary *now, double elapsed, int self_pid, GPUClientsSample *out) {
    if (!before || !now || elapsed <= 0) return;
    out->self_ms = out->other_ms = 0;
    NSMutableDictionary<NSString *, NSNumber *> *totals = [NSMutableDictionary dictionary];
    for (NSNumber *key in now) {
        NSDictionary *a = before[key], *b = now[key];
        if (!a || ![a[@"pid"] isEqual:b[@"pid"]]) continue;
        uint64_t x = [a[@"ns"] unsignedLongLongValue], y = [b[@"ns"] unsignedLongLongValue];
        if (y < x) continue; // counter reset
        double ms = (y - x) / 1e6 / elapsed;
        if (ms > 1000.0 * 64) continue; // beyond any real per-client GPU time per second: a bad counter, not load
        if ([b[@"pid"] intValue] == self_pid) out->self_ms += ms;
        else {
            out->other_ms += ms;
            NSString *name = b[@"name"];
            totals[name] = @([totals[name] doubleValue] + ms);
        }
    }
    NSArray *names = [totals.allKeys sortedArrayUsingComparator:^NSComparisonResult(NSString *a, NSString *b) {
        NSComparisonResult r = [totals[b] compare:totals[a]];
        return r == NSOrderedSame ? [a compare:b] : r;
    }];
    NSMutableArray *top = [NSMutableArray array];
    for (NSString *name in names) {
        if ([totals[name] doubleValue] <= 0) continue;
        // Bound individual names so all three entries fit without truncating the number/grammar.
        [top addObject:[NSString stringWithFormat:@"%@:%.2f", [name substringToIndex:MIN(name.length, 100)], [totals[name] doubleValue]]];
        if (top.count == 3) break;
    }
    if (top.count) snprintf(out->top, sizeof out->top, "%s", [top componentsJoinedByString:@","].UTF8String);
}

GPUClients *gpuclients_create(void) {
    GPUClients *s = calloc(1, sizeof *s);
    if (!s) return NULL;
    s->lib = dlopen("/usr/lib/libIOReport.dylib", RTLD_LAZY | RTLD_LOCAL);
    if (!s->lib) return s;
#define LOAD(member, symbol) do { *(void **)(&s->member) = dlsym(s->lib, symbol); if (!s->member) return s; } while (0)
    LOAD(copy, "IOReportCopyChannelsInGroup"); LOAD(subscribe, "IOReportCreateSubscription");
    LOAD(samples, "IOReportCreateSamples"); LOAD(delta, "IOReportCreateSamplesDelta");
    LOAD(iterate, "IOReportIterate"); LOAD(count, "IOReportStateGetCount");
    LOAD(name, "IOReportStateGetNameForIndex"); LOAD(residency, "IOReportStateGetResidency");
#undef LOAD
    s->channels = s->copy(CFSTR("GPU Stats"), CFSTR("GPU Performance States"), 0, 0, 0);
    if (!s->channels) return s;
    CFMutableDictionaryRef actual = NULL;
    s->subscription = s->subscribe(NULL, (CFMutableDictionaryRef)s->channels, &actual, 0, NULL);
    if (actual) { CFRelease(s->channels); s->channels = actual; }
    return s;
}
void gpuclients_destroy(GPUClients *s) {
    if (!s) return;
    s->previous = nil;
    if (s->before) CFRelease(s->before);
    if (s->subscription) CFRelease(s->subscription);
    if (s->channels) CFRelease(s->channels);
    if (s->lib) dlclose(s->lib);
    free(s);
}
static void clock_sample(GPUClients *s, GPUClientsSample *out) {
    if (!s->subscription) return;
    CFDictionaryRef current = s->samples(s->subscription, s->channels, NULL);
    if (!current) { if (s->before) CFRelease(s->before); s->before = NULL; return; }
    CFDictionaryRef delta = s->before ? s->delta(s->before, current, NULL) : NULL;
    if (s->before) CFRelease(s->before);
    s->before = current;
    if (!delta) return;
    __block double weighted = 0, total = 0, dominant = 0;
    __block BOOL decoded = YES;
    __block NSString *state = nil;
    s->iterate(delta, ^int(CFDictionaryRef channel) {
        for (int i = 0, n = s->count(channel); i < n && i < 256; ++i) {
            double residency = s->residency(channel, i);
            if (residency <= 0) continue;
            NSString *name = (__bridge NSString *)s->name(channel, i);
            if (![name isKindOfClass:NSString.class]) { decoded = NO; continue; }
            NSString *lower = name.lowercaseString;
            if ([lower containsString:@"idle"] || [lower containsString:@"off"] || [lower containsString:@"down"]) continue;
            if (residency > dominant) { dominant = residency; state = name; }
            double mhz = 0; char unit[16] = {0};
            if (sscanf(name.UTF8String, "%lf %15s", &mhz, unit) == 2 && mhz > 0 &&
                (!strcasecmp(unit, "MHz") || !strcasecmp(unit, "GHz"))) {
                if (!strcasecmp(unit, "GHz")) mhz *= 1000;
                weighted += residency * mhz;
            } else decoded = NO;
            total += residency;
        }
        return 0;
    });
    if (decoded && total > 0) snprintf(out->mhz, sizeof out->mhz, "%.1f", weighted / total);
    // Prefix opaque labels: a numeric state ID must never masquerade as MHz in the reader.
    else if (state) snprintf(out->mhz, sizeof out->mhz, "state=%s", safe_name(state).UTF8String ?: "unknown");
    CFRelease(delta);
}
void gpuclients_sample(GPUClients *s, GPUClientsSample *out) {
    empty_sample(out);
    if (!s) return;
    @autoreleasepool {
        double now = monotime();
        NSDictionary *clients = read_clients();
        out->dev_util = read_util();
        diff_clients(s->previous, clients, now - s->time, getpid(), out);
        s->previous = [clients mutableCopy]; s->time = now;
        clock_sample(s, out);
    }
}

static pthread_mutex_t cache_lock = PTHREAD_MUTEX_INITIALIZER;
static GPUClientsSample cache;
static double cache_time;
static dispatch_source_t timer;
void gpuclients_start(void) {
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        dispatch_queue_t q = dispatch_queue_create("nfs.gpuclients", dispatch_queue_attr_make_with_qos_class(DISPATCH_QUEUE_SERIAL, QOS_CLASS_UTILITY, 0));
        dispatch_async(q, ^{
            GPUClients *s = gpuclients_create();
            GPUClientsSample initial;
            gpuclients_sample(s, &initial); // baseline; do not publish boot totals
            timer = dispatch_source_create(DISPATCH_SOURCE_TYPE_TIMER, 0, 0, q);
            dispatch_source_set_timer(timer, dispatch_time(DISPATCH_TIME_NOW, NSEC_PER_SEC), NSEC_PER_SEC, 50 * NSEC_PER_MSEC);
            dispatch_source_set_event_handler(timer, ^{
                GPUClientsSample value;
                gpuclients_sample(s, &value);
                pthread_mutex_lock(&cache_lock);
                cache = value; cache_time = monotime();
                pthread_mutex_unlock(&cache_lock);
            });
            dispatch_resume(timer);
        });
    });
}
void gpuclients_cached(GPUClientsSample *out) {
    empty_sample(out);
    if (pthread_mutex_trylock(&cache_lock)) return;
    if (cache_time > 0 && monotime() - cache_time < 3) *out = cache;
    pthread_mutex_unlock(&cache_lock);
}
void gpuclients_print(FILE *f, const GPUClientsSample *s) {
    const double values[] = {s->dev_util, s->self_ms, s->other_ms};
    for (int i = 0; i < 3; ++i) {
        if (isfinite(values[i])) fprintf(f, "\t%.2f", values[i]); else fputs("\t-", f);
    }
    fprintf(f, "\t%s\t%s", s->top, s->mhz);
}
