// Shared payload privacy for issue previews and every text file in a report bundle.
// Mac-only Foundation report path; Windows does not use this sanitizer or report exporter.
#import <Foundation/Foundation.h>
#import <IOKit/IOKitLib.h>
#import <SystemConfiguration/SystemConfiguration.h>
#include <fcntl.h>
#include <stdio.h>
#include <unistd.h>
#include <sys/stat.h>
#include <string.h>
static NSString *ReportPrivateText(NSString *text);
static BOOL ReportCodeChecksum(NSString *code) {
    // Same GF(32) recurrence as roam_code_decode (x^5 + x^2 + 1).
    const char *alphabet = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ";
    unsigned check = 0;
    for (NSUInteger i = 0; i < code.length; ++i) {
        unichar c = [code characterAtIndex:i];
        if (c == '-') continue;
        if (c >= 'a' && c <= 'z') c -= 'a' - 'A';
        const char *symbol = c && c < 128 ? strchr(alphabet, c) : NULL;
        if (!symbol) return NO;
        check = (((check << 1) ^ ((check & 16) ? 0x25u : 0)) & 31) ^ (unsigned)(symbol - alphabet);
    }
    return check == 0;
}
static NSString *ReportEndpointRedact(NSString *text) {
    // Sanitize complete values before callers shorten text for a public destination.
    text = ReportPrivateText(text);
    if (!text) return nil;
    // Boundaries are letters/digits only: Markdown emphasis underscores must not shield an endpoint.
    // IPv6 needs compression (::) or eight groups; clock times such as 07:44:26 stay readable.
    NSArray *patterns = @[
        @"(?<![A-Za-z0-9.])(?:[0-9]{1,3}\\.){3}[0-9]{1,3}(?![A-Za-z0-9]|\\.[0-9])",
        @"(?<![A-Za-z0-9:])(?=[0-9a-f:]*::|(?:[0-9a-f]{1,4}:){7})(?:[0-9a-f]{0,4}:){2,}[0-9a-f:.]*(?:%[a-z0-9]+)?(?![A-Za-z0-9:])",
        // Labelled input can be invalid, including characters outside the code alphabet.
        @"\\b(?:join[ _-]?code|host[ _-]?code|room[ _-]?code|online\\.code|hosting:\\s*code)\\s*[:=]?\\s*(?:\"[^\"\\r\\n]*\"|'[^'\\r\\n]*'|[^\\s,;]+)",
        // Unlabelled codes may use any case or only letters; validate their checksum below.
        @"(?<![A-Za-z0-9-])[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{4}-?[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{5}(?![A-Za-z0-9-])",
        // Prefixed room codes are private even when mistyped; legacy codes retain checksum filtering.
        @"(?<![A-Za-z0-9-])R-[A-Za-z0-9]{4}-?[A-Za-z0-9]{5}(?![A-Za-z0-9-])"
    ];
    for (NSUInteger i = 0; i < patterns.count; ++i) {
        NSRegularExpression *re = [NSRegularExpression regularExpressionWithPattern:patterns[i]
            options:NSRegularExpressionCaseInsensitive error:NULL];
        if (!re) return nil;
        if (i == 3) {
            NSMutableString *redacted = [text mutableCopy];
            // Replace backwards so every match still refers to the original string's range.
            for (NSTextCheckingResult *match in [[re matchesInString:text options:0 range:NSMakeRange(0, text.length)] reverseObjectEnumerator])
                if (ReportCodeChecksum([text substringWithRange:match.range]))
                    [redacted replaceCharactersInRange:match.range withString:@"[join code]"];
            text = redacted;
        } else {
            text = [re stringByReplacingMatchesInString:text options:0 range:NSMakeRange(0, text.length)
                withTemplate:i < 2 ? @"[IP]" : @"[join code]"];
        }
    }
    return text;
}

