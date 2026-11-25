"""Exported car release proof with generated fixtures and optional local assets.

Issue #24 retail Carbon PC attributes contracts: 0/30 approved before, 30/30 after.
Converter refresh retains 86 source equivalents: old 0/30 retail and 0/28 decoded
contracts admitted; refreshed 30/30 retail and 28/28 decoded (both attribute copies).
Legacy archive retains all 409 previous entries and adds 14 measured console donors.
Issue #24 Carbon PC repack: 2/30 imported before, 30/30 after.
Altered decoded solid: 1/1 imported when one changed byte is ignored, 0/1 with
verification. The failed import leaves the current selection intact.
The original package has no packaged policy: --verify-policy fails before any
car can activate. Synthetic approvals are installed only in a disposable export.
Four controls cover an asset, a private roster, shader math and a missing codec.
No synthetic result authorizes a real car or substitutes for native save proof.
Good: 7 checks, 12 shipped CLI commands, 2 native admissions, 26 pin refusals.
Broken: original unpinned import exits 1; omitted pixel helper exits 1;
native HUFF registry originally fails 1/1; stale/noncanonical/absent trust
controls reject. Publication separately refuses 3/3 asset/roster/shader leaks.
Discovery integration: fast-tier plan originally errors 1/7 after removing
Setup discovery; full-tier plan passes 7/7, including the removal control.
Measured exported PC review: 10/10 contracts and 70/70 appearance outputs match;
270/270 changed input pins and 70/70 changed output pins reject. Native reader
admits 10; changed input/output files reject 2/2 before publication, and a
changed installed output rejects with zero native admissions. Original review
CLI exits 2; --verify-contracts accepts the measured rows (exit 0). These local
contracts do not prove player selection metadata delivery or a game lifecycle.
Measured exported X360 review: 9/9 contracts and 63/63 appearance outputs match;
306/306 changed input pins and 63/63 changed output pins reject. Native reader
admits 9; altered X360 source input and converter output files each fail their
pin before publication (2/2 refused: changed source input / changed converter
output). An altered installed X360 output admits zero cars (exit 1). PC 10/10
identities and their original ordered approval variants remain unchanged.
Additional measured contracts: PC 6/6, X360 5/6; all 448 changed input
pins and 94 changed output pins reject (including one deliberately broken
pin per edition/car). Original 19 selections and ordered approval variants
remain byte-identical to their GRIP.BIN refresh. In an exported source tree,
reproduce the rows with --review-additions PC_ROOT X360_ISO MW_ROOT WORK
(measured with GRIP.BIN, 8 outputs each); trust artifacts use --generate
and --generate-selections as before. No converter check is relaxed.
X360 MUSTANGSHLBYN refuses source parity: KIT00_REAR_WINDOW_T0_A UV error
0.876464844 exceeds the unchanged 0.0015 threshold; PC remains approved.
Carbon PC roster regression: 0/14 new permanent identities on the old
package (16 PC selections), 14/14 on the expanded package (30 PC selections).
The 112 promoted output hashes match the approved local install.
Packaged selection metadata: 44/44 reviewed rows (PC 30, X360 14); edited
display/source/slot/parts/output and omitted/extra rows: 7 FAIL refusals,
original canonical metadata: PASS. Generator reproduces identical bytes.
Exported metadata edit: --verify-policy FAIL exit 1; original PASS exit 0.
Current converter inventories: original rows match 0/19 measured inventories
(FAIL: 19 missing GRIP.BIN); regenerated rows match and approve 19/19 (PASS).
Actual measured contracts: original approvals accept 0/19; regenerated 19/19.
Exported converter pins (published HUFF, Setup's locked Python/NumPy): rows
pinned from the private interpreter and HUFF: 30/30 mismatched, 0/30 approved
(FAIL); re-pinned in the exported tree: 0/30 mismatched, 30/30 approved, the
same with the shipped interpreter itself. --refresh-converter-pins and
--review-additions refuse any other interpreter or an unexported tree.
SHA-checked PC/X360 conversion receipts independently pin the output inventory.
Generator preserves reviewed inputs, identities, compatibility and variant order.
The multi-game roster advances from 30 to 75 permanent slots. ProStreet PC
matches 217 approved hashes across 31 cars, including all 31 stock player AUDIO.BIN packages, and Undercover PC matches 56 across 14. The original 44 Carbon contracts retain their measured output hashes.
ProStreet performance revision retained after the Carbon repack merge: 89/89
generation-8 contracts PASS; one contract dropped from that generation: FAIL.
Revision 11 retained after Undercover performance: 89/89 generation-9
contracts PASS; one contract untagged from that generation: 88, FAIL.
"""
import copy
from contextlib import closing, redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
sys.path[:0] = [str(ROOT / 'tools'), str(Path(__file__).resolve().parent)]
from setup_kit import car_catalog as cc

POLICY = 'tools/setup_kit/car_approvals.json'
HEADER = (Path('runtime') / 'car_approvals_public.h').as_posix()
SELECTIONS = 'tools/setup_kit/car_selections.json'
LOCK = 'tools/installer/deps.lock.json'
SETUP_PYTHON = 'build/installer/dmg/NFS Most Wanted Native Setup.app/Contents/Resources/python/bin/python3'
PIN =re.compile(r'^PACKAGED_POLICY_SHA256 = .*$', re.M)
# Generated from actual PC/X360 conversions by --refresh-converter-pins.
MEASURED_OUTPUTS = '{"pc":{"converter":{"policy":"2366e34698cb0356b3afa9f7aa0071132c1e0948ee7b8d6960b70f03d4253239","revision":2},"outputs":{"AUDIO.BIN":{"sha256":"fbe66eb609a07e9c7ee3519ee42433ec8e7ce50b77e4d0c51f25e811ad04c5cb","size":4028804},"COLLISION.BIN":{"sha256":"3c7c590819521ec3b8d00123ea59cc685d2848a2e4e063eee278f40b710707f4","size":804},"GEOMETRY.BIN":{"sha256":"176851d91c89debdd7989f9e8d19e473b79e09af8c4cb6a74f7bf7b8cdf7e55a","size":31821188},"GRIP.BIN":{"sha256":"af08088289f1d5272e85c066a78f0ca263c6bba137e35791cfac11a96a2b79da","size":1096},"PAINT.BIN":{"sha256":"4f93698a8357e5605dc0c4c4460842833f503d81b25c5059a1710abd8d7b7474","size":32},"PERFORMANCE.BIN":{"sha256":"a7d33579b17de01751d311f0b6e3e35ebb48d0deaa6f5da2bd0e85fb9eddf8cc","size":344},"PHYSICS.BIN":{"sha256":"90a7dfcd6832bd149d945587e22f5c6c87b315be6ca3e80f58ef43a6106dc042","size":36},"SOLIDMAP.BIN":{"sha256":"fecf6712465dd21dfbcfdb41adf825e4f15a872c2871822cb8e06d9bcc4a42ad","size":8600},"TEXTURES.BIN":{"sha256":"130f958a3932f42c42a5dd13b8c8f961b503b8b22a29890ece199b9e0b56b36e","size":894620},"WHEELFIT.BIN":{"sha256":"b03e5a0b5b8d80548926363400de7c7f91f1a5acda157c8b2ba93c12a0f5eba9","size":380}},"slot":0},"x360":{"converter":{"policy":"2df99183ee44a82df9af9f59e7c710b2534f3cfebdd5b5d0c5a744f899224b28","revision":2},"outputs":{"AUDIO.BIN":{"sha256":"09f8cfc744202d6af67aff6b49f5240341f0d94a3debe0ae8ed558f53d0f14df","size":1087690},"COLLISION.BIN":{"sha256":"3c7c590819521ec3b8d00123ea59cc685d2848a2e4e063eee278f40b710707f4","size":804},"GEOMETRY.BIN":{"sha256":"8fc2ae2f49fce608fb39b3c012505115317ae0ff549f1af26f875ac5249f5e02","size":24033149},"GRIP.BIN":{"sha256":"af08088289f1d5272e85c066a78f0ca263c6bba137e35791cfac11a96a2b79da","size":1096},"PAINT.BIN":{"sha256":"4f93698a8357e5605dc0c4c4460842833f503d81b25c5059a1710abd8d7b7474","size":32},"PERFORMANCE.BIN":{"sha256":"a7d33579b17de01751d311f0b6e3e35ebb48d0deaa6f5da2bd0e85fb9eddf8cc","size":344},"PHYSICS.BIN":{"sha256":"90a7dfcd6832bd149d945587e22f5c6c87b315be6ca3e80f58ef43a6106dc042","size":36},"SOLIDMAP.BIN":{"sha256":"fecf6712465dd21dfbcfdb41adf825e4f15a872c2871822cb8e06d9bcc4a42ad","size":8600},"TEXTURES.BIN":{"sha256":"2bd3ebccf9d75de9e773b140297ca7283fe1f1a1345ae08ed308ddda659f0e39","size":931484},"WHEELFIT.BIN":{"sha256":"b03e5a0b5b8d80548926363400de7c7f91f1a5acda157c8b2ba93c12a0f5eba9","size":380}},"slot":0}}\n'
MEASURED_OUTPUTS_SHA256 = '39efa7d051be40a31503f0323c9e46b96334c299b7acf4c54d26e1fb85e3ed07'
FORBIDDEN = (
    'tools_snapshot/carbon_car.py', 'tools_snapshot/carbon_batch.py',
    'tools_snapshot/private_cars.py', 'tools_snapshot/carbon_namespace.py',
    'tools_snapshot/carbon_repair.py', 'tools_snapshot/v25_car.py',
    'tools_snapshot/uc_car.py', 'tools_snapshot/later_tex.py',
    'runtime/private_cars.c', 'runtime/carbon_lamp_private.h',
    'runtime/car85_brands_private.h',
)
FORBIDDEN_PREFIXES = ('tools/publish/', 'tools_snapshot/private_cars_fixtures/',
                      'runtime/private_cars_fixtures/', 'run_shots/')

