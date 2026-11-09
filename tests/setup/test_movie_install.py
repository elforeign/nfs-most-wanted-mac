#!/usr/bin/env python3
"""Disc movie installation and ordinary startup logging, with synthetic media only.

Regression proof: loose-MOVIES ISO on original code: 0/1 installed; fixed: 1/1.
Pinned original Disc initializer: 0/1; current initializer: 1/1, same oracle.
Startup with diagnostics off, missing/empty MOVIES: original 0/2 warnings;
current 2/2. A populated mixed-case MOVIES folder emits 0 warnings.
Duplicate install proof: original installer succeeds 2/2; first movie-aware
version succeeds 0/2; fixed succeeds 2/2 with one byte-identical movie retained.
Both archived + loose and loose on two discs are covered. Different-size and
same-size conflicting copies are refused with a plain message. The original
installer did not copy loose movies; its control checks installation success.
Original fragments are embedded and SHA-256 checked, independent of git history.

Installed game folder (a player's report: a game-files install, no movies played):
Setup copies MOVIES from a folder source byte for byte (checked below); a folder
with no or an empty MOVIES gave no note: original 0/2 notes, fixed 2/2, and a
populated mixed-case MOVIES 0 notes. The game asks for <name>_<language>_<region>
.vp6 with region NTSC only for English US (LanguageConversionTable 0x008FB578,
BuildRegion::IsPal 0x0064A110), so English US on a PAL-named install, or a European
language on an NTSC one, missed every movie. Original bFileExists hook (no X360
folder): 0/4 of those requests played; fixed: 4/4 play the installed variant, the
game's own English fallback is left to it, and a movie with no file is logged once.

Trace: the installed PC installer's filelist.txt names 32 MOVIES VP6 files.
Setup's supported disc payloads are numbered compressed.zip archives, not CABs;
its old fixtures omitted movies entirely. common_filelist.txt only supplied
loose root files, and both mounted-folder and ISO readers discarded directories.
CalculateMovieFilename (0x00516130) builds MOVIES\\<name>_<language>_<region>.vp6;
FEngMovieStarter::Callback (0x00559680) advances when bFileExists fails. The port
may substitute a matching X360 WMV, otherwise MoviePlayer::Play uses PC VP6.
"""
import hashlib
import io
import inspect
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock
import zipfile

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_backend import Fixture, FakeRunner, make_iso, write, b

ORIGINAL_DISC = r'''    def __init__(self, path):
        self.path, self.handle = Path(path), None
        if self.path.is_dir():
            self.files = {}
            for seen, p in enumerate(self.path.iterdir()):
                if seen >= MAX_ROOT_FILES:  # every entry counts, folders and links too
                    raise DiscError("The disc folder holds far too many files.")
                if p.is_file() and not p.is_symlink():
                    if p.name.casefold() in self.files:
                        raise DiscError(f"The disc folder has {p.name} twice (in different case).")
                    self.files[p.name.casefold()] = (p.name, None, p.stat().st_size)
        elif self.path.is_file():
            self.handle = self.path.open("rb")
            try:
                size = os.fstat(self.handle.fileno()).st_size
                self.files = _listing(self.handle, size, *_root(self.handle, size))
            except BaseException:
                self.close()
                raise
        else:
            raise DiscError(f"{self.path} is not a disc image or a disc folder.")
        inf = self.text("autorun.inf")
        number = re.search(r"(?im)^\s*Disk\s*=\s*(\d+)\s*$", inf)
        product = re.search(r"(?im)^\s*ProductGuiID\s*=\s*(\S+)\s*$", inf)
        self.number = int(number.group(1)) if number else 1
        self.product = product.group(1).upper() if product else None
        archives = sorted((int(m.group(1)), self.files[k][0]) for k in self.files if (m := ARCHIVE.fullmatch(k)))
        self.archives = [name for _, name in archives]
'''
ORIGINAL_DISC_SHA256 = "ccc89ca5308fcc14377a59a17bc183c89071fcd8ba69c37977e0593ab5e2ac96"
ORIGINAL_START = r'''int main(int argc, char **argv) {
    game_dir = app_start(argc, argv);
    void settings_init(void), settings_dump(const char *), report_init(void);
    settings_init();                       // settings.c: the F10 menu's saved values, before anything reads its switch
    private_cars_boot();
    void settings_language_setup(const char *);
    settings_language_setup(game_dir);     // settings_port.c: the language packs present; the Language row when > 1
    if (getenv("BOOT_DIAG")) { char p[1100]; snprintf(p, sizeof p, "%s/settings_rows.txt", getenv("BOOT_DIAG")); settings_dump(p); }
    report_init();                         // report.m: full logging (next launch), the hang watchdog
    save_cleanup();                        // discard interrupted saves before any guest profile scan
    boot_trace = getenv("BOOT_TRACE") != NULL;
    deadline_start();
'''
ORIGINAL_START_SHA256 = "0ba351dcdbe12f69e12755bf8a4be0974995535922763929fed474262491b218"


