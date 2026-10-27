#!/usr/bin/env python3
"""Offline L2RA auxiliary-light import; never opens a game asset for writing.

Run with the project Python: python -I tools_snapshot/x360_auxlights.py --x360-tracks X --pc-tracks P [--output T]
(tools/gen_tables.py does, with the roots manifest's folders; the defaults are the legacy owner layout).
Uses the same texture_compare JDLZ/HUFF wrappers as xstream.py, but reads bounded
blocks and retains only one compressed section's index at a time. Scenery
containers are located in decoded blocks, then walked and validated. Require all
source context indices to be accounted for, so missing metadata fails closed.
"""
import argparse
import collections
import hashlib
import ctypes
import importlib.util
import math
from pathlib import Path
import struct
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT.parent
DEFAULT_X360 = ASSETS / "x360/files/TRACKS"
DEFAULT_PC = ASSETS / "Need For Speed Most Wanted Black Edition/TRACKS"
DEFAULT_TABLE = ROOT / "runtime/gen/auxlights_data.inc"
PC_EXE = DEFAULT_PC.parent / "speed.exe"   # the HUFF leaf's PE data; import_data sets it from --pc-tracks
MAX_READ = 32 << 20
MAGIC = 0x55441122
# SHA-256 of the first context's first light (8 floats): identifies the retail X360 stream without holding its bytes
SENTINEL_LIGHT_SHA256 = "f4b932fd7cb792d87f5e86635ce8ad8f372f83505a381b6242fbbd4238f70cc8"


def exact_read(f, offset, size):
    if not 0 <= size <= MAX_READ:
        raise ValueError(f"unbounded read: {size}")
    f.seek(offset)
    data = f.read(size)
    if len(data) != size:
        raise ValueError(f"short read at {offset:#x}")
    return data


def chunks(read, start, end):
    while start + 8 <= end:
        kind, size = struct.unpack("<II", read(start, 8))
        stop = start + 8 + size
        if stop > end:
            raise ValueError(f"chunk {kind:#x} exceeds parent at {start:#x}")
        yield kind, start + 8, stop
        start = stop
    if start != end:
        raise ValueError("trailing partial chunk")


def find_container(f, wanted):
    read = lambda p, n: exact_read(f, p, n)
    def walk(a, b):
        for kind, p, end in chunks(read, a, b):
            if kind == wanted:
                yield p, end
            elif kind & 0x80000000:
                yield from walk(p, end)
    yield from walk(0, f.seek(0, 2))


def extract_contexts(path):
    contexts = {}
    with path.open("rb") as f:
        containers = list(find_container(f, 0x80034115))
        if len(containers) != 1:
            raise ValueError(f"expected one light container, got {len(containers)}")
        begin, end = containers[0]
        for kind, p, stop in chunks(lambda p, n: exact_read(f, p, n), begin, end):
            if kind != 0x34118:
                continue
            p = (p + 15) & ~15
            header = exact_read(f, p, 48)
            typ, = struct.unpack_from("<I", header)
            name = header[4:38].split(b"\0", 1)[0].decode("ascii")
            index, = struct.unpack_from("<h", header, 38)
            count, = struct.unpack_from("<I", header, 44)
            if typ != 1 or index in contexts or index < 0 or not 0 < count <= 64 or p + 48 + count * 64 != stop:
                raise ValueError(f"invalid context at {p:#x}")
            lights = tuple(exact_read(f, p + 48 + i * 64, 32) for i in range(count))
            if not all(math.isfinite(v) for light in lights for v in struct.unpack("<8f", light)):
                raise ValueError("nonfinite light")
            contexts[index] = (name, lights)
    if sorted(contexts) != list(range(len(contexts))):
        raise ValueError("noncontiguous context indices")
    result = [contexts[i] for i in range(len(contexts))]
    check_sentinel(result)
    return result


def check_sentinel(contexts):
    if len(contexts) != 1231 or sum(len(lights) for _, lights in contexts) != 2997:
        raise ValueError("sentinel context/light totals differ")
    name, lights = contexts[0]
    if name != "TRN_CP_IT_Road_A_14_AUX07_CHOP_R3" or len(lights) != 4 or hashlib.sha256(lights[0]).hexdigest() != SENTINEL_LIGHT_SHA256:
        raise ValueError("sentinel first context/light differs")


