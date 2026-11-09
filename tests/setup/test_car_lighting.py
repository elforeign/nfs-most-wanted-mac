"""Row-policy contract: source identities in, remap + standalone MW payloads out.

Plan: independently evaluate both setters' min/range constants; retain the
original role-only selection as the broken amplitude baseline. No game runs.
Default/Off: exact MW donor bytes, zero emitted amplitude rows. Dynamic BODY
and replaceable rims are explicitly skipped. Lamp rows clone MW coefficients.
Measured: 2,916 endpoint scalars pass, 339 negative ranges preserved (162/156
local rows). Five broken controls fail 595/688/339/8/685 scalars respectively:
specular gain, environment gain, positive-only ranges, alpha, vinyl amplitude.
Original role-only policy fails 12/18 synthetic constants; translation fails 0.
Lamp default-Off regression: reviewed default-On code fails 2/2 lamp cases; fixed 0/2.
No source asset records are embedded. Optional local census needs both bundles.
"""
import sys as _inputs_sys
from pathlib import Path as _InputsPath
_inputs_sys.path.insert(0, str(_InputsPath(__file__).resolve().parents[2] / "tools"))
from setup_kit.input_paths import input_path
import copy
import hashlib
import math
import os
from pathlib import Path
import struct
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
from setup_kit import car_convert as cc
from setup_kit import car_lighting as cl


def f(row, offset):
    return struct.unpack_from('<f', row, offset)[0]


def source_constants(row, spec=.25):
    # Independent endpoint calculation: expand each RGB channel, then subtract.
    values = []
    for low, high, gain in ((0x64, 0x74, 1), (0x98, 0xA8, spec), (0xC4, 0xD4, .5)):
        minimum = [f(row, low + channel) * f(row, low) * gain for channel in (4, 8, 12)]
        maximum = [f(row, high + channel) * f(row, high) * gain for channel in (4, 8, 12)]
        values += minimum + [b - a for a, b in zip(minimum, maximum)]
    return values


def mw_constants(row, spec=1, env=1):
    values = []
    for low, high, gain in ((0x30, 0x40, 1), (0x5C, 0x6C, spec), (0x80, 0x90, env)):
        minimum = [f(row, low + 4 * (i + 1)) * f(row, low) * gain for i in range(3)]
        ranges = [gain * f(row, high + 4 * (i + 1)) * f(row, high) - minimum[i] for i in range(3)]
        values.extend(minimum)
        values.extend(ranges)
    return values


def errors(actual, expected):
    return sum(abs(a - b) > 1e-6 * max(1, abs(b)) for a, b in zip(actual, expected))


def endpoints(row, source, spec=.25, env=.5):
    offsets = (0x64, 0x74, 0x98, 0xA8, 0xC4, 0xD4) if source else (0x30, 0x40, 0x5C, 0x6C, 0x80, 0x90)
    return [f(row, off + i) * f(row, off) * gain
            for off, gain in zip(offsets, (1, 1, spec, spec, env, env)) for i in (4, 8, 12)]


def fixture(name, source=True):
    row = bytearray(236 if source else 168)
    struct.pack_into('<II', row, 8, 3, cc.name_hash(name))
    width = 32 if source else 28
    row[20:20+width] = name.encode().ljust(width, b'\0')
    blocks = (0x64, 0x74, 0x98, 0xA8, 0xC4, 0xD4) if source else (0x30, 0x40, 0x5C, 0x6C, 0x80, 0x90)
    for i, offset in enumerate(blocks):
        struct.pack_into('<4f', row, offset, 8 if i % 2 == 0 else 1, 1, .5, .25)
    for off, value in ((0x84 if source else 0x50, .75), (0x88 if source else 0x54, .25),
                       (0x8C if source else 0x58, 16), (0xB8 if source else 0x7C, 4)):
        struct.pack_into('<f', row, off, value)
    return bytes(row)


