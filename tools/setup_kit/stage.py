"""Prepare update artifacts without acquiring or modifying the game profile.

Plan: remember inputs after installation; copy verified build outputs into a new
job directory, bind their relative roots and provenance, fsync everything, then
atomically publish ready.json. Application/recovery belongs to the installer.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import uuid

from .install import (InstallError, defer_interrupts, digest_tree, ident, is_current_app,
                      real_dir, sync_dir, write_synced)


def load_choices(state, required=False):
    path = Path(state) / "source-choices.json"
    try:
        value = json.loads(path.read_text())
        if (value.get("format_version") != 1 or "x360" not in value or not isinstance(value.get("pc"), str)
                or not isinstance(value.get("destination"), str)
                or not (value["x360"] is None or isinstance(value["x360"], str) and Path(value["x360"]).is_absolute())
                or not Path(value["pc"]).is_absolute() or not Path(value["destination"]).is_absolute()):
            raise ValueError("invalid choices")
        return value
    except FileNotFoundError:
        if not required:
            return None
        raise InstallError("SOURCE_CHOICES_MISSING: Setup has no remembered source choices.",
                           "Run Setup successfully once, or supply --pc, --x360 and --dest.") from None
    except (ValueError, AttributeError, TypeError) as e:
        raise InstallError("SOURCE_CHOICES_INVALID: Setup's remembered choices are damaged.",
                           "Run Setup again to select your original sources.") from e


def save_choices(state, pc, x360, destination):
    state = Path(state)
    state.mkdir(parents=True, exist_ok=True)
    temporary = state / (".source-choices-" + uuid.uuid4().hex)
    try:
        write_synced(temporary, json.dumps(dict(format_version=1, pc=str(pc),
                     x360=str(x360) if x360 else None, destination=str(destination)), sort_keys=True).encode(), 0o600)
        os.replace(temporary, state / "source-choices.json")
        sync_dir(state)
    finally:
        temporary.unlink(missing_ok=True)


def sync_tree(root):
    """Flush file contents and directory entries before publishing readiness."""
    for base, dirs, names in os.walk(root, topdown=False):
        for name in names:
            with open(Path(base) / name, "rb") as f:
                os.fsync(f.fileno())
        sync_dir(base)


def validate_destination(destination, required=False):
    """Remembered paths must still name an installed public app before any hashing."""
    destination = Path(destination)
    if not required and not os.path.lexists(destination):
        return  # Explicit destinations may prepare a first installation.
    if not real_dir(destination) or not is_current_app(destination):
        raise InstallError(f"STAGE_DESTINATION_INVALID: The installed app is missing or unrecognized: {destination}",
                           "Reselect the installed NFS Most Wanted Native.app with --dest.")


def publish(target, app, data, roots, destination, profile, version, provenance, on_commit=None):
    """A fresh directory only: failed/retried jobs can never inherit stale readiness.

    Roots are relative to the descriptor's directory. Consumers must verify the
    recorded hashes and destination identity again before applying the update.
    """
    target, app, data, destination = map(Path, (target, app, data, destination))
    relative = {key: (Path("data") / Path(value).relative_to(data)).as_posix() for key, value in roots.items()}
    if "pc_root" not in relative:
        raise InstallError("The prepared update has no PC root.")
    # Capture the active destination without writing it or acquiring its locks.
    validate_destination(destination)
    destination_record = dict(path=str(destination), identity=ident(destination),
                              sha256=digest_tree(destination) if destination.exists() else None)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.mkdir(mode=0o700)  # refuses existing jobs, including partial or ready jobs
    ready = target / "ready.json"
    committed = False
    try:
        shutil.copytree(app, target / app.name)
        shutil.copytree(data, target / "data")
        hashes = dict(app=digest_tree(target / app.name), data=digest_tree(target / "data"))
        if hashes != dict(app=digest_tree(app), data=digest_tree(data)):
            raise InstallError("The staged update copy failed verification.", "Check the staging disk and retry in a new directory.")
        roots_body = "".join(f"{key}={value}\n" for key, value in sorted(relative.items())).encode()
        write_synced(target / "roots.txt", roots_body)
        hashes["roots"] = hashlib.sha256(roots_body).hexdigest()
        record = dict(format_version=1, version=version, app=app.name, data="data", roots=relative,
                      hashes=hashes, destination=destination_record, profile=str(profile), provenance=provenance)
        sync_tree(target)
        sync_dir(target.parent)
        # The descriptor is the commit marker, never visible partially written.
        write_synced(target / ".ready.json.tmp", json.dumps(record, sort_keys=True).encode())
        # Keep cancellation from splitting durable publication and its notification.
        # Copying and hashing above remain cancellable.
        with defer_interrupts():
            os.rename(target / ".ready.json.tmp", ready)
            sync_dir(target)
            committed = True
            if on_commit:
                on_commit(ready)
    except BaseException:
        if not committed:
            ready.unlink(missing_ok=True)
        raise
    return ready
