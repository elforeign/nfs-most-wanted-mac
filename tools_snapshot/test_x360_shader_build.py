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
import hashlib
import tempfile
from types import SimpleNamespace
from unittest import mock

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


def retained_equivalence(iso):
    """Read the original archive directly from the disc; copy only the retained extent.
    No full archive is materialized, including for the full-file generator comparison.
    """
    sys.path.insert(0, str(HERE.parent / "tools"))
    from setup_kit import x360
    scratch = HERE.parent / ".metal-cache"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="shader-retention-", dir=scratch) as tmp:
        tmp = Path(tmp)
        source = x360.Source(iso)
        try:
            retained = tmp / "zzdata0_09bb7000.bin"
            source.copy("NFS/ZZDATA0.BIN", retained, 0x09BB7000, 413696)
            assert retained.stat().st_size == 413696
            assert hashlib.sha256(retained.read_bytes()).hexdigest() == "8bf0fefe00108c1af3f9065c0c76c5e07f1a908122195b5f5f062576ddc13b57"
            x360._validate_shaders(retained)
            full = tmp / "ZZDATA0.BIN"
            open_file, stat = open, Path.stat
            item = source.files["NFS/ZZDATA0.BIN"]
            class ArchiveReader:
                def __enter__(self):
                    self.file = open_file(iso, "rb")
                    self.file.seek(item.offset)
                    return self
                def seek(self, offset):
                    return self.file.seek(item.offset + offset)
                def read(self, size):
                    return self.file.read(size)
                def __exit__(self, *args):
                    self.file.close()
            def read_file(path, *args, **kwargs):
                return ArchiveReader() if path == full else open_file(path, *args, **kwargs)
            def file_stat(path, *args, **kwargs):
                return SimpleNamespace(st_size=item.size) if path == full else stat(path, *args, **kwargs)
            (tmp / "slice-work").mkdir()
            (tmp / "full-work").mkdir()
            slice_lits = lits.text(lits.read_all(retained), "X360 NFS/ZZDATA0.BIN")
            slice_bodies = body.generate(retained, tmp / "slice-work")
            with mock.patch("builtins.open", read_file), mock.patch.object(Path, "stat", file_stat):
                assert lits.text(lits.read_all(full), "X360 NFS/ZZDATA0.BIN") == slice_lits
                assert body.generate(full, tmp / "full-work") == slice_bodies
            assert not full.exists()
            found = lits.scan(retained)
            literal_names = lits.needed()
            body_names = sorted({r["shader"] for r in body.RECIPES.values()})
            assert lits.select(found, literal_names)[0] == "USA"
            assert not body.locate(body.read_window(retained), body_names)[1]
            xex = source.size("DEFAULT.XEX")
            print("PASS retained shader bytes: %d -> %d; saved=%d; shader+XEX=%d" %
                  (item.size, 413696, item.size - 413696, 413696 + xex))
            print("PASS full/slice outputs: %d/%d literals, %d/%d bodies; %d containers" %
                  (len(literal_names), len(literal_names), len(body_names), len(body_names), sum(map(len, found.values()))))
        finally:
            source.close()


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
    if "--retention-iso" in sys.argv:
        retained_equivalence(Path(sys.argv[sys.argv.index("--retention-iso") + 1]))
    sys.exit(main())
