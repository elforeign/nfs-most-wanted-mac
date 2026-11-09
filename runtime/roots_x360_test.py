"""Offline test of roots.h's X360 feature gates for the public build (NFS_PUBLIC) without a roots.txt: an explicit
NFS_X360_FILES is honoured (as tools/gen_tables.py honours it at build), no override means no X360 features, and a
roots.txt still decides when there is one. No game launch; synthetic folders only.
Run: tools/.venv/bin/python -I runtime/roots_x360_test.py"""
import os
import hashlib
import shutil
import sys
from pathlib import Path
import subprocess
import tempfile

RUNTIME = Path(__file__).resolve().parent
C = r'''
#include <stdio.h>
#include "roots.h"
const char *game_dir;
int main(void) {
    printf("%d %d %d\n", roots_x360_has("audio"), roots_x360_has("vinyls"), roots_x360_has("world"));
    return 0;
}
'''


def run(binary, env):
    e = {"PATH": os.environ["PATH"], "HOME": env.pop("HOME")}
    e.update(env)
    return subprocess.run([str(binary)], env=e, capture_output=True, text=True, check=True).stdout.strip()


def main():
    with tempfile.TemporaryDirectory(prefix="roots-x360-") as d:
        d = Path(d)
        x = d / "x360"
        (x / "SOUND").mkdir(parents=True)
        (x / "GLOBAL").mkdir()
        (x / "GLOBAL/INGAMEMEMORYFILE.BIN").write_bytes(b"x")
        (x / "CARS").mkdir()
        (d / "t.c").write_text(C)
        for flag in ("-DNFS_PUBLIC=1", ""):
            binary = d / ("t_pub" if flag else "t_dev")
            subprocess.run(["clang", "-Wall", "-Werror", "-I", str(RUNTIME), str(d / "t.c"), "-o", str(binary)] + ([flag] if flag else []), check=True)
            home = {"HOME": str(d / "home"), "NFS_ROOTS": str(d / "none.txt")}
            none = run(binary, dict(home))
            env = run(binary, dict(home, NFS_X360_FILES=str(x)))
            if flag:
                assert none == "0 0 0", none           # no roots.txt, no override: no X360 features
                assert env == "1 1 0", env             # the explicit override is honoured (audio, vinyls; no GLOBALB.BUN)
            else:
                assert none == "1 1 1", none           # development layout without a manifest: always
            # a manifest decides when there is one, whatever the environment says
            roots = d / "roots.txt"
            roots.write_text("pc_root=/nonexistent\n")
            if flag:    # public: an explicit override wins over the manifest (here the manifest has no X360 root)
                assert run(binary, dict(home, NFS_ROOTS=str(roots), NFS_X360_FILES=str(x))) == "1 1 0"
                empty = d / "empty"
                empty.mkdir(exist_ok=True)
                roots.write_text("pc_root=/nonexistent\nx360_root=%s\n" % x)       # manifest has files, override points at none
                assert run(binary, dict(home, NFS_ROOTS=str(roots), NFS_X360_FILES=str(empty))) == "0 0 0"
            else:       # development: the manifest decides, as before
                assert run(binary, dict(home, NFS_ROOTS=str(roots), NFS_X360_FILES=str(x))) == "0 0 0"
            roots.write_text("pc_root=/nonexistent\nx360_root=%s\n" % x)
            assert run(binary, dict(home, NFS_ROOTS=str(roots))) == "1 1 0"
            print("PASS: %s build" % ("public" if flag else "development"))


ORIGINAL_PRESENCE = '        return shaders ? roots_file_ok_(r, "NFS/ZZDATA0.BIN") : roots_file_ok_(r, "default.xex") || roots_file_ok_(r, "../default.xex");'
ORIGINAL_PRESENCE_SHA = '6f82e97d5fe8e29bc8afcaaf91a0ec1c47875158be9c002f511c8b4ae8a6dff2'


