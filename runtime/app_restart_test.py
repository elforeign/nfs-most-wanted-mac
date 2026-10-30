"""Restart routing without launching apps, games, or desktop services.
Production: 14/14 routing/guard/path cases + built plist pass. Original bundled exec
path fails 2 cases; missing cwd restoration fails 2 vs production 0. The pre-fix
branch fails both relative-path cases (2/14); missing Local Network key fails the plist assertion.
Path checks use the captured launch environment and real waiter/replacement child
processes with different working directories, relative game/profile/log/roots/cache paths.
"""
import os
from pathlib import Path
import plistlib
import subprocess
import tempfile

RUNTIME = Path(__file__).resolve().parent

STUBS = r'''
#import <Cocoa/Cocoa.h>
#include <errno.h>
#include <fcntl.h>
#include <mach-o/dyld.h>
#include <spawn.h>
#include <sys/event.h>
#include <sys/wait.h>
#include <unistd.h>
extern char **environ;
static int app_bundled;
static int path_child(const char *path, char *const *args, char *const *env) {
    pid_t pid = fork();
    if (!pid) { if (chdir("/")) _exit(3); execve(path, args, env); _exit(4); }
    int status;
    if (pid < 0 || waitpid(pid, &status, 0) != pid || !WIFEXITED(status)) return 5;
    return WEXITSTATUS(status);
}
@interface RestartBundle : NSObject
+ (instancetype)mainBundle;
@property(readonly) NSString *bundleIdentifier;
@property(readonly) NSURL *bundleURL;
@end
@implementation RestartBundle
+ (instancetype)mainBundle { return [self new]; }
- (NSString *)bundleIdentifier { return app_bundled ? @"test.restart" : nil; }
- (NSURL *)bundleURL { return [NSURL fileURLWithPath:@"/scratch/Test Game.app"]; }
@end
@interface RestartWorkspace : NSObject
+ (instancetype)sharedWorkspace;
- (void)openApplicationAtURL:(NSURL *)url configuration:(NSWorkspaceOpenConfiguration *)config
    completionHandler:(void (^)(NSRunningApplication *, NSError *))handler;
@end
@implementation RestartWorkspace
+ (instancetype)sharedWorkspace { return [self new]; }
- (void)openApplicationAtURL:(NSURL *)url configuration:(NSWorkspaceOpenConfiguration *)config
    completionHandler:(void (^)(NSRunningApplication *, NSError *))handler {
    printf("LS new=%d bundle=%s args=%s env=%s\n", config.createsNewApplicationInstance,
        url.path.UTF8String, [config.arguments componentsJoinedByString:@"|"].UTF8String,
        [config.environment[@"RESTART_FIXTURE"] UTF8String]);
    fflush(stdout);
    if (config.environment[@"RESTART_PATHS"]) {
        char **args = calloc(config.arguments.count + 2, sizeof *args);
        char **env = calloc(config.environment.count + 1, sizeof *env);
        args[0] = strdup(config.environment[@"RESTART_BINARY"].UTF8String);
        NSUInteger i = 1;
        for (NSString *arg in config.arguments) args[i++] = strdup(arg.UTF8String);
        i = 0;
        for (NSString *key in config.environment)
            env[i++] = strdup([NSString stringWithFormat:@"%@=%@", key, config.environment[key]].UTF8String);
        if (path_child(args[0], args, env)) _exit(6);
    }
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_DEFAULT, 0), ^{
        if (getenv("RESTART_ERROR")) handler(nil, [NSError errorWithDomain:@"fixture" code:1 userInfo:nil]);
        else handler((NSRunningApplication *)[NSObject new], nil);
    });
}
@end
static int fixture_exec(const char *path, char *const *args, char *const *env) {
    if (getenv("RESTART_PATHS")) _exit(path_child(path, args, env));
    printf("EXEC %s %s %s env=%d\n", path, args[0], args[1], env == environ); fflush(stdout); _exit(0);
}
static int fixture_spawn(pid_t *pid, const char *path, const posix_spawn_file_actions_t *actions,
    const posix_spawnattr_t *attr, char *const *args, char *const *env) {
    (void)actions; (void)attr;
    if (getenv("RESTART_PATHS")) {
        // The original process has exited by the time the real waiter opens the replacement.
        char **wait_args;
        int n = 0; while (args[n]) ++n;
        wait_args = calloc((size_t)n + 1, sizeof *wait_args);
        for (int i = 0; i < n; ++i) wait_args[i] = args[i];
        wait_args[2] = "2";
        if (path_child(path, wait_args, env)) _exit(7);
        *pid = 123; return 0;
    }
    printf("SPAWN %s %s %s env=%d\n", path, args[1], args[3], env == environ); *pid = 123; return 0;
}
#define NSBundle RestartBundle
#define NSWorkspace RestartWorkspace
#define execve fixture_exec
#define posix_spawn fixture_spawn
'''
MAIN = r'''
int main(int argc, char **argv) { @autoreleasepool {
    app_bundled = getenv("RESTART_PATHS") ? atoi(getenv("RESTART_PATH_BUNDLE")) : (argc == 5 ? atoi(argv[1]) : 0);
    STARTUP_PRELUDE
    if (getenv("RESTART_PATHS")) {
        if (argc == 5) {
            char *game_args[] = {argv[0], "relative game", NULL};
            restart_capture(game_args);
            restart_requested = restart_clean = 1;
            if (chdir("/")) return 9;
            return 0;  // exercise the registered atexit callback
        }
        const char *paths[] = {"BOOT_USERDATA", "BOOT_LOG", "NFS_ROOTS", "NFS_X360_FILES", "NFS_METAL_CACHE"};
        int good = argc == 2 && !access(argv[1], F_OK);
        for (unsigned i = 0; i < sizeof paths / sizeof *paths; ++i)
            good &= getenv(paths[i]) && !access(getenv(paths[i]), F_OK);
        good &= getenv("_NFS_RESTART_CWD") == NULL;
        puts(good ? "PATHS pass" : "PATHS fail");
        return good ? 0 : 8;
    }
    if (argc != 5) return 2;
    app_bundled = atoi(argv[1]);
    restart_requested = atoi(argv[2]); restart_clean = atoi(argv[3]); restart_guarded = atoi(argv[4]);
    char *args[] = {"/scratch/boot", "--port-restart-wait", "2", "/scratch/boot", "folder with spaces", NULL};
    if (getenv("RESTART_WAITER")) { restart_wait(5, args); puts("IGNORED"); return 0; }
    restart_exe = args[0]; restart_argv = args; restart_env = environ;
    restart_at_exit();
    return 0;
} }
'''


