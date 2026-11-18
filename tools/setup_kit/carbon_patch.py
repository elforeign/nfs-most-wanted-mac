"""Read Carbon's official RTPatch 7.00 MODIFY/history update without executing it.

Update.apply(source, destination) takes an extracted disc tree, including its
original NFSC.exe as an edition witness, and creates a separate tree. Executables
are never patched or copied: Setup supplies the compatible executable later.
MODIFY/history records require an existing source, so an absent data file is an
error, not an instruction to create one. An absent executable (nfsc_link.exe on
some discs) is reported along with the other explicitly skipped executables.

The bounded readers, decompressor and checksums are shared with rtpatch. Delta
instructions follow rtptool's src/apply.rs (MIT; see NOTICE). Memory is bounded
by the update and one source, delta and output file, independently of tree size.
"""
from dataclasses import dataclass
import os
from pathlib import Path
import stat
import struct
import uuid

from . import rtpatch as rtp

MAX_CODE = 16 << 20
MAX_STEPS = 8
MAX_ENTRIES = 20000
MAX_TABLE = 65536
CHUNK = 1 << 16
FIX = "Choose the official Carbon 1.4 update matching your original PC disc edition."


class PatchError(rtp.PatchError):
    def __init__(self, message):
        super().__init__(message, FIX)


class Cancelled(PatchError):
    pass


def _poll(cancel):
    if cancel and cancel():
        raise Cancelled("The Carbon update was cancelled.")


@dataclass(frozen=True)
class Fingerprint:
    size: int
    w1: int
    w2: int


@dataclass(frozen=True)
class Delta:
    source: Fingerprint
    target: Fingerprint
    offset: int
    length: int
    size: int                  # decompressed instruction bytes, not output bytes


@dataclass(frozen=True)
class Record:
    path: str
    kind: int
    steps: tuple


@dataclass(frozen=True)
class Result:
    written: tuple             # verified data records, including already-current ones
    copied: int                # other files copied unchanged
    skipped_executables: tuple # all .exe paths in the source or update
    absent_executables: tuple  # skipped .exe records with no source file


def fingerprint(data, cancel=None):
    """Chunked use of the shared rolling checksum, with cancellation between chunks.

    Concatenation rotates the preceding checksum by eight bits per appended byte
    then XORs the chunk checksum; no change to the MW checksum primitive is needed.
    """
    sums = [0, 0]
    view = memoryview(data)
    for offset in range(0, len(view), CHUNK):
        _poll(cancel)
        chunk = view[offset:offset + CHUNK]
        for i, bits in enumerate((31, 30)):
            shift = (8 * len(chunk)) % bits
            w = sums[i]
            sums[i] = (((w << shift) | (w >> (bits - shift))) & ((1 << bits) - 1)) ^ rtp.rolling(chunk, bits)
    return Fingerprint(len(data), *sums)


