"""Native update delivery, real loopback HTTP + read-only DMGs and ad-hoc Setup signatures.
Proof: 2 good release flows verify; 12 hostile releases reject; 8 guard mutants and
1 packaging mutant are detected (including checksum bypass and interrupted promotion).
Review proof: 7 good protections pass / 7 broken controls detected: ACL, open-fd
validation, losing lock, busy detach, SIGTERM, partial attach and progress-failure cleanup/retry.
Expected-size mismatch, interrupted process, clean Retry and job locking also pass.
Transport proof: 8 prefix sizes reject before promotion with the completion guard;
its mutant promotes all 8. Before the delivery barrier, the 1-byte prefix delivered
0 bytes and hit the checksum guard instead, masking the disabled completion guard.
No external network or release dependencies. runtime/*_test.py discovery registers this test.
The production binary is checked for absence of the fixture URL environment variable.
"""
import contextlib
import fcntl
import hashlib
import http.server
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parent
TAG = 'v4.2.0'
ASSET = 'NFS-Most-Wanted-Native-Setup-v4.2.0.dmg'
SUM = ASSET + '.sha256'
PREFIX = '/elforeign/nfs-most-wanted-mac/releases/download/' + TAG + '/'
SETUP = 'NFS Most Wanted Native Setup.app'
CAP = 1024 * 1024


def run(args, **kwargs):
    result = subprocess.run([str(x) for x in args], capture_output=True, text=True, timeout=120, **kwargs)
    assert result.returncode == 0, '%s exit %s\nstdout: %s\nstderr: %s' % (args[0], result.returncode, result.stdout[-1000:], result.stderr[-2000:])
    return result


def build(root, name, patch=None, testing=True):
    source = (ROOT / 'pub_update_helper.m').read_text()
    if patch:
        for before, after in (patch if isinstance(patch, list) else [patch]):
            assert source.count(before) == 1, before
            source = source.replace(before, after)
    path = root / (name + '.m')
    path.write_text(source)
    exe = root / name
    run(['clang', '-fobjc-arc', '-Wall', '-Wextra', '-Werror', '-Wno-deprecated-declarations',
         *(['-DPUB_UPDATE_HELPER_TESTING=1'] if testing else []), '-I', ROOT, path, '-framework', 'Foundation', '-o', exe])
    return exe


def truncated_prefix(data):
    # Deliver whole network buffer blocks: tiny bodies can remain buffered until
    # completion, and premature EOF can discard them. Padding is only for this
    # hostile transport fixture; it need not survive the later DMG validation.
    prefix = data[:-128]
    return prefix + bytes((-len(prefix)) % 65536)


