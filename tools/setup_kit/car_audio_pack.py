"""Optional Mac stock-tier audio sidecar; Windows packs are unchanged.

The complete A1 graph and its resources are retained. Execution is restricted
to the two source programs whose binding, backend and lifecycle were replayed.
No source paths, native pointers or truncated digest identities are installed.
"""
import hashlib
import json
from pathlib import PurePosixPath
import struct

from . import car_audio, car_audio_abi as abi
from .car_audio_codec import decode_bank
from .car_convert import hash32, require

HEADER = struct.Struct('<4s4I16s32s')
PROFILE = 'jaguarxk:stock-v1'
ATTRIBUTES = 'b1c9ba06e476519db69ef3878a6b2796a0e7be90708355527601f3d58c129a4b'
MAX_SIZE = 256 << 20
PACKAGE_SHA256 = '66b587fa9e179b69180bcae26c8111d4b94c96dabc2a949f8e262c0289e2c02c'


def _u32(*values):
    return struct.pack('<' + 'I' * len(values), *values)


def _interface(row):
    name = row['name']
    wire = struct.pack('<HH', row['source_system'], row['source_signature']) + name.encode('ascii') + b'\0'
    return _u32(len(wire), row['source_member_words'], row['kind']) + wire


def execution_plan(report):
    """Pointer-free complete runtime manifest, using source file offsets."""
    interfaces = report['interfaces']
    result = bytearray(_u32(len(interfaces), len(report['modules'])))
    for row in interfaces:
        result += _u32(row['site']) + _interface(row)
    for row in report['modules']:
        exported = next(i for i, ref in enumerate(interfaces) if ref['site'] == row['descriptor'] + 4)
        result += _u32(*(row[k] for k in ('descriptor', 'entry', 'state', 'state_size', 'member_offset', 'destroy_offset')),
                       exported, len(row['players']), len(row['class_offsets']))
        for player in row['players']:
            result += _u32(player['offset'], player['size'], player['selector_offset'])
        result += _u32(*row['class_offsets'])
    return bytes(result)


def calibration(attributes, key):
    """Materialize effective numeric source layout, with inherited values.

    Pointer/string/reference fields remain zero; controllers consume only the
    decoded numeric calibration block and scalar fields. A1 retains all others.
    """
    result = bytearray(0x12c)
    for field, (dt, at, size, _, flags, _) in attributes.defs[car_audio.ENGINE].items():
        if not flags & 2 or at < 0x28 or at + size > len(result):
            continue
        value = attributes.field(car_audio.ENGINE, key, field)
        require(value is not None and len(value['values']) == 1, 'audio missing effective calibration')
        value = value['values'][0]
        if isinstance(value, dict):
            require(set(value) == {'bytes', 'strings'} and not value['strings'], 'audio calibration pointer')
            raw = bytes.fromhex(value['bytes'])
        else:
            fmt = car_audio.PRIMITIVES.get(dt)
            require(fmt is not None, 'audio unknown calibration scalar')
            raw = struct.pack('<' + fmt, value)
        require(len(raw) == size, 'audio calibration size')
        result[at:at+size] = raw
    return bytes(result)


