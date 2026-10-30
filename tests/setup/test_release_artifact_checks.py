#!/usr/bin/env python3
"""Artifact entry guards: real signed Setup, no payload build or game launch.

Review regression proof: old guards accept 7 broken Setup variants and omit the
offline version (8 failures); fixed guards pass 2 checks, rejecting all 7 variants.
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