def image(root, name, mode='good'):
    folder = root / name
    app = folder / SETUP
    contents = app / 'Contents'
    (contents / 'MacOS').mkdir(parents=True)
    (contents / 'Resources').mkdir()
    (contents / 'Resources/sealed.txt').write_text('original contents\n')
    info = dict(CFBundleIdentifier='org.nfsmw-native.setup', CFBundleExecutable='Setup',
                CFBundlePackageType='APPL', CFBundleName='Setup', CFBundleShortVersionString='4.2.0',
                CFBundleVersion='4.2.0')
    if mode == 'version':
        info['CFBundleVersion'] = '4.1.0'
    (contents / 'Info.plist').write_bytes(plistlib.dumps(info))
    shutil.copy2(root / 'fixture', contents / 'MacOS/Setup')
    if mode != 'unsigned':
        run(['/usr/bin/codesign', '--force', '--sign', '-', '--timestamp=none', app])
        run(['/usr/bin/codesign', '--verify', '--deep', '--strict', app])
    if mode == 'modified':
        (contents / 'Resources/sealed.txt').write_text('changed after signing\n')
    dmg = root / (name + '.dmg')
    # makehybrid builds the filesystem directly; create -srcfolder first stages a mount
    # under /Volumes, outside a test job's writable directories.
    hybrid = root / (name + '-hybrid.iso')
    made = run(['/usr/bin/hdiutil', 'makehybrid', '-udf', '-udf-volume-name', 'UpdateFixture', '-o', hybrid, folder])
    assert hybrid.exists(), made.stdout + '\n' + made.stderr + '\n' + '\n'.join(p.name for p in root.iterdir())
    run(['/usr/bin/hdiutil', 'convert', hybrid, '-format', 'UDZO', '-o', dmg])
    data = dmg.read_bytes()
    assert len(data) < CAP
    return data


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, address=('127.0.0.1', 0)):
        super().__init__(address, Handler)
        self.mode = 'good'
        self.data = b''
        self.requests = []
        self.slow_started = threading.Event()
        self.slow_finish = threading.Event()
        self.partial_path = None
        self.truncated_delivered = False


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def log_message(self, *args):
        pass

    def do_GET(self):
        server = getattr(self.server, 'main', self.server)
        mode = server.mode
        server.requests.append((self.path, self.headers.get('Range')))
        if self.path == PREFIX + SUM:
            data = server.data
            if mode == 'truncated':
                data = truncated_prefix(data)  # only transport completion rejects this checksum-matched prefix
            digest = '0' * 64 if mode == 'checksum' else hashlib.sha256(data).hexdigest()
            name = 'other.dmg' if mode == 'missing' else ASSET
            body = (digest + '  ' + name + '\n').encode()
            if mode == 'duplicate':
                body *= 2
            if mode == 'sum-oversize':
                body += b' ' * 4096
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path not in (PREFIX + ASSET, '/delivery', '/foreign'):
            self.send_error(404)
            return
        if self.path == PREFIX + ASSET and mode in ('foreign', 'redirect', 'redirect-loop'):
            host = '127.1' if mode == 'foreign' else '127.0.0.1'
            path = PREFIX + ASSET if mode == 'redirect-loop' else '/foreign' if mode == 'foreign' else '/delivery'
            self.send_response(302)
            self.send_header('Location', 'http://%s:%s%s' % (host, server.server_port, path))
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        body = server.data
        length = len(body)
        if mode == 'truncated':
            body = truncated_prefix(body)
            length = len(body) + 128
        if mode in ('oversize', 'stream-oversize'):
            body = b'x' * (CAP + 1)
            length = len(body)
        self.send_response(200)
        if mode != 'stream-oversize':
            self.send_header('Content-Length', str(length))
        else:
            self.send_header('Connection', 'close')
            self.close_connection = True
        self.end_headers()
        try:
            if mode == 'slow':
                self.wfile.write(body[:64])
                self.wfile.flush()
                server.slow_started.set()
                server.slow_finish.wait(30)
                body = body[64:]
            self.wfile.write(body)
            self.wfile.flush()
            if mode == 'truncated':
                # EOF can make NSURLSession discard buffered body bytes. Ensure the
                # advertised checksum's prefix reached the helper before breaking
                # transport, so only the completion guard rejects it.
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    try:
                        server.truncated_delivered = server.partial_path.stat().st_size == len(body)
                    except FileNotFoundError:
                        pass
                    if server.truncated_delivered:
                        break
                    time.sleep(0.01)
        except (BrokenPipeError, ConnectionResetError):
            pass
        if mode == 'truncated':
            self.close_connection = True


@contextlib.contextmanager
def fixture():
    server = Server()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.slow_finish.set()
        server.shutdown()
        server.server_close()
        thread.join()


def detach(job):
    # Only this test's private, deterministic mount path is ever detached.
    result = subprocess.run(['/usr/bin/hdiutil', 'detach', str(job / 'mount')], capture_output=True, timeout=20)
    return result.returncode == 0


def invoke(exe, root, server, data, mode='good', job=None, args=None, keep_mount=False):
    server.data = data
    server.mode = mode
    server.requests.clear()
    job = job or root / ('job-' + exe.name + '-' + mode)
    job.mkdir(mode=0o700, exist_ok=True)
    server.partial_path = job / (ASSET + '.part')
    server.truncated_delivered = False
    env = dict(os.environ, PUB_UPDATE_HELPER_TEST_BASE_URL='http://127.0.0.1:%s' % server.server_port)
    command = [str(exe), str(job), TAG, ASSET, SUM] + (args or [])
    try:
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=150)
        progress = json.loads((job / 'progress.json').read_text())
        if mode == 'truncated':
            assert server.truncated_delivered, 'truncated fixture did not deliver its full checksum prefix'
        mounted = progress.get('state') == 'verified'
        if mounted:
            assert (Path(progress['setup_path']) / 'Contents/Info.plist').is_file(), progress
            assert 'read-only' in run(['/sbin/mount']).stdout.split(str(job / 'mount'))[-1].split('\n')[0]
        assert not (job / 'execution-sentinel').exists(), 'Setup was executed'
        return result, progress, job, list(server.requests)
    finally:
        if not keep_mount:
            detach(job)


