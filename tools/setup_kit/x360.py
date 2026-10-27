"""Import a player's X360 copy transactionally; never read owner-only decomps.

Plan: inventory and bound the disc/folder and ZDIR first; derive hash names from
PC paths and the game's executables; extract only runtime/converter inputs;
convert into private staging; independently validate inventories, DDS metadata,
HUFF bytes and arrest references; publish with one directory rename.

Premise: roots.h consumes x360_root (raw files incl. NFS/ZZDATA0.BIN and
default.xex), assets_root/{hd_textures_mips_alpha,hd_textures_ui,nis_arrest},
and movies. Audio/music/police/NIS/reverb/vinyls use raw SOUND/GLOBAL/CARS;
world tables also need GLOBAL vaults, GLOBALB and both L2RA track bundles.
The other roots.h keys (pc_root, caches, tmp) belong to the backend.

Offline proof and owner equivalence commands live in tests/setup/test_assets.py.
"""
from dataclasses import dataclass
import csv
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile

from . import huff, xdvdfs

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / "tools_snapshot"
FIX = "Choose a complete copy of your own X360 disc, or select its .iso image."
FEATURES = ("audio", "music", "police", "nis", "reverb", "movies", "textures", "ui", "arrest", "vinyls", "world")
SCENES = ("CAREER_SAFEHOUSE", "PLATFORMCRIB", "CAR_LOT", "CUSTOMIZATION_SHOP",
          "CUSTOMIZATION_SHOP_BACKROOM", "NEXTGENSKY")
REQUIRED = (
    "GLOBAL/ATTRIBUTES.BIN", "GLOBAL/GAMEPLAY.BIN", "GLOBAL/FE_ATTRIB.BIN",
    "GLOBAL/GLOBALB.BUN", "GLOBAL/INGAMEMEMORYFILE.BIN",
    "TRACKS/STREAML2RA.BUN", "TRACKS/STREAML2RA_PERM.BUN",
    "SOUND/PFDATA/MW_MUSIC.MPF", "SOUND/PFDATA/MW_MUSIC.MUS",
    "SOUND/SPEECH/COPSPEECH_EN.BIG", "SOUND/STREAMS/NISAUDIO.BIG", "SOUND/STREAMS/NISAUDIO.IDX",
) + tuple(f"FRONTEND/PLATFORMS/{s}.LZC" for s in SCENES)
SELECTED_DIRS = {"GLOBAL", "TRACKS", "CARS", "SOUND", "NIS", "FRONTEND", "LANGUAGES"}
ARREST = re.compile(r"SCENE_ARREST[FM]\d+B?_BUNDLEB\.BUN$", re.I)
NAME_PATTERN = re.compile(r"[A-Za-z0-9_\\/%.\-]+\.[A-Za-z0-9]{2,4}".encode())
XEX_TITLE = "0x454107D9"
XEX_SHA256 = "e0002893223a1425e9cd760171921689007093b03ad3d597e156d4c15de09618"


class X360Error(Exception):
    def __init__(self, msg, fix=FIX):
        super().__init__(msg)
        self.msg, self.fix = msg, fix


@dataclass
class X360Result:
    roots: dict
    features: tuple
    missing: tuple
    names_found: int = 0
    entries: int = 0


def path_hash(path):
    value = 0xFFFFFFFF
    for c in path.upper().replace("/", "\\").encode("latin-1"):
        value = (value * 33 + c) & 0xFFFFFFFF
    return value


def _files(folder):
    """Case-insensitive physical inventory. Never follow links or special files."""
    result = {}
    def failed(error):
        raise error
    for dp, dirs, files in os.walk(folder, followlinks=False, onerror=failed):
        for name in dirs + files:
            p = Path(dp) / name
            if p.is_symlink() or (not p.is_file() and not p.is_dir()):
                raise X360Error(f"X360 folder contains a link or special file: {p}")
        for name in files:
            p = Path(dp) / name
            rel = xdvdfs.safe_path(p.relative_to(folder).as_posix()).upper()
            if rel in result or len(result) >= xdvdfs.MAX_FILES:
                raise X360Error("X360 folder has duplicate names or too many files")
            result[rel] = p
    return result


