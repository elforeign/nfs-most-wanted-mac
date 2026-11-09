"""Memory-backed guest and file fault controls for native save checks."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def marked(path, begin, end):
    return (ROOT / path).read_text().split(begin, 1)[1].split('\n', 1)[1].split(end, 1)[0]


FILE_PREFIX = r'''
#define SAVE_TIME(stage, expression) (expression)
#define SAVE_VOID(stage, expression) do { expression; } while (0)
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
__attribute__((weak)) void underglow_save_complete(int committed) { (void)committed; }
static const char *userroot;
static const char *game_dir;
static uint8_t guest[4096], *G = guest;
#define M32(a) (*(uint32_t *)(G + (a)))
#define M64(a) (*(uint64_t *)(G + (a)))
static uint32_t last_error;
static int diag_on;
static void diag_event(const char *f, ...) { (void)f; }
static const char *userdata_dir(void) { return userroot; }
static int x360audio_open(const char *w, const char *h) { return -1; }
static int fail_sync, fail_rename, short_write;
static char sync_order[32];
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
