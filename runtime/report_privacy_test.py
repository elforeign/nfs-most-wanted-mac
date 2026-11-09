"""Synthetic shared/preview/six-attachment privacy regression and independent removal proofs.

Compact mistyped room codes: pinned rule 25 checks/12 failures; fixed 25/0; ordinary words preserved.
Merged proof: pinned original 887 checks/830 failures; review predecessor 893/20; fixed 893/0.
Userinfo 3/3 -> 3/0; encoded continuations 16/16 -> 16/0; production log copy 4/1 -> 4/0.
The original 31 broken controls plus four review controls and mixed decoding are retained (36 total).
Mixed Latin-1 note/UTF-8 identity: pinned decoder 893 checks/3 failures; fixed 893/0.
Injected regex failure: 14 checks/0 failures.
Pinned original and review-predecessor proofs run without Git history in the offline suite.
All compilation, profiles, logs and temporary directories stay under .metal-cache in this checkout.
Coverage: explicit GitHub forms, labelled credentials/auth, personal path roots, JSON-style escapes
and percent escapes (including nesting); arbitrary unlabelled secrets cannot be detected reliably.
Both GitHub and Discord previews use the production snapshot builder. The log-copy check compiles
production copy_tail; the standalone Prefix(sanitized, cap) assertion is equivalence evidence only.
Coverage retains each known identity class, token prefix, credential/path rule, decoding layer,
bundle identity hook, case-varied text extension, and nesting/read/write/regex fail-closed behavior.

Quoted and unquoted fields, unfinished/multiline quotes and escaped quotes are covered. Unquoted
personal paths/credentials conservatively consume to a field or line delimiter; opaque encodings
such as base64 outside recognized authorization values are not decoded as arbitrary secret containers.
Wall time (same load, back to back): 130 s with the broken controls in sequence, 43 s with four at a time; identical result lines.
"""
import concurrent.futures
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / 'runtime'
SCRATCH = ROOT / '.metal-cache' / 'report-privacy-proof'
ORIGINAL = 'f9e5aa04488deb00cf978ecefee137b5ed3e8d5ad394a69818164ec09cd904ba'
PREDECESSOR = '1f942d8a'
# Pinned predecessor code keeps ordinary offline runs independent of Git history.
PREDECESSOR_RULES = {
    'github': '    text = ReportPrivacyReplace(text, @"(?<![A-Za-z0-9])(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]+", @"[token]"); // privacy:github',
    'bearer': '    text = ReportPrivacyReplace(text, @"\\\\b(?:Bearer|Basic)\\\\s+[A-Za-z0-9._~+/=-]+", @"[authorization]"); // privacy:bearer',
}
# Reviewed mixed-encoding decoder, pinned independently of unrelated privacy rules.
LATIN1_BASELINE = '''NSString *text = data ? [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding] : nil;
        if (!text && data) text = [[NSString alloc] initWithData:data encoding:NSISOLatin1StringEncoding];'''
assert hashlib.sha256(LATIN1_BASELINE.encode()).hexdigest() == '4be47ebcd7a1a82ba3edbfc497325783a123a258cf1e91f08fe2d95587053414'

ROOM_CODE_BASELINE = '        @"(?<![A-Za-z0-9-])R-[A-Za-z0-9]{4}-[A-Za-z0-9]{5}(?![A-Za-z0-9-])"'
assert hashlib.sha256(ROOM_CODE_BASELINE.encode()).hexdigest() == '957c0bf63af7805f9b09b9c610e31593f65c0821c650b4e1c67790f83981643a'

PREDECESSOR_COPY = r'''static void copy_tail(const char *from, const char *to, long max) {
    FILE *in = fopen(from, "rb");
    if (!in) return;
    FILE *out = fopen(to, "wb");
    if (!out) { fclose(in); return; }
    fseek(in, 0, SEEK_END);
    long size = ftell(in);
    if (max > 0 && size > max) {
        fseek(in, size - max, SEEK_SET);
        int ch;
        while ((ch = fgetc(in)) != EOF && ch != '\n') {}     // start at a line
    } else fseek(in, 0, SEEK_SET);
    char buf[65536];
    size_t got;
    while ((got = fread(buf, 1, sizeof buf, in)) > 0) fwrite(buf, 1, got, out);
    fclose(in);
    fclose(out);
}
'''



