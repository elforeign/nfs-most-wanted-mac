"""




























"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import errno
import fcntl
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import traceback
import unicodedata
import uuid
import zipfile

from .install import (PUBLIC_BUNDLE_ID, InstallError, OWNER_MARK, digest_tree, mark_owned, owned, real_dir,
                      recover_interrupted, remove, transactional_install, write_synced)

ROOT = Path(__file__).resolve().parents[2]
FFMPEG_SHA = "733984395e0dbbe5c046abda2dc49a5544e7e0e1e2366bba849222ae9e3a03b1"
PUBLIC_NAME = "NFS Most Wanted Native"
PUBLIC_ID = PUBLIC_BUNDLE_ID  # also the 3.x id: the Dock icon carries over. Never local.nfsmw-port.
PUBLIC_EXEC = "NFSMostWanted"  # never NFSMW: a distinct name, so this app is never mistaken for another build
PUBLIC_VERSION = "4.2.0"
LLVM_FLAGS = ["-mllvm", "-capture-tracking-max-uses-to-explore=100000"]
STAGES = [
    ("preflight", "Checking developer tools"), ("pc", "Checking the installed PC game"),
    ("x360", "Preparing your Xbox 360 copy"), ("recompile", "Recompiling the PC executable"),
    ("tables", "Generating game tables"), ("audio", "Building audio"),
    ("shaders", "Building the shader translator"), ("ffmpeg", "Verifying FFmpeg"),
    ("runtime", "Building the native runtime"), ("assets", "Verifying player assets"),
    ("bundle", "Signing and verifying the app"), ("install", "Installing the verified app"),
]
X360_MISSING = ["world", "audio", "reverb", "music", "police", "nis", "vinyls", "textures", "movies"]


class SetupError(Exception):
    def __init__(self, msg, fix="Open the log for details, correct the problem and try again."):
        super().__init__(msg)
        self.fix = fix


class Cancelled(SetupError):
    def __init__(self):
        super().__init__("Setup was cancelled.", "Run Setup again to resume verified build stages.")


def sha_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def identity(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def clean_path(value):
    p = Path(value).expanduser().absolute().resolve()
    if any(c in str(p) for c in "\r\n\0") or len(os.fsencode(p)) > 900:
        raise SetupError("The selected path is too long or contains a line break.", "Choose a shorter ordinary folder path.")
    return p


class Paths:
    def __init__(self, work, env=None):
        env = os.environ if env is None else env
        home = Path(env.get("HOME", str(Path.home())))
        override = env.get("NFSMW_SETUP_HOME")
        base = Path(override).expanduser() if override else None
        self.work = clean_path(work)
        defaults = {
            "state": base / "state" if base else home / "Library/Application Support/NFSMW-Setup",
            "logs": base / "logs" if base else home / "Library/Logs/NFSMW-Setup",
            "cache": base / "cache" if base else home / "Library/Caches/NFSMW-Setup",
            "data": base / "data" if base else home / "Library/Application Support/NFSMW-port",
            # 3.x profile folder (only read: its career saves are copied into a 4.0 profile that has none).
            "legacy": base / "legacy" if base else home / "Library/Application Support/NFS Most Wanted Native",
        }
        for key, default in defaults.items():
            setattr(self, key, clean_path(env.get("NFSMW_SETUP_" + key.upper(), str(default))))
        self.default_work = clean_path(self.state / "work")
        self.default_cache = clean_path(defaults["cache"])
        # FFmpeg is configured and installed inside the cache (its --prefix and generated files take the path
        # unquoted): a cache path with a quote, space or other shell character moves to the per-user cache folder.
        self.cache_moved = None
        if not SHELL_SAFE.match(str(self.cache)):
            self.cache_moved, self.cache = self.cache, safe_cache_dir()


def safe_cache_dir():
    """"""
    try:
        base = os.confstr("CS_DARWIN_USER_CACHE_DIR")
    except (ValueError, OSError):
        base = None
    if not base:
        try:
            base = subprocess.run(["/usr/bin/getconf", "DARWIN_USER_CACHE_DIR"], capture_output=True, text=True).stdout.strip()
        except OSError:
            base = ""
    base = os.path.realpath(base) if base and os.path.isdir(base) else ""
    if not base or not SHELL_SAFE.match(base):
        base = "/private/tmp/nfsmw-setup-cache-" + str(os.getuid())
    return Path(base) / "NFSMW-Setup"


# (pc_src.tmp and check-x360.tmp are the stamps' temporary files: an interrupted run leaves them.)
SETUP_WORK_NAMES = {"run-log.txt", "tmp", "tool-home", "toolchain", "logs", "stage", "check-x360", "check-x360.json",
                    "check-x360.tmp", "pc_src", "pc_src.json", "pc_src.tmp", ".setup.lock", OWNER_MARK}
SETUP_CACHE_NAMES = {"builds", "build.lock", OWNER_MARK}


def claim(folder, default, known, what, registry):
    """Make folder Setup's own (OWNER_MARK) or refuse it. Setup removes and rebuilds things inside its work and
    cache folders, so a chosen folder must be new, empty, or already Setup's; an existing folder holding anything
    else (~/Documents) is never used. The default folder may also hold only Setup's own names from an earlier run."""
    folder = Path(folder)
    if os.path.lexists(folder) and not real_dir(folder):
        raise SetupError(f"The {what} folder {folder} is a link or not a folder.", f"Choose a new, empty {what} folder.")
    folder.mkdir(parents=True, exist_ok=True)
    if not real_dir(folder):
        raise SetupError(f"The {what} folder {folder} is reached through a link.", f"Choose a new, empty {what} folder.")
    if owned(folder, registry):
        return
    entries = set(os.listdir(folder))
    if entries and not (default and entries <= known):
        raise SetupError(f"The {what} folder {folder} already holds other files; Setup only uses a folder of its own.",
                         f"Choose a new or empty {what} folder.")
    mark_owned(folder, registry)


FIXED_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
CPU_NAMES = {0x0100000C: "arm64", 0x01000007: "x86_64", 7: "i386"}


def machine_archs(path, depth=0):
    """Architectures a tool can run natively: Mach-O (thin or universal) header, or a #! script's interpreter."""
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
    except OSError:
        return set()
    if head[:2] == b"#!" and depth < 4:
        interpreter = head[2:].split(b"\n", 1)[0].strip().split()
        if not interpreter:
            return set()
        if os.path.basename(interpreter[0]) == b"env" and len(interpreter) > 1:
            found = shutil.which(os.fsdecode(interpreter[1]), path=FIXED_PATH)
            return machine_archs(found, depth + 1) if found else set()
        return machine_archs(os.fsdecode(interpreter[0]), depth + 1)
    magic = int.from_bytes(head[:4], "big") if len(head) >= 4 else 0
    if magic in (0xCAFEBABE, 0xCAFEBABF):  # universal (fat32/fat64), big-endian
        count = int.from_bytes(head[4:8], "big")
        size = 20 if magic == 0xCAFEBABE else 32
        return {CPU_NAMES.get(int.from_bytes(head[8 + i * size:12 + i * size], "big"), "other") for i in range(min(count, 100))}
    if int.from_bytes(head[:4], "little") in (0xFEEDFACF, 0xFEEDFACE):  # thin Mach-O, little-endian
        return {CPU_NAMES.get(int.from_bytes(head[4:8], "little"), "other")}
    return set()


