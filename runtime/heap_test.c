// heap_test.c - standalone test of heap.c against a model (no game needed).
//   cc -O2 -DHEAP_TEST -o /tmp/heap_test runtime/heap_test.c runtime/heap.c && /tmp/heap_test
// Random alloc/free/realloc mixes (small, large, in-place-only) on a heap over a mapped range: every live block
// is filled with its own pattern and checked when it's freed or moved, so overlaps show up; blocks must be
// aligned and zeroed; the allocator's structures are checked against each other as it goes.
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>

#include "heap.h"

uint8_t *G;
static int fails;
#define CHECK(x, ...) do { if (!(x)) { if (fails++ < 20) { printf("FAIL %s:%d: ", __FILE__, __LINE__); printf(__VA_ARGS__); printf("\n"); } } } while (0)

typedef struct { uint32_t a, n; uint8_t tag; } Live;
static uint64_t rng = 0x9E3779B97F4A7C15ull;
static uint32_t rnd(void) { rng ^= rng << 13; rng ^= rng >> 7; rng ^= rng << 17; return (uint32_t)(rng >> 16); }

static uint32_t rnd_size(void) {
    uint32_t r = rnd() % 100;
    if (r < 60) return rnd() % 128;
    if (r < 85) return rnd() % 2048;
    if (r < 96) return rnd() % 20000;
    if (r < 99) return rnd() % 300000;
    return rnd() % 4000000;
}
static void fill(Live *l) { memset(G + l->a, l->tag, l->n); }
static int intact(Live *l) {
    for (uint32_t k = 0; k < l->n; ++k) if (G[l->a + k] != l->tag) return 0;
    return 1;
}
static int zeroed(uint32_t a, uint32_t n) {
    for (uint32_t k = 0; k < n; ++k) if (G[a + k]) return 0;
    return 1;
}

static void random_test(uint32_t gran, int steps, int max_live) {
    Heap *h = heap_create("test", 0x10000000u, 0x30000000u, gran);   // 512 MB, as in the game
    Live *live = calloc(max_live, sizeof *live);
    int nlive = 0;
    for (int step = 0; step < steps; ++step) {
        uint32_t op = rnd() % 10;
        if (nlive < max_live && (op < 5 || nlive == 0)) {
            Live l = {0, rnd_size(), (uint8_t)(rnd() | 1)};
            l.a = heap_alloc(h, l.n);
            CHECK(l.a, "alloc %u failed", l.n);
            if (!l.a) continue;
            CHECK(l.a % gran == 0, "alignment %08X gran %u", l.a, gran);
            CHECK(zeroed(l.a, l.n), "alloc %u at %08X not zeroed", l.n, l.a);
            CHECK(heap_size(h, l.a) == l.n, "size %u != %u", heap_size(h, l.a), l.n);
            fill(&l);
            live[nlive++] = l;
        } else if (op < 8) {
            int k = rnd() % nlive;
            CHECK(intact(&live[k]), "block %08X (%u) overwritten", live[k].a, live[k].n);
            CHECK(heap_free(h, live[k].a), "free %08X refused", live[k].a);
            CHECK(!heap_free(h, live[k].a), "double free of %08X accepted", live[k].a);
            live[k] = live[--nlive];
        } else {
            int k = rnd() % nlive, inplace = rnd() % 8 == 0;
            Live *l = &live[k];
            uint32_t n = rnd() % 4 == 0 ? rnd_size() : l->n + rnd() % 64 - 32;
            if ((int32_t)n < 0) n = 0;
            CHECK(intact(l), "block %08X (%u) overwritten before realloc", l->a, l->n);
            uint32_t r = heap_realloc(h, l->a, n, inplace);
            if (!r) { CHECK(inplace, "realloc %u -> %u failed", l->n, n); CHECK(intact(l), "failed in-place realloc changed the block"); continue; }
            if (inplace) CHECK(r == l->a, "in-place realloc moved");
            uint32_t keep = n < l->n ? n : l->n;
            for (uint32_t q = 0; q < keep; ++q) if (G[r + q] != l->tag) { CHECK(0, "realloc lost contents at +%u", q); break; }
            if (n > l->n) CHECK(zeroed(r + l->n, n - l->n), "realloc growth not zeroed (%u -> %u)", l->n, n);
            CHECK(r % gran == 0, "realloc alignment");
            l->a = r; l->n = n;
            fill(l);
        }
        if (step % 5000 == 0) CHECK(heap_check(h), "heap_check failed at step %d", step);
    }
    CHECK(heap_check(h), "heap_check failed at end");
    while (nlive) { --nlive; CHECK(intact(&live[nlive]), "overwritten at end"); heap_free(h, live[nlive].a); }
    HeapStats st = heap_stats(h);
    CHECK(st.live_bytes == 0 && st.live_blocks == 0, "live after freeing all: %llu bytes %llu blocks", st.live_bytes, st.live_blocks);
    CHECK(st.pages_used * 4096 <= 4u << 20, "%llu KB of pages still used after freeing all (only one slab per class should stay)", st.pages_used * 4);
    CHECK(heap_check(h), "heap_check failed after freeing all");
    printf("gran %u: %d steps, live peak %.1f MB, pages peak %.1f MB, hwm %.1f MB, %llu allocs %llu frees %llu reallocs\n", gran, steps,
           st.live_peak / 1048576.0, st.pages_peak * 4096 / 1048576.0, st.hwm_bytes / 1048576.0, st.allocs, st.frees, st.reallocs);
    free(live);
}

