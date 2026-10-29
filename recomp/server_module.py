"""




"""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess

import recomp

HERE = Path(__file__).resolve().parent
FILE_SIZE = 380928
SHA256 = "c4fdd0e9e779be7f4be7728b03339eda4fd27248ceb9d2c640403b8fc90587ff"
PREFERRED_BASE = 0x10000000
# Build-time module reservation, including room up to the next 1 MB boundary.
# speed ends at 0x00A79000; process heap begins at 0x10000000 (heap.c).
                                                                           
# 0x30000000; resources at 0x50000000; main stack/TEB/PEB near 0x7FF00000.
# The future loader must honor this reservation for modules and explicit-address
# VirtualAlloc requests too; this file does not add a runtime allocator/loader.
BASE = 0x02000000
RESERVED_END = 0x02100000
IMAGE_SIZE = 0x6F000
ENTRY_RVA = 0x3FF65
FUNCTION_COUNT = 1413
EXPORTS = {"IsServerRunning": (1, 0x10B0), "StartServer": (2, 0x15B0),
           "StopServer": (3, 0x1000)}


class ServerPE(recomp.PE):
    """Explicit second profile: fingerprint first, map/zero-fill, then HIGHLOW fixups.

    data is an in-memory PE image, not file layout. Inherited import parsing uses
    _rva_off below; read() includes mapped BSS and cannot leak into another section.
    The owner's file is opened read-only and is never patched on disk.
    """
    def __init__(self, path):
        raw = Path(path).read_bytes()
        self.sha256 = hashlib.sha256(raw).hexdigest()
        if len(raw) != FILE_SIZE or self.sha256 != SHA256:
            raise ValueError(f"{path}: unsupported server.dll (size {len(raw)}, SHA-256 {self.sha256})")
        self.fingerprint = self.sha256
        off = struct.unpack_from('<I', raw, 0x3C)[0]
        self.opt = off + 24
        machine, n = struct.unpack_from('<HH', raw, off + 4)
        self.characteristics = struct.unpack_from('<H', raw, off + 22)[0]
        self.dll_characteristics = struct.unpack_from('<H', raw, self.opt + 70)[0]
        self.preferred_base = struct.unpack_from('<I', raw, self.opt + 28)[0]
        self.size = struct.unpack_from('<I', raw, self.opt + 56)[0]
        entry = struct.unpack_from('<I', raw, self.opt + 16)[0]
        if (raw[off:off + 4] != b'PE\0\0' or machine != 0x14C or
                struct.unpack_from('<H', raw, self.opt)[0] != 0x10B or
                self.characteristics != 0x210E or self.dll_characteristics != 0 or
                (self.preferred_base, self.size, entry) != (PREFERRED_BASE, IMAGE_SIZE, ENTRY_RVA)):
            raise ValueError('server PE metadata differs from profile')
        start = self.opt + struct.unpack_from('<H', raw, off + 20)[0]
        self.sections = [struct.unpack_from('<8sIIII', raw, start + i * 40) for i in range(n)]
        self.section_flags = [struct.unpack_from('<I', raw, start + i * 40 + 36)[0] for i in range(n)]
        self.dirs = [struct.unpack_from('<II', raw, self.opt + 96 + i * 8) for i in range(16)]
        if self.dirs[9] != (0, 0) or self.dirs[13] != (0, 0):
            raise ValueError('server TLS/delay imports require a new profile')
        self.base = BASE
        self.entry = self.base + entry
        if self.base + self.size > RESERVED_END:
            raise ValueError('server image exceeds reserved range')
        image = bytearray(self.size)
        headers = struct.unpack_from('<I', raw, self.opt + 60)[0]
        image[:headers] = raw[:headers]
        for _, vs, rva, rs, fileoff in self.sections:
            if rva + max(vs, rs) > self.size or fileoff + rs > len(raw):
                raise ValueError('server section outside image/file')
            image[rva:rva + rs] = raw[fileoff:fileoff + rs]
        self.relocations = []
        rva, size = self.dirs[5]
        pos, end = rva, rva + size
        self.relocation_blocks = self.relocation_padding = 0
        while pos < end:
            page, blocksize = struct.unpack_from('<II', image, pos)
            if blocksize < 8 or blocksize % 2 or pos + blocksize > end:
                raise ValueError('invalid server relocation block')
            self.relocation_blocks += 1
            for at in range(pos + 8, pos + blocksize, 2):
                item = struct.unpack_from('<H', image, at)[0]
                kind, target = item >> 12, page + (item & 4095)
                if kind == 0:
                    self.relocation_padding += 1
                elif kind == 3 and target + 4 <= self.size:
                    self.relocations.append(target)
                else:
                    raise ValueError(f'unsupported server relocation {kind} at {target:08X}')
            pos += blocksize
        if (self.relocation_blocks, len(self.relocations), self.relocation_padding) != (86, 5287, 43):
            raise ValueError('server relocation counts differ from profile')
        # Parse the entire directory before mutating the mapped image.
        for target in self.relocations:
            value = struct.unpack_from('<I', image, target)[0]
            struct.pack_into('<I', image, target, (value + self.base - self.preferred_base) & 0xFFFFFFFF)
        struct.pack_into('<I', image, self.opt + 28, self.base)
        self.data = bytes(image)
        self.imports = self._imports(self.opt)
        self.exports = self._exports()
        self.validate_tables()

    def _rva_off(self, rva):
        if not 0 <= rva < self.size:
            raise ValueError(f'server RVA outside image: {rva:08X}')
        return rva

    def read(self, addr, n):
        rva = addr - self.base
        if 0 <= rva and rva + n <= self.size:
            return self.data[rva:rva + n]
        return None

    def _exports(self):
        rva, size = self.dirs[0]
        _, _, _, _, _, ordinal, nf, nn, af, an, ao = struct.unpack_from('<IIHHIIIIIII', self.data, rva)
        if nf != 3 or nn != 3:
            raise ValueError('server requires exactly three export slots/names')
        exports = {}
        for j in range(nn):
            nr = struct.unpack_from('<I', self.data, an + j * 4)[0]
            ix = struct.unpack_from('<H', self.data, ao + j * 2)[0]
            target = struct.unpack_from('<I', self.data, af + ix * 4)[0]
            if ix >= nf or rva <= target < rva + size:
                raise ValueError('invalid/forwarded server export')
            name = self.data[nr:].split(b'\0', 1)[0].decode('ascii')
            exports[name] = (ordinal + ix, self.base + target)
        return exports

    def validate_tables(self):
        expected = {name: (ordinal, self.base + rva) for name, (ordinal, rva) in EXPORTS.items()}
        if self.exports != expected:
            raise ValueError('server exports differ from profile')
        if len(self.imports) != 129:
            raise ValueError('server requires 129 IAT cells')


