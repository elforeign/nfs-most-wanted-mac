// Local-only issue preparation. Only the explicit confirmation opens a browser.
#if defined(NFS_PUBLIC) && NFS_PUBLIC
#import <Cocoa/Cocoa.h>
#import <CoreText/CoreText.h>
#import <IOKit/IOKitLib.h>
#import <SystemConfiguration/SystemConfiguration.h>
#include <unistd.h>
#include <math.h>
#include "settings.h"
#include "report_github.h"
#include "report_privacy.h"

const char *report_last_path(void);
void portmenu_status(const char *fmt, ...);
void portmenu_open_folder(const char *path);

static NSString *const IssueBase = @"https://github.com/elforeign/nfs-most-wanted-mac/issues/new";
static const NSUInteger IssueLimit = 7500;
static NSString *const DiscordInvite = @"https://discord.gg/VYKkrVhMrd";
static const NSUInteger DiscordLimit = 2000;
static NSString *issueURL, *issueTitle, *issueBody, *issueFolder, *issueError;
static NSArray *previewLines;
static NSMutableArray<NSNumber *> *previewBaselines;
static double offset, contentHeight, viewHeight, layoutWidth;
static int active, confirmSelected;

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

// Percent-encode UTF-8 bytes with an unreserved-only alphabet, including query delimiters.
static NSString *Encode(NSString *text) {
    return [text stringByAddingPercentEncodingWithAllowedCharacters:
        [NSCharacterSet characterSetWithCharactersInString:@"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"]];
}
static NSString *MakeURL(NSString *title, NSString *body) {
    return [NSString stringWithFormat:@"%@?title=%@&body=%@", IssueBase, Encode(title), Encode(body)];
}
static NSString *Prefix(NSString *text, NSUInteger length) {
    if (text.length <= length) return text;
    NSRange range = [text rangeOfComposedCharacterSequenceAtIndex:length];
    return [text substringToIndex:range.location];
}

// Priority: summary and player note, changed settings, then recent events.
// Both destinations share the exact preview body, capped before consent or copying.
static NSDictionary *BuildIssue(NSString *summary, NSString *events, NSDictionary *identity) {
    NSArray *lines = [summary componentsSeparatedByString:@"\n"];
    NSMutableString *core = [NSMutableString string], *settings = [NSMutableString string];
    NSString *category = @"Other", *note = @"(none)";
    BOOL inSettings = NO;
    for (NSString *line in lines) {
        NSRange colon = [line rangeOfString:@":"];
        NSString *key = colon.location == NSNotFound ? @"" : [line substringToIndex:colon.location];
        NSString *value = colon.location == NSNotFound ? @"" : [[line substringFromIndex:colon.location + 1]
            stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceCharacterSet];
        if ([key isEqual:@"category"]) category = value;
        else if ([key isEqual:@"note"]) note = value;
        else if ([@[@"build", @"macOS", @"Mac model", @"display", @"controller", @"audio"] containsObject:key] || [key hasPrefix:@"last "])
            [core appendFormat:@"%@\n", line];
        if ([line hasPrefix:@"settings that differ"]) { inSettings = YES; continue; }
        if ([line hasPrefix:@"files:"]) inSettings = NO;
        if (inSettings && line.length) [settings appendFormat:@"%@\n", line];
    }
    NSString *title = Redact([NSString stringWithFormat:@"[%@] Problem report", category], identity);
    title = Prefix(title, 120);
    NSString *head = Redact([NSString stringWithFormat:@"Category: %@\nPlayer note: %@\n\n%@", category, note, core], identity);
    NSString *changed = Redact(settings.length ? settings : @"(none)\n", identity);
    NSMutableArray *recent = [NSMutableArray array];
    for (NSString *line in [events componentsSeparatedByString:@"\n"]) {
        if ([line hasPrefix:@"==== last "]) break; // the diagnostic ring follows the notable-event ring
        if (line.length && ![line hasPrefix:@"#"]) [recent addObject:line];
    }
    if (recent.count > 12) [recent removeObjectsInRange:NSMakeRange(0, recent.count - 12)];
    NSString *notable = Redact(recent.count ? [recent componentsJoinedByString:@"\n"] : @"(none)", identity);
    NSString *footer = @"\n\nFor Discord: paste it in #bug-reports and drag the screenshot and log in. For GitHub: Drag screenshot.png and log.txt into the issue. Review attachments for personal information before posting.\n";
    BOOL truncated = NO;
    NSString *body, *url;
    for (;;) {
        body = [NSString stringWithFormat:@"Title: %@\n\n%@\nSettings differing from defaults:\n%@\nNotable events (recent, short):\n%@%@%@",
            title, head, changed, notable, truncated ? @"\n[Truncated to fit Discord's 2,000-character limit and the browser URL: events first, then settings, then summary/note.]" : @"", footer];
        url = MakeURL(title, body);
        if (url.length <= IssueLimit && body.length <= DiscordLimit) break;
        truncated = YES;
        // Remove lower-priority tails first; retain the newest events when possible.
        if (notable.length) {
            NSRange newline = [notable rangeOfString:@"\n"];
            notable = newline.location == NSNotFound ? @"" : [notable substringFromIndex:newline.location + 1];
        } else if (changed.length) changed = Prefix(changed, changed.length > 80 ? changed.length - 80 : 0);
        else if (head.length) head = Prefix(head, head.length > 80 ? head.length - 80 : 0);
        else break;
    }
    return @{@"title":title, @"body":body, @"url":url};
}
static NSString *ReadReport(NSString *folder, NSString *name, NSUInteger cap) {
    NSFileHandle *file = [NSFileHandle fileHandleForReadingAtPath:[folder stringByAppendingPathComponent:name]];
    if (!file) return nil;
    NSData *data = [file readDataOfLength:cap + 1]; [file closeFile];
    if (data.length > cap) return nil;
    return [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding];
}
static void SendToGitHub(int unused) {
    (void)unused;
    const char *path = report_last_path();
    if (!path || !*path) { portmenu_status("Save a report first, then choose a posting action."); return; }
    NSString *folder = [NSString stringWithUTF8String:path];
    NSString *summary = ReadReport(folder, @"report.txt", 128 * 1024);
    if (!summary.length) { portmenu_status("Couldn't read the saved report. Save a new report and retry."); return; }
    NSDictionary *issue = BuildIssue(summary, ReadReport(folder, @"events.txt", 512 * 1024) ?: @"", LocalIdentity());
    issueTitle = issue[@"title"]; issueBody = issue[@"body"]; issueURL = issue[@"url"]; issueFolder = folder;
    issueError = nil;
    active = 1; confirmSelected = 0; offset = 0; previewLines = nil; layoutWidth = 0;
}
static Setting s_github = {.id = "diag.report_github", .category = SET_DIAGNOSTICS, .order = 65,
    .label = "Send to GitHub", .type = SET_ACTION,
    .help = "After saving a report, preview its redacted text. Open GitHub to create a public issue in your browser; attachments are added by you.",
    .apply = SendToGitHub};
