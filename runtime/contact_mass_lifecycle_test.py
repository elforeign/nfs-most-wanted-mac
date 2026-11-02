"""Push lifecycle: 19 checks pass; omitted reset fails 9 checks (exit 1).
The c09f5b1b module also fails 9 checks (exit 1): remember a 300C,
retire it before placement, then integrate a different car and a cop.
Retired integrator storage is poisoned with a valid sentinel, so detecting
stale use never requires crashing a process. No game or private assets.
"""
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''
#include "host.h"
#include "car_physics.h"
#include <stdlib.h>
#include <stdarg.h>
uint8_t *G;
int diag_on=1;
static unsigned checks,failures,retired,integrated;
#define CHECK(x) do { ++checks; if (!(x)) { ++failures; fprintf(stderr,"line %d: %s\n",__LINE__,#x); } } while (0)
void diag_event(const char *s,...) { (void)s; }
int car_physics_enabled(void) { return 0; }
uint32_t car_physics_key(const char *s) { return !strcmp(s,"300c")?1:!strcmp(s,"copmidsize")?2:4; }
const CarPhysics *car85_contact_physics(uint32_t k) { (void)k; return NULL; }
uint32_t call_guest(Cpu *c,uint32_t fn,int n,const uint32_t *args) {
    (void)fn; (void)n; (void)args; return c->ecx+0x80;
}
#define ORIGINAL(addr) void f_##addr##_orig(Cpu *c) { c->esp+=4; }
ORIGINAL(00487160) ORIGINAL(0069ABF0) ORIGINAL(00698360)
ORIGINAL(006A85B0) ORIGINAL(00698650) ORIGINAL(00488F70)
void f_006978F0_orig(Cpu *c) { ++integrated; c->esp+=8; }
void f_006B7720_orig(Cpu *c);
MODULE
FALLBACK
void f_006B7720_orig(Cpu *c) {
    // Model the same release on world unload and a player-car replacement.
    // Reused storage looks fast enough to trigger the erroneous cop placement.
    CHECK(!push_player && !push_body_a && !push_body_b && !push_completed && !push_placed);
    ++retired;
    M32(M32(c->ecx+0x78))=0x70000;
    MF32(0x70020)=20;
    c->esp+=4;
}
static void body(uint32_t b,uint32_t key) {
    uint32_t owner=b+0x800,vt=b+0xa00;
    memset(G+b,0,0x1000);
    M32(b+0x60)=vt; M32(vt+0x2c)=0x676e10;
    M32(b+0x34)=owner; M32(owner)=vt; M32(vt+0x44)=0x100;
    M32(owner+0x84)=owner+0xc0; M32(owner+0xe0)=key;
    M32(b+0x78)=b+0x300; M32(b+0x300)=b+0x400;
    MF32(b+0x42c)=1595; MF32(b+0x410)=100;
}
static void integrate(uint32_t b) {
    Cpu c={0}; c.ecx=b; c.esp=0x80000;
    f_006978F0(&c); CHECK(c.esp==0x80008);
}
static void retire(uint32_t b) {
    Cpu c={0}; c.ecx=b; c.esp=0x80000;
    f_006B7720(&c); CHECK(c.esp==0x80004);
}
int main(void) {
    G=calloc(1,0x100000); setenv("NFS_TEST_CONTACT_PUSH","1",1);
    // Unload before placement, then reload a different player with cops.
    body(0x10000,1); integrate(0x10000);
    CHECK(push_player==0x10000 && !push_placed && !push_completed);
    retire(0x10000);
    CHECK(!push_player && !push_body_a && !push_body_b && !push_placed && !push_completed);
    body(0x12000,3); body(0x14000,2);
    integrate(0x12000); integrate(0x14000);
    CHECK(!push_player && !push_placed);
    CHECK(MF32(0x14410)==100);
    // A fresh 300C can still place its cop after the reset.
    body(0x16000,1); MF32(0x16420)=20;
    integrate(0x16000); integrate(0x14000);
    CHECK(push_player==0x16000 && push_placed);
    CHECK(MF32(0x14410)==106);
    // Car replacement after a completed fixture re-arms every diagnostic field,
    // even if diagnostics were switched off before destruction.
    push_body_a=0x16000; push_body_b=0x14000; push_completed=1;
    diag_on=0; retire(0x16000);
    CHECK(!push_player && !push_body_a && !push_body_b && !push_placed && !push_completed);
    diag_on=1; body(0x18000,1); integrate(0x18000);
    CHECK(push_player==0x18000 && !push_placed && !push_completed);
    CHECK(retired==2 && integrated==6);
    printf("lifecycle checks=%u failures=%u\n",checks,failures);
    free(G); return failures?1:0;
}
'''


def main():
    module = Path(sys.argv[1]).read_text() if len(sys.argv) > 1 else (ROOT/'runtime/contact_mass.c').read_text()
    variants = {'actual': module}
    if len(sys.argv) == 1:
        broken = module.replace('    push_reset();', '    (void)push_reset; /* omitted lifecycle reset */')
        assert broken != module
        variants['omitted-reset'] = broken
    with tempfile.TemporaryDirectory(prefix='contact-lifecycle-') as directory:
        tmp = Path(directory)
        for name, source in variants.items():
            fallback = '' if 'void f_006B7720(Cpu *c)' in source else 'void f_006B7720(Cpu *c) { f_006B7720_orig(c); }'
            unit = tmp/'fixture.c'
            unit.write_text(HARNESS.replace('MODULE', source).replace('FALLBACK', fallback))
            exe = tmp/'fixture'
            build = subprocess.run(['clang', '-O1', '-Wall', '-Wextra', '-Werror',
                                    '-I'+str(ROOT/'runtime'), '-I'+str(ROOT/'recomp'),
                                    '-I'+str(ROOT/'recomp/out'), str(unit), '-o', str(exe)],
                                   capture_output=True, text=True)
            assert build.returncode == 0, build.stderr[-4000:]
            run = subprocess.run([str(exe)], capture_output=True, text=True)
            print(name, run.stdout.strip())
            if name == 'actual' and run.returncode:
                print(run.stderr[-3000:])
            assert run.returncode == (0 if name == 'actual' else 1), (name, run.returncode)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
