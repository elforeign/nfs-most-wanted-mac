// Durable updater writes without Foundation's external replacement directory.
#ifndef PUB_UPDATE_WRITE_H
#define PUB_UPDATE_WRITE_H
#import <Foundation/Foundation.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

// A supplied directory descriptor pins the validated job even if its path moves.
// Otherwise open the target's parent and keep all operations relative to it.
static inline BOOL PUReplaceFileAt(int directory, NSString *path, NSData *data, mode_t mode) {
    BOOL owned = directory < 0;
    int fd = -1, failure = 0;
    char temporary[64] = "";
    struct stat created;
    const char *name = path.lastPathComponent.fileSystemRepresentation;
    if (!data) { failure = EINVAL; goto done; }
    if (owned) directory = open(path.stringByDeletingLastPathComponent.fileSystemRepresentation,
                                O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (directory < 0) { failure = errno; goto done; }
    for (int attempt = 0; attempt < 16; ++attempt) {
        snprintf(temporary, sizeof temporary, ".pub-update-%08x%08x.tmp", arc4random(), arc4random());
        fd = openat(directory, temporary, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC,
                    mode == 0666 ? 0666 : 0600);
        if (fd >= 0) break;
        if (errno != EEXIST) { failure = errno; break; }
    }
    if (fd < 0) { if (!failure) failure = EEXIST; temporary[0] = 0; goto done; }
    // The launch failure marker used Foundation's existing mode, or 0666 filtered by umask.
    // All private state and scripts request an explicit final mode instead.
    if (mode == 0666) {
        if (fstat(fd, &created)) { failure = errno; goto done; }
        mode = created.st_mode & 0777;
        if (!fstatat(directory, name, &created, AT_SYMLINK_NOFOLLOW) && S_ISREG(created.st_mode))
            mode = created.st_mode & 0777;
    }
    {
        const char *bytes = data.bytes;
        size_t remaining = data.length;
        while (remaining) {
            ssize_t count = write(fd, bytes, remaining);
            if (count < 0 && errno == EINTR) continue;
            if (count <= 0) { failure = count < 0 ? errno : EIO; goto done; }
            bytes += count; remaining -= (size_t)count;
        }
    }
    if (fsync(fd) || fchmod(fd, mode)) { failure = errno; goto done; }
    if (close(fd)) { failure = errno; fd = -1; goto done; }
    fd = -1;
    if (renameat(directory, temporary, directory, name)) { failure = errno; goto done; }
    temporary[0] = 0;
    if (fsync(directory)) failure = errno;
done:
    if (fd >= 0) close(fd);
    if (temporary[0]) unlinkat(directory, temporary, 0);
    if (owned && directory >= 0) close(directory);
    if (failure) {
        fprintf(stderr, "update: cannot write %s: errno %d (%s)\n", path.fileSystemRepresentation,
                failure, strerror(failure));
        errno = failure;
    }
    return failure == 0;
}

static inline BOOL PUReplaceFile(NSString *path, NSData *data, mode_t mode) {
    return PUReplaceFileAt(-1, path, data, mode);
}
#endif
