"""Launch-time update transaction and its independent startup supervisor.

Plan: verify the ready descriptor under Setup's existing locks, prepare immutable
copies, journal identities, exchange sibling names, then retain the old app until
startup acknowledgment. Recovery runs before reading roots and never touches saves.

The verified extraction must remain at JOB/source and JOB/python (relocatable
Python), with the fresh staging output at JOB/staged, through recovery. A flat
JOB/ready.json layout is also accepted. The verified preparer calls publish_ready
to atomically publish the profile's private .update-ready.json index. Launch
integration invokes this file with -I -B, --profile, --destination, --parent and
optionally --ready. Bytecode writes must not change the pinned tools. No network
is used.
"""
import argparse
import base64
from contextlib import ExitStack, nullcontext
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import time
import uuid
from xml.parsers.expat import ExpatError

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from setup_kit import install as ins
    from setup_kit import update_manifest as updates
    from setup_kit.export import refuse_update
else:
    from . import install as ins
    from . import update_manifest as updates
    from .export import refuse_update

KIND = "launch-update-v1"
ACK_SECONDS = 60
READY_INDEX = ".update-ready.json"


def publish_ready(ready, profile, destination):
    """Called by the preparer AFTER verifying the retained source/Python extraction.

    The private profile index anchors tool hashes outside the retained job. No
    launch scans historical jobs or trusts hashes supplied by an untrusted job.
    Publishing another entry replaces the previous pending entry for this profile.
    """
    ready, profile, destination = map(Path, (ready, profile, destination))
    read_ready(ready, profile, destination)
    job = ready.parent.parent if ready.parent.name == "staged" else ready.parent
    if not ins.real_dir(job) or stat.S_IMODE(job.stat().st_mode) != 0o700 or job.stat().st_uid != os.getuid():
        raise ins.InstallError("The update job must be a private owned directory.")
    tools = {name: ins.digest_tree(job / name) for name in ("source", "python")
             if ins.real_dir(job / name)}
    if len(tools) != 2:
        raise ins.InstallError("The retained update tools are missing.")
    record = dict(format_version=1, profile=str(profile), destination=str(destination),
                  ready=str(ready), job=str(job), tools=tools)
    # Publishing an atomic announcement does not modify active game state.
    # Preparation is allowed while the playing process holds the profile lock.
    with ins.defer_interrupts():
        path = profile / READY_INDEX
        temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
        ins.write_synced(temporary, json.dumps(record, sort_keys=True).encode(), 0o600)
        os.replace(temporary, path)
        ins.sync_dir(profile)
    return record


def _indexed_tools(profile, ready, job):
    path = profile / READY_INDEX
    if not path.exists():
        return None  # direct transaction callers do not use a launch payload
    record = json.loads(path.read_bytes())
    if record.get("ready") != str(ready) or record.get("job") != str(job):
        raise ins.InstallError("The ready index does not match this update job.")
    tools = record["tools"]
    if tools != {name: ins.digest_tree(job / name) for name in ("source", "python")}:
        raise ins.InstallError("The retained update tools changed after preparation.")
    return tools


def _step(name):
    """Fault injection boundary after each durable operation (tests kill here)."""


def _sync_tree(root):
    for base, dirs, files in os.walk(root, topdown=False):
        for name in files:
            with open(Path(base) / name, "rb") as f:
                ins.full_sync(f.fileno())
            _step("sync-file-" + (Path(base) / name).relative_to(root).as_posix())
        ins.sync_dir(base)
        _step("sync-directory-" + Path(base).relative_to(root).as_posix())


def _inside(base, name):
    if not isinstance(name, str) or not name or "\n" in name or "\r" in name:
        raise ins.InstallError("Invalid staged path.")
    relative = PurePosixPath(name)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ins.InstallError("A staged path escapes the update job.")
    path = base / name
    if not ins.real_dir(path):
        raise ins.InstallError("A staged directory is missing or uses symbolic links.")
    return path


