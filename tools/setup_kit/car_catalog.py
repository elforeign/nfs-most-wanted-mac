"""Public added-car contracts and atomic, verified local generations.

Only an independently pinned packaged policy authorizes activation. There are
no built-in approvals. See runtime/docs/added-car-catalog.md for the wire format
and the trust boundary required of callers and runtime readers.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import errno
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
IMPORT_SOURCES = frozenset((('carbon', 'pc'), ('carbon', 'x360'), ('prostreet', 'pc'), ('undercover', 'pc')))
MAX_JSON = 4 << 20
MAX_FILE = 2 << 30
# Filled by the release's approval step, together with the runtime trust root.
# A digest supplied in a local catalog or beside a policy is never a trust root.
PACKAGED_POLICY_SHA256 = 'f679645d63b3c426eb1c72286589a781e9dbceaec52e154401cf9c7ffed813c8'
LEGACY_POLICY_SHA256 = 'cf1965a6ad93a2da2ea28001f29e298912c1d61c3883d6842231f98162918a9c'
LEGACY_POLICY_REVISION = 2
# Last policy before Carbon PC audio. Its original contracts remain reviewable.
PRE_AUDIO_POLICY_SHA256 = '54d1cae330a4e6313541fdc24805849b2b2ad0f1710bbc89817a44f2eaff1f5e'
PRE_AUDIO_POLICY_REVISION = 4
PC_AUDIO_POLICY_SHA256 = 'fb94c2862e1ee321c1220b97d22b275da3ea73fbe697d082d745c48395402aa1'
PC_AUDIO_POLICY_REVISION = 5
X360_AUDIO_POLICY_SHA256 = '243e055faf5957ba00d4ce5329756e6861355571c44335e1fe0543d32b7c4626'
X360_AUDIO_POLICY_REVISION = 6
AUDIO_V7_POLICY_SHA256 = '5abf0a914b1029ad9cac84a0e90e0f6c581b79e98b74b84c28eb8c96be4408ed'
CPF1_POLICY_SHA256 = '6f8a273c185c2554a30520a8c9e4e80ac01bad81d23d98e2e8523cb843cc9e22'
CPF2_POLICY_SHA256 = '43a33ad36f53f90c2f0ebfabf7f77b183f0bacc61e8f6842d181a6b19bce0471'
CPF3_POLICY_SHA256 = '312f3f1918451e49b641cef9ad596f77a2b7c0e242b5050b2a30b91343ea01a3'
PRE_RETAIL_ATTRIBUTES_POLICY_SHA256 = 'db670b741c125f0cb2c04551f2f272685af6733bde946c8d05a99fbd86006f0d'
RETAIL_ATTRIBUTES_POLICY_SHA256 = 'ebb6868f4e9d9ae0cae0e414e3269834860671c5d093621cc22248b081c34bd4'
PS_PERFORMANCE_POLICY_SHA256 = 'c04434fa3790528f8e8723b0448a3bc97edc4ac92cdb57e8cca3092125ad23c2'
PRE_UC_PERFORMANCE_POLICY_SHA256 = 'e5800b882bf2fc7a01d23974ec1f1e7316b0b3977137ceccf5b1d82b8d45eaf7'
PRE_SIBLING_PERFORMANCE_POLICY_SHA256 = '93a8b9666cdffe4108ae9234fa08e718e47538609110feca05dbfd317b76295b'
PRE_CACHE_PUBLISH_POLICY_SHA256 = '582212d17b6c590bd8ddcdff84eb4b296e78ee1846a5457bb72942e28d6aa830'
PRE_PROSTREET_AUDIO_POLICY_SHA256 = '12f70e44254f539a2adb100a6c876d3ad7e562c52b5ae619f316115a343d45cd'
PRE_TORQUE_SPLINE_POLICY_SHA256 = '3481940c810528f6ffe74e4ca34ca956f16b205461199b811d4e1b02d19247c7'
PRE_CACHE_RENAME_POLICY_SHA256 = '1933a5cb7999c9248030687d4bafc440bdf92d5e9f9010d9dfb35f477f52f3db'
PRE_UC_TORQUE_POLICY_SHA256 = 'd2a7671118660bf0c48c67bf91499a698619134480587f7b18800c68a8072aa7'
PRE_STOCK_PLAYER_AUDIO_POLICY_SHA256 = '5c52fc73694c442707aef4b023a2170d5402f609fcd0276afc1e9bfb6496ec6e'
POLICY_PATH = Path(__file__).with_name('car_approvals.json')
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


def _entry_stat(base, name):
    try:
        return os.stat(name, dir_fd=base, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _identity(st):
    return st.st_dev, st.st_ino, stat.S_IFMT(st.st_mode)


def _new_stage_dir(base, name):
    os.mkdir(name, 0o700, dir_fd=base)
    current = _entry_stat(base, name)
    _require(current is not None and stat.S_ISDIR(current.st_mode), 'stage directory changed: ' + repr(name))
    return _identity(current)


def _remove_stage(base, name, fd, entries, path, unexpected):
    """Use the creation records, never a discovered entry, as authority to delete."""
    def bound():
        current = _entry_stat(base, name)
        if current is None:
            return False
        if _identity(current) == _identity(os.fstat(fd)):
            return True
        unexpected.append(path)
        return False

    if not bound():
        return
    os.fchmod(fd, 0o700)
    before = len(unexpected)
    for child in os.listdir(fd):
        child_path = path + '/' + child
        current = _entry_stat(fd, child)
        if current is None:
            continue
        owned = entries.get(child)
        if owned is None or _identity(current) != owned[0]:
            unexpected.append(child_path)
        elif owned[1] is None:
            try:
                os.unlink(child, dir_fd=fd)
            except FileNotFoundError:
                pass
            except IsADirectoryError:
                unexpected.append(child_path)
        else:
            try:
                sub = os.open(child, _DIR_FLAGS, dir_fd=fd)
            except FileNotFoundError:
                continue
            except OSError as exc:
                if exc.errno not in (errno.ELOOP, errno.ENOTDIR):
                    raise
                unexpected.append(child_path)
                continue
            try:
                if _identity(os.fstat(sub)) == owned[0]:
                    _remove_stage(fd, child, sub, owned[1], child_path, unexpected)
                else:
                    unexpected.append(child_path)
            finally:
                os.close(sub)
    if len(unexpected) == before and bound():
        try:
            os.rmdir(name, dir_fd=base)
        except FileNotFoundError:
            pass
        except OSError as exc:
            if exc.errno not in (errno.EEXIST, errno.ENOTEMPTY, errno.ENOTDIR):
                raise
            unexpected.append(path)


class _Stage:
    def __init__(self, base, name, fd):
        self.base, self.name, self.fd = base, name, fd
        self.entries = {}
        self.published = False

    @contextmanager
    def directory(self, path):
        fd, entries = os.dup(self.fd), self.entries
        try:
            for name in _relative(path) if path else ():
                if name not in entries:
                    entries[name] = (_new_stage_dir(fd, name), {})
                identity, children = entries[name]
                child = os.open(name, _DIR_FLAGS, dir_fd=fd)
                os.close(fd)
                fd = child
                _require(children is not None and _identity(os.fstat(fd)) == identity,
                         'stage directory changed: ' + repr(path))
                entries = children
            yield fd, entries
        finally:
            os.close(fd)

    def open_file(self, path):
        parent, _, name = path.rpartition('/')
        _relative(path)
        with self.directory(parent) as (fd, entries):
            leaf = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
            try:
                entries[name] = (_identity(os.fstat(leaf)), None)
            except BaseException:
                os.close(leaf)
                raise
            return leaf

    def write(self, path, data):
        fd = self.open_file(path)
        try:
            _write_durable(fd, data)
        finally:
            os.close(fd)

    def seal(self):
        self._seal(self.fd, self.entries)

    def _seal(self, fd, entries):
        _require(set(os.listdir(fd)) == entries.keys(), 'unexpected entry in the transaction stage')
        for name, (identity, children) in entries.items():
            current = _entry_stat(fd, name)
            _require(current is not None and _identity(current) == identity,
                     'stage entry changed: ' + repr(name))
            flags = _DIR_FLAGS if children is not None else os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            leaf = os.open(name, flags, dir_fd=fd)
            try:
                opened = os.fstat(leaf)
                _require(_identity(opened) == identity, 'stage entry changed: ' + repr(name))
                if children is None:
                    _require(opened.st_nlink == 1, 'hardlinked stage file: ' + repr(name))
                    os.fchmod(leaf, 0o444)
                    os.fsync(leaf)
                else:
                    self._seal(leaf, children)
            finally:
                os.close(leaf)
        os.fchmod(fd, 0o555)
        os.fsync(fd)

    def publish(self, name):
        current = _entry_stat(self.base, self.name)
        _require(current is not None and _identity(current) == _identity(os.fstat(self.fd)),
                 'stage directory changed: ' + repr(self.name))
        with _renamable(self.fd):
            os.rename(self.name, name, src_dir_fd=self.base, dst_dir_fd=self.base)
        self.published = True


@contextmanager
def _renamable(fd):
    """macOS before 26.4 refuses to rename any directory its owner cannot
    write (EACCES), so a sealed directory is writable only for the rename."""
    mode = stat.S_IMODE(os.fstat(fd).st_mode)
    if mode & stat.S_IWUSR:
        yield
        return
    os.fchmod(fd, mode | stat.S_IWUSR)
    try:
        yield
    finally:
        os.fchmod(fd, mode)
        os.fsync(fd)


def _set_aside(base, name, prefix):
    """Rename a retained entry aside, keeping its bytes and modes."""
    try:
        fd = os.open(name, _DIR_FLAGS, dir_fd=base)
    except OSError:
        fd = None  # not a directory: renaming it needs no write access to it
    try:
        if fd is None:
            os.rename(name, prefix + uuid.uuid4().hex, src_dir_fd=base, dst_dir_fd=base)
        else:
            with _renamable(fd):
                os.rename(name, prefix + uuid.uuid4().hex, src_dir_fd=base, dst_dir_fd=base)
    finally:
        if fd is not None:
            os.close(fd)
    os.fsync(base)


@contextmanager
def _stage(base, name):
    identity = _new_stage_dir(base, name)
    fd = os.open(name, _DIR_FLAGS, dir_fd=base)
    try:
        _require(_identity(os.fstat(fd)) == identity, 'stage directory changed: ' + repr(name))
        stage = _Stage(base, name, fd)
        try:
            yield stage
        finally:
            if not stage.published:
                unexpected = []
                _remove_stage(base, name, fd, stage.entries, name, unexpected)
                _require(not unexpected, 'left unexpected stage entries untouched: ' +
                         ', '.join(repr(path) for path in sorted(unexpected)))
    finally:
        os.close(fd)


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


def _record(fd, sink=None, *, limit=MAX_FILE):
    before = os.fstat(fd)
    h, size = hashlib.sha256(), 0
    while True:
        data = os.read(fd, 1 << 20)
        if not data:
            break
        size += len(data)
        _require(size <= limit, 'file grew beyond limit')
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
            if type(variant) is dict and 'legacy' in variant:
                _keys(variant, ('contract', 'compatibility', 'legacy'))
                _integer(variant['legacy'], 1, 16)
            else:
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
    _require(type(origin['game']) is str and type(origin['platform']) is str and
             (origin['game'], origin['platform']) in IMPORT_SOURCES and
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
    _require('AUDIO.BIN' not in row['outputs'] or origin['game'] == 'carbon' or
             (origin['game'], origin['platform']) == ('prostreet', 'pc'),
             'audio sidecar requires an audited source')
    contract, compatibility = contract_hash(row), slot_compatibility(row)
    _require(any(v['contract'] == contract and v['compatibility'] == compatibility for v in policy['variants']),
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
    _require(doc['policy_sha256'] in (policy.sha256, LEGACY_POLICY_SHA256, PRE_AUDIO_POLICY_SHA256,
                                     PC_AUDIO_POLICY_SHA256, X360_AUDIO_POLICY_SHA256, AUDIO_V7_POLICY_SHA256,
                                     CPF1_POLICY_SHA256, CPF2_POLICY_SHA256, CPF3_POLICY_SHA256,
                                     PRE_RETAIL_ATTRIBUTES_POLICY_SHA256, RETAIL_ATTRIBUTES_POLICY_SHA256,
                                     PS_PERFORMANCE_POLICY_SHA256, PRE_UC_PERFORMANCE_POLICY_SHA256,
                                     PRE_SIBLING_PERFORMANCE_POLICY_SHA256, PRE_CACHE_PUBLISH_POLICY_SHA256,
                                     PRE_PROSTREET_AUDIO_POLICY_SHA256, PRE_TORQUE_SPLINE_POLICY_SHA256,
                                     PRE_CACHE_RENAME_POLICY_SHA256, PRE_STOCK_PLAYER_AUDIO_POLICY_SHA256,
                                     PRE_UC_TORQUE_POLICY_SHA256),
             'catalog policy or compatibility mismatch')
    _require(dict(doc, policy_sha256=policy.sha256) == make_catalog(doc['cars'], policy),
             'catalog policy or compatibility mismatch')
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
    policy = policy if policy is not None else packaged_policy()
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
            pointer, doc = _pointer(base, policy)
            _bound(root, base)
        if doc is None:
            return Verification()
        return Verification(pointer['generation'], doc['compatibility'],
                            tuple(doc['cars'][s] for s in pointer['selection']), doc)
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        if isinstance(exc, FileNotFoundError) and exc.filename == 'current.json':
            return Verification()
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
        _write_durable(fd, data)
    finally:
        os.close(fd)


def _write_durable(fd, data):
    _write_all(fd, data)
    os.fsync(fd)
    os.fchmod(fd, 0o444)
    os.fsync(fd)


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
    try:
        _write(base, pending, data)
        os.fsync(base)
        _hit(fault, 'pointer_ready')
        _hit(fault, 'before_publish')
        if precommit is not None:
            precommit()
        _bound(root, base)
        os.replace(pending, 'current.json', src_dir_fd=base, dst_dir_fd=base)
    except BaseException:
        try:
            os.unlink(pending, dir_fd=base)
        except FileNotFoundError:
            pass
        raise
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
    with _writer(root) as base:
        directory_ids = {}

        @contextmanager
        def source_directory(path):
            with _directory(path) as fd:
                st = os.fstat(fd)
                identity = (st.st_dev, st.st_ino)
                _require(identity == directory_ids.setdefault(path, identity), 'directory changed')
                yield fd
                _bound(path, fd)

        def check_inputs():
            for row in rows:
                s = row['slot']
                if s not in ready:
                    continue
                for group, pins in row['inputs'].items():
                    with source_directory(input_dirs[s][group]) as source:
                        for name, pin in pins.items():
                            with _file(source, name) as fd:
                                _require(_record(fd) == pin, 'changed ' + group + ' input')
                with source_directory(output_dirs[s]) as source:
                    _require(_inventory(source) == set(row['outputs']), 'missing output pin or extra output')
                    for name, pin in row['outputs'].items():
                        with _file(source, name) as fd:
                            _require(_record(fd) == pin, 'changed converter output')

        check_inputs()
        _hit(fault, 'inputs_verified')
        try:
            os.stat(generation, dir_fd=base, follow_symlinks=False)
        except FileNotFoundError:
            stage = '.stage-' + uuid.uuid4().hex
            with _stage(base, stage) as staged:
                with staged.directory('cars') as (cars, _):
                    for row in rows:
                        if row['state'] != 'ready':
                            continue
                        s = row['slot']
                        path = f'cars/{s}'
                        with staged.directory(path) as (car, _):
                            with source_directory(output_dirs[s]) as output:
                                for name, pin in row['outputs'].items():
                                    target = staged.open_file(f'{path}/{name}')
                                    try:
                                        with _file(output, name) as source:
                                            _require(_record(source, target) == pin, 'output changed during copy')
                                        os.fchmod(target, 0o444)
                                        os.fsync(target)
                                    finally:
                                        os.close(target)
                                    _hit(fault, 'output_durable')
                            os.fchmod(car, 0o555)
                            os.fsync(car)
                    os.fchmod(cars, 0o555)
                    os.fsync(cars)
                staged.write('catalog.json', data)
                os.fchmod(staged.fd, 0o555)
                os.fsync(staged.fd)
                _hit(fault, 'catalog_durable')
                staged.publish(generation)
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
