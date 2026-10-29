#!/usr/bin/env python3
"""





"""
from pathlib import Path
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PRE = r'''
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <pthread.h>
static int fake_create(pthread_t *t,const pthread_attr_t *a,void *(*fn)(void *),void *p) {
 (void)t;(void)a;(void)fn;(void)p;return 0;
}
#define pthread_create fake_create
'''
POST = r'''
#undef pthread_create
uint8_t *G;int diag_on,boot_trace;uint32_t last_error;
uint64_t diag_idle_ns[IDLE_N];volatile uint64_t perf_nframes;
uint64_t host_ticks(void){return 0;}
uint64_t host_filetime(void){return 0;}
void diag_event(const char *f,...){(void)f;}
const char *diag_symbol(uint32_t a,uint32_t *off){(void)a;if(off)*off=0;return "";}
uint32_t galloc(uint32_t n){(void)n;return 0x600000;}
uint32_t call_guest(Cpu *c,uint32_t fn,int n,const uint32_t *args){(void)c;(void)fn;(void)n;(void)args;return 0;}
static unsigned checks,failures;
#define CHECK(x) do {++checks;if(!(x)){++failures;if(failures<9)printf("FAIL %d %s\n",__LINE__,#x);}}while(0)
static unsigned objects(void){unsigned n=0;for(unsigned i=1;i<MAX_KOBJ;++i)n+=kobj[i].type!=0;return n;}
int main(void){
 G=calloc(1,16*1024*1024);setenv("NFS_PADS","2",1);
 M32(0x91CF90)=0x400000;M8(0x400000)=2;M8(0x40012C)=4;
 KObj *initial;uint32_t original=knew(K_THREAD,&initial);
 initial->start=0x820BD7;initial->signaled=1;initial->cpu=calloc(1,sizeof(Cpu));M32(0x9C22E8)=original;
 // Actual creation path reclaims before allocating, keeps the one saved handle,
 // and reuses the completed guest stack. pthread_create is a recording sink.
 for(unsigned i=0;i<1000;++i){
  switch(i%4){case 0:unsetenv("NFS_PADS");break;case 1:setenv("NFS_PADS","1",1);break;default:setenv("NFS_PADS","2",1);break;}
  M8(0x400000)=i%3;M8(0x40012C)=i%2?4:0;
  free_stacks[0].lo=0x600000;free_stacks[0].size=0x100000;nfree_stacks=1;
  uint32_t tid=0,h=thread_create(0x100000,0x820BD7,0,0,&tid);
  CHECK(h==original);CHECK(objects()==1);CHECK(tid==i+2);
  KObj *t=kget(h);CHECK(t&&t->cpu&&t->start==0x820BD7);CHECK(nfree_stacks==0);
  t->signaled=1;t->running=0;M32(0x9C22E8)=h;
 }
 // Capture while running, then finish/reclaim while a watchdog still holds the list.
 KObj *retiring=kget(original);retiring->cpu->eax=0x12345678;retiring->running=1;
 pthread_t ths[64];Cpu *cpus[64];uint32_t tids[64];
 int n=threads_list(ths,cpus,tids,64);CHECK(n==2);
 Cpu *held=cpus[1];CHECK(held!=retiring->cpu);CHECK(held->eax==0x12345678);
 retiring->running=0;retiring->signaled=1;split_sound_worker_reap(0x820BD7);
 CHECK(objects()==0);CHECK(held->eax==0x12345678);
 KObj *again;CHECK(knew(K_THREAD,&again)==original);again->cpu=calloc(1,sizeof(Cpu));
 // Every excluded state preserves ALL guest bytes and the complete object table.
 for(unsigned mode=6;mode<12;++mode){
  setenv("NFS_PADS","2",1);M32(0x91CF90)=0x400000;M8(0x400000)=2;M8(0x40012C)=4;
  KObj *t=kget(original);t->type=K_THREAD;t->start=0x820BD7;t->running=0;t->signaled=1;
  uint32_t start=0x820BD7;M32(0x9C22E8)=original;
  switch(mode){
   case 0:unsetenv("NFS_PADS");break;case 1:setenv("NFS_PADS","1",1);break;
   case 2:setenv("NFS_PADS","invalid",1);break;case 3:M32(0x91CF90)=0;break;
   case 4:M8(0x400000)=1;break;case 5:M8(0x40012C)=0;break;
   case 6:start=0x85D6E0;break;case 7:t->running=1;break;
   case 8:t->signaled=0;break;case 9:t->type=K_EVENT;break;
   case 10:t->start=0x85D6E0;break;case 11:M32(0x9C22E8)=0;break;
  }
  uint8_t *memory=malloc(16*1024*1024);memcpy(memory,G,16*1024*1024);
  KObj saved[MAX_KOBJ];memcpy(saved,kobj,sizeof saved);
  split_sound_worker_reap(start);
  CHECK(!memcmp(memory,G,16*1024*1024));CHECK(!memcmp(saved,kobj,sizeof saved));free(memory);
  // A broken guard may have retired the record; restore a fresh owned CPU for the next mode.
  if(!kobj[(original-KOBJ_BASE)/4].type){memcpy(kobj,saved,sizeof saved);kobj[(original-KOBJ_BASE)/4].cpu=calloc(1,sizeof(Cpu));}
 }
 printf("splitlife threads: %u checks / %u failures; 1000 mixed-mode loads, 6 excluded-state snapshots, held watchdog snapshot\n",checks,failures);
 free(G);return failures?1:0;
}
'''

