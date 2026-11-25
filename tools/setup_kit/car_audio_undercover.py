"""Bounded Undercover console car recordings, with separate normalized GINs.

TMX controls and ABKC metadata are BE; embedded Gnsu20 stays LE. This reader
retains source identities and authored rates/loops. The stock player adapter
selects Audio[0]; secondary bank programs and upgrades remain unadmitted.
Disc files are read only.
"""
import hashlib
import math
from pathlib import Path
import re
import struct

from . import car_audio_codec as codec, xdvdfs
from .car_convert import name_hash, require, span
from .car_audio_pack import validate_gin

MAX_BYTES = 16 << 20
ATTRIBUTES = '1ae36cd4e9efd9ccc94e33594c753681028ab45ce2cfb1ed3b8feaa5785b8971'
CARS = frozenset('AUD_S5_STK_08 BMW_M6_STK_08 DOD_CHR_BEE_07 DOD_VIP_SRT_08 FOR_SHL_GT_08 FOR_SHL_TER_08 LAM_GAL_560_09 LAM_GAL_SUP_08 LEX_IS_F_08 MER_CLS_55_08 MER_SLR_722_07 NIS_370_Z_09 REN_MEG_COU_08 VOL_SCI_STK_08'.split())


def admitted(attributes, car):
    return car.upper() in CARS and hashlib.sha256(attributes).hexdigest() == ATTRIBUTES


def describe(attributes, car):
    """Resolve the stock pvehicle Audio[0] typed NG player filenames."""
    from . import car_audio as ca, uc_attrib
    from .car_convert import FitAttributes, hash32
    require(admitted(attributes, car), 'audio unsupported Undercover attributes/car')
    native = FitAttributes(attributes, vaults=uc_attrib.vaults(attributes))
    attrib = object.__new__(ca.AudioAttributes)
    attrib.defs, attrib.collections = native.defs, native.collections
    own = hash32(uc_attrib.STOCK_ALIASES.get(car.upper(), car).lower())
    tiers = attrib.field(hash32('pvehicle'), own, hash32('Audio'))
    require(tiers and tiers['values'], 'Undercover audio selection missing')
    ref = tiers['values'][0]
    require(int(ref['class'], 16) == ca.CAR and int(ref['key'], 16), 'Undercover audio class')
    audio = int(ref['key'], 16)
    layers = []
    for field, role in ((0x9aed4ffe, 'engine'), (0xb3186761, 'exhaust')):
        value = attrib.field(ca.CAR, audio, field)
        require(value and len(value['values']) == 1, 'Undercover NG reference')
        ref = value['values'][0]
        key = int(ref['key'], 16)
        require(int(ref['class'], 16) == 0x6d483f8f and key, 'Undercover NG class')
        value = attrib.field(0x6d483f8f, key, 0x0eb1c9d2)
        require(value and len(value['values']) == 1, 'Undercover NG filename field')
        filename = value['values'][0]['text']
        require(filename and re.fullmatch(r'car_[0-9]{3}-' + role + r'-player\.tmx', filename.lower()),
                'Undercover NG player filename')
        layers.append(dict(role=role, key=f'{key:08x}', paths=['SOUND/ENGINE/' + filename.upper()]))
    return dict(pvehicle=f'{own:08x}', caraudio=f'{audio:08x}', audio_index=0, layers=layers)


def resource_paths(attributes, car, candidates):
    names = {p.casefold() for layer in describe(attributes, car)['layers'] for p in layer['paths']}
    found = {}
    for path in candidates:
        key = path.casefold()
        if key in names:
            require(key not in found, 'Undercover audio case collision')
            found[key] = path
    require(found.keys() == names, 'Undercover audio missing reachable resources')
    return sorted(found.values())


def build(attributes, car, resources):
    """v6 keeps original BE TMXs and PCM at authored rates/loop positions.

    GIN normalization is applied to the guest copy at attachment, preserving
    source TMX identities and all compressed blocks/tables in the package.
    """
    from .car_audio_pack import HEADER, MAX_SIZE
    layers = describe(attributes, car)['layers']
    paths = resource_paths(attributes, car, resources)
    require(set(paths) == set(resources), 'Undercover audio resource closure differs')
    plans = {p: tmx(resources[p]) for p in paths}
    index = {p.casefold(): i for i, p in enumerate(paths)}
    ordered = [next(p for p in paths if p.casefold() == layer['paths'][0].casefold()) for layer in layers]
    payload = bytearray(struct.pack('<2I4f', *(index[p.casefold()] for p in ordered),
                                    *(v for p in ordered for v in plans[p]['rpm'])))
    payload += bytes.fromhex(ATTRIBUTES)
    for path in paths:
        data = resources[path]
        sounds = [track['sound'] for track in plans[path]['tracks'] if track['sound']]
        payload += struct.pack('<4I', 3, len(data), 0, len(sounds)) + hashlib.sha256(data).digest() + data
        for sound in sounds:
            pcm = codec.decode_sound(sound).pcm
            payload += struct.pack('<4I', sound.frames, sound.rate,
                                   0 if sound.loop_start is None else sound.loop_start + 1, len(pcm)) + pcm
    require(HEADER.size + len(payload) <= MAX_SIZE, 'Undercover audio package limit')
    return HEADER.pack(b'A7CA', 6, HEADER.size + len(payload), len(paths), 1,
                       car.lower().encode().ljust(16, b'\0'), hashlib.sha256(payload).digest()) + payload


