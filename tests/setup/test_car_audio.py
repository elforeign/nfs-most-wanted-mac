"""Source graph proof; baseline pinned to 13984ab7 donor-clone semantics.

Original donor selection fails both layer identities (2/2 synthetic; private
31/32 stock key mismatches over 16 cars, source graph 32/32 passing). Missing engine/exhaust and graph/parent cycles are independent
controls. All fixtures are generated, without embedded source tables.
Review regression (eb845b37): depth rejection 0/2 before, 2/2 after;
64-node boundary accepted 1/1 before and after. Donor comparison is equivalence
evidence for frozen donor semantics, not a production selection regression.
"""
import sys as _inputs_sys
from pathlib import Path as _InputsPath
_inputs_sys.path.insert(0, str(_InputsPath(__file__).resolve().parents[2] / "tools"))
from setup_kit.input_paths import input_path
import copy
import json
import os
from pathlib import Path
import re
import struct
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
from setup_kit import car_audio as ca
from setup_kit.car_convert import hash32 as h


def chunk(tag, data):
    return tag[::-1] + struct.pack('<I', len(data) + 8) + data


def bank():
    raw = bytearray(160)
    raw[:4] = b'ABKC'
    struct.pack_into('<I', raw, 0x14, len(raw))
    struct.pack_into('<II', raw, 0x20, 96, 64)
    raw[96:100] = b'S10A'
    struct.pack_into('>III', raw, 100, 0, 1, 16)
    struct.pack_into('>II', raw, 112, 4 << 24 | 44100, 128)
    return bytes(raw)


