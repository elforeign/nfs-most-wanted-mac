"""Prepare Carbon's Xbox 360 artwork and selected sound from the player's copy.

The artwork archives are: GLOBAL/GLOBALB.BUN (the 24 button and stick glyphs) and
GLOBAL/DYNTEX.BIN (the two 256x256 controller diagrams). The source is an
extracted folder or an ISO; logical files are found through the game's own file
table, never by fixed disc offsets. Everything is checked before a byte is kept:
the executable's title id, each archive's size and SHA-256, its chunk structure,
every glyph surface and both diagram entries. Setup also retains the four music
MPF/MUS pairs, the five improved GIN recordings, and the police/crew and UCAP
speech banks with their CSI/EVT/IDX companions. Other sound files stay PC.
Any failure leaves nothing behind;
without an optional source Carbon keeps its PC sound and keycaps.
"""
import hashlib
from pathlib import Path
import shutil
import struct
import tempfile

from . import car_sources, xdvdfs
from .x360 import X360Error, X360Result

TITLE = car_sources.CARBON_TITLE
FIX = "Choose your own complete Need for Speed Carbon Xbox 360 copy (folder or .iso), or continue without it."
# Audited retail archives: logical name -> (bytes, SHA-256). Other editions are refused until audited.
FILES = {
    "GLOBAL/GLOBALB.BUN": (26302100, "d8eddfd473df508e96556796bbee34e1fca7b4c185deb45c6edd88b442c29c8f"),
    "GLOBAL/DYNTEX.BIN": (12134656, "e0fc43d2a0ca35c7722252bfaf9088cc5cc555916e61a52cded2294a38dd4ada"),
    "SOUND/ENGINE/GIN/EN_ACL_11.GIN": (262423, "6dd5a92a2c706fedffa527356053b9f0074dda9257b0077ea0cdbb4d3c5b81bb"),
    "SOUND/ENGINE/GIN/EN_ACL_25.GIN": (378891, "018c9d5aac3d6c08d9cd4fcbbfa7ea2171e9c3b0a56331bb35f92c412ede768e"),
    "SOUND/ENGINE/GIN/EN_ACL_68.GIN": (353269, "5b4545479e5e5fb56c575d4253f0cfecf832a84380c9937f61caa21a2b0eb49a"),
    "SOUND/ENGINE/GIN/EN_DCL_11.GIN": (131925, "94685aed1437c58c9cc409b886966ba27fdcb2a82fd515865c80cb772c82a437"),
    "SOUND/ENGINE/GIN/EN_DCL_68.GIN": (309869, "ddaaab77c9835f3c51eb2f1b88812a29eaa3edc4e366d7454e52980313369516"),
    "SOUND/SPEECH/COPSPEECH.BIG": (433741824, "2dd1d9d6933a5f4217ea190039139d2a7759ea39f1a931c4c26081b6a3ecae60"),
    "SOUND/SPEECH/COPSPEECH.CSI": (6564, "fdfcac0d239181be51e2fd551adb061777dc39a90ab0b7048dbc065997945c70"),
    "SOUND/SPEECH/COPSPEECH.EVT": (25257, "d747def4de0b0262c6bb2dc5796df19f294e23b11d8161b2e9a7f78cc362e97e"),
    "SOUND/SPEECH/COPSPEECH.IDX": (89376, "0aecfb5269285b33f437720395216c199638b5ef9bfb782eb2c83d11d71885c4"),
    "SOUND/SPEECH/UCAPAUDIO.BIG": (4261888, "dc689d4ce2fe8c00cc3c3ca2d90132afef6477a44de123064a73a218562015d8"),
    "SOUND/SPEECH/UCAPAUDIO.CSI": (180, "7753939aa301c65009d474620beddb032770346825057903a38ad83a196c016c"),
    "SOUND/SPEECH/UCAPAUDIO.EVT": (555, "9b8df37af0233893fc96e19e32fd2d8c7b68a65529f0544d8772fb78f868a7cb"),
    "SOUND/SPEECH/UCAPAUDIO.IDX": (528, "51bf7992ad795c0e07a67af5876800528171205c5287aa6238a3fd6030f7afc1"),
    "SOUND/PFDATA/CB_MUS_0.MPF": (17168, "1e679d434129c4586b0891b241e0023247ee55be274d2c3a31ab1a9f5370f9cf"),
    "SOUND/PFDATA/CB_MUS_0.MUS": (445296128, "92e19ffb291a9bacf1ff5dd89931c9fb0d41ed23c465223f4382d682bdc44f78"),
    "SOUND/PFDATA/CB_MUS_1.MPF": (18100, "28756b7a5eb2b7d27fbf88dbe0916f066e59f94a1f16244a67c6f26dfc15718a"),
    "SOUND/PFDATA/CB_MUS_1.MUS": (41680384, "5161d6bb4aa7e2cad4afe94fa9dad83b25c0783bb9b474f6db6fb24f33f99739"),
    "SOUND/PFDATA/CB_MUS_2.MPF": (16320, "9d7e0d7d251e6c9f65ee90622eaad4ab6c7390ad6cd4527b33f461db0acb11fa"),
    "SOUND/PFDATA/CB_MUS_2.MUS": (52387328, "79b041c6f4e98c022779aab918b955518088c228551122e7ea49e39bdb9cebf7"),
    "SOUND/PFDATA/CB_MUS_3.MPF": (46400, "6673e8d3fc032d111b25cd2cf83d4d59f9341d64186694e4c94cc4e63348d120"),
    "SOUND/PFDATA/CB_MUS_3.MUS": (125959936, "7276c2b5da3fb4fd6a1c784348b05d37ad4b3ce751cfc67aed50e0f3e80de9e1"),
}
# The 24 prompt slots, in the runtime's order: slot name, then the surface it draws when that differs.
GLYPH_SLOTS = (("CROSS", None), ("TRIANGLE", None), ("CIRCLE", None), ("SQUARE", None), ("L1", None), ("R1", None),
               ("L2", None), ("R2", None), ("L3", "LEFT_ANALOG"), ("R3", None), ("START", None), ("SELECT", "BACK"),
               ("DPAD", None), ("DPAD_UP", None), ("DPAD_DOWN", None), ("DPAD_LEFT", None), ("DPAD_RIGHT", None),
               ("DPAD_LEFTRIGHT", None), ("LEFT_ANALOG", None), ("RIGHT_ANALOG", None), ("RIGHT_ANALOG_UP", None),
               ("RIGHT_ANALOG_DOWN", None), ("L1_LOADING", "L1"), ("R1_LOADING", "R1"))
