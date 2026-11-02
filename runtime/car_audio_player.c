#include "car_audio_player.h"
#include <string.h>

/* Decoded state: update 00898E58 vs 0081A52C, reset 008984B9 vs
 * 0081A42F, creation 00898D9F, options 00897A5C, cleanup 00897BDA.
 * Private words are mirrored as MW-native values, never pointer-cast voices.
 * The source factory's mode and six packed bytes are preserved, not discarded.
 */
static uint32_t read32(const uint8_t *p)
{
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 |
           (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

static int32_t signed32(uint32_t n)
{
    return n <= INT32_MAX ? (int32_t)n : -1 - (int32_t)(UINT32_MAX - n);
}

static void write32(uint8_t *p, uint32_t n)
{
    for (unsigned i = 0; i < 4; ++i) p[i] = (uint8_t)(n >> (i * 8));
}

static int option_supported(uint8_t code)
{
    return code == 0 || code == 2 || code == 3 || code == 5 ||
           code == 6 || code == 8;
}

static int profile(const uint8_t *s, size_t size)
{
    if (!s || size < 28 || size - 28 < (size_t)s[14] * 12)
        return CAR_AUDIO_PLAYER_INVALID;
    if (s[15] || s[17]) return CAR_AUDIO_PLAYER_PROFILE;
    for (unsigned i = 0; i < s[14]; ++i)
        if (!option_supported(s[28 + i * 12])) return CAR_AUDIO_PLAYER_PROFILE;
    return 0;
}

static int table_valid(const uint8_t *rows, size_t size)
{
    if (!rows || size < 4) return 0;
    uint32_t n = read32(rows);
    if (n > 256 || n > (size - 4) / 12) return 0;
    for (uint32_t i = 0; i < n; ++i) {
        const uint8_t *r = rows + 4 + i * 12;
        uint32_t first = read32(r);
        /* A3's exact accepted source rows, including authored empty entries.
         * Row+4 is an identity cross-check, never a dispatch selector.
         */
        if (first == 65535 && !read32(r + 4) && !read32(r + 8)) continue;
        uint32_t index = first & 65535;
        uint32_t mode = first >> 16;
        if (index > INT16_MAX || (mode != 60 && mode != 80 && mode != 100) ||
            read32(r + 4) != index + 1 || read32(r + 8) != UINT32_MAX) return 0;
    }
    return 1;
}

static void reset(CarAudioPlayer *p)
{
    if (p->owned) {
        p->backend.release(p->context, p->voice);
        p->owned = 0;
    }
    p->voice = -1;
    write32(p->state + 8, 0);
    p->state[16] = 255;
}

int car_audio_player_attach(CarAudioPlayer *p, uint8_t *state, size_t size,
                          const uint8_t *selectors, size_t selectors_size,
                          uint64_t bank_identity,
                          const CarAudioPlayerBackend *backend, void *context)
{
    if (!p || p->state || !backend || !backend->create || !backend->release ||
        !backend->pause || !backend->resume || !backend->option ||
        !backend->status) return CAR_AUDIO_PLAYER_INVALID;
    int error = profile(state, size);
    if (error) return error;
    if (!table_valid(selectors, selectors_size)) return CAR_AUDIO_PLAYER_INVALID;
    if (read32(state + 8) || state[16] != 255 || state[12] || state[13])
        return CAR_AUDIO_PLAYER_INVALID;
    /* The C API takes real bounded buffers; guest pointer words are never used
     * to reconstruct them. Reject overlap without overflowing end pointers.
     */
    uintptr_t a = (uintptr_t)state, b = (uintptr_t)selectors;
    if ((a <= b && b - a < size) || (b < a && a - b < selectors_size))
        return CAR_AUDIO_PLAYER_INVALID;
    *p = (CarAudioPlayer){ .state = state, .state_size = size,
        .selectors = selectors, .selectors_size = selectors_size,
        .backend = *backend, .context = context, .bank_identity = bank_identity,
        .voice = -1, .option_count = state[14] };
    return 0;
}

static void options(CarAudioPlayer *p, int all)
{
    for (unsigned i = 0; i < p->option_count; ++i) {
        uint8_t *r = p->state + 28 + i * 12;
        uint32_t value = read32(r + 8);
        if (all || read32(r + 4) != value) {
            p->backend.option(p->context, p->voice, r[0], signed32(value));
            write32(r + 4, value);
        }
    }
}

static void start(CarAudioPlayer *p)
{
    uint32_t count = read32(p->selectors);
    if (!count) { reset(p); return; } /* Safe empty table, no row[-1]. */
    int32_t selected = signed32(read32(p->state + 20));
    if (selected < 0) selected = 0;
    else if ((uint32_t)selected >= count) selected = (int32_t)count - 1;
    const uint8_t *row = p->selectors + 4 + (size_t)selected * 12;
    uint16_t sample = (uint16_t)((unsigned)row[0] | (unsigned)row[1] << 8);
    if (sample == UINT16_MAX) { reset(p); return; }
    CarAudioPlayerCreate request = { .bank_identity = p->bank_identity,
        .sample = (int16_t)sample, .mode = row[2],
        .packed_offset = read32(row + 8), .options = p->state + 28,
        .option_count = p->option_count };
    for (unsigned i = 0; i < 6; ++i)
        request.initialization[i] = (uint32_t)row[3 + i] << 8;
    int32_t voice = -1;
    int result = p->backend.create(p->context, &request, &voice);
    if (result < 0 || voice < 0) {
        p->last_error = CAR_AUDIO_PLAYER_BACKEND;
        reset(p);
        return;
    }
    p->voice = voice;
    p->owned = 1;
    write32(p->state + 8, (uint32_t)voice);
    p->state[16] = 0;
    options(p, 1);
}

int car_audio_player_update(CarAudioPlayer *p)
{
    if (!p || !p->state) return CAR_AUDIO_PLAYER_INVALID;
    p->last_error = 0;
    int error = profile(p->state, p->state_size);
    if (!error && (p->state[14] != p->option_count ||
                   !table_valid(p->selectors, p->selectors_size)))
        error = CAR_AUDIO_PLAYER_INVALID;
    /* Detect mixed native/updater ownership without ever releasing a word
     * supplied by the wire state. Detach still consumes our original voice.
     */
    if (!error && (p->state[16] != (p->owned ? 0 : 255) ||
                   read32(p->state + 8) != (p->owned ? (uint32_t)p->voice : 0) ||
                   p->state[12] > 2 || p->state[13] > 2))
        error = CAR_AUDIO_PLAYER_INVALID;
    if (error) { p->last_error = error; return error; }
    int command = signed32(read32(p->state + 24));
    if (command < 0) command = 0;
    else if (command > 2) command = 2;
    int current = p->state[12], previous = p->state[13];
    if (command != current) {
        if (!command) {
            if (p->owned) reset(p);
        } else if (command == 1) {
            if (p->owned) p->backend.resume(p->context, p->voice);
            else if (current != 2 || previous != 1) start(p);
        } else if (p->owned) p->backend.pause(p->context, p->voice);
        p->state[13] = (uint8_t)current;
        p->state[12] = (uint8_t)command;
    }
    if (command == 1 && p->owned) {
        options(p, 0);
        int status = p->backend.status(p->context, p->voice);
        if (status <= 0) {
            if (status < 0) p->last_error = CAR_AUDIO_PLAYER_BACKEND;
            reset(p);
            return 0;
        }
        return 1;
    }
    return p->owned ? command : 0;
}

void car_audio_player_detach(CarAudioPlayer *p)
{
    if (!p || !p->state) return;
    reset(p);
    p->state[12] = p->state[13] = 0;
    /* Requested command and options belong to the module; leave them intact. */
    memset(p, 0, sizeof(*p));
}

void car_audio_player_destroy(CarAudioPlayer *players, size_t count)
{
    if (players) for (size_t i = 0; i < count; ++i)
        car_audio_player_detach(players + i);
}
