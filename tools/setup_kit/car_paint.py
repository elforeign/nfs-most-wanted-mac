"""Resolve stock customization HSV against MW paint parts of the same finish.

DPT1 stores identities and a validated selection, never source material data.
Stock hue, saturation and brightness determine the RGB selection.
Missing/unknown selections explicitly retain the donor.
"""
import colorsys
import math
import re
import struct
import sys

from .car_convert import FitAttributes, FormatError, hash32, name_hash, require, span, text_at, u32, walk, words

MAGIC = 0x31545044


def palette(data, *, carbon=False):
    """Decode base paint only, excluding wheel, vinyl and group entries.

    MW parts are 14 bytes (hash, slot, type, name/attribute-list indices).
    Source parts are four bytes (slot, type, attribute-list index); selector
    attributes generate the identity. Both use dword string indices and ushort attribute lists.
    """
    chunks = {}
    for cid, off, size in walk(data):
        if cid in (0x34604, 0x34605, 0x34606, 0x3460B, 0x3460C):
            require(cid not in chunks, 'duplicate paint-part table')
            chunks[cid] = span(data, off + 8, size)
    require(len(chunks) == 5, 'missing paint-part table')
    parts, attrs, strings, types, lists = (chunks[k] for k in
                                         (0x34604, 0x34605, 0x34606, 0x3460B, 0x3460C))
    require(len(attrs) % 8 == 0 and len(types) % 4 == 0 and len(lists) % 2 == 0,
            'paint-part table stride')
    stride = 4 if carbon else 14
    # Retail MW ends its part table with two alignment bytes.
    remainder = len(parts) % stride
    require(remainder in ((0,) if carbon else (0, 2)), 'paint-part record stride')
    require(not remainder or not any(parts[-remainder:]), 'paint-part record padding')
    out = {}
    brands = {name_hash(n): n for n in
              ('GLOSS', 'METALLIC', 'PEARL', 'MATTE', 'CHROME', 'IRID', 'CANDY', 'TRAFFIC', 'COP', 'SWATCH')}
    for p in range(0, len(parts) - remainder, stride):
        slot, typ = parts[p:p + 2] if carbon else (parts[p + 4], parts[p + 7])
        if u32(types, typ * 4) != name_hash('PAINT') or (not carbon and slot != 0x4C):
            continue
        ai = words(parts, p + (2 if carbon else 10), '<H')[0]
        if ai == 0xFFFF:
            continue
        count = words(lists, ai * 2, '<H')[0]
        indices = words(lists, ai * 2 + 2, '<' + str(count) + 'H')
        pairs = [words(attrs, i * 8, '<II') for i in indices]
        require(len({k for k, _ in pairs}) == len(pairs), 'duplicate paint attribute')
        values = dict(pairs)
        if carbon:
            # Generated identities follow the selector/base and the two packed
            # string offsets. NAME_OFFSET is a display label, not this identity.
            selector = values.get(name_hash('PART_NAME_SELECTOR'), 0xFFFFFFFF)
            base = (values.get(name_hash('BRAND_NAME')) if selector == 1 else
                    values.get(name_hash('PART_NAME_BASE_HASH')) if selector == 2 else
                    u32(types, typ * 4) if selector == 0 else None)
            if base not in brands:
                continue
            name = brands[base]
            offsets = values.get(name_hash('PART_NAME_OFFSETS'), 0xFFFFFFFF)
            for index in (offsets & 0xFFFF, offsets >> 16):
                if index != 0xFFFF:
                    name += '_' + text_at(strings, index * 4)
            if not re.fullmatch(r'(?:[A-Z]+_L1|SWATCH)_COLOR[0-9]+', name):
                continue
        else:
            name = text_at(strings, words(parts, p + 8, '<H')[0] * 4)
        key = name_hash(name)
        require(carbon or key == u32(parts, p), 'paint-part name/hash mismatch')
        rgb = tuple(values.get(name_hash(k), -1) for k in ('RED', 'GREEN', 'BLUE'))
        require(all(0 <= x <= 255 for x in rgb), 'paint-part RGB range')
        require(key not in out, 'duplicate base-paint key')
        out[key] = dict(name=name, rgb=rgb)
    return out


