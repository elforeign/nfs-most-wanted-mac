"""M4 F7: in the public build (NFS_PUBLIC) code must not reach outside the repository / install for the owner's
sibling folders (<game>/../x360, ../assets_audio, <checkout>/../decomp, ../render_diff). The owner build (no
NFS_PUBLIC) keeps its legacy lookups. On main 20c0254 the public cases found the sibling folders (fails).
Run with the project's Python -I.
"""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
DRIVER = r'''
#include <stdio.h>
#include "roots.h"
const char *game_dir;
int main(int argc, char **argv) {
    game_dir = argv[1];
    char m[2048], a[2048];
    const char *movies = roots_movies_dir(m, sizeof m);
    printf("files=%s\nasset=%s\nmovies=%s\nworld=%d\nactive=%d\n", roots_x360_files(),
           roots_asset_dir("nis_arrest", a, sizeof a), movies ? movies : "(none)", roots_x360_has("world"), roots_active());
    return 0;
}
'''


class PublicPaths(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name).resolve()
        for rel in ("game/speed.exe", "x360/files/GLOBAL/GLOBALB.BUN", "x360/Movies/a.wmv", "x360/nis_arrest/a"):
            (self.d / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.d / rel).write_bytes(b"x")
        (self.d / "driver.c").write_text(DRIVER)

    def tearDown(self):
        self.tmp.cleanup()

    def run_roots(self, public):
        exe = self.d / ("pub" if public else "owner")
        subprocess.run(["clang", "-std=c11", "-w", "-I", ROOT / "runtime", "-I", ROOT / "recomp"] +
                       (["-DNFS_PUBLIC=1"] if public else []) + [self.d / "driver.c", "-o", exe], check=True)
        env = {"PATH": "/usr/bin:/bin", "HOME": str(self.d / "nohome")}   # no roots.txt anywhere
        out = subprocess.run([exe, self.d / "game"], env=env, cwd=self.d / "game", capture_output=True, text=True, check=True)
        return dict(l.split("=", 1) for l in out.stdout.splitlines())

    def test_public_build_never_uses_sibling_x360_folders(self):
        got = self.run_roots(True)
        self.assertNotIn(str(self.d / "x360"), "".join(got.values()), got)
        self.assertNotIn("..", got["files"] + got["asset"] + got["movies"], got)
        self.assertEqual((got["world"], got["movies"]), ("0", "(none)"))

    def test_owner_build_keeps_legacy_layout(self):
        got = self.run_roots(False)
        self.assertEqual(got["files"], str(self.d / "game/../x360/files"))
        self.assertEqual(got["movies"], str(self.d / "game/../x360/Movies"))
        self.assertEqual((got["world"], got["active"]), ("1", "0"))

    def preprocess(self, path, public):
        return subprocess.run(["clang", "-E", "-P", "-w", "-I", ROOT / "runtime", "-I", ROOT / "recomp", "-I", ROOT / "third_party/ffmpeg/include"]
                              + (["-DNFS_PUBLIC=1"] if public else []) + [ROOT / path], capture_output=True, text=True).stdout

    def test_diag_symbols_never_read_decomp_in_public(self):
        public, owner = self.preprocess("runtime/diag.c", True), self.preprocess("runtime/diag.c", False)
        self.assertTrue("load_symbols_once" in public, "diag.c did not preprocess")
        self.assertTrue("../../decomp/functions.tsv" in owner)
        self.assertFalse("/../../decomp/" in public, "the public diag.c reads <checkout>/../decomp")

    def test_shader_build_reference_only_outside_public(self):
        # host/shaders/build.sh (full mode): the optional ../../../render_diff reference is read only without NFS_PUBLIC.
        text = (ROOT / "host/shaders/build.sh").read_text()
        uses = [l for l in text.splitlines() if '"$R"' in l and not l.lstrip().startswith("#")]
        self.assertTrue(uses)
        for line in uses:
            if line.lstrip().startswith(("if ", "elif ")):
                self.assertIn('"${NFS_PUBLIC:-0}" != 1', line, line)


if __name__ == "__main__":
    unittest.main(verbosity=2)
