"""Offline stream framing: 7/0; early-frame control 7/6; ungated control 7/2.
No socket/game calls; no packet payload logged."""
from pathlib import Path
import subprocess
import tempfile
ROOT=Path(__file__).resolve().parent.parent
HARNESS=r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
int diag_on=1;
#include "SOURCE"
int main(void){
 setenv("NFS_LAN","1",1);LanWire w={0};unsigned checks=0,bad=0;
#define C(x) do{++checks;bad+=!(x);}while(0)
 uint8_t frames[28]={'a','u','t','h',0,0,0,0,0,0,0,16,'a','b','c',0,'g','c','r','e',0,0,0,0,0,0,0,12};
 lan_wire_feed(&w,"rx",frames,5);C(!w.frames && w.used==5);
 lan_wire_feed(&w,"rx",frames+5,7);C(!w.frames && w.left==4);
 lan_wire_feed(&w,"rx",frames+12,3);C(!w.frames && w.left==1);
 lan_wire_feed(&w,"rx",frames+15,13);C(w.frames==2 && !strcmp(w.last_command,"gcre") && !w.last_code);
 uint8_t error[12]={'p','e','r','s',0xff,0xff,0xff,0xff,0,0,0,12};
 lan_wire_feed(&w,"rx",error,12);C(w.frames==3 && (int32_t)w.last_code==-1);
 diag_on=0;lan_wire_feed(&w,"rx",frames,28);C(w.frames==3);diag_on=1;
 uint8_t invalid[12]={0};lan_wire_feed(&w,"rx",invalid,12);C(w.opaque && w.frames==3);
 printf("LAN WIRE %s: %u checks, %u failures\n",bad?"FAIL":"PASS",checks,bad);return bad!=0;
}
'''
def main():
 s=(ROOT/'runtime/lan_diag.h').read_text()
 with tempfile.TemporaryDirectory(prefix='lan-wire-') as td:
  p=Path(td)
  for name,text in [('normal',s),('early',s.replace('w->left=size-12;','w->left=0;')),('ungated',s.replace('!lan_diag_on() || w->opaque','w->opaque'))]:
   h=p/(name+'.h');h.write_text(text);c=p/'test.c';c.write_text(HARNESS.replace('SOURCE',str(h)));exe=p/name
   r=subprocess.run(['clang','-O1',str(c),'-o',str(exe)],capture_output=True,text=True);assert r.returncode==0,r.stderr[-1500:]
   r=subprocess.run([str(exe)],capture_output=True,text=True);print(name,r.stdout.strip(),flush=True)
   assert r.returncode==(0 if name=='normal' else 1),(name,r.stdout,r.stderr)
 return 0
if __name__=='__main__':raise SystemExit(main())
