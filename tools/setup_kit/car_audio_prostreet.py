"""Audited ProStreet stock engine/exhaust TMX closure and package adapter.

The stock player selects typed NG engine/exhaust filenames at vehicle index zero.
Secondary banks are inventory only; their programs are not executed.
"""
import hashlib
import math
import re
import struct

from . import car_audio as ca, car_audio_codec as codec
from .car_convert import hash32, require, span
from .ps_attrib import pvehicle_key

ATTRIBUTES = '4435fda2ca35074d656cb1e9230c2df81e169682e95b79719e2ed52b6194b69d'
NG = 0x6d483f8f
FILENAME = 0x0eb1c9d2
SWEET_BANKS = 0xa7468316
# Reviewed additions; other source cars remain outside the import contract.
CARS = frozenset('997GT2 997GT3 BMWM3E92 BMWMZ4 CIVICHB CIVICSI CORVETTE67 COSWORTH CTSV DBR9 FOCUSST GTO65 GTRPROTO INTEGRALS INTEGRATYPER IS350 LANCEREVOX LEONCUPRA MCLARENF1 MUSTANG03 NSX RS4 RSX S2000 S3 S4 SILVIA SKYLINER35 SOLSTICEGXP TTN VEYRON'.split())


def admitted(attributes, car):
    return car.upper() in CARS and hashlib.sha256(attributes).hexdigest() == ATTRIBUTES


def _value(attrib, cls, key, field):
    value = attrib.field(cls, key, field)
    require(value and len(value['values']) == 1, 'ProStreet audio field shape')
    return value['values'][0]


def describe(attributes, car):
    require(admitted(attributes, car), 'audio unsupported ProStreet attributes/car')
    attrib = ca.AudioAttributes(attributes)
    pv = pvehicle_key(attrib, car)
    vehicles = attrib.field(hash32('pvehicle'), pv, hash32('vehicle'))
    require(vehicles and vehicles['values'], 'ProStreet audio vehicle missing')
    vehicle = vehicles['values'][0]
    require(int(vehicle['class'], 16) == hash32('vehicle'), 'ProStreet vehicle class')
    ref = _value(attrib, int(vehicle['class'], 16), int(vehicle['key'], 16), hash32('CARAUDIO'))
    require(int(ref['class'], 16) == ca.CAR and int(ref['key'], 16), 'ProStreet audio class')
    audio = int(ref['key'], 16)
    layers = []
    for field, name in ((0x9aed4ffe, 'engine'), (0xb3186761, 'exhaust')):
        ref = _value(attrib, ca.CAR, audio, field)
        require(int(ref['class'], 16) == NG and int(ref['key'], 16), 'ProStreet NG class')
        key = int(ref['key'], 16)
        filename = _value(attrib, NG, key, FILENAME)['text']
        require(filename and re.fullmatch(r'car_[0-9]{3}-' + name + r'\.tmx', filename.lower()),
                'ProStreet NG filename')
        banks = attrib.field(NG, key, SWEET_BANKS)
        layers.append(dict(role=name, key=f'{key:08x}', bank=int(filename[4:7]),
                           label=_value(attrib, NG, key, hash32('CollectionName')),
                           paths=['SOUND/ENGINE/' + filename.upper()],
                           sweet_banks=[] if banks is None else [v['text'] for v in banks['values']]))
    # Secondary selectors describe authored inventory, not playback dependencies.
    turbo_field = attrib.field(int(vehicle['class'], 16), int(vehicle['key'], 16), 0x6e5b8588)
    turbo = turbo_field['values'][0] if turbo_field and turbo_field['values'] else None
    turbo_key = int(turbo['key'], 16) if turbo else 0
    blower = (_value(attrib, int(turbo['class'], 16), turbo_key, 0xbf49a7d9)['text']
              if turbo_key else None)
    gear_field = attrib.field(ca.CAR, audio, 0x364efa6a)
    gear = gear_field['values'][0]['text'] if gear_field and gear_field['values'] else None
    skids = set()
    for cls, key in attrib.collections:
        if cls == hash32('audiosystem'):
            banks = attrib.field(cls, key, 0xbd6f7135)
            if banks:
                skids.update(v['text'] for v in banks['values'] if v['text'])
    return dict(pvehicle=f'{pv:08x}', caraudio=f'{audio:08x}', vehicle_index=0,
                layers=layers, secondary=dict(blower=blower, gear=gear, skid=sorted(skids)))


def resource_paths(attributes, car, candidates):
    names = {p.casefold() for e in describe(attributes, car)['layers'] for p in e['paths']}
    found = {}
    for path in candidates:
        key = path.casefold()
        if key in names:
            require(key not in found, 'ProStreet audio case collision')
            found[key] = path
    require(found.keys() == names, 'ProStreet audio missing reachable resources')
    return sorted(found.values())


