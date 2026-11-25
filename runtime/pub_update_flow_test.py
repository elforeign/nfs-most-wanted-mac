"""























"""
import io
import http.server
import json
import os
from pathlib import Path
import runpy
import shlex
import shutil
import subprocess
import sys
import tempfile
import tarfile
import threading
import time
from unittest import mock

ROOT = Path(__file__).resolve().parent
PROOF = runpy.run_path(str(ROOT / 'pub_update_helper_test.py'))


def run(args, **kwargs):
    result = subprocess.run(list(map(str, args)), capture_output=True, text=True, timeout=90, **kwargs)
    assert result.returncode == 0, (result.returncode, result.stdout[-2000:], result.stderr[-3000:])
    return result


DRIVER = r'''
static int requests, jobs, held = -1;
static NSString *fixture;
static Setting *registered[8]; static int rows;
void settings_add(Setting *s) { registered[rows++] = s; }
const char *userdata_dir(void) { return fixture.UTF8String; }
static int fixture_version(char *out, size_t cap) { snprintf(out, cap, "4.0.0"); return 1; }
static int fixture_fetch(void) { ++requests; return 1; }
static int fixture_start(const char *version) {
    (void)version; ++jobs;
    if (held >= 0) close(held);
    pu_job = [fixture stringByAppendingPathComponent:[NSString stringWithFormat:@"job%d", jobs]];
    [NSFileManager.defaultManager createDirectoryAtPath:pu_job withIntermediateDirectories:YES attributes:nil error:nil];
    held = open([pu_job stringByAppendingPathComponent:@"prepare.lock"].fileSystemRepresentation, O_CREAT | O_WRONLY, 0600);
    flock(held, LOCK_EX | LOCK_NB);
    PUWrite([pu_job stringByAppendingPathComponent:@"progress.json"], @{@"state":@"downloading"});
    return 1;
}
static void put(NSString *state, NSString *message) {
    PUWrite([pu_job stringByAppendingPathComponent:@"progress.json"], @{@"schema":@1, @"state":state, @"message":message,
        @"received_bytes":@1500000, @"total_bytes":@3000000});
}
static void show(const char *name) { char text[1400]; update_text(text, sizeof text); printf("%s=%s\n", name, text); }
int main(int argc, char **argv) { @autoreleasepool {
    (void)argc; fixture = PUCanonical([NSString stringWithUTF8String:argv[1]]); pu_loaded = YES;
    show("idle"); for (int i=0; i<100; ++i) PULoad();
    printf("initial_requests=%d\n", requests);
    printf("rows=%d,%d,%d,%d,%d\n", rows, registered[0]->type, registered[1]->type, registered[2]->type, registered[3]->type);
    update_press(0); show("checking"); update_press(0);
    pu_done(1, "{\"tag_name\":\"v4.2.0\"}"); show("offer"); printf("label=%s\n", s_update.label);
    update_press(0); for (int i=0; i<20; ++i) update_press(0);
    printf("clicked=%d,%d\n", requests, jobs);
    put(@"downloading", @"installer"); show("download");
    put(@"verifying", @"checksum"); show("verify");
    put(@"verified", @"Preparing the update"); show("verified");
    put(@"building", @"Building audio (1/2)"); show("build");
    cancel_press(0); show("cancelling");
    printf("cancel_file=%d\n", [NSFileManager.defaultManager fileExistsAtPath:[pu_job stringByAppendingPathComponent:@"cancel"]]);
    put(@"cancelled", @"cancelled"); close(held); held=-1; show("cancelled");
    retry_press(0); show("retried"); printf("retried_jobs=%d\n", jobs);
    NSString *ready = [pu_job stringByAppendingPathComponent:@"staged/ready.json"];
    [NSFileManager.defaultManager createDirectoryAtPath:ready.stringByDeletingLastPathComponent withIntermediateDirectories:YES attributes:nil error:nil];
    NSString *dest=PUCanonical(NSBundle.mainBundle.bundlePath);
    PUWrite(ready, @{@"format_version":@1, @"version":@"4.2.0", @"destination":@{@"path":dest}});
    NSString *index=[fixture stringByAppendingPathComponent:@".update-ready.json"];
    PUWrite(index, @{@"format_version":@1, @"job":pu_job, @"ready":ready, @"profile":fixture, @"destination":dest});
    put(@"ready", @"ready"); show("ready_running");
    close(held); held=-1; show("ready");
    for (int i=0; i<10; ++i) update_press(0); printf("ready_jobs=%d\n", jobs);
    cancel_press(0); show("ready_cancelled");
    printf("index_removed=%d\n", ![NSFileManager.defaultManager fileExistsAtPath:index]);
    retry_press(0); put(@"failed", @"The downloaded DMG does not match the release SHA256 checksum");
    close(held); held=-1; show("checksum");
    put(@"ready", @"forged success"); show("false_ready");
    const char *errors[] = {"SOURCE_CHOICES_MISSING: Setup has no remembered source choices. Run Setup once, or supply --pc, --x360 and --dest", "Missing developer tools. Install or update Apple developer tools", "Permission denied. Reconnect the source volume and check folder access"};
    for (int i=0; i<3; ++i) { put(@"failed", [NSString stringWithUTF8String:errors[i]]); char name[20]; snprintf(name,sizeof name,"failure%d",i); show(name); }
    printf("final_requests=%d\n", requests);
    return 0;
} }
'''


def native_flow(root, patch=None):
    source = (ROOT / 'pub_update.m').read_text()
    header = (ROOT / 'pub_update_core.h').read_text()
    # Substitute OS request/start boundaries; all actions and the local progress
    # reader remain production code. Calling the request boundary is observable.
    a = source.index('static int mac_version(')
    b = source.index('static int mac_fetch(', a)
    c = source.index('static void PULocalError(', b)
    d = source.index('static int mac_start(', c)
    e = source.index('static int mac_cancel(', d)
    source = source[:a] + 'static int mac_version(char *, size_t);\nstatic int mac_fetch(void);\nstatic int mac_start(const char *);\n' + source[e:]
    source = source.replace('static int mac_version(char *, size_t);', 'static int fixture_version(char *, size_t);\n#define mac_version fixture_version')
    source = source.replace('static int mac_fetch(void);', 'static int fixture_fetch(void);\n#define mac_fetch fixture_fetch')
    source = source.replace('static int mac_start(const char *);', 'static int fixture_start(const char *);\n#define mac_start fixture_start')
    if patch:
        target, before, after = patch
        value = header if target == 'header' else source
        assert before in value, before
        value = value.replace(before, after, 1)
        if target == 'header': header = value
        else: source = value
    # Ensure an automatic constructor request is counted without touching HTTPS.
    source = source.replace('static int fixture_fetch(void);', 'static int requests;\nstatic int fixture_fetch(void);')
    path = root / 'driver.m'
    path.write_text(source + DRIVER)
    (root / 'pub_update_core.h').write_text(header)
    run(['clang', '-fobjc-arc', '-Wno-unused-function', '-I', ROOT, '-I', root,
         path, '-framework', 'Cocoa', '-framework', 'Security', '-o', root / 'flow'])
    work = root / 'profile'; work.mkdir()
    result = run([root / 'flow', work])
    return dict(line.split('=', 1) for line in result.stdout.splitlines())


