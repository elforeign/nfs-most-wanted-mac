// A personal export's manifest belongs to the executable's bundle, independent
// of the current directory, profile and any previous app location.
#pragma once
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#ifdef __APPLE__
#include <mach-o/dyld.h>
#include <limits.h>
#endif

#define BUNDLE_UPDATE_MESSAGE "Re-export from Setup to update. Keep the previous export to roll back."
// Signed executables advertise support; Setup must not export older runtimes.
static const char bundle_roots_capability[] __attribute__((used)) = "NFS_BUNDLE_ROOTS_V1";

static inline const char *bundle_roots_file(void) {
    static char path[4096];
    static int loaded;
    if (!loaded) {
        loaded = 1;
#ifdef __APPLE__
        char executable[PATH_MAX], canonical[PATH_MAX];
        uint32_t size = sizeof executable;
        if (!_NSGetExecutablePath(executable, &size) && realpath(executable, canonical)) {
            char *name = strrchr(canonical, '/');
            if (name) {
                *name = 0;
                size_t length = strlen(canonical);
                const char *suffix = "/Contents/MacOS";
                size_t tail = strlen(suffix);
                if (length >= tail && !strcmp(canonical + length - tail, suffix)) {
                    canonical[length - tail] = 0;
                    int n = snprintf(path, sizeof path, "%s/Contents/Resources/AllInOne/roots.txt", canonical);
                    struct stat st;
                    char identity[4096];
                    int m = snprintf(identity, sizeof identity, "%s/Contents/Resources/AllInOne.export", canonical);
                    // Identity survives loss of the asset directory. Keep the
                    // manifest path even when missing, so startup rejects the
                    // damaged export and update restrictions remain in force.
                    if (n < 0 || (size_t)n >= sizeof path ||
                        (lstat(path, &st) && (m < 0 || (size_t)m >= sizeof identity || lstat(identity, &st)))) path[0] = 0;
                }
            }
        }
#endif
    }
    return path[0] ? path : NULL;
}

static inline int roots_exported(void) { return bundle_roots_file() != NULL; }

static inline int bundle_root_value(const char *value, char *out, size_t size) {
    const char *manifest = bundle_roots_file();
    if (!manifest || !*value || value[0] == '/') return 0;
    // Accept only relative components; even a damaged export cannot fall back
    // to a stale external install or write a cache into its signed resources.
    for (const char *p = value; *p;) {
        const char *end = strchr(p, '/');
        size_t n = end ? (size_t)(end - p) : strlen(p);
        if (!n || (n == 1 && *p == '.') || (n == 2 && p[0] == '.' && p[1] == '.')) return 0;
        p += n;
        if (*p) ++p;
    }
    const char *slash = strrchr(manifest, '/');
    int n = snprintf(out, size, "%.*s/%s", (int)(slash - manifest), manifest, value);
    return n > 0 && (size_t)n < size;
}
