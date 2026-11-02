// Real issue builder and F10 input, with fake clipboard/browser/Finder and no network or game.
// Proof: public 401 checks/0 failures, private 6/0; 49 broken controls rejected.
// Per-control counts: report_github_test.py.
#import <Cocoa/Cocoa.h>
#import <CoreText/CoreText.h>
#import <ImageIO/ImageIO.h>
static NSMutableArray *drawnText;
static CTLineRef RecordedLine(CFAttributedStringRef text) {
    [drawnText addObject:[(__bridge NSAttributedString *)text string]];
    return CTLineCreateWithAttributedString(text);
}
#define CTLineCreateWithAttributedString RecordedLine
static int fakeIssues;
static int browserOpens, folderOpens, networkRequests, browserOK = 1;
static NSMutableArray *openOrder;
static NSURL *openedURL;
static NSString *clipboardText;
static int clipboardWrites, clipboardOK = 1;
@interface GHPasteboard : NSObject
+ (instancetype)generalPasteboard;
- (NSInteger)clearContents;
- (BOOL)setString:(NSString *)text forType:(NSPasteboardType)type;
- (NSString *)stringForType:(NSPasteboardType)type;
@end
@implementation GHPasteboard
+ (instancetype)generalPasteboard { return [GHPasteboard new]; }
- (NSInteger)clearContents { clipboardText = nil; return 0; }
- (NSString *)stringForType:(NSPasteboardType)type { return [type isEqual:NSPasteboardTypeString] ? clipboardText : nil; }
- (BOOL)setString:(NSString *)text forType:(NSPasteboardType)type {
    ++clipboardWrites; [openOrder addObject:@"clipboard"];
    if (!clipboardOK || ![type isEqual:NSPasteboardTypeString]) return NO;
    clipboardText = text; return YES;
}
@end
@interface GHWorkspace : NSObject
+ (instancetype)sharedWorkspace;
- (BOOL)openURL:(NSURL *)url;
@end
@implementation GHWorkspace
+ (instancetype)sharedWorkspace { static GHWorkspace *value; if (!value) value = [GHWorkspace new]; return value; }
- (BOOL)openURL:(NSURL *)url {
    if (url.isFileURL) { ++folderOpens; [openOrder addObject:@"folder"]; }
    else { ++browserOpens; openedURL = url; [openOrder addObject:@"browser"]; }
    return browserOK;
}
@end
@interface GHProtocol : NSURLProtocol @end
@implementation GHProtocol
+ (BOOL)canInitWithRequest:(NSURLRequest *)request { (void)request; ++networkRequests; return YES; }
+ (NSURLRequest *)canonicalRequestForRequest:(NSURLRequest *)request { return request; }
- (void)startLoading { [self.client URLProtocol:self didFailWithError:[NSError errorWithDomain:@"offline" code:1 userInfo:nil]]; }
- (void)stopLoading {}
@end
@interface GHTask : NSObject
- (void)resume;
@end
@implementation GHTask
- (void)resume { ++networkRequests; }
@end
@interface GHSession : NSObject
+ (instancetype)sharedSession;
- (GHTask *)dataTaskWithURL:(NSURL *)url;
@end
@implementation GHSession
+ (instancetype)sharedSession { return [GHSession new]; }
- (GHTask *)dataTaskWithURL:(NSURL *)url { (void)url; return [GHTask new]; }
@end
#define NSWorkspace GHWorkspace
#define NSURLSession GHSession
#define NSPasteboard GHPasteboard
#include "report_github.m"
#ifndef REPORT_ORIGINAL_PROBES
#include "report_submit.m"
#endif
#include "portmenu.m"

uint8_t *G;
int diag_on;
const char *diag_dir = "";
Device dev;
CAMetalLayer *layer;
static int checks, failures;
static uint64_t ticks;
static NSString *fixture;
#define CHECK(...) do { ++checks; if (!(__VA_ARGS__)) { ++failures; fprintf(stderr, "FAIL %d: %s\n", __LINE__, #__VA_ARGS__); } } while (0)
uint64_t host_ticks(void) { return ticks; }
void report_request_shot(void) {}
void report_note(const char *fmt, ...) { (void)fmt; }
void diag_event(const char *fmt, ...) { (void)fmt; }
void user32_menu_opening(void) {}
void user32_menu_cursor(void) {}
void feedback_poll_pause(void) {}
void feedback_suppress(unsigned why, int on) { (void)why; (void)on; }
int user32_pad_layout_xbox(void) { return 0; }
uint32_t gstr(const char *s) { (void)s; return 0x1234; }
uint32_t call_guest(Cpu *c, uint32_t fn, int n, const uint32_t *args) { (void)c; (void)fn; (void)n; (void)args; return 0; }
const char *app_pref(const char *key) { (void)key; return NULL; }
void app_pref_set(const char *key, const char *value) { (void)key; (void)value; }
int roots_x360_has(const char *feature) { (void)feature; return 1; }
const char *port_build_id(void) { return "fixture"; }
void osk_note_begin(char *s, size_t n, int p) { (void)s; (void)n; (void)p; }
void osk_note_end(int cancel) { (void)cancel; }
int osk_last_pad(void) { return 0; }
void osk_pass(Cpu *c) { (void)c; }
int guide_active(void) { return 0; }
void guide_open(int testing) { (void)testing; }
void guide_close(void) {}
void guide_refresh(void) {}
void guide_command(int command) { (void)command; }
void guide_scroll(double count) { (void)count; }
double guide_scale_cap(void) { return 1; }
void guide_draw(CGContextRef c, double h, CGRect v, int x) { (void)c; (void)h; (void)v; (void)x; }
GuideShortcutResult guide_shortcut(GuideShortcut *s, double n, uint32_t p, int h, int t, int has, int c, int e, int o) {
    (void)s; (void)n; (void)p; (void)h; (void)t; (void)has; (void)c; (void)e; (void)o; return (GuideShortcutResult){0};
}
const char *report_last_path(void) { return fixture ? fixture.fileSystemRepresentation : ""; }

