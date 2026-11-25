"""Signed manifest primitives: native C and standard-library Python parity.

Failing-first controls: signature bypass accepts rewritten proof 2/2; digest
bypass accepts changed DMG + repaired checksum 2/2 (one per implementation).
Intact admission refuses those controls 4/4; signed originals admitted 4/4
(both products, both implementations). Throwaway P-256 keys stay in scratch.
The test is registered by offline_tests.sh's runtime/*_test.py discovery.
"""
import ctypes
from dataclasses import replace
import hashlib
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import types

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "tools/setup_kit/update_manifest.py"
HEADER = ROOT / "runtime/pub_update_manifest.h"
KEYS = ROOT / "runtime/pub_update_keys.h"

HARNESS = r'''
#include "pub_update_manifest.h"
int schema(const void *data, size_t length, const char *repo, const char *product,
           const char *tag, char *out, size_t cap, size_t *written) {
    PUMManifest m;
    return pum_parse(data, length, repo, product, tag, &m) && pum_serialize(&m, out, cap, written);
}
int order(const char *a, const char *b, int *result) { return pum_compare_versions(a, b, result); }
void pin(unsigned slot, unsigned char *out) { memcpy(out, PUM_PINS[slot], 65); }
int admission(const void *data, size_t length, const void *sig, size_t sig_length,
              const char *repo, const char *product, const char *tag,
              const unsigned char *a, const unsigned char *b, const char *dmg, const char *checksum) {
    const uint8_t *pins[2] = {a, b};
    PUMManifest m;
    int valid = a ? pum_verify_with_pins(data, length, sig, sig_length, repo, product, tag, &m, pins, 2)
                  : pum_verify(data, length, sig, sig_length, repo, product, tag, &m);
    if (!valid) {
        /* A failed proof must not leave parsed authority in the result. */
        PUMManifest zero = {0};
        return memcmp(&m, &zero, sizeof m) ? -1 : 0;
    }
    return (!dmg || pum_verify_file(&m.dmg, dmg)) && (!checksum || pum_verify_file(&m.checksum, checksum));
}
int invalid_struct(const void *data, size_t length, const char *repo, const char *product,
                   const char *tag, unsigned fault) {
    PUMManifest m;
    char out[PUM_MANIFEST_MAX + 1]; size_t written = 99;
    if (!pum_parse(data, length, repo, product, tag, &m)) return -1;
    switch (fault) {
        case 0: m.dmg.bytes = 0; break;
        case 1: memset(m.version, '1', sizeof m.version); break;
        case 2: m.dmg.sha256[0] = 'g'; break;
        case 3: m.tag[0] = 'V'; break;
        case 4: strcpy(m.checksum.name, "wrong.sha256"); break;
        case 5: return !pum_serialize(&m, out, 1, &written) && !written;
        case 6: m.product[0] = 'x'; break;
    }
    return !pum_serialize(&m, out, sizeof out, &written) && !written && !out[0];
}
'''


def command(args):
    result = subprocess.run(list(map(str, args)), capture_output=True, timeout=60)
    assert result.returncode == 0, (args[0], result.returncode, result.stderr.decode(errors="replace")[-3000:])
    return result.stdout


