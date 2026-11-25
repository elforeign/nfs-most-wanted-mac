"""Prepare update artifacts without changing the active installation.

Plan: remember inputs after installation; copy verified build outputs into a new
job directory, bind their relative roots and provenance, fsync everything, then
atomically publish ready.json. Most Wanted also stages verified car refreshes.
The installer owns activation and recovery.
"""
from contextlib import contextmanager
import ctypes
import errno
import fcntl
import hashlib
from itertools import chain
import json
import os
import re
from pathlib import Path
import shutil
import stat
import tempfile
import time
import uuid

from . import car_catalog, x360_extras
from . import install

from .install import (APP_BUNDLE, PRODUCTS, InstallError, defer_interrupts, digest_tree, ident, is_current_app,
                      real_dir, sync_dir, write_synced)

CAR_INPUTS = "car-inputs"
CAR_REFRESH_KEPT = "Car refresh failed. The previously verified cars remain active."


def source_digest(path):
    """Content identity survives a folder or archive being renamed."""
    path = Path(path)
    if path.is_symlink() or not path.exists():
        raise InstallError(f"Source is unavailable: {path}")
    if path.is_file():
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(block)
        return digest.hexdigest()
    return digest_tree(path, source_files=True)


def _source_record(state):
    try:
        value = json.loads((Path(state) / 'source-inputs.json').read_bytes())
        if type(value) is dict and value.get('format_version') == 1 and type(value.get('sources')) is dict:
            return value
    except (OSError, ValueError):
        pass
    return dict(format_version=1, sources={})


def _save_source_record(state, value):
    state = Path(state)
    state.mkdir(parents=True, exist_ok=True)
    temporary = state / ('.source-inputs-' + uuid.uuid4().hex)
    try:
        write_synced(temporary, json.dumps(value, sort_keys=True).encode(), 0o600)
        os.replace(temporary, state / 'source-inputs.json')
        sync_dir(state)
    finally:
        temporary.unlink(missing_ok=True)


def installed_pc(profile):
    """Read only the installed generation's PC root, never a user-selected path."""
    profile = Path(profile).resolve()
    try:
        for line in (profile / 'roots.txt').read_text().splitlines():
            if line.startswith('pc_root='):
                path = Path(line.split('=', 1)[1])
                if (path.resolve().is_relative_to(profile / 'installs') and path.is_dir()
                        and install.owned(path.parent, profile)):
                    return path
    except (OSError, ValueError):
        pass
    return None


def remember_sources(state, sources, profile=None):
    value = _source_record(state)
    for path in sources:
        if path:
            value['sources'][str(path)] = dict(sha256=source_digest(path), location=str(path))
    pc = installed_pc(profile) if profile else None
    if pc:
        value['retained_pc'] = dict(path=str(pc), sha256=source_digest(pc))
    _save_source_record(state, value)


def resolve_source(state, path, *, profile=None, pc=False, locate=None, verify_kept=None):
    """A missing original may be replaced only by content that verifies."""
    path = Path(path)
    if path.exists():
        return path
    record = _source_record(state)
    pin = record['sources'].get(str(path), {})
    try:
        choices = load_choices(state) if pc else None
    except InstallError:
        choices = None
    remembered_pc = choices and choices['pc'] == str(path)
    if pc and profile and remembered_pc:
        kept = installed_pc(profile)
        retained = record.get('retained_pc', {})
        try:
            if kept and source_digest(kept) == retained.get('sha256'):
                return kept
        except (OSError, InstallError):
            pass
        if kept and not retained and verify_kept:
            # Installations made before input receipts still have an owned
            # generation. Revalidate its supported executable and folder layout.
            try:
                verify_kept(kept)
                return kept
            except (OSError, ValueError, InstallError):
                pass
    expected = pin.get('sha256')
    # Earlier Setup versions already installed a complete PC copy. It supplies
    # an independent identity for Locate, even without a saved input receipt.
    if not expected and pc and profile and remembered_pc:
        kept = installed_pc(profile)
        if kept:
            expected = source_digest(kept)
    previous = pin.get('location')
    if expected and previous and Path(previous).exists() and source_digest(previous) == expected:
        return Path(previous)
    request = dict(name=path.name, path=str(path),
                   title=f"Can't find {path.name}",
                   body=f"It was at {path}. If you moved it, choose its new location. Your settings and cars are kept.")
    while locate:
        replacement = locate(request)
        if not replacement:
            raise InstallError(f"Skipped {path.name}. Your settings and cars are kept.")
        try:
            candidate = Path(replacement)
            if not candidate.is_absolute() or not expected or source_digest(candidate) != expected:
                raise ValueError('The selected folder does not verify as the same source.')
        except (OSError, ValueError, InstallError):
            request['error'] = 'The selected location does not verify as the same source. Choose its new location.'
            continue
        record['sources'][str(path)] = dict(sha256=expected, location=str(candidate))
        _save_source_record(state, record)
        return candidate
    raise InstallError(request['title'] + '. ' + request['body'], 'Choose Locate in Setup or the updater to continue.')