# Pinned copy policies: the original installer copied only disc 1 root files;
# the first movie-aware version rejected every existing loose destination.
ORIGINAL_LOOSE = r'''    for name in loose:
        if name.casefold() in {p.name.casefold() for p in out.iterdir()}:
            raise SetupError(f"The discs hold {name} twice.", DISC_FIX)
        with first.open(name) as src, open(out / name, "xb") as dst:
            shutil.copyfileobj(src, dst, 1 << 20)
        if (out / name).stat().st_size != first.size(name):
            raise SetupError(f"{name} on disc 1 changed or was cut short while Setup copied it.", DISC_FIX)
'''
ORIGINAL_LOOSE_SHA256 = "8f582a1a7e3a757a5fd007ba33d926d88639a3d4fdc5434bea037af1e927e95f"
FIRST_MOVIE_LOOSE = r'''    for disc, name in loose_files:
        # Merge Windows component case, including a MOVIES folder already created by an archive.
        target = out
        for part in name.split("/"):
            matches = [p for p in target.iterdir() if p.name.casefold() == part.casefold()] if target.is_dir() else []
            if len(matches) > 1:
                raise SetupError(f"The discs hold {name} twice (in different case).", DISC_FIX)
            target = matches[0] if matches else target / part
        if target.exists():
            raise SetupError(f"The discs hold {name} twice.", DISC_FIX)
        target.parent.mkdir(parents=True, exist_ok=True)
        with disc.open(name) as src, open(target, "xb") as dst:
            shutil.copyfileobj(src, dst, 1 << 20)
        if target.stat().st_size != disc.size(name):
            raise SetupError(f"{name} on disc {disc.number} changed or was cut short while Setup copied it.", DISC_FIX)
'''
FIRST_MOVIE_LOOSE_SHA256 = "8b286b2b721baf6b4270e061827b84c0179ee58ff8e5ebb0e6516a76328117b5"


def pinned(fragment, digest):
    assert hashlib.sha256(fragment.encode()).hexdigest() == digest
    return fragment


