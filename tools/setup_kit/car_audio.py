"""Read source audio graphs without changing donor data or generating sound.

Only the audited little endian vault / ABKC and big endian S10A combination
is supported. Callers provide source identity and resource bytes explicitly.
"""
import hashlib
import math
from pathlib import PurePosixPath
import struct

from .car_convert import FitAttributes, hash32, span, u32, words, require

VERSION = 1
REF = hash32('Attrib::RefSpec')
STRING = hash32('Attrib::StringKey')
TEXT = hash32('EA::Reflection::Text')
ENGINE = hash32('engineaudio')
CAR = hash32('caraudio')
PRIMITIVES = {
    hash32('EA::Reflection::Float'): 'f',
    hash32('EA::Reflection::UInt32'): 'I',
    hash32('EA::Reflection::Int32'): 'i',
    hash32('EA::Reflection::UInt16'): 'H',
    hash32('EA::Reflection::Bool'): 'B',
    hash32('Attrib::Types::Matrix'): '16f',
}


def identity(cls, key):
    return f'{cls:08x}:{key:08x}'


class AudioAttributes(FitAttributes):
    """Bounded typed reader; absent optional fields differ from broken pointers."""
    def lineage(self, cls, key):
        chain = []
        while key:
            require(key not in chain and len(chain) < 64, 'audio inheritance cycle/depth')
            require((cls, key) in self.collections, f'audio missing collection {identity(cls, key)}')
            chain.append(key)
            vault, off = self.collections[cls, key]
            key = u32(vault.buffers[0], off + 8)
        return chain

    def field(self, cls, key, field):
        require(cls in self.defs and field in self.defs[cls], 'audio missing field definition')
        dt, at, size, maximum, flags, align = self.defs[cls][field]
        require(size > 0 and maximum > 0, 'audio invalid field shape')
        for owner in self.lineage(cls, key):
            vault, off = self.collections[cls, owner]
            raw = vault.buffers[0]
            target = None
            if flags & 2:
                layout = vault.pointer(0, off + 28)
                if layout:
                    target = (layout[0], layout[1] + at)
            else:
                nt = words(raw, off + 24, '<H')[0] if vault.modern else u32(raw, off + 24)
                start = off + ((32 + nt * 4 + 7) & ~7) if vault.modern else off + 32 + nt * 4
                entries = span(raw, start, u32(raw, off + 20) * 12, 'audio entries range')
                hits = [start + i for i in range(0, len(entries), 12) if u32(entries, i) == field]
                require(len(hits) <= 1, 'audio duplicate field')
                if hits:
                    entry = hits[0]
                    inline = raw[entry + 10] & (0x40 if vault.modern else 0x20)
                    require(not inline or (size <= 4 and not flags & 1), 'audio inline field shape')
                    target = (0, entry + 4) if inline else vault.pointer(0, entry + 4)
                    require(target is not None, 'audio unresolved field pointer')
            if target is None:
                continue
            dep, pos = target
            count = 1
            if flags & 1:
                alloc, count, esz, enc = words(vault.buffers[dep], pos, '<4H')
                require(count <= alloc <= maximum and esz == size, 'audio array shape')
                pos += 8 + (8 if enc & 0x8000 else 0)
            span(vault.buffers[dep], pos, count * size, 'audio value range')
            values = [self.atom(vault, dep, pos + i * size, dt, size) for i in range(count)]
            return {'type': f'{dt:08x}', 'array': bool(flags & 1),
                    'owner': identity(cls, owner), 'values': values,
                    'definition': {'element_size': size, 'maximum': maximum,
                                   'flags': flags, 'alignment': align}}
        return None

    @staticmethod
    def string(vault, dep, pos):
        target = vault.pointer(dep, pos)
        if target is None:
            require(u32(vault.buffers[dep], pos) == 0, 'audio unresolved string')
            return None
        data = vault.buffers[target[0]]
        end = data.find(b'\0', target[1], target[1] + 4096)
        require(end >= target[1], 'audio unterminated string')
        return bytes(data[target[1]:end]).decode('ascii')

    def atom(self, vault, dep, pos, dt, size):
        data = vault.buffers[dep]
        if dt == REF:
            require(size == 12, 'audio reference size')
            cls, key, reserved = words(data, pos, '<3I')
            require(reserved == 0, 'audio reference reserved word')
            return {'class': f'{cls:08x}', 'key': f'{key:08x}'}
        if dt == STRING:
            require(size == 8, 'unsupported audio string-key layout')
            text = self.string(vault, dep, pos + 4)
            key = u32(data, pos)
            require(text is None or key == hash32(text), 'audio string-key identity mismatch')
            return {'hash': f'{key:08x}', 'text': text}
        if dt == TEXT:
            require(size == 4, 'audio text size')
            return self.string(vault, dep, pos)
        fmt = PRIMITIVES.get(dt)
        if fmt:
            require(struct.calcsize('<' + fmt) == size, 'audio primitive size')
            values = list(words(data, pos, '<' + fmt))
            require(all(not isinstance(v, float) or math.isfinite(v) for v in values), 'audio nonfinite value')
            return values[0] if len(values) == 1 else values
        # Source-specific inline records preserve every byte plus relocations.
        # No process/source pointer is emitted. Semantic interpretation is later.
        payload = bytearray(span(data, pos, size))
        strings = {}
        for offset in range(0, size - 3, 4):
            if (dep, pos + offset) in vault.fixups:
                strings[str(offset)] = self.string(vault, dep, pos + offset)
                payload[offset:offset + 4] = b'\0' * 4
        return {'bytes': payload.hex(), 'strings': strings}


