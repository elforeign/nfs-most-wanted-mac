"""First-time Setup robustness; run with the project's Python -I.

Original backend on macOS: 8/11 portable cases fail, 3 controls pass; the Mac capacity helper is absent.
MW base: 12/12 pass in the trusted runner, including the real CoreFoundation capacity read.
Carbon additions on this base before fixes: 12/15 tests fail (15 failed subcases), 3 controls pass;
interrupted run-log claim: 1/1 error. Patched combined file: 31/31 pass in the trusted runner.
Archive advice regression: 1/1 fails before preserving the executable refusal's fix.
Disc privacy regression: 1/1 fails before letting the read error reach the shared input handler.

1. Free space: the app's Review page shows macOS's important-usage capacity (it counts purgeable space), while the
   backend refused with statfs's smaller number. Old code fails test_space_counts_purgeable_like_the_app (it raises
   "Not enough space"); fixed code passes. Control: a real shortage on both numbers is still refused.
2. Developer tools: /usr/bin/clang is an Apple stub that exits non-zero without the Command Line Tools (exit 1)
   or before the Xcode license is accepted (exit 69). Old code reported "clang failed (exit N)." with the generic
   "Open the log" fix; fixed code names the cause and the command that repairs it. Control: a good toolchain
   passes the same preflight.
3. Leftover names: an unmarked default work folder holding the app's own source-replies/ folder, or a default
   cache folder that Finder left a .DS_Store in, was refused ("already holds other files") with a fix the app's
   player cannot follow (the app has no folder choice). Control: a folder holding a player's own file is still
   refused.
"""
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from setup_kit import backend as b
from setup_kit import install as ins


