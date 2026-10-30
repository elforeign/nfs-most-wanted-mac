// Opt-in update UI and its separately packaged background preparation supervisor.
// The background process prepares only; application belongs to the normal next-launch hook.
#import <AppKit/AppKit.h>
#import <Foundation/Foundation.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <sys/mount.h>
#include <fcntl.h>
#include <signal.h>
#include <unistd.h>
#include "pub_update_core.h"
#include "pub_update_rehearsal.h"
#include "pub_update_write.h"

static NSDictionary *PURead(NSString *path) {
    int fd = open(path.fileSystemRepresentation, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC);
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
    id value = [NSJSONSerialization JSONObjectWithData:data options:0 error:nil];
    return [value isKindOfClass:NSDictionary.class] ? value : nil;
}
static BOOL PUWrite(NSString *path, NSDictionary *value) {
    NSData *data = [NSJSONSerialization dataWithJSONObject:value options:0 error:nil];
    return PUReplaceFile(path, data, 0600);
}
static NSString *PUCanonical(NSString *path) {
    char resolved[PATH_MAX];
    return realpath(path.fileSystemRepresentation, resolved) ? [NSString stringWithUTF8String:resolved] : path;
}

#ifdef PUB_UPDATE_WORKER
static NSString *const PrepareScript =
@"\n"
@"import contextlib, json, os, shutil, signal, subprocess, sys, tarfile, time\n"
@"from pathlib import Path\n"
@"job, setup, profile, destination = map(Path, sys.argv[1:])\n"
@"def progress(state, message):\n"
@"    value = dict(schema=1, state=state, message=message)\n"
@"    tmp = job / 'progress.prepare.tmp'\n"
@"    with tmp.open('w') as f:\n"
@"        json.dump(value, f); f.flush(); os.fsync(f.fileno())\n"
@"    tmp.replace(job / 'progress.json')\n"
@"def check_cancel():\n"
@"    if (job / 'cancel').exists(): raise RuntimeError('Update cancelled')\n"
@"def safe_copy(source, target):\n"
@"    source = source.resolve(strict=True)\n"
@"    # Relocatable Python has internal aliases. Materialize only regular files\n"
@"    # whose resolved path remains within the signed resource tree.\n"
@"    for path in source.rglob('*'):\n"
@"        real = path.resolve(strict=True)\n"
@"        if not real.is_relative_to(source) or not (real.is_file() or real.is_dir()):\n"
@"            raise RuntimeError('Setup resource contains an unsafe link or special file')\n"
@"        if path.is_symlink() and real.is_dir():\n"
@"            raise RuntimeError('Setup resource contains a directory link')\n"
@"    shutil.copytree(source, target, symlinks=False)\n"
@"    for path in [target, *target.rglob('*')]:\n"
@"        path.chmod(0o700 if path.is_dir() else (0o700 if path.stat().st_mode & 0o111 else 0o600))\n"
@"if str(setup) != '--retained':\n"
@"    try:\n"
@"        progress('building', 'Preparing the update')\n"
@"        check_cancel()\n"
@"        resources = setup / 'Contents/Resources'\n"
@"        source = job / 'source'\n"
@"        source.mkdir(mode=0o700)\n"
@"        with tarfile.open(resources / 'source.tar.gz') as archive:\n"
@"            members = archive.getmembers()\n"
@"            if len(members) > 100000 or sum(m.size for m in members) > 2 * 1024**3:\n"
@"                raise RuntimeError('Setup source archive exceeds the extraction limit')\n"
@"            for member in members:\n"
@"                path = source / member.name\n"
@"                if not path.resolve().is_relative_to(source) or not (member.isfile() or member.isdir()):\n"
@"                    raise RuntimeError('Setup source archive contains an unsafe path or link')\n"
@"            archive.extractall(source, members=members, filter='data')\n"
@"        safe_copy(resources / 'python', job / 'python')\n"
@"        dl = source / 'third_party/dl'\n"
@"        dl.parent.mkdir(parents=True, exist_ok=True)\n"
@"        safe_copy(resources / 'third_party/dl', dl)\n"
@"        for path in [source, *source.rglob('*')]:\n"
@"            path.chmod(0o700 if path.is_dir() else (0o700 if path.stat().st_mode & 0o111 else 0o600))\n"
@"        check_cancel()\n"
@"        # Re-exec the retained interpreter so no library remains mapped from the DMG.\n"
@"        if not (job / 'retained').exists():\n"
@"            (job / 'retained').touch(mode=0o600)\n"
@"        os.execv(str(job / 'python/bin/python3'), [str(job / 'python/bin/python3'), '-I', '-B',\n"
@"                 '-c', (job / 'prepare.py').read_text(), str(job), '--retained', str(profile), str(destination)])\n"
@"    except Exception as e:\n"
@"        if str(setup) != '--retained':\n"
@"            progress('failed', str(e)); raise\n"
@"\n"
@"source = job / 'source'\n"
@"sys.path.insert(0, str(source / 'tools'))\n"
@"from setup_kit import backend, apply\n"
@"class Events:\n"
@"    pending = ''\n"
@"    error = None\n"
@"    def write(self, text):\n"
@"        self.pending += text\n"
@"        while '\\n' in self.pending:\n"
@"            line, self.pending = self.pending.split('\\n', 1)\n"
@"            if not line: continue\n"
@"            print(line, file=sys.stderr, flush=True)\n"
@"            value = json.loads(line)\n"
@"            if value.get('ev') == 'stage':\n"
@"                self.title = value['title']; progress('building', self.title)\n"
@"            if value.get('ev') == 'progress':\n"
@"                progress('building', getattr(self, 'title', 'Preparing the update') + ' (%s/%s)' % (value.get('done', 0), value.get('total', 0)))\n"
@"            if value.get('ev') == 'error':\n"
@"                self.error = value.get('msg', 'Preparation failed') + ' ' + value.get('fix', 'Open the log')\n"
@"                progress('failed', self.error)\n"
@"        return len(text)\n"
@"    def flush(self): pass\n"
@"stream = Events()\n"
@"try:\n"
@"    check_cancel()\n"
@"    # Only the retained interpreter may detach: the first interpreter uses the image.\n"
@"    mount = job / 'mount'\n"
@"    for attempt in range(5):\n"
@"        if not mount.is_mount(): break\n"
@"        check_cancel()\n"
@"        result = subprocess.run(['/usr/bin/hdiutil', 'detach', str(mount)], stdout=sys.stderr)\n"
@"        if result.returncode == 0: break\n"
@"        if result.returncode != 16 or attempt == 4:\n"
@"            raise RuntimeError('Cannot detach the update image (exit %s). Close files on it and retry. See helper.log' % result.returncode)\n"
@"        time.sleep(1)\n"
@"    check_cancel()\n"
@"    with contextlib.redirect_stdout(stream):\n"
@"        rc = backend.main(['--stage-only', '--stage', str(job / 'staged'), '--dest', str(destination), '--events'])\n"
@"    if rc or stream.error: raise RuntimeError(stream.error or 'Rebuild failed. Open the log')\n"
@"    check_cancel()\n"
@"    ready = job / 'staged/ready.json'\n"
@"    apply.publish_ready(ready, profile, destination)\n"
@"    check_cancel()\n"
@"    progress('ready', 'Ready - applies next launch')\n"
@"except BaseException as e:\n"
@"    progress('failed', str(e) or 'Preparation was interrupted. Retry or open the log')\n"
@"    raise\n"
;

