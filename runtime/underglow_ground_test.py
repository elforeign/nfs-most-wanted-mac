"""Pool depth admission on cambered, triangulated ground; horizontal control.
The reproduced road has pool z=152.299623 m versus ground samples
152.207046..152.344230 m. The original rectangle passes 145881/321520
captured depth pixels (45.372294%, tolerance 1e-7). Replaying the clipped
receivers against the identical captured depth increases that to 240928/321520
(74.934063%); body occlusion remains. This fixture executes production clipping, cached
triangle traversal, vertex placement and UV packing, with a deliberately broken
horizontal receiver. Its one visual number is the ground depth-pass share;
normal depth must also keep the foreground wall/body dark. No game/emulation.
Fixture: current 40401/40401 (100%); horizontal control 15959/40401 (39.5014975%),
one failure. Foreground 3481 samples, zero leaks in both cases.
"""
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
from underglow_light_test import function_fragment

DRIVER = r'''
#include "underglow_light.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
uint8_t *G;int diag_on;
static uint32_t scratch=0x10000;
static UGVec3 emitted[64][4];static unsigned count,failures;
static UGVec3 rectangle[4]={{-2.71865,-2.95953,.002},{2.5417,-2.95953,.002},
                          {2.5417,2.72307,.002},{-2.71865,2.72307,.002}};
#define CHECK(x) do{if(!(x)){++failures;fprintf(stderr,"line %u: %s\n",__LINE__,#x);}}while(0)
uint64_t host_ticks(void){return 0;}
unsigned underglow_pulse(void){return 0;}
FILE *dump_file(void){return NULL;}
uint32_t call_guest(Cpu *c,uint32_t fn,int n,const uint32_t *a){
 if(fn==0x006c0a30){memset(G+c->ecx,0,0x94);return 0;}
 CHECK(fn==0x006be750 && n==5 && count<64);
 if(count>=64)return 0;
 for(unsigned i=0;i<4;++i){
  UGVec3 p={MF32(a[0]+16*i),MF32(a[0]+16*i+4),MF32(a[0]+16*i+8)};
  emitted[count][i]=p;
  CHECK(fabs(MF32(a[0]+0x40+8*i)-(p.x-rectangle[0].x)/(rectangle[2].x-rectangle[0].x))<1e-6);
  CHECK(fabs(MF32(a[0]+0x44+8*i)-(p.y-rectangle[0].y)/(rectangle[2].y-rectangle[0].y))<1e-6);
  CHECK(M32(a[0]+0x80+4*i)==0xffffffffu);
 }
 ++count;return 0;
}
PRODUCTION
static double road(double x,double y){
 /* Two continuous faces with different slopes meet on x+y=0. */
 return -.008659*x+.004479*y+.006*fmax(0,x+y);
}
static void triangle(unsigned i,UGVec3 a,UGVec3 b,UGVec3 c){
 UGVec3 p[3]={a,b,c};uint32_t at=0x3300+48*i;
 for(unsigned v=0;v<3;++v){MF32(at+16*v)=-p[v].y;MF32(at+16*v+4)=p[v].z;MF32(at+16*v+8)=p[v].x;}
}
static int height(double x,double y,double *z){
 int found=0;*z=-INFINITY;
 for(unsigned n=0;n<count;++n)for(unsigned t=0;t<2;++t){
  UGVec3 a=emitted[n][0],b=emitted[n][t+1],c=emitted[n][t+2];
  double d=(b.y-c.y)*(a.x-c.x)+(c.x-b.x)*(a.y-c.y);if(fabs(d)<1e-10)continue;
  double u=((b.y-c.y)*(x-c.x)+(c.x-b.x)*(y-c.y))/d;
  double v=((c.y-a.y)*(x-c.x)+(a.x-c.x)*(y-c.y))/d;
  if(u<-1e-7||v<-1e-7||u+v>1+1e-7)continue;
  *z=fmax(*z,u*a.z+v*b.z+(1-u-v)*c.z);found=1;
 }
 return found;
}
int main(void){
 G=calloc(1,0x20000);CHECK(G!=NULL);Cpu c={0};UGCarCapture s={0};
 s.self=0x1000;s.position[2]=.105194;
 M32(s.self+0x44)=0x3000;M32(0x3060)=0x3100;M32(0x3064)=0x3104;
 M32(0x3100)=0x3200;M32(0x3204)=0x3300;M32(0x3208)=0x3360;
 UGVec3 a={-10,-10,0},b={10,-10,0},d={-10,10,0},e={10,10,0};
 a.z=road(a.x,a.y);b.z=road(b.x,b.y);d.z=road(d.x,d.y);e.z=road(e.x,e.y);
 triangle(0,a,b,d);triangle(1,b,e,d);
 CHECK(ground_pool(&c,&s,rectangle,0)==count && count>0);
 unsigned pixels=0,pass=0,covered=0,blocked=0,leaks=0;
 for(unsigned y=0;y<201;++y)for(unsigned x=0;x<201;++x){
  double wx=rectangle[0].x+(rectangle[2].x-rectangle[0].x)*(x+.5)/201;
  double wy=rectangle[0].y+(rectangle[2].y-rectangle[0].y)*(y+.5)/201,z;
  ++pixels;if(!height(wx,wy,&z))continue;++covered;
  if(z>=road(wx,wy)-1e-6)++pass;
  if(x>70&&x<130&&y>70&&y<130){++blocked;if(z>=.8)++leaks;}
 }
 printf("ground depth-pass=%u/%u (%.9f), covered=%u, foreground=%u leaks=%u\n",pass,pixels,(double)pass/pixels,covered,blocked,leaks);
 CHECK(covered==pixels);CHECK(pass==pixels);CHECK(blocked>0 && leaks==0);
 count=0;M32(s.self+0x44)=0;CHECK(ground_pool(&c,&s,rectangle,0)==0 && !count);
 M32(s.self+0x44)=0x3000;M32(0x3064)=0x3105;CHECK(ground_pool(&c,&s,rectangle,0)==0 && !count);
 M32(0x3064)=0x3104;M32(0x3208)=0x3330;
 triangle(0,(UGVec3){0,-10,-1},(UGVec3){0,10,-1},(UGVec3){0,0,1});
 CHECK(ground_pool(&c,&s,rectangle,0)==0 && !count);
 a.z=b.z=d.z=1;triangle(0,a,b,d);CHECK(ground_pool(&c,&s,rectangle,0)==0 && !count);
 a.z=b.z=d.z=-2;triangle(0,a,b,d);CHECK(ground_pool(&c,&s,rectangle,0)==0 && !count);
 printf("ground failures=%u\n",failures);free(G);return !!failures;
}
'''


