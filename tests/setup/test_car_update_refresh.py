"""Offline update: good 1/1 refreshed, refresh removed 0/1 (fails).

Cache-deletion control: cache-only importer fails 0/1; retained/installed merge
passes 2/2 (legacy and current installs). Damaged-catalog Retry: old importer
fails 0/1 before repair; repaired importer passes 1/1, retaining the damaged bytes.
Controls: --without-retention and --without-repair on the respective regressions.
The old Setup leaves a verified cached preparation. The update migrates it,
prepares the new performance bytes, and keeps career compatibility.
"""
from contextlib import nullcontext, redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_apply
import test_car_import as car_fixture
from setup_kit import apply, backend, car_catalog as catalog, car_import as ci, car_performance, install, stage

BROKEN = '--without-refresh' in sys.argv
if BROKEN:
    sys.argv.remove('--without-refresh')

WITHOUT_RETENTION = '--without-retention' in sys.argv
if WITHOUT_RETENTION:
    sys.argv.remove('--without-retention')

WITHOUT_REPAIR = '--without-repair' in sys.argv
if WITHOUT_REPAIR:
    sys.argv.remove('--without-repair')


class CarUpdateRefresh(unittest.TestCase):
    def setUp(self):
        mock.patch.object(car_performance, 'metadata', return_value=b'old performance').start()
        self.addCleanup(mock.patch.stopall)
        self.assets = car_fixture.Lifecycle('runTest')
        self.assets.setUp()
        self.assets.policy_doc['revision'] = 12
        self.assets.save_policy()
        self.addCleanup(self.assets.doCleanups)
        temp = tempfile.TemporaryDirectory(dir=self.assets.root)
        self.addCleanup(temp.cleanup)
        self.fixture = test_apply.Fixture(temp.name)
        self.root = self.fixture.profile / 'car-catalog'
        self.cache = self.assets.root / 'work'
        self.assets.engine = ci.CarImporter(self.root, self.cache, margin_bytes=4096)
        mock.patch.object(install, 'codesign_ok', return_value=True).start()

    def install_previous(self, engine, source=None):
        engine.import_cars(source or self.assets.carbon, mw_pc=self.assets.mw, cars=[self.assets.car])
        return self.assets.ready()

    def new_release(self, source=None, *, new_mw_dependency=False):
        # A release changes the performance tool and its approved output.
        # Change the executable pin in a test copy while running the shared
        # converter with the fixture's independent performance bytes.
        module = self.assets.root / 'package/car_performance.py'
        module.write_bytes(Path(car_performance.__file__).read_bytes() + b'\n# fixture v2\n')
        mock.patch.object(car_performance, '__file__', str(module)).start()
        if new_mw_dependency:
            mock.patch.dict(car_performance.SIBLINGS, {self.assets.car['folder']: 'DONOR'}).start()
            mock.patch.object(car_performance, 'sibling_record', return_value=b'new performance').start()
            test_apply.put(self.assets.mw / 'GLOBAL/attributes.bin', b'fixture dependency')
            test_apply.put(self.fixture.job / 'data/pc/GLOBAL/attributes.bin', b'fixture dependency')
        else:
            mock.patch.object(car_performance, 'metadata', return_value=b'new performance').start()
        old_policy = self.assets.policy
        self.assets.policy_doc['revision'] += 1
        mock.patch.object(catalog, 'PRE_SIBLING_PERFORMANCE_POLICY_SHA256', old_policy.sha256).start()
        contract = self.assets.engine.plan(source or self.assets.carbon, mw_pc=self.assets.mw, cars=[self.assets.car],
                                           measure=True)['cars'][0]['contract']
        self.assets.approve(contract)
        self.assets.save_policy()
        self.assertIn('PERFORMANCE.BIN', contract['outputs'])
        return contract

    def prepare_update(self):
        f = self.fixture
        data = f.job / 'data'
        before = self.assets.pointer()
        warning = io.StringIO()
        refresh = mock.patch.object(stage, 'prepare_car_refresh', return_value=None) if BROKEN else nullcontext()
        with redirect_stderr(warning), refresh:
            ready = backend.publish_mw_update(f.base / 'staged', f.job / f.bundle, data,
                                              {'pc_root': data / 'pc', 'assets_root': data / 'assets'},
                                              f.dest, f.profile, f.version, {'fixture': True}, cache=self.cache)
        self.assertEqual(self.assets.pointer(), before)
        return ready, warning.getvalue()

    def apply_update(self, ready, *, acknowledge=True):
        f = self.fixture
        with redirect_stdout(io.StringIO()):
            token = apply.apply_ready(ready, f.profile, f.dest, running=lambda: None)
            if acknowledge:
                f.receipt(token)
                apply.acknowledge(f.profile, token)
        f.consistent('new')
        return self.assets.ready()

    def assert_refresh(self, previous, expected, after):
        self.assertEqual(after.compatibility, previous.compatibility)
        old, new = previous.selected[0], after.selected[0]
        self.assertNotEqual(old['outputs']['PERFORMANCE.BIN'], expected['outputs']['PERFORMANCE.BIN'])
        for name in old['outputs'].keys() - {'PERFORMANCE.BIN'}:
            self.assertEqual(new['outputs'][name], old['outputs'][name])
        self.assertEqual(catalog.slot_compatibility(new), catalog.slot_compatibility(old))
        refreshed = sum(row['outputs'].get('PERFORMANCE.BIN') == expected['outputs']['PERFORMANCE.BIN']
                        for row in after.selected)
        print(f'{refreshed}/1 refreshed')
        self.assertEqual(refreshed, 1, f'{refreshed}/1 refreshed')

    def test_update_refreshes_previous_import(self):
        previous = self.install_previous(self.assets.engine)
        expected = self.new_release(new_mw_dependency=True)
        ready, warning = self.prepare_update()
        self.assertEqual(warning, '')
        self.assert_refresh(previous, expected, self.apply_update(ready))
        self.assertTrue(any((self.fixture.profile / stage.CAR_INPUTS).glob('car-import-*/c-*')))

    def test_update_without_cache_or_original_media(self):
        previous = self.install_previous(stage.car_importer(self.fixture.profile, self.cache))
        expected = self.new_release()
        car_fixture.force_remove(self.cache)
        shutil.rmtree(self.assets.carbon)
        ready, warning = self.prepare_update()
        self.assertEqual(warning, '')
        self.assert_refresh(previous, expected, self.apply_update(ready))
        self.assertFalse(self.cache.exists())

    def test_x360_update_without_original_media(self):
        previous = self.install_previous(stage.car_importer(self.fixture.profile, self.cache), self.assets.x360)
        expected = self.new_release(self.assets.x360)
        shutil.rmtree(self.assets.x360)
        ready, warning = self.prepare_update()
        self.assertEqual(warning, '')
        self.assert_refresh(previous, expected, self.apply_update(ready))

    def test_failed_conversion_keeps_old_active_and_updates_app(self):
        previous = self.install_previous(self.assets.engine)
        self.new_release()
        with mock.patch.object(ci.CarImporter, '_convert', side_effect=RuntimeError('fixture conversion failure')) as convert:
            ready, warning = self.prepare_update()
        convert.assert_called_once()
        self.assertEqual(warning.splitlines(), [stage.CAR_REFRESH_KEPT])
        self.assertEqual(self.apply_update(ready), previous)

    def test_unacknowledged_update_restores_previous_pointer_with_app(self):
        previous = self.install_previous(self.assets.engine)
        before = self.assets.pointer()
        expected = self.new_release()
        ready, warning = self.prepare_update()
        self.assertEqual(warning, '')
        self.assert_refresh(previous, expected, self.apply_update(ready, acknowledge=False))
        self.assertEqual(self.fixture.recover(), 'restored')
        self.fixture.consistent('old')
        self.assertEqual(self.assets.pointer(), before)

    def test_selection_change_since_preparation_is_preserved(self):
        previous = self.install_previous(self.assets.engine)
        self.new_release()
        ready, warning = self.prepare_update()
        self.assertEqual(warning, '')
        self.assets.engine.remove([self.assets.car['slot']])
        log = io.StringIO()
        with redirect_stderr(log):
            result = self.apply_update(ready)
        self.assertEqual(log.getvalue().splitlines(), [stage.CAR_REFRESH_KEPT])
        self.assertEqual(result.generation, previous.generation)
        self.assertEqual(result.selected, ())

    def test_unchanged_release_does_not_stage_generation(self):
        previous = self.install_previous(self.assets.engine)
        before = self.assets.pointer()
        with mock.patch.object(ci.CarImporter, '_convert', side_effect=AssertionError('unexpected conversion')) as convert:
            ready, warning = self.prepare_update()
        convert.assert_not_called()
        self.assertEqual(warning, '')
        self.assertNotIn('cars', json.loads(ready.read_bytes()))
        self.assertEqual(self.apply_update(ready), previous)
        self.assertEqual(self.assets.pointer(), before)

    def test_tool_change_refreshes_with_same_approval_policy(self):
        module = self.assets.root / 'package/car_performance.py'
        module.write_bytes(Path(car_performance.__file__).read_bytes() + b'\n# fixture v2\n')

        def new_tools():
            return mock.patch.multiple(car_performance, __file__=str(module),
                                       metadata=mock.Mock(return_value=b'new performance'))

        with new_tools():
            contract = self.assets.engine.plan(self.assets.carbon, mw_pc=self.assets.mw, cars=[self.assets.car],
                                               measure=True)['cars'][0]['contract']
        self.assets.approve(contract)
        self.assets.save_policy()
        previous = self.install_previous(self.assets.engine)
        with new_tools():
            ready, warning = self.prepare_update()
        self.assertEqual(warning, '')
        updated = self.apply_update(ready)
        self.assertEqual(updated.catalog['policy_sha256'], previous.catalog['policy_sha256'])
        self.assert_refresh(previous, contract, updated)

    def test_add_pack_after_legacy_cache_was_deleted(self):
        self.add_pack_after_cache_deletion(retained=False)

    def test_add_pack_after_current_cache_was_deleted(self):
        self.add_pack_after_cache_deletion(retained=True)

    def add_pack_after_cache_deletion(self, *, retained):
        other = car_fixture.spec('OTHER', 0)
        second = self.assets.root / 'other-carbon'
        files = {'CARS/OTHER/GEOMETRY.BIN': car_fixture.pc_fixture.geometry('OTHER', True),
                 'CARS/OTHER/TEXTURES.BIN': car_fixture.pc_fixture.texture('OTHER'),
                 'GLOBAL/GLOBALB.LZC': (self.assets.carbon / 'GLOBAL/GLOBALB.LZC').read_bytes(),
                 'GLOBAL/ATTRIBUTES.BIN': car_fixture.pc_fixture.attributes('OTHER')}
        car_fixture.source_fixture.write_files(second, files)
        manifest = json.loads(self.assets.fingerprints.read_bytes())
        manifest['releases'].append(car_fixture.source_fixture.release('pc', car_fixture.source_fixture.fake_pe(), files))
        self.assets.fingerprints.write_text(json.dumps(manifest))
        for source, car in ((self.assets.carbon, self.assets.car), (second, other)):
            contract = self.assets.engine.plan(source, mw_pc=self.assets.mw, cars=[car], measure=True)
            self.assets.approve(contract['cars'][0]['contract'])
        self.assets.save_policy()
        initial = (stage.car_importer(self.fixture.profile, self.cache, mutating=True)
                   if retained else self.assets.engine)
        previous = self.install_previous(initial)
        installed = car_fixture.tree_hash(self.root / previous.generation)
        car_fixture.force_remove(self.cache)
        shutil.rmtree(self.assets.carbon)
        engine = (ci.CarImporter(self.root, self.cache) if WITHOUT_RETENTION else
                  stage.car_importer(self.fixture.profile, self.cache, mutating=True))
        engine.import_cars(second, mw_pc=self.assets.mw, cars=[other])
        merged = self.assets.ready()
        self.assertEqual([row['slot'] for row in merged.selected], [other['slot'], self.assets.car['slot']])
        self.assertEqual(merged.selected[1], previous.selected[0])
        self.assertEqual(car_fixture.tree_hash(self.root / previous.generation), installed)
        self.assertFalse(self.cache.exists())
        return second, other, previous, merged

    def test_retry_repairs_corrupt_active_catalog(self):
        engine = stage.car_importer(self.fixture.profile, self.cache, mutating=True)
        self.install_previous(engine)
        previous = self.assets.ready()
        generation = self.root / previous.generation
        path = generation / 'catalog.json'
        path.chmod(0o644)
        path.write_bytes(b'broken catalog')
        self.assertTrue(catalog.verify_current(self.root, policy=self.assets.policy).error)
        retry = engine.import_cars if WITHOUT_REPAIR else engine.reimport
        retry(self.assets.carbon, mw_pc=self.assets.mw, cars=[self.assets.car])
        self.assertEqual(self.assets.ready(), previous)
        damaged = list(self.root.glob('.damaged-*'))
        self.assertEqual(len(damaged), 1)
        self.assertEqual((damaged[0] / 'catalog.json').read_bytes(), b'broken catalog')

    def test_retry_restores_verified_generation_and_merges_without_old_inputs(self):
        source, other, previous, merged = self.add_pack_after_cache_deletion(retained=False)
        installed = car_fixture.tree_hash(self.root / previous.generation)
        path = self.root / merged.generation / 'catalog.json'
        path.chmod(0o644)
        path.write_bytes(b'broken catalog')
        car_fixture.force_remove(self.fixture.profile / stage.CAR_INPUTS)
        engine = stage.car_importer(self.fixture.profile, self.cache, mutating=True)
        engine.reimport(source, mw_pc=self.assets.mw, cars=[other])
        self.assertEqual(self.assets.ready(), merged)
        self.assertEqual(car_fixture.tree_hash(self.root / previous.generation), installed)
        self.assertEqual(len(list(self.root.glob('.damaged-*'))), 1)

    def test_retry_repairs_output_damage_and_reselects_requested_pack(self):
        engine = stage.car_importer(self.fixture.profile, self.cache, mutating=True)
        previous = self.install_previous(engine)
        engine.remove([self.assets.car['slot']])
        path = self.root / previous.generation / 'cars' / str(self.assets.car['slot']) / 'GEOMETRY.BIN'
        path.chmod(0o644)
        path.write_bytes(b'broken geometry')
        engine.reimport(self.assets.carbon, mw_pc=self.assets.mw, cars=[self.assets.car])
        # Explicitly retrying this pack selects it again, as an ordinary import does.
        self.assertEqual(self.assets.ready(), previous)
        self.assertEqual(len(list(self.root.glob('.damaged-*'))), 1)

    def test_retry_merges_intact_outputs_from_damaged_legacy_generation(self):
        source, other, previous, merged = self.add_pack_after_cache_deletion(retained=False)
        installed = car_fixture.tree_hash(self.root / previous.generation)
        path = self.root / merged.generation / 'cars' / str(other['slot']) / 'GEOMETRY.BIN'
        path.chmod(0o644)
        path.write_bytes(b'broken geometry')
        engine = stage.car_importer(self.fixture.profile, self.cache, mutating=True)
        engine.reimport(source, mw_pc=self.assets.mw, cars=[other])
        self.assertEqual(self.assets.ready(), merged)
        self.assertEqual(car_fixture.tree_hash(self.root / previous.generation), installed)
        damaged = next(self.root.glob('.damaged-*'))
        self.assertEqual((damaged / 'cars' / str(other['slot']) / 'GEOMETRY.BIN').read_bytes(), b'broken geometry')

    def test_retry_failure_keeps_pointer_and_verified_generation(self):
        source, other, previous, merged = self.add_pack_after_cache_deletion(retained=True)
        installed = car_fixture.tree_hash(self.root / previous.generation)
        path = self.root / merged.generation / 'catalog.json'
        path.chmod(0o644)
        path.write_bytes(b'broken catalog')
        pointer = self.assets.pointer()
        engine = stage.car_importer(self.fixture.profile, self.cache, mutating=True)

        def fail(event):
            if event['phase'] == 'before_publish':
                raise OSError('fixture repair commit failure')

        with self.assertRaisesRegex(OSError, 'fixture repair commit failure'):
            engine.reimport(source, mw_pc=self.assets.mw, cars=[other], progress=fail)
        self.assertEqual(self.assets.pointer(), pointer)
        self.assertEqual(car_fixture.tree_hash(self.root / previous.generation), installed)
        engine.reimport(source, mw_pc=self.assets.mw, cars=[other])
        self.assertEqual(self.assets.ready(), merged)

    def test_retry_restores_only_verified_generation_after_pointer_damage(self):
        engine = stage.car_importer(self.fixture.profile, self.cache, mutating=True)
        previous = self.install_previous(engine)
        installed = car_fixture.tree_hash(self.root / previous.generation)
        pointer = self.root / 'current.json'
        pointer.chmod(0o644)
        pointer.write_bytes(b'broken pointer')
        engine.reimport(self.assets.carbon, mw_pc=self.assets.mw, cars=[self.assets.car])
        self.assertEqual(self.assets.ready(), previous)
        self.assertEqual(car_fixture.tree_hash(self.root / previous.generation), installed)
        self.assertFalse(list(self.root.glob('.damaged-*')))

    def test_failed_pointer_exchange_keeps_old_active_and_updates_app(self):
        previous = self.install_previous(self.assets.engine)
        self.new_release()
        ready, warning = self.prepare_update()
        self.assertEqual(warning, '')
        exchange = install.exchange

        def fail_car_pointer(path, candidate):
            if path == self.root / 'current.json':
                raise OSError('fixture pointer exchange failure')
            exchange(path, candidate)

        log = io.StringIO()
        with mock.patch.object(install, 'exchange', side_effect=fail_car_pointer), redirect_stderr(log):
            self.assertEqual(self.apply_update(ready), previous)
        self.assertEqual(log.getvalue().splitlines(), [stage.CAR_REFRESH_KEPT])


if __name__ == '__main__':
    unittest.main()
