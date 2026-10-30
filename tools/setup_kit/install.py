"""Publish a verified app, immutable data generation and preferences as one transaction.

All copies and signature checks finish before replacement. Catchable interruption
rolls back every rename, including app.txt (only game_dir is changed). The old app
waits under a uniquely named backup until the commit and is removed after it (a
3.x app is kept under its visible 3.x name). After a commit only this generation
and the one it replaced are kept in <data>/installs. Saves are never rewritten.

Crash safety: a journal (<data>/.install-journal.json) records the
intent before anything is created and the identity (device, inode) of every file
it will move before the first rename; it is removed after the last step. Every
rename is exclusive (renamex_np RENAME_EXCL: it never replaces anything), every
backup name carries this run's random token, and rollback/recovery only moves or
deletes a path whose identity is the one recorded. The next Setup run reads a
leftover journal and restores the previous app, roots and preferences, or, after
the commit point, finishes the install. Either way: old complete or new complete.
"""
from contextlib import contextmanager
import ctypes
import errno
import fcntl
import json
import hashlib
import os
from pathlib import Path
import plistlib
import re
import shutil
import signal
import stat
import subprocess
import uuid


class InstallError(Exception):
    def __init__(self, msg, fix="Quit the game and try Setup again."):
        super().__init__(msg)
        self.fix = fix


def exists(path):
    return path.exists() or path.is_symlink()


def remove(path):
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif exists(path):
        path.unlink()


def digest_tree(root):
    """Content, relative names and execute bits; never follow a symlink."""
    root = Path(root)
    h = hashlib.sha256()
    paths = [root] if root.is_file() else sorted(root.rglob("*"))
    for p in paths:
        if p.is_symlink():
            raise InstallError(f"Unexpected symbolic link: {p}", "Choose a regular installed game folder.")
        if p.is_file():
            h.update(str(p.relative_to(root) if p != root else p.name).encode())
            h.update(b"\0" + str(p.stat().st_mode & 0o111).encode() + b"\0")
            with p.open("rb") as f:
                for block in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(block)
            h.update(b"\0")
        elif not p.is_dir():
            raise InstallError(f"Not a regular file: {p}")
    return h.hexdigest()


RENAME_EXCL = 0x00000004
_libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)


def rename_new(src, dst):
    """Atomically rename src to dst; fails with EEXIST instead of replacing anything already at dst."""
    if _libc.renamex_np(os.fsencode(src), os.fsencode(dst), RENAME_EXCL) != 0:
        e = ctypes.get_errno()
        raise OSError(e, os.strerror(e), str(src), None, str(dst))


def exchange(src, dst):
    """Exchange two existing sibling names atomically; never fall back to copy/delete."""
    if Path(src).parent != Path(dst).parent:
        raise InstallError("Atomic replacement requires sibling paths.")
    if _libc.renamex_np(os.fsencode(src), os.fsencode(dst), 0x00000002) != 0:  # RENAME_SWAP
        e = ctypes.get_errno()
        raise OSError(e, os.strerror(e), str(src), None, str(dst))


def ident(path):
    """Filesystem identity of path itself (not a link target), or None when nothing is there."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    return [st.st_dev, st.st_ino]


def _step(name):
    """Crash-test hook: called between every pair of install steps (tests replace it with os._exit)."""


def write_synced(path, data, mode=0o644):
    with open(path, "xb") as f:
        os.chmod(path, mode)
        f.write(data)
        f.flush()
        full_sync(f.fileno())


def full_sync(fd):
    """Drain kernel writes AND the device's volatile write cache (macOS)."""
    os.fsync(fd)
    fcntl.fcntl(fd, fcntl.F_FULLFSYNC)


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        full_sync(fd)
    finally:
        os.close(fd)


def sync_tree(root):
    for base, dirs, files in os.walk(root, topdown=False):
        for name in files:
            with open(Path(base) / name, "rb") as f:
                full_sync(f.fileno())
        sync_dir(base)


def sync_volumes(profile, destination):
    # These directories can be on different devices. Flush both before a
    # transaction crosses its exchange or backup-deletion boundary.
    sync_dir(profile)
    sync_dir(destination)


