"""The PC game's own 1.3 update, applied to Setup's copy of the game without running it.

The update (nfsmwpatch1.3.exe) is a Windows program with an RTPatch 7.00 patch file appended after its PE sections.
That patch replaces seven files whole: speed.exe, server.dll, FRONTEND\\FrontB.lzc and GLOBAL\\gameplay.bin,
gameplay.lzc, InGameB.bun and InGameB.lzc. Each record holds the file's Windows path, its size, two rolling checksums
and the file compressed with RTPatch's adaptive Huffman + LZSS coder. Nothing is executed, and every file is checked
against its record's size and both checksums before it is written.

The decoder is a port of rtptool's (https://github.com/bwrsandman/rtptool, src/codec.rs; MIT license, see NOTICE),
which reproduces the patcher's own model (patchw32.dll) including the layout of its tables in memory. rtptool reads
the 2.09 container; the 7.00 records read here were checked against the 1.3 update (the server.dll it carries is the
one recomp/server_module.py pins). Records that patch an existing file instead of replacing it are refused.
"""
from dataclasses import dataclass
import os
from pathlib import Path
import struct
import uuid

MAGIC = b"K*"
DIFF_MAGIC = 0xB59C
MAX_UPDATE = 64 << 20          # the 1.3 update is 12.8 MB
MAX_FILE = 64 << 20            # its largest file, speed.exe, is 7.3 MB
MAX_RECORDS = 256
HEADER_SCAN = 64 << 10         # where the records may start after the fixed header (see Update.__init__)
REPLACE, EOF = 2, 1            # record kinds (rec_hdr bits 15-12)
FILES = ("FRONTEND\\FrontB.lzc", "GLOBAL\\gameplay.bin", "GLOBAL\\gameplay.lzc", "GLOBAL\\InGameB.bun",
         "GLOBAL\\InGameB.lzc", "server.dll", "speed.exe")   # what the 1.3 update replaces, each once
FIX = "Choose the official 1.3 update of the PC game (nfsmwpatch1.3.exe), as downloaded; Setup only reads it."


class PatchError(ValueError):
    def __init__(self, msg, fix=FIX):
        super().__init__(msg)
        self.fix = fix


@dataclass(frozen=True)
class Record:
    path: str       # Windows path relative to the game folder, e.g. GLOBAL\gameplay.bin
    size: int
    w1: int         # rolling checksums of the file (31 and 30 bits)
    w2: int
    offset: int     # the compressed file in the update
    length: int


def rolling(data, bits):
    """RTPatch's file checksum: w = rotl8(w ^ byte) within `bits` bits."""
    mask, shift, w = (1 << bits) - 1, bits - 8, 0
    for c in data:
        x = w ^ c
        w = ((x << 8) | (x >> shift)) & mask
    return w


class _Reader:
    def __init__(self, data, pos):
        self.data, self.pos = data, pos

    def take(self, n):
        if n < 0 or self.pos + n > len(self.data):
            raise PatchError("The update is truncated.")
        self.pos += n
        return self.data[self.pos - n:self.pos]

    def u16(self):
        return struct.unpack("<H", self.take(2))[0]

    def u32(self):
        return struct.unpack("<I", self.take(4))[0]

    def lp(self):
        """Length-prefixed, NUL-terminated string; the length includes the NUL and 0xFF escapes to a u16."""
        n = self.take(1)[0]
        if n == 0xFF:
            n = self.u16()
        return self.take(n).rstrip(b"\0").decode("latin-1")

    def vli(self):
        """Lead byte: bit 7 sign, then a run of 1 bits from bit 6 counting the little-endian bytes that follow."""
        b = self.take(1)[0]
        count, mask = 0, 0x40
        while mask and b & mask:
            count, mask = count + 1, mask >> 1
        if count == 0:
            value = b & 0x3F
        else:
            value = ((b & ((0x40 >> count) - 1)) << (8 * count)) | int.from_bytes(self.take(count), "little")
        return -value if b & 0x80 else value


