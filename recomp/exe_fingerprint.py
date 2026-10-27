"""Which speed.exe the port supports: the PC v1.3 *code*, not one exact file.

The recompiled functions, hooks and function starts are addresses in the loaded image; the runtime loads the
exe's sections at their virtual addresses and binds imports by slot. So the port depends on exactly these bytes:
  .text   0x401000  size 0x48E2A5   the code that was translated
  .rdata  0x890000  size 0x5971D    constants, vtables, the import tables (names/ordinals the runtime binds)
  .data   0x8EA000  size 0xDCE10    initialized globals (the tail past the file's raw data is zero)
  layout  image base, entry point, import-directory location, and the three regions' address and size.
It does not depend on the PE timestamp, checksum, other header fields, resources (.rsrc, 0x9C7000: nothing
translated or hooked touches it; the runtime's LoadIcon/LoadCursor are stubs), the unnamed 5th section (0xA38000,
0x41000 bytes: a base-relocation table in some repacks; the image loads at its preferred base so it is never
applied; no translated code, hook or runtime address falls in it) or anything appended after the sections.

fingerprint = SHA-256("nfsmw-exe-fp-v1\\n" + layout digest + .text digest + .rdata digest + .data digest), each
digest the SHA-256 of that region's loaded bytes. Regions are found by virtual address, whatever the section is
called. runtime/app.m computes the same thing for the game-folder check; keep the two in step.
"""
import hashlib
import struct

# Whole-file SHA-256 of the owner's exe, still accepted as an alias.
WHOLE_FILE_SHA256 = "80774c2e5d619b4f120b48d4462896fd504c263399d203a238769cffde1d253c"
IMAGE_BASE = 0x400000
REGIONS = ((".text", 0x1000, 0x48E2A5), (".rdata", 0x490000, 0x5971D), (".data", 0x4EA000, 0xDCE10))   # (name, rva, size)
SUPPORTED = {   # digests of the owner's exe (exe_fingerprint_test.py checks they still come out of it)
    "layout": "6f058466b8fcaaeaef24a2308c66df8b8cebae016e64c42eec4cee718573a429",
    ".text": "f258b7f607d10109c577e63497041476935793b8211e5acfd3cb88ac66b24470",
    ".rdata": "fb27d471178fed85c12d31ee6a5c8dd3e891868ed9c04f19c0fecdbbe660ddc1",
    ".data": "831c71c966f4aa361e0eef3bc4d8507e7b44bf0a9ffab2af8eb18dd25d29aec5",
}
SUPPORTED_FINGERPRINT = "92079ca1caec8fea3919d4861a79c81f75f7c8f0007469c8ecfdb0451302447f"
NAMES = ("layout", ".text", ".rdata", ".data")
WHY = "a different game version or a protected exe; this port supports the PC v1.3 code"   # (layout: image base, entry point, import table, section addresses)


def _parse(data):
    """(optional-header offset, sections [(vsize, rva, rawsize, rawoff)]) or ValueError."""
    try:
        if data[:2] != b"MZ":
            raise ValueError
        off = struct.unpack_from("<I", data, 0x3C)[0]
        if data[off:off + 4] != b"PE\0\0":
            raise ValueError
        machine, n = struct.unpack_from("<HH", data, off + 4)
        optsz = struct.unpack_from("<H", data, off + 20)[0]
        opt = off + 24
        if machine != 0x14C or struct.unpack_from("<H", data, opt)[0] != 0x10B or n > 96:
            raise ValueError
        s0 = opt + optsz
        secs = [struct.unpack_from("<IIII", data, s0 + i * 40 + 8) for i in range(n)]
        return opt, secs
    except (ValueError, struct.error):
        raise ValueError("not a 32-bit Windows exe") from None


def _region(data, secs, rva, size):
    """The loaded bytes [rva, rva+size) of the image (zero beyond the section's raw data), or None."""
    for vs, va, rs, raw in secs:
        if va <= rva and rva + size <= va + max(vs, rs):
            take = max(0, min(size, rs - (rva - va)))
            blob = data[raw + rva - va:raw + rva - va + take]
            if len(blob) != take:
                return None
            return blob + bytes(size - take)
    return None


def describe(data):
    """({'layout'|'.text'|'.rdata'|'.data': hex digest or None}, fingerprint hex) of a speed.exe image."""
    opt, secs = _parse(data)
    base, entry = struct.unpack_from("<I", data, opt + 28)[0], struct.unpack_from("<I", data, opt + 16)[0]
    imp = struct.unpack_from("<II", data, opt + 96 + 8)
    out = {"layout": hashlib.sha256(struct.pack("<III", base, entry, imp[0]) + struct.pack("<I", imp[1]) +
                                    b"".join(struct.pack("<II", r, s) for _, r, s in REGIONS)).hexdigest()}
    for name, rva, size in REGIONS:
        blob = _region(data, secs, rva, size)
        out[name] = hashlib.sha256(blob).hexdigest() if blob is not None else None
    h = hashlib.sha256(b"nfsmw-exe-fp-v1\n")
    for k in NAMES:
        h.update(bytes.fromhex(out[k]) if out[k] else bytes(32))
    return out, h.hexdigest()


def check(data, path="speed.exe"):
    """The fingerprint of a supported exe; SystemExit with one clear message when the code differs."""
    if hashlib.sha256(data).hexdigest() == WHOLE_FILE_SHA256:
        return SUPPORTED_FINGERPRINT
    try:
        parts, fp = describe(data)
    except ValueError as e:
        raise SystemExit(f"{path}: {e}; this port supports the NFS Most Wanted PC v1.3 speed.exe.")
    if fp != SUPPORTED_FINGERPRINT:
        bad = [k for k in NAMES if parts[k] != SUPPORTED[k]]
        what = ", ".join(("PE layout" if k == "layout" else k) for k in bad)
        raise SystemExit(f"{path}: {what} differs: {WHY}.\n  code fingerprint {fp}\n  expected         {SUPPORTED_FINGERPRINT}")
    return fp