def source_locator(directory, emit):
    """One nonce per prompt; replies cannot be reused by a later request."""
    directory = Path(directory)
    def locate(request):
        directory.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        reply = directory / (token + '.json')
        emit(dict(ev='source-missing', token=token, **request))
        deadline = time.monotonic() + 1800
        while time.monotonic() < deadline:
            try:
                response = json.loads(reply.read_bytes())
                reply.unlink()
                return response.get('path')
            except FileNotFoundError:
                time.sleep(0.1)
        raise InstallError('Source selection timed out. Your settings and cars are kept.')
    return locate


class MissingCarPreparation(ValueError):
    """A verified generation outlived its original preparation."""


def car_importer(profile, cache, *, mutating=False):
    """Keep the existing verified preparations in the profile; adopt legacy cache entries on demand."""
    from . import car_import

    class RetainedImporter(car_import.CarImporter):
        @contextmanager
        def _transaction(self, cancel):
            with super()._transaction(cancel) as receipt:
                if mutating and install.exists(install.journal_path(profile)):
                    try:
                        journal = json.loads(install.journal_path(profile).read_bytes())
                    except FileNotFoundError:
                        journal = {}
                    if journal.get("cars") is True:
                        raise InstallError("Another update is in progress. Open its log or wait before Retry")
                yield receipt

        def _retained_input(self, row, policy):
            try:
                return super()._retained_input(row, policy)
            except car_import.MissingAssets:
                try:
                    old = legacy._retained_input(row, policy)
                except (car_import.MissingAssets, car_import.CacheError) as exc:
                    raise MissingCarPreparation() from exc
                with car_catalog._directory(old) as source:
                    receipt = car_catalog._read(source, "receipt.json")
                    entries = car_catalog._inventory(source) - {"receipt.json"}
                    pins = {f"outputs/{name}": pin for name, pin in row["outputs"].items()}
                    for group, names in row["inputs"].items():
                        folder = "mw" if group in ("donor", "dependencies") else group
                        pins.update({f"{folder}/{name}": pin for name, pin in names.items()})
                    car_catalog._require(entries == pins.keys(), "legacy car preparation changed")
                    with car_catalog._directory(self.work) as base, _car_stage(base) as staged:
                        for name, pin in pins.items():
                            _copy_car_file(source, name, staged, name, pin)
                        staged.write("receipt.json", receipt)
                        staged.seal()
                        staged.publish(old.name)
                        os.fsync(base)
                new = self.work / old.name
                self._cached(new, car_catalog._decode(receipt)["identity"], policy, retained=True)
                return new

        def import_cars(self, carbon, *, mw_pc, cars, mw_x360=None, cancel=None, progress=None, repair_cache=False):
            car_import._require(type(repair_cache) is bool, 'invalid cache repair option')
            cars = list(cars)
            if repair_cache:
                # Retry uses the installed-output transaction, which can recover
                # catalog metadata before replacing the damaged generation.
                self._locations(carbon, mw_pc, mw_x360)
                return _merge_car_import(self, carbon, mw_pc, cars, mw_x360, cancel, progress, True)
            try:
                return super().import_cars(carbon, mw_pc=mw_pc, cars=cars, mw_x360=mw_x360,
                                           cancel=cancel, progress=progress, repair_cache=repair_cache)
            except MissingCarPreparation:
                # Old Setup could lose its preparation when the user cleared
                # the cache. Its verified installed outputs can still be merged.
                return _merge_car_import(self, carbon, mw_pc, cars, mw_x360, cancel, progress, repair_cache)

        def _publication_bytes(self, rows, policy):
            try:
                return super()._publication_bytes(rows, policy)
            except (OSError, ValueError):
                # A rejected identical generation needs a complete new payload.
                # Publication still verifies it or quarantines it during Retry.
                return sum(pin['size'] for row in rows if row['state'] == 'ready'
                           for pin in row['outputs'].values())

    profile = Path(profile)
    root = profile / "car-catalog"
    legacy = car_import.CarImporter(root, cache)
    return RetainedImporter(root, profile / CAR_INPUTS)


