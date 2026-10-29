#!/usr/bin/env python3
"""Real dump_mapping: guard-page retail four-byte FE object, ASan/UBSan, no game.
6 positive modes pass (unset/1/invalid/02/empty/2); 4/4 presence-only mutants
(1/invalid/02/empty) fault at fe+4 with AddressSanitizer. Only 2 has an eight-byte FE.
"""
from pathlib import Path
import os
import subprocess
import tempfile
ROOT=Path(__file__).resolve().parents[1]
PRE=r'''
#include "host.h"
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
uint8_t *G;static FILE *inv;
'''
POST=r'''
int main(int argc,char **argv){
 const size_t bytes=16*1024*1024,page=getpagesize();
 G=mmap(NULL,bytes+page,PROT_READ|PROT_WRITE,MAP_ANON|MAP_PRIVATE,-1,0);
 if(G==MAP_FAILED||mprotect(G+bytes,page,PROT_NONE))return 2;
 if(argc<2)return 2;
 if(!strcmp(argv[1],"unset"))unsetenv("NFS_PADS");else setenv("NFS_PADS",argv[1],1);
 int split=!strcmp(argv[1],"2");uint32_t fe=bytes-(split?8:4);M32(0x91CAD0)=fe;
 for(int i=0;i<(split?2:1);++i){uint32_t q=0x200000+i*0x1000,map=q+0x400;
  M32(fe+4*i)=q;M32(q+0x270)=map;M32(q+0x268)=8;}
 inv=tmpfile();dump_mapping();long end=ftell(inv);rewind(inv);char text[2048]={0};
 fread(text,1,sizeof(text)-1,inv);fclose(inv);
 unsigned lines=0;for(char *p=text;(p=strstr(p,"FE input mapping"));p+=16)++lines;
 if(lines!=(split?2:1)||!end)return 1;
 printf("%s: %u queues; retail FE %u bytes, guard intact\n",argv[1],lines,split?8:4);
 munmap(G,bytes+page);return 0;
}
'''

def main():
    source=(ROOT/'runtime/padprompts.c').read_text().split('static void dump_mapping(void)',1)[1].split('void prompts_diag_frame',1)[0]
    source='static void dump_mapping(void)'+source
    broken=source.replace('pads && !strcmp(pads, "2")','pads != NULL')
    assert broken!=source
    with tempfile.TemporaryDirectory(prefix='padprompts-mapping-') as tmp:
        tmp=Path(tmp)
        for label,body in [('positive',source),('presence-only',broken)]:
            f=tmp/'test.c';f.write_text(PRE+body+POST);exe=tmp/'test'
            p=subprocess.run(['clang','-O1','-g','-fsanitize=address,undefined','-fno-sanitize-recover=all','-I'+str(ROOT/'runtime'),'-I'+str(ROOT/'recomp'),'-I'+str(ROOT/'recomp/out'),str(f),'-o',str(exe)],capture_output=True,text=True)
            assert p.returncode==0,p.stderr[-3000:]
            for mode in ['unset','1','invalid','02','','2']:
                p=subprocess.run([str(exe),mode],capture_output=True,text=True,env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0'})
                if label=='positive':
                    assert p.returncode==0,(mode,p.stdout,p.stderr[-1500:])
                    print(p.stdout.strip())
                elif mode not in ('unset','2'):
                    assert p.returncode!=0,mode+' broken variant survived'
                    assert 'AddressSanitizer' in p.stderr,p.stderr[-1500:]
                    print('broken presence-only '+repr(mode)+': guard read rejected')
        print('PADPROMPTS MAPPING PASS: 6 positive cases, 4/4 broken cases rejected')
if __name__=='__main__':main()
