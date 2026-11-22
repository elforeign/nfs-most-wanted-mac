"""Build Mac audio from the ordered source tiers and their direct resources."""
import hashlib
import json
from pathlib import PurePosixPath
import re
import struct

from . import car_audio, car_audio_abi as abi
from .car_audio_codec import decode_bank
from .car_convert import hash32, require

HEADER = struct.Struct('<4s4I16s32s')
PROFILE = 'jaguarxk:stock-v1'
ATTRIBUTES = 'b1c9ba06e476519db69ef3878a6b2796a0e7be90708355527601f3d58c129a4b'
X360_ATTRIBUTES = '24cc0188954147db87c8889363d6d0338a6dac40ab5d3b2780873cd1173b904d'
MAX_SIZE = 256 << 20
LEGACY_XK_SHA256 = '66b587fa9e179b69180bcae26c8111d4b94c96dabc2a949f8e262c0289e2c02c'



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
            if attributes.endian == '>':
                # These layouts are arrays of signed words or a 32-bit enum.
                require(dt in (0x081e39b5, 0x8fd96119, 0xce9c0458, 0x9c2a3cd2) and size % 4 == 0,
                        'audio unknown Xenon calibration layout')
                raw = b''.join(raw[i:i+4][::-1] for i in range(0, size, 4))
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


LAYER_FIELDS = (car_audio.ENGINE, hash32('exhaustaudio'))
RESOURCE_FIELDS = ('2fc9c96f', '2cee0a54', '3a7c2f3d')
PLAYER_ADAPTERS = {'A5-player', 'A5-player-destruction', 'A6-engine-class'}


def tier_plan(graph):
    """Each tier is engine then exhaust, each with ABKC, accel GIN and decel GIN."""
    tiers = []
    for tier in graph['tiers']:
        fields = graph['nodes'][tier['target']]['fields']
        layers = []
        for field in LAYER_FIELDS:
            ref = fields[f'{field:08x}']['values'][0]
            key = int(ref['key'], 16)
            node = graph['nodes'][car_audio.identity(car_audio.ENGINE, key)]
            paths = tuple(node['fields'][f]['resources'][0]['path'] for f in RESOURCE_FIELDS)
            layers.append((key, paths))
        tiers.append(layers)
    return tiers


def _x360_tiers(attrib, car):
    audio = attrib.field(hash32('pvehicle'), hash32(car.lower()), hash32('Audio'))
    require(audio and audio['array'] and audio['type'] == f'{car_audio.REF:08x}', 'Xenon audio tiers')
    tiers = []
    for tier in audio['values']:
        require(int(tier['class'], 16) == car_audio.CAR and int(tier['key'], 16), 'Xenon tier reference')
        layers = []
        for role in LAYER_FIELDS:
            field = attrib.field(car_audio.CAR, int(tier['key'], 16), role)
            require(field and len(field['values']) == 1 and field['type'] == f'{car_audio.REF:08x}',
                    'Xenon layer reference')
            ref = field['values'][0]
            key = int(ref['key'], 16)
            require(key and int(ref['class'], 16) == car_audio.ENGINE, 'Xenon layer class')
            paths = []
            for resource in RESOURCE_FIELDS:
                field = attrib.field(car_audio.ENGINE, key, int(resource, 16))
                require(field and len(field['values']) == 1 and field['type'] == f'{car_audio.STRING:08x}',
                        'Xenon audio resource reference')
                name = (field['values'][0]['text'] or '').upper()
                suffix = 'ABK' if resource == RESOURCE_FIELDS[0] else 'GIN'
                require(re.fullmatch(r'[A-Z0-9_]{1,63}\.' + suffix, name), 'Xenon audio resource name')
                paths.append(('SOUND/ENGINE/' if suffix == 'ABK' else 'SOUND/ENGINE/GIN/') + name)
            layers.append((key, tuple(paths)))
        tiers.append(layers)
    return tiers


def x360_resource_paths(attributes, car):
    """Resolve the direct console closure before copying any sound assets."""
    require(hashlib.sha256(attributes).hexdigest() == X360_ATTRIBUTES, 'audio unsupported Xenon attributes revision')
    return sorted({path for layers in _x360_tiers(car_audio.XenonAudioAttributes(attributes), car)
                   for _, paths in layers for path in paths})