def fixture(source, root):
    helper = source[source.index('static char **restart_argv'):source.index('\nconst char *log_dir')]
    src = root / 'fixture.m'
    prelude = source[source.index('const char *app_start(int argc, char **argv) {'):].split('@autoreleasepool', 1)[0].split('{', 1)[1]
    src.write_text(STUBS + helper + MAIN.replace('STARTUP_PRELUDE', prelude))
    exe = root / 'fixture'
    subprocess.run(['clang', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function', '-fobjc-arc',
                    str(src), '-framework', 'Cocoa', '-o', str(exe)], check=True, capture_output=True)
    failures = 0
    env = {k: v for k, v in os.environ.items() if k not in ('BOOT_DEADLINE', 'BOOT_BACKGROUND')}
    env['RESTART_FIXTURE'] = 'launch override'
    cases = [
        (1, 1, 1, 0, {'RESTART_WAITER': '1'}, 0, 'LS new=1 bundle=/scratch/Test Game.app args=folder with spaces env=launch override'),
        (1, 1, 1, 0, {'RESTART_WAITER': '1', 'RESTART_ERROR': '1'}, 126, 'LS new=1 bundle=/scratch/Test Game.app args=folder with spaces env=launch override'),
        (0, 1, 1, 0, {'RESTART_WAITER': '1'}, 0, 'EXEC /scratch/boot /scratch/boot folder with spaces env=1'),
        (1, 1, 1, 0, {'RESTART_WAITER': '1', 'BOOT_DEADLINE': '1'}, 125, ''),
        (1, 1, 1, 0, {'RESTART_WAITER': '1', 'BOOT_BACKGROUND': '1'}, 125, ''),
        (1, 1, 1, 0, {}, 0, 'SPAWN /scratch/boot --port-restart-wait /scratch/boot env=1'),
        (0, 1, 1, 0, {}, 0, 'SPAWN /scratch/boot --port-restart-wait /scratch/boot env=1'),
        (1, 0, 1, 0, {}, 0, ''), (1, 1, 0, 0, {}, 0, ''), (1, 1, 1, 1, {}, 0, ''),
        (1, 1, 1, 0, {'BOOT_DEADLINE': '1'}, 0, ''),
        (1, 1, 1, 0, {'BOOT_BACKGROUND': '1'}, 0, ''),
    ]
    for bundled, requested, clean, guarded, extra, rc, expected in cases:
        out = subprocess.run([str(exe), *map(str, (bundled, requested, clean, guarded))],
                             env=dict(env, **extra), capture_output=True, text=True, timeout=20)
        if out.returncode != rc or out.stdout.strip() != expected:
            failures += 1
    launch = root / 'launch cwd'; launch.mkdir()
    paths = {'BOOT_USERDATA': 'relative profile', 'BOOT_LOG': 'relative log', 'NFS_ROOTS': 'relative roots',
             'NFS_X360_FILES': 'relative x360', 'NFS_METAL_CACHE': 'relative metal'}
    for name in ['relative game', *paths.values()]: (launch / name).touch()
    for bundled in (0, 1):
        out = subprocess.run([str(exe), '1', '1', '1', '0'], cwd=launch,
                             env=dict(env, **paths, RESTART_PATHS='1', RESTART_BINARY=str(exe),
                                      RESTART_PATH_BUNDLE=str(bundled)),
                             capture_output=True, text=True, timeout=20)
        if out.returncode or 'PATHS pass' not in out.stdout or 'PATHS fail' in out.stdout:
            failures += 1
    return failures, len(cases) + 2