def updated_profile(original, pc_root):
    # Preserve unknown keys, ordering, comments, encodings and all non-game_dir
    # bytes. The runtime trims whitespace around keys and uses the first match.
    value = os.fsencode(pc_root)
    lines, found = [], False
    for line in original.splitlines(keepends=True):
        if line.partition(b"=")[0].strip() == b"game_dir":
            ending = b"\r\n" if line.endswith(b"\r\n") else b"\n"
            lines.append(b"game_dir=" + value + ending)
            found = True
        else:
            lines.append(line)
    if not found:
        if lines and not lines[-1].endswith(b"\n"):
            lines.append(b"\n")
        lines.append(b"game_dir=" + value + b"\n")
    return b"".join(lines)


APP_NAME = "NFS Most Wanted Native"
APP_BUNDLE = APP_NAME + ".app"
EXEC_NAME = "NFSMostWanted"
# 3.x used this bundle id too, so 4.0 keeps it (Dock icon and Launch Services continuity). It is never the owner
# build's id (local.nfsmw-port).
PUBLIC_BUNDLE_ID = "io.github.nfsmw-native-mac"
OWNER_BUNDLE_ID = "local.nfsmw-port"
RUNNING_APP = re.compile(r"NFSMW\.app/Contents/MacOS/|NFS Most Wanted Native(?: 3\.x(?: \(\d+\))?)?\.app/Contents/MacOS/")


def public_app_path(dest):
    """The only app Setup may replace or rename: 'NFS Most Wanted Native.app' directly inside dest."""
    target = Path(dest) / APP_BUNDLE
    if target.name != APP_BUNDLE or target.parent != Path(dest):
        raise InstallError("Refusing to touch an app other than " + APP_BUNDLE + ".")
    return target


def is_current_app(app):
    """True for an installed 4.x public app (safe to replace). Anything else, 3.x or unknown, is kept."""
    try:
        with (Path(app) / "Contents/Info.plist").open("rb") as f:
            info = plistlib.load(f)
        major = int(str(info["CFBundleShortVersionString"]).split(".")[0])
        return info["CFBundleIdentifier"] == PUBLIC_BUNDLE_ID and major >= 4
    except (OSError, ValueError, KeyError, TypeError, plistlib.InvalidFileException):
        return False


def legacy_name(dest):
    """First free 'NFS Most Wanted Native 3.x.app', then ' (2)', ' (3)'..."""
    dest = Path(dest)
    path = dest / f"{APP_NAME} 3.x.app"
    n = 2
    while exists(path):
        path = dest / f"{APP_NAME} 3.x ({n}).app"
        n += 1
    return path


def assert_not_running(processes=None):
    if processes is None:
        try:
            result = subprocess.run(["/bin/ps", "-axo", "pid=,comm="], capture_output=True, text=True, check=True)
        except (OSError, subprocess.CalledProcessError) as e:
            raise InstallError("Setup could not check whether the game is running.",
                               "Allow process inspection, quit the game and run Setup again.") from e
        processes = result.stdout
    for line in processes.splitlines():
        if RUNNING_APP.search(line):
            raise InstallError("NFS Most Wanted is running.", "Quit every copy of the game before installing.")


@contextmanager
def profile_guard(data):
    data.mkdir(parents=True, exist_ok=True)
    lock = data / ".profile.lock"
    if lock.is_symlink():
        raise InstallError("The profile lock is a symbolic link.")
    with lock.open("a+b") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise InstallError("The game is using this profile.", "Quit the game before installing.") from None
        yield


@contextmanager
def defer_interrupts():
    """Do not allow a second Cancel to interrupt rollback itself."""
    old = {s: signal.signal(s, signal.SIG_IGN) for s in (signal.SIGINT, signal.SIGTERM)}
    try:
        yield
    finally:
        for s, handler in old.items():
            signal.signal(s, handler)


JOURNAL = ".install-journal.json"


def journal_path(data_home):
    return Path(data_home) / JOURNAL