# Permanent slots and donor choices; source bindings and part keys are decoded
# from the local inputs by derive_addition_specs, never copied from game data.
ADDITIONS = (
    (0, '240SX', 'RX7', 'NISSAN'),
    (3, '997TT', '911TURBO', 'PORSCHE'),
    (5, 'CAMARON', 'MUSTANGGT', 'CHEVROLET'),
    (17, 'G35', 'IS300', 'INFINITI'),
    (20, 'MAZDASPEED3', 'GTI', 'MAZDA'),
    (23, 'MUSTANGSHLBYN', 'MUSTANGGT', 'SHELBY'),
)
CARBON_PC_PROMOTIONS = {
    'CHALLENGER71': 91, 'CHALLENGERN': 92, 'CHARGER06': 93, 'CHARGER69': 94,
    'CHEVELLE': 95, 'COROLLA': 96, 'CUDA': 98, 'ECLIPSE': 99,
    'LANCEREVO9': 103, 'MURCIELAGO640': 106, 'MUSTANGSHLBYO': 108,
    'ROADRUNNER': 111, 'SKYLINE': 112, 'ZONDA': 113,
}


def derive_addition_specs(pc, mw, additions=None):
    """Reproduce review metadata from source RefSpecs and native donor parts."""
    from setup_kit import car_convert as cv
    from setup_kit.car_grip import raw_field
    attributes = cv.FitAttributes((pc / 'GLOBAL/attributes.bin').read_bytes())
    frontend = cv.FitAttributes((pc / 'GLOBAL/FE_ATTRIB.bin').read_bytes())
    attributes.collections.update(frontend.collections)
    attributes.defs.update(frontend.defs)
    names = {}
    for path in (pc / 'GLOBAL/attributes.bin', pc / 'GLOBAL/FE_ATTRIB.bin'):
        for _, binary, _ in cv.read_vpak(path.read_bytes()):
            for word in re.findall(rb'[A-Za-z0-9_]{1,60}', binary):
                name = word.decode('ascii')
                names[cv.hash32(name)] = name
    donor_parts = read_donor_parts(mw)
    result = []
    if additions is None:
        additions = [(slot, folder, donor, donor, folder, maker)
                     for slot, folder, donor, maker in ADDITIONS]
    for slot, folder, donor, price, display, maker in additions:
        key = cv.hash32(folder.lower())
        assert (cv.hash32('ecar'), key) in attributes.collections
        cls, front, _ = struct.unpack('<III', raw_field(attributes, 'pvehicle', key, 'frontend'))
        assert cls == cv.hash32('frontend') and (cls, front) in attributes.collections
        front_name = names[front]
        assert cv.hash32(front_name) == front
        result.append(dict(slot=slot, folder=folder, display=dict(name=display, manufacturer=maker),
            bindings=dict(folder=folder, pvehicle=folder, frontend=front_name.upper(), ecar=folder),
            donors=dict(model=donor, price=price),
            parts=donor_parts(donor)))
    return result


def read_donor_parts(mw):
    """Decode the native donor's customizable part keys from the MW vault."""
    from setup_kit import car_convert as cv
    data = cv.unpack_file((mw / 'GLOBAL/GLOBALB.LZC').read_bytes())
    root, size = cv.find(data, 0x80034602)
    chunks = {key: data[offset+8:offset+8+length]
              for key, offset, length in cv.children(data, root+8, root+8+size)}
    parts, count = chunks[0x34604], cv.u32(data, root+0x48)
    assert count * 14 <= len(parts) < count * 14 + 4
    types = [x[0] for x in struct.iter_unpack('<I', chunks[0x3460b])]
    assert len(types) == cv.u32(data, root+0x38)
    offset, size = cv.find(data, 0x34607)
    slots = data[offset+8:offset+8+size]
    assert len(slots) >= 0x458 and (len(slots)-0x458) % 16 == 0
    defaults = list(struct.iter_unpack('<II', slots[:0x458]))
    overrides = list(struct.iter_unpack('<4I', slots[0x458:]))

    def for_donor(donor):
        donor_hash = cv.name_hash(donor)
        allowed = {donor_hash if v == 0xffffffff else v for row in defaults for v in row}
        allowed.update(v for owner, _, *values in overrides if owner == donor_hash for v in values)
        allowed.discard(0)
        keys = {}
        for index in range(count):
            row = parts[index*14:(index+1)*14]
            kind, type_index = row[4], row[7]
            assert type_index < len(types)
            part_type = types[type_index]
            if part_type in allowed:
                assert kind < 255
                semantic = f'P_{cv.u32(row):08X}_{part_type:08X}_{index:05d}'
                keys.setdefault(kind, []).append(semantic)
        assert keys
        return [dict(type=kind, keys=values) for kind, values in keys.items()]

    return for_donor


def locked_runtime(root):
    """Python and NumPy versions Setup ships, from its dependency lock."""
    lock = json.loads((root / LOCK).read_bytes())
    python = [int(v) for v in lock['python']['version'].split('+')[0].split('.')]
    return python, next(w['version'] for w in lock['wheels'] if w['name'] == 'numpy')


def require_shipped_runtime():
    """Contracts pin the interpreter and HUFF bytes Setup runs: exported tree, shipped Python only."""
    from setup_kit import car_convert
    python, numpy = locked_runtime(ROOT)
    if (list(sys.version_info[:3]) != python or car_convert.np.__version__ != numpy or
            (ROOT / 'tools/publish/import.py').exists()):
        raise SystemExit('measure contracts in the exported payload with Setup\'s Python ' +
                         '.'.join(map(str, python)) + ' and NumPy ' + numpy + ' (tools/publish/README.md)')


def converter_pin_report(emulate):
    """Compare every packaged row with the converter inputs Setup measures here."""
    import types
    from setup_kit import car_import, car_selection
    if emulate:
        # _code reads only these interpreter facts; every code pin comes from this tree's bytes.
        python, numpy = locked_runtime(ROOT)
        car_import.sys = types.SimpleNamespace(version_info=tuple(python), implementation=sys.implementation)
        car_import.converter.np = types.SimpleNamespace(__version__=numpy)
    else:
        require_shipped_runtime()
    rows = car_selection.load()
    measured = refreshed_converter_rows(rows)
    mismatched = [r['origin']['platform'] + ' ' + r['origin']['folder'] for r, m in zip(rows, measured) if r != m]
    accepted = 0
    for row in measured:
        reservations = cc.reservations()
        reservations[row['slot']] = row
        try:
            cc.make_catalog(reservations, cc.packaged_policy())
            accepted += 1
        except cc.CatalogError as exc:
            if 'conversion contract is not approved' not in str(exc):
                raise
    donor_rows = x360_donor_rows(measured)
    for row in donor_rows:
        reservations = cc.reservations()
        reservations[row['slot']] = row
        cc.make_catalog(reservations, cc.packaged_policy())
    return dict(rows=len(rows), mismatched=len(mismatched), first=mismatched[:3], accepted=accepted,
                console_donor_approved=len(donor_rows))


def carbon_pc_repack(repack, mw, *, emulate=False):
    """Import the real repack and refuse a changed decoded solid in the same store."""
    import types
    from setup_kit import car_convert as cv, car_import, car_selection, car_sources
    from setup_kit.x360 import Source

    if emulate:
        python, numpy = locked_runtime(ROOT)
        assert cv.np.__version__ == numpy
        car_import.sys = types.SimpleNamespace(version_info=tuple(python), implementation=sys.implementation)
    else:
        require_shipped_runtime()
    rows = {r['slot']: r for r in car_selection.load() if r['origin']['game'] == 'carbon'
            and r['origin']['platform'] == 'pc'}
    assert len(rows) == 30
    with tempfile.TemporaryDirectory(prefix='carbon-pc-repack-') as td:
        root = Path(td).resolve()
        store = root / 'catalog'
        engine = car_import.CarImporter(store, root / 'cache')
        recognition = engine.check(repack, mw_pc=mw)['recognition']
        assert (recognition['game'], recognition['platform']) == ('carbon', 'pc')
        recognised = {r['car_hash'] for r in recognition['cars'] if r['status'] == 'importable'}
        specs = [car_selection.spec(r) for r in rows.values()
                 if car_sources.path_hash('CARS/' + r['origin']['folder']) in recognised]
        engine.import_cars(repack, mw_pc=mw, cars=specs)
        active = cc.verify_current(store)
        assert not active.error
        imported = {r['slot'] for r in active.selected}
        assert imported == {r['slot'] for r in specs}
        for row in active.selected:
            assert row['outputs'] == rows[row['slot']]['outputs'], row['origin']['folder']
        reader = native_reader(ROOT, root)
        command([str(reader), str(store), str(len(imported))])
        print(f'Carbon PC repack: {len(imported)}/{len(rows)} imported with approved outputs', flush=True)

        row = next(r for r in rows.values() if r['origin']['folder'] == 'CORVETTEZ06')
        geometry = 'CARS/CORVETTEZ06/GEOMETRY.BIN'
        changed = root / 'altered'
        with closing(Source(repack)) as reader:
            for name in {*row['inputs']['source'], 'CARS/TEXTURES.BIN'}:
                target = changed / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(reader.files[name], target)
        target = changed / geometry
        data = bytearray(target.read_bytes())
        entry = next(e for e in cv.index(data) if e[4] == 0x200 and data[e[1]+24:e[1]+28] == b'RAWW')
        before = cv.unpack_entry(bytes(data), entry)
        block_size = cv.u32(data, entry[1] + 8)
        data[entry[1] + block_size - 1] ^= 1
        after = cv.unpack_entry(bytes(data), entry)
        assert len(before) == len(after) and sum(a != b for a, b in zip(before, after)) == 1
        target.write_bytes(data)

        pointer = (store / 'current.json').read_bytes()
        with mock.patch.object(engine, '_convert', wraps=engine._convert) as convert:
            try:
                engine.import_cars(changed, mw_pc=mw, cars=[car_selection.spec(row)])
            except car_import.ImportError as exc:
                assert str(exc) == 'selected source pair is not verified: CORVETTEZ06', str(exc)
            else:
                raise AssertionError('1/1 cars with changed decoded geometry imported')
            convert.assert_not_called()
        assert (store / 'current.json').read_bytes() == pointer
        print('Changed decoded geometry: 0/1 imported and 0 converter calls', flush=True)
        assert imported == rows.keys(), sorted(rows.keys() - imported)