def fixture(*, modern=True, drop=None, cycle=False, parent_cycle=False, inherited_self=False,
            reference_chain=0):
    """Build a real VPAK with relocated arrays, strings and inherited fields."""
    gain, curve = h('gain'), h('curve')
    refspec = (ca.REF, 12, 1, 4)
    stringkey = (ca.STRING, 8, 1, 4)
    schema = {
        h('pvehicle'): {h('Audio'): (ca.REF, 12, 4, 5)},
        ca.CAR: {ca.ENGINE: refspec, h('exhaustaudio'): refspec},
        ca.ENGINE: {gain: (h('EA::Reflection::Float'), 4, 1, 4),
                    curve: (h('EA::Reflection::Float'), 4, 4, 5),
                    0x2cee0a54: stringkey,
                    0x3a7c2f3d: stringkey,
                    0x2fc9c96f: stringkey,
                    h('next'): refspec}}
    if inherited_self:
        schema[ca.ENGINE][0x80730597] = refspec
    ref = lambda c, k: (c, k)
    collections = [(h('pvehicle'), h('source'), 0,
                    {h('Audio'): [ref(ca.CAR, h('stock')), ref(ca.CAR, h('upgrade'))]})]
    for tier in ('stock', 'upgrade'):
        values = {ca.ENGINE: ref(ca.ENGINE, h(tier + '_engine')),
                  h('exhaustaudio'): ref(ca.ENGINE, h(tier + '_exhaust'))}
        if drop:
            values.pop(h(drop))
        collections.append((ca.CAR, h(tier), 0, values))
    collections.append((ca.ENGINE, h('base'), h('base') if parent_cycle else 0,
                        {gain: .75, curve: [.1, .5, 1.],
                         0x2cee0a54: 'accel.gin', 0x3a7c2f3d: 'decel.gin',
                         0x2fc9c96f: 'samples.abk'}))
    if inherited_self:
        collections[-1][3][0x80730597] = ref(ca.ENGINE, h('stock_engine'))
    for tier in ('stock', 'upgrade'):
        for layer in ('engine', 'exhaust'):
            values = {}
            if cycle:
                values[h('next')] = ref(ca.ENGINE, h(tier + '_' + ('engine' if layer == 'exhaust' else 'exhaust')))
            if reference_chain and (tier, layer) == ('stock', 'engine'):
                values[h('next')] = ref(ca.ENGINE, h('chain_0'))
            collections.append((ca.ENGINE, h(tier + '_' + layer), h('base'), values))
    for index in range(reference_chain):
        values = ({h('next'): ref(ca.ENGINE, h(f'chain_{index + 1}'))}
                  if index + 1 < reference_chain else {})
        collections.append((ca.ENGINE, h(f'chain_{index}'), h('base'), values))
    binary, binfix, fixups, records = bytearray(), [], [], []

    def put(data):
        at = len(binary)
        binary.extend(data)
        return at

    def atom(dt, value):
        if dt == ca.REF:
            return struct.pack('<3I', *value, 0)
        if dt == ca.STRING:
            text = put(value.encode() + b'\0')
            at = put(struct.pack('<2I', h(value), 0))
            binfix.append((at + 4, 3, 1, text))
            return at
        return struct.pack('<f', value)

    for cls, fields in schema.items():
        at = put(b''.join(struct.pack('<IIHHHBB', fk, dt, 0, size, maximum, flags, 2)
                          for fk, (dt, size, maximum, flags) in fields.items()))
        row = bytearray(24)
        struct.pack_into('<I', row, 8, len(fields))
        records.append((cls, 0x5e970cbc, row, [(12, at)]))
    for cls, key, parent, fields in collections:
        row = bytearray(32)
        struct.pack_into('<3I', row, 0, key, cls, parent)
        struct.pack_into('<I', row, 20, len(fields))
        ptrs = []
        for fk, value in fields.items():
            dt, size, maximum, flags = schema[cls][fk]
            if flags & 1:
                data = struct.pack('<4H', len(value), len(value), size, 0)
                data += b''.join(atom(dt, item) for item in value)
                at = put(data)
            else:
                data = atom(dt, value)
                at = data if isinstance(data, int) else put(data)
            ptrs.append((len(row) + 4, at))
            row.extend(struct.pack('<IIHBB', fk, 0, 0, 2 if flags & 1 else 0, 0))
        records.append((key, 0x8e112eb7, row, ptrs))
    dep = chunk(b'DepN', struct.pack('<3I', 2, 0, 0))
    vers = chunk(b'Vers', struct.pack('<I', 3)) if modern else b''
    nf = sum(len(r[3]) for r in records)
    ptrsize = 8 + 12 * (nf + bool(binfix) + len(binfix)) + 8
    expsize = 12 + len(records) * (16 if modern else 20)
    pos = len(vers) + len(dep) + ptrsize + expsize + 8
    exports, payload = [], bytearray()
    for key, typ, row, ptrs in records:
        off = pos + len(payload)
        exports.append(struct.pack('<4I' if modern else '<5I',
                                   *((key, typ, len(row), off) if modern else (key, typ, 0, len(row), off))))
        fixups.extend((off + at, 3, 1, target) for at, target in ptrs)
        payload.extend(row)
    if binfix:
        fixups.append((0, 2, 1, 0))
        fixups.extend(binfix)
    ptr = chunk(b'PtrN', b''.join(struct.pack('<IHHI', *f) for f in fixups) + bytes(8))
    vault = vers + dep + ptr + chunk(b'ExpN', struct.pack('<I', len(records)) + b''.join(exports)) + chunk(b'DatN', payload)
    names = b'audio-fixture\0'
    bo = 36 + len(names)
    return (b'VPAK' + struct.pack('<3I', 1, 36, len(names)) +
            struct.pack('<5I', 0, len(binary), len(vault), bo, bo + len(binary)) + names + binary + vault)


def resources():
    return {'SOUND/ENGINE/GIN/accel.gin': b'Gnsu20fixture-a',
            'SOUND/ENGINE/GIN/decel.gin': b'Gnsu20fixture-d',
            'SOUND/ENGINE/samples.abk': bank()}


