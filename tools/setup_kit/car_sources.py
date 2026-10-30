"""Recognise audited later-game inputs without changing their files.

The fingerprint catalog contains no source paths or car roster. File IDs are
the game's case-insensitive path hashes; car IDs hash their parent directory.
``cars`` and ``shared`` describe the complete audited conversion inputs, with
identical IDs and hashes for an ISO and its extracted files. Extra files are
ignored, and are never implicitly approved for conversion. This is source
recognition only: it does not approve converted output or decode car meshes.
"""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import struct

from . import xdvdfs
from .x360 import Source, X360Error

MANIFEST = Path(__file__).with_suffix(".json")
MAX_EXECUTABLE = 128 << 20
CARBON_TITLE = "0x454107EC"
UNDERCOVER_TITLE = "0x45410876"
GAME_NAMES = {"carbon": "Carbon", "prostreet": "ProStreet", "undercover": "Undercover"}


class SourceError(ValueError):
    """An incomplete, damaged or unaudited source was selected."""


def path_hash(path):
    value = 0xFFFFFFFF
    for c in path.upper().replace("/", "\\").encode("latin-1"):
        value = (value * 33 + c) & 0xFFFFFFFF
    return f"{value:08x}"


def _require(ok, message):
    if not ok:
        raise SourceError(message)


def _range(data, offset, size):
    _require(0 <= offset <= len(data) and 0 <= size <= len(data) - offset,
             "Executable metadata is truncated or outside its file")
    return data[offset:offset + size]


def pe_info(data):
    """Read PE32 identity and the version resource, if present (never guess it)."""
    _require(_range(data, 0, 2) == b"MZ", "PC executable lacks an MZ header")
    pe = struct.unpack("<I", _range(data, 60, 4))[0]
    _require(_range(data, pe, 4) == b"PE\0\0", "Invalid PE signature or byte order")
    machine, sections, _, _, _, optional_size, _ = struct.unpack(
        "<HHIIIHH", _range(data, pe + 4, 20))
    _require(machine == 0x14C and 0 < sections <= 96, "Unsupported PC executable architecture")
    optional = _range(data, pe + 24, optional_size)
    _require(optional_size >= 120 and optional[:2] == b"\x0b\x01", "Invalid PE32 optional header")
    table = _range(data, pe + 24 + optional_size, sections * 40)
    spans = []
    for i in range(sections):
        virtual_size, rva, raw_size, raw = struct.unpack_from("<4I", table, i * 40 + 8)
        _range(data, raw, raw_size)
        spans.append((rva, virtual_size, raw, raw_size))

    def at_rva(rva, size):
        for start, _, raw, count in spans:
            if start <= rva and 0 <= size <= count - (rva - start):
                return _range(data, raw + rva - start, size)
        raise SourceError("PE resource range is outside a section")

    result = {"machine": machine, "file_version": None, "product_version": None}
    if struct.unpack_from("<I", optional, 92)[0] <= 2:
        return result
    resource_rva, resource_size = struct.unpack_from("<II", optional, 112)
    if not resource_size:
        return result
    _require(resource_size <= 16 << 20, "Oversized PE resource directory")
    resource = at_rva(resource_rva, resource_size)

    def entries(offset):
        head = _range(resource, offset, 16)
        named, ids = struct.unpack_from("<HH", head, 12)
        _require(named + ids <= 1024, "Too many PE resource entries")
        return struct.iter_unpack("<II", _range(resource, offset + 16, 8 * (named + ids)))

    versions = [target for key, target in entries(0) if key == 16]
    _require(len(versions) <= 1, "Duplicate PE version resources")
    if not versions:
        return result
    version_values = set()

    def visit(target, depth):
        _require(depth <= 3, "Excessively deep PE version resource")
        if target & 0x80000000:
            for _, child in entries(target & 0x7FFFFFFF):
                visit(child, depth + 1)
            return
        rva, size, _, _ = struct.unpack("<4I", _range(resource, target, 16))
        _require(size <= 1 << 20, "Oversized PE version value")
        value = at_rva(rva, size)
        length, value_size, kind = struct.unpack("<HHH", _range(value, 0, 6))
        key = "VS_VERSION_INFO\0".encode("utf-16le")
        _require(6 + len(key) <= length <= len(value) and value_size == 52 and kind == 0
                 and _range(value, 6, len(key)) == key, "Invalid PE version value")
        offset = (6 + len(key) + 3) & ~3
        _require(offset + 52 <= length, "Truncated PE fixed version")
        fixed = struct.unpack("<13I", _range(value, offset, 52))
        _require(fixed[:2] == (0xFEEF04BD, 0x10000), "Invalid PE fixed-version signature")
        version_values.add(tuple(fixed[2:6]))

    visit(versions[0], 0)
    _require(len(version_values) == 1, "Conflicting PE version resources")
    fixed = next(iter(version_values))
    result["file_version"] = [x for n in fixed[:2] for x in (n >> 16, n & 65535)]
    result["product_version"] = [x for n in fixed[2:] for x in (n >> 16, n & 65535)]
    return result