static NSString *ReportPrivacyReplace(NSString *text, NSString *pattern, NSString *replacement) {
    if (!text) return nil;
    NSError *error = nil;
    // Collect overlapping matches before replacing: an unfinished quoted field must not hide
    // a later labelled field and leave the later value's suffix in the exported text.
    NSRegularExpression *regex = [NSRegularExpression regularExpressionWithPattern:
        [NSString stringWithFormat:@"(?=(%@))", pattern]
        options:NSRegularExpressionCaseInsensitive error:&error];
    if (!regex || error) return nil;
    NSMutableArray<NSValue *> *ranges = [NSMutableArray array];
    __block BOOL valid = YES;
    [regex enumerateMatchesInString:text options:NSMatchingReportCompletion range:NSMakeRange(0, text.length)
        usingBlock:^(NSTextCheckingResult *match, NSMatchingFlags flags, BOOL *stop) {
            (void)stop;
            if (flags & NSMatchingInternalError) valid = NO;
            if (!match) return;
            NSRange range = [match rangeAtIndex:1];
            if (!range.length) return;
            if (ranges.count && range.location <= NSMaxRange(ranges.lastObject.rangeValue))
                ranges[ranges.count - 1] = [NSValue valueWithRange:NSUnionRange(ranges.lastObject.rangeValue, range)];
            else [ranges addObject:[NSValue valueWithRange:range]];
        }];
    if (!valid) return nil;  // ICU resource errors must not silently leave a credential unchanged.
    NSMutableString *out = [text mutableCopy];
    for (NSValue *range in ranges.reverseObjectEnumerator)
        [out replaceCharactersInRange:range.rangeValue withString:replacement];
    return out;
}

static NSString *ReportPrivateRules(NSString *text) {
    // Remove URL userinfo before endpoint replacement can destroy its context.
    text = ReportPrivacyReplace(text, @"(?<=//)[^/\\s?#]*@", @"[credentials]@"); // privacy:userinfo
    // Recognizable token prefixes only; unlabelled arbitrary secrets remain a review responsibility.
    // Percent/backslash continuations belong to the credential, including nested encodings.
    text = ReportPrivacyReplace(text, @"(?<![A-Za-z0-9])(?:gh[pousr]_|github_pat_)[A-Za-z0-9_%\\\\]+", @"[token]"); // privacy:github
    // Quoted values consume escaped characters before the closing quote. JSON keys may be quoted.
    NSString *quoted = @"(?:\"(?:\\\\[\\s\\S]|[^\"\\\\])*(?:\"|$)|'(?:\\\\[\\s\\S]|[^'\\\\])*(?:'|$))";
    NSString *value = [NSString stringWithFormat:@"(?:%@|[^\\r\\n,;\\\"'&<>}]+)", quoted];
    NSString *authorization = [NSString stringWithFormat:@"(?<![A-Za-z0-9_])(?:proxy[ _-]?)?authorization[\\\"']?\\s*[:=]\\s*(?:%@|[^\\r\\n<>}]+)", quoted];
    text = ReportPrivacyReplace(text, authorization, @"[authorization]"); // privacy:authorization
    text = ReportPrivacyReplace(text, @"\\b(?:Bearer|Basic)\\s+[A-Za-z0-9._~+/%\\\\=-]+", @"[authorization]"); // privacy:bearer
    NSString *secret = [NSString stringWithFormat:@"(?<![A-Za-z0-9_])(?:x[ _-]api[ _-]key|api[ _-]?(?:key|token|secret)|(?:access|refresh|auth|session|client|github|gh)[ _-]?(?:token|secret)|aws[ _-](?:secret[ _-]access[ _-]key|session[ _-]token|access[ _-]key[ _-]id)|stream[ _-]key|private[ _-]key|password|passwd|pwd|secret|token)[\\\"']?\\s*[:=]\\s*%@", value];
    text = ReportPrivacyReplace(text, secret, @"[secret]"); // privacy:secret
    // Ingest query credentials, including decoded/quoted JSON forms.
    NSString *cid = [NSString stringWithFormat:@"(?<![A-Za-z0-9_])cid[\\\"']?\\s*[:=]\\s*%@", value];
    text = ReportPrivacyReplace(text, cid, @"[stream key]"); // privacy:cid
    // Quoted paths can contain spaces; unquoted paths conservatively consume to a field/line delimiter.
    // Keep the established ~/suffix preview output: its known home has already been removed.
    NSString *root = @"(?:file:(?://|/)|/(?:Volumes|Users|home|(?:private/)?var/folders)/|[A-Za-z]:[\\\\/]+|\\\\{2,}[^\\\\/\\s\"'<>]+[\\\\/]+)";
    NSString *path = [NSString stringWithFormat:@"(?<![A-Za-z0-9_])(?:\"%@(\\\\[\\s\\S]|[^\"\\\\])*(?:\"|$)|'%@(\\\\[\\s\\S]|[^'\\\\])*(?:'|$)|%@[^\\\"'\\r\\n,;<>}]+)", root, root, root];
    text = ReportPrivacyReplace(text, path, @"[path]"); // privacy:path
    return text;
}

