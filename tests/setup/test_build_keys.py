"""Setup stage dependency and retained-output proofs; synthetic files, no game.

Code-count premise: six build stages; first unchanged / X360-only / runtime-only
updates previously ran 2 / 6 / 6 (steady unchanged: 0). Now: 0 / 0 / 1 for unrelated X360
content; a table input runs tables + runtime (2), without recompiling libraries.
Each declared source/data input is changed with its timestamp preserved. The
oracle requires exactly the affected stages, including downstream consumers.
68/68 source/data mutations passed; six omitted-input mutants each failed the
same stage-set oracle (6/6 detected). Target/flags and output integrity are
checked separately, including relocations and interrupted publications.
Retained shader slice: same-size/same-mtime edit and deletion both invalidate tables.
Data-reference scan: fixed 1/1 passes; raw-padding mutant fails 1/1.
Partial producer outputs: 3/3 rejected; omitting each requirement makes the
same producer-boundary assertion fail (3/3 omission mutants detected).
HUFF helper headers with identical speed_32.c: original keys miss 2/2 table
invalidations; fixed keys detect 2/2. Interpreter relocation: original reruns
2/6 stages; fixed reruns 0/6, while changed interpreter bytes/version rerun 2/6.
"""
import ast
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import struct
import tempfile
import types
import unittest
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_backend import Fixture, FakeRunner, ROOT, b, synthetic_pc, write

bi = b.importlib.util.spec_from_file_location('stage_inputs', ROOT / 'recomp/build_inputs.py')
inputs = b.importlib.util.module_from_spec(bi)
bi.loader.exec_module(inputs)
STAGES = ('recompile', 'tables', 'ffmpeg', 'audio', 'shaders', 'runtime')

# Original output-directory resume check, frozen independently of repository
# history. Runtime writes coverage/build metadata after the first two stamps.
LEGACY_RUN = '''def run(self, name, outputs, action):
    stamp = self.directory / (name + ".json")
    try:
        expected = json.loads(stamp.read_text())
        actual = {str(p): digest_tree(p) for p in outputs if p.exists()}
        valid = len(actual) == len(outputs) and expected == actual
    except (OSError, ValueError, InstallError):
        valid = False
    if valid:
        self.events.emit("warn", id=self.events.current, msg=f"Resuming verified {name} output.")
        return
    stamp.unlink(missing_ok=True)
    for output in outputs:
        remove(output)
    action()
    if any(not p.exists() or (p.is_file() and not p.stat().st_size) for p in outputs):
        raise SetupError(f"{name} completed without all required outputs.")
    records = {str(p): digest_tree(p) for p in outputs}
    temporary = stamp.with_suffix(".tmp")
    temporary.write_text(json.dumps(records, sort_keys=True))
    os.replace(temporary, stamp)
'''
LEGACY_SHA256 = 'aa610b15beaaa6c88862a0f4e980537f8f7ab0d05f45f9cda2479b4dabeb54d0'


class DependencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='stage-keys-')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        self.root = self.home / 'source'
        self.pc, self.x360 = self.home / 'pc', self.home / 'x360'
        self.exe, self.offsets = synthetic_pc(self.pc, self.root)
        write(self.pc / 'server.dll', 'server input')
        self.compiler = dict(versions=dict(clang='clang 1', **{'clang++': 'clang++ 1', 'ar': 'ar 1', 'make': 'make 1'}),
                             sdk='sdk', sdk_version='15', architecture='arm64', python='python 1',
                             packages=dict(capstone='1', numpy='1', Pillow='1'), selected={'clang': ['clang', 'bytes-1']})
        self.env = {'NFS_PUBLIC': '1'}
        # Preserve audited import edges, without loading translator/decoder code.
        driver = ast.parse((ROOT / 'recomp/build_all.py').read_text())
        imported = {n.module for n in ast.walk(driver) if isinstance(n, ast.ImportFrom)}
        imported.update(a.name for n in ast.walk(driver) if isinstance(n, ast.Import) for a in n.names)
        recompiler = sorted({'build_all'} | {n for n in imported if n and (ROOT / ('recomp/' + n + '.py')).is_file()})
        self.recomp_sources = {'recomp/' + n + '.py' for n in recompiler}
        self.recomp_sources.update('recomp/' + n for n in
                                   ('build_inputs.py', 'exe_fingerprint.py', 'hooks.txt', 'functions_v13.tsv',
                                    'server_functions.tsv', 'runtime.h', 'windows_compile.h'))
        self.recomp_sources.add('runtime/breaker_diag.c')
        for name in recompiler:
            tree = ast.parse((ROOT / ('recomp/' + name + '.py')).read_text())
            imports = [ast.unparse(n) for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
            write(self.root / ('recomp/' + name + '.py'), '\n'.join(imports) + '\n')
        write(self.root / 'recomp/build_inputs.py', '# dependency policy\n')
        for name in ('hooks.txt', 'functions_v13.tsv', 'server_functions.tsv', 'windows_compile.h'):
            write(self.root / ('recomp/' + name), '// input\n')
        write(self.root / 'recomp/runtime.h', '#include "windows_compile.h"\n')
        write(self.root / 'runtime/breaker_diag.c', '#include "breaker_diag.inc"\n')
        write(self.root / 'tools/gen_tables.py', (ROOT / 'tools/gen_tables.py').read_bytes())
        self.generator = inputs.load_module(self.root / 'tools/gen_tables.py')
        self.table_sources = set(self.generator.GENERATORS) | {'tools/gen_tables.py'}
        for name in self.generator.GENERATORS:
            write(self.root / name, '# input\n' if name.endswith('.py') else '// input\n')
        self.raw_tables = set()
        write(self.x360 / 'NFS/ZZDATA0.BIN', 'shader data')
        for path in self.generator.inputs(self.pc, self.x360, self.x360 / 'NFS/ZZDATA0.BIN'):
            if path != self.pc / 'speed.exe' and path != self.root / 'recomp/out/speed_32.c':
                write(path, 'table data')
                self.raw_tables.add(path)
        for role in (self.pc, self.x360):
            (role / 'CARS').mkdir(exist_ok=True)
        write(self.root / 'runtime/build.sh', 'clang boot.c\n')
        write(self.root / 'runtime/env.sh', '# environment\n')
        write(self.root / 'runtime/boot.c', '#include "boot.h"\n#include "manifest.inc"\n')
        write(self.root / 'runtime/boot.h', '// header\n')
        write(self.root / 'runtime/manifest.inc', '// generated manifest\n')
        for folder, sources in (('host/audio', ('dsound_host.cpp',)), ('host/shaders', ('sm_parse.cpp', 'sm_msl.cpp'))):
            write(self.root / (folder + '/build.sh'), '# compiler flags\n')
            for source in sources:
                write(self.root / folder / source, '#include "api.h"\n')
            write(self.root / folder / 'api.h', '// API\n')
        write(self.root / 'media/media.cpp', '#include "media.h"\n#include "movie_api.h"\n')
        write(self.root / 'media/media.h', '// media API\n')
        write(self.root / 'media/movie_api.h', '// C media API\n')
        write(self.root / 'media/build_ffmpeg.sh', '# configure flags\n')
        for name in ('check_ffmpeg_sources.py', 'check_libraries.py', 'library_members.tsv'):
            write(self.root / 'media' / name, '# FFmpeg source audit input\n')
        write(self.root / 'third_party/dl/ffmpeg-7.1.1.tar.xz', 'pinned source')
        self.outputs = dict(recompile=['recomp/out', 'recomp/out/speed.a', 'recomp/out/funcs.h', 'recomp/out/breaker_diag.inc'],
                            tables=['runtime/gen/' + n for n in list(self.generator.STUBS) + self.generator.PUBTABLES + self.generator.PADTABLES] +
                                   ['runtime/gen/inputs.txt'], ffmpeg=['third_party/ffmpeg'],
                            audio=['host/audio/build/libhostaudio.a', 'host/audio/build/libmedia.a'],
                            shaders=['host/shaders/build/libsmtranslate.a'], runtime=['runtime/boot'])
        events = b.Events(False, io.BytesIO(), io.StringIO())
        self.cache = b.BuildStageCache(self.home / 'cache', events, inputs)
        self.pipeline()

    def test_retained_shader_slice_dependency(self):
        full = self.x360 / 'NFS/ZZDATA0.BIN'
        full.unlink()
        retained = self.x360 / 'NFS/zzdata0_09bb7000.bin'
        write(retained, 'shader window')
        self.assertEqual(self.generator.zzdata0_path(self.x360, environment={}), retained)
        self.assertIn(retained, inputs.stage_inputs('tables', self.root, self.pc, self.x360, self.env))
        before = self.fingerprint('tables')
        stat = retained.stat()
        write(retained, 'Shader window')
        os.utime(retained, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.assertNotEqual(before, self.fingerprint('tables'))
        before = self.fingerprint('tables')
        retained.unlink()
        self.assertNotEqual(before, self.fingerprint('tables'))

    def fingerprint(self, stage, **kwargs):
        options = dict(root=self.root, pc=self.pc, x360=self.x360, compiler=self.compiler, env=self.env)
        options.update(kwargs)
        return inputs.stage_key(stage, **options)

    def pipeline(self):
        ran = set()
        for stage in STAGES:
            fingerprint = self.fingerprint(stage)
            def build(stage=stage, fingerprint=fingerprint):
                ran.add(stage)
                names = self.outputs[stage]
                if stage == 'recompile':
                    names = names[1:] + ['recomp/out/speed_32.c']
                elif stage == 'ffmpeg':
                    names = ['third_party/ffmpeg/include/av.h', 'third_party/ffmpeg/lib/libavcodec.a']
                for name in names:
                    write(self.root / name, fingerprint, name == 'runtime/boot')
            self.cache.run(stage, fingerprint, self.root, self.outputs[stage], build)
        return ran

    def expected(self, path):
        if path in self.raw_tables:
            return {'tables', 'runtime'}
        if path == self.pc / 'server.dll' or path == self.pc / 'speed.exe':
            return {'recompile', 'tables', 'runtime'}
        relative = str(path.relative_to(self.root))
        if relative in self.recomp_sources:
            return {'recompile', 'tables', 'runtime'}
        if relative in self.table_sources:
            return {'tables', 'runtime'}
        if relative in ('media/build_ffmpeg.sh', 'media/check_ffmpeg_sources.py', 'media/check_libraries.py',
                        'media/library_members.tsv', 'third_party/dl/ffmpeg-7.1.1.tar.xz'):
            return {'ffmpeg', 'audio', 'runtime'}
        if relative.startswith('host/audio/') or relative in ('media/media.cpp', 'media/media.h', 'media/movie_api.h'):
            return {'audio', 'runtime'}
        if relative.startswith('host/shaders/'):
            return {'shaders', 'runtime'}
        return {'runtime'}

    def change(self, path):
        before, stat = path.read_bytes(), path.stat()
        path.write_bytes(before + b'\n# changed\n')
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        return before

    def test_every_declared_input_and_exact_dependents(self):
        paths = {self.root / n for n in self.recomp_sources | self.table_sources}
        paths |= self.raw_tables | {self.pc / 'server.dll'}
        paths |= {self.root / n for n in ('runtime/boot.c', 'runtime/boot.h', 'runtime/manifest.inc',
                  'runtime/build.sh', 'runtime/env.sh', 'media/media.cpp', 'media/media.h', 'media/movie_api.h')}
        for stage in ('ffmpeg', 'audio', 'shaders'):
            paths.update(p for p in inputs.stage_inputs(stage, self.root) if 'third_party/ffmpeg/' not in str(p))
        for path in sorted(paths):
            with self.subTest(input=str(path.relative_to(self.home))):
                before = self.change(path)
                try:
                    self.assertEqual(self.pipeline(), self.expected(path))
                finally:
                    path.write_bytes(before)
                    self.pipeline()
        print('source/data input mutations: %d/%d exact stage sets' % (len(paths), len(paths)))

    def test_omitted_input_mutant_for_each_stage(self):
        choices = dict(recompile=self.root / 'recomp/hooks.txt', tables=self.pc / 'GLOBAL/ATTRIBUTES.BIN',
                       ffmpeg=self.root / 'third_party/dl/ffmpeg-7.1.1.tar.xz',
                       audio=self.root / 'host/audio/dsound_host.cpp', shaders=self.root / 'host/shaders/sm_parse.cpp',
                       runtime=self.root / 'runtime/boot.c')
        real_digest, real_load = inputs.digest, inputs.load_module
        for stage, path in choices.items():
            frozen_digest = real_digest(path)
            frozen_table = self.generator.content_hash(path)
            def omitted_digest(p):
                return frozen_digest if Path(p) == path else real_digest(p)
            def omitted_module(p):
                module = real_load(p)
                if str(p).endswith('tools/gen_tables.py'):
                    content_hash = module.content_hash
                    module.content_hash = lambda p: frozen_table if Path(p) == path else content_hash(p)
                return module
            before = self.change(path)
            try:
                with mock.patch.object(inputs, 'digest', side_effect=omitted_digest), \
                     mock.patch.object(inputs, 'load_module', side_effect=omitted_module):
                    with self.assertRaises(AssertionError, msg='omission survived: ' + stage):
                        self.assertEqual(self.pipeline(), self.expected(path))
            finally:
                path.write_bytes(before)
                self.pipeline()
        print('omitted-input mutants: 6/6 rejected by stage-set oracle')

    def test_compiler_flags_packages_and_target(self):
        for field in ('sdk_version', 'architecture', 'python'):
            original = self.compiler[field]
            self.compiler[field] = 'changed'
            expected = {'recompile', 'tables'} if field == 'python' else set(STAGES)
            if field == 'python':
                expected.add('runtime')
            self.assertEqual(self.pipeline(), expected, field)
            self.compiler[field] = original
            self.pipeline()
        for component in ('clang', 'clang++', 'ar', 'make'):
            original = self.compiler['versions'][component]
            self.compiler['versions'][component] = 'new version'
            expected = (set(STAGES) - {'shaders'} if component == 'clang' else
                        set(STAGES) - {'recompile'} if component == 'clang++' else
                        {'ffmpeg', 'audio', 'runtime'} if component == 'make' else set(STAGES))
            self.assertEqual(self.pipeline(), expected)
            self.compiler['versions'][component] = original
            self.pipeline()
        for component in ('capstone', 'numpy', 'Pillow'):
            original = self.compiler['packages'][component]
            self.compiler['packages'][component] = 'new version'
            self.assertEqual(self.pipeline(), {'recompile', 'tables', 'runtime'} if component == 'capstone' else {'tables', 'runtime'})
            self.compiler['packages'][component] = original
            self.pipeline()
        original = self.compiler['selected']['clang']
        self.compiler['selected']['clang'] = ['clang', 'same-version-new-bytes']
        self.assertEqual(self.pipeline(), set(STAGES) - {'shaders'})
        self.compiler['selected']['clang'] = original
        self.pipeline()
        self.compiler['selected']['ld'] = ['ld', 'same-version-new-linker']
        self.assertEqual(self.pipeline(), {'tables', 'ffmpeg', 'audio', 'runtime'})
        del self.compiler['selected']['ld']
        self.pipeline()
        self.compiler['package-files'] = {'capstone': {'decoder.so': 'before'}}
        self.pipeline()
        self.compiler['package-files']['capstone']['decoder.so'] = 'same-version-repair'
        self.assertEqual(self.pipeline(), {'recompile', 'tables', 'runtime'})
        del self.compiler['package-files']
        self.pipeline()
        self.compiler['python-binary'] = 'replacement-interpreter'
        self.assertEqual(self.pipeline(), {'recompile', 'tables', 'runtime'})
        del self.compiler['python-binary']
        self.pipeline()
        package = self.home / 'package'
        binaries = [Path('decoder' + suffix) for suffix in ('.so', '.dylib', '.pyd', '.dll', '.py')]
        for binary in binaries:
            write(package / binary, 'library A')
        distribution = types.SimpleNamespace(files=binaries, locate_file=lambda p: package / p)
        with mock.patch.object(inputs.importlib.metadata, 'distribution', return_value=distribution):
            original = inputs.package_files(['capstone'])
            for binary in binaries:
                decoder = package / binary
                stat = decoder.stat()
                write(decoder, 'library B')
                os.utime(decoder, ns=(stat.st_atime_ns, stat.st_mtime_ns))
                self.assertNotEqual(inputs.package_files(['capstone']), original)
                write(decoder, 'library A')
        compiler_binary = self.home / 'compiler'
        write(compiler_binary, 'compiler A')
        version = types.SimpleNamespace(returncode=0, stdout='unchanged compiler version')
        with mock.patch.object(inputs.subprocess, 'run', return_value=version) as invoke, \
             mock.patch.object(inputs.shutil, 'which', return_value=str(compiler_binary)):
            original = inputs.toolchain(target='x86_64-pc-windows-msvc', compiler='selected-compiler')
            write(compiler_binary, 'compiler B')
            self.assertNotEqual(inputs.toolchain(target='x86_64-pc-windows-msvc', compiler='selected-compiler'), original)
            self.assertEqual(invoke.call_args.args[0], ['selected-compiler', '--version'])
        for name in ('RECOMP_CFLAGS', 'RECOMP_LISTING', 'CFLAGS', 'CXXFLAGS', 'CPPFLAGS', 'LDFLAGS',
                     'ASFLAGS', 'CC', 'CXX', 'DEVELOPER_DIR', 'SDKROOT', 'MACOSX_DEPLOYMENT_TARGET', 'CPATH',
                     'C_INCLUDE_PATH', 'CPLUS_INCLUDE_PATH', 'LIBRARY_PATH'):
            self.env[name] = '-changed'
            affected = ({'recompile', 'tables', 'runtime'} if name.startswith('RECOMP_') else
                        {'ffmpeg', 'audio', 'runtime'} if name in ('CC', 'CXX', 'CFLAGS', 'CXXFLAGS', 'CPPFLAGS', 'LDFLAGS', 'ASFLAGS') else set(STAGES))
            self.assertEqual(self.pipeline(), affected, name)
            del self.env[name]
            self.pipeline()
        for stage in STAGES:
            baseline = self.fingerprint(stage)
            self.assertNotEqual(baseline, self.fingerprint(stage, target='x86_64-pc-windows-msvc'))
            self.assertNotEqual(baseline, self.fingerprint(stage, arguments=['--different-build-flag']))
            self.assertEqual(self.fingerprint(stage, target='x86_64-pc-windows-msvc'),
                             self.fingerprint(stage, target='x86_64-pc-windows-msvc'))

    def test_generated_inputs_invalidate_only_consumers(self):
        cases = {self.root / 'recomp/out/speed_32.c': {'tables'},
                 self.root / 'third_party/ffmpeg/include/av.h': {'audio', 'runtime'}}
        for name in ('recomp/out/speed.a', 'recomp/out/funcs.h', 'recomp/out/breaker_diag.inc',
                     'host/audio/build/libhostaudio.a', 'host/audio/build/libmedia.a',
                     'host/shaders/build/libsmtranslate.a', 'third_party/ffmpeg/lib/libavcodec.a'):
            cases[self.root / name] = {'runtime'}
        for name in self.outputs['tables'][:-1]:
            cases[self.root / name] = {'runtime'}
        for path, affected in cases.items():
            before = {stage: self.fingerprint(stage) for stage in STAGES}
            content = self.change(path)
            try:
                self.assertEqual({stage for stage in STAGES if self.fingerprint(stage) != before[stage]}, affected, str(path))
            finally:
                path.write_bytes(content)
        print('generated input mutations: %d/%d exact consumer key sets' % (len(cases), len(cases)))

    def test_huff_headers_with_identical_generated_c(self):
        generated = self.root / 'recomp/out/speed_32.c'
        original = generated.read_bytes()
        real_inputs = inputs.stage_inputs
        def omitted_headers(stage, *args, **kwargs):
            paths = real_inputs(stage, *args, **kwargs)
            if stage == 'tables':
                paths = [p for p in paths if p not in (self.root / 'recomp/runtime.h', self.root / 'recomp/windows_compile.h')]
            return paths
        for name in ('runtime.h', 'windows_compile.h'):
            path = self.root / 'recomp' / name
            for broken in (False, True):
                with mock.patch.object(inputs, 'stage_inputs', side_effect=omitted_headers if broken else real_inputs):
                    before = {stage: self.fingerprint(stage) for stage in STAGES}
                    content = self.change(path)
                    try:
                        affected = {stage for stage in STAGES if self.fingerprint(stage) != before[stage]}
                        def oracle():
                            self.assertEqual(affected, {'recompile', 'tables', 'runtime'})
                        if broken:
                            self.assertEqual(affected, {'recompile', 'runtime'})
                            with self.assertRaises(AssertionError):
                                oracle()
                        else:
                            oracle()
                        self.assertEqual(generated.read_bytes(), original)
                    finally:
                        path.write_bytes(content)
        print('HUFF headers with unchanged translated C: 2/2 fixed; 2/2 original omissions rejected')

    def test_executable_regions_and_irrelevant_content(self):
        exe = self.pc / 'speed.exe'
        for region in ('.text', '.rdata', '.data'):
            changed = bytearray(self.exe)
            changed[self.offsets[region]] ^= 1
            exe.write_bytes(changed)
            self.assertEqual(self.pipeline(), {'recompile', 'tables', 'runtime'})
            exe.write_bytes(self.exe)
            self.pipeline()
        changed = bytearray(self.exe)
        changed[0x80 + 24 + 16] ^= 1
        exe.write_bytes(changed)
        self.assertEqual(self.pipeline(), {'recompile', 'tables', 'runtime'})
        exe.write_bytes(self.exe)
        self.pipeline()
        changed = bytearray(self.exe)
        changed[self.offsets['.rsrc']] ^= 1
        exe.write_bytes(changed + b'overlay')
        self.assertEqual(self.pipeline(), {'tables', 'runtime'})
        exe.write_bytes(self.exe)
        self.pipeline()
        write(self.pc / 'MOVIES/extra.vp6', 'changed movie')
        write(self.x360 / 'MOVIES/extra.wmv', 'changed movie')
        write(self.root / 'runtime/helper_test.c', '// test source')
        write(self.root / 'recomp/build_windows.py', '# other target driver')
        self.assertEqual(self.pipeline(), set())
        (self.x360 / 'CARS/new-car').mkdir()
        self.assertEqual(self.pipeline(), {'tables', 'runtime'})

    def test_relocation_integrity_and_interrupted_publication(self):
        moved = self.home / 'new-source'
        shutil.copytree(self.root, moved)
        old_root, self.root = self.root, moved
        self.assertEqual(self.pipeline(), set(), 'source relocation must reuse all stages')
        self.root = old_root
        runtime_key = self.fingerprint('runtime')
        retained = self.home / 'cache/runtime' / runtime_key
        write(self.root / 'runtime/boot', 'tampered', True)
        self.assertEqual(self.pipeline(), set(), 'restore a verified retained binary without rebuilding')
        self.assertEqual((self.root / 'runtime/boot').read_text(), runtime_key)
        self.assertNotEqual((self.root / 'runtime/boot').stat().st_ino,
                            (retained / 'outputs/runtime/boot').stat().st_ino)
        write(retained / 'outputs/runtime/boot', 'tampered retained binary', True)
        self.assertEqual(self.pipeline(), {'runtime'})
        (retained / 'manifest.json').unlink()
        self.assertEqual(self.pipeline(), {'runtime'})
        (retained / 'outputs/runtime/boot').unlink()
        self.assertEqual(self.pipeline(), {'runtime'})
        write(self.home / 'cache/runtime/.pending-abandoned/outputs/runtime/boot', 'partial')
        self.assertEqual(self.pipeline(), set())
        stamp = retained / 'manifest.json'
        manifest = json.loads(stamp.read_text())
        manifest['key'] = 'other-target'
        stamp.write_text(json.dumps(manifest))
        self.assertEqual(self.pipeline(), {'runtime'})
        new_key = self.fingerprint('runtime', arguments=['--new'])
        def fail():
            write(self.root / 'runtime/boot', 'partial')
            raise RuntimeError('interrupted build')
        with self.assertRaisesRegex(RuntimeError, 'interrupted'):
            self.cache.run('runtime', new_key, self.root, self.outputs['runtime'], fail)
        self.assertFalse((self.home / 'cache/runtime' / new_key).exists())
        self.assertTrue((retained / 'manifest.json').exists())
        self.assertEqual(self.pipeline(), set())

    def test_transitive_headers_imports_and_missing_inputs(self):
        write(self.root / 'host/audio/api.h', '#include "nested.h"\n')
        write(self.root / 'host/audio/nested.h', '// nested\n')
        self.assertEqual(self.pipeline(), {'audio', 'runtime'})
        self.change(self.root / 'host/audio/nested.h')
        self.assertEqual(self.pipeline(), {'audio', 'runtime'})
        (self.root / 'host/audio/nested.h').unlink()
        self.assertEqual(self.pipeline(), {'audio', 'runtime'})
        with (self.root / 'recomp/recomp.py').open('a') as source:
            source.write('\nimport new_translation_helper\n')
        write(self.root / 'recomp/new_translation_helper.py', '# helper\n')
        self.assertEqual(self.pipeline(), {'recompile', 'tables', 'runtime'})
        self.change(self.root / 'recomp/new_translation_helper.py')
        self.assertEqual(self.pipeline(), {'recompile', 'tables', 'runtime'})
        del self.env['NFS_PUBLIC']
        self.assertEqual(self.pipeline(), {'tables', 'runtime'})

    def test_output_parent_links_never_modify_the_link_target(self):
        fingerprint = self.fingerprint('runtime')
        protected = self.home / 'protected-runtime'
        (self.root / 'runtime').rename(protected)
        os.symlink(protected, self.root / 'runtime')
        original = (protected / 'boot').read_bytes()
        try:
            with self.assertRaisesRegex(b.SetupError, 'symbolic link'):
                self.cache.run('runtime', fingerprint, self.root, self.outputs['runtime'], lambda: self.fail('unexpected build'))
            self.assertEqual((protected / 'boot').read_bytes(), original)
        finally:
            (self.root / 'runtime').unlink()
            protected.rename(self.root / 'runtime')

    def test_data_reference_scan_ignores_unfingerprinted_padding(self):
        # Isolate the actual data-reference loop: no decoder or emulation.
        source = (ROOT / 'recomp/build_all.py').read_text()
        function = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == 'add_data_referenced')
        stop = next(i for i, n in enumerate(function.body) if isinstance(n, ast.Assign) and
                    any(isinstance(t, ast.Name) and t.id == 'names' for t in n.targets))
        function.body = function.body[:stop] + ast.parse('return sorted(extra)').body
        fp = inputs.load_module(ROOT / 'recomp/exe_fingerprint.py')
        target = 0x401010
        data = bytearray(self.exe)
        # Enlarge .data's raw section into resource bytes, without changing its
        # loaded region. The original raw scan mistakenly discovers this word.
        section = 0x80 + 24 + 224 + 2 * 40
        struct.pack_into('<I', data, section + 16, fp.REGIONS[2][2] + 256)
        struct.pack_into('<I', data, self.offsets['.rsrc'], target)
        opt, sections = fp._parse(data)
        class PE:
            def __init__(self):
                self.data = data
                self.sections = [(bytes(data[opt + 224 + 40*i:opt + 224 + 40*i + 8]), *s) for i, s in enumerate(sections)]
            def read(self, address, size):
                return b'\xcc' * size
        namespace = dict(recomp=types.SimpleNamespace(PE=PE, exe_fingerprint=fp), plausible_start=lambda *args: True,
                         listing=types.SimpleNamespace(load=lambda pe: types.SimpleNamespace(starts={target: None}, ends={})))
        exec(compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])), 'scan', 'exec'), namespace)
        self.assertEqual(namespace['add_data_referenced']([]), [])
        original_loop = '''for name, vs, va, rs, raw in pe.sections:
    if name.startswith(b".text"):
        continue
    if not any(va <= r_va < va + max(vs, rs) for n, r_va, _ in recomp.exe_fingerprint.REGIONS if n != ".text"):
        continue
    blob = pe.data[raw:raw + rs]
'''
        # Retain the production inner pointer scan and replace just its input.
        loop = next(n for n in function.body if isinstance(n, ast.For))
        broken = ast.parse(original_loop).body[0]
        broken.body.append(loop.body[-1])
        function.body[function.body.index(loop)] = broken
        exec(compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])), 'raw-scan', 'exec'), namespace)
        with self.assertRaises(AssertionError):
            self.assertEqual(namespace['add_data_referenced']([]), [])
        self.assertEqual(namespace['add_data_referenced']([]), [target])
        print('data-reference scan: fixed 1/1 passes; raw-padding mutant 1/1 fails')