def _recover_car_current(engine, policy, preparations):
    """Recover metadata without selecting or trusting damaged output bytes.

    The current digest must match reconstructed approved rows exactly.
    Otherwise only an independently verified retained generation is usable.
    All recovery reads run under the catalog writer lock.
    """
    from . import car_import

    with car_catalog._directory(engine.root) as base:
        try:
            pointer = car_catalog._decode(car_catalog._read(base, 'current.json'))
            car_catalog._require(type(pointer) is dict and type(pointer.get('generation')) is str and
                     re.fullmatch('g-[0-9a-f]{64}', pointer['generation']) and
                     pointer.get('catalog_sha256') == pointer['generation'][2:], 'invalid repair pointer')
        except (OSError, ValueError):
            pointer = None

        def recovered(doc, candidate):
            car_catalog._selection(candidate['selection'], doc)
            car_catalog._require(candidate['compatibility'] == doc['compatibility'], 'repair compatibility mismatch')
            return car_catalog.Verification(candidate['generation'], doc['compatibility'],
                tuple(doc['cars'][s] for s in candidate['selection']), doc)

        if pointer is not None:
            try:
                with car_catalog._directory(engine.root / pointer['generation']) as generation:
                    doc = car_catalog._decode(car_catalog._read(generation, 'catalog.json'))
                car_catalog._require(car_catalog.digest(doc) == pointer['catalog_sha256'], 'repair catalog mismatch')
                return recovered(car_catalog._catalog(doc, policy), pointer)
            except (OSError, ValueError, KeyError, TypeError):
                pass
            # Preparations include converter snapshots and approved pins.
            # Never infer a missing row from its display name or selection.
            rows, ambiguous = {}, set()
            for entry in sorted(engine.work.glob('c-*')):
                try:
                    with car_catalog._directory(entry) as cached:
                        receipt = car_catalog._decode(car_catalog._read(cached, 'receipt.json'))
                    row = engine._cached(entry, receipt['identity'], policy, retained=True)
                    slot = row['slot']
                    if slot in rows and rows[slot] != row:
                        ambiguous.add(slot)
                    rows[slot] = row
                except (OSError, ValueError, KeyError, TypeError):
                    continue
            for item in preparations:
                row = item['row']
                rows[row['slot']] = row
                ambiguous.discard(row['slot'])
            if not ambiguous:
                rebuilt = car_catalog.reservations()
                for slot, row in rows.items():
                    rebuilt[slot] = row
                try:
                    doc = car_catalog.make_catalog(rebuilt, policy)
                    car_catalog._require(car_catalog.digest(doc) == pointer['catalog_sha256'], 'repair catalog mismatch')
                    return recovered(doc, pointer)
                except (ValueError, KeyError, TypeError):
                    pass

        # Prefer the most recent pointer backup, preserving deselections.
        backups = sorted(engine.root.glob('.previous-*'), key=lambda p: p.lstat().st_mtime_ns, reverse=True)
        for path in backups:
            try:
                candidate = car_catalog._decode(car_catalog._read(base, path.name))
                doc = car_catalog._verify_generation(base, candidate['generation'], candidate['catalog_sha256'], policy)
                return recovered(doc, candidate)
            except (OSError, ValueError, KeyError, TypeError):
                continue
        # A damaged pointer can outlive its only generation without a backup.
        generations = sorted(engine.root.glob('g-*'), key=lambda p: p.lstat().st_mtime_ns, reverse=True)
        for path in generations:
            try:
                doc = car_catalog._verify_generation(base, path.name, path.name[2:], policy)
                selected = [row['slot'] for row in doc['cars'] if row['state'] == 'ready']
                return recovered(doc, _car_pointer(path.name, doc, selected))
            except (OSError, ValueError, KeyError, TypeError):
                continue
    raise car_import.ImportError('no verified catalog metadata remains; retained generations have been preserved')