def scenery(read, begin, end):
    section = None
    records = []
    for kind, p, stop in chunks(read, begin, end):
        if kind == 0x34101:
            p = (p + 15) & ~15
            section, = struct.unpack("<I", read(p + 12, 4))
        elif kind == 0x34103:
            p = (p + 15) & ~15
            if section is None or (stop - p) % 64:
                raise ValueError("invalid scenery instance array")
            for offset in range(p, stop, 64):
                data = read(offset, 64)
                bbox = data[:24]
                if not all(math.isfinite(v) for v in struct.unpack("<6f", bbox)):
                    raise ValueError("nonfinite bbox")
                records.append((section, bbox, struct.unpack_from("<h", data, 30)[0]))
    if section is None:
        raise ValueError("scenery has no section header")
    return records


def pc_instances(path):
    result = []
    with path.open("rb") as f:
        for begin, end in find_container(f, 0x80034100):
            result.extend(scenery(lambda p, n: exact_read(f, p, n), begin, end))
    return result


class RecompiledHuff:
    """Run xstream's original PC HUFF leaf without Unicorn's host CPU probe.

    Unicorn init_cache_info faults on CTR_EL0 on this host before decoding. The
    existing static recompiler provides the exact same 006500F0/0064DCA0 routines;
    compile just those two leaves as an offline library, with their PE data.
    No game entry point, hooks, imports, or runtime executable are involved.
    """
    def __init__(self):
        self.scratch = tempfile.TemporaryDirectory(prefix='auxlights-huff-')
        scratch = Path(self.scratch.name)
        functions = []
        active = False
        with (ROOT / 'recomp/out/speed_32.c').open() as f:
            for line in f:
                if line.startswith('void f_'):
                    active = line.startswith(('void f_0064DCA0(', 'void f_006500F0('))
                if active:
                    functions.append(line)
        if sum(line.startswith('void f_') for line in functions) != 2:
            raise ValueError('recompiled HUFF leaf functions not found')
        helper = '''
#include "runtime.h"
#include <stdlib.h>
uint8_t *G;
void *aux_huff_init(void) { G = calloc(1, 128u << 20); return G; }
void aux_huff_free(void) { free(G); G = NULL; }
'''+ ''.join(functions) + '''
int aux_huff_decode(const uint8_t *src, size_t size, uint8_t *out, size_t usize) {
    if (size > (32u << 20) || usize > (32u << 20)) return -1;
    memcpy(G + 0x1000000, src, size);
    memset(G + 0x1000000 + size, 0, 64);
    Cpu c = {0}; c.esp = 0x200000;
    M32(c.esp) = 0; M32(c.esp + 4) = 0x1000000; M32(c.esp + 8) = 0x4000000;
    f_006500F0(&c);
    if (c.eax != usize) return -2;
    memcpy(out, G + 0x4000000, usize); return 0;
}
'''
        source = scratch / 'huff.c'
        source.write_text(helper)
        build = subprocess.run(['clang', '-O2', '-w', '-dynamiclib', '-I', str(ROOT / 'recomp'),
                                str(source), '-o', str(scratch / 'huff.dylib')], capture_output=True, text=True)
        if build.returncode:
            raise RuntimeError('offline HUFF build failed: ' + build.stderr[-3000:])
        self.lib = ctypes.CDLL(str(scratch / 'huff.dylib'))
        self.lib.aux_huff_init.restype = ctypes.c_void_p
        memory = self.lib.aux_huff_init()
        if not memory:
            raise MemoryError('HUFF guest data allocation')
        with PC_EXE.open('rb') as f:
            pe = struct.unpack_from('<I', exact_read(f, 0x3c, 4))[0]
            header = exact_read(f, pe, 24)
            count = struct.unpack_from('<H', header, 6)[0]
            table = pe + 24 + struct.unpack_from('<H', header, 20)[0]
            for i in range(count):
                _, vsize, va, rawsize, raw = struct.unpack_from('<8sIIII', exact_read(f, table + i * 40, 40))
                if 0x400000 + va + max(vsize, rawsize) > 0x1000000:
                    raise ValueError('PE section outside HUFF image area')
                ctypes.memmove(memory + 0x400000 + va, exact_read(f, raw, rawsize), rawsize)
        self.lib.aux_huff_decode.argtypes = (ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t)

    def __call__(self, data, offset=0):
        usize, csize = struct.unpack_from('<II', data, offset + 8)
        data = data[offset:offset + 16 + csize + 64]   # the block (+ the decoder's read-ahead), not the rest of a stream
        if data[:5] != b'HUFF\x01' or usize > MAX_READ or csize + 16 > len(data):
            raise ValueError('invalid HUFF block')
        output = ctypes.create_string_buffer(usize)
        if self.lib.aux_huff_decode(data, len(data), output, usize):
            raise ValueError('recompiled HUFF returned a different size')
        return output.raw

    def close(self):
        if getattr(self, 'lib', None):
            self.lib.aux_huff_free()
            self.lib = None
        self.scratch.cleanup()

    def __del__(self):
        self.close()


