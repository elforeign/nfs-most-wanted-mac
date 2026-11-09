#!/usr/bin/env python3
"""Artifact entry guards: real signed Setup, no payload build or game launch.

Review regression proof: old guards accept 7 broken Setup variants and omit the
offline version (8 failures); fixed guards pass 2 checks, rejecting all 7 variants.
Car release guards: 4 checks pass; driver failure, incomplete receipt, wrong code
pin, empty policy and changed trust artifact reject 5/5; missing inputs exit 3.
"""
import contextlib
import importlib.util
import io
import json
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


class ArtifactChecks(unittest.TestCase):
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
