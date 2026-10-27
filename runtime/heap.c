// heap.c - freeable guest heaps: the Win32 process heap (HeapAlloc/HeapFree/HeapReAlloc/HeapSize, GlobalAlloc)
// and the runtime's resource heap (gmalloc/gfree: D3D resources, DirectSound buffers).
//
// Each heap owns a fixed guest address range. Bookkeeping lives host-side, so a guest overrun or a stray write
// can't corrupt the allocator, and a bad or double free is detected and ignored instead of crashing later:
//   - pages (4 KB) come from an extent allocator: free extents are binned by log2 length and coalesce with their
//     neighbours on free (boundary tags in host arrays, O(1));
//   - small blocks (<= 16 KB) come from size-class slabs (64 KB or more of pages, cut into equal blocks) with a
//     per-slab free stack; a slab that empties is returned to the pages unless it's the class's last one;
//   - larger blocks are runs of pages; HeapReAlloc grows them in place into a free right neighbour when it can.
// Blocks are 16-byte aligned (Win32 gives 8) for the process heap and 64-byte aligned for the resource heap.
// Every block is zeroed when allocated (the bump heap always did, and the game ran on that), which also covers
// HEAP_ZERO_MEMORY; pages above the heap's high-water mark are known zero and aren't touched.
// Thread safety: guest code calls in under the guest lock; each heap also has its own mutex for host callers.
//
// Guest memory map: process heap 0x10000000-0x30000000 (512 MB), resource heap 0x50000000-0x7F000000.
// heap_test.c tests this file standalone (cc -DHEAP_TEST).
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef HEAP_TEST
extern uint8_t *G;
#else
#include "host.h"
#endif
#include "heap.h"

#define PAGE 4096u
#define NBINS 32
#define SMALL_MAX 16384u
#define MAX_CLASSES 64

typedef struct Span {
    uint32_t base, npages;         // guest address, pages
    int cls;                       // size class, or -1 for a large block
    uint32_t size;                 // large: requested size
    uint32_t bsize, nblocks, nfree;// slab
    uint16_t *freestk;             // slab: free block indices (top = next to hand out)
    uint32_t *req;                 // slab: requested size per block
    uint8_t *used;                 // slab: block in use
    struct Span *next, *prev;      // slab: class list of slabs with free blocks
    int partial;
} Span;

struct Heap {
    const char *name;
    pthread_mutex_t mu;
    uint32_t base, npages, gran, hwm;            // hwm: pages at or above it were never handed out (still zero)
    Span **owner;                                // page -> span
    uint32_t *ext_len, *ext_start1;              // free extent length at its first page / start+1 at its last page
    int32_t *nxt, *prv;                          // bin links, on first pages
    int32_t bins[NBINS];
    int ncls;
    uint32_t cls_size[MAX_CLASSES];
    uint8_t *cls_of;                             // (n + gran - 1) / gran -> class, n <= SMALL_MAX
    Span *partial[MAX_CLASSES];
    HeapStats st;
};

// ---------------- page extents ----------------
static int bin_of(uint32_t n) { return 31 - __builtin_clz(n); }

