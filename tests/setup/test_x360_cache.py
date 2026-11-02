"""Persistent X360 preparation proofs (synthetic converter, real installs).

Reuse counter: first + second install = 1 + 0 preparations; persistence disabled
produces 1 + 1 and fails the reuse oracle. Corruption: validation on rejects 1/1;
--validation-off rejects 0/1 and the corruption oracle fails (exit 1).
Review regressions on cfa2780f: 0/6 tests pass (2 failures, 11 errors);
fixed: 6/6 pass. Toolchain switch prepares 1 time before / 2 after and
publishes the current toolchain's bytes. Eviction-before-copy retains 0/1
replacement entries before / 1/1 after under simulated disk pressure.
Publication I/O faults (mkdir/hash/prune/copy/manifest/rename/cleanup):
0/8 installs succeed before / 8/8 after; cache-read fault: 0/1 before / 1/1 after.
Stale pending plus low space on a cache hit: 0/1 installs before / 1/1 after;
lock-before-cleanup/space preserves a live publication: 0/1 before / 1/1 after.
Run with the project's Python -I; the offline suite discovers this file.
"""
import json
import errno
import os
from pathlib import Path
import shutil
import sys
import types
import unittest
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_backend import Fixture, FakeRunner, b, ins, write

cache_impl = sys.modules[b.PreparedCache.__module__]
check_space = b.check_space


