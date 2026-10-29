#!/usr/bin/env python3
"""Menu-engine P2 release regression, registered by offline_tests.sh's *_test.py scan.
Actual translated allocator/vector constructor/reset/SetInitialState/GetJoyPadMask/
Update/ProcessPadsForPackage/HeldFor. Fake heap, FE interface, package-update and
message sinks only; no game/GPU/Unicorn. ASan/UBSan and RECOMP_ABICHECK.
PASS: 5,889/0 checks for 2, 4,480/0 each unset/1/invalid; 38 full guest/CPU
snapshots (637,541,808 bytes) each single-pad mode identical to original bodies.
Broken retain-one-pad and wrong-caller: 15/4,403 failures each; all-callers:
2/6,084 failures. Original count=1: zero P2 notifications; fix: two queued
EBFCDA65 messages, both mask 2, only on release. P1=1, simultaneous=3;
package mask=1 excludes P2. Unrelated calls/explicit count=2 also covered.
"""
from pathlib import Path
import os
import filecmp
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent

def body(file, name, original=False):
    s = (ROOT/'recomp/out'/file).read_text()
    m = re.search(r'void f_'+name+r'(?:_orig)?\(Cpu \*restrict c\) \{.*?\n\}', s, re.S)
    assert m, name
    return re.sub(r'void f_'+name+r'(?:_orig)?\(', 'void f_'+name+('_orig' if original else '')+'(', m[0], count=1)

