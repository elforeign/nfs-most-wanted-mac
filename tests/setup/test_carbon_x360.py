"""Carbon Xbox artwork preparation: synthetic archives in a folder, an ISO and extracted files.

Good: 13/13 tests pass (the real-sample check runs when a Carbon Xbox 360 image is at NFS_CARBON_X360 or beside
the checkout, else it skips). Broken, same tests, exit 1 (run with --broken NAME), failing tests of 13:
title (the title id is not checked) 1; glyph (glyph completeness not checked) 3; fingerprint (archive hash not
checked) 1; diagram (diagram entries not checked) 1.
The archives here are synthetic and carry no console data; the fingerprint table is patched to match them.
"""
import hashlib
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from setup_kit import carbon_x360 as cx, car_sources as cs
import test_car_sources as helpers

GLOBALB, DYNTEX = "GLOBAL/GLOBALB.BUN", "GLOBAL/DYNTEX.BIN"


def chunk(kind, payload):
    return struct.pack("<II", kind, len(payload)) + payload


def glyph_archive(names=None, width=32):
    names = sorted(cx.GLYPHS) if names is None else names
    info, platform = b"", b""
    for index, name in enumerate(names):
        raw = name.encode()
        record = bytearray(((89 + len(raw) + 3) & ~3))
        struct.pack_into("<I", record, 12, cx._string_hash(name))
        struct.pack_into("<I", record, 20, index * 64)
        struct.pack_into("<I", record, 36, 64)
        struct.pack_into("<HH", record, 40, width, width)
        record[88] = len(raw)
        record[89:89 + len(raw)] = raw
        info += bytes(record)
        platform += bytes(20) + struct.pack("<I", cx.TEXTURE_FORMAT) + bytes(8)
    pixels = bytes(64 * len(names))
    pack = chunk(cx.PACK, chunk(0xB3310000, chunk(cx.GLYPH_INFO, info) + chunk(cx.GLYPH_PLATFORM, platform)) +
                 chunk(0xB3320000, chunk(0x33320003, pixels)))
    return chunk(0x80000001, pack) + chunk(0x00034100, b"padding")


def diagram_archive(hashes=cx.DIAGRAMS):
    start = 16 + 24 * len(hashes) + 8     # wrapper and table headers, rows, then the body chunk header
    rows = b"".join(struct.pack("<6I", h, start, 300, 300, 0, 0) for h in hashes)
    return chunk(0x80000002, chunk(cx.DIAGRAM_TABLE, rows)) + chunk(0x33310000, bytes(400))


def fingerprints(assets):
    return {name: (len(data), hashlib.sha256(data).hexdigest()) for name, data in assets.items()}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.assets = {GLOBALB: glyph_archive(), DYNTEX: diagram_archive()}
        self.patch_fingerprints(self.assets)

    def patch_fingerprints(self, assets):
        patch = mock.patch.object(cx, "FILES", fingerprints(assets))
        patch.start()
        self.addCleanup(patch.stop)

    def sources(self, assets=None, xex=None):
        assets = self.assets if assets is None else assets
        xex = helpers.fake_xex() if xex is None else xex
        physical = helpers.archive_files(xex, assets)
        folder, extracted, image = self.work / "disc", self.work / "extracted", self.work / "disc.iso"
        helpers.write_files(folder, physical)
        helpers.write_files(extracted, dict(assets, **{"DEFAULT.XEX": xex}))
        image.write_bytes(helpers.tiny_iso(physical))
        return {"folder": folder, "extracted": extracted, "iso": image}

    def prepare(self, source, name="out"):
        return cx.prepare(source, self.work / name)


