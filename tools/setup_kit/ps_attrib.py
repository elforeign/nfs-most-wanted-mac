"""ProStreet added cars: body mass and wheel positions from GLOBAL/ATTRIBUTES.BIN.

ProStreet keeps the vault container of Carbon but moved the physical fields:
pvehicle.vehicle[0] names a `vehicle` collection that holds MASS, TENSOR_SCALE,
TRACK_WIDTH, WHEEL_BASE, FRONT_AXLE and the tyre sizes (Carbon: pvehicle, chassis,
tires). The render car (ecar) keeps Carbon's names for wheel offsets and body motion.
Outputs are the sidecars the Carbon path writes: PHYSICS.BIN (PHS1), WHEELFIT.BIN (WFT2).

Two groups are not carried over and are written with the donor's values:
- chassis RIDE_HEIGHT and TRAVEL use another suspension scale in ProStreet
  (DB9: 12/20 there, 6/6 in Most Wanted and Carbon for the same car);
- the five cameras: ProStreet has speed-reaction records instead of camerainfo.
"""
import math
import struct
from .car_convert import (CAMERA_FIELDS, CAMERA_LINKS, RENDER_FIELDS, TIRE_FIELDS, WHEEL_FIELDS,
                          FitAttributes, hash32, require, wheel_fields)


SOURCE_CHASSIS = ('TRACK_WIDTH', 'WHEEL_BASE', 'FRONT_AXLE')
DONOR_CHASSIS = ('RIDE_HEIGHT', 'TRAVEL')


def keys(attrib, car):
    """(vehicle key, ecar key) of a car; the download cars are filed as dlcbp_<car>."""
    pvehicle = hash32('pvehicle')
    found = [k for k in (hash32(car.lower()), hash32('dlcbp_' + car.lower())) if (pvehicle, k) in attrib.collections]
    require(len(found) == 1, 'ProStreet pvehicle collection missing or ambiguous')
    ecar = attrib.linked_key('pvehicle', found[0], 'ecar')
    require(ecar == hash32(car.lower()), 'ProStreet render car differs from the folder name')
    return attrib.linked_key('pvehicle', found[0], 'vehicle'), ecar


def values(data, car):
    """Decoded source values, named as in the vault (floats; tuples for axle pairs)."""
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
    native = FitAttributes(donor_data)
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


def sidecars(attributes, donor_data, car, donor):
    decoded = values(attributes, car)
    return {'PHYSICS.BIN': physics(decoded, car, donor), 'WHEELFIT.BIN': wheel_fit(decoded, donor_data, car, donor)}