# Baseline integrity proof: 4/4 authentic fragments accepted; 4/4 one-byte corruptions rejected.
# Reviewed baseline fragment; its digest prevents accidental control drift.
PIN_13984AB7_REPORT_PRIVACY_H = r'''// Shared endpoint privacy for issue previews and every text file in a report bundle.
#import <Foundation/Foundation.h>
#include <string.h>
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
    // Boundaries are letters/digits only: Markdown emphasis underscores must not shield an endpoint.
    // IPv6 needs compression (::) or eight groups; clock times such as 07:44:26 stay readable.
    NSArray *patterns = @[
        @"(?<![A-Za-z0-9.])(?:[0-9]{1,3}\\.){3}[0-9]{1,3}(?![A-Za-z0-9]|\\.[0-9])",
        @"(?<![A-Za-z0-9:])(?=[0-9a-f:]*::|(?:[0-9a-f]{1,4}:){7})(?:[0-9a-f]{0,4}:){2,}[0-9a-f:.]*(?:%[a-z0-9]+)?(?![A-Za-z0-9:])",
        // Labelled input can be invalid, including characters outside the code alphabet.
        @"\\b(?:join[ _-]?code|host[ _-]?code|online\\.code|hosting:\\s*code)\\s*[:=]?\\s*(?:\"[^\"\\r\\n]*\"|'[^'\\r\\n]*'|[^\\s,;]+)",
        // Unlabelled codes may use any case or only letters; validate their checksum below.
        @"(?<![A-Za-z0-9-])[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{4}-?[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{5}(?![A-Za-z0-9-])"
    ];
    for (NSUInteger i = 0; i < patterns.count; ++i) {
        NSRegularExpression *re = [NSRegularExpression regularExpressionWithPattern:patterns[i]
            options:NSRegularExpressionCaseInsensitive error:NULL];
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
static void ReportRedactBundle(NSString *folder) {
    NSFileManager *fm = NSFileManager.defaultManager;
    for (NSString *name in [fm contentsOfDirectoryAtPath:folder error:NULL]) {
        if (![name.pathExtension isEqualToString:@"txt"]) continue;
        NSString *path = [folder stringByAppendingPathComponent:name];
        NSData *data = [NSData dataWithContentsOfFile:path];
        NSString *text = data ? [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding] : nil;
        if (!text && data) text = [[NSString alloc] initWithData:data encoding:NSISOLatin1StringEncoding];
        // Fail closed: a failed conversion/write must not leave an unredacted attachment.
        if (!text || ![ReportEndpointRedact(text) writeToFile:path atomically:YES encoding:NSUTF8StringEncoding error:NULL])
            [fm removeItemAtPath:path error:NULL];
    }
}
'''
assert hashlib.sha256(PIN_13984AB7_REPORT_PRIVACY_H.encode()).hexdigest() == 'f9e5aa04488deb00cf978ecefee137b5ed3e8d5ad394a69818164ec09cd904ba', 'pinned baseline changed'


# Reviewed baseline fragment; its digest prevents accidental control drift.
PIN_13984AB7_REPORT_M = r'''static void copy_tail(const char *from, const char *to, long max) {
    FILE *in = fopen(from, "rb");
    if (!in) return;
    FILE *out = fopen(to, "wb");
    if (!out) { fclose(in); return; }
    fseek(in, 0, SEEK_END);
    long size = ftell(in);
    if (max > 0 && size > max) {
        fseek(in, size - max, SEEK_SET);
        int ch;
        while ((ch = fgetc(in)) != EOF && ch != '\n') {}     // start at a line
    } else fseek(in, 0, SEEK_SET);
    char buf[65536];
    size_t got;
    while ((got = fread(buf, 1, sizeof buf, in)) > 0) fwrite(buf, 1, got, out);
    fclose(in);
    fclose(out);
}
static const char *flow_name'''
assert hashlib.sha256(PIN_13984AB7_REPORT_M.encode()).hexdigest() == '115f18a7371c7c8b057260fc4d707d3c7542029c5c9232ab044b5f2c22b2ab9c', 'pinned baseline changed'