def rejected(result):
    rc, progress, job, _ = result
    return rc.returncode != 0 and progress['state'] == 'failed' and not progress.get('setup_path')


def bundle_check(root, release):
    """Run the actual bundle hook, compiling both update executables with harmless icon-tool fixtures."""
    runtime = root / 'package/runtime'
    runtime.mkdir(parents=True)
    shutil.copy2(ROOT / 'bundle.sh', runtime / 'bundle.sh')
    (runtime / 'docs').mkdir()
    shutil.copy2(ROOT / 'docs/guide.md', runtime / 'docs/guide.md')
    for name in ('pub_update_helper.m', 'pub_update.m', 'pub_update_core.h', 'pub_update_rehearsal.h', 'pub_update_write.h'):
        shutil.copy2(ROOT / name, runtime / name)
    shutil.copy2(release, runtime / 'boot')
    (runtime / 'app_icon.m').touch()
    tools = root / 'package/bin'
    tools.mkdir()
    scripts = {
        'clang': '''case "$*" in *pub_update_helper.m*|*pub_update.m*) exec /usr/bin/clang "$@" ;; esac
while [ "$1" != -o ]; do shift; done
shift
printf '#!/bin/sh\\ntouch "$1"\\n' > "$1"
chmod +x "$1"
''',
        'sips': 'exit 0\n',
        'iconutil': 'while [ "$1" != -o ]; do shift; done\nshift\ntouch "$1"\n',
    }
    for name, text in scripts.items():
        path = tools / name
        path.write_text('#!/bin/sh\n' + text)
        path.chmod(0o755)
    env = dict(os.environ, PATH=str(tools) + ':/usr/bin:/bin:/usr/sbin:/sbin', NFS_PUBLIC='1')
    dest = root / 'package/output'
    run(['/bin/sh', runtime / 'bundle.sh', dest, '--version', '4.2.0'], env=env)
    helper = dest / 'NFSMW.app/Contents/MacOS/pub_update_helper'
    assert helper.is_file() and os.access(helper, os.X_OK)
    prep_tool = dest / 'NFSMW.app/Contents/MacOS/pub_update_worker'
    assert prep_tool.is_file() and os.access(prep_tool, os.X_OK)
    run(['/usr/bin/codesign', '--verify', '--deep', '--strict', dest / 'NFSMW.app'])
    assert 'PUB_UPDATE_HELPER_TEST_BASE_URL' not in run(['/usr/bin/strings', helper]).stdout
    for binary in (helper, prep_tool):
        assert 'PUB_UPDATE_REHEARSAL_BASE_URL' not in run(['/usr/bin/strings', binary]).stdout
    # A packaging mutation must lose this executable.
    script = runtime / 'bundle.sh'
    text = script.read_text()
    text = text.replace('cp "$TMP/pub_update_helper" "$APP/Contents/MacOS/pub_update_helper"', ':')
    text = text.replace('codesign --force --sign - --timestamp=none "$APP/Contents/MacOS/pub_update_helper"', ':')
    script.write_text(text)
    run(['/bin/sh', script, dest, '--version', '4.2.0'], env=env)
    assert not helper.exists(), 'packaging broken control still included the helper'


def await_file(path, process):
    deadline = time.monotonic() + 20
    while not path.exists():
        assert process.poll() is None, 'helper exited before barrier'
        assert time.monotonic() < deadline, 'helper barrier timed out'
        time.sleep(0.02)