def decoders(backend):
    # xstream.py imports these exact functions; importing xstream itself would
    # execute its CLI and read the entire stream. Keep that plumbing out of here.
    path = ROOT / "tools_snapshot/texture_compare.py"
    spec = importlib.util.spec_from_file_location("aux_texture_compare", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if backend == 'native':
        return module.jdlz, module.huff
    if backend != 'recompiled':
        raise ValueError('HUFF backend must be native or recompiled (no emulator)')
    class NativeExecutor:
        native = None
        def huff_decode(self, data, offset=0):
            if self.native is None:
                self.native = RecompiledHuff()
            return self.native(data, offset)
    module._emu = NativeExecutor()
    return module.jdlz, module.huff


def stream_groups(f):
    """Index one section's compressed blocks, including destination reordering."""
    size = f.seek(0, 2)
    pos, group = 0, []
    while pos + 24 <= size:
        header = exact_read(f, pos, 24)
        magic, usize, bsize, dst, rel, _ = struct.unpack("<6I", header)
        if magic != MAGIC:
            # The gaps are ordinary padding chunks, never search arbitrary bytes.
            kind, length = struct.unpack_from("<II", header)
            if kind != 0 or pos + 8 + length > size:
                raise ValueError(f"unexpected stream gap at {pos:#x}")
            pos += 8 + length
            continue
        if bsize < 24 or bsize > MAX_READ or usize > MAX_READ or pos + bsize > size:
            raise ValueError("invalid compression block")
        if rel == 0 and group:
            yield group
            group = []
        if rel and (not group or pos - group[0][0] != rel):
            raise ValueError("invalid block relative offset")
        group.append((pos, usize, bsize, dst))
        pos += bsize
    if group:
        yield group


def x360_instances(path, contexts, backend='native'):
    jdlz, huff = decoders(backend)
    result = []
    with path.open("rb") as f:
        for group in stream_groups(f):
            cache = {}
            def decode(block):
                pos, usize, bsize, dst = block
                if pos not in cache:
                    packed = exact_read(f, pos + 24, bsize - 24)
                    data = jdlz(packed) if packed[:4] == b"JDLZ" else huff(packed, 0) if packed[:4] == b"HUFF" else packed[:usize]
                    if len(data) != usize:
                        raise ValueError("decoded size mismatch")
                    cache[pos] = data
                return cache[pos]
            ordered = sorted(group, key=lambda b: b[3])
            total = 0
            for _, usize, _, dst in ordered:
                if dst != total:
                    raise ValueError("missing/overlapping destination blocks")
                total += usize
            def read(p, n):
                out = bytearray()
                for block in ordered:
                    _, usize, _, dst = block
                    if p < dst + usize and p + n > dst:
                        a, b = max(p, dst), min(p + n, dst + usize)
                        out.extend(decode(block)[a - dst:b - dst])
                if len(out) != n:
                    raise ValueError("scenery exceeds decoded section")
                return bytes(out)
            seen = set()
            # Scenery metadata can be JDLZ or HUFF. Scan every decoded byte,
            # including a container signature split across destination blocks.
            # Candidate headers must have the real first child 34101, followed
            # by a complete valid child chunk walk.
            tail = b''
            for block in ordered:
                data = decode(block)
                scan = tail + data
                start = 0
                while True:
                    hit = scan.find(b"\x00\x41\x03\x80", start)
                    if hit < 0:
                        break
                    start = hit + 4
                    p = block[3] - len(tail) + hit
                    if p in seen or p + 16 > total:
                        continue
                    kind, length, child, _ = struct.unpack("<4I", read(p, 16))
                    if child != 0x34101 or length < 16 or p + 8 + length > total:
                        continue
                    seen.add(p)
                    result.extend(scenery(read, p + 8, p + 8 + length))
                tail = data[-3:]
                # Keep only metadata needed by a crossing scenery container;
                # large geometry sections otherwise retain no decoded payload.
                cache.clear()
    used = {idx for _, _, idx in result if 0 <= idx < len(contexts)}
    if used != set(range(len(contexts))):
        raise ValueError(f"incomplete source context coverage: {len(used)}/{len(contexts)}")
    return result


def match_instances(contexts, source, pc):
    src = collections.defaultdict(list)
    dst = collections.Counter((s, b) for s, b, _ in pc)
    invalid = 0
    for section, bbox, index in source:
        if index < -1 or index >= len(contexts):
            invalid += 1
        src[section, bbox].append(index)
    rows = []
    stats = dict(contexts=len(contexts), lights=sum(len(l) for _, l in contexts),
                 stored_lights=sum(min(len(l), 8) for _, l in contexts),
                 truncated_contexts=sum(len(l) > 8 for _, l in contexts),
                 lit_instances=0, matched=0, unmatched=0, collisions=0, invalid=invalid,
                 pc_instances=len(pc), x360_instances=len(source))
    for key, indices in sorted(src.items()):
        lit = [i for i in indices if 0 <= i < len(contexts)]
        if not lit:
            continue
        stats['lit_instances'] += len(lit)
        if key not in dst:
            stats['unmatched'] += len(lit)
            continue
        # Include unlit duplicates: mapping such a key would illuminate objects
        # whose source has no context. Names are part of context identity.
        identities = {contexts[i] if 0 <= i < len(contexts) else None for i in indices}
        if len(identities) != 1:
            stats['collisions'] += len(lit)
            continue
        stats['matched'] += len(lit)
        rows.append((*key, min(lit)))
    stats['rows'] = len(rows)
    return rows, stats


def render_table(contexts, rows, stats):
    lines = ['/* Generated by tools_snapshot/x360_auxlights.py; do not edit. */']
    lines.extend(f'#define AUX_DATA_{k.upper()} {v}u' for k, v in stats.items())
    lines.append('static const AuxLightContext aux_contexts[] = {')
    for name, lights in contexts:
        # C hexadecimal floats preserve every little-endian source float bit.
        lines.append(f'    {{"{name}", {min(len(lights), 8)}, {len(lights)}, {{')
        for light in lights[:8]:
            v = [float(x).hex() + 'f' for x in struct.unpack('<8f', light)]
            lines.append('        {{' + ','.join(v[:3]) + '},' + v[3] + ',{' + ','.join(v[4:7]) + '},' + v[7] + '},')
        lines.append('    }},')
    lines.append('};\nstatic const AuxLightKey aux_keys[] = {')
    for section, bbox, index in rows:
        lines.append(f'    {{{section}u,{{' + ','.join(f'0x{b:02x}' for b in bbox) + f'}},{index}u}},')
    lines.append('};\n')
    return '\n'.join(lines)


def import_data(x360=DEFAULT_X360, pc=DEFAULT_PC, backend='native'):
    global PC_EXE
    PC_EXE = Path(pc).parent / 'speed.exe'
    contexts = extract_contexts(x360 / 'STREAML2RA_PERM.BUN')
    source = x360_instances(x360 / 'STREAML2RA.BUN', contexts, backend)
    target = pc_instances(pc / 'STREAML2RA.BUN')
    rows, stats = match_instances(contexts, source, target)
    return contexts, rows, stats


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--x360-tracks', type=Path, default=DEFAULT_X360)
    parser.add_argument('--pc-tracks', type=Path, default=DEFAULT_PC)
    parser.add_argument('--output', type=Path, default=DEFAULT_TABLE)
    parser.add_argument('--huff-backend', choices=('native', 'recompiled'), default='native')
    args = parser.parse_args()
    contexts, rows, stats = import_data(args.x360_tracks, args.pc_tracks, args.huff_backend)
    text = render_table(contexts, rows, stats)
    if len(text.encode()) >= 1_000_000:
        raise ValueError('generated table exceeds 1 MB')
    args.output.write_text(text)
    print(' '.join(f'{k}={v}' for k, v in stats.items()))
    print(f'table={args.output} bytes={len(text.encode())}')


if __name__ == '__main__':
    main()
