// Saved report review and explicit public submission; no external action during preparation.
#if defined(NFS_PUBLIC) && NFS_PUBLIC
#import <Cocoa/Cocoa.h>
#import <CoreText/CoreText.h>
#import <IOKit/IOKitLib.h>
#import <SystemConfiguration/SystemConfiguration.h>
#include <unistd.h>
#include <math.h>
#include <fcntl.h>
#include <sys/stat.h>
#import <ImageIO/ImageIO.h>
#include "settings.h"
#include "report_github.h"
#include "report_submit.h"
#include "report_privacy.h"

const char *report_last_path(void);
void portmenu_status(const char *fmt, ...);
void portmenu_open_folder(const char *path);

static NSString *const IssueBase = @"https://github.com/elforeign/nfs-most-wanted-mac/issues/new";
static NSString *const DiscordInvite = @"https://discord.gg/VYKkrVhMrd";
static const NSUInteger DiscordLimit = 2000;
static NSString *issueURL, *issueTitle, *issueBody, *issueFolder, *issueError;
static NSArray *previewLines;
static NSMutableArray<NSNumber *> *previewBaselines;
static double offset, contentHeight, viewHeight, layoutWidth;
static int active, confirmSelected;
static ReportSubmission *submission;
static ReportTaskRunner submissionRunner;
static BOOL discordPreview, keyArmed, mouseArmed, padArmed;
static BOOL enterHeld, spaceHeld, mouseHeld, padHeld, accountSeen;
static BOOL screenshotOn, screenshotView, screenshotSeen, frameWriteFailed;
static NSData *frozenFrame;
static NSImage *framePreview;
static NSString *frameInfo;
static double (*reportNow)(void);
static NSString *(*snapshotReader)(NSString *, NSString *, NSUInteger);
static double ReportNow(void) { return reportNow ? reportNow() : NSDate.date.timeIntervalSince1970; }
static void Rearm(void) { keyArmed = !enterHeld && !spaceHeld; mouseArmed = !mouseHeld; padArmed = !padHeld; confirmSelected = 0; }


static NSString *ReplacePattern(NSString *text, NSString *pattern, NSString *replacement) {
    NSRegularExpression *re = [NSRegularExpression regularExpressionWithPattern:pattern
        options:NSRegularExpressionCaseInsensitive error:NULL];
    return [re stringByReplacingMatchesInString:text options:0 range:NSMakeRange(0, text.length) withTemplate:replacement];
}

// Identities are supplied separately so tests exercise every rule without real user data.
static NSString *Redact(NSString *text, NSDictionary *identity) {
    text = text ?: @"";
    NSString *home = identity[@"home"];
    if (home.length) text = [text stringByReplacingOccurrencesOfString:home withString:@"~"];
    text = ReplacePattern(text, @"/(?:Users|home)/[^/\\s]+", @"~"); // home paths
    text = ReplacePattern(text, @"\\b[\\p{L}\\p{N}._%+!'-]+@[\\p{L}\\p{N}.-]+\\.[\\p{L}]{2,}\\b", @"[email]");
    for (NSString *key in @[@"user", @"computer", @"serial", @"wifi"]) {
        for (NSString *value in identity[key]) {
            if (!value.length) continue;
            NSString *pattern = [NSString stringWithFormat:@"(?<![\\p{L}\\p{N}_])%@(?![\\p{L}\\p{N}_])",
                [NSRegularExpression escapedPatternForString:value]];
            text = ReplacePattern(text, pattern, @"[redacted]"); // known identities
        }
    }
    text = ReplacePattern(text, @"\\b[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\\b", @"[UUID]");
    text = ReplacePattern(text, @"\\b(?:serial(?:[ _-]?(?:number|no))?|s/n|uuid)\\s*[:=]\\s*[^\\s,;]+", @"serial: [redacted]");
    text = ReplacePattern(text, @"\\b(?:SSID|Wi[ -]?Fi(?:[ -]?name)?|wireless network)\\s*[:=]\\s*(?:\"[^\"\\r\\n]*\"|'[^'\\r\\n]*'|[^\\r\\n,;]+)", @"Wi-Fi: [redacted]");
    return ReportEndpointRedact(text);
}

