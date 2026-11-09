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

from .install import (PUBLIC_BUNDLE_ID, InstallError, OWNER_MARK, OWN_DIR, digest_tree, mark_owned, owned, real_dir,
                      recover_interrupted, remove, transactional_install, write_synced)
from . import pc_disc, rtpatch, prebuilt
from . import car_sources, car_catalog
from . import stage as staging
from .x360_cache import PreparedCache, preparation_key
from . import x360_extras

ROOT = Path(__file__).resolve().parents[2]
FFMPEG_SHA = "733984395e0dbbe5c046abda2dc49a5544e7e0e1e2366bba849222ae9e3a03b1"
PUBLIC_NAME = "NFS Most Wanted Native"
PUBLIC_ID = PUBLIC_BUNDLE_ID  # also the 3.x id: the Dock icon carries over. Never local.nfsmw-port.
PUBLIC_EXEC = "NFSMostWanted"  # never NFSMW: a distinct name, so this app is never mistaken for another build
PUBLIC_VERSION = "4.5.0"
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


# (pc_src.tmp and check-x360.tmp are the stamps' temporary files: an interrupted run leaves them; car-selection.json is
# the extras page's ticked cars, written before the first PC check can mark the folder.)
SETUP_WORK_NAMES = {"run-log.txt", "tmp", "tool-home", "toolchain", "logs", "stage", "check-x360", "check-x360.json",
                    "check-x360.tmp", "pc_src", "pc_src.json", "pc_src.tmp", ".setup.lock", "extras", "car-selection.json",
                    OWNER_MARK}
SETUP_CACHE_NAMES = {"builds", "stages", "build.lock", "x360", "cars", OWNER_MARK}


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
        self.ready = None
        self.completed = False

    def report_committed(self):
        if self.completed:
            return True
        if self.ready is None and self.installed is None:
            return False
        with ignored_cancellation():
            if self.ready is not None:
                self.emit("ready", descriptor=str(self.ready))
            else:
                self.emit("done", app=str(self.installed))
        return True

    def emit(self, ev, **fields):
        obj = dict(ev=ev, **fields)
        line = json.dumps(obj, ensure_ascii=True, separators=(",", ":"))
        try:
            self.log.write((line + "\n").encode())
            self.log.flush()
        except OSError:
            if ev not in ("done", "ready", "cars-result"):
                raise
        try:
            if self.enabled:
                print(line, file=self.stream, flush=True)
            elif ev in ("stage", "error", "warn", "done", "ready", "cars-result"):
                message = (fields.get("title") or fields.get("msg") or fields.get("app") or fields.get("descriptor")
                           or ("Extras " + fields.get("operation", "") + " completed."))
                print(message, file=self.stream, flush=True)
        except OSError:
            if ev not in ("done", "ready", "cars-result"):
                raise
        if ev in ("done", "ready"):
            self.completed = True

    def stage(self, name):
        self.current = name
        self.emit("stage", id=name, title="Preparing the update" if name == "stage" else dict(STAGES)[name])

    def progress(self, done=1, total=1):
        self.emit("progress", id=self.current, done=done, total=total)


class Runner:
    """Log every byte; own each child process group, including grandchildren."""
    def __init__(self, log, env, work):
        self.log, self.env, self.work = log, env, work
        self.log.write(("tmp: " + self.env.get("TMPDIR", "") + "\n").encode())
        self.log.flush()

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
        "import capstone,numpy,PIL,json; from importlib.metadata import version; print(json.dumps({k:v for k,v in [('capstone',version('capstone')),('numpy',numpy.__version__),('Pillow',PIL.__version__)]}))"], capture=True).strip()
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


def exe_fingerprint(data, path, source_root=ROOT):
    """The code fingerprint of a speed.exe (recomp/exe_fingerprint.py), or SetupError naming the regions that differ."""
    spec = importlib.util.spec_from_file_location("_setup_exe_fingerprint", source_root / "recomp/exe_fingerprint.py")
    fp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fp)
    try:
        return fp.check(data, str(path))
    except SystemExit as e:
        raise SetupError(str(e), "Choose the supported PC v1.3 executable; see the differing code region in the log.") from None


def validate_pc(root, source_root=ROOT):
    if not root.is_dir():
        msg = ("Setup doesn't read .dmg images: open it in Finder and choose the game folder or disc inside it."
               if root.suffix.lower() == ".dmg" else "Choose the installed folder containing speed.exe.")
        raise SetupError(msg, "Choose the installed PC v1.3 game folder.")
    exe = case_child(root, "speed.exe")
    if not exe.is_file():
        raise SetupError("speed.exe is not a file.", "Choose an installed PC v1.3 game folder.")
    for name in ("GLOBAL", "TRACKS", "CARS", "FRONTEND", "SOUND"):
        if not case_child(root, name).is_dir():
            raise SetupError(f"The PC folder has no {name} directory.", "Install the complete PC game, then select its folder.")
    return exe, exe_fingerprint(exe.read_bytes(), exe, source_root)


def movie_note(root):
    """A player-facing note when the game folder has no usable movies (MOVIES with a non-empty .vp6), else None.
    Disc sets without them are refused while they are unpacked; an installed folder or its .zip is used as it is."""
    folders = [p for p in root.iterdir() if p.name.casefold() == "movies" and p.is_dir() and not p.is_symlink()]
    if any(p.is_file() and not p.is_symlink() and p.suffix.casefold() == ".vp6" and p.stat().st_size > 0
           for folder in folders for p in folder.iterdir()):
        return None
    return ("Your game folder has " + ("an empty MOVIES folder" if folders else "no MOVIES folder") +
            ", so the game's intro and story movies won't play. To have them, copy the MOVIES folder from your game"
            " disc into the game folder, then run Setup again.")


ZIP_MAX_BYTES = 8 * 1024**3   # a full game folder unpacks to about 2.8 GB
ZIP_MAX_FILES = 20000         # it has about 1,500 entries
PC_DIRS = ("GLOBAL", "TRACKS", "CARS", "FRONTEND", "SOUND")


def zip_members(archive, label="The .zip", fix=None):
    """Checked entries of a PC game .zip; refuses anything that could write outside the extraction folder."""
    members, total = [], 0
    for info in archive.infolist():
        name = info.filename
        if name.replace("\\", "/").startswith("__MACOSX/") or Path(name.replace("\\", "/")).name.startswith("._"):
            continue  # Finder's resource-fork copies, not game files
        parts = name.replace("\\", "/").split("/")
        if (name.startswith(("/", "\\")) or re.match(r"[A-Za-z]:", name) or ".." in parts or "\0" in name
                or any(c in name for c in "\r\n")):
            raise SetupError(f"{label} contains an unsafe path: {name!r}", fix or "Use the original game download, or unzip it and choose the folder.")
        mode = info.external_attr >> 16
        if (mode & 0o170000) not in (0, 0o100000, 0o040000):
            raise SetupError(f"{label} contains a link or special file: {name!r}", fix or "Use the original game download, or unzip it and choose the folder.")
        total += info.file_size
        members.append(info)
        if len(members) > ZIP_MAX_FILES or total > ZIP_MAX_BYTES:
            raise SetupError(f"{label} is far larger than the game (more than 8 GB or 20,000 files).",
                             fix or "Choose the Need for Speed Most Wanted game .zip or its unzipped folder.")
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


