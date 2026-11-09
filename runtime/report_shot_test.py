"""Report capture identity proof (no game/GPU): original 18 checks / 9 failures; fixed 18 / 0.
Compiles the production screenshot functions and a pinned original, including the old snapshot retain rule.
"""
import hashlib
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = r'''// ---------------- the screenshot: the frame when the menu opened ----------------
static volatile int shot_wanted;
static char shot_file[1100];
static double shot_at;
static pthread_mutex_t shot_lock = PTHREAD_MUTEX_INITIALIZER;
static CFDataRef shot_data;
// The PNG queue calls this after encoding, so retaining the image never reads a file under the guest lock.
void report_shot_ready(const char *path) {
    pthread_mutex_lock(&shot_lock);
    int ours = shot_file[0] && !strcmp(path, shot_file);
    pthread_mutex_unlock(&shot_lock);
    if (!ours) return;
    @autoreleasepool {
        NSData *data = [NSData dataWithContentsOfFile:[NSString stringWithUTF8String:path]];
        CFDataRef image = data.length ? CFRetain((__bridge CFDataRef)data) : NULL;
        pthread_mutex_lock(&shot_lock);
        CFDataRef old = shot_data;
        shot_data = image;
        pthread_mutex_unlock(&shot_lock);
        if (old) CFRelease(old);
    }
}
static const char *reports_dir(void) {
    static char dir[1100];
    if (!dir[0]) { snprintf(dir, sizeof dir, "%s/reports", userdata_dir()); mkdir(dir, 0755); }
    return dir;
}
void report_request_shot(void) { shot_wanted = 1; }
// render_present.m: the next back buffer is saved as <userdata>/reports/.menu_frame.png (encoded off the game thread).
int report_shot(char *path, size_t n) {
    if (!shot_wanted || path[0]) return 0;
    shot_wanted = 0;
    const char *root = reports_dir();
    pthread_mutex_lock(&shot_lock);
    snprintf(shot_file, sizeof shot_file, "%s/.menu_frame.png", root);
    snprintf(path, n, "%s", shot_file);
    shot_at = host_ticks() / 1e9;
    pthread_mutex_unlock(&shot_lock);
    return 1;
}

'''
ORIGINAL_SHA = '8776955e14a70f6eec90801aad40dc3baf3736e284c6fb556d78d74ab13d721b'