def retained_shader_presence():
    """Retained-input matrix: original 56/64 pass; fixed 64/64 pass.
    Also test length, built flags, full-file compatibility, and engine independence.
    """
    with tempfile.TemporaryDirectory(prefix="roots-shader-") as tmp:
        d = Path(tmp)
        header = (RUNTIME / "roots.h").read_text()
        if "--broken-retention" in sys.argv:
            assert hashlib.sha256(ORIGINAL_PRESENCE.encode()).hexdigest() == ORIGINAL_PRESENCE_SHA
            start = header.index('        if (!shaders) return')
            end = header.index('\n    }', start)
            header = header[:start] + ORIGINAL_PRESENCE + header[end:]
        (d / "roots.h").write_text(header)
        (d / "bundle_roots.h").write_bytes((RUNTIME / "bundle_roots.h").read_bytes())
        (d / "gen").mkdir()
        (d / "t.c").write_text(C.replace('roots_x360_has("audio"), roots_x360_has("vinyls"), roots_x360_has("world")',
                                         'roots_x360_has("shaders"), roots_x360_has("engine"), roots_active()'))
        root = d / "files"
        (root / "NFS").mkdir(parents=True)
        retained = root / "NFS/zzdata0_09bb7000.bin"
        roots = d / "roots.txt"
        roots.write_text("pc_root=/missing\nx360_root=%s\n" % root)
        failures, checks = 0, 0
        for public in (False, True):
            for shaders, engine in ((0, 0), (1, 0), (0, 1), (1, 1)):
                (d / "gen/x360_shader_lits.inc").write_text("#define X360_SHADER_LITS %d\n" % shaders)
                (d / "gen/x360_engine.inc").write_text("#define X360_ENGINE_CONSTS %d\n" % engine)
                binary = d / "test"
                subprocess.run(["clang", "-Wall", "-Werror", "-I", str(d), str(d / "t.c"), "-o", str(binary)] +
                               (["-DNFS_PUBLIC=1"] if public else []), check=True)
                env = dict(HOME=str(d / "home"), NFS_ROOTS=str(roots))
                for size, want in ((413696, shaders), (413695, 0), (413697, 0), (0, 0), (None, 0)):
                    if size is None:
                        retained.unlink()
                    else:
                        with retained.open("wb") as f:
                            f.truncate(size)
                    got = run(binary, dict(env))
                    checks += 1
                    failures += got != "%d 0 1" % want
                (root / "NFS/ZZDATA0.BIN").write_bytes(b"legacy")
                (root / "default.xex").write_bytes(b"engine")
                checks += 1
                failures += run(binary, dict(env)) != "%d %d 1" % (shaders, engine)
                (root / "default.xex").unlink()
                (root / "NFS/ZZDATA0.BIN").unlink()
                # A source moved to an export keeps relative roots and wins over stale overrides.
                bundle = d / "Moved.app/Contents"
                (bundle / "MacOS").mkdir(parents=True, exist_ok=True)
                resources = bundle / "Resources/AllInOne"
                resources.mkdir(parents=True, exist_ok=True)
                (resources / "roots.txt").write_text("x360_root=files\n")
                retained.write_bytes(bytes(413696))
                (root / "default.xex").write_bytes(b"engine")
                shutil.copytree(root, resources / "files")
                moved = bundle / "MacOS/test"
                binary.rename(moved)
                checks += 1
                failures += run(moved, dict(env, NFS_X360_FILES=str(d / "missing"))) != "%d %d 1" % (shaders, engine)
                (resources / "files/NFS/zzdata0_09bb7000.bin").unlink()
                checks += 1
                failures += run(moved, dict(env, NFS_X360_FILES=str(root))) != "0 %d 1" % engine
                shutil.rmtree(resources / "files")
                (root / "default.xex").unlink()
                retained.unlink()
        print("shader presence: %d/%d passed" % (checks - failures, checks))
        assert not failures, "%d retained presence failures" % failures


def generator_precedence():
    """tools/gen_tables.py: in the public build an explicit NFS_X360_FILES beats the manifest's x360_root (the generator
    fails inside the empty override folder); the development resolver keeps the manifest."""
    import sys
    gen = RUNTIME.parent / "tools/gen_tables.py"
    with tempfile.TemporaryDirectory(prefix="roots-gen-") as d:
        d = Path(d)
        pc, x, empty = d / "pc", d / "x360", d / "empty"
        for p in (pc, x, empty):
            p.mkdir()
        (pc / "speed.exe").write_bytes(b"MZ")
        roots = d / "roots.txt"
        roots.write_text("pc_root=%s\nx360_root=%s\n" % (pc, x))
        code = ("import sys, runpy; sys.argv = ['gen_tables.py']; m = runpy.run_path(%r, run_name='gt'); "
                "a = type('A', (), dict(pc=None, x360=None, no_x360=False))(); print(m['resolve'](a)[1])" % str(gen))
        for public, want in (("1", empty), ("0", x)):
            e = {"PATH": os.environ["PATH"], "HOME": str(d), "NFS_ROOTS": str(roots), "NFS_X360_FILES": str(empty), "NFS_PUBLIC": public}
            out = subprocess.run([sys.executable, "-I", "-c", code], env=e, capture_output=True, text=True, check=True).stdout.strip()
            assert out == str(want), (public, out)
    print("PASS: gen_tables precedence (public: override wins; development: manifest)")



