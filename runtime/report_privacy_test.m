// Synthetic payloads only; paired driver records original, fixed and independent broken controls.
// Compact mistyped room codes: pinned rule 25 checks/12 failures; fixed 25/0; ordinary words preserved.
// Proof: pinned original 887 checks/830 failures; review predecessor 893/20; fixed 893/0.
// Both destination previews covered; all 36 independent broken controls rejected; regex failure 14/0.
// Userinfo 3/3 -> 3/0; encoded continuations 16/16 -> 16/0; production log copy 4/1 -> 4/0.
// The Prefix(sanitized, cap) assertion is equivalence evidence, not log-copy regression proof.
// Mixed Latin-1 note/UTF-8 identity: pinned decoder 893 checks/3 failures; fixed 893/0.
#define main ReportGithubFixtureMain
#include "report_github_test.m"
#undef main
#import <objc/runtime.h>
#include "report_copy.inc"

#ifndef REPORT_PRIVACY_BASELINE
static void RoomCodeTests(NSString *root) {
    for (NSString *code in @[@"R-ABCDEFGHJ", @"r-abcdefghj", @"R-ABCD-EFGHJ", @"R-ABCD0FGHI"]) {
        NSString *input=[NSString stringWithFormat:@"Met a player in %@ today.",code];
        NSString *expected=@"Met a player in [join code] today.";
        CHECK([ReportEndpointRedact(input) isEqual:expected]);
        for (NSNumber *discord in @[@NO,@YES]) {
            NSString *summary=[Summary() stringByReplacingOccurrencesOfString:@"Sound cuts out" withString:input];
            NSString *body=BuildSnapshot(summary,nil,nil,Identity(),discord.boolValue)[@"body"];
            CHECK(body && ![body containsString:code] && [body containsString:@"[join code]"]);
        }
        NSString *path=[root stringByAppendingPathComponent:@"note.txt"];
        CHECK([input writeToFile:path atomically:YES encoding:NSUTF8StringEncoding error:NULL]);
        ReportPrivacyRedactBundle(root,Identity());
        CHECK([[NSString stringWithContentsOfFile:path encoding:NSUTF8StringEncoding error:NULL] isEqual:expected]);
    }
    // Ordinary prose and longer tokens must survive; the R- prefix and exact code length matter.
    for (NSString *input in @[@"rendering reference reconnecting multiplayer controller ordinary words",
                             @"A renderer rendered an ordinary frame.",
                             @"preR-ABCDEFGHJ R-ABCDEFGHJtail R-ABCD-EFGHJK",
                             @"R-ABC R-ABCD R-ABCDEFGH R-ABCDEFGHJK"]) {
        CHECK([ReportEndpointRedact(input) isEqual:input]);
    }
}

#endif