def write_journal(data_home, record):
    path = journal_path(data_home)
    tmp = path.with_name(f".{JOURNAL}.tmp-{record['token']}")
    if exists(tmp):
        tmp.unlink()
    write_synced(tmp, json.dumps(record, sort_keys=True).encode())
    os.replace(tmp, path)  # our own journal: replacing the previous version of it is the point
    sync_dir(path.parent)


def _plan(data_home, record):
    """The paths a journal may touch, derived from its token: nothing else is ever moved or deleted."""
    token, dest = record["token"], Path(record["dest"])
    if not re.fullmatch(r"[0-9a-f]{32}", token):
        raise InstallError("The install journal is damaged.", "Remove " + str(journal_path(data_home)) + " and run Setup again.")
    target = dest / APP_BUNDLE
    names = [(target, dest / f".{APP_BUNDLE}.new-{token}", dest / f".{APP_BUNDLE}.backup-{token}")]
    for name in ("roots.txt", "app.txt"):
        path = data_home / name
        names.append((path, path.with_name(f".{name}.new-{token}"), path.with_name(f".{name}.backup-{token}")))
    generation = data_home / "installs" / token
    return names, generation, generation.with_name(".new-" + token)


def _rollback(data_home, record):
    """Undo an uncommitted install: put back each original whose recorded identity is at its backup name."""
    names, generation, generation_tmp = _plan(data_home, record)
    ids = record.get("ids") or {}
    problems = []
    for path, new, backup in reversed(names):
        old_id, new_id = ids.get(str(path), [None, None])
        if new_id is not None and ident(path) == new_id:
            remove(path)  # only our new copy: never the original
            _step("rollback-remove")
        if old_id is not None and ident(backup) == old_id:
            if exists(path):
                problems.append(str(backup))  # something unknown took the name: keep both untouched
                continue
            rename_new(backup, path)
            sync_dir(path.parent)
            _step("rollback-restore")
        elif old_id is not None and ident(path) != old_id:
            problems.append(str(path))
        remove(new)
    for path in (generation, generation_tmp):
        remove(path)
    _step("rollback-generation")
    if problems:
        raise InstallError("Setup could not restore every file of your previous version: " + ", ".join(problems),
                           "Your previous files are kept under the names above; move them back, then run Setup again.")


def codesign_ok(app):
    """The app's signature verifies (ad hoc is fine). Replaced by tests: the fixtures' apps are unsigned."""
    try:
        return subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)],
                              capture_output=True, timeout=300).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def app_complete(app):
    """The installed app is whole: Info.plist names an executable that is a regular, executable, non-empty file
    (not a link), and the signature verifies."""
    try:
        with (Path(app) / "Contents/Info.plist").open("rb") as f:
            name = plistlib.load(f)["CFBundleExecutable"]
        exe = Path(app) / "Contents/MacOS" / name
        st = os.lstat(exe)
        if name != os.path.basename(name) or not stat.S_ISREG(st.st_mode) or not st.st_size or not os.access(exe, os.X_OK):
            return False
    except (OSError, ValueError, KeyError, TypeError, plistlib.InvalidFileException):
        return False
    return codesign_ok(app)


def _placed(path, new_id):
    """True when path is the file/app this install put there (identity recorded before the rename)."""
    return new_id is not None and ident(path) == new_id and exists(path)


