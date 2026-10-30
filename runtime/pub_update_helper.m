// Standalone delivery helper. No app or script from a release is ever executed here.
// Usage: pub_update_helper JOB_DIR vVERSION DMG_NAME CHECKSUM_NAME [EXPECTED_DMG_BYTES]
// JOB_DIR must be private (0700), without granting ACLs, and owned by the current user. Retry uses the same arguments;
// it discards previous downloads and detaches the job's previous mount. One helper per job.
// progress.json is atomically replaced: downloading -> verifying -> verified, or failed.
// cleanup_pending retains the device/mount for retry when detach cannot finish.
// A verified result retains a read-only mount for the next preparation step. The caller must
// detach mount_point when finished; setup_path is valid only for this mounted job, not a receipt
// authorizing later execution after the mount or files have changed. HTTPS + the release account
// are the trust root; the current ad-hoc signature proves integrity, not publisher identity.
#import <Foundation/Foundation.h>
#import <CommonCrypto/CommonDigest.h>
#include <sys/file.h>
#include <sys/acl.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <signal.h>
#include <unistd.h>
#include <errno.h>
#include "pub_update_rehearsal.h"
#include "pub_update_write.h"

static const uint64_t ChecksumCap = 4096;
#if defined(PUB_UPDATE_HELPER_TESTING)
static const uint64_t AssetCap = 1024 * 1024;
#else
static const uint64_t AssetCap = 4ULL * 1024 * 1024 * 1024;
#endif
static NSString *const SetupName = @"NFS Most Wanted Native Setup.app";
static int jobFD = -1;
static NSString *jobPath, *tag, *asset, *checksum, *attachedDevice;
static volatile sig_atomic_t cancelled;
static void Cancel(int signo) { cancelled = signo; }

static void Fail(NSString *message) {
    @throw [NSException exceptionWithName:@"UpdateFailure" reason:message userInfo:nil];
}
static void CheckCancelled(void) { if (cancelled) Fail(@"Update cancelled; retry starts a fresh download."); }
static BOOL PrivateJob(int fd) {
    struct stat st;
    if (fstat(fd, &st) || !S_ISDIR(st.st_mode) || st.st_uid != geteuid() || (st.st_mode & 077)) return NO;
    acl_t acl = acl_get_fd_np(fd, ACL_TYPE_EXTENDED);
    if (!acl) return errno == ENOENT;
    acl_entry_t entry;
    int status = acl_get_entry(acl, ACL_FIRST_ENTRY, &entry);
    BOOL safe = YES;
    while (status == 0) {
        acl_tag_t type;
        if (acl_get_tag_type(entry, &type) || type == ACL_EXTENDED_ALLOW) { safe = NO; break; }
        status = acl_get_entry(acl, ACL_NEXT_ENTRY, &entry);
    }
    // Darwin returns -1 at the end of an ACL (errno EINVAL).
    acl_free(acl);
    return safe;
}
static BOOL WriteAll(int fd, const void *bytes, size_t count) {
    const char *p = bytes;
    while (count) {
        ssize_t n = write(fd, p, count);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) return NO;
        p += n; count -= n;
    }
    return YES;
}
static void Progress(NSString *state, NSString *message, uint64_t received, uint64_t total, NSDictionary *extra) {
    NSMutableDictionary *d = [@{@"schema": @1, @"state": state, @"message": message,
        @"tag": tag ?: @"", @"asset": asset ?: @"", @"checksum_asset": checksum ?: @"",
        @"received_bytes": @(received), @"total_bytes": @(total)} mutableCopy];
    if (extra) [d addEntriesFromDictionary:extra];
    NSData *data = [NSJSONSerialization dataWithJSONObject:d options:NSJSONWritingPrettyPrinted error:nil];
    if (!PUReplaceFileAt(jobFD, [jobPath stringByAppendingPathComponent:@"progress.json"], data, 0600))
        Fail(@"Cannot save update progress.");
}
static BOOL Matches(NSString *s, NSString *pattern) {
    return [s rangeOfString:pattern options:NSRegularExpressionSearch].location != NSNotFound;
}
static BOOL PlainHTTPS(NSURL *u) {
    return [u.scheme isEqualToString:@"https"] && !u.user && !u.password && !u.fragment &&
        (!u.port || u.port.intValue == 443);
}
static NSURL *TestBase(void) {
#if defined(PUB_UPDATE_REHEARSAL)
    return PURBase();
#endif
#if defined(PUB_UPDATE_HELPER_TESTING)
    NSString *base = NSProcessInfo.processInfo.environment[@"PUB_UPDATE_HELPER_TEST_BASE_URL"];
    if (base) {
        NSURL *u = [NSURL URLWithString:base];
        if (![u.scheme isEqualToString:@"http"] || ![u.host isEqualToString:@"127.0.0.1"] ||
            !u.port || u.port.intValue <= 0 || u.port.intValue > 65535 || u.user || u.password || u.query || u.fragment ||
            (u.path.length && ![u.path isEqualToString:@"/"])) Fail(@"Invalid loopback fixture URL.");
        return u;
    }
#endif
    return nil;
}
static NSURL *AssetURL(NSString *name) {
    NSURL *base = TestBase();
    NSString *origin = base ? [NSString stringWithFormat:@"http://127.0.0.1:%@", base.port] : @"https://github.com";
    return [NSURL URLWithString:[NSString stringWithFormat:@"%@/elforeign/nfs-most-wanted-mac/releases/download/%@/%@", origin, tag, name]];
}
static BOOL AllowedRedirect(NSURL *u) {
    NSURL *base = TestBase();
    if (base) return [u.scheme isEqualToString:@"http"] && [u.host isEqualToString:@"127.0.0.1"] &&
        [u.port isEqual:base.port] && !u.user && !u.password && !u.fragment;
    return PlainHTTPS(u) && [u.host isEqualToString:@"release-assets.githubusercontent.com"];
}

