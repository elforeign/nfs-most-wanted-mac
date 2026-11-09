"""Extras integration proof with generated sources only.

Picked-source regressions (violated expectations, old -> new): symlink 1 -> 0;
loose executable 1 -> 0. Pinned original carbon_input runs both controls.
Resolved protected paths and links inside the source remain refused.

Original CLI: --cars-list exits 2 (one regression error). Current: 16 checks
pass, including a real public-entry subprocess lifecycle with test-only pinned
approvals. PC and X360 ISO sources convert without compiler/build invocation;
remove/restore work with media and installed roots disconnected. Source/save
hashes and last-good pointer bytes survive failures and cancellation. Broken
source, approval, cache, progress-log and recognition controls all refuse;
a corrupt cache repairs through F-T, and post-commit log/cancel faults still
report the completed car transaction. No command emits install done/ready.
Missing optional source: original unfiltered selection FAIL (1 import error);
filtered selection PASS (1 admitted, 1 named skip); all-missing selection
preserves pointer bytes and invokes the importer zero times.
"""
import io
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
(ROOT / '.metal-cache').mkdir(exist_ok=True)
sys.path[:0] = [str(ROOT / 'tools'), str(Path(__file__).resolve().parent)]
from setup_kit import backend as b


ORIGINAL_CARBON_INPUT = '''def carbon_input(value):
    """Validate before normalization; arbitrary executable names stand for a folder."""
    car_import = car_api()
    path = car_import._path(value)
    if path.is_file() and (path.suffix.lower() == '.exe' or path.name.casefold() == 'default.xex'):
        return path.parent
    return car_sources._folder(path)
'''


ORIGINAL_CARBON_INPUT_SHA = '9a96cd22d54d30e10b4758abbc3af5e7d952e1b71008f5de5308ef127af60f13'

def original_carbon_input():
    assert hashlib.sha256(ORIGINAL_CARBON_INPUT.encode()).hexdigest() == ORIGINAL_CARBON_INPUT_SHA
    namespace = dict(vars(b))
    exec(ORIGINAL_CARBON_INPUT, namespace)
    return lambda value, **kwargs: namespace['carbon_input'](value)


