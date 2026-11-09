"""Public packed_display against a loader-shaped language resource, no game.

Proof: fixed check exits 0; original 0091CF7C check exits 1 and reports
"car85: invalid language histogram" (the production fail() child exits 2).
Built with -O2 -DNFS_PUBLIC=1, plus ASan/UBSan. The real translated loader
0057E960 relocates distinct histogram, record and string-pool addresses; the
real translated decoder 0056BFC0 round-trips Citroën and a two-byte code.

Address audit (packed_display and register_names language accesses):
0091CF78 count MATCH 0057E973 / SearchForString 0056BB80;
0091CF80 records MATCH 0057E988 / SearchForString 0056BB80;
0091CF84 histogram MATCH 0057E97D / PackedStringToWideString 0057E860;
0091CF7C histogram MISMATCH: 0057E991/0057E997 relocate the string pool;
0091CF88 flag MATCH UnpackString 0056BFE6 (nonzero bypasses histogram);
M32(histogram) count MATCH PackString 0056BE80; M16(histogram+4+2*i)
and the prefix lookup MATCH UnpackString 0056BFF8 / 0056C021.
M32(record+0/+4), stride 8 MATCH SearchForString / loader 0057E9B0.
"""
from pathlib import Path
import hashlib
import os
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL_GLOBAL = 'histogram=M32(0x0091CF7Cu)'
ORIGINAL_SHA256 = '6cdd702cb4d4baa1549bf760f1448441ecd261f72f58a02cb08c84d8cfebdbff'


def function(source, name):
    match = re.search(r'^(?:static )?[^\n]+\b' + re.escape(name) + r'\([^\n]*\) \{', source, re.M)
    assert match, name
    return source[match.start():source.index('\n}', match.end()) + 2]


HARNESS = r'''
#include <stdlib.h>
#include <CoreFoundation/CoreFoundation.h>
#include "host.h"
#include "funcs.h"
#if !defined(NFS_PUBLIC) || NFS_PUBLIC != 1
#error public build required
#endif
uint8_t *G;
int diag_on;
static unsigned checks, failures;
typedef struct { char display_name[513]; uint32_t display_string, string; } Addition;
#define CHECK(x) do { ++checks; if (!(x)) { ++failures; fprintf(stderr,"FAIL line %d: %s\n",__LINE__,#x); } } while (0)
uint32_t gmalloc(uint32_t n) { CHECK(n==513); return 0x30000; }
void diag_event(const char *fmt, ...) { (void)fmt; }
PRODUCTION
TRANSLATED
static void roundtrip(uint32_t packed, const uint16_t *expected, unsigned n) {
    Cpu c={0}; c.esp=0x80000; c.ecx=M32(0x91CF84);
    M32(c.esp+4)=0x40000; M32(c.esp+8)=256; M32(c.esp+12)=packed;
    f_0056BFC0(&c);
    CHECK(c.eax==1 && c.esp==0x80010);
    CHECK(!memcmp(G+0x40000,expected,n*sizeof *expected));
}
int main(void) {
    G=calloc(1,0xa00000); if (!G) return 2;
    // Chunk header +8 is the base for all three relative offsets, exactly
    // as LoaderLanguage 0057E96C..0057E997. No aliased pool/histogram fixture.
    const uint32_t chunk=0x10000, base=chunk+8;
    const uint32_t histogram=base+0x40, records=base+0x400, pool=base+0x500;
    M32(chunk)=0x39000; M32(chunk+4)=0x600;
    M32(base)=histogram-base; M32(base+4)=1;
    M32(base+8)=records-base; M32(base+12)=pool-base;
    M32(histogram)=300;
    M16(histogram+4+2*128)=0xEB; // ë has a single-byte histogram code
    M16(histogram+4+2*129)=2;    // prefix for indices 256..383
    M16(histogram+4+2*299)=0x3A9;
    M32(records)=0x12345678; M32(records+4)=0;
    memcpy(G+pool,"Stock",6);  // first dword exceeds the histogram count bound
    Cpu c={0}; c.esp=0x80000; M32(c.esp+4)=chunk; f_0057E960(&c);
    CHECK(c.eax==1 && c.esp==0x80004);
    CHECK(M32(0x91CF78)==1 && M32(0x91CF80)==records);
    CHECK(M32(0x91CF84)==histogram && M32(0x91CF7C)==pool);
    CHECK(M32(records+4)==pool && M32(pool)>65536);
    Addition a={0}; strcpy(a.display_name,"Citro\xC3\xABn");
    uint32_t packed=packed_display(&a);
    CHECK(packed==a.display_string && !memcmp(G+packed,"Citro\x80n",8));
    const uint16_t citroen[]={'C','i','t','r','o',0xEB,'n',0};
    roundtrip(packed,citroen,8);
    strcpy(a.display_name,"\xCE\xA9"); packed=packed_display(&a);
    CHECK(!memcmp(G+packed,"\x81\xAB",3));
    const uint16_t omega[]={0x3A9,0}; roundtrip(packed,omega,2);
    // DisableWideStringHistogram keeps Latin-1 bytes; the pool stays distinct.
    M32(0x91CF88)=1; strcpy(a.display_name,"Citro\xC3\xABn");
    packed=packed_display(&a); CHECK(!memcmp(G+packed,"Citro\xEBn",8));
    roundtrip(packed,citroen,8);
    M32(0x91CF88)=0;
    CHECK(!strcmp((char *)G+pool,"Stock") && M32(records+4)==pool);
    printf("public language: %u checks, %u failures; Citroën round-trip PASS\n",checks,failures);
    free(G); return failures!=0;
}
'''


