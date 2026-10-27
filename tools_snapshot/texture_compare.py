"""Compare texture resolutions between PC and X360 by TextureInfo name hash.

TextureInfo (124 bytes, verified on PC TPKs): +0x0C name[24], +0x24 name hash, +0x38 image size,
+0x44 width u16, +0x46 height u16, +0x48 log2 w, +0x49 log2 h.
Platform record (32 bytes, follows the TextureInfo): +0x14 format (PC: FourCC or D3DFORMAT; X360: Xenon D3DFORMAT).
Sources: 0x33310004 arrays in uncompressed TPKs; the TextureInfo at the tail of each JDLZ blob in
compressed TPKs (0x33310003 tables); and, for X360 streaming bundles (0x55441122 blocks), any
TextureInfo array found in JDLZ or HUFF blocks (bounded native decoders; no emulator).

usage: texture_compare.py <pc dir> <x360 files dir> <out.tsv>
"""
import os, re, struct, sys
from pathlib import Path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
from setup_kit import huff as native_huff

def jdlz(src, p=0, max_size=32 << 20):
    return native_huff.jdlz(src, p, max_size)

XENON = {0x1A200152: "DXT1", 0x1A200153: "DXT3", 0x1A200154: "DXT5", 0x18280186: "A8R8G8B8",
         0x18280086: "LIN_A8R8G8B8", 0x1A200052: "LIN_DXT1", 0x1A200053: "LIN_DXT3", 0x1A200054: "LIN_DXT5"}
PCFMT = {21: "A8R8G8B8", 22: "X8R8G8B8", 41: "P8", 28: "A8", 50: "L8"}

def fmtname(plat, fmt):
    if len(plat) < 0x18: return "?"
    raw = plat[0x14:0x18]
    if raw.isalnum(): return raw.decode()
    v, = struct.unpack_from(fmt + "I", raw)
    return XENON.get(v) or PCFMT.get(v) or f"0x{v:08X}"

def info(e, fmt, plat=b""):
    h, = struct.unpack_from(fmt + "I", e, 0x24)
    w, ht = struct.unpack_from(fmt + "HH", e, 0x44)
    name = e[12:36].split(b"\0")[0].decode("latin-1")
    if not (0 < w <= 4096 and 0 < ht <= 4096 and name.isprintable() and name): return None
    return h, name, w, ht, fmtname(plat, fmt)

def arrays(d):
    tag = struct.pack("<I", 0x33310004)
    for m in re.finditer(re.escape(tag), d):
        p = m.start()
        sz, = struct.unpack_from("<I", d, p + 4)
        if sz == 0 or sz % 124 or p + 8 + sz > len(d): continue
        n, q = sz // 124, p + 8 + sz
        cid, psz = struct.unpack_from("<II", d, q) if q + 8 <= len(d) else (0, 0)
        plats = d[q + 8:q + 8 + psz] if cid == 0x33310005 and psz == n * 32 else b""
        for k in range(n):
            yield d[p + 8 + k * 124:p + 8 + (k + 1) * 124], plats[k * 32:(k + 1) * 32]

_emu = None

def huff(d, p):
    """Use bounded native HUFF; an explicit recompiled oracle can still be injected offline."""
    return _emu.huff_decode(d, p) if _emu is not None else native_huff.decode(d, p)

def jdlz_blobs(d):
    end = 0
    for m in re.finditer(b"JDLZ\x02\x10|HUFF\x01\x10", d):
        p = m.start()
        if p < end:
            continue  # compressed bytes can themselves contain header-looking strings
        yield jdlz(d, p, max_size=128 << 20) if d[p] == ord("J") else huff(d, p)
        csize, = struct.unpack_from("<I", d, p + 12)
        end = p + csize + (16 if d[p] == ord("H") else 0)

def scan_file(args):
    try:
        return _scan_file(args)
    except Exception as e:
        raise ValueError(f"texture scan failed for {args[0]}: {e}") from e

