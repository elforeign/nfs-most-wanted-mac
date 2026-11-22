"""ProStreet added-car wheel fit, body and stock performance."""

from bisect import bisect_right
import math
import struct

from . import car_performance
from .car_convert import (CAMERA_FIELDS, CAMERA_LINKS, RENDER_FIELDS, TIRE_FIELDS, WHEEL_FIELDS,
                          FitAttributes, hash32, require, span, wheel_fields)


SOURCE_CHASSIS = ('TRACK_WIDTH', 'WHEEL_BASE', 'FRONT_AXLE')
DONOR_CHASSIS = ('RIDE_HEIGHT', 'TRAVEL')
DONOR_PERFORMANCE = frozenset(('TORQUE_CONVERTER', 'VACUUM', 0xCF11AF87, 'PSI', 'BRAKE_LOCK'))


def pvehicle_key(attrib, car):
    """Download cars are filed as dlcbp_<car>."""
    pvehicle = hash32('pvehicle')
    found = [k for k in (hash32(car.lower()), hash32('dlcbp_' + car.lower())) if (pvehicle, k) in attrib.collections]
    require(len(found) == 1, 'ProStreet pvehicle collection missing or ambiguous')
    return found[0]


def keys(attrib, car):
    """(vehicle key, ecar key) of a car."""
    own = pvehicle_key(attrib, car)
    ecar = attrib.linked_key('pvehicle', own, 'ecar')
    require(ecar == hash32(car.lower()), 'ProStreet render car differs from the folder name')
    return attrib.linked_key('pvehicle', own, 'vehicle'), ecar


def values(data, car, *, attrib=None):
    """Decoded source values, named as in the vault (floats; tuples for axle pairs)."""
    if attrib is None:
        attrib = FitAttributes(data)
    vehicle, ecar = keys(attrib, car)
    out = {name: attrib.floats('vehicle', vehicle, (name,))
           for name in ('MASS', 'TENSOR_SCALE') + SOURCE_CHASSIS + TIRE_FIELDS}
    out.update({name: attrib.floats('ecar', ecar, (name,)) for name in RENDER_FIELDS})
    out['WheelSpokeCount'] = attrib.raw('ecar', ecar, 'WheelSpokeCount')
    out.update(wheel_fields(data, car))
    return out


def physics(source, car, donor):
    """PHS1 from values(), as setup_kit.car_physics.metadata writes it for Carbon."""
    mass, tensor = source['MASS'], source['TENSOR_SCALE']
    require(len(mass) == 1 and len(tensor) == 4, 'physics field shape')
    require(0 < mass[0] <= 100000 and all(0 < v <= 100 for v in tensor[:3]) and tensor[3] == 0,
            'physics value range')
    return struct.pack('<4I5f', 0x31534850, 1, hash32(car.lower()), hash32(donor.lower()), *mass, *tensor)


def wheel_fit(source, donor_data, car, donor):
    """WFT2 from values(): source wheels, body motion, track, wheelbase, tyres; donor travel and cameras."""
    native = donor_data if isinstance(donor_data, FitAttributes) else FitAttributes(donor_data)
    key = hash32(donor.lower())
    chassis = native.floats('chassis', native.linked_key('pvehicle', key, 'chassis'), DONOR_CHASSIS)
    cameras = tuple(v for link in CAMERA_LINKS for v in native.floats(
        'camerainfo', native.linked_key('ecar', key, link), CAMERA_FIELDS))
    render = tuple(v for name in RENDER_FIELDS for v in source[name])
    chassis += tuple(v for name in SOURCE_CHASSIS for v in source[name])
    tires = tuple(v for name in TIRE_FIELDS for v in source[name])
    require((len(render), len(source['WheelSpokeCount']), len(chassis), len(tires), len(cameras)) == (11, 1, 8, 6, 40),
            'fit schema counts')
    require(all(math.isfinite(v) and v > 0 for v in chassis[4:] + tires), 'fit dimension range')
    out = struct.pack('<4I', 0x31544657, 2, hash32(car.lower()), key)
    for name, fmt, count in WHEEL_FIELDS:
        out += struct.pack('<' + str(count) + fmt, *source[name])
    return out + struct.pack('<11fI54f', *render, source['WheelSpokeCount'][0], *chassis, *tires, *cameras)