def checks(r):
    return {
        'opt_in': r['initial_requests'] == '0' and r['final_requests'] == '1',
        'single_job': r['clicked'] == '1,1',
        'offer': r['offer'] == '4.2.0 is out: Update now' and r['label'] == 'Update now',
        'download': r['download'] == 'Downloading... 1.5/3.0 MB',
        'verify': r['verify'] == 'Verifying...' and r['verified'] == 'Preparing the update',
        'build': r['build'] == 'Building audio (1/2)',
        'cancel': r['cancelling'] == 'Cancelling...' and r['cancel_file'] == '1' and r['cancelled'].startswith('Update cancelled'),
        'retry': r['retried_jobs'] == '2' and r['retried'].startswith('Downloading...'),
        'ready': r['ready'] == 'Ready - applies next launch' and r['ready_jobs'] == '2' and r['ready_running'] != r['ready'] and r['final_requests'] == '1',
        'cancel_ready': r['index_removed'] == '1' and r['ready_cancelled'].startswith('Update cancelled'),
        'false_success': r['checksum'].startswith('Update failed: The downloaded DMG') and r['false_ready'].startswith('Update failed:'),
        'failures_and_controller': all(r[f'failure{i}'].startswith('Update failed:') and r[f'failure{i}'].endswith('Your previous version is available.') for i in range(3)) and r['rows'] == '4,4,4,4,4' and 'Open Setup and select' in r['failure0'] and '--pc' not in r['failure0'] and 'Install or update Apple developer tools' in r['failure1'] and 'check folder access' in r['failure2'],
    }


MUTANTS = [
    ('opt_in', ('source', 'settings_add(&s_update); settings_add(&s_cancel);',
                'mac_fetch(); settings_add(&s_update); settings_add(&s_cancel);')),
    ('single_job', ('header', '|| pu_busy() || pu_state == PU_READY', '|| pu_state == PU_READY')),
    ('download', ('header', 'pu_received / 1000000', 'pu_received / 1000')),
    ('cancel', ('header', 'if (!cancel())', 'if (1)')),
    ('retry', ('source', 'pu_state == PU_UPDATE_FAILED || pu_state == PU_CANCELLED || pu_state == PU_FAILED', 'pu_state == PU_UPDATE_FAILED || pu_state == PU_FAILED')),
    ('ready', ('header', '|| pu_state == PU_READY) return;', ') return;')),
    ('cancel_ready', ('source', '&& ![NSFileManager.defaultManager removeItemAtPath:index error:nil]', '&& NO')),
    ('false_success', ('header', '!strcmp(state, "ready") && ready_valid', '!strcmp(state, "ready")')),
    ('failures_and_controller', ('header', 'strstr(reason, "SOURCE_CHOICES")', 'strstr(reason, "UNRECOGNIZED_SOURCE_ERROR")')),
]


def bridge_script():
    text = (ROOT / 'pub_update.m').read_text().split('static NSString *const PrepareScript =\n', 1)[1].split('\n;', 1)[0]
    text = text.replace('" PU_STAGE_GAME "', '')  # Most Wanted's expansion (Carbon's adds --game carbon)
    return ''.join(json.loads(line[1:]) for line in text.splitlines())


def preparation_fixture(job, script):
    job.mkdir()
    setup = job / 'setup.app'
    resources = setup / 'Contents/Resources'
    (resources / 'python/bin').mkdir(parents=True)
    (resources / 'python/bin/python3').write_text('interpreter fixture')
    (resources / 'third_party/dl').mkdir(parents=True)
    with tarfile.open(resources / 'source.tar.gz', 'w:gz') as archive:
        for name, text in {
            'tools/setup_kit/__init__.py': '',
            'tools/setup_kit/backend.py': 'from pathlib import Path\nimport sys\ndef main(args):\n'
                '    job=Path(args[args.index("--stage")+1]).parent\n'
                '    assert not (job/"mount").is_mount(), "image still mounted at build"\n'
                '    assert Path(sys.executable).is_relative_to(job/"python"), sys.executable\n'
                '    (job/"built").touch()\n    return 0\n',
            'tools/setup_kit/apply.py': 'def publish_ready(*args): pass\n',
        }.items():
            data = text.encode(); member = tarfile.TarInfo(name); member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    (job / 'prepare.py').write_text(script)
    return setup


# Python releases before extraction filters need this only for the small test
# archive above. Released Setup supplies its own newer interpreter.
def extract_fixture(archive, path, members, **kwargs):
    for member in members:
        archive.extract(member, path)


ORIGINAL_DETACH = "        subprocess.run(['/usr/bin/hdiutil', 'detach', str(job / 'mount')], check=True, stdout=sys.stderr)\n"


def original_order(script):
    anchor = '        # Re-exec the retained interpreter'
    assert anchor in script
    return script.replace(anchor, ORIGINAL_DETACH + anchor, 1)


def detach_order_cases(root):
    script = bridge_script()
    class Reexecuted(BaseException): pass
    for broken in (False, True):
        for marker in (False, True):
            job = root / ('order-%s-%s' % (broken, marker))
            setup = preparation_fixture(job, script)
            if marker: (job / 'retained').touch()
            def reexec(path, args):
                assert path == str(job / 'python/bin/python3') and args[6] == '--retained', args
                raise Reexecuted()
            with mock.patch.object(sys, 'argv', ['prepare', str(job), str(setup), str(job/'profile'), str(job/'app')]), \
                 mock.patch.object(os, 'execv', side_effect=reexec) as execute, \
                 mock.patch.object(Path, 'is_mount', return_value=True), \
                 mock.patch.object(subprocess, 'run', side_effect=AssertionError('detach before re-exec')) as detach, \
                 mock.patch.object(tarfile.TarFile, 'extractall', extract_fixture):
                try:
                    exec(original_order(script) if broken else script, {})
                except Reexecuted:
                    assert not broken
                except AssertionError as error:
                    assert broken and str(error) == 'detach before re-exec', error
                else:
                    raise AssertionError('first phase did not re-exec')
                assert execute.call_count == (0 if broken else 1)
                assert detach.call_count == (1 if broken else 0)
    print('Detach order good=2 PASS; original-order broken=2 rejected (detach before re-exec)')


