"""Offline proof of the speed.exe code fingerprint (exe_fingerprint.py); no game launch, temp copies only.

    tools/.venv/bin/python -I port/recomp/exe_fingerprint_test.py [path/to/speed.exe]

(a) the owner's exe is accepted; (b) a copy with another PE timestamp and checksum, a changed resource byte, a
changed byte in the unnamed 5th section, an extra appended section and an overlay is accepted with the same
fingerprint (also through recomp.PE); (c) one flipped .text byte, (d) one .rdata byte, (e) one .data byte, (f) a
changed entry point are each refused, naming the region.
Check of the check: with the fingerprint made to ignore .text (a stub region reader), (c) must stop being refused;
the run prints both outcomes and fails if the broken fingerprint still passed it.
C folder fixtures: MW 9/9, Carbon 9/9. Original Carbon timestamp/checksum control
exit 1 (acceptance assertion fails); new exit 0. Flipped .text: exit 1, names .text.
Last run: 6 cases PASS; with .text ignored (c) was ACCEPTED (test sees the break) -> exit 0.
"""
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
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


# Pinned original Carbon rejection branch; no history reads in the regression test.
OLD_CARBON_BRANCH = '''#if NFS_GAME == NFS_GAME_MW
    int ok = exe_digests(d, (size_t)len, parts, fp);
#else
    free(d); snprintf(why, n, "private image SHA-256 mismatch"); return 1;
    int ok = 0; /* unreachable MW digest path */
#endif
'''
OLD_CARBON_BRANCH_SHA256 = '155946a457363c7c0d77334f963cb74e66b98d19a0d3472df7e3a49d21abbbc6'


def synthetic_pe(game):
    """Independent PE fixtures: MW's pinned layout; a distinct, small Carbon layout."""
    specs = ((('.text', 0x1000, 0x48E2A5), ('.rdata', 0x490000, 0x5971D),
              ('.data', 0x4EA000, 0xDCE10)) if game == 'mw' else
             (('.text', 0x2000, 0x650), ('.rdata', 0x5000, 0x340), ('.data', 0x7000, 0x920)))
    d = bytearray(0x400)
    d[:2] = b'MZ'
    struct.pack_into('<I', d, 0x3c, 0x80)
    d[0x80:0x84] = b'PE\0\0'
    struct.pack_into('<HH', d, 0x84, 0x14c, 3)
    struct.pack_into('<H', d, 0x94, 0xe0)
    opt, s0 = 0x98, 0x178
    struct.pack_into('<H', d, opt, 0x10b)
    struct.pack_into('<I', d, opt + 16, specs[0][1])
    struct.pack_into('<I', d, opt + 28, 0x400000)
    struct.pack_into('<II', d, opt + 104, specs[1][1], 40)
    for i, (name, rva, size) in enumerate(specs):
        raw = len(d)
        # Exercise the loaded .data zero tail, rather than hashing file padding.
        rs = size if i < 2 else size // 2
        struct.pack_into('<8sIIII', d, s0 + 40*i, name.encode(), size, rva, rs, raw)
        d += bytes([0x41 + i]) * rs
    return bytes(d), specs


