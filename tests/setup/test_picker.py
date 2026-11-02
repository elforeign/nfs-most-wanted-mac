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


def extract_function(source, name="func pcRoot"):
    start = source.index(name)
    brace = source.index("{", start)
    depth = 0
    for i in range(brace, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start:i + 1]
    raise AssertionError(name + " closing brace not found")


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

    def test_missing_disc_numbers_read_from_the_backend_messages(self):
        """The disc page offers "Disc N: Choose..." for exactly the discs the backend's check could not find."""
        sys.path.insert(0, str(ROOT / "tests/setup"))
        from test_backend import make_iso
        from setup_kit import pc_disc
        with tempfile.TemporaryDirectory(prefix="setup-discs-") as td:
            scratch = Path(td)
            inf = "[Special]\r\nDisk={}\r\nProductGuiID={{FIXTURE}}\r\n"
            listing = "1,1,speed.exe\r\n2,1,\r\n3,1,\r\n4,1,\r\n"
            make_iso(scratch / "a.iso", {"autorun.inf": inf.format(1).encode(), "common_filelist.txt": listing.encode(),
                                         "speed.exe": b"x", "0compressed.zip": b"z"})
            make_iso(scratch / "b.iso", {"autorun.inf": inf.format(3).encode(), "2compressed.zip": b"z"})
            (scratch / "alone").mkdir()
            make_iso(scratch / "alone/c.iso", {"autorun.inf": inf.format(2).encode(), "1compressed.zip": b"z"})
            make_iso(scratch / "Disc of the set.iso", {"autorun.inf": inf.replace("FIXTURE", "OTHER").format(2).encode(),
                                                        "1compressed.zip": b"z"})
            messages = {}
            for chosen, extra, expected in ((scratch / "a.iso", (), [2, 4]), (scratch / "alone/c.iso", (), [1]),
                                            (scratch / "a.iso", [scratch / "Disc of the set.iso"], [])):
                with self.assertRaises(pc_disc.DiscError) as caught:
                    pc_disc.disc_set(chosen, extra)
                messages[str(caught.exception)] = expected
            self.assertIn("Disc of the set.iso is not a disc of the same game set", list(messages)[2])
            messages["The .zip can't be read: Bad magic number"] = []
            messages["Disc 1 has no speed.exe."] = []
            source = scratch / "missing.swift"
            source.write_text("import Foundation\n" + extract_function(SWIFT.read_text(), "func missingDiscNumbers") +
                              "\nfor line in CommandLine.arguments.dropFirst() { print(missingDiscNumbers(line)) }\n")
            subprocess.run(["swiftc", "-module-cache-path", str(scratch / "modules"), str(source), "-o", str(scratch / "missing")],
                           check=True, capture_output=True)
            output = subprocess.run([str(scratch / "missing"), *messages], capture_output=True, text=True, check=True)
            self.assertEqual(output.stdout.splitlines(), [str(v) for v in messages.values()])


if __name__ == "__main__":
    if shutil.which("swiftc") is None:
        print("SKIP: swiftc unavailable")
        raise SystemExit(3)
    unittest.main(verbosity=2)
