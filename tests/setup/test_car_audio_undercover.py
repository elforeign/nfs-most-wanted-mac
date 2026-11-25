"""Bounded console audio proof; missing private inputs exit 3 after fixtures.

Red first: 2/2 API checks errored before implementation; production PC readers
reject 64/64 source TMXs and 19/19 ABKs. Uncorrected GINs reject 42/151.
Passing: 6/6 fixture checks, 83/83 bounded reads with unchanged hashes,
151 GINs (42 corrected), 101 XAS decodes / 11,718,728 oracle-matched frames,
337 XMA decodes / 5,130,361 frames, 144 null slots. Truncation rejection:
83/83 source spans, 151/151 GINs, 101/101 XAS and 336/336 bank XMA sounds.
Incorrect GIN frame controls reject 151/151. All seven mutants exit 1:
GIN count 2 errors; GIN endian 2; TMX endian 1; duplicate hash 1 failure;
span bounds 1; XAS block size 1; SNR endian 3 errors. --original exits 1;
missing private inputs exits 3 after 6/6 fixtures. These tests join
runtime/offline_tests.sh by filename discovery and runtime/impact_tests.py
through direct production-module imports.
The closure is derived from typed PC selectors, independently of the reader.
Only the GIN frame word may change; compressed blocks/tables retain their hashes.
"""
import collections
import hashlib
import importlib.util
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import types
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
from setup_kit import car_audio_codec as codec, car_audio_undercover as uc
from setup_kit import car_audio, car_audio_pack, car_audio_prostreet, uc_attrib, xdvdfs
from setup_kit.car_convert import FitAttributes, hash32, name_hash
from setup_kit.input_paths import input_path


def gin(frames=128):
    # Three 32-frame compressed blocks, terminal cycle sample 64 -> 65 frames.
    return b'Gnsu20\0\0' + struct.pack('<ff4I4I', 900, 6000, 1, 1, frames, 34000,
                                         0, 64, 0, 64) + bytes(3 * 19)


def snr():
    return struct.pack('>5I', 0x04000000 | 22050, 0x20000000 | 7, 2, 84, 7) + bytes(76)


def xma():
    # Structural fixture only: decoding proof uses authored XMA bitstreams.
    return struct.pack('>5I', 0x03000000 | 44100, 7, 16, 7, 8 << 2) + bytes(4)


def bank():
    sample = xma()
    s10 = b'S10A' + struct.pack('>5I', 0, 3, 24, 0xffffffff, 24 + len(sample)) + sample * 2
    header = bytearray(96)
    header[:4] = b'ABKC'
    struct.pack_into('>I', header, 20, len(header) + len(s10))
    struct.pack_into('>2I', header, 32, 96, len(s10))
    return bytes(header) + s10


def tmx():
    out = bytearray(96)
    out[:12] = struct.pack('>3I', 11, 22, 33)
    struct.pack_into('>4s10I', out, 12, b'0001', 4, 3, 0, 12, 3, 1, 3, 3, 3, 16)
    offsets = [len(out)]
    for block, controls in enumerate((7, 5, 4)):
        out += out[:12] + struct.pack('>5I', block, controls, 0, 0, 0)
    offsets.append(len(out))
    for pair in range(2):
        for side in range(2):
            out += struct.pack('>6I5f', 2*pair + side, 0, 0, 2*side, 0, 0, 0, 1, 0, 1, 0)
        out += bytes(16)
    offsets.append(len(out))
    for index in range(3):
        out += struct.pack('>7I', index, 1, 28, 0, 0, 0, 0)
        for side in range(2):
            out += struct.pack('>4I4f', side, index, 0, 0, 900 + side*5100, 1, 1, 0)
    offsets.extend([len(out)] * 3)
    out += struct.pack('>2f', 900, 6000) + bytes(120)
    offsets.append(len(out)); out += bytes(64)
    offsets.append(len(out)); out += bytes(12)
    offsets.append(len(out))
    payload = bytearray()
    for index, raw in enumerate((gin(), gin(65), snr())):
        kind = 0x534e5220 if index == 2 else 0x47494e20
        out += struct.pack('>7I2f', kind, index, len(payload), 0, 0, 1, 0, 1000, 0)
        payload += raw + bytes(-len(raw) % 4)
    offsets.append(len(out))
    struct.pack_into('>10I', out, 56, *offsets)
    return bytes(out + payload)


