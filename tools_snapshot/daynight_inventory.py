#!/usr/bin/env python3
"""Read-only day/night source inventory; writes reports only to ignored scratch.

Plan: count envelopes before interpreting records, reuse the stream/solid parsers,
then measure stored vertex colours and list texture metadata. N means undecoded.
No rendered brightness, lamp-head placement, or artistic intent is inferred.

Important correction to the design: 135002 contains a 16-byte tree header and
48-byte spatial nodes (41/27/19 initially); 135003 contains 96-byte lights.
Evidence: LoaderLights 00501A10 assigns these separately; 00501E10 indexes lights
at 0x60, and 00651040 indexes tree nodes at 0x30. FUN_004FC020 reads light kind
at +4, enable at +7, colour at +12, position at +16 and radius at +28.
The shared version-4 layout is measured, but kind enum names remain N.

Vertex layouts reuse worldmerge.convert.EFFECTS / validate.STRIDES and the
x360_glass_uv1.solids parser. Only their classified world declarations contribute
to brightness: D3DCOLOR at +24, or +12 for the later minimal declaration.
Brightness = arithmetic mean of stored RGB / 255, before shader power, treatment
or textures; vertices are counted as stored, including LODs and repeated solids.
Section means are not averaged to obtain a city mean (vertices are weighted).
"""
import argparse
from collections import Counter, defaultdict
import csv
import json
import math
from pathlib import Path
import struct
import sys

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / 'worldmerge'))
from formats import Chunk, FormatError, chunks, require, u32
from merge import DEFAULT_MW, DEFAULT_CARBON, load_index, read_section
from audit import aligned_payload
from convert import EFFECTS
from validate import STRIDES
import texture_compare as textures
import x360_auxlights as aux
import x360_glass_uv1 as glass

SCRATCH = ROOT / '.metal-cache' / 'daynight-inventory'
LIGHT_PACK = 0x80135000
FLARE_PACK = 0x80135100
POST_NAME = ('streetlight', 'lightpost', 'lightpole', 'parklamp', 'ironlampstanding')


class Brightness:
    def __init__(self):
        self.hist = np.zeros(766, dtype=np.int64)

    def add(self, raw, stride, colour):
        require(len(raw) % stride == 0, 'vertex stride does not divide buffer')
        pixels = np.frombuffer(raw, np.uint8).reshape(-1, stride)
        levels = pixels[:, colour:colour + 3].sum(axis=1)
        self.hist += np.bincount(levels.astype(np.int64), minlength=766)

    def merge(self, other):
        self.hist += other.hist

    def report(self):
        n = int(self.hist.sum())
        if not n:
            return dict(vertices=0, mean='N', stddev='N', min='N', max='N', p05='N', p50='N', p95='N')
        x = np.arange(766) / 765
        mean = float(self.hist @ x / n)
        used = np.flatnonzero(self.hist)
        cumulative = self.hist.cumsum()
        result = dict(vertices=n, mean=mean,
                      stddev=math.sqrt(max(0, float(self.hist @ (x * x) / n) - mean * mean)),
                      min=float(x[used[0]]), max=float(x[used[-1]]))
        for key, q in [('p05', .05), ('p50', .5), ('p95', .95)]:
            result[key] = float(x[np.searchsorted(cumulative, math.ceil(q * n))])
        return result


def spread(points):
    if not points:
        return dict(count=0, min='N', max='N', mean='N', stddev='N')
    a = np.asarray(points, dtype=np.float64)
    require(a.ndim == 2 and a.shape[1] == 3 and np.isfinite(a).all(), 'invalid positions')
    return dict(count=len(points), min=a.min(axis=0).tolist(), max=a.max(axis=0).tolist(),
                mean=a.mean(axis=0).tolist(), stddev=a.std(axis=0).tolist())


def walk(items):
    """Envelope-only traversal; never search compressed payload for signatures."""
    for c in items:
        yield c
        if c.kind & 0x80000000:
            yield from walk(chunks(c.payload, c.offset + 8))


