// Production report save action + writer, with a fake guest sound writer and slow bundle export.
// The paired Python driver pins the original functions and records before/after write gaps.
#import <Cocoa/Cocoa.h>
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdatomic.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
#include "d3d9_internal.h"
#include "settings.h"
#include "report_privacy.h"

static int checks, failures;
#define CHECK(x) do { ++checks; if (!(x)) { ++failures; printf("FAIL line %d: %s\n", __LINE__, #x); } } while (0)
static pthread_t main_thread;
static pthread_mutex_t guest = PTHREAD_MUTEX_INITIALIZER;
static _Thread_local int guest_held;
static _Atomic int sound_stop, sound_writes;
static double sound_gap;
static uint64_t mono(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint64_t)t.tv_sec * 1000000000ull + (uint64_t)t.tv_nsec;
}
static void *sound_main(void *p) {
    (void)p;
    uint64_t last = mono();
    while (!atomic_load(&sound_stop)) {
        pthread_mutex_lock(&guest);
        uint64_t now = mono();
        double gap = (now - last) / 1e6;
        if (gap > sound_gap) sound_gap = gap;
        last = now;
        atomic_fetch_add(&sound_writes, 1);
        pthread_mutex_unlock(&guest);
        usleep(5000);
    }
    return NULL;
}
static void guest_read(void) { CHECK(pthread_equal(main_thread, pthread_self()) && guest_held); }
static void guest_enter(void) { pthread_mutex_lock(&guest); guest_held = 1; }
static void guest_leave(void) { guest_held = 0; pthread_mutex_unlock(&guest); }

uint8_t *G;
Device dev;
int diag_on;
const char speed_exe_fingerprint[] = "0123456789abcdef";
static char profile[1200], status[1600];
static int snapshot_gets, completed, export_held, exports;
static _Atomic int note_count;
static dispatch_semaphore_t image_gate;
static _Atomic int block_image;
static const char *const kCatV[] = {"Audio", "Graphics", "Controls", "Gameplay", "Crash-freeze", "Other"};
static Setting cat = {.type = SET_CHOICE, .value = 0};
static Setting choice = {.id = "graphics.fixture", .type = SET_CHOICE, .value = 1, .def = 0, .restart = 1};
static char user_note[96] = "synthetic initial note";
const char *userdata_dir(void) { return profile; }
uint64_t host_ticks(void) { return 123456000000ull; }
uint64_t diag_current_frame(void) { guest_read(); return 97552; }
const char *port_build_id(void) { return "synthetic-build"; }
int settings_count(void) { guest_read(); ++snapshot_gets; return 1; }
Setting *settings_at(int i) { guest_read(); (void)i; return &choice; }
Setting *settings_find(const char *id) { guest_read(); (void)id; return &cat; }
const char *settings_text(const char *id) { guest_read(); (void)id; return user_note; }
const char *settings_source(const Setting *s) { guest_read(); (void)s; return "app.txt"; }
const char *settings_value_name(const Setting *s, int v, char *b, int n) {
    guest_read(); (void)s; snprintf(b, (size_t)n, "%s", v ? "X360" : "PC"); return b;
}
int settings_get(const char *id) { guest_read(); (void)id; return 0; }
int render_msaa_samples(void) { guest_read(); return 2; }
int x360_shadow_map_size(void) { guest_read(); return 2048; }
int user32_pad_layout_xbox(void) { guest_read(); return 1; }
int dsound_output_info(const char **mode, int *live, uint64_t *under) {
    guest_read(); *mode = "synthetic stereo"; *live = 1; *under = 7; return 1;
}
void diag_ring_dump(FILE *f, int n) { guest_read(); CHECK(n == 400); fputs("diagnostic fixture event\n", f); }
void report_note(const char *fmt, ...) { (void)fmt; usleep(100000); ++note_count; }
void diag_event(const char *fmt, ...) { (void)fmt; }
void portmenu_status(const char *fmt, ...) {
    CHECK(pthread_equal(main_thread, pthread_self()));
    va_list ap; va_start(ap, fmt); vsnprintf(status, sizeof status, fmt, ap); va_end(ap);
    if (!strncmp(status, "Report saved:", 13) || !strcmp(status, "Couldn't write the report")) ++completed;
}
static void SlowRedact(NSString *folder) {
    // Model a slow disk/privacy pass; only the old path runs it while owning the guest lock.
    ++exports;
    export_held += guest_held;
    usleep(650000);
    ReportRedactBundle(folder);
}
static time_t FixtureTime(time_t *out) { time_t value = 1700000000; if (out) *out = value; return value; }
static uint64_t t_start = 100000000000ull;
static double uptime(void) { return (host_ticks() - t_start) / 1e9; }
static char sys_display[160] = "synthetic display", sys_edr[128] = "output=SDR sRGB", sys_pad[128] = "synthetic controller";
static char sys_os[128] = "synthetic OS", sys_model[128] = "synthetic model", sys_device[96] = "synthetic device";
#define GAMEFLOW_STATE 0x00925E90u
#define NOTES 512
#define NOTE_LEN 176
static char notes[NOTES][NOTE_LEN];
static volatile uint32_t notes_head;
#define SECS 180
#include "report_sec.inc"
static Sec secs[SECS];
static uint64_t nsecs;
#include "report_format.inc"
static int GatedMkdir(const char *path, mode_t mode) {
    if (image_gate && atomic_exchange(&block_image, 0))
        dispatch_semaphore_wait(image_gate, DISPATCH_TIME_FOREVER);
    return mkdir(path, mode);
}
#define mkdir GatedMkdir
#define time FixtureTime
#define ReportRedactBundle SlowRedact
#include "report_writer.inc"
#undef ReportRedactBundle
#undef time
#undef mkdir
#include "report_save.inc"

