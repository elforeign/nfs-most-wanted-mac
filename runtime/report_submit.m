#if defined(NFS_PUBLIC) && NFS_PUBLIC
#import "report_submit.h"
#include <sys/stat.h>
#include <unistd.h>
#include <fcntl.h>
#include <signal.h>
#include <errno.h>
#include <limits.h>

// No PATH lookup or user-selected executable/configuration. Symlinks must resolve into a trusted prefix.
static BOOL ReportSafeExecutable(NSString *path) {
    char resolved[PATH_MAX];
    if (!realpath(path.fileSystemRepresentation, resolved)) return NO;
    NSString *p = [NSString stringWithUTF8String:resolved];
    if (![p.lastPathComponent isEqual:@"gh"]) return NO;
    if (!([p hasPrefix:@"/opt/homebrew/"] || [p hasPrefix:@"/usr/local/"] || [p hasPrefix:@"/usr/bin/"])) return NO;
    struct stat info;
    if (stat(resolved, &info) || !S_ISREG(info.st_mode) || access(resolved, X_OK)) return NO;
    while (p.length > 1) {
        if (lstat(p.fileSystemRepresentation, &info) || (info.st_mode & 0022) ||
            (info.st_uid != 0 && info.st_uid != getuid())) return NO;
        p = p.stringByDeletingLastPathComponent;
    }
    return YES;
}
static NSString *ReportExecutable(void) {
    for (NSString *path in @[@"/opt/homebrew/bin/gh", @"/usr/local/bin/gh", @"/usr/bin/gh"])
        if (ReportSafeExecutable(path)) return path.stringByResolvingSymlinksInPath;
    return nil;
}
static NSDictionary *ReportChildEnvironment(void) {
    return @{@"HOME":NSHomeDirectory(), @"PATH":@"/usr/bin:/bin", @"LANG":@"en_US.UTF-8",
        @"GH_HOST":@"github.com", @"GH_PROMPT_DISABLED":@"1",
        @"GH_NO_UPDATE_NOTIFIER":@"1", @"GH_NO_EXTENSION_UPDATE_NOTIFIER":@"1", @"GIT_TERMINAL_PROMPT":@"0",
        @"GH_BROWSER":@"/usr/bin/false", @"GH_PAGER":@"/bin/cat", @"NO_COLOR":@"1"};
}
static double ReportMonotonic(void) { return NSProcessInfo.processInfo.systemUptime; }
typedef void (^ReportRawTaskRunner)(NSArray *, NSData *, double, NSDictionary *, ReportTaskDone);
static ReportRawTaskRunner ReportRawRunner(NSString *(^findTool)(void), NSTask *(^makeTask)(void), double (^now)(void), void (^pause)(double)) {
    return ^(NSArray *arguments, NSData *input, double seconds, NSDictionary *environment, ReportTaskDone done) {
        dispatch_async(dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^{
            @autoreleasepool {
                if (input.length > 64 * 1024) { done(-2, nil, NO); return; }
                NSString *tool = findTool();
                if (!tool) { done(-2, nil, NO); return; }
                NSTask *task = makeTask();
                task.executableURL = [NSURL fileURLWithPath:tool]; task.arguments = arguments;
                task.environment = environment; task.currentDirectoryURL = [NSURL fileURLWithPath:@"/"];
                NSPipe *inPipe = [NSPipe pipe], *outPipe = [NSPipe pipe];
                task.standardInput = inPipe; task.standardOutput = outPipe;
                // Child diagnostics can contain credentials, paths and remote data: never retain or display them.
                task.standardError = NSFileHandle.fileHandleWithNullDevice;
                NSError *error = nil;
                if (![task launchAndReturnError:&error]) { done(-2, nil, NO); return; }
                [inPipe.fileHandleForReading closeFile]; [outPipe.fileHandleForWriting closeFile];
                int infd = inPipe.fileHandleForWriting.fileDescriptor, outfd = outPipe.fileHandleForReading.fileDescriptor;
                fcntl(infd, F_SETFL, O_NONBLOCK); fcntl(infd, F_SETNOSIGPIPE, 1); fcntl(outfd, F_SETFL, O_NONBLOCK);
                NSMutableData *output = [NSMutableData data]; NSUInteger sent = 0; BOOL closed = NO, uncertain = NO;
                double end = now() + fmin(30, fmax(1, seconds));
                for (;;) {
                    if (!closed) {
                        if (sent < input.length) {
                            ssize_t n = write(infd, (const char *)input.bytes + sent, input.length - sent);
                            if (n > 0) sent += (NSUInteger)n;
                            else if (n < 0 && errno != EAGAIN && errno != EINTR) { closed = YES; [inPipe.fileHandleForWriting closeFile]; }
                        }
                        if (!closed && sent == input.length) { closed = YES; [inPipe.fileHandleForWriting closeFile]; }
                    }
                    char buffer[1024]; ssize_t n;
                    while ((n = read(outfd, buffer, sizeof buffer)) > 0) {
                        if (output.length + (NSUInteger)n > 4096) { uncertain = YES; break; }
                        [output appendBytes:buffer length:(NSUInteger)n];
                    }
                    if (uncertain || now() >= end) { uncertain = YES; break; }
                    if (!task.running) {
                        // Drain again after observing exit: the final stdout write can race the first nonblocking read.
                        while ((n = read(outfd, buffer, sizeof buffer)) > 0) {
                            if (output.length + (NSUInteger)n > 4096) { uncertain = YES; break; }
                            [output appendBytes:buffer length:(NSUInteger)n];
                        }
                        if (sent != input.length) uncertain = YES;
                        break;
                    }
                    pause(0.02);
                }
                if (task.running) {
                    [task terminate];
                    double grace = now() + 0.25;
                    while (task.running && now() < grace) pause(0.01);
                    if (task.running) kill(task.processIdentifier, SIGKILL);
                }
                if (!closed) [inPipe.fileHandleForWriting closeFile];
                [outPipe.fileHandleForReading closeFile];
                done(uncertain ? -1 : task.terminationStatus, [output copy], uncertain);
            }
        });
    };
}
// Freeze one CLI credential for this review session. Account switching in the CLI cannot
// change the identity between verification and creation. Keep secrets out of argv and diagnostics.
static ReportTaskRunner ReportRunner(NSString *(^findTool)(void), NSTask *(^makeTask)(void), double (^now)(void), void (^pause)(double)) {
    ReportRawTaskRunner raw = ReportRawRunner(findTool, makeTask, now, pause);
    __block NSDictionary *pinnedEnvironment;
    return ^(NSArray *arguments, NSData *input, double seconds, ReportTaskDone done) {
        if (pinnedEnvironment) { raw(arguments, input, seconds, pinnedEnvironment, done); return; }
        raw(@[@"auth", @"token", @"--hostname", @"github.com"], nil, 15, ReportChildEnvironment(),
            ^(int status, NSData *output, BOOL uncertain) {
                if (status || uncertain || !output.length) { done(-2, nil, NO); return; }
                NSString *token = [[[NSString alloc] initWithData:output encoding:NSUTF8StringEncoding]
                    stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
                NSCharacterSet *invalid = [[NSCharacterSet characterSetWithCharactersInString:
                    @"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_"] invertedSet];
                if (!token.length || token.length > 4096 || [token rangeOfCharacterFromSet:invalid].location != NSNotFound) {
                    done(-2, nil, NO); return;
                }
                NSMutableDictionary *environment = [ReportChildEnvironment() mutableCopy];
                environment[@"GH_TOKEN"] = token;
                pinnedEnvironment = [environment copy];
                raw(arguments, input, seconds, pinnedEnvironment, done);
            });
    };
}
ReportTaskRunner ReportGitHubRunner(void) {
    return ReportRunner(^{ return ReportExecutable(); }, ^{ return [NSTask new]; },
        ^{ return ReportMonotonic(); }, ^(double seconds) { [NSThread sleepForTimeInterval:seconds]; });
}
BOOL ReportValidIssueURL(NSString *text) {
    if (text.length > 200) return NO;
    NSRegularExpression *pattern = [NSRegularExpression regularExpressionWithPattern:
        @"\\Ahttps://github\\.com/elforeign/nfs-most-wanted-mac/issues/[1-9][0-9]*\\z" options:0 error:NULL];
    return text && [pattern numberOfMatchesInString:text options:0 range:NSMakeRange(0, text.length)] == 1;
}
// Local capability: this adapter implements text issue creation only; images use manual browser attachment.
BOOL ReportAutomaticAttachmentsSupported(void) { return NO; }
static NSArray *ReportAccountArguments(void) { return @[@"api", @"--hostname", @"github.com", @"user", @"--jq", @".login"]; }
static NSString *ReportAccount(NSData *data) {
    if (data.length > 64) return nil;
    NSString *text = [[[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding]
        stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
    NSRegularExpression *re = [NSRegularExpression regularExpressionWithPattern:@"\\A[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?\\z" options:0 error:NULL];
    return text.length && [re numberOfMatchesInString:text options:0 range:NSMakeRange(0, text.length)] ? text : nil;
}
@implementation ReportSubmission {
    ReportTaskRunner _runner;
    NSUInteger _generation;
}
- (instancetype)initWithSnapshot:(NSDictionary *)snapshot runner:(ReportTaskRunner)runner {
    if ((self = [super init])) { _snapshot = @{@"title":[snapshot[@"title"] copy], @"body":[snapshot[@"body"] copy]}; _runner = [runner copy]; _message = @"Check your GitHub account separately before posting."; }
    return self;
}
- (void)notify { if (self.changed) self.changed(); }
- (void)forgetAccount {
    if (_state == ReportSubmitChecking || _state == ReportSubmitReady || _state == ReportSubmitFailed) {
        ++_generation; _account = nil; _state = ReportSubmitIdle;
        _message = @"Check your GitHub account separately before posting.";
    }
}
- (void)checkAccount {
    if (getenv("BOOT_KEYS") || !(_state == ReportSubmitIdle || _state == ReportSubmitFailed)) return;
    _state = ReportSubmitChecking; _account = nil; _message = @"Checking GitHub identity only…";
    NSUInteger generation = ++_generation; [self notify];
    _runner(ReportAccountArguments(), nil, 15, ^(int status, NSData *output, BOOL uncertain) {
        dispatch_async(dispatch_get_main_queue(), ^{
            if (generation != self->_generation || self->_state != ReportSubmitChecking) return;
            NSString *account = status == 0 && !uncertain ? ReportAccount(output) : nil;
            self->_account = account; self->_state = account ? ReportSubmitReady : ReportSubmitFailed;
            self->_message = account ? @"Account verified. Review the public destination, then choose Yes." :
                @"GitHub identity unavailable. CLI may be missing, signed out or offline. Copy text & open GitHub is available.";
            [self notify];
        });
    });
}
- (void)post {
    if (getenv("BOOT_KEYS") || _state != ReportSubmitReady || !_account.length) return;
    _state = ReportSubmitPosting; _message = @"Posting publicly… Do not submit another copy.";
    NSUInteger generation = ++_generation; NSString *account = _account; [self notify];
    // Revalidate and create with the same pinned credential; never silently post as a different account.
    _runner(ReportAccountArguments(), nil, 15, ^(int status, NSData *output, BOOL uncertain) {
        dispatch_async(dispatch_get_main_queue(), ^{
            if (generation != self->_generation || self->_state != ReportSubmitPosting) return;
            if (status || uncertain || ![ReportAccount(output) isEqual:account]) {
                self->_state = ReportSubmitFailed; self->_account = nil;
                self->_message = @"GitHub identity changed or could not be verified. No issue was requested. Check account again.";
                [self notify]; return;
            }
            NSUInteger creationGeneration = ++self->_generation;
            NSData *body = [NSJSONSerialization dataWithJSONObject:@{@"title":self.snapshot[@"title"], @"body":self.snapshot[@"body"]} options:0 error:NULL];
            self->_runner(@[@"api", @"--hostname", @"github.com", @"--method", @"POST",
                @"repos/elforeign/nfs-most-wanted-mac/issues", @"--input", @"-", @"--jq", @".html_url"], body, 25,
                ^(int result, NSData *data, BOOL unknown) {
                    dispatch_async(dispatch_get_main_queue(), ^{
                        if (creationGeneration != self->_generation || self->_state != ReportSubmitPosting) return;
                        NSString *url = [[[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding] stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
                        if (ReportValidIssueURL(url)) self->_issueURL = url;
                        if (self->_issueURL && result == 0 && !unknown) {
                            self->_state = ReportSubmitCreated;
                            self->_message = [NSString stringWithFormat:@"Issue created: %@. Screenshots require manual attachment to this issue.", url];
                        } else {
                            // A lost response or nonzero CLI exit can follow successful creation. Never retry automatically.
                            self->_state = ReportSubmitUnknown;
                            self->_message = @"Creation unconfirmed; it may already exist. Check GitHub before a manual post. No automatic retry.";
                        }
                        (void)result; (void)unknown; [self notify];
                    });
                });
        });
    });
}
@end
#endif