PRELUDE = r'''
#import <Cocoa/Cocoa.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
static char profile[1024];
static const char *userdata_dir(void) { return profile; }
static uint64_t host_ticks(void) {
    struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint64_t)t.tv_sec * 1000000000ull + t.tv_nsec;
}
'''
HARNESS = r'''
static int checks, failures;
#define CHECK(x) do { ++checks; if (!(x)) { ++failures; printf("FAIL %d: %s\n", __LINE__, #x); } } while (0)
static void publish(const char *path, const char *bytes) {
    FILE *f = fopen(path, "wb");
    if (f) { fputs(bytes, f); fclose(f); }
    report_shot_ready(path);
}
static int equal(CFDataRef data, const char *bytes) {
    int same = data && CFDataGetLength(data) == (CFIndex)strlen(bytes) &&
               !memcmp(CFDataGetBytePtr(data), bytes, strlen(bytes));
    if (data) CFRelease(data);
    return same;
}
static int absent(CFDataRef data) { if (data) CFRelease(data); return !data; }
int main(int argc, char **argv) {
    @autoreleasepool {
        if (argc != 2) return 2;
        snprintf(profile, sizeof profile, "%s", argv[1]); mkdir(profile, 0755);
        char first[1100] = "", second[1100] = "", stale[1100] = "", current[1100] = "";
        uint64_t a = request();
        CHECK(report_shot(first, sizeof first));
        publish(first, "previous report");
        CHECK(equal(take(a), "previous report"));
        uint64_t b = request();
        // Other diagnostic captures must not consume the request.
        char busy[1100] = "diagnostic.png";
        CHECK(!report_shot(busy, sizeof busy));
        CHECK(report_shot(second, sizeof second));
        CHECK(strcmp(first, second));
        NSString *next = [NSString stringWithUTF8String:second];
        dispatch_async(dispatch_get_global_queue(QOS_CLASS_DEFAULT, 0), ^{
            usleep(100000); publish(next.UTF8String, "this report");
        });
        uint64_t begin = host_ticks();
        CHECK(equal(take(b), "this report"));
        double waited = (host_ticks() - begin) / 1e6;
        CHECK(waited >= 50 && waited < 1500);
        // Drain the fake encoder before starting the next scenario.
        usleep(200000);
        CHECK(absent(take(a)));
        request(); CHECK(report_shot(stale, sizeof stale));
        uint64_t d = request(); CHECK(report_shot(current, sizeof current));
        publish(stale, "late older encoder");
        publish(current, "current encoder");
        CHECK(equal(take(d), "current encoder"));
        CHECK(access(current, F_OK) != 0);
        CHECK(access(stale, F_OK) != 0);
        uint64_t missing = request();
        begin = host_ticks();
        CHECK(absent(take(missing)));
        double timeout = (host_ticks() - begin) / 1e6;
        CHECK(timeout >= 1900 && timeout < 3000);
        char failed[1100] = "";
        uint64_t e = request(); CHECK(report_shot(failed, sizeof failed));
        // An encoder failure is terminal for this request; never fall back to cached bytes.
        report_shot_ready(failed);
        begin = host_ticks();
        CHECK(absent(take(e)));
        CHECK((host_ticks() - begin) / 1e6 < 200);
        printf("capture identity: %d checks / %d failures; delayed %.1f ms, timeout %.1f ms\n",
               checks, failures, waited, timeout);
        return failures ? 1 : 0;
    }
}
'''


def main():
    assert hashlib.sha256(ORIGINAL.encode()).hexdigest() == ORIGINAL_SHA
    source = (ROOT / 'runtime/report.m').read_text()
    start = source.index('// ---------------- the screenshot:')
    end = source.index('// ---------------- writing a report', start)
    current = source[start:end]
    old_adapter = '''
static uint64_t test_generation;
static uint64_t request(void) { report_request_shot(); return ++test_generation; }
static CFDataRef take(uint64_t generation) {
    (void)generation;
    CFDataRef result = NULL;
    pthread_mutex_lock(&shot_lock);
    if (shot_data && host_ticks() / 1e9 - shot_at < 600) result = CFRetain(shot_data);
    pthread_mutex_unlock(&shot_lock);
    return result;
}
'''
    adapter = '''
static uint64_t request(void) { report_request_shot(); return shot_generation; }
static CFDataRef take(uint64_t generation) { return report_wait_shot(generation); }
'''
    with tempfile.TemporaryDirectory(prefix='report-shot-') as tmp:
        work = Path(tmp)
        for original in (True, False):
            name = 'original' if original else 'fixed'
            src, binary = work / (name + '.m'), work / name
            src.write_text(PRELUDE + (ORIGINAL if original else current) + (old_adapter if original else adapter) + HARNESS)
            compiled = subprocess.run(['clang', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
                                       '-fsanitize=address,undefined', '-fobjc-arc', str(src),
                                       '-framework', 'Cocoa', '-o', str(binary)], capture_output=True, text=True, timeout=90)
            assert compiled.returncode == 0, compiled.stderr[-4000:]
            result = subprocess.run([str(binary), str(work / (name + '-profile'))], capture_output=True, text=True, timeout=10)
            print(name + ': ' + result.stdout.strip(), flush=True)
            assert result.returncode == int(original), result.stdout + result.stderr
            if original:
                assert 'equal(take(b), "this report")' in result.stdout, 'original cached-shot bug must fail'
            else:
                assert '18 checks / 0 failures' in result.stdout, result.stdout


if __name__ == '__main__':
    main()
