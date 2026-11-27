"""Secondary-program proof, including the original profile/selector failures.

The original Carbon translator rejects 19/19 console banks; the fixed adapter
admits 19/19, retaining 336 live / 144 null slots and 312 rows (144 empty).
Expectations below walk the original BE descriptors, instructions and selector
rows independently. Null S10A slots never change the indices of later sounds.
Private inputs are optional; their absence exits 3 after the generated checks.
"""
import collections
import hashlib
import importlib.util
from pathlib import Path
import struct
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
from setup_kit import car_audio_undercover as uc, car_audio_x360 as xenon, car_audio_abi as abi
from setup_kit import car_audio, car_audio_pack


def u32(data, at):
    return struct.unpack_from('>I', data, at)[0]


def source_modules(raw):
    """Independent source descriptor/CSIS/selector oracle; no adapter calls."""
    interfaces = {}
    f3 = u32(raw, 56)
    for i in range(u32(raw, f3)):
        site, text, kind = struct.unpack_from('>3I', raw, f3 + 4 + 12*i)
        end = raw.index(0, text + 4)
        interfaces[site] = (*struct.unpack_from('>2H', raw, text), raw[text+4:end].decode())
        assert kind >> 24 in (1, 2)
    at = u32(raw, 28)
    result = []
    for _ in range(struct.unpack_from('>H', raw, 10)[0]):
        players, destructor, members, classes = raw[at+36:at+40]
        entry, state, size, destroy = struct.unpack_from('>4I', raw, at+40)
        offsets = struct.unpack_from('>' + str(players+classes) + 'I', raw, at+60)
        traces = []
        for off in offsets[:players]:
            target = u32(raw, state+off+4)
            rows = [struct.unpack_from('>3I', raw, target+4+12*i) for i in range(u32(raw, target))]
            traces.append((off, target, rows))
        result.append(dict(descriptor=at, interface=interfaces[at+4], entry=entry,
                           state=state, size=size, destroy=destroy, players=traces))
        at += 60 + 4*(players+classes)
    return result


def sounds(raw):
    bank = u32(raw, 32)
    return [dict(empty=True) if s is None else
            dict(empty=False, offset=s.header_offset-bank, frames=s.frames,
                 rate=s.rate, loop_start=s.loop_start) for s in uc.bank(raw)]


def fixture():
    """Own program: 7-word SWTN export, one player, live/null/live samples."""
    table, entry = 92, 156
    code = struct.pack('>BBHi', 0, 0, 0, 20)
    code += struct.pack('>BBH3i', 1, 1, 0, 24, 124, 48)
    code += b''.join(struct.pack('>BBHi', op, 0, 0, step) for op, step in ((3, 4), (27, 64)))
    code += struct.pack('>BBH5i', 31, 2, 0, -1, 32, -1, 56, 12)
    code += b''.join(struct.pack('>BBHi', op, 0, 0, step) for op, step in ((10, 24), (10, 24), (4, 0)))
    code += b'\xff\0\0\0'
    state = entry + len(code)
    template = bytearray(236)
    template[60] = 7
    struct.pack_into('>I', template, 92, 1)
    template[110] = 3; template[112] = 255
    struct.pack_into('>3I', template, 160, 0xfffffc18, 17, 0)
    struct.pack_into('>4i', template, 172, 1, 100, 0, 0)
    struct.pack_into('>4i', template, 196, -100, -1, 0, 0)
    for i, option in enumerate((0, 2, 3)):
        template[124+12*i] = option
    selector = state + len(template)
    struct.pack_into('>I', template, 100, selector)
    rows = struct.pack('>I9I', 3, 0x00026400, 3, 0xffffffff,
                       0xffff0000, 0, 0, 0x00006400, 1, 0xffffffff)
    bank = selector + len(rows)
    sample = struct.pack('>5I', 0x0300ac44, 7, 16, 7, 32) + b'\x08\0\0\0'
    s10 = b'S10A' + struct.pack('>5I', 0, 3, 24, 0xffffffff, 24+len(sample)) + sample*2
    f1 = bank + len(s10); f2 = f1+4; f3 = f2+8
    identity = b'\x6a\x16\x78\x59CAR_SWTN\0'
    out = bytearray(entry)
    out[:4] = b'ABKC'; struct.pack_into('>H', out, 10, 1)
    struct.pack_into('>5I', out, 20, f3+16+len(identity), bank, table, bank, len(s10))
    struct.pack_into('>3I', out, 48, f1, f2, f3)
    out[table+36:table+40] = bytes((1, 1, 1, 0))
    struct.pack_into('>5I', out, table+40, entry, state, len(template), 220, 0)
    struct.pack_into('>I', out, table+60, 96)
    return bytes(out) + code + template + rows + s10 + struct.pack('>7I',
        0, 1, state+100, 1, table+4, f3+16, 0x01000000) + identity


