"""Stock performance projections for the MW class layouts."""
import math
import struct

from .car_convert import FitAttributes, TIRE_FIELDS, hash32, require

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

# Retail sibling, peak torque ratio, weight ratio, top-speed ratio, handling points.
# Each ratio is added car / retail sibling, with matching units within a pair.
# Handling offsets are authored on MW's stock rating scale.
SIBLINGS = {
    # Mercedes-Benz SLR 722 press information (Jan 2007), SLR technical data: 650/626 PS, Nm, EC kg, km/h.
    'MER_SLR_722_07': ('SLR', 820 / 780, 1724 / 1768, 337 / 334, 2),
    # Lamborghini Murciélago and LP640 coupé technical sheets: 640/580 CV, Nm, dry kg, km/h.
    'MURCIELAGO640': ('MURCIELAGO', 660 / 650, 1650 / 1650, 340 / 330, 2),
    # Lamborghini 2008 Geneva LP560-4 and 2003 Gallardo technical data: 560/500 CV, Nm, dry kg, km/h.
    'LAM_GAL_560_09': ('GALLARDO', 540 / 510, 1410 / 1430, 325 / 309, 2),
    # Lamborghini 2007 Geneva Superleggera and 2003 Gallardo technical data: 530/500 CV, Nm, dry kg, km/h.
    'LAM_GAL_SUP_08': ('GALLARDO', 510 / 510, 1330 / 1430, 315 / 309, 3),
    # Chevrolet 2006 Corvette/Z06 specifications: 505/400 hp, lb-ft, curb lb, mph.
    'CORVETTEZ06': ('CORVETTE', 470 / 400, 3130 / 3179, 198 / 186, 3),
    # Porsche 997 GT3 RS and 2005 Carrera S technical data: 415/355 PS, Nm, DIN kg, km/h.
    '997GT3RS': ('997S', 405 / 400, 1375 / 1420, 310 / 293, 4),
    # Porsche 2007 GT3 and 2005 Carrera S technical data: 415/355 PS, Nm, DIN kg, km/h.
    '997GT3': ('997S', 405 / 400, 1395 / 1420, 310 / 293, 3),
    # Porsche 2008 GT2 and 2005 Carrera S technical data: 530/355 PS, Nm, DIN kg, km/h.
    '997GT2': ('997S', 680 / 400, 1440 / 1420, 329 / 293, 4),
    # Porsche 2007 and 2001 911 Turbo technical data: 480/420 PS, Nm, DIN kg, km/h.
    '997TT': ('911TURBO', 620 / 560, 1585 / 1540, 310 / 305, 2),
    # Mitsubishi 2006/2005 Lancer Evolution US specs: 286/276 hp, lb-ft, curb lb, mph.
    'LANCEREVO9': ('LANCEREVO8', 289 / 286, 3263 / 3263, 157 / 155, 2),
    # Mitsubishi 2008/2005 Lancer Evolution US specs: 291/276 hp, lb-ft, curb lb, mph.
    'LANCEREVOX': ('LANCEREVO8', 300 / 286, 3517 / 3263, 155 / 155, 3),
    # Mitsubishi 1999 GSX / 2006 GT US brochures and contemporary road tests: 210/263 hp, lb-ft, curb lb, mph.
    'ECLIPSE': ('ECLIPSEGT', 214 / 260, 3270 / 3472, 140 / 149, 1),
    # Ford 2005 Mustang and 2007 Shelby GT500 technical data: 500/300 hp, lb-ft, curb lb, mph.
    'MUSTANGSHLBYN': ('MUSTANGGT', 480 / 320, 3920 / 3450, 155 / 149, 2),
    # Ford 2005 Mustang and 2008 Shelby GT500KR technical data: 540/300 hp, lb-ft, curb lb, mph.
    'FOR_SHL_GT_08': ('MUSTANGGT', 510 / 320, 3920 / 3450, 155 / 149, 3),
    # Shelby 2008 Terlingua supercharged package / 2005 Mustang technical data: 375/300 hp, lb-ft, curb lb, mph.
    'FOR_SHL_TER_08': ('MUSTANGGT', 375 / 320, 3450 / 3450, 149 / 149, 3),
    # Ford 2003 and 2005 Mustang GT technical data: 260/300 hp, lb-ft, curb lb, mph.
    'MUSTANG03': ('MUSTANGGT', 302 / 320, 3273 / 3450, 140 / 149, -1),
    # Volkswagen Golf V GTI and R32 technical data: 250/200 PS, Nm, kerb kg, km/h.
    'R32': ('GTI', 320 / 280, 1510 / 1328, 250 / 235, 2),
    # Cadillac 2005 CTS and CTS-V specifications: 400/255 hp, lb-ft, curb lb, mph.
    'CTSV': ('CTS', 395 / 252, 3850 / 3510, 163 / 145, 3),
    # Lexus 2006 IS 350 / 2005 IS 300 US specifications: 306/215 hp, lb-ft, curb lb, mph.
    'IS350': ('IS300', 277 / 218, 3527 / 3285, 144 / 143, 1),
    # Lexus 2008 IS F / 2005 IS 300 US specifications: 416/215 hp, lb-ft, curb lb, mph.
    'LEX_IS_F_08': ('IS300', 371 / 218, 3780 / 3285, 170 / 143, 3),
    # Audi A4 3.2 quattro and S4 B7 saloon technical data: 344/255 PS, Nm, kerb kg, km/h.
    'S4': ('A4', 410 / 330, 1660 / 1550, 250 / 250, 2),
    # Audi A4 3.2 quattro and RS4 B7 saloon technical data: 420/255 PS, Nm, kerb kg, km/h.
    'RS4': ('A4', 430 / 330, 1650 / 1550, 250 / 250, 4),
    # Audi A3 3.2 quattro and S3 8P technical data: 265/250 PS, Nm, kerb kg, km/h.
    'S3': ('A3', 350 / 320, 1455 / 1495, 250 / 250, 2),
    # Audi TT 3.2 quattro 8J and 8N technical data: 250/250 PS, Nm, kerb kg, km/h.
    'TTN': ('TT', 320 / 320, 1430 / 1520, 250 / 250, 2),
    # Dodge 2008 and 2003 Viper SRT-10 technical data: 600/500 hp, lb-ft, curb lb, mph.
    'DOD_VIP_SRT_08': ('VIPER', 560 / 525, 3440 / 3380, 202 / 190, 2),
}