DETACH_DRIVER = r'''
import json, sys, subprocess, time, types
from pathlib import Path
job=Path(sys.argv[1]); codes=json.loads(sys.argv[2]); cancel=sys.argv[3]=='cancel'; events=[]
def detach(args, **kwargs):
    assert args == ['/usr/bin/hdiutil', 'detach', str(job/'mount')], args
    code=codes.pop(0); events.append(['detach', code])
    return types.SimpleNamespace(returncode=code)
def sleep(seconds):
    assert seconds == 1; events.append(['sleep', seconds])
    if cancel: (job/'cancel').touch()
def build(args): events.append(['build']); return 0
subprocess.run=detach; time.sleep=sleep
Path.is_mount=lambda self: bool(codes)
package=types.ModuleType('setup_kit'); package.backend=types.SimpleNamespace(main=build)
package.apply=types.SimpleNamespace(publish_ready=lambda *args: events.append(['publish']))
sys.modules['setup_kit']=package
sys.argv=['prepare', str(job), '--retained', str(job/'profile'), str(job/'app')]
try: exec((job/'prepare.py').read_text(), {})
except RuntimeError: pass
(job/'events.json').write_text(json.dumps(events))
'''


def detach_retry_cases(root):
    script = bridge_script()
    def check(name, codes, cancel, expected, success):
        job = root/name; job.mkdir(); (job/'prepare.py').write_text(script)
        run([sys.executable, '-I', '-B', '-c', DETACH_DRIVER, job, json.dumps(codes), 'cancel' if cancel else 'run'])
        events = json.loads((job/'events.json').read_text())
        assert events == expected + ([['build'], ['publish']] if success else []), (name, events)
        progress = json.loads((job/'progress.json').read_text())
        assert progress['state'] == ('ready' if success else 'failed'), progress
        if not success:
            assert ('cancelled' if cancel else 'Cannot detach the update image') in progress['message'], progress
    check('absent', [], False, [], True)
    check('immediate', [0], False, [['detach', 0]], True)
    check('busy', [16, 16, 0], False, [['detach', 16], ['sleep', 1]]*2 + [['detach', 0]], True)
    check('exhausted', [16]*5, False, [['detach', 16], ['sleep', 1]]*4 + [['detach', 16]], False)
    check('error', [1], False, [['detach', 1]], False)
    check('cancel', [16, 0], True, [['detach', 16], ['sleep', 1]], False)
    for name, before, after, codes in [
        ('no-busy-retry', 'result.returncode != 16', 'result.returncode != 0', [16, 0]),
        ('retries-other-errors', 'result.returncode != 16', 'False', [1, 0]),
    ]:
        assert before in script
        job=root/name; job.mkdir(); (job/'prepare.py').write_text(script.replace(before, after, 1))
        run([sys.executable, '-I', '-B', '-c', DETACH_DRIVER, job, json.dumps(codes), 'run'])
        ready=json.loads((job/'progress.json').read_text())['state']=='ready'
        assert ready == (name == 'retries-other-errors'), name
    print('Retained detach good=6 PASS (0/1/3/5/1/1 attempts, bounded waits); broken retry policy=2 rejected')


def cleanup_mount(mount, deadline=None):
    """Wait for detach before TemporaryDirectory can remove the mount directory."""
    deadline = time.monotonic() + 9 if deadline is None else deadline
    for force in (False, False, True):
        if not mount.is_mount(): return
        remaining = deadline - time.monotonic()
        if remaining <= 0: break
        try:
            result = subprocess.run(['/usr/bin/hdiutil', 'detach', *(['-force'] if force else []), str(mount)],
                                    capture_output=True, text=True, timeout=remaining if force else min(2, remaining))
            assert result.returncode in (0, 16) or 'busy' in result.stderr.lower(), result.stderr
            if result.returncode == 0: break
        except subprocess.TimeoutExpired:
            pass
        time.sleep(min(.1, max(0, deadline - time.monotonic())))
    while mount.is_mount() and time.monotonic() < deadline:
        time.sleep(min(.1, max(0, deadline - time.monotonic())))
    assert not mount.is_mount(), 'Scratch image still mounted after bounded cleanup'