def xex_info(data):
    """XEX2 optional-header keys and execution info use big-endian integers."""
    _require(_range(data, 0, 4) == b"XEX2", "Invalid XEX2 signature or byte order")
    count = struct.unpack(">I", _range(data, 20, 4))[0]
    _require(0 < count <= 64, "Invalid XEX optional-header count")
    headers = _range(data, 24, count * 8)
    offsets = [v for k, v in struct.iter_unpack(">II", headers) if k == 0x40006]
    _require(len(offsets) == 1 and offsets[0] >= 24 + count * 8,
             "Missing or ambiguous XEX execution info")
    _, version, base, title, _, _, disc, discs, _ = struct.unpack(
        ">IIII4BI", _range(data, offsets[0], 24))
    _require(0 < disc <= discs, "Invalid XEX disc number")
    return {"title_id": f"0x{title:08X}", "version": version, "base_version": base}


@contextmanager
def _source(path):
    source = None
    try:
        source = Source(path)
        yield source
    except (OSError, X360Error, xdvdfs.DiscError, UnicodeError, struct.error) as error:
        raise SourceError(str(error)) from error
    finally:
        if source is not None:
            source.close()


def _physical(source):
    files = {}
    for key in source.files:
        name = key[6:] if key.startswith("FILES/") else key
        _require(name not in files, "Ambiguous root and FILES directory contents")
        files[name] = key
    return files


def _read(source, entry, offset=0, size=None):
    key, start, count = entry
    size = count - offset if size is None else size
    _require(0 <= offset <= count and 0 <= size <= count - offset,
             "Logical file range is outside its archive")
    _require(start + count <= source.size(key), "Truncated source archive")
    if source.handle:
        return xdvdfs.read_exact(source.handle, source.files[key].offset + start + offset, size)
    with source.files[key].open("rb") as handle:
        return xdvdfs.read_exact(handle, start + offset, size)


def _digest(source, entry):
    digest = hashlib.sha256()
    for offset in range(0, entry[2], 1 << 20):
        digest.update(_read(source, entry, offset, min(1 << 20, entry[2] - offset)))
    return digest.hexdigest()


def _logical(source, files):
    """Resolve logical IDs from ZDIR or fully extracted CARS/GLOBAL files."""
    if "NFS/ZDIR.BIN" not in files:
        result = {}
        for name, key in files.items():
            if name.startswith(("CARS/", "GLOBAL/")):
                identity = path_hash(name)
                _require(identity not in result, "Colliding logical file hashes")
                result[identity] = (key, 0, source.size(key))
        return result
    key = files["NFS/ZDIR.BIN"]
    size = source.size(key)
    _require(0 < size <= 24 * xdvdfs.MAX_FILES and size % 24 == 0, "Invalid ZDIR table size")
    rows = struct.iter_unpack("<6I", _read(source, (key, 0, size)))
    undercover = "NFS/00GLOBAL.BIN" in files
    result, seen = {}, {}
    for row in rows:
        identity, archive, sector, _, count, _ = row
        identity = f"{identity:08x}"
        if undercover:
            _require(identity not in seen, "Duplicate Undercover ZDIR entry")
        _require(identity not in seen or row[4:] == seen[identity][4:],
                 "Conflicting duplicate ZDIR entries")
        seen[identity] = row
        if archive == 0xFFFFFFFF:
            _require(count == 0, "ZDIR stub has a nonempty range")
            continue
        _require(archive <= 99, "Invalid ZDIR archive number or byte order")
        if undercover:
            # Other numbered archives contain tracks/audio/frontend data, not
            # conversion inputs. Never open or fingerprint those archives.
            if archive != 0 and not 16 <= archive <= 20:
                continue
            _require(count <= 128 << 20, "Invalid Undercover ZDIR range size")
            name = "NFS/00GLOBAL.BIN" if archive == 0 else f"NFS/{archive:02}CARS.BIN"
        else:
            name = f"NFS/ZZDATA{archive}.BIN"
        _require(name in files, "Missing ZDIR archive")
        key, start = files[name], sector * xdvdfs.SECTOR
        _require(start <= source.size(key) and count <= source.size(key) - start,
                 "ZDIR file range exceeds its archive")
        entry = (key, start, count)
        if identity in result:
            # Retail archives replicate some files at different sectors. CRC
            # equality is insufficient: compare the actual replicated bytes.
            _require(_digest(source, entry) == _digest(source, result[identity]),
                     "Conflicting duplicate ZDIR contents")
        else:
            result[identity] = entry
    return result


