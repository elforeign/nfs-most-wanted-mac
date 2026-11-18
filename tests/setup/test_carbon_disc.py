"""Carbon EA disc extraction, using tiny synthetic ISO9660/Joliet images.

Shared ZIP listing budget: 0/1 passes on 972909b3, 1/1 passes with preflight.

Good-disc control on base: 1/1 fails (extraction absent, exit 1);
implementation: 1/1 passes (exit 0), full synthetic suite 14/14 passes.
Private inventory/extraction: --private (CARBON_DISC_IMAGE), exit 3 if absent.
No executable compatibility or patching is implied by extraction.
"""
import hashlib
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from setup_kit import backend as b, pc_disc
try:
    from setup_kit import carbon_disc
except ImportError:
    carbon_disc = None
from test_backend import make_iso

PRODUCT = "{259C0ABB-A3B2-4D70-008F-BF7EE491B70B}"
LOOSE = ("EAInstall.dll", "eauninstall.exe", "server.dll", "server.cfg", "NFS_icon.ico", "msvcr71.dll",
         "msvcp71.dll", "nfs_inst.exe", "nfs_uninst.exe", "setup.exe", "00000000.256", "nfsc.exe")


def archive(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_STORED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return stream.getvalue()


class CarbonDiscTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.work = self.home / "work"
        self.work.mkdir()
        self.events = b.Events(False, io.BytesIO(), io.StringIO())
        self.payload = {f"{name}\\fixture.bin": name.encode() for name in b.PC_DIRS}
        self.payload["MOVIES\\fixture.vp6"] = b"movie"
        self.loose = {name: ("loose " + name).encode() for name in LOOSE}
        self.files = {
            "autorun.inf": f"[Special]\r\nDisk=1\r\nProductGuiID={PRODUCT}\r\n".encode(),
            "common_filelist.txt": "".join(f"1,1,{name}\r\n" for name in LOOSE).encode(),
            "0compressed.zip": archive(dict(list(self.payload.items())[:3])),
            "1compressed.zip": archive(dict(list(self.payload.items())[3:])),
            **self.loose,
        }
        self.image = self.disc()

    def disc(self, name="game.iso", files=None):
        return make_iso(self.home / name, self.files if files is None else files)

    def prepare(self, image=None, **kwargs):
        self.assertIsNotNone(carbon_disc, "Carbon disc extraction is absent on the base")
        return carbon_disc.prepare(image or self.image, self.work, self.events, **kwargs)

    def assert_tree(self, root):
        expected = {name.replace("\\", "/"): data for name, data in dict(self.payload, **self.loose).items()}
        actual = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}
        self.assertEqual(actual, expected)

    def test_good_disc_full_tree_and_source_unchanged(self):
        before = hashlib.sha256(self.image.read_bytes()).hexdigest(), self.image.stat().st_mtime_ns
        self.assert_tree(self.prepare())
        self.assertEqual(before, (hashlib.sha256(self.image.read_bytes()).hexdigest(), self.image.stat().st_mtime_ns))

    def test_mdf_is_opt_in_and_validated(self):
        image = self.disc("game.mdf")
        self.assertFalse(pc_disc.is_disc(image))
        self.assertTrue(carbon_disc.is_disc(image))
        self.assert_tree(self.prepare(image))
        image.write_bytes(b"raw-sector MDF is not ISO9660")
        self.assertFalse(carbon_disc.is_disc(image))
        with self.assertRaises(b.SetupError):
            self.prepare(image)

    def refused(self, files, pattern=None):
        self.disc(files=files)
        with self.assertRaises(b.SetupError) as error:
            self.prepare()
        if pattern:
            self.assertRegex(str(error.exception), pattern)
        self.assertFalse((self.work / "carbon_src").exists())
        self.assertFalse((self.work / "carbon_src.json").exists())

    def test_unsafe_archive_path(self):
        self.refused(dict(self.files, **{"1compressed.zip": archive({"../escaped": b"bad"})}), "unsafe path")
        self.assertFalse((self.work / "escaped").exists())

    def test_truncated_archive(self):
        self.refused(dict(self.files, **{"1compressed.zip": self.files["1compressed.zip"][:-30]}))

    def test_crc_corruption(self):
        damaged = self.files["1compressed.zip"].replace(b"movie", b"MOVIE", 1)
        self.refused(dict(self.files, **{"1compressed.zip": damaged}), "damaged")

    def test_wrong_product(self):
        self.refused(dict(self.files, **{"autorun.inf": self.files["autorun.inf"].replace(PRODUCT.encode(), b"{OTHER}")}), "Carbon")

    def test_missing_disc(self):
        self.refused(dict(self.files, **{"common_filelist.txt": self.files["common_filelist.txt"] + b"2,1,\r\n"}), "Disc 2")

    def test_missing_archive(self):
        files = dict(self.files)
        del files["1compressed.zip"]
        self.refused(files, "1compressed.zip")

    def test_bonus_disc_ignored(self):
        bonus = {"autorun.inf": f"[Special]\nDisk=2\nProductGuiID={PRODUCT}\n".encode(), "bonus.txt": b"bonus"}
        self.disc("bonus.iso", bonus)
        self.assert_tree(self.prepare())

    def test_mdf_sibling_disc_discovery(self):
        files = dict(self.files)
        files["common_filelist.txt"] += b"2,1,\r\n"
        second = {"autorun.inf": files["autorun.inf"].replace(b"Disk=1", b"Disk=2"),
                  "1compressed.zip": files.pop("1compressed.zip")}
        self.disc(files=files)
        self.disc("second.mdf", second)
        self.assert_tree(self.prepare())

    def test_total_size_bound(self):
        with mock.patch.object(b, "ZIP_MAX_BYTES", 10):
            self.refused(self.files, "larger")

    def test_disc_window_zip_entry_budget_is_shared_before_open(self):
        with mock.patch.object(b, "ZIP_MAX_FILES", 4), \
                mock.patch.object(zipfile, "ZipFile", wraps=zipfile.ZipFile) as opened:
            with self.assertRaisesRegex(b.SetupError, "1compressed.zip.*too many entries"):
                self.prepare()
        self.assertEqual(opened.call_count, 1)
        self.assertFalse((self.work / "carbon_src").exists())

    def test_cancellation_removes_partial_tree(self):
        partial = []
        def cancel(done, total):
            if (self.work / "carbon_src/EAInstall.dll").is_file():
                partial.append(done)
                raise b.Cancelled()
        with mock.patch.object(self.events, "progress", side_effect=cancel):
            with self.assertRaises(b.Cancelled):
                self.prepare()
        self.assertTrue(partial, "cancel after archives and a loose file have been written")
        self.assertFalse((self.work / "carbon_src").exists())
        self.assertFalse((self.work / "carbon_src.json").exists())

    def test_cache_repair(self):
        root = self.prepare()
        (root / "nfsc.exe").write_bytes(b"changed")
        self.assert_tree(self.prepare())

    def test_output_cannot_contain_source(self):
        nested = self.work / "carbon_src"
        nested.mkdir()
        image = make_iso(nested / "game.iso", self.files)
        before = image.read_bytes()
        with self.assertRaises(b.SetupError):
            self.prepare(image)
        self.assertEqual(image.read_bytes(), before)


