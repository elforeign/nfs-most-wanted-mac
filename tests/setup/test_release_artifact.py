#!/usr/bin/env python3
"""Build the shipped Setup source with its shipped Python, offline and without launching it.

Usage: python -I tests/setup/test_release_artifact.py --expected-version VERSION [--setup Setup.app] [--pc GAME] [--proof]
No Setup artifact or PC input: SKIP/exit 3 (also in offline_tests.sh). Release
preparation requires a pass, so a skip cannot authorize uploading an untested app.
The optional --proof repacks three broken source payloads and runs this same audit.
Proof numbers: good exit 0; missing server_functions.tsv exit 1; restored Metal
compiler requirement exit 1; leaked Testing.md exit 1 (3/3 broken runs rejected).

Only real tools/builds/signatures are used. The driver selects Command Line Tools
in the backend's child environment: production deliberately filters inherited
DEVELOPER_DIR. It does not replace preflight, compilation, bundling or installation.
The install uses an empty process snapshot: its destination/profile are entirely
scratch, and the offline sandbox cannot enumerate live processes. The production
running-game matcher and install transaction still run. The GUI picker is separate.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[2]
APP_NAME = 'NFS Most Wanted Native Setup.app'
CLT = Path('/Library/Developer/CommandLineTools')
TABLES = ('recomp/functions_v13.tsv', 'recomp/server_functions.tsv', 'recomp/hooks.txt')
GENERATED = ('attrib_x360', 'x360audio_attrib', 'attrib_light_data', 'trees_params',
             'controltype_buttons', 'auxlights_data', 'glass_uv1')

# Keep the normal CLI and source intact; select the CLT for every build child,
# including preflight (the app normally uses the system-selected developer tools).
DRIVER = '''
import runpy, sys
from pathlib import Path
source = Path(sys.argv.pop(1))
sys.path.insert(0, str(source / 'tools'))
from setup_kit import backend, install
original = backend.child_environment
def clt_environment(*args, **kwargs):
    env = original(*args, **kwargs)
    env['CFFIXED_USER_HOME'] = env['HOME']
    env['DEVELOPER_DIR'] = '/Library/Developer/CommandLineTools'
    return env
backend.child_environment = clt_environment
transaction = backend.transactional_install
def scratch_install(*args, **kwargs):
    kwargs['running'] = lambda: install.assert_not_running(processes='')
    return transaction(*args, **kwargs)
backend.transactional_install = scratch_install
sys.argv[0] = str(source / 'tools/setup.py')
runpy.run_path(sys.argv[0], run_name='__main__')
'''


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def nonempty(path):
    return path.is_file() and path.stat().st_size > 0


def check_scratch(path, home=None):
    home = Path.home() if home is None else home
    personal = [home / 'Applications', home / 'Documents/NFS Most Wanted']
    personal += [home / 'Library' / area / name for area, name in (
        ('Application Support', 'NFSMW-port'), ('Application Support', 'NFSMW-Setup'),
        ('Application Support', 'NFS Most Wanted Native'), ('Caches', 'NFSMW-Setup'), ('Logs', 'NFSMW-Setup'))]
    require(all(not path.is_relative_to(p.resolve()) and not p.resolve().is_relative_to(path) for p in personal),
            'scratch overlaps personal Applications, saves or Setup state')


def bundle_version(app, expected):
    with (app / 'Contents/Info.plist').open('rb') as f:
        info = plistlib.load(f)
    for key in ('CFBundleShortVersionString', 'CFBundleVersion'):
        require(info.get(key) == expected, f'{app.name}: {key} must equal {expected}, got {info.get(key)!r}')
    return info


def verify_setup(app, expected):
    info = bundle_version(app, expected)
    require(info.get('CFBundleIdentifier') == 'org.nfsmw-native.setup' and
            info.get('CFBundleExecutable') == 'Setup' and info.get('CFBundlePackageType') == 'APPL',
            'wrong Setup bundle identity')
    executable = app / 'Contents/MacOS/Setup'
    require(nonempty(executable) and not executable.is_symlink() and os.access(executable, os.X_OK),
            'missing or non-executable Setup binary')
    signature = subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)],
                               capture_output=True)
    require(signature.returncode == 0, 'invalid Setup code signature')


def snapshot(tree):
    """Hash all file bytes; refuse links that could escape the scratch copy."""
    result = {}
    for path in sorted(tree.rglob('*')):
        require(not path.is_symlink(), 'snapshot contains a link: ' + str(path))
        if path.is_file():
            h = hashlib.sha256()
            with path.open('rb') as f:
                for block in iter(lambda: f.read(1024 * 1024), b''):
                    h.update(block)
            result[path.relative_to(tree).as_posix()] = h.hexdigest()
    return result


def preferences(path):
    return [line for line in path.read_bytes().splitlines(keepends=True)
            if line.partition(b'=')[0].strip() != b'game_dir']


def read_roots(profile):
    roots = {}
    for line in (profile / 'roots.txt').read_text().splitlines():
        if line and not line.startswith('#'):
            key, sep, value = line.partition('=')
            require(sep and key in ('pc_root', 'x360_root', 'assets_root', 'movies', 'caches', 'tmp') and
                    key not in roots and Path(value).is_absolute() and Path(value).is_dir(),
                    'invalid configured root: ' + line)
            roots[key] = Path(value).resolve()
    require('pc_root' in roots, 'profile has no PC root')
    return roots


def legacy_copy(app, profile, scratch):
    info = bundle_version(app, '4.2.0')
    require(info.get('CFBundleIdentifier') == 'io.github.nfsmw-native-mac' and
            info.get('CFBundleExecutable') == 'NFSMostWanted', 'wrong legacy app identity')
    signed = subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)], capture_output=True)
    require(signed.returncode == 0, 'legacy app signature is invalid')
    original = (snapshot(app), snapshot(profile))
    dest = scratch / 'Applications'
    dest.mkdir()
    shutil.copytree(app, dest / 'NFS Most Wanted Native.app')
    target = scratch / 'home/Library/Application Support/NFSMW-port'
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(profile, target)
    roots = read_roots(profile)
    root_hashes = {key: snapshot(path) for key, path in roots.items()}
    relocated = {}
    for key, path in roots.items():
        if path.is_relative_to(profile):
            relocated[key] = target / path.relative_to(profile)
        else:
            relocated[key] = scratch / 'legacy-roots' / key
            shutil.copytree(path, relocated[key])
    (target / 'roots.txt').write_text(''.join(f'{key}={path}\n' for key, path in sorted(relocated.items())))
    require((target / 'app.txt').is_file() and (target / 'registry.txt').is_file(),
            'legacy profile must include preferences and registry')
    # Only the configured game directory is allowed to change during replacement.
    protected = {k: v for k, v in snapshot(target).items()
                 if k not in ('app.txt', 'roots.txt', '.profile.lock') and
                 not k.startswith(('installs/', '.setup-owned/'))}
    require(any(k.startswith(('NFS Most Wanted/', 'saves/', 'Saves/')) for k in protected),
            'legacy profile must include at least one save file')
    return target, original, roots, root_hashes, protected, preferences(target / 'app.txt')


def extract(archive, target):
    # Payloads come from git archive; refuse escaping links/paths even in proofs.
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            p = Path(member.name)
            require(not p.is_absolute() and '..' not in p.parts and
                    (member.isfile() or member.isdir()), 'unsafe source payload member: ' + member.name)
        # Members above are regular files/directories with relative paths only;
        # keep compatibility with the supported Python 3.9 test driver.
        tar.extractall(target)


def audit(resources, pc, scratch, expected_version, legacy=None, x360=None):
    require(re.fullmatch(r'[A-Za-z0-9/._+,:@%=-]+', str(scratch)), 'scratch path must be shell-safe for the build cache')
    scratch.mkdir(parents=True)
    # Relocate the complete payload, including Python and its wheels. Any .pyc
    # generated by -I children stays here rather than changing the Setup app.
    resources = Path(shutil.copytree(resources, scratch / 'Setup Resources'))
    source = scratch / 'extracted source'
    source.mkdir()
    extract(resources / 'source.tar.gz', source)
    for rel in TABLES:
        require(nonempty(source / rel), 'missing payload table: ' + rel)
    leaked = [str(p.relative_to(source)) for p in source.rglob('*')
              if p.name == 'Testing.md' or p.relative_to(source).as_posix() == 'runtime/docs/testing.md']
    require(not leaked, 'testing document in payload: ' + ', '.join(leaked))
    require(nonempty(source / 'runtime/docs/guide.md'), 'missing payload Guide source')
    dl = resources / 'third_party/dl'
    require(dl.is_dir(), 'missing bundled FFmpeg source')
    shutil.copytree(dl, source / 'third_party/dl')
    home = scratch / 'home'
    home.mkdir()
    work = scratch / 'work'
    dest = scratch / 'Applications'
    tmp = scratch / 'tmp'
    tmp.mkdir()
    preservation = legacy_copy(*legacy, scratch) if legacy else None
    python = resources / 'python/bin/python3'
    require(python.is_file() and os.access(python, os.X_OK), 'missing bundled Python')
    env = dict(PATH='/usr/bin:/bin:/usr/sbin:/sbin', HOME=str(home), CFFIXED_USER_HOME=str(home), TMPDIR=str(tmp),
               LANG='en_US.UTF-8', PYTHONDONTWRITEBYTECODE='1',
               DEVELOPER_DIR=str(CLT))
    # Prove this toolchain has no offline Metal compiler; never change xcode-select.
    probe = subprocess.run(['/usr/bin/xcrun', '--find', 'metal'], env=env, capture_output=True)
    require(probe.returncode != 0, 'CLT fixture unexpectedly supplies a Metal compiler')
    events_file = scratch / 'events.jsonl'
    with events_file.open('w') as events, (scratch / 'stderr.log').open('w') as errors:
        child = subprocess.Popen([str(python), '-I', '-c', DRIVER, str(source), '--build',
                                  '--pc', str(pc), '--dest', str(dest), '--work', str(work),
                                  *(['--x360', str(x360)] if x360 else []), '--events'],
                                 cwd=scratch, env=env, stdout=events, stderr=errors)
        try:
            status = child.wait(timeout=1500)
        except BaseException:
            # Give the backend's cancellation handler time to stop its current
            # compiler process group and roll back, before removing scratch.
            child.terminate()
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
            raise
    try:
        events = [json.loads(line) for line in events_file.read_text().splitlines()]
    except ValueError as e:
        raise RuntimeError('backend stdout is not JSON events') from e
    failures = [e for e in events if e.get('ev') == 'error']
    if status or failures:
        # Preserve only a small error tail in the report; full logs stay in scratch
        # until the enclosing TemporaryDirectory is removed.
        detail = json.dumps(failures[-1] if failures else {})
        pointer = work / 'run-log.txt'
        if pointer.is_file():
            log = Path(pointer.read_text().strip())
            if log.is_file():
                detail += '\n' + log.read_text(errors='replace')[-2500:]
        raise RuntimeError(f'headless build exit {status}: {detail}')
    done = [e for e in events if e.get('ev') == 'done']
    require(len(done) == 1 and events[-1] == done[0], 'missing terminal done event')
    app = dest / 'NFS Most Wanted Native.app'
    require(done[0].get('app') == str(app), 'done points outside scratch destination')
    info = bundle_version(app, expected_version)
    require(info['CFBundleIdentifier'] == 'io.github.nfsmw-native-mac', 'wrong app identity')
    require(info['CFBundleExecutable'] == 'NFSMostWanted', 'wrong app executable')
    for name in (info['CFBundleExecutable'], 'pub_update_helper', 'pub_update_worker'):
        binary = app / 'Contents/MacOS' / name
        require(nonempty(binary) and os.access(binary, os.X_OK), 'missing app binary: ' + name)
        signature = subprocess.run(['/usr/bin/codesign', '--verify', '--strict', str(binary)],
                                   capture_output=True)
        require(signature.returncode == 0, 'invalid app binary signature: ' + name)
    guide = app / 'Contents/Resources/Guide.md'
    require(nonempty(guide) and guide.read_bytes() == (source / 'runtime/docs/guide.md').read_bytes(),
            'Guide.md missing or different')
    require(not list(app.rglob('Testing.md')), 'Testing.md leaked into app')
    require(nonempty(app / 'Contents/Resources/AppIcon.icns'), 'missing app icon')
    signature = subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)],
                               capture_output=True)
    require(signature.returncode == 0, 'invalid installed app signature')
    # Tables are build inputs/compiled data, not loose app resources. Check both
    # the extracted payload and the backend's independent build copy.
    caches = list(home.glob('Library/Caches/NFSMW-Setup/builds/*/source'))
    require(len(caches) == 1, 'missing isolated build source')
    cached = caches[0]
    for rel in TABLES:
        require((cached / rel).read_bytes() == (source / rel).read_bytes(), 'build table differs: ' + rel)
    for name in GENERATED:
        require(nonempty(cached / f'runtime/gen/{name}.inc'), 'missing generated table: ' + name)
    profile = home / 'Library/Application Support/NFSMW-port'
    roots = read_roots(profile)
    require(all(path.is_relative_to(scratch) for path in roots.values()), 'installed root escaped scratch')
    game_dirs = [line.partition(b'=')[2].strip() for line in (profile / 'app.txt').read_bytes().splitlines()
                 if line.partition(b'=')[0].strip() == b'game_dir']
    require(game_dirs and all(value == os.fsencode(roots['pc_root']) for value in game_dirs),
            'installed preferences do not point at the configured PC root')
    if preservation:
        target, original, old_roots, root_hashes, protected, prefs = preservation
        after = snapshot(target)
        require(all(after.get(k) == v for k, v in protected.items()), 'legacy saves/registry bytes changed')
        require(preferences(target / 'app.txt') == prefs, 'legacy preferences changed')
        require(set(roots) == set(old_roots), 'configured roots were lost during replacement')
        require((snapshot(legacy[0]), snapshot(legacy[1])) == original, 'legacy input changed')
        require({k: snapshot(p) for k, p in old_roots.items()} == root_hashes, 'legacy input roots changed')
        print(f'legacy replacement: PASS ({len(protected)} protected files, preferences, {len(roots)} valid roots; inputs unchanged)')
    print(f'release artifact: PASS (3 binaries, Guide, 3 source tables, 7 generated tables; {len(events)} events; CLT only)')


def prove(resources, pc, scratch, expected_version):
    for fault in ('server-table', 'clt-only', 'testing-leak'):
        case = scratch / fault
        mutated = case / 'Broken Setup.app/Contents/Resources'
        mutated.mkdir(parents=True)
        # Only source.tar.gz changes; dependencies remain the shipped bytes.
        for name in ('python', 'third_party'):
            (mutated / name).symlink_to(resources / name, target_is_directory=True)
        shutil.copy2(resources.parent / 'Info.plist', mutated.parent / 'Info.plist')
        tree = case / 'source'
        tree.mkdir()
        extract(resources / 'source.tar.gz', tree)
        expected = {'server-table': 'missing payload table: recomp/server_functions.tsv',
                    'clt-only': 'headless build exit 1',
                    'testing-leak': 'testing document in payload: Testing.md'}[fault]
        if fault == 'server-table':
            (tree / 'recomp/server_functions.tsv').unlink()
        elif fault == 'testing-leak':
            (tree / 'Testing.md').write_text('Private checklist fixture\n')
        else:
            path = tree / 'tools/setup_kit/backend.py'
            text = path.read_text()
            needle = '        # No `metal` probe:'
            require(needle in text, 'CLT mutation target missing')
            text = text.replace(needle, '        runner.run(["xcrun", "--sdk", "macosx", "metal", "--version"])\n' + needle, 1)
            path.write_text(text)
        with tarfile.open(mutated / 'source.tar.gz', 'w:gz') as tar:
            tar.add(tree, arcname='.')
        # Source mutations intentionally invalidate the shipped bundle seal.
        # Exercise the payload audit directly, after the original Setup passed
        # verify_setup; entry-guard proofs use independently signed tiny apps.
        driver = '''import runpy, sys
from pathlib import Path
audit = runpy.run_path(sys.argv[1])['audit']
try:
    audit(Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]), sys.argv[5])
except (RuntimeError, OSError) as error:
    print(error, file=sys.stderr); sys.exit(1)
'''
        result = subprocess.run([sys.executable, '-I', '-c', driver, str(Path(__file__).resolve()),
                                 str(mutated), str(pc), str(case / 'audit'), expected_version],
                                capture_output=True, text=True, timeout=90)
        detail = result.stdout + result.stderr
        require(result.returncode == 1 and expected in detail,
                f'{fault} failed for the wrong reason (exit {result.returncode}): {detail[-3000:]}')
        if fault == 'clt-only':
            require('metal' in detail and 'preflight' in detail, 'CLT mutant did not reach the toolchain check')
        print(f'release artifact: broken {fault} rejected (exit {result.returncode})')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--setup', type=Path, default=ROOT / 'build/installer/dmg' / APP_NAME)
    parser.add_argument('--pc', type=Path, default=Path(os.environ.get('NFS_GAME', ROOT.parent / 'Need For Speed Most Wanted Black Edition')))
    parser.add_argument('--x360', type=Path, help='optional authentic Xbox 360 source for root/hash preservation coverage')
    parser.add_argument('--proof', action='store_true')
    parser.add_argument('--expected-version',
                        help='independent release version; both Setup and installed bundle versions must match')
    parser.add_argument('--legacy-app', type=Path, help='signed scratch 4.2.0 app (copied, never modified)')
    parser.add_argument('--legacy-profile', type=Path, help='scratch 4.2.0 profile with saves, registry, preferences and roots')
    parser.add_argument('--output', type=Path, help='new empty scratch output directory to retain the installation')
    args = parser.parse_args()
    resources = args.setup.resolve() / 'Contents/Resources'
    if not args.setup.exists() or not (args.pc / 'speed.exe').is_file():
        print('SKIP: needs packaged Setup.app (--setup) and PC v1.3 input (--pc)')
        return 3
    try:
        require(args.expected_version, '--expected-version is required for an artifact audit')
        require(bool(args.legacy_app) == bool(args.legacy_profile), 'supply both legacy app and profile')
        verify_setup(args.setup.resolve(), args.expected_version)
        require(nonempty(resources / 'source.tar.gz'), 'missing Setup source payload')
        require((CLT / 'usr/bin/clang').is_file(), 'Command Line Tools required for release artifact audit')
        def run(scratch):
            legacy = (args.legacy_app.resolve(), args.legacy_profile.resolve()) if args.legacy_app else None
            pc_before = snapshot(args.pc.resolve())
            x360 = args.x360.resolve() if args.x360 else None
            x360_before = snapshot(x360) if x360 else None
            audit(resources, args.pc.resolve(), scratch / 'good', args.expected_version, legacy, x360)
            require(snapshot(args.pc.resolve()) == pc_before, 'original PC input changed')
            require(not x360 or snapshot(x360) == x360_before, 'original Xbox 360 input changed')
            if args.proof:
                prove(resources, args.pc.resolve(), scratch / 'proof', args.expected_version)
        if args.output:
            scratch = args.output.absolute().resolve()
            check_scratch(scratch)
            inputs = [args.pc.resolve(), args.setup.resolve()]
            if args.x360:
                inputs.append(args.x360.resolve())
            if args.legacy_app:
                inputs += [args.legacy_app.resolve(), args.legacy_profile.resolve()]
            require(all(not scratch.is_relative_to(p) and not p.is_relative_to(scratch) for p in inputs),
                    'output overlaps an input')
            scratch.mkdir()  # An existing directory is never reused or removed.
            run(scratch)
        else:
            with tempfile.TemporaryDirectory(prefix='release-artifact-') as directory:
                run(Path(directory).resolve())
        return 0
    except (RuntimeError, OSError, subprocess.TimeoutExpired, KeyError) as e:
        print('release artifact: FAIL: ' + str(e), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