def _finish(data_home, record, log=None, allow_rollback=True):
    """Complete a committed install: drop backups, give a kept 3.x app its visible name.

    A backup is removed only when the name it was replaced by holds this install's own new copy (its recorded
    identity). If the new app is missing or incomplete and allow_rollback, the whole transaction goes back (journal
    state "rolling_back" first, durably) and "restored" is returned. Without allow_rollback (the caller already told
    the user the install succeeded) nothing is removed or rolled back: "unverified" is returned."""
    log = log or (lambda msg: None)
    names, generation, generation_tmp = _plan(data_home, record)
    sync_volumes(data_home, Path(record["dest"]))
    ids = record.get("ids") or {}
    for path, new, backup in names:
        remove(new)
    remove(generation_tmp)
    target, _, app_backup = names[0]
    app_ok = False
    try:
        app_ok = _placed(target, ids.get(str(target), [None, None])[1]) and is_current_app(target) and \
            not target.is_symlink() and app_complete(target)
    except OSError:
        pass
    old_app_id = ids.get(str(target), [None, None])[0]
    if not app_ok and not allow_rollback:
        log(f"{target} could not be verified after the commit was reported; the new install was left as it is "
            "and the backups were kept")
        return "unverified"
    if not app_ok and old_app_id is not None and ident(app_backup) == old_app_id:
        # The new app is missing or incomplete: the whole transaction goes back (old app AND old roots.txt/app.txt
        # together, new generation removed). Backups stay until every restore is done; an unknown file at a name
        # keeps the journal and both copies (the rollback raises).
        log(f"{target} is missing or incomplete after the commit; the previous version is being restored")
        record["state"] = "rolling_back"  # durable first: a retry finishes the rollback, never re-verifies the new install
        write_journal(data_home, record)
        _step("journal-rolling-back")
        _rollback(data_home, record)
        return "restored"
    for path, new, backup in names:
        old_id, new_id = ids.get(str(path), [None, None])
        if old_id is None or ident(backup) != old_id:
            continue
        if path == target and record.get("keep_legacy"):
            continue  # handled below: a 3.x app is kept for good
        if _placed(path, new_id) if path != target else app_ok:
            remove(backup)
            sync_dir(path.parent)
            _step("finish-backup " + path.name)
        elif not exists(path):
            rename_new(backup, path)  # the new copy is gone: the previous one is better than nothing
            sync_dir(path.parent)
            log(f"{path} was missing after the commit; the previous version was put back")
            _step("finish-restore")
        else:
            log(f"{path} is not this install's copy; its backup {backup} was kept")
    old_id = old_app_id
    if record.get("keep_legacy") and old_id is not None and ident(app_backup) == old_id:
        for _ in range(1000):
            try:
                rename_new(app_backup, legacy_name(target.parent))
                break
            except FileExistsError:
                continue  # taken between the check and the rename: try the next number
        sync_dir(target.parent)
        _step("finish-legacy")
    if app_ok:
        _prune_generations(data_home, record, log)
    else:
        log("the installed app could not be verified after the commit: no install generation was removed")
    return None


OWNER_MARK = ".nfsmw-setup-owned"
GENERATION = re.compile(r"[0-9a-f]{32}")


def real_dir(path):
    """path is a directory reached without any symbolic link (every component is what it says)."""
    p = os.path.abspath(path)
    return os.path.isdir(p) and not os.path.islink(p) and os.path.realpath(p) == p


OWN_DIR = ".setup-owned"


def _own_record(registry, token):
    return Path(registry) / OWN_DIR / token


def owned(folder, registry):
    """A folder Setup made: its marker names this very directory (device and inode) and a per-install token that
    Setup's own state (<registry>/.setup-owned/<token>) records for the same directory. A copied or empty marker,
    or a marker in another folder, is not ownership. The folder is reached without symbolic links."""
    try:
        if not real_dir(folder):
            return False
        marker = os.path.join(folder, OWNER_MARK)
        if os.path.islink(marker) or not os.path.isfile(marker):
            return False
        with open(marker, "rb") as f:
            mark = json.loads(f.read(4096))
        token = mark["token"]
        if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{32}", token):
            return False
        st = os.lstat(folder)
        record = _own_record(registry, token)
        if os.path.islink(record) or not os.path.isfile(record):
            return False
        with open(record, "rb") as f:
            kept = json.loads(f.read(4096))
        return [mark["dev"], mark["ino"]] == [st.st_dev, st.st_ino] == [kept["dev"], kept["ino"]] and kept["token"] == token
    except (OSError, ValueError, KeyError, TypeError):
        return False