def _repair_car_generation(base, generation, sha, policy):
    """Keep rejected bytes aside; a verified generation is never moved."""
    if car_catalog._entry_stat(base, generation) is None:
        return
    try:
        car_catalog._verify_generation(base, generation, sha, policy)
    except (OSError, ValueError):
        car_catalog._set_aside(base, generation, '.damaged-')


def _car_repair_snapshot(engine, policy, preparations):
    with car_catalog._writer(engine.root):
        current = car_catalog.verify_current(engine.root, policy=policy)
        if current.error:
            current = _recover_car_current(engine, policy, preparations)
        return current, engine._pointer_bytes()


def _car_stage(base):
    return car_catalog._stage(base, ".refresh-" + uuid.uuid4().hex)


def _copy_car_file(source, name, staged, target, pin):
    """Clone an already verified file on APFS, and check the resulting pin on either path."""
    parent, _, leaf = target.rpartition("/")
    with car_catalog._file(source, name) as original, staged.directory(parent) as (base, entries):
        if install._libc.fclonefileat(original, base, os.fsencode(leaf), 0) == 0:
            st = car_catalog._entry_stat(base, leaf)
            entries[leaf] = (car_catalog._identity(st), None)
            with car_catalog._file(base, leaf) as copied:
                car_catalog._require(car_catalog._record(copied) == pin, "cloned car file changed")
            return
        error = ctypes.get_errno()
        if error not in (errno.EXDEV, errno.ENOTSUP, errno.EINVAL, errno.ENOSYS):
            raise OSError(error, os.strerror(error), target)
        copied = staged.open_file(target)
        try:
            car_catalog._require(car_catalog._record(original, copied) == pin, "copied car file changed")
        finally:
            os.close(copied)


def write_car_generation(base, rows, policy, output, *, repair=False):
    """Publish an immutable generation to a catalog directory without selecting it."""
    doc = car_catalog.make_catalog(rows, policy)
    data = car_catalog.encoded(doc)
    sha = hashlib.sha256(data).hexdigest()
    name = "g-" + sha
    rejected = False
    if repair:
        try:
            car_catalog._verify_generation(base, name, sha, policy)
        except (OSError, ValueError):
            rejected = True
    if rejected or car_catalog._entry_stat(base, name) is None:
        with _car_stage(base) as staged:
            for row in rows:
                if row["state"] != "ready":
                    continue
                for leaf, pin in row["outputs"].items():
                    relative = f"cars/{row['slot']}/{leaf}"
                    source_dir, source_name = output(row, leaf, relative, pin)
                    with car_catalog._directory(source_dir) as source:
                        _copy_car_file(source, source_name, staged, relative, pin)
            staged.write("catalog.json", data)
            staged.seal()
            # Unchanged outputs may come from the damaged generation itself.
            # Keep its path available until every pinned file has been copied.
            if repair:
                _repair_car_generation(base, name, sha, policy)
            staged.publish(name)
        os.fsync(base)
    car_catalog._verify_generation(base, name, sha, policy)
    return name, doc


def _car_pointer(generation, doc, selection):
    return dict(schema=1, generation=generation, catalog_sha256=generation[2:],
                compatibility=doc["compatibility"], selection=sorted(selection))


