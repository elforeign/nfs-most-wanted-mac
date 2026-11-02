"""Public added-car contracts and atomic, verified local generations.

Only an independently pinned packaged policy authorizes activation. There are
no built-in approvals. See runtime/docs/added-car-catalog.md for the wire format
and the trust boundary required of callers and runtime readers.
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid

SLOT_COUNT = 90
ADDITION_COUNT = 76
FIRST_ID = 84
INPUT_GROUPS = ('source', 'dependencies', 'donor', 'converter')
REQUIRED_OUTPUTS = frozenset(('GEOMETRY.BIN', 'TEXTURES.BIN', 'SOLIDMAP.BIN'))
MAX_JSON = 4 << 20
MAX_FILE = 2 << 30
# Filled by the release's approval step, together with the runtime trust root.
# A digest supplied in a local catalog or beside a policy is never a trust root.
PACKAGED_POLICY_SHA256 = None
POLICY_PATH = Path(__file__).with_name('car_catalog.approvals.json')
_HEX = re.compile(r'[0-9a-f]{64}\Z')
_TOKEN = re.compile(r'[A-Z0-9_]{1,31}\Z')
_OUTPUT = re.compile(r'[A-Z0-9_]{1,31}\.BIN\Z')
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


class CatalogError(ValueError):
    """Invalid, changed, unapproved or unsafe catalog data."""


def _require(ok, message):
    if not ok:
        raise CatalogError(message)


def encoded(value):
    """Canonical wire bytes: sorted keys, compact ASCII JSON, final newline."""
    return (json.dumps(value, sort_keys=True, separators=(',', ':'),
                       ensure_ascii=True, allow_nan=False) + '\n').encode('ascii')


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def _keys(value, required):
    _require(type(value) is dict and set(value) == set(required), 'unexpected fields')


def _hex(value):
    _require(type(value) is str and _HEX.fullmatch(value), 'invalid SHA-256')


def _integer(value, lo, hi):
    _require(type(value) is int and lo <= value <= hi, 'integer outside contract')


def _relative(value):
    _require(type(value) is str and 0 < len(value) <= 512 and
             '\\' not in value and '\0' not in value and
             all(p not in ('', '.', '..') for p in value.split('/')) and
             all(32 <= ord(c) < 127 for c in value), 'unsafe relative path')
    return value.split('/')


def _absolute(path):
    path = os.fspath(path)
    _require('\0' not in path and '..' not in Path(path).parts, 'unsafe root path')
    return os.path.abspath(path)


@contextmanager
def _directory(path, create=False):
    """Walk every component using directory descriptors; never follow links."""
    path = _absolute(path)
    fd = os.open('/', _DIR_FLAGS)
    try:
        for part in path.split('/')[1:]:
            if not part:
                continue
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                    os.fsync(fd)
                except FileExistsError:
                    pass
            nxt = os.open(part, _DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = nxt
        yield fd
    finally:
        os.close(fd)


def _bound(path, fd):
    with _directory(path) as current:
        a, b = os.fstat(current), os.fstat(fd)
        _require((a.st_dev, a.st_ino) == (b.st_dev, b.st_ino), 'directory changed')


@contextmanager
def _file(base, name):
    parts = _relative(name)
    fd = os.dup(base)
    try:
        for part in parts[:-1]:
            nxt = os.open(part, _DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = nxt
        leaf = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            st = os.fstat(leaf)
            _require(stat.S_ISREG(st.st_mode) and st.st_nlink == 1 and
                     st.st_size <= MAX_FILE, 'unsafe or oversized file')
            yield leaf
        finally:
            os.close(leaf)
    finally:
        os.close(fd)


def _record(fd, sink=None):
    before = os.fstat(fd)
    h, size = hashlib.sha256(), 0
    while True:
        data = os.read(fd, 1 << 20)
        if not data:
            break
        size += len(data)
        _require(size <= MAX_FILE, 'file grew beyond limit')
        h.update(data)
        if sink is not None:
            _write_all(sink, data)
    after = os.fstat(fd)
    _require((before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
             (after.st_size, after.st_mtime_ns, after.st_ctime_ns) and
             size == before.st_size, 'file changed while reading')
    return dict(size=size, sha256=h.hexdigest())


def fingerprint(path):
    """Hash a regular local file without following any symlink component."""
    path = _absolute(path)
    with _directory(os.path.dirname(path)) as base, _file(base, os.path.basename(path)) as fd:
        return _record(fd)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, 'duplicate JSON key')
        result[key] = value
    return result


def _decode(data):
    _require(len(data) <= MAX_JSON, 'JSON exceeds limit')
    try:
        value = json.loads(data, object_pairs_hook=_unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(CatalogError('nonfinite JSON')))
        _require(encoded(value) == data, 'noncanonical JSON')
        return value
    except (UnicodeError, RecursionError, TypeError, ValueError) as exc:
        raise CatalogError('invalid canonical JSON: ' + str(exc)) from exc


def _read(base, name):
    with _file(base, name) as fd:
        data = bytearray()
        while len(data) <= MAX_JSON:
            block = os.read(fd, min(65536, MAX_JSON + 1 - len(data)))
            if not block:
                break
            data.extend(block)
        _require(len(data) <= MAX_JSON, 'JSON exceeds limit')
        return bytes(data)


@dataclass(frozen=True)
class Policy:
    """Pinned canonical bytes; built by load_policy, never from a catalog."""
    data: bytes
    sha256: str


def load_policy(path, expected_sha256):
    """expected_sha256 MUST come from trusted package code, never local data."""
    _hex(expected_sha256)
    path = _absolute(path)
    with _directory(os.path.dirname(path)) as base:
        data = _read(base, os.path.basename(path))
    _require(hashlib.sha256(data).hexdigest() == expected_sha256, 'untrusted approval policy')
    policy = Policy(data, expected_sha256)
    _policy(policy)
    return policy


def packaged_policy():
    _require(PACKAGED_POLICY_SHA256 is not None, 'no packaged approvals')
    return load_policy(POLICY_PATH, PACKAGED_POLICY_SHA256)


def _policy(policy):
    _require(type(policy) is Policy, 'pinned policy required')
    _hex(policy.sha256)
    _require(hashlib.sha256(policy.data).hexdigest() == policy.sha256, 'policy bytes changed')
    doc = _decode(policy.data)
    _keys(doc, ('schema', 'revision', 'slots'))
    _require(type(doc['schema']) is int and doc['schema'] == 1, 'unsupported policy schema')
    _integer(doc['revision'], 1, 2147483647)
    _require(type(doc['slots']) is list and len(doc['slots']) == SLOT_COUNT, 'policy reservations required')
    identities = set()
    for slot, row in enumerate(doc['slots']):
        _keys(row, ('slot', 'identity', 'variants'))
        _integer(row['slot'], slot, slot)
        _require(type(row['variants']) is list and len(row['variants']) <= 256, 'invalid policy variants')
        if row['identity'] is None:
            _require(not row['variants'], 'approval without reserved identity')
        else:
            _hex(row['identity'])
            _require(row['identity'] not in identities, 'reassigned policy identity')
            identities.add(row['identity'])
        seen = set()
        for variant in row['variants']:
            _keys(variant, ('contract', 'compatibility'))
            _hex(variant['contract'])
            _hex(variant['compatibility'])
            _require(variant['contract'] not in seen, 'duplicate policy variant')
            seen.add(variant['contract'])
    return doc


def reservations():
    """Every missing slot remains explicit; swaps have id=0 and swap_index."""
    return [dict(slot=s, id=FIRST_ID + s if s < ADDITION_COUNT else 0,
                 role='addition' if s < ADDITION_COUNT else 'swap',
                 swap_index=None if s < ADDITION_COUNT else s - ADDITION_COUNT,
                 state='absent') for s in range(SLOT_COUNT)]


def source_identity(game, platform, folder):
    """Logical identity survives platform changes; full contracts pin platform."""
    return digest(dict(game=game, folder=folder))


def slot_compatibility(row):
    """Save/part meaning; excludes presentation, outputs and converter revision."""
    return digest({k: row[k] for k in ('slot', 'id', 'role', 'swap_index',
                                      'identity', 'bindings', 'donors', 'parts')})


def contract_hash(row):
    """All local metadata and pins, excluding no fields (including no evidence)."""
    return digest(row)


def _pins(pins):
    _require(type(pins) is dict and 0 < len(pins) <= 1024, 'missing file pins')
    folded = set()
    for name, pin in pins.items():
        _relative(name)
        _require(name.casefold() not in folded, 'case-colliding file pins')
        folded.add(name.casefold())
        _keys(pin, ('size', 'sha256'))
        _integer(pin['size'], 0, MAX_FILE)
        _hex(pin['sha256'])


def _row(row, expected, policy):
    base = set(expected)
    _require(type(row) is dict, 'invalid car row')
    for key in base - {'state'}:
        _require(type(row.get(key)) is type(expected[key]) and row[key] == expected[key],
                 'duplicate, reordered or reassigned slot')
    if row.get('state') == 'absent':
        _keys(row, base)
        return
    _keys(row, base | {'identity', 'origin', 'display', 'bindings', 'donors',
                       'parts', 'converter', 'inputs', 'outputs'})
    _require(row['state'] == 'ready' and row['role'] == 'addition', 'unsupported car state or swap import')
    _keys(row['origin'], ('game', 'platform', 'folder'))
    origin = row['origin']
    _require(origin['game'] == 'carbon' and origin['platform'] in ('pc', 'x360') and
             type(origin['folder']) is str and _TOKEN.fullmatch(origin['folder']), 'unsupported source')
    _hex(row['identity'])
    _require(row['identity'] == source_identity(**origin) == policy['identity'], 'unapproved or reassigned identity')
    _keys(row['display'], ('name', 'manufacturer'))
    for text in row['display'].values():
        _require(type(text) is str and 0 < len(text) <= 128 and
                 all(ord(c) >= 32 and not 127 <= ord(c) <= 159 and
                     not 0xD800 <= ord(c) <= 0xDFFF for c in text), 'invalid local display text')
    for key, fields in (('bindings', ('folder', 'pvehicle', 'frontend', 'ecar')),
                        ('donors', ('model', 'price'))):
        _keys(row[key], fields)
        _require(all(type(t) is str and _TOKEN.fullmatch(t) for t in row[key].values()), 'invalid local binding')
    parts = row['parts']
    _require(type(parts) is list and 0 < len(parts) <= 255, 'missing part semantics')
    types, keys, count = set(), set(), 0
    for part in parts:
        _keys(part, ('type', 'keys'))
        _integer(part['type'], 0, 254)
        _require(part['type'] not in types, 'duplicate part type')
        types.add(part['type'])
        _require(type(part['keys']) is list and part['keys'], 'empty part type')
        for key in part['keys']:
            _require(type(key) is str and _TOKEN.fullmatch(key) and key not in keys, 'invalid or duplicate part key')
            keys.add(key)
            count += 1
        _require(count <= 32768, 'flattened part index exceeds 32767')
    _keys(row['converter'], ('revision', 'policy'))
    _integer(row['converter']['revision'], 1, 2147483647)
    _hex(row['converter']['policy'])
    _keys(row['inputs'], INPUT_GROUPS)
    for group in INPUT_GROUPS:
        _pins(row['inputs'][group])
    _pins(row['outputs'])
    _require(REQUIRED_OUTPUTS <= row['outputs'].keys() and
             all(_OUTPUT.fullmatch(name) for name in row['outputs']), 'missing required output or invalid filename')
    _require(dict(contract=contract_hash(row), compatibility=slot_compatibility(row)) in policy['variants'],
             'conversion contract is not approved')


def make_catalog(rows, policy):
    """Validate an import plan against packaged policy, without touching files."""
    pol = _policy(policy)
    _require(type(rows) is list and len(rows) == SLOT_COUNT, 'all reservations required')
    identities, bindings = set(), set()
    for row, expected, approval in zip(rows, reservations(), pol['slots']):
        _row(row, expected, approval)
        if row['state'] == 'ready':
            _require(row['identity'] not in identities, 'duplicate identity')
            identities.add(row['identity'])
            # Each namespace must remain unique, including frontend RefSpec.
            for field, value in row['bindings'].items():
                _require((field, value) not in bindings, 'duplicate runtime binding')
                bindings.add((field, value))
    compatibility = digest([slot_compatibility(r) if r['state'] == 'ready' else None for r in rows])
    return dict(schema=1, policy_sha256=policy.sha256, compatibility=compatibility, cars=rows)


def _catalog(doc, policy):
    _keys(doc, ('schema', 'policy_sha256', 'compatibility', 'cars'))
    _require(type(doc['schema']) is int and doc['schema'] == 1, 'unsupported catalog schema')
    _require(doc == make_catalog(doc['cars'], policy), 'catalog policy or compatibility mismatch')
    return doc


def _selection(selection, catalog):
    _require(type(selection) is list and all(type(s) is int for s in selection) and
             selection == sorted(set(selection)), 'selection must be sorted unique slots')
    for slot in selection:
        _integer(slot, 0, SLOT_COUNT - 1)
        _require(catalog['cars'][slot]['state'] == 'ready', 'selection of absent slot')


def _inventory(fd):
    """Exact recursive regular-file inventory; rejects links and special files."""
    result = set()
    for name in os.listdir(fd):
        _relative(name)
        st = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if stat.S_ISDIR(st.st_mode):
            child = os.open(name, _DIR_FLAGS, dir_fd=fd)
            try:
                result.update(name + '/' + p for p in _inventory(child))
            finally:
                os.close(child)
        else:
            _require(stat.S_ISREG(st.st_mode) and st.st_nlink == 1, 'symlink or special output')
            result.add(name)
    return result


def _verify_generation(base, generation, catalog_sha256, policy):
    _hex(catalog_sha256)
    _require(generation == 'g-' + catalog_sha256, 'invalid generation name')
    fd = os.open(generation, _DIR_FLAGS, dir_fd=base)
    try:
        data = _read(fd, 'catalog.json')
        _require(hashlib.sha256(data).hexdigest() == catalog_sha256, 'catalog digest mismatch')
        doc = _catalog(_decode(data), policy)
        expected = {'catalog.json'}
        for row in doc['cars']:
            if row['state'] != 'ready':
                continue
            for name, pin in row['outputs'].items():
                path = f"cars/{row['slot']}/{name}"
                expected.add(path)
                with _file(fd, path) as output:
                    _require(_record(output) == pin, 'output digest mismatch')
        _require(_inventory(fd) == expected, 'unpinned generation output')
        return doc
    finally:
        os.close(fd)


def _pointer(base, policy):
    pointer = _decode(_read(base, 'current.json'))
    _keys(pointer, ('schema', 'generation', 'catalog_sha256', 'compatibility', 'selection'))
    _require(type(pointer['schema']) is int and pointer['schema'] == 1, 'unsupported pointer schema')
    if pointer['generation'] is None:
        _require(pointer == disabled_pointer(), 'invalid disabled pointer')
        return pointer, None
    doc = _verify_generation(base, pointer['generation'], pointer['catalog_sha256'], policy)
    _require(pointer['compatibility'] == doc['compatibility'], 'pointer compatibility mismatch')
    _selection(pointer['selection'], doc)
    return pointer, doc


@dataclass(frozen=True)
class Verification:
    generation: str = ''
    compatibility: str = ''
    selected: tuple = ()
    catalog: dict | None = None
    error: str = ''


def verify_current(root, *, policy=None):
    """Startup reader: any missing, corrupt or unapproved state activates zero."""
    try:
        with _directory(root) as base:
            pointer, doc = _pointer(base, policy if policy is not None else packaged_policy())
            _bound(root, base)
        if doc is None:
            return Verification()
        return Verification(pointer['generation'], doc['compatibility'],
                            tuple(doc['cars'][s] for s in pointer['selection']), doc)
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        return Verification(error=str(exc))


def _write_all(fd, data):
    view = memoryview(data)
    while view:
        size = os.write(fd, view)
        _require(size > 0, 'short write')
        view = view[size:]


def _write(base, name, data):
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=base)
    try:
        _write_all(fd, data)
        os.fsync(fd)
        os.fchmod(fd, 0o444)
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def _writer(root):
    with _directory(root, create=True) as base:
        fd = os.open('.writer.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                     0o600, dir_fd=base)
        try:
            st = os.fstat(fd)
            _require(stat.S_ISREG(st.st_mode) and st.st_nlink == 1, 'unsafe writer lock')
            fcntl.flock(fd, fcntl.LOCK_EX)
            _bound(root, base)
            yield base
        finally:
            os.close(fd)


def _hit(fault, point):
    if fault is not None:
        fault(point)


def _publish_pointer(root, base, pointer, fault, precommit=None):
    data = encoded(pointer)
    try:
        previous = _read(base, 'current.json')
    except FileNotFoundError:
        previous = None
    token = uuid.uuid4().hex
    pending, backup = '.pointer-' + token, '.previous-' + token
    if previous is not None:
        _write(base, backup, previous)
    _write(base, pending, data)
    os.fsync(base)
    _hit(fault, 'pointer_ready')
    _hit(fault, 'before_publish')
    if precommit is not None:
        precommit()
    _bound(root, base)
    os.replace(pending, 'current.json', src_dir_fd=base, dst_dir_fd=base)
    try:
        os.fsync(base)
    except OSError:
        # Restore exact bytes on a reported commit-flush failure. A power loss
        # may expose either complete pointer; neither points at partial output.
        if previous is None:
            os.unlink('current.json', dir_fd=base)
        else:
            os.replace(backup, 'current.json', src_dir_fd=base, dst_dir_fd=base)
        os.fsync(base)
        raise
    # Keep recovery files and generations. Cleanup is deliberately separate.
    return pointer


def _disjoint(root, paths):
    root = _absolute(root).casefold()
    for path in paths:
        path = _absolute(path).casefold()
        _require(root != path and not root.startswith(path.rstrip('/') + '/') and
                 not path.startswith(root.rstrip('/') + '/'), 'catalog overlaps source tree')


def publish(root, rows, output_dirs, input_dirs, *, policy=None, selection=None, fault=None):
    """Copy approved outputs into a durable generation, then atomically select.

    output_dirs maps ready slots to converter output directories. input_dirs maps
    those slots to the four INPUT_GROUPS' directory roots. File keys in each
    group's pins are relative to that root. All pins are rechecked under the
    writer lock. fault(point) is a test/cancellation hook, before publication.
    No source, completed generation or save is changed or deleted.
    """
    policy = policy if policy is not None else packaged_policy()
    # Freeze caller-owned containers before any callback or I/O.
    rows = _decode(encoded(rows))
    catalog = make_catalog(rows, policy)
    ready = {r['slot'] for r in rows if r['state'] == 'ready'}
    _require(set(output_dirs) == set(input_dirs) == ready, 'missing or extra source directories')
    for groups in input_dirs.values():
        _keys(groups, INPUT_GROUPS)
    selection = sorted(ready) if selection is None else list(selection)
    _selection(selection, catalog)
    paths = list(output_dirs.values()) + [p for groups in input_dirs.values() for p in groups.values()]
    _disjoint(root, paths)
    data = encoded(catalog)
    _require(len(data) <= MAX_JSON, 'catalog exceeds limit')
    sha = hashlib.sha256(data).hexdigest()
    generation = 'g-' + sha
    with _writer(root) as base, ExitStack() as stack:
        sources = {s: stack.enter_context(_directory(p)) for s, p in output_dirs.items()}
        inputs = {s: {g: stack.enter_context(_directory(p)) for g, p in groups.items()}
                  for s, groups in input_dirs.items()}

        def check_inputs():
            for row in rows:
                s = row['slot']
                if s not in ready:
                    continue
                for group, pins in row['inputs'].items():
                    _bound(input_dirs[s][group], inputs[s][group])
                    for name, pin in pins.items():
                        with _file(inputs[s][group], name) as fd:
                            _require(_record(fd) == pin, 'changed ' + group + ' input')
                _bound(output_dirs[s], sources[s])
                _require(_inventory(sources[s]) == set(row['outputs']), 'missing output pin or extra output')
                for name, pin in row['outputs'].items():
                    with _file(sources[s], name) as fd:
                        _require(_record(fd) == pin, 'changed converter output')

        check_inputs()
        _hit(fault, 'inputs_verified')
        try:
            os.stat(generation, dir_fd=base, follow_symlinks=False)
        except FileNotFoundError:
            stage = '.stage-' + uuid.uuid4().hex
            os.mkdir(stage, 0o700, dir_fd=base)
            os.fsync(base)
            staged = os.open(stage, _DIR_FLAGS, dir_fd=base)
            try:
                os.mkdir('cars', 0o700, dir_fd=staged)
                cars = os.open('cars', _DIR_FLAGS, dir_fd=staged)
                try:
                    for row in rows:
                        if row['state'] != 'ready':
                            continue
                        s = row['slot']
                        os.mkdir(str(s), 0o700, dir_fd=cars)
                        car = os.open(str(s), _DIR_FLAGS, dir_fd=cars)
                        try:
                            for name, pin in row['outputs'].items():
                                target = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                                 0o600, dir_fd=car)
                                try:
                                    with _file(sources[s], name) as source:
                                        _require(_record(source, target) == pin, 'output changed during copy')
                                    os.fchmod(target, 0o444)
                                    os.fsync(target)
                                finally:
                                    os.close(target)
                                _hit(fault, 'output_durable')
                            os.fchmod(car, 0o555)
                            os.fsync(car)
                        finally:
                            os.close(car)
                    os.fchmod(cars, 0o555)
                    os.fsync(cars)
                finally:
                    os.close(cars)
                _write(staged, 'catalog.json', data)
                os.fchmod(staged, 0o555)
                os.fsync(staged)
                _hit(fault, 'catalog_durable')
            finally:
                os.close(staged)
            os.rename(stage, generation, src_dir_fd=base, dst_dir_fd=base)
            os.fsync(base)
        _verify_generation(base, generation, sha, policy)
        _hit(fault, 'generation_verified')
        check_inputs()
        def precommit():
            check_inputs()
            _verify_generation(base, generation, sha, policy)

        return _publish_pointer(root, base, dict(schema=1, generation=generation,
                                                catalog_sha256=sha, compatibility=catalog['compatibility'],
                                                selection=selection), fault, precommit)


def set_selection(root, selection, *, policy=None, fault=None):
    """Publish selection only after verifying the entire immutable generation."""
    policy = policy if policy is not None else packaged_policy()
    with _writer(root) as base:
        pointer, catalog = _pointer(base, policy)
        _require(catalog is not None, 'no current generation')
        selection = list(selection)
        _selection(selection, catalog)
        return _publish_pointer(root, base, dict(pointer, selection=selection), fault,
                                lambda: _pointer(base, policy))


def restore_generation(root, generation, *, policy=None, selection=None, fault=None):
    """Select a retained, fully verified generation without original source I/O."""
    policy = policy if policy is not None else packaged_policy()
    _require(type(generation) is str and generation.startswith('g-'), 'invalid recovery generation')
    sha = generation[2:]
    with _writer(root) as base:
        catalog = _verify_generation(base, generation, sha, policy)
        selection = ([r['slot'] for r in catalog['cars'] if r['state'] == 'ready']
                     if selection is None else list(selection))
        _selection(selection, catalog)
        pointer = dict(schema=1, generation=generation, catalog_sha256=sha,
                       compatibility=catalog['compatibility'], selection=selection)
        return _publish_pointer(root, base, pointer, fault,
                                lambda: _verify_generation(base, generation, sha, policy))


def disabled_pointer():
    return dict(schema=1, generation=None, catalog_sha256=None, compatibility=None, selection=[])


def disable(root, *, fault=None):
    """Disable before any external cleanup, even when the old catalog is corrupt.

    Retain every generation and recovery file. No save discovery or writes.
    """
    with _writer(root) as base:
        return _publish_pointer(root, base, disabled_pointer(), fault)
