"""Bounded Xenon conversion inputs, independent of the PC path-based writer.

The VPAK directory is LE; its BIN/VLT sections use BE tags, integers, shorts,
references and float components. Only the conversion dependency projection is
rebuilt as a canonical LE VPAK. Uninterpreted attribute data is never emitted.
Shared bChunks (materials, customization, paint and packed collision) are LE.
Array textures have 32-byte platform records and tiled pixels, unlike PC's
24-byte platforms and linear pixels. Indexed car textures use the same Xenon
pixel decoder. No source dependency can be supplied from another installation.

Plan: fingerprint selected logical ranges, decode and validate shared layouts,
resolve typed dependencies, and return values for the common writer. The raw
geometry is only a pack envelope; writers must use ``models`` rather than the
PC solid decoder. ``x360_opaque`` streams are deliberately removed here.
"""
from dataclasses import dataclass
import hashlib
import re
import struct

import numpy as np

from . import car_convert as cc, car_paint, car_physics, car_sources as cs, car_x360 as cx

require = cc.require
FormatError = cc.FormatError


class XenonVault:
    """Checked BE VLT directories and relocations; no live pointer patching."""
    def __init__(self, binary, vault):
        self.buffers = (vault, binary)
        self.chunks, self.fixups, self.exports = {}, {}, []
        pos = 0
        while pos < len(vault):
            tag, size = cc.words(vault, pos, '>4sI')
            if size == 0:
                require(not any(vault[pos:]), 'X360 VLT terminator')
                break
            require(size >= 8, 'X360 VLT chunk size')
            cc.span(vault, pos, size, 'X360 VLT chunk range')
            require(tag not in self.chunks, 'X360 duplicate VLT chunk')
            self.chunks[tag] = (pos, size)
            pos += size
        require(all(t in self.chunks for t in (b'Vers', b'DepN', b'PtrN', b'ExpN', b'DatN')),
                'X360 VLT required chunks')
        dep = self.chunk(b'DepN')
        count = self.uint(dep, 8)
        require(count == 2, 'X360 VLT dependency count')
        cc.span(dep, 12, count * 4, 'X360 VLT dependencies')
        ptr = self.chunk(b'PtrN')
        count, remainder = divmod(len(ptr) - 8, 12)
        require(remainder in (0, 8) and not any(ptr[8 + count * 12:]), 'X360 relocation stride')
        base = 0
        for pos in range(8, 8 + count * 12, 12):
            off, typ, idx, val = cc.words(ptr, pos, '>IHHI')
            if typ == 0:
                require(not any(ptr[pos:]), 'X360 relocation terminator')
                break
            if typ == 2:
                require(idx < 2, 'X360 relocation base')
                base = idx
            else:
                require(typ in (1, 3, 4, 5), 'X360 relocation type')
                cc.span(self.buffers[base], off, 4, 'X360 relocation site')
                require((base, off) not in self.fixups, 'X360 duplicate relocation')
                target = None
                if typ == 3:
                    require(idx < 2, 'X360 relocation target dependency')
                    cc.span(self.buffers[idx], val, 0, 'X360 relocation target')
                    target = idx, val
                self.fixups[base, off] = target
        exp = self.chunk(b'ExpN')
        count = self.uint(exp, 8)
        rows = cc.span(exp, 12, count * 16, 'X360 export table')
        require(not any(exp[12 + count * 16:]), 'X360 export padding')
        for key, typ, size, off in struct.iter_unpack('>4I', rows):
            cc.span(vault, off, size, 'X360 export range')
            require(size >= 12, 'X360 export size')
            self.exports.append((key, typ, off))

    @staticmethod
    def uint(data, off=0):
        return cc.words(data, off, '>I')[0]

    def chunk(self, tag):
        off, size = self.chunks[tag]
        return cc.span(self.buffers[0], off, size)

    def pointer(self, dep, off):
        cc.span(self.buffers[dep], off, 4, 'X360 pointer site')
        return self.fixups.get((dep, off))


