"""tools/toolchain.py: the compiler check a game's Setup runs before it builds (run 37, D-203a)."""

import importlib.util
import os
from pathlib import Path
import stat
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec_ = importlib.util.spec_from_file_location("toolchain", ROOT / "tools/toolchain.py")
toolchain = importlib.util.module_from_spec(spec_)
spec_.loader.exec_module(toolchain)

SPEC = {"tested_clang": "Apple clang 21.0.0 (clang-2100.3.34.2)", "tested_sdk": "27.0", "minimum_clang": "16",
        "minimum_sdk": "15"}


class ToolchainTests(unittest.TestCase):
    def test_the_kit_names_a_tested_compiler_and_a_minimum(self):
        spec = toolchain.read_spec()
        self.assertRegex(spec["tested_clang"], r"^Apple clang \d+\.\d+\.\d+ \(clang-[\d.]+\)$")
        self.assertLessEqual(int(spec["minimum_clang"]), int(spec["tested_clang"].split()[2].split(".")[0]))
        self.assertLessEqual(int(spec["minimum_sdk"]), int(spec["tested_sdk"].split(".")[0]))

    def test_the_tested_compiler_is_named_as_such(self):
        ok, line = toolchain.check(SPEC, "Apple clang version 21.0.0 (clang-2100.3.34.2)", "27.0")
        self.assertTrue(ok)
        self.assertEqual(line, "toolchain: Apple clang 21.0.0 (clang-2100.3.34.2), macOS SDK 27.0: the tested compiler")

    def test_another_compiler_above_the_minimum_builds_and_says_so(self):
        for clang, sdk in (("Apple clang version 17.0.0 (clang-1700.0.13.5)", "15.5"),
                           ("Apple clang version 21.0.0 (clang-2100.3.34.2)", "26.4")):
            ok, line = toolchain.check(SPEC, clang, sdk)
            self.assertTrue(ok, line)
            self.assertIn("not the tested compiler (tested with Apple clang 21.0.0 (clang-2100.3.34.2), macOS SDK "
                          "27.0)", line)

    def test_below_the_minimum_is_refused_with_the_way_to_update(self):
        for clang, sdk in (("Apple clang version 15.0.0 (clang-1500.3.9.4)", "14.5"),
                           ("Apple clang version 16.0.0 (clang-1600.0.26.6)", "14.5")):
            ok, line = toolchain.check(SPEC, clang, sdk)
            self.assertFalse(ok, line)
            self.assertIn("older than this game needs", line)
            self.assertIn("xcode-select --install", line)

    def test_an_unreadable_compiler_is_reported_not_refused(self):
        ok, line = toolchain.check(SPEC, "clang version 21.1.0 (https://github.com/llvm/llvm-project)", "")
        self.assertTrue(ok)
        self.assertIn("could not read the compiler's version", line)

    def test_probe_reads_a_fake_old_clang(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "clang"
            fake.write_text("#!/bin/sh\necho 'Apple clang version 14.0.3 (clang-1403.0.22.14.1)'\n"
                            "echo 'Target: arm64-apple-darwin'\n")
            fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
            clang_line, _sdk = toolchain.probe(str(fake))
            self.assertEqual(clang_line, "Apple clang version 14.0.3 (clang-1403.0.22.14.1)")
            self.assertFalse(toolchain.check(SPEC, clang_line, "13.3")[0])
        self.assertEqual(toolchain.probe(os.path.join(tmp, "missing"))[0], "")


if __name__ == "__main__":
    unittest.main()