def layer_ids(graph):
    return [(node['fields'][f'{ca.ENGINE:08x}']['values'][0]['key'],
             node['fields'][f'{h("exhaustaudio"):08x}']['values'][0]['key'])
            for tier in graph['tiers'] for node in [graph['nodes'][tier['target']]]]


class GraphProof(unittest.TestCase):
    def test_reference_depth_is_classified(self):
        good = ca.inventory(fixture(reference_chain=62), ['source'], resources())
        self.assertIn('source', good['supported'])
        for length in (63, 1200):
            with self.subTest(length=length):
                report = ca.inventory(fixture(reference_chain=length), ['source', 'absent'], resources())
                self.assertFalse(report['supported'])
                self.assertIn('audio reference depth', report['unsupported']['source'])
                self.assertIn('absent', report['unsupported'])

    def test_graph_and_original_donor_identity(self):
        for modern in (False, True):
            g = ca.graph(fixture(modern=modern), 'source', resources())
            expected = [(f'{h(t+"_engine"):08x}', f'{h(t+"_exhaust"):08x}') for t in ('stock', 'upgrade')]
            self.assertEqual(layer_ids(g), expected)
            # 13984ab7 car85 append_collection_as copies donor pointed-to fields;
            # a changed vehicle key does not create source audio collections.
            donor = (f'{h("donor_engine"):08x}', f'{h("donor_exhaust"):08x}')
            self.assertEqual(sum(a != b for a, b in zip(donor, expected[0])), 2)
            if '--original' in sys.argv:
                self.assertEqual(donor, expected[0], 'original donor graph fails stock source identity goldens')
            self.assertEqual(len(g['nodes']), 6)
            self.assertEqual(len(g['resources']), 3)
            self.assertEqual(json.loads(json.dumps(g)), g)
            for node in g['nodes'].values():
                for edge in node['edges']:
                    self.assertIn(edge['target'], g['nodes'])
            engine = g['nodes'][ca.identity(ca.ENGINE, h('stock_engine'))]
            self.assertEqual(engine['fields'][f'{h("gain"):08x}']['values'], [.75])
            self.assertEqual(engine['fields'][f'{h("curve"):08x}']['values'][0], .10000000149011612)
            self.assertEqual(engine['fields'][f'{h("gain"):08x}']['owner'], ca.identity(ca.ENGINE, h('base')))
        print('synthetic: source 4/4 tier-layer identities; original donor 2/2 stock mismatches; both vault formats pass')

    def test_independent_missing_layers_and_cycles(self):
        for layer in ('engineaudio', 'exhaustaudio'):
            with self.subTest(layer=layer), self.assertRaisesRegex(ValueError, 'missing engine or exhaust'):
                ca.graph(fixture(drop=layer), 'source', resources())
        for kwargs, reason in [({'cycle': True}, 'reference cycle'), ({'parent_cycle': True}, 'inheritance cycle')]:
            with self.assertRaisesRegex(ValueError, reason):
                ca.graph(fixture(**kwargs), 'source', resources())
        original_field = ca.AudioAttributes.field
        def missing(self, cls, key, fk):
            value = original_field(self, cls, key, fk)
            if cls == ca.CAR and fk == ca.ENGINE:
                value['values'][0]['key'] = '12345678'
            return value
        with mock.patch.object(ca.AudioAttributes, 'field', missing):
            with self.assertRaisesRegex(ValueError, 'missing collection'):
                ca.graph(fixture(), 'source', resources())
        print('mutants: missing collection 1/1; engine deletion 1/1; exhaust deletion 1/1; reference/parent cycles 2/2 rejected')

    def test_resource_controls(self):
        data = resources()
        del data['SOUND/ENGINE/GIN/accel.gin']
        with self.assertRaisesRegex(ValueError, 'missing resource'):
            ca.graph(fixture(), 'source', data)
        for path in ('other/accel.gin', 'sound/engine/gin/ACCEL.GIN'):
            bad = dict(resources(), **{path: b'Gnsu20other'})
            with self.assertRaisesRegex(ValueError, 'collision'):
                ca.graph(fixture(), 'source', bad)
        for game, platform in [('carbon', 'x360'), ('prostreet', 'pc'), ('undercover', 'pc')]:
            r = ca.inventory(b'', ['source'], {}, game=game, platform=platform)
            self.assertIn('unsupported audio source/platform', r['unsupported']['source'])
        good = ca.graph(fixture(), 'source', resources())
        bad = copy.deepcopy(good)
        bad['tiers'].reverse()
        self.assertNotEqual(layer_ids(good), layer_ids(bad), 'shifted tier mutant detected')
        print('resource controls: missing 1, basename/case collisions 2, unsupported pairs 3, shifted tiers 1 rejected')

    def test_inherited_self_alias_and_string_identity(self):
        g = ca.graph(fixture(inherited_self=True), 'source', resources())
        edges = [e for n in g['nodes'].values() for e in n['edges'] if e['inherited_self']]
        self.assertEqual(len(edges), 1)
        data = fixture()
        broken = data.replace(b'accel.gin', b'other.gin')
        with self.assertRaisesRegex(ValueError, 'string-key identity mismatch'):
            ca.graph(broken, 'source', resources())

    def test_bank_controls(self):
        self.assertEqual(len(ca.bank_inventory(bank())['sounds']), 1)
        for offset, value in [(0x14, 0), (0x20, 0), (112, 0x0300ac44), (116, 0x40000080)]:
            data = bytearray(bank())
            struct.pack_into('>I' if offset >= 112 else '<I', data, offset, value)
            with self.assertRaises(ValueError):
                ca.bank_inventory(data)