def tmx(data):
    """Decode bounded TMX v1000 pairs, bins and embedded resident recordings.

    The reviewed layouts have four crossfade controls, four RPM bins, three to six tracks,
    no cross-pair links, and one input block with five live controls. Offsets are
    file-relative except each track's payload offset, relative to header+5c.
    """
    require(isinstance(data, bytes) and 0x60 <= len(data) <= 16 << 20, 'TMX size')
    u = lambda at: struct.unpack('<I', span(data, at, 4, 'TMX field bounds'))[0]
    require(data[12:16] == b'1000' and [u(at) for at in (16, 20, 24, 48)] == [4, 4, 0, 2] and 3 <= u(40) <= 6,
            'TMX unsupported topology')
    offsets = [u(at) for at in range(56, 96, 4)]
    require(offsets == sorted(offsets) and offsets[0] == 96 and offsets[-1] < len(data), 'TMX section offsets')
    inputs, cross, bins, extras, _, variables, _, _, tracks, base = offsets
    require(cross == inputs + 64 and data[:12] == data[inputs:inputs+12] == data[inputs+32:inputs+44] and
            u(inputs+12) == 0 and u(inputs+16) == 5 and u(inputs+44) == 1 and u(inputs+48) == 4,
            'TMX input blocks')
    controls = []
    at = cross
    for pair in range(2):
        for _ in range(2):
            ident, curve, zero, control, block, pointer = struct.unpack('<6I', span(data, at, 24))
            values = struct.unpack('<5f', span(data, at+24, 20))
            require(ident == len(controls) and curve in (0, 1, 4, 8, 9) and not (zero or block or pointer) and
                    control < 5 and all(math.isfinite(v) for v in values) and
                    values[1] > 0 and 0 <= values[2] <= values[3] <= 1 and values[4] == 0,
                    'TMX crossfade control')
            controls.append(dict(curve=curve, input=control, values=values[:4]))
            at += 44
        require(span(data, at, 16) == bytes(16), 'TMX cross-pair links unsupported')
        at += 16
    require(at == bins, 'TMX crossfade extent')
    layers = []
    for index in range(4):
        ident, count, extent, deps, control, block, pointer = struct.unpack('<7I', span(data, at, 28))
        require(ident == index and 0 <= count <= 3 and deps <= 4 and extent == 28 + 4*deps and
                control < 5 and not (block or pointer), 'TMX bin header')
        dependencies = struct.unpack('<' + 'I'*deps, span(data, at+28, deps*4))
        require(len(set(dependencies)) == deps and all(x < 4 for x in dependencies), 'TMX bin dependencies')
        ends = []
        for side in range(2*count):
            pos = at + extent + 32*side
            ident, track, zero, curve = struct.unpack('<4I', span(data, pos, 16))
            start, width, gain, output = struct.unpack('<4f', span(data, pos+16, 16))
            require(ident == side and track < u(40) and not zero and curve in (0, 1, 4, 8, 9) and
                    all(math.isfinite(v) for v in (start, width, gain, output)) and
                    start >= 0 and width >= 0 and 0 <= gain <= 1 and output == 0, 'TMX bin endpoint')
            ends.append(dict(track=track, curve=curve, start=start, width=width, gain=gain))
        require(all(ends[i]['start'] < ends[i+1]['start'] for i in range(0, len(ends), 2)), 'TMX bin interval')
        layers.append(dict(input=control, dependencies=dependencies, ends=ends))
        at += extent + 64*count
    require(at == extras == variables and tracks + u(40)*36 == base, 'TMX bin/track extent')
    sources = []
    previous = -1
    for index in range(u(40)):
        at = tracks + index*36
        kind, ident, offset, pointer, zero, one, history, frequency, gain = struct.unpack('<7I2f', span(data, at, 36))
        require(kind in (0x534e5220, 0x47494e20) and ident == index and
                not (pointer or zero or history or gain) and one == 1 and math.isfinite(frequency) and 0 < frequency <= 20000 and offset > previous,
                'TMX track descriptor')
        stop = base + (u(at+36+8) if index+1 < u(40) else len(data)-base)
        pos = base + offset
        require(pos < stop <= len(data), 'TMX track extent')
        raw = bytes(span(data, pos, stop-pos))
        if kind == 0x47494e20:
            from .car_audio_pack import validate_gin
            require(len(raw) >= 40, 'TMX GIN header')
            require(struct.unpack_from('<I', raw, 28)[0] in (24000, 32000, 36000, 44000, 44100, 48000),
                    'TMX GIN rate')
            f, c, frames = struct.unpack_from('<3I', raw, 16)
            extent = 40 + 4*(f+c) + ((frames+31)//32)*19
            require(raw[extent:] in (b'', b'\0', b'\0\0', b'\0\0\0'), 'TMX GIN padding')
            raw = raw[:extent]
            validate_gin(raw)
            sound = None
        else:
            sound = snr(raw)
        sources.append(dict(kind='gin' if sound is None else 'snr', offset=pos, size=len(raw),
                            sha256=hashlib.sha256(raw).hexdigest(), sound=sound))
        previous = offset
    require(any(s['kind'] == 'gin' for s in sources), 'TMX recording topology')
    bounds = struct.unpack('<2f', span(data, variables, 8, 'TMX RPM bounds'))
    require(all(math.isfinite(v) for v in bounds) and 0 < bounds[0] < bounds[1], 'TMX RPM range')
    return dict(controls=controls, bins=layers, tracks=sources, rpm=bounds)


def snr(data):
    """Reuse the resident XAS decoder with a bounded SNR sound descriptor."""
    require(len(data) >= 20, 'TMX SNR header')
    a, b = struct.unpack_from('>2I', data)
    frames, loop = b & 0x1fffffff, bool(b & 0x20000000)
    require(a >> 24 == 4 and not (b >> 30) and (a >> 18 & 63) == 0 and
            a & 0x3ffff in (32000, 44000, 48000) and frames > 0, 'TMX SNR codec/rate')
    at = 12 if loop else 8
    start = struct.unpack_from('>I', data, 8)[0] if loop else None
    require(start is None or start < frames, 'TMX SNR loop')
    total, blocks = 0, []
    while total < frames:
        size, count = struct.unpack('>2I', span(data, at, 8, 'TMX SNR block header'))
        require(0 < count <= frames-total and size == 8+((count+127)//128)*76, 'TMX SNR block shape')
        raw = bytes(span(data, at+8, size-8, 'TMX SNR block bounds'))
        require(all(raw[p+n*4] & 15 < 4 for p in range(0, len(raw), 76) for n in range(4)), 'TMX SNR predictor')
        blocks.append(codec.Block(at, count, raw)); total += count; at += size
    require(data[at:] in (b'', b'\0', b'\0\0', b'\0\0\0'), 'TMX SNR padding')
    return codec.Sound(0, 0, 4, 0, 1, a & 0x3ffff, frames, start, frames-1 if loop else None, 0, at, tuple(blocks))


def graph(attributes, car, resources):
    description = describe(attributes, car)
    selected = resource_paths(attributes, car, resources)
    require(set(selected) == set(resources), 'ProStreet audio resource closure differs')
    rows = []
    for path in selected:
        data = resources[path]
        row = dict(path=path, size=len(data), sha256=hashlib.sha256(data).hexdigest())
        plan = tmx(data)
        layer = next(e for e in description['layers'] if e['paths'][0].casefold() == path.casefold())
        layer['rpm'] = list(plan['rpm'])
        row.update(kind='tmx', tracks=[{k: v for k, v in t.items() if k != 'sound'} for t in plan['tracks']])
        rows.append(row)
    return dict(schema='source-car-audio', version=ca.VERSION,
                source=dict(game='prostreet', platform='pc', car=car.lower(), attributes_sha256=ATTRIBUTES),
                selection=description, resources=rows)


def build(attributes, car, resources):
    from .car_audio_pack import HEADER, MAX_SIZE
    report = graph(attributes, car, resources)
    rows = report['resources']
    indices = {r['path'].casefold(): i for i, r in enumerate(rows)}
    layers = report['selection']['layers']
    payload = bytearray(struct.pack('<2I4f', *(indices[e['paths'][0].casefold()] for e in layers),
                                    *(v for e in layers for v in e['rpm'])))
    payload += bytes.fromhex(ATTRIBUTES)
    for row in rows:
        data = resources[row['path']]
        sounds = [t['sound'] for t in tmx(data)['tracks'] if t['sound'] is not None]
        payload += struct.pack('<4I', 3, len(data), 0, len(sounds))
        payload += bytes.fromhex(row['sha256']) + data
        for sound in sounds:
            pcm = codec.decode_sound(sound).pcm
            payload += struct.pack('<4I', sound.frames, sound.rate,
                                   0 if sound.loop_start is None else sound.loop_start+1, len(pcm)) + pcm
    require(HEADER.size + len(payload) <= MAX_SIZE, 'ProStreet audio package limit')
    return HEADER.pack(b'A7CA', 5, HEADER.size+len(payload), len(rows), 1,
                       car.lower().encode().ljust(16, b'\0'), hashlib.sha256(payload).digest()) + payload