GLYPHS = {alias or name for name, alias in GLYPH_SLOTS}
DIAGRAMS = (0xEC3C0443, 0xEC3C0444)
PACK, GLYPH_INFO, GLYPH_PLATFORM, TEXTURE_FORMAT = 0xB3300000, 0x33310004, 0x33310005, 0x186
DIAGRAM_TABLE = 0x33310003
CHUNK = 1 << 20


class CarbonArtError(X360Error):
    def __init__(self, msg):
        super().__init__(msg, FIX)


def _string_hash(name):
    value = 0xFFFFFFFF
    for byte in name.upper().encode("ascii"):
        value = (value * 33 + byte) & 0xFFFFFFFF
    return value


def _chunks(data, begin, end):
    """Sequential (id, payload offset, size) chunks; one that crosses its parent is damage."""
    position = begin
    while position < end:
        if position + 8 > end:
            raise CarbonArtError("The Carbon archive has a truncated chunk header.")
        kind, size = struct.unpack_from("<II", data, position)
        if size > end - position - 8:
            raise CarbonArtError("The Carbon archive has a chunk that crosses its parent.")
        yield kind, position + 8, size
        position += 8 + size


def glyph_surfaces(data, begin=0, end=None, depth=0, found=None):
    """The 32x32 colour surfaces named in GLOBALB's texture packs, by name."""
    found = {} if found is None else found
    end = len(data) if end is None else end
    if depth > 8:
        raise CarbonArtError("The Carbon archive nests too deeply.")
    for kind, start, size in _chunks(data, begin, end):
        if kind == PACK:
            parts = {}
            for sub, sub_start, sub_size in _chunks(data, start, start + size):
                if sub in (0xB3310000, 0xB3320000):
                    for tag, tag_start, tag_size in _chunks(data, sub_start, sub_start + sub_size):
                        parts[tag] = (tag_start, tag_size)
            info, platform = parts.get(GLYPH_INFO), parts.get(GLYPH_PLATFORM)
            pixels = parts.get(0x33320002) or parts.get(0x33320003)
            if not (info and platform and pixels):
                continue
            record, index = info[0], 0
            while record + 89 <= info[0] + info[1]:
                count = data[record + 88]
                length = (89 + count + 3) & ~3
                if not count or length > info[0] + info[1] - record or (index + 1) * 32 > platform[1]:
                    break
                name = bytes(data[record + 89:record + 89 + count]).split(b"\0")[0].decode("ascii", "replace")
                width, height = struct.unpack_from("<HH", data, record + 40)
                offset, stored = struct.unpack_from("<I", data, record + 20)[0], struct.unpack_from("<I", data, record + 36)[0]
                hash_ok = struct.unpack_from("<I", data, record + 12)[0] == _string_hash(name)
                texture_format = struct.unpack_from("<I", data, platform[0] + 32 * index + 20)[0]
                if (hash_ok and width == height == 32 and texture_format & 0x1FF == TEXTURE_FORMAT
                        and offset <= pixels[1] and stored <= pixels[1] - offset):
                    found.setdefault(name, (width, height))
                record, index = record + length, index + 1
        elif kind & 0x80000000:
            glyph_surfaces(data, start, start + size, depth + 1, found)
    return found