class XenonAttributes:
    """Resolve inherited fields without assuming PC class/collection bytes."""
    def __init__(self, data):
        self.defs, self.collections = {}, {}
        for _, binary, raw in cc.read_vpak(data):
            v = XenonVault(binary, raw)
            for key, typ, off in v.exports:
                if typ == 0x5E970CBC:
                    count = v.uint(raw, off + 8) & 0xFFFF
                    target = v.pointer(0, off + 12)
                    require(target is not None and key not in self.defs, 'X360 class definitions')
                    dep, at = target
                    rows = list(struct.iter_unpack('>IIHHHBB', cc.span(v.buffers[dep], at, count * 16)))
                    require(len({r[0] for r in rows}) == count, 'X360 duplicate field definition')
                    self.defs[key] = {r[0]: r[1:] for r in rows}
                elif typ == 0x8E112EB7:
                    cc.span(raw, off, 32, 'X360 collection header')
                    identity = v.uint(raw, off + 4), v.uint(raw, off)
                    require(identity not in self.collections, 'X360 duplicate collection')
                    self.collections[identity] = v, off

    def raw(self, cls, key, name, seen=()):
        cls = cc.hash32(cls) if isinstance(cls, str) else cls
        field = cc.hash32(name) if isinstance(name, str) else name
        require(key not in seen and len(seen) < 32, 'X360 attribute parent cycle/depth')
        require(cls in self.defs and field in self.defs[cls] and (cls, key) in self.collections,
                'X360 attribute class, field or collection absent')
        v, off = self.collections[cls, key]
        raw = v.buffers[0]
        _, at, size, maximum, flags, align = self.defs[cls][field]
        require(size > 0 and maximum > 0 and align <= 16, 'X360 field definition shape')
        target = None
        if flags & 2:
            target = v.pointer(0, off + 28)
            if target:
                target = target[0], target[1] + at
        else:
            nt = cc.words(raw, off + 24, '>H')[0]
            start = off + ((32 + nt * 4 + 7) & ~7)
            cc.span(raw, off + 32, nt * 4, 'X360 collection types')
            entries = cc.span(raw, start, v.uint(raw, off + 20) * 12, 'X360 collection entries')
            matches = [i for i in range(len(entries) // 12) if v.uint(entries, i * 12) == field]
            require(len(matches) <= 1, 'X360 duplicate collection field')
            if matches:
                entry = start + matches[0] * 12
                target = (0, entry + 4) if raw[entry + 10] & 0x20 else v.pointer(0, entry + 4)
                require(target is not None, 'X360 attribute pointer unresolved')
        if target is None:
            parent = v.uint(raw, off + 8)
            require(parent != 0, 'X360 attribute field absent: ' + str(name))
            return self.raw(cls, parent, name, seen + (key,))
        dep, pos = target
        if flags & 1:
            alloc, count, esz, enc = cc.words(v.buffers[dep], pos, '>4H')
            require(0 < count <= alloc <= maximum and esz == size, 'X360 attribute array shape')
            pos += 8 + (8 if enc & 0x8000 else 0)
            size *= count
        require(size <= 4096, 'X360 projected field limit')
        return bytes(cc.span(v.buffers[dep], pos, size, 'X360 attribute value range'))


def _little(raw, unit=4):
    require(len(raw) % unit == 0, 'X360 typed field alignment')
    return b''.join(raw[i:i + unit][::-1] for i in range(0, len(raw), unit))


def _project_vpak(rows):
    """Build a value-only LE VPAK accepted by the checked public readers.

    Rows are (class hash, collection hash, [(field hash, LE bytes, array size)]).
    All inheritance and references have already been resolved in the source.
    """
    binary, definitions, collections = bytearray(), {}, []
    for cls, key, fields in rows:
        defs, base = [], len(binary)
        for name, value, esz in fields:
            require(value and (not esz or len(value) % esz == 0), 'projected attribute shape')
            at = len(binary)
            n = len(value) // esz if esz else 1
            require(at - base < 65536 and n < 65536, 'projected attribute layout limit')
            if esz:
                binary += struct.pack('<4H', n, n, esz, 0)
            binary += value
            defs.append((name, 0, at - base, esz or len(value), n, 3 if esz else 2, 4))
        require(cls not in definitions or definitions[cls] == defs, 'projected class schema differs')
        definitions[cls] = defs
        collections.append((cls, key, base))
    def vc(tag, raw):
        return tag[::-1] + struct.pack('<I', len(raw) + 8) + raw
    vers = vc(b'Vers', struct.pack('<II', 0, 0))
    dep = vc(b'DepN', struct.pack('<3I', 2, 0, 0))
    # Directories precede data; relocations always originate in the VLT.
    nexports = len(definitions) + len(collections)
    nfix = len(definitions) + len(collections)
    datstart = len(vers) + len(dep) + (8 + nfix * 12 + 8) + (12 + nexports * 16) + 8
    body, exports, fixups = bytearray(), [], []
    for cls, defs in definitions.items():
        at = len(binary)
        binary += b''.join(struct.pack('<IIHHHBB', *row) for row in defs)
        off = datstart + len(body)
        h = bytearray(24)
        struct.pack_into('<I', h, 8, len(defs))
        body += h
        exports.append((cls, 0x5E970CBC, 24, off))
        fixups.append((off + 12, 3, 1, at))
    for cls, key, base in collections:
        off = datstart + len(body)
        h = bytearray(32)
        struct.pack_into('<II', h, 0, key, cls)
        body += h
        fixups.append((off + 28, 3, 1, base))
        exports.append((key, 0x8E112EB7, 32, off))
    vault = vers + dep + vc(b'PtrN', b''.join(struct.pack('<IHHI', *r) for r in fixups) + bytes(8))
    vault += vc(b'ExpN', struct.pack('<I', len(exports)) + b''.join(struct.pack('<4I', *r) for r in exports))
    vault += vc(b'DatN', body)
    names = b'normalized\0'
    bo = 36 + len(names)
    return (b'VPAK' + struct.pack('<3I', 1, 36, len(names)) +
            struct.pack('<5I', 0, len(binary), len(vault), bo, bo + len(binary)) + names + binary + vault)


def normalize_attributes(data, car):
    """Resolve exactly the wheel, render, camera, paint and physics inputs."""
    src = XenonAttributes(data)
    key = cc.hash32(car.lower())
    rows = []
    def fields(cls, identity, specs):
        values = []
        for name, unit, esz in specs:
            raw = src.raw(cls, identity, name)
            hs = cc.hash32(name) if isinstance(name, str) else name
            values.append((hs, _little(raw, unit), esz))
        rows.append((cc.hash32(cls), identity, values))
        return values
    ecar = [(n, 1 if f == 'B' else 4, 16 if n == 'TireOffsets' else struct.calcsize(f))
            for n, f, _ in cc.WHEEL_FIELDS]
    ecar += [(n, 4, 0) for n in cc.RENDER_FIELDS] + [('WheelSpokeCount', 1, 0)]
    ecar += [(n, 4, 0) for n in cc.CAMERA_LINKS]
    stock = 0xAC97D3D1
    if stock in src.defs.get(cc.hash32('ecar'), {}):
        ecar.append((stock, 4, 0))
    values = fields('ecar', key, ecar)
    for link in cc.CAMERA_LINKS:
        raw = next(v for n, v, _ in values if n == cc.hash32(link))
        require(len(raw) == 12 and cc.u32(raw) == cc.hash32('camerainfo'), 'X360 camera reference shape')
        camera = cc.u32(raw, 4)
        if not any(c == cc.hash32('camerainfo') and k == camera for c, k, _ in rows):
            fields('camerainfo', camera, [(n, 4, 0) for n in cc.CAMERA_FIELDS])
    vehicle = fields('pvehicle', key, [(n, 4, 0) for n in ('chassis', 'tires', 'MASS', 'TENSOR_SCALE')])
    for cls, names in (('chassis', cc.CHASSIS_FIELDS), ('tires', cc.TIRE_FIELDS)):
        raw = next(v for n, v, _ in vehicle if n == cc.hash32(cls))
        require(len(raw) >= 12 and len(raw) % 12 == 0 and
                all(cc.u32(raw, i) == cc.hash32(cls) for i in range(0, len(raw), 12)),
                'X360 physics reference shape')
        fields(cls, cc.u32(raw, 4), [(n, 4, 0) for n in names])
    return _project_vpak(rows)


SHARED_FORMATS = dict(cx.FORMATS)
SHARED_FORMATS.update({0x18280186: ('A8R8G8B8', 4, 0x20), 0x28000102: ('L8', 1, 0x28)})
EXCLUDED_STREAMS = frozenset((0x134017, 0x134018, 0x134019))


def array_entries(data):
    """Checked Xenon array textures, with pack-relative 4 KiB pixel alignment.

    Metadata is LE including offsets. The pixel chunk ID 33320003 denotes
    platform data here, not another compression wrapper. Every record's full
    allocation is range-checked even when only its top mip will be decoded.
    """
    require(bool(data), 'X360 shared texture input empty')
    result = {}
    for cid, root, size in cc.walk(data):
        if cid != 0xB3300000:
            continue
        parts = list(cc.walk(data, root + 8, root + 8 + size))
        def one(kind):
            matches = [(o, z) for c, o, z in parts if c == kind]
            require(len(matches) == 1, 'X360 shared texture chunk count')
            return matches[0]
        info, iz = one(0x33310001)
        require(iz >= 4 and cc.u32(data, info + 8) == 8, 'X360 shared texture version')
        off, length = one(0x33310004)
        pp, pz = one(0x33310005)
        start, count = one(0x33320003)
        base = root + ((start + 8 - root + 4095) & ~4095)
        end = start + 8 + count
        require(base <= end and all(b == 0x11 for b in data[start + 8:base]),
                'X360 shared texture pixel alignment')
        q, limit, index, ranges = off + 8, off + 8 + length, 0, []
        while q < limit:
            require(limit - q >= 89, 'X360 shared texture record truncated')
            nl = data[q + 88]
            require(nl > 1, 'X360 shared texture name length')
            size = (89 + nl + 3) & ~3
            require(size <= limit - q, 'X360 shared texture name range')
            record = cc.span(data, q, size)
            require(record[89 + nl - 1] == 0, 'X360 shared texture name terminator')
            name = cc.text_at(record, 89, 89 + nl)
            hs = cc.u32(record, 12)
            # Retail labels are capped at 34 characters; the stored identity
            # hashes the untruncated name. Short labels must still hash back.
            require((len(name) == 34 or hs == cc.name_hash(name)) and hs not in result,
                    'X360 shared texture identity')
            require((index + 1) * 32 <= pz, 'X360 shared platform range')
            platform = cc.span(data, pp + 8 + index * 32, 32)
            offset, length_bytes = cc.u32(record, 20), cc.u32(record, 28)
            require(length_bytes > 0 and offset <= end - base and length_bytes <= end - base - offset,
                    'X360 shared texture allocation range')
            pixels = memoryview(data)[base + offset:base + offset + length_bytes]
            validate_shared_texture(record, platform, pixels)
            result[hs] = record, platform, pixels
            ranges.append((offset, offset + length_bytes))
            q += size
            index += 1
        require(index * 32 == pz, 'X360 shared platform count')
        ranges.sort()
        require(all(a[1] <= b[0] for a, b in zip(ranges, ranges[1:])), 'X360 shared texture overlap')
    return result


def validate_shared_texture(record, platform, pixels):
    require(len(record) >= 90 and len(platform) == 32, 'X360 shared texture tail')
    fmt = cc.u32(platform, 20)
    require(fmt in SHARED_FORMATS, f'X360 shared texture format {fmt:#010x} not decoded yet')
    w, h = cc.words(record, 40, '<HH')
    require(0 < w <= 4096 and 0 < h <= 4096 and record[44] <= 12 and record[45] <= 12 and
            w == 1 << record[44] and h == 1 << record[45], 'X360 shared texture dimensions')
    require(record[46] == SHARED_FORMATS[fmt][2] and
            1 <= record[50] <= max(record[44], record[45]) + 1, 'X360 shared texture format/mips')
    require(cc.u32(record, 28) == len(pixels) and 0 < cc.u32(record, 36) <= len(pixels),
            'X360 shared texture declared sizes')
    nl = record[88]
    require(1 < nl <= len(record) - 89 and record[89 + nl - 1] == 0, 'X360 shared texture name')
    name = cc.text_at(record, 89, 89 + nl)
    require(len(name) == 34 or cc.u32(record, 12) == cc.name_hash(name),
            'X360 shared texture hash/name')
    return fmt, w, h


def packed_top_origin(w, h, block):
    if min(w, h) > 16:
        return 0, 0
    return (0, 16 // block) if w > h else (16 // block, 0)


def shared_linear_top(record, platform, pixels):
    fmt, w, h = validate_shared_texture(record, platform, pixels)
    top = cc.u32(record, 36)
    if fmt == 0x18280086:
        return cx.linear_top(fmt, w, h, pixels[:top])
    name, bpp, _ = SHARED_FORMATS[fmt]
    block = 4 if name in ('DXT1', 'DXT3', 'DXT5', 'BC5') else 1
    wb, hb = (w + block - 1) // block, (h + block - 1) // block
    # A top level with a <=16-pixel short side already lives in the packed
    # tail, even when the record declares one mip. Its long-axis neighbour
    # starts 16 pixels along the short axis. Offsets are texture coordinates,
    # so BC formats must divide by four before calculating tiled addresses.
    ox, oy = packed_top_origin(w, h, block)
    aw, ah = (wb + 31) & ~31, hb + oy
    offsets = cx._tile_indices(aw, ah, bpp).reshape(ah, aw)[oy:oy + hb, ox:ox + wb].ravel()
    need = (int(offsets.max()) + 1) * bpp
    require(need <= top and top % 4096 == 0, 'X360 shared tiled top range')
    blocks = np.frombuffer(pixels[:top - top % bpp], np.uint8).reshape(-1, bpp)
    linear = blocks[offsets]
    if name == 'A8R8G8B8':
        linear = linear[:, ::-1]
    elif name != 'L8':
        linear = linear.reshape(-1, 2)[:, ::-1]
    return name, linear.tobytes()


def shared_texture_entry(record, platform, pixels):
    """Normalize variable-length global records to the common v8 tuple."""
    name, top = shared_linear_top(record, platform, pixels)
    w, h = cc.words(record, 40, '<HH')
    if name == 'L8':
        grey = np.frombuffer(top, np.uint8).reshape(h, w)
        rgba = np.stack((grey, grey, grey, np.full_like(grey, 255)), 2)
        top, name = cx._pixels().encode_dxt('DXT5', rgba), 'DXT5'
    elif name in ('BC5', 'A8R8G8B8'):
        top = cx._pixels().encode_dxt('DXT5', cx.decode_image(name, w, h, top))
        name = 'DXT5'
    r, pl = bytearray(record), bytearray(24)
    struct.pack_into('<III', r, 20, 0, 0xFFFFFFFF, len(top))
    struct.pack_into('<I', r, 36, len(top))
    r[46], r[50] = {'DXT1': 0x22, 'DXT3': 0x24, 'DXT5': 0x26}[name], 1
    pl[12:16] = name.encode('ascii')
    entry = bytes(r), bytes(pl), top
    cc.validate_texture(*entry, 8, cc.u32(r, 12))
    return entry


def shared_chunks(data):
    """Project compatible LE resources; exclude all platform texture chunks.

    Materials use the v4 236-byte layout. Customization is 104 bytes, paint
    tables use 4-byte parts/8-byte attributes/u16 lists, and collision retains
    checked 48-byte bounds and float4 clouds. These are independently tested
    against PC counterparts; material coefficient equality is not assumed.
    """
    keep = {0x135200, 0x30250, 0x34604, 0x34605, 0x34606, 0x3460B, 0x3460C}
    body, materials, seen, collision_rows = bytearray(), {}, set(), []
    collision_container = False
    for cid, off, size in cc.walk(data):
        if cid == 0x8003B900:
            require(not collision_container, 'X360 duplicate collision container')
            collision_container = True
        elif cid in keep:
            raw = cc.span(data, off + 8, size)
            if cid == 0x135200:
                require(size == 236 and cc.u32(raw, 8) == 3, 'X360 material row layout')
                name = cc.text_at(raw, 20, 52)
                hs = cc.u32(raw, 12)
                require(hs == cc.name_hash(name) and hs not in materials, 'X360 material identity')
                materials[hs] = bytes(raw)
            else:
                require(cid not in seen, 'X360 duplicate shared table')
                seen.add(cid)
                require(cid != 0x30250 or size % 104 == 0, 'X360 customization stride')
            body += cc.chunk(cid, raw)
        elif cid == 0x3B901:
            start = (off + 23) & ~15
            raw = cc.span(data, start, off + 8 + size - start)
            collision_rows.append(bytes(raw))
    if collision_rows or collision_container:
        collections = bytearray()
        for raw in collision_rows:
            collections += cc.aligned_chunk(0x3B901, len(body) + 8 + len(collections), raw, 16)
        body += cc.chunk(0x8003B900, collections)
    # Palette decoding validates indices, strings, list bounds and RGB fields.
    palette = car_paint.palette(body, carbon=True)
    cc.material_role_map(body)
    return bytes(body), materials, palette


@dataclass
class Bundle:
    """Normalized dependencies; output approval and MW policy belong to callers.

    ``geometry`` supplies the original pack envelope to the common writer.
    ``models``/``textures`` are D-normalized dictionaries. ``shared_textures``
    supplies requested global images, ``global_data`` only compatible shared
    chunks, ``attributes`` only typed conversion fields in canonical LE VPAK.
    ``runtime_bindings`` names generated paint/window/light/decal identities;
    ``texture_aliases`` records missing-glass-mask and dangling-normal fallbacks.
    ``runtime_materials`` identifies runtime paint/rim replacements. Source
    material coefficients are input evidence, never a ready-to-emit MW row.
    Sidecar values are source-derived; paint remains a selection until the MW
    palette is supplied by the caller. Call ``check_unchanged`` before output
    and include every ``inputs`` digest in output approval, including rows
    marked ``catalogued=False`` for same-source dependencies beyond recognition.
    """
    car: str
    geometry: bytes
    models: dict
    textures: dict
    shared_textures: dict
    runtime_bindings: dict
    texture_aliases: dict
    global_data: bytes
    materials: dict
    material_roles: dict
    runtime_materials: frozenset
    attributes: bytes
    wheels: dict
    render_fit: bytes
    wheel_fit: bytes
    paint: object
    palette: dict
    collision: object
    physics: bytes
    inputs: dict
    _reader: object

    def check_unchanged(self):
        self._reader.check_unchanged()


class LogicalReader:
    """Selected logical ranges through Source; never copy a numbered archive.

    Expected rows come from ``car_sources.recognise``. Their hashes are checked
    while reading and again on demand, including logical inventory changes.
    The caller keeps Source open until all checks finish.
    """
    def __init__(self, source, expected):
        self.source = source
        self.logical = cs._logical(source, cs._physical(source))
        self.expected, self.inputs = {}, {}
        for row in expected:
            identity = row['path_hash']
            require(identity not in self.expected and row.get('status', 'importable') == 'importable',
                    'X360 duplicate or unapproved dependency')
            require(0 <= row['size'] <= cc.MAX_FILE and re.fullmatch('[0-9a-f]{64}', row['sha256']),
                    'X360 dependency fingerprint shape')
            self.expected[identity] = row
        self.catalogued = frozenset(self.expected)

    def read(self, name):
        identity = cs.path_hash(name)
        require(identity in self.expected and identity in self.logical, 'X360 dependency absent from recognition')
        row, entry = self.expected[identity], self.logical[identity]
        require(entry[2] == row['size'], 'X360 dependency size changed')
        data = cs._read(self.source, entry)
        digest = hashlib.sha256(data).hexdigest()
        if digest != row['sha256']:
            raise cc.InputChangedError('X360 logical dependency changed: ' + name)
        self.inputs[identity] = dict(size=len(data), sha256=digest, catalogued=identity in self.catalogued)
        return data

    def snapshot_dependency(self, name):
        """Record an additional same-source dependency for output approval.

        The recognition catalog predates the full dependency closure. Extra
        selected ranges are snapshotted here and included in ``inputs``; their
        hashes must participate in the caller's output approval/cache identity.
        They never receive a claim of catalog fingerprint approval.
        """
        identity = cs.path_hash(name)
        require(identity in self.logical, 'X360 additional logical dependency absent')
        entry = self.logical[identity]
        require(0 < entry[2] <= cc.MAX_FILE, 'X360 additional dependency size')
        if identity not in self.expected:
            self.expected[identity] = dict(path_hash=identity, size=entry[2], sha256=cs._digest(self.source, entry))
        return self.read(name)

    def check_unchanged(self):
        current = cs._logical(self.source, cs._physical(self.source))
        for identity, row in self.inputs.items():
            if (current.get(identity) != self.logical[identity] or
                    cs._digest(self.source, current[identity]) != row['sha256']):
                raise cc.InputChangedError('X360 logical dependency changed')


def resolve(source, car, donor=None, *, recognition=None):
    """Resolve one selected car from an approved ISO/archive/logical source.

    The Source stays owned by the caller. An optional recognition result avoids
    re-inventorying other catalog cars; it never bypasses selected file hashes.
    No PC paths, donor assets, disc extraction or complete importer are used.
    """
    require(re.fullmatch('[A-Za-z0-9_]{1,15}', car) is not None, 'X360 car identity')
    car, donor = car.upper(), (donor or car).upper()
    require(re.fullmatch('[A-Z0-9_]{1,15}', donor) is not None, 'X360 donor identity')
    recognition = cs.recognise(source.path) if recognition is None else recognition
    require(recognition.get('platform') == 'x360' and recognition.get('game', 'carbon') == 'carbon',
            'X360 bundle needs recognized source platform')
    cars = [c for c in recognition['cars'] if c['car_hash'] == cs.path_hash('CARS/' + car)]
    require(len(cars) == 1 and cars[0]['status'] == 'importable', 'X360 selected car is not importable')
    read = LogicalReader(source, recognition['shared'] + cars[0]['files'])
    geometry = read.read(f'CARS/{car}/GEOMETRY.BIN')
    models = cx.source_models(geometry)
    for m in models.values():
        require(not any(cid in EXCLUDED_STREAMS for cid, _ in m['extras']), 'X360 opaque stream in output extras')
        m.pop('x360_opaque', None)
    textures = cx.source_textures(read.read(f'CARS/{car}/TEXTURES.BIN'))
    global_data = cc.unpack_file(read.read('GLOBAL/GLOBALB.LZC'))
    require(read.read('GLOBAL/GLOBALB.BUN') == global_data, 'X360 compressed/shared companion differs')
    attributes = normalize_attributes(read.read('GLOBAL/ATTRIBUTES.BIN'), car)
    shared_pack = read.read('CARS/TEXTURES.BIN')
    array = array_entries(global_data)
    shared_blobs = dict(cc.texture_blobs(shared_pack))
    wanted = {h for m in models.values() for h in m['textures']} - textures.keys()
    # The common writer's normal/resource policy also requests these images.
    wanted |= set(map(cc.name_hash, ('TIRE_STYLE01', 'TIRE_STYLE02', 'TIRE_STYLE01_N', 'DOORLINE_N')))
    # Runtime replacement tables bind these logical identities to paint,
    # damage, windows, lights and decals. They are not missing static pixels.
    names = {'DAMAGE0_N'} | {f'{car}_{r}' for r in cc.MW_PAINT_BINDINGS}
    names |= {f'WINDOW_{side}' for side in ('FRONT', 'REAR', 'LEFT_FRONT', 'RIGHT_FRONT', 'LEFT_REAR', 'RIGHT_REAR')}
    for kind in ('HEADLIGHT', 'HEADLIGHT_GLASS', 'BRAKELIGHT', 'BRAKELIGHT_GLASS'):
        names |= {f'{kind}_{s}' for s in ('LEFT', 'RIGHT', 'CENTRE')}
    names |= {f'DUMMY_DECAL{i}' for i in range(1, 7)} | {'DUMMY_NUMBER_LEFT', 'DUMMY_NUMBER_RIGHT'}
    runtime = {cc.name_hash(n): n for n in names if cc.name_hash(n) in wanted}
    aliases = {}
    for kit in range(6):
        name = f'{car}_KIT{kit:02d}_HEADLIGHT_GLASS_OFF'
        hs = cc.name_hash(name)
        if hs in wanted and hs not in shared_blobs and hs not in array:
            aliases[hs] = cc.name_hash('WINDOW_MASK'), 'WINDOW_MASK'
            wanted.add(cc.name_hash('WINDOW_MASK'))
    static = wanted - runtime.keys() - aliases.keys()
    absent = static - array.keys() - shared_blobs.keys()
    extra = {}
    if absent:
        # Driver/plate art lives in INGAMEA rather than GLOBALB on this platform.
        extra = array_entries(cc.unpack_file(read.snapshot_dependency('GLOBAL/INGAMEA.BUN')))
        for hs in absent & extra.keys():
            array[hs] = extra[hs]
    shared = {}
    for hs in sorted(static):
        if hs in shared_blobs:
            shared[hs] = cx.texture_entry(hs, shared_blobs[hs])
        elif hs in array:
            shared[hs] = shared_texture_entry(*array[hs])
    unresolved = static - shared.keys()
    # This dangling retail normal uses the source engine's zero-alpha fallback.
    grill, fallback = map(cc.name_hash, ('GRILL_05_N', 'DEFAULTALPHA'))
    if grill in unresolved:
        roles = {slot for m in models.values() for record in m['records']
                 for slot in range(24, 29) if m['textures'][record[slot]] == grill}
        if roles == {25}:
            if fallback in shared_blobs:
                shared[fallback] = cx.texture_entry(fallback, shared_blobs[fallback])
            elif fallback in array:
                shared[fallback] = shared_texture_entry(*array[fallback])
            elif fallback in extra:
                shared[fallback] = shared_texture_entry(*extra[fallback])
            if fallback in textures or fallback in shared:
                aliases[grill] = fallback, 'DEFAULTALPHA'
                unresolved.remove(grill)
    require(not unresolved, 'X360 unresolved source texture identities: ' +
            ','.join(f'{h:08x}' for h in sorted(unresolved)))
    normalized, materials, palette = shared_chunks(global_data)
    used = {h for m in models.values() for h in m['lights']}
    # The public writer already admits these runtime paint/rim replacements.
    runtime_materials = frozenset(used & {0xD6D6080A, 0x22719FA9})
    require(used <= materials.keys() | runtime_materials, 'X360 unresolved source material identities')
    wheels = cc.wheel_fields(attributes, car)
    fit = cc.render_fit(attributes, car)
    wheel_fit = cc.wheel_sidecar(attributes, car, donor, fit=True)
    paint = car_paint.stock_customization(normalized, attributes, car)
    collision = cc.collision_sidecar(normalized, car, donor)
    physics = car_physics.metadata(attributes, car, donor)
    require(physics is not None, 'X360 physics dependencies missing')
    read.check_unchanged()
    return Bundle(car, geometry, models, textures, shared, runtime, aliases, normalized, materials,
                  cc.material_role_map(normalized), runtime_materials, attributes, wheels, fit, wheel_fit,
                  paint, palette, collision, physics, dict(read.inputs), read)
