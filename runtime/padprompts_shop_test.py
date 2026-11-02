#!/usr/bin/env python3
"""Translated CustomizeSub::RefreshHeader, real recursive visibility and pad hooks.
7e5d3dd0 On regression and missing-mirror controls must fail; production must pass.
Retail-gate control: 234 failures / 288 cases (216 before the career prompt moved above Cash/Total); fixed: 0 / 288. Missing-mirror control: 144 failures. Both groups and their children checked.
Optional --source runs the identical harness against a frozen older source.
No assets, game, controller or graphics device required.
"""
from pathlib import Path
import argparse
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def function(source, name):
    match = re.search(r'^void ' + name + r'\(Cpu \*[^\n]*\) \{', source, re.M)
    assert match, name
    return source[match.start():source.index('\n}', match.end())+2]+'\n'


PRE = r'''
#include "host.h"
#include <stdlib.h>
#include "settings.h"
void settings_add(Setting*s){(void)s;}
void f_007BB870_orig(Cpu*c){(void)c;abort();}
uint8_t *G;
void dispatch(Cpu*c,uint32_t fn){(void)c;(void)fn;abort();}
static int enabled, shop_mirror;
uint32_t galloc(uint32_t n){(void)n;return 0xA000;}
static int prompt_mode, missing, layout_calls, layout_errors;
static int mode(void) { return prompt_mode; }
void f_005B8190(Cpu *c);
void f_005B7DE0(Cpu *c);
void f_005B13D0(Cpu *c);
void bad_jump(Cpu*c,uint32_t p){(void)c;(void)p;abort();}
void f_005B1140(Cpu*c){c->eax=0;c->esp+=4;}
void f_00514CC0(Cpu *c);
void f_00514C70(Cpu *c);
void f_00524850(Cpu *c) {
 uint32_t h=arg(c,1);
 c->eax=h==0x8805E665u?0x2000:h==0x5AEC8D91u&&!missing?0x3000:0;
 c->esp+=4;
}
void f_007B0E00(Cpu*c){c->esp+=4;}
void f_0056BC10(Cpu*c){c->eax=0x4000;c->esp+=4;}
void f_004647E0(Cpu*c){c->esp+=4;}
void f_0056BB20(Cpu*c){c->eax=0;c->esp+=4;}
void f_005252D0(Cpu*c){c->esp+=4;}
void f_005251A0(Cpu*c){c->eax=0;c->esp+=4;}
void f_00515B90(Cpu*c){c->esp+=4;}
uint32_t call_guest(Cpu*c,uint32_t fn,int n,const uint32_t*a) {
 uint32_t esp=c->esp;
 for(int i=n-1;i>=0;i--){c->esp-=4;M32(c->esp)=a[i];}
 c->esp-=4;M32(c->esp)=0;
 switch(fn){case 0x00524850:f_00524850(c);break;
 case 0x00514CC0:f_00514CC0(c);break;case 0x00514C70:f_00514C70(c);break;
 case 0x005B8190: {
  uint32_t v=arg(c,0);float y;memcpy(&y,G+v+4,4);
  int show=enabled && (M8(0x512C)||M8(0x9B9E8D)) && M32(0x1150)==0x802;
  float want=c->ecx==0x2000?-103.0f:-87.0f; // keyboard 156<->53, console 117<->30
  layout_errors+=(c->ecx!=0x3000&&c->ecx!=0x2000) || arg(c,1)!=1 || y!=(show?want:-want);
  layout_errors+=M32(v)!=0||M32(v+8)!=0;layout_calls++;
  f_005B8190(c);
  break;
 }
 default:abort();}
 c->esp=esp;return c->eax;
}
'''
POST = r'''
int main(void) {
 G=calloc(1,16*1024*1024);if(!G)return 2;
 int errors=0,cases=0;uint32_t categories[]={0x802,0x801,0x103};
 for(int setting=0;setting<2;setting++)for(prompt_mode=0;prompt_mode<3;prompt_mode++)for(missing=0;missing<2;missing++)
 for(int career=0;career<2;career++)for(int test=0;test<2;test++)
 for(int cat=0;cat<3;cat++)for(int hidden=0;hidden<2;hidden++) {
  enabled=setting;layout_calls=layout_errors=0;M32(0x302C)=0x7000;
  M32(0x7020)=0x42EA0000;M32(0x701C)=0xC35E0000;M32(0x7024)=0x43750000;
  M32(0x304C)=0xB000;M32(0x8A2BF0)=0x1C;
  M32(0x202C)=0x7100;M32(0x204C)=0;MF32(0x7100+0x1C)=-241;MF32(0x7100+0x20)=156;MF32(0x7100+0x24)=40;
  for(int k=0;k<2;k++){uint32_t script=0xB000+k*0x100,track=0xC000+k*0x100;
   M32(script+4)=k?0:0xB100;M32(script+0x1C)=1;M32(script+0x20)=track;
   M8(track+7)=0x1C;MF32(track+0x18)=-222;MF32(track+0x1C)=117;MF32(track+0x20)=k?125:245;}
  M32(0x91CF90)=0x5000;M8(0x512C)=career;M8(0x9B9E8D)=test;
  M32(0x1010)=0x6000;M32(0x1150)=categories[cat];
  for(int j=0;j<2;j++){uint32_t o=0x2000+j*0x1000,ch=o+0x100;
   M32(o+0x18)=5;M32(o+0x60)=1;M32(o+0x64)=ch;
   M32(o+0x1C)=(j?0x40:8)|hidden;M32(ch+0x1C)=(j?0x40:8)|hidden;}
  Cpu c={0};c.ecx=0x1000;c.esp=0x8000;c.ebx=17;c.esi=19;c.edi=23;c.ebp=29;
  f_007B1070(&c);
  int want=cat!=0||(!setting&&(career||test));
  errors+=layout_errors||layout_calls!=(setting&&(career||test)&&cat==0)*(missing?1:2);
  float y=setting&&(career||test)&&cat==0?30:117;
  float pcy=setting&&(career||test)&&cat==0?53:156;
  errors+=MF32(0x7120)!=pcy||MF32(0x711C)!=-241||MF32(0x7124)!=40;
  if(!missing){errors+=MF32(0x7020)!=y;
   for(int k=0;k<2;k++){uint32_t track=0xC000+k*0x100;
    errors+=MF32(track+0x18)!=-222||MF32(track+0x1C)!=y||MF32(track+0x20)!=(k?125:245);}}

  errors+=(M32(0x201C)&1)!=want;errors+=(M32(0x211C)&1)!=want;
  if(!missing){errors+=(M32(0x301C)&1)!=want;errors+=(M32(0x311C)&1)!=want;}
  else {errors+=(M32(0x301C)&1)!=hidden;errors+=(M32(0x311C)&1)!=hidden;}
  errors+=c.esp!=0x8004||c.ebx!=17||c.esi!=19||c.edi!=23||c.ebp!=29;
  errors+=(M32(0x301C)&0x40)==0;
  // Live On/Retail transition restores the authored layout without changing X/Z tracks.
  if(!missing){Cpu saved=c;enabled=!setting;maxperf_career_header(&c,0x1000);
   float next_y=!setting&&(career||test)&&cat==0?30:117;
   errors+=memcmp(&saved,&c,sizeof c)!=0||MF32(0x7020)!=next_y||MF32(0x7120)!=(!setting&&(career||test)&&cat==0?53:156);
   for(int k=0;k<2;k++){uint32_t track=0xC000+k*0x100;
    errors+=MF32(track+0x18)!=-222||MF32(track+0x1C)!=next_y||MF32(track+0x20)!=(k?125:245);}
  }
  cases++;
 }
 printf("shop prompt cases=%d visibility/ABI failures=%d\n",cases,errors);
 free(G);return errors?1:0;
}
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, default=ROOT/'runtime/padprompts.c')
    args = parser.parse_args()
    source = args.source.read_text()
    assert re.search(r'^007B1070\s', (ROOT/'recomp/hooks.txt').read_text(), re.M)
    translated = next(p for p in (ROOT/'recomp/out').glob('speed_*.c')
                      if 'void f_007B1070(' in p.read_text())
    translated_source = translated.read_text()
    name = 'f_007B1070_orig' if 'void f_007B1070_orig(' in translated_source else 'f_007B1070'
    refresh = function(translated_source, name).replace('void f_007B1070(', 'void f_007B1070_orig(')
    visibility = (ROOT/'recomp/out/speed_17.c').read_text()
    visible = function(visibility, 'f_00514CC0_orig')
    invisible = function(visibility, 'f_00514C70')
    hook = (function(source, 'f_007B1070') if 'void f_007B1070(Cpu *c) {' in source
            else 'void f_007B1070(Cpu*c){f_007B1070_orig(c);}\n')
    with tempfile.TemporaryDirectory(prefix='shop-prompts-') as directory:
        tmp = Path(directory)
        feature = (ROOT/'runtime/maxperf_career.c').read_text()
        feature = feature[:feature.index('void f_007BB870_orig')]
        position = (ROOT/'recomp/out/speed_23.c').read_text()
        setters = function(position, 'f_005B8190')+function(position, 'f_005B7DE0')+function(position, 'f_005B13D0')
        text = PRE+feature+setters+invisible+visible+function(source, 'f_00514CC0')+refresh+hook+POST
        variants = [('production', text)]
        if args.source == ROOT/'runtime/padprompts.c':
            variants.append(('missing-mirror', text.replace(hook, 'void f_007B1070(Cpu*c){uint32_t self=c->ecx;f_007B1070_orig(c);maxperf_career_header(c,self);}\n')))
            variants.append(('retail-gate', text.replace('maxperf_career_header(c, self);', '/* frozen retail header */')))
        for label, body in variants:
            (tmp/'test.c').write_text(body)
            compile_result = subprocess.run(['clang', '-O1', '-I'+str(ROOT/'runtime'), '-I'+str(ROOT/'recomp'),
                            str(tmp/'test.c'), '-o', str(tmp/'test')], capture_output=True, text=True)
            assert compile_result.returncode == 0, compile_result.stderr[-3000:]
            p = subprocess.run([str(tmp/'test')], capture_output=True, text=True)
            print(label+': '+p.stdout.strip())
            assert p.returncode == (0 if label == 'production' else 1), p.stderr[-2000:]


if __name__ == '__main__':
    main()
