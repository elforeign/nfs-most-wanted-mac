#!/usr/bin/env python3
"""Artifact entry guards: real signed Setup, no payload build or game launch.

Review regression proof: old guards accept 7 broken Setup variants and omit the
offline version (8 failures); fixed guards pass 2 checks, rejecting all 7 variants.
Carbon product (--game carbon): the installer prints the Carbon DMG and checksum names, the unchanged
Most Wanted names stay, a bad game is refused, and Setup identity is checked per product (4 subtests pass);
the pinned old installer fragment refuses `--game carbon` (exit 1 against 0 for the new script) and a Most Wanted
app presented as Carbon is rejected.
Carbon install audit entry: a signed Carbon Setup with a Carbon PC folder (NFSC.exe) reaches the install audit
(exit 0, audit called once for Carbon with that folder); Most Wanted-only parts (--proof) and a folder without
NFSC.exe are refused (exit 1 / 3). Broken control: the audit file before this change exits 3 without auditing
(this test: 2 failures there, exit 3 for the Carbon folder with and without --proof; 0 here).
Car release guards: 4 checks pass; driver failure, incomplete receipt, wrong code
pin, empty policy and changed trust artifact reject 5/5; missing inputs exit 3.
Image record: the build record of a Setup install names a staging copy of NFSC.exe that no longer exists; the real
binding-proof driver skips on it (audit fails) and, with the audited executable passed, no longer does.
Exported Carbon capability proof: PC-only and optional-Xbox each PASS/exit 0;
missing settings registration, forced-PC resolver and forced-PC binding adapter
each FAIL/exit 1 (6/6 broken exports rejected, fixed 2/2).
Packaged update verifier: absent audit fails 1/1; fixed accepts 1/1 and refuses 3/3 broken payloads.
Publication findings are reported separately; this fixture cannot authorize a release.
The native evaluator comes from the local PC build; every provider, selector and
test is read from the publication export. No console binding data is copied.
"""
import contextlib
import importlib.util
import io
import json
import re
import os
from pathlib import Path
import plistlib
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('artifact', HERE / 'test_release_artifact.py')
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)


# The main-branch argument handling before --game existed (build_installer.sh), pinned by SHA-256.
OLD_INSTALLER_HEAD = 'MIN_MACOS=14.5          # the one place the macOS floor is set (raise or lower it here)\nPRODUCT="NFS-Most-Wanted-Native-Setup"\nNAME="NFS Most Wanted Native Setup"\nPYTHON="${PYTHON:-python3}"\n\ndie() { echo "build_installer: $*" >&2; exit 1; }\n\n# Accepts 4.0.0 or v4.0.0 (2 to 4 numeric parts); prints the number without the v.\nnorm_version() {\n    v="${1#v}"\n    printf \'%s\\n\' "$v" | grep -Eq \'^[0-9]+(\\.[0-9]+){1,3}$\' || die "version must look like 4.0.0 (got \'$1\')"\n    printf \'%s\\n\' "$v"\n}\ndmg_name() { nv="$(norm_version "$1")" || exit 1; echo "$PRODUCT-v$nv.dmg"; }\n\nMODE=release\ncase "${1:-}" in\n    --print-names) [ $# -eq 2 ] || die "usage: --print-names VERSION"\n        n="$(dmg_name "$2")"; echo "$n"; echo "$n.sha256"; exit 0 ;;\n'
OLD_INSTALLER_SHA256 = '6621a08176626132ce7da6857bcb93021d2d10a10b071c7f4803d506612d16f9'


