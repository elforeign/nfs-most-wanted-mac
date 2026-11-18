"""Exported car release proof, using generated assets only.

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
Additional measured contracts: PC 6/6, X360 5/6; all 343 changed input
pins and 88 changed output pins reject (including one deliberately broken
pin per edition/car). Original 19 selections and ordered approval variants
remain byte-identical to their GRIP.BIN refresh. In an exported source tree,
reproduce the rows with --review-additions PC_ROOT X360_ISO MW_ROOT WORK
(measured with GRIP.BIN, 8 outputs each); trust artifacts use --generate
and --generate-selections as before. No converter check is relaxed.
X360 MUSTANGSHLBYN refuses source parity: KIT00_REAR_WINDOW_T0_A UV error
0.876464844 exceeds the unchanged 0.0015 threshold; PC remains approved.
Packaged selection metadata: 30/30 reviewed rows (PC 16, X360 14); edited
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
"""
import copy
from contextlib import closing
import hashlib
import importlib.util
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
MEASURED_OUTPUTS = '{"pc":{"converter":{"policy":"27506fb10448191d6843e10c71fa4a33cfc2bd2dff36455e1762b8e3b75f9cad","revision":2},"outputs":{"COLLISION.BIN":{"sha256":"3c7c590819521ec3b8d00123ea59cc685d2848a2e4e063eee278f40b710707f4","size":804},"GEOMETRY.BIN":{"sha256":"630e44334ca9b1aa5a966a15ee7204833243de6b97f5d2b634b9b484661ce46a","size":31794692},"GRIP.BIN":{"sha256":"af08088289f1d5272e85c066a78f0ca263c6bba137e35791cfac11a96a2b79da","size":1096},"PAINT.BIN":{"sha256":"4f93698a8357e5605dc0c4c4460842833f503d81b25c5059a1710abd8d7b7474","size":32},"PHYSICS.BIN":{"sha256":"90a7dfcd6832bd149d945587e22f5c6c87b315be6ca3e80f58ef43a6106dc042","size":36},"SOLIDMAP.BIN":{"sha256":"fecf6712465dd21dfbcfdb41adf825e4f15a872c2871822cb8e06d9bcc4a42ad","size":8600},"TEXTURES.BIN":{"sha256":"130f958a3932f42c42a5dd13b8c8f961b503b8b22a29890ece199b9e0b56b36e","size":894620},"WHEELFIT.BIN":{"sha256":"b03e5a0b5b8d80548926363400de7c7f91f1a5acda157c8b2ba93c12a0f5eba9","size":380}},"slot":0},"x360":{"converter":{"policy":"a426449c1b53db45c08ed59b58bc4bbad7e3d5a0c45da56ffc04c95bb4e57642","revision":2},"outputs":{"COLLISION.BIN":{"sha256":"3c7c590819521ec3b8d00123ea59cc685d2848a2e4e063eee278f40b710707f4","size":804},"GEOMETRY.BIN":{"sha256":"85d950fcd3174a2ba3906c50004e9ebc283b04a154d88403b0642e2bdc947f7b","size":24024061},"GRIP.BIN":{"sha256":"af08088289f1d5272e85c066a78f0ca263c6bba137e35791cfac11a96a2b79da","size":1096},"PAINT.BIN":{"sha256":"4f93698a8357e5605dc0c4c4460842833f503d81b25c5059a1710abd8d7b7474","size":32},"PHYSICS.BIN":{"sha256":"90a7dfcd6832bd149d945587e22f5c6c87b315be6ca3e80f58ef43a6106dc042","size":36},"SOLIDMAP.BIN":{"sha256":"fecf6712465dd21dfbcfdb41adf825e4f15a872c2871822cb8e06d9bcc4a42ad","size":8600},"TEXTURES.BIN":{"sha256":"2bd3ebccf9d75de9e773b140297ca7283fe1f1a1345ae08ed308ddda659f0e39","size":931484},"WHEELFIT.BIN":{"sha256":"b03e5a0b5b8d80548926363400de7c7f91f1a5acda157c8b2ba93c12a0f5eba9","size":380}},"slot":0}}\n'
MEASURED_OUTPUTS_SHA256 = '89174c653beecd9433fc03fe904e04319d7b978a4bc1476dcf8f4fa03653ecf0'
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


def derive_addition_specs(pc, mw):
    """Reproduce review metadata from source RefSpecs and native donor parts."""
    from collections import OrderedDict
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
    result = []
    for slot, folder, donor, maker in ADDITIONS:
        key = cv.hash32(folder.lower())
        assert (cv.hash32('ecar'), key) in attributes.collections
        cls, front, _ = struct.unpack('<III', raw_field(attributes, 'pvehicle', key, 'frontend'))
        assert cls == cv.hash32('frontend') and (cls, front) in attributes.collections
        front_name = names[front]
        assert cv.hash32(front_name) == front
        donor_hash = cv.name_hash(donor)
        allowed = {donor_hash if v == 0xffffffff else v for row in defaults for v in row}
        allowed.update(v for owner, _, *values in overrides if owner == donor_hash for v in values)
        allowed.discard(0)
        keys = OrderedDict()
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
        result.append(dict(slot=slot, folder=folder, display=dict(name=folder, manufacturer=maker),
            bindings=dict(folder=folder, pvehicle=folder, frontend=front_name.upper(), ecar=folder),
            donors=dict(model=donor, price=donor),
            parts=[dict(type=kind, keys=values) for kind, values in keys.items()]))
    return result


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
    return dict(rows=len(rows), mismatched=len(mismatched), first=mismatched[:3], accepted=accepted)