# Reviewed baseline fragment; its digest prevents accidental control drift.
PIN_1F942D8A_REPORT_PRIVACY_H = r'''// Shared payload privacy for issue previews and every text file in a report bundle.
#import <Foundation/Foundation.h>
#import <IOKit/IOKitLib.h>
#import <SystemConfiguration/SystemConfiguration.h>
#include <unistd.h>
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
        @"\\b(?:join[ _-]?code|host[ _-]?code|online\\.code|hosting:\\s*code)\\s*[:=]?\\s*(?:\"[^\"\\r\\n]*\"|'[^'\\r\\n]*'|[^\\s,;]+)",
        // Unlabelled codes may use any case or only letters; validate their checksum below.
        @"(?<![A-Za-z0-9-])[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{4}-?[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{5}(?![A-Za-z0-9-])"
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
    for (NSTextCheckingResult *match in [regex matchesInString:text options:0 range:NSMakeRange(0, text.length)]) {
        NSRange range = [match rangeAtIndex:1];
        if (!range.length) continue;
        if (ranges.count && range.location <= NSMaxRange(ranges.lastObject.rangeValue))
            ranges[ranges.count - 1] = [NSValue valueWithRange:NSUnionRange(ranges.lastObject.rangeValue, range)];
        else [ranges addObject:[NSValue valueWithRange:range]];
    }
    NSMutableString *out = [text mutableCopy];
    for (NSValue *range in ranges.reverseObjectEnumerator)
        [out replaceCharactersInRange:range.rangeValue withString:replacement];
    return out;
}

static NSString *ReportPrivateRules(NSString *text) {
    // Recognizable token prefixes only; unlabelled arbitrary secrets remain a review responsibility.
    text = ReportPrivacyReplace(text, @"(?<![A-Za-z0-9])(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]+", @"[token]"); // privacy:github
    // Quoted values consume escaped characters before the closing quote. JSON keys may be quoted.
    NSString *quoted = @"(?:\"(?:\\\\[\\s\\S]|[^\"\\\\])*(?:\"|$)|'(?:\\\\[\\s\\S]|[^'\\\\])*(?:'|$))";
    NSString *value = [NSString stringWithFormat:@"(?:%@|[^\\r\\n,;\\\"'&<>}]+)", quoted];
    NSString *authorization = [NSString stringWithFormat:@"(?<![A-Za-z0-9_])(?:proxy[ _-]?)?authorization[\\\"']?\\s*[:=]\\s*(?:%@|[^\\r\\n<>}]+)", quoted];
    text = ReportPrivacyReplace(text, authorization, @"[authorization]"); // privacy:authorization
    text = ReportPrivacyReplace(text, @"\\b(?:Bearer|Basic)\\s+[A-Za-z0-9._~+/=-]+", @"[authorization]"); // privacy:bearer
    NSString *secret = [NSString stringWithFormat:@"(?<![A-Za-z0-9_])(?:x[ _-]api[ _-]key|api[ _-]?(?:key|token|secret)|(?:access|refresh|auth|session|client|github|gh)[ _-]?(?:token|secret)|aws[ _-](?:secret[ _-]access[ _-]key|session[ _-]token|access[ _-]key[ _-]id)|private[ _-]key|password|passwd|pwd|secret|token)[\\\"']?\\s*[:=]\\s*%@", value];
    text = ReportPrivacyReplace(text, secret, @"[secret]"); // privacy:secret
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

static void ReportPrivacyRedactBundle(NSString *folder, NSDictionary *identity) {
    NSFileManager *fm = NSFileManager.defaultManager;
    for (NSString *name in [fm contentsOfDirectoryAtPath:folder error:NULL]) {
        if (![name.pathExtension.lowercaseString isEqualToString:@"txt"]) continue;
        NSString *path = [folder stringByAppendingPathComponent:name];
        NSData *data = [NSData dataWithContentsOfFile:path];
        NSString *text = data ? [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding] : nil;
        if (!text && data) text = [[NSString alloc] initWithData:data encoding:NSISOLatin1StringEncoding];
        text = ReportPrivacyIdentityRedact(text, identity);
        // Fail closed: a failed conversion/write must not leave an unredacted attachment.
        if (!text || ![ReportEndpointRedact(text) writeToFile:path atomically:YES encoding:NSUTF8StringEncoding error:NULL])
            [fm removeItemAtPath:path error:NULL];
    }
}
static void ReportRedactBundle(NSString *folder) {
    ReportPrivacyRedactBundle(folder, ReportPrivacyLocalIdentity());
}
'''
assert hashlib.sha256(PIN_1F942D8A_REPORT_PRIVACY_H.encode()).hexdigest() == 'bf54464b36ad3caf8120986f4994b6c20d5395839bab6848d0a29b4d4d8d4846', 'pinned baseline changed'


