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
import struct
import sys
import time
import uuid

from . import car_catalog as catalog, car_convert as converter, car_sources as sources
from . import car_x360_inputs as xenon
from .x360 import Source

REVISION = 1
LATER_PC_INPUT_LIMIT = 64 << 20
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


def _pin(row):
    return {k: row[k] for k in ('size', 'sha256')}


def _range(path, offset, size, *, target=None, stage=None, tick=lambda: None, packed_geometry=None):
    _require(type(size) is int and 0 <= size <= converter.MAX_FILE, 'logical range too large')
    with _asset(path) as fd, ExitStack() as stack:
        before = os.fstat(fd)
        _require(0 <= offset <= before.st_size and size <= before.st_size - offset,
                 'logical range exceeds container')
        digest, left = hashlib.sha256(), size
        os.lseek(fd, offset, os.SEEK_SET)
        sink = None
        packed_blocks = [] if packed_geometry is not None else None
        if target is not None:
            sink = stack.enter_context(os.fdopen(stage.open_file(target), 'wb'))
        try:
            while left:
                tick()
                block = os.read(fd, min(left, 1 << 20))
                _require(bool(block), 'source truncated while reading')
                digest.update(block)
                if packed_blocks is not None:
                    packed_blocks.append(block)
                elif sink is not None:
                    sink.write(block)
                left -= len(block)
            pin = dict(size=size, sha256=digest.hexdigest())
            if packed_blocks is not None:
                _require(pin == packed_geometry, 'source changed during selected extraction')
                pin = sources._decoded_geometry(b''.join(packed_blocks), sink=sink, tick=tick)
            if sink is not None:
                sink.flush()
                os.fsync(sink.fileno())
            after = os.fstat(fd)
            _require((before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
                     (after.st_size, after.st_mtime_ns, after.st_ctime_ns), 'source changed while reading')
        finally:
            if sink is not None:
                sink.close()
    return pin


def _logical(source, entry, **kwargs):
    key, offset, size = entry
    if source.handle:
        offset += source.files[key].offset
        path = source.path
    else:
        path = source.files[key]
    return _range(path, offset, size, **kwargs)


def _x360_donor_export(physical):
    return ('NFS/ZDIR.BIN' not in physical and
            not any(name.startswith('SOUND/ENGINE/') for name in physical))


@contextmanager
def _open_source(path):
    # The recognition context wraps every OSError from its body as a source
    # error. Keep transaction ENOSPC/EIO and interruption types intact instead.
    source = Source(path)
    try:
        yield source
    finally:
        source.close()


def _car_packs(folder):
    return (f'CARS/{folder}/GEOMETRY.BIN', f'CARS/{folder}/TEXTURES.BIN')


def _mw_names(game, donor):
    packs = _car_packs(donor)
    if game == 'carbon':
        return (*packs, *MW_SHARED)
    from .ps_namespace import TEMPLATE
    shared = ('GLOBAL/ATTRIBUTES.BIN',)
    if game == 'prostreet':
        shared = ('GLOBAL/GLOBALB.LZC', *shared)
    return tuple(dict.fromkeys((*packs, *_car_packs(TEMPLATE), *shared)))


def _mw_asset(root, name, game):
    path = root / name
    if game != 'carbon' and name.startswith('GLOBAL/') and not path.exists():
        retail = root / ('GLOBAL/GlobalB.lzc' if name.endswith('.LZC') else 'GLOBAL/attributes.bin')
        if retail.exists():
            return retail
    return path


def _source_names(platform, folder, identities, game='carbon', audio_names=()):
    names = [*_car_packs(folder), *sources.PC_CONVERSION_SHARED[game]]
    if platform == 'x360':
        names += ['GLOBAL/GLOBALB.BUN', 'CARS/TEXTURES.BIN', 'GLOBAL/INGAMEA.BUN']
    names += [n for n in audio_names if game == 'carbon' and n.startswith('SOUND/ENGINE/')]
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


def _later_route(game):
    from . import ps_namespace, v25_batch, v25_car
    modules = [ps_namespace, v25_batch, v25_car]
    if game == 'prostreet':
        from . import car_performance, ps_attrib
        return ps_namespace, modules + [ps_attrib, car_performance]
    _require(game == 'undercover', 'unsupported PC converter')
    from . import car_performance, ps_attrib, uc_attrib, uc_batch, ucpc_car
    return ucpc_car, modules + [ps_attrib, uc_attrib, uc_batch, ucpc_car, car_performance]


def _code(platform, absent, game='carbon', *, mac_audio=False):
    code = _Code()
    if game == 'carbon':
        policy, _ = converter._converter_policy(platform, None, code, mac_audio)
    else:
        from . import car_texture_namespace, huff, x360, xdvdfs
        _, route_modules = _later_route(game)
        for module in (converter, car_texture_namespace, huff, x360, xdvdfs, *route_modules):
            code.read(Path(module.__file__))
        policy = dict(revision=1, game=game, platform=platform, modules=code.pins.copy())
        policy['sha256'] = catalog.digest(policy)
    for path in (Path(__file__), sources.MANIFEST,
                 Path(__file__).resolve().parents[2] / 'runtime/huff.h'):
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
                     'Later-game and MW input roles must be separate')
        if not mw_pc.is_dir():
            raise MissingAssets('required MW PC directory is missing')
        return carbon, mw_pc, optional

    def _inspect(self, carbon, mw_pc, mw_x360, cars):
        carbon, mw_pc, optional = self._locations(carbon, mw_pc, mw_x360)
        try:
            recognition = sources.recognise(carbon)
        except sources.SourceError as error:
            raise ImportError(str(error)) from error
        game, platform = recognition['game'], recognition['platform']
        _require(not cars or (game, platform) in catalog.IMPORT_SOURCES,
                 'this game and platform have no approved conversion route')
        statuses = {c['car_hash']: c for c in recognition['cars']}
        for car in cars:
            row = statuses.get(sources.path_hash('CARS/' + car['folder']))
            if row is None or row['status'] == 'missing':
                raise MissingAssets('selected source pair is missing: ' + car['folder'])
            _require(row['status'] == 'importable', 'selected source pair is not verified: ' + car['folder'])
            for name in ('GEOMETRY.BIN', 'TEXTURES.BIN'):
                if not (mw_pc / 'CARS' / car['donors']['model'] / name).is_file():
                    raise MissingAssets('required MW PC donor pack is missing')
            if game != 'carbon' and any(not _mw_asset(mw_pc, name, game).is_file()
                                        for name in _mw_names(game, car['donors']['model'])):
                raise MissingAssets('required MW PC material template or shared pack is missing')
        if game == 'carbon' and not (mw_pc / 'GLOBAL/GLOBALB.LZC').is_file():
            raise MissingAssets('required MW PC shared pack is missing')
        return carbon, mw_pc, optional, recognition

    def check(self, carbon, *, mw_pc, mw_x360=None, cars=()):
        """Read-only recognition; source status is distinct from output approval."""
        cars = _specs(cars) if cars else []
        source, mw, optional, recognition = self._inspect(carbon, mw_pc, mw_x360, cars)
        try:
            policy = catalog.packaged_policy()
            approval = dict(available=(recognition['game'], recognition['platform']) in catalog.IMPORT_SOURCES,
                            sha256=policy.sha256)
        except catalog.CatalogError as exc:
            approval = dict(available=False, reason=str(exc))
        return dict(inputs=dict(carbon=dict(path=str(source), game=recognition['game'], platform=recognition['platform']),
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

    @staticmethod
    def _later_rows(recognition, policy):
        if recognition['game'] == 'carbon':
            return {}
        _require(policy is not None, 'No packaged approvals for this car source.')
        from . import car_selection
        return {row['slot']: row for row in car_selection.load(policy=policy)
                if (row['origin']['game'], row['origin']['platform']) ==
                   (recognition['game'], recognition['platform'])}

    @staticmethod
    def _later_mw_pins(paths, reviewed, car, source_bytes):
        from .car_selection import spec
        mismatch = 'The selected MW donor or template files do not match the approved copies for this car.'
        oversized = 'The selected car and MW files exceed the conversion size limit.'
        _require(reviewed is not None and spec(reviewed) == car, mismatch)
        expected = reviewed['inputs']['donor'] | reviewed['inputs']['dependencies']
        _require(paths.keys() == expected.keys(), mismatch)
        remaining = LATER_PC_INPUT_LIMIT - source_bytes
        for name, path in paths.items():
            with _asset(path) as fd:
                size = os.fstat(fd).st_size
                remaining -= size
                _require(remaining >= 0, oversized)
                _require(size == expected[name]['size'] and
                         catalog._record(fd, limit=size) == expected[name], mismatch)
        return expected

    def _prepare(self, source, mw, recognition, car, policy, receipt, cancel, progress,
                 *, later_rows, repair_cache=False):
        game, platform, folder = recognition['game'], recognition['platform'], car['folder']
        by_car = {c['car_hash']: c for c in recognition['cars']}
        expected = recognition['shared'] + by_car[sources.path_hash('CARS/' + folder)]['files']
        tick = lambda: self._tick(cancel)
        with _open_source(source) as reader:
            physical = sources._physical(reader)
            logical = sources._logical(reader, physical, engine_audio=platform == 'x360')
            mac_audio = False
            audio_paths = ()
            donor_export = False
            packed_geometry = {}
            if platform == 'x360':
                bundle = xenon.resolve(reader, folder, car['donors']['model'], recognition=recognition)
                source_pins = {key: _pin(row) for key, row in bundle.inputs.items()}
                names = _source_names(platform, folder, source_pins)
            else:
                names = {sources.path_hash(n): n for n in (*_car_packs(folder), *sources.PC_CONVERSION_SHARED[game])}
                allowed = {r['path_hash']: r for r in expected}
                _require(names.keys() <= allowed.keys(), 'PC dependency missing from recognition')
                source_pins = {key: _pin(allowed[key]) for key in names}
                packed_geometry = {key: allowed[key]['packed'] for key in names if 'packed' in allowed[key]}
            for key, pin in source_pins.items():
                _require(key in logical and _logical(reader, logical[key], tick=tick) == packed_geometry.get(key, pin),
                         'selected logical source changed')
            if game == 'carbon':
                from . import car_audio_pack
                attribute_id = sources.path_hash('GLOBAL/ATTRIBUTES.BIN')
                x360 = platform == 'x360'
                attributes_pins = (car_audio_pack.X360_ATTRIBUTES,) if x360 else car_audio_pack.ATTRIBUTES
                mac_audio = source_pins[attribute_id]['sha256'] in attributes_pins
                donor_export = mac_audio and x360 and _x360_donor_export(physical)
                mac_audio = mac_audio and not donor_export
                if mac_audio:
                    attr = sources._read(reader, logical[attribute_id])
                    audio_pins = converter.x360_audio_inputs(attr, folder, logical) if x360 else None
                    audio_paths = (audio_pins if x360 else
                        car_audio_pack.resource_paths(attr, folder, (n for n in physical if n.startswith('SOUND/ENGINE/'))))
                    for name in audio_paths:
                        key = sources.path_hash(name)
                        _require(key not in source_pins, 'colliding audio input')
                        if not x360:
                            _require(key not in logical, 'colliding audio input')
                            logical[key] = (physical[name], 0, reader.size(physical[name]))
                        source_pins[key] = _logical(reader, logical[key], tick=tick)
                        if x360:
                            _require(source_pins[key] == audio_pins[name],
                                     'Console engine audio does not match the approved file: ' + name + '.')
                        names[key] = name
            mw_names = _mw_names(game, car['donors']['model'])
            mw_paths = {name: _mw_asset(mw, name, game) for name in mw_names}
            extraction = sum(p['size'] for p in source_pins.values())
            reviewed = later_rows.get(car['slot'])
            if game == 'carbon':
                mw_pins = {name: catalog.fingerprint(path) for name, path in mw_paths.items() if path.exists()}
            else:
                mw_pins = self._later_mw_pins(mw_paths, reviewed, car, extraction)
            absent = sorted(set(mw_names) - mw_pins.keys())
            code, conversion_policy, environment = _code(platform, absent, game, mac_audio=mac_audio)
            dependencies = sum(p['size'] for p in mw_pins.values()) + sum(p['size'] for p in code.pins.values())
            identity = dict(schema=REVISION, car=car, game=game, platform=platform, source=source_pins,
                mw=mw_pins, converter=code.pins, conversion_policy=conversion_policy,
                approval=None if policy is None else dict(sha256=policy.sha256,
                                                         revision=catalog._policy(policy)['revision']))
            key = catalog.digest(identity)
            entry = self.work / ('c-' + key)

            def unchanged():
                tick()
                with _open_source(source) as fresh:
                    files = sources._physical(fresh)
                    if donor_export:
                        _require(_x360_donor_export(files), 'Console engine audio files changed before activation.')
                    current = sources._logical(fresh, files, engine_audio=platform == 'x360')
                    if platform == 'pc':
                        for name in audio_paths:
                            if name in files:
                                current[sources.path_hash(name)] = (files[name], 0, fresh.size(files[name]))
                    for name, pin in source_pins.items():
                        _require(current.get(name) == logical[name] and
                                 _logical(fresh, current[name], tick=tick) == packed_geometry.get(name, pin),
                                 'source changed before activation')
                for name, pin in mw_pins.items():
                    path = _mw_asset(mw, name, game)
                    _require(path == mw_paths[name] and catalog.fingerprint(path) == pin,
                             'MW dependency changed before activation')
                _require(all(not _mw_asset(mw, name, game).exists() for name in absent), 'MW dependency inventory changed')
                current_code, current_policy, _ = _code(platform, absent, game, mac_audio=mac_audio)
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
        input_pins = dict(source={names[k]: v for k, v in source_pins.items()},
                          donor={n: v for n, v in mw_pins.items() if n.startswith('CARS/' + car['donors']['model'] + '/')},
                          dependencies={n: v for n, v in mw_pins.items() if not n.startswith('CARS/' + car['donors']['model'] + '/')},
                          converter=code.pins)
        expected_output = sum(p['size'] for p in reviewed['outputs'].values()) if reviewed else 0
        planned_row = reviewed if reviewed and reviewed['inputs'] == input_pins else None

        def finish():
            _require(shutil.disk_usage(self.work).free >= extraction + dependencies + expected_output + self.margin,
                     'There is not enough free disk space to prepare the selected car.')
            stage = self.work / ('.txn-' + uuid.uuid4().hex)
            with catalog._directory(self.work) as work_fd, catalog._stage(work_fd, stage.name) as owned:
                for group in ('source', 'mw', 'converter'):
                    with owned.directory(group):
                        pass
                with _open_source(source) as fresh:
                    for name, pin in source_pins.items():
                        actual = _logical(fresh, logical[name], stage=owned, target='source/' + names[name],
                                          tick=tick, packed_geometry=packed_geometry.get(name))
                        _require(actual == pin, 'source changed during selected extraction')
                for name, pin in mw_pins.items():
                    actual = _range(mw_paths[name], 0, pin['size'], stage=owned, target='mw/' + name, tick=tick)
                    _require(actual == pin, 'MW dependency changed during snapshot')
                for name, path in code.files.items():
                    pin = code.pins[name]
                    if name in code.neutral:
                        owned.write('converter/' + name, code.neutral[name])
                        continue
                    _require(_range(path, 0, pin['size'], stage=owned, target='converter/' + name, tick=tick) == pin,
                             'converter dependency changed during snapshot')
                owned.write('converter/environment.json', environment)
                self._boundary('extracted', receipt, cancel, progress)
                donor = stage / 'mw/CARS' / car['donors']['model']
                catalog._bound(stage, owned.fd)
                report = self._convert(platform, folder, car, recognition, stage, owned, donor, names, identity)
                self._boundary('converted', receipt, cancel, progress)
                self._report(report, stage, identity, names)
                pins = {n: catalog.fingerprint(stage / 'outputs' / n) for n in _inventory(stage / 'outputs')}
                row = dict(catalog.reservations()[car['slot']], state='ready',
                    origin=dict(game=game, platform=platform, folder=folder),
                    display=car['display'], bindings=car['bindings'], donors=car['donors'], parts=car['parts'],
                    converter=dict(revision=report['converter_revision'], policy=conversion_policy['sha256']),
                    inputs=input_pins, outputs=pins)
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
                owned.write('receipt.json', catalog.encoded(dict(schema=REVISION, key=key, identity=identity, row=row)))
                self._boundary('prepared', receipt, cancel, progress)
                if approved:
                    owned.seal()
                    catalog._bound(stage, owned.fd)
                    owned.publish(entry.name)
                    os.fsync(work_fd)
                    self._cached(entry, identity, policy)
                return dict(row=row, entry=entry if approved else None, approved=approved, reason=reason,
                            output_bytes=sum(p['size'] for p in pins.values()))

        return dict(row=None, entry=None, reused=False, approved=False, unchanged=unchanged,
                    extraction_bytes=extraction, output_bytes=None, dependency_bytes=dependencies,
                    known_output_bytes=expected_output, planned_row=planned_row, slot=car['slot'], finish=finish)

    @staticmethod
    def _convert(platform, folder, car, recognition, stage, owned, donor, names, identity):
        """Track the converter's output entries when they are created."""
        _require(catalog._entry_stat(owned.fd, 'outputs') is None, 'unexpected entry in the transaction stage')

        def write(out, files, protected):
            converter.check_output_path(out, protected)
            catalog._bound(stage, owned.fd)
            for name, data in files.items():
                _require(Path(name).name == name, 'output filename must be a basename')
                if data is not None:
                    owned.write('outputs/' + name, data)

        out = stage / 'outputs'
        game = recognition['game']
        if game != 'carbon':
            report = CarImporter._convert_later_pc(game, stage, folder, donor.name, names, identity, write)
        elif platform == 'pc':
            report = converter.convert(stage / 'source/CARS' / folder, donor,
                dict(carbon_global=stage / 'source/GLOBAL/GLOBALB.LZC',
                     attributes=stage / 'source/GLOBAL/ATTRIBUTES.BIN', mw_root=stage / 'mw'), out,
                mac_audio=any(n.startswith('SOUND/ENGINE/') for n in names.values()), writer=write)
        else:
            # Resolve again from the bounded, verified logical snapshot.
            # Conversion never needs the ISO's containing directory as an
            # output exclusion root, nor any unselected archive bytes.
            with _open_source(stage / 'source') as snapshot:
                frozen = xenon.resolve(snapshot, folder, car['donors']['model'], recognition=recognition)
                report = converter.convert_x360(frozen, donor, out, mw_root=stage / 'mw',
                    mac_audio=any(n.startswith('SOUND/ENGINE/') for n in names.values()), writer=write)
        catalog._bound(stage, owned.fd)
        return report

    @staticmethod
    def _convert_later_pc(game, stage, car, donor, names, identity, write):
        policy = identity['conversion_policy']

        def read(path, pin):
            with _asset(path) as fd, os.fdopen(os.dup(fd), 'rb') as stream:
                size = os.fstat(fd).st_size
                _require(size == pin['size'] and size <= LATER_PC_INPUT_LIMIT,
                         'A verified car input changed before conversion.')
                data = stream.read(size + 1)
            digest = hashlib.sha256(data).hexdigest()
            _require(len(data) == size and digest == pin['sha256'],
                     'A verified car input changed before conversion.')
            inputs[str(path)] = digest
            return data

        inputs = {}
        source = {name: read(stage / 'source' / name, identity['source'][key]) for key, name in names.items()}
        mw = {name: read(stage / 'mw' / name, pin) for name, pin in identity['mw'].items()}
        route, _ = _later_route(game)
        try:
            outputs = route.convert(car, donor, source, mw)
        except (converter.ConversionError, IndexError, KeyError, StopIteration, OverflowError, struct.error) as error:
            raise ImportError('Setup could not convert the selected car. Check the game files and retry.') from error
        write(stage / 'outputs', outputs, (stage / 'source', stage / 'mw'))
        return dict(car=car, donor=donor, game=game, platform='pc', converter_revision=policy['revision'],
                    converter_policy=policy, inputs=inputs,
                    source_inputs={key: dict(size=len(source[name]), sha256=inputs[str(stage / 'source' / name)])
                                   for key, name in names.items()},
                    outputs={name: hashlib.sha256(data).hexdigest() for name, data in outputs.items()})

    @staticmethod
    def _report(report, stage, identity, names):
        _require(report['car'] == identity['car']['folder'] and
                 report['donor'] == identity['car']['donors']['model'] and
                 report.get('game', 'carbon') == identity['game'] and report['platform'] == identity['platform'] and
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

    def _cached(self, entry, identity, policy, *, retained=False):
        try:
            _require(policy is not None, 'cache requires packaged approval')
            legacy = identity['approval'] == dict(
                sha256=catalog.LEGACY_POLICY_SHA256, revision=catalog.LEGACY_POLICY_REVISION)
            retained_policy = retained and identity['approval'] in (
                dict(sha256=catalog.LEGACY_POLICY_SHA256, revision=catalog.LEGACY_POLICY_REVISION),
                dict(sha256=catalog.PRE_AUDIO_POLICY_SHA256, revision=catalog.PRE_AUDIO_POLICY_REVISION),
                dict(sha256=catalog.PC_AUDIO_POLICY_SHA256, revision=catalog.PC_AUDIO_POLICY_REVISION),
                dict(sha256=catalog.X360_AUDIO_POLICY_SHA256, revision=catalog.X360_AUDIO_POLICY_REVISION),
                dict(sha256=catalog.AUDIO_V7_POLICY_SHA256, revision=7),
                dict(sha256=catalog.CPF1_POLICY_SHA256, revision=5),
                dict(sha256=catalog.CPF2_POLICY_SHA256, revision=6),
                dict(sha256=catalog.CPF3_POLICY_SHA256, revision=7),
                dict(sha256=catalog.PRE_RETAIL_ATTRIBUTES_POLICY_SHA256, revision=8),
                dict(sha256=catalog.RETAIL_ATTRIBUTES_POLICY_SHA256, revision=9),
                dict(sha256=catalog.PS_PERFORMANCE_POLICY_SHA256, revision=9),
                dict(sha256=catalog.PRE_UC_PERFORMANCE_POLICY_SHA256, revision=11),
            )
            _require(retained_policy or identity['approval'] == dict(sha256=policy.sha256,
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
            game = identity.get('game', 'carbon') if legacy else identity['game']
            _require(all(row[k] == car[k] for k in ('slot', 'display', 'bindings', 'donors', 'parts')) and
                     row['origin'] == dict(game=game, platform=identity['platform'], folder=car['folder']) and
                     row['converter'] == dict(revision=identity['conversion_policy']['revision'],
                                              policy=identity['conversion_policy']['sha256']), 'cache contract mismatch')
            source_names = _source_names(identity['platform'], car['folder'], identity['source'], game,
                                         row['inputs']['source'])
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

    def _planned_publication(self, preparations, policy, current):
        absent = catalog.reservations()
        rows = _clone(current.catalog['cars']) if current is not None and current.catalog else absent.copy()
        unknown, compatible, known_bytes = False, True, 0
        for item in preparations:
            row = item['row'] or item.get('planned_row')
            if row is None:
                unknown = True
                slot = item['slot']
                rows[slot] = absent[slot]
                known_bytes += item['known_output_bytes']
                continue
            previous = rows[row['slot']]
            if previous['state'] == 'ready' and catalog.slot_compatibility(previous) != catalog.slot_compatibility(row):
                compatible = False
            rows[row['slot']] = row
        known_bytes += sum(p['size'] for r in rows if r['state'] == 'ready' for p in r['outputs'].values())
        if unknown:
            return None, known_bytes, compatible
        if policy is not None and all(p['approved'] or p.get('planned_row') is not None for p in preparations):
            known_bytes = self._publication_bytes(rows, policy)
        return known_bytes, known_bytes, compatible

    def _space(self, preparations, receipt, publication_bytes, publication_minimum=0):
        volumes = {}
        for role, root in (('work', self.work), ('catalog', self.root)):
            device, available = _volume(root)
            row = volumes.setdefault(device, dict(device=device, paths=[], extraction_bytes=0,
                staging_bytes=0, output_bytes=0, retention_bytes=0, margin_bytes=self.margin,
                minimum_bytes=self.margin, available_bytes=available))
            row['paths'].append(str(root))
            initial = receipt.volumes.get(device, {})
            row['retention_bytes'] = initial.get('initial_bytes', 0)
            row['available_bytes'] = initial.get('initial_available_bytes', row['available_bytes'])
            for item in preparations:
                if role == 'work':
                    row['extraction_bytes'] += item['extraction_bytes']
                    extra = 0 if item['reused'] else item.get('dependency_bytes', 0)
                    row['minimum_bytes'] += item['extraction_bytes'] + extra
                    if not item['reused']:
                        row['minimum_bytes'] += (item['known_output_bytes'] if item['output_bytes'] is None
                                                 else item['output_bytes'])
                    if row['staging_bytes'] is not None:
                        row['staging_bytes'] = (None if item['output_bytes'] is None else
                                                row['staging_bytes'] + extra + (0 if item['reused'] else item['output_bytes']))
            if role == 'catalog':
                row['output_bytes'] = publication_bytes
                row['minimum_bytes'] += publication_minimum if publication_bytes is None else publication_bytes
        for row in volumes.values():
            values = [row[n] for n in ('extraction_bytes', 'staging_bytes', 'output_bytes', 'margin_bytes')]
            row['required_bytes'] = None if None in values else sum(values)
            row['sufficient'] = None if row['required_bytes'] is None else row['required_bytes'] <= row['available_bytes']
        return list(volumes.values())

    def _finish_preparations(self, preparations, receipt, policy, current):
        cold = [item for item in preparations if not item['reused']]
        if not cold:
            return
        work_minimum = self.margin + sum(p['extraction_bytes'] + p['dependency_bytes'] + p['known_output_bytes']
                                         for p in cold)
        _require(_volume(self.work)[1] >= work_minimum,
                 'There is not enough free disk space to prepare the selected car.')
        publication_bytes, minimum, _ = self._planned_publication(preparations, policy, current)
        for volume in self._space(preparations, receipt, publication_bytes, minimum):
            if _volume(Path(volume['paths'][0]))[1] < volume['minimum_bytes']:
                raise OSError(errno.ENOSPC, 'insufficient space for durable catalog publication')
        for item in cold:
            item.update(item.pop('finish')())

    def plan(self, carbon, *, mw_pc, cars, mw_x360=None, measure=False, cancel=None, progress=None):
        """Plan without publishing. Unknown sizes are null, never extrapolations."""
        cars = _specs(cars)
        self._locations(carbon, mw_pc, mw_x360)
        with self._transaction(cancel) as receipt:
            source, mw, _, recognition = self._inspect(carbon, mw_pc, mw_x360, cars)
            policy = self._policy(False)
            later_rows = self._later_rows(recognition, policy)
            preparations = [self._prepare(source, mw, recognition, car, policy, receipt, cancel, progress,
                                          later_rows=later_rows)
                            for car in cars]
            current = self._current(policy) if policy is not None else None
            if measure:
                self._finish_preparations(preparations, receipt, policy, current)
            publication_bytes, minimum, compatible = self._planned_publication(preparations, policy, current)
            receipt.sample('planned')
            return dict(measured=measure, approved=all(p['approved'] for p in preparations), compatible=compatible,
                platform=recognition['platform'], volumes=self._space(preparations, receipt, publication_bytes, minimum),
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
                self._cached(entry, doc['identity'], policy, retained=True)
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
            later_rows = self._later_rows(recognition, policy)
            preparations = [self._prepare(source, mw, recognition, car, policy, receipt, cancel, progress,
                                          later_rows=later_rows, repair_cache=repair_cache)
                            for car in cars]
            self._finish_preparations(preparations, receipt, policy, current)
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