static NSDictionary *LocalIdentity(void) {
    NSMutableDictionary *values = [NSMutableDictionary dictionary];
    values[@"home"] = NSHomeDirectory();
    values[@"user"] = @[NSUserName() ?: @"", NSFullUserName() ?: @""];
    char host[256] = ""; gethostname(host, sizeof host - 1);
    NSString *computer = CFBridgingRelease(SCDynamicStoreCopyComputerName(NULL, NULL));
    NSString *local = CFBridgingRelease(SCDynamicStoreCopyLocalHostName(NULL));
    values[@"computer"] = @[computer ?: @"", local ?: @"", [NSString stringWithUTF8String:host] ?: @""];
    NSMutableArray *serials = [NSMutableArray array];
    io_service_t platform = IOServiceGetMatchingService(kIOMainPortDefault, IOServiceMatching("IOPlatformExpertDevice"));
    if (platform) {
        for (NSString *key in @[@"IOPlatformSerialNumber", @"IOPlatformUUID"]) {
            id value = CFBridgingRelease(IORegistryEntryCreateCFProperty(platform, (__bridge CFStringRef)key, kCFAllocatorDefault, 0));
            if ([value isKindOfClass:NSString.class]) [serials addObject:value];
        }
        IOObjectRelease(platform);
    }
    values[@"serial"] = serials;
    // Current SSIDs already published in local dynamic-store state need no scan or location prompt.
    SCDynamicStoreRef store = SCDynamicStoreCreate(NULL, CFSTR("Report privacy"), NULL, NULL);
    NSMutableArray *wifi = [NSMutableArray array];
    if (store) {
        NSArray *keys = CFBridgingRelease(SCDynamicStoreCopyKeyList(store, CFSTR("State:/Network/Interface/.*/AirPort")));
        for (NSString *key in keys) {
            NSDictionary *state = CFBridgingRelease(SCDynamicStoreCopyValue(store, (__bridge CFStringRef)key));
            id ssid = state[@"SSID_STR"];
            if ([ssid isKindOfClass:NSString.class] && [ssid length]) [wifi addObject:ssid];
        }
        CFRelease(store);
    }
    values[@"wifi"] = wifi;
    return values;
}

static NSString *Prefix(NSString *text, NSUInteger length) {
    if (text.length <= length) return text;
    NSRange range = [text rangeOfComposedCharacterSequenceAtIndex:length];
    return [text substringToIndex:range.location];
}

