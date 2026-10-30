"""Same-directory updater publication under a scratch-only sandbox.
Proof oracle: shared helper must return 0; original NSDataWritingAtomic must
return 1 with a write error under the same policy (unsandboxed both return 0).
Measured on the host: unsandboxed new=0 / old=0; scratch-only sandbox new=0 / old=1.
Inside an outer sandbox, sandbox_apply is refused (EPERM) and the test exits 3.
Six injected I/O failures must retain the previous bytes and remove the temp;
measured good=6 PASS; deliberately disabled cleanup=1 rejected.
short writes/EINTR, modes, symlink replacement and pinned directory also pass.
Exit 3 only if sandbox-exec itself cannot start; a failed write is always FAIL.
Discovered by the ordinary runtime/*_test.py offline suite.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
DRIVER = r'''
#import <Foundation/Foundation.h>
#include <errno.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>
#include <string.h>
static const char *fault;
static int calls;
static ssize_t test_write(int fd, const void *p, size_t n) {
    if (!strcmp(fault, "write")) { errno = ENOSPC; return -1; }
    if (!strcmp(fault, "zero")) return 0;
    if (!strcmp(fault, "short")) {
        if (!calls++) { errno = EINTR; return -1; }
        if (n > 2) n = 2;
    }
    return write(fd, p, n);
}
static int test_fsync(int fd) {
    if (!strcmp(fault, "fsync")) { errno = EIO; return -1; }
    return fsync(fd);
}
static int test_fchmod(int fd, mode_t mode) {
    if (!strcmp(fault, "chmod")) { errno = EPERM; return -1; }
    return fchmod(fd, mode);
}
static int test_renameat(int a, const char *b, int c, const char *d) {
    if (!strcmp(fault, "rename")) { errno = EACCES; return -1; }
    return renameat(a, b, c, d);
}
static int test_openat(int dir, const char *name, int flags, mode_t mode) {
    if (!strcmp(fault, "create")) { errno = EACCES; return -1; }
    return openat(dir, name, flags, mode);
}
#define write test_write
#define fsync test_fsync
#define fchmod test_fchmod
#define renameat test_renameat
#define openat test_openat
#include "pub_update_write.h"
WRITE_FUNCTION
int main(int argc, const char **argv) { @autoreleasepool {
    if (argc != 3) return 2;
    puts("writer-started"); fflush(stdout);
    fault = argv[2];
    NSString *root = [NSString stringWithUTF8String:argv[1]];
    NSString *path = [root stringByAppendingPathComponent:@"current.json"];
    if (!strcmp(fault, "marker"))
        return PUReplaceFile(path, [@"{}\n" dataUsingEncoding:NSUTF8StringEncoding], 0666) ? 0 : 1;
    if (!strcmp(fault, "pinned")) {
        int dir = open(root.fileSystemRepresentation, O_RDONLY | O_DIRECTORY);
        NSString *moved = [root stringByAppendingString:@".moved"];
        if (rename(root.fileSystemRepresentation, moved.fileSystemRepresentation)) return 2;
        BOOL ok = PUReplaceFileAt(dir, path, [@"pinned" dataUsingEncoding:NSUTF8StringEncoding], 0600);
        close(dir); return ok ? 0 : 1;
    }
    if (!PUWrite(path, @{@"state":@"ready"})) return 1;
    if (!strcmp(fault, "good")) {
        if (!PUWrite(path, @{@"state":@"ready"})) return 1;
        if (!PUReplaceFile([root stringByAppendingPathComponent:@"prepare.py"],
            [@"print('ready')\n" dataUsingEncoding:NSUTF8StringEncoding], 0700)) return 1;
        if (!PUReplaceFile([root stringByAppendingPathComponent:@"empty"], [NSData data], 0600)) return 1;
    }
    return 0;
} }
'''
OLD = '''static BOOL PUWrite(NSString *path, NSDictionary *value) {
    NSData *data = [NSJSONSerialization dataWithJSONObject:value options:0 error:nil];
    return data && [data writeToFile:path options:NSDataWritingAtomic error:nil] && chmod(path.fileSystemRepresentation, 0600) == 0;
}'''


def execute(args, **kwargs):
    return subprocess.run(list(map(str, args)), capture_output=True, text=True, timeout=60, **kwargs)


def build(root, name, function):
    source = root / (name + '.m')
    source.write_text(DRIVER.replace('WRITE_FUNCTION', function))
    exe = root / name
    result = execute(['clang', '-fobjc-arc', '-Wall', '-Wextra', '-Werror',
                      '-Wno-unused-function', '-I', ROOT, source, '-framework', 'Foundation', '-o', exe])
    assert result.returncode == 0, result.stderr[-4000:]
    return exe


def failure_saved(result, path):
    return (result.returncode == 1 and path.read_bytes() == b'previous'
            and list(path.parent.iterdir()) == [path]
            and str(path) in result.stderr and 'errno ' in result.stderr)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        text = (ROOT / 'pub_update.m').read_text()
        start = text.index('static BOOL PUWrite(')
        function = text[start:text.index('\n}', start) + 2]
        good = build(root, 'good', function)
        old = build(root, 'old', OLD)
        for binary in (good, old):
            box = root / ('plain-' + binary.name); box.mkdir()
            result = execute([binary, box, 'good'])
            assert result.returncode == 0, result.stderr
        print('Unsandboxed shared helper=0; original Foundation atomic=0')
        for fault in ('write', 'zero', 'fsync', 'chmod', 'rename', 'create'):
            box = root / fault; box.mkdir()
            path = box / 'current.json'; path.write_bytes(b'previous')
            result = execute([good, box, fault])
            assert failure_saved(result, path), (fault, result.stderr, list(box.iterdir()))
        mutant = root / 'mutant'; mutant.mkdir()
        header = (ROOT / 'pub_update_write.h').read_text()
        cleanup = 'if (temporary[0]) unlinkat(directory, temporary, 0);'
        assert header.count(cleanup) == 1
        (mutant / 'pub_update_write.h').write_text(header.replace(cleanup, '(void)directory;'))
        leak = build(mutant, 'leak', function)
        box = root / 'leak'; box.mkdir()
        path = box / 'current.json'; path.write_bytes(b'previous')
        assert not failure_saved(execute([leak, box, 'write']), path), 'disabled cleanup still passed'
        print('Failure cleanup: good=6 PASS; broken=1 rejected')
        box = root / 'short'; box.mkdir()
        assert execute([good, box, 'short']).returncode == 0
        target = root / 'outside'; target.write_bytes(b'untouched')
        box = root / 'link'; box.mkdir(); (box / 'current.json').symlink_to(target)
        assert execute([good, box, 'good']).returncode == 0
        assert target.read_bytes() == b'untouched' and not (box / 'current.json').is_symlink()
        assert (box / 'current.json').stat().st_mode & 0o777 == 0o600
        assert (box / 'prepare.py').stat().st_mode & 0o777 == 0o700
        assert (box / 'empty').stat().st_size == 0
        for mask in (0o022, 0o077):
            box = root / ('mask-' + str(mask)); box.mkdir()
            assert execute([good, box, 'marker'], umask=mask).returncode == 0
            assert (box / 'current.json').stat().st_mode & 0o777 == 0o666 & ~mask
            (box / 'current.json').chmod(0o640)
            assert execute([good, box, 'marker'], umask=mask).returncode == 0
            assert (box / 'current.json').stat().st_mode & 0o777 == 0o640
        box = root / 'pinned'; box.mkdir()
        assert execute([good, box, 'pinned']).returncode == 0
        assert (root / 'pinned.moved/current.json').read_bytes() == b'pinned'
        print('6 I/O failures retain old bytes/clean temp/log errno; short/EINTR, modes, links, pinned fd PASS')
        for binary in (good, old):
            box = root / ('sandbox-' + binary.name); box.mkdir()
            # Include TMPDIR in the allowed tree: Foundation still tries an external replacement directory.
            policy = '(version 1)(allow default)(deny file-write*)(allow file-write* (subpath "%s"))' % box
            env = dict(os.environ, TMPDIR=str(box) + '/')
            result = execute(['/usr/bin/sandbox-exec', '-p', policy, binary, box, 'good'], env=env)
            if 'writer-started' not in result.stdout:
                assert 'sandbox-exec:' in result.stderr and 'sandbox_apply:' in result.stderr, result.stderr
                print('SKIP: sandbox-exec cannot start: ' + result.stderr.strip())
                return 3
            assert result.returncode == (0 if binary == good else 1), (binary.name, result.returncode, result.stderr)
            assert not list(box.glob('.pub-update-*')), list(box.iterdir())
            print('Scratch-only sandbox: %s rc=%d' % (binary.name, result.returncode))
    print('PUB UPDATE WRITE TEST PASS (shared helper=0; original atomic=1)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
