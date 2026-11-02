"""Build MW BNKl/PT PCM sample artifacts, without executable ABK modules.

Plan: retain source bank digest plus S10A index as identity, validate selector
rows against those indices, then lay out little endian selectors and aligned
BNKl/PT extents. No donor templates, resource renaming or playback defaults
are supplied. Module ABI, player state and complete ABKC assembly are separate.

Source PC rows are {flags<<16 | index, index+1, -1}, or {65535,0,0}.
Observed flags 60/80/100 are retained as provenance, not interpreted as MW
player flags. MW rows are {0xff00, slot, 0}; null rows select null slot zero.
Only resident mono codec-4 inputs are admitted: stereo PT layout is unverified.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import struct

from .car_audio_codec import DecodedSound, decode_bank, parse_bank

MAX_BYTES = 256 * 1024 * 1024


def _require(ok, message):
    if not ok:
        raise ValueError(message)


def _u32(data, at, end=None):
    end = len(data) if end is None else end
    _require(0 <= at <= end <= len(data) and at + 4 <= end, 'selector field bounds')
    return struct.unpack_from('<I', data, at)[0]


def _align(n, alignment):
    return (n + alignment - 1) & -alignment


@dataclass(frozen=True)
class Selector:
    source_offset: int
    pointer_sites: tuple[int, ...]
    # None is an authored empty row; order and duplicates are significant.
    indices: tuple[int | None, ...]
    source_rows: tuple[tuple[int, int, int], ...]


@dataclass(frozen=True)
class PlacedSelector:
    source: Selector
    offset: int
    data: bytes


@dataclass(frozen=True)
class SampleBank:
    """A placement fragment, never a complete executable ABK.

    Offsets are absolute for insertion at `offset`. bank_extent includes the
    128-byte padding; bank_used excludes it. pointer_sites are source sites for
    a later module adapter, and are deliberately NOT patched by this builder.
    identities[MW slot] is (source SHA-256, source index), or None for nulls.
    """
    source_sha256: str
    offset: int
    data: bytes
    bank_offset: int
    bank_extent: int
    bank_used: int
    pcm_bytes: int
    selectors: tuple[PlacedSelector, ...]
    identities: tuple[tuple[str, int] | None, ...]


def source_selectors(data, sounds, *, table_offsets=None):
    """Read bounded PC selectors reachable from ABKC pointer-fixup sites.

    Automatic discovery recognizes the audited source row signatures; it does
    not certify an opaque module's ABI or identify zero-count arrays by guessing.
    A module adapter can pass explicit table_offsets (including zero-count
    tables); those must be reachable pointer targets. Unknown module payloads
    remain outside this sample-only contract.
    """
    bank = _u32(data, 0x20)
    f1, f2, f3 = (_u32(data, at) for at in (0x30, 0x34, 0x38))
    if not (f1 or f2 or f3):
        _require(table_offsets is None or not tuple(table_offsets), 'selector table without fixups')
        return ()
    _require(bank + _u32(data, 0x24) == f1 and f1 <= f2 < f3 <= len(data) - 4,
             'selector fixup extents')
    count = _u32(data, f2, f3)
    _require(count <= 65536 and f2 + 4 + 4 * count <= f3, 'selector fixup count')
    targets = {}
    for at in range(f2 + 4, f2 + 4 + count * 4, 4):
        site = _u32(data, at, f3)
        _require(0x60 <= site <= bank - 4, 'selector pointer site outside modules')
        target = _u32(data, site, bank)
        _require(0x60 <= target <= bank - 4, 'selector pointer target outside modules')
        targets.setdefault(target, []).append(site)
    if table_offsets is None:
        selected = []
        for target in targets:
            n = _u32(data, target, bank)
            if n == 0 or target + 16 > bank:
                continue
            word = _u32(data, target + 4, bank)
            # Classify by the first row's signature, then validate EVERY row.
            if word == 65535 or word >> 16 in (60, 80, 100):
                selected.append(target)
    else:
        selected = tuple(table_offsets)
        _require(len(set(selected)) == len(selected), 'duplicate selector table offset')
        _require(all(t in targets for t in selected), 'selector table is not a pointer target')
    result = []
    for target in sorted(selected):
        n = _u32(data, target, bank)
        _require(n <= 256 and target + 4 + n * 12 <= bank, 'selector table extent/count')
        rows, indices = [], []
        for row in range(n):
            words = struct.unpack_from('<3I', data, target + 4 + row * 12)
            word, slot, reserved = words
            rows.append(words)
            if words == (65535, 0, 0):
                indices.append(None)
                continue
            index = word & 65535
            _require(word >> 16 in (60, 80, 100) and reserved == 0xffffffff,
                     'unsupported source selector row')
            _require(index < len(sounds) and slot == index + 1 and sounds[index] is not None,
                     'source selector sample identity')
            indices.append(index)
        result.append(Selector(target, tuple(targets[target]), tuple(indices), tuple(rows)))
    for left, right in zip(result, result[1:]):
        _require(left.source_offset + 4 + 12 * len(left.indices) <= right.source_offset,
                 'overlapping selector tables')
    return tuple(result)


def _tag(tag, value):
    _require(-(1 << 31) <= value < (1 << 31), 'PT signed value range')
    # Fixed signed big-endian words avoid sign-extension ambiguity at 0x80.
    return bytes((tag, 4)) + value.to_bytes(4, 'big', signed=True)


def _pt(sound, data_offset):
    return (b'PT\0\0' + b'\xfd' + _tag(0x80, 2) + _tag(0x82, 1)
            + _tag(0x84, sound.rate) + _tag(0x85, sound.frames)
            + _tag(0x86, -1 if sound.loop_start is None else sound.loop_start)
            + _tag(0x87, -1 if sound.loop_end is None else sound.loop_end)
            + _tag(0xa0, 8) + _tag(0x8a, 0) + _tag(0x88, data_offset) + b'\xff')


def build_sample_bank(source, decoded=None, *, offset=0x60, table_offsets=None,
                      max_bytes=MAX_BYTES):
    """Lay out selectors and BNKl from exact decoded source samples.

    `decoded`, if supplied, must carry exactly the parsed source Sound metadata
    and declared PCM byte counts, including null positions. It is the codec
    layer's trusted PCM result; this builder does not re-decode supplied PCM.
    The allocation bound is checked before decoding and before output allocation.
    Source SHA-256 namespaces the slot identities so paired resources cannot be
    confused even when their samples or filenames happen to coincide.
    """
    _require(type(offset) is int and 0 <= offset < (1 << 31), 'placement offset range')
    _require(type(max_bytes) is int and 0 < max_bytes <= MAX_BYTES, 'allocation limit')
    sounds = parse_bank(source)
    _require(len(sounds) + 1 <= 65535, 'BNKl slot count')
    pcm_bytes = sum(s.frames * s.channels * 2 for s in sounds if s is not None)
    _require(pcm_bytes <= max_bytes, 'PCM allocation limit')
    for sound in sounds:
        if sound is not None:
            _require(sound.channels == 1, 'MW stereo PT layout unverified')
    selectors = source_selectors(source, sounds, table_offsets=table_offsets)
    table_bytes = sum(4 + 12 * len(s.indices) for s in selectors)
    bank_offset = _align(offset + table_bytes, 128)
    slots = len(sounds) + 1
    header_end = 20 + 4 * slots
    for sound in sounds:
        if sound is not None:
            header_end += _align(len(_pt(sound, 0)), 4)
    used = header_end + sum(_align(s.frames * 2, 4) for s in sounds if s is not None)
    extent = _align(used, 128)
    total = bank_offset - offset + extent
    _require(total <= max_bytes and bank_offset + extent < (1 << 31), 'bank allocation/placement limit')
    decoded = tuple(decode_bank(source) if decoded is None else decoded)
    _require(len(decoded) == len(sounds), 'decoded slot count')
    for sound, item in zip(sounds, decoded):
        if sound is None:
            _require(item is None, 'decoded null slot identity')
        else:
            _require(isinstance(item, DecodedSound) and item.sound == sound, 'decoded source identity')
            _require(isinstance(item.pcm, bytes) and len(item.pcm) == sound.frames * 2,
                     'decoded PCM byte count')
    out = bytearray(total)
    placed, pos = [], 0
    for selector in selectors:
        table = struct.pack('<I', len(selector.indices)) + b''.join(
            struct.pack('<3I', 0xff00, 0 if index is None else index + 1, 0)
            for index in selector.indices)
        out[pos:pos + len(table)] = table
        placed.append(PlacedSelector(selector, offset + pos, table))
        pos += len(table)
    base = bank_offset - offset
    out[base:base + 20] = struct.pack('<4sHH3I', b'BNKl', 5, slots, used, 0, 0)
    h, p = 20 + 4 * slots, header_end
    digest = hashlib.sha256(source).hexdigest()
    identities = [None]
    for index, item in enumerate(decoded):
        if item is None:
            identities.append(None)
            continue
        site = 20 + 4 * (index + 1)
        struct.pack_into('<I', out, base + site, h - site)
        header = _pt(item.sound, p)
        out[base + h:base + h + len(header)] = header
        out[base + p:base + p + len(item.pcm)] = item.pcm
        h += _align(len(header), 4)
        p += _align(len(item.pcm), 4)
        identities.append((digest, index))
    _require(h == header_end and p == used, 'internal bank extent mismatch')
    return SampleBank(digest, offset, bytes(out), bank_offset, extent, used,
                      pcm_bytes, tuple(placed), tuple(identities))
