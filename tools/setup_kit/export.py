"""Make a relocatable, signed personal backup of an installed app and its assets.

Only a fresh destination is published. The installed app, manifests and saves are
never changed. Clonefile shares APFS blocks; other copies are SHA-256 verified.
"""
import ctypes
import errno
import hashlib
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import time

from . import install as ins

MANIFEST = Path("Contents/Resources/AllInOne/roots.txt")
IDENTITY = Path("Contents/Resources/AllInOne.export")
RUNTIME_CAPABILITY = b"NFS_BUNDLE_ROOTS_V1\0"
ASSET_KEYS = ("pc_root", "x360_root", "assets_root", "movies")
UPDATE_MESSAGE = "Re-export from Setup to update. Keep the previous export to roll back."


def is_export(app):
    return any(os.path.lexists(Path(app) / marker) for marker in (IDENTITY, MANIFEST))


def runtime_supports_export(app):
    # Search the signed executable, never a version number or a resource that
    # could have been added to an older runtime by a newer packaging script.
    with (Path(app) / "Contents/MacOS" / ins.EXEC_NAME).open("rb") as executable:
        tail = b""
        for block in iter(lambda: executable.read(1024 * 1024), b""):
            data = tail + block
            if RUNTIME_CAPABILITY in data:
                return True
            tail = data[-len(RUNTIME_CAPABILITY):]
    return False


def refuse_update(app):
    if is_export(app):
        raise ins.InstallError(UPDATE_MESSAGE, UPDATE_MESSAGE)


def _manifest(path):
    values = {}
    for line in Path(path).read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and not line.startswith("#"):
            values.setdefault(key.strip(), value.strip())
    return values


def current_roots(app, profile):
    """Mirror normal startup's game_dir preference, or the export's own roots."""
    bundled = is_export(app)
    path = Path(app) / MANIFEST if bundled else Path(profile) / "roots.txt"
    if bundled and not path.is_file():
        raise ins.InstallError("The export's asset manifest is missing.", "Re-export from Setup using a complete installation.")
    values = _manifest(path)
    if not bundled and (Path(profile) / "app.txt").exists():
        preferred = _manifest(Path(profile) / "app.txt").get("game_dir")
        if preferred:
            values["pc_root"] = preferred
    roots = {}
    for key in ASSET_KEYS:
        if not values.get(key):
            continue
        value = Path(values[key])
        if bundled:
            if value.is_absolute() or ".." in value.parts:
                raise ins.InstallError("The export's asset manifest is invalid.")
            value = path.parent / value
        elif not value.is_absolute():
            raise ins.InstallError("An installed asset root is not absolute.")
        if not ins.real_dir(value):
            raise ins.InstallError(f"The installed {key} is missing or uses symbolic links.")
        roots[key] = value.resolve()
    if "pc_root" not in roots:
        raise ins.InstallError("The current installation has no PC asset root.", "Run Setup before exporting.")
    return roots


def clone_file(source, destination):
    if ins._libc.clonefile(os.fsencode(source), os.fsencode(destination), 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(source))


def _sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.digest()


def _walk_error(error):
    raise error


