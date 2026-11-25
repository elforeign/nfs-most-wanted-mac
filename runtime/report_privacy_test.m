// Device name parts: main 22 checks/15 failures; fixed 22/0.
// Synthetic payloads only; paired driver records original, fixed and independent broken controls.
// Compact mistyped room codes: pinned rule 25 checks/12 failures; fixed 25/0; ordinary words preserved.
// Proof: pinned original 1003 checks/941 failures; review predecessor 1009/20; fixed 1009/0.
// Both destination previews covered; all 36 independent broken controls rejected; regex failure 16/0.
// Userinfo 3/3 -> 3/0; encoded continuations 16/16 -> 16/0; production log copy 4/1 -> 4/0.
// The Prefix(sanitized, cap) assertion is equivalence evidence, not log-copy regression proof.
// Mixed Latin-1 note/UTF-8 identity: pinned decoder 1009 checks/3 failures; fixed 1009/0.
// Public report targets: Carbon base 5 checks/4 failures, fixed 12/0; Most Wanted base and fixed 10/0.
#define main ReportGithubFixtureMain
#include "report_github_test.m"
#undef main

#ifdef REPORT_TARGET_PROBE
int main(int argc, char **argv) { @autoreleasepool {
    if (argc != 2) return 2;
    NSString *repo = NFS_GAME == NFS_GAME_MW ? @"elforeign/nfs-most-wanted-mac" : @"elforeign/nfs-carbon-mac";
    NSString *newIssue = [NSString stringWithFormat:@"https://github.com/%@/issues/new", repo];
    NSString *createdIssue = [NSString stringWithFormat:@"https://github.com/%@/issues/42", repo];
    Setting *action = settings_find("diag.report_github");
    CHECK(action && action->type == SET_ACTION && action->category == SET_DIAGNOSTICS &&
          !action->hidden && !strcmp(action->label, "Send to GitHub"));
    BOOL reviewed = NO, sent = NO;
#ifndef REPORT_TARGET_NO_UNIT
    if (action) {
        fixture = [NSString stringWithUTF8String:argv[1]];
        if (![Summary() writeToFile:[fixture stringByAppendingPathComponent:@"report.txt"]
                           atomically:YES encoding:NSUTF8StringEncoding error:NULL]) return 2;
        openOrder = NSMutableArray.array; drawnText = NSMutableArray.array;
        cliPending = NSMutableArray.array; cliArguments = NSMutableArray.array; cliInputs = NSMutableArray.array;
        submissionRunner = FakeCLI();
        action->apply(0);
        DrawReview();
        NSString *labels = [drawnText componentsJoinedByString:@"\n"];
        reviewed = report_github_active() && confirmSelected == 0 && !screenshotOn &&
                   !cliCalls && !browserOpens && [labels containsString:repo];
        Click(350, 560);
        action->apply(0);
        Click(100, 560);
        FinishCLI(0, @"fixture-account", NO);
        Click(100, 560);
        FinishCLI(0, @"fixture-account", NO);
        NSString *endpoint = [NSString stringWithFormat:@"repos/%@/issues", repo];
        sent = [cliArguments.lastObject containsObject:endpoint] &&
               !ReportValidIssueURL(NFS_GAME == NFS_GAME_MW ?
                   @"https://github.com/elforeign/nfs-carbon-mac/issues/42" :
                   @"https://github.com/elforeign/nfs-most-wanted-mac/issues/42");
        FinishCLI(0, createdIssue, NO);
        sent = sent && submission.state == ReportSubmitCreated && [submission.issueURL isEqual:createdIssue];
        if (NFS_GAME != NFS_GAME_MW) {
            report_github_key(0x27);
            report_github_key(0x27);
            report_github_key(0x27);
            CHECK(confirmSelected == 4 && ![labels containsString:@"Post on Discord"]);
            int opens = browserOpens;
            Click(650, 560);
            CHECK(browserOpens == opens && !discordPreview);
        }
    }
#else
    (void)argv;
    (void)createdIssue;
#endif
    CHECK(reviewed);
    CHECK([openedURL.absoluteString isEqual:newIssue]);
    CHECK(sent);
    CHECK((settings_find("diag.report_discord") != NULL) == (NFS_GAME == NFS_GAME_MW));
    printf("report target: %d checks, %d failures\n", checks, failures);
    return failures ? 1 : 0;
} }
#else
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