def review_checks(root, exe, server, data, good_data):
    """Review regressions: 7 good protections / 7 broken controls detected.
    Controls restore the old ACL omission, path-before-open check, catch lock retry,
    ignored detach failure, default SIGTERM, loss of a failed attach's device,
    and cleanup aborted by a progress write failure.
    Real mounts are checked before cleanup, so a failed receipt alone cannot pass.
    """
    env = dict(os.environ, PUB_UPDATE_HELPER_TEST_BASE_URL='http://127.0.0.1:%s' % server.server_port)
    def command(binary, job):
        return [str(binary), str(job), TAG, ASSET, SUM]
    def mounted(job):
        return str(job / 'mount') + ' (' in run(['/sbin/mount']).stdout
    def execute(binary, job):
        return subprocess.run(command(binary, job), env=env, capture_output=True, text=True, timeout=30)

    # A mode-0700 directory may still grant another principal write access via ACL.
    acl_broken = build(root, 'broken-acl', ('type == ACL_EXTENDED_ALLOW', '(NO && type == ACL_EXTENDED_ALLOW)'))
    for binary, good in ((exe, True), (acl_broken, False)):
        job = root / ('acl-' + binary.name)
        job.mkdir(mode=0o700)
        receipt = job / 'progress.json'
        receipt.write_text('{"state":"verified","winner":true}')
        before = receipt.read_bytes()
        run(['/bin/chmod', '+a', 'everyone allow add_file,delete_child', job])
        try:
            server.data = data; server.mode = 'checksum'; server.requests.clear()
            result = execute(binary, job)
            protected = result.returncode != 0 and receipt.read_bytes() == before and not server.requests
            assert protected == good, (binary, result.stderr)
        finally:
            run(['/bin/chmod', '-N', job])
            detach(job)

    # Exchange the pathname after open: validate the original fd, not its replacement.
    opened = 'jobFD = open(argv[1], O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);'
    exchange = opened + """
            NSString *saved = [[NSString stringWithUTF8String:argv[1]] stringByAppendingString:@".opened"];
            rename(argv[1], saved.fileSystemRepresentation);
            chmod(saved.fileSystemRepresentation, 0755);
            mkdir(argv[1], 0700);
"""
    for good in (True, False):
        patches = [(opened, exchange)]
        if not good:
            patches.append(('fstat(fd, &st)', 'stat([[NSProcessInfo.processInfo.arguments objectAtIndex:1] fileSystemRepresentation], &st)'))
        binary = build(root, 'opened-' + str(good), patches)
        job = root / ('directory-' + str(good)); job.mkdir(mode=0o700)
        result = execute(binary, job)
        assert ('private directory' in result.stderr) == good, result.stderr

    # Hold the winner's lock until the loser has failed its first attempt. Publish
    # the winner's receipt and release before letting the losing catch continue.
    failure = 'if (lockFD < 0 || flock(lockFD, LOCK_EX | LOCK_NB)) Fail(@"Another update helper is using this job.");'
    barrier = """if (lockFD < 0 || flock(lockFD, LOCK_EX | LOCK_NB)) {
                int ready = openat(jobFD, "loser-ready", O_CREAT | O_WRONLY, 0600); close(ready);
                while (faccessat(jobFD, "winner-done", F_OK, 0)) usleep(10000);
                Fail(@"Another update helper is using this job.");
            }"""
    for good in (True, False):
        patches = [(failure, barrier)]
        if not good:
            patches.append(('if (ownsLock) {', 'if ((ownsLock || !ownsLock) && jobFD >= 0 && lockFD >= 0 && !flock(lockFD, LOCK_EX | LOCK_NB)) {'))
        binary = build(root, 'loser-' + str(good), patches)
        job = root / ('lock-' + str(good)); job.mkdir(mode=0o700)
        with (job / 'helper.lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            process = subprocess.Popen(command(binary, job), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                await_file(job / 'loser-ready', process)
                receipt = job / 'progress.json'
                receipt.write_text('{"state":"verified","winner":true}')
                before = receipt.read_bytes()
                fcntl.flock(lock, fcntl.LOCK_UN)
                (job / 'winner-done').touch()
                assert process.wait(timeout=15) != 0
                assert (receipt.read_bytes() == before) == good
            finally:
                if process.poll() is None:
                    process.kill(); process.wait(timeout=10)

    # Simulate a busy detach after a real attach and a signature rejection. The
    # good helper records a retryable device and the next normal invocation cleans it.
    tool = 'static BOOL RunTool(NSString *tool, NSArray<NSString *> *arguments) {'
    busy = tool + '\n    if ([arguments.firstObject isEqual:@"detach"]) return NO;'
    for good in (True, False):
        patches = [(tool, busy)]
        if not good:
            patches.append(('Progress(cleaned ? @"failed" : @"cleanup_pending",', 'Progress((cleaned || !cleaned) ? @"failed" : @"cleanup_pending",'))
        binary = build(root, 'busy-' + str(good), patches)
        job = root / ('busy-job-' + str(good))
        try:
            outcome = invoke(binary, root, server, data, job=job, keep_mount=True)
            # This fixture's bundle version is deliberately rejected after attachment.
            assert outcome[0].returncode != 0 and mounted(job), outcome[:2]
            progress = outcome[1]
            recoverable = progress['state'] == 'cleanup_pending' and progress.get('attached_device', '').startswith('/dev/disk')
            assert bool(recoverable) == good, progress
            retried = invoke(exe, root, server, good_data, job=job)
            assert retried[0].returncode == 0, retried[:2]
        finally:
            detach(job)

    # SIGTERM after attach must enter checked cleanup. Default handling leaves a
    # live mount; a retry still discovers and detaches it before downloading again.
    setup_line = 'NSString *setup = [mount stringByAppendingPathComponent:SetupName];'
    pause = """int ready = openat(jobFD, "attached-ready", O_CREAT | O_WRONLY, 0600); close(ready);
    while (!cancelled) usleep(10000);
    CheckCancelled();
    """ + setup_line
    for good in (True, False):
        patches = [(setup_line, pause)]
        if not good:
            patches.append(('sigaction(SIGTERM, &action, NULL);', 'signal(SIGTERM, SIG_DFL);'))
        binary = build(root, 'term-' + str(good), patches)
        job = root / ('term-job-' + str(good)); job.mkdir(mode=0o700)
        server.data = good_data; server.mode = 'good'
        process = subprocess.Popen(command(binary, job), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            await_file(job / 'attached-ready', process)
            assert mounted(job)
            process.terminate(); assert process.wait(timeout=30) != 0
            progress = json.loads((job / 'progress.json').read_text())
            assert (not mounted(job) and progress['state'] == 'failed') == good, progress
            retried = invoke(exe, root, server, good_data, job=job)
            assert retried[0].returncode == 0, retried[:2]
        finally:
            if process.poll() is None:
                process.kill(); process.wait(timeout=10)
            detach(job)

    # hdiutil can attach a device then return failure. Cleanup must discover it even
    # before VerifyImage records a mount; skipping that discovery restores the leak.
    finished = 'return task.terminationStatus == 0;'
    partial = 'if ([arguments.firstObject isEqual:@"attach"]) return NO;\n    ' + finished
    for good in (True, False):
        patches = [(finished, partial)]
        if not good:
            patches.append(('if (mount) cleaned = CleanupImage(mount);', 'if (mount) cleaned = YES;'))
        binary = build(root, 'partial-attach-' + str(good), patches)
        job = root / ('partial-attach-job-' + str(good))
        try:
            outcome = invoke(binary, root, server, good_data, job=job, keep_mount=True)
            assert outcome[0].returncode != 0
            assert (not mounted(job)) == good, outcome[:2]
            if not good:
                assert invoke(exe, root, server, good_data, job=job)[0].returncode == 0
        finally:
            detach(job)
    # A progress write failure during cleanup must not skip the real detach.
    progress_fn = 'static void Progress(NSString *state, NSString *message, uint64_t received, uint64_t total, NSDictionary *extra) {'
    no_receipt = progress_fn + '\n    if ([message isEqual:@"Detaching the update image."]) Fail(@"Fixture: progress disk full.");'
    for good in (True, False):
        patches = [(progress_fn, no_receipt)]
        if not good:
            # Restore the old cleanup behavior: a write failure escapes to main.
            patches.append(('} @catch (NSException *ignored) { (void)ignored; }\n        if (!RunTool',
                            '} @catch (NSException *ignored) { @throw ignored; }\n        if (!RunTool'))
        binary = build(root, 'receipt-failure-' + str(good), patches)
        job = root / ('receipt-failure-job-' + str(good))
        try:
            outcome = invoke(binary, root, server, data, job=job, keep_mount=True)
            assert outcome[0].returncode != 0
            assert (not mounted(job)) == good, outcome[:2]
            assert invoke(exe, root, server, good_data, job=job)[0].returncode == 0
        finally:
            detach(job)
    print('REVIEW: 7 good protections PASS; 7 broken controls detected; cleanup/retry PASS')


def main():
    with tempfile.TemporaryDirectory(prefix='update-delivery-') as temp:
        root = Path(temp).resolve()
        source = root / 'fixture.c'
        source.write_text('#include <stdio.h>\nint main(void) { FILE *f=fopen("execution-sentinel", "w"); if(f) fclose(f); return 0; }\n')
        run(['clang', source, '-o', root / 'fixture'])
        # Exercise transport independently of DMG validity at eight buffer sizes.
        exe = build(root, 'helper')
        transport = build(root, 'transport-mutant',
            ('if (!_failure && (error || !_received || (_total && _received != _total) || (_expected && _received != _expected)))',
             'if (!_failure && NO && (error || !_received))'))
        with fixture() as server:
            for length in range(65536, 524289, 65536):
                data = b'x' * (length + 128)
                for binary, broken in ((exe, False), (transport, True)):
                    outcome = invoke(binary, root, server, data, 'truncated',
                                     job=root / ('transport-%s-%s' % (binary.name, length)))
                    path = outcome[2] / (ASSET if broken else ASSET + '.part')
                    assert path.is_file() and path.stat().st_size == length, (length, broken, outcome[:2])
                    assert (outcome[2] / ASSET).exists() == broken, (length, broken, outcome[:2])
                    if not broken:
                        assert rejected(outcome), outcome[:2]
        print('TRANSPORT: 8 complete prefixes; good REJECT before promotion / broken PROMOTED')
        images = {mode: image(root, 'image-' + mode, mode) for mode in ('good', 'unsigned', 'modified', 'version')}
        release = build(root, 'release', testing=False)
        assert 'PUB_UPDATE_HELPER_TEST_BASE_URL' not in run(['/usr/bin/strings', release]).stdout
        assert 'PUB_UPDATE_REHEARSAL_BASE_URL' not in run(['/usr/bin/strings', release]).stdout
        names = run(['/bin/sh', ROOT.parent / 'tools/installer/build_installer.sh', '--print-names', TAG]).stdout.splitlines()
        assert names == [ASSET, SUM]
        bundle_check(root, release)
        with fixture() as server:
            review_checks(root, exe, server, images['version'], images['good'])
            for mode in ('good', 'redirect'):
                result, progress, job, requests = invoke(exe, root, server, images['good'], mode, args=[str(len(images['good']))])
                assert result.returncode == 0 and progress['state'] == 'verified', result.stderr + '\n' + (job / 'helper.log').read_text()[-2000:]
                assert progress['sha256'] == hashlib.sha256(images['good']).hexdigest()
                assert not (job / (ASSET + '.part')).exists()
                assert [x[0] for x in requests][:2] == [PREFIX + SUM, PREFIX + ASSET]
                assert all(r is None for _, r in requests)
            bad_modes = ('checksum', 'truncated', 'foreign', 'oversize', 'stream-oversize', 'missing',
                         'duplicate', 'sum-oversize', 'redirect-loop')
            for mode in bad_modes:
                outcome = invoke(exe, root, server, images['good'], mode)
                assert rejected(outcome), (mode, outcome[:2])
                if mode in ('checksum', 'truncated'):
                    assert not (outcome[2] / ASSET).exists(), mode + ' promoted a bad partial'
                if mode == 'foreign':
                    assert not any(p == '/foreign' for p, _ in outcome[3]), outcome[3]
                print(mode, 'good REJECT')
            for mode in ('unsigned', 'modified', 'version'):
                assert rejected(invoke(exe, root, server, images[mode], job=root / ('job-image-' + mode))), mode
                print(mode, 'good REJECT')
            assert rejected(invoke(exe, root, server, images['good'], args=[str(len(images['good']) + 1)]))
            # Retry owns cleanup of a previously verified mount, even if the caller exited.
            retained = root / 'retained-mount'
            try:
                first = invoke(exe, root, server, images['good'], job=retained, keep_mount=True)
                assert first[0].returncode == 0 and first[1]['state'] == 'verified'
                second = invoke(exe, root, server, images['good'], job=retained)
                assert second[0].returncode == 0 and second[1]['state'] == 'verified', second[0].stderr
            finally:
                detach(retained)
            # Retry after a failed checksum clears both partials, stale verified state and final assets.
            retry = root / 'retry'
            assert rejected(invoke(exe, root, server, images['good'], 'checksum', job=retry))
            (retry / 'progress.json').write_text('{"state":"verified"}')
            result, progress, _, requests = invoke(exe, root, server, images['good'], job=retry)
            assert result.returncode == 0 and progress['state'] == 'verified', progress
            assert len(requests) == 2 and all(r is None for _, r in requests)
            # A process killed with a partial stream never publishes the DMG or a verified receipt.
            job = root / 'interrupted'
            job.mkdir(mode=0o700)
            server.data = images['good']; server.mode = 'slow'
            env = dict(os.environ, PUB_UPDATE_HELPER_TEST_BASE_URL='http://127.0.0.1:%s' % server.server_port)
            process = subprocess.Popen([str(exe), str(job), TAG, ASSET, SUM], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                assert server.slow_started.wait(15), 'slow download did not begin'
                # A second helper cannot overwrite the active helper's progress or partial file.
                second = subprocess.run([str(exe), str(job), TAG, ASSET, SUM], env=env, capture_output=True, timeout=15)
                assert second.returncode != 0
                process.terminate(); process.wait(timeout=10)
                progress = json.loads((job / 'progress.json').read_text())
                assert progress['state'] != 'verified' and not (job / ASSET).exists()
                assert (job / (ASSET + '.part')).exists()
            finally:
                if process.poll() is None:
                    process.kill(); process.wait(timeout=10)
                server.slow_finish.set()
            result, progress, _, _ = invoke(exe, root, server, images['good'], job=job)
            assert result.returncode == 0 and progress['state'] == 'verified', progress
            # Guard mutants: same hostile fixtures must expose a bypass, so the rejection check fails.
            mutants = {
                'checksum': ('if (![hash isEqualToString:download[@"sha256"]])', 'if (NO)'),
                'truncated': ('if (!_failure && (error || !_received || (_total && _received != _total) || (_expected && _received != _expected)))',
                              'if (!_failure && NO && (error || !_received))'),
                'foreign': ('if (++_redirects > 3 || !AllowedRedirect(request.URL))', 'if (++_redirects > 3 || (NO && !AllowedRedirect(request.URL)))'),
                'missing': ('if (!m) Fail(@"Release checksum has no unique, valid entry for the expected DMG.");',
                            'if (!m) return [text substringToIndex:64];'),
                'signature': ('if (!RunTool(@"/usr/bin/codesign", @[@"--verify", @"--deep", @"--strict", setup]))', 'if (NO)'),
                'version': ('![info[@"CFBundleVersion"] isEqual:version])', 'NO)'),
            }
            for name, patch in mutants.items():
                mutant = build(root, 'broken-' + name, patch)
                mode = 'good' if name in ('signature', 'version') else name
                data = images['unsigned'] if name == 'signature' else images['version'] if name == 'version' else images['good']
                outcome = invoke(mutant, root, server, data, mode)
                if name == 'truncated':
                    partial = outcome[2] / (ASSET + '.part')
                    assert (outcome[2] / ASSET).exists(), (
                        'broken incomplete-download control did not promote the partial', outcome[0].stderr,
                        outcome[1], partial.stat().st_size if partial.exists() else None,
                        len(truncated_prefix(data)), hashlib.sha256(partial.read_bytes()).hexdigest() if partial.exists() else None,
                        hashlib.sha256(truncated_prefix(data)).hexdigest())
                    print(name, 'good REJECT before promotion, broken PROMOTED')
                    continue
                assert not rejected(outcome), (name, 'broken control still rejected', outcome[0].stderr)
                if name == 'foreign':
                    assert any(p == '/foreign' for p, _ in outcome[3])
                if name == 'signature':
                    assert not rejected(invoke(mutant, root, server, images['modified'], job=root / 'broken-modified-signature'))
                print(name, 'good REJECT, broken ACCEPT')
            # Cap mutants cannot produce a valid DMG; detect bypass at the network/write boundary.
            mutant = build(root, 'broken-cap', ('static const uint64_t AssetCap = 1024 * 1024;', 'static const uint64_t AssetCap = 2 * 1024 * 1024;'))
            for mode in ('oversize', 'stream-oversize'):
                good = invoke(exe, root, server, images['good'], mode)
                broken = invoke(mutant, root, server, images['good'], mode)
                good_size = (good[2] / (ASSET + '.part')).stat().st_size
                broken_size = (broken[2] / (ASSET + '.part')).stat().st_size
                assert good_size <= CAP and broken_size > CAP, (mode, good_size, broken_size)
                print(mode, 'good bounded %d, broken %d' % (good_size, broken_size))
    print('PUB UPDATE HELPER PASS: 2 verified releases; 12 hostile releases rejected; 8 guard + 1 packaging mutants detected; 7 review controls detected; interruption/retry/lock PASS')


if __name__ == '__main__':
    main()