def review_additions(pc, x360, mw, work):
    """Remeasure the complete reviewed roster; retain fail-closed refusals."""
    require_shipped_runtime()
    from setup_kit.car_import import CarImporter
    from test_car_x360_assets import compare_car
    from setup_kit import car_sources, car_selection
    from setup_kit.x360 import Source
    work.mkdir(parents=True, exist_ok=True)
    previous = car_selection.load()
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
        mw_names = list(car_import.MW_SHARED) + [
            f'CARS/{row["donors"]["model"]}/{name}' for name in ('GEOMETRY.BIN', 'TEXTURES.BIN')]
        available = row['inputs']['dependencies'].keys() | row['inputs']['donor'].keys()
        code, policy, _ = car_import._code(row['origin']['platform'], sorted(set(mw_names) - available))
        row['inputs']['converter'] = code.pins
        row['converter']['policy'] = policy['sha256']
    return refreshed


def measured_converter_rows(rows, pc, x360, mw):
    """Run the importer writer; never infer outputs from an obsolete inventory."""
    from setup_kit import car_import, car_selection
    with tempfile.TemporaryDirectory() as td:
        work = Path(td).resolve()
        engine = car_import.CarImporter(work / 'catalog', work / 'work')
        measured = []
        for before in rows:
            source = pc if before['origin']['platform'] == 'pc' else x360
            result = engine.plan(source, mw_pc=mw, cars=[car_selection.spec(before)], measure=True)
            after = result['cars'][0]['contract']
            # Approval refresh must not silently bless changed source assets or
            # selection semantics. Only executable policy and outputs may move.
            expected = refreshed_converter_rows([before])[0]
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


def refresh_converter_pins(pc, x360, mw):
    """Regenerate existing variants from real writes, preserving variant ordering."""
    require_shipped_runtime()
    from setup_kit import car_selection
    rows = car_selection.load()
    refreshed = measured_converter_rows(rows, pc, x360, mw)
    policy = verify_policy(ROOT)
    for before, after in zip(rows, refreshed):
        variants = policy['slots'][before['slot']]['variants']
        variant = dict(contract=cc.contract_hash(before), compatibility=cc.slot_compatibility(before))
        index = variants.index(variant)
        assert cc.slot_compatibility(before) == cc.slot_compatibility(after)
        variants[index] = dict(contract=cc.contract_hash(after), compatibility=cc.slot_compatibility(after))
    (ROOT / POLICY).write_bytes(cc.encoded(policy))
    generate(ROOT)
    cc.PACKAGED_POLICY_SHA256 = cc.digest(policy)
    generate_selections(refreshed)
    generate_output_fixture(refreshed)


def verify_selections(root):
    from setup_kit import car_selection
    rows = car_selection.load(root / SELECTIONS)
    sha = hashlib.sha256((root / SELECTIONS).read_bytes()).hexdigest()
    if 'let carSelectionsSHA256 = "' + sha + '"' not in (root / 'tools/installer/SetupApp.swift').read_text():
        raise ValueError('stale Setup car selection pin')
    return rows


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
             str(source), str(root / 'runtime/car_grip.c'), str(root / 'runtime/car_catalog.c'), '-o', str(binary)])
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
    names = command(['git', '-C', str(ROOT), 'ls-files', '-z']).stdout.split('\0')
    names += ['tools/publish/denylist_carbon.txt', POLICY, HEADER, SELECTIONS, 'tools/setup_kit/car_selection.py',
              'tests/setup/test_car_release.py', 'runtime/save_file_fixture.py']
    names += ['tools/setup_kit/carbon_source.py']
    allowed = publish.patterns(ROOT / 'tools/publish/allowlist.txt')
    denied = publish.deny_patterns(ROOT / 'tools/publish')
    for name in sorted(set(names) - {''}):
        src = ROOT / name
        if not src.is_file() or (not name.startswith('tools/publish/') and
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
        new_slots = {r[0] for r in ADDITIONS}
        original = copy.deepcopy([r for r in car_selection.load() if r['slot'] not in new_slots])
        # Executable and writer pins are remeasured; source, donors and selection
        # semantics retain their original reviewed bytes.
        for row in original:
            del row['inputs']['converter'], row['converter'], row['outputs']
        self.assertEqual(len(original), 19)
        self.assertEqual(hashlib.sha256(cc.encoded(original)).hexdigest(),
                         '6aed512190fb5c46a5eaf37bb2d5f1451758cab43b3f887057fa92912fd8516a')
        policy = verify_policy(ROOT)
        previous = copy.deepcopy([r for r in policy['slots'] if r['variants'] and r['slot'] not in new_slots])
        for row in previous:
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
        self.assertEqual(proof['rejected']['inputs'], 343)
        self.assertEqual(proof['rejected']['outputs'], 88)
        print('additional contracts: ' + json.dumps(proof, sort_keys=True))

    def test_packaged_selections_and_tamper_controls(self):
        from setup_kit import car_selection as selection
        rows = selection.load()
        self.assertEqual({p: sum(r['origin']['platform'] == p for r in rows)
                          for p in ('pc', 'x360')}, dict(pc=16, x360=14))
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
        print('selection metadata: PASS 30 approved; FAIL controls 7/7 refused')

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
        self.assertEqual(hashlib.sha256(MEASURED_OUTPUTS.encode('ascii')).hexdigest(),
                         MEASURED_OUTPUTS_SHA256)
        measured = json.loads(MEASURED_OUTPUTS)
        self.assertEqual(set(measured), {'pc', 'x360'})
        inventories = 0
        for row in rows:
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
            for name in ('car_catalog.c', 'car_catalog.h', 'car_grip.c', 'car_grip.h'):
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
    if sys.argv[1:2] == ['--refresh-converter-pins']:
        if len(sys.argv) != 5:
            raise SystemExit('usage: --refresh-converter-pins CARBON_PC CARBON_X360 MW_PC')
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