def tables(d):
    """Compressed-TPK tables (0x33310003): 24-byte entries hash, offset, packed size, unpacked size, flags, 0.
    Unpacked size = pixel bytes + 156 (TextureInfo + platform record), so it covers HUFF blobs too."""
    tag = struct.pack("<I", 0x33310003)
    for m in re.finditer(re.escape(tag), d):
        p = m.start()
        sz, = struct.unpack_from("<I", d, p + 4)
        if sz == 0 or sz % 24 or p + 8 + sz > len(d): continue
        for k in range(sz // 24):
            h, _, cs, us, _, _ = struct.unpack_from("<6I", d, p + 8 + k * 24)
            if 156 <= us < 64 << 20 and cs <= us + 64: yield h, us - 156

def unblock(d):
    """X360 streaming bundles: a run of 0x55441122 blocks separated by zero padding. Block header: magic,
    unpacked size, block size incl. this 24-byte header, destination offset in the section's unpacked data,
    offset of this block from the section's first block (0 starts a section), 0; then a JDLZ or HUFF
    payload. Blocks aren't stored in destination order (a section usually stores 0x8000.. first and its
    first 32 KB last), so each is placed at its destination. Returns the sections one after another, i.e.
    the same layout as the PC's raw file."""
    out, pos, section, first, spans = bytearray(), 0, 0, None, []
    def complete():
        end = 0
        for start, size in sorted(spans):
            if start != end:
                raise ValueError("missing/overlapping streaming destination blocks")
            end += size
    while pos < len(d):
        if pos + 24 > len(d):
            if any(d[pos:]):
                raise ValueError("truncated streaming header")
            break
        magic, usize, bsize, dst, rel = struct.unpack_from("<IIIII", d, pos)
        if magic != 0x55441122:
            kind, length = struct.unpack_from("<II", d, pos)
            if kind or pos + 8 + length > len(d):
                raise ValueError(f"invalid streaming gap at {pos:#x}")
            pos += 8 + length
            continue
        if rel == 0:
            complete()
            section = len(out)
            first, spans = pos, []
        elif first is None or pos - first != rel:
            raise ValueError("invalid streaming relative offset")
        if not 24 <= bsize <= len(d) - pos or not 0 < usize <= 32 << 20 or dst + usize > 512 << 20:
            raise ValueError(f"invalid streaming block at {pos:#x}")
        packed = d[pos + 24:pos + bsize]
        kind = packed[:4]
        part = jdlz(packed) if kind == b"JDLZ" else huff(packed, 0) if kind == b"HUFF" else packed[:usize]
        if len(part) != usize:
            raise ValueError(f"streaming block decoded {len(part)}/{usize} bytes")
        at = section + dst
        if at + usize > 2 << 30:
            raise ValueError("streaming output exceeds decode limit")
        if len(out) < at + usize:
            out += bytes(at + usize - len(out))
        out[at:at + usize] = part[:usize]
        spans.append((dst, usize))
        pos += bsize
    complete()
    return bytes(out)

def _scan_file(args):
    path, root = args
    tex = {}
    d = Path(path).read_bytes()
    if d[:4] == b"\x22\x11\x44\x55":
        d = unblock(d)
    rel = os.path.relpath(path, root)
    for h, n in tables(d):
        tex.setdefault(("bytes", h), n)
    for b in [d] + list(jdlz_blobs(d)):
        for e, plat in arrays(b):
            for fmt in "<>":
                r = info(e, fmt, plat)
                if r: tex.setdefault(r[0], (r[1], r[2], r[3], r[4], rel)); break
        # compressed TPK blob: TextureInfo + 32-byte platform record at the tail
        if b is not d and len(b) >= 156:
            for fmt in "<>":
                r = info(b[-156:-32], fmt, b[-32:])
                if r: tex.setdefault(r[0], (r[1], r[2], r[3], r[4], rel)); break
    return tex

def _scan_counted(args):
    args, work = args
    if native_huff._decoder is None:
        native_huff._decoder = native_huff.NativeHuff(work)
    decoder = native_huff._decoder
    before = decoder.blocks, decoder.bytes
    result = scan_file(args)
    decoder = native_huff._decoder
    after = decoder.blocks, decoder.bytes
    return result, after[0] - before[0], after[1] - before[1]


def scan(root, decoder=None):
    jobs = [(os.path.join(dp, fn), root) for dp, dirs, fs in os.walk(root)
            if os.path.relpath(dp, root).split(os.sep)[0].upper() != "NFS" for fn in sorted(fs)
            if fn.upper().endswith((".BIN", ".BUN", ".LZC"))]
    tex = {}
    if decoder is not None and (len(jobs) <= 4 or native_huff.job_count() == 1):
        # The validator's injected decoder already counts in-process calls.
        parts = ((scan_file(job), 0, 0) for job in sorted(jobs))
    else:
        work = str(Path(decoder.scratch.name).parent) if decoder is not None else os.environ.get("NFSMW_SETUP_HUFF_WORK")
        parts = native_huff.ordered_map(_scan_counted, ((job, work) for job in sorted(jobs)), worker_cap=3)
    for part, blocks, size in parts:
        if decoder is not None:
            decoder.blocks += blocks
            decoder.bytes += size
        for k, v in part.items(): tex.setdefault(k, v)
    return tex

def main():
    pc, xb = scan(sys.argv[1]), scan(sys.argv[2])
    pcb = {k[1]: v for k, v in pc.items() if isinstance(k, tuple)}
    xbb = {k[1]: v for k, v in xb.items() if isinstance(k, tuple)}
    pc = {k: v for k, v in pc.items() if not isinstance(k, tuple)}
    xb = {k: v for k, v in xb.items() if not isinstance(k, tuple)}
    common = set(pcb) & set(xbb)
    print(f"compressed-TPK table entries: PC {len(pcb)}, X360 {len(xbb)}, common {len(common)}: "
          f"same bytes {sum(pcb[h] == xbb[h] for h in common)}, X360 more {sum(xbb[h] > pcb[h] for h in common)}, "
          f"X360 fewer {sum(xbb[h] < pcb[h] for h in common)}")
    same = bigger = smaller = 0
    with open(sys.argv[3], "w") as out:
        out.write("hash\tname\tpc_w\tpc_h\tpc_fmt\tx360_w\tx360_h\tx360_fmt\tpc_file\tx360_file\tpc_bytes\tx360_bytes\n")
        for h in sorted(set(pc) | set(xb) | set(pcb) | set(xbb)):
            a, b = pc.get(h), xb.get(h)
            if a and b:
                pa, xa = a[1] * a[2], b[1] * b[2]
                same += pa == xa; bigger += xa > pa; smaller += xa < pa
            pa_, xb_ = a or ("",) * 5, b or ("",) * 5
            name = (a or b or ("",))[0]
            out.write(f"{h:08x}\t{name}\t{pa_[1]}\t{pa_[2]}\t{pa_[3]}\t{xb_[1]}\t{xb_[2]}\t{xb_[3]}\t{pa_[4]}\t{xb_[4]}\t{pcb.get(h, '')}\t{xbb.get(h, '')}\n")
    print(f"PC textures {len(pc)}, X360 textures {len(xb)}, common {same+bigger+smaller}: same {same}, X360 larger {bigger}, X360 smaller {smaller}")


if __name__ == "__main__":
    main()
