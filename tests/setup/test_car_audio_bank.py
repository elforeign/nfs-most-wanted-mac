"""MW sample-bank proof; original reader pinned to main 13984ab7.

Plan: feed source S10A banks to the original C MW reader, then compare generated
BNKl/PT through that same reader and a separate strict Python parser. Verify
source indices, selector order, loops, all PCM bytes and relocated extents.
--original fails on the original incompatibility, not a missing module import.
--mutant slot-number/extent/loop-tag exercises the same acceptance checks.
Checkout: 30be3b7745e7f865235b53a738802b27be4eed70 (A1+A2 dependency base).
Measured original: 69/69 source banks rejected by the pinned C MW reader;
--original exits 1. Seven synthetic checks pass. Full inventory round-trip:
69 banks, 641 samples, 313 loops, 69 tables, 1041 rows (400 empty), 44,981,490
PCM bytes; zero identity/PCM/loop/selector mismatches. Each of slot-number,
extent and loop-tag mutants exits 1 with four failed checks.
Fragment pin regression: before repair, 1 setup error / 0 checks; after,
7/7 checks pass. A BNKl-to-S10A fragment mutation fails its SHA-256 check.
No complete executable ABK or runtime admission is claimed by these envelopes.
"""
from dataclasses import replace
import hashlib
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import types
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRATCH = ROOT / '.metal-cache/car-audio-bank'
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from setup_kit import car_audio_bank as writer
from setup_kit import car_audio_codec as codec
from test_car_audio_codec import bank as source_bank, unit