def translated():
    """True when this process runs under Rosetta (sysctl.proc_translated = 1)."""
    import ctypes
    value, size = ctypes.c_int(0), ctypes.c_size_t(4)
    libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    if libc.sysctlbyname("sysctl.proc_translated".encode(), ctypes.byref(value), ctypes.byref(size), None, 0):
        return False  # the key is absent where there is no translation at all
    return value.value == 1


# Characters an unquoted $TMPDIR survives in a shell script. FFmpeg's configure writes `: > $TMPC` with TMPC under
# $TMPDIR, so the app's default work folder ("~/Library/Application Support/...": a space) failed every build there
# ("$TMPC: ambiguous redirect", M4 F1).
SHELL_SAFE = re.compile(r"[A-Za-z0-9/._+,:@%=-]+\Z")


@contextmanager
def tool_tmp(work, env=None):
    """


"""
    env = os.environ if env is None else env
    own = Path(work) / "tmp"
    if SHELL_SAFE.match(str(own)):
        if os.path.lexists(own) and not real_dir(own):
            raise SetupError(f"The temporary folder {own} is a link or not a folder.", "Remove it, then run Setup again.")
        own.mkdir(exist_ok=True)
        if not real_dir(own) or os.lstat(own).st_uid != os.getuid():
            raise SetupError(f"The temporary folder {own} is not yours.", "Remove it, then run Setup again.")
        yield own
        return
    base = os.path.realpath(env.get("TMPDIR") or tempfile.gettempdir())
    if not SHELL_SAFE.match(base) or not os.path.isdir(base):
        base = "/private/tmp"
    path = Path(tempfile.mkdtemp(prefix="nfsmw-setup-", dir=base))
    st = os.lstat(path)
    made = (st.st_dev, st.st_ino)
    try:
        yield path
    finally:
        with ignored_cancellation():
            try:
                st = os.lstat(path)
                if (st.st_dev, st.st_ino) == made and not os.path.islink(path):
                    shutil.rmtree(path, ignore_errors=True)
            except OSError:
                pass