def review_additions(pc, x360, mw, work):
    """Remeasure the reviewed Carbon roster and retain fail-closed refusals."""
    require_shipped_runtime()
    from setup_kit.car_import import CarImporter
    from test_car_x360_assets import compare_car
    from setup_kit import car_sources, car_selection
    from setup_kit.x360 import Source
    work.mkdir(parents=True, exist_ok=True)
    previous = [row for row in car_selection.load() if row['origin']['game'] == 'carbon']
    refreshed = refreshed_converter_rows(previous)
    specs = {(row['origin']['platform'], row['origin']['folder']): row for row in refreshed}
    contracts, refusals = [], []
    for platform, source in (('pc', pc), ('x360', x360)):
        for before in previous:
            if before['origin']['platform'] != platform:
                continue
            spec = car_selection.spec(before)
            importer = CarImporter(work / ('catalog-' + platform),
                work / ('work-' + platform + '-' + spec['folder']))
            try:
                # These additions were admitted by strict PC/X360 source
                # parity. Earlier contracts retain their separately reviewed
                # platform models; their exact source pins must stay unchanged.
                if platform == 'x360' and spec['folder'] in {row[1] for row in ADDITIONS}:
                    with closing(Source(source)) as reader:
                        logical = car_sources._logical(reader, car_sources._physical(reader))
                        compare_car(spec['folder'], pc / 'CARS', reader, logical)
                plan = importer.plan(source, mw_pc=mw, cars=[spec], measure=True)
                after = plan['cars'][0]['contract']
                expected = copy.deepcopy(specs[platform, spec['folder']])
                expected['outputs'] = after['outputs']
                if after != expected:
                    raise ValueError('measured inputs/metadata changed: ' + spec['folder'])
                contracts.append(after)
                print('reviewed ' + platform + ' ' + spec['folder'] + ': ' +
                      str(len(after['outputs'])) + ' outputs', flush=True)
            except (ValueError, OSError, AssertionError) as exc:
                refusals.append(dict(platform=platform, folder=spec['folder'], reason=str(exc)))
            (work / 'contracts.json').write_bytes(cc.encoded(contracts))
            (work / 'refusals.json').write_bytes(cc.encoded(refusals))
    print(json.dumps(dict(contracts=len(contracts), refusals=refusals), sort_keys=True))


def trust_outputs(root, data):
    """Derive both trust roots from validated canonical policy bytes."""
    sha = hashlib.sha256(data).hexdigest()
    cc._policy(cc.Policy(data, sha))
    path = root / 'tools/setup_kit/car_catalog.py'
    python, count = PIN.subn('PACKAGED_POLICY_SHA256 = ' + repr(sha), path.read_text())
    if count != 1:
        raise ValueError('packaged policy pin must occur exactly once')
    header = ('/* Generated from car_approvals.json; regenerate both trust roots together. */\n'
              '#ifndef CAR_APPROVALS_PUBLIC_H\n#define CAR_APPROVALS_PUBLIC_H\n'
              '#define CAR_CATALOG_POLICY_JSON ' + json.dumps(data.decode('ascii')) + '\n'
              '#define CAR_CATALOG_POLICY_SHA256 "' + sha + '"\n#endif\n')
    return {path: python.encode(), root / HEADER: header.encode()}


def generate(root):
    outputs = trust_outputs(root, (root / POLICY).read_bytes())
    for path, data in outputs.items():
        path.write_bytes(data)


def verify_policy(root):
    data = (root / POLICY).read_bytes()
    for path, expected in trust_outputs(root, data).items():
        if path.read_bytes() != expected:
            raise ValueError('stale packaged trust artifact: ' + path.name)
    return json.loads(data)


def verify_contracts(root, contracts):
    """Verify measured review rows against the package and exercise every pin."""
    verify_policy(root)
    policy = cc.packaged_policy()
    if not isinstance(contracts, list) or not contracts:
        raise ValueError('nonempty measured contract list required')
    seen, platforms = set(), {}
    rejected = dict(inputs=0, outputs=0)
    for row in contracts:
        identity = (row['origin']['platform'], row['slot'])
        if identity in seen:
            raise ValueError('duplicate review contract')
        seen.add(identity)
        rows = cc.reservations()
        rows[row['slot']] = row
        cc.make_catalog(rows, policy)
        platforms[identity[0]] = platforms.get(identity[0], 0) + 1
        for kind, groups in (('inputs', row['inputs'].values()),
                             ('outputs', [row['outputs']])):
            for group in groups:
                for pin in group.values():
                    original = pin['sha256']
                    pin['sha256'] = ('1' if original[0] == '0' else '0') + original[1:]
                    try:
                        cc.make_catalog(rows, policy)
                    except cc.CatalogError as exc:
                        if 'conversion contract is not approved' not in str(exc):
                            raise
                        rejected[kind] += 1
                    else:
                        raise AssertionError('altered ' + kind + ' pin was approved')
                    finally:
                        pin['sha256'] = original
    return dict(contracts=len(seen), platforms=platforms, rejected=rejected)


def generate_selections(contracts):
    """Generate Setup metadata and its UI pin from policy-validated review rows."""
    from setup_kit import car_selection
    data = car_selection.generate(contracts, ROOT / SELECTIONS)
    path = ROOT / 'tools/installer/SetupApp.swift'
    text, count = re.subn(r'^let carSelectionsSHA256 = "[0-9a-f]{64}"$',
                         'let carSelectionsSHA256 = "' + hashlib.sha256(data).hexdigest() + '"',
                         path.read_text(), flags=re.M)
    if count != 1:
        raise ValueError('Setup metadata pin must occur exactly once')
    path.write_text(text)


def refreshed_converter_rows(rows):
    """Check executable dependencies separately from measured output inventories."""
    from setup_kit import car_import
    refreshed = copy.deepcopy(rows)
    for row in refreshed:
        game, platform = row['origin']['game'], row['origin']['platform']
        mw_names = car_import._mw_names(game, row['donors']['model'], row['origin']['folder'])
        available = row['inputs']['dependencies'].keys() | row['inputs']['donor'].keys()
        code, policy, _ = car_import._code(platform, sorted(set(mw_names) - available), game,
                                           mac_audio='AUDIO.BIN' in row['outputs'])
        row['inputs']['converter'] = code.pins
        row['converter']['policy'] = policy['sha256']
    return refreshed


def x360_donor_rows(rows):
    """Derive the donor variant of measured console outputs for logical exports."""
    donor_rows = []
    for row in rows:
        if (row['origin']['game'], row['origin']['platform']) != ('carbon', 'x360'):
            continue
        donor = copy.deepcopy(row)
        del donor['outputs']['AUDIO.BIN']
        donor['inputs']['source'] = {name: pin for name, pin in donor['inputs']['source'].items()
                                     if not name.startswith('SOUND/ENGINE/')}
        donor_rows.append(donor)
    return refreshed_converter_rows(donor_rows)


def measured_converter_rows(rows, pc, x360, mw, later_pc=None):
    """Run the importer writer; never infer outputs from an obsolete inventory."""
    from setup_kit import car_import, car_selection
    with tempfile.TemporaryDirectory() as td:
        work = Path(td).resolve()
        engine = car_import.CarImporter(work / 'catalog', work / 'work')
        measured = []
        for before in rows:
            game, platform = before['origin']['game'], before['origin']['platform']
            source = (pc if platform == 'pc' else x360) if game == 'carbon' else (later_pc or {})[game]
            result = engine.plan(source, mw_pc=mw, cars=[car_selection.spec(before)], measure=True)
            after = result['cars'][0]['contract']
            # Approval refresh must not silently bless changed source assets or
            # selection semantics. Only executable policy and outputs may move.
            expected = refreshed_converter_rows([before])[0]
            if (game, platform) == ('prostreet', 'pc'):
                from setup_kit import car_audio_pack
                attributes = (source / 'GLOBAL/ATTRIBUTES.BIN').read_bytes()
                candidates = (p.relative_to(source).as_posix() for p in (source / 'SOUND/ENGINE').iterdir() if p.is_file())
                paths = car_audio_pack.resource_paths(attributes, before['origin']['folder'], candidates, game='prostreet')
                expected['inputs']['source'] = {p: pin for p, pin in expected['inputs']['source'].items()
                                                if not p.startswith('SOUND/ENGINE/')}
                expected['inputs']['source'].update({p.upper(): cc.fingerprint(source / p) for p in paths})
            expected['outputs'] = after['outputs']
            if after != expected:
                raise ValueError('measured inputs/metadata changed: ' + before['origin']['folder'])
            measured.append(after)
            print('measured ' + before['origin']['platform'] + ' ' + before['origin']['folder'] +
                  ': ' + str(len(after['outputs'])) + ' outputs', flush=True)
        return measured


def generate_output_fixture(rows):
    """Keep one complete writer receipt per platform as an independent baseline."""
    samples = {}
    for row in rows:
        if row['origin']['game'] != 'carbon':
            continue
        samples.setdefault(row['origin']['platform'],
                           dict(slot=row['slot'], outputs=row['outputs'], converter=row['converter']))
    data = cc.encoded(samples)
    path = Path(__file__)
    text = path.read_text()
    for name, value in (('MEASURED_OUTPUTS', data.decode('ascii')),
                        ('MEASURED_OUTPUTS_SHA256', hashlib.sha256(data).hexdigest())):
        text, count = re.subn('^' + name + r' = .*$', lambda _: name + ' = ' + repr(value), text, flags=re.M)
        if count != 1:
            raise ValueError('measured output fixture pin must occur exactly once')
    path.write_text(text)


def refresh_carbon_source_variants(policy, previous, refreshed):
    """Carry already approved retail/decoded source equivalents to measured outputs."""
    from setup_kit import car_sources
    release = next(r for r in json.loads(car_sources.MANIFEST.read_bytes())['releases']
                   if (r.get('game', 'carbon'), r['platform']) == ('carbon', 'pc'))
    decoded = {r['path_hash']: r['decoded'] for r in release['files'] if 'decoded' in r}
    attributes = 'GLOBAL/ATTRIBUTES.BIN'
    retail = release['shared_variants'][0][car_sources.path_hash(attributes)]
    count = 0
    for before, after in zip(previous, refreshed):
        if (before['origin']['game'], before['origin']['platform']) != ('carbon', 'pc'):
            continue
        name = f"CARS/{before['origin']['folder']}/GEOMETRY.BIN"
        variants = policy['slots'][before['slot']]['variants']
        changes = [{attributes: retail}]
        pin = decoded.get(car_sources.path_hash(name))
        if pin is not None:
            changes += [{name: pin}, {name: pin, attributes: retail}]
        for change in changes:
            old, new = copy.deepcopy(before), copy.deepcopy(after)
            old['inputs']['source'].update(change)
            new['inputs']['source'].update(change)
            if old == before:
                continue
            original = cc.contract_hash(old)
            if not any(v['contract'] == original and v['compatibility'] == cc.slot_compatibility(old)
                       for v in variants):
                raise ValueError('unapproved source equivalence: ' + before['origin']['folder'])
            assert cc.slot_compatibility(old) == cc.slot_compatibility(new)
            variant = dict(contract=cc.contract_hash(new), compatibility=cc.slot_compatibility(new), legacy=7)
            if not any(v['contract'] == variant['contract'] for v in variants):
                variants.append(variant)
                count += 1
    return count


