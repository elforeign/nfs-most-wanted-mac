// Read-only, process-lifetime audio sources; each guest open has its own cursor.
#pragma once
#include <stddef.h>
#include <stdint.h>

typedef struct {
    const uint8_t *data;
    size_t size;
    int64_t pos;
    int mus;
    // Optional file-backed source (police BIG); all memory sources leave this NULL.
    size_t (*read_at)(void *source, void *dst, size_t n, size_t pos);
    void *source;
} X360MusicHandle;

int x360audio_open(const char *win, const char *host);
// Validated memory-file rebuild, independently selected mix maps and reverb. Caller frees the result.
// NULL on malformed/missing required presets; inputs are unchanged, *out_size is zero on failure.
uint8_t *x360audio_merge_memory(const uint8_t *pc, size_t pc_size, const uint8_t *x360, size_t x360_size,
                              int mix, int reverb, size_t *out_size);
int x360music_open(const char *win, X360MusicHandle *out);
size_t x360music_read(X360MusicHandle *h, void *dst, size_t n);
int64_t x360music_seek(X360MusicHandle *h, int64_t distance, unsigned method);
// Exact retail MW MPF 5.1 layout; dst must have src_size bytes. No source mutation.
int x360music_convert(const uint8_t *src, size_t src_size, uint8_t *dst, char *why, size_t why_size);
// A file below `root` by its case-insensitive relative path ("SOUND/PFDATA/MW_Music.mpf"): 1 and its host path, or 0.
int x360audio_case_find(const char *root, const char *rel, char *out, size_t n);
// Extra EA Trax (extratrax.c): when on, *out becomes the music pair (base_mpf/base_mus: the X360 pair, or NULL for the
// PC files) with the extra songs appended, and it returns 1; off (or unavailable) it returns 0 and leaves *out alone.
int extratrax_open(int is_mus, const X360MusicHandle *base_mpf, const X360MusicHandle *base_mus, X360MusicHandle *out);