class Source:
    """A bounded inventory over either input form; copying always goes to staging."""
    def __init__(self, path):
        self.path, self.handle = Path(path), None
        if self.path.is_dir():
            self.files = _files(self.path)
        elif self.path.is_file() and self.path.suffix.lower() == ".iso":
            self.handle = self.path.open("rb")
            try:
                self.files = xdvdfs.inventory(self.handle)
            except BaseException:
                self.close()
                raise
        else:
            raise X360Error(f"X360 source is not a folder or .iso: {path}")

    def close(self):
        if self.handle:
            self.handle.close()

    def size(self, name):
        item = self.files[name]
        return item.size if self.handle else item.stat().st_size

    def read(self, name, limit=64 << 20):
        n = self.size(name)
        if not 0 < n <= limit:
            raise X360Error(f"Invalid X360 metadata size for {name}: {n}")
        if self.handle:
            return xdvdfs.read_exact(self.handle, self.files[name].offset, n)
        data = self.files[name].read_bytes()
        if len(data) != n:
            raise X360Error(f"X360 source changed while reading {name}")
        return data

    def copy(self, name, dest, offset=0, size=None):
        if size is None:
            size = self.size(name)
        if offset < 0 or size <= 0 or offset + size > self.size(name):
            raise X360Error(f"X360 archive range outside {name}: {offset}+{size}")
        if self.handle:
            xdvdfs.copy_range(self.handle, self.files[name].offset + offset, size, dest)
        else:
            with self.files[name].open("rb") as f:
                xdvdfs.copy_range(f, offset, size, dest)


def _run(command, work, env=None, timeout=3600):
    """Keep converter output in the transaction and failures bounded and readable."""
    log = work / (Path(str(command[0])).name + "-" + str(len(list(work.glob("*.log")))) + ".log")
    env = dict(os.environ if env is None else env, TMPDIR=str(work))
    with log.open("wb") as f:
        result = subprocess.run([str(c) for c in command], stdout=f, stderr=subprocess.STDOUT,
                                env=env, cwd=ROOT, timeout=timeout)
    if result.returncode:
        with log.open("rb") as f:
            f.seek(max(0, log.stat().st_size - 2000))
            tail = f.read().decode(errors="replace")
        raise X360Error(f"X360 conversion failed ({Path(str(command[1])).name if len(command)>1 else command[0]}): {tail}",
                        "Check that both game copies are complete and reinstall Setup if its tools are missing.")


def _game_names(xex, pc_root, work):
    """Decode the player's supported executable with the shipped, bounded XEX tool."""
    data = xex.read_bytes()
    if len(data) < 0x18 or data[:4] != b"XEX2":
        raise X360Error("X360 default.xex is not an XEX2 executable")
    sec, nopt = struct.unpack_from(">II", data, 0x10)
    if nopt > 64 or 0x18 + nopt * 8 > len(data) or sec + 8 > len(data):
        raise X360Error("X360 executable header is truncated")
    base = next((struct.unpack_from(">I", data, p + 4)[0] for p in range(0x18, 0x18 + nopt * 8, 8)
                 if struct.unpack_from(">I", data, p)[0] == 0x10201), None)
    size = struct.unpack_from(">I", data, sec + 4)[0]
    if base is None or not 0 < size <= 256 << 20:
        raise X360Error("X360 executable has an invalid image range")
    tool = work / "xex_read"
    _run(["clang", "-O2", "-w", SNAPSHOT / "xex_read.c", "-o", tool], work, timeout=120)
    image = work / "xex-image.bin"
    with image.open("wb") as output, (work / "xex-read.log").open("wb") as errors:
        result = subprocess.run([str(tool), str(xex), hex(base), str(size), XEX_TITLE, XEX_SHA256],
                                stdout=output, stderr=errors, timeout=120)
    if result.returncode or image.stat().st_size != size:
        raise X360Error("X360 executable is damaged or is not the supported NFS:MW USA retail build",
                        "Select the supported USA retail X360 disc, or continue without X360 assets.")
    for path in (pc_root / "speed.exe", image):
        if not path.is_file() or path.stat().st_size > 256 << 20:
            raise X360Error(f"Missing or oversized game executable: {path.name}")
        yield from (m.decode("latin-1") for m in NAME_PATTERN.findall(path.read_bytes()))


