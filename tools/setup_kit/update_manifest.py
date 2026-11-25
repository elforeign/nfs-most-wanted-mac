"""Signed update primitives, mirrored by runtime/pub_update_manifest.h.

Call verify on the exact retained wire bytes with trusted repo/product/tag, then
verify_asset for BOTH files before using their contents. parse is schema-only.
ECDSA verification uses macOS Security/CoreFoundation, never an external package
or a downloaded key. All malformed/untrusted inputs raise ManifestError.
"""
import ctypes
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import re
import sys

MANIFEST_MAX = 4096
SIGNATURE_MAX = 80
VERSION_MAX = 43
UINT64_MAX = (1 << 64) - 1

# Publisher X9.63 P-256 points (A signs, B is the rotation backup). Kept literal
# for independent native/Python pin parity checks.
PIN_A = bytes.fromhex(
    "046f08c64142ae30d5ad25eb55be73f9cac1a1834d03c68993c48a7bfaa6f14e"
    "1b9effd4439e250cf945f8595e7bafe260cb29a35945ff60881fb777eab95962"
    "f3")
PIN_B = bytes.fromhex(
    "04678ddd10cd4fbd73b0f97f66e21efd161edf1524d71a7596dff2a37e3ce7e9"
    "922083c959a137aec9fd8dcc8b5b7ed25e791eebb322f8fcf3b5c009b2f29fdc"
    "db")
PINS = (PIN_A, PIN_B)

PRODUCTS = {
    "mw": ("elforeign/nfs-most-wanted-mac", "NFS-Most-Wanted-Native-Setup"),
    "carbon": ("elforeign/nfs-carbon-mac", "NFS-Carbon-Native-Setup"),
}


class ManifestError(ValueError):
    """Update proof is invalid or cannot be authenticated."""


@dataclass(frozen=True)
class Asset:
    name: str
    bytes: int
    sha256: str


@dataclass(frozen=True)
class Manifest:
    repo: str
    product: str
    version: str
    tag: str
    dmg: Asset
    checksum: Asset


def _decimal(text, maximum):
    if not re.fullmatch(r"0|[1-9][0-9]*", text) or len(text) > len(str(maximum)):
        raise ManifestError("Noncanonical or oversized integer")
    value = int(text)
    if value > maximum:
        raise ManifestError("Integer overflow")
    return value


def version_parts(version):
    """2–4 canonical uint32 decimal parts; return a zero-padded tuple of four."""
    if not isinstance(version, str) or len(version) > VERSION_MAX:
        raise ManifestError("Invalid version")
    parts = version.split(".")
    if not 2 <= len(parts) <= 4:
        raise ManifestError("Version needs 2–4 parts")
    values = tuple(_decimal(part, (1 << 32) - 1) for part in parts)
    return values + (0,) * (4 - len(values))


def compare_versions(a, b):
    """Return -1, 0 or 1; invalid versions raise instead of ranking as older."""
    x, y = version_parts(a), version_parts(b)
    return (x > y) - (x < y)


def _asset(text, expected):
    parts = text.split("|")
    if len(parts) != 3 or parts[0] != expected or not re.fullmatch(r"[0-9a-f]{64}", parts[2]):
        raise ManifestError("Unexpected asset name or SHA256")
    size = _decimal(parts[1], UINT64_MAX)
    if not size:
        raise ManifestError("Empty update asset")
    return Asset(parts[0], size, parts[2])


def parse(data, *, repo, product, tag):
    """Parse seven fixed ASCII/LF records bound to trusted caller expectations."""
    if not isinstance(data, bytes) or not 0 < len(data) <= MANIFEST_MAX or not data.endswith(b"\n"):
        raise ManifestError("Invalid manifest length or final LF")
    if any(c != 10 and not 32 <= c <= 126 for c in data):
        raise ManifestError("Manifest must be printable ASCII and LF")
    lines = data.decode("ascii").split("\n")
    if len(lines) != 8 or lines[-1] or lines[0] != "NFS-UPDATE-1":
        raise ManifestError("Invalid manifest records")
    fields = []
    for line, prefix in zip(lines[1:7], ("repo=", "product=", "version=", "tag=", "dmg=", "checksum=")):
        if not line.startswith(prefix):
            raise ManifestError("Missing or reordered manifest record")
        fields.append(line[len(prefix):])
    repository, game, version, signed_tag, dmg, checksum = fields
    if repository != repo or game != product or signed_tag != tag:
        raise ManifestError("Manifest identity does not match the requested update")
    fixed = PRODUCTS.get(game)
    if fixed is None or repository != fixed[0]:
        raise ManifestError("Unknown product or repository")
    version_parts(version)
    if signed_tag != "v" + version:
        raise ManifestError("Version and tag differ")
    name = f"{fixed[1]}-v{version}.dmg"
    return Manifest(repository, game, version, signed_tag,
                    _asset(dmg, name), _asset(checksum, name + ".sha256"))


def serialize(manifest):
    """Return canonical wire bytes, including their signed final LF."""
    if not isinstance(manifest, Manifest):
        raise ManifestError("Expected a manifest")
    for asset in (manifest.dmg, manifest.checksum):
        if not isinstance(asset, Asset) or type(asset.bytes) is not int:
            raise ManifestError("Invalid asset")
    try:
        data = (f"NFS-UPDATE-1\nrepo={manifest.repo}\nproduct={manifest.product}\n"
                f"version={manifest.version}\ntag={manifest.tag}\n"
                f"dmg={manifest.dmg.name}|{manifest.dmg.bytes}|{manifest.dmg.sha256}\n"
                f"checksum={manifest.checksum.name}|{manifest.checksum.bytes}|{manifest.checksum.sha256}\n").encode("ascii")
    except UnicodeError as error:
        raise ManifestError("Manifest must be ASCII") from error
    parse(data, repo=manifest.repo, product=manifest.product, tag=manifest.tag)
    return data