static void ReviewRegressionTests(NSString *root) {
    int beforeChecks = checks, beforeFailures = failures;
    // URL userinfo must disappear before endpoint substitution, for both local and named hosts.
    for (NSString *input in @[@"http://user:SyntheticPasswordValue@127.0.0.1/",
                             @"https://user:SyntheticPasswordValue@example.invalid/path",
                             @"https://user:Synthetic%50asswordValue@example.invalid/"]) {
        NSString *out = ReportEndpointRedact(input);
        CHECK(out && ![out containsString:@"SyntheticPasswordValue"]);
    }
    printf("review userinfo: %d checks, %d failures\n", checks - beforeChecks, failures - beforeFailures);
    beforeChecks = checks; beforeFailures = failures;
    // Prefix rules must consume encoded continuations before removing the recognizable prefix.
    for (NSString *prefix in @[@"ghp_SYNTHETIC", @"github_pat_SYNTHETIC", @"Bearer SYNTHETIC", @"Basic SYNTHETIC"])
        for (NSString *suffix in @[@"%54okenSuffix", @"%2554okenSuffix", @"\\u0054okenSuffix", @"%5Cu0054okenSuffix"]) {
            NSString *out = ReportEndpointRedact([prefix stringByAppendingString:suffix]);
            CHECK(out && ![out containsString:@"TokenSuffix"] && ![out containsString:@"okenSuffix"]);
        }
    printf("review encoded_continuations: %d checks, %d failures\n", checks - beforeChecks, failures - beforeFailures);
    beforeChecks = checks; beforeFailures = failures;
    // Exercise production copy_tail with the same cap as report_write. The label falls outside
    // the old byte tail, while the quoted continuation and closing quote fall inside it.
    NSString *source = [root stringByAppendingPathComponent:@"source.log"];
    NSString *dest = [root stringByAppendingPathComponent:@"copied.txt"];
    NSString *head = [@"x" stringByPaddingToLength:256 * 1024 + 128 withString:@"x" startingAtIndex:0];
    NSString *input = [NSString stringWithFormat:@"password=\"%@\nSyntheticCutoffContinuation\"\nbuild: fixture\n", head];
    CHECK([input writeToFile:source atomically:YES encoding:NSUTF8StringEncoding error:NULL]);
    copy_tail(source.fileSystemRepresentation, dest.fileSystemRepresentation, 256 * 1024);
    ReportRedactBundle(root);
    NSString *out = [NSString stringWithContentsOfFile:dest encoding:NSUTF8StringEncoding error:NULL];
    CHECK(out && ![out containsString:@"SyntheticCutoffContinuation"]);
    CHECK([out containsString:@"build: fixture"]);
    CHECK([out lengthOfBytesUsingEncoding:NSUTF8StringEncoding] <= 256 * 1024);
    printf("review production_log_copy: %d checks, %d failures\n", checks - beforeChecks, failures - beforeFailures);
}

#ifndef REPORT_PRIVACY_BASELINE
static void MixedEncodingTests(NSString *root) {
    // Report notes are Latin-1 bytes; device strings in the same file are UTF-8.
    NSDictionary *identity = @{ @"computer": @[@"Zoë", @"テスト🎮"] };
    NSString *path = [root stringByAppendingPathComponent:@"report.txt"];
    NSMutableData *input = [NSMutableData dataWithData:[@"note: café\n" dataUsingEncoding:NSISOLatin1StringEncoding]];
    [input appendData:[@"controller: Zoë / テスト🎮\nbuild: fixture\n" dataUsingEncoding:NSUTF8StringEncoding]];
    CHECK([input writeToFile:path atomically:YES]);
    ReportPrivacyRedactBundle(root, identity);
    NSString *out = SavedText(root, @"report.txt", 128 * 1024);
    CHECK(out && [out containsString:@"café"] && [out containsString:@"build: fixture"]);
    CHECK(out && ![out containsString:@"Zoë"] && ![out containsString:@"ZoÃ«"]);
    CHECK(out && ![out containsString:@"テスト🎮"] && [out containsString:@"[redacted]"]);
    NSString *body = BuildSnapshot(out, nil, nil, identity, NO)[@"body"];
    CHECK(body && ![body containsString:@"Zoë"] && ![body containsString:@"ZoÃ«"]);
    CHECK(body && ![body containsString:@"テスト🎮"] && [body containsString:@"café"]);
}
#endif