def load_module(name, text=None):
    spec = importlib.util.spec_from_file_location(name, MODULE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    if text is None:
        spec.loader.exec_module(module)
    else:
        exec(compile(text, str(MODULE), "exec"), module.__dict__)
    return module


def mutate(text, old, new):
    assert text.count(old) == 1, "Mutation must hit exactly one production check"
    return text.replace(old, new)


def build(root, label, header):
    folder = root / label
    folder.mkdir()
    (folder / HEADER.name).write_text(header)
    (folder / KEYS.name).write_bytes(KEYS.read_bytes())
    source = folder / "harness.c"
    source.write_text(HARNESS)
    library = folder / "manifest.dylib"
    command(["clang", "-std=c11", "-Wall", "-Wextra", "-Werror", "-Wno-deprecated-declarations",
             "-dynamiclib", source, "-framework", "Security", "-framework", "CoreFoundation", "-o", library])
    native = ctypes.CDLL(str(library))
    string, size = ctypes.c_char_p, ctypes.c_size_t
    native.schema.argtypes = [string, size, string, string, string, ctypes.c_void_p, size, ctypes.POINTER(size)]
    native.schema.restype = ctypes.c_int
    native.order.argtypes = [string, string, ctypes.POINTER(ctypes.c_int)]
    native.order.restype = ctypes.c_int
    native.pin.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    native.pin.restype = None
    native.admission.argtypes = [string, size, string, size, string, string, string, string, string, string, string]
    native.admission.restype = ctypes.c_int
    native.invalid_struct.argtypes = [string, size, string, string, string, ctypes.c_uint]
    native.invalid_struct.restype = ctypes.c_int
    return native


def fixture(product="mw", version="4.11.0", dmg=b"original image", checksum=None):
    # This independent wire fixture does not use either production serializer.
    if product == "mw":
        repo, prefix = "elforeign/nfs-most-wanted-mac", "NFS-Most-Wanted-Native-Setup"
    else:
        repo, prefix = "elforeign/nfs-carbon-mac", "NFS-Carbon-Native-Setup"
    name = f"{prefix}-v{version}.dmg"
    digest = hashlib.sha256(dmg).hexdigest()
    if checksum is None:
        checksum = f"{digest}  {name}\n".encode()
    wire = (f"NFS-UPDATE-1\nrepo={repo}\nproduct={product}\nversion={version}\ntag=v{version}\n"
            f"dmg={name}|{len(dmg)}|{digest}\n"
            f"checksum={name}.sha256|{len(checksum)}|{hashlib.sha256(checksum).hexdigest()}\n").encode()
    return wire, dict(repo=repo, product=product, tag="v" + version), dmg, checksum


def native_schema(native, wire, identity, cap=4097):
    out, written = ctypes.create_string_buffer(cap), ctypes.c_size_t()
    valid = native.schema(wire, len(wire), *(identity[k].encode() for k in ("repo", "product", "tag")),
                          out, cap, ctypes.byref(written))
    return out.raw[:written.value] if valid else None


def python_schema(module, wire, identity):
    try:
        return module.serialize(module.parse(wire, **identity))
    except module.ManifestError:
        return None


def schema_checks(native, module):
    valid = []
    for product in ("mw", "carbon"):
        for version in ("0.0", "4.11.0", "1.2.0.4", ".".join(["4294967295"] * 4)):
            wire, identity, _, _ = fixture(product, version)
            valid.append((wire, identity))
    wire, identity = valid[1]
    # Positive uint64 size boundaries are schema-valid, even if no such file exists.
    valid.append((wire.replace(b"|14|", f"|{(1 << 64) - 1}|".encode()), identity))
    invalid = []
    lines = wire.splitlines(keepends=True)
    for i in range(7):
        invalid.append((b"".join(lines[:i] + lines[i + 1:]), identity))
        invalid.append((b"".join(lines[:i] + [lines[i]] + lines[i:]), identity))
    for bad in (wire[:-1], wire + b"\n", wire.replace(b"\n", b"\r\n"), b"\x00" + wire,
                b"\xff" + wire, wire.replace(b"repo=", b"repo=\t"), b"x" * 4096, b"x" * 4097):
        invalid.append((bad, identity))
    for key in ("repo", "product", "tag"):
        invalid.append((wire, dict(identity, **{key: "wrong"})))
    for old, new in ((b"NFS-UPDATE-1", b"NFS-UPDATE-2"), (b"version=", b"tag="),
                     (identity["repo"].encode(), b"example/wrong"), (b"product=mw", b"product=other"),
                     (b"tag=v", b"tag=V"), (b"tag=v4.11.0", b"tag=v4.11.1"),
                     (b"dmg=NFS-", b"dmg=../NFS-"),
                     (b".dmg.sha256", b".sha256")):
        invalid.append((wire.replace(old, new), identity))
    for size in (b"0", b"00", b"014", b"+14", b"-14", b"14x", b" 14", b"18446744073709551616"):
        invalid.append((wire.replace(b"|14|", b"|" + size + b"|"), identity))
    digest = hashlib.sha256(b"original image").hexdigest().encode()
    for hash_value in (digest[:-1], digest + b"0", digest.upper(), b"g" * 64, digest + b"|extra"):
        invalid.append((wire.replace(digest, hash_value), identity))
    invalid_versions = ["1", "", "1.2.3.4.5", "01.2", "1.02", "1..2", ".1.2", "1.2.",
                        "4294967296.0", "v1.2", "+1.2", "-1.2", "1.2-rc1", "1.2+build", "1.2 ", "1.２"]
    for version in invalid_versions:
        invalid.append((wire.replace(b"4.11.0", version.encode()), dict(identity, tag="v" + version)))
    # Swap records rather than merely changing their labels.
    invalid.append((b"".join(lines[:2] + [lines[3], lines[2]] + lines[4:]), identity))
    for expected, cases in ((True, valid), (False, invalid)):
        for data, bound in cases:
            py, c = python_schema(module, data, bound), native_schema(native, data, bound)
            assert (py is not None) == expected and (c is not None) == expected, (expected, data[:160], py, c)
            if expected:
                assert py == c == data
    assert native_schema(native, wire, identity, len(wire)) is None
    assert native_schema(native, wire, identity, len(wire) + 1) == wire
    args = (wire, len(wire), *(identity[k].encode() for k in ("repo", "product", "tag")))
    for fault in range(7):
        assert native.invalid_struct(*args, fault) == 1, fault
    parsed = module.parse(wire, **identity)
    for bad in (replace(parsed, tag="v4.11.1"), replace(parsed, version="01.2"),
                replace(parsed, dmg=replace(parsed.dmg, bytes=0)),
                replace(parsed, checksum=replace(parsed.checksum, sha256="g" * 64))):
        try:
            module.serialize(bad)
        except module.ManifestError:
            pass
        else:
            raise AssertionError("Serializer accepted invalid struct")
    pairs = [("1.2", "1.2.0.0", 0), ("1.2.0.1", "1.2", 1), ("4.10.0", "4.9.9", 1),
             ("0.0", "0.0.0.1", -1), ("4294967295.0", "4294967294.9", 1)]
    for a, b, expected in pairs:
        result = ctypes.c_int()
        assert native.order(a.encode(), b.encode(), ctypes.byref(result)) == 1
        assert result.value == module.compare_versions(a, b) == expected
        assert module.compare_versions(b, a) == -expected
    for bad in invalid_versions:
        for a, b in ((bad, "1.2"), ("1.2", bad)):
            assert native.order(a.encode(), b.encode(), ctypes.byref(ctypes.c_int())) == 0
            try:
                module.compare_versions(a, b)
            except module.ManifestError:
                pass
            else:
                raise AssertionError(("Invalid version accepted", a, b))
    print(f"Schema parity valid={len(valid)} invalid={len(invalid)}; versions valid={len(pairs)} invalid={len(invalid_versions) * 2}")


def keypair(root, name):
    key = root / (name + ".pem")
    command(["/usr/bin/openssl", "genpkey", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:prime256v1",
             "-pkeyopt", "ec_param_enc:named_curve", "-out", key])
    key.chmod(0o600)
    der = command(["/usr/bin/openssl", "pkey", "-in", key, "-pubout", "-outform", "DER"])
    # Fixed SubjectPublicKeyInfo prefix for id-ecPublicKey / prime256v1.
    prefix = bytes.fromhex("3059301306072a8648ce3d020106082a8648ce3d030107034200")
    assert der[:len(prefix)] == prefix and len(der) == len(prefix) + 65
    return key, der[len(prefix):]


def sign(root, key, wire):
    message = root / "message"
    message.write_bytes(wire)
    return command(["/usr/bin/openssl", "dgst", "-sha256", "-sign", key, message])


def admit(implementation, wire, sig, identity, pins, paths=None):
    if isinstance(implementation, types.ModuleType):
        try:
            manifest = implementation.verify(wire, sig, **identity, **({"pins": pins} if pins is not None else {}))
            if paths:
                for path, asset in zip(paths, (manifest.dmg, manifest.checksum)):
                    implementation.verify_asset(path, asset)
            return True
        except implementation.ManifestError:
            return False
    result = implementation.admission(wire, len(wire), sig, len(sig),
        *(identity[k].encode() for k in ("repo", "product", "tag")),
        *(pins if pins is not None else (None, None)),
        *(str(p).encode() for p in paths) if paths else (None, None))
    assert result in (0, 1), "Failed native authentication retained parsed authority"
    return result == 1


def crypto_checks(root, native, module, signature_mutants, digest_mutants):
    key, point = keypair(root, "signer")
    _, wrong = keypair(root, "wrong")
    pins = (point, wrong)
    wire, identity, dmg, checksum = fixture()
    signature = sign(root, key, wire)
    paths = (root / "image.dmg", root / "image.dmg.sha256")
    changed, _, hostile, repaired = fixture(dmg=b"replaced image")
    assert len(hostile) == len(dmg) and len(repaired) == len(checksum)
    paths[0].write_bytes(hostile)
    paths[1].write_bytes(repaired)
    # Prove the attack also satisfies the old unsigned checksum relationship.
    assert repaired.decode().split()[0] == hashlib.sha256(hostile).hexdigest()
    # Failing-first: each removed admission check accepts its broken control.
    for mutant in signature_mutants:
        assert admit(mutant, changed, signature, identity, pins, paths), "Signature control failed to reach the bypass"
    for mutant in digest_mutants:
        assert admit(mutant, wire, signature, identity, pins, paths), "Digest control failed to reach the bypass"
    rejected = 0
    for implementation in (native, module):
        assert not admit(implementation, changed, signature, identity, pins, paths)
        assert not admit(implementation, wire, signature, identity, pins, paths)
        rejected += 2
    print(f"Failing-first bypass accepted signature=2/2 digest=2/2; intact refused={rejected}/4")
    for product in ("mw", "carbon"):
        wire, identity, dmg, checksum = fixture(product)
        signature = sign(root, key, wire)
        paths[0].write_bytes(dmg)
        paths[1].write_bytes(checksum)
        for implementation in (native, module):
            assert admit(implementation, wire, signature, identity, pins, paths)
            assert admit(implementation, wire, signature, identity, (wrong, point), paths), "Pin B rotation"
            assert admit(implementation, wire, signature, identity, (bytes(65), point), paths), "Unset slot cannot mask valid B"
            assert not admit(implementation, wire, signature, identity, (wrong, wrong), paths)
            assert not admit(implementation, wire, signature, identity, (bytes(65), bytes(65)), paths)
            assert not admit(implementation, wire, signature, identity, None, paths)
            assert not admit(implementation, wire, signature[:-1] + bytes([signature[-1] ^ 1]), identity, pins, paths)
            for bad in (b"", b"\x00", b"x" * 80, signature + b"\x00", b"x" * 81):
                assert not admit(implementation, wire, bad, identity, pins, paths)
            for bad in (wire[:-1], wire + b"\n", b"x" * 4097):
                assert not admit(implementation, bad, signature, identity, pins, paths)
            paths[0].write_bytes(dmg + b"x")
            assert not admit(implementation, wire, signature, identity, pins, paths)
            paths[0].write_bytes(dmg[:-1])
            assert not admit(implementation, wire, signature, identity, pins, paths)
            paths[0].write_bytes(dmg)
            paths[1].write_bytes(checksum.replace(checksum[:1], b"z", 1))
            assert not admit(implementation, wire, signature, identity, pins, paths)
            paths[1].write_bytes(checksum)
    for slot, expected in enumerate(module.PINS):
        out = ctypes.create_string_buffer(65)
        native.pin(slot, out)
        assert out.raw == expected and len(expected) == 65, "Native/Python pin mismatch"
    print("Signed originals admitted=4/4; wrong keys, unset pins, altered signatures/assets and bounds refused; pins A/B parity=2/2")


def main():
    with tempfile.TemporaryDirectory(prefix="update-manifest-") as scratch:
        root = Path(scratch)
        header, python = HEADER.read_text(), MODULE.read_text()
        # Build broken controls first, then the intact implementation.
        signature_c = mutate(header,
            "valid = SecKeyVerifySignature(key, kSecKeyAlgorithmECDSASignatureMessageX962SHA256,\n"
            "                                          message, sig, &error);", "valid = 1;")
        digest_c = mutate(header, "!strcmp(asset->sha256, sha256)", "1")
        signature_py = mutate(python,
            "valid = sec.SecKeyVerifySignature(key, algorithm, message, sig, ctypes.byref(error))", "valid = True")
        digest_py = mutate(python, "and asset.sha256 == sha256", "and True")
        sig_mutants = (build(root, "signature-bypass", signature_c), load_module("manifest_signature_bypass", signature_py))
        hash_mutants = (build(root, "digest-bypass", digest_c), load_module("manifest_digest_bypass", digest_py))
        native, module = build(root, "intact", header), load_module("manifest_intact")
        schema_checks(native, module)
        crypto_checks(root, native, module, sig_mutants, hash_mutants)
    print("pub_update_manifest_test PASS")


if __name__ == "__main__":
    main()