// macOS names Bluetooth and Continuity devices after the first name alone, and the short name differs here.
// The audio and display rows reach the public issue body and report.txt.
static void NamePartTests(NSString *root) {
    int beforeChecks = checks, beforeFailures = failures;
    NSDictionary *idn = @{@"home":@"/Volumes/TestHomes/synthuser", @"user":@[@"synthuser", @"Synthia Fixtureson"],
        @"computer":@[], @"serial":@[], @"wifi":@[]};
    for (NSString *device in @[@"Synthia\u2019s AirPods Pro", @"Synthia's iPhone", @"iPhone de Synthia", @"Synthias iPad",
                               @"Fixtureson\u2019s Speaker"]) {
        NSString *summary = [Summary() stringByReplacingOccurrencesOfString:@"Built-in speakers" withString:device];
        summary = [summary stringByReplacingOccurrencesOfString:@"3840x2160, 120 Hz"
                                                      withString:[device stringByAppendingString:@", 3840x2160"]];
        for (NSNumber *discord in @[@NO, @YES]) {
            NSString *body = BuildSnapshot(summary, nil, nil, idn, discord.boolValue)[@"body"];
            CHECK(body && ![body containsString:@"Synthia"] && ![body containsString:@"Fixtureson"] && [body containsString:@"Example pad"] && [body containsString:@"3840x2160"] && [body containsString:@"audio: PCM,"]);
        }
        NSString *path = [root stringByAppendingPathComponent:@"report.txt"];
        CHECK([summary writeToFile:path atomically:YES encoding:NSUTF8StringEncoding error:NULL]);
        ReportPrivacyRedactBundle(root, idn);
        NSString *out = [NSString stringWithContentsOfFile:path encoding:NSUTF8StringEncoding error:NULL];
        CHECK(out && ![out containsString:@"Synthia"] && ![out containsString:@"Fixtureson"] && [out containsString:@"controller: Example pad"]);
    }
    // Initials are not redacted on their own.
    CHECK([ReportPrivacyIdentityRedact(@"S mode, 5 s", @{@"user":@[@"synthuser", @"S. Fixtureson"]}) isEqual:@"S mode, 5 s"]);
    printf("review name_parts: %d checks, %d failures\n", checks - beforeChecks, failures - beforeFailures);
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
        @[@"known_user", @"Synthia Fixtureson", @"Synthia Fixtureson"],
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
        for (NSString *name in @[@"report.txt", @"events.txt", @"log.txt", @"crash.txt", @"settings.txt", @"stats.txt", @"blackbox.txt"])
            CHECK([input writeToFile:[root stringByAppendingPathComponent:name] atomically:YES encoding:NSUTF8StringEncoding error:NULL]);
        ReportRedactBundle(root);
        for (NSString *name in @[@"report.txt", @"events.txt", @"log.txt", @"crash.txt", @"settings.txt", @"stats.txt", @"blackbox.txt"])
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
    NSArray *files = @[@"report.txt", @"events.txt", @"log.txt", @"crash.txt", @"settings.txt", @"stats.txt", @"blackbox.txt"];
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
#ifdef REPORT_PRIVACY_BASELINE
    CHECK(custom && ![custom containsString:@"SyntheticCustomHome"] && [custom containsString:@"~/log.txt"]);
#else
    CHECK([custom isEqual:@"[path]"]);
#endif
    FailureTests(root);
    printf("privacy: %d checks, %d failures\n", checks, failures);
    return failures ? 1 : 0;
} }
#endif