def generate_prostreet_runtime_pins(root, rows):
    """Reproduce native v5 car/package identities from measured writer receipts."""
    entries=sorted((r['origin']['folder'].lower(),r['outputs']['AUDIO.BIN']['sha256'])
                   for r in rows if r['origin']['game']=='prostreet')
    if len(entries)!=31 or len({name for name,_ in entries})!=31:
        raise ValueError('incomplete stock player package inventory')
    table='static const struct { const char *car, *sha; } prostreet_packages[] = {\n'
    table+=''.join('    {"'+name+'","'+sha+'"},\n' for name,sha in entries)+'};'
    path=root/'runtime/car_audio.c'
    text,count=re.subn(r'static const struct \{ const char \*car, \*sha; \} prostreet_packages\[\] = \{.*?\n\};',
                       lambda _:table,path.read_text(),flags=re.S)
    if count!=1:raise ValueError('native stock player pin table must occur once')
    path.write_text(text)


def refresh_converter_pins(pc, x360, mw, prostreet, undercover):
    """Regenerate existing variants from real writes, preserving variant ordering."""
    require_shipped_runtime()
    from setup_kit import car_selection
    rows = car_selection.load()
    refreshed = measured_converter_rows(rows, pc, x360, mw, dict(prostreet=prostreet, undercover=undercover))
    policy = verify_policy(ROOT)
    for before, after in zip(rows, refreshed):
        variants = policy['slots'][before['slot']]['variants']
        variant = dict(contract=cc.contract_hash(before), compatibility=cc.slot_compatibility(before))
        index = variants.index(variant)
        assert cc.slot_compatibility(before) == cc.slot_compatibility(after)
        replacement = dict(contract=cc.contract_hash(after), compatibility=cc.slot_compatibility(after))
        if replacement != variant:
            variants[index] = dict(variant, legacy=16)
            variants.append(replacement)
    for row in x360_donor_rows(refreshed):
        variants = policy['slots'][row['slot']]['variants']
        variant = dict(contract=cc.contract_hash(row), compatibility=cc.slot_compatibility(row), legacy=1)
        if variant not in variants:
            variants.append(variant)
    refresh_carbon_source_variants(policy, rows, refreshed)
    policy['revision'] = max(policy['revision'], 20)
    (ROOT / POLICY).write_bytes(cc.encoded(policy))
    generate(ROOT)
    cc.PACKAGED_POLICY_SHA256 = cc.digest(policy)
    generate_selections(refreshed)
    generate_output_fixture(refreshed)
    generate_prostreet_runtime_pins(ROOT, refreshed)


def verify_selections(root):
    from setup_kit import car_selection
    rows = car_selection.load(root / SELECTIONS)
    sha = hashlib.sha256((root / SELECTIONS).read_bytes()).hexdigest()
    if 'let carSelectionsSHA256 = "' + sha + '"' not in (root / 'tools/installer/SetupApp.swift').read_text():
        raise ValueError('stale Setup car selection pin')
    return rows


def later_pc_setup(mw, prostreet, undercover):
    """Use the shipped Python and published converters through the Setup CLI."""
    from setup_kit import backend, car_import, car_selection, car_convert, ps_namespace
    require_shipped_runtime()
    rows = car_selection.load()
    with tempfile.TemporaryDirectory(prefix='later-pc-setup-') as td:
        root = Path(td).resolve()
        for key in ('DATA', 'STATE', 'CACHE', 'LOGS', 'LEGACY'):
            os.environ.pop('NFSMW_SETUP_' + key, None)
        os.environ['NFSMW_SETUP_HOME'] = str(root / 'home')
        paths = backend.Paths(root / 'work')
        store = paths.data / 'car-catalog'

        def run(source, row, mw_source=mw):
            spec = root / 'car.json'
            spec.write_bytes(cc.encoded([car_selection.spec(row)]))
            out = io.StringIO()
            with redirect_stdout(out):
                status = backend.main(['--cars-import', '--carbon', str(source), '--cars-spec', str(spec),
                                       '--cars-mw-pc', str(mw_source), '--work', str(paths.work), '--events'])
            return status, [json.loads(line) for line in out.getvalue().splitlines()]

        chosen = []
        for game, source, car in (('prostreet', prostreet, '997GT2'),
                                   ('undercover', undercover, 'MER_SLR_722_07')):
            row = next(r for r in rows if r['origin']['game'] == game and r['origin']['folder'] == car)
            status, events = run(source, row)
            assert status == 0, events[-2:]
            chosen.append(row)
            active = cc.verify_current(store)
            assert not active.error and list(active.selected) == sorted(chosen, key=lambda r: r['slot']), active.error
            assert any(event['ev'] == 'cars-result' and event['verified'] for event in events), events[-2:]
            print(f'{game}: {row["origin"]["folder"]}, {len(row["outputs"])} output hashes approved', flush=True)

        pointer = store / 'current.json'
        previous = pointer.read_bytes()
        failed = next(r for r in rows if r['origin']['game'] == 'prostreet' and r != chosen[0])
        with mock.patch.object(ps_namespace, 'convert', side_effect=car_convert.FormatError('test parser error')) as route:
            status, events = run(prostreet, failed)
        assert route.call_count == 1 and status == 1, events[-2:]
        messages = [event['msg'] for event in events if event['ev'] == 'error']
        assert messages == ['Setup could not convert the selected car. Check the game files and retry.'], messages
        assert pointer.read_bytes() == previous and not list(root.rglob('.txn-*'))
        print('failed conversion: plain Setup message, current pointer intact, zero unfinished stages', flush=True)

        boundary = car_import.CarImporter._boundary

        def tamper(engine, phase, *args):
            boundary(engine, phase, *args)
            if phase == 'extracted':
                stage, = engine.work.glob('.txn-*')
                file = stage / 'mw/CARS' / failed['donors']['model'] / 'GEOMETRY.BIN'
                with file.open('r+b') as binary:
                    first = binary.read(1)
                    binary.seek(0)
                    binary.write(bytes([first[0] ^ 1]))

        with mock.patch.object(car_import.CarImporter, '_boundary', tamper), \
             mock.patch.object(ps_namespace, 'convert', side_effect=AssertionError('decoder reached')) as route:
            status, events = run(prostreet, failed)
        assert route.call_count == 0 and status == 1, events[-2:]
        assert [event['msg'] for event in events if event['ev'] == 'error'] == [
            'A verified car input changed before conversion.']
        assert pointer.read_bytes() == previous and not list(root.rglob('.txn-*'))
        print('changed snapshot: zero converter calls, current pointer intact', flush=True)

        # Use only the selected MW inputs so size and fingerprint controls do
        # not modify the supplied installation or rely on its other files.
        copy = root / 'mw-copy'
        pins = failed['inputs']['donor'] | failed['inputs']['dependencies']
        for name in pins:
            target = copy / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(car_import._mw_asset(mw, name, 'prostreet'), target)
        file = copy / 'CARS' / failed['donors']['model'] / 'GEOMETRY.BIN'
        with file.open('r+b') as binary:
            first = binary.read(1)
            binary.seek(0)
            binary.write(bytes([first[0] ^ 1]))
        for message in ('The selected MW donor or template files do not match the approved copies for this car.',
                        'The selected car and MW files exceed the conversion size limit.'):
            with mock.patch.object(ps_namespace, 'convert', side_effect=AssertionError('decoder reached')) as route:
                status, events = run(prostreet, failed, copy)
            assert route.call_count == 0 and status == 1, events[-2:]
            assert [event['msg'] for event in events if event['ev'] == 'error'] == [message]
            assert pointer.read_bytes() == previous and not list(root.rglob('.txn-*'))
            with file.open('r+b') as binary:
                binary.truncate(65 << 20)
        print('MW fingerprint and input budget: two refusals before the converter', flush=True)


def native_reader(root, work):
    source = work / 'reader-main.c'
    source.write_text('#include "car_catalog.h"\n#include <stdio.h>\n'
        'int main(int n,char **v){if(n!=3)return 2;'
        'int r=car_catalog_init(v[1],1);'
        'printf("%d %zu %s\\n",r,car_catalog_count(),car_catalog_error());'
        'return r<0 || car_catalog_count()!=(size_t)atoi(v[2]);}\n')
    # Include stdlib separately so the harness has no implicit declarations.
    source.write_text('#include <stdlib.h>\n' + source.read_text())
    binary = work / 'reader'
    command(['clang', '-std=c11', '-Werror', '-DNFS_PUBLIC=1', '-I', str(root / 'runtime'),
             str(source), str(root / 'runtime/car_grip.c'), str(root / 'runtime/car_performance.c'), str(root / 'runtime/car_catalog.c'), '-o', str(binary)])
    return binary


def command(args, **kwargs):
    result = subprocess.run(args, capture_output=True, text=True, timeout=180, **kwargs)
    if result.returncode:
        raise RuntimeError(f'command exit {result.returncode}: ' + result.stdout[-1500:] + result.stderr[-1500:])
    return result


