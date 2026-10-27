// heap.h - freeable guest heaps (heap.c).
#pragma once
#include <stdint.h>

typedef struct Heap Heap;
typedef struct HeapStats {
    uint64_t live_bytes, live_peak, live_blocks;   // requested sizes
    uint64_t pages_used, pages_peak, hwm_bytes;    // 4 KB pages taken from the range (slabs and large blocks)
    uint64_t allocs, frees, reallocs, bad_frees, slabs, large;
} HeapStats;

// A heap over guest addresses [base, limit), blocks aligned to gran (16 or 64).
Heap *heap_create(const char *name, uint32_t base, uint32_t limit, uint32_t gran);
uint32_t heap_alloc(Heap *h, uint32_t n);                          // zeroed; 0 when full
int heap_free(Heap *h, uint32_t a);                                // 0 if a isn't a live block (ignored)
uint32_t heap_realloc(Heap *h, uint32_t a, uint32_t n, int in_place_only);   // 0 on failure, old block kept
uint32_t heap_size(Heap *h, uint32_t a);                           // requested size, or 0xFFFFFFFF
int heap_owns(Heap *h, uint32_t a);
HeapStats heap_stats(Heap *h);
int heap_check(Heap *h);                                           // full consistency check (tests)