def unpacked(work, key, events, what, unpack):
    """<work>/pc_src made by unpack(out), which returns the game folder in it. Reused while the inputs' key (paths,
    sizes, modification times) and the unpacked files' digest are unchanged; never left half made."""
    out = work / "pc_src"
    stamp = work / "pc_src.json"
    try:
        recorded = json.loads(stamp.read_text())
        if recorded["key"] == key:
            root = Path(recorded["root"])
            if root.is_relative_to(out) and digest_tree(root) == recorded["digest"]:
                events.emit("warn", id="pc", msg=f"Reusing the game unpacked earlier from {what}.")
                return root
    except (OSError, ValueError, KeyError, TypeError, InstallError):
        pass
    stamp.unlink(missing_ok=True)
    remove(out)
    try:
        root = unpack(out)
        tmp = stamp.with_suffix(".tmp")
        tmp.write_text(json.dumps(dict(key=key, root=str(root), digest=digest_tree(root))))
        os.replace(tmp, stamp)  # the stamp appears only for a complete, verified unpack
        return root
    except BaseException:
        with ignored_cancellation():
            remove(out)  # never leave half a game behind: a later run must not see a partial pc_src
        raise


def extract_pc_zip(source, work, events):
    """Unpack a PC game .zip (read only) into <work>/pc_src and return the game folder inside it."""
    st = source.stat()
    def unpack(out):
        try:
            archive = zipfile.ZipFile(source)
        except (zipfile.BadZipFile, OSError) as e:
            raise SetupError(f"The .zip can't be read: {e}", "Download the game .zip again, or unzip it and choose the folder.") from None
        with archive:
            members, total = zip_members(archive)
            if shutil.disk_usage(work).free < total + 512 * 1024**2:
                raise SetupError("There is not enough free disk space to unpack the .zip.", "Free space on the work volume, then try again.")
            out.mkdir(parents=True)
            unpack_zip(archive, members, total, out, lambda i, written: i % 50 == 0 and events.progress(i, len(members)))
        return find_pc_root(out)
    return unpacked(work, dict(path=str(source), size=st.st_size, mtime=st.st_mtime_ns), events, "the .zip", unpack)


def unpack_zip(archive, members, total, out, after, label="The .zip", fix=None):
    """Write the checked members (zip_members) below out; Windows paths (as on the PC discs) become folders.
    after(index, bytes written) follows each member."""
    base = out.resolve()
    written = 0
    for i, info in enumerate(members):
        name = info.filename.replace("\\", "/")
        target = (out / name).resolve()
        if target != base and base not in target.parents:
            raise SetupError(f"{label} contains an unsafe path: {info.filename!r}", fix or "Use the original game download.")
        if name.endswith("/"):
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with archive.open(info) as src, open(target, "xb") as dst:
                for block in iter(lambda: src.read(1024 * 1024), b""):
                    written += len(block)
                    if written > total or written > ZIP_MAX_BYTES:  # sizes in the directory were false
                        raise SetupError(f"{label} unpacks to more than it declares.", fix or "Download the game .zip again.")
                    dst.write(block)
        except (zipfile.BadZipFile, zlib_error(), EOFError, NotImplementedError) as e:
            raise SetupError(f"{label} is damaged or uses an unsupported format: {e}",
                             fix or "Download the game .zip again, or unzip it and choose the folder.") from None
        except FileExistsError:
            raise SetupError(f"{label} contains the same file twice: {info.filename!r}", fix or "Use the original game download.") from None
        after(i, written)


DISC_FIX = "Make the disc image again from the original disc (or download it again if the download was interrupted)."
UPDATE_NEEDED = ("Choose the PC game's official 1.3 update (nfsmwpatch1.3.exe) as well. Setup applies it to its own copy of "
                 "the game; the update is only read, never run.")


def extract_pc_disc(source, update, exe, work, events, source_root=ROOT, extra=()):
    """Install the game from PC disc images (read only) into <work>/pc_src as EA's installer does: every disc's
    archives and disc 1's loose files, then the 1.3 update's files and, when chosen, a speed.exe that has the 1.3 code
    (the discs' and the update's are copy-protected). extra are discs chosen one by one; the rest are found beside
    source. Returns the game folder."""
    try:
        discs = pc_disc.disc_set(source, extra)
    except (pc_disc.DiscError, OSError) as e:
        raise SetupError(str(e), getattr(e, "fix", pc_disc.FIX)) from None
    def stat(p):
        st = p.stat()
        return [str(p), st.st_size, st.st_mtime_ns]
    def disc_stamp(d):  # a disc folder's own time stays put when a file in it is overwritten: stamp every file
        return stat(d.path) if d.handle else [str(d.path)] + [stat(d.path / name) for name, _, _ in sorted(d.files.values())]
    try:
        key = dict(movie_layout=1, discs=[disc_stamp(d) for d in discs.values()], update=stat(update) if update else None,
                   exe=stat(exe) if exe else None)
        return unpacked(work, key, events, "the disc images",
                        lambda out: _install_discs(discs, update, exe, out, work, events, source_root))
    except (pc_disc.DiscError, rtpatch.PatchError) as e:
        raise SetupError(str(e), e.fix) from None
    finally:
        for disc in discs.values():
            disc.close()


