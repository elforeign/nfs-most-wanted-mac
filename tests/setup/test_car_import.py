"""Standalone import lifecycle proof using generated assets and test-only policy.

Original baseline: the public engine API test fails (1 failure, 0 errors)
because no standalone transaction engine exists. No test reads repository history.
Good: 45 lifecycle/API tests; real PC/X360 selected-range dry runs write seven
outputs each without activation. Broken: --mutant-cache and --mutant-source
each fail their regression (1 failure, 0 errors). Four transaction fault classes
preserve pointer bytes and source hashes; sources edited by a fault remain at
the externally edited bytes until the harness restores them. --assets exits 3
without local inputs. Test-only approvals never enter the production package.
Parent-swap regression: vulnerable path-open copy fails 1/1 (source tree changed);
descriptor-relative copy passes 1/1, preserving all source hashes.
Retained-cache policy-revision bypass fails 1/1; checking the current pinned
policy digest and revision before cache reuse passes the same regression.
The harness runs the real packaged loader and writes the installed digest into
a copy of the catalog module. Pinning raw catalog bytes (approval hash cycle):
45 run, 3 failures, 32 errors; pinning with the digest neutral: 45 OK.
With only that fix applied, the earlier engine fails three checks (45 run,
2 failures, 1 error): a link planted at the stage 'outputs' redirects seven
real converter writes into a player folder, and an external disable or
deselection landing after the state read is undone. Fixed engine: 45 OK.
"""
import copy
from contextlib import contextmanager
import errno
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sys
import threading
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'tools'), str(Path(__file__).resolve().parent)]
from setup_kit import car_catalog as catalog, car_convert as converter, car_sources as sources
if importlib.util.find_spec('setup_kit.car_import') is not None:
    from setup_kit import car_import as ci
else:
    ci = None  # Running PublicAPI alone preserves the original failing control.
import test_car_platform_conversion as platform_fixture
import test_car_sources as source_fixture
import test_car_convert as pc_fixture


def force_remove(path):
    if not path.exists():
        return
    for base, dirs, _ in os.walk(path, followlinks=False):
        os.chmod(base, 0o700)
        for name in dirs:
            if not (Path(base) / name).is_symlink():
                os.chmod(Path(base) / name, 0o700)
    shutil.rmtree(path)


def spec(folder='SYNTH', slot=11):
    return dict(slot=slot, folder=folder, display=dict(name='Synthetic car', manufacturer='Synthetic maker'),
                bindings=dict(folder=folder, pvehicle=folder, frontend=folder + '_FE', ecar=folder + '_EC'),
                donors=dict(model='DONOR', price='DONOR'),
                parts=[dict(type=0, keys=['STOCK_BODY', 'ALTERNATE_BODY']), dict(type=4, keys=['STOCK_WHEEL'])])