def build(attributes, car, resources, *, platform='pc', legacy_xk=False):
    require(0 < len(car) < 16 and car.isascii() and car.replace('_', '').isalnum(), 'audio unsupported car')
    require(platform in ('pc', 'x360'), 'audio unsupported platform')
    console = platform == 'x360'
    require(not legacy_xk or (not console and car.lower() == 'jaguarxk'), 'legacy audio is Jaguar XK only')
    require(hashlib.sha256(attributes).hexdigest() == (X360_ATTRIBUTES if console else ATTRIBUTES),
            'audio unsupported attributes revision')
    inventory = car_audio.Resources(resources, platform=platform)
    if console:
        attrib = car_audio.XenonAudioAttributes(attributes)
        tiers = _x360_tiers(attrib, car)
    else:
        graph = car_audio.graph(attributes, car, resources)
        tiers = tier_plan(graph)
        attrib = car_audio.AudioAttributes(attributes)
    vehicle, key = hash32('pvehicle'), hash32(car.lower())
    # Carbon 0053B570 clamps the effective engine upgrade by pvehicle +38.
    max_level = attrib.field(vehicle, key, 0x7708db25)['values'][0]
    default = attrib.field(vehicle, key, 0xb12ccb69)
    default_level = default['values'][0] if default else 0
    require(0 < len(tiers) <= 4 and 0 <= default_level <= max_level <= 4, 'audio upgrade selector')

    version, layout, payload = (3 if console else 2), len(tiers), bytearray(_u32(max_level, default_level))
    if legacy_xk:
        # Keep the prior private installer's approved Jaguar output reproducible.
        graph_bytes = json.dumps(graph, sort_keys=True, separators=(',', ':')).encode()
        version, layout, payload = 1, len(graph_bytes), bytearray(graph_bytes)
        tiers = tiers[:1]
    banks = {}
    needed = set()
    for layers in tiers:
        for (key, paths), interface in zip(layers, ('CAR_ENG', 'CAR')):
            require(banks.setdefault(paths[0], interface) == interface, 'audio conflicting layer roles')
            needed.update(paths)
    if console:
        require(set(resources) == needed, 'Xenon audio resource closure differs')
        rows = [inventory.get(PurePosixPath(path).name) for path in sorted(needed)]
    elif legacy_xk:
        rows = graph['resources']
    else:
        rows = sorted((row for row in graph['resources'] if row['path'] in needed),
                      key=lambda row: row['path'].casefold())
    index = {row['path']: i for i, row in enumerate(rows)}
    for layers in tiers:
        for key, paths in layers:
            payload += calibration(attrib, key) + _u32(*(index[path] for path in paths))
    for row in rows:
        path = row['path']
        data = inventory.files[path.casefold()][1]
        plan, sounds = b'', ()
        if row['kind'] == 'gin':
            validate_gin(data)
        else:
            if console:
                from . import car_audio_x360
                native = car_audio_x360.translate(data, row)
                profile = dict(car_audio.bank_inventory(native), kind='abk', size=len(native),
                               sha256=hashlib.sha256(native).hexdigest())
                profile['sounds'] = row['sounds']
                report = abi.inspect_bank(native, profile)
            else:
                report = abi.inspect_bank(data, row)
            if path in banks:
                require(len(report['modules']) == 1, 'audio bank module count')
                module = report['modules'][0]
                require(module['interface'] == banks[path] and module['member_words'] == 100 and
                        all(p['classification'] == abi.TRANSLATED for p in module['players']) and
                        set(report['blockers']) <= PLAYER_ADAPTERS |
                        {'native program semantics and adapters require separate admission proof'},
                        'audio unsupported engine program: ' + path)
                plan = execution_plan(report)
                if console:
                    plan = _u32(len(native)) + native + plan
            sounds = row['sounds'] if console else decode_bank(data)
        payload += _u32(1 if row['kind'] == 'gin' else 2, len(data), len(plan), len(sounds))
        payload += hashlib.sha256(data).digest() + data + plan
        for sound in sounds:
            if sound is None or (console and sound.get('empty')):
                payload += _u32(0, 0, 0, 0)
            elif console:
                loop = sound['loop_start']
                payload += _u32(sound['frames'], sound['rate'], 0 if loop is None else loop + 1, 0)
            else:
                s = sound.sound
                require(s.channels == 1 and s.rate == 44100, 'audio unsupported PCM rate/channels')
                require(not legacy_xk or s.loop_start in (None, 0), 'legacy audio loop')
                loop = 0 if s.loop_start is None else s.loop_start + 1
                payload += _u32(s.frames, s.rate, loop, len(sound.pcm)) + sound.pcm
    require(HEADER.size + len(payload) <= MAX_SIZE, 'audio package limit')
    package = HEADER.pack(b'A7CA', version, HEADER.size+len(payload), len(rows), layout,
                          car.lower().encode().ljust(16, b'\0'), hashlib.sha256(payload).digest()) + payload
    require(not legacy_xk or hashlib.sha256(package).hexdigest() == LEGACY_XK_SHA256,
            'audio closure differs from proved legacy XK package')
    return package


def resource_paths(attributes, car, candidates):
    """Resolve the graph's complete resource closure against relative source paths."""
    require(hashlib.sha256(attributes).hexdigest() == ATTRIBUTES, 'audio unsupported attributes revision')
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
    for path in candidates:
        basename = PurePosixPath(path).name.casefold()
        if basename in names:
            require(basename not in paths, 'audio basename collision')
            paths[basename] = path
    require(set(paths) == names, 'audio missing reachable resources')
    return sorted(paths.values())


def from_directory(attributes, car, root, read, *, legacy_xk=False):
    """Read only graph-reachable resources through the converter input tracker."""
    paths = (p.relative_to(root).as_posix() for p in (root / 'SOUND/ENGINE').rglob('*') if p.is_file())
    selected = resource_paths(attributes, car, paths)
    return build(attributes, car, {path: read(root / path) for path in selected}, legacy_xk=legacy_xk)


def resource_name(package_digest, resource_digest):
    require(all(len(x) == 64 and all(c in '0123456789abcdef' for c in x)
                for x in (package_digest, resource_digest)), 'audio digest identity')
    return str(PurePosixPath('SOUND/ADDCAR_AUDIO') / package_digest / resource_digest)
