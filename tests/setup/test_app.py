#!/usr/bin/env python3
"""T11: Setup app protocol, cancel, environment and legacy-updater checks (offline; temp dirs only).

Run: python3 -I tests/setup/test_app.py        (needs swiftc; no network, no window, no game files)

The app's pure logic is compiled from tools/installer/SetupApp.swift and driven through its headless modes
(--protocol-test, --env-test, --cancel-test). Every check also runs against a deliberately broken copy of the source
(or a broken comparison) and must FAIL there. Violations found (measured when written; real build = 0 in each row):
  broken control                                  violations
  false success accepted (no app/path check)      3
  split lines not reassembled                     16
  environment filter removed                      6
  cancel signals only the leader (not the group)  hang (1)
  lexical version compare (3.9.0 vs 3.10.0)       1
  constructed asset URL instead of html_url       3
  SIGKILL during install (old: always at 5 s)     1   (rollback cut off: previous version not restored)
  quit checks only "running" (old)                1   (quitting while checking left the backend running)
The install/quit controls reproduce the code before the fix (98ac89e), which had no headless mode.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SWIFT = ROOT / 'tools/installer/SetupApp.swift'
BUILD = ROOT / 'tools/installer/build_installer.sh'
_CACHE = Path(tempfile.mkdtemp(prefix='setup-app-test-', dir=os.environ.get('TMPDIR')))
_built = {}


def compile_app(source_text):
    """Compile the (possibly mutated) source once per content; returns the binary path."""
    key = hashlib.sha256(source_text.encode()).hexdigest()[:12]
    if key not in _built:
        d = _CACHE / key
        d.mkdir()
        src = d / 'SetupApp.swift'
        src.write_text(source_text)
        r = subprocess.run(['swiftc', '-Onone', '-parse-as-library', '-target', 'arm64-apple-macos14.5',
                            '-o', str(d / 'Setup'), str(src)], capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError('swiftc failed:\n' + r.stderr[-3000:])
        _built[key] = d / 'Setup'
    return _built[key]


def mutate(old, new, count=1):
    text = SWIFT.read_text()
    assert text.count(old) >= 1, 'mutation target missing: ' + old
    return text.replace(old, new) if count == 0 else text.replace(old, new, count)


def ev(**kw):
    return json.dumps(kw, separators=(',', ':')) + '\n'


def stream(*events, tail=''):
    return ''.join(events) + tail


def run_protocol(binary, text, status, chunk, mode, cancelled=False, tmp=None):
    f = Path(tmp) / 'stream.jsonl'
    f.write_text(text)
    args = [str(binary), '--protocol-test', str(f), str(status), str(chunk), mode] + (['cancelled'] if cancelled else [])
    r = subprocess.run(args, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def protocol_failures(binary, which=None):
    """Returns the list of violated expectations (empty when the app behaves as specified)."""
    bad = []
    with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as tmp:
        app = Path(tmp) / 'NFSMW.app'
        app.mkdir()
        stages = [ev(ev='stage', id=i, title=i) for i in ('preflight', 'pc', 'recompile')]
        good = stream(*stages, ev(ev='progress', id='recompile', done=5, total=10),
                      ev(ev='warn', id='pc', msg='w'), ev(ev='done', app=str(app)))
        failed = stream(*stages, ev(ev='error', id='recompile', msg='Compile failed in speed_12.c',
                                    fix='Install the Apple developer tools.', log='/x/run.log'))

        def want(name, got, **kw):
            for k, v in kw.items():
                if got.get(k) != v:
                    bad.append('%s: %s = %r, wanted %r' % (name, k, got.get(k), v))

        if which in (None, 'false-success'):
            want('success', run_protocol(binary, good, 0, 4096, 'build', tmp=tmp), outcome='success', app=str(app))
            want('failed stage', run_protocol(binary, failed, 1, 4096, 'build', tmp=tmp), outcome='failed',
                 failedStage='recompile', message='Compile failed in speed_12.c', fix='Install the Apple developer tools.')
            want('error event beats exit 0', run_protocol(binary, failed, 0, 4096, 'build', tmp=tmp), outcome='failed')
            want('cancel', run_protocol(binary, good, 143, 4096, 'build', cancelled=True, tmp=tmp), outcome='cancelled')
            want('cancel even if exit 0', run_protocol(binary, good, 0, 4096, 'build', cancelled=True, tmp=tmp), outcome='cancelled')
            want('done without app path', run_protocol(binary, stream(*stages, ev(ev='done', app='')), 0, 4096, 'build', tmp=tmp), outcome='failed')
            want('exit 0 without done', run_protocol(binary, stream(*stages), 0, 4096, 'build', tmp=tmp), outcome='failed')
            want('done but app missing', run_protocol(binary, stream(*stages, ev(ev='done', app=str(app) + '-gone')), 0, 4096, 'build', tmp=tmp), outcome='failed')
            want('done but non-zero exit', run_protocol(binary, good, 1, 4096, 'build', tmp=tmp), outcome='failed')
            want('check passes with no app', run_protocol(binary, stream(*stages, ev(ev='features', x360=False, missing=['a'])), 0, 4096, 'check', tmp=tmp),
                 outcome='success')
            want('check needs exit 0', run_protocol(binary, stream(*stages), 2, 4096, 'check', tmp=tmp), outcome='failed')
        if which in (None, 'split'):
            ref = run_protocol(binary, good, 0, 1 << 20, 'build', tmp=tmp)
            want('reference', ref, outcome='success', lines=6, stage='recompile', warnings=1)
            for chunk in (1, 3, 17):
                got = run_protocol(binary, good, 0, chunk, 'build', tmp=tmp)
                want('split %d' % chunk, got, outcome='success', lines=6, stage='recompile', warnings=1, overall=ref['overall'])
            # the final line arrives without a newline
            no_nl = good.rstrip('\n')
            want('no trailing newline', run_protocol(binary, no_nl, 0, 5, 'build', tmp=tmp), outcome='success', lines=6)
            # non-JSON noise between events is ignored, not fatal
            noisy = stream(stages[0], 'plain text line\n', '{"ev": broken\n', stages[1], ev(ev='done', app=str(app)))
            want('noise', run_protocol(binary, noisy, 0, 2, 'build', tmp=tmp), outcome='success', stage='pc')
            feat = run_protocol(binary, stream(ev(ev='features', x360=True, missing=['a', 'b'])), 0, 3, 'check', tmp=tmp)
            want('features', feat, features={'x360': True, 'missing': ['a', 'b']})
    return bad


def env_failures(binary):
    env = dict(os.environ, NFS_FOO='1', NFS_ROOTS='/evil', BOOT_RACE='1', PYTHONPATH='/evil', PYTHONHOME='/evil',
               VIRTUAL_ENV='/evil', PYTHONSTARTUP='/evil', NFSMW_SETUP_HOME='/kept/for/tests')
    r = subprocess.run([str(binary), '--env-test'], capture_output=True, text=True, env=env, timeout=30)
    got = json.loads(r.stdout)
    bad = []
    for k in ('NFS_FOO', 'BOOT_RACE', 'PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', 'PYTHONSTARTUP'):
        if k in got:
            bad.append('inherited %s leaked' % k)
    if got.get('NFS_ROOTS') != '/roots.txt':
        bad.append('NFS_ROOTS not explicit: %r' % got.get('NFS_ROOTS'))
    if got.get('NFS_PUBLIC') != '1' or got.get('TOOLS_PY') != '/py':
        bad.append('NFS_PUBLIC/TOOLS_PY not explicit')
    if got.get('NFSMW_SETUP_HOME') != '/kept/for/tests':
        bad.append('test override NFSMW_SETUP_HOME dropped')
    return bad


def cancel_failures(binary):
    try:
        r = subprocess.run([str(binary), '--cancel-test'], capture_output=True, text=True, timeout=20)
    except subprocess.TimeoutExpired:
        return ['cancel hung: the group was not stopped, pipes stayed open']
    got = json.loads(r.stdout)
    bad = []
    if got['started'] != 2:
        bad.append('test shell did not start two children')
    if got['alive']:
        bad.append('children survived cancel: %s' % got['alive'])
    return bad


def install_cancel_failures(binary):
    """Cancel in the install stage must let the backend finish its rollback; elsewhere KILL still follows TERM."""
    bad = []
    with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as tmp:
        for stage, want in (('install', {'exit': 130, 'restored': True}), ('recompile', {'exit': 137, 'restored': False})):
            marker = Path(tmp) / ('restored-' + stage)
            r = subprocess.run([str(binary), '--install-cancel-test', stage, '1', str(marker)],
                               capture_output=True, text=True, timeout=30)
            got = json.loads(r.stdout)
            if got != want:
                bad.append('%s: %s (want %s)' % (stage, got, want))
    return bad


def quit_failures(binary):
    r = subprocess.run([str(binary), '--quit-policy-test'], capture_output=True, text=True, timeout=20)
    got = json.loads(r.stdout)
    want = {'idle': 'now', 'checking': 'cancel-wait', 'building': 'ask-cancel-wait'}
    return ['%s: %s (want %s)' % (k, got.get(k), v) for k, v in want.items() if got.get(k) != v]


# --- legacy updater model: what the shipped 3.x app does with a release (compared numerically, no assets used) ----
def legacy_numeric(v):
    v = v.strip()
    if v[:1] in 'vV':
        v = v[1:]
    parts = []
    for p in v.split('.'):
        n = ''
        for c in p:
            if not c.isdigit():
                break
            n += c
        parts.append(int(n or 0))
    return parts


def legacy_offers_update(installed, release_json):
    rel = json.loads(release_json)
    a, b = legacy_numeric(installed), legacy_numeric(rel['tag_name'])
    n = max(len(a), len(b))
    a += [0] * (n - len(a)); b += [0] * (n - len(b))
    return (rel['html_url'] if b > a else None)


def lexical_offers_update(installed, release_json):
    rel = json.loads(release_json)
    return rel['html_url'] if rel['tag_name'].lstrip('vV') > installed.lstrip('vV') else None


def constructed_asset_url(installed, release_json):
    rel = json.loads(release_json)
    name = rel['tag_name']
    return 'https://example.invalid/download/%s/Setup-%s.dmg' % (name, name)   # a made-up asset URL, never the page


RELEASE = json.dumps({'tag_name': 'v4.0.0', 'html_url': 'https://example.invalid/releases/tag/v4.0.0'})   # no assets array
UPDATER_CASES = [('3.0.4', RELEASE, 'https://example.invalid/releases/tag/v4.0.0'),
                 ('v4.0.0', RELEASE, None),
                 ('3.9.0', json.dumps({'tag_name': 'v3.10.0', 'html_url': 'u'}), 'u')]   # lexical order gets this wrong


def updater_failures(fn):
    return ['%s vs %s' % (inst, json.loads(rel)['tag_name']) for inst, rel, want in UPDATER_CASES if fn(inst, rel) != want]


class AppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which('swiftc'):
            raise unittest.SkipTest('swiftc not available')
        cls.real = compile_app(SWIFT.read_text())

    # -- the app compiles (arm64, floor 14.5) and behaves on the protocol --
    def test_protocol_real(self):
        self.assertEqual(protocol_failures(self.real), [])

    def test_protocol_control_false_success_accepted(self):
        broken = compile_app(mutate('guard appExists(app) else', 'guard true || appExists(app) else')
                             .replace('guard let app = appPath else {', 'let app = appPath ?? "/"\n        if false {'))
        bad = protocol_failures(broken, 'false-success')
        self.assertGreaterEqual(len(bad), 1, 'broken control was not detected')

    def test_protocol_control_split_line(self):
        broken = compile_app(mutate('pending.append(chunk)', 'pending = chunk'))
        self.assertGreaterEqual(len(protocol_failures(broken, 'split')), 1)

    # -- environment --
    def test_environment_real(self):
        self.assertEqual(env_failures(self.real), [])

    def test_environment_control_filter_removed(self):
        broken = compile_app(mutate('if k == "HOME" ||', 'if true || k == "HOME" ||'))
        self.assertGreaterEqual(len(env_failures(broken)), 1)

    # -- cancel stops the whole process group --
    def test_cancel_real(self):
        self.assertEqual(cancel_failures(self.real), [])

    def test_cancel_control_leader_only(self):
        broken = compile_app(mutate('killpg(', 'kill(', 0))
        self.assertGreaterEqual(len(cancel_failures(broken)), 1)

    # -- Cancel during install waits for rollback; quitting always cancels and waits --
    def test_install_cancel_waits_for_rollback(self):
        self.assertEqual(install_cancel_failures(self.real), [])

    def test_install_cancel_control_old_unconditional_kill(self):
        broken = compile_app(mutate('func forceStopAllowed(stage: String) -> Bool { stage != "install" }',
                                    'func forceStopAllowed(stage: String) -> Bool { true }'))
        self.assertGreaterEqual(len(install_cancel_failures(broken)), 1)

    def test_quit_cancels_and_waits_while_checking_or_building(self):
        self.assertEqual(quit_failures(self.real), [])
        src = SWIFT.read_text()
        self.assertIn('s.stopThen { NSApp.reply(toApplicationShouldTerminate: true) } ? .terminateLater', src)

    def test_quit_control_old_running_only(self):
        broken = compile_app(mutate('guard childActive else { return .now }', 'guard childActive && building else { return .now }'))
        self.assertGreaterEqual(len(quit_failures(broken)), 1)

    # -- legacy updater: v3.0.4 sees tag v4.0.0 numerically and opens html_url; no assets parsed --
    def test_updater_numeric(self):
        self.assertEqual(updater_failures(legacy_offers_update), [])

    def test_updater_control_lexical(self):
        self.assertGreaterEqual(len(updater_failures(lexical_offers_update)), 1)

    def test_updater_control_constructed_asset_url(self):
        self.assertGreaterEqual(len(updater_failures(constructed_asset_url)), 1)

    def test_release_asset_name_follows_tag(self):
        names = subprocess.run(['sh', str(BUILD), '--print-names', 'v4.0.0'], capture_output=True, text=True).stdout.split()
        self.assertEqual(names, ['NFS-Most-Wanted-Native-Setup-v4.0.0.dmg', 'NFS-Most-Wanted-Native-Setup-v4.0.0.dmg.sha256'])

    def test_one_floor_constant(self):
        self.assertEqual(len([l for l in BUILD.read_text().splitlines() if l.startswith('MIN_MACOS=')]), 1)
        self.assertNotIn('14.5', SWIFT.read_text().replace('arm64-apple-macos14.5', ''))


def tearDownModule():
    shutil.rmtree(_CACHE, ignore_errors=True)


if __name__ == '__main__':
    unittest.main(verbosity=2)
