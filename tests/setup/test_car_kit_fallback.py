"""Missing-kit SOLIDMAP regression: predecessor 11 missing targets; fixed 0.

Two synthetic packs cover stock-only and partial kits, regular/wide kit names,
slot/LOD fidelity, retained authored kits, and independent image identities.
"""
from pathlib import Path
import contextlib
import inspect
import io
import struct
from unittest import mock
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from setup_kit import car_convert as cc
import test_car_convert as fixture


class KitFallback(unittest.TestCase):
    def test_missing_targets(self):
        missing_total = 0
        for partial in (False, True):
            with self.subTest(partial=partial):
                models = cc.validate_geometry(fixture.geometry('DONOR', False))
                requested = [f'DONOR_KIT03_BODY_{lod}' for lod in 'ABCD']
                requested += ['DONOR_KITW02_FRONT_BRAKE_A', 'DONOR_KIT05_FRONT_TIRE_D']
                if partial:
                    models['DONOR_KIT03_BODY_B'] = models['DONOR_KIT00_BODY_B']
                models['DONOR_KITW99_BODY_D'] = models['DONOR_KIT00_BODY_D']
                references = dict.fromkeys([*models, *requested, 'DONOR_KIT03_BODY_Z', 'DONOR_SPOILER03_A'])
                images = {fixture.nh('DONOR_DIFFUSE'): fixture.nh('NFSAD_CAR_DIFFUSE')}
                renamed, solidmap = cc.isolate(models, references, 'DONOR', 'CAR', images)
                self.assertEqual(struct.unpack_from('<4I', solidmap),
                                 (0x31504D53, 2, fixture.nh('CAR'), len(references) + 1))
                rows = list(struct.iter_unpack('<II', solidmap[16:]))
                self.assertEqual(rows, sorted(rows))
                mapping = dict(rows)
                present = {fixture.nh(name) for name in renamed}
                missing = sum(mapping[fixture.nh(name)] not in present for name in requested)
                missing_total += missing
                print(f'SOLIDMAP partial={partial}: missing targets {missing}/{len(requested)}')
                self.assertEqual(missing, 0, 'donor kit resolves to absent converted geometry')
                for name in requested:
                    suffix = name[len('DONOR_'):]
                    if name not in models:
                        suffix = 'KIT00_' + suffix.split('_', 1)[1]
                    self.assertEqual(mapping[fixture.nh(name)], fixture.nh('NFSAD_CAR_' + suffix))
                for name in models:
                    self.assertEqual(mapping[fixture.nh(name)], fixture.nh('NFSAD_CAR' + name[len('DONOR'):]))
                # No matching stock LOD/slot: never borrow another LOD or a donor model.
                for name in ('DONOR_KIT03_BODY_Z', 'DONOR_SPOILER03_A'):
                    self.assertEqual(mapping[fixture.nh(name)], fixture.nh('NFSAD_CAR' + name[len('DONOR'):]))
                self.assertEqual(mapping[fixture.nh('DONOR_DIFFUSE')], images[fixture.nh('DONOR_DIFFUSE')])
                self.assertEqual(len(mapping), len(rows))
        self.assertEqual(missing_total, 0)

    def test_reject_missing_kit_mutant(self):
        source = inspect.getsource(cc.isolate)
        broken = source.replace('if stock in models:', 'if False:')
        self.assertNotEqual(source, broken)
        scope = dict(vars(cc))
        exec(broken, scope)
        result = unittest.TestResult()
        with mock.patch.object(cc, 'isolate', scope['isolate']), contextlib.redirect_stdout(io.StringIO()):
            KitFallback('test_missing_targets').run(result)
        self.assertFalse(result.errors)
        self.assertEqual(len(result.failures), 3)
        self.assertTrue(any('11 != 0' in failure for _, failure in result.failures))
        print('missing-kit mutant rejected: 11 missing targets')


if __name__ == '__main__':
    unittest.main(verbosity=2)