__attribute__((constructor)) static void RegisterGitHub(void) { settings_add(&s_github); }
static Setting s_discord = {.id = "diag.report_discord", .category = SET_DIAGNOSTICS, .order = 66,
    .label = "Post on Discord", .type = SET_ACTION,
    .help = "Preview the redacted report, then copy it and open Discord. Paste it in #bug-reports and drag the screenshot and log in.",
    .apply = SendToGitHub};
__attribute__((constructor)) static void RegisterDiscord(void) { settings_add(&s_discord); }

int report_github_active(void) { return active; }
void report_github_close(void) {
    active = 0; issueURL = issueTitle = issueBody = issueFolder = issueError = nil; previewLines = nil;
}
static BOOL OpenBrowser(NSURL *url) { return [NSWorkspace.sharedWorkspace openURL:url]; }
static BOOL CopyReport(NSString *text) {
    NSPasteboard *pasteboard = NSPasteboard.generalPasteboard;
    [pasteboard clearContents];
    return [pasteboard setString:text forType:NSPasteboardTypeString];
}
static void Confirm(int discord) {
    if (!active) return;
    if (getenv("BOOT_KEYS")) { issueError = @"Posting actions are disabled in scripted runs."; return; }
    if (discord && !CopyReport(issueBody)) { issueError = @"Couldn't copy the report. Try again or cancel."; return; }
    if (!OpenBrowser([NSURL URLWithString:discord ? DiscordInvite : issueURL])) {
        issueError = discord ? @"Report copied, but couldn't open Discord. Try again or cancel." : @"Couldn't open GitHub. Try again or cancel.";
        return;
    }
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
    case 0x25: confirmSelected = (confirmSelected + 2) % 3; break;
    case 0x27: case 0x09: confirmSelected = (confirmSelected + 1) % 3; break;
    case 0x0D: case 0x20: if (confirmSelected) Confirm(confirmSelected == 2); else report_github_close(); break;
    }
}
void report_github_pad(int direction, uint32_t edge) {
    if (!active) return;
    if (direction) report_github_key(direction == 1 ? 0x26 : direction == 2 ? 0x28 : direction == 3 ? 0x25 : 0x27);
    if (edge & 2) report_github_close();
    if (edge & 16) report_github_key(0x21);
    if (edge & 32) report_github_key(0x22);
    if (edge & 1) report_github_key(0x0D);
}
void report_github_mouse(int kind, double x, double y, double scroll) {
    if (kind == 4) Scroll(-scroll);
    if (kind == 1 && y >= 546 && y < 582) {
        if (x >= 32 && x < 210) Confirm(0);
        else if (x >= 240 && x < 440) Confirm(1);
        else if (x >= 470 && x < 620) report_github_close();
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
    viewHeight = viewport.size.height;
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
    Label(ctx, @"Report a problem — review the exact text", 32, height - 48, 24);
    Label(ctx, @"GitHub opens a draft. Discord copies this text and opens #bug-reports. You choose whether to post.", 32, height - 82, 16);
    CGContextSaveGState(ctx);
    CGContextClipToRect(ctx, CGRectMake(viewport.origin.x, height - CGRectGetMaxY(viewport), viewport.size.width, viewHeight));
    for (NSUInteger i = 0; i < previewLines.count; ++i) {
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
    if (issueError) Label(ctx, issueError, 32, height - 540, 14);
    Label(ctx, confirmSelected == 1 ? @"› Open GitHub" : @"Open GitHub", 32, height - 570, 20);
    Label(ctx, confirmSelected == 2 ? @"› Post on Discord" : @"Post on Discord", 240, height - 570, 20);
    Label(ctx, confirmSelected == 0 ? @"› Cancel" : @"Cancel", 470, height - 570, 20);
    Label(ctx, xbox ? @"↑↓ / wheel scroll · LB/RB page · ←→ choose · A/Enter select · B/Esc cancel" :
        @"↑↓ / wheel scroll · L1/R1 page · ←→ choose · ✕/Enter select · ○/Esc cancel", 32, height - 610, 15);
}
#endif