def sound_spans(zdir, files):
    """Resolve unique ZDIR hashes to bounded physical sound-archive spans.

    `files` is an XDVDFS inventory. Validate the full hash namespace before
    selecting archives 13/14; no extraction or archive-sized allocation occurs.
    """
    require(0 < len(zdir) <= 24 * xdvdfs.MAX_FILES and len(zdir) % 24 == 0, 'ZDIR size')
    result, seen = {}, set()
    for identity, archive, sector, _, size, _ in struct.iter_unpack('<6I', zdir):
        require(identity not in seen, 'ZDIR duplicate hash')
        seen.add(identity)
        if archive == 0xffffffff:
            require(size == 0, 'ZDIR stub size')
            continue
        require(archive <= 99, 'ZDIR archive byte order')
        if archive not in (13, 14):
            continue
        entry = files.get(f'NFS/{archive:02}SOUND.BIN')
        require(entry is not None, 'ZDIR missing sound archive')
        start = sector * xdvdfs.SECTOR
        require(0 < size <= 128 << 20 and start <= entry.size and size <= entry.size - start,
                'ZDIR sound span bounds')
        result[identity] = (entry.offset + start, size)
    return result


class Disc:
    """Read exact named source spans from an ISO opened in binary read mode."""
    def __init__(self, path):
        self.stream = Path(path).open('rb')
        try:
            self.files = xdvdfs.inventory(self.stream)
            z = self.files.get('NFS/ZDIR.BIN')
            require(z is not None and 0 < z.size <= 24 * xdvdfs.MAX_FILES, 'ZDIR missing/size')
            raw = xdvdfs.read_exact(self.stream, z.offset, z.size)
            self.zdir_sha256 = hashlib.sha256(raw).hexdigest()
            self.spans = sound_spans(raw, self.files)
        except BaseException:
            self.close()
            raise

    def read(self, path):
        path = path.upper().replace('\\', '/')
        require(re.fullmatch(r'SOUND/(ENGINE|IG_GLOBAL|SHIFTING)/[A-Z0-9_-]+\.(TMX|ABK)', path),
                'sound source path')
        identity = name_hash(path.replace('/', '\\'))
        require(identity in self.spans, 'missing sound source: ' + path)
        offset, size = self.spans[identity]
        require(size <= MAX_BYTES, 'sound source allocation limit')
        return xdvdfs.read_exact(self.stream, offset, size)

    def close(self):
        self.stream.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def normalize_gin(data):
    """Return a copy with frames = terminal cycle sample + 1.

    Only the +18h word may change. Validate LE dimensions before table access,
    then require the corrected count to describe every compressed byte exactly.
    Up to three zero TMX alignment bytes are excluded from the returned GIN.
    """
    require(isinstance(data, bytes) and 40 <= len(data) <= MAX_BYTES and data[:8] == b'Gnsu20\0\0',
            'GIN signature/size')
    lo, hi, frequencies, cycles, frames, rate = struct.unpack_from('<ff4I', data, 8)
    require(math.isfinite(lo) and math.isfinite(hi) and 0 < lo < hi and
            0 < frequencies <= 65536 and 0 < cycles <= 1048576 and
            0 < frames <= 100000000 and 8000 <= rate <= 192000, 'GIN LE dimensions')
    tables = 40 + 4 * (frequencies + cycles)
    require(tables <= len(data), 'GIN table bounds')
    terminal = struct.unpack_from('<I', data, tables - 4)[0] + 1
    # The classified source defect is an oversized frame count, never a short
    # recording. Refuse to repair an arbitrary undersized/corrupt frame word.
    require(0 < terminal <= frames, 'GIN terminal frame count')
    extent = tables + ((terminal + 31) // 32) * 19
    require(extent <= len(data) and len(data) - extent <= 3 and not any(data[extent:]),
            'GIN compressed extent/padding')
    result = bytearray(data[:extent])
    struct.pack_into('<I', result, 24, terminal)
    result = bytes(result)
    validate_gin(result)
    return result


def bank(data):
    """Read BE ABKC/S10A sample identities; preserve null slots and XMA bytes."""
    require(isinstance(data, bytes) and len(data) <= MAX_BYTES, 'ABKC size')
    return codec.parse_bank(data, endian='>', codecs=(3,))


def tmx(data):
    """Read the audited BE control topology and bounded resident GIN/SNR tracks.

    Normalized track data is separate from source SHA-256/offset/span_size.
    Offsets refer to the original TMX, including each track's alignment bytes.
    """
    require(isinstance(data, bytes) and 96 <= len(data) <= MAX_BYTES, 'TMX size')
    u = lambda at: struct.unpack('>I', span(data, at, 4, 'TMX field bounds'))[0]
    bins_count, count = u(20), u(40)
    require(data[12:16] == b'0001' and u(16) == 4 and bins_count in (3, 4) and
            u(24) == 0 and 3 <= count <= 6 and u(32) == u(44) == count and
            u(36) == 1 and u(48) == 3 and u(52) == 16 and 0 < u(28) <= 32,
            'TMX BE topology')
    offsets = [u(at) for at in range(56, 96, 4)]
    require(offsets == sorted(offsets) and offsets[0] == 96 and offsets[-1] < len(data), 'TMX section offsets')
    inputs, cross, bins, extras, spare, variables, v1, v2, tracks, base = offsets
    require(cross == inputs + 96, 'TMX input extent')
    for block, controls in enumerate((7, 5, 4)):
        at = inputs + 32 * block
        require(data[at:at+12] == data[:12] and u(at+12) == block and u(at+16) == controls and
                not any(data[at+20:at+32]), 'TMX input block')
    controls, at = [], cross
    for _ in range(2):
        for _ in range(2):
            ident, curve, zero, control, block, pointer = struct.unpack('>6I', span(data, at, 24))
            values = struct.unpack('>5f', span(data, at+24, 20))
            require(ident == len(controls) and curve in (0, 1, 4, 8, 9) and not (zero or block or pointer) and
                    control in (0, 2, 4) and all(math.isfinite(v) for v in values) and
                    values[1] > 0 and 0 <= values[2] <= values[3] <= 1 and values[4] == 0,
                    'TMX crossfade control')
            controls.append(dict(curve=curve, input=control, values=values[:4]))
            at += 44
        require(span(data, at, 16) == bytes(16), 'TMX cross-pair links')
        at += 16
    require(at == bins, 'TMX crossfade extent')
    layers = []
    for index in range(bins_count):
        ident, pairs, extent, deps, control, block, pointer = struct.unpack('>7I', span(data, at, 28))
        require(ident == index and 0 <= pairs <= 3 and deps <= 4 and extent == 28 + 4*deps and
                control in (0, 2, 4) and not (block or pointer), 'TMX bin header')
        dependencies = struct.unpack('>' + 'I'*deps, span(data, at+28, deps*4))
        require(len(set(dependencies)) == deps and all(x < 4 for x in dependencies), 'TMX bin dependencies')
        ends = []
        for side in range(2*pairs):
            pos = at + extent + 32*side
            ident, track, zero, curve = struct.unpack('>4I', span(data, pos, 16))
            start, width, gain, output = struct.unpack('>4f', span(data, pos+16, 16))
            require(ident == side and track < count and not zero and curve in (0, 1, 4, 8, 9) and
                    all(math.isfinite(v) for v in (start, width, gain, output)) and
                    start >= 0 and width >= 0 and 0 <= gain <= 1 and output == 0, 'TMX bin endpoint')
            ends.append(dict(track=track, curve=curve, start=start, width=width, gain=gain))
        require(all(ends[i]['start'] < ends[i+1]['start'] for i in range(0, len(ends), 2)), 'TMX bin interval')
        layers.append(dict(input=control, dependencies=dependencies, ends=ends))
        at += extent + 64*pairs
    require(at == extras == spare == variables and v1 == variables + 128 and v2 == v1 + 64 and
            tracks == v2 + 4*count and tracks + count*36 == base and
            not any(data[variables+8:tracks]), 'TMX variable/bin/track extent')
    bounds = struct.unpack('>2f', span(data, variables, 8))
    require(all(math.isfinite(v) for v in bounds) and 0 < bounds[0] < bounds[1], 'TMX RPM range')
    sources, previous = [], -1
    for index in range(count):
        at = tracks + index*36
        kind, ident, offset, pointer, zero, one, history, frequency, gain = struct.unpack('>7I2f', span(data, at, 36))
        require(kind in (0x534e5220, 0x47494e20) and ident == index and
                not (pointer or zero or history or gain) and one == 1 and math.isfinite(frequency) and
                0 < frequency <= 20000 and offset > previous and (index != 0 or offset == 0),
                'TMX track descriptor')
        stop = base + (u(at+44) if index+1 < count else len(data)-base)
        pos = base + offset
        require(pos < stop <= len(data), 'TMX track extent')
        raw = bytes(span(data, pos, stop-pos))
        if kind == 0x47494e20:
            normalized, sound = normalize_gin(raw), None
        else:
            sound = codec.parse_snr(raw, padding=3)
            normalized = raw[:sound.end_offset]
        sources.append(dict(kind='gin' if sound is None else 'snr', offset=pos, span_size=len(raw),
                            size=len(normalized), source_sha256=hashlib.sha256(raw).hexdigest(),
                            sha256=hashlib.sha256(normalized).hexdigest(), data=normalized, sound=sound))
        previous = offset
    require(any(s['kind'] == 'gin' for s in sources), 'TMX recording topology')
    return dict(source_sha256=hashlib.sha256(data).hexdigest(), controls=controls,
                bins=layers, tracks=sources, rpm=bounds)
