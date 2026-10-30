"""Retained update tools use install.digest_tree's component ordering.

Proof fixture: a/b, a-b/c, a.b, a/b-c (including execute bits).
Original whole-path strcmp (rc=1 on the equality oracle):
651ca1bada00e2aab891bcae1e91aaf04605abbfc18b61b6aae457bd745d4b1a
Fixed app ToolHash and install.digest_tree (rc=0):
30fd988fa01878f258cc279d3918fe7383da1902a07b0bca02246d8bb984be73
No app, network or game is launched; offline_tests.sh discovers this test.
"""
from pathlib import Path
import runpy
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parent
LEGACY_SORT = '''[names sortUsingComparator:^NSComparisonResult(NSString *a, NSString *b) {
        int order = strcmp(a.UTF8String, b.UTF8String);
        return order < 0 ? NSOrderedAscending : order > 0 ? NSOrderedDescending : NSOrderedSame;
    }];'''
DRIVER = r'''
const char *game_dir = NULL;
int main(int argc, char **argv) { @autoreleasepool {
    if (argc != 2) return 2;
    NSString *hash = ToolHash([NSString stringWithUTF8String:argv[1]]);
    if (!hash) return 1;
    puts(hash.UTF8String);
    return 0;
} }
'''


def native_hash(work, tree, legacy=False):
    source = (ROOT / 'pub_update_launch.m').read_text()
    if legacy:
        start = source.index('[names sortUsingComparator:')
        end = source.index('\n    }];', start) + len('\n    }];')
        source = source[:start] + LEGACY_SORT + source[end:]
    driver = work / ('legacy.m' if legacy else 'current.m')
    driver.write_text(source + DRIVER)
    executable = driver.with_suffix('')
    result = subprocess.run(['clang', '-fobjc-arc', '-Wno-deprecated-declarations',
                             '-Wno-incompatible-pointer-types', '-I', str(ROOT),
                             str(driver), '-framework', 'Cocoa', '-o', str(executable)],
                            capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stderr[-4000:]
    return subprocess.run([str(executable), str(tree)], capture_output=True,
                          text=True, timeout=30, check=True).stdout.strip()


def test_toolhash_matches_install():
    digest_tree = runpy.run_path(str(ROOT.parent / 'tools/setup_kit/install.py'))['digest_tree']
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory).resolve()
        tree = work / 'tree'
        for name in ('a/b', 'a-b/c', 'a.b', 'a/b-c'):
            path = tree / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((name + '\n').encode())
            path.chmod(0o755 if name == 'a/b-c' else 0o644)
        expected = digest_tree(tree)
        original = native_hash(work, tree, legacy=True)
        actual = native_hash(work, tree)
        print(f'install={expected}\noriginal={original}\nToolHash={actual}', flush=True)
        assert expected == '30fd988fa01878f258cc279d3918fe7383da1902a07b0bca02246d8bb984be73'
        assert original == '651ca1bada00e2aab891bcae1e91aaf04605abbfc18b61b6aae457bd745d4b1a'
        assert original != expected, 'Original comparator must fail the equality oracle'
        assert actual == expected, 'App ToolHash differs from install.digest_tree'


if __name__ == '__main__':
    test_toolhash_matches_install()
    print('PUB UPDATE TOOLHASH TEST PASS')