def plist_check(source, root):
    # Execute the production Info.plist heredoc, then parse the generated file, not source text.
    app = root / 'Test Game.app'
    (app / 'Contents').mkdir(parents=True)
    block = source[source.index('cat > "$APP/Contents/Info.plist"'):source.index('\nplutil -lint')]
    env = dict(os.environ, APP=str(app), BUNDLE_ID='test.restart', NAME='Test Game', EXEC_NAME='NFSMW',
               VERSION='1.0', BUILD_VERSION='1', SOURCE_ID='fixture', MINOS='14.0')
    subprocess.run(['/bin/sh', '-c', block], env=env, check=True, capture_output=True)
    with (app / 'Contents/Info.plist').open('rb') as f:
        info = plistlib.load(f)
    return info.get('NSLocalNetworkUsageDescription') == 'Online free roam finds your router to host a session.'


def main():
    source = (RUNTIME / 'app.m').read_text()
    bundle = (RUNTIME / 'bundle.sh').read_text()
    with tempfile.TemporaryDirectory(prefix='app-restart-') as tmp:
        root = Path(tmp)
        good = root / 'good'; good.mkdir()
        failures, count = fixture(source, good)
        assert failures == 0, (failures, count)
        assert plist_check(bundle, good)
        # Exact original behavior: the waiter directly execs the bundle binary.
        start = source.index('    if (NSBundle.mainBundle.bundleIdentifier) {')
        end = source.index('    execve(argv[0], argv + 3, environ);', start)
        bad = root / 'bad'; bad.mkdir()
        broken, _ = fixture(source[:start] + source[end:], bad)
        assert broken == 2, broken
        cwd_bad = root / 'cwd_bad'; cwd_bad.mkdir()
        cwd_broken, _ = fixture(source.replace('    restart_restore_cwd();', ''), cwd_bad)
        assert cwd_broken == 2, cwd_broken
        old_plist = '\n'.join(line for line in bundle.splitlines() if 'NSLocalNetworkUsageDescription' not in line)
        assert not plist_check(old_plist, bad)
        print(f'PASS restart: {count}/{count}; original exec path {broken} failures; missing cwd {cwd_broken} failures; built plist pass, missing-key mutant rejected')


if __name__ == '__main__':
    main()