class PrivateDiscTests(unittest.TestCase):
    def test_real_disc_inventory_and_extraction(self):
        path = Path(os.environ.get("CARBON_DISC_IMAGE", str(ROOT.parent / ".runs/cdisc/Disc 1 v1/NFSCARBON.mdf")))
        if not path.is_file():
            self.skipTest("private Carbon image absent")
        before = b.sha_file(path)
        disc = pc_disc.Disc(path)
        try:
            self.assertEqual(disc.product, PRODUCT)
            numbers, loose = pc_disc.install_list(disc)
            self.assertEqual(numbers, {1})
            self.assertEqual(len(loose), 12)
            counts, sizes = [], []
            for name in disc.archives:
                with disc.open(name) as stream, zipfile.ZipFile(stream) as z:
                    members = [i for i in z.infolist() if not i.is_dir()]
                    counts.append(len(members))
                    sizes.append(sum(i.file_size for i in members))
            self.assertEqual(counts, [23, 1298])
            self.assertEqual(sizes, [3055395080, 2636251132])
            total = sum(sizes) + sum(disc.size(n) for n in loose)
            self.assertEqual(total, 5704016115)
        finally:
            disc.close()
        with tempfile.TemporaryDirectory() as tmp:
            events = b.Events(False, io.BytesIO(), io.StringIO())
            root = carbon_disc.prepare(path, Path(tmp), events)
            files = [p for p in root.rglob("*") if p.is_file()]
            self.assertEqual(len(files), 23 + 1298 + 12)
            self.assertEqual(sum(p.stat().st_size for p in files), total)
        self.assertEqual(b.sha_file(path), before)


if __name__ == "__main__":
    if "--private" in sys.argv:
        sys.argv.remove("--private")
        result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(PrivateDiscTests))
        sys.exit(3 if result.skipped else 0 if result.wasSuccessful() else 1)
    unittest.main(defaultTest="CarbonDiscTests", verbosity=2)