def mounted_prepare():
    """Real mapped executable on a scratch volume; no released image required."""
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp).resolve(); image=root/'scratch.dmg'
        # Probe disk-image access before constructing the interpreter fixture.
        # Disable indexing at creation, before Spotlight can see an attached volume.
        result=subprocess.run(['/usr/bin/hdiutil', 'create', '-nospotlight', '-size', '32m', '-fs', 'HFS+',
                               '-volname', 'UpdateDetachTest', str(image)], capture_output=True, text=True)
        def refused(result):
            message=result.stderr.lower()
            return result.returncode and any(reason in message for reason in
                ('operation not permitted', 'permission denied', 'device not configured'))
        if refused(result):
            print('SKIP: scratch disk image refused: '+result.stderr.strip()); return 3
        assert result.returncode == 0, result.stderr
        outcomes=[]
        for broken in (False, True):
            job=root/('broken' if broken else 'good'); script=original_order(bridge_script()) if broken else bridge_script()
            setup=preparation_fixture(job, script)
            python=setup/'Contents/Resources/python/bin/python3'
            original=Path(sys.executable).resolve()
            # Framework bin/python is a launcher that spawns this actual main.
            framework_main=Path(sys.base_prefix)/'Resources/Python.app/Contents/MacOS/Python'
            if framework_main.is_file(): original=framework_main
            shutil.copy2(original, python)
            # Keep the framework library on the image too: a launcher linked to
            # the host library does not reproduce the live image dependency.
            libraries=run(['/usr/bin/otool', '-L', original]).stdout
            for library in sorted(set(line.strip().split(' (', 1)[0] for line in libraries.splitlines()
                                      if line.strip().startswith('@executable_path/'))):
                resolved=(original.parent/library.removeprefix('@executable_path/')).resolve()
                target=python.parent.parent/resolved.name
                shutil.copy2(resolved, target)
                run(['/usr/bin/install_name_tool', '-change', library,
                     '@executable_path/../'+resolved.name, python])
            run(['/usr/bin/codesign', '--force', '--sign', '-', python])
            mount=job/'mount'; mount.mkdir()
            result=subprocess.run(['/usr/bin/hdiutil', 'attach', '-nobrowse', '-noautoopen', '-mountpoint', str(mount), str(image)], capture_output=True, text=True)
            if refused(result):
                print('SKIP: scratch disk image attach refused: '+result.stderr.strip()); return 3
            assert result.returncode == 0, result.stderr
            try:
                mounted=mount/'Setup.app'
                if mounted.exists(): shutil.rmtree(mounted)
                shutil.copytree(setup, mounted)
                # The test archive contains only the regular fixture files above.
                prefix='import tarfile\ntarfile.TarFile.extractall='+extract_fixture.__name__+'\n'
                import inspect
                wrapper=inspect.getsource(extract_fixture)+prefix+script
                result=subprocess.run([str(mounted/'Contents/Resources/python/bin/python3'), '-I', '-B', '-c', wrapper,
                                       str(job), str(mounted), str(job/'profile'), str(job/'app')], cwd=job, capture_output=True, text=True, timeout=45)
                state=json.loads((job/'progress.json').read_text())
                assert (result.returncode == 0) == (not broken), (result.stderr[-3000:], state)
                assert (state['state']=='ready') == (not broken), state
                assert (job/'built').exists() == (not broken)
                assert mount.is_mount() == broken
                if broken: assert 'busy' in result.stderr.lower(), result.stderr
                outcomes.append(result.returncode)
            finally:
                deadline = time.monotonic() + 9
                held = None
                try:
                    if broken and mount.is_mount():
                        held = (mount/'cleanup-busy').open('w')
                        try:
                            try:
                                subprocess.run(['/usr/bin/hdiutil', 'detach', str(mount)],
                                               capture_output=True, text=True, timeout=1)
                            finally: shutil.rmtree(mount)
                        except OSError as error:
                            assert error.errno == 16, error
                        else: raise AssertionError('Original cleanup accepted a busy volume')
                finally:
                    try: cleanup_mount(mount, deadline)
                    finally:
                        if held: held.close()
                if held: print('Cleanup held-open file: original EBUSY=16; retry/force unmounted=1 PASS')
        print('Mounted interpreter good=1 detached before build; original-order broken=1 busy; rc='+str(outcomes))
    return 0


def bridge_cases(root):
    """Run the retained preparation bridge, with a stub offline build protocol."""
    script = bridge_script()
    compile(script, 'prepare.py', 'exec')
    for mode in ('good', 'failed', 'checksum'):
        job = root / mode; (job / 'source/tools/setup_kit').mkdir(parents=True)
        (job / 'python/bin').mkdir(parents=True)
        package = job / 'source/tools/setup_kit'
        (package / '__init__.py').touch()
        (package / 'backend.py').write_text('import json\nfrom pathlib import Path\ndef main(args):\n'
            '    assert "--stage-only" in args and "--build" not in args\n'
            '    print(json.dumps(dict(ev="stage", title="Building audio")))\n'
            '    stage=Path(args[args.index("--stage")+1]); stage.mkdir()\n'
            '    (stage/"ready.json").write_text("{}")\n'
            + ('    print(json.dumps(dict(ev="error", msg="SHA256 checksum mismatch", fix="Retry")))\n    return 1\n' if mode == 'checksum' else '    return '+('1' if mode == 'failed' else '0')+'\n'))
        (package / 'apply.py').write_text('def publish_ready(ready, profile, destination):\n    (profile/".update-ready.json").write_text("published")\n')
        profile = job / 'profile'; profile.mkdir()
        result = subprocess.run([sys.executable, '-I', '-B', '-c', script, str(job), '--retained', str(profile), str(job/'app')], capture_output=True, text=True)
        value = json.loads((job / 'progress.json').read_text())
        assert (result.returncode == 0) == (mode == 'good'), result.stderr
        assert (value['state'] == 'ready') == (mode == 'good'), value
        assert (profile / '.update-ready.json').exists() == (mode == 'good')
    # A helper failure must prevent executing any released Python, even when
    # it leaves a misleading verified receipt. The broken supervisor executes it.
    source = (ROOT/'pub_update.m').read_text()
    headers = PROOF['fixture_headers'](root)
    source = source.replace('"pub_update_manifest.h"', '"fixture-headers/pub_update_manifest.h"')
    before = 'if (ok && [receipt[@"state"] isEqual:@"verified"] && [receipt[@"setup_path"] isEqual:setup])'
    assert before in source
    for broken in (False, True):
        native = root/('broken.m' if broken else 'good.m')
        native.write_text(source.replace(before, 'if (YES)', 1).replace('ok = PUAdmit(job, delivery[1], delivery[2], delivery[3]);', 'ok = YES;', 1) if broken else source)
        preparer = root/('broken-preparer' if broken else 'preparer')
        run(['clang', '-fobjc-arc', '-Wno-unused-function', '-I', ROOT, '-DPUB_UPDATE_WORKER=1', native, '-framework', 'Cocoa', '-framework', 'Security', '-o', preparer])
        for reason in ('helper failed', 'SHA256 checksum mismatch') + (() if broken else ('verified',)):
            job = root/(('broken-' if broken else 'good-')+reason.replace(' ', '-')); job.mkdir(mode=0o700)
            setup=job/'mount/NFS Most Wanted Native Setup.app'
            python=setup/'Contents/Resources/python/bin/python3'; python.parent.mkdir(parents=True)
            python.write_text('#!/bin/sh\nprintf \'{"state":"ready","message":"false success"}\' > "$PWD/progress.json"\ntouch "$PWD/executed"\n')
            python.chmod(0o700)
            helper=job/'pub_update_helper'
            verified = reason == 'verified'
            asset = 'NFS-Most-Wanted-Native-Setup-v4.2.0.dmg'
            if verified: PROOF['retain_proof'](job, headers)
            helper.write_text('#!/bin/sh\nprintf %s '+shlex.quote(json.dumps(dict(state='verified', message=reason, setup_path=str(setup))))+f' > "$1/progress.json"\nexit {0 if verified else 1}\n')
            helper.chmod(0o700)
            result=subprocess.run([str(preparer), str(job), 'v4.2.0', asset, asset+'.sha256', '10', str(root/'good/profile'), str(root/'app')], capture_output=True, text=True)
            value=json.loads((job/'progress.json').read_text())
            assert (value['state']=='ready') == (broken or verified), (result.stderr, value)
            assert (job/'executed').exists() == (broken or verified)
            binding = job/'target-version.json'
            if verified:
                assert json.loads(binding.read_text()) == {'version': '4.2.0'}
            elif not broken:
                assert not binding.exists()
    print('Native failed-helper/checksum controls good=2 rejected broken=2 false successes detected')


