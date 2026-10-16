// memory.h - guest arena and the guest heap allocator.
#pragma once
#include "guest.h"

// Maps (or re-maps) the configured guest arena at g_mem, zero filled, and resets
// the heap free list. Safe to call repeatedly; each call discards all guest
// state. Aborts on failure.
void mem_init();
void mem_shutdown();

// First-fit free-list allocator over HEAP_BASE..HEAP_LIMIT and the optional
// disjoint extra span. The fixed runtime gap is never allocated.
// Returns a guest address, or 0 on failure. Sizes are rounded up to a multiple
// of 16 and every block is at least 16-byte aligned; `align` may request more
// (VirtualAlloc uses 4096). `zero` clears the usable bytes.
uint32_t heap_alloc(uint32_t size, bool zero = false, uint32_t align = 16);
// The kit's own objects (NFSMW issue 8, 3.0.4): a block from the kit span at the top of the extra heap span, which
// heap_alloc never hands to the guest; the shared heap when there is no span or it is full. Freed with heap_free.
uint32_t heap_alloc_kit(uint32_t size, bool zero = false, uint32_t align = 16);
// Grows or shrinks in place when possible, otherwise allocates and copies.
// Returns the new address or 0 (in which case the old block is untouched).
uint32_t heap_realloc(uint32_t addr, uint32_t new_size, bool zero = false);
bool heap_free(uint32_t addr);
// Requested (not rounded) size of a live block, or 0xffffffff if `addr` is not
// the start of a live allocation.
uint32_t heap_size(uint32_t addr);
bool heap_owns(uint32_t addr);

// Guarded blocks: `guard` bytes of a known fill on each side of the usable
// span, so a guest write that runs off either end lands in the gap instead of
// on the neighbouring allocation, and is reported once with where it landed.
// Used for memory the guest writes in bulk next to kit objects (DirectSound
// sample rings, guest thread stacks). Returns the usable address (0 on
// failure); `what` must be a string literal. RECOMP_HEAP_GUARD=0 allocates
// without the gaps (the old layout).
uint32_t heap_alloc_guarded(uint32_t size, bool zero, uint32_t guard, const char *what,
                            uint32_t tag);
// Checks the gaps, then frees. A plain block is freed as heap_free would.
bool heap_free_guarded(uint32_t addr);
// Checks one guarded block's gaps; true when both are intact (or `addr` is not
// a guarded block). Damage is logged the first time, counted, and the gap is
// filled again so a later write is seen too.
bool heap_guard_check(uint32_t addr);
// Checks every guarded block; returns the number found damaged.
uint32_t heap_guard_check_all();
// Times any gap has been found written (all blocks, since mem_init).
uint64_t heap_guard_hits();

// Read-only guest pages (NFSMW issue 8, 3.0.3): a small kit object the guest must only read (a COM view) gets whole
// host pages of its own, writable until heap_seal_readonly. A guest write to a sealed page is stopped in the fault
// handler (the store is stepped over, so the object stays intact and the game goes on) and recorded with the guest
// EIP, stack and the host pc; heap_readonly_report logs new records from a normal context. heap_free_guarded frees
// these too. 3.0.4: opt-in, RECOMP_READONLY_PAGES=1 (a stopped store leaves the guest's own data half-written).
bool heap_readonly_enabled();
uint32_t heap_alloc_readonly(uint32_t size, const char *what, uint32_t tag);
bool heap_seal_readonly(uint32_t addr);
void heap_readonly_report();
// Writes stopped so far (all pages, since mem_init).
uint32_t heap_readonly_hits();

struct HeapStats {
    uint32_t used_blocks;
    uint32_t free_blocks;
    uint64_t used_bytes; // rounded block sizes
    uint64_t req_bytes;  // bytes actually requested by the guest
    uint64_t free_bytes;
    uint32_t largest_free;
    uint64_t total_allocs;
    uint64_t total_frees;
};
HeapStats heap_stats();
// Walks the block list and returns an error string, or "" when consistent.
std::string heap_check();
