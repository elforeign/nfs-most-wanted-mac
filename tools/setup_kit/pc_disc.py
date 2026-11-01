"""PC disc images of the game: a bounded, read-only ISO 9660 reader and the disc set a chosen disc belongs to.

EA's PC installer keeps the game in plain deflate .zip archives at the root of each disc (0compressed.zip on disc 1,
1compressed.zip on disc 2 and so on), with Windows paths inside. The loose files it installs from disc 1 (speed.exe,
server.dll, ...) are listed in that disc's common_filelist.txt as "disc,flag,path" lines, which also name every disc
of the set. Each disc's autorun.inf carries its number and the set's id ([Special] Disk=, ProductGuiID=).

A disc is an .iso image (read here, never mounted) or a folder holding the disc's files (a disc mounted in Finder, or a
copy of one). Only root directories are read, and every extent is checked against the image, so a malformed image can
neither read outside itself nor name a path outside the folder it is unpacked into.
"""
import io
import os
from pathlib import Path
import re
import struct

SECTOR = 2048
MAX_DESCRIPTORS = 32
MAX_DIRECTORY = 1 << 20      # a disc root holds a few dozen entries
MAX_ROOT_FILES = 4096
MAX_INFO = 64 << 10          # autorun.inf and common_filelist.txt are a few hundred bytes
MAX_SIBLINGS = 256           # entries beside the chosen disc that are looked at for the rest of its set
JOLIET = (b"%/@", b"%/C", b"%/E")
ARCHIVE = re.compile(r"(\d{1,2})compressed\.zip")
FALLBACK_FILES = ("speed.exe", "server.dll", "server.cfg")   # disc 1's loose game files when it has no common_filelist.txt
FIX = "Choose disc 1 of your PC game discs (the .iso image, or the disc mounted in Finder), with the other discs beside it."


class DiscError(ValueError):
    def __init__(self, msg, fix=FIX):
        super().__init__(msg)
        self.fix = fix


def safe_name(name):
    if (not name or len(name) > 255 or name in (".", "..") or any(c in name for c in "/\\:")
            or any(ord(c) < 32 or ord(c) == 0x7F for c in name)):
        raise DiscError(f"The disc image contains an unsafe name: {name!r}")
    return name


class Window(io.RawIOBase):
    """A read-only, seekable view of one file inside the image (zipfile reads the game archives through it)."""
    def __init__(self, f, offset, size):
        super().__init__()
        self.f, self.offset, self.size, self.pos = f, offset, size, 0

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, pos, whence=io.SEEK_SET):
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self.pos, io.SEEK_END: self.size}[whence]
        self.pos = max(0, base + pos)
        return self.pos

    def readinto(self, buffer):
        n = max(0, min(len(buffer), self.size - self.pos))
        if not n:
            return 0
        self.f.seek(self.offset + self.pos)
        data = self.f.read(n)
        if len(data) != n:
            raise DiscError("The disc image is truncated (it ends inside a file).",
                            "Make the disc image again, or download it again if the download was interrupted.")
        buffer[:n] = data
        self.pos += n
        return n


def _root(f, size):
    """Root directory (extent, length) and whether names are Joliet's: preferred, as the discs' archive names are
    longer than ISO 9660's 8.3 names."""
    primary = joliet = None
    for i in range(MAX_DESCRIPTORS):
        off = (16 + i) * SECTOR
        if off + SECTOR > size:
            break
        f.seek(off)
        d = f.read(SECTOR)
        if len(d) != SECTOR or d[1:6] != b"CD001" or d[0] == 255:
            break
        if d[0] in (1, 2) and struct.unpack_from("<H", d, 128)[0] != SECTOR:
            raise DiscError("The disc image uses an unsupported block size.")
        if d[0] == 1 and primary is None:
            primary = d
        elif d[0] == 2 and d[88:91] in JOLIET and joliet is None:
            joliet = d
    vd = joliet or primary
    if vd is None:
        raise DiscError("The file is not a disc image Setup can read (no ISO 9660 file system).",
                        "Open the image in Finder and choose the mounted disc instead.")
    extent, length = struct.unpack_from("<I", vd, 156 + 2)[0], struct.unpack_from("<I", vd, 156 + 10)[0]
    return extent, length, vd is joliet