def safe_path(path):
    parts = path.split("\\")
    if (not 0 < len(parts) <= 4 or any(p in ("", ".", "..") or len(p) > 255 for p in parts)
            or any(c in path for c in "/:") or any(ord(c) < 32 or ord(c) == 0x7F for c in path)):
        raise PatchError(f"The update names an unsafe file: {path!r}")
    return path


def overlay(data):
    """Where the patch file starts: after the last PE section of the update program (or at 0 for a bare patch)."""
    if data[:2] == MAGIC:
        return 0
    try:
        if data[:2] != b"MZ":
            raise ValueError
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        if data[pe:pe + 4] != b"PE\0\0":
            raise ValueError
        count, optional = struct.unpack_from("<H", data, pe + 6)[0], struct.unpack_from("<H", data, pe + 20)[0]
        table = pe + 24 + optional
        end = max([0] + [sum(struct.unpack_from("<II", data, table + 40 * i + 16)) for i in range(count)])
    except (ValueError, struct.error):
        raise PatchError("The chosen file is not the 1.3 update program.") from None
    if data[end:end + 2] != MAGIC:
        raise PatchError("The chosen program does not carry an RTPatch update.")
    return end


class Update:
    """The records of an RTPatch 7.00 update that replaces files whole."""
    def __init__(self, path):
        path = Path(path)
        if not path.is_file() or not 0 < path.stat().st_size <= MAX_UPDATE:
            raise PatchError("The chosen update is not a file of the expected size.")
        self.path, self.data = path, path.read_bytes()
        start = overlay(self.data)
        r = _Reader(self.data, start + 2)
        version, flags = r.u16(), r.u16()
        extra = (r.u32() >> 16) & 1 if flags & 0x8000 else 0
        self.options = r.u16()
        r.take(18)  # payload size, reserved, attributes, command flags, reserved (the 2.09 fields)
        if b"Need for Speed Most Wanted" not in self.data[start:start + 4096]:
            raise PatchError("The chosen update is not one for Need for Speed: Most Wanted.")
        self.version, self.extra = version, extra
        # 7.00 follows the fixed fields with the registry key and value the Windows patcher reads to find the game,
        # then the folder table; neither is needed here. The records start right after them, at the first offset
        # where a record reads (a whole-file record whose compressed file starts with its magic). From there every
        # record must read and be safe, up to the EOF record, and replace exactly the update's files.
        for pos in range(r.pos, min(len(self.data), r.pos + HEADER_SCAN)):
            try:
                self._record(_Reader(self.data, pos))
            except (PatchError, ValueError, struct.error, UnicodeDecodeError):
                continue
            self.records = self._chain(_Reader(self.data, pos))
            return
        raise PatchError("Setup can't read this update: it is damaged, or not the 1.3 update of the PC game.")

    def _record(self, r):
        """One whole-file record at r (ValueError when none reads there); its name is not checked yet."""
        rec = r.u16()
        if rec >> 12 != REPLACE or not rec & 0x04:
            raise ValueError("not a whole-file record")
        options = r.u16() if rec & 0x02 else self.options
        name = r.lp()
        if options & 0xC0:
            r.vli()
            if self.extra:
                r.vli()
        if rec & 0x80:
            r.vli()
        if rec & 0x100:
            r.u16()
        if rec & 0x200:
            r.lp(), r.lp()
        r.take(10)
        if r.vli() != 1:
            raise ValueError("not one file version")
        size, length = r.u32(), r.u32()
        entry, sums = r.take(24), r.take(10)
        if self.extra:
            r.take(8), r.lp()
        offset = r.pos
        if (struct.unpack_from("<I", entry, 16)[0] != size or not 0 < size <= MAX_FILE
                or struct.unpack_from(">H", self.data, offset)[0] != DIFF_MAGIC):
            raise ValueError("no compressed file")
        r.take(length)
        return Record(name, size, struct.unpack_from("<I", sums, 2)[0] & 0x7FFFFFFF,
                      struct.unpack_from("<I", sums, 6)[0] & 0x3FFFFFFF, offset, length)

    def _chain(self, r):
        records = []
        try:
            while len(records) < MAX_RECORDS and r.u16() >> 12 != EOF:
                r.pos -= 2
                record = self._record(r)
                safe_path(record.path)
                records.append(record)
        except (ValueError, struct.error, UnicodeDecodeError) as e:
            raise PatchError(f"Setup can't read this update: it is damaged ({e}), or not the 1.3 update of the PC game.") from None
        if (len(self.data) - r.pos > 16 or
                sorted(record.path.casefold() for record in records) != sorted(f.casefold() for f in FILES)):
            raise PatchError("Setup can't read this update: it is damaged, or not the 1.3 update of the PC game.")
        return records

    def record(self, name):
        for rec in self.records:
            if rec.path.casefold() == name.casefold():
                return rec
        return None

    def decode(self, rec, progress=None):
        try:
            data = decompress(self.data, rec.offset, rec.length, rec.size, progress)
        except (IndexError, ValueError) as e:
            raise PatchError(f"The update is damaged ({rec.path}: {e}).") from None
        if len(data) != rec.size or rolling(data, 31) != rec.w1 or rolling(data, 30) != rec.w2:
            raise PatchError(f"The update is damaged: {rec.path} does not match its checksum.")
        return data

    def apply(self, root, skip=(), progress=None):
        """Write every file of the update into the game folder root (Windows names: an existing file of any case is
        replaced in place, folders must exist). Returns the paths written."""
        todo = [rec for rec in self.records if rec.path.casefold() not in {s.casefold() for s in skip}]
        total, done, written = sum(rec.size for rec in todo), 0, []
        for rec in todo:
            target = Path(root)
            *folders, name = rec.path.split("\\")
            for part in folders:
                matches = [p for p in target.iterdir() if p.name.casefold() == part.casefold()]
                if len(matches) != 1 or matches[0].is_symlink() or not matches[0].is_dir():
                    raise PatchError(f"The game has no single {part} folder for the update's {rec.path}.",
                                     "Make the disc images again from the original discs.")
                target = matches[0]
            existing = [p for p in target.iterdir() if p.name.casefold() == name.casefold()]
            if len(existing) > 1 or existing and (existing[0].is_symlink() or not existing[0].is_file()):
                raise PatchError(f"The game has {rec.path} more than once, or not as a file.")
            target = existing[0] if existing else target / name
            data = self.decode(rec, progress and (lambda n, base=done: progress(base + n, total)))
            temporary = target.with_name(f".{target.name}.update-{uuid.uuid4().hex}")
            try:
                temporary.write_bytes(data)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
            done += rec.size
            written.append(rec.path)
        return written