def _merge_car_import(engine, source, mw_pc, cars, mw_x360, cancel, progress, repair_cache):
    """Finish a normal import using verified installed outputs for pre-retention rows."""
    from . import car_import

    cars = car_import._specs(cars)
    with engine._transaction(cancel) as receipt:
        policy = engine._policy(True)
        current, baseline = engine._snapshot(policy) if not repair_cache else (None, None)
        source, mw, _, recognition = engine._inspect(source, mw_pc, mw_x360, cars)
        later_rows = engine._later_rows(recognition, policy)
        preparations = [engine._prepare(source, mw, recognition, car, policy, receipt, cancel, progress,
                                        later_rows=later_rows, repair_cache=repair_cache) for car in cars]
        engine._finish_preparations(preparations, receipt, policy, current)
        if repair_cache:
            current, baseline = _car_repair_snapshot(engine, policy, preparations)
        rows = current.catalog["cars"].copy() if current.catalog else car_catalog.reservations()
        selected = {row["slot"] for row in current.selected}
        incoming = {}
        for item in preparations:
            row = item["row"]
            car_catalog._require(item["approved"], "converted output is not approved by the packaged policy")
            slot = row["slot"]
            previous = rows[slot]
            car_catalog._require(previous["state"] != "ready" or
                car_catalog.slot_compatibility(previous) == car_catalog.slot_compatibility(row),
                "incompatible revision; retain the old generation and resolve required packs first")
            rows[slot], incoming[slot] = row, item["entry"] / "outputs"
            selected.add(slot)

        def output(row, leaf, relative, pin):
            slot = row["slot"]
            return (incoming[slot], leaf) if slot in incoming else (engine.root / current.generation, relative)

        publication_bytes = engine._publication_bytes(rows, policy)
        volumes = engine._space(preparations, receipt, publication_bytes)
        if shutil.disk_usage(engine.root).free < publication_bytes + engine.margin:
            raise OSError(errno.ENOSPC, 'insufficient space for durable catalog publication')
        engine._boundary("publishing", receipt, cancel, progress)
        with car_catalog._writer(engine.root) as base:
            engine._unchanged_since(baseline)
            name, doc = write_car_generation(base, rows, policy, output, repair=repair_cache)

            def precommit():
                engine._unchanged_since(baseline)
                for item in preparations:
                    item["unchanged"]()
                    engine._retained_input(item["row"], policy)
                car_catalog._require(car_catalog.packaged_policy() == policy, "packaged approval policy changed")
                car_catalog._verify_generation(base, name, name[2:], policy)

            pointer = car_catalog._publish_pointer(engine.root, base, _car_pointer(name, doc, selected),
                lambda point: engine._boundary(point, receipt, cancel, progress), precommit)
        receipt.sample("committed")
        return dict(pointer=pointer, reused=sum(item["reused"] for item in preparations),
                    volumes=volumes, receipt=receipt.result())


def _refresh_row(engine, row, entry, pc, policy, receipt, target):
    from . import car_import, car_selection, car_sources

    origin = row["origin"]
    game, folder = origin["game"], origin["folder"]
    recognition = dict(origin, shared=[], cars=[dict(car_hash=car_sources.path_hash("CARS/" + folder),
        status="importable", files=[dict(pin, path_hash=car_sources.path_hash(name))
                                   for name, pin in row["inputs"]["source"].items()])])
    mw_names = car_import._mw_names(game, row["donors"]["model"], folder)
    mw = entry / "mw"
    # A release may start using another file from the required MW install.
    # Keep the old verified inputs and supplement them from the staged PC root.
    extra = {name: car_import._mw_asset(pc, name, game) for name in mw_names
             if name not in row["inputs"]["donor"] and name not in row["inputs"]["dependencies"]}
    with car_catalog._directory(target) as base, _car_stage(base) as scratch:
        if any(path.is_file() for path in extra.values()):
            mw = target / scratch.name / "mw"
            with car_catalog._directory(entry / "mw") as source:
                for group in ("donor", "dependencies"):
                    for name, pin in row["inputs"][group].items():
                        _copy_car_file(source, name, scratch, "mw/" + name, pin)
            for name, path in extra.items():
                if path.is_file():
                    with car_catalog._directory(path.parent) as source:
                        _copy_car_file(source, path.name, scratch, "mw/" + name, car_catalog.fingerprint(path))
        preparation = engine._prepare(entry / "source", mw, recognition, car_selection.spec(row), policy,
                                      receipt, None, None, later_rows=engine._later_rows(recognition, policy))
        if not preparation["reused"]:
            preparation.update(preparation["finish"]())
        car_catalog._require(preparation["approved"], "refreshed car is not approved")
        updated = preparation["row"]
        car_catalog._require(car_catalog.slot_compatibility(updated) == car_catalog.slot_compatibility(row),
                             "refreshed car has different save compatibility")
        return updated, preparation["entry"]


