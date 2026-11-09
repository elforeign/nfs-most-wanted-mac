#!/usr/bin/env python3
"""Translated shop notification: original career gate vs built-in confirmation extension.
7e5d3dd0 control: 3 dialog failures / 48 cases; production: 0 / 48.
Confirmation continues through translated MaxOut call with career bits intact.
"""
from pathlib import Path
import re
import subprocess
import tempfile
import runpy
import os

ROOT = Path(__file__).resolve().parents[1]
function = runpy.run_path(str(ROOT/'runtime/padprompts_shop_test.py'))['function']
PRE = r'''
#include "host.h"
#include <stdlib.h>
#include <string.h>
uint8_t *G;
static int dialogs,refreshes,maxouts,bases,bad,career_bits,test_bits;
static void context(void){bad+=M8(0x512C)!=career_bits||M8(0x9B9E8D)!=test_bits;}
void dispatch(Cpu*c,uint32_t fn){context();if(fn!=0x12345678)abort();
 bad+=c->ecx!=0x1000;refreshes++;c->esp+=4;}
void f_007BB680(Cpu*c){context();bases++;c->esp+=20;}
void underglow_menu_notify(uint32_t self,uint32_t msg){}
void f_007B5BB0(Cpu*c){context();maxouts++;bad+=c->ecx!=0x9B9EC0;c->esp+=4;}
void f_00598DD0(Cpu*c){context();dialogs++;
 uint32_t want[]={0x6000,0x890978,3,0x70E01038,0x417B25E4,0x6820E23E,0xB4EDEB6D,0,0x892CB612};
 for(int i=0;i<9;i++)bad+=arg(c,i)!=want[i];c->esp+=4;}
uint32_t call_guest(Cpu*c,uint32_t fn,int n,const uint32_t*a){
 uint32_t esp=c->esp;for(int i=n-1;i>=0;i--){c->esp-=4;M32(c->esp)=a[i];}
 c->esp-=4;M32(c->esp)=0;
 if(fn==0x00598DD0)f_00598DD0(c);else dispatch(c,fn);
 c->esp=esp;return c->eax;
}
'''
POST = r'''
int main(int argc,char**argv){
 G=calloc(1,16*1024*1024);if(!G)return 2;
 int errors=0,cases=0;
 uint32_t categories[]={0x802,0x801,0x103},events[]={0xC519BFC3,0x6820E23E,0xB4EDEB6D,0xDEADBEEF};
 const int setting=1;
 for(int career=0;career<2;career++)
 for(int test=0;test<2;test++)for(int cat=0;cat<3;cat++)for(int event=0;event<4;event++){
  dialogs=refreshes=maxouts=bases=bad=0;
  career_bits=0xA0|career;test_bits=test;
  M32(0x91CF90)=0x5000;M8(0x512C)=career_bits;M8(0x9B9E8D)=test_bits;
  M32(0x1000)=0x9000;M32(0x900C)=0x12345678;
  M32(0x1010)=0x6000;M32(0x1038)=0x2000;M32(0x2050)=0x501;M32(0x1150)=categories[cat];
  Cpu c={0};c.ecx=0x1000;c.esp=0x8000;c.ebx=17;c.esi=19;c.edi=23;c.ebp=29;
  M32(0x8004)=events[event];
  f_007BB870(&c);
  int want=event==0&&cat==0&&(setting||(!career&&!test));
  errors+=dialogs!=want||maxouts!=(event==1)||refreshes!=(want||event==1||event==2)||bases!=1||bad;
  errors+=c.esp!=0x8014||c.ebx!=17||c.esi!=19||c.edi!=23||c.ebp!=29;
  cases++;
 }
 printf("translated gate cases=%d dialog/ABI/state failures=%d\n",cases,errors);
 free(G);return errors?1:0;
}
'''


def main():
    translated = (ROOT/'recomp/out/speed_52.c').read_text()
    name = 'f_007BB870_orig' if 'void f_007BB870_orig(' in translated else 'f_007BB870'
    original = function(translated, name).replace('void f_007BB870(', 'void f_007BB870_orig(')
    feature = (ROOT/'runtime/maxperf_career.c').read_text()
    # Header is exercised by the translated RefreshHeader harness, not this gate harness.
    feature = feature.replace(function(feature, 'maxperf_career_header'), '')
    known = {'f_007BB680', 'f_007B5BB0', 'f_00598DD0'}
    unused = sorted(set(re.findall(r'\b(f_[0-9A-F]{8})\(c\)', original))-known)
    stubs = ''.join('void '+name+'(Cpu*c){(void)c;abort();}\n' for name in unused)
    text = PRE+stubs+original+feature+POST
    hook = function(feature, 'f_007BB870')
    variants = [('production', text), ('7e5d3dd0-gate', text.replace(hook, 'void f_007BB870(Cpu*c){f_007BB870_orig(c);}\n'))]
    with tempfile.TemporaryDirectory(prefix='maxperf-career-') as directory:
        tmp = Path(directory)
        for label, body in variants:
            (tmp/'test.c').write_text(body)
            p = subprocess.run(['clang', '-O1', '-I'+str(ROOT/'runtime'), '-I'+str(ROOT/'recomp'), str(tmp/'test.c'), '-o', str(tmp/'test')],capture_output=True,text=True)
            assert p.returncode == 0, p.stderr[-3000:]
            for spelling, expected in [(None, 1), ('on', 1), ('retail', 1)]:
                env = dict(os.environ);env.pop('NFS_MAXPERF_CAREER', None)
                if spelling: env['NFS_MAXPERF_CAREER'] = spelling
                p = subprocess.run([str(tmp/'test'), str(expected)], env=env,capture_output=True,text=True)
                print(label+'/'+str(spelling)+': '+p.stdout.strip())
                assert p.returncode == (0 if label=='production' else 1), p.stderr[-2000:]


if __name__ == '__main__':
    main()