class RowPolicy(unittest.TestCase):
    def setUp(self):
        self.key = cc.name_hash('CARBONFIBER')
        self.target = cc.name_hash('CARBONFIBER2')
        self.source = {self.key: fixture('CARBONFIBER')}
        self.mw = {self.target: fixture('CARBONFIBER2', False)}
        self.kw = dict(namespace='TEST_CAR', roles={self.key: self.target}, lamps=False)

    def policy(self, **kw):
        return cl.policy(self.source, self.mw, [self.key], **(self.kw | kw))

    def test_original_role_only_fails_amplitude_expectations(self):
        # Pin the original role decision with a source-format record, then run
        # the independent new oracle on the unmodified MW donor it selects.
        packed = struct.pack('<II', 0x135200, 236) + self.source[self.key]
        self.assertEqual(cc.material_role_map(packed)[self.key], self.target)
        good = self.policy(amplitudes=True, gains=cl.Gains(.25, 1, 1))
        want = source_constants(self.source[self.key])
        old_errors = errors(mw_constants(self.mw[self.target]), want)
        self.assertEqual(old_errors, 12)
        self.assertEqual(errors(mw_constants(good.rows[good.remap[self.key]]), want), 0)
        print(f'original role-only: {old_errors}/18 failures; amplitude policy: 0/18', flush=True)

    def test_off_dynamic_and_input_invariance(self):
        before = copy.deepcopy((self.source, self.mw))
        off = self.policy()
        self.assertEqual(off.remap, {self.key: self.target})
        self.assertEqual(off.rows, {})
        for dynamic in ([self.key], [self.target]):
            on = self.policy(amplitudes=True, gains=cl.Gains(.25, 1, 1), dynamic=dynamic)
            self.assertEqual(on.remap, off.remap)
            self.assertEqual(on.rows, {})
            self.assertEqual(on.dynamic, (self.key,))
        for name in ('BODY', 'MAGSILVER'):
            key = cc.name_hash(name)
            result = cl.policy({key: fixture(name)}, {key: fixture(name, False)}, [key],
                               namespace='TEST_CAR', amplitudes=True, gains=cl.Gains(.25, 1, 1))
            self.assertEqual(result.rows, {})
            self.assertEqual(result.dynamic, (key,))
        self.assertEqual((self.source, self.mw), before)

    def test_signed_hdr_alpha_powers_and_gain_changes(self):
        for gains in (cl.Gains(.25, 1, 1), cl.Gains(1, .3, .5, 2, .7), cl.Gains(0, 1, 1)):
            out = self.policy(amplitudes=True, gains=gains)
            row = out.rows[out.remap[self.key]]
            self.assertEqual(errors(mw_constants(row, gains.mw_specular, gains.mw_environment),
                                    source_constants(self.source[self.key], gains.source_specular)), 0)
            self.assertAlmostEqual(f(row, 0x50), .75)
            self.assertAlmostEqual((f(row, 0x54) - f(row, 0x50)) * .25, -.5)
            self.assertAlmostEqual(f(row, 0x58) * gains.specular_power, 16, places=5)
            self.assertAlmostEqual(f(row, 0x7C) * gains.environment_power, 4, places=5)
            self.assertEqual(row[0xA0:0xA8], bytes(8))
            self.assertGreater(mw_constants(row)[0], 1)
            self.assertLess(mw_constants(row)[3], 0)
            self.assertIn('diffuse_power_clamp', out.unsupported[self.key])

    def test_namespaces_collisions_reload_and_converging_roles(self):
        kwargs = dict(amplitudes=True, gains=cl.Gains(.25, 1, 1))
        first = self.policy(**kwargs)
        self.assertEqual(first, self.policy(**kwargs))
        other = self.policy(**(kwargs | dict(namespace='OTHER_CAR')))
        self.assertTrue(set(first.rows).isdisjoint(other.rows))
        with self.assertRaisesRegex(ValueError, 'collision'):
            self.policy(**kwargs, occupied=first.rows)
        key2 = cc.name_hash('OTHER_SURFACE')
        self.source[key2] = fixture('OTHER_SURFACE')
        both = cl.policy(self.source, self.mw, [self.key, key2], namespace='TEST_CAR',
                         roles={self.key: self.target, key2: self.target}, **kwargs)
        self.assertEqual(len(both.rows), 2)

    def test_lamps_default_off(self):
        for name in cl.LAMP_NAMES:
            with self.subTest(name=name):
                key = cc.name_hash(name)
                result = cl.policy({key: fixture(name)}, {key: fixture(name, False)},
                                   [key], namespace='TEST_CAR')
                self.assertEqual((result.remap, result.rows), ({key: key}, {}))

    def test_mw_lamp_rows_no_source_constants(self):
        # Independent runtime selector contract, including the native exclusion.
        selected = {'BRAKELIGHTGLASS': 0xDAEE90A1, 'HEADLIGHTGLASS': 0x3CF307CE}
        for name, output in cl.LAMP_NAMES.items():
            key = cc.name_hash(name)
            donor = fixture(name, False)
            result = cl.policy({key: fixture(name)}, {key: donor}, [key], namespace='TEST_CAR', lamps=True)
            row = result.rows[cc.name_hash(output)]
            self.assertEqual(row[0x30:], donor[0x30:])
            self.assertEqual(row[:8], bytes(8))
            self.assertEqual(result.remap[key], cc.name_hash(output))
            self.assertEqual(result.remap[key], selected[name])
            self.assertNotIn(key, result.rows)
            off = cl.policy({key: fixture(name)}, {key: donor}, [key], namespace='TEST_CAR', lamps=False)
            self.assertEqual(off.rows, {})
            with self.assertRaisesRegex(ValueError, 'lamp donor role mismatch'):
                cl.policy({key: fixture(name)}, {key: donor, self.target: self.mw[self.target]},
                          [key], namespace='TEST_CAR', roles={key: self.target}, lamps=True)

    def test_unsupported_shader_controls_are_classified(self):
        row = bytearray(self.source[self.key])
        for offset in (0x54, 0x58, 0x5C, 0x90, 0xBC, 0x60, 0x94, 0xC0, 0xE4, 0xE8):
            struct.pack_into('<f', row, offset, 2)
        self.source[self.key] = bytes(row)
        result = self.policy(amplitudes=True, gains=cl.Gains(.25, 1, 1))
        unsupported = result.unsupported[self.key]
        self.assertEqual(unsupported['diffuse_power_clamp'], (2, 2))
        self.assertEqual(unsupported['environment_clamp'], 2)
        self.assertEqual(unsupported['flakes'], (2, 2))
        self.assertEqual(unsupported['vinyl_luminance'], (2,) * 5)
        self.assertIn('environment_rgb_scalar_path', unsupported)
        translated = result.rows[result.remap[self.key]]
        self.assertEqual(errors(mw_constants(translated), source_constants(bytes(row))), 0)
        self.assertEqual(translated[0xA0:0xA8], bytes(8))

    def test_reject_invalid_rows_and_options(self):
        for raw in (self.source[self.key][:-1], bytes(236),
                    self.source[self.key][:0x64] + struct.pack('<f', math.nan) + self.source[self.key][0x68:]):
            with self.assertRaises(ValueError):
                cl.policy({self.key: raw}, self.mw, [self.key], **self.kw)
        with self.assertRaises(ValueError):
            self.policy(amplitudes=True)
        for gains in (cl.Gains(.25, 0, 1), cl.Gains(.25, 1, -1), cl.Gains(math.nan, 1, 1)):
            with self.assertRaises(ValueError):
                self.policy(amplitudes=True, gains=gains)
        with self.assertRaises(ValueError):
            self.policy(namespace='../escape')
        with self.assertRaises(ValueError):
            self.policy(occupied=range(cl.MAX_ROWS + 1))
        with self.assertRaises(ValueError):
            cl.policy({}, self.mw, [self.key], namespace='TEST_CAR', amplitudes=True,
                      gains=cl.Gains(.25, 1, 1))