def prepare_car_refresh(target, profile, cache, pc):
    """Stage an update pointer and approved outputs while the installed pointer stays live."""
    root = Path(profile) / "car-catalog"
    if not install.exists(root / "current.json"):
        return None
    from . import car_import

    engine = car_importer(profile, cache)
    with engine._transaction(None) as receipt:
        policy = car_catalog.packaged_policy()
        current, baseline = engine._snapshot(policy)
        if current.catalog is None:
            return None
        rows = current.catalog["cars"].copy()
        previous = root / current.generation
        selected = {row["slot"] for row in current.selected}
        legacy = car_import.CarImporter(root, cache).work.is_dir()
        changed = {}
        for row in rows:
            if row["state"] != "ready":
                continue
            slot = row["slot"]
            if slot in selected and (current.catalog["policy_sha256"] != policy.sha256 or _car_tools_changed(row)):
                entry = engine._retained_input(row, policy)
                rows[slot], changed[slot] = _refresh_row(engine, row, entry, pc, policy, receipt, target)
            elif legacy:
                try:
                    engine._retained_input(row, policy)
                except (MissingCarPreparation, car_import.CacheError):
                    pass  # no refresh needed, so a missing old preparation is harmless
        if not changed:
            return None

        def output(row, leaf, relative, pin):
            slot = row["slot"]
            if current.catalog["cars"][slot]["outputs"].get(leaf) == pin:
                return previous, relative
            return changed[slot] / "outputs", leaf

        with tempfile.TemporaryDirectory(prefix=".cars-", dir=target) as scratch:
            staged = Path(scratch) / "cars"
            staged.mkdir(mode=0o700)
            with car_catalog._directory(staged) as base:
                generation, doc = write_car_generation(base, rows, policy, output)
                car_catalog._require(doc["compatibility"] == current.compatibility, "catalog compatibility changed")
                body = car_catalog.encoded(_car_pointer(generation, doc, selected))
                write_synced(staged / "current.json", body, 0o600)
                os.fsync(base)
            os.rename(staged, target / "cars")
        return dict(before=hashlib.sha256(baseline).hexdigest(), after=hashlib.sha256(body).hexdigest())


def _car_tools_changed(row):
    from . import car_import

    origin = row["origin"]
    names = car_import._mw_names(origin["game"], row["donors"]["model"], origin["folder"])
    absent = sorted(set(names) - row["inputs"]["donor"].keys() - row["inputs"]["dependencies"].keys())
    code, _, _ = car_import._code(origin["platform"], absent, origin["game"], mac_audio="AUDIO.BIN" in row["outputs"])
    return code.pins != row["inputs"]["converter"]


@contextmanager
def lock_car_update(root):
    """Take the importer lock before the writer lock, just as import does."""
    with car_catalog._directory(root) as base:
        fd = os.open(".import.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                     0o600, dir_fd=base)
        try:
            info = os.fstat(fd)
            car_catalog._require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "unsafe car import lock")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with car_catalog._writer(root) as writer:
                car_catalog._bound(root, base)
                yield writer
        finally:
            os.close(fd)


def prepare_car_pointer(staged, root, base, hashes):
    """Verify and retain the staged generation without changing the active pointer."""
    policy = car_catalog.packaged_policy()
    baseline = car_catalog._read(base, "current.json")
    car_catalog._require(hashlib.sha256(baseline).hexdigest() == hashes["before"], "car selection changed")
    previous, _ = car_catalog._pointer(base, policy)
    with car_catalog._directory(staged) as source:
        pointer, doc = car_catalog._pointer(source, policy)
        body = car_catalog.encoded(pointer)
        car_catalog._require(hashlib.sha256(body).hexdigest() == hashes["after"] and
                             pointer["compatibility"] == previous["compatibility"] and
                             pointer["selection"] == previous["selection"] and doc["policy_sha256"] == policy.sha256,
                             "staged car pointer changed")
        source_generation = staged / pointer["generation"]
        generation, _ = write_car_generation(base, doc["cars"], policy,
                                              lambda row, leaf, relative, pin: (source_generation, relative))
        car_catalog._require(generation == pointer["generation"], "staged car generation changed")
        car_catalog._bound(root, base)
    return body


