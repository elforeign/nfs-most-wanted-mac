#!/usr/bin/env python3
"""Synthetic all-in-one export proofs; no game, network or real profile.

Relocation control: original preference-first selection resolves 1 external PC
root; fixed selection resolves 4/4 embedded roots after moving the signed app.
EXDEV control: corrupting one fallback copy fails its SHA-256 check (1/1).
Updater control: bypassing the export guard accepts the marker (1/1 detected).
Native updater: 1/1 shows re-export without profile reads; removing guards
fails 1/1 at the message/registration oracle before any network operation.
Signing boundary: 1/1 verifies after signing; skipped signing fails 1/1.
Review regressions, measured against the unfixed working tree:
Old signed runtime: before 1/1 wrongly accepted; after 0/1 accepted.
Missing manifest: before 1/1 loses export identity; after 0/1 loses it.
Stale media paths: before 1/1 checked pair bypasses embedded data; after
3/3 pairs use embedded data, with 4/4 texture on/off switches preserved.
Post-rename ENOSPC/SIGINT/SIGTERM: before 3/3 report failure for a published
app; after 0/3 report failure, with 3/3 signed destinations and done events.
Post-publication log ENOSPC: before 3/3 raise on the closed log; after 0/3
raise, with 3/3 signed destinations and done events despite failed log writes.
Executable installs: copying over a binary that already ran gets it killed
(returncode -9, not the expected 94) in 14/20 standalone repros; copying to a
sibling name and os.replace() runs correctly 20/20 (helper install_exe).
"""
import errno
import contextlib
import io
import json
import os
from pathlib import Path
import plistlib
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

def install_exe(source, destination):
    """Install an executable as a new inode: an in-place overwrite of a binary
    that already ran keeps a stale kernel signature and the next exec is killed."""
    destination = Path(destination)
    temporary = destination.with_name(destination.name + ".new")
    shutil.copy2(source, temporary)
    os.replace(temporary, destination)


ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "tools"))
from setup_kit import export as ex, install as ins, apply, stage, backend

DRIVER = r'''
#include "roots.h"
const char *game_dir;
int main(int argc, char **argv) {
    char pref[4096] = "", path[4096], line[4096];
    snprintf(path, sizeof path, "%s/app.txt", getenv("BOOT_USERDATA"));
    FILE *f = fopen(path, "r");
    while (f && fgets(line, sizeof line, f)) {
        if (!strncmp(line, "game_dir=", 9)) {
            line[strcspn(line, "\r\n")] = 0;
            snprintf(pref, sizeof pref, "%s", line + 9);
            break;
        }
    }
    if (f) fclose(f);
    const char *keys[] = {"pc_root", "x360_root", "assets_root", "movies"};
    for (int i = 0; i < 4; ++i) {
        const char *p = roots_get(keys[i]);
        printf("%s=%s\n", keys[i], p ? p : "(none)");
    }
    const char *selected = roots_pc_choice(argc > 1 ? argv[1] : NULL, pref[0] ? pref : NULL);
    printf("selected=%s\n", selected ? selected : "(none)");
    printf("x360=%s\n", roots_x360_files());
    printf("manifest=%s\n", roots_file_() ? roots_file_() : "(none)");
    printf("exported=%d\n", roots_exported());
    return 0;
}
'''


def run(command, **kwargs):
    result = subprocess.run(list(map(str, command)), capture_output=True, text=True, **kwargs)
    if result.returncode:
        raise AssertionError(result.stderr[-2000:] + result.stdout[-2000:])
    return result.stdout


class ExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory()
        cls.build_path = Path(cls.build.name).resolve()
        (cls.build_path / "driver.c").write_text(DRIVER)
        cls.binary = cls.build_path / "driver"
        run(["clang", "-DNFS_PUBLIC=1", "-I", ROOT / "runtime", cls.build_path / "driver.c", "-o", cls.binary])
        movies = (ROOT / "runtime/movies.c").read_text()
        textures = (ROOT / "runtime/x360_textures.m").read_text()
        media = cls.build_path / "media.c"
        media.write_text(r'''
#include "roots.h"
const char *game_dir;
#define mlog(...) ((void)0)
#define diag_event(...) ((void)0)
static int nhd, nhd_later, nhd_tone, diag_on;
static void *hd, *hd_later, *hd_tone;
static int hd_list(const char *dir, void **list, int n) {
    (void)list;
    printf("scan=%s\n", dir);
    return n + 1;
}
static const char *later_root(char *out, size_t size) { (void)out; (void)size; return NULL; }
''' + movies[movies.index("static const char *x360_dir(void)"):movies.index("// PC name (", movies.index("static const char *x360_dir(void)"))] +
            textures[textures.index("static void hd_scan(void)"):textures.index("// ---- validation:")] + r'''
int main(void) {
    printf("movies=%s\n", x360_dir() ? x360_dir() : "(none)");
    hd_scan();
    printf("textures=%d\n", nhd);
    return 0;
}
''')
        cls.media = cls.build_path / "media"
        run(["clang", "-DNFS_PUBLIC=1", "-I", ROOT / "runtime", media, "-o", cls.media])
        # Compile the actual startup selector with only UI/profile/process
        # boundaries stubbed. Never initialize Cocoa's application or a game.
        app = (ROOT / "runtime/app.m").read_text()
        startup = app[app.index("const char *app_start("):]
        startup = startup.replace("(NSEvent.modifierFlags & NSEventModifierFlagOption)", "0")
        prefix = r'''
#import <Cocoa/Cocoa.h>
#include "roots.h"
const char *game_dir;
static int app_bundled;
static char fixture_preference[4096];
static void restart_restore_cwd(void) {}
static void restart_wait(int argc, char **argv) { (void)argc; (void)argv; }
static void restart_capture(char **argv) { (void)argv; }
static void log_setup(void) {}
static void profile_lock(void) {}
static const char *userdata_dir(void) { return getenv("BOOT_USERDATA"); }
void pub_update_launch(int *argc, char ***argv, const char *profile) { (void)argc; (void)argv; (void)profile; }
static const char *app_pref(const char *key) { (void)key; return fixture_preference; }
static void app_pref_set(const char *key, const char *value) { (void)key; (void)value; exit(93); }
static int check_game_dir(const char *dir, char *why, size_t n) {
    (void)why; (void)n; struct stat st; return stat(dir, &st) || !S_ISDIR(st.st_mode);
}
static void app_cocoa_init(void) { exit(94); }
static NSString *ask_game_dir(const char *why) { (void)why; exit(95); }
'''
        suffix = r'''
int main(int argc, char **argv) { @autoreleasepool {
    char path[4096], line[4096];
    snprintf(path, sizeof path, "%s/app.txt", getenv("BOOT_USERDATA"));
    FILE *f = fopen(path, "r");
    while (f && fgets(line, sizeof line, f)) if (!strncmp(line, "game_dir=", 9)) {
        line[strcspn(line, "\r\n")] = 0;
        snprintf(fixture_preference, sizeof fixture_preference, "%s", line + 9); break;
    }
    if (f) fclose(f);
    printf("selected=%s\n", app_start(argc, argv));
    return 0;
} }
'''
        begin = startup.index("        if (roots_exported()) {")
        end = startup.index("        const char *arg = NULL;", begin)
        original = startup[:begin] + startup[end:]
        original = original.replace('const char *saved = roots_pc_choice(arg, app_pref("game_dir"));',
                                    'const char *saved = arg ? arg : app_pref("game_dir");\n'
                                    '        if (!saved) saved = roots_get("pc_root");')
        cls.startup, cls.original_startup = cls.build_path / "startup", cls.build_path / "original-startup"
        for name, selector in ((cls.startup, startup), (cls.original_startup, original)):
            source = name.with_suffix(".m")
            source.write_text(prefix + selector + suffix)
            run(["clang", "-fobjc-arc", "-I", ROOT / "runtime", source, "-framework", "Cocoa", "-o", name])
        ui = cls.build_path / "updater.m"
        ui.write_text(r'''
#include "pub_update.m"
#include "pub_update_launch.m"
static int registrations;
const char *game_dir;
void settings_add(Setting *setting) { (void)setting; ++registrations; }
const char *userdata_dir(void) { fprintf(stderr, "unexpected profile access\n"); exit(97); }
int main(int argc, char **argv) { @autoreleasepool {
    char text[1024];
    if (!roots_exported()) return 96;
    update_text(text, sizeof text);
    printf("message=%s\nlabel=%s\nregistrations=%d\n", text, s_update.label, registrations);
    if (registrations != 1 || !strstr(text, BUNDLE_UPDATE_MESSAGE)) {
        fprintf(stderr, "export updater guard missing\n"); return 97;
    }
    update_press(0); cancel_press(0); retry_press(0); log_press(0);
    if (mac_start("9.0.0") != 0 || pu_loaded || pu_job) return 95;
    // NULL proves the export guard runs before any profile conversion or reads.
    pub_update_launch(&argc, &argv, NULL);
    return 0;
} }
''')
        cls.updater = cls.build_path / "updater"
        run(["clang", "-fobjc-arc", "-Wno-deprecated-declarations", "-I", ROOT / "runtime", ui,
             "-framework", "Cocoa", "-o", cls.updater])
        broken = cls.build_path / "broken"
        broken.mkdir()
        for name in ("pub_update.m", "pub_update_launch.m"):
            source = (ROOT / "runtime" / name).read_text()
            assert "if (roots_exported())" in source
            (broken / name).write_text(source.replace("if (roots_exported())", "if (0)"))
        cls.broken_updater = cls.build_path / "broken-updater"
        run(["clang", "-fobjc-arc", "-Wno-deprecated-declarations", "-I", broken, "-I", ROOT / "runtime", ui,
             "-framework", "Cocoa", "-o", cls.broken_updater])

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name).resolve()
        self.profile = self.d / "profile"
        self.profile.mkdir()
        self.app = self.d / ins.APP_BUNDLE
        exe = self.app / "Contents/MacOS" / ins.EXEC_NAME
        exe.parent.mkdir(parents=True)
        (self.app / "Contents/Resources").mkdir()
        install_exe(self.binary, exe)
        with (self.app / "Contents/Info.plist").open("wb") as f:
            plistlib.dump(dict(CFBundleIdentifier=ins.PUBLIC_BUNDLE_ID, CFBundleExecutable=ins.EXEC_NAME,
                              CFBundleShortVersionString="4.3.0", CFBundlePackageType="APPL"), f)
        ex.sign_bundle(self.app)
        self.roots = {}
        for key in ex.ASSET_KEYS:
            root = self.d / "install" / key
            root.mkdir(parents=True)
            (root / "synthetic.bin").write_bytes(bytes(range(256)) * 4096)
            self.roots[key] = root
        self.write_roots()
        (self.profile / "app.txt").write_text(f"game_dir={self.roots['pc_root']}\nvolume=0.5\n")
        self.destination = self.d / "Backup.app"

    def write_roots(self):
        (self.profile / "roots.txt").write_text("".join(f"{k}={p}\n" for k, p in self.roots.items()))

    def export(self, **kwargs):
        return ex.export_app(self.app, self.profile, self.destination, **kwargs)

    def inspect(self, app, argument=False):
        env = dict(os.environ, BOOT_USERDATA=str(self.profile), NFS_ROOTS=str(self.profile / "roots.txt"),
                   NFS_X360_FILES=str(self.d / "stale-x360"))
        command = [app / "Contents/MacOS" / ins.EXEC_NAME]
        if argument:
            command.append(self.d / "stale-argument")
        return dict(line.split("=", 1) for line in run(command, cwd=self.d, env=env).splitlines())

    def test_signed_export_moves_and_overrides_every_stale_root(self):
        # A normal app demonstrates the old behavior with the same harness.
        normal = self.inspect(self.app, argument=True)
        self.assertEqual(normal["selected"], str(self.d / "stale-argument"))
        metrics = self.export()
        self.assertGreater(metrics["cloned_files"] + metrics["copied_files"], 4)
        self.assertGreater(metrics["allocated_bytes"], 0)
        self.assertGreater(metrics["logical_bytes"], 4 * 1024 * 1024)
        moved_parent = self.d / "another folder"
        moved_parent.mkdir()
        moved = moved_parent / "Renamed.app"
        self.destination.rename(moved)
        # All original roots disappear, leaving stale manifests and preferences.
        (self.d / "install").rename(self.d / "unavailable-install")
        (self.profile / "app.txt").write_text(f"game_dir={self.d / 'stale-preference'}\nvolume=0.5\n")
        observed = self.inspect(moved)
        for key in ex.ASSET_KEYS:
            expected = moved / ex.MANIFEST.parent / key
            self.assertEqual(observed[key], str(expected))
            self.assertEqual((expected / "synthetic.bin").read_bytes(), bytes(range(256)) * 4096)
        self.assertEqual(observed["selected"], observed["pc_root"])
        self.assertEqual(observed["x360"], observed["x360_root"])
        self.assertEqual(observed["manifest"], str(moved / ex.MANIFEST))
        self.assertEqual(observed["exported"], "1")
        self.assertEqual(self.inspect(moved, argument=True)["selected"], observed["pc_root"])
        self.assertTrue(ins.codesign_ok(moved))
        print("EXPORT_FIXTURE " + json.dumps(metrics, sort_keys=True))

    def test_original_preference_first_control_fails_relocation_oracle(self):
        install_exe(self.startup, self.app / "Contents/MacOS" / ins.EXEC_NAME)
        ex.sign_bundle(self.app)
        self.export()
        moved = self.d / "Moved.app"
        self.destination.rename(moved)
        stale = self.d / "stale-install"
        stale.mkdir()
        (self.profile / "app.txt").write_text(f"game_dir={stale}\n")
        (self.d / "install").rename(self.d / "unavailable-install")
        expected = str(moved / ex.MANIFEST.parent / "pc_root")
        environment = dict(os.environ, BOOT_USERDATA=str(self.profile), NFS_ROOTS=str(self.profile / "roots.txt"))
        executable = moved / "Contents/MacOS" / ins.EXEC_NAME
        self.assertEqual(run([executable], env=environment).strip(), "selected=" + expected)
        self.assertTrue(ins.codesign_ok(moved))
        # Pinned original app_start selection: the same moved layout resolves
        # the still-valid external install from stale app.txt (the reported bug).
        install_exe(self.original_startup, executable)
        ex.sign_bundle(moved)
        self.assertEqual(run([executable], env=environment).strip(), "selected=" + str(stale))

    def test_cross_volume_path_copies_and_checks_sha256(self):
        def cross_volume(source, destination):
            raise OSError(errno.EXDEV, "Cross-device link")
        metrics = self.export(clone=cross_volume)
        self.assertEqual(metrics["cloned_files"], 0)
        self.assertEqual(metrics["digest_verified_bytes"], metrics["transferred_bytes"])
        self.assertGreater(metrics["copied_files"], 4)
        self.assertTrue(ins.codesign_ok(self.destination))
        print("EXPORT_EXDEV_FIXTURE " + json.dumps(metrics, sort_keys=True))
        self.destination.rename(self.d / "Previous.app")
        copy = shutil.copy2

        def corrupt(source, destination):
            result = copy(source, destination)
            if Path(source).name == "synthetic.bin":
                Path(destination).write_bytes(b"damaged synthetic fixture")
            return result
        with patch.object(ex.shutil, "copy2", corrupt):
            with self.assertRaisesRegex(ins.InstallError, "SHA-256"):
                self.export(clone=cross_volume)
        self.assertFalse(self.destination.exists())
        self.assertTrue(ins.codesign_ok(self.d / "Previous.app"))
        self.assertFalse(list(self.d.glob(".all-in-one-*")))

    def test_real_cross_volume_copy_when_disk_images_are_available(self):
        image, mount = self.d / "fixture.dmg", self.d / "mounted"
        mount.mkdir()
        created = subprocess.run(["hdiutil", "create", "-size", "32m", "-fs", "HFS+", "-volname", "ExportFixture",
                                  "-format", "UDRW", str(image)], capture_output=True, text=True, timeout=60)
        if created.returncode:
            self.skipTest("scratch disk images unavailable: " + created.stderr[-180:].strip())
        try:
            attached = subprocess.run(["hdiutil", "attach", "-nobrowse", "-noautoopen", "-mountpoint", str(mount), str(image)],
                                      capture_output=True, text=True, timeout=60)
            if attached.returncode:
                self.skipTest("scratch disk image mount unavailable: " + attached.stderr[-180:].strip())
            self.assertNotEqual(self.app.stat().st_dev, mount.stat().st_dev)
            destination = mount / "CrossVolume.app"
            metrics = ex.export_app(self.app, self.profile, destination)
            self.assertEqual(metrics["cloned_files"], 0)
            self.assertEqual(metrics["copied_files"], 7)
            self.assertEqual(metrics["digest_verified_bytes"], metrics["transferred_bytes"])
            self.assertTrue(ins.codesign_ok(destination))
            for key in ex.ASSET_KEYS:
                self.assertEqual((destination / ex.MANIFEST.parent / key / "synthetic.bin").read_bytes(),
                                 (self.roots[key] / "synthetic.bin").read_bytes())
            print("EXPORT_REAL_CROSS_VOLUME " + json.dumps(metrics, sort_keys=True))
        finally:
            if os.path.ismount(mount):
                detached = subprocess.run(["hdiutil", "detach", str(mount)], capture_output=True, text=True, timeout=60)
                if detached.returncode:
                    detached = subprocess.run(["hdiutil", "detach", "-force", str(mount)], capture_output=True, text=True, timeout=60)
                self.assertEqual(detached.returncode, 0, detached.stderr[-1000:])

    def test_updater_and_install_refuse_exports_before_profile_mutation(self):
        self.export()
        before = (self.profile / "app.txt").read_bytes()
        for operation in (
                lambda: apply.read_ready(self.d / "ready.json", self.profile, self.destination),
                lambda: apply.apply_ready(self.d / "ready.json", self.profile, self.destination),
                lambda: apply.supervise(self.profile, self.destination),
                lambda: stage.validate_destination(self.destination)):
            with self.assertRaisesRegex(ins.InstallError, "Re-export from Setup to update"):
                operation()
        self.assertEqual((self.profile / "app.txt").read_bytes(), before)
        self.assertFalse(ins.journal_path(self.profile).exists())
        with patch.object(ex, "is_export", return_value=False):
            ex.refuse_update(self.destination)  # deliberately broken guard accepts it
        self.assertFalse(ex.is_export(self.app))
        with self.assertRaisesRegex(ins.InstallError, "destination already exists"):
            self.export()
        install_dest = self.d / "ordinary-install"
        install_dest.mkdir()
        self.destination.rename(install_dest / ins.APP_BUNDLE)
        with self.assertRaisesRegex(ins.InstallError, "Re-export from Setup to update"):
            ins.transactional_install(self.app, self.d / "unused-stage", self.roots, self.profile, install_dest,
                                      verify=lambda _: None, running=lambda: None)
        self.assertFalse(ins.journal_path(self.profile).exists())

    def test_missing_or_symlinked_assets_and_signing_failure_publish_nothing(self):
        missing = self.roots["movies"]
        missing.rename(self.d / "removed")
        with self.assertRaisesRegex(ins.InstallError, "movies"):
            self.export()
        missing.symlink_to(self.d / "removed", target_is_directory=True)
        with self.assertRaisesRegex(ins.InstallError, "symbolic links"):
            self.export()
        missing.unlink()
        (self.d / "removed").rename(missing)
        (self.roots["pc_root"] / "escape").symlink_to(self.profile / "app.txt")
        with self.assertRaisesRegex(ins.InstallError, "link or special"):
            self.export()
        (self.roots["pc_root"] / "escape").unlink()
        unreadable = self.roots["pc_root"] / "unreadable"
        unreadable.mkdir()
        (unreadable / "synthetic.bin").write_bytes(b"must not be skipped")
        unreadable.chmod(0)
        try:
            with self.assertRaises(PermissionError):
                self.export()
        finally:
            unreadable.chmod(0o700)
        with self.assertRaisesRegex(RuntimeError, "signing failed"):
            self.export(sign=lambda _: (_ for _ in ()).throw(RuntimeError("signing failed")))
        self.assertFalse(self.destination.exists())
        self.assertFalse(list(self.d.glob(".all-in-one-*")))

    def test_overlapping_roots_and_preference_capture(self):
        self.roots["movies"] = self.roots["x360_root"] / "Movies"
        self.roots["movies"].mkdir()
        (self.roots["movies"] / "movie.bin").write_bytes(b"synthetic movie")
        self.write_roots()
        preferred = self.d / "preferred-pc"
        preferred.mkdir()
        (preferred / "preferred.bin").write_bytes(b"selected PC data")
        (self.profile / "app.txt").write_text(f"game_dir={preferred}\n")
        self.export()
        manifest = ex._manifest(self.destination / ex.MANIFEST)
        self.assertEqual(manifest["movies"], "x360_root/Movies")
        self.assertTrue((self.destination / ex.MANIFEST.parent / "pc_root/preferred.bin").is_file())
        self.assertFalse((self.destination / ex.MANIFEST.parent / "movies").exists())

    def test_pc_only_export_and_reexport_do_not_inherit_external_optional_roots(self):
        for key in tuple(self.roots):
            if key != "pc_root":
                del self.roots[key]
        self.write_roots()
        self.export()
        for key in ("x360_root", "assets_root", "movies"):
            self.assertEqual(self.inspect(self.destination)[key], "(none)")
        self.assertEqual(self.inspect(self.destination)["x360"], "/dev/null/x360-not-installed")
        newer = self.d / "Reexported.app"
        metrics = ex.export_app(self.destination, self.profile, newer)
        self.assertEqual(self.inspect(newer)["selected"], str(newer / ex.MANIFEST.parent / "pc_root"))
        self.assertEqual(metrics["cloned_files"] + metrics["copied_files"], 4)
        self.assertTrue(ins.codesign_ok(self.destination))
        self.assertTrue(ins.codesign_ok(newer))

    def test_final_signing_control_and_existing_app_preserved(self):
        self.export(sign=lambda _: None)  # deliberately broken final-sign boundary
        self.assertFalse(ins.codesign_ok(self.destination))
        self.assertTrue(ins.codesign_ok(self.app))
        self.destination.rename(self.d / "Unsigned.app")
        self.export()
        self.assertTrue(ins.codesign_ok(self.destination))
        self.assertTrue(ins.codesign_ok(self.app))

    def test_native_updater_displays_reexport_and_never_reads_profile(self):
        install_exe(self.updater, self.app / "Contents/MacOS" / ins.EXEC_NAME)
        ex.sign_bundle(self.app)
        self.export()
        home = self.d / "isolated-home"
        home.mkdir()
        environment = dict(os.environ, HOME=str(home), CFFIXED_USER_HOME=str(home))
        result = run([self.destination / "Contents/MacOS" / ins.EXEC_NAME], env=environment)
        observed = dict(line.split("=", 1) for line in result.splitlines())
        self.assertIn("Re-export from Setup to update", observed["message"])
        self.assertEqual(observed["label"], "Re-export from Setup to update")
        self.assertEqual(observed["registrations"], "1")
        install_exe(self.broken_updater, self.destination / "Contents/MacOS" / ins.EXEC_NAME)
        ex.sign_bundle(self.destination)
        broken = subprocess.run([self.destination / "Contents/MacOS" / ins.EXEC_NAME], env=environment,
                                capture_output=True, text=True)
        self.assertEqual(broken.returncode, 97)
        self.assertIn("export updater guard missing", broken.stderr)

    def test_cli_export_events_and_profile_unchanged(self):
        environment = dict(os.environ, NFSMW_SETUP_HOME=str(self.d / "setup"), NFSMW_SETUP_DATA=str(self.profile))
        (self.profile / "player-save.bin").write_bytes(b"external player save")
        (self.profile / "player-settings.txt").write_text("external player settings\n")
        before = {p.name: p.read_bytes() for p in self.profile.iterdir() if p.is_file()}
        output = run([sys.executable, "-I", ROOT / "tools/setup.py", "--export-all-in-one", "--app", self.app,
                      "--dest", self.destination, "--events"], env=environment)
        events = [json.loads(line) for line in output.splitlines()]
        self.assertEqual(events[-1], dict(ev="done", app=str(self.destination)))
        self.assertTrue(any(e["ev"] == "warn" and "Volume space used" in e["msg"] for e in events))
        for name, data in before.items():
            self.assertEqual((self.profile / name).read_bytes(), data)
        for name in ("player-save.bin", "player-settings.txt", "app.txt"):
            self.assertFalse(list(self.destination.rglob(name)))

    def test_old_signed_runtime_requires_export_capability(self):
        source = self.d / "old.c"
        source.write_text("int main(void) { return 0; }\n")
        run(["clang", source, "-o", self.app / "Contents/MacOS" / ins.EXEC_NAME])
        ex.sign_bundle(self.app)
        self.assertTrue(ins.app_complete(self.app))
        with self.assertRaisesRegex(ins.InstallError, "does not support.*export"):
            self.export()
        self.assertFalse(self.destination.exists())
        self.assertFalse(list(self.d.glob(".all-in-one-*")))

    def test_missing_export_manifest_keeps_identity_and_refuses_fallback(self):
        self.export()
        (self.destination / ex.MANIFEST).unlink()
        # A valid external installation must never hide the damaged backup.
        observed = self.inspect(self.destination, argument=True)
        self.assertEqual(observed["exported"], "1")
        self.assertEqual(observed["selected"], "(none)")
        self.assertEqual(observed["pc_root"], "(none)")
        with self.assertRaisesRegex(ins.InstallError, "Re-export from Setup"):
            ex.refuse_update(self.destination)
        with self.assertRaisesRegex(ins.InstallError, "manifest.*missing"):
            ex.current_roots(self.destination, self.profile)
        shutil.rmtree(self.destination / ex.MANIFEST.parent)
        self.assertEqual(self.inspect(self.destination)["exported"], "1")
        self.assertEqual(self.inspect(self.destination)["selected"], "(none)")
        executable = self.destination / "Contents/MacOS" / ins.EXEC_NAME
        install_exe(self.startup, executable)
        result = subprocess.run([executable], env=dict(os.environ, BOOT_USERDATA=str(self.profile)),
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 94)  # damaged-export alert, before folder chooser/profile writes
        install_exe(self.updater, executable)
        self.assertIn("Re-export from Setup to update", run([executable]))

    def test_export_media_ignores_stale_paths_and_keeps_texture_switches(self):
        install_exe(self.media, self.app / "Contents/MacOS" / ins.EXEC_NAME)
        ex.sign_bundle(self.app)
        self.export()
        executable = self.destination / "Contents/MacOS" / ins.EXEC_NAME
        embedded = self.destination / ex.MANIFEST.parent
        for stale in ("", str(self.d / "missing-media"), str(self.roots["movies"])):
            environment = dict(os.environ, NFS_X360_MOVIES=stale, NFS_X360_TEXTURES=stale,
                               NFS_X360_UI_TEXTURES="off")
            output = run([executable], env=environment)
            self.assertIn(f"movies={embedded / 'movies'}\n", output)
            self.assertIn(f"scan={embedded / 'assets_root/hd_textures_mips'}\n", output)
        for switch, count in (("off", 0), ("0", 0), ("on", 1), ("1", 1)):
            output = run([executable], env=dict(os.environ, NFS_X360_TEXTURES=switch, NFS_X360_UI_TEXTURES="off"))
            self.assertIn(f"textures={count}\n", output)
        # Ordinary apps continue to accept explicit development paths.
        output = run([self.app / "Contents/MacOS" / ins.EXEC_NAME],
                     env=dict(os.environ, BOOT_USERDATA=str(self.profile), NFS_X360_MOVIES="/movie-override",
                              NFS_X360_TEXTURES="/texture-override", NFS_X360_UI_TEXTURES="off"))
        self.assertIn("movies=/movie-override\n", output)
        self.assertIn("scan=/texture-override\n", output)

    def test_cli_reports_published_app_after_sync_failure_or_cancel(self):
        environment = dict(NFSMW_SETUP_HOME=str(self.d / "setup"), NFSMW_SETUP_DATA=str(self.profile))
        sync = ins.sync_dir
        events_class = backend.Events
        for failure, full_log in ((failure, full_log) for failure in ("disk-full", "cancel", "rename-cancel")
                                  for full_log in (False, True)):
            with self.subTest(failure=failure, full_log=full_log):
                destination = self.d / (failure + ("-full-log" if full_log else "") + ".app")
                failed_writes = []
                def make_events(enabled, log):
                    write = log.write
                    def fail_write(data):
                        if full_log and destination.exists() and not log.closed:
                            failed_writes.append(data)
                            raise OSError(errno.ENOSPC, "injected log failure")
                        return write(data)
                    log.write = fail_write
                    return events_class(enabled, log)
                def fail_sync(path):
                    if destination.exists() and Path(path) == self.d:
                        if failure == "disk-full":
                            raise OSError(errno.ENOSPC, "injected sync failure")
                        signal.raise_signal(signal.SIGINT)
                    return sync(path)
                rename = ins.rename_new
                def cancel_rename(source, target):
                    rename(source, target)
                    signal.raise_signal(signal.SIGTERM)
                output = io.StringIO()
                with patch.dict(os.environ, environment), contextlib.redirect_stdout(output), \
                        patch.object(backend, "Events", make_events), \
                        patch.object(ins, "sync_dir", fail_sync), \
                        patch.object(ins, "rename_new", cancel_rename if failure == "rename-cancel" else rename):
                    status = backend.main(["--export-all-in-one", "--app", str(self.app), "--dest", str(destination), "--events"])
                events = [json.loads(line) for line in output.getvalue().splitlines()]
                self.assertTrue(destination.exists())
                self.assertTrue(ins.codesign_ok(destination))
                self.assertEqual(status, 0, events)
                self.assertEqual(events[-1], dict(ev="done", app=str(destination)))
                self.assertFalse(any(e["ev"] == "error" for e in events))
                if full_log:
                    self.assertTrue(any(b'"ev":"warn"' in data for data in failed_writes))
                    self.assertTrue(any(b'"ev":"done"' in data for data in failed_writes))


if __name__ == "__main__":
    unittest.main()