def signed_worker_cases(root):
    root.mkdir()
    headers = PROOF['fixture_headers'](root)
    source = (ROOT/'pub_update.m').read_text().replace('"pub_update_manifest.h"', '"fixture-headers/pub_update_manifest.h"')
    gate = 'ok = PUAdmit(job, delivery[1], delivery[2], delivery[3]);'
    assert source.count(gate) == 1
    for product in ('mw', 'carbon'):
        setup_name = 'NFS Carbon Native Setup.app' if product == 'carbon' else 'NFS Most Wanted Native Setup.app'
        for broken in (True, False):
            path=root/'preparer.m'; path.write_text(source.replace(gate, 'ok = YES;', 1) if broken else source)
            exe=root/'preparer'
            run(['clang', '-fobjc-arc', '-Wno-deprecated-declarations', '-Wno-unused-function', '-I', ROOT,
                 *(['-DNFS_GAME=2'] if product == 'carbon' else []), '-DPUB_UPDATE_WORKER=1', path,
                 '-framework', 'Cocoa', '-framework', 'Security', '-o', exe])
            for case in ('unsigned', 'partial', 'dmg', 'checksum', 'signature', 'binding', 'good'):
                job=root/f'{product}-{broken}-{case}'; job.mkdir(mode=0o700)
                asset=PROOF['retain_proof'](job, headers, product=product)
                proof=job/(asset+'.manifest'); sig=job/(asset+'.manifest.sig')
                if case == 'unsigned': proof.unlink(); sig.unlink()
                if case == 'partial': sig.unlink()
                if case == 'dmg': (job/asset).write_bytes(b'changed image')
                if case == 'checksum': (job/(asset+'.sha256')).write_bytes(b'changed checksum')
                if case == 'signature': sig.write_bytes(b'invalid')
                if case == 'binding': proof.write_bytes(proof.read_bytes().replace(b'tag=v4.2.0', b'tag=v4.3.0'))
                setup=job/'mount'/setup_name
                python=setup/'Contents/Resources/python/bin/python3'; python.parent.mkdir(parents=True)
                python.write_text('#!/bin/sh\ntouch "$PWD/executed"\nprintf \'{"state":"ready"}\' > "$PWD/progress.json"\n')
                python.chmod(0o700)
                helper=job/'pub_update_helper'
                receipt=json.dumps(dict(state='verified', setup_path=str(setup)))
                helper.write_text('#!/bin/sh\nprintf %s '+shlex.quote(receipt)+' > "$1/progress.json"\n')
                helper.chmod(0o700)
                result=subprocess.run([exe, job, 'v4.2.0', asset, asset+'.sha256', '1', root/'profile', root/'App.app'],
                                      capture_output=True, text=True, timeout=20)
                accepted=broken or case=='good'
                assert (job/'executed').exists() == accepted, (product, broken, case, result.stderr)
                assert (result.returncode == 0) == accepted, (product, broken, case, result.stderr)
    print('Preparation signed admission: bypass EXECUTE=12 / intact REFUSE=12; signed EXECUTE=2 (both products)')