def _pc_paths(pc_root):
    return {p.relative_to(pc_root).as_posix().upper(): p for p in pc_root.rglob("*") if p.is_file()}


def _candidates(pc_paths, game_names):
    names = set(REQUIRED)
    names.update(pc_paths)
    # The X360 file-level compressed frontend scenes use LZC where PC uses BIN.
    names.update(p[:-4] + ".LZC" for p in pc_paths if p.endswith(".BIN"))
    for value in game_names:
        value = value.replace("\\\\", "\\").replace("\\", "/").upper()
        try:
            value = xdvdfs.safe_path(value)
        except xdvdfs.DiscError:
            continue  # executable strings include format strings and absolute build paths
        if value.split("/")[0] in SELECTED_DIRS:
            names.add(value)
    hashes = {}
    for name in sorted(names):
        h = path_hash(name)
        if h in hashes and hashes[h] != name:
            raise X360Error(f"Ambiguous X360 path hash: {hashes[h]}, {name}")
        hashes[h] = name
    return hashes


def _entries(data):
    if not data or len(data) % 24 or len(data) > 24 * 100000:
        raise X360Error("Invalid X360 ZDIR entry table")
    entries = {}
    for row in struct.iter_unpack("<6I", data):
        if row[1] != 0xFFFFFFFF and row[1] > 99:
            raise X360Error("Invalid or duplicate X360 ZDIR entry")
        if row[0] in entries and (entries[row[0]][4:] != row[4:]):
            raise X360Error("Conflicting duplicate X360 ZDIR entry")
        entries.setdefault(row[0], row)  # retail contains identical replicated files and zero-size stubs
    return entries


def _wanted(name):
    top = name.split("/")[0]
    if top == "SOUND":
        return name.endswith((".ABK", ".GIN", ".BIG", ".IDX", ".MPF", ".MUS", ".MXB"))
    if top == "CARS":
        return name.endswith(("/VINYLS.BIN", "/TEXTURES.BIN", "/PREVINYL.BIN"))
    if top == "NIS":
        return bool(ARREST.fullmatch(name.split("/")[-1]))
    if top == "TRACKS":
        return name in ("TRACKS/STREAML2RA.BUN", "TRACKS/STREAML2RA_PERM.BUN")
    return top in SELECTED_DIRS and name.endswith((".BIN", ".BUN", ".LZC"))


