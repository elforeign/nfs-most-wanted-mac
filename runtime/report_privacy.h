// Shared endpoint privacy for issue previews and every text file in a report bundle.
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
