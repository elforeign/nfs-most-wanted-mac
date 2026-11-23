#!/usr/bin/env python3
"""T11: Setup app protocol, cancel, environment and legacy-updater checks (offline; temp dirs only).

P5 item 11 Review: destination-only/broken aggregation 0/1; grouped volume fixture 1/1.
P5 item 7 disc collection: eager-check control 0/2; explicit Check 2/2 products.

X360 choices: 8/8 combinations pass; original all-enabled loading violates 7/8.
Remembered source fields: original empty loading restores 0/2 product fixtures;
current Setup restores 2/2 (PC, destination, disc supplements and car selections).
Updater Locate: original missing prompt hook passes 0/1; current native UI passes
4/4 Locate/Skip fixtures across both products, with dialogs replaced by recorders.
Car selections: two ticked PC cars reproduce 2/2 pinned specs; defaults empty;
zero ticks and missing sources produce zero import arguments. Edited metadata
exits 1 (FAIL refusal), packaged metadata exits 0 (PASS). Removing the empty
selection guard produces 1 unwanted import command; restored guard produces 0.

Review regressions (violations, old -> new): engine messages 3 -> 0;
refresh loses recognition/plan/Import eligibility 3 -> 0. Compiled controls
restore the original generic failure and unconditional launch-state reset.

Extras proof: 23 compiled checks pass. The pinned original mode dispatch
violates 12 extras expectations; current decoding violates zero. Broken busy
skip, cancellation and retry controls each fail their state oracle. Actual
F-T events decode at one-byte boundaries, including measured per-car approval.
Install and extras cancellation both wait for the backend's transaction boundary.

Carbon product (SetupProduct=carbon): 23 checks of its backend commands (check, check with its Xbox 360 copy,
build: --game carbon --carbon; PC discs carry --pc-update and --pc-exe), the backend accepting all three, its app name,
its page order (Xbox 360 copy -> Review, no Most Wanted extras page) and Most Wanted's unchanged command: 0 violations.
The original command (backendArguments always gave Most Wanted's --pc; pinned by SHA-256 below) violates 13 of 23
(the three commands, their Most Wanted inputs and five source commands). The compiled app's folder, executable,
ZIP, 7z and ISO commands also reach the real pipeline: all five accepted with the decoded ISO tree; source digests stay fixed.

First-run recovery violations (old -> new): silent Gatekeeper failure 1 -> 0;
cancelled developer-tools install strands Welcome 1 -> 0; generic car import
success 1 -> 0. Each oracle runs against a compiled broken control.

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
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SWIFT = ROOT / 'tools/installer/SetupApp.swift'
BUILD = ROOT / 'tools/installer/build_installer.sh'
(ROOT / '.metal-cache').mkdir(exist_ok=True)
_CACHE = Path(tempfile.mkdtemp(prefix='setup-app-test-', dir=ROOT / '.metal-cache'))
_built = {}


def compile_app(source_text):
    """Compile the (possibly mutated) source once per content; returns the binary path."""
    key = hashlib.sha256(source_text.encode()).hexdigest()[:12]
    if key not in _built:
        d = _CACHE / key
        d.mkdir()
        src = d / 'SetupApp.swift'
        src.write_text(source_text)
        r = subprocess.run(['swiftc', '-module-cache-path', str(d / 'modules'), '-Onone', '-parse-as-library', '-target', 'arm64-apple-macos14.5',
                            '-o', str(d / 'Setup'), str(src)], capture_output=True, text=True)
        if r.returncode:
            # SwiftUI's macros expand in a plugin process the GPU-check sandbox does not allow; the host run covers it.
            if (os.environ.get('NFS_GPU_SANDBOX') or os.environ.get('CODEX_SANDBOX')) and \
                    'external macro implementation' in r.stderr:
                print('SKIP: PENDING host run (SwiftUI macros need the plugin process): runtime/offline_tests.sh test_app')
                sys.exit(3)
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
    with tempfile.TemporaryDirectory(dir=ROOT / '.metal-cache') as tmp:
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


def car_protocol_failures(binary):
    bad = []
    with tempfile.TemporaryDirectory(dir=ROOT / '.metal-cache') as tmp:
        recognition = ev(ev='cars-recognition', game='carbon', platform='pc', approval_available=True,
                         output_verified=False, cars=[{'car_hash': 'deadbeef', 'status': 'importable'}])
        volume = dict(device='1', paths=['/work', '/catalog'], extraction_bytes=100,
                      staging_bytes=None, output_bytes=None, retention_bytes=0, margin_bytes=16,
                      available_bytes=1000, required_bytes=None, sufficient=None)
        plan = ev(ev='cars-plan', measured=False, approved=False, volumes=[volume], cars=[])
        progress = ev(ev='cars-progress', phase='converted', slot=11, done=1, total=1)
        result = dict(ev='cars-result', operation='import', verified=True, generation='g-' + 'a' * 64,
                      message='', selection=[11], cars=[dict(slot=11, name='Synthetic café', state='ready', selected=True)],
                      generations=[])
        text = recognition + plan + progress + ev(**result)
        def want(name, text, outcome='success', mode='carsImport', status=0, cancelled=False, chunk=1, **extra):
            got = run_protocol(binary, text, status, chunk, mode, cancelled=cancelled, tmp=tmp)
            for key, value in dict(outcome=outcome, **extra).items():
                if got.get(key) != value:
                    bad.append('%s: %s = %r, wanted %r' % (name, key, got.get(key), value))
        for chunk in (1, 3, 29, 65536):
            want('split extras %d' % chunk, text, chunk=chunk, carOperation='import', carSelection=[11],
                 carPhase='converted', carSlot=11, carVolumes=1, carApproved=False,
                 recognition={'platform': 'pc', 'approval': True, 'count': 1})
        for game, platform in (('prostreet', 'pc'), ('undercover', 'pc'), ('undercover', 'x360')):
            approved = platform == 'pc'
            event = ev(ev='cars-recognition', game=game, platform=platform, approval_available=approved,
                       output_verified=False, cars=[{'car_hash': 'deadbeef', 'status': 'importable'}])
            want(game + ' ' + platform, event + ev(**result),
                 recognition={'platform': platform, 'approval': approved, 'count': 1})
        want('no newline', text.rstrip('\n'), carOperation='import')
        want('no result', recognition + plan, 'failed')
        want('done is not car success', recognition + ev(ev='done', app=tmp), 'failed')
        want('wrong operation', ev(**dict(result, operation='check')), 'failed')
        want('failed exit', text, 'failed', status=1)
        want('cancelled optional', recognition, 'cancelled', status=130, cancelled=True)
        want('committed before cancel', text, cancelled=True)
        want('unverified result', ev(**dict(result, verified=False)), 'failed')
        want('error beats result', text + ev(ev='error', id='cars', msg='failed', fix='retry', log=''), 'failed')
        want('result is not install success', text, 'failed', mode='build')
        want('unknown fields', ev(**dict(result, approval_override=True)), 'failed')
        want('nested unknown fields', ev(**dict(result, cars=[dict(result['cars'][0], policy='local')])), 'failed')
        want('wrong bool type', ev(**dict(result, verified=1)), 'failed')
        want('wrong slot type', ev(**dict(result, selection=[True])), 'failed')
        want('invalid generation', ev(**dict(result, generation='g-../source')), 'failed')
        want('missing committed generation', ev(**dict(result, generation='')), 'failed')
        want('slot out of range', ev(**dict(result, selection=[76])), 'failed')
        want('unverified retained directory', ev(**dict(result, operation='list', generations=[dict(generation='g-invalid', verified=False)])), mode='carsList')
        want('retained swap slot', ev(**dict(result, operation='list', cars=[dict(slot=89, name='', state='absent', selected=False)])),
             mode='carsList', carOperation='list')
    return bad


def car_message_failures(binary):
    bad = []
    with tempfile.TemporaryDirectory(dir=ROOT / '.metal-cache') as tmp:
        for operation in ('import', 'remove', 'restore'):
            message = 'Retained pack failed verification: ' + operation
            event = ev(ev='cars-result', operation=operation, verified=False, generation='g-' + 'b' * 64,
                       message=message, selection=[], cars=[], generations=[])
            got = run_protocol(binary, event, 0, 1, 'cars' + operation.capitalize(), tmp=tmp)
            if (got.get('outcome'), got.get('message')) != ('failed', message):
                bad.append(operation)
        # Missing/mismatched/empty messages still use the generic fallback.
        for event in ('', ev(ev='cars-result', operation='remove', verified=False,
                            generation='g-' + 'b' * 64, message='wrong operation', selection=[], cars=[], generations=[]),
                      ev(ev='cars-result', operation='import', verified=False,
                         generation='g-' + 'b' * 64, message='', selection=[], cars=[], generations=[])):
            got = run_protocol(binary, event, 0, 1, 'carsImport', tmp=tmp)
            if got.get('message') != 'The extras operation ended without a verified result.':
                bad.append('generic fallback')
    return bad


def refresh_failures(source):
    """Exercise runCars and launch's real reset; replace only the child transport."""
    start = source.index('        let python = bundledPython()', source.index('    private func launch('))
    end = source.index('    func consume(', start)
    source = source[:start] + '''        state.carResult = CarResult(operation: "list", verified: true, generation: "",
                                    message: "", selection: [], cars: [], generations: [])
        finish(.success(app: nil), state)
    }

''' + source[end:]
    harness = '''
        if args.count > 1, args[1] == "--refresh-review-test" {
            let s = Setup()
            s.carbon = URL(fileURLWithPath: "/source")
            let choice = CarChoice(game: "carbon", platform: "pc", slot: 1, folder: "300C", spec: [:])
            s.carChoices = [choice]; s.selectedCarIDs = [choice.id]
            s.state.recognition = CarRecognition(game: "carbon", platform: "pc", approval_available: true,
                                                output_verified: false, cars: [RecognizedCar(car_hash: choice.carHash, status: "importable")])
            s.state.carPlan = CarPlan(measured: true, approved: true, volumes: [], cars: [])
            s.runCars(.carsList)
            let keptRecognition = s.state.recognition != nil
            let keptPlan = s.state.carPlan?.approved == true
            let importEnabled = !s.busy && s.carbon != nil && s.availableCarSelection && s.state.carPlan?.approved == true
            let installedUpdated = s.installedCars?.operation == "list"
            s.selectedCarIDs = []
            s.runCars(.carsCheck)
            print(jsonString(["recognition": keptRecognition, "plan": keptPlan, "import": importEnabled,
                              "installed": installedUpdated, "newCheckClears": s.state.recognition == nil && s.state.carPlan == nil]))
            exit(0)
        }
'''
    source = source.replace('        let args = CommandLine.arguments', '        let args = CommandLine.arguments\n' + harness)
    binary = compile_app(source)
    result = subprocess.run([str(binary), '--refresh-review-test'], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    got = json.loads(result.stdout)
    return [key for key in ('recognition', 'plan', 'import', 'installed', 'newCheckClears') if not got.get(key)]


CARBON_TAIL = ['--dest', '/dest', '--work', '/work', '--events']
MW_BUILD = ['--build', '--pc', '/pc', '--pc-update', '/patch', '--pc-disc', '/disc2', '--x360', '/x360',
            '--x360-extras', 'arrest,textures,ui'] + CARBON_TAIL


def backend_accepts(command, tmp):
    """The real backend parses the app's Carbon command and reaches its build step (stubbed): '' or the refusal."""
    sys.path.insert(0, str(ROOT / 'tools'))
    import io
    from contextlib import redirect_stdout
    from unittest import mock
    from setup_kit import backend
    base = Path(tmp)
    places = {name: base / name[1:] for name in ('/carbon', '/x360', '/dest', '/work', '/pc')}
    for name in ('/carbon', '/x360', '/dest', '/pc'):
        places[name].mkdir()
    argv = [str(places.get(a, a)) for a in command[2:]]   # after "-I tools/setup.py"
    out = io.StringIO()
    with mock.patch.dict(os.environ, {'NFSMW_SETUP_HOME': str(base / 'home')}), \
            mock.patch.object(backend, 'build', side_effect=backend.SetupError('reached build')), redirect_stdout(out):
        backend.main(argv)
    return '' if 'reached build' in out.getvalue() else out.getvalue()[-300:]


def carbon_product_failures(binary):
    result = subprocess.run([str(binary), '--carbon-product-test'], capture_output=True, text=True, timeout=30)
    if result.returncode:
        return ['carbon product test failed to run']
    got = json.loads(result.stdout)
    bad = []

    def want(name, ok):
        if not ok:
            bad.append(name)
    for key, mode, x360 in (('check', '--check', []), ('checkX360', '--check', ['--x360', '/x360']),
                            ('build', '--build', ['--x360', '/x360'])):
        args = got[key]
        want(key + ' command', args[2:] == [mode, '--game', 'carbon', '--carbon', '/carbon'] + x360 + CARBON_TAIL)
        want(key + ' has no Most Wanted inputs', not any(a.startswith('--pc') or a == '--x360-extras' for a in args))
        with tempfile.TemporaryDirectory(prefix='setup-carbon-', dir='/private/tmp') as tmp:   # outside the source tree
            want(key + ' accepted by the backend', backend_accepts(args, tmp) == '')
    for key, mode, path in (('discCheck', '--check', '/carbon.iso'), ('discBuild', '--build', '/carbon.mdf')):
        want(key, got[key][2:] == [mode, '--game', 'carbon', '--carbon', path,
                                   '--pc-update', '/carbon-patch', '--pc-exe', '/NFSC.exe'] + CARBON_TAIL)
    want('Most Wanted command unchanged', got['mwBuild'][2:] == MW_BUILD)
    want('Carbon app name', got['app'] == '/dest/NFS Carbon Native.app')
    want('Most Wanted app name', got['mwApp'] == '/dest/NFS Most Wanted Native.app')
    want('Carbon skips the Most Wanted extras page', got['afterX360'] == 4)   # Page.review
    want('Most Wanted keeps its extras page', got['mwAfterX360'] == 3)       # Page.extras
    want('an unmarked bundle is Most Wanted', got['bundled'] == 'mw')
    want('Carbon export product and changed destination', got['export'][2:] ==
         ['--export-all-in-one', '--app', '/Carbon.app', '--dest', '/backup/Carbon.app',
          '--work', '/work', '--events', '--game', 'carbon'])
    for path, args in zip(('/folder', '/folder/NFSC.exe', '/game.zip', '/game.7z', '/disc.iso'), got['inputs']):
        want('Carbon source command ' + path, args[2:] ==
             ['--check', '--game', 'carbon', '--carbon', path, '--dest', '/other-destination', '--work', '/work', '--events'])
    return bad


def extras_flow_failures(binary):
    result = subprocess.run([str(binary), '--extras-flow-test'], capture_output=True, text=True, timeout=20)
    if result.returncode:
        return ['extras flow failed to run']
    got = json.loads(result.stdout)
    names = ('busySkipRefused', 'failureIsOptional', 'retryReset', 'cancelled', 'skipped', 'completed', 'retryAfterSuccess', 'retryAfterSkip', 'pageOrder')
    return [key for key in names if got.get(key) is not True]


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
    with tempfile.TemporaryDirectory(dir=ROOT / '.metal-cache') as tmp:
        for stage, want in (('install', {'exit': 130, 'restored': True}), ('cars', {'exit': 130, 'restored': True}), ('recompile', {'exit': 137, 'restored': False})):
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
    def test_P5_7_collect_disc_inputs_before_check(self):
        """Both products: broken eager selection 0/2; collected selection 2/2."""
        text = SWIFT.read_text()
        picker_start = text.index('    func pickFolder(')
        picker_end = text.index('\n    func locateSource(', picker_start)
        text = text[:picker_start] + '''    func pickFolder(message: String, files: Bool = false, types: [UTType] = [], folders: Bool = true) -> URL? {
            return URL(fileURLWithPath: "/disc.iso")
        }
''' + text[picker_end:]
        check_start = text.index('    func runCheck(forX360: Bool) {')
        check_end = text.index('\n    func build()', check_start)
        text = text[:check_start] + '''    func runCheck(forX360: Bool) { x360Status += "check" }
''' + text[check_end:]
        hook = '''
        if args.count > 1, args[1] == "--disc-input-test" {
            var rows: [[String: Any]] = []
            for product in [SetupProduct.mw, .carbon] {
                let model = Setup(product: product)
                if product == .mw { model.chooseKind(.disc); model.chooseDisc() }
                else { model.choosePC() }
                model.chooseUpdate(); model.chooseExe(); model.chooseOtherDisc(2)
                let before = model.x360Status
                model.runCheck(forX360: false)
                rows.append(["before": before, "after": model.x360Status,
                             "instructions": model.discInstructions,
                             "args": model.installArguments(mode: .check, pc: model.pc!, x360: nil,
                                                           dest: model.dest, work: workDir())])
            }
            print(jsonString(rows)); exit(0)
        }
'''
        text = text.replace('        let args = CommandLine.arguments\n', '        let args = CommandLine.arguments\n' + hook)

        def collected(source):
            binary = compile_app(source)
            rows = json.loads(subprocess.check_output([binary, '--disc-input-test'], text=True))
            passed = 0
            for row, version, exe in zip(rows, ('1.3', '1.4'), ('speed.exe', 'NFSC.exe')):
                command = row['args']
                passed += (row['before'] == '' and row['after'] == 'check'
                           and row['instructions'] == f'A disc install needs three things: the disc (or a copy of its files), the official {version} update, and the {exe} from that update. Choose all three, then press Check.'
                           and command[command.index('--pc-disc') + 1] == '/disc.iso')
            return passed

        self.assertEqual(collected(text.replace('        discInputsChanged()\n', '        runCheck(forX360: false)\n')), 0)
        self.assertEqual(collected(text), 2)
        # Both product panels use the same picker rows and Check action.
        self.assertEqual(SWIFT.read_text().count('\n                        discInputs\n') +
                         SWIFT.read_text().count('\n                    discInputs\n'), 2)
        self.assertIn('Button("Add another disc")', SWIFT.read_text())

    def test_P5_11_review_totals_and_retry(self):
        """P5 item 11 Review: original 0/1; grouped volume/shortage/Retry fixture 1/1."""
        source = SWIFT.read_text()
        self.assertIn("struct StorageVolume", source)
        self.assertIn('Button("Retry") { s.build() }', source)
        self.assertIn('Button("Retry") { s.objectWillChange.send() }', source)
        harness = r'''
        if args.count == 2, args[1] == "--p5-space-test" {
            let needs: [(URL, Double)] = [(URL(fileURLWithPath: "/work"), 5),
                (URL(fileURLWithPath: "/cache"), 4), (URL(fileURLWithPath: "/data"), 3),
                (URL(fileURLWithPath: "/destination"), 0.25)]
            let volumes = storagePlan(needs) { path in
                let external = path.path == "/cache"
                return StorageVolume(id: external ? "external" : "internal", name: external ? "Cache Disk" : "Mac",
                                     availableGB: external ? 2 : 7)
            }
            guard volumes.count == 2,
                  let cache = volumes.first(where: { $0.id == "external" }), cache.requiredGB == 4, !cache.sufficient,
                  cache.shortage == "Not enough space on Cache Disk: 2.0 GB more needed. Free up space there, then press Retry.",
                  let internalVolume = volumes.first(where: { $0.id == "internal" }), internalVolume.requiredGB == 8.25,
                  !internalVolume.sufficient else { exit(1) }
            let full = storagePlan(needs) { _ in StorageVolume(id: "one", name: "One", availableGB: 20) }
            guard full.count == 1, full[0].requiredGB == 12.25, full[0].sufficient else { exit(1) }
            let s = Setup()
            guard s.storageNeeds.count == 4 else { exit(1) }
            print("P5 Review PASS: 2 volumes, 12.25 GB total, both shortages detected; Retry retained")
            exit(0)
        }
'''
        source = source.replace('        let args = CommandLine.arguments', '        let args = CommandLine.arguments\n' + harness)
        binary = compile_app(source)
        result = subprocess.run([str(binary), '--p5-space-test'], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("P5 Review PASS", result.stdout)
        broken = source.replace("total.requiredGB += amount", "total.requiredGB = amount")
        self.assertNotEqual(source, broken)
        control = subprocess.run([str(compile_app(broken)), '--p5-space-test'], capture_output=True, text=True, timeout=30)
        self.assertEqual(control.returncode, 1, control.stdout + control.stderr)


    def test_updater_locate_prompt_and_reply_both_products(self):
        prefix = r'''
#import <Cocoa/Cocoa.h>
static NSMutableArray *fixtureButtons;
static NSString *fixture;
static BOOL fixtureSkip;
@interface LocateAlert : NSObject
@property NSString *messageText;
@property NSString *informativeText;
- (void)addButtonWithTitle:(NSString *)title;
- (NSModalResponse)runModal;
@end
@implementation LocateAlert
- (void)addButtonWithTitle:(NSString *)title { [fixtureButtons addObject:title]; }
- (NSModalResponse)runModal { return fixtureSkip ? NSAlertSecondButtonReturn : NSAlertFirstButtonReturn; }
@end
@interface LocatePanel : NSObject
@property BOOL canChooseDirectories, canChooseFiles, allowsMultipleSelection;
@property NSString *message, *prompt;
@property NSURL *URL;
+ (instancetype)openPanel;
- (NSModalResponse)runModal;
@end
@implementation LocatePanel
+ (instancetype)openPanel { LocatePanel *p = [LocatePanel new]; p.URL = [NSURL fileURLWithPath:@"/moved/source"]; return p; }
- (NSModalResponse)runModal { return NSModalResponseOK; }
@end
'''
        suffix = r'''
void settings_add(Setting *setting) { (void)setting; }
const char *userdata_dir(void) { return fixture.fileSystemRepresentation; }
int main(int argc, char **argv) { @autoreleasepool {
    fixture = [NSString stringWithUTF8String:argv[1]]; fixtureSkip = argc > 2;
    fixtureButtons = [NSMutableArray new]; pu_job = fixture; pu_loaded = YES; pu_state = PU_BUILDING;
    NSString *token = @"0123456789abcdef0123456789abcdef";
    NSDictionary *request = @{@"token":token, @"title":@"Can't find Source", @"body":@"It was at /old/source. If you moved it, choose its new location. Your settings and cars are kept."};
    [NSFileManager.defaultManager createDirectoryAtPath:[fixture stringByAppendingPathComponent:@"source-replies"] withIntermediateDirectories:YES attributes:nil error:nil];
    int held = open([fixture stringByAppendingPathComponent:@"prepare.lock"].fileSystemRepresentation, O_CREAT | O_RDWR, 0600);
    flock(held, LOCK_EX | LOCK_NB);
    PUWrite([fixture stringByAppendingPathComponent:@"progress.json"], @{@"schema":@1, @"state":@"building", @"message":@"Waiting for source", @"source":request});
    PULoad();
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, NSEC_PER_SEC / 10), dispatch_get_main_queue(), ^{
        NSDictionary *reply = PURead([[fixture stringByAppendingPathComponent:@"source-replies"] stringByAppendingPathComponent:[token stringByAppendingString:@".json"]]);
        NSData *data = [NSJSONSerialization dataWithJSONObject:@{@"buttons":fixtureButtons, @"reply":reply ?: @{}} options:0 error:nil];
        puts([[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding].UTF8String); close(held); exit(0);
    });
    dispatch_main();
} }
'''
        source = (ROOT / 'runtime/pub_update.m').read_text().replace('NSAlert *alert = [NSAlert new]',
                    'LocateAlert *alert = [LocateAlert new]').replace('NSOpenPanel *panel = [NSOpenPanel openPanel]',
                    'LocatePanel *panel = [LocatePanel openPanel]')
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for game, broken in ((1, False), (2, False), (1, True)):
                text = source.replace('PULocateSource(value[@"source"]);', '(void)0;') if broken else source
                file = directory / f'locate-{game}-{broken}.m'
                file.write_text(prefix + text + suffix)
                binary = file.with_suffix('')
                result = subprocess.run(['clang', '-fobjc-arc', '-Wno-incompatible-pointer-types',
                                         '-I', str(ROOT / 'runtime'), f'-DNFS_GAME={game}', str(file),
                                         '-framework', 'Cocoa', '-o', str(binary)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr[-2000:])
                for skip in (False, True) if not broken else (False,):
                    job = directory / f'job-{game}-{broken}-{skip}'
                    job.mkdir()
                    result = subprocess.run([str(binary), str(job)] + (['skip'] if skip else []),
                                            capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    row = json.loads(result.stdout)
                    if broken:
                        self.assertEqual(row, dict(buttons=[], reply={}))
                    else:
                        self.assertEqual(row['buttons'], ['Locate…', 'Skip this source'])
                        self.assertEqual(row['reply'], dict(path=None if skip else '/moved/source'))

    def test_remembered_source_fields_and_original_control(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            (state / 'Carbon').mkdir()
            saved = dict(format_version=1, pc='/old/game', x360='/old/console', destination='/apps/Game.app',
                         pc_update='/old/patch', pc_exe='/old/exe', pc_discs=['/old/disc2'])
            for folder in (state, state / 'Carbon'):
                (folder / 'source-choices.json').write_text(json.dumps(saved))
            (state / 'car-source-choices.json').write_text(json.dumps(dict(source='/old/cars', selection=['car-one'])))
            def restored(binary):
                count = 0
                for product in ('mw', 'carbon'):
                    result = subprocess.run([str(binary), '--remembered-choices-test', product, str(state)],
                                            capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    row = json.loads(result.stdout)
                    if (row['pc'], row['x360'], row['dest'], row['update'], row['exe'], row['discs'], row['disc']) == \
                       ('/old/game', '/old/console', '/apps', '/old/patch', '/old/exe', ['/old/disc2'], True):
                        if product == 'carbon' or (row['carbon'], row['selection']) == ('/old/cars', ['car-one']):
                            count += 1
                return count
            self.assertEqual(restored(self.real), 2)
            broken = compile_app(mutate('let saved = read(gameState.appendingPathComponent("source-choices.json"))',
                                        'let saved: [String: Any] = [:]'))
            self.assertEqual(restored(broken), 0)

    @classmethod
    def setUpClass(cls):
        if not shutil.which('swiftc'):
            raise unittest.SkipTest('swiftc not available')
        profile = mock.patch.dict(os.environ, {'NFSMW_SETUP_HOME': str(_CACHE / 'home')})
        profile.start()
        cls.addClassCleanup(profile.stop)
        cls.real = compile_app(SWIFT.read_text())

    def test_gatekeeper_open_anyway(self):
        path = 'System Settings > Privacy & Security > Open Anyway'
        for doc in ('GETTING_STARTED.md', 'tools/installer/ReadMe.txt'):
            self.assertIn(path, (ROOT / doc).read_text())
        original = 'if current.domain == NSOSStatusErrorDomain {'
        broken = compile_app(mutate(original, 'if false {'))

        def explained(binary):
            message = subprocess.check_output([binary, '--game-open-test', '-67018'], text=True)
            return 'macOS blocked the game' in message and path in message

        self.assertFalse(explained(broken))
        self.assertTrue(explained(self.real))
        self.assertNotIn(path, subprocess.check_output([self.real, '--game-open-test', '-10827'], text=True))

    def test_cancelled_tools_install_can_retry(self):
        source = mutate('if canContinue { return nil }',
                        'if canContinue { return nil }; if case .waiting = self { return nil }')
        source = source.replace('if case let .waiting(until) = self, now < until { return true }',
                                'if case .waiting = self { return true }')
        broken = compile_app(source)

        def can_retry(binary):
            got = json.loads(subprocess.check_output([binary, '--developer-tools-test'], text=True))
            return (not got['polling'] and got['waitingButton'] == got['retryButton'] == got['failedButton']
                    == 'Retry Install…' and 'Click Retry Install.' in got['message']
                    and not got['continueWhileMissing'] and got['continueEnabled'])

        self.assertFalse(can_retry(broken))
        self.assertTrue(can_retry(self.real))

    def test_import_success_shows_activation_steps(self):
        steps = ('In the game, press F10. Open Cars & cutscenes. '
                 'Turn on Cars from later games. Restart the game.')
        broken = compile_app(mutate(f'"{steps}"', '"Extras operation completed."'))

        def shows_steps(binary):
            got = json.loads(subprocess.check_output([binary, '--extras-flow-test'], text=True))
            return got['imported'] == steps

        self.assertFalse(shows_steps(broken))
        self.assertTrue(shows_steps(self.real))

    def test_recovery_uses_the_matching_product_profile(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.metal-cache') as tmp:
            home = Path(tmp)
            journals = []
            restored = []
            for profile in (home / 'data', home / 'data/Carbon'):
                profile.mkdir()
                token = '1' * 32
                original = profile / 'roots.txt'
                backup = profile / f'.roots.txt.backup-{token}'
                backup.write_text(profile.name)
                stat = backup.stat()
                journal = profile / '.install-journal.json'
                journal.write_text(json.dumps(dict(state='preparing', token=token, dest=str(home / 'apps'),
                                                   ids={str(original): [[stat.st_dev, stat.st_ino], None]})))
                journals.append(journal)
                restored.append(original)
            env = {key: value for key, value in os.environ.items() if not key.startswith('NFSMW_SETUP_')}
            env['NFSMW_SETUP_HOME'] = tmp
            for product, journal, original in zip(('carbon', 'mw'), reversed(journals), reversed(restored)):
                args = json.loads(subprocess.check_output(
                    [self.real, '--recovery-arguments-test', product, ROOT, home / 'work'], text=True))
                subprocess.run([sys.executable, *args], env=env, check=True, capture_output=True, timeout=30)
                self.assertFalse(journal.exists())
                self.assertEqual(original.read_text(), original.parent.name)
                if product == 'carbon':
                    self.assertTrue(journals[0].exists())

    def test_mw_extras_uses_the_selected_sevenzip_file_or_folder(self):
        # On 999f865a both paths are redirected to pc_src (0/2); after the fix both selections pass (2/2).
        source = SWIFT.read_text()
        start = source.index('        let previous = state\n', source.index('    private func launch('))
        end = source.index('    func consume(', start)
        source = source[:start] + '''        print(jsonString(["arguments": arguments]))
        exit(0)
    }

''' + source[end:]
        harness = '''
        if args.count == 3, args[1] == "--mw-extras-source-test" {
            let s = Setup()
            s.pc = s.pcRoot(URL(fileURLWithPath: args[2]))
            s.carbon = URL(fileURLWithPath: "/carbon")
            let choice = CarChoice(game: "carbon", platform: "pc", slot: 1, folder: "300C", spec: [:])
            s.carChoices = [choice]; s.selectedCarIDs = [choice.id]
            s.state.recognition = CarRecognition(game: "carbon", platform: "pc", approval_available: true,
                                                output_verified: false, cars: [RecognizedCar(car_hash: choice.carHash, status: "importable")])
            s.runCars(.carsCheck)
            exit(1)
        }
'''
        source = source.replace('        let args = CommandLine.arguments', '        let args = CommandLine.arguments\n' + harness)
        binary = compile_app(source)
        with tempfile.TemporaryDirectory(dir=ROOT / '.metal-cache') as tmp:
            folder = Path(tmp) / 'X.7z'
            folder.mkdir()
            archive = Path(tmp) / 'MW.7z'
            archive.touch()
            for pc in (folder, archive):
                with self.subTest(pc=pc.name):
                    result = subprocess.run([str(binary), '--mw-extras-source-test', str(pc)], check=True,
                                            env=dict(os.environ, NFSMW_SETUP_HOME=tmp), capture_output=True, text=True, timeout=30)
                    args = json.loads(result.stdout)['arguments']
                    self.assertEqual(args[args.index('--cars-mw-pc') + 1], str(pc))

    def test_packaged_car_selection_arguments(self):
        sys.path.insert(0, str(ROOT / 'tools'))
        from setup_kit import car_selection, car_sources
        rows = car_selection.load()
        pc = [r for r in rows if r['origin']['platform'] == 'pc'
              and r['origin']['folder'] in ('240SX', '300C', 'ZONDA')]
        recognition = dict(game='carbon', platform='pc', approval_available=True, output_verified=False,
                           cars=[dict(car_hash=car_sources.path_hash('CARS/' + r['origin']['folder']),
                                      status='importable') for r in pc])
        selected = ['pc:' + str(r['slot']) for r in pc]
        with tempfile.TemporaryDirectory(dir=ROOT / '.metal-cache') as td:
            def call(binary, ticks, metadata=car_selection.METADATA, source=recognition):
                result = subprocess.run([str(binary), '--car-selections-test', str(metadata), td,
                                         json.dumps(source), json.dumps(ticks)], capture_output=True, text=True, timeout=30)
                return result, json.loads(result.stdout) if result.returncode == 0 else None
            result, got = call(self.real, selected)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(Path(got['spec']).read_text()), [car_selection.spec(r) for r in pc])
            self.assertIn('--cars-import', got['arguments'])
            self.assertEqual(got['arguments'][got['arguments'].index('--cars-spec') + 1], got['spec'])
            self.assertEqual(got['defaults'], [])
            self.assertIn('Chrysler 300C', got['names'])
            self.assertIn('Porsche 911 GT3 RS', got['names'])
            self.assertEqual(got['names'].count('Pagani Zonda F'), 1)
            self.assertEqual(len(got['names']), 89)
            for name in ('Nissan 240SX', 'Porsche 911 Turbo', 'Chevrolet Camaro Concept',
                         'Infiniti G35', 'Mazdaspeed3', 'Shelby GT500'):
                self.assertEqual(got['names'].count(name), 1 if name == 'Shelby GT500' else 2)
            for ticks, source in (([], recognition), (selected, dict(recognition, cars=[])),
                                  (selected, dict(recognition, platform='x360'))):
                result, got = call(self.real, ticks, source=source)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(got['arguments'], [])
                self.assertEqual(got['spec'], '')
            for game in ('prostreet', 'undercover'):
                matches = [r for r in rows if r['origin']['game'] == game]
                source = dict(recognition, game=game, cars=[
                    dict(car_hash=car_sources.path_hash('CARS/' + r['origin']['folder']), status='importable')
                    for r in matches])
                ticks = selected + [game + ':pc:' + str(r['slot']) for r in matches]
                result, got = call(self.real, ticks, source=source)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(Path(got['spec']).read_text()),
                                 [car_selection.spec(r) for r in matches])
            changed = Path(td) / 'edited.json'
            changed.write_bytes(car_selection.METADATA.read_bytes().replace(b'300C', b'300X', 1))
            result, _ = call(self.real, selected, metadata=changed)
            self.assertEqual(result.returncode, 1)
            self.assertIn('failed verification', result.stderr)
            broken = compile_app(mutate('    if operation == "import" && selection == nil { return [] }', ''))
            _, got = call(broken, [])
            self.assertIn('--cars-import', got['arguments'])
        text = SWIFT.read_text()
        self.assertNotIn('chooseCarSelection', text)
        self.assertNotIn('Choose a car selection file', text)
        self.assertIn('Toggle(car.name', text)
        self.assertIn('$0.game == recognition.game && $0.platform == recognition.platform', text)

    def test_x360_optional_choices_arguments_and_space(self):
        def violations(binary):
            bad = []
            for mask in range(8):
                names = [n for i, n in enumerate(("arrest", "textures", "ui")) if mask & (1 << i)]
                saved = json.dumps(dict(x360_extras=names))
                result = subprocess.run([str(binary), "--x360-extras-test", saved], capture_output=True, text=True, timeout=20)
                row = json.loads(result.stdout)
                expected_bytes = sum(size for n, size in (("arrest", 239_000_000), ("textures", 253_000_000), ("ui", 39_000_000)) if n in names)
                args = row["arguments"]
                if row["names"] != names or row["bytes"] != expected_bytes or "--x360-extras" not in args:
                    bad.append(mask)
                elif args[args.index("--x360-extras") + 1] != ",".join(names):
                    bad.append(mask)
                elif abs(row["requiredGB"] - (12 - (531_000_000 - expected_bytes - (146_000_000 if "textures" in names and "arrest" not in names else 0)) / 1e9)) > 1e-8:
                    bad.append(mask)
            return bad
        self.assertEqual(violations(self.real), [])
        broken = compile_app(mutate('return X360Extras(arrest: names.contains("arrest"), textures: names.contains("textures"), ui: names.contains("ui"))', 'return X360Extras()'))
        self.assertEqual(len(violations(broken)), 7)
        text = SWIFT.read_text()
        for binding in ("arrest", "textures", "ui"):
            self.assertIn('isOn: $s.x360Extras.' + binding, text)
        self.assertIn('x360Extras: x360Extras)', text)
        pick = text.split('    func chooseX360() {', 1)[1].split('    func chooseCarbon()', 1)[0]
        self.assertNotIn('runCheck(', pick, 'conversion must wait until the extras have been selected')

    def test_carbon_product_commands_pages_and_original_control(self):
        self.assertEqual(carbon_product_failures(self.real), [])
        # The original: every check or build got Most Wanted's --pc, whatever the product.
        original = ('    var a = ["-I", sourceDir().appendingPathComponent("tools/setup.py").path,\n'
                    '             mode == .check ? "--check" : "--build", "--pc", pc.path]')
        self.assertEqual(hashlib.sha256(original.encode()).hexdigest(),
                         '0b76db77a49cb62ba3340bc737e399c40b6569556bb6ccb9392a339f8d40455a')
        self.assertIn(original, SWIFT.read_text())   # still Most Wanted's command
        broken = compile_app(mutate('    if carbon {\n        // Carbon reads', '    if false {\n        // Carbon reads'))
        bad = carbon_product_failures(broken)
        print('Original Setup command for Carbon: %d of 23 violated' % len(bad))
        self.assertEqual(len(bad), 13)

    def test_carbon_disc_arguments_and_controls(self):
        # Original Carbon argument forwarding loses both inputs: two failed commands.
        for line in ('        if let u = pcUpdate { a += ["--pc-update", u.path] }',
                     '        if let e = pcExe { a += ["--pc-exe", e.path] }'):
            broken = compile_app(mutate(line, ''))
            self.assertEqual(carbon_product_failures(broken), ['discCheck', 'discBuild'])
        text = SWIFT.read_text()
        self.assertIn('if s.carbonDiscSource {', text)
        self.assertIn('Choose the official Carbon update for your PC disc edition.', text)
        self.assertIn('Choose your compatible NFSC.exe.', text)

    def test_carbon_generated_source_commands_reach_real_pipeline(self):
        # The compiled app supplies every argument. Only native builds/toolchain probes are synthetic.
        sys.path.insert(0, str(ROOT / 'tests/setup'))
        import test_backend as fixtures
        from contextlib import redirect_stdout
        from unittest import mock
        result = subprocess.run([str(self.real), '--carbon-product-test'], capture_output=True, text=True, check=True)
        commands = json.loads(result.stdout)['inputs']
        fixture = fixtures.CarbonBuildTests()
        fixture.setUp()
        try:
            b = fixtures.b
            zip_source = fixture.carbon_archive('.zip')
            seven_source = fixture.carbon_archive('.7z')
            iso = fixture.home / 'Carbon.iso'
            iso.write_bytes(b'unknown Carbon disc layout')
            changed_dest = fixture.home / 'Changed destination'
            paths = {'/folder': fixture.carbon, '/folder/NFSC.exe': fixture.carbon / 'NFSC.exe',
                     '/game.zip': zip_source, '/game.7z': seven_source, '/disc.iso': iso,
                     '/other-destination': changed_dest, '/work': fixture.paths.work}
            before = b.digest_tree(fixture.carbon)
            hashes = [b.sha_file(path) for path in (zip_source, seven_source, iso)]
            from setup_kit import carbon_repack
            real = b.build
            def build(args, paths, events, runner, source_root=None):
                return real(args, paths, events, fixtures.CarbonRunner(fixture.log, fixture.child_env, paths.work), fixture.source)
            with fixture.pipeline_stubs(), mock.patch.object(b, 'assert_not_running'), mock.patch.object(b, 'build', build), \
                    mock.patch.object(carbon_repack, 'prepare', return_value=fixture.carbon) as decode_iso:
                for index, command in enumerate(commands):
                    fixture.stream.seek(0); fixture.stream.truncate()
                    argv = [str(paths.get(arg, arg)) for arg in command[2:]]
                    with redirect_stdout(fixture.stream):
                        rc = b.main(argv)
                    self.assertEqual(rc, 0, fixture.stream.getvalue()[-2000:])
                decode_iso.assert_called_once_with(iso, fixture.paths.work, mock.ANY)
            self.assertEqual(b.digest_tree(fixture.carbon), before)
            self.assertEqual([b.sha_file(path) for path in (zip_source, seven_source, iso)], hashes)
            self.assertFalse((fixture.paths.data / 'roots.txt').exists())
        finally:
            fixture.tearDown()

    def test_carbon_disc_folder_has_helpers_for_check_and_build(self):
        # On 7942da5b the mounted/copied fixture fails 1/1; with the fix it passes 1/1.
        with tempfile.TemporaryDirectory(dir=_CACHE) as tmp:
            root = Path(tmp)
            disc = root / "Carbon disc"
            disc.mkdir()
            (disc / "AUTORUN.INF").write_text("[Special]\nDisk=1\nProductGuiID={259C0ABB-A3B2-4D70-008F-BF7EE491B70B}\n")
            (disc / "0compressed.zip").write_bytes(b"disc archive placeholder")
            game = root / "Installed Carbon"
            (game / "TRACKS").mkdir(parents=True)
            (game / "NFSC.exe").write_bytes(b"installed game")
            for folder, helpers in ((disc, True), (game, False)):
                with self.subTest(folder=folder.name):
                    result = subprocess.run([str(self.real), "--carbon-product-test", str(folder)],
                                            capture_output=True, text=True, check=True)
                    got = json.loads(result.stdout)
                    expected = ["--pc-update", "/carbon-patch", "--pc-exe", "/NFSC.exe"] if helpers else []
                    self.assertEqual(
                        (got["helpers"], got["check"][2:], got["build"][2:]),
                        (helpers,
                         ["--check", "--game", "carbon", "--carbon", str(folder)] + expected + CARBON_TAIL,
                         ["--build", "--game", "carbon", "--carbon", str(folder)] + expected + CARBON_TAIL),
                    )

    def test_carbon_pc_picker_describes_only_pc_sources(self):
        # On 7942da5b the picker copy fails 1/1; with the fix it passes 1/1.
        picker = SWIFT.read_text().split("func choosePC()", 1)[1].split("return\n", 1)[0]
        self.assertNotIn("Xbox 360", picker)
        self.assertIn("mounted", picker)

    @unittest.skipUnless((ROOT / "tools/setup_kit/carbon_repack.py").is_file(), "public payload carries no repack reader")
    def test_carbon_iso_picker_preserves_file_and_control(self):
        # Compiled production pcRoot, with an existing ISO (the old code only
        # dropped a file's name when it existed): current passes 1/1, old 0/1.
        with tempfile.TemporaryDirectory(dir=_CACHE) as tmp:
            iso = Path(tmp) / 'source.ISO'
            iso.write_bytes(b'synthetic ISO picker input')
            def selected(binary):
                result = subprocess.run([str(binary), '--carbon-root-test', str(iso)],
                                        capture_output=True, text=True, check=True)
                return result.stdout.strip()
            self.assertEqual(selected(self.real), str(iso))
            broken = compile_app(mutate('["zip", "7z", "iso", "mdf"] :', '["zip", "7z"] :'))
            self.assertEqual(selected(broken), str(iso.parent))
        picker = SWIFT.read_text().split('    func choosePC() {', 1)[1].split('// The game folder', 1)[0]
        self.assertIn('["7z", "iso", "mdf"].compactMap { UTType(filenameExtension: $0) }', picker)
                                                                                                              
        build = BUILD.read_text()
        self.assertIn('"$sdk/LzmaDec.c" "$sdk/bridge.c"', build)
        self.assertIn('"$sdk/carbon_lzma.dylib"', build)
        print('Carbon ISO picker: current 1/1 passes; removed ISO allowance 0/1 passes')

    def test_carbon_product_words_and_hidden_extras(self):
        text = SWIFT.read_text()
        self.assertIn('"Xbox 360 files (optional)"', text)
        self.assertIn('row("Carbon Xbox 360"', text)
        # Only what Carbon reads from its console copy today: the button art and the two controller diagrams.
        benefit = text.split('? "If you own Need for Speed Carbon for Xbox 360', 1)[1].split('"', 1)[0]
        self.assertIn('button art and its two controller diagrams', benefit)
        for word in ('arrest', 'texture', 'menu art', 'world', 'audio', 'music', 'movie', 'cars'):
            self.assertNotIn(word, benefit.lower())
        # Most Wanted's extras sit behind the product; both products offer export.
        self.assertIn('if !s.carbonProduct { featureList }', text)
        self.assertEqual(text.count('Button("Manage installed extras…")'), 2)
        self.assertEqual(text.count('            if !s.carbonProduct {\n                Button("Manage installed extras…")'), 2)
        self.assertEqual(text.count('            }\n            Button(s.exportActionTitle)'), 2)
        self.assertIn('                    } else {\n                        row("Xbox 360 copy"', text)
        choose = text.split('    func chooseCarbonX360() {', 1)[1].split('\n    }\n', 1)[0]
        self.assertIn('runCheck(forX360: true)', choose, 'the console copy is validated when chosen')

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

    def test_car_protocol_split_strict_results_and_cancel(self):
        self.assertEqual(car_protocol_failures(self.real), [])

    def test_unverified_engine_message_and_original_control(self):
        source = SWIFT.read_text()
        start = source.index('            if let result = carResult, result.operation == operation, !result.verified,')
        end = source.index('            guard let result = carResult', start)
        broken = compile_app(source[:start] + source[end:])
        self.assertEqual(car_message_failures(broken), ['import', 'remove', 'restore'])
        self.assertEqual(car_message_failures(self.real), [])
        print('Engine message violations: old 3 -> new 0')

    def test_refresh_retains_plan_and_original_control(self):
        source = SWIFT.read_text()
        start = source.index('        let previous = state\n', source.index('    private func launch('))
        end = source.index('        let python = bundledPython()', start)
        original = '        state = RunState(); lines = []; stderrTail = []; splitter = LineSplitter(); cancelled = false; stopping = false\n'
        broken = source[:start] + original + source[end:]
        self.assertEqual(refresh_failures(broken), ['recognition', 'plan', 'import'])
        self.assertEqual(refresh_failures(source), [])
        print('Refresh state violations: old 3 -> new 0')

    def test_real_backend_events_decode_in_compiled_app(self):
        sys.path.insert(0, str(ROOT / 'tests/setup'))
        import test_car_setup_flow as flow
        fixture = flow.CarSetupLifecycle('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        _, events = fixture.call('--cars-check', '--carbon', str(fixture.f.carbon),
                                 '--cars-spec', str(fixture.selection), '--cars-measure')
        with tempfile.TemporaryDirectory(dir=ROOT / '.metal-cache') as tmp:
            result = run_protocol(self.real, ''.join(ev(**event) for event in events), 0, 1, 'carsCheck', tmp=tmp)
        self.assertEqual(result['outcome'], 'success')
        self.assertEqual(result['approvedCars'], [11])
        self.assertEqual(result['carSlot'], 11)
        self.assertGreater(result['carBytes'], 0)

    def test_car_protocol_original_mode_dispatch_control(self):
        # Pinned original dispatch silently treated any new mode as a check.
        original = '    let mode: RunMode = a[3] == "build" ? .build : .check'
        self.assertEqual(hashlib.sha256(original.encode()).hexdigest(),
                         'aad5baf103fe8c3da2ccc90d83aa0aad01ab414acf865e52d24017704ea45413')
        broken = compile_app(mutate('    guard let mode = RunMode(rawValue: a[3]) else { return 2 }', original))
        bad = car_protocol_failures(broken)
        self.assertGreaterEqual(len(bad), 1)
        print('Original extras dispatch: %d violated expectations' % len(bad))

    def test_extras_flow_retry_skip_cancel_and_page_order(self):
        self.assertEqual(extras_flow_failures(self.real), [])

    def test_extras_skip_busy_control(self):
        broken = compile_app(mutate('mutating func skip() { guard !busy else { return };',
                                    'mutating func skip() {'))
        self.assertIn('busySkipRefused', extras_flow_failures(broken))

    def test_extras_retry_control(self):
        broken = compile_app(mutate('busy = true; skipped = false; completed = false;',
                                    'busy = true; skipped = false;'))
        self.assertIn('retryAfterSuccess', extras_flow_failures(broken))

    def test_extras_cancel_control(self):
        broken = compile_app(mutate('case .cancelled: completed = false;', 'case .cancelled: completed = true;'))
        self.assertIn('cancelled', extras_flow_failures(broken))

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
        broken = compile_app(mutate('func forceStopAllowed(stage: String) -> Bool { stage != "install" && stage != "cars" }',
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