def sibling_record(native, car, donor, *, wheels=None):
    """Project the retail sibling's stock layouts while keeping the catalog donor binding."""
    spec = SIBLINGS.get(car.upper())
    if spec is None:
        return None
    sibling, torque, weight, speed, handling = spec
    key = hash32(sibling.lower())
    if not isinstance(native, FitAttributes):
        native = FitAttributes(native)

    def rear_diameter(tires):
        rim, section, aspect = tires[1::2]
        return rim * 0.0254 + section * aspect * 0.00002

    def mw_diameter(name):
        tire_key = native.linked_key('pvehicle', hash32(name.lower()), 'tires')
        return rear_diameter(native.floats('tires', tire_key, TIRE_FIELDS))

    # WFT2 replaces tyre dimensions after performance layout application. Match
    # the sibling's engine RPM at the scaled road speed on the fitted tyres.
    diameter = (rear_diameter(struct.unpack_from('<6f', wheels, 196))
                if wheels is not None and len(wheels) == 380 else mw_diameter(donor))
    overrides = {
        'MASS': lambda v: (v[0] * weight,),
        'HandlingRating': lambda v: (min(v[0] + handling, v[1] - 1), v[1]),
        'TORQUE': lambda v: tuple(x * torque for x in v),
        'FINAL_GEAR': lambda v: (v[0] * diameter / (mw_diameter(sibling) * speed),),
        'SPEED_LIMITER': lambda v: tuple(x * speed for x in v),
        # At the scaled speed, wheel force is torque_ratio / speed_ratio of
        # stock. Scale drag to give the same balance if power limits top gear.
        'DRAG_COEFFICIENT': lambda v: (v[0] * torque / speed ** 3,),
    }
    records = []
    for cls, fields in (('pvehicle', VEHICLE), *LINKS):
        collection = key if cls == 'pvehicle' else native.linked_key('pvehicle', key, cls)
        values = []
        for name, *_ in fields:
            raw = native.raw(cls, collection, name, allow_empty=name == 'SPEED_LIMITER')
            value = struct.unpack(f'<{len(raw) // 4}f', raw)
            values.append(overrides[name](value) if name in overrides else value)
        records.append(values)
    return serialize(records, car, donor)


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
    records = decode(attrib, car, endian)
    records[-1][:2] = [(0.0,), (0.0,)]  # Keep equipped nitrous; stock has no boost or capacity.
    return serialize(records, car, donor)


def serialize(records, car, donor, *, torque_spline=None):
    """Write stock fields; CPF4 extends CPF1 with normalized cubic torque data."""
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
    if torque_spline is not None:
        require(magic == b'CPF1', 'torque spline requires source stock fields')
        bounds, xs, ys, second = torque_spline
        count = len(xs)
        require(len(bounds) == 4 and 2 <= count <= 64 and len(ys) == len(second) == count and
                all(math.isfinite(v) for vector in torque_spline for v in vector) and
                bounds[0] < bounds[1] and bounds[2] <= bounds[3] and
                xs[0] == 0 and xs[-1] == 1 and all(a < b for a, b in zip(xs, xs[1:])),
                'torque spline shape')
        magic = b'CPF4'
        body += struct.pack('<4fI', *bounds, count)
        body += struct.pack(f'<{3 * count}f', *xs, *ys, *second)
    return struct.pack('<4s3I', magic, 16 + len(body), hash32(car.lower()), hash32(donor.lower())) + body