def signed_launch_cases(root):
    root.mkdir()
    headers=PROOF['fixture_headers'](root)
    digest_tree=runpy.run_path(str(ROOT.parent/'tools/setup_kit/install.py'))['digest_tree']
    source=(ROOT/'pub_update_launch.m').read_text().replace('"pub_update_manifest.h"', '"fixture-headers/pub_update_manifest.h"')
    source=source.replace('if (![NSBundle.mainBundle.bundleIdentifier isEqual:PublicBundleID]) return;', '')
    source=source.replace('CanonicalPath(NSBundle.mainBundle.bundlePath)', 'CanonicalPath([NSString stringWithUTF8String:getenv("FIXTURE_DEST")])')
    source=source.replace('NSBundle.mainBundle.infoDictionary[@"CFBundleShortVersionString"]', '@"4.0.0"')
    a=source.index('static void UpdateError('); b=source.index('static void InterruptedInstall(', a)
    source=source[:a]+'static void UpdateError(NSString *reason) { fprintf(stderr, "%s\\n", reason.UTF8String); exit(99); }\n'+source[b:]
    driver='''
const char *game_dir;
int main(int argc, char **argv) { @autoreleasepool {
    const char *profile=argv[1]; argc=1;
    pub_update_launch(&argc,&argv,profile); return 0;
} }
'''
    env={k:v for k,v in os.environ.items() if k not in ('BOOT_BACKGROUND','BOOT_DEADLINE')}
    healthy_cases = ('healthy-lower', 'healthy-equal', 'healthy-padded', 'healthy-higher',
                     'healthy-json', 'healthy-shape', 'healthy-product', 'healthy-version',
                     'healthy-large', 'healthy-link')
    healthy_refused = set(healthy_cases) - {'healthy-lower'}
    floor_bypass = floor_refused = floor_allowed = 0
    for product in ('mw','carbon'):
        for broken in ('healthy', 'signature', None):
            gate='        !SignedJob(job, target) ||\n'
            assert source.count(gate)==1
            floor=' || !HealthyVersionAllows(target, profile)'
            assert source.count(floor)==1
            changed = source.replace(floor, '', 1) if broken == 'healthy' else source.replace(gate, '', 1) if broken == 'signature' else source
            path=root/'launch.m'; path.write_text(changed+driver)
            exe=root/'launch'
            run(['clang','-fobjc-arc','-Wno-deprecated-declarations','-I',ROOT,
                 *(['-DNFS_GAME=2'] if product=='carbon' else []),path,
                 '-framework','Cocoa','-framework','Security','-o',exe])
            for case in ('unsigned','partial','dmg','checksum','signature','binding','good','legacy-recovery', *healthy_cases):
                work=root/f'{product}-{broken}-{case}'; work.mkdir()
                profile=work/'profile'; profile.mkdir()
                destination=work/'Game.app'; destination.mkdir()
                job=work/'job'; job.mkdir(mode=0o700)
                (job/'python/bin').mkdir(parents=True); (job/'source/tools/setup_kit').mkdir(parents=True)
                python=job/'python/bin/python3'
                python.write_text('#!/bin/sh\ntouch "$PWD/executed"\n'); python.chmod(0o700)
                (job/'source/tools/setup_kit/apply.py').write_text('# retained fixture\n')
                tools={name:digest_tree(job/name) for name in ('source','python')}
                (job/'target-version.json').write_text('{"version":"4.2.0"}')
                staged=job/'staged'; (staged/'Game.app/Contents').mkdir(parents=True)
                import plistlib
                (staged/'Game.app/Contents/Info.plist').write_bytes(plistlib.dumps(dict(CFBundleShortVersionString='4.2.0')))
                ready=staged/'ready.json'; ready.write_text('{"app":"Game.app","version":"4.2.0"}')
                asset=PROOF['retain_proof'](job, headers, product=product)
                proof=job/(asset+'.manifest'); sig=job/(asset+'.manifest.sig')
                if case in ('unsigned','legacy-recovery'): proof.unlink(); sig.unlink()
                if case=='partial': sig.unlink()
                if case=='dmg': (job/asset).write_bytes(b'changed image')
                if case=='checksum': (job/(asset+'.sha256')).write_bytes(b'changed checksum')
                if case=='signature': sig.write_bytes(b'invalid')
                if case=='binding':
                    raw=proof.read_bytes().replace(b'version=4.2.0',b'version=4.3.0')
                    proof.write_bytes(raw); sig.write_bytes(PROOF['sign_bytes'](raw, headers/'key.pem'))
                if case in healthy_cases:
                    healthy = profile / ('.update-healthy-' + product + '.json')
                    version = {'healthy-lower':'4.1.0', 'healthy-padded':'4.2.0.0', 'healthy-higher':'4.3.0'}.get(case, '4.2.0')
                    record = dict(format_version=1, product=product, version=version)
                    if case == 'healthy-product': record['product'] = 'carbon' if product == 'mw' else 'mw'
                    if case == 'healthy-version': record['version'] = '04.2.0'
                    if case == 'healthy-shape': record['extra'] = True
                    healthy.write_text('broken json' if case == 'healthy-json' else json.dumps(record))
                    if case == 'healthy-large': healthy.write_text(healthy.read_text() + ' ' * 1024)
                    if case == 'healthy-link':
                        other = work / 'healthy.json'; healthy.rename(other); healthy.symlink_to(other)
                index=dict(format_version=1,job=str(job),ready=str(ready),profile=str(profile),destination=str(destination),tools=tools)
                (profile/'.update-ready.json').write_text(json.dumps(index)); (profile/'.update-ready.json').chmod(0o600)
                if case=='legacy-recovery':
                    pending=dict(kind='launch-update-v1',state='moving',job=str(job),tools=tools)
                    (profile/'.install-journal.json').write_text(json.dumps(pending))
                result=subprocess.run([exe,profile],env=dict(env,FIXTURE_DEST=str(destination)),capture_output=True,text=True,timeout=20)
                assert result.returncode==0,(product,broken,case,result.stderr)
                accepted = case == 'legacy-recovery' or (
                    (broken == 'signature' or case == 'good' or case in healthy_cases) and
                    (broken == 'healthy' or case not in healthy_refused))
                marker=job/'executed'
                if accepted:
                    deadline=time.monotonic()+5
                    while not marker.exists() and time.monotonic()<deadline: time.sleep(.01)
                assert marker.exists()==accepted,(product,broken,case,result.stderr)
                if not accepted:
                    assert (staged/'.apply-failed.json').is_file(), (case,result.stderr)
                if broken == 'healthy' and case in healthy_refused: floor_bypass += marker.exists()
                if broken is None and case in healthy_refused: floor_refused += not marker.exists()
                if broken is None and case in ('good', 'healthy-lower'): floor_allowed += marker.exists()
    print('Launch signed admission: bypass EXECUTE=12 / intact REFUSE=12; signed EXECUTE=2; legacy recovery EXECUTE=2 (both products)')
    assert (floor_bypass, floor_refused, floor_allowed) == (18, 18, 4)
    print('Launch healthy floor: removed check EXECUTE=18/18; intact REFUSE=18/18; missing/lower EXECUTE=4/4 (both products)')


def ready_while_playing(root):
    helpers = runpy.run_path(str(ROOT.parent/'tests/setup/test_apply.py'))
    apply = helpers['a']; install = helpers['ins']
    root.mkdir()
    fixture = helpers['Fixture'](root)
    fixture.job.chmod(0o700)
    helpers['put'](fixture.job/'source/tools/setup_kit/apply.py', '# retained source')
    helpers['put'](fixture.job/'python/bin/python3', 'retained interpreter')
    text = (ROOT.parent/'tools/setup_kit/apply.py').read_text()
    function = text.split('def publish_ready(', 1)[1].split('\ndef _indexed_tools', 1)[0]
    broken = 'def publish_ready(' + function.replace('with ins.defer_interrupts():', 'with ins.profile_guard(profile):', 1)
    namespace = dict(apply.__dict__)
    exec(broken, namespace)
    with mock.patch.object(install, 'codesign_ok', return_value=True), install.profile_guard(fixture.profile):
        apply.publish_ready(fixture.ready, fixture.profile, fixture.dest)
        fixture.consistent('old')
        try:
            namespace['publish_ready'](fixture.ready, fixture.profile, fixture.dest)
        except install.InstallError as error:
            assert 'using this profile' in str(error), error
        else:
            raise AssertionError('profile-lock publication mutant passed')
    print('Publish while playing good=1 PASS broken=1 rejected; active app/data/preferences/saves unchanged')


