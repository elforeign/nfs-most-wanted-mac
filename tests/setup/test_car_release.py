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
Packaged selection metadata: 19/19 reviewed rows (PC 10, X360 9); edited
display/source/slot/parts/output and omitted/extra rows: 7 FAIL refusals,
original canonical metadata: PASS. Generator reproduces identical bytes.
Exported metadata edit: --verify-policy FAIL exit 1; original PASS exit 0.
"""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
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
PIN = re.compile(r'^PACKAGED_POLICY_SHA256 = .*$', re.M)
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
             str(source), str(root / 'runtime/car_catalog.c'), '-o', str(binary)])
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
    names += [POLICY, HEADER, SELECTIONS, 'tools/setup_kit/car_selection.py',
              'tests/setup/test_car_release.py', 'runtime/save_file_fixture.py']
    allowed = publish.patterns(ROOT / 'tools/publish/allowlist.txt')
    denied = publish.patterns(ROOT / 'tools/publish/denylist.txt')
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
    def test_packaged_selections_and_tamper_controls(self):
        from setup_kit import car_selection as selection
        rows = selection.load()
        self.assertEqual({p: sum(r['origin']['platform'] == p for r in rows)
                          for p in ('pc', 'x360')}, dict(pc=10, x360=9))
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
        print('selection metadata: PASS 19 approved; FAIL controls 7/7 refused')

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
            for name in ('car_catalog.c', 'car_catalog.h'):
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
    if sys.argv[1:] == ['--generate']:
        generate(ROOT)
    elif sys.argv[1:] == ['--verify-policy']:
        verify_policy(ROOT)
        verify_selections(ROOT)
    elif len(sys.argv) == 3 and sys.argv[1] == '--verify-contracts':
        print(json.dumps(verify_contracts(ROOT, json.loads(Path(sys.argv[2]).read_bytes())), sort_keys=True))
    elif sys.argv[1:2] == ['--generate-selections'] and len(sys.argv) in (2, 3):
        generate_selections(json.loads(Path(sys.argv[2] if len(sys.argv) == 3 else ROOT / SELECTIONS).read_bytes()))
    elif sys.argv[1:] == ['--synthetic']:
        synthetic(ROOT)
    elif sys.argv[1:] == ['--synthetic-baseline']:
        synthetic(ROOT, install=False)
    else:
        unittest.main(verbosity=2)