// Read only fixed bundle names, bound I/O, reject symlinks and mark missing/oversize files.
static NSString *SavedText(NSString *folder, NSString *name, NSUInteger cap) {
    NSString *path = [folder stringByAppendingPathComponent:name];
    int fd = open(path.fileSystemRepresentation, O_RDONLY | O_NOFOLLOW | O_NONBLOCK);
    if (fd < 0) return nil;
    struct stat attrs;
    if (fstat(fd, &attrs) || !S_ISREG(attrs.st_mode) || attrs.st_size < 0 || (uint64_t)attrs.st_size > cap) { close(fd); return nil; }
    NSMutableData *data = [NSMutableData data]; char bytes[4096]; ssize_t n;
    while ((n = read(fd, bytes, sizeof bytes)) > 0) {
        if (data.length + (NSUInteger)n > cap) { close(fd); return nil; }
        [data appendBytes:bytes length:(NSUInteger)n];
    }
    close(fd); if (n < 0) return nil;
    return [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding];
}
static NSString *BoundSection(NSString *value, NSUInteger cap) {
    if (!value) return @"[unavailable: missing, oversized, unreadable or privacy check failed]";
    if (!value.length) return @"(none)";
    return value.length <= cap ? value : [Prefix(value, cap) stringByAppendingString:@"\n[Truncated section]"];
}
// Only fixed diagnostic labels and numeric values leave the saved logs. Never export inventories or peer names.
static NSString *DiagnosticSummary(NSString *text) {
    if (!text) return nil;
    NSMutableArray *result = [NSMutableArray array];
    NSRegularExpression *numeric = [NSRegularExpression regularExpressionWithPattern:
        @"\\A(?:gpu: frame_ms|audio: underruns|render: draws|diag: frame_ms)=[0-9]{1,9}(?:\\.[0-9]{1,3})?\\z" options:0 error:NULL];
    NSRegularExpression *gil = [NSRegularExpression regularExpressionWithPattern:
        @"\\A(?:f[0-9]+ t[0-9]+ )?gpu-gil t=[0-9.]+ wait_ms=([0-9.]{1,16}) gil_ms=([0-9.]{1,16}) unlocked=1\\z" options:0 error:NULL];
    NSUInteger renderErrors = 0, audioWatchdogs = 0;
    for (NSString *line in [text componentsSeparatedByString:@"\n"]) {
        if ([numeric numberOfMatchesInString:line options:0 range:NSMakeRange(0, line.length)]) [result addObject:line];
        NSTextCheckingResult *match = [gil firstMatchInString:line options:0 range:NSMakeRange(0, line.length)];
        if (match) [result addObject:[NSString stringWithFormat:@"GPU wait ms=%@; GIL ms=%@", [line substringWithRange:[match rangeAtIndex:1]], [line substringWithRange:[match rangeAtIndex:2]]]];
        if ([line hasPrefix:@"boot: render: command buffer error:"]) ++renderErrors;
        if ([line hasPrefix:@"boot: audio loop watchdog:"]) ++audioWatchdogs;
        if (result.count > 6) [result removeObjectAtIndex:0];
    }
    if (renderErrors) [result addObject:[NSString stringWithFormat:@"Render command buffer errors: %lu", (unsigned long)renderErrors]];
    if (audioWatchdogs) [result addObject:[NSString stringWithFormat:@"Audio watchdog events: %lu", (unsigned long)audioWatchdogs]];
    return result.count ? [result componentsJoinedByString:@"\n"] : @"[no allowlisted diagnostic fields available]";
}
static NSDictionary *BuildSnapshot(NSString *summary, NSString *log, NSString *events, NSDictionary *identity, BOOL discord) {
    summary = Redact(summary, identity);
    if (!summary.length) return nil;
    NSMutableDictionary *fields = [NSMutableDictionary dictionary];
    NSMutableString *settings = [NSMutableString string], *performance = [NSMutableString string]; BOOL inSettings = NO;
    NSRegularExpression *perfPattern = [NSRegularExpression regularExpressionWithPattern:
        @"\\Afps mean [0-9.]+(?: min [0-9.]+)?, worst p99 [0-9.]+ ms, GPU mean [0-9.]+ ms\\z" options:0 error:NULL];
    for (NSString *line in [summary componentsSeparatedByString:@"\n"]) {
        NSRange colon = [line rangeOfString:@":"];
        NSString *key = colon.location == NSNotFound ? @"" : [line substringToIndex:colon.location];
        NSString *value = colon.location == NSNotFound ? @"" : [[line substringFromIndex:colon.location + 1] stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceCharacterSet];
        if ([@[@"category", @"note", @"build", @"macOS", @"Mac model", @"display", @"audio", @"controller"] containsObject:key]) {
            if ([key isEqual:@"display"] && fields[key]) fields[key] = [fields[key] stringByAppendingFormat:@"; %@", value];
            else fields[key] = value;
        }
        if ([key rangeOfString:@"\\Alast [0-9]{1,3}s\\z" options:NSRegularExpressionSearch].location != NSNotFound && ([perfPattern numberOfMatchesInString:value options:0 range:NSMakeRange(0, value.length)] || [value isEqual:@"no frame samples yet (fps / p99 / GPU unavailable)"]))
            [performance appendFormat:@"%@: %@\n", key, BoundSection(value, 500)];
        if ([line hasPrefix:@"files:"]) inSettings = NO;
        if (inSettings && line.length) [settings appendFormat:@"%@\n", line];
        if ([line hasPrefix:@"settings that differ"]) inSettings = YES;
    }
    if (![@[@"Audio", @"Graphics", @"Controls", @"Gameplay", @"Crash-freeze", @"Other"] containsObject:fields[@"category"]]) fields[@"category"] = @"Other (unavailable or unrecognized)";
    NSMutableArray *sections = [NSMutableArray array];
    NSUInteger noteCap = discord ? 220 : 1400, fieldCap = discord ? 64 : 220;
    [sections addObject:[NSString stringWithFormat:@"Category: %@\nPlayer note: %@", BoundSection(fields[@"category"], 120), BoundSection(fields[@"note"], noteCap)]];
    for (NSString *key in @[@"build", @"macOS", @"Mac model", @"display", @"audio", @"controller"])
        [sections addObject:[NSString stringWithFormat:@"%@: %@", key, BoundSection(fields[key], fieldCap)]];
    [sections addObject:[@"Recent performance:\n" stringByAppendingString:BoundSection(performance.length ? performance : nil, discord ? 140 : 700)]];
    [sections addObject:[@"Settings differing from defaults:\n" stringByAppendingString:BoundSection(settings, discord ? 200 : 1600)]];
    // Sanitization precedes selection/truncation. A failure never falls back to raw input.
    log = log ? Redact(log, identity) : nil; events = events ? Redact(events, identity) : nil;
    [sections addObject:[@"Last log summary (allowlisted numbers only):\n" stringByAppendingString:BoundSection(DiagnosticSummary(log), discord ? 110 : 500)]];
    [sections addObject:[@"Diagnostic summary (allowlisted numbers only):\n" stringByAppendingString:BoundSection(DiagnosticSummary(events), discord ? 110 : 500)]];
    if (discord) [sections addObject:@"For Discord: paste in #bug-reports; review any manual attachments."];
    NSString *body = [sections componentsJoinedByString:@"\n\n"];
    NSString *title = [NSString stringWithFormat:@"[%@] Problem report", BoundSection(fields[@"category"], 64)];
    body = [NSString stringWithFormat:@"Title: %@\n\n%@", title, body];
    if (discord && body.length > DiscordLimit) return nil;
    return @{@"title":[title copy], @"body":[body copy]};
}
static NSDictionary *DestinationSnapshot(NSString *folder, NSDictionary *identity, BOOL discord) {
    NSString *(*reader)(NSString *, NSString *, NSUInteger) = snapshotReader ?: SavedText;
    return BuildSnapshot(reader(folder, @"report.txt", 128 * 1024), reader(folder, @"log.txt", 512 * 1024), reader(folder, @"events.txt", 512 * 1024), identity, discord);
}
static NSDictionary *PublicSnapshot(NSString *folder, NSDictionary *identity) { return DestinationSnapshot(folder, identity, NO); }
static void FreezeFrame(NSString *folder) {
    frozenFrame = nil; framePreview = nil; screenshotOn = screenshotView = screenshotSeen = frameWriteFailed = NO;
    NSString *path = [folder stringByAppendingPathComponent:@"screenshot.png"];
    NSDictionary *attrs = [NSFileManager.defaultManager attributesOfItemAtPath:path error:NULL];
    NSDate *date = attrs[NSFileModificationDate];
    double age = date ? ReportNow() - date.timeIntervalSince1970 : -1;
    frameInfo = @"Saved game frame unavailable. Sharing Off.";
    if (![attrs[NSFileType] isEqual:NSFileTypeRegular] || !date || [attrs[NSFileSize] unsignedLongLongValue] > 32 * 1024 * 1024 || age < -5) return;
    NSString *savedAge = [NSString stringWithFormat:@"When frozen: saved %.0f s earlier; capture age at save unknown (up to 600 s).", fmax(0, age)];
    if (age > 600) { frameInfo = @"Saved frame is stale (saved over 600 s ago). Save a new report. Sharing Off."; return; }
    NSData *source = [NSData dataWithContentsOfFile:path options:NSDataReadingMappedIfSafe error:NULL];
    CGImageSourceRef sourceImage = source ? CGImageSourceCreateWithData((__bridge CFDataRef)source, NULL) : NULL;
    if (!sourceImage) return;
    NSDictionary *properties = CFBridgingRelease(CGImageSourceCopyPropertiesAtIndex(sourceImage, 0, NULL));
    NSUInteger width = [properties[(__bridge NSString *)kCGImagePropertyPixelWidth] unsignedIntegerValue];
    NSUInteger pixelHeight = [properties[(__bridge NSString *)kCGImagePropertyPixelHeight] unsignedIntegerValue];
    if (!width || !pixelHeight || width > 8192 || pixelHeight > 8192 || width * pixelHeight > 20000000 ||
        (!CGImageSourceGetType(sourceImage) || !CFEqual(CGImageSourceGetType(sourceImage), CFSTR("public.png")))) { CFRelease(sourceImage); return; }
    CGImageRef image = CGImageSourceCreateImageAtIndex(sourceImage, 0, NULL); CFRelease(sourceImage);
    if (!image) return;
    CGColorSpaceRef cs = CGColorSpaceCreateWithName(kCGColorSpaceSRGB);
    CGContextRef ctx = CGBitmapContextCreate(NULL, width, pixelHeight, 8, width * 4, cs, (CGBitmapInfo)kCGImageAlphaPremultipliedLast);
    CGColorSpaceRelease(cs); if (!ctx) { CGImageRelease(image); return; }
    CGContextDrawImage(ctx, CGRectMake(0, 0, width, pixelHeight), image); CGImageRelease(image);
    CGImageRef pixels = CGBitmapContextCreateImage(ctx); CGContextRelease(ctx);
    if (!pixels) return;
    NSBitmapImageRep *clean = [[NSBitmapImageRep alloc] initWithCGImage:pixels]; CGImageRelease(pixels);
    frozenFrame = [clean representationUsingType:NSBitmapImageFileTypePNG properties:@{}];
    framePreview = frozenFrame ? [[NSImage alloc] initWithData:frozenFrame] : nil;
    if (framePreview) frameInfo = savedAge;
}
static BOOL SaveReviewedFrame(void) {
    if (!screenshotOn) return YES;
    if (!frozenFrame || !screenshotSeen) return NO;
    BOOL ok = [frozenFrame writeToFile:[issueFolder stringByAppendingPathComponent:@"reviewed-frame.png"] options:NSDataWritingAtomic error:NULL];
    frameWriteFailed = !ok; return ok;
}
static void SendToGitHub(int discord) {
    const char *path = report_last_path();
    if (!path || !*path) { portmenu_status("Save a report first, then choose a posting action."); return; }
    NSString *folder = [NSString stringWithUTF8String:path];
    BOOL reuse = submission && [issueFolder isEqual:folder] && (submission.state == ReportSubmitPosting || submission.state == ReportSubmitUnknown || submission.state == ReportSubmitCreated);
    NSDictionary *identity = LocalIdentity();
    ReportSubmission *nextSubmission = submission;
    if (!reuse) {
        NSDictionary *snapshot = PublicSnapshot(folder, identity);
        if (!snapshot) { issueError = @"Couldn't safely read the saved report. Save a new report and retry."; portmenu_status("%s", issueError.UTF8String); return; }
        nextSubmission = [[ReportSubmission alloc] initWithSnapshot:snapshot runner:submissionRunner ?: ReportGitHubRunner()];
    }
    BOOL requestedDiscord = discord != 0;
    NSDictionary *issue = requestedDiscord ? DestinationSnapshot(folder, identity, YES) : nextSubmission.snapshot;
    if (!issue[@"body"]) { issueError = @"Privacy check failed. Save a new report. The previous preview is retained."; portmenu_status("%s", issueError.UTF8String); return; }
    if (!reuse) { submission = nextSubmission; issueFolder = folder; FreezeFrame(folder); }
    discordPreview = requestedDiscord;
    issueTitle = issue[@"title"]; issueBody = issue[@"body"]; issueURL = IssueBase;
    issueError = nil; accountSeen = NO;
    active = 1; confirmSelected = 0; offset = 0; previewLines = nil; layoutWidth = 0; Rearm(); keyArmed = mouseArmed = padArmed = NO; enterHeld = spaceHeld = mouseHeld = padHeld = NO;
    __weak ReportSubmission *session = submission;
    submission.changed = ^{
        if (!active || submission != session) return;
        Rearm(); issueError = nil; accountSeen = NO;
        if (submission.state == ReportSubmitCreated && screenshotOn && !SaveReviewedFrame())
            issueError = @"Issue created; frame preparation failed. Retry frame only; do not create another issue.";
        portmenu_status("%s", submission.message.UTF8String);
    };
}
static void SendToDiscord(int unused) { (void)unused; SendToGitHub(1); }
static Setting s_github = {.id = "diag.report_github", .category = SET_DIAGNOSTICS, .order = 65,
    .label = "Send to GitHub", .type = SET_ACTION,
    .help = "Review saved text. Check GitHub identity separately, then explicitly post publicly, or copy text and open GitHub (may need sign-in). Screenshot sharing starts Off.",
    .apply = SendToGitHub};