def export_payload(base, fault=None):
    """Run the actual publication importer on a fresh index, without commits."""
    spec = importlib.util.spec_from_file_location('car_release_publish', ROOT / 'tools/publish/import.py')
    publish = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = publish
    spec.loader.exec_module(publish)
    repo, public = base / 'indexed', base / 'public'
    repo.mkdir()
    names = command(['git', '-C', str(ROOT), 'ls-files', '--cached', '--others',
                     '--exclude-standard', '-z']).stdout.split('\0')
    names += ['tools/publish/denylist_carbon.txt', POLICY, HEADER, SELECTIONS, 'tools/setup_kit/car_selection.py',
              'tests/setup/test_car_release.py', 'runtime/save_file_fixture.py']
    names += ['tools/setup_kit/carbon_source.py', 'tools/setup_kit/car_audio_x360.py']
    names += [f'tools/setup_kit/{stem}.py' for stem in
              ('ps_attrib', 'ps_namespace', 'uc_attrib', 'uc_batch', 'ucpc_car', 'v25_batch', 'v25_car')]
    allowed = publish.patterns(ROOT / 'tools/publish/allowlist.txt')
    denied = publish.deny_patterns(ROOT / 'tools/publish')
    for name in sorted(set(names) - {''}):
        src = ROOT / name
        if not src.is_file() or src.is_symlink() or (not name.startswith('tools/publish/') and
                (publish.deny(name, denied) or not any(publish.matches(name, p) for p in allowed))):
            continue
        dst = repo / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    if fault:
        name, data = fault
        dst = repo / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        # Deliberately select the forbidden candidate: the scanner must refuse.
        with (repo / 'tools/publish/allowlist.txt').open('a') as out:
            out.write('\n' + name + '\n')
        deny = repo / 'tools/publish/denylist.txt'
        deny.write_text('\n'.join(p for p in deny.read_text().splitlines()
                                 if not publish.matches(name, p.strip())) + '\n')
    command(['git', '-C', str(repo), 'init', '-q'])
    command(['git', '-C', str(repo), 'add', '-f', '-A'])
    result = subprocess.run([sys.executable, '-I', str(repo / 'tools/publish/import.py'),
                             str(repo), str(public), '--summary-only'],
                            capture_output=True, text=True, timeout=180)
    if result.returncode:
        return result, public
    (public / 'tools/publish/owned.txt').unlink()
    shutil.rmtree(public / 'tools/publish')
    forbidden = [p.relative_to(public).as_posix() for p in public.rglob('*')
                 if p.is_file() and publish.deny(p.relative_to(public).as_posix(), denied)]
    if forbidden:
        raise AssertionError('forbidden exported files: ' + repr(forbidden))
    archive = base / 'source.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        for entry in sorted(public.iterdir()):
            tar.add(entry, arcname=entry.name)
    shutil.rmtree(repo)
    shutil.rmtree(public)
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            if (Path(member.name).is_absolute() or '..' in Path(member.name).parts or
                    not (member.isfile() or member.isdir())):
                raise ValueError('unsafe generated payload member')
        tar.extractall(public)
    return result, public


def synthetic(root, install=True):
    """Exercise unmodified public CLI in children with a disposable test policy."""
    if (root / '.git').exists() or (root / 'tools/publish/import.py').exists():
        raise ValueError('synthetic approvals require a disposable exported source copy')
    import test_car_platform_conversion as fixture
    import test_car_sources as sf
    import test_car_import as fi
    from setup_kit import car_import as ci, car_sources as cs, huff
    from unittest import mock
    workspace = tempfile.TemporaryDirectory(prefix='synthetic-cars-')
    f = fixture.PlatformConversion('runTest')
    with mock.patch.object(fixture, 'ROOT', Path(workspace.name).resolve()):
        f.setUp()
    console = f.root / 'x360'
    (console / 'FILES').mkdir()
    for name in ('CARS', 'GLOBAL'):
        (console / name).rename(console / 'FILES' / name)
    original = {p: p.read_bytes() if p.exists() else None for p in (root / POLICY, root / HEADER,
                root / 'tools/setup_kit/car_catalog.py', cs.MANIFEST)}
    old_pin = cc.PACKAGED_POLICY_SHA256
    try:
        payload = b'synthetic exported HUFF dependency proof'
        assert huff.decode(huff.fixture(payload)) == payload
        pc_files = {p.relative_to(f.root / 'pc').as_posix().upper(): p.read_bytes()
                    for p in (f.root / 'pc').rglob('*') if p.is_file()}
        cs.MANIFEST.write_text(json.dumps(dict(schema=1, releases=[
            sf.release('pc', sf.fake_pe(), pc_files),
            sf.release('x360', sf.fake_xex(), f.files)])))
        policy = dict(schema=1, revision=1,
                      slots=[dict(slot=s, identity=None, variants=[]) for s in range(90)])
        spec = fi.spec()
        selection = f.root / 'selection.json'
        selection.write_text(json.dumps([spec]))
        rows, plans = {}, {}
        engine = ci.CarImporter(f.root / 'planning-catalog', f.root / 'planning-cache', margin_bytes=4096)
        for platform in ('pc', 'x360'):
            plan = engine.plan(f.root / platform, mw_pc=f.root / 'mw', cars=[spec], measure=True)
            row = plan['cars'][0]['contract']
            for name, path in (('huff.py', root / 'tools/setup_kit/huff.py'),
                               ('huff.h', root / 'runtime/huff.h')):
                assert row['inputs']['converter'][name] == cc.fingerprint(path)
            if platform == 'x360':
                for name in ('x360_tex.py', 'texture_compare.py'):
                    assert row['inputs']['converter'][name] == cc.fingerprint(root / 'tools_snapshot' / name)
            rows[platform], plans[platform] = row, plan
            slot = policy['slots'][row['slot']]
            slot['identity'] = row['identity']
            slot['variants'].append(dict(contract=cc.contract_hash(row), compatibility=cc.slot_compatibility(row)))
        if install:
            (root / POLICY).write_bytes(cc.encoded(policy))
            generate(root)
            cc.PACKAGED_POLICY_SHA256 = cc.digest(policy)
            contracts = f.root / 'review-contracts.json'
            contracts.write_bytes(cc.encoded(list(rows.values())))
            reviewed = json.loads(command([sys.executable, '-I',
                str(root / 'tests/setup/test_car_release.py'), '--verify-contracts', str(contracts)]).stdout)
            assert reviewed['platforms'] == dict(pc=1, x360=1)
            assert reviewed['rejected']['inputs'] == sum(
                len(pins) for row in rows.values() for pins in row['inputs'].values())
            assert reviewed['rejected']['outputs'] == 14
        native = native_reader(root, f.root) if install else None
        before = f.snapshot()
        reports = []
        for platform in ('pc', 'x360'):
            home = f.root / ('home-' + platform)
            save = home / 'data/NFS Most Wanted/profile/career.bin'
            save.parent.mkdir(parents=True)
            save.write_bytes(b'synthetic career must remain byte-identical')
            save_before = save.read_bytes()
            env = {k: v for k, v in os.environ.items() if not k.startswith(('NFSMW_SETUP_', 'PYTHON'))}
            env.update(NFSMW_SETUP_HOME=str(home), HOME=str(home))
            calls = []
            def invoke(*args, good=True):
                result = subprocess.run([sys.executable, '-I', str(root / 'tools/setup.py'), *args, '--events'],
                                        cwd=f.root, env=env, capture_output=True, text=True, timeout=90)
                events = [json.loads(line) for line in result.stdout.splitlines()]
                if (result.returncode == 0) != good:
                    raise AssertionError(result.stdout[-2000:] + result.stderr[-2000:])
                if good:
                    assert events[-1]['ev'] == 'cars-result', events
                    assert not any(e['ev'] in ('done', 'ready') for e in events)
                assert save.read_bytes() == save_before
                calls.append(args[0])
                return events[-1]
            args = ['--carbon', str(f.root / platform), '--cars-spec', str(selection),
                    '--cars-mw-pc', str(f.root / 'mw')]
            invoke('--cars-check', *args)
            imported = invoke('--cars-import', *args)
            assert imported['selection'] == [11]
            pointer_path = home / 'data/car-catalog/current.json'
            pointer = pointer_path.read_bytes()
            invoke('--cars-list')
            invoke('--cars-remove', '11')
            assert json.loads(pointer_path.read_bytes())['selection'] == []
            invoke('--cars-restore', imported['generation'])
            assert pointer_path.read_bytes() == pointer
            invoke('--cars-import', *args)
            assert pointer_path.read_bytes() == pointer
            verified = cc.verify_current(home / 'data/car-catalog')
            assert not verified.error and [r['id'] for r in verified.selected] == [95]
            assert len(verified.catalog['cars']) == 90
            row = verified.selected[0]
            assert row == rows[platform]
            if native:
                command([str(native), str(home / 'data/car-catalog'), '1'])
            # Each contract component independently changes approval meaning.
            mutations = []
            changed = copy.deepcopy(row)
            changed['origin']['platform'] = 'x360' if platform == 'pc' else 'pc'
            mutations.append(changed)
            changed = copy.deepcopy(row)
            changed['converter']['policy'] = '0' * 64
            mutations.append(changed)
            for group in cc.INPUT_GROUPS:
                changed = copy.deepcopy(row)
                pin = next(iter(changed['inputs'][group].values()))
                pin['sha256'] = '0' * 64
                mutations.append(changed)
            for name in row['outputs']:
                changed = copy.deepcopy(row)
                changed['outputs'][name]['sha256'] = '0' * 64
                mutations.append(changed)
            for changed in mutations:
                altered = cc.reservations()
                altered[11] = changed
                try:
                    cc.make_catalog(altered, cc.packaged_policy())
                except cc.CatalogError:
                    pass
                else:
                    raise AssertionError('changed conversion contract was approved')
            reports.append(dict(platform=platform, commands=calls, id=row['id'],
                outputs=len(row['outputs']), receipt=plans[platform]['receipt'],
                volumes=plans[platform]['volumes'], rejected_pin_mutants=len(mutations),
                public_huff_sha256=row['inputs']['converter']['huff.py']['sha256']))
        assert f.snapshot() == before
        # Every imported module must belong to this relocated tree or Python's
        # installed standard/site libraries. A sibling source checkout is absent.
        for module in tuple(sys.modules.values()):
            filename = getattr(module, '__file__', None)
            if filename and (getattr(module, '__name__', '').startswith('setup_kit') or
                             getattr(module, '__name__', '').startswith('test_car') or
                             getattr(module, '__name__', '') in ('x360_tex', 'texture_compare')):
                assert Path(filename).resolve().is_relative_to(root)
        print(json.dumps(dict(synthetic=True, reports=reports, source_unchanged=True,
                              saves_unchanged=True, commands=12), sort_keys=True))
    finally:
        cc.PACKAGED_POLICY_SHA256 = old_pin
        for path, data in original.items():
            if data is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(data)
        f.doCleanups()
        workspace.cleanup()