// Each row names a protection, its input, and the exact value forbidden in public output.
static NSArray *PrivacyCases(void) {
    NSMutableArray *rows = [NSMutableArray array];
    for (NSString *prefix in @[@"ghp_", @"gho_", @"ghu_", @"ghs_", @"ghr_", @"github_pat_"]) {
        NSString *token = [prefix stringByAppendingString:@"SYNTHETIC0123456789abcdef0123456789abcd"];
        [rows addObject:@[@"github", token, token]];
    }
    [rows addObjectsFromArray:@[
        @[@"authorization", @"Authorization: Bearer SyntheticBearerValue", @"SyntheticBearerValue"],
        @[@"authorization", @"Bearer SyntheticStandaloneValue", @"SyntheticStandaloneValue"],
        @[@"authorization", @"Proxy-Authorization: Basic U3ludGhldGljOnNlY3JldA==", @"U3ludGhldGljOnNlY3JldA"],
        @[@"authorization", @"{\"Authorization\":\"token SyntheticAuthValue\"}", @"SyntheticAuthValue"],
        @[@"authorization", @"Authorization: Digest username=\"SyntheticDigestPerson\", realm=\"SyntheticRealm\"", @"SyntheticRealm"],
        @[@"secret", @"api_key=SyntheticApiValue", @"SyntheticApiValue"],
        @[@"secret", @"{\"access_token\":\"Synthetic Access Value\"}", @"Synthetic Access Value"],
        @[@"secret", @"client-secret='Synthetic Client Value'", @"Synthetic Client Value"],
        @[@"secret", @"password: SyntheticPasswordValue", @"SyntheticPasswordValue"],
        @[@"secret", @"GH_TOKEN=SyntheticEnvValue", @"SyntheticEnvValue"],
        @[@"secret", @"refresh-token=\"Synthetic Refresh Value\"", @"Synthetic Refresh Value"],
        @[@"secret", @"api_key=\"Synthetic Head\\\"SyntheticTail\"", @"SyntheticTail"],
        @[@"secret", @"password=Unquoted Synthetic Phrase", @"Synthetic Phrase"],
        @[@"secret", @"password=\"Unterminated Synthetic Phrase", @"Synthetic Phrase"],
        @[@"secret", @"password=\"Multiline\nSyntheticMultilineTail\"", @"SyntheticMultilineTail"],
        @[@"secret", @"AWS_SECRET_ACCESS_KEY=SyntheticAWSValue", @"SyntheticAWSValue"],
        @[@"secret", @"x-api-key=SyntheticHeaderValue", @"SyntheticHeaderValue"],
        @[@"secret", @"private_key='SyntheticPrivateKey'", @"SyntheticPrivateKey"],
        @[@"path", @"/Volumes/SyntheticDisk/PrivateFolder/report.txt", @"PrivateFolder"],
        @[@"path", @"\"/Volumes/Synthetic Disk/Private Folder/report.txt\"", @"Private Folder"],
        @[@"path", @"\"/Volumes/Synthetic\\\"Disk/QuotedPrivateFolder/report.txt\"", @"QuotedPrivateFolder"],
        @[@"path", @"'C:\\Users\\SyntheticPerson\\PrivateFolder\\log.txt'", @"SyntheticPerson"],
        @[@"path", [@[@"C:", @"Users", @"SyntheticPerson", @"PrivateFolder", @"log.txt"] componentsJoinedByString:@"/"], @"SyntheticPerson"],
        @[@"path", @"\\\\SyntheticServer\\PrivateShare\\log.txt", @"PrivateShare"],
        @[@"path", @"file:///Volumes/SyntheticDisk/PrivateFolder/log.txt", @"PrivateFolder"],
        @[@"path", @"file://SyntheticServer/PrivateShare/log.txt", @"PrivateShare"],
        @[@"path", [NSString pathWithComponents:@[@"/", @"Users", @"SyntheticPerson", @"PrivateFolder", @"log.txt"]], @"SyntheticPerson"],
        @[@"path", [NSString pathWithComponents:@[@"/", @"home", @"SyntheticPerson", @"PrivateFolder", @"log.txt"]], @"SyntheticPerson"],
        @[@"path", [NSString pathWithComponents:@[@"/", @"private", @"var", @"folders", @"SyntheticShard", @"PrivateFolder", @"log.txt"]], @"PrivateFolder"],
        @[@"escape", @"{\\\"api_key\\\":\\\"SyntheticEscapedValue\\\"}", @"SyntheticEscapedValue"],
        @[@"escape", @"{\"path\":\"C:\\\\Users\\\\SyntheticEscapedPerson\\\\PrivateFolder\"}", @"SyntheticEscapedPerson"],
        @[@"escape", @"\\/Volumes\\/SyntheticDisk\\/EscapedFolder\\/log.txt", @"EscapedFolder"],
        @[@"escape", @"\\u0067hp_SYNTHETIC0123456789abcdef0123456789abcd", @"SYNTHETIC0123456789abcdef0123456789abcd"],
        @[@"escape", @"{\\\"api_key\\\":\\\"SyntheticHead\\\\\\\"SyntheticEscapedTail\\\"}", @"SyntheticEscapedTail"],
        @[@"percent", @"api%5Fkey%3DPercentSecretValue", @"PercentSecretValue"],
        @[@"percent", @"file%3A%2F%2F%2FVolumes%2FSyntheticDisk%2FPercentFolder%2Flog.txt", @"PercentFolder"],
        @[@"percent", @"%252FVolumes%252FSyntheticDisk%252FNestedFolder%252Flog.txt", @"NestedFolder"],
        @[@"percent", @"Authorization%3A%20Bearer%20PercentBearerValue", @"PercentBearerValue"],
        @[@"percent", @"%67hp_SYNTHETICEncoded0123456789abcdef0123456789abcd", @"SYNTHETICEncoded0123456789abcdef0123456789abcd"],
        @[@"percent", @"C%3A%5CUsers%5CSyntheticEncodedPerson%5CPrivateFolder%5Clog.txt", @"SyntheticEncodedPerson"],
        @[@"percent", @"%5C%5CSyntheticEncodedServer%5CEncodedShare%5Clog.txt", @"EncodedShare"],
        @[@"percent", @"file:///Volumes/Synthetic%20Disk/EncodedFolder/log.txt", @"EncodedFolder"]
    ]];
    return rows;
}

