"""Public Setup offline proofs; run with the project's Python -I.
P5 item 7: missing-supplement reports old 0/2, fixed 2/2; Carbon second folder old 0/1, fixed 1/1.
P5 item 10: MW + Carbon patcher calls old 1/2 each, fixed 2/2 each; unchanged retries reuse.
P5 item 11: plain ENOSPC + same-volume shortage old 0/2, fixed 2/2;
separate work/cache/data/destination volumes each refuse shortages (4/4).


Setup input bounds: 0/7 tests pass on 972909b3 (13 failed assertions);
7/7 pass with preflight checks for PC/Xbox/Carbon ZIPs, speed.exe, hard links and .7z listings.

Carbon PC disc CLI control: base de6085c1 fails 1/1; wired Setup passes 1/1
(CarbonSetupDiscTests.test_disc_cli_check_build_resume). Six synthetic disc
checks cover patch/executable refusal, cache changes, remembered inputs and cancellation.
Moved Carbon source: original path-only update resumes 0/1; verified installed
PC inputs resume 1/1 after the original folder is renamed.

Carbon PC archive control: ZIP/7z install and remember the source 2/2;
removing Carbon archive dispatch makes the same acceptance oracle fail 2/2.
Carbon folder/executable, changed destinations, source digests, cancellation,
cache repair and wrong-game/version/link/path/ISO refusal are covered below.

Xbox archive proof: old folder/ISO guard refuses 2/2 (.zip and .7z); archive routing accepts 2/2.
X360ArchiveTests also exercises both products, safety, cache, space and unchanged source hashes.

Carbon Xbox artwork (CarbonBuildTests, 6 new tests, 15/15 pass): dropping x360_root from the install fails 2,
refusing --x360 for Carbon fails 1.

Optional X360 regressions: original implementation fails all 6 new import/backend
tests (1 failure, 15 subtest errors); selected-output implementation passes 6/6.
Default manifests compare every path and SHA-256; eight selections, re-ticking,
repair/update persistence and selection-bound reuse are covered.

Extras proof: eight safety checks pass. Omitting the Carbon input role accepts
one unsafe work path; including it refuses all five writable roles. Original
work/cache name sets refuse both extras directories; the extended sets accept
both without deleting them. Standalone guards reject source/save overlaps,
links/traversal and a planted log-parent link before writing to the source.
Management guard: the original accepts four unsafe work paths; the fixed
guard refuses all four, including manifest/preference roots with media offline.
Per-volume checks preserve unknown sizes and refuse either full volume.

Effective TMPDIR log: original code fails 1/1; fixed code passes 1/1, with
two real child commands sharing one log entry independent of the caller TMPDIR.

Release-version proof: bundle test passes 1/1 with PUBLIC_VERSION 4.10.0;
changing it to 4.9.0 fails 1/1 against the independent 4.10.0 expectations.

Run-log link regression: good 2/2 symlink/hardlink preservation oracles pass;
broken base 2c887bc3 passes 0/2 (2 failures) after an injected runtime build failure.

T3 stub toolchain: good probe passes; missing/old clang, SDK and the actual
recompiler LLVM option fail. Bypassing preflight breaks the oracle. A toolchain
without `metal` (Command Line Tools) passes, stubbed and real (DEVELOPER_DIR=CLT):
before the fix both raised SetupError (xcrun exit 8 / 72).
The package probe with capstone module 5.0.7 / distribution 5.0.9 reports 5.0.9;
the old module-version probe fails that oracle (1/1 each).
Command line: child PATH is fixed (was the caller's); an x86_64-only `make` first
on PATH and Rosetta (sysctl.proc_translated) are refused. Before the fix the x86_64
make was accepted ("SetupError not raised"); control: an arm64 make on the same
PATH passes.
T4 synthetic PE: metadata/case variants pass; layout/.text/.rdata/.data and
missing folders fail. Ignoring .text breaks the rejection oracle.
T8 real clang, AST-isolated build_all.main (no Unicorn): injected compile error
fails and removes stale speed.a; good compilation succeeds. Old main fails oracle.
T8 fixture verification: 1/1 passes; making the good translation broken exits 1
with 1 failure. An unspecified future transform also passes with real compilation.
Generate-only: good 1/1 emits sources with zero native commands; a forced-compile
mutation is rejected 1/1 before invoking a native tool.
T9 O2/debug identity passes 1/1; the old O1 identity fails 1/1.
T9 same stage inputs resume; changed true inputs invalidate their consumers.
Stale/missing working outputs restore from a verified retained copy; missing
stamps or corrupt retained outputs rebuild. Trusting existence breaks the oracle.
T10 permission/disk-full/signing/rename/Cancel failures preserve existing app,
roots, profile and save hashes; a child/grandchild cancellation proof checks pids.
Early replacement and an orphan child both fail the preservation oracle.
Protocol golden: exact stage order, event shape, terminal done only on success;
child stdout and false success are deliberately broken controls.
Last verification (2026-10-26): 21/21 tests PASS; T3 rejects 7 broken tool
configurations (plus a bypass caught by its oracle); T4 rejects 4 changed code
regions and 5 missing directories (ignoring .text is caught); T8 good compile
returns 0, bad compile returns 1 with no archive, restored old behavior returns
0 with a stale archive and fails the oracle; T10 checks 8 transaction faults.
PC disc images (2026-11-01): synthetic ISO 9660/Joliet discs with EA's archive layout and an RTPatch 7.00 update
install like the folder from either disc, also as folders (mounted discs); the inputs keep their hashes and times. An
older or copy-protected speed.exe, a missing disc, an unsafe, missing or extra update record, a flipped bit in an
update file (its checksum), a truncated image and an archive path escaping work are refused before anything is left
in work or outside it. RTPatchOwnerTests decodes the owner's 1.3 update (NFS_PC_UPDATE): its server.dll matches the
SHA-256 recomp/server_module.py pins and a changed checksum is refused. Owner run: 4 CD images + update + speed.exe
install in 52 s (1414 files, 3.03 GB) and pass validate_pc with the supported fingerprint.
PC disc review fixes, before -> after: an ISO record with extended attributes or disagreeing endian copies 4/4
accepted -> refused; a disc folder over MAX_ROOT_FILES accepted -> refused, and the siblings read beside it 23 -> 4
(MAX_SIBLINGS 4); a folder of 2 files + 2 subfolders over MAX_ROOT_FILES 3 accepted (only files counted) ->
refused; an archive overwritten in a copied disc folder reused 1 time -> 0.
No game launch, network or real Library/Applications writes. All fixtures live
under a temporary NFSMW_SETUP_HOME. Production libraries/signing are stubbed in
pipeline tests; toolchain probes use executable stubs; compile proof uses clang.
"""
import ast
import collections
import concurrent.futures
from contextlib import contextmanager, redirect_stdout
import errno
import functools
import lzma
import zlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import signal
import struct
import subprocess
import sys
from shlex import quote as shlex_quote
import tempfile
import time
import types
import unittest
from unittest import mock

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from setup_kit import backend as b
from setup_kit import install as ins
import setup_kit


def write(path, data, executable=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode() if isinstance(data, str) else bytes(data))
    if executable:
        path.chmod(0o755)


def synthetic_pc(pc, source):
    """Use the real fingerprint algorithm with fixture-specific expected hashes."""
    spec = importlib.util.spec_from_file_location("fixture_fp", ROOT / "recomp/exe_fingerprint.py")
    fp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fp)
    regions = list(fp.REGIONS) + [(".rsrc", 0x5C7000, 256)]
    data = bytearray(1024 + sum(s for _, _, s in regions))
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HH", data, 0x84, 0x14C, len(regions))
    struct.pack_into("<H", data, 0x80 + 20, 224)
    opt = 0x80 + 24
    struct.pack_into("<H", data, opt, 0x10B)
    struct.pack_into("<I", data, opt + 16, 0x1000)
    struct.pack_into("<I", data, opt + 28, 0x400000)
    struct.pack_into("<II", data, opt + 104, 0x490100, 128)
    offsets = {}
    raw = 1024
    for i, (name, rva, size) in enumerate(regions):
        struct.pack_into("<8sIIII", data, opt + 224 + 40 * i, name.encode(), size, rva, size, raw)
        data[raw:raw + 4] = b"NFS!"
        offsets[name] = raw
        raw += size
    parts, fingerprint = fp.describe(data)
    text = (ROOT / "recomp/exe_fingerprint.py").read_text()
    for k in fp.NAMES:
        text = text.replace(fp.SUPPORTED[k], parts[k])
    text = text.replace(fp.SUPPORTED_FINGERPRINT, fingerprint).replace(fp.WHOLE_FILE_SHA256, "0" * 64)
    write(source / "recomp/exe_fingerprint.py", text)
    write(pc / "speed.exe", data)
    for name in ("GLOBAL", "TRACKS", "CARS", "FRONTEND", "SOUND"):
        write(pc / name / "fixture.bin", name)
    return bytes(data), offsets


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="NFSMW_SETUP_HOME-")
        self.home = Path(self.temp.name).resolve()
        self.env = mock.patch.dict(os.environ, {"NFSMW_SETUP_HOME": str(self.home)}, clear=False)
        self.env.start()
        for key in [k for k in os.environ if k.startswith("NFS_")]:   # caller paths read as root overrides
            del os.environ[key]
        self.source = self.home / "payload"
        for name in b.SOURCE_DIRS:
            (self.source / name).mkdir(parents=True)
        write(self.source / "setup.sh", "#!/bin/sh\n", True)
        write(self.source / "recomp/build_all.py", "# stubbed compiler driver\n")
        write(self.source / "recomp/build_inputs.py", (ROOT / "recomp/build_inputs.py").read_text() +
              "\n# The pipeline's generator packages are synthetic, like its tools.\ndef package_files(names):\n    return {}\n")
        write(self.source / "runtime/build.sh", "# stubbed linker driver\n")
        # Keep real input/stamp/output discovery, without the fixture needing
        # every converter source (the separate key proof covers that graph).
        write(self.source / "tools/gen_tables.py", (ROOT / "tools/gen_tables.py").read_text() + "\nGENERATORS = []\n")
        write(self.source / "tools/setup_kit/export.py", (ROOT / "tools/setup_kit/export.py").read_bytes())
        write(self.source / "media/build_ffmpeg.sh", "# stubbed dependency driver\n")
        write(self.source / "third_party/dl/ffmpeg-7.1.1.tar.xz", 'test dependency (not a real archive)'.encode())
        self.pc = self.home / "PC game ü with spaces"
        self.data, self.offsets = synthetic_pc(self.pc, self.source)
        self.paths = b.Paths(self.home / "work")
        self.paths.work.mkdir()
        self.codesign = mock.patch.object(ins, "codesign_ok", create=True, return_value=True)  # the fixtures' apps are unsigned
        self.codesign.start()
        ins.mark_owned(self.paths.work, self.paths.state)  # Setup's own work folder, as claim() leaves it (fixture.log lives here)
        (self.paths.work / "tmp").mkdir()
        self.dest = self.home / "Applications"
        self.log = (self.paths.work / "fixture.log").open("w+b", buffering=0)
        self.stream = io.StringIO()
        self.events = b.Events(True, self.log, self.stream)
        self.child_env = b.child_environment(self.paths.work / "stage/roots.txt", self.paths.work)
        self.package_path = list(setup_kit.__path__)
        self.toolchain_version = "fixture-v1"
        self.dependency_pin = "fixture-v1"

    def tearDown(self):
        setup_kit.__path__ = self.package_path
        for name in list(sys.modules):
            if name.startswith("setup_kit.") and name not in ("setup_kit.backend", "setup_kit.install"):
                del sys.modules[name]
        self.env.stop()
        self.codesign.stop()
        self.log.close()
        self.temp.cleanup()

    def args(self, check=False):
        return types.SimpleNamespace(pc=str(self.pc), dest=str(self.dest), x360=None,
                                     build=not check, check=check)

    def toolchain(self):
        return dict(packages={"capstone": "fixture", "numpy": "fixture", "Pillow": "fixture"}, clang=self.toolchain_version, sdk="fixture-sdk")

    @contextmanager
    def pipeline_stubs(self, runner=None):
        real_install = ins.transactional_install
        install = functools.partial(real_install, running=lambda: None)
        with mock.patch.object(b, "preflight", return_value=self.toolchain()), \
             mock.patch.object(b, "dependency_identity", return_value={"pin": self.dependency_pin}), \
             mock.patch.object(b, "check_space"), \
             mock.patch.object(b, "transactional_install", side_effect=install):
            yield

    def build(self, runner=None):
        runner = runner or FakeRunner(self.log, self.child_env, self.paths.work)
        with self.pipeline_stubs():
            return b.build(self.args(), self.paths, self.events, runner, self.source)

    def old_install(self):
        self.plant_app(self.dest / "NFS Most Wanted Native.app", "4.0.0")
        write(self.dest / "NFS Most Wanted Native.app/Contents/old", b"existing app")
        write(self.paths.data / "roots.txt", 'pc_root=/existing/game\n'.encode())
        write(self.paths.data / "app.txt", '# preserve me\n game_dir = /old/game\nfullscreen=0\nunknown=\xff\n'.encode("latin-1"))
        write(self.paths.data / "saves/career", b"existing career")

    def plant_app(self, app, version, bundle_id=None, executable="NFSMostWanted"):
        write(app / "Contents/Info.plist", plistlib.dumps(dict(CFBundleExecutable=executable,
              CFBundleIdentifier=bundle_id or b.PUBLIC_ID, CFBundleShortVersionString=version)))
        write(app / f"Contents/MacOS/{executable}", b"old " + version.encode(), True)

    def snapshot(self):
        return (ins.digest_tree(self.dest / "NFS Most Wanted Native.app"),
                tuple(b.sha_file(self.paths.data / p) for p in ("roots.txt", "app.txt", "saves/career")))


class FakeRunner:
    def __init__(self, log, env, work):
        self.log, self.env, self.work = log, env, work
        self.commands = []
        self.fail = None
        self.omit = None

    def run(self, argv, cwd=None, capture=False):
        argv = list(map(str, argv))
        self.commands.append(argv)
        self.log.write('child full log: deliberately not JSON\n'.encode())
        source = Path(cwd) if cwd else None
        text = " ".join(argv)
        name = next((n for token, n in (("recomp/build_all.py", "recompile"), ("gen_tables.py", "tables"),
                    ("media/build_ffmpeg.sh", "ffmpeg"), ("host/audio/build.sh", "audio"),
                    ("host/shaders/build.sh", "shaders"), ("runtime/build.sh", "runtime"),
                    ("runtime/bundle.sh", "bundle"), ("codesign", "sign")) if token in text), None)
        if name is not None and self.fail == name:
            raise b.SetupError(f"injected {name} failure")
        if name is not None and self.omit == name:
            if name == "recompile":
                write(source / "recomp/out/report.txt", "compile errors: 1\n")
            return ""
        if name == "recompile":
            write(source / "recomp/out/speed.a", b"verified new archive")
            write(source / "recomp/out/speed_32.c", b"fixture C")
            write(source / "recomp/out/funcs.h", b"fixture declarations")
            write(source / "recomp/out/breaker_diag.inc", b"fixture diagnostics")
        elif name == "tables":
            inputs = b.importlib.util.spec_from_file_location("fixture_inputs", source / "recomp/build_inputs.py")
            module = b.importlib.util.module_from_spec(inputs)
            inputs.loader.exec_module(module)
            generator = module.load_module(source / "tools/gen_tables.py")
            for table in list(generator.STUBS) + generator.PUBTABLES + generator.PADTABLES:
                write(source / f"runtime/gen/{table}", b"empty PC-only table")
            pc = Path(argv[argv.index("--pc") + 1])
            x360 = Path(argv[argv.index("--x360") + 1]) if "--x360" in argv else None
            write(source / "runtime/gen/inputs.txt", generator.stamp_text(pc, x360, generator.zzdata0_path(x360)))
        elif name == "ffmpeg":
            write(source / "third_party/ffmpeg/include/libavformat/avformat.h", b"dependency declarations")
            for lib in ("avformat", "avcodec", "avutil", "swresample", "swscale"):
                write(source / f"third_party/ffmpeg/lib/lib{lib}.a", b"dependency library")
        elif name == "audio":
            assert (source / "third_party/ffmpeg/lib/libavcodec.a").exists(), "FFmpeg must precede audio"
            for lib in ("hostaudio", "media"):
                write(source / f"host/audio/build/lib{lib}.a", b"audio library")
        elif name == "shaders":
            write(source / "host/shaders/build/libsmtranslate.a", b"shader library")
        elif name == "runtime":
            write(source / "runtime/boot", 'native runtime fixture'.encode(), True)
            write(source / "runtime/gen/build-fixture.json", '{"runtime": "built"}')
            write(source / "recomp/out/hook-coverage.json", '{"coverage": "checked"}')
        elif name == "bundle":
            assert argv[argv.index("--name") + 1] == b.PUBLIC_NAME and argv[argv.index("--exec-name") + 1] == "NFSMostWanted"
            app = Path(argv[2]) / "NFS Most Wanted Native.app"
            write(app / "Contents/MacOS/NFSMostWanted", 'signed runtime fixture'.encode(), True)
            write(app / "Contents/Info.plist", plistlib.dumps(dict(CFBundleExecutable="NFSMostWanted",
                  CFBundleIdentifier=b.PUBLIC_ID, CFBundleShortVersionString=b.PUBLIC_VERSION)))
        return ""


