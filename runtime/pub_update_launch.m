// Launch-only update transfer. No network or roots/preferences reads belong here.
// A prepared job retains its verified extraction at source/ and python/ until
// apply.py has consumed its journal. Both old and new apps use this same hook.
#import <Cocoa/Cocoa.h>
#include <sys/stat.h>
#include <sys/acl.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>
#include <CommonCrypto/CommonDigest.h>
#include "pub_update_write.h"

extern const char *game_dir;  // assigned only after app_start validates the PC root

static NSDictionary *UpdateJSON(NSString *path) {
    // The ready index and journals are small. Never follow a descriptor link or
    // allocate an unbounded file on the ordinary startup path.
    int fd = open(path.fileSystemRepresentation, O_RDONLY | O_NOFOLLOW | O_NONBLOCK);
    if (fd < 0) return nil;
    struct stat st;
    if (fstat(fd, &st) || !S_ISREG(st.st_mode) || st.st_size < 0 || st.st_size > 16384) { close(fd); return nil; }
    NSMutableData *data = [NSMutableData dataWithLength:(NSUInteger)st.st_size];
    size_t offset = 0;
    while (offset < data.length) {
        ssize_t count = read(fd, (char *)data.mutableBytes + offset, data.length - offset);
        if (count <= 0) { close(fd); return nil; }
        offset += (size_t)count;
    }
    close(fd);
    id value = data ? [NSJSONSerialization JSONObjectWithData:data options:0 error:nil] : nil;
    return [value isKindOfClass:NSDictionary.class] ? value : nil;
}
static BOOL PlainDirectory(NSString *path) {
    char resolved[PATH_MAX];
    struct stat st;
    return lstat(path.fileSystemRepresentation, &st) == 0 && S_ISDIR(st.st_mode) &&
        realpath(path.fileSystemRepresentation, resolved) && !strcmp(resolved, path.fileSystemRepresentation);
}
static NSString *CanonicalPath(NSString *path) {
    // Foundation standardization can remove /private while descriptor paths
    // use realpath. Compare the same filesystem spelling on both sides.
    char resolved[PATH_MAX];
    return realpath(path.fileSystemRepresentation, resolved) ? [NSString stringWithUTF8String:resolved] : path;
}

static BOOL SecurePath(NSString *path, BOOL privateDirectory) {
    struct stat st;
    if (lstat(path.fileSystemRepresentation, &st) || st.st_uid != geteuid() ||
        (!S_ISDIR(st.st_mode) && !S_ISREG(st.st_mode)) ||
        (st.st_mode & (privateDirectory ? 077 : 022)) ||
        (S_ISREG(st.st_mode) && st.st_nlink != 1)) return NO;
    acl_t acl = acl_get_file(path.fileSystemRepresentation, ACL_TYPE_EXTENDED);
    if (!acl) return errno == ENOENT;  // macOS reports an absent extended ACL this way
    acl_entry_t entry;
    int which = ACL_FIRST_ENTRY;
    BOOL ok = YES;
    while (acl_get_entry(acl, which, &entry) == 0) {
        acl_tag_t tag;
        if (acl_get_tag_type(entry, &tag) || tag == ACL_EXTENDED_ALLOW) { ok = NO; break; }
        which = ACL_NEXT_ENTRY;
    }
    acl_free(acl);
    return ok;
}