def translated_report(raw, interface):
    metadata = sounds(raw)
    native, identity = xenon.translate_undercover(raw, dict(sounds=metadata), interface)
    profile = dict(car_audio.bank_inventory(native), kind='abk', size=len(native),
                   sha256=hashlib.sha256(native).hexdigest(), sounds=metadata)
    return native, abi.inspect_bank(native, profile)


class Secondary(unittest.TestCase):
    def test_original_rejects_module_profile(self):
        raw = fixture()
        with self.assertRaisesRegex(ValueError, 'module descriptor profile'):
            xenon.translate(raw, dict(sounds=sounds(raw)))

    def test_generated_selector_identity(self):
        raw = fixture()
        native, report = translated_report(raw, 'CAR_SWTN')
        self.assertEqual(report['modules'][0]['member_words'], 7)
        self.assertEqual(report['interfaces'][0]['source_system'], 0x6a16)
        self.assertEqual(report['selectors'][0]['indices'], [2, None, 0])
        self.assertEqual(set(report['blockers']), {'A5-player', 'A5-player-destruction',
            'native program semantics and adapters require separate admission proof'})
        self.assertEqual(hashlib.sha256(raw).hexdigest(), hashlib.sha256(fixture()).hexdigest())

    def test_export_kind_identity_and_null_reference_refusals(self):
        raw=fixture(); metadata=dict(sounds=sounds(raw))
        native, identity=xenon.translate_undercover(raw,metadata,'CAR_SWTN')
        self.assertEqual(identity,17)
        f3=u32(raw,56);name=u32(raw,f3+8)
        for offset,value in ((f3+12,2), (name,0x31), (20,255)):
            bad=bytearray(raw);bad[offset]=value
            with self.assertRaises(ValueError):xenon.translate_undercover(bytes(bad),metadata,'CAR_SWTN')
        bad=bytearray(raw);selector=source_modules(raw)[0]['players'][0][1]
        struct.pack_into('>3I',bad,selector+4,0x00016400,2,0xffffffff)
        with self.assertRaisesRegex(ValueError,'selector sample identity'):
            xenon.translate_undercover(bytes(bad),metadata,'CAR_SWTN')


def private_proof(original=False):
    spec = importlib.util.spec_from_file_location('uc_corpus', ROOT / 'tests/setup/test_car_audio_undercover.py')
    corpus = importlib.util.module_from_spec(spec); spec.loader.exec_module(corpus)
    attr = corpus.input_path('Need for Speed Undercover/GLOBAL/ATTRIBUTES.BIN')
    images = sorted((corpus.input_path().parent / 'ext_untrusted/undercover_x360').glob('*.iso'))
    if not attr.is_file() or len(images) != 1:
        print('SKIP secondary corpus: source attributes/disc absent'); return False
    totals = collections.Counter()
    with uc.Disc(images[0]) as disc:
        for path in corpus.closure(attr.read_bytes()):
            if not path.endswith('.ABK'): continue
            raw = disc.read(path)
            metadata = sounds(raw)
            totals['banks'] += 1
            totals['null'] += sum(s.get('empty', False) for s in metadata)
            totals['live'] += sum(not s.get('empty', False) for s in metadata)
            try: xenon.translate(raw, dict(sounds=metadata))
            except ValueError: totals['original_rejected'] += 1
            if original: continue
            expected = next(m for m in source_modules(raw)
                            if m['interface'][2] in ('CAR_SWTN', 'CAR_WHINE', 'FX_SHIFTING_01'))
            native, report = translated_report(raw, expected['interface'][2])
            module = report['modules'][0]
            assert module['interface'] == expected['interface'][2]
            assert report['interfaces'][0]['source_system'] == expected['interface'][0]
            assert report['interfaces'][0]['source_signature'] == expected['interface'][1]
            assert all(p['classification'] == abi.TRANSLATED for p in module['players'])
            assert len(module['players']) == len(expected['players']) == 1
            for _, _, rows in expected['players']:
                indices = [None if first == 0xffff0000 else first >> 16 for first, _, _ in rows]
                assert report['selectors'][0]['indices'] == indices
                totals['selectors'] += len(rows)
                totals['empty_rows'] += indices.count(None)
            assert len(raw) == u32(raw, 20)
    print('secondary source proof:', dict(totals))
    assert totals['banks'] == totals['original_rejected'] == 19
    assert totals['live'] == 336 and totals['null'] == 144
    if original: raise AssertionError('original adapter: 0/19 module profiles admitted')
    return True


if __name__ == '__main__':
    original = '--original' in sys.argv
    if not original:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(Secondary)
        result = unittest.TextTestRunner(verbosity=1).run(suite)
        if not result.wasSuccessful(): raise SystemExit(1)
    if not private_proof(original): raise SystemExit(3)
