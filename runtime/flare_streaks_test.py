#!/usr/bin/env python3
"""Lamp streaks: flare_streaks.c's stretch of a world flare quad, without a game launch.

Fixture: camera at 120 km/h straight ahead, a 1 m lamp billboard 6 m right, 4 m up, 20 m ahead. Numbers the look
depends on: streak length at the lamp 60 Hz 0.3898 m, 120 Hz 0.3951 m (equal within 3 %: a fixed exposure over the
real frame time, not one frame), and the gap from the lamp centre to the streak's texture core, 0.0000 m (the streak
starts on the lamp's glow). Controls in the same run: previous transform = current (a paused or frozen camera) gives
0 stretch; a camera cut, a skipped frame and night factor 0 leave the quad byte-identical; the hook with the switch
Off or at noon leaves the polygon byte-identical.
Broken runs (exit 1 each): the per-frame delta without the frame-time scale fails the rate check (120 Hz 0.1976 m);
the earlier offset (quad centred mid-streak) fails the gap check (core 0.6934 m from the lamp); the earlier
edge mapping (the billboard's own spun texture axes) samples outside the texture on a 45-degree billboard (UV
-0.1923..1.1923, a hard edge where it clamps) where the fix stays at 0..1 with the long edges on the border.
Default: with NFS_FLARE_STREAKS unset (day-night on, night) the hook changes 2 of 3 frames; the old Off default
(unset reads as off) changes 0 and fails (exit 1); an explicit off still changes 0.
"""
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / 'runtime'
SOURCE = RUNTIME / 'flare_streaks.c'