class PrepareTests(Base):
    def test_every_source_form_prepares_the_same_two_files(self):
        for form, source in self.sources().items():
            with self.subTest(form=form):
                result = self.prepare(source, "out-" + form)
                root = result.roots["x360_root"]
                found = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
                self.assertEqual(found, [DYNTEX, GLOBALB])
                for name, data in self.assets.items():
                    self.assertEqual((root / name).read_bytes(), data)
                self.assertEqual(result.features, ("xbox_art",))
                self.assertEqual(sorted(p.name for p in (self.work / ("out-" + form)).iterdir()), ["prepared"])

    def refused(self, sources, pattern):
        for form, source in sources.items():
            with self.subTest(form=form), self.assertRaisesRegex(cx.CarbonArtError, pattern):
                self.prepare(source, "bad-" + form)
            out = self.work / ("bad-" + form)
            self.assertFalse((out / "prepared").exists())
            self.assertEqual(list(out.iterdir()), [])   # nothing partial stays behind

    def test_wrong_title_is_refused(self):
        self.refused(self.sources(xex=helpers.fake_xex(0x45410876)), "not Need for Speed Carbon")

    def test_missing_executable_is_refused(self):
        sources = self.sources()
        (sources["folder"] / "DEFAULT.XEX").unlink()
        (sources["extracted"] / "DEFAULT.XEX").unlink()
        self.refused({k: sources[k] for k in ("folder", "extracted")}, "no default.xex")

    def test_a_missing_glyph_is_refused(self):
        assets = dict(self.assets, **{GLOBALB: glyph_archive(sorted(cx.GLYPHS - {"SQUARE"}))})
        self.patch_fingerprints(assets)
        self.refused(self.sources(assets), "SQUARE")

    def test_a_glyph_of_the_wrong_size_is_refused(self):
        assets = dict(self.assets, **{GLOBALB: glyph_archive(width=64)})
        self.patch_fingerprints(assets)
        self.refused(self.sources(assets), "lacks")

    def test_a_missing_diagram_is_refused(self):
        assets = dict(self.assets, **{DYNTEX: diagram_archive(cx.DIAGRAMS[:1])})
        self.patch_fingerprints(assets)
        self.refused(self.sources(assets), "diagram")

    def test_a_truncated_archive_is_refused(self):
        truncated = dict(self.assets, **{GLOBALB: self.assets[GLOBALB][:-9]})
        self.refused(self.sources(truncated), "not an audited")

    def test_a_truncated_archive_with_a_matching_fingerprint_fails_its_structure(self):
        truncated = dict(self.assets, **{GLOBALB: self.assets[GLOBALB][:-9]})
        self.patch_fingerprints(truncated)
        self.refused(self.sources(truncated), "chunk|lacks")

    def test_a_changed_archive_of_the_same_size_is_refused(self):
        data = bytearray(self.assets[DYNTEX])
        data[-1] ^= 1
        self.refused(self.sources(dict(self.assets, **{DYNTEX: bytes(data)})), "audited")

    def test_a_missing_archive_is_refused(self):
        sources = self.sources()
        (sources["extracted"] / DYNTEX).unlink()
        self.refused({"extracted": sources["extracted"]}, "no GLOBAL/DYNTEX.BIN")

    def test_output_inside_the_source_is_refused(self):
        source = self.sources()["folder"]
        with self.assertRaises(cx.CarbonArtError):
            cx.prepare(source, source / "out")
        self.assertFalse((source / "out").exists())

    def test_an_existing_result_is_not_replaced(self):
        source = self.sources()["folder"]
        self.prepare(source)
        with self.assertRaisesRegex(cx.CarbonArtError, "already exists"):
            self.prepare(source)


class RealSampleTests(unittest.TestCase):
    def test_real_carbon_xbox_image_prepares(self):
        path = os.environ.get("NFS_CARBON_X360")
        if not path:
            found = sorted((ROOT.parent / "ext_untrusted/carbon_x360").glob("*.iso"))
            path = found[0] if len(found) == 1 else None
        if not path or not Path(path).is_file():
            self.skipTest("no Carbon Xbox 360 image")
        with tempfile.TemporaryDirectory() as work:
            result = cx.prepare(path, Path(work) / "out")
            root = result.roots["x360_root"]
            for name, (size, digest) in cx.FILES.items():
                data = (root / name).read_bytes()
                self.assertEqual((len(data), hashlib.sha256(data).hexdigest()), (size, digest))


def main():
    broken = None
    if "--broken" in sys.argv:
        at = sys.argv.index("--broken")
        broken = sys.argv[at + 1]
        del sys.argv[at:at + 2]
    patches = {
        "title": mock.patch.object(cs, "xex_info", lambda data: {"title_id": cx.TITLE}),
        "glyph": mock.patch.object(cx, "glyph_surfaces", lambda data: set(cx.GLYPHS)),
        "fingerprint": mock.patch.object(cx, "_copy", lambda source, entry, name, destination, progress, done, total:
                                         (destination.parent.mkdir(parents=True, exist_ok=True),
                                          destination.write_bytes(cs._read(source, entry)))),
        "diagram": mock.patch.object(cx, "diagram_entries", lambda data: {}),
    }
    if broken:
        patches[broken].start()
    unittest.main()


if __name__ == "__main__":
    main()
