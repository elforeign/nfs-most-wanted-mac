"""Mac sidecar boundaries; generated GIN accepts both table directions.

Control: increasing-only validation rejects the descending fixture (0/1);
current validation accepts it (1/1). Mutation of every header/table boundary
below is rejected; unsupported profiles cannot reach the conversion writer.
Private package reproduction is checked by runtime/car_audio_test.py.
"""
import hashlib
from pathlib import Path
import struct
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
from setup_kit import car_audio_pack as pack
from setup_kit import car_audio_abi as abi


def gin(descending=False):
    data = bytearray(48+2*19)
    data[:6] = b'Gnsu20'
    struct.pack_into('<ff4I', data, 8, 1000, 9000, 1, 1, 64, 44100)
    struct.pack_into('<4I', data, 32, *(63, 0) if descending else (0, 63), 0, 63)
    return bytes(data)


class Pack(unittest.TestCase):
    def test_gin_both_directions(self):
        for descending in (False, True):
            pack.validate_gin(gin(descending))
        values = struct.unpack_from('<2I', gin(True), 32)
        self.assertFalse(all(a <= b for a, b in zip(values, values[1:])))

    def test_gin_corrupt(self):
        for at, fmt, value in ((0, '<I', 0), (8, '<f', float('nan')), (12, '<f', 0),
                               (16, '<I', 0), (20, '<I', 0xffffffff), (24, '<I', 0),
                               (28, '<I', 1), (32, '<I', 64), (44, '<I', 64)):
            with self.subTest(at=at):
                data = bytearray(gin())
                struct.pack_into(fmt, data, at, value)
                with self.assertRaises(ValueError):
                    pack.validate_gin(bytes(data))
        with self.assertRaises(ValueError):
            pack.validate_gin(gin()[:-1])

    def test_full_digest_names(self):
        a, b = (hashlib.sha256(v).hexdigest() for v in (b'car', b'resource'))
        self.assertEqual(pack.resource_name(a, b), 'SOUND/ADDCAR_AUDIO/'+a+'/'+b)
        self.assertNotEqual(pack.resource_name(a, b), pack.resource_name(b, a))
        for bad in (a[:-1], a.upper(), '../'+a, 'g'*64):
            with self.assertRaises(ValueError):
                pack.resource_name(bad, b)

    def test_unsupported_before_parsing(self):
        with self.assertRaisesRegex(ValueError, 'unsupported car'):
            pack.build(b'', 'othercar', {})
        with self.assertRaisesRegex(ValueError, 'attributes revision'):
            pack.build(b'', 'jaguarxk', {})

    def test_generic_admission_stays_closed(self):
        # No caller-provided certificate, bool or arbitrary profile can create
        # an admitted bank. Exact bytes and complete ABI validation come first.
        for closure in (None, True, pack.PROFILE):
            with self.assertRaises(ValueError):
                abi.require_sound_admission(b'ABKC', {}, closure=closure)

    def test_converter_optional(self):
        from setup_kit import car_convert
        import inspect
        self.assertFalse(inspect.signature(car_convert.convert).parameters['mac_audio'].default)
        self.assertNotIn('mac_audio', inspect.signature(car_convert.convert_x360).parameters)


if __name__ == '__main__':
    unittest.main()