def stock_customization(source, attributes, car):
    """Resolve the inherited ecar stock-customization link, then its paint HSV."""
    if not attributes:
        return None
    attrib = FitAttributes(attributes)
    # ecar layout +0x118: the hashed scalar stock-customization definition.
    cls, key, field = hash32('ecar'), hash32(car.lower()), 0xAC97D3D1
    if (cls, key) not in attrib.collections or field not in attrib.defs.get(cls, {}):
        return None
    try:
        selection = u32(attrib.raw(cls, key, field))
    except FormatError:
        return None
    if selection in (0, 0xFFFFFFFF):
        return None
    chunks = [(o, z) for cid, o, z in walk(source) if cid == 0x30250]
    require(len(chunks) <= 1, 'duplicate stock customization table')
    if not chunks:
        return None
    off, size = chunks[0]
    require(size % 104 == 0, 'stock customization stride')
    rows = [p for p in range(off + 8, off + 8 + size, 104)
            if name_hash(text_at(source, p + 8, p + 40)) == selection]
    require(len(rows) <= 1, 'duplicate stock customization identity')
    if not rows:
        return None
    finish_key, swatch_key, saturation, brightness = words(source, rows[0] + 40, '<IIff')
    finishes = {name_hash(n): n for n in ('GLOSS', 'METALLIC', 'PEARL', 'MATTE', 'CHROME', 'IRID', 'CANDY')}
    try:
        swatch = palette(source, carbon=True).get(swatch_key)
    except FormatError as exc:
        print(f'{car}: invalid source paint palette ({exc}); donor retained', file=sys.stderr)
        return None
    if finish_key not in finishes or not swatch or not swatch['name'].startswith('SWATCH_'):
        return None
    if not all(math.isfinite(x) and 0 <= x <= 1 for x in (saturation, brightness)):
        return None
    hue = colorsys.rgb_to_hsv(*(x / 255 for x in swatch['rgb']))[0]
    rgb = tuple(x * 255 for x in colorsys.hsv_to_rgb(hue, saturation, brightness))
    return dict(customization=f'{selection:08X}', finish=finishes[finish_key],
                source_key=swatch_key, source_swatch=swatch['name'],
                saturation=saturation, brightness=brightness, rgb=rgb)


def resolve(stock, target):
    if not stock:
        return 0, 0, 'missing or invalid stock customization; donor retained'
    finish = {'METALLIC': 'METAL'}.get(stock['finish'], stock['finish'])
    candidates = {k: v for k, v in target.items() if v['name'].startswith(finish + '_L1_')}
    if not candidates:
        return 0, 0, 'no MW swatch of the source finish; donor retained'
    distance = lambda k: sum((a - b) ** 2 for a, b in zip(stock['rgb'], candidates[k]['rgb']))
    mapped = min(candidates, key=lambda k: (distance(k), k))
    squared = distance(mapped)
    reason = ('exact finish/RGB' if squared == 0 else 'nearest RGB within finish')
    return mapped, 2, f'{reason}; distance={math.sqrt(squared):.6f}; squared distance={squared:.6f}'


def metadata(source, read_target, car, donor, attributes=None):
    """Missing stock data retains the donor without reading the target palette."""
    stock = stock_customization(source, attributes, car)
    target = palette(read_target()) if stock else {}
    mapped, method, reason = resolve(stock, target)
    key = stock['source_key'] if stock else 0
    header = (MAGIC, 2, name_hash(car), name_hash(donor), key, mapped, method)
    checksum = 0xFFFFFFFF
    for word in header:
        checksum = ((checksum ^ word) * 16777619) & 0xFFFFFFFF
    blob = struct.pack('<8I', *header, checksum)
    report = dict(source_key=f'{key:08X}', mw_key=f'{mapped:08X}',
                  swatch=target[mapped]['name'] if mapped else None, reason=reason)
    if stock:
        report.update({k: v for k, v in stock.items() if k != 'source_key'})
        report['distance'] = math.sqrt(sum((a-b)**2 for a,b in zip(stock['rgb'], target[mapped]['rgb']))) if mapped else None
    return blob, report
