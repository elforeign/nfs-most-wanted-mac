// Resolve absent kit/style models against the pack's own geometry, before streaming.
// Standard C only: the same resource policy is usable by every host.
#ifndef NFS_CAR_KIT_FALLBACK_H
#define NFS_CAR_KIT_FALLBACK_H
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct { uint32_t target, row; } CarKitMap;

static uint32_t car_kit_hash(const char *s) {
    uint32_t h = UINT32_MAX;
    while (*s) h = h * 33u + (unsigned char)*s++;
    return h;
}

static int car_kit_read(FILE *f, uint32_t at, void *out, uint32_t size, uint32_t end) {
    return at <= end && size <= end - at && !fseek(f, (long)at, SEEK_SET) &&
           fread(out, 1, size, f) == size;
}

static int car_kit_compare(const void *left, const void *right) {
    const CarKitMap *a = left, *b = right;
    return (a->target > b->target) - (a->target < b->target);
}

static int car_kit_present(const uint32_t *rows, unsigned count, uint32_t hash) {
    unsigned lo = 0, hi = count;
    while (lo < hi) {
        unsigned mid = lo + (hi - lo) / 2;
        if (rows[6*mid] < hash) lo = mid + 1; else hi = mid;
    }
    return lo < count && rows[6*lo] == hash;
}

// Returns the repaired row count, or -1 when this is not a decoded output pack.
// Read only indexed model headers, never meshes. Commit changes only after all
// ranges validate; compressed/foreign packs retain their existing mapping.
static int car_kit_fallback(FILE *f, const char *space, uint32_t *map, unsigned count) {
    uint32_t h[2], end, table = 0, table_size = 0;
    if (!count || count > 8192 || !car_kit_read(f, 0, h, 8, 512u << 20) ||
        h[0] != 0x80134000u || h[1] > (512u << 20) - 8) return -1;
    end = h[1] + 8;
    if (fseek(f, 0, SEEK_END) || ftell(f) < (long)end) return -1;
    for (uint32_t at = 8; at < end;) {
        if (!car_kit_read(f, at, h, 8, end) || h[1] > end - at - 8) return -1;
        uint32_t next = at + 8 + h[1];
        if (h[0] == 0x80134001u) {
            for (uint32_t part = at + 8; part < next;) {
                if (!car_kit_read(f, part, h, 8, next) || h[1] > next - part - 8) return -1;
                if (h[0] == 0x134004u) {
                    if (table) return -1;
                    table = part + 8; table_size = h[1];
                }
                part += 8 + h[1];
            }
            break;
        }
        at = next;
    }
    if (!table || !table_size || table_size % 24 || table_size / 24 > 8192) return -1;
    unsigned solids = table_size / 24;
    uint32_t *rows = malloc(table_size), *resolved = malloc(count * sizeof *resolved);
    CarKitMap *sorted = malloc(count * sizeof *sorted);
    int result = -1;
    if (!rows || !resolved || !sorted || !car_kit_read(f, table, rows, table_size, end)) goto done;
    for (unsigned i = 0; i < count; ++i) {
        sorted[i] = (CarKitMap){map[2*i+1], i}; resolved[i] = map[2*i+1];
    }
    qsort(sorted, count, sizeof *sorted, car_kit_compare);
    for (unsigned i = 0; i < solids; ++i) {
        const uint32_t *r = rows + 6*i;
        if ((i && r[0] <= rows[6*i-6]) || r[1] > end || r[2] > end - r[1] ||
            r[2] < 8 || r[2] != r[3] || r[4] || r[5]) goto done;
    }
    char stock_prefix[64];
    int prefix_size = snprintf(stock_prefix, sizeof stock_prefix, "%s_KIT00_", space);
    if (prefix_size < 0 || (unsigned)prefix_size >= sizeof stock_prefix) goto done;
    for (unsigned i = 0; i < solids; ++i) {
        const uint32_t *r = rows + 6*i;
        uint32_t top = r[1] + r[2];
        int named = 0;
        if (!car_kit_read(f, r[1], h, 8, top) || h[0] != 0x80134010u || h[1] != r[2] - 8) goto done;
        for (uint32_t at = r[1] + 8; at < top;) {
            if (!car_kit_read(f, at, h, 8, top) || h[1] > top - at - 8) goto done;
            uint32_t next = at + 8 + h[1];
            if (h[0] == 0x134011u) {
                named = 1;
                uint32_t header = (at + 23u) & ~15u, hash;
                char name[128], candidate[160];
                if (header > next || next - header <= 160 ||
                    !car_kit_read(f, header + 16, &hash, 4, next)) goto done;
                uint32_t size = next - header - 160;
                if (size > sizeof name) size = sizeof name;
                if (!car_kit_read(f, header + 160, name, size, next) ||
                    !memchr(name, 0, size) || hash != r[0] || car_kit_hash(name) != hash) goto done;
                if (strncmp(name, stock_prefix, (size_t)prefix_size)) break;
                const char *suffix = name + prefix_size;
                // Random donor parts also request STYLE models. Preserve the
                // entire slot/LOD suffix, including multiword slots; authored
                // targets win over the pack's own stock model.
                for (unsigned family = 0; family < 3; ++family) for (unsigned kit = 0; kit < 100; ++kit) {
                    const char *kind = family == 2 ? "STYLE" : family ? "KITW" : "KIT";
                    int length = snprintf(candidate, sizeof candidate, "%s_%s%02u_%s", space,
                                          kind, kit, suffix);
                    if (length < 0 || (unsigned)length >= sizeof candidate) goto done;
                    uint32_t missing = car_kit_hash(candidate);
                    if (car_kit_present(rows, solids, missing)) continue;
                    unsigned lo = 0, hi = count;
                    while (lo < hi) {
                        unsigned mid = lo + (hi - lo) / 2;
                        if (sorted[mid].target < missing) lo = mid + 1; else hi = mid;
                    }
                    for (; lo < count && sorted[lo].target == missing; ++lo)
                        resolved[sorted[lo].row] = hash;
                }
                break;
            }
            at = next;
        }
        if (!named) goto done;
    }
    result = 0;
    for (unsigned i = 0; i < count; ++i) if (map[2*i+1] != resolved[i]) {
        map[2*i+1] = resolved[i]; ++result;
    }
done:
    free(rows); free(resolved); free(sorted);
    return result;
}
#endif