SHARED_NAMES = {path_hash(name): name for name in (
    "GLOBAL/GLOBALB.LZC", "GLOBAL/GLOBALB.BUN", "GLOBAL/ATTRIBUTES.BIN", "CARS/TEXTURES.BIN")}


def _executable(source, files, platform, selected):
    if platform == "pc":
        candidates = sorted(name for name in files if "/" not in name and name.endswith(".EXE"))
        if selected is not None:
            candidates = [selected]
        if not candidates:
            return None
        name = candidates[0]
    else:
        name = "DEFAULT.XEX"
        if name not in files:
            _require(not source.handle and "NFS/ZDIR.BIN" not in files,
                     "Choose a disc image or extracted archive containing default.xex")
            return None
    key = files[name]
    size = source.size(key)
    entry = (key, 0, size)
    info = {}
    if platform == "x360":
        _require(0 < size <= MAX_EXECUTABLE, "X360 executable is empty or too large")
        info = xex_info(_read(source, entry))
    # PC identity comes from shared assets. Community executables need not have
    # the catalog's size, version resources, architecture or even a PE header.
    return dict(info, size=size, sha256=_digest(source, entry))


def _file_status(source, logical, expected):
    entry = logical.get(expected["path_hash"])
    if entry is None:
        return "missing"
    if "archive" in expected:
        archive = expected["archive"]
        name = "NFS/00GLOBAL.BIN" if archive == 0 else f"NFS/{archive:02}CARS.BIN"
        _require(entry[0].removeprefix("FILES/") == name,
                 "Undercover input is in the wrong numbered archive")
    if entry[2] != expected["size"] or _digest(source, entry) != expected["sha256"]:
        return "unknown"
    return "importable"


def _shared_signature(rows, platform, game="carbon"):
    # Carbon PC needs three shared inputs, independent of whether a copy also
    # includes the catalog's GLOBALB.BUN companion. Later games require all four.
    optional = path_hash("GLOBAL/GLOBALB.BUN") if (platform, game) == ("pc", "carbon") else None
    return sorted((r["path_hash"], r["size"], r["sha256"]) for r in rows
                  if "car_hash" not in r and r["path_hash"] != optional)


