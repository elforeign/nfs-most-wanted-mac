#!/usr/bin/env python3
"""test_x360_shader_build.py - the X360 shader pins are taken from one disc (x360_shader_lits.select).

ZZDATA0.BIN is the USA or the European (PAL) retail disc's; nine of the shaders the port uses differ between them. Both
x360_shader_lits.read_all and x360_shader_body.locate must accept a file whose shaders are all one disc's and refuse one
that mixes the two (one PAL-only shader in an otherwise USA set, and the reverse). No game bytes: the container scan
and parser are replaced by synthetic identity hashes.
Before the fix (pins accepted from either disc, shader by shader): 4 of 8 checks fail (both mixed sets accepted by
read_all and by locate); after: 8 of 8 pass.
Run: tools/.venv/bin/python -I tools_snapshot/test_x360_shader_build.py"""
from pathlib import Path
import struct
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import x360_shader_body as body  # noqa: E402
import x360_shader_lits as lits  # noqa: E402


def table(text):
    return dict(line.split() for line in text.split("\n") if line.strip())


USA, PAL_ONLY = table(lits.PINS), table(lits.PINS_PAL)
PAL = dict(USA, **PAL_ONLY)
DIFFER = sorted(PAL_ONLY)


def disc_sets():
    """(label, {shader: identity hash}, accepted?) for the two discs and two mixed sets."""
    usa_mixed = dict(USA, **{DIFFER[0]: PAL_ONLY[DIFFER[0]]})
    pal_mixed = dict(PAL, **{DIFFER[-1]: USA[DIFFER[-1]]})
    return [("USA disc", USA, True), ("PAL disc", PAL, True),
            ("USA set with one PAL shader", usa_mixed, False), ("PAL set with one USA shader", pal_mixed, False)]


def run_lits(digests):
    found = {}
    for k, name in enumerate(sorted(digests)):
        found.setdefault(digests[name], []).append((name, ("payload", k)))
    saved = lits.scan
    lits.scan = lambda path: found
    try:
        lits.read_all(Path("synthetic"))
        return True
    except SystemExit:
        return False
    finally:
        lits.scan = saved


def run_body(digests):
    rows = sorted(dict(digests, **body.EXTRA_PINS).items())
    d = bytearray(0x20 * (len(rows) + 1))
    by_offset = {}
    for k, (name, digest) in enumerate(rows):
        struct.pack_into(">I", d, 0x20 * k, lits.MAGIC)
        by_offset[0x20 * k] = digest
    saved = lits.parse
    lits.parse = lambda data, i: (by_offset[i],) if i in by_offset else None
    try:
        found, missing, _ = body.locate(bytes(d), sorted(digests))
        return not missing and len(found) == len(digests)
    finally:
        lits.parse = saved


def main():
    assert DIFFER and set(PAL_ONLY) <= set(USA), "the PAL pins must be a subset of the USA shaders"
    fails = 0
    for label, digests, want in disc_sets():
        for tool, run in (("read_all", run_lits), ("locate", run_body)):
            got = run(digests)
            ok = got == want
            fails += not ok
            print("%s %s: %s -> %s" % ("PASS" if ok else "FAIL", tool, label, "accepted" if got else "refused"))
    print("%d of %d checks failed" % (fails, 2 * len(disc_sets())))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
