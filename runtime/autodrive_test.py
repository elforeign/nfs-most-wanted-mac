#!/usr/bin/env python3
"""Production diagnostic AI dispatch; recording guest calls, no game/GPU/Unicorn.
ASan/UBSan: 62 checks / 0 failures, 134,217,728 guest bytes + CPU snapshots identical for =1/disabled.
4/4 mutants rejected: P2 omitted 2, P1 reused 1, NIS-owned 10, unscoped P2 1 failures.
"""
from pathlib import Path
import os
import re
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
PRE=r'''
#include "host.h"
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
uint8_t *G;
static unsigned calls,ids[4],split,checks,failures;
#define CHECK(x) do {++checks;if(!(x)){++failures;if(failures<8)printf("FAIL %d %s\n",__LINE__,#x);}}while(0)
int split_view_rect(uint32_t v,uint32_t r[4]){(void)r;return split&&v==0x9196C0;}
uint32_t call_guest(Cpu*c,uint32_t fn,int n,const uint32_t*a){
 CHECK(fn==0x6123B0&&n==1);ids[calls++]=a[0];c->esp-=40;c->eax=0xABC+a[0];
 M32(0x400000+4*a[0])+=a[0]+1;return c->eax;
}
static void baseline(Cpu*c,int enabled){
 if(enabled&&!M32(0x9885C8)){uint32_t sp=c->esp,a[1]={0};call_guest(c,0x6123B0,1,a);c->esp=sp;}
}
'''
POST=r'''
int main(void){
 G=calloc(1,16*1024*1024);uint8_t *snapshot=malloc(16*1024*1024);
 for(unsigned nis=0;nis<2;++nis)for(split=0;split<2;++split)for(int mode=0;mode<3;++mode){
  Cpu c={0};c.esp=0xF00000;c.eax=123;M32(0x9885C8)=nis;calls=0;
  autodrive_players(&c,mode);unsigned expected=nis||!mode?0:(mode==2&&split?2:1);
  CHECK(calls==expected);CHECK(c.esp==0xF00000);if(expected)CHECK(ids[0]==0);if(expected==2)CHECK(ids[1]==1);
 }
 // Original =1 and disabled path: exact guest memory + CPU/call snapshots, both NIS states and layouts.
 for(unsigned nis=0;nis<2;++nis)for(split=0;split<2;++split)for(int mode=0;mode<2;++mode){
  Cpu c={0};c.esp=0xF00000;c.eax=456;M32(0x9885C8)=nis;calls=0;
  M32(0x400000)=M32(0x400004)=0;
  baseline(&c,mode);Cpu old=c;unsigned old_calls=calls;memcpy(snapshot,G,16*1024*1024);
  c.esp=0xF00000;c.eax=456;calls=0;M32(0x400000)=M32(0x400004)=0;
  autodrive_players(&c,mode);CHECK(!memcmp(&c,&old,sizeof c));CHECK(calls==old_calls);
  CHECK(!memcmp(snapshot,G,16*1024*1024));
 }
 printf("autodrive: %u checks / %u failures; 8 exact 16 MiB + CPU snapshots\n",checks,failures);
 free(snapshot);free(G);return failures?1:0;
}
'''
def main():
    src=(ROOT/'runtime/d3d9.m').read_text()
    body=re.search(r'static void autodrive_players\(Cpu \*c, int mode\) \{.*?\n\}',src,re.S)[0]
    mutants={
        'P2-omitted':('mode == 2 && split_view_rect(0x009196C0u, NULL) ? 2 : 1','1'),
        'P1-reused':('uint32_t a[1] = {i};','uint32_t a[1] = {0};'),
        'NIS-owned':('if (!mode || M32(0x009885C8u)) return;','if (!mode) return;'),
        'unscoped-P2':('mode == 2 && split_view_rect(0x009196C0u, NULL)','mode == 2'),
    }
    with tempfile.TemporaryDirectory(prefix='autodrive-test-') as tmp:
        tmp=Path(tmp)
        for name,b in [('positive',body)]+[(n,body.replace(a,z,1))for n,(a,z)in mutants.items()]:
            for a,_ in mutants.values():assert body.count(a)==1,a
            c=tmp/'test.c';c.write_text(PRE+b+POST);exe=tmp/'test'
            p=subprocess.run(['clang','-O1','-g','-w','-fsanitize=address,undefined','-I'+str(ROOT/'runtime'),'-I'+str(ROOT/'recomp'),'-I'+str(ROOT/'recomp/out'),str(c),'-o',str(exe)],capture_output=True,text=True)
            if p.returncode:raise RuntimeError(p.stderr[-3000:])
            p=subprocess.run([str(exe)],capture_output=True,text=True,env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0'})
            print(name+': '+(p.stdout+p.stderr)[-1200:].strip());assert p.returncode==(0 if name=='positive' else 1),name
    print('AUTODRIVE PASS (4/4 mutants rejected)')
if __name__=='__main__':main()