@interface Fetch : NSObject <NSURLSessionDataDelegate>
@property int fd;
@property uint64_t cap, received, total, expected;
@property NSUInteger redirects;
@property NSString *failure;
@property NSString *label;
@property dispatch_semaphore_t done;
@property double lastProgress;
@property CC_SHA256_CTX digestContext;
@end
@implementation Fetch
- (void)URLSession:(NSURLSession *)session task:(NSURLSessionTask *)task
    willPerformHTTPRedirection:(NSHTTPURLResponse *)response newRequest:(NSURLRequest *)request
    completionHandler:(void (^)(NSURLRequest *))completion {
    (void)session; (void)task; (void)response;
    if (++_redirects > 3 || !AllowedRedirect(request.URL)) {
        _failure = @"Release redirect is outside the allowed delivery host or exceeds the redirect limit.";
        completion(nil);
    } else completion(request);
}
- (void)URLSession:(NSURLSession *)session dataTask:(NSURLSessionDataTask *)task
    didReceiveResponse:(NSURLResponse *)response completionHandler:(void (^)(NSURLSessionResponseDisposition))completion {
    (void)session; (void)task;
    if (_failure) { completion(NSURLSessionResponseCancel); return; }
    NSHTTPURLResponse *http = (NSHTTPURLResponse *)response;
    int64_t length = response.expectedContentLength;
    NSString *encoding = [http valueForHTTPHeaderField:@"Content-Encoding"];
    if (http.statusCode != 200 || (encoding && ![encoding.lowercaseString isEqualToString:@"identity"]))
        _failure = [NSString stringWithFormat:@"Release asset request failed (HTTP %ld; expected an unencoded HTTP 200 response).", (long)http.statusCode];
    else if (length > (int64_t)_cap || (_expected && length >= 0 && (uint64_t)length != _expected))
        _failure = @"Release asset size exceeds the limit or differs from the expected size.";
    _total = length >= 0 ? (uint64_t)length : _expected;
    completion(_failure ? NSURLSessionResponseCancel : NSURLSessionResponseAllow);
}
- (void)URLSession:(NSURLSession *)session dataTask:(NSURLSessionDataTask *)task didReceiveData:(NSData *)data {
    (void)session;
    if (_failure) return;
    if (data.length > _cap - _received) _failure = @"Release asset exceeds the download size limit.";
    else if (!WriteAll(_fd, data.bytes, data.length)) _failure = @"Cannot write release asset (check available disk space).";
    if (_failure) { [task cancel]; return; }
    CC_SHA256_CTX ctx = _digestContext;
    CC_SHA256_Update(&ctx, data.bytes, (CC_LONG)data.length);
    _digestContext = ctx;
    _received += data.length;
    double now = NSProcessInfo.processInfo.systemUptime;
    if (now - _lastProgress >= 0.25) {
        @try { Progress(@"downloading", _label, _received, _total, nil); }
        @catch (NSException *e) { _failure = e.reason; [task cancel]; }
        _lastProgress = now;
    }
}
- (void)URLSession:(NSURLSession *)session task:(NSURLSessionTask *)task didCompleteWithError:(NSError *)error {
    (void)session; (void)task;
    if (!_failure && (error || !_received || (_total && _received != _total) || (_expected && _received != _expected)))
        _failure = [NSString stringWithFormat:@"Release download was interrupted or incomplete (%@). Retry starts a fresh download.", error.localizedDescription ?: @"size mismatch"];
    dispatch_semaphore_signal(_done);
}
@end

