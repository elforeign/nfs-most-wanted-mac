"""Unsupported persistence tokens: fixed 6/6, predecessor 540af7d2 0/6.
Bounded diagnostic fixture executes production C. Good 24/24; career-guard omission fails 6/24 and
deadline omission fails 7/24. Extracted input seam 6/6; omitted stop fails 3/6.
Native original/mutation UI proof pending."""
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PRE = r'''
#include "host.h"
#include <stdlib.h>
#include <stdarg.h>
uint8_t *G;
int diag_on=1;
static int career=1, creates, shops, memcard_requests;
static uint64_t fake_ticks;
uint64_t host_ticks(void) { return fake_ticks; }
uint32_t galloc(uint32_t n) { return 0xB00000; }
uint32_t gstr(const char *s) { strcpy((char *)G+0xA00000,s);return 0xA00000; }
void diag_event(const char *fmt,...) {}
uint32_t call_guest(Cpu *c,uint32_t fn,int n,const uint32_t *a) {
 switch(fn) {
 case 0x0050E310: return career;
 case 0x005816B0: return 10;
 case 0x00668370: return 0x9000;
 case 0x00599D20:
  ++creates;M32(0x1428)=1;M32(0x1434)=2;G[0x1438]=0;return 0x1428;
 case 0x005660A0: M32(0x10A8)=a[0];return 0;
 case 0x0050E4C0: M32(0x512C)|=a[0];return 0;
 case 0x00589D60: ++shops;M32(0x91D3D4)=a[1];return 0;
 case 0x00527940: ++memcard_requests;return 0;
 default: abort();
 }
}
'''
POST = r'''
int main(int argc,char **argv) {
 G=calloc(1,16*1024*1024);if(!G)return 2;
 M32(0x91CF90)=0x5000;M32(0x5010)=0x1000;
 strcpy((char *)G+0x1000,"TEST");strcpy((char *)G+0x9000,"is300");
 for(int i=0;i<200;++i)M32(0x1414+i*20)=0xFFFFFFFF;
 M32(0x1414)=0;G[0x1424]=255;
 const char *key=(void *)1;int failures=0,checks=0;
 #define CHECK(x) do{++checks;if(!(x)){++failures;fprintf(stderr,"FAIL %d: %s\n",__LINE__,#x);}}while(0)
 career=0;CHECK(underglow_ui_step("owned:is300",0,"MainMenu_Sub.fng",&key)==-1);CHECK(!creates&&!key);
 career=1;CHECK(underglow_ui_step("owned:is300",1800,"MainMenu_Sub.fng",&key)==-1);CHECK(!creates);
 CHECK(underglow_ui_step("owned:is300",0,"Wrong.fng",&key)==-1);CHECK(!creates);
 diag_on=0;CHECK(underglow_ui_step("owned:is300",0,"MainMenu_Sub.fng",&key)==-1);CHECK(!creates);diag_on=1;
 CHECK(underglow_ui_step("owned:is300",0,"MainMenu_Sub.fng",&key)==1);CHECK(creates==1&&M32(0x10A8)==1);
 CHECK(underglow_ui_step("shop",0,"MainMenu_Sub.fng",&key)==0);CHECK(shops==1&&!key);
 CHECK(underglow_ui_step("shop",1,"CustomizeMain.fng",&key)==1);
 for(int i=0;i<2;++i) {
  const char *step=i?"reload":"save";
  CHECK(underglow_ui_step(step,0,"CustomizeMain.fng",&key)==-1);
  CHECK(underglow_ui_step(step,1,"CustomizeMain.fng",&key)==-1);
  CHECK(!memcard_requests&&!key);
 }
 CHECK(underglow_ui_step("shop",1800,"Other.fng",&key)==-1);
 fake_ticks=31000000000ull;CHECK(underglow_ui_step("shop",1,"CustomizeMain.fng",&key)==-1);CHECK(shops==1);fake_ticks=0;
 M32(0x10A8)=2;CHECK(underglow_ui_step("shop",0,"CustomizeMain.fng",&key)==-1);CHECK(shops==1);
 printf("fixture: %d checks, %d failures\n",checks,failures);free(G);return failures?1:0;
}
'''