def _extract(source, stage, pc_root, work, progress):
    raw, movies = stage / "files", stage / "Movies"
    raw.mkdir()
    prefix = "FILES/" if "FILES/GLOBAL/GLOBALB.BUN" in source.files else ""
    for name, dest in (("DEFAULT.XEX", raw / "default.xex"), ("NFS/ZDIR.BIN", raw / "NFS/ZDIR.BIN"),
                       ("NFS/ZZDATA0.BIN", raw / "NFS/ZZDATA0.BIN")):
        key = name if name in source.files else prefix + name
        if key not in source.files:
            raise X360Error(f"X360 copy lacks {name}")
        source.copy(key, dest)
    pc_paths = _pc_paths(pc_root)
    candidates = _candidates(pc_paths, _game_names(raw / "default.xex", pc_root, work))
    entries = _entries((raw / "NFS/ZDIR.BIN").read_bytes())
    named = {candidates[h]: e for h, e in entries.items() if h in candidates}
    required = set(REQUIRED)
    required.update(p for p in pc_paths if p.startswith("NIS/") and ARREST.fullmatch(p.split("/")[-1]))
    required.update(p for p, physical in pc_paths.items() if p.startswith("CARS/") and p.endswith("/VINYLS.BIN")
                    and physical.stat().st_size > 0)  # traffic/police copies are zero-byte retail stubs
    absent = sorted(p for p in required if p not in named or named[p][1] == 0xFFFFFFFF or not named[p][4])
    if absent:
        raise X360Error("X360 ZDIR lacks required entries: " + ", ".join(absent[:8]))
    plan = []
    for name, row in sorted(named.items()):
        if not _wanted(name) or row[1] == 0xFFFFFFFF or not row[4]:
            continue
        _, archive, sector, _, size, _ = row
        archive_path = f"NFS/ZZDATA{archive}.BIN"
        if archive_path in source.files and sector * 2048 + size > source.size(archive_path):
            raise X360Error(f"X360 ZDIR range outside {archive_path}: {name}")
        direct = prefix + name
        if direct in source.files:
            if source.size(direct) != size:
                raise X360Error(f"X360 extracted file size differs from ZDIR: {name}")
            plan.append((name, direct, 0, size))
        else:
            if archive_path not in source.files:
                raise X360Error(f"X360 copy lacks {name} and {archive_path}")
            if sector * 2048 + size > source.size(archive_path):
                raise X360Error(f"X360 ZDIR range outside {archive_path}: {name}")
            plan.append((name, archive_path, sector * 2048, size))
    if not any(n.startswith("CARS/") for n, *_ in plan):
        raise X360Error("X360 copy has no named vinyl packs")
    if not any(n.startswith("NIS/") for n, *_ in plan):
        raise X360Error("X360 copy has no named arrest bundles")
    movie_names = sorted(k for k in source.files if k.startswith("MOVIES/") and k.endswith(".WMV"))
    if not movie_names:
        raise X360Error("X360 copy lacks Movies/*.wmv")
    for i, (name, key, off, size) in enumerate(plan, 1):
        source.copy(key, raw / name, off, size)
        if progress:
            progress(i, len(plan) + len(movie_names))
    for i, key in enumerate(movie_names, len(plan) + 1):
        source.copy(key, movies / key[len("MOVIES/"):])
        if progress:
            progress(i, len(plan) + len(movie_names))
    all_rows = list(struct.iter_unpack("<6I", (raw / "NFS/ZDIR.BIN").read_bytes()))
    coverage = {"names_found": sum(r[0] in candidates for r in all_rows), "entries": len(all_rows), "selected": len(plan),
                "required": sorted(required), "files": [n for n, *_ in plan],
                "sizes": {name: size for name, _, _, size in plan},
                "movie_files": {name[len("MOVIES/"):]: source.size(name) for name in movie_names}}
    (stage / "coverage.json").write_text(json.dumps(coverage, indent=2) + "\n")
    return coverage


# Progress over the whole import, in thousandths: copying the files out of the disc is fast and the converters and
# the independent validation are the long part (they were silent for about 6 minutes after "760/760", M4 F4).
# Each step's share is its measured time on an M-series Mac; the bar moves when a step finishes.
# Measured (168 s): extract 3, texture_compare 22, hd_textures 42, ui_textures 7, nis_arrest 34, cuffs 0, validate 60.
PROGRESS_STEPS = (("extract", 20), ("texture_compare", 130), ("hd_textures", 250), ("ui_textures", 40),
                  ("nis_arrest", 200), ("cuffs", 5), ("validate", 355))
PROGRESS_TOTAL = sum(w for _, w in PROGRESS_STEPS)