def _install_discs(discs, update, exe, out, work, events, source_root):
    first = discs[1]
    _, loose = pc_disc.install_list(first)
    loose_files = [(first, name) for name in loose]
    loose_files += [(disc, name) for disc in discs.values() for name, _, _ in disc.files.values()
                    if name.casefold().startswith("movies/")]
    if "speed.exe" not in first:
        raise SetupError("Disc 1 has no speed.exe.", pc_disc.FIX)
    def current(data):
        try:
            return bool(exe_fingerprint(data, "speed.exe", source_root))
        except SetupError:
            return False
    # Which speed.exe the game ends up with is settled first, before gigabytes are unpacked.
    patch = rtpatch.Update(update) if update else None
    if patch is None and not current(first.read("speed.exe", rtpatch.MAX_FILE)):
        raise SetupError("Your discs have an earlier version of the PC game than 1.3.", UPDATE_NEEDED)
    new_exe = None
    if exe:
        if not exe.is_file() or not 0 < exe.stat().st_size <= rtpatch.MAX_FILE:
            raise SetupError(f"The chosen speed.exe is not a file of the expected size: {exe}", "Choose the speed.exe again.")
        new_exe = exe.read_bytes()
        exe_fingerprint(new_exe, exe, source_root)
    elif patch and patch.record("speed.exe"):
        record = patch.record("speed.exe")
        new_exe = patch.decode(record, lambda n: events.progress(n, record.size))
        if not current(new_exe):
            raise SetupError("The speed.exe on your discs and in the 1.3 update is copy-protected, so Setup can't translate its code.",
                             "Choose a speed.exe that has the version 1.3 code as well. Setup checks it and only reads it.")
    opened, archives = [], []
    try:
        for number, disc in discs.items():
            for name in disc.archives:
                label = f"{name} on disc {number}"
                opened.append(disc.open(name))
                try:
                    archive = zipfile.ZipFile(opened[-1])
                except (zipfile.BadZipFile, OSError) as e:
                    raise SetupError(f"{label} can't be read: {e}", DISC_FIX) from None
                opened.append(archive)
                archives.append((label, archive) + zip_members(archive, label, DISC_FIX))
        total = sum(size for *_, size in archives) + sum(disc.size(name) for disc, name in loose_files)
        if total > ZIP_MAX_BYTES or len(loose_files) + sum(len(members) for _, _, members, _ in archives) > ZIP_MAX_FILES:
            raise SetupError("The discs hold far more than the game (more than 8 GB or 20,000 files).", pc_disc.FIX)
        if shutil.disk_usage(work).free < total + 512 * 1024**2:
            raise SetupError("There is not enough free disk space to unpack the discs.", "Free space on the work volume, then try again.")
        out.mkdir(parents=True)
        done = 0
        for label, archive, members, size in archives:
            unpack_zip(archive, members, size, out, lambda i, written, base=done: events.progress(base + written, total), label, DISC_FIX)
            done += size
    finally:
        for f in reversed(opened):
            f.close()
    for disc, name in loose_files:
        # Merge Windows component case, including a MOVIES folder already created by an archive.
        target = out
        for part in name.split("/"):
            matches = [p for p in target.iterdir() if p.name.casefold() == part.casefold()] if target.is_dir() else []
            if len(matches) > 1:
                raise SetupError(f"The discs hold {name} twice (in different case).", DISC_FIX)
            target = matches[0] if matches else target / part
        if target.exists():
            identical = target.is_file() and target.stat().st_size == disc.size(name)
            if identical:
                with disc.open(name) as src, target.open("rb") as existing:
                    while True:
                        chunk = src.read(1 << 20)
                        if chunk != existing.read(1 << 20):
                            identical = False
                            break
                        if not chunk:
                            break
            if not identical:
                raise SetupError(f"The discs hold copies with different contents: {name}.", DISC_FIX)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with disc.open(name) as src, open(target, "xb") as dst:
            shutil.copyfileobj(src, dst, 1 << 20)
        if target.stat().st_size != disc.size(name):
            raise SetupError(f"{name} on disc {disc.number} changed or was cut short while Setup copied it.", DISC_FIX)
    if patch:
        patch.apply(out, skip=["speed.exe"] if new_exe is not None else [], progress=events.progress)
    if new_exe is not None:
        existing = [p for p in out.iterdir() if p.name.casefold() == "speed.exe"]
        target = existing[0] if len(existing) == 1 else out / "speed.exe"
        temporary = out / (".setup-speed-" + uuid.uuid4().hex)
        temporary.write_bytes(new_exe)
        os.replace(temporary, target)
    names = {p.name.casefold() for p in out.iterdir()}
    if "speed.exe" not in names or not all(n.casefold() in names for n in PC_DIRS):
        raise SetupError("The discs don't hold the whole game (speed.exe, GLOBAL, TRACKS, CARS, FRONTEND and SOUND).", pc_disc.FIX)
    movie_dirs = [p for p in out.iterdir() if p.name.casefold() == "movies" and p.is_dir()]
    if len(movie_dirs) != 1 or not any(p.is_file() and p.suffix.casefold() == ".vp6" and p.stat().st_size > 0
                                       for p in movie_dirs[0].iterdir()):
        raise SetupError("The PC discs are missing MOVIES or its .vp6 movie files (the MOVIES folder is empty).",
                         "Choose the complete PC disc set, including its MOVIES files, or an installed game folder with MOVIES.")
    return out


def zlib_error():
    import zlib
    return zlib.error


def is_pc_zip(path):
    return path.is_file() and path.suffix.lower() == ".zip"


def pc_extras(pc, update, exe, discs=()):
    """The 1.3 update, speed.exe and further discs that go with PC disc images: checked to exist, refused with
    anything else."""
    if (update or exe or discs) and not pc_disc.is_disc(pc):
        raise SetupError("The 1.3 update, a separate speed.exe and further discs are only used with PC disc images.",
                         "Choose your game's disc images, or leave the update, speed.exe and discs out.")
    for path, what in ((update, "1.3 update"), (exe, "speed.exe")):
        if path and not path.is_file():
            raise SetupError(f"The chosen {what} is missing or not a file: {path}", f"Choose the {what} again.")
    for path in discs:
        if not pc_disc.is_disc(path):
            raise SetupError(f"The chosen disc is missing or not a disc image: {path}", "Choose the disc again.")
    return [p for p in (update, exe) if p] + list(discs)


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
    for name in ("LICENSE", "NOTICE"):
        if (root / name).is_file():
            result[name] = sha_file(root / name)
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


def cache_key(source, dependencies, toolchain, pc_hash, x360_hash, extras=None):
    return identity(dict(schema=1, source=source, dependencies=dependencies, toolchain=toolchain,
                         player=dict(pc=pc_hash, x360=x360_hash, **x360_extras.identity(extras)), public=True, cflags="recomp defaults; runtime -O2 -g"))


class BuildStageCache:
    """Verified immutable stage outputs, independent of the disposable source copy.

    Callers hold build.lock. Neither interrupted output nor an old target/compiler
    artifact can satisfy build_inputs.fresh. Copies never share writable inodes.
    """
    def __init__(self, directory, events, inputs):
        self.directory, self.events, self.inputs = directory, events, inputs

    def run(self, name, fingerprint, source, relative, action):
        parent = self.directory / name
        self.directory.mkdir(parents=True, exist_ok=True)
        if not real_dir(self.directory):
            raise SetupError("The build-stage cache is not a real directory.")
        parent.mkdir(parents=True, exist_ok=True)
        if not real_dir(parent):
            raise SetupError("The build-stage cache is not a real directory.")
        entry = parent / fingerprint
        names = list(dict.fromkeys(relative))
        tops = [n for n in names if not any(Path(n).is_relative_to(p) for p in names if p != n)]

        def artifacts(base):
            result = {}
            for n in names:
                output = base / n
                if os.path.realpath(output) != os.path.abspath(output):
                    raise InstallError("Unexpected symbolic link in build output.")
                result[n] = digest_tree(output) if output.exists() and not (
                    output.is_file() and not output.stat().st_size) else 'missing'
            return result

        def clear_output(base, n):
            output = base / n
            if os.path.realpath(output.parent) != os.path.abspath(output.parent):
                raise SetupError("The build output parent is a symbolic link.")
            remove(output)

        def copy_outputs(src, dst):
            for n in tops:
                output = dst / n
                clear_output(dst, n)
                output.parent.mkdir(parents=True, exist_ok=True)
                if (src / n).is_dir():
                    shutil.copytree(src / n, output)
                else:
                    shutil.copy2(src / n, output)

        try:
            saved = artifacts(entry / 'outputs') if real_dir(entry) else {}
            valid = bool(saved) and self.inputs.fresh(entry / 'manifest.json', fingerprint, saved)
        except (OSError, InstallError, ValueError):
            valid = False
        if valid:
            try:
                local = artifacts(source)
            except (OSError, InstallError):
                local = {}
            if local != saved:
                copy_outputs(entry / 'outputs', source)
            if artifacts(source) != saved:
                raise SetupError(f"The retained {name} output changed while being restored.")
            self.events.emit("warn", id=self.events.current, msg=f"Resuming verified {name} output.")
            return False
        remove(entry)
        for n in tops:
            clear_output(source, n)
        action()
        saved = artifacts(source)
        if 'missing' in saved.values():
            raise SetupError(f"{name} completed without all required outputs.")
        pending = parent / (".pending-" + uuid.uuid4().hex)
        try:
            copy_outputs(source, pending / 'outputs')
            if artifacts(pending / 'outputs') != saved:
                raise SetupError(f"The {name} output changed while being retained.")
            self.inputs.record(pending / 'manifest.json', fingerprint, saved)
            pending.rename(entry)
        finally:
            remove(pending)
        return True


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


def prepare_x360(source, out, pc, events, env, record=None, extras=None):
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
            result = module.prepare(source, out, pc, progress, **({} if x360_extras.selection(extras) == x360_extras.ALL else {"extras": extras}))
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


def x360_content_digest(source):
    # A disc image's filename/location is not part of its content identity.
    return sha_file(source) if source.is_file() else digest_tree(source)


