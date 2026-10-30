"""M4 F6: a PC-only install (roots.txt without X360 files) must not list X360 traces in F10: the "Road reflection"
row (X360 half screen) and the "Xbox 360 sound" heading whose rows are all hidden. With X360 files, or with no
roots.txt (the owner's layout), the list is unchanged. Compiles runtime/settings.c with a small driver (no game).
On main 20c0254 the PC-only case listed both (fails); run with the project's Python -I.
Retail checks: 6/6 pass; treating world files as grade availability fails the missing-attributes assertion (1 failure).
"""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
DRIVER = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "settings.h"
int diag_on = 0;
const char *game_dir;
const char *app_pref(const char *key) { (void)key; return NULL; }
void app_pref_set(const char *key, const char *value) { (void)key; (void)value; }
void app_pref_batch_begin(void) {}
void app_pref_batch_end(void) {}
void report_note(const char *fmt, ...) { (void)fmt; }
void diag_event(const char *fmt, ...) { (void)fmt; }
static const char *const kXP[] = {"X360", "PC"}, *const kXPV[] = {"x360", "pc"};
static const char *const kPX[] = {"PC", "X360"}, *const kOO[] = {"off", "on"};
static Setting h_out = {.id = "audio.h_output", .category = SET_AUDIO, .order = 5, .label = "Sound", .type = SET_HEADING};
static Setting vol = {.id = "audio.limiter", .category = SET_AUDIO, .order = 10, .label = "Limiter", .type = SET_TOGGLE};
static Setting h_x = {.id = "audio.h_x360", .category = SET_AUDIO, .order = 50, .label = "Xbox 360 sound", .type = SET_HEADING};
static Setting mix = {.id = "audio.x360_mix", .category = SET_AUDIO, .order = 60, .label = "Sound mix", .type = SET_TOGGLE,
    .choices = kPX, .values = kOO, .nchoices = 2, .def = 1};
static Setting music = {.id = "audio.x360_music", .category = SET_AUDIO, .order = 70, .label = "Music", .type = SET_TOGGLE,
    .choices = kPX, .values = kOO, .nchoices = 2, .def = 1};
static Setting h_w = {.id = "world.h_surfaces", .category = SET_WORLD, .order = 48, .label = "Surfaces", .type = SET_HEADING};
static Setting refl = {.id = "graphics.road_reflection", .category = SET_WORLD, .order = 90, .label = "Road reflection",
    .type = SET_CHOICE, .choices = (const char *const[]){"X360 (half screen)", "PC (320x240)"}, .values = kXPV, .nchoices = 2,
    .env = "NFS_ROAD_REFLECTION"};
static Setting light = {.id = "graphics.world_lighting", .category = SET_WORLD, .order = 10, .label = "World lighting",
    .type = SET_CHOICE, .choices = (const char *const[]){"Neutral", "Original"},
    .values = (const char *const[]){"neutral", "original"}, .nchoices = 2};
static Setting grade = {.id = "graphics.look_data", .type = SET_CHOICE, .hidden = 1, .restart = 1,
    .choices = kPX, .values = (const char *const[]){"pc", "x360"}, .nchoices = 2};
static Setting filter = {.id = "graphics.colour_filter", .category = SET_PICTURE, .type = SET_SLIDER,
    .min = 0, .max = 100, .step = 10};