def mark_owned(folder, registry):
    """Make folder Setup's own: a fresh token recorded in registry (Setup's state folder) and in a marker that
    holds the folder's identity. Only called for a folder Setup just made, or one it has decided to claim."""
    folder = Path(folder)
    st = os.lstat(folder)
    token = uuid.uuid4().hex
    record = _own_record(registry, token)
    record.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(dict(token=token, dev=st.st_dev, ino=st.st_ino), sort_keys=True).encode()
    write_synced(record, body)
    marker = folder / OWNER_MARK
    if exists(marker):
        marker.unlink()  # an old, copied or empty marker never counted: replaced by one bound to this folder
    write_synced(marker, body)


def remove_owned(folder, registry):
    """Delete an owned folder, keeping the authority to finish until nothing is left: contents first, then the
    marker, the folder, and only then the state record. An interruption leaves it owned (marker and record), so the
    next run removes it; at worst an empty folder remains."""
    folder = Path(folder)
    try:
        with open(folder / OWNER_MARK, "rb") as f:
            token = json.loads(f.read(4096))["token"]
    except (OSError, ValueError, KeyError, TypeError):
        return
    for child in os.listdir(folder):
        if child != OWNER_MARK:
            remove(folder / child)
    _step("prune-contents")
    (folder / OWNER_MARK).unlink()
    os.rmdir(folder)
    if re.fullmatch(r"[0-9a-f]{32}", token):
        _own_record(registry, token).unlink(missing_ok=True)


def referenced_generations(roots_text, data_home):
    """The install generations a roots.txt uses (every key, symbolic links and '..' resolved), or None when that is
    uncertain: a value that names the installs folder but resolves elsewhere or to no generation."""
    installs = os.path.realpath(Path(data_home) / "installs")
    found = set()
    for line in roots_text.splitlines():
        key, eq, value = line.partition("=")
        value = value.strip()
        if not eq or not value or key.strip().startswith("#"):
            continue
        real = os.path.realpath(value)
        lexical = os.path.abspath(value)
        inside = real.startswith(installs + os.sep)
        if not inside:
            if (lexical + os.sep).startswith(installs + os.sep) or "/installs/" in lexical:
                return None  # spelled into installs but not really there: uncertain
            continue
        name = real[len(installs) + 1:].split(os.sep)[0]
        if not GENERATION.fullmatch(name):
            return None
        found.add(name)
    return found


def generation_of(roots_text, data_home):
    """The install generation a roots.txt's pc_root really lies in (resolved), or None."""
    for line in roots_text.splitlines():
        key, _, value = line.partition("=")
        if key.strip() == "pc_root":
            gens = referenced_generations("pc_root=" + value.strip(), data_home)
            return next(iter(gens)) if gens and len(gens) == 1 else None
    return None


def _prune_generations(data_home, record, log=None):
    """After a committed, verified install: keep this generation and the one(s) the replaced roots.txt used
    (rollback), remove older ones. Each rebuild used to add 2.8-7.9 GB there for good (M4 F4).

    Nothing is removed unless every check holds: <data>/installs is a real folder reached without symbolic links,
    the journal says which generations the previous roots.txt used, and the current roots.txt (resolved) uses only
    this install's generation. Only plain children named like a token that Setup itself made and recorded (their
    marker matches Setup's state) are removed: generations of an earlier Setup stay."""
    log = log or (lambda msg: None)
    installs = Path(data_home) / "installs"
    if record.get("previous") is None:
        log("prune skipped: the journal does not say which install generations the previous version used")
        return
    if not real_dir(installs):
        log(f"prune skipped: {installs} is not a plain folder (a link, or reached through one)")
        return
    try:
        current = referenced_generations((Path(data_home) / "roots.txt").read_text(), data_home)
    except (OSError, UnicodeDecodeError):
        current = None
    if current != {record["token"]}:
        log("prune skipped: roots.txt does not use exactly this install's generation")
        return
    keep = {record["token"]} | set(record["previous"])
    for child in sorted(os.listdir(installs)):
        path = installs / child
        if child in keep or not GENERATION.fullmatch(child) or os.path.islink(path) or not os.path.isdir(path):
            continue
        if not owned(path, data_home):
            try:
                os.rmdir(path)  # what an interrupted removal left of a generation: empty and nothing else
            except OSError:
                pass  # not empty: not made and recorded by this Setup, never ours to remove
            continue  # not made and recorded by this Setup: never ours to remove
        remove_owned(path, data_home)
        log(f"removed old install generation {child}")
        _step("prune-generation")