class MovieDiscTests(Fixture):
    def setUp(self):
        super().setUp()
        self.image = self.home / "disc.iso"
        self.movie = "MOVIES/ealogo_english_ntsc.vp6"
        self.video = b"synthetic VP6 fixture"
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for p in self.pc.rglob("*"):
                if p.is_file() and p.name != "speed.exe":
                    archive.writestr(str(p.relative_to(self.pc)).replace("/", "\\"), p.read_bytes())
        self.files = {"autorun.inf": "[Special]\nDisk=1\nProductGuiID={MOVIE-FIXTURE}\n".encode("ascii"),
                      "common_filelist.txt": "1,1,speed.exe\n1,1,MOVIES\\ealogo_english_ntsc.vp6\n".encode("ascii"),
                      "speed.exe": self.data, "0compressed.zip": buffer.getvalue()}

    def extract(self, source=None):
        return b.extract_pc_disc(source or self.image, None, None, self.paths.work, self.events, self.source)

    def assert_movie(self):
        root = self.extract()
        self.assertEqual((root / self.movie).read_bytes(), self.video)

    def test_loose_iso_original_and_current(self):
        make_iso(self.image, dict(self.files, **{self.movie: self.video}))
        from setup_kit import pc_disc
        scope = {}
        exec(textwrap.dedent(pinned(ORIGINAL_DISC, ORIGINAL_DISC_SHA256)), pc_disc.__dict__, scope)
        with mock.patch.object(pc_disc.Disc, "__init__", scope["__init__"]):
            # The identical successful-install oracle must reject the old reader.
            with self.assertRaises((AssertionError, FileNotFoundError, b.SetupError)):
                self.assert_movie()
        shutil.rmtree(self.paths.work / "pc_src", ignore_errors=True)
        (self.paths.work / "pc_src.json").unlink(missing_ok=True)
        self.assert_movie()

    def test_loose_iso_without_joliet(self):
        make_iso(self.image, dict(self.files, **{self.movie: self.video}), joliet=False)
        root = self.extract()
        self.assertEqual((root / self.movie.upper()).read_bytes(), self.video)

    def test_loose_movies_on_later_disc(self):
        self.files["common_filelist.txt"] += b"2,1,\n"
        make_iso(self.image, self.files)
        second = dict(self.files, **{self.movie: self.video})
        second["autorun.inf"] = second["autorun.inf"].replace(b"Disk=1", b"Disk=2")
        second["1compressed.zip"] = second.pop("0compressed.zip")
        # Later disc's archive is empty; its loose movies still belong to the install.
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w"):
            pass
        second["1compressed.zip"] = buffer.getvalue()
        make_iso(self.home / "second.iso", second)
        self.assert_movie()

    def test_archive_movies_still_install(self):
        self.files["common_filelist.txt"] = b"1,1,speed.exe\n"
        buffer = io.BytesIO(self.files["0compressed.zip"])
        with zipfile.ZipFile(buffer, "a") as archive:
            archive.writestr(self.movie.replace("/", "\\"), self.video)
        self.files["0compressed.zip"] = buffer.getvalue()
        make_iso(self.image, self.files)
        self.assert_movie()

    def duplicate_discs(self, layout, other=None):
        files = dict(self.files, **{self.movie: self.video})
        if layout == "archive":
            buffer = io.BytesIO(files["0compressed.zip"])
            with zipfile.ZipFile(buffer, "a") as archive:
                archive.writestr("mOvIeS/EaLogo_English_Ntsc.VP6", self.video if other is None else other)
            files["0compressed.zip"] = buffer.getvalue()
        else:
            files["common_filelist.txt"] += b"2,1,\n"
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w"):
                pass
            second = {"autorun.inf": files["autorun.inf"].replace(b"Disk=1", b"Disk=2"),
                      "1compressed.zip": buffer.getvalue(),
                      "movies/EALOGO_ENGLISH_NTSC.VP6": self.video if other is None else other}
            make_iso(self.home / "second.iso", second)
        make_iso(self.image, files)

    def clear_install(self):
        shutil.rmtree(self.paths.work / "pc_src", ignore_errors=True)
        (self.paths.work / "pc_src.json").unlink(missing_ok=True)

    def assert_single_movie(self):
        root = self.extract()
        movies = [p for p in root.rglob("*") if p.suffix.casefold() == ".vp6"]
        self.assertEqual(len(movies), 1)
        self.assertEqual(movies[0].read_bytes(), self.video)
        self.assertEqual(len([p for p in root.iterdir() if p.name.casefold() == "movies"]), 1)

    def test_archived_and_loose_identical(self):
        self.duplicate_discs("archive")
        self.assert_single_movie()

    def test_two_discs_loose_identical(self):
        self.duplicate_discs("loose")
        self.assert_single_movie()

    def test_conflicting_movie_duplicates(self):
        for layout in ("archive", "loose"):
            for other in (self.video + b" changed", self.video[:-1] + b"!"):
                with self.subTest(layout=layout, size=len(other)):
                    self.duplicate_discs(layout, other)
                    with self.assertRaisesRegex(b.SetupError, r"different contents.*[Ee][Aa][Ll][Oo][Gg][Oo]"):
                        self.extract()
                    self.assertFalse((self.paths.work / "pc_src").exists())

    def test_duplicate_install_pinned_controls(self):
        source = inspect.getsource(b._install_discs)
        start = source.index("    for disc, name in loose_files:\n")
        end = source.index("    if patch:\n", start)
        for label, fragment, digest, successes in (
                ("original", ORIGINAL_LOOSE, ORIGINAL_LOOSE_SHA256, 2),
                ("first", FIRST_MOVIE_LOOSE, FIRST_MOVIE_LOOSE_SHA256, 0)):
            baseline = source[:start] + pinned(fragment, digest) + source[end:]
            if label == "original":
                # Restore the original return: it predates the loose-movie completeness check.
                baseline = baseline[:baseline.index("    movie_dirs =")] + "    return out\n"
            scope = {}
            exec(baseline, b.__dict__, scope)
            installed = 0
            for layout in ("archive", "loose"):
                self.clear_install()
                self.duplicate_discs(layout)
                with mock.patch.object(b, "_install_discs", scope["_install_discs"]):
                    try:
                        self.extract()
                    except b.SetupError as error:
                        self.assertEqual(label, "first")
                        self.assertIn("twice", str(error))
                    else:
                        installed += 1
            self.assertEqual(installed, successes, label)

    def test_folder_movies_and_changed_content_invalidate_reuse(self):
        folder = self.home / "mounted"
        for name, data in dict(self.files, **{self.movie: self.video}).items():
            write(folder / name, data)
        root = self.extract(folder)
        self.assertEqual((root / self.movie).read_bytes(), self.video)
        write(folder / self.movie, self.video + b" changed")
        root = self.extract(folder)
        self.assertEqual((root / self.movie).read_bytes(), self.video + b" changed")

    def test_missing_empty_and_zero_length_movies_refused(self):
        for files in ({}, {"MOVIES/readme.txt": b"no video"}, {self.movie: b""}):
            with self.subTest(files=list(files)):
                make_iso(self.image, dict(self.files, **files))
                with self.assertRaisesRegex(b.SetupError, "MOVIES"):
                    self.extract()
                self.assertFalse((self.paths.work / "pc_src").exists())

    def test_movie_links_refused(self):
        folder = self.home / "mounted"
        for name, data in self.files.items():
            write(folder / name, data)
        outside = self.home / "outside"
        write(outside / "ealogo_english_ntsc.vp6", self.video)
        (folder / "MOVIES").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(b.SetupError, "MOVIES|link"):
            self.extract(folder)

    def test_loose_movie_size_counts_toward_limit(self):
        make_iso(self.image, dict(self.files, **{self.movie: self.video}))
        with zipfile.ZipFile(io.BytesIO(self.files["0compressed.zip"])) as archive:
            before_movies = len(self.data) + sum(info.file_size for info in archive.infolist())
        with mock.patch.object(b, "ZIP_MAX_BYTES", before_movies), self.assertRaisesRegex(b.SetupError, "far more"):
            self.extract()

    def test_movie_directory_bounds_and_cycles(self):
        from setup_kit import pc_disc
        make_iso(self.image, dict(self.files, **{self.movie: self.video}))
        good = self.image.read_bytes()
        # Locate actual Joliet directory records, rather than assuming payload sector numbers.
        folder_record = good.index("MOVIES".encode("utf-16-be"), 20 * 2048) - 33
        movie_record = good.index((Path(self.movie).name + ";1").encode("utf-16-be")) - 33
        for record, extent, message in ((movie_record, len(good) // 2048 + 1, "truncated"),
                                        (folder_record, 20, "loop")):
            damaged = bytearray(good)
            struct.pack_into("<I", damaged, record + 2, extent)
            struct.pack_into(">I", damaged, record + 6, extent)
            self.image.write_bytes(damaged)
            with self.subTest(message=message), self.assertRaisesRegex(b.SetupError, message):
                self.extract()
        self.image.write_bytes(good)
        with mock.patch.object(pc_disc, "MAX_MOVIE_ENTRIES", 0), self.assertRaisesRegex(b.SetupError, "too many"):
            self.extract()

    def test_nested_movie_directories_and_depth_bound(self):
        from setup_kit import pc_disc
        nested = "MOVIES/extra/notes.txt"
        make_iso(self.image, dict(self.files, **{self.movie: self.video, nested: b"notes"}))
        root = self.extract()
        self.assertEqual((root / nested).read_bytes(), b"notes")
        with mock.patch.object(pc_disc, "MAX_MOVIE_DEPTH", 0), self.assertRaisesRegex(b.SetupError, "too deep"):
            self.extract()


class StartupMovieTests(unittest.TestCase):
    def test_normal_startup_reports_missing_and_empty_movies(self):
        boot = (ROOT / "runtime/boot.c").read_text()
        current = boot[boot.index("int main(int argc, char **argv) {"):]
        current = current[:current.index("    char path[4096];")]
        movies = (ROOT / "runtime/movies.c").read_text()
        helper = movies[movies.index("// BEGIN movie install check"):movies.index("// END movie install check")]
        original = pinned(ORIGINAL_START, ORIGINAL_START_SHA256)
        stubs = r'''
#include <dirent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>
const char *game_dir;
int boot_trace;
static const char *app_start(int argc, char **argv) { (void)argc; return argv[1]; }
static void settings_init(void) {}
static void private_cars_boot(void) {}
static void settings_language_setup(const char *p) { (void)p; }
static void settings_dump(const char *p) { (void)p; }
static void report_init(void) {}
static void save_cleanup(void) {}
static void deadline_start(void) {}
'''
        with tempfile.TemporaryDirectory(prefix="movie-start-") as tmp:
            scratch = Path(tmp)
            environment = {k: v for k, v in os.environ.items() if k not in ("BOOT_DIAG", "BOOT_TRACE")}
            for label, start, expected in (("original", original, 0), ("current", current, 2)):
                source, binary = scratch / (label + ".c"), scratch / label
                source.write_text(stubs + helper + start + "    return 0;\n}\n")
                subprocess.run(["clang", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined",
                                str(source), "-o", str(binary)], check=True, capture_output=True)
                game = scratch / (label + "-game")
                game.mkdir()
                warnings = 0
                for populate in (False, True):
                    if populate:
                        (game / "mOvIeS").mkdir()
                    result = subprocess.run([str(binary), str(game)], env=environment, capture_output=True, text=True, check=True)
                    warnings += len(result.stderr.splitlines())
                    if label == "current":
                        self.assertIn("movies:", result.stderr)
                        self.assertIn("missing" if not populate else "empty", result.stderr)
                self.assertEqual(warnings, expected)
                write(game / "mOvIeS" / "EaLogo.Vp6", b"movie")
                result = subprocess.run([str(binary), str(game)], env=environment, capture_output=True, text=True, check=True)
                self.assertEqual(result.stderr, "")


class GameFolderMovieTests(Fixture):
    """An installed game folder is copied whole, MOVIES included; without usable movies Setup says so."""
    def notes(self, build=False):
        start = len(self.stream.getvalue())
        with self.pipeline_stubs():
            b.build(self.args(check=not build), self.paths, self.events,
                    FakeRunner(self.log, self.child_env, self.paths.work), self.source)
        events = [json.loads(l) for l in self.stream.getvalue()[start:].splitlines() if l.startswith("{")]
        return [e["msg"] for e in events if e["ev"] == "warn" and e["id"] == "pc" and "MOVIES" in e["msg"]]

    def test_folder_movies_installed_and_missing_ones_reported(self):
        shutil.rmtree(self.pc / "MOVIES", ignore_errors=True)
        missing = self.notes()
        write(self.pc / "MOVIES/readme.txt", b"no video")
        write(self.pc / "MOVIES/ealogo_english_ntsc.vp6", b"")
        empty = self.notes()
        self.assertEqual((len(missing), len(empty)), (1, 1))
        self.assertIn("no MOVIES folder", missing[0])
        self.assertIn("empty MOVIES folder", empty[0])
        shutil.rmtree(self.pc / "MOVIES")
        write(self.pc / "Movies/EALOGO_ENGLISH_NTSC.VP6", b"synthetic movie")
        self.assertEqual(self.notes(build=True), [])
        roots = dict(l.split("=", 1) for l in (self.paths.data / "roots.txt").read_text().splitlines())
        installed = Path(roots["pc_root"]) / "Movies/EALOGO_ENGLISH_NTSC.VP6"
        self.assertEqual(installed.read_bytes(), b"synthetic movie")


class MovieVariantTests(unittest.TestCase):
    """The bFileExists hook names an installed movie of another region or language (movies.c movie variants)."""
    def run_requests(self, block, files, requests):
        harness = r'''
#include <dirent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>
const char *game_dir;
''' + block + r'''
int main(int argc, char **argv) {
    game_dir = argv[1];
    for (int i = 2; i < argc; ++i) {
        char guest[64];
        snprintf(guest, sizeof guest, "%s", argv[i]);
        printf("%d %s\n", movie_substitute(guest, sizeof guest), guest);
    }
    return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix="movie-variants-") as tmp:
            scratch = Path(tmp)
            (scratch / "h.c").write_text(harness)
            subprocess.run(["clang", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined", str(scratch / "h.c"),
                            "-o", str(scratch / "h")], check=True, capture_output=True)
            for name, data in files.items():
                write(scratch / "game/mOvIeS" / name, data)
            result = subprocess.run([str(scratch / "h"), str(scratch / "game")] + requests, capture_output=True, text=True, check=True)
        return [l.split(" ", 1) for l in result.stdout.splitlines()], result.stderr

    def test_installed_region_and_language_stand_in(self):
        movies = (ROOT / "runtime/movies.c").read_text()
        block = movies[movies.index("// BEGIN movie variants"):movies.index("// END movie variants")]
        pal = {"ealogo_english_pal.vp6": b"v", "storyfmv_cro06_coh06a_english_pal.vp6": b"v",
               "storyfmv_cro06_coh06a_french_pal.vp6": b"v", "psa_english_ntsc.vp6": b""}
        ntsc = {"ealogo_english_ntsc.vp6": b"v", "attract_movie_english_ntsc.vp6": b"v"}
        cases = [  # (installed files, the name the game asks for, what plays: "" = left as asked)
            (pal, "MOVIES\\ealogo_english_ntsc.vp6", "MOVIES\\ealogo_english_pal.vp6"),           # English US, PAL install
            (pal, "MOVIES\\storyfmv_cro06_coh06a_english_ntsc.vp6", "MOVIES\\storyfmv_cro06_coh06a_english_pal.vp6"),
            (ntsc, "MOVIES\\ealogo_german_pal.vp6", "MOVIES\\ealogo_english_ntsc.vp6"),            # German, NTSC install
            (ntsc, "MOVIES\\attract_movie_english_pal.vp6", "MOVIES\\attract_movie_english_ntsc.vp6"),
        ]
        played = 0
        for files, want, plays in cases:
            (result, name), = self.run_requests(block, files, [want])[0]
            played += result == "1" and name == plays
        self.assertEqual(played, len(cases))
        # The original hook leaves every one of these as not found (no X360 folder): 0 played.
        # English in the asked region: the game's own second lookup finds it, so it is left alone and not logged.
        out, err = self.run_requests(block, pal, ["MOVIES\\ealogo_french_pal.vp6"])
        self.assertEqual(out, [["0", "MOVIES\\ealogo_french_pal.vp6"]])
        self.assertEqual(err, "")
        # A zero-length file never stands in; a movie with no file is logged once, at the English lookup.
        out, err = self.run_requests(block, pal, ["MOVIES\\psa_french_pal.vp6", "MOVIES\\psa_english_pal.vp6",
                                                  "MOVIES\\blacklist_01_english_ntsc.vp6"])
        self.assertEqual([r for r, _ in out], ["0", "0", "0"])
        self.assertEqual(len(err.splitlines()), 2)
        self.assertIn("blacklist_01_english_ntsc.vp6 is not installed", err)


if __name__ == "__main__":
    unittest.main()