# Only the original MW bank reader and its two helpers are frozen. Unrelated
# production code is neither hashed nor linked into this historical proof.
BASELINE_SHA = '421cec60167eccdc0a59526da4ab70e6d16d7fa868f07d48aa10fe8ade532a56'
BASELINE_C = r'''static int fail(char *why, size_t n, const char *fmt, unsigned a, unsigned b) { snprintf(why, n, fmt, a, b); return 0; }
static uint32_t le(const uint8_t *p) { return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24; }

int x360abk_pc_bank(const uint8_t *f, size_t size, X360AbkPcSlot *slots, unsigned max, unsigned *count, char *why, size_t n) {
    if (size < 0x60 || memcmp(f, "ABKC", 4) || le(f + 0x14) != size) return fail(why, n, "PC: not ABKC / size", 0, 0);
    uint32_t b = le(f + 0x20), ext = le(f + 0x24);
    if (b < 0x60 || ext < 0x14 || ext > size - b || memcmp(f + b, "BNKl", 4)) return fail(why, n, "PC: no BNKl at %08X", b, 0);
    unsigned cnt = f[b + 6] | f[b + 7] << 8;
    if (cnt > max || 0x14 + 4 * cnt > ext) return fail(why, n, "PC: BNKl slot count %u", cnt, 0);
    *count = cnt;
    for (unsigned i = 0; i < cnt; ++i) {
        X360AbkPcSlot *s = &slots[i];
        memset(s, 0, sizeof *s);
        uint32_t at = b + 0x14 + 4 * i, rel = le(f + at);
        if (!rel) continue;
        uint32_t p = at + rel;
        if (rel > ext || p + 5 > b + ext || memcmp(f + p, "PT\0\0", 4)) return fail(why, n, "PC: slot %u PT at %08X", i, p);
        s->present = 1; s->channels = 1; s->rate = 22050; s->codec = 10; s->loop_start = s->loop_end = -1;
        for (p += 4; p < b + ext;) {
            unsigned tag = f[p++];
            if (tag == 0xff) break;
            if (tag == 0xfc || tag == 0xfd || tag == 0xfe) continue;
            unsigned len = f[p++];
            if (len > 4 || p + len > b + ext) return fail(why, n, "PC: slot %u bad tag %02X", i, tag);
            int32_t v = len && (f[p] & 0x80) ? -1 : 0;
            for (unsigned j = 0; j < len; ++j) v = (int32_t)((uint32_t)v << 8 | f[p++]);
            if (tag == 0x82) s->channels = v; else if (tag == 0x84) s->rate = v; else if (tag == 0x85) s->samples = v;
            else if (tag == 0x86) s->loop_start = v; else if (tag == 0x87) s->loop_end = v; else if (tag == 0x88) s->data = v;
            else if (tag == 0xa0) s->codec = v;
        }
        if (s->data >= ext) return fail(why, n, "PC: slot %u data offset outside bank", i, 0);
        s->data += b;
    }
    return 1;
}
'''
READER_C = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "x360abk.h"
static void put(unsigned v) { for(unsigned i=0;i<4;i++) putchar(v>>(i*8)); }
int main(int argc, char **argv) {
    if(argc!=2) return 2;
    FILE *f=fopen(argv[1],"rb"); if(!f) return 2;
    fseek(f,0,SEEK_END); long n=ftell(f); rewind(f);
    if(n<96 || n>268435456) return 2;
    unsigned char *d=malloc(n); if(!d) return 2;
    if(fread(d,1,n,f)!=(size_t)n) return 2; fclose(f);
    X360AbkPcSlot slots[4097]; unsigned count=0; char why[256];
    int ok=x360abk_pc_bank(d,n,slots,4097,&count,why,sizeof why);
    if(!ok) { fprintf(stderr,"%s\n",why); free(d); return 1; }
    put(count);
    for(unsigned i=0;i<count;i++) {
        X360AbkPcSlot *s=slots+i; put(s->present);
        if(!s->present) continue;
        put(s->channels); put(s->rate); put(s->codec); put(s->samples);
        put(s->loop_start); put(s->loop_end); put(s->data);
    }
    free(d); return 0;
}
'''


def build_reader(folder):
    ff = ROOT / 'third_party/ffmpeg'
    if not shutil.which('clang') or not (ff / 'lib/libavcodec.a').is_file():
        return None
    assert hashlib.sha256(BASELINE_C.encode()).hexdigest() == BASELINE_SHA
    path, exe = folder / 'reader.c', folder / 'reader'
    path.write_text(READER_C + BASELINE_C)
    result = subprocess.run(['clang', '-O2', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
                             '-I' + str(ff / 'include'), '-I' + str(ROOT / 'runtime'), str(path),
                             str(ff / 'lib/libavcodec.a'),
                             str(ff / 'lib/libavutil.a'), '-lz', '-o', str(exe)],
                            capture_output=True, timeout=90)
    (folder / 'compile.log').write_bytes(result.stdout + result.stderr)
    assert result.returncode == 0, result.stderr.decode(errors='replace')[-2000:]
    return exe


def c_read(exe, data, folder):
    path = folder / 'input.abk'
    path.write_bytes(data)
    return subprocess.run([str(exe), str(path)], capture_output=True, timeout=30)


def envelope(artifact):
    """Test-only ABKC envelope: no executable modules and no serving contract."""
    assert artifact.offset >= 96
    data = bytearray(artifact.offset) + artifact.data
    data[:4] = b'ABKC'
    struct.pack_into('<I', data, 20, len(data))
    struct.pack_into('<I', data, 24, artifact.bank_offset)
    struct.pack_into('<2I', data, 32, artifact.bank_offset, artifact.bank_extent)
    return bytes(data)


def mw_parse(data):
    """Independent bounded MW wire reader, including exact PCM extents."""
    def get(at, fmt, end):
        assert 0 <= at <= end <= len(data) and at + struct.calcsize(fmt) <= end
        return struct.unpack_from(fmt, data, at)
    assert data[:4] == b'ABKC' and get(20, '<I', len(data))[0] == len(data)
    base, extent = get(32, '<2I', len(data))
    end = base + extent
    assert base >= 96 and end <= len(data)
    magic, version, count, used, r1, r2 = get(base, '<4sHH3I', end)
    assert magic == b'BNKl' and version == 5 and r1 == r2 == 0
    assert 20 + 4 * count <= used <= extent and extent % 128 == 0
    slots, ranges = [], []
    for i in range(count):
        site = base + 20 + 4 * i
        rel, = get(site, '<I', base + used)
        if not rel:
            slots.append(None)
            continue
        at = site + rel
        assert data[at:at + 4] == b'PT\0\0'
        start, at, tags, channel = at, at + 4, {}, False
        while True:
            tag, = get(at, 'B', base + used)
            at += 1
            if tag == 255:
                break
            if tag == 253:
                assert not channel
                channel = True
                continue
            length, = get(at, 'B', base + used)
            at += 1
            assert tag not in tags and 0 <= length <= 4 and at + length <= base + used
            tags[tag] = int.from_bytes(data[at:at + length], 'big', signed=True)
            at += length
        assert channel and tags[128] == 2 and tags[130] == 1 and tags[160] == 8
        rate, frames = tags[132], tags[133]
        loop = tags.get(134, -1), tags.get(135, -1)
        assert rate > 0 and frames > 0
        assert loop == (-1, -1) or 0 <= loop[0] <= loop[1] < frames
        pcm_start = base + tags[136]
        pcm_end = pcm_start + frames * 2
        assert base + 20 + count * 4 <= pcm_start < pcm_end <= base + used
        ranges.extend([(start, at), (pcm_start, pcm_end)])
        slots.append(((1, rate, 8, frames, *loop, pcm_start), data[pcm_start:pcm_end]))
    ranges.sort()
    assert all(a[1] <= b[0] for a, b in zip(ranges, ranges[1:]))
    assert not any(data[base + used:end])
    return slots


def source_fixture(*, rows=(2, None, 0, 2), empty=False, tables=1):
    raw = source_bank(rate=44100)
    old_base, size = struct.unpack_from('<2I', raw, 32)
    base = 1024
    data = bytearray(base) + raw[old_base:old_base + size]
    data[:96] = raw[:96]
    # Two source pointers may alias one selector table. No copied module code.
    for i in range(tables):
        table = 256 + 128 * i
        struct.pack_into('<2I', data, 96 + i * 8, table, table)
        struct.pack_into('<I', data, table, 0 if empty else len(rows))
        if not empty:
            for j, index in enumerate(rows):
                values = (65535, 0, 0) if index is None else (60 << 16 | index, index + 1, 0xffffffff)
                struct.pack_into('<3I', data, table + 4 + 12 * j, *values)
    f1 = len(data)
    data += bytes(4)
    f2 = len(data)
    sites = [96 + i * 8 + j * 4 for i in range(tables) for j in range(2)]
    data += struct.pack('<I', len(sites)) + struct.pack('<' + 'I' * len(sites), *sites)
    f3 = len(data)
    data += bytes(4)
    struct.pack_into('<I', data, 20, len(data))
    struct.pack_into('<2I', data, 32, base, size)
    struct.pack_into('<3I', data, 48, f1, f2, f3)
    return bytes(data)


def verify(source, artifact, decoded, exe=None, folder=None):
    data = envelope(artifact)
    slots = mw_parse(data)
    assert len(slots) == len(decoded) + 1 and slots[0] is None
    digest = hashlib.sha256(source).hexdigest()
    assert artifact.source_sha256 == digest
    assert artifact.bank_offset % 128 == 0
    assert artifact.bank_offset + artifact.bank_extent == len(data)
    assert artifact.bank_used == struct.unpack_from('<I', data, artifact.bank_offset + 8)[0]
    assert artifact.pcm_bytes == sum(len(d.pcm) for d in decoded if d)
    assert len(artifact.identities) == len(slots)
    assert artifact.identities[0] is None
    for i, item in enumerate(decoded):
        got = slots[i + 1]
        if item is None:
            assert got is None and artifact.identities[i + 1] is None
            continue
        s = item.sound
        loop = (-1, -1) if s.loop_start is None else (s.loop_start, s.loop_end)
        assert got[0][:-1] == (s.channels, s.rate, 8, s.frames, *loop)
        assert got[1] == item.pcm
        assert artifact.identities[i + 1] == (digest, i)
    for table in artifact.selectors:
        count, = struct.unpack_from('<I', source, table.source.source_offset)
        assert len(table.source.indices) == count
        assert data[table.offset:table.offset + len(table.data)] == table.data
        assert struct.unpack_from('<I', data, table.offset)[0] == count
        for j in range(count):
            w, slot, reserved = struct.unpack_from('<3I', source, table.source.source_offset + 4 + 12 * j)
            idx = None if (w, slot, reserved) == (65535, 0, 0) else w & 65535
            want_slot = 0 if idx is None else idx + 1
            assert table.source.indices[j] == idx
            assert struct.unpack_from('<3I', data, table.offset + 4 + j * 12) == (0xff00, want_slot, 0)
            assert slots[want_slot] is None if idx is None else slots[want_slot][1] == decoded[idx].pcm
    if exe:
        result = c_read(exe, data, folder)
        assert result.returncode == 0, result.stderr.decode(errors='replace')
        expected = struct.pack('<I', len(slots))
        for slot in slots:
            expected += struct.pack('<I', int(slot is not None))
            if slot:
                expected += struct.pack('<4I2iI', *slot[0])
        assert result.stdout == expected
    return slots


MUTATIONS = {
    'slot-number': ('site = 20 + 4 * (index + 1)', 'site = 20 + 4 * index'),
    'extent': ('_pt(item.sound, p)', '_pt(item.sound, p + 4)'),
    'loop-tag': ('_tag(0x87, -1 if sound.loop_end is None else sound.loop_end)',
                 '_tag(0x87, -1 if sound.loop_end is None else sound.loop_end - 1)'),
}


def mutated(name):
    source = Path(writer.__file__).read_text()
    before, after = MUTATIONS[name]
    assert source.count(before) == 1
    module = types.ModuleType('setup_kit.audio_bank_mutant')
    sys.modules[module.__name__] = module
    exec(compile(source.replace(before, after), writer.__file__, 'exec'), module.__dict__)
    return module


class BankProof(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(prefix='proof-', dir=SCRATCH)
        cls.folder = Path(cls.temp.name)
        cls.exe = build_reader(cls.folder)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def check(self, source, **kwargs):
        decoded = codec.decode_bank(source)
        artifact = writer.build_sample_bank(source, decoded, **kwargs)
        verify(source, artifact, decoded, self.exe, self.folder)
        return artifact

    def test_first_last_null_multiple_loops_and_aliases(self):
        source = source_fixture(tables=2)
        artifact = self.check(source)
        self.assertEqual(len(artifact.selectors), 2)
        self.assertEqual(artifact.selectors[0].source.pointer_sites, (96, 100))
        self.assertEqual(artifact.selectors[0].source.indices, (2, None, 0, 2))
        self.assertEqual(artifact.pcm_bytes, 522)
        self.assertEqual(sum(d.sound.loop_start is not None for d in codec.decode_bank(source) if d), 2)

    def test_one_shot_empty_tables_all_null_and_zero_sounds(self):
        self.check(source_bank(loop_start=None))
        source = source_fixture(empty=True)
        artifact = self.check(source, table_offsets=(256,))
        self.assertEqual(artifact.selectors[0].data, bytes(4))
        self.assertEqual(writer.source_selectors(source, codec.parse_bank(source)), ())
        self.check(source_bank([None, None], loop_start=None))
        self.check(source_bank([], loop_start=None))
        self.check(source_fixture(rows=(None, None)))

    def test_relocation_and_signed_tag_boundaries(self):
        source = source_fixture()
        for offset in (96, 129, 4096, 65535):
            artifact = self.check(source, offset=offset)
            self.assertEqual(artifact.selectors[0].offset, offset)
        for rate in (127, 128, 255, 256, 65535, 65536):
            self.check(source_bank([[(128, unit())]], rate=rate, loop_start=127))

    def test_decoded_identity_lengths_and_allocation_bounds(self):
        source = source_fixture()
        decoded = codec.decode_bank(source)
        bad = list(decoded)
        for pcm in (decoded[0].pcm[:-2], decoded[0].pcm + bytes(2)):
            bad[0] = replace(decoded[0], pcm=pcm)
            with self.assertRaisesRegex(ValueError, 'PCM byte count'):
                writer.build_sample_bank(source, bad)
        bad[0] = replace(decoded[0], sound=replace(decoded[0].sound, slot=2))
        with self.assertRaisesRegex(ValueError, 'source identity'):
            writer.build_sample_bank(source, bad)
        with self.assertRaisesRegex(ValueError, 'slot count'):
            writer.build_sample_bank(source, decoded[:-1])
        bad = list(decoded); bad[1] = decoded[0]
        with self.assertRaisesRegex(ValueError, 'null slot'):
            writer.build_sample_bank(source, bad)
        for kwargs in ({'offset': -1}, {'offset': 1 << 31}, {'max_bytes': 10},
                       {'max_bytes': 700}, {'max_bytes': 0}, {'max_bytes': writer.MAX_BYTES + 1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                writer.build_sample_bank(source, **kwargs)
        with self.assertRaisesRegex(ValueError, 'stereo'):
            writer.build_sample_bank(source_bank(channels=2))

    def test_source_selector_mutations(self):
        source = source_fixture()
        f2, = struct.unpack_from('<I', source, 52)
        for at, value in ((260, 60 << 16 | 1), (264, 1), (268, 0),
                          (256, 257), (f2 + 4, 0xffffffff), (96, 0xffffffff),
                          (52, len(source)), (48, 0)):
            broken = bytearray(source)
            struct.pack_into('<I', broken, at, value)
            with self.subTest(at=at), self.assertRaises(ValueError):
                writer.build_sample_bank(bytes(broken), table_offsets=(256,))
        broken = bytearray(source)
        struct.pack_into('<I', broken, 256, 257)
        with self.assertRaisesRegex(ValueError, 'extent/count'):
            writer.build_sample_bank(bytes(broken))
        for tables in ((260,), (256, 256)):
            with self.assertRaises(ValueError):
                writer.build_sample_bank(source, table_offsets=tables)

    def test_source_rejected_by_original_mw_reader(self):
        if self.exe is None:
            self.skipTest('local C reader toolchain absent')
        result = c_read(self.exe, source_fixture(), self.folder)
        self.assertEqual(result.returncode, 1)
        self.assertIn(b'PC: no BNKl', result.stderr)

    def test_resource_identity_and_internal_mutants(self):
        source = source_fixture()
        artifact = self.check(source)
        other = bytearray(source); other[90] ^= 1
        other_artifact = self.check(bytes(other))
        self.assertNotEqual(artifact.identities, other_artifact.identities)
        # Each corruption is rejected by the same complete acceptance check.
        for name in MUTATIONS:
            broken = mutated(name).build_sample_bank(source)
            with self.subTest(name=name), self.assertRaises(AssertionError):
                verify(source, broken, codec.decode_bank(source), self.exe, self.folder)


def private_proof(exe, folder, *, original=False):
    from setup_kit import car_audio as graph
    source = Path(os.environ.get('NFS_CARBON_PC', Path.home() / 'Downloads/NFSC'))
    roster = ROOT / 'tools_snapshot/private_cars.py'
    attributes = source / 'GLOBAL/attributes.bin'
    if not attributes.is_file() or not roster.is_file() or exe is None:
        print('SKIP private inputs/toolchain absent; synthetic proof completed')
        return False
    cars = re.findall(r'\|C/([^|\n]+)\|', roster.read_text())
    assert cars
    files = {p.relative_to(source).as_posix(): p.read_bytes()
             for p in (source / 'SOUND/ENGINE').rglob('*') if p.suffix.lower() in ('.abk', '.gin')}
    inventory = graph.inventory(attributes.read_bytes(), cars, files)
    assert not inventory['unsupported'], inventory['unsupported']
    resources = {r['path']: r for g in inventory['supported'].values()
                 for r in g['resources'] if r['kind'] == 'abk'}
    rejected = samples = loops = tables = rows = nulls = pcm_bytes = 0
    for path, resource in sorted(resources.items()):
        data = files[path]
        result = c_read(exe, data, folder)
        assert result.returncode == 1 and b'PC: no BNKl' in result.stderr
        rejected += 1
        if original:
            continue
        decoded = codec.decode_bank(data)
        artifact = writer.build_sample_bank(data, decoded, offset=0x1001)
        verify(data, artifact, decoded, exe, folder)
        assert artifact.source_sha256 == resource['sha256']
        # Enumerate source tables independently from all reachable pointer
        # targets with authored row sentinel words, not the writer's classifier.
        b, = struct.unpack_from('<I', data, 32)
        f2, = struct.unpack_from('<I', data, 52)
        n, = struct.unpack_from('<I', data, f2)
        targets = {struct.unpack_from('<I', data, struct.unpack_from('<I', data, f2 + 4 + i * 4)[0])[0]
                   for i in range(n)}
        expected = set()
        for t in targets:
            count, = struct.unpack_from('<I', data, t)
            if not 0 < count <= 256 or t + 4 + 12 * count > b:
                continue
            source_rows = [struct.unpack_from('<3I', data, t + 4 + 12 * j) for j in range(count)]
            if all(z == 0xffffffff or (w, s, z) == (65535, 0, 0) for w, s, z in source_rows):
                expected.add(t)
        assert {t.source.source_offset for t in artifact.selectors} == expected
        assert len(decoded) == len(resource['sounds'])
        for i, d in enumerate(decoded):
            r = resource['sounds'][i]
            if d is None:
                assert r['empty']
                continue
            assert r['slot'] == i == d.sound.slot
            assert (r['frames'], r['loop_start']) == (d.sound.frames, d.sound.loop_start)
            samples += 1; loops += d.sound.loop_start is not None
        tables += len(artifact.selectors)
        rows += sum(len(t.source.indices) for t in artifact.selectors)
        nulls += sum(i is None for t in artifact.selectors for i in t.source.indices)
        pcm_bytes += artifact.pcm_bytes
    print(f'original 13984ab7 MW reader: {rejected}/{len(resources)} source banks rejected: PC: no BNKl')
    assert rejected == len(resources) > 0
    if original:
        raise AssertionError(f'original MW reader incompatible with {rejected}/{rejected} source banks')
    print(f'round-trip: {len(resources)} banks, {samples} samples, {loops} loops, {tables} tables, '
          f'{rows} rows ({nulls} empty), {pcm_bytes} PCM bytes; identity/PCM/loop/selector mismatches=0')
    return True


if __name__ == '__main__':
    SCRATCH.mkdir(parents=True, exist_ok=True)
    if '--mutant' in sys.argv:
        writer = mutated(sys.argv[sys.argv.index('--mutant') + 1])
    result = unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromTestCase(BankProof))
    if not result.wasSuccessful():
        sys.exit(1)
    with tempfile.TemporaryDirectory(prefix='inventory-', dir=SCRATCH) as name:
        folder = Path(name)
        exe = build_reader(folder)
        sys.exit(0 if private_proof(exe, folder, original='--original' in sys.argv) else 3)