static NSArray *BundleIdentityCases(void) {
    return @[@[@"email", @"josé@例え.invalid", @"josé"],
        @[@"uuid", @"12345678-abcd-1234-5678-123456789abc", @"12345678-abcd"],
        @[@"serial", @"serial=SERIALSYNTHETIC", @"SERIALSYNTHETIC"],
        @[@"wifi", @"SSID=\"Synthetic Other Network\"", @"Synthetic Other Network"],
        @[@"known_home", @"/Volumes/TestHomes/playerone/file", @"playerone"],
        @[@"known_user", @"Example Player", @"Example Player"],
        @[@"known_computer", @"My Test Mac", @"My Test Mac"],
        @[@"known_serial", @"C02TEST12345", @"C02TEST12345"],
        @[@"known_wifi", @"Secret Network", @"Secret Network"]];
}

static void LeakCheck(NSString *out, NSArray *row, NSString *destination) {
    ++checks;
    if (!out || [out containsString:row[2]]) {
        ++failures;
        fprintf(stderr, "LEAK %s/%s: surviving value %s; shared result %s\n", destination.UTF8String, [row[0] UTF8String],
            [row[2] UTF8String], [destination isEqual:@"shared"] ? (out ?: @"<nil>").UTF8String : "<see fixture>");
    }
}

static BOOL PrivacyFailWrite(id object, SEL selector, NSString *path, BOOL atomically,
                             NSStringEncoding encoding, NSError **error) {
    (void)object; (void)selector; (void)path; (void)atomically; (void)encoding; (void)error;
    return NO;
}