def verify_app(app, bundle_id=ins.PUBLIC_BUNDLE_ID):
    if not ins.is_current_app(app, bundle_id) or not ins.app_complete(app):
        raise ins.InstallError("The update app is incomplete or its signature is invalid.")


def _version_parts(version):
    return updates.version_parts(version)


def _app_version(app):
    with (app / "Contents/Info.plist").open("rb") as info:
        return plistlib.load(info)["CFBundleShortVersionString"]


def _small_file(path, limit):
    """Bound retained metadata reads and refuse links/special files before reading."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
            raise ValueError("invalid update metadata file")
        body = source.read(limit + 1)
        if len(body) > limit:
            raise ValueError("oversized update metadata")
        return body


def _product_name(destination):
    ins.product(destination)
    return "mw" if Path(destination).name == ins.APP_BUNDLE else "carbon"


def highest_healthy(profile, product):
    """Missing state is a first install; malformed existing state never resets trust."""
    path = Path(profile) / (".update-healthy-" + product + ".json")
    try:
        body = _small_file(path, 1024)
        record = json.loads(body)
        if (record.get("format_version") != 1 or record.get("product") != product
                or set(record) != {"format_version", "product", "version"}):
            raise ValueError("invalid healthy-version record")
        _version_parts(record["version"])
        return record["version"]
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError, AttributeError) as error:
        raise ValueError(f"Invalid healthy-version record {path}. Delete this file to reset update history.") from error


def _proof_files(job):
    return set(job.glob("*.manifest")) | set(job.glob("*.manifest.sig"))


def verify_update(job, profile, destination, target):
    """Reauthenticate the retained release at both preparation and apply boundaries.

    The only unsigned migration is a destination at/below the last pre-F4
    version with BOTH sidecars absent. A supplied proof always has to verify.
    Caller serializes healthy state with the existing destination lock.
    """
    job, profile, destination = map(Path, (job, profile, destination))
    try:
        product = _product_name(destination)
        installed = _app_version(destination)
        target_parts, installed_parts = _version_parts(target), _version_parts(installed)
        healthy = highest_healthy(profile, product)
        if target_parts <= installed_parts or (healthy is not None and target_parts <= _version_parts(healthy)):
            raise ValueError("target version must be newer than installed and highest healthy versions")
        binding = job / "target-version.json"
        if os.path.lexists(binding):
            if json.loads(_small_file(binding, 16384))["version"] != target:
                raise ValueError("target version binding differs from the release")
        repo, prefix = updates.PRODUCTS[product]
        name = f"{prefix}-v{target}.dmg"
        manifest, signature = job / (name + ".manifest"), job / (name + ".manifest.sig")
        supplied = _proof_files(job)
        if not supplied:
            cutoff = "4.10.0" if product == "mw" else "1.3.0"
            if installed_parts <= _version_parts(cutoff):
                return None
            raise ValueError("publisher proof is required after the bootstrap version")
        if supplied != {manifest, signature}:
            raise ValueError("missing or unexpected update proof files")
        release = updates.verify(_small_file(manifest, updates.MANIFEST_MAX),
                                 _small_file(signature, updates.SIGNATURE_MAX),
                                 repo=repo, product=product, tag="v" + target)
        # The retained Setup source independently carries its own product version.
        from setup_kit import backend
        setup_version = backend.PUBLIC_VERSION if product == "mw" else backend.CARBON_VERSION
        if release.version != target or setup_version != target or not os.path.lexists(binding):
            raise ValueError("signed version, Setup version and target binding must match")
        for asset in (release.dmg, release.checksum):
            path = job / asset.name
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("unsafe retained update asset")
            updates.verify_asset(path, asset)
        checksum = _small_file(job / release.checksum.name, 4096)
        match = re.fullmatch(rb"([0-9a-fA-F]{64})  " + re.escape(name.encode("ascii")) + rb"\n?", checksum)
        if match is None or match[1].decode("ascii").lower() != release.dmg.sha256:
            raise ValueError("invalid release checksum entry")
        return release
    except (OSError, ValueError, KeyError, TypeError, AttributeError, ExpatError) as error:
        raise ins.InstallError("Update proof/version verification failed: " + str(error),
                               "Discard this update and prepare it again.") from error


def _advance_healthy(profile, record):
    """Only acknowledged journals may advance state; recovery repeats this atomically."""
    if record["state"] != "acknowledged":
        raise ins.InstallError("An update cannot become healthy before acknowledgment.")
    app = Path(record["dest"]) / record.get("bundle", ins.APP_BUNDLE)
    try:
        product = _product_name(app)
        version = _app_version(app)
        _version_parts(version)
        if version != record.get("version", version):
            raise ValueError("acknowledged app version changed")
        try:
            healthy = highest_healthy(profile, product)
        except ValueError as error:
            # The acknowledged app is authoritative even if the previous floor
            # was damaged. Finish cleanup and replace it with this healthy version.
            print(str(error))
            healthy = None
        if healthy is not None and _version_parts(version) <= _version_parts(healthy):
            return
        path = Path(profile) / (".update-healthy-" + product + ".json")
        temporary = path.with_name(path.name + ".tmp-" + record["token"])
        ins.remove(temporary)
        body = json.dumps(dict(format_version=1, product=product, version=version), sort_keys=True).encode()
        ins.write_synced(temporary, body, 0o600)
        os.replace(temporary, path)
        ins.sync_dir(profile)
        _step("healthy-version-written")
    except (OSError, ValueError, KeyError, TypeError, AttributeError, ExpatError) as error:
        raise ins.InstallError("Cannot record the healthy update version: " + str(error)) from error


def _check_versions(ready, descriptor, app, destination):
    target = descriptor["version"]
    job = ready.parent.parent if ready.parent.name == "staged" else ready.parent
    # A custom --stage name also keeps retained assets in its parent. Flat
    # jobs keep proof beside ready.json; conflicting supplied proof refuses.
    if ready.parent.name != "staged" and _proof_files(job.parent):
        if _proof_files(job):
            raise ValueError("ambiguous update proof location")
        binding = job / "target-version.json"
        if os.path.lexists(binding) and json.loads(_small_file(binding, 16384))["version"] != target:
            raise ValueError("target version binding differs from the release")
        job = job.parent
    verify_update(job, Path(descriptor["profile"]), destination, target)
    if _app_version(app) != target:
        raise ins.InstallError("The staged app must match the target version and be newer than the installed app.",
                               "Prepare the update again.")


def _check_ready_location(ready, profile, job=None):
    ready, profile = Path(ready), Path(profile)
    if (not ready.is_absolute() or ready.name != "ready.json"
            or ready.resolve().is_relative_to(profile.resolve())):
        raise ins.InstallError("Update staging must be outside the game profile.")
    if job is not None and ready.parent not in (Path(job), Path(job) / "staged"):
        raise ins.InstallError("The update journal's job location is damaged.")


def read_ready(ready, profile, destination):
    ready, profile, destination = map(Path, (ready, profile, destination))
    refuse_update(destination)
    _check_ready_location(ready, profile)
    if ready.name != "ready.json" or ready.is_symlink() or not ins.real_dir(ready.parent):
        raise ins.InstallError("Invalid ready descriptor location.")
    try:
        record = json.loads(ready.read_bytes())
        if record["format_version"] != 1 or Path(record["profile"]) != profile:
            raise ValueError("profile or version")
        bound = record["destination"]
        if (not destination.is_absolute() or destination.name not in ins.PRODUCTS
                or not ins.real_dir(destination) or not ins.is_current_app(destination, ins.product(destination)["bundle_id"])
                or Path(bound["path"]) != destination or bound["identity"] != ins.ident(destination)
                or bound["sha256"] != ins.digest_tree(destination)):
            raise ins.InstallError("The installed app changed since this update was prepared.", "Prepare the update again.")
        app, data = _inside(ready.parent, record["app"]), _inside(ready.parent, record["data"])
        if app.name != destination.name or data.name != "data" or app.parent != ready.parent or data.parent != ready.parent:
            raise ValueError("artifact names")
        roots = record["roots"]
        if not isinstance(roots, dict) or "pc_root" not in roots:
            raise ValueError("roots")
        for key, relative in roots.items():
            if not re.fullmatch(r"[a-z][a-z0-9_]*", key) or not _inside(ready.parent, relative).is_relative_to(data):
                raise ValueError("root outside data")
        body = "".join(f"{key}={value}\n" for key, value in sorted(roots.items())).encode()
        root_file = ready.parent / "roots.txt"
        if root_file.is_symlink() or root_file.read_bytes() != body:
            raise ValueError("roots manifest")
        hashes = dict(app=ins.digest_tree(app), data=ins.digest_tree(data), roots=hashlib.sha256(body).hexdigest())
        if record["hashes"] != hashes:
            raise ins.InstallError("The staged update failed hash verification.", "Discard this update and prepare it again.")
        _check_versions(ready, record, app, destination)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, ExpatError) as e:
        raise ins.InstallError("The ready descriptor is invalid: " + str(e)) from e
    return record, app, data


def _journal(profile, record, state):
    record["state"] = state
    ins.write_journal(profile, record)
    _step("journal-" + state)


def _clear(profile):
    ins.journal_path(profile).unlink()
    ins.sync_dir(profile)
    _step("journal-removed")


def _names(profile, record):
    names, generation, temporary = ins._plan(Path(profile), record)
    if record.get("cars") is True:
        path = Path(profile) / "car-catalog/current.json"
        names.append((path, path.with_name(".current.json.new-" + record["token"]), None))
    return names, generation, temporary


def _prepare_cars(ready, profile, descriptor, record, locks):
    if "cars" not in descriptor or descriptor["app"] != ins.APP_BUNDLE:
        return
    from setup_kit import stage

    root = profile / "car-catalog"
    with ExitStack() as guard:
        try:
            base = guard.enter_context(stage.lock_car_update(root))
            body = stage.prepare_car_pointer(ready.parent / "cars", root, base, descriptor["cars"])
        except (OSError, ValueError, KeyError, TypeError):
            print(stage.CAR_REFRESH_KEPT, file=sys.stderr, flush=True)
            return

        record["cars"] = True
        _journal(profile, record, "preparing")  # reserve the pointer candidate for recovery
        path, candidate, _ = _names(profile, record)[0][-1]
        try:
            ins.write_synced(candidate, body, 0o600)
            ins.sync_dir(path.parent)
            _step("prepared-car-pointer")
        except OSError:
            ins.remove(candidate)
            record.pop("cars")
            print(stage.CAR_REFRESH_KEPT, file=sys.stderr, flush=True)
        else:
            locks.enter_context(guard.pop_all())


def _skip_car_exchange(profile, record, path, candidate, old_id, new_id):
    """Preserve the old pointer when its optional exchange could not complete."""
    if record.get("cars") is not True or path != profile / "car-catalog/current.json":
        return False
    from setup_kit import stage

    if ins.ident(path) == new_id and ins.ident(candidate) == old_id:
        ins.exchange(path, candidate)
    if ins.ident(path) != old_id or ins.ident(candidate) != new_id:
        return False
    ins.remove(candidate)
    ins.sync_dir(path.parent)
    del record["cars"], record["ids"][str(path)]
    print(stage.CAR_REFRESH_KEPT, file=sys.stderr, flush=True)
    return True


def _retire_ready(record):
    # Consume exactly this ready marker; a failed health check must not reapply it.
    ready = Path(record["ready"])
    if ins.ident(ready) == record["ready_id"]:
        ready.unlink()
        ins.sync_dir(ready.parent)
    index = Path(record["profile"]) / READY_INDEX if "profile" in record else None
    if index and index.exists():
        entry = json.loads(index.read_bytes())
        if entry.get("ready") == str(ready):
            index.unlink()
            ins.sync_dir(index.parent)
    _step("ready-retired")


def _start_rollback(profile, record):
    # A replacement can have written settings before a failed health check.
    # Keep those settings, changing only game_dir back to the previous root.
    if "rollback_preferences" not in record and record.get("ids"):
        path, candidate, unused = _names(profile, record)[0][2]
        old_id, new_id = record["ids"][str(path)]
        original = candidate if ins.ident(candidate) == old_id and old_id is not None else path
        old = original.read_bytes() if old_id is not None else b""
        current = path.read_bytes() if path.exists() and not path.is_symlink() else old
        if current != old and ins.ident(path) != old_id:
            old_values = [line for line in old.splitlines(keepends=True)
                          if line.partition(b"=")[0].strip() == b"game_dir"]
            value = old_values[0].partition(b"=")[2].rstrip(b"\r\n") if old_values else None
            merged = ins.updated_profile(current, os.fsdecode(value)) if value is not None else b"".join(
                line for line in current.splitlines(keepends=True) if line.partition(b"=")[0].strip() != b"game_dir")
            # If nothing except our root changed, restore the original bytes exactly.
            unchanged = ins.updated_profile(old, os.fsdecode(value)) if value is not None else old
            if merged == unchanged:
                merged = old
            record["rollback_preferences"] = base64.b64encode(merged).decode("ascii")
            record["rollback_preferences_absent"] = old_id is None and not merged
    _journal(profile, record, "rolling_back")


def _rollback(profile, record):
    names, generation, temporary = _names(profile, record)
    restored_prefs = base64.b64decode(record["rollback_preferences"]) if "rollback_preferences" in record else None
    for path, candidate, unused in reversed(names):
        old_id, new_id = record.get("ids", {}).get(str(path), [None, None])
        prefs = path.name == "app.txt"
        merged_already = prefs and restored_prefs is not None and path.is_file() and not path.is_symlink() and path.read_bytes() == restored_prefs
        if merged_already:
            pass
        elif new_id is not None and (ins.ident(path) == new_id or
                prefs and restored_prefs is not None and (old_id is None or ins.ident(candidate) == old_id)
                and path.is_file() and not path.is_symlink()):
            if old_id is None:
                path.unlink()
            elif ins.ident(candidate) == old_id:
                ins.exchange(path, candidate)
            else:
                raise ins.InstallError("The update backup is missing; recovery kept all remaining files.")
            ins.sync_dir(path.parent)
            _step("restore-" + path.name)
        elif old_id is not None and ins.ident(path) != old_id:
            raise ins.InstallError("A destination changed during update recovery; backups were kept.")
        # The candidate name is reserved by the journal before its creation.
        ins.remove(candidate)
        ins.sync_dir(path.parent)
        _step("discard-" + path.name)
    if restored_prefs is not None and not record.get("rollback_preferences_absent") and (
            not (profile / "app.txt").exists() or (profile / "app.txt").read_bytes() != restored_prefs):
        path, candidate, temporary_prefs = names[2]
        ins.remove(temporary_prefs)
        ins.write_synced(temporary_prefs, restored_prefs, path.stat().st_mode & 0o777 if path.exists() else 0o600)
        os.replace(temporary_prefs, path)
        ins.sync_dir(profile)
        _step("restore-latest-preferences")
    for path in (temporary, generation):
        ins.remove(path)
        ins.sync_dir(path.parent)
        _step("discard-generation" + ("-temporary" if path == temporary else ""))
    receipt = ack_path(profile, record["token"])
    receipt.unlink(missing_ok=True)
    receipt.with_name(receipt.name + ".tmp").unlink(missing_ok=True)
    ins.sync_dir(profile)
    _step("discard-receipt")
    _retire_ready(record)
    _clear(profile)
    return "restored"


def _finish(profile, record):
    names, generation, temporary = _names(profile, record)
    # Recovery must validate the acknowledged identities before advancing trust.
    for path, candidate, unused in names:
        if path.name != "app.txt" and ins.ident(path) != record["ids"][str(path)][1]:
            raise ins.InstallError("An acknowledged update changed; its remaining backups were kept.")
    _advance_healthy(profile, record)
    ins.sync_volumes(profile, Path(record["dest"]))
    # Once acknowledged, rollback is forbidden: cleanup may have removed backups.
    for path, candidate, unused in names:
        old_id, new_id = record["ids"][str(path)]
        # app.txt is mutable once startup has begun; runtime preference writes
        # atomically replace its inode. Cleanup never replaces the latest file.
        if path.name != "app.txt" and ins.ident(path) != new_id:
            raise ins.InstallError("An acknowledged update changed; its remaining backups were kept.")
        if ins.ident(candidate) == old_id and old_id is not None:
            ins.remove(candidate)
        elif ins.exists(candidate):
            raise ins.InstallError("An update backup changed; it was kept.")
        ins.sync_dir(path.parent)
        _step("finish-" + path.name)
    _retire_ready(record)
    ack_path(profile, record["token"]).unlink(missing_ok=True)
    ins._prune_generations(profile, record, log=print)
    _clear(profile)
    return "finished"


def ack_path(profile, token):
    if not re.fullmatch(r"[0-9a-f]{32}", token):
        raise ins.InstallError("Invalid update acknowledgment token.")
    return Path(profile) / (".update-ack-" + token)


def recover_locked(profile, record):
    """Called only by Setup's shared recovery with profile and destination locks."""
    try:
        _check_ready_location(record["ready"], profile, record["job"])
        if record["state"] not in ("preparing", "exchanging", "awaiting_ack", "rolling_back", "acknowledged"):
            raise ValueError("invalid state")
    except (KeyError, TypeError, ValueError) as e:
        raise ins.InstallError("The update journal is damaged.", "Run Setup again to repair this installation.") from e
    with _car_writer(profile, record):
        if record["state"] == "acknowledged":
            return _finish(profile, record)
        _start_rollback(profile, record)
        return _rollback(profile, record)


