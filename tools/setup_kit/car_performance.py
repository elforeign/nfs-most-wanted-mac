"""Carbon stock performance values for the MW class layouts (CPF3)."""
import math
import struct

from .car_convert import FitAttributes, hash32, require

# name, Carbon offset, MW offset, float count/capacity, array
VEHICLE = (
    ('MASS', 0x40, 0x38, 1, False),
    ('TENSOR_SCALE', 0, 0, 4, False),
    ('HandlingRating', 0, 0, 2, True),  # sparse float array
)
LINKS = (
    ('engine', (
        ('TORQUE', 0, 0, 9, True),
        ('SPEED_LIMITER', 0x2C, 0x2C, 2, True),
        ('ENGINE_BRAKING', 0x3C, 0x3C, 3, True),
        ('FLYWHEEL_MASS', 0x50, 0x50, 1, False),
        ('MAX_RPM', 0x54, 0x54, 1, False),
        ('RED_LINE', 0x58, 0x58, 1, False),
        ('IDLE', 0x5C, 0x5C, 1, False),
    )),
    ('transmission', (
        ('GEAR_RATIO', 0, 0, 9, True),
        ('DIFFERENTIAL', 0x2C, 0x2C, 3, True),
        (0x5250EB92, 0x40, 0x40, 9, True),
        ('TORQUE_CONVERTER', 0x6C, 0x6C, 1, False),
        (0x7381EE38, 0x70, 0x70, 1, False),
        (0xCF5DD4E9, 0x74, 0x74, 1, False),
        (0x8DC16371, 0x78, 0x78, 1, False),
        ('SHIFT_SPEED', 0x7C, 0x7C, 1, False),
        ('FINAL_GEAR', 0x80, 0x80, 1, False),
    )),
    ('induction', (
        ('LOW_BOOST', 0, 0, 1, False),
        (0x1F158097, 4, 4, 1, False),
        ('VACUUM', 8, 8, 1, False),
        (0xCF11AF87, 0xC, 0xC, 1, False),
        (0xAC1C0D2F, 0x10, 0x10, 1, False),
        ('PSI', 0x14, 0x14, 1, False),
        ('HIGH_BOOST', 0x18, 0x18, 1, False),
    )),
    ('chassis', (
        ('AERO_COEFFICIENT', 0x70, 0x68, 1, False),
        ('DRAG_COEFFICIENT', 0x78, 0x70, 1, False),
    )),
    ('brakes', (
        ('BRAKE_LOCK', 0, 0, 2, False),
        ('BRAKES', 8, 8, 2, False),
        ('EBRAKE', 0x10, 0x10, 1, False),
    )),
    ('tires', (
        ('STEERING', 0x78, 0x48, 1, False),
    )),
    ('nos', (
        # Keep the CPF2 fields first for the native legacy reader.
        ('TORQUE_BOOST', 4, 4, 1, False),
        ('NOS_CAPACITY', 0x10, 0x10, 1, False),
        ('NOS_DISENGAGE', 0, 0, 1, False),
        ('FLOW_RATE', 8, 8, 1, False),
        ('RECHARGE_MIN', 0xC, 0xC, 1, False),
        ('RECHARGE_MAX', 0x14, 0x14, 1, False),
        ('RECHARGE_MAX_SPEED', 0x18, 0x18, 1, False),
        ('RECHARGE_MIN_SPEED', 0x1C, 0x1C, 1, False),
    )),
)

MAGIC = b'CPF3'


def decode(attrib, car, endian='<'):
    """Resolve stock links in the source vault and validate the field schema."""
    key = hash32(car.lower())

    def values(cls, collection, fields):
        result = []
        for name, source, _, capacity, array in fields:
            ident = hash32(name) if isinstance(name, str) else name
            typ, offset, width, maximum, flags, _ = attrib.defs[hash32(cls)][ident]
            sparse = name == 'HandlingRating'
            size = 4 if array else 4 * capacity
            type_key = {4: 0x3C16EC5E, 8: 0x4CB36381, 16: 0x34FDE6BB}[size]
            require((typ, offset, width, maximum, flags) ==
                    (type_key, source, size, capacity if array else 1, 5 if sparse else 7 if array else 6),
                    f'Carbon performance field schema: {cls}.{name}')
            raw = attrib.raw(cls, collection, name, allow_empty=name == 'SPEED_LIMITER')
            count = len(raw) // 4
            require(len(raw) == count * 4 and
                    (3 <= count <= capacity if name == 'GEAR_RATIO' else
                     count <= capacity if name == 'SPEED_LIMITER' else count == capacity),
                    f'Carbon performance field count: {cls}.{name}')
            value = struct.unpack(f'{endian}{count}f', raw)
            require(all(math.isfinite(v) and abs(v) <= 100000 for v in value),
                    f'Carbon performance field values: {cls}.{name}')
            result.append(value)
        return result

    records = [values('pvehicle', key, VEHICLE)]
    for cls, fields in LINKS:
        kind = hash32(cls)
        refs = attrib.raw('pvehicle', key, cls)
        require(len(refs) in (12, 24, 36), f'Carbon performance link count: {cls}')
        endpoints = list(struct.iter_unpack(endian + '3I', refs))
        require(all(c == kind and k and flags == 0 for c, k, flags in endpoints),
                f'Carbon performance reference: {cls}')
        records.append(values(cls, endpoints[0][1], fields))
    return records


def metadata(data, car, donor, *, attrib=None, endian='<'):
    if attrib is None:
        attrib = FitAttributes(data)
    if hash32('pvehicle') not in attrib.defs or hash32('engine') not in attrib.defs:
        return None
    return serialize(decode(attrib, car, endian), car, donor)


def serialize(records, car, donor):
    """Write MW stock fields. CPF1 keeps the complete MW nitrous setup."""
    blocks = (('pvehicle', VEHICLE), *LINKS)
    require(len(records) in (len(blocks) - 1, len(blocks)), 'performance block count')
    magic = MAGIC if len(records) == len(blocks) else b'CPF1'
    body = bytearray()
    for (_, fields), values in zip(blocks, records):
        require(len(fields) == len(values), 'performance field count')
        for (_, _, _, capacity, array), value in zip(fields, values):
            if array:
                body += struct.pack('<I', len(value))
            body += struct.pack(f'<{capacity}f', *value, *([0.0] * (capacity - len(value))))
    return struct.pack('<4s3I', magic, 16 + len(body), hash32(car.lower()), hash32(donor.lower())) + body
