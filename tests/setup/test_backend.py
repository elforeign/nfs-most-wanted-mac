"""Public Setup offline proofs; run with the project's Python -I.

Effective TMPDIR log: original code fails 1/1; fixed code passes 1/1, with
two real child commands sharing one log entry independent of the caller TMPDIR.

Release-version proof: bundle test passes 1/1 with PUBLIC_VERSION 4.3.0;
changing it to 4.2.0 fails 1/1 against the independent 4.3.0 expectations.

Run-log link regression: good 2/2 symlink/hardlink preservation oracles pass;
broken base 2c887bc3 passes 0/2 (2 failures) after an injected runtime build failure.

T3 stub toolchain: good probe passes; missing/old clang, SDK and the actual
recompiler LLVM option fail. Bypassing preflight breaks the oracle. A toolchain
without `metal` (Command Line Tools) passes, stubbed and real (DEVELOPER_DIR=CLT):
before the fix both raised SetupError (xcrun exit 8 / 72).
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
T9 same content resumes; source/dependency/toolchain/player changes invalidate;
corrupt/stale/missing outputs rebuild. Trusting existence breaks the oracle.
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
No game launch, network or real Library/Applications writes. All fixtures live
under a temporary NFSMW_SETUP_HOME. Production libraries/signing are stubbed in
pipeline tests; toolchain probes use executable stubs; compile proof uses clang.
"""
import ast
import collections
import concurrent.futures
from contextlib import contextmanager
import errno
import functools
import importlib.util
import io
import json
import os
from pathlib import Path
import plistlib
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
        self.source = self.home / "payload"
        for name in b.SOURCE_DIRS:
            (self.source / name).mkdir(parents=True)
        write(self.source / "setup.sh", "#!/bin/sh\n", True)
        write(self.source / "recomp/build_all.py", "# stubbed compiler driver\n")
        write(self.source / "runtime/build.sh", "# stubbed linker driver\n")
        write(self.source / "tools/gen_tables.py", "# stubbed tables driver\n")
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
        if self.fail == name:
            raise b.SetupError(f"injected {name} failure")
        if self.omit == name:
            if name == "recompile":
                write(source / "recomp/out/report.txt", "compile errors: 1\n")
            return ""
        if name == "recompile":
            write(source / "recomp/out/speed.a", b"verified new archive")
            write(source / "recomp/out/speed_32.c", b"fixture C")
        elif name == "tables":
            for table in ("attrib_x360", "x360audio_attrib", "attrib_light_data", "trees_params",
                          "controltype_buttons", "auxlights_data", "glass_uv1"):
                write(source / f"runtime/gen/{table}.inc", b"empty PC-only table")
        elif name == "ffmpeg":
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
        result = b.preflight(runner, self.paths.work)
        self.assertEqual(result["sdk_version"], "15.0")
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
        with self.assertRaisesRegex(b.SetupError, "Not available yet"):
            b.validate_pc(self.home / "disc.iso", self.source)

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
    def compile_fixture(self, text, broken=False):
        tree = ast.parse(text)
        main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
        out = self.home / ("broken-compile" if broken else "compile")
        out.mkdir(exist_ok=True)
        write(out / "speed.a", b"stale archive")
        write(out / "runtime.h", "#include <stdint.h>\ntypedef struct {int dummy;} Cpu;\n")
        class RC:
            def __init__(self, **kw):
                self.pe = types.SimpleNamespace(fingerprint="fixture")
            def translate(self, *args):
                return ("this deliberately will not compile !!!" if broken else "void f_00000001(Cpu *c) {}"), None
        fp = types.SimpleNamespace(SUPPORTED={"fixture": "0"}, NAMES=["fixture"], WHOLE_FILE_SHA256="0")
        ns = dict(argparse=__import__("argparse"), os=os, sys=sys, Path=Path, collections=collections,
                  concurrent=concurrent, subprocess=subprocess, OUT=str(out), HERE=str(out), CHUNK=800,
                  build_inputs=types.SimpleNamespace(recomp_key=lambda **kw: 'fixture',
                      fresh=lambda *a: False, outputs=lambda: {}, record=lambda *a: None),
                  load_functions=lambda: [(1, 2, "fixture")], add_data_referenced=lambda x: x,
                  load_hooks=lambda: {},
                  recomp=types.SimpleNamespace(Recompiler=RC, CFLAGS=["-O0"], exe_fingerprint=fp,
                                               EXE=str(out / "speed.exe")))
        # Keep translation transforms isolated as main grows; compilation and
        # archive/error handling below still run for real.
        functions = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
        functions.update(alias.asname or alias.name for n in tree.body if isinstance(n, ast.ImportFrom)
                         for alias in n.names)
        referenced = {n.id for n in ast.walk(main) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        for name in (functions & referenced) - ns.keys() - {"main"}:
            ns[name] = lambda *args, **kwargs: args[-1]
        server = types.ModuleType("server_module")
        server.build = mock.Mock()
        with mock.patch.dict(sys.modules, server_module=server), \
                mock.patch.object(sys, "argv", ["test", "--jobs", "1"]), \
                mock.patch("sys.stdout", new=io.StringIO()):
            exec(compile(ast.Module(body=[main], type_ignores=[]), "build_all.py", "exec"), ns)
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
        old = text.replace('    if os.path.exists(lib):\n        os.remove(lib)\n', '', 1)
        old = old.replace('    if errors:\n        raise SystemExit(1)\n', '', 1)
        self.assertNotEqual(old, text)
        status, out = self.compile_fixture(old, broken=True)
        self.assertEqual(status, 0)
        self.assertTrue((out / "speed.a").exists())
        with self.assertRaises(AssertionError):
            oracle(status, out)

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
        # Any stale archive at the keyed source path is removed before retry.
        for source in self.paths.cache.glob("builds/*/source"):
            write(source / "recomp/out/speed.a", b"stale archive")
        with self.assertRaisesRegex(b.SetupError, "without all required outputs"):
            self.build(runner)
        self.assertEqual(before, self.snapshot())
        runner.omit = "runtime"
        for source in self.paths.cache.glob("builds/*/source"):
            write(source / "runtime/boot", b"stale runtime", True)
        with self.assertRaisesRegex(b.SetupError, "without all required outputs"):
            self.build(runner)
        self.assertEqual(before, self.snapshot())


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
        self.assertTrue(any("recomp/build_all.py" in c for c in third.commands))
        write(self.source / "runtime/new.c", "changed source")
        fourth = FakeRunner(self.log, self.child_env, self.paths.work)
        self.build(fourth)
        self.assertTrue(any("runtime/build.sh" in c for c in fourth.commands))
        for component in ("toolchain_version", "dependency_pin"):
            setattr(self, component, "fixture-v2")
            changed = FakeRunner(self.log, self.child_env, self.paths.work)
            self.build(changed)
            self.assertTrue(any("runtime/build.sh" in c for c in changed.commands), component)
        parts = [dict(source={"a": "1"}, dependencies={"pin": "1"}, toolchain={"clang": "1"}, pc_hash="1", x360_hash="1")]
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
        cache = b.Cache(self.home / "stage-cache", self.events)
        output = self.home / "output.a"
        calls = []
        def action():
            calls.append(1)
            write(output, b"fresh")
        cache.run("stage", [output], action)
        cache.run("stage", [output], action)
        self.assertEqual(len(calls), 1)
        write(output, b"stale")
        cache.run("stage", [output], action)
        output.unlink()
        cache.run("stage", [output], action)
        (cache.directory / "stage.json").unlink()
        cache.run("stage", [output], action)
        self.assertEqual(len(calls), 4)
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
        proc = subprocess.Popen([sys.executable, "-I", "-c", code, str(ROOT / "tools"), str(self.home)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            deadline = time.monotonic() + 5
            while not pidfile.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(pidfile.exists())
            self.assertEqual(os.read(read_fd, 100), b"ready")
            proc.send_signal(signal.SIGTERM)
            self.assertEqual(proc.wait(timeout=5), 130)
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
        self.build()
        self.assertFalse(self.new_saves.exists())
        self.assertFalse([e for e in self.events_seen() if e["ev"] == "warn"])


class PCZipTests(Fixture):
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
        for limit in ("ZIP_MAX_BYTES", "ZIP_MAX_FILES"):
            with self.subTest(limit), mock.patch.object(b, limit, 3), self.assertRaisesRegex(b.SetupError, "far larger"):
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
        self.assertEqual(public["CFBundleShortVersionString"], "4.3.0")
        self.assertEqual(public["CFBundleVersion"], "4.3.0")
        self.assertEqual(public["NFSMWSourceIdentity"], "content-hash")
        via_env = bundle(overrides=dict(NFSMW_BUNDLE_NAME=b.PUBLIC_NAME, NFSMW_BUNDLE_ID=b.PUBLIC_ID, NFSMW_BUNDLE_EXEC="NFSMostWanted",
                                       NFSMW_BUNDLE_VERSION="4.0.1", NFSMW_SOURCE_ID="explicit-source"), expect=b.PUBLIC_NAME)
        self.assertEqual(via_env["CFBundleShortVersionString"], "4.0.1")
        with self.assertRaises(AssertionError):
            self.assertEqual(owner["CFBundleShortVersionString"], "4.0.0")  # broken: git-count version
        result = subprocess.run(["/bin/sh", str(runtime / "bundle.sh"), str(self.home / "bundled"), "--version", "4..0"],
                                env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)

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
        self.assertEqual(schema["$defs"]["stage"]["enum"], [s for s, _ in b.STAGES] + ["stage"])
        names = {shape["properties"]["ev"]["const"] for shape in schema["oneOf"]}
        self.assertEqual(names, {"stage", "progress", "warn", "error", "features", "done", "ready"})
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
        # Not reused: a changed copy (modification time), a changed check output, or no check at all.
        run(True)
        os.utime(iso, ns=(1, 1))
        run(False)
        self.assertEqual(len(calls), 3)
        run(True)
        write(self.paths.work / "check-x360/prepared/files/file", b"tampered")
        run(False)
        self.assertEqual(len(calls), 5)
        roots = dict(l.split("=", 1) for l in (self.paths.data / "roots.txt").read_text().splitlines())
        self.assertEqual((Path(roots["x360_root"]) / "file").read_bytes(), b"converted")
        run(False)
        self.assertEqual(len(calls), 6)

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


@contextmanager
def contextlib_nullcontext(value=None):
    yield value


if __name__ == "__main__":
    unittest.main(verbosity=2)
