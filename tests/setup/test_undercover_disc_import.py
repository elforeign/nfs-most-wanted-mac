"""PC cars plus an optional console sound disc, without a PC SOUND folder.

Red first: the disc-input regression errors for all 14 cars on the original
importer (unexpected uc_x360 argument); the 14 donor controls pass. Fixed:
14/14 disc-enabled, 14/14 exact extracted pairs, 14/14 donor controls and
9/9 fixture/CLI checks. --mutant-disc drops the second input and fails 14/14
own-audio assertions. Fixtures use the real
XDVDFS/ZDIR reader and exact selected-range extraction, without private data.
"""
import copy
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'tools'), str(Path(__file__).resolve().parent)]
from setup_kit import car_import as ci, car_catalog, car_selection, car_sources
from setup_kit import car_audio_undercover as audio, xdvdfs
from setup_kit import backend, stage
from setup_kit.car_convert import name_hash
from test_car_audio_undercover import tmx


def disc_fixture(path, resources):
    sector = xdvdfs.SECTOR
    archive = bytearray()
    rows = bytearray()
    for name, raw in resources.items():
        start = len(archive) // sector
        rows += struct.pack('<6I', name_hash(name.replace('/', '\\')), 13, start, 0, len(raw), 0)
        archive += raw + bytes(-len(raw) % sector)
    image = bytearray(41 * sector) + archive
    image[32*sector:32*sector+20] = xdvdfs.MAGIC
    struct.pack_into('<2I', image, 32*sector+20, 33, sector)

    def entry(at, name, start, size, flags=0):
        struct.pack_into('<HHIIBB', image, at, 0, 0, start, size, flags, len(name))
        image[at+14:at+14+len(name)] = name
        return (at + 14 + len(name) + 3) & ~3

    entry(33*sector, b'NFS', 34, sector, 0x10)
    at = entry(34*sector, b'ZDIR.BIN', 40, len(rows))
    entry(at, b'13SOUND.BIN', 41, len(archive))
    image[40*sector:40*sector+len(rows)] = rows
    path.write_bytes(image)


