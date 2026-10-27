// Durable file replacement helpers (audit4 #4): fsync alone only reaches the drive's cache on macOS, and a rename is
// not on disk until its directory is. Career saves, registry.txt, extra_trax.ini and app.txt use these.
#pragma once
#include <errno.h>
#include <fcntl.h>
#include <libgen.h>
#include <limits.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

// Flush a file's data to the drive (F_FULLFSYNC; plain fsync where the filesystem refuses it). 0 = ok.
static inline int durable_sync(int fd) { return fcntl(fd, F_FULLFSYNC) == 0 ? 0 : fsync(fd); }
// Cheaper: fsync only (a few ms, not ~tens), for small settings files written while the game runs.
static inline int durable_sync_fast(int fd) { return fsync(fd); }
// After a rename: flush the directory entry of `file`. Best effort (the new file is already in place).
static inline int durable_dir_sync_with(const char *file, int full) {
    char copy[PATH_MAX];
    snprintf(copy, sizeof copy, "%s", file);
    int fd = open(dirname(copy), O_RDONLY);
    if (fd < 0) return -1;
    int r = full ? durable_sync(fd) : durable_sync_fast(fd);
    close(fd);
    return r;
}
static inline int durable_dir_sync(const char *file) { return durable_dir_sync_with(file, 1); }
// Finish writing the stream `f` (staged at `tmp`) and replace `path` with it: flush, sync, close, rename, sync the
// directory (`full`: F_FULLFSYNC, else plain fsync). On any error the staged file is removed and `path` is untouched. 1 = replaced, 0 = failed (errno set).
static inline int durable_commit_with(FILE *f, const char *tmp, const char *path, int full) {
    int ok = !ferror(f) && !fflush(f) && !(full ? durable_sync(fileno(f)) : durable_sync_fast(fileno(f)));
    int err = errno;
    if (fclose(f)) { ok = 0; err = errno; }
    if (ok && !rename(tmp, path)) { durable_dir_sync_with(path, full); return 1; }
    if (ok) err = errno;
    unlink(tmp);
    errno = err;
    return 0;
}
static inline int durable_commit(FILE *f, const char *tmp, const char *path) { return durable_commit_with(f, tmp, path, 1); }