# Reviewed baseline fragment; its digest prevents accidental control drift.
PIN_1F942D8A_REPORT_M = r'''static void copy_tail(const char *from, const char *to, long max) {
    FILE *in = fopen(from, "rb");
    if (!in) return;
    FILE *out = fopen(to, "wb");
    if (!out) { fclose(in); return; }
    fseek(in, 0, SEEK_END);
    long size = ftell(in);
    if (max > 0 && size > max) {
        fseek(in, size - max, SEEK_SET);
        int ch;
        while ((ch = fgetc(in)) != EOF && ch != '\n') {}     // start at a line
    } else fseek(in, 0, SEEK_SET);
    char buf[65536];
    size_t got;
    while ((got = fread(buf, 1, sizeof buf, in)) > 0) fwrite(buf, 1, got, out);
    fclose(in);
    fclose(out);
}
static const char *flow_name'''
assert hashlib.sha256(PIN_1F942D8A_REPORT_M.encode()).hexdigest() == '115f18a7371c7c8b057260fc4d707d3c7542029c5c9232ab044b5f2c22b2ab9c', 'pinned baseline changed'

PINNED_SOURCES = {
    ('13984ab7', 'report_privacy.h'): PIN_13984AB7_REPORT_PRIVACY_H,
    ('13984ab7', 'report.m'): PIN_13984AB7_REPORT_M,
    ('1f942d8a', 'report_privacy.h'): PIN_1F942D8A_REPORT_PRIVACY_H,
    ('1f942d8a', 'report.m'): PIN_1F942D8A_REPORT_M,
}

def pinned_source(revision, name):
    return PINNED_SOURCES[revision, name]


def run(name, header, *, baseline=False, expected_failure=False, fixture=None, report=None, copy_source=None, review=None, expected_counts=None):
    work = SCRATCH / name
    work.mkdir(parents=True, exist_ok=True)
    for file in ('report_github_test.m', 'report_github.m', 'report_github.h', 'portmenu.m', 'report_submit.m', 'report_submit.h'):
        shutil.copyfile(RUNTIME / file, work / file)
    (work / 'report_privacy.h').write_text(header)
    # Compile the actual log-copy function, including its truncation order, not a fixture substitute.
    report = report if report is not None else (RUNTIME / 'report.m').read_text()
    start = report.index('static void copy_tail(')
    end = report.index('static const char *flow_name', start)
    (work / 'report_copy.inc').write_text(copy_source or report[start:end])
    (work / 'report_privacy_test.m').write_text(fixture or (RUNTIME / 'report_privacy_test.m').read_text())
    profile = work / 'profile'
    if profile.exists():
        shutil.rmtree(profile)
    profile.mkdir()
    command = ['clang', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
               '-Wno-unused-variable', '-fsanitize=address,undefined', '-fobjc-arc', '-DNFS_PUBLIC=1',
               '-I' + str(ROOT / 'recomp'), '-I' + str(ROOT / 'recomp/out'), '-I' + str(RUNTIME),
               str(work / 'report_privacy_test.m'), str(RUNTIME / 'settings.c'), str(RUNTIME / 'text_fit.c'), str(RUNTIME / 'offline_stubs.c'), str(RUNTIME / 'replay_input.c'),
               '-framework', 'Cocoa', '-framework', 'CoreText', '-framework', 'ImageIO', '-framework', 'QuartzCore', '-framework', 'Metal',
               '-framework', 'SystemConfiguration', '-framework', 'IOKit', '-Wl,-dead_strip', '-o', str(work / 'check')]
    if baseline:
        command.insert(1, '-DREPORT_PRIVACY_BASELINE=1')
    compiled = subprocess.run(command, capture_output=True, text=True, timeout=90)
    (work / 'compile.log').write_text(compiled.stdout + compiled.stderr)
    assert compiled.returncode == 0, name + ' compile failed:\n' + compiled.stderr[-5000:]
    result = subprocess.run([str(work / 'check'), str(profile)], capture_output=True, text=True, timeout=60,
                            env=dict(os.environ, TMPDIR=str(work), ASAN_OPTIONS='detect_leaks=0'))
    (work / 'result.log').write_text(result.stdout + result.stderr)
    count = re.search(r'privacy: (\d+) checks, (\d+) failures', result.stdout)
    assert count, name + ' did not finish:\n' + result.stderr[-4000:]
    checks, failures = map(int, count.groups())
    print(f'{name}: {checks} checks, {failures} failures', flush=True)
    if expected_counts is not None:
        assert (checks, failures) == expected_counts, (name, checks, failures, expected_counts)
    if review is not None:
        actual = {name: (int(total), int(failed)) for name, total, failed in
                  re.findall(r'review (\w+): (\d+) checks, (\d+) failures', result.stdout)}
        assert actual == review, (name, actual, review)
        print(f'{name} review classes: {actual}', flush=True)
    if expected_failure:
        assert result.returncode == 1 and failures > 0, name + ' escaped detection'
    else:
        assert result.returncode == 0 and failures == 0, result.stderr[-5000:]
    return failures


