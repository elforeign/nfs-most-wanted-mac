#include "host.h"
uint8_t *G;
static uint64_t heap_next = 0x30000000u;
static uint8_t committed[0x20000];
int win_vm_init(void) {
    if (G) return 0;
    G = VirtualAlloc(NULL, (size_t)WIN_GUEST_SIZE, MEM_RESERVE, PAGE_READWRITE);
    heap_next = 0x30000000u;
    memset(committed, 0, sizeof committed);
    return G != NULL;
}
int win_vm_commit(uint32_t address, uint64_t size) {
    uint64_t start, length;
    if (!G || !win_page_range(address, size, 0x1000, &start, &length)) return 0;
    if (VirtualAlloc(G + start, (size_t)length, MEM_COMMIT, PAGE_READWRITE) != G + start) return 0;
    for (uint64_t p=start/0x1000; p<(start+length)/0x1000; ++p)
        committed[p/8] |= (uint8_t)(1u << (p%8));
    return 1;
}
int win_vm_access(uint32_t address, uint64_t size) {
    uint64_t start, length;
    if (!G || !address || !win_page_range(address,size,0x1000,&start,&length)) return 0;
    for (uint64_t p=start/0x1000; p<(start+length)/0x1000; ++p)
        if (!(committed[p/8] & (1u << (p%8)))) return 0;
    return 1;
}
void win_vm_destroy(void) {
    if (G) { VirtualFree(G, 0, MEM_RELEASE); G = NULL; }
}
uint32_t win_alloc(uint32_t size) {
    uint64_t n = ((uint64_t)size + 0xfff) & ~UINT64_C(0xfff);
    if (!n || n > 0x70000000ull - heap_next) return 0;
    uint32_t address = (uint32_t)heap_next;
    if (!win_vm_commit(address, n)) return 0;
    heap_next += n; return address;
}