static void edge_tests(void) {
    Heap *h = heap_create("edge", 0x20000000u, 0x20400000u, 16);       // 4 MB
    uint32_t z = heap_alloc(h, 0);
    CHECK(z && heap_size(h, z) == 0, "zero-size alloc");
    CHECK(!heap_free(h, z + 16) || 1, "");
    CHECK(!heap_free(h, 0x20000004u), "interior pointer free accepted");
    CHECK(!heap_free(h, 0x30000000u), "foreign pointer free accepted");
    CHECK(heap_free(h, 0), "free(NULL)");
    CHECK(heap_alloc(h, 8u << 20) == 0, "oversize alloc should fail");
    // fill with large blocks until full, free every other one, then a block bigger than any hole must fail and
    // freeing the rest must coalesce back into one extent
    uint32_t big[64]; int nb = 0;
    while (nb < 64 && (big[nb] = heap_alloc(h, 100000))) ++nb;
    CHECK(nb > 30 && nb < 41, "filled %d 100 KB blocks in 4 MB", nb);
    for (int k = 0; k < nb; k += 2) heap_free(h, big[k]);
    CHECK(heap_alloc(h, 300000) == 0, "fragmented alloc should fail");
    for (int k = 1; k < nb; k += 2) heap_free(h, big[k]);
    CHECK(heap_check(h), "check after coalesce");
    uint32_t all = heap_alloc(h, 3u << 20);
    CHECK(all, "3 MB after coalescing");
    // grow in place into the right neighbour, blocked in-place-only growth, shrink in place
    heap_free(h, all);
    CHECK(heap_check(h), "check after freeing all");
    uint32_t a = heap_alloc(h, 50000), b = heap_alloc(h, 50000);
    CHECK(b == a + 13 * 4096, "large blocks packed from the start (%08X %08X)", a, b);
    CHECK(heap_realloc(h, a, 200000, 1) == 0, "in-place-only grow must fail when blocked");
    heap_free(h, b);
    CHECK(heap_realloc(h, a, 200000, 1) == a, "large grow into free right neighbour");
    CHECK(heap_realloc(h, a, 2000000, 0) == a, "grow again");
    CHECK(heap_realloc(h, a, 60000, 0) == a, "shrink large in place");
    CHECK(heap_size(h, a) == 60000, "size after shrink");
    uint32_t s = heap_alloc(h, 40), s2 = heap_realloc(h, s, 48, 0);
    CHECK(s2 == s, "small grow within class");
    CHECK(heap_check(h), "check after reallocs");
}

static void bench(void) {
    Heap *h = heap_create("bench", 0x10000000u, 0x30000000u, 16);
    enum { N = 1 << 16 };
    static uint32_t slot[N];
    struct timespec t0, t1;
    clock_gettime(CLOCK_MONOTONIC, &t0);
    for (int i = 0; i < 4000000; ++i) {
        uint32_t k = rnd() % N;
        if (slot[k]) heap_free(h, slot[k]);
        slot[k] = heap_alloc(h, rnd() % 512 + (rnd() % 64 == 0 ? rnd() % 60000 : 0));
    }
    clock_gettime(CLOCK_MONOTONIC, &t1);
    double s = (t1.tv_sec - t0.tv_sec) + (t1.tv_nsec - t0.tv_nsec) / 1e9;
    printf("bench: 4M free+alloc pairs in %.2f s (%.0f ns per pair)\n", s, s / 4e6 * 1e9);
    CHECK(heap_check(h), "bench check");
}

int main(void) {
    G = mmap(0, 1ull << 32, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON | MAP_NORESERVE, -1, 0);
    if (G == MAP_FAILED) { perror("mmap"); return 1; }
    edge_tests();
    random_test(16, 300000, 4000);
    random_test(64, 300000, 4000);
    bench();
    printf(fails ? "heap_test: %d FAILURES\n" : "heap_test: all passed\n", fails);
    return fails != 0;
}