def adapter_test(tmp):
    user=(ROOT/'runtime/user32.m').read_text()
    start=user.index('    } else if (!strncmp(tok, "underglow:", 10))')
    end=user.index('    } else if (!strncmp(tok, "pkg:", 4))', start)
    branch=user[start:end].replace('    } else if (','    if (',1)+'    }\n'
    pre=r'''
#include <stdint.h>
#include <stdio.h>
#include <string.h>
static int step,sequence_failed,calls,presses,result;
static uint64_t step_start=7,release_at;
static char held[16];
static const char *next_key;
static const char *fe_package_in_control(void) { return "MC_Main.fng"; }
int underglow_ui_step(const char *s,uint64_t elapsed,const char *pkg,const char **key) {
 ++calls;if(strcmp(s,"save")||strcmp(pkg,"MC_Main.fng"))return -1;
 *key=next_key;return result;
}
static void press(const char *k,int down,uint64_t frame) { ++presses; }
static void tick(const char *tok,uint64_t frame) {
 uint64_t real_frame=frame;
'''
    guard='    if(sequence_failed)return;'
    assert guard in user
    post=r'''
}
int main(void) {
 int bad=0;
 tick("underglow:save",7);bad+=step||sequence_failed||calls!=1||presses;
 result=1;tick("underglow:save",8);bad+=step!=1||step_start||calls!=2;
 result=-1;tick("underglow:save",9);bad+=!sequence_failed||calls!=3;
 tick("underglow:save",10);bad+=calls!=3||presses;
 sequence_failed=0;result=0;next_key="RIGHT";tick("underglow:save",11);
 bad+=presses!=1||strcmp(held,"RIGHT")||release_at!=15;
 tick("unrelated",12);bad+=calls!=4;
 printf("input seam: 6 checks, %d failures\n",bad);return !!bad;
}
'''
    for name,code in [('good',branch),('failure-stop-missing',branch.replace('sequence_failed = 1;','(void)0;'))]:
        (tmp/'adapter.c').write_text(pre+guard+code+post)
        p=subprocess.run(['clang',str(tmp/'adapter.c'),'-o',str(tmp/'adapter')],capture_output=True,text=True)
        assert p.returncode==0,p.stderr[-2000:]
        p=subprocess.run([str(tmp/'adapter')],capture_output=True,text=True)
        print(name+': '+p.stdout.strip())
        assert p.returncode==(0 if name=='good' else 1),p.stderr[-2000:]

def main():
    source=(ROOT/'runtime/underglow_ui.c').read_text()
    variants={'good':source,
              'career-guard-missing':source.replace('if (!(call_guest(&c,0x0050E310u,0,NULL)&255))','if (0)'),
              'deadline-missing':source.replace('if (elapsed >= 1800 || host_ticks()-fixture_started >= 30000000000ull)','if (0)')}
    with tempfile.TemporaryDirectory(prefix='ug-ui-') as directory:
        tmp=Path(directory)
        adapter_test(tmp)
        for name,body in variants.items():
            (tmp/'test.c').write_text(PRE+body+POST)
            p=subprocess.run(['clang','-O1','-I'+str(ROOT/'runtime'),'-I'+str(ROOT/'recomp'),str(tmp/'test.c'),'-o',str(tmp/'test')],capture_output=True,text=True)
            assert p.returncode==0,p.stderr[-2000:]
            p=subprocess.run([str(tmp/'test')],capture_output=True,text=True)
            print(name+': '+p.stdout.strip())
            assert p.returncode==(0 if name=='good' else 1),p.stderr[-2000:]

if __name__=='__main__':main()
