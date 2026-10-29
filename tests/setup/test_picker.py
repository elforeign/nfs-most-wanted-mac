#!/usr/bin/env python3
"""




"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SWIFT = ROOT / "tools/installer/SetupApp.swift"
sys.path.insert(0, str(ROOT / "tools"))
from setup_kit import backend


def extract_function(source):
    start = source.index("func pcRoot")
    brace = source.index("{", start)
    depth = 0
    for i in range(brace, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start:i + 1]
    raise AssertionError("pcRoot closing brace not found")


def compile_root(tmp, function, label):
    source = Path(tmp) / (label + ".swift")
    binary = Path(tmp) / label
    source.write_text("import Foundation\n" + function + '\nprint(pcRoot(URL(fileURLWithPath: CommandLine.arguments[1])).path)\n')
    result = subprocess.run(["swiftc", "-module-cache-path", str(Path(tmp) / "modules"),
                             str(source), "-o", str(binary)], capture_output=True, text=True)
    if result.returncode:
        raise AssertionError("swiftc failed: " + result.stderr[-2000:])
    return binary


class PickerTests(unittest.TestCase):
    def test_pc_root_and_backend(self):
        with tempfile.TemporaryDirectory(prefix="setup-picker-") as td:
            scratch = Path(td)
            game = scratch / "game"
            (game / "GLOBAL").mkdir(parents=True)
            for name in ("TRACKS", "CARS", "FRONTEND", "SOUND"):
                (game / name).mkdir()
            (game / "speed.exe").write_bytes(b"fixture exe")
            nested = game / "GLOBAL" / "x.bin"
            nested.write_bytes(b"x")
            archive = scratch / "g.zip"
            archive.write_bytes(b"zip fixture")
            fp = scratch / "payload" / "recomp" / "exe_fingerprint.py"
            fp.parent.mkdir(parents=True)
            fp.write_text("def check(data, name):\n    return 'fixture fingerprint'\n")
            cases = ((game / "speed.exe", game, True), (game, game, True),
                     (archive, archive, False), (nested, game / "GLOBAL", False))

            function = extract_function(SWIFT.read_text())
            binary = compile_root(scratch, function, "current")
            for index, (picked, expected, accepted) in enumerate(cases):
                result = subprocess.run([str(binary), str(picked)], capture_output=True, text=True, check=True)
                actual = Path(result.stdout.strip())
                self.assertEqual(actual, expected, f"case {index}: pcRoot({picked})")
                try:
                    backend.validate_pc(actual, scratch / "payload")
                    got_accepted = True
                except backend.SetupError:
                    got_accepted = False
                self.assertEqual(got_accepted, accepted, f"case {index}: validate_pc({actual})")

            broken_function = function.replace("return url.deletingLastPathComponent()", "return url")
            # The broken control models the reported regression: file selections stay files.
            broken = compile_root(scratch, broken_function, "broken")
            failures = 0
            output = subprocess.run([str(broken), str(game / "speed.exe")], capture_output=True, text=True, check=True)
            broken_root = Path(output.stdout.strip())
            try:
                backend.validate_pc(broken_root, scratch / "payload")
            except backend.SetupError:
                failures += 1
            self.assertEqual(failures, 1, "broken pcRoot control must fail the speed.exe acceptance oracle")


if __name__ == "__main__":
    if shutil.which("swiftc") is None:
        print("SKIP: swiftc unavailable")
        raise SystemExit(3)
    unittest.main(verbosity=2)
