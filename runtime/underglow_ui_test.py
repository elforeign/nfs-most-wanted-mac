"""Unsupported persistence tokens: fixed 6/6, predecessor 540af7d2 0/6.
Bounded diagnostic fixture executes production C; owned:<car> creates the named stock car (any case).
Good 30/30; career-guard omission fails 10/30 and deadline omission fails 13/30; matching a fixed is300 instead of
the requested name fails 7/30; owned:<car> not waiting out the closing bonus Dialog.fng fails 1/30. Extracted input seam 6/6; omitted stop fails 3/6.
Selected-car shop step: good 9/9; owned-bits omission fails 2, missing dialog
wait 1, and starting only at elapsed zero 2.
Native menu: underglow_menu_test.py."""
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
int underglow_render_scene_step(const char *name,uint64_t elapsed) { return -1; }
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
 case 0x0056ECC0: return 0x1428;
 case 0x00527940: ++memcard_requests;return 0;
 default: abort();
 }
}
'''
POST_SEL = r'''
int main(int argc,char **argv) {
 G=calloc(1,16*1024*1024);if(!G)return 2;
 M32(0x91CF90)=0x5000;M32(0x5010)=0x1000;M32(0x10A8)=82;
 strcpy((char *)G+0x1000,"TEST");M32(0x1428)=82;G[0x1438]=32;M32(0x1434)=2;
 const char *key=(void *)1;int failures=0,checks=0;
 #define CHECK(x) do{++checks;if(!(x)){++failures;fprintf(stderr,"FAIL %d: %s\n",__LINE__,#x);}}while(0)
 CHECK(underglow_ui_step("shop",0,"Wrong.fng",&key)==-1);CHECK(!shops);
 CHECK(underglow_ui_step("shop",0,"Dialog.fng",&key)==0);CHECK(!shops);
 M32(0x1434)=0;CHECK(underglow_ui_step("shop",0,"MainMenu_Sub.fng",&key)==-1);CHECK(!shops);M32(0x1434)=2;
 CHECK(underglow_ui_step("shop",1,"MainMenu_Sub.fng",&key)==0);CHECK(shops==1);
 CHECK(underglow_ui_step("shop",1,"CustomizeMain.fng",&key)==1);
 printf("selected-car shop: %d checks, %d failures\n",checks,failures);return failures?1:0;
}
'''
POST = r'''
int main(int argc,char **argv) {
 G=calloc(1,16*1024*1024);if(!G)return 2;
 M32(0x91CF90)=0x5000;M32(0x5010)=0x1000;
 strcpy((char *)G+0x1000,"TEST");strcpy((char *)G+0x9000,"CARRERAGT");
 for(int i=0;i<200;++i)M32(0x1414+i*20)=0xFFFFFFFF;
 M32(0x1414)=0;G[0x1424]=255;
 const char *key=(void *)1;int failures=0,checks=0;
 #define CHECK(x) do{++checks;if(!(x)){++failures;fprintf(stderr,"FAIL %d: %s\n",__LINE__,#x);}}while(0)
 career=0;CHECK(underglow_ui_step("owned:carreragt",0,"MainMenu_Sub.fng",&key)==-1);CHECK(!creates&&!key);
 career=1;CHECK(underglow_ui_step("owned:carreragt",1800,"MainMenu_Sub.fng",&key)==-1);CHECK(!creates);
 CHECK(underglow_ui_step("owned:carreragt",0,"Wrong.fng",&key)==-1);CHECK(!creates);
 diag_on=0;CHECK(underglow_ui_step("owned:carreragt",0,"MainMenu_Sub.fng",&key)==-1);CHECK(!creates);diag_on=1;
 CHECK(underglow_ui_step("owned:is300",0,"MainMenu_Sub.fng",&key)==-1);CHECK(!creates);
 CHECK(underglow_ui_step("owned:",0,"MainMenu_Sub.fng",&key)==-1);CHECK(!creates);
 CHECK(underglow_ui_step("owned:carreragt",1,"Dialog.fng",&key)==0);CHECK(!creates);
 CHECK(underglow_ui_step("owned:carreragt",0,"MainMenu_Sub.fng",&key)==1);CHECK(creates==1&&M32(0x10A8)==1);
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
              'deadline-missing':source.replace('if (elapsed >= 1800 || host_ticks()-fixture_started >= 30000000000ull)','if (0)'),
              'owned-dialog-wait-missing':source.replace('(!strcmp(step,"shop") || !strncmp(step,"owned:",6))','!strcmp(step,"shop")'),
              'car-name-ignored':source.replace('strcasecmp((char *)G+name,want)','strcasecmp((char *)G+name,"is300")')}
    assert all(body!=source for name,body in variants.items() if name!='good')
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
        for name,body in [('selected-good',source),('selected-owned-bits-missing',source.replace('!(M32(row+12)&6)','0')),
                          ('selected-dialog-wait-missing',source.replace('if ((!strcmp(step,"shop") || !strncmp(step,"owned:",6)) && !fixture_row && pkg && !strcmp(pkg,"Dialog.fng")) return 0;', '')),
                          ('selected-start-only-zero',source.replace('if (!fixture_shop_started)', 'if (!elapsed)'))]:
            (tmp/'sel.c').write_text(PRE+body+POST_SEL)
            p=subprocess.run(['clang','-O1','-I'+str(ROOT/'runtime'),'-I'+str(ROOT/'recomp'),str(tmp/'sel.c'),'-o',str(tmp/'sel')],capture_output=True,text=True)
            assert p.returncode==0,p.stderr[-2000:]
            p=subprocess.run([str(tmp/'sel')],capture_output=True,text=True)
            print(name+': '+p.stdout.strip())
            assert p.returncode==(0 if name=='selected-good' else 1),p.stderr[-2000:]

if __name__=='__main__':main()