def independent_texture_presence():
    """Native folder/settings matrix: fixed 32/32; original master return fails 6/32.
    Compile the production scanner and folder gates; no renderer or game runs.
    """
    source = (RUNTIME / "x360_textures.m").read_text()
    scan = source[source.index('static void hd_scan(void) {'):source.index('// ---- validation:')]
    listing = source[source.index('static int hd_list('):source.index('// Private defaults follow')]
    harness = r"""
#include <dirent.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include "roots.h"
const char *game_dir;
enum { HD_NEW };
typedef struct { uint32_t hash; char *path; int state; } HdTex;
static HdTex *hd, *hd_tone, *hd_later;
static int nhd, nhd_tone, nhd_later, diag_on;
static void diag_event(const char *fmt, ...) { (void)fmt; }
static const char *later_root(char *out, size_t n) { (void)out; (void)n; return NULL; }
"""
    ending = r"""
int main(void) {
    if (!roots_x360_has("textures")) setenv("NFS_X360_TEXTURES", "off", 1);
    if (!roots_x360_has("ui")) setenv("NFS_X360_UI_TEXTURES", "off", 1);
    hd_scan();
    unsigned mask = 0;
    for (int i = 0; i < nhd; ++i) mask |= hd[i].hash;
    printf("%u\n", mask);
    return 0;
}
"""
    with tempfile.TemporaryDirectory(prefix="roots-extras-") as tmp:
        d = Path(tmp)
        assets = d / "assets"
        roots = d / "roots.txt"
        roots.write_text("pc_root=/missing\nassets_root=%s\n" % assets)
        failures = []
        for broken in (False, True):
            variant = scan
            if broken:
                # Reproduce the original master-Off return before the UI scan.
                line = '    int main_off = e && (!strcmp(e, "off") || !strcmp(e, "0"));'
                assert line in variant
                original = '    if (e && (!strcmp(e, "off") || !strcmp(e, "0"))) return;'
                assert hashlib.sha256(original.encode()).hexdigest() == '4b35e52d2eca081544db4fdcc06ab6addeaebe0f5c7cafecaef24527798712f3'
                variant = variant.replace(line, line + '\n' + original, 1)
            (d / "test.c").write_text(harness + listing + variant + ending)
            failed = checks = 0
            for public in (False, True):
                binary = d / "test"
                subprocess.run(["clang", "-Wall", "-Werror", "-I", str(RUNTIME), str(d / "test.c"), "-o", str(binary)] +
                               (["-DNFS_PUBLIC=1"] if public else []), check=True, capture_output=True)
                for present in range(4):
                    shutil.rmtree(assets, ignore_errors=True)
                    for bit, folder, file in ((1, "hd_textures_mips_alpha", "00000001_HD.dds"), (2, "hd_textures_ui", "00000002_UI.dds")):
                        if present & bit:
                            (assets / folder).mkdir(parents=True)
                            (assets / folder / file).write_bytes(b"synthetic scan entry")
                    for enabled in range(4):
                        env = dict(HOME=str(d), NFS_ROOTS=str(roots), NFS_X360_TEXTURES="on" if enabled & 1 else "off",
                                   NFS_X360_UI_TEXTURES="on" if enabled & 2 else "off")
                        got = int(run(binary, env))
                        failed += got != present & enabled
                        checks += 1
            failures.append(failed)
            assert failed == (6 if broken else 0), (broken, failed, checks)
        print("PASS: independent HD/menu presence 32/32; original return rejected 6/32")


if __name__ == "__main__":
    main()
    generator_precedence()
    retained_shader_presence()
    independent_texture_presence()
