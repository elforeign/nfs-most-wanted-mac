"""Native generated-instruction regression, no guest image or emulator.

Good: 59 assertions covering x87 compare/pop flags and non-ST1 operands; SSE aliasing, upper lanes,
narrow memory operands, arithmetic/NaNs, shuffles and MXCSR conversions pass.
Broken: remove the compare pop from the identical generated fixture -> exit 1.
"""
from pathlib import Path
import subprocess
import sys
import tempfile
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import recomp
from simd import emit


def main():
    rc=recomp.Recompiler(pe=object(),mw_patches=False)
    cases={
        'compare':'dff3', 'quiet':'dfe9', 'nonpop':'dbf3',
        'move':'0f28c1', 'scalar_reg':'f30f10c1', 'scalar_mem':'f30f1000',
        'store_scalar':'f30f1100', 'add':'0f58c1', 'mul':'f30f59c1',
        'shuffle':'0fc6c11b', 'low':'0f12c1', 'high':'0f16c1',
        'unpack':'0f14c1', 'mask':'0fc2c104', 'convert':'0f2dc1',
        'convert_back':'0f2ac1', 'int_float':'f30f2ac0', 'scan':'0fbdc1',
    }
    definitions=[]
    for name,raw in cases.items():
        ins=next(rc.md.disasm(bytes.fromhex(raw),0x401000))
        code=emit(rc,ins)
        if code is None:code=rc.x87(ins,ins.mnemonic)
        definitions.append('void '+name+'(Cpu*c){uint8_t cf=1,pf=1,af=1,zf=1,sf=1,of=1; '+ ' '.join(code)+' c->eflags=cf|(pf<<2)|(af<<4)|(zf<<6)|(sf<<7)|(of<<11);}\n')
    source='''#include "runtime.h"
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <unistd.h>
uint8_t *G;
'''+''.join(definitions)+'''
#define CHECK(v) do { ++checks; if(!(v)){fprintf(stderr,"check %d line %d failed\\n",checks,__LINE__);return 1;} }while(0)
static void floats(Cpu*c,const float*a,const float*b){memcpy(c->xmm[0],a,16);memcpy(c->xmm[1],b,16);}
int main(void){Cpu c={0};int checks=0;c.cw=0x37f;
 const double pairs[][2]={{1,2},{2,1},{2,2},{NAN,2},{INFINITY,INFINITY},{-0.0,0.0}};
 const unsigned flags[]={1,0,64,69,64,64};
 for(int i=0;i<6;i++){c.top=6;c.st[6]=pairs[i][0];c.st[1]=pairs[i][1];c.st[7]=-99;c.sw=0x1234;compare(&c);CHECK(c.top==7);CHECK(c.eflags==flags[i]);CHECK(c.sw==0x1234);}
 c.top=7;c.st[7]=NAN;c.st[0]=2;quiet(&c);CHECK(c.top==0&&c.eflags==69);
 c.top=6;c.st[6]=1;c.st[1]=2;nonpop(&c);CHECK(c.top==6&&c.eflags==1);
 float a[]={1,2,3,4},b[]={5,6,7,8};floats(&c,a,b);scalar_reg(&c);CHECK(c.xmm[0][0]==c.xmm[1][0]&&c.xmm[0][3]==0x40800000);
 long page=sysconf(_SC_PAGESIZE);G=mmap(0,2*page,PROT_READ|PROT_WRITE,MAP_ANON|MAP_PRIVATE,-1,0);CHECK(G!=MAP_FAILED);CHECK(mprotect(G+page,page,PROT_NONE)==0);
 c.eax=(uint32_t)page-4;MF32(c.eax)=9;scalar_mem(&c);CHECK(c.xmm[0][0]==0x41100000&&c.xmm[0][1]==0&&c.xmm[0][3]==0);store_scalar(&c);CHECK(MF32(c.eax)==9);
 floats(&c,a,b);move(&c);CHECK(!memcmp(c.xmm[0],b,16));floats(&c,a,b);add(&c);float sums[]={6,8,10,12};CHECK(!memcmp(c.xmm[0],sums,16));
 floats(&c,a,b);mul(&c);CHECK(c.xmm[0][0]==0x40a00000&&c.xmm[0][3]==0x40800000);
 floats(&c,a,b);shuffle(&c);float shuffled[]={4,3,6,5};CHECK(!memcmp(c.xmm[0],shuffled,16));
 floats(&c,a,b);low(&c);float lowval[]={7,8,3,4};CHECK(!memcmp(c.xmm[0],lowval,16));
 floats(&c,a,b);high(&c);float highval[]={1,2,5,6};CHECK(!memcmp(c.xmm[0],highval,16));
 floats(&c,a,b);unpack(&c);float interleaved[]={1,5,2,6};CHECK(!memcmp(c.xmm[0],interleaved,16));
 float nan[]={NAN,2,3,4};floats(&c,nan,b);mask(&c);CHECK(c.xmm[0][0]==UINT32_MAX&&c.xmm[0][3]==UINT32_MAX);
 float round[]={1.5f,-1.5f,0,0};unsigned expected[4][2]={{2,-2},{1,-2},{2,-1},{1,-1}};
 for(int k=0;k<4;k++){floats(&c,a,round);c.mxcsr=(unsigned)k<<13;convert(&c);CHECK((uint32_t)c.mm[0]==expected[k][0]&&(uint32_t)(c.mm[0]>>32)==expected[k][1]);}
 floats(&c,a,nan);convert(&c);CHECK((uint32_t)c.mm[0]==0x80000000u);
 c.mm[1]=((uint64_t)(uint32_t)-3<<32)|2;floats(&c,a,b);convert_back(&c);float converted[]={2,-3,3,4};CHECK(!memcmp(c.xmm[0],converted,16));
 c.eax=(uint32_t)-42;int_float(&c);CHECK(c.xmm[0][0]==0xc2280000&&c.xmm[0][3]==0x40800000);
 for(int k=0;k<4;k++){c.mxcsr=(unsigned)k<<13;c.eax=16777217;int_float(&c);CHECK(c.xmm[0][0]==(k==2 ? 0x4b800001u : 0x4b800000u));
 c.mm[1]=((uint64_t)(uint32_t)-16777217<<32)|16777217;convert_back(&c);CHECK(c.xmm[0][0]==(k==2 ? 0x4b800001u : 0x4b800000u));CHECK(c.xmm[0][1]==(k==1 ? 0xcb800001u : 0xcb800000u));}
 float half[]={0x1p-24f,0,0,0};for(int k=0;k<4;k++){c.mxcsr=(unsigned)k<<13;floats(&c,a,half);add(&c);CHECK(c.xmm[0][0]==(k==2 ? 0x3f800001u : 0x3f800000u));}
 c.mxcsr=0;fesetround(FE_DOWNWARD);floats(&c,a,half);add(&c);CHECK(c.xmm[0][0]==0x3f800000u&&fegetround()==FE_DOWNWARD);fesetround(FE_TONEAREST);
 c.ecx=0x80001000;scan(&c);CHECK(c.eax==31&&!(c.eflags&64));c.ecx=0;scan(&c);CHECK(c.eax==31&&(c.eflags&64));
 munmap(G,2*page);printf("SIMD/X87 PASS assertions=%d\\n",checks);return 0;}
'''
    with tempfile.TemporaryDirectory(prefix='simd-emitter-') as td:
        path=Path(td)/'test.c';binary=Path(td)/'test'
        for broken in (False,True):
            # The same flag/pop check must reject the exact historical failure.
            text=source.replace('x87_pop(c);','/* broken: missing compare pop */',1) if broken else source
            path.write_text(text)
            p=subprocess.run(['clang','-O1','-frounding-math','-fsanitize=address,undefined','-I',str(HERE),str(path),'-o',str(binary)],capture_output=True,text=True)
            if p.returncode:raise RuntimeError(p.stderr[-2000:])
            p=subprocess.run([str(binary)],capture_output=True,text=True)
            print(('broken: ' if broken else 'good: ')+p.stdout.strip()+p.stderr.strip())
            assert p.returncode==(1 if broken else 0)
    print('SIMD/X87 REGRESSION PASS (broken exit=1)')


if __name__=='__main__':main()
