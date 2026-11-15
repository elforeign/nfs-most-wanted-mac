#!/usr/bin/env python3
"""J1 offline inventory proof; private assets absent => exit 3.

Baseline: Rockport 0/0 packs, 947 scenery chunks (720 index records); Palmont
444 packs, first tree counts 41/27/19 at stride 48. Deliberate failures:
--mutate stride47: stride 47 must fail (48 passes); --mutate drop-section:
946 scenery chunks must fail (947 passes). Both mutations run the real reader.
The independent oracle derives pack counts and record counts from chunk lengths,
without any inventory parsing/counting helpers. No game or source writes.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / 'worldmerge'))
from formats import Chunk, FormatError, TrackIndex, chunks
from merge import DEFAULT_MW, DEFAULT_CARBON
import daynight_inventory as inv


def expect(ok, message):
    if not ok:
        raise AssertionError(message)


def rejected(action, message):
    try:
        action()
    except (ValueError, AssertionError):
        return
    raise AssertionError(message)


def independent(root, base):
    """Use only shared envelopes/Section/TrackIndex, not the inventory reader."""
    index = TrackIndex((root / f'{base}.BUN').read_bytes())
    source = root / f'STREAM{base}.BUN'
    index.validate(source.stat().st_size)
    lengths, light_counts, scenery_ids, pack_sections = [], [], [], []
    flares = 0
    per_section = {}
    with source.open('rb') as f:
        for section in index.sections:
            f.seek(section.offset)
            raw = f.read(section.size)
            expect(len(raw) == section.size, 'independent short section read')
            scene_count = 0
            for c in chunks(raw):
                if c.kind == 0x80034100:
                    scene_count += 1
                    head = next(x for x in chunks(c.payload, c.offset + 8) if x.kind == 0x34101)
                    pad = -(head.offset + 8) % 16
                    scenery_ids.append(struct.unpack_from('<I', head.payload, pad + 12)[0])
                elif c.kind == 0x80135100:
                    flares += 1
                elif c.kind == 0x80135000:
                    parts = chunks(c.payload, c.offset + 8)
                    tree = next(p for p in parts if p.kind == 0x135002)
                    padding = -(tree.offset + 8) % 16
                    size = len(tree.payload) - padding - 16
                    expect(size >= 0 and size % 48 == 0, 'independent tree stride')
                    n = size // 48
                    expect(n == struct.unpack_from('<H', tree.payload, padding + 8)[0], 'independent tree header count')
                    lengths.append(n)
                    light = next(p for p in parts if p.kind == 0x135003)
                    size = len(light.payload) - (-(light.offset + 8) % 16)
                    expect(size % 96 == 0, 'independent light stride')
                    light_counts.append(size // 96)
                    pack_sections.append(section.number)
            per_section[section.number] = scene_count
    return dict(index_count=len(index.sections), trees=lengths, lights=light_counts,
                pack_sections=pack_sections, scenery=scenery_ids, flares=flares, per_section=per_section)


def small_checks(report):
    # Asymmetric channels and unequal sample sizes expose offset/alpha and
    # section-weighting bugs. Histograms are independently calculated scalars.
    a, b = inv.Brightness(), inv.Brightness()
    a.add(struct.pack('<3fI2f', 1, 2, 3, 0xff0000ff, 0, 0), 24, 12)
    b.add(struct.pack('<6fI2f', 0, 0, 0, 0, 0, 1, 0xff000000, 0, 0) * 3, 36, 24)
    a.merge(b)
    expect(a.report()['vertices'] == 4 and abs(a.report()['mean'] - 1 / 12) < 1e-12,
           'RGB arithmetic mean / vertex weighting')
    rejected(lambda: a.add(bytes(23), 24, 12), 'partial vertex accepted')
    rejected(lambda: inv.spread([(1, float('nan'), 3)]), 'nonfinite position accepted')
    expect(inv.spread([(1, 2, 3), (3, 4, 5)])['stddev'] == [1, 1, 1], 'XYZ spread')

    source = report['cities'][1]['packs'][0]
    expect(source['tree_records'] == 41 and source['light_records'] == 32, 'tree/light distinction')
    expect(source['tree_nodes'][0]['kind'].startswith('N'), 'tree interpreted as lamp kind')
    expect(source['lights'][0]['position_xyz'] != source['tree_nodes'][0]['center_xyz'],
           'tree centre substituted for light position')
    # Exercise the two count guards in the ordinary offline suite as well as
    # the separately runnable reader mutations documented above.
    with (DEFAULT_CARBON / 'STREAML5RA.BUN').open('rb') as f:
        carbon_index = TrackIndex((DEFAULT_CARBON / 'L5RA.BUN').read_bytes())
        section = next(s for s in carbon_index.sections if s.number == source['index_section'])
        f.seek(section.offset)
        raw = f.read(section.size)
    pack = next(c for c in chunks(raw) if c.kind == 0x80135000)
    rejected(lambda: inv.light_pack(pack, tree_stride=47), 'stride 47 passed the count check')
    actual_light = next(c for c in chunks(pack.payload, pack.offset + 8) if c.kind == 0x135003)
    p = -(actual_light.offset + 8) % 16
    expect(tuple(source['lights'][0]['position_xyz']) == struct.unpack_from('<3f', actual_light.payload, p + 16),
           'light position field')
    expect(source['lights'][0]['kind_code'] == actual_light.payload[p + 4]
           and source['lights'][0]['rgb8'] == list(actual_light.payload[p + 12:p + 15]), 'kind/colour fields')
    dropped = [dict(c) for c in report['cities']]
    dropped[0]['scenery_sections'] = 946
    rejected(lambda: inv.check_baseline(dropped), '946 passed the 947 section count check')

    # Derive the lamp count on S04 from native model indices and names.
    index = TrackIndex((DEFAULT_MW / 'L2RA.BUN').read_bytes())
    section = next(s for s in index.sections if s.number == 1904)
    with (DEFAULT_MW / 'STREAML2RA.BUN').open('rb') as f:
        f.seek(section.offset)
        raw = f.read(section.size)
    scene = next(c for c in chunks(raw) if c.kind == 0x80034100)
    parts = chunks(scene.payload, scene.offset + 8)
    models = next(c.payload for c in parts if c.kind == 0x34102)
    records = next(c for c in parts if c.kind == 0x34103)
    start = -(records.offset + 8) % 16
    names = []
    for p in range(start, len(records.payload), 64):
        i = struct.unpack_from('<h', records.payload, p + 62)[0]
        name = models[i * 72:i * 72 + 24].split(b'\0')[0].decode('ascii')
        if any(s in name.lower() for s in ('streetlight', 'lightpost', 'lightpole', 'parklamp', 'ironlampstanding')):
            names.append(name)
    actual = [r['model'] for r in report['cities'][0]['lamp_model_candidates'] if r['index_section'] == 1904]
    expect(Counter(actual) == Counter(names) and len(names) == 7, 'S04 lamp model instance coverage')
    expect(not any('traffic' in r['model'].lower() for r in report['cities'][0]['lamp_model_candidates']),
           'traffic signals classified as lamp posts')

    # Re-read emitted scalar tables, and require the output restriction before
    # any directory creation. All artifacts remain in ignored scratch.
    inv.SCRATCH.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=inv.SCRATCH, prefix='proof-') as temp:
        target = Path(temp)
        inv.write_reports(report, target)
        decoded = json.loads((target / 'inventory.json').read_text())
        expect(decoded['cities'][0]['brightness'] == report['cities'][0]['brightness'], 'JSON measurement round-trip')
        for city in report['cities']:
            expect(len((target / f"{city['city'].lower()}_sections.csv").read_text().splitlines()) == city['index_sections'] + 1,
                   'CSV omitted a section')
    rejected(lambda: inv.write_reports(report, inv.ROOT / 'forbidden-inventory-output'), 'output escaped scratch')
    for script in ('daynight_inventory.py', 'test_daynight_inventory.py'):
        proc = subprocess.run([sys.executable, '-I', str(HERE / script), '--mw', str(inv.SCRATCH / 'absent')],
                              text=True, capture_output=True)
        expect(proc.returncode == 3, f'{script}: missing private data did not exit 3')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mutate', choices=('stride47', 'drop-section'))
    parser.add_argument('--mw', type=Path, default=DEFAULT_MW)
    parser.add_argument('--carbon', type=Path, default=DEFAULT_CARBON)
    parser.add_argument('--x360', type=Path, default=inv.aux.DEFAULT_X360)
    args = parser.parse_args()
    if inv.missing_data(args.mw, args.carbon, args.x360):
        print('SKIP: private game data absent')
        return 3
    try:
        mw, carbon = independent(args.mw, 'L2RA'), independent(args.carbon, 'L5RA')
        expect(mw['index_count'] == 720 and len(mw['scenery']) == 947 and not mw['trees'] and mw['flares'] == 0,
               'independent Rockport baseline differs')
        expect(carbon['index_count'] == 962 and len(carbon['trees']) == 444 and carbon['trees'][:3] == [41, 27, 19],
               'independent Palmont baseline differs')
        if args.mutate == 'stride47':
            report = inv.inventory(args.mw, args.carbon, args.x360, tree_stride=47)
        elif args.mutate == 'drop-section':
            original = inv.read_section
            dropped = False
            def drop(stream, section):
                nonlocal dropped
                raw = original(stream, section)
                if dropped or stream.name != 'STREAML2RA.BUN':
                    return raw
                for c in chunks(raw):
                    if c.kind == 0x80034100:
                        changed = bytearray(raw)
                        struct.pack_into('<I', changed, c.offset, 0)
                        dropped = True
                        return bytes(changed)
                return raw
            with mock.patch.object(inv, 'read_section', drop):
                report = inv.inventory(args.mw, args.carbon, args.x360)
        else:
            report = inv.inventory(args.mw, args.carbon, args.x360)
        for actual, oracle in zip(report['cities'], (mw, carbon)):
            expect(actual['index_sections'] == oracle['index_count'], 'index coverage differs')
            expect(actual['scenery_sections'] == len(oracle['scenery']), 'scenery coverage differs')
            expect(actual['light_packs'] == len(oracle['trees']), 'pack count differs from chunk lengths')
            expect([p['tree_records'] for p in actual['packs']] == oracle['trees'], 'tree count differs from chunk lengths')
            expect([p['light_records'] for p in actual['packs']] == oracle['lights'], 'light count differs from chunk lengths')
            expect([p['index_section'] for p in actual['packs']] == oracle['pack_sections'], 'pack section association differs')
            expect(sum(s['brightness']['vertices'] for s in actual['sections']) == actual['brightness']['vertices'], 'vertex coverage differs')
            expect(actual['brightness']['vertices'] > 0, 'brightness not measured')
        x = report['cities'][0]['x360']
        expect((x['contexts'], x['light_references'], x['unique_positions']) == (1231, 2997, 2021), 'Xbox source totals')
        expect(sum(s['lit_instances'] for s in x['sections']) == x['lit_instances'] == 1235, 'Xbox per-section coverage')
        expect(x['light_packs'] == x['flare_packs'] == 0, 'Xbox pack count differs')
        day = {r['name'] for r in report['cities'][0]['sky_textures']}
        night = {r['name'] for r in report['cities'][1]['sky_textures']}
        expect(any(n.startswith('SKY_MIDDAY_A_') for n in day), 'Rockport day sky texture metadata absent')
        expect('SKY_NIGHT_CLOUDS_02' in night, 'Palmont night sky texture metadata absent')
        if args.mw == DEFAULT_MW:
            small_checks(report)
    except (ValueError, AssertionError, OSError, StopIteration) as e:
        print(f'FAIL: {args.mutate or "baseline"}: {e}')
        return 1
    print('DAYNIGHT PASS: 0/0/444 packs; tree counts 41/27/19 at 48 bytes; 947 scenery chunks; '
          '96-byte light records; Xbox/model/brightness/sky tables; missing data exits 3')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
