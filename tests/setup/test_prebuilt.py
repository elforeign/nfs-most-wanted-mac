#!/usr/bin/env python3
"""Optional Setup libraries: real arm64 archives, integrity and publication controls.

Good: 10 archives / 390 members copied, no game objects. Broken: missing set,
SHA mismatch, wrong recipe, missing archive/header, extra file, incompatible
object, symlink and debug paths each return source fallback (9/9); missing legal texts or
source archive fail publication (6/6). Checks are registered by offline_tests.sh.
Build-machine metadata: original archives 7/7 contain paths; stripped 0/7.
Path guard removed: 1/1 violations; present: 0/1 violations.
40abf6af: substituted archives + manifest accepted 10/390 (FAIL); fixed: 0/390.
40abf6af: optional unreadable copy aborts 2/8 first/update cases (FAIL);
fixed: 8/8 reach the backend and select source fallback (PASS).
Cache-pin control: 1 wrong digest forwarded; signed-app pin: 0 (compiled Swift).
40abf6af: missing app pin and partial output-copy controls fail 2/2;
fixed: both reject optional libraries without leaving archives (PASS).
Same-version legacy cache: 40abf6af retains the old backend (1 failure);
fixed signed-pin cache identity refreshes it (0 failures).
"""
import importlib.util
import inspect
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tarfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('prebuilt', ROOT / 'tools/setup_kit/prebuilt.py')
p = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(p)


class PrebuiltTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if sys.platform != 'darwin' or not shutil.which('clang'):
            raise unittest.SkipTest('requires macOS clang/ar')
        if not (ROOT / p.FFMPEG).is_file():
            raise unittest.SkipTest('requires the accompanying pinned FFmpeg archive')
        cls.temp = tempfile.TemporaryDirectory(prefix='prebuilt-fixture-')
        cls.base = Path(cls.temp.name)
        cls.seed = cls.base / 'seed'
        cls.seed.mkdir()
        for name in p.recipe(ROOT):
            dst = cls.seed / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, dst)
        shutil.copyfile(ROOT / 'tools/setup.py', cls.seed / 'tools/setup.py')
        dst = cls.seed / p.FFMPEG
        dst.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / p.FFMPEG, dst)
        obj = cls.base / 'good.o'
        source = cls.base / 'independent.c'
        source.write_text('int independent_probe(void) { return 42; }\n')
        subprocess.run(['clang', '-arch', 'arm64', '-mmacosx-version-min=14.5', '-c', str(source), '-o', str(obj)],
                       check=True, capture_output=True)
        checker = p.load(ROOT / 'media/check_libraries.py')
        for index, (name, members) in enumerate(checker.manifest().items()):
            archive = cls.seed / 'prebuilt/files' / name
            archive.parent.mkdir(parents=True, exist_ok=True)
            objects = []
            for i, member in enumerate(members.elements()):
                dest = cls.base / 'objects' / str(index) / str(i) / member
                dest.parent.mkdir(parents=True)
                shutil.copyfile(obj, dest)
                objects.append(str(dest))
            subprocess.run(['ar', 'qc', str(archive), *objects], check=True, capture_output=True)
        header = cls.seed / 'prebuilt/files/third_party/ffmpeg/include/libavformat/avformat.h'
        header.parent.mkdir(parents=True)
        header.write_text('/* synthetic header */\n')
        (cls.seed / 'prebuilt/configure.txt').write_text('synthetic reduced LGPL configure line\n')
        cls.data = dict(schema=1, target='arm64-macos14.5', source=p.SHA, recipe=p.recipe(cls.seed),
                        configure=p.digest(cls.seed / 'prebuilt/configure.txt'),
                        files=p.inventory(cls.seed / 'prebuilt/files'))
        (cls.seed / 'prebuilt/manifest.json').write_text(json.dumps(cls.data))

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'temp'):
            cls.temp.cleanup()

    def setUp(self):
        self.case = tempfile.TemporaryDirectory(dir=self.base)
        self.root = Path(self.case.name) / 'source'
        shutil.copytree(self.seed, self.root)
        self.target = Path(self.case.name) / 'build'
        self.target.mkdir()
        self.messages = []
        self.expected_sha256 = p.digest(self.seed / 'prebuilt/manifest.json')

    def tearDown(self):
        self.case.cleanup()

    def rejected(self):
        self.assertFalse(self.install(), self.messages)
        self.assertFalse(any(self.target.rglob('*.a')), 'no partial prebuilt set may reach the link')

    def test_verified_set_installs(self):
        self.assertTrue(self.install(), self.messages)
        self.assertEqual(len(list(self.target.rglob('*.a'))), 10)
        self.assertFalse((self.target / 'recomp').exists())

    def install(self):
        # Exercise the original API too, so the regression reports acceptance
        # rather than failing merely because the new argument is unavailable.
        options = ({'expected_sha256': self.expected_sha256}
                   if 'expected_sha256' in inspect.signature(p.install).parameters else {})
        return p.install(self.root, self.target, self.messages.append, **options)

    def test_substituted_archives_and_manifest_fall_back(self):
        source = Path(self.case.name) / 'replacement.c'
        source.write_text('int independent_probe(void) { return 99; }\n')
        obj = source.with_suffix('.o')
        subprocess.run(['clang', '-arch', 'arm64', '-mmacosx-version-min=14.5', '-c', str(source), '-o', str(obj)],
                       check=True, capture_output=True)
        checker = p.load(ROOT / 'media/check_libraries.py')
        count = 0
        for index, (name, members) in enumerate(checker.manifest().items()):
            archive = self.root / 'prebuilt/files' / name
            archive.unlink()
            objects = []
            for i, member in enumerate(members.elements()):
                dest = Path(self.case.name) / 'replacement' / str(index) / str(i) / member
                dest.parent.mkdir(parents=True)
                shutil.copyfile(obj, dest)
                objects.append(str(dest))
            subprocess.run(['ar', 'qc', str(archive), *objects], check=True, capture_output=True)
            count += checker.check_archive(archive, members)
        self.assertEqual(count, 390)
        self.refresh_files(repin=False)
        accepted = self.install()
        copied = len(list(self.target.rglob('*.a')))
        print(f'substituted archives + manifest: accepted={accepted}, copied={copied}/10, members={count}', flush=True)
        self.assertFalse(accepted, 'payload must not supply its own expected digests')
        self.assertEqual(copied, 0)

    def test_missing_setup_pin_falls_back(self):
        self.assertFalse(p.install(self.root, self.target, self.messages.append))
        self.assertFalse(any(self.target.rglob('*.a')))

    def test_output_copy_failure_cleans_partial_libraries(self):
        original = shutil.copytree
        def copy(source, destination, *args, **kwargs):
            if Path(destination) == self.target / 'host/audio/build':
                Path(destination).mkdir(parents=True)
                (Path(destination) / 'partial.a').write_bytes(b'partial copy')
                raise PermissionError('unreadable optional library during output copy')
            return original(source, destination, *args, **kwargs)
        with mock.patch.object(p.shutil, 'copytree', side_effect=copy):
            self.rejected()

    def test_setup_passes_app_pin_even_when_cache_has_a_replacement(self):
        swift = (ROOT / 'tools/installer/SetupApp.swift').read_text()
        def function(name):
            start = swift.index('func ' + name + '(')
            brace = swift.index('{', start)
            depth, end = 1, brace + 1
            while depth:
                depth += (swift[end] == '{') - (swift[end] == '}')
                end += 1
            return swift[start:end]
        resources = Path(self.case.name) / 'signed Resources'
        resources.mkdir()
        trusted = self.expected_sha256
        (resources / 'prebuilt-manifest.sha256').write_text(trusted + '\n')
        (self.root / 'prebuilt-manifest.sha256').write_text('0' * 64)
        code = '''import Foundation
enum RunMode { case build, check, exportApp }
struct Bundle {
    static let main = Bundle()
    var resourceURL: URL? { URL(fileURLWithPath: CommandLine.arguments[1]) }
    func url(forResource name: String, withExtension ext: String) -> URL? {
        URL(fileURLWithPath: CommandLine.arguments[1]).appendingPathComponent(name + "." + ext)
    }
}
func sourceDir() -> URL { URL(fileURLWithPath: CommandLine.arguments[2]) }
func supportDir() -> URL { sourceDir().deletingLastPathComponent() }
func bundledVersion() -> String { "cache-version" }
struct X360Extras { var names: [String] = [] }  // backendArguments' optional Setup-choice parameter; unused here
'''
        code += '\n'.join(function(name) for name in
                         ('runQuick', 'copyOptionalPrebuilt', 'prepareSource', 'bundledPrebuiltDigest', 'backendArguments'))
        code += '''
try prepareSource()
let url = sourceDir()
let args = backendArguments(mode: .build, pc: url, x360: nil, dest: url, work: url)
print(String(data: try! JSONEncoder().encode(args), encoding: .utf8)!)
'''
        results = []
        for label, text in (('current', code), ('cache-pin-control', code.replace(
                'Bundle.main.url(forResource: "prebuilt-manifest", withExtension: "sha256")',
                'Optional(sourceDir().appendingPathComponent("prebuilt-manifest.sha256"))'))):
            stamp_pin = trusted if label == 'current' else '0' * 64
            (self.root / '.source-version').write_text('cache-version\nprebuilt=' + stamp_pin)
            source = Path(self.case.name) / (label + '.swift')
            binary = source.with_suffix('')
            source.write_text(text)
            subprocess.run(['swiftc', '-module-cache-path', str(Path(self.case.name) / 'modules'),
                            str(source), '-o', str(binary)], check=True, capture_output=True)
            args = json.loads(subprocess.run([str(binary), str(resources), str(self.root)],
                                            check=True, capture_output=True, text=True).stdout)
            results.append(args[args.index('--prebuilt-manifest-sha256') + 1] == trusted)
        self.assertEqual(results, [True, False], 'only the signed app resource may supply the pin')
        builder = (ROOT / 'tools/installer/build_installer.sh').read_text()
        self.assertLess(builder.index('prebuilt-manifest.sha256'), builder.index('codesign --force --sign - "$APP"'))

    def test_prepare_optional_failures_allow_first_install_and_update(self):
        swift = (ROOT / 'tools/installer/SetupApp.swift').read_text()
        def function(name):
            start = swift.index('func ' + name + '(')
            brace = swift.index('{', start)
            depth = 1
            end = brace + 1
            while depth:
                depth += (swift[end] == '{') - (swift[end] == '}')
                end += 1
            return swift[start:end]
        harness = Path(self.case.name) / 'prepare.swift'
        binary = harness.with_suffix('')
        harness.write_text('''import Foundation
struct Bundle {
    static let main = Bundle()
    var resourceURL: URL? { URL(fileURLWithPath: CommandLine.arguments[1]) }
    func url(forResource name: String, withExtension ext: String) -> URL? {
        let url = resourceURL!.appendingPathComponent(name + "." + ext)
        return FileManager.default.fileExists(atPath: url.path) ? url : nil
    }
}
func supportDir() -> URL { URL(fileURLWithPath: CommandLine.arguments[2]) }
func sourceDir() -> URL { supportDir().appendingPathComponent("source") }
func bundledVersion() -> String { "new-version" }
''' + function('runQuick') + '\n' +
            (function('bundledPrebuiltDigest') if 'func bundledPrebuiltDigest(' in swift else '') + '\n' +
            (function('copyOptionalPrebuilt') if 'func copyOptionalPrebuilt(' in swift else '') + '\n' +
            function('prepareSource') + '''
do { try prepareSource(); print("backend-ready") }
catch { fputs("prepare failed: \\(error)\\n", stderr); exit(1) }
''')
        subprocess.run(['swiftc', '-module-cache-path', str(Path(self.case.name) / 'modules'),
                        str(harness), '-o', str(binary)], check=True, capture_output=True)
        for update in (False, True):
            for fault in ('unreadable', 'missing', 'partial', 'bad-hash'):
                with self.subTest(update=update, fault=fault):
                    case = Path(self.case.name) / f'{update}-{fault}'
                    resources = case / 'Resources'
                    resources.mkdir(parents=True)
                    with tarfile.open(resources / 'source.tar.gz', 'w:gz') as tar:
                        for entry in self.seed.iterdir():
                            if entry.name not in ('prebuilt', 'third_party'):
                                tar.add(entry, arcname=entry.name)
                    shutil.copytree(self.seed / 'third_party', resources / 'third_party')
                    if fault != 'missing':
                        shutil.copytree(self.seed / 'prebuilt', resources / 'prebuilt')
                    library = resources / 'prebuilt/files/host/audio/build/libhostaudio.a'
                    if fault == 'unreadable':
                        library.chmod(0)
                    elif fault == 'partial':
                        library.unlink()
                    elif fault == 'bad-hash':
                        library.write_bytes(b'damaged library')
                    support = case / 'support'
                    if update:
                        (support / 'source/tools').mkdir(parents=True)
                        (support / 'source/tools/setup.py').write_text('old source')
                        (support / 'source/.source-version').write_text('old-version')
                    result = subprocess.run([str(binary), str(resources), str(support)], capture_output=True, text=True)
                    if fault == 'unreadable':
                        library.chmod(0o644)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    extracted = support / 'source'
                    self.assertEqual((extracted / '.source-version').read_text().splitlines()[0], 'new-version')
                    self.assertTrue((extracted / 'tools/setup_kit/prebuilt.py').is_file())
                    self.assertTrue((extracted / 'tools/setup.py').is_file())
                    output = case / 'build'
                    output.mkdir()
                    options = ({'expected_sha256': p.digest(self.seed / 'prebuilt/manifest.json')}
                               if 'expected_sha256' in inspect.signature(p.install).parameters else {})
                    self.assertFalse(p.install(extracted, output, self.messages.append, **options))
                    self.assertFalse(any(output.rglob('*.a')))
                    print(f'optional {fault}, update={update}: backend ready, source fallback', flush=True)
        # The legacy cache version can equal a rebuilt Setup's version even
        # though its backend predates the trusted-pin argument.
        resources = Path(self.case.name) / 'True-bad-hash/Resources'
        (resources / 'prebuilt-manifest.sha256').write_text(self.expected_sha256)
        support = Path(self.case.name) / 'True-bad-hash/support'
        (support / 'source/.source-version').write_text('new-version')
        (support / 'source/tools/setup.py').write_text('legacy backend without pin support')
        result = subprocess.run([str(binary), str(resources), str(support)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((support / 'source/tools/setup.py').read_bytes(), (self.seed / 'tools/setup.py').read_bytes())

    def test_missing_set_falls_back(self):
        shutil.rmtree(self.root / 'prebuilt')
        self.rejected()

    def test_bad_sha_falls_back(self):
        (self.root / 'prebuilt/files/host/audio/build/libhostaudio.a').write_bytes(b'damaged library')
        self.rejected()
        self.assertIn('SHA-256', self.messages[-1])

    def test_changed_build_recipe_falls_back(self):
        with (self.root / 'media/build_ffmpeg.sh').open('a') as f:
            f.write('\n# changed configuration\n')
        self.rejected()
        self.assertIn('recipe changed', self.messages[-1])

    def test_incomplete_archive_set_falls_back(self):
        (self.root / 'prebuilt/files/host/audio/build/libmedia.a').unlink()
        self.rejected()

    def test_missing_headers_fall_back_even_with_updated_manifest(self):
        shutil.rmtree(self.root / 'prebuilt/files/third_party/ffmpeg/include')
        self.refresh_files()
        self.rejected()

    def test_extra_file_falls_back_even_with_updated_manifest(self):
        (self.root / 'prebuilt/files/speed.o').write_bytes(b'forbidden object')
        self.refresh_files()
        self.rejected()

    def test_wrong_object_falls_back_even_with_updated_manifest(self):
        (self.root / 'prebuilt/files/host/audio/build/libhostaudio.a').write_bytes(b'!<arch>\n')
        self.refresh_files()
        self.rejected()

    def test_symbolic_link_falls_back(self):
        header = self.root / 'prebuilt/files/third_party/ffmpeg/include/libavformat/avformat.h'
        header.unlink()
        header.symlink_to(self.base / 'good.o')
        self.rejected()

    def test_debug_paths_fall_back_even_with_updated_manifest(self):
        obj = Path(self.case.name) / 'dsound_host.o'
        subprocess.run(['clang', '-g', '-arch', 'arm64', '-mmacosx-version-min=14.5', '-c',
                        str(self.base / 'independent.c'), '-o', str(obj)], check=True, capture_output=True)
        archive = self.root / 'prebuilt/files/host/audio/build/libhostaudio.a'
        archive.unlink()
        subprocess.run(['ar', 'qc', str(archive), str(obj)], check=True, capture_output=True)
        self.refresh_files()
        self.rejected()
        self.assertIn('build-machine path', self.messages[-1])

    def refresh_files(self, repin=True):
        data = dict(self.data, files=p.inventory(self.root / 'prebuilt/files'))
        (self.root / 'prebuilt/manifest.json').write_text(json.dumps(data))
        if repin:
            # Semantic-format checks model a release pinned before publication.
            self.expected_sha256 = p.digest(self.root / 'prebuilt/manifest.json')

    def test_publication_requires_all_licence_and_source_material(self):
        script = ROOT / 'tools/setup_kit/prebuilt.py'
        good = subprocess.run([sys.executable, '-I', str(script), 'check', str(self.root), str(self.root / 'prebuilt')],
                              capture_output=True, text=True)
        self.assertEqual(good.returncode, 0, good.stderr)
        for name in (*p.LEGAL, p.FFMPEG):
            with self.subTest(name=name):
                path = self.root / name
                saved = path.read_bytes()
                path.unlink()
                bad = subprocess.run([sys.executable, '-I', str(script), 'check', str(self.root), str(self.root / 'prebuilt')],
                                     capture_output=True, text=True)
                self.assertEqual(bad.returncode, 1, name)
                self.assertIn('FAIL', bad.stderr)
                path.write_bytes(saved)


if __name__ == '__main__':
    if sys.platform != 'darwin' or not (ROOT / p.FFMPEG).is_file():
        print('SKIP: needs macOS and the accompanying pinned FFmpeg archive')
        sys.exit(3)
    unittest.main()