static NSString *Read(const char *dir, NSString *name) {
    return [NSString stringWithContentsOfFile:[[NSString stringWithUTF8String:dir] stringByAppendingPathComponent:name]
        encoding:NSUTF8StringEncoding error:NULL];
}
static void pump_until(int target) {
    uint64_t deadline = mono() + 5000000000ull;
    while (completed < target && mono() < deadline) {
        [[NSRunLoop currentRunLoop] runUntilDate:[NSDate dateWithTimeIntervalSinceNow:0.005]];
    }
    CHECK(completed == target);
}
static void reset_guest_state(void) {
    choice.value = 1; cat.value = 0;
    snprintf(user_note, sizeof user_note, "synthetic initial note");
    M32(GAMEFLOW_STATE) = 6;
    M32(0x00901830u) = 1024;
    dev.pp[0] = 1920; dev.pp[1] = 1080;
    nsecs = 181;
    for (int i = 0; i < SECS; ++i)
        secs[i] = (Sec){.t = i, .frame = (uint64_t)i * 60, .fps = 60, .p50 = 16.7, .p99 = 17, .max = 18,
            .busy = 8, .gpu = 7, .rms = {0.1, 0.2, 0.3, 0.4, 0.5, 0.6}, .have_rms = i % 2, .flow = 6};
    notes_head = 513;
    for (int i = 0; i < NOTES; ++i) snprintf(notes[i], NOTE_LEN, "fixture event %d", i);
}
static int budget_checks(const char *root) {
    NSString *folder = [NSString stringWithUTF8String:root];
    [NSFileManager.defaultManager createDirectoryAtPath:folder withIntermediateDirectories:YES attributes:nil error:NULL];
    NSString *source = [folder stringByAppendingPathComponent:@"budget.log"];
    NSString *dest = [folder stringByAppendingPathComponent:@"budget.txt"];
    const size_t budget = 1024 * 1024;
    // Above the scan budget, no raw suffix may escape from an unseen multiline credential.
    NSMutableString *input = [NSMutableString stringWithString:@"password=\""];
    [input appendString:[@"x" stringByPaddingToLength:budget withString:@"x" startingAtIndex:0]];
    [input appendString:@"\nSyntheticOversizeContinuation\"\nbuild: fixture\n"];
    CHECK([input writeToFile:source atomically:YES encoding:NSUTF8StringEncoding error:NULL]);
    ReportPrivacyCopyLog(source.fileSystemRepresentation, dest.fileSystemRepresentation, 256 * 1024);
    NSString *out = [NSString stringWithContentsOfFile:dest encoding:NSUTF8StringEncoding error:NULL];
    CHECK([out hasPrefix:@"Log omitted:"]);
    CHECK(![out containsString:@"SyntheticOversizeContinuation"]);
    CHECK(out.length < 256 * 1024);
    // A large quoted field can exhaust ICU even below the byte budget; this must fail closed.
    input = [NSMutableString stringWithString:@"password=\""];
    [input appendString:[@"x" stringByPaddingToLength:budget - 128 withString:@"x" startingAtIndex:0]];
    [input appendString:@"\nSyntheticWithinBudgetContinuation\"\nbuild: retained\n"];
    CHECK([input writeToFile:source atomically:YES encoding:NSUTF8StringEncoding error:NULL]);
    ReportPrivacyCopyLog(source.fileSystemRepresentation, dest.fileSystemRepresentation, 256 * 1024);
    out = [NSString stringWithContentsOfFile:dest encoding:NSUTF8StringEncoding error:NULL];
    CHECK(out == nil);  // ICU's stack limit is exhausted: do not export unfiltered text.
    CHECK(![out containsString:@"SyntheticWithinBudgetContinuation"]);
    // At the budget, keep the same sanitized tail and complete-line boundary as before.
    FILE *f = fopen(source.fileSystemRepresentation, "wb");
    CHECK(f != NULL);
    if (f) {
        for (size_t i = 0; i < budget / 16 - 1; ++i) fputs("build: fixture!\n", f); // 16 bytes
        fputs("last: retained!\n", f);
        fclose(f);
    }
    ReportPrivacyCopyLog(source.fileSystemRepresentation, dest.fileSystemRepresentation, 256 * 1024);
    out = [NSString stringWithContentsOfFile:dest encoding:NSUTF8StringEncoding error:NULL];
    CHECK([out containsString:@"last: retained!"]);
    CHECK(![out hasPrefix:@"Log omitted:"]);
    CHECK([out lengthOfBytesUsingEncoding:NSUTF8StringEncoding] <= 256 * 1024);
    printf("log budget: %d checks, %d failures\n", checks, failures);
    return failures ? 1 : 0;
}

