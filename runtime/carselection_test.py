#!/usr/bin/env python3
"""










"""
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]

PRE = r'''
#include "funcs.h"
#include "host.h"
#include <stdlib.h>
uint8_t *G;
static unsigned checks, failures, accepted;
#define CHECK(x) do { ++checks; if (!(x)) { ++failures; printf("FAIL %d: %s\n",__LINE__,#x); } } while(0)
void abi_violation(Cpu *c,uint32_t fn,uint32_t b,uint32_t s,uint32_t i,uint32_t p,uint32_t sp) {
 (void)c;(void)b;(void)s;(void)i;(void)p;(void)sp; fprintf(stderr,"ABI %x\n",fn); exit(2);
}
void bad_jump(Cpu *c,uint32_t t) { (void)c; fprintf(stderr,"jump %x\n",t); exit(2); }
void dispatch(Cpu *c,uint32_t t) { bad_jump(c,t); }
void untranslated(Cpu *c,uint32_t t) { bad_jump(c,t); }
void f_007A3D50(Cpu *c) { ++accepted; CHECK(c->ecx==0x300000); c->esp+=4; }
'''

MAIN = r'''
int main(void) {
 G=calloc(1,0x1000000); if(!G) return 2;
 const uint32_t messages[]={0x406415e3,0x0c407210};
 const unsigned delays[]={0,100,400,999,1000,1001,4000};
 for(unsigned split=0;split<2;split++) for(unsigned player=0;player<2;player++)
 for(unsigned msg=0;msg<2;msg++) for(unsigned selection=0;selection<4;selection++)
 for(unsigned k=0;k<sizeof delays/sizeof *delays;k++) {
  memset(G,0,0x1000000); accepted=0;
  const uint32_t screen=0x300000,db=0x310000,profile=0x320000,car=0x330000,sp=0x800000;
  M32(0x91cf90)=db; M32(db)=split?2:0; M32(db+0x12c)=split?4:0;
  M32(db+0x10)=profile; M8(profile+0x86+player*0x14)=1;
  M32(screen+0x34)=selection?car:0; M8(car+0xc)=selection==2?1:selection==3?255:0;
  M32(screen+0x1a8)=player; M32(screen+0x38)=4000; M32(0x925b14)=4000+delays[k];
  MF32(0x890984)=0.00025f; MF32(0x8910f0)=0.25f;
  M32(sp+4)=messages[msg]; M32(sp+8)=0; M32(sp+12)=0; M32(sp+16)=0;
  Cpu c={.ecx=screen,.esp=sp,.ebx=7,.esi=8,.edi=9,.ebp=10,.cw=0x37f};
  f_007C1770(&c);
  CHECK(accepted==(selection==1 && delays[k]>=1000));
  CHECK(c.esp==sp+20); CHECK(c.ebx==7); CHECK(c.esi==8); CHECK(c.edi==9); CHECK(c.ebp==10);
  CHECK(c.top==0 && c.cw==0x37f);
 }
 printf("%u checks, %u failures\n",checks,failures); free(G); return failures?1:0;
}
'''


def run(args):
    p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if p.returncode:
        raise RuntimeError(f'{args[0]} returned {p.returncode}:\n{p.stdout[-3000:]}')
    return p.stdout


def main():
    source = (ROOT / 'recomp/out/speed_52.c').read_text()
    body = re.search(r'void f_007C1770(?:_orig)?\(Cpu \*restrict c\) \{.*?\n\}', source, re.S).group()
    assert '_orig' not in body.splitlines()[0], 'Update extraction for a newly hooked function'
    dependencies = sorted(set(re.findall(r'\bf_[0-9A-F]{8}(?=\()', body)) - {'f_007C1770', 'f_007A3D50'})
    stubs = '\n'.join(f'void {name}(Cpu *c) {{ bad_jump(c,0x{name[2:]}u); }}' for name in dependencies)
    def remove_branch(address, branch):
        pattern = rf'(/\* {address}: [^\n]+\*/\n    ){re.escape(branch)}'
        result, n = re.subn(pattern, r'\1/* deliberately broken: guard removed */', body)
        assert n == 1, (address, n)
        return result
    variants = {
        'actual': body,
        'locked-guard-removed': remove_branch('007C1994', 'if (!zf) goto L_007C2731;'),
        'delay-guard-removed': remove_branch('007C19BE', 'if (!pf) goto L_007C2731;'),
    }
    with tempfile.TemporaryDirectory(prefix='carselection-') as directory:
        tmp = Path(directory)
        for name, variant in variants.items():
            path, exe = tmp / f'{name}.c', tmp / name
            path.write_text(PRE + stubs + '\n' + variant + '\n' + MAIN)
            run(['clang', '-std=c11', '-g', '-O1', '-fsanitize=address,undefined',
                 '-fno-sanitize-recover=all', '-DRECOMP_ABICHECK', '-I', str(ROOT/'recomp'),
                 '-I', str(ROOT/'recomp/out'), '-I', str(ROOT/'runtime'), str(path), '-o', str(exe)])
            p = subprocess.run([str(exe)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            result = p.stdout.splitlines()[-1] if p.stdout else '(no output)'
            assert p.returncode == (0 if name == 'actual' else 1), (name, p.returncode, p.stdout[-3000:])
            assert re.fullmatch(r'\d+ checks, \d+ failures', result), (name, p.stdout[-3000:])
            print(f'{name}: {result}')
    print('carselection_test: PASS (both broken guards rejected)')


if __name__ == '__main__':
    main()