def private_proof():
    source = input_path('NFSC')
    mw = Path(os.environ.get('NFS_GAME', ROOT.parent / 'Need For Speed Most Wanted Black Edition'))
    if not (source / 'GLOBAL/attributes.bin').is_file() or not (mw / 'GLOBAL/attributes.bin').is_file():
        print('private source/donor assets absent; synthetic proof completed')
        return False
    # Existing conversion admission cases are the shared source of this roster.
    path = ROOT / 'tools_snapshot/test_driver_seats.py'
    if not path.is_file():
        print('private admission roster absent; synthetic proof completed')
        return False
    import ast
    module = ast.parse(path.read_text())
    cases = next(ast.literal_eval(n.value) for n in module.body
                 if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'CASES' for t in n.targets))
    files = {p.relative_to(source).as_posix(): p.read_bytes()
             for p in (source / 'SOUND/ENGINE').rglob('*') if p.suffix.lower() in ('.abk', '.gin')}
    data = (source / 'GLOBAL/attributes.bin').read_bytes()
    a = ca.AudioAttributes(data)
    donor = ca.AudioAttributes((mw / 'GLOBAL/attributes.bin').read_bytes())
    cars = re.findall(r'\|C/([^|\n]+)\|', (ROOT / 'tools_snapshot/private_cars.py').read_text())
    report = ca.inventory(data, cars, files)
    assert not report['unsupported'], report['unsupported']
    assert len(report['supported']) == len(cars)
    roster = ast.parse((ROOT / 'tools_snapshot/private_cars.py').read_text())
    # ADDITIONS is a literal followed by .strip(); read the literal, not code.
    additions = next(ast.literal_eval(n.value.func.value) for n in roster.body
                     if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'ADDITIONS' for t in n.targets))
    groups = {code: [line.split('|')[2].split('/')[1] for line in additions.splitlines()
                     if line and line.split('|')[2].startswith(code + '/')] for code in ('P', 'U')}
    swaps = next(ast.literal_eval(n.value.func.value).split() for n in roster.body
                 if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'SWAPS' for t in n.targets))
    groups['P'] += swaps
    for code, game in [('P', 'prostreet'), ('U', 'undercover')]:
        rejected = ca.inventory(b'', groups[code], {}, game=game)
        assert len(rejected['unsupported']) == len(groups[code])
        assert all('schema and resources not audited' in reason for reason in rejected['unsupported'].values())
    print('unsupported roster:', {code: len(values) for code, values in groups.items()})
    tier_layers = 0
    for car, g in report['supported'].items():
        tiers = a.field(h('pvehicle'), h(car.lower()), h('Audio'))['values']
        expected = [tuple(a.field(ca.CAR, int(t['key'], 16), fk)['values'][0]['key']
                          for fk in (ca.ENGINE, h('exhaustaudio'))) for t in tiers]
        assert layer_ids(g) == expected
        tier_layers += len(expected) * 2
    print(f'all-source tier-layer goldens: {tier_layers}/{tier_layers}')
    mismatches = comparisons = 0
    missing = {'engine': 0, 'exhaust': 0}
    for car, old in cases:
        g = report['supported'][car]
        # Decode goldens directly from the source field reader, independently of
        # graph traversal. Pin original behavior to unchanged donor RefSpecs.
        tiers = a.field(h('pvehicle'), h(car.lower()), h('Audio'))['values']
        golden = []
        for tier in tiers:
            k = int(tier['key'], 16)
            golden.append(tuple(a.field(ca.CAR, k, field)['values'][0]['key']
                                for field in (ca.ENGINE, h('exhaustaudio'))))
        assert layer_ids(g) == golden
        original = donor.field(h('pvehicle'), h(old.lower()), ca.ENGINE)['values'][0]
        # The native donor graph has no caraudio pair or exhaustaudio field.
        before = (original['key'], None)
        mismatches += sum(x != y for x, y in zip(before, golden[0]))
        comparisons += 2
        for label, field in [('engine', ca.ENGINE), ('exhaust', h('exhaustaudio'))]:
            original_field = ca.AudioAttributes.field
            def omit(self, cls, key, fk):
                return None if cls == ca.CAR and fk == field else original_field(self, cls, key, fk)
            with mock.patch.object(ca.AudioAttributes, 'field', omit):
                try:
                    ca.graph(data, car, files)
                except ValueError as error:
                    assert 'missing engine or exhaust' in str(error), str(error)
                    missing[label] += 1
                else:
                    raise AssertionError('deleted layer accepted')
    for car, filename in [('G35', 'ex_acl_92.gin'), ('MUSTANGSHLBYN', 'ex_dcl_14.gin')]:
        assert any(r['path'].endswith('/' + filename) for r in report['supported'][car]['resources'])
    historical = {}
    for car, _ in cases:
        g = report['supported'][car]
        paths = set()
        for tier in g['tiers']:
            pair = g['nodes'][tier['target']]
            for edge in pair['edges']:
                node = g['nodes'][edge['target']]
                for value in node['fields'].values():
                    paths.update(r['path'] for r in value.get('resources', []))
        historical.update({r['path']: r for r in g['resources'] if r['path'] in paths})
    print('historical direct-pair recount:',
          sum(r['kind'] == 'gin' for r in historical.values()),
          sum(r['kind'] == 'abk' for r in historical.values()),
          sum(not s.get('empty', False) for r in historical.values() for s in r.get('sounds', [])))
    print(f'original donor layer mismatches: {mismatches}/{comparisons}')
    assert comparisons == len(cases) * 2 and mismatches >= len(cases)
    assert missing == dict.fromkeys(missing, len(cases))
    print(f'private: {len(cars)}/{len(cars)} closed; inventory {report["counts"]}; original donor fails {mismatches}/{comparisons}; deletion mutants {missing}')
    return True


if __name__ == '__main__':
    result = unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromTestCase(GraphProof))
    if not result.wasSuccessful():
        sys.exit(1)
    sys.exit(0 if private_proof() else 3)