static void ext_insert(Heap *h, uint32_t s, uint32_t n) {
    h->ext_len[s] = n;
    h->ext_start1[s + n - 1] = s + 1;
    int b = bin_of(n);
    h->prv[s] = -1;
    h->nxt[s] = h->bins[b];
    if (h->bins[b] >= 0) h->prv[h->bins[b]] = (int32_t)s;
    h->bins[b] = (int32_t)s;
}
static void ext_remove(Heap *h, uint32_t s) {
    uint32_t n = h->ext_len[s];
    int b = bin_of(n);
    if (h->prv[s] >= 0) h->nxt[h->prv[s]] = h->nxt[s]; else h->bins[b] = h->nxt[s];
    if (h->nxt[s] >= 0) h->prv[h->nxt[s]] = h->prv[s];
    h->ext_len[s] = 0;
    h->ext_start1[s + n - 1] = 0;
}
// Takes [a, a+n) out of the free extent starting at s, putting the pieces before and after back.
static void ext_take(Heap *h, uint32_t s, uint32_t a, uint32_t n) {
    uint32_t len = h->ext_len[s];
    ext_remove(h, s);
    if (a > s) ext_insert(h, s, a - s);
    if (a + n < s + len) ext_insert(h, a + n, s + len - (a + n));
}
// Zeroes guest bytes [a, a+n) that may have been used before (below the high-water mark).
static void zero_dirty(Heap *h, uint32_t a, uint32_t n) {
    uint32_t top = h->base + h->hwm * PAGE;
    if (a < top) memset(G + a, 0, (a + n < top ? a + n : top) - a);
}
static void mark_used(Heap *h, uint32_t p, uint32_t n) {
    if (p + n > h->hwm) h->hwm = p + n;
    h->st.pages_used += n;
    if (h->st.pages_used > h->st.pages_peak) h->st.pages_peak = h->st.pages_used;
}
// Allocates n pages aligned to `align` pages; returns the first page index or -1.
static int64_t pages_alloc(Heap *h, uint32_t n, uint32_t align) {
    for (int b = bin_of(n), scanned = 0; b < NBINS; ++b)
        for (int32_t s = h->bins[b]; s >= 0; s = h->nxt[s]) {
            uint32_t a = (s + align - 1) / align * align;
            if (a + n <= s + h->ext_len[s]) { ext_take(h, (uint32_t)s, a, n); mark_used(h, a, n); return a; }
            if (++scanned > 64 && align == 1) break;   // the next bin up always fits
        }
    return -1;
}
static void pages_free(Heap *h, uint32_t s, uint32_t n) {
    h->st.pages_used -= n;
    for (uint32_t k = 0; k < n; ++k) h->owner[s + k] = NULL;
    if (s > 0 && h->ext_start1[s - 1]) { uint32_t ls = h->ext_start1[s - 1] - 1; n += h->ext_len[ls]; ext_remove(h, ls); s = ls; }
    if (s + n < h->npages && h->ext_len[s + n]) { uint32_t r = s + n; n += h->ext_len[r]; ext_remove(h, r); }
    ext_insert(h, s, n);
}

// ---------------- heap ----------------
Heap *heap_create(const char *name, uint32_t base, uint32_t limit, uint32_t gran) {
    Heap *h = calloc(1, sizeof *h);
    h->name = name;
    pthread_mutex_init(&h->mu, NULL);
    h->base = base;
    h->npages = (limit - base) / PAGE;
    h->gran = gran;
    h->owner = calloc(h->npages, sizeof *h->owner);
    h->ext_len = calloc(h->npages, 4);
    h->ext_start1 = calloc(h->npages, 4);
    h->nxt = calloc(h->npages, 4);
    h->prv = calloc(h->npages, 4);
    for (int b = 0; b < NBINS; ++b) h->bins[b] = -1;
    ext_insert(h, 0, h->npages);
    // classes: gran, 2*gran ... 16*gran, then four steps per power of two up to SMALL_MAX
    for (uint32_t k = 1; k <= 16; ++k) h->cls_size[h->ncls++] = k * gran;
    for (uint32_t p = 16 * gran; p < SMALL_MAX; p *= 2)
        for (uint32_t k = 5; k <= 8; ++k) h->cls_size[h->ncls++] = p / 4 * k;
    h->cls_of = calloc(SMALL_MAX / gran + 1, 1);
    for (uint32_t u = 0, c = 0; u <= SMALL_MAX / gran; ++u) {
        while (h->cls_size[c] < (u ? u : 1) * gran) ++c;
        h->cls_of[u] = (uint8_t)c;
    }
    return h;
}

