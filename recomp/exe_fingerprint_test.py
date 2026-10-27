"""Offline proof of the speed.exe code fingerprint (exe_fingerprint.py); no game launch, temp copies only.

    tools/.venv/bin/python -I port/recomp/exe_fingerprint_test.py [path/to/speed.exe]

(a) the owner's exe is accepted; (b) a copy with another PE timestamp and checksum, a changed resource byte, a
changed byte in the unnamed 5th section, an extra appended section and an overlay is accepted with the same
fingerprint (also through recomp.PE); (c) one flipped .text byte, (d) one .rdata byte, (e) one .data byte, (f) a
changed entry point are each refused, naming the region.
Check of the check: with the fingerprint made to ignore .text (a stub region reader), (c) must stop being refused;
the run prints both outcomes and fails if the broken fingerprint still passed it.
Last run: 6 cases PASS; with .text ignored (c) was ACCEPTED (test sees the break) -> exit 0.
"""
import hashlib
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import exe_fingerprint as fpm  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
EXE = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("NFS_SPEED_EXE")
       or os.path.join(ROOT, "Need For Speed Most Wanted Black Edition", "speed.exe"))


def layout(d):
    off = struct.unpack_from("<I", d, 0x3C)[0]
    n = struct.unpack_from("<H", d, off + 6)[0]
    s0 = off + 24 + struct.unpack_from("<H", d, off + 20)[0]
    secs = [struct.unpack_from("<8sIIII", d, s0 + i * 40) for i in range(n)]
    return off, s0, secs


def variant_b(d):
    """Same code, everything the port doesn't read changed."""
    d = bytearray(d)
    off, s0, secs = layout(d)
    struct.pack_into("<I", d, off + 8, 0x5A5A5A5A)                      # TimeDateStamp
    struct.pack_into("<I", d, off + 24 + 64, 0x12345678)                # CheckSum
    rsrc = [s for s in secs if s[2] == 0x5C7000][0]
    d[rsrc[4] + 0x100] ^= 0xFF                                          # a resource byte
    fifth = [s for s in secs if s[2] == 0x638000][0]
    d[fifth[4] + 0x20] ^= 0xFF                                          # the unnamed 5th section
    n = len(secs)
    new_raw = (len(d) + 0xFFF) & ~0xFFF
    d += bytes(new_raw - len(d)) + b"extra section payload" * 100
    new_rva = 0x680000
    struct.pack_into("<8sIIIIIIHHI", d, s0 + n * 40, b".extra", 0x1000, new_rva, 0x1000, new_raw, 0, 0, 0, 0, 0x40000040)
    struct.pack_into("<H", d, off + 6, n + 1)
    struct.pack_into("<I", d, off + 24 + 56, 0x681000)                  # SizeOfImage
    d += b"\xAA" * 777                                                  # overlay
    return bytes(d)


def flip(d, section_rva, delta):
    d = bytearray(d)
    for nm, vs, va, rs, raw in layout(d)[2]:
        if va == section_rva:
            d[raw + delta] ^= 0x01
            return bytes(d)
    raise AssertionError("section not found")


def entry(d):
    d = bytearray(d)
    off = layout(d)[0]
    struct.pack_into("<I", d, off + 24 + 16, struct.unpack_from("<I", d, off + 24 + 16)[0] + 16)
    return bytes(d)


def refused(d):
    """The message when check() refuses `d`, else None."""
    try:
        fpm.check(d, "temp.exe")
    except SystemExit as e:
        return str(e)
    return None


def cases(d):
    """[(label, passed, detail)]"""
    out = []
    out.append(("a owner exe accepted", fpm.check(d) == fpm.SUPPORTED_FINGERPRINT and
                fpm.describe(d)[1] == fpm.SUPPORTED_FINGERPRINT, ""))
    b = variant_b(d)
    parts_b, fp_b = fpm.describe(b)
    out.append(("b headers/resource/5th/extra section/overlay accepted, same fingerprint",
                b != d and hashlib.sha256(b).hexdigest() != fpm.WHOLE_FILE_SHA256 and fp_b == fpm.SUPPORTED_FINGERPRINT
                and refused(b) is None, ""))
    for label, name, data in (("c .text byte flipped refused naming .text", ".text", flip(d, 0x1000, 0x12345)),
                              ("d .rdata byte flipped refused naming .rdata", ".rdata", flip(d, 0x490000, 0x4321)),
                              ("e .data byte flipped refused naming .data", ".data", flip(d, 0x4EA000, 0x1000)),
                              ("f entry point changed refused naming PE layout", "PE layout", entry(d))):
        msg = refused(data)
        others = [n for n in (".text", ".rdata", ".data", "PE layout") if n != name]
        out.append((label, msg is not None and f"{name} differs" in msg and not any(f"{o} differs" in msg for o in others)
                    and "PC v1.3 code" in msg, (msg or "ACCEPTED").splitlines()[0]))
    return out


def main():
    d = open(EXE, "rb").read()
    results = cases(d)
    for label, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {label}  {detail}")
    bad = [r for r in results if not r[1]]
    # Pipeline check through the translator's own loader (recomp.PE), for the variant that must be accepted.
    import tempfile
    import recomp
    with tempfile.NamedTemporaryFile(suffix=".exe") as t:
        t.write(variant_b(d))
        t.flush()
        pe = recomp.PE(t.name)
    ok = pe.fingerprint == fpm.SUPPORTED_FINGERPRINT and pe.read(0x401000, 16) == d[0x1000:0x1010]
    print(f"{'PASS' if ok else 'FAIL'}  b2 recomp.PE loads the variant, same fingerprint and code bytes")
    bad += [] if ok else ["b2"]

    # Check of the check: a fingerprint that ignores .text must let case (c) through.
    real = fpm._region
    fpm._region = lambda data, secs, rva, size: bytes(size) if rva == 0x1000 else real(data, secs, rva, size)
    real_supported = dict(fpm.SUPPORTED)
    zero = fpm.hashlib.sha256(bytes(0x48E2A5)).hexdigest()
    fpm.SUPPORTED[".text"] = zero
    h = fpm.hashlib.sha256(b"nfsmw-exe-fp-v1\n")
    for k in fpm.NAMES:
        h.update(bytes.fromhex(fpm.SUPPORTED[k]))
    broken_fp = h.hexdigest()
    saved_fp, saved_whole = fpm.SUPPORTED_FINGERPRINT, fpm.WHOLE_FILE_SHA256
    fpm.SUPPORTED_FINGERPRINT, fpm.WHOLE_FILE_SHA256 = broken_fp, "0" * 64
    try:
        accepted = refused(flip(d, 0x1000, 0x12345)) is None
    finally:
        fpm._region, fpm.SUPPORTED_FINGERPRINT, fpm.WHOLE_FILE_SHA256 = real, saved_fp, saved_whole
        fpm.SUPPORTED.update(real_supported)
    print(f"{'PASS' if accepted else 'FAIL'}  check of the check: with .text ignored, a flipped .text byte is "
          f"{'ACCEPTED (the test sees it)' if accepted else 'still refused (the test would not notice)'}")
    bad += [] if accepted else ["selfcheck"]
    print("exe_fingerprint_test:", "PASS" if not bad else f"FAIL {len(bad)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