class _Progress:
    def __init__(self, callback):
        self.callback, self.done = callback, 0

    def part(self, step, i=1, n=1):
        """Report step (a PROGRESS_STEPS name) i/n of the way through."""
        if not self.callback:
            return
        start = 0
        for name, weight in PROGRESS_STEPS:
            if name == step:
                self.done = max(self.done, start + weight * min(i, n) // max(n, 1))
                self.callback(self.done, PROGRESS_TOTAL)
                return
            start += weight


def _convert(stage, pc_root, work, progress=None):
    report = (lambda step: progress.part(step)) if progress else (lambda step: None)
    raw, assets = stage / "files", stage / "assets"
    assets.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("NFS_", "BOOT_", "NFSMW_SETUP_HUFF"))}
    env.update(PYTHONDONTWRITEBYTECODE="1", TMPDIR=str(work), NFSMW_SETUP_HUFF_WORK=str(work),
               NFSMW_SETUP_JOBS=str(huff.job_count()))
    compare = stage / "texture_compare.tsv"
    def run(script, *args):
        _run([sys.executable, "-I", "-B", SNAPSHOT / script, *args], work, env)
    run("texture_compare.py", pc_root, raw, compare)
    report("texture_compare")
    run("x360_tex.py", raw, assets / "hd_textures_mips_alpha", "--larger", compare,
        "--mips", "--pc-alpha", pc_root, "--tone-match", pc_root, "--frontend-files")
    report("hd_textures")
    run("x360_tex.py", raw, assets / "hd_textures_ui", "--same-size-better", compare, "--mips")
    report("ui_textures")
    run("x360_nis_arrest.py", raw, pc_root, assets / "nis_arrest")
    report("nis_arrest")
    run("x360_cuffs.py", raw, pc_root, assets / "hd_textures_mips_alpha")
    report("cuffs")