def bank_inventory(data):
    """Inventory embedded modules and sample identities, without decoding PCM."""
    require(len(data) >= 0x60 and data[:4] == b'ABKC', 'unsupported bank: expected ABKC')
    require(u32(data, 0x14) == len(data), 'ABKC declared size')
    count = words(data, 10, '<H')[0]
    table, bank, extent = words(data, 0x1c, '<3I')
    require(bank >= 0x60 and extent >= 12, 'ABKC bank extent')
    s10 = span(data, bank, extent, 'ABKC bank outside file')
    require(s10[:4] == b'S10A', 'unsupported bank payload: expected S10A')
    require(int.from_bytes(s10[4:8], 'big') == 0, 'S10A reserved word')
    n = int.from_bytes(s10[8:12], 'big')
    require(n <= 4096, 'S10A sound limit')
    offsets = words(s10, 12, '>' + str(n) + 'I')
    sounds = []
    previous = 0
    for slot, off in enumerate(offsets):
        if off == 0xffffffff:
            sounds.append({'slot': slot, 'empty': True})
            continue
        require(off >= 12 + 4 * n and off > previous, 'S10A sound offset/order')
        a, b = words(s10, off, '>2I')
        loop = bool(b >> 29 & 1)
        loop_start = words(s10, off + 8, '>I')[0] if loop else None
        version, codec, channels, rate = a >> 28, a >> 24 & 15, (a >> 18 & 63) + 1, a & 0x3ffff
        require(version == 0 and codec == 4 and channels == 1 and rate == 44100,
                'unsupported S10A version/codec/channels/rate')
        require(b >> 30 == 0, 'unsupported S10A nonresident resource dependency')
        frames = b & 0x1fffffff
        require(frames > 0 and (not loop or loop_start < frames), 'S10A frames/loop')
        sounds.append({'slot': slot, 'offset': off, 'version': version, 'codec': codec,
                       'channels': channels, 'rate': rate, 'frames': frames, 'loop_start': loop_start})
        previous = off
    # Module table structure is retained as a bounded dependency identity. ABI
    # and instruction validation belong to the bank adapter, not graph admission.
    require(count <= 1024, 'ABKC module limit')
    require(count == 0 or 0x28 <= table < bank, 'ABKC module table range')
    modules = span(data, table, bank - table) if count else b''
    return {'sounds': sounds, 'module_count': count,
            'module_region_offset': table, 'module_region_size': len(modules),
            'module_region_sha256': hashlib.sha256(modules).hexdigest()}


