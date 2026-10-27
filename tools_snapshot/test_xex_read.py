#!/usr/bin/env python3
"""test_xex_read.py - crafted XEX2 files against tools_snapshot/xex_read.c, built with -fsanitize=address,undefined.

Every crafted file must be refused cleanly (exit 1, the expected message, no sanitizer report); a well-formed synthetic
executable (own session key, one SHA-1-valid block, a 3-symbol LZX stream "MZ" + a 2-byte match) must decode to
"MZZZ". Files are synthetic: no game bytes. Needs clang and macOS CommonCrypto (ctypes) only.

  python -I tools_snapshot/test_xex_read.py [--source xex_read.c]
Proof (2026-10-26): the pre-hardening reader (git show 6a42cfd:tools_snapshot/xex_read.c) -> 6 of 7 FAIL (ASan
heap-buffer-overflow for the optional-header count and the image-size overrun, ASan memcpy overlap for the 65535-byte
chunk, UBSan shift exponent 32 for the window field; another title and another build accepted); hardened -> 7/7 PASS."""
import argparse
import ctypes
import hashlib
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
RETAIL = bytes.fromhex("20B185A59D28FDC340583FBB0896BF91")   # the public XEX2 retail key (as in xex_read.c)
TITLE = 0x454107D9
BASE = 0x82000000

_cc = ctypes.CDLL("/usr/lib/system/libcommonCrypto.dylib")


def aes(encrypt, key, data, ecb):
    out = ctypes.create_string_buffer(len(data))
    moved = ctypes.c_size_t()
    r = _cc.CCCrypt(0 if encrypt else 1, 0, 2 if ecb else 0, key, 16, None if ecb else bytes(16), data, len(data),
                    out, len(data), ctypes.byref(moved))
    assert r == 0, r
    return out.raw


class Bits:
    """LZX bit writer: MSB-first into 16-bit little-endian words."""
    def __init__(self):
        self.v, self.n = 0, 0

    def put(self, value, width):
        self.v, self.n = self.v << width | value, self.n + width

    def put_code(self, code):
        for c in code:
            self.put(int(c), 1)

    def data(self, pad_words=4):
        n = (self.n + 15) // 16 * 16 + 16 * pad_words
        v = self.v << (n - self.n)
        words = [(v >> (n - 16 * (i + 1))) & 0xFFFF for i in range(n // 16)]
        return b"".join(struct.pack("<H", w) for w in words)


def lzx_mz_match():
    """A verbatim block of 4 output bytes: literals 'M' 'Z', then a 2-byte match at the repeated offset (R0 = 1)."""
    b = Bits()
    b.put(0, 1)                       # no Intel E8 header
    b.put(1, 3); b.put(0, 16); b.put(4, 8)
    main = {0x00: "00", 0x4D: "01", 0x5A: "10", 256: "11"}   # four symbols of length 2 (canonical order)
    for first, last in ((0, 256), (256, 496)):
        for i in range(20):           # pretree: symbol 0 (keep 0) and 15 (0 -> 2) with 1-bit codes
            b.put(1 if i in (0, 15) else 0, 4)
        for x in range(first, last):
            b.put(1 if x in main else 0, 1)
    for i in range(20):               # length tree: all 249 lengths stay 0 (empty)
        b.put(1 if i in (0, 15) else 0, 4)
    for _ in range(249):
        b.put(0, 1)
    for sym in (0x4D, 0x5A, 256):
        b.put_code(main[sym])
    return b.data()


def xex(stream, image_size=4, nopt=3, window=0x8000, title=TITLE, chunk_len=None, size=None):
    """A synthetic XEX2: own session key wrapped with the retail key, one block {next 0, hash, chunk, 0}."""
    session = bytes(range(16))
    filekey = aes(True, RETAIL, session, ecb=True)
    block = struct.pack(">I", 0) + bytes(20) + struct.pack(">H", len(stream) if chunk_len is None else chunk_len)
    block += stream + struct.pack(">H", 0)
    data = block + bytes(-len(block) % 16)
    h = bytearray(0x400)
    h[0:4] = b"XEX2"
    struct.pack_into(">IIII", h, 8, 0x400, 0, 0x100, nopt)
    for i, (k, v) in enumerate(((0x3FF, 0x80), (0x10201, BASE), (0x40006, 0xC0))):
        struct.pack_into(">II", h, 0x18 + 8 * i, k, v)
    struct.pack_into(">IHHII", h, 0x80, 0x24, 1, 2, window, len(block))
    h[0x90:0xA4] = hashlib.sha1(block).digest()
    struct.pack_into(">I", h, 0xC0 + 0xC, title)
    struct.pack_into(">I", h, 0x104, image_size)
    h[0x250:0x260] = filekey
    return bytes(h) + aes(True, session, data, ecb=False)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", type=Path, default=HERE / "xex_read.c")
    a = ap.parse_args()
    good_hash = hashlib.sha256(b"MZZZ").hexdigest()
    stream = lzx_mz_match()
    tiny = bytearray(0x200); tiny[0:4] = b"XEX2"; struct.pack_into(">I", tiny, 0x14, 62)
    cases = [   # (name, file bytes, length requested, expected refusal or None for the positive control)
        ("well-formed synthetic executable decodes", xex(stream), 4, None),
        ("optional-header count past a 512-byte file", bytes(tiny), 4, "header field past the end"),
        ("SHA-valid block declaring a 65535-byte chunk", xex(stream, chunk_len=0xFFFF), 4, "chunk past the end"),
        ("image size 3, a match writing a 4th byte", xex(stream, image_size=3), 3, "match past the block, frame or image"),
        ("window field 0x80000001", xex(stream, window=0x80000001), 4, "unsupported LZX window size"),
        ("another title ID", xex(stream, title=0x41560817), 4, "another title"),
        ("right title, another build (image hash)", xex(stream), 4, "another build"),
    ]
    fails = 0
    with tempfile.TemporaryDirectory(prefix="xex_read_test-") as tmp:
        tmp = Path(tmp)
        exe = tmp / "xex_read"
        subprocess.run(["clang", "-O1", "-g", "-w", "-fsanitize=address,undefined", "-fno-sanitize-recover=undefined",
                        "-o", str(exe), str(a.source)], check=True)
        for i, (name, data, length, want) in enumerate(cases):
            f = tmp / ("case%d.xex" % i)
            f.write_bytes(data)
            expect = hashlib.sha256(b"MZZY").hexdigest() if "another build" in name else good_hash
            args = [str(exe), str(f), "0x%X" % BASE, str(length), "0x%08X" % TITLE, expect]
            try:
                r = subprocess.run(args, capture_output=True, timeout=20)
                if b"usage" in r.stderr:            # the pre-hardening reader took 3 arguments
                    r = subprocess.run(args[:4], capture_output=True, timeout=20)
                err = r.stderr.decode(errors="replace")
            except subprocess.TimeoutExpired:
                r, err = None, "timeout (hang)"
            sanitizer = "Sanitizer" in err or "runtime error" in err
            if want is None:
                ok = r is not None and r.returncode == 0 and r.stdout == b"MZZZ" and not sanitizer
            else:
                ok = r is not None and r.returncode == 1 and want in err and not sanitizer and not r.stdout
            fails += not ok
            first = next((ln for ln in err.splitlines() if "ERROR" in ln or "runtime error" in ln or "xex_read:" in ln),
                         err.strip()[:160])
            print("%s %-48s %s" % ("PASS" if ok else "FAIL", name, first.strip()[:150]))
    print("xex_read_test: %s (%d of %d failed)" % ("FAIL" if fails else "PASS", fails, len(cases)))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