static void FailureTests(NSString *root) {
    BOOL nilClosed = NO;
    @try { nilClosed = ReportEndpointRedact(nil) == nil; }
    @catch (NSException *exception) { (void)exception; }
    CHECK(nilClosed);
    NSString *deep = @"%2FVolumes%2FSyntheticDisk%2FDeepFolder%2Flog.txt";
    for (unsigned i = 0; i < 20; ++i) deep = [deep stringByReplacingOccurrencesOfString:@"%" withString:@"%25"];
    for (NSString *input in @[deep, @"api%5Fkey=%FF"]) {
        CHECK(ReportEndpointRedact(input) == nil);
        for (NSString *name in @[@"report.txt", @"events.txt", @"log.txt", @"crash.txt", @"settings.txt", @"stats.txt"])
            CHECK([input writeToFile:[root stringByAppendingPathComponent:name] atomically:YES encoding:NSUTF8StringEncoding error:NULL]);
        ReportRedactBundle(root);
        for (NSString *name in @[@"report.txt", @"events.txt", @"log.txt", @"crash.txt", @"settings.txt", @"stats.txt"])
            CHECK(![NSFileManager.defaultManager fileExistsAtPath:[root stringByAppendingPathComponent:name]]);
    }
    NSString *path = [root stringByAppendingPathComponent:@"failed-write.txt"];
    CHECK([@"api_key=SyntheticWriteSecret" writeToFile:path atomically:YES encoding:NSUTF8StringEncoding error:NULL]);
    Method writer = class_getInstanceMethod(NSString.class, @selector(writeToFile:atomically:encoding:error:));
    IMP saved = method_setImplementation(writer, (IMP)PrivacyFailWrite);
    ReportRedactBundle(root);
    method_setImplementation(writer, saved);
    CHECK(![NSFileManager.defaultManager fileExistsAtPath:path]);
    // A failed read/conversion and a case-varied text extension must receive the same protection.
    path = [root stringByAppendingPathComponent:@"unreadable.txt"];
    CHECK([NSFileManager.defaultManager createDirectoryAtPath:path withIntermediateDirectories:NO attributes:nil error:NULL]);
    ReportRedactBundle(root);
    CHECK(![NSFileManager.defaultManager fileExistsAtPath:path]);
    path = [root stringByAppendingPathComponent:@"extra.TXT"];
    CHECK([@"api_key=SyntheticUppercaseSecret" writeToFile:path atomically:YES encoding:NSUTF8StringEncoding error:NULL]);
    ReportRedactBundle(root);
    NSString *out = [NSString stringWithContentsOfFile:path encoding:NSUTF8StringEncoding error:NULL];
    CHECK(out && ![out containsString:@"SyntheticUppercaseSecret"]);
    path = [root stringByAppendingPathComponent:@"latin.txt"];
    CHECK([@"café api_key=SyntheticLatinSecret\n3840x2160" writeToFile:path atomically:YES encoding:NSISOLatin1StringEncoding error:NULL]);
    ReportRedactBundle(root);
    out = [NSString stringWithContentsOfFile:path encoding:NSUTF8StringEncoding error:NULL];
    CHECK(out && [out containsString:@"café"] && [out containsString:@"3840x2160"] && ![out containsString:@"SyntheticLatinSecret"]);
}