def folder_fixture(root, game, data, *, old=False, whole_only=False):
    """Compile the production C folder check from runtime/app.m, as private_mix_test.py does."""
    source = Path(__file__).resolve().parent.parent.joinpath('runtime/app.m').read_text()
    block = source[source.index('extern const char speed_exe_fingerprint[]'):source.index('static NSString *ns(')]
    constants = fpm.table_constants(data, game=game)
    if whole_only:
        parts, fp = fpm.describe(data, game=game)
        constants = constants.replace(fp, '0'*64)
        for digest in parts.values():
            constants = constants.replace(digest, '0'*64)
    if old:
        assert hashlib.sha256(OLD_CARBON_BRANCH.encode()).hexdigest() == OLD_CARBON_BRANCH_SHA256
        needle = '    int ok = exe_digests(d, (size_t)len, parts, fp);\n'
        assert block.count(needle) == 1
        block = block.replace(needle, OLD_CARBON_BRANCH)
        a, b = block.index('static int exe_digests('), block.index('// 0 when `dir`')
        block = block[:a] + ('static int exe_digests(const uint8_t *d, size_t len, char parts[4][65], char fp[65]) { return 0; }\n\n') + block[b:]
        whole = hashlib.sha256(data).hexdigest()
        constants = (f'const char speed_exe_fingerprint[] = "{whole}";\n'
                     'const char speed_exe_region_sha256[4][65] = {"","","",""};\n'
                     f'const char speed_exe_sha256[] = "{whole}";\n')
    name = 'speed.exe' if game == 'mw' else 'NFSC.exe'
    prelude = '''#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <CommonCrypto/CommonDigest.h>
#define NFS_GAME_MW 1
extern const char speed_exe_sha256[];
'''
    prelude += f'#define NFS_GAME {1 if game == "mw" else 2}\n#define GAME_EXECUTABLE "{name}"\n'
    main = '''int main(int argc, char **argv) {
    if (argc != 2) return 2;
    char why[1400] = "";
    int result = check_game_dir(argv[1], why, sizeof why);
    puts(why);
    return result;
}
'''
    src, table, binary = root/'check.c', root/'table.c', root/'check'
    src.write_text(prelude + block + main)
    table.write_text('#include <stdint.h>\n' + constants)
    result = subprocess.run(['clang', '-O1', '-w', '-fsanitize=address,undefined', str(src), str(table), '-o', str(binary)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-3000:]
    return binary, name


def synthetic_cases():
    """Old Carbon timestamp check: exit 1; new: exit 0; flipped .text: exit 1 naming .text."""
    import build_all
    import types
    app = Path(__file__).resolve().parent.parent.joinpath('runtime/app.m').read_text()
    # Check the actual Objective-C panel strings selected by each build.
    panel = app[app.index('static NSString *pick_folder('):app.index('// Asks until')]
    carbon_panel = panel.split('#else', 1)[1].split('#endif', 1)[0]
    assert 'Need for Speed Carbon' in carbon_panel and 'GAME_EXECUTABLE' in carbon_panel
    assert 'Most Wanted' not in carbon_panel and '1.3' not in carbon_panel
    assert 'Choose your Need for Speed Most Wanted folder: the one with speed.exe (version 1.3) in it.' in panel
    with tempfile.TemporaryDirectory(prefix='exe-folder-') as td:
        root = Path(td)
        for game in ('mw', 'carbon'):
            dest = root/game
            dest.mkdir()
            data, specs = synthetic_pe(game)
            parts, fp = fpm.describe(data, game=game)
            assert fpm.regions(data, game=game) == specs
            # Independent digest oracle over fixture payloads and the zero-filled .data tail.
            for i, (name, _, size) in enumerate(specs):
                rs = size if i < 2 else size // 2
                assert parts[name] == hashlib.sha256(bytes([0x41+i])*rs + bytes(size-rs)).hexdigest()
            binary, name = folder_fixture(dest, game, data)
            image = dest/name
            def run(blob):
                image.write_bytes(blob)
                out = subprocess.run([str(binary), str(dest)], capture_output=True, text=True)
                assert not out.stderr, out.stderr[-2000:]
                return out.returncode, out.stdout.strip()
            edited = bytearray(data)
            struct.pack_into('<I', edited, 0x88, 0x12345678)
            struct.pack_into('<I', edited, 0x98 + 64, 0x87654321)
            assert fpm.describe(edited, game=game) == (parts, fp)
            assert run(data) == (0, '')
            assert run(edited) == (0, '')
            title = 'Need for Speed Most Wanted PC v1.3' if game == 'mw' else 'Need for Speed Carbon'
            for region, rva, _ in specs:
                rc, msg = run(flip(data, rva, 5))
                assert rc == 1 and f'{region} differs' in msg and title in msg and name in msg, (rc, msg)
            rc, msg = run(entry(data))
            assert rc == 1 and 'PE layout differs' in msg and title in msg, (rc, msg)
            rc, msg = run(b'not a PE')
            assert rc == 1 and "isn't a 32-bit Windows program" in msg and title in msg and name in msg, (rc, msg)
            # An absent region has a zero digest and must be rejected by name.
            missing = bytearray(data)
            struct.pack_into('<I', missing, 0x178 + 40 + 12, 0x7ffff000)
            rc, msg = run(missing)
            assert rc == 1 and '.rdata differs' in msg, (rc, msg)
            assert run(edited + b'overlay') == (0, '')
            print(f'PASS {game} C folder check: 9/9; timestamp/checksum exit=0; .text flip exit=1 naming .text')
            if game == 'carbon':
                # Prove build_image emits these constants, rather than leaving the private whole-file fingerprint.
                output = dest/'generated'
                pe = types.SimpleNamespace(data=data, imports={}, fingerprint=hashlib.sha256(data).hexdigest(), sha256=hashlib.sha256(data).hexdigest())
                build_all.build_image([], pe, str(output), 'fixture', mw_patches=False, game='carbon', compile=False)
                assert (output/'table.c').read_text().endswith(fpm.table_constants(data, game='carbon'))
                old = dest/'old'; old.mkdir()
                binary, name = folder_fixture(old, game, data, old=True)
                assert run(data) == (0, '')   # original whole-file fast path
                rc, msg = run(edited)
                assert rc == 1 and msg == 'private image SHA-256 mismatch', (rc, msg)
                print('PASS original Carbon control: timestamp/checksum exit=1 (regression assertion fails); new exit=0; .text flip exit=1')
                # Force the whole-file path independently of region validation.
                fast = dest/'fast'; fast.mkdir()
                binary, name = folder_fixture(fast, game, data, whole_only=True)
                assert run(data) == (0, '')
                print('PASS Carbon whole-file fast path: exit=0 with deliberately wrong region digests')


def main():
    synthetic_cases()
    if not Path(EXE).is_file():
        print("SKIP real-image fingerprint cases: executable absent")
        return 3
    d = open(EXE, "rb").read()
    legacy_constants = (f'const char speed_exe_fingerprint[] = "{fpm.SUPPORTED_FINGERPRINT}";\n' +
                        "const char speed_exe_region_sha256[4][65] = {" +
                        ",".join(f'"{fpm.SUPPORTED[k]}"' for k in fpm.NAMES) + "};\n" +
                        f'const char speed_exe_sha256[] = "{fpm.WHOLE_FILE_SHA256}";\n')
    assert fpm.table_constants(d).startswith(legacy_constants)
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
