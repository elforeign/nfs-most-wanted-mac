#!/usr/bin/env python3
"""Offline production hd_read: byte parity, cache hits/invalidation/eviction,
GIL ownership, concurrent readers, failures and host timings. No game launch.
Optional --baseline <old x360_textures.m> must fail the cache/GIL assertions.
"""
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''
#include <stdio.h>
#include <time.h>
#include <sys/stat.h>
#include <unistd.h>
#include <errno.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
static unsigned checks, failures, reads, locked_reads;
static int held = 1, interrupt_read;
#define CHECK(x) do { checks++; if (!(x)) { failures++; fprintf(stderr,"FAIL %d: %s\n",__LINE__,#x); } } while (0)
static ssize_t test_read(int fd, void *p, size_t n) {
    reads++; locked_reads += held;
    if (interrupt_read) { interrupt_read = 0; errno = EINTR; return -1; }
    return read(fd,p,n);
}
#define read test_read
#include "hd_file_cache.h"
static uint8_t *G;
static uint32_t gmalloc(uint32_t size) { CHECK(held && size <= (64u << 20)); return 4096; }
static void gfree(uint32_t g) { CHECK(held && g == 4096); }
static void gil_release(void) { CHECK(held); held = 0; }
static void gil_acquire(void) { CHECK(!held); held = 1; }
@WRAPPER@
#undef read
static double ms(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC,&t); return t.tv_sec*1e3+t.tv_nsec/1e6; }
static void fixture(const char *path, unsigned n, uint8_t v) {
    FILE *f=fopen(path,"wb"); CHECK(f);
    for (unsigned i=0;i<n;i++) fputc((i*37+v)&255,f);
    CHECK(!fclose(f));
}
static void *reader(void *arg) {
    for (unsigned i=0;i<100;i++) {
        HdFile *f=hd_file_get(arg);
        if (!f || f->size!=98304 || f->bytes[100]!=(uint8_t)(100*37+7)) abort();
        hd_file_release(f);
    }
    return NULL;
}
int main(int argc, char **argv) {
    G=malloc((64u << 20)+4096); CHECK(G);
    if (argc>2 && !strcmp(argv[1],"perf")) {
        double cold=0,warm=0; size_t bytes=0; unsigned files=0;
        for (int i=2;i<argc;i++) {
            uint32_t n=0,m=0; double t=ms(); uint32_t a=hd_read(argv[i],&n); cold+=ms()-t;
            CHECK(a); uint8_t *ref=malloc(n); memcpy(ref,G+a,n); gfree(a);
            unsigned before=reads; t=ms(); a=hd_read(argv[i],&m); warm+=ms()-t;
            CHECK(a && n==m && !memcmp(ref,G+a,n)); CHECK(reads==before);
            bytes+=n; files++; free(ref); if(a) gfree(a);
        }
        printf("hd_read %u files, %zu bytes: first %.3f ms; cached %.3f ms; host reads %u, under GIL %u\n",files,bytes,cold,warm,reads,locked_reads);
    } else {
        char a[4096],b[4096],missing[4096];
        snprintf(a,sizeof a,"%s/a.dds",argv[1]); snprintf(b,sizeof b,"%s/b.dds",argv[1]);
        snprintf(missing,sizeof missing,"%s/missing.dds",argv[1]);
        fixture(a,98304,7); fixture(b,98304,9);
        uint32_t n=42; CHECK(!hd_read(missing,&n) && n==42);
        uint32_t g=hd_read(a,&n); CHECK(g && n==98304);
        for (unsigned i=0;g && i<n;i++) CHECK(G[g+i]==(uint8_t)(i*37+7));
        if (g) gfree(g);
        unsigned before=reads; g=hd_read(a,&n); CHECK(g && reads==before); if(g) gfree(g);
        held=0; HdFile *pinned=hd_file_get(a); held=1; CHECK(pinned);
        g=hd_read(b,&n); CHECK(g && hd_file_bytes<=HD_FILE_CACHE_LIMIT); if(g) gfree(g);
        CHECK(pinned && pinned->bytes[123]==(uint8_t)(123*37+7)); hd_file_release(pinned);
        before=reads; g=hd_read(a,&n); CHECK(g && reads>before); if(g) gfree(g); // LRU eviction
        fixture(a,98304,11); before=reads; g=hd_read(a,&n);
        CHECK(g && reads>before && G[g]==11); if(g) gfree(g); // same-size rewrite invalidates
        fixture(a,127,0); n=42; CHECK(!hd_read(a,&n) && n==42);
        fixture(a,98304,7); interrupt_read=1; g=hd_read(a,&n); CHECK(g); if(g) gfree(g);
        // Concurrent host-only callers can pin and evict while another copies.
        held=0; pthread_t workers[4];
        for (unsigned i=0;i<4;i++) CHECK(!pthread_create(&workers[i],NULL,reader,a));
        for (unsigned i=0;i<4;i++) CHECK(!pthread_join(workers[i],NULL));
        held=1;
        FILE *large=fopen(b,"wb"); CHECK(large && !ftruncate(fileno(large),(64u<<20)+1)); fclose(large);
        n=42; CHECK(!hd_read(b,&n) && n==42);
        printf("host reads %u, under GIL %u; cache %zu/%u bytes\n",reads,locked_reads,hd_file_bytes,HD_FILE_CACHE_LIMIT);
    }
    CHECK(!locked_reads); CHECK(held);
    while(hd_files) hd_file_remove(&hd_files);
    CHECK(!hd_file_bytes); free(G);
    printf("%s: %u checks, %u failures\n",failures?"FAIL":"PASS",checks,failures);
    return failures!=0;
}
'''


def run(source, directory, limit, args, expected=0):
    wrapper = source.split('static uint32_t hd_read(', 1)[1].split('// ---- verdict cache:', 1)[0]
    code = Path(directory) / 'hd_test.c'
    code.write_text(HARNESS.replace('@WRAPPER@', 'static uint32_t hd_read(' + wrapper))
    binary = Path(directory) / 'hd_test'
    subprocess.run(['clang', '-std=c11', '-D_DARWIN_C_SOURCE', f'-DHD_FILE_CACHE_LIMIT={limit}',
                    '-O2', '-g', '-fsanitize=address,undefined', '-I', str(ROOT / 'runtime'),
                    str(code), '-o', str(binary)], check=True, capture_output=True)
    result = subprocess.run([str(binary), *map(str, args)], capture_output=True, text=True)
    print(result.stdout, end='')
    if result.returncode != expected:
        print(result.stderr[-3000:])
        raise AssertionError(f'exit {result.returncode}, expected {expected}')
    assert 'AddressSanitizer' not in result.stderr and 'runtime error:' not in result.stderr, result.stderr[-3000:]
    if expected:
        assert 'reads==before' in result.stderr and '!locked_reads' in result.stderr
        print('old implementation: cache/GIL assertions rejected as expected')


def main():
    source = (ROOT / 'runtime/x360_textures.m').read_text()
    with tempfile.TemporaryDirectory(prefix='nfsmw-hd-read-') as directory:
        run(source, directory, 128 << 10, [directory])
        files = sorted((ROOT.parent / 'x360/hd_textures_mips_alpha').glob('*.dds'))[:64]
        # Absent-DDS proof: old assertion exits 1; synthetic checks + SKIP exit 3.
        if not files:
            print('SKIP: real DDS performance fixtures are absent (synthetic checks passed)')
            sys.exit(3)
        run(source, directory, 64 << 20, ['perf', *files])
        if len(sys.argv) == 3 and sys.argv[1] == '--baseline':
            run(Path(sys.argv[2]).read_text(), directory, 64 << 20, ['perf', *files], expected=1)


if __name__ == '__main__':
    main()