static NSString *job;
static void WorkerProgress(NSString *state, NSString *message) {
    PUWrite([job stringByAppendingPathComponent:@"progress.json"], @{@"schema":@1, @"state":state, @"message":message ?: @"Preparation failed"});
}
static BOOL IsCancelled(void) { return [NSFileManager.defaultManager fileExistsAtPath:[job stringByAppendingPathComponent:@"cancel"]]; }
static BOOL Run(NSString *tool, NSArray *args, NSFileHandle *log) {
    NSTask *task = [NSTask new];
    task.executableURL = [NSURL fileURLWithPath:tool]; task.arguments = args;
    task.currentDirectoryURL = [NSURL fileURLWithPath:job];
    task.environment = @{@"HOME":NSHomeDirectory(), @"PATH":@"/usr/bin:/bin:/usr/sbin:/sbin",
        @"TMPDIR":NSTemporaryDirectory(), @"PYTHONDONTWRITEBYTECODE":@"1"};
#ifdef PUB_UPDATE_REHEARSAL
    PURInside(tool); task.environment = PURChildEnvironment(task.environment);
#endif
    task.standardInput = NSFileHandle.fileHandleWithNullDevice; task.standardOutput = log; task.standardError = log;
    NSError *error = nil;
    if (![task launchAndReturnError:&error]) { WorkerProgress(@"failed", error.localizedDescription); return NO; }
    BOOL stopped = NO;
    while (task.running) {
        if (!stopped && IsCancelled()) { [task terminate]; stopped = YES; }
        [NSThread sleepForTimeInterval:0.1];
    }
    [task waitUntilExit];
    return task.terminationStatus == 0 && !IsCancelled();
}
int main(int argc, const char **argv) {
    @autoreleasepool {
        if (argc != 8) return 2;
        NSThread.currentThread.qualityOfService = NSQualityOfServiceUtility;
        job = PUCanonical([NSString stringWithUTF8String:argv[1]]);
        NSString *profile = [NSString stringWithUTF8String:argv[6]], *destination = [NSString stringWithUTF8String:argv[7]];
#ifdef PUB_UPDATE_REHEARSAL
        @try {
            (void)PURChildEnvironment(@{}); PURInside(job); PURInside(profile); PURInside(destination);
            PURRequire([PURPath(destination) isEqual:PURPath(NSProcessInfo.processInfo.environment[@"PUB_UPDATE_REHEARSAL_APP"])],
                       @"Rehearsal destination differs from the scratch app");
        }
        @catch (NSException *error) { fprintf(stderr, "%s\n", error.reason.UTF8String); return 1; }
#endif
        int lock = open([[job stringByDeletingLastPathComponent] stringByAppendingPathComponent:@"player.lock"].fileSystemRepresentation,
                        O_CREAT | O_WRONLY | O_NOFOLLOW | O_CLOEXEC, 0600);
        if (lock < 0 || flock(lock, LOCK_EX | LOCK_NB)) { WorkerProgress(@"failed", @"Another update is in progress. Open its log or wait before Retry"); return 1; }
        int active = open([job stringByAppendingPathComponent:@"prepare.lock"].fileSystemRepresentation, O_CREAT | O_WRONLY | O_NOFOLLOW | O_CLOEXEC, 0600);
        if (active < 0 || flock(active, LOCK_EX | LOCK_NB)) return 1;
        int fd = open([job stringByAppendingPathComponent:@"helper.log"].fileSystemRepresentation, O_CREAT | O_APPEND | O_WRONLY | O_NOFOLLOW | O_CLOEXEC, 0600);
        if (fd < 0) { WorkerProgress(@"failed", @"Cannot open the update log. Check folder access and free disk space"); return 1; }
        // The supervisor is detached; keep its own state-write errors in the job log too.
        if (dup2(fd, STDERR_FILENO) < 0) return 1;
        NSFileHandle *log = [[NSFileHandle alloc] initWithFileDescriptor:fd closeOnDealloc:YES];
        NSArray *delivery = @[job, [NSString stringWithUTF8String:argv[2]], [NSString stringWithUTF8String:argv[3]],
            [NSString stringWithUTF8String:argv[4]], [NSString stringWithUTF8String:argv[5]]];
        BOOL ok = Run([job stringByAppendingPathComponent:@"pub_update_helper"], delivery, log);
        NSDictionary *receipt = PURead([job stringByAppendingPathComponent:@"progress.json"]);
        NSString *setup = [job stringByAppendingPathComponent:@"mount/NFS Most Wanted Native Setup.app"];
        NSString *prepareScript = PrepareScript;
#ifdef PUB_UPDATE_REHEARSAL
        prepareScript = PURPrepareScript(prepareScript);
#endif
        if (ok && [receipt[@"state"] isEqual:@"verified"] && [receipt[@"setup_path"] isEqual:setup]) {
            NSString *script = [job stringByAppendingPathComponent:@"prepare.py"];
            ok = PUReplaceFile(script, [prepareScript dataUsingEncoding:NSUTF8StringEncoding], 0600);
            if (ok) ok = Run([setup stringByAppendingPathComponent:@"Contents/Resources/python/bin/python3"],
                @[@"-I", @"-B", @"-c", prepareScript, job, setup, profile, destination], log);
        } else ok = NO;
        if (IsCancelled()) {
            NSString *index = [profile stringByAppendingPathComponent:@".update-ready.json"];
            if ([PURead(index)[@"job"] isEqual:job]) [NSFileManager.defaultManager removeItemAtPath:index error:nil];
            WorkerProgress(@"cancelled", @"Update cancelled. Retry when ready");
        } else if (!ok) {
            receipt = PURead([job stringByAppendingPathComponent:@"progress.json"]);
            if (![receipt[@"state"] isEqual:@"failed"])
                WorkerProgress(@"failed", @"Preparation was interrupted or verification failed. Retry or open the log");
        }
        // A failed preparation process must never leave a ready launch index, even if a child
        // emitted ready before failing. No application is performed here.
        if (!ok) {
            NSString *index = [profile stringByAppendingPathComponent:@".update-ready.json"];
            if ([PURead(index)[@"job"] isEqual:job]) [NSFileManager.defaultManager removeItemAtPath:index error:nil];
        }
        NSString *mount = [job stringByAppendingPathComponent:@"mount"];
        struct statfs mountInfo;
        if (!statfs(mount.fileSystemRepresentation, &mountInfo) && !strcmp(mountInfo.f_mntonname, mount.fileSystemRepresentation)) {
            NSTask *detach = [NSTask new]; detach.executableURL = [NSURL fileURLWithPath:@"/usr/bin/hdiutil"];
            detach.arguments = @[@"detach", mount]; detach.standardOutput = log; detach.standardError = log;
            if ([detach launchAndReturnError:nil]) [detach waitUntilExit];
        }
        close(active); close(lock);
        return ok ? 0 : 1;
    }
}
#else