def main():
    src = (ROOT/'runtime/threads.c').read_text()
    # Relocate the fixed Windows main TEB into this fixture's 16 MiB guest.
                                                                          
    assert src.count('#define MAIN_TEB 0x7FFDE000u')==1
    src=src.replace('#define MAIN_TEB 0x7FFDE000u','#define MAIN_TEB 0x800000u')
    mutants = {
        'no-reclaim': ('    split_sound_worker_reap(start);', '    (void)start;'),
        'incoming-mode': ('if (start != 0x00820BD7u) return;', 'if (start != 0x00820BD7u || !getenv("NFS_PADS") || strcmp(getenv("NFS_PADS"),"2")) return;'),
        'borrowed-cpu': ('snapshots[n]=*kobj[i].cpu; cpus[n]=&snapshots[n];', 'snapshots[n]=*kobj[i].cpu; cpus[n]=kobj[i].cpu;'),
        'live-thread': ('old->running || !old->signaled', '!old->signaled'),
        'unfinished-thread': ('old->running || !old->signaled', 'old->running'),
        'wrong-thread': ('old->start != start ||', ''),
        'wrong-object': ('old->type != K_THREAD ||', ''),
    }
    with tempfile.TemporaryDirectory(prefix='splitlife-threads-') as tmp:
        tmp=Path(tmp)
        for name,source in [('positive',src)]+[(n,src.replace(a,b,1)) for n,(a,b) in mutants.items()]:
            for a,_ in mutants.values():assert src.count(a)==1,a
            inc=tmp/'threads.inc';inc.write_text(source)
            c=tmp/'test.c';c.write_text(PRE+'\n#include "threads.inc"\n'+POST)
            exe=tmp/'test'
            p=subprocess.run(['clang','-O1','-g','-w','-fsanitize=address,undefined','-fno-sanitize-recover=all',
                '-I'+str(ROOT/'runtime'),'-I'+str(ROOT/'recomp'),'-I'+str(ROOT/'recomp/out'),str(c),'-o',str(exe)],capture_output=True,text=True)
            if p.returncode:raise RuntimeError(p.stderr[-4000:])
            p=subprocess.run([str(exe)],capture_output=True,text=True,env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0'})
            print(name+': '+(p.stdout+p.stderr)[-1800:].strip())
            assert (p.returncode==0 if name=='positive' else p.returncode!=0),name
            if name=='borrowed-cpu':assert 'heap-use-after-free' in p.stderr,p.stderr[-1500:]
    print(f'SPLIT LIFECYCLE THREADS PASS ({len(mutants)}/{len(mutants)} mutants rejected)')

if __name__=='__main__':main()