def _match(source, files, logical, release, selected, releases):
    game = release.get("game", "carbon")
    label = GAME_NAMES[game]
    if release["platform"] == "pc":
        _require(any(n.startswith("CARS/") for n in files)
                 and any(n.startswith("GLOBAL/") for n in files),
                 "Choose the folder that contains CARS and GLOBAL")
    exe = _executable(source, files, release["platform"], selected)
    if release["platform"] == "x360":
        title = UNDERCOVER_TITLE if game == "undercover" else CARBON_TITLE
        _require(exe is None or exe["title_id"] == title,
                 f"default.xex is not Need for Speed {label} (title ID must be {title})")
    if game == "undercover":
        _require(exe is not None and "NFS/ZDIR.BIN" in files and "NFS/00GLOBAL.BIN" in files,
                 "Choose an Undercover disc image or extracted folder containing default.xex and NFS archives")
    shared, problems = [], []
    for expected in release["files"]:
        if "car_hash" in expected:
            continue
        status = _file_status(source, logical, expected)
        if status != "importable":
            if (release["platform"], game) == ("pc", "carbon") and expected["path_hash"] == path_hash("GLOBAL/GLOBALB.BUN"):
                continue
            name = SHARED_NAMES.get(expected["path_hash"], expected["path_hash"])
            problems.append(f"{name} ({'missing' if status == 'missing' else 'does not match a known file'})")
        else:
            shared.append(dict(expected))
    _require(not problems, label + " shared files did not match: " + ", ".join(problems))

    # Keep pairs together: geometry from one known release and textures from
    # another do not establish a known conversion input pair.
    pairs = {}
    for candidate in releases:
        if candidate["platform"] != release["platform"] or candidate.get("game", "carbon") != game:
            continue
        if _shared_signature(candidate["files"], release["platform"], game) != _shared_signature(
                shared, release["platform"], game):
            continue
        cars = {}
        for row in candidate["files"]:
            if "car_hash" in row:
                cars.setdefault(row["car_hash"], []).append(row)
        for car, rows in cars.items():
            pairs.setdefault(car, []).append(rows)
    cars = []
    for car, alternatives in sorted(pairs.items()):
        checked = [(rows, [_file_status(source, logical, row) for row in rows])
                   for rows in alternatives]
        rows, statuses = next(((rows, statuses) for rows, statuses in checked
                               if set(statuses) == {"importable"}), checked[0])
        status = ("importable" if set(statuses) == {"importable"} else
                  "missing" if "missing" in statuses else "unknown")
        records = []
        for row, state in zip(rows, statuses):
            record = {k: v for k, v in row.items() if k != "car_hash"}
            record["status"] = state
            records.append(record)
        cars.append({"car_hash": car, "status": status,
                     "files": sorted(records, key=lambda r: r["path_hash"])})
    return {"game": game, "platform": release["platform"], "edition": release["edition"],
            "fingerprints": {"executable": exe, "catalog_executable": dict(release["executable"])},
            "cars": cars, "shared": sorted(shared, key=lambda r: r["path_hash"])}


def _folder(path):
    """Accept an installed folder or one enclosing directory, as Setup does."""
    if not path.is_dir():
        return path
    children = list(path.iterdir())
    names = {p.name.upper() for p in children if p.is_dir()}
    if {"CARS", "GLOBAL"} <= names or "NFS" in names or any(
            p.name.upper() == "DEFAULT.XEX" for p in children):
        return path
    candidates = []
    for child in children:
        if child.is_dir() and not child.is_symlink():
            names = {p.name.upper() for p in child.iterdir() if p.is_dir()}
            if {"CARS", "GLOBAL"} <= names:
                candidates.append(child)
    _require(len(candidates) <= 1, "Choose the folder that contains CARS and GLOBAL")
    return candidates[0] if candidates else path


def recognise(path):
    """Inventory Carbon, ProStreet PC or Undercover X360 conversion inputs.

    Accept a PC folder or any selected .exe, X360 ISO, extracted archive or
    logical files. Shared fingerprints identify the platform; executable hashes
    are recorded only. XEX metadata must identify the game, with any version.
    Each catalog car has an importable, unknown or missing status. File records
    on unknown/missing cars describe expected fingerprints, with their status;
    only importable pairs have been verified for conversion.
    Edition labels describe the catalog sample, not the selected executable.
    """
    path = Path(path).expanduser()
    _require(not path.is_symlink(), "Source must not be a symbolic link")
    selected = None
    if path.is_file() and path.suffix.lower() == ".exe":
        selected, path = path.name.upper(), path.parent
    else:
        path = _folder(path)
    with MANIFEST.open(encoding="utf-8") as handle:
        catalog = json.load(handle)
    _require(catalog["schema"] == 1 and catalog["releases"], "Invalid car fingerprint catalog")
    with _source(path) as source:
        files = _physical(source)
        logical = _logical(source, files)
        errors = []
        for release in catalog["releases"]:
            if ((source.handle or "DEFAULT.XEX" in files or "NFS/ZDIR.BIN" in files)
                    and release["platform"] != "x360"):
                continue
            if selected is not None and release["platform"] != "pc":
                continue
            try:
                return _match(source, files, logical, release, selected, catalog["releases"])
            except SourceError as error:
                errors.append(str(error))
        raise SourceError("Car source not recognised: " + "; ".join(dict.fromkeys(errors)))
