"""Synthetic PE layout regression; no game bytes or game entry execution.

Windows: fixed loader exit 0, pre-rounding loader exit 1 on the same fixture.
Small alignment: fixed loader exit 0, page-minimum mutation exit 1.
Build environment: x64 target gate passes; VCToolsInstallDir-only gate fails.
Mac: actual boot.c mapping block exit 0; truncated raw-copy mutant exit 1.
SizeOfImage 0x678e4e, SectionAlignment 0x1000, unnamed fifth section
RVA 0x638000 / virtual size 0x40e4e / raw size 0x41000.
Discovered by runtime/offline_tests.sh; no game entry point is executed.
"""
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent
WIN = ROOT / 'runtime/win'


def fixture():
    # Only the fifth section models the reported repack; the first four are
    # synthetic disjoint spans. The file contains headers and zero padding.
    data = bytearray(0x1000 + 0x41000)
    def p16(at, value):
        struct.pack_into('<H', data, at, value)
    def p32(at, value):
        struct.pack_into('<I', data, at, value)
    p16(0, 0x5a4d)
    p32(0x3c, 0x80)
    p32(0x80, 0x4550)
    p16(0x84, 0x14c)
    p16(0x86, 5)
    p16(0x94, 224)
    opt = 0x98
    p16(opt, 0x10b)
    for offset, value in ((16, 0x3c4040), (28, 0x400000), (32, 0x1000),
                          (36, 0x200), (56, 0x678e4e), (60, 0x1000)):
        p32(opt + offset, value)
    for i in range(5):
        section = opt + 224 + i * 40
        if i < 4:
            data[section:section+8] = f'.s{i}'.encode().ljust(8, b'\0')
        vs, va, rs, raw = ((0x40e4e, 0x638000, 0x41000, 0x1000) if i == 4
                           else (0x1000, 0x1000 + i * 0x1000, 0, 0))
        for offset, value in ((8, vs), (12, va), (16, rs), (20, raw)):
            p32(section + offset, value)
    return data


def small_fixture():
    # Low-alignment images use matching file offsets and RVAs.
    data = bytearray(0x3c4200)
    data[:0x200] = fixture()[:0x200]
    struct.pack_into('<H', data, 0x86, 1)
    opt = 0x98
    for offset, value in ((32, 0x200), (36, 0x200), (56, 0x3c4200), (60, 0x200)):
        struct.pack_into('<I', data, opt + offset, value)
    section = opt + 224
    for offset, value in ((8, 0x200), (12, 0x3c4000), (16, 0x200), (20, 0x3c4000)):
        struct.pack_into('<I', data, section + offset, value)
    data[0x3c4040] = 0x5a
    return data


def check_build_environment():
    gate = 'if /i "%VSCMD_ARG_TGT_ARCH%"=="x64" goto toolsready'
    old = 'if defined VCToolsInstallDir goto toolsready'
    batch = (ROOT / 'recomp/win_build.bat').read_text()
    def valid(text):
        return (text.count(gate) == 1 and old not in text and
                text.index(gate) < text.index('call "%VCVARS%"') < text.index(':toolsready'))
    assert valid(batch), 'build environment must skip vcvars64 only for target x64'
    assert not valid(batch.replace(gate, old))
    print('build environment: x64 target gate PASS; old tools-directory gate FAIL')


