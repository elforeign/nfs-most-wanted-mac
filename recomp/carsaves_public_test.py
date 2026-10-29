"""Public profile wrappers: 6 checks pass; private-admission mutant fails 1/6.

Compile the actual public pass-through source without car85.c or extra music.
Guest originals are counters: this checks linkage/routing, not serialization.
"""
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent


def main():
    actual = (ROOT / 'runtime/extratrax_public.c').read_text()
    addresses = re.findall(r'^PASS\(([0-9A-F]{8})\)', actual, re.M)
    stubs = '\n'.join(f'void f_{a}_orig(Cpu *c) {{ ++originals; c->eax=0xA5000001; }}' for a in addresses)
    harness = r'''
#include <stdio.h>
#include <stdlib.h>
#include "public.c"
uint8_t *G;
static unsigned originals, admissions, checks, failures;
void car85_profile_loaded(Cpu *c, uint32_t self) { ++admissions; }
STUBS
#define CHECK(x) do { ++checks; if(!(x)) ++failures; } while(0)
int main(void) {
    setenv("NFS_TEST_ADDCARS","/not-a-manifest",1);
    setenv("NFS_TEST_ADDCARS_SEED","/not-a-save",1);
    Cpu c={0}; c.ecx=0x12345678; c.esp=0x800000;
    f_005ACB70(&c);
    CHECK(originals==1); CHECK(c.eax==0xA5000001); CHECK(c.ecx==0x12345678);
    f_0058FD10(&c);
    CHECK(originals==2); CHECK(c.esp==0x800000); CHECK(admissions==0);
    printf("public checks=%u failures=%u original_calls=%u admission_calls=%u\n",checks,failures,originals,admissions);
    return failures?1:0;
}
'''.replace('STUBS', stubs)
    with tempfile.TemporaryDirectory(prefix='carsaves-public-') as directory:
        root = Path(directory)
        (root / 'test.c').write_text(harness)
        for broken in (False, True):
            source = actual
            if broken:
                source = source.replace('PASS(005ACB70)', '''void car85_profile_loaded(Cpu *, uint32_t);
void f_005ACB70_orig(Cpu *c);
void f_005ACB70(Cpu *c) { uint32_t self=c->ecx; f_005ACB70_orig(c); car85_profile_loaded(c,self); }''')
                assert source != actual
            (root / 'public.c').write_text(source)
            binary = root / ('broken' if broken else 'good')
            build = subprocess.run(['clang', '-O1', '-DNFS_PUBLIC=1', '-I', str(ROOT / 'runtime'), '-I', str(ROOT / 'recomp'), '-I', str(ROOT / 'recomp/out'), str(root / 'test.c'), '-o', str(binary)], text=True, capture_output=True)
            assert build.returncode == 0, build.stderr[-4000:]
            result = subprocess.run([str(binary)], text=True, capture_output=True)
            print('private-admission mutant' if broken else 'good', result.stdout.strip())
            assert result.returncode == int(broken), result.stderr
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