def check(binary):
    run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20,
                         env={**os.environ, 'ASAN_OPTIONS': 'detect_leaks=0'})
    assert 'AddressSanitizer' not in run.stderr and 'runtime error:' not in run.stderr, run.stderr[-2000:]
    print(run.stdout.strip() or run.stderr.strip())
    return (0 if run.returncode == 0 else 1), run


def main():
    source = (ROOT / 'runtime/car85.c').read_text()
    production = function(source, 'fail') + '\n' + function(source, 'packed_display')
    paths = [(ROOT / 'recomp/out/speed_21.c', 'f_0057E960'),
             (ROOT / 'recomp/out/speed_20.c', 'f_0056BFC0')]
    if any(not path.is_file() for path, _ in paths):
        print('SKIP: build translated language loader/decoder first')
        return 3
    translated = '\n'.join(function(path.read_text(), name) for path, name in paths)
    with tempfile.TemporaryDirectory(prefix='car85-language-') as directory:
        tmp = Path(directory)

        def build(code, label):
            unit, binary = tmp / 'test.c', tmp / label
            unit.write_text(HARNESS.replace('PRODUCTION', code).replace('TRANSLATED', translated))
            result = subprocess.run(['clang', '-std=c11', '-O2', '-g', '-DNFS_PUBLIC=1',
                                     '-fsanitize=address,undefined', '-Iruntime', '-Irecomp', '-Irecomp/out',
                                     str(unit), '-framework', 'CoreFoundation', '-o', str(binary)],
                                    cwd=ROOT, capture_output=True, text=True, timeout=60)
            assert result.returncode == 0, result.stderr[-3000:]
            return binary

        fixed_status, _ = check(build(production, 'actual'))
        if fixed_status:
            return fixed_status
        # Restore only the original erroneous global, preserving the real
        # packer and fail() so the control reproduces the reported crash.
        assert hashlib.sha256(ORIGINAL_GLOBAL.encode()).hexdigest() == ORIGINAL_SHA256
        old, replacements = re.subn(r'histogram=M32\(0x0091CF84u\)', ORIGINAL_GLOBAL, production)
        assert replacements == 1
        old_status, old_run = check(build(old, 'old-pointer'))
        assert old_status == 1 and old_run.returncode == 2
        assert old_run.stderr.strip() == 'car85: invalid language histogram', old_run.stderr
        print(f'fixed check exit={fixed_status}; old-pointer check exit={old_status} (child exit=2)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
