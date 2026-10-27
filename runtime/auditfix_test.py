#!/usr/bin/env python3
"""Offline regression demos for AUDIT_1023 fixes. No game, gate, profile or game-folder access.

Run: $TOOLS_PY -I runtime/auditfix_test.py
Compiles the marked production C/Objective-C helpers (Metal uses memory-backed doubles),
sources only the marked shell functions/case body, and tests deliberate broken variants.
All generated files live in a temporary directory inside this worktree and are removed.
"""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
PY = os.environ.get("TOOLS_PY") or sys.executable
NAMES = ("chase", "hood", "rain", "title", "menu")


def run(argv, expected=0, **kwargs):
    p = subprocess.run(argv, cwd=ROOT, text=True, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, **kwargs)
    if p.returncode != expected:
        raise AssertionError(f"{argv[0]}: expected exit {expected}, got {p.returncode}\n{p.stdout}")
    return p.stdout


def marked(path, begin, end):
    return (ROOT / path).read_text().split(begin, 1)[1].split("\n", 1)[1].split(end, 1)[0]


def shell(code, *args, expected=0, **kwargs):
    return run(["/bin/sh", "-c", code, "offline-test", *map(str, args)], expected, **kwargs)


def shell_tests(tmp):
    gate = marked("runtime/gate.sh", "# BEGIN offline result checks", "# END offline result checks")
    fail = 'fail() { echo "GATE FAIL: $*"; exit 1; }\n'
    smoke = tmp / "smoke.txt"
    smoke.write_text("".join(f"SMOKE {s} PASS: fixture\n" for s in ("pad", "triggers", "audio")))
    shell(fail + gate + '\ncheck_smoke_result "$1"', smoke)
    smoke.write_text(smoke.read_text().replace("triggers PASS", "triggers FAIL"))
    out = shell(fail + gate + '\ncheck_smoke_result "$1"', smoke, expected=1)
    assert "GATE FAIL: triggers:" in out
    shell(fail + gate.replace("pad triggers audio", "pad audio") + '\ncheck_smoke_result "$1"', smoke)
    print("triggers: all PASS exit 0; trigger-only FAIL exit 1; broken (ignored triggers) exit 0")

    frames = tmp / "frames.txt"
    metrics = "".join(f"{n:6s}  0.00 % of blocks changed (limit 4.0)\n" for n in NAMES)
    frames.write_text(metrics)
    shell(fail + gate + '\ncheck_refs_result "$1" "$2"', frames, 0)
    shell(fail + gate + '\ncheck_refs_result "$1" "$2"', frames, 7, expected=1)
    frames.write_text(metrics.rsplit("menu", 1)[0])
    shell(fail + gate + '\ncheck_refs_result "$1" "$2"', frames, 0, expected=1)
    # The original gate merely counted lines and never required five or checked this status here.
    shell('nr=$(grep -c "% of blocks" "$1")', frames, 7)
    print("gate refs: 5 metrics exit 0; 4 metrics / comparator exit 7 rejected (1); broken count-only exit 0")

    captures = tmp / "captures"
    captures.mkdir()
    for n in NAMES:
        shutil.copyfile(ROOT / "runtime/refs" / (n + ".jpg"), captures / (n + ".jpg"))
    args = [PY, "-I", "runtime/refs_compare.py", "check", "runtime/refs", str(captures), str(captures)]
    out = run(args)
    assert out.count("% of blocks changed") == 5 and out.count(" 0.00 %") == 5
    (captures / "chase.jpg").unlink()
    (captures / "chase.png").write_bytes(b"deliberately corrupt PNG")
    out = run(args, 1)
    assert "REFS FAIL: chase: cannot decode image" in out
    (captures / "chase.png").unlink()
    out = run(args, 1)
    assert "REFS FAIL: chase: not captured" in out
    shutil.copyfile(ROOT / "runtime/refs/chase.jpg", captures / "chase.jpg")

    comparator = marked("runtime/refs.sh", "# BEGIN offline comparator", "# END offline comparator")
    case = (ROOT / "runtime/refs.sh").read_text().split("\ncase $mode in\n", 1)[1]
    # Replace only capture with a no-op: execute the production check/retry dispatch offline.
    driver = 'set -u\nmode=check\npy="$2"\ncapture() { echo capture >> "$1/calls"; }\n' + comparator + '\ncase $mode in\n' + case
    crashpy = tmp / "crash-python"
    crashpy.write_text("#!/bin/sh\nexit 7\n")
    crashpy.chmod(0o755)
    out = shell(driver, captures, crashpy, expected=1)
    assert "REFS FAIL: comparator exit 7" in out and "REFS PASS" not in out
    old = ('py="$2"; dir="$1"; if ! "$py" -I runtime/refs_compare.py check runtime/refs "$dir" "$dir" '
           '> "$dir/refs.txt" && [ -n "${REFS_RETRY:-}" ]; then exit 1; fi\n'
           'grep -q "REFS FAIL" "$dir/refs.txt" && exit 1\necho "REFS PASS"')
    assert "REFS PASS" in shell(old, captures, crashpy)
    out = shell(driver, captures, PY)
    assert "REFS PASS" in out and out.count("% of blocks changed") == 5
    retry = tmp / "retry-python"
    retry.write_text('#!/bin/sh\nif [ ! -f "$ATTEMPT" ]; then touch "$ATTEMPT"; exit 7; fi\nexec ' + shlex.quote(PY) + ' "$@"\n')
    retry.chmod(0o755)
    env = dict(os.environ, REFS_RETRY="1", ATTEMPT=str(tmp / "attempt"))
    out = shell(driver, captures, retry, env=env)
    assert "REFS PASS" in out and out.count("% of blocks changed") == 5 and "REFS FAIL" not in out
    assert "comparator exit 7" in (captures / "first/refs.txt").read_text()
    print("refs: good copies 5 x 0.00%, corrupt PNG / missing PNG exit 1 + REFS FAIL")
    print("refs.sh: crashed comparator exit 7 -> exit 1; broken drops status -> REFS PASS (0); retry final 5 frames passes")