def tree_hash(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


class PublicAPI(unittest.TestCase):
    def test_standalone_engine_exists(self):
        self.assertIsNotNone(importlib.util.find_spec('setup_kit.car_import'),
                             'standalone car transaction engine is missing')
        for name in ('check', 'plan', 'import_cars', 'list_cars', 'remove', 'disable', 'restore', 'reimport'):
            self.assertTrue(callable(getattr(ci.CarImporter, name, None)), name)


class Lifecycle(unittest.TestCase):
    def setUp(self):
        self.assets = platform_fixture.PlatformConversion('runTest')
        self.assets.setUp()
        self.addCleanup(self.assets.doCleanups)
        self.addCleanup(force_remove, self.assets.root)
        self.root = self.assets.root
        self.carbon = self.root / 'pc'
        self.x360 = self.root / 'x360'
        self.mw = self.root / 'mw'
        self.car = spec()
        self.alternative = copy.deepcopy(self.car)
        self.alternative['parts'][0]['keys'].reverse()
        pe, xex = source_fixture.fake_pe(), source_fixture.fake_xex()
        pc_files = {p.relative_to(self.carbon).as_posix().upper(): p.read_bytes()
                    for p in self.carbon.rglob('*') if p.is_file()}
        self.fingerprints = self.root / 'fingerprints.json'
        self.fingerprints.write_text(json.dumps(dict(schema=1, releases=[
            source_fixture.release('pc', pe, pc_files),
            source_fixture.release('x360', xex, self.assets.files)])))
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(sources, 'MANIFEST', self.fingerprints).start()
        # The real packaged loader runs. Installing a policy writes its digest
        # into a copy of the catalog module, exactly as the release step does,
        # so the converter pins see the installed bytes.
        self.installed = self.root / 'package/car_catalog.py'
        self.installed.parent.mkdir()
        self.installed.write_bytes(Path(catalog.__file__).read_bytes())
        mock.patch.object(catalog, '__file__', str(self.installed)).start()
        mock.patch.object(catalog, 'POLICY_PATH', self.root / 'test-policy.json').start()
        mock.patch.object(catalog, 'PACKAGED_POLICY_SHA256', None).start()
        self.policy = None
        self.engine = ci.CarImporter(self.root / 'catalog', self.root / 'work', margin_bytes=4096)
        self.policy_doc = dict(schema=1, revision=1,
            slots=[dict(slot=s, identity=None, variants=[]) for s in range(catalog.SLOT_COUNT)])
        # Approval injection exists only here: review generated, deterministic
        # rows, then pin canonical policy bytes through the ordinary loader.
        for source, car in ((self.carbon, self.car), (self.x360, self.car), (self.carbon, self.alternative)):
            row = self.engine.plan(source, mw_pc=self.mw, cars=[car], measure=True)['cars'][0]['contract']
            self.approve(row)
        self.save_policy()
        self.before_sources = self.snapshot()

    def approve(self, row):
        slot = self.policy_doc['slots'][row['slot']]
        slot['identity'] = row['identity']
        variant = dict(contract=catalog.contract_hash(row), compatibility=catalog.slot_compatibility(row))
        if variant not in slot['variants']:
            slot['variants'].append(variant)

    def save_policy(self):
        path = self.root / 'test-policy.json'
        path.write_bytes(catalog.encoded(self.policy_doc))
        self.policy = catalog.load_policy(path, catalog.digest(self.policy_doc))

    @property
    def policy(self):
        return self._policy

    @policy.setter
    def policy(self, value):
        self._policy = value
        catalog.PACKAGED_POLICY_SHA256 = None if value is None else value.sha256
        line = 'PACKAGED_POLICY_SHA256 = ' + ('None' if value is None else repr(value.sha256))
        text = self.installed.read_text()
        self.installed.write_text(re.sub(r'(?m)^PACKAGED_POLICY_SHA256 = .*$', line, text, count=1))

    def snapshot(self):
        return {name: tree_hash(self.root / name) for name in ('pc', 'mw', 'x360')}

    def run_import(self, source=None, car=None, **kwargs):
        return self.engine.import_cars(source or self.carbon, mw_pc=self.mw,
                                       cars=[car or self.car], **kwargs)

    def pointer(self):
        return (self.engine.root / 'current.json').read_bytes()

    def ready(self):
        result = catalog.verify_current(self.engine.root, policy=self.policy)
        self.assertFalse(result.error)
        return result

    def cache(self):
        return next(self.engine.work.glob('c-*'))

    def test_check_keeps_source_roles_and_never_approves_output(self):
        optional = self.root / 'optional-mw-x360'
        optional.mkdir()
        result = self.engine.check(self.carbon, mw_pc=self.mw, mw_x360=optional, cars=[self.car])
        self.assertEqual(result['inputs']['carbon']['platform'], 'pc')
        self.assertTrue(result['inputs']['mw_pc']['required'])
        self.assertFalse(result['inputs']['mw_x360']['used'])
        self.assertFalse(result['output_verified'])
        self.assertEqual(self.before_sources, self.snapshot())
        self.assertFalse((self.engine.root / 'current.json').exists())

    def test_unmeasured_plan_reports_unknown_without_running_writer(self):
        with mock.patch.object(converter, 'convert', side_effect=AssertionError('unexpected conversion')):
            result = self.engine.plan(self.carbon, mw_pc=self.mw, cars=[self.car])
        self.assertFalse(result['measured'])
        self.assertIsNone(result['volumes'][0]['output_bytes'])
        self.assertIsNone(result['volumes'][0]['required_bytes'])
        self.assertGreater(result['volumes'][0]['extraction_bytes'], 0)

    def test_measured_plan_has_contract_and_per_volume_receipt_without_activation(self):
        result = self.engine.plan(self.carbon, mw_pc=self.mw, cars=[self.car], measure=True)
        self.assertTrue(result['approved'])
        self.assertFalse((self.engine.root / 'current.json').exists())
        volume = result['volumes'][0]
        for key in ('extraction_bytes', 'staging_bytes', 'output_bytes', 'retention_bytes', 'margin_bytes'):
            self.assertGreater(volume[key], 0, key)
        self.assertEqual(volume['required_bytes'], sum(volume[k] for k in
            ('extraction_bytes', 'staging_bytes', 'output_bytes', 'margin_bytes')))
        receipt = result['receipt']
        self.assertGreater(receipt['seconds'], 0)
        self.assertGreater(receipt['volumes'][0]['peak_bytes'], receipt['volumes'][0]['initial_bytes'])
        self.assertFalse(receipt['measurement_errors'])

    def test_independent_import_and_identical_reimport_reuse_verified_output(self):
        with mock.patch.object(converter, 'convert', wraps=converter.convert) as writer:
            first = self.run_import()
            pointer = self.pointer()
            second = self.run_import()
        self.assertEqual(writer.call_count, 1)
        self.assertEqual((first['reused'], second['reused']), (0, 1))
        self.assertEqual(pointer, self.pointer())
        self.assertEqual(first['pointer'], second['pointer'])
        verified = self.ready()
        self.assertEqual([r['id'] for r in verified.selected], [95])
        self.assertEqual(len(verified.catalog['cars']), 90)
        self.assertEqual(self.before_sources, self.snapshot())

    def test_x360_full_entry_uses_only_selected_logical_ranges(self):
        physical = source_fixture.archive_files(source_fixture.fake_xex(), self.assets.files)
        # Unselected padding must never appear in extraction or its receipt.
        physical['NFS/UNSELECTED.BIN'] = b'not a selected logical range' * 200
        iso = self.root / 'carbon.iso'
        iso.write_bytes(source_fixture.tiny_iso(physical))
        before = catalog.fingerprint(iso)
        with mock.patch.object(converter, 'convert_x360', wraps=converter.convert_x360) as writer:
            first = self.run_import(iso)
            second = self.run_import(iso)
        self.assertEqual(writer.call_count, 1)
        self.assertEqual(second['reused'], 1)
        row = self.ready().selected[0]
        self.assertEqual(row['origin']['platform'], 'x360')
        self.assertEqual(sum(p['size'] for p in row['inputs']['source'].values()),
                         sum(len(v) for v in self.assets.files.values()))
        self.assertEqual(catalog.fingerprint(iso), before)
        self.assertEqual(first['pointer'], second['pointer'])

    def test_missing_pack_restore_needs_neither_source_nor_cache(self):
        first = self.run_import()
        row = self.ready().selected[0]
        signature = catalog.slot_compatibility(row)
        self.engine.remove([11])
        self.assertEqual(self.ready().selected, ())
        self.engine.disable()
        force_remove(self.engine.work)
        with mock.patch.object(sources, 'recognise', side_effect=AssertionError('media read during restoration')):
            with mock.patch.object(sources, '_source', side_effect=AssertionError('media read during restoration')):
                restored = self.engine.restore(first['pointer']['generation'], required={11: signature})
        self.assertEqual(restored['pointer'], first['pointer'])
        self.assertEqual([r['id'] for r in self.ready().selected], [95])
        self.assertEqual(self.before_sources, self.snapshot())

    def test_remove_and_reimport_keep_sparse_ids_and_retained_generation(self):
        first = self.run_import()
        self.engine.remove([11])
        self.assertEqual(self.ready().selected, ())
        self.assertEqual(self.ready().catalog['cars'][11]['id'], 95)
        self.assertTrue((self.engine.root / first['pointer']['generation']).is_dir())
        again = self.run_import()
        self.assertEqual(again['pointer'], first['pointer'])
        self.assertEqual(again['reused'], 1)
        listed = self.engine.list_cars()
        self.assertEqual(listed['selection'], [11])
        self.assertEqual(len(listed['generations']), 1)

    def test_restore_rejects_incompatible_or_missing_required_pack(self):
        first = self.run_import()
        self.engine.disable()
        pointer = self.pointer()
        for required in ({11: '0' * 64}, {0: '0' * 64}, {True: '0' * 64}):
            with self.subTest(required=required), self.assertRaises(ci.ImportError):
                self.engine.restore(first['pointer']['generation'], required=required)
            self.assertEqual(self.pointer(), pointer)

    def test_incompatible_approved_revision_preserves_existing_selection(self):
        self.run_import()
        pointer = self.pointer()
        with self.assertRaisesRegex(ci.ImportError, 'incompatible revision'):
            self.run_import(car=self.alternative)
        self.assertEqual(self.pointer(), pointer)
        self.assertEqual(self.before_sources, self.snapshot())

    def test_new_approval_revision_cannot_reuse_old_contract_or_pointer(self):
        self.run_import()
        pointer = self.pointer()
        old_cache = self.cache()
        old_identity = json.loads((old_cache / 'receipt.json').read_bytes())['identity']
        self.policy_doc['revision'] += 1
        self.save_policy()
        with self.assertRaises(ci.CacheError):
            self.engine._cached(old_cache, old_identity, self.policy)
        with self.assertRaisesRegex(ci.ImportError, 'current catalog is unavailable'):
            self.run_import()
        self.assertEqual(self.pointer(), pointer)
        self.engine.disable()
        with mock.patch.object(converter, 'convert', wraps=converter.convert) as writer:
            self.run_import()
        self.assertEqual(writer.call_count, 1)

    def test_unapproved_default_never_activates_or_accepts_approval_argument(self):
        self.policy = None
        with self.assertRaises(catalog.CatalogError):
            self.run_import()
        with self.assertRaises(TypeError):
            ci.CarImporter(self.root / 'other', self.root / 'other-work', policy=object())
        self.assertFalse((self.engine.root / 'current.json').exists())
        result = self.engine.plan(self.carbon, mw_pc=self.mw, cars=[self.car], measure=True)
        self.assertFalse(result['approved'])
        self.assertFalse((self.engine.root / 'current.json').exists())

    def test_cancel_at_every_publication_boundary_keeps_pointer_and_sources(self):
        self.run_import()
        self.engine.remove([11])
        pointer = self.pointer()
        for phase in ('publishing', 'inputs_verified', 'generation_verified', 'pointer_ready', 'before_publish'):
            flag = threading.Event()

            def progress(event):
                if event['phase'] == phase:
                    flag.set()

            with self.subTest(phase=phase), self.assertRaises(ci.Cancelled):
                self.run_import(cancel=flag.is_set, progress=progress)
            self.assertEqual(self.pointer(), pointer)
            self.assertEqual(self.before_sources, self.snapshot())
        self.assertEqual(self.run_import()['reused'], 1)

    def test_disk_full_during_extraction_preserves_pointer_and_sources(self):
        self.run_import(self.x360)
        pointer = self.pointer()
        original = ci._range

        def full(*args, **kwargs):
            if kwargs.get('target') is not None:
                raise OSError(errno.ENOSPC, 'synthetic disk full')
            return original(*args, **kwargs)

        with mock.patch.object(ci, '_range', side_effect=full), self.assertRaises(OSError) as failure:
            self.run_import()
        self.assertEqual(failure.exception.errno, errno.ENOSPC)
        self.assertIn('receipt', vars(failure.exception))
        self.assertEqual(self.pointer(), pointer)
        self.assertEqual(self.before_sources, self.snapshot())
        self.run_import()

    def test_source_changed_after_preparation_rejects_activation(self):
        self.run_import()
        self.engine.remove([11])
        pointer = self.pointer()
        path = self.carbon / 'CARS/SYNTH/GEOMETRY.BIN'
        data = path.read_bytes()

        def change(event):
            if event['phase'] == 'publishing':
                path.write_bytes(data + b'external edit')

        try:
            with self.assertRaisesRegex(ci.ImportError, 'source changed'):
                self.run_import(progress=change)
            self.assertEqual(path.read_bytes(), data + b'external edit')
            self.assertEqual(self.pointer(), pointer)
        finally:
            path.write_bytes(data)
        self.assertEqual(self.before_sources, self.snapshot())

    def test_failed_atomic_publication_keeps_pointer_sources_and_retry(self):
        self.run_import()
        self.engine.remove([11])
        pointer = self.pointer()
        original = catalog.os.replace

        def fail_current(src, dst, *args, **kwargs):
            if dst == 'current.json':
                raise OSError(errno.EIO, 'synthetic publication failure')
            return original(src, dst, *args, **kwargs)

        with mock.patch.object(catalog.os, 'replace', side_effect=fail_current), self.assertRaises(OSError):
            self.run_import()
        self.assertEqual(self.pointer(), pointer)
        self.assertEqual(self.before_sources, self.snapshot())
        self.run_import()

    def test_interruption_leaves_only_unselected_stage_then_retry_completes(self):
        self.run_import(self.x360)
        pointer = self.pointer()

        def interrupt(event):
            if event['phase'] == 'converted':
                raise KeyboardInterrupt('synthetic interruption')

        with self.assertRaises(KeyboardInterrupt):
            self.run_import(progress=interrupt)
        self.assertEqual(self.pointer(), pointer)
        self.assertTrue(list(self.engine.work.glob('.txn-*')))
        self.assertEqual(self.before_sources, self.snapshot())
        self.assertEqual(self.run_import()['reused'], 0)
        self.assertEqual([r['id'] for r in self.ready().selected], [95])

    def test_corrupt_cache_rejected_before_plan_or_activation(self):
        self.run_import()
        pointer = self.pointer()
        path = self.cache() / 'outputs/GEOMETRY.BIN'
        os.chmod(path, 0o600)
        path.write_bytes(path.read_bytes() + b'corrupt')
        for operation in (lambda: self.run_import(), lambda: self.engine.plan(self.carbon, mw_pc=self.mw, cars=[self.car])):
            with self.subTest(operation=operation), self.assertRaises(ci.CacheError):
                operation()
        self.assertEqual(self.pointer(), pointer)
        self.assertEqual([r['id'] for r in self.ready().selected], [95])

    def test_plan_rejects_corrupt_cache_before_any_publication(self):
        self.run_import()
        path = self.cache() / 'outputs/GEOMETRY.BIN'
        os.chmod(path, 0o600)
        path.write_bytes(b'corrupt cached output')
        with self.assertRaises(ci.CacheError):
            self.engine.plan(self.carbon, mw_pc=self.mw, cars=[self.car])

    def test_missing_or_extra_cache_input_rejected_before_activation(self):
        self.run_import()
        pointer = self.pointer()
        entry = self.cache()
        os.chmod(entry / 'converter', 0o700)
        (entry / 'converter/EXTRA.BIN').write_bytes(b'untracked dependency')
        with self.assertRaises(ci.CacheError):
            self.run_import()
        self.assertEqual(self.pointer(), pointer)

    def test_reimport_reconstructs_corrupt_cache_without_reusing_bad_output(self):
        first = self.run_import()
        self.engine.remove([11])
        path = self.cache() / 'outputs/GEOMETRY.BIN'
        os.chmod(path, 0o600)
        path.write_bytes(b'corrupt cache')
        with mock.patch.object(converter, 'convert', wraps=converter.convert) as writer:
            result = self.engine.reimport(self.carbon, mw_pc=self.mw, cars=[self.car])
        self.assertEqual(writer.call_count, 1)
        self.assertEqual(result['reused'], 0)
        self.assertEqual(result['pointer'], first['pointer'])
        rejected = next(self.engine.work.glob('.rejected-*'))
        self.assertEqual((rejected / 'outputs/GEOMETRY.BIN').read_bytes(), b'corrupt cache')
        self.assertEqual(self.before_sources, self.snapshot())

    def test_cache_repair_cancellation_keeps_current_generation_and_source(self):
        self.run_import()
        pointer = self.pointer()
        path = self.cache() / 'outputs/GEOMETRY.BIN'
        os.chmod(path, 0o600)
        path.write_bytes(b'corrupt cache')
        cancelled = threading.Event()

        def progress(event):
            if event['phase'] == 'cache_rejected':
                cancelled.set()

        with self.assertRaises(ci.Cancelled):
            self.engine.reimport(self.carbon, mw_pc=self.mw, cars=[self.car],
                                 cancel=cancelled.is_set, progress=progress)
        self.assertEqual(self.pointer(), pointer)
        self.assertEqual(self.before_sources, self.snapshot())

    def test_corrupt_cache_receipt_identity_is_rejected(self):
        self.run_import()
        pointer = self.pointer()
        path = self.cache() / 'receipt.json'
        doc = json.loads(path.read_bytes())
        doc['identity']['platform'] = 'x360'
        os.chmod(path, 0o600)
        path.write_bytes(catalog.encoded(doc))
        with self.assertRaises(ci.CacheError):
            self.run_import()
        self.assertEqual(self.pointer(), pointer)

    def test_cache_identity_requires_exact_types_and_dependency_receipts(self):
        self.run_import()
        pointer = self.pointer()
        path = self.cache() / 'receipt.json'
        original = path.read_bytes()
        for mutation in ('schema', 'identity_schema', 'source_receipt'):
            doc = json.loads(original)
            if mutation == 'schema':
                doc['schema'] = True
            elif mutation == 'identity_schema':
                doc['identity']['schema'] = True
            else:
                doc['row']['inputs']['source']['GLOBAL/ATTRIBUTES.BIN']['sha256'] = '0' * 64
            os.chmod(path, 0o600)
            path.write_bytes(catalog.encoded(doc))
            with self.subTest(mutation=mutation), self.assertRaises(ci.CacheError):
                self.run_import()
            self.assertEqual(self.pointer(), pointer)

    def test_converter_omitted_dependency_is_rejected_on_both_platforms(self):
        self.run_import()
        self.engine.remove([11])
        pointer = self.pointer()
        for platform, source in (('pc', self.carbon), ('x360', self.x360)):
            # A fresh work namespace forces the writer rather than an existing
            # preparation. The catalog and previous selection are shared.
            engine = ci.CarImporter(self.engine.root, self.root / ('untracked-' + platform))
            method = 'convert' if platform == 'pc' else 'convert_x360'
            original = getattr(converter, method)

            def omitted(*args, **kwargs):
                report = original(*args, **kwargs)
                report['inputs'].pop(next(iter(report['inputs'])))
                return report

            with self.subTest(platform=platform), mock.patch.object(converter, method, side_effect=omitted):
                with self.assertRaisesRegex(ci.ImportError, 'omitted or added'):
                    engine.import_cars(source, mw_pc=self.mw, cars=[self.car])
            self.assertEqual(self.pointer(), pointer)
            self.assertEqual(self.before_sources, self.snapshot())

    def test_changed_source_and_mw_dependencies_during_publication_are_rejected(self):
        self.run_import()
        self.engine.remove([11])
        pointer = self.pointer()
        path = self.mw / 'GLOBAL/GLOBALA.BUN'
        data = path.read_bytes()

        def change(event):
            if event['phase'] == 'before_publish':
                path.write_bytes(data + converter.chunk(0x1234, b''))

        try:
            with self.assertRaisesRegex(ci.ImportError, 'MW dependency changed'):
                self.run_import(progress=change)
            self.assertEqual(self.pointer(), pointer)
        finally:
            path.write_bytes(data)
        self.assertEqual(self.before_sources, self.snapshot())

    def test_publication_space_failure_rejects_before_activation(self):
        self.run_import()
        self.engine.remove([11])
        pointer = self.pointer()
        usage = shutil.disk_usage(self.engine.root)
        with mock.patch.object(ci.shutil, 'disk_usage', return_value=usage._replace(free=0)):
            with self.assertRaises(OSError) as failure:
                self.run_import()
        self.assertEqual(failure.exception.errno, errno.ENOSPC)
        self.assertEqual(self.pointer(), pointer)
        self.assertEqual(self.before_sources, self.snapshot())

    def test_identical_generation_needs_no_additional_payload_space(self):
        self.run_import()
        with mock.patch.object(converter, 'convert', side_effect=AssertionError('unexpected writer')):
            result = self.engine.plan(self.carbon, mw_pc=self.mw, cars=[self.car])
        self.assertEqual(result['volumes'][0]['output_bytes'], 0)
        self.assertEqual(result['volumes'][0]['extraction_bytes'], 0)
        self.assertEqual(result['volumes'][0]['staging_bytes'], 0)
        self.assertEqual(result['volumes'][0]['required_bytes'], 4096)

    def test_space_plan_keeps_separate_volume_requirements_separate(self):
        with self.engine._transaction(None) as receipt:
            receipt.volumes = {
                'work-volume': dict(initial_bytes=7, initial_available_bytes=100000),
                'catalog-volume': dict(initial_bytes=11, initial_available_bytes=100000)}
            preparations = [dict(reused=False, extraction_bytes=10, dependency_bytes=20, output_bytes=30)]

            def volume(path):
                return ('work-volume' if path == self.engine.work else 'catalog-volume'), 100000

            with mock.patch.object(ci, '_volume', side_effect=volume):
                rows = {r['device']: r for r in self.engine._space(preparations, receipt, 40)}
        work, output = rows['work-volume'], rows['catalog-volume']
        self.assertEqual((work['extraction_bytes'], work['staging_bytes'], work['output_bytes']), (10, 50, 0))
        self.assertEqual((output['extraction_bytes'], output['staging_bytes'], output['output_bytes']), (0, 0, 40))
        self.assertEqual((work['retention_bytes'], output['retention_bytes']), (7, 11))
        self.assertEqual((work['required_bytes'], output['required_bytes']), (4156, 4136))

    def test_changed_helper_version_cannot_activate_old_outputs(self):
        self.run_import()
        pointer = self.pointer()
        with mock.patch.object(converter.np, '__version__', 'synthetic-new-version'):
            with self.assertRaisesRegex(ci.ImportError, 'not approved'):
                self.run_import()
        self.assertEqual(self.pointer(), pointer)

    def test_changed_mw_dependency_cannot_reuse_old_output(self):
        self.run_import()
        pointer = self.pointer()
        path = self.mw / 'GLOBAL/GLOBALA.BUN'
        original = path.read_bytes()
        path.write_bytes(original + converter.chunk(0x1234, b''))
        with self.assertRaisesRegex(ci.ImportError, 'not approved'):
            self.run_import()
        self.assertEqual(self.pointer(), pointer)
        self.assertEqual(path.read_bytes(), original + converter.chunk(0x1234, b''))

    def test_same_inputs_at_new_location_reuse_content_cache(self):
        self.run_import()
        relocated = self.root / 'relocated'
        shutil.copytree(self.carbon, relocated)
        with mock.patch.object(converter, 'convert', side_effect=AssertionError('reconverted identical input')):
            self.assertEqual(self.run_import(relocated)['reused'], 1)

    def test_missing_assets_exit_three_and_unverified_selected_car_rejects(self):
        for source, mw, car in ((self.root / 'absent', self.mw, self.car),
                                (self.carbon, self.root / 'absent', self.car),
                                (self.carbon, self.mw, spec('MISSING'))):
            with self.subTest(source=source, mw=mw), self.assertRaises(ci.MissingAssets) as failure:
                self.engine.check(source, mw_pc=mw, cars=[car])
            self.assertEqual(failure.exception.exit_code, 3)
        recognition = sources.recognise(self.carbon)
        recognition['cars'][0]['status'] = 'unknown'
        with mock.patch.object(sources, 'recognise', return_value=recognition), self.assertRaises(ci.ImportError):
            self.run_import()

    def test_overlapping_source_roles_and_managed_directories_rejected(self):
        for mw, optional in ((self.carbon, None), (self.mw, self.carbon)):
            with self.assertRaises(ci.ImportError):
                self.engine.check(self.carbon, mw_pc=mw, mw_x360=optional, cars=[self.car])
        nested = ci.CarImporter(self.carbon / 'catalog', self.root / 'other-work')
        with self.assertRaises(ci.ImportError):
            nested.plan(self.carbon, mw_pc=self.mw, cars=[self.car])
        self.assertEqual(self.before_sources, self.snapshot())

    def test_symlink_and_hardlink_inputs_rejected(self):
        link = self.root / 'source-link'
        link.symlink_to(self.carbon, target_is_directory=True)
        with self.assertRaises((ci.ImportError, OSError)):
            self.engine.check(link, mw_pc=self.mw, cars=[self.car])
        path = self.carbon / 'CARS/SYNTH/GEOMETRY.BIN'
        os.link(path, self.root / 'second-link')
        with self.assertRaises((ci.ImportError, catalog.CatalogError)):
            self.run_import()

    def test_parent_swap_cannot_redirect_selected_extraction_into_source(self):
        original = catalog._directory
        target = self.root / 'target/UNEXPECTED.BIN'
        moved = self.root / 'original-target'
        source = self.carbon / 'GLOBAL/GLOBALB.LZC'

        @contextmanager
        def swapped(path, create=False):
            with original(path, create=create) as fd:
                if Path(path) == target.parent and create:
                    target.parent.rename(moved)
                    target.parent.symlink_to(self.carbon, target_is_directory=True)
                yield fd

        with mock.patch.object(catalog, '_directory', side_effect=swapped):
            ci._range(source, 0, source.stat().st_size, target=target)
        self.assertFalse((self.carbon / 'UNEXPECTED.BIN').exists(), 'extraction escaped into source tree')
        self.assertEqual((moved / target.name).read_bytes(), source.read_bytes())
        self.assertEqual(self.before_sources, self.snapshot())

    def test_writers_serialize_across_independent_instances(self):
        entered, release, second_started = threading.Event(), threading.Event(), threading.Event()
        errors, results = [], []

        def progress(event):
            if event['phase'] == 'converted':
                entered.set()
                if not release.wait(5):
                    raise AssertionError('test writer was not released')

        def first():
            try:
                results.append(self.run_import(progress=progress))
            except BaseException as exc:
                errors.append(exc)

        other = ci.CarImporter(self.engine.root, self.root / 'work-2', margin_bytes=4096)

        def second():
            second_started.set()
            try:
                results.append(other.import_cars(self.x360, mw_pc=self.mw, cars=[self.car]))
            except BaseException as exc:
                errors.append(exc)

        one = threading.Thread(target=first)
        two = threading.Thread(target=second)
        one.start()
        try:
            self.assertTrue(entered.wait(5))
            two.start()
            self.assertTrue(second_started.wait(5))
            self.assertFalse(other.work.exists(), 'second writer entered while first held the lock')
        finally:
            release.set()
            one.join(10)
            if two.ident is not None:
                two.join(10)
        self.assertFalse(one.is_alive() or two.is_alive())
        self.assertFalse(errors, errors)
        self.assertEqual(len(results), 2)
        self.assertEqual(len(self.engine.list_cars()['generations']), 2)
        self.assertEqual(self.before_sources, self.snapshot())

    def test_waiting_writer_can_cancel_without_entering_transaction(self):
        cancelled = threading.Event()
        result = []

        def waiter():
            try:
                self.run_import(cancel=cancelled.is_set)
            except BaseException as exc:
                result.append(exc)

        with self.engine._transaction(None):
            thread = threading.Thread(target=waiter)
            thread.start()
            cancelled.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], ci.Cancelled)
        self.assertFalse((self.engine.root / 'current.json').exists())

    def test_external_catalog_writer_is_not_overwritten(self):
        self.run_import()
        changed = []

        def progress(event):
            if event['phase'] == 'publishing':
                # Direct catalog clients have their own publication lock.
                catalog.disable(self.engine.root)
                changed.append(self.pointer())

        with self.assertRaisesRegex(ci.ImportError, 'catalog changed outside'):
            self.run_import(progress=progress)
        self.assertEqual(self.pointer(), changed[0])

    def test_linked_stage_output_cannot_redirect_writes_into_player_files(self):
        player = self.root / 'player/CARS/SYNTH'
        player.mkdir(parents=True)
        (player / 'GEOMETRY.BIN').write_bytes(b'player geometry')
        earlier = set(self.engine.work.glob('.txn-*'))

        def swap(event):
            if event['phase'] == 'extracted':
                stage, = set(self.engine.work.glob('.txn-*')) - earlier
                (stage / 'outputs').symlink_to(player, target_is_directory=True)

        with self.assertRaises((ci.ImportError, catalog.CatalogError, OSError)):
            self.run_import(progress=swap)
        self.assertEqual(sorted(p.name for p in player.iterdir()), ['GEOMETRY.BIN'])
        self.assertEqual((player / 'GEOMETRY.BIN').read_bytes(), b'player geometry')
        self.assertFalse((self.engine.root / 'current.json').exists())

    def test_installed_policy_digest_keeps_the_approved_converter_pin(self):
        self.assertIsNotNone(self.policy)
        self.assertIn(self.policy.sha256, self.installed.read_text())
        pins = ci._code('pc', [])[0].pins['car_catalog.py']
        self.policy = None
        self.assertEqual(ci._code('pc', [])[0].pins['car_catalog.py'], pins)

    def interleave(self, writer):
        """A direct catalog client writes just after the engine reads its state."""
        original, threads = self.engine._current, []

        def current(policy):
            result = original(policy)
            thread = threading.Thread(target=writer)
            thread.start()
            thread.join(1)
            threads.append(thread)
            return result
        return mock.patch.object(self.engine, '_current', side_effect=current), threads

    def test_import_cannot_undo_a_disable_that_lands_after_its_state_read(self):
        self.run_import()
        patch, threads = self.interleave(lambda: catalog.disable(self.engine.root))
        with patch, self.assertRaisesRegex(ci.ImportError, 'catalog changed outside'):
            self.run_import(self.x360)
        threads[0].join(10)
        self.assertEqual(json.loads(self.pointer()), catalog.disabled_pointer())

    def test_remove_cannot_undo_a_deselection_that_lands_after_its_state_read(self):
        self.run_import()
        patch, threads = self.interleave(
            lambda: catalog.set_selection(self.engine.root, [], policy=self.policy))
        with patch, self.assertRaisesRegex(ci.ImportError, 'catalog changed outside'):
            self.engine.remove([5])
        threads[0].join(10)
        self.assertEqual(json.loads(self.pointer())['selection'], [])

    def test_corrupt_generation_cannot_restore_but_disable_still_works(self):
        first = self.run_import()
        path = self.engine.root / first['pointer']['generation'] / 'cars/11/TEXTURES.BIN'
        os.chmod(path, 0o600)
        path.write_bytes(b'corrupt generation')
        pointer = self.pointer()
        with self.assertRaises(catalog.CatalogError):
            self.engine.restore(first['pointer']['generation'])
        self.assertEqual(self.pointer(), pointer)
        self.engine.disable()
        self.assertEqual(self.ready().selected, ())
        self.assertFalse(self.engine.list_cars()['generations'][0]['verified'])

    def test_incremental_import_merges_other_slots_and_retains_old_generation(self):
        other = spec('OTHER', 0)
        second = self.root / 'other-carbon'
        data = {'CARS/OTHER/GEOMETRY.BIN': pc_fixture.geometry('OTHER', True),
                'CARS/OTHER/TEXTURES.BIN': pc_fixture.texture('OTHER'),
                'GLOBAL/GLOBALB.LZC': (self.carbon / 'GLOBAL/GLOBALB.LZC').read_bytes(),
                'GLOBAL/ATTRIBUTES.BIN': pc_fixture.attributes('OTHER')}
        source_fixture.write_files(second, data)
        manifest = json.loads(self.fingerprints.read_bytes())
        manifest['releases'].append(source_fixture.release('pc', source_fixture.fake_pe(), data))
        self.fingerprints.write_text(json.dumps(manifest))
        for source, car in ((self.carbon, self.car), (second, other)):
            result = self.engine.plan(source, mw_pc=self.mw, cars=[car], measure=True)
            self.approve(result['cars'][0]['contract'])
        self.save_policy()
        first = self.run_import()
        self.run_import(second, other)
        self.assertEqual([r['id'] for r in self.ready().selected], [84, 95])
        self.assertEqual(len(self.engine.list_cars()['generations']), 2)
        self.engine.restore(first['pointer']['generation'])
        self.assertEqual([r['id'] for r in self.ready().selected], [95])