class ArtifactChecks(unittest.TestCase):
    def test_carbon_capability_checks_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            driver = root / 'recomp/private_pad_layout_test.py'
            driver.parent.mkdir()
            with self.assertRaisesRegex(RuntimeError, 'missing exported'):
                a.audit_carbon_capabilities(root, sys.executable, root, os.environ)
            driver.write_text('import sys; sys.exit(3)\n')
            with self.assertRaisesRegex(RuntimeError, 'failed or skipped'):
                a.audit_carbon_capabilities(root, sys.executable, root, os.environ)

    def test_image_record_follows_the_audited_executable(self):
        """The built image record names the Setup's staging copy of NFSC.exe, gone after install.

        Real binding-proof driver against a record naming a missing executable: it skips (exit 3), so the
        audit fails; with the audited executable passed, the record is repointed and the driver gets past
        that skip (this stand-in file has the wrong bytes, so it stops at the pinned hash instead).
        """
        driver = a.ROOT / 'recomp/private_padbind_public_test.py'
        if not driver.is_file():
            self.skipTest('requires the Carbon recompiler tests')
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'recomp').mkdir()
            for name in (driver.name, 'recomp.py', 'exe_fingerprint.py'):
                shutil.copy2(driver.parent / name, root / 'recomp' / name)
            for other in ('private_pad_layout_test', 'private_port_settings_test'):  # not under test here
                (root / 'recomp' / (other + '.py')).write_text('pass\n')
            base = root / 'recomp/out/carbon-base'
            base.mkdir(parents=True)
            for name in ('table.c', 'funcs.h', 'NFSC.a'):
                (base / name).write_text('x')
            staged = root / 'stage/carbon-data/pc/NFSC.exe'  # recorded at build time, renamed away by the install
            record = base / 'image.json'
            record.write_text(json.dumps({'exe': str(staged)}))
            held = root / 'held.json'  # the build's own record, hard-linked like an exported tree
            os.link(record, held)
            exe = root / 'installed/NFSC.exe'
            exe.parent.mkdir()
            exe.write_bytes(b'not the pinned executable')
            with self.assertRaisesRegex(RuntimeError, 'skipped: private_padbind_public_test\n.*image absent'):
                a.audit_carbon_capabilities(root, sys.executable, root, dict(os.environ))
            with self.assertRaisesRegex(RuntimeError, 'executable missing'):
                a.audit_carbon_capabilities(root, sys.executable, root, dict(os.environ), exe=root / 'gone.exe')
            with self.assertRaises(RuntimeError) as later:
                a.audit_carbon_capabilities(root, sys.executable, root, dict(os.environ), exe=exe)
            self.assertNotIn('image absent', str(later.exception))
            self.assertEqual(json.loads(record.read_text())['exe'], str(exe.resolve()))
            self.assertEqual(json.loads(held.read_text())['exe'], str(staged))

    def test_exported_carbon_capabilities(self):
        """Real publication selection/redaction, then real exported C/ObjC proofs."""
        importer = a.ROOT / 'tools/publish/import.py'
        base = a.ROOT / 'recomp/out/carbon-base'
        if not importer.is_file() or not (base / 'NFSC.a').is_file():
            self.skipTest('requires publication tooling and the local Carbon PC build')
        spec = importlib.util.spec_from_file_location('capability_publication', importer)
        publish = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(publish)
        with tempfile.TemporaryDirectory(prefix='artifact-capabilities-') as td:
            root = Path(td).resolve()
            indexed = root / 'indexed'; indexed.mkdir()
            exported = root / 'exported'
            # Snapshot the selected working bytes into a scratch index so the
            # importer's real index-equality guard also covers uncommitted edits.
            allowed = publish.patterns(publish.HERE / 'allowlist.txt')
            denied = publish.deny_patterns(with_carbon=True)
            # New providers must enter this working-tree fixture too; a branch
            # under review need not stage files in its real Git index first.
            untracked = subprocess.run(['git', '-C', str(a.ROOT), 'ls-files',
                '--others', '--exclude-standard', '-z'], check=True,
                capture_output=True, text=True).stdout.split('\0')
            candidates = set(publish.tracked_files(a.ROOT)) | set(filter(None, untracked))
            selected = [name for name in sorted(candidates)
                        if (a.ROOT / name).is_file() and not (a.ROOT / name).is_symlink()
                        and not publish.deny(name, denied)
                        and any(publish.matches(name, p) for p in allowed)]
            for name in selected:
                target = indexed / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(a.ROOT / name, target)
            for argv in (['git', 'init', '-q', str(indexed)], ['git', '-C', str(indexed), 'add', '-A', '-f']):
                subprocess.run(argv, check=True, capture_output=True)
            # Reproduce the importer's selected/redacted source bytes without
            # authorizing publication. Its independent scan still gates release;
            # unrelated findings must not prevent exercising broken capabilities.
            scanner = publish.scan.Scanner(publish.HERE)
            index = publish.tracked_files(indexed)
            for name in publish.selected_files(indexed, allowed, denied, index=index):
                target = exported / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(publish.redact.redact_bytes(name,
                    publish.read_indexed_file(indexed, name, index[name]), scanner))
            findings = scanner.scan_tree(exported)
            print('Carbon publication findings=' + str(len(findings)))
            for name in ('carbon_padbind_ps3.inc', 'carbon_padbind_x360.inc',
                         'carbon_worldreflect_shader.inc', 'world_probe.inc'):
                self.assertFalse(list(exported.rglob(name)), name)
            # These are immutable PC translation inputs, not private providers.
            # Hard links keep the fixture small; no proof writes to this directory.
            shutil.copytree(base, exported / 'recomp/out/carbon-base', copy_function=os.link)
            private = exported / 'recomp/private_runtime'
            settings = private / 'game_settings.h'
            resolver = private / 'carbon_pad_layout.c'
            adapter = private / 'carbon_padbind.c'
            original = {p: p.read_text() for p in (settings, resolver, adapter)}
            # Reuse the pinned original adapter, without copying console fixtures.
            spec = importlib.util.spec_from_file_location('exported_binding_proof',
                exported / 'recomp/private_padbind_public_test.py')
            binding = importlib.util.module_from_spec(spec); spec.loader.exec_module(binding)
            port = '#define CARBON_PADBIND_PORT 1\n#include "carbon_padbind_port.h"'
            mutations = (
                ('missing-settings', settings, '    carbon_port_settings_init();\n', ''),
                ('forced-pc-resolver', resolver, '    Setting *row =',
                 '    choice.layout = CARBON_LAYOUT_PC;\n    Setting *row ='),
                ('forced-pc-adapter', adapter, port,
                 binding.ORIGINAL[binding.ORIGINAL.index('static CarbonPadLayoutChoice'):] + '\n' + port),
            )
            env = {k: v for k, v in os.environ.items() if not k.startswith(('NFS_', 'BOOT_'))}
            env['NFS_PUBLIC'] = '1'
            pc = root / 'pc'; pc.mkdir()
            xbox = root / 'xbox-input'; xbox.mkdir()
            for mode in ('pc-only', 'optional-xbox'):
                roots = root / (mode + '.txt')
                roots.write_text('pc_root=' + str(pc) + '\n' +
                                 ('x360_root=' + str(xbox) + '\n' if mode == 'optional-xbox' else ''))
                run_env = dict(env, NFS_ROOTS=str(roots))
                scratch = root / mode; scratch.mkdir()
                with contextlib.redirect_stdout(io.StringIO()):
                    a.audit_carbon_capabilities(exported, sys.executable, scratch, run_env)
                for name, path, before, after in mutations:
                    with self.subTest(mode=mode, broken=name):
                        self.assertEqual(original[path].count(before), 1)
                        path.write_text(original[path].replace(before, after))
                        try:
                            with contextlib.redirect_stdout(io.StringIO()), \
                                    self.assertRaisesRegex(RuntimeError, 'exported Carbon capability failed') as failure:
                                a.audit_carbon_capabilities(exported, sys.executable, scratch, run_env)
                            if name != 'missing-settings':
                                self.assertRegex(str(failure.exception), r'failures=[1-9][0-9]*')
                            if name == 'forced-pc-adapter':
                                self.assertRegex(str(failure.exception), r'bindings=[1-9][0-9]*/')
                        finally:
                            path.write_text(original[path])
            print('exported Carbon: fixed PASS=2 broken FAIL=6')

    def test_carbon_product(self):
        import hashlib
        installer = a.ROOT / 'tools/installer/build_installer.sh'
        def names(*argv, script=installer):
            return subprocess.run(['/bin/sh', str(script), *argv], capture_output=True, text=True)
        mw = ['NFS-Most-Wanted-Native-Setup-v4.6.0.dmg', 'NFS-Most-Wanted-Native-Setup-v4.6.0.dmg.sha256']
        cb = ['NFS-Carbon-Native-Setup-v1.0.0.dmg', 'NFS-Carbon-Native-Setup-v1.0.0.dmg.sha256']
        for argv, want in ((('--print-names', '4.6.0'), mw), (('--print-names', '4.6.0', '--game', 'mw'), mw),
                           (('--print-names', '1.0.0', '--game', 'carbon'), cb),
                           (('--game', 'carbon', '--print-names', 'v1.0.0'), cb)):
            with self.subTest(argv=argv):
                result = names(*argv)
                self.assertEqual((result.returncode, result.stdout.split()), (0, want), result.stderr)
        for bad in (('--game', 'x'), ('--game',)):
            with self.subTest(bad=bad):
                self.assertNotEqual(names('--print-names', '1.0.0', *bad).returncode, 0)
        # Broken control: the installer's head before this change refuses the Carbon product.
        self.assertEqual(hashlib.sha256(OLD_INSTALLER_HEAD.encode()).hexdigest(), OLD_INSTALLER_SHA256)
        with tempfile.TemporaryDirectory() as td:
            old = Path(td) / 'old.sh'
            old.write_text('set -eu\n' + OLD_INSTALLER_HEAD + 'esac\n')
            self.assertEqual(names('--print-names', '4.6.0', script=old).returncode, 0)
            self.assertEqual(names('--print-names', '1.0.0', '--game', 'carbon', script=old).returncode, 1)
            # Setup identity per product: a Most Wanted app is not a Carbon Setup, and the reverse.
            for bundle_id, product, game, ok in (
                    ('org.nfscarbon-native.setup', 'carbon', 'carbon', True),
                    ('org.nfsmw-native.setup', None, 'carbon', False),
                    ('org.nfscarbon-native.setup', 'carbon', 'mw', False),
                    ('org.nfsmw-native.setup', None, 'mw', True)):
                app = Path(td) / (bundle_id + str(game) + '.app')
                (app / 'Contents/MacOS').mkdir(parents=True)
                src = Path(td) / 'main.c'; src.write_text('int main(void) { return 0; }\n')
                subprocess.run(['clang', str(src), '-o', str(app / 'Contents/MacOS/Setup')], check=True, capture_output=True)
                info = dict(CFBundleIdentifier=bundle_id, CFBundleExecutable='Setup', CFBundlePackageType='APPL',
                            CFBundleShortVersionString='1.0.0', CFBundleVersion='1.0.0')
                if product: info['SetupProduct'] = product
                (app / 'Contents/Info.plist').write_bytes(plistlib.dumps(info))
                subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', '--timestamp=none', str(app)],
                               check=True, capture_output=True)
                if ok:
                    a.verify_setup(app, '1.0.0', game)
                else:
                    with self.assertRaisesRegex(RuntimeError, 'wrong Setup bundle identity'):
                        a.verify_setup(app, '1.0.0', game)

    def test_carbon_install_audit_entry(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            src = root / 'main.c'; src.write_text('int main(void) { return 0; }\n')
            setup = root / 'NFS Carbon Native Setup.app'
            (setup / 'Contents/MacOS').mkdir(parents=True)
            (setup / 'Contents/Resources').mkdir()
            (setup / 'Contents/Resources/source.tar.gz').write_bytes(b'fixture payload')
            subprocess.run(['clang', str(src), '-o', str(setup / 'Contents/MacOS/Setup')], check=True, capture_output=True)
            (setup / 'Contents/Info.plist').write_bytes(plistlib.dumps(dict(
                CFBundleIdentifier='org.nfscarbon-native.setup', CFBundleExecutable='Setup', CFBundlePackageType='APPL',
                CFBundleShortVersionString='1.0.0', CFBundleVersion='1.0.0', SetupProduct='carbon')))
            subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', '--timestamp=none', str(setup)],
                           check=True, capture_output=True)
            carbon = root / 'NFSC'; carbon.mkdir(); (carbon / 'NFSC.exe').write_bytes(b'fixture')
            mw_only = root / 'MW'; mw_only.mkdir(); (mw_only / 'speed.exe').write_bytes(b'fixture')
            for folder, extra, status, audited in ((carbon, [], 0, 1), (carbon, ['--proof'], 1, 0), (mw_only, [], 3, 0)):
                with self.subTest(folder=folder.name, extra=extra):
                    argv = ['audit', '--game', 'carbon', '--setup', str(setup), '--pc', str(folder),
                            '--expected-version', '1.0.0', *extra]
                    with mock.patch.object(sys, 'argv', argv), mock.patch.object(a, 'audit') as audit, \
                            contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                        self.assertEqual(a.main(), status)
                        self.assertEqual(audit.call_count, audited)
                        if audited:
                            self.assertEqual(audit.call_args.args[1], carbon)
                            self.assertEqual(audit.call_args.kwargs.get('game'), 'carbon')
            xbox = root / 'xbox'; xbox.mkdir()
            argv = ['audit', '--game', 'carbon', '--setup', str(setup), '--pc', str(carbon),
                    '--x360', str(xbox), '--expected-version', '1.0.0']
            with mock.patch.object(sys, 'argv', argv), mock.patch.object(a, 'audit') as audit, \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(a.main(), 0)
                self.assertEqual(audit.call_count, 2)
                pc_only, optional = audit.call_args_list
                self.assertEqual(pc_only.kwargs, dict(game='carbon'))
                self.assertEqual(pc_only.args[1], carbon)
                self.assertEqual(optional.args[5], xbox)
                self.assertEqual(optional.kwargs, dict(game='carbon'))

    def test_missing_artifact_or_input_remains_skip(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            pc = root / 'pc'; pc.mkdir()
            setup = root / 'Setup.app'
            for existing in (False, True):
                if existing:
                    setup.mkdir()
                with mock.patch.object(sys, 'argv', ['audit', '--setup', str(setup), '--pc', str(pc),
                                                    '--expected-version', '4.3.1']), \
                        mock.patch.object(a, 'audit') as audit, contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(a.main(), 3)
                    audit.assert_not_called()

    def test_car_release_guards(self):
        """Guard controls use synthetic receipts; real execution is test_car_release."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            source = root / 'source'
            names = ('tests/setup/test_car_release.py', 'tools/setup_kit/car_approvals.json',
                     'runtime/car_approvals_public.h', 'tools/setup_kit/car_catalog.py',
                     'tools/setup_kit/car_sources.json', 'tools/setup_kit/huff.py')
            for name in names:
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(a.ROOT / name, path)
            import hashlib
            codec_sha = hashlib.sha256((source / names[-1]).read_bytes()).hexdigest()
            receipt = dict(synthetic=True, commands=12, source_unchanged=True, saves_unchanged=True,
                reports=[dict(platform=p, id=95, outputs=7, rejected_pin_mutants=13,
                              public_huff_sha256=codec_sha) for p in ('pc', 'x360')])
            python = root / 'shipped-python'
            (source / names[1]).write_text(json.dumps(dict(schema=1, revision=1,
                slots=[dict(slot=s, identity=None, variants=[]) for s in range(90)])))
            for fault in ('driver', 'receipt', 'private-code-pin', 'empty-policy', 'changed-policy'):
                with self.subTest(fault=fault):
                    result = dict(receipt)
                    if fault == 'receipt': result['commands'] = 11
                    if fault == 'private-code-pin':
                        result['reports'] = [dict(row, public_huff_sha256='0' * 64) for row in receipt['reports']]
                    def run(args, **kwargs):
                        self.assertEqual(args[:3], [str(python), '-I', str(source / names[0])])
                        if fault == 'changed-policy' and args[-1] == '--synthetic':
                            (source / 'runtime/car_approvals_public.h').write_text('changed')
                        return subprocess.CompletedProcess(args, 1 if fault == 'driver' else 0,
                                                           json.dumps(result) + '\n', '')
                    reason = {'driver': 'shipped car command proof failed',
                              'receipt': 'incomplete shipped car lifecycle receipt',
                              'private-code-pin': 'do not pin the shipped decoder',
                              'empty-policy': 'no reviewed real-car approvals',
                              'changed-policy': 'synthetic policy escaped'}[fault]
                    with mock.patch.object(a.subprocess, 'run', side_effect=run), \
                            self.assertRaisesRegex(RuntimeError, reason):
                        a.audit_car_commands(source, python, root, {})
                    shutil.copyfile(a.ROOT / 'runtime/car_approvals_public.h', source / 'runtime/car_approvals_public.h')
            doc = json.loads((source / names[1]).read_text())
            doc['slots'][0].update(identity='1' * 64,
                variants=[dict(contract='2' * 64, compatibility='3' * 64)])
            (source / names[1]).write_text(json.dumps(doc))
            result = subprocess.CompletedProcess([], 0, json.dumps(receipt) + '\n', '')
            with mock.patch.object(a.subprocess, 'run', return_value=result), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(a.audit_car_commands(source, python, root, {}), receipt)
            (source / names[0]).unlink()
            with self.assertRaisesRegex(RuntimeError, 'missing shipped'):
                a.audit_car_commands(source, python, root, {})

    def test_packaged_update_verifier(self):
        with tempfile.TemporaryDirectory(prefix='artifact-trust-') as directory:
            root = Path(directory).resolve()
            for fault in ('good', 'missing', 'pins', 'signature-bypass'):
                source = root / fault
                for rel in ('runtime/pub_update_manifest_test.py', 'runtime/pub_update_manifest.h',
                            'runtime/pub_update_keys.h', 'tools/setup_kit/update_manifest.py'):
                    path = source / rel; path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(a.ROOT / rel, path)
                verifier = source / 'tools/setup_kit/update_manifest.py'
                if fault == 'missing': verifier.unlink()
                elif fault == 'pins':
                    verifier.write_text(re.sub(r'(?m)^PINS = .*$',
                        lambda m: m.group(0) + '\nPINS = (bytes([4]) + bytes(64), PINS[1])',
                        verifier.read_text()))
                elif fault == 'signature-bypass':
                    verifier.write_text(verifier.read_text().replace(
                        'valid = sec.SecKeyVerifySignature(key, algorithm, message, sig, ctypes.byref(error))', 'valid = True'))
                scratch = root / ('scratch-' + fault); scratch.mkdir()
                if fault == 'good':
                    a.audit_update_verifier(source, Path(sys.executable), scratch, dict(os.environ))
                else:
                    with self.assertRaisesRegex(RuntimeError, 'update verifier'):
                        a.audit_update_verifier(source, Path(sys.executable), scratch, dict(os.environ))

    def test_setup_guards(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / 'main.c'; source.write_text('int main(void) { return 0; }\n')
            binary = root / 'Setup'
            subprocess.run(['clang', str(source), '-o', str(binary)], check=True, capture_output=True)
            pc = root / 'pc'; pc.mkdir(); (pc / 'speed.exe').write_bytes(b'fixture')
            for fault in ('good', 'identity', 'executable-name', 'missing', 'empty', 'mode', 'damaged', 'unsigned'):
                with self.subTest(fault=fault):
                    setup = root / (fault + '.app')
                    resources = setup / 'Contents/Resources'; resources.mkdir(parents=True)
                    (resources / 'source.tar.gz').write_bytes(b'fixture payload')
                    executable = setup / 'Contents/MacOS/Setup'; executable.parent.mkdir()
                    shutil.copy2(binary, executable)
                    info = dict(CFBundleIdentifier='org.nfsmw-native.setup', CFBundleExecutable='Setup',
                                CFBundlePackageType='APPL', CFBundleShortVersionString='4.3.1', CFBundleVersion='4.3.1')
                    if fault == 'identity': info['CFBundleIdentifier'] = 'invalid.fixture'
                    if fault == 'executable-name': info['CFBundleExecutable'] = '../Setup'
                    (setup / 'Contents/Info.plist').write_bytes(plistlib.dumps(info))
                    subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', '--timestamp=none', str(setup)],
                                   check=True, capture_output=True)
                    if fault == 'missing': executable.unlink()
                    if fault == 'empty': executable.write_bytes(b'')
                    if fault == 'mode': executable.chmod(0o644)
                    if fault == 'damaged':
                        data = bytearray(executable.read_bytes()); data[4096] ^= 1; executable.write_bytes(data)
                    if fault == 'unsigned':
                        subprocess.run(['/usr/bin/codesign', '--remove-signature', str(executable)],
                                       check=True, capture_output=True)
                    output = io.StringIO()
                    with mock.patch.object(sys, 'argv', ['audit', '--setup', str(setup), '--pc', str(pc),
                                                        '--expected-version', '4.3.1']), \
                            mock.patch.object(a, 'audit') as audit, contextlib.redirect_stderr(output):
                        self.assertEqual(a.main(), 0 if fault == 'good' else 1, output.getvalue())
                        self.assertEqual(audit.call_count, 1 if fault == 'good' else 0)

    def test_offline_supplies_current_version(self):
        script = (a.ROOT / 'runtime/offline_tests.sh').read_text()
        function = script[script.index('run_one() {'):script.index('\nif [ "$1" = "--one" ];')]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            capture = root / 'args.json'
            wrapper = root / 'python'
            code = 'import json, pathlib, sys; pathlib.Path(' + repr(str(capture)) + ').write_text(json.dumps(sys.argv[1:]))'
            wrapper.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' -I -c ' + shlex.quote(code) + ' "$@"\n')
            wrapper.chmod(0o755)
            env = dict(os.environ, py=str(wrapper), TOOLS_PY=str(wrapper), CODEX_SANDBOX='')
            result = subprocess.run(['/bin/sh', '-c', function + '\nrun_one test_release_artifact "$1"',
                                     'offline-check', str(root / 'work')], cwd=a.ROOT, env=env, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            args = json.loads(capture.read_text())
            self.assertIn('--expected-version', args)
            import ast
            tree = ast.parse((a.ROOT / 'tools/setup_kit/backend.py').read_text())
            version = next(node.value.value for node in tree.body if isinstance(node, ast.Assign) and
                           any(isinstance(t, ast.Name) and t.id == 'PUBLIC_VERSION' for t in node.targets))
            self.assertEqual(args[args.index('--expected-version') + 1], version)


if __name__ == '__main__':
    unittest.main()
