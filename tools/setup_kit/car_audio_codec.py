"""Bounded resident ABKC/S10A codec-4 reader and EA-XAS decoder.

ABKC fields are little endian; S10A fields and block headers are big endian.
Each XAS unit has 128 frames in 76 bytes per channel, with little endian
predictor histories. PCM is signed interleaved 16-bit little endian. Loop end
is inclusive (frames - 1), matching the destination PT sample-bank contract.
No streaming storage, external resources, or other codecs are inferred.
"""
from __future__ import annotations

from array import array
from dataclasses import dataclass
import struct
import sys


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _words(data, offset, fmt, end=None):
    end = len(data) if end is None else end
    _require(0 <= offset <= end <= len(data) and struct.calcsize(fmt) <= end - offset,
             'truncated audio field')
    return struct.unpack_from(fmt, data, offset)


@dataclass(frozen=True)
class Block:
    """One independently trimmed resident block; payload owns its bytes."""
    offset: int
    frames: int
    payload: bytes


@dataclass(frozen=True)
class Sound:
    slot: int
    version: int
    codec: int
    storage: int
    channels: int
    rate: int
    frames: int
    loop_start: int | None
    loop_end: int | None
    header_offset: int
    end_offset: int
    blocks: tuple[Block, ...]


@dataclass(frozen=True)
class DecodedSound:
    """Source metadata and exact declared PCM; null bank slots remain None."""
    sound: Sound
    pcm: bytes


def parse_bank(data):
    """Return a tuple of Sound or None, preserving S10A slot identities.

    Only version 0, codec 4, resident mono/stereo is supported. Every header
    and block must fit its own sound extent, not merely the containing file.
    Trailing bytes in the ABKC outside its declared S10A extent are untouched.
    """
    _require(len(data) >= 0x60 and data[:4] == b'ABKC', 'expected ABKC header')
    _require(_words(data, 0x14, '<I')[0] == len(data), 'ABKC declared size')
    bank, extent = _words(data, 0x20, '<2I')
    _require(bank >= 0x60 and extent >= 12 and bank <= len(data)
             and extent <= len(data) - bank, 'ABKC bank extent')
    end = bank + extent
    _require(data[bank:bank + 4] == b'S10A', 'expected S10A bank')
    reserved, count = _words(data, bank + 4, '>2I', end)
    _require(reserved == 0 and count <= 4096 and 12 + 4 * count <= extent,
             'S10A reserved/count/table')
    offsets = _words(data, bank + 12, f'>{count}I', end)
    live = [(slot, bank + rel) for slot, rel in enumerate(offsets) if rel != 0xffffffff]
    previous = bank + 12 + count * 4 - 1
    for _, at in live:
        _require(previous < at and at + 8 <= end, 'S10A sound offset/order')
        previous = at
    sounds = [None] * count
    for index, (slot, at) in enumerate(live):
        stop = live[index + 1][1] if index + 1 < len(live) else end
        a, b = _words(data, at, '>2I', stop)
        version, codec = a >> 28, (a >> 24) & 15
        channels, rate = ((a >> 18) & 63) + 1, a & 0x3ffff
        storage, loop, frames = b >> 30, bool(b & 0x20000000), b & 0x1fffffff
        _require(version == 0 and codec == 4, 'unsupported S10A version/codec')
        _require(storage == 0, f'unsupported S10A storage {storage}: resident required')
        _require(channels in (1, 2) and rate > 0, 'unsupported S10A channels/rate')
        _require(frames > 0, 'S10A zero frames')
        pos = at + 8
        loop_start = _words(data, pos, '>I', stop)[0] if loop else None
        pos += 4 if loop else 0
        _require(not loop or loop_start < frames, 'S10A loop outside frames')
        total, blocks = 0, []
        while total < frames:
            size, block_frames = _words(data, pos, '>2I', stop)
            _require(0 < block_frames <= frames - total, 'resident block frame count')
            expected = 8 + ((block_frames + 127) // 128) * 76 * channels
            _require(size == expected and size <= stop - pos, 'resident block bounds/size')
            payload = bytes(data[pos + 8:pos + size])
            # Only the four classified XAS predictors are admitted. The nibble
            # is an index, not a signed history or a shift value.
            _require(all(payload[p + n * 4] & 15 < 4
                         for p in range(0, len(payload), 76)
                         for n in range(4)), 'unsupported XAS predictor')
            blocks.append(Block(pos, block_frames, payload))
            pos += size
            total += block_frames
        _require(pos == stop, 'unexplained bytes after resident blocks')
        sounds[slot] = Sound(slot, version, codec, storage, channels, rate, frames,
                             loop_start, frames - 1 if loop else None,
                             at, stop, tuple(blocks))
    return tuple(sounds)


_COEFFICIENTS = ((0, 0), (240, 0), (460, -208), (392, -220))


def _decode_unit(payload, offset):
    """Decode one channel's four 32-sample lanes in an XAS unit."""
    samples = [0] * 128
    for lane in range(4):
        first, second = struct.unpack_from('<hh', payload, offset + lane * 4)
        c0, c1 = _COEFFICIENTS[first & 15]
        shift = 20 - (second & 15)
        base = lane * 32
        samples[base], samples[base + 1] = first & ~15, second & ~15
        previous, current = samples[base:base + 2]
        for pair in range(15):
            byte = payload[offset + 16 + pair * 4 + lane]
            for half, nibble in enumerate((byte >> 4, byte & 15)):
                level = (nibble if nibble < 8 else nibble - 16) * (1 << shift)
                value = (level + current * c0 + previous * c1 + 128) >> 8
                value = min(32767, max(-32768, value))
                samples[base + 2 + pair * 2 + half] = value
                previous, current = current, value
    return samples


def decode_sound(sound):
    """Decode a parsed sound, trimming each block before concatenation.

    Loop endpoints address the declared PCM, never the padded XAS frames.
    Keep the Sound object with the result so sample identity cannot be lost.
    """
    pcm = array('h')
    for block in sound.blocks:
        remaining = block.frames
        for at in range(0, len(block.payload), 76 * sound.channels):
            planar = [_decode_unit(block.payload, at + 76 * channel)
                      for channel in range(sound.channels)]
            take = min(128, remaining)
            if sound.channels == 1:
                pcm.extend(planar[0][:take])
            else:
                pcm.extend(value for row in zip(*(p[:take] for p in planar)) for value in row)
            remaining -= take
        _require(remaining == 0, 'decoded block frame count')
    _require(len(pcm) == sound.frames * sound.channels, 'decoded sound frame count')
    if sys.byteorder != 'little':
        pcm.byteswap()
    return DecodedSound(sound, pcm.tobytes())


def decode_bank(data):
    """Decode all present slots, leaving empty slots as None."""
    return tuple(decode_sound(sound) if sound is not None else None for sound in parse_bank(data))