def _car_writer(profile, record):
    if record.get("cars") is True:
        from setup_kit import car_catalog
        return car_catalog._writer(Path(profile) / "car-catalog")
    return nullcontext()


def apply_ready(ready, profile, destination, verify=None, running=None, held=None,
                environment=None):
    """Return health token; caller must supervise launch or recover on next launch.

    verify and running default to the destination's product (Most Wanted's or Carbon's app)."""
    ready, profile, destination = map(Path, (ready, profile, destination))
    refuse_update(destination)
    product = ins.product(destination)
    verify = verify or (lambda app: verify_app(app, product["bundle_id"]))
    running = running or (lambda: ins.assert_not_running(pattern=product["running"]))
    if not profile.is_absolute() or not ins.real_dir(profile):
        raise ins.InstallError("The update profile must be an existing plain absolute directory.")
    with ins.profile_guard(profile), ins.dest_lock(destination.parent, held), ExitStack() as locks:
        ins._recover(profile, held=destination.parent)
        running()
        descriptor, app, data = read_ready(ready, profile, destination)
        verify(app)
        record = dict(kind=KIND, token=uuid.uuid4().hex, dest=str(destination.parent), state="preparing", ids={},
                      ready=str(ready), ready_id=ins.ident(ready), job=str(ready.parent), version=descriptor["version"])
        if destination.name != ins.APP_BUNDLE:  # another product's journal names its app (install._plan, recovery)
            record.update(bundle=destination.name, bundle_id=product["bundle_id"])
        if ready.parent.name == "staged":
            record["job"] = str(ready.parent.parent)
        record["profile"] = str(profile)
        record["launch_roots"] = ins.root_environment(os.environ if environment is None else environment)
        record["tools"] = _indexed_tools(profile, ready, Path(record["job"]))
        record["previous"] = ins.previous_generations(profile)
        names, generation, temporary = _names(profile, record)
        generation.parent.mkdir(exist_ok=True)
        if not ins.real_dir(generation.parent):
            raise ins.InstallError("The installs directory uses symbolic links.")
        try:
            _journal(profile, record, "preparing")
            shutil.copytree(data, temporary)
            if ins.digest_tree(temporary) != descriptor["hashes"]["data"]:
                raise ins.InstallError("The copied update data failed verification.")
            ins.mark_owned(temporary, profile)
            _sync_tree(temporary)
            ins.rename_new(temporary, generation)
            ins.sync_dir(generation.parent)
            _step("data-prepared")
            shutil.copytree(app, names[0][1])
            if ins.digest_tree(names[0][1]) != descriptor["hashes"]["app"]:
                raise ins.InstallError("The copied update app failed verification.")
            verify(names[0][1])
            _sync_tree(names[0][1])
            ins.sync_dir(destination.parent)
            _step("app-prepared")
            relocated = {key: generation / Path(value).relative_to("data") for key, value in descriptor["roots"].items()}
            contents = ("".join(f"{k}={v}\n" for k, v in sorted(relocated.items())).encode(),
                        ins.updated_profile((profile / "app.txt").read_bytes() if (profile / "app.txt").exists() else b"",
                                            relocated["pc_root"]))
            for (path, candidate, unused), content in zip(names[1:], contents):
                if path.is_symlink():
                    raise ins.InstallError("A profile manifest is a symbolic link.")
                ins.write_synced(candidate, content, path.stat().st_mode & 0o777 if path.exists() else 0o600)
                ins.sync_dir(profile)
                _step("prepared-" + path.name)
            # Recheck the stage and destination after long copies, before any exchange.
            read_ready(ready, profile, destination)
            running()
            _prepare_cars(ready, profile, descriptor, record, locks)
            names, _, _ = _names(profile, record)
            record["ids"] = {str(path): [ins.ident(path), ins.ident(candidate)] for path, candidate, unused in names}
            _journal(profile, record, "exchanging")
            ins.sync_volumes(profile, destination.parent)
            for path, candidate, unused in names:
                old_id, new_id = record["ids"][str(path)]
                if ins.ident(path) != old_id or ins.ident(candidate) != new_id:
                    raise ins.InstallError("An update destination changed during application.")
                try:
                    if old_id is None:
                        ins.rename_new(candidate, path)
                    else:
                        ins.exchange(path, candidate)
                    ins.sync_dir(path.parent)
                except OSError:
                    if _skip_car_exchange(profile, record, path, candidate, old_id, new_id):
                        continue
                    raise
                _step("exchange-" + path.name)
            verify(destination)
            # The child caches roots on startup. Preserve that launch snapshot
            # even if an alternate manifest or preferences change before ACK.
            launch = ins.previous_generations(profile)
            overrides = ins.environment_generations(record["launch_roots"], profile)
            record["launch_generations"] = None
            if launch is not None and overrides is not None:
                record["launch_generations"] = sorted(set(launch) | overrides)
            _journal(profile, record, "awaiting_ack")
            ins._prune_generations(profile, record, log=print)
        except BaseException:
            with ins.defer_interrupts():
                if ins.journal_path(profile).exists():
                    _start_rollback(profile, record)
                    _rollback(profile, record)
            raise
    return record["token"]