def load_choices(state, required=False):
    path = Path(state) / "source-choices.json"
    try:
        value = json.loads(path.read_text())
        def absolute(v):  # or None; pc_update, pc_exe and pc_discs (disc images) are absent from earlier choices
            return v is None or isinstance(v, str) and Path(v).is_absolute()
        discs = value.get("pc_discs", [])
        if (value.get("format_version") != 1 or "x360" not in value or not isinstance(value.get("pc"), str)
                or not isinstance(value.get("destination"), str) or not absolute(value["x360"])
                or not absolute(value.get("pc_update")) or not absolute(value.get("pc_exe"))
                or not isinstance(discs, list) or not all(v is not None and absolute(v) for v in discs)
                or not Path(value["pc"]).is_absolute() or not Path(value["destination"]).is_absolute()):
            raise ValueError("invalid choices")
        if "x360_extras" in value:
            if not isinstance(value["x360_extras"], list):
                raise ValueError("invalid extra choices")
            x360_extras.selection(value["x360_extras"])
        return value
    except FileNotFoundError:
        if not required:
            return None
        raise InstallError("SOURCE_CHOICES_MISSING: Setup has no remembered source choices.",
                           "Run Setup successfully once, or supply --pc, --x360 and --dest.") from None
    except (ValueError, AttributeError, TypeError) as e:
        raise InstallError("SOURCE_CHOICES_INVALID: Setup's remembered choices are damaged.",
                           "Run Setup again to select your original sources.") from e


def save_choices(state, pc, x360, destination, pc_update=None, pc_exe=None, pc_discs=(), extra_choices=None):
    state = Path(state)
    state.mkdir(parents=True, exist_ok=True)
    temporary = state / (".source-choices-" + uuid.uuid4().hex)
    try:
        extras = {k: str(v) for k, v in (("pc_update", pc_update), ("pc_exe", pc_exe)) if v}  # disc images only
        if pc_discs:
            extras["pc_discs"] = [str(p) for p in pc_discs]
        extras.update(x360_extras.identity(extra_choices))
        write_synced(temporary, json.dumps(dict(format_version=1, pc=str(pc),
                     x360=str(x360) if x360 else None, destination=str(destination), **extras), sort_keys=True).encode(), 0o600)
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
    from .export import refuse_update
    refuse_update(destination)
    if not required and not os.path.lexists(destination):
        return  # Explicit destinations may prepare a first installation.
    # Carbon's app has its own id; any other name is checked as Most Wanted's (and refused by the stage checks).
    if not real_dir(destination) or not is_current_app(destination, PRODUCTS.get(destination.name, PRODUCTS[APP_BUNDLE])["bundle_id"]):
        raise InstallError(f"STAGE_DESTINATION_INVALID: The installed app is missing or unrecognized: {destination}",
                           "Reselect the installed NFS Most Wanted Native.app with --dest.")


def publish(target, app, data, roots, destination, profile, version, provenance, on_commit=None,
            prepare_cars=None):
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
        # Launch checks every entry in the job for group/other write access, and the job can
        # hold these work copies, so clear the bits at the source; the copies inherit the modes.
        for root in (app, data):
            for path in chain((root,), root.rglob("*")):
                mode = stat.S_IMODE(path.lstat().st_mode)
                if mode & 0o022 and not path.is_symlink():
                    path.chmod(mode & ~0o022)
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
        if prepare_cars is not None and destination.name == APP_BUNDLE:
            cars = prepare_cars(target, profile, target / relative["pc_root"])
            if cars is not None:
                record["cars"] = cars
            sync_dir(target)
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
