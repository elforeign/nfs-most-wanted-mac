"""tools/game_config.py loads a game directory; tools/gen_game_config.py renders it."""

import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load_module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


game_config = load_module("game_config")
gen_game_config = load_module("gen_game_config")


class LoadTests(unittest.TestCase):
    def test_cd_tracks_preserve_disc_order_and_reject_non_strings(self):
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp)
            stub = (ROOT / "games/stub/game.toml").read_text()
            (game / "globals.toml").write_text("")
            (game / "game.toml").write_text(stub + '\n[media]\ncd_tracks = ["", "music/02.ogg"]\n')
            cfg = game_config.load(game)
            self.assertEqual(cfg["media"]["cd_tracks"], ["", "music/02.ogg"])
            self.assertIn('#define RECOMP_CD_AUDIO_TRACKS {"", "music/02.ogg", 0}',
                          gen_game_config.render_header(cfg))
            for bad in ('[2]', '"music"'):
                (game / "game.toml").write_text(stub + '\n[media]\ncd_tracks = ' + bad + '\n')
                with self.assertRaisesRegex(ValueError, "cd_tracks"):
                    game_config.load(game)

    def test_windows_version_defaults_and_optional_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp)
            stub = (ROOT / "games/stub/game.toml").read_text()
            base = "\n".join(line for line in stub.splitlines()
                             if not line.startswith("windows_version ="))
            (game / "globals.toml").write_text("")
            for version, expected in ((None, (4, 10, 2222, 1)),
                                      ("6.1", (6, 1, 7601, 2)),
                                      ("6.1.7600", (6, 1, 7600, 2)),
                                      ("5.0", (5, 0, 0, 2))):
                setting = "" if version is None else 'windows_version = "%s"\n' % version
                (game / "game.toml").write_text(base.replace("[game]\n", "[game]\n" + setting))
                cfg = game_config.load(game)
                self.assertEqual(cfg["game"]["windows_version"], version or "4.10")
                header = gen_game_config.render_header(cfg)
                for field, value in zip(("MAJOR", "MINOR", "BUILD", "PLATFORM"), expected):
                    self.assertIn("#define RECOMP_WINDOWS_%s %du" % (field, value), header)

    def test_frame_cap_is_optional_and_rendered(self):
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp)
            stub = (ROOT / "games/stub/game.toml").read_text()
            (game / "globals.toml").write_text("")
            (game / "game.toml").write_text(stub)
            self.assertNotIn("RECOMP_GAME_FRAME_CAP_ADDR", gen_game_config.render_header(game_config.load(game)))
            (game / "game.toml").write_text(stub.replace("[game]\n", "[game]\nframe_cap = 0x006f0890\n"))
            self.assertIn("#define RECOMP_GAME_FRAME_CAP_ADDR 0x006f0890u",
                          gen_game_config.render_header(game_config.load(game)))
            for bad in ('"x"', "true", "-1"):
                (game / "game.toml").write_text(stub.replace("[game]\n", "[game]\nframe_cap = %s\n" % bad))
                with self.assertRaisesRegex(ValueError, "frame_cap"):
                    game_config.load(game)

    def test_windows_version_rejects_invalid_components(self):
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp)
            stub = (ROOT / "games/stub/game.toml").read_text()
            base = "\n".join(line for line in stub.splitlines()
                             if not line.startswith("windows_version ="))
            (game / "globals.toml").write_text("")
            for bad in ('"6"', '"6.1.2.3"', '"6.-1"', '"6.256"', '"6.1.32768"', '6.1', 'true'):
                (game / "game.toml").write_text(base.replace(
                    "[game]\n", "[game]\nwindows_version = %s\n" % bad))
                with self.assertRaisesRegex(ValueError, "windows_version"):
                    game_config.load(game)

    def test_function_alignment_defaults_to_16_and_can_be_overridden(self):
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp)
            stub = (ROOT / "games/stub/game.toml").read_text()
            base = "\n".join(line for line in stub.splitlines()
                             if not line.startswith("function_alignment ="))
            (game / "globals.toml").write_text("")
            for value, expected in ((None, 16), (4, 4)):
                setting = "" if value is None else "function_alignment = %d\n" % value
                (game / "game.toml").write_text(base.replace("[translate]\n", "[translate]\n" + setting))
                self.assertEqual(game_config.load(game)["translate"]["function_alignment"], expected)

    def test_function_alignment_must_be_a_positive_integer(self):
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp)
            stub = (ROOT / "games/stub/game.toml").read_text()
            base = "\n".join(line for line in stub.splitlines()
                             if not line.startswith("function_alignment ="))
            (game / "globals.toml").write_text("")
            for bad in ("0", "-4", '"four"', "4.0", "true"):
                (game / "game.toml").write_text(base.replace(
                    "[translate]\n", "[translate]\nfunction_alignment = %s\n" % bad))
                with self.assertRaisesRegex(ValueError, "function_alignment"):
                    game_config.load(game)

    def test_extra_heap_is_explicit_bounded_and_disjoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp)
            stub = (ROOT / "games/stub/game.toml").read_text()
            (game / "globals.toml").write_text("")
            def load(settings):
                head, sep, tail = settings.partition("[modules.")
                (game / "game.toml").write_text(stub.replace("[game]", "[game]\n" + head) + "\n" + sep + tail)
                return game_config.load(game)
            cfg = load("guest_size=0x40000000\nheap_extra_base=0x10000000\nheap_extra_size=0x30000000")
            self.assertIn("#define RECOMP_HEAP_EXTRA_SIZE 0x30000000u", gen_game_config.render_header(cfg))
            self.assertEqual(load("")["game"]["heap_extra_size"], 0)
            for bad in (
                "heap_extra_base=0x10000000", "heap_extra_size=4096",
                "heap_extra_base=0x0e000000\nheap_extra_size=4096",
                "guest_size=0x20000000\nheap_extra_base=0x10000001\nheap_extra_size=4096",
                "heap_extra_base=0x10000000\nheap_extra_size=4096",
                "guest_size=0x40000000\nheap_extra_base=0x10000000\nheap_extra_size=0x40000000",
                "heap_extra_base=-4096\nheap_extra_size=-4096",
                "heap_extra_base=true\nheap_extra_size=true",
            ):
                with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "heap_extra"):
                    load(bad)
            with self.assertRaisesRegex(ValueError, "overlaps auxiliary"):
                load('guest_size=0x40000000\nheap_extra_base=0x10000000\nheap_extra_size=0x30000000\n'
                     '[modules.aux.test]\nname="test.dll"\npath="test.dll"\nsha256="' + '0'*64 +
                     '"\nbase=0x10000000\nsize=4096')

    def test_heap_base_defaults_to_the_kit_layout(self):
        cfg = game_config.load(ROOT / "games/stub")
        self.assertEqual(cfg["game"]["heap_base"], 0x01000000)
        self.assertIn("#define RECOMP_HEAP_BASE 0x01000000u", gen_game_config.render_header(cfg))
        self.assertIn("set(RECOMP_HEAP_BASE 0x01000000u)", gen_game_config.render_cmake(cfg))

    def test_heap_base_is_validated(self):
        cfg = game_config.load(ROOT / "games/stub")
        for bad in (0x01000010, 0x0e000000, 0x00400000):
            with self.assertRaises(ValueError):
                game_config.validate_heap_base(bad)
        self.assertEqual(game_config.validate_heap_base(0x01400000), 0x01400000)

    def test_the_stub_game_loads_with_every_required_key(self):
        cfg = game_config.load(ROOT / "games/stub")
        self.assertEqual(cfg["game"]["id"], "stub")
        self.assertEqual(cfg["game"]["executable"], "STUB.EXE")
        self.assertEqual(cfg["game"]["entry_point"], 0x00401000)
        self.assertEqual(cfg["game"]["image_base"], 0x00400000)
        self.assertIn("simulation_turn", cfg["globals"])
        self.assertEqual(cfg["globals"]["entity_base"]["stride"], 179)

    def test_developer_paths_resolve_relative_to_the_game_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp) / "g"
            game.mkdir()
            (game / "game.toml").write_text((ROOT / "games/stub/game.toml").read_text())
            (game / "globals.toml").write_text((ROOT / "games/stub/globals.toml").read_text())
            cfg = game_config.load(game)
            self.assertEqual(cfg["developer_exe_path"], (game / "original/STUB.EXE").resolve())
            self.assertEqual(cfg["listings_path"], (game / "analysis/STUB.EXE").resolve())
            cmake = gen_game_config.render_cmake(cfg)
            self.assertIn('set(RECOMP_DEVELOPER_EXE "%s")' % (game / "original/STUB.EXE").resolve().as_posix(),
                          cmake)
            self.assertIn('set(RECOMP_DEVELOPER_GAME_DIR "%s")' % (game / "original").resolve().as_posix(),
                          cmake)
            header = gen_game_config.render_header(cfg)
            self.assertIn('#define RECOMP_DEVELOPER_EXE "%s"' % (game / "original/STUB.EXE").resolve().as_posix(),
                          header)
            self.assertIn('#define RECOMP_GAME_DIR "%s"' % game.resolve().as_posix(), header)
            self.assertIn('#define RECOMP_KIT_DIR "%s"' % ROOT.resolve().as_posix(), header)

    def test_override_header_is_optional_and_must_exist(self):
        """[translate] overrides names the header the generated sources include
        before they define FN_<addr>, which is how a game replaces one
        translated function with a native one. Absent by default; named and
        missing is an error, because a path that quietly failed to resolve
        would leave a build looking replaced while running the original."""
        base = game_config.load(ROOT / "games/stub")
        self.assertIsNone(base["overrides_header"])
        self.assertIn('set(RECOMP_OVERRIDE_HEADER "")', gen_game_config.render_cmake(base))
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp) / "g"
            game.mkdir()
            text = (ROOT / "games/stub/game.toml").read_text()
            text = text.replace("[translate]", '[translate]\noverrides = "native/overrides.h"', 1)
            (game / "game.toml").write_text(text)
            (game / "globals.toml").write_text((ROOT / "games/stub/globals.toml").read_text())
            with self.assertRaises(ValueError):
                game_config.load(game)  # named but absent
            (game / "native").mkdir()
            (game / "native/overrides.h").write_text("/* none yet */\n")
            cfg = game_config.load(game)
            self.assertEqual(cfg["overrides_header"], (game / "native/overrides.h").resolve())
            self.assertIn('set(RECOMP_OVERRIDE_HEADER "%s")'
                          % (game / "native/overrides.h").resolve().as_posix(),
                          gen_game_config.render_cmake(cfg))

    def test_auxiliary_modules_and_guest_size(self):
        """[modules.aux.<key>] names a DLL the guest loads at run time that the kit
        translates as a second image at its preferred base; [game] guest_size
        grows the arena to hold it. Defaults: no modules, 0x10000000."""
        base = game_config.load(ROOT / "games/stub")
        self.assertEqual(base["aux_modules"], [])
        self.assertEqual(base["game"]["guest_size"], 0x10000000)
        self.assertIn("#define RECOMP_GUEST_SIZE 0x10000000u", gen_game_config.render_header(base))
        self.assertIn("#define RECOMP_AUX_MODULE_COUNT 0", gen_game_config.render_header(base))
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp) / "g"
            game.mkdir()
            text = (ROOT / "games/stub/game.toml").read_text()
            text = text.replace('guest_root = ', 'guest_size = 0x10100000\nguest_root = ', 1)
            text += ('\n[modules.aux.blit]\nname = "Blit_p6.dll"\npath = "original/Blit_p6.dll"\n'
                     'sha256 = "%s"\nbase = 0x10000000\nsize = 0x28000\nlistings = "analysis/Blit_p6.dll"\nfunction_alignment = 1\n' % ("ab" * 32))
            (game / "game.toml").write_text(text)
            (game / "globals.toml").write_text((ROOT / "games/stub/globals.toml").read_text())
            cfg = game_config.load(game)
            self.assertEqual(cfg["game"]["guest_size"], 0x10100000)
            mod = cfg["aux_modules"][0]
            self.assertEqual(mod["key"], "blit")
            self.assertEqual(mod["name"], "Blit_p6.dll")
            self.assertEqual(mod["path"], (game / "original/Blit_p6.dll").resolve())
            self.assertEqual(mod["listings_path"], (game / "analysis/Blit_p6.dll").resolve())
            self.assertEqual(mod["function_alignment"], 1)
            self.assertEqual(mod["entry_points"], [])
            self.assertEqual((mod["base"], mod["size"]), (0x10000000, 0x28000))
            header = gen_game_config.render_header(cfg)
            self.assertIn("#define RECOMP_GUEST_SIZE 0x10100000u", header)
            self.assertIn("#define RECOMP_AUX_MODULE_COUNT 1", header)
            self.assertIn('{"Blit_p6.dll", "%s", "%s", 0x10000000u, 0x00028000u}' % ((game / "original/Blit_p6.dll").resolve().as_posix(), "ab" * 32),
                          header)
            cmake = gen_game_config.render_cmake(cfg)
            self.assertIn("set(RECOMP_GUEST_SIZE 0x10100000u)", cmake)
            self.assertIn("set(RECOMP_AUX_MODULES blit)", cmake)
            (game / "game.toml").write_text(text + 'entry_points = [0x10001234]\n')
            self.assertEqual(game_config.load(game)["aux_modules"][0]["entry_points"],
                             [0x10001234])
            for entries in ('[0x0fffffff]', '[0x10028000]', '["0x10001234"]', '7'):
                (game / "game.toml").write_text(text + 'entry_points = ' + entries + '\n')
                with self.assertRaises(ValueError):
                    game_config.load(game)
            bad = text.replace('guest_size = 0x10100000', 'guest_size = 0x10000000')
            (game / "game.toml").write_text(bad)
            with self.assertRaises(ValueError):
                game_config.load(game)   # a module needs an arena that reaches it: checked at load

    def test_controls_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp)
            (game / "globals.toml").write_text((ROOT / "games/stub/globals.toml").read_text())
            stub = (ROOT / "games/stub/game.toml").read_text()
            base = stub[:stub.index("[controls]")] + stub[stub.index("[bundle]"):]  # without the stub's own [controls]
            (game / "game.toml").write_text(base)
            cfg = game_config.load(game)
            self.assertEqual(cfg["controls"]["default_layout"], "keys")
            self.assertEqual(cfg["controls"]["pad"], "mapped")
            self.assertEqual(cfg["controls"]["mapped"], game_config.MAPPED_DEFAULTS)

            # The old [touch] keypad knob maps onto default_layout when the
            # game names no default_layout of its own.
            (game / "game.toml").write_text(base + '\n[touch]\nkeypad = "hidden"\n')
            self.assertEqual(game_config.load(game)["controls"]["default_layout"], "hidden")

            # Validation.
            (game / "game.toml").write_text(base + '\n[controls]\npad = "sometimes"\n')
            with self.assertRaises(ValueError):
                game_config.load(game)
            (game / "game.toml").write_text(
                base + '\n[controls]\n[controls.mapped]\ncross = "key:Nope"\n')  # bad button target
            with self.assertRaises(ValueError):
                game_config.load(game)
            (game / "game.toml").write_text(
                base + '\n[controls]\n[controls.mapped]\nnot_a_button = "key:Space"\n')  # unknown key
            with self.assertRaises(ValueError):
                game_config.load(game)
            (game / "game.toml").write_text(
                base + '\n[controls]\n[controls.native]\naxes = ["x", "y", "z"]\n')  # wrong length
            with self.assertRaises(ValueError):
                game_config.load(game)
            (game / "game.toml").write_text(
                base + '\n[controls]\n[controls.native]\nsensitivity = 5\n')  # unknown key
            with self.assertRaises(ValueError):
                game_config.load(game)

            # The header.
            (game / "game.toml").write_text(
                base + '\n[controls.mapped]\nleft_stick = "horizontal_arrows"\n')
            self.assertIn("left_stick=horizontal_arrows",
                          gen_game_config.render_header(game_config.load(game)))
            (game / "game.toml").write_text(base)
            header = gen_game_config.render_header(game_config.load(game))
            self.assertIn("#define RECOMP_CONTROLS_PAD 1", header)
            self.assertIn("cross=mouse_left", header)

            # The old rows spelling still loads.
            (game / "game.toml").write_text(base + '\n[settings]\nrows = ["window", "keypad"]\n')
            game_config.load(game)

    def test_settings_rows_knob(self):
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp)
            (game / "globals.toml").write_text((ROOT / "games/stub/globals.toml").read_text())
            base = (ROOT / "games/stub/game.toml").read_text()
            (game / "game.toml").write_text(base)
            self.assertIn("#define RECOMP_SETTINGS_ROWS 0x3ffu", gen_game_config.render_header(game_config.load(game)))
            (game / "game.toml").write_text(base + '\n[settings]\nrows = ["window", "performance_overlay", "keypad"]\n')
            self.assertIn("#define RECOMP_SETTINGS_ROWS 0x248u", gen_game_config.render_header(game_config.load(game)))
            (game / "game.toml").write_text(base + '\n[settings]\nrows = []\n')
            self.assertIn("#define RECOMP_SETTINGS_ROWS 0x000u", gen_game_config.render_header(game_config.load(game)))
            (game / "game.toml").write_text(base + '\n[settings]\nrows = ["window", "sharpness"]\n')
            with self.assertRaises(ValueError) as caught:
                game_config.load(game)
            self.assertIn("sharpness", str(caught.exception))

    def test_mods_builtin_knob(self):
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp)
            (game / "globals.toml").write_text((ROOT / "games/stub/globals.toml").read_text())
            base = (ROOT / "games/stub/game.toml").read_text()
            (game / "game.toml").write_text(base)
            self.assertIn("#define RECOMP_MODS_BUILTIN_POPULOUS 1", gen_game_config.render_header(game_config.load(game)))
            (game / "game.toml").write_text(base + '\n[mods]\nbuiltin = "none"\n')
            self.assertIn("#define RECOMP_MODS_BUILTIN_POPULOUS 0", gen_game_config.render_header(game_config.load(game)))
            (game / "game.toml").write_text(base + '\n[mods]\nbuiltin = "majesty"\n')
            with self.assertRaises(ValueError):
                gen_game_config.render_header(game_config.load(game))

    def test_launcher_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp)
            (game / "globals.toml").write_text((ROOT / "games/stub/globals.toml").read_text())
            base = (ROOT / "games/stub/game.toml").read_text()
            (game / "game.toml").write_text(base)
            header = gen_game_config.render_header(game_config.load(game))
            self.assertIn('#define RECOMP_LAUNCHER_TITLE "Stub Game"', header)
            self.assertIn('#define RECOMP_LAUNCHER_STORE_URL ""', header)
            self.assertIn("#define RECOMP_LAUNCHER_INSTALL_NAMES {0}", header)
            self.assertIn("#define RECOMP_LAUNCHER_MIN_FREE_MB 0u", header)
            self.assertIn("#define RECOMP_REQUIRED_DIRS {0}", header)
            (game / "game.toml").write_text(
                base.replace("[bundle]\nexclude = []", '[bundle]\nexclude = ["*.dll", "app"]')
                + '\n[launcher]\ntitle = "Stub \\"Deluxe\\""\nstore = "https://example.test/stub"\n'
                  'install_names = ["Stub Game", "STUB"]\ngog_ids = ["123"]\nmin_free_mb = 50\n'
                  '\n[setup]\nrequired_dirs = ["data", "levels"]\n')
            header = gen_game_config.render_header(game_config.load(game))
            self.assertIn('#define RECOMP_LAUNCHER_TITLE "Stub \\"Deluxe\\""', header)
            self.assertIn('#define RECOMP_LAUNCHER_INSTALL_NAMES {"Stub Game", "STUB", 0}', header)
            self.assertIn('#define RECOMP_LAUNCHER_GOG_IDS {"123", 0}', header)
            self.assertIn("#define RECOMP_LAUNCHER_STEAM_IDS {0}", header)
            self.assertIn("#define RECOMP_LAUNCHER_MIN_FREE_MB 50u", header)
            self.assertIn('#define RECOMP_REQUIRED_DIRS {"data", "levels", 0}', header)
            self.assertIn('#define RECOMP_BUNDLE_EXCLUDE {"*.dll", "app", 0}', header)
            for bad in ('min_free_mb = -1', 'gog_ids = "123"', 'title = 5'):
                (game / "game.toml").write_text(base + "\n[launcher]\n" + bad + "\n")
                with self.assertRaises(ValueError):
                    game_config.load(game)

    def test_missing_key_is_an_error_naming_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp)
            (game / "game.toml").write_text('[game]\nid = "x"\n')
            (game / "globals.toml").write_text("")
            with self.assertRaises(ValueError) as caught:
                game_config.load(game)
            self.assertIn("game.toml", str(caught.exception))
            self.assertIn("sha256", str(caught.exception))


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.cfg = game_config.load(ROOT / "games/stub")
        self.header = gen_game_config.render_header(self.cfg)
        self.cmake = gen_game_config.render_cmake(self.cfg)

    def test_identity_macros(self):
        self.assertIn('#define RECOMP_GAME_ID "stub"', self.header)
        self.assertIn('#define RECOMP_APP_NAME "StubRecomp"', self.header)
        self.assertIn('#define RECOMP_EXECUTABLE "STUB.EXE"', self.header)
        self.assertIn('#define RECOMP_EXE_SHA256 "%s"' % ("0" * 64), self.header)
        self.assertIn('#define RECOMP_GUEST_ROOT "C:\\\\Stub"', self.header)
        self.assertIn("#define RECOMP_ENTRY_POINT 0x00401000u", self.header)
        self.assertIn("#define RECOMP_IMAGE_BASE 0x00400000u", self.header)

    def test_globals_macros(self):
        self.assertIn("#define RECOMP_GLOBAL_SIMULATION_TURN_ADDR 0x00700000u", self.header)
        self.assertIn("#define RECOMP_GLOBAL_ENTITY_BASE_STRIDE 179u", self.header)
        self.assertIn("#define RECOMP_GLOBAL_ENTITY_BASE_COUNT 2000u", self.header)

    def test_hook_lists_render_as_brace_lists_with_a_count(self):
        cfg = dict(self.cfg, hooks={"pair": [0x10, 0x20], "one": 0x30})
        header = gen_game_config.render_header(cfg)
        self.assertIn("#define RECOMP_HOOK_PAIR_COUNT 2", header)
        self.assertIn("#define RECOMP_HOOK_PAIR {0x00000010u, 0x00000020u}", header)
        self.assertIn("#define RECOMP_HOOK_ONE 0x00000030u", header)

    def test_stub_hooks_render(self):
        self.assertIn("#define RECOMP_HOOK_FRAME_CLOCK_BEGIN 0x00401100u", self.header)
        self.assertIn("#define RECOMP_HOOK_CURSOR_SURFACE_PTRS_COUNT 2", self.header)
        self.assertIn("#define RECOMP_HOOK_CURSOR_SURFACE_PTRS {0x00600100u, 0x00600104u}", self.header)
        self.assertIn("#define RECOMP_HOOK_MOUSE_VTABLE 0x00600200u", self.header)

    def test_cmake_fragment(self):
        self.assertIn('set(RECOMP_APP_NAME "StubRecomp")', self.cmake)
        self.assertIn('set(RECOMP_GAME_NAME "Stub Game")', self.cmake)
        self.assertIn("set(RECOMP_IMAGE_BASE 0x00400000u)", self.cmake)
        stub = (ROOT / "games/stub").resolve()
        self.assertIn('set(RECOMP_DEVELOPER_GAME_DIR "%s")' % (stub / "original").as_posix(), self.cmake)
        self.assertIn('set(RECOMP_DEVELOPER_EXE "%s")' % (stub / "original/STUB.EXE").as_posix(), self.cmake)


if __name__ == "__main__":
    unittest.main()
