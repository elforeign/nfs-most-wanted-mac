"""Undercover pack decoding and its stock material and socket mappings."""
import struct

import numpy as np

from . import car_convert as mw
from .car_convert import name_hash, require, u32

MAX_ENTRY = 128 << 20


def refpack(d):
    """Bounded EA RefPack, with checked RFPK and 0x10FB size envelopes."""
    require(len(d) >= 21 and d[:8] == b'RFPK\x01\x10\0\0', 'RFPK envelope/version')
    size, stored = u32(d, 8), u32(d, 12)
    require(0 < size <= MAX_ENTRY and 0 <= len(d) - 16 - stored <= 3 and
            not any(d[16 + stored:]), 'RFPK LE sizes/padding')
    d = d[:16 + stored]
    require(d[16:18] == b'\x10\xfb' and int.from_bytes(d[18:21], 'big') == size, 'RefPack BE size/signature')
    out = bytearray(); p = 21; ended = False
    while p < len(d):
        a = d[p]; p += 1
        if a >= 0xfc:
            literal, count, distance = a & 3, 0, 0; ended = True
        elif a >= 0xe0:
            literal, count, distance = ((a & 31) + 1) * 4, 0, 0
        else:
            n = 1 if a < 0x80 else 2 if a < 0xc0 else 3
            require(p + n <= len(d), 'RefPack command truncated')
            b = d[p]; c = d[p + 1] if n >= 2 else 0; e = d[p + 2] if n == 3 else 0; p += n
            if n == 1:
                literal, count, distance = a & 3, ((a >> 2) & 7) + 3, ((a & 0x60) << 3) + b + 1
            elif n == 2:
                literal, count, distance = b >> 6, (a & 0x3f) + 4, ((b & 0x3f) << 8) + c + 1
            else:
                literal, count, distance = a & 3, ((a & 0x0c) << 6) + e + 5, ((a & 0x10) << 12) + (b << 8) + c + 1
        require(p + literal <= len(d) and len(out) + literal + count <= size, 'RefPack literal/output range')
        out += d[p:p + literal]; p += literal
        if count:
            require(0 < distance <= len(out), 'RefPack backreference range')
            for _ in range(count): out.append(out[-distance])
        if ended: break
    require(ended and p == len(d) and len(out) == size, 'RefPack terminator/decoded size')
    return bytes(out)


def _unpack_entry(d, e):
    _, off, stored, size, flags, zero = e
    require(0 < size <= MAX_ENTRY and zero == 0 and off <= len(d) and stored <= len(d) - off, 'UC index LE range/size')
    raw = d[off:off + stored]
    if flags == 0:
        require(len(raw) == size, 'UC raw size'); return raw
    require(flags == 0x200, 'UC index flags')
    out = bytearray(size); spans = []; p = 0; first = None
    while p < len(raw):
        require(p + 8 <= len(raw), 'UC streaming header')
        magic, n = struct.unpack_from('<II', raw, p)
        if magic == 0:
            require(n <= len(raw) - p - 8 and not any(raw[p + 8:p + 8 + n]), 'UC streaming gap')
            p += 8 + n; continue
        require(p + 24 <= len(raw) and magic == 0x55441122, 'UC streaming magic/header')
        _, n, bs, dst, rel, zero = struct.unpack_from('<6I', raw, p)
        if first is None: first = p
        require(rel == p - first and zero == 0, 'UC streaming LE relative offset')
        require(24 <= bs <= len(raw) - p and 0 < n <= size and dst <= size - n, 'UC streaming LE block range')
        packed = raw[p + 24:p + bs]
        if packed[:4] == b'RFPK': part = refpack(packed)
        elif packed[:4] == b'RAWW':
            require(len(packed) >= 16 and packed[4:8] == b'\x01\x10\0\0' and u32(packed, 8) == u32(packed, 12) == n, 'UC RAWW envelope')
            part = packed[16:]
        else: raise mw.FormatError('UC unsupported stream codec')
        require(len(part) == n, 'UC streaming decoded size')
        spans.append((dst, n)); out[dst:dst + n] = part; p += bs
    cursor = 0
    for dst, n in sorted(spans):
        require(dst == cursor, 'UC streaming coverage/overlap'); cursor += n
    require(cursor == size, 'UC streaming coverage/size')
    return bytes(out)


def pack_entries(d, cid):
    entries = mw.index(d, cid); hs = [e[0] for e in entries]
    require(len(entries) <= 4096 and sum(e[3] for e in entries) <= MAX_ENTRY, 'UC aggregate decode limit')
    require(hs == sorted(set(hs)), 'UC sorted unique index hashes')
    spans = sorted((e[1], e[1] + e[2]) for e in entries)
    require(all(a[1] <= b[0] for a, b in zip(spans, spans[1:])), 'UC stored entries overlap')
    return [(e[0], _unpack_entry(d, e)) for e in entries]


# Decoded UC shader-role names -> matching static MW material names. These
# aliases supply effect-4 fields, not UC shading/tangents/normal-map equivalence.
LIGHT_ALIASES = {0xCC772AFB: 0xDCD01662,   # DOORLINE -> MW DOORLINE
                 0x721AFDDC: 0xB57AB98E,   # CARBONFIBER -> MW CARBONFIBRE_BODY
                 0x128FB83E: 0x54949AFD,   # EXHAUST_CHROME -> MW BODY_CHROME
                 0x6332A7A7: 0x54949AFD,   # HEADLIGHTCHROME -> MW BODY_CHROME
                 0x6B637059: 0xFD102A92,   # MAGLIP -> MW RIM_KIT00_CHROME
                 0xD22FF4B6: 0xD6D6080A}   # REGPAINTBLACK -> MW BODY (repaintable)


LIGHT_FALLBACK = {0x579D51E8: name_hash('BOTTOM'), 0x6C39F5ED: 0x3ED70C43}


def translated_source(src, translation):
    src = dict(src); src['draws'] = [dict(d, vertices=d['vertices'].copy()) for d in src['draws']]
    for d in src['draws']: d['vertices'].view('<f4').reshape(-1, 9)[:, :3] += translation
    rows = []
    for raw in src['markers']:
        r = bytearray(raw); p = np.frombuffer(raw[64:76], '<f4') + translation
        r[64:76] = p.astype('<f4').tobytes(); rows.append(bytes(r))
    src['markers'] = rows
    return src
