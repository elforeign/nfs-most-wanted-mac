"""Content-addressed preparation copies; committed entries are never edited.

The caller holds Setup's build lock for cleanup/publication/pruning. A check only reads:
an entry removed by a concurrent build becomes a miss. Installs receive ordinary
copies, never hard links or roots pointing into this disposable cache.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from .install import InstallError, digest_tree, real_dir, remove
from . import x360_extras

CONVERTER_VERSION = 2  # retain the bounded shader window; full-archive entries are incompatible
MAX_BYTES = 12 * 1024**3
MAX_ENTRIES = 2
ENTRY = re.compile(r"[0-9a-f]{64}\.[0-9a-f]{64}")
ROOT_KEYS = {"x360_root", "assets_root", "movies"}


def _identity(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def preparation_key(content, pc, inventory, toolchain, extras=None):
    # Snapshot converters import each other, including audit helpers. Include the
    # entire shipped snapshot set so a transitive helper cannot silently change.
    converters = {p: h for p, h in inventory.items() if p.startswith("tools_snapshot/") or
                  p in {"tools/setup_kit/x360.py", "tools/setup_kit/xdvdfs.py", "tools/setup_kit/huff.py",
                        "tools/setup_kit/x360_cache.py", "tools/setup_kit/x360_extras.py", "runtime/huff.h"}}
    return _identity(dict(version=CONVERTER_VERSION, x360=content, pc=pc, converters=converters,
                          toolchain=toolchain, **x360_extras.identity(extras)))


def _roots(data, relative):
    if not isinstance(relative, dict) or not relative or set(relative) - ROOT_KEYS:
        raise ValueError("invalid preparation roots")
    result = {}
    for key, rel in relative.items():
        p = Path(rel)
        if p.is_absolute() or ".." in p.parts or not real_dir(data / p):
            raise ValueError("invalid preparation root path")
        result[key] = data / p
    return result


def _output_digest(data, metadata):
    return _identity(dict(tree=digest_tree(data), metadata=metadata))


class PreparedCache:
    def __init__(self, directory, key):
        self.directory, self.key = Path(directory), key

    def _matches(self, data, metadata, digest):
        return _output_digest(data, metadata) == digest

    def restore(self, target, record):
        """An unavailable cache is a miss, including failures during enumeration."""
        try:
            return self._restore(target, record)
        except (OSError, ValueError, KeyError, TypeError, InstallError):
            self._discard(target)
            return None

    def _restore(self, target, record):
        """Hash before copying and verify the copy too. Never mutate the cache."""
        if not real_dir(self.directory):
            return None
        for entry in sorted(self.directory.glob(self.key + ".*")):
            if not ENTRY.fullmatch(entry.name) or not real_dir(entry):
                continue
            try:
                manifest = entry / "manifest.json"
                if (manifest.is_symlink() or not manifest.is_file() or manifest.stat().st_size > 65536 or
                        not real_dir(entry / "data")):
                    continue
                metadata = json.loads(manifest.read_text())
                if (set(metadata) != {"roots", "features", "missing"} or
                        type(metadata["features"]) is not bool or
                        not isinstance(metadata["missing"], list) or
                        not all(isinstance(s, str) for s in metadata["missing"])):
                    continue
                _roots(entry / "data", metadata["roots"])
                digest = entry.name.split(".")[1]
                if not self._matches(entry / "data", metadata, digest):
                    continue
                # No link following, including a link introduced during copying.
                shutil.copytree(entry / "data", target, symlinks=True)
                roots = _roots(target, metadata["roots"])
                if not self._matches(target, metadata, digest):
                    raise ValueError("preparation changed while copying")
                record.update(features=metadata["features"], missing=metadata["missing"])
                return roots
            except (OSError, ValueError, KeyError, TypeError, InstallError):
                remove(target)
        return None

    @staticmethod
    def _discard(path):
        try:
            remove(path)
        except OSError:
            pass

    @staticmethod
    def clean_pending(directory):
        """Remove abandoned copies only while holding the shared build lock."""
        try:
            directory = Path(directory)
            if real_dir(directory):
                for pending in directory.glob(".pending-*"):
                    PreparedCache._discard(pending)
        except OSError:
            pass

    def publish(self, source, roots, record):
        """Publish a complete copy atomically, within the retained byte/count cap."""
        pending = None
        try:
            self.directory.mkdir(exist_ok=True)
            if not real_dir(self.directory):
                return False
            metadata = dict(roots={k: str(Path(v).relative_to(source)) for k, v in roots.items()},
                            features=bool(record["features"]), missing=list(record["missing"]))
            _roots(source, metadata["roots"])
            digest = _output_digest(source, metadata)
            manifest = json.dumps(metadata, sort_keys=True)
            # Reserve the complete payload plus manifest before making a second
            # copy, so eviction can supply the space needed for publication.
            size = self._size(source) + len(manifest.encode("utf-8"))
            self._prune(size)
            if size > MAX_BYTES:
                return False
            pending = Path(tempfile.mkdtemp(prefix=".pending-", dir=self.directory))
            shutil.copytree(source, pending / "data", symlinks=True)
            if _output_digest(pending / "data", metadata) != digest:
                raise InstallError("Prepared X360 files changed while caching.")
            (pending / "manifest.json").write_text(manifest, encoding="utf-8")
            entry = self.directory / (self.key + "." + digest)
            # A corrupt entry with this address must be replaced as a whole.
            remove(entry)
            os.rename(pending, entry)
            return True
        except (OSError, ValueError, KeyError, TypeError, InstallError):
            # Preparation is already staged for installation. Retention is
            # optional, so a full or unavailable cache must never abort it.
            return False
        finally:
            if pending is not None:
                self._discard(pending)

    @staticmethod
    def _size(entry):
        return sum(p.lstat().st_size for p in entry.rglob("*") if p.is_file() or p.is_symlink())

    def _prune(self, reserve):
        entries = sorted((p for p in self.directory.iterdir() if ENTRY.fullmatch(p.name)),
                         key=lambda p: p.lstat().st_mtime_ns, reverse=True)
        used, count = reserve, 1
        for p in entries:
            size = self._size(p) if real_dir(p) else 0
            if not real_dir(p) or count >= MAX_ENTRIES or used + size > MAX_BYTES:
                remove(p)
            else:
                used, count = used + size, count + 1
