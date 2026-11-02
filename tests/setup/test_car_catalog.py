"""Synthetic public trust/transaction proof; no distributed assets or approvals.

Original missing behavior: recognized/converted files activate 0 without a
published verified contract. Approved sparse fixture activates IDs 84, 95, 159.
Proof: 30 tests pass, 22 good checks and 117 rejected broken controls. Bypassing
the contract-approval guard in memory makes four tests fail (zero errors).
Counts include six publication faults and a post-replace directory-flush failure.
"""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
from setup_kit import car_catalog as cc


def clone(value):
    return copy.deepcopy(value)


def force_remove(path):
    # Production deliberately seals and retains generations; only fixtures prune.
    if not path.exists():
        return
    for base, dirs, _ in os.walk(path, followlinks=False):
        os.chmod(base, 0o700)
        for name in dirs:
            child = Path(base) / name
            if not child.is_symlink():
                os.chmod(child, 0o700)
    shutil.rmtree(path)


def fixture_module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'tests/setup' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CatalogTests(unittest.TestCase):
    good = 0
    broken = 0

    @classmethod
    def tearDownClass(cls):
        print(f'catalog proof: good={cls.good}; broken={cls.broken} rejected; sparse IDs=84,95,159')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(force_remove, self.root)
        self.store = self.root / 'catalog'
        self.rows = cc.reservations()
        self.outputs, self.inputs = {}, {}
        for slot in (0, 11, 75):
            folder = 'MODEL_' + str(slot)
            source = self.root / ('local-' + str(slot))
            source.mkdir()
            groups = {}
            for group in cc.INPUT_GROUPS:
                directory = source / group
                directory.mkdir()
                (directory / 'INPUT.BIN').write_bytes(('synthetic ' + group + ' input ' + folder).encode())
                groups[group] = directory
            out = source / 'converted'
            out.mkdir()
            for name in sorted(cc.REQUIRED_OUTPUTS | {'WHEELFIT.BIN'}):
                (out / name).write_bytes(('synthetic converted ' + folder + ' ' + name).encode())
            row = dict(self.rows[slot], state='ready',
                       origin=dict(game='carbon', platform='pc', folder=folder),
                       display=dict(name='Synthetic model ' + str(slot), manufacturer='Synthetic maker'),
                       bindings=dict(folder=folder, pvehicle=folder, frontend=folder + '_FE', ecar=folder + '_EC'),
                       donors=dict(model='DONOR', price='PRICE_DONOR'),
                       parts=[dict(type=0, keys=['BODY_STOCK', 'BODY_ALT']), dict(type=4, keys=['WHEEL_STOCK'])],
                       converter=dict(revision=1, policy=cc.digest('synthetic material policy')),
                       inputs={group: {'INPUT.BIN': cc.fingerprint(directory / 'INPUT.BIN')}
                               for group, directory in groups.items()},
                       outputs={name: cc.fingerprint(out / name) for name in sorted(os.listdir(out))})
            row['identity'] = cc.source_identity(**row['origin'])
            self.rows[slot], self.outputs[slot], self.inputs[slot] = row, out, groups
        self.policy_path = self.root / 'synthetic-policy.json'
        self.policy_doc = dict(schema=1, revision=1,
                               slots=[dict(slot=s, identity=None, variants=[]) for s in range(cc.SLOT_COUNT)])
        for slot in self.outputs:
            row = self.rows[slot]
            self.policy_doc['slots'][slot]['identity'] = row['identity']
            self.approve(row)
            compatible = clone(row)
            compatible['converter']['revision'] = 2
            self.approve(compatible)
            platform_variant = clone(row)
            platform_variant['origin']['platform'] = 'x360'
            self.approve(platform_variant)
            incompatible = clone(compatible)
            incompatible['parts'][0]['keys'].reverse()
            self.approve(incompatible)
        self.save_policy()

    def approve(self, row):
        variant = dict(contract=cc.contract_hash(row), compatibility=cc.slot_compatibility(row))
        self.policy_doc['slots'][row['slot']]['variants'].append(variant)

    def save_policy(self):
        self.policy_path.write_bytes(cc.encoded(self.policy_doc))
        self.policy = cc.load_policy(self.policy_path, cc.digest(self.policy_doc))

    def snapshot(self):
        return {str(p.relative_to(self.root)): hashlib.sha256(p.read_bytes()).hexdigest()
                for base in self.outputs.values() for p in base.parent.rglob('*')
                if p.is_file() and not p.is_symlink()}

    def publish(self, **kwargs):
        args = dict(root=self.store, rows=self.rows, output_dirs=self.outputs,
                    input_dirs=self.inputs, policy=self.policy)
        args.update(kwargs)
        return cc.publish(**args)

    def verify(self):
        return cc.verify_current(self.store, policy=self.policy)

    def pointer_bytes(self):
        path = self.store / 'current.json'
        return path.read_bytes() if path.exists() else None

    def tamper_pointer(self, data):
        path = self.store / 'current.json'
        path.chmod(0o600)
        path.write_bytes(data)

    def reject(self, action):
        before, pointer = self.snapshot(), self.pointer_bytes()
        with self.assertRaises((cc.CatalogError, OSError)):
            action()
        self.assertEqual(before, self.snapshot(), 'writer changed a source tree')
        self.assertEqual(pointer, self.pointer_bytes(), 'failed writer changed previous pointer')
        type(self).broken += 1

    def zero(self, result=None):
        result = self.verify() if result is None else result
        self.assertEqual(result.selected, ())
        type(self).broken += 1
        return result

    def install_forged(self, rows=None, **changes):
        # Attacker can update all local hashes and pointer bytes, but not policy.
        doc = cc.make_catalog(self.rows, self.policy)
        if rows is not None:
            doc['cars'] = rows
            doc['compatibility'] = cc.digest([cc.slot_compatibility(r) if r['state'] == 'ready' else None for r in rows])
        doc.update(changes)
        data = cc.encoded(doc)
        sha = hashlib.sha256(data).hexdigest()
        gen = self.store / ('g-' + sha)
        gen.mkdir(parents=True, exist_ok=True)
        (gen / 'catalog.json').write_bytes(data)
        for slot, out in self.outputs.items():
            target = gen / 'cars' / str(slot)
            if not target.exists():
                shutil.copytree(out, target)
        pointer = dict(schema=1, generation=gen.name, catalog_sha256=sha,
                       compatibility=doc['compatibility'], selection=[0, 11, 75])
        (self.store / 'current.json').write_bytes(cc.encoded(pointer))
        return gen

    def test_original_missing_behavior_and_sparse_activation(self):
        self.zero()
        self.store.mkdir()
        # Converted directory and a locally self-consistent catalog confer no trust.
        cc.disable(self.store)
        self.zero()
        self.assertTrue(all(p.exists() for p in self.outputs.values()))
        before = self.snapshot()
        pointer = self.publish()
        verified = self.verify()
        self.assertEqual([r['id'] for r in verified.selected], [84, 95, 159])
        self.assertEqual(len(verified.catalog['cars']), 90)
        self.assertEqual(verified.catalog['cars'][1], cc.reservations()[1])
        self.assertEqual(pointer['generation'], verified.generation)
        self.assertEqual(before, self.snapshot())
        type(self).good += 3

    def test_packaged_default_has_no_synthetic_trust(self):
        self.publish()
        with mock.patch.object(cc, 'PACKAGED_POLICY_SHA256', None):
            self.zero(cc.verify_current(self.store))
            self.reject(lambda: cc.publish(self.store, self.rows, self.outputs, self.inputs))

    def test_forged_policy_and_canonical_bytes(self):
        expected = self.policy.sha256
        forged = clone(self.policy_doc)
        forged['revision'] += 1
        self.policy_path.write_bytes(cc.encoded(forged))
        self.reject(lambda: cc.load_policy(self.policy_path, expected))
        for raw in (json.dumps(self.policy_doc, indent=2).encode(),
                    '{"schema":1,"schema":1}\n'.encode(), b'{"schema":NaN}\n'):
            self.policy_path.write_bytes(raw)
            sha = hashlib.sha256(raw).hexdigest()
            self.reject(lambda: cc.load_policy(self.policy_path, sha))
        invalid = clone(self.policy_doc)
        invalid['slots'][1]['identity'] = invalid['slots'][0]['identity']
        self.policy_path.write_bytes(cc.encoded(invalid))
        self.reject(lambda: cc.load_policy(self.policy_path, cc.digest(invalid)))

    def test_self_consistent_catalog_never_grants_approval(self):
        mutations = []
        row = clone(self.rows)
        row[0]['converter']['revision'] = 99
        mutations.append(row)
        row = clone(self.rows)
        row[0]['inputs']['dependencies']['INPUT.BIN']['sha256'] = '1' * 64
        mutations.append(row)
        row = clone(self.rows)
        row[0]['outputs']['GEOMETRY.BIN']['sha256'] = '2' * 64
        mutations.append(row)
        row = clone(self.rows)
        row[0]['approved'] = 'approved'
        row[0]['evidence'] = 'A locally asserted visual review'
        mutations.append(row)
        for rows in mutations:
            with self.subTest(row=rows[0]['converter']):
                self.install_forged(rows)
                self.zero()

    def test_missing_corrupt_and_unapproved_activate_zero(self):
        self.publish()
        pointer = self.pointer_bytes()
        for raw in (b'', b'broken JSON', '{"schema":1,"schema":1}\n'.encode()):
            self.tamper_pointer(raw)
            self.zero()
        self.tamper_pointer(pointer)
        gen = self.store / self.verify().generation
        catalog = gen / 'catalog.json'
        catalog.chmod(0o600)
        original = catalog.read_bytes()
        catalog.write_bytes(original + b' ')
        self.zero()
        catalog.write_bytes(original)
        output = gen / 'cars/75/WHEELFIT.BIN'
        output.chmod(0o600)
        old = output.read_bytes()
        output.write_bytes(b'corrupt sidecar')
        # Deselection does not exempt a ready row from full verification.
        local = cc._decode(pointer)
        local['selection'] = [0]
        self.tamper_pointer(cc.encoded(local))
        self.zero()
        output.write_bytes(old)
        missing = gen / 'cars/11/TEXTURES.BIN'
        missing.parent.chmod(0o700)
        missing.unlink()
        self.zero()

    def test_every_input_output_and_policy_pin(self):
        self.publish()
        for group in cc.INPUT_GROUPS:
            path = self.inputs[0][group] / 'INPUT.BIN'
            old = path.read_bytes()
            path.write_bytes(old + b' changed')
            self.reject(self.publish)
            # Rehashing local pins cannot approve a changed dependency.
            rows = clone(self.rows)
            rows[0]['inputs'][group]['INPUT.BIN'] = cc.fingerprint(path)
            self.reject(lambda: self.publish(rows=rows))
            path.write_bytes(old)
        for name in self.rows[0]['outputs']:
            path = self.outputs[0] / name
            old = path.read_bytes()
            path.write_bytes(old + b' changed')
            self.reject(self.publish)
            path.write_bytes(old)
        for field in ('revision', 'policy'):
            rows = clone(self.rows)
            rows[0]['converter'][field] = 3 if field == 'revision' else 'a' * 64
            self.reject(lambda: self.publish(rows=rows))

    def test_missing_output_pin_or_input_group(self):
        self.publish()
        for name in self.rows[0]['outputs']:
            rows = clone(self.rows)
            del rows[0]['outputs'][name]
            self.reject(lambda: self.publish(rows=rows))
        extra = self.outputs[0] / 'EXTRA.BIN'
        extra.write_bytes(b'unpinned synthetic output')
        self.reject(self.publish)
        extra.unlink()
        rows = clone(self.rows)
        del rows[0]['inputs']['dependencies']
        self.reject(lambda: self.publish(rows=rows))
        dirs = clone(self.inputs)
        del dirs[0]['donor']
        self.reject(lambda: self.publish(input_dirs=dirs))
        # Even an erroneous approval cannot exempt an emitted file from pinning.
        rows = clone(self.rows)
        del rows[0]['outputs']['WHEELFIT.BIN']
        self.approve(rows[0])
        self.save_policy()
        self.reject(lambda: self.publish(rows=rows))

    def test_duplicate_reassigned_missing_and_reordered_slots(self):
        self.publish()
        rows = clone(self.rows)
        rows[11]['id'] = 84
        self.reject(lambda: self.publish(rows=rows))
        rows = clone(self.rows)
        rows[1] = clone(rows[0])
        self.reject(lambda: self.publish(rows=rows))
        rows = clone(self.rows)
        rows[0], rows[11] = rows[11], rows[0]
        self.reject(lambda: self.publish(rows=rows))
        self.reject(lambda: self.publish(rows=self.rows[:-1]))
        rows = clone(self.rows)
        rows[11]['identity'] = rows[0]['identity']
        self.reject(lambda: self.publish(rows=rows))
        rows = clone(self.rows)
        rows[0]['slot'] = False
        self.reject(lambda: self.publish(rows=rows))

    def test_reserved_swaps_and_unsupported_sources(self):
        self.assertEqual([r['id'] for r in self.rows[:76]], list(range(84, 160)))
        self.assertEqual([r['swap_index'] for r in self.rows[76:]], list(range(14)))
        self.assertTrue(all(r['state'] == 'absent' for r in self.rows[76:]))
        type(self).good += 1
        for game in ('prostreet', 'undercover'):
            rows = clone(self.rows)
            rows[0]['origin']['game'] = game
            self.reject(lambda: self.publish(rows=rows))
        rows = clone(self.rows)
        rows[76] = dict(rows[0], **{k: rows[76][k] for k in ('slot', 'id', 'role', 'swap_index')})
        self.reject(lambda: self.publish(rows=rows))

    def test_part_and_binding_semantics(self):
        mutations = []
        row = clone(self.rows[0])
        row['parts'][0]['keys'].reverse()
        mutations.append(row)
        row = clone(self.rows[0])
        row['parts'][0]['type'] = 255
        mutations.append(row)
        row = clone(self.rows[0])
        row['parts'][1]['type'] = 0
        mutations.append(row)
        row = clone(self.rows[0])
        row['parts'][1]['keys'] = ['BODY_STOCK']
        mutations.append(row)
        row = clone(self.rows[0])
        row['bindings']['frontend'] = row['bindings']['pvehicle']
        mutations.append(row)
        row = clone(self.rows[0])
        row['donors']['price'] = 'OTHER_DONOR'
        mutations.append(row)
        for row in mutations:
            rows = clone(self.rows)
            rows[0] = row
            self.reject(lambda: self.publish(rows=rows))
        # Even independently approved rows must not collide at runtime.
        rows = clone(self.rows)
        rows[11]['bindings']['frontend'] = rows[0]['bindings']['frontend']
        self.approve(rows[11])
        self.save_policy()
        self.reject(lambda: self.publish(rows=rows))

    def test_traversal_and_invalid_output_paths(self):
        self.publish()
        for name in ('../GEOMETRY.BIN', '/GEOMETRY.BIN', 'dir//file', 'dir/./file',
                     'dir/../file', 'dir\\file', 'file\0name'):
            rows = clone(self.rows)
            rows[0]['inputs']['source'] = {name: next(iter(rows[0]['inputs']['source'].values()))}
            self.reject(lambda: self.publish(rows=rows))
        for name in ('../EXTRA.BIN', '/EXTRA.BIN', 'low.bin'):
            rows = clone(self.rows)
            rows[0]['outputs'][name] = next(iter(rows[0]['outputs'].values()))
            self.reject(lambda: self.publish(rows=rows))
        self.reject(lambda: self.publish(root=self.store / '../escape'))
        self.reject(lambda: self.publish(root=self.outputs[0] / 'nested'))
        self.reject(lambda: self.publish(root=self.inputs[0]['source'].parent))
        pointer = cc._decode(self.pointer_bytes())
        for generation in ('../elsewhere', '/tmp', 'g-' + 'a' * 63):
            pointer['generation'] = generation
            self.tamper_pointer(cc.encoded(pointer))
            self.zero()

    def test_symlinks_and_hardlinks(self):
        self.publish()
        for path in (self.inputs[0]['source'] / 'INPUT.BIN', self.outputs[0] / 'GEOMETRY.BIN'):
            old = path.read_bytes()
            path.unlink()
            target = self.root / 'link-target'
            target.write_bytes(old)
            path.symlink_to(target)
            self.reject(self.publish)
            path.unlink()
            os.link(target, path)
            self.reject(self.publish)
            path.unlink()
            path.write_bytes(old)
            target.unlink()
        link = self.root / 'linked-parent'
        link.symlink_to(self.inputs[0]['source'], target_is_directory=True)
        inputs = clone(self.inputs)
        inputs[0]['source'] = link
        self.reject(lambda: self.publish(input_dirs=inputs))
        link.unlink()
        linked_store = self.root / 'linked-store'
        linked_store.symlink_to(self.store, target_is_directory=True)
        self.reject(lambda: self.publish(root=linked_store))
        self.zero(cc.verify_current(linked_store, policy=self.policy))
        gen = self.store / self.verify().generation
        output = gen / 'cars/0/GEOMETRY.BIN'
        output.parent.chmod(0o700)
        output.unlink()
        output.symlink_to(self.outputs[0] / 'GEOMETRY.BIN')
        self.zero()

    def test_pointer_and_generation_symlinks(self):
        self.publish()
        pointer = self.pointer_bytes()
        path = self.store / 'current.json'
        path.unlink()
        target = self.root / 'pointer-copy'
        target.write_bytes(pointer)
        path.symlink_to(target)
        self.zero()
        path.unlink()
        path.write_bytes(pointer)
        generation = self.store / self.verify().generation
        moved = self.root / 'moved-generation'
        generation.chmod(0o700)
        generation.rename(moved)
        generation.symlink_to(moved, target_is_directory=True)
        self.zero()
        self.reject(self.publish)

    def test_compatible_and_incompatible_reimport_and_reuse(self):
        first = self.publish()
        gen = self.store / first['generation']
        original = {str(p.relative_to(gen)): (p.stat().st_ino, p.stat().st_mtime_ns, p.read_bytes())
                    for p in gen.rglob('*') if p.is_file()}
        second = self.publish(selection=[11])
        self.assertEqual(first['generation'], second['generation'])
        self.assertEqual([r['id'] for r in self.verify().selected], [95])
        self.assertEqual(original, {str(p.relative_to(gen)): (p.stat().st_ino, p.stat().st_mtime_ns, p.read_bytes())
                                    for p in gen.rglob('*') if p.is_file()})
        compatible = clone(self.rows)
        compatible[0]['converter']['revision'] = 2
        third = self.publish(rows=compatible)
        self.assertNotEqual(first['generation'], third['generation'])
        self.assertEqual(first['compatibility'], third['compatibility'])
        incompatible = clone(compatible)
        incompatible[0]['parts'][0]['keys'].reverse()
        fourth = self.publish(rows=incompatible)
        self.assertNotEqual(third['compatibility'], fourth['compatibility'])
        self.assertEqual(cc.slot_compatibility(self.rows[0]), cc.slot_compatibility(compatible[0]))
        self.assertNotEqual(cc.slot_compatibility(compatible[0]), cc.slot_compatibility(incompatible[0]))
        self.assertTrue(gen.exists())
        self.assertEqual(len(list(self.store.glob('g-*'))), 3)
        restored = self.publish()
        self.assertEqual(first['generation'], restored['generation'])
        type(self).good += 5

    def test_corrupt_generation_not_reused_or_overwritten(self):
        first = self.publish()
        output = self.store / first['generation'] / 'cars/0/GEOMETRY.BIN'
        output.chmod(0o600)
        output.write_bytes(b'broken retained generation')
        self.reject(self.publish)
        self.assertEqual(output.read_bytes(), b'broken retained generation')
        self.zero()

    def test_approved_platform_reimport_preserves_logical_slot(self):
        pc = self.publish()
        rows = clone(self.rows)
        rows[0]['origin']['platform'] = 'x360'
        self.assertEqual(rows[0]['identity'], cc.source_identity(**rows[0]['origin']))
        x360 = self.publish(rows=rows)
        self.assertNotEqual(pc['generation'], x360['generation'])
        self.assertEqual(pc['compatibility'], x360['compatibility'])
        self.assertEqual([r['id'] for r in self.verify().selected], [84, 95, 159])
        type(self).good += 1

    def test_selection_disable_and_recovery(self):
        first = self.publish()
        before = self.snapshot()
        cc.set_selection(self.store, [0, 75], policy=self.policy)
        self.assertEqual([r['id'] for r in self.verify().selected], [84, 159])
        for selection in ([1], [0, 0], [75, 0], [False], [-1], [90]):
            self.reject(lambda: cc.set_selection(self.store, selection, policy=self.policy))
        cc.disable(self.store)
        self.assertEqual(self.pointer_bytes(), cc.encoded(cc.disabled_pointer()))
        self.assertEqual(self.verify().selected, ())
        self.assertTrue((self.store / first['generation']).exists())
        restored = cc.restore_generation(self.store, first['generation'], policy=self.policy)
        self.assertEqual(restored['generation'], first['generation'])
        self.assertEqual(before, self.snapshot())
        # No interpretation of corrupt old data is needed to disable it.
        self.tamper_pointer(b'bad pointer')
        cc.disable(self.store)
        self.assertEqual(self.verify().selected, ())
        type(self).good += 4

    def test_recovery_verifies_retained_generation_before_selection(self):
        first = self.publish()
        cc.disable(self.store)
        for generation in ('../escape', 'g-../escape', 'g-' + '1' * 64):
            self.reject(lambda: cc.restore_generation(self.store, generation, policy=self.policy))
        self.reject(lambda: cc.restore_generation(self.store, first['generation'],
                                                   policy=self.policy, selection=[1]))
        # Source trees may be unavailable during recovery; approved retained
        # generation bytes suffice, and no attempt is made to inspect sources.
        with mock.patch.object(cc, 'fingerprint', side_effect=AssertionError('source I/O')):
            restored = cc.restore_generation(self.store, first['generation'],
                                              policy=self.policy, selection=[75])
        self.assertEqual(restored['selection'], [75])
        self.assertEqual([r['id'] for r in self.verify().selected], [159])
        cc.disable(self.store)
        output = self.store / first['generation'] / 'cars/0/TEXTURES.BIN'
        output.chmod(0o600)
        output.write_bytes(b'corrupt retained texture')
        self.reject(lambda: cc.restore_generation(self.store, first['generation'], policy=self.policy))
        self.assertEqual(self.verify().selected, ())
        type(self).good += 1

    def test_empty_catalog_is_verified_zero(self):
        self.publish(rows=cc.reservations(), output_dirs={}, input_dirs={})
        result = self.verify()
        self.assertFalse(result.error)
        self.assertEqual(result.selected, ())
        self.assertEqual(len(result.catalog['cars']), 90)
        type(self).good += 1

    def test_publication_failures_preserve_pointer_and_sources(self):
        first = self.publish()
        newer = clone(self.rows)
        newer[0]['converter']['revision'] = 2
        points = ('inputs_verified', 'output_durable', 'catalog_durable',
                  'generation_verified', 'pointer_ready', 'before_publish')
        for number, point in enumerate(points):
            # Separate roots ensure each fault exercises a fresh copy transaction.
            self.store = self.root / ('fault-store-' + str(number))
            self.publish()
            def fault(where):
                if where == point:
                    raise OSError('injected storage failure at ' + point)
            self.reject(lambda: self.publish(rows=newer, fault=fault))
            self.assertEqual(self.verify().generation, first['generation'])
        print('broken: 6/6 publication phase failures preserve exact pointer/source hashes')

    def test_no_previous_pointer_on_failed_first_import(self):
        for point in ('output_durable', 'catalog_durable', 'pointer_ready'):
            self.store = self.root / ('first-' + point)
            def fault(where):
                if where == point:
                    raise OSError('first import interrupted')
            self.reject(lambda: self.publish(fault=fault))
            self.assertFalse((self.store / 'current.json').exists())
            self.zero()

    def test_post_replace_flush_failure_rolls_back_exact_pointer(self):
        self.publish()
        replace = os.replace
        fsync = os.fsync
        armed = False
        failed = False
        def wrapped_replace(src, dst, **kwargs):
            nonlocal armed
            result = replace(src, dst, **kwargs)
            if str(src).startswith('.pointer-'):
                armed = True
            return result
        def wrapped_fsync(fd):
            nonlocal failed
            if armed and not failed:
                failed = True
                raise OSError('injected commit directory flush failure')
            return fsync(fd)
        with mock.patch.object(cc.os, 'replace', wrapped_replace), mock.patch.object(cc.os, 'fsync', wrapped_fsync):
            self.reject(lambda: cc.set_selection(self.store, [75], policy=self.policy))
        self.assertTrue(failed)
        self.assertEqual([r['id'] for r in self.verify().selected], [84, 95, 159])

    def test_short_writes_are_completed_and_enospc_rolls_back(self):
        write = os.write
        def short_write(fd, data):
            return write(fd, data[:max(1, len(data) // 3)])
        with mock.patch.object(cc.os, 'write', short_write):
            self.publish()
        self.assertEqual(len(self.verify().selected), 3)
        type(self).good += 1
        with mock.patch.object(cc.os, 'write', side_effect=OSError('disk full')):
            self.reject(lambda: cc.set_selection(self.store, [0], policy=self.policy))

    def test_changed_input_at_last_boundary_is_rejected(self):
        self.publish()
        path = self.inputs[0]['dependencies'] / 'INPUT.BIN'
        pointer = self.pointer_bytes()
        def mutate(where):
            if where == 'before_publish':
                path.write_bytes(b'intentionally changed dependency')
        with self.assertRaises(cc.CatalogError):
            self.publish(fault=mutate)
        self.assertEqual(pointer, self.pointer_bytes())
        self.assertEqual(path.read_bytes(), b'intentionally changed dependency')
        type(self).broken += 1

    def test_parent_replaced_during_transaction_is_rejected(self):
        self.publish()
        source = self.inputs[0]['source']
        moved = source.with_name('source-moved')
        before = self.pointer_bytes()
        def mutate(where):
            if where == 'generation_verified':
                source.rename(moved)
                source.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(OSError):
            self.publish(fault=mutate)
        self.assertEqual(before, self.pointer_bytes())
        self.assertTrue((moved / 'INPUT.BIN').is_file())
        type(self).broken += 1

    def test_generation_inventory_rejects_unpinned_file(self):
        self.publish()
        gen = self.store / self.verify().generation
        gen.chmod(0o700)
        (gen / 'UNPINNED.BIN').write_bytes(b'unpinned synthetic generation output')
        self.zero()

    def test_writer_serialization_and_reader_atomicity(self):
        self.publish()
        entered, release, second_done = threading.Event(), threading.Event(), threading.Event()
        errors = []
        def fault(where):
            if where == 'pointer_ready':
                entered.set()
                if not release.wait(5):
                    raise RuntimeError('serialization test timed out')
        def first():
            try:
                cc.set_selection(self.store, [0], policy=self.policy, fault=fault)
            except BaseException as exc:
                errors.append(exc)
        def second():
            try:
                cc.disable(self.store)
                second_done.set()
            except BaseException as exc:
                errors.append(exc)
        a = threading.Thread(target=first)
        b = threading.Thread(target=second)
        a.start()
        try:
            self.assertTrue(entered.wait(5))
            b.start()
            # A reader sees the complete previous selection while a writer stages.
            for _ in range(3):
                self.assertEqual([r['id'] for r in self.verify().selected], [84, 95, 159])
            self.assertFalse(second_done.wait(.05))
        finally:
            release.set()
            a.join(5)
            if b.ident is not None:
                b.join(5)
        self.assertFalse(a.is_alive() or b.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(second_done.is_set())
        self.assertEqual(self.verify().selected, ())
        type(self).good += 2

    def test_schema_exposes_shared_contract(self):
        schema = json.loads((ROOT / 'tools/setup_kit/car_catalog.schema.json').read_text())
        self.assertEqual(schema['$schema'], 'https://json-schema.org/draft/2020-12/schema')
        self.assertEqual(set(schema['$defs']['catalog']['required']),
                         set(cc.make_catalog(self.rows, self.policy)))
        self.assertEqual(set(schema['$defs']['pointer']['required']), set(cc.disabled_pointer()))
        self.assertEqual(set(schema['$defs']['policy']['required']), set(self.policy_doc))
        self.assertEqual(schema['$defs']['catalog']['properties']['cars']['minItems'], 90)
        type(self).good += 1

    def test_real_converter_synthetic_fixture_requires_independent_approval(self):
        # Reuse the original public converter's generated geometry/texture/vault
        # builders; this is an actual conversion, not a pre-labelled ready row.
        fixtures = fixture_module('test_car_convert')
        fixture = fixtures.CarConversion()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        before = fixture.snapshot()
        result = fixture.convert()
        self.assertEqual(result['solids'], 14)
        self.assertEqual(len(result['outputs']), 5)
        self.zero()
        row = clone(self.rows[0])
        row['origin']['folder'] = 'CAR'
        row['identity'] = cc.source_identity(**row['origin'])
        row['outputs'] = {n: cc.fingerprint(fixture.out / n) for n in result['outputs']}
        # Partition every reported actual input under its local directory root.
        dirs = dict(source=fixture.car, dependencies=fixture.root,
                    donor=fixture.donor, converter=ROOT / 'tools/setup_kit')
        pins = {g: {} for g in cc.INPUT_GROUPS}
        for name, sha in result['inputs'].items():
            path = Path(name)
            group = 'source' if path.is_relative_to(fixture.car) else 'donor' if path.is_relative_to(fixture.donor) else 'dependencies'
            pin = cc.fingerprint(path)
            self.assertEqual(pin['sha256'], sha)
            pins[group][str(path.relative_to(dirs[group]))] = pin
        for module in ('car_convert.py', 'car_paint.py', 'car_physics.py', 'huff.py'):
            pins['converter'][module] = cc.fingerprint(dirs['converter'] / module)
        row['inputs'] = pins
        rows = cc.reservations()
        rows[0] = row
        outputs, inputs = {0: fixture.out}, {0: dirs}
        self.reject(lambda: self.publish(rows=rows, output_dirs=outputs, input_dirs=inputs))
        self.policy_doc['slots'][0]['identity'] = row['identity']
        self.policy_doc['slots'][0]['variants'] = []
        self.approve(row)
        self.save_policy()
        self.publish(rows=rows, output_dirs=outputs, input_dirs=inputs)
        verified = self.verify()
        self.assertEqual([r['id'] for r in verified.selected], [84])
        self.assertEqual(before, fixture.snapshot())
        self.assertEqual(verified.selected[0]['outputs'], row['outputs'])
        type(self).good += 1
        print('good: actual synthetic converter 14 solids / 5 outputs / 7 asset inputs; unapproved=0, approved=1')

    def test_recognition_alone_never_publishes(self):
        fixtures = fixture_module('test_car_sources')
        fixture = fixtures.RecognitionTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        result = fixtures.cs.recognise(fixture.pc)
        self.assertEqual([r['status'] for r in result['cars']], ['importable', 'importable'])
        self.zero()
        type(self).good += 1


if __name__ == '__main__':
    unittest.main()