// Decode only percent-byte runs and JSON-style slash/quote/Unicode escapes, retaining ordinary % prose.
// Reapply rules at each layer: a quoted secret containing escaped quotes must be consumed before decoding.
// A finite nesting budget bounds hostile input; exhaustion/invalid encoded UTF-8 blocks export.
static NSString *ReportPrivateText(NSString *text) {
    if (!text) return nil;
    @try {
        NSError *error = nil;
        NSRegularExpression *percent = [NSRegularExpression regularExpressionWithPattern:@"(?:%[0-9a-f]{2})+"
            options:NSRegularExpressionCaseInsensitive error:&error];
        NSRegularExpression *escape = [NSRegularExpression regularExpressionWithPattern:@"\\\\(?:\\\\|\"|'|/|u[0-9a-f]{4})"
            options:NSRegularExpressionCaseInsensitive error:&error];
        if (!percent || !escape || error) return nil;
        for (unsigned depth = 0; depth < 16; ++depth) {
            text = ReportPrivateRules(text);
            if (!text) return nil;
            NSMutableString *decoded = [text mutableCopy];
            for (NSTextCheckingResult *match in [[percent matchesInString:text options:0 range:NSMakeRange(0, text.length)] reverseObjectEnumerator]) {
                NSString *bytes = [[text substringWithRange:match.range] stringByRemovingPercentEncoding];
                if (!bytes) return nil;
                [decoded replaceCharactersInRange:match.range withString:bytes];
            }
            // Percent bytes can reveal a UNC prefix; redact it before JSON backslash unescaping.
            text = ReportPrivateRules(decoded);
            if (!text) return nil;
            decoded = [text mutableCopy];
            for (NSTextCheckingResult *match in [[escape matchesInString:text options:0 range:NSMakeRange(0, text.length)] reverseObjectEnumerator]) {
                NSString *code = [text substringWithRange:match.range];
                NSString *character;
                if (code.length == 6) {
                    unsigned hex = 0;
                    if (![[NSScanner scannerWithString:[code substringFromIndex:2]] scanHexInt:&hex]) return nil;
                    unichar c = (unichar)hex;
                    character = [NSString stringWithCharacters:&c length:1];
                } else character = [code substringFromIndex:1];
                [decoded replaceCharactersInRange:match.range withString:character];
            }
            if ([decoded isEqual:text] && ![percent numberOfMatchesInString:text options:0 range:NSMakeRange(0, text.length)])
                return ReportPrivateRules(decoded);
            text = decoded;
        }
    } @catch (NSException *exception) { (void)exception; }
    return nil;
}