static int regex_error_checks(void) {
    NSString *body = [@"x" stringByPaddingToLength:512 * 1024 withString:@"x" startingAtIndex:0];
    NSString *input = [NSString stringWithFormat:@"\"%@\"", body];
    NSString *out = ReportPrivacyReplace(input, @"^\"(?:\\\\[\\s\\S]|[^\"\\\\])*(?:\"|$)", @"[secret]");
    CHECK(out == nil);
    printf("regex error: %d checks, %d failures\n", checks, failures);
    return failures ? 1 : 0;
}

int main(int argc, char **argv) {
    @autoreleasepool {
        if (argc < 2) return 2;
        if (argc > 2 && !strcmp(argv[2], "--budget")) return budget_checks(argv[1]);
        if (argc > 2 && !strcmp(argv[2], "--regex-error")) return regex_error_checks();
        main_thread = pthread_self();
        snprintf(profile, sizeof profile, "%s", argv[1]);
        mkdir(profile, 0755);
        G = calloc(1, 0x01000000);
        reset_guest_state();
        char stderr_path[1400]; snprintf(stderr_path, sizeof stderr_path, "%s/source.log", profile);
        FILE *source = fopen(stderr_path, "w"); fputs("build: log fixture\npassword=\"SyntheticSecret\"\n", source); fclose(source);
        if (!freopen(stderr_path, "a", stderr)) return 2;
        snprintf(shot_file, sizeof shot_file, "%s/source.png", profile);
        shot_at = host_ticks() / 1e9;
        FILE *shot = fopen(shot_file, "w"); fputs("synthetic image bytes", shot); fclose(shot);
        report_shot_ready(shot_file);
        if (argc > 2 && !strcmp(argv[2], "--exit")) {
            guest_enter(); report_save(0); guest_leave();
            free(G);
            return 0;
        }
        pthread_t sound;
        pthread_create(&sound, NULL, sound_main, NULL);
        while (atomic_load(&sound_writes) < 3) usleep(1000);
#if !REPORT_LOCK_BASELINE
        image_gate = dispatch_semaphore_create(0);
        atomic_store(&block_image, 1);
#endif
        guest_enter();
        uint64_t started = mono();
        report_save(0);
        double request_ms = (mono() - started) / 1e6;
#if !REPORT_LOCK_BASELINE
        CHECK(!*report_last_path());
        CHECK(!strcmp(status, "Saving report..."));
        int captured = snapshot_gets;
        for (int i = 0; i < 12; ++i) report_save(0);
        CHECK(snapshot_gets == captured);
#endif
        // Changes after the action returns must not leak into the queued snapshot.
        choice.value = 0; cat.value = 2;
        snprintf(user_note, sizeof user_note, "changed after snapshot");
        M32(GAMEFLOW_STATE) = 3; dev.pp[0] = 800;
        memset(secs, 0, sizeof secs);
        memset(notes, 0, sizeof notes);
        guest_leave();
        shot = fopen(shot_file, "w"); fputs("later menu image", shot); fclose(shot);
        report_shot_ready(shot_file);
        if (image_gate) dispatch_semaphore_signal(image_gate);
        pump_until(1);
        usleep(15000);
        atomic_store(&sound_stop, 1);
        pthread_join(sound, NULL);
        CHECK(sound_gap <= 200);
        CHECK(request_ms <= 200);
        CHECK(exports == 1);
        CHECK(note_count == 1);
        CHECK(!strcmp(status, [[NSString stringWithFormat:@"Report saved: %s", report_last_path()] UTF8String]));
        const char *dir = report_last_path();
        NSString *summary = Read(dir, @"report.txt"), *settings = Read(dir, @"settings.txt");
        CHECK([summary containsString:@"category:   Audio\n"]);
        CHECK([summary containsString:@"synthetic initial note"]);
        CHECK([summary containsString:@"racing / free roam (game-flow 6)"]);
        CHECK([summary containsString:@"back buffer 1920x1080"]);
        CHECK([summary containsString:@"last 60s:   fps mean 60.0"]);
        CHECK([settings containsString:@"graphics.fixture = X360 (app.txt) [after restart]"]);
        CHECK([Read(dir, @"events.txt") containsString:@"fixture event 511"]);
        CHECK([Read(dir, @"events.txt") containsString:@"diagnostic fixture event"]);
        CHECK([Read(dir, @"stats.txt") containsString:@"60.0"]);
        CHECK(![Read(dir, @"log.txt") containsString:@"SyntheticSecret"]);
        CHECK([Read(dir, @"log.txt") containsString:@"build: log fixture"]);
        CHECK([Read(dir, @"screenshot.png") isEqualToString:@"synthetic image bytes"]);
        printf("report lock: request %.2f ms, sound max gap %.2f ms, %d writes, export held lock %d; %d checks, %d failures\n",
            request_ms, sound_gap, atomic_load(&sound_writes), export_held, checks, failures);
        printf("bundle=%s\n", dir);
#if !REPORT_LOCK_BASELINE
        CHECK(export_held == 0);
        // A failed job must release the pending slot and retain the last completed bundle.
        char previous[1400], original_profile[1200];
        snprintf(previous, sizeof previous, "%s", dir);
        snprintf(original_profile, sizeof original_profile, "%s", profile);
        snprintf(profile, sizeof profile, "%s/not-a-directory", original_profile);
        FILE *block = fopen(profile, "w"); CHECK(block != NULL); if (block) fclose(block);
        guest_enter(); report_save(0); guest_leave();
        pump_until(2);
        CHECK(!strcmp(status, "Couldn't write the report"));
        CHECK(!strcmp(previous, report_last_path()));
        snprintf(profile, sizeof profile, "%s", original_profile);
        guest_enter(); reset_guest_state(); report_save(0); guest_leave();
        pump_until(3);
        CHECK(strstr(status, "Report saved:") == status);
        CHECK(strcmp(previous, report_last_path()));
        // The synchronous API still attaches crash diagnostics and omits screenshots.
        char attach[1400]; snprintf(attach, sizeof attach, "%s/diagnostic.txt", profile);
        FILE *a = fopen(attach, "w"); fputs("synthetic crash diagnostic\n", a); fclose(a);
        guest_enter();
        const char *auto_dir = report_write("Crash-freeze", "", "synthetic hang", attach);
        guest_leave();
        CHECK(*auto_dir);
        CHECK([Read(auto_dir, @"report.txt") containsString:@"reason:     synthetic hang"]);
        CHECK(![Read(auto_dir, @"report.txt") containsString:@"screenshot.png"]);
        CHECK([Read(auto_dir, @"crash.txt") containsString:@"synthetic crash diagnostic"]);
        printf("lifecycle: %d checks, %d failures\n", checks, failures);
#endif
        free(G);
        return failures ? 1 : 0;
    }
}