def diagram_entries(data, begin=0, end=None, depth=0):
    """Both diagram entries of DYNTEX's table (found in any nested chunk), each inside the file."""
    end = len(data) if end is None else end
    if depth > 8:
        raise CarbonArtError("The Carbon archive nests too deeply.")
    for kind, start, size in _chunks(data, begin, end):
        if kind == DIAGRAM_TABLE:
            if size % 24:
                raise CarbonArtError("The Carbon diagram table is damaged.")
            rows = {row[0]: row for row in struct.iter_unpack("<6I", data[start:start + size])}
            for identity in DIAGRAMS:
                row = rows.get(identity)
                if row is None or row[2] < 156 or row[3] < 156 or row[1] > len(data) or row[2] > len(data) - row[1]:
                    raise CarbonArtError("A Carbon controller diagram is missing or outside its archive.")
            return rows
        if kind & 0x80000000:
            try:
                return diagram_entries(data, start, start + size, depth + 1)
            except CarbonArtError as error:
                if "no diagram table" not in str(error):
                    raise
    raise CarbonArtError("The Carbon diagram archive has no diagram table.")


def validate_archives(globalb, dyntex):
    missing = sorted(GLYPHS - set(glyph_surfaces(globalb)))
    if missing:
        raise CarbonArtError("The Carbon glyph archive lacks: " + ", ".join(missing) + ".")
    diagram_entries(dyntex)


def _resolve(source):
    """The title id and the exact audited files from the game's own tables."""
    files = car_sources._physical(source)
    if "DEFAULT.XEX" not in files:
        raise CarbonArtError("The Carbon copy has no default.xex.")
    key = files["DEFAULT.XEX"]
    size = source.size(key)
    if not 0 < size <= car_sources.MAX_EXECUTABLE:
        raise CarbonArtError("The Carbon executable is empty or too large.")
    title = car_sources.xex_info(car_sources._read(source, (key, 0, size)))["title_id"]
    if title != TITLE:
        raise CarbonArtError(f"This is not Need for Speed Carbon for Xbox 360 (title {title}).")
    logical = car_sources._logical(source, files)
    entries = {}
    for name in FILES:
        # Speech is localized on disc; the prepared English set uses PC names.
        names = (name, name.replace(".", "_EN.", 1)) if name.startswith("SOUND/SPEECH/") else (name,)
        entry = None
        for source_name in names:
            entry = logical.get(car_sources.path_hash(source_name))
            # The car-source reader inventories only CARS/GLOBAL in extracted
            # folders. Admit only this importer's exact sound list here.
            if entry is None and "NFS/ZDIR.BIN" not in files and source_name in files:
                key = files[source_name]
                entry = (key, 0, source.size(key))
            if entry is not None:
                break
        if entry is None:
            raise CarbonArtError(f"The Carbon copy has no {name}.")
        if entry[2] != FILES[name][0]:
            raise CarbonArtError(f"{name} is not an audited Carbon archive ({entry[2]} bytes).")
        entries[name] = entry
    return entries


def _copy(source, entry, name, destination, progress, done, total):
    """Copy one archive, hashing the bytes written; a mismatch removes the copy."""
    digest = hashlib.sha256()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("xb") as output:
        for offset in range(0, entry[2], CHUNK):
            block = car_sources._read(source, entry, offset, min(CHUNK, entry[2] - offset))
            digest.update(block)
            output.write(block)
            if progress:
                progress(done + offset + len(block), total)
    if digest.hexdigest() != FILES[name][1]:
        raise CarbonArtError(f"{name} does not match the audited Carbon archive.")


def prepare(source, out, progress=None):
    """Write only audited artwork, music, five GINs and speech below prepared/files."""
    source, out = Path(source).resolve(), Path(out).resolve()
    if out == source or out.is_relative_to(source) or (source.is_dir() and source.is_relative_to(out)):
        raise CarbonArtError("The Carbon output must be separate from the game copy.")
    out.mkdir(parents=True, exist_ok=True)
    final = out / "prepared"
    if final.exists():
        raise CarbonArtError("The Carbon output already exists; choose a fresh Setup work folder.")
    stage = Path(tempfile.mkdtemp(prefix=".carbon-", dir=out))
    try:
        try:
            with car_sources._source(source) as opened:
                entries = _resolve(opened)
                total, done = sum(e[2] for e in entries.values()), 0
                for name, entry in entries.items():
                    _copy(opened, entry, name, stage / "files" / name, progress, done, total)
                    done += entry[2]
        except car_sources.SourceError as error:
            raise CarbonArtError(str(error)) from error
        except (xdvdfs.DiscError, struct.error, UnicodeError) as error:
            raise CarbonArtError(f"The Carbon copy is damaged: {error}") from error
        validate_archives(*[(stage / "files" / name).read_bytes()
                            for name in ("GLOBAL/GLOBALB.BUN", "GLOBAL/DYNTEX.BIN")])
        stage.rename(final)
        return X360Result({"x360_root": final / "files"}, ("xbox_art", "xbox_music", "xbox_gin", "xbox_speech"), ())
    except OSError as error:
        raise CarbonArtError(f"Could not read or write the Carbon artwork: {error}") from error
    finally:
        if stage.exists():
            shutil.rmtree(stage)