// Bundle attachments have the same known-identity and generic identity protection as previews.
// Identity is explicit here so fixtures need no real machine identifiers or network scans.
static NSString *ReportPrivacyIdentityRedact(NSString *text, NSDictionary *identity) {
    text = ReportPrivateText(text);
    NSString *home = identity[@"home"];
    if (home.length) text = [text stringByReplacingOccurrencesOfString:home withString:@"~"]; // privacy:home
    for (NSString *key in @[@"user", @"computer", @"serial", @"wifi"]) {
        for (NSString *value in identity[key]) {
            if (!value.length) continue;
            NSString *pattern = [NSString stringWithFormat:@"(?<![\\p{L}\\p{N}_])%@(?![\\p{L}\\p{N}_])",
                [NSRegularExpression escapedPatternForString:value]];
            text = ReportPrivacyReplace(text, pattern, @"[redacted]"); // privacy:identity
        }
    }
    text = ReportPrivacyReplace(text, @"\\b[\\p{L}\\p{N}._%+!'-]+@[\\p{L}\\p{N}.-]+\\.[\\p{L}]{2,}\\b", @"[email]"); // privacy:email
    text = ReportPrivacyReplace(text, @"\\b[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\\b", @"[UUID]"); // privacy:uuid
    text = ReportPrivacyReplace(text, @"\\b(?:serial(?:[ _-]?(?:number|no))?|s/n|uuid)\\s*[:=]\\s*[^\\s,;]+", @"serial: [redacted]"); // privacy:serial
    text = ReportPrivacyReplace(text, @"\\b(?:SSID|Wi[ -]?Fi(?:[ -]?name)?|wireless network)\\s*[:=]\\s*(?:\"[^\"\\r\\n]*\"|'[^'\\r\\n]*'|[^\\r\\n,;]+)", @"Wi-Fi: [redacted]"); // privacy:wifi
    return text;
}

static NSDictionary *ReportPrivacyLocalIdentity(void) {
    NSMutableDictionary *identity = [NSMutableDictionary dictionary];
    identity[@"home"] = NSHomeDirectory();
    identity[@"user"] = @[NSUserName() ?: @"", NSFullUserName() ?: @""];
    char host[256] = ""; gethostname(host, sizeof host - 1);
    identity[@"computer"] = @[CFBridgingRelease(SCDynamicStoreCopyComputerName(NULL, NULL)) ?: @"",
        CFBridgingRelease(SCDynamicStoreCopyLocalHostName(NULL)) ?: @"", [NSString stringWithUTF8String:host] ?: @""];
    NSMutableArray *serial = [NSMutableArray array], *wifi = [NSMutableArray array];
    io_service_t platform = IOServiceGetMatchingService(kIOMainPortDefault, IOServiceMatching("IOPlatformExpertDevice"));
    if (platform) {
        for (NSString *key in @[@"IOPlatformSerialNumber", @"IOPlatformUUID"]) {
            id value = CFBridgingRelease(IORegistryEntryCreateCFProperty(platform, (__bridge CFStringRef)key, kCFAllocatorDefault, 0));
            if ([value isKindOfClass:NSString.class]) [serial addObject:value];
        }
        IOObjectRelease(platform);
    }
    SCDynamicStoreRef store = SCDynamicStoreCreate(NULL, CFSTR("Report privacy"), NULL, NULL);
    if (store) {
        NSArray *keys = CFBridgingRelease(SCDynamicStoreCopyKeyList(store, CFSTR("State:/Network/Interface/.*/AirPort")));
        for (NSString *key in keys) {
            NSDictionary *state = CFBridgingRelease(SCDynamicStoreCopyValue(store, (__bridge CFStringRef)key));
            id ssid = state[@"SSID_STR"];
            if ([ssid isKindOfClass:NSString.class] && [ssid length]) [wifi addObject:ssid];
        }
        CFRelease(store);
    }
    identity[@"serial"] = serial; identity[@"wifi"] = wifi;
    return identity;
}

// Report notes use Latin-1 while device/log strings use UTF-8 in the same byte stream.
// Preserve valid UTF-8 sequences and convert only isolated legacy bytes; a whole-file
// Latin-1 fallback would change identity strings before redaction can match them.
static NSString *ReportPrivacyDecodeData(NSData *data) {
    if (!data) return nil;
    if (!data.length) return @"";
    const unsigned char *bytes = data.bytes;
    NSMutableString *text = [NSMutableString string];
    NSUInteger start = 0, i = 0;
    while (i < data.length) {
        unsigned c = bytes[i], count = c < 0x80 ? 1 : c >= 0xC2 && c <= 0xDF ? 2 :
            c >= 0xE0 && c <= 0xEF ? 3 : c >= 0xF0 && c <= 0xF4 ? 4 : 0;
        BOOL valid = count && count <= data.length - i;
        for (unsigned k = 1; valid && k < count; ++k) valid = (bytes[i + k] & 0xC0) == 0x80;
        if (valid && count >= 3) {
            unsigned second = bytes[i + 1];
            valid = !((c == 0xE0 && second < 0xA0) || (c == 0xED && second >= 0xA0) ||
                      (c == 0xF0 && second < 0x90) || (c == 0xF4 && second >= 0x90));
        }
        if (valid) { i += count; continue; }
        NSString *run = [[NSString alloc] initWithBytes:bytes + start length:i - start encoding:NSUTF8StringEncoding];
        if (!run) return nil;
        [text appendString:run];
        [text appendFormat:@"%C", (unichar)c];
        start = ++i;
    }
    NSString *run = [[NSString alloc] initWithBytes:bytes + start length:i - start encoding:NSUTF8StringEncoding];
    if (!run) return nil;
    [text appendString:run];
    return text;
}

