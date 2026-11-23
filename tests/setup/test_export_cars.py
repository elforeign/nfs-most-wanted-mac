#!/usr/bin/env python3
"""Fixture exports only; no real installation, user data, or game launch.

Imported cars: original omission 0/2, current signed relocated snapshot 2/2.
Broken snapshots: source corruption and copy corruption rejected 2/2.
Replacement: signing/copy/swap failures retain the old signed copy 3/3;
broken remove-first control retains it 0/1. Successful update refreshes 2/2.
Startup: original userdata root finds 0/2 relocated cars; bundle root finds 2/2.
Master opt-in remains off (0) on both original and current startup paths.
Picker prefills: original default fields restore 0/2 products; current 2/2.
"""
import errno
import hashlib
import importlib.util
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'tools'))
from setup_kit import car_catalog as cc, export as ex, install as ins


def catalog_fixture():
    spec = importlib.util.spec_from_file_location('catalog_fixture', ROOT / 'tests/setup/test_car_catalog.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fixture = module.CatalogTests()
    fixture.setUp()
    fixture.rows[75] = cc.reservations()[75]
    del fixture.outputs[75], fixture.inputs[75]
    return fixture


def run(command, **kwargs):
    result = subprocess.run(list(map(str, command)), capture_output=True, text=True, **kwargs)
    if result.returncode:
        raise AssertionError(result.stderr[-2000:] + result.stdout[-2000:])
    return result.stdout


class ExportCarsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.build.cleanup)
        build = Path(cls.build.name)
        boot = (ROOT / 'runtime/boot.c').read_text()
        start = boot.index('    static const char bundle_cars_capability')
        public = boot[start:boot.index('#else', start)]
        # Compile the public startup body itself, with only save/settings/car
        # consumers stubbed. This executable never starts a game or Cocoa UI.
        prefix = r'''
#include "bundle_roots.h"
typedef struct { int unused; } Cpu;
typedef struct { int boot_value; } Setting;
static const char *userdata_dir(void) { return getenv("BOOT_USERDATA"); }
static void carsave_public_init(const char *root, const char *(*fn)(Cpu *, unsigned)) { (void)root; (void)fn; }
const char *car85_public_reconcile(Cpu *cpu, unsigned key) { (void)cpu; (void)key; return ""; }
static Setting *settings_find(const char *key) { (void)key; return NULL; }
static void private_cars_init(const char *root, int enabled, void *originals, int count) {
    (void)originals; (void)count; printf("root=%s\nmaster=%d\n", root, enabled);
}
static void boot(void) { char root[4096];
'''
        baseline = '    snprintf(root,sizeof root,"%s/car-catalog",userdata_dir());'
        assert hashlib.sha256(baseline.encode()).hexdigest() == 'b8ba438e2154d3922566a9710d37653f83de83f08f038f95b29595df45171335'
        for name, body in (('current', public), ('original', baseline + r'''
    private_cars_init(root, 0, NULL, 0);
''')):
            source = build / (name + '.c')
            source.write_text(prefix + body + '\n}\nint main(void) { boot(); return 0; }\n')
            run(['clang', '-I', ROOT / 'runtime', source, '-o', build / name])
        cls.binary, cls.original = build / 'current', build / 'original'

    def setUp(self):
        self.fixture = catalog_fixture()
        self.addCleanup(self.fixture.doCleanups)
        self.d = self.fixture.root
        self.profile = self.d / 'profile'
        self.profile.mkdir()
        self.fixture.store = self.profile / 'car-catalog'
        self.fixture.publish()
        self.policy = patch.object(cc, 'packaged_policy', return_value=self.fixture.policy)
        self.policy.start()
        self.addCleanup(self.policy.stop)
        self.app = self.d / ins.APP_BUNDLE
        exe = self.app / 'Contents/MacOS' / ins.EXEC_NAME
        exe.parent.mkdir(parents=True)
        (self.app / 'Contents/Resources').mkdir()
        shutil.copy2(self.binary, exe)
        with (self.app / 'Contents/Info.plist').open('wb') as stream:
            plistlib.dump(dict(CFBundleIdentifier=ins.PUBLIC_BUNDLE_ID,
                              CFBundleExecutable=ins.EXEC_NAME, CFBundlePackageType='APPL',
                              CFBundleShortVersionString='4.7.1'), stream)
        ex.sign_bundle(self.app)
        self.pc = self.d / 'pc'
        self.pc.mkdir()
        (self.pc / 'fixture.bin').write_bytes(b'synthetic PC data')
        (self.profile / 'roots.txt').write_text(f'pc_root={self.pc}\n')
        self.target = self.d / 'Backup.app'

    def export(self, **kwargs):
        return ex.export_app(self.app, self.profile, self.target, **kwargs)

    def snapshot(self):
        return self.target / ex.MANIFEST.parent / 'car-catalog'

    def count(self, root):
        if not root.exists():
            return 0
        verified = cc.verify_current(root)
        self.assertFalse(verified.error, verified.error)
        return len(verified.selected)

    def test_two_cars_relocated_refreshed_and_opt_in(self):
        (self.fixture.store / 'g-unverified').mkdir()
        (self.fixture.store / 'g-unverified/broken.bin').write_bytes(b'never export this')
        (self.profile / 'career-save').write_bytes(b'synthetic save, stays external')
        with patch.object(ex, 'snapshot_cars', return_value=None):
            self.export()  # Original export omits the catalog.
        self.assertFalse(self.snapshot().exists())
        self.assertEqual(self.count(self.snapshot()), 0)
        old_identity = ins.ident(self.target)
        metrics = self.export()
        self.assertNotEqual(ins.ident(self.target), old_identity)
        self.assertEqual(metrics['catalog_cars'], 2)
        self.assertEqual(self.count(self.snapshot()), 2)
        self.assertFalse(list(self.target.rglob('career-save')))
        self.assertEqual((self.profile / 'career-save').read_bytes(), b'synthetic save, stays external')
        self.assertEqual(set(p.name for p in self.snapshot().iterdir()),
                         {'current.json', self.fixture.verify().generation})
        self.assertTrue(ins.codesign_ok(self.target))
        self.fixture.store.rename(self.d / 'offline-catalog')
        self.pc.rename(self.d / 'offline-pc')
        self.target = self.target.rename(self.d / 'Relocated.app')
        environment = dict(os.environ, BOOT_USERDATA=str(self.profile))
        def startup(executable):
            return dict(line.split('=', 1) for line in run([executable], env=environment).splitlines())
        current = startup(self.target / 'Contents/MacOS' / ins.EXEC_NAME)
        self.assertEqual(current['master'], '0')
        self.assertEqual(Path(current['root']), self.snapshot())
        self.assertEqual(self.count(Path(current['root'])), 2)
        original = startup(self.original)
        self.assertEqual(original['master'], '0')
        self.assertEqual(self.count(Path(original['root'])), 0)
        # Refresh from the current installed catalog, including deselections.
        (self.d / 'offline-catalog').rename(self.fixture.store)
        (self.d / 'offline-pc').rename(self.pc)
        cc.set_selection(self.fixture.store, [11], policy=self.fixture.policy)
        self.export()
        self.assertEqual(self.count(self.snapshot()), 1)
        self.assertEqual(cc.verify_current(self.snapshot()).selected[0]['slot'], 11)

    def test_broken_source_and_copy_refused(self):
        self.export()
        before = ins.digest_tree(self.target)
        gen = self.fixture.verify().generation
        output = self.fixture.store / gen / 'cars/0/GEOMETRY.BIN'
        output.chmod(0o600)
        original = output.read_bytes()
        output.write_bytes(b'broken source')
        with self.assertRaisesRegex(ins.InstallError, 'snapshot failed verification'):
            self.export()
        self.assertEqual(ins.digest_tree(self.target), before)
        output.write_bytes(original)
        def corrupt(source, destination):
            ex.clone_file(source, destination)
            if Path(source).name == 'GEOMETRY.BIN':
                Path(destination).chmod(0o600)
                Path(destination).write_bytes(b'broken copy')
        with self.assertRaisesRegex(ins.InstallError, 'snapshot failed verification'):
            self.export(clone=corrupt)
        self.assertEqual(ins.digest_tree(self.target), before)

    def test_failed_update_retains_old_copy(self):
        self.export()
        old, identity = ins.digest_tree(self.target), ins.ident(self.target)
        (self.pc / 'fixture.bin').write_bytes(b'new synthetic PC data')
        def fail_sign(_):
            raise ins.InstallError('fixture signing failure')
        def fail_copy(*_):
            raise OSError(errno.ENOSPC, 'fixture full volume')
        for failure in ('sign', 'copy', 'swap'):
            with self.subTest(failure=failure):
                swap = patch.object(ex, '_exchange_export', side_effect=OSError(errno.EIO, 'fixture swap failure'))
                with swap if failure == 'swap' else patch.object(ex, '_exchange_export', wraps=ex._exchange_export):
                    with self.assertRaises((ins.InstallError, OSError)):
                        self.export(**({'sign': fail_sign} if failure == 'sign' else
                                       {'clone': fail_copy} if failure == 'copy' else {}))
                self.assertEqual(ins.ident(self.target), identity)
                self.assertEqual(ins.digest_tree(self.target), old)
                self.assertTrue(ins.codesign_ok(self.target))
                self.assertFalse(list(self.d.glob('.all-in-one-*')))
        # Broken remove-first replacement loses its old copy on signing failure.
        self.target.rename(self.d / 'fixture-control-removed.app')
        with self.assertRaises(ins.InstallError):
            self.export(sign=fail_sign)
        self.assertFalse(self.target.exists())

    def test_no_replacement_of_source_ordinary_app_or_symlink(self):
        for target in (self.app, self.profile / 'Nested.app'):
            with self.assertRaises(ins.InstallError):
                ex.export_app(self.app, self.profile, target)
        shutil.copytree(self.app, self.target)
        with self.assertRaisesRegex(ins.InstallError, 'not a verified export'):
            self.export()
        shutil.rmtree(self.target)
        self.target.symlink_to(self.app, target_is_directory=True)
        with self.assertRaises(ins.InstallError):
            self.export()
        self.target.unlink()
        self.export()
        alias = self.d / 'alias'
        alias.mkdir()
        with self.assertRaises(ins.InstallError):
            ex.export_app(self.target, self.profile, alias / '..' / self.target.name)

    def test_picker_prefills_and_update_wording(self):
        swift = (ROOT / 'tools/installer/SetupApp.swift').read_text()
        properties = swift[swift.index('    private var exportPreference:'):swift.index('    func exportAllInOne()')]
        panel_lines = [line for line in swift.splitlines() if line.strip().startswith((
            'let preferredSource =', 'source.directoryURL =', 'source.nameFieldStringValue =',
            'save.directoryURL = lastExportTarget', 'save.nameFieldStringValue = lastExportTarget'))]
        self.assertEqual(len(panel_lines), 5)
        prefix = r'''
import Foundation
final class UserDefaults {
    static let standard = UserDefaults()
    var values: [String: String] = [:]
    func string(forKey key: String) -> String? { values[key] }
}
final class Panel { var directoryURL: URL?; var nameFieldStringValue = "" }
struct Setup {
    var carbonProduct: Bool
    var installedApp: URL { URL(fileURLWithPath: "/default.app") }
'''
        suffix = r'''
    func fields() -> [String] {
        let source = Panel(), save = Panel(), app = installedApp
PANEL_LINES
        return [source.directoryURL!.appendingPathComponent(source.nameFieldStringValue).path,
                save.directoryURL!.appendingPathComponent(save.nameFieldStringValue).path]
    }
}
let directory = URL(fileURLWithPath: CommandLine.arguments[1])
var restored = 0
for game in ["mw", "carbon"] {
    let source = "/fixtures/" + game + ".app"
    let target = directory.appendingPathComponent(game + " backup.app")
    try! FileManager.default.createDirectory(at: target, withIntermediateDirectories: true)
    UserDefaults.standard.values["export." + game + ".source"] = source
    UserDefaults.standard.values["export." + game + ".target"] = target.path
    let model = Setup(carbonProduct: game == "carbon")
    if model.fields() == [source, target.path] && model.exportActionTitle == "Update this export" { restored += 1 }
}
print(restored)
'''.replace('PANEL_LINES', '\n'.join(panel_lines))
        for name, expected in (('current', '2'), ('original', '0')):
            # The original panel defaults bypassed remembered values entirely.
            variant = suffix if name == 'current' else suffix.replace('lastExportSource ?? installedApp', 'installedApp').replace(
                'source.nameFieldStringValue = preferredSource.lastPathComponent', 'source.nameFieldStringValue = "default.app"')
            code = self.d / (name + '.swift')
            code.write_text(prefix + properties + variant)
            binary = self.d / (name + '-prefills')
            run(['swiftc', '-module-cache-path', self.d / 'modules', code, '-o', binary])
            self.assertEqual(run([binary, self.d / 'picker-fixtures']).strip(), expected)
        self.assertIn('UserDefaults.standard.set(app.path, forKey: self.exportPreference + ".source")', swift)
        self.assertIn('UserDefaults.standard.set(target.path, forKey: self.exportPreference + ".target")', swift)
        self.assertIn('"Updated. Your old copy was replaced."', swift)

    def test_car_runtime_capability_required(self):
        def old(app, executable=ins.EXEC_NAME, capability=ex.RUNTIME_CAPABILITY):
            return capability == ex.RUNTIME_CAPABILITY
        with patch.object(ex, 'runtime_supports_export', side_effect=old):
            with self.assertRaisesRegex(ins.InstallError, 'does not support exported cars'):
                self.export()
        self.assertFalse(self.target.exists())


if __name__ == '__main__':
    unittest.main()