class ToolchainTests(Fixture):
    def test_runner_logs_effective_tmpdir_once_before_commands(self):
        with b.tool_tmp(self.paths.work) as tmp:
            env = b.child_environment(self.paths.work / 'stage/roots.txt', self.paths.work, tmp=tmp)
            with mock.patch.dict(os.environ, {'TMPDIR': str(self.home / 'caller-tmp')}):
                runner = b.Runner(self.log, env, self.paths.work)
                for _ in range(2):
                    output = runner.run(['/bin/sh', '-c', 'printf "%s" "$TMPDIR"'], capture=True)
                    self.assertEqual(output, str(tmp))
            self.log.seek(0)
            log = self.log.read().decode()
            self.assertEqual(log.splitlines()[0], f'tmp: {tmp}')
            self.assertEqual(log.count(f'tmp: {tmp}\n'), 1)
            self.assertNotIn(str(self.home / 'caller-tmp'), log)

    def stub_tools(self):
        tools = self.home / "stub tools"
        tools.mkdir()
        sdk = self.home / "SDK"
        sdk.mkdir()
        config = tools / "scenario.json"
        config.write_text("{}")
        script = tools / "stub.py"
        script.write_text("""import json, os, pathlib, sys
tool=sys.argv[1]; args=sys.argv[2:]; base=pathlib.Path(__file__).parent
cfg=json.loads((base/'scenario.json').read_text())
with (base/'calls.jsonl').open('a') as f: f.write(json.dumps([tool]+args)+'\\n')
if '--version' in args:
 print('Apple clang version '+('9.0.0' if cfg.get('old') else '17.0.0')); sys.exit(0)
if tool=='xcrun' and '--show-sdk-path' in args:
 print(cfg.get('sdk',SDK)); sys.exit(0)
if tool=='xcrun' and '--show-sdk-version' in args:
 print('12.0' if cfg.get('old_sdk') else '15.0'); sys.exit(0)
if cfg.get('llvm') and '-mllvm' in args: sys.exit(7)
if cfg.get('metal') and 'metal' in args: sys.exit(8)
if cfg.get('no_output'): sys.exit(0)
if '-o' in args:
 p=pathlib.Path(args[args.index('-o')+1]); p.write_bytes(b'capability probe'); p.chmod(0o755)
if tool=='ar': pathlib.Path(args[1]).write_bytes(b'archive probe')
""".replace("SDK", repr(str(sdk))))
        for tool in ("clang", "clang++", "ar", "make", "xcrun", "otool", "sips", "iconutil", "plutil", "codesign", "tar", "shasum", "sh"):
            import shlex
            write(tools / tool, "#!/bin/sh\nexec " + shlex.quote(sys.executable) + " -I " + shlex.quote(str(script)) + " " + shlex.quote(tool) + ' "$@"\n', True)
        env = dict(self.child_env, PATH=str(tools))
        return tools, config, b.Runner(self.log, env, self.paths.work)

    @unittest.skipUnless(importlib.util.find_spec('capstone') and importlib.util.find_spec('numpy') and importlib.util.find_spec('PIL'),
                         'needs the build Python (capstone, numpy, Pillow): run with Setup\'s or the tools venv interpreter')
    def test_T3_good_and_broken_tools(self):
        tools, config, runner = self.stub_tools()
        with mock.patch.object(runner, "run", wraps=runner.run) as commands:
            result = b.preflight(runner, self.paths.work)
        self.assertEqual(result["sdk_version"], "15.0")
        # Execute the real package probe against a wheel-style version mismatch.
        # This stays independent of the versions installed in the test venv.
        probe = next(c.args[0][3] for c in commands.call_args_list
                     if len(c.args[0]) > 3 and c.args[0][1:3] == ["-I", "-c"])
        def package_oracle(code):
            output = io.StringIO()
            modules = {name: types.SimpleNamespace(__version__=version)
                       for name, version in (("capstone", "5.0.7"), ("numpy", "2.0.2"), ("PIL", "fixture"))}
            with mock.patch.dict(sys.modules, modules), \
                    mock.patch("importlib.metadata.version", return_value="5.0.9"), redirect_stdout(output):
                exec(code, {})
            self.assertEqual(json.loads(output.getvalue())["capstone"], "5.0.9")
        package_oracle(probe)
        old_probe = probe.replace("version('capstone')", "capstone.__version__")
        self.assertNotEqual(old_probe, probe)
        with self.assertRaises(AssertionError):
            package_oracle(old_probe)
        calls = [json.loads(s) for s in (tools / "calls.jsonl").read_text().splitlines()]
        self.assertTrue(any(b.LLVM_FLAGS[1] in c for c in calls))
        for broken in ({"old": True}, {"sdk": "/missing/sdk"}, {"old_sdk": True}, {"llvm": True}, {"no_output": True}):
            config.write_text(json.dumps(broken))
            with self.subTest(broken=broken), self.assertRaises(b.SetupError):
                b.preflight(runner, self.paths.work)
        config.write_text("{}")
        (tools / "clang").unlink()
        def missing_oracle(probe):
            with self.assertRaises(b.SetupError):
                probe(runner, self.paths.work)
        missing_oracle(b.preflight)
        with self.assertRaises(AssertionError):
            missing_oracle(lambda *a: result)  # broken control: omit the probe

    def test_environment_drops_owner_and_injection(self):
        env = b.child_environment(self.home / "roots", self.paths.work,
              dict(PATH="/usr/bin:/bin", HOME=str(self.home), NFS_X360_FILES="/owner", NFS_PUBLIC="0",
                   NFS_ROOTS="/owner/roots", BOOT_OUT="/owner/boot", BOOT_USERDATA="/owner/profile",
                   RECOMP_CFLAGS="bad", CFLAGS="bad", PYTHONPATH="/owner", DYLD_INSERT_LIBRARIES="bad"))
        self.assertEqual({k for k in env if k.startswith("NFS_")}, {"NFS_ROOTS", "NFS_PUBLIC"})
        self.assertFalse(any(k.startswith("BOOT_") for k in env))
        self.assertEqual(env["TOOLS_PY"], os.path.abspath(sys.executable))
        self.assertEqual(env["NFS_PUBLIC"], "1")
        self.assertEqual(env["HOME"], str(self.paths.work / "tool-home"))
        self.assertNotIn("RECOMP_CFLAGS", env)
        self.assertNotIn("PYTHONPATH", env)

    @unittest.skipUnless(importlib.util.find_spec('capstone') and importlib.util.find_spec('numpy') and importlib.util.find_spec('PIL'),
                         'needs the build Python (capstone, numpy, Pillow)')
    def test_T3_command_line_tools_without_metal_compiler_pass(self):
        # The Command Line Tools have no `metal`; the build never runs it (shaders compile at run time). Stub:
        # every `xcrun ... metal` fails (exit 8, as on CLT). Before the fix: SetupError. Broken control: the old
        # probe (an extra xcrun metal step) is refused by the same oracle.
        tools, config, runner = self.stub_tools()
        config.write_text(json.dumps({"metal": True}))
        b.preflight(runner, self.paths.work)
        calls = [json.loads(s) for s in (tools / "calls.jsonl").read_text().splitlines()]
        self.assertFalse(any("metal" in c for c in calls), "preflight must not probe the Metal compiler")
        with self.assertRaises(b.SetupError):  # broken control: re-adding the probe refuses CLT-only Macs
            runner.run(["xcrun", "--sdk", "macosx", "metal", "-c", "x.metal"])

    @unittest.skipUnless(Path("/Library/Developer/CommandLineTools/usr/bin/clang").exists() and
                         importlib.util.find_spec('capstone') and importlib.util.find_spec('numpy') and importlib.util.find_spec('PIL'),
                         'needs Apple Command Line Tools and the build Python')
    def test_T3_real_command_line_tools_only(self):
        # Real tools, DEVELOPER_DIR = the Command Line Tools (env var only; xcode-select is never changed).
        env = dict(self.child_env, DEVELOPER_DIR="/Library/Developer/CommandLineTools")
        probe = subprocess.run(["/usr/bin/xcrun", "--find", "metal"], env=env, capture_output=True)
        self.assertNotEqual(probe.returncode, 0, "fixture: these Command Line Tools unexpectedly include metal")
        result = b.preflight(b.Runner(self.log, env, self.paths.work), self.paths.work)
        self.assertTrue(result["sdk"].startswith("/Library/Developer/CommandLineTools"), result["sdk"])

    def test_child_path_is_fixed(self):
        env = b.child_environment(self.home / "roots", self.paths.work,
                                  dict(PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin", HOME=str(self.home)))
        self.assertEqual(env["PATH"], "/usr/bin:/bin:/usr/sbin:/sbin")

    def x86_make(self, tools):
        src = self.home / "x86make.c"
        src.write_text('#include <stdio.h>\nint main(void){puts("GNU Make 3.81");return 0;}\n')
        built = subprocess.run(["/usr/bin/clang", "-arch", "x86_64", src, "-o", tools / "make"], capture_output=True)
        runs = built.returncode == 0 and subprocess.run([tools / "make"], capture_output=True).returncode == 0
        if not runs:
            self.skipTest("cannot build/run an x86_64 test tool (no x86_64 SDK slice or Rosetta)")

    @unittest.skipUnless(importlib.util.find_spec('capstone') and importlib.util.find_spec('numpy') and importlib.util.find_spec('PIL'),
                         'needs the build Python (capstone, numpy, Pillow)')
    def test_intel_only_tool_and_rosetta_refused(self):
        tools, config, runner = self.stub_tools()
        self.x86_make(tools)  # a real x86_64-only make first on PATH (it runs, under Rosetta)
        with self.assertRaisesRegex(b.SetupError, "Intel-only.*make"):
            b.preflight(runner, self.paths.work)
        self.assertEqual(b.machine_archs(tools / "make"), {"x86_64"})
        self.assertTrue(b.machine_archs(Path("/usr/bin/make")) & {"arm64", "arm64e"})
        (tools / "make").unlink()
        write(tools / "make", "#!/bin/sh\nexec " + shlex_quote(sys.executable) + " -I " +
              shlex_quote(str(tools / "stub.py")) + ' make "$@"\n', True)
        b.preflight(runner, self.paths.work)  # control: the same PATH with an Apple Silicon make passes
        with mock.patch.object(b, "translated", return_value=True), self.assertRaisesRegex(b.SetupError, "Rosetta"):
            b.preflight(runner, self.paths.work)
        # Real Rosetta: the same interpreter forced to x86_64 reports translation.
        out = subprocess.run(["/usr/bin/arch", "-x86_64", sys.executable, "-I", "-c",
                              "import sys; sys.path.insert(0, %r); from setup_kit import backend; print(backend.translated())" % str(ROOT / "tools")],
                             capture_output=True, text=True)
        if out.returncode == 0:
            self.assertEqual(out.stdout.strip(), "True")
        self.assertFalse(b.translated())


class PCTests(Fixture):
    def test_folder_executable_size_checked_before_fingerprint(self):
        with mock.patch.object(b.rtpatch, "MAX_FILE", len(self.data) - 1), \
                mock.patch.object(b, "exe_fingerprint") as fingerprint:
            with self.assertRaisesRegex(b.SetupError, "regular file of the expected size"):
                b.validate_pc(self.pc, self.source)
        fingerprint.assert_not_called()
        self.assertTrue(b.validate_pc(self.pc, self.source)[1])

    def test_folder_check_refuses_multiply_linked_file(self):
        original = self.pc / "extra.dat"
        original.write_bytes(b"ordinary fixture data")
        duplicate = self.pc / "extra-copy.dat"
        os.link(original, duplicate)
        runner = FakeRunner(self.log, self.child_env, self.paths.work)
        with self.pipeline_stubs():
            with self.assertRaisesRegex(ins.InstallError, "more than one hard link"):
                b.build(self.args(check=True), self.paths, self.events, runner, self.source)
            duplicate.unlink()
            b.build(self.args(check=True), self.paths, self.events, runner, self.source)

    def test_T4_metadata_case_regions_missing(self):
        original = self.data
        self.assertTrue(b.validate_pc(self.pc, self.source)[1])
        variant = bytearray(original)
        struct.pack_into("<I", variant, 0x88, 123456)
        struct.pack_into("<I", variant, 0x80 + 24 + 64, 7654321)
        variant[self.offsets[".rsrc"] + 4] ^= 1
        write(self.pc / "speed.exe", variant + b"overlay")
        b.validate_pc(self.pc, self.source)
        (self.pc / "speed.exe").rename(self.pc / "SPEED.EXE")
        (self.pc / "GLOBAL").rename(self.pc / "Global")
        b.validate_pc(self.pc, self.source)
        (self.pc / "SPEED.EXE").rename(self.pc / "speed.exe")
        for region in (".text", ".rdata", ".data", "layout"):
            changed = bytearray(original)
            offset = self.offsets[region] + 5 if region != "layout" else 0x80 + 24 + 16
            changed[offset] ^= 1
            write(self.pc / "speed.exe", changed)
            with self.subTest(region=region), self.assertRaisesRegex(b.SetupError, "differs"):
                b.validate_pc(self.pc, self.source)
        write(self.pc / "speed.exe", original)
        for folder in ("Global", "TRACKS", "CARS", "FRONTEND", "SOUND"):
            source, moved = self.pc / folder, self.home / folder
            source.rename(moved)
            with self.subTest(missing=folder), self.assertRaises(b.SetupError):
                b.validate_pc(self.pc, self.source)
            moved.rename(source)
        with self.assertRaisesRegex(b.SetupError, "open it in Finder"):
            b.validate_pc(self.home / "game.dmg", self.source)

    def test_T4_broken_region_checker_is_detected(self):
        module = self.source / "recomp/exe_fingerprint.py"
        text = module.read_text()
        text = text.replace('"""The loaded bytes [rva, rva+size) of the image (zero beyond the section\'s raw data), or None."""',
             '"""Deliberately broken control."""\n    if rva == 0x1000:\n        return b"NFS!" + bytes(size - 4)')
        module.write_text(text)
        changed = bytearray(self.data)
        changed[self.offsets[".text"] + 5] ^= 1
        write(self.pc / "speed.exe", changed)
        def oracle():
            with self.assertRaises(b.SetupError):
                b.validate_pc(self.pc, self.source)
        with self.assertRaises(AssertionError):
            oracle()


class BuildFailureTests(Fixture):
    def compile_fixture(self, text, broken=False, generate_only=False):
        tree = ast.parse(text)
        # main parses arguments; build_image holds the shared pipeline (lib removal, compile, archive, report).
        pipeline = [next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
                    for name in ("main", "build_image")]
        out = self.home / ("broken-compile" if broken else "compile")
        out.mkdir(exist_ok=True)
        write(out / "speed.a", b"stale archive")
        write(out / "runtime.h", "#include <stdint.h>\ntypedef struct {int dummy;} Cpu;\n")
        class RC:
            def __init__(self, **kw):
                self.pe = types.SimpleNamespace(fingerprint="fixture")
            def translate(self, *args):
                return ("this deliberately will not compile !!!" if broken else "void f_00000001(Cpu *c) {}"), None
        # Identity emission is covered by exe_fingerprint_test; isolate the compiler/error handling here.
        fp = types.SimpleNamespace(table_constants=lambda data, **kw: "")
        ns = dict(argparse=__import__("argparse"), os=os, sys=sys, Path=Path, collections=collections,
                  concurrent=concurrent, subprocess=subprocess, OUT=str(out), HERE=str(out), CHUNK=800,
                  build_inputs=types.SimpleNamespace(recomp_key=lambda **kw: 'fixture',
                      fresh=lambda *a: False, outputs=lambda: {}, record=lambda *a: None),
                  hook_coverage=types.SimpleNamespace(generate=lambda out: []),
                  load_functions=lambda: [(1, 2, "fixture")], add_data_referenced=lambda x: x,
                  load_hooks=lambda: {},
                  breaker_diagnostic_bodies=lambda bodies: "",
                  recomp=types.SimpleNamespace(Recompiler=RC, CFLAGS=["-O0"], exe_fingerprint=fp,
                                               PE=lambda: types.SimpleNamespace(data=b"fixture", sha256="0", fingerprint="fixture"),
                                               EXE=str(out / "speed.exe")))
        # Keep translation transforms isolated as main grows; compilation and
        # archive/error handling below still run for real.
        functions = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
        functions.update(alias.asname or alias.name for n in tree.body if isinstance(n, ast.ImportFrom)
                         for alias in n.names)
        referenced = {n.id for fn in pipeline for n in ast.walk(fn) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        for name in (functions & referenced) - ns.keys() - {"main", "build_image"}:
            ns[name] = lambda *args, **kwargs: args[-1]
        server = types.ModuleType("server_module")
        server.build = mock.Mock()
        with mock.patch.dict(sys.modules, server_module=server), \
                mock.patch.object(sys, "argv", ["test", "--jobs", "1"] +
                                  (["--generate-only"] if generate_only else [])), \
                mock.patch("sys.stdout", new=io.StringIO()):
            exec(compile(ast.Module(body=pipeline, type_ignores=[]), "build_all.py", "exec"), ns)
            try:
                ns["main"]()
                status = 0
            except SystemExit as e:
                status = e.code
        return status, out

    def test_T8_real_compile_failure_and_old_success_control(self):
        text = (ROOT / "recomp/build_all.py").read_text()
        status, out = self.compile_fixture(text)
        self.assertEqual(status, 0)
        self.assertTrue((out / "speed.a").exists())
        status, out = self.compile_fixture(text, broken=True)
        def oracle(rc, output):
            self.assertNotEqual(rc, 0)
            self.assertFalse((output / "speed.a").exists())
        oracle(status, out)
        self.assertIn("compile errors: 1", (out / "report.txt").read_text())
        # Restore the two old failure semantics without depending on git/HEAD;
        # this proof must also run from a release source archive after commits.
        old = text.replace('    if not generate_only and os.path.exists(lib):\n        os.remove(lib)\n', '', 1)
        old = old.replace('    if errors:\n        raise SystemExit(1)\n', '', 1)
        self.assertNotEqual(old, text)
        status, out = self.compile_fixture(old, broken=True)
        self.assertEqual(status, 0)
        self.assertTrue((out / "speed.a").exists())
        with self.assertRaises(AssertionError):
            oracle(status, out)

    def test_generate_only_uses_no_native_tools(self):
        text = (ROOT / "recomp/build_all.py").read_text()
        with mock.patch.object(subprocess, 'run', side_effect=AssertionError('native tool during generation')):
            status, out = self.compile_fixture(text, generate_only=True)
            self.assertEqual(status, 0)
            self.assertTrue((out / 'funcs.h').is_file())
            self.assertTrue((out / 'table.c').is_file())
            self.assertTrue((out / 'speed_00.c').is_file())
            self.assertEqual((out / 'speed.a').read_bytes(), b'stale archive')
            mutant = text.replace('    if not generate_only:\n        with concurrent.',
                                  '    if True:\n        with concurrent.', 1)
            self.assertNotEqual(mutant, text)
            with self.assertRaisesRegex(AssertionError, 'native tool during generation'):
                self.compile_fixture(mutant, generate_only=True)

    def test_T8_compile_link_sign_failure_no_install(self):
        self.old_install()
        before = self.snapshot()
        for failure in ("recompile", "runtime", "sign"):
            runner = FakeRunner(self.log, self.child_env, self.paths.work)
            runner.fail = failure
            with self.subTest(failure=failure), self.assertRaises(b.SetupError):
                self.build(runner)
            self.assertEqual(before, self.snapshot())
        runner = FakeRunner(self.log, self.child_env, self.paths.work)
        runner.omit = "recompile"
        shutil.rmtree(self.paths.cache / "stages")
        # Any stale archive at the keyed source path is removed before retry.
        for source in self.paths.cache.glob("builds/*/source"):
            write(source / "recomp/out/speed.a", b"stale archive")
        with self.assertRaisesRegex(b.SetupError, "without all required outputs"):
            self.build(runner)
        self.assertEqual(before, self.snapshot())
        runner.omit = "runtime"
        shutil.rmtree(self.paths.cache / "stages")
        for source in self.paths.cache.glob("builds/*/source"):
            write(source / "runtime/boot", b"stale runtime", True)
        with self.assertRaisesRegex(b.SetupError, "without all required outputs"):
            self.build(runner)
        self.assertEqual(before, self.snapshot())


class PrebuiltPipelineTests(Fixture):
    # No prebuilt: 3/3 source stages. Damaged SHA: 3/3 source stages.
    # Verified set: 0/3 source stages; runtime and final bundle still execute.
    # Pin forwarding: 40abf6af fails 1/1; fixed 0 failures (3 pipeline tests PASS).
    def library_commands(self, runner):
        return [a for a in runner.commands if any(name in a for name in
                ("media/build_ffmpeg.sh", "host/audio/build.sh", "host/shaders/build.sh"))]

    def test_missing_prebuilt_uses_all_source_builds(self):
        runner = FakeRunner(self.log, self.child_env, self.paths.work)
        self.build(runner)
        self.assertEqual(len(self.library_commands(runner)), 3)

    def test_failed_sha_uses_all_source_builds(self):
        folder = self.source / "prebuilt"
        write(folder / "files/library.a", b"damaged")
        write(folder / "configure.txt", b"fixture configure")
        write(folder / "manifest.json", json.dumps(dict(schema=1, target="arm64-macos14.5", source=b.prebuilt.SHA,
              recipe={}, configure=b.prebuilt.digest(folder / "configure.txt"), files={"library.a": "0" * 64})))
        runner = FakeRunner(self.log, self.child_env, self.paths.work)
        args = self.args()
        args.prebuilt_manifest_sha256 = b.prebuilt.digest(folder / 'manifest.json')
        with mock.patch.object(b.prebuilt, "check_notices"), mock.patch.object(b.prebuilt, "recipe", return_value={}), \
                mock.patch.object(self, 'args', return_value=args):
            self.build(runner)
        self.assertEqual(len(self.library_commands(runner)), 3)

    def test_verified_prebuilt_skips_only_the_library_compiles(self):
        runner = FakeRunner(self.log, self.child_env, self.paths.work)
        args = self.args()
        args.prebuilt_manifest_sha256 = '1' * 64
        def installed(root, source, log, *, expected_sha256=None):
            self.assertEqual(expected_sha256, args.prebuilt_manifest_sha256)
            write(source / "third_party/ffmpeg/include/libavformat/avformat.h", b"verified declarations")
            for name in ("avformat", "avcodec", "swscale", "swresample", "avutil"):
                write(source / f"third_party/ffmpeg/lib/lib{name}.a", b"verified compiled library")
            write(source / "host/audio/build/libhostaudio.a", b"verified host audio")
            write(source / "host/audio/build/libmedia.a", b"verified media")
            write(source / "host/shaders/build/libsmtranslate.a", b"verified translator")
            return True
        with mock.patch.object(b.prebuilt, "install", side_effect=installed), mock.patch.object(self, 'args', return_value=args):
            self.build(runner)
        self.assertEqual(self.library_commands(runner), [])
        self.assertTrue(any("runtime/build.sh" in a for a in runner.commands))
        self.assertTrue(any("runtime/bundle.sh" in a for a in runner.commands))


class CacheTests(Fixture):
    def test_T9_resume_and_every_key_component(self):
        first = FakeRunner(self.log, self.child_env, self.paths.work)
        self.build(first)
        second = FakeRunner(self.log, self.child_env, self.paths.work)
        self.build(second)
        self.assertTrue(any("recomp/build_all.py" in c for c in first.commands))
        self.assertFalse(any("recomp/build_all.py" in c for c in second.commands))
        self.assertFalse(any("runtime/build.sh" in c for c in second.commands))
        # Same-length, same-mtime mutation defeats metadata-only table caches.
        player_file = self.pc / "GLOBAL/fixture.bin"
        stat = player_file.stat()
        player_file.write_bytes(b"CHANGED")
        os.utime(player_file, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        third = FakeRunner(self.log, self.child_env, self.paths.work)
        self.build(third)
        self.assertFalse(any("recomp/build_all.py" in c for c in third.commands))
        write(self.source / "runtime/new.c", "changed source")
        fourth = FakeRunner(self.log, self.child_env, self.paths.work)
        self.build(fourth)
        self.assertTrue(any("runtime/build.sh" in c for c in fourth.commands))
        for component in ("toolchain_version",):
            setattr(self, component, "fixture-v2")
            changed = FakeRunner(self.log, self.child_env, self.paths.work)
            self.build(changed)
            self.assertTrue(any("runtime/build.sh" in c for c in changed.commands), component)
        parts = [dict(source={"a": "1"}, dependencies={"pin": "1"}, toolchain={"clang": "1"}, pc_hash="1", x360_hash="1")]
        with mock.patch.object(b, "identity", side_effect=lambda value: value):
            policy = b.cache_key(**parts[0])
        self.assertEqual(policy["cflags"], "recomp defaults; runtime -O2 -g")
        baseline = b.cache_key(**parts[0])
        keys = {baseline}
        for component in parts[0]:
            modified = dict(parts[0])
            modified[component] = "changed"
            new_key = b.cache_key(**modified)
            self.assertNotEqual(new_key, baseline)
            keys.add(new_key)
        self.assertEqual(len(keys), 6)
        with self.assertRaises(AssertionError):
            self.assertNotEqual(baseline, baseline)  # broken: ignore changed input

    def test_T9_corrupt_missing_unstamped_and_existence_control(self):
        spec = importlib.util.spec_from_file_location("fixture_inputs", self.source / "recomp/build_inputs.py")
        inputs = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(inputs)
        cache = b.BuildStageCache(self.home / "stage-cache", self.events, inputs)
        fingerprint = '1' * 64
        output = self.home / "output.a"
        calls = []
        def action():
            calls.append(1)
            write(output, b"fresh")
        def run():
            cache.run("stage", fingerprint, self.home, ["output.a"], action)
        run()
        run()
        self.assertEqual(len(calls), 1)
        write(output, b"stale")
        run()
        output.unlink()
        run()
        self.assertEqual(len(calls), 1, "working outputs restore without rebuilding")
        entry = cache.directory / "stage" / fingerprint
        (entry / "manifest.json").unlink()
        run()
        write(entry / "outputs/output.a", b"stale")
        run()
        self.assertEqual(len(calls), 3)
        write(output, b"stale")
        def existence_only():
            if not output.exists():
                action()
        existence_only()
        with self.assertRaises(AssertionError):
            self.assertEqual(output.read_bytes(), b"fresh")


class TransactionTests(Fixture):
    def prepared(self):
        self.old_install()
        app = self.home / "prepared/NFS Most Wanted Native.app"
        self.plant_app(app, "4.0.0")
        data = self.home / "prepared/data"
        write(app / "Contents/new", b"new app")
        write(data / "pc/speed.exe", b"player data")
        return app, data, {"pc_root": data / "pc"}

    def install(self, prepared, verify=lambda p: None):
        return ins.transactional_install(*prepared, self.paths.data, self.dest, verify, running=lambda: None)

    def test_T10_commit_profile_preservation_and_backup(self):
        prepared = self.prepared()
        save_hash = b.sha_file(self.paths.data / "saves/career")
        app = self.install(prepared)
        self.assertEqual((app / "Contents/new").read_bytes(), b"new app")
        self.assertEqual(save_hash, b.sha_file(self.paths.data / "saves/career"))
        roots = (self.paths.data / "roots.txt").read_text()
        self.assertNotIn(str(self.home / "prepared"), roots)
        pc = Path(roots.strip().split("=", 1)[1])
        self.assertTrue((pc / "speed.exe").is_file())
        profile = (self.paths.data / "app.txt").read_bytes()
        self.assertIn('fullscreen=0\nunknown=\xff\n'.encode("latin-1"), profile)
        self.assertIn(b"game_dir=" + os.fsencode(pc) + b"\n", profile)
        # M4 F5: the replaced 4.x app's backup is removed after the verified commit (it was left, 25 MB, every time).
        self.assertEqual(list(self.dest.glob(".NFS Most Wanted Native.app.backup-*")), [])
        self.assertEqual([p.name for p in self.dest.iterdir()], ["NFS Most Wanted Native.app"])

    def test_T10_permission_full_signing_and_interrupt_rollback(self):
        prepared = self.prepared()
        before = self.snapshot()
        real_replace = ins.rename_new
        for failed_target in ("NFS Most Wanted Native.app", "roots.txt", "app.txt"):
            fired = []
            def fail_replace(src, dst):
                if Path(dst).name == failed_target and not fired:
                    fired.append(1)
                    raise PermissionError(errno.EACCES, "injected permission denial")
                return real_replace(src, dst)
            with self.subTest(target=failed_target), mock.patch.object(ins, "rename_new", side_effect=fail_replace):
                with self.assertRaises(PermissionError):
                    self.install(prepared)
            self.assertEqual(before, self.snapshot())
        with mock.patch.object(ins, "write_synced", side_effect=OSError(errno.ENOSPC, "injected disk full")):
            with self.assertRaises(OSError):
                self.install(prepared)
        self.assertEqual(before, self.snapshot())
        count = []
        def fail_sign(path):
            count.append(1)
            if len(count) == 2:
                raise b.SetupError("injected final signature failure")
        with self.assertRaises(b.SetupError):
            self.install(prepared, fail_sign)
        self.assertEqual(before, self.snapshot())
        for interrupted_target in ("NFS Most Wanted Native.app", "roots.txt", "app.txt"):
            fired = []
            def interrupt_after_rename(src, dst):
                result = real_replace(src, dst)
                if Path(dst).name == interrupted_target and not fired:
                    fired.append(1)
                    raise b.Cancelled()
                return result
            with self.subTest(cancel=interrupted_target), mock.patch.object(ins, "rename_new", side_effect=interrupt_after_rename):
                with self.assertRaises(b.Cancelled):
                    self.install(prepared)
            self.assertEqual(before, self.snapshot())
        self.assertTrue((prepared[0] / "Contents/new").exists(), "keep staging for retry")
        self.assertFalse(list((self.paths.data / "installs").glob("[!.]*")))
        # Deliberately broken early replacement must trip the same hash oracle.
        write(self.dest / "NFS Most Wanted Native.app/Contents/old", b"replaced too early")
        with self.assertRaises(AssertionError):
            self.assertEqual(before, self.snapshot())

    # --- public app name: NFS Most Wanted Native.app replaces 3.x in place and keeps it ---------------------------
    APP = "NFS Most Wanted Native.app"

    def legacy_app(self, name=None):
        app = self.dest / (name or self.APP)
        self.plant_app(app, "3.4.1", executable="Launch")
        write(app / "Contents/MacOS/SpeedRecomp", b"3.x engine", True)
        write(app / "Contents/Resources/game.bin", b"3.x data")
        return app

    def fresh_prepared(self):
        app = self.home / "prepared" / self.APP
        data = self.home / "prepared/data"
        self.plant_app(app, "4.0.0")
        write(data / "pc/speed.exe", b"player data")
        return app, data, {"pc_root": data / "pc"}

    def test_pubapp_fresh_install_path_name_and_executable(self):
        installed = self.install(self.fresh_prepared())
        self.assertEqual(installed, self.dest / self.APP)
        self.assertEqual(sorted(p.name for p in self.dest.iterdir()), [self.APP])
        self.assertTrue((installed / "Contents/MacOS/NFSMostWanted").is_file())
        self.assertFalse((installed / "Contents/MacOS/NFSMW").exists())
        self.assertNotEqual(b.PUBLIC_ID, "local.nfsmw-port")
        self.assertEqual(b.PUBLIC_ID, ins.PUBLIC_BUNDLE_ID)
        self.assertEqual(b.PUBLIC_EXEC, "NFSMostWanted")
        with self.assertRaises(AssertionError):  # control: the old name must not be what we produce
            self.assertEqual(installed, self.dest / "NFSMW.app")

    def test_pubapp_owner_bundle_id_and_exec_name_refused_by_verify(self):
        class R:
            def run(self, *a, **k): pass
        app = self.home / "staged" / self.APP
        self.plant_app(app, b.PUBLIC_VERSION)
        b.verify_app(app, R())
        for kw in (dict(bundle_id="local.nfsmw-port"), dict(executable="NFSMW")):
            bad = self.home / "bad" / self.APP
            shutil.rmtree(bad, ignore_errors=True)
            self.plant_app(bad, b.PUBLIC_VERSION, **kw)
            with self.subTest(kw=kw), self.assertRaises(b.SetupError):
                b.verify_app(bad, R())

    def test_pubapp_3x_renamed_kept_byte_identical_and_40_installed(self):
        legacy = self.legacy_app()
        before = ins.digest_tree(legacy)
        installed = self.install(self.fresh_prepared())
        kept = self.dest / "NFS Most Wanted Native 3.x.app"
        self.assertEqual(ins.digest_tree(kept), before)
        self.assertEqual((installed / "Contents/MacOS/NFSMostWanted").read_bytes(), b"old 4.0.0")
        self.assertEqual(sorted(p.name for p in self.dest.iterdir()), ["NFS Most Wanted Native 3.x.app", self.APP])
        with self.assertRaises(AssertionError):  # control: a replace-and-delete would not keep the digest
            self.assertEqual(ins.digest_tree(installed), before)

    def test_pubapp_3x_by_executable_without_version_is_kept_too(self):
        app = self.dest / self.APP
        write(app / "Contents/MacOS/SpeedRecomp", b"3.x engine", True)
        write(app / "Contents/Info.plist", plistlib.dumps(dict(CFBundleExecutable="Launch", CFBundleIdentifier=b.PUBLIC_ID)))
        before = ins.digest_tree(app)
        self.install(self.fresh_prepared())
        self.assertEqual(ins.digest_tree(self.dest / "NFS Most Wanted Native 3.x.app"), before)

    def test_pubapp_existing_3x_name_gets_numbered_suffix(self):
        taken = self.legacy_app("NFS Most Wanted Native 3.x.app")
        self.legacy_app()
        write(taken / "Contents/Resources/mark", b"older kept copy")
        first = ins.digest_tree(taken)
        self.install(self.fresh_prepared())
        self.assertEqual(ins.digest_tree(taken), first)
        self.assertTrue((self.dest / "NFS Most Wanted Native 3.x (2).app/Contents/MacOS/SpeedRecomp").is_file())
        self.assertTrue((self.dest / self.APP / "Contents/MacOS/NFSMostWanted").is_file())

    def test_pubapp_4x_replaced_and_rolled_back_on_failure(self):
        prepared = self.prepared()  # plants a 4.0.0 app with Contents/old
        before = self.snapshot()
        real_replace = ins.rename_new
        fired = []
        def fail(src, dst):
            if Path(dst).name == "roots.txt" and not fired:
                fired.append(1)
                raise PermissionError(errno.EACCES, "injected")
            return real_replace(src, dst)
        with mock.patch.object(ins, "rename_new", side_effect=fail), self.assertRaises(PermissionError):
            self.install(prepared)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(sorted(p.name for p in self.dest.iterdir()), [self.APP])  # nothing renamed to 3.x
        self.install(prepared)
        self.assertTrue((self.dest / self.APP / "Contents/new").exists())
        self.assertFalse((self.dest / self.APP / "Contents/old").exists())
        self.assertFalse(list(self.dest.glob("*3.x*")))

    def test_pubapp_3x_rollback_restores_original_name_and_bytes(self):
        legacy = self.legacy_app()
        before = ins.digest_tree(legacy)
        real_replace = ins.rename_new
        fired = []
        def fail(src, dst):
            if Path(dst).name == "app.txt" and not fired:
                fired.append(1)
                raise PermissionError(errno.EACCES, "injected")
            return real_replace(src, dst)
        with mock.patch.object(ins, "rename_new", side_effect=fail), self.assertRaises(PermissionError):
            self.install(self.fresh_prepared())
        self.assertEqual(ins.digest_tree(legacy), before)
        self.assertEqual(sorted(p.name for p in self.dest.iterdir()), [self.APP])

    def test_pubapp_only_touches_its_own_name_decoys_untouched(self):
        decoy_owner = self.dest / "NFSMW.app"
        decoy_other = self.dest / "Other.app"
        decoy_similar = self.dest / "NFS Most Wanted Native Extras.app"
        for d in (decoy_owner, decoy_other, decoy_similar):
            self.plant_app(d, "3.0", bundle_id="local.nfsmw-port", executable="NFSMW")
        digests = [ins.digest_tree(d) for d in (decoy_owner, decoy_other, decoy_similar)]
        self.legacy_app()
        self.install(self.fresh_prepared())
        self.assertEqual(digests, [ins.digest_tree(d) for d in (decoy_owner, decoy_other, decoy_similar)])
        self.assertEqual(ins.public_app_path(self.dest), self.dest / self.APP)
        self.assertEqual(ins.public_app_path(self.home / "x").name, self.APP)
        # A symlinked target is refused rather than followed.
        shutil.rmtree(self.dest / self.APP)
        (self.dest / self.APP).symlink_to(decoy_other)
        with self.assertRaises(ins.InstallError):
            self.install(self.fresh_prepared())
        self.assertEqual(digests[1], ins.digest_tree(decoy_other))

    def test_T10_running_process_and_profile_lock(self):
        prepared = self.prepared()
        before = self.snapshot()
        with self.assertRaises(ins.InstallError):
            ins.assert_not_running("123 /Applications/NFSMW.app/Contents/MacOS/NFSMW\n")
        with self.assertRaises(ins.InstallError):
            ins.assert_not_running("126 /Applications/NFS Most Wanted Native 3.x.app/Contents/MacOS/Launch\n")
        with self.assertRaises(ins.InstallError):
            ins.assert_not_running("127 /Applications/NFS Most Wanted Native.app/Contents/MacOS/NFSMostWanted\n")
        with self.assertRaises(ins.InstallError):
            ins.assert_not_running("124 /Applications/NFS Most Wanted Native.app/Contents/MacOS/Launch\n")
        ins.assert_not_running("125 /bin/sh\n")
        with ins.profile_guard(self.paths.data):
            with self.assertRaises(ins.InstallError):
                self.install(prepared)
        self.assertEqual(before, self.snapshot())

    def test_T10_cancel_stops_owned_process_group_and_orphan_control(self):
        pidfile = self.home / "grandchild.pid"
        fifo = self.home / "descendants.pipe"
        os.mkfifo(fifo)
        read_fd = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
        code = """import pathlib,sys
sys.dont_write_bytecode=True
sys.path.insert(0,sys.argv[1])
from setup_kit.backend import Runner,cancellation,Cancelled
p=pathlib.Path(sys.argv[2])
with (p/'child.log').open('wb') as log:
 r=Runner(log,dict(__import__('os').environ),p)
 try:
  with cancellation(): r.run(['/bin/sh','-c','exec 3>"$2"; printf ready >&3; sleep 30 & echo "$!" > "$1"; wait','owned-child',str(p/'grandchild.pid'),str(p/'descendants.pipe')])
 except Cancelled: sys.exit(130)
"""
        error_log = self.home / "cancellation-stderr.log"
        with error_log.open("wb") as errors:
            proc = subprocess.Popen([sys.executable, "-I", "-c", code, str(ROOT / "tools"), str(self.home)],
                                    stdout=subprocess.DEVNULL, stderr=errors, start_new_session=True)
        try:
            deadline = time.monotonic() + 5
            while not pidfile.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(pidfile.exists())
            self.assertEqual(os.read(read_fd, 100), b"ready")
            proc.send_signal(signal.SIGTERM)
            self.assertEqual(proc.wait(timeout=5), 130, error_log.read_text(errors="replace")[-2000:])
            # Both shell and grandchild hold the FIFO writer. EOF proves every
            # descendant closed it; no process-list access is needed in sandbox.
            def stopped():
                try:
                    data = os.read(read_fd, 100)
                except BlockingIOError:
                    self.fail("owned child still holds the pipe open")
                self.assertEqual(data, b"")
            stopped()
            broken = subprocess.Popen(["/bin/sh", "-c", 'exec 3>"$1"; printf ready >&3; exec sleep 30', "broken", str(fifo)])
            try:
                deadline = time.monotonic() + 5
                ready = b""
                while not ready and time.monotonic() < deadline:
                    try:
                        ready = os.read(read_fd, 100)
                    except BlockingIOError:
                        pass
                    time.sleep(0.01)
                self.assertEqual(ready, b"ready")
                with self.assertRaises(AssertionError):
                    stopped()
            finally:
                broken.terminate()
                broken.wait(timeout=5)
        finally:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=5)
            os.close(read_fd)


def game_save(body=b"career progress", alias=b"ALIAS"):
    """A save shaped like the game's own (Realmc header: '20CM', file length, CalcSignature of bytes 0..0x17)."""
    size = 0x1C + len(body)
    head = b"20CM" + size.to_bytes(4, "little") + (8).to_bytes(4, "little") + max(0, size - 0x24).to_bytes(4, "little") + alias[:8].ljust(8, b"\0")
    u = ~int.from_bytes(head[:4], "big") & 0xFFFFFFFF
    table = b.SAVE_CRC if hasattr(b, "SAVE_CRC") else None
    if table is None:  # pre-fix backend: compute the same CRC here
        table = []
        for i in range(256):
            c = i << 24
            for _ in range(8):
                c = ((c << 1) ^ 0x04C11DB7) & 0xFFFFFFFF if c & 0x80000000 else (c << 1) & 0xFFFFFFFF
            table.append(c)
    for x in head[4:]:
        u = (((u << 8) | x) & 0xFFFFFFFF) ^ table[u >> 24]
    return head + (~u & 0xFFFFFFFF).to_bytes(4, "little") + body


MIGRATED = "Your 3.x career was copied into 4.0; the 3.x copy is untouched."


class SaveMigrationTests(Fixture):
    """3.x careers are copied into a 4.0 profile that has none (settings are not)."""
    def setUp(self):
        super().setUp()
        self.legacy = self.home / "legacy"   # NFSMW_SETUP_HOME/legacy = ~/Library/Application Support/NFS Most Wanted Native
        self.old_saves = self.legacy / "Documents/NFS Most Wanted"
        self.new_saves = self.paths.data / "NFS Most Wanted"
        write(self.old_saves / "ALIAS/ALIAS", game_save())
        write(self.old_saves / "SECOND/SECOND", game_save(b"x" * 63000, b"SECOND"))
        write(self.legacy / "mod-settings.json", "{}")          # 3.x settings: never carried
        write(self.legacy / "Save Backups/old/Documents/NFS Most Wanted/ALIAS/ALIAS", game_save(b"older"))

    def legacy_state(self):
        return {str(p.relative_to(self.legacy)): (os.readlink(p) if p.is_symlink() else p.read_bytes() if p.is_file() else "dir",
                                                  p.lstat().st_mtime_ns, p.lstat().st_mode)
                for p in sorted(self.legacy.rglob("*"))}

    def events_seen(self):
        return [json.loads(l) for l in self.stream.getvalue().splitlines() if l.startswith("{")]

    def copied_oracle(self, before):
        self.assertEqual(self.legacy_state(), before, "3.x files must be byte-identical and untouched")
        for alias in ("ALIAS", "SECOND"):
            self.assertEqual((self.new_saves / alias / alias).read_bytes(), (self.old_saves / alias / alias).read_bytes())
        self.assertFalse((self.paths.data / "mod-settings.json").exists())
        self.assertFalse((self.paths.data / "Save Backups").exists())
        self.assertIn({"ev": "warn", "id": "install", "msg": MIGRATED}, self.events_seen())

    def test_3x_careers_copied_originals_untouched(self):
        before = self.legacy_state()
        self.build()
        self.copied_oracle(before)
        self.assertIn("saves: copied 3.x careers ALIAS, SECOND".encode(), self.log_bytes())
        self.assertFalse(list(self.paths.data.glob(".NFS Most Wanted.from-3x-*")))
        # Broken control: a migration that moves the 3.x folder fails the same oracle.
        shutil.rmtree(self.new_saves)
        shutil.move(self.old_saves, self.new_saves)
        with self.assertRaises(AssertionError):
            self.copied_oracle(before)

    def log_bytes(self):
        self.log.seek(0)
        return self.log.read()

    def test_existing_40_saves_never_overwritten(self):
        write(self.new_saves / "OWN/OWN", game_save(b"4.0 career"))
        write(self.new_saves / "ALIAS/ALIAS", game_save("4.0 career with the 3.x name".encode()))
        def oracle():
            self.assertEqual((self.new_saves / "OWN/OWN").read_bytes(), game_save(b"4.0 career"))
            self.assertEqual((self.new_saves / "ALIAS/ALIAS").read_bytes(), game_save("4.0 career with the 3.x name".encode()))
            self.assertFalse((self.new_saves / "SECOND").exists(), "no 3.x career is merged into existing 4.0 saves")
            self.assertNotIn(MIGRATED, self.stream.getvalue())
        before = self.legacy_state()
        self.build()
        oracle()
        self.assertEqual(self.legacy_state(), before)
        self.assertIn("4.0 already has saves".encode(), self.log_bytes())
        # Broken control: a merge that copies over existing saves fails the oracle.
        shutil.copytree(self.old_saves, self.new_saves, dirs_exist_ok=True)
        with self.assertRaises(AssertionError):
            oracle()

    def test_empty_40_save_folder_still_receives_3x_careers(self):
        self.new_saves.mkdir(parents=True)   # the game makes the folder before the first career exists
        before = self.legacy_state()
        self.build()
        self.copied_oracle(before)

    def test_corrupt_or_partial_3x_folder_copies_nothing(self):
        good = game_save()
        cases = {
            "truncated (partial copy)": lambda: write(self.old_saves / "ALIAS/ALIAS", good[:-10]),
            "wrong magic": lambda: write(self.old_saves / "ALIAS/ALIAS", b"XXXX" + good[4:]),
            "header signature": lambda: write(self.old_saves / "ALIAS/ALIAS", good[:0x10] + b"\xff" + good[0x11:]),
            "career without its save": lambda: (self.old_saves / "ALIAS/ALIAS").rename(self.old_saves / "ALIAS/other"),
            "symbolic link": lambda: (self.old_saves / "SECOND/SECOND").unlink() or
                                     (self.old_saves / "SECOND/SECOND").symlink_to(self.home / "elsewhere"),
            "stray file": lambda: write(self.old_saves / "notes.txt", "x"),
        }
        for name, damage in cases.items():
            with self.subTest(name):
                shutil.rmtree(self.old_saves)
                write(self.old_saves / "ALIAS/ALIAS", good)
                write(self.old_saves / "SECOND/SECOND", game_save(b"x" * 63000, b"SECOND"))
                if self.new_saves.exists():
                    shutil.rmtree(self.new_saves)
                damage()
                self.stream.seek(0)
                self.stream.truncate()
                before = self.legacy_state()
                migrated = b.migrate_3x_saves(self.paths, self.events)
                self.assertEqual(migrated, [])
                self.assertFalse(self.new_saves.exists(), "nothing is copied from a damaged 3.x folder")
                self.assertEqual(self.legacy_state(), before)
                warns = [e["msg"] for e in self.events_seen() if e["ev"] == "warn"]
                self.assertEqual(len(warns), 1)
                self.assertIn("were not copied into 4.0", warns[0])
                self.assertIn(str(self.old_saves), warns[0])
        # Broken control: a check of the magic alone accepts the truncated save (the game itself would reject it).
        shutil.rmtree(self.old_saves)
        write(self.old_saves / "ALIAS/ALIAS", good[:-10])
        with mock.patch.object(b, "save_header_ok", lambda d: d[:4] == b"20CM"):
            self.assertEqual(b.migrate_3x_saves(self.paths, self.events), ["ALIAS"])
        self.assertTrue(self.new_saves.exists())

    def test_no_3x_profile_is_silent(self):
        shutil.rmtree(self.legacy)
        write(self.pc / "MOVIES/ealogo_english_ntsc.vp6", b"synthetic movie")  # without movies Setup notes it (pc)
        self.build()
        self.assertFalse(self.new_saves.exists())
        self.assertFalse([e for e in self.events_seen() if e["ev"] == "warn"])


class X360ArchiveTests(Fixture):
    def test_sevenzip_listing_is_bounded_without_spooling(self):
        popen = subprocess.Popen

        def listing_child(command, **kwargs):
            return popen([sys.executable, "-I", "-c", 'print("listing" * 32)'], **kwargs)

        with mock.patch.object(b, "SEVENZIP_MAX_LISTING", 128, create=True), \
                mock.patch.object(b.subprocess, "Popen", side_effect=listing_child), \
                mock.patch.object(b.tempfile, "TemporaryFile", side_effect=AssertionError("listing spooled to disk")):
            with self.assertRaisesRegex(b.SetupError, "too many entries"):
                b.sevenzip_members(self.home / "game.7z")

    """Xbox archive regression: old guard rejects 2/2 synthetic archives; fixed passes both formats.
    The preparation stub tests routing and identical bytes, not real disc conversion."""
    def archive(self, suffix, entries=None, stem="console"):
        import zipfile
        import tarfile
        entries = entries if entries is not None else [("download/game.iso", b"fake Xbox ISO"), ("readme.txt", b"notes"),
                                                     ("__MACOSX/._game.iso", b"resource fork")]
        path = self.home / (stem + suffix)
        if suffix == ".zip":
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
                for name, data in entries:
                    info = zipfile.ZipInfo(name)
                    info.external_attr = (0o120777 if data is None else 0o100644) << 16
                    z.writestr(info, b"../outside" if data is None else data)
        else:
            tar = self.home / (stem + ".tar")
            with tarfile.open(tar, "w") as a:
                for name, data in entries:
                    info = tarfile.TarInfo(name)
                    if data is None:
                        info.type, info.linkname = tarfile.SYMTYPE, "../outside"
                    else:
                        info.size = len(data)
                    a.addfile(info, None if data is None else io.BytesIO(data))
            result = subprocess.run(["/usr/bin/bsdtar", "--format=7zip", "-P", "-cf", str(path), "@" + str(tar)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        return path

    def test_archives_prepare_like_bare_iso_both_products_unchanged(self):
        bare = self.home / "game.iso"
        bare.write_bytes(b"fake Xbox ISO")
        for game in ("mw", "carbon"):
            seen = []
            def prepare(src, dst, *args, **kwargs):
                seen.append(src)
                write(dst / "files/data", src.read_bytes())
                return types.SimpleNamespace(roots={"x360_root": dst / "files"}, features=["textures"], missing=[])
            with mock.patch.object(b.importlib, "import_module", return_value=types.SimpleNamespace(prepare=prepare)) as importer:
                expected = b.prepare_x360(bare, self.paths.work / "prepared", self.pc, self.events, self.child_env, game=game)
                digest = ins.digest_tree(expected["x360_root"])
                for suffix in (".zip", ".7z"):
                    with self.subTest(game=game, suffix=suffix):
                        archive = self.archive(suffix)
                        before = (b.sha_file(archive), archive.stat().st_size, archive.stat().st_mtime_ns)
                        actual = b.prepare_x360(archive, self.paths.work / "prepared", self.pc, self.events, self.child_env, game=game)
                        self.assertEqual(actual, expected)
                        self.assertEqual(ins.digest_tree(actual["x360_root"]), digest)
                        self.assertEqual(seen[-1].suffix, ".iso")
                        self.assertEqual(seen[-1].read_bytes(), bare.read_bytes())
                        self.assertFalse((self.paths.work / "x360_src/__MACOSX").exists())
                        self.assertEqual((b.sha_file(archive), archive.stat().st_size, archive.stat().st_mtime_ns), before)
                self.assertEqual(importer.call_args.args[0], "setup_kit.carbon_x360" if game == "carbon" else "setup_kit.x360")
        self.assertIn('"ev":"progress"', self.stream.getvalue().replace(" ", ""))

    def test_zero_two_and_mixed_candidates_named(self):
        for suffix in (".zip", ".7z"):
            for entries, count, found in (([("notes.txt", b"notes")], 0, "none"),
                                          ([("a.iso", b"a"), ("nested/b.iso", b"b")], 2, "a.iso"),
                                          ([("a.iso", b"a"), ("game/default.xex", b"x")], 2, "game")):
                with self.subTest(suffix=suffix, count=count):
                    archive = self.archive(suffix, entries)
                    with self.assertRaisesRegex(b.SetupError, f"found {count}:.*{found}"):
                        b.extract_x360_archive(archive, self.paths.work, self.events)
                    self.assertFalse((self.paths.work / "x360_src").exists())
                    self.assertFalse((self.paths.work / "x360_src.json").exists())

    def test_paths_and_links_refused_before_writes(self):
        for suffix in (".zip", ".7z"):
            for name, data in (("../escaped.iso", b"iso"), ("/absolute.iso", b"iso"),
                               ("game.iso", None), ("nested/link", None)):
                with self.subTest(suffix=suffix, name=name):
                    archive = self.archive(suffix, [("notes.txt", b"notes"), (name, data)])
                    before = {str(p.relative_to(self.home)): b.sha_file(p) for p in self.home.rglob("*")
                              if p.is_file() and self.paths.work not in p.parents}
                    with self.assertRaisesRegex(b.SetupError, "unsafe path|link or special"):
                        b.extract_x360_archive(archive, self.paths.work, self.events)
                    after = {str(p.relative_to(self.home)): b.sha_file(p) for p in self.home.rglob("*")
                             if p.is_file() and self.paths.work not in p.parents}
                    self.assertEqual(after, before)
                    self.assertFalse((self.paths.work / "x360_src").exists())

    def test_folder_archives_and_cache_coexist_with_pc(self):
        pc_archive = PCZipTests.make_zip(self, self.home / "pc.zip")
        pc_root = b.extract_pc_zip(pc_archive, self.paths.work, self.events)
        for suffix in (".zip", ".7z"):
            archive = self.archive(suffix, [("Xbox game/default.xex", b"xex"), ("Xbox game/GLOBAL/data", b"data")])
            root = b.extract_x360_archive(archive, self.paths.work, self.events)
            self.assertEqual(root, self.paths.work / "x360_src/Xbox game")
            self.assertEqual((root / "GLOBAL/data").read_bytes(), b"data")
            with mock.patch.object(b, "sevenzip_members", side_effect=AssertionError("cache miss")), \
                 mock.patch.object(b.zipfile, "ZipFile", side_effect=AssertionError("cache miss")):
                self.assertEqual(b.extract_x360_archive(archive, self.paths.work, self.events), root)
            self.assertTrue((pc_root / "speed.exe").is_file())
        self.assertEqual(b.extract_pc_zip(pc_archive, self.paths.work, self.events), pc_root)

    def test_cache_reextracts_changed_input_or_output(self):
        for suffix in (".zip", ".7z"):
            archive = self.archive(suffix)
            root = b.extract_x360_archive(archive, self.paths.work, self.events)
            root.write_bytes(b"tampered")
            self.assertEqual(b.extract_x360_archive(archive, self.paths.work, self.events).read_bytes(), b"fake Xbox ISO")
            self.archive(suffix, [("other.iso", b"new ISO")])
            root = b.extract_x360_archive(archive, self.paths.work, self.events)
            self.assertEqual(root.name, "other.iso")
            self.assertEqual(root.read_bytes(), b"new ISO")
            root.unlink()
            self.assertEqual(b.extract_x360_archive(archive, self.paths.work, self.events).read_bytes(), b"new ISO")

    def test_space_and_size_limits(self):
        for suffix in (".zip", ".7z"):
            archive = self.archive(suffix)
            with mock.patch.object(b.shutil, "disk_usage", return_value=types.SimpleNamespace(free=1)):
                with self.assertRaisesRegex(b.SetupError, "Not enough space on"):
                    b.extract_x360_archive(archive, self.paths.work, self.events)
            with mock.patch.object(b, "X360_ARCHIVE_BYTES", 1):
                with self.assertRaisesRegex(b.SetupError, "larger|more than"):
                    b.extract_x360_archive(archive, self.paths.work, self.events)
            self.assertFalse((self.paths.work / "x360_src").exists())

    def test_sevenzip_stream_refuses_short_or_oversized_output(self):
        archive = self.archive(".7z", [("game.iso", b"fake Xbox ISO")])
        for declared in (1, 100):
            with self.subTest(declared=declared):
                members, _ = b.sevenzip_members(archive)
                members[0].file_size = declared
                with mock.patch.object(b, "sevenzip_members", return_value=(members, declared)):
                    with self.assertRaisesRegex(b.SetupError, "truncated|more than it declares"):
                        b.extract_x360_archive(archive, self.paths.work, self.events)
                self.assertFalse((self.paths.work / "x360_src").exists())
                self.assertFalse((self.paths.work / "x360_src.json").exists())

    def test_carbon_car_check_receives_unpacked_iso(self):
        for suffix in (".zip", ".7z"):
            archive = self.archive(suffix)
            before = b.sha_file(archive)
            output = io.StringIO()
            recognition = dict(game="carbon", platform="x360", cars=[], shared=[])
            with redirect_stdout(output), mock.patch.object(b.car_sources, "recognise", return_value=recognition) as scan:
                rc = b.main(["--cars-check", "--carbon", str(archive), "--work", str(self.paths.work), "--events"])
            self.assertEqual(rc, 0, output.getvalue())
            selected = scan.call_args.args[0]
            self.assertEqual(selected, self.paths.work / "x360_src/download/game.iso")
            self.assertEqual(selected.read_bytes(), b"fake Xbox ISO")
            self.assertEqual(b.sha_file(archive), before)

    def test_check_route_both_products_uses_work_cache(self):
        for suffix in (".zip", ".7z"):
            for game in ("mw", "carbon"):
                archive = self.archive(suffix)
                args = self.args(check=True)
                args.x360 = str(archive)
                args.game = game
                if game == "carbon":
                    args.carbon = str(self.pc)
                with self.pipeline_stubs(), mock.patch.object(b, "validate_carbon"), \
                     mock.patch.object(b, "prepare_x360", return_value={}) as prepare:
                    b.build(args, self.paths, self.events, FakeRunner(self.log, self.child_env, self.paths.work), self.source)
                selected = prepare.call_args.args[0]
                self.assertEqual(selected, self.paths.work / "x360_src/download/game.iso")
                self.assertEqual(selected.read_bytes(), b"fake Xbox ISO")


class PCZipTests(Fixture):
    def test_zip_byte_and_entry_caps_precede_open_for_all_standalone_routes(self):
        import zipfile
        from setup_kit import carbon_source
        archive = self.home / "game.zip"
        # Use an ordinary small ZIP64 archive to exercise the same metadata path as big ISOs.
        with mock.patch.object(zipfile, "ZIP64_LIMIT", 1), zipfile.ZipFile(archive, "w") as out:
            out.writestr("first.txt", b"first")
            out.writestr("second.txt", b"second")
        routes = (b.extract_pc_zip, b.extract_x360_archive, carbon_source.prepare)
        for route in routes:
            for limit, value in (("ZIP_MAX_DIRECTORY", 32), ("ZIP_MAX_FILES", 1)):
                with self.subTest(route=route.__module__ + "." + route.__name__, limit=limit), \
                        mock.patch.object(b, limit, value, create=True), \
                        mock.patch.object(zipfile, "ZipFile", side_effect=AssertionError("ZIP opened before preflight")):
                    with self.assertRaisesRegex(b.SetupError, "too much file listing data or too many entries"):
                        route(archive, self.paths.work, self.events)

    def test_zip_preflight_counts_directory_headers_as_well_as_end_record(self):
        import zipfile
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("first.txt", b"first")
            archive.writestr("second.txt", b"second")
        end = zipfile._EndRecData(stream)
        end[zipfile._ECD_ENTRIES_TOTAL] = 1
        archive = self.home / "game.zip"
        archive.write_bytes(stream.getvalue())
        with mock.patch.object(zipfile, "_EndRecData", return_value=end), \
                mock.patch.object(b, "ZIP_MAX_FILES", 1), \
                mock.patch.object(zipfile, "ZipFile", side_effect=AssertionError("ZIP opened before preflight")):
            with self.assertRaisesRegex(b.SetupError, "too many entries"):
                b.extract_pc_zip(archive, self.paths.work, self.events)

    """--pc accepts a .zip of the game folder (the game one level down)."""
    def make_zip(self, path, extra=(), top="Need For Speed Most Wanted/"):
        import zipfile
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(self.pc.rglob("*")):
                if f.is_file():
                    z.write(f, top + str(f.relative_to(self.pc)))
            z.writestr("__MACOSX/._speed.exe", b"resource fork")
            for item in extra:
                if isinstance(item, tuple):
                    info, data = item
                    z.writestr(info, data)
        return path

    def outside_work(self):
        work = self.paths.work
        return sorted(str(p) for p in self.home.rglob("*") if work not in p.parents and p != work)

    def test_sevenzip_game_at_root_or_one_level_down(self):
        # On 7942da5b both archive layouts fail (2/2); with the fix both pass (2/2).
        # On 999f865a both layouts pass Check and fail Extras; the fix passes both stages for both layouts.
        from setup_kit import car_selection

        spec = car_selection.spec(next(row for row in car_selection.load() if row["origin"]["platform"] == "pc"))
        recognition = dict(game="carbon", platform="pc", cars=[
            dict(car_hash=b.car_sources.path_hash("CARS/" + spec["folder"]), status="importable")])
        selection = self.home / "selection.json"
        write(selection, json.dumps([spec]))
        carbon = self.home / "Carbon game"
        carbon.mkdir()
        expected = b.digest_tree(self.pc)

        def plan(_carbon, *, mw_pc, **kwargs):
            if b.digest_tree(mw_pc) != expected:
                raise AssertionError("Extras did not receive the checked MW donor files.")
            return dict(measured=False, approved=False, volumes=[], cars=[])

        for depth, directory, names in (
            (0, self.pc, sorted(p.name for p in self.pc.iterdir())),
            (1, self.pc.parent, [self.pc.name]),
        ):
            with self.subTest(depth=depth):
                archive = self.home / f"pc-{depth}.7z"
                subprocess.run(["/usr/bin/bsdtar", "--format=7zip", "-cf", str(archive),
                                "-C", str(directory), *names], check=True, capture_output=True)
                before = b.sha_file(archive)
                args = self.args(check=True)
                args.pc = str(archive)
                with self.pipeline_stubs():
                    self.assertIsNone(b.build(args, self.paths, self.events,
                                              FakeRunner(self.log, self.child_env, self.paths.work), self.source))
                root = self.paths.work / "pc_src"
                self.assertEqual(b.digest_tree(root / self.pc.name if depth else root), b.digest_tree(self.pc))
                self.assertEqual(b.sha_file(archive), before)
                output = io.StringIO()
                with redirect_stdout(output), mock.patch.object(b.car_sources, "recognise", return_value=recognition), \
                     mock.patch.object(b.car_api().CarImporter, "plan", side_effect=plan):
                    rc = b.main(["--cars-check", "--carbon", str(carbon), "--cars-spec", str(selection),
                                 "--cars-mw-pc", str(archive), "--work", str(self.paths.work / "extras"), "--events"])
                self.assertEqual((rc, json.loads(output.getvalue().splitlines()[-1])["ev"]), (0, "cars-result"),
                                 output.getvalue()[-1500:])

    def test_zip_validates_like_the_folder_and_source_untouched(self):
        archive = self.make_zip(self.home / "Need For Speed Most Wanted.zip")
        before = (b.sha_file(archive), archive.stat().st_mtime_ns)
        root = b.extract_pc_zip(archive, self.paths.work, self.events)
        self.assertEqual(root, self.paths.work / "pc_src/Need For Speed Most Wanted")
        self.assertEqual(b.validate_pc(root, self.source)[1], b.validate_pc(self.pc, self.source)[1])
        self.assertEqual(ins.digest_tree(root), ins.digest_tree(self.pc))
        self.assertFalse((self.paths.work / "pc_src/__MACOSX").exists())
        self.assertEqual((b.sha_file(archive), archive.stat().st_mtime_ns), before)
        # The whole --check route with the .zip and an install from it, as with the folder.
        args = self.args(check=True); args.pc = str(archive)
        with self.pipeline_stubs():
            self.assertIsNone(b.build(args, self.paths, self.events, FakeRunner(self.log, self.child_env, self.paths.work), self.source))
            args = self.args(); args.pc = str(archive)
            app = b.build(args, self.paths, self.events, FakeRunner(self.log, self.child_env, self.paths.work), self.source)
        self.assertTrue(app.exists())
        roots = dict(l.split("=", 1) for l in (self.paths.data / "roots.txt").read_text().splitlines())
        self.assertEqual(ins.digest_tree(Path(roots["pc_root"])), ins.digest_tree(self.pc))
        self.assertIn("Reusing the game unpacked earlier", self.stream.getvalue())
        self.assertEqual((b.sha_file(archive), archive.stat().st_mtime_ns), before)

    def test_zip_without_game_or_with_two_games_refused(self):
        import zipfile
        empty = self.home / "other.zip"
        with zipfile.ZipFile(empty, "w") as z:
            z.writestr("readme.txt", "no game")
        with self.assertRaisesRegex(b.SetupError, "one game folder"):
            b.extract_pc_zip(empty, self.paths.work, self.events)
        two = self.make_zip(self.home / "two.zip")
        with zipfile.ZipFile(two, "a") as z:
            for f in sorted(self.pc.rglob("*")):
                if f.is_file():
                    z.write(f, "copy/" + str(f.relative_to(self.pc)))
        with self.assertRaisesRegex(b.SetupError, "more than one"):
            b.extract_pc_zip(two, self.paths.work, self.events)

    def test_malicious_zips_refused_nothing_outside_work(self):
        import zipfile
        def entry(name, data=b"x", mode=None):
            info = zipfile.ZipInfo(name)
            if mode is not None:
                info.external_attr = mode << 16
            return (info, data)
        cases = {
            "parent": [entry("../../escaped.txt")],
            "nested parent": [entry("Need For Speed Most Wanted/GLOBAL/../../../escaped.txt")],
            "absolute": [entry("/tmp/escaped-nfsmw-test.txt")],
            "symlink": [entry("Need For Speed Most Wanted/link", str(self.home).encode(), 0o120777)],
        }
        outside = self.outside_work()
        for name, extra in cases.items():
            with self.subTest(name):
                archive = self.make_zip(self.paths.work.parent / f"evil-{name.replace(' ', '-')}.zip", extra)
                outside_now = self.outside_work()
                with self.assertRaisesRegex(b.SetupError, "unsafe path|link or special"):
                    b.extract_pc_zip(archive, self.paths.work, self.events)
                self.assertEqual(self.outside_work(), outside_now)
                self.assertFalse(Path("/tmp/escaped-nfsmw-test.txt").exists())
                self.assertFalse((self.home / "escaped.txt").exists())
        archive = self.make_zip(self.home / "big.zip")
        for limit, message in (("ZIP_MAX_BYTES", "far larger"), ("ZIP_MAX_FILES", "too many entries")):
            with self.subTest(limit), mock.patch.object(b, limit, 3), self.assertRaisesRegex(b.SetupError, message):
                b.extract_pc_zip(archive, self.paths.work, self.events)
        # Broken control: a naive extractor writes the ../ entry outside work, and the oracle sees it.
        archive = self.make_zip(self.paths.work.parent / "evil-control.zip", cases["parent"])
        before = self.outside_work()
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                target = self.paths.work / "pc_src" / info.filename
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(z.read(info))
        self.assertNotEqual(self.outside_work(), before)


def make_iso(path, files, label="FIXTURE", joliet=True):
    """A minimal ISO 9660/Joliet image, including directories for loose disc files."""
    def record(extent, size, flags, name):
        n = 33 + len(name) + (len(name) + 1) % 2
        r = bytearray(n)
        r[0] = n
        struct.pack_into("<I", r, 2, extent)
        struct.pack_into(">I", r, 6, extent)
        struct.pack_into("<I", r, 10, size)
        struct.pack_into(">I", r, 14, size)
        r[25], r[32] = flags, len(name)
        struct.pack_into("<H", r, 28, 1)
        struct.pack_into(">H", r, 30, 1)
        r[33:33 + len(name)] = name
        return bytes(r)
    first = 21
    extents, sector = {}, first
    for name, data in files.items():
        extents[name] = sector
        sector += max(1, -(-len(data) // 2048))
    folders = sorted({str(p) for name in files for p in Path(name).parents if str(p) != "."})
    primary = {name: sector + i for i, name in enumerate(folders)}
    secondary = {name: sector + len(folders) + i for i, name in enumerate(folders)}
    sector += 2 * len(folders)
    def directory(at, encode, folder="", directories=None):
        directories = directories or primary
        body = record(at, 2048, 2, b"\0") + record(at, 2048, 2, b"\1")
        for name, data in files.items():
            if str(Path(name).parent) == (folder or "."):
                body += record(extents[name], len(data), 0, encode(Path(name).name + ";1"))
        for name, extent in directories.items():
            if str(Path(name).parent) == (folder or "."):
                body += record(extent, 2048, 2, encode(Path(name).name))
        assert len(body) <= 2048
        return body.ljust(2048, b"\0")
    def descriptor(kind, root_at, escape=b""):
        d = bytearray(2048)
        d[0], d[1:6], d[6] = kind, b"CD001", 1
        d[40:72] = label.encode().ljust(32)
        struct.pack_into("<I", d, 80, sector)
        struct.pack_into(">I", d, 84, sector)
        d[88:88 + len(escape)] = escape
        struct.pack_into("<H", d, 128, 2048)
        struct.pack_into(">H", d, 130, 2048)
        d[156:190] = record(root_at, 2048, 2, b"\0")
        return bytes(d)
    terminator = bytes([255]) + b"CD001\x01" + bytes(2041)
    image = bytearray(16 * 2048) + descriptor(1, 19) + (descriptor(2, 20, b"%/E") if joliet else terminator) + terminator
    image += directory(19, lambda n: n.upper().encode()) + directory(20, lambda n: n.encode("utf-16-be"), directories=secondary)
    for name, data in files.items():
        image += bytes(data).ljust(max(1, -(-len(data) // 2048)) * 2048, b"\0")
    for directories, encode in ((primary, lambda n: n.upper().encode()), (secondary, lambda n: n.encode("utf-16-be"))):
        for name, extent in directories.items():
            image += directory(extent, encode, name, directories)
    path.write_bytes(image)
    return path


LITERALS = ["0" + format(c, "08b") for c in range(256)]  # a raw literal token: flag 0, then the byte


def rtp_update(path, files, damaged=None):
    """An update program in the 1.3 update's form: a small PE, then an RTPatch 7.00 file replacing `files`
    ({Windows path: bytes}) whole. The compressed streams use raw literals (no Huffman model to mirror here);
    the adaptive Huffman decoder is proved on the player's own update (RTPatchOwnerTests)."""
    def lp(text):
        data = text.encode("latin-1") + b"\0"
        return bytes([len(data)]) + data
    def stream(data, damage):
        bits = "1011010110011100" + "00000001" + "00000000" + "000000010000" + "000000000100" + "1000"
        bits += "".join(map(LITERALS.__getitem__, data))
        if damage:  # one bit of the middle literal's value: the stream still decodes, to the wrong bytes
            at = 60 + 9 * (len(data) // 2) + 3
            bits = bits[:at] + "10"[int(bits[at])] + bits[at + 1:]
        bits += "0" * (-len(bits) % 8)
        return int(bits, 2).to_bytes(len(bits) // 8, "big")
    from setup_kit import rtpatch
    body = b"K*" + struct.pack("<HHIH", 0x02BC, 0x92E4, 0x00010000, 0x0030) + bytes(18)
    body += b"\x07\x00\x00\x00\x01\x00\x00\x04\x00" + lp("SOFTWARE\\EA GAMES\\Need for Speed Most Wanted") + lp("Install Dir")
    body += b"\x00\x00\x02\x00" + lp("FRONTEND") + lp("GLOBAL")
    for name, data in files.items():
        short = name.split("\\")[-1]
        packed = stream(data, damaged == name)
        entry = short.encode().ljust(12, b"\0") + struct.pack("<HHII", 0, 0x20, len(data), 0)
        sums = bytes(2) + struct.pack("<II", rtpatch.rolling(data, 31), rtpatch.rolling(data, 30))
        body += struct.pack("<H", 0x2444) + lp(name) + bytes(10) + b"\x01" + struct.pack("<II", len(data), len(packed))
        body += entry + sums + bytes(8) + lp(short) + packed
    body += struct.pack("<H", 0x1000) + bytes(8)
    pe = bytearray(0x400)
    pe[:2] = b"MZ"
    struct.pack_into("<I", pe, 0x3C, 0x80)
    pe[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HH", pe, 0x84, 0x14C, 1)
    struct.pack_into("<H", pe, 0x80 + 20, 224)
    struct.pack_into("<8sIIII", pe, 0x80 + 24 + 224, b".text", 0x200, 0x1000, 0x200, 0x200)
    path.write_bytes(bytes(pe) + body)
    return path


class PCDiscTests(Fixture):
    """--pc accepts the PC disc images: every disc's archives and disc 1's loose files are installed into work,
    then the 1.3 update (--pc-update) and a speed.exe with the 1.3 code (--pc-exe). Inputs are only read."""
    def setUp(self):
        super().setUp()
        import zipfile
        write(self.pc / "MOVIES/ealogo_english_ntsc.vp6", b"synthetic movie")
        self.discs = self.home / "My discs"
        self.discs.mkdir()
        self.old_exe = bytearray(self.data)
        self.old_exe[self.offsets[".text"] + 5] ^= 1  # the discs' exe: not the 1.3 code
        self.protected = b"MZ" + bytes(254) + b"stands for the update's copy-protected 1.3 exe"
        self.patched = {"FRONTEND\\FrontB.lzc": b"front end 1.3", "GLOBAL\\gameplay.bin": b"gameplay 1.3" * 300,
                        "GLOBAL\\gameplay.lzc": b"gameplay.lzc 1.3", "GLOBAL\\InGameB.bun": b"in game 1.3",
                        "GLOBAL\\InGameB.lzc": b"in game.lzc 1.3", "server.dll": b"server 1.3"}
        game = sorted(p for p in self.pc.rglob("*") if p.is_file() and p.name != "speed.exe")
        def archive(members, extra=()):
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
                for p in members:  # Windows paths, as EA's archives store them
                    z.writestr(str(p.relative_to(self.pc)).replace("/", "\\"), p.read_bytes())
                for name, data in extra:
                    z.writestr(name, data)
            return buffer.getvalue()
        inf = "[autorun]\r\nopen=RunGame.exe\r\n\r\n[Special]\r\nDisk={}\r\nProductGuiID={{ADE91A13-FIXTURE}}\r\n"
        listing = "1,1,Support\\EA Help\\*.* /s\r\n1,1,Support\\*.*\r\n1,1,speed.exe\r\n1,1,server.dll\r\n2,1,\r\n"
        self.disc1 = make_iso(self.discs / "NFSMW_DISC_1.iso", {
            "autorun.inf": inf.format(1).encode(), "common_filelist.txt": listing.encode(), "speed.exe": bytes(self.old_exe),
            "server.dll": b"server 1.0", "AutoRun.exe": b"not installed", "0compressed.zip": archive(game[:2], [("GLOBAL\\gameplay.bin", b"gameplay 1.0")])}, "NFSMW_DISC1")
        self.disc2 = make_iso(self.discs / "NFSMW_DISC_2.iso", {
            "autorun.inf": inf.format(2).encode(), "1compressed.zip": archive(game[2:]), "RunGame.exe": b"x"}, "NFSMW_DISC2")
        make_iso(self.discs / "Other game.iso", {"autorun.inf": b"[Special]\r\nDisk=2\r\nProductGuiID={OTHER}\r\n",
                                                 "1compressed.zip": archive([])})
        self.update = rtp_update(self.home / "nfsmwpatch1.3.exe", dict(self.patched, **{"speed.exe": self.protected}))
        self.exe = self.home / "speed.exe"
        write(self.exe, self.data)

    def inputs(self):
        return [(p, b.sha_file(p), p.stat().st_mtime_ns) for p in sorted(self.discs.iterdir()) + [self.update, self.exe]]

    def test_P5_7_missing_supplements_one_report(self):
        """P5 item 7: old 0/1 complete reports; collected-input implementation 1/1."""
        with self.assertRaises(b.SetupError) as error:
            self.extract(update=False, exe=False)
        self.assertEqual(str(error.exception).splitlines(), [
            "Missing: official 1.3 update. Choose it above.",
            "Missing: speed.exe from that update. Choose it above."])
        self.assertFalse((self.paths.work / "pc_src").exists())

    def extract(self, disc=None, update=True, exe=True):
        return b.extract_pc_disc(disc or self.disc1, self.update if update else None, self.exe if exe else None,
                                 self.paths.work, self.events, self.source)

    def test_P5_10_tool_change_invalidates_disc_cache(self):
        """P5 item 10: old patcher calls 1/2; fixed 2/2 (unchanged retry still reuses)."""
        real_hash = b.sha_file
        tool = Path(b.__file__).with_name("rtpatch.py")
        with mock.patch.object(b, "_install_discs", wraps=b._install_discs) as install:
            self.extract()
            self.extract()
            self.assertEqual(install.call_count, 1)
            with mock.patch.object(b, "sha_file", side_effect=lambda p: "new tool" if Path(p) == tool else real_hash(p)):
                self.extract()
            self.assertEqual(install.call_count, 2)

    def test_disc_window_zip_byte_cap_precedes_open(self):
        with mock.patch.object(b, "ZIP_MAX_DIRECTORY", 32, create=True), \
                mock.patch.object(b.zipfile, "ZipFile", side_effect=AssertionError("ZIP opened before preflight")):
            with self.assertRaisesRegex(b.SetupError, "too much file listing data"):
                self.extract()
        self.assertFalse((self.paths.work / "pc_src").exists())

    def test_mounted_disc_rejects_multiply_linked_root_files_and_movies(self):
        folder = self.home / "disc folder"
        (folder / "MOVIES").mkdir(parents=True)
        for rel in ("server.dll", "MOVIES/intro.vp6"):
            with self.subTest(path=rel):
                path = folder / rel
                path.write_bytes(b"ordinary fixture data")
                duplicate = path.with_name("copy-" + path.name)
                os.link(path, duplicate)
                with self.assertRaisesRegex(b.pc_disc.DiscError, "more than one hard link"):
                    b.pc_disc.Disc(folder)
                duplicate.unlink()
                self.assertIn(rel, b.pc_disc.Disc(folder))

    def test_discs_install_like_the_folder_any_disc_chosen_inputs_untouched(self):
        before = self.inputs()
        root = self.extract(self.disc2)  # disc 2 chosen: disc 1 is found beside it, the other game's disc is not used
        self.assertEqual(root, self.paths.work / "pc_src")
        self.assertEqual(b.validate_pc(root, self.source)[1], b.validate_pc(self.pc, self.source)[1])
        for p in self.pc.rglob("*"):
            relative = str(p.relative_to(self.pc)).replace("/", "\\")
            if p.is_file() and relative not in self.patched:
                self.assertEqual((root / p.relative_to(self.pc)).read_bytes(), p.read_bytes(), relative)
        for name, data in self.patched.items():
            self.assertEqual((root / name.replace("\\", "/")).read_bytes(), data)
        self.assertEqual(sorted(p.name for p in root.iterdir()), sorted(["speed.exe", "server.dll", "MOVIES", *b.PC_DIRS]))
        self.assertFalse(any("\\" in p.name for p in root.rglob("*")))
        self.assertEqual(self.inputs(), before)
        # The --check and --build routes with the disc images, and an install from them.
        def args(check):
            a = self.args(check=check)
            a.pc, a.pc_update, a.pc_exe = str(self.disc1), str(self.update), str(self.exe)
            return a
        with self.pipeline_stubs():
            self.assertIsNone(b.build(args(True), self.paths, self.events, FakeRunner(self.log, self.child_env, self.paths.work), self.source))
            app = b.build(args(False), self.paths, self.events, FakeRunner(self.log, self.child_env, self.paths.work), self.source)
        self.assertTrue(app.exists())
        self.assertIn("Reusing the game unpacked earlier from the disc images", self.stream.getvalue())
        roots = dict(l.split("=", 1) for l in (self.paths.data / "roots.txt").read_text().splitlines())
        self.assertEqual(b.validate_pc(Path(roots["pc_root"]), self.source)[1], b.validate_pc(self.pc, self.source)[1])
        choices = json.loads((self.paths.state / "source-choices.json").read_text())
        self.assertEqual((choices["pc"], choices["pc_update"], choices["pc_exe"]), (str(self.disc1), str(self.update), str(self.exe)))
        self.assertEqual(self.inputs(), before)

    def test_disc_folders_mounted_in_finder_work_too(self):
        import zipfile
        volumes = self.home / "Volumes"
        for image in (self.disc1, self.disc2):
            from setup_kit import pc_disc
            disc = pc_disc.Disc(image)
            folder = volumes / image.stem
            for key, (name, _, _) in disc.files.items():
                write(folder / name, disc.read(name, 1 << 30))
            disc.close()
        (volumes / "Macintosh HD/GLOBAL").mkdir(parents=True)
        root = self.extract(volumes / "NFSMW_DISC_1")
        self.assertEqual(b.validate_pc(root, self.source)[1], b.validate_pc(self.pc, self.source)[1])
        self.assertTrue(b.pc_disc.is_disc(volumes / "NFSMW_DISC_2"))
        self.assertFalse(b.pc_disc.is_disc(self.pc))

    def test_versions_missing_disc_and_inputs_refused_before_unpacking(self):
        cases = [
            ("Missing: official 1.3 update", dict(update=False, exe=False)),
            ("Missing: speed.exe", dict(exe=False)),
        ]
        for message, kwargs in cases:
            with self.subTest(message), self.assertRaisesRegex(b.SetupError, message):
                self.extract(**kwargs)
            self.assertFalse((self.paths.work / "pc_src").exists())
        write(self.exe, self.old_exe)
        with self.assertRaisesRegex(b.SetupError, "differs"):
            self.extract()
        write(self.exe, self.data)
        # An update whose own speed.exe has the 1.3 code needs no separate speed.exe.
        rtp_update(self.update, dict(self.patched, **{"speed.exe": self.data}))
        self.assertEqual((self.extract(exe=False) / "speed.exe").read_bytes(), self.data)
        elsewhere = self.home / "Other drive" / "renamed.iso"  # any name: discs are known by their autorun.inf
        elsewhere.parent.mkdir()
        self.disc2.rename(elsewhere)
        with self.assertRaisesRegex(b.SetupError, r"^Disc 2 of the set \(2 discs\) was not found with disc 1"):
            self.extract()
        with self.assertRaisesRegex(b.SetupError, "^Disc 1 of the set was not found with the chosen disc"):
            self.extract(elsewhere)
        with self.assertRaisesRegex(b.SetupError, "only used with PC disc images"):
            b.pc_extras(self.pc, self.update, None)
        with self.assertRaisesRegex(b.SetupError, "only used with PC disc images"):
            b.pc_extras(self.pc, None, None, [elsewhere])
        with self.assertRaisesRegex(b.SetupError, "not a disc image"):
            b.pc_extras(self.disc1, None, None, [self.exe])
        self.assertEqual(b.pc_extras(self.disc1, self.update, self.exe, [elsewhere]), [self.update, self.exe, elsewhere])

    def test_another_speed_exe_or_update_is_not_served_from_the_earlier_unpack(self):
        self.extract()
        reused = "Reusing the game unpacked earlier from the disc images"
        self.extract()
        self.assertEqual(self.stream.getvalue().count(reused), 1)
        other = bytes(self.data[:-1]) + bytes([self.data[-1] ^ 1])  # the same 1.3 code, different bytes
        write(self.exe, other)
        root = self.extract()
        self.assertEqual(self.stream.getvalue().count(reused), 1)
        self.assertEqual((root / "speed.exe").read_bytes(), other)
        rtp_update(self.update, dict(self.patched, **{"speed.exe": self.protected, "server.dll": b"server 1.3 again"}))
        root = self.extract()
        self.assertEqual(self.stream.getvalue().count(reused), 1)
        self.assertEqual((root / "server.dll").read_bytes(), b"server 1.3 again")

    def test_discs_chosen_one_by_one_from_any_folder(self):
        elsewhere = self.home / "Other drive" / "my second disc.iso"
        elsewhere.parent.mkdir()
        self.disc2.rename(elsewhere)
        root = b.extract_pc_disc(self.disc1, self.update, self.exe, self.paths.work, self.events, self.source, [elsewhere])
        self.assertEqual(b.validate_pc(root, self.source)[1], b.validate_pc(self.pc, self.source)[1])
        # Chosen the other way round: disc 1 is found beside the disc picked by hand.
        b.remove(self.paths.work / "pc_src")
        (self.paths.work / "pc_src.json").unlink()
        root = b.extract_pc_disc(elsewhere, self.update, self.exe, self.paths.work, self.events, self.source, [self.disc1])
        self.assertTrue((root / "TRACKS/fixture.bin").is_file())
        cases = [("not a disc of the same game set", [self.discs / "Other game.iso"]),
                 ("is disc 1, which is already chosen", [self.disc1])]
        for message, extra in cases:
            with self.subTest(message), self.assertRaisesRegex(b.SetupError, message):
                b.extract_pc_disc(self.disc1, self.update, self.exe, self.paths.work, self.events, self.source, extra)
        # Through build: the hand-picked disc is remembered with the others.
        args = self.args()
        args.pc, args.pc_update, args.pc_exe, args.pc_disc = str(self.disc1), str(self.update), str(self.exe), [str(elsewhere)]
        with self.pipeline_stubs():
            self.assertTrue(b.build(args, self.paths, self.events, FakeRunner(self.log, self.child_env, self.paths.work), self.source).exists())
        choices = json.loads((self.paths.state / "source-choices.json").read_text())
        self.assertEqual(choices["pc_discs"], [str(elsewhere)])

    def test_damaged_update_and_images_refused_nothing_left_or_outside_work(self):
        import zipfile
        rtp_update(self.update, dict(self.patched, **{"speed.exe": self.protected}), damaged="GLOBAL\\gameplay.bin")
        with self.assertRaisesRegex(b.SetupError, "gameplay.bin does not match its checksum"):
            self.extract()
        self.assertFalse((self.paths.work / "pc_src").exists())
        rtp_update(self.update, dict(self.patched, **{"speed.exe": self.protected}))
        write(self.home / "not an update.exe", self.data)
        with self.assertRaisesRegex(b.SetupError, "does not carry an RTPatch update"):
            b.extract_pc_disc(self.disc1, self.home / "not an update.exe", self.exe, self.paths.work, self.events, self.source)
        # An unsafe name, or a record missing or added, is refused: the rest of the chain is never used on its own.
        others = dict(self.patched, **{"speed.exe": self.data})
        for name, files in (("unsafe", {"..\\..\\escaped.txt": b"x", **others}), ("missing", dict(list(others.items())[1:])),
                            ("extra", dict(others, **{"GLOBAL\\extra.bin": b"x"}))):
            with self.subTest(name), self.assertRaisesRegex(b.SetupError, "can't read this update"):
                b.extract_pc_disc(self.disc1, rtp_update(self.home / f"{name}.exe", files), self.exe, self.paths.work,
                                  self.events, self.source)
        self.assertFalse((self.home / "escaped.txt").exists())
        good = self.disc1.read_bytes()
        self.disc1.write_bytes(good[:len(good) - 4096])
        with self.assertRaisesRegex(b.SetupError, "truncated"):
            self.extract()
        self.disc1.write_bytes(good)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as z:
            z.writestr("..\\..\\escaped.txt", b"x")
        from setup_kit import pc_disc
        disc = pc_disc.Disc(self.disc2)
        files = {name: disc.read(name, 1 << 30) for name, _, _ in disc.files.values()}
        disc.close()
        make_iso(self.disc2, dict(files, **{"1compressed.zip": buffer.getvalue()}))
        outside = sorted(str(p) for p in self.home.rglob("*") if self.paths.work not in p.parents)
        with self.assertRaisesRegex(b.SetupError, "1compressed.zip on disc 2 contains an unsafe path"):
            self.extract()
        self.assertEqual(sorted(str(p) for p in self.home.rglob("*") if self.paths.work not in p.parents), outside)
        self.assertFalse((self.paths.work / "pc_src").exists())

    def test_iso_reader_bounds_and_names(self):
        from setup_kit import pc_disc
        plain = make_iso(self.home / "plain.iso", {"autorun.inf": b"", "0compressed.zip": b"z"}, joliet=False)
        disc = pc_disc.Disc(plain)  # without Joliet: ISO 9660 names, version suffix dropped
        self.assertEqual(disc.archives, ["0COMPRESSED.ZIP"])
        self.assertEqual(disc.read("0compressed.zip", 10), b"z")
        disc.close()
        data = bytearray(plain.read_bytes())
        struct.pack_into("<I", data, 19 * 2048 + 68 + 2, 10**6)  # the first file (after . and ..) far past the end
        struct.pack_into(">I", data, 19 * 2048 + 68 + 6, 10**6)
        (self.home / "bad.iso").write_bytes(data)
        with self.assertRaisesRegex(pc_disc.DiscError, "truncated"):
            pc_disc.Disc(self.home / "bad.iso")
        write(self.home / "not.iso", b"x" * 40000)
        with self.assertRaisesRegex(pc_disc.DiscError, "no ISO 9660"):
            pc_disc.Disc(self.home / "not.iso")
        for name in ("..", "a/b", "c:d", "x\\y"):
            with self.subTest(name), self.assertRaises(pc_disc.DiscError):
                pc_disc.safe_name(name)

    def test_iso_records_with_attributes_or_disagreeing_copies_refused(self):
        """A file record with extended attributes (its data starts after them) or whose little- and big-endian copies
        of the start or size disagree is refused, never read from the wrong place."""
        from setup_kit import pc_disc
        plain = make_iso(self.home / "plain.iso", {"autorun.inf": b"", "0compressed.zip": b"z"}, joliet=False)
        first = 19 * 2048 + 68  # the first file's record, after . and ..
        for label, at, value in (("attributes", first + 1, b"\1"), ("start", first + 6, b"\0\0\0\x7f"),
                                 ("size", first + 14, b"\0\0\0\x02"), ("root", 16 * 2048 + 156 + 1, b"\1")):
            data = bytearray(plain.read_bytes())
            data[at:at + len(value)] = value
            (self.home / "bad.iso").write_bytes(data)
            with self.subTest(label), self.assertRaisesRegex(pc_disc.DiscError, "damaged"):
                pc_disc.Disc(self.home / "bad.iso").close()

    def test_disc_folder_listing_and_siblings_read_within_their_limits(self):
        """A disc folder with more files than an image may list is refused, and only MAX_SIBLINGS entries beside the
        chosen disc are read (not the whole folder, then cut after sorting)."""
        from setup_kit import pc_disc
        folder = self.home / "Folder disc"
        for i in range(2):
            write(folder / f"f{i}", b"")
        for i in range(2):  # folders are not disc files, but they count towards the limit as well
            (folder / f"d{i}").mkdir()
        with mock.patch.object(pc_disc, "MAX_ROOT_FILES", 3), self.assertRaisesRegex(pc_disc.DiscError, "too many files"):
            pc_disc.Disc(folder)
        for i in range(20):
            write(self.discs / f"unrelated {i}.txt", b"")
        read, iterdir = collections.Counter(), Path.iterdir
        def counted(path):
            for p in iterdir(path):
                read[path] += 1
                yield p
        with mock.patch.object(pc_disc, "MAX_SIBLINGS", 4), mock.patch.object(Path, "iterdir", counted):
            try:
                found = pc_disc.disc_set(self.disc1)
            except pc_disc.DiscError:  # which four are read is the folder's order; only how many matters here
                found = {}
        for disc in found.values():
            disc.close()
        self.assertEqual(read[self.discs], 4)

    def test_archive_overwritten_in_a_disc_folder_is_unpacked_again(self):
        """Overwriting an archive in a copied disc folder leaves the folder's own size and time as they were: the
        earlier unpack must not be reused for the new archive."""
        import zipfile
        from setup_kit import pc_disc
        folders = []
        for image in (self.disc1, self.disc2):
            disc = pc_disc.Disc(image)
            folders.append(self.home / "Copied" / image.stem)
            for name, _, _ in disc.files.values():
                write(folders[-1] / name, disc.read(name, 1 << 30))
            disc.close()
        self.extract(folders[0])
        archive = folders[1] / "1compressed.zip"
        with zipfile.ZipFile(archive) as z:
            members = [(info.filename, z.read(info)) for info in z.infolist()]
        changed = members[0][0]
        folder_times = folders[1].stat()
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
            for name, data in members:
                z.writestr(name, b"changed on the copied disc" if name == changed else data)
        with open(archive, "r+b") as f:  # in place, as an overwrite keeps the folder entry
            f.truncate(0)
            f.write(buffer.getvalue())
        os.utime(folders[1], ns=(folder_times.st_atime_ns, folder_times.st_mtime_ns))
        before = self.stream.getvalue().count("Reusing the game unpacked earlier")
        root = self.extract(folders[0])
        self.assertEqual(self.stream.getvalue().count("Reusing the game unpacked earlier"), before)
        self.assertEqual((root / changed.replace("\\", "/")).read_bytes(), b"changed on the copied disc")


class RTPatchOwnerTests(unittest.TestCase):
    """The adaptive Huffman decoder against the player's own 1.3 update (NFS_PC_UPDATE=<nfsmwpatch1.3.exe>): its
    server.dll must come out as the one recomp/server_module.py pins, and every file must match its checksums."""
    def test_owner_update_decodes_to_pinned_server_dll(self):
        path = os.environ.get("NFS_PC_UPDATE")
        if not path:
            raise unittest.SkipTest("needs the owner's 1.3 update (NFS_PC_UPDATE)")
        from setup_kit import rtpatch
        update = rtpatch.Update(path)
        self.assertEqual([r.path for r in update.records], ["FRONTEND\\FrontB.lzc", "GLOBAL\\gameplay.bin", "GLOBAL\\gameplay.lzc",
                         "GLOBAL\\InGameB.bun", "GLOBAL\\InGameB.lzc", "server.dll", "speed.exe"])
        pinned = re.search(r'SHA256 = "([0-9a-f]{64})"', (ROOT / "recomp/server_module.py").read_text()).group(1)
        self.assertEqual(hashlib.sha256(update.decode(update.record("server.dll"))).hexdigest(), pinned)
        record = update.record("GLOBAL\\gameplay.bin")
        self.assertEqual(len(update.decode(record)), record.size)  # decode() raises on a checksum mismatch
        broken = rtpatch.Record(record.path, record.size, record.w1 ^ 1, record.w2, record.offset, record.length)
        with self.assertRaisesRegex(rtpatch.PatchError, "checksum"):
            update.decode(broken)


class ProtocolTests(Fixture):
    def assert_golden(self, output, success=True):
        records = [json.loads(line) for line in output.splitlines()]
        allowed = {
            "stage": {"ev", "id", "title"}, "progress": {"ev", "id", "done", "total"},
            "features": {"ev", "x360", "missing"}, "warn": {"ev", "id", "msg"},
            "error": {"ev", "id", "msg", "fix", "log"}, "done": {"ev", "app"},
        }
        for record in records:
            self.assertEqual(set(record), allowed[record["ev"]])
        stages = [r["id"] for r in records if r["ev"] == "stage"]
        if success:
            self.assertEqual(stages, [s for s, _ in b.STAGES])
            self.assertEqual(records[-1], dict(ev="done", app=str(self.dest / "NFS Most Wanted Native.app")))
            self.assertFalse(any(r["ev"] == "error" for r in records))
            self.assertTrue((self.dest / "NFS Most Wanted Native.app/Contents/MacOS/NFSMostWanted").is_file())
        else:
            self.assertEqual(records[-1]["ev"], "error")
            self.assertFalse(any(r["ev"] == "done" for r in records))
        return records

    def main_fixture(self, fail=None, check=False):
        original_build = functools.partial(b.build, source_root=self.source)
        fake = FakeRunner(self.log, self.child_env, self.paths.work)
        fake.fail = fail
        with self.pipeline_stubs(), mock.patch.object(b, "ROOT", self.source), \
             mock.patch.object(b, "build", side_effect=original_build), \
             mock.patch.object(b, "Runner", return_value=fake), mock.patch("sys.stdout", self.stream):
            rc = b.main(["--check" if check else "--build", "--pc", str(self.pc), "--dest", str(self.dest),
                         "--work", str(self.paths.work), "--events"])
        return rc

    def test_event_stream_golden_and_false_success_controls(self):
        self.assertEqual(self.main_fixture(), 0)
        output = self.stream.getvalue()
        records = self.assert_golden(output)
        self.assertEqual([r for r in records if r["ev"] == "features"],
                         [dict(ev="features", x360=False, missing=b.X360_MISSING)])
        with self.assertRaises((ValueError, AssertionError)):
            self.assert_golden(output + "compiler output leaked to stdout\n")
        with self.assertRaises(AssertionError):
            self.assert_golden(output.replace('"id":"install"', '"id":"bundle"'))
        shutil.rmtree(self.dest / "NFS Most Wanted Native.app")
        with self.assertRaises(AssertionError):
            self.assert_golden(output)  # broken: done with no installed artifact

    def test_failure_stream_and_check_writes_only_work(self):
        self.assertNotEqual(self.main_fixture(fail="recompile"), 0)
        records = self.assert_golden(self.stream.getvalue(), success=False)
        self.assertEqual(records[-1]["id"], "recompile")
        self.assertTrue(Path(records[-1]["log"]).is_file())
        self.stream.seek(0)
        self.stream.truncate()
        before = {str(p.relative_to(self.home)): b.sha_file(p) for p in self.home.rglob("*")
                  if p.is_file() and self.paths.work not in p.parents}
        self.assertEqual(self.main_fixture(check=True), 0)
        after = {str(p.relative_to(self.home)): b.sha_file(p) for p in self.home.rglob("*")
                 if p.is_file() and self.paths.work not in p.parents}
        self.assertEqual(before, after)
        checks = [json.loads(s) for s in self.stream.getvalue().splitlines()]
        self.assertEqual([r["id"] for r in checks if r["ev"] == "stage"], ["preflight", "pc", "x360"])
        self.assertFalse(any(r["ev"] == "done" for r in checks))
        self.assertTrue(Path((self.paths.work / "run-log.txt").read_text().strip()).is_relative_to(self.paths.work))

    def test_cancel_after_commit_reports_completed_install(self):
        real_progress = b.Events.progress
        def late_cancel(events, done=1, total=1):
            if events.installed is not None:
                raise b.Cancelled()
            return real_progress(events, done, total)
        with mock.patch.object(b.Events, "progress", late_cancel):
            self.assertEqual(self.main_fixture(), 0)
        self.assert_golden(self.stream.getvalue())

    def test_run_log_link_never_truncates_preferences_on_failure(self):
        self.old_install()
        preferences = self.paths.data / "app.txt"
        before = self.snapshot()
        pointer = self.paths.work / "run-log.txt"
        for link in ("symlink", "hardlink"):
            with self.subTest(link=link):
                pointer.unlink(missing_ok=True)
                if link == "symlink":
                    pointer.symlink_to(preferences)
                else:
                    os.link(preferences, pointer)
                self.stream.seek(0)
                self.stream.truncate()
                self.assertNotEqual(self.main_fixture(fail="runtime"), 0)
                self.assertEqual(self.snapshot(), before)
                self.assertFalse(pointer.is_symlink())
                self.assertNotEqual(ins.ident(pointer), ins.ident(preferences))
                self.assertTrue(Path(pointer.read_text().strip()).is_file())

    def test_x360_optional_module_stub_and_failure(self):
        source = self.home / "own.iso"
        write(source, "fixture ISO")
        with mock.patch.object(b.importlib, "import_module", side_effect=ModuleNotFoundError(name="setup_kit.x360")):
            self.assertEqual(b.prepare_x360(source, self.home / "out", self.pc, self.events, self.child_env), {})
        records = [json.loads(s) for s in self.stream.getvalue().splitlines()]
        self.assertEqual([r["ev"] for r in records], ["warn", "features"])
        out = self.home / "out"
        def prepare(src, dst, pc, progress):
            self.assertEqual(os.environ["NFS_PUBLIC"], "1")
            write(dst / "files/file", b"converted")
            progress(1, 2)
            return types.SimpleNamespace(roots={"x360_root": dst / "files"}, features=["audio"], missing=["world"])
        with mock.patch.object(b.importlib, "import_module", return_value=types.SimpleNamespace(prepare=prepare)):
            self.assertEqual(b.prepare_x360(source, out, self.pc, self.events, self.child_env), {"x360_root": out / "files"})
        def fail(*args):
            raise b.SetupError("missing required asset", "Choose a complete Xbox 360 copy.")
        with mock.patch.object(b.importlib, "import_module", return_value=types.SimpleNamespace(prepare=fail)):
            with self.assertRaisesRegex(b.SetupError, "missing required asset"):
                b.prepare_x360(source, out, self.pc, self.events, self.child_env)

    def test_paths_defaults_overrides_and_overlap(self):
        defaults = b.Paths(self.home / "scratch", env={"HOME": str(self.home / "user")})
        self.assertEqual(defaults.data, self.home / "user/Library/Application Support/NFSMW-port")
        self.assertEqual(self.paths.data, self.home / "data")
        paths = b.Paths(self.home / "scratch", env={"NFSMW_SETUP_HOME": str(self.home), "NFSMW_SETUP_DATA": str(self.home / "custom data")})
        self.assertEqual(paths.data, self.home / "custom data")
        with self.assertRaises(b.SetupError):
            b.check_locations(b.Paths(self.pc / "work"), self.pc, None, self.dest, self.source)
        # C unpacks payload under the persistent Setup state directory; neither
        # that nor installing directly in a home folder is a forbidden overlap.
        state_paths = b.Paths(self.home / "scratch", env={"NFSMW_SETUP_HOME": str(self.home),
                            "NFSMW_SETUP_STATE": str(self.source.parent)})
        b.check_locations(state_paths, self.pc, None, self.home, self.source)

    def test_bundle_public_arguments_environment_and_owner_defaults(self):
        runtime = self.home / "bundle payload/runtime"
        write(runtime / "bundle.sh", (ROOT / "runtime/bundle.sh").read_bytes())
        write(runtime / "docs/guide.md", (ROOT / "runtime/docs/guide.md").read_bytes())
        for name in b.prebuilt.LEGAL:
            write(runtime.parent / name, (ROOT / name).read_bytes())
        write(runtime / "boot", b"runtime fixture", True)
        tools = self.home / "bundle tools"
        # Real shell + real plist parser; stub only compiler/icon/signing tools.
        write(tools / "otool", '#!/bin/sh\nprintf "LC_BUILD_VERSION\\n minos 14.0\\n"\n', True)
        write(tools / "git", '#!/bin/sh\ncase "$*" in *--count*) echo 314 ;; *) echo abcdef ;; esac\n', True)
        write(tools / "clang", '''#!/bin/sh
while [ "$1" != -o ]; do shift; done
shift
printf '#!/bin/sh\nprintf icon > "$1"\n' > "$1"
chmod +x "$1"
''', True)
        write(tools / "sips", '#!/bin/sh\nexit 0\n', True)
        write(tools / "iconutil", '''#!/bin/sh
while [ "$1" != -o ]; do shift; done
shift
printf icon > "$1"
''', True)
        write(tools / "codesign", '#!/bin/sh\nexit 0\n', True)
        env = dict(self.child_env, PATH=str(tools) + ":/usr/bin:/bin")
        def bundle(extra=(), overrides=None, expect="NFSMW"):
            out = self.home / "bundled"
            shutil.rmtree(out, ignore_errors=True)
            result = subprocess.run(["/bin/sh", str(runtime / "bundle.sh"), str(out)] + list(extra),
                                    env=dict(env, **(overrides or {})), capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            apps = sorted(x.name for x in out.glob("*.app"))
            self.assertEqual(apps, [expect + ".app"])
            with (out / apps[0] / "Contents/Info.plist").open("rb") as f:
                return plistlib.load(f)
        owner = bundle()
        self.assertEqual((owner["CFBundleName"], owner["CFBundleIdentifier"], owner["CFBundleShortVersionString"], owner["CFBundleVersion"]),
                         ("NFSMW", "local.nfsmw-port", "0.314", "314 (abcdef)"))
        public = bundle(["--name", b.PUBLIC_NAME, "--bundle-id", b.PUBLIC_ID, "--version", b.PUBLIC_VERSION, "--source-id", "content-hash",
                         "--exec-name", "NFSMostWanted"], expect=b.PUBLIC_NAME)
        self.assertEqual(public["CFBundleExecutable"], "NFSMostWanted")
        self.assertEqual(owner["CFBundleExecutable"], "NFSMW")
        self.assertTrue((self.home / "bundled" / (b.PUBLIC_NAME + ".app/Contents/MacOS/NFSMostWanted")).is_file())
        self.assertNotEqual(public["CFBundleIdentifier"], "local.nfsmw-port")
        self.assertEqual(public["CFBundleIdentifier"], b.PUBLIC_ID)
        self.assertEqual(public["CFBundleDisplayName"], b.PUBLIC_NAME)
        self.assertEqual(public["CFBundleShortVersionString"], "4.10.0")
        self.assertEqual(public["CFBundleVersion"], "4.10.0")
        self.assertEqual(public["NFSMWSourceIdentity"], "content-hash")
        via_env = bundle(overrides=dict(NFSMW_BUNDLE_NAME=b.PUBLIC_NAME, NFSMW_BUNDLE_ID=b.PUBLIC_ID, NFSMW_BUNDLE_EXEC="NFSMostWanted",
                                       NFSMW_BUNDLE_VERSION="4.0.1", NFSMW_SOURCE_ID="explicit-source"), expect=b.PUBLIC_NAME)
        self.assertEqual(via_env["CFBundleShortVersionString"], "4.0.1")
        with self.assertRaises(AssertionError):
            self.assertEqual(owner["CFBundleShortVersionString"], "4.0.0")  # broken: git-count version
        result = subprocess.run(["/bin/sh", str(runtime / "bundle.sh"), str(self.home / "bundled"), "--version", "4..0"],
                                env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)

    def test_P5_11_space_message_and_volume_totals(self):
        """P5 item 11: plain ENOSPC + aggregated refusal old 0/2, fixed 2/2."""
        import errno
        error = OSError(errno.ENOSPC, "No space left on device", str(self.paths.cache / "pending"))
        with mock.patch.object(b, "claim", side_effect=error), mock.patch("sys.stdout", self.stream):
            rc = b.main(["--check", "--pc", str(self.pc), "--dest", str(self.dest),
                         "--work", str(self.paths.work), "--events"])
        self.assertEqual(rc, 1)
        event = json.loads(self.stream.getvalue().splitlines()[-1])
        with self.subTest(boundary="before log"):
            self.assertRegex(event["msg"], r"^Not enough space on .+: [0-9.]+ GB more needed\. Free up space there, then press Retry\.$")
            self.assertNotIn("writable", event["fix"])
        with self.subTest(boundary="same volume totals"):
            with mock.patch.object(b.shutil, "disk_usage", return_value=types.SimpleNamespace(free=6 * 1024**3)):
                with self.assertRaises(b.SetupError):
                    b.check_space(self.paths, self.pc, self.dest)
        # Different storage devices must stay separate; each one can block.
        roots = {path: types.SimpleNamespace(stat=lambda i=i: types.SimpleNamespace(st_dev=i))
                 for i, path in enumerate((self.paths.work, self.paths.cache, self.paths.data, self.dest))}
        def root(path):
            return roots[path] if isinstance(path, Path) else path
        with mock.patch.object(b, "volume_root", side_effect=root), \
             mock.patch.object(b, "existing_parent", side_effect=lambda p: p), \
             mock.patch.object(b, "volume_name", side_effect=lambda p: f"Disk {p.stat().st_dev}"):
            requirements = b.space_requirements(self.paths, self.pc, self.dest)
            self.assertEqual(len(requirements), 4)
            for full_device in range(4):
                with self.subTest(full_device=full_device), mock.patch.object(b.shutil, "disk_usage", side_effect=lambda p:
                        types.SimpleNamespace(free=0 if p.stat().st_dev == full_device else 100 * 1024**3)):
                    with self.assertRaisesRegex(b.SetupError, f"Not enough space on Disk {full_device}:"):
                        b.check_space(self.paths, self.pc, self.dest)
        for target in (self.paths.work, self.paths.cache, self.paths.data, self.dest):
            self.stream.seek(0)
            self.stream.truncate()
            error = OSError(errno.ENOSPC, "No space left on device", str(target / "pending"))
            with self.subTest(late_write=target), mock.patch.object(b, "build", side_effect=error), \
                 mock.patch("sys.stdout", self.stream):
                rc = b.main(["--check", "--pc", str(self.pc), "--dest", str(self.dest),
                             "--work", str(self.paths.work), "--events"])
                self.assertEqual(rc, 1)
                event = json.loads(self.stream.getvalue().splitlines()[-1])
                self.assertTrue(event["msg"].startswith("Not enough space on "), event)
                self.assertNotIn("Errno", event["msg"])
        try:
            raise OSError(errno.ENOSPC, "No space left on device", str(self.paths.cache / "pending"))
        except OSError:
            wrapped = b.SetupError("Patch failed", "Make the disc again")
            try:
                raise wrapped from None
            except b.SetupError as error:
                self.assertTrue(str(b.storage_failure(error, self.paths)).startswith("Not enough space on "))
        ordinary = OSError(errno.EACCES, "Denied", str(self.dest))
        self.assertIs(b.storage_failure(ordinary, self.paths), ordinary)

    def test_disk_space_preflight_broken_control(self):
        free = types.SimpleNamespace(free=100 * 1024**3)
        with mock.patch.object(b.shutil, "disk_usage", return_value=free):
            b.check_space(self.paths, self.pc, self.dest)
        with mock.patch.object(b.shutil, "disk_usage", return_value=types.SimpleNamespace(free=1)):
            with self.assertRaises(b.SetupError):
                b.check_space(self.paths, self.pc, self.dest)
            with self.assertRaises(AssertionError):
                with self.assertRaises(b.SetupError):
                    (lambda *a: None)(self.paths, self.pc, self.dest)  # broken: skip space check

    def test_protocol_file_matches_golden_shapes(self):
        schema = json.loads((ROOT / "tools/setup_kit/protocol.json").read_text())
        order = [s for s, _ in b.STAGES]   # Carbon's stage follows runtime (--game carbon only)
        self.assertEqual(schema["$defs"]["stage"]["enum"], order[:order.index("runtime") + 1] + ["carbon"] +
                         order[order.index("runtime") + 1:] + ["stage", "cars"])
        names = {shape["properties"]["ev"]["const"] for shape in schema["oneOf"]}
        self.assertEqual(names, {"stage", "progress", "warn", "error", "features", "done", "ready", "source-missing",
                                 "cars-recognition", "cars-plan", "cars-progress", "cars-result"})
        self.assertTrue(all(s["additionalProperties"] is False for s in schema["oneOf"]))

    def test_media_pin_missing_and_wrong_even_with_extracted_tree(self):
        sandbox = self.home / "media fixture"
        shutil.copy2(ROOT / "media/build_ffmpeg.sh", self.home / "ffmpeg-script-copy")
        write(sandbox / "media/build_ffmpeg.sh", (ROOT / "media/build_ffmpeg.sh").read_bytes())
        (sandbox / "third_party/src/ffmpeg-7.1.1").mkdir(parents=True)
        for bad, expected in ((None, "source missing"), (b"wrong archive", "checksum failed")):
            if bad is not None:
                write(sandbox / "third_party/dl/ffmpeg-7.1.1.tar.xz", bad)
            result = subprocess.run(["/bin/sh", str(sandbox / "media/build_ffmpeg.sh")], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(expected, result.stderr)
        self.assertFalse((sandbox / "third_party/ffmpeg").exists())



class PubSafetyTests(Fixture):
    """Setup data-safety review, one test per finding.

    Each test was run against the code before its fix and failed there.
    """
    APP = "NFS Most Wanted Native.app"
    prepared = TransactionTests.prepared
    fresh_prepared = TransactionTests.fresh_prepared
    legacy_app = TransactionTests.legacy_app
    install = TransactionTests.install

    def apps_with_digest(self, digest):
        return [p for p in self.dest.iterdir() if p.is_dir() and ins.digest_tree(p) == digest]

    # F1 (HIGH, install.py:245): rollback must only restore a backup this transaction created.
    def test_F1_raced_3x_backup_name_never_deletes_original(self):
        legacy = self.legacy_app()
        before = ins.digest_tree(legacy)
        real_name = ins.legacy_name
        raced = []
        def racing_name(dest):
            path = real_name(dest)
            if not raced:  # another process takes the name right after Setup checked it was free
                raced.append(path)
                write(path / "Contents/other", b"someone else's app")
            return path
        with mock.patch.object(ins, "legacy_name", side_effect=racing_name):
            try:
                self.install(self.fresh_prepared())
            except Exception:
                pass
        self.assertTrue(raced)
        self.assertEqual((raced[0] / "Contents/other").read_bytes(), b"someone else's app", "unrelated app moved or deleted")
        self.assertEqual(len(self.apps_with_digest(before)), 1, "the original 3.x app must survive exactly once")

    def test_F1_occupied_backup_name_never_deletes_4x_original(self):
        self.prepared()
        before = self.snapshot()
        token = types.SimpleNamespace(hex="f" * 32)
        other = self.dest / f".{self.APP}.backup-{token.hex}"
        write(other / "Contents/other", b"not ours")
        with mock.patch.object(ins.uuid, "uuid4", return_value=token):
            try:
                self.install(self.prepared())
            except Exception:
                pass
        self.assertEqual((other / "Contents/other").read_bytes(), b"not ours")
        app = self.dest / self.APP
        self.assertTrue((app / "Contents/old").exists() or (app / "Contents/new").exists(), "app missing")
        if (app / "Contents/old").exists():
            self.assertEqual(before, self.snapshot())

    # F2 (HIGH, backend.py:660): overlap by filesystem identity, not spelling.
    def test_F2_case_and_symlink_aliases_overlap(self):
        base = self.home / "X"
        archive = base / "Games/pc_src/game.zip"
        write(archive, b"zip bytes")
        if not os.path.samefile(base / "games", base / "Games"):
            self.skipTest("temp volume is case-sensitive")
        pc = b.clean_path(archive)
        for work in (base / "games", base / "GAMES/pc_src", base / "games/PC_SRC/game.zip/inner"):
            with self.subTest(work=work), self.assertRaises(b.SetupError):
                b.check_locations(b.Paths(work), pc, None, self.dest, self.source)
        # Reverse containment, through a case alias: work inside the (zip's) game folder spelled differently.
        game = base / "Folder"
        game.mkdir()
        with self.assertRaises(b.SetupError):
            b.check_locations(b.Paths(base / "FOLDER/work"), b.clean_path(game), None, self.dest, self.source)
        (self.home / "link").symlink_to(base / "Games")
        with self.assertRaises(b.SetupError):
            b.check_locations(b.Paths(self.home / "link"), pc, None, self.dest, self.source)
        # The CLI refuses before creating anything at the work path.
        with mock.patch("sys.stdout", io.StringIO()) as out:
            rc = b.main(["--check", "--pc", str(archive), "--dest", str(self.dest), "--work", str(base / "games"), "--events"])
        self.assertEqual(rc, 1)
        self.assertIn("overlaps a protected folder", out.getvalue())
        self.assertEqual(sorted(p.name for p in (base / "Games").iterdir()), ["pc_src"])
        self.assertEqual(archive.read_bytes(), b"zip bytes")
        # Control: separate folders still pass.
        b.check_locations(b.Paths(base / "work"), pc, None, self.dest, self.source)

    # F3 (HIGH, backend.py:657): 3.x saves and 4.0 data are protected from every writable location.
    def test_F3_legacy_saves_and_data_protected(self):
        legacy = self.home / "legacy"
        saves = legacy / "Documents/NFS Most Wanted"
        write(saves / "ALIAS/ALIAS", game_save())
        self.paths.data.mkdir(parents=True, exist_ok=True)
        cases = {
            "work = 3.x save folder": {"work": saves},
            "work encloses 3.x profile": {"work": legacy.parent / "outer", "NFSMW_SETUP_LEGACY": str(legacy.parent / "outer/legacy")},
            "work inside 4.0 data": {"work": self.paths.data / "w"},
            "cache = 3.x profile": {"work": self.home / "w2", "NFSMW_SETUP_CACHE": str(legacy)},
            "data inside 3.x profile": {"work": self.home / "w3", "NFSMW_SETUP_DATA": str(legacy / "data")},
            "logs inside 3.x saves": {"work": self.home / "w4", "NFSMW_SETUP_LOGS": str(saves / "logs")},
        }
        for name, case in cases.items():
            env = {"NFSMW_SETUP_HOME": str(self.home), **{k: v for k, v in case.items() if k != "work"}}
            with self.subTest(name), self.assertRaises(b.SetupError):
                b.check_locations(b.Paths(case["work"], env=env), self.pc, None, self.dest, self.source)
        b.check_locations(b.Paths(self.home / "w5"), self.pc, None, self.dest, self.source)  # control

    # F4 (MEDIUM, backend.py:664): the destination must not be inside an input or profile folder.
    def test_F4_destination_inside_inputs_or_profiles_refused(self):
        x360 = self.home / "x360 copy"
        x360.mkdir()
        archive = self.home / "dl/game.zip"
        write(archive, b"zip")
        legacy = self.home / "legacy"
        legacy.mkdir()
        cases = {"pc folder": (self.pc, None, self.pc), "pc child": (self.pc, None, self.pc / "GLOBAL"),
                 "x360 copy": (self.pc, x360, x360 / "apps"), "3.x profile": (self.pc, None, legacy),
                 "4.0 data": (self.pc, None, self.paths.data), "zip's folder": (archive, None, archive.parent),
                 "work": (self.pc, None, self.paths.work / "dest")}
        for name, (pc, x, dest) in cases.items():
            with self.subTest(name), self.assertRaises(b.SetupError):
                b.check_locations(self.paths, b.clean_path(pc), b.clean_path(x) if x else None, b.clean_path(dest), self.source)
        # Controls: a separate folder, the home folder itself (only the app's own names are written there) and a
        # subfolder of the zip's folder (zip in ~, app in ~/Applications).
        b.check_locations(self.paths, self.pc, x360, self.dest, self.source)
        b.check_locations(self.paths, b.clean_path(archive), x360, self.home, self.source)
        b.check_locations(self.paths, b.clean_path(archive), x360, b.clean_path(archive.parent / "Applications"), self.source)

    def test_file_destination_error_names_selected_input(self):
        # On 7942da5b both filenames fail (2/2); with the fix both pass (2/2).
        for name in ("Game Archive.7z", "Game Disc {1}.iso"):
            selected = self.home / "downloads" / name
            write(selected, b"synthetic input")
            with self.subTest(name=name), self.assertRaisesRegex(b.SetupError, re.escape(name)):
                b.check_locations(self.paths, selected, None, selected.parent, self.source)

    # F5 (MEDIUM, backend.py:222): a child started just before a Cancel is still stopped.
    def test_F5_cancel_between_spawn_and_registration_leaves_no_child(self):
        code = """import os,pathlib,signal,subprocess,sys,time
sys.dont_write_bytecode=True
sys.path.insert(0,sys.argv[1])
from setup_kit.backend import Runner,cancellation,Cancelled
p=pathlib.Path(sys.argv[2])
real=subprocess.Popen
def popen(*a,**k):
 child=real(*a,**k)
 end=time.monotonic()+5
 while not (p/'ready').exists() and time.monotonic()<end: time.sleep(0.01)
 os.kill(os.getpid(),signal.SIGTERM)  # Cancel lands after the fork, before Runner sees the child
 for _ in range(100000): pass
 return child
subprocess.Popen=popen
with (p/'child.log').open('wb') as log:
 r=Runner(log,dict(os.environ),p)
 try:
  with cancellation(): r.run(['/bin/sh','-c','echo $$ > "$1"; : > "$2"; exec sleep 30','child',str(p/'pid'),str(p/'ready')])
 except Cancelled: sys.exit(130)
sys.exit(3)
"""
        proc = subprocess.run([sys.executable, "-I", "-c", code, str(ROOT / "tools"), str(self.home)],
                              capture_output=True, timeout=30, start_new_session=True)
        pid = int((self.home / "pid").read_text())
        def alive():
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return False
            return True
        deadline = time.monotonic() + 3
        while alive() and time.monotonic() < deadline:
            time.sleep(0.05)
        survived = alive()
        if survived:
            os.killpg(pid, signal.SIGKILL)
        self.assertEqual(proc.returncode, 130, proc.stderr.decode()[-2000:])
        self.assertFalse(survived, "the child outlived Cancel")

    def test_F5_cancel_during_child_cleanup_is_not_lost(self):
        # A Cancel that lands while Runner stops a finished child must still stop Setup (it used to be ignored, so the
        # build went on, possibly into the install, where the app no longer force-kills it).
        real = os.killpg
        sent = []
        def killpg(pid, sig):
            if sig == signal.SIGTERM and not sent:
                sent.append(sig)
                os.kill(os.getpid(), signal.SIGTERM)
            real(pid, sig)
        runner = b.Runner(self.log, dict(os.environ), self.paths.work)
        with b.cancellation(), mock.patch.object(b.os, "killpg", side_effect=killpg):
            with self.assertRaises(b.Cancelled):
                runner.run(["/usr/bin/true"])
        self.assertEqual(sent, [signal.SIGTERM])

    # F6 (MEDIUM, install.py:231): a crash between any two install steps is recovered on the next run.
    def plant_old(self, dest, data, scenario):
        if scenario == "3x":
            app = dest / self.APP
            self.plant_app(app, "3.4.1", executable="Launch")
            write(app / "Contents/MacOS/SpeedRecomp", b"3.x engine", True)
        else:
            self.plant_app(dest / self.APP, "4.0.0")
            write(dest / self.APP / "Contents/old", b"existing app")
        write(data / "roots.txt", "pc_root=/existing/game\n")
        write(data / "app.txt", "# preserve me\n game_dir = /old/game\nfullscreen=0\n")
        return ins.digest_tree(dest / self.APP), (data / "roots.txt").read_bytes(), (data / "app.txt").read_bytes()

    def run_crashing(self, prepared, dest, data, crash_at, verify):
        pid = os.fork()
        if pid == 0:  # child: the Setup that loses power
            code = 1
            try:
                seen = []
                def step(name):
                    seen.append(name)
                    if len(seen) == crash_at:
                        os._exit(77)
                ins._step = step
                ins.transactional_install(*prepared, data, dest, verify, running=lambda: None)
                code = 0
            except BaseException:
                code = 2
            finally:
                os._exit(code)
        return os.waitpid(pid, 0)[1] >> 8

    def assert_consistent(self, dest, data, old_app, old_roots, old_profile, new_app, scenario, label):
        app = dest / self.APP
        self.assertTrue(app.is_dir(), f"{label}: app missing")
        got = ins.digest_tree(app)
        leftovers = [p.name for d in (dest, data) for p in d.iterdir() if ".new-" in p.name or "journal" in p.name]
        self.assertEqual(leftovers, [], label)
        live = sorted(p.name for p in (data / "installs").iterdir() if not p.name.startswith(".")) if (data / "installs").exists() else []
        if got == (old_app if scenario == "4x" else old_app):
            self.assertEqual((data / "roots.txt").read_bytes(), old_roots, f"{label}: old app with new roots")
            self.assertEqual((data / "app.txt").read_bytes(), old_profile, f"{label}: old app with new profile")
            self.assertEqual(live, [], f"{label}: unused generation left")
            self.assertEqual([p.name for p in dest.iterdir() if not p.name.startswith(".")], [self.APP], label)
        else:
            self.assertEqual(got, new_app, f"{label}: mixed or partial app")
            roots = dict(l.split("=", 1) for l in (data / "roots.txt").read_text().splitlines())
            self.assertTrue((Path(roots["pc_root"]) / "speed.exe").is_file(), f"{label}: roots point nowhere")
            self.assertIn(b"game_dir=" + os.fsencode(roots["pc_root"]) + b"\n", (data / "app.txt").read_bytes(), label)
            self.assertEqual(len(live), 1, label)
            kept = [p for p in dest.iterdir() if p.is_dir() and ins.digest_tree(p) == old_app]
            backups = [p.name for p in dest.iterdir() if ".backup-" in p.name]
            self.assertEqual(backups, [], f"{label}: backup left in the Applications folder (M4 F5)")
            if scenario == "3x":
                self.assertEqual([p.name for p in kept], ["NFS Most Wanted Native 3.x.app"], f"{label}: previous app lost")
            else:  # a replaced 4.x app is removed once the new one is installed and verified
                self.assertEqual(kept, [], label)

    def test_F6_crash_between_every_step_recovers_old_or_new(self):
        prepared = self.fresh_prepared()
        write(prepared[0] / "Contents/new", b"new app")
        new_app = ins.digest_tree(prepared[0])
        points = collections.Counter()
        for scenario in ("4x", "3x", "rollback"):
            k = 1
            while True:
                dest, data = self.home / f"d-{scenario}-{k}", self.home / f"p-{scenario}-{k}"
                old_app, old_roots, old_profile = self.plant_old(dest, data, "3x" if scenario == "3x" else "4x")
                calls = []
                def verify(path):
                    calls.append(path)
                    if scenario == "rollback" and len(calls) == 2:
                        raise b.SetupError("injected final verification failure")
                status = self.run_crashing(prepared, dest, data, k, verify)
                ins.recover_interrupted(data)  # the next Setup run
                self.assert_consistent(dest, data, old_app, old_roots, old_profile, new_app,
                                       "3x" if scenario == "3x" else "4x", f"{scenario} crash at step {k}")
                if status != 77:
                    self.assertEqual(status, 2 if scenario == "rollback" else 0)
                    break
                points[scenario] += 1
                k += 1
        # Every rename, the commit point and cleanup are crash points (8+ install steps, 4+ rollback steps).
        self.assertGreaterEqual(points["4x"], 8, points)
        self.assertGreaterEqual(points["3x"], 8, points)
        self.assertGreaterEqual(points["rollback"], 4, points)

    # Extraction failures leave no partial pc_src (a later run never trusts half an unpacked game).
    def test_failed_extraction_removes_partial_work(self):
        archive = PCZipTests.make_zip(self, self.home / "game.zip")
        real_open = b.open if hasattr(b, "open") else open
        written = []
        import builtins
        def failing_open(path, mode="r", *a, **k):
            f = builtins.open(path, mode, *a, **k)
            if mode == "xb":
                written.append(path)
                if len(written) == 3:
                    f.close()
                    raise OSError(errno.ENOSPC, "injected disk full")
            return f
        with mock.patch.object(b, "open", side_effect=failing_open, create=True), self.assertRaises(OSError):
            b.extract_pc_zip(archive, self.paths.work, self.events)
        self.assertGreaterEqual(len(written), 3)
        self.assertFalse((self.paths.work / "pc_src").exists(), "partial extraction left behind")
        self.assertFalse((self.paths.work / "pc_src.json").exists())
        root = b.extract_pc_zip(archive, self.paths.work, self.events)  # a clean retry still works
        self.assertEqual(ins.digest_tree(root), ins.digest_tree(self.pc))


class M4Tests(Fixture):
    """"""
    APP = "NFS Most Wanted Native.app"
    fresh_prepared = TransactionTests.fresh_prepared
    main_fixture = ProtocolTests.main_fixture

    # F1 BLOCKER: the app's default work folder has a space ("Application Support"). FFmpeg's configure does
    # `: > $TMPC` with TMPC under $TMPDIR, unquoted: "ambiguous redirect", every app-driven build failed.
    def test_F1_space_in_work_path_gives_tools_a_shell_safe_tmpdir(self):
        work = self.home / "Library/Application Support/NFSMW-Setup/work"
        work.mkdir(parents=True)
        sys_tmp = self.home / "systmp"
        sys_tmp.mkdir()
        with b.tool_tmp(work, dict(TMPDIR=str(sys_tmp))) as tmp:
            env = b.child_environment(work / "stage/roots.txt", work, dict(HOME=str(self.home)), tmp=tmp)
            # FFmpeg 7.1.1 configure, lines 4060/4766: tmpfile() then `: > $TMPC`, both unquoted.
            script = 'TMPC="$TMPDIR/ffconf.x/test.c"; mkdir -p "$TMPDIR/ffconf.x"; : > $TMPC && test -f $TMPC && echo ok'
            r = subprocess.run(["/bin/sh", "-c", script], env=env, capture_output=True, text=True)
            self.assertEqual((r.returncode, r.stdout.strip()), (0, "ok"), r.stderr)
            self.assertNotIn(" ", env["TMPDIR"] + env["CLANG_MODULE_CACHE_PATH"])
            self.assertTrue(env["CLANG_MODULE_CACHE_PATH"].startswith(env["TMPDIR"]))
            self.assertEqual(os.stat(tmp).st_mode & 0o777, 0o700)
        self.assertFalse(tmp.exists(), "the private temporary folder outside work is removed afterwards")
        plain = self.home / "plain-work"   # a shell-safe work folder keeps <work>/tmp (unchanged behaviour)
        plain.mkdir()
        with b.tool_tmp(plain) as tmp:
            self.assertEqual(tmp, plain / "tmp")

    # F5: each reinstall over a 4.x app left ".NFS Most Wanted Native.app.backup-<id>" (25 MB) in Applications.
    # (TransactionTests.test_T10_commit... and PubSafetyTests.test_F6_crash... assert the same at every crash point.)
    def test_F5_reinstall_leaves_no_backup_and_rollback_still_restores(self):
        self.plant_app(self.dest / self.APP, "4.0.0")
        write(self.dest / self.APP / "Contents/old", b"existing app")
        old = ins.digest_tree(self.dest / self.APP)
        count = []
        def fail_final(path):
            count.append(1)
            if len(count) == 2:
                raise b.SetupError("injected final verification failure")
        with self.assertRaises(b.SetupError):
            ins.transactional_install(*self.fresh_prepared(), self.paths.data, self.dest, fail_final, running=lambda: None)
        self.assertEqual(ins.digest_tree(self.dest / self.APP), old, "rollback restores the previous app")
        ins.transactional_install(*self.fresh_prepared(), self.paths.data, self.dest, lambda p: None, running=lambda: None)
        self.assertEqual(sorted(p.name for p in self.dest.iterdir()), [self.APP])

    # F4a: every rebuild added <data>/installs/<id> (2.8-7.9 GB) for good.
    def test_F4a_keeps_current_and_previous_generation_only(self):
        installs = self.paths.data / "installs"
        decoys = [installs / "player-notes", installs / ("f" * 31)]
        generations = []
        for i in range(4):
            if i == 1:  # beside Setup's own generations (installs/ is now Setup's marked folder)
                for d in decoys:
                    write(d / "keep", "not a generation".encode())
                os.symlink(self.home, installs / ("e" * 32))
            app, data, roots = self.fresh_prepared()
            write(data / "pc/speed.exe", f"player data {i}")
            ins.transactional_install(app, data, roots, self.paths.data, self.dest, lambda p: None, running=lambda: None)
            pc_root = dict(l.split("=", 1) for l in (self.paths.data / "roots.txt").read_text().splitlines())["pc_root"]
            generations.append(Path(pc_root).relative_to(installs).parts[0])
            shutil.rmtree(self.home / "prepared")
        live = sorted(p.name for p in installs.iterdir() if b.re.fullmatch(r"[0-9a-f]{32}", p.name) and not p.is_symlink())
        self.assertEqual(live, sorted(generations[-2:]), "current + the one it replaced")
        self.assertEqual((installs / generations[-1] / "pc/speed.exe").read_bytes(), b"player data 3")
        for d in decoys:
            self.assertEqual((d / "keep").read_bytes(), b"not a generation")
        self.assertTrue((installs / ("e" * 32)).is_symlink() and self.home.is_dir())

    # F4 peak: the staged data (2.8-7.9 GB) is moved into the install, not copied; another volume still copies.
    def test_F4_staged_data_moved_or_copied_and_rollback_keeps_old_install(self):
        def install(prepared, verify=lambda p: None):
            return ins.transactional_install(*prepared, self.paths.data, self.dest, verify, running=lambda: None, move_data=True)
        app, data, roots = self.fresh_prepared()
        before = os.stat(data / "pc/speed.exe").st_ino
        install((app, data, roots))
        pc = Path(dict(l.split("=", 1) for l in (self.paths.data / "roots.txt").read_text().splitlines())["pc_root"])
        self.assertFalse(data.exists(), "moved, not copied")
        self.assertEqual(os.stat(pc / "speed.exe").st_ino, before)
        old_roots = (self.paths.data / "roots.txt").read_bytes()
        shutil.rmtree(self.home / "prepared")
        prepared = self.fresh_prepared()
        real = ins.rename_new
        def cross_device(src, dst):
            if Path(src) == prepared[1]:
                raise OSError(errno.EXDEV, "cross-device")
            return real(src, dst)
        count = []
        def fail_final(path):
            count.append(1)
            if len(count) == 2:
                raise b.SetupError("injected final verification failure")
        with mock.patch.object(ins, "rename_new", side_effect=cross_device), self.assertRaises(b.SetupError):
            install(prepared, fail_final)
        self.assertTrue((prepared[1] / "pc/speed.exe").is_file(), "copied (another volume): staging kept")
        self.assertEqual((self.paths.data / "roots.txt").read_bytes(), old_roots)
        self.assertTrue((pc / "speed.exe").is_file(), "the previous install is untouched")
        count.clear()
        with self.assertRaises(b.SetupError):
            install(prepared, fail_final)  # moved this time, then rolled back
        self.assertEqual((self.paths.data / "roots.txt").read_bytes(), old_roots)
        self.assertEqual(sorted(p.name for p in (self.paths.data / "installs").iterdir() if p.name[0] != "."), [pc.parent.name])

    # F4b: after a successful build the work folder kept stage/ (7.9 GB), check-x360/ (5 GB) and pc_src/ (2.8 GB).
    def test_F4b_work_copies_removed_after_success_only(self):
        for name in ("check-x360/prepared/f", "pc_src/game/f"):
            write(self.paths.work / name, b"old copy")
        write(self.paths.data.parent / "cache/builds" / ("a" * 64) / "source/f", b"stale build cache")
        fake = FakeRunner(self.log, self.child_env, self.paths.work)
        fake.fail = "bundle"
        with self.assertRaises(b.SetupError):
            self.build(fake)
        self.assertTrue((self.paths.work / "stage").is_dir(), "a failed build keeps its work (resume)")
        self.assertTrue((self.paths.work / "pc_src/game/f").is_file())
        self.assertIsNotNone(self.build())
        for name in ("stage", "check-x360", "pc_src"):
            self.assertFalse((self.paths.work / name).exists(), name)
        builds = [p.name for p in (self.paths.cache / "builds").iterdir()]
        self.assertEqual(len(builds), 1, builds)
        self.assertNotEqual(builds, ["a" * 64])

    # F4c: --check converted the whole X360 copy and --build converted it again (about 6 minutes each).
    def test_F4c_build_reuses_the_checked_x360_conversion(self):
        iso = self.home / "own.iso"
        write(iso, "fixture ISO")
        calls = []
        def prepare(src, dst, pc, progress):
            calls.append(dst)
            write(dst / "prepared/files/file", b"converted")
            write(dst / "prepared/assets/a", b"asset")
            return types.SimpleNamespace(roots={"x360_root": dst / "prepared/files", "assets_root": dst / "prepared/assets"},
                                         features=["audio"], missing=[])
        module = types.SimpleNamespace(prepare=prepare)
        def run(check):
            args = types.SimpleNamespace(pc=str(self.pc), dest=str(self.dest), x360=str(iso), build=not check, check=check)
            with self.pipeline_stubs(), mock.patch.object(b.importlib, "import_module", return_value=module):
                return b.build(args, self.paths, self.events, FakeRunner(self.log, self.child_env, self.paths.work), self.source)
        run(True)
        pc_only = types.SimpleNamespace(pc=str(self.pc), dest=str(self.dest), x360=None, build=False, check=True)
        with self.pipeline_stubs():  # the app's PC-folder check after the X360 check keeps the converted copy
            b.build(pc_only, self.paths, self.events, FakeRunner(self.log, self.child_env, self.paths.work), self.source)
        run(False)
        self.assertEqual(len(calls), 1, "the build converted the X360 copy again")
        roots = dict(l.split("=", 1) for l in (self.paths.data / "roots.txt").read_text().splitlines())
        self.assertEqual((Path(roots["x360_root"]) / "file").read_bytes(), b"converted")
        self.assertIn("Reusing the Xbox 360 files converted by the check.", self.stream.getvalue())
        # Persistent entries survive installs. A timestamp change alone, a damaged
        # disposable check copy, and builds without a check all reuse verified bytes.
        run(True)
        os.utime(iso, ns=(1, 1))
        run(False)
        self.assertEqual(len(calls), 1)
        run(True)
        write(self.paths.work / "check-x360/prepared/files/file", b"tampered")
        run(False)
        self.assertEqual(len(calls), 1)
        roots = dict(l.split("=", 1) for l in (self.paths.data / "roots.txt").read_text().splitlines())
        self.assertEqual((Path(roots["x360_root"]) / "file").read_bytes(), b"converted")
        run(False)
        self.assertEqual(len(calls), 1)

    # F4d: X360 progress reached 760/760 in 1 s (disc copy) and then said nothing for about 6 minutes of conversion.
    def test_F4d_x360_progress_covers_conversion_and_validation(self):
        from setup_kit import x360
        events = []
        def extract(src, stage, pc_root, work, progress):
            for i in range(1, 761):
                progress(i, 760)
            return {"names_found": 1, "entries": 1}
        with mock.patch.object(x360, "Source"), mock.patch.object(x360, "_extract", side_effect=extract), \
             mock.patch.object(x360, "_run"), mock.patch.object(x360, "_validate"):
            x360.prepare(self.home / "own.iso", self.home / "x360-out", self.pc, lambda d, t: events.append(d / t))
        after_copy = events[759]
        self.assertLess(after_copy, 0.1, "copying the files out of the disc is not most of the work")
        later = sorted(set(events[760:]))
        self.assertGreaterEqual(len(later), 5, f"conversion steps reported: {later}")
        self.assertEqual(events, sorted(events), "progress never goes back")
        self.assertEqual(events[-1], 1.0)

    # F8: FFmpeg built inside the audio stage reported its errors as "audio".
    def test_F8_ffmpeg_failure_is_reported_as_ffmpeg(self):
        self.assertNotEqual(self.main_fixture(fail="ffmpeg"), 0)
        records = [json.loads(l) for l in self.stream.getvalue().splitlines()]
        self.assertEqual(records[-1]["ev"], "error")
        self.assertEqual(records[-1]["id"], "ffmpeg")
        self.assertEqual([r["id"] for r in records if r["ev"] == "stage"][-1], "audio", "stage order unchanged")


class ReviewTests(Fixture):
    """
"""
    APP = "NFS Most Wanted Native.app"
    fresh_prepared = TransactionTests.fresh_prepared

    def run_main(self, work, check=False):
        original_build = functools.partial(b.build, source_root=self.source)
        fake = FakeRunner(self.log, self.child_env, Path(work))
        out = io.StringIO()
        with self.pipeline_stubs(), mock.patch.object(b, "ROOT", self.source), \
             mock.patch.object(b, "build", side_effect=original_build), \
             mock.patch.object(b, "Runner", return_value=fake), mock.patch("sys.stdout", out):
            rc = b.main(["--check" if check else "--build", "--pc", str(self.pc), "--dest", str(self.dest),
                         "--work", str(work), "--events"])
        return rc, out.getvalue()

    # 1 HIGH: an accepted existing --work (~/Documents) lost its pc_src/ and check-x360/ after a successful build.
    def test_R1_existing_user_folder_is_never_used_as_work(self):
        docs = self.home / "Documents"
        for rel in ("pc_src/thesis.txt", "check-x360/photos.txt", "notes.txt"):
            write(docs / rel, "the player's own file")
        before = {str(p): b.sha_file(p) for p in docs.rglob("*") if p.is_file()}
        rc, out = self.run_main(docs)
        self.assertNotEqual(rc, 0, out)
        self.assertIn("already holds other files", out)
        self.assertEqual(before, {str(p): b.sha_file(p) for p in docs.rglob("*") if p.is_file()})
        self.assertFalse((docs / getattr(ins, "OWNER_MARK", ".nfsmw-setup-owned")).exists())
        # A new folder is Setup's (marked) and its copies go after success; so does the default work folder that
        # holds only Setup's own names from an earlier run.
        fresh = self.home / "fresh-work"
        self.assertEqual(self.run_main(fresh)[0], 0)
        self.assertTrue(ins.owned(fresh, self.paths.state))
        self.assertFalse((fresh / "stage").exists())
        default = self.paths.default_work
        write(default / "stage/data/pc/speed.exe", "old staging")
        write(default / "run-log.txt", "old log path")
        self.assertEqual(self.run_main(default)[0], 0)
        self.assertTrue(ins.owned(default, self.paths.state) and not (default / "stage").exists())

    # 2 HIGH: pruning followed a symbolic-linked installs/ (and builds/) parent into a folder outside Setup's.
    def test_R2_symlinked_installs_and_builds_parents_are_never_pruned(self):
        outside = self.home / "outside"
        victims = [outside / ("a" * 32), outside / ("b" * 32), outside / ("c" * 64)]
        for v in victims:
            write(v / "precious", "not Setup's")
        self.paths.data.mkdir(parents=True, exist_ok=True)
        os.symlink(outside, self.paths.data / "installs")
        for i in range(3):
            app, data, roots = self.fresh_prepared()
            ins.transactional_install(app, data, roots, self.paths.data, self.dest, lambda p: None, running=lambda: None)
            shutil.rmtree(self.home / "prepared")
        for v in victims:
            self.assertTrue((v / "precious").is_file(), v)
        self.paths.cache.mkdir(parents=True, exist_ok=True)
        ins.mark_owned(self.paths.cache, self.paths.state)
        os.symlink(outside, self.paths.cache / "builds")
        b.clean_work(self.paths, self.events, "d" * 64)
        self.assertTrue((outside / ("c" * 64) / "precious").is_file())

    # 3 HIGH: generation detection was lexical: installs/A/../B/pc counted as A, so B (really in use) was pruned.
    def test_R3_roots_resolved_before_pruning(self):
        install = lambda: ins.transactional_install(*self.fresh_prepared(), self.paths.data, self.dest, lambda p: None,
                                                    running=lambda: None)
        install()
        shutil.rmtree(self.home / "prepared")
        installs = self.paths.data / "installs"
        a, b_, c = "a" * 32, "b" * 32, "c" * 32
        for g in (a, b_, c):
            write(installs / g / "pc/speed.exe", g)
            ins.mark_owned(installs / g, self.paths.data)  # generations Setup made (recorded), as an earlier install leaves them
        (self.paths.data / "roots.txt").write_text(f"pc_root={installs}/{a}/../{b_}/pc\n")
        install()
        live = sorted(p.name for p in installs.iterdir() if not p.name.startswith("."))
        self.assertIn(b_, live, "the generation roots.txt really used was removed")
        self.assertNotIn(c, live)
        shutil.rmtree(self.home / "prepared")
        # Uncertain: a root spelled into installs that resolves elsewhere: nothing is pruned.
        elsewhere = self.home / "elsewhere"
        write(elsewhere / "pc/speed.exe", "x")
        os.symlink(elsewhere, installs / ("d" * 32))
        (self.paths.data / "roots.txt").write_text(f"pc_root={installs}/{'d' * 32}/pc\n")
        before = sorted(p.name for p in installs.iterdir())
        install()
        after = sorted(p.name for p in installs.iterdir())
        self.assertEqual(set(before) - set(after), set(), "pruned although retention was uncertain")

    # 4 HIGH: committed-journal recovery removed the old app's backup without checking the new app was there.
    def test_R4_committed_recovery_checks_the_new_app_and_installs_are_serialized(self):
        prepared = self.fresh_prepared()
        self.plant_app(self.dest / self.APP, "4.0.0")
        write(self.dest / self.APP / "Contents/old", "existing app")
        old = ins.digest_tree(self.dest / self.APP)
        real_step = ins._step
        def crash(name):
            if name == "journal-committed":
                raise SystemExit(77)  # stands in for power loss right after the commit point
        with mock.patch.object(ins, "_step", side_effect=crash), self.assertRaises(SystemExit):
            ins.transactional_install(*prepared, self.paths.data, self.dest, lambda p: None, running=lambda: None)
        self.assertTrue(ins.journal_path(self.paths.data).exists())
        shutil.rmtree(self.dest / self.APP)  # the new app is gone (deleted, or a damaged disk)
        self.assertEqual(ins.recover_interrupted(self.paths.data), "restored")
        self.assertEqual(ins.digest_tree(self.dest / self.APP), old, "the previous app was deleted with no app left")
        # Serialized: a second installer into the same folder is refused while one holds it.
        with ins.dest_lock(self.dest):
            with self.assertRaisesRegex(ins.InstallError, "Another Setup is installing"):
                ins.transactional_install(*self.fresh_prepared(), self.home / "other-profile", self.dest, lambda p: None,
                                          running=lambda: None)

    # 5 MEDIUM: the "private" temp folder was named after work only and an existing 0700 folder was adopted.
    def test_R5_per_run_temp_folder_never_adopted_or_shared(self):
        work = self.home / "Application Support/work"
        work.mkdir(parents=True)
        sys_tmp = self.home / "systmp"
        sys_tmp.mkdir()
        env = dict(TMPDIR=str(sys_tmp))
        def tmp_ctx():
            made = b.tool_tmp(work, env)
            return b.private_tmp(made, work) if isinstance(made, Path) else made   # (the API before the fix)
        with tmp_ctx() as one, tmp_ctx() as two:
            self.assertNotEqual(one, two)
            shutil.rmtree(one)
            one.mkdir()  # someone else's folder now has that name
            write(one / "theirs", "keep")
        self.assertTrue((one / "theirs").is_file(), "removed a folder this run did not make")
        self.assertFalse(two.exists())

    # 6 MEDIUM: X360 reuse ignored a content change that kept size, modification time and inode.
    def test_R6_x360_reuse_follows_content(self):
        iso = self.home / "own.iso"
        write(iso, "fixture ISO A")
        calls = []
        def prepare(src, dst, pc, progress):
            calls.append(src.read_text())
            write(dst / "prepared/files/file", src.read_text())
            return types.SimpleNamespace(roots={"x360_root": dst / "prepared/files"}, features=["audio"], missing=[])
        module = types.SimpleNamespace(prepare=prepare)
        def run(check):
            args = types.SimpleNamespace(pc=str(self.pc), dest=str(self.dest), x360=str(iso), build=not check, check=check)
            with self.pipeline_stubs(), mock.patch.object(b.importlib, "import_module", return_value=module):
                return b.build(args, self.paths, self.events, FakeRunner(self.log, self.child_env, self.paths.work), self.source)
        run(True)
        st = iso.stat()
        with iso.open("r+b") as f:
            f.write(b"fixture ISO B")  # same size, same inode
        os.utime(iso, ns=(st.st_atime_ns, st.st_mtime_ns))
        self.assertEqual((iso.stat().st_size, iso.stat().st_mtime_ns, iso.stat().st_ino), (st.st_size, st.st_mtime_ns, st.st_ino))
        run(False)
        self.assertEqual(calls, ["fixture ISO A", "fixture ISO B"], "the old conversion was reused")
        roots = dict(l.split("=", 1) for l in (self.paths.data / "roots.txt").read_text().splitlines())
        self.assertEqual((Path(roots["x360_root"]) / "file").read_text(), "fixture ISO B")

    # 7 MEDIUM: FFmpeg's prefix is inside the cache path; a double quote there broke its generated install recipe.
    def test_R7_cache_with_shell_characters_moves_to_a_safe_folder(self):
        for name in ('ca"che', "ca che", "ca$che", "ca`che"):
            with self.subTest(name):
                paths = b.Paths(self.home / "w", env={"HOME": str(self.home), "NFSMW_SETUP_CACHE": str(self.home / name)})
                self.assertTrue(b.SHELL_SAFE.match(str(paths.cache)), paths.cache)
                self.assertEqual(getattr(paths, "cache_moved", None), b.clean_path(self.home / name))
                # the recipe line FFmpeg's Makefile runs for its prefix (`prefix=...` in config.mak, then unquoted)
                r = subprocess.run(["/bin/sh", "-n", "-c", f'install -d "{paths.cache}/builds/k/source/third_party/ffmpeg/lib"'])
                self.assertEqual(r.returncode, 0)
        plain = b.Paths(self.home / "w", env={"HOME": str(self.home), "NFSMW_SETUP_CACHE": str(self.home / "cache")})
        self.assertEqual((plain.cache, plain.cache_moved), (b.clean_path(self.home / "cache"), None))

    # ---- round 2 (pubsetupm4_rereview_report.md): each of these failed on 1c0bf62 ----
    def committed_crash(self):
        """A 4.x app over an existing profile, power lost right after the commit point; returns the old state."""
        prepared = self.fresh_prepared()
        self.old_install()
        self.plant_app(self.dest / self.APP, "4.0.0")
        write(self.dest / self.APP / "Contents/old", "existing app")
        old = self.snapshot()
        def crash(name):
            if name == "journal-committed":
                raise SystemExit(77)
        with mock.patch.object(ins, "_step", side_effect=crash), self.assertRaises(SystemExit):
            ins.transactional_install(*prepared, self.paths.data, self.dest, lambda p: None, running=lambda: None)
        self.assertTrue(ins.journal_path(self.paths.data).exists())
        return old

    # R1 HIGH: a marker file alone authorized a nonempty folder. Ownership is bound to the directory and to state.
    def test_R1b_copied_or_empty_marker_does_not_authorize_a_folder(self):
        mine = self.home / "mine"
        mine.mkdir()
        write(mine / "a", "x")
        ins.mark_owned(mine, self.paths.state)
        self.assertTrue(ins.owned(mine, self.paths.state))
        docs = self.home / "Documents"
        write(docs / "thesis.txt", "the player's own file")
        shutil.copy(mine / ins.OWNER_MARK, docs / ins.OWNER_MARK)   # copied marker
        self.assertFalse(ins.owned(docs, self.paths.state))
        (docs / ins.OWNER_MARK).write_bytes(b"")                    # empty marker
        self.assertFalse(ins.owned(docs, self.paths.state))
        self.assertFalse(ins.owned(mine, self.home / "elsewhere"), "state that does not record the folder")
        moved = self.home / "moved"
        shutil.copytree(mine, moved)                                # a copy of a marked folder is not marked
        self.assertFalse(ins.owned(moved, self.paths.state))
        for chosen in (docs, moved):
            with self.assertRaisesRegex(b.SetupError, "already holds other files"):
                b.claim(chosen, False, b.SETUP_WORK_NAMES, "work", self.paths.state)
        self.assertTrue((docs / "thesis.txt").is_file())
        # installs/: a generation with only a copied marker is never pruned
        installs = self.paths.data / "installs"
        victim = installs / ("a" * 32)
        write(victim / "precious", "x")
        shutil.copy(mine / ins.OWNER_MARK, victim / ins.OWNER_MARK)
        for _ in range(3):
            ins.transactional_install(*self.fresh_prepared(), self.paths.data, self.dest, lambda p: None, running=lambda: None)
            shutil.rmtree(self.home / "prepared")
        self.assertTrue((victim / "precious").is_file())

    # R4 HIGH: the old app's backup was deleted when the new app was only a directory with an Info.plist.
    def check_incomplete_new_app(self, damage):
        old = self.committed_crash()
        damage()
        self.assertEqual(ins.recover_interrupted(self.paths.data), "restored")
        self.assertEqual(self.snapshot(), old)

    def test_R4b_new_app_without_executable_keeps_the_previous_one(self):
        self.check_incomplete_new_app(lambda: (self.dest / self.APP / "Contents/MacOS/NFSMostWanted").unlink())

    def test_R4b_new_app_failing_codesign_keeps_the_previous_one(self):
        def damage():
            patch = mock.patch.object(ins, "codesign_ok", return_value=False)
            patch.start()
            self.addCleanup(patch.stop)
        self.check_incomplete_new_app(damage)

    # NEW HIGH: a missing new app restored the old app but deleted the old manifests' backups (new roots.txt/app.txt).
    def test_R4c_missing_new_app_restores_old_app_and_manifests_together(self):
        old = self.committed_crash()
        shutil.rmtree(self.dest / self.APP)
        self.assertEqual(ins.recover_interrupted(self.paths.data), "restored")
        self.assertEqual(self.snapshot(), old)
        self.assertEqual(sorted(p.name for p in self.dest.iterdir()), [self.APP])
        self.assertFalse(ins.journal_path(self.paths.data).exists())
        self.assertEqual([p.name for p in (self.paths.data / "installs").iterdir() if p.name[0] != "."], [])

    # R6 MEDIUM: the reuse key named the bytes digested before the conversion, not the ones converted.
    def test_R6b_source_changed_during_conversion_is_not_reusable(self):
        iso = self.home / "own.iso"
        write(iso, "fixture ISO A")
        st = iso.stat()
        def prepare(src, dst, pc, progress):
            write(dst / "prepared/files/file", src.read_text())   # converts B ...
            with src.open("r+b") as f:
                f.write(b"fixture ISO B")
            os.utime(src, ns=(st.st_atime_ns, st.st_mtime_ns))  # ... after the bytes changed, metadata unchanged
            return types.SimpleNamespace(roots={"x360_root": dst / "prepared/files"}, features=["audio"], missing=[])
        module = types.SimpleNamespace(prepare=prepare)
        def run(check):
            args = types.SimpleNamespace(pc=str(self.pc), dest=str(self.dest), x360=str(iso), build=not check, check=check)
            with self.pipeline_stubs(), mock.patch.object(b.importlib, "import_module", return_value=module):
                return b.build(args, self.paths, self.events, FakeRunner(self.log, self.child_env, self.paths.work), self.source)
        run(True)
        self.assertFalse((self.paths.work / (b.CHECK_X360 + ".json")).exists(), "a conversion of other bytes was recorded")
        write(iso, "fixture ISO A")
        os.utime(iso, ns=(st.st_atime_ns, st.st_mtime_ns))
        with self.assertRaisesRegex(b.SetupError, "changed while Setup was converting"):
            run(False)

    # Review 11-06: the extras page writes car-selection.json into the unmarked default work folder before the first
    # PC check marks it; the check then refused the folder. PASS with the name listed; without it: "already holds other files".
    def test_default_work_folder_accepts_the_car_selection(self):
        default = self.paths.default_work
        write(default / "car-selection.json", "[]")
        b.claim(default, True, b.SETUP_WORK_NAMES, "work", self.paths.state)
        self.assertTrue((default / "car-selection.json").is_file())

    # NEW MEDIUM: an unmarked default work folder with Setup's own pc_src.tmp was refused before journal recovery.
    def test_R8_recovery_runs_before_the_work_folder_is_judged(self):
        default = self.paths.default_work
        write(default / "pc_src.tmp", "{}")
        write(default / "check-x360.tmp", "{}")
        calls = []
        def recover(data, log=None):
            calls.append(data)
            return "restored" if len(calls) == 1 else None
        with mock.patch.object(b, "recover_interrupted", side_effect=recover):
            rc, out = self.run_main(default)
        self.assertEqual(rc, 0, out)
        self.assertGreaterEqual(len(calls), 1)
        self.assertIn("interrupted install", out)
        # another unmarked folder with unknown files is still refused, after recovery
        calls.clear()
        other = self.home / "Documents"
        write(other / "notes.txt", "x")
        with mock.patch.object(b, "recover_interrupted", side_effect=recover):
            rc, out = self.run_main(other)
        self.assertNotEqual(rc, 0)
        self.assertEqual(len(calls), 1)

    # NEW MEDIUM: the shell-safe branch used any existing tmp/ (a link led child writes out of work).
    def test_R9_existing_tmp_link_is_refused(self):
        work = self.home / "plain-work2"
        outside = self.home / "outside"
        outside.mkdir()
        work.mkdir()
        os.symlink(outside, work / "tmp")
        with self.assertRaisesRegex(b.SetupError, "link or not a folder"):
            with b.tool_tmp(work):
                self.fail("a linked tmp/ was used")
        self.assertEqual(list(outside.iterdir()), [])
        (work / "tmp").unlink()
        with b.tool_tmp(work) as tmp:
            self.assertEqual(tmp, work / "tmp")

    # NEW LOW: an existing unmarked installs/ (from an earlier Setup) disabled pruning for good.
    def test_R10_existing_unmarked_installs_prunes_only_generations_setup_made(self):
        installs = self.paths.data / "installs"
        legacy = [installs / ("a" * 32), installs / ("b" * 32)]
        for g in legacy:
            write(g / "pc/speed.exe", "earlier Setup's generation")
        made = []
        for i in range(4):
            app, data, roots = self.fresh_prepared()
            ins.transactional_install(app, data, roots, self.paths.data, self.dest, lambda p: None, running=lambda: None)
            shutil.rmtree(self.home / "prepared")
            made.append(Path((self.paths.data / "roots.txt").read_text().split("=", 1)[1].strip()).relative_to(installs).parts[0])
        live = sorted(p.name for p in installs.iterdir() if p.name[0] != ".")
        self.assertEqual(live, sorted([g.name for g in legacy] + made[-2:]))


    # ---- round 3 (pubsetupm4_rereview3_report.md) ----
    # HIGH: rollback began while the journal still said "committed"; an interrupted rollback was then "finished"
    # (new app + old app.txt) on the retry and the backups were deleted.
    def test_R11_interrupted_rollback_is_finished_not_reverified(self):
        old = self.committed_crash()
        def interrupt(name):
            if name == "rollback-restore":
                raise SystemExit(78)  # power lost during the first restore
        with mock.patch.object(ins, "codesign_ok", return_value=False), \
             mock.patch.object(ins, "_step", side_effect=interrupt), self.assertRaises(SystemExit):
            ins.recover_interrupted(self.paths.data)
        self.assertTrue(ins.journal_path(self.paths.data).exists())
        # the retry's verification would succeed: it must not matter, the rollback is finished
        self.assertEqual(ins.recover_interrupted(self.paths.data), "restored")
        self.assertEqual(self.snapshot(), old)
        self.assertEqual(sorted(p.name for p in self.dest.iterdir()), [self.APP])

    # MEDIUM (a): a cleanup failure after on_commit/after_commit reported success rolled the install back.
    def test_R12_cleanup_failure_after_success_never_rolls_back(self):
        self.old_install()
        old = self.snapshot()
        calls = []
        patch = mock.patch.object(ins, "codesign_ok", return_value=False)   # the cleanup's signature check fails
        patch.start()
        self.addCleanup(patch.stop)
        installed = ins.transactional_install(*self.fresh_prepared(), self.paths.data, self.dest, lambda p: None,
                                              running=lambda: None, on_commit=lambda p: calls.append("commit"),
                                              after_commit=lambda: calls.append("after"))
        self.assertEqual(calls, ["commit", "after"])
        self.assertEqual(installed, self.dest / self.APP)
        self.assertNotEqual(self.snapshot(), old, "the install that was reported as done was rolled back")
        self.assertIn("pc_root=" + str(self.paths.data / "installs"), (self.paths.data / "roots.txt").read_text())

    # MEDIUM R6: a change A -> B -> A with mtime restored kept every endpoint hash and size/mtime/inode.
    def test_R13_changed_and_restored_source_is_detected_by_ctime(self):
        iso = self.home / "own.iso"
        write(iso, "fixture ISO A")
        st = iso.stat()
        def prepare(src, dst, pc, progress):
            write(dst / "prepared/files/file", "converted")
            with src.open("r+b") as f:
                f.write(b"fixture ISO B")
            with src.open("r+b") as f:
                f.write(b"fixture ISO A")
            os.utime(src, ns=(st.st_atime_ns, st.st_mtime_ns))
            return types.SimpleNamespace(roots={"x360_root": dst / "prepared/files"}, features=["audio"], missing=[])
        module = types.SimpleNamespace(prepare=prepare)
        def run(check):
            args = types.SimpleNamespace(pc=str(self.pc), dest=str(self.dest), x360=str(iso), build=not check, check=check)
            with self.pipeline_stubs(), mock.patch.object(b.importlib, "import_module", return_value=module):
                return b.build(args, self.paths, self.events, FakeRunner(self.log, self.child_env, self.paths.work), self.source)
        run(True)
        self.assertFalse((self.paths.work / (b.CHECK_X360 + ".json")).exists())
        with self.assertRaisesRegex(b.SetupError, "changed while Setup was converting"):
            run(False)

    # MEDIUM: NFSMW_SETUP_STATE (the ownership registry) pointing into the PC game was not validated.
    def test_R14_state_registry_inside_an_input_is_refused(self):
        for inside in (self.pc, self.pc / "GLOBAL"):
            paths = b.Paths(self.home / "w2", env={"HOME": str(self.home), "NFSMW_SETUP_STATE": str(inside)})
            with self.assertRaisesRegex(b.SetupError, "protected folder"):
                b.check_locations(paths, b.clean_path(self.pc), None, b.clean_path(self.dest), self.source)
        self.assertFalse((self.pc / ins.OWN_DIR).exists())

    # LOW: ownership was forgotten before the deletion; an interruption left a generation nobody could prune.
    def test_R15_interrupted_prune_is_finished_by_the_next_run(self):
        installs = self.paths.data / "installs"
        gens = []
        def install():
            ins.transactional_install(*self.fresh_prepared(), self.paths.data, self.dest, lambda p: None, running=lambda: None)
            shutil.rmtree(self.home / "prepared")
            gens.append(Path((self.paths.data / "roots.txt").read_text().split("=", 1)[1].strip()).relative_to(installs).parts[0])
        install()
        install()
        def crash(name):
            if name == "prune-contents":
                raise SystemExit(79)
        with mock.patch.object(ins, "_step", side_effect=crash), self.assertRaises(SystemExit):
            install()
        half = installs / gens[0]
        self.assertTrue(half.is_dir() and ins.owned(half, self.paths.data), "authority was dropped before deleting")
        third = Path((self.paths.data / "roots.txt").read_text().split("=", 1)[1].strip()).relative_to(installs).parts[0]
        install()
        self.assertEqual(sorted(p.name for p in installs.iterdir() if p.name[0] != "."), sorted([third, gens[-1]]))


class ExtrasSafetyTests(unittest.TestCase):
    """Protected-path controls: original has no Carbon role or volume-plan API."""
    def setUp(self):
        (ROOT / '.metal-cache').mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='extras-safety-', dir=ROOT / '.metal-cache')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'payload'
        self.pc = self.root / 'mw'
        self.carbon = self.root / 'carbon'
        self.carbon.mkdir()
        (self.carbon / 'arbitrary.EXE').write_bytes(b'synthetic executable')
        self.dest = self.root / 'apps'
        self.paths = b.Paths(self.root / 'work', env={'HOME': str(self.root), 'NFSMW_SETUP_HOME': str(self.root / 'setup')})

    def test_required_build_protects_optional_carbon_inputs(self):
        b.check_locations(self.paths, self.pc, None, self.dest, self.source, carbon=self.carbon / 'arbitrary.EXE')
        for attr in ('work', 'cache', 'data', 'logs', 'state'):
            original = getattr(self.paths, attr)
            setattr(self.paths, attr, self.carbon / 'must-not-write')
            try:
                with self.assertRaises(b.SetupError, msg=attr):
                    b.check_locations(self.paths, self.pc, None, self.dest, self.source, carbon=self.carbon / 'arbitrary.EXE')
            finally:
                setattr(self.paths, attr, original)
        self.assertEqual(sorted(p.name for p in self.carbon.iterdir()), ['arbitrary.EXE'])
        # Deliberately omit the Carbon role: the same unsafe choice is accepted.
        self.paths.work = self.carbon / 'must-not-write'
        b.check_locations(self.paths, self.pc, None, self.dest, self.source)

    def test_standalone_refuses_work_cache_catalog_and_save_overlaps_before_writes(self):
        with mock.patch.object(b, 'ROOT', self.source):
            b.car_locations(self.paths, [self.carbon, self.pc])
            for attr, target in (('work', self.carbon / 'work'), ('cache', self.carbon),
                                 ('data', self.carbon), ('work', self.paths.legacy / 'scratch'),
                                 ('work', self.paths.data / b.SAVE_DIR / 'scratch'),
                                 ('work', self.source / 'work'), ('work', self.paths.cache / 'cars/work')):
                original = getattr(self.paths, attr)
                setattr(self.paths, attr, target)
                try:
                    with self.assertRaises(b.SetupError, msg=(attr, target)):
                        b.car_locations(self.paths, [self.carbon, self.pc])
                finally:
                    setattr(self.paths, attr, original)
        self.assertFalse(self.paths.work.exists())
        self.assertFalse(self.paths.data.exists())

    def test_standalone_rejects_symlink_and_traversal_inputs(self):
        link = self.root / 'alias'
        link.symlink_to(self.carbon, target_is_directory=True)
        for path in (link, link / 'arbitrary.EXE', self.carbon / '../carbon', self.carbon / 'arbitrary.EXE/../x'):
            with self.assertRaises((b.car_api().ImportError, OSError, b.car_catalog.CatalogError)):
                b.carbon_input(path)

    def test_standalone_cli_protects_paths_before_opening_log(self):
        base = self.root / 'setup-home'
        for options in ({'NFSMW_SETUP_DATA': str(self.carbon)},
                        {'NFSMW_SETUP_CACHE': str(self.carbon)},
                        {'NFSMW_SETUP_CACHE': str(self.root / 'alias')},
                        {'NFSMW_SETUP_DATA': str(base / 'legacy')}):
            alias = self.root / 'alias'
            if not alias.exists():
                alias.symlink_to(self.carbon, target_is_directory=True)
            output = io.StringIO()
            env = dict(os.environ, NFSMW_SETUP_HOME=str(base), **options)
            with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(b, 'ROOT', self.source), redirect_stdout(output):
                rc = b.main(['--cars-check', '--carbon', str(self.carbon / 'arbitrary.EXE'), '--events'])
            self.assertNotEqual(rc, 0, output.getvalue())
            self.assertFalse((base / 'state/car-work').exists())
            self.assertEqual(sorted(p.name for p in self.carbon.iterdir()), ['arbitrary.EXE'])

    def test_optional_work_and_cache_bootstrap_preserve_required_claims(self):
        work = self.paths.default_work
        (work / 'extras').mkdir(parents=True)
        (self.paths.cache / 'cars').mkdir(parents=True)
        for folder, names, label in ((work, b.SETUP_WORK_NAMES, 'work'),
                                     (self.paths.cache, b.SETUP_CACHE_NAMES, 'cache')):
            with self.assertRaises(b.SetupError):
                b.claim(folder, True, names - {'extras', 'cars'}, label, self.paths.state)
            b.claim(folder, True, names, label, self.paths.state)
            self.assertTrue(ins.owned(folder, self.paths.state))
        self.assertTrue((work / 'extras').is_dir())
        self.assertTrue((self.paths.cache / 'cars').is_dir())

    def test_management_protects_installed_asset_paths_without_requiring_media(self):
        self.paths.data.mkdir(parents=True)
        assets = self.root / 'unplugged-assets'
        (self.paths.data / 'roots.txt').write_text('pc_root=' + str(self.pc) + '\nassets_root=' + str(assets) + '\n')
        (self.paths.data / 'app.txt').write_text('game_dir=' + str(self.carbon) + '\n')
        with mock.patch.object(b, 'ROOT', self.source):
            b.car_locations(self.paths, [])
            for target in (self.pc / 'logs', assets / 'logs', self.carbon / 'logs', self.paths.data / 'temporary'):
                original = self.paths.work
                self.paths.work = target
                try:
                    with self.assertRaises(b.SetupError, msg=target):
                        b.car_locations(self.paths, [])
                finally:
                    self.paths.work = original
        self.assertFalse(self.pc.exists())
        self.assertFalse(assets.exists())
        self.assertEqual(sorted(p.name for p in self.carbon.iterdir()), ['arbitrary.EXE'])

    def test_log_parent_swap_does_not_write_into_source(self):
        base = self.root / 'setup-home'
        original = b.car_locations
        def swap(paths, inputs):
            original(paths, inputs)
            paths.work.parent.mkdir(parents=True)
            paths.work.symlink_to(self.carbon, target_is_directory=True)
        output = io.StringIO()
        with mock.patch.dict(os.environ, {'NFSMW_SETUP_HOME': str(base)}), \
                mock.patch.object(b, 'ROOT', self.source), mock.patch.object(b, 'car_locations', swap), redirect_stdout(output):
            rc = b.main(['--cars-check', '--carbon', str(self.carbon), '--events'])
        self.assertNotEqual(rc, 0)
        self.assertEqual(sorted(p.name for p in self.carbon.iterdir()), ['arbitrary.EXE'])

    def test_ft_volume_plans_keep_unknown_sizes_and_refuse_each_full_volume(self):
        unknown = dict(volumes=[dict(required_bytes=None, available_bytes=0)])
        b.check_space(self.paths, car_plan=unknown)
        self.assertIsNone(unknown['volumes'][0]['required_bytes'])
        for index in range(2):
            volumes = [dict(required_bytes=100, available_bytes=1000), dict(required_bytes=100, available_bytes=1000)]
            volumes[index]['available_bytes'] = 99
            with self.assertRaises(b.SetupError):
                b.check_space(self.paths, car_plan={'volumes': volumes})
        b.check_space(self.paths, car_plan={'volumes': [dict(required_bytes=100, available_bytes=100)]})


class X360ExtrasTests(Fixture):
    def xbox_run(self, chosen=None, *, check=False, stage=False):
        from setup_kit import x360_extras
        xbox = self.home / "own Xbox.iso"
        if not xbox.exists():
            write(xbox, b"synthetic Xbox disc")
        args = self.args(check=check)
        args.x360 = str(xbox)
        args.x360_extras = chosen
        if stage:
            args.build = False
            args.stage_only = True
            args.stage = str(self.home / "prepared-update")
            args.dest = str(self.dest / (b.PUBLIC_NAME + ".app"))
        def prepare(source, out, pc, events, env, record=None, extras=None):
            selection = x360_extras.selection(extras)
            write(out / "files/shared.bin", b"shared")
            (out / "assets").mkdir(exist_ok=True)
            for feature in selection:
                write(out / "assets" / x360_extras.FOLDERS[feature] / "asset", feature.encode())
            if record is not None:
                record.update(features=True, missing=[n for n in x360_extras.ALL if n not in selection])
            return dict(x360_root=out / "files", assets_root=out / "assets")
        with self.pipeline_stubs(), mock.patch.object(b, "prepare_x360", side_effect=prepare):
            return b.build(args, self.paths, self.events, FakeRunner(self.log, self.child_env, self.paths.work), self.source)

    def installed_extras(self):
        roots = dict(line.split("=", 1) for line in (self.paths.data / "roots.txt").read_text().splitlines())
        return {p.parent.name: p.read_bytes() for p in Path(roots["assets_root"]).rglob("asset")}

    def test_x360_choices_repair_update_and_retick(self):
        # A full check must never be reused for a build with one extra unticked.
        self.xbox_run(check=True)
        self.xbox_run("textures,ui")
        expected = {"hd_textures_mips_alpha": b"textures", "hd_textures_ui": b"ui"}
        self.assertEqual(self.installed_extras(), expected)
        self.assertEqual(b.staging.load_choices(self.paths.state)["x360_extras"], ["textures", "ui"])
        self.xbox_run()  # repair without an explicit choice
        self.assertEqual(self.installed_extras(), expected)
        ready = self.xbox_run(stage=True)  # prepared update also preserves the selection
        descriptor = json.loads(ready.read_text())
        self.assertEqual(descriptor["provenance"]["x360_extras"], ["textures", "ui"])
        self.assertFalse(any(p.name == "nis_arrest" for p in ready.parent.rglob("*")))
        self.xbox_run("arrest,textures,ui")
        self.assertEqual(self.installed_extras(), dict(expected, nis_arrest=b"arrest"))
        self.xbox_run()  # reticking is remembered too
        self.assertEqual(self.installed_extras(), dict(expected, nis_arrest=b"arrest"))
        for chosen, absent in (("arrest,ui", "hd_textures_mips_alpha"), ("arrest,textures", "hd_textures_ui"), ("", None)):
            with self.subTest(chosen=chosen):
                self.xbox_run(chosen)
                if absent:
                    self.assertNotIn(absent, self.installed_extras())
                    self.assertEqual(len(self.installed_extras()), 2)
                else:
                    self.assertEqual(self.installed_extras(), {})
                before = self.installed_extras()
                self.xbox_run()
                self.assertEqual(self.installed_extras(), before)

    def test_x360_selection_cache_keys_and_validation(self):
        from setup_kit import x360_extras
        key_args = ({}, {}, {}, "pc", "xbox")
        self.assertEqual(b.cache_key(*key_args), b.cache_key(*key_args, extras=list(x360_extras.ALL)))
        self.assertEqual(len({b.cache_key(*key_args, extras=[n for i, n in enumerate(x360_extras.ALL) if mask & (1 << i)])
                              for mask in range(8)}), 8)
        self.assertEqual(len({b.preparation_key("xbox", "pc", {}, {}, extras=[n for i, n in enumerate(x360_extras.ALL) if mask & (1 << i)])
                              for mask in range(8)}), 8)
        for invalid in ("unknown", "arrest,arrest", True, ["ui", 1]):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                x360_extras.selection(invalid)
        b.staging.save_choices(self.paths.state, self.pc, None, self.dest, extra_choices=[])
        self.assertEqual(b.staging.load_choices(self.paths.state)["x360_extras"], [])
        saved = json.loads((self.paths.state / "source-choices.json").read_text())
        for invalid in (False, "arrest", ["unknown"], ["ui", "ui"]):
            write(self.paths.state / "source-choices.json", json.dumps(dict(saved, x360_extras=invalid)))
            with self.assertRaises(ins.InstallError):
                b.staging.load_choices(self.paths.state)

    def test_x360_omitted_space_is_not_reserved(self):
        from setup_kit import x360_extras
        base = 2 * sum(p.stat().st_size for p in self.pc.rglob("*") if p.is_file()) + 8 * 1024**3 + 768 * 1024**2
        with mock.patch.object(b.shutil, "disk_usage", return_value=types.SimpleNamespace(free=base)):
            b.check_space(self.paths, self.pc, self.dest, x360_bytes=x360_extras.required_bytes([]))
            for feature in x360_extras.ALL:
                with self.subTest(feature=feature), self.assertRaises(b.SetupError):
                    b.check_space(self.paths, self.pc, self.dest, x360_bytes=x360_extras.required_bytes([feature]))


@contextmanager
def contextlib_nullcontext(value=None):
    yield value


class CarbonRunner(FakeRunner):
    """The Carbon stages' four commands, its tables and its bundle; Most Wanted's commands as FakeRunner."""
    def run(self, argv, cwd=None, capture=False):
        text = " ".join(map(str, argv))
        if "gen_tables.py" in text and "--no-pc" in text:
            self.commands.append(list(map(str, argv)))
            spec = b.importlib.util.spec_from_file_location("fixture_tables", Path(cwd) / "tools/gen_tables.py")
            generator = b.importlib.util.module_from_spec(spec)
            spec.loader.exec_module(generator)
            for table in generator.NOPC_TABLES:
                write(Path(cwd) / f"runtime/gen/{table}", b"table made without a game folder")
            return ""
        if "runtime/bundle.sh" in text:
            argv = list(map(str, argv))
            self.commands.append(argv)
            name, exe = argv[argv.index("--name") + 1], argv[argv.index("--exec-name") + 1]
            app = Path(argv[2]) / f"{name}.app"
            write(app / f"Contents/MacOS/{exe}", b"signed Carbon fixture", True)
            write(app / "Contents/Info.plist", plistlib.dumps(dict(CFBundleExecutable=exe,
                  CFBundleIdentifier=argv[argv.index("--bundle-id") + 1], CFBundleShortVersionString=argv[argv.index("--version") + 1])))
            return ""
        if "build_private_runtime.py" in text:
            self.commands.append(list(map(str, argv)))
            write(Path(cwd) / "recomp/out/carbon/boot_carbon", b"Carbon program fixture", True)
            return ""
        return super().run(argv, cwd, capture)


class CarbonRepackTests(Fixture):
    """Repack proof: real media 1,417 files / 5,474,475,805 bytes; CRC/MD5 checked.

    Full import: 109.889 s, peak RSS 391,806,976 bytes (native arm64).
    Small fixture: good inventory 2 files / 18 bytes, flipped and truncated
    archives rejected 2/2. Removed ISO dispatch rejected 1/1. Real media is an
    opt-in offline check: --carbon-repack-iso PATH (exit 3 when absent).
    Native bridge/build inputs: tools/setup_kit/lzma_sdk/LzmaDec.c,
    LzmaDec.h, 7zTypes.h, Precomp.h, Compiler.h, bridge.c, NOTICE.txt.
    """
    @staticmethod
    def arc_fixture():
        from setup_kit import carbon_repack as r
        import lzma
        def num(value):
            for n in range(1, 9):
                if value < 1 << (7*n):
                    return ((value << n) | ((1 << (n-1))-1)).to_bytes(n, 'little')
            return b'\xff' + value.to_bytes(8, 'little')
        def text(value):
            return value.encode() + b'\0'
        def pack(data):
            return lzma.compress(data, format=lzma.FORMAT_RAW, filters=[dict(
                id=lzma.FILTER_LZMA1, dict_size=r.MIB, lc=3, lp=0, pb=2)])
        def desc(typ, original, packed, crc):
            data = (b'ArC\x01' + num(typ) + text('lzma:1mb:normal:bt4:32') + num(original)
                    + num(packed) + struct.pack('<I', crc))
            return data + struct.pack('<I', zlib.crc32(data))
        contents = [b'MZ synthetic', b'track!']
        sizes = [len(data) for data in contents]
        directory = (num(1) + num(2) + text(r.CHAIN) + num(32) + num(32)
                     + num(2) + text('') + text('TRACKS')
                     + text('NFSC.exe') + text('fixture.bin') + num(0) + num(1)
                     + b''.join(num(n) for n in sizes) + bytes(10)
                     + b''.join(struct.pack('<I', zlib.crc32(data)) for data in contents))
        # A padding prefix keeps this small fixture large enough for footer search.
        prefix = b'ArC\x01HEAD' + bytes(4088)
        payload = bytes(32)
        compressed = pack(directory)
        directory_start = len(prefix) + len(payload)
        # offset is relative to directory_start, so its data begins at 4096.
        directory_descriptor = desc(3, len(directory), len(compressed), zlib.crc32(directory))
        body = prefix + payload + compressed + directory_descriptor
        footer_start = len(body)
        footer = (num(2) + num(1) + text('storing') + num(footer_start) + num(8) + num(8)
                  + struct.pack('<I', zlib.crc32(prefix[:8]))
                  + num(3) + text('lzma:1mb:normal:bt4:32') + num(footer_start-directory_start)
                  + num(len(directory)) + num(len(compressed)) + struct.pack('<I', zlib.crc32(directory)))
        compressed_footer = pack(footer)
        return body + compressed_footer + desc(4, len(footer), len(compressed_footer), zlib.crc32(footer))

    @unittest.skipUnless((ROOT / "tools/setup_kit/carbon_repack.py").is_file(), "public payload carries no repack reader")
    def test_repack_inventory_corruption_and_dispatch_controls(self):
        from setup_kit import carbon_repack as r, carbon_source
        data = self.arc_fixture()
        def inventory(blob):
            return r.inventory(r.Extent(io.BytesIO(blob), [(0, len(blob))], len(blob)))
        files, start, packed = inventory(data)
        self.assertEqual((len(files), sum(i.file_size for i, _ in files)), (2, 18))
        self.assertEqual((start, packed), (4096, 32))
        flipped = bytearray(data)
        flipped[-1] ^= 1
        for broken in (flipped, data[:-20]):
            with self.assertRaises((ValueError, lzma.LZMAError)):
                inventory(broken)
        iso = self.home / 'repack.iso'
        iso.write_bytes(b'synthetic media selection')
        before = b.sha_file(iso)
        with mock.patch.object(r, 'prepare', return_value=self.pc) as decode:
            self.assertEqual(carbon_source.prepare(iso, self.paths.work, self.events), self.pc)
            decode.assert_called_once_with(iso, self.paths.work, self.events)
        import inspect
        original = inspect.getsource(carbon_source.prepare)
        broken = original.replace('        return carbon_repack.prepare(source, work, events)',
                                  '        raise b.SetupError("Carbon PC disc installation is not supported yet.", FIX)')
        self.assertNotEqual(original, broken)
        namespace = {}
        exec(compile(broken, str(ROOT / 'tools/setup_kit/carbon_source.py'), 'exec'), carbon_source.__dict__, namespace)
        with self.assertRaisesRegex(b.SetupError, 'not supported yet'):
            namespace['prepare'](iso, self.paths.work, self.events)
        self.assertEqual(b.sha_file(iso), before)
        print('Repack fixture PASS: 2 files / 18 bytes; corrupt controls rejected 2/2; removed dispatch rejected 1/1')

    @unittest.skipUnless((ROOT / "tools/setup_kit/carbon_repack.py").is_file(), "public payload carries no repack reader")
    def test_repack_native_lzma_delta_srep_and_damage(self):
        from setup_kit import carbon_repack as r
        # Zero literals use the same LZMA context with lc=3 and lc=8. Encode with
        # the stdlib, decode with the bundled SDK's lc8 properties (liblzma refuses lc8).
        zeros = bytes(16384)
        packed = lzma.compress(zeros, format=lzma.FORMAT_RAW, filters=[dict(
            id=lzma.FILTER_LZMA1, dict_size=158*r.MIB, lc=3, lp=0, pb=2)])
        view = r.Extent(io.BytesIO(packed), [(0, len(packed))], len(packed))
        self.assertEqual(b''.join(r.lzma_blocks(view, 0, len(packed), self.paths.work)), zeros)
        with self.assertRaises(ValueError):
            b''.join(r.lzma_blocks(view, 0, len(packed)-1, self.paths.work))
        # Two adjacent little-endian bytes have independent cumulative carries.
        # A type sentinel at bit 2 means width=2, both columns delta-coded.
        delta = struct.pack('<IIIII', 6, 4, 0, 4, 3) + bytes([1, 0, 1, 0, 254, 0])
        self.assertEqual(b''.join(r.delta_blocks([delta])), bytes([1, 0, 2, 0, 0, 1]))
        for broken in (delta[:-1], struct.pack('<II', r.BLOCK+1, 0)):
            with self.assertRaises(ValueError):
                b''.join(r.delta_blocks([broken]))
        plain = b'abcdabcd'
        head = struct.pack('<4I', 0x26351817, 0x50455253, 3, 256)
        # Within-block match length 4 is serialized modulo 2**32 relative to 256.
        table = struct.pack('<4I', 0, 4, 0, (4-256) & 0xffffffff)
        block = struct.pack('<III', 4, 8, len(table)) + hashlib.md5(plain).digest() + table + b'abcd'
        self.assertEqual(b''.join(r.srep_blocks([head+block], self.paths.work, 8)), plain)
        for broken in (head+block[:-1], head+block[:-1]+b'e'):
            with self.assertRaises(ValueError):
                b''.join(r.srep_blocks([broken], self.paths.work, 8))
        print('Native lc8 PASS: 16384 bytes; truncated LZMA rejected 1/1; Delta and SREP damage rejected 4/4')

    @unittest.skipUnless((ROOT / "tools/setup_kit/carbon_repack.py").is_file(), "public payload carries no repack reader")
    def test_repack_rejects_bad_media_without_partial_tree(self):
        from setup_kit import carbon_source
        iso = self.home / 'broken.iso'
        iso.write_bytes(bytes(257*2048))
        before = b.sha_file(iso)
        with self.assertRaisesRegex(b.SetupError, 'UDF'):
            carbon_source.prepare(iso, self.paths.work, self.events)
        self.assertFalse((self.paths.work / 'carbon_src').exists())
        self.assertFalse((self.paths.work / 'carbon_src.json').exists())
        self.assertEqual(b.sha_file(iso), before)

    def test_real_repack_iso(self):
        from setup_kit import carbon_source
        import resource
        import time
        if not os.environ.get('CARBON_REPACK_ISO'):
            self.skipTest('use --carbon-repack-iso PATH for the real-media check')
        iso = Path(os.environ['CARBON_REPACK_ISO'])
        if not iso.is_file():
            self.skipTest('real Carbon repack ISO absent')
        before = (b.sha_file(iso), iso.stat().st_size, iso.stat().st_mtime_ns)
        start = time.monotonic()
        root = carbon_source.prepare(iso, self.paths.work, self.events)
        elapsed = time.monotonic()-start
        files = sorted(p for p in root.rglob('*') if p.is_file())
        self.assertEqual((len(files), sum(p.stat().st_size for p in files)), (1417, 5474475805))
        self.assertEqual(b.sha_file(root / 'NFSC.exe'), b.CARBON_EXE_SHA256)
        manifest = hashlib.sha256()
        for path in files:
            manifest.update(path.relative_to(root).as_posix().encode() + b'\0')
            manifest.update(bytes.fromhex(b.sha_file(path)))
        # SHA-256 of sorted relative path + NUL + binary file SHA-256.
        # Independently CRC/MD5-verified decoded tree, including every support file.
        self.assertEqual(manifest.hexdigest(),
                         '20b88b8c49fc9a070174b1193996aab0e14212575e23d5e54a7b68a9617b4280')
        print('REPACK SHA256 MANIFEST', manifest.hexdigest())
        self.assertEqual((b.sha_file(iso), iso.stat().st_size, iso.stat().st_mtime_ns), before)
        # Remove the actual dispatch and run exactly the same selection.
        import inspect
        namespace = {}
        old = inspect.getsource(carbon_source.prepare)
        broken = old.replace('        return carbon_repack.prepare(source, work, events)',
                             '        raise b.SetupError("Carbon PC disc installation is not supported yet.", FIX)')
        self.assertNotEqual(old, broken)
        exec(compile(broken, str(ROOT / 'tools/setup_kit/carbon_source.py'), 'exec'), carbon_source.__dict__, namespace)
        with self.assertRaisesRegex(b.SetupError, 'not supported yet'):
            namespace['prepare'](iso, self.paths.work, self.events)
        print('REAL REPACK PASS: 1417 files / 5474475805 bytes; dispatch control rejected 1/1; '
              'elapsed=%.3fs peak_rss=%d bytes' % (elapsed, resource.getrusage(resource.RUSAGE_SELF).ru_maxrss))


class CarbonBuildTests(Fixture):
    def test_moved_carbon_source_update_and_original_control(self):
        installed, _ = self.carbon_build()
        self.carbon.rename(self.home / 'Moved Carbon')
        setup_kit.__path__ = self.package_path
        real = b.build
        def run(target):
            with mock.patch.object(b, 'build', lambda args, paths, events, runner:
                                   real(args, paths, events, CarbonRunner(self.log, self.child_env, paths.work), self.source)), \
                 self.pipeline_stubs(), mock.patch.object(b, 'assert_not_running'), redirect_stdout(self.stream):
                return b.main(['--stage-only', '--game', 'carbon', '--stage', str(target),
                               '--work', str(self.paths.work), '--events'])
        with mock.patch.object(b.staging, 'resolve_source', side_effect=lambda state, path, **kw: Path(path)):
            self.assertEqual(run(self.home / 'broken-carbon'), 1)
        self.assertEqual(run(self.home / 'carbon-update'), 0, self.stream.getvalue()[-1000:])
        self.assertTrue((self.home / 'carbon-update/ready.json').is_file())
        self.assertTrue(Path(installed).is_dir())

    """--game carbon: the shared stages without Most Wanted, then Carbon's, its own app and profile <data>/Carbon.

    Proof: 3/3 pass; the same tests on main ef8b58ae's backend/install fail 3/3 (no --game option: main() exits 2
    through argparse; build() makes and installs NFS Most Wanted Native.app from the Most Wanted program).
    Carbon only (no Most Wanted folder): commands reading the Most Wanted copy 1 -> 0; on main 86d96004's backend
    test_carbon_build_without_most_wanted_folder fails (main() refuses: "--check and --build require --pc").
    Most Wanted's golden stream, bundle arguments and install tests in this file are unchanged and pass."""
    def setUp(self):
        super().setUp()
        self.carbon = self.home / "Carbon game"
        write(self.carbon / "NFSC.exe", b"MZ Carbon fixture executable")
        write(self.carbon / "TRACKS/fixture.bin", b"Carbon track")
        self.pin = mock.patch.object(b, "CARBON_EXE_SHA256", b.sha_file(self.carbon / "NFSC.exe"), create=True)
        self.pin.start()

    def tearDown(self):
        self.pin.stop()
        super().tearDown()

    def carbon_build(self):
        runner = CarbonRunner(self.log, self.child_env, self.paths.work)
        args = types.SimpleNamespace(pc=None, dest=str(self.dest), x360=None, build=True, check=False,
                                     game="carbon", carbon=str(self.carbon))
        with self.pipeline_stubs(), mock.patch.object(b, "assert_not_running"):
            return b.build(args, self.paths, self.events, runner, self.source), runner

    def carbon_archive(self, suffix=".zip", entries=None):
        entries = entries if entries is not None else [
            ("Carbon/NFSC.exe", (self.carbon / "NFSC.exe").read_bytes()),
            ("Carbon/TRACKS/fixture.bin", b"Carbon track")]
        return X360ArchiveTests.archive(self, suffix, entries, "pc-carbon")

    def test_carbon_archives_install_remember_original_and_control(self):
        import inspect
        original = self.carbon
        expected = b.digest_tree(original)
        entries = [("Carbon/NFSC.exe", (original / "NFSC.exe").read_bytes()),
                   ("Carbon/TRACKS/fixture.bin", b"Carbon track")]
        for suffix in (".zip", ".7z"):
            with self.subTest(suffix=suffix):
                archive = self.carbon_archive(suffix, entries)
                before = b.sha_file(archive)
                self.carbon = archive
                self.dest = self.home / ("Destination " + suffix)
                installed, _ = self.carbon_build()
                self.assertEqual(Path(installed), self.dest / "NFS Carbon Native.app")
                roots = dict(line.split("=", 1) for line in (self.paths.data / "Carbon/roots.txt").read_text().splitlines())
                self.assertEqual(set(roots), {"pc_root"})
                self.assertEqual(b.digest_tree(Path(roots["pc_root"])), expected)
                choices = b.staging.load_choices(b.carbon_state(self.paths), required=True)
                self.assertEqual(choices["pc"], str(archive))
                self.assertEqual(Path(choices["destination"]), Path(installed))
                self.assertFalse((self.paths.data / "roots.txt").exists())
                self.assertEqual(b.sha_file(archive), before)
                self.assertEqual(b.digest_tree(original), expected)
                self.assertFalse((self.paths.work / "carbon_src").exists())
                # Execute the production build with only archive dispatch removed.
                namespace = {}
                source = inspect.getsource(b.build).replace("carbon = carbon_source.prepare(carbon, paths.work, events, pc_update, pc_exe, pc_discs)", "validate_carbon(carbon)")
                self.assertNotEqual(source, inspect.getsource(b.build))
                exec(compile(source, str(ROOT / "tools/setup_kit/backend.py"), "exec"), b.__dict__, namespace)
                setup_kit.__path__ = self.package_path
                with mock.patch.object(b, "build", namespace["build"]), self.assertRaisesRegex(b.SetupError, "folder was not found"):
                    self.carbon_build()
        self.carbon = original

    def test_carbon_executable_input(self):
        original = self.carbon
        before = b.digest_tree(original)
        self.carbon = original / "NFSC.exe"
        installed, _ = self.carbon_build()
        self.assertTrue(Path(installed).is_dir())
        self.assertEqual(b.digest_tree(original), before)
        self.assertEqual(b.staging.load_choices(b.carbon_state(self.paths), required=True)["pc"], str(self.carbon))

    def test_carbon_archive_rejects_wrong_game_damage_and_links(self):
        from setup_kit import carbon_source
        for entries in ([('speed.exe', b'MW'), ('TRACKS/x', b'x')],
                        [('NFSC.exe', b'wrong version'), ('TRACKS/x', b'x')],
                        [('NFSC.exe', (self.carbon / 'NFSC.exe').read_bytes())],
                        [('NFSC.exe', None), ('TRACKS/x', b'x')],
                        [('../escape', b'no')],
                        [('a/NFSC.exe', b'a'), ('a/TRACKS/x', b'x'),
                         ('b/NFSC.exe', b'b'), ('b/TRACKS/x', b'x')]):
            for suffix in ('.zip', '.7z'):
                with self.subTest(suffix=suffix, entries=entries):
                    archive = self.carbon_archive(suffix, entries)
                    before = b.sha_file(archive)
                    with self.assertRaises(b.SetupError):
                        carbon_source.prepare(archive, self.paths.work, self.events)
                    self.assertEqual(b.sha_file(archive), before)
                    self.assertFalse((self.paths.work / 'carbon_src').exists())
                    self.assertFalse((self.paths.work / 'carbon_src.json').exists())
        self.assertFalse((self.home / 'escape').exists())

    def test_carbon_archive_cancel_cleans_and_retry_verifies_cache(self):
        from setup_kit import carbon_source
        for suffix in ('.zip', '.7z'):
            archive = self.carbon_archive(suffix)
            with mock.patch.object(self.events, 'progress', side_effect=b.Cancelled()), self.assertRaises(b.Cancelled):
                carbon_source.prepare(archive, self.paths.work, self.events)
            self.assertFalse((self.paths.work / 'carbon_src').exists())
            self.assertFalse((self.paths.work / 'carbon_src.json').exists())
            root = carbon_source.prepare(archive, self.paths.work, self.events)
            expected = b.digest_tree(root)
            write(root / 'TRACKS/fixture.bin', b'tampered unpack')
            self.assertEqual(b.digest_tree(carbon_source.prepare(archive, self.paths.work, self.events)), expected)
            b.remove(self.paths.work / 'carbon_src')
            (self.paths.work / 'carbon_src.json').unlink()

    def test_carbon_iso_not_decoded_refused_without_mw_installation(self):
        iso = self.home / 'Carbon.iso'
        write(iso, b'unknown Carbon disc layout')
        self.carbon = iso
        before = b.sha_file(iso)
        with self.assertRaises(b.SetupError):
            self.carbon_build()
        self.assertEqual(b.sha_file(iso), before)
        self.assertFalse((self.dest / 'NFS Most Wanted Native.app').exists())
        self.assertFalse((self.dest / 'NFS Carbon Native.app').exists())

    def test_derived_carbon_profile_refused_before_recovery(self):
        self.paths.data.mkdir(parents=True)
        profile = self.paths.data / "Carbon"
        for target in (self.carbon, self.source, self.paths.legacy,   # the inputs and Setup's own folders (no Most Wanted folder)
                       self.paths.work, self.paths.cache):
            with self.subTest(target=target.name):
                profile.symlink_to(target, target_is_directory=True)
                try:
                    with mock.patch.object(b, "recover_interrupted",
                                           side_effect=AssertionError("unsafe profile reached recovery")) as recovery:
                        with self.assertRaises(b.SetupError):
                            self.carbon_build()
                        recovery.assert_not_called()
                finally:
                    profile.unlink()

    def test_initial_carbon_profile_refused_before_recovery(self):
        self.paths.data.mkdir(parents=True)
        (self.paths.data / "Carbon").symlink_to(self.carbon, target_is_directory=True)
        with mock.patch.object(b, "recover_interrupted",
                               side_effect=AssertionError("unsafe profile reached recovery")) as recovery, \
             mock.patch.object(b, "build") as build, redirect_stdout(self.stream):
            rc = b.main(["--build", "--game", "carbon", "--carbon", str(self.carbon), "--dest", str(self.dest),
                         "--work", str(self.home / "new-work"), "--events"])
        self.assertEqual(rc, 1)
        recovery.assert_not_called()
        build.assert_not_called()
        self.assertFalse((self.home / "new-work").exists())
        self.assertFalse(self.paths.logs.exists())

    def test_initial_carbon_input_protected_before_writes(self):
        # Both a Carbon build and MW's optional executable pick protect the input folder.
        for game, picked in (("carbon", self.carbon), ("mw", self.carbon / "NFSC.exe")):
            with self.subTest(game=game), \
                 mock.patch.dict(os.environ, {"NFSMW_SETUP_LOGS": str(self.carbon / "setup-logs")}), \
                 mock.patch.object(b, "build", side_effect=b.SetupError("later rejection")), \
                 redirect_stdout(self.stream):
                rc = b.main(["--build", "--game", game, "--carbon", str(picked)] + (["--pc", str(self.pc)] if game == "mw" else []) +
                            ["--dest", str(self.dest), "--work", str(self.home / "new-work"), "--events"])
                self.assertEqual(rc, 1)
                self.assertFalse((self.carbon / "setup-logs").exists())
                self.assertFalse((self.home / "new-work").exists())

    def test_carbon_tracks_must_be_directory(self):
        shutil.rmtree(self.carbon / "TRACKS")
        write(self.carbon / "TRACKS", b"not a directory")
        with self.assertRaisesRegex(b.SetupError, "TRACKS"):
            b.validate_carbon(self.carbon)

    def test_carbon_build_installs_its_own_app_and_profile(self):
        installed, runner = self.carbon_build()
        app = self.dest / "NFS Carbon Native.app"
        self.assertEqual(Path(installed), app)
        self.assertTrue((app / "Contents/MacOS/NFSCarbon").is_file())
        self.assertFalse((self.dest / "NFS Most Wanted Native.app").exists())
        roots = dict(line.split("=", 1) for line in (self.paths.data / "Carbon/roots.txt").read_text().splitlines())
        self.assertTrue((Path(roots["pc_root"]) / "NFSC.exe").is_file())
        self.assertFalse((self.paths.data / "roots.txt").exists())   # Most Wanted's profile untouched
        stages = [json.loads(l)["id"] for l in self.stream.getvalue().splitlines() if json.loads(l)["ev"] == "stage"]
        order = [s for s, _ in b.STAGES]
        self.assertEqual(stages, order[:order.index("runtime") + 1] + ["carbon"] + order[order.index("runtime") + 1:])
        text = [" ".join(c) for c in runner.commands]
        for tool in ("recomp/build_private.py", "recomp/private_world.py", "recomp/build_private_runtime.py"):
            self.assertEqual(sum(tool in c for c in text), 1, tool)
        self.assertFalse(any("runtime/build.sh" in c for c in text))
        self.assertFalse(any("region.py" in c or "--proof" in c for c in text))
        # No command reads Most Wanted: once its TRACKS (world proof) and speed.exe (the runtime link's host ranges).
        self.assertEqual(sum("NFS_SPEED_EXE" in c or str(self.pc) in c for c in text), 0)
        self.assertFalse(any("recomp/build_all.py" in c for c in text))
        self.assertEqual([c for c in text if "gen_tables.py" in c], [c for c in text if "gen_tables.py --no-pc" in c])
        bundle = next(c for c in runner.commands if "runtime/bundle.sh" in " ".join(c))
        self.assertEqual(bundle[bundle.index("--game") + 1], "carbon")
        self.assertTrue(bundle[bundle.index("--boot") + 1].endswith("recomp/out/carbon/boot_carbon"))

    def test_carbon_update_from_its_own_version_and_remembered_folder(self):
        """Carbon's updater inputs: its own version 1.3.0 (Most Wanted's stays), its sources remembered at install in
        its own Setup state, and a 1.3.1 update staged from those alone, bound to its app and profile <data>/Carbon.
        Proof: with main 583933e9's tools/setup_kit this fails at its first check (bundle --version 4.6.0); fixed: passes."""
        from setup_kit import apply   # the checked-in launch transaction (the builds below load the build copy's)
        installed, runner = self.carbon_build()
        bundle = next(c for c in runner.commands if "runtime/bundle.sh" in " ".join(c))
        self.assertEqual(bundle[bundle.index("--version") + 1], "1.3.0")
        self.assertNotEqual(b.PUBLIC_VERSION.split(".")[0], "1")   # Most Wanted keeps its 4.x numbers
        with (Path(installed) / "Contents/Info.plist").open("rb") as f:
            self.assertEqual(plistlib.load(f)["CFBundleShortVersionString"], "1.3.0")
        choices = b.staging.load_choices(self.paths.state / "Carbon", required=True)
        self.assertEqual((Path(choices["pc"]), Path(choices["destination"])), (self.carbon.resolve(), Path(installed)))
        self.assertFalse((self.paths.state / "source-choices.json").exists())   # Most Wanted's choices untouched
        setup_kit.__path__ = self.package_path   # a new process (the update's preparer) imports the full package again
        stage = self.home / "carbon-update"
        real = b.build
        with mock.patch.object(b, "build", lambda args, paths, events, runner, source_root=None:
                               real(args, paths, events, CarbonRunner(self.log, self.child_env, paths.work), self.source)), \
             mock.patch.object(b, "CARBON_VERSION", "1.3.1"), \
             self.pipeline_stubs(), mock.patch.object(b, "assert_not_running"), redirect_stdout(self.stream):
            rc = b.main(["--stage-only", "--game", "carbon", "--stage", str(stage), "--work", str(self.paths.work), "--events"])
        self.assertEqual(rc, 0, self.stream.getvalue()[-2000:])
        ready = json.loads((stage / "ready.json").read_text())
        self.assertEqual((ready["version"], ready["app"], ready["profile"], ready["destination"]["path"]),
                         ("1.3.1", "NFS Carbon Native.app", str(self.paths.data / "Carbon"), str(installed)))
        record, app, data = apply.read_ready(stage / "ready.json", self.paths.data / "Carbon", Path(installed))
        self.assertTrue((Path(app) / "Contents/MacOS/NFSCarbon").is_file())
        self.assertTrue((stage / record["roots"]["pc_root"] / "NFSC.exe").is_file())
        self.assertFalse((self.paths.data / "roots.txt").exists())   # Most Wanted's profile untouched

    def test_carbon_1x_app_is_current(self):
        """A Carbon 1.x app is the current app (replaced, staged, applied); Most Wanted's 3.x rule stays its own.
        Proof: on main 583933e9 the Carbon 1.0.0 case is False (major >= 4 for every product)."""
        app = self.home / "apps/NFS Carbon Native.app"
        for bundle_id, version, current in ((b.CARBON_PRODUCT["bundle_id"], "1.0.0", True),
                                            (b.PUBLIC_ID, "3.9.0", False), (b.PUBLIC_ID, "4.0.0", True)):
            write(app / "Contents/Info.plist", plistlib.dumps(dict(CFBundleIdentifier=bundle_id, CFBundleShortVersionString=version)))
            self.assertEqual(ins.is_current_app(app, bundle_id), current, (bundle_id, version))

    def test_carbon_build_without_most_wanted_folder(self):
        shutil.rmtree(self.pc)
        real = b.build
        with mock.patch.object(b, "build", lambda args, paths, events, runner, source_root=None:
                               real(args, paths, events, CarbonRunner(self.log, self.child_env, paths.work), self.source)), \
             self.pipeline_stubs(), mock.patch.object(b, "assert_not_running"), redirect_stdout(self.stream):
            rc = b.main(["--build", "--game", "carbon", "--carbon", str(self.carbon), "--dest", str(self.dest),
                         "--work", str(self.paths.work), "--events"])
        self.assertEqual(rc, 0, self.stream.getvalue()[-2000:])
        self.assertTrue((self.dest / "NFS Carbon Native.app/Contents/MacOS/NFSCarbon").is_file())
        self.assertFalse(self.pc.exists())

    def test_carbon_refuses_a_most_wanted_folder(self):
        with mock.patch("sys.stdout", self.stream), mock.patch.object(b, "build") as build:
            rc = b.main(["--build", "--game", "carbon", "--carbon", str(self.carbon), "--pc", str(self.pc),
                         "--dest", str(self.dest), "--work", str(self.paths.work), "--events"])
        self.assertEqual(rc, 1)
        build.assert_not_called()
        self.assertIn("reads only the Carbon folder", json.loads(self.stream.getvalue())["msg"])

    def test_folder_without_nfsc_exe_refused_nothing_installed(self):
        (self.carbon / "NFSC.exe").unlink()
        with self.assertRaises(b.SetupError) as raised:
            self.carbon_build()
        self.assertIn("NFSC.exe", str(raised.exception))
        self.assertFalse((self.dest / "NFS Carbon Native.app").exists())
        self.assertFalse((self.paths.data / "Carbon/roots.txt").exists())

    def carbon_x360(self, title=0x454107EC):
        """A synthetic Carbon Xbox 360 folder, its converter copied into the fixture's Setup source."""
        tests = str(Path(__file__).resolve().parent)
        if tests not in sys.path:
            sys.path.insert(0, tests)
        import test_carbon_x360 as art
        self.assets = {art.GLOBALB: art.glyph_archive(), art.DYNTEX: art.diagram_archive()}
        for name in ("car_sources", "x360", "xdvdfs", "huff", "x360_extras"):
            write(self.source / f"tools/setup_kit/{name}.py", (ROOT / f"tools/setup_kit/{name}.py").read_bytes())
        # The build reloads the converter from its source copy: that copy carries the synthetic archives' fingerprints.
        text = (ROOT / "tools/setup_kit/carbon_x360.py").read_text()
        table = re.search(r"^FILES = \{.*?^\}\n", text, re.S | re.M).group(0)
        write(self.source / "tools/setup_kit/carbon_x360.py", text.replace(table, "FILES = " + repr(art.fingerprints(self.assets)) + "\n"))
        # --check runs the converter that is already imported.
        import setup_kit.carbon_x360 as imported
        patch = mock.patch.object(imported, "FILES", art.fingerprints(self.assets))
        patch.start()
        self.addCleanup(patch.stop)
        physical = art.helpers.archive_files(art.helpers.fake_xex(title), self.assets)
        folder = self.home / "Carbon Xbox 360"
        art.helpers.write_files(folder, physical)
        return folder

    def carbon_command(self, x360=None, check=False):
        runner = CarbonRunner(self.log, self.child_env, self.paths.work)
        args = types.SimpleNamespace(pc=None, dest=str(self.dest), x360=str(x360) if x360 else None, build=not check,
                                     check=check, game="carbon", carbon=str(self.carbon))
        with self.pipeline_stubs(), mock.patch.object(b, "assert_not_running"):
            return b.build(args, self.paths, self.events, runner, self.source), runner

    def installed_roots(self):
        return dict(line.split("=", 1) for line in (self.paths.data / "Carbon/roots.txt").read_text().splitlines())

    def test_carbon_artwork_installs_inside_carbon_data(self):
        installed, runner = self.carbon_command(self.carbon_x360())
        roots = self.installed_roots()
        self.assertEqual(sorted(roots), ["pc_root", "x360_root"])
        pc, art = Path(roots["pc_root"]), Path(roots["x360_root"])
        self.assertTrue(art.is_relative_to(pc.parent))   # beside the Carbon copy, in the same generation
        for name, data in self.assets.items():
            self.assertEqual((art / name).read_bytes(), data)
        self.assertEqual(sorted(p.relative_to(art).as_posix() for p in art.rglob("*") if p.is_file()), sorted(self.assets))
        self.assertFalse((self.paths.data / "roots.txt").exists())   # Most Wanted's profile untouched
        # No build command may take the artwork as an input.
        self.assertFalse([c for c in map(" ".join, runner.commands) if str(art) in c or "x360" in c])

    def test_carbon_without_a_console_copy_keeps_only_its_own_root(self):
        self.carbon_command()
        self.assertEqual(sorted(self.installed_roots()), ["pc_root"])

    def test_carbon_refuses_another_title_and_installs_nothing(self):
        source = self.carbon_x360(title=0x45410876)
        with self.assertRaisesRegex(b.SetupError, "not Need for Speed Carbon"):
            self.carbon_command(source)
        self.assertFalse((self.dest / "NFS Carbon Native.app").exists())
        self.assertFalse((self.paths.data / "Carbon/roots.txt").exists())

    def test_carbon_check_then_build_reuses_the_checked_artwork(self):
        source = self.carbon_x360()
        self.assertIsNone(self.carbon_command(source, check=True)[0])
        self.assertTrue((self.paths.work / (b.CHECK_X360 + ".json")).is_file())
        self.carbon_command(source)
        self.assertIn("Reusing the Xbox 360 files converted by the check", self.stream.getvalue())
        self.assertEqual(sorted(self.installed_roots()), ["pc_root", "x360_root"])

    def test_carbon_artwork_is_cached_per_game(self):
        source = self.carbon_x360()
        self.carbon_command(source)
        self.assertIn("Verified preparation saved", self.stream.getvalue())
        self.stream.truncate(0)
        self.stream.seek(0)
        self.carbon_command(source, check=True)   # the same copy: restored, not prepared again
        self.assertIn("persistent cache", self.stream.getvalue())
        mw = b.preparation_key("x", None, {}, {}, game="mw")
        self.assertNotEqual(b.preparation_key("x", None, {}, {}, game="carbon"), mw)
        self.assertEqual(mw, b.preparation_key("x", None, {}, {}))   # Most Wanted's keys are unchanged

    def test_command_line_accepts_the_console_copy_for_carbon(self):
        write(self.home / 'x', b'console fixture')
        with mock.patch.object(b, "build", side_effect=b.SetupError("reached build")), redirect_stdout(self.stream):
            rc = b.main(["--check", "--game", "carbon", "--carbon", str(self.carbon), "--x360", str(self.home / "x"),
                         "--dest", str(self.dest), "--work", str(self.home / "new-work"), "--events"])
        self.assertEqual(rc, 1)
        self.assertIn("reached build", self.stream.getvalue())

    def test_command_line_requires_the_carbon_folder(self):
        with mock.patch("sys.stdout", self.stream):
            rc = b.main(["--build", "--game", "carbon", "--pc", str(self.pc), "--dest", str(self.dest),
                         "--work", str(self.paths.work), "--events"])
        self.assertEqual(rc, 1)
        self.assertIn("--game carbon requires --build and --carbon", json.loads(self.stream.getvalue())["msg"])


class CarbonSetupDiscTests(Fixture):
    """Real synthetic ISO + RTPatch + supplied executable through the CLI pipeline.

    Base de6085c1: test_disc_cli_check_build_resume fails 1/1 (CLI refuses inputs).
    Wired implementation: same oracle passes 1/1, including validated installed data.
    Native compilers/signing are stubbed; extraction, patching and install are real.
    """
    def setUp(self):
        super().setUp()
        sys.path.insert(0, str(Path(__file__).parent))
        import test_carbon_patch as fixture
        self.patch_fixture = fixture
        self.good_exe = b"MZ compatible Carbon fixture"
        self.exe = self.home / "player/NFSC.exe"
        write(self.exe, self.good_exe)
        self.pin = mock.patch.object(b, "CARBON_EXE_SHA256", b.sha_file(self.exe))
        self.pin.start()
        self.addCleanup(self.pin.stop)
        self.update = self.home / "official.exe"
        self.records = [fixture.record("GLOBAL\\data.bin", fixture.DATA, fixture.CHAIN_CODES),
                        fixture.record("nfsc.exe", fixture.EXE)]
        write(self.update, fixture.update_bytes(self.records))
        import zipfile
        def archive(files):
            out = io.BytesIO()
            with zipfile.ZipFile(out, "w") as z:
                for name, data in files.items():
                    z.writestr(name, data)
            return out.getvalue()
        loose = {"nfsc.exe": fixture.EXE[0], "server.dll": b"server"}
        self.files = {
            "autorun.inf": "[Special]\r\nDisk=1\r\nProductGuiID={259C0ABB-A3B2-4D70-008F-BF7EE491B70B}\r\n".encode(),
            "common_filelist.txt": "1,1,nfsc.exe\r\n1,1,server.dll\r\n".encode(),
            "0compressed.zip": archive({"GLOBAL\\data.bin": fixture.DATA[0]}),
            "1compressed.zip": archive({"TRACKS\\track.bin": b"track", "MOVIES\\intro.vp6": b"movie"}),
            **loose}
        self.disc = make_iso(self.home / "Carbon.iso", self.files)

    def cli(self, *options, expected=0):
        setup_kit.__path__ = self.package_path
        real = b.build
        def build(args, paths, events, runner):
            return real(args, paths, events, CarbonRunner(self.log, self.child_env, paths.work), self.source)
        self.stream.seek(0)
        self.stream.truncate()
        with self.pipeline_stubs(), mock.patch.object(b, "build", side_effect=build), \
             mock.patch.object(b, "assert_not_running"), redirect_stdout(self.stream):
            rc = b.main(["--game", "carbon", "--work", str(self.paths.work), "--events", *options])
        self.assertEqual(rc, expected, self.stream.getvalue()[-2000:])

    def selection(self):
        return ["--carbon", str(self.disc), "--pc-update", str(self.update), "--pc-exe", str(self.exe),
                "--dest", str(self.dest)]

    def test_P5_7_missing_supplements_one_report(self):
        """P5 item 7: old 0/1 complete reports; collected-input implementation 1/1."""
        self.cli("--check", "--carbon", str(self.disc), "--dest", str(self.dest), expected=1)
        errors = [json.loads(line) for line in self.stream.getvalue().splitlines()
                  if json.loads(line).get("ev") == "error"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["msg"].splitlines(), [
            "Missing: official 1.4 update. Choose it above.",
            "Missing: NFSC.exe from that update. Choose it above."])

    def test_P5_7_second_disc_folder(self):
        """P5 item 7: old 0/1 accepted second folders; shared disc path 1/1."""
        first = self.home / "drive-a/disc"
        second = self.home / "drive-b/disc"
        for name, data in self.files.items():
            if name == "1compressed.zip":
                write(second / name, data)
            else:
                write(first / name, data)
        write(first / "common_filelist.txt", self.files["common_filelist.txt"] + b"2,1,\r\n")
        write(second / "autorun.inf", self.files["autorun.inf"].replace(b"Disk=1", b"Disk=2"))
        self.disc = first
        self.cli("--check", *self.selection(), "--pc-disc", str(second))
        root = self.paths.work / "carbon_src"
        self.assertEqual((root / "TRACKS/track.bin").read_bytes(), b"track")
        self.assertEqual((root / "NFSC.exe").read_bytes(), self.good_exe)

    def test_disc_cli_check_build_resume(self):
        before = [b.sha_file(p) for p in (self.disc, self.update, self.exe)]
        self.cli("--check", *self.selection())
        self.assertEqual(b.validate_carbon(self.paths.work / "carbon_src").read_bytes(), self.good_exe)
        self.cli("--build", *self.selection())
        saved = b.staging.load_choices(b.carbon_state(self.paths), required=True)
        self.assertEqual([saved[k] for k in ("pc", "pc_update", "pc_exe")],
                         [str(p) for p in (self.disc, self.update, self.exe)])
        stage = self.home / "update"
        self.cli("--stage-only", "--stage", str(stage))
        record = json.loads((stage / "ready.json").read_text())
        self.assertEqual([record["provenance"][k] for k in ("carbon", "pc_update", "pc_exe")],
                         [str(p) for p in (self.disc, self.update, self.exe)])
        root = stage / record["roots"]["pc_root"]
        self.assertEqual(b.validate_carbon(root).read_bytes(), self.good_exe)
        self.assertEqual((root / "GLOBAL/data.bin").read_bytes(), self.patch_fixture.DATA[-1])
        self.assertEqual(sorted(p.name for p in root.rglob("*.exe")), ["NFSC.exe"])
        self.assertEqual(before, [b.sha_file(p) for p in (self.disc, self.update, self.exe)])
        self.assertFalse(any((self.paths.work / "tmp").iterdir()))
        self.assertFalse((self.paths.state / "source-choices.json").exists())

    def prepare(self, update=True, exe=True):
        return b.carbon_source.prepare(self.disc, self.paths.work, self.events,
                                       self.update if update else None, self.exe if exe else None)

    def test_disc_build_cache_tracks_patch_and_executable(self):
        keys = []
        real = b.cache_key
        def key(*args, **kwargs):
            result = real(*args, **kwargs)
            keys.append(result)
            return result
        with mock.patch.object(b, "cache_key", side_effect=key):
            self.cli("--build", *self.selection())
            self.cli("--build", *self.selection())
            self.assertEqual(keys[-1], keys[-2])
            write(self.update, self.patch_fixture.update_bytes(list(reversed(self.records))))
            self.cli("--build", *self.selection())
            self.assertNotEqual(keys[-1], keys[-2])
            self.exe.write_bytes(self.good_exe + b"changed")
            with mock.patch.object(b, "CARBON_EXE_SHA256", b.sha_file(self.exe)):
                self.cli("--build", *self.selection())
            self.assertNotEqual(keys[-1], keys[-2])

    def test_disc_cli_missing_inputs(self):
        for flag, message in (("--pc-update", "Missing: official 1.4 update"),
                              ("--pc-exe", "Missing: NFSC.exe")):
            args = self.selection()
            index = args.index(flag)
            del args[index:index + 2]
            self.cli("--check", *args, expected=1)
            self.assertIn(message, self.stream.getvalue())

    def test_xbox_iso_in_pc_slot_points_to_optional_files(self):
        # On 7942da5b this test fails (0/1); with the fix it passes (1/1).
        from setup_kit import xdvdfs
        from test_car_sources import tiny_iso

        for base in (0, xdvdfs.PARTITION_OFFSETS[1]):
            xbox = self.home / f"console-{base}.iso"
            with xbox.open("wb") as image:
                image.seek(base)
                image.write(tiny_iso({"default.xex": b"XEX2"}))
            with self.subTest(base=base):
                with xbox.open("rb") as image:
                    self.assertIn("DEFAULT.XEX", xdvdfs.inventory(image))
                with self.assertRaises(b.SetupError) as error:
                    b.carbon_source.prepare(xbox, self.paths.work, self.events)
                self.assertEqual(str(error.exception), "This is an Xbox 360 disc image.")
                self.assertEqual(error.exception.fix,
                                 "Add it under Xbox 360 files (optional). Choose your PC game first.")

    def test_pc_disc_with_xbox_magic_in_file_data(self):
        # On 999f865a Check fails (0/1); with PC disc recognition first it passes (1/1).
        from setup_kit import xdvdfs

        # make_iso puts its first file at sector 21. The marker lives 11 sectors into that file.
        self.disc = make_iso(self.disc, {"pad.bin": bytes(11 * xdvdfs.SECTOR) + xdvdfs.MAGIC, **self.files})
        with self.disc.open("rb") as image:
            self.assertEqual(xdvdfs.find_base(image), 0)
        self.cli("--check", *self.selection())

    def test_disc_helper_fix_uses_setup_labels(self):
        # On 7942da5b this test fails (0/1); with the fix it passes (1/1).
        for update, exe in ((False, True), (True, False)):
            with self.subTest(update=update, exe=exe):
                with self.assertRaises(b.SetupError) as error:
                    self.prepare(update, exe)
                self.assertEqual(error.exception.fix,
                                 "Use Carbon update to select the official update and NFSC.exe to select your compatible game executable.")

    def test_missing_carbon_disc_fix_points_to_sibling_inputs(self):
        # On 7942da5b this test fails (0/1); with the fix it passes (1/1).
        self.disc = make_iso(self.disc, dict(self.files, **{
            "common_filelist.txt": self.files["common_filelist.txt"] + b"2,1,\r\n"}))
        with self.assertRaisesRegex(b.SetupError, "Disc 2 of the set") as error:
            self.prepare()
        self.assertEqual(error.exception.fix,
                         "Keep all disc images together, or copy each disc into its own folder beside the others. Then choose disc 1 again.")

    def test_disc_missing_inputs_and_wrong_exe(self):
        for update, exe, message in ((False, True, "Missing: official 1.4 update"),
                                      (True, False, "Missing: NFSC.exe")):
            with self.subTest(message=message), self.assertRaisesRegex(b.SetupError, message):
                self.prepare(update, exe)
        for data in (b"wrong executable", self.patch_fixture.EXE[0]):
            self.exe.write_bytes(data)
            with self.assertRaisesRegex(b.SetupError, "NFSC.exe is not the supported PC release of Need for Speed Carbon"):
                self.prepare()
        self.assertFalse((self.paths.work / "carbon_src").exists())

    def test_disc_wrong_edition_patch_and_cancel_cleanup(self):
        f = self.patch_fixture
        write(self.update, f.update_bytes([self.records[0], f.record("nfsc.exe", (b"wrong-edition", b"target"))]))
        with self.assertRaisesRegex(b.SetupError, "wrong edition or changed file"):
            self.prepare()
        self.assertFalse((self.paths.work / "carbon_src").exists())
        self.assertFalse(any((self.paths.work / "tmp").iterdir()))
        write(self.update, f.update_bytes(self.records))
        # Cancel during patch output, after the raw executable was extracted.
        real = b.carbon_source.carbon_patch.Update.apply
        def cancelled(patch, source, destination, progress=None):
            def stop(*args):
                raise b.Cancelled()
            return real(patch, source, destination, progress=stop)
        with mock.patch.object(b.carbon_source.carbon_patch.Update, "apply", cancelled), self.assertRaises(b.Cancelled):
            self.prepare()
        self.assertFalse((self.paths.work / "carbon_src").exists())
        self.assertFalse((self.paths.work / "carbon_src.json").exists())
        self.assertFalse(any((self.paths.work / "tmp").iterdir()))
        b.validate_carbon(self.prepare())

    def test_P5_10_carbon_tool_change_invalidates_disc_cache(self):
        """P5 item 10 Carbon: old patcher calls 1/2; fixed 2/2."""
        module = b.carbon_source.carbon_patch
        real_hash = b.sha_file
        tool = Path(b.__file__).with_name("carbon_patch.py")
        with mock.patch.object(module.Update, "apply", autospec=True, side_effect=module.Update.apply) as apply:
            self.prepare()
            self.prepare()
            self.assertEqual(apply.call_count, 1)
            with mock.patch.object(b, "sha_file", side_effect=lambda p: "new tool" if Path(p) == tool else real_hash(p)):
                self.prepare()
            self.assertEqual(apply.call_count, 2)

    def test_disc_cache_binds_every_input(self):
        module = b.carbon_source.carbon_patch
        real = module.Update.apply
        with mock.patch.object(module.Update, "apply", autospec=True, side_effect=real) as apply:
            self.prepare()
            self.prepare()
            self.assertEqual(apply.call_count, 1)
            # Another supported patch container with the same outputs still invalidates.
            write(self.update, self.patch_fixture.update_bytes(list(reversed(self.records))))
            self.prepare()
            self.assertEqual(apply.call_count, 2)
            self.exe.write_bytes(self.good_exe + b"changed")
            with mock.patch.object(b, "CARBON_EXE_SHA256", b.sha_file(self.exe)):
                b.validate_carbon(self.prepare())
            self.assertEqual(apply.call_count, 3)
            self.files["server.dll"] = b"changed disc data"
            make_iso(self.disc, self.files)
            with mock.patch.object(b, "CARBON_EXE_SHA256", b.sha_file(self.exe)):
                self.assertEqual((self.prepare() / "server.dll").read_bytes(), b"changed disc data")
            self.assertEqual(apply.call_count, 4)


class PrivateCarbonSetupTests(unittest.TestCase):
    @unittest.skipUnless(all(os.environ.get(k) for k in ("CARBON_DISC_IMAGE", "CARBON_PATCH_EXE", "CARBON_PLAYER_EXE")),
                         "private Carbon disc/update/executable paths not supplied")
    def test_private_cli_build(self):
        """Opt-in full CLI build; scratch outputs are confined to the worktree and deleted."""
        selected = [Path(os.environ[k]).resolve() for k in ("CARBON_DISC_IMAGE", "CARBON_PATCH_EXE", "CARBON_PLAYER_EXE")]
        if not all(p.is_file() for p in selected):
            self.skipTest("private Carbon inputs absent")
        with tempfile.TemporaryDirectory(prefix="carbon-setup-private-", dir=ROOT / ".metal-cache") as td:
            base = Path(td)
            # A packaged payload beside work keeps Setup's normal source protection intact.
            payload = base / "payload"
            b.copy_source(ROOT, payload, b.source_inventory(ROOT, b.CARBON_EXTS))
            real_build = b.build
            with mock.patch.dict(os.environ, {"NFSMW_SETUP_HOME": str(base / "home")}), \
                 mock.patch.object(b, "ROOT", payload), \
                 mock.patch.object(b, "build", side_effect=functools.partial(real_build, source_root=payload)):
                rc = b.main(["--build", "--game", "carbon", "--carbon", str(selected[0]),
                             "--pc-update", str(selected[1]), "--pc-exe", str(selected[2]),
                             "--dest", str(base / "apps"), "--work", str(base / "work")])
                paths = b.Paths(base / "work")
                if rc == 0:
                    roots = dict(line.split("=", 1) for line in (paths.data / "Carbon/roots.txt").read_text().splitlines())
                    root = Path(roots["pc_root"])
                else:
                    root = paths.work / "carbon_src"
                if root.is_dir():
                    b.validate_carbon(root)
                    files = [p for p in root.rglob("*") if p.is_file()]
                    print("Private Carbon CLI: validate_carbon PASS; files=%d bytes=%d" %
                          (len(files), sum(p.stat().st_size for p in files)))
                logs = sorted(paths.logs.glob("*.log"), key=lambda p: p.stat().st_mtime)
                tail = "\n".join(logs[-1].read_text(errors="replace").splitlines()[-30:])[-6000:] if logs else "No build log."
                self.assertEqual(rc, 0, tail)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == '--carbon-repack-iso':
        selected = Path(sys.argv[2]).expanduser() if len(sys.argv) > 2 else Path('/absent')
        if not selected.is_file():
            print('SKIP: real Carbon repack ISO absent')
            sys.exit(3)
        os.environ['CARBON_REPACK_ISO'] = str(selected.resolve())
        sys.argv[1:] = ['CarbonRepackTests.test_real_repack_iso']
    if '--private-carbon-disc' in sys.argv:
        if not all(os.environ.get(k) and Path(os.environ[k]).is_file()
                   for k in ('CARBON_DISC_IMAGE', 'CARBON_PATCH_EXE', 'CARBON_PLAYER_EXE')):
            print('SKIP: private Carbon inputs absent')
            sys.exit(3)
        sys.argv[1:] = ['PrivateCarbonSetupTests.test_private_cli_build']
    unittest.main(verbosity=2)
