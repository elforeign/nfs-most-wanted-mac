// Fake CLI/task/clock completions only; no installed CLI or network.
// Proof: fixed 207 checks/0 failures; pinned predecessor 188/3; 16 broken controls rejected.
#import <Foundation/Foundation.h>
#include "report_submit.m"
static int checks, failures, calls, creations;
static NSMutableArray *pending, *arguments, *inputs;
#define CHECK(...) do { ++checks; if (!(__VA_ARGS__)) { ++failures; fprintf(stderr, "FAIL %d: %s\n", __LINE__, #__VA_ARGS__); } } while (0)
static ReportTaskRunner FakeRunner(void) {
    return ^(NSArray *args, NSData *input, double seconds, ReportTaskDone done) {
        ++calls; if ([args containsObject:@"POST"]) ++creations;
        CHECK(seconds > 0 && seconds <= 30);
        CHECK([args containsObject:@"github.com"]);
        [arguments addObject:args]; [inputs addObject:input ?: NSData.data]; [pending addObject:[done copy]];
    };
}
static void Drain(void) {
    // Pump the same main queue used by production callbacks; no elapsed-time assertion.
    for (int i = 0; i < 10; ++i) [NSRunLoop.currentRunLoop runUntilDate:[NSDate dateWithTimeIntervalSinceNow:0.001]];
}
static void Finish(int status, NSString *text, BOOL unknown) {
    CHECK(pending.count > 0); if (!pending.count) return;
    ReportTaskDone done = pending.firstObject; [pending removeObjectAtIndex:0];
    done(status, [text dataUsingEncoding:NSUTF8StringEncoding], unknown); Drain();
}
static ReportSubmission *NewSession(void) {
    return [[ReportSubmission alloc] initWithSnapshot:@{@"title":@"[Audio] Problem report", @"body":@"Category: Audio\nPlayer note: café 🎮 &?#+\nBuild: test"} runner:FakeRunner()];
}
static void Ready(ReportSubmission *s) { [s checkAccount]; Finish(0, @"sample-account\n", NO); CHECK(s.state == ReportSubmitReady); }
// The real adapter runs against pipes, a fake task and a deterministic monotonic clock.
@interface ReportFakeTask : NSObject
@property NSURL *executableURL, *currentDirectoryURL;
@property NSArray *arguments;
@property NSDictionary *environment;
@property NSPipe *standardInput, *standardOutput;
@property id standardError;
@property int terminationStatus, processIdentifier, reader;
@property BOOL live, launchOK, hang, accountMode;
@property NSData *fakeOutput;
@property NSMutableData *received;
- (BOOL)launchAndReturnError:(NSError **)error;
- (BOOL)isRunning;
- (BOOL)running;
- (void)terminate;
@end
static NSString *activeCredential, *postedCredential;
static int credentialReads;
@implementation ReportFakeTask
- (BOOL)launchAndReturnError:(NSError **)error {
    (void)error; if (!self.launchOK) return NO;
    if (self.accountMode) {
        NSString *value;
        if ([self.arguments containsObject:@"token"]) { ++credentialReads; value = activeCredential; }
        else if ([self.arguments containsObject:@"POST"]) {
            postedCredential = self.environment[@"GH_TOKEN"] ?: activeCredential;
            value = @"https://github.com/elforeign/nfs-most-wanted-mac/issues/42";
        } else value = [(self.environment[@"GH_TOKEN"] ?: activeCredential) isEqual:@"synthetic_A"] ? @"account-A" : @"account-B";
        self.fakeOutput = [value dataUsingEncoding:NSUTF8StringEncoding];
    }
    self.reader = dup(self.standardInput.fileHandleForReading.fileDescriptor);
    fcntl(self.reader, F_SETFL, O_NONBLOCK); self.received = NSMutableData.data;
    if (self.fakeOutput.length) CHECK(write(self.standardOutput.fileHandleForWriting.fileDescriptor, self.fakeOutput.bytes, self.fakeOutput.length) == (ssize_t)self.fakeOutput.length);
    self.live = YES; return YES;
}
- (BOOL)isRunning { return [self running]; }
- (BOOL)running {
    char bytes[1024]; ssize_t n;
    while ((n = read(self.reader, bytes, sizeof bytes)) > 0) [self.received appendBytes:bytes length:(NSUInteger)n];
    return self.live && self.hang;
}
- (void)terminate { self.live = NO; self.terminationStatus = 143; }
- (void)dealloc { if (_reader > 0) close(_reader); }
@end
static void AdapterCase(BOOL present, BOOL launch, BOOL hang, NSUInteger size, NSData *input) {
    ReportFakeTask *task = [ReportFakeTask new]; task.launchOK = launch; task.hang = hang;
    task.fakeOutput = [[@"x" stringByPaddingToLength:size withString:@"x" startingAtIndex:0] dataUsingEncoding:NSUTF8StringEncoding];
    __block double time = 0; __block int code = -99; __block BOOL unknown = NO; __block NSData *result;
    ReportRawTaskRunner runner = ReportRawRunner(^{ return present ? @"/fake/gh" : nil; }, ^{ return (NSTask *)(id)task; },
        ^{ return time; }, ^(double seconds) { time += seconds; });
    dispatch_semaphore_t finished = dispatch_semaphore_create(0);
    runner(@[@"api", @"--hostname", @"github.com", @"user", @"--jq", @".login"], input, 1, ReportChildEnvironment(),
        ^(int status, NSData *data, BOOL uncertain) { code = status; result = data; unknown = uncertain; dispatch_semaphore_signal(finished); });
    CHECK(dispatch_semaphore_wait(finished, dispatch_time(DISPATCH_TIME_NOW, 10 * NSEC_PER_SEC)) == 0);
    if (!present || !launch || input.length > 65536) CHECK(code == -2 && !unknown && !result);
    else {
        CHECK([task.environment isEqual:ReportChildEnvironment()] && [task.currentDirectoryURL.path isEqual:@"/"]);
        CHECK([task.arguments containsObject:@"github.com"] && [task.standardError isKindOfClass:NSFileHandle.class]);
        CHECK([task.received isEqual:input ?: NSData.data] || hang);
        if (hang || size > 4096) CHECK(unknown && code == -1 && result.length <= 4096 && time < 1.3);
        else CHECK(code == 0 && !unknown && result.length == size);
    }
}
static NSString *PinnedCall(ReportTaskRunner runner, NSArray *args) {
    __block NSString *response;
    dispatch_semaphore_t finished = dispatch_semaphore_create(0);
    runner(args, nil, 15, ^(int status, NSData *data, BOOL uncertain) {
        CHECK(status == 0 && !uncertain);
        response = [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding];
        dispatch_semaphore_signal(finished);
    });
    CHECK(dispatch_semaphore_wait(finished, dispatch_time(DISPATCH_TIME_NOW, 10 * NSEC_PER_SEC)) == 0);
    return response;
}
static void AccountSwitchTests(void) {
    activeCredential = @"synthetic_A"; postedCredential = nil; credentialReads = 0;
    ReportTaskRunner runner = ReportRunner(^{ return @"/fake/gh"; }, ^{
        ReportFakeTask *task = [ReportFakeTask new]; task.launchOK = YES; task.accountMode = YES;
        return (NSTask *)(id)task;
    }, ^{ return ReportMonotonic(); }, ^(double seconds) { [NSThread sleepForTimeInterval:seconds]; });
    NSArray *identity = @[@"api", @"--hostname", @"github.com", @"user", @"--jq", @".login"];
    CHECK([PinnedCall(runner, identity) isEqual:@"account-A"]);
    CHECK([PinnedCall(runner, identity) isEqual:@"account-A"]);
    // Switch after revalidation has returned, before the separately launched POST reads credentials.
    activeCredential = @"synthetic_B";
    CHECK([PinnedCall(runner, @[@"api", @"--hostname", @"github.com", @"--method", @"POST",
        @"repos/elforeign/nfs-most-wanted-mac/issues", @"--input", @"-"]) hasSuffix:@"/42"]);
    CHECK([postedCredential isEqual:@"synthetic_A"]);
    CHECK(credentialReads == 1);
    CHECK([PinnedCall(runner, identity) isEqual:@"account-A"]);
}
static void CredentialFailureTests(void) {
    for (int mode = 0; mode < 5; ++mode) {
        __block int tasks = 0;
        ReportTaskRunner runner = ReportRunner(^{ return @"/fake/gh"; }, ^{
            ++tasks;
            ReportFakeTask *task = [ReportFakeTask new]; task.launchOK = mode != 0;
            NSString *text = mode == 1 ? @"" : mode == 2 ? @"bad token" : mode == 3 ?
                [@"x" stringByPaddingToLength:5000 withString:@"x" startingAtIndex:0] : @"synthetic_A";
            task.fakeOutput = [text dataUsingEncoding:NSUTF8StringEncoding];
            task.terminationStatus = mode == 4 ? 1 : 0;
            return (NSTask *)(id)task;
        }, ^{ return ReportMonotonic(); }, ^(double seconds) { [NSThread sleepForTimeInterval:seconds]; });
        dispatch_semaphore_t finished = dispatch_semaphore_create(0);
        runner(@[@"api", @"user"], nil, 15, ^(int status, NSData *output, BOOL uncertain) {
            CHECK(status == -2 && !output && !uncertain);
            dispatch_semaphore_signal(finished);
        });
        CHECK(dispatch_semaphore_wait(finished, dispatch_time(DISPATCH_TIME_NOW, 10 * NSEC_PER_SEC)) == 0);
        CHECK(tasks == 1); // Never fall back to an unpinned API call.
    }
}
static void AdapterTests(void) {
    AdapterCase(NO, NO, NO, 0, nil);
    AdapterCase(YES, NO, NO, 0, nil);
    AdapterCase(YES, YES, NO, 30, [@"stdin body &? café 🎮" dataUsingEncoding:NSUTF8StringEncoding]);
    AdapterCase(YES, YES, NO, 5000, nil);
    AdapterCase(YES, YES, YES, 0, nil);
    AdapterCase(YES, YES, NO, 30, [NSMutableData dataWithLength:1048576]);
}
static void TransportTests(void) {
    NSDictionary *env = ReportChildEnvironment();
    for (NSString *key in @[@"GH_TOKEN", @"GITHUB_TOKEN", @"GH_CONFIG_DIR", @"GH_REPO", @"GITHUB_HOST", @"DYLD_INSERT_LIBRARIES", @"XDG_CONFIG_HOME", @"GIT_CONFIG_GLOBAL"])
        CHECK(!env[key]);
    CHECK([env[@"GH_PROMPT_DISABLED"] isEqual:@"1"] && [env[@"GH_HOST"] isEqual:@"github.com"]);
    CHECK([env[@"PATH"] isEqual:@"/usr/bin:/bin"] && env.count == 11 && [env[@"GH_NO_UPDATE_NOTIFIER"] isEqual:@"1"] && [env[@"GH_NO_EXTENSION_UPDATE_NOTIFIER"] isEqual:@"1"]);
    CHECK(!ReportSafeExecutable(@"/tmp/gh") && !ReportSafeExecutable(@"relative-gh"));
    CHECK(!ReportAutomaticAttachmentsSupported());
    CHECK(ReportValidIssueURL(@"https://github.com/elforeign/nfs-most-wanted-mac/issues/12"));
    for (NSString *bad in @[@"http://github.com/elforeign/nfs-most-wanted-mac/issues/1", @"https://github.com/other/repo/issues/1",
        @"https://github.com.evil.invalid/elforeign/nfs-most-wanted-mac/issues/1", @"https://github.com/elforeign/nfs-most-wanted-mac/issues/0",
        @"https://github.com/elforeign/nfs-most-wanted-mac/issues/1?secret=x", @"https://github.com/elforeign/nfs-most-wanted-mac/issues/1\n/path"])
        CHECK(!ReportValidIssueURL(bad));
}
static void StateTests(void) {
    ReportSubmission *s = NewSession();
    CHECK(s.state == ReportSubmitIdle && calls == 0); [s post]; CHECK(calls == 0);
    [s checkAccount]; [s checkAccount]; [s post]; CHECK(calls == 1 && creations == 0);
    CHECK(([arguments.lastObject isEqual:@[@"api", @"--hostname", @"github.com", @"user", @"--jq", @".login"]]));
    Finish(-2, @"token=do-not-display /Volumes/Private/path", NO);
    CHECK(s.state == ReportSubmitFailed && !s.account && ![s.message containsString:@"do-not-display"] && ![s.message containsString:@"/Volumes"]);
    [s checkAccount]; Finish(1, @"signed-out@example.invalid", NO); CHECK(s.state == ReportSubmitFailed);
    [s checkAccount]; Finish(0, @"bad/login", NO); CHECK(s.state == ReportSubmitFailed);
    [s checkAccount]; [s forgetAccount]; Finish(0, @"late-account", NO); CHECK(s.state == ReportSubmitIdle && !s.account);
    Ready(s); int before = creations;
    [s post]; [s post]; [s checkAccount]; CHECK(creations == before && s.state == ReportSubmitPosting);
    Finish(0, @"different-account", NO); CHECK(s.state == ReportSubmitFailed && creations == before);
    Ready(s); [s post]; ReportTaskDone repeatedIdentity = pending.firstObject; Finish(0, @"sample-account", NO);
    CHECK(repeatedIdentity != nil); if (repeatedIdentity) repeatedIdentity(0, [@"sample-account" dataUsingEncoding:NSUTF8StringEncoding], NO); Drain();
    CHECK(creations == before + 1); [s post]; CHECK(creations == before + 1);
    NSArray *args = arguments.lastObject;
    CHECK([args containsObject:@"repos/elforeign/nfs-most-wanted-mac/issues"] && [args containsObject:@"--input"] && [args containsObject:@"-"]);
    CHECK(![args containsObject:s.snapshot[@"body"]]);
    NSDictionary *payload = [NSJSONSerialization JSONObjectWithData:inputs.lastObject options:0 error:NULL];
    CHECK([payload[@"body"] isEqual:s.snapshot[@"body"]] && [payload[@"title"] isEqual:s.snapshot[@"title"]]);
    Finish(0, @"https://github.com/elforeign/nfs-most-wanted-mac/issues/42\n", NO);
    CHECK(s.state == ReportSubmitCreated && s.issueURL.length && [s.message containsString:@"manual attachment"]);
    // Partial attachment support must never reset the creation state or permit duplicate creation.
    CHECK(!ReportAutomaticAttachmentsSupported()); [s post]; [s checkAccount]; [s forgetAccount]; CHECK(creations == before + 1 && s.state == ReportSubmitCreated);
    for (NSString *response in @[@"", @"https://github.com/wrong/repo/issues/1", @"https://github.com/elforeign/nfs-most-wanted-mac/issues/7?token=secret"]) {
        s = NewSession(); Ready(s); [s post]; Finish(0, @"sample-account", NO); Finish(1, response, YES);
        CHECK(s.state == ReportSubmitUnknown && !s.issueURL && ![s.message containsString:@"secret"]);
        int count = calls; [s post]; [s checkAccount]; CHECK(calls == count);
    }
    s = NewSession(); Ready(s); [s post]; Finish(0, @"sample-account", NO);
    ReportTaskDone late = pending.firstObject; Finish(-1, @"", YES); int count = calls;
    CHECK(late != nil); if (late) late(0, [@"https://github.com/elforeign/nfs-most-wanted-mac/issues/99" dataUsingEncoding:NSUTF8StringEncoding], NO); Drain();
    CHECK(s.state == ReportSubmitUnknown && !s.issueURL && calls == count);
    s = NewSession(); setenv("BOOT_KEYS", "1", 1); [s checkAccount]; [s post]; CHECK(calls == count); unsetenv("BOOT_KEYS");
    CHECK(pending.count == 0);
    s = NewSession(); Ready(s); count = calls; setenv("BOOT_KEYS", "1", 1); [s post]; CHECK(calls == count); unsetenv("BOOT_KEYS");
    for (NSNumber *unknown in @[@NO, @YES]) {
        s = NewSession(); Ready(s); [s post]; Finish(0, @"sample-account", NO);
        Finish(unknown.boolValue ? 0 : 1, @"https://github.com/elforeign/nfs-most-wanted-mac/issues/123", unknown.boolValue);
        CHECK(s.state == ReportSubmitUnknown && s.issueURL.length); count = calls; [s post]; [s checkAccount]; CHECK(calls == count);
    }
    NSMutableString *mutableBody = [@"frozen reviewed text" mutableCopy];
    s = [[ReportSubmission alloc] initWithSnapshot:@{@"title":@"test", @"body":mutableBody} runner:FakeRunner()];
    [mutableBody setString:@"unreviewed replacement"]; CHECK([s.snapshot[@"body"] isEqual:@"frozen reviewed text"]);
}
int main(void) { @autoreleasepool {
    pending = NSMutableArray.array; arguments = NSMutableArray.array; inputs = NSMutableArray.array;
    AdapterTests(); AccountSwitchTests(); CredentialFailureTests(); TransportTests(); StateTests();
    printf("report submit: %d checks, %d failures; fake creations %d\n", checks, failures, creations);
    return failures ? 1 : 0;
} }
