"""Player update flow, driven by the real local progress reader and F10 actions.
Proof: 19 good checks (12 UI, 7 preparation/launch); 13 broken controls rejected
(9 UI, 4 preparation/launch), including automatic fetch and false-success controls.
Launch fixture: inherited BOOT_BACKGROUND=1 skipped the normal-launch guard
(broken rc=0); clear launch-mode flags in this stub child (good rc=0, broken rc=99).
No network, game, or desktop app is started. The normal offline discovery runs this file.
Transport compilation: production UI good=1 has the fixed release URL and no
fixture environment; the fixture-enabled build fails that same release oracle (1/1).
"""
import json
import os
from pathlib import Path
import runpy
import shlex
import subprocess
import sys
import tempfile
from unittest import mock

ROOT = Path(__file__).resolve().parent


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
         path, '-framework', 'Cocoa', '-o', root / 'flow'])
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
    ('opt_in', ('source', 'settings_add(&s_update);', 'mac_fetch(); settings_add(&s_update);')),
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
    return ''.join(json.loads(line[1:]) for line in text.splitlines())


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
    before = 'if (ok && [receipt[@"state"] isEqual:@"verified"] && [receipt[@"setup_path"] isEqual:setup])'
    assert before in source
    for broken in (False, True):
        native = root/('broken.m' if broken else 'good.m')
        native.write_text(source.replace(before, 'if (YES)', 1) if broken else source)
        preparer = root/('broken-preparer' if broken else 'preparer')
        run(['clang', '-fobjc-arc', '-Wno-unused-function', '-I', ROOT, '-DPUB_UPDATE_WORKER=1', native, '-framework', 'Cocoa', '-o', preparer])
        for reason in ('helper failed', 'SHA256 checksum mismatch'):
            job = root/(('broken-' if broken else 'good-')+reason.replace(' ', '-')); job.mkdir(mode=0o700)
            setup=job/'mount/NFS Most Wanted Native Setup.app'
            python=setup/'Contents/Resources/python/bin/python3'; python.parent.mkdir(parents=True)
            python.write_text('#!/bin/sh\nprintf \'{"state":"ready","message":"false success"}\' > "$PWD/progress.json"\ntouch "$PWD/executed"\n')
            python.chmod(0o700)
            helper=job/'pub_update_helper'
            helper.write_text('#!/bin/sh\nprintf %s '+shlex.quote(json.dumps(dict(state='verified', message=reason, setup_path=str(setup))))+' > "$1/progress.json"\nexit 1\n')
            helper.chmod(0o700)
            result=subprocess.run([str(preparer), str(job), 'v4.2.0', 'fixture.dmg', 'fixture.sha256', '10', str(root/'good/profile'), str(root/'app')], capture_output=True, text=True)
            value=json.loads((job/'progress.json').read_text())
            assert (value['state']=='ready') == broken, (result.stderr, value)
            assert (job/'executed').exists() == broken
    print('Native failed-helper/checksum controls good=2 rejected broken=2 false successes detected')


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
    a=source.index('static void UpdateError('); b=source.index('// Called exactly once', a)
    source=source[:a]+'static void UpdateError(NSString *reason) { fprintf(stderr, "UpdateError: %s\\n", reason.UTF8String); exit(99); }\n'+source[b:]
    source=source.replace('if (![NSBundle.mainBundle.bundleIdentifier isEqual:@"io.github.nfsmw-native-mac"]) return;', '')
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
        run(['clang', '-fobjc-arc', '-Wno-deprecated-declarations', path, '-framework', 'Cocoa', '-o', exe])
        result=subprocess.run([str(exe),str(root)], capture_output=True, env=env)
        assert result.returncode == (99 if broken else 0), (broken, result.returncode, result.stdout, result.stderr)
        assert (b'retained tools failed verification' in result.stderr) == broken, result.stderr
    print('Cancelled launch good=1 PASS (rc=0) broken=1 rejected (rc=99, retained-tools check); cancelled job ignored')


def production_transport(root):
    root.mkdir()
    path = root / 'client.m'
    path.write_text((ROOT / 'pub_update.m').read_text() + '''
void settings_add(Setting *setting) { (void)setting; }
const char *userdata_dir(void) { return "/unused"; }
int main(void) { return 0; }
''')
    release_ok = []
    for enabled in (False, True):
        exe = root / ('fixture-enabled' if enabled else 'production')
        flags = ['-DPUB_UPDATE_REHEARSAL=1',
                 '-DPUB_UPDATE_REHEARSAL_FEED_URL="http://127.0.0.1:1/rehearsal"'] if enabled else []
        run(['clang', '-fobjc-arc', '-Wno-unused-function', '-I', ROOT, *flags,
             path, '-framework', 'Cocoa', '-o', exe])
        data = run(['/usr/bin/strings', exe]).stdout
        release_ok.append('https://api.github.com/repos/' in data and 'PUB_UPDATE_REHEARSAL_BASE_URL' not in data)
    assert release_ok == [True, False], release_ok
    print('Production transport good=1 fixed feed/no fixture config; broken=1 fixture-enabled build detected')


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
        ready_while_playing(root/"locked-profile")
        cancelled_launch(root/"cancelled-launch")
        production_transport(root/'production-transport')
        print('Preparation/launch good=7 PASS; broken=13 detected')
    print('PUB UPDATE FLOW TEST PASS')


if __name__ == '__main__':
    main()