def copy_tree(source, destination, metrics, clone=clone_file, exclude=()):
    """No links/special files: the resulting backup must contain every byte."""
    source, destination = Path(source), Path(destination)
    destination.mkdir()
    for base, dirs, files in os.walk(source, followlinks=False, onerror=_walk_error):
        out = destination / Path(base).relative_to(source)
        dirs[:] = [name for name in dirs if (Path(base) / name).relative_to(source) not in exclude]
        for name in dirs + files:
            src, dst = Path(base) / name, out / name
            if src.relative_to(source) in exclude:
                continue
            before = src.lstat()
            if stat.S_ISDIR(before.st_mode):
                dst.mkdir()
                continue
            if not stat.S_ISREG(before.st_mode):
                raise ins.InstallError(f"Cannot export a link or special file: {src}")
            copied = False
            try:
                # clonefile itself rejects cross-volume and unsupported filesystems.
                clone(src, dst)
                metrics["cloned_files"] += 1
            except OSError as error:
                if error.errno not in (errno.EXDEV, errno.ENOTSUP, errno.EINVAL, errno.ENOSYS):
                    raise
                if os.path.lexists(dst):
                    raise ins.InstallError("A clone destination unexpectedly exists.") from error
                shutil.copy2(src, dst)
                if _sha(src) != _sha(dst):
                    raise ins.InstallError("An exported copy failed its SHA-256 check.")
                metrics["copied_files"] += 1
                copied = True
            after = src.lstat()
            if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) !=
                    (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
                raise ins.InstallError("An asset changed during export.", "Quit file-copy or mod tools and try again.")
            metrics["transferred_bytes"] += before.st_size
            if copied:
                metrics["digest_verified_bytes"] += before.st_size


def sign_bundle(app):
    try:
        subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", str(app)],
                       check=True, capture_output=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as error:
        raise ins.InstallError("The exported app could not be signed.",
                               "Check destination access and free space, then export again.") from error
    if not ins.codesign_ok(app):
        raise ins.InstallError("The exported app's signature did not verify.")


def export_app(app, profile, destination, clone=clone_file, verify=None, sign=sign_bundle, on_publish=None):
    """Return measurements; notify on_publish at rename, before fallible final sync/cleanup."""
    started = time.monotonic()
    app, profile, destination = map(lambda p: Path(p).absolute(), (app, profile, destination))
    if destination.suffix != ".app" or not ins.real_dir(destination.parent):
        raise ins.InstallError("Choose a new .app in an existing ordinary folder.")
    if ins.exists(destination):
        raise ins.InstallError("The export destination already exists.", "Choose a new name; keep the previous export for rollback.")
    if not ins.real_dir(app) or not ins.is_current_app(app):
        raise ins.InstallError("Choose an installed NFS Most Wanted Native app.")
    verify = verify or (lambda p: ins.app_complete(p))
    metrics = dict(cloned_files=0, copied_files=0, transferred_bytes=0, digest_verified_bytes=0)
    with ins.profile_guard(profile), ins.dest_lock(app.parent):
        if ins.journal_path(profile).exists():
            raise ins.InstallError("An installation needs recovery.", "Run Setup to recover it before exporting.")
        if not verify(app):
            raise ins.InstallError("The installed app's signature did not verify.")
        if not runtime_supports_export(app):
            raise ins.InstallError("The installed runtime does not support all-in-one export.",
                                   "Update the ordinary installation with Setup, then export again.")
        roots = current_roots(app, profile)
        protected = [app.resolve(), profile.resolve(), *roots.values()]
        parent = destination.parent.resolve()
        if any(parent == p or parent.is_relative_to(p) for p in protected):
            raise ins.InstallError("The export destination is inside the app, profile or game data.")
        free_before = shutil.disk_usage(parent).free
        with tempfile.TemporaryDirectory(prefix=".all-in-one-", dir=parent) as tmp:
            staged = Path(tmp) / destination.name
            copy_tree(app, staged, metrics, clone, exclude=(MANIFEST.parent, IDENTITY))
            embedded = staged / MANIFEST.parent
            if embedded.exists():
                shutil.rmtree(embedded)  # re-export uses a fresh embedded snapshot
            embedded.mkdir(parents=True)
            relative = {}
            # Ancestor roots first: preserve nested/identical roots without duplicating bytes.
            owners = []
            for key, source in sorted(roots.items(), key=lambda item: len(item[1].parts)):
                containing = next(((root, name) for root, name in owners if source.is_relative_to(root)), None)
                if containing:
                    root, name = containing
                    relative[key] = (Path(name) / source.relative_to(root)).as_posix()
                else:
                    relative[key] = key
                    copy_tree(source, embedded / key, metrics, clone)
                    owners.append((source, key))
            (staged / MANIFEST).write_text("# Bundle-relative asset roots; saves remain in Application Support.\n" +
                                          "".join(f"{k}={v}\n" for k, v in sorted(relative.items())))
            (staged / IDENTITY).write_text("NFS_ALL_IN_ONE_V1\n")
            sign(staged)  # last mutation inside the bundle, as in runtime/bundle.sh
            ins.sync_tree(staged)
            entries = [p.lstat() for p in [staged, *staged.rglob("*")]]
            metrics["logical_bytes"] = sum(s.st_size for s in entries if stat.S_ISREG(s.st_mode))
            metrics["allocated_bytes"] = sum(s.st_blocks * 512 for s in entries)
            # No cancellation gap between publishing and recording success.
            with ins.defer_interrupts():
                ins.rename_new(staged, destination)
                if on_publish:
                    on_publish(destination)
            ins.sync_dir(parent)
        metrics["volume_used_delta_bytes"] = free_before - shutil.disk_usage(parent).free
    metrics["seconds"] = round(time.monotonic() - started, 3)
    return metrics
