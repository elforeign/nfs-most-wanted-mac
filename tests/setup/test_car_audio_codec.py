"""Resident codec proof, baseline behaviour from 13984ab7 (also at 026e442e).

The linked original x360abk_decode rejects real codec-4 samples, independently
of the new reader. FFmpeg's compiled adpcm_ea_xas is the PCM oracle; its C
harness reads source headers and trims blocks independently of Python.
Synthetic fixtures contain no private data. Missing private inputs exit 3 only
after synthetic checks. --original demonstrates the old failure; --mutant NAME
runs the same checks against a deliberately broken implementation.

Measured: original rejects 641/641; new reader matches 69 banks, 641 sounds,
22,490,745 frames, 644 blocks and 313 loops with zero PCM/metadata differences.
Six synthetic checks pass; missing assets exit 3. All nine mutants exit 1:
truncation 13 failures; block bounds 1 failure/1 error; channel layout 1;
nibble order 3; predictor 2; trim 3 errors; loop end 2; rounding 2; signed PCM 2.
Strict oracle compile regression: before cleanup, 2 unused-variable errors /
0 checks; after, -Werror build and 6/6 checks pass.
"""
import sys as _inputs_sys
from pathlib import Path as _InputsPath
_inputs_sys.path.insert(0, str(_InputsPath(__file__).resolve().parents[2] / "tools"))
from setup_kit.input_paths import input_path
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
SCRATCH = ROOT / '.metal-cache/car-audio-codec'
sys.path.insert(0, str(ROOT / 'tools'))
from setup_kit import car_audio_codec as codec

# This harness calls the actual vendored FFmpeg decoder, not a transcription of
# its predictor loop. Its container walk shares no parsing code with the reader.
ORACLE_C = r'''
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <libavcodec/avcodec.h>
#include <libavutil/channel_layout.h>
#include "x360abk.h"
#define CHECK(c) do { if (!(c)) { fprintf(stderr, "oracle line %d\n", __LINE__); exit(2); } } while(0)
static unsigned be(const uint8_t *p) { return (unsigned)p[0]<<24 | (unsigned)p[1]<<16 | p[2]<<8 | p[3]; }
static unsigned le(const uint8_t *p) { return p[0] | p[1]<<8 | p[2]<<16 | (unsigned)p[3]<<24; }
static void put(unsigned n) { for(int k=0;k<4;k++) CHECK(putchar((n>>(8*k))&255)!=EOF); }
int main(int argc, char **argv) {
    CHECK(argc==3);
    int baseline = !strcmp(argv[1], "baseline");
    FILE *f=fopen(argv[2], "rb"); CHECK(f);
    CHECK(!fseek(f,0,SEEK_END)); long len=ftell(f); CHECK(len>=96);
    rewind(f); uint8_t *d=malloc(len); CHECK(d);
    CHECK(fread(d,1,len,f)==(size_t)len); fclose(f);
    CHECK(!memcmp(d,"ABKC",4) && le(d+20)==(unsigned)len);
    unsigned b=le(d+32), size=le(d+36); CHECK(b<=len && size<=len-b && size>=12);
    CHECK(!memcmp(d+b,"S10A",4) && !be(d+b+4));
    unsigned count=be(d+b+8); CHECK(count<=4096 && 12+4*count<=size);
    unsigned rejected=0;
    if(!baseline) put(count);
    for(unsigned i=0;i<count;i++) {
        unsigned rel=be(d+b+12+4*i);
        if(rel==0xffffffffu) { if(!baseline) put(0); continue; }
        unsigned stop=b+size;
        for(unsigned j=i+1;j<count;j++) {
            unsigned next=be(d+b+12+4*j);
            if(next!=0xffffffffu) { stop=b+next; break; }
        }
        CHECK(rel>=12+4*count && rel<size && b+rel+8<=stop && stop<=b+size);
        unsigned pos=b+rel, a=be(d+pos), v=be(d+pos+4); pos+=8;
        unsigned channels=((a>>18)&63)+1, rate=a&0x3ffff, frames=v&0x1fffffff;
        unsigned loop=(v>>29)&1, start=0xffffffffu;
        if(loop) { CHECK(pos+4<=stop); start=be(d+pos); pos+=4; CHECK(start<frames); }
        CHECK((a>>28)==0 && ((a>>24)&15)==4 && !(v>>30) && channels<=2 && rate && frames);
        if(baseline) {
            X360AbkSound s={0}; s.codec=(a>>24)&15; s.storage=v>>30;
            s.channels=channels; s.rate=rate; s.samples=frames;
            s.loop=loop; s.loop_start=start; s.hdr=b+rel; s.data=pos; s.end=stop;
            int16_t *pcm=NULL; unsigned got=0; char why[160];
            int ok=x360abk_decode(d,&s,&pcm,&got,why,sizeof why);
            CHECK(!ok && !pcm && !got && !strcmp(why,"unsupported codec 4 storage 0"));
            rejected++; continue;
        }
        put(1); put(channels); put(rate); put(frames); put(start);
        put(loop ? frames-1 : 0xffffffffu); put(frames*channels*2);
        const AVCodec *dec=avcodec_find_decoder(AV_CODEC_ID_ADPCM_EA_XAS); CHECK(dec);
        AVCodecContext *c=avcodec_alloc_context3(dec); CHECK(c);
        av_channel_layout_default(&c->ch_layout,channels); c->sample_rate=rate;
        CHECK(avcodec_open2(c,dec,NULL)>=0);
        AVPacket *p=av_packet_alloc(); AVFrame *fr=av_frame_alloc(); CHECK(p && fr);
        unsigned total=0;
        while(total<frames) {
            CHECK(pos+8<=stop); unsigned bytes=be(d+pos), n=be(d+pos+4);
            CHECK(n && n<=frames-total && bytes==8+((n+127)/128)*76*channels && bytes<=stop-pos);
            unsigned left=n;
            for(unsigned off=pos+8;off<pos+bytes;off+=76*channels) {
                CHECK(av_new_packet(p,76*channels)>=0);
                memcpy(p->data,d+off,76*channels);
                CHECK(avcodec_send_packet(c,p)>=0); av_packet_unref(p);
                CHECK(avcodec_receive_frame(c,fr)>=0 && fr->nb_samples==128 && fr->format==AV_SAMPLE_FMT_S16P);
                unsigned take=left<128?left:128;
                for(unsigned k=0;k<take;k++) for(unsigned ch=0;ch<channels;ch++) {
                    unsigned value=(uint16_t)((int16_t *)fr->extended_data[ch])[k];
                    CHECK(putchar(value&255)!=EOF && putchar(value>>8)!=EOF);
                }
                left-=take; av_frame_unref(fr);
            }
            CHECK(!left); total+=n; pos+=bytes;
        }
        CHECK(pos==stop);
        av_packet_free(&p); av_frame_free(&fr); avcodec_free_context(&c);
    }
    free(d);
    if(baseline) { printf("original rejected %u\n",rejected); return 1; }
    CHECK(!fflush(stdout)); return 0;
}
'''