# The decoder (rtptool src/codec.rs). Each adaptive Huffman alphabet lives in one flat little-endian buffer laid out
# like patchw32.dll's: header words, a group count per code length, a slot table per length, symbol slots that point
# into the weight table, and the limits used for canonical decoding.

class _Bits:
    """MSB-first bit reader; bl is the number of bits still unread in buf[pos]."""
    def __init__(self, buf, pos, end):
        self.buf, self.pos, self.end, self.bl = buf, pos, end, 8

    def bit(self):
        if self.pos >= self.end:
            raise ValueError("compressed data truncated")
        v = (self.buf[self.pos] >> (self.bl - 1)) & 1
        self.bl -= 1
        if self.bl == 0:
            self.pos += 1
            self.bl = 8
        return v

    def bits(self, n):
        v = 0
        while n:
            if self.pos >= self.end:
                raise ValueError("compressed data truncated")
            take = min(n, self.bl)
            self.bl -= take
            v = (v << take) | ((self.buf[self.pos] >> self.bl) & ((1 << take) - 1))
            n -= take
            if self.bl == 0:
                self.pos += 1
                self.bl = 8
        return v


class _Tree:
    def __init__(self, esc_bits, levels, init_period, upd_period):
        alpha = 1 << esc_bits
        groups, symtab = 0x34, levels * 2 + 0x34
        slot = levels * 6 + 0x38
        weight = levels * 6 + 0x40 + alpha * 4
        limit = weight + 4 + alpha * 2
        self.m = bytearray(limit + 0xC0 + 0x100)
        w16, w32 = self.w16, self.w32
        for off in (0x32, 0x02, 0x00):
            w16(off, init_period)
        for off in (0x30, 0x2E, 0x2C):
            w16(off, upd_period)
        w16(0x06, esc_bits)
        w16(0x04, levels)
        w32(0x0C, groups)
        w32(0x20, symtab)
        w32(0x1C, slot)
        w32(0x18, weight)
        w32(0x14, limit)
        w32(0x10, limit)
        w16(0x0A, 1)
        w16(0x08, 1)
        w16(groups, 2)
        w32(symtab, slot)
        for i in range(1, levels + 1):
            w32(symtab + i * 4, slot + 8)
        base = weight + alpha * 2
        w32(slot, base)
        for i in range(1, alpha + 2):
            w32(slot + i * 4, base + 2)
        for j in range(alpha):
            w16(weight + j * 2, 0x8000)
        w16(0x24, alpha)
        self.build_limits(0)

    def r16u(self, o):
        return self.m[o] | (self.m[o + 1] << 8)

    def r16s(self, o):
        v = self.m[o] | (self.m[o + 1] << 8)
        return v - 0x10000 if v & 0x8000 else v

    def w16(self, o, v):
        v &= 0xFFFF
        self.m[o] = v & 0xFF
        self.m[o + 1] = v >> 8

    def r32(self, o):
        m = self.m
        return m[o] | (m[o + 1] << 8) | (m[o + 2] << 16) | (m[o + 3] << 24)

    def w32(self, o, v):
        self.m[o:o + 4] = (v & 0xFFFFFFFF).to_bytes(4, "little")

    def build_limits(self, start):
        gc, lim, levels = self.r32(0x0C), self.r32(0x10), self.r16u(0x04)
        s3 = 2 if start == 0 else self.r16s(lim + (start - 1) * 8) * 2
        for level in range(start, levels):
            s4 = s3 - self.r16s(gc + level * 2)
            self.w16(lim + level * 8, s4)
            s3 = s4 * 2
            self.w16(lim + level * 8 + 2, s3)
            self.w16(lim + level * 8 + 4, s4 * 4)
            self.w16(lim + level * 8 + 6, s4 * 16)

    def add_symbol(self, symbol):
        """Registers a new symbol; returns the lowest code length whose limits changed."""
        w, slot, gc, st = self.r32(0x18), self.r32(0x1C), self.r32(0x0C), self.r32(0x20)
        self.w16(w + symbol * 2, 1)
        n_slots = self.r16u(0x08)
        self.w32(slot + n_slots * 4, w + symbol * 2)
        n_slots += 1
        self.w16(0x08, n_slots)
        if n_slots == 2:
            return 0
        n_groups, levels = self.r16u(0x0A), self.r16u(0x04)
        if n_groups < levels:
            u = (n_groups - 1) & 0xFFFF
            self.w16(0x0A, n_groups + 1)
        else:
            u = (n_groups - 2) & 0xFFFF
            while self.r16s(gc + u * 2) == 0:
                u = (u - 1) & 0xFFFF
        self.w16(gc + u * 2, self.r16s(gc + u * 2) - 1)
        self.w16(gc + 2 + u * 2, self.r16s(gc + 2 + u * 2) + 2)
        self.w32(st + (u + 1) * 4, self.r32(st + (u + 1) * 4) - 4)
        for k in range(u + 2, levels + 1):
            self.w32(st + k * 4, self.r32(st + k * 4) + 4)
        return u

    def rebuild(self):
        """Periodic re-partition: optionally halve the weights, sort the slots by weight bit by bit, then move
        symbols between code lengths; the update period adapts to how much moved."""
        slot, symt, gc = self.r32(0x1C), self.r32(0x20), self.r32(0x0C)
        n_slots, upd, levels = self.r16u(0x08), self.r16s(0x2C), self.r16u(0x04)
        self.w16(0x2C, upd - 1)
        maxw = 0
        for i in range(n_slots):
            p = self.r32(slot + i * 4)
            wv = self.r16u(p)
            if ((upd - 1) & 0xFFFF) == 0:
                wv >>= 1
                self.w16(p, wv)
            maxw = max(maxw, wv)
        if maxw:
            mask = 0x8000
            while not maxw & mask:
                mask = (mask >> 1) | 0x8000
            la = 0
            while la < n_slots:
                if self.r16u(self.r32(slot + la * 4)) & mask:
                    la += 1
                    continue
                u17, ins = la + 1, la
                if n_slots <= u17:
                    break
                u5 = ins
                while u17 < n_slots:
                    pp = slot + u17 * 4
                    p9 = self.r32(pp)
                    u5 = ins
                    if self.r16u(p9) & mask:
                        u5 = ins + 1
                        self.w32(pp, self.r32(slot + ins * 4))
                        self.w32(slot + ins * 4, p9)
                    u17 += 1
                    ins = u5
                if u5 != la:
                    la = u5 - 1
                nm = mask >> 1
                mask = nm | 0x8000
                if nm & 1:
                    break
        la, moved = 0, 0
        n_groups = self.r16u(0x0A)
        ustk = (n_groups - 1) & 0xFFFF
        # Bounded: a damaged stream must not spin here. Accepted worst case: a 164-byte stream whose model never
        # settles takes all 1,048,576 steps (about 2.5 s) before it is refused; a valid stream may need thousands.
        for _ in range(1 << 20):
            if la >= n_groups:
                break
            pgc, p2 = gc + la * 2, symt + la * 4
            g = self.r16u(pgc)
            if g == 0:
                la += 1
                continue
            p1 = self.r32(p2 + 4)
            w_first = self.r16u(self.r32(self.r32(p2)))
            w_last, w_last2 = self.r16u(self.r32(p1 - 4)), self.r16u(self.r32(p1 - 8))
            if g < 3 or ((levels - 1) & 0xFFFF) == la or w_first < w_last + w_last2:
                p16, found = p2 + 4, False
                acc = self.r16u(self.r32(p1 - 4))
                u17 = la + 2
                while u17 < n_groups:
                    p4 = symt + u17 * 4
                    acc = (acc - self.r16s(self.r32(self.r32(p4)))) & 0xFFFF
                    if self.r16u(gc + u17 * 2) > 1 and (acc & 0x8000 or acc < self.r16u(self.r32(self.r32(p4) + 4))):
                        found = True
                        break
                    u17 += 1
                if not found:
                    la += 1
                    continue
                self.w16(pgc, self.r16s(pgc) - 1)
                moved += 1
                la += 1
                self.w32(p16, self.r32(p16) - 4)
                self.w16(gc + la * 2, self.r16s(gc + la * 2) + 2)
                self.w32(p2 + 8, self.r32(p2 + 8) + 4)
                if la < ((u17 - 1) & 0xFFFF):
                    span = u17 - 1 - la
                    la += span
                    for _ in range(span):
                        self.w32(p16 + 8, self.r32(p16 + 8) + 4)
                        p16 += 4
                self.w16(gc + la * 2, self.r16s(gc + la * 2) + 1)
                self.w32(p16 + 4, self.r32(p16 + 4) + 4)
                self.w16(gc + 2 + la * 2, self.r16s(gc + 2 + la * 2) - 2)
                if self.r16s(gc + ustk * 2) == 0:
                    n_groups -= 1
                    self.w16(0x0A, n_groups)
                    ustk = (ustk - 1) & 0xFFFF
            else:
                moved += 1
                self.w16(pgc - 2, self.r16s(pgc - 2) + 1)
                self.w16(pgc, self.r16s(pgc) - 3)
                self.w16(gc + 2 + la * 2, self.r16s(gc + 2 + la * 2) + 2)
                self.w32(p2, self.r32(p2) + 4)
                self.w32(p2 + 4, self.r32(p2 + 4) - 8)
                if ustk == la:
                    n_groups += 1
                    self.w16(0x0A, n_groups)
                    ustk = (ustk + 1) & 0xFFFF
            la = 0
        else:
            raise ValueError("Huffman model does not settle")
        if moved < 0x10:
            if moved < 8 and self.r16u(0x2E) != 1:
                self.w16(0x02, self.r16u(0x02) << 1)
                self.w16(0x2C, self.r16u(0x2C) >> 1)
                self.w16(0x2E, self.r16u(0x2E) >> 1)
        else:
            self.w16(0x02, self.r16u(0x32))
            self.w16(0x2E, self.r16u(0x30))
        self.w16(0x00, self.r16u(0x02))
        if self.r16u(0x2C) == 0:
            self.w16(0x2C, self.r16u(0x2E))

    def decode(self, bi):
        buf = bi.buf
        if bi.pos >= bi.end:
            raise ValueError("compressed data truncated")
        val = ((1 << bi.bl) - 1) & buf[bi.pos]
        idx = tot = bi.bl
        idx -= 1
        lim = self.r32(0x10)
        if val < self.r16u(lim + idx * 8):
            while True:
                bi.pos += 1
                if bi.pos >= bi.end:
                    raise ValueError("compressed data truncated")
                idx += 8
                tot += 8
                val = (((val & 0xFF) << 8) | buf[bi.pos]) & 0xFFFF
                if val >= self.r16u(lim + idx * 8):
                    break
        idx -= 1
        cnt, shifted = (tot - 1) & 0xFF, 0
        while cnt:
            off = lim + 2 + idx * 8
            if val < (self.r16u(off) if 0 <= off < len(self.m) - 1 else 0):
                break
            idx -= 1
            cnt -= 1
            val >>= 1
            shifted += 1
        level = (idx + 1) & 0xFFFF
        k = (val - self.r16s(lim + level * 8)) & 0xFFFF
        p = self.r32(self.r32(self.r32(0x20) + level * 4) + k * 4)
        symbol = ((p - self.r32(0x18)) & 0xFFFFFFFF) >> 1
        if shifted == 0:
            shifted = 8
            bi.pos += 1
        bi.bl = shifted
        w = self.r32(0x18) + symbol * 2
        self.w16(w, self.r16u(w) + 1)
        countdown = self.r16s(0x00) - 1
        self.w16(0x00, countdown)
        if (countdown & 0xFFFF) == 0:
            self.rebuild()
            self.build_limits(0)
        if symbol == self.r16u(0x24):  # escape: a symbol not seen yet follows as raw bits
            raw = bi.bits(self.r16u(0x06))
            self.build_limits(self.add_symbol(raw))
            return raw
        return symbol


