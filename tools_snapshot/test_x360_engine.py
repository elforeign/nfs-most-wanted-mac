#!/usr/bin/env python3
"""test_x360_engine.py - the X360 engine-constant table (x360_engine.inc, tools_snapshot/pubtables_gen.py).

The repository holds no retail numbers: brightness endpoints, shadow-fit tuning and rumble envelope/collision numbers are
read from the player's default.xex at build time. This test needs no game bytes for its first part:
  1. a synthetic image (every float 1.5) produces every macro the runtime uses, and only finite numbers;
  2. the no-executable stub defines every macro too, with the port's own fallbacks for brightness and rumble, so the
     runtime keeps working on the PC game (identity of the brightness slider stays inside its range);
and, when the player's X360 default.xex is reachable (NFS_X360_XEX, else <x360 root>/default.xex from roots.txt, else
beside a sibling x360/files), a second part:
  3. the real executable yields numbers in plausible ranges (brightness low < 1 < high);
  4. a copy with one byte changed is refused (no table is written from an executable that is not the supported build).
Exit 3 = part 3-4 skipped (no executable). Run: tools/.venv/bin/python -I tools_snapshot/test_x360_engine.py"""
import math
import os
from pathlib import Path
import re
import struct
import sys
import tempfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import pubtables_gen as pg  # noqa: E402

REQUIRED = {"X360E_BRIGHT_LOW", "X360E_BRIGHT_HIGH", "X360E_SHADOW_LIGHT_Z_MIN", "X360E_SHADOW_N_SCALE", "X360E_SHADOW_N_OFFSET",
            "X360E_SHADOW_EYE_BACK", "X360E_SHADOW_Y_SCALE", "X360E_SHADOW_DEPTH_SCALE", "X360E_FB_HIT_SOFT", "X360E_FB_HIT_SLOPE",
            "X360E_FB_WORLD_SLOPE", "X360E_FB_GROUND_SLOPE", "X360E_FB_SAME_DOT", "X360E_FB_RAMP_LO", "X360E_FB_RAMP_HI",
            "X360E_FB_SHIFT_TIME", "X360E_FB_SHIFT_GOOD", "X360E_FB_SHIFT_PERFECT", "X360E_FB_NOS_TIME", "X360E_FB_NOS_END",
            "X360E_FB_DAMAGE_END", "X360E_FB_GEAR_TIME", "X360E_FB_GEAR_LEVEL"}


def macros(text):
    return {m.group(1): m.group(2) for m in re.finditer(r"^#define (X360E_\w+) (\S+)$", text, re.M)}


def number(t):
    return float(t[:-1] if t.endswith("f") else t)


LI_IMMEDIATE = 0x0123      # an arbitrary synthetic immediate (not any retail value)


def synthetic_image(shift=None):
    """A synthetic image: every constant 1.5, each load site a `lis rK,hi` + `lfs f0,lo(rK)` pair (a distinct register per
    site) that addresses its constant; `shift` = (name, bytes) makes that one load address a different word."""
    img = bytearray(pg.IMAGE_SIZE)
    for _, va in pg.ENGINE + pg.ENGINE_DEC:
        struct.pack_into(">f", img, va - pg.IMAGE_BASE, 1.5)
    for _, va in pg.ENGINE_LI:
        struct.pack_into(">I", img, va - pg.IMAGE_BASE, (14 << 26) | LI_IMMEDIATE)
    byname = dict(pg.ENGINE + pg.ENGINE_DEC)
    for k, (name, site) in enumerate(sorted(pg.LOAD_SITES.items())):
        va = byname[name] + (shift[1] if shift and shift[0] == name else 0)
        reg = 3 + k
        hi, lo = (va + 0x8000) >> 16, va & 0xFFFF
        struct.pack_into(">I", img, site - 0x400 - 4 * k - pg.IMAGE_BASE, (15 << 26) | (reg << 21) | (hi & 0xFFFF))
        struct.pack_into(">I", img, site - pg.IMAGE_BASE, (48 << 26) | (reg << 21) | (reg << 16) | lo)
    return bytes(img)


def find_xex():
    for e in (os.environ.get("NFS_X360_XEX"),):
        if e and Path(e).is_file():
            return Path(e)
    rf = os.environ.get("NFS_ROOTS") or os.path.join(os.environ.get("HOME", "/tmp"), "Library/Application Support/NFSMW-port/roots.txt")
    try:
        for line in Path(rf).read_text().splitlines():
            if line.startswith("x360_root="):
                for c in (Path(line.split("=", 1)[1].strip()) / "default.xex", Path(line.split("=", 1)[1].strip()).parent / "default.xex"):
                    if c.is_file():
                        return c
    except OSError:
        pass
    c = HERE.parent.parent / "x360/default.xex"          # a development layout
    return c if c.is_file() else None