HARNESS = r'''
#include "host.h"
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
uint8_t *G;
int diag_on=0;
uint64_t frame_serial=10;
static float level=1;
float daynight_lamp_level(void) { return level; }
void diag_event(const char *f,...) { (void)f; }
int diag_shot_due(uint64_t f) { (void)f; return 0; }
void diag_shot_request(uint64_t f) { (void)f; }
uint64_t diag_current_frame(void) { return 0; }
static int failures;
uint32_t call_guest(Cpu *c,uint32_t fn,int n,const uint32_t *args) { (void)fn; (void)n; (void)args; c->ecx=0; c->esp-=99; return 0; }
#include <time.h>
static int fake_clock(int id,struct timespec *ts) { (void)id; ts->tv_sec=0; ts->tv_nsec=(long)(frame_serial%60)*16666667L; return 0; }
#define clock_gettime fake_clock
#include TEST_SOURCE
#define CHECK(x) do { if(!(x)) { ++failures; printf("FAIL line %d: %s\n",__LINE__,#x); } } while(0)
static const float LAMP[3]={6,4,20};
static void camera(StreakCamera *s,float z,double t,uint64_t serial) {
    memset(s,0,sizeof *s); s->m[0]=s->m[5]=s->m[10]=s->m[15]=1; s->m[14]=-z; s->eye[2]=z; s->t=t; s->serial=serial;
}
static float spin;   // the billboard's screen rotation (radians), as the game spins world flares
static void quad(float *q,float *uv,uint32_t *col) {
    static const float sx[4]={-1,1,1,-1}, sy[4]={-1,-1,1,1};
    float cs=cosf(spin), sn=sinf(spin);
    for(int i=0;i<4;++i){ q[i*4]=LAMP[0]+.5f*(sx[i]*cs-sy[i]*sn); q[i*4+1]=LAMP[1]+.5f*(sx[i]*sn+sy[i]*cs); q[i*4+2]=LAMP[2]; q[i*4+3]=1; col[i]=0xFFC08040u;
        uv[i*2]=(sx[i]+1)*.5f; uv[i*2+1]=(sy[i]+1)*.5f; }
}
// World distance from the lamp centre to the streak's texture core (texture coordinates .5,.5), via the quad's
// affine map from corners 0..2.
static float world_gap(const float *q,const float *uv) {
    float a=uv[2]-uv[0], b=uv[4]-uv[0], c=uv[3]-uv[1], d=uv[5]-uv[1], det=a*d-b*c;
    if(fabsf(det)<1e-12f) return -1;
    float du=.5f-uv[0], dv=.5f-uv[1], p=(du*d-dv*b)/det, r=(dv*a-du*c)/det, s=0;
    for(int j=0;j<3;++j){ float x=q[j]+p*(q[4+j]-q[j])+r*(q[8+j]-q[j])-LAMP[j]; s+=x*x; }
    return sqrtf(s);
}
static float last_gap=-1, uv_lo, uv_hi;
static float streak(double hz,float factor,float jump,uint64_t gap,int same) {
    const float v=120/3.6f; double dt=1/hz;
    StreakCamera a,b; camera(&a,same?0:-(float)(v*dt)-jump,0,7); camera(&b,0,dt,7+gap);
    float q[16],q0[16],uv[8],uv0[8]; uint32_t col[4],col0[4]; quad(q,uv,col);
    memcpy(q0,q,sizeof q); memcpy(col0,col,sizeof col); memcpy(uv0,uv,sizeof uv);
    float len=flare_streak_apply(q,uv,col,&a,&b,factor);
    if(len<=0) CHECK(!memcmp(q,q0,sizeof q) && !memcmp(col,col0,sizeof col) && !memcmp(uv,uv0,sizeof uv));
    else { CHECK(col[0]!=col0[0] || col[2]!=col0[2]); last_gap=world_gap(q,uv);
        uv_lo=1e9f; uv_hi=-1e9f; for(int k=0;k<8;++k){ uv_lo=fminf(uv_lo,uv[k]); uv_hi=fmaxf(uv_hi,uv[k]); } }
    return len;
}
// The hook on guest memory: stack, flare frame, view, camera and polygon.
static int hook_changes(const char *on,const char *dn,float lv) {
    if(on) setenv("NFS_FLARE_STREAKS",on,1); else unsetenv("NFS_FLARE_STREAKS"); setenv("NFS_DAYNIGHT",dn,1); enabled=-1; level=lv; memset(views,0,sizeof views);
    const uint32_t esp=0x100000, ebp=0x101000, view=0x00919650u, cam=0x102000, poly=0x103000;
    int changed=0;
    for(int f=0;f<3;++f) {
        ++frame_serial;
        M32(esp)=0x00505E4Eu; M32(esp+4)=poly; M32(ebp+4)=0x00505F3Du; M32(ebp+0x1c)=0; M32(view+0x40)=cam;
        StreakCamera s; camera(&s,f*(120/3.6f)/60,0,0); memcpy(G+cam,s.m,64); memcpy(G+cam+0x40,s.eye,12);
        float q[16],uv[8]; uint32_t col[4]; quad(q,uv,col); memcpy(G+poly,q,64); memcpy(G+poly+0x40,uv,32); memcpy(G+poly+0x80,col,16);
        uint8_t before[0x90]; memcpy(before,G+poly,0x90);
        Cpu c={.esp=esp,.ebp=ebp,.ecx=view};
        flare_streaks_poly(&c);
        changed+=memcmp(before,G+poly,0x90)!=0;
    }
    return changed;
}
int main(void) {
    G=calloc(1,0x1000000);
    float l60=streak(60,1,0,1,0), l120=streak(120,1,0,1,0);
    printf("streak 60Hz=%.4f m 120Hz=%.4f m ratio=%.4f core_gap=%.4f m\n",l60,l120,l60/l120,last_gap);
    CHECK(last_gap>=0 && last_gap<.01f);   // the streak's core sits on the lamp: no gap
    // A spun billboard: the streak still samples only inside the texture, its long edges on the border (fade).
    spin=.7853982f; float ls=streak(60,1,0,1,0); spin=0;
    printf("spun 45 deg: streak=%.4f m uv range %.4f..%.4f gap=%.4f m\n",ls,uv_lo,uv_hi,last_gap);
    CHECK(ls>.05f && uv_lo>=-1e-4f && uv_hi<=1+1e-4f && last_gap<.01f);
    CHECK(l60>.05f && l120>.05f && fabsf(l60/l120-1)<.03f);
    CHECK(streak(60,.5f,0,1,0)<l60*.51f && streak(60,.5f,0,1,0)>l60*.49f);
    CHECK(streak(60,1,0,1,1)==0);      // broken control: previous transform = current
    CHECK(streak(60,1,10,1,0)==0);     // camera cut: 10 m jump in one frame
    CHECK(streak(60,1,0,2,0)==0);      // a frame without the sample
    CHECK(streak(60,0,0,1,0)==0);      // day: night factor 0
    CHECK(hook_changes("off","on",1)==0);
    CHECK(hook_changes("on","off",1)==0);   // day-night off is daytime
    CHECK(hook_changes("on","on",0)==0);    // noon
    CHECK(hook_changes("on","on",1)==2);    // first frame has no previous sample
    CHECK(hook_changes(NULL,"on",1)==2);    // switch unset: On by default
    printf("checks failures=%d\n",failures);
    return failures!=0;
}
'''