#include "settings.h"
extern const char *userdata_dir(void);
#ifndef PUB_RELEASES_REPO
#define PUB_RELEASES_REPO "elforeign/nfs-most-wanted-mac"
#endif
static NSString *pu_job, *pu_asset, *pu_checksum;
static unsigned long long pu_size;
static NSTask *pu_worker;
static BOOL pu_loaded, pu_local_error;
static char pu_help[1400];
static NSString *PUProfile(void) { return PUCanonical([NSString stringWithUTF8String:userdata_dir()]); }
static NSString *PUApplicationPath(void) {
#ifdef PUB_UPDATE_REHEARSAL
    NSString *path = NSProcessInfo.processInfo.environment[@"PUB_UPDATE_REHEARSAL_APP"];
    PURInside(path); return PURPath(path);
#else
    return PUCanonical(NSBundle.mainBundle.bundlePath);
#endif
}
static NSString *PUUpdates(void) { return [NSHomeDirectory() stringByAppendingPathComponent:@"Library/Application Support/NFSMW-Setup/updates"]; }
static BOOL PUAlive(void) {
    if (pu_worker.running) return YES;
    int fd = open([pu_job stringByAppendingPathComponent:@"prepare.lock"].fileSystemRepresentation, O_WRONLY | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0) return NO;
    BOOL busy = flock(fd, LOCK_EX | LOCK_NB) != 0;
    close(fd); return busy;
}
static BOOL PUReady(void) {
    NSDictionary *index = PURead([PUProfile() stringByAppendingPathComponent:@".update-ready.json"]);
    NSString *ready = [pu_job stringByAppendingPathComponent:@"staged/ready.json"];
    NSDictionary *descriptor = PURead(ready);
    NSDictionary *dest = descriptor[@"destination"];
    return [index[@"format_version"] isEqual:@1] && [index[@"job"] isEqual:pu_job] &&
        [index[@"ready"] isEqual:ready] && [index[@"profile"] isEqual:PUProfile()] &&
        [index[@"destination"] isEqual:PUApplicationPath()] &&
        [descriptor[@"format_version"] isEqual:@1] && [descriptor[@"version"] isEqual:[NSString stringWithUTF8String:pu_latest]] &&
        [dest isKindOfClass:NSDictionary.class] && [dest[@"path"] isEqual:index[@"destination"]] &&
        ![NSFileManager.defaultManager fileExistsAtPath:[pu_job stringByAppendingPathComponent:@"staged/.apply-failed.json"]];
}
static void PULoad(void) {
    if (pu_local_error) return;
    if (!pu_loaded) {
        pu_loaded = YES;
        NSDictionary *entry = PURead([PUUpdates() stringByAppendingPathComponent:@"current.json"]);
        NSString *path = entry[@"job"];
        if ([path isKindOfClass:NSString.class] && [path.stringByDeletingLastPathComponent isEqual:PUCanonical(PUUpdates())]) {
            pu_job = path;
            pu_asset = [entry[@"asset"] isKindOfClass:NSString.class] ? entry[@"asset"] : nil; pu_checksum = [entry[@"checksum"] isKindOfClass:NSString.class] ? entry[@"checksum"] : nil; pu_size = [entry[@"size"] isKindOfClass:NSNumber.class] ? [entry[@"size"] unsignedLongLongValue] : 0;
            NSString *version = entry[@"version"];
            if ([version isKindOfClass:NSString.class]) snprintf(pu_latest, sizeof pu_latest, "%s", version.UTF8String);
            NSString *current = [NSBundle.mainBundle objectForInfoDictionaryKey:@"CFBundleShortVersionString"];
            if (current && pu_compare_versions(pu_latest, current.UTF8String) <= 0 &&
                !PURead([PUProfile() stringByAppendingPathComponent:@".update-ready.json"])) {
                pu_job = nil; snprintf(pu_current, sizeof pu_current, "%s", current.UTF8String); pu_state = PU_UP_TO_DATE;
            }
        }
    }
    if (!pu_job) return;
    NSDictionary *value = PURead([pu_job stringByAppendingPathComponent:@"progress.json"]);
    NSString *state = value[@"state"], *message = value[@"message"];
    if (![state isKindOfClass:NSString.class] || (message && ![message isKindOfClass:NSString.class])) {
        pu_error("Update progress is missing. Retry or open the log"); return;
    }
    BOOL ready = [state isEqual:@"ready"] && PUReady() && !PUAlive() && (!pu_worker || pu_worker.terminationStatus == 0);
    // The index is published before the preparation process exits. Wait for successful exit.
    if ([state isEqual:@"ready"] && PUAlive()) return;
    double received = [value[@"received_bytes"] isKindOfClass:NSNumber.class] ? [value[@"received_bytes"] doubleValue] : 0;
    double total = [value[@"total_bytes"] isKindOfClass:NSNumber.class] ? [value[@"total_bytes"] doubleValue] : 0;
    pu_progress(state.UTF8String, message.UTF8String, received, total, ready);
    if (pu_busy() && !PUAlive()) pu_error("Preparation was interrupted. Retry or open the log");
}
static int mac_version(char *out, size_t cap) {
    NSString *v = [NSBundle.mainBundle objectForInfoDictionaryKey:@"CFBundleShortVersionString"];
    snprintf(out, cap, "%s", v ? v.UTF8String : ""); return v != nil;
}
static int mac_fetch(void) {
    NSURL *url = [NSURL URLWithString:[NSString stringWithFormat:@"https://api.github.com/repos/%s/releases/latest", PUB_RELEASES_REPO]];
#ifdef PUB_UPDATE_REHEARSAL
    (void)PURChildEnvironment(@{}); url = PURFeed();
#endif
    NSMutableURLRequest *request = [NSMutableURLRequest requestWithURL:url cachePolicy:NSURLRequestReloadIgnoringLocalCacheData timeoutInterval:15];
    [request setValue:@"application/vnd.github+json" forHTTPHeaderField:@"Accept"];
    NSURLSessionConfiguration *configuration = NSURLSessionConfiguration.ephemeralSessionConfiguration;
    configuration.URLCache = nil; configuration.HTTPCookieStorage = nil; configuration.URLCredentialStorage = nil;
    NSURLSession *session = [NSURLSession sessionWithConfiguration:configuration];
#ifdef PUB_UPDATE_REHEARSAL
    [session invalidateAndCancel];
    session = [NSURLSession sessionWithConfiguration:configuration delegate:[PURFeedDelegate new] delegateQueue:nil];
#endif
    [[session dataTaskWithRequest:request completionHandler:^(NSData *data, NSURLResponse *response, NSError *error) {
        BOOL ok = !error && data && [response isKindOfClass:NSHTTPURLResponse.class] && ((NSHTTPURLResponse *)response).statusCode == 200;
        NSString *body = ok ? [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding] : nil;
        NSDictionary *release = body ? [NSJSONSerialization JSONObjectWithData:data options:0 error:nil] : nil;
        dispatch_async(dispatch_get_main_queue(), ^{
            pu_done(body != nil, body.UTF8String);
            pu_asset = pu_checksum = nil; pu_size = 0;
            if (pu_state == PU_NEWER && [release isKindOfClass:NSDictionary.class]) {
                NSString *expected = [NSString stringWithFormat:@"NFS-Most-Wanted-Native-Setup-v%s.dmg", pu_latest];
                id assets = release[@"assets"];
                if ([assets isKindOfClass:NSArray.class]) for (id asset in assets) {
                    if (![asset isKindOfClass:NSDictionary.class]) continue;
                    if ([asset[@"name"] isEqual:expected]) { pu_asset = expected; pu_size = [asset[@"size"] isKindOfClass:NSNumber.class] ? [asset[@"size"] unsignedLongLongValue] : 0; }
                    if ([asset[@"name"] isEqual:[expected stringByAppendingString:@".sha256"]]) pu_checksum = asset[@"name"];
                }
            }
        });
        [session finishTasksAndInvalidate];
    }] resume];
    return 1;
}
static void PULocalError(const char *message) { pu_error(message); pu_local_error = YES; }
static int mac_start(const char *version) {
    if (pu_job && PUAlive()) return 1;
    pu_local_error = NO;
    if (!pu_asset || !pu_checksum || !pu_size || pu_size > 4ULL * 1024 * 1024 * 1024) {
        PULocalError("This release is missing its installer/checksum pair. Retry the update check later"); return 1;
    }
    NSFileManager *files = NSFileManager.defaultManager;
    NSString *updates = PUUpdates();
    NSError *error = nil;
    if (![files createDirectoryAtPath:updates withIntermediateDirectories:YES attributes:@{NSFilePosixPermissions:@0700} error:&error]) {
        PULocalError("Cannot create the update folder. Check folder access and free disk space"); return 1;
    }
    pu_job = [PUCanonical(updates) stringByAppendingPathComponent:NSUUID.UUID.UUIDString];
    if (![files createDirectoryAtPath:pu_job withIntermediateDirectories:NO attributes:@{NSFilePosixPermissions:@0700} error:&error]) { PULocalError("Cannot create the update job. Check folder access and free disk space"); return 1; }
    for (NSString *name in @[@"pub_update_helper", @"pub_update_worker"]) {
        NSString *from = [NSBundle.mainBundle.executablePath.stringByDeletingLastPathComponent stringByAppendingPathComponent:name];
        if (![files copyItemAtPath:from toPath:[pu_job stringByAppendingPathComponent:name] error:&error]) {
            PULocalError("Update tools are missing or inaccessible. Run Setup once to repair this app"); return 1;
        }
    }
    NSDictionary *entry = @{@"job":pu_job, @"version":[NSString stringWithUTF8String:version], @"asset":pu_asset, @"checksum":pu_checksum, @"size":@(pu_size)};
    if (!PUWrite([updates stringByAppendingPathComponent:@"current.json"], entry) ||
        !PUWrite([pu_job stringByAppendingPathComponent:@"progress.json"], @{@"schema":@1, @"state":@"downloading", @"message":@"Starting update"})) { PULocalError("Cannot save update progress. Check folder access and free disk space"); return 1; }
    pu_worker = [NSTask new]; pu_worker.executableURL = [NSURL fileURLWithPath:[pu_job stringByAppendingPathComponent:@"pub_update_worker"]];
    pu_worker.arguments = @[pu_job, [@"v" stringByAppendingString:[NSString stringWithUTF8String:version]], pu_asset, pu_checksum,
        [NSString stringWithFormat:@"%llu", pu_size], PUProfile(), PUApplicationPath()];
    pu_worker.environment = @{@"HOME":NSHomeDirectory(), @"PATH":@"/usr/bin:/bin:/usr/sbin:/sbin", @"TMPDIR":NSTemporaryDirectory()};
#ifdef PUB_UPDATE_REHEARSAL
    PURInside(pu_job); PURInside(PUProfile()); PURInside(PUApplicationPath());
    pu_worker.environment = PURChildEnvironment(pu_worker.environment);
#endif
    pu_worker.standardInput = NSFileHandle.fileHandleWithNullDevice;
    pu_worker.standardOutput = NSFileHandle.fileHandleWithNullDevice; pu_worker.standardError = NSFileHandle.fileHandleWithNullDevice;
    if (![pu_worker launchAndReturnError:&error]) PULocalError("Cannot launch the update tools. Run Setup once to repair this app");
    return 1;
}
static int mac_cancel(void) {
    if (!pu_job) return 0;
    if (!PUWrite([pu_job stringByAppendingPathComponent:@"cancel"], @{})) return 0;
    if (!PUAlive()) {
        NSString *index = [PUProfile() stringByAppendingPathComponent:@".update-ready.json"];
        if ([PURead(index)[@"job"] isEqual:pu_job] && ![NSFileManager.defaultManager removeItemAtPath:index error:nil]) return 0;
        PUWrite([pu_job stringByAppendingPathComponent:@"progress.json"], @{@"schema":@1, @"state":@"cancelled", @"message":@"Update cancelled"});
    }
    return 1;
}
static void update_press(int unused) {
    (void)unused; PULoad();
    if (pu_local_error && (!pu_asset || !pu_checksum || !pu_size)) { pu_local_error = NO; pu_job = nil; pu_state = PU_IDLE; }
    pu_press(mac_version, mac_fetch, mac_start);
}
static void cancel_press(int unused) { (void)unused; PULoad(); pu_cancel(mac_cancel); }
static void retry_press(int unused) {
    (void)unused; PULoad();
    if (pu_state == PU_UPDATE_FAILED || pu_state == PU_CANCELLED || pu_state == PU_FAILED) update_press(0);
}
static void log_press(int unused) {
    (void)unused; PULoad();
    if (pu_job) [NSWorkspace.sharedWorkspace openURL:[NSURL fileURLWithPath:[pu_job stringByAppendingPathComponent:@"helper.log"]]];
}
static void update_text(char *buf, int n);
static Setting s_update = {.id="online.check_updates", .category=SET_DISPLAY, .order=61, .label="Check for updates", .type=SET_ACTION,
    .help="Asks GitHub only when pressed. Update now prepares an update for the next launch.", .apply=update_press, .value_text=update_text};
