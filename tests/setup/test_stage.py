"""Headless staging proofs using synthetic PC/Xbox sources and stub compilers.

Verification: 16/16 tests pass. Good/broken controls: 3/3 good pipeline oracles (failed build has no readiness,
stage preserves active files, remembered Xbox extras survive); 3/3 deliberately
broken pipelines must fail those same oracles (publish after failure, install
early, forget Xbox). All paths are temporary; no game, network or real installs.
The real CLI, validation, copy/hash/fsync and profile lock are exercised; build
tools, signing and Xbox conversion are fixtures, as in test_backend.py.

Review regressions: good 10/10 case oracles pass; broken base 2c887bc3
passes 0/10 (10 failures): log symlink/hardlink (2), late cancellation
after publication/final sync/cleanup (3), moved/empty/foreign/private
remembered destination (4), and corrupt choices with explicit inputs (1).
"""
import functools
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_backend import Fixture, FakeRunner, write, b, ins

s = b.staging


class StageTests(Fixture):
    def run_stage(self, target=None, remembered=False, fail=None, extra=(), default_work=False, builder=None):
        target = target or self.home / "update"
        build = builder or functools.partial(b.build, source_root=self.source)
        out = io.StringIO()
        def runner(log, env, work):
            result = FakeRunner(log, env, work)
            result.fail = fail
            return result
        argv = ["--stage-only", "--stage", str(target), "--events"]
        if not default_work:
            argv += ["--work", str(self.paths.work)]
        if not remembered:
            argv += ["--pc", str(self.pc), "--dest", str(self.dest / (b.PUBLIC_NAME + ".app"))]
        argv += list(extra)
        with self.pipeline_stubs(), mock.patch.object(b, "ROOT", self.source), \
             mock.patch.object(b, "build", side_effect=build), \
             mock.patch.object(b, "Runner", side_effect=runner), mock.patch("sys.stdout", out):
            rc = b.main(argv)
        return rc, [json.loads(line) for line in out.getvalue().splitlines()]

    def assert_ready(self, target, result):
        rc, events = result
        self.assertEqual(rc, 0, events)
        self.assertEqual(events[-1], dict(ev="ready", descriptor=str(target / "ready.json")))
        self.assertFalse(any(event["ev"] in ("done", "error") for event in events))
        self.assertEqual([e["id"] for e in events if e["ev"] == "stage"],
                         [name for name, _ in b.STAGES[:-1]] + ["stage"])
        record = json.loads((target / "ready.json").read_text())
        self.assertEqual((record["format_version"], record["version"]), (1, b.PUBLIC_VERSION))
        for key in ("app", "data"):
            self.assertEqual(record["hashes"][key], ins.digest_tree(target / record[key]))
        self.assertEqual(record["hashes"]["roots"], b.sha_file(target / "roots.txt"))
        for root in record["roots"].values():
            self.assertFalse(Path(root).is_absolute())
            self.assertTrue((target / root).is_dir())
            self.assertTrue((target / root).resolve().is_relative_to(target))
        self.assertNotIn(str(self.paths.work), (target / "roots.txt").read_text())
        return record

    def assert_failed(self, target, result):
        rc, events = result
        self.assertNotEqual(rc, 0, events)
        self.assertEqual(events[-1]["ev"], "error")
        self.assertFalse(any(e["ev"] in ("ready", "done") for e in events))
        self.assertFalse((target / "ready.json").exists())

    def protected_hashes(self):
        return (ins.digest_tree(self.source), ins.digest_tree(self.pc),
                ins.digest_tree(self.paths.data), self.snapshot())

    def assert_xbox(self, target, result):
        record = self.assert_ready(target, result)
        self.assertEqual(record["provenance"]["x360"], str(self.xbox))
        self.assertIn("x360_root", record["roots"])
        self.assertEqual((target / record["roots"]["x360_root"] / "extras.bin").read_bytes(), b"Xbox extras")

    def xbox_prepare(self, source, out, pc, events, env, record=None):
        if source is None:
            events.emit("features", x360=False, missing=b.X360_MISSING)
            return {}
        self.assertEqual(source, self.xbox)
        write(out / "files/extras.bin", b"Xbox extras")
        events.emit("features", x360=True, missing=[])
        return dict(x360_root=out / "files")

    def remember_xbox(self):
        self.xbox = self.home / "original Xbox.iso"
        write(self.xbox, b"synthetic original Xbox input")
        args = self.args()
        args.x360 = str(self.xbox)
        with self.pipeline_stubs(), mock.patch.object(b, "prepare_x360", side_effect=self.xbox_prepare):
            b.build(args, self.paths, self.events, FakeRunner(self.log, self.child_env, self.paths.work), self.source)
        saved = s.load_choices(self.paths.state, required=True)
        self.assertEqual(saved, dict(format_version=1, pc=str(self.pc), x360=str(self.xbox),
                                    destination=str(self.dest / (b.PUBLIC_NAME + ".app"))))
        return saved

    def test_stage_locked_profile_preserves_every_protected_hash(self):
        self.old_install()
        # Even an outstanding install journal must remain untouched during preparation.
        write(self.paths.data / ins.JOURNAL, b"pending install belongs to application/recovery")
        with ins.profile_guard(self.paths.data):
            before = self.protected_hashes()
            with mock.patch.object(b, "recover_interrupted", side_effect=AssertionError("recovery during stage")), \
                 mock.patch.object(b, "migrate_3x_saves", side_effect=AssertionError("migration during stage")):
                record = self.assert_ready(self.home / "update", self.run_stage())
            self.assertEqual(before, self.protected_hashes())
        self.assertEqual(record["destination"]["identity"], ins.ident(self.dest / (b.PUBLIC_NAME + ".app")))
        self.assertEqual(record["destination"]["sha256"], before[-1][0])
        self.assertEqual(record["provenance"]["pc"], str(self.pc))
        self.assertFalse((self.paths.state / "source-choices.json").exists())

    def test_failed_build_has_no_ready_and_failed_rebuild_mutant_is_caught(self):
        target = self.home / "failed"
        self.assert_failed(target, self.run_stage(target, fail="runtime"))
        # Broken pipeline publishes a commit marker even though the compiler failed.
        original = functools.partial(b.build, source_root=self.source)
        mutant = self.home / "failed-mutant"
        def bad_build(*args, **kwargs):
            try:
                return original(*args, **kwargs)
            except b.SetupError:
                write(mutant / "ready.json", '{}')
                raise
        with self.assertRaises(AssertionError):
            self.assert_failed(mutant, self.run_stage(mutant, fail="runtime", builder=bad_build))

    def test_early_install_mutant_fails_preservation_oracle(self):
        self.old_install()
        before = self.protected_hashes()
        self.assert_ready(self.home / "good", self.run_stage(self.home / "good"))
        self.assertEqual(before, self.protected_hashes())
        publish = s.publish
        def early_install(target, app, data, roots, destination, profile, version, provenance, **kwargs):
            ins.transactional_install(app, data, roots, profile, destination.parent, lambda app: None,
                                      running=lambda: None)
            return publish(target, app, data, roots, destination, profile, version, provenance, **kwargs)
        with mock.patch.object(s, "publish", side_effect=early_install):
            self.assert_ready(self.home / "early-mutant", self.run_stage(self.home / "early-mutant"))
        with self.assertRaises(AssertionError):
            self.assertEqual(before, self.protected_hashes())

    def test_remembered_xbox_and_lost_choice_mutant(self):
        saved = self.remember_xbox()
        choices_hash = b.sha_file(self.paths.state / "source-choices.json")
        xbox_hash = b.sha_file(self.xbox)
        with mock.patch.object(b, "prepare_x360", side_effect=self.xbox_prepare):
            target = self.home / "remembered"
            self.assert_xbox(target, self.run_stage(target, remembered=True))
            mutant = self.home / "lost-choice"
            with mock.patch.object(s, "load_choices", return_value=dict(saved, x360=None)):
                result = self.run_stage(mutant, remembered=True)
            with self.assertRaises(AssertionError):
                self.assert_xbox(mutant, result)
        self.assertEqual(b.sha_file(self.paths.state / "source-choices.json"), choices_hash)
        self.assertEqual(b.sha_file(self.xbox), xbox_hash)

    def test_missing_remembered_xbox_is_named_error_before_build(self):
        self.remember_xbox()
        self.xbox.unlink()
        with mock.patch.object(b, "preflight", side_effect=AssertionError("should not build")):
            result = self.run_stage(remembered=True)
        self.assert_failed(self.home / "update", result)
        self.assertIn("X360_SOURCE_MISSING", result[1][-1]["msg"])

    def test_importer_cannot_silently_drop_xbox(self):
        self.remember_xbox()
        with mock.patch.object(b, "prepare_x360", return_value={}):
            result = self.run_stage(remembered=True)
        self.assert_failed(self.home / "update", result)
        self.assertIn("X360_PREPARATION_MISSING", result[1][-1]["msg"])

    def test_failed_normal_setup_does_not_change_remembered_choices(self):
        self.remember_xbox()
        before = b.sha_file(self.paths.state / "source-choices.json")
        runner = FakeRunner(self.log, self.child_env, self.paths.work)
        runner.fail = "bundle"
        with self.pipeline_stubs(), self.assertRaises(b.SetupError):
            b.build(self.args(), self.paths, self.events, runner, self.source)
        self.assertEqual(b.sha_file(self.paths.state / "source-choices.json"), before)

    def test_missing_and_damaged_choices_are_named_errors(self):
        result = self.run_stage(remembered=True)
        self.assert_failed(self.home / "update", result)
        self.assertIn("SOURCE_CHOICES_MISSING", result[1][-1]["msg"])
        damaged = [[], dict(format_version=1, pc=str(self.pc), destination=str(self.dest)),
                   dict(format_version=1, pc=str(self.pc), destination=str(self.dest), x360="")]
        for choices in damaged:
            with self.subTest(choices=choices):
                write(self.paths.state / "source-choices.json", json.dumps(choices))
                result = self.run_stage(remembered=True)
                self.assert_failed(self.home / "update", result)
                self.assertIn("SOURCE_CHOICES_INVALID", result[1][-1]["msg"])

    def test_explicit_inputs_ignore_corrupt_choices(self):
        write(self.paths.state / "source-choices.json", "{broken")
        self.xbox = self.home / "original Xbox.iso"
        write(self.xbox, b"synthetic Xbox input")
        with mock.patch.object(b, "prepare_x360", side_effect=self.xbox_prepare):
            self.assert_xbox(self.home / "update", self.run_stage(extra=("--x360", str(self.xbox))))
        self.assertEqual((self.paths.state / "source-choices.json").read_text(), "{broken")

    def test_stage_log_links_preserve_preferences_on_build_failure(self):
        self.old_install()
        preferences = self.paths.data / "app.txt"
        before = self.protected_hashes()
        pointer = self.paths.work / "run-log.txt"
        for link in ("symlink", "hardlink"):
            with self.subTest(link=link):
                pointer.unlink(missing_ok=True)
                if link == "symlink":
                    pointer.symlink_to(preferences)
                else:
                    os.link(preferences, pointer)
                target = self.home / link
                self.assert_failed(target, self.run_stage(target, fail="runtime"))
                self.assertEqual(before, self.protected_hashes())
                self.assertFalse(pointer.is_symlink())
                self.assertNotEqual(ins.ident(pointer), ins.ident(preferences))
                self.assertTrue(Path(pointer.read_text().strip()).is_file())

    def test_remembered_destination_is_validated_before_hashing(self):
        app = self.dest / (b.PUBLIC_NAME + ".app")
        s.save_choices(self.paths.state, self.pc, None, app)
        self.plant_app(app, "4.0.0")
        moved = self.home / "moved.app"
        app.rename(moved)
        for kind in ("moved", "empty", "foreign", "private"):
            with self.subTest(kind=kind):
                if kind == "empty":
                    app.mkdir()
                elif kind in ("foreign", "private"):
                    self.plant_app(app, "4.0.0", bundle_id="example.foreign" if kind == "foreign" else ins.OWNER_BUNDLE_ID)
                target = self.home / kind
                with mock.patch.object(s, "digest_tree", side_effect=AssertionError("hashed before validation")), \
                     mock.patch.object(b, "preflight", side_effect=AssertionError("built before validation")):
                    result = self.run_stage(target, remembered=True)
                self.assert_failed(target, result)
                self.assertIn("STAGE_DESTINATION_INVALID", result[1][-1]["msg"])
                self.assertIn("--dest", result[1][-1]["fix"])
        self.assertTrue(moved.is_dir())

    def test_late_cancel_reports_durable_ready(self):
        publish, sync_dir, tool_tmp = s.publish, s.sync_dir, b.tool_tmp
        for window in ("after-publish", "final-sync", "cleanup"):
            with self.subTest(window=window):
                target = self.home / window
                def late_publish(*args, **kwargs):
                    ready = publish(*args, **kwargs)
                    raise b.Cancelled()
                def late_sync(path):
                    sync_dir(path)
                    if Path(path) == target and (target / "ready.json").exists():
                        # Real cancellation at the durability boundary must not split
                        # publication from the caller's committed-state notification.
                        import signal
                        signal.raise_signal(signal.SIGTERM)
                @contextmanager
                def late_cleanup(*args, **kwargs):
                    with tool_tmp(*args, **kwargs) as tmp:
                        yield tmp
                    raise b.Cancelled()
                patch = {"after-publish": mock.patch.object(s, "publish", side_effect=late_publish),
                         "final-sync": mock.patch.object(s, "sync_dir", side_effect=late_sync),
                         "cleanup": mock.patch.object(b, "tool_tmp", side_effect=late_cleanup)}[window]
                with patch:
                    result = self.run_stage(target)
                self.assert_ready(target, result)
                self.assertEqual(sum(event["ev"] == "ready" for event in result[1]), 1)

    def test_no_work_argument_and_no_active_install(self):
        record = self.assert_ready(self.home / "update", self.run_stage(default_work=True))
        self.assertIsNone(record["destination"]["identity"])
        self.assertFalse(self.dest.exists())
        self.assertFalse(self.paths.data.exists())

    def test_protected_stage_locations_rejected_before_writes(self):
        self.old_install()
        for target in (self.pc / "new", self.source / "new", self.paths.data / "new",
                       self.dest / (b.PUBLIC_NAME + ".app") / "new", self.paths.work / "new",
                       self.paths.cache / "new", self.paths.legacy / "new"):
            with self.subTest(target=target):
                before = self.protected_hashes()
                result = self.run_stage(target)
                self.assert_failed(target, result)
                self.assertIn("overlaps", result[1][-1]["msg"])
                self.assertEqual(before, self.protected_hashes())
                self.assertFalse(target.exists())

    def test_ready_is_last_durable_write_and_sync_failures_never_publish(self):
        target = self.home / "update"
        synced = []
        real_fsync, real_rename = os.fsync, os.rename
        def fsync(fd):
            synced.append("sync")
            return real_fsync(fd)
        def rename(src, dst):
            if Path(dst).name == "ready.json":
                self.assertFalse(Path(dst).exists())
                self.assertTrue((target / "roots.txt").is_file())
                self.assertTrue((target / "data/pc/speed.exe").is_file())
                self.assertGreater(len(synced), 10)
                synced.append("ready")
            return real_rename(src, dst)
        with mock.patch.object(s.os, "fsync", side_effect=fsync), mock.patch.object(s.os, "rename", side_effect=rename):
            self.assert_ready(target, self.run_stage(target))
        self.assertEqual(synced[-2:], ["ready", "sync"])
        bad = self.home / "sync-failure"
        with mock.patch.object(s, "sync_tree", side_effect=OSError("injected fsync failure")):
            self.assert_failed(bad, self.run_stage(bad))
        # Failure of the final directory sync must also withdraw readiness.
        bad = self.home / "last-sync-failure"
        sync_dir = s.sync_dir
        def fail_final(path):
            if (Path(path) / "ready.json").exists():
                raise OSError("injected final fsync failure")
            return sync_dir(path)
        with mock.patch.object(s, "sync_dir", side_effect=fail_final):
            self.assert_failed(bad, self.run_stage(bad))

    def test_existing_ready_job_is_never_overwritten(self):
        target = self.home / "update"
        self.assert_ready(target, self.run_stage(target))
        before = ins.digest_tree(target)
        result = self.run_stage(target, fail="runtime")
        self.assertNotEqual(result[0], 0)
        self.assertIn("already exists", result[1][-1]["msg"])
        self.assertEqual(ins.digest_tree(target), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