def _dds(path, width, height, kind, mips):
    data = path.read_bytes()
    if len(data) < 128 or data[:4] != b"DDS " or struct.unpack_from("<I", data, 4)[0] != 124:
        raise X360Error(f"Invalid DDS header: {path.name}")
    h, w = struct.unpack_from("<II", data, 12)
    count = struct.unpack_from("<I", data, 28)[0]
    pfsize, flags = struct.unpack_from("<II", data, 76)
    fourcc = data[84:88]
    actual = fourcc.decode("ascii", errors="replace") if flags & 4 else "L8" if flags & 0x20000 else "A8R8G8B8"
    expected_count = max(w, h).bit_length() if mips else 1
    if (w, h, actual, pfsize, count) != (width, height, kind, 32, expected_count) or not 0 < w <= 4096 or not 0 < h <= 4096:
        raise X360Error(f"DDS metadata differs from inventory: {path.name}")
    header_flags, pitch = struct.unpack_from("<I", data, 8)[0], struct.unpack_from("<I", data, 20)[0]
    caps = struct.unpack_from("<I", data, 108)[0]
    bits, red, green, blue, alpha = struct.unpack_from("<5I", data, 88)
    if kind in ("DXT1", "DXT3", "DXT5"):
        pixel_ok = flags == 4 and not any((bits, red, green, blue, alpha))
        expected_pitch = ((w + 3) // 4) * ((h + 3) // 4) * (8 if kind == "DXT1" else 16)
        required_flag = 0x80000
    elif kind == "A8R8G8B8":
        pixel_ok = (flags, fourcc, bits, red, green, blue, alpha) == (0x41, bytes(4), 32, 0xFF0000, 0xFF00, 0xFF, 0xFF000000)
        expected_pitch, required_flag = w * 4, 8
    elif kind == "L8":
        pixel_ok = (flags, fourcc, bits, red, green, blue, alpha) == (0x20000, bytes(4), 8, 255, 0, 0, 0)
        expected_pitch, required_flag = w, 8
    else:
        raise X360Error(f"Unsupported DDS format: {kind}")
    required_flags = 0x1007 | required_flag | (0x20000 if count > 1 else 0)
    required_caps = 0x1000 | (0x400008 if count > 1 else 0)
    if not pixel_ok or pitch != expected_pitch or header_flags & required_flags != required_flags or caps & required_caps != required_caps:
        raise X360Error(f"Invalid DDS pixel format, pitch or mip flags: {path.name}")
    n = 128
    for _ in range(count):
        n += ((w + 3) // 4) * ((h + 3) // 4) * (8 if kind == "DXT1" else 16) if kind.startswith("DXT") else w * h * (1 if kind == "L8" else 4)
        w, h = max(1, w // 2), max(1, h // 2)
    if n != len(data):
        raise X360Error(f"DDS pixel bytes missing or trailing: {path.name} ({len(data)}/{n})")


def _rows(path):
    with path.open(newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def _texture_inventory(folder, expected, mips):
    rows = _rows(folder / "index.tsv")
    actual = {int(r["hash"], 16): r for r in rows}
    if len(actual) != len(rows) or actual.keys() != expected.keys() or not actual:
        missing = sorted(expected.keys() - actual.keys())
        extra = sorted(actual.keys() - expected.keys())
        raise X360Error(f"Converted texture inventory incomplete: {folder.name}; expected {len(expected)}, got {len(actual)}; "
                        f"missing {missing[:5]}, unexpected {extra[:5]}")
    declared = set()
    for key, r in actual.items():
        w, ht = int(r["width"]), int(r["height"])
        if (w, ht) != expected[key]:
            raise X360Error(f"Converted texture dimensions differ: {key:08x}")
        name = f'{key:08x}_{re.sub(r"[^A-Za-z0-9_]", "_", r["name"])}.dds'
        declared.add(name)
        _dds(folder / name, w, ht, r["format"], mips)
        tone = folder / "x360_tone" / name
        if tone.exists():
            _dds(tone, w, ht, r["format"], mips)
    if {p.name for p in folder.glob("*.dds")} != declared:
        raise X360Error(f"Undeclared or missing DDS files in {folder.name}")


def _verify_arrest(args):
    """"""
    sys.path.insert(0, str(SNAPSHOT))
    import x360_nis_arrest as arrest
    pc_path, raw_path, emitted = args
    expected, textures, n = arrest.convert_bundle(pc_path.read_bytes(), raw_path.read_bytes(), lambda _: None)
    if hashlib.sha256(expected).digest() != hashlib.sha256(emitted.read_bytes()).digest():
        raise X360Error(f"Arrest conversion differs from source meshes: NIS/{raw_path.name}")
    return textures, n


def _validate(stage, pc_root, work, coverage):
    # Inspect inputs independently of converter exit codes and their indexes.
    sys.path.insert(0, str(SNAPSHOT))
    import x360_tex as tex
    import x360_nis_arrest as arrest
    import texture_compare as compare
    from .huff import NativeHuff
    raw, assets = stage / "files", stage / "assets"
    for name in sorted(set(coverage["required"]) | set(coverage.get("files", []))) + ["default.xex", "NFS/ZDIR.BIN", "NFS/ZZDATA0.BIN"]:
        p = raw / name
        if not p.is_file() or p.stat().st_size == 0:
            raise X360Error(f"X360 output lacks required file: {name}")
        if name in coverage.get("sizes", {}) and p.stat().st_size != coverage["sizes"][name]:
            raise X360Error(f"X360 extracted size differs from inventory: {name}")
    for name, size in coverage.get("movie_files", {}).items():
        p = stage / "Movies" / name
        if not p.is_file() or p.stat().st_size != size:
            raise X360Error(f"X360 movie inventory incomplete: {name}")
    # Re-read selected texture inputs with a fresh native decoder; scan checks
    # every matching HUFF header, including those converters used to skip.
    native = NativeHuff(work)
    previous = tex.huff, tex.jdlz, compare.huff, compare.jdlz
    try:
        tex.huff = compare.huff = native
        tex.jdlz = compare.jdlz = native.jdlz
        wanted_hd, wanted_ui, xbox = _expected_textures(stage, tex, pc_root)
        if native.blocks == 0 or native.bytes == 0:
            raise X360Error("X360 validation decoded no HUFF bytes")
        _texture_inventory(assets / "hd_textures_mips_alpha", wanted_hd, True)
        _texture_inventory(assets / "hd_textures_ui", wanted_ui, True)
        pc_paths = _pc_paths(pc_root)
        bundles = sorted(p for p in pc_paths if p.startswith("NIS/") and ARREST.fullmatch(p.split("/")[-1]))
        output = assets / "nis_arrest"
        if {p.name.upper() for p in output.glob("*.bun")} != {p.split("/")[-1] for p in bundles}:
            raise X360Error("Arrest bundle inventory incomplete")
        refs = _rows(output / "textures.tsv")
        aliases = {int(r["alias"], 16) for r in refs}
        emitted = {p.name.upper(): p for p in output.glob("*.bun")}
        used, swaps = set(), 0
        jobs = [(pc_paths[name], raw / name, emitted[name.split("/")[-1]]) for name in bundles]
        for textures, n in huff.ordered_map(_verify_arrest, jobs):
            used.update(arrest.alias(t) for t in textures)
            swaps += n
        if len(aliases) != len(refs) or not aliases or aliases != used or not swaps:
            raise X360Error("Arrest texture references are incomplete or unused")
        for r in refs:
            key = int(r["hash"], 16)
            if (int(r["alias"], 16) != arrest.alias(key) or
                    xbox.get((key, r["file"].upper())) != (int(r["width"]), int(r["height"]))):
                raise X360Error("Arrest reference names a missing X360 texture")
        (stage / "validation.json").write_text(json.dumps({"huff_blocks": native.blocks, "huff_bytes": native.bytes,
            "hd_textures": len(wanted_hd), "ui_textures": len(wanted_ui), "arrest_bundles": len(bundles),
            "arrest_textures": len(refs)}, indent=2) + "\n")
    finally:
        tex.huff, tex.jdlz, compare.huff, compare.jdlz = previous
        native.close()


def _expected_textures(stage, tex, pc_root):
    # Converter selection is described by the independent comparison inventory;
    # decode the input again so a zero-exit converter cannot redefine that set.
    import texture_compare as compare
    def scan(folder):
        return {k: v for k, v in compare.scan(str(folder), tex.huff).items() if not isinstance(k, tuple)}
    pc, xb = scan(pc_root), scan(stage / "files")
    comparison = []
    for key in sorted(pc.keys() & xb.keys()):
        a, b = pc[key], xb[key]
        comparison.append(dict(hash=f"{key:08x}", name=a[0], pc_w=str(a[1]), pc_h=str(a[2]), pc_fmt=a[3],
            x360_w=str(b[1]), x360_h=str(b[2]), x360_fmt=b[3], x360_file=b[4]))
    larger, better = set(), set()
    glyphs = {"L1", "R1", "L2", "R2", "L3", "R3", "CROSS", "CIRCLE", "SQUARE", "TRIANGLE", "START", "SELECT",
              "BACK", "A", "B", "X", "Y", "LT", "RT", "LB", "RB", "BUTTON_RING", "DPAD", "DPAD_UP", "DPAD_DOWN",
              "DPAD_LEFT", "DPAD_RIGHT", "DPAD_LEFTRIGHT", "DPAD_UPDOWN", "LEFT_ANALOG", "RIGHT_ANALOG"}
    for r in comparison:
        if not r["pc_w"] or not r["x360_w"]:
            continue
        key = int(r["hash"], 16)
        if int(r["x360_w"]) * int(r["x360_h"]) > int(r["pc_w"]) * int(r["pc_h"]):
            larger.add(key)
        if ((r["pc_w"], r["pc_h"]) == (r["x360_w"], r["x360_h"]) and
            r["x360_file"].upper().startswith(("GLOBAL/", "FRONTEND/", "LANGUAGES/")) and
            r["name"].upper() not in glyphs and not r["name"].upper().startswith(("PC_KEY_", "DPAD_", "LEFT_ANALOG", "RIGHT_ANALOG")) and
            ((r["x360_fmt"] in ("A8R8G8B8", "LIN_A8R8G8B8") and r["pc_fmt"] in ("DXT1", "DXT3", "DXT5", "P8")) or
             (r["x360_fmt"] == "DXT5" and r["pc_fmt"] == "DXT3"))):
            better.add(key)
    hd, ui, xbox, best = {}, {}, {}, {}
    pc_sizes = {int(r["hash"], 16): (int(r["pc_w"]), int(r["pc_h"])) for r in comparison if r["pc_w"]}
    ui_sizes = dict(pc_sizes)  # a hash can have a different PC size in a frontend scene
    platforms = stage / "files/FRONTEND/PLATFORMS"
    if platforms.is_dir():
        sources = [(f"FRONTEND/PLATFORMS/{scene}.LZC", (platforms / (scene + ".LZC")).read_bytes()) for scene in SCENES]
        for scene in tex.frontend_inventory(sources, str(pc_root)).values():
            for key, (pc_texture, _) in scene.items():
                larger.add(key)
                pc_sizes[key] = pc_texture[2:4]
    for p in sorted((stage / "files").rglob("*")):
        rel = p.relative_to(stage / "files").as_posix().upper()
        if not p.is_file() or rel.startswith(("NFS/", "SOUND/")) or p.name.upper() == "VINYLS.BIN" or p.suffix.upper() not in (".BIN", ".BUN", ".LZC"):
            continue
        for name, key, w, ht, fmt, pix in tex.textures_in(tex.unpack_file(p.read_bytes())):
            xbox[key, rel] = (w, ht)
            if name == "CSHOP_TOOLBOX_NORMAL":
                continue
            if key in larger:
                pw, ph = pc_sizes[key]
                fx, fy = w // pw, ht // ph
                covers = fx and fy and w == fx * pw and ht == fy * ph and not fx & (fx - 1) and not fy & (fy - 1)
                score = (1 if covers else 0, w * ht if covers else 0, 1 if fx == fy else 0, w * ht)
                if key not in best or score > best[key]:
                    hd[key], best[key] = (w, ht), score
            if key in better and (w, ht) == ui_sizes[key] and rel.startswith(("GLOBAL/", "FRONTEND/", "LANGUAGES/")):
                ui[key] = (w, ht)
    return hd, ui, xbox


def prepare(source: Path, out: Path, pc_root: Path, progress=None) -> X360Result:
    stage = src = None
    bytecode = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True  # late converter imports must also write only under out
        source, out, pc_root = Path(source).resolve(), Path(out).resolve(), Path(pc_root).resolve()
        if out == source or (source.is_dir() and out.is_relative_to(source)) or out == pc_root or out.is_relative_to(pc_root):
            raise X360Error("X360 output must be separate from both game copies", "Choose a separate Setup work folder.")
        out.mkdir(parents=True, exist_ok=True)
        final = out / "prepared"
        if final.exists():
            raise X360Error("X360 output already exists", "Choose a fresh Setup work folder; keep the previous prepared copy.")
        stage = Path(tempfile.mkdtemp(prefix=".x360-", dir=out))
        work = stage / ".work"
        work.mkdir()
        src = Source(source)
        steps = _Progress(progress)
        coverage = _extract(src, stage, pc_root, work, lambda i, n: steps.part("extract", i, n))
        src.close()
        src = None
        _convert(stage, pc_root, work, steps)
        _validate(stage, pc_root, work, coverage)
        steps.part("validate")
        shutil.rmtree(work)
        stage.rename(final)
        return X360Result({"x360_root": final / "files", "assets_root": final / "assets", "movies": final / "Movies"},
                          FEATURES, (), coverage["names_found"], coverage["entries"])
    except X360Error:
        raise
    except OSError as error:
        if error.errno == errno.ENOSPC:
            raise X360Error("There is not enough space to prepare the X360 assets.",
                            "Free space on the Setup work drive and try again.") from error
        raise X360Error(f"Could not read or write X360 assets: {error}",
                        "Choose a readable game copy and a writable Setup work folder.") from error
    except Exception as error:
        raise X360Error(f"Could not prepare X360 assets: {error}") from error
    finally:
        sys.dont_write_bytecode = bytecode
        if src:
            src.close()
        if stage is not None and stage.exists():
            shutil.rmtree(stage)
