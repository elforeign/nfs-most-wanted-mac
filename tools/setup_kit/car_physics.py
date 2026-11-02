"""Serialize inherited body mass and inertia as values, without vault pointers."""
import math
import struct

from .car_convert import FitAttributes, hash32, require

MAGIC = 0x31534850


def metadata(data, car, donor):
    attrib = FitAttributes(data)
    # Wheel-only inputs stay valid without this sidecar, but the car then keeps
    # donor physics. A present vehicle schema must resolve both fields.
    if hash32('pvehicle') not in attrib.defs:
        print(f'warning: {car}: source has no pvehicle schema; PHYSICS.BIN omitted, donor physics kept')
        return None
    key = hash32(car.lower())
    mass = attrib.raw('pvehicle', key, 'MASS')
    tensor = attrib.raw('pvehicle', key, 'TENSOR_SCALE')
    require(len(mass) == 4 and len(tensor) == 16, 'physics field shape')
    values = struct.unpack('<5f', mass + tensor)
    require(all(math.isfinite(v) for v in values), 'physics nonfinite value')
    require(0 < values[0] <= 100000 and all(0 < v <= 100 for v in values[1:4])
            and values[4] == 0, 'physics value range')
    return struct.pack('<4I', MAGIC, 1, key, hash32(donor.lower())) + mass + tensor