static NSDictionary *Download(NSString *name, uint64_t cap, uint64_t expected) {
    NSString *part = [name stringByAppendingString:@".part"];
    int fd = openat(jobFD, part.fileSystemRepresentation, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
    if (fd < 0) Fail(@"Cannot create partial download in the update job.");
    Fetch *fetch = [Fetch new];
    fetch.fd = fd; fetch.cap = cap; fetch.expected = expected; fetch.label = name;
    fetch.done = dispatch_semaphore_create(0);
    CC_SHA256_CTX ctx; CC_SHA256_Init(&ctx); fetch.digestContext = ctx;
    NSURLSessionConfiguration *config = NSURLSessionConfiguration.ephemeralSessionConfiguration;
    config.URLCache = nil; config.URLCredentialStorage = nil; config.HTTPCookieStorage = nil;
    config.HTTPShouldSetCookies = NO; config.timeoutIntervalForRequest = 30; config.timeoutIntervalForResource = 1800;
    config.connectionProxyDictionary = @{};
    NSOperationQueue *queue = [NSOperationQueue new]; queue.maxConcurrentOperationCount = 1;
    queue.qualityOfService = NSQualityOfServiceUtility;
    NSURLSession *session = [NSURLSession sessionWithConfiguration:config delegate:fetch delegateQueue:queue];
    NSMutableURLRequest *request = [NSMutableURLRequest requestWithURL:AssetURL(name)];
    request.cachePolicy = NSURLRequestReloadIgnoringLocalCacheData;
    [request setValue:@"identity" forHTTPHeaderField:@"Accept-Encoding"];
    Progress(@"downloading", name, 0, expected, nil);
    NSURLSessionDataTask *task = [session dataTaskWithRequest:request];
    [task resume];
    while (dispatch_semaphore_wait(fetch.done, dispatch_time(DISPATCH_TIME_NOW, 100 * NSEC_PER_MSEC))) {
        if (cancelled) [task cancel];
    }
    [session finishTasksAndInvalidate];
    BOOL synced = fsync(fd) == 0; close(fd);
    CheckCancelled();
    if (fetch.failure) Fail(fetch.failure);
    if (!synced) Fail(@"Cannot sync the downloaded release asset.");
    unsigned char digest[CC_SHA256_DIGEST_LENGTH]; ctx = fetch.digestContext; CC_SHA256_Final(digest, &ctx);
    NSMutableString *hex = [NSMutableString new];
    for (unsigned i = 0; i < sizeof digest; ++i) [hex appendFormat:@"%02x", digest[i]];
    return @{@"sha256": hex, @"bytes": @(fetch.received)};
}
static NSString *ExpectedHash(void) {
    NSString *path = [jobPath stringByAppendingPathComponent:[checksum stringByAppendingString:@".part"]];
    NSData *bytes = [NSData dataWithContentsOfFile:path];
    NSString *text = [[NSString alloc] initWithData:bytes encoding:NSUTF8StringEncoding];
    // Preserve shasum -a 256's existing single-entry format. No paths, duplicates or alternate entries.
    NSString *pattern = [NSString stringWithFormat:@"\\A([0-9a-fA-F]{64})  %@\\n?\\z", [NSRegularExpression escapedPatternForString:asset]];
    NSRegularExpression *re = [NSRegularExpression regularExpressionWithPattern:pattern options:0 error:nil];
    NSTextCheckingResult *m = text ? [re firstMatchInString:text options:0 range:NSMakeRange(0, text.length)] : nil;
    if (!m) Fail(@"Release checksum has no unique, valid entry for the expected DMG.");
    return [[text substringWithRange:[m rangeAtIndex:1]] lowercaseString];
}
static BOOL RunToolOutput(NSString *tool, NSArray<NSString *> *arguments, NSString *output) {
    int fd = openat(jobFD, "helper.log", O_WRONLY | O_APPEND | O_CREAT | O_NOFOLLOW | O_CLOEXEC, 0600);
    if (fd < 0) Fail(@"Cannot open update log.");
    NSFileHandle *log = [[NSFileHandle alloc] initWithFileDescriptor:fd closeOnDealloc:YES];
    NSTask *task = [NSTask new]; task.executableURL = [NSURL fileURLWithPath:tool]; task.arguments = arguments;
    task.currentDirectoryURL = [NSURL fileURLWithPath:jobPath];
    task.environment = @{@"PATH": @"/usr/bin:/bin:/usr/sbin:/sbin", @"LANG": @"C"};
    NSFileHandle *out = log;
    if (output) {
        unlinkat(jobFD, output.fileSystemRepresentation, 0);
        int outFD = openat(jobFD, output.fileSystemRepresentation, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
        if (outFD < 0) Fail(@"Cannot create image device record.");
        out = [[NSFileHandle alloc] initWithFileDescriptor:outFD closeOnDealloc:YES];
    }
    task.standardInput = [NSFileHandle fileHandleWithNullDevice]; task.standardOutput = out; task.standardError = log;
    if (![task launchAndReturnError:nil]) return NO;
    double deadline = NSProcessInfo.processInfo.systemUptime + 120;
    while (task.running && NSProcessInfo.processInfo.systemUptime < deadline) [NSThread sleepForTimeInterval:0.05];
    if (task.running) {
        [task terminate]; [NSThread sleepForTimeInterval:0.2];
        if (task.running) kill(task.processIdentifier, SIGKILL);
        [task waitUntilExit]; return NO;
    }
    [task waitUntilExit];
    if (output && fsync(out.fileDescriptor)) return NO;
    return task.terminationStatus == 0;
}
static BOOL RunTool(NSString *tool, NSArray<NSString *> *arguments) {
    return RunToolOutput(tool, arguments, nil);
}
// Query live image identity instead of trusting a stale /dev/disk number on retry.
static BOOL CleanupImage(NSString *mount) {
    if (!RunToolOutput(@"/usr/bin/hdiutil", @[@"info", @"-plist"], @"images.plist")) return NO;
    NSDictionary *info = [NSDictionary dictionaryWithContentsOfFile:[jobPath stringByAppendingPathComponent:@"images.plist"]];
    if (![info[@"images"] isKindOfClass:NSArray.class]) return NO;
    NSString *dmg = [jobPath stringByAppendingPathComponent:asset];
    for (NSDictionary *image in info[@"images"]) {
        if (![image[@"image-path"] isEqual:dmg]) continue;
        NSArray *entities = image[@"system-entities"];
        NSString *device = nil;
        for (NSDictionary *entity in entities) {
            NSString *candidate = entity[@"dev-entry"];
            if ([candidate isKindOfClass:NSString.class] && Matches(candidate, @"\\A/dev/disk[0-9]+(?:s[0-9]+)*\\z")) {
                if (!device) device = candidate;
                if (Matches(candidate, @"\\A/dev/disk[0-9]+\\z")) { device = candidate; break; }
            }
        }
        if (!device) return NO;
        attachedDevice = device;
        // A full disk or failed receipt write must not prevent the detach attempt.
        @try {
            Progress(@"cleanup_pending", @"Detaching the update image.", 0, 0,
                     @{@"mount_point": mount, @"attached_device": device});
        } @catch (NSException *ignored) { (void)ignored; }
        if (!RunTool(@"/usr/bin/hdiutil", @[@"detach", device])) return NO;
        attachedDevice = nil;
    }
    return YES;
}
static BOOL IsJobMount(NSString *path) {
    struct statfs fs;
    return !statfs(path.fileSystemRepresentation, &fs) && !strcmp(fs.f_mntonname, path.fileSystemRepresentation);
}
static void ClearPrevious(NSString *mount) {
    if (!CleanupImage(mount))
        Fail(@"Cannot detach the previous update image. Close files on it and retry.");
    struct stat st;
    if (!fstatat(jobFD, "mount", &st, AT_SYMLINK_NOFOLLOW)) {
        if (!S_ISDIR(st.st_mode) || rmdir(mount.fileSystemRepresentation)) Fail(@"Update mount directory is not empty or safe.");
    } else if (errno != ENOENT) Fail(@"Cannot inspect the update mount directory.");
    for (NSString *name in @[asset, checksum, [asset stringByAppendingString:@".part"], [checksum stringByAppendingString:@".part"]]) {
        if (unlinkat(jobFD, name.fileSystemRepresentation, 0) && errno != ENOENT) Fail(@"Cannot clear the previous download.");
    }
}
static NSDictionary *VerifyImage(NSString *mount, NSString *version) {
    if (mkdirat(jobFD, "mount", 0700)) Fail(@"Cannot create the update mount directory.");
    NSString *dmg = [jobPath stringByAppendingPathComponent:asset];
    Progress(@"cleanup_pending", @"Attaching the update image; retry will finish cleanup if interrupted.", 0, 0, @{@"mount_point": mount});
    if (!RunToolOutput(@"/usr/bin/hdiutil", @[@"attach", @"-readonly", @"-nobrowse", @"-noautoopen", @"-mountpoint", mount, @"-plist", dmg], @"attach.plist"))
        Fail(@"Cannot attach the downloaded disk image read-only. See helper.log.");
    struct statfs fs;
    if (!IsJobMount(mount) || statfs(mount.fileSystemRepresentation, &fs) || !(fs.f_flags & MNT_RDONLY))
        Fail(@"The update image is not mounted read-only at the expected location.");
    attachedDevice = [NSString stringWithUTF8String:fs.f_mntfromname];
    Progress(@"verifying", @"Checking Setup's identity and signature.", 0, 0,
             @{@"mount_point": mount, @"attached_device": attachedDevice});
    CheckCancelled();
    NSString *setup = [mount stringByAppendingPathComponent:SetupName];
    NSString *contents = [setup stringByAppendingPathComponent:@"Contents"];
    NSString *plist = [contents stringByAppendingPathComponent:@"Info.plist"];
    NSString *macos = [contents stringByAppendingPathComponent:@"MacOS"];
    NSString *exe = [macos stringByAppendingPathComponent:@"Setup"];
    // Reject links at each bundle boundary before handing the bundle to any system tool.
    for (NSString *p in @[setup, contents, macos, plist, exe]) {
        struct stat st;
        BOOL file = [p isEqual:plist] || [p isEqual:exe];
        if (lstat(p.fileSystemRepresentation, &st) || (file ? !S_ISREG(st.st_mode) : !S_ISDIR(st.st_mode)) ||
            (file && st.st_size > 32 * 1024 * 1024) || ([p isEqual:exe] && !(st.st_mode & 0111)))
            Fail(@"The update does not contain a safe Setup bundle.");
    }
    NSDictionary *info = [NSDictionary dictionaryWithContentsOfFile:plist];
    if (![info[@"CFBundleIdentifier"] isEqual:@"org.nfsmw-native.setup"] ||
        ![info[@"CFBundleExecutable"] isEqual:@"Setup"] || ![info[@"CFBundlePackageType"] isEqual:@"APPL"] ||
        ![info[@"CFBundleShortVersionString"] isEqual:version] || ![info[@"CFBundleVersion"] isEqual:version])
        Fail(@"Setup's bundle identity or version does not match the requested release.");
    if (!RunTool(@"/usr/bin/codesign", @[@"--verify", @"--deep", @"--strict", setup]))
        Fail(@"Setup's code signature is missing or invalid. See helper.log.");
    CheckCancelled();
    return @{@"dmg_path": dmg, @"mount_point": mount, @"attached_device": attachedDevice, @"setup_path": setup};
}
int main(int argc, const char **argv) {
    @autoreleasepool {
        NSThread.currentThread.qualityOfService = NSQualityOfServiceUtility;
        if (argc != 5 && argc != 6) { fprintf(stderr, "usage: pub_update_helper JOB_DIR vVERSION DMG CHECKSUM [EXPECTED_DMG_BYTES]\n"); return 2; }
        struct sigaction action = {0}; action.sa_handler = Cancel; sigemptyset(&action.sa_mask);
        sigaction(SIGTERM, &action, NULL); sigaction(SIGINT, &action, NULL);
        int lockFD = -1; BOOL ownsLock = NO; NSString *mount;
        @try {
            jobFD = open(argv[1], O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
            char resolved[PATH_MAX];
            if (jobFD < 0 || !PrivateJob(jobFD))
                Fail(@"Update job must be a private directory owned by the current user without granting ACLs.");
            if (fcntl(jobFD, F_GETPATH, resolved)) Fail(@"Cannot resolve the open update job directory.");
            jobPath = [NSString stringWithUTF8String:resolved];
#ifdef PUB_UPDATE_REHEARSAL
            (void)PURChildEnvironment(@{}); PURInside(jobPath);
#endif
            lockFD = openat(jobFD, "helper.lock", O_WRONLY | O_CREAT | O_NOFOLLOW | O_CLOEXEC, 0600);
            if (lockFD < 0 || flock(lockFD, LOCK_EX | LOCK_NB)) Fail(@"Another update helper is using this job.");
            ownsLock = YES;
            tag = [NSString stringWithUTF8String:argv[2]]; asset = [NSString stringWithUTF8String:argv[3]];
            checksum = [NSString stringWithUTF8String:argv[4]];
            if (tag.length > 64 || !Matches(tag, @"\\Av[0-9]+(?:\\.[0-9]+){1,3}\\z")) Fail(@"Invalid release tag (expected v followed by a numeric version).");
            NSString *version = [tag substringFromIndex:1];
            NSString *wanted = [NSString stringWithFormat:@"NFS-Most-Wanted-Native-Setup-v%@.dmg", version];
            if (![asset isEqual:wanted] || ![checksum isEqual:[wanted stringByAppendingString:@".sha256"]])
                Fail(@"Asset names do not match the requested release's installer and checksum pair.");
            uint64_t size = 0;
            if (argc == 6) {
                NSString *s = [NSString stringWithUTF8String:argv[5]];
                if (s.length > 12 || !Matches(s, @"\\A[1-9][0-9]*\\z") || (size = s.longLongValue) > AssetCap)
                    Fail(@"Invalid expected DMG size.");
            }
            mount = [jobPath stringByAppendingPathComponent:@"mount"];
            ClearPrevious(mount);
            CheckCancelled();
            Progress(@"downloading", @"Starting a fresh update download.", 0, 0, nil);
            Download(checksum, ChecksumCap, 0);
            NSString *hash = ExpectedHash();
            NSDictionary *download = Download(asset, AssetCap, size);
            Progress(@"verifying", @"Verifying the release checksum and Setup bundle.", [download[@"bytes"] unsignedLongLongValue], size, nil);
            if (![hash isEqualToString:download[@"sha256"]]) Fail(@"The downloaded DMG does not match the release SHA256 checksum.");
            if (renameat(jobFD, [[asset stringByAppendingString:@".part"] fileSystemRepresentation], jobFD, asset.fileSystemRepresentation) ||
                renameat(jobFD, [[checksum stringByAppendingString:@".part"] fileSystemRepresentation], jobFD, checksum.fileSystemRepresentation) || fsync(jobFD))
                Fail(@"Cannot publish the complete release download.");
            NSMutableDictionary *result = [VerifyImage(mount, version) mutableCopy];
            result[@"sha256"] = hash; result[@"dmg_bytes"] = download[@"bytes"];
            Progress(@"verified", @"Release download and Setup signature verified.", [download[@"bytes"] unsignedLongLongValue], [download[@"bytes"] unsignedLongLongValue], result);
            close(lockFD); close(jobFD); return 0;
        } @catch (NSException *e) {
            // A competing invocation must never overwrite the active helper's progress.
            if (ownsLock) {
                BOOL cleaned = mount == nil;
                @try { if (mount) cleaned = CleanupImage(mount); }
                @catch (NSException *ignored) { (void)ignored; }
                @try {
                    NSDictionary *extra = cleaned ? nil : @{@"mount_point": mount ?: @"", @"attached_device": attachedDevice ?: @""};
                    Progress(cleaned ? @"failed" : @"cleanup_pending",
                             cleaned ? (e.reason ?: @"Update verification failed.") : @"Cannot detach the update image. Close files on it and retry to finish cleanup.", 0, 0, extra);
                } @catch (NSException *ignored) { (void)ignored; }
            }
            fprintf(stderr, "%s\n", e.reason.UTF8String);
            if (lockFD >= 0) close(lockFD);
            if (jobFD >= 0) close(jobFD);
            return 1;
        }
    }
}
