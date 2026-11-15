"""Explicit, private PE build using build_all's translator/compiler pipeline.

All image-derived output must live in a gitignored directory. No private game
addresses, manifests or image bytes belong in this tool. No executable is run.
"""
import argparse
import collections
import hashlib
import json
import os
import re
from pathlib import Path
import struct
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_all
import recomp


def ignored_output(path):
    path = path.resolve()
    root = HERE.parent
    if not path.is_relative_to(root / "recomp" / "out"):
        raise ValueError("private output must be below recomp/out")
    checkout = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=root,
                              capture_output=True, text=True)
    if checkout.returncode == 0 and checkout.stdout.strip() == "true":
        subprocess.run(["git", "check-ignore", "-q", str(path / "private.c")],
                       cwd=root, check=True)
    path.mkdir(parents=True, exist_ok=True)
    return path


def image_functions(pe, manifest, *, data_starts=True, frame_callbacks=True):
    """Manifest starts plus conservative data callbacks, including frame prologues.

    The aligned pass reproduces the feasibility scan; canonical frame entries
    also admit unaligned CRT callbacks. MW manifests/patches are never applied.
    """
    _, vs, va, rs, raw = next(s for s in pe.sections if s[0].rstrip(b"\0") == b".text")
    lo, hi = pe.base + va, pe.base + va + vs
    starts = {}
    lines = Path(manifest).read_text().splitlines()
    metadata = dict(line[2:].split(' ', 1) for line in lines
                    if line.startswith(('# image-sha256 ', '# starts-')))
    compact = 'image-sha256' in metadata
    if compact and metadata['image-sha256'] != pe.sha256:
        raise ValueError('function list image SHA mismatch')
    listed = []
    for line in lines:
        if compact and (not line or line.startswith('#')):
            continue
        f = line.split("\t")
        if compact or (len(f) >= 4 and f[3].lower() == "false"):
            a = int(f[0], 16)
            listed.append(a)
            if lo <= a < hi:
                starts[a] = (f[1] if not compact or (len(f) > 1 and f[1]) else f'FUN_{a:08X}')
    if compact:
        digest = hashlib.sha256(''.join(f'{a:08x}\n' for a in listed).encode()).hexdigest()
        if digest != metadata['starts-sha256']:
            detail = ''
            if len(listed) == int(metadata['starts-count']) - 1:
                missing = int(metadata['starts-sum'], 16) - sum(listed)
                detail = f': missing function FUN_{missing:08X}'
            raise ValueError('function list starts digest mismatch' + detail)
    initial = len(starts)
    if data_starts:
        for name, _, _, size, offset in pe.sections:
            if name.rstrip(b"\0") not in (b".rdata", b".data"):
                continue
            blob = pe.data[offset:offset + size]
            for k in range(0, len(blob) - 3, 4):
                a = int.from_bytes(blob[k:k + 4], "little")
                aligned = a % 16 == 0 and pe.read(a - 1, 1) in (b"\xcc", b"\xc3", b"\x90") if lo <= a < hi else False
                # CRT initializer arrays can contain unaligned callbacks missing
                # from the disassembler manifest. Require the canonical complete
                # frame prologue instead of guessing that every pointer is code.
                frame = frame_callbacks and pe.read(a, 3) == b"\x55\x8b\xec" if lo <= a < hi else False
                if lo <= a < hi and (aligned or frame):
                    starts.setdefault(a, f"data_ref_{a:08X}")
    ordered = sorted(starts)
    print(f"manifest starts: {initial}; data-referenced starts: {len(starts) - initial}", flush=True)
    return [(a, ordered[k + 1] if k + 1 < len(ordered) else hi, starts[a])
            for k, a in enumerate(ordered)]


def write_imports(pe, out):
    off = struct.unpack_from("<I", pe.data, 0x3c)[0]
    entry = pe.base + struct.unpack_from("<I", pe.data, off + 24 + 16)[0]
    source = '#include <stdint.h>\n'
    source += f'const uint32_t speed_entry = 0x{entry:08X}u;\n'
    source += 'const struct ImportSlot { uint32_t slot; const char *name; } speed_imports[] = {\n'
    source += ''.join(f'    {{0x{a:08X}u, {json.dumps(n)}}},\n' for a, n in sorted(pe.imports.items()))
    source += '};\nconst unsigned speed_import_count = sizeof speed_imports / sizeof *speed_imports;\n'
    (out / 'imports.c').write_text(source)
    (out / 'image.json').write_text(json.dumps({'sha256': pe.sha256, 'exe': str(Path(recomp.EXE).resolve()),
                                              'imports': pe.imports}, indent=2) + '\n')


