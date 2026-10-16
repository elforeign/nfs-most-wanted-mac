"""tools/build.py argument handling and CMake invocation, without CMake or game files."""

import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("build_py", Path(__file__).parents[1] / "tools/build.py")
build_py = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_py)


class BuildPyTests(unittest.TestCase):
    def test_default_preset_follows_the_operating_system(self):
        self.assertEqual(build_py.default_preset("Darwin"), "macos")
        self.assertEqual(build_py.default_preset("Linux"), "linux")
        self.assertEqual(build_py.default_preset("Windows"), "windows")

    def test_debug_config_selects_the_debug_preset(self):
        self.assertEqual(build_py.preset_name("macos", "Release"), "macos")
        self.assertEqual(build_py.preset_name("linux", "Debug"), "linux-debug")

    def test_archive_path_per_platform(self):
        root = Path("/r/build")
        self.assertEqual(build_py.archive_path(root, "Darwin"), root / "cmake/macos/lib/librecomp_gen.a")
        self.assertEqual(build_py.archive_path(root, "Windows"), root / "cmake/windows/lib/recomp_gen.lib")
        self.assertEqual(build_py.archive_path(root, "Darwin", "macos-debug"),
                         root / "cmake/macos-debug/lib/librecomp_gen.a")

    def test_desktop_hosts_are_allowed_on_linux_and_windows(self):
        for system, preset in (("Linux", "linux"), ("Windows", "windows")):
            for target in ("app", "smoke", "headless", "fixture"):
                for extra in ([], ["--regenerate"]):
                    with self.subTest(system=system, target=target, extra=extra):
                        args, _ = build_py.parse_args(["--target", target] + extra, system=system)
                        self.assertEqual(args.preset, preset)
                        self.assertEqual(args.regenerate, bool(extra))

    def test_web_target_uses_the_web_presets(self):
        self.assertEqual(build_py.preset_name("macos", "Release", target="web"), "web")
        self.assertEqual(build_py.preset_name("linux", "Release", stub=True, target="web"), "web-stub")
        with patch.dict(os.environ, {"EMSDK": "/emsdk"}):
            args, _ = build_py.parse_args(["--target", "web"], system="Linux")
        self.assertEqual(args.target, "web")
        with patch.dict(os.environ, {"EMSDK": ""}), self.assertRaises(SystemExit):
            build_py.parse_args(["--target", "web"], system="Darwin")

    def test_jobs_must_be_positive(self):
        with self.assertRaises(SystemExit):
            build_py.parse_args(["--jobs", "0"], system="Darwin")

    def test_build_passes_targets_and_parallelism_to_cmake(self):
        with patch.object(build_py.subprocess, "run") as run:
            build_py.build("macos", ["pop_smoke"], 6)
        command = run.call_args[0][0]
        self.assertIn("--build", command)
        self.assertEqual(command[command.index("--preset") + 1], "macos")
        self.assertEqual(command[command.index("--parallel") + 1], "6")
        self.assertEqual(command[command.index("--target") + 1:], ["pop_smoke"])

    def test_game_dir_defaults_to_the_stub_and_is_validated(self):
        args, _ = build_py.parse_args([], system="Darwin")
        self.assertEqual(args.game_dir, build_py.ROOT / "games/stub")
        self.assertEqual(args.build_root, build_py.ROOT / "build")
        with self.assertRaises(SystemExit):
            build_py.parse_args(["--game-dir", "/no/such/game"], system="Darwin")
        with self.assertRaises(SystemExit):
            build_py.parse_args(["--game-dir", "games/stub"], system="Darwin")  # relative
        with self.assertRaises(SystemExit):
            build_py.parse_args(["--target", "plugins"], system="Darwin")  # the stub has no mods

    def test_build_root_follows_an_external_game_dir(self):
        self.assertEqual(build_py.build_root_for(build_py.ROOT / "games/stub"), build_py.ROOT / "build")
        self.assertEqual(build_py.build_root_for(Path("/tmp/populous-recomp")), Path("/tmp/populous-recomp/build"))
        self.assertEqual(build_py.build_dir_for(Path("/tmp/pr/build"), "macos"), Path("/tmp/pr/build/cmake/macos"))

    def test_configure_passes_the_build_dir_and_the_cache_paths(self):
        with patch.object(build_py.subprocess, "run") as run:
            build_py.configure("macos", build_py.game_defines(Path("/g"), Path("/g/build")),
                               build_dir=Path("/g/build/cmake/macos"))
        command = run.call_args[0][0]
        self.assertEqual(command[command.index("-B") + 1], str(Path("/g/build/cmake/macos")))
        self.assertIn("-DRECOMP_GAME_DIR=/g", command)
        self.assertIn("-DPOP_BUILD_ROOT=/g/build", command)
        with patch.object(build_py.subprocess, "run") as run:
            build_py.build("macos", ["pop_smoke"], 2, build_dir=Path("/g/build/cmake/macos"))
        command = run.call_args[0][0]
        self.assertEqual(command[command.index("--build") + 1], str(Path("/g/build/cmake/macos")))
        self.assertEqual(command[command.index("--config") + 1], "Release")  # Xcode is multi-config

    def test_version_reaches_cmake_only_when_given(self):
        # Run 37 (D-203a): a Setup build from a source archive has no git history, so the host's "[host] built with"
        # line said "(kit unknown)"; Setup passes its release version.
        self.assertFalse(any("POP_RECOMP_VERSION" in d for d in build_py.game_defines(Path("/g"), Path("/g/b"))))
        self.assertIn("-DPOP_RECOMP_VERSION=3.0.4", build_py.game_defines(Path("/g"), Path("/g/b"), "3.0.4"))
        args, _ = build_py.parse_args(["--version", "3.0.4"], system="Darwin")
        self.assertEqual(args.version, "3.0.4")

    def test_stub_selects_the_stub_preset_and_rejects_debug(self):
        args, _ = build_py.parse_args(["--stub"], system="Linux")
        self.assertEqual(build_py.preset_name(args.preset, args.config, stub=args.stub), "linux-stub")
        with self.assertRaises(SystemExit):
            build_py.parse_args(["--stub", "--config", "Debug"], system="Linux")

    def test_ios_target_uses_the_ios_preset_and_needs_macos(self):
        args, _ = build_py.parse_args(["--target", "ios", "--team", "T"], system="Darwin")
        self.assertEqual(build_py.preset_name(args.preset, args.config, stub=False, target=args.target), "ios")
        with self.assertRaises(SystemExit):
            build_py.parse_args(["--target", "ios", "--team", "T"], system="Linux")
        with self.assertRaises(SystemExit):
            build_py.parse_args(["--target", "ios"], system="Darwin")  # no team

    def test_run_translator_translates_each_auxiliary_module_into_its_own_directory(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp, patch.object(build_py.subprocess, "run") as run:
            build_py.run_translator(tmp, "/g", "/b", None, ["dfx"])
            self.assertEqual(run.call_count, 2)
            main_cmd, aux_cmd = run.call_args_list[0][0][0], run.call_args_list[1][0][0]
            self.assertNotIn("--module", main_cmd)
            self.assertEqual(aux_cmd[aux_cmd.index("--module") + 1], "dfx")
            self.assertEqual(aux_cmd[aux_cmd.index("--out") + 1], str(Path(tmp) / "aux-dfx"))
            self.assertTrue((Path(tmp) / "aux-dfx").is_dir())
            self.assertIn("translate-dfx-report.json", aux_cmd[aux_cmd.index("--report") + 1])

    def test_pick_device_prefers_the_single_paired_ipad(self):
        devices = [
            {"identifier": "A", "hardwareProperties": {"productType": "iPhone16,1"},
             "connectionProperties": {"pairingState": "paired"}},
            {"identifier": "B", "hardwareProperties": {"productType": "iPad16,3"},
             "connectionProperties": {"pairingState": "paired"}},
        ]
        self.assertEqual(build_py.pick_device(devices), "B")
        with self.assertRaises(SystemExit):
            build_py.pick_device(devices + [{"identifier": "C", "hardwareProperties": {"productType": "iPad14,1"},
                                             "connectionProperties": {"pairingState": "paired"}}])
        with self.assertRaises(SystemExit):
            build_py.pick_device([])


if __name__ == "__main__":
    unittest.main()