class LocalCensus(unittest.TestCase):
    def test_local_endpoints_and_five_controls(self):
        source_root = input_path('NFSC')
        mw_root = Path(os.environ.get('NFS_GAME', ROOT.parent / 'Need For Speed Most Wanted Black Edition'))
        paths = [p / 'GLOBAL/GLOBALB.LZC' for p in (source_root, mw_root)]
        if not all(p.is_file() for p in paths):
            self.skipTest('local lighting bundles absent; synthetic proof still runs')
        decoded = []
        for path in paths:
            raw = cc.unpack_file(path.read_bytes())
            decoded.append([raw[o+8:o+8+n] for cid, o, n in cc.walk(raw) if cid == 0x135200])
        source, donors = decoded
        donor = donors[0]
        failures = [0] * 5
        good = negative = scalars = chromatic = flakes = 0
        for i, src in enumerate(source):
            want = source_constants(src)
            row = cl.translate_row(src, donor, f'IMP_CENSUS_{i}', cl.Gains(.25, 1, 1))
            actual = mw_constants(row)
            good += errors(actual, want)
            endpoint_want = endpoints(src, True)
            good += errors(endpoints(row, False, 1, 1), endpoint_want)
            scalars += len(endpoint_want)
            negative += sum(v < 0 for start in (3, 9, 15) for v in want[start:start+3])
            chromatic += 'environment_rgb_scalar_path' in cl.limitations(src)
            flakes += f(src, 0x90) != 0
            for control in (0, 1, 4):
                broken = bytearray(row)
                for so, dst in ((0x98, 0x5C), (0xA8, 0x6C)) if control == 0 else ((0xC4, 0x80), (0xD4, 0x90)):
                    scale = f(src, so) if control in (0, 1) else f(src, 0xC0) * .5
                    struct.pack_into('<f', broken, dst, scale)
                # Census counts endpoint differences, including tiny nonzero
                # coefficients. Acceptance above uses the stated tolerance.
                failures[control] += sum(a != b for a, b in zip(endpoints(broken, False, 1, 1), endpoint_want))
            clamped = actual[:]
            for start in (3, 9, 15):
                clamped[start:start+3] = [max(0, v) for v in clamped[start:start+3]]
            failures[2] += sum(a != b for a, b in zip(clamped, want))
            desired_alpha = f(src, 0x88) - f(src, 0x84)
            self.assertEqual(errors([f(row, 0x50), (f(row, 0x54)-f(row, 0x50))*.25],
                                    [f(src, 0x84), desired_alpha]), 0)
            failures[3] += errors([desired_alpha * .25], [desired_alpha])
        self.assertEqual(good, 0)
        self.assertTrue(all(n > 0 for n in failures))
        # Pin the row corpus rather than treating every 162-row release as
        # identical. Hashes identify local inputs; no input rows are bundled.
        corpus = hashlib.sha256(b''.join(source)).hexdigest()
        if corpus == 'd69274f449e65e037a145a22ef4a53404663c02c1e1a47d013733915011304dc':
            self.assertEqual((scalars, negative, chromatic, flakes), (2916, 339, 30, 82))
            self.assertEqual(failures, [595, 688, 339, 8, 685])
        print(f'local census: {len(source)}/{len(donors)} rows, {scalars} scalars, {good} failures, '
              f'{negative} signed ranges; five controls={failures}', flush=True)


if __name__ == '__main__':
    unittest.main()