FILE_PREFIX = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <errno.h>
#include <dirent.h>
#include <fcntl.h>
#include <fnmatch.h>
#include <sys/stat.h>
#include <unistd.h>
#include <signal.h>
#include <sys/wait.h>
static const char *userroot;
static const char *game_dir;
static uint8_t guest[4096], *G = guest;
#define M32(a) (*(uint32_t *)(G + (a)))
#define M64(a) (*(uint64_t *)(G + (a)))
static uint32_t last_error;
static int diag_on;   // stub: the layer's BOOT_DIAG line (the test was stale without it)
static void diag_event(const char *f, ...) { (void)f; }
static const char *userdata_dir(void) { return userroot; }
static int x360audio_open(const char *w, const char *h) { return -1; }
static int fail_sync, fail_rename, short_write;
static char sync_order[32];   // S = staged file synced, R = rename, D = directory synced (audit4 #4: must come out SRD)
static void note(char ch) { size_t n = strlen(sync_order); if (n < sizeof sync_order - 1) sync_order[n] = ch; }
static int test_fsync(int fd) { if (fail_sync) { fail_sync = 0; errno = ENOSPC; return -1; } return fsync(fd); }
static int test_rename(const char *a, const char *b) { if (fail_rename) { fail_rename = 0; errno = EACCES; return -1; } note('R'); return rename(a, b); }
static int durable_sync(int fd) { note('S'); return test_fsync(fd); }
static int durable_dir_sync(const char *file) { (void)file; note('D'); return 0; }
static ssize_t test_write(int fd, const void *p, size_t n) { if (short_write) { short_write = 0; return write(fd, p, n / 2); } return write(fd, p, n); }
#define fsync test_fsync
#define rename test_rename
#define write test_write
#define HANDLE_BASE 0x1000u
'''

FILE_TEST = r'''
#undef fsync
#undef rename
#undef write
static unsigned checks;
#define CHECK(x) do { ++checks; if (!(x)) { fprintf(stderr, "file check %u failed at line %d: %s\n", checks, __LINE__, #x); return 2; } } while (0)
static int contents(const char *path, const char *expect) {
    char buf[1024]; int fd = open(path, O_RDONLY); if (fd < 0) return 0;
    ssize_t n = read(fd, buf, sizeof buf); close(fd);
    return n == (ssize_t)strlen(expect) && !memcmp(buf, expect, (size_t)n);
}
static void seed(const char *path) { int fd = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0644); if (fd < 0 || write(fd, "OLD-SAVE", 8) != 8) exit(3); close(fd); }
static uint32_t op(const char *p, uint32_t a, uint32_t sh, uint32_t d) { return file_open(p, p, a, sh, d, 0); }
static int listed(const char *pattern, const char *name) {
    uint32_t h = find_first(pattern, 0);
    int k = find_handle(h), found = 0;
    if (k < 0) return 0;
    do { if (!name || !strcmp((char *)G + 44, name)) ++found; } while (find_next((uint32_t)k, 0));
    closedir(finds[k].d); finds[k].d = NULL;
    return found;
}
int main(int argc, char **argv) {
    userroot = argv[1]; mkdir(userroot, 0755);
    if (argc > 2) { save_cleanup(); return 0; }
    game_dir = userroot;
    char path[4096], other[4096], missing[4096], buf[32];
    snprintf(path, sizeof path, "%s/profile", userroot);
    snprintf(other, sizeof other, "%s-other", userroot);
    snprintf(missing, sizeof missing, "%s/new-profile", userroot);
    seed(path);
    int ready[2]; CHECK(!pipe(ready));
    pid_t child = fork(); CHECK(child >= 0);
    if (!child) {
        close(ready[0]); uint32_t h = op(path, 0xc0000000, 0, 2);
        if (h == UINT32_MAX || file_write(h, "NEW", 3) != 3 || !file_flush(h)) _exit(4);
        write(ready[1], "R", 1); for (;;) pause();
    }
    close(ready[1]); CHECK(read(ready[0], buf, 1) == 1); close(ready[0]);
    CHECK(!kill(child, SIGKILL)); int status; CHECK(waitpid(child, &status, 0) == child);
    CHECK(WIFSIGNALED(status) && WTERMSIG(status) == SIGKILL);
    CHECK(contents(path, "OLD-SAVE"));   // deliberate in-place variant fails here: 3 bytes NEW, old 8 lost
    uint32_t h = op(path, 0xc0000000, 0, 2); CHECK(h != UINT32_MAX);
    // The killed child's orphan and this open writer's stage both exist, but neither is a guest scan result.
    const char *stage = strrchr(file_info[(h - HANDLE_BASE) / 4].save->temp, '/') + 1;
    char stage_pattern[1024]; snprintf(stage_pattern, sizeof stage_pattern, "USERDATA\\%s", stage);
    CHECK(!listed("USERDATA\\*.nfs-save-??????", NULL));
    CHECK(!listed("USERDATA\\*", stage) && !listed("USERDATA\\*.*", stage));
    CHECK(!listed(stage_pattern, stage));
    CHECK(listed("USERDATA\\*", "profile") == 1);
    CHECK(file_write(h, "NEW-SAVE-DATA", 13) == 13);
    CHECK(lseek(handle_fd(h), 0, SEEK_SET) == 0);
    CHECK(read(handle_fd(h), buf, 13) == 13 && !memcmp(buf, "NEW-SAVE-DATA", 13));
    struct stat st; CHECK(!fstat(handle_fd(h), &st) && st.st_size == 13);
    CHECK(file_flush(h) && contents(path, "OLD-SAVE"));
    CHECK(lseek(handle_fd(h), 8, SEEK_SET) == 8 && file_end(h));
    CHECK(!fstat(handle_fd(h), &st) && st.st_size == 8);
    CHECK(file_write(h, "-DATA", 5) == 5 && file_close(h));
    CHECK(contents(path, "NEW-SAVE-DATA"));
    h = op(path, 0xc0000000, 0, 3); CHECK(h != UINT32_MAX);
    CHECK(lseek(handle_fd(h), 0, SEEK_END) == 13 && file_write(h, "+", 1) == 1 && file_close(h));
    CHECK(contents(path, "NEW-SAVE-DATA+"));

    // Reader opened before a shareable writer follows its stage; each handle keeps its own seek position.
    uint32_t r = op(path, 0x80000000, 3, 3); CHECK(r != UINT32_MAX);
    CHECK(lseek(handle_fd(r), 1, SEEK_SET) == 1);
    h = op(path, 0xc0000000, 3, 3); CHECK(h != UINT32_MAX);
    CHECK(file_write(h, "SHR", 3) == 3);
    CHECK(read(handle_fd(r), buf, 2) == 2 && !memcmp(buf, "HR", 2));
    uint32_t r2 = op(path, 0x80000000, 3, 3); CHECK(r2 != UINT32_MAX);
    CHECK(read(handle_fd(r2), buf, 3) == 3 && !memcmp(buf, "SHR", 3));
    CHECK(contents(path, "NEW-SAVE-DATA+"));
    CHECK(file_close(h) && contents(path, "SHR-SAVE-DATA+"));
    CHECK(lseek(handle_fd(r), 0, SEEK_SET) == 0 && read(handle_fd(r), buf, 3) == 3 && !memcmp(buf, "SHR", 3));
    CHECK(file_close(r) && file_close(r2));

    h = op(path, 0xc0000000, 3, 2); CHECK(h != UINT32_MAX && file_write(h, "A", 1) == 1);
    uint32_t w2 = op(path, 0xc0000000, 3, 3); CHECK(w2 != UINT32_MAX);
    CHECK(lseek(handle_fd(w2), 1, SEEK_SET) == 1 && file_write(w2, "B", 1) == 1);
    CHECK(file_close(h) && contents(path, "SHR-SAVE-DATA+"));
    CHECK(file_close(w2) && contents(path, "AB"));
    h = op(path, 0xc0000000, 0, 3); CHECK(h != UINT32_MAX);
    CHECK(op(path, 0x80000000, 3, 3) == UINT32_MAX && last_error == 32);
    CHECK(file_close(h));
    CHECK(op(path, 0xc0000000, 0, 1) == UINT32_MAX && contents(path, "AB"));
    CHECK(op(missing, 0xc0000000, 0, 3) == UINT32_MAX);
    CHECK(op(missing, 0xc0000000, 0, 5) == UINT32_MAX);
    h = op(missing, 0xc0000000, 0, 1); CHECK(h != UINT32_MAX && access(missing, F_OK));
    CHECK(file_write(h, "C", 1) == 1 && file_close(h) && contents(missing, "C"));
    h = op(missing, 0xc0000000, 0, 4); CHECK(h != UINT32_MAX);
    CHECK(lseek(handle_fd(h), 0, SEEK_END) == 1 && file_write(h, "D", 1) == 1 && file_close(h) && contents(missing, "CD"));
    h = op(missing, 0xc0000000, 0, 5); CHECK(h != UINT32_MAX && file_close(h) && contents(missing, ""));

    // Actual career save shape (007f4f11): CRT __open(0x8301), 28-byte header, payload, __close.
    char header[28] = "MC02", saved[35];
    h = op(path, 0x40000000, 3, 2); CHECK(h != UINT32_MAX);
    CHECK(file_write(h, header, sizeof header) == sizeof header && file_write(h, "PAYLOAD", 7) == 7);
    CHECK(read(handle_fd(h), buf, 1) == -1 && errno == EBADF); // write-only remains write-only
    CHECK(file_close(h));
    r = op(path, 0x80000000, 3, 3); CHECK(r != UINT32_MAX);
    CHECK(!fstat(handle_fd(r), &st) && st.st_size == sizeof saved);
    CHECK(read(handle_fd(r), saved, sizeof saved) == sizeof saved && !memcmp(saved, header, 28) && !memcmp(saved + 28, "PAYLOAD", 7));
    CHECK(file_close(r));

    // Failed flush, close-time fsync, rename and short write must all preserve the previous file.
    for (int which = 0; which < 4; ++which) {
        seed(path); h = op(path, 0xc0000000, 0, 2); CHECK(h != UINT32_MAX);
        if (which == 3) short_write = 1;
        ssize_t n = file_write(h, "REPLACEMENT", 11); CHECK(n == (which == 3 ? 5 : 11));
        if (which == 0) { fail_sync = 1; CHECK(!file_flush(h)); }
        if (which == 1) fail_sync = 1;
        if (which == 2) fail_rename = 1;
        CHECK(!file_close(h)); CHECK(contents(path, "OLD-SAVE"));
    }
    CHECK(!file_flush(UINT32_MAX) && last_error == 6);
    CHECK(!file_end(UINT32_MAX) && last_error == 6);
    seed(other); h = op(other, 0xc0000000, 0, 2); CHECK(h != UINT32_MAX);
    CHECK(file_write(h, "RAW", 3) == 3 && contents(other, "RAW") && file_close(h));
    uint32_t readers[255];
    for (int i = 0; i < 255; ++i) { readers[i] = op(path, 0x80000000, 3, 3); CHECK(readers[i] != UINT32_MAX); }
    CHECK(op(path, 0xc0000000, 3, 2) == UINT32_MAX && last_error == 4 && contents(path, "OLD-SAVE"));
    for (int i = 0; i < 255; ++i) CHECK(file_close(readers[i]));
    printf("file layer: %u checks PASS; SIGKILL before close preserves 8 old bytes, close installs 13 new bytes\n", checks);
    return 0;
}
'''


RING_PREFIX = r'''
#import <Foundation/Foundation.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
@protocol MTLBuffer
- (void *)contents;
- (NSUInteger)length;
@end
@protocol MTLCommandBuffer @end
enum { MTLResourceStorageModeShared = 0 };
static unsigned allocations;
static int fail_buffer;
@interface TestBuffer : NSObject <MTLBuffer> { void *bytes; NSUInteger size; }
- (id)initWithLength:(NSUInteger)n;
@end
@implementation TestBuffer
- (id)initWithLength:(NSUInteger)n { if ((self = [super init])) { size = n; bytes = calloc(1, n); } return self; }
- (void *)contents { return bytes; }
- (NSUInteger)length { return size; }
- (void)dealloc { free(bytes); }
@end
@interface TestDevice : NSObject
- (id<MTLBuffer>)newBufferWithLength:(NSUInteger)n options:(NSUInteger)o;
@end
@implementation TestDevice
- (id<MTLBuffer>)newBufferWithLength:(NSUInteger)n options:(NSUInteger)o { ++allocations; if (fail_buffer) { fail_buffer = 0; return nil; } return [[TestBuffer alloc] initWithLength:n]; }
@end
@interface TestCommand : NSObject { @public NSMutableArray *handlers; }
- (void)addCompletedHandler:(void (^)(id<MTLCommandBuffer>))block;
- (void)complete;
@end
@implementation TestCommand
- (id)init { if ((self = [super init])) handlers = [NSMutableArray new]; return self; }
- (void)addCompletedHandler:(void (^)(id<MTLCommandBuffer>))block { [handlers addObject:[block copy]]; }
- (void)complete { for (void (^b)(id<MTLCommandBuffer>) in handlers) b(nil); [handlers removeAllObjects]; }
@end
@interface TestEncoder : NSObject { @public id<MTLBuffer> vertex, fragment; NSUInteger voff, foff; }
- (void)setVertexBuffer:(id<MTLBuffer>)b offset:(NSUInteger)o atIndex:(NSUInteger)i;
- (void)setFragmentBuffer:(id<MTLBuffer>)b offset:(NSUInteger)o atIndex:(NSUInteger)i;
- (void)setVertexBufferOffset:(NSUInteger)o atIndex:(NSUInteger)i;
- (void)setFragmentBufferOffset:(NSUInteger)o atIndex:(NSUInteger)i;
@end
@implementation TestEncoder
- (void)setVertexBuffer:(id<MTLBuffer>)b offset:(NSUInteger)o atIndex:(NSUInteger)i { vertex = b; voff = o; }
- (void)setFragmentBuffer:(id<MTLBuffer>)b offset:(NSUInteger)o atIndex:(NSUInteger)i { fragment = b; foff = o; }
- (void)setVertexBufferOffset:(NSUInteger)o atIndex:(NSUInteger)i { voff = o; }
- (void)setFragmentBufferOffset:(NSUInteger)o atIndex:(NSUInteger)i { foff = o; }
@end
static id<MTLBuffer> ring[3];
static uint32_t ring_idx, ring_off, ring_size = 512;
static uint64_t frame_serial = 1;
static TestDevice *mtl;
static TestCommand *cmd;
static TestEncoder *enc;
static struct { void *cbuf[2]; uint32_t coff[2]; } eb;
static void ensure_cmd(void) { if (!cmd) cmd = [TestCommand new]; }
'''

RING_TEST = r'''
static unsigned checks;
#define CHECK(x) do { ++checks; if (!(x)) { fprintf(stderr, "ring check %u failed at line %d: %s\n", checks, __LINE__, #x); return 2; } } while (0)
int main(void) { @autoreleasepool {
    mtl = [TestDevice new]; cmd = [TestCommand new]; enc = [TestEncoder new];
    ring[0] = [mtl newBufferWithLength:512 options:0];
    uint32_t off; __unsafe_unretained id<MTLBuffer> b;
    void *p = ring_alloc(1, &off, &b); CHECK(p && off == 0 && b == ring[0]); memset(p, 0x11, 256);
    p = ring_alloc(255, &off, &b); CHECK(p && off == 256 && b == ring[0]); memset(p, 0x22, 256);
    CHECK(allocations == 1 && cmd->handlers.count == 0 && ring_off == 512);
    __weak id<MTLBuffer> weak_spill;
    @autoreleasepool {
        __unsafe_unretained id<MTLBuffer> spill;
        p = ring_alloc(256, &off, &spill); CHECK(p); memset(p, 0x33, 256);
        CHECK(((uint8_t *)ring[0].contents)[0] == 0x11 && ((uint8_t *)ring[0].contents)[256] == 0x22);
        CHECK(spill != ring[0] && off == 0 && ring_off == 512);
        weak_spill = spill; spill = nil;
    }
    CHECK(weak_spill != nil); // completion handler owns it even before/without a Metal encoder binding
    [cmd complete]; CHECK(weak_spill == nil);
    @autoreleasepool {
        __unsafe_unretained id<MTLBuffer> oversized;
        p = ring_alloc(768, &off, &oversized); CHECK(p && oversized.length >= 768 && off == 0);
        memset(p, 0x44, 768); CHECK(((uint8_t *)ring[0].contents)[0] == 0x11 && ring_off == 512);
    }
    CHECK(cmd->handlers.count == 1); [cmd complete];
    frame_serial = 2;
    float constants[64]; for (int i = 0; i < 64; ++i) constants[i] = (float)i;
    CHECK(bind_floats(0, constants, 1, 16, 16));
    CHECK(enc->vertex != ring[0] && !memcmp(enc->vertex.contents, constants, sizeof constants));
    id<MTLBuffer> cached = enc->vertex;
    CHECK(bind_floats(1, constants, 1, 16, 16));
    CHECK(enc->fragment != ring[0] && enc->fragment != cached && !memcmp(enc->fragment.contents, constants, sizeof constants));
    unsigned count = allocations;
    [cmd complete]; // diagnostic draw captures can complete a command buffer in the middle of a frame
    memset(&eb, 0, sizeof eb); enc = [TestEncoder new];
    CHECK(bind_floats(0, constants, 1, 16, 16));
    CHECK(enc->vertex == cached && allocations == count); // cached spilled constants must keep their own buffer
    frame_serial = 3; ring_idx = 1; ring_off = 0;
    ring[1] = [mtl newBufferWithLength:512 options:0];
    CHECK(bind_floats(0, constants, 1, 16, 16) && enc->vertex == ring[1]);
    CHECK(bind_floats(1, constants, 1, 16, 16) && enc->fragment == ring[1] && enc->foff == 256);
    CHECK(ring_off == 512);
    frame_serial = 4; fail_buffer = 1;
    CHECK(!bind_floats(0, constants, 2, 16, 16));
    [cmd complete];
    printf("upload ring: %u checks PASS; 512-byte cap, 256/768-byte spills, retained until completion, cached bindings intact\n", checks);
    return 0;
} }
'''


def cleanup_tests(tmp, exe):
    tmp.mkdir()
    root, outside = tmp / "userdata", tmp / "outside"
    root.mkdir()
    outside.mkdir()
    nested = root / "NFS Most Wanted" / "Alias"
    nested.mkdir(parents=True)
    directory = root / "dir.nfs-save-ABC123"
    directory.mkdir()
    stale = root / "x.nfs-save-ABC123"
    removed = (stale, nested / "Alias.nfs-save-ab_!09", directory / ".nfs-save-123456")
    kept = (root / "x.nfs-save-1", root / "x.nfs-save-12345", root / "x.nfs-save-1234567",
            root / "x.nfs-save-123456.bak", root / "x.NFS-save-123456", nested / "Alias",
            outside / "x.nfs-save-ABC123")
    for p in (*removed, *kept):
        p.write_bytes(b"SCRATCH-SAVE")
    links = (root / "file.nfs-save-ABC123", root / "outside-link", root / "cycle",
             root / "dangling.nfs-save-ABC123")
    links[0].symlink_to(kept[-1])
    links[1].symlink_to(outside, target_is_directory=True)
    links[2].symlink_to(root, target_is_directory=True)
    links[3].symlink_to(outside / "missing")
    fifo = root / "pipe.nfs-save-ABC123"
    os.mkfifo(fifo)

    # A symlink anywhere in the supplied userdata path (including a trailing slash) must not be traversed.
    alias = tmp / "userdata-link"
    alias.symlink_to(root, target_is_directory=True)
    for path in (alias, str(alias) + "/", alias / "NFS Most Wanted" / "Alias"):
        out = run([str(exe), str(path), "cleanup-only"])
        assert all(p.is_file() for p in removed), "symlink userdata root traversed"
        assert "removed stale save temp" not in out

    out = run([str(exe), str(root), "cleanup-only"])
    assert not stale.exists(), "stale save temp retained"
    assert all(not p.exists() for p in removed), "nested stale save temp retained"
    assert all(p.read_bytes() == b"SCRATCH-SAVE" for p in kept), "look-alike or outside file changed"
    assert directory.is_dir() and fifo.is_fifo(), "non-regular entry removed"
    assert all(p.is_symlink() for p in links), "symlink removed"
    lines = out.splitlines()
    assert sorted(lines) == sorted(f"boot: removed stale save temp {p}" for p in removed), "removal log mismatch"
    assert not run([str(exe), str(root), "cleanup-only"]), "cleanup not idempotent"


def native_tests(tmp):
    scan = marked("runtime/boot.c", "// BEGIN offline save scan", "// END offline save scan")
    layer = marked("runtime/boot.c", "// BEGIN offline file layer", "// END offline file layer")
    file_source = FILE_PREFIX + scan + layer + FILE_TEST
    startup = (ROOT / "runtime/boot.c").read_text().split("int main(int argc, char **argv)", 1)[1]
    assert startup.index("report_init();") < startup.index("save_cleanup();") < startup.index("dispatch(c, speed_entry);")
    c = tmp / "file_test.c"
    c.write_text(file_source)
    exe = tmp / "file_test"
    run(["clang", "-Wall", "-Wextra", "-Wno-unused-parameter", "-O1", str(c), "-o", str(exe)])
    cleanup_tests(tmp / "cleanup", exe)
    print("save cleanup: 3 exact-suffix regular files removed/logged recursively; 7 ordinary/look-alike/outside files, directory, FIFO and 4 symlinks kept; symlink userdata paths skipped; second pass empty")
    print(run([str(exe), str(tmp / "userdata")]).strip())
    c.write_text(file_source.replace("save_cleanup_dir(fd, root);", "close(fd);", 1))
    run(["clang", "-O1", str(c), "-o", str(exe)])
    try:
        cleanup_tests(tmp / "broken-cleanup", exe)
    except AssertionError as e:
        assert str(e) == "stale save temp retained"
    else:
        raise AssertionError("disabled cleanup unexpectedly passed")
    c.write_text(file_source.replace("if (file_is_save(p) && save_temp_name(e->d_name)) continue;", "/* broken: expose stages */", 1))
    run(["clang", "-O1", str(c), "-o", str(exe)])
    out = run([str(exe), str(tmp / "broken-scan")], 2)
    assert '!listed("USERDATA\\\\*.nfs-save-??????", NULL)' in out
    print("save cleanup broken (disabled): stale-file check fails; scan broken (unfiltered): open/orphan stage enumeration fails (2)")
    c.write_text(file_source.replace("if (file_is_save(host))", "if (0 && file_is_save(host))", 1))
    run(["clang", "-O1", str(c), "-o", str(exe)])
    out = run([str(exe), str(tmp / "broken-userdata")], 2)
    assert 'contents(path, "OLD-SAVE")' in out
    print("atomic-save broken (in-place): SIGKILL leaves 3 bytes NEW instead of 8 bytes OLD-SAVE; preservation check fails (2)")

    allocator = marked("runtime/render.m", "// BEGIN offline upload test", "// END offline upload test")
    render = (ROOT / "runtime/render.m").read_text()
    # bind_floats alone, to its closing brace (what follows it in render.m changes with every feature and needs real Metal types)
    at = render.index("static int bind_floats(")
    depth, end = 0, render.index("{", at)
    while True:
        depth += (render[end] == "{") - (render[end] == "}")
        end += 1
        if depth == 0: break
    constants = render[at:end] + "\n"
    source = RING_PREFIX + allocator + constants + RING_TEST
    objc = tmp / "ring_test.m"
    exe = tmp / "ring_test"

    def ring_run(code, expected):
        objc.write_text(code)
        run(["clang", "-fobjc-arc", "-O1", str(objc), "-framework", "Foundation", "-o", str(exe)])
        return run([str(exe)], expected)

    out = ring_run(source, 0)
    assert out.count("upload ring full this frame") == 3  # frames 1, 2, 4; only once despite multiple spills
    print(out.splitlines()[-1] + "; 3 warnings in 3 overflow frames")
    start = allocator.index("    if (n > ring_size")
    end = allocator.index("    *off = ring_off;", start)
    broken = allocator[:start] + "    if (n > ring_size || ring_off > ring_size - n) ring_off = 0;\n" + allocator[end:]
    out = ring_run(RING_PREFIX + broken + constants + RING_TEST, 2)
    assert "0x11" in out
    out = ring_run(source.replace("id<MTLBuffer> b = buffers[st];", "id<MTLBuffer> b = ring[ring_idx];"), 2)
    assert "enc->vertex != ring[0]" in out
    print("upload ring broken wrap: sentinel overwritten (2); broken constant-buffer binding: spill bound as ring (2)")


if __name__ == "__main__":
    if sys.executable != PY:
        raise SystemExit(f"Use {PY} -I runtime/auditfix_test.py")
    with tempfile.TemporaryDirectory(prefix=".auditfix-", dir=ROOT) as d:
        tmp = Path(d)
        os.environ["TMPDIR"] = str(tmp)
        os.environ.pop("REFS_RETRY", None)
        shell_tests(tmp)
        native_tests(tmp)
    print("OFFLINE AUDITFIX PASS (production helpers + deliberate broken variants; no game/gate run)")
