"""Offline server module proof; autodiscovered by runtime/offline_tests.sh.

Owner input only, read-only; temp generated tables; no game/Unicorn/network.
Last run: 5287/5287 relocated words, 1413 manifest entries, 3 exports/129 IAT
cells; normal 0 failed checks. --broken base: 2 failed checks (unapplied
relocations + translated absolute operand); --broken export: 1 failed check.
The default run requires both broken subprocesses to fail with those counts.
Data code pointers: every relocated word in .rdata/.data that points into .text is a manifest function except two
exception continuations; with 0x2BC90 and 0x43FEA removed from the manifest: 1 failed check (4 unlisted, 2026-10-27).
"""
import argparse
import copy
import hashlib
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import recomp
import server_module as server


def check_image(pe, raw, out):
    """Independent source-file words, plus a translated export and emitted tables."""
    failures = []

    def check(ok, name):
        if not ok:
            failures.append(name)

    delta = server.BASE - server.PREFERRED_BASE
    relocated = 0
    for rva in pe.relocations:
        # Convert each relocation RVA back to original file storage, not the
        # implementation's mapped image or its already-adjusted words.
        section = next(s for s in pe.sections if s[2] <= rva < s[2] + s[3])
        fileoff = section[4] + rva - section[2]
        original = struct.unpack_from('<I', raw, fileoff)[0]
        actual = struct.unpack('<I', pe.read(server.BASE + rva, 4))[0]
        relocated += actual == (original + delta) & 0xFFFFFFFF
    check(relocated == 5287, f'relocations {relocated}/5287')
    check(pe.read(server.BASE + 0x59000, 0x1172C) == bytes(0x1172C), 'BSS zero fill')
    check(pe.read(server.BASE + pe.size, 1) is None, 'image bounds')
    functions = server.load_functions(pe)
    # Code addresses kept in data (function-pointer tables) are indirect call targets: each must be a manifest
                                                                                                              
    # unhandled-exception filter, also pushed to SetUnhandledExceptionFilter). The two exceptions are
    # exception-path continuations inside their functions: a catch block (0x14AF) and an SEH scope handler (0x441C6).
    text_hi = server.BASE + 0x4CF85
    pointed = set()
    for rva in pe.relocations:                  # original file words, independent of the relocation check above
        section = next(s for s in pe.sections if s[2] <= rva < s[2] + s[3])
        if rva >= 0x4CF85:
            pointed.add((struct.unpack_from('<I', raw, section[4] + rva - section[2])[0] + delta) & 0xFFFFFFFF)
    unlisted = {a for a in pointed if server.BASE + 0x1000 <= a < text_hi} - functions.keys()
    check(unlisted == {server.BASE + 0x14AF, server.BASE + 0x441C6},
          'data code pointers outside manifest: ' + ' '.join(f'{a:08X}' for a in sorted(unlisted)))
    rc = recomp.Recompiler(pe=pe, functions=functions)
    rc.calls = True
    code, why = rc.translate(server.BASE + 0x10B0, functions[server.BASE + 0x10B0])
    check(code is not None and why is None and '0x2058368u' in code and '0x10058368u' not in code,
          'translated export absolute operand at relocated base')
    server.emit_tables(pe, functions, out)
    tables = (out / 'server_tables.c').read_text()
    exports = re.findall(r'\{"([^"]+)", (\d+)u, 0x([0-9A-F]+)u\}', tables)
    check({name: (int(ordinal), int(address, 16)) for name, ordinal, address in exports} ==
          {name: (ordinal, server.BASE + rva) for name, (ordinal, rva) in server.EXPORTS.items()},
          f'export tables {len(exports)}/3')
    imports = re.findall(r'\{0x([0-9A-F]+)u, "([^"]+![^"]+)"\}', tables)
    check(len(imports) == 129 and {int(a, 16): n for a, n in imports} == pe.imports, '129 import cells')
    check(f'server_entry_addr = 0x{server.BASE + server.ENTRY_RVA:08X}u' in tables, 'PE entry table')
    dispatch = re.search(r'server_guest_func_addr\[\] = \{([^}]+)\}', tables)[1]
    check([int(a.removesuffix('u'), 16) for a in dispatch.split(',')] == list(functions),
          '1413 dispatch addresses')
    pointers = re.search(r'server_guest_func_ptr\[\]\)\(Cpu \*\) = \{([^}]+)\}', tables)[1]
    check(pointers.split(',') == [f'f_{a:08X}' for a in functions], '1413 dispatch pointers')
    check(not re.search(r'\b(?:guest_func_addr|guest_func_ptr|guest_func_count|speed_imports)\b', tables),
          'private table namespace')
    print(f'words={relocated}/5287 functions={len(functions)} exports={len(exports)}/3 IAT={len(imports)}/129')
    for failure in failures:
        print('FAIL:', failure)
    print(f'failed checks={len(failures)}')
    return len(failures)