@contextmanager
def dest_lock(dest, held=None):
    """One Setup at a time installs into a destination folder (flock on the folder itself: nothing is written
    there). Two installers with different profiles but one Applications folder are serialized too."""
    dest = Path(dest)
    if held is not None and Path(held) == dest or not dest.is_dir():
        yield
        return
    fd = os.open(dest, os.O_RDONLY)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise InstallError(f"Another Setup is installing into {dest}.", "Wait for it to finish, then try again.") from None
        yield
    finally:
        os.close(fd)


def _recover(data_home, held=None, log=None):
    """Resolve a journal left by an interrupted install. Returns 'restored', 'finished' or None."""
    path = journal_path(data_home)
    if not exists(path):
        return None
    try:
        record = json.loads(path.read_bytes())
        committed = record["state"] == "committed"
        dest = Path(record["dest"])
    except (OSError, ValueError, KeyError, TypeError):
        raise InstallError("The install journal is damaged.", "Remove " + str(path) + " and run Setup again.") from None
    with dest_lock(dest, held), defer_interrupts():
        if record.get("kind") == "launch-update-v1":
            from .apply import recover_locked
            return recover_locked(data_home, record)
        outcome = _finish(data_home, record, log) if committed else None
        if not committed:
            _rollback(data_home, record)
        path.unlink()
        sync_dir(path.parent)
    return outcome or ("finished" if committed else "restored")


def recover_interrupted(data_home, log=None, held=None):
    """For the next Setup run: resolve an interrupted install under the profile lock (None when there was none)."""
    if not exists(journal_path(data_home)):
        return None
    with profile_guard(Path(data_home)):
        return _recover(Path(data_home), held=held, log=log)


