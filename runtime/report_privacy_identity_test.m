// Identity forms the bundle redactor missed (synthetic identity, no real machine data).
// Build: clang -fobjc-arc -framework Foundation -framework IOKit -framework SystemConfiguration \
//          runtime/report_privacy_identity_test.m -o /tmp/report_privacy_identity_test && /tmp/report_privacy_identity_test <scratch dir>
// Main: 14 checks, 10 failures. Fixed: 14 checks, 0 failures.
#include "report_privacy.h"

static int checks, failures;
static void Expect(NSString *label, NSString *input, NSString *secret, NSString *keep, NSString *output) {
    ++checks;
    if (!output || [output rangeOfString:secret].location != NSNotFound ||
        (keep.length && [output rangeOfString:keep].location == NSNotFound)) {
        ++failures;
        fprintf(stderr, "LEAK %s: %s -> %s\n", label.UTF8String, input.UTF8String, output ? output.UTF8String : "(nil)");
    }
}

int main(int argc, char **argv) { @autoreleasepool {
    if (argc != 2) return 2;
    // Short name differs from the first name, as on many Macs; the Apple ID names the AirPods.
    NSDictionary *identity = @{@"home": @"/Volumes/TestHomes/tplayer", @"user": @[@"tplayer", @"Testa Player"],
        @"computer": @[@"Testa’s MacBook Pro", @"Testas-MacBook-Pro"], @"serial": @[], @"wifi": @[]};
    NSArray *lines = @[
        @[@"airpods", @"audio:      CoreAudio (playing, 0 underruns); macOS default output Testa’s AirPods Pro", @"Testa", @"AirPods Pro"],
        @[@"renamed_pad", @"controller: Testa's DualSense (DualSense), PlayStation layout", @"Testa", @"DualSense"],
        @[@"apple_id_name", @"audio: output device changed to Tess’s AirPods Max", @"Tess", @"AirPods Max"],
        @[@"iphone_mic", @"audio: input Testa Player’s iPhone Microphone", @"Player’s", @"iPhone"],
        @[@"reordered", @"owner: Player, Testa", @"Testa", @"owner: [redacted], [redacted]"],
        @[@"plus_joined", @"name=Testa+Player", @"Testa", @"name=[redacted]+[redacted]"],
        @[@"underscore", @"cache tplayer_backup ready", @"tplayer", @"ready"],
        @[@"capitals", @"TESTA PLAYER", @"TESTA", @""],
        // Ordinary words that equal a name word in another case must survive.
        @[@"ordinary_case", @"Met a player online", @"Testa", @"Met a player online"],
    ];
    for (NSArray *row in lines)
        Expect(row[0], row[1], row[2], row[3], ReportEndpointRedact(ReportPrivacyIdentityRedact(row[1], identity)));
    // UTF-16 files: with and without a byte-order mark, little and big endian.
    NSString *root = [NSString stringWithUTF8String:argv[1]];
    NSString *text = @"log: café /Volumes/TestHomes/tplayer/Library and Testa Player on Testas-MacBook-Pro\n";
    NSArray *encodings = @[@(NSUnicodeStringEncoding), @(NSUTF16LittleEndianStringEncoding), @(NSUTF16BigEndianStringEncoding)];
    for (NSNumber *encoding in encodings) {
        NSString *path = [root stringByAppendingPathComponent:[NSString stringWithFormat:@"utf16-%@.txt", encoding]];
        if (![[text dataUsingEncoding:encoding.unsignedIntegerValue] writeToFile:path atomically:YES]) return 2;
    }
    NSString *mixed = [root stringByAppendingPathComponent:@"mixed.txt"];
    const char mixedBytes[] = "log: tpla\0yer kept alongside ordinary diagnostic text\n";
    if (![[NSData dataWithBytes:mixedBytes length:sizeof mixedBytes - 1] writeToFile:mixed atomically:YES]) return 2;
    ReportPrivacyRedactBundle(root, identity);
    NSString *mixedOutput = [NSString stringWithContentsOfFile:mixed encoding:NSUTF8StringEncoding error:NULL];
    unichar nul = 0;
    mixedOutput = [mixedOutput stringByReplacingOccurrencesOfString:[NSString stringWithCharacters:&nul length:1] withString:@""];
    Expect(@"mixed_nul", @"(mixed text file)", @"tplayer", @"ordinary diagnostic text", mixedOutput);
    for (NSNumber *encoding in encodings) {
        NSString *path = [root stringByAppendingPathComponent:[NSString stringWithFormat:@"utf16-%@.txt", encoding]];
        NSData *data = [NSData dataWithContentsOfFile:path];
        NSMutableData *plain = [NSMutableData data];   // NULs removed, so interleaved UTF-16 reads as ASCII
        for (NSUInteger i = 0; i < data.length; ++i)
            if (((const char *)data.bytes)[i]) [plain appendBytes:(const char *)data.bytes + i length:1];
        NSString *out = [[NSString alloc] initWithData:plain encoding:NSUTF8StringEncoding];
        if (!out && data) out = [[NSString alloc] initWithData:plain encoding:NSISOLatin1StringEncoding];
        Expect([NSString stringWithFormat:@"utf16_%@", encoding], @"(UTF-16 file)", @"tplayer", @"café", out);
    }
    // Keep ordinary device names intact.
    Expect(@"ordinary_device", @"default output Generic Display", @"[", @"Generic Display",
           ReportEndpointRedact(ReportPrivacyIdentityRedact(@"default output Generic Display", identity)));
    printf("report identity forms: %d checks, %d failures\n", checks, failures);
    return failures ? 1 : 0;
} }
