/* MW 1.3 cinematic state on the guest frame thread, and lifecycle entry hooks. */
#include "runtime.h"
#include "blackbox_mw_cinematics.h"
#include <stdatomic.h>
#include <string.h>

#define MOVIE_PLAYER 0x0091CB10u
#define NIS_INTERFACE 0x009885C8u
#define NIS_ROOT_OFFSET 0x48u

static int readable(uint32_t address) {
    /* The flat guest mapping leaves room for every fixed field and bounded name read. */
    return G && address >= 0x1000u && address < 0xFFFFF000u;
}

static uint32_t name_hash(uint32_t address) {
    if (!readable(address)) return 0;
    uint32_t hash = UINT32_MAX; /* bStringHash, 00460BF0 */
    for (unsigned i = 0; i < 64; ++i) {
        unsigned ch = M8(address + i);
        if (!ch) return i ? hash : 0;
        if (ch < 0x20 || ch >= 0x7F) break;
        hash = hash * 33 + ch;
    }
    return 0;
}

static int movie_state(BlackboxCinematic *s) {
    if (!readable(s->address)) return s->address == 0;
    s->state = M32(s->address + 0x11C);
    s->next = M32(s->address + 0x120);
    s->paused = M8(s->address + 0x129);
    return 1;
}

static int nis_state(BlackboxCinematic *s) {
    if (!readable(s->address)) return s->address == 0;
    s->state = M32(s->address + 0x2F4);
    s->id = M32(s->address + 0x394);
    s->scene = M32(s->address + 0x39C);
    return 1;
}

/* Play can replace the filename in an existing player without a sampled state change. */
static _Atomic uint64_t movie_request;

void blackbox_mw_movie_edge(BlackboxCinematicEdge edge, uint32_t player) {
    if (!blackbox_enabled()) return;
    BlackboxCinematic s = {.address = player};
    int valid = movie_state(&s);
    if (player && valid) s.id = name_hash(player + 0x18);
    if (edge == BB_CIN_PLAY)
        atomic_store_explicit(&movie_request, ((uint64_t)player << 32) | s.id, memory_order_relaxed);
    else if (edge == BB_CIN_DESTROY) {
        uint64_t request = atomic_load_explicit(&movie_request, memory_order_relaxed);
        if ((uint32_t)(request >> 32) == player)
            atomic_compare_exchange_strong_explicit(&movie_request, &request, 0,
                                                    memory_order_relaxed, memory_order_relaxed);
    }
    blackbox_cinematic_edge(BB_MOVIE, edge, &s, valid);
}

void blackbox_mw_nis_edge(BlackboxCinematicEdge edge, uint32_t activity, uint32_t name) {
    if (!blackbox_enabled()) return;
    BlackboxCinematic s = {.address = activity};
    int valid = nis_state(&s);
    if (edge == BB_CIN_LOAD) {
        /* Load sets the new hash early in the guest call. Use the request here. */
        s.id = name_hash(name);
        s.scene = 0;
    }
    blackbox_cinematic_edge(BB_NIS, edge, &s, valid);
}

void blackbox_mw_cinematics(uint64_t frame, uint64_t now) {
    if (!blackbox_enabled() || !G) return;
    static BlackboxCinematic previous_movie, previous_nis;
    static int sampled;

    BlackboxCinematic movie = {.address = M32(MOVIE_PLAYER)};
    int movie_valid = movie_state(&movie);
    if (movie.address && movie_valid) {
        uint64_t request = atomic_load_explicit(&movie_request, memory_order_relaxed);
        if ((uint32_t)(request >> 32) == movie.address) movie.id = (uint32_t)request;
        else if (movie.address == previous_movie.address) movie.id = previous_movie.id;
        else movie.id = name_hash(movie.address + 0x18);
    }
    uint32_t interface = M32(NIS_INTERFACE);
    BlackboxCinematic nis = {.address = interface ? interface - NIS_ROOT_OFFSET : 0};
    int nis_valid = nis_state(&nis);
    if (!sampled || memcmp(&movie, &previous_movie, sizeof movie)) {
        blackbox_record_cinematic(BB_MOVIE, &movie, movie_valid, frame, now);
        previous_movie = movie;
    }
    if (!sampled || memcmp(&nis, &previous_nis, sizeof nis)) {
        blackbox_record_cinematic(BB_NIS, &nis, nis_valid, frame, now);
        previous_nis = nis;
    }
    sampled = 1;
}