def load_functions(pe):
    rvas = [int(line, 16) for line in (HERE / 'server_functions.tsv').read_text().splitlines()]
    if len(rvas) != FUNCTION_COUNT or rvas != sorted(set(rvas)):
        raise ValueError('invalid server function manifest count/order')
    text_lo, text_hi = 0x1000, 0x4CF85
    if not all(text_lo <= rva < text_hi for rva in rvas):
        raise ValueError('server function outside executable section')
    if not {ENTRY_RVA, *(rva for _, rva in EXPORTS.values())} <= set(rvas):
        raise ValueError('server manifest missing entry/export')
    starts = [pe.base + rva for rva in rvas]
    return {a: b for a, b in zip(starts, starts[1:] + [pe.base + text_hi])}


def emit_tables(pe, functions, out):
    """Only server-prefixed data symbols; function VAs cannot overlap speed's."""
    (out / 'funcs.h').write_text('#pragma once\n#include "runtime.h"\n' +
                               ''.join(f'void f_{a:08X}(Cpu *);\n' for a in functions))
    header = '''#pragma once
#include "runtime.h"
typedef struct { uint32_t slot; const char *name; } ServerImport;
typedef struct { const char *name; uint32_t ordinal, address; } ServerExport;
extern const uint32_t server_guest_func_addr[];
extern void (*const server_guest_func_ptr[])(Cpu *);
extern const unsigned server_guest_func_count;
extern const ServerImport server_imports[];
extern const unsigned server_import_count;
extern const ServerExport server_exports[];
extern const unsigned server_export_count;
extern const uint32_t server_image_base, server_image_size, server_entry_addr;
extern const uint32_t server_reserved_end, server_preferred_base;
extern const uint32_t server_relocation_rva[];
extern const unsigned server_relocation_count, server_file_size;
extern const char server_file_sha256[];
'''
    (out / 'server_tables.h').write_text(header)
    lines = ['#include "funcs.h"', '#include "server_tables.h"']
    lines += ['const uint32_t server_guest_func_addr[] = {' + ','.join(f'0x{a:08X}u' for a in functions) + '};',
              'void (*const server_guest_func_ptr[])(Cpu *) = {' + ','.join(f'f_{a:08X}' for a in functions) + '};',
              f'const unsigned server_guest_func_count = {len(functions)};',
              'const ServerImport server_imports[] = {']
    lines += [f'{{0x{slot:08X}u, {json.dumps(name)}}},' for slot, name in sorted(pe.imports.items())]
    lines += ['};', f'const unsigned server_import_count = {len(pe.imports)};',
              'const ServerExport server_exports[] = {']
    lines += [f'{{{json.dumps(name)}, {ordinal}u, 0x{address:08X}u}},'
              for name, (ordinal, address) in sorted(pe.exports.items())]
    lines += ['};', f'const unsigned server_export_count = {len(pe.exports)};']
    for name, value in [('image_base', pe.base), ('image_size', pe.size), ('entry_addr', pe.entry),
                        ('reserved_end', RESERVED_END), ('preferred_base', pe.preferred_base)]:
        lines.append(f'const uint32_t server_{name} = 0x{value:08X}u;')
    lines += ['const uint32_t server_relocation_rva[] = {' + ','.join(f'0x{r:X}u' for r in pe.relocations) + '};',
              f'const unsigned server_relocation_count = {len(pe.relocations)};',
              f'const unsigned server_file_size = {FILE_SIZE};',
              f'const char server_file_sha256[] = "{SHA256}";']
    (out / 'server_tables.c').write_text('\n'.join(lines) + '\n')