def memory_map_check():
    root = HERE.parent
    boot = (root / 'runtime/boot.c').read_text()
    heap = (root / 'runtime/heap.c').read_text()
    threads = (root / 'runtime/threads.c').read_text()

    def define(source, name):
        return int(re.search(r'^#define ' + name + r' (0x[0-9A-Fa-f]+)u?\b', source, re.M)[1], 16)

    ranges = [(define(heap, 'PROCESS_HEAP_LO'), define(heap, 'PROCESS_HEAP_HI')),
              (define(boot, 'HEAP_BASE'), 0x50000000),
              (define(heap, 'RESOURCE_HEAP_LO'), define(heap, 'RESOURCE_HEAP_HI')),
              (define(boot, 'STACK_TOP') - 0x100000, define(boot, 'STACK_TOP')),
              (define(boot, 'TEB'), define(boot, 'PEB') + 0x1000)]
    assert all(hi <= server.BASE or server.RESERVED_END <= lo for lo, hi in ranges)
    assert 'if (!lo) lo = galloc(stack + 0x2000);' in threads
    speed = recomp.PE()
    server.check_reservation(speed)
    collision = copy.copy(speed)
    collision.sections = [(b'.extra', 4096, server.BASE - speed.base, 4096, 0)]
    try:
        server.check_reservation(collision)
    except ValueError:
        pass
    else:
        raise AssertionError('overlapping speed section accepted')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--broken', choices=['base', 'export'])
    ap.add_argument('--dll', default=os.environ.get('NFS_SERVER_DLL') or
                    str(Path(recomp.EXE).with_name('server.dll')))
    args = ap.parse_args()
    path = Path(args.dll)
    if not path.exists():
        print('SKIP: owner server.dll absent')
        return 3
    pe = server.ServerPE(path)
    raw = path.read_bytes()
    with tempfile.TemporaryDirectory(prefix='server-module-') as tmp:
        out = Path(tmp)
        if args.broken == 'base':
            # Correct mapping/base but none of the 5287 HIGHLOW words adjusted.
            image = bytearray(pe.data)
            for _, _, rva, rs, fileoff in pe.sections:
                image[rva:rva + rs] = raw[fileoff:fileoff + rs]
            pe.data = bytes(image)
        elif args.broken == 'export':
            del pe.exports['StartServer']
        failed = check_image(pe, raw, out)
        if args.broken:
            return int(failed != 0)
        assert failed == 0
        memory_map_check()
        # Strict server fingerprint does not broaden recomp.PE acceptance.
        try:
            recomp.PE(str(path))
        except SystemExit:
            pass
        else:
            raise AssertionError('speed PE unexpectedly accepted DLL')
        corrupt = bytearray(raw)
        corrupt[-1] ^= 1
        bad = out / 'bad.dll'
        bad.write_bytes(corrupt)
        for content in [bytes(corrupt), raw[:-1]]:
            bad.write_bytes(content)
            try:
                server.ServerPE(bad)
            except ValueError:
                pass
            else:
                raise AssertionError('server fingerprint accepted modified input')
        # Missing DLL is cleanly optional and clears an old archive.
        stale = out / 'server.a'
        stale.write_text('stale')
        assert server.build(out / 'absent.dll', out=out) is None and not stale.exists()
        for variant, expected in [('base', 2), ('export', 1)]:
            proc = subprocess.run([sys.executable, '-I', str(Path(__file__).resolve()),
                                   '--dll', str(path), '--broken', variant], capture_output=True, text=True)
            assert proc.returncode == 1 and f'failed checks={expected}' in proc.stdout, proc.stdout + proc.stderr
            print(f'broken {variant}: rejected ({expected} failed checks, exit 1)')
    assert hashlib.sha256(path.read_bytes()).hexdigest() == server.SHA256
    print('server_module_test PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