int main(int argc, char **argv) { @autoreleasepool {
    CHECK(argc == 2);
    NSString *root = [NSString stringWithUTF8String:argv[1]];
    NSArray *rows = PrivacyCases();
    ReviewRegressionTests(root);
#ifndef REPORT_PRIVACY_BASELINE
    MixedEncodingTests(root);
#endif
    NSString *ordinary = @"build: 4.0.0 13984ab7 speed.exe 012345abcdef\naudio.output=1 NFS_HDR_OUTPUT=off\n60 fps, Mac16,1, 3840x2160 120 Hz, GPU 4.0 ms\n07:44:26.020 DualSense café 🎮 100% ready\nprofile: normal, file: report.txt, password prompt, https://example.invalid/guide";
    for (NSArray *row in rows) LeakCheck(ReportEndpointRedact(row[1]), row, @"shared");
    CHECK([ReportEndpointRedact(ordinary) isEqual:ordinary]);
    CHECK([ReportEndpointRedact(@"\"/Volumes/Synthetic Disk/log.txt\"; build: 13984ab7, GPU 4.0 ms")
        containsString:@"build: 13984ab7, GPU 4.0 ms"]);
    CHECK([ReportEndpointRedact(@"UnlabelledSyntheticCredential") isEqual:@"UnlabelledSyntheticCredential"]);
    // Test actual issue construction, including a quoted secret spanning the truncation boundary.
    for (NSArray *row in rows) {
        NSString *summary = [Summary() stringByReplacingOccurrencesOfString:@"Sound cuts out" withString:row[1]];
        for (NSNumber *discord in @[@NO, @YES])
            LeakCheck(BuildSnapshot(summary, nil, Events(), Identity(), discord.boolValue)[@"body"], row, discord.boolValue ? @"Discord preview" : @"GitHub preview");
    }
    NSString *longSecret = [@"SyntheticLongSecret" stringByPaddingToLength:9000 withString:@"x" startingAtIndex:0];
    NSString *summary = [Summary() stringByReplacingOccurrencesOfString:@"Sound cuts out" withString:
        [NSString stringWithFormat:@"api_key=\"%@\"", longSecret]];
    for (NSNumber *discord in @[@NO, @YES]) {
        NSDictionary *issue = BuildSnapshot(summary, nil, Events(), Identity(), discord.boolValue);
        CHECK(![issue[@"body"] containsString:@"SyntheticLongSecret"]);
        CHECK([issue[@"body"] containsString:@"4.0.0 fixture"]);
        CHECK([issue[@"body"] length] <= (discord.boolValue ? 2000u : 10000u));
    }
    // Equivalence evidence for already-sanitized preview text; production log proof is above.
    CHECK(![Prefix(ReportEndpointRedact([NSString stringWithFormat:@"password=\"%@\"", longSecret]), 120)
        containsString:@"SyntheticLongSecret"]);
    NSMutableArray *all = [rows mutableCopy]; [all addObjectsFromArray:BundleIdentityCases()];
    NSMutableArray *values = [NSMutableArray arrayWithObject:ordinary];
    for (NSArray *row in all) [values addObject:row[1]];
    NSString *payload = [values componentsJoinedByString:@"\n"];
    NSArray *files = @[@"report.txt", @"events.txt", @"log.txt", @"crash.txt", @"settings.txt", @"stats.txt"];
    for (NSString *file in files) CHECK([payload writeToFile:[root stringByAppendingPathComponent:file]
        atomically:YES encoding:NSUTF8StringEncoding error:NULL]);
#ifdef REPORT_PRIVACY_BASELINE
    ReportRedactBundle(root);
#else
    ReportPrivacyRedactBundle(root, Identity());
#endif
    for (NSString *file in files) {
        NSString *out = [NSString stringWithContentsOfFile:[root stringByAppendingPathComponent:file]
            encoding:NSUTF8StringEncoding error:NULL];
        CHECK([out containsString:ordinary]);
        for (NSArray *row in all) LeakCheck(out, row, file);
    }
    // Exercise the unchanged production entry point on each attachment as well.
    for (NSString *file in files) [payload writeToFile:[root stringByAppendingPathComponent:file]
        atomically:YES encoding:NSUTF8StringEncoding error:NULL];
    ReportRedactBundle(root);
    for (NSString *file in files) {
        NSString *out = [NSString stringWithContentsOfFile:[root stringByAppendingPathComponent:file]
            encoding:NSUTF8StringEncoding error:NULL];
        CHECK([out containsString:ordinary]);
        for (NSArray *row in rows) LeakCheck(out, row, @"production bundle");
        for (NSArray *row in [BundleIdentityCases() subarrayWithRange:NSMakeRange(0, 4)]) LeakCheck(out, row, @"production identity");
    }
    // A configured home need not live under one of the recognized personal path roots.
    NSString *customPath = [root stringByAppendingPathComponent:@"custom-home.txt"];
    CHECK([@"/srv/SyntheticCustomHome/log.txt" writeToFile:customPath atomically:YES encoding:NSUTF8StringEncoding error:NULL]);
#ifdef REPORT_PRIVACY_BASELINE
    ReportRedactBundle(root);
#else
    ReportPrivacyRedactBundle(root, @{@"home":@"/srv/SyntheticCustomHome"});
#endif
    NSString *custom = [NSString stringWithContentsOfFile:customPath encoding:NSUTF8StringEncoding error:NULL];
    CHECK(custom && ![custom containsString:@"SyntheticCustomHome"] && [custom containsString:@"~/log.txt"]);
    FailureTests(root);
    printf("privacy: %d checks, %d failures\n", checks, failures);
    return failures ? 1 : 0;
} }
