// Real issue builder and F10 input, with fake clipboard/browser/Finder and no network or game.
// Proof: public 219 checks/0 failures, private 6/0; 38 broken controls rejected.
// Per-control counts: report_github_test.py.
#import <Cocoa/Cocoa.h>
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
    for (NSString *secret in @[@"TE29-43A2W", @"TE2943A2W", @"te2943a2w", @"tE29-43a2W", @"ABCDEFGAU", @"ABCD-EFGAU", @"Join code: bad-0O1I!", @"online.code=invalid", @"Host code = 'wrong input'"]) {
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
    NSString *privateText = @"TE29-43A2W TE2943A2W te2943a2w tE29-43a2W ABCDEFGAU ABCD-EFGAU Join code: bad-0O1I!\n192.168.1.2 fe80::1%en0\nroam: forward UDP 47800 to 192.168.1.9. See Guide.\n60 fps, 3840x2160";
    privateText = [privateText stringByAppendingFormat:@"\n%@", ordinary];
    for (NSString *name in files) [privateText writeToFile:[folder stringByAppendingPathComponent:name] atomically:YES encoding:NSUTF8StringEncoding error:NULL];
    ReportRedactBundle(folder);
    for (NSString *name in files) {
        NSString *out = [NSString stringWithContentsOfFile:[folder stringByAppendingPathComponent:name] encoding:NSUTF8StringEncoding error:NULL];
        CHECK(out && [out containsString:@"60 fps, 3840x2160"]);
        CHECK([out containsString:ordinary]);
        for (NSString *secret in @[@"TE29-43A2W", @"TE2943A2W", @"te2943a2w", @"tE29-43a2W", @"ABCDEFGAU", @"ABCD-EFGAU", @"bad-0O1I!", @"192.168.1.2", @"fe80::1", @"192.168.1.9"]) CHECK(![out containsString:secret]);
    }
    [NSFileManager.defaultManager removeItemAtPath:folder error:NULL];
    CHECK([Redact(@"60 fps, Mac16,1, 3840x2160 120 Hz, GPU 4.0 ms", idn) isEqual:@"60 fps, Mac16,1, 3840x2160 120 Hz, GPU 4.0 ms"]);
}
static void BuilderTests(void) {
    NSDictionary *issue = BuildIssue(Summary(), Events(), Identity());
    NSURLComponents *url = [NSURLComponents componentsWithString:issue[@"url"]];
    NSMutableDictionary *query = [NSMutableDictionary dictionary];
    for (NSURLQueryItem *item in url.queryItems) query[item.name] = item.value;
    CHECK([url.host isEqual:@"github.com"] && [url.path isEqual:@"/elforeign/nfs-most-wanted-mac/issues/new"]);
    CHECK([query[@"title"] isEqual:issue[@"title"]] && [query[@"body"] isEqual:issue[@"body"]]);
    CHECK(query.count == 2);
    NSString *body = issue[@"body"];
    for (NSString *part in @[@"Category: Audio", @"Player note: Sound cuts out & then returns? #test + café 🎮", @"4.0.0 fixture",
        @"Version 15.0", @"Mac16,1", @"3840x2160, 120 Hz", @"PCM, Built-in speakers", @"Example pad", @"fps mean 60.0", @"p99 20.0",
        @"GPU mean 4.0", @"audio.output PCM", @"audio device changed", @"Drag screenshot.png and log.txt"]) CHECK([body containsString:part]);
    CHECK(![body containsString:@"raw diagnostic excluded"]);
    CHECK(![body containsString:@"Truncated"]);
    NSString *privateSummary = [Summary() stringByReplacingOccurrencesOfString:@"Sound cuts out" withString:@"playerone 192.168.1.2 Secret Network TE29-43A2W TE2943A2W Join code: bad-0O1I!"];
    issue = BuildIssue(privateSummary, @"My Test Mac", Identity());
    CHECK(![issue[@"body"] containsString:@"playerone"] && ![issue[@"body"] containsString:@"192.168.1.2"] &&
        ![issue[@"body"] containsString:@"Secret Network"] && ![issue[@"body"] containsString:@"My Test Mac"]);
    CHECK(![issue[@"body"] containsString:@"TE29-43A2W"] && ![issue[@"body"] containsString:@"TE2943A2W"] &&
        ![issue[@"body"] containsString:@"bad-0O1I!"] && ![issue[@"url"] containsString:@"TE29-43A2W"]);
    NSString *huge = [@"🎮 &?#+" stringByPaddingToLength:24000 withString:@"🎮 &?#+" startingAtIndex:0];
    issue = BuildIssue(Summary(), huge, Identity());
    CHECK([issue[@"url"] length] <= 7500 && [issue[@"body"] containsString:@"Truncated"]);
    CHECK([issue[@"body"] containsString:@"audio.output PCM"] && [issue[@"body"] containsString:@"Sound cuts out"]);
    NSString *bigSettings = [Summary() stringByReplacingOccurrencesOfString:@"audio.output PCM" withString:huge];
    issue = BuildIssue(bigSettings, @"event to omit first", Identity());
    CHECK([issue[@"url"] length] <= 7500 && ![issue[@"body"] containsString:@"event to omit first"]);
    CHECK([issue[@"body"] containsString:@"Sound cuts out"]);
    NSString *bigNote = [Summary() stringByReplacingOccurrencesOfString:@"Sound cuts out" withString:huge];
    issue = BuildIssue(bigNote, huge, Identity());
    CHECK([issue[@"url"] length] <= 7500 && [issue[@"body"] containsString:@"Truncated"] && [issue[@"body"] containsString:@"Drag screenshot.png and log.txt"]);
    NSString *ascii = [@"x" stringByPaddingToLength:3200 withString:@"x" startingAtIndex:0];
    issue = BuildIssue([Summary() stringByReplacingOccurrencesOfString:@"Sound cuts out" withString:ascii], Events(), Identity());
    CHECK([issue[@"body"] length] <= 2000 && [issue[@"body"] containsString:@"Truncated"]);
    CHECK([issue[@"body"] containsString:@"2,000-character"]);
    CHECK(![issue[@"body"] containsString:@"audio.output PCM"] && ![issue[@"body"] containsString:@"audio device changed"]);
    issue = BuildIssue(Summary(), ascii, Identity());
    CHECK([issue[@"body"] length] <= 2000 && [issue[@"body"] containsString:@"audio.output PCM"] && [issue[@"body"] containsString:@"Sound cuts out"]);
    issue = BuildIssue([Summary() stringByReplacingOccurrencesOfString:@"audio.output PCM" withString:ascii], Events(), Identity());
    CHECK([issue[@"body"] length] <= 2000 && ![issue[@"body"] containsString:@"audio device changed"] && [issue[@"body"] containsString:@"Sound cuts out"]);
    NSString *dense = [@"界" stringByPaddingToLength:6000 withString:@"界" startingAtIndex:0];
    issue = BuildIssue([Summary() stringByReplacingOccurrencesOfString:@"Sound cuts out" withString:dense], Events(), Identity());
    CHECK([issue[@"url"] length] <= 7500 && [issue[@"body"] length] <= 2000);
}
static void WriteFixture(void) {
    CHECK([Summary() writeToFile:[fixture stringByAppendingPathComponent:@"report.txt"] atomically:YES encoding:NSUTF8StringEncoding error:NULL]);
    CHECK([Events() writeToFile:[fixture stringByAppendingPathComponent:@"events.txt"] atomically:YES encoding:NSUTF8StringEncoding error:NULL]);
}
static void Press(int vk) { portmenu_key(vk, 1, 0); portmenu_key(vk, 0, 0); }
static void Pad(uint32_t buttons, int direction) {
    uint32_t inject; ticks += 500000000;
    portmenu_pad(buttons, direction == 1, direction == 2, direction == 3, direction == 4, 0, 0, 0, 0, 1, &inject);
}
static void FlowTests(NSString *directory) {
    Setting *s = settings_find("diag.report_github");
    CHECK(s && s->type == SET_ACTION && s->category == SET_DIAGNOSTICS && !s->hidden);
    CHECK(!strcmp(s->label, "Send to GitHub"));
    CHECK(browserOpens == 0 && folderOpens == 0 && networkRequests == 0);
    s->apply(0); CHECK(!report_github_active() && browserOpens == 0);
    fixture = directory; WriteFixture();
    set_tab(SET_DIAGNOSTICS); set_open(1);
    s->apply(0); CHECK(report_github_active() && browserOpens == 0 && folderOpens == 0);
    NSString *previewTitle = issueTitle, *previewBody = issueBody;
    Press(0x0D); CHECK(!report_github_active() && browserOpens == 0); // default is Cancel
    s->apply(0); Press(0x1B); CHECK(!report_github_active() && portmenu_is_open());
    s->apply(0); Press(0x79); CHECK(!report_github_active() && !portmenu_is_open()); Press(0x79);
    s->apply(0); Pad(0, 0); Pad(2, 0); CHECK(!report_github_active() && portmenu_is_open()); Pad(0, 0);
    s->apply(0);
    CGColorSpaceRef cs = CGColorSpaceCreateDeviceRGB();
    CGContextRef ctx = CGBitmapContextCreate(NULL, 980, 640, 8, 3920, cs, (CGBitmapInfo)kCGImageAlphaPremultipliedLast); CGColorSpaceRelease(cs);
    CHECK(ctx != NULL); report_github_draw(ctx, 640, CGRectMake(32, 116, 916, 406), 0);
    CHECK(previewLines.count > 5 && contentHeight > viewHeight);
    CFIndex renderedLength = 0;
    for (id line in previewLines) renderedLength += CTLineGetStringRange((__bridge CTLineRef)line).length;
    CHECK(renderedLength == (CFIndex)issueBody.length);
    Pad(0, 2); CHECK(offset > 0); Pad(0, 0);
    double before = offset; Press(0x22); CHECK(offset > before);
    Press(0x24); CHECK(offset == 0); Press(0x23); CHECK(offset == contentHeight - viewHeight);
    report_github_mouse(4, 0, 0, 5); CHECK(offset < contentHeight - viewHeight);
    report_github_draw(ctx, 640, CGRectMake(32, 116, 600, 250), 1); CHECK(layoutWidth == 600);
    CGContextRelease(ctx);
    Pad(0, 4); Pad(0, 0); CHECK(confirmSelected == 1 && browserOpens == 0);
    Pad(1, 0); CHECK(!report_github_active() && browserOpens == 1 && folderOpens == 1); Pad(0, 0);
    CHECK(([openOrder isEqual:@[@"browser", @"folder"]]));
    NSURLComponents *url = openedURL ? [NSURLComponents componentsWithURL:openedURL resolvingAgainstBaseURL:NO] : nil;
    CHECK([url.queryItems[0].value isEqual:previewTitle] && [url.queryItems[1].value isEqual:previewBody]);
    s->apply(0); report_github_mouse(1, 500, 560, 0); CHECK(!report_github_active() && browserOpens == 1);
    s->apply(0); browserOK = 0; report_github_mouse(1, 50, 560, 0);
    CHECK(report_github_active() && browserOpens == 2 && folderOpens == 1);
    CHECK([issueError containsString:@"Couldn't open GitHub"]); browserOK = 1;
    setenv("BOOT_KEYS", "1", 1); Press(0x27); Press(0x0D); CHECK(report_github_active() && browserOpens == 2); unsetenv("BOOT_KEYS");
    report_github_close();
    [[NSFileManager defaultManager] removeItemAtPath:[fixture stringByAppendingPathComponent:@"report.txt"] error:NULL];
    s->apply(0); CHECK(!report_github_active());
    CHECK(networkRequests == 0);
}
static void DiscordTests(void) {
    Setting *s = settings_find("diag.report_discord");
    CHECK(s && s->type == SET_ACTION && s->category == SET_DIAGNOSTICS && !s->hidden);
    if (!s) return;
    CHECK(!strcmp(s->label, "Post on Discord") && s->order == s_github.order + 1);
    WriteFixture();
    int browsers = browserOpens, folders = folderOpens;
    s->apply(0);
    CHECK(report_github_active() && clipboardWrites == 0 && browserOpens == browsers);
    NSString *preview = issueBody;
    CHECK(preview.length <= 2000 && [preview containsString:@"paste it in #bug-reports and drag the screenshot and log in"]);
    Press(0x0D); CHECK(!report_github_active() && clipboardWrites == 0);
    s->apply(0); Press(0x1B); CHECK(clipboardWrites == 0 && browserOpens == browsers);
    s->apply(0); Pad(0, 3); Pad(0, 0); CHECK(confirmSelected == 2);
    [openOrder removeAllObjects];
    Pad(1, 0); Pad(0, 0);
    CHECK(!report_github_active() && browserOpens == browsers + 1 && folderOpens == folders + 1);
    CHECK(clipboardWrites == 1 && [clipboardText isEqual:preview] && clipboardText.length <= 2000);
    CHECK([openedURL.absoluteString isEqual:@"https://discord.gg/VYKkrVhMrd"]);
    CHECK(([openOrder isEqual:@[@"clipboard", @"browser", @"folder"]]));
    s->apply(0); clipboardOK = 0;
    report_github_mouse(1, 250, 560, 0);
    CHECK(report_github_active() && [issueError containsString:@"Couldn't copy"] && browserOpens == browsers + 1);
    clipboardOK = 1; browserOK = 0;
    report_github_mouse(1, 250, 560, 0);
    CHECK(report_github_active() && [issueError containsString:@"couldn't open Discord"] && [clipboardText isEqual:preview]);
    CHECK(folderOpens == folders + 1); browserOK = 1;
    s->apply(0); Press(0x09); Press(0x09); CHECK(confirmSelected == 2);
    browsers = browserOpens; int writes = clipboardWrites;
    setenv("BOOT_KEYS", "1", 1); Press(0x0D); unsetenv("BOOT_KEYS");
    CHECK(report_github_active() && browserOpens == browsers && clipboardWrites == writes);
    Press(0x0D); CHECK(!report_github_active() && browserOpens == browsers + 1 && clipboardWrites == writes + 1);
    CHECK([clipboardText isEqual:preview] && [openedURL.absoluteString isEqual:@"https://discord.gg/VYKkrVhMrd"]);
    // Long, private input uses the same production redaction and rendering path.
    NSString *note = [[[NSString pathWithComponents:@[@"/", @"Users", @"tester", @"log"]] stringByAppendingString:@" person@example.invalid 192.168.1.2 "] stringByPaddingToLength:5000 withString:@"🎮 secret " startingAtIndex:0];
    NSString *summary = [Summary() stringByReplacingOccurrencesOfString:@"Sound cuts out" withString:note];
    CHECK([summary writeToFile:[fixture stringByAppendingPathComponent:@"report.txt"] atomically:YES encoding:NSUTF8StringEncoding error:NULL]);
    s->apply(0); preview = issueBody;
    CHECK(preview.length <= 2000 && [preview containsString:@"Truncated"] && ![preview containsString:@"tester/log"] && ![preview containsString:@"person@example.invalid"] && ![preview containsString:@"192.168.1.2"]);
    CGColorSpaceRef cs = CGColorSpaceCreateDeviceRGB();
    CGContextRef ctx = CGBitmapContextCreate(NULL, 980, 640, 8, 3920, cs, (CGBitmapInfo)kCGImageAlphaPremultipliedLast); CGColorSpaceRelease(cs);
    report_github_draw(ctx, 640, CGRectMake(32, 116, 916, 406), 0);
    CFIndex length = 0; for (id line in previewLines) length += CTLineGetStringRange((__bridge CTLineRef)line).length;
    CHECK(length == (CFIndex)preview.length); CGContextRelease(ctx);
    report_github_mouse(1, 250, 560, 0);
    CHECK([clipboardText isEqual:preview] && clipboardText.length <= 2000 && networkRequests == 0);
}
#endif
int main(int argc, char **argv) { @autoreleasepool {
    (void)argv;
    CHECK(argc == 2); [NSURLProtocol registerClass:GHProtocol.class]; openOrder = [NSMutableArray array];
    G = calloc(1, 0x1000000); CHECK(G != NULL);
#if defined(NFS_PUBLIC) && NFS_PUBLIC
    RedactionTests(); BuilderTests(); FlowTests([NSString stringWithUTF8String:argv[1]]); DiscordTests();
#else
    CHECK(settings_find("diag.report_github") == NULL); CHECK(!report_github_active());
    CHECK(settings_find("diag.report_discord") == NULL && clipboardWrites == 0);
    CHECK(browserOpens == 0 && folderOpens == 0 && networkRequests == 0);
#endif
    free(G); printf("github report: %d checks, %d failures; network requests %d\n", checks, failures, networkRequests);
    return failures ? 1 : 0;
} }