def room_code_proof(header, premise=False):
    fixture=(RUNTIME/'report_privacy_test.m').read_text()
    start=fixture.index('    NSArray *rows = PrivacyCases();')
    end=fixture.index('    printf("privacy:')
    fixture=fixture[:start]+'    RoomCodeTests(root);\n'+fixture[end:]
    fixed=next(line for line in header.splitlines() if ')R-' in line)
    assert header.count(fixed)==1
    run('room_code_baseline',header.replace(fixed,ROOM_CODE_BASELINE),fixture=fixture,expected_failure=True,expected_counts=(25,12))
    if not premise: run('room_code_fixed',header,fixture=fixture,expected_counts=(25,0))


def main():
    SCRATCH.mkdir(parents=True, exist_ok=True)
    header = (RUNTIME / 'report_privacy.h').read_text()
    if '--prove-room-code-premise' in sys.argv:
        room_code_proof(header,premise=True)
        return
    room_code_proof(header)
    original = pinned_source('13984ab7', 'report_privacy.h').encode()
    assert hashlib.sha256(original).hexdigest() == ORIGINAL
    print('original SHA256 ' + ORIGINAL, flush=True)
    run('original', original.decode(), baseline=True, expected_failure=True,
        report=pinned_source('13984ab7', 'report.m'),
        expected_counts=(887, 830))
    if '--prove-original' in sys.argv:
        return
    # Recreate the reviewed predecessor by restoring only the three changed protections.
    # Both baselines use pinned source; even explicit proof runs need no repository history.
    predecessor_header = pinned_source(PREDECESSOR, 'report_privacy.h')
    predecessor_report = pinned_source(PREDECESSOR, 'report.m')
    assert all(line in predecessor_header for line in PREDECESSOR_RULES.values())
    assert PREDECESSOR_COPY in predecessor_report
    predecessor = header
    userinfo = next(line for line in header.splitlines() if line.endswith('// privacy:userinfo'))
    predecessor = predecessor.replace(userinfo, '    (void)text;')
    for protection, old in PREDECESSOR_RULES.items():
        fixed = next(line for line in header.splitlines() if line.endswith('// privacy:' + protection))
        assert fixed != old
        predecessor = predecessor.replace(fixed, old)
    review = {'userinfo': (3, 3), 'encoded_continuations': (16, 16), 'production_log_copy': (4, 1)}
    print('review predecessor ' + PREDECESSOR + ' protections', flush=True)
    run('review_predecessor', predecessor, expected_failure=True, report=predecessor_report, review=review,
        expected_counts=(893, 20))
    run('fixed', header, review={name: (total, 0) for name, (total, _) in review.items()}, expected_counts=(893, 0))
    mutants = {}
    assert header.count('NSString *text = ReportPrivacyDecodeData(data);') == 2
    mutants['mixed_encoding'] = header.replace('NSString *text = ReportPrivacyDecodeData(data);', LATIN1_BASELINE)
    for protection in ('userinfo', 'github', 'authorization', 'bearer', 'secret', 'path', 'identity', 'email', 'uuid', 'serial', 'wifi'):
        line = next(line for line in header.splitlines() if line.endswith('// privacy:' + protection))
        mutants[protection] = header.replace(line, '    (void)text;')
    for key in ('user', 'computer', 'serial', 'wifi'):
        old = '@[@"user", @"computer", @"serial", @"wifi"]'
        new = '@[' + ', '.join('@"' + other + '"' for other in ('user', 'computer', 'serial', 'wifi') if other != key) + ']'
        assert header.count(old) == 1
        mutants['known_' + key] = header.replace(old, new)
    for symbol in 'pousr':
        mutants['github_' + symbol] = header.replace('gh[pousr]_', 'gh[' + 'pousr'.replace(symbol, '') + ']_')
    mutants['github_pat'] = header.replace('|github_pat_', '|absent_pat_')
    for protection in ('percent', 'escape'):
        line = next(line for line in header.splitlines() if 'NSRegularExpression *' + protection + ' =' in line)
        mutants[protection] = header.replace(line, line.split('regularExpressionWithPattern:')[0] + 'regularExpressionWithPattern:@"(?!)"')
    mutants['bundle_identity'] = header.replace('text = ReportPrivacyIdentityRedact(text, identity);', '(void)identity;')
    line = next(line for line in header.splitlines() if line.endswith('// privacy:home'))
    mutants['home'] = header.replace(line, '    (void)home;')
    mutants['overlapping_fields'] = header.replace('@"(?=(%@))"', '@"(%@)"')
    mutants['decode_order'] = header.replace('text = ReportPrivateRules(decoded);', 'text = decoded;')
    mutants['text_extension'] = header.replace('name.pathExtension.lowercaseString', 'name.pathExtension')
    mutants['nesting_fail_closed'] = header.replace('return nil;\n}\n\n// Bundle', 'return text;\n}\n\n// Bundle')
    mutants['decode_fail_closed'] = header.replace('if (!bytes) return nil;', 'if (!bytes) return text;')
    mutants['write_fail_closed'] = header.replace('[fm removeItemAtPath:path error:NULL];', '(void)path;')
    # The broken controls are independent (own scratch dir and binary each); compile them four at a time.
    jobs = []
    for name, broken in mutants.items():
        assert broken != header, name + ' did not mutate production code'
        jobs.append(('broken_' + name, broken, dict(expected_failure=True,
                     expected_counts=(893, 3) if name == 'mixed_encoding' else None)))
    # Restore each partial-token rule independently without reverting other protections.
    for protection in ('github', 'bearer'):
        marker = '// privacy:' + protection
        fixed = next(line for line in header.splitlines() if line.endswith(marker))
        old = PREDECESSOR_RULES[protection]
        assert fixed != old
        jobs.append(('broken_encoded_' + protection, header.replace(fixed, old), dict(expected_failure=True)))
    jobs.append(('broken_log_copy', header, dict(copy_source=PREDECESSOR_COPY, expected_failure=True)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for future in [pool.submit(run, n, h, **kw) for n, h, kw in jobs]:
            future.result()
    # Force a production regex compilation error. A nil sanitizer must delete, never export, its input.
    line = next(line for line in header.splitlines() if '// privacy:github' in line)
    invalid = header.replace(line, '    text = ReportPrivacyReplace(text, @"[", @"[token]");')
    fixture = (RUNTIME / 'report_privacy_test.m').read_text()
    start = fixture.index('    NSArray *rows = PrivacyCases();')
    end = fixture.index('    printf("privacy:')
    fixture = fixture[:start] + '''    CHECK(ReportEndpointRedact(@"SyntheticRegexInput") == nil);
    for (NSString *file in @[@"report.txt", @"events.txt", @"log.txt", @"crash.txt", @"settings.txt", @"stats.txt"])
        CHECK([@"SyntheticRegexInput" writeToFile:[root stringByAppendingPathComponent:file] atomically:YES encoding:NSUTF8StringEncoding error:NULL]);
    ReportRedactBundle(root);
    for (NSString *file in @[@"report.txt", @"events.txt", @"log.txt", @"crash.txt", @"settings.txt", @"stats.txt"])
        CHECK(![NSFileManager.defaultManager fileExistsAtPath:[root stringByAppendingPathComponent:file]]);
''' + fixture[end:]
    run('regex_failure_closed', invalid, fixture=fixture)
    run('broken_regex_fail_closed', invalid.replace('if (!regex || error) return nil;', 'if (!regex || error) return text;'),
        fixture=fixture, expected_failure=True)
    print(f'PASS: shared report privacy; {len(mutants) + 4} independent broken controls rejected')


if __name__ == '__main__':
    main()
