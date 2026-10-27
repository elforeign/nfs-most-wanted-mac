// Host DDS bytes only. 64 MiB LRU across pack reloads; callers hold a reference
// while copying into guest memory. No guest access or GIL is needed here.
#pragma once
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#ifndef HD_FILE_CACHE_LIMIT
#define HD_FILE_CACHE_LIMIT (64u << 20)
#endif
typedef struct HdFile {
    struct HdFile *next;
    char *path;
    struct stat stamp;
    atomic_uint refs;
    uint32_t size;
    uint8_t bytes[];
} HdFile;
static HdFile *hd_files;
static size_t hd_file_bytes;
static pthread_mutex_t hd_file_lock = PTHREAD_MUTEX_INITIALIZER;

static void hd_file_release(HdFile *file) {
    if (file && atomic_fetch_sub(&file->refs, 1) == 1) {
        free(file->path);
        free(file);
    }
}
static int hd_file_same(const struct stat *a, const struct stat *b) {
    return a->st_dev == b->st_dev && a->st_ino == b->st_ino && a->st_size == b->st_size &&
           a->st_mtimespec.tv_sec == b->st_mtimespec.tv_sec && a->st_mtimespec.tv_nsec == b->st_mtimespec.tv_nsec &&
           a->st_ctimespec.tv_sec == b->st_ctimespec.tv_sec && a->st_ctimespec.tv_nsec == b->st_ctimespec.tv_nsec;
}
static void hd_file_remove(HdFile **link) {
    HdFile *file = *link;
    *link = file->next;
    hd_file_bytes -= file->size;
    hd_file_release(file); // drop cache ownership; an active reader retains its bytes
}
static HdFile *hd_file_get(const char *path) {
    int fd = open(path, O_RDONLY);
    struct stat st;
    if (fd < 0) return NULL;
    if (fstat(fd, &st) || st.st_size < 128 || st.st_size > (64 << 20)) { close(fd); return NULL; }
    pthread_mutex_lock(&hd_file_lock);
    for (HdFile **link = &hd_files; *link; link = &(*link)->next) {
        HdFile *file = *link;
        if (strcmp(file->path, path)) continue;
        if (!hd_file_same(&file->stamp, &st)) { hd_file_remove(link); break; }
        *link = file->next;
        file->next = hd_files;
        hd_files = file;
        atomic_fetch_add(&file->refs, 1);
        pthread_mutex_unlock(&hd_file_lock);
        close(fd);
        return file;
    }
    pthread_mutex_unlock(&hd_file_lock);
    HdFile *file = malloc(sizeof *file + (size_t)st.st_size);
    if (!file) { close(fd); return NULL; }
    file->path = strdup(path);
    file->size = (uint32_t)st.st_size;
    file->stamp = st;
    atomic_init(&file->refs, 1); // caller ownership
    size_t n = 0;
    while (n < file->size) {
        ssize_t got = read(fd, file->bytes + n, file->size - n);
        if (got < 0 && errno == EINTR) continue;
        if (got <= 0) break;
        n += (size_t)got;
    }
    // A file changed during the read must not populate the cache.
    struct stat after;
    int stable = !fstat(fd, &after) && hd_file_same(&st, &after);
    close(fd);
    if (n != file->size || !file->path) { hd_file_release(file); return NULL; }
    if (!stable || file->size > HD_FILE_CACHE_LIMIT) return file;
    pthread_mutex_lock(&hd_file_lock);
    // Another host reader may have populated this path while we read it.
    for (HdFile **link = &hd_files; *link;) {
        if (!strcmp((*link)->path, path)) hd_file_remove(link);
        else link = &(*link)->next;
    }
    while (hd_file_bytes + file->size > HD_FILE_CACHE_LIMIT && hd_files) {
        HdFile **last = &hd_files;
        while ((*last)->next) last = &(*last)->next;
        hd_file_remove(last);
    }
    atomic_fetch_add(&file->refs, 1); // cache ownership
    file->next = hd_files;
    hd_files = file;
    hd_file_bytes += file->size;
    pthread_mutex_unlock(&hd_file_lock);
    return file;
}