static Span *slab_new(Heap *h, int cls) {
    uint32_t bsize = h->cls_size[cls], np = 16;
    while (np * PAGE < 8 * bsize) np *= 2;
    int64_t p = pages_alloc(h, np, 1);
    if (p < 0) return NULL;
    Span *s = calloc(1, sizeof *s);
    s->base = h->base + (uint32_t)p * PAGE; s->npages = np; s->cls = cls; s->bsize = bsize;
    s->nblocks = s->nfree = np * PAGE / bsize;
    s->freestk = malloc(s->nblocks * sizeof *s->freestk);
    for (uint32_t i = 0; i < s->nblocks; ++i) s->freestk[i] = (uint16_t)(s->nblocks - 1 - i);
    s->req = calloc(s->nblocks, 4);
    s->used = calloc(s->nblocks, 1);
    for (uint32_t k = 0; k < np; ++k) h->owner[p + k] = s;
    ++h->st.slabs;
    return s;
}
static void partial_add(Heap *h, Span *s) {
    s->prev = NULL; s->next = h->partial[s->cls];
    if (s->next) s->next->prev = s;
    h->partial[s->cls] = s; s->partial = 1;
}
static void partial_del(Heap *h, Span *s) {
    if (s->prev) s->prev->next = s->next; else h->partial[s->cls] = s->next;
    if (s->next) s->next->prev = s->prev;
    s->partial = 0;
}

static uint32_t alloc_locked(Heap *h, uint32_t n) {
    uint32_t a;
    if (n <= SMALL_MAX) {
        int cls = h->cls_of[(n + h->gran - 1) / h->gran];
        Span *s = h->partial[cls];
        if (!s) { if (!(s = slab_new(h, cls))) return 0; partial_add(h, s); }
        uint32_t i = s->freestk[--s->nfree];
        if (!s->nfree) partial_del(h, s);
        s->used[i] = 1; s->req[i] = n;
        a = s->base + i * s->bsize;
        zero_dirty(h, a, s->bsize);
    } else {
        if (n > h->npages * PAGE) return 0;
        uint32_t np = (n + PAGE - 1) / PAGE;
        int64_t p = pages_alloc(h, np, 1);
        if (p < 0) return 0;
        Span *s = calloc(1, sizeof *s);
        s->base = a = h->base + (uint32_t)p * PAGE; s->npages = np; s->cls = -1; s->size = n;
        for (uint32_t k = 0; k < np; ++k) h->owner[p + k] = s;
        zero_dirty(h, a, n);
        ++h->st.large;
    }
    h->st.live_bytes += n; ++h->st.live_blocks; ++h->st.allocs;
    if (h->st.live_bytes > h->st.live_peak) h->st.live_peak = h->st.live_bytes;
    return a;
}

// Finds the span and (for slabs) block index of a block start; NULL if `a` isn't a live block of this heap.
static Span *lookup(Heap *h, uint32_t a, uint32_t *idx) {
    if (a < h->base || a >= h->base + h->npages * PAGE) return NULL;
    Span *s = h->owner[(a - h->base) / PAGE];
    if (!s) return NULL;
    if (s->cls < 0) return a == s->base ? s : NULL;
    uint32_t off = a - s->base;
    if (off % s->bsize || !s->used[off / s->bsize]) return NULL;
    *idx = off / s->bsize;
    return s;
}
static uint32_t size_of(Span *s, uint32_t i) { return s->cls < 0 ? s->size : s->req[i]; }

static void free_span_block(Heap *h, Span *s, uint32_t i) {
    h->st.live_bytes -= size_of(s, i); --h->st.live_blocks; ++h->st.frees;
    if (s->cls < 0) {
        pages_free(h, (s->base - h->base) / PAGE, s->npages);
        --h->st.large;
        free(s);
        return;
    }
    s->used[i] = 0;
    s->freestk[s->nfree++] = (uint16_t)i;
    if (s->nfree == 1) partial_add(h, s);
    if (s->nfree == s->nblocks && (s->prev || s->next)) {   // empty and not the class's only slab with room
        partial_del(h, s);
        pages_free(h, (s->base - h->base) / PAGE, s->npages);
        free(s->freestk); free(s->req); free(s->used); free(s);
        --h->st.slabs;
    }
}

uint32_t heap_alloc(Heap *h, uint32_t n) {
    pthread_mutex_lock(&h->mu);
    uint32_t a = alloc_locked(h, n);
    pthread_mutex_unlock(&h->mu);
    return a;
}