def write(path, data, executable=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode() if isinstance(data, str) else bytes(data))
    if executable:
        path.chmod(0o755)


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="NFSMW_SETUP_ROBUST-")
        self.home = Path(self.temp.name).resolve()
        env = mock.patch.dict(os.environ, {"NFSMW_SETUP_HOME": str(self.home)}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(self.temp.cleanup)
        self.paths = b.Paths(self.home / "state/work")
        self.log = (self.home / "fixture.log").open("w+b", buffering=0)
        self.addCleanup(self.log.close)


class SpaceTests(Base):
    GB = 1_000_000_000

    def check(self, statfs_free, important):
        pc = self.home / "pc"
        write(pc / "speed.exe", b"x")
        usage = mock.Mock(free=statfs_free, total=500 * self.GB, used=0)
        with mock.patch.object(b.shutil, "disk_usage", return_value=usage), \
             mock.patch.object(b, "_important_capacity", create=True, return_value=important), \
             mock.patch.object(b, "volume_name", return_value="Macintosh HD"):
            b.check_space(self.paths, pc, self.home / "Applications")

    def test_space_counts_purgeable_like_the_app(self):
        # About 9.4 GB needed on one volume; statfs says 6 GB, the app (important usage) says 30 GB.
        self.check(6 * self.GB, 30 * self.GB)

    def test_real_shortage_is_still_refused(self):
        with self.assertRaisesRegex(b.SetupError, "Not enough space on Macintosh HD"):
            self.check(6 * self.GB, 7 * self.GB)
        with self.assertRaisesRegex(b.SetupError, "Not enough space"):
            self.check(6 * self.GB, None)  # capacity unreadable: statfs alone, as before

    def test_require_space_uses_the_same_number(self):
        usage = mock.Mock(free=1 * self.GB)
        with mock.patch.object(b.shutil, "disk_usage", return_value=usage), \
             mock.patch.object(b, "_important_capacity", create=True, return_value=20 * self.GB):
            b.require_space(self.home, 5 * self.GB)

    @unittest.skipUnless(sys.platform == "darwin", "reads CoreFoundation")
    def test_real_capacity_reads_on_this_mac(self):
        value = b._important_capacity(self.home)
        self.assertIsInstance(value, int)
        self.assertGreater(value, 0)
        # Important usage includes statfs's free blocks plus purgeable space; allow 1 GB of reserve either way.
        self.assertGreater(value, b.shutil.disk_usage(self.home).free - self.GB)


class DeveloperToolsTests(Base):
    TOOLS = ("clang", "clang++", "ar", "make", "xcrun", "otool", "sips", "iconutil", "plutil", "codesign", "tar",
             "shasum", "sh")

    def stub_tools(self, scenario):
        tools = self.home / "stub tools"
        sdk = self.home / "SDK"
        sdk.mkdir(exist_ok=True)
        script = tools / "stub.py"
        write(script, """import json, pathlib, sys
tool=sys.argv[1]; args=sys.argv[2:]; cfg=json.loads((pathlib.Path(__file__).parent/'scenario.json').read_text())
if tool in ('clang','clang++','xcrun') and cfg.get('license'):
 sys.stderr.write("You have not agreed to the Xcode license agreements. Please run 'sudo xcodebuild -license' from within a Terminal window to review and agree to the Xcode and Apple SDKs license.\\n"); sys.exit(69)
if tool in ('clang','clang++','xcrun') and cfg.get('no_tools'):
 sys.stderr.write("xcode-select: note: No developer tools were found, requesting install.\\nIf developer tools are located at a non-default location on disk, use `sudo xcode-select --switch path/to/Xcode.app` to specify the Xcode that you wish to use for command line developer tools, and cancel the installation dialog.\\n"); sys.exit(1)
if tool in ('clang','clang++','xcrun') and cfg.get('stale'):
 sys.stderr.write("xcrun: error: invalid active developer path (/Library/Developer/CommandLineTools), missing xcrun at: /Library/Developer/CommandLineTools/usr/bin/xcrun\\n"); sys.exit(1)
if '--version' in args: print('Apple clang version 17.0.0'); sys.exit(0)
if tool=='xcrun' and '--show-sdk-path' in args: print(SDK); sys.exit(0)
if tool=='xcrun' and '--show-sdk-version' in args: print('15.0'); sys.exit(0)
if '-o' in args:
 p=pathlib.Path(args[args.index('-o')+1]); p.write_bytes(b'probe'); p.chmod(0o755)
if tool=='ar': pathlib.Path(args[1]).write_bytes(b'archive probe')
""".replace("SDK", repr(str(sdk))))
        write(tools / "scenario.json", json.dumps(scenario))
        for tool in self.TOOLS:
            write(tools / tool, "#!/bin/sh\nexec " + shlex.quote(sys.executable) + " -I " + shlex.quote(str(script)) + " " +
                  shlex.quote(tool) + ' "$@"\n', True)
        self.paths.work.mkdir(parents=True, exist_ok=True)
        env = dict(PATH=str(tools), HOME=str(self.home))
        return b.Runner(self.log, env, self.paths.work)

    def preflight(self, scenario):
        runner = self.stub_tools(scenario)
        # The host and tool architecture checks have their own tests; this one exercises the tool failures only.
        packages = '{"capstone": "x", "numpy": "x", "Pillow": "x"}'
        real_run = runner.run
        def run(argv, cwd=None, capture=False):
            if len(argv) > 2 and str(argv[1]) == "-I" and str(argv[2]) == "-c":
                return packages
            return real_run(argv, cwd=cwd, capture=capture)
        with mock.patch.object(b.platform, "system", return_value="Darwin"), \
             mock.patch.object(b.platform, "machine", return_value="arm64"), \
             mock.patch.object(b, "translated", return_value=False), \
             mock.patch.object(b, "machine_archs", return_value={"arm64"}), \
             mock.patch.object(runner, "run", side_effect=run):
            return b.preflight(runner, self.paths.work)

    def test_good_tools_pass(self):
        self.assertEqual(self.preflight({})["sdk_version"], "15.0")

    def test_license_not_accepted_names_the_fix(self):
        with self.assertRaises(b.SetupError) as raised:
            self.preflight({"license": True})
        self.assertIn("license", str(raised.exception))
        self.assertIn("xcodebuild -license accept", raised.exception.fix)

    def test_missing_command_line_tools_names_the_fix(self):
        with self.assertRaises(b.SetupError) as raised:
            self.preflight({"no_tools": True})
        self.assertIn("Command Line Tools", str(raised.exception))
        self.assertIn("xcode-select --install", raised.exception.fix)

    def test_stale_developer_path_after_macos_update(self):
        with self.assertRaises(b.SetupError) as raised:
            self.preflight({"stale": True})
        self.assertIn("Command Line Tools", str(raised.exception))
        self.assertIn("xcode-select --install", raised.exception.fix)


class LeftoverNameTests(Base):
    def test_default_work_folder_accepts_locate_replies(self):
        default = self.paths.default_work
        (default / "source-replies").mkdir(parents=True)
        b.claim(default, True, b.SETUP_WORK_NAMES, "work", self.paths.state)
        self.assertTrue(ins.owned(default, self.paths.state))

    def test_default_cache_with_finder_metadata_is_accepted(self):
        cache = self.paths.default_cache
        write(cache / ".DS_Store", b"\0\0\0\1Bud1")
        (cache / "builds").mkdir()
        b.claim(cache, True, b.SETUP_CACHE_NAMES, "cache", self.paths.state)
        self.assertTrue(ins.owned(cache, self.paths.state))

    def test_chosen_folder_with_only_finder_metadata_counts_as_empty(self):
        chosen = self.home / "My Setup work"
        write(chosen / ".DS_Store", b"\0\0\0\1Bud1")
        write(chosen / "Icon\r", b"")
        b.claim(chosen, False, b.SETUP_WORK_NAMES, "work", self.paths.state)
        self.assertTrue(ins.owned(chosen, self.paths.state))

    def test_default_work_folder_accepts_interrupted_run_log(self):
        default = self.paths.default_work
        write(default / ".run-log-abandoned", b"interrupted log pointer")
        b.claim(default, True, b.SETUP_WORK_NAMES, "work", self.paths.state)
        self.assertTrue(ins.owned(default, self.paths.state))

    def test_player_files_are_still_refused(self):
        for default in (True, False):
            folder = self.home / f"Documents {default}"
            write(folder / ".DS_Store", b"\0\0\0\1Bud1")
            write(folder / "thesis.txt", "the player's own file")
            with self.subTest(default=default), self.assertRaisesRegex(b.SetupError, "already holds other files"):
                b.claim(folder, default, b.SETUP_WORK_NAMES, "work", self.paths.state)
            self.assertTrue((folder / "thesis.txt").is_file())
            self.assertFalse(ins.owned(folder, self.paths.state))




import errno
import io
import shutil
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from setup_kit import carbon_source
from test_backend import make_iso

PRODUCT = "{259C0ABB-A3B2-4D70-008F-BF7EE491B70B}"


def archive(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_STORED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return stream.getvalue()


def disc_files(product=PRODUCT, extra=None):
    files = {
        "autorun.inf": f"[Special]\r\nDisk=1\r\nProductGuiID={product}\r\n".encode(),
        "common_filelist.txt": b"1,1,server.dll\r\n1,1,nfsc.exe\r\n",
        "server.dll": b"server",
        "nfsc.exe": b"copy-protected disc executable",
        "0compressed.zip": archive({"TRACKS\\fixture.bin": b"t" * 4096, "GLOBAL\\fixture.bin": b"g" * 4096}),
        "1compressed.zip": archive({"MOVIES\\fixture.vp6": b"m" * 4096}),
    }
    files.update(extra or {})
    return files


class CarbonBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="carbon-robust-")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        self.work = self.home / "work"
        self.work.mkdir()
        self.events = b.Events(False, io.BytesIO(), io.StringIO())
        self.update = self.home / "update.exe"
        self.update.write_bytes(b"synthetic update")
        self.exe = self.home / "NFSC.exe"
        self.exe.write_bytes(b"synthetic compatible NFSC.exe")

    def refused(self, source, pattern, **inputs):
        with self.assertRaises(b.SetupError) as caught:
            carbon_source.prepare(source, self.work, self.events, **inputs)
        self.assertRegex(str(caught.exception), pattern)
        self.assertFalse((self.work / "carbon_src").exists())
        return caught.exception


class WrongExecutableTests(CarbonBase):
    """Finding 1 in REPORT.md."""

    def game(self, exe_bytes):
        folder = self.home / "Carbon"
        (folder / "TRACKS").mkdir(parents=True, exist_ok=True)
        (folder / "NFSC.exe").write_bytes(exe_bytes)
        return folder

    def test_refusal_states_the_version_and_what_is_needed(self):
        import struct
        # A version resource stating 1.0.0.0, as an un-updated disc install would.
        blob = b"MZ" + bytes(510) + struct.pack("<IIII", 0xFEEF04BD, 0x10000, 1 << 16, 0) + bytes(64)
        with self.assertRaises(b.SetupError) as caught:
            b.validate_carbon(self.game(blob))
        self.assertRegex(str(caught.exception), r"^NFSC\.exe is not the supported PC release of Need for Speed Carbon "
                                                r"\(it states version 1\.0\.0\.0, 592 bytes, SHA-256 [0-9a-f]{12}\)\.$")
        self.assertIn("version 1.4 NFSC.exe", caught.exception.fix)
        self.assertNotIn("Choose the installed", caught.exception.fix)

    def test_refusal_without_a_version_resource(self):
        with self.assertRaisesRegex(b.SetupError, r"it states no version"):
            b.validate_carbon(self.game(b"not a windows program"))

    def test_archive_keeps_executable_advice(self):
        source = self.home / "carbon.zip"
        source.write_bytes(archive({"Carbon/NFSC.exe": b"wrong build", "Carbon/TRACKS/x": b"x"}))
        error = self.refused(source, r"it states no version")
        self.assertIn("version 1.4 NFSC.exe", error.fix)

    def test_disc_route_separate_exe_uses_the_same_refusal(self):
        image = make_iso(self.home / "carbon.iso", disc_files())
        self.refused(image, r"it states no version", update=self.update, exe=self.exe)


class UnusableDiscTests(CarbonBase):
    """Finding 2 in REPORT.md."""

    def test_other_edition_iso_names_its_disc_id(self):
        image = make_iso(self.home / "carbon-ce.iso", disc_files("{11111111-2222-3333-4444-555555555555}"))
        for inputs in ({}, dict(update=self.update, exe=self.exe)):
            with self.subTest(inputs=sorted(inputs)):
                error = self.refused(image, r"isn't a supported Carbon PC disc edition \(disc id \{11111111", **inputs)
                self.assertIn("installed Carbon folder", error.fix)

    def test_other_edition_mdf_is_not_called_a_missing_folder(self):
        image = make_iso(self.home / "carbon-ce.mdf", disc_files("{11111111-2222-3333-4444-555555555555}"))
        for inputs in ({}, dict(update=self.update, exe=self.exe)):
            with self.subTest(inputs=sorted(inputs)):
                self.refused(image, r"isn't a supported Carbon PC disc edition", **inputs)

    def test_raw_sector_mdf_says_it_cannot_be_read(self):
        image = self.home / "carbon.mdf"
        image.write_bytes(bytes(64 * 2352))  # no ISO 9660 descriptor at 2048-byte sector 16
        for inputs in ({}, dict(update=self.update, exe=self.exe)):
            with self.subTest(inputs=sorted(inputs)):
                error = self.refused(image, r"can't read this \.mdf", **inputs)
                self.assertIn(".iso", error.fix)

    def test_most_wanted_disc_is_named(self):
        image = make_iso(self.home / "mw.iso", {
            "autorun.inf": b"[Special]\r\nDisk=1\r\nProductGuiID={MW-FIXTURE}\r\n",
            "speed.exe": b"mw", "0compressed.zip": archive({"GLOBAL\\x": b"x"})})
        self.refused(image, r"Most Wanted disc", update=self.update, exe=self.exe)
        self.refused(image, r"Most Wanted disc")

    def test_supported_disc_still_reaches_the_disc_route(self):
        # Control: the supported disc is not caught by the new refusals; it still asks for the update and NFSC.exe.
        image = make_iso(self.home / "carbon.iso", disc_files())
        self.refused(image, r"Missing: official 1\.4 update")


class FakeUpdate:
    """Stands in for carbon_patch.Update: copies the raw tree as the updated copy (no executables)."""
    def __init__(self, path):
        self.path = path

    def apply(self, source, destination, progress=None):
        shutil.copytree(source, destination, ignore=shutil.ignore_patterns("*.exe"))


class DiscSpaceTests(CarbonBase):
    """Finding 4 in REPORT.md."""

    def setUp(self):
        super().setUp()
        self.image = make_iso(self.home / "carbon.iso", disc_files())
        payload = disc_files()
        with zipfile.ZipFile(io.BytesIO(payload["0compressed.zip"])) as a, \
                zipfile.ZipFile(io.BytesIO(payload["1compressed.zip"])) as c:
            self.total = sum(i.file_size for z in (a, c) for i in z.infolist())
        self.total += len(payload["server.dll"]) + len(payload["nfsc.exe"])
        self.pins = [mock.patch.object(b, "_important_capacity", return_value=None), mock.patch.object(carbon_source.carbon_patch, "Update", FakeUpdate),
                     mock.patch.object(b, "CARBON_EXE_SHA256", b.sha_file(self.exe)),
                     mock.patch.object(b, "volume_name", return_value="Fixture Disk")]
        for pin in self.pins:
            pin.start()
            self.addCleanup(pin.stop)

    def free(self, amount):
        usage = shutil.disk_usage(self.work)
        return mock.patch.object(b.shutil, "disk_usage", return_value=usage._replace(free=amount))

    def prepare(self):
        return carbon_source.prepare(self.image, self.work, self.events, update=self.update, exe=self.exe)

    def test_room_for_one_copy_is_refused_before_writing(self):
        # Room for the raw tree but not for the updated copy beside it: the base unpacked it and ran out part-way.
        with self.free(512 * 1024**2 + self.total + self.total // 2):
            with self.assertRaisesRegex(b.SetupError, r"^Not enough space on Fixture Disk: [0-9.]+ GB more needed"):
                self.prepare()
        self.assertFalse((self.work / "carbon_src").exists())
        self.assertEqual(list((self.work / "tmp").glob("carbon-disc-*")), [])

    def test_room_for_both_copies_installs(self):
        with self.free(512 * 1024**2 + 2 * self.total + 1):
            root = self.prepare()
        self.assertEqual(b.sha_file(root / "NFSC.exe"), b.sha_file(self.exe))
        self.assertTrue((root / "TRACKS/fixture.bin").is_file())

    def test_force_quit_raw_tree_is_removed_on_cached_run(self):
        self.prepare()
        stale = self.work / "tmp" / "carbon-disc-abandoned"
        stale.mkdir()
        self.prepare()
        self.assertFalse(stale.exists())

    def test_force_quit_raw_tree_is_removed_next_run(self):
        stale = self.work / "tmp" / "carbon-disc-abandoned"
        (stale / "carbon_src/TRACKS").mkdir(parents=True)
        (stale / "carbon_src/TRACKS/big.bin").write_bytes(b"x" * 4096)
        self.prepare()
        self.assertFalse(stale.exists())


class BlockedInputTests(CarbonBase):
    """Finding 3 in REPORT.md."""

    def setUp(self):
        super().setUp()
        self.carbon = self.home / "Downloads" / "Need for Speed Carbon"
        (self.carbon / "TRACKS").mkdir(parents=True)
        (self.carbon / "NFSC.exe").write_bytes(b"exe")
        self.dest = self.home / "Applications"
        self.dest.mkdir()
        self.env = mock.patch.dict(os.environ, {"NFSMW_SETUP_HOME": str(self.home / "setup-home")})
        self.env.start()
        self.addCleanup(self.env.stop)

    def run_main(self, error):
        stream = io.StringIO()
        with mock.patch.object(b, "build", side_effect=error), mock.patch("sys.stdout", stream):
            rc = b.main(["--check", "--game", "carbon", "--carbon", str(self.carbon), "--dest", str(self.dest),
                         "--work", str(self.home / "setup-work"), "--events"])
        self.assertEqual(rc, 1)
        return json.loads(stream.getvalue().splitlines()[-1])

    def test_privacy_refusal_names_the_setting(self):
        denied = PermissionError(errno.EPERM, "Operation not permitted", str(self.carbon / "TRACKS"))
        event = self.run_main(denied)
        self.assertIn("macOS did not allow Setup to read", event["msg"])
        self.assertIn("Privacy & Security", event["fix"])
        self.assertNotIn("writable destination", event["fix"])

    def test_wrapped_archive_refusal_is_found(self):
        try:
            try:
                raise PermissionError(errno.EPERM, "Operation not permitted", str(self.carbon / "NFSC.exe"))
            except OSError as inner:
                raise b.SetupError(f"The Carbon archive can't be read: {inner}", carbon_source.FIX) from None
        except b.SetupError as wrapped:
            event = self.run_main(wrapped)
        self.assertIn("Privacy & Security", event["fix"])

    def test_file_permission_refusal(self):
        event = self.run_main(PermissionError(errno.EACCES, "Permission denied", str(self.carbon / "NFSC.exe")))
        self.assertIn("can't read", event["msg"])
        self.assertIn("Get Info", event["fix"])

    def test_unreadable_disc_keeps_privacy_error(self):
        image = self.carbon / "disc.mdf"
        denied = PermissionError(errno.EPERM, "Operation not permitted", str(image))
        with mock.patch.object(b.pc_disc, "Disc", side_effect=denied), self.assertRaises(PermissionError) as caught:
            carbon_source.disc_refusal(image)
        event = self.run_main(caught.exception)
        self.assertIn("Privacy & Security", event["fix"])

    def test_destination_refusal_keeps_destination_advice(self):
        # Control: a refused write at the destination is not the player's game files.
        event = self.run_main(PermissionError(errno.EACCES, "Permission denied", str(self.dest / "x.app")))
        self.assertIn("writable destination", event["fix"])


if __name__ == "__main__":
    unittest.main()