class Resources:
    """Exact source paths; case and basename ambiguity are rejected up front."""
    def __init__(self, files):
        self.files, self.basenames = {}, {}
        for path, data in files.items():
            path = path.replace('\\', '/')
            p = PurePosixPath(path)
            require(not p.is_absolute() and '..' not in p.parts and str(p) == path,
                    'audio resource path is not canonical relative path')
            key = path.casefold()
            base = p.name.casefold()
            require(key not in self.files, 'audio case collision')
            require(base not in self.basenames, 'audio basename collision')
            self.files[key] = (path, data)
            self.basenames[base] = key

    def get(self, name):
        require(isinstance(name, str) and name and '/' not in name and '\\' not in name,
                'audio resource name must be a basename')
        key = self.basenames.get(name.casefold())
        require(key is not None, 'audio missing resource: ' + name)
        path, data = self.files[key]
        suffix = PurePosixPath(path).suffix.casefold()
        result = {'path': path, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
        if suffix == '.gin':
            require(data.startswith(b'Gnsu20'), 'unsupported GIN signature')
            result['kind'] = 'gin'
        elif suffix == '.abk':
            result['kind'] = 'abk'
            result.update(bank_inventory(data))
        else:
            raise ValueError('unsupported audio resource extension: ' + path)
        return result


def graph(attributes, car, resources, *, game='carbon', platform='pc'):
    """Emit a JSON-compatible closed graph; invalid inputs raise ValueError.

    Resources is a relative-path -> bytes mapping. No donor argument is accepted.
    Ordered Audio entries retain authored tier indices; reference edges retain
    field IDs and element indices, including secondary engineaudio dependencies.
    """
    require(game == 'carbon' and platform == 'pc',
            f'unsupported audio source/platform: {game}/{platform}; schema and resources not audited')
    attrib = AudioAttributes(attributes)
    inventory = Resources(resources)
    vehicle, audio = hash32('pvehicle'), hash32('Audio')
    key = hash32(car.lower())
    tiers = attrib.field(vehicle, key, audio)
    require(tiers is not None and tiers['values'], 'audio missing pvehicle Audio tiers')
    require(tiers['type'] == f'{REF:08x}' and tiers['array'], 'audio Audio must be a reference array')
    nodes, active, found = {}, set(), {}

    def visit(cls, target):
        node_id = identity(cls, target)
        require(node_id not in active, 'audio reference cycle: ' + node_id)
        if node_id in nodes:
            return node_id
        require(len(active) < 64, 'audio reference depth exceeds 64 nodes')
        require(cls in attrib.defs, 'audio missing class: ' + f'{cls:08x}')
        lineage = attrib.lineage(cls, target)
        active.add(node_id)
        fields, edges = {}, []
        for fk in sorted(attrib.defs[cls]):
            field = attrib.field(cls, target, fk)
            if field is None:
                continue
            fields[f'{fk:08x}'] = field
            dt = int(field['type'], 16)
            for index, value in enumerate(field['values']):
                if dt == REF:
                    c, k = int(value['class'], 16), int(value['key'], 16)
                    if not k:
                        continue
                    # An inherited link back to this derived collection is
                    # authored in the source. Retain it as a terminal alias.
                    inherited_self = (cls == ENGINE and fk == 0x80730597 and c == cls
                                      and k == target and field['owner'] != node_id)
                    child = node_id if inherited_self else visit(c, k)
                    edges.append({'field': f'{fk:08x}', 'index': index, 'target': child,
                                  'inherited_self': inherited_self})
                elif dt == STRING:
                    # The source's SPU bank alternatives are console-only.
                    if cls == ENGINE and fk == 0x04935eab:
                        field['excluded'] = 'SPU bank alternatives are not PC dependencies'
                        continue
                    text = value['text']
                    require(text is not None or value['hash'] == '00000000', 'audio missing resource string')
                    if text:
                        resource = inventory.get(text)
                        found[resource['path']] = resource
                        field.setdefault('resources', []).append({'index': index, 'path': resource['path']})
        if cls == ENGINE:
            for fk in (0x2cee0a54, 0x3a7c2f3d, 0x2fc9c96f):
                require(fields.get(f'{fk:08x}', {}).get('resources'),
                        'audio missing acceleration/deceleration/bank resource')
        if cls == CAR:
            for fk in (ENGINE, hash32('exhaustaudio')):
                require(f'{fk:08x}' in fields, 'audio missing engine or exhaust layer')
                require(fields[f'{fk:08x}']['type'] == f'{REF:08x}', 'audio layer reference type')
                refs = fields[f'{fk:08x}']['values']
                require(len(refs) == 1 and int(refs[0]['class'], 16) == ENGINE
                        and int(refs[0]['key'], 16) != 0, 'audio layer must target engineaudio')
        nodes[node_id] = {'class': f'{cls:08x}', 'key': f'{target:08x}',
                          'lineage': [identity(cls, k) for k in lineage], 'fields': fields, 'edges': edges}
        active.remove(node_id)
        return node_id

    roots = []
    for index, ref in enumerate(tiers['values']):
        require(int(ref['class'], 16) == CAR and int(ref['key'], 16) != 0,
                'audio tier must target caraudio')
        roots.append({'tier': index, 'target': visit(CAR, int(ref['key'], 16))})
    return {'schema': 'source-car-audio', 'version': VERSION,
            'source': {'game': game, 'platform': platform, 'car': car.lower(),
                       'attributes_sha256': hashlib.sha256(attributes).hexdigest()},
            'audio': tiers, 'tiers': roots, 'nodes': dict(sorted(nodes.items())),
            'resources': [found[p] for p in sorted(found)]}


def inventory(attributes, cars, resources, *, game='carbon', platform='pc'):
    """Classify every requested car independently, with concrete rejection reasons."""
    supported, unsupported = {}, {}
    for car in cars:
        try:
            supported[car] = graph(attributes, car, resources, game=game, platform=platform)
        except (ValueError, KeyError, UnicodeError, struct.error) as error:
            unsupported[car] = str(error)
    unique = {r['path']: r for g in supported.values() for r in g['resources']}
    return {'version': VERSION, 'supported': supported, 'unsupported': unsupported,
            'counts': {'gin': sum(r['kind'] == 'gin' for r in unique.values()),
                       'banks': sum(r['kind'] == 'abk' for r in unique.values()),
                       'sounds': sum(not s.get('empty', False) for r in unique.values()
                                     for s in r.get('sounds', []))}}