// A byte tail must be selected from sanitized text: a quoted field can start before the cap.
static void ReportPrivacyCopyLog(const char *from, const char *to, long max) {
    NSString *source = [NSString stringWithUTF8String:from];
    NSString *destination = [NSString stringWithUTF8String:to];
    // Do not read an ever-growing session log just to export its tail. A truncated raw tail can
    // begin inside a multiline credential, so over-budget inputs fail closed instead of guessing.
    const NSUInteger budget = 1024 * 1024;
    int fd = open(source.fileSystemRepresentation, O_RDONLY | O_NONBLOCK);
    FILE *file = fd >= 0 ? fdopen(fd, "rb") : NULL;
    if (!file && fd >= 0) close(fd);
    struct stat st;
    NSData *data = nil;
    if (file && !fstat(fileno(file), &st) && S_ISREG(st.st_mode) && st.st_size <= (off_t)budget) {
        NSMutableData *bytes = [NSMutableData dataWithLength:budget + 1];
        size_t got = fread(bytes.mutableBytes, 1, budget + 1, file);
        if (!ferror(file)) { bytes.length = got; data = bytes; }
    }
    if (file) fclose(file);
    if (!data || data.length > budget) {
        NSString *message = @"Log omitted: exceeds the 1 MiB privacy scan budget or could not be read.\n";
        if (![message writeToFile:destination atomically:YES encoding:NSUTF8StringEncoding error:NULL])
            [NSFileManager.defaultManager removeItemAtPath:destination error:NULL];
        return;
    }
    NSString *text = ReportPrivacyDecodeData(data);
    text = ReportEndpointRedact(ReportPrivacyIdentityRedact(text, ReportPrivacyLocalIdentity()));
    NSData *safe = [text dataUsingEncoding:NSUTF8StringEncoding];
    if (max > 0 && safe.length > (NSUInteger)max) {
        NSUInteger start = safe.length - (NSUInteger)max;
        const unsigned char *bytes = safe.bytes;
        while (start < safe.length && bytes[start++] != '\n') {} // start at a complete line
        safe = [safe subdataWithRange:NSMakeRange(start, safe.length - start)];
    }
    if (!safe || ![safe writeToFile:destination options:NSDataWritingAtomic error:NULL])
        [NSFileManager.defaultManager removeItemAtPath:destination error:NULL];
}

static void ReportPrivacyRedactBundle(NSString *folder, NSDictionary *identity) {
    NSFileManager *fm = NSFileManager.defaultManager;
    for (NSString *name in [fm contentsOfDirectoryAtPath:folder error:NULL]) {
        if (![name.pathExtension.lowercaseString isEqualToString:@"txt"]) continue;
        NSString *path = [folder stringByAppendingPathComponent:name];
        NSData *data = [NSData dataWithContentsOfFile:path];
        NSString *text = ReportPrivacyDecodeData(data);
        text = ReportPrivacyIdentityRedact(text, identity);
        // Fail closed: a failed conversion/write must not leave an unredacted attachment.
        if (!text || ![ReportEndpointRedact(text) writeToFile:path atomically:YES encoding:NSUTF8StringEncoding error:NULL])
            [fm removeItemAtPath:path error:NULL];
    }
}
static void ReportRedactBundle(NSString *folder) {
    ReportPrivacyRedactBundle(folder, ReportPrivacyLocalIdentity());
}