PRE = r'''
#include "funcs.h"
#include "host.h"
#include <stdlib.h>
uint8_t *G;
static unsigned checks, failures, allocated, polls[2], messages, joymask;
static FILE *snapshot;
static unsigned release_messages, release_mask;
#define E 0x200000u
#define H 0x300010u
#define P 0x400000u
#define CHECK(x) do { ++checks; if(!(x)) { ++failures; printf("FAIL %d: %s\n",__LINE__,#x); } } while(0)
void bad_jump(Cpu *c,uint32_t t) { (void)c; fprintf(stderr,"jump %x\n",t); exit(2); }
void abi_violation(Cpu *c,uint32_t f,uint32_t b,uint32_t s,uint32_t i,uint32_t p,uint32_t sp) {
 (void)c;(void)b;(void)s;(void)i;(void)p;(void)sp; fprintf(stderr,"ABI %x\n",f); exit(2);
}
static Cpu cpu(uint32_t self) {
 return (Cpu){.ecx=self,.esp=0x800000,.fs_base=0x700000,.ebx=7,.esi=8,.edi=9,.ebp=10,.cw=0x37f};
}
static void abi(Cpu *c,unsigned n) {
 CHECK(c->esp==0x800004+4*n); CHECK(c->ebx==7 && c->esi==8 && c->edi==9 && c->ebp==10);
 CHECK(c->top==0 && c->cw==0x37f); CHECK(M32(0x700000)==0);
}
void f_00652AD0(Cpu *c) {
 allocated=arg(c,0); CHECK(allocated==136 || allocated==272);
 memset(G+H-16,0xa5,allocated+32); c->eax=H; c->esp+=4;
}
void f_00652B10(Cpu *c) { CHECK(arg(c,0)==0); c->esp+=4; }
void f_005B7780(Cpu *c) {
 CHECK(c->ecx==E); CHECK(arg(c,0)==0xebfcda65); CHECK(arg(c,1)==0);
 CHECK(arg(c,2)==P); CHECK(arg(c,3)==0xfffffffd || arg(c,3)==0xfffffffb);
 ++messages; joymask|=arg(c,4); c->esp+=24;
}
void f_005C1460(Cpu *c) { CHECK(c->ecx==E); c->esp+=4; }
void f_005C4F60(Cpu *c) { CHECK(c->ecx==E); c->esp+=4; }
void f_005C1B30(Cpu *c) { CHECK(c->ecx==P); c->esp+=12; }
void f_005B01E0(Cpu *c) { (void)c; abort(); }
void f_005B0B20(Cpu *c) { (void)c; abort(); }
void f_005B9640(Cpu *c) { (void)c; abort(); }
void f_005B1560(Cpu *c) { (void)c; abort(); }
void dispatch(Cpu *c,uint32_t t) {
 if(t==0x5b54c0) { f_005B54C0(c); return; }
 if(t==0x516860) {
  unsigned p=arg(c,0)&255; CHECK(p<2); ++polls[p]; f_00516860(c); return;
 }
 fprintf(stderr,"dispatch %x\n",t); exit(2);
}
static void bounds(void) {
 for(unsigned i=0;i<16;i++) { CHECK(M8(H-16+i)==0xa5); CHECK(M8(H+allocated+i)==0xa5); }
}
static void capture(Cpu *c) {
 if(snapshot) {
  CHECK(fwrite(G,1,0x1000000,snapshot)==0x1000000);
  CHECK(fwrite(c,1,sizeof *c,snapshot)==sizeof *c);
 }
}
static void init(unsigned force_one,uint32_t caller,unsigned count) {
 memset(G,0,0x1000000); memset(polls,0,sizeof polls); allocated=messages=joymask=0;
 Cpu c=cpu(E); M32(c.esp)=caller; M32(c.esp+4)=count;
 if(force_one) f_005B7420_orig(&c); else f_005B7420(&c);
 abi(&c,1); unsigned expected=(!force_one && pad_count()==2 && caller==0x516934 && count==1)?2:count;
 CHECK(M32(E+0xd0)==expected); CHECK(M32(E+4)==H); CHECK(allocated==136*expected);
 for(unsigned i=0;i<allocated;i++) CHECK(M8(H+i)==0);
 // Exercise original SetInitialState's per-pad reset too, on dirty objects.
 memset(G+H,0x5a,allocated); c=cpu(E); f_005BB720(&c); abi(&c,0);
 for(unsigned i=0;i<allocated;i++) CHECK(M8(H+i)==0);
 M8(E)=1; M32(E+0x108)=0x410000; M32(0x410000)=0x420000; M32(0x420040)=0x516860;
 M32(E+0xe0)=P; M8(P+0x28)=1; M32(P+0x20)=3; M32(P+0x5c)=0x430000;
 M32(0x43000c)=0xebfcda65; M32(0x8f5740+6*4)=0xebfcda65;
 // PE row 7: action 24h / FE START 40h; activity columns F8 / FC.
 M32(0x8f34ec)=0x24; M32(0x8f34f0)=0x40;
 bounds();
 capture(&c);
}
static void tick(unsigned active,unsigned expected) {
 M32(0x8f34f8)=active&1; M32(0x8f34fc)=(active>>1)&1;
 for(unsigned p=0;p<2;p++) {
  Cpu q=cpu(0); M32(q.esp+4)=p; f_00516860(&q); abi(&q,1);
  CHECK(q.eax==((active&(1u<<p))?0x40:0));
 }
 messages=joymask=0;
 Cpu c=cpu(E); M32(c.esp+4)=16; M32(c.esp+8)=0; f_005C53C0(&c); abi(&c,2);
 CHECK(joymask==expected); CHECK(messages==(expected?2:0)); bounds();
 capture(&c);
}
static void sequence(unsigned force_one,unsigned active,unsigned package_mask,unsigned expected) {
 init(force_one,0x516934,1); M32(P+0x20)=package_mask;
 unsigned n=M32(E+0xd0);
 tick(0,0); tick(active,0); tick(active,0);
 for(unsigned p=0;p<n;p++) if(active&(1u<<p)) CHECK(M32(H+136*p+8+6*4)==16);
 tick(0,expected); release_messages=messages; release_mask=joymask;
 tick(0,0); CHECK(polls[0]==5); CHECK(polls[1]==(n==2?5:0));
}
int main(int argc,char **argv) {
 G=calloc(1,0x1000000); CHECK(G!=NULL); unsigned two=pad_count()==2;
 if(argc>1) { snapshot=fopen(argv[1],"wb"); CHECK(snapshot!=NULL); }
 sequence(0,2,3,two?2:0); unsigned fixed_messages=release_messages, fixed_mask=release_mask;
 sequence(0,1,3,1); sequence(0,3,3,two?3:1);
 sequence(0,2,1,0); sequence(0,2,2,two?2:0);
 // Failure proof: identical P2 press/hold/release sequence with original count=1.
 sequence(1,2,3,0);
 printf("failure proof: count=1 P2 notifications=%u mask=%u; fixed notifications=%u mask=%u\n",
        release_messages,release_mask,fixed_messages,fixed_mask);
 // Unrelated callers and explicit counts preserve the original allocator contract.
 init(0,0x123456,1); init(0,0x516934,2);
 if(snapshot) fclose(snapshot);
 printf("padmenu: %u checks, %u failures (%s)\n",checks,failures,two?"2":"single"); free(G); return failures?1:0;
}
'''