def torque_samples(attrib, vehicle, idle, maximum):
    """Evaluate the source cubic spline at the nine MW engine RPM samples."""
    vault, dep, pos, size, _, flags = attrib.field_site('vehicle', vehicle, 'TORQUE')
    require(size == 44 and flags == 6, 'ProStreet torque field schema')
    raw = span(vault.buffers[dep], pos, size, 'ProStreet torque record')
    x_min, x_max, y_min, y_max = struct.unpack_from('<4f', raw)
    counts = struct.unpack_from('<7I', raw, 16)[1::2]
    count = counts[0]
    require(counts == (count, count, count) and 2 <= count <= 64 and
            all(math.isfinite(x) for x in (x_min, x_max, y_min, y_max)) and x_min < x_max and
            y_min <= y_max and 0 < idle < maximum, 'ProStreet torque spline shape')
    vectors = []
    for offset in (16, 24, 32):
        target = vault.pointer(dep, pos + offset)
        require(target is not None, 'ProStreet torque vector unresolved')
        idx, at = target
        vector = struct.unpack(f'<{count}f', span(vault.buffers[idx], at, count * 4, 'ProStreet torque vector'))
        require(all(math.isfinite(x) for x in vector), 'ProStreet nonfinite torque spline')
        vectors.append(vector)
    xs, ys, second = vectors
    require(xs[0] == 0 and xs[-1] == 1 and all(a < b for a, b in zip(xs, xs[1:])),
            'ProStreet torque knot order')

    def torque(rpm):
        x = min(1.0, max(0.0, (rpm - x_min) / (x_max - x_min)))
        hi = min(bisect_right(xs, x), count - 1)
        lo = hi - 1
        width = xs[hi] - xs[lo]
        b = (x - xs[lo]) / width
        a = 1.0 - b
        value = (a * ys[lo] + b * ys[hi] +
                 ((a**3 - a) * second[lo] + (b**3 - b) * second[hi]) * width**2 / 6.0)
        result = y_min + (y_max - y_min) * value
        # The source ramps torque from zero when RPM is below a positive first knot.
        return result * rpm / x_min if rpm < x_min and x_min > 0 else result

    return tuple(torque(idle + (maximum - idle) * i / 8) for i in range(9))


def performance(attrib, native, car, donor):
    """Write stock values into the shared MW wire schema."""
    own = pvehicle_key(attrib, car)
    vehicle = attrib.linked_key('pvehicle', own, 'vehicle')
    retail = hash32(donor.lower())

    def floats(vault, cls, key, name):
        raw = vault.raw(cls, key, name, allow_empty=name == 'SPEED_LIMITER')
        return struct.unpack(f'<{len(raw) // 4}f', raw)

    def source(name):
        return floats(attrib, 'vehicle', vehicle, name)

    steering = source('STEERING')
    require(len(steering) == 10 and all(scale == steering[0] for scale in steering),
            'ProStreet steering multiplier is not constant')
    overrides = {
        'HandlingRating': floats(attrib, 'pvehicle', own, 'HandlingRating'),
        'TORQUE': torque_samples(attrib, vehicle, *source('IDLE'), *source('MAX_RPM')),
        'BRAKES': tuple(torque / 4 for torque in source('BRAKES')),
        'STEERING': steering[:1],
    }
    records = []
    # Stock source nitrous and the later-game recharge model have no MW kit mapping.
    for cls, fields in (('pvehicle', car_performance.VEHICLE), *car_performance.LINKS[:-1]):
        donor_key = retail if cls == 'pvehicle' else native.linked_key('pvehicle', retail, cls)
        record = []
        for name, _, _, capacity, _ in fields:
            if name in overrides:
                value = overrides[name]
            elif name in DONOR_PERFORMANCE:
                value = floats(native, cls, donor_key, name)
            else:
                value = source(name)
                if name == 0x5250EB92:  # Efficiency entries beyond the source gear count are unused.
                    donor_efficiency = floats(native, cls, donor_key, name)
                    value = value[:capacity] + donor_efficiency[len(value):capacity]
            require((3 <= len(value) <= capacity if name == 'GEAR_RATIO' else
                     len(value) <= capacity if name == 'SPEED_LIMITER' else len(value) == capacity) and
                    all(math.isfinite(x) and abs(x) <= 100000 for x in value),
                    f'ProStreet performance field: {cls}.{name}')
            record.append(value)
        records.append(record)
    return car_performance.serialize(records, car, donor)


def sidecars(attributes, donor_data, car, donor):
    attrib, native = FitAttributes(attributes), FitAttributes(donor_data)
    decoded = values(attributes, car, attrib=attrib)
    return {'PHYSICS.BIN': physics(decoded, car, donor),
            'WHEELFIT.BIN': wheel_fit(decoded, native, car, donor),
            'PERFORMANCE.BIN': performance(attrib, native, car, donor)}
