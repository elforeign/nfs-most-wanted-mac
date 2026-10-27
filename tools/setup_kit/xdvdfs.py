"""Bounded, read-only XDVDFS reader, vendored from tools/scripts/xdvdfs.py.

All offsets are checked before extraction; malformed directories cannot recurse
forever, escape the destination or turn an EOF into an infinite copy loop.
"""
from dataclasses import dataclass
from pathlib import PurePosixPath
import os
import struct

SECTOR = 2048
PARTITION_OFFSETS = (0, 0xFD90000, 0x2080000, 0x18300000)
MAGIC = b"MICROSOFT*XBOX*MEDIA"
MAX_DIRECTORY = 16 << 20
MAX_FILES = 100000


class DiscError(ValueError):
    pass


def safe_path(path):
    path = path.replace("\\", "/")
    parts = path.split("/")
    if (not path or len(path) > 1024 or any(p in ("", ".", "..") for p in parts)
            or any(ord(c) < 32 or c in ":\x7f" for c in path)):
        raise DiscError(f"unsafe asset path: {path!r}")
    return str(PurePosixPath(*parts))


def length(f):
    return os.fstat(f.fileno()).st_size


def check_range(f, offset, size, *, sectors=False):
    end = offset + ((size + SECTOR - 1) // SECTOR * SECTOR if sectors else size)
    if offset < 0 or size < 0 or end > length(f):
        raise DiscError(f"disc range outside image: offset={offset}, bytes={size}")


def read_exact(f, offset, size):
    check_range(f, offset, size)
    f.seek(offset)
    data = f.read(size)
    if len(data) != size:
        raise DiscError(f"truncated input at {offset}: {len(data)}/{size} bytes")
    return data


def find_base(f):
    for base in PARTITION_OFFSETS:
        off = base + 32 * SECTOR
        if off + SECTOR <= length(f) and read_exact(f, off, 20) == MAGIC:
            return base
    raise DiscError("no XDVDFS volume descriptor found (or truncated sector)")


def walk(f, base, sector, size, prefix="", _seen=None, _depth=0):
    seen = set() if _seen is None else _seen
    if _depth > 64 or (sector, size) in seen:
        raise DiscError("cyclic or excessively deep disc directory")
    if not 0 < size <= MAX_DIRECTORY:
        raise DiscError("invalid disc directory size")
    seen.add((sector, size))
    off = base + sector * SECTOR
    check_range(f, off, size, sectors=True)
    data = read_exact(f, off, size)
    pos, names, entries, pointers = 0, set(), set(), []
    while pos + 14 <= size:
        left, right, start, count, attr, n = struct.unpack_from("<HHIIBB", data, pos)
        if left == right == 0xFFFF or not any(data[pos:pos + 14]):
            pos = (pos // SECTOR + 1) * SECTOR
            continue
        if not n or pos + 14 + n > size:
            raise DiscError("truncated directory entry")
        name = data[pos + 14:pos + 14 + n].decode("latin-1")
        if "/" in name or "\\" in name:
            raise DiscError(f"unsafe disc name: {name!r}")
        name = safe_path(name)
        if name.upper() in names:
            raise DiscError(f"duplicate disc name: {name}")
        names.add(name.upper())
        entries.add(pos)
        pointers.extend(p * 4 for p in (left, right) if p)
        path = safe_path(prefix + name)
        check_range(f, base + start * SECTOR, count, sectors=True)
        if attr & 0x10:
            yield from walk(f, base, start, count, path + "/", seen, _depth + 1)
        else:
            yield path, base + start * SECTOR, count
        pos = (pos + 14 + n + 3) & ~3
    if any(p not in entries for p in pointers):
        raise DiscError("directory tree pointer outside an entry")


@dataclass(frozen=True)
class Entry:
    path: str
    offset: int
    size: int


def inventory(f):
    base = find_base(f)
    sector, size = struct.unpack("<II", read_exact(f, base + 32 * SECTOR + 20, 8))
    result = {}
    for path, off, n in walk(f, base, sector, size):
        if path.upper() in result or len(result) >= MAX_FILES:
            raise DiscError("duplicate or too many disc files")
        result[path.upper()] = Entry(path, off, n)
    return result


def copy_range(f, offset, size, dest):
    check_range(f, offset, size)
    dest.parent.mkdir(parents=True, exist_ok=True)
    f.seek(offset)
    with dest.open("xb") as output:
        remaining = size
        while remaining:
            data = f.read(min(remaining, 4 << 20))
            if not data:
                raise DiscError("input disappeared or was truncated during extraction")
            output.write(data)
            remaining -= len(data)
