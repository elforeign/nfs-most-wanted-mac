#!/usr/bin/env python3
"""Translated MaxOut -> cart notification -> paid checkout, no game or emulation.
Runs complete translated selection, lock/price policy, cart, affordability,
checkout, cash debit and package installation with ABI checks and ASan/UBSan.
Fixture boundaries supply vehicle max levels, unlock/cost tables, allocation,
record lookup and physics acceptance; UI dialogs are spies. Unknown calls abort.
Proof: production 60 cases / 12,240 checks / 0 failures; lock bypass 1,684,
no cash debit 24, affordability bypass 198, dropped installation 114 failures.
Five locked/unlocked type mixes, three progress tiers (including locked higher
levels), and zero/short/exact/surplus cash assert selected levels and exact debit.
"""
from pathlib import Path
import re
import runpy
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
function=runpy.run_path(str(ROOT/'runtime/padprompts_shop_test.py'))['function']
FUNCTIONS={'52':'007B5BB0 007B5870 007BAB60 007BB240',
           '51':'007A4E50 007A4DB0 007AEF70 007AED80 007B0170 007AEB90 007AECA0 007A4C20 007A4D40 007AEEB0',
           '20':'0056D7A0'}
PRE=r'''
#include "host.h"
#include "funcs.h"
#include <stdlib.h>
#include <string.h>
uint8_t *G;
static unsigned checks,failures,unlocked,heap,ok_dialogs,confirm_dialogs,physics,refreshes;
static const uint32_t manager=0x9B9EC0,db=0x310000,profile=0x320000,record=0x330000,screen=0x340000,sp=0x800000;
static const unsigned levels[]={3,4,3,4,4,3,3};
static unsigned available[7];
#define CHECK(x) do{++checks;if(!(x)){++failures;if(failures<12)printf("FAIL %d: %s\n",__LINE__,#x);}}while(0)
void bad_jump(Cpu*c,uint32_t t){(void)c;fprintf(stderr,"unexpected %x\n",t);exit(2);}
void untranslated(Cpu*c,uint32_t t){bad_jump(c,t);}
void abi_violation(Cpu*c,uint32_t fn,uint32_t b,uint32_t s,uint32_t i,uint32_t p,uint32_t st){
 (void)b;(void)s;(void)i;(void)p;(void)st;bad_jump(c,fn);
}
void dispatch(Cpu*c,uint32_t t){
 // Both translated list implementations call deleting destructors (thiscall, pop 4).
 if(t!=0x12345678)bad_jump(c,t);CHECK(arg(c,0)==1);c->esp+=8;
}
void f_00652AD0(Cpu*c){ // FastMem allocation: deterministic scratch guest heap
 unsigned size=arg(c,0);CHECK(size==0x2c||size==0x18);c->eax=heap;heap+=(size+15)&~15u;c->esp+=4;
}
void f_00672E80(Cpu*c){ // vehicle max-level table
 unsigned type=arg(c,1);CHECK(arg(c,0)==manager+8);CHECK(type<7);
 c->eax=type<7?levels[type]:0;c->esp+=4;
}
void f_00575B00(Cpu*c){c->eax=0;c->esp+=4;} // front room; no marker stock
void f_0058A960(Cpu*c){ // unlock system fixture, called by real IsPartLocked
 unsigned type=arg(c,1),level=arg(c,2);
 // MaxOut also probes the following level when assigning the cart badge.
 CHECK(arg(c,0)==2 && type<7 && level>=1 && level<=levels[type]+1);
 CHECK(arg(c,3)==0 && arg(c,4)==0);
 c->eax=level<=available[type];c->esp+=4;
}
void f_007A4FF0(Cpu*c){c->eax=0;c->esp+=12;} // new-item badge only
void f_0058ACD0(Cpu*c){ // unlock-system price table; real GetPartPrice maps its arguments
 unsigned type=arg(c,1);CHECK(arg(c,0)==2 && type<7);CHECK(arg(c,2)==available[type] && arg(c,3)==0);
 c->eax=100+type*17+arg(c,2)*31;c->esp+=4;
}
void f_0056F100(Cpu*c){CHECK(arg(c,0)==1);c->eax=record;c->esp+=8;}
void f_0056F120(Cpu*c){CHECK(arg(c,0)==2);c->eax=0x350000;c->esp+=8;}
void f_0056F210(Cpu*c){CHECK(c->ecx==record && arg(c,0)==manager+8);++physics;c->eax=1;c->esp+=8;}
void f_007A4BC0(Cpu*c){CHECK(arg(c,0)<7);c->esp+=12;} // visual preview only
void f_007B5A50(Cpu*c){c->esp+=4;}
void f_005A96C0(Cpu*c){c->esp+=20;} // UI base notification
void f_00598700(Cpu*c){++ok_dialogs;c->esp+=4;}
void f_00598C00(Cpu*c){++confirm_dialogs;c->esp+=4;}
void f_007A56C0(Cpu*c){c->eax=1;c->esp+=4;} // career UI wording
void f_007B3FF0(Cpu*c){c->esp+=4;} // clear empty UI list
void f_005258B0(Cpu*c){c->esp+=8;} // pop UI package
void f_00516C50(Cpu*c){c->esp+=16;} // queue UI game message
void f_007B0620(Cpu*c){++refreshes;c->esp+=4;} // cart header painting
'''
POST=r'''
static Cpu cpu(unsigned self){
 Cpu c={.ecx=self,.esp=sp,.eax=0x12345600,.ebx=7,.esi=8,.edi=9,.ebp=10,.cw=0x37f};
 M32(sp)=0xDEADBEEF;return c;
}
static void abi(Cpu c,unsigned pop){CHECK(c.esp==sp+pop);CHECK(c.ebx==7&&c.esi==8&&c.edi==9&&c.ebp==10);CHECK(!c.top);}
static void notify(unsigned event){Cpu c=cpu(screen);M32(sp+4)=event;M32(sp+8)=M32(sp+12)=M32(sp+16)=0;f_007BB240(&c);abi(c,20);}
int main(void){
 G=calloc(1,0x1000000);if(!G)return 2;unsigned cases=0;
 unsigned masks[]={0,1,0x2a,0x55,0x7f};
 for(unsigned m=0;m<5;m++)for(unsigned progression=0;progression<3;progression++)for(unsigned funds=0;funds<4;funds++){
  memset(G,0,0x1000000);unlocked=masks[m];heap=0x400000;ok_dialogs=confirm_dialogs=physics=refreshes=0;
  M32(0x91CF90)=db;M32(db+0x10)=profile;M32(db+0x12c)=1;
  M32(manager+4)=0x360000;M8(0x360010)=1;M8(0x360011)=2;
  M32(manager+0x1b4)=M32(manager+0x1b8)=manager+0x1b4;
  M32(screen+0x10)=0x370000;
  // Synthetic vtables route only destructors; production allocates/clones/links each item.
  M32(0x8B7518)=0x12345678;M32(0x8B751C)=0x12345678;
  MF32(0x890988)=1.0f;
  unsigned total=0,count=0;
  for(unsigned t=0;t<7;t++){
   available[t]=(unlocked&(1u<<t))?(progression==0?levels[t]:progression==1?levels[t]-1:1+t%2):0;
   if(available[t]){total+=100+t*17+available[t]*31;++count;}
  }
  unsigned cash=funds==0?0:funds==1?(total?total-1:0):funds==2?total:total+1234;
  M32(profile+0xb4)=cash;
  Cpu c=cpu(manager);f_007B5BB0(&c);abi(c,4);
  CHECK(M32(manager+0x1bc)==count);
  unsigned seen=0;
  for(unsigned link=M32(manager+0x1b4);link!=manager+0x1b4;link=M32(link)){
   unsigned item=link-4,part=M32(item+0xc),type=M32(part+0x18);
   CHECK(type<7);if(type>=7)break;CHECK(unlocked&(1u<<type));CHECK(!(seen&(1u<<type)));seen|=1u<<type;
   CHECK(M32(part+0x14)==available[type]);CHECK(M32(part+0x24)==100+type*17+available[type]*31);CHECK(M8(part+0x1c)==1);
  }
  CHECK(seen==unlocked);CHECK(M32(profile+0xb4)==cash);CHECK(!physics);
  c=cpu(manager);M32(sp+4)=2;f_007AED80(&c);abi(c,8);CHECK(c.eax==total);
  c=cpu(screen);f_007B0170(&c);abi(c,4);CHECK(!!(uint8_t)c.eax==(cash>=total));
  notify(0x406415E3); // real UI affordability branch, then confirmation only if offered
  CHECK(ok_dialogs==(count && cash<total));CHECK(confirm_dialogs==(count && cash>=total));
  if(confirm_dialogs)notify(0xD05FC3A3);
  unsigned paid=count && cash>=total;
  CHECK(M32(profile+0xb4)==cash-(paid?total:0));CHECK(physics==(paid?count:0));
  for(unsigned t=0;t<7;t++)CHECK(M32(record+0x118+t*4)==(paid&&(unlocked&(1u<<t))?available[t]:0));
  if(paid)CHECK(M32(manager+0x1b4)==manager+0x1b4);
  ++cases;
 }
 printf("%u cases, %u checks, %u failures\n",cases,checks,failures);free(G);return failures?1:0;
}
'''