def unit(seed=0, *, predictor=None, shift=None, constant=False):
    """Generate varied histories, predictors, signed nibbles and saturation."""
    data = bytearray(76)
    for lane in range(4):
        p = (lane + seed) % 4 if predictor is None else predictor
        sh = (lane * 3 + seed) % 16 if shift is None else shift
        first = ((seed * 193 + lane * 1201) % 4096 - 2048) * 16
        second = ((seed * 317 + lane * 719) % 4096 - 2048) * 16
        struct.pack_into('<hh', data, lane * 4, first | p, second | sh)
    for i in range(60):
        data[16 + i] = 0 if constant else (seed * 73 + i * 37 + 0x18) & 255
    return bytes(data)


def bank(sounds=None, *, channels=1, loop_start=3, rate=32000):
    """Two trimmed blocks by default, plus a null slot between live samples."""
    if sounds is None:
        sounds = [[(7, b''.join(unit(c) for c in range(channels))),
                   (129, b''.join(unit(c + 4) for c in range(channels * 2)))], None,
                  [(125, b''.join(unit(c + 9) for c in range(channels)))]]
    s10 = bytearray(b'S10A' + struct.pack('>2I', 0, len(sounds)) + bytes(len(sounds) * 4))
    for slot, blocks in enumerate(sounds):
        if blocks is None:
            struct.pack_into('>I', s10, 12 + slot * 4, 0xffffffff)
            continue
        struct.pack_into('>I', s10, 12 + slot * 4, len(s10))
        frames = sum(n for n, _ in blocks)
        s10.extend(struct.pack('>2I', 4 << 24 | (channels - 1) << 18 | rate,
                               frames | (0x20000000 if loop_start is not None else 0)))
        if loop_start is not None:
            s10.extend(struct.pack('>I', loop_start))
        for frames, payload in blocks:
            s10.extend(struct.pack('>2I', len(payload) + 8, frames) + payload)
    raw = bytearray(96)
    raw[:4] = b'ABKC'
    struct.pack_into('<I', raw, 20, len(raw) + len(s10) + 13)
    struct.pack_into('<2I', raw, 32, 96, len(s10))
    return bytes(raw + s10 + b'outside-bank!')


