// Pure address arithmetic, import lookup, clock conversion and guest TEB layout.
#include "host.h"
int win_page_range(uint32_t address, uint64_t size, uint32_t page,
                   uint64_t *start, uint64_t *length) {
    if (!page || (page & (page - 1)) || !size || size > WIN_GUEST_SIZE - address) return 0;
    uint64_t lo = (uint64_t)address & ~((uint64_t)page - 1);
    uint64_t hi = ((uint64_t)address + size + page - 1) & ~((uint64_t)page - 1);
    if (hi > WIN_GUEST_SIZE) return 0;
    *start = lo; *length = hi - lo; return 1;
}
int win_import_table_valid(const struct ImportSlot *table, unsigned n) {
    if (n > (WIN_GUEST_SIZE - WIN_THUNK_BASE) / 4) return 0;
    for (unsigned i = 0; i < n; ++i)
        if ((table[i].slot & 3) || table[i].slot > UINT32_MAX - 3 || !table[i].name ||
            !table[i].name[0] || (i && table[i-1].slot >= table[i].slot)) return 0;
    return 1;
}
const struct ImportSlot *win_import_find(const struct ImportSlot *table, unsigned n, uint32_t slot) {
    unsigned lo = 0, hi = n;
    while (lo < hi) {
        unsigned m = lo + (hi - lo)/2;
        if (table[m].slot == slot) return table + m;
        if (table[m].slot < slot) lo = m + 1; else hi = m;
    }
    return NULL;
}
int win_thunk_index(uint32_t target, unsigned n, unsigned *index) {
    if (target < WIN_THUNK_BASE || (target & 3)) return 0;
    uint32_t i = (target - WIN_THUNK_BASE)/4;
    if (i >= n) return 0;
    *index = i; return 1;
}
uint64_t win_counter_ns(uint64_t ticks, uint64_t frequency) {
    // Divide before multiplying: QPC may already have run for months.
    if (!frequency || frequency > UINT64_MAX / 1000000000ull ||
        ticks / frequency > UINT64_MAX / 1000000000ull) return 0;
    uint64_t whole=ticks / frequency * 1000000000ull;
    uint64_t part=ticks % frequency * 1000000000ull / frequency;
    if (part > UINT64_MAX-whole) return 0;
    return whole+part;
}
void win_cpu_init(Cpu *c, uint32_t teb, uint32_t lo, uint32_t top, uint32_t tid) {
    memset(c, 0, sizeof *c);
    memset(G + teb, 0, 0x1000);
    c->fs_base = teb; c->esp = top - 0x100; c->cw = 0x027f; c->mxcsr = 0x1f80;
    M32(teb) = UINT32_MAX;
    M32(teb + 4) = top; M32(teb + 8) = lo;
    M32(teb + 0x18) = teb; M32(teb + 0x24) = tid;
    M32(teb + 0x30) = WIN_PEB;
}