class UndercoverDiscImport(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='uc-disc-import-')
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.pc, self.mw = self.base / 'pc', self.base / 'mw'
        self.rows = [copy.deepcopy(r) for r in car_selection.load() if r['origin']['game'] == 'undercover']
        self.assertEqual(len(self.rows), 14)
        self.resources = {}
        self.paths = {}
        for i, row in enumerate(self.rows):
            folder = row['origin']['folder']
            self.paths[folder] = [f'SOUND/ENGINE/CAR_{i+1:03}-{role}-PLAYER.TMX' for role in ('ENGINE', 'EXHAUST')]
            for name in self.paths[folder]:
                self.resources[name] = tmx()
            for name in ci._car_packs(folder):
                self.write(self.pc, name, b'car fixture')
            for name in ci._mw_names('undercover', row['donors']['model'], folder):
                self.write(self.mw, name, b'donor fixture')
        for name in car_sources.PC_CONVERSION_SHARED['undercover']:
            self.write(self.pc, name, b'attribute fixture' if 'ATTRIBUTES' in name else b'shared fixture')
        self.disc = self.base / 'sound.iso'
        disc_fixture(self.disc, self.resources)
        self.before = self.disc.read_bytes()
        self.engine = ci.CarImporter(self.base / 'catalog', self.base / 'work', margin_bytes=0)
        pin = lambda p: dict(path_hash=car_sources.path_hash(p), **car_catalog.fingerprint(self.pc / p))
        self.recognition = dict(game='undercover', platform='pc',
            shared=[pin(n) for n in car_sources.PC_CONVERSION_SHARED['undercover']],
            cars=[dict(car_hash=car_sources.path_hash('CARS/' + r['origin']['folder']), status='importable',
                       files=[pin(n) for n in ci._car_packs(r['origin']['folder'])]) for r in self.rows])
        for row in self.rows:
            names = ci._mw_names('undercover', row['donors']['model'], row['origin']['folder'])
            row['inputs']['donor'] = {n: car_catalog.fingerprint(self.mw / n) for n in names}
            row['inputs']['dependencies'] = {}
            row['inputs']['source'].update({n: dict(size=len(self.resources[n]),
                sha256=hashlib.sha256(self.resources[n]).hexdigest()) for n in self.paths[row['origin']['folder']]})
            row['outputs'] = {}
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(audio, 'ATTRIBUTES', hashlib.sha256(b'attribute fixture').hexdigest()).start()
        mock.patch.object(audio, 'describe', side_effect=lambda _, car: dict(layers=[dict(paths=[p]) for p in self.paths[car]])).start()
        mock.patch.object(car_sources, 'recognise', return_value=self.recognition).start()
        mock.patch.object(self.engine, '_later_rows', return_value={r['slot']: r for r in self.rows}).start()

    @staticmethod
    def write(root, name, raw):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)

    def test_pc_without_sound_plus_disc_enables_all_14(self):
        self.assertFalse((self.pc / 'SOUND').exists())
        for row in self.rows:
            with self.subTest(car=row['origin']['folder']), mock.patch.object(ci, '_code', wraps=ci._code) as code:
                self.engine.plan(self.pc, mw_pc=self.mw, cars=[car_selection.spec(row)], uc_x360=self.disc)
                self.assertTrue(code.call_args.kwargs['mac_audio'])
        self.assertEqual(self.disc.read_bytes(), self.before)
        self.assertFalse((self.pc / 'SOUND').exists())

    def test_without_disc_keeps_all_14_donor_sounds(self):
        for row in self.rows:
            with self.subTest(car=row['origin']['folder']), mock.patch.object(ci, '_code', wraps=ci._code) as code:
                result = self.engine.plan(self.pc, mw_pc=self.mw, cars=[car_selection.spec(row)])
                self.assertFalse(code.call_args.kwargs['mac_audio'])
                self.assertEqual(len(result['notes']), 1)
                self.assertIn('donor engine sounds', result['notes'][0])

    def test_exact_disc_bytes_reach_converter_for_all_14(self):
        class Extracted(Exception):
            pass

        def check_snapshot(platform, folder, car, recognition, stage, owned, donor, names, identity):
            self.assertEqual((platform, recognition['game']), ('pc', 'undercover'))
            selected = self.paths[folder]
            self.assertEqual(sorted(n for n in names.values() if n.startswith('SOUND/')), selected)
            for name in selected:
                self.assertEqual((stage / 'source' / name).read_bytes(), self.resources[name])
                self.assertEqual(identity['source'][car_sources.path_hash(name)]['sha256'],
                                 hashlib.sha256(self.resources[name]).hexdigest())
            raise Extracted()

        for row in self.rows:
            with self.subTest(car=row['origin']['folder']), mock.patch.object(self.engine, '_convert', side_effect=check_snapshot):
                with self.assertRaises(Extracted):
                    self.engine.plan(self.pc, mw_pc=self.mw, cars=[car_selection.spec(row)],
                                     uc_x360=self.disc, measure=True)
        self.assertEqual(self.disc.read_bytes(), self.before)
        self.assertFalse((self.pc / 'SOUND').exists())

    def prepare_one(self):
        row = self.rows[0]
        with self.engine._transaction(None) as receipt:
            return self.engine._prepare(self.pc, self.mw, self.recognition, car_selection.spec(row),
                car_catalog.packaged_policy(), receipt, None, None,
                later_rows={r['slot']: r for r in self.rows}, uc_x360=self.disc)

    def test_changed_disc_refused_before_extraction_and_activation(self):
        prepared = self.prepare_one()
        changed = bytearray(self.before)
        changed[41 * xdvdfs.SECTOR + len(tmx()) - 1] ^= 1
        self.disc.write_bytes(changed)
        with self.assertRaisesRegex(ci.ImportError, 'disc changed'):
            prepared['unchanged']()
        with self.assertRaisesRegex(ci.ImportError, 'source changed during selected extraction'):
            prepared['finish']()
        self.assertFalse((self.engine.root / 'current.json').exists())

    def test_moved_disc_span_refused_before_activation(self):
        prepared = self.prepare_one()
        changed = bytearray(self.before)
        struct.pack_into('<I', changed, 40 * xdvdfs.SECTOR + 8, 1)
        self.disc.write_bytes(changed)
        with self.assertRaisesRegex(ci.ImportError, 'spans changed'):
            prepared['unchanged']()

    def test_unapproved_disc_resource_refused_during_plan(self):
        changed = bytearray(self.before)
        changed[41 * xdvdfs.SECTOR + len(tmx()) - 1] ^= 1
        self.disc.write_bytes(changed)
        with self.assertRaisesRegex(ci.ImportError, 'does not match the approved file'):
            self.engine.plan(self.pc, mw_pc=self.mw, cars=[car_selection.spec(self.rows[0])], uc_x360=self.disc)

    def test_disc_symlink_and_hardlink_refused(self):
        link = self.base / 'link.iso'
        link.symlink_to(self.disc)
        with self.assertRaises((ValueError, OSError)):
            self.engine.plan(self.pc, mw_pc=self.mw, cars=[car_selection.spec(self.rows[0])], uc_x360=link)
        link.unlink()
        import os
        os.link(self.disc, link)
        with self.assertRaises((ValueError, OSError)):
            self.engine.plan(self.pc, mw_pc=self.mw, cars=[car_selection.spec(self.rows[0])], uc_x360=link)

    def test_incomplete_or_other_disc_refused(self):
        self.disc.write_bytes(self.before[:41 * xdvdfs.SECTOR])
        with self.assertRaises(ValueError):
            self.engine.plan(self.pc, mw_pc=self.mw, cars=[car_selection.spec(self.rows[0])], uc_x360=self.disc)
        disc_fixture(self.disc, {'SOUND/ENGINE/CAR_999-ENGINE-PLAYER.TMX': tmx()})
        with self.assertRaisesRegex(ci.ImportError, 'disc is missing'):
            self.engine.plan(self.pc, mw_pc=self.mw, cars=[car_selection.spec(self.rows[0])], uc_x360=self.disc)

    def test_setup_cli_forwards_disc_and_reports_donor_fallback(self):
        selection = self.base / 'selection.json'
        selection.write_text(json.dumps([car_selection.spec(r) for r in self.rows]))
        import os
        env = {k: v for k, v in os.environ.items() if not k.startswith('NFSMW_SETUP_')}
        env['NFSMW_SETUP_HOME'] = str(self.base / 'setup-home')
        for disc in (self.disc, None):
            output = io.StringIO()
            args = ['--cars-check', '--carbon', str(self.pc), '--cars-spec', str(selection),
                    '--cars-mw-pc', str(self.mw), '--events']
            if disc:
                args += ['--cars-uc-x360', str(disc)]
            with self.subTest(disc=bool(disc)), mock.patch.dict(os.environ, env, clear=True), \
                 mock.patch.object(stage, 'car_importer', return_value=self.engine), \
                 mock.patch.object(ci, '_code', wraps=ci._code) as code, redirect_stdout(output):
                rc = backend.main(args)
            events = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertEqual(rc, 0, events)
            self.assertEqual(len(code.call_args_list), 14)
            self.assertTrue(all(call.kwargs['mac_audio'] == bool(disc) for call in code.call_args_list))
            notes = [e['msg'] for e in events if e['ev'] == 'warn']
            self.assertEqual(any('donor engine sounds' in note for note in notes), disc is None)


if __name__ == '__main__':
    if '--mutant-disc' in sys.argv:
        original = ci.CarImporter._prepare

        def drop_disc(self, *args, **kwargs):
            kwargs['uc_x360'] = None
            return original(self, *args, **kwargs)

        with mock.patch.object(ci.CarImporter, '_prepare', drop_disc):
            result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([
                UndercoverDiscImport('test_pc_without_sound_plus_disc_enables_all_14')]))
        raise SystemExit(0 if result.wasSuccessful() else 1)
    unittest.main(verbosity=2)