def build_oracle(scratch):
    ff = ROOT / 'third_party/ffmpeg'
    if not shutil.which('clang') or not (ff / 'lib/libavcodec.a').is_file():
        return None
    path = scratch / 'oracle.c'
    path.write_text(ORACLE_C)
    exe = scratch / 'oracle'
    args = ['clang', '-O2', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function', '-I' + str(ff / 'include'),
            '-I' + str(ROOT / 'runtime'), str(path), str(ROOT / 'runtime/x360abk.c'),
            str(ff / 'lib/libavcodec.a'), str(ff / 'lib/libavutil.a'), '-lz', '-o', str(exe)]
    result = subprocess.run(args, capture_output=True, timeout=90)
    (scratch / 'compile.log').write_bytes(result.stdout + result.stderr)
    if result.returncode:
        raise AssertionError('oracle compile failed: ' + result.stderr.decode(errors='replace')[-3000:])
    return exe


def oracle(exe, data, scratch, *, baseline=False):
    path = scratch / 'input.abk'
    path.write_bytes(data)
    result = subprocess.run([str(exe), 'baseline' if baseline else 'decode', str(path)],
                            capture_output=True, timeout=60)
    if baseline:
        assert result.returncode == 1, result.stderr.decode(errors='replace')
        return int(result.stdout.decode().split()[-1])
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    out, pos = result.stdout, 4
    count, = struct.unpack_from('<I', out)
    decoded = []
    for _ in range(count):
        present, = struct.unpack_from('<I', out, pos)
        pos += 4
        if not present:
            decoded.append(None)
            continue
        channels, rate, frames, start, end, size = struct.unpack_from('<6I', out, pos)
        pos += 24
        assert size == frames * channels * 2 and pos + size <= len(out)
        decoded.append(((channels, rate, frames,
                         None if start == 0xffffffff else start,
                         None if end == 0xffffffff else end), out[pos:pos + size]))
        pos += size
    assert pos == len(out)
    return decoded


def metadata(sound):
    return (sound.channels, sound.rate, sound.frames, sound.loop_start, sound.loop_end)


# Each mutation changes production code in memory, without editing the checkout.
# All are checked by the same tests as the passing implementation.
MUTATIONS = {
    'truncation': ("_words(data, 0x14, '<I')[0] == len(data)",
                   "_words(data, 0x14, '<I')[0] >= len(data)"),
    'block-bounds': ('size == expected', 'size >= 8'),
    'channel-layout': ('for row in zip(*(p[:take] for p in planar)) for value in row',
                       'for p in planar for value in p[:take]'),
    'nibble-order': ('(byte >> 4, byte & 15)', '(byte & 15, byte >> 4)'),
    'predictor': ('_COEFFICIENTS[first & 15]', '_COEFFICIENTS[0]'),
    'trim': ('take = min(128, remaining)', 'take = 128'),
    'loop-end': ('frames - 1 if loop else None', 'frames if loop else None'),
    'rounding': ('previous * c1 + 128', 'previous * c1'),
    'signed-pcm': ('value = min(32767, max(-32768, value))',
                   'value = ((value + 32768) & 65535) - 32768'),
}


def mutated(name):
    source = Path(codec.__file__).read_text()
    before, after = MUTATIONS[name]
    assert source.count(before) == 1
    module = types.ModuleType('audio_codec_mutant')
    sys.modules[module.__name__] = module
    exec(compile(source.replace(before, after), codec.__file__, 'exec'), module.__dict__)
    return module