def x360_stat(source):
    """Path, size, modification and change time (ctime cannot be set back) and inode of every file of the X360 copy (a cheap change detector)."""
    def stat(p):
        st = p.stat()
        return [st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_ino]
    if source.is_dir():
        return {str(p.relative_to(source)): stat(p) for p in sorted(source.rglob("*")) if p.is_file()}
    return {"": stat(source)}


def x360_identity(source, pc_hash, inventory, content, toolchain, extras=None):
    """What a converted X360 copy depends on: the player's copy (its content digest, the one --build computes for
    its cache key, plus every file's path, size, modification time and inode), the PC game's content and this
    Setup's source and toolchain."""
    files = x360_stat(source)
    return identity(dict(schema=3, x360=str(source), files=files, content=content, pc=pc_hash,
                         source=identity(inventory), toolchain=toolchain, **x360_extras.identity(extras)))


def record_check_x360(work, key, roots, record):
    """After a successful --check: remember what its converted copy is, so --build can reuse it instead of
    converting the same copy again (about 6 minutes and 5 GB, M4 F4)."""
    out = work / CHECK_X360
    relative = {k: str(Path(v).relative_to(out.resolve())) for k, v in roots.items()}
    stamp = work / (CHECK_X360 + ".json")
    tmp = stamp.with_suffix(".tmp")
    tmp.write_text(json.dumps(dict(key=key, roots=relative, digest=digest_tree(out), **record), sort_keys=True))
    os.replace(tmp, stamp)


def reuse_check_x360(work, key, target, events, record=None):
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
    if record is not None:
        record.update(features=features, missing=missing)
    return roots


def restore_prepared_x360(cache, target, events, record):
    roots = cache.restore(target, record)
    if roots is not None:
        events.emit("warn", id="x360", msg="Reusing verified Xbox 360 preparation from the persistent cache.")
        events.emit("features", x360=record["features"], missing=record["missing"])
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


def check_locations(paths, pc, x360, dest, source, extras=(), carbon=None):
    """Refuse any location choice that could make Setup write into, or delete, something it must only read.

    work and cache are recursively removed and populated (stage, pc_src extraction, build copies): they may not
    overlap, either way, any input, the source, the app names, the 4.0 data or the 3.x profile (its saves). data and
    logs may not lie inside any of those. The destination may not lie inside an input (or a .zip's folder), the
    profiles, the source, work or cache, nor be the
    folder holding a .zip; it may contain them (installing into a home folder is fine: only the app's
    own names directly in it are written). All comparisons are by filesystem identity (within()). extras are further
    inputs (a disc image's 1.3 update and speed.exe).
    """
    inputs = [pc] + ([x360] if x360 else []) + list(extras) + ([carbon_input(carbon)] if carbon else [])
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