__attribute__((constructor)) static void RegisterGitHub(void) { settings_add(&s_github); }
static Setting s_discord = {.id = "diag.report_discord", .category = SET_DIAGNOSTICS, .order = 66,
    .label = "Post on Discord", .type = SET_ACTION,
    .help = "Preview the redacted report, then copy it and open Discord. Paste it in #bug-reports and review manual attachments.",
    .apply = SendToDiscord};
__attribute__((constructor)) static void RegisterDiscord(void) { settings_add(&s_discord); }

int report_github_active(void) { return active; }
void report_github_close(void) {
    active = 0; submission.changed = nil; [submission forgetAccount];
    issueError = nil; previewLines = nil; Rearm();
}
static BOOL OpenBrowser(NSURL *url) { return [NSWorkspace.sharedWorkspace openURL:url]; }
static BOOL CopyReport(NSString *text) {
    NSPasteboard *pasteboard = NSPasteboard.generalPasteboard;
    [pasteboard clearContents];
    return [pasteboard setString:text forType:NSPasteboardTypeString];
}
static void Confirm(int action) {
    if (!active) return;
    if (action == 0) { report_github_close(); return; }
    if (getenv("BOOT_KEYS")) { issueError = @"Posting actions are disabled in scripted runs."; return; }
    if (submission.state == ReportSubmitPosting || submission.state == ReportSubmitChecking) return;
    if (action == 4) {
        if (screenshotOn && frameWriteFailed) { issueError = SaveReviewedFrame() ? @"Reviewed frame ready: manually attach reviewed-frame.png. No automatic upload." : @"Frame preparation failed. Select Retry reviewed frame."; return; }
        issueError = nil;
        if (!frozenFrame) { issueError = frameInfo; return; }
        if (screenshotOn) { screenshotOn = NO; screenshotView = NO; return; }
        if (!screenshotView) { screenshotView = YES; screenshotSeen = NO; return; }
        if (!screenshotSeen) return; // selected pixels must have been drawn before inclusion
        screenshotOn = YES; screenshotView = NO;
        if (!ReportAutomaticAttachmentsSupported()) issueError = @"This route supports text only. Manually attach reviewed-frame.png in the browser; no automatic image upload.";
        if (submission.state == ReportSubmitCreated && !SaveReviewedFrame()) issueError = @"Frame preparation failed. Retry frame only.";
        return;
    }
    if (screenshotView) { screenshotView = NO; issueError = @"Review the text before choosing a posting action."; return; }
    if (action == 1) {
        if (submission.state == ReportSubmitReady && !accountSeen) { issueError = @"Review the verified account and repository on screen before choosing Yes."; return; }
        if (discordPreview) return;
        if (submission.state == ReportSubmitReady) [submission post]; else [submission checkAccount];
        return;
    }
    BOOL discord = action == 3;
    // The representation being copied is always the representation on screen. Change destination first.
    if (discord != discordPreview) { SendToGitHub(discord); return; }
    if (!CopyReport(issueBody)) { issueError = @"Couldn't copy the report. Try again or cancel."; return; }
    NSURL *url = [NSURL URLWithString:discord ? DiscordInvite : submission.issueURL ?: IssueBase];
    if (!OpenBrowser(url)) {
        issueError = discord ? @"Report copied, but couldn't open Discord. Try again or cancel." : @"Report copied, but couldn't open GitHub. Try again or cancel.";
        return;
    }
    if (!SaveReviewedFrame()) { issueError = @"Text copied and page opened; frame preparation failed. Retry frame only."; return; }
    portmenu_open_folder(issueFolder.fileSystemRepresentation);
    report_github_close();
}
static void Scroll(double lines) { offset = fmax(0, fmin(offset + lines * 24, fmax(0, contentHeight - viewHeight))); }
void report_github_key(int key) {
    if (!active) return;
    switch (key) {
    case 0x1B: report_github_close(); break;
    case 0x26: Scroll(-1); break;
    case 0x28: Scroll(1); break;
    case 0x21: Scroll(-viewHeight / 24 + 1); break;
    case 0x22: Scroll(viewHeight / 24 - 1); break;
    case 0x24: offset = 0; break;
    case 0x23: Scroll(contentHeight / 24); break;
    case 0x25: confirmSelected = (confirmSelected + 4) % 5; break;
    case 0x27: case 0x09: confirmSelected = (confirmSelected + 1) % 5; break;
    case 0x0D: case 0x20: if (keyArmed) { keyArmed = NO; Confirm(confirmSelected); } break;
    }
}
void report_github_key_event(int key, int down, int repeat) {
    if (!active) return;
    BOOL wasHeld = key == 0x0D ? enterHeld : key == 0x20 ? spaceHeld : NO;
    if (key == 0x0D) enterHeld = down != 0;
    if (key == 0x20) spaceHeld = down != 0;
    if (!down) { if ((key == 0x0D || key == 0x20) && !enterHeld && !spaceHeld) keyArmed = YES; return; }
    if (repeat && (key == 0x0D || key == 0x20)) return;
    if (wasHeld) return;
    report_github_key(key);
}
void report_github_pad_release(uint32_t physical) { padHeld = (physical & 1) != 0; if (!padHeld) padArmed = YES; }
void report_github_pad(int direction, uint32_t edge) {
    if (!active) return;
    if (direction) report_github_key(direction == 1 ? 0x26 : direction == 2 ? 0x28 : direction == 3 ? 0x25 : 0x27);
    if (edge & 2) report_github_close();
    if (edge & 16) report_github_key(0x21);
    if (edge & 32) report_github_key(0x22);
    if ((edge & 1) && padArmed) { padArmed = NO; Confirm(confirmSelected); }
}
void report_github_mouse(int kind, double x, double y, double scroll) {
    if (!active) return;
    if (kind == 4) Scroll(-scroll);
    if (kind == 2) { mouseHeld = NO; mouseArmed = YES; return; }
    if (kind != 1) return;
    BOOL wasHeld = mouseHeld; mouseHeld = YES;
    if (!mouseArmed || wasHeld) return;
    mouseArmed = NO;
    if (y >= 484 && y < 516 && x >= 32 && x < 940) Confirm(4);
    if (y >= 546 && y < 582) {
        if (x >= 32 && x < 280) Confirm(1);
        else if (x >= 290 && x < 580) Confirm(2);
        else if (x >= 600 && x < 800) Confirm(3);
        else if (x >= 820 && x < 950) Confirm(0);
    }
}
static NSDictionary *TextAttributes(double size) {
    return @{NSFontAttributeName:[NSFont systemFontOfSize:size], NSForegroundColorAttributeName:NSColor.whiteColor};
}
static void Label(CGContextRef ctx, NSString *text, double x, double y, double size) {
    NSAttributedString *a = [[NSAttributedString alloc] initWithString:text attributes:TextAttributes(size)];
    CTLineRef line = CTLineCreateWithAttributedString((__bridge CFAttributedStringRef)a);
    CGContextSetTextPosition(ctx, x, y); CTLineDraw(line, ctx); CFRelease(line);
}
void report_github_draw(CGContextRef ctx, double height, CGRect viewport, int xbox) {
    viewHeight = fmin(viewport.size.height, 320);
    if (!previewLines || layoutWidth != viewport.size.width) {
        NSString *text = issueBody;
        NSAttributedString *a = [[NSAttributedString alloc] initWithString:text attributes:TextAttributes(16)];
        CTTypesetterRef typesetter = CTTypesetterCreateWithAttributedString((__bridge CFAttributedStringRef)a);
        NSMutableArray *lines = [NSMutableArray array]; previewBaselines = [NSMutableArray array];
        CFIndex pos = 0; contentHeight = 0;
        while (pos < (CFIndex)a.length) {
            CFIndex count = CTTypesetterSuggestLineBreak(typesetter, pos, fmax(1, viewport.size.width - 20));
            if (count <= 0) count = [text rangeOfComposedCharacterSequenceAtIndex:pos].length;
            [lines addObject:CFBridgingRelease(CTTypesetterCreateLine(typesetter, CFRangeMake(pos, count)))];
            [previewBaselines addObject:@(contentHeight + 18)]; contentHeight += 24; pos += count;
        }
        CFRelease(typesetter); previewLines = lines; layoutWidth = viewport.size.width;
    }
    Scroll(0);
    Label(ctx, [NSString stringWithFormat:@"Review — %@", issueTitle], 32, height - 48, 20);
    Label(ctx, discordPreview ? @"Discord: copies this exact text, then opens Discord. Paste it yourself." :
        @"Yes posts this text PUBLICLY to github.com/elforeign/nfs-most-wanted-mac", 32, height - 82, 16);
    CGContextSaveGState(ctx);
    CGContextClipToRect(ctx, CGRectMake(viewport.origin.x, height - viewport.origin.y - viewHeight, viewport.size.width, viewHeight));
    if (screenshotView && framePreview) {
        CGRect area = CGRectMake(viewport.origin.x, height - viewport.origin.y - viewHeight, viewport.size.width, viewHeight);
        CGFloat ratio = fmin(area.size.width / framePreview.size.width, area.size.height / framePreview.size.height);
        CGRect imageRect = CGRectMake(area.origin.x, area.origin.y, framePreview.size.width * ratio, framePreview.size.height * ratio);
        CGContextDrawImage(ctx, imageRect, [framePreview CGImageForProposedRect:NULL context:nil hints:nil]);
        screenshotSeen = YES;
    }
    for (NSUInteger i = 0; !screenshotView && i < previewLines.count; ++i) {
        double y = previewBaselines[i].doubleValue - offset;
        if (y < -30 || y > viewHeight + 30) continue;
        CGContextSetTextPosition(ctx, viewport.origin.x, height - viewport.origin.y - y);
        CTLineDraw((__bridge CTLineRef)previewLines[i], ctx);
    }
    CGContextRestoreGState(ctx);
    if (contentHeight > viewHeight) {
        double h = fmax(16, viewHeight * viewHeight / contentHeight);
        double y = viewport.origin.y + (viewHeight - h) * offset / (contentHeight - viewHeight);
        CGContextSetRGBFillColor(ctx, 1, .62, .20, 1);
        CGContextFillRect(ctx, CGRectMake(CGRectGetMaxX(viewport) - 5, height - y - h, 4, h));
    }
    NSString *accountLine = submission.account ? [NSString stringWithFormat:@"Verified account: %@ · github.com/elforeign/nfs-most-wanted-mac", submission.account] : @"Account unverified. Check account contacts GitHub for identity only; browser may require sign-in.";
    Label(ctx, accountLine, 32, height - 462, 14);
    if (submission.state == ReportSubmitReady) accountSeen = YES;
    NSString *shotLabel = frameWriteFailed ? @"Retry reviewed frame (attachment only)" : screenshotOn ? @"Screenshot On (manual attachment) — select to turn Off" : screenshotView ?
        @"Include reviewed frame: I checked for names, join codes, secrets and personal paths" : @"Screenshot Off — review saved game frame";
    Label(ctx, confirmSelected == 4 ? [@"› " stringByAppendingString:shotLabel] : shotLabel, 32, height - 500, 16);
    Label(ctx, screenshotView ? @"No pixel redaction. Exclude any frame with player/profile names, join codes, secrets or paths." : frameInfo, 32, height - 480, 13);
    NSString *message = issueError ?: submission.message;
    // Status contains application prose and, after confirmation, a validated issue URL; wrap it so failures remain readable at the review size.
    NSArray *words = [message componentsSeparatedByString:@" "]; NSMutableString *statusLine = [NSMutableString string]; double statusY = height - 518;
    for (NSString *word in words) {
        NSString *next = statusLine.length ? [statusLine stringByAppendingFormat:@" %@", word] : word;
        if ([next sizeWithAttributes:TextAttributes(12)].width > 910 && statusLine.length) {
            Label(ctx, statusLine, 32, statusY, 12); statusY -= 14; [statusLine setString:word];
        } else [statusLine setString:next];
    }
    Label(ctx, statusLine, 32, statusY, 12);
    NSString *post = submission.state == ReportSubmitReady ? @"Yes, post publicly" : submission.state == ReportSubmitPosting ? @"Posting…" :
        submission.state == ReportSubmitChecking ? @"Checking identity…" : submission.state == ReportSubmitCreated ? @"Issue created" :
        submission.state == ReportSubmitUnknown ? @"Creation unconfirmed" : @"Check GitHub account";
    Label(ctx, confirmSelected == 1 ? [@"› " stringByAppendingString:post] : post, 32, height - 570, 18);
    NSString *fallback = submission.issueURL ? @"Copy & open created issue" : @"Copy text & open GitHub";
    Label(ctx, confirmSelected == 2 ? [@"› " stringByAppendingString:fallback] : fallback, 290, height - 570, 18);
    Label(ctx, confirmSelected == 3 ? @"› Post on Discord" : @"Post on Discord", 600, height - 570, 18);
    Label(ctx, confirmSelected == 0 ? @"› Cancel" : @"Cancel", 820, height - 570, 18);
    Label(ctx, xbox ? @"↑↓ / wheel scroll · LB/RB page · ←→ choose · A/Enter select · B/Esc cancel" :
        @"↑↓ / wheel scroll · L1/R1 page · ←→ choose · ✕/Enter select · ○/Esc cancel", 32, height - 610, 15);
}
#endif