@lru_cache(maxsize=1)
def _frameworks():
    if sys.platform != "darwin":
        raise ManifestError("Signature verification requires macOS Security")
    try:
        cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        sec = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
        ptr = ctypes.c_void_p
        cf.CFDataCreate.argtypes = [ptr, ctypes.c_char_p, ctypes.c_long]
        cf.CFDataCreate.restype = ptr
        cf.CFDictionaryCreate.argtypes = [ptr, ctypes.POINTER(ptr), ctypes.POINTER(ptr), ctypes.c_long, ptr, ptr]
        cf.CFDictionaryCreate.restype = ptr
        cf.CFRelease.argtypes = [ptr]
        cf.CFRelease.restype = None
        sec.SecKeyCreateWithData.argtypes = [ptr, ptr, ctypes.POINTER(ptr)]
        sec.SecKeyCreateWithData.restype = ptr
        sec.SecKeyVerifySignature.argtypes = [ptr, ptr, ptr, ptr, ctypes.POINTER(ptr)]
        sec.SecKeyVerifySignature.restype = ctypes.c_bool
        return cf, sec
    except (OSError, AttributeError) as error:
        raise ManifestError("Cannot load signature verifier") from error


def verify_signature(data, signature, *, pins=PINS):
    """Authenticate exact bytes against either pin; fixture keys may be supplied.

    Unset/malformed slots cannot verify anything. CF Create/Copy ownership and
    CFError out parameters are released on both success and failure.
    """
    if not isinstance(data, bytes) or not 0 < len(data) <= MANIFEST_MAX:
        raise ManifestError("Invalid signed message length")
    if not isinstance(signature, bytes) or not 0 < len(signature) <= SIGNATURE_MAX:
        raise ManifestError("Invalid signature length")
    points = [pin for pin in pins if isinstance(pin, bytes) and len(pin) == 65 and pin[0] == 4]
    if not points:
        raise ManifestError("No configured publisher pin")
    cf, sec = _frameworks()
    ptr = ctypes.c_void_p

    def constant(library, name):
        return ptr.in_dll(library, name).value

    owned = []

    def keep(value):
        if not value:
            raise ManifestError("Cannot allocate signature verifier data")
        owned.append(value)
        return value

    def release_error(error):
        if error.value:
            cf.CFRelease(error)
            error.value = None

    try:
        message = keep(cf.CFDataCreate(None, data, len(data)))
        sig = keep(cf.CFDataCreate(None, signature, len(signature)))
        keys = (ptr * 2)(constant(sec, "kSecAttrKeyType"), constant(sec, "kSecAttrKeyClass"))
        values = (ptr * 2)(constant(sec, "kSecAttrKeyTypeECSECPrimeRandom"), constant(sec, "kSecAttrKeyClassPublic"))
        attrs = keep(cf.CFDictionaryCreate(None, keys, values, 2,
            ctypes.addressof(ctypes.c_byte.in_dll(cf, "kCFTypeDictionaryKeyCallBacks")),
            ctypes.addressof(ctypes.c_byte.in_dll(cf, "kCFTypeDictionaryValueCallBacks"))))
        algorithm = constant(sec, "kSecKeyAlgorithmECDSASignatureMessageX962SHA256")
        for pin in points:
            point = keep(cf.CFDataCreate(None, pin, len(pin)))
            error = ptr()
            key = sec.SecKeyCreateWithData(point, attrs, ctypes.byref(error))
            release_error(error)
            if not key:
                continue
            owned.append(key)
            valid = sec.SecKeyVerifySignature(key, algorithm, message, sig, ctypes.byref(error))
            release_error(error)
            if valid:
                return True
    except (OSError, AttributeError) as error:
        raise ManifestError("Signature verification unavailable") from error
    finally:
        for value in reversed(owned):
            cf.CFRelease(value)
    raise ManifestError("Publisher signature does not match")


def verify(data, signature, *, repo, product, tag, pins=PINS):
    """Authenticate a manifest without reserializing its signed message."""
    manifest = parse(data, repo=repo, product=product, tag=tag)
    verify_signature(data, signature, pins=pins)
    return manifest


def asset_matches(asset, size, sha256):
    """Check independently computed asset metadata against the signed values."""
    return (isinstance(asset, Asset) and type(size) is int and asset.bytes == size
            and isinstance(sha256, str) and re.fullmatch(r"[0-9a-f]{64}", sha256) is not None
            and asset.sha256 == sha256)


def verify_asset(path, asset):
    """Stream a retained asset and refuse changed size or SHA256."""
    digest, size = hashlib.sha256(), 0
    try:
        with open(path, "rb") as source:
            for block in iter(lambda: source.read(65536), b""):
                size += len(block)
                if size > asset.bytes:
                    raise ManifestError("Asset exceeds signed size")
                digest.update(block)
    except OSError as error:
        raise ManifestError("Cannot read update asset") from error
    if not asset_matches(asset, size, digest.hexdigest()):
        raise ManifestError("Asset does not match signed size/SHA256")
    return True