def check_space(paths, pc=None, dest=None, stage_only=False, car_plan=None, x360_bytes=0, x360_shared_bytes=0):
    if car_plan is not None:
        # F-T groups extraction, temporary output, publication and margin by
        # volume. Unknown conversion sizes stay unknown until measured.
        for volume in car_plan['volumes']:
            required = volume['required_bytes']
            minimum = (required if required is not None else
                       sum(volume.get(key) or 0 for key in ('extraction_bytes', 'staging_bytes', 'output_bytes', 'margin_bytes')))
            if minimum > volume['available_bytes']:
                raise SetupError("There is not enough free disk space for the selected extras.",
                                 "Free space on the listed extras volumes and retry.")
        return
    size = sum(p.stat().st_size for p in pc.rglob("*") if p.is_file())
    # Keep two copies until commit (staging and permanent); allow 4 GiB for
    # generated C, libraries and toolchain output. Check every relevant volume.
    for path, required in ((paths.work, size + 4 * 1024**3 + x360_bytes + x360_shared_bytes), (paths.cache, 4 * 1024**3 + x360_bytes),
                           (dest if stage_only else paths.data, size + 512 * 1024**2 + x360_bytes), (dest, 256 * 1024**2)):
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
    stage_only = getattr(args, "stage_only", False)
    pc, selected_pc, dest = clean_path(args.pc), clean_path(args.pc), clean_path(args.dest)
    # Normal Setup receives a destination directory; headless staging receives the app path.
    destination = dest if stage_only else dest / f"{PUBLIC_NAME}.app"
    if stage_only:
        dest = destination.parent
    x360 = clean_path(args.x360) if args.x360 else None
    chosen = getattr(args, "x360_extras", None)
    if chosen is None:
        try:
            remembered = staging.load_choices(paths.state)
        except InstallError:
            remembered = None  # explicit sources can repair damaged remembered inputs
        chosen = remembered.get("x360_extras") if remembered else None
    try:
        chosen = x360_extras.selection(chosen)
    except ValueError as error:
        raise SetupError(str(error)) from error
    options = {} if chosen == x360_extras.ALL else {"extras": chosen}
    pc_update = clean_path(args.pc_update) if getattr(args, "pc_update", None) else None
    pc_exe = clean_path(args.pc_exe) if getattr(args, "pc_exe", None) else None
    pc_discs = [clean_path(p) for p in getattr(args, "pc_disc", None) or []]
    extras = pc_extras(pc, pc_update, pc_exe, pc_discs)
    check_locations(paths, pc, x360, dest, source_root, extras)
    if stage_only:
        check_stage_locations(args.stage, paths, pc, x360, destination, source_root, extras)
        if x360 and not x360.exists():
            raise SetupError(f"X360_SOURCE_MISSING: The remembered Xbox 360 source is unavailable: {x360}",
                             "Reconnect the Xbox 360 source or select its new location with --x360.")
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
    dependencies = dependency_identity(source_root, toolchain) if not args.check else None
    events.progress()
    events.stage("pc")
    if is_pc_zip(pc):  # a .zip of the game folder: only read
        pc = extract_pc_zip(pc, paths.work, events)
    elif pc_disc.is_disc(pc):  # PC disc images, the 1.3 update and speed.exe: only read, installed into work
        pc = extract_pc_disc(pc, pc_update, pc_exe, paths.work, events, source_root, pc_discs)
    exe, fingerprint = validate_pc(pc, source_root)
    note = movie_note(pc)
    if note:  # the game runs without them, but must not lose its movies without a word
        setup_log(note)
        events.emit("warn", id="pc", msg=note)
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
        content = x360_content_digest(x360)
        inventory = source_inventory(source_root)
        prepared_cache = PreparedCache(paths.cache / "x360", preparation_key(content, pc_hash, inventory, toolchain, **options))
        roots = restore_prepared_x360(prepared_cache, check_out, events, record) if owned(paths.cache, paths.state) else None
        if roots is None:
            roots = prepare_x360(x360, check_out, pc, events, runner.env, record, **options)
        if roots and x360_stat(x360) == before and x360_content_digest(x360) == content:  # changed while it was converted: not reused by the build
            record_check_x360(paths.work, x360_identity(x360, pc_hash, inventory, content, toolchain, **options), roots, record)
        events.progress()
        return None
    x360_before = x360_stat(x360) if x360 else None
    x360_hash = x360_content_digest(x360) if x360 else None
    inventory = source_inventory(source_root)
    key = cache_key(inventory, dependencies, toolchain, pc_hash, x360_hash, **options)
    cache_home = paths.cache / "builds" / key
    claim(paths.cache, paths.cache == paths.default_cache, SETUP_CACHE_NAMES, "cache", paths.state)
    with exclusive(paths.cache / "build.lock"):
        # Publishers hold this same lock: every pending copy is now abandoned.
        # Reclaim it before disk-space checks, including builds with cache hits.
        PreparedCache.clean_pending(paths.cache / "x360")
        space = dict(x360_bytes=x360_extras.required_bytes(chosen) if x360 else 0,
                     x360_shared_bytes=146_000_000 if x360 and "textures" in chosen and "arrest" not in chosen else 0)
        if stage_only:
            check_space(paths, pc, clean_path(args.stage), stage_only=True, **space)
        else:
            check_space(paths, pc, dest, **space)
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
        record = {}
        prepared_cache = PreparedCache(paths.cache / "x360", preparation_key(x360_hash, pc_hash, inventory, toolchain, **options))
        reused = restore_prepared_x360(prepared_cache, staged_data / "x360", events, record) if x360 else None
        cached_preparation = reused is not None
        if x360 and reused is None:
            reused = reuse_check_x360(paths.work, x360_identity(x360, pc_hash, inventory, x360_hash, toolchain, **options),
                                      staged_data / "x360", events, record)
        roots.update(reused if reused is not None else prepare_x360(x360, staged_data / "x360", pc_copy, events, runner.env, record, **options))
        if stage_only and x360 and not (set(roots) - {"pc_root"}):
            raise SetupError("X360_PREPARATION_MISSING: The selected Xbox 360 source produced no extras.",
                             "Use a complete Setup build with Xbox 360 support and reconnect your source.")
        if x360 and (x360_stat(x360) != x360_before or x360_content_digest(x360) != x360_hash):
            # The cache key names the bytes digested before the conversion: those must be the bytes converted.
            raise SetupError("Your Xbox 360 copy changed while Setup was converting it.", "Leave it unchanged and run Setup again.")
        if x360 and not cached_preparation and set(roots) - {"pc_root"}:
            saved = prepared_cache.publish(staged_data / "x360", {k: v for k, v in roots.items() if k != "pc_root"}, record)
            if saved:
                events.emit("warn", id="x360", msg=f"Verified preparation saved in {prepared_cache.directory}. "
                            "To clear it, quit Setup and delete that cache folder.")
        manifest = stage / "roots.txt"
        write_roots(manifest, roots)
        events.progress()
        spec = importlib.util.spec_from_file_location("_setup_build_inputs", source / "recomp/build_inputs.py")
        build_inputs = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(build_inputs)
        cache = BuildStageCache(paths.cache / "stages", events, build_inputs)
        # /usr/bin compiler launchers can stay unchanged across a toolchain
        # repair. Include the selected binaries as well as preflight's versions.
        build_compiler = dict(toolchain)
        build_compiler["python-binary"] = sha_file(Path(sys.executable))
        build_compiler["package-files"] = build_inputs.package_files(toolchain["packages"])
        build_compiler["selected"] = {}
        for tool in ("clang", "clang++", "ar", "ld", "make"):
            selected = runner.run(["xcrun", "--find", tool], capture=True).strip()
            build_compiler["selected"][tool] = [selected, sha_file(Path(selected)) if selected else "missing"]
        py = Path(sys.executable).absolute()
        def command(argv):
            return lambda: runner.run(argv, cwd=source)
        def cached(name, relative, argv):
            # Absolute staging/cache paths change on every source/content update;
            # argument roles and values, including flags, determine the build.
            # The executable's bytes/version remain in build_compiler; its
            # installation path is a role just like source and asset paths.
            roles = {str(py): "$PYTHON", str(pc_copy): "$PC", str(source): "$SOURCE"}
            if "x360_root" in roots:
                roles[str(roots["x360_root"])] = "$X360"
            arguments = []
            for arg in map(str, argv):
                arguments.append(next((role + arg[len(path):] for path, role in roles.items()
                                       if arg == path or arg.startswith(path + os.sep)), arg))
            fingerprint = build_inputs.stage_key(name, source, pc_copy, roots.get("x360_root"),
                compiler=build_compiler, env=runner.env, arguments=arguments)
            cache.run(name, fingerprint, source, relative, command(argv))
        events.stage("recompile")
        cached("recompile", ["recomp/out", "recomp/out/speed.a", "recomp/out/funcs.h", "recomp/out/breaker_diag.inc",
                             "recomp/out/speed_32.c"], [py, "-I", "recomp/build_all.py", "--exe", pc_copy / "speed.exe"])
        if not (source / "recomp/out/speed.a").is_file():
            raise SetupError("Recompilation produced no speed.a; stale archives are not accepted.")
        events.progress()
        events.stage("tables")
        table_generator = build_inputs.load_module(source / "tools/gen_tables.py")
        table_names = list(table_generator.STUBS) + table_generator.PUBTABLES + table_generator.PADTABLES
        cached("tables", ["runtime/gen/" + name for name in table_names] + ["runtime/gen/inputs.txt"], [py, "-I", "tools/gen_tables.py", "--pc", pc_copy] +
               (["--x360", roots["x360_root"]] if "x360_root" in roots else ["--no-x360"]))
        events.progress()
        events.stage("audio")
        # FFmpeg is built here (media.cpp needs its headers) but reported as itself: an FFmpeg failure is an "ffmpeg"
        # error ("Building the video decoder"), not an audio one. No stage event: the contract's order is unchanged.
        events.current = "ffmpeg"
        used_prebuilt = prebuilt.install(source_root, source, setup_log,
                                        expected_sha256=getattr(args, 'prebuilt_manifest_sha256', None))
        ffmpeg_libraries = [f"third_party/ffmpeg/lib/lib{name}.a" for name in ("avformat", "avcodec", "swscale", "swresample", "avutil")]
        if not used_prebuilt:
            cached("ffmpeg", ["third_party/ffmpeg", "third_party/ffmpeg/include/libavformat/avformat.h"] + ffmpeg_libraries,
                   ["sh", "media/build_ffmpeg.sh"])
        events.current = "audio"
        # Install only the libraries; build.sh's audio tests run in the offline suite.
        if not used_prebuilt:
            cached("audio", ["host/audio/build/libhostaudio.a", "host/audio/build/libmedia.a"], ["sh", "host/audio/build.sh", "lib"])
        events.progress()
        events.stage("shaders")
        if not used_prebuilt:
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
        # Coverage is validation against the current host source, not an input
        # to translated code. Recheck it even when both native artifacts resume.
        runner.run([py, "-I", "recomp/hook_coverage.py", "--host", "mac", "--binary", "runtime/boot"], cwd=source)
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
        if x360 and x360_stat(x360) != x360_before:
            raise SetupError("Your Xbox 360 copy changed while Setup was reading it.", "Leave it unchanged and run Setup again.")
        if stage_only:
            events.stage("stage")
            ready = staging.publish(clean_path(args.stage), app, staged_data, roots, destination, paths.data,
                                    PUBLIC_VERSION, dict(pc=str(selected_pc), x360=str(x360) if x360 else None,
                                    pc_update=str(pc_update) if pc_update else None, pc_exe=str(pc_exe) if pc_exe else None,
                                    pc_discs=[str(p) for p in pc_discs], **x360_extras.identity(chosen),
                                    pc_sha256=pc_hash, x360_sha256=x360_hash, source_id=identity(inventory),
                                    executable_fingerprint=fingerprint, dependencies=dependencies, toolchain=toolchain),
                                    on_commit=lambda path: setattr(events, "ready", path))
            return ready
        events.stage("install")
        def committed(path):
            events.installed = path
            # Only a committed normal installation changes remembered source choices.
            with ignored_cancellation():
                try:
                    staging.save_choices(paths.state, selected_pc, x360, path, pc_update, pc_exe, pc_discs, chosen)
                except (OSError, InstallError) as e:
                    events.emit("warn", id="install", msg=f"The app was installed, but source choices could not be saved: {e}. Run Setup again before updating.")
        installed = transactional_install(app, staged_data, roots, paths.data, dest, lambda p: verify_app(p, runner), log=setup_log,
                                          on_commit=committed,
                                          after_commit=lambda: migrate_3x_saves(paths, events), move_data=True)
        clean_work(paths, events, key)
        events.progress()
        return installed


