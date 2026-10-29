// jdlz_test.c - standalone check of jdlz.h against every JDLZ blob in the X360 vinyl packs (no game needed).
//   cc -O1 -o /tmp/jdlz_test runtime/jdlz_test.c && /tmp/jdlz_test 'OWNER_X360_EXTRACT/files/CARS'
// The reference is the byte-by-byte decoder that BOOT_DIAG runs proved identical to the game's LZDecompress
// (vinyls.m checks its first 40 blobs against the guest call); jdlz_native's chunked match copies must give the
// same bytes. Also prints the decode rate of both, and decodes every HUFF blob with huff.h (size against the table,
// rate; the byte-for-byte check of HUFF against the game's own code is huff_test.py).
#include <dirent.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

#include "huff.h"
#include "jdlz.h"

static uint32_t jdlz_ref(const uint8_t *in, uint8_t *out) {   // JLZDecompress (0064DB40) as the game runs it
    uint32_t usize, csize;
    memcpy(&usize, in + 8, 4); memcpy(&csize, in + 0xc, 4);
    const uint8_t *p = in + 0x12;
    uint8_t *o = out, *end = out + usize;
    int32_t n = (int32_t)csize - 0x12;
    uint32_t f1 = in[0x10] | 0x100, f2 = in[0x11] | 0x100;
    while (n != 0) {
        if (!(f1 & 1)) {
            if (o < end) *o++ = *p;
            ++p; --n;
        } else {
            uint32_t b0 = p[0], b1 = p[1], len, dist;
            if (!(f2 & 1)) { len = (b0 & 0x1f) + 3; dist = ((b0 & 0xe0) << 3 | b1) + 0x11; }
            else { len = ((b0 & 0xf0) << 4 | b1) + 3; dist = (b0 & 0xf) + 1; }
            for (uint32_t i = 0; i < len; ++i) o[i] = o[(int32_t)i - (int32_t)dist];
            o += len;
            f2 >>= 1;
            p += 2; n -= 2;
        }
        f1 >>= 1;
        if (f1 == 1) { f1 = *p++ | 0x100; --n; }
        if (f2 == 1) { f2 = *p++ | 0x100; --n; }
    }
    return usize;
}

static double now(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec + t.tv_nsec / 1e9; }

int main(int argc, char **argv) {
    const char *dir = argc > 1 ? argv[1] : "../x360/files/CARS";
    DIR *d = opendir(dir);
    if (!d) { printf("no %s\n", dir); return 1; }
    uint64_t blobs = 0, bad = 0, other = 0, bytes = 0, hblobs = 0, hbad = 0, hbytes = 0;
    double tref = 0, tnat = 0, thuff = 0;
    uint8_t *a = malloc(64 << 20), *b = malloc(64 << 20);   // touched once, so page faults don't count in the timings
    memset(a, 0, 64 << 20); memset(b, 0, 64 << 20);
    struct dirent *e;
    while ((e = readdir(d))) {
        if (e->d_name[0] == '.') continue;
        char path[2048];
        snprintf(path, sizeof path, "%s/%s/VINYLS.BIN", dir, e->d_name);
        FILE *f = fopen(path, "rb");
        if (!f) continue;
        fseek(f, 0, SEEK_END);
        long sz = ftell(f);
        uint8_t *buf = malloc((size_t)sz);
        fseek(f, 0, SEEK_SET);
        if (fread(buf, 1, (size_t)sz, f) != (size_t)sz) { fclose(f); free(buf); continue; }
        fclose(f);
        // the compressed-TPK table, as vinyls.m's index_pack: 0xB3300000 > 0xB3310000 > 0x33310003 (24-byte entries)
        uint32_t h[2];
        long p = 8, end;
        memcpy(h, buf, 8);
        end = 8 + (long)h[1];
        while (p + 8 <= end) {
            memcpy(h, buf + p, 8);
            if (h[0] == 0xB3310000u) {
                long q = p + 8, qend = q + (long)h[1];
                while (q + 8 <= qend) {
                    memcpy(h, buf + q, 8);
                    if (h[0] == 0x33310003u)
                        for (uint32_t k = 0; k < h[1] / 24; ++k) {
                            uint32_t t[6];
                            memcpy(t, buf + q + 8 + 24 * k, 24);
                            if (!t[0] || (long)t[1] + (long)t[2] > sz || t[3] > (64u << 20)) continue;
                            const uint8_t *in = buf + t[1];
                            if (!memcmp(in, "HUFF", 4) && in[4] == 1) {
                                double t0 = now();
                                uint32_t n = huff_native(in, t[2], b, 64u << 20);
                                thuff += now() - t0;
                                ++hblobs; hbytes += n;
                                if (n != t[3]) { if (hbad++ < 10) printf("HUFF FAILED: %s hash %08X (%u / %u bytes)\n", path, t[0], n, t[3]); }
                                continue;
                            }
                            if (memcmp(in, "JDLZ", 4) || in[4] != 2) { ++other; continue; }
                            double t0 = now();
                            uint32_t n1 = jdlz_ref(in, a);
                            double t1 = now();
                            uint32_t n2 = jdlz_native(in, t[2], b, 64u << 20);
                            double t2 = now();
                            tref += t1 - t0; tnat += t2 - t1;
                            ++blobs; bytes += n1;
                            if (n1 != n2 || n1 != t[3] || memcmp(a, b, n1)) { if (bad++ < 10) printf("DIFFERENT: %s hash %08X (%u / %u / %u bytes)\n", path, t[0], n1, n2, t[3]); }
                        }
                    q += 8 + (long)h[1];
                }
                break;
            }
            p += 8 + (long)h[1];
        }
        free(buf);
    }
    closedir(d);
    printf("%llu JDLZ blobs (%llu MB unpacked): %llu differ; %llu other\n", (unsigned long long)blobs, (unsigned long long)(bytes >> 20),
           (unsigned long long)bad, (unsigned long long)other);
    printf("reference %.0f MB/s (%.2f ms per 4 MB), jdlz_native %.0f MB/s (%.2f ms per 4 MB)\n", bytes / 1e6 / tref, 4.194e3 * tref / (bytes / 1e6),
           bytes / 1e6 / tnat, 4.194e3 * tnat / (bytes / 1e6));
    printf("%llu HUFF blobs (%llu MB unpacked): %llu not decoded to the table's size; huff_native %.0f MB/s (%.2f ms per 4 MB)\n",
           (unsigned long long)hblobs, (unsigned long long)(hbytes >> 20), (unsigned long long)hbad, hbytes / 1e6 / thuff,
           4.194e3 * thuff / (hbytes / 1e6));
    return bad != 0 || hbad != 0;
}