def _listing(f, size, extent, length, joliet):
    """{casefolded name: (name, offset, size)} of the root directory's files; folders are not needed."""
    if not 0 < length <= MAX_DIRECTORY or extent * SECTOR + length > size:
        raise DiscError("The disc image's root directory is damaged.")
    f.seek(extent * SECTOR)
    data = f.read(length)
    if len(data) != length:
        raise DiscError("The disc image is truncated.", "Make the disc image again.")
    files, pos = {}, 0
    while pos < length:
        n = data[pos]
        if n == 0:  # records never cross a sector; the rest of this one is padding
            pos = (pos // SECTOR + 1) * SECTOR
            continue
        if n < 34 or pos + n > length or pos % SECTOR + n > SECTOR or 33 + data[pos + 32] > n:
            raise DiscError("The disc image's root directory is damaged.")
        start, count = struct.unpack_from("<I", data, pos + 2)[0], struct.unpack_from("<I", data, pos + 10)[0]
        flags, raw = data[pos + 25], data[pos + 33:pos + 33 + data[pos + 32]]
        pos += n
        if raw in (b"\0", b"\1") or flags & 0x02:
            continue
        if flags & 0x80:
            raise DiscError("The disc image stores a file in several parts, which Setup doesn't read.",
                            "Open the image in Finder and choose the mounted disc instead.")
        try:
            name = raw.decode("utf-16-be") if joliet else raw.decode("latin-1")
        except UnicodeDecodeError:
            raise DiscError("The disc image's root directory is damaged.") from None
        name = name.split(";")[0]
        name = safe_name(name if joliet else name.rstrip("."))
        if start * SECTOR + count > size:
            raise DiscError(f"The disc image is truncated: {name} lies past its end.",
                            "Make the disc image again, or download it again if the download was interrupted.")
        if name.casefold() in files or len(files) >= MAX_ROOT_FILES:
            raise DiscError(f"The disc image lists {name} twice, or too many files.")
        files[name.casefold()] = (name, start * SECTOR, count)
    return files


class Disc:
    """One disc: an .iso image or a folder with the disc's files. Lists its root files and reads its autorun.inf."""
    def __init__(self, path):
        self.path, self.handle = Path(path), None
        if self.path.is_dir():
            self.files = {}
            for p in self.path.iterdir():
                if p.is_file() and not p.is_symlink():
                    if p.name.casefold() in self.files:
                        raise DiscError(f"The disc folder has {p.name} twice (in different case).")
                    self.files[p.name.casefold()] = (p.name, None, p.stat().st_size)
        elif self.path.is_file():
            self.handle = self.path.open("rb")
            try:
                size = os.fstat(self.handle.fileno()).st_size
                self.files = _listing(self.handle, size, *_root(self.handle, size))
            except BaseException:
                self.close()
                raise
        else:
            raise DiscError(f"{self.path} is not a disc image or a disc folder.")
        inf = self.text("autorun.inf")
        number = re.search(r"(?im)^\s*Disk\s*=\s*(\d+)\s*$", inf)
        product = re.search(r"(?im)^\s*ProductGuiID\s*=\s*(\S+)\s*$", inf)
        self.number = int(number.group(1)) if number else 1
        self.product = product.group(1).upper() if product else None
        archives = sorted((int(m.group(1)), self.files[k][0]) for k in self.files if (m := ARCHIVE.fullmatch(k)))
        self.archives = [name for _, name in archives]

    def close(self):
        if self.handle:
            self.handle.close()
            self.handle = None

    def __contains__(self, name):
        return name.casefold() in self.files

    def size(self, name):
        return self.files[name.casefold()][2]

    def open(self, name):
        real, offset, size = self.files[name.casefold()]
        if self.handle is None:
            return (self.path / real).open("rb")
        return io.BufferedReader(Window(self.handle, offset, size), 1 << 20)

    def read(self, name, limit):
        if not 0 <= self.size(name) <= limit:
            raise DiscError(f"{name} on the disc is far larger than expected.")
        with self.open(name) as f:
            data = f.read(limit + 1)
        if len(data) != self.size(name):
            raise DiscError(f"{name} changed or was cut short while Setup read it.", "Make the disc image again.")
        return data

    def text(self, name):
        return self.read(name, MAX_INFO).decode("latin-1") if name in self else ""


def install_list(disc):
    """(numbers of every disc of the set, loose files of disc 1 the installer copies) from common_filelist.txt.
    Entries with a folder or wildcard are the installer's help and support files, not the game's."""
    numbers, files = {1}, []
    listing = disc.text("common_filelist.txt")
    for line in listing.splitlines():
        parts = [p.strip() for p in line.split(",", 2)]
        if len(parts) != 3 or not parts[0].isdigit():
            continue
        number = int(parts[0])
        if not 1 <= number <= 16:
            raise DiscError(f"common_filelist.txt names an unknown disc: {line.strip()!r}")
        numbers.add(number)
        if number == 1 and parts[2] and not any(c in parts[2] for c in "\\/*?"):
            if parts[2] not in disc:
                raise DiscError(f"Disc 1 has no {parts[2]}, which its own file list names.",
                                "Make the disc image again from the original disc.")
            files.append(disc.files[parts[2].casefold()][0])
    if not listing:
        files = [disc.files[n][0] for n in FALLBACK_FILES if n in disc]
    return numbers, files


def is_disc(path):
    """A disc image (.iso) or a folder with a disc's game archives, as opposed to an installed game folder."""
    path = Path(path)
    if path.is_file():
        return path.suffix.lower() == ".iso"
    try:
        names = {p.name.casefold() for p in path.iterdir()} if path.is_dir() else set()
    except OSError:
        return False
    return "autorun.inf" in names and any(ARCHIVE.fullmatch(n) for n in names) and "global" not in names


MISSING_FIX = ("Choose the missing disc as well (Setup reads each disc's number from the disc itself, whatever its name), "
               "or keep the images of all your discs in one folder.")


def disc_set(chosen, extra=()):
    """{number: Disc} of every disc the set needs, in order. extra are discs the player chose one by one, from any
    folder; the rest are looked for beside the chosen disc (and those): the other .iso images there (for an image) or
    folders (for a folder, such as the discs mounted in /Volumes). Every disc is identified by its own autorun.inf
    (its number and the set's id), never by its name. The caller closes the discs."""
    chosen = Path(chosen)
    first = Disc(chosen)
    found = {first.number: first}
    try:
        if not first.archives:
            raise DiscError("This disc holds none of the game's archives (0compressed.zip, 1compressed.zip, ...).",
                            "Choose a disc of the PC game Need for Speed: Most Wanted.")
        for path in map(Path, extra):
            other = Disc(path)
            if not other.archives or other.product != first.product:
                other.close()
                raise DiscError(f"{path.name} is not a disc of the same game set as {chosen.name}.",
                                "Choose the discs of one copy of the game.")
            if other.number in found:
                other.close()
                raise DiscError(f"{path.name} is disc {other.number}, which is already chosen.",
                                "Choose each disc once.")
            found[other.number] = other
        if first.product:
            beside = []
            for folder in dict.fromkeys([chosen.parent] + [Path(p).parent for p in extra]):
                try:
                    beside += sorted(folder.iterdir())[:MAX_SIBLINGS]
                except OSError:
                    pass
            image = first.handle is not None
            for p in beside:
                if p == chosen or p in map(Path, extra) or not (p.is_file() and p.suffix.lower() == ".iso" if image else p.is_dir()):
                    continue
                try:
                    other = Disc(p)
                except (DiscError, OSError):
                    continue
                if other.product == first.product and other.archives and other.number not in found:
                    found[other.number] = other
                else:
                    other.close()
        # The Setup app reads the missing numbers from "Disc N[, M] of the set" to offer a choice for each.
        if 1 not in found:
            raise DiscError("Disc 1 of the set was not found with the chosen disc.", MISSING_FIX)
        numbers, _ = install_list(found[1])
        missing = sorted(numbers - set(found))
        if missing:
            raise DiscError(f"Disc {', '.join(map(str, missing))} of the set ({len(numbers)} discs) was not found with disc 1.",
                            MISSING_FIX)
    except BaseException:
        for disc in found.values():
            disc.close()
        raise
    for number in set(found) - numbers:
        found.pop(number).close()
    return {n: found[n] for n in sorted(found)}
