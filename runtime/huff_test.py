"""Check huff.h against the game's own HUFF_decode on every HUFF blob of the X360 vinyl packs (no game run needed).

    tools/.venv/bin/python -I port/runtime/huff_test.py [CARS dir] [--jobs N] [--limit N]

The reference is speed.exe's x86 HUFF_decode (0064DCA0, through LZDecompress's HUFF case 006500F0) under Unicorn
(tools/scripts/speed_emu.py, the same emulator the recompiler's difftest uses); huff_native is compiled from
runtime/huff.h into a scratch dylib and called through ctypes. Every byte of every blob is compared; nothing is
written to disk but the dylib (blobs are decoded in memory, one at a time per job). Default CARS dir:
<NFS_PARENT>/x360/files/CARS (read only; a development layout, absent from a public checkout).
"""
import ctypes
import multiprocessing
import os
import struct
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..", "..")          # the folder holding port/ and tools/ (from port/runtime or a worktree beside port)
sys.path.insert(0, os.path.join(ROOT, "tools", "scripts"))

WRAPPER = '#include "huff.h"\nuint32_t huff(const uint8_t *i, uint32_t n, uint8_t *o, uint32_t c) { return huff_native(i, n, o, c); }\n'


def build_lib(tmp):
    src, lib = os.path.join(tmp, "huff_lib.c"), os.path.join(tmp, "huff_lib.dylib")
    open(src, "w").write(WRAPPER)
    subprocess.check_call(["cc", "-O2", "-shared", "-I", HERE, "-o", lib, src])
    return lib


def huff_blobs(path):
    """(hash, offset, packed, unpacked) of each HUFF entry in the compressed-TPK table, as vinyls.m's index_pack."""
    d = open(path, "rb").read()
    out = []
    if len(d) < 8:
        return d, out
    t0, ln = struct.unpack_from("<II", d, 0)
    p, end = 8, 8 + ln
    while p + 8 <= end:
        cid, cl = struct.unpack_from("<II", d, p)
        if cid == 0xB3310000:
            q, qend = p + 8, p + 8 + cl
            while q + 8 <= qend:
                cid2, cl2 = struct.unpack_from("<II", d, q)
                if cid2 == 0x33310003:
                    for k in range(cl2 // 24):
                        h, off, packed, unpacked = struct.unpack_from("<IIII", d, q + 8 + 24 * k)
                        if h and off + packed <= len(d) and unpacked <= 64 << 20 and d[off:off + 5] == b"HUFF\x01":
                            out.append((h, off, packed, unpacked))
                q += 8 + cl2
            break
        p += 8 + cl
    return d, out


def check_pack(args):
    path, lib, limit = args
    import speed_emu
    emu = speed_emu.SpeedEmu()
    native = ctypes.CDLL(lib).huff
    native.restype = ctypes.c_uint32
    native.argtypes = [ctypes.c_char_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32]
    d, blobs = huff_blobs(path)
    res = {"blobs": 0, "bytes": 0, "differ": [], "t_emu": 0.0, "t_nat": 0.0}
    cap = 64 << 20
    buf = ctypes.create_string_buffer(cap)
    for h, off, packed, unpacked in blobs[:limit or None]:
        blob = d[off:off + packed]
        t0 = time.perf_counter()
        try:
            ref = emu.huff_decode(d, off)
        except Exception as e:      # the game's decoder failing would be a finding in itself
            ref = None
            res["differ"].append((path, h, f"emulator: {e}"))
            continue
        t1 = time.perf_counter()
        n = native(blob, len(blob), buf, cap)
        t2 = time.perf_counter()
        res["t_emu"] += t1 - t0
        res["t_nat"] += t2 - t1
        res["blobs"] += 1
        res["bytes"] += len(ref)
        if n != len(ref) or n != unpacked or ctypes.string_at(buf, n) != ref:
            res["differ"].append((path, h, f"native {n} bytes, game {len(ref)}, table {unpacked}"))
    return path, res


def main():
    try:
        import speed_emu  # noqa: F401  (developer emulator; absent from a public checkout)
    except ImportError:
        print("SKIP: needs the developer emulator tools/scripts/speed_emu.py (not in a public checkout)")
        return 3
    argv = sys.argv[1:]
    jobs = int(argv[argv.index("--jobs") + 1]) if "--jobs" in argv else 2
    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else 0
    pos = [a for i, a in enumerate(argv) if not a.startswith("--") and (i == 0 or argv[i - 1] not in ("--jobs", "--limit"))]
    cars = pos[0] if pos else os.path.join(ROOT, "x360", "files", "CARS")
    packs = sorted(os.path.join(cars, c, "VINYLS.BIN") for c in os.listdir(cars) if os.path.isfile(os.path.join(cars, c, "VINYLS.BIN")))
    with tempfile.TemporaryDirectory() as tmp:
        lib = build_lib(tmp)
        tot = {"blobs": 0, "bytes": 0, "differ": [], "t_emu": 0.0, "t_nat": 0.0}
        t0 = time.time()
        with multiprocessing.Pool(jobs) as pool:
            for path, r in pool.imap_unordered(check_pack, [(p, lib, limit) for p in packs]):
                for k in ("blobs", "bytes", "t_emu", "t_nat"):
                    tot[k] += r[k]
                tot["differ"] += r["differ"]
                print(f"{os.path.basename(os.path.dirname(path)):16s} {r['blobs']:4d} HUFF blobs, {len(r['differ'])} differ "
                      f"({time.time() - t0:.0f} s)", flush=True)
                for x in r["differ"][:5]:
                    print("  DIFFERENT:", x[0], f"{x[1]:08X}", x[2], flush=True)
    packs = [p for p in packs if os.path.getsize(p) >= 8]
    print(f"{len(packs)} packs: {tot['blobs']} HUFF blobs ({tot['bytes'] >> 20} MB unpacked): {len(tot['differ'])} differ")
    if tot["bytes"]:
        print(f"game HUFF_decode under Unicorn {tot['t_emu']:.0f} s; huff_native {tot['bytes'] / 1e6 / tot['t_nat']:.0f} MB/s "
              f"({4.194e3 * tot['t_nat'] / (tot['bytes'] / 1e6):.2f} ms per 4 MB, ctypes call included)")
    return 1 if tot["differ"] or not tot["blobs"] else 0


if __name__ == "__main__":
    sys.exit(main())
