"""Offline test of roots.h's X360 feature gates for the public build (NFS_PUBLIC) without a roots.txt: an explicit
NFS_X360_FILES is honoured (as tools/gen_tables.py honours it at build), no override means no X360 features, and a
roots.txt still decides when there is one. No game launch; synthetic folders only.
Run: tools/.venv/bin/python -I runtime/roots_x360_test.py"""
import os
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


if __name__ == "__main__":
    main()
    generator_precedence()
