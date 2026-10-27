"""Native, bounded EA HUFF decoding. No emulator or generated recompiler needed.

Uses the runtime's bounds-checked implementation of the same HUFF leaf used by
x360_auxlights.RecompiledHuff. A synthetic known-byte decode tests the loaded
library before any player assets are accepted. Setup supplies a work directory
inside its transaction; other offline tools use an owned temporary directory.
"""
import atexit
import ctypes
import multiprocessing
import os
from pathlib import Path
import struct
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
MAX_READ = 32 << 20


def job_count():
    """




"""
    return max(1, min(os.cpu_count() or 1, 4, int(os.environ.get("NFSMW_SETUP_JOBS", "4"))))


def ordered_map(function, jobs, *, worker_cap=4):
    """Bound the pool and preserve first-copy-wins reduction and log order.

    Pass file paths, not decoded buffers. Callers return small inventories or
    digests, so even completed results waiting for an earlier job stay bounded.
    Small fixture/single-core runs stay in-process (including injected oracles).
    """
    jobs = list(jobs)
    count = min(job_count(), worker_cap, len(jobs))
    if count <= 1:
        yield from map(function, jobs)
        return
    with multiprocessing.get_context("spawn").Pool(count) as pool:
        yield from pool.imap(function, jobs, chunksize=1)


# File-level JDLZ scenes exceed 32 MiB. This bounded decoder keeps comparison
# and conversion practical without the glass tool's unchecked C fast path.
JDLZ_C = r'''
uint32_t decode_jdlz(const uint8_t *s, uint32_t n, uint8_t *d, uint32_t cap) {
    if (n < 18 || memcmp(s,"JDLZ\2\20",6)) return 0;
    uint32_t us, cs; memcpy(&us,s+8,4); memcpy(&cs,s+12,4);
    if (!us || us > cap || cs > n || cs < 18) return 0;
    uint32_t i=16,o=0,f1=1,f2=1;
    while (o < us) {
        if (f1==1) { if(i>=cs) return 0; f1=s[i++]|256; }
        if (f2==1) { if(i>=cs) return 0; f2=s[i++]|256; }
        if (f1&1) {
            if (i+2>cs) return 0;
            uint32_t a=s[i++],b=s[i++],len,dist;
            if(f2&1) { len=(b|((a&240)<<4))+3; dist=(a&15)+1; }
            else { dist=(b|((a&224)<<3))+17; len=(a&31)+3; }
            if(dist>o || len>us-o) return 0;
            for(uint32_t k=0;k<len;k++) { d[o]=d[o-dist]; o++; }
            f2>>=1;
        } else { if(i>=cs) return 0; d[o++]=s[i++]; }
        f1>>=1;
    }
    return o;
}
'''


def fixture(payload=b"Native HUFF\x00\xff proof"):
    """Encode original synthetic bytes using two one-bit codes and literal escapes."""
    def num(v):
        if v < 4:
            return f"{v + 4:03b}"
        n = 2
        while v >= (1 << (n + 1)) - 4:
            n += 1
        return "0" * (n - 2) + "1" + format(v - (1 << n) + 4, f"0{n}b")
    bits = f"{0x30fb:016b}{len(payload):024b}{255:08b}"
    bits += num(2) + num(65) + num(189)  # 'A', then the escape symbol 255
    for b in payload:
        bits += "0" if b == 65 else "1" + num(0) + "0" + f"{b:08b}"
    bits += "1" + num(0) + "1"  # escape / zero run / end
    bits += "0" * (-len(bits) % 16)
    packed = int(bits, 2).to_bytes(len(bits) // 8, "big")
    return b"HUFF\x01\x10\x00\x00" + struct.pack("<II", len(payload), len(packed)) + packed


class NativeHuff:
    def __init__(self, work=None):
        self.scratch = tempfile.TemporaryDirectory(prefix="native-huff-", dir=work)
        scratch = Path(self.scratch.name)
        source, lib = scratch / "huff.c", scratch / "huff.dylib"
        source.write_text('#include "huff.h"\nuint32_t decode(const uint8_t *s, uint32_t n, '
                          'uint8_t *d, uint32_t cap) { return huff_native(s,n,d,cap); }\n' + JDLZ_C)
        build = subprocess.run(["clang", "-O2", "-shared", "-I", str(ROOT / "runtime"),
                                str(source), "-o", str(lib)], capture_output=True, timeout=120,
                               env=dict(os.environ, TMPDIR=str(scratch)))
        if build.returncode:
            raise ValueError("native HUFF build failed: " + build.stderr.decode(errors="replace")[-2000:])
        self.lib = ctypes.CDLL(str(lib))
        self.lib.decode.argtypes = [ctypes.c_char_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32]
        self.lib.decode.restype = ctypes.c_uint32
        self.lib.decode_jdlz.argtypes = self.lib.decode.argtypes
        self.lib.decode_jdlz.restype = ctypes.c_uint32
        self.blocks = self.bytes = 0
        expected = b"Native HUFF\x00\xff proof"
        if self(fixture(expected)) != expected:
            raise ValueError("native HUFF known-byte self-test failed")
        self.blocks = self.bytes = 0

    def __call__(self, data, offset=0):
        if offset < 0 or offset + 16 > len(data) or data[offset:offset + 6] != b"HUFF\x01\x10":
            raise ValueError("invalid HUFF header")
        usize, csize = struct.unpack_from("<II", data, offset + 8)
        if not 0 < usize <= MAX_READ or not 0 < csize <= MAX_READ or offset + 16 + csize > len(data):
            raise ValueError("HUFF block outside input or decode limit")
        packed = data[offset:offset + 16 + csize]
        out = ctypes.create_string_buffer(usize)
        decoded = self.lib.decode(packed, len(packed), out, usize)
        if decoded != usize:
            raise ValueError(f"HUFF decoded {decoded}/{usize} required bytes")
        self.blocks += 1
        self.bytes += decoded
        return out.raw

    def huff_decode(self, data, offset=0):
        return self(data, offset)

    def jdlz(self, data, offset=0, max_size=MAX_READ):
        if offset < 0 or offset + 16 > len(data) or data[offset:offset + 6] != b"JDLZ\x02\x10":
            raise ValueError("invalid JDLZ header")
        usize, csize = struct.unpack_from("<II", data, offset + 8)
        if not 0 < usize <= max_size or not 18 <= csize <= max_size or offset + csize > len(data):
            raise ValueError("JDLZ block outside input or decode limit")
        out = ctypes.create_string_buffer(usize)
        n = self.lib.decode_jdlz(data[offset:offset + csize], csize, out, usize)
        if n != usize:
            raise ValueError(f"JDLZ decoded {n}/{usize} required bytes")
        return out.raw

    def close(self):
        if getattr(self, "scratch", None):
            self.scratch.cleanup()
            self.scratch = None

    def __del__(self):
        self.close()


_decoder = None


def decode(data, offset=0):
    global _decoder
    if _decoder is None:
        _decoder = NativeHuff(os.environ.get("NFSMW_SETUP_HUFF_WORK"))
    return _decoder(data, offset)


def jdlz(data, offset=0, max_size=MAX_READ):
    global _decoder
    if _decoder is None:
        _decoder = NativeHuff(os.environ.get("NFSMW_SETUP_HUFF_WORK"))
    return _decoder.jdlz(data, offset, max_size)


def _close():
    if _decoder is not None:
        _decoder.close()


atexit.register(_close)