def cancelled_launch(root):
    root.mkdir()
    job=root/'job'; job.mkdir()
    (job/'cancel').write_text('{}')
    index=dict(format_version=1, job=str(job), ready=str(job/'staged/ready.json'), profile=str(root), destination=str(root))
    (root/'.update-ready.json').write_text(json.dumps(index)); (root/'.update-ready.json').chmod(0o600)
    source=(ROOT/'pub_update_launch.m').read_text()
    a=source.index('static void UpdateError('); b=source.index('static void InterruptedInstall(', a)
    source=source[:a]+'static void UpdateError(NSString *reason) { fprintf(stderr, "UpdateError: %s\\n", reason.UTF8String); exit(99); }\n'+source[b:]
    source=source.replace('if (![NSBundle.mainBundle.bundleIdentifier isEqual:PublicBundleID]) return;', '')
    source=source.replace('CanonicalPath(NSBundle.mainBundle.bundlePath)', 'profile')
    main='const char *game_dir; int main(int argc, char **argv) { @autoreleasepool { const char *profile=argv[1]; argc=1; pub_update_launch(&argc,&argv,profile); return 0; } }'
    guard=' ||\n                [files fileExistsAtPath:[job stringByAppendingPathComponent:@"cancel"]]'
    assert guard in source
    # Exercise a normal launch even when the offline runner is unattended.
    # This executable contains only the launch hook and never starts the game.
    env = {key: value for key, value in os.environ.items() if key not in ('BOOT_BACKGROUND', 'BOOT_DEADLINE')}
    for broken in (False, True):
        path=root/'launch.m'; path.write_text((source.replace(guard, '', 1) if broken else source)+main)
        exe=root/'launch'
        run(['clang', '-fobjc-arc', '-Wno-deprecated-declarations', '-I', ROOT, path, '-framework', 'Cocoa', '-framework', 'Security', '-o', exe])
        result=subprocess.run([str(exe),str(root)], capture_output=True, env=env)
        assert result.returncode == (99 if broken else 0), (broken, result.returncode, result.stdout, result.stderr)
        assert (b'retained tools failed verification' in result.stderr) == broken, result.stderr
    print('Cancelled launch good=1 PASS (rc=0) broken=1 rejected (rc=99, retained-tools check); cancelled job ignored')


def interrupted_setup_launch(root):
    root.mkdir()
    (root / '.install-journal.json').write_text('{"state":"preparing"}')
    (root / '.install-journal.json').chmod(0o600)
    source = (ROOT / 'pub_update_launch.m').read_text()
    source = source.replace('if (![NSBundle.mainBundle.bundleIdentifier isEqual:PublicBundleID]) return;', '')
    source = source.replace('[NSApplication sharedApplication];', '')
    source = source.replace('NSAlert *alert = [NSAlert new];', 'FixtureAlert *alert = [FixtureAlert new];')
    source = source.replace('static void UpdateError(', '''
@interface FixtureAlert : NSObject
@property(copy) NSString *messageText, *informativeText;
- (void)addButtonWithTitle:(NSString *)title;
- (NSModalResponse)runModal;
@end
@implementation FixtureAlert
- (void)addButtonWithTitle:(NSString *)title { fprintf(stderr, "%s\\n", title.UTF8String); }
- (NSModalResponse)runModal { return NSAlertFirstButtonReturn; }
@end
static void UpdateError(''')
    start = source.index('static NSURL *RecoverySetupURL(')
    end = source.index('@interface FixtureAlert', start)
    source = source[:start] + '''
static NSURL *RecoverySetupURL(void) { return [NSURL fileURLWithPath:@"/fixture/Setup.app"]; }
''' + source[end:]
    source = source.replace('@"/usr/bin/open"', '@"/bin/echo"')
    source += '''
const char *game_dir;
int main(int argc, char **argv) {
    @autoreleasepool {
        const char *profile = argv[1];
        argc = 1;
        pub_update_launch(&argc, &argv, profile);
        return 0;
    }
}
'''
    button = '    [alert addButtonWithTitle:@"Open Setup"];\n'
    assert button in source
    broken = source.replace('            InterruptedInstall();',
                            '            UpdateError(@"Run Setup again to recover the interrupted installation.");')
    assert broken != source
    env = {key: value for key, value in os.environ.items() if key not in ('BOOT_BACKGROUND', 'BOOT_DEADLINE')}
    for fixture, expected in ((broken, False), (source, True)):
        path = root / 'launch.m'
        path.write_text(fixture)
        exe = root / 'launch'
        run(['clang', '-fobjc-arc', '-Wno-deprecated-declarations', '-I', ROOT, path, '-framework', 'Cocoa', '-framework', 'Security', '-o', exe])
        result = subprocess.run([exe, root], capture_output=True, text=True, env=env, timeout=20)
        assert result.returncode == 1, result
        offered_recovery = ('Open Setup\nQuit\n' in result.stderr
                            and '-a /fixture/Setup.app --args --recover' in result.stdout)
        assert offered_recovery == expected, result
    print('Interrupted Setup launch: original 0/1, fixed 1/1 (Open Setup, Quit, --recover)')


def production_transport(root):
    root.mkdir()
    path = root / 'client.m'
    path.write_text((ROOT / 'pub_update.m').read_text() + '''
void settings_add(Setting *setting) { (void)setting; }
const char *userdata_dir(void) { return "/unused"; }
int main(void) { return 0; }
''')
    for product, repository, asset in (('mw', 'elforeign/nfs-most-wanted-mac', 'NFS-Most-Wanted-Native-Setup'),
                                       ('carbon', 'elforeign/nfs-carbon-mac', 'NFS-Carbon-Native-Setup')):
        release_ok = []
        for enabled in (False, True):
            exe = root / (product + ('-fixture-enabled' if enabled else '-production'))
            flags = ['-DPUB_UPDATE_REHEARSAL=1',
                     '-DPUB_UPDATE_REHEARSAL_FEED_URL="http://127.0.0.1:1/rehearsal"'] if enabled else []
            if product == 'carbon':
                flags += ['-DNFS_GAME=2', '-DNFS_PUBLIC=1']  # the public build (a private one registers no rows)
            run(['clang', '-fobjc-arc', '-Wno-unused-function', '-I', ROOT, *flags,
                 path, '-framework', 'Cocoa', '-framework', 'Security', '-o', exe])
            data = run(['/usr/bin/strings', exe]).stdout
            release_ok.append('https://api.github.com/repos/' in data and
                              'PUB_UPDATE_REHEARSAL_BASE_URL' not in data)
            assert repository in data and asset in data, product
        assert release_ok == [True, False], (product, release_ok)
    print('Production transports good=2 fixed product feeds/assets; broken=2 fixture-enabled builds detected')
    # Each game's own update folder and rebuild: Carbon's jobs never share Most Wanted's folder (one app adopting or
    # overwriting the other's update), and its preparer rebuilds Carbon. Main 583933e9: Carbon shared the folder and
    # staged Most Wanted (both checks fail); fixed: pass.
    for product, flags, folder, other, game in (
            ('mw', [], 'NFSMW-Setup/updates', 'NFSMW-Setup/Carbon/updates', False),
            ('carbon', ['-DNFS_GAME=2'], 'NFSMW-Setup/Carbon/updates', None, True)):
        client = run(['/usr/bin/strings', root / (product + '-production')]).stdout
        assert folder in client and (other is None or other not in client), (product, folder)
        preparer = root / (product + '-preparer')
        run(['clang', '-fobjc-arc', '-Wno-unused-function', '-I', ROOT, *flags, '-DPUB_UPDATE_WORKER=1',
             ROOT / 'pub_update.m', '-framework', 'Cocoa', '-framework', 'Security', '-o', preparer])
        assert ("'--game', 'carbon'" in run(['/usr/bin/strings', preparer]).stdout) == game, product
    print('Update folders and rebuild product good=2 (Most Wanted, Carbon)')