def stream_tops(path):
    """Bounded file reads, with envelope validation by the shared chunk parser."""
    with path.open('rb') as f:
        end = path.stat().st_size
        p = 0
        while p < end:
            header = f.read(8)
            require(len(header) == 8, 'short stream envelope')
            kind, size = struct.unpack('<II', header)
            require(size <= end - p - 8 and size <= 256 << 20, 'stream chunk outside file/budget')
            body = f.read(size)
            require(len(body) == size, 'short stream chunk')
            yield chunks(header + body, p)[0]
            p += size + 8


def light_pack(c, tree_stride=48):
    parts = chunks(c.payload, c.offset + 8)
    one = lambda k: aligned_payload(next(p for p in parts if p.kind == k))
    header, tree, lights = (one(k) for k in (0x135001, 0x135002, 0x135003))
    require(len(header) == 32 and struct.unpack_from('<H', header, 8)[0] == 4, 'light pack version/header')
    require(len(tree) >= 16 and tree_stride > 0 and (len(tree) - 16) % tree_stride == 0,
            f'tree record stride/count: {len(tree) - 16} bytes at stride {tree_stride} (retail 48)')
    count = (len(tree) - 16) // tree_stride
    require(tree_stride == 48 and struct.unpack_from('<H', tree, 8)[0] == count, 'tree header count differs')
    require(len(lights) == u32(header, 28) * 96, 'light record stride/header count')
    nodes = []
    for p in range(16, len(tree), 48):
        position = struct.unpack_from('<3f', tree, p)
        extent = struct.unpack_from('<3f', tree, p + 16)
        require(all(math.isfinite(x) for x in (*position, *extent)), 'nonfinite tree bounds')
        nodes.append(dict(center_xyz=position, half_extent_xyz=extent,
                          signed_child_count=struct.unpack_from('<h', tree, p + 14)[0],
                          kind='N: spatial node, not a light kind', colour='N: no colour field'))
    rows = []
    for p in range(0, len(lights), 96):
        b = lights[p:p + 96]
        position = struct.unpack_from('<3f', b, 16)
        require(all(math.isfinite(x) for x in position), 'nonfinite light position')
        rows.append(dict(hash=f'{u32(b):08x}', kind_code=b[4], kind_name='N',
                         mode_code=b[5], mode_name='N', byte6=b[6], enabled=b[7],
                         colour_word=f'{u32(b, 12):08x}', rgb8=list(b[12:15]),
                         position_xyz=position, outer_radius=struct.unpack_from('<f', b, 28)[0],
                         name=b[62:96].split(b'\0', 1)[0].decode('ascii'),
                         other_fields='N: +8 word, +32..+60 semantics, byte6, colour high byte'))
    return dict(section=u32(header, 12), tree_records=count, light_records=len(rows),
                tree_nodes=nodes, lights=rows)


