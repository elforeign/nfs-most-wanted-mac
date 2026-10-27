"""X360 arrest-scene handcuffs (CUFFS 02971076) for the HD set as DXT5 + mips with the PC alpha.

Usage: python -I x360_cuffs.py <X360 files> <PC game> <HD set>
The X360 copy is 256x256 DXT1 over the PC's 64x64 P8. The PC palette is opaque, but x360_textures.m's Init hook
rejects DXT1 over any alpha-capable PC format (P8 included), so the DXT1 file the set had was never loaded. This writes
the X360 colour blocks with the PC alpha (all 255) as DXT5 + mips over <set>/02971076_CUFFS.dds and its index.tsv row,
after checking it the way the runtime does (covering mip box-filtered to 64x64, RGB <= 12, alpha <= 16, DXT1 guard).
Negative controls: the old DXT1 file must fail the guard, and the new file read with a wrong width must fail.
Verified 2026-10-25: new DXT5 rgb 3.597 alpha 0.000 -> swap; old DXT1 -> guard; broken width -> rejected.
"""
import os
import struct
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_x360_fe_textures as audit
import x360_tex as tex

HASH, NAME, PC_SIZE, X_SIZE = 0x02971076, "CUFFS", (64, 64), (256, 256)
SOURCE = "NIS/SCENE_ARRESTF02B_BUNDLEB.BUN"


def pc_reference(pc_dir):
    """(w, h, RGBA) of the PC CUFFS and its format, from the PC arrest bundle."""
    d = open(os.path.join(pc_dir, "NIS", "Scene_ArrestF02b_BundleB.bun"), "rb").read()
    palettes = {}
    for _, h, w, ht, fmt, pix in tex.textures_in(d, 0x80, palettes):
        if h == HASH:
            assert (w, ht, fmt) == (*PC_SIZE, 41), f"PC CUFFS is {w}x{ht} format {fmt}, expected 64x64 P8"
            palette = np.frombuffer(palettes[h], np.uint8).reshape(256, 4)[:, [2, 1, 0, 3]]
            return (w, ht, palette[np.frombuffer(pix[:w * ht], np.uint8).reshape(ht, w)]), fmt
    raise ValueError("no CUFFS in the PC arrest bundle")


def x360_top(x360_dir):
    d = tex.unpack_file(open(os.path.join(x360_dir, SOURCE), "rb").read())
    for name, h, w, ht, fmt, pix in tex.textures_in(d):
        if h == HASH:
            kind, lin = tex.top_level(fmt, w, ht, pix)
            assert (name, w, ht, kind) == (NAME, *X_SIZE, "DXT1"), f"X360 CUFFS is {name} {w}x{ht} {kind}"
            return kind, lin
    raise ValueError("no CUFFS in the X360 arrest bundle")


def main():
    x360_dir, pc_dir, out = sys.argv[1:4]
    pc, pcfmt = pc_reference(pc_dir)
    kind, lin = x360_top(x360_dir)
    w, h = X_SIZE
    kind, lin = tex.with_alpha(kind, w, h, lin, tex.upscale(pc[2][:, :, 3], w, h))
    mips = tex.mip_chain(kind, w, h, lin)[0]
    new = tex.dds(kind, w, h, lin, mips)
    top = tex.decode_preview(kind, w, h, lin)
    assert kind == "DXT5" and top[:, :, 3].min() == 255, "alpha not preserved"
    ok, rgb, alpha, _, k = audit.verdict(new, pc, pcfmt)
    print(f"new {k} {w}x{h}, {struct.unpack_from('<I', new, 28)[0]} levels: rgb {rgb:.3f} alpha {alpha:.3f} -> {'swap' if ok else 'KEPT'}")
    assert ok
    path = os.path.join(out, f"{HASH:08x}_{NAME}.dds")
    if os.path.exists(path):                              # negative control: the old DXT1 must fail the guard
        old = open(path, "rb").read()
        if old[84:88] == b"DXT1":
            ok_old, rgb_old, _, _, _ = audit.verdict(old, pc, pcfmt)
            print(f"old DXT1: rgb {rgb_old:.3f} -> {'swap' if ok_old else 'kept (PC-alpha/DXT1 guard)'}")
            assert not ok_old
    broken = bytearray(new)                              # negative control: a wrong width must not pass
    struct.pack_into("<I", broken, 16, w // 2 + 4)
    try:
        ok_b = audit.verdict(bytes(broken), pc, pcfmt)[0]
    except (AssertionError, ValueError):
        ok_b = False
    print(f"broken width: {'swap' if ok_b else 'rejected'}")
    assert not ok_b
    open(path, "wb").write(new)
    index = os.path.join(out, "index.tsv")
    rows = open(index).read().splitlines(True)
    rows = [r for r in rows if not r.startswith(f"{HASH:08x}\t")]
    rows.append(f"{HASH:08x}\t{NAME}\t{w}\t{h}\t{kind}\t{SOURCE}\n")
    open(index, "w").write(rows[0] + "".join(sorted(rows[1:])))
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
