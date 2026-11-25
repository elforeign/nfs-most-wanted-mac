"""Undercover PC stock car tuning projected onto MW performance layouts."""

from bisect import bisect_right
import math
import struct

from . import car_performance
from .car_convert import FitAttributes, hash32, read_vpak, require, span, u32, words


TUNING = 'car_tuning'
STOCK_ALIASES = {'LAM_GAL_SUP_08': 'LAM_GAL_560_09'}
DONOR_FIELDS = frozenset(('HandlingRating', 'TORQUE_CONVERTER', 'VACUUM', 0xCF11AF87,
                          'PSI', 'AERO_COEFFICIENT', 'BRAKE_LOCK'))


def vaults(data):
    """Normalize the extra zero dword at the end of the relocation table."""
    for name, binary, vault in read_vpak(data):
        pos = 0
        while pos < len(vault):
            tag, size = words(vault, pos, '<4sI')
            if size == 0:
                break
            require(8 <= size <= len(vault) - pos, 'UC PC vault chunk extent')
            if tag == b'NrtP' and pos + size == len(vault) and (size - 8) % 12 == 4:
                require(not any(vault[-4:]), 'UC PC pointer table padding')
                vault = vault[:pos + 4] + struct.pack('<I', size - 4) + vault[pos + 8:-4]
                break
            pos += size
        yield name, binary, vault


def stock_key(attrib, car):
    own = hash32(STOCK_ALIASES.get(car.upper(), car).lower())
    return attrib.linked_key('pvehicle', own, TUNING)


def curve(attrib, key, field, count=1):
    """Read relocated normalized cubic splines from a tuning collection."""
    vault, dep, pos, size, capacity, flags = attrib.field_site(TUNING, key, field)
    require((size, capacity, flags) == (48 * count, 1, 38), f'Undercover spline schema: {field}')
    out = []
    for at in range(pos, pos + size, 48):
        raw = span(vault.buffers[dep], at, 48, 'Undercover spline header')
        bounds = struct.unpack_from('<4f', raw)
        x_min, x_max, y_min, y_max = bounds
        counts = struct.unpack_from('<6I', raw, 20)[1::2]
        length = counts[0]
        require(counts == (length, length, length) and 2 <= length <= 64 and
                all(math.isfinite(v) for v in bounds) and x_min < x_max and y_min <= y_max,
                f'Undercover spline bounds: {field}')
        vectors = []
        for offset in (20, 28, 36):
            target = vault.pointer(dep, at + offset)
            require(target is not None, f'Undercover spline relocation: {field}')
            sec, loc = target
            values = words(vault.buffers[sec], loc, f'<{length}f')
            require(all(math.isfinite(v) for v in values), f'Undercover spline vector: {field}')
            vectors.append(values)
        xs = vectors[0]
        require(0 <= xs[0] < xs[-1] <= 1 and all(a < b for a, b in zip(xs, xs[1:])),
                f'Undercover spline knots: {field}')
        out.append((bounds, *vectors))
    return out


def evaluate(spline, value, *, torque=False):
    (x_min, x_max, y_min, y_max), xs, ys, second = spline
    x = min(1.0, max(0.0, (value - x_min) / (x_max - x_min)))
    hi = max(1, min(bisect_right(xs, x), len(xs) - 1))
    lo = hi - 1
    width = xs[hi] - xs[lo]
    b = (x - xs[lo]) / width
    a = 1.0 - b
    y = (a * ys[lo] + b * ys[hi] +
         ((a**3 - a) * second[lo] + (b**3 - b) * second[hi]) * width**2 / 6)
    result = y_min + (y_max - y_min) * y
    if torque:
        if 0 < x_min and value < x_min:
            return result * value / x_min
        if value > x_max:
            return y_min + (y_max - y_min) * ys[-1]
    return result if torque else min(y_max, max(y_min, result))


def performance(attributes, donor_data, car, donor):
    sibling = car_performance.sibling_record(donor_data, car, donor)
    if sibling is not None:
        return sibling
    return source_performance(attributes, donor_data, car, donor)


def source_performance(attributes, donor_data, car, donor):
    """Serialize the stock inputs MW can consume. Keep its chassis and kit model."""
    attrib = FitAttributes(attributes, vaults=vaults(attributes))
    native = FitAttributes(donor_data)
    own = stock_key(attrib, car)
    retail = hash32(donor.lower())

    def source(name):
        return attrib.floats(TUNING, own, (name,))

    idle, = source('IDLE')
    maximum, = source('MAX_RPM')
    require(0 < idle < maximum, 'Undercover engine RPM range')
    torque, = curve(attrib, own, 'TORQUE')
    steering, = curve(attrib, own, 'STEERING')
    handbrake, = curve(attrib, own, 'EBRAKE')
    onset, cutoff = source('SPEED_LIMITER')
    require(0 < onset < cutoff, 'Undercover limiter thresholds')
    overrides = {
        'TORQUE': tuple(evaluate(torque, idle + (maximum - idle) * i / 8, torque=True)
                        for i in range(9)),
        'SPEED_LIMITER': (onset, cutoff - onset),
        # Undercover initializes the static brake torques at zero mph. MW
        # expects torque before its service and handbrake multipliers.
        'BRAKES': tuple(evaluate(spline, 0) / 4 for spline in curve(attrib, own, 'BRAKES', 2)),
        'EBRAKE': (evaluate(handbrake, 0) / 10,),
        'STEERING': (evaluate(steering, 0),),
    }
    records = []
    for cls, fields in (('pvehicle', car_performance.VEHICLE), *car_performance.LINKS[:-1]):
        donor_key = retail if cls == 'pvehicle' else native.linked_key('pvehicle', retail, cls)
        values = []
        for name, _, _, capacity, _ in fields:
            if name in overrides:
                value = overrides[name]
            elif name in DONOR_FIELDS:
                value = native.floats(cls, donor_key, (name,))
            else:
                value = source(name)
                if name == 'GEAR_RATIO':
                    value = value[:capacity]  # MW has seven forward slots.
                elif name == 0x5250EB92:
                    value = value[:capacity] + native.floats(cls, donor_key, (name,))[len(value):capacity]
            require((3 <= len(value) <= capacity if name == 'GEAR_RATIO' else len(value) == capacity) and
                    all(math.isfinite(v) and abs(v) <= 100000 for v in value),
                    f'Undercover performance field: {cls}.{name}')
            values.append(value)
        records.append(values)
    return car_performance.serialize(records, car, donor, torque_spline=torque)
