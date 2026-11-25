#!/usr/bin/env python3
"""Split shadow techniques must survive both queue insertion and submission.

Executes translated 006E0770/006E0AB0 eligibility blocks and 006E0200's
GetTechnique/SetTechnique branch, with the production split_shadow_id alias.
ASan/UBSan: production 294 checks / 0 failures; the original code has 284
checks / 25 failures. Omitting the 006E0770/006E0AB0/submission substitutions
fails 7/7/11 checks, respectively. Real held GPU control: P2 paint marker
0.000% despite swapping both cars; P1 marks either car. Fixed GPU coverage
is P1 0.937% / P2 0.838% newly magenta (the split-shadow version row has the
other per-view measurements). No GPU, game or Unicorn needed
for this offline check. Unsupported materials, unrelated id-2 descriptors,
both single-player views, real IDs and the guest stack are also checked.
"""
from pathlib import Path
import hashlib
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL_SHA = '5c8b582500a33ac39fd1420b832adea217d3d05e7a8d62bd3669d896a008df20'


def fragment(source, start, end):
    return source[source.index('    /* '+start+':'):source.index('    /* '+end+':')]


PRE = r'''
#include "funcs.h"
#include "host.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
uint8_t *G;
#define VIEW_P2 0x9196C0u
#define Q 0x300000u
#define SHADER 0x301000u
#define EFFECT 0x302000u
#define VTABLE 0x303000u
#define D1 0x310000u
#define D2 0x310100u
#define OTHER 0x311000u
static int split_on, checks, failures, selected, submissions;
static int shadow_split(void) { return split_on; }
#define CHECK(x) do { ++checks; if (!(x)) { ++failures; if (failures<8) printf("FAIL %d: %s\n",__LINE__,#x); } } while(0)
void bad_jump(Cpu *c,uint32_t pc) { (void)c;(void)pc;abort(); }
void dispatch(Cpu *c,uint32_t fn) {
 uint32_t object=M32(c->esp+4), value=M32(c->esp+8);
 CHECK(object==EFFECT);
 if(fn==1) c->eax=value+0xAB00;
 else if(fn==2) { selected=(int)value-0xAB00; ++submissions; c->eax=0; }
 else abort();
 c->esp+=12;
}
'''

POST = r'''
static Cpu cpu(void) { return (Cpu){.esp=0x800000,.esi=Q,.edi=SHADER,.cw=0x37f}; }
int main(void) {
 G=calloc(1,16*1024*1024); if(!G)return 3;
 M32(VIEW_P2+4)=2; M32(VIEW_P2+0x60)=D2;
 M32(D1+4)=1; M32(D2+4)=2; M32(OTHER+4)=2;
 M32(Q+0x10)=SHADER; M32(Q+0x3C)=3;
 M32(SHADER+0x48)=EFFECT; M32(EFFECT)=VTABLE;
 M32(VTABLE+0x30)=1; M32(VTABLE+0xE8)=2;
 for(unsigned i=0;i<4;i++)M32(SHADER+0x18+4*i)=11*i;
 // The six supported material families come from the retail switch, not the
 // operand-substitution registry. Include its unsupported type 2 as a control.
 unsigned types[]={0,1,3,4,5,18,2};
 struct {int split;unsigned target;int eligible;} cases[]={
  {1,D1,1},{1,D2,1},{0,D1,1},{0,D2,0},{1,OTHER,0},{0,OTHER,0}};
 for(unsigned t=0;t<sizeof types/sizeof *types;t++) {
  M32(SHADER+4)=types[t];
  M8(0x6E0718+types[t])=types[t]==2;
  for(unsigned i=0;i<sizeof cases/sizeof *cases;i++) {
   split_on=cases[i].split; M32(0x982A20)=cases[i].target;
   int eligible=cases[i].eligible;
   Cpu a=cpu(),b=cpu(),c=cpu();
   CHECK(insert_0770(&a)==eligible);
   CHECK(insert_0ab0(&b)==eligible);
   submissions=0; selected=-1; submit(&c);
   int expected=types[t]!=2 && (eligible || types[t]==4);
   CHECK(submissions==expected);
   CHECK(selected==(expected ? (eligible ? 33:0):-1));
   CHECK(c.esp==0x800000);
   CHECK(M32(D2+4)==2 && M32(VIEW_P2+4)==2);
  }
 }
 printf("splitshadow submission: %d checks / %d failures\n",checks,failures);
 free(G); return failures?1:0;
}
'''