class Boundaries(unittest.TestCase):
    def test_shared_snr(self):
        sound = codec.parse_snr(snr())
        self.assertEqual((sound.rate, sound.frames, sound.loop_start, sound.loop_end), (22050, 7, 2, 6))
        self.assertEqual(codec.decode_sound(sound).pcm, bytes(14))
        self.assertEqual(codec.parse_snr(snr() + bytes(3), padding=3), sound)
        # Reads remain bounded even with enough bytes in the containing file.
        for end in range(len(snr())):
            with self.assertRaises(ValueError):
                codec.parse_snr(snr() + bytes(100), end=end)
        for at, fmt, value in ((0, '<I', 0x04000000 | 22050), (4, '>I', 8),
                               (12, '>I', 85), (16, '>I', 8), (20, '>B', 4)):
            raw = bytearray(snr()); struct.pack_into(fmt, raw, at, value)
            with self.assertRaises(ValueError):
                codec.parse_snr(bytes(raw))
        oversized = bytearray(snr() + bytes(76))
        struct.pack_into('>I', oversized, 12, 160)
        with self.assertRaises(ValueError):
            codec.parse_snr(bytes(oversized))
        with self.assertRaises(ValueError):
            codec.parse_snr(snr() + b'\x01', padding=3)

    def test_xma_and_null_slots(self):
        sounds = uc.bank(bank())
        self.assertEqual(len(sounds), 3)
        self.assertIsNone(sounds[1])
        self.assertEqual((sounds[0].codec, sounds[0].rate, sounds[2].slot), (3, 44100, 2))
        with self.assertRaises(ValueError):
            codec.decode_sound(sounds[0])
        for end in range(len(xma())):
            with self.assertRaises(ValueError):
                codec.parse_snr(xma(), end=end)
        for at, value in ((8, 17), (12, 8), (16, 0)):
            raw = bytearray(xma()); struct.pack_into('>I', raw, at, value)
            with self.assertRaises(ValueError):
                codec.parse_snr(bytes(raw))
        for at, fmt, value in ((20, '<I', len(bank())), (32, '>I', 0xfffffff0),
                               (108, '>I', 25), (116, '>I', 24)):
            raw = bytearray(bank()); struct.pack_into(fmt, raw, at, value)
            with self.assertRaises(ValueError):
                uc.bank(bytes(raw))
        with self.assertRaises(ValueError):
            uc.bank(bank()[:-1])

    def test_gin_normalization(self):
        source = gin()
        normalized = uc.normalize_gin(source)
        self.assertEqual(struct.unpack_from('<I', normalized, 24)[0], 65)
        self.assertEqual(normalized[:24] + normalized[28:], source[:24] + source[28:])
        self.assertEqual(uc.normalize_gin(normalized), normalized)
        self.assertEqual(uc.normalize_gin(source + bytes(3)), normalized)
        with self.assertRaises(ValueError):
            car_audio_pack.validate_gin(source)
        for end in range(len(source)):
            with self.assertRaises(ValueError):
                uc.normalize_gin(source[:end])
        for at, fmt, value in ((16, '>I', 1), (20, '<I', 0xffffffff), (24, '<I', 64),
                               (44, '<I', 96), (36, '<I', 66), (28, '>I', 34000)):
            raw = bytearray(source); struct.pack_into(fmt, raw, at, value)
            with self.assertRaises(ValueError):
                uc.normalize_gin(bytes(raw))
        for extra in (bytes(4), b'\x01'):
            with self.assertRaises(ValueError):
                uc.normalize_gin(source + extra)

    def test_tmx_controls_and_source_identity(self):
        source = tmx(); digest = hashlib.sha256(source).hexdigest()
        plan = uc.tmx(source)
        self.assertEqual((len(plan['controls']), len(plan['bins']), len(plan['tracks'])), (4, 3, 3))
        self.assertEqual(plan['rpm'], (900, 6000))
        self.assertEqual(plan['source_sha256'], digest)
        for track in plan['tracks']:
            raw = source[track['offset']:track['offset'] + track['span_size']]
            self.assertEqual(track['source_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(hashlib.sha256(source).hexdigest(), digest)
        self.assertEqual(struct.unpack_from('<I', plan['tracks'][0]['data'], 24)[0], 65)
        # PC controls cannot silently admit BE controls or vice versa.
        with self.assertRaises(ValueError):
            car_audio_prostreet.tmx(source)
        for at, fmt, value in ((16, '<I', 4), (20, '>I', 5), (48, '>I', 2),
                               (96+16, '>I', 5), (192+16, '>I', 1), (88, '>I', 0xfffffff0)):
            raw = bytearray(source); struct.pack_into(fmt, raw, at, value)
            with self.assertRaises(ValueError):
                uc.tmx(bytes(raw))
        # Losing alignment alone is valid; truncate actual compressed/header bytes.
        for end in (95, 191, 399, len(source)-4):
            with self.assertRaises(ValueError):
                uc.tmx(source[:end])

    def test_zdir_bounds_uniqueness_and_endian(self):
        files = {'NFS/13SOUND.BIN': xdvdfs.Entry('NFS/13SOUND.BIN', 100, 4096)}
        row = struct.pack('<6I', 1, 13, 1, 123, 80, 456)
        self.assertEqual(uc.sound_spans(row, files), {1: (2148, 80)})
        for raw in (row + row, row[:-1], struct.pack('>6I', 1, 13, 1, 123, 80, 456)):
            with self.assertRaises(ValueError):
                uc.sound_spans(raw, files)
        for at, value in ((8, 2), (16, 2049), (16, 0)):
            raw = bytearray(row); struct.pack_into('<I', raw, at, value)
            with self.assertRaises(ValueError):
                uc.sound_spans(bytes(raw), files)
        with self.assertRaises(ValueError):
            uc.sound_spans(row, {})

    def test_disc_exact_read_and_truncated_span(self):
        source = tmx(); sector = xdvdfs.SECTOR
        image = bytearray(42 * sector)
        image[32*sector:32*sector+20] = xdvdfs.MAGIC
        struct.pack_into('<2I', image, 32*sector+20, 33, sector)
        def entry(at, name, start, size, flags=0):
            struct.pack_into('<HHIIBB', image, at, 0, 0, start, size, flags, len(name))
            image[at+14:at+14+len(name)] = name
            return (at + 14 + len(name) + 3) & ~3
        entry(33*sector, b'NFS', 34, sector, 0x10)
        at = entry(34*sector, b'ZDIR.BIN', 40, 24)
        entry(at, b'13SOUND.BIN', 41, sector)
        name = 'SOUND/ENGINE/CAR_001-ENGINE-PLAYER.TMX'
        struct.pack_into('<6I', image, 40*sector, name_hash(name.replace('/', '\\')), 13, 0, 0, len(source), 0)
        image[41*sector:41*sector+len(source)] = source
        with tempfile.TemporaryDirectory(prefix='sound-disc-') as folder:
            path = Path(folder) / 'source.iso'; path.write_bytes(image)
            with uc.Disc(path) as disc:
                self.assertEqual(disc.read(name), source)
                for bad in ('../escape.TMX', '/SOUND/ENGINE/X.TMX', 'SOUND/ENGINE/ABSENT.TMX'):
                    with self.assertRaises(ValueError): disc.read(bad)
                path.write_bytes(image[:41*sector + len(source)-1])
                with self.assertRaises(ValueError): disc.read(name)


def closure(attributes):
    """Independent typed closure of every tier for the 14 catalog additions."""
    native = FitAttributes(attributes, vaults=uc_attrib.vaults(attributes))
    attrib = object.__new__(car_audio.AudioAttributes)
    attrib.defs, attrib.collections = native.defs, native.collections
    import json
    cars = [r['origin']['folder'] for r in json.loads(
        (ROOT / 'tools/setup_kit/car_selections.json').read_text()) if r['origin']['game'] == 'undercover']
    assert len(cars) == len(set(cars)) == 14
    paths = set()
    for car in cars:
        key = hash32(uc_attrib.STOCK_ALIASES.get(car.upper(), car).lower())
        tiers = attrib.field(hash32('pvehicle'), key, hash32('Audio'))
        for ref in tiers['values']:
            cls, key = int(ref['class'], 16), int(ref['key'], 16)
            gear = attrib.field(cls, key, 0x364efa6a)
            if gear:
                paths.update('SOUND/SHIFTING/' + v['text'].upper() for v in gear['values'])
            for field in (0x9aed4ffe, 0xb3186761):
                ng = attrib.field(cls, key, field)['values'][0]
                nc, nk = int(ng['class'], 16), int(ng['key'], 16)
                for field in (0x0eb1c9d2, 0xdfb61b9c):
                    value = attrib.field(nc, nk, field)
                    paths.update('SOUND/ENGINE/' + v['text'].upper() for v in value['values'])
                banks = attrib.field(nc, nk, 0xa7468316)
                if banks:
                    for v in banks['values']:
                        name = v['text'].upper()
                        paths.add(('SOUND/IG_GLOBAL/' if name == 'CAR_WHINE.ABK' else 'SOUND/ENGINE/') + name)
    assert len(paths) == 83 and sum(p.endswith('.TMX') for p in paths) == 64
    assert len({name_hash(p.replace('/', '\\')) for p in paths}) == 83
    return sorted(paths)


def xma_decode(sound, ffmpeg):
    """Independently wrap EA blocks as RIFF XMA1 for bundled FFmpeg."""
    packets = bytearray()
    for block in sound.blocks:
        payload = block.payload[4:]  # Remove the EA inner-size word only.
        for at in range(0, len(payload), 2048):
            packets += payload[at:at+2048].ljust(2048, b'\0')
    extra = bytearray(28); extra[4] = 1; extra[25] = sound.channels
    struct.pack_into('<I', extra, 12, sound.rate)
    fmt = struct.pack('<HH', 0x165, 16) + extra
    body = b'WAVEfmt ' + struct.pack('<I', len(fmt)) + fmt + b'data' + struct.pack('<I', len(packets)) + packets
    wav = b'RIFF' + struct.pack('<I', len(body)) + body
    result = subprocess.run([str(ffmpeg), '-v', 'error', '-nostdin', '-f', 'wav', '-i', 'pipe:0',
                             '-map', '0:a:0', '-c:a', 'pcm_s16le', '-f', 'wav', 'pipe:1'],
                            input=wav, capture_output=True, timeout=30)
    assert result.returncode == 0 and not result.stderr, result.stderr[-1000:]
    at = 12
    while at + 8 <= len(result.stdout):
        tag, size = struct.unpack_from('<4sI', result.stdout, at)
        if tag == b'data':
            pcm = result.stdout[at+8:]
            assert len(pcm) == sound.frames * sound.channels * 2 and any(pcm)
            return pcm
        at += 8 + size + (size & 1)
    raise AssertionError('FFmpeg output missing PCM')


def private_proof(scratch, original=False):
    attributes = input_path('Need for Speed Undercover/GLOBAL/ATTRIBUTES.BIN')
    override = os.environ.get('NFS_UC_X360_ISO')
    images = [Path(override)] if override else sorted((input_path().parent / 'ext_untrusted/undercover_x360').glob('*.iso'))
    ffmpeg = ROOT / 'third_party/ffmpeg/bin/ffmpeg'
    if not attributes.is_file() or len(images) != 1 or not images[0].is_file() or not ffmpeg.is_file():
        print('SKIP private corpus: source attributes, console ISO or bundled decoder absent; fixtures completed')
        return False
    paths = closure(attributes.read_bytes())
    spec = importlib.util.spec_from_file_location('xas_oracle', ROOT / 'tests/setup/test_car_audio_codec.py')
    oracle = importlib.util.module_from_spec(spec); spec.loader.exec_module(oracle)
    exe = oracle.build_oracle(scratch)
    if exe is None:
        print('SKIP private PCM oracle: bundled decoder libraries absent; fixtures completed')
        return False
    counts = collections.Counter()
    digests, xma_pcm = {}, hashlib.sha256()
    with uc.Disc(images[0]) as disc:
        assert disc.zdir_sha256 == '113dc7fee54bb9d8355200e298cb51c45663faf9885dbf040ef66f12369a2e0e'
        for path in paths:
            data = disc.read(path); digests[path] = hashlib.sha256(data).hexdigest()
            counts['reads'] += 1
            if path.endswith('.TMX'):
                try: car_audio_prostreet.tmx(data)
                except ValueError: counts['old_tmx_rejected'] += 1
                if original: continue
                report = uc.tmx(data)
                assert report['source_sha256'] == digests[path]
                for track in report['tracks']:
                    raw = data[track['offset']:track['offset'] + track['span_size']]
                    assert hashlib.sha256(raw).hexdigest() == track['source_sha256']
                    if track['kind'] == 'gin':
                        normalized = track['data']; car_audio_pack.validate_gin(normalized)
                        freqs, cycles = struct.unpack_from('<2I', raw, 16)
                        terminal = struct.unpack_from('<I', raw, 36 + 4*(freqs+cycles))[0] + 1
                        assert struct.unpack_from('<I', normalized, 24)[0] == terminal
                        assert normalized[:24] + normalized[28:] == raw[:24] + raw[28:len(normalized)]
                        try: car_audio_pack.validate_gin(raw[:len(normalized)])
                        except ValueError: counts['uncorrected_rejected'] += 1
                        try: uc.normalize_gin(raw[:-4])
                        except ValueError: counts['truncated_gin_rejected'] += 1
                        counts['gin'] += 1
                        bad = bytearray(normalized); struct.pack_into('<I', bad, 24, terminal + 32)
                        try: car_audio_pack.validate_gin(bytes(bad))
                        except ValueError: counts['bad_gin_rejected'] += 1
                    elif track['sound'].codec == 4:
                        sound = track['sound']; pcm = codec.decode_sound(sound).pcm
                        # Rehouse the same SNR into a PC envelope for the existing
                        # independently parsed, vendored FFmpeg XAS oracle.
                        src = track['data']
                        s10 = b'S10A' + struct.pack('>3I', 0, 1, 16) + src
                        envelope = bytearray(96); envelope[:4] = b'ABKC'
                        struct.pack_into('<I', envelope, 20, 96+len(s10))
                        struct.pack_into('<2I', envelope, 32, 96, len(s10))
                        expected = oracle.oracle(exe, bytes(envelope)+s10, scratch)[0]
                        assert pcm == expected[1]
                        counts['xas'] += 1; counts['xas_frames'] += sound.frames
                        try: codec.parse_snr(src[:-1])
                        except ValueError: counts['truncated_xas_rejected'] += 1
                    else:
                        pcm = xma_decode(track['sound'], ffmpeg); xma_pcm.update(pcm)
                        counts['xma'] += 1; counts['tmx_xma_frames'] += track['sound'].frames
            else:
                try: codec.parse_bank(data)
                except ValueError: counts['old_abk_rejected'] += 1
                if original: continue
                sounds = uc.bank(data)
                counts['null'] += sum(s is None for s in sounds)
                for sound in sounds:
                    if sound is None: continue
                    pcm = xma_decode(sound, ffmpeg); xma_pcm.update(pcm)
                    counts['xma'] += 1; counts['abk_xma_frames'] += sound.frames
                    try: codec.parse_snr(data, offset=sound.header_offset, end=sound.end_offset-1)
                    except ValueError: counts['truncated_xma_rejected'] += 1
            truncated = data[:-4] if path.endswith('.TMX') else data[:-1]
            try: (uc.tmx if path.endswith('.TMX') else uc.bank)(truncated)
            except ValueError: counts['truncated_source_rejected'] += 1
            assert hashlib.sha256(data).hexdigest() == digests[path]
        # Re-read from the read-only source, independently of normalized copies.
        assert all(hashlib.sha256(disc.read(p)).hexdigest() == digest for p, digest in digests.items())
    print('corpus:', dict(counts), 'source hashes unchanged:', len(digests))
    assert counts['old_tmx_rejected'] == 64 and counts['old_abk_rejected'] == 19
    if original:
        raise AssertionError('original reader rejects 83/83 required source files')
    for key, expected in dict(reads=83, gin=151, xas=101, xma=337, null=144,
                              uncorrected_rejected=42, bad_gin_rejected=151,
                              truncated_xas_rejected=101, truncated_xma_rejected=336,
                              truncated_source_rejected=83, truncated_gin_rejected=151,
                              xas_frames=11718728, abk_xma_frames=5077075, tmx_xma_frames=53286).items():
        assert counts[key] == expected, (key, counts[key], expected)
    print('XMA PCM aggregate SHA-256:', xma_pcm.hexdigest())
    return True


MUTANTS = {
    'gin-count': ('uc', "struct.pack_into('<I', result, 24, terminal)", 'pass'),
    'gin-endian': ('uc', "struct.unpack_from('<ff4I', data, 8)", "struct.unpack_from('>ff4I', data, 8)"),
    'tmx-endian': ('uc', "struct.unpack('>I', span(data, at, 4, 'TMX field bounds'))", "struct.unpack('<I', span(data, at, 4, 'TMX field bounds'))"),
    'duplicate-hash': ('uc', "identity not in seen, 'ZDIR duplicate hash'", "True, 'ZDIR duplicate hash'"),
    'span-bounds': ('uc', 'size <= entry.size - start', 'True'),
    'snr-block': ('codec', 'size == expected', 'True'),
    'snr-endian': ('codec', "_words(data, offset, '>2I', end)", "_words(data, offset, '<2I', end)"),
}


def mutate(name):
    global uc, codec
    target, before, after = MUTANTS[name]
    original = uc if target == 'uc' else codec
    source = Path(original.__file__).read_text()
    assert source.count(before) == 1
    module = types.ModuleType('setup_kit.audio_source_mutant'); module.__package__ = 'setup_kit'
    sys.modules[module.__name__] = module
    exec(compile(source.replace(before, after), original.__file__, 'exec'), module.__dict__)
    if target == 'uc': uc = module
    else: codec = module; uc.codec = module


if __name__ == '__main__':
    if '--mutant' in sys.argv:
        mutate(sys.argv[sys.argv.index('--mutant') + 1])
    result = unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromTestCase(Boundaries))
    if not result.wasSuccessful(): sys.exit(1)
    with tempfile.TemporaryDirectory(prefix='car-audio-undercover-') as folder:
        sys.exit(0 if private_proof(Path(folder), '--original' in sys.argv) else 3)