class PersistentPreparationTests(Fixture):
    def setUp(self):
        super().setUp()
        self.iso = self.home / "source.iso"
        write(self.iso, b"synthetic source A")
        self.calls = 0
        self.module = types.SimpleNamespace(prepare=self.prepare)

    def prepare(self, source, out, pc, progress):
        self.calls += 1
        write(out / "prepared/files/file", source.read_bytes())
        write(out / "prepared/assets/texture", b"converted texture")
        return types.SimpleNamespace(roots={"x360_root": out / "prepared/files",
                                            "assets_root": out / "prepared/assets"}, features=["audio"], missing=[])

    def run_setup(self, check=False, space_check=None):
        args = self.args(check)
        args.x360 = str(self.iso)
        with self.pipeline_stubs(), mock.patch.object(b, "check_space", side_effect=space_check), \
                mock.patch.object(b.importlib, "import_module", return_value=self.module):
            return b.build(args, self.paths, self.events, FakeRunner(self.log, self.child_env, self.paths.work), self.source)

    def installed(self):
        roots = dict(line.split("=", 1) for line in (self.paths.data / "roots.txt").read_text().splitlines())
        return Path(roots["x360_root"]) / "file"

    def entry(self):
        return next((self.paths.cache / "x360").glob("*.*"))

    def test_second_install_skips_preparation(self):
        original = (self.iso.read_bytes(), self.iso.stat())
        self.run_setup()
        first = self.calls
        entry = self.entry()
        cache_hash = ins.digest_tree(entry)
        self.assertFalse((self.paths.work / "stage").exists())
        self.run_setup()
        self.assertEqual((first, self.calls - first), (1, 0), "second install prepared the source again")
        self.assertEqual(self.installed().read_bytes(), original[0])
        self.assertEqual(ins.digest_tree(entry), cache_hash)
        self.assertNotEqual(self.installed().stat().st_ino, (entry / "data/prepared/files/file").stat().st_ino)
        write(self.installed(), b"edited installed copy")
        self.assertEqual(ins.digest_tree(entry), cache_hash, "install writes damaged the retained entry")
        self.run_setup(check=True)
        self.assertEqual(self.calls, 1)
        self.assertEqual(ins.digest_tree(entry), cache_hash, "check must never write into the cache")
        self.assertEqual(self.iso.read_bytes(), original[0])
        self.assertEqual(self.iso.stat().st_mtime_ns, original[1].st_mtime_ns)
        os.utime(self.iso, ns=(1, 1))
        self.run_setup()
        self.assertEqual(self.calls, 1, "source timestamps are not content")
        renamed = self.home / "relocated.iso"
        self.iso.rename(renamed)
        self.iso = renamed
        self.run_setup()
        self.assertEqual(self.calls, 1, "ISO filenames are not content")
        print("X360 preparation counter: first=1 second=0; check/timestamp reuse=0")

    def test_corruption_is_rejected(self):
        self.run_setup()
        entry = self.entry()
        write(entry / "data/prepared/files/file", b"corrupted output!!")
        self.run_setup()
        self.assertEqual(self.calls, 2, "corrupted entry was reused instead of preparing again")
        self.assertEqual(self.installed().read_bytes(), self.iso.read_bytes())
        print("X360 corrupted entries rejected=1/1; rebuilt preparation count=2")

    def test_source_converter_pc_and_toolchain_changes_rebuild(self):
        self.run_setup()
        st = self.iso.stat()
        write(self.iso, b"synthetic source B")
        os.utime(self.iso, ns=(st.st_atime_ns, st.st_mtime_ns))
        self.run_setup()
        self.assertEqual(self.calls, 2)
        write(self.source / "tools_snapshot/converter.py", "# changed converter\n")
        self.run_setup()
        self.assertEqual(self.calls, 3)
        write(self.pc / "GLOBAL/fixture.bin", b"changed PC texture input")
        self.run_setup()
        self.assertEqual(self.calls, 4)
        self.toolchain_version = "fixture-v2"
        self.run_setup()
        self.assertEqual(self.calls, 5)
        write(self.source / "runtime/unrelated.c", "/* unrelated runtime update */\n")
        self.run_setup()
        self.assertEqual(self.calls, 5, "runtime-only update invalidated converter output")

    def test_check_toolchain_switch_rebuilds_before_publication(self):
        def prepare(*args):
            result = self.prepare(*args)
            write(result.roots["x360_root"] / "file", self.toolchain_version)
            return result
        self.module.prepare = prepare
        self.run_setup(check=True)
        self.assertEqual(self.calls, 1)
        self.toolchain_version = "fixture-v2"
        self.run_setup()
        self.assertEqual(self.calls, 2, "check output from the old toolchain was published under the new key")
        self.assertEqual(self.installed().read_text(), "fixture-v2")
        self.run_setup()
        self.assertEqual(self.calls, 2)
        self.assertEqual(self.installed().read_text(), "fixture-v2")

    def test_eviction_precedes_publication_copy(self):
        self.run_setup()
        previous = self.entry()
        write(self.iso, b"replacement preparation")
        copytree = shutil.copytree
        copies = []
        def limited_copy(source, target, *args, **kwargs):
            target = Path(target)
            if target.name == "data" and target.parent.name.startswith(".pending-"):
                copies.append(target)
                if previous.exists():
                    raise OSError(errno.ENOSPC, "old entry occupies the publication space")
            return copytree(source, target, *args, **kwargs)
        with mock.patch.object(cache_impl, "MAX_ENTRIES", 1), \
                mock.patch.object(shutil, "copytree", side_effect=limited_copy):
            self.run_setup()
        self.assertEqual(len(copies), 1)
        self.assertFalse(previous.exists())
        self.assertEqual(len(list((self.paths.cache / "x360").iterdir())), 1)
        self.assertEqual(self.installed().read_bytes(), self.iso.read_bytes())
        self.run_setup()
        self.assertEqual(self.calls, 2, "replacement was not retained after eviction")

    def test_publication_io_failures_still_install(self):
        self.run_setup()
        for operation, code in (("mkdir", errno.EIO), ("digest", errno.EIO), ("prune", errno.EIO),
                                ("copy", errno.ENOSPC), ("copy", errno.EIO), ("manifest", errno.EIO),
                                ("rename", errno.EIO), ("cleanup", errno.EIO)):
            with self.subTest(operation=operation, errno=code):
                write(self.iso, f"input {operation} {code}")
                if operation == "mkdir":
                    owner, name = Path, "mkdir"
                    affected = lambda path, *args: Path(path) == self.paths.cache / "x360"
                elif operation in ("digest", "prune"):
                    owner = cache_impl if operation == "digest" else b.PreparedCache
                    name = "_output_digest" if operation == "digest" else "_prune"
                    affected = lambda *args: True
                elif operation == "copy":
                    owner, name = shutil, "copytree"
                    affected = lambda source, target, *args: Path(target).parent.name.startswith(".pending-")
                elif operation == "manifest":
                    owner, name = Path, "write_text"
                    affected = lambda path, *args: Path(path).name == "manifest.json"
                elif operation == "rename":
                    owner, name = os, "rename"
                    affected = lambda source, *args: Path(source).name.startswith(".pending-")
                else:
                    owner, name = cache_impl, "remove"
                    affected = lambda path: Path(path).name.startswith(".pending-")
                original = getattr(owner, name)
                def fail(*args, **kwargs):
                    if affected(*args):
                        raise OSError(code, "injected optional-cache failure")
                    return original(*args, **kwargs)
                with mock.patch.object(owner, name, autospec=True, side_effect=fail):
                    self.run_setup()
                self.assertEqual(self.installed().read_bytes(), self.iso.read_bytes())

    def test_cache_read_failure_still_installs(self):
        self.run_setup()
        original = Path.glob
        def fail(path, pattern):
            if path == self.paths.cache / "x360":
                raise OSError(errno.EIO, "unreadable optional cache")
            return original(path, pattern)
        with mock.patch.object(Path, "glob", fail):
            self.run_setup()
        self.assertEqual(self.calls, 2)
        self.assertEqual(self.installed().read_bytes(), self.iso.read_bytes())

    def test_stale_pending_removed_before_space_check_on_hit(self):
        self.run_setup()
        pending = self.paths.cache / "x360/.pending-interrupted"
        write(pending / "data/partial", b"interrupted publication")
        probes = []
        def space(paths, pc, dest):
            def usage(path):
                probes.append(path)
                return types.SimpleNamespace(free=(3 if pending.exists() else 32) * 1024**3)
            with mock.patch.object(shutil, "disk_usage", side_effect=usage):
                check_space(paths, pc, dest)
        self.run_setup(space_check=space)
        self.assertTrue(probes)
        self.assertFalse(pending.exists())
        self.assertEqual(self.calls, 1, "a valid cache hit should not convert again")

    def test_live_publication_is_not_cleaned(self):
        self.run_setup()
        pending = self.paths.cache / "x360/.pending-live"
        write(pending / "data/partial", b"live publication")
        with b.exclusive(self.paths.cache / "build.lock"):
            with self.assertRaisesRegex(b.SetupError, "Another Setup"):
                self.run_setup(space_check=lambda *args: self.fail("space check ran before the cache lock"))
            self.assertEqual((pending / "data/partial").read_bytes(), b"live publication")

    def test_failed_install_keeps_preparation_for_repair(self):
        runner = FakeRunner(self.log, self.child_env, self.paths.work)
        runner.fail = "bundle"
        args = self.args()
        args.x360 = str(self.iso)
        with self.pipeline_stubs(), mock.patch.object(b.importlib, "import_module", return_value=self.module), \
                self.assertRaises(b.SetupError):
            b.build(args, self.paths, self.events, runner, self.source)
        self.assertTrue(self.entry().is_dir())
        self.run_setup()
        self.assertEqual(self.calls, 1)

    def test_count_byte_bounds_and_clear(self):
        with mock.patch.object(cache_impl, "MAX_ENTRIES", 2):
            for letter in "ABC":
                write(self.iso, ("source " + letter).encode())
                self.run_setup()
            self.assertEqual(len(list((self.paths.cache / "x360").iterdir())), 2)
        with mock.patch.object(cache_impl, "MAX_BYTES", 1):
            write(self.iso, b"oversize source")
            self.run_setup()
            self.assertEqual(list((self.paths.cache / "x360").iterdir()), [])
            self.assertEqual(self.installed().read_bytes(), b"oversize source")
        self.run_setup()
        shutil.rmtree(self.paths.cache / "x360")
        before = self.calls
        self.run_setup()
        self.assertEqual(self.calls, before + 1)

    def test_linked_cache_and_roots_do_not_escape(self):
        self.run_setup()
        entry = self.entry()
        outside = self.home / "outside"
        write(outside / "keep", b"preserve me")
        shutil.rmtree(entry / "data/prepared/files")
        os.symlink(outside, entry / "data/prepared/files")
        self.run_setup()
        self.assertEqual(self.calls, 2)
        self.assertEqual((outside / "keep").read_bytes(), b"preserve me")
        shutil.rmtree(self.paths.cache / "x360")
        os.symlink(outside, self.paths.cache / "x360")
        self.run_setup()
        self.assertEqual(self.calls, 3, "a linked cache must fall back to uncached preparation")
        self.assertEqual(self.installed().read_bytes(), self.iso.read_bytes())
        self.assertEqual(list(outside.iterdir()), [outside / "keep"])

    def test_incomplete_manifest_and_publication_rebuild(self):
        self.run_setup()
        entry = self.entry()
        (entry / "manifest.json").write_text("{broken")
        pending = entry.parent / ".pending-interrupted"
        write(pending / "data/partial", b"incomplete")
        self.run_setup()
        self.assertEqual(self.calls, 2)
        self.assertFalse(pending.exists())
        metadata = json.loads((self.entry() / "manifest.json").read_text())
        metadata["roots"]["x360_root"] = "../../outside"
        (self.entry() / "manifest.json").write_text(json.dumps(metadata))
        self.run_setup()
        self.assertEqual(self.calls, 3)


if __name__ == "__main__":
    if "--validation-off" in sys.argv:
        sys.argv.remove("--validation-off")
        with mock.patch.object(b.PreparedCache, "_matches", return_value=True):
            unittest.main(defaultTest="PersistentPreparationTests.test_corruption_is_rejected", verbosity=2)
    elif "--persistence-off" in sys.argv:
        sys.argv.remove("--persistence-off")
        with mock.patch.object(b.PreparedCache, "restore", return_value=None):
            unittest.main(defaultTest="PersistentPreparationTests.test_second_install_skips_preparation", verbosity=2)
    else:
        unittest.main(verbosity=2)