def audit_inline_traps(pe, out, prefix, discovery):
    """Decode residual traps and distinguish the conservative startup frontier."""
    result = {}
    inline = collections.Counter()
    reachable = set(discovery['crt_graph_reachable_starts'])
    trap_sites = []
    md = recomp.Cs(recomp.CS_ARCH_X86, recomp.CS_MODE_32)
    for path in out.glob(prefix + '_*.c'):
        source = path.read_text()
        inline.update(re.findall(r'untranslated\(c, 0x[0-9A-F]+u\); return; /\* (.*?) \*/', source))
        owner = None
        for match in re.finditer(r'void f_([0-9A-F]+)\(|untranslated\(c, 0x([0-9A-F]+)u\); return; /\* (.*?) \*/', source):
            if match[1]:
                owner = int(match[1], 16)
                continue
            site = int(match[2], 16)
            raw = pe.read(site, 16) or b''
            insn = next(md.disasm(raw, site), None)
            trap_sites.append({'site': site, 'owner': owner, 'kind': match[3],
                               'startup_reference_graph': owner in reachable,
                               'bytes': insn.bytes.hex() if insn else raw.hex(),
                               'decode': (insn.mnemonic + ' ' + insn.op_str) if insn else None})
    startup_traps = [row for row in trap_sites if row['startup_reference_graph']]
    (out / 'inline_traps.json').write_text(json.dumps({
        'sites': trap_sites, 'startup_sites': startup_traps,
        'scope': discovery['scope'],
        'opaque_indirect_sites': len(discovery['crt_graph_indirect_sites']),
    }, indent=2) + '\n')
    result['inline_traps'] = sum(inline.values())
    result['inline_traps_per_kind'] = dict(inline.most_common())
    result['startup_inline_traps'] = len(startup_traps)
    result['startup_inline_traps_per_kind'] = dict(collections.Counter(row['kind'] for row in startup_traps))
    return result


def measured_build(funcs, pe, out, prefix, **options):
    """Measure translation separately from compilation without changing MW."""
    original = recomp.Recompiler.translate
    translation_seconds = 0
    def measured(self, *args, **kwargs):
        nonlocal translation_seconds
        begin = time.perf_counter()
        try:
            return original(self, *args, **kwargs)
        finally:
            translation_seconds += time.perf_counter()-begin
    begin = time.perf_counter()
    recomp.Recompiler.translate = measured
    try:
        result = build_all.build_image(funcs, pe, str(out), prefix, mw_patches=False, game="carbon", **options)
    finally:
        recomp.Recompiler.translate = original
    result['translation_seconds'] = translation_seconds
    result['pipeline_seconds'] = time.perf_counter()-begin
    result['generated_c_bytes'] = sum(p.stat().st_size for p in out.glob(prefix+'_*.c'))
    return result