COMMON = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
uint8_t *G;
static void die(const char *s) { fprintf(stderr,"%s\n",s); exit(2); }
static int check_image(const uint8_t *pe) {
    return memcmp(G+0x400000,pe,0x1000)==0 &&
           memcmp(G+0xa38000,pe+0x1000,0x41000)==0;
}
'''

WINDOWS = r'''
#include "host.h"
static void *reserve_guest(void) {
    void *p=mmap(0,1ull<<32,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);
    if (p==MAP_FAILED) die("reserve");
    memset((uint8_t *)p+0x400000,0xa5,0x679000);
    return p;
}
const uint32_t speed_entry=0x7c4040;
const struct ImportSlot speed_imports[]={{0x401000,"fixture"}};
const unsigned speed_import_count=1;
static uint64_t committed;
int win_vm_commit(uint32_t at,uint64_t size) {
    if (at!=0x400000) return 0;
    committed=size; return 1;
}
int win_import_table_valid(const struct ImportSlot *s,unsigned n) {
    return s==speed_imports && n==1;
}
static int load(uint8_t *pe,size_t size) {
    G=reserve_guest();
    int ok=win_load_pe(pe,size) && committed==0x679000 && check_image(pe);
    munmap(G,1ull<<32); return ok;
}
'''

SMALL_COMMON = COMMON[:COMMON.index('static int check_image')] + r'''
static int check_image(const uint8_t *pe) {
    return memcmp(G+0x400000,pe,0x200)==0 &&
           memcmp(G+0x7c4000,pe+0x3c4000,0x200)==0 && G[0x7c4040]==0x5a;
}
'''
SMALL_WINDOWS = WINDOWS.replace('committed==0x679000', 'committed==0x3c4200')

MAC = r'''
static void *seed_mmap(void *at,size_t size,int prot,int flags,int fd,off_t offset) {
    if (size!=(1ull<<32)) die("unexpected guest mapping size");
    void *p=mmap(at,size,prot,flags,fd,offset);
    /* Sentinel catches a missing raw tail copy even though payload is zero. */
    if (p!=MAP_FAILED) memset((uint8_t *)p+0x400000,0xa5,0x679000);
    return p;
}
#define mmap seed_mmap
static int load(uint8_t *pe,size_t size) {
    (void)size;
'''

MAIN = r'''
int main(int argc,char **argv) {
    if (argc!=2) return 2;
    FILE *f=fopen(argv[1],"rb"); if (!f) return 2;
    fseek(f,0,SEEK_END); size_t size=(size_t)ftell(f); rewind(f);
    uint8_t *pe=malloc(size); if (!pe) return 2;
    if (fread(pe,1,size,f)!=size) return 2;
    fclose(f); int ok=load(pe,size); free(pe); return !ok;
}
'''


def main():
    clang = shutil.which('clang')
    if not clang:
        return 3
    boot = (ROOT / 'runtime/boot.c').read_text()
    start = boot.index('    G = mmap(', boot.index('// ---------------- loader'))
    end = boot.index('    for (unsigned i = 0; i < speed_import_count;', start)
    block = boot[start:end]
    # Use the actual Mac loader statements, with only allocation intercepted to
    # seed the sentinel. app.m's fingerprint ignores SizeOfImage altogether.
    assert block.count('G = mmap(') == 1
    mac = (MAC + block +
           '\nint ok=check_image(pe); munmap(G,1ull<<32); return ok;\n}\n#undef mmap\n')
    loader = (WIN / 'loader.c').read_text()
    rounding = '    image=(image+align-1)&~(align-1);\n'
    assert loader.count(rounding) == 1
    truncated = mac.replace('pe + raw, rs);',
                            'pe + raw, rs > *(uint32_t *)(s + 8) ? *(uint32_t *)(s + 8) : rs);')
    assert truncated != mac
    with tempfile.TemporaryDirectory(prefix='pe-layout-') as tmp:
        folder = Path(tmp)
        image = folder / 'headers.pe'
        image.write_bytes(fixture())
        small_image = folder / 'small.pe'
        small_image.write_bytes(small_fixture())
        page_minimum = loader.replace('align<0x200', 'align<0x1000')
        assert page_minimum != loader
        for label, common, harness, source, pe, expected in (
                ('windows', COMMON, WINDOWS, loader, image, 0),
                ('windows-before-rounding', COMMON, WINDOWS, loader.replace(rounding, ''), image, 1),
                ('windows-small', SMALL_COMMON, SMALL_WINDOWS, loader, small_image, 0),
                ('windows-page-minimum', SMALL_COMMON, SMALL_WINDOWS, page_minimum, small_image, 1),
                ('mac', COMMON, mac, '', image, 0),
                ('mac-truncated-copy', COMMON, truncated, '', image, 1)):
            unit = folder / (label + '.c')
            unit.write_text(common + harness + MAIN)
            args = [clang, '-std=c11', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', '-g', '-DNFS_WIN_TEST',
                    '-I', str(WIN), '-I', str(ROOT / 'recomp'), str(unit)]
            if source:
                implementation = folder / (label + '-loader.c')
                implementation.write_text(source)
                args.append(str(implementation))
            exe = folder / label
            compiled = subprocess.run(args + ['-o', str(exe)], capture_output=True, text=True)
            assert compiled.returncode == 0, compiled.stderr[-3000:]
            result = subprocess.run([str(exe), str(pe)], capture_output=True, text=True)
            assert result.returncode == expected, (label, result.returncode, result.stderr[-2000:])
            print(f'{label}: exit {result.returncode} (expected {expected})')
    print('PE layout: Windows commits 0x679000; Mac copies full raw tail without SizeOfImage')
    check_build_environment()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
