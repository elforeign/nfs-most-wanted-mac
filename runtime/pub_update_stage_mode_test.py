"""The native launch walk accepts the app and data copies from Setup.

The fixture includes a 0777 PC file and a 0775 PC directory, plus writable
app and data roots. The prepare job also holds the work copies the app and
data sources come from, so the walk covers the whole job. The pinned original
has 8 insecure entries (rc=1) and the pinned first fix, which cleared only the
staged copies, still has 4 (rc=1). The fixed publish has 0 (rc=0), with
identical app and data hashes.
offline_tests.sh discovers the test.
"""
import hashlib
import inspect
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / 'tools'))
from setup_kit import stage


ORIGINAL_COPY = '''        shutil.copytree(app, target / app.name)
        shutil.copytree(data, target / "data")
'''
ORIGINAL_COPY_SHA256 = 'c4f9b0964f5b7064fc194d0cbe29c92332e928e6f861b36204366b26ea4c70a5'
# The first fix: bits cleared on the staged copies only.
TARGET_ONLY_COPY = ORIGINAL_COPY + '''        # Launch checks every entry in the job for group/other write access.
        for root in (target / app.name, target / "data"):
            for path in chain((root,), root.rglob("*")):
                mode = stat.S_IMODE(path.stat().st_mode)
                if mode & 0o022:
                    path.chmod(mode & ~0o022)
'''
TARGET_ONLY_COPY_SHA256 = '39c5f064525cab63f0dacf406df4f370e6613967a8e9c8560f1e86de92ad1594'

DRIVER = r'''
const char *game_dir = NULL;
int main(int argc, char **argv) { @autoreleasepool {
    if (argc != 2) return 2;
    NSString *job = [NSString stringWithUTF8String:argv[1]];
    __block BOOL complete = YES;
    NSDirectoryEnumerator *walk = [NSFileManager.defaultManager enumeratorAtURL:[NSURL fileURLWithPath:job]
        includingPropertiesForKeys:nil options:0 errorHandler:^BOOL(NSURL *url, NSError *error) {
            (void)url; (void)error; complete = NO; return NO;
        }];
    if (!walk) return 2;
    unsigned insecure = !SecurePath(job, YES);
    for (NSURL *url in walk) if (!SecurePath(url.path, NO)) insecure++;
    if (!complete) return 2;
    printf("%u\n", insecure);
    return insecure ? 1 : 0;
} }
'''


def pinned_publish(fragment, digest):
    assert hashlib.sha256(fragment.encode()).hexdigest() == digest
    function = inspect.getsource(stage.publish)
    start = function.index('        # Launch checks every entry')
    end = function.index('        hashes = dict(app=', start)
    namespace = vars(stage).copy()
    exec(function[:start] + fragment + function[end:], namespace)
    return namespace['publish']


def test_prepared_modes_pass_launch_walk():
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory).resolve()
        driver = work / 'stage_mode.m'
        driver.write_text((ROOT / 'pub_update_launch.m').read_text() + DRIVER)
        executable = driver.with_suffix('')
        compiled = subprocess.run(['clang', '-fobjc-arc', '-Wno-deprecated-declarations',
                                   '-Wno-incompatible-pointer-types', '-I', str(ROOT),
                                   str(driver), '-framework', 'Cocoa', '-o', str(executable)],
                                  capture_output=True, text=True, timeout=90)
        assert compiled.returncode == 0, compiled.stderr[-4000:]

        variants = (('original', pinned_publish(ORIGINAL_COPY, ORIGINAL_COPY_SHA256)),
                    ('target_only', pinned_publish(TARGET_ONLY_COPY, TARGET_ONLY_COPY_SHA256)),
                    ('current', stage.publish))
        results = {}
        for tag, publish in variants:
            job = work / tag
            # Like a stage-only prepare: the job keeps the work copies beside the staged one.
            source = job / '.staged-work' / 'stage'
            app = source / stage.APP_BUNDLE
            data = source / 'data'
            pc = data / 'pc'
            job.mkdir(mode=0o700)
            app.mkdir(parents=True)
            pc.mkdir(parents=True)
            binary = app / 'fixture'
            binary.write_bytes(b'app\n')
            game_file = pc / 'speed.exe'
            game_file.write_bytes(b'game data\n')
            modes = {app: 0o775, binary: 0o751, data: 0o775, pc: 0o775, game_file: 0o777}
            for path, mode in modes.items():
                path.chmod(mode)
            hashes = {name: stage.digest_tree(path) for name, path in (('app', app), ('data', data))}
            staged = job / 'staged'
            ready = publish(staged, app, data, {'pc_root': pc}, work / 'installed' / app.name,
                            work / 'profile', '0.0.0', {})
            recorded = json.loads(ready.read_text())['hashes']
            assert {name: recorded[name] for name in hashes} == hashes, recorded
            result = subprocess.run([str(executable), str(job)], capture_output=True,
                                    text=True, timeout=30)
            results[tag] = (result.returncode, result.stdout.strip())
            print(f'{tag}: insecure={result.stdout.strip()} rc={result.returncode} '
                  f'app={recorded["app"]} data={recorded["data"]}', flush=True)
            if tag == 'current':
                for path, mode in modes.items():
                    assert stat.S_IMODE(path.stat().st_mode) == mode & ~0o022, path
                    copied = staged / path.relative_to(source)
                    assert stat.S_IMODE(copied.stat().st_mode) == mode & ~0o022, copied

        assert results['original'][0] == 1 and results['target_only'][0] == 1, results
        assert results['current'] == (0, '0'), results


if __name__ == '__main__':
    test_prepared_modes_pass_launch_walk()
    print('PUB UPDATE STAGE MODE TEST PASS')