class StandaloneCLI(unittest.TestCase):
    def test_list_without_build_arguments(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.metal-cache') as td:
            out = io.StringIO()
            with mock.patch.dict(os.environ, {'NFSMW_SETUP_HOME': td}), redirect_stdout(out), \
                    mock.patch.object(b, 'ROOT', Path(td) / 'payload'), \
                    mock.patch.object(b, 'build', side_effect=AssertionError('build invoked')), \
                    mock.patch.object(b, 'preflight', side_effect=AssertionError('compiler invoked')):
                rc = b.main(['--cars-list', '--events'])
            self.assertEqual(rc, 0, out.getvalue())
            events = [json.loads(l) for l in out.getvalue().splitlines()]
            self.assertEqual(events[-1]['ev'], 'cars-result')
            self.assertEqual(events[-1]['operation'], 'list')
            self.assertFalse(any(e['ev'] == 'done' for e in events))


def validate_event(test, event):
    """Check emitted events against the packaged strict wire schema."""
    import re
    schema = json.loads((ROOT / 'tools/setup_kit/protocol.json').read_text())
    shape = next(row for row in schema['oneOf'] if row['properties']['ev']['const'] == event['ev'])
    def check(value, rule):
        if '$ref' in rule:
            return check(value, schema['$defs'][rule['$ref'].split('/')[-1]])
        if 'const' in rule:
            test.assertEqual(value, rule['const'])
        if 'enum' in rule:
            test.assertIn(value, rule['enum'])
        types = rule.get('type', [])
        types = types if isinstance(types, list) else [types]
        actual = ('null' if value is None else 'boolean' if type(value) is bool else
                  'integer' if type(value) is int else 'string' if type(value) is str else
                  'array' if type(value) is list else 'object')
        if types:
            test.assertIn(actual, types, (value, rule))
        if actual == 'object' and 'properties' in rule:
            test.assertEqual(set(value), set(rule['required']))
            for key, item in value.items():
                check(item, rule['properties'][key])
        if actual == 'array':
            for item in value:
                check(item, rule['items'])
        if actual == 'integer':
            test.assertGreaterEqual(value, rule.get('minimum', value))
            test.assertLessEqual(value, rule.get('maximum', value))
        if actual == 'string' and 'pattern' in rule:
            test.assertIsNotNone(re.fullmatch(rule['pattern'], value))
    check(event, shape)


class CarSetupLifecycle(unittest.TestCase):
    def setUp(self):
        import test_car_import as fixtures
        self.fixtures = fixtures
        self.f = fixtures.Lifecycle('runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.root = self.f.root
        self.base = self.root / 'setup-home'
        self.data = self.base / 'data'
        self.data.mkdir(parents=True)
        (self.data / 'roots.txt').write_text('pc_root=' + str(self.f.mw) + '\n')
        save = self.data / b.SAVE_DIR / 'profile/career.bin'
        save.parent.mkdir(parents=True)
        save.write_bytes(b'unchanged synthetic career')
        legacy = self.base / 'legacy/Documents' / b.SAVE_DIR / 'profile/career.bin'
        legacy.parent.mkdir(parents=True)
        legacy.write_bytes(b'unchanged synthetic legacy career')
        self.before_saves = {str(p): p.read_bytes() for p in (save, legacy)}
        self.selection = self.root / 'selection.json'
        self.selection.write_text(json.dumps([self.f.car]))
        self.env = dict(os.environ, NFSMW_SETUP_HOME=str(self.base))
        for key in ('DATA', 'STATE', 'CACHE', 'LOGS', 'LEGACY'):
            self.env.pop('NFSMW_SETUP_' + key, None)
        self.patch = mock.patch.dict(os.environ, self.env, clear=True)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.root_patch = mock.patch.object(b, 'ROOT', self.root / 'payload')
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.pointer = self.data / 'car-catalog/current.json'

    def call(self, *args, success=True):
        out = io.StringIO()
        with redirect_stdout(out), \
                mock.patch.object(b, 'build', side_effect=AssertionError('build invoked')), \
                mock.patch.object(b, 'preflight', side_effect=AssertionError('compiler preflight invoked')), \
                mock.patch.object(b, 'Runner', side_effect=AssertionError('build runner invoked')):
            rc = b.main([*args, '--events'])
        events = [json.loads(line) for line in out.getvalue().splitlines()]
        for event in events:
            validate_event(self, event)
        self.assertFalse(any(e['ev'] in ('done', 'ready') for e in events), events)
        if success:
            self.assertEqual(rc, 0, events)
            self.assertEqual(events[-1]['ev'], 'cars-result', events)
        else:
            self.assertNotEqual(rc, 0, events)
            self.assertEqual(events[-1]['ev'], 'error', events)
            self.assertFalse(any(e['ev'] == 'cars-result' for e in events))
        for name, data in self.before_saves.items():
            self.assertEqual(Path(name).read_bytes(), data)
        return rc, events

    def import_args(self, source=None):
        return ['--cars-import', '--carbon', str(source or self.f.carbon), '--cars-spec', str(self.selection)]

    def test_missing_selected_source_is_optional(self):
        import copy
        from setup_kit import car_selection, car_import
        missing = copy.deepcopy(self.f.car)
        missing.update(slot=12, folder='MISSING', display=dict(name='Missing car', manufacturer='Synthetic'))
        self.selection.write_text(json.dumps([self.f.car, missing]))
        with mock.patch.object(car_selection, 'available_specs', side_effect=lambda specs, _: (specs, [])):
            rc, _ = self.call(*self.import_args(), success=False)
        self.assertNotEqual(rc, 0)
        _, events = self.call(*self.import_args())
        self.assertEqual(events[-1]['selection'], [11])
        reasons = [e['msg'] for e in events if e['ev'] == 'warn']
        self.assertEqual(reasons, ['Missing car: missing from this Carbon copy; skipped.'])
        before = self.pointer.read_bytes()
        self.selection.write_text(json.dumps([missing]))
        with mock.patch.object(car_import.CarImporter, 'import_cars', side_effect=AssertionError('empty import')):
            _, events = self.call(*self.import_args())
        self.assertEqual(events[-1]['selection'], [11])
        self.assertEqual(self.pointer.read_bytes(), before)

    def test_picked_symlink_resolves_before_protection(self):
        alias = self.root / 'picked-source'
        alias.symlink_to(self.f.carbon, target_is_directory=True)
        with mock.patch.object(b, 'carbon_input', original_carbon_input()):
            with self.assertRaises(AssertionError):
                self.call('--cars-check', '--carbon', str(alias))
        self.call('--cars-check', '--carbon', str(alias))
        self.assertEqual(self.f.snapshot(), self.f.before_sources)
        paths = b.Paths(self.base / 'work')
        paths.cache.mkdir(parents=True, exist_ok=True)
        alias.unlink()
        alias.symlink_to(paths.cache, target_is_directory=True)
        _, events = self.call('--cars-check', '--carbon', str(alias), success=False)
        self.assertIn('protected path', events[-1]['msg'])
        alias.unlink()
        alias.symlink_to(self.f.carbon, target_is_directory=True)
        (self.f.carbon / 'escape').symlink_to(self.f.mw, target_is_directory=True)
        self.call('--cars-check', '--carbon', str(alias), success=False)
        print('Picked symlink: old 1 -> new 0; protected target and interior link refused')

    def test_loose_executable_requests_folder_without_scanning(self):
        downloads = self.root / 'Downloads'
        downloads.mkdir()
        exe = downloads / 'anything.exe'
        exe.write_bytes(b'not executed')
        unrelated = downloads / 'private.txt'
        unrelated.write_bytes(b'unrelated file must stay untouched')
        before = self.fixtures.tree_hash(downloads)
        def check():
            with mock.patch.object(b.car_sources, 'recognise', wraps=b.car_sources.recognise) as scan:
                _, events = self.call('--cars-check', '--carbon', str(exe), success=False)
            self.assertEqual((scan.call_count, 'game folder' in events[-1]['msg']), (0, True))
        with mock.patch.object(b, 'carbon_input', original_carbon_input()):
            with self.assertRaises(AssertionError):
                check()
        check()
        self.assertEqual(self.fixtures.tree_hash(downloads), before)
        self.assertFalse(self.pointer.exists())
        print('Loose executable: old 1 -> new 0; no recognition scan, source hashes unchanged')

    def test_pc_lifecycle_installed_roots_and_saves(self):
        _, events = self.call('--cars-check', '--carbon', str(self.f.carbon), '--cars-spec', str(self.selection))
        recognition = next(e for e in events if e['ev'] == 'cars-recognition')
        self.assertTrue(recognition['approval_available'])
        self.assertFalse(recognition['output_verified'])
        plan = next(e for e in events if e['ev'] == 'cars-plan')
        self.assertFalse(plan['measured'])
        self.assertFalse(plan['approved'])
        self.assertTrue(any(v['required_bytes'] is None for v in plan['volumes']))
        self.assertFalse(self.pointer.exists())
        _, imported = self.call(*self.import_args())
        pointer = self.pointer.read_bytes()
        generation = imported[-1]['generation']
        self.assertEqual(imported[-1]['selection'], [11])
        phases = [e['phase'] for e in imported if e['ev'] == 'cars-progress']
        self.assertIn('extracted', phases)
        self.assertIn('converted', phases)
        self.assertIn('prepared', phases)
        self.assertIn('verification', phases)
        self.assertTrue(any(e.get('slot') == 11 and e.get('phase') == 'converted' for e in imported))
        extraction = next(e for e in imported if e.get('phase') == 'extracted')
        self.assertGreater(extraction['done'], 0)
        self.assertEqual(extraction['done'], extraction['total'])
        _, listed = self.call('--cars-list')
        self.assertTrue(listed[-1]['verified'])
        self.assertEqual([c['name'] for c in listed[-1]['cars'] if c['selected']], ['Synthetic car'])
        self.call('--cars-remove', '11')
        self.assertEqual(json.loads(self.pointer.read_bytes())['selection'], [])
        self.call('--cars-restore', generation)
        self.assertEqual(self.pointer.read_bytes(), pointer)
        self.assertEqual(self.f.snapshot(), self.f.before_sources)

    def test_x360_iso_lifecycle(self):
        import test_car_sources as sf
        iso = self.root / 'carbon.iso'
        iso.write_bytes(sf.tiny_iso(sf.archive_files(sf.fake_xex(), self.f.assets.files)))
        _, events = self.call('--cars-check', '--carbon', str(iso))
        self.assertEqual(next(e for e in events if e['ev'] == 'cars-recognition')['platform'], 'x360')
        self.call(*self.import_args(iso))
        _, listed = self.call('--cars-list')
        self.assertEqual(listed[-1]['selection'], [11])
        self.assertEqual(self.f.snapshot(), self.f.before_sources)

    def test_measured_check_never_activates_and_retry_reuses_cache(self):
        _, measured = self.call('--cars-check', '--carbon', str(self.f.carbon), '--cars-spec', str(self.selection), '--cars-measure')
        plan = next(e for e in measured if e['ev'] == 'cars-plan')
        self.assertTrue(plan['measured'])
        self.assertTrue(plan['approved'])
        self.assertTrue(all(type(v['required_bytes']) is int for v in plan['volumes']))
        self.assertFalse(self.pointer.exists())
        self.call(*self.import_args())
        pointer = self.pointer.read_bytes()
        with mock.patch.object(b.car_api().CarImporter, '_convert', side_effect=AssertionError('verified cache not reused')):
            self.call(*self.import_args())
        self.assertEqual(self.pointer.read_bytes(), pointer)

    def test_failed_optional_import_preserves_pointer_and_no_install_success(self):
        self.call(*self.import_args())
        pointer = self.pointer.read_bytes()
        source = self.f.carbon / 'CARS/SYNTH/GEOMETRY.BIN'
        source.write_bytes(source.read_bytes() + b'changed')
        self.call(*self.import_args(), success=False)
        self.assertEqual(self.pointer.read_bytes(), pointer)

    def test_cancel_before_activation_preserves_pointer_and_retry(self):
        import signal
        self.call(*self.import_args())
        pointer = self.pointer.read_bytes()
        original = b.car_api().CarImporter._boundary
        def interrupt(engine, phase, receipt, cancel, progress):
            if phase == 'before_publish':
                os.kill(os.getpid(), signal.SIGTERM)
            return original(engine, phase, receipt, cancel, progress)
        with mock.patch.object(b.car_api().CarImporter, '_boundary', interrupt):
            rc, _ = self.call('--cars-remove', '11', success=False)
        self.assertEqual(rc, 130)
        self.assertEqual(self.pointer.read_bytes(), pointer)
        self.call('--cars-remove', '11')
        self.assertEqual(json.loads(self.pointer.read_bytes())['selection'], [])

    def test_cancel_during_recognition_never_activates(self):
        import signal
        def interrupt(*args, **kwargs):
            os.kill(os.getpid(), signal.SIGTERM)
            raise AssertionError('recognition did not stop')
        with mock.patch.object(b.car_sources, 'recognise', interrupt):
            rc, _ = self.call('--cars-check', '--carbon', str(self.f.carbon), success=False)
        self.assertEqual(rc, 130)
        self.assertFalse(self.pointer.exists())

    def test_late_cancel_reports_completed_car_transaction(self):
        import signal
        original = b.car_api().CarImporter.import_cars
        def interrupt(engine, *args, **kwargs):
            result = original(engine, *args, **kwargs)
            os.kill(os.getpid(), signal.SIGTERM)
            return result
        with mock.patch.object(b.car_api().CarImporter, 'import_cars', interrupt):
            _, events = self.call(*self.import_args())
        self.assertEqual(events[-1]['operation'], 'import')
        self.assertTrue(self.pointer.exists())

    def test_unapproved_sources_are_recognized_but_refused(self):
        self.f.policy = None
        _, events = self.call('--cars-check', '--carbon', str(self.f.carbon))
        recognition = next(e for e in events if e['ev'] == 'cars-recognition')
        self.assertFalse(recognition['approval_available'])
        self.assertTrue(any(c['status'] == 'importable' for c in recognition['cars']))
        self.call(*self.import_args(), success=False)
        self.assertFalse(self.pointer.exists())

    def test_repair_retry_rebuilds_rejected_preparation(self):
        self.call(*self.import_args())
        pointer = self.pointer.read_bytes()
        entry = next((self.base / 'cache/cars').glob('car-import-*/c-*'))
        damaged = entry / 'outputs/GEOMETRY.BIN'
        damaged.chmod(0o644)
        damaged.write_bytes(b'corrupt retained cache')
        self.call(*self.import_args(), success=False)
        self.assertEqual(self.pointer.read_bytes(), pointer)
        self.call(*self.import_args(), '--cars-repair')
        self.assertEqual(self.pointer.read_bytes(), pointer)

    def test_optional_preparation_keeps_custom_cache_usable_by_build(self):
        cache = self.root / 'custom-cache'
        with mock.patch.dict(os.environ, {'NFSMW_SETUP_CACHE': str(cache)}):
            self.call(*self.import_args())
            paths = b.Paths(self.base / 'work')
            b.claim(paths.cache, False, b.SETUP_CACHE_NAMES, 'cache', paths.state)
            self.assertTrue(b.owned(paths.cache, paths.state))
            self.assertTrue((paths.cache / 'cars').is_dir())
        self.assertTrue(self.pointer.exists())

    def test_management_needs_neither_sources_nor_installed_pc_roots(self):
        _, events = self.call(*self.import_args())
        pointer = self.pointer.read_bytes()
        (self.data / 'roots.txt').unlink()
        self.f.carbon.rename(self.root / 'carbon-offline')
        self.f.mw.rename(self.root / 'mw-offline')
        self.call('--cars-list')
        self.call('--cars-remove', '11')
        self.call('--cars-restore', events[-1]['generation'])
        self.assertEqual(self.pointer.read_bytes(), pointer)

    def test_unavailable_catalog_can_be_listed_and_restored(self):
        _, events = self.call(*self.import_args())
        pointer = self.pointer.read_bytes()
        self.pointer.chmod(0o644)
        self.pointer.write_bytes(b'{broken')
        _, listed = self.call('--cars-list')
        self.assertFalse(listed[-1]['verified'])
        self.assertTrue(listed[-1]['message'])
        self.assertEqual(self.pointer.read_bytes(), b'{broken')
        self.call('--cars-restore', events[-1]['generation'])
        self.assertEqual(self.pointer.read_bytes(), pointer)

    def test_log_full_after_commit_still_reports_completed_import(self):
        from contextlib import contextmanager
        class FullLog(io.BytesIO):
            def write(log, data):
                if data.startswith(b'{"pointer"') or b'"ev":"cars-result"' in data:
                    raise OSError(28, 'synthetic full log')
                return super().write(data)
        @contextmanager
        def full_log(directory):
            yield directory / 'full.log', FullLog()
        with mock.patch.object(b, 'car_log', full_log):
            _, events = self.call(*self.import_args())
        self.assertEqual(events[-1]['operation'], 'import')
        self.assertEqual(json.loads(self.pointer.read_bytes())['selection'], [11])

    def test_progress_failure_before_commit_retains_last_good(self):
        self.call(*self.import_args())
        pointer = self.pointer.read_bytes()
        original = b.Events.emit
        def full_log(events, event, **fields):
            if event == 'cars-progress':
                raise OSError(28, 'synthetic full log')
            return original(events, event, **fields)
        with mock.patch.object(b.Events, 'emit', full_log):
            self.call('--cars-remove', '11', success=False)
        self.assertEqual(self.pointer.read_bytes(), pointer)

    def test_public_entry_subprocess_lifecycle(self):
        # Test-only approval pin and fingerprints feed the ordinary packaged
        # loader. No public option accepts approval injection.
        harness = self.root / 'entry.py'
        harness.write_text("import sys, runpy\nfrom pathlib import Path\nsys.path.insert(0, " + repr(str(ROOT / 'tools')) + ")\n"
            "from setup_kit import backend as b, car_catalog as c, car_sources as s\n"
            "b.ROOT = Path(" + repr(str(self.root / 'payload')) + ")\n"
            "c.__file__ = " + repr(str(self.f.installed)) + "\n"
            "c.POLICY_PATH = Path(" + repr(str(self.root / 'test-policy.json')) + ")\n"
            "c.PACKAGED_POLICY_SHA256 = " + repr(self.f.policy.sha256) + "\n"
            "s.MANIFEST = Path(" + repr(str(self.f.fingerprints)) + ")\n"
            "def forbidden(*a, **kw): raise AssertionError('build or compiler invoked')\n"
            "b.build = b.preflight = b.Runner = forbidden\n"
            "runpy.run_path(" + repr(str(ROOT / 'tools/setup.py')) + ", run_name='__main__')\n")
        def invoke(*args):
            result = subprocess.run([sys.executable, '-I', str(harness), *args, '--events'],
                                    capture_output=True, text=True, env=self.env, timeout=40)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr[-1000:])
            events = [json.loads(line) for line in result.stdout.splitlines()]
            for event in events:
                validate_event(self, event)
            self.assertEqual(events[-1]['ev'], 'cars-result')
            self.assertFalse(any(e['ev'] == 'done' for e in events))
            return events[-1]
        invoke('--cars-check', '--carbon', str(self.f.carbon))
        imported = invoke(*self.import_args())
        invoke('--cars-list')
        invoke('--cars-remove', '11')
        invoke('--cars-restore', imported['generation'])
        self.assertEqual(self.f.snapshot(), self.f.before_sources)
        for name, data in self.before_saves.items():
            self.assertEqual(Path(name).read_bytes(), data)


if __name__ == '__main__':
    unittest.main(verbosity=2)
