// X360 sound banks (ABKC + S10A, codec 3 = EA-wrapped XMA1) parsed and decoded to PCM16 (A10, G3).
#pragma once
#include <stddef.h>
#include <stdint.h>

typedef struct {
    unsigned version, codec, channels, rate, storage, loop, samples, loop_start;
    uint32_t hdr;      // header offset in the file; 0 = empty slot (offset FFFFFFFF)
    uint32_t data;     // first data block in the file (resident storage: right after the header)
    uint32_t end;      // end of the sound's bytes (next header, or the bank's end)
} X360AbkSound;

typedef struct {
    uint32_t bank, bank_size;  // S10A offset/extent in the file
    unsigned count;
    X360AbkSound s[512];
} X360Abk;

// Typed big-endian ABKC fields and the S10A header table (X360 825EA4C8). 1 = ok, else why is set.
int x360abk_parse(const uint8_t *file, size_t size, X360Abk *out, char *why, size_t why_size);
// One sound to interleaved PCM16 (malloc'd, *frames = declared sample count exactly). Codec 3 only. 1 = ok.
int x360abk_decode(const uint8_t *file, const X360AbkSound *s, int16_t **pcm, unsigned *frames, char *why, size_t why_size);

// A PC BNKl slot as the PC's PT parser (00823E83) reads it.
typedef struct {
    int present, channels, rate, codec, samples, loop_start, loop_end;
    uint32_t data;   // file offset of the sample data
} X360AbkPcSlot;
int x360abk_pc_bank(const uint8_t *file, size_t size, X360AbkPcSlot *slots, unsigned max, unsigned *count, char *why, size_t why_size);

typedef struct {
    unsigned sounds, tables;
    uint32_t table_rows[8][2];   // {PC rows, X360 rows} per converted sample table
    size_t bank_bytes, pcm_bytes;
} X360AbkBuildInfo;
// The PC bank rebuilt around the X360 sounds: the PC's modules (x86 constructors, templates) and fixups kept, the
// X360's sample tables converted and the template pointers redirected to them, a new BNKl of PCM16LE (codec 8) slots
// (X360 sound i at slot i + 1). pcm[i] = x360abk_decode output of sound i (mono). *out is malloc'd. 1 = ok.
int x360abk_build_pc(const uint8_t *pc, size_t pc_size, const uint8_t *x, size_t x_size, const X360Abk *k,
                     int16_t *const *pcm, uint8_t **out, size_t *out_size, X360AbkBuildInfo *info, char *why, size_t why_size);

// G3 production path: the retail pair pinned by CRC32 (PC file, X360 file, all decoded PCM), decoded, rebuilt and read
// back as the PC parses it. Anything else (other files, a corrupt byte, a decoder change) returns 0 and the PC bank stays.
typedef struct { const char *name; uint32_t pc_crc, x_crc, pcm_crc; } X360AbkPin;
int x360abk_convert_pinned(const uint8_t *pc, size_t pc_size, const uint8_t *x, size_t x_size, const X360AbkPin *pin,
                           uint8_t **out, size_t *out_size, X360AbkBuildInfo *info, char *why, size_t why_size);

// A10, every other bank: the PC file kept byte for byte (modules, sample tables, fixups, each PT header's tags before
// its channel section) and only the BNKl rebuilt, X360 sound i (PCM16LE, codec 8, X360 rate/length/loop) in PC slot i + 1.
// Decided per bank by structure, no list: the same slots present, the same sample tables row for row, mono on both, the
// same loop flags, no sound that matches another slot's length (reordered), every sound decoded; an all-zero X360
// sound (placeholder) keeps the PC's slot and data. The result is read back as the PC parses it. 0 = PC bank stays,
// why = "<reason>: <detail>" (the reason before the colon groups the BOOT_DIAG counts).
typedef struct { unsigned sounds, placeholders; size_t pcm_bytes; } X360AbkSwapInfo;
int x360abk_swap_pc(const uint8_t *pc, size_t pc_size, const uint8_t *x, size_t x_size, uint8_t **out, size_t *out_size,
                    X360AbkSwapInfo *info, char *why, size_t why_size);

// A10 serving: the swapped bank of this PC/X360 file pair as a read-only descriptor at offset 0, else -1 (PC bank stays;
// why says so). Converted at first use and cached as <cache_dir>/<name>_<PC CRC32>_<X360 CRC32>_v1.abk (temp file +
// rename, so a cache file is whole or absent; a cache file that does not read back as a PC bank of that size is
// converted again); a cache that cannot be written serves an unlinked temp file. Thread-safe.
typedef struct { int cached; X360AbkSwapInfo info; size_t bytes; double ms; } X360AbkServe;
int x360abk_serve(const char *pc_path, const char *x_path, const char *cache_dir, X360AbkServe *st, char *why, size_t why_size);