def feed_transport(root):
    """Real URLSession, with only the production endpoint replaced by loopback."""
    root.mkdir()
    source = (ROOT / 'pub_update.m').read_text()
    endpoint = '    NSMutableURLRequest *request = [NSMutableURLRequest requestWithURL:url'
    assert source.count(endpoint) == 1
    source = source.replace(endpoint, '    url = [NSURL URLWithString:NSProcessInfo.processInfo.environment[@"FIXTURE_FEED"]];\n'
                            + endpoint, 1)
    path = root / 'feed.m'
    path.write_text(source + r'''
void settings_add(Setting *setting) { (void)setting; }
const char *userdata_dir(void) { return "/unused"; }
int main(void) { @autoreleasepool {
    snprintf(pu_current, sizeof pu_current, "4.0.0");
    pu_state = PU_CHECKING;
    mac_fetch();
    NSDate *deadline = [NSDate dateWithTimeIntervalSinceNow:30];
    while (pu_state == PU_CHECKING && [deadline timeIntervalSinceNow] > 0)
        [NSRunLoop.currentRunLoop runUntilDate:[NSDate dateWithTimeIntervalSinceNow:0.01]];
    printf("%s\n", pu_state == PU_NEWER ? "newer" : pu_state == PU_FAILED ? "failed" : "unexpected");
    return 0;
} }
''')
    executable = root / 'feed'
    run(['clang', '-fobjc-arc', '-I', ROOT, path, '-framework', 'Cocoa', '-framework', 'Security', '-o', executable])

    cap = 1024 * 1024
    release = b'{"tag_name":"v4.2.0"}'
    requests = []

    class Feed(http.server.BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *args):
            pass

        def do_GET(self):
            requests.append(self.path)
            if self.path == '/redirect':
                self.send_response(302)
                self.send_header('Location', '/redirect-target')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            self.send_response(200)
            try:
                if self.path in ('/at-cap', '/over-cap'):
                    self.send_header('Transfer-Encoding', 'chunked')
                    self.end_headers()
                    body = release.ljust(cap + (self.path == '/over-cap'))
                    for i in range(0, len(body), 16384):
                        chunk = body[i:i + 16384]
                        self.wfile.write(f'{len(chunk):x}\r\n'.encode() + chunk + b'\r\n')
                    self.wfile.write(b'0\r\n\r\n')
                else:
                    self.send_header('Content-Length', str(len(release)))
                    self.end_headers()
                    self.wfile.write(release)
            except (BrokenPipeError, ConnectionResetError):
                pass  # The bounded client cancels the oversized stream.

    with http.server.ThreadingHTTPServer(('127.0.0.1', 0), Feed) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            outcomes = {}
            for route in ('/normal', '/redirect', '/at-cap', '/over-cap'):
                outcome = run([executable], env=dict(os.environ,
                    FIXTURE_FEED=f'http://127.0.0.1:{server.server_port}{route}'))
                outcomes[route] = outcome.stdout.strip()
        finally:
            server.shutdown()
            thread.join()
    expected = {'/normal': 'newer', '/redirect': 'failed', '/at-cap': 'newer', '/over-cap': 'failed'}
    assert outcomes == expected and '/redirect-target' not in requests, (outcomes, requests)
    print('Release feed accepts valid and exact-cap responses, rejects redirect without following and streamed cap+1')


def carbon_selection():
    """Carbon's runtime links the update rows and the next-launch apply instead of its empty stub.
    Main 583933e9: neither unit selected, the stub kept (fails); fixed: passes."""
    sys.path.insert(0, str(ROOT.parent / 'recomp/private_runtime'))
    import layers
    manifest = layers.load_manifest()
    units = manifest['targets']['private']['units']
    assert 'pub_update.m' in units and 'pub_update_launch.m' in units, units
    enabled = manifest['_private_registry']['enabled_providers']
    assert 'pub_update_launch' in enabled, sorted(enabled)
    assert b'pub_update_launch' not in layers._disabled_bytes(manifest)
    print('Carbon selection good=1: update rows and next-launch apply linked, stub removed')


def main():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        good = root/'good'; good.mkdir()
        result = checks(native_flow(good))
        assert all(result.values()), result
        print('UI flow good=12 PASS')
        for i, (name, patch) in enumerate(MUTANTS):
            directory = root/str(i); directory.mkdir()
            assert not checks(native_flow(directory, patch))[name], ('broken control passed', name)
            print(name, 'broken FAIL')
        bridge = root/"bridge"; bridge.mkdir()
        bridge_cases(bridge)
        signed_worker_cases(root/"signed-preparation")
        signed_launch_cases(root/"signed-launch")
        ready_while_playing(root/"locked-profile")
        cancelled_launch(root/"cancelled-launch")
        interrupted_setup_launch(root/"interrupted-setup")
        production_transport(root/'production-transport')
        feed_transport(root/'feed-transport')
        carbon_selection()
        detach_order_cases(root)
        retry=root/'detach-retry'; retry.mkdir(); detach_retry_cases(retry)
        mounted=subprocess.run([sys.executable, '-I', '-B', __file__, '--mounted-prepare'], capture_output=True, text=True, timeout=120)
        assert mounted.returncode in (0, 3), (mounted.returncode, mounted.stdout[-1000:], mounted.stderr[-3000:])
        print(mounted.stdout.strip())
        print('Mounted interpreter case rc='+str(mounted.returncode))
        print('Preparation/launch good=15 PASS; broken=17 detected (9 UI, 8 preparation/launch)')
    print('PUB UPDATE FLOW TEST PASS')


if __name__ == '__main__':
    if sys.argv[1:] == ['--mounted-prepare']:
        raise SystemExit(mounted_prepare())
    main()