int heap_free(Heap *h, uint32_t a) {
    if (!a) return 1;
    pthread_mutex_lock(&h->mu);
    uint32_t i = 0;
    Span *s = lookup(h, a, &i);
    if (s) free_span_block(h, s, i); else ++h->st.bad_frees;
    pthread_mutex_unlock(&h->mu);
    return s != NULL;
}

uint32_t heap_size(Heap *h, uint32_t a) {
    pthread_mutex_lock(&h->mu);
    uint32_t i = 0;
    Span *s = lookup(h, a, &i);
    uint32_t n = s ? size_of(s, i) : 0xFFFFFFFFu;
    pthread_mutex_unlock(&h->mu);
    return n;
}

int heap_owns(Heap *h, uint32_t a) {
    pthread_mutex_lock(&h->mu);
    uint32_t i = 0;
    int r = lookup(h, a, &i) != NULL;
    pthread_mutex_unlock(&h->mu);
    return r;
}

// Resizes in place when the block's capacity allows (or a large block can grow into free pages to its right);
// otherwise moves it, unless in_place_only. Returns the block's address, or 0 (the old block is then untouched).
uint32_t heap_realloc(Heap *h, uint32_t a, uint32_t n, int in_place_only) {
    pthread_mutex_lock(&h->mu);
    uint32_t i = 0, r = 0;
    Span *s = lookup(h, a, &i);
    if (!s) { ++h->st.bad_frees; goto out; }
    uint32_t old = size_of(s, i);
    ++h->st.reallocs;
    if (s->cls >= 0) {
        // stay put if it fits and doesn't waste more than half the block (or moving isn't allowed)
        if (n <= s->bsize && (in_place_only || n > s->bsize / 2 || s->cls == 0)) {
            if (n > old) memset(G + a + old, 0, n - old);
            s->req[i] = n; h->st.live_bytes = h->st.live_bytes - old + n;
            r = a; goto out;
        }
    } else if (n > SMALL_MAX || in_place_only) {
        uint32_t np = (n + PAGE - 1) / PAGE, p = (a - h->base) / PAGE;
        if (np <= s->npages) {                               // shrink: give the tail pages back
            if (np < s->npages && np) { pages_free(h, p + np, s->npages - np); s->npages = np; }
            if (n > old) memset(G + a + old, 0, n - old);
            s->size = n; h->st.live_bytes = h->st.live_bytes - old + n;
            r = a; goto out;
        }
        uint32_t right = p + s->npages, need = np - s->npages;
        if (right < h->npages && h->ext_len[right] >= need) {  // grow into the free extent to the right
            ext_take(h, right, right, need);
            mark_used(h, right, need);
            for (uint32_t k = 0; k < need; ++k) h->owner[right + k] = s;
            memset(G + a + old, 0, (s->npages * PAGE) - old);  // rest of the old last page
            zero_dirty(h, h->base + right * PAGE, n - s->npages * PAGE);
            s->npages = np; s->size = n; h->st.live_bytes = h->st.live_bytes - old + n;
            r = a; goto out;
        }
    }
    if (in_place_only) goto out;
    r = alloc_locked(h, n);
    if (r) {
        memcpy(G + r, G + a, old < n ? old : n);
        free_span_block(h, s, i);                            // alloc_locked never releases spans, so s is still valid
        --h->st.frees; --h->st.allocs;                       // count the move as one realloc
    }
out:
    pthread_mutex_unlock(&h->mu);
    return r;
}

HeapStats heap_stats(Heap *h) {
    pthread_mutex_lock(&h->mu);
    HeapStats st = h->st;
    st.hwm_bytes = (uint64_t)h->hwm * PAGE;
    pthread_mutex_unlock(&h->mu);
    return st;
}