def transactional_install(app, staged_data, roots, data_home, dest, verify, running=assert_not_running, on_commit=None,
                          after_commit=None, move_data=False, log=None):
    """Return installed app. verify(path) must reject an invalid/unsigned app.

    roots values point into staged_data and are relocated to a new permanent
    generation. Nothing at an existing manifest path is modified in place.
    """
    app, staged_data, data_home, dest = map(Path, (app, staged_data, data_home, dest))
    token = uuid.uuid4().hex
    target = public_app_path(dest)
    record = dict(token=token, dest=str(dest), state="preparing", keep_legacy=False, ids={})
    names, generation, generation_tmp = _plan(data_home, record)
    candidate = names[0][1]
    committed = False
    dest.mkdir(parents=True, exist_ok=True)
    with profile_guard(data_home), dest_lock(dest):
        _recover(data_home, held=dest, log=log)  # an earlier interrupted install is resolved before anything new starts
        running()
        try:
            dest.mkdir(parents=True, exist_ok=True)
            generation.parent.mkdir(parents=True, exist_ok=True)
            write_journal(data_home, record)  # intent first: an interruption from here on is recoverable
            _step("journal-intent")
            moved = False
            if move_data:
                # Setup's staged data is rebuilt by every run, so it is moved, not copied, on the same volume: the
                # install no longer needs a second 2.8-7.9 GB copy at its peak (M4 F4). Another volume: copied.
                try:
                    rename_new(staged_data, generation_tmp)
                    moved = True
                except OSError as e:
                    if e.errno != errno.EXDEV:
                        raise
            if not moved:
                shutil.copytree(staged_data, generation_tmp)
                if digest_tree(staged_data) != digest_tree(generation_tmp):
                    raise InstallError("The copied game data failed verification.", "Check the source and destination disks.")
            mark_owned(generation_tmp, data_home)  # this generation is Setup's (identity survives the rename): prunable later
            sync_tree(generation_tmp)
            rename_new(generation_tmp, generation)
            sync_dir(generation.parent)
            relocated = {}
            for key, value in roots.items():
                try:
                    relative = Path(value).relative_to(staged_data)
                except ValueError:
                    raise InstallError(f"Root {key} is outside the prepared game data.") from None
                relocated[key] = generation / relative
            if "pc_root" not in relocated:
                raise InstallError("The prepared game has no PC root.")
            shutil.copytree(app, candidate)
            if digest_tree(app) != digest_tree(candidate):
                raise InstallError("The copied app failed verification.", "Check the destination disk.")
            verify(candidate)
            sync_tree(candidate)
            # Prepare every replacement before moving any existing file.
            for (path, new, backup), content in zip(names[1:], (
                "".join(f"{k}={v}\n" for k, v in sorted(relocated.items())).encode(),
                updated_profile((data_home / "app.txt").read_bytes() if (data_home / "app.txt").exists() else b"",
                                relocated["pc_root"]),
            )):
                if path.is_symlink():
                    raise InstallError(f"Refusing to replace symbolic link: {path}")
                write_synced(new, content, (path.stat().st_mode & 0o777) if path.exists() else 0o644)
            if target.is_symlink():
                raise InstallError("The destination app is a symbolic link.", "Choose a different install destination.")
            # A 4.x app is replaced (backup kept). Anything else there (3.x) is kept for good under a visible
            # 3.x name, given only after the commit; until then it waits under this run's unique backup name.
            record["keep_legacy"] = exists(target) and not is_current_app(target)
            record["ids"] = {str(path): [ident(path), ident(new)] for path, new, backup in names}
            # The generations the current roots.txt uses: kept by the prune after commit (one step of rollback).
            # None (uncertain: unreadable, or a root that only seems to be in installs) means nothing is pruned.
            roots_file = data_home / "roots.txt"
            try:
                previous = referenced_generations(roots_file.read_text(), data_home) if exists(roots_file) else set()
            except (OSError, UnicodeDecodeError):
                previous = None
            record["previous"] = sorted(previous) if previous is not None else None
            record["state"] = "replacing"
            running()  # The old copy may have been launched while we copied data.
            write_journal(data_home, record)
            _step("journal-replacing")
            sync_volumes(data_home, dest)
            for path, new, backup in names:
                old_id = record["ids"][str(path)][0]
                if old_id is not None:
                    rename_new(path, backup)  # never onto an existing name; identity recorded above
                    if ident(backup) != old_id:
                        raise InstallError(f"{path} changed while Setup was installing.", "Quit other installers and run Setup again.")
                    _step("backup " + path.name)
                elif exists(path):
                    raise InstallError(f"{path} appeared while Setup was installing.", "Quit other installers and run Setup again.")
                rename_new(new, path)
                sync_dir(path.parent)
                _step("place " + path.name)
            verify(target)
            record["state"] = "committed"
            write_journal(data_home, record)
            _step("journal-committed")
            # Tell the caller at the commit point, with cancellation deferred.
            # A Cancel arriving after this point must report the installed app,
            # rather than claim that an already completed install failed.
            with defer_interrupts():
                if on_commit:
                    on_commit(target)
                committed = True
        except BaseException:
            with defer_interrupts():
                # Never delete an original: only paths whose identity this run recorded are moved back or removed.
                if record["state"] != "committed":
                    try:
                        _rollback(data_home, record)
                    except BaseException:
                        raise  # the journal stays: the next run retries, nothing is deleted on a guess
                    journal_path(data_home).unlink(missing_ok=True)
            raise
        finally:
            if committed:
                with defer_interrupts():
                    # Cleanup must not turn a successful commit into a reported failure.
                    try:
                        _finish(data_home, record, log, allow_rollback=False)
                        journal_path(data_home).unlink()
                        _step("journal-removed")
                    except (OSError, InstallError):
                        pass  # the journal stays; the next run finishes the cleanup
        # Still under the profile lock (the game can't start and write saves meanwhile). The install is complete:
        # a failure here is reported by the callee as a warning, never as a failed install.
        if committed and after_commit:
            with defer_interrupts():
                try:
                    after_commit()
                except Exception:
                    pass
    return target
