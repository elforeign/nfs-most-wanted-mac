#pragma once
#include "x360audio.h"
#include <string.h>

// Shared by the X360 EA-audio adapters (cop speech, NIS audio): byte helpers and the sparse BIG overlay.
static inline uint32_t ea_read32(const uint8_t *p, int be) {
    return be ? (uint32_t)p[0]<<24 | (uint32_t)p[1]<<16 | (uint32_t)p[2]<<8 | p[3]
              : (uint32_t)p[3]<<24 | (uint32_t)p[2]<<16 | (uint32_t)p[1]<<8 | p[0];
}
static inline unsigned ea_read16(const uint8_t *p, int be) { return be ? p[0]*256u+p[1] : p[1]*256u+p[0]; }
static inline void ea_put32(uint8_t *p, uint32_t v) { for (unsigned i=0;i<4;i++) p[i]=(uint8_t)(v>>(8*i)); }
typedef struct { uint32_t offset; uint8_t bytes[4]; } CopWord;
typedef struct {
    int fd;
    CopWord *words;
    size_t count, capacity;
} CopBig;
int ea_find_case(const char *root, const char *name, char *out, size_t size);
uint8_t *ea_map_file(const char *path, size_t expected, int writable);
int ea_checked_file(const char *path, size_t size, uint32_t expected);
int ea_read_exact(int fd, void *dst, size_t n, size_t offset);
int ea_collect_words(CopBig *b, size_t offset, const uint8_t *raw, const uint8_t *converted, size_t n);
int ea_sort_words(CopBig *b);   // 0 if two patches overlap
int ea_indexed_extent(const uint8_t *idx, unsigned bank, unsigned sample, size_t *start, size_t *end, size_t limit);
size_t ea_big_read_at(void *source, void *dst, size_t n, size_t pos);

typedef struct {
    unsigned codec, rate, samples, blocks, channels;
    double peak, energy;
} CopSpeechProof;

// All four files are selected together; buffers/file backing have process lifetime.
int x360copspeech_open(const char *win, X360MusicHandle *out);
void x360copspeech_warm(void); // optional host-only preflight after settings/diagnostics init
// Production normalization/validation and the original translated MicroTalk decoder.
// These are also exercised by x360copspeech_test.py without an audio device.
// kind: 1 = CSI, 2 = EVT, 3 = IDX; destination must have size bytes.
int copspeech_sidecar(unsigned kind, const uint8_t *src, size_t size, uint8_t *dst);
int copspeech_stream(uint8_t *data, size_t size, int convert, CopSpeechProof *proof);
int copspeech_decode(const uint8_t *data, size_t size, CopSpeechProof *proof);

// Layout parameters of one EA sidecar set (the CSI/EVT/IDX records are the same format in every bank).
typedef struct {
    unsigned count;          // CSI function count == EVT event count; IDX bank count is separate
    size_t evt_limit;        // EVT size field (start of the 32-byte function-handle table)
    int evt_head8;           // swap the EVT header word at +8 (cop speech only; NIS keeps it)
    int evt_words;           // swap per-event u32 words between the sub-offset array and the first sub (NIS)
    unsigned banks;          // IDX bank count
} EaSide;
int ea_sidecar(unsigned kind, const EaSide *p, const uint8_t *src, size_t size, uint8_t *dst);
// NIS audio (A10): X360 PT09 EA-XA streams -> PC PT00 (same payload bytes), same API as copspeech_stream.
int nisaudio_stream(uint8_t *data, size_t size, int convert, CopSpeechProof *proof);
int x360nisaudio_open(const char *win, X360MusicHandle *out);
int nisaudio_decode(const uint8_t *data, size_t size, CopSpeechProof *proof);