def main():
    source = (ROOT / 'runtime/underglow_light.m').read_text()
    functions = '\n'.join(function_fragment(source, signature) for signature in (
        'static double pulse_factor(', 'static void quad(',
        'static unsigned pool_clip(', 'static unsigned ground_pool('))
    lift = 'for(unsigned i=0;i<n;++i)polygon[i].z+=.002;'
    assert lift in functions
    broken = functions.replace(lift, 'for(unsigned i=0;i<n;++i)polygon[i].z=rectangle[0].z;')
    with tempfile.TemporaryDirectory(prefix='ug-ground-') as directory:
        tmp = Path(directory)
        for name, body in [('current', functions), ('horizontal-control', broken)]:
            unit, exe = tmp / (name + '.c'), tmp / name
            unit.write_text(DRIVER.replace('PRODUCTION', body))
            cp = subprocess.run(['clang', '-O1', '-g', '-fsanitize=address,undefined',
                                 '-I'+str(ROOT/'runtime'), '-I'+str(ROOT/'recomp'),
                                 str(unit), '-o', str(exe)], capture_output=True, text=True)
            assert cp.returncode == 0, cp.stderr[-3000:]
            cp = subprocess.run([str(exe)], capture_output=True, text=True)
            print(name + ': ' + cp.stdout.strip(), flush=True)
            assert cp.returncode == (name != 'current'), cp.stderr[-3000:]
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