// Consistency check for tests: extents and spans tile the range exactly, bins and boundary tags agree.
int heap_check(Heap *h) {
    uint32_t free_pages = 0, nfree_ext = 0;
    for (uint32_t p = 0; p < h->npages;) {
        if (h->ext_len[p]) {
            uint32_t n = h->ext_len[p];
            if (h->ext_start1[p + n - 1] != p + 1) return 0;
            for (uint32_t k = 0; k < n; ++k) if (h->owner[p + k]) return 0;
            if (p + n < h->npages && h->ext_len[p + n]) return 0;      // uncoalesced neighbours
            free_pages += n; ++nfree_ext; p += n;
        } else {
            Span *s = h->owner[p];
            if (!s || s->base != h->base + p * PAGE) return 0;
            for (uint32_t k = 0; k < s->npages; ++k) if (h->owner[p + k] != s) return 0;
            if (s->cls >= 0) {
                uint32_t used = 0;
                for (uint32_t b = 0; b < s->nblocks; ++b) used += s->used[b];
                if (used + s->nfree != s->nblocks || (s->nfree > 0) != s->partial) return 0;
            }
            p += s->npages;
        }
    }
    uint32_t in_bins = 0;
    for (int b = 0; b < NBINS; ++b)
        for (int32_t s = h->bins[b]; s >= 0; s = h->nxt[s]) { if (bin_of(h->ext_len[s]) != b) return 0; ++in_bins; }
    return in_bins == nfree_ext && free_pages + h->st.pages_used == h->npages;
}

#ifndef HEAP_TEST
// ---------------- guest side ----------------
#include <mach/mach.h>

#define PROCESS_HEAP_LO 0x10000000u
#define PROCESS_HEAP_HI 0x30000000u
#define RESOURCE_HEAP_LO 0x50000000u
#define RESOURCE_HEAP_HI 0x7F000000u
#define PROCESS_HEAP_HANDLE 0x00010000u
#define HEAP_REALLOC_IN_PLACE_ONLY 0x10u

static Heap *proc_heap, *res_heap;
void diag_report(const char *file, const char *why);

static void heaps_create(void) {
    proc_heap = heap_create("process", PROCESS_HEAP_LO, PROCESS_HEAP_HI, 16);
    res_heap = heap_create("resource", RESOURCE_HEAP_LO, RESOURCE_HEAP_HI, 64);
}
static void heaps_init(void) {
    static pthread_once_t once = PTHREAD_ONCE_INIT;
    if (!res_heap) pthread_once(&once, heaps_create);
}

static void full(Heap *h, uint32_t n) {
    HeapStats st = heap_stats(h);
    char msg[256];
    snprintf(msg, sizeof msg, "guest %s heap full: %u bytes requested, %llu MB live in %llu blocks, %llu MB of pages used",
             h->name, n, (unsigned long long)st.live_bytes >> 20, (unsigned long long)st.live_blocks, (unsigned long long)st.pages_used * PAGE >> 20);
    fprintf(stderr, "boot: %s\n", msg);
    diag_report("crash.txt", msg);
    exit(1);
}