#if defined(NFS_PUBLIC) && NFS_PUBLIC
static NSDictionary *Identity(void) {
    return @{@"home":@"/Volumes/TestHomes/playerone", @"user":@[@"playerone", @"Example Player"],
        @"computer":@[@"My Test Mac", @"test-mac.local"], @"serial":@[@"C02TEST12345"], @"wifi":@[@"Secret Network"]};
}
static NSString *Summary(void) {
    return @"category:   Audio\nnote:       Sound cuts out & then returns? #test + café 🎮\nbuild:      4.0.0 fixture\n"
        "macOS:      Version 15.0\nMac model:  Mac16,1\ndisplay:    3840x2160, 120 Hz\n"
        "audio:      PCM, Built-in speakers\ncontroller: Example pad\nlast 60s:   fps mean 60.0, worst p99 20.0 ms, GPU mean 4.0 ms\n"
        "\nsettings that differ from their defaults or are set by a switch:\n  audio.output PCM\n"
        "\nfiles: report.txt, screenshot.png, log.txt\n";
}
static NSString *Events(void) {
    return @"# notable events\n1 s audio device changed\n2 s setting audio.output = 1\n\n==== last 1 events ====\nraw diagnostic excluded\n";
}
static void RedactionTests(void) {
    NSDictionary *idn = Identity();
    NSArray *inputs = @[@"/Volumes/TestHomes/playerone/file", [NSString pathWithComponents:@[@"/", @"Users", @"tester", @"log"]], [NSString pathWithComponents:@[@"/", @"home", @"tester", @"log"]],
        @"playerone Example Player", @"My Test Mac test-mac.local", @"C02TEST12345",
        @"12345678-abcd-1234-5678-123456789abc", @"serial=PRIVATE123", @"UUID=PRIVATE456", @"s/n: PRIVATE789",
        @"192.168.1.2", @"2001:db8:85a3::8a2e:370:7334", @"fe80::1%en0", @"::1", @"::ffff:192.0.2.1",
        @"example.person+tag@example.invalid", @"SSID=\"Some Wifi Name\"", @"Wi-Fi: Some Wifi Name",
        @"wireless network='Another Name'", @"Secret Network"];
    for (NSString *value in inputs) CHECK(![Redact(value, idn) isEqual:value]);
    NSString *all = Redact([inputs componentsJoinedByString:@"\n"], idn);
    for (NSString *secret in @[@"playerone", @"otheruser", @"Example Player", @"My Test Mac", @"test-mac.local", @"C02TEST12345",
        @"12345678-abcd", @"PRIVATE123", @"PRIVATE456", @"PRIVATE789", @"192.168", @"2001:db8", @"fe80::1", @"::1", @"::ffff",
        @"example.person", @"Some Wifi Name", @"Another Name", @"Secret Network"]) CHECK(![all containsString:secret]);
    CHECK([all containsString:@"~/file"] && [all containsString:@"~/log"]);
    CHECK([Redact(@"playerone@example.invalid someone@test-mac.local", idn) isEqual:@"[email] [email]"]);
    CHECK([Redact(@"josé@例え.invalid", idn) isEqual:@"[email]"]);
    for (NSString *secret in @[@"R-2622-2223S", @"r-2622-2223s", @"R-2622-2223N", @"Room code: invalid-room", @"TE29-43A2W", @"TE2943A2W", @"te2943a2w", @"tE29-43a2W", @"ABCDEFGAU", @"ABCD-EFGAU", @"Join code: bad-0O1I!", @"online.code=invalid", @"Host code = 'wrong input'"]) {
        CHECK(![Redact(secret, idn) isEqual:secret]);
    }
    CHECK(![Redact(@"roam: forward UDP 47800 to 192.168.1.9. See Guide.", idn) containsString:@"192.168.1.9"]);
    // Markdown emphasis (_x_, __x__) must not shield an endpoint: underscores count as boundaries.
    CHECK(![Redact(@"_TE29-43A2W_ __203.0.113.5__ _fe80::1_", idn) containsString:@"TE29-43A2W"]);
    CHECK(![Redact(@"_TE29-43A2W_ __203.0.113.5__ _fe80::1_", idn) containsString:@"203.0.113.5"]);
    CHECK(![Redact(@"_TE29-43A2W_ __203.0.113.5__ _fe80::1_", idn) containsString:@"fe80::1"]);
    NSString *ordinary = @"DualSense underruns UNDERRUNS 07:44:26 07:44:26.020 7:4:26 ABCD-EFGHJ";
    CHECK([Redact(ordinary, idn) isEqual:ordinary]);
    CHECK([ReportEndpointRedact(@"2001:0db8:0000:0000:0000:0000:0000:0001") isEqual:@"[IP]"]);
    CHECK([ReportEndpointRedact(@"2001:db8::1") isEqual:@"[IP]"]);
    NSString *folder = [NSTemporaryDirectory() stringByAppendingPathComponent:NSUUID.UUID.UUIDString];
    [NSFileManager.defaultManager createDirectoryAtPath:folder withIntermediateDirectories:YES attributes:nil error:NULL];
    NSArray *files = @[@"report.txt", @"events.txt", @"log.txt", @"crash.txt", @"settings.txt", @"stats.txt"];
    NSString *privateText = @"R-2622-2223S r-2622-2223s R-2622-2223N Room code: invalid-room\nTE29-43A2W TE2943A2W te2943a2w tE29-43a2W ABCDEFGAU ABCD-EFGAU Join code: bad-0O1I!\n192.168.1.2 fe80::1%en0\nroam: forward UDP 47800 to 192.168.1.9. See Guide.\n60 fps, 3840x2160";
    privateText = [privateText stringByAppendingFormat:@"\n%@", ordinary];
    for (NSString *name in files) [privateText writeToFile:[folder stringByAppendingPathComponent:name] atomically:YES encoding:NSUTF8StringEncoding error:NULL];
    ReportRedactBundle(folder);
    for (NSString *name in files) {
        NSString *out = [NSString stringWithContentsOfFile:[folder stringByAppendingPathComponent:name] encoding:NSUTF8StringEncoding error:NULL];
        CHECK(out && [out containsString:@"60 fps, 3840x2160"]);
        CHECK([out containsString:ordinary]);
        for (NSString *secret in @[@"R-2622-2223S", @"r-2622-2223s", @"R-2622-2223N", @"Room code: invalid-room", @"TE29-43A2W", @"TE2943A2W", @"te2943a2w", @"tE29-43a2W", @"ABCDEFGAU", @"ABCD-EFGAU", @"bad-0O1I!", @"192.168.1.2", @"fe80::1", @"192.168.1.9"]) CHECK(![out containsString:secret]);
    }
    [NSFileManager.defaultManager removeItemAtPath:folder error:NULL];
    CHECK([Redact(@"60 fps, Mac16,1, 3840x2160 120 Hz, GPU 4.0 ms", idn) isEqual:@"60 fps, Mac16,1, 3840x2160 120 Hz, GPU 4.0 ms"]);
}
#ifndef REPORT_ORIGINAL_PROBES
static void BuilderTests(void) {
    NSDictionary *issue = BuildSnapshot(Summary(), @"gpu: frame_ms=16.7", @"diag: frame_ms=33.4", Identity(), NO);
    NSString *body = issue[@"body"];
    for (NSString *part in @[@"Category: Audio", @"Player note: Sound cuts out & then returns? #test + café 🎮", @"4.0.0 fixture",
        @"Version 15.0", @"Mac16,1", @"3840x2160, 120 Hz", @"PCM, Built-in speakers", @"Example pad", @"fps mean 60.0", @"p99 20.0",
        @"GPU mean 4.0", @"audio.output PCM", @"16.7", @"33.4"]) CHECK([body containsString:part]);
    CHECK(![body containsString:@"Truncated"]);
    NSString *privateSummary = [Summary() stringByReplacingOccurrencesOfString:@"Sound cuts out" withString:@"playerone 192.168.1.2 Secret Network R-2622-2223S r-2622-2223s TE29-43A2W TE2943A2W Join code: bad-0O1I!"];
    for (NSNumber *discord in @[@NO, @YES]) {
        NSDictionary *privateIssue = BuildSnapshot(privateSummary, @"R-2622-2223S TE29-43A2W My Test Mac", nil, Identity(), discord.boolValue);
        for (NSString *secret in @[@"playerone", @"192.168.1.2", @"Secret Network", @"My Test Mac", @"TE29-43A2W", @"TE2943A2W", @"bad-0O1I!", @"R-2622-2223S", @"r-2622-2223s"])
            CHECK(![privateIssue[@"body"] containsString:secret]);
    }
    NSString *twoDisplays = [Summary() stringByReplacingOccurrencesOfString:@"display:    3840x2160, 120 Hz" withString:@"display:    3840x2160, 120 Hz\ndisplay:    EDR current=1.0 potential=1.0 reference=1.0; output=SDR sRGB"];
    for (NSNumber *discord in @[@NO, @YES]) {
        NSDictionary *displayIssue = BuildSnapshot(twoDisplays, nil, nil, Identity(), discord.boolValue);
        CHECK([displayIssue[@"body"] containsString:@"3840x2160, 120 Hz"] && (discord.boolValue || [displayIssue[@"body"] containsString:@"EDR current=1.0"]));
    }
    NSString *huge = [@"界🎮 &?#+" stringByPaddingToLength:24000 withString:@"界🎮 &?#+" startingAtIndex:0];
    for (NSNumber *discord in @[@NO, @YES]) {
        NSString *bigNote = [Summary() stringByReplacingOccurrencesOfString:@"Sound cuts out" withString:huge];
        NSString *bigSettings = [bigNote stringByReplacingOccurrencesOfString:@"audio.output PCM" withString:[@"audio.output PCM " stringByAppendingString:huge]];
        issue = BuildSnapshot(bigSettings, @"gpu: frame_ms=16.7", @"diag: frame_ms=33.4", Identity(), discord.boolValue);
        CHECK(issue && [issue[@"body"] length] <= (discord.boolValue ? 2000u : 10000u));
        for (NSString *part in @[@"Truncated section", @"4.0.0 fixture", @"audio.output PCM", @"fps mean 60.0", @"16.7", @"33.4"])
            CHECK([issue[@"body"] containsString:part]);
        NSString *secret = @"playerone ghp_CanaryToken123 /Volumes/Private/Stuff token=SecretValue";
        issue = BuildSnapshot([Summary() stringByReplacingOccurrencesOfString:@"Sound cuts out" withString:secret], @"process OtherApp peer=SecretPlayer", @"peer SecretPlayer", Identity(), discord.boolValue);
        for (NSString *bad in @[@"playerone", @"CanaryToken", @"SecretValue", @"/Volumes/Private", @"OtherApp", @"SecretPlayer"])
            CHECK(![issue[@"body"] containsString:bad] && ![issue[@"title"] containsString:bad]);
    }
    CHECK(!BuildSnapshot([Summary() stringByAppendingString:@"%FF"], nil, nil, Identity(), NO));
    NSDictionary *realFields = BuildSnapshot(Summary(), @"boot: render: command buffer error: SecretPlayer /Volumes/Private\nboot: audio loop watchdog: OtherApp", @"f999 t1 gpu-gil t=17.000 wait_ms=2.300 gil_ms=4.500 unlocked=1", Identity(), NO);
    CHECK([realFields[@"body"] containsString:@"Render command buffer errors: 1"] && [realFields[@"body"] containsString:@"Audio watchdog events: 1"] && [realFields[@"body"] containsString:@"GPU wait ms=2.300; GIL ms=4.500"]);
    CHECK(![realFields[@"body"] containsString:@"SecretPlayer"] && ![realFields[@"body"] containsString:@"OtherApp"]);
    NSMutableString *maximal = [NSMutableString string];
    for (NSString *key in @[@"category", @"note", @"build", @"macOS", @"Mac model", @"display", @"audio", @"controller"])
        [maximal appendFormat:@"%@: %@\n", key, huge];
    [maximal appendFormat:@"last 60s: fps mean 60.0, worst p99 20.0 ms, GPU mean 4.0 ms\nsettings that differ:\n audio.output %@\nfiles:\n", huge];
    NSDictionary *maxIssue = BuildSnapshot(maximal, nil, nil, Identity(), YES);
    CHECK(maxIssue && [maxIssue[@"body"] length] <= 2000 && [maxIssue[@"body"] containsString:@"build:"] && [maxIssue[@"body"] containsString:@"Settings differing"]);
    CHECK([maxIssue[@"title"] containsString:@"Other"]);
    NSDictionary *inventory = BuildSnapshot([Summary() stringByAppendingString:@"last 60s: fps mean 60.0 OtherApp peer SecretPlayer\n"], nil, nil, Identity(), NO);
    CHECK(![inventory[@"body"] containsString:@"OtherApp"] && ![inventory[@"body"] containsString:@"SecretPlayer"]);
    NSString *ascii = [@"x" stringByPaddingToLength:3200 withString:@"x" startingAtIndex:0];
    NSString *dense = [@"界" stringByPaddingToLength:6000 withString:@"界" startingAtIndex:0];
    for (NSNumber *discord in @[@NO, @YES]) {
        NSUInteger limit = discord.boolValue ? 2000u : 10000u;
        issue = BuildSnapshot(Summary(), huge, nil, Identity(), discord.boolValue);
        CHECK([issue[@"body"] length] <= limit && [issue[@"body"] containsString:@"audio.output PCM"] && [issue[@"body"] containsString:@"Sound cuts out"]);
        NSString *bigSettings = [Summary() stringByReplacingOccurrencesOfString:@"audio.output PCM" withString:huge];
        issue = BuildSnapshot(bigSettings, @"event to omit first", nil, Identity(), discord.boolValue);
        CHECK([issue[@"body"] length] <= limit && [issue[@"body"] containsString:@"Truncated section"] && ![issue[@"body"] containsString:@"event to omit first"]);
        CHECK([issue[@"body"] containsString:@"Sound cuts out"]);
        for (NSString *note in @[huge, ascii, dense]) {
            issue = BuildSnapshot([Summary() stringByReplacingOccurrencesOfString:@"Sound cuts out" withString:note], nil, nil, Identity(), discord.boolValue);
            CHECK([issue[@"body"] length] <= limit && [issue[@"body"] containsString:@"Truncated section"]);
            CHECK([issue[@"body"] containsString:@"audio.output PCM"]);
        }
        issue = BuildSnapshot(Summary(), ascii, nil, Identity(), discord.boolValue);
        CHECK([issue[@"body"] length] <= limit && [issue[@"body"] containsString:@"audio.output PCM"] && [issue[@"body"] containsString:@"Sound cuts out"]);
        issue = BuildSnapshot([Summary() stringByReplacingOccurrencesOfString:@"audio.output PCM" withString:ascii], Events(), nil, Identity(), discord.boolValue);
        CHECK([issue[@"body"] length] <= limit && [issue[@"body"] containsString:@"Sound cuts out"] && ![issue[@"body"] containsString:@"audio device changed"]);
    }

}
#endif
static void WriteFixture(void) {
    CHECK([Summary() writeToFile:[fixture stringByAppendingPathComponent:@"report.txt"] atomically:YES encoding:NSUTF8StringEncoding error:NULL]);
    CHECK([Events() writeToFile:[fixture stringByAppendingPathComponent:@"events.txt"] atomically:YES encoding:NSUTF8StringEncoding error:NULL]);
}
static void Press(int vk) { portmenu_key(vk, 1, 0); portmenu_key(vk, 0, 0); }
static void Pad(uint32_t buttons, int direction) {
    uint32_t inject; ticks += 500000000;
    portmenu_pad(buttons, direction == 1, direction == 2, direction == 3, direction == 4, 0, 0, 0, 0, 1, &inject);
}
#ifdef REPORT_ORIGINAL_PROBES
static void OriginalProbes(NSString *directory) {
    fixture = directory; WriteFixture();
    [@"gpu: frame_ms=16.7\nprocess: OtherApp peer=SecretPlayer\n" writeToFile:[fixture stringByAppendingPathComponent:@"log.txt"] atomically:YES encoding:NSUTF8StringEncoding error:NULL];
    NSString *summary = [Summary() stringByAppendingString:@"last process inventory: OtherApp peer=SecretPlayer fps=60\n"];
    [summary writeToFile:[fixture stringByAppendingPathComponent:@"report.txt"] atomically:YES encoding:NSUTF8StringEncoding error:NULL];
    Setting *s = settings_find("diag.report_github"); s->apply(0);
    CHECK([issueBody containsString:@"16.7"]);
    CHECK(![issueBody containsString:@"OtherApp"] && ![issueBody containsString:@"SecretPlayer"]);
    CGColorSpaceRef cs = CGColorSpaceCreateDeviceRGB();
    CGContextRef ctx = CGBitmapContextCreate(NULL, 980, 640, 8, 3920, cs, (CGBitmapInfo)kCGImageAlphaPremultipliedLast); CGColorSpaceRelease(cs);
    report_github_draw(ctx, 640, CGRectMake(32, 116, 916, 406), 0);
    CGContextRelease(ctx);
    NSString *labels = [drawnText componentsJoinedByString:@"\n"];
    CHECK([labels containsString:@"Check GitHub account"]);
    CHECK([labels containsString:@"PUBLICLY"]);
    CHECK([labels containsString:@"Screenshot Off"]);
    CHECK([labels containsString:@"saved game frame"]);
    CHECK([labels containsString:@"identity only"]);
    int before = clipboardWrites; Confirm(0);
    CHECK(clipboardWrites == before + 1);
    CHECK([openedURL.absoluteString isEqual:@"https://github.com/elforeign/nfs-most-wanted-mac/issues/new"]);
    s->apply(0); Press(0x27); int opens = browserOpens;
    portmenu_key(0x0D, 1, 1);
    CHECK(browserOpens == opens); // repeat cannot confer consent
    s->apply(0); Press(0x27); Press(0x0D);
    CHECK(fakeIssues == 1);
    s->apply(0); browserOK = 0; Press(0x27); Press(0x0D); browserOK = 1;
    CHECK([issueError containsString:@"unconfirmed"] || [issueError containsString:@"unavailable"]);
    report_github_close();
}
#endif
#ifndef REPORT_ORIGINAL_PROBES
static NSMutableArray *cliPending, *cliArguments, *cliInputs;
static int cliCalls;
static BOOL skipReadyRender;
static void DrawReview(void);
static ReportTaskRunner FakeCLI(void) {
    return ^(NSArray *args, NSData *input, double seconds, ReportTaskDone done) {
        (void)seconds; ++cliCalls;
        if ([args containsObject:@"POST"]) ++fakeIssues;
        [cliArguments addObject:args]; [cliInputs addObject:input ?: NSData.data]; [cliPending addObject:[done copy]];
    };
}
static void FinishCLI(int status, NSString *output, BOOL unknown) {
    CHECK(cliPending.count); if (!cliPending.count) return;
    ReportTaskDone done = cliPending.firstObject; [cliPending removeObjectAtIndex:0];
    done(status, [output dataUsingEncoding:NSUTF8StringEncoding], unknown);
    for (int i = 0; i < 10; ++i) [NSRunLoop.currentRunLoop runUntilDate:[NSDate dateWithTimeIntervalSinceNow:0.001]];
    if (!skipReadyRender && submission.state == ReportSubmitReady) DrawReview();
}
static void FreshPress(int vk) { portmenu_key(vk, 0, 0); Press(vk); }
static void Click(double x, double y) { report_github_mouse(2, x, y, 0); report_github_mouse(1, x, y, 0); report_github_mouse(2, x, y, 0); }
static CGContextRef TestContext(void) {
    CGColorSpaceRef cs = CGColorSpaceCreateDeviceRGB();
    CGContextRef ctx = CGBitmapContextCreate(NULL, 980, 640, 8, 3920, cs, (CGBitmapInfo)kCGImageAlphaPremultipliedLast); CGColorSpaceRelease(cs); return ctx;
}
static int visiblePreviewTop;
static void DrawReview(void) {
    CGContextRef ctx = TestContext(); CHECK(ctx != NULL);
    [drawnText removeAllObjects]; report_github_draw(ctx, 640, CGRectMake(32, 116, 916, 406), 0);
    CGContextRef mask = TestContext(); CGContextSetRGBFillColor(mask, 1, 1, 1, 1);
    CGContextFillRect(mask, CGRectMake(32, 460, 600, 48));
    const unsigned char *actual = CGBitmapContextGetData(ctx), *area = CGBitmapContextGetData(mask);
    visiblePreviewTop = 0; for (int i = 0; i < 980 * 640; ++i) if (area[i * 4 + 3] && actual[i * 4 + 3]) ++visiblePreviewTop;
    CGContextRelease(mask); CGContextRelease(ctx);
}
static void FreshSession(void) {
    report_github_close(); submission = nil; issueFolder = nil;
    settings_find("diag.report_github")->apply(0);
}
static void FlowTests(NSString *directory) {
    cliPending = NSMutableArray.array; cliArguments = NSMutableArray.array; cliInputs = NSMutableArray.array;
    submissionRunner = FakeCLI();
    Setting *s = settings_find("diag.report_github");
    CHECK(s && s->type == SET_ACTION && s->category == SET_DIAGNOSTICS && !s->hidden);
    CHECK(!strcmp(s->label, "Send to GitHub"));
    s->apply(0); CHECK(!report_github_active() && browserOpens == 0 && cliCalls == 0);
    fixture = directory; WriteFixture(); set_tab(SET_DIAGNOSTICS); set_open(1);
    [@"gpu: frame_ms=16.7\nprocess: OtherApp peer=SecretPlayer\napi_key=SecretCanary\n/Volumes/Private/Data\n" writeToFile:[fixture stringByAppendingPathComponent:@"log.txt"] atomically:YES encoding:NSUTF8StringEncoding error:NULL];
    FreshSession();
    CHECK(report_github_active() && browserOpens == 0 && cliCalls == 0 && !screenshotOn);
    CHECK([issueBody containsString:@"16.7"] && [issueBody containsString:@"4.0.0 fixture"]);
    for (NSString *secret in @[@"OtherApp", @"SecretPlayer", @"SecretCanary", @"/Volumes/Private"]) CHECK(![issueBody containsString:secret]);
    DrawReview(); NSString *labels = [drawnText componentsJoinedByString:@"\n"];
    CHECK(visiblePreviewTop > 0);
    CHECK([labels containsString:@"PUBLICLY"] && [labels containsString:@"Screenshot Off"] && [labels containsString:@"Cancel"] && [labels containsString:@"identity only"]);
    CFIndex renderedLength = 0; for (id line in previewLines) renderedLength += CTLineGetStringRange((__bridge CTLineRef)line).length;
    CHECK(renderedLength == (CFIndex)issueBody.length && contentHeight > viewHeight);
    FreshPress(0x0D); CHECK(!report_github_active() && cliCalls == 0 && browserOpens == 0);
    FreshSession(); Press(0x1B); CHECK(!report_github_active() && portmenu_is_open());
    FreshSession(); Press(0x79); CHECK(!report_github_active() && !portmenu_is_open()); Press(0x79);
    FreshSession(); Pad(0, 0); Pad(2, 0); CHECK(!report_github_active()); Pad(0, 0);
    FreshSession(); DrawReview(); double before = offset;
    Press(0x22); CHECK(offset > before); Press(0x24); CHECK(offset == 0); Press(0x23); CHECK(offset == contentHeight - viewHeight);
    report_github_mouse(4, 0, 0, 5); CHECK(offset < contentHeight - viewHeight);
    Press(0x27); portmenu_key(0x0D, 1, 0); CHECK(cliCalls == 0);
    portmenu_key(0x0D, 0, 0); portmenu_key(0x0D, 1, 1); CHECK(cliCalls == 0); // held/repeated opening key is inert
    FreshPress(0x0D); CHECK(cliCalls == 1 && fakeIssues == 0 && submission.state == ReportSubmitChecking);
    Click(100, 560); CHECK(cliCalls == 1); // pending duplicate input
    skipReadyRender = YES; FinishCLI(0, @"fixture-account\n", NO); skipReadyRender = NO; CHECK(submission.state == ReportSubmitReady && confirmSelected == 0);
    Click(100, 560); CHECK(cliCalls == 1 && !accountSeen);
    DrawReview(); labels = [drawnText componentsJoinedByString:@"\n"];
    CHECK([labels containsString:@"fixture-account"] && [labels containsString:@"Yes, post publicly"] && [labels containsString:@"github.com/elforeign/nfs-most-wanted-mac"]);
    // The account-check key cannot flow through to posting when it repeats across the result.
    Press(0x27); portmenu_key(0x0D, 1, 1); CHECK(fakeIssues == 0 && cliCalls == 1);
    FreshPress(0x0D); CHECK(submission.state == ReportSubmitPosting && cliCalls == 2);
    Click(100, 560); CHECK(cliCalls == 2);
    FinishCLI(0, @"fixture-account", NO); CHECK(fakeIssues == 1);
    NSString *reviewed = issueBody;
    NSDictionary *payload = [NSJSONSerialization JSONObjectWithData:cliInputs.lastObject options:0 error:NULL];
    CHECK([payload[@"body"] isEqual:reviewed]);
    FinishCLI(0, @"https://github.com/elforeign/nfs-most-wanted-mac/issues/42", NO);
    CHECK(submission.state == ReportSubmitCreated && fakeIssues == 1);
    Click(100, 560); CHECK(fakeIssues == 1 && cliCalls == 3); // no repeat creation
    report_github_close(); s->apply(0); CHECK(submission.state == ReportSubmitCreated && [issueBody isEqual:reviewed]);
    // Explicit fallback copies before opening a constant URL; no automatic fallback on a failure.
    FreshSession(); reviewed = issueBody; [openOrder removeAllObjects];
    report_github_mouse(1, 350, 560, 0); CHECK(openOrder.count == 0); // opening mouse-down must be released
    Click(350, 560); CHECK(!report_github_active() && [clipboardText isEqual:reviewed]);
    CHECK([openedURL.absoluteString isEqual:@"https://github.com/elforeign/nfs-most-wanted-mac/issues/new"]);
    CHECK(([openOrder isEqual:@[@"clipboard", @"browser", @"folder"]]));
    FreshSession(); int opens = browserOpens; clipboardOK = 0; Click(350, 560);
    CHECK(report_github_active() && browserOpens == opens && [issueError containsString:@"Couldn't copy"]);
    clipboardOK = 1; browserOK = 0; Click(350, 560);
    CHECK(report_github_active() && browserOpens == opens + 1 && [issueError containsString:@"couldn't open GitHub"] && [clipboardText isEqual:issueBody]);
    browserOK = 1;
    FreshSession(); opens = browserOpens; int count = cliCalls; int writes = clipboardWrites;
    setenv("BOOT_KEYS", "1", 1); Click(100, 560); Click(350, 560); CHECK(cliCalls == count && browserOpens == opens && clipboardWrites == writes); unsetenv("BOOT_KEYS");
    // Controller affirmative action and Cancel, with a release between each activation.
    FreshSession(); Pad(0, 0); Pad(0, 4); Pad(0, 0); Pad(1, 0); CHECK(submission.state == ReportSubmitChecking); Pad(0, 0);
    FinishCLI(-2, @"missing /Volumes/Secret token=SecretCanary", NO);
    CHECK(submission.state == ReportSubmitFailed && browserOpens == opens && ![submission.message containsString:@"SecretCanary"]);
    Pad(0, 4); Pad(0, 0); Pad(1, 0); Pad(0, 0); // account recheck
    FinishCLI(1, @"signed out", NO); CHECK(submission.state == ReportSubmitFailed);
    Pad(0, 4); Pad(0, 0); Pad(1, 0); Pad(0, 0); FinishCLI(0, @"fixture-account", NO);
    Pad(0, 4); Pad(0, 0); Pad(1, 0); Pad(1, 0); Pad(0, 0); FinishCLI(0, @"fixture-account", NO);
    int padCreated = fakeIssues; FinishCLI(0, @"https://github.com/elforeign/nfs-most-wanted-mac/issues/43", NO);
    CHECK(submission.state == ReportSubmitCreated && fakeIssues == padCreated);
    // Close/reopen invalidates account callbacks, but preserves in-flight creations and their result.
    FreshSession(); Click(100, 560); report_github_close(); s->apply(0); FinishCLI(0, @"late-account", NO);
    CHECK(submission.state == ReportSubmitIdle && !submission.account);
    Click(100, 560); FinishCLI(0, @"fixture-account", NO); Click(100, 560); FinishCLI(0, @"fixture-account", NO);
    int created = fakeIssues; report_github_close(); s->apply(0); CHECK(submission.state == ReportSubmitPosting);
    FinishCLI(-1, @"", YES); CHECK(submission.state == ReportSubmitUnknown && browserOpens == opens);
    Click(100, 560); CHECK(fakeIssues == created);
    report_github_close(); s->apply(0); CHECK(submission.state == ReportSubmitUnknown);
    FreshSession(); Click(850, 560); CHECK(!report_github_active());
    CHECK(cliPending.count == 0 && networkRequests == 0);
}
static void DiscordTests(void) {
    Setting *s = settings_find("diag.report_discord"); CHECK(s && !strcmp(s->label, "Post on Discord") && s->order == s_github.order + 1);
    if (!s) return;
    report_github_close(); submission = nil; s->apply(0);
    NSString *preview = issueBody; CHECK(discordPreview && preview.length <= 2000);
    int opens = browserOpens, writes = clipboardWrites;
    FreshPress(0x0D); CHECK(!report_github_active() && clipboardWrites == writes && browserOpens == opens);
    s->apply(0); [openOrder removeAllObjects]; Click(650, 560);
    CHECK(!report_github_active() && [clipboardText isEqual:preview] && clipboardWrites == writes + 1);
    CHECK([openedURL.absoluteString isEqual:@"https://discord.gg/VYKkrVhMrd"]);
    CHECK(([openOrder isEqual:@[@"clipboard", @"browser", @"folder"]]));
    s->apply(0); clipboardOK = 0; opens = browserOpens; Click(650, 560);
    CHECK(report_github_active() && [issueError containsString:@"Couldn't copy"] && browserOpens == opens);
    clipboardOK = 1; browserOK = 0; Click(650, 560);
    CHECK(report_github_active() && [issueError containsString:@"couldn't open Discord"] && [clipboardText isEqual:preview]); browserOK = 1;
    // Switching representations is a review step and causes no external action.
    FreshSession(); opens = browserOpens; writes = clipboardWrites; Click(650, 560);
    CHECK(discordPreview && browserOpens == opens && clipboardWrites == writes && confirmSelected == 0);
    DrawReview(); CHECK([issueBody length] <= 2000);
    Click(650, 560); CHECK([clipboardText isEqual:issueBody] && browserOpens == opens + 1);
}
static double fakeNow;
static double FakeNow(void) { return fakeNow; }
static void ScreenshotTests(void) {
    reportNow = FakeNow; fakeNow = NSDate.date.timeIntervalSince1970;
    NSString *path = [fixture stringByAppendingPathComponent:@"screenshot.png"];
    NSBitmapImageRep *image = [[NSBitmapImageRep alloc] initWithBitmapDataPlanes:NULL pixelsWide:8 pixelsHigh:4 bitsPerSample:8 samplesPerPixel:4 hasAlpha:YES isPlanar:NO colorSpaceName:NSDeviceRGBColorSpace bytesPerRow:32 bitsPerPixel:32];
    memset(image.bitmapData, 100, 128);
    NSMutableData *png = [NSMutableData data];
    CGImageDestinationRef dest = CGImageDestinationCreateWithData((__bridge CFMutableDataRef)png, CFSTR("public.png"), 1, NULL);
    CGImageDestinationAddImage(dest, image.CGImage, (__bridge CFDictionaryRef)@{(__bridge NSString *)kCGImagePropertyPNGDictionary:@{(__bridge NSString *)kCGImagePropertyPNGDescription:@"token=PixelMetadataCanary /Volumes/Private/metadata"}});
    CHECK(CGImageDestinationFinalize(dest)); CFRelease(dest);
    CHECK([png writeToFile:path atomically:YES]); FreshSession();
    CHECK(!screenshotOn && frozenFrame.length && [frameInfo containsString:@"capture age at save unknown"]);
    NSData *frozen = frozenFrame;
    Click(100, 500); CHECK(screenshotView && !screenshotOn && !screenshotSeen);
    Click(100, 500); CHECK(!screenshotOn); // cannot include an unseen frame
    DrawReview(); CHECK(screenshotSeen && visiblePreviewTop > 0);
    CHECK([[drawnText componentsJoinedByString:@"\n"] containsString:@"No pixel redaction"]);
    Click(100, 500); CHECK(screenshotOn && !screenshotView);
    [@"replacement" writeToFile:path atomically:YES encoding:NSUTF8StringEncoding error:NULL];
    CHECK([frozenFrame isEqual:frozen]); // never silently replace reviewed pixels
    CHECK(SaveReviewedFrame());
    NSData *shared = [NSData dataWithContentsOfFile:[fixture stringByAppendingPathComponent:@"reviewed-frame.png"]];
    CHECK([shared isEqual:frozen]);
    NSBitmapImageRep *clean = [NSBitmapImageRep imageRepWithData:shared];
    CHECK(clean.pixelsWide == 8 && clean.pixelsHigh == 4 && ![[[NSString alloc] initWithData:shared encoding:NSISOLatin1StringEncoding] containsString:@"PixelMetadataCanary"]);
    // Issue creation succeeds independently from a failed manual attachment preparation.
    Click(100, 560); FinishCLI(0, @"fixture-account", NO); Click(100, 560); FinishCLI(0, @"fixture-account", NO);
    NSString *savedFolder = issueFolder; issueFolder = [fixture stringByAppendingPathComponent:@"missing/folder"];
    int before = fakeIssues; FinishCLI(0, @"https://github.com/elforeign/nfs-most-wanted-mac/issues/88", NO);
    CHECK(submission.state == ReportSubmitCreated && submission.issueURL && [issueError containsString:@"frame preparation failed"]);
    Click(100, 560); CHECK(fakeIssues == before); issueFolder = savedFolder;
    Click(100, 500); CHECK(!frameWriteFailed && fakeIssues == before);
    FreshSession(); CHECK(!screenshotOn && !frozenFrame && [frameInfo containsString:@"unavailable"]); // invalid replacement is unavailable
    [png writeToFile:path atomically:YES];
    [NSFileManager.defaultManager setAttributes:@{NSFileModificationDate:[NSDate dateWithTimeIntervalSince1970:fakeNow - 700]} ofItemAtPath:path error:NULL];
    FreshSession(); CHECK(!frozenFrame && !screenshotOn && [frameInfo containsString:@"stale"]);
    [NSFileManager.defaultManager removeItemAtPath:path error:NULL]; FreshSession(); CHECK(!frozenFrame && [frameInfo containsString:@"unavailable"]);
    reportNow = NULL;
}
static int summaryReads;
static NSString *FailSecondSummaryRead(NSString *folder, NSString *name, NSUInteger cap) {
    if ([name isEqual:@"report.txt"] && ++summaryReads == 2) return nil;
    return SavedText(folder, name, cap);
}
static void SnapshotTests(void) {
    WriteFixture();
    NSString *secret = @"ghp_SecretCanary123456 /Volumes/Private/Stuff token=TokenCanary";
    NSString *summary = [Summary() stringByReplacingOccurrencesOfString:@"Sound cuts out" withString:secret];
    [summary writeToFile:[fixture stringByAppendingPathComponent:@"report.txt"] atomically:YES encoding:NSUTF8StringEncoding error:NULL];
    [@"gpu: frame_ms=16.7\nprocess: OtherApp peer=SecretPlayer\n" writeToFile:[fixture stringByAppendingPathComponent:@"log.txt"] atomically:YES encoding:NSUTF8StringEncoding error:NULL];
    [@"diag: frame_ms=33.4\npeer SecretPlayer OtherApp\n" writeToFile:[fixture stringByAppendingPathComponent:@"events.txt"] atomically:YES encoding:NSUTF8StringEncoding error:NULL];
    FreshSession(); CHECK([issueBody containsString:@"16.7"] && [issueBody containsString:@"33.4"]);
    for (NSString *bad in @[@"SecretCanary", @"TokenCanary", @"/Volumes/Private", @"SecretPlayer", @"OtherApp"])
        CHECK(![issueBody containsString:bad] && ![issueTitle containsString:bad] && ![issueURL containsString:bad]);
    NSString *frozen = issueBody; WriteFixture(); CHECK([issueBody isEqual:frozen]);
    Click(350, 560); CHECK([clipboardText isEqual:frozen] && !openedURL.query);
    [summary writeToFile:[fixture stringByAppendingPathComponent:@"report.txt"] atomically:YES encoding:NSUTF8StringEncoding error:NULL];
    FreshSession(); Click(100, 560); FinishCLI(0, @"fixture-account", NO); Click(100, 560); FinishCLI(0, @"fixture-account", NO);
    NSString *serialized = [[NSString alloc] initWithData:cliInputs.lastObject encoding:NSUTF8StringEncoding];
    for (NSString *bad in @[@"SecretCanary", @"TokenCanary", @"/Volumes/Private", @"SecretPlayer", @"OtherApp"]) CHECK(![serialized containsString:bad]);
    FinishCLI(1, @"/Volumes/Private token=TokenCanary ghp_SecretCanary", YES); DrawReview();
    CHECK(![[drawnText componentsJoinedByString:@"\n"] containsString:@"TokenCanary"]);
    NSString *beforeSwitch = issueBody; int opens = browserOpens;
    [@"note: %FF" writeToFile:[fixture stringByAppendingPathComponent:@"report.txt"] atomically:YES encoding:NSUTF8StringEncoding error:NULL];
    Click(650, 560); CHECK(!discordPreview && [issueBody isEqual:beforeSwitch] && issueError && report_github_active());
    CHECK(browserOpens == opens);
    [summary writeToFile:[fixture stringByAppendingPathComponent:@"report.txt"] atomically:YES encoding:NSUTF8StringEncoding error:NULL];
    report_github_close(); submission = nil; Setting *discordSetting = settings_find("diag.report_discord");
    if (discordSetting) { discordSetting->apply(0); NSString *discordText = issueBody;
    for (NSString *bad in @[@"SecretCanary", @"TokenCanary", @"/Volumes/Private", @"SecretPlayer", @"OtherApp"]) CHECK(![discordText containsString:bad]);
    Click(650, 560); CHECK([clipboardText isEqual:discordText] && discordText.length <= 2000);
    }
    WriteFixture();
    NSDictionary *bounded = PublicSnapshot(fixture, Identity()); CHECK([bounded[@"body"] containsString:@"4.0.0 fixture"]);
    NSString *longNote = [@"界🎮" stringByPaddingToLength:24000 withString:@"界🎮" startingAtIndex:0];
    summary = [Summary() stringByReplacingOccurrencesOfString:@"Sound cuts out" withString:longNote];
    [summary writeToFile:[fixture stringByAppendingPathComponent:@"report.txt"] atomically:YES encoding:NSUTF8StringEncoding error:NULL];
    bounded = PublicSnapshot(fixture, Identity());
    CHECK([bounded[@"body"] length] < 10000 && [bounded[@"body"] containsString:@"Truncated section"] && [bounded[@"body"] containsString:@"4.0.0 fixture"] && [bounded[@"body"] containsString:@"audio.output PCM"] && [bounded[@"body"] containsString:@"fps mean 60.0"]);
    [NSFileManager.defaultManager removeItemAtPath:[fixture stringByAppendingPathComponent:@"log.txt"] error:NULL];
    bounded = PublicSnapshot(fixture, Identity()); CHECK([bounded[@"body"] containsString:@"unavailable"]);
    WriteFixture();
    report_github_close(); submission = nil;
    Setting *destination = settings_find("diag.report_discord");
    if (destination) {
        destination->apply(0); NSString *prior = issueBody; int copied = clipboardWrites, opened = browserOpens;
        [NSFileManager.defaultManager removeItemAtPath:[fixture stringByAppendingPathComponent:@"report.txt"] error:NULL];
        Click(350, 560);
        CHECK(report_github_active() && discordPreview && [issueBody isEqual:prior] && issueError);
        CHECK(clipboardWrites == copied && browserOpens == opened);
        WriteFixture();
    }
    FreshSession(); ReportSubmission *priorSubmission = submission; NSString *priorBody = issueBody;
    int priorOpens = browserOpens, priorCalls = cliCalls;
    [[Summary() stringByReplacingOccurrencesOfString:@"Sound cuts out" withString:@"unreviewed replacement"] writeToFile:[fixture stringByAppendingPathComponent:@"report.txt"] atomically:YES encoding:NSUTF8StringEncoding error:NULL];
    summaryReads = 0; snapshotReader = FailSecondSummaryRead; Click(650, 560); snapshotReader = NULL;
    CHECK(submission == priorSubmission && [issueBody isEqual:priorBody] && !discordPreview && issueError);
    CHECK(browserOpens == priorOpens && cliCalls == priorCalls);
}
#endif

#endif
int main(int argc, char **argv) { @autoreleasepool {
    (void)argv;
    CHECK(argc == 2); [NSURLProtocol registerClass:GHProtocol.class]; openOrder = [NSMutableArray array]; drawnText = [NSMutableArray array];
    G = calloc(1, 0x1000000); CHECK(G != NULL);
#if defined(NFS_PUBLIC) && NFS_PUBLIC
    #ifdef REPORT_ORIGINAL_PROBES
    OriginalProbes([NSString stringWithUTF8String:argv[1]]);
#else
    RedactionTests(); BuilderTests(); FlowTests([NSString stringWithUTF8String:argv[1]]); DiscordTests(); ScreenshotTests(); SnapshotTests();
#endif
#else
    CHECK(settings_find("diag.report_github") == NULL); CHECK(!report_github_active());
    CHECK(settings_find("diag.report_discord") == NULL && clipboardWrites == 0);
    CHECK(browserOpens == 0 && folderOpens == 0 && networkRequests == 0);
#endif
    free(G); printf("github report: %d checks, %d failures; network requests %d\n", checks, failures, networkRequests);
    return failures ? 1 : 0;
} }