class PipelineCounts(Fixture):
    def test_interpreter_relocation_preserves_identity(self):
        first, moved = self.home / 'Setup A/python', self.home / 'Setup B/python'
        write(first, b'interpreter A')
        write(moved, first.read_bytes())
        tokens = dict(recompile='recomp/build_all.py', tables='tools/gen_tables.py', ffmpeg='media/build_ffmpeg.sh',
                      audio='host/audio/build.sh', shaders='host/shaders/build.sh', runtime='runtime/build.sh')
        def run(executable):
            runner = FakeRunner(self.log, self.child_env, self.paths.work)
            with mock.patch.object(b.sys, 'executable', str(executable)):
                self.build(runner)
            return {name for name, token in tokens.items() if any(token in c for c in runner.commands)}
        self.assertEqual(run(first), set(STAGES))
        self.assertEqual(run(moved), set())
        # Restore the original unmapped interpreter argument in memory. The
        # same relocation oracle must reject its two unnecessary stage runs.
        tree = ast.parse((ROOT / 'tools/setup_kit/backend.py').read_text())
        build = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'build')
        roles = next(n.value for n in ast.walk(build) if isinstance(n, ast.Assign) and
                     any(isinstance(t, ast.Name) and t.id == 'roles' for t in n.targets))
        index = next(i for i, value in enumerate(roles.values) if isinstance(value, ast.Constant) and value.value == '$PYTHON')
        del roles.keys[index], roles.values[index]
        namespace = dict(b.__dict__)
        exec(compile(ast.fix_missing_locations(ast.Module(body=[build], type_ignores=[])), 'unmapped-interpreter', 'exec'), namespace)
        mutant = types.FunctionType(namespace['build'].__code__, b.__dict__, argdefs=namespace['build'].__defaults__)
        with mock.patch.object(b, 'build', mutant):
            run(first)
            affected = run(moved)
            self.assertEqual(affected, {'recompile', 'tables'})
            with self.assertRaises(AssertionError):
                self.assertEqual(affected, set())
        run(moved)  # restore the portable-argument stamps
        write(moved, b'interpreter B')
        self.assertEqual(run(moved), {'recompile', 'tables'})
        original = self.toolchain
        self.toolchain = lambda: dict(original(), python='different interpreter version')
        self.assertEqual(run(moved), {'recompile', 'tables'})
        print('interpreter relocation: original 2/6 rejected, fixed 0/6; changed bytes/version: 2/6 each')

    def test_original_stage_counts_include_runtime_metadata(self):
        self.assertEqual(hashlib.sha256(LEGACY_RUN.encode()).hexdigest(), LEGACY_SHA256)
        namespace = dict(json=json, os=os, digest_tree=b.digest_tree, remove=b.remove,
                         InstallError=b.InstallError, SetupError=b.SetupError)
        exec(LEGACY_RUN, namespace)
        def run(outer):
            source = self.home / 'old-builds' / outer / 'source'
            directory = source.parent / 'stamps'
            directory.mkdir(parents=True, exist_ok=True)
            cache = types.SimpleNamespace(directory=directory, events=self.events)
            runner = FakeRunner(self.log, self.child_env, self.paths.work)
            outputs = dict(recompile=['recomp/out', 'recomp/out/speed.a'], tables=['runtime/gen'],
                           ffmpeg=['third_party/ffmpeg'], audio=['host/audio/build/libhostaudio.a', 'host/audio/build/libmedia.a'],
                           shaders=['host/shaders/build/libsmtranslate.a'], runtime=['runtime/boot'])
            commands = dict(recompile=['recomp/build_all.py'], tables=['tools/gen_tables.py', '--pc', self.pc, '--no-x360'],
                            ffmpeg=['media/build_ffmpeg.sh'], audio=['host/audio/build.sh'],
                            shaders=['host/shaders/build.sh', 'lib'], runtime=['runtime/build.sh'])
            ran = set()
            for stage in STAGES:
                def build(stage=stage):
                    ran.add(stage)
                    runner.run(commands[stage], cwd=source)
                namespace['run'](cache, stage, [source / name for name in outputs[stage]], build)
            return ran
        # The synthetic runner uses the real generation input/stamp routines.
        for key in ('same', 'new-x360', 'new-runtime'):
            source = self.home / 'old-builds' / key / 'source'
            write(source / 'recomp/build_inputs.py', (self.source / 'recomp/build_inputs.py').read_bytes())
            write(source / 'tools/gen_tables.py', (self.source / 'tools/gen_tables.py').read_bytes())
        self.assertEqual(run('same'), set(STAGES))
        self.assertEqual(run('same'), {'recompile', 'tables'})
        self.assertEqual(run('same'), set())
        self.assertEqual(run('new-x360'), set(STAGES))
        self.assertEqual(run('new-runtime'), set(STAGES))
        print('original stage counts: first unchanged/X360/runtime = 2/6/6; steady unchanged = 0')

    def test_partial_producers_fail_before_the_next_stage(self):
        cases = dict(recompile='recomp/out/funcs.h', tables='runtime/gen/look_pc.inc',
                     ffmpeg='third_party/ffmpeg/include/libavformat/avformat.h')
        for stage, missing in cases.items():
            class PartialRunner(FakeRunner):
                def run(self, argv, cwd=None, capture=False):
                    result = super().run(argv, cwd, capture)
                    if cwd and (Path(cwd) / missing).exists():
                        (Path(cwd) / missing).unlink()
                    return result
            runner = PartialRunner(self.log, self.child_env, self.paths.work)
            original = b.BuildStageCache.run
            def omit_requirement(cache, name, fingerprint, source, relative, action):
                return original(cache, name, fingerprint, source, [n for n in relative if n != missing], action)
            def oracle():
                with self.assertRaisesRegex(b.SetupError, stage + ' completed without all required outputs'):
                    self.build(runner)
            for broken in (False, True):
                shutil.rmtree(self.paths.cache / 'stages', ignore_errors=True)
                if broken:
                    with mock.patch.object(b.BuildStageCache, 'run', new=omit_requirement):
                        with self.assertRaises(AssertionError, msg='missing output requirement survived: ' + stage):
                            oracle()
                else:
                    oracle()
        print('partial producer outputs: 3/3 rejected; required-output omission mutants: 3/3 detected')

    def test_reinstall_x360_only_and_runtime_only_counts(self):
        x360 = self.home / 'X360'
        movie = x360 / 'MOVIES/a.wmv'
        write(movie, 'movie A')
        def prepare(src, out, pc, progress):
            write(out / 'prepared/files/MOVIES/a.wmv', (src / 'MOVIES/a.wmv').read_bytes())
            return types.SimpleNamespace(roots={'x360_root': out / 'prepared/files'}, features=['movies'], missing=[])
        module = types.SimpleNamespace(prepare=prepare)
        def run(with_x360=False):
            runner = FakeRunner(self.log, self.child_env, self.paths.work)
            if with_x360:
                args = self.args()
                args.x360 = str(x360)
                with self.pipeline_stubs(), mock.patch.object(b.importlib, 'import_module', return_value=module):
                    b.build(args, self.paths, self.events, runner, self.source)
            else:
                self.build(runner)
            tokens = dict(recompile='recomp/build_all.py', tables='tools/gen_tables.py', ffmpeg='media/build_ffmpeg.sh',
                          audio='host/audio/build.sh', shaders='host/shaders/build.sh', runtime='runtime/build.sh')
            return {name for name, token in tokens.items() if any(token in c for c in runner.commands)}
        self.assertEqual(run(), set(STAGES))
        self.assertEqual(run(), set())
        write(self.source / 'runtime/boot.c', '// changed runtime\n')
        self.assertEqual(run(), {'runtime'})
        run(True)  # warm the X360-present configuration
        write(movie, 'movie B')
        self.assertEqual(run(True), set())
        print('pipeline counts: before first unchanged/X360-content/runtime = 2/6/6; after = 0/0/1')


if __name__ == '__main__':
    unittest.main(verbosity=2)