def acknowledge(profile, token, held=None):
    """Consume a live startup receipt under the destination lock, without changing preferences."""
    profile = Path(profile)
    record = json.loads(ins.journal_path(profile).read_bytes())
    if record.get("kind") != KIND or record["token"] != token or record["state"] != "awaiting_ack":
        raise ins.InstallError("Unexpected startup acknowledgment.")
    destination = Path(record["dest"])
    with ins.dest_lock(destination, held):
        record = json.loads(ins.journal_path(profile).read_bytes())
        if (record.get("kind") != KIND or record["token"] != token or record["state"] != "awaiting_ack"
                or Path(record["dest"]) != destination):
            raise ins.InstallError("Unexpected startup acknowledgment.")
        receipt = json.loads(ack_path(profile, token).read_bytes())
        app = Path(record["dest"]) / record.get("bundle", ins.APP_BUNDLE)
        if receipt != dict(token=token, identity=ins.ident(app)):
            raise ins.InstallError("Startup acknowledgment does not match the installed app.")
        verify_app(app, record.get("bundle_id", ins.PUBLIC_BUNDLE_ID))
        with _car_writer(profile, record):
            _journal(profile, record, "acknowledged")
            return _finish(profile, record)


def supervise(profile, destination, ready=None, parent=None, timeout=ACK_SECONDS):
    """Run outside the initiating app, then supervise only the child we start."""
    refuse_update(destination)
    # A competing helper must fail before inspecting/recovering a live journal.
    # The profile guard is released for the child, but the destination remains
    # serialized until acknowledgment/rollback and cleanup have completed.
    with ins.dest_lock(Path(destination).parent):
        return _supervise_locked(Path(profile), Path(destination), ready, parent, timeout)