def main():
    bodies=[]
    for chunk,names in FUNCTIONS.items():
        path=ROOT/f'recomp/out/speed_{chunk}.c'
        if not path.exists():print('SKIP: translated PC functions require a local build');raise SystemExit(3)
        source=path.read_text()
        for name in names.split():
            fn='f_'+name
            actual=fn+'_orig' if 'void '+fn+'_orig(' in source else fn
            bodies.append(function(source,actual).replace('void '+actual+'(', 'void '+fn+'('))
    translated='\n'.join(bodies)
    used=set(re.findall(r'\bf_[0-9A-F]{8}(?=\(c\))',translated))
    defined=set(re.findall(r'void (f_[0-9A-F]{8})\(',PRE+translated))
    stubs='\n'.join(f'void {fn}(Cpu*c){{bad_jump(c,0x{fn[2:]});}}' for fn in sorted(used-defined))
    variants=[('production',translated)]
    for label,fn,replacement in [
        ('lock-bypassed','f_007A4E50','void f_007A4E50(Cpu*c){c->eax=0;c->esp+=12;}\n'),
        ('cash-not-debited','f_0056D7A0','void f_0056D7A0(Cpu*c){c->esp+=8;}\n'),
        ('affordability-bypassed','f_007B0170','void f_007B0170(Cpu*c){c->eax=1;c->esp+=4;}\n'),
        ('installation-dropped','f_007A4C20','void f_007A4C20(Cpu*c){c->esp+=12;}\n')]:
        original=function(translated,fn)
        assert translated.count(original)==1
        variants.append((label,translated.replace(original,replacement)))
    with tempfile.TemporaryDirectory(prefix='maxperf-checkout-') as d:
        f,exe=Path(d)/'t.c',Path(d)/'t'
        for label,body in variants:
            f.write_text(PRE+stubs+body+POST)
            p=subprocess.run(['clang','-O1','-g','-DRECOMP_ABICHECK','-fsanitize=address,undefined','-fno-sanitize-recover=all','-I'+str(ROOT/'runtime'),'-I'+str(ROOT/'recomp'),'-I'+str(ROOT/'recomp/out'),str(f),'-o',str(exe)],capture_output=True,text=True)
            assert p.returncode==0,p.stderr[-3500:]
            p=subprocess.run([str(exe)],capture_output=True,text=True)
            summary=p.stdout.splitlines()[-1] if p.stdout else '(no output)'
            assert re.fullmatch(r'60 cases, \d+ checks, \d+ failures',summary),(label,p.stdout,p.stderr[-2000:])
            print(label+': '+summary,flush=True)
            assert p.returncode==(0 if label=='production' else 1),(label,p.returncode,p.stdout[-3500:],p.stderr[-3500:])

if __name__=='__main__':main()
