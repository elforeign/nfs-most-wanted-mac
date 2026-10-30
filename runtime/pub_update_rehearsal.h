// Explicit compile-time transport for a scratch-only, loopback update rehearsal.
// This header has no effect in release builds.
#ifdef PUB_UPDATE_REHEARSAL
#ifndef PUB_UPDATE_REHEARSAL_FEED_URL
#error PUB_UPDATE_REHEARSAL_FEED_URL must name the loopback fixture feed
#endif

static inline void PURRequire(BOOL ok, NSString *message) {
    if (!ok) @throw [NSException exceptionWithName:@"RehearsalIsolation" reason:message userInfo:nil];
}
static inline NSString *PURPath(NSString *value) {
    if (!value.isAbsolutePath) return nil;
    // Resolve the deepest existing ancestor, including /var -> /private/var.
    // Foundation's spelling can differ for not-yet-created child directories.
    NSString *path = value.stringByStandardizingPath;
    NSMutableArray *tail = [NSMutableArray array];
    char resolved[PATH_MAX];
    while (!realpath(path.fileSystemRepresentation, resolved)) {
        if ([path isEqual:@"/"]) return nil;
        [tail addObject:path.lastPathComponent]; path = path.stringByDeletingLastPathComponent;
    }
    path = [NSString stringWithUTF8String:resolved];
    for (NSString *part in tail.reverseObjectEnumerator) path = [path stringByAppendingPathComponent:part];
    return path;
}
static inline void PURInside(NSString *path) {
    NSString *root = NSProcessInfo.processInfo.environment[@"PUB_UPDATE_REHEARSAL_ROOT"];
    PURRequire(root.isAbsolutePath && ![PURPath(root) isEqual:@"/"], @"Missing scratch rehearsal root");
    PURRequire(path.isAbsolutePath && [PURPath(path) hasPrefix:[PURPath(root) stringByAppendingString:@"/"]],
               [@"Rehearsal path is outside scratch: " stringByAppendingString:path ?: @"(missing)"]);
}
static inline NSURL *PURBase(void) {
    NSString *value = NSProcessInfo.processInfo.environment[@"PUB_UPDATE_REHEARSAL_BASE_URL"];
    NSURL *url = value ? [NSURL URLWithString:value] : nil;
    PURRequire([url.scheme isEqual:@"http"] && [url.host isEqual:@"127.0.0.1"] &&
        url.port && url.port.intValue > 0 && url.port.intValue <= 65535 && !url.user && !url.password &&
        !url.query && !url.fragment && (!url.path.length || [url.path isEqual:@"/"]), @"Invalid rehearsal loopback URL");
    return url;
}
static inline NSURL *PURFeed(void) {
    NSURL *url = [NSURL URLWithString:@PUB_UPDATE_REHEARSAL_FEED_URL], *base = PURBase();
    PURRequire([url.scheme isEqual:base.scheme] && [url.host isEqual:base.host] && [url.port isEqual:base.port] &&
        !url.user && !url.password && !url.fragment, @"Rehearsal feed must use the fixture origin");
    return url;
}
static inline NSDictionary *PURChildEnvironment(NSDictionary *normal) {
    NSDictionary *env = NSProcessInfo.processInfo.environment;
    (void)PURFeed();
    PURRequire([PURPath(NSHomeDirectory()) isEqual:PURPath(env[@"HOME"])], @"Foundation HOME differs from scratch HOME");
    PURInside(NSHomeDirectory());
    NSMutableDictionary *child = [normal mutableCopy];
    for (NSString *key in @[@"HOME", @"CFFIXED_USER_HOME", @"TMPDIR", @"NFSMW_SETUP_HOME", @"NFSMW_SETUP_DATA",
                            @"NFSMW_SETUP_CACHE", @"NFSMW_SETUP_STATE", @"NFSMW_SETUP_LOGS", @"BOOT_USERDATA",
                            @"PUB_UPDATE_REHEARSAL_APP"]) {
        NSString *value = env[key];
        PURRequire(value.length > 0, [@"Missing rehearsal path: " stringByAppendingString:key]);
        PURInside(value); child[key] = value;
    }
    child[@"PUB_UPDATE_REHEARSAL_ROOT"] = env[@"PUB_UPDATE_REHEARSAL_ROOT"];
    child[@"PUB_UPDATE_REHEARSAL_BASE_URL"] = env[@"PUB_UPDATE_REHEARSAL_BASE_URL"];
    NSString *fault = env[@"PUB_UPDATE_REHEARSAL_FAULT"];
    PURRequire(!fault || [fault isEqual:@"rebuild"], @"Unknown rehearsal fault");
    if (fault) child[@"PUB_UPDATE_REHEARSAL_FAULT"] = fault;
    return child;
}