def run():
    with tempfile.TemporaryDirectory(prefix='flarestreaks-') as tmp:
        temp = Path(tmp)
        (temp / 'harness.c').write_text(HARNESS)
        text = SOURCE.read_text()
        rate = text.replace('EXPOSURE / (float)dt', 'EXPOSURE * 60')
        offset = text.replace('float sa[4] = {0, 0, len, len}, sb[4] = {-w, w, w, -w};',
                              'float sa[4] = {-h, -h, len + h, len + h}, sb[4] = {-w, w, w, -w};')
        clipped = text.replace('    w = .5f / g;\n', '').replace(
            'uv[i * 2] = tex[0][0] + (sa[i] > 0 ? .5f : 0); uv[i * 2 + 1] = tex[1][0] + (sb[i] > 0 ? .5f : -.5f);',
            'for (int k = 0; k < 2; ++k) uv[i * 2 + k] = tex[k][0] + tex[k][1] * (sa[i] > 0 ? h : 0) + tex[k][2] * sb[i];')
        olddef = text.replace('return !e || !(!strcmp(e, "0") || !strcasecmp(e, "off"));', 'return e && (!strcmp(e, "1") || !strcasecmp(e, "on"));')
        assert olddef != text
        assert rate != text and offset != text and clipped.count('tex[k][2] * sb[i]') == 1 and 'w = .5f / g' not in clipped
        variants = {'normal': SOURCE}
        for label, code in (('broken-rate', rate), ('broken-offset', offset), ('broken-edges', clipped), ('broken-default', olddef)):
            (temp / (label + '.c')).write_text(code)
            variants[label] = temp / (label + '.c')
        cmd = ['clang', '-w', '-std=c11', '-I' + str(RUNTIME), '-I' + str(ROOT / 'recomp'), str(temp / 'harness.c'), '-lm']
        results = {}
        for label, source in variants.items():
            binary = temp / label
            subprocess.run(cmd + ['-DTEST_SOURCE="' + str(source) + '"', '-o', str(binary)], check=True)
            results[label] = subprocess.run([str(binary)], capture_output=True, text=True)
            print(label, ' | '.join(results[label].stdout.strip().splitlines()))
        if results['normal'].returncode:
            raise AssertionError(results['normal'].stdout)
        assert results['broken-rate'].returncode, 'one-frame tail passed the rate check'
        assert results['broken-offset'].returncode, 'streak core off the lamp passed the gap check'
        assert results['broken-edges'].returncode, 'streak edges outside the texture passed the edge check'
        assert results['broken-default'].returncode, 'the old Off default passed the default-on check'
    print('PASS flare_streaks: rate-independent length, 0 stretch on previous = current, Off/noon unchanged; broken control fails')
    return 0


if __name__ == '__main__':
    sys.exit(run())