def check_reservation(speed_pe):
    for _, vs, rva, rs, _ in speed_pe.sections:
        lo, hi = speed_pe.base + rva, speed_pe.base + rva + max(vs, rs)
        if lo < RESERVED_END and BASE < hi:
            raise ValueError('speed image overlaps reserved server module range')


def build(path, jobs=4, out=None, speed_pe=None):
    out = Path(out) if out is not None else HERE / 'out/server'
    lib = out / 'server.a'
    # Missing input is optional, but must not leave a stale usable archive.
    lib.unlink(missing_ok=True)
    if not Path(path).exists():
        print(f'server: SKIP (DLL absent: {path})')
        return None
    pe = ServerPE(path)
    check_reservation(speed_pe or recomp.PE())
    functions = load_functions(pe)
    rc = recomp.Recompiler(pe=pe, functions=functions)
    rc.calls = True
    bodies, missing, decoded = [], set(), set()
    stubs = partial = 0
    for address, limit in functions.items():
        before = getattr(rc, 'partial', 0)
        code, why = rc.translate(address, limit)
        if code is None:
            stubs += 1
        else:
            partial += getattr(rc, 'partial', 0) - before
            decoded.update(rc.seen)
            missing.update(rc.callees - functions.keys())
            # Check actual emitted references too, including tail calls/switches.
            missing.update(int(a, 16) for a in re.findall(r'\bf_([0-9A-F]{8})\(', code)
                           if int(a, 16) not in functions)
            bodies.append(code)
    report = dict(functions=len(functions), translated=len(bodies), stubs=stubs, traps=partial,
                  instructions=len(decoded), missing_guest_references=sorted(missing),
                  base=pe.base, entry=pe.entry, image_size=pe.size, sha256=pe.sha256,
                  exports=pe.exports, imports=pe.imports, relocations=len(pe.relocations),
                  sections=[(name.decode().rstrip('\0'), vs, rva, rs, flags)
                            for (name, vs, rva, rs, _), flags in zip(pe.sections, pe.section_flags)])
    out.mkdir(parents=True, exist_ok=True)
    (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    if stubs or partial or missing:
        raise ValueError(f'server translation incomplete: stubs={stubs}, traps={partial}, missing={len(missing)}')
    emit_tables(pe, functions, out)
    files = []
    for k in range(0, len(bodies), 200):
        source = out / f'server_{k // 200:02d}.c'
        source.write_text('#include "funcs.h"\n' + '\n\n'.join(bodies[k:k + 200]))
        files.append(source)
    files.append(out / 'server_tables.c')

    def compile_one(source):
        obj = source.with_suffix('.o')
        proc = subprocess.run(['clang', '-arch', 'arm64', '-c', *recomp.CFLAGS,
                               *os.environ.get('RECOMP_CFLAGS', '').split(),
                               '-I', str(HERE), '-I', str(out), str(source), '-o', str(obj)],
                              capture_output=True, text=True)
        if proc.returncode:
            raise RuntimeError(f'{source.name}: compiler exit {proc.returncode}\n{proc.stderr[-2000:]}')
        return str(obj)

    with concurrent.futures.ThreadPoolExecutor(jobs) as executor:
        objects = list(executor.map(compile_one, files))
    pending = lib.with_suffix('.a.new')
    pending.unlink(missing_ok=True)
    try:
        subprocess.run(['ar', 'rcs', str(pending), *objects], check=True, capture_output=True)
        pending.replace(lib)
    finally:
        pending.unlink(missing_ok=True)
    print(f'server: functions={len(functions)}, stubs={stubs}, traps={partial}, '
          f'missing guest references={len(missing)}, exports={len(pe.exports)}, '
          f'IAT cells={len(pe.imports)}, relocations={len(pe.relocations)}, arm64 objects={len(objects)}')
    return report