def model_candidates(c):
    parts = chunks(c.payload, c.offset + 8)
    one = lambda k: next(p for p in parts if p.kind == k)
    header = aligned_payload(one(0x34101))
    require(len(header) in (60, 68), 'scenery header layout')
    extended = len(header) == 68
    width = 96 if extended else 64
    infos = one(0x34102).payload
    records = aligned_payload(one(0x34103))
    require(len(infos) % 72 == 0 and len(records) % width == 0, 'scenery table stride')
    result = []
    for p in range(0, len(records), width):
        model = struct.unpack_from('<h', records, p + (84 if extended else 62))[0]
        require(0 <= model < len(infos) // 72, 'scenery model index outside table')
        name = infos[model * 72:model * 72 + 24].split(b'\0', 1)[0].decode('ascii')
        if not any(token in name.lower() for token in POST_NAME):
            continue
        lo = struct.unpack_from('<3f', records, p)
        hi = struct.unpack_from('<3f', records, p + (16 if extended else 12))
        position = struct.unpack_from('<3f', records, p + 32)
        require(all(math.isfinite(v) for v in (*lo, *hi, *position)), 'nonfinite lamp instance')
        result.append(dict(section=u32(header, 12), model=name, model_index=model,
                           instance_index=p // width, origin_xyz=position, bounds=[lo, hi],
                           head_position='N', full_model_name='N: model table stores 24 bytes'))
    return u32(header, 12), result


def vertex_colours(raw, root, later):
    """Read the established solid-list parser, then validate native buffer groups."""
    # Validate bounds before the legacy metadata walker, which trusts its input.
    all_parts = list(walk([root]))
    headers = [p for p in all_parts if p.kind == 0x134011]
    for h in headers:
        require(u32(aligned_payload(h), 12) & 0xffff == 22, 'undecoded solid version')
    parsed = glass.solids(raw, root.offset, root.offset + 8 + len(root.payload))
    require(len(parsed) == len(headers), 'solid parser coverage differs')
    total, unknown = Brightness(), Counter()
    for solid in parsed:
        def data(item, align=16):
            off, size = item
            return aligned_payload(Chunk(0, raw[off + 8:off + 8 + size], off)) if align == 16 else buffer(item, align)
        def buffer(item, align):
            off, size = item
            c = Chunk(0, raw[off + 8:off + 8 + size], off)
            pad = -(off + 8) % align
            require(c.payload[:pad] == b'\x11' * pad, 'vertex alignment filler')
            return c.payload[pad:]
        mh = data(solid[0x134900])
        rb = data(solid[0x134b02])
        width, nv_offset = (144, 64) if later else (104, 60)
        require(len(mh) == (52 if later else 48), 'mesh header layout')
        count = u32(mh, 16)
        require(len(rb) == count * width, 'material record count')
        groups = []
        for p in range(0, len(rb), width):
            effect, nv = u32(rb, p + 48), u32(rb, p + nv_offset)
            if not groups or effect != groups[-1][0]:
                groups.append([effect, 0])
            groups[-1][1] += nv
        vbs = [(off, size) for cid, off, size in solid['seq'] if cid == 0x134b01]
        require(len(vbs) == len(groups) == u32(mh, 24), 'vertex/effect group count')
        for (effect, nv), vb in zip(groups, vbs):
            decoded = effect in (STRIDES if later else EFFECTS)
            if not decoded:
                unknown[f'effect_{effect}'] += nv
                continue
            stride = STRIDES[effect] if later else EFFECTS[effect][0]
            b = buffer(vb, 16 if later else 128)
            require(len(b) == stride * nv, f'effect {effect}: vertex count/stride differs')
            total.add(b, stride, 12 if later and effect == 6 else 24)
    return total, unknown


def sky_names(root, source):
    """Only actual TPK TextureInfo records count as texture presence."""
    result = []
    for c in walk([root]):
        if c.kind != 0xb3310000:
            continue
        hp = chunks(c.payload, c.offset + 8)
        get = lambda k: next((p.payload for p in hp if p.kind == k), None)
        header, rows = get(0x33310001), get(0x33310004)
        if header is None or rows is None:
            continue  # compressed TPK handled through the shared texture scanner
        version = u32(header)
        decoded = []
        if version == 5:
            require(len(rows) % 124 == 0, 'texture record stride')
            for p in range(0, len(rows), 124):
                info = textures.info(rows[p:p + 124], '<')
                require(info is not None, 'invalid texture info')
                hs, name, w, h, _ = info
                decoded.append((hs, name, w, h))
        elif version in (8, 9):
            p = 0
            while p < len(rows):
                require(p + 89 <= len(rows), 'short variable texture record')
                length = rows[p + 88]
                end = p + ((89 + length + 3) // 4 * 4)
                require(length and end <= len(rows) and b'\0' in rows[p + 89:p + 89 + length], 'texture name bounds')
                name = rows[p + 89:p + 89 + length].split(b'\0', 1)[0].decode('ascii')
                w, h = struct.unpack_from('<HH', rows, p + 40)
                decoded.append((u32(rows, p + 12), name, w, h))
                p = end
        else:
            raise FormatError(f'undecoded texture version {version}')
        for hs, name, w, h in decoded:
            if name.startswith('SKY_MIDDAY_A_') or name == 'SKY_NIGHT_CLOUDS_02':
                result.append(dict(name=name, hash=f'{hs:08x}', width=w, height=h, source=source))
    return result


def city_inventory(name, root, base, tree_stride=48):
    index, stream, _ = load_index(root, base)
    result = dict(city=name, base=base, index_sections=len(index.sections), scenery_sections=0,
                  light_packs=0, flare_packs=0, packs=[], sections=[], sky_textures=[],
                  lamp_model_candidates=[], brightness_unknown_vertices=Counter())
    total = Brightness()
    # Independently count the entire stream; the index and scenery counts differ.
    stream_counts = Counter(c.kind for c in stream_tops(stream))
    result['stream_counts'] = {f'{k:08x}': n for k, n in sorted(stream_counts.items())}
    for section in index.sections:
        raw = read_section(stream, section)
        row = dict(index_section=section.number, scenery_ids=[], light_packs=0, flare_packs=0,
                   lamp_model_candidates=0, lamp_models=Counter(), brightness_unknown_vertices=Counter())
        section_brightness = Brightness()
        for c in chunks(raw):
            if c.kind == LIGHT_PACK:
                pack = light_pack(c, tree_stride)
                pack['index_section'] = section.number
                result['packs'].append(pack)
                row['light_packs'] += 1
            elif c.kind == FLARE_PACK:
                row['flare_packs'] += 1
            elif c.kind == 0x80034100:
                number, candidates = model_candidates(c)
                row['scenery_ids'].append(number)
                row['lamp_model_candidates'] += len(candidates)
                row['lamp_models'].update(candidate['model'] for candidate in candidates)
                for candidate in candidates:
                    candidate['index_section'] = section.number
                result['lamp_model_candidates'].extend(candidates)
                result['scenery_sections'] += 1
            elif c.kind == 0x80134000:
                brightness, unknown = vertex_colours(raw, c, name == 'Palmont')
                section_brightness.merge(brightness)
                row['brightness_unknown_vertices'].update(unknown)
            elif c.kind == 0xb3300000:
                result['sky_textures'].extend(sky_names(c, f'TRACKS/STREAM{base}.BUN:section={section.number}'))
        total.merge(section_brightness)
        result['brightness_unknown_vertices'].update(row['brightness_unknown_vertices'])
        row['brightness'] = section_brightness.report()
        result['sections'].append(row)
        result['light_packs'] += row['light_packs']
        result['flare_packs'] += row['flare_packs']
    for kind, key in [(0x80034100, 'scenery_sections'), (LIGHT_PACK, 'light_packs'), (FLARE_PACK, 'flare_packs')]:
        require(result[key] == stream_counts[kind],
                f'index coverage for {key}: {result[key]} measured / {stream_counts[kind]} in full stream')
    result['brightness'] = total.report()
    result['tree_records'] = sum(p['tree_records'] for p in result['packs'])
    result['light_records'] = sum(p['light_records'] for p in result['packs'])
    result['light_position_spread'] = spread([r['position_xyz'] for p in result['packs'] for r in p['lights']])
    result['kind_codes'] = dict(Counter(r['kind_code'] for p in result['packs'] for r in p['lights']))
    # Dedup by name and original bounds, retaining section occurrences separately.
    result['lamp_model_occurrences'] = len(result['lamp_model_candidates'])
    result['lamp_model_unique_bounds'] = len({(r['model'], tuple(r['bounds'][0]), tuple(r['bounds'][1]))
                                             for r in result['lamp_model_candidates']})
    result['lamp_models'] = dict(Counter(r['model'] for r in result['lamp_model_candidates']))
    for path in sorted((root.parent / 'GLOBAL').glob('*')):
        if path.name.upper() not in ('GLOBALB.BUN', 'INGAMEA.BUN'):
            continue
        for c in stream_tops(path):
            result['sky_textures'].extend(sky_names(c, f'GLOBAL/{path.name}'))
        # A compressed TPK contains TextureInfo at each decoded blob's tail.
        if name == 'Rockport':
            for hs, value in textures.scan_file((path, root.parent)).items():
                if isinstance(hs, int) and (value[0].startswith('SKY_MIDDAY_A_') or value[0] == 'SKY_NIGHT_CLOUDS_02'):
                    n, w, h, _, source = value
                    record = dict(name=n, hash=f'{hs:08x}', width=w, height=h, source=source)
                    if record not in result['sky_textures']:
                        result['sky_textures'].append(record)
    result['sky_search_scope'] = f'complete STREAM{base}.BUN plus GLOBALB.BUN and InGameA.bun TextureInfo records'
    return result


def x360_inventory(root=aux.DEFAULT_X360):
    contexts = aux.extract_contexts(root / 'STREAML2RA_PERM.BUN')
    references = aux.x360_instances(root / 'STREAML2RA.BUN', contexts)
    used = defaultdict(list)
    for section, bbox, idx in references:
        if 0 <= idx < len(contexts):
            used[section].append(idx)
    sections = []
    for section, indices in sorted(used.items()):
        lights = [b for idx in sorted(set(indices)) for b in contexts[idx][1]]
        sections.append(dict(section=section, lit_instances=len(indices), contexts=sorted(set(indices)),
                             light_references=len(lights),
                             positions=spread([struct.unpack('<8f', b)[:3] for b in lights])))
    counts = Counter()
    groups = 0
    # Reuse both the compression-group validator and the existing full decoder.
    # No alternate JDLZ/HUFF implementation or signature scan is introduced.
    with (root / 'STREAML2RA.BUN').open('rb') as f:
        for group in aux.stream_groups(f):
            start = group[0][0]
            end = group[-1][0] + group[-1][2]
            require(end - start <= 256 << 20, 'compressed group exceeds inventory budget')
            f.seek(start)
            decoded = textures.unblock(f.read(end - start))
            for c in walk(chunks(decoded)):
                counts[c.kind] += 1
            groups += 1
    lights = [b for _, records in contexts for b in records]
    unique = set(lights)
    return dict(contexts=len(contexts), light_references=len(lights), unique_light_records=len(unique),
                unique_positions=len({b[:12] for b in lights}),
                positions=spread([struct.unpack('<8f', b)[:3] for b in lights]),
                lit_instances=sum(len(v) for v in used.values()), sections=sections,
                context_names=[n for n, _ in contexts], compression_groups=groups,
                light_packs=counts[LIGHT_PACK], flare_packs=counts[FLARE_PACK],
                scenery_chunks=counts[0x80034100],
                interpretation='Scenery influence lights in world space; physical street-lamp identity N. '
                               'Road/tunnel fill contexts are not lamp-post positions.',
                position_fields='position_xyz, radius, rgb, intensity: x360_auxlights / runtime/auxlights.h')


def check_baseline(cities):
    mw, carbon = cities
    require(mw['scenery_sections'] == 947, 'Rockport scenery section count must be 947')
    require((mw['light_packs'], mw['flare_packs'], carbon['light_packs']) == (0, 0, 444), 'pack counts differ from design')
    require([p['tree_records'] for p in carbon['packs'][:3]] == [41, 27, 19], 'first three tree counts differ')


def inventory(mw=DEFAULT_MW, carbon=DEFAULT_CARBON, x360=aux.DEFAULT_X360, tree_stride=48):
    cities = [city_inventory('Rockport', mw, 'L2RA', tree_stride),
              city_inventory('Palmont', carbon, 'L5RA', tree_stride)]
    check_baseline(cities)
    cities[0]['x360'] = x360_inventory(x360)
    cities[1]['x360'] = 'N: no Palmont Xbox source requested'
    return dict(schema=1, cities=cities,
                unknowns=['135002 light kind/colour: no such light fields (spatial nodes)',
                          '135003 kind/mode enum names, byte6, +8 word, +32..+60 semantics, colour high byte',
                          'scenery lamp-head positions, full names beyond 24-byte model table',
                          'Xbox scenery-light physical lamp identity',
                          'vertex declarations listed in each brightness_unknown_vertices map',
                          'rendered brightness, emissive/window intent, unlit Palmont colours'],
                risk2='retired for absence of placement sources: named Rockport lamp-post instances exist; '
                      'lamp-head offsets still require per-model work; Xbox fill lights alone are not proof of lamps')


def markdown(report):
    lines = ['# Day/night data inventory', '',
             '947 counts scenery chunks, not TrackIndex records (720). '
             '41/27/19 are 48-byte spatial nodes, not light records. '
             '135003 holds the 96-byte light records.', '',
             'Brightness is stored RGB mean / 255, vertex weighted, before shader processing; '
             'includes LODs/repeated solids. N = undecoded. Sky absence is within the stated search scope.', '']
    for city in report['cities']:
        b = city['brightness']
        lines.extend([f"## {city['city']}", '', '| Item | Measurement |', '| --- | --- |',
                      f"| Coverage | {city['index_sections']} index records; {city['scenery_sections']} scenery chunks |",
                      f"| Light / flare packs | {city['light_packs']} / {city['flare_packs']}; {city['tree_records']} spatial nodes; {city['light_records']} lights |",
                      f"| Lamp-post candidates | {city['lamp_model_occurrences']} occurrences; {city['lamp_model_unique_bounds']} distinct name+bounds; {len(city['lamp_models'])} model names; head offsets N |",
                      f"| Stored vertex brightness | n={b['vertices']}; mean={b['mean']:.6f}; std={b['stddev']:.6f}; range={b['min']:.6f}..{b['max']:.6f}; p05/p50/p95={b['p05']:.6f}/{b['p50']:.6f}/{b['p95']:.6f} |",
                      f"| Undecoded vertices | {dict(city['brightness_unknown_vertices'])} |",
                      f"| Sky names present | {', '.join(sorted({r['name'] for r in city['sky_textures']})) or 'none in search scope'} |",
                      f"| Sky search | {city['sky_search_scope']} |"])
        x = city['x360']
        if isinstance(x, dict):
            lines.append(f"| Xbox scenery lights | {x['contexts']} contexts; {x['light_references']} references; {x['unique_positions']} distinct positions; {x['lit_instances']} lit instances in {len(x['sections'])} sections; packs light/flare={x['light_packs']}/{x['flare_packs']}; XYZ range={x['positions']['min']}..{x['positions']['max']}; physical lamp identity N |")
        lines.append('')
    lines += ['## Undecoded fields', ''] + [f'- {n}' for n in report['unknowns']]
    lines += ['', f"Risk 2: {report['risk2']}.", '']
    return '\n'.join(lines)


def write_reports(report, output):
    output = output.resolve()
    require(output.is_relative_to(SCRATCH.resolve()), 'output must be inside ignored inventory scratch')
    output.mkdir(parents=True, exist_ok=True)
    (output / 'inventory.json').write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
    (output / 'inventory.md').write_text(markdown(report))
    for city in report['cities']:
        with (output / f"{city['city'].lower()}_sections.csv").open('w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['index_section', 'scenery_ids', 'light_packs', 'flare_packs', 'lamp_model_candidates', 'lamp_models',
                             'vertices', 'mean', 'stddev', 'min', 'max', 'p05', 'p50', 'p95', 'undecoded_vertices'])
            for row in city['sections']:
                b = row['brightness']
                writer.writerow([row['index_section'], ';'.join(map(str, row['scenery_ids'])),
                                 row['light_packs'], row['flare_packs'], row['lamp_model_candidates'],
                                 json.dumps(row['lamp_models'], sort_keys=True),
                                 *(b[k] for k in ('vertices', 'mean', 'stddev', 'min', 'max', 'p05', 'p50', 'p95')),
                                 json.dumps(row['brightness_unknown_vertices'], sort_keys=True)])


def missing_data(mw, carbon, x360):
    required = [mw / 'L2RA.BUN', mw / 'STREAML2RA.BUN', carbon / 'L5RA.BUN', carbon / 'STREAML5RA.BUN',
                mw.parent / 'GLOBAL/GLOBALB.BUN', carbon.parent / 'GLOBAL/GLOBALB.BUN',
                mw.parent / 'GLOBAL/InGameA.bun', carbon.parent / 'GLOBAL/InGameA.bun',
                x360 / 'STREAML2RA.BUN', x360 / 'STREAML2RA_PERM.BUN']
    return [p.name for p in required if not p.is_file()]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mw', type=Path, default=DEFAULT_MW)
    parser.add_argument('--carbon', type=Path, default=DEFAULT_CARBON)
    parser.add_argument('--x360', type=Path, default=aux.DEFAULT_X360)
    parser.add_argument('--output', type=Path, default=SCRATCH)
    args = parser.parse_args()
    missing = missing_data(args.mw, args.carbon, args.x360)
    if missing:
        print('SKIP: private game data absent: ' + ', '.join(missing))
        return 3
    try:
        require(args.output.resolve().is_relative_to(SCRATCH.resolve()), 'output must be inside ignored inventory scratch')
        report = inventory(args.mw, args.carbon, args.x360)
        write_reports(report, args.output)
    except (ValueError, OSError, StopIteration) as e:
        print(f'FAIL: {e}', file=sys.stderr)
        return 1
    print(markdown(report))
    print(f"Reports: {args.output.relative_to(ROOT) if args.output.is_absolute() else args.output}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