def check_stage_locations(target, paths, pc, x360, destination, source, extras=()):
    target = clean_path(target)
    if destination.name != f"{PUBLIC_NAME}.app":
        raise SetupError("The staging destination must name NFS Most Wanted Native.app.", "Pass --dest with the installed app path.")
    protected = [pc, source, destination, destination.parent / "NFSMW.app", paths.data, paths.legacy,
                 paths.work, paths.cache, paths.logs, paths.state / "source-choices.json"] + ([x360] if x360 else []) + list(extras)
    for keep in protected:
        if overlaps(target, keep):
            raise SetupError(f"The update stage overlaps a protected location: {keep}", "Choose a separate new staging directory.")
    # Ownership records and logs are also writes, even though staging never calls the installer.
    for writable in (paths.logs, paths.state / OWN_DIR):
        for keep in [pc, source, destination, paths.data, paths.legacy] + ([x360] if x360 else []) + list(extras):
            if overlaps(writable, keep):
                raise SetupError(f"Setup staging logs/state overlaps a protected location: {keep}",
                                 "Choose separate Setup logs, state, source and profile locations.")
    if os.path.lexists(target):
        raise SetupError("The update staging directory already exists.", "Choose a new staging directory for each update attempt.")


def car_api():
    # Keep optional conversion dependencies out of the required build's import
    # path; its normal preflight reports any missing bundled Python packages.
    return importlib.import_module('.car_import', __package__)


@contextmanager
def car_cancellation():
    """F-T polls cancellation before activation; never interrupt its commit."""
    pending = [False]
    def stop(signum, frame):
        pending[0] = True
    old = {sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        yield lambda: pending[0]
    finally:
        for sig, handler in old.items():
            signal.signal(sig, handler)


@contextmanager
def car_log(directory):
    """Open the extras log relative to a checked directory descriptor."""
    path = directory / ('cars-' + uuid.uuid4().hex + '.log')
    with car_catalog._directory(directory, create=True) as base:
        car_catalog._bound(directory, base)
        fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=base)
        with os.fdopen(fd, 'wb', buffering=0) as log:
            yield path, log


def carbon_input(value, *, picked=False):
    """Resolve extras picks once; retain build protection's existing normalization."""
    car_import = car_api()
    path = car_import._path(Path(value).expanduser().resolve(strict=True) if picked else value)
    if path.is_file() and (path.suffix.lower() == '.exe' or path.name.casefold() == 'default.xex'):
        if not picked:
            return path.parent
        raise SetupError("Choose the Carbon game folder instead of its executable.",
                         "Choose the folder containing CARS and GLOBAL, or an Xbox 360 ISO.")
    return car_sources._folder(path)


def car_specs(path):
    if not path:
        return []
    car_import = car_api()
    path = car_import._path(path)
    with car_import._asset(path) as fd:
        if os.fstat(fd).st_size > 1 << 20:
            raise SetupError("The car selection JSON is too large.")
        with os.fdopen(os.dup(fd), 'r') as stream:
            value = json.load(stream)
    if type(value) is not list:
        raise SetupError('The car selection must be a JSON array of car metadata.')
    return car_import._specs(value)


def car_installed_inputs(paths):
    """Protect remembered asset roots even when management needs no media.

    Only read the manifest paths; unplugged asset volumes must not prevent
    listing, removal or restoration of retained packs.
    """
    inputs = []
    for name, keys in (('roots.txt', {'pc_root', 'x360_root', 'assets_root', 'movies'}),
                       ('app.txt', {'game_dir'})):
        manifest = paths.data / name
        if not manifest.exists():
            continue
        with car_api()._asset(manifest) as fd:
            if os.fstat(fd).st_size > 1 << 18:
                raise SetupError('The installed asset manifest is too large.')
            with os.fdopen(os.dup(fd), 'r') as stream:
                for line in stream:
                    key, sep, value = line.strip().partition('=')
                    if sep and key.strip() in keys and Path(value.strip()).is_absolute():
                        inputs.append(clean_path(value.strip()))
    return inputs


def car_locations(paths, inputs):
    """Only catalog/cache/logs are writable; source and save trees are protected."""
    managed = [paths.data / 'car-catalog', paths.cache, paths.work, paths.state / OWN_DIR]
    protected = [ROOT, paths.legacy, paths.data / SAVE_DIR] + list(inputs) + car_installed_inputs(paths)
    for directory in (paths.cache, paths.work, paths.state / OWN_DIR):
        if overlaps(directory, paths.data):
            raise SetupError('Extras temporary files must be outside the installed data profile.')
    if overlaps(paths.data, paths.legacy):
        raise SetupError("The data and legacy save folders overlap.")
    for target in managed:
        for keep in protected:
            if overlaps(target, keep):
                raise SetupError("An extras work/catalog folder overlaps a protected path.",
                                 "Choose separate source, save, cache and work folders.")
    for i, target in enumerate(managed):
        if any(overlaps(target, other) for other in managed[i + 1:]):
            raise SetupError("Extras work, cache and catalog folders must be separate.")
    for source in inputs:
        if overlaps(source, paths.data):
            # Installed MW roots may live beneath data, but never contain the
            # catalog, work, saves or metadata being written (checked above).
            if within(paths.data, source):
                raise SetupError("An extras input contains the installed data root.")


def car_plan_event(events, plan, specs):
    by_slot = {car['slot']: car for car in specs}
    rows = []
    for car in plan.get('cars', []):
        spec = by_slot[car['slot']]
        display = spec['display']
        name = display.get('name') if type(display) is dict else None
        rows.append(dict(slot=car['slot'], name=name if type(name) is str else spec['folder'],
                         car_hash=car_sources.path_hash('CARS/' + spec['folder']), approved=car['approved'],
                         reused=car['reused'], reason=car.get('reason', '') or
                         ('' if car['approved'] else 'Output has not passed packaged approval.')))
    events.emit('cars-plan', measured=plan['measured'], approved=plan['approved'], volumes=plan['volumes'], cars=rows)


def car_result(events, operation, result):
    selected = result.get('selection', result.get('pointer', {}).get('selection', []))
    events.emit('cars-result', operation=operation, verified=not bool(result.get('error')),
                generation=result.get('generation') or result.get('pointer', {}).get('generation') or '',
                message=result.get('error', ''),
                selection=selected,
                cars=[dict(slot=r['slot'], name=r.get('display', {}).get('name', ''),
                           state=r['state'], selected=r['slot'] in selected) for r in result.get('cars', [])],
                generations=[dict(generation=r['generation'], verified=r['verified'])
                             for r in result.get('generations', [])])


