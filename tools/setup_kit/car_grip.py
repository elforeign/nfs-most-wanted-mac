"""Value-only tyre metadata; no selection or runtime integration is implied."""
import math
import struct

from .car_convert import FitAttributes, hash32, require, span, u32, words

MAGIC = 0x31505247  # GRP1
VERSION = 1
SHARED = 1
MAX_TIERS = 8
MAX_VALUES = 40
MAX_BYTES = 40 + MAX_TIERS * (8 + 17 * (16 + MAX_VALUES * 4))
# key, flattened count (0 means variable), source schema capacity, semantics
SHARED_FIELDS = (
    ('SECTION_WIDTH', 2), ('YAW_CONTROL', 4), ('DYNAMIC_GRIP', 2),
    ('RIM_SIZE', 2), ('YAW_SPEED', 1), ('ASPECT_RATIO', 2),
    ('STATIC_GRIP', 2), ('GRIP_SCALE', 2), ('STEERING', 1),
)
FIELDS = tuple(sorted(
    [(hash32(n), c, 4 if n == 'YAW_CONTROL' else 1, SHARED)
     for n, c in SHARED_FIELDS] + [
        (0x0D255C03, 1, 1, 0), (0x7CAFC471, 0, 40, 0),
        (0x7DB55397, 1, 1, 0), (0xC141848A, 1, 1, 0),
        (0xC51EB1A1, 0, 65535, 0), (0xD15B2BDE, 0, 10, 0),
        (0xECEFA5B3, 1, 1, 0), (0xF4BBE085, 0, 65535, 0),
    ]))


def raw_field(attrib, cls, key, field, seen=()):
    """FitAttributes inheritance with numeric keys and bounded array counts.

    The common reader's sparse-entry branch hashes names unconditionally.
    Keep this numeric-key seam local until that shared API changes.
    """
    cls = hash32(cls) if isinstance(cls, str) else cls
    field = hash32(field) if isinstance(field, str) else field
    require(key not in seen and len(seen) < 32, 'grip parent cycle/depth')
    require((cls, key) in attrib.collections, 'grip collection missing')
    require(cls in attrib.defs and field in attrib.defs[cls], 'grip field missing')
    vault, off = attrib.collections[cls, key]
    raw = vault.buffers[0]
    _, at, size, maximum, flags, _ = attrib.defs[cls][field]
    target = None
    if flags & 2:
        target = vault.pointer(0, off + 28)
        if target:
            target = (target[0], target[1] + at)
    else:
        nt = words(raw, off + 24, '<H')[0] if vault.modern else u32(raw, off + 24)
        start = off + ((32 + nt * 4 + 7) & ~7) if vault.modern else off + 32 + nt * 4
        entries = span(raw, start, u32(raw, off + 20) * 12, 'grip entries range')
        matches = [i for i in range(len(entries) // 12) if u32(entries, i * 12) == field]
        require(len(matches) <= 1, 'duplicate grip field')
        if matches:
            e = start + matches[0] * 12
            inline = 0x40 if vault.modern else 0x20
            target = (0, e + 4) if raw[e + 10] & inline else vault.pointer(0, e + 4)
            require(target is not None, 'grip pointer unresolved')
    if target is None:
        parent = u32(raw, off + 8)
        require(parent != 0, 'grip inherited field absent')
        return raw_field(attrib, cls, parent, field, seen + (key,))
    dep, pos = target
    if flags & 1:
        alloc, count, esz, enc = words(vault.buffers[dep], pos, '<4H')
        require(0 < count <= alloc <= maximum and esz == size, 'grip array shape')
        require(count <= MAX_VALUES, 'grip array count exceeds contract')
        pos += 8 + (8 if enc & 0x8000 else 0)
        size *= count
    require(0 < size <= MAX_VALUES * 4, 'grip field exceeds contract')
    return bytes(span(vault.buffers[dep], pos, size, 'grip value range'))


def tyre_schema(attrib):
    """Reject new schema semantics rather than silently discarding fields."""
    schema = attrib.defs[hash32('tires')]
    require(set(schema) == {r[0] for r in FIELDS}, 'grip unsupported tyre schema')
    for key, count, capacity, _ in FIELDS:
        typ, _, size, maximum, flags, _ = schema[key]
        array = count == 0 or count == 4
        require((typ, size, maximum, flags) == (
            0x4CB36381 if count == 2 else 0x3C16EC5E,
            8 if count == 2 else 4, capacity,
            (7 if count == 4 or key == 0xD15B2BDE else 5) if array else 6),
            'grip unsupported field shape')


def metadata(data, car, donor, *, mode='shared', attrib=None):
    """Serialize every authored reference, retaining the independent counter.

    Donor mode intentionally never parses data. Missing schemas omit the file;
    an incomplete present schema is an error. Unresolved extras are evidence
    only, and cannot be requested as supported runtime semantics.
    """
    require(mode in ('donor', 'shared'), 'grip unsupported mode')
    if mode == 'donor':
        return None
    attrib = FitAttributes(data) if attrib is None else attrib
    if hash32('pvehicle') not in attrib.defs or hash32('tires') not in attrib.defs:
        return None
    tyre_schema(attrib)
    source, target = hash32(car.lower()), hash32(donor.lower())
    require(source and target and car and donor and len(car) < 64 and len(donor) < 64
            and car.isascii() and donor.isascii() and '\0' not in car and '\0' not in donor,
            'grip identity')
    refs = raw_field(attrib, 'pvehicle', source, 'tires')
    counter = raw_field(attrib, 'pvehicle', source, 'tires_upgrades')
    require(len(refs) % 12 == 0 and 1 <= len(refs) // 12 <= MAX_TIERS,
            'grip reference count')
    require(len(counter) == 4 and u32(counter) <= 8, 'grip upgrade counter')
    body = bytearray()
    for ordinal, (cls, key, flags) in enumerate(struct.iter_unpack('<3I', refs)):
        require(cls == hash32('tires') and key != 0 and flags == 0, 'grip reference identity')
        body += struct.pack('<2I', ordinal, key)
        for field, count, _, semantics in FIELDS:
            raw = raw_field(attrib, cls, key, field)
            require(len(raw) % 4 == 0, 'grip float alignment')
            n = len(raw) // 4
            require(n == count if count else 1 <= n <= (10 if field == 0xD15B2BDE else MAX_VALUES),
                    'grip field count')
            values = struct.unpack('<' + str(n) + 'f', raw)
            require(all(math.isfinite(v) and abs(v) <= 1000000 for v in values), 'grip finite range')
            body += struct.pack('<4I', field, semantics, n, 0) + raw
    size = 40 + len(body)
    require(size <= MAX_BYTES, 'grip file size')
    return struct.pack('<10I', MAGIC, VERSION, size, source, target, SHARED,
                       len(refs) // 12, u32(counter), len(FIELDS), 0) + body