static Setting s_cancel = {.id="online.cancel_update", .category=SET_DISPLAY, .order=62, .label="Cancel update", .type=SET_ACTION,
    .help="Cancel download, preparation, or a ready update before the next launch.", .apply=cancel_press};
static Setting s_retry = {.id="online.retry_update", .category=SET_DISPLAY, .order=63, .label="Retry update", .type=SET_ACTION,
    .help="Start a fresh attempt after a failure or cancellation.", .apply=retry_press};
static Setting s_log = {.id="online.update_log", .category=SET_DISPLAY, .order=64, .label="Open log", .type=SET_ACTION,
    .help="Open the local update log for details and corrective steps.", .apply=log_press};
static void update_text(char *buf, int n) {
    PULoad(); pu_status(buf, n); pu_status(pu_help, sizeof pu_help); s_update.help = pu_help;
    s_update.label = pu_state == PU_NEWER ? "Update now" : (pu_state == PU_UPDATE_FAILED || pu_state == PU_CANCELLED) ? "Retry update" : (pu_busy() || pu_state == PU_READY) ? "Update status" : "Check for updates";
}
__attribute__((constructor)) static void pub_update_register(void) {
    settings_add(&s_update); settings_add(&s_cancel); settings_add(&s_retry); settings_add(&s_log);
}
#endif
