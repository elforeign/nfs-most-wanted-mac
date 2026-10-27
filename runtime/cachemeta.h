// Cache metadata: xattrs where supported, atomic checksum sidecars otherwise. Payload formats stay unchanged.
#pragma once
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/xattr.h>
#include <unistd.h>
#include <CommonCrypto/CommonDigest.h>

static int cache_meta_write(const char *path, const char *suffix, const void *data, size_t size) {
    char dest[4352], tmp[4400];
    if (snprintf(dest, sizeof dest, "%s%s", path, suffix) >= (int)sizeof dest) return 0;
    snprintf(tmp, sizeof tmp, "%s.XXXXXX", dest);
    int fd = mkstemp(tmp);
    if (fd < 0) return 0;
    int ok = write(fd, data, size) == (ssize_t)size;
    if (close(fd)) ok = 0;
    if (ok && rename(tmp, dest)) ok = 0;
    if (!ok) unlink(tmp);
    return ok;
}
static int cache_meta_read(const char *path, const char *suffix, void *data, size_t size) {
    char name[4352];
    if (snprintf(name, sizeof name, "%s%s", path, suffix) >= (int)sizeof name) return 0;
    int fd = open(name, O_RDONLY);
    if (fd < 0) return 0;
    struct stat st;
    int ok = !fstat(fd, &st) && st.st_size == (off_t)size && read(fd, data, size) == (ssize_t)size;
    close(fd);
    if (!ok) errno = EINVAL;
    return ok;
}
#define CACHE_DIGEST "com.nfsmw.converter.sha256.v1"
static int cache_digest(int fd, const char *path, const void *data, size_t size, int writing) {
    unsigned char hash[CC_SHA256_DIGEST_LENGTH], stored[CC_SHA256_DIGEST_LENGTH];
    if (size > UINT32_MAX || !CC_SHA256(data, (CC_LONG)size, hash)) return 0;
    if (writing) {
        if (!fsetxattr(fd, CACHE_DIGEST, hash, sizeof hash, 0, 0)) return 1;
        if (errno != ENOTSUP) return 0;
        return cache_meta_write(path, ".sha256", hash, sizeof hash);
    }
    ssize_t n = fgetxattr(fd, CACHE_DIGEST, stored, sizeof stored, 0, 0);
    if (n < 0 && (errno == ENOTSUP || errno == ENOATTR)) {
        if (!cache_meta_read(path, ".sha256", stored, sizeof stored)) return 0;
    } else if (n != sizeof stored) return 0;
    return !memcmp(hash, stored, sizeof hash);
}

// Source identity is separate from the payload hash. Existing verified caches acquire it once, without conversion.
// Size + nanosecond mtime catches source repairs; unreadable decodes are never published to disk.
static int cache_source(const char *cache, const char *source, const void *data, size_t size, int writing) {
    struct stat st;
    if (stat(source, &st)) return 0;
    unsigned char hash[CC_SHA256_DIGEST_LENGTH];
    if (size > UINT32_MAX || !CC_SHA256(data, (CC_LONG)size, hash)) return 0;
    char identity[192] = {0}, stored[192];
    snprintf(identity, sizeof identity, "%lld:%lld:%ld:", (long long)st.st_size,
             (long long)st.st_mtimespec.tv_sec, st.st_mtimespec.tv_nsec);
    size_t len = strlen(identity);
    for (size_t i = 0; i < sizeof hash; ++i) snprintf(identity + len + 2 * i, 3, "%02x", hash[i]);
    if (writing) return cache_meta_write(cache, ".source", identity, sizeof identity);
    if (!cache_meta_read(cache, ".source", stored, sizeof stored)) {
        // Only absent metadata is legacy. Corrupt/unreadable metadata requires regeneration.
        if (errno != ENOENT) return 0;
        return cache_meta_write(cache, ".source", identity, sizeof identity);
    }
    return !memcmp(identity, stored, sizeof identity);
}
