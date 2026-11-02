"""Standalone, serialized preparation and management of local added-car packs.

``CarImporter(catalog_root, work_root)`` exposes ``check``, ``plan``,
``import_cars`` (also ``reimport``), ``list_cars``, ``remove``, ``disable`` and
``restore`` without building an application. ``cars`` is a sequence of local
metadata dictionaries with slot, folder, display, bindings, donors and parts,
as specified by the catalog contract. Slots are permanent policy reservations,
never discovery-order allocations. The caller derives labels and part semantics
from local assets; the complete row still needs independent packaged approval.

``check`` recognizes inputs. ``plan(measure=False)`` hashes selected inputs and
reports unknown output sizes honestly; ``plan(measure=True)`` runs the shared
converter and returns measured contracts/space without changing the pointer.
Only approved preparations enter the reusable cache. No API accepts a policy,
approval flag, alternative converter, or a caller-supplied dependency receipt.
Tests replace packaged_policy in their own harness, never in shipped code.

Remove means deselect, retaining immutable generations and cache dependencies.
Restore verifies a retained generation without original media; ``required``
maps slots to save compatibility signatures without reading or modifying saves.
Reimport can quarantine a rejected cache directory and rebuild it from verified
original inputs; the replacement needs the same complete approval as any import.
Optional MW X360 input is identified separately and is currently unused by the
shared writer, whose donor/shared dependencies come from required MW PC input.
The public later-game look option remains default Off; this module changes no
settings. Windows defers public import and provenance admission to a future host
implementation; imported-car saves require their packs on the Mac host. That
host must refuse NTFS junctions and other reparse points as this one refuses
symlinks: open each component with FILE_FLAG_OPEN_REPARSE_POINT and reject
FILE_ATTRIBUTE_REPARSE_POINT before any write.
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
import errno
import fcntl
import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import time
import uuid

from . import car_catalog as catalog, car_convert as converter, car_sources as sources
from . import car_x360_inputs as xenon
from .x360 import Source

REVISION = 1
MW_SHARED = ('GLOBAL/GLOBALA.BUN', 'GLOBAL/GLOBALB.LZC', 'GLOBAL/INGAMEA.BUN',
             'GLOBAL/INGAMEB.BUN', 'GLOBAL/DYNTEX.BIN', 'CARS/TEXTURES.BIN',
             'CARS/WHEELS/TEXTURES.BIN')
SPEC_FIELDS = {'slot', 'folder', 'display', 'bindings', 'donors', 'parts'}
_POLICY_PIN = re.compile('^PACKAGED_POLICY_SHA256 = .*$'.encode(), re.M)


class ImportError(ValueError):
    """Preparation failed; an existing selection has not been replaced."""
    exit_code = 1


class MissingAssets(ImportError):
    exit_code = 3


class Cancelled(ImportError):
    exit_code = 130


class CacheError(ImportError):
    """An existing cache entry is stale, incomplete or corrupt; never activate it."""


def _require(ok, message):
    if not ok:
        raise ImportError(message)


def _clone(value):
    return catalog._decode(catalog.encoded(value))


def _path(value):
    path = Path(os.path.abspath(os.path.expanduser(os.fspath(value))))
    _require('..' not in Path(value).parts, 'parent traversal in path')
    ancestor = path
    while not ancestor.exists() and not ancestor.is_symlink():
        ancestor = ancestor.parent
    if ancestor.is_file():
        with catalog._directory(ancestor.parent), _asset(ancestor):
            pass
    else:
        with catalog._directory(ancestor):
            pass
    return path


def _overlaps(a, b):
    a, b = str(a).casefold().rstrip('/'), str(b).casefold().rstrip('/')
    return a == b or a.startswith(b + '/') or b.startswith(a + '/')


@contextmanager
def _asset(path):
    """No-follow descriptor, including large ISO containers (hash only ranges)."""
    with catalog._directory(path.parent) as base:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=base)
        try:
            st = os.fstat(fd)
            _require(stat.S_ISREG(st.st_mode) and st.st_nlink == 1, 'unsafe input file')
            yield fd
        finally:
            os.close(fd)


def _mkdir(path):
    with catalog._directory(path, create=True):
        pass


def _write(path, data):
    _mkdir(path.parent)
    with catalog._directory(path.parent) as base:
        catalog._write(base, path.name, data)


def _pin(row):
    return {k: row[k] for k in ('size', 'sha256')}


def _range(path, offset, size, *, target=None, tick=lambda: None):
    _require(type(size) is int and 0 <= size <= converter.MAX_FILE, 'logical range too large')
    with _asset(path) as fd, ExitStack() as stack:
        before = os.fstat(fd)
        _require(0 <= offset <= before.st_size and size <= before.st_size - offset,
                 'logical range exceeds container')
        digest, left = hashlib.sha256(), size
        os.lseek(fd, offset, os.SEEK_SET)
        sink = None
        if target is not None:
            parent = stack.enter_context(catalog._directory(target.parent, create=True))
            leaf = os.open(target.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                           0o600, dir_fd=parent)
            sink = stack.enter_context(os.fdopen(leaf, 'wb'))
        try:
            while left:
                tick()
                block = os.read(fd, min(left, 1 << 20))
                _require(bool(block), 'source truncated while reading')
                digest.update(block)
                if sink is not None:
                    sink.write(block)
                left -= len(block)
            if sink is not None:
                sink.flush()
                os.fsync(sink.fileno())
            after = os.fstat(fd)
            _require((before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
                     (after.st_size, after.st_mtime_ns, after.st_ctime_ns), 'source changed while reading')
        finally:
            if sink is not None:
                sink.close()
    return dict(size=size, sha256=digest.hexdigest())


def _logical(source, entry, **kwargs):
    key, offset, size = entry
    if source.handle:
        offset += source.files[key].offset
        path = source.path
    else:
        path = source.files[key]
    return _range(path, offset, size, **kwargs)


@contextmanager
def _open_source(path):
    # The recognition context wraps every OSError from its body as a source
    # error. Keep transaction ENOSPC/EIO and interruption types intact instead.
    source = Source(path)
    try:
        yield source
    finally:
        source.close()


def _source_names(platform, folder, identities):
    names = [f'CARS/{folder}/GEOMETRY.BIN', f'CARS/{folder}/TEXTURES.BIN',
             'GLOBAL/GLOBALB.LZC', 'GLOBAL/ATTRIBUTES.BIN']
    if platform == 'x360':
        names += ['GLOBAL/GLOBALB.BUN', 'CARS/TEXTURES.BIN', 'GLOBAL/INGAMEA.BUN']
    known = {sources.path_hash(n): n for n in names}
    _require(set(identities) <= known.keys(), 'unmapped converter logical dependency')
    return {n: known[n] for n in identities}


def _inventory(root):
    """Exact regular-file inventory; never traverse a symlink or special file."""
    result = {}
    if not root.exists():
        return result
    with catalog._directory(root):
        for base, dirs, files in os.walk(root, followlinks=False):
            for name in dirs + files:
                path = Path(base) / name
                st = path.lstat()
                _require(not stat.S_ISLNK(st.st_mode) and
                         (stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode)), 'unsafe managed file')
                if name in files:
                    _require(st.st_nlink == 1, 'hardlinked managed file')
                    result[path.relative_to(root).as_posix()] = st
    return result


def _seal(root):
    for name in _inventory(root):
        with _asset(root / name) as fd:
            os.fchmod(fd, 0o444)
            os.fsync(fd)
    for base, _, _ in os.walk(root, topdown=False):
        with catalog._directory(base) as fd:
            os.fchmod(fd, 0o555)
            os.fsync(fd)


def _volume(root):
    ancestor = root
    while not ancestor.exists():
        ancestor = ancestor.parent
    return str(ancestor.stat().st_dev), shutil.disk_usage(ancestor).free


def _specs(cars):
    cars = _clone(list(cars))
    _require(bool(cars), 'select at least one car')
    slots, identities = set(), set()
    for car in cars:
        _require(type(car) is dict and set(car) == SPEC_FIELDS, 'invalid local car metadata')
        slot, folder = car['slot'], car['folder']
        _require(type(slot) is int and 0 <= slot < catalog.ADDITION_COUNT and slot not in slots,
                 'duplicate or invalid permanent slot')
        _require(type(folder) is str and re.fullmatch('[A-Z0-9_]{1,15}', folder) and folder not in identities,
                 'duplicate or invalid source folder')
        _require(type(car['donors']) is dict and set(car['donors']) == {'model', 'price'} and
                 all(type(v) is str and re.fullmatch('[A-Z0-9_]{1,15}', v) for v in car['donors'].values()),
                 'invalid local donor metadata')
        slots.add(slot)
        identities.add(folder)
    return sorted(cars, key=lambda c: c['slot'])


class _Code:
    def __init__(self):
        self.files, self.pins, self.neutral = {}, {}, {}

    def read(self, path):
        path = _path(path)
        pin = catalog.fingerprint(path)
        data = path.read_bytes()
        _require(hashlib.sha256(data).hexdigest() == pin['sha256'], 'converter dependency changed')
        _require(path.name not in self.files or self.files[path.name] == path, 'ambiguous converter dependency')
        self.files[path.name], self.pins[path.name] = path, pin
        return data

    def read_catalog(self, path):
        """Pin the catalog module with its packaged policy digest neutral.

        Installing an approval writes that digest into this module; pinning the
        raw bytes would change every approved contract (a hash cycle).
        """
        neutral, count = _POLICY_PIN.subn('PACKAGED_POLICY_SHA256 = None'.encode(), self.read(path))
        _require(count == 1, 'catalog policy pin is not unique')
        self.neutral[path.name] = neutral
        self.pins[path.name] = dict(size=len(neutral), sha256=hashlib.sha256(neutral).hexdigest())


def _code(platform, absent):
    code = _Code()
    policy, _ = converter._converter_policy(platform, None, code)
    for path in (Path(__file__), sources.MANIFEST):
        code.read(path)
    code.read_catalog(Path(catalog.__file__))
    environment = catalog.encoded(dict(engine=REVISION, python=list(sys.version_info[:3]),
        implementation=sys.implementation.name, numpy=converter.np.__version__, absent_mw=absent))
    code.pins['environment.json'] = dict(size=len(environment), sha256=hashlib.sha256(environment).hexdigest())
    return code, policy, environment


class _Receipt:
    def __init__(self, roots):
        self.roots, self.start = roots, time.monotonic()
        self.volumes, self.steps, self.errors = {}, [], []
        self.sample('start')

    def sample(self, phase):
        totals = {}
        try:
            for root in self.roots:
                if not root.exists():
                    continue
                device = str(root.stat().st_dev)
                row = totals.setdefault(device, dict(bytes=0, allocated_bytes=0))
                for st in _inventory(root).values():
                    row['bytes'] += st.st_size
                    row['allocated_bytes'] += st.st_blocks * 512
            for device, sizes in totals.items():
                root = next(r for r in self.roots if r.exists() and str(r.stat().st_dev) == device)
                row = self.volumes.setdefault(device, dict(device=device, initial_bytes=sizes['bytes'],
                    initial_available_bytes=shutil.disk_usage(root).free,
                    initial_allocated_bytes=sizes['allocated_bytes'], peak_bytes=0, peak_allocated_bytes=0))
                for key in ('bytes', 'allocated_bytes'):
                    row['peak_' + key] = max(row['peak_' + key], sizes[key])
            self.steps.append(dict(phase=phase, seconds=time.monotonic() - self.start))
        except (OSError, ImportError) as exc:
            self.errors.append(str(exc))

    def result(self):
        return dict(seconds=time.monotonic() - self.start, volumes=list(self.volumes.values()),
                    sampling='file bytes and allocated blocks at transaction boundaries',
                    steps=list(self.steps), measurement_errors=list(self.errors))


class CarImporter:
    def __init__(self, catalog_root, work_root, *, margin_bytes=16 << 20):
        self.root, self.work_root = _path(catalog_root), _path(work_root)
        _require(not _overlaps(self.root, self.work_root), 'catalog and work roots must be disjoint')
        _require(type(margin_bytes) is int and margin_bytes >= 0, 'invalid space margin')
        self.margin = margin_bytes
        self.work = self.work_root / ('car-import-' + catalog.digest(str(self.root))[:24])

    @staticmethod
    def _tick(cancel):
        if cancel is not None and cancel():
            raise Cancelled('car operation cancelled before activation')

    @contextmanager
    def _transaction(self, cancel):
        self._tick(cancel)
        with catalog._directory(self.root, create=True) as base:
            fd = os.open('.import.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                         0o600, dir_fd=base)
            try:
                st = os.fstat(fd)
                _require(stat.S_ISREG(st.st_mode) and st.st_nlink == 1, 'unsafe transaction lock')
                while True:
                    self._tick(cancel)
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        time.sleep(0.05)
                catalog._bound(self.root, base)
                _mkdir(self.work)
                receipt = _Receipt((self.root, self.work))
                try:
                    yield receipt
                except BaseException as exc:
                    receipt.sample('failed')
                    exc.receipt = receipt.result()
                    raise
            finally:
                os.close(fd)

    def _locations(self, carbon, mw_pc, mw_x360):
        carbon, mw_pc = _path(carbon), _path(mw_pc)
        if carbon.suffix.lower() == '.exe' and carbon.is_file():
            carbon = carbon.parent
        carbon = sources._folder(carbon)
        optional = _path(mw_x360) if mw_x360 is not None else None
        protected = [carbon, mw_pc] + ([optional] if optional is not None else [])
        for path in protected:
            if not path.exists():
                raise MissingAssets('required input is missing: ' + path.name)
            _require(not _overlaps(path, self.root) and not _overlaps(path, self.work_root),
                     'managed directories overlap an input tree')
        for i, path in enumerate(protected):
            _require(not any(_overlaps(path, other) for other in protected[i + 1:]),
                     'Carbon and MW input roles must be separate')
        if not mw_pc.is_dir():
            raise MissingAssets('required MW PC directory is missing')
        return carbon, mw_pc, optional

    def _inspect(self, carbon, mw_pc, mw_x360, cars):
        carbon, mw_pc, optional = self._locations(carbon, mw_pc, mw_x360)
        recognition = sources.recognise(carbon)
        _require(recognition['game'] == 'carbon' and recognition['platform'] in ('pc', 'x360'),
                 'only recognized Carbon PC or Carbon X360 input is supported')
        statuses = {c['car_hash']: c for c in recognition['cars']}
        for car in cars:
            row = statuses.get(sources.path_hash('CARS/' + car['folder']))
            if row is None or row['status'] == 'missing':
                raise MissingAssets('selected source pair is missing: ' + car['folder'])
            _require(row['status'] == 'importable', 'selected source pair is not verified: ' + car['folder'])
            for name in ('GEOMETRY.BIN', 'TEXTURES.BIN'):
                if not (mw_pc / 'CARS' / car['donors']['model'] / name).is_file():
                    raise MissingAssets('required MW PC donor pack is missing')
        if not (mw_pc / 'GLOBAL/GLOBALB.LZC').is_file():
            raise MissingAssets('required MW PC shared pack is missing')
        return carbon, mw_pc, optional, recognition

    def check(self, carbon, *, mw_pc, mw_x360=None, cars=()):
        """Read-only recognition; source status is distinct from output approval."""
        cars = _specs(cars) if cars else []
        source, mw, optional, recognition = self._inspect(carbon, mw_pc, mw_x360, cars)
        try:
            policy = catalog.packaged_policy()
            approval = dict(available=True, sha256=policy.sha256)
        except catalog.CatalogError as exc:
            approval = dict(available=False, reason=str(exc))
        return dict(inputs=dict(carbon=dict(path=str(source), game='carbon', platform=recognition['platform']),
            mw_pc=dict(path=str(mw), required=True),
            mw_x360=dict(path=str(optional) if optional else None, required=False, used=False)),
            recognition=recognition, approval=approval, output_verified=False)

    @staticmethod
    def _policy(required):
        try:
            return catalog.packaged_policy()
        except catalog.CatalogError:
            if required:
                raise
            return None

    def _boundary(self, phase, receipt, cancel, progress):
        self._tick(cancel)
        receipt.sample(phase)
        if progress is not None:
            progress(dict(phase=phase, seconds=time.monotonic() - receipt.start))
        self._tick(cancel)

    def _prepare(self, source, mw, recognition, car, policy, receipt, cancel, progress, measure,
                 repair_cache=False):
        platform, folder = recognition['platform'], car['folder']
        by_car = {c['car_hash']: c for c in recognition['cars']}
        expected = recognition['shared'] + by_car[sources.path_hash('CARS/' + folder)]['files']
        tick = lambda: self._tick(cancel)
        with _open_source(source) as reader:
            logical = sources._logical(reader, sources._physical(reader))
            bundle = None
            if platform == 'x360':
                bundle = xenon.resolve(reader, folder, car['donors']['model'], recognition=recognition)
                source_pins = {key: _pin(row) for key, row in bundle.inputs.items()}
                names = _source_names(platform, folder, source_pins)
            else:
                names = {sources.path_hash(n): n for n in
                         (f'CARS/{folder}/GEOMETRY.BIN', f'CARS/{folder}/TEXTURES.BIN',
                          'GLOBAL/GLOBALB.LZC', 'GLOBAL/ATTRIBUTES.BIN')}
                allowed = {r['path_hash']: r for r in expected}
                _require(names.keys() <= allowed.keys(), 'PC dependency missing from recognition')
                source_pins = {key: _pin(allowed[key]) for key in names}
            for key, pin in source_pins.items():
                _require(key in logical and _logical(reader, logical[key], tick=tick) == pin,
                         'selected logical source changed')
            mw_names = list(MW_SHARED) + [f'CARS/{car["donors"]["model"]}/{n}'
                                         for n in ('GEOMETRY.BIN', 'TEXTURES.BIN')]
            mw_pins = {name: catalog.fingerprint(mw / name) for name in mw_names if (mw / name).exists()}
            absent = sorted(set(mw_names) - mw_pins.keys())
            code, conversion_policy, environment = _code(platform, absent)
            identity = dict(schema=REVISION, car=car, platform=platform, source=source_pins,
                mw=mw_pins, converter=code.pins, conversion_policy=conversion_policy,
                approval=None if policy is None else dict(sha256=policy.sha256,
                                                         revision=catalog._policy(policy)['revision']))
            key = catalog.digest(identity)
            entry = self.work / ('c-' + key)

            def unchanged():
                tick()
                with _open_source(source) as fresh:
                    current = sources._logical(fresh, sources._physical(fresh))
                    for name, pin in source_pins.items():
                        _require(current.get(name) == logical[name] and
                                 _logical(fresh, current[name], tick=tick) == pin, 'source changed before activation')
                for name, pin in mw_pins.items():
                    _require(catalog.fingerprint(mw / name) == pin, 'MW dependency changed before activation')
                _require(all(not (mw / name).exists() for name in absent), 'MW dependency inventory changed')
                current_code, current_policy, _ = _code(platform, absent)
                _require(current_code.pins == code.pins and current_policy == conversion_policy,
                         'converter dependency changed before activation')

            if entry.exists() or entry.is_symlink():
                try:
                    row = self._cached(entry, identity, policy)
                except CacheError:
                    if not repair_cache:
                        raise
                    # Never overwrite rejected data or follow an unsafe cache
                    # root. Leave it retained for diagnosis; rebuild separately.
                    with catalog._directory(entry):
                        pass
                    self._boundary('cache_rejected', receipt, cancel, progress)
                    with catalog._directory(self.work) as fd:
                        os.rename(entry.name, '.rejected-' + uuid.uuid4().hex, src_dir_fd=fd, dst_dir_fd=fd)
                        os.fsync(fd)
                else:
                    unchanged()
                    return dict(row=row, entry=entry, reused=True, approved=True, unchanged=unchanged,
                                extraction_bytes=0, output_bytes=sum(p['size'] for p in row['outputs'].values()))
            if not measure:
                return dict(row=None, entry=None, reused=False, approved=False, unchanged=unchanged,
                            extraction_bytes=sum(p['size'] for p in source_pins.values()), output_bytes=None,
                            dependency_bytes=sum(p['size'] for p in mw_pins.values()) +
                                             sum(p['size'] for p in code.pins.values()))

            stage = self.work / ('.txn-' + uuid.uuid4().hex)
            _mkdir(stage)
            for group in ('source', 'mw', 'converter'):
                _mkdir(stage / group)
            extraction = 0
            for name, pin in source_pins.items():
                actual = _logical(reader, logical[name], target=stage / 'source' / names[name], tick=tick)
                _require(actual == pin, 'source changed during selected extraction')
                extraction += pin['size']
            for name, pin in mw_pins.items():
                actual = _range(mw / name, 0, pin['size'], target=stage / 'mw' / name, tick=tick)
                _require(actual == pin, 'MW dependency changed during snapshot')
            for name, path in code.files.items():
                pin = code.pins[name]
                if name in code.neutral:
                    _write(stage / 'converter' / name, code.neutral[name])
                    continue
                _require(_range(path, 0, pin['size'], target=stage / 'converter' / name, tick=tick) == pin,
                         'converter dependency changed during snapshot')
            _write(stage / 'converter/environment.json', environment)
            self._boundary('extracted', receipt, cancel, progress)
            donor = stage / 'mw/CARS' / car['donors']['model']
            with catalog._directory(stage) as stage_fd:
                report = self._convert(platform, folder, car, recognition, stage, stage_fd, donor)
            self._boundary('converted', receipt, cancel, progress)
            self._report(report, stage, identity, names)
            pins = {n: catalog.fingerprint(stage / 'outputs' / n) for n in _inventory(stage / 'outputs')}
            row = dict(catalog.reservations()[car['slot']], state='ready',
                origin=dict(game='carbon', platform=platform, folder=folder),
                display=car['display'], bindings=car['bindings'], donors=car['donors'], parts=car['parts'],
                converter=dict(revision=report['converter_revision'], policy=conversion_policy['sha256']),
                inputs=dict(source={names[k]: v for k, v in source_pins.items()},
                    donor={n: v for n, v in mw_pins.items() if n.startswith('CARS/' + car['donors']['model'] + '/')},
                    dependencies={n: v for n, v in mw_pins.items() if not n.startswith('CARS/' + car['donors']['model'] + '/')},
                    converter=code.pins), outputs=pins)
            row['identity'] = catalog.source_identity(**row['origin'])
            approved, reason = False, 'no packaged approvals'
            if policy is not None:
                try:
                    rows = catalog.reservations()
                    rows[car['slot']] = row
                    catalog.make_catalog(rows, policy)
                    approved, reason = True, ''
                except catalog.CatalogError as exc:
                    reason = str(exc)
            unchanged()
            _write(stage / 'receipt.json', catalog.encoded(dict(schema=REVISION, key=key, identity=identity, row=row)))
            if approved:
                _seal(stage)
                with catalog._directory(self.work) as fd:
                    os.rename(stage.name, entry.name, src_dir_fd=fd, dst_dir_fd=fd)
                    os.fsync(fd)
                self._cached(entry, identity, policy)
            else:
                entry = stage
            self._boundary('prepared', receipt, cancel, progress)
            return dict(row=row, entry=entry, reused=False, approved=approved, reason=reason,
                        unchanged=unchanged, extraction_bytes=extraction,
                        dependency_bytes=sum(p['size'] for p in mw_pins.values()) +
                                         sum(p['size'] for p in code.pins.values()),
                        output_bytes=sum(p['size'] for p in pins.values()))

    @staticmethod
    def _convert(platform, folder, car, recognition, stage, stage_fd, donor):
        """Write converter output only beneath the held, link-free stage.

        The converter resolves its output path once and then opens every
        component without following links. A fresh, unpublished leaf name means
        nothing can be planted at it before that resolution; a link planted at
        any other name, including 'outputs', is refused before a byte is written.
        A stage swapped during conversion fails the bind check and is rejected.
        """
        def absent(name):
            try:
                os.stat(name, dir_fd=stage_fd, follow_symlinks=False)
            except FileNotFoundError:
                return True
            return False

        _require(absent('outputs'), 'unexpected entry in the transaction stage')
        leaf = '.outputs-' + uuid.uuid4().hex
        if platform == 'pc':
            report = converter.convert(stage / 'source/CARS' / folder, donor,
                dict(carbon_global=stage / 'source/GLOBAL/GLOBALB.LZC',
                     attributes=stage / 'source/GLOBAL/ATTRIBUTES.BIN', mw_root=stage / 'mw'), stage / leaf)
        else:
            # Resolve again from the bounded, verified logical snapshot.
            # Conversion never needs the ISO's containing directory as an
            # output exclusion root, nor any unselected archive bytes.
            with _open_source(stage / 'source') as snapshot:
                frozen = xenon.resolve(snapshot, folder, car['donors']['model'], recognition=recognition)
                report = converter.convert_x360(frozen, donor, stage / leaf, mw_root=stage / 'mw')
        catalog._bound(stage, stage_fd)
        _require(stat.S_ISDIR(os.stat(leaf, dir_fd=stage_fd, follow_symlinks=False).st_mode) and absent('outputs'),
                 'unexpected entry in the transaction stage')
        os.rename(leaf, 'outputs', src_dir_fd=stage_fd, dst_dir_fd=stage_fd)
        os.fsync(stage_fd)
        return report

    @staticmethod
    def _report(report, stage, identity, names):
        _require(report['car'] == identity['car']['folder'] and
                 report['donor'] == identity['car']['donors']['model'] and
                 report['platform'] == identity['platform'] and
                 report['converter_revision'] == identity['conversion_policy']['revision'] and
                 report['converter_policy'] == identity['conversion_policy'], 'converter identity differs from plan')
        _require({n: _pin(p) for n, p in report['source_inputs'].items()} == identity['source'],
                 'converter source dependency closure differs from plan')
        expected = {str(stage / 'mw' / n): p['sha256'] for n, p in identity['mw'].items()}
        if identity['platform'] == 'pc':
            expected.update({str(stage / 'source' / names[n]): p['sha256'] for n, p in identity['source'].items()})
        else:
            expected.update({'x360:' + n: p['sha256'] for n, p in identity['source'].items()})
        _require(report['inputs'] == expected, 'converter omitted or added an input dependency')
        actual = {n: catalog.fingerprint(stage / 'outputs' / n)['sha256'] for n in _inventory(stage / 'outputs')}
        _require(actual == report['outputs'], 'written outputs differ from converter receipt')

    @staticmethod
    def _dirs(entry):
        return dict(source=entry / 'source', donor=entry / 'mw', dependencies=entry / 'mw',
                    converter=entry / 'converter')

    def _cached(self, entry, identity, policy):
        try:
            _require(policy is not None, 'cache requires packaged approval')
            _require(identity['approval'] == dict(sha256=policy.sha256,
                revision=catalog._policy(policy)['revision']), 'cache approval revision is stale')
            with catalog._directory(entry) as base:
                doc = catalog._decode(catalog._read(base, 'receipt.json'))
            _require(set(doc) == {'schema', 'key', 'identity', 'row'} and
                     type(doc['schema']) is int and doc['schema'] == REVISION and
                     doc['key'] == catalog.digest(identity) and
                     catalog.encoded(doc['identity']) == catalog.encoded(identity) and
                     entry.name == 'c-' + doc['key'], 'stale cache identity')
            row = doc['row']
            car = identity['car']
            _require(all(row[k] == car[k] for k in ('slot', 'display', 'bindings', 'donors', 'parts')) and
                     row['origin'] == dict(game='carbon', platform=identity['platform'], folder=car['folder']) and
                     row['converter'] == dict(revision=identity['conversion_policy']['revision'],
                                              policy=identity['conversion_policy']['sha256']), 'cache contract mismatch')
            source_names = _source_names(identity['platform'], car['folder'], identity['source'])
            prefix = 'CARS/' + car['donors']['model'] + '/'
            _require(row['inputs'] == dict(
                source={source_names[n]: p for n, p in identity['source'].items()},
                donor={n: p for n, p in identity['mw'].items() if n.startswith(prefix)},
                dependencies={n: p for n, p in identity['mw'].items() if not n.startswith(prefix)},
                converter=identity['converter']), 'cache dependency receipt mismatch')
            rows = catalog.reservations()
            rows[row['slot']] = row
            catalog.make_catalog(rows, policy)
            roots = self._dirs(entry)
            expected = {'receipt.json'}
            for group, pins in row['inputs'].items():
                for name, pin in pins.items():
                    path = roots[group] / name
                    _require(catalog.fingerprint(path) == pin, 'corrupt cache input')
                    expected.add(path.relative_to(entry).as_posix())
            for name, pin in row['outputs'].items():
                path = entry / 'outputs' / name
                _require(catalog.fingerprint(path) == pin, 'corrupt cache output')
                expected.add(path.relative_to(entry).as_posix())
            _require(set(_inventory(entry)) == expected, 'extra or missing cache files')
            return row
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise CacheError('cache rejected before activation: ' + str(exc)) from exc

    def _publication_bytes(self, rows, policy):
        """Exact additional payload allocation; an identical generation costs zero."""
        doc = catalog.make_catalog(rows, policy)
        sha = catalog.digest(doc)
        generation = self.root / ('g-' + sha)
        if generation.exists() or generation.is_symlink():
            with catalog._directory(self.root) as base:
                catalog._verify_generation(base, generation.name, sha, policy)
            return 0
        return sum(p['size'] for r in rows if r['state'] == 'ready' for p in r['outputs'].values())

    def _planned_publication(self, preparations, policy):
        current = self._current(policy) if policy is not None else None
        rows = _clone(current.catalog['cars']) if current is not None and current.catalog else catalog.reservations()
        unknown, compatible = False, True
        for item in preparations:
            row = item['row']
            if row is None:
                unknown = True
                continue
            previous = rows[row['slot']]
            if previous['state'] == 'ready' and catalog.slot_compatibility(previous) != catalog.slot_compatibility(row):
                compatible = False
            rows[row['slot']] = row
        if unknown:
            return None, compatible
        if policy is not None and all(p['approved'] for p in preparations):
            return self._publication_bytes(rows, policy), compatible
        return sum(p['size'] for r in rows if r['state'] == 'ready' for p in r['outputs'].values()), compatible

    def _space(self, preparations, receipt, publication_bytes):
        volumes = {}
        for role, root in (('work', self.work), ('catalog', self.root)):
            device, available = _volume(root)
            row = volumes.setdefault(device, dict(device=device, paths=[], extraction_bytes=0,
                staging_bytes=0, output_bytes=0, retention_bytes=0, margin_bytes=self.margin,
                available_bytes=available))
            row['paths'].append(str(root))
            initial = receipt.volumes.get(device, {})
            row['retention_bytes'] = initial.get('initial_bytes', 0)
            row['available_bytes'] = initial.get('initial_available_bytes', row['available_bytes'])
            for item in preparations:
                if role == 'work':
                    row['extraction_bytes'] += item['extraction_bytes']
                    extra = 0 if item['reused'] else item.get('dependency_bytes', 0)
                    if row['staging_bytes'] is not None:
                        row['staging_bytes'] = (None if item['output_bytes'] is None else
                                                row['staging_bytes'] + extra + (0 if item['reused'] else item['output_bytes']))
            if role == 'catalog':
                row['output_bytes'] = publication_bytes
        for row in volumes.values():
            values = [row[n] for n in ('extraction_bytes', 'staging_bytes', 'output_bytes', 'margin_bytes')]
            row['required_bytes'] = None if None in values else sum(values)
            row['sufficient'] = None if row['required_bytes'] is None else row['required_bytes'] <= row['available_bytes']
        return list(volumes.values())

    def plan(self, carbon, *, mw_pc, cars, mw_x360=None, measure=False, cancel=None, progress=None):
        """Plan without publishing. Unknown sizes are null, never extrapolations."""
        cars = _specs(cars)
        self._locations(carbon, mw_pc, mw_x360)
        with self._transaction(cancel) as receipt:
            source, mw, _, recognition = self._inspect(carbon, mw_pc, mw_x360, cars)
            policy = self._policy(False)
            preparations = [self._prepare(source, mw, recognition, car, policy, receipt, cancel, progress, measure)
                            for car in cars]
            publication_bytes, compatible = self._planned_publication(preparations, policy)
            receipt.sample('planned')
            return dict(measured=measure, approved=all(p['approved'] for p in preparations), compatible=compatible,
                platform=recognition['platform'], volumes=self._space(preparations, receipt, publication_bytes),
                cars=[dict(slot=c['slot'], reused=p['reused'], approved=p['approved'],
                           reason=p.get('reason', ''), contract=p['row']) for c, p in zip(cars, preparations)],
                receipt=receipt.result())

    def _current(self, policy):
        current = catalog.verify_current(self.root, policy=policy)
        if current.error and (self.root / 'current.json').exists():
            raise ImportError('current catalog is unavailable; disable or restore before reimport: ' + current.error)
        return current

    def _snapshot(self, policy):
        """Current state and its exact pointer bytes, read under the catalog
        writer lock so a direct catalog client cannot interleave between them."""
        with catalog._writer(self.root):
            current = self._current(policy)
            return current, self._pointer_bytes()

    def _pointer_bytes(self):
        return (self.root / 'current.json').read_bytes() if (self.root / 'current.json').exists() else None

    def _unchanged_since(self, baseline):
        # Called at 'before_publish', inside the catalog writer lock.
        _require(self._pointer_bytes() == baseline, 'catalog changed outside this transaction; retry')

    def _retained_input(self, row, policy):
        rejected = []
        for entry in sorted(self.work.glob('c-*')):
            try:
                with catalog._directory(entry) as base:
                    doc = catalog._decode(catalog._read(base, 'receipt.json'))
                _require(type(doc) is dict and 'row' in doc and 'identity' in doc, 'invalid retained cache receipt')
            except (OSError, ValueError) as exc:
                rejected.append(str(exc))
                continue
            if doc.get('row') == row:
                self._cached(entry, doc['identity'], policy)
                return entry
        if rejected:
            raise CacheError('retained dependency cache rejected; reimport required packs')
        raise MissingAssets('retained dependency cache is missing; reimport this pack before merging')

    def import_cars(self, carbon, *, mw_pc, cars, mw_x360=None, cancel=None, progress=None, repair_cache=False):
        """Prepare, approve and publish one complete transaction; merge installed packs."""
        _require(type(repair_cache) is bool, 'invalid cache repair option')
        cars = _specs(cars)
        self._locations(carbon, mw_pc, mw_x360)
        with self._transaction(cancel) as receipt:
            policy = self._policy(True)
            current, baseline = self._snapshot(policy)
            source, mw, _, recognition = self._inspect(carbon, mw_pc, mw_x360, cars)
            preparations = [self._prepare(source, mw, recognition, car, policy, receipt, cancel, progress, True,
                                          repair_cache=repair_cache)
                            for car in cars]
            _require(all(p['approved'] for p in preparations),
                     'converted output is not approved by the packaged policy')
            rows = _clone(current.catalog['cars']) if current.catalog else catalog.reservations()
            selection = {r['slot'] for r in current.selected}
            outputs, inputs = {}, {}
            replacing = {c['slot'] for c in cars}
            for row in rows:
                if row['state'] == 'ready' and row['slot'] not in replacing:
                    entry = self._retained_input(row, policy)
                    outputs[row['slot']], inputs[row['slot']] = entry / 'outputs', self._dirs(entry)
            for item in preparations:
                row, entry = item['row'], item['entry']
                previous = rows[row['slot']]
                _require(previous['state'] != 'ready' or
                         catalog.slot_compatibility(previous) == catalog.slot_compatibility(row),
                         'incompatible revision; retain the old generation and resolve required packs first')
                rows[row['slot']] = row
                selection.add(row['slot'])
                outputs[row['slot']], inputs[row['slot']] = entry / 'outputs', self._dirs(entry)
            publication_bytes = self._publication_bytes(rows, policy)
            volumes = self._space(preparations, receipt, publication_bytes)
            # Preparation is already on disk. Only the generation and margin
            # remain to allocate, including packs merged from retained caches.
            if shutil.disk_usage(self.root).free < publication_bytes + self.margin:
                raise OSError(errno.ENOSPC, 'insufficient space for durable catalog publication')

            def before_publish(point):
                self._boundary(point, receipt, cancel, progress)
                if point == 'before_publish':
                    self._unchanged_since(baseline)
                    for item in preparations:
                        item['unchanged']()
                    _require(catalog.packaged_policy() == policy, 'packaged approval policy changed')

            self._boundary('publishing', receipt, cancel, progress)
            pointer = catalog.publish(self.root, rows, outputs, inputs, policy=policy,
                                      selection=sorted(selection), fault=before_publish)
            # No cancellation/progress callbacks or required disk writes after
            # commit. A completed pointer must be reported as a completed import.
            receipt.sample('committed')
            return dict(pointer=pointer, reused=sum(p['reused'] for p in preparations),
                        volumes=volumes, receipt=receipt.result())

    def reimport(self, carbon, *, mw_pc, cars, mw_x360=None, cancel=None, progress=None):
        """Reuse good cache; quarantine and reconstruct a rejected preparation."""
        return self.import_cars(carbon, mw_pc=mw_pc, cars=cars, mw_x360=mw_x360,
                                cancel=cancel, progress=progress, repair_cache=True)

    def list_cars(self):
        """Verify current and retained generations; do not inspect media or saves."""
        with self._transaction(None) as receipt:
            policy = self._policy(False)
            current = catalog.verify_current(self.root, policy=policy)
            generations = []
            for path in sorted(self.root.glob('g-*')):
                try:
                    _require(policy is not None, 'no packaged approvals')
                    with catalog._directory(self.root) as base:
                        doc = catalog._verify_generation(base, path.name, path.name[2:], policy)
                    generations.append(dict(generation=path.name, verified=True,
                                            slots=[r['slot'] for r in doc['cars'] if r['state'] == 'ready']))
                except (OSError, ValueError) as exc:
                    generations.append(dict(generation=path.name, verified=False, reason=str(exc)))
            return dict(generation=current.generation, error=current.error,
                        cars=current.catalog['cars'] if current.catalog else catalog.reservations(),
                        selection=[r['slot'] for r in current.selected], generations=generations,
                        receipt=receipt.result())

    def disable(self, *, cancel=None, progress=None):
        with self._transaction(cancel) as receipt:
            pointer = catalog.disable(self.root,
                fault=lambda p: self._boundary(p, receipt, cancel, progress))
            receipt.sample('disabled')
            return dict(pointer=pointer, receipt=receipt.result())

    def remove(self, slots, *, cancel=None, progress=None):
        """Deselect only; keep all generations and stable IDs for missing saves."""
        slots = list(slots)
        _require(all(type(s) is int and 0 <= s < catalog.ADDITION_COUNT for s in slots), 'invalid removal slot')
        with self._transaction(cancel) as receipt:
            policy = self._policy(True)
            current, baseline = self._snapshot(policy)
            _require(current.catalog is not None, 'no current generation')
            selection = sorted({r['slot'] for r in current.selected} - set(slots))

            def before_publish(point):
                self._boundary(point, receipt, cancel, progress)
                if point == 'before_publish':
                    self._unchanged_since(baseline)

            pointer = catalog.set_selection(self.root, selection, policy=policy, fault=before_publish)
            receipt.sample('removed')
            return dict(pointer=pointer, receipt=receipt.result())

    def restore(self, generation, *, selection=None, required=None, cancel=None, progress=None):
        _require(required is None or type(required) is dict, 'required packs must map slots to signatures')
        with self._transaction(cancel) as receipt:
            policy = self._policy(True)
            _require(type(generation) is str and re.fullmatch('g-[0-9a-f]{64}', generation), 'invalid generation')
            with catalog._directory(self.root) as base:
                doc = catalog._verify_generation(base, generation, generation[2:], policy)
            selected = ([r['slot'] for r in doc['cars'] if r['state'] == 'ready']
                        if selection is None else list(selection))
            for slot, signature in (required or {}).items():
                _require(type(slot) is int and 0 <= slot < catalog.ADDITION_COUNT and slot in selected and
                         doc['cars'][slot]['state'] == 'ready' and
                         catalog.slot_compatibility(doc['cars'][slot]) == signature,
                         'retained pack cannot satisfy required save compatibility')
            pointer = catalog.restore_generation(self.root, generation, selection=selected, policy=policy,
                fault=lambda p: self._boundary(p, receipt, cancel, progress))
            receipt.sample('restored')
            return dict(pointer=pointer, receipt=receipt.result())