static inline NSString *PURTemporaryDirectory(void) {
    // Foundation can return the account's system temp even with TMPDIR set.
    // Every explicit temp path in the rehearsal binaries uses the validated
    // scratch value; the child sandbox denies writes to the system temp.
    NSString *path = NSProcessInfo.processInfo.environment[@"TMPDIR"];
    PURInside(path);
    return path;
}
#define NSTemporaryDirectory() PURTemporaryDirectory()

static inline NSString *PURPrepareScript(NSString *script) {
    // Check backend locations before it creates directories. The only injected
    // failure is a build-command exception, confined to the rehearsal binary.
    NSString *guard = @"stream = Events()\n"
        @"root = Path(os.environ['PUB_UPDATE_REHEARSAL_ROOT']).resolve()\n"
        @"paths = backend.Paths(Path(os.environ['NFSMW_SETUP_STATE']) / 'work')\n"
        @"for path in (paths.work, paths.state, paths.logs, paths.cache, paths.data, paths.legacy, Path.home(), Path(os.environ['TMPDIR']), Path(os.environ['BOOT_USERDATA'])):\n"
        @"    if not path.resolve().is_relative_to(root): raise RuntimeError('Backend path escaped rehearsal scratch')\n"
        @"if paths.cache_moved: raise RuntimeError('Rehearsal cache path is not shell-safe')\n"
        @"original_environment = backend.child_environment\n"
        @"def isolated_environment(*args, **kwargs):\n"
        @"    env = original_environment(*args, **kwargs)\n"
        @"    env['CFFIXED_USER_HOME'] = env['HOME']\n"
        @"    for key in ('HOME', 'TMPDIR', 'CLANG_MODULE_CACHE_PATH'):\n"
        @"        if not Path(env[key]).resolve().is_relative_to(root): raise RuntimeError('Build child path escaped rehearsal scratch')\n"
        @"    return env\n"
        @"backend.child_environment = isolated_environment\n"
        @"if os.environ.get('PUB_UPDATE_REHEARSAL_FAULT') == 'rebuild':\n"
        @"    original_run = backend.Runner.run\n"
        @"    def fail_build(self, argv, *args, **kwargs):\n"
        @"        if 'runtime/build.sh' in [str(a) for a in argv]: raise backend.SetupError('Rehearsal forced rebuild failure')\n"
        @"        return original_run(self, argv, *args, **kwargs)\n"
        @"    backend.Runner.run = fail_build\n";
    return [script stringByReplacingOccurrencesOfString:@"stream = Events()\n" withString:guard];
}

@interface PURFeedDelegate : NSObject <NSURLSessionTaskDelegate>
@end
@implementation PURFeedDelegate
- (void)URLSession:(NSURLSession *)session task:(NSURLSessionTask *)task
    willPerformHTTPRedirection:(NSHTTPURLResponse *)response newRequest:(NSURLRequest *)request
    completionHandler:(void (^)(NSURLRequest *))completion {
    (void)session; (void)task; (void)response; (void)request;
    // Feed fixtures never redirect. Refuse before any request can leave loopback.
    completion(nil);
}
@end
#endif