def validate_gin(data):
    require(isinstance(data, bytes) and len(data) >= 0x28 and data[:6] == b'Gnsu20', 'audio GIN signature/size')
    lo, hi, frequencies, cycles, frames, rate = struct.unpack_from('<ff4I', data, 8)
    import math
    require(math.isfinite(lo) and math.isfinite(hi) and 0 < lo < hi and
            0 < frequencies <= 65536 and 0 < cycles <= 1048576 and
            0 < frames <= 100000000 and 8000 <= rate <= 192000, 'audio GIN dimensions')
    tables = 0x20 + 4 * (frequencies + cycles + 2)
    require(tables + ((frames + 31)//32)*19 == len(data), 'audio GIN extent')
    for at, count in ((0x20, frequencies+1), (0x24+4*frequencies, cycles+1)):
        values = struct.unpack_from('<' + 'I'*count, data, at)
        ordered = all(a <= b for a, b in zip(values, values[1:]))
        if at == 0x20:
            ordered |= all(a >= b for a, b in zip(values, values[1:]))
        require(ordered and max(values) < frames,
                'audio GIN sample table')


def build(attributes, car, resources):
    require(car.lower() == 'jaguarxk', 'audio unsupported car/stock profile')
    require(hashlib.sha256(attributes).hexdigest() == ATTRIBUTES, 'audio unsupported attributes revision')
    graph = car_audio.graph(attributes, car, resources)
    graph_bytes = json.dumps(graph, sort_keys=True, separators=(',', ':')).encode()
    inventory = car_audio.Resources(resources)
    rows = graph['resources']
    index = {row['path']: i for i, row in enumerate(rows)}
    primary = graph['nodes'][graph['tiers'][0]['target']]
    layers = []
    active = set()
    for field in (car_audio.ENGINE, hash32('exhaustaudio')):
        ref = primary['fields'][f'{field:08x}']['values'][0]
        node = graph['nodes'][car_audio.identity(int(ref['class'], 16), int(ref['key'], 16))]
        paths = [node['fields'][key]['resources'][0]['path'] for key in ('2fc9c96f', '2cee0a54', '3a7c2f3d')]
        active.add(paths[0])
        layers.append(calibration(car_audio.AudioAttributes(attributes), int(node['key'], 16)) +
                      _u32(*(index[path] for path in paths)))
    payload = bytearray(graph_bytes + b''.join(layers))
    for row in rows:
        data = inventory.files[row['path'].casefold()][1]
        plan, sounds = b'', ()
        if row['kind'] == 'gin':
            validate_gin(data)
        else:
            report = abi.inspect_bank(data, row)
            if row['path'] in active:
                report = abi.require_sound_admission(data, row, closure=PROFILE)
                plan = execution_plan(report)
            sounds = decode_bank(data)
        payload += _u32(1 if row['kind'] == 'gin' else 2, len(data), len(plan), len(sounds))
        payload += hashlib.sha256(data).digest() + data + plan
        for sound in sounds:
            if sound is None:
                payload += _u32(0, 0, 0, 0)
            else:
                s = sound.sound
                require(s.channels == 1 and s.loop_start in (None, 0), 'audio unsupported PCM loop/channels')
                payload += _u32(s.frames, s.rate, int(s.loop_start is not None), len(sound.pcm)) + sound.pcm
    require(HEADER.size + len(payload) <= MAX_SIZE, 'audio package limit')
    package = HEADER.pack(b'A7CA', 1, HEADER.size+len(payload), len(rows), len(graph_bytes),
                          car.lower().encode().ljust(16, b'\0'), hashlib.sha256(payload).digest()) + payload
    require(hashlib.sha256(package).hexdigest() == PACKAGE_SHA256, 'audio closure differs from proved stock package')
    return package


def from_directory(attributes, car, root, read):
    """Read only A1-reachable resources through the converter's input tracker."""
    attrib = car_audio.AudioAttributes(attributes)
    names, seen = set(), set()
    def visit(cls, key):
        if (cls, key) in seen:
            return
        seen.add((cls, key))
        for field in attrib.defs[cls]:
            value = attrib.field(cls, key, field)
            if not value:
                continue
            dt = int(value['type'], 16)
            if dt == car_audio.REF:
                for ref in value['values']:
                    if int(ref['key'], 16):
                        visit(int(ref['class'], 16), int(ref['key'], 16))
            elif dt == car_audio.STRING and not (cls == car_audio.ENGINE and field == 0x04935eab):
                names.update(v['text'].casefold() for v in value['values'] if v['text'])
    tiers = attrib.field(hash32('pvehicle'), hash32(car.lower()), hash32('Audio'))
    require(tiers is not None, 'audio missing tiers')
    for ref in tiers['values']:
        visit(int(ref['class'], 16), int(ref['key'], 16))
    paths = {}
    for path in (root / 'SOUND/ENGINE').rglob('*'):
        if path.is_file() and path.name.casefold() in names:
            require(path.name.casefold() not in paths, 'audio basename collision')
            paths[path.name.casefold()] = path
    require(set(paths) == names, 'audio missing reachable resources')
    return build(attributes, car, {p.relative_to(root).as_posix(): read(p) for p in paths.values()})


def resource_name(package_digest, resource_digest):
    require(all(len(x) == 64 and all(c in '0123456789abcdef' for c in x)
                for x in (package_digest, resource_digest)), 'audio digest identity')
    return str(PurePosixPath('SOUND/ADDCAR_AUDIO') / package_digest / resource_digest)
