#!/usr/bin/env python3
"""Validate independent release archives: exact members, arm64 objects, macOS 14.5.

The manifest lists object basenames, including repeated names. It is intentionally
fixed rather than inferred from the archive being checked. No extraction or game
inputs are needed. This checks build compatibility, not distribution permission.
"""
import argparse
from collections import Counter
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = Path(__file__).with_name('library_members.tsv')
MIN_OS = (14 << 16) | (5 << 8)


def members(data):
    """Read BSD ar members in order, retaining duplicates instead of overwriting them."""
    if not data.startswith(b'!<arch>\n'):
        raise ValueError('not a regular ar archive')
    offset = 8
    while offset < len(data):
        header = data[offset:offset + 60]
        if len(header) != 60 or header[58:] != b'`\n':
            raise ValueError('invalid ar member header')
        size = int(header[48:58])
        start = offset + 60
        end = start + size
        if size < 0 or end > len(data):
            raise ValueError('truncated ar member')
        name = header[:16].decode('ascii').rstrip()
        payload = data[start:end]
        if name.startswith('#1/'):
            length = int(name[3:])
            if length < 1 or length > size:
                raise ValueError('invalid extended member name')
            name = payload[:length].rstrip(b'\0').decode('ascii')
            payload = payload[length:]
        else:
            name = name.removesuffix('/')
        if name not in ('__.SYMDEF', '__.SYMDEF SORTED', '__.SYMDEF_64', '__.SYMDEF_64 SORTED'):
            yield name, payload
        offset = end + (size & 1)
        if offset > len(data):
            raise ValueError('missing ar padding')


def object_target(data):
    if len(data) < 32:
        raise ValueError('truncated Mach-O object')
    magic, cpu, subtype, kind, ncmds, sizeofcmds, flags, reserved = struct.unpack_from('<8I', data)
    if magic != 0xfeedfacf or cpu != 0x100000c or kind != 1:
        raise ValueError('expected a thin arm64 Mach-O object')
    if sizeofcmds > len(data) - 32:
        raise ValueError('truncated load commands')
    offset = 32
    versions = []
    for _ in range(ncmds):
        if offset + 8 > 32 + sizeofcmds:
            raise ValueError('missing load command')
        command, size = struct.unpack_from('<2I', data, offset)
        if size < 8 or offset + size > 32 + sizeofcmds:
            raise ValueError('invalid load command size')
        if command == 0x32:  # LC_BUILD_VERSION
            if size < 24:
                raise ValueError('truncated LC_BUILD_VERSION')
            platform, version = struct.unpack_from('<2I', data, offset + 8)
            if platform != 1:
                raise ValueError('expected macOS platform')
            versions.append(version)
        elif command == 0x24:  # LC_VERSION_MIN_MACOSX
            if size < 16:
                raise ValueError('truncated LC_VERSION_MIN_MACOSX')
            versions.append(struct.unpack_from('<I', data, offset + 8)[0])
        offset += size
    if offset != 32 + sizeofcmds or len(versions) != 1:
        raise ValueError('expected exactly one minimum OS command')
    version = versions[0]
    if version != MIN_OS:
        shown = f'{version >> 16}.{(version >> 8) & 255}.{version & 255}'
        raise ValueError(f'minimum macOS {shown}, expected 14.5.0')


def manifest():
    archives = {}
    for line in MANIFEST.read_text().splitlines():
        if not line or line.startswith('#'):
            continue
        archive, member = line.split('\t')
        archives.setdefault(archive, Counter())[member] += 1
    return archives


def check_archive(path, expected):
    objects = list(members(path.read_bytes()))
    actual = Counter(name for name, _ in objects)
    if actual != expected:
        raise ValueError(f'members differ: unexpected={dict(actual - expected)}, missing={dict(expected - actual)}')
    for name, data in objects:
        try:
            object_target(data)
        except ValueError as error:
            raise ValueError(f'{name}: {error}') from error
    return len(objects)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--group', choices=('all', 'audio', 'shaders', 'ffmpeg'), default='all')
    parser.add_argument('--shader-archive', type=Path, help='check an unpublished translator archive')
    args = parser.parse_args()
    groups = {'audio': 'host/audio/', 'shaders': 'host/shaders/', 'ffmpeg': 'third_party/ffmpeg/'}
    count = total = 0
    for relative, expected in manifest().items():
        if args.group != 'all' and not relative.startswith(groups[args.group]):
            continue
        path = args.root / relative
        if args.shader_archive and relative.endswith('libsmtranslate.a'):
            path = args.shader_archive
        try:
            total += check_archive(path, expected)
        except (OSError, ValueError, struct.error, UnicodeError) as error:
            print(f'FAIL {relative}: {error}', file=sys.stderr)
            return 1
        count += 1
    print(f'libraries: {count} archives, {total} arm64 members, minimum macOS 14.5.0')
    return 0


if __name__ == '__main__':
    sys.exit(main())