def audit_superset_translation(pe, out, prefix, discovery):
    added = {row['address'] for row in discovery.get('superset', {}).get('entries', [])}
    failures = []; existing_stubs = []; found = set()
    text_end = max(pe.base+va+vs for name,vs,va,_,_ in pe.sections if name.rstrip(b'\0') == b'.text')
    decoder = recomp.Recompiler(pe=pe, functions={row['address']:text_end for row in discovery.get('starts',[])},
                                mw_patches=False, import_thunks=True)
    decoder.calls = True
    def failed_decode(address):
        trace = {'site':address}
        original = decoder.md.disasm
        def traced(code, at, *args, **kwargs):
            trace['site'] = at
            return original(code, at, *args, **kwargs)
        decoder.md.disasm = traced
        decoder.tables = {}; decoder.ft_exits = {}; decoder.int3_stops = set()
        try:
            decoder.discover(address, text_end)
        except recomp.Unsupported as error:
            return {'decode_site':trace['site'], 'decode_reason':str(error),
                    'bytes':(pe.read(trace['site'],16) or b'').hex()}
        finally:
            decoder.md.disasm = original
        return {}
    for path in sorted(out.glob(prefix+'_*.c')):
        source = path.read_text()
        definitions = list(re.finditer(r'void f_([0-9A-F]{8})\(Cpu [^)]*\)\s*\{', source))
        for index, match in enumerate(definitions):
            address = int(match[1], 16)
            block = source[match.start():definitions[index+1].start() if index+1 < len(definitions) else len(source)]
            stub = re.search(r'untranslated\(c, 0x[0-9A-F]+u\); \} /\* (.*?) \*/', block)
            if address not in added:
                if stub:existing_stubs.append({'address': address, 'reason': stub[1]})
                continue
            found.add(address)
            if stub:
                failures.append({'address': address, 'kind': 'whole_function', 'reason': stub[1], 'unit': path.name,
                                 **(failed_decode(address) if stub[1].endswith('undecodable') else {})})
            else:
                for trap in re.finditer(r'untranslated\(c, 0x([0-9A-F]+)u\); return; /\* (.*?) \*/', block):
                    site = int(trap[1], 16)
                    failures.append({'address': address, 'kind': 'inline_trap', 'site': site,
                                     'reason': trap[2], 'bytes': (pe.read(site,16) or b'').hex(), 'unit': path.name})
    assert found == added, 'superset entry missing from generated C'
    failed = {row['address'] for row in failures}
    report = {'new_entries': len(added), 'fully_translated': len(added-failed),
              'entries_with_failures': len(failed), 'failures': failures,
              'existing_whole_function_stubs': existing_stubs}
    (out/'superset_translation.json').write_text(json.dumps(report,indent=2)+'\n')
    lines = [f"new entries={len(added)} fully translated={len(added-failed)} incomplete={len(failed)}"]
    for row in failures:
        site = row.get('site', row.get('decode_site', row['address']))
        lines.append(f"{row['address']:08X} {row['kind']} at {site:08X}: {row['reason']} bytes={row.get('bytes','')} unit={row['unit']}")
    lines.append('Existing whole-function stubs (not superset additions):')
    lines.extend(f"{row['address']:08X} {row['reason']}" for row in existing_stubs)
    (out/'superset_translation_failures.txt').write_text('\n'.join(lines)+'\n')
    return {key: report[key] for key in ('new_entries','fully_translated','entries_with_failures')}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--exe', required=True)
    ap.add_argument('--functions', default=HERE / 'functions_nfsc.tsv')
    ap.add_argument('--out', required=True)
    ap.add_argument('--prefix', default='image')
    ap.add_argument('--jobs', type=int, default=os.cpu_count())
    ap.add_argument('--translate-only', action='store_true')
    ap.add_argument('--hook-registry', type=Path, default=HERE / 'private_runtime' / 'hooks',
                    help='private feature manifests (default: private_runtime/hooks)')
    ap.add_argument('--no-superset', action='store_true', help='reference build without the private pointer superset')
    args = ap.parse_args()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", args.prefix):
        ap.error("--prefix must be a C identifier, without path separators")
    out = ignored_output(Path(args.out))
    recomp.EXE = args.exe
    digest = hashlib.sha256(Path(args.exe).read_bytes()).hexdigest()
    pe = recomp.PE(args.exe, expected_sha256=digest)
    funcs = image_functions(pe, args.functions)
    from private_discovery import complete
    funcs,discovery = complete(pe,funcs,superset=not args.no_superset)
    (out / 'discovery.json').write_text(json.dumps(discovery,indent=2)+'\n')
    print(f"static closure added: {discovery['added']}; functions: {len(funcs)}",flush=True)
    write_imports(pe, out)
    if 'superset' in discovery:
        (out/'superset.json').write_text(json.dumps(discovery['superset'],indent=2)+'\n')
        print('private superset:',discovery['superset']['added'],'entries;',round(discovery['superset']['seconds'],3),'seconds',flush=True)
    result = measured_build(funcs, pe, out, args.prefix, jobs=args.jobs, compile=not args.translate_only,
                            hook_registry=args.hook_registry)
    result.update(audit_inline_traps(pe, out, args.prefix, discovery))
    result['superset_translation'] = audit_superset_translation(pe, out, args.prefix, discovery)
    result['image_sha256'] = pe.sha256
    if not args.translate_only and not result.get('compile_errors'):
        result['archive_sha256'] = hashlib.sha256((out / (args.prefix + '.a')).read_bytes()).hexdigest()
    errors = collections.Counter()
    for path in out.glob('*.c.log'):
        errors.update(line.split('error:', 1)[1].strip() for line in path.read_text().splitlines() if 'error:' in line)
    result['compiler_errors_per_kind'] = dict(errors)
    (out / 'build.json').write_text(json.dumps(result, indent=2) + '\n')
    print('private build metrics:',json.dumps({key:result[key] for key in ('translation_seconds','pipeline_seconds','generated_c_bytes','superset_translation')}),flush=True)
    if result.get('compile_errors'):raise SystemExit(1)


if __name__ == '__main__':
    main()