// BOOT_DIAG: guest allocations by call site (galloc, gmalloc: host caller; HeapAlloc, VirtualAlloc: guest caller),
// the top 25 appended to heap_callers.txt every 30 s, so whatever still grows can be traced to its caller. Addresses are symbolized with dladdr; static
// functions show as the nearest exported symbol, and the image offset is for `atos -o runtime/boot -l 0x100000000`.
#include <dlfcn.h>
#include <mach-o/dyld.h>
typedef struct { void *ret; int kind; uint64_t calls, bytes; } Site;
static Site sites[512];
static pthread_mutex_t sites_mu = PTHREAD_MUTEX_INITIALIZER;
void heap_note(int kind, void *ret, uint32_t n) {
    if (!diag_on) return;
    pthread_mutex_lock(&sites_mu);
    uint32_t i = (uint32_t)(((uintptr_t)ret >> 2) * 2654435761u + kind) % 512;
    for (int k = 0; k < 512 && sites[i].ret && (sites[i].ret != ret || sites[i].kind != kind); ++k) i = (i + 1) % 512;
    if (!sites[i].ret) { sites[i].ret = ret; sites[i].kind = kind; }
    if (sites[i].ret == ret) { ++sites[i].calls; sites[i].bytes += n; }
    pthread_mutex_unlock(&sites_mu);
}
static int site_cmp(const void *a, const void *b) {
    const Site *x = a, *y = b;
    return x->bytes < y->bytes ? 1 : x->bytes > y->bytes ? -1 : 0;
}
static void write_sites(double t) {
    char path[4096];
    snprintf(path, sizeof path, "%s/heap_callers.txt", diag_dir);
    FILE *f = fopen(path, "a");
    if (!f) return;
    static Site copy[512];
    pthread_mutex_lock(&sites_mu);
    memcpy(copy, sites, sizeof sites);
    pthread_mutex_unlock(&sites_mu);
    qsort(copy, 512, sizeof *copy, site_cmp);
    static const char *const kinds[] = {"galloc", "gmalloc", "HeapAlloc", "VirtualAlloc"};
    intptr_t slide = _dyld_get_image_vmaddr_slide(0);
    fprintf(f, "# at t=%.0f s: guest allocations by host call site, most bytes first (cumulative, not live)\n", t);
    for (int k = 0; k < 25 && copy[k].ret; ++k) {
        if (copy[k].kind >= 2) {                                   // guest call site
            uint32_t ga = (uint32_t)(uintptr_t)copy[k].ret, off = 0;
            const char *sym = diag_symbol(ga, &off);
            fprintf(f, "%-12s %10llu calls %12llu bytes  guest %08X %s+0x%X\n", kinds[copy[k].kind], (unsigned long long)copy[k].calls,
                    (unsigned long long)copy[k].bytes, ga, sym ? sym : "?", off);
            continue;
        }
        Dl_info di = {0};
        dladdr(copy[k].ret, &di);
        fprintf(f, "%-12s %10llu calls %12llu bytes  %p (image 0x%llx) %s+0x%lx\n", kinds[copy[k].kind], (unsigned long long)copy[k].calls,
                (unsigned long long)copy[k].bytes, copy[k].ret, (unsigned long long)((uintptr_t)copy[k].ret - slide),
                di.dli_sname ? di.dli_sname : "?", di.dli_saddr ? (long)((char *)copy[k].ret - (char *)di.dli_saddr) : 0L);
    }
    fclose(f);
}

// BOOT_DIAG: one line per second in heap.log with both heaps' live/page/high-water sizes and the process RSS.
static void heap_log_tick(void) {
    static uint64_t last;
    static FILE *f;
    if (!diag_on) return;
    uint64_t now = host_ticks();
    if (now - last < 1000000000ull) return;
    last = now;
    if (!f) {
        char path[4096];
        snprintf(path, sizeof path, "%s/heap.log", diag_dir);
        if (!(f = fopen(path, "w"))) return;
        fprintf(f, "# t_s rss_MB | process: live_MB blocks pages_MB peak_pages_MB hwm_MB allocs frees reallocs bad | resource: same\n");
    }
    mach_task_basic_info_data_t ti;
    mach_msg_type_number_t cnt = MACH_TASK_BASIC_INFO_COUNT;
    uint64_t rss = task_info(mach_task_self(), MACH_TASK_BASIC_INFO, (task_info_t)&ti, &cnt) == KERN_SUCCESS ? ti.resident_size : 0;
    static uint64_t t0;
    if (!t0) t0 = now;
    fprintf(f, "%.0f %llu", (now - t0) / 1e9, (unsigned long long)rss >> 20);
    Heap *hs[2] = {proc_heap, res_heap};
    for (int k = 0; k < 2; ++k) {
        HeapStats st = heap_stats(hs[k]);
        fprintf(f, " | %.1f %llu %.1f %.1f %.1f %llu %llu %llu %llu", st.live_bytes / 1048576.0, (unsigned long long)st.live_blocks,
                st.pages_used * (double)PAGE / 1048576.0, st.pages_peak * (double)PAGE / 1048576.0, st.hwm_bytes / 1048576.0,
                (unsigned long long)st.allocs, (unsigned long long)st.frees, (unsigned long long)st.reallocs, (unsigned long long)st.bad_frees);
    }
    fputc('\n', f);
    fflush(f);
    static uint64_t last_sites;
    if (now - last_sites >= 30000000000ull) { last_sites = now; write_sites((now - t0) / 1e9); }
}