def cars_command(args):
    """No build flags, developer tools, source copying or profile/save migration."""
    car_import = car_api()
    operation = next(name for name in ('check', 'import', 'list', 'remove', 'restore')
                     if getattr(args, 'cars_' + name))
    if any((args.pc, args.x360, args.dest, args.stage, args.app, args.pc_update, args.pc_exe, args.pc_disc)):
        raise SetupError("Car commands use the installed data root; omit PC build and destination arguments.")
    if operation in ('check', 'import') and not args.carbon:
        raise SetupError("--cars-check and --cars-import require --carbon.")
    if args.cars_measure and operation != 'check':
        raise SetupError('--cars-measure requires --cars-check.')
    if args.cars_repair and operation != 'import':
        raise SetupError('--cars-repair requires --cars-import.')
    if operation == 'import' and not args.cars_spec:
        raise SetupError("--cars-import requires --cars-spec (local car selection JSON).")
    if operation not in ('check', 'import') and any((args.carbon, args.cars_spec, args.cars_mw_pc,
                                                    args.cars_measure, args.cars_repair)):
        raise SetupError("Source/selection options apply only to car check/import.")
    # Paths normally resolves aliases for the build. CarImporter requires no
    # symlink components, including environment-selected profile/cache roots.
    for key in ('HOME', 'NFSMW_SETUP_HOME', 'NFSMW_SETUP_DATA', 'NFSMW_SETUP_CACHE', 'NFSMW_SETUP_STATE'):
        if os.environ.get(key):
            car_import._path(os.environ[key])
    if args.work:
        car_import._path(args.work)
    defaults = Paths(args.work or Path.home())
    paths = Paths(args.work or defaults.state / 'car-work')
    specs = car_specs(args.cars_spec)
    carbon = carbon_input(args.carbon, picked=True) if args.carbon else None
    inputs = ([carbon] if carbon else []) + ([car_import._path(args.cars_spec)] if args.cars_spec else [])
    mw = None
    if operation == 'import' or (operation == 'check' and specs):
        if args.cars_mw_pc:
            mw = car_import._path(args.cars_mw_pc)
        else:
            from .export import current_roots
            mw = current_roots(paths.data / 'unused.app', paths.data)['pc_root']
        if not (mw / 'CARS').is_dir():
            mw = find_pc_root(mw)
        inputs.append(mw)
    for managed in (paths.data, paths.cache, paths.work):
        car_import._path(managed)
    car_locations(paths, inputs)
    engine = car_import.CarImporter(paths.data / 'car-catalog', paths.cache / 'cars')
    if specs or operation in ('list', 'remove', 'restore'):
        # Adopt the same cache ownership as a required build before creating
        # car preparations. A later build must accept this cache, including an
        # explicit initially-empty cache override used before the first build.
        car_import._path(paths.state / OWN_DIR)
        claim(paths.cache, paths.cache == paths.default_cache, SETUP_CACHE_NAMES, 'cache', paths.state)
    with car_log(paths.work) as (log_path, log), car_cancellation() as cancel:
        events = Events(args.events, log)
        events.current = 'cars'
        events.emit('stage', id='cars', title='Checking and preparing optional extras')
        try:
            recognition = None
            if carbon:
                with cancellation():  # recognition has no catalog writes to protect
                    recognition = car_sources.recognise(carbon)
                engine._tick(cancel)
                if recognition['game'] != 'carbon':
                    raise SetupError("Choose a Carbon source for these extras.")
                try:
                    car_catalog.packaged_policy()
                    approval = True
                except (OSError, car_catalog.CatalogError):
                    approval = False
                events.emit('cars-recognition', game=recognition['game'], platform=recognition['platform'],
                            approval_available=approval, output_verified=False,
                            cars=[dict(car_hash=r['car_hash'], status=r['status']) for r in recognition['cars']])
                from .car_selection import available_specs
                specs, skipped = available_specs(specs, recognition)
                for reason in skipped:
                    events.emit('warn', id='cars', msg=reason)
            plan = None
            pending = iter(specs)
            active = None
            extracted = 0
            def progress(row):
                nonlocal active, extracted
                phase = row['phase']
                if phase == 'extracted':
                    active = next(pending, None)
                    extracted += 1
                slot = active['slot'] if active else -1
                if phase in ('extracted', 'converted', 'prepared'):
                    total = sum(v['extraction_bytes'] for v in plan['volumes']) if plan else 0
                    count = sum(not c['reused'] for c in plan['cars']) if plan else len(specs)
                    done = total if extracted == count else 0
                    # During a measured plan the exact byte plan is not yet
                    # available; 0/0 means unknown, never a guessed estimate.
                    events.emit('cars-progress', phase=phase, slot=slot,
                                done=done if phase == 'extracted' else 1,
                                total=total if phase == 'extracted' else 1)
                else:
                    events.emit('cars-progress', phase='verification', slot=-1, done=0, total=0)
            if specs:
                # Imports plan without conversion, then convert once inside
                # the activation transaction. Explicit measured checks stay dry.
                try:
                    plan = engine.plan(carbon, mw_pc=mw, cars=specs,
                                       measure=args.cars_measure and operation == 'check', cancel=cancel, progress=progress)
                except car_import.CacheError:
                    if not args.cars_repair:
                        raise
                    # Reimport owns quarantine/reconstruction. A normal plan
                    # deliberately refuses the corrupt preparation first.
                    events.emit('warn', id='cars', msg='A retained preparation needs repair; rebuilding it from the verified source.')
                if plan is not None:
                    check_space(paths, car_plan=plan)
                    car_plan_event(events, plan, specs)
                    if plan['measured']:
                        for car in plan['cars']:
                            if not car['reused']:
                                size = sum(p['size'] for p in car['contract']['inputs']['source'].values())
                                events.emit('cars-progress', phase='extracted', slot=car['slot'], done=size, total=size)
                    pending = iter(c for c in specs if not next(p for p in plan['cars'] if p['slot'] == c['slot'])['reused'])
                active, extracted = None, 0
            if operation == 'import':
                if not specs:
                    result = engine.list_cars()
                else:
                    result = engine.reimport(carbon, mw_pc=mw, cars=specs, cancel=cancel, progress=progress) if args.cars_repair else \
                        engine.import_cars(carbon, mw_pc=mw, cars=specs, cancel=cancel, progress=progress)
            elif operation == 'remove':
                result = engine.remove(args.cars_remove, cancel=cancel, progress=progress)
            elif operation == 'restore':
                result = engine.restore(args.cars_restore, cancel=cancel, progress=progress)
            elif operation == 'list':
                result = engine.list_cars()
            else:
                result = {}
            if operation in ('check', 'list'):
                engine._tick(cancel)
            # F-T's returned pointer is committed. Nothing after it may turn
            # that completed import/removal/restore into a cancellation/error.
            with ignored_cancellation():
                try:
                    log.write((json.dumps(result, sort_keys=True) + '\n').encode())
                except OSError:
                    pass
                if plan is None and result.get('volumes'):
                    try:
                        events.emit('cars-plan', measured=True, approved=True, volumes=result['volumes'], cars=[])
                    except OSError:
                        pass
                car_result(events, operation, result)
            return 0
        except (Exception, KeyboardInterrupt) as exc:
            try:
                log.write(traceback.format_exc().encode())
            except OSError:
                pass
            events.emit('error', id='cars', msg=str(exc) or 'Extras were cancelled.',
                        fix='Retry the extras operation or skip it. Your previous catalog is retained.', log=str(log_path))
            return 130 if isinstance(exc, (Cancelled, car_import.Cancelled, KeyboardInterrupt)) else getattr(exc, 'exit_code', 1)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build and install NFS Most Wanted Native from your own PC v1.3 game. Offline; no game files are supplied.")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--build", action="store_true")
    mode.add_argument("--stage-only", action="store_true", help="prepare a verified update without installing it")
    mode.add_argument("--export-all-in-one", action="store_true", help="export a signed app containing the current install's assets")
    for name in ('check', 'import', 'list'):
        mode.add_argument('--cars-' + name, action='store_true', help='standalone extras ' + name)
    mode.add_argument('--cars-remove', nargs='+', type=int, help='deselect permanent slots; retain packs')
    mode.add_argument('--cars-restore', help='restore a verified retained generation')
    ap.add_argument('--carbon', help='Carbon folder, any executable, or X360 ISO')
    ap.add_argument('--cars-spec', help='JSON array of local car metadata for the F-T API')
    ap.add_argument('--cars-mw-pc', help='MW donor folder before first installation; otherwise use installed roots')
    ap.add_argument('--cars-measure', action='store_true', help='measure conversion without activating it')
    ap.add_argument('--cars-repair', action='store_true', help='rebuild a rejected preparation from original sources')
    ap.add_argument("--app", help="installed .app to export (required with --export-all-in-one)")
    ap.add_argument("--stage", help="new directory for the prepared update and ready.json (required with --stage-only)")
    ap.add_argument("--pc", help="installed PC game folder, a .zip of it (unpacked into --work), or a PC disc image (.iso, or "
                    "a disc mounted in Finder; the set's other discs are found beside it and installed into --work)")
    ap.add_argument("--pc-update", help="with disc images: the PC game's official 1.3 update (nfsmwpatch1.3.exe), "
                    "applied to Setup's copy without running it")
    ap.add_argument("--pc-exe", help="with disc images: a speed.exe with the 1.3 code, used instead of the copy-protected one")
    ap.add_argument("--pc-disc", action="append", help="with disc images: another disc of the set, from any folder "
                    "(repeat for each disc not beside --pc; discs are identified by their content, not their names)")
    ap.add_argument("--x360-extras", help="comma-separated arrest,textures,ui; empty skips all three; omitted remembers the last install")
    ap.add_argument("--x360", help="your Xbox 360 game folder or ISO")
    ap.add_argument("--dest", help="installation directory; --stage-only: installed .app; --export-all-in-one: new .app path")
    ap.add_argument("--work", help="disposable build directory (required with --check or --build)")
    ap.add_argument("--events", action="store_true", help="JSON-line events on stdout; full output in the run log")
    ap.add_argument("--prebuilt-manifest-sha256", help="optional libraries' manifest digest from the signed Setup app")
    args = ap.parse_args(argv)
    paths = None
    events = None
    log_path = None
    car_mode = any((args.cars_check, args.cars_import, args.cars_list, args.cars_remove, args.cars_restore))
    try:
        if car_mode:
            return cars_command(args)
        if any((args.carbon, args.cars_spec, args.cars_mw_pc, args.cars_measure, args.cars_repair)):
            raise SetupError("Extras options require a --cars-* operation.")
        if args.export_all_in_one:
            if not args.app or not args.dest or args.pc or args.x360 or args.stage:
                raise SetupError("--export-all-in-one requires --app and --dest (a new .app path).")
            from .export import export_app, UPDATE_MESSAGE
            paths = Paths(args.work or tempfile.gettempdir())
            paths.logs.mkdir(parents=True, exist_ok=True)
            log_path = paths.logs / ("export-" + uuid.uuid4().hex + ".log")
            with log_path.open("xb", buffering=0) as log, cancellation():
                events = Events(args.events, log)
                events.stage("bundle")
                try:
                    metrics = export_app(clean_path(args.app), paths.data, clean_path(args.dest),
                                         on_publish=lambda path: setattr(events, "installed", str(path)))
                    log.write(("Export measurements: " + json.dumps(metrics, sort_keys=True) + "\n").encode())
                    events.emit("warn", id="bundle", msg=f"Exported in {metrics['seconds']:.1f} seconds. " +
                                f"App data: {metrics['logical_bytes'] / 1e9:.3f} GB; allocated file blocks: {metrics['allocated_bytes'] / 1e9:.3f} GB " +
                                "(includes blocks shared with the install). " +
                                f"Volume space used during export: {metrics['volume_used_delta_bytes'] / 1e9:+.3f} GB " +
                                "(includes metadata and other disk activity). " + UPDATE_MESSAGE)
                    events.emit("done", app=events.installed)
                except (Exception, KeyboardInterrupt):
                    if events.installed is not None and not events.completed:
                        with ignored_cancellation():
                            try:
                                events.emit("warn", id="bundle", msg="The export was published at " + str(events.installed) +
                                            ". Final synchronization or cleanup was interrupted; keep this app and check it before removing other copies.")
                            except OSError:
                                pass  # A full log must not prevent reporting the published app.
                    if events.report_committed():
                        return 0
                    log.write(traceback.format_exc().encode())
                    raise
            return 0
        if args.stage_only:
            if not args.stage:
                raise SetupError("--stage-only requires --stage.")
            stage_path = clean_path(args.stage)
            args.work = args.work or str(stage_path.parent / ("." + stage_path.name + "-work"))
        elif not (args.pc and args.dest and args.work) or args.stage:
            raise SetupError("--check and --build require --pc, --dest and --work; --stage requires --stage-only.")
        paths = Paths(args.work)
        if args.stage_only:
            choices = None if args.pc and args.x360 and args.dest else staging.load_choices(paths.state, required=not args.pc)
            remembered_destination = not args.dest
            if not args.pc and choices:  # the disc images' update and speed.exe go with the remembered images
                args.pc_update = args.pc_update or choices.get("pc_update")
                args.pc_exe = args.pc_exe or choices.get("pc_exe")
                args.pc_disc = args.pc_disc or choices.get("pc_discs")
            args.pc = args.pc or (choices and choices["pc"])
            args.x360 = args.x360 or (choices and choices["x360"])
            args.dest = args.dest or (choices and choices["destination"])
            if not args.dest:
                raise SetupError("--stage-only requires --dest or a remembered destination.")
            if remembered_destination:
                staging.validate_destination(clean_path(args.dest), required=True)
        extras = [clean_path(p) for p in [args.pc_update, args.pc_exe] + (args.pc_disc or []) if p]
        if args.stage_only:
            check_stage_locations(args.stage, paths, clean_path(args.pc), clean_path(args.x360) if args.x360 else None,
                                  clean_path(args.dest), ROOT, extras)
        # Validate before creating anything at a user-selected path.
        check_locations(paths, clean_path(args.pc), clean_path(args.x360) if args.x360 else None,
                        clean_path(args.dest).parent if args.stage_only else clean_path(args.dest), ROOT, extras)
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
            log_pointer = paths.work / (".run-log-" + uuid.uuid4().hex)
            try:
                write_synced(log_pointer, (str(log_path) + "\n").encode(), 0o600)
                os.replace(log_pointer, paths.work / "run-log.txt")
            finally:
                log_pointer.unlink(missing_ok=True)
            with cancellation(), exclusive(paths.work / ".setup.lock"), tool_tmp(paths.work) as tmp:
                env = child_environment(paths.work / "stage/roots.txt", paths.work, tmp=tmp)
                runner = Runner(log, env, paths.work)
                try:
                    installed = build(args, paths, events, runner)
                except (Exception, KeyboardInterrupt) as e:
                    if events.report_committed():
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
                        if args.stage_only:
                            events.emit("ready", descriptor=str(installed))
                        else:
                            events.emit("done", app=str(installed))
                return 0
    except (Exception, KeyboardInterrupt) as e:
        if events is not None and events.report_committed():
            return 0
        # Includes failures before a log can be opened. stdout remains parseable.
        obj = dict(ev="error", id=events.current if events else ("cars" if car_mode else "preflight"), msg=str(e) or "Setup was interrupted.", fix=getattr(e, "fix", "Choose a writable work directory and try again."), log=str(log_path or ""))
        print(json.dumps(obj) if args.events else obj["msg"], flush=True)
        return 130 if isinstance(e, (Cancelled, KeyboardInterrupt)) else 1


if __name__ == "__main__":
    sys.exit(main())