def main():
    path = ROOT/'recomp/out/speed_41.c'
    if not path.is_file():
        print('SKIP: generated MW translation absent')
        return 3
    source = path.read_text()
    # Pin only the original instruction fragment, not an entire production file.
    for site in ('006E0289', '006E089F', '006E0BED'):
        block = source.split('    /* '+site+': ',1)[1].split('    /* ',1)[0]
        original = block.replace('split_shadow_id(c->edx)', 'M32((uint32_t)(c->edx + 0x4u))')
        assert hashlib.sha256(original.encode()).hexdigest()==ORIGINAL_SHA, site
    world = (ROOT/'runtime/splitworld.c').read_text()
    alias = re.search(r'uint32_t split_shadow_id\([^\n]*\).*?\n}', world, re.S)[0]
    flags = 'uint8_t cf=0,pf=0,af=0,zf=0,sf=0,of=0;\n'
    first = fragment(source,'006E0896','006E08B0')
    second = fragment(source,'006E0BE0','006E0BFA')
    submit = fragment(source,'006E0283','006E02E4')
    bodies = [
        'static int insert_0770(Cpu *c) {\n'+flags+first+'return 1;\nL_006E09E2: return 0;\n}\n',
        'static int insert_0ab0(Cpu *c) {\n'+flags+second+'return 1;\nL_006E0D27: return 0;\n}\n',
        'static void submit(Cpu *c) {\n'+flags+submit+'\n}\n',
    ]
    with tempfile.TemporaryDirectory(prefix='split-submit-') as directory:
        directory = Path(directory)
        def run(label, parts):
            src=directory/'submit.c'; binary=directory/'submit'
            src.write_text(PRE+alias+'\n'+''.join(parts)+POST)
            compiled=subprocess.run(['clang','-std=c11','-Wall','-Wextra','-Werror',
                '-Wno-unused-variable','-Wno-unused-but-set-variable','-g',
                '-fsanitize=address,undefined','-fno-sanitize-recover=all',
                '-I'+str(ROOT/'recomp'),'-I'+str(ROOT/'recomp/out'),'-I'+str(ROOT/'runtime'),
                str(src),'-o',str(binary)],capture_output=True,text=True)
            assert compiled.returncode==0,compiled.stderr[-5000:]
            result=subprocess.run([str(binary)],capture_output=True,text=True)
            print(label+': '+result.stdout.splitlines()[-1],flush=True)
            assert 'AddressSanitizer' not in result.stderr and 'runtime error:' not in result.stderr,result.stderr[-2000:]
            return result
        positive=run('production',bodies)
        if positive.returncode:
            print(positive.stdout[:1500])
            return 1
        old='M32((uint32_t)(c->edx + 0x4u))'
        for i,label in enumerate(('006E0770 gate omitted','006E0AB0 gate omitted','submission gate omitted')):
            parts=bodies.copy()
            assert parts[i].count('split_shadow_id(c->edx)')==1
            parts[i]=parts[i].replace('split_shadow_id(c->edx)',old)
            result=run(label,parts)
            assert result.returncode==1,label
        original=run('all three original gates',[b.replace('split_shadow_id(c->edx)',old) for b in bodies])
        assert original.returncode==1
    return 0


if __name__=='__main__':
    raise SystemExit(main())