static NSString *ToolHash(NSString *root) {
    // Same names, execute bits and byte stream as install.digest_tree. Hash the
    // entire retained interpreter/source, including imports and shared libraries.
    NSFileManager *files = NSFileManager.defaultManager;
    __block BOOL failed = NO;
    NSDirectoryEnumerator *walk = [files enumeratorAtURL:[NSURL fileURLWithPath:root]
        includingPropertiesForKeys:nil options:0 errorHandler:^BOOL(NSURL *url, NSError *error) {
            (void)url; (void)error; failed = YES; return NO;
        }];
    if (!walk) return nil;
    NSMutableArray<NSString *> *names = [NSMutableArray new];
    for (NSURL *url in walk) [names addObject:[url.path substringFromIndex:root.length + 1]];
    if (failed) return nil;
    [names sortUsingComparator:^NSComparisonResult(NSString *a, NSString *b) {
        // pathlib orders components, so a/b precedes a-b/c and a.b.
        NSArray<NSString *> *left = [a componentsSeparatedByString:@"/"];
        NSArray<NSString *> *right = [b componentsSeparatedByString:@"/"];
        NSUInteger count = MIN(left.count, right.count);
        for (NSUInteger i = 0; i < count; ++i) {
            int order = strcmp(left[i].UTF8String, right[i].UTF8String);
            if (order) return order < 0 ? NSOrderedAscending : NSOrderedDescending;
        }
        return left.count < right.count ? NSOrderedAscending :
            left.count > right.count ? NSOrderedDescending : NSOrderedSame;
    }];
    CC_SHA256_CTX context;
    CC_SHA256_Init(&context);
    for (NSString *name in names) {
        NSString *path = [root stringByAppendingPathComponent:name];
        struct stat st;
        if (lstat(path.fileSystemRepresentation, &st)) return nil;
        if (S_ISDIR(st.st_mode)) continue;
        if (!S_ISREG(st.st_mode)) return nil;
        int fd = open(path.fileSystemRepresentation, O_RDONLY | O_NOFOLLOW | O_NONBLOCK);
        if (fd < 0) return nil;
        const char *relative = name.UTF8String;
        CC_SHA256_Update(&context, relative, (CC_LONG)strlen(relative));
        char mode[24];
        int length = snprintf(mode, sizeof mode, "%c%u%c", 0, (unsigned)(st.st_mode & 0111), 0);
        CC_SHA256_Update(&context, mode, (CC_LONG)length);
        unsigned char block[65536];
        ssize_t count;
        while ((count = read(fd, block, sizeof block)) > 0) CC_SHA256_Update(&context, block, (CC_LONG)count);
        close(fd);
        if (count < 0) { failed = YES; break; }
        CC_SHA256_Update(&context, "", 1);
    }
    if (failed) return nil;
    unsigned char digest[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256_Final(digest, &context);
    char hex[65];
    for (int i = 0; i < CC_SHA256_DIGEST_LENGTH; ++i) snprintf(hex + i * 2, 3, "%02x", digest[i]);
    return [NSString stringWithUTF8String:hex];
}

static BOOL TrustedJob(NSString *job, NSDictionary *tools) {
    if (![job isKindOfClass:NSString.class] || !PlainDirectory(job) || !SecurePath(job, YES) ||
        ![tools isKindOfClass:NSDictionary.class] || tools.count != 2) return NO;
    __block BOOL complete = YES;
    NSDirectoryEnumerator *walk = [NSFileManager.defaultManager enumeratorAtURL:[NSURL fileURLWithPath:job]
        includingPropertiesForKeys:nil options:0 errorHandler:^BOOL(NSURL *url, NSError *error) {
            (void)url; (void)error; complete = NO; return NO;
        }];
    if (!walk) return NO;
    for (NSURL *url in walk) if (!SecurePath(url.path, NO)) return NO;
    if (!complete) return NO;
    for (NSString *name in @[@"source", @"python"]) {
        NSString *root = [job stringByAppendingPathComponent:name];
        NSString *expected = tools[name];
        if (![expected isKindOfClass:NSString.class] || expected.length != 64 || !PlainDirectory(root) ||
            ![ToolHash(root) isEqual:expected]) return NO;
    }
    return YES;
}
static void UpdateError(NSString *reason) {
    fprintf(stderr, "boot: update transfer: %s\n", reason.UTF8String);
    [NSApplication sharedApplication];
    NSAlert *alert = [NSAlert new];
    alert.messageText = @"Update could not finish";
    alert.informativeText = reason;
    [alert addButtonWithTitle:@"Quit"];
    [alert runModal];
}

// Called exactly once before the profile lock. The health receipt runs later on
// the main queue: the profile is then locked and normal startup has reached its
// event pump. A blocked/failed startup never acknowledges the replacement.
void pub_update_launch(int *argc, char ***argv, const char *profilePath) {
    if (![NSBundle.mainBundle.bundleIdentifier isEqual:@"io.github.nfsmw-native-mac"]) return;
    NSString *profile = CanonicalPath([NSString stringWithUTF8String:profilePath]);
    NSString *destination = CanonicalPath(NSBundle.mainBundle.bundlePath);
    NSString *journal = [profile stringByAppendingPathComponent:@".install-journal.json"];
    BOOL needsRecovery = [NSFileManager.defaultManager fileExistsAtPath:journal];
    NSDictionary *pending = needsRecovery && SecurePath(journal, NO) ? UpdateJSON(journal) : nil;
    if (getenv("BOOT_DEADLINE") || getenv("BOOT_BACKGROUND")) {
        if (needsRecovery) { fprintf(stderr, "boot: recover the interrupted update with a normal launch first\n"); exit(125); }
        return;
    }
    if (*argc >= 3 && !strcmp((*argv)[1], "--port-update-ack")) {
        NSString *token = [NSString stringWithUTF8String:(*argv)[2]];
        if (![pending[@"kind"] isEqual:@"launch-update-v1"] ||
            ![pending[@"state"] isEqual:@"awaiting_ack"] || ![pending[@"token"] isEqual:token] ||
            token.length != 32 || [token rangeOfCharacterFromSet:
                [[NSCharacterSet characterSetWithCharactersInString:@"0123456789abcdef"] invertedSet]].location != NSNotFound)
            exit(1);
        // Remove private arguments before app_start interprets a game-folder argument.
        for (int i = 1; i + 2 <= *argc; ++i) (*argv)[i] = (*argv)[i + 2];
        *argc -= 2;
        struct stat st;
        if (lstat(destination.fileSystemRepresentation, &st)) exit(1);
        NSArray *identity = @[@(st.st_dev), @(st.st_ino)];
        NSDictionary *ids = pending[@"ids"];
        NSArray *expected = [ids isKindOfClass:NSDictionary.class] ? ids[destination] : nil;
        if (![expected isKindOfClass:NSArray.class] || expected.count != 2 || ![expected[1] isEqual:identity]) exit(1);
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 10 * NSEC_PER_SEC), dispatch_get_main_queue(), ^{
            if (!game_dir || !*game_dir) return;  // a folder/error modal is not successful startup
            NSDictionary *receipt = @{@"token":token, @"identity":identity};
            NSData *body = [NSJSONSerialization dataWithJSONObject:receipt options:0 error:nil];
            NSString *path = [profile stringByAppendingPathComponent:[@".update-ack-" stringByAppendingString:token]];
            PUReplaceFile(path, body, 0600);
        });
        return;
    }
    NSString *job = nil, *ready = nil;
    NSDictionary *tools = nil;
    NSFileManager *files = NSFileManager.defaultManager;
    if (needsRecovery) {
        // Setup journals also need recovery before roots are read; they have no
        // launch payload, so an explicit Setup repair is required for those.
        if (![pending[@"kind"] isEqual:@"launch-update-v1"]) {
            UpdateError(@"Run Setup again to recover the interrupted installation. Your saves are unchanged.");
            exit(1);
        }
        job = pending[@"job"];
        tools = pending[@"tools"];
    } else {
        NSString *index = [profile stringByAppendingPathComponent:@".update-ready.json"];
        NSDictionary *entry = PlainDirectory(profile) && SecurePath(profile, NO) && SecurePath(index, YES) ? UpdateJSON(index) : nil;
        if ([entry[@"format_version"] isEqual:@1] && [entry[@"profile"] isEqual:profile] &&
            [entry[@"destination"] isEqual:destination]) {
            ready = entry[@"ready"]; job = entry[@"job"]; tools = entry[@"tools"];
            if (![ready isKindOfClass:NSString.class] || ![job isKindOfClass:NSString.class] ||
                (![ready isEqual:[job stringByAppendingPathComponent:@"ready.json"]] &&
                 ![ready isEqual:[job stringByAppendingPathComponent:@"staged/ready.json"]]) ||
                [files fileExistsAtPath:[ready.stringByDeletingLastPathComponent stringByAppendingPathComponent:@".apply-failed.json"]] ||
                [files fileExistsAtPath:[job stringByAppendingPathComponent:@"cancel"]]) return;
        }
    }
    if (!job && !needsRecovery) return;
    if (!TrustedJob(job, tools)) {
        UpdateError(@"The prepared update folder or its retained tools failed verification. Run Setup again.");
        if (needsRecovery) exit(1);
        return;
    }
    NSString *python = [job stringByAppendingPathComponent:@"python/bin/python3"];
    NSString *script = [job stringByAppendingPathComponent:@"source/tools/setup_kit/apply.py"];
    if (![files isExecutableFileAtPath:python] || ![files fileExistsAtPath:script]) {
        UpdateError(@"The prepared update is missing its recovery tools. Run Setup again.");
        if (needsRecovery) exit(1);
        NSString *failed = [ready.stringByDeletingLastPathComponent stringByAppendingPathComponent:@".apply-failed.json"];
        PUReplaceFile(failed, [@"{\"state\":\"failed\"}\n" dataUsingEncoding:NSUTF8StringEncoding], 0666);
        return;
    }
    NSTask *helper = [NSTask new];
    helper.executableURL = [NSURL fileURLWithPath:python];
    NSMutableArray *arguments = [@[@"-I", @"-B", script, @"--profile", profile, @"--destination", destination,
        @"--parent", [NSString stringWithFormat:@"%d", getpid()]] mutableCopy];
    if (ready) [arguments addObjectsFromArray:@[@"--ready", ready]];
    helper.arguments = arguments;
    helper.currentDirectoryURL = [NSURL fileURLWithPath:job];
    helper.environment = @{@"HOME":NSHomeDirectory(), @"PATH":@"/usr/bin:/bin:/usr/sbin:/sbin", @"TMPDIR":NSTemporaryDirectory(),
        @"BOOT_USERDATA":profile};
    NSError *error = nil;
    if (![helper launchAndReturnError:&error]) { UpdateError(error.localizedDescription); exit(1); }
    exit(0);
}