class CarRelease(unittest.TestCase):
    def test_existing_inputs_and_selection_semantics_are_unchanged(self):
        from setup_kit import car_selection
        new_slots = {r[0] for r in ADDITIONS} | {i - cc.FIRST_ID for i in CARBON_PC_PROMOTIONS.values()}
        original = copy.deepcopy([r for r in car_selection.load()
                                  if r['origin']['game'] == 'carbon' and r['slot'] not in new_slots])
        # Executable and writer pins are remeasured; source, donors and selection
        # semantics retain their original reviewed bytes.
        for row in original:
            row['inputs']['source'] = {name: pin for name, pin in row['inputs']['source'].items()
                                       if not name.startswith('SOUND/ENGINE/')}
            # The additional retail dependency is checked in the Carbon contract test.
            row['inputs']['dependencies'].pop('GLOBAL/attributes.bin', None)
            del row['inputs']['converter'], row['converter'], row['outputs']
        self.assertEqual(len(original), 19)
        self.assertEqual(hashlib.sha256(cc.encoded(original)).hexdigest(),
                         '6aed512190fb5c46a5eaf37bb2d5f1451758cab43b3f887057fa92912fd8516a')
        policy = verify_policy(ROOT)
        slots = {r['slot'] for r in original}
        previous = copy.deepcopy([r for r in policy['slots'] if r['slot'] in slots])
        for row in previous:
            row['variants'] = [variant for variant in row['variants'] if 'legacy' not in variant]
            for variant in row['variants']:
                del variant['contract']
        self.assertEqual(hashlib.sha256(cc.encoded(previous)).hexdigest(),
                         'f5c936c989e90de4355efde74960e6b09ae75e9cd1ed024de73c831a944e7d57')

    def test_additional_contracts_and_broken_pins(self):
        from setup_kit import car_selection
        folders = {r[1] for r in ADDITIONS}
        rows = [r for r in car_selection.load() if r['origin']['folder'] in folders]
        expected = {(p, f) for p in ('pc', 'x360') for f in folders}
        expected.remove(('x360', 'MUSTANGSHLBYN'))
        self.assertEqual({(r['origin']['platform'], r['origin']['folder']) for r in rows}, expected)
        proof = verify_contracts(ROOT, rows)
        self.assertEqual(proof['platforms'], dict(pc=6, x360=5))
        self.assertEqual(proof['contracts'], 11)
        self.assertEqual(proof['rejected']['inputs'], 537)
        self.assertEqual(proof['rejected']['outputs'], 110)
        print('additional contracts: ' + json.dumps(proof, sort_keys=True))

    def test_multigame_roster(self):
        """Original package fails at 30/75 permanent slots. Expanded package passes at 75/75."""
        from collections import Counter
        from setup_kit import backend, car_selection
        rows = car_selection.load()
        counts = Counter((r['origin']['game'], r['origin']['platform']) for r in rows)
        self.assertEqual(len({r['slot'] for r in rows}), 75)
        self.assertEqual(counts, {('carbon', 'pc'): 30, ('carbon', 'x360'): 14,
                                  ('prostreet', 'pc'): 31, ('undercover', 'pc'): 14})
        # Compact digests over the independently approved local output hashes.
        approvals = {
            'prostreet': (217, '71644f705ef737a3aff2e9c43acb31c1595ad88e084c58c89f40c2cc2b3ebc1a'),
            'undercover': (56, 'd1144f863fa1f83e03b9aa4b9a7434a3c896a054082dc4d1cf20d3b982e6591a'),
        }
        for game, (count, digest) in approvals.items():
            by_game = [r for r in rows if r['origin']['game'] == game]
            hashes = {r['origin']['folder']: {f: pin['sha256'] for f, pin in r['outputs'].items()
                                              if f != 'PERFORMANCE.BIN'} for r in by_game}
            self.assertEqual(sum(map(len, hashes.values())), count)
            self.assertEqual(cc.digest(hashes), digest)
            with tempfile.TemporaryDirectory() as td:
                path = Path(td).resolve() / 'selection.json'
                specs = [car_selection.spec(r) for r in by_game]
                path.write_bytes(cc.encoded(specs))
                self.assertEqual(backend.car_specs(path), specs)

    def test_carbon_pc_roster(self):
        from setup_kit import car_selection
        pc = {r['origin']['folder']: r for r in car_selection.load()
              if (r['origin']['game'], r['origin']['platform']) == ('carbon', 'pc')}
        added = {name: pc[name] for name in CARBON_PC_PROMOTIONS if name in pc}
        self.assertEqual({name: row['id'] for name, row in added.items()}, CARBON_PC_PROMOTIONS)
        self.assertEqual(len(pc), 30)
        self.assertEqual(sum('AUDIO.BIN' in row['outputs'] for row in pc.values()), 30)
        outputs = {name: {file: pin['sha256'] for file, pin in row['outputs'].items()
                          if file not in ('AUDIO.BIN', 'PERFORMANCE.BIN')} for name, row in added.items()}
        self.assertEqual(sum(map(len, outputs.values())), 112)
        # Canonical SHA-256 of the independently approved local output hashes.
        self.assertEqual(cc.digest(outputs), '1896af0e64d67ada0e6ba3ce0df87afab5ffb428dbf83d94b67d66d88b91f199')

    def test_issue24_retail_carbon_attributes_contracts(self):
        from setup_kit import car_selection
        pin = dict(size=1035584, sha256='d64234661f8226fe24abd45eae7f5c24ce400a214526a4d23d6b421e75d820e4')
        policy = verify_policy(ROOT)
        accepted = 0
        for row in car_selection.load():
            if (row['origin']['game'], row['origin']['platform']) != ('carbon', 'pc'):
                continue
            row['inputs']['source']['GLOBAL/ATTRIBUTES.BIN'] = pin
            contract = cc.contract_hash(row)
            accepted += any(v['contract'] == contract and v['compatibility'] == cc.slot_compatibility(row)
                            for v in policy['slots'][row['slot']]['variants'])
        self.assertEqual(accepted, 30)

    def test_issue24_decoded_carbon_geometry_contracts(self):
        from setup_kit import car_selection, car_sources

        releases = json.loads(car_sources.MANIFEST.read_bytes())['releases']
        release = next(r for r in releases if (r.get('game', 'carbon'), r['platform']) == ('carbon', 'pc'))
        decoded = {r['path_hash']: r['decoded'] for r in release['files'] if 'decoded' in r}
        self.assertEqual(len(decoded), 28)
        attributes = 'GLOBAL/ATTRIBUTES.BIN'
        retail_pin = release['shared_variants'][0][car_sources.path_hash(attributes)]
        policy = cc.packaged_policy()
        for row in car_selection.load():
            if (row['origin']['game'], row['origin']['platform']) != ('carbon', 'pc'):
                continue
            name = f"CARS/{row['origin']['folder']}/GEOMETRY.BIN"
            pin = decoded.pop(car_sources.path_hash(name), None)
            if pin is None:
                continue
            with self.subTest(car=row['origin']['folder']):
                rows = cc.reservations()
                rows[row['slot']] = row
                source = row['inputs']['source']
                source[name] = pin
                cc.make_catalog(rows, policy)
                source[attributes] = retail_pin
                cc.make_catalog(rows, policy)
        self.assertEqual(decoded, {})

    def test_carbon_performance_contracts(self):
        from setup_kit import car_performance, car_selection
        rows = car_selection.load()
        carbon_outputs = {(row['origin']['platform'], row['origin']['folder']): row['outputs']
                          for row in rows if row['origin']['game'] == 'carbon'}
        self.assertEqual(len(carbon_outputs), 44)
        retail_attributes = next(row['inputs']['dependencies']['GLOBAL/ATTRIBUTES.BIN']
                                 for row in rows if row['origin']['game'] == 'prostreet')
        siblings = 0
        for row in rows:
            if row['origin']['game'] != 'carbon':
                continue
            pin = row['inputs']['dependencies'].get('GLOBAL/attributes.bin')
            is_sibling = row['origin']['folder'] in car_performance.SIBLINGS
            self.assertEqual(pin, retail_attributes if is_sibling else None)
            siblings += is_sibling
        self.assertEqual(siblings, 12)
        for (platform, folder), outputs in carbon_outputs.items():
            self.assertGreater(outputs['AUDIO.BIN']['size'], 68)
            pin = outputs['PERFORMANCE.BIN']
            self.assertEqual(pin['size'], 344)
            if platform == 'x360':
                for name in ('PERFORMANCE.BIN', 'WHEELFIT.BIN', 'GRIP.BIN'):
                    self.assertEqual(outputs[name], carbon_outputs['pc', folder][name])

    def test_prostreet_performance_contracts(self):
        from setup_kit import car_performance, car_selection
        rows = [row for row in car_selection.load() if row['origin']['game'] == 'prostreet']
        self.assertEqual(len(rows), 31)
        for row in rows:
            size = row['outputs']['PERFORMANCE.BIN']['size']
            if row['origin']['folder'] in car_performance.SIBLINGS:
                self.assertEqual(size, 344)
            else:
                self.assertTrue(404 <= size <= 452 and (size-332) % 12 == 0)

    def test_undercover_performance_contracts(self):
        from setup_kit import car_performance, car_selection
        rows = [row for row in car_selection.load() if row['origin']['game'] == 'undercover']
        self.assertEqual(len(rows), 14)
        for row in rows:
            size = row['outputs']['PERFORMANCE.BIN']['size']
            if row['origin']['folder'] in car_performance.SIBLINGS:
                self.assertEqual(size, 344)
            else:
                self.assertTrue(392 <= size <= 452 and (size-332) % 12 == 0)
            self.assertIn('GLOBAL/ATTRIBUTES.BIN', row['inputs']['dependencies'])

    def test_packaged_selections_and_tamper_controls(self):
        from setup_kit import car_selection as selection
        rows = selection.load()
        self.assertEqual({p: sum(r['origin']['platform'] == p for r in rows)
                          for p in ('pc', 'x360')}, dict(pc=75, x360=14))
        data = (ROOT / SELECTIONS).read_bytes()
        self.assertIn('let carSelectionsSHA256 = "' + hashlib.sha256(data).hexdigest() + '"',
                      (ROOT / 'tools/installer/SetupApp.swift').read_text())
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'selections.json'
            self.assertEqual(selection.generate(list(reversed(rows)), path), data)
            faults = []
            for key in ('display', 'origin', 'slot', 'parts', 'outputs'):
                altered = copy.deepcopy(rows)
                row = altered[0]
                if key == 'slot':
                    row[key] += 1
                elif key == 'parts':
                    row[key][0]['keys'][0] += '_EDITED'
                elif key == 'outputs':
                    row[key]['GEOMETRY.BIN']['sha256'] = '0' * 64
                else:
                    row[key][next(iter(row[key]))] += '_EDITED'
                faults.append(altered)
            faults += [rows[:-1], rows + [rows[0]]]
            for altered in faults:
                path.write_bytes(cc.encoded(altered))
                with self.assertRaises(cc.CatalogError):
                    selection.load(path)
        self.assertEqual(len(faults), 7)
        print(f'selection metadata: PASS {len(rows)} approved; FAIL controls 7/7 refused')

    @unittest.skipUnless((ROOT / 'tools/publish/import.py').is_file(), 'exporter belongs to the source checkout')
    def test_current_converter_contracts_approve(self):
        from setup_kit import car_selection
        rows = car_selection.load()
        # Setup measures the exported tree (published HUFF) under its locked Python/NumPy;
        # the private checkout's interpreter and HUFF never define a packaged pin.
        with tempfile.TemporaryDirectory(prefix='car-pins-') as td:
            result, public = export_payload(Path(td).resolve())
            self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr[-1000:])
            script = str(public / 'tests/setup/test_car_release.py')
            runs = {'locked identity': [sys.executable, '-I', script, '--converter-pins', '--emulate-locked']}
            if (ROOT / SETUP_PYTHON).is_file():
                runs['shipped Python'] = [str(ROOT / SETUP_PYTHON), '-I', '-B', script, '--converter-pins']
            for label, args in runs.items():
                report = json.loads(command(args, cwd=public).stdout.splitlines()[-1])
                print(f'exported converter pins ({label}): {report["mismatched"]}/{report["rows"]} rows '
                      f'mismatched, {report["accepted"]}/{report["rows"]} measured contracts approved')
                self.assertEqual(report['mismatched'], 0, report['first'])
                self.assertEqual(report['accepted'], len(rows))
                self.assertEqual(report['console_donor_approved'], 14)
        self.assertEqual(hashlib.sha256(MEASURED_OUTPUTS.encode('ascii')).hexdigest(),
                         MEASURED_OUTPUTS_SHA256)
        measured = json.loads(MEASURED_OUTPUTS)
        self.assertEqual(set(measured), {'pc', 'x360'})
        inventories = 0
        for row in rows:
            if row['origin']['game'] != 'carbon':
                continue
            sample = measured[row['origin']['platform']]
            with self.subTest(platform=row['origin']['platform'], slot=row['slot']):
                self.assertEqual(row['converter'], sample['converter'], 'remeasure changed converter')
                self.assertEqual(set(row['outputs']), set(sample['outputs']), 'measured output inventory')
                if row['slot'] == sample['slot']:
                    self.assertEqual(row['outputs'], sample['outputs'], 'measured output pins')
                inventories += 1
        print(f'current converter: {inventories}/{len(rows)} measured inventories')

    def test_importer_pins_native_huff_dependency(self):
        from setup_kit import car_import
        code, _, _ = car_import._code('pc', [])
        self.assertIn('huff.h', code.pins)
        self.assertEqual(code.pins['huff.h'], cc.fingerprint(ROOT / 'runtime/huff.h'))

    def test_offline_discovers_release_proof(self):
        script = ROOT / 'runtime/offline_tests.sh'
        env = dict(os.environ, OFFLINE_TESTS_PLAN='1', OFFLINE_TIER='full')
        result = command(['sh', str(script)], cwd=ROOT, env=env)
        self.assertIn('test_car_release', result.stdout.split())
        original = script.read_text()
        broken = original.replace(' tools/publish/test_*.py tests/setup/test_*.py runtime/defaults_check.py;',
                                  ' tools/publish/test_*.py runtime/defaults_check.py;')
        self.assertNotEqual(original, broken)
        missing = command(['sh', '-c', broken, str(script)], cwd=ROOT, env=env)
        self.assertNotIn('test_car_release', missing.stdout.split())

    def test_generated_trust_artifacts(self):
        doc = verify_policy(ROOT)
        self.assertEqual(len(doc['slots']), 90)
        self.assertEqual(doc['revision'], 20)

    def test_prostreet_stock_player_audio_is_approved(self):
        from setup_kit import car_selection
        rows=[r for r in car_selection.load() if r['origin']['game']=='prostreet']
        self.assertEqual(len(rows),31)
        for row in rows:
            names={n for n in row['inputs']['source'] if n.startswith('SOUND/ENGINE/')}
            self.assertEqual(len(names),2)
            self.assertTrue(all(n.endswith('.TMX') for n in names))
            self.assertGreater(row['outputs']['AUDIO.BIN']['size'],68)
        row=next(r for r in rows if r['origin']['folder']=='VEYRON')
        self.assertEqual(row['outputs']['AUDIO.BIN'],dict(size=2779992,
            sha256='5fef0aea88488249d4d0064343686fb2f0c586049c4bdc6dfbd2475f1d9b0ada'))
        self.assertEqual({n for n in row['inputs']['source'] if n.startswith('SOUND/ENGINE/')},
            {'SOUND/ENGINE/CAR_106-ENGINE.TMX','SOUND/ENGINE/CAR_126-EXHAUST.TMX'})

    def test_pre_stock_player_audio_approvals_are_retained(self):
        doc=verify_policy(ROOT)
        entries=sorted((slot['slot'],v['contract'],v['compatibility'])
                       for slot in doc['slots'] for v in slot['variants'] if v.get('legacy')==15)
        self.assertEqual(len(entries),89)
        self.assertEqual(cc.digest(entries),'6e2b89a2f42baf6c249671c1279d0b40b730eaaa1bcceff3fce5b19d71eddf1f')

    def test_pre_lamp_converter_approvals_are_retained(self):
        doc = verify_policy(ROOT)
        entries = sorted((slot['slot'], variant['contract'], variant['compatibility'])
                         for slot in doc['slots'] for variant in slot['variants']
                         if variant.get('legacy') == 12)
        self.assertEqual(len(entries), 134)
        self.assertEqual(cc.digest(entries), 'e976bc115da4b30a7407697528e0314cd9886a0129774ba042868a27484db96a')

    def test_retained_approvals_match_public_releases(self):
        """Preserve 325 audio-era legacy contracts and every refresh of the console donor variants."""
        doc = verify_policy(ROOT)
        entries = sorted((slot['slot'], variant['contract'], variant['compatibility'])
                         for slot in doc['slots'] for variant in slot['variants']
                         if variant.get('legacy') in (1, 2))
        self.assertEqual(len(entries), 493)
        self.assertEqual(cc.digest(entries), 'f3e9523fef1112e71752112f6c6fd8bc1fb23deb6b2f2c12fc171484a7bd1449')

    def test_pre_performance_approvals_are_retained(self):
        doc = verify_policy(ROOT)
        entries = sorted((slot['slot'], variant['contract'], variant['compatibility'])
                         for slot in doc['slots'] for variant in slot['variants']
                         if variant.get('legacy') == 2)
        self.assertEqual(len(entries), 89)
        self.assertEqual(cc.digest(entries), '2ff48808b350491eb5fc0254f6d6f43f4aed407c4978a0fb8c68e7224ee70947')

    def test_cpf1_approvals_are_retained(self):
        doc = verify_policy(ROOT)
        entries = sorted((slot['slot'], variant['contract'], variant['compatibility'])
                         for slot in doc['slots'] for variant in slot['variants']
                         if variant.get('legacy') == 3)
        self.assertEqual(len(entries), 89)
        self.assertEqual(cc.digest(entries), 'dc0bc6d5ada5fffe7e2857918c3e5e20bfc10b433adec1e725c4fcba2a0a413a')

    def test_cpf2_approvals_are_retained(self):
        doc = verify_policy(ROOT)
        entries = sorted((slot['slot'], variant['contract'], variant['compatibility'])
                         for slot in doc['slots'] for variant in slot['variants']
                         if variant.get('legacy') == 4)
        self.assertEqual(len(entries), 89)
        self.assertEqual(cc.digest(entries), '4b170e89a4b3813e38441be3e2a217d6be751bf1732336b63181849195c17352')

    def test_both_revision_seven_approvals_are_retained(self):
        doc = verify_policy(ROOT)
        entries = sorted((slot['slot'], variant['contract'], variant['compatibility'])
                         for slot in doc['slots'] for variant in slot['variants']
                         if variant.get('legacy') == 5)
        self.assertEqual(len(entries), 178)
        self.assertEqual(cc.digest(entries), '9856fd60933846cf3a1916997eae63647d6aed2c3736a2fd22fc070eccd03cef')

    def test_retail_attributes_approvals_are_retained(self):
        doc = verify_policy(ROOT)
        entries = sorted((slot['slot'], variant['contract'], variant['compatibility'])
                         for slot in doc['slots'] for variant in slot['variants']
                         if variant.get('legacy') == 6)
        self.assertEqual(len(entries), 133)
        self.assertEqual(cc.digest(entries), '822696985b2a24deba9c3a8bfe4eefffcda6026ec2244307ba633022956e1ed5')

    def test_prostreet_performance_approvals_are_retained(self):
        """The 89 current contracts of the ProStreet performance policy stay approved."""
        doc = verify_policy(ROOT)
        entries = sorted((slot['slot'], variant['contract'], variant['compatibility'])
                         for slot in doc['slots'] for variant in slot['variants']
                         if variant.get('legacy') == 8)
        self.assertEqual(len(entries), 89)
        self.assertEqual(cc.digest(entries), '87769c5a7658feb275f1ed4a8a0645d71c90c0802d421ceda944a6214cc5304c')

    def test_pre_undercover_performance_approvals_are_retained(self):
        """The 89 current contracts of revision 11 (before Undercover performance) stay approved."""
        doc = verify_policy(ROOT)
        entries = sorted((slot['slot'], variant['contract'], variant['compatibility'])
                         for slot in doc['slots'] for variant in slot['variants']
                         if variant.get('legacy') == 9)
        self.assertEqual(len(entries), 89)
        self.assertEqual(cc.digest(entries), '412f823b2050c35d3e304f6afd71f95b23cf60de20e2fb998410e9bb4539522b')

    def test_pre_sibling_performance_approvals_are_retained(self):
        doc = verify_policy(ROOT)
        entries = sorted((slot['slot'], variant['contract'], variant['compatibility'])
                         for slot in doc['slots'] for variant in slot['variants']
                         if variant.get('legacy') == 10)
        self.assertEqual(len(entries), 89)
        self.assertEqual(cc.digest(entries), '764df0117cb07ddce77220111eab25159718e84736b272e032e87ab47cd6687c')

    def test_pre_torque_spline_approvals_are_retained(self):
        doc = verify_policy(ROOT)
        entries = sorted((slot['slot'], variant['contract'], variant['compatibility'])
                         for slot in doc['slots'] for variant in slot['variants']
                         if variant.get('legacy') == 13)
        self.assertEqual(len(entries), 89)
        self.assertEqual(cc.digest(entries), 'ab7cc51a1a9ed240a32b2e4d5ed1e46a11c1bad5427fc091d724b225025258df')

    def test_pre_cache_rename_approvals_are_retained(self):
        doc = verify_policy(ROOT)
        entries = sorted((slot['slot'], variant['contract'], variant['compatibility'])
                         for slot in doc['slots'] for variant in slot['variants']
                         if variant.get('legacy') == 14)
        self.assertEqual(len(entries), 89)
        self.assertEqual(cc.digest(entries), '57c34c140f9805430ed43133f1b3b88e563917db6a38169f2a826d4993342478')

    def test_pre_uc_torque_approvals_are_retained(self):
        doc = verify_policy(ROOT)
        entries = sorted((slot['slot'], variant['contract'], variant['compatibility'])
                         for slot in doc['slots'] for variant in slot['variants']
                         if variant.get('legacy') == 16)
        self.assertEqual(len(entries), 89)
        self.assertEqual(cc.digest(entries), '23a410c3bebb4cc20391367f5991e228b91330d1b0478d89a7a7035ffd14505b')

    def test_pre_cache_publish_approvals_are_retained(self):
        doc = verify_policy(ROOT)
        entries = sorted((slot['slot'], variant['contract'], variant['compatibility'])
                         for slot in doc['slots'] for variant in slot['variants']
                         if variant.get('legacy') == 11)
        self.assertEqual(len(entries), 89)
        self.assertEqual(cc.digest(entries), '5d8abc10763f8632573d63d12a9e5d9d37b6ea4f0267f6c81e76e84f48ed7be7')

    @unittest.skipUnless((ROOT / 'tools/publish/import.py').is_file(), 'exporter belongs to the source checkout')
    def test_later_pc_shipped_setup(self):
        """c4b98837 emits the raw parser error and enters conversion on a changed snapshot."""
        keys = ('NFS_GAME', 'NFS_PROSTREET_PC', 'NFS_UNDERCOVER_PC')
        assets = [os.environ.get(key) for key in keys]
        setup_python = Path(os.environ.get('NFS_SETUP_PYTHON', ROOT / SETUP_PYTHON)).resolve()
        missing = [key for key, value in zip(keys, assets) if not value or not Path(value).is_dir()]
        if not setup_python.is_file():
            missing.append('NFS_SETUP_PYTHON')
        if missing:
            self.skipTest('shipped converter requires ' + ', '.join(missing))
        with tempfile.TemporaryDirectory(prefix='later-pc-release-') as td:
            result, public = export_payload(Path(td).resolve())
            self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr[-1000:])
            run = command([str(setup_python), '-I', str(public / 'tests/setup/test_car_release.py'),
                           '--later-pc-setup', *assets], cwd=public)
            print(run.stdout, end='')

    def test_stale_artifacts_and_noncanonical_policy_reject(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            for name in (POLICY, HEADER, 'tools/setup_kit/car_catalog.py'):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / name, path)
            (root / HEADER).write_text('stale')
            with self.assertRaisesRegex(ValueError, 'stale packaged'):
                verify_policy(root)
            generate(root)
            verify_policy(root)
            (root / POLICY).write_bytes((root / POLICY).read_bytes() + b' ')
            with self.assertRaises(cc.CatalogError):
                generate(root)

    def test_missing_and_mismatched_policy_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td).resolve() / 'policy.json'
            with self.assertRaises(OSError):
                cc.load_policy(path, '0' * 64)
            path.write_bytes((ROOT / POLICY).read_bytes())
            with self.assertRaises(cc.CatalogError):
                cc.load_policy(path, '0' * 64)
        policy = cc.packaged_policy()
        self.assertEqual(policy.data, (ROOT / POLICY).read_bytes())

    def test_native_missing_and_mismatched_trust_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            (root / 'runtime').mkdir()
            for name in ('car_catalog.c', 'car_catalog.h', 'car_grip.c', 'car_grip.h',
                         'car_performance.c', 'car_performance.h'):
                shutil.copyfile(ROOT / 'runtime' / name, root / 'runtime' / name)
            shutil.copyfile(ROOT / HEADER, root / HEADER)
            binary = native_reader(root, root)
            command([str(binary), str(root / 'store'), '0'])
            store = root / 'store'
            store.mkdir()
            (store / 'current.json').write_bytes(cc.encoded(dict(schema=1, generation='g-' + '1' * 64,
                catalog_sha256='1' * 64, compatibility='2' * 64, selection=[])))
            data = (root / HEADER).read_text()
            (root / HEADER).write_text(data.replace(cc.PACKAGED_POLICY_SHA256, '0' * 64))
            binary = native_reader(root, root)
            broken = subprocess.run([str(binary), str(root / 'store'), '0'], capture_output=True, text=True)
            self.assertNotEqual(broken.returncode, 0)
            self.assertIn('digest mismatch', broken.stdout)
            (root / HEADER).unlink()
            with self.assertRaises(RuntimeError):
                native_reader(root, root)

    @unittest.skipUnless((ROOT / 'tools/publish/import.py').is_file(), 'exporter belongs to the source checkout')
    def test_exported_lifecycle_and_omitted_dependency(self):
        with tempfile.TemporaryDirectory(prefix='car-release-') as td:
            result, public = export_payload(Path(td).resolve())
            self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr[-1000:])
            files = [p.relative_to(public).as_posix() for p in public.rglob('*') if p.is_file()]
            self.assertFalse(any(n.startswith(FORBIDDEN_PREFIXES) or n in FORBIDDEN for n in files))
            self.assertIn('runtime/save_file_fixture.py', files)
            self.assertNotIn('runtime/auditfix_test.py', files)
            command([sys.executable, '-I', str(public / 'tests/setup/test_car_release.py'), '--verify-policy'], cwd=public)
            metadata = public / SELECTIONS
            original_metadata = metadata.read_bytes()
            metadata.write_bytes(original_metadata.replace(b'300C', b'300X', 1))
            edited = subprocess.run([sys.executable, '-I', str(public / 'tests/setup/test_car_release.py'),
                                     '--verify-policy'], cwd=public, capture_output=True, text=True, timeout=30)
            self.assertEqual(edited.returncode, 1)
            self.assertIn('conversion contract is not approved', edited.stderr)
            metadata.write_bytes(original_metadata)
            catalog_path = public / 'tools/setup_kit/car_catalog.py'
            current = catalog_path.read_bytes()
            catalog_path.write_text(PIN.sub('PACKAGED_POLICY_SHA256 = None', current.decode()))
            baseline = subprocess.run([sys.executable, '-I', str(public / 'tests/setup/test_car_release.py'),
                                       '--synthetic-baseline'], cwd=public, capture_output=True, text=True, timeout=180)
            self.assertNotEqual(baseline.returncode, 0)
            self.assertIn('no packaged approvals', baseline.stderr)
            catalog_path.write_bytes(current)
            print('original unpinned exported import: rejected (exit ' + str(baseline.returncode) + ')')
            good = command([sys.executable, '-I', str(public / 'tests/setup/test_car_release.py'), '--synthetic'], cwd=public)
            receipt = json.loads(good.stdout.splitlines()[-1])
            self.assertEqual(receipt['commands'], 12)
            for row in receipt['reports']:
                self.assertEqual(row['public_huff_sha256'],
                                 cc.fingerprint(public / 'tools/setup_kit/huff.py')['sha256'])
            print('exported car lifecycle: ' + json.dumps(receipt, sort_keys=True))
            command([sys.executable, '-I', str(public / 'runtime/car_catalog_test.py'),
                     'NativeCatalogTests.test_sparse_writer_reader_agreement'], cwd=public)
            (public / 'tools_snapshot/x360_tex.py').unlink()
            broken = subprocess.run([sys.executable, '-I', str(public / 'tests/setup/test_car_release.py'), '--synthetic'],
                                    cwd=public, capture_output=True, text=True, timeout=180)
            self.assertNotEqual(broken.returncode, 0)
            self.assertIn('x360_tex', broken.stderr)
            print('omitted pixel dependency: rejected (exit ' + str(broken.returncode) + ')')