// diag.c's stats.log hooks: guest memory taken from each heap's range (pages of slabs and large blocks).
uint32_t gmalloc_used(void) { heaps_init(); return (uint32_t)(heap_stats(res_heap).pages_used * PAGE); }
uint32_t heap_used(void) { heaps_init(); return (uint32_t)(heap_stats(proc_heap).pages_used * PAGE); }

uint32_t gmalloc(uint32_t n) {
    heaps_init();
    heap_note(1, __builtin_return_address(0), n);
    uint32_t a = heap_alloc(res_heap, n ? n : 1);
    if (!a) full(res_heap, n);
    heap_log_tick();
    return a;
}
uint32_t gmalloc_try(uint32_t n) {
    heaps_init();
    heap_note(1, __builtin_return_address(0), n);
    uint32_t a = heap_alloc(res_heap, n ? n : 1);
    heap_log_tick();
    return a;
}
void gfree(uint32_t a) { if (a) { heaps_init(); heap_free(res_heap, a); } }

static uint32_t proc_alloc(uint32_t n) {
    uint32_t a = heap_alloc(proc_heap, n);
    if (!a && n < 0x10000000u) full(proc_heap, n);   // a huge request just fails, as on Windows
    heap_log_tick();
    return a;
}

// KERNEL32 heap functions. Every heap handle is served by the one process heap: the game uses the CRT heap
// (HeapCreate) and the process heap, and never relies on HeapDestroy freeing blocks.
int win32_heap(Cpu *c, const char *fn) {
    heaps_init();
    if (strncmp(fn, "Heap", 4) && strncmp(fn, "Global", 6) && strcmp(fn, "GetProcessHeap")) return 0;
    if (diag_on && (!strcmp(fn, "HeapAlloc") || !strcmp(fn, "HeapReAlloc") || !strcmp(fn, "GlobalAlloc")))
        heap_note(2, (void *)(uintptr_t)M32(c->esp), fn[0] == 'G' ? arg(c, 1) : fn[4] == 'A' ? arg(c, 2) : arg(c, 3));
    if (!strcmp(fn, "HeapCreate")) RET(PROCESS_HEAP_HANDLE, 3);
    if (!strcmp(fn, "GetProcessHeap")) RET(PROCESS_HEAP_HANDLE, 0);
    if (!strcmp(fn, "HeapDestroy")) RET(1, 1);
    if (!strcmp(fn, "HeapAlloc")) RET(proc_alloc(arg(c, 2)), 3);                     // (heap, flags, size)
    if (!strcmp(fn, "HeapFree")) { heap_free(proc_heap, arg(c, 2)); RET(1, 3); }       // (heap, flags, ptr)
    if (!strcmp(fn, "HeapSize")) RET(heap_size(proc_heap, arg(c, 2)), 3);             // (heap, flags, ptr)
    if (!strcmp(fn, "HeapValidate")) RET(!arg(c, 2) || heap_owns(proc_heap, arg(c, 2)), 3);
    if (!strcmp(fn, "HeapReAlloc")) {                                                  // (heap, flags, ptr, size)
        uint32_t flags = arg(c, 1), p = arg(c, 2), n = arg(c, 3), r;
        if (!p) r = proc_alloc(n);
        else {
            r = heap_realloc(proc_heap, p, n, (flags & HEAP_REALLOC_IN_PLACE_ONLY) != 0);
            if (!r && !(flags & HEAP_REALLOC_IN_PLACE_ONLY) && heap_owns(proc_heap, p) && n < 0x10000000u) full(proc_heap, n);
        }
        if (!r) last_error = 8;                                                        // ERROR_NOT_ENOUGH_MEMORY
        heap_log_tick();
        RET(r, 4);
    }
    if (!strcmp(fn, "GlobalAlloc")) RET(proc_alloc(arg(c, 1) ? arg(c, 1) : 1), 2);    // (flags, size): GMEM_FIXED
    if (!strcmp(fn, "GlobalFree")) { heap_free(proc_heap, arg(c, 0)); RET(0, 1); }
    return 0;
}
#endif
