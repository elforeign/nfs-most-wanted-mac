"""Bounded lighting-row policy for locally decoded car materials.

Contract: pass payloads (236-byte source / 168-byte MW), keyed by name hash,
and source identities actually used by the geometry BEFORE role replacement.
Import inside the conversion entry, after the converter's parser primitives.
``policy`` returns remap[source_hash] = target_hash and rows[target_hash] =
standalone MW payload; append rows with append_material_rows and validate both
the emitted identities and the streaming-resource range. No file I/O occurs.

Default amplitudes=False retains role-only coefficients. Lamps are a separate
MW-derived policy: clone the matching MW row into its explicit lamp identity,
without importing source lamp constants. lamps=False (default) retains role-only bytes; lamps=True opts in.
Amplitude translation needs explicit capture gains, has separate per-car names,
and preserves signed endpoints/HDR. BODY and replaceable rim bindings must be
supplied in dynamic; they are never claimed as translated. Unsupported shader
controls are reported, not reinterpreted as MW MetallicScale or HotSpot.
"""
from dataclasses import dataclass
import hashlib
import math
import re
import struct

from .car_convert import name_hash, require

MAX_ROWS = 4096
LAMP_NAMES = {'BRAKELIGHTGLASS': 'CARBON_LAMP_BRAKE', 'HEADLIGHTGLASS': 'CARBON_LAMP_HEAD'}


@dataclass(frozen=True)
class Gains:
    source_specular: float
    mw_specular: float
    mw_environment: float
    specular_power: float = 1.0
    environment_power: float = 1.0

    def validate(self):
        require(math.isfinite(self.source_specular) and self.source_specular >= 0,
                'invalid source specular gain')
        for value in (self.mw_specular, self.mw_environment,
                      self.specular_power, self.environment_power):
            require(math.isfinite(value) and value > 0, 'invalid MW lighting gain')


@dataclass
class MaterialPolicy:
    remap: dict
    rows: dict
    unsupported: dict
    dynamic: tuple