def child_environment(roots, work, env=None, tmp=None):
    env = os.environ if env is None else env
    # No inherited BOOT_*, NFS_*, compiler flags, DYLD_*, PYTHONPATH, SDKROOT,
    # external venv or user-site injection. TOOLS_PY keeps the venv symlink path.
    # A fixed PATH, as the Setup app passes: Homebrew/MacPorts/Intel tools earlier on a
    # Terminal PATH must never be the ones Setup builds with (command-line route).
    result = {k: env[k] for k in ("LANG", "LC_ALL") if k in env}
    result["PATH"] = FIXED_PATH
    tmp = Path(tmp) if tmp is not None else Path(work) / "tmp"
    result.update(HOME=str(work / "tool-home"), TOOLS_PY=os.path.abspath(sys.executable), NFS_PUBLIC="1", NFS_ROOTS=str(roots),
                  TMPDIR=str(tmp), CLANG_MODULE_CACHE_PATH=str(tmp / "clang-modules"),
                  PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1")
    return result


class Events:
    def __init__(self, enabled, log, stream=None):
        self.enabled, self.log, self.stream = enabled, log, stream or sys.stdout
        self.current = "preflight"
        self.installed = None

    def emit(self, ev, **fields):
        obj = dict(ev=ev, **fields)
        line = json.dumps(obj, ensure_ascii=True, separators=(",", ":"))
        try:
            self.log.write((line + "\n").encode())
            self.log.flush()
        except OSError:
            if ev != "done":
                raise
        if self.enabled:
            try:
                print(line, file=self.stream, flush=True)
            except OSError:
                if ev != "done":
                    raise
        elif ev in ("stage", "error", "warn", "done"):
            print(fields.get("title") or fields.get("msg") or fields.get("app"), file=self.stream, flush=True)

    def stage(self, name):
        self.current = name
        self.emit("stage", id=name, title=dict(STAGES)[name])

    def progress(self, done=1, total=1):
        self.emit("progress", id=self.current, done=done, total=total)


class Runner:
    """Log every byte; own each child process group, including grandchildren."""
    def __init__(self, log, env, work):
        self.log, self.env, self.work = log, env, work

    def run(self, argv, cwd=None, capture=False):
        argv = [str(a) for a in argv]
        self.log.write(("$ " + shlex.join(argv) + "\n").encode())
        self.log.flush()
        output = tempfile.TemporaryFile(dir=self.work) if capture else self.log
        proc = None
        try:
            # A Cancel arriving while the child is being created is held until `proc` names it (its own session and
            # process group), then delivered here, inside the try: the finally below always stops that group.
            with held_cancellation():
                proc = subprocess.Popen(argv, cwd=cwd, env=self.env, stdin=subprocess.DEVNULL,
                                        stdout=output, stderr=self.log, start_new_session=True)
            rc = proc.wait()
            if capture:
                output.seek(0)
                data = output.read()
                self.log.write(data)
                self.log.flush()
            else:
                data = b""
            if rc:
                raise SetupError(f"{Path(argv[0]).name} failed (exit {rc}).")
            return data.decode("utf-8", "replace") if capture else ""
        finally:
            if capture:
                output.close()
            if proc is not None:
                # Held, not ignored: a Cancel landing here is delivered once the group is gone (never lost).
                with held_cancellation():
                    try:
                        os.killpg(proc.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    # Stop the whole group, even if the direct child exited and
                    # left descendants behind. These are exclusively our pids.
                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        pass
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    proc.wait()


@contextmanager
def ignored_cancellation():
    old = {s: signal.signal(s, signal.SIG_IGN) for s in (signal.SIGINT, signal.SIGTERM)}
    try:
        yield
    finally:
        for s, h in old.items():
            signal.signal(s, h)


@contextmanager
def held_cancellation():
    """Record SIGINT/SIGTERM instead of acting on them, then re-deliver the first one on exit.

    Not SIG_IGN: an ignored disposition would be inherited across exec by the child being started."""
    pending = []
    def hold(signum, frame):
        pending.append(signum)
    old = {s: signal.signal(s, hold) for s in (signal.SIGINT, signal.SIGTERM)}
    try:
        yield
    finally:
        for s, h in old.items():
            signal.signal(s, h)
        if pending:
            signal.raise_signal(pending[0])


@contextmanager
def cancellation():
    def stop(signum, frame):
        raise Cancelled()
    old = {s: signal.signal(s, stop) for s in (signal.SIGINT, signal.SIGTERM)}
    try:
        yield
    finally:
        for s, h in old.items():
            signal.signal(s, h)


@contextmanager
def exclusive(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SetupError("Another Setup is using this work or cache folder.", "Wait for the other Setup to finish.") from None
        yield


def preflight(runner, work):
    if platform.system() != "Darwin" or platform.machine() != "arm64" or translated():
        raise SetupError("Setup requires native Apple Silicon macOS; it is running under Rosetta (Intel emulation).",
                         "Run Setup natively: in Terminal use `arch -arm64 ./setup.sh ...`, and turn off "
                         "\"Open using Rosetta\" for Terminal and the Setup app.")
    if sys.version_info < (3, 9):
        raise SetupError("Setup's Python is too old.", "Download Setup again.")
    required = ("clang", "clang++", "ar", "make", "xcrun", "otool", "sips", "iconutil", "plutil", "codesign", "tar", "shasum", "sh")
    missing = [t for t in required if shutil.which(t, path=runner.env["PATH"]) is None]
    if missing:
        raise SetupError("Missing developer tools: " + ", ".join(missing), "Install or update Apple developer tools, then try again.")
    # An Intel-only tool would run under Rosetta and build for the wrong Mac (or fail half-way). Check each one's
    # Mach-O header before running any of them.
    intel = []
    for t, p in [("python", sys.executable)] + [(t, shutil.which(t, path=runner.env["PATH"])) for t in
                                                ("clang", "clang++", "make", "ar", "xcrun", "sh")]:
        archs = machine_archs(p)
        if not archs & {"arm64"}:
            intel.append(f"{t} ({p}: {', '.join(sorted(archs)) or 'unknown format'})")
    if intel:
        raise SetupError("Intel-only (x86_64) developer tools found: " + "; ".join(intel),
                         "Setup uses Apple's tools in /usr/bin. Remove or rename the Intel-only tools, or install "
                         "Apple's Command Line Tools (xcode-select --install), then try again.")
    # Apple's ar has no --version option. Hash the actual tool and probe archive
    # creation instead of treating that unsupported option as a missing CLT.
    versions = {t: runner.run([t, "--version"], capture=True) for t in ("clang", "clang++", "make")}
    versions["ar"] = sha_file(shutil.which("ar", path=runner.env["PATH"]))
    m = re.search(r"clang version (\d+)", versions["clang"])
    if not m or int(m[1]) < 14:
        raise SetupError("Apple clang 14 or newer is required.", "Install or update Apple developer tools.")
    sdk = runner.run(["xcrun", "--sdk", "macosx", "--show-sdk-path"], capture=True).strip()
    sdk_version = runner.run(["xcrun", "--sdk", "macosx", "--show-sdk-version"], capture=True).strip()
    if not Path(sdk).is_dir() or not re.match(r"\d+(\.\d+)*$", sdk_version):
        raise SetupError("The macOS SDK is unavailable.", "Install or update Apple developer tools.")
    if int(sdk_version.split(".")[0]) < 14:
        raise SetupError("The macOS SDK is too old.", "Install or update Apple developer tools (macOS 14 SDK or newer).")
    packages = runner.run([sys.executable, "-I", "-c",
        "import capstone,numpy,PIL,json; print(json.dumps({k:v for k,v in [('capstone',capstone.__version__),('numpy',numpy.__version__),('Pillow',PIL.__version__)]}))"], capture=True).strip()
    try:
        package_versions = json.loads(packages)
    except ValueError:
        raise SetupError("Setup's Python packages are incomplete.", "Download Setup again.") from None
    probe = work / "toolchain"
    remove(probe)
    probe.mkdir()
    (probe / "test.c").write_text("int main(void) { return 0; }\n")
    (probe / "test.mm").write_text("#import <Cocoa/Cocoa.h>\n#import <Metal/Metal.h>\nint main() { return 0; }\n")
    try:
        runner.run(["clang", "-arch", "arm64", "-isysroot", sdk] + LLVM_FLAGS + [probe / "test.c", "-o", probe / "test"])
        runner.run(["clang", "-arch", "arm64", "-isysroot", sdk, "-c", probe / "test.c", "-o", probe / "test.o"])
        runner.run(["ar", "rcs", probe / "test.a", probe / "test.o"])
        runner.run(["clang++", "-arch", "arm64", "-isysroot", sdk, "-std=c++17", "-fobjc-arc", probe / "test.mm",
                    "-framework", "Cocoa", "-framework", "Metal", "-o", probe / "test-cxx"])
        # No `metal` probe: the Command Line Tools have no Metal compiler and the build doesn't need one
        # (host/shaders/build.sh lib stops before `xcrun metal`; the game compiles its shaders at run time).
        for name in ("test", "test.a", "test-cxx"):
            if not (probe / name).is_file() or not (probe / name).stat().st_size:
                raise SetupError("Developer tools did not produce the capability probe.")
    except SetupError as e:
        raise SetupError("Developer tools cannot compile the required C/C++/Objective-C code or LLVM flag.",
                         "Install or update Apple developer tools; see the log for the failed capability.") from e
    return dict(versions=versions, sdk=sdk, sdk_version=sdk_version, architecture=platform.machine(),
                python=sys.version, packages=package_versions, llvm_flags=LLVM_FLAGS,
                developer_tools={t: sha_file(shutil.which(t, path=runner.env["PATH"])) for t in ("clang", "clang++", "xcrun")},
                tools={t: shutil.which(t, path=runner.env["PATH"]) for t in required})


def case_child(root, name):
    matches = [p for p in root.iterdir() if p.name.casefold() == name.casefold()]
    if len(matches) != 1:
        raise SetupError(f"The PC folder is missing {name}, or has conflicting case variants.", "Choose the installed folder containing speed.exe, GLOBAL, TRACKS, CARS, FRONTEND and SOUND.")
    return matches[0]


def validate_pc(root, source_root=ROOT):
    if not root.is_dir():
        msg = "Not available yet; choose an installed PC game folder" if root.suffix.lower() in (".iso", ".dmg") else "Choose the installed folder containing speed.exe."
        raise SetupError(msg, "Choose the installed PC v1.3 game folder.")
    exe = case_child(root, "speed.exe")
    if not exe.is_file():
        raise SetupError("speed.exe is not a file.", "Choose an installed PC v1.3 game folder.")
    for name in ("GLOBAL", "TRACKS", "CARS", "FRONTEND", "SOUND"):
        if not case_child(root, name).is_dir():
            raise SetupError(f"The PC folder has no {name} directory.", "Install the complete PC game, then select its folder.")
    spec = importlib.util.spec_from_file_location("_setup_exe_fingerprint", source_root / "recomp/exe_fingerprint.py")
    fp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fp)
    try:
        fingerprint = fp.check(exe.read_bytes(), str(exe))
    except SystemExit as e:
        raise SetupError(str(e), "Choose the supported PC v1.3 executable; see the differing code region in the log.") from None
    return exe, fingerprint


ZIP_MAX_BYTES = 8 * 1024**3   # a full game folder unpacks to about 2.8 GB
ZIP_MAX_FILES = 20000         # it has about 1,500 entries
PC_DIRS = ("GLOBAL", "TRACKS", "CARS", "FRONTEND", "SOUND")


def zip_members(archive):
    """Checked entries of a PC game .zip; refuses anything that could write outside the extraction folder."""
    members, total = [], 0
    for info in archive.infolist():
        name = info.filename
        if name.startswith("__MACOSX/") or Path(name).name.startswith("._"):
            continue  # Finder's resource-fork copies, not game files
        parts = name.replace("\\", "/").split("/")
        if (name.startswith(("/", "\\")) or re.match(r"[A-Za-z]:", name) or ".." in parts or "\0" in name
                or any(c in name for c in "\r\n")):
            raise SetupError(f"The .zip contains an unsafe path: {name!r}", "Use the original game download, or unzip it and choose the folder.")
        mode = info.external_attr >> 16
        if (mode & 0o170000) not in (0, 0o100000, 0o040000):
            raise SetupError(f"The .zip contains a link or special file: {name!r}", "Use the original game download, or unzip it and choose the folder.")
        total += info.file_size
        members.append(info)
        if len(members) > ZIP_MAX_FILES or total > ZIP_MAX_BYTES:
            raise SetupError("The .zip is far larger than the game (more than 8 GB or 20,000 files).",
                             "Choose the Need for Speed Most Wanted game .zip or its unzipped folder.")
    return members, total


def find_pc_root(top):
    """The folder holding speed.exe and the game folders: top itself or one level down."""
    def is_root(d):
        names = {p.name.casefold() for p in d.iterdir()}
        return "speed.exe" in names and all(n.casefold() in names for n in PC_DIRS)
    candidates = [top] if is_root(top) else [d for d in sorted(top.iterdir()) if d.is_dir() and not d.is_symlink() and is_root(d)]
    if len(candidates) != 1:
        raise SetupError("The .zip doesn't contain one game folder with speed.exe, GLOBAL, TRACKS, CARS, FRONTEND and SOUND."
                         if not candidates else "The .zip contains more than one game folder.",
                         "Choose the Need for Speed Most Wanted (version 1.3) game .zip or folder.")
    return candidates[0]


def extract_pc_zip(source, work, events):
    """Unpack a PC game .zip (read only) into <work>/pc_src and return the game folder inside it."""
    out = work / "pc_src"
    stamp = work / "pc_src.json"
    st = source.stat()
    key = dict(path=str(source), size=st.st_size, mtime=st.st_mtime_ns)
    try:
        recorded = json.loads(stamp.read_text())
        if recorded["key"] == key:
            root = Path(recorded["root"])
            if root.is_relative_to(out) and digest_tree(root) == recorded["digest"]:
                events.emit("warn", id="pc", msg="Reusing the game unpacked earlier from the .zip.")
                return root
    except (OSError, ValueError, KeyError, TypeError, InstallError):
        pass
    stamp.unlink(missing_ok=True)
    remove(out)
    try:
        archive = zipfile.ZipFile(source)
    except (zipfile.BadZipFile, OSError) as e:
        raise SetupError(f"The .zip can't be read: {e}", "Download the game .zip again, or unzip it and choose the folder.") from None
    try:
        return _extract(archive, out, stamp, key, work, events)
    except BaseException:
        with ignored_cancellation():
            remove(out)  # never leave half a game behind: a later run must not see a partial pc_src
        raise


def _extract(archive, out, stamp, key, work, events):
    with archive:
        members, total = zip_members(archive)
        if shutil.disk_usage(work).free < total + 512 * 1024**2:
            raise SetupError("There is not enough free disk space to unpack the .zip.", "Free space on the work volume, then try again.")
        out.mkdir(parents=True)
        base = out.resolve()
        written = 0
        for i, info in enumerate(members):
            target = (out / info.filename).resolve()
            if target != base and base not in target.parents:
                raise SetupError(f"The .zip contains an unsafe path: {info.filename!r}", "Use the original game download.")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with archive.open(info) as src, open(target, "xb") as dst:
                    for block in iter(lambda: src.read(1024 * 1024), b""):
                        written += len(block)
                        if written > total or written > ZIP_MAX_BYTES:  # sizes in the directory were false
                            raise SetupError("The .zip unpacks to more than it declares.", "Download the game .zip again.")
                        dst.write(block)
            except (zipfile.BadZipFile, zlib_error(), EOFError, NotImplementedError) as e:
                raise SetupError(f"The .zip is damaged or uses an unsupported format: {e}",
                                 "Download the game .zip again, or unzip it and choose the folder.") from None
            except FileExistsError:
                raise SetupError(f"The .zip contains the same file twice: {info.filename!r}", "Use the original game download.") from None
            if i % 50 == 0:
                events.progress(i, len(members))
    root = find_pc_root(out)
    tmp = stamp.with_suffix(".tmp")
    tmp.write_text(json.dumps(dict(key=key, root=str(root), digest=digest_tree(root))))
    os.replace(tmp, stamp)  # the stamp appears only for a complete, verified unpack
    return root


def zlib_error():
    import zlib
    return zlib.error


def is_pc_zip(path):
    return path.is_file() and path.suffix.lower() == ".zip"


SOURCE_DIRS = ("recomp", "runtime", "host", "media", "tools", "tools_snapshot")
SOURCE_EXTS = {".py", ".c", ".h", ".cpp", ".m", ".mm", ".sh", ".metal", ".tsv", ".txt", ".json", ".swift", ".md", ".asm"}
IGNORED = {"out", "build", "gen", "__pycache__", ".venv", ".git", "testdata", "testdata_synth"}


def source_inventory(root):
    result = {}
    for name in SOURCE_DIRS:
        directory = root / name
        if not directory.is_dir():
            raise SetupError(f"Setup source is missing {name}.", "Download Setup again.")
        for base, dirs, files in os.walk(directory):
            dirs[:] = sorted(d for d in dirs if d not in IGNORED and not d.startswith("."))
            for name in sorted(files):
                p = Path(base) / name
                if p.suffix in SOURCE_EXTS and not p.name.endswith(".log"):
                    if p.is_symlink():
                        raise SetupError(f"Setup source contains a symbolic link: {p}", "Download Setup again.")
                    result[str(p.relative_to(root))] = sha_file(p)
    result["setup.sh"] = sha_file(root / "setup.sh")
    return result


def dependency_identity(root, toolchain):
    tarball = root / "third_party/dl/ffmpeg-7.1.1.tar.xz"
    if not tarball.is_file():
        raise SetupError("The pinned FFmpeg source is missing.", "Download Setup again.")
    if sha_file(tarball) != FFMPEG_SHA:
        raise SetupError("Dependency checksum failed: FFmpeg 7.1.1.", "Download Setup again.")
    lock = root / "tools/installer/deps.lock.json"
    return {"ffmpeg": FFMPEG_SHA, "lock": sha_file(lock) if lock.exists() else "embedded-ffmpeg-pin-v1",
            "packages": toolchain["packages"]}


def cache_key(source, dependencies, toolchain, pc_hash, x360_hash):
    return identity(dict(schema=1, source=source, dependencies=dependencies, toolchain=toolchain,
                         player=dict(pc=pc_hash, x360=x360_hash), public=True, cflags="recomp defaults; runtime -O1"))


class Cache:
    def __init__(self, directory, events):
        self.directory, self.events = directory, events
        directory.mkdir(parents=True, exist_ok=True)

    def run(self, name, outputs, action):
        stamp = self.directory / (name + ".json")
        try:
            expected = json.loads(stamp.read_text())
            actual = {str(p): digest_tree(p) for p in outputs if p.exists()}
            valid = len(actual) == len(outputs) and expected == actual
        except (OSError, ValueError, InstallError):
            valid = False
        if valid:
            self.events.emit("warn", id=self.events.current, msg=f"Resuming verified {name} output.")
            return
        stamp.unlink(missing_ok=True)
        for output in outputs:
            remove(output)
        action()
        if any(not p.exists() or (p.is_file() and not p.stat().st_size) for p in outputs):
            raise SetupError(f"{name} completed without all required outputs.")
        records = {str(p): digest_tree(p) for p in outputs}
        temporary = stamp.with_suffix(".tmp")
        temporary.write_text(json.dumps(records, sort_keys=True))
        os.replace(temporary, stamp)


def copy_source(root, target, inventory):
    marker = target.parent / "source.json"
    if marker.exists():
        try:
            recorded = json.loads(marker.read_text())
            if (recorded == inventory and all(sha_file(target / p) == h for p, h in inventory.items()) and
                    sha_file(target / "third_party/dl/ffmpeg-7.1.1.tar.xz") == sha_file(root / "third_party/dl/ffmpeg-7.1.1.tar.xz")):
                return
        except (OSError, ValueError):
            pass
    remove(target)
    for relative, expected in inventory.items():
        dst = target / relative
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / relative, dst)
        if sha_file(dst) != expected:
            raise SetupError("Setup source changed while it was being copied.", "Close other build processes and try again.")
    archive = target / "third_party/dl/ffmpeg-7.1.1.tar.xz"
    archive.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(root / "third_party/dl/ffmpeg-7.1.1.tar.xz", archive)
    marker.write_text(json.dumps(inventory, sort_keys=True))


def write_roots(path, roots):
    allowed = {"pc_root", "x360_root", "assets_root", "movies", "caches", "tmp"}
    if "pc_root" not in roots or set(roots) - allowed:
        raise SetupError("Prepared assets have an invalid roots manifest.")
    for key, value in roots.items():
        clean_path(value)
        if not Path(value).is_dir():
            raise SetupError(f"Prepared root {key} is missing: {value}")
    path.write_text("".join(f"{k}={v}\n" for k, v in sorted(roots.items())))


@contextmanager
def module_environment(env):
    original = dict(os.environ)
    os.environ.clear()
    os.environ.update(env)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(original)


def prepare_x360(source, out, pc, events, env, record=None):
    if source is None:
        events.emit("features", x360=False, missing=X360_MISSING)
        return {}
    if not source.is_dir() and not (source.is_file() and source.suffix.lower() == ".iso"):
        raise SetupError("Choose an Xbox 360 game folder or ISO.", "Choose your own complete Xbox 360 copy, or continue without Xbox 360 features.")
    try:
        module = importlib.import_module("setup_kit.x360")
    except ModuleNotFoundError as e:
        if e.name != "setup_kit.x360":
            raise SetupError("The Xbox 360 importer is incomplete.", "Download Setup again.") from e
        events.emit("warn", id="x360", msg="Xbox 360 support is unavailable in this Setup build; continuing with PC features.")
        events.emit("features", x360=False, missing=X360_MISSING)
        return {}
    def progress(*args, **kwargs):
        # B's callback may supply (done,total), (label,done,total), a message,
        # or named done/total. Keep every emitted event inside the shared schema.
        numbers = [a for a in args if isinstance(a, (int, float))]
        done, total = kwargs.get("done"), kwargs.get("total")
        if len(numbers) >= 2:
            done, total = numbers[-2:]
        if done is not None and total is not None:
            events.progress(int(done), int(total))
        else:
            events.log.write(("x360: " + str(args or kwargs) + "\n").encode())
    try:
        with module_environment(env):
            result = module.prepare(source, out, pc, progress)
    except Exception as e:
        if isinstance(e, Cancelled):
            raise
        raise SetupError(str(e), getattr(e, "fix", "Check your Xbox 360 copy, or choose PC-only setup.")) from e
    roots = {k: clean_path(v) for k, v in result.roots.items()}
    for key, path in roots.items():
        if not path.is_relative_to(out.resolve()):
            raise SetupError(f"The Xbox 360 importer returned a root outside its output: {key}.")
    events.emit("features", x360=bool(result.features), missing=list(result.missing))
    if record is not None:
        record.update(features=bool(result.features), missing=list(result.missing))
    return roots


CHECK_X360 = "check-x360"


def x360_stat(source):
    """Path, size, modification and change time (ctime cannot be set back) and inode of every file of the X360 copy (a cheap change detector)."""
    def stat(p):
        st = p.stat()
        return [st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_ino]
    if source.is_dir():
        return {str(p.relative_to(source)): stat(p) for p in sorted(source.rglob("*")) if p.is_file()}
    return {"": stat(source)}


def x360_identity(source, pc_hash, inventory, content):
    """What a converted X360 copy depends on: the player's copy (its content digest, the one --build computes for
    its cache key, plus every file's path, size, modification time and inode), the PC game's content and this
    Setup's source."""
    files = x360_stat(source)
    return identity(dict(schema=2, x360=str(source), files=files, content=content, pc=pc_hash, source=identity(inventory)))


def record_check_x360(work, key, roots, record):
    """After a successful --check: remember what its converted copy is, so --build can reuse it instead of
    converting the same copy again (about 6 minutes and 5 GB, M4 F4)."""
    out = work / CHECK_X360
    relative = {k: str(Path(v).relative_to(out.resolve())) for k, v in roots.items()}
    stamp = work / (CHECK_X360 + ".json")
    tmp = stamp.with_suffix(".tmp")
    tmp.write_text(json.dumps(dict(key=key, roots=relative, digest=digest_tree(out), **record), sort_keys=True))
    os.replace(tmp, stamp)


def reuse_check_x360(work, key, target, events):
    """Move the check's converted copy to target when it was made from this exact X360 copy, PC game and Setup
    source and is unchanged since (content digest); returns its roots, or None (then the caller converts)."""
    out, stamp = work / CHECK_X360, work / (CHECK_X360 + ".json")
    try:
        recorded = json.loads(stamp.read_text())
        valid = (recorded["key"] == key and out.is_dir() and not out.is_symlink() and
                 digest_tree(out) == recorded["digest"])
        relative = recorded["roots"]
        features, missing = bool(recorded["features"]), list(recorded["missing"])
    except (OSError, ValueError, KeyError, TypeError, InstallError):
        valid = False
    stamp.unlink(missing_ok=True)  # used at most once: the copy moves into the build below
    if not valid:
        remove(out)
        return None
    os.rename(out, target)
    roots = {}
    for k, rel in relative.items():
        p = (target / rel).resolve()
        if not p.is_relative_to(target.resolve()) or not p.is_dir():
            raise SetupError(f"The checked Xbox 360 copy has an invalid root: {k}.")
        roots[k] = p
    events.emit("warn", id="x360", msg="Reusing the Xbox 360 files converted by the check.")
    events.emit("features", x360=features, missing=missing)
    return roots


def clean_work(paths, events, key=None):
    """After a successful install: the work folder's copies are not needed again (M4 F4: 13-16 GB). stage/ was
    copied into the install, check-x360/ was used or is stale, and pc_src/ (a .zip unpacked) is unpacked again
    if a later build needs it. Failures here never fail the install."""
    def log(msg):
        events.log.write(("cleanup: " + msg + "\n").encode())
    if not owned(paths.work, paths.state):
        log(f"{paths.work} is not Setup's own folder: nothing removed")
    else:
        for name in ("stage", CHECK_X360, CHECK_X360 + ".json", "pc_src", "pc_src.json"):
            try:
                remove(paths.work / name)
            except OSError as e:
                log(f"could not remove {paths.work / name}: {e}")
    # Build caches of earlier Setup versions or other games (each about 0.5-1 GB): only this build's is reused.
    builds = paths.cache / "builds"
    children = []
    if key and owned(paths.cache, paths.state) and real_dir(builds):
        try:
            children = list(builds.iterdir())
        except OSError:
            children = []
    elif key:
        log(f"{builds} is not inside Setup's own cache folder (or is a link): no build cache removed")
    for child in children:
        if child.name != key and re.fullmatch(r"[0-9a-f]{64}", child.name) and child.is_dir() and not child.is_symlink():
            shutil.rmtree(child, ignore_errors=True)


def verify_app(app, runner, version=PUBLIC_VERSION):
    try:
        with (app / "Contents/Info.plist").open("rb") as f:
            info = plistlib.load(f)
        executable = app / "Contents/MacOS" / info["CFBundleExecutable"]
        if (info["CFBundleIdentifier"] != PUBLIC_ID or info["CFBundleExecutable"] != PUBLIC_EXEC or info["CFBundleShortVersionString"] != version or
                not executable.is_file() or not executable.stat().st_size or not os.access(executable, os.X_OK)):
            raise ValueError("app identity or executable")
    except (OSError, ValueError, KeyError, plistlib.InvalidFileException) as e:
        raise SetupError("The staged app failed verification.", "Open the log and rebuild the app.") from e
    runner.run(["codesign", "--verify", "--deep", "--strict", app])


def _fs_ident(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_dev, st.st_ino)


def _name_key(name):
    # APFS/HFS+ default volumes ignore case and Unicode normalization. On a case-sensitive volume this only makes
    # the check refuse more, never less.
    return unicodedata.normalize("NFD", name).casefold()


def _anchor(path):
    """Identity of the deepest existing ancestor (symlinks resolved) and the comparison keys of the names below it."""
    path, tail = clean_path(path), []
    while True:
        found = _fs_ident(path)
        if found is not None or path.parent == path:
            return found, tail[::-1]
        tail.append(_name_key(path.name))
        path = path.parent


def within(inner, outer):
    """True when inner is outer or lies inside it, by filesystem identity rather than spelling: symlinks,
    case and normalization variants and firmlinked aliases of the same folder all count."""
    outer_id, outer_tail = _anchor(outer)
    below = []
    q = clean_path(inner)
    while True:
        found = _fs_ident(q)
        if found is not None and found == outer_id:
            rest = below[::-1]
            if rest[:len(outer_tail)] == outer_tail:
                return True
        if q.parent == q:
            return False
        below.append(_name_key(q.name))
        q = q.parent


def overlaps(a, b):
    return within(a, b) or within(b, a)


def check_locations(paths, pc, x360, dest, source):
    """Refuse any location choice that could make Setup write into, or delete, something it must only read.

    work and cache are recursively removed and populated (stage, pc_src extraction, build copies): they may not
    overlap, either way, any input, the source, the app names, the 4.0 data or the 3.x profile (its saves). data and
    logs may not lie inside any of those. The destination may not lie inside an input (or a .zip's folder), the
    profiles, the source, work or cache, nor be the
    folder holding a .zip; it may contain them (installing into a home folder is fine: only the app's
    own names directly in it are written). All comparisons are by filesystem identity (within()).
    """
    inputs = [pc] + ([x360] if x360 else [])
    apps = [dest / "NFSMW.app", dest / f"{PUBLIC_NAME}.app"]
    profiles = [paths.data, paths.legacy]
    protected = inputs + [source] + apps + profiles
    def refuse(msg, a):
        raise SetupError(msg.format(a), "Choose separate work, cache, game, save and destination folders.")
    for disposable in (paths.work, paths.cache):
        for keep in protected:
            if overlaps(disposable, keep):
                refuse("Setup work/cache overlaps a protected folder: {}", disposable)
    if overlaps(paths.work, paths.cache):
        refuse("Setup work and cache folders overlap: {}", paths.work)
    for writable in (paths.data, paths.logs, paths.state):
        for keep in inputs + [source] + apps + [paths.legacy]:
            if within(writable, keep):
                refuse("Setup data/logs would write into a protected folder: {}", writable)
    if overlaps(paths.data, paths.legacy):
        refuse("The 4.0 data folder overlaps the 3.x profile: {}", paths.data)
    for keep in inputs + profiles + [source, paths.work, paths.cache]:
        if within(dest, keep):
            refuse("The destination is inside a folder Setup must not write to: {}", dest)
    if pc.is_file() and within(dest, pc.parent) and within(pc.parent, dest):
        # Not beside the player's .zip either; a subfolder of the zip's folder (zip in ~, app in ~/Applications) is fine.
        refuse("The destination is the folder holding the game .zip: {}", dest)
    # The installer normally unpacks source below paths.state. That is allowed;
    # state itself is not recursively deleted or used as a build output root.


def check_space(paths, pc, dest):
    size = sum(p.stat().st_size for p in pc.rglob("*") if p.is_file())
    # Keep two copies until commit (staging and permanent); allow 4 GiB for
    # generated C, libraries and toolchain output. Check every relevant volume.
    for path, required in ((paths.work, size + 4 * 1024**3), (paths.cache, 4 * 1024**3),
                           (paths.data, size + 512 * 1024**2), (dest, 256 * 1024**2)):
        while not path.exists():
            path = path.parent
        if shutil.disk_usage(path).free < required:
            raise SetupError("There is not enough free disk space for Setup.", "Free space on the work and installation volumes, then try again.")


SAVE_DIR = "NFS Most Wanted"            # the game's own folder under SHGetFolderPath (4.0: <data>/NFS Most Wanted)
LEGACY_SAVES = Path("Documents") / SAVE_DIR  # 3.x mapped CSIDL_PERSONAL to <profile>/Documents
MIGRATED_MSG = "Your 3.x career was copied into 4.0; the 3.x copy is untouched."


def _save_crc_table():
    table = []
    for i in range(256):
        c = i << 24
        for _ in range(8):
            c = ((c << 1) ^ 0x04C11DB7) & 0xFFFFFFFF if c & 0x80000000 else (c << 1) & 0xFFFFFFFF
        table.append(c)
    return table


SAVE_CRC = _save_crc_table()


def save_header_ok(data):
    """The game's own load check (Realmc MemcardInterfaceImpl, 007f55xx): a 0x1c-byte header with magic '20CM',
    the file length at +4 and CalcSignature (007f73e1, MSB-first CRC-32) of bytes 0..0x17 at +0x18."""
    if len(data) < 0x1C or data[:4] != b"20CM" or int.from_bytes(data[4:8], "little") != len(data):
        return False
    u = ~int.from_bytes(data[:4], "big") & 0xFFFFFFFF
    for x in data[4:0x18]:
        u = (((u << 8) | x) & 0xFFFFFFFF) ^ SAVE_CRC[u >> 24]
    return (~u & 0xFFFFFFFF) == int.from_bytes(data[0x18:0x1C], "little")


class LegacySaveError(Exception):
    pass


def legacy_saves(folder):
    """Every file of a 3.x save folder (relative path -> bytes), or LegacySaveError. Each alias folder must hold
    its save file (same name) with a valid game header; nothing but folders and regular files is accepted."""
    files, aliases = {}, []
    if folder.is_symlink() or not folder.is_dir():
        raise LegacySaveError(f"{folder} is not a folder")
    for base, dirs, names in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for name in sorted(dirs + [n for n in names if not n.startswith(".")]):
            p = Path(base) / name
            if p.is_symlink() or not (p.is_dir() or p.is_file()):
                raise LegacySaveError(f"{p} is not a regular file or folder")
            if p.is_file():
                files[p.relative_to(folder)] = p.read_bytes()
    for entry in sorted(folder.iterdir()):
        if entry.name.startswith("."):
            continue
        if not entry.is_dir():
            raise LegacySaveError(f"unexpected file {entry.name} beside the career folders")
        save = entry / entry.name
        if not save.is_file():
            raise LegacySaveError(f"career {entry.name} has no save file")
        if not save_header_ok(files[save.relative_to(folder)]):
            raise LegacySaveError(f"the save of career {entry.name} is damaged or incomplete")
        aliases.append(entry.name)
    return files, aliases


def migrate_3x_saves(paths, events):
    """Copy 3.x career saves into a 4.0 profile that has none. Never moves, changes or deletes the 3.x files and
    never writes over 4.0 saves; settings are not carried over. Returns the copied careers (empty when skipped)."""
    def log(msg):
        events.log.write(("saves: " + msg + "\n").encode())
        events.log.flush()
    source, target = paths.legacy / LEGACY_SAVES, paths.data / SAVE_DIR
    if not source.exists() and not source.is_symlink():
        log(f"no 3.x saves at {source}")
        return []
    if target.exists() or target.is_symlink():
        if target.is_symlink() or not target.is_dir() or any(not p.name.startswith(".") for p in target.iterdir()):
            log(f"4.0 already has saves at {target}; 3.x saves not copied")
            return []
    try:
        files, aliases = legacy_saves(source)
    except (LegacySaveError, OSError) as e:
        log(f"3.x saves not copied: {e}")
        events.emit("warn", id=events.current, msg=f"Your 3.x saves were not copied into 4.0 ({e}). The 3.x files were "
                    f"not changed: they are in {source}. Start a new career, or copy a career folder into {target} "
                    "by hand while the game is closed.")
        return []
    if not aliases:
        log(f"3.x save folder {source} has no careers")
        return []
    staging = paths.data / f".{SAVE_DIR}.from-3x-{uuid.uuid4().hex}"
    try:
        for relative, data in files.items():
            (staging / relative).parent.mkdir(parents=True, exist_ok=True)
            write_synced(staging / relative, data)
        if digest_tree(staging) != digest_tree(source):
            raise LegacySaveError("the copy differs from the 3.x saves")
        if target.is_dir():
            target.rmdir()  # only an empty folder (checked above): rmdir refuses anything else
        os.rename(staging, target)
    except (LegacySaveError, OSError) as e:
        remove(staging)
        log(f"3.x saves not copied: {e}")
        events.emit("warn", id=events.current, msg=f"Your 3.x saves could not be copied into 4.0 ({e}). The 3.x files "
                    f"were not changed: they are in {source}.")
        return []
    log(f"copied 3.x careers {', '.join(aliases)} from {source} to {target}; the 3.x files were not changed")
    events.emit("warn", id=events.current, msg=MIGRATED_MSG)
    return aliases


def build(args, paths, events, runner, source_root=ROOT):
    pc, dest = clean_path(args.pc), clean_path(args.dest)
    x360 = clean_path(args.x360) if args.x360 else None
    check_locations(paths, pc, x360, dest, source_root)
    def setup_log(msg):
        events.log.write(("install: " + msg + "\n").encode())
        events.log.flush()
    if args.build:
        # A previous install that lost power or was killed mid-way: restore the previous app/roots/preferences (or
        # finish a committed one) before anything else (even before the work folder is judged: recovery needs only
        # the profile and destination), so a failure later in this run still leaves a whole install.
        outcome = recover_interrupted(paths.data, log=setup_log)
        if outcome:
            events.emit("warn", id="preflight", msg="Setup found an interrupted install and " +
                        ("restored your previous version." if outcome == "restored" else "finished it."))
    claim(paths.work, paths.work == paths.default_work, SETUP_WORK_NAMES, "work", paths.state)
    events.stage("preflight")
    if paths.cache_moved:
        setup_log(f"cache {paths.cache_moved} has shell characters; using {paths.cache}")
    toolchain = preflight(runner, paths.work)
    dependencies = dependency_identity(source_root, toolchain) if args.build else None
    events.progress()
    events.stage("pc")
    if is_pc_zip(pc):  # a .zip of the game folder: only read
        pc = extract_pc_zip(pc, paths.work, events)
    exe, fingerprint = validate_pc(pc, source_root)
    pc_hash = digest_tree(pc)
    events.progress()
    events.stage("x360")
    stage = paths.work / "stage"
    if args.check:
        check_out = paths.work / CHECK_X360
        if x360 is None:  # the app checks the PC game on its own too: keep an earlier X360 check for the build
            prepare_x360(None, check_out, pc, events, runner.env)
            events.progress()
            return None
        (paths.work / (CHECK_X360 + ".json")).unlink(missing_ok=True)
        remove(check_out)
        record = {}
        before = x360_stat(x360)
        content = digest_tree(x360)
        roots = prepare_x360(x360, check_out, pc, events, runner.env, record)
        if roots and x360_stat(x360) == before and digest_tree(x360) == content:  # changed while it was converted: not reused by the build
            record_check_x360(paths.work, x360_identity(x360, pc_hash, source_inventory(source_root), content), roots, record)
        events.progress()
        return None
    check_space(paths, pc, dest)
    x360_before = x360_stat(x360) if x360 else None
    x360_hash = digest_tree(x360) if x360 else None
    inventory = source_inventory(source_root)
    key = cache_key(inventory, dependencies, toolchain, pc_hash, x360_hash)
    cache_home = paths.cache / "builds" / key
    claim(paths.cache, paths.cache == paths.default_cache, SETUP_CACHE_NAMES, "cache", paths.state)
    with exclusive(paths.cache / "build.lock"):
        source = cache_home / "source"
        source.parent.mkdir(parents=True, exist_ok=True)
        copy_source(source_root, source, inventory)
        # Reload optional importer and its relative imports from the build copy;
        # its __file__-relative HUFF/generator paths must never use owner output.
        import setup_kit
        setup_kit.__path__ = [str(source / "tools/setup_kit")]
        for name in list(sys.modules):
            if name.startswith("setup_kit.") and name not in ("setup_kit.backend", "setup_kit.install"):
                del sys.modules[name]
        remove(stage)
        staged_data = stage / "data"
        pc_copy = staged_data / "pc"
        pc_copy.parent.mkdir(parents=True)
        shutil.copytree(pc, pc_copy)
        if digest_tree(pc_copy) != pc_hash:
            raise SetupError("The PC game changed while it was being copied.", "Close other installers or file-copy operations and try again.")
        # Windows names are case insensitive; gen_tables.py uses these spellings.
        for canonical in ("speed.exe", "GLOBAL", "TRACKS", "CARS", "FRONTEND", "SOUND"):
            actual = case_child(pc_copy, canonical)
            if actual.name != canonical:
                temporary = actual.with_name(".setup-case-" + uuid.uuid4().hex)
                actual.rename(temporary)
                temporary.rename(pc_copy / canonical)
        roots = {"pc_root": pc_copy}
        reused = reuse_check_x360(paths.work, x360_identity(x360, pc_hash, inventory, x360_hash), staged_data / "x360",
                                  events) if x360 else None
        roots.update(reused if reused is not None else prepare_x360(x360, staged_data / "x360", pc_copy, events, runner.env))
        if x360 and reused is None and (x360_stat(x360) != x360_before or digest_tree(x360) != x360_hash):
            # The cache key names the bytes digested before the conversion: those must be the bytes converted.
            raise SetupError("Your Xbox 360 copy changed while Setup was converting it.", "Leave it unchanged and run Setup again.")
        manifest = stage / "roots.txt"
        write_roots(manifest, roots)
        events.progress()
        cache = Cache(cache_home / "stamps", events)
        py = Path(sys.executable).absolute()
        def command(argv):
            return lambda: runner.run(argv, cwd=source)
        def cached(name, relative, argv):
            cache.run(name, [source / p for p in relative], command(argv))
        events.stage("recompile")
        cached("recompile", ["recomp/out", "recomp/out/speed.a"], [py, "-I", "recomp/build_all.py", "--exe", pc_copy / "speed.exe"])
        if not (source / "recomp/out/speed.a").is_file():
            raise SetupError("Recompilation produced no speed.a; stale archives are not accepted.")
        events.progress()
        events.stage("tables")
        table_names = ("attrib_x360", "x360audio_attrib", "attrib_light_data", "trees_params", "controltype_buttons", "auxlights_data", "glass_uv1")
        cached("tables", ["runtime/gen"] + [f"runtime/gen/{name}.inc" for name in table_names], [py, "-I", "tools/gen_tables.py", "--pc", pc_copy] +
               (["--x360", roots["x360_root"]] if "x360_root" in roots else ["--no-x360"]))
        events.progress()
        events.stage("audio")
        # FFmpeg is built here (media.cpp needs its headers) but reported as itself: an FFmpeg failure is an "ffmpeg"
        # error ("Building the video decoder"), not an audio one. No stage event: the contract's order is unchanged.
        events.current = "ffmpeg"
        cached("ffmpeg", ["third_party/ffmpeg"], ["sh", "media/build_ffmpeg.sh"])
        events.current = "audio"
        cached("audio", ["host/audio/build/libhostaudio.a", "host/audio/build/libmedia.a"], ["sh", "host/audio/build.sh"])
        events.progress()
        events.stage("shaders")
        cached("shaders", ["host/shaders/build/libsmtranslate.a"], ["sh", "host/shaders/build.sh", "lib"])
        events.progress()
        events.stage("ffmpeg")
        for library in ("avformat", "avcodec", "swscale", "swresample", "avutil"):
            p = source / f"third_party/ffmpeg/lib/lib{library}.a"
            if not p.is_file() or not p.stat().st_size:
                raise SetupError(f"FFmpeg library {library} is missing.")
        events.progress()
        events.stage("runtime")
        cached("runtime", ["runtime/boot"], ["sh", "runtime/build.sh"])
        events.progress()
        events.stage("assets")
        write_roots(manifest, roots)
        digest_tree(staged_data)  # Refuse escaping symlinks or partial/unreadable outputs.
        events.progress()
        events.stage("bundle")
        runner.run(["sh", "runtime/bundle.sh", stage, "--name", PUBLIC_NAME, "--exec-name", PUBLIC_EXEC, "--bundle-id", PUBLIC_ID,
                    "--version", PUBLIC_VERSION, "--build-version", PUBLIC_VERSION, "--source-id", identity(inventory)], cwd=source)
        app = stage / f"{PUBLIC_NAME}.app"
        verify_app(app, runner)
        events.progress()
        events.stage("install")
        if x360 and x360_stat(x360) != x360_before:
            raise SetupError("Your Xbox 360 copy changed while Setup was reading it.", "Leave it unchanged and run Setup again.")
        installed = transactional_install(app, staged_data, roots, paths.data, dest, lambda p: verify_app(p, runner), log=setup_log,
                                          on_commit=lambda path: setattr(events, "installed", path),
                                          after_commit=lambda: migrate_3x_saves(paths, events), move_data=True)
        clean_work(paths, events, key)
        events.progress()
        return installed


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build and install NFS Most Wanted Native from your own PC v1.3 game. Offline; no game files are supplied.")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--build", action="store_true")
    ap.add_argument("--pc", required=True, help="installed PC game folder, or a .zip of it (unpacked into --work; disc images are not supported)")
    ap.add_argument("--x360", help="your Xbox 360 game folder or ISO")
    ap.add_argument("--dest", required=True, help="directory that receives NFS Most Wanted Native.app")
    ap.add_argument("--work", required=True, help="disposable build/staging directory")
    ap.add_argument("--events", action="store_true", help="JSON-line events on stdout; full output in the run log")
    args = ap.parse_args(argv)
    paths = None
    events = None
    log_path = None
    try:
        paths = Paths(args.work)
        # Validate before creating anything at a user-selected path.
        check_locations(paths, clean_path(args.pc), clean_path(args.x360) if args.x360 else None,
                        clean_path(args.dest), ROOT)
        # Setup's own work folder (new, empty or marked), before anything is written into it.
        recovered = []
        if args.build:  # an interrupted install is resolved before the work folder is judged (build() finds nothing left)
            outcome = recover_interrupted(paths.data, log=recovered.append)
            if outcome:
                recovered.append("outcome " + outcome)
        claim(paths.work, paths.work == paths.default_work, SETUP_WORK_NAMES, "work", paths.state)
        (paths.work / "tool-home").mkdir(exist_ok=True)
        log_dir = paths.work / "logs" if args.check else paths.logs
        log_dir.mkdir(parents=True, exist_ok=True)
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8]
        log_path = log_dir / (run_id + ".log")
        with log_path.open("xb", buffering=0) as log:
            events = Events(args.events, log)
            for note in recovered:
                if note.startswith("outcome "):
                    events.emit("warn", id="preflight", msg="Setup found an interrupted install and " +
                                ("restored your previous version." if note.endswith("restored") else "finished it."))
                else:
                    log.write(("install: " + note + "\n").encode())
            # No protocol event is added just for the log path: errors always
            # carry it, and the UI may discover the current run's log in --work.
            (paths.work / "run-log.txt").write_text(str(log_path) + "\n")
            with cancellation(), exclusive(paths.work / ".setup.lock"), tool_tmp(paths.work) as tmp:
                env = child_environment(paths.work / "stage/roots.txt", paths.work, tmp=tmp)
                runner = Runner(log, env, paths.work)
                try:
                    installed = build(args, paths, events, runner)
                except (Exception, KeyboardInterrupt) as e:
                    if events.installed is not None:
                        with ignored_cancellation():
                            events.emit("done", app=str(events.installed))
                        return 0
                    try:
                        log.write(traceback.format_exc().encode())
                    except OSError:
                        pass
                    if isinstance(e, OSError):
                        fix = "Free disk space, then try Setup again." if e.errno == errno.ENOSPC else (
                            "Choose a writable destination or ask an administrator to install the staged app." if e.errno in (errno.EACCES, errno.EPERM)
                            else "Reconnect the source volume, check folder access and open the log.")
                    else:
                        fix = getattr(e, "fix", "Run Setup again.")
                    events.emit("error", id=events.current, msg=str(e) or "Setup was interrupted.", fix=fix, log=str(log_path))
                    return 130 if isinstance(e, (Cancelled, KeyboardInterrupt)) else 1
                if installed is not None:
                    with ignored_cancellation():
                        events.emit("done", app=str(installed))
                return 0
    except (Exception, KeyboardInterrupt) as e:
        # Includes failures before a log can be opened. stdout remains parseable.
        obj = dict(ev="error", id=events.current if events else "preflight", msg=str(e) or "Setup was interrupted.", fix=getattr(e, "fix", "Choose a writable work directory and try again."), log=str(log_path or ""))
        print(json.dumps(obj) if args.events else obj["msg"], flush=True)
        return 130 if isinstance(e, (Cancelled, KeyboardInterrupt)) else 1


if __name__ == "__main__":
    sys.exit(main())