if __name__ == '__main__':
    if sys.argv[1:2] == ['--carbon-pc-repack'] and (len(sys.argv) == 4 or sys.argv[4:] == ['--emulate-locked']):
        carbon_pc_repack(*(Path(p).resolve() for p in sys.argv[2:4]), emulate=len(sys.argv) == 5)
    elif sys.argv[1:2] == ['--later-pc-setup'] and len(sys.argv) == 5:
        later_pc_setup(*(Path(p).resolve() for p in sys.argv[2:]))
    elif sys.argv[1:2] == ['--refresh-converter-pins']:
        if len(sys.argv) != 7:
            raise SystemExit('usage: --refresh-converter-pins CARBON_PC CARBON_X360 MW_PC PROSTREET_PC UNDERCOVER_PC')
        refresh_converter_pins(*(Path(p).expanduser().resolve() for p in sys.argv[2:]))
    elif len(sys.argv) == 6 and sys.argv[1] == '--review-additions':
        review_additions(*(Path(p).resolve() for p in sys.argv[2:]))
    elif sys.argv[1:] == ['--generate']:
        generate(ROOT)
    elif sys.argv[1:] == ['--verify-policy']:
        verify_policy(ROOT)
        verify_selections(ROOT)
    elif len(sys.argv) == 3 and sys.argv[1] == '--verify-contracts':
        print(json.dumps(verify_contracts(ROOT, json.loads(Path(sys.argv[2]).read_bytes())), sort_keys=True))
    elif sys.argv[1:2] == ['--generate-selections'] and len(sys.argv) in (2, 3):
        generate_selections(json.loads(Path(sys.argv[2] if len(sys.argv) == 3 else ROOT / SELECTIONS).read_bytes()))
    elif sys.argv[1:] in (['--converter-pins'], ['--converter-pins', '--emulate-locked']):
        print(json.dumps(converter_pin_report(len(sys.argv) == 3), sort_keys=True))
    elif sys.argv[1:] == ['--synthetic']:
        synthetic(ROOT)
    elif sys.argv[1:] == ['--synthetic-baseline']:
        synthetic(ROOT, install=False)
    else:
        unittest.main(verbosity=2)