class CodecProof(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(prefix='codec-', dir=SCRATCH)
        cls.scratch = Path(cls.temp.name)
        cls.exe = build_oracle(cls.scratch)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_manual_signed_histories_nibbles_and_trim(self):
        data = bytearray(76)
        for lane in range(4):
            struct.pack_into('<hh', data, lane * 4, -32, 16 | 12)
        data[16:20] = bytes([0x1f] * 4)
        d = codec.decode_bank(bank([[(4, bytes(data))]], loop_start=1))[0]
        self.assertEqual(metadata(d.sound), (1, 32000, 4, 1, 3))
        self.assertEqual(struct.unpack('<4h', d.pcm), (-32, 16, 1, -1))
        self.assertEqual(d.sound.codec, 4)
        self.assertEqual(d.sound.storage, 0)

    def test_multi_block_trim_and_null_identity(self):
        data = bank()
        parsed = codec.parse_bank(data)
        self.assertEqual(len(parsed), 3)
        self.assertIsNone(parsed[1])
        got = codec.decode_bank(data)
        self.assertIsNone(got[1])
        self.assertEqual([d.sound.slot for d in got if d], [0, 2])
        self.assertEqual([len(d.pcm) // 2 for d in got if d], [136, 125])
        self.assertEqual(struct.unpack_from('<h', got[0].pcm, 7 * 2)[0],
                         struct.unpack_from('<h', unit(4))[0] & ~15)

    def test_oracle_pcm_and_metadata(self):
        if self.exe is None:
            self.skipTest('local FFmpeg oracle absent')
        for channels in (1, 2):
            for start in (None, 3):
                data = bank(channels=channels, loop_start=start)
                expected = oracle(self.exe, data, self.scratch)
                actual = codec.decode_bank(data)
                self.assertEqual(len(actual), len(expected))
                for got, want in zip(actual, expected):
                    if want is None:
                        self.assertIsNone(got)
                    else:
                        self.assertEqual(metadata(got.sound), want[0])
                        self.assertEqual(got.pcm, want[1])

    def test_all_predictors_shifts_and_saturation(self):
        if self.exe is None:
            self.skipTest('local FFmpeg oracle absent')
        sounds = [[(128, unit(seed=p * 16 + shift, predictor=p, shift=shift))]
                  for p in range(4) for shift in range(16)]
        data = bank(sounds, loop_start=None, rate=44100)
        expected = oracle(self.exe, data, self.scratch)
        actual = codec.decode_bank(data)
        for got, want in zip(actual, expected):
            self.assertEqual(got.pcm, want[1])
        samples = struct.unpack('<' + str(sum(len(x.pcm) for x in actual) // 2) + 'h',
                                b''.join(x.pcm for x in actual))
        self.assertIn(-32768, samples)
        self.assertIn(32767, samples)
        print('synthetic oracle: 64 predictor/shift cases, signed saturation, mono/stereo, two-block trim')

    def test_truncation_and_structural_bounds(self):
        data = bank()
        # Every physical truncation must fail, even inside the unrelated tail:
        # ABKC declares the whole file, while all sound reads use bank bounds.
        for end in range(len(data)):
            with self.subTest(end=end), self.assertRaises(ValueError):
                codec.parse_bank(data[:end])
        sound = codec.parse_bank(data)[0]
        base, = struct.unpack_from('<I', data, 32)
        for off, fmt, value in [(20, '<I', 1), (32, '<I', 0xfffffff0),
                                (36, '<I', 0xffffffff), (base + 4, '>I', 1),
                                (base + 8, '>I', 0xffffffff), (base + 12, '>I', 0),
                                (base + 20, '>I', 24), (base + 12, '>I', 0xfffffff0),
                                (sound.header_offset + 8, '>I', sound.frames)]:
            broken = bytearray(data)
            struct.pack_into(fmt, broken, off, value)
            with self.subTest(offset=off, value=value), self.assertRaises(ValueError):
                codec.parse_bank(broken)
        # Shorten only the S10A extent, keeping physical bytes and ABKC intact.
        # A decoder bounded by the file instead of the sound must fail this test.
        broken = bytearray(data)
        extent, = struct.unpack_from('<I', broken, 36)
        struct.pack_into('<I', broken, 36, extent - 1)
        with self.assertRaises(ValueError):
            codec.parse_bank(broken)

    def test_block_bounds_storage_and_headers(self):
        data = bank()
        sound = codec.parse_bank(data)[0]
        off = sound.blocks[0].offset
        # The shorter size retains a complete unit in the physical file. Only
        # validating the enclosing file would incorrectly admit this block.
        for at, value in [(off, 8), (off, 83), (off, 85), (off, 0xffffffff),
                          (off + 4, 0), (off + 4, sound.frames + 1),
                          (sound.header_offset, 3 << 24 | 44100),
                          (sound.header_offset, 1 << 28 | 4 << 24 | 44100),
                          (sound.header_offset, 4 << 24),
                          (sound.header_offset, 4 << 24 | 1 << 18 | 44100),
                          (sound.header_offset, 4 << 24 | 2 << 18 | 44100)]:
            broken = bytearray(data)
            struct.pack_into('>I', broken, at, value)
            with self.subTest(offset=at, value=value), self.assertRaises(ValueError):
                codec.parse_bank(broken)
        for storage in (1, 2, 3):
            broken = bytearray(data)
            old, = struct.unpack_from('>I', broken, sound.header_offset + 4)
            struct.pack_into('>I', broken, sound.header_offset + 4, old | storage << 30)
            with self.assertRaisesRegex(ValueError, 'unsupported S10A storage'):
                codec.parse_bank(broken)
        for predictor in range(4, 16):
            broken = bytearray(data)
            broken[off + 8] = broken[off + 8] & 0xf0 | predictor
            with self.assertRaisesRegex(ValueError, 'unsupported XAS predictor'):
                codec.parse_bank(broken)


def private_proof(exe, scratch, *, original=False):
    from setup_kit import car_audio as graph
    source = input_path('NFSC')
    roster = ROOT / 'tools_snapshot/private_cars.py'
    attributes = source / 'GLOBAL/attributes.bin'
    if not attributes.is_file() or not roster.is_file() or exe is None:
        print('SKIP private comparisons: source assets, roster or local FFmpeg absent; synthetic checks completed')
        return False
    cars = re.findall(r'\|C/([^|\n]+)\|', roster.read_text())
    assert cars, 'empty source inventory'
    files = {p.relative_to(source).as_posix(): p.read_bytes()
             for p in (source / 'SOUND/ENGINE').rglob('*') if p.suffix.lower() in ('.abk', '.gin')}
    inventory = graph.inventory(attributes.read_bytes(), cars, files)
    assert not inventory['unsupported'], inventory['unsupported']
    resources = {r['path']: r for g in inventory['supported'].values()
                 for r in g['resources'] if r['kind'] == 'abk'}
    # The oracle checks the baseline rejection result and exact diagnostic for
    # every codec-4 sample; unrelated production edits need no source hash.
    sounds = frames = loops = blocks = rejected = pcm_diff = metadata_diff = 0
    for path, resource in sorted(resources.items()):
        data = files[path]
        rejected += oracle(exe, data, scratch, baseline=True)
        if original:
            continue
        expected = oracle(exe, data, scratch)
        actual = codec.decode_bank(data)
        assert len(actual) == len(expected) == len(resource['sounds'])
        for got, want in zip(actual, expected):
            if want is None:
                assert got is None
                continue
            sounds += 1
            frames += got.sound.frames
            blocks += len(got.sound.blocks)
            loops += got.sound.loop_start is not None
            metadata_diff += metadata(got.sound) != want[0]
            if got.pcm != want[1]:
                a = struct.unpack('<' + str(len(got.pcm) // 2) + 'h', got.pcm)
                b = struct.unpack('<' + str(len(want[1]) // 2) + 'h', want[1])
                pcm_diff += sum(x != y for x, y in zip(a, b)) + abs(len(a) - len(b))
    print(f'original 13984ab7: {rejected} real codec-4 samples rejected (unsupported codec 4 storage 0)')
    assert rejected > 0
    if original:
        raise AssertionError(f'original decoder failed {rejected}/{rejected} referenced sounds')
    assert rejected == sounds
    print(f'private oracle: {len(resources)} banks / {sounds} sounds / {frames} frames / {blocks} blocks / {loops} loops; '
          f'PCM differences={pcm_diff}; frame/rate/channel/loop differences={metadata_diff}')
    assert pcm_diff == metadata_diff == 0
    return True


if __name__ == '__main__':
    SCRATCH.mkdir(parents=True, exist_ok=True)
    if '--mutant' in sys.argv:
        codec = mutated(sys.argv[sys.argv.index('--mutant') + 1])
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(CodecProof)
    result = unittest.TextTestRunner().run(suite)
    if not result.wasSuccessful():
        sys.exit(1)
    with tempfile.TemporaryDirectory(prefix='inventory-', dir=SCRATCH) as folder:
        scratch = Path(folder)
        exe = build_oracle(scratch)
        sys.exit(0 if private_proof(exe, scratch, original='--original' in sys.argv) else 3)