def _row(raw, size, key=None):
    require(isinstance(raw, bytes) and len(raw) == size, 'lighting row size')
    require(struct.unpack_from('<I', raw, 8)[0] == 3, 'lighting row version')
    limit = 52 if size == 236 else 48
    text = raw[20:limit]
    require(b'\0' in text, 'unterminated lighting name')
    try:
        name = text.split(b'\0', 1)[0].decode('ascii')
    except UnicodeDecodeError as e:
        raise ValueError('invalid lighting name') from e
    identity = struct.unpack_from('<I', raw, 12)[0]
    require(name and identity == name_hash(name), 'lighting name/hash mismatch')
    require(key is None or identity == key, 'lighting mapping key mismatch')
    start = 0x54 if size == 236 else 0x30
    require(all(math.isfinite(v) for v in struct.unpack_from(
        '<' + str((size - start) // 4) + 'f', raw, start)), 'nonfinite lighting row')
    return name


def _identity(row, name):
    row = bytearray(row)
    row[:8] = bytes(8)  # loader-owned links, never pointers from the input
    struct.pack_into('<I', row, 12, name_hash(name))
    row[20:48] = name.encode('ascii').ljust(28, b'\0')
    return row


def limitations(source):
    """Raw source controls whose angular/flake/vinyl responses MW cannot carry."""
    _row(source, 236)
    f = lambda off: struct.unpack_from('<f', source, off)[0]
    result = {'diffuse_power_clamp': (f(0x54), f(0x58)),
              'environment_clamp': f(0xBC)}
    if f(0x5C) != 0 or f(0x90) != 0:
        result['flakes'] = (f(0x5C), f(0x90))
    if any(f(o) != 0 for o in (0x60, 0x94, 0xC0, 0xE4, 0xE8)):
        result['vinyl_luminance'] = tuple(f(o) for o in (0x60, 0x94, 0xC0, 0xE4, 0xE8))
    rgb = [struct.unpack_from('<3f', source, o) for o in (0xC8, 0xD8)]
    if any(len(set(v)) > 1 for v in rgb):
        result['environment_rgb_scalar_path'] = 'MW ordinary car vertex shader uses red only'
    return result


def translate_row(source, donor, name, gains):
    """Translate endpoint amplitudes only; no promise of shader/visual parity."""
    _row(source, 236)
    _row(donor, 168)
    require(re.fullmatch(r'[A-Z0-9_]{1,27}', name) is not None, 'invalid output lighting name')
    require(isinstance(gains, Gains), 'explicit lighting gains required')
    gains.validate()
    row = _identity(donor, name)
    row[0x30:0x50] = source[0x64:0x84]
    low, high = struct.unpack_from('<2f', source, 0x84)
    try:
        struct.pack_into('<2f', row, 0x50, low, low + 4 * (high - low))
        for src, dst, scale in ((0x98, 0x5C, gains.source_specular / gains.mw_specular),
                                (0xA8, 0x6C, gains.source_specular / gains.mw_specular),
                                (0xC4, 0x80, 0.5 / gains.mw_environment),
                                (0xD4, 0x90, 0.5 / gains.mw_environment)):
            values = struct.unpack_from('<4f', source, src)
            struct.pack_into('<4f', row, dst, values[0] * scale, *values[1:])
        for src, dst, gain in ((0x8C, 0x58, gains.specular_power),
                               (0xB8, 0x7C, gains.environment_power)):
            power = struct.unpack_from('<f', source, src)[0]
            require(power >= 0, 'negative lighting power')
            struct.pack_into('<f', row, dst, power / gain)
    except (OverflowError, struct.error) as e:
        raise ValueError('lighting translation exceeds float32') from e
    struct.pack_into('<2f', row, 0xA0, 0, 0)  # no justified flake/hotspot mapping
    result = bytes(row)
    _row(result, 168)
    return result


def policy(source_rows, mw_rows, used, *, namespace, roles=None, dynamic=(),
           amplitudes=False, lamps=False, gains=None, occupied=()):
    """Return a deterministic, collision-checked policy; never mutate inputs.

    roles maps source hashes to MW donor hashes. dynamic must include draw-time
    BODY/rim material identities, even when their source name is arbitrary.
    BODY and MAGSILVER are additionally excluded by name. Separate source rows
    remain separate even if role mapping converges on one MW donor.
    occupied is the set of other output-resource hashes; collisions fail closed.
    With both options off, no rows are emitted and the original role map holds.
    """
    require(len(source_rows) <= MAX_ROWS and len(mw_rows) <= MAX_ROWS, 'too many lighting rows')
    require(isinstance(namespace, str) and re.fullmatch(r'[A-Za-z0-9_]{1,64}', namespace),
            'invalid lighting namespace')
    require(isinstance(amplitudes, bool) and isinstance(lamps, bool), 'invalid lighting policy option')
    # Bound before materializing arbitrary caller iterators.
    def bounded(values):
        out = set()
        for i, key in enumerate(values):
            require(i < MAX_ROWS and type(key) is int and 0 <= key <= 0xFFFFFFFF,
                    'invalid lighting identity list')
            out.add(key)
        return out
    used, dynamic, occupied = bounded(used), bounded(dynamic), bounded(occupied)
    roles = dict(roles or {})
    require(len(roles) <= MAX_ROWS, 'too many lighting roles')
    for key, target in roles.items():
        require(type(key) is int and type(target) is int and
                0 <= key <= 0xFFFFFFFF and 0 <= target <= 0xFFFFFFFF, 'invalid lighting role')
    source_names = {key: _row(raw, 236, key) for key, raw in source_rows.items()}
    for key, raw in mw_rows.items():
        _row(raw, 168, key)
    if amplitudes:
        require(isinstance(gains, Gains), 'explicit lighting gains required')
        gains.validate()
    rows, remap, unsupported, skipped = {}, {}, {}, []
    reserved = set(source_rows) | set(mw_rows) | dynamic | occupied
    for key in sorted(used):
        target = roles.get(key, key)
        remap[key] = target
        name = source_names.get(key)
        if key in dynamic or target in dynamic or name in ('BODY', 'MAGSILVER'):
            skipped.append(key)
            continue
        if not (amplitudes or (lamps and name in LAMP_NAMES)):
            continue
        require(name is not None, 'used source lighting row absent')
        require(target in mw_rows, 'MW lighting donor absent')
        if lamps and name in LAMP_NAMES:
            # Shared compatibility identities deliberately use MW lamp rows,
            # never optional amplitude rows or source shader parameters.
            require(target == key, 'MW lamp donor role mismatch')
            output_name = LAMP_NAMES[name]
            row = bytes(_identity(mw_rows[target], output_name))
        else:
            digest = hashlib.sha256((namespace.upper() + ':' + name).encode('ascii')).hexdigest()[:20].upper()
            output_name = 'IMP_' + digest
            row = translate_row(source_rows[key], mw_rows[target], output_name, gains)
            unsupported[key] = limitations(source_rows[key])
        identity = name_hash(output_name)
        require(identity not in reserved and identity not in rows, 'lighting namespace collision')
        rows[identity], remap[key] = row, identity
    return MaterialPolicy(remap, rows, unsupported, tuple(skipped))
