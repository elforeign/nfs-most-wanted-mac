"""Launch update offline proofs (synthetic apps/data; no game or network).

Good/broken counts are printed by the suite: every durable apply, rollback and
acknowledgment boundary is killed with SIGKILL and recovered through the shared
Setup journal. Four deliberate mutants must fail the same safety oracles: missing
journal, copy/delete exchange, premature backup deletion, changed staged hash.
The normal app name must be executable at every observed boundary; recovery must
produce a whole old/new app/data/roots/preferences set and preserve save bytes.
Verified controls: 54 SIGKILL cases (21 apply, 12 rollback, 13 preference merge,
6 acknowledgment, 2 partial copies); 4/4 unsafe mutants rejected; 8/8 invalid
ready/destination cases rejected. Native harness: 4 good transfer/receipt paths,
2 guarded/unhealthy startup refusals. All 25 tests pass with synthetic signatures.
Restart arguments: correct ordering passes; 1 consumed-token ordering mutant fails.
Review regressions: private retained tools good=1, broken=12 rejected; serialization
good=2 intents/1 launch, old=2 intents/3 launches (oracle fails). Full-flush
good=2 volumes at exchange/deletion, broken=3 missing-barrier controls rejected.
Custom profile: good=3 launch paths, broken=3 missing-profile controls rejected.
Ready index: good=1 small read with 1000 retained jobs, broken scan rejected;
index publication/retirement good=2 layouts, changed retained tools rejected.
Retained imports: -I -B leaves hashes unchanged (good=1); enabling bytecode
writes changes the verified source (broken=1 rejected).
"""
import hashlib
import json
import os
from pathlib import Path
import plistlib
from contextlib import nullcontext
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from setup_kit import apply as a
from setup_kit import install as ins


def put(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body.encode() if isinstance(body, str) else body)