def asset_main():
    """Measure local inputs only; generated metadata is explicitly measurement-only.

    This never manufactures an approval. Full lifecycle approval injection is
    confined to Lifecycle above. Real plan results stay unselected even when
    the package contains no approvals. Receipts and local bytes are ignored.
    """
    values = {name: os.environ.get(name) for name in ('NFS_CARBON_PC', 'NFS_CARBON_X360', 'NFS_GAME')}
    if any(not value or not Path(value).exists() for value in values.values()):
        print('SKIP: supply existing local PC/X360/MW inputs')
        return 3
    pc, x360, mw = (Path(values[n]).expanduser().resolve() for n in values)
    checks = {platform: sources.recognise(path) for platform, path in (('pc', pc), ('x360', x360))}
    selected = os.environ.get('NFS_CARBON_CARS', '').split()
    if not selected:
        available = [{c['car_hash'] for c in check['cars'] if c['status'] == 'importable'}
                     for check in checks.values()]
        selected = [p.name.upper() for p in sorted((pc / 'CARS').iterdir()) if p.is_dir() and
                    all(sources.path_hash('CARS/' + p.name) in ids for ids in available) and
                    (mw / 'CARS' / p.name / 'GEOMETRY.BIN').is_file()][:1]
    donors = os.environ.get('NFS_CARBON_DONORS', ' '.join(selected)).split()
    if not selected or len(selected) != len(donors):
        print('SKIP: no selected local source/donor pair')
        return 3
    out = ROOT / '.metal-cache/car-import-assets'
    out.mkdir(parents=True, exist_ok=True)
    engine = ci.CarImporter(out / 'catalog', out / 'work')
    reports = []
    for folder, donor in zip(selected, donors):
        car = spec(folder.upper(), 0)
        car['display'] = dict(name=folder, manufacturer=folder)
        car['donors'] = dict(model=donor.upper(), price=donor.upper())
        car['parts'] = [dict(type=0, keys=[folder.upper() + '_MEASUREMENT'])]
        for platform, source in (('pc', pc), ('x360', x360)):
            result = engine.plan(source, mw_pc=mw, cars=[car], measure=True)
            row = result['cars'][0]['contract']
            report = dict(platform=platform, folder=folder, metadata='measurement-only',
                extraction_bytes=sum(p['size'] for p in row['inputs']['source'].values()),
                output_bytes=sum(p['size'] for p in row['outputs'].values()),
                input_files=sum(len(p) for p in row['inputs'].values()), outputs=len(row['outputs']),
                seconds=result['receipt']['seconds'], volumes=result['volumes'],
                receipt=result['receipt'], approved=result['approved'])
            reports.append(report)
            print(json.dumps({k: v for k, v in report.items() if k not in ('receipt', 'volumes')}, sort_keys=True))
            assert not (engine.root / 'current.json').exists(), 'dry run activated a pointer'
    (out / 'measurements.json').write_text(json.dumps(reports, sort_keys=True, indent=2) + '\n')
    return 0


def mutant_main(kind):
    """Deliberately broken in-memory controls, confined to this test harness."""
    if kind == 'cache':
        def unchecked(self, entry, identity, policy):
            return json.loads((entry / 'receipt.json').read_bytes())['row']
        patch = mock.patch.object(ci.CarImporter, '_cached', unchecked)
        name = 'test_plan_rejects_corrupt_cache_before_any_publication'
    else:
        original = ci.CarImporter._prepare

        def unchecked(self, *args, **kwargs):
            result = original(self, *args, **kwargs)
            result['unchanged'] = lambda: None
            return result
        patch = mock.patch.object(ci.CarImporter, '_prepare', unchecked)
        name = 'test_source_changed_after_preparation_rejects_activation'
    with patch:
        result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([Lifecycle(name)]))
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    if '--assets' in sys.argv:
        try:
            sys.exit(asset_main())
        except ci.MissingAssets as exc:
            print('SKIP: ' + str(exc))
            sys.exit(3)
    if '--mutant-cache' in sys.argv:
        sys.exit(mutant_main('cache'))
    if '--mutant-source' in sys.argv:
        sys.exit(mutant_main('source'))
    unittest.main(verbosity=2)