def run(cmd, **kw):
    r=subprocess.run(cmd,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,**kw)
    if r.returncode: raise RuntimeError(r.stdout[-5000:])
    return r.stdout

def main():
    hook=re.search(r'void f_005B7420\(Cpu \*c\) \{.*?\n\}',(ROOT/'runtime/padguest.c').read_text(),re.S)[0]
    pad=re.search(r'int pad_count\(void\) \{.*?\n\}',(ROOT/'runtime/user32.m').read_text(),re.S)[0]
    groups=(('speed_00.c',['00404DB0']),('speed_17.c',['00516860']),
            ('speed_23.c',['005B7420','005B54C0','005AE8E0','005AE960','005AEA10','005BB720']),
            ('speed_24.c',['005C53C0','005C20A0']))
    bodies='\n'.join(body(f,n,n=='005B7420') for f,ns in groups for n in ns)
    mutants={'retain-one-pad':hook.replace('M32(c->esp + 4) = 2;','M32(c->esp + 4) = 1;'),
             'wrong-caller':hook.replace('0x00516934u','0x00516935u'),
             'all-callers':hook.replace(' && M32(c->esp) == 0x00516934u','')}
    with tempfile.TemporaryDirectory(prefix='padmenu_') as td:
        tmp=Path(td)
        def compile_one(name,h):
            src=tmp/(name+'.c'); exe=tmp/name
            src.write_text(PRE[:PRE.index('static void bounds')]+pad+'\nvoid f_005B7420_orig(Cpu *c);\n'+h+'\n'+bodies+'\n'+PRE[PRE.index('static void bounds'):])
            run(['clang','-std=c11','-g','-w','-DRECOMP_ABICHECK','-fsanitize=address,undefined','-fno-sanitize-recover=all',
                 '-I',str(ROOT/'recomp'),'-I',str(ROOT/'recomp/out'),'-I',str(ROOT/'runtime'),str(src),'-o',str(exe)])
            return exe
        good=compile_one('good',hook)
        baseline=compile_one('baseline','void f_005B7420(Cpu *c) { f_005B7420_orig(c); }')
        for mode in (None,'1','invalid','2'):
            env=dict(os.environ); env.pop('NFS_PADS',None); env.pop('ASAN_OPTIONS',None)
            if mode is not None: env['NFS_PADS']=mode
            print(run([str(good),str(tmp/'good.bin')],env=env).strip())
            if mode!='2':
                run([str(baseline),str(tmp/'baseline.bin')],env=env)
                assert filecmp.cmp(tmp/'good.bin',tmp/'baseline.bin',shallow=False)
                print(f'  {mode or "unset"}: {(tmp/"good.bin").stat().st_size:,} guest/CPU snapshot bytes identical to original')
        for name,h in mutants.items():
            exe=compile_one(name,h)
            r=subprocess.run([str(exe)],env=dict(os.environ,NFS_PADS='2'),text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
            assert r.returncode==1 and 'checks,' in r.stdout,(name,r.stdout[-2000:])
            print(f'  rejected {name}: {r.stdout.splitlines()[-1]}')
    print('padmenu_test: PASS')

if __name__=='__main__': main()