def _supervise_locked(profile, destination, ready, parent, timeout):
    held = destination.parent
    if parent:
        deadline = time.monotonic() + 30
        while True:
            try:
                os.kill(parent, 0)
            except ProcessLookupError:
                break
            if time.monotonic() >= deadline:
                raise ins.InstallError("The initiating game did not exit.")
            time.sleep(.05)
    if ins.journal_path(profile).exists():
        ins.recover_interrupted(profile, held=held)
        ready = None  # recovery never immediately retries an unhealthy update
    product = ins.product(destination)
    command = [str(Path(destination) / "Contents/MacOS" / product["exec"])]
    child = None
    environment = dict(os.environ, BOOT_USERDATA=str(profile))

    def stop_child():
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()

    try:
        token = apply_ready(ready, profile, destination, held=held, environment=environment) if ready else None
        if token:
            command += ["--port-update-ack", token]
        child = subprocess.Popen(command, close_fds=True, start_new_session=True, env=environment)
        if not token:
            return
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and child.poll() is None:
            if ack_path(profile, token).exists():
                acknowledge(profile, token, held=held)
                return
            time.sleep(.1)
        stop_child()
        ins.recover_interrupted(profile, held=held)
        subprocess.Popen(command[:1], close_fds=True, start_new_session=True, env=environment)
    except BaseException:
        stop_child()
        # Leave an unrecoverable journal intact. A subsequent launch will hand
        # off again, rather than read a possibly mixed set of roots/preferences.
        ins.recover_interrupted(profile, held=held)
        if ready:
            failed = Path(ready).parent / ".apply-failed.json"
            temporary = failed.with_name(failed.name + ".tmp-" + uuid.uuid4().hex)
            ins.write_synced(temporary, b'{"state":"failed"}\n', 0o600)
            os.replace(temporary, failed)
            ins.sync_dir(failed.parent)
        verify_app(destination, product["bundle_id"])
        subprocess.Popen(command[:1], close_fds=True, start_new_session=True, env=environment)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--ready", type=Path)
    parser.add_argument("--parent", type=int)
    parser.add_argument("--publish-ready", action="store_true",
                        help="Publish an index after the preparer has verified the retained tools.")
    args = parser.parse_args()
    try:
        if args.publish_ready:
            if not args.ready:
                parser.error("--publish-ready requires --ready")
            publish_ready(args.ready, args.profile, args.destination)
            return 0
        supervise(args.profile, args.destination, args.ready, args.parent)
    except (OSError, ValueError, ins.InstallError) as e:
        print("Update failed: " + str(e) + ". Your previous version is available.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