static Setting device = {.id = "display.window", .category = SET_DISPLAY, .type = SET_TOGGLE, .def = 1};
static Setting control = {.id = "controls.rumble", .category = SET_CONTROLS, .type = SET_TOGGLE, .def = 1};
static Setting gameplay = {.id = "gameplay.heat", .category = SET_GAMEPLAY, .type = SET_TOGGLE, .def = 1};
int main(void) {
    Setting *rows[] = {&h_out, &vol, &h_x, &mix, &music, &h_w, &refl, &light, &grade, &filter, &device, &control, &gameplay};
    for (unsigned i = 0; i < sizeof rows / sizeof *rows; ++i) settings_add(rows[i]);
    settings_init();
    Setting *r[32];
    int cats[] = {SET_WORLD, SET_AUDIO};
    for (int c = 0; c < 2; ++c) {
        int n = settings_rows(cats[c], r, 32);
        for (int i = 0; i < n; ++i) printf("%s%s", i ? " " : "", r[i]->id);
        printf("\n");
    }
    const char *e = getenv("NFS_ROAD_REFLECTION");
    printf("NFS_ROAD_REFLECTION=%s\n", e ? e : "");
    settings_look_apply(PRESET_X360);
    printf("look=%d partial=%d filter=%d grade=%d untouched=%d/%d/%d/%d/%d\n", settings_look_state(),
        settings_look_partial(), settings_get("graphics.colour_filter"), settings_get("graphics.look_data"),
        mix.value, music.value, device.value, control.value, gameplay.value);
    settings_look_apply(PRESET_PC);
    printf("pc=%d partial=%d filter=%d grade=%d\n", settings_look_state(), settings_look_partial(), filter.value, grade.value);
    return 0;
}
'''
ALL_WORLD = "graphics.world_lighting world.h_surfaces graphics.road_reflection"
ALL_AUDIO = "audio.h_output audio.limiter audio.h_x360 audio.x360_mix audio.x360_music"


class MenuX360Traces(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = Path(cls.tmp.name)
        (d / "driver.c").write_text(DRIVER)
        cls.exe = d / "driver"
        subprocess.run(["clang", "-std=c11", "-w", "-I", ROOT / "recomp", "-I", ROOT / "runtime", d / "driver.c",
                        ROOT / "runtime/settings.c", "-o", cls.exe], check=True)
        cls.public_exe = d / "driver-public"
        subprocess.run(["clang", "-DNFS_PUBLIC=1", "-std=c11", "-w", "-I", ROOT / "recomp", "-I", ROOT / "runtime", d / "driver.c",
                        ROOT / "runtime/settings.c", "-o", cls.public_exe], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_with(self, manifest, public=False):
        env = {"PATH": "/usr/bin:/bin", "HOME": self.tmp.name + "/nohome"}
        if manifest is not None:
            env["NFS_ROOTS"] = str(manifest)
        out = subprocess.run([self.public_exe if public else self.exe], env=env, capture_output=True, text=True, check=True).stdout.splitlines()
        return out

    def manifest(self, name, x360):
        d = Path(self.tmp.name) / name
        pc = d / "pc"
        pc.mkdir(parents=True)
        text = f"pc_root={pc}\n"
        if x360:
            x = d / "x360"
            for rel in ("GLOBAL/GLOBALB.BUN", "GLOBAL/ATTRIBUTES.BIN", "GLOBAL/INGAMEMEMORYFILE.BIN", "SOUND/PFDATA/MW_MUSIC.MPF"):
                (x / rel).parent.mkdir(parents=True, exist_ok=True)
                (x / rel).write_bytes(b"x")
            text += f"x360_root={x}\n"
        (d / "roots.txt").write_text(text)
        return d / "roots.txt"

    def test_pc_only_hides_road_reflection_and_empty_x360_heading(self):
        world, audio, env, *_ = self.run_with(self.manifest("pc", False))
        # Road reflection was the last listed row under "Surfaces and weather" in PC-only (M4 settings_rows): that
        # heading goes with it, while "Sound" keeps its visible rows.
        self.assertEqual(world, "graphics.world_lighting")
        self.assertEqual(audio, "audio.h_output audio.limiter")
        self.assertEqual(env, "NFS_ROAD_REFLECTION=pc", "hidden: the PC 320x240 reflection is in effect")

    def test_x360_install_and_owner_layout_unchanged(self):
        for label, manifest in (("x360", self.manifest("x360", True)), ("owner", None)):
            with self.subTest(label):
                world, audio, env, *_ = self.run_with(manifest)
                self.assertEqual(world, ALL_WORLD)
                self.assertEqual(audio, ALL_AUDIO)
                self.assertEqual(env, "NFS_ROAD_REFLECTION=")

    def test_retail_is_explicitly_partial_without_x360(self):
        out = self.run_with(self.manifest("partial", False))
        self.assertEqual(out[3], "look=1 partial=1 filter=100 grade=0 untouched=0/0/1/1/1")
        self.assertEqual(out[4], "pc=2 partial=1 filter=100 grade=0")
        # The message remains visible when selecting the row after a restart or
        # after the transient change notification expires.
        port = (ROOT / "runtime/settings_port.c").read_text()
        menu = (ROOT / "runtime/portmenu.m").read_text()
        for source in (port, menu):
            self.assertIn("partial: your Xbox 360 files are not installed", source)
            self.assertIn("closest to retail", source)
        self.assertIn("settings_look_state() == PRESET_X360 && settings_look_partial()", menu)

    def test_legacy_layout_has_full_grade_and_preserves_preferences(self):
        out = self.run_with(None)
        self.assertEqual(out[3], "look=1 partial=0 filter=100 grade=1 untouched=1/1/1/1/1")
        self.assertEqual(out[4], "pc=2 partial=0 filter=100 grade=0")

    def test_grade_data_must_be_installed_even_with_world_files(self):
        manifest = self.manifest("grade_missing", True)
        self.assertIn("look=1 partial=0 filter=100 grade=1", self.run_with(manifest)[3])
        (manifest.parent / "x360/GLOBAL/ATTRIBUTES.BIN").unlink()
        self.assertIn("look=1 partial=1 filter=100 grade=0", self.run_with(manifest)[3])

    def test_public_build_without_x360_applies_partial_retail_look(self):
        out = self.run_with(self.manifest("public_pc", False), public=True)
        self.assertEqual(out[3], "look=1 partial=1 filter=100 grade=0 untouched=0/0/1/1/1")
        self.assertEqual(out[4], "pc=2 partial=1 filter=100 grade=0")


if __name__ == "__main__":
    unittest.main(verbosity=2)