def apply_delta(source, code, size, cancel=None):
    """Apply a bounded RTPatch instruction stream to verified source bytes."""
    if not 0 <= size <= rtp.MAX_FILE or not 0 < len(code) <= MAX_CODE:
        raise PatchError("The Carbon delta exceeds the supported file bounds.")
    reader = rtp._Reader(code, 0)
    output = bytearray(size)
    source_view = memoryview(source)
    position = poke = 0
    gaps, templates = [], []

    def bound(offset, count, limit):
        if offset < 0 or count < 0 or offset + count > limit:
            raise PatchError("The Carbon delta contains an out-of-range instruction.")

    def gap(count):
        nonlocal position
        bound(position, count, size)
        if count:
            if len(gaps) >= MAX_TABLE:
                raise PatchError("The Carbon delta has too many literal gaps.")
            gaps.append((position, count))
            position += count

    def copy(offset, count):
        nonlocal position
        bound(offset, count, len(source))
        bound(position, count, size)
        for start in range(0, count, CHUNK):
            _poll(cancel)
            n = min(CHUNK, count - start)
            output[position:position + n] = source_view[offset + start:offset + start + n]
            position += n

    def fill(pattern, count):
        nonlocal position
        bound(position, count, size)
        block = pattern * (CHUNK // len(pattern))
        for start in range(0, count, CHUNK):
            _poll(cancel)
            n = min(CHUNK, count - start)
            output[position:position + n] = block[:n]
            position += n

    def edit(width, delta):
        bound(poke, width, size)
        old = int.from_bytes(output[poke:poke + width], "little")
        output[poke:poke + width] = ((old + delta) % (1 << (8 * width))).to_bytes(width, "little")

    def byte():
        return reader.take(1)[0]

    def count():
        n = reader.vli()
        # Each edit needs at least one offset byte; reject giant/negative loops.
        bound(reader.pos, n, len(code))
        return n

    try:
        if byte() != 2 or reader.vli() != 0:
            raise PatchError("The Carbon delta has an unsupported starting offset.")
        while reader.pos < len(code):
            _poll(cancel)
            op = byte()
            if op == 1:
                if reader.pos != len(code) or position != size or gaps:
                    raise PatchError("The Carbon delta ends with incomplete or trailing data.")
                return bytes(output)
            if op in (3, 4):
                if op == 4:
                    gap(reader.vli())
                copy(reader.vli(), reader.vli())
            elif op == 5:
                gap(size - position)
                for offset, n in gaps:
                    for start in range(0, n, CHUNK):
                        _poll(cancel)
                        data = reader.take(min(CHUNK, n - start))
                        output[offset + start:offset + start + len(data)] = data
                gaps.clear()
            elif op == 6:
                poke += reader.vli()
                edit(1, byte())
            elif op in (7, 14, 15, 16):
                poke = 0
                width = {7: 1, 14: 1, 15: 2, 16: 4}[op]
                delta = int.from_bytes(reader.take(width), "little")
                for _ in range(count()):
                    _poll(cancel)
                    poke += reader.vli()
                    edit(width, delta)
            elif op == 8:
                if len(templates) >= MAX_TABLE:
                    raise PatchError("The Carbon delta has too many copy templates.")
                template = (reader.vli(), reader.vli())
                bound(*template, len(source))
                templates.append(template)
            elif op in (9, 10):
                if op == 10:
                    gap(reader.vli())
                index = reader.vli()
                bound(index, 1, len(templates))
                copy(*templates[index])
            elif op in (11, 12):
                if op == 12:
                    gap(reader.vli())
                fill(b"\0", reader.vli())
            elif op == 13:
                poke = 0
                for _ in range(count()):
                    _poll(cancel)
                    poke += reader.vli()
                    edit(1, byte())
            elif 17 <= op <= 22:
                if op >= 20:
                    gap(reader.vli())
                width = (1, 2, 4)[(op - 17) % 3]
                fill(reader.take(width), reader.vli())
            else:
                raise PatchError(f"The Carbon delta has an unsupported opcode: {op}.")
        raise PatchError("The Carbon delta has no end instruction.")
    except rtp.PatchError as error:
        if isinstance(error, PatchError):
            raise
        raise PatchError(str(error)) from None


class Update:
    def __init__(self, path):
        self.path = Path(path)
        if not self.path.is_file() or not 0 < self.path.stat().st_size <= rtp.MAX_UPDATE:
            raise PatchError("The Carbon update is not a file of the expected size.")
        with self.path.open("rb") as handle:
            self.data = handle.read(rtp.MAX_UPDATE + 1)
        if len(self.data) > rtp.MAX_UPDATE:
            raise PatchError("The Carbon update is too large.")
        try:
            start = rtp.overlay(self.data)
            reader = rtp._Reader(self.data, start + 2)
            version, flags, extra, options = reader.u16(), reader.u16(), reader.u32(), reader.u16()
            if (version, flags, extra, options) != (0x2BC, 0x83E4, 0x10000, 0x38):
                raise PatchError("The Carbon update has an unsupported RTPatch header.")
            reader.take(18)
            declared = reader.u32()
            if not 0 < declared <= rtp.MAX_RECORDS or b"Need for Speed Carbon" not in self.data[start:start + 4096]:
                raise PatchError("The chosen update is not a supported Carbon update.")
            # The Windows registry expressions and directory table precede the
            # records. As in the MW reader, find the first readable record, then
            # require the entire chain, declared count and EOF to agree.
            for pos in range(reader.pos, min(len(self.data) - 1, reader.pos + rtp.HEADER_SCAN)):
                if self.data[pos:pos + 2] not in (b"\x06\x50", b"\x46\x44"):
                    continue
                try:
                    self._record(rtp._Reader(self.data, pos))
                except (ValueError, struct.error):
                    continue
                self.records = self._chain(rtp._Reader(self.data, pos), declared)
                if self.record("nfsc.exe") is None:
                    raise PatchError("The Carbon update has no executable edition record.")
                return
            raise PatchError("The Carbon update has no supported MODIFY/history records.")
        except (rtp.PatchError, struct.error) as error:
            raise PatchError(str(error)) from None

    @staticmethod
    def _sums(reader, size):
        if not 0 <= size <= rtp.MAX_FILE:
            raise PatchError("The Carbon update exceeds the supported file size.")
        data = reader.take(10)
        return Fingerprint(size, struct.unpack_from("<I", data, 2)[0] & 0x7FFFFFFF,
                           struct.unpack_from("<I", data, 6)[0] & 0x3FFFFFFF)

    def _entry(self, reader):
        entry = reader.take(24)
        result = self._sums(reader, struct.unpack_from("<I", entry, 16)[0])
        reader.take(8)
        rtp.safe_path(reader.lp())
        return result

    def _record(self, reader):
        header = reader.u16()
        if header not in (0x5006, 0x4446):
            raise PatchError(f"The Carbon update has an unsupported record: {header:#06x}.")
        options = reader.u16()
        if options not in (0x38, 0x1B8):
            raise PatchError("The Carbon update has unsupported record options.")
        name = rtp.safe_path(reader.lp())
        if options & 0xC0:
            reader.vli(), reader.vli()
        kind = header >> 12
        length, steps = (reader.u32(), reader.u16()) if kind == 5 else (None, 1)
        if not 0 < steps <= MAX_STEPS:
            raise PatchError("The Carbon update has too many history steps.")
        reader.take(10)
        end = reader.pos + length if length is not None else None
        previous, deltas = None, []
        for _ in range(steps):
            if kind == 5 and reader.u16() != 0x2C4:
                raise PatchError("The Carbon update has an unsupported history marker.")
            if reader.u16() != 9:
                raise PatchError("The Carbon update has unsupported delta flags.")
            sources = reader.vli() if previous is None else 1
            targets = reader.vli()
            size, packed = reader.u32(), reader.u32()
            if sources != 1 or targets != 1 or not 0 < size <= MAX_CODE or not 0 < packed <= rtp.MAX_UPDATE:
                raise PatchError("The Carbon update has an unsupported delta layout.")
            source = self._entry(reader) if previous is None else self._sums(reader, previous.size)
            if previous is not None and source != previous:
                raise PatchError("The Carbon update has a broken history checksum chain.")
            target = self._entry(reader)
            offset = reader.pos
            if reader.take(packed)[:2] != struct.pack(">H", rtp.DIFF_MAGIC):
                raise PatchError("The Carbon update has damaged compressed delta data.")
            deltas.append(Delta(source, target, offset, packed, size))
            previous = target
        if end is not None and reader.pos != end:
            raise PatchError("The Carbon update has an invalid history length.")
        return Record(name, kind, tuple(deltas))

    def _chain(self, reader, declared):
        records, seen = [], set()
        for _ in range(declared):
            record = self._record(reader)
            key = record.path.casefold()
            if key in seen:
                raise PatchError("The Carbon update names a file more than once.")
            seen.add(key)
            records.append(record)
        if reader.u16() != 0x1000 or reader.take(len(self.data) - reader.pos) != b"\x00\xa6\x03\x00DKNJ":
            raise PatchError("The Carbon update has an invalid record count or end marker.")
        return tuple(records)

    def record(self, name):
        return next((rec for rec in self.records if rec.path.casefold() == name.casefold()), None)

    @staticmethod
    def _select(record, current):
        if current == record.steps[-1].target:
            return len(record.steps)
        for index, step in enumerate(record.steps):
            if current == step.source:
                return index
        raise PatchError(f"The source size/checksums do not match this Carbon update: {record.path} (wrong edition or changed file).")

    def decode(self, record, source, cancel=None):
        """Return verified final bytes, selecting the original/intermediate/final history by content."""
        if Path(record.path).suffix.casefold() == ".exe":
            raise PatchError("Carbon executable records are skipped; supply the compatible executable separately.")
        index = self._select(record, fingerprint(source, cancel))
        for step in record.steps[index:]:
            _poll(cancel)
            try:
                code = rtp.decompress(self.data, step.offset, step.length, step.size, lambda _: _poll(cancel))
            except Cancelled:
                raise
            except (IndexError, ValueError) as error:
                raise PatchError(f"The Carbon delta is damaged ({record.path}: {error}).") from None
            if len(code) != step.size:
                raise PatchError(f"The Carbon delta is truncated: {record.path}.")
            source = apply_delta(source, code, step.target.size, cancel)
            if fingerprint(source, cancel) != step.target:
                raise PatchError(f"The Carbon output checksum does not match: {record.path}.")
        return source

    def apply(self, source, destination, progress=None, cancel=None):
        """Create destination exclusively; source must include the disc's NFSC.exe.

        progress(done, total) counts output bytes and cancel() requests cancellation.
        Each file is renamed atomically after verification. On failure completed
        files may remain in destination; the caller may discard that new tree.
        """
        source, destination = Path(source), Path(destination)
        src, dst = source.resolve(), destination.resolve()
        if source.is_symlink() or not source.is_dir() or src == dst or src in dst.parents or dst in src.parents:
            raise PatchError("The Carbon update needs separate source and destination trees.")
        if destination.exists() or destination.is_symlink():
            raise PatchError("The Carbon update destination must be a new tree.")
        files = _inventory(source, cancel)
        for record in self.records:
            path = files.get(record.path.casefold())
            if Path(record.path).suffix.casefold() == ".exe" and record.path.casefold() != "nfsc.exe":
                continue
            if path is None:
                raise PatchError(f"The Carbon MODIFY/history record needs a source file: {record.path}.")
            self._select(record, fingerprint(_read(path), cancel))
        total = sum(self.record(name).steps[-1].target.size if self.record(name) else path.stat().st_size
                    for name, path in files.items() if path.suffix.casefold() != ".exe")
        done = copied = 0
        written, skipped = [], {rec.path for rec in self.records if Path(rec.path).suffix.casefold() == ".exe"}
        absent = tuple(rec.path for rec in self.records if Path(rec.path).suffix.casefold() == ".exe"
                       and rec.path.casefold() not in files)
        _poll(cancel)
        destination.mkdir()
        for key, path in files.items():
            _poll(cancel)
            relative = path.relative_to(source)
            if path.suffix.casefold() == ".exe":
                if key not in {name.casefold() for name in skipped}:
                    skipped.add(str(relative).replace(os.sep, "\\"))
                continue
            record = self.record(key)
            data = self.decode(record, _read(path), cancel) if record else None
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.update-{uuid.uuid4().hex}")
            try:
                with temporary.open("xb") as handle:
                    if data is not None:
                        for offset in range(0, len(data), CHUNK):
                            _poll(cancel)
                            chunk = data[offset:offset + CHUNK]
                            handle.write(chunk)
                            done += len(chunk)
                            if progress:
                                progress(done, total)
                    else:
                        with path.open("rb") as original:
                            while chunk := original.read(CHUNK):
                                _poll(cancel)
                                handle.write(chunk)
                                done += len(chunk)
                                if progress:
                                    progress(done, total)
                _poll(cancel)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
            if record:
                written.append(record.path)
            else:
                copied += 1
        if progress:
            progress(done, total)
        return Result(tuple(written), copied, tuple(sorted(skipped, key=str.casefold)), absent)


def _read(path):
    with path.open("rb") as handle:
        data = handle.read(rtp.MAX_FILE + 1)
    if len(data) > rtp.MAX_FILE:
        raise PatchError(f"The Carbon source file is too large: {path.name}.")
    return data


def _inventory(root, cancel):
    files, seen = {}, set()

    def visit(folder, depth):
        if depth > 16:
            raise PatchError("The Carbon source tree is too deep.")
        with os.scandir(folder) as entries:
            for entry in entries:
                _poll(cancel)
                path = Path(entry.path)
                if "\\" in entry.name:
                    raise PatchError("The Carbon source contains an unsafe file name.")
                try:
                    rtp.safe_path(entry.name)
                except rtp.PatchError as error:
                    raise PatchError(str(error)) from None
                key = str(path.relative_to(root)).replace(os.sep, "\\").casefold()
                if key in seen or len(seen) >= MAX_ENTRIES:
                    raise PatchError("The Carbon source has duplicate names or too many entries.")
                seen.add(key)
                mode = entry.stat(follow_symlinks=False).st_mode
                if stat.S_ISDIR(mode):
                    visit(path, depth + 1)
                elif stat.S_ISREG(mode):
                    files[key] = path
                else:
                    raise PatchError("The Carbon source contains a link or special file.")
    visit(root, 0)
    return files