class Fixture:
    def __init__(self, base, manifests=True):
        self.base = Path(base)
        self.profile = self.base / "profile"
        self.dest = self.base / "Applications" / ins.APP_BUNDLE
        self.job = self.base / "job"
        self.ready = self.job / "ready.json"
        self.profile.mkdir()
        self.job.mkdir()
        self.old_data = self.base / "old-data"
        put(self.old_data / "pc/marker", "old")
        self.make_app(self.dest, "old")
        self.make_app(self.job / ins.APP_BUNDLE, "new")
        put(self.job / "data/pc/marker", "new")
        put(self.job / "data/assets/marker", "new")
        put(self.profile / "Saves/career", b"career\0\xffnever-roll-back")
        put(self.profile / "registry.txt", "resolution=latest\n")
        self.roots = f"pc_root={self.old_data}/pc\n".encode()
        self.prefs = b"# latest settings\r\naudio=surround\r\ngame_dir=" + os.fsencode(self.old_data / "pc") + b"\r\nunknown=keep\xff\r\n"
        if manifests:
            put(self.profile / "roots.txt", self.roots)
            put(self.profile / "app.txt", self.prefs)
        roots = dict(pc_root="data/pc", assets_root="data/assets")
        body = "".join(f"{k}={v}\n" for k, v in sorted(roots.items())).encode()
        put(self.job / "roots.txt", body)
        self.descriptor = dict(format_version=1, version="4.3.0", app=ins.APP_BUNDLE, data="data", roots=roots,
            hashes=dict(app=ins.digest_tree(self.job / ins.APP_BUNDLE), data=ins.digest_tree(self.job / "data"),
                        roots=hashlib.sha256(body).hexdigest()),
            destination=dict(path=str(self.dest), identity=ins.ident(self.dest), sha256=ins.digest_tree(self.dest)),
            profile=str(self.profile), provenance={"fixture": True})
        self.write_ready()
        self.saves = (self.profile / "Saves/career").read_bytes()

    def make_app(self, app, marker):
        put(app / "Contents/Info.plist", plistlib.dumps(dict(CFBundleIdentifier=ins.PUBLIC_BUNDLE_ID,
            CFBundleShortVersionString="4.3.0", CFBundleExecutable=ins.EXEC_NAME)))
        put(app / "Contents/MacOS" / ins.EXEC_NAME, "#!/bin/sh\nexit 0\n")
        (app / "Contents/MacOS" / ins.EXEC_NAME).chmod(0o755)
        put(app / "Contents/marker", marker)

    def write_ready(self):
        put(self.ready, json.dumps(self.descriptor))

    def launchable(self):
        assert self.dest.is_dir(), "normal app path disappeared"
        assert ins.app_complete(self.dest), "normal app is not launchable"

    def apply(self):
        return a.apply_ready(self.ready, self.profile, self.dest, running=lambda: None)

    def recover(self):
        return ins.recover_interrupted(self.profile)

    def receipt(self, token):
        put(a.ack_path(self.profile, token), json.dumps(dict(token=token, identity=ins.ident(self.dest))))

    def consistent(self, version=None):
        self.launchable()
        marker = (self.dest / "Contents/marker").read_text()
        assert marker in ("old", "new")
        if version:
            assert marker == version, (marker, version)
        if marker == "old":
            assert (self.profile / "roots.txt").read_bytes() == self.roots
            assert (self.profile / "app.txt").read_bytes() == self.prefs
        else:
            roots = dict(line.split("=", 1) for line in (self.profile / "roots.txt").read_text().splitlines())
            for value in roots.values():
                assert (Path(value) / "marker").read_text() == "new"
            assert (self.profile / "app.txt").read_bytes() == ins.updated_profile(self.prefs, roots["pc_root"])
        assert (self.profile / "Saves/career").read_bytes() == self.saves
        assert (self.profile / "registry.txt").read_text() == "resolution=latest\n"


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.signing = mock.patch.object(ins, "codesign_ok", return_value=True)
        self.signing.start()

    def tearDown(self):
        self.signing.stop()
        self.temp.cleanup()

    def fixture(self, name="fixture", **kw):
        path = self.root / name
        path.mkdir()
        return Fixture(path, **kw)

    def kill_at(self, fixture, action, index):
        pid = os.fork()
        if pid == 0:
            counter = 0
            def step(name):
                nonlocal counter
                fixture.launchable()
                if name != "journal-removed":
                    assert ins.journal_path(fixture.profile).exists(), "missing recovery journal"
                if counter == index:
                    os.kill(os.getpid(), signal.SIGKILL)
                counter += 1
            a._step = step
            try:
                action()
            except BaseException:
                os._exit(99)
            os._exit(98)
        _, status = os.waitpid(pid, 0)
        self.assertTrue(os.WIFSIGNALED(status), (index, status))
        self.assertEqual(os.WTERMSIG(status), signal.SIGKILL)

    def trace(self, action):
        steps = []
        with mock.patch.object(a, "_step", side_effect=steps.append):
            action()
        return steps

    def test_apply_crash_every_durable_step(self):
        baseline = self.fixture("baseline")
        steps = self.trace(baseline.apply)
        self.assertGreaterEqual(len(steps), 10)
        for index, step in enumerate(steps):
            with self.subTest(step=step):
                f = self.fixture(f"apply-{index}")
                self.kill_at(f, f.apply, index)
                f.launchable()
                f.recover()
                f.consistent("old")
                self.assertFalse(ins.journal_path(f.profile).exists())
        print(f"APPLY_CRASH good={len(steps)} broken=0 (SIGKILL boundaries)")

    def test_recovery_crash_every_durable_step(self):
        baseline = self.fixture("baseline")
        baseline.apply()
        steps = self.trace(baseline.recover)
        for index, step in enumerate(steps):
            with self.subTest(step=step):
                f = self.fixture(f"rollback-{index}")
                f.apply()
                self.kill_at(f, f.recover, index)
                f.recover()
                f.consistent("old")
        print(f"ROLLBACK_CRASH good={len(steps)} broken=0 (SIGKILL boundaries)")

    def test_ack_crash_every_durable_step(self):
        baseline = self.fixture("baseline")
        token = baseline.apply()
        baseline.receipt(token)
        steps = self.trace(lambda: a.acknowledge(baseline.profile, token))
        for index, step in enumerate(steps):
            with self.subTest(step=step):
                f = self.fixture(f"ack-{index}")
                token = f.apply()
                f.receipt(token)
                self.kill_at(f, lambda: a.acknowledge(f.profile, token), index)
                f.recover()
                f.consistent("new")
        print(f"ACK_CRASH good={len(steps)} broken=0 (SIGKILL boundaries)")

    def test_latest_preferences_and_retained_backup(self):
        f = self.fixture()
        f.prefs += b"changed_since_staging=yes\n"
        put(f.profile / "app.txt", f.prefs)
        token = f.apply()
        f.consistent("new")
        record = json.loads(ins.journal_path(f.profile).read_bytes())
        names, _, _ = a._names(f.profile, record)
        self.assertEqual((names[0][1] / "Contents/marker").read_text(), "old")
        self.assertFalse(a.ack_path(f.profile, token).exists())
        f.receipt(token)
        a.acknowledge(f.profile, token)
        self.assertFalse(names[0][1].exists())
        f.consistent("new")

    def test_failure_rolls_back(self):
        f = self.fixture()
        def fail(name):
            if name == "exchange-roots.txt":
                raise OSError("disk full")
        with mock.patch.object(a, "_step", side_effect=fail):
            with self.assertRaises(OSError):
                f.apply()
        f.consistent("old")

    def test_preferences_changed_during_startup(self):
        for outcome in ("ack", "rollback"):
            f = self.fixture(outcome)
            token = f.apply()
            # Match the runtime's atomic preference replacement (new inode).
            temporary = f.profile / ".prefs.tmp"
            put(temporary, (f.profile / "app.txt").read_bytes() + b"new_setting=keep\n")
            os.replace(temporary, f.profile / "app.txt")
            f.prefs += b"new_setting=keep\n"
            if outcome == "ack":
                f.receipt(token)
                a.acknowledge(f.profile, token)
                f.consistent("new")
            else:
                f.recover()
                f.consistent("old")

    def test_latest_preference_recovery_crashes(self):
        def prepare(f):
            f.apply()
            temporary = f.profile / ".prefs.tmp"
            put(temporary, (f.profile / "app.txt").read_bytes() + b"late=preserved\n")
            os.replace(temporary, f.profile / "app.txt")
            f.prefs += b"late=preserved\n"
        baseline = self.fixture("baseline")
        prepare(baseline)
        steps = self.trace(baseline.recover)
        self.assertIn("restore-latest-preferences", steps)
        for index, step in enumerate(steps):
            with self.subTest(step=step):
                f = self.fixture(f"late-{index}")
                prepare(f)
                self.kill_at(f, f.recover, index)
                f.recover()
                f.consistent("old")
        print(f"PREFERENCE_CRASH good={len(steps)} broken=0 (SIGKILL boundaries)")

    def test_supervisor_health_failure_and_launch_failure(self):
        class Child:
            def __init__(self, exited=False): self.exited = exited
            def poll(self): return 1 if self.exited else None
            def terminate(self): self.exited = True
            def kill(self): self.exited = True
            def wait(self, timeout=None): return 1
        for outcome in ("healthy", "no_ack", "launch_error", "bad_receipt", "hash_error"):
            f = self.fixture(outcome)
            calls = []
            child = Child()
            def launch(command, **kwargs):
                calls.append(command)
                if len(calls) == 1:
                    if outcome == "launch_error": raise OSError("cannot start replacement")
                    if outcome in ("healthy", "bad_receipt"):
                        token = command[2]
                        f.receipt(token)
                        if outcome == "bad_receipt": put(a.ack_path(f.profile, token), "{}")
                return child
            if outcome == "hash_error": put(f.job / "data/pc/marker", "changed")
            real_apply = a.apply_ready
            with mock.patch.object(a.subprocess, "Popen", side_effect=launch), mock.patch.object(a, "apply_ready", side_effect=lambda *args, **kw: real_apply(*args, running=lambda: None, **kw)):
                if outcome in ("launch_error", "bad_receipt", "hash_error"):
                    with self.assertRaises((OSError, ins.InstallError)):
                        a.supervise(f.profile, f.dest, f.ready, timeout=.02)
                    self.assertTrue((f.job / ".apply-failed.json").exists())
                else:
                    a.supervise(f.profile, f.dest, f.ready, timeout=.02)
            f.consistent("new" if outcome == "healthy" else "old")
            self.assertFalse(ins.journal_path(f.profile).exists())
            self.assertEqual(len(calls), 1 if outcome in ("healthy", "hash_error") else 2)
            if outcome in ("no_ack", "bad_receipt"):
                self.assertTrue(child.exited)

    def test_missing_manifests(self):
        f = self.fixture(manifests=False)
        f.apply()
        f.recover()
        f.launchable()
        self.assertFalse((f.profile / "app.txt").exists())
        self.assertFalse((f.profile / "roots.txt").exists())

    def test_first_launch_keeps_destination_serialized(self):
        class Child:
            def poll(self): return None
            def terminate(self): pass
            def wait(self, timeout=None): return 0
        def exercise(name, broken=False):
            f = self.fixture(name)
            intents, launches, observed = ["first"], [], []
            real_apply, real_lock = a.apply_ready, ins.dest_lock
            def launch(command, **kw):
                launches.append(command)
                if len(launches) == 1:
                    intents.append("second")
                    try:
                        a.supervise(f.profile, f.dest, f.ready, timeout=0)
                    except ins.InstallError:
                        pass
                    # The child has validated its identity, but has not taken
                    # its profile lock yet. No other helper may change roots.
                    observed.append((f.dest / "Contents/marker").read_text())
                    if observed[-1] == "new": f.receipt(command[2])
                return Child()
            # This is the old missing-supervisor-lock behavior; locks within
            # apply/recovery are retained, so it reproduces the reentry race.
            calls = 0
            def lock(dest, held=None):
                nonlocal calls
                calls += 1
                if broken and calls == 1:
                    return nullcontext()
                return real_lock(dest, held)
            with mock.patch.object(a.subprocess, "Popen", side_effect=launch), \
                    mock.patch.object(ins, "dest_lock", side_effect=lock), \
                    mock.patch.object(a, "apply_ready", side_effect=lambda *args, **kw: real_apply(*args, running=lambda: None, **kw)):
                a.supervise(f.profile, f.dest, f.ready, timeout=.1)
            if broken:
                self.assertEqual((len(intents), len(launches), observed), (2, 3, ["old"]))
                print("FIRST_LAUNCH_OLD intents=2 launches=3 roots=old")
            self.assertEqual((len(intents), len(launches)), (2, 1))
            f.consistent("new")
        exercise("serialized")
        with self.assertRaises(AssertionError): exercise("old-reentry", broken=True)
        print("FIRST_LAUNCH_SERIALIZATION good=2 intents/1 launch broken=reentry rejected")

    def test_custom_profile_on_all_supervisor_launches(self):
        class Child:
            def poll(self): return None
            def terminate(self): pass
            def wait(self, timeout=None): return 0
        for outcome in ("healthy", "timeout", "exception"):
            f = self.fixture(outcome)
            calls = []
            real_apply = a.apply_ready
            def launch(command, **kw):
                calls.append(kw)
                self.assertEqual(kw["env"]["BOOT_USERDATA"], str(f.profile))
                if len(calls) == 1 and outcome == "healthy": f.receipt(command[2])
                if len(calls) == 1 and outcome == "exception": raise OSError("launch failed")
                return Child()
            with mock.patch.dict(os.environ, {"BOOT_USERDATA": "/wrong-profile"}), \
                    mock.patch.object(a.subprocess, "Popen", side_effect=launch), \
                    mock.patch.object(a, "apply_ready", side_effect=lambda *args, **kw: real_apply(*args, running=lambda: None, **kw)):
                if outcome == "exception":
                    with self.assertRaises(OSError): a.supervise(f.profile, f.dest, f.ready, timeout=.01)
                else:
                    a.supervise(f.profile, f.dest, f.ready, timeout=.01)
            self.assertEqual(len(calls), 1 if outcome == "healthy" else 2)
            old = dict(calls[-1], env={k: v for k, v in calls[-1]["env"].items() if k != "BOOT_USERDATA"})
            with self.assertRaises(AssertionError): self.assertEqual(old["env"].get("BOOT_USERDATA"), str(f.profile))
        print("CUSTOM_PROFILE good=3 paths broken=3 rejected")

    def test_full_flush_before_exchanges_and_backup_deletion(self):
        real_full_sync = ins.full_sync
        real_fsync, real_fcntl = ins.os.fsync, ins.fcntl.fcntl
        events = []
        def fsync(fd):
            events.append(("fsync", os.fstat(fd).st_dev, os.fstat(fd).st_ino))
            return real_fsync(fd)
        def fcntl(fd, op, *args):
            if op == ins.fcntl.F_FULLFSYNC:
                events.append(("full", os.fstat(fd).st_dev, os.fstat(fd).st_ino))
            return real_fcntl(fd, op, *args)
        def exercise(name, broken=None):
            f = self.fixture(name)
            paths = {tuple(ins.ident(p)) for p in (f.profile, f.dest.parent)}
            seen = set()
            real_sync_volumes, real_exchange, real_remove = ins.sync_volumes, ins.exchange, ins.remove
            def volumes(profile, dest):
                events.clear()
                if broken != "both-volumes":
                    real_sync_volumes(profile, dest)
                else:
                    ins.sync_dir(profile)
                full = {item[1:] for item in events if item[0] == "full"}
                self.assertTrue(paths <= full, (paths, events))
                for i, item in enumerate(events):
                    if item[0] == "full": self.assertIn(("fsync", *item[1:]), events[:i])
                seen.update(full)
            def exchange(src, dst):
                self.assertTrue(paths <= seen)
                return real_exchange(src, dst)
            def remove(path):
                if Path(path).name.startswith("." + ins.APP_BUNDLE + ".new-"):
                    self.assertTrue(paths <= seen)
                return real_remove(path)
            def full(fd):
                if broken == "fsync-only": return real_fsync(fd)
                return real_full_sync(fd)
            with mock.patch.object(ins.os, "fsync", side_effect=fsync), \
                    mock.patch.object(ins.fcntl, "fcntl", side_effect=fcntl), \
                    mock.patch.object(ins, "full_sync", side_effect=full), \
                    mock.patch.object(ins, "sync_volumes", side_effect=volumes), \
                    mock.patch.object(ins, "exchange", side_effect=exchange), \
                    mock.patch.object(ins, "remove", side_effect=remove):
                token = f.apply()
                f.receipt(token)
                seen.clear()
                if broken == "cleanup":
                    with mock.patch.object(ins, "sync_volumes"):
                        a.acknowledge(f.profile, token)
                else:
                    a.acknowledge(f.profile, token)
            f.consistent("new")
        exercise("full-sync")
        for broken in ("fsync-only", "both-volumes", "cleanup"):
            with self.assertRaises(AssertionError): exercise(broken, broken)
        print("FULL_FLUSH good=2 volumes/exchange+cleanup broken=3 rejected")

    def test_ready_index_publication_retirement_and_tools(self):
        for layout in ("flat", "nested"):
            f = self.fixture(layout)
            f.job.chmod(0o700)
            if layout == "nested":
                stage = f.job / "staged"
                stage.mkdir()
                for item in (f.ready, f.job / "data", f.job / ins.APP_BUNDLE, f.job / "roots.txt"):
                    item.rename(stage / item.name)
                f.ready = stage / "ready.json"
            put(f.job / "source/tools/setup_kit/apply.py", "# verified source")
            put(f.job / "python/bin/python3", "verified interpreter")
            record = a.publish_ready(f.ready, f.profile, f.dest)
            index = f.profile / a.READY_INDEX
            self.assertEqual(index.stat().st_mode & 0o777, 0o600)
            self.assertLess(index.stat().st_size, 16384)
            self.assertEqual(record["tools"], {name: ins.digest_tree(f.job / name) for name in ("source", "python")})
            put(f.job / "source/tools/setup_kit/imported.py", "changed after verification")
            with self.assertRaises(ins.InstallError): f.apply()
            (f.job / "source/tools/setup_kit/imported.py").unlink()
            token = f.apply()
            self.assertEqual(json.loads(ins.journal_path(f.profile).read_bytes())["tools"], record["tools"])
            f.receipt(token)
            a.acknowledge(f.profile, token)
            self.assertFalse(index.exists())
        print("READY_INDEX good=2 layouts changed-tool rejection=2")

    def test_native_retained_job_trust(self):
        source = self.root / "trust.m"
        put(source, '''#import <Cocoa/Cocoa.h>
#include "''' + str(ROOT / "runtime/pub_update_launch.m") + '''"
const char *game_dir;
int main(int argc, char **argv) { @autoreleasepool {
    (void)argc;
    NSString *job = [NSString stringWithUTF8String:argv[1]];
    BOOL ok = getenv("BROKEN_JOB") ? PlainDirectory(job) :
        (!strcmp(argv[2], "owner") ? SecurePath(job, NO) :
         TrustedJob(job, UpdateJSON([NSString stringWithUTF8String:argv[2]])));
    return ok ? 0 : 1;
} }
''')
        executable = self.root / "trust"
        build = subprocess.run(["clang", "-fobjc-arc", "-Wall", "-Wextra", "-Werror", str(source),
            "-framework", "Cocoa", "-o", str(executable)], capture_output=True)
        self.assertEqual(build.returncode, 0, build.stderr.decode()[-3000:])
        def run(job, hashes, broken=False):
            return subprocess.run([str(executable), str(job), str(hashes)],
                env=dict(os.environ, **({"BROKEN_JOB": "1"} if broken else {})), capture_output=True).returncode
        def fixture(name):
            job = self.root / name
            job.mkdir(mode=0o700)
            for relative in ("source/tools/setup_kit/apply.py", "source/tools/setup_kit/imported.py",
                             "python/bin/python3", "python/lib/libpython.dylib"):
                put(job / relative, "verified tool " + relative)
            hashes = self.root / (name + ".json")
            put(hashes, json.dumps({key: ins.digest_tree(job / key) for key in ("source", "python")}))
            return job, hashes
        job, hashes = fixture("trusted")
        self.assertEqual(run(job, hashes), 0)
        cases = ("mode", "file-link", "directory-link", "script", "import", "python", "library",
                 "writable", "job-acl", "child-acl", "fifo")
        for case in cases:
            with self.subTest(case=case):
                job, hashes = fixture(case)
                if case == "mode": job.chmod(0o755)
                if case == "file-link": (job / "staged-link").symlink_to(hashes)
                if case == "directory-link": (job / "staged-link").symlink_to(self.root, target_is_directory=True)
                targets = dict(script="source/tools/setup_kit/apply.py", **{"import": "source/tools/setup_kit/imported.py"},
                               python="python/bin/python3", library="python/lib/libpython.dylib")
                if case in targets: put(job / targets[case], "untrusted replacement")
                if case == "writable": (job / "python/lib").chmod(0o777)
                if case.endswith("acl"):
                    target = job if case == "job-acl" else job / "python/bin/python3"
                    subprocess.run(["/bin/chmod", "+a", "everyone allow write", str(target)], check=True, capture_output=True)
                if case == "fifo": os.mkfifo(job / "pipe")
                self.assertEqual(run(job, hashes), 1, case)
                self.assertEqual(run(job, hashes, broken=True), 0, case)
                if case.endswith("acl"): subprocess.run(["/bin/chmod", "-N", str(target)], check=True, capture_output=True)
        # A root-owned, non-writable plain directory isolates the uid check.
        self.assertEqual(run("/bin", "owner"), 1)
        self.assertEqual(run("/bin", "owner", broken=True), 0)
        hook = (ROOT / "runtime/pub_update_launch.m").read_text()
        self.assertLess(hook.index("if (!TrustedJob(job, tools))"), hook.index("[helper launchAndReturnError:"))
        print(f"NATIVE_JOB_TRUST good=1 broken={len(cases) + 1} rejected before execution")

    def test_retained_imports_do_not_change_pinned_hashes(self):
        source = self.root / "retained-source/tools/setup_kit"
        source.mkdir(parents=True)
        for name in ("apply.py", "install.py"):
            shutil.copy2(ROOT / "tools/setup_kit" / name, source / name)
        before = ins.digest_tree(source)
        command = [sys.executable, "-I", "-B", str(source / "apply.py"), "--help"]
        result = subprocess.run(command, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(ins.digest_tree(source), before)
        # The local test interpreter may itself disable bytecode by default.
        # Explicitly restore the normal interpreter's writable import behavior.
        command = [sys.executable, "-I", "-c",
            "import runpy,sys; sys.dont_write_bytecode=False; "
            "sys.argv=[sys.argv[1], '--help']; runpy.run_path(sys.argv[0], run_name='__main__')",
            str(source / "apply.py")]
        result = subprocess.run(command, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertTrue(list(source.rglob("*.pyc")))
        with self.assertRaises(AssertionError): self.assertEqual(ins.digest_tree(source), before)
        print("RETAINED_IMPORTS good=1 unchanged hash broken=1 bytecode mutation rejected")

    def test_normal_startup_reads_one_index_without_scanning_jobs(self):
        home = self.root / "home"
        updates = home / "Library/Application Support/NFSMW-Setup/updates"
        updates.mkdir(parents=True)
        for i in range(1000):
            (updates / f"retained-{i:04}").mkdir()
        f = self.fixture()
        index = f.profile / a.READY_INDEX
        put(index, "{}")
        index.chmod(0o600)
        hook = (ROOT / "runtime/pub_update_launch.m").read_text()
        # Restore the old eager journal read and historical-job scan as a
        # deliberate broken control. This remains stable across later commits.
        begin = hook.index('        NSString *index = [profile stringByAppendingPathComponent:@".update-ready.json"];')
        end = hook.index("    if (!job && !needsRecovery) return;", begin)
        old = hook[:begin] + '''
        NSString *updates = CanonicalPath([NSHomeDirectory() stringByAppendingPathComponent:@"Library/Application Support/NFSMW-Setup/updates"]);
        for (NSString *name in [[files contentsOfDirectoryAtPath:updates error:nil] sortedArrayUsingSelector:@selector(compare:)]) {
            NSString *folder = [updates stringByAppendingPathComponent:name];
            for (NSString *relative in @[@"staged/ready.json", @"ready.json"]) {
                NSString *candidate = [folder stringByAppendingPathComponent:relative];
                NSDictionary *descriptor = UpdateJSON(candidate);
                NSDictionary *bound = descriptor[@"destination"];
                if ([bound isKindOfClass:NSDictionary.class] && [descriptor[@"profile"] isEqual:profile] && [bound[@"path"] isEqual:destination]) {
                    ready = candidate; job = folder; break;
                }
            }
            if (ready) break;
        }
    }
''' + hook[end:]
        old = old.replace("NSDictionary *pending = needsRecovery && SecurePath(journal, NO) ? UpdateJSON(journal) : nil;",
                          "NSDictionary *pending = UpdateJSON(journal);")
        # Instrument the production JSON reader, not a model of its algorithm.
        def build(source, name):
            marker = "static NSDictionary *UpdateJSON(NSString *path) {"
            self.assertIn(marker, source)
            source = source.replace(marker, "static unsigned jsonReads;\n" + marker + "\n    ++jsonReads;", 1)
            source += '''
const char *game_dir;
int main(int argc, char **argv) { @autoreleasepool {
    pub_update_launch(&argc, &argv, getenv("FIXTURE_PROFILE"));
    printf("JSON_READS=%u\\n", jsonReads);
    return 0;
} }
'''
            path = self.root / (name + ".m")
            put(path, source)
            exe = self.root / name
            result = subprocess.run(["clang", "-fobjc-arc", "-Wall", "-Wextra", "-Werror", str(path),
                "-framework", "Cocoa", "-o", str(exe)], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr.decode()[-2000:])
            return exe
        def run(exe):
            shutil.copy2(exe, f.dest / "Contents/MacOS" / ins.EXEC_NAME)
            env = dict(os.environ, CFFIXED_USER_HOME=str(home), HOME=str(home), FIXTURE_PROFILE=str(f.profile))
            env.pop("BOOT_BACKGROUND", None)
            env.pop("BOOT_DEADLINE", None)
            result = subprocess.run([str(f.dest / "Contents/MacOS" / ins.EXEC_NAME)],
                                    env=env, capture_output=True, timeout=120)  # hang guard only; Cocoa launch under a loaded suite can exceed 10 s
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            return int(result.stdout.split(b"JSON_READS=")[1].splitlines()[0])
        self.assertEqual(run(build(hook, "indexed-launch")), 1)
        old_reads = run(build(old, "scanning-launch"))
        self.assertEqual(old_reads, 2001)
        with self.assertRaises(AssertionError): self.assertLessEqual(old_reads, 1)
        index.unlink()
        self.assertEqual(run(self.root / "indexed-launch"), 0)
        print(f"STARTUP_INDEX good=1 small read (0 absent) broken={old_reads} reads/1000 jobs rejected")

    def test_missing_original_preferences_keep_new_settings(self):
        f = self.fixture(manifests=False)
        f.apply()
        temporary = f.profile / ".prefs.tmp"
        put(temporary, (f.profile / "app.txt").read_bytes() + b"new_setting=keep\n")
        os.replace(temporary, f.profile / "app.txt")
        f.recover()
        f.launchable()
        self.assertEqual((f.profile / "app.txt").read_bytes(), b"new_setting=keep\n")
        self.assertFalse((f.profile / "roots.txt").exists())

    def test_killed_during_partial_copy(self):
        for artifact in ("data", "app"):
            f = self.fixture(artifact)
            copytree = shutil.copytree
            def interrupted(src, dst, *args, **kwargs):
                if Path(src) == f.job / ("data" if artifact == "data" else ins.APP_BUNDLE):
                    put(Path(dst) / "torn-copy", b"partial")
                    a._step("partial-copy")
                    raise AssertionError("the copy fault did not kill the child")
                return copytree(src, dst, *args, **kwargs)
            def kill_partial(name):
                f.launchable()
                if name == "partial-copy": os.kill(os.getpid(), signal.SIGKILL)
            pid = os.fork()
            if pid == 0:
                with mock.patch.object(a, "_step", side_effect=kill_partial), mock.patch.object(shutil, "copytree", side_effect=interrupted):
                    try: f.apply()
                    except BaseException: os._exit(99)
                os._exit(98)
            _, status = os.waitpid(pid, 0)
            self.assertTrue(os.WIFSIGNALED(status), status)
            self.assertEqual(os.WTERMSIG(status), signal.SIGKILL)
            f.recover()
            f.consistent("old")
        print("PARTIAL_COPY good=2 broken=0 (SIGKILL during app/data copies)")

    def test_nested_staging_layout(self):
        f = self.fixture()
        nested = f.job / "staged"
        nested.mkdir()
        for item in (f.ready, f.job / "data", f.job / ins.APP_BUNDLE, f.job / "roots.txt"):
            item.rename(nested / item.name)
        f.ready = nested / "ready.json"
        f.apply()
        record = json.loads(ins.journal_path(f.profile).read_bytes())
        self.assertEqual(record["job"], str(f.job))
        f.recover()
        f.consistent("old")

    def test_saves_are_never_staging_or_generation_storage(self):
        f = self.fixture("profile-stage")
        unsafe = f.profile / "Saves/update"
        f.job.rename(unsafe)
        f.ready = unsafe / "ready.json"
        with self.assertRaises(ins.InstallError): f.apply()
        self.assertEqual((f.profile / "Saves/career").read_bytes(), f.saves)
        f = self.fixture("linked-installs")
        (f.profile / "installs").symlink_to(f.profile / "Saves", target_is_directory=True)
        with self.assertRaises(ins.InstallError): f.apply()
        f.consistent("old")

    def test_invalid_stage_and_destination(self):
        changes = ["data", "app", "roots", "identity", "destination_hash", "profile", "traversal", "symlink"]
        for change in changes:
            with self.subTest(change=change):
                f = self.fixture(change)
                if change == "data": put(f.job / "data/pc/marker", "changed")
                if change == "app": put(f.job / ins.APP_BUNDLE / "Contents/marker", "changed")
                if change == "roots": put(f.job / "roots.txt", "pc_root=elsewhere\n")
                if change == "identity":
                    backup = f.dest.with_name("replaced.app")
                    f.dest.rename(backup)
                    shutil.copytree(backup, f.dest)
                if change == "destination_hash": put(f.dest / "Contents/changed", "other app contents")
                if change == "profile": f.descriptor["profile"] = str(f.base / "other")
                if change == "traversal": f.descriptor["roots"]["pc_root"] = "../old-data/pc"
                if change == "symlink":
                    (f.job / "data/link").symlink_to(f.old_data, target_is_directory=True)
                if change in ("profile", "traversal"): f.write_ready()
                with self.assertRaises(ins.InstallError): f.apply()
                self.assertEqual((f.dest / "Contents/marker").read_text(), "old")
                self.assertFalse(ins.journal_path(f.profile).exists())
        print(f"READY_VALIDATION good=1 broken={len(changes)} rejected")

    def test_profile_and_destination_serialization(self):
        for lock in ("profile", "destination"):
            f = self.fixture(lock)
            pid = os.fork()
            if pid == 0:
                guard = ins.profile_guard(f.profile) if lock == "profile" else ins.dest_lock(f.dest.parent)
                with guard:
                    put(f.base / "locked", "yes")
                    signal.pause()
                os._exit(0)
            try:
                import time
                deadline = time.monotonic() + 5
                while not (f.base / "locked").exists() and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue((f.base / "locked").exists())
                with self.assertRaises(ins.InstallError): f.apply()
                f.consistent("old")
            finally:
                os.kill(pid, signal.SIGTERM)
                os.waitpid(pid, 0)

    def test_four_required_mutants_fail(self):
        rejected = 0
        f = self.fixture("missing-journal")
        def journal_oracle(name):
            assert ins.journal_path(f.profile).exists(), "missing journal"
        with mock.patch.object(ins, "write_journal"), mock.patch.object(a, "_step", side_effect=journal_oracle):
            with self.assertRaises(AssertionError): f.apply()
        rejected += 1
        f = self.fixture("non-atomic")
        def copy_delete(src, dst):
            aside = src.with_name(src.name + ".aside")
            if src.is_dir(): shutil.copytree(src, aside)
            else: shutil.copy2(src, aside)
            ins.remove(src)
            f.launchable()  # same always-present oracle observes the deliberate gap
            os.rename(dst, src)
            os.rename(aside, dst)
        with mock.patch.object(ins, "exchange", side_effect=copy_delete):
            with self.assertRaises((AssertionError, ins.InstallError)): f.apply()
        rejected += 1
        f = self.fixture("early-backup-delete")
        original = a._journal
        def premature(profile, record, state):
            original(profile, record, state)
            if state == "awaiting_ack":
                names, _, _ = a._names(profile, record)
                ins.remove(names[0][1])
                assert names[0][1].is_dir(), "backup was removed before startup acknowledgment"
        with mock.patch.object(a, "_journal", side_effect=premature):
            with self.assertRaises((AssertionError, ins.InstallError)): f.apply()
        rejected += 1
        f = self.fixture("staged-hash")
        put(f.job / "data/pc/marker", "changed after ready")
        def hash_oracle():
            try: f.apply()
            except ins.InstallError: return
            raise AssertionError("application accepted a changed staged hash")
        hash_oracle()  # good implementation rejects the change
        f.consistent("old")
        original_digest = ins.digest_tree
        def bypass_data_hash(path):
            path = Path(path)
            if path == f.job / "data" or path.parent.name == "installs":
                return f.descriptor["hashes"]["data"]
            return original_digest(path)
        with mock.patch.object(ins, "digest_tree", side_effect=bypass_data_hash):
            with self.assertRaises(AssertionError): hash_oracle()
        rejected += 1
        self.assertEqual(rejected, 4)
        print(f"APPLY_MUTANTS good=1 broken={rejected} rejected")

    def test_launch_hook_build_and_order(self):
        app = (ROOT / "runtime/app.m").read_text()
        start = app[app.index("const char *app_start("):]
        self.assertLess(start.index("pub_update_launch(&argc"), start.index("profile_lock();"))
        self.assertLess(start.index("pub_update_launch(&argc"), start.index('app_pref("game_dir")'))
        self.assertLess(start.index("pub_update_launch(&argc"), start.index("restart_capture(argv)"))
        # Broken ordering preserves a consumed health token in restart_argv.
        broken = "restart_capture(argv);\n" + start.replace("restart_capture(argv)", "removed_capture", 1)
        with self.assertRaises(AssertionError):
            self.assertLess(broken.index("pub_update_launch(&argc"), broken.index("restart_capture(argv)"))
        self.assertIn("pub_update_launch.m", (ROOT / "runtime/build.sh").read_text())
        output = subprocess.run(["clang", "-fobjc-arc", "-Wall", "-Wextra", "-Werror", "-c",
            str(ROOT / "runtime/pub_update_launch.m"), "-o", str(self.root / "launch.o")], capture_output=True)
        self.assertEqual(output.returncode, 0, output.stderr.decode()[-3000:])

    def test_native_transfer_and_health_receipt(self):
        # This executable is only a Foundation harness; it contains no game code.
        harness = self.root / "harness.m"
        put(harness, '''#import <Cocoa/Cocoa.h>
#include <stdio.h>
#include <stdlib.h>
extern void pub_update_launch(int *, char ***, const char *);
const char *game_dir;
int main(int argc, char **argv) { @autoreleasepool {
    fprintf(stderr, "BUNDLE=%s DEST=%s PROFILE=%s\\n", NSBundle.mainBundle.bundleIdentifier.UTF8String,
        NSBundle.mainBundle.bundlePath.UTF8String, getenv("FIXTURE_PROFILE"));
    pub_update_launch(&argc, &argv, getenv("FIXTURE_PROFILE"));
    if (!getenv("FIXTURE_UNHEALTHY")) game_dir = "/validated-fixture-pc-root";
    printf("HOME=%s\\nCONTINUE=%d\\n", NSHomeDirectory().UTF8String, argc);
    if (getenv("FIXTURE_PUMP"))
        [NSRunLoop.currentRunLoop runUntilDate:[NSDate dateWithTimeIntervalSinceNow:11]];
    return 0;
} }
''')
        executable = self.root / "launch-harness"
        build = subprocess.run(["clang", "-fobjc-arc", "-Wall", "-Wextra", "-Werror", str(harness),
            str(ROOT / "runtime/pub_update_launch.m"), "-framework", "Cocoa", "-o", str(executable)], capture_output=True)
        self.assertEqual(build.returncode, 0, build.stderr.decode()[-2000:])
        home = self.root / "home"
        home.mkdir()
        for layout in ("flat", "nested", "recovery", "ack", "unhealthy", "guarded"):
            f = self.fixture(layout)
            shutil.copy2(executable, f.dest / "Contents/MacOS" / ins.EXEC_NAME)
            environment = dict(os.environ, CFFIXED_USER_HOME=str(home), HOME=str(home), FIXTURE_PROFILE=str(f.profile))
            # The offline runner guards real games. This Foundation-only binary
            # must exercise normal transfer too; guarded refusal has its own case.
            environment.pop("BOOT_BACKGROUND", None)
            environment.pop("BOOT_DEADLINE", None)
            command = [str(f.dest / "Contents/MacOS" / ins.EXEC_NAME)]
            token = "a" * 32
            if layout in ("ack", "unhealthy", "guarded"):
                record = dict(kind=a.KIND, state="awaiting_ack", token=token,
                    ids={str(f.dest): [None, ins.ident(f.dest)]}, job=str(f.job))
                ins.write_journal(f.profile, record)
                if layout == "guarded": environment["BOOT_BACKGROUND"] = "1"
                else:
                    command += ["--port-update-ack", token]
                    environment["FIXTURE_PUMP"] = "1"
                    if layout == "unhealthy": environment["FIXTURE_UNHEALTHY"] = "1"
            else:
                job = home / "Library/Application Support/NFSMW-Setup/updates" / layout
                job.parent.mkdir(parents=True, exist_ok=True)
                f.job.rename(job)
                job.chmod(0o700)
                f.job = job
                stage = job / "staged" if layout == "nested" else job
                stage.mkdir(exist_ok=True)
                if layout == "nested":
                    for item in (job / "data", job / ins.APP_BUNDLE, job / "roots.txt"):
                        item.rename(stage / item.name)
                f.descriptor["destination"]["sha256"] = ins.digest_tree(f.dest)
                put(stage / "ready.json", json.dumps(f.descriptor))
                put(job / "source/tools/setup_kit/apply.py", "# retained verified source fixture\n")
                python = job / "python/bin/python3"
                put(python, '#!/bin/sh\nprintf "%s\\n" "$@" > "$PWD/transfer.txt"\nprintf "%s" "$BOOT_USERDATA" > "$PWD/profile.txt"\n')
                python.chmod(0o755)
                entry = a.publish_ready(stage / "ready.json", f.profile, f.dest)
                if layout == "recovery":
                    # Recovery uses the journal's pinned tools even after a
                    # previous cleanup has already retired the ready index.
                    (f.profile / a.READY_INDEX).unlink()
                    ins.write_journal(f.profile, dict(kind=a.KIND, state="awaiting_ack", token=token,
                        job=str(job), tools=entry["tools"], dest=str(f.dest.parent)))
            result = subprocess.run(command, env=environment, capture_output=True, timeout=120)  # hang guard only
            if layout == "guarded":
                self.assertEqual(result.returncode, 125, result.stderr.decode())
                self.assertFalse(a.ack_path(f.profile, token).exists())
            elif layout in ("ack", "unhealthy"):
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertIn(b"CONTINUE=1", result.stdout)
                reported_home = result.stdout.split(b"HOME=", 1)[1].splitlines()[0]
                self.assertEqual(Path(os.fsdecode(reported_home)).resolve(), home)
                if layout == "unhealthy":
                    self.assertFalse(a.ack_path(f.profile, token).exists())
                else:
                    self.assertEqual(json.loads(a.ack_path(f.profile, token).read_bytes()),
                                     dict(token=token, identity=ins.ident(f.dest)))
            else:
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertNotIn(b"CONTINUE", result.stdout, result.stderr.decode() + " expected " + str(f.dest))
                import time
                limit = time.monotonic() + 5
                while not (job / "transfer.txt").exists() and time.monotonic() < limit: time.sleep(.01)
                arguments = (job / "transfer.txt").read_text().splitlines()
                self.assertEqual(arguments[:3], ["-I", "-B", str(job / "source/tools/setup_kit/apply.py")])
                if layout == "recovery": self.assertNotIn("--ready", arguments)
                else: self.assertEqual(arguments[arguments.index("--ready") + 1], str(stage / "ready.json"))
                self.assertIn("--parent", arguments)
                self.assertEqual((job / "profile.txt").read_text(), str(f.profile))


if __name__ == "__main__":
    unittest.main(verbosity=2)