def decompress(data, offset, length, size, progress=None):
    """One file of the update: the LZSS token stream (flag 0 literal, flag 1 back-reference; distance 0 ends it)
    with adaptive Huffman literals, lengths and distance high bits. Back-references before the start read zero."""
    bi = _Bits(data, offset, min(offset + length, len(data)))
    if bi.bits(16) != DIFF_MAGIC:
        raise ValueError("bad compressed-data magic")
    raw_literals = bi.bits(8)
    bi.bits(8)
    init_period, upd_period, window = bi.bits(12), bi.bits(12), bi.bits(4)
    low_bits = 7 if window == 8 else 6
    literals = None if raw_literals else _Tree(8, 0x10, init_period, upd_period)
    lengths, distances = _Tree(6, 0x0C, init_period, upd_period), _Tree(6, 0x0C, init_period, upd_period)
    out = bytearray()
    report = 1 << 18
    while len(out) < size:
        if bi.bit() == 0:
            out.append(literals.decode(bi) if literals else bi.bits(8))
        else:
            low = bi.bits(low_bits)
            back = ((distances.decode(bi) << low_bits) | low) + 1
            if back == 1:
                break
            n = min(lengths.decode(bi) & 0x7F, size - len(out))
            for _ in range(n):
                out.append(out[-back] if len(out) >= back else 0)
        if progress and len(out) >= report:
            progress(len(out))
            report += 1 << 18
    return bytes(out)