def main():
    text = pg.engine_text(synthetic_image(), "synthetic image (test)")
    got = macros(text)
    missing = REQUIRED - set(got)
    assert not missing, "missing macros: %s" % sorted(missing)
    assert all(math.isfinite(number(v)) for v in got.values())
    assert got["X360E_SHADOW_SIZE"] == str(LI_IMMEDIATE), got["X360E_SHADOW_SIZE"]      # the decoded immediate
    print("PASS: synthetic image -> %d macros, every runtime macro present, immediate decoded" % len(got))
    for name in pg.LOAD_SITES:       # control: an address that its load instruction does not read is refused
        try:
            pg.engine_text(synthetic_image((name, 4)), "shifted")
        except SystemExit as e:
            assert "is not what the load" in str(e), e
        else:
            raise AssertionError("%s shifted by 4 bytes was accepted" % name)
    for kind, word in (("ori", lambda r: (24 << 26) | (r << 21) | (r << 16) | 4),               # ori rK,rK,4
                       ("or", lambda r: (31 << 26) | (r << 21) | (r << 16) | (5 << 11) | (444 << 1)),     # or rK,rK,r5
                       ("addis", lambda r: (15 << 26) | (r << 21) | (r << 16) | 1),             # addis rK,rK,1
                       ("rlwinm", lambda r: (21 << 26) | (r << 21) | (r << 16) | (31 << 1)),    # rlwinm rK,rK,0,0,31
                       ("lwzu", lambda r: (33 << 26) | (31 << 21) | (r << 16) | 4),             # lwzu r31,4(rK)
                       ("stwu", lambda r: (37 << 26) | (31 << 21) | (r << 16) | 4),             # stwu r31,4(rK)
                       ("lfsu", lambda r: (49 << 26) | (1 << 21) | (r << 16) | 4),              # lfsu f1,4(rK)
                       ("lmw", lambda r: (46 << 26) | (r << 21) | (1 << 16)),                   # lmw rK,0(r1)
                       ("sc", lambda r: 0x44000022)):                                           # sc 1 (may change any GPR)
        for k, (name, site) in enumerate(sorted(pg.LOAD_SITES.items())):
            if site - 4 in pg.LOAD_SITES.values():       # the word before is another site's lfs
                continue
            img = bytearray(synthetic_image())
            struct.pack_into(">I", img, site - 4 - pg.IMAGE_BASE, word(3 + k))     # right before the lfs
            try:
                pg.engine_text(bytes(img), "tampered")
            except SystemExit as e:
                assert "not a known constant" in str(e), e
            else:
                raise AssertionError("%s on the base register before %s was accepted" % (kind, name))
    print("PASS: ori / or / addis / rlwinm / lwzu / stwu / lfsu / lmw / sc on the tracked base register invalidate it "
          "(refused for every site with a free word before it)")
    try:       # a site that is not an lfs
        img = bytearray(synthetic_image())
        struct.pack_into(">I", img, pg.LOAD_SITES["X360E_FB_HIT_SOFT"] - pg.IMAGE_BASE, 0x60000000)
        pg.engine_text(bytes(img), "nop")
    except SystemExit as e:
        assert "not an lfs" in str(e), e
    else:
        raise AssertionError("a non-lfs load site was accepted")
    print("PASS: +4-byte address shift refused for all %d checked constants; non-lfs site refused" % len(pg.LOAD_SITES))

    stub = macros(pg.ENGINE_STUB)
    assert REQUIRED <= set(stub), sorted(REQUIRED - set(stub))
    assert "#define X360_ENGINE_CONSTS 0" in pg.ENGINE_STUB
    lo, hi = number(stub["X360E_BRIGHT_LOW"]), number(stub["X360E_BRIGHT_HIGH"])
    assert 0 < lo < 1 < hi
    assert number(stub["X360E_FB_RAMP_LO"]) < number(stub["X360E_FB_RAMP_HI"])
    assert number(stub["X360E_FB_HIT_SOFT"]) > 0 and number(stub["X360E_FB_WORLD_SLOPE"]) > 0
    print("PASS: no-executable stub defines every macro with working fallbacks")

    xex = find_xex()
    if not xex:
        print("SKIP parts 3-4: no X360 default.xex reachable")
        return 3
    with tempfile.TemporaryDirectory(prefix="x360-engine-") as d:
        work = Path(d)
        real = macros(pg.engine_consts(xex, work))
        assert REQUIRED <= set(real)
        assert number(real["X360E_BRIGHT_LOW"]) < 1 < number(real["X360E_BRIGHT_HIGH"])
        assert number(real["X360E_FB_RAMP_LO"]) < number(real["X360E_FB_RAMP_HI"])
        assert 0 < number(real["X360E_SHADOW_LIGHT_Z_MIN"]) < 1
        attrib = xex.parent / "files/GLOBAL/ATTRIBUTES.BIN"
        if not attrib.is_file():
            attrib = xex.parent / "GLOBAL/ATTRIBUTES.BIN"
        if attrib.is_file():
            word = pg.steer_low_word(attrib)
            assert 0 < struct.unpack(">f", struct.pack(">I", word))[0] < 0.5 and "0x%08Xu" % word in pg.steer_text(word)
            print("PASS: steering dead zone read from ATTRIBUTES.BIN")
        print("PASS: the player's executable yields plausible numbers")
        data = bytearray(xex.read_bytes())
        for pos in (len(data) // 3, len(data) // 2):          # a changed byte inside the image data
            bad = bytearray(data)
            bad[pos] ^= 0x01
            path = work / "bad.xex"
            path.write_bytes(bad)
            try:
                pg.engine_consts(path, work)
            except SystemExit as e:
                assert "not the supported" in str(e), e
            else:
                raise AssertionError("a corrupted executable was accepted (byte %d)" % pos)
        print("PASS: corrupted executables refused")
    return 0


if __name__ == "__main__":
    sys.exit(main())
