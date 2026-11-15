"""Clock and horizon acceptance fixtures (ASan/UBSan).
Base: race +1.000000000 h/150 s; direction flip 2.000000000,
colour step .755000000. Production: 1,116,283 numeric checks; race spread 0.
At 150 s/h the full-day sweep bounds T/phase/n/wlight by .000111111111 /
8.40425491e-6 / 8.34465027e-5 / .000127747961 per frame. At 5 s/h:
.00333333333 / .000250041485 / .00249999762 / .00382926507.
T uses circular distance over 24 h. Phase uses raw distance, bounds 8.75e-6 /
.0002625, except when the hour decreases on two advancing frames: exactly
.949999988 > .050000001, n=w=sky factor=1 on both sides, once per day.
The real sky moon step at wrap is <= 83666*(pi/12)*step*1.05.
No-freeze: race drift 4 h/600 s; no weight: step 2.000000071 vs .000183259571;
no wrap: 37 instead of 13; ignored career: 10 instead of 15;
replacement identity: 15.0000003179 instead of 9; pinned base drive:
race drift 1 h/150 s. Every broken variant exits nonzero. Hidden fixed/15.75
preferences are saved and reloaded by the real registry in separate processes.
Noon-wrap fails the phase rule (.949999988 > .500008285, n 1 > 0);
dusk-reset fails its raw bound (.899991632 > 8.75e-6 at 18:00).
"""
from pathlib import Path
import hashlib
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / 'runtime'
BASE_DRIVE = r'''static void drive(uint32_t tod) {
    float phase = read_float(tod + 8);
    if (!clock_state.ready || clock_state.object != tod || phase != clock_state.phase) {
        // Observe the existing race setter and native slider without hooking either.
        clock_state.hour = 6 + 12 * (fmaxf(0.05f, fminf(0.95f, phase)) - 0.05f) / 0.9;
        clock_state.object = tod;
        clock_state.ready = 1;
    }
    float dt = read_float(REAL_DT);
    if (fixed) clock_state.hour = fixed_hour;
    else if (isfinite(dt) && dt > 0)
        clock_state.hour = daynight_wrap(clock_state.hour + dt / SECONDS_PER_GAME_HOUR);
    DaynightSample s;
    sample_object(tod, &s);
    clock_state.phase = s.phase;
    write_float(tod + 8, s.phase);
    M32(tod + 4) = 1; // Integer direction, never the retail endpoint reversal.
    memcpy(G + tod + 0x180, s.light, sizeof(s.light));
    write_float(tod + 0x18c, 0);
    for (int i = 0; i < 3; ++i) write_float(tod + 0x170 + 4 * i, s.light[i] * 100000);
    write_float(tod + 0x17c, 1);
}
'''
BASE_SHA256 = "976a9737759e6a363f6e3a7af55a7261734fdf4d11d7e31fc3e49f2ad1aa9737"

BASE_HARNESS = r'''#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include DAYNIGHT_SOURCE
uint8_t *G;
int diag_on;
uint64_t diag_current_frame(void) { return 0; }
uint64_t host_ticks(void) { return 0; }
void diag_event(const char *fmt, ...) { (void)fmt; }
void f_00769360_orig(Cpu *c) { c->esp += 4; }
void f_00769580_orig(Cpu *c) { (void)c; }
void f_00502130_orig(Cpu *c) { c->esp += 4; }
int postfx_look_variant(const char *v) { (void)v; return 0; }
int main(void) {
    G=calloc(1,0xa00000); assert(G);
    enabled=1; fixed=0;
    uint32_t tod=0x5000, race=0x6000;
    M32(FLOW)=6; M32(0x0091E000)=race; M32(race+0x1968)=0x7000;
    write_float(tod+8,.5f); write_float(tod+0x10,.5f);
    write_float(REAL_DT,0); Cpu c={.ecx=tod}; f_00769360(&c);
    double start=clock_state.hour;
    write_float(REAL_DT,150); c.ecx=tod; f_00769360(&c);
    double drift=clock_state.hour-start;
    DaynightSample a,b; daynight_sample(18,0,.5f,&a);
    daynight_sample(18+1.0/60/150,0,.5f,&b);
    double flip=0,colour=0;
    for(int i=0;i<3;i++) {
        double d=fabs(a.light[i]-b.light[i]); if(d>flip)flip=d;
        d=fabs(a.light[i]-b.light[i]) * (i==0?.3775:i==1?.2575:.0525);
        if(d>colour)colour=d;
    }
    printf("BASE race_150s=%.9f h, direction_step=%.9f, colour_step=%.9f\n",drift,flip,colour);
    free(G);
    return fabs(drift-1)>1e-6 || fabs(flip-2)>1e-5 || fabs(colour-.755)>1e-5;
}
'''


def baseline(source):
    assert hashlib.sha256(BASE_DRIVE.encode()).hexdigest() == BASE_SHA256
    with tempfile.TemporaryDirectory(prefix='dnclock-base-') as d:
        d = Path(d)
        candidate = d / 'base.c'
        candidate.write_text(source)
        harness = d / 'check.c'
        harness.write_text(BASE_HARNESS)
        exe = d / 'check'
        subprocess.run(['clang', '-std=c11', '-D_DARWIN_C_SOURCE', '-O1', '-g',
                        '-fsanitize=address,undefined', '-I', str(RUNTIME),
                        '-I', str(ROOT / 'recomp'), f'-DDAYNIGHT_SOURCE="{candidate}"',
                        str(harness), '-o', str(exe)], check=True)
        r = subprocess.run([str(exe)], text=True, capture_output=True,
                           env=dict(os.environ, NFS_DAYNIGHT='off'))
        assert r.returncode == 0, r.stdout + r.stderr
        print(r.stdout.strip())


HARNESS = r'''
#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include "daynight_clock.h"
#include "daynight_sky.h"
#include "settings.h"
#include DAYNIGHT_SOURCE
uint8_t *G;
int diag_on;
const char *game_dir;
static int checks, advances, blends, ticks, diagnostic_lines, wrap_lines;
static char last_wrap[512], last_diagnostic[1024];
static uint64_t now;
static double largest[4];
static DaynightRecord authored;
static const char *pref_path;
static char pref_mode[32], pref_hour[32];
uint64_t diag_current_frame(void) { return 0; }
uint64_t host_ticks(void) { ++ticks; return now; }
void diag_event(const char *fmt, ...) {
    if (!strncmp(fmt,"daynight_clock:",15)) {
        ++diagnostic_lines;
        va_list ap; va_start(ap,fmt);
        vsnprintf(last_diagnostic,sizeof last_diagnostic,fmt,ap); va_end(ap);
        if (!strncmp(fmt,"daynight_clock: wrap ",20)) {
            ++wrap_lines;
            snprintf(last_wrap,sizeof last_wrap,"%s",last_diagnostic);
        }
    }
}
void report_note(const char *fmt, ...) { (void)fmt; }
void app_pref_batch_begin(void) {}
void app_pref_batch_end(void) {}
const char *app_pref(const char *key) {
    return !strcmp(key,"gameplay.daynight_mode") ? pref_mode :
           !strcmp(key,"gameplay.daynight_hour") ? pref_hour : NULL;
}
void app_pref_set(const char *key, const char *value) {
    char *p=!strcmp(key,"gameplay.daynight_mode")?pref_mode:pref_hour;
    snprintf(p,32,"%s",value);
    if (pref_path) {
        FILE *f=fopen(pref_path,"w"); assert(f);
        fprintf(f,"%s\n%s\n",pref_mode,pref_hour); fclose(f);
    }
}
void f_00769360_orig(Cpu *c) { ++advances; c->esp+=4; }
void f_00769580_orig(Cpu *c) { ++blends; record_io(c->ecx,&authored,1); }
void f_00502130_orig(Cpu *c) { c->esp += 4; }
int postfx_look_variant(const char *v) { (void)v; return 0; }
void flare_streaks_poly(Cpu *c) { (void)c; }
void f_006BE750_orig(Cpu *c) { (void)c; }
void f_00503830_orig(Cpu *c) { c->esp += 4; }
void f_006D04E0_orig(Cpu *c) { c->esp += 8; }
static void equal(double got, double want, double tolerance, const char *what) {
    ++checks;
    if (!(fabs(got-want)<=tolerance)) {
        fprintf(stderr,"FAIL %s got=%.12g wanted=%.12g error=%.12g\n",what,got,want,fabs(got-want));
        exit(1);
    }
}
static double frame(DaynightClock *clock, DaynightClockInput *input,
                    DaynightMode mode, double rate, double start, DaynightClockOutput *out) {
    daynight_clock_step(clock,input,mode,4.25,rate,start,out);
    if(input->active) {
        DaynightSample s; daynight_sample(out->hour,input->azimuth,input->tilt,&s);
        input->phase=s.phase;
    }
    return out->hour;
}
static void policies(void) {
    DaynightClock clock={0};
    DaynightClockInput in={.active=1,.phase=.5f,.tilt=.5f};
    DaynightClockOutput out={0};
    equal(frame(&clock,&in,DAYNIGHT_CYCLE,150,NAN,&out),12,1e-6,"seed");
    in.dt=150;
    equal(frame(&clock,&in,DAYNIGHT_CYCLE,150,3,&out),13,1e-6,"150 s roam = 1 h; seed once");
    for(int i=0;i<24;i++) frame(&clock,&in,DAYNIGHT_CYCLE,150,NAN,&out);
    equal(out.hour,13,1e-6,"3600 s = full day");
    in.race=1; in.phase=.275f; in.dt=0;
    double first=frame(&clock,&in,DAYNIGHT_CYCLE,150,NAN,&out);
    in.dt=1.0/60;
    for(int i=0;i<600*60;i++) frame(&clock,&in,DAYNIGHT_CYCLE,150,NAN,&out);
    equal(out.hour,first,0,"600 s race hold");
    in.active=0; frame(&clock,&in,DAYNIGHT_CYCLE,150,NAN,&out);
    in.active=1; in.race=0; in.dt=0; in.phase=.8f;
    equal(frame(&clock,&in,DAYNIGHT_CYCLE,150,3,&out),13,1e-6,"hour survives flow and game writes");
    in.paused=1; in.dt=150;
    equal(frame(&clock,&in,DAYNIGHT_CYCLE,150,NAN,&out),13,1e-6,"paused hold");
    in.phase=.275f;
    equal(frame(&clock,&in,DAYNIGHT_CYCLE,150,NAN,&out),9,1e-6,"paused slider");
    in.paused=0; in.phase=.725f; in.dt=0;
    equal(frame(&clock,&in,DAYNIGHT_CYCLE,150,NAN,&out),9,1e-6,"unpaused setter ignored");
    for(int m=0;m<3;m++) for(int state=0;state<3;state++) {
        clock=(DaynightClock){0};
        in=(DaynightClockInput){.active=1,.phase=.725f,.tilt=.5f,
                               .race=state!=0,.career=state==2?2:0};
        frame(&clock,&in,m,150,10,&out);
        in.dt=150;
        double want=m==DAYNIGHT_FIXED ? 4.25 : state==0 ? 11 :
                    m==DAYNIGHT_GAME && state==2 ? 15 : 10;
        equal(frame(&clock,&in,m,150,NAN,&out),want,1e-6,"mode x roam/quick/career");
        if(state) {
            // All later authored writes are ignored, including in career events.
            in.phase=.275f;
            equal(frame(&clock,&in,m,150,NAN,&out),want,1e-6,"event authored time held");
            in.race=0; in.dt=0;
            equal(frame(&clock,&in,m,150,NAN,&out),m==DAYNIGHT_FIXED?4.25:10,
                  1e-6,"roam hour restored after event");
        }
    }
    // Consecutive events can replace the parameters without an inactive tick.
    clock=(DaynightClock){0};
    in=(DaynightClockInput){.active=1,.race=1,.race_id=41,.career=2,.phase=.725f,.tilt=.5f};
    equal(frame(&clock,&in,DAYNIGHT_GAME,150,10,&out),15,1e-6,"first career event");
    in.race_id=42; in.phase=.275f;
    equal(frame(&clock,&in,DAYNIGHT_GAME,150,NAN,&out),9,1e-6,"replacement career event");
    in.paused=1; in.phase=.725f; in.dt=150;
    equal(frame(&clock,&in,DAYNIGHT_GAME,150,NAN,&out),15,1e-6,"race paused slider");
    in.paused=0; in.phase=.275f;
    equal(frame(&clock,&in,DAYNIGHT_GAME,150,NAN,&out),15,1e-6,"race slider value held");
    clock=(DaynightClock){0};
    in=(DaynightClockInput){.active=1,.phase=.5f,.tilt=.5f};
    frame(&clock,&in,DAYNIGHT_CYCLE,150,23.999,&out);
    in.dt=.3;
    equal(frame(&clock,&in,DAYNIGHT_CYCLE,150,NAN,&out),.001,1e-10,"midnight wrap");
    double bad[]={NAN,INFINITY,-1,0};
    for(unsigned i=0;i<sizeof bad/sizeof *bad;i++) {
        in.dt=bad[i]; equal(frame(&clock,&in,DAYNIGHT_CYCLE,150,NAN,&out),.001,1e-10,"invalid dt holds");
    }
    in.override=1; in.override_hour=0; in.dt=150; in.paused=1; in.phase=.725f;
    equal(frame(&clock,&in,DAYNIGHT_FIXED,150,NAN,&out),0,0,"test override beats fixed and slider");
    printf("PASS policies: 150 s roam +1 h, 3600 s wrap, 600 s race spread=0, 9 mode/state pairs\n");
}
static void sweep(double rate, double start) {
    memset(largest,0,sizeof largest);
    DaynightClock clock={0};
    DaynightClockInput in={.active=1,.phase=.05f,.tilt=.5f,.azimuth=3.28f};
    DaynightClockOutput out={0};
    frame(&clock,&in,DAYNIGHT_CYCLE,rate,start,&out);
    DaynightSample previous; daynight_sample(out.hour,in.azimuth,in.tilt,&previous);
    double previous_hour=out.hour; float previous_weight=out.weight;
    int wraps=0, previous_advancing=0;
    double moon_max=0;
    const double step=1.0/60/rate;
    // Linear phase mapping .9/12; smoothstep derivative max 1.5 over 2 h;
    // unit orbit speed pi/12. Product w*L has speed <= |L'| + |z'|/height.
    const double slopes[]={1,.9/12,1.5/2,(3.141592653589793/12)*(1+1/DAYNIGHT_SUN_HEIGHT)};
    int count=(int)llround(24/step)+1;
    in.dt=1.0/60;
    for(int i=1;i<=count;i++) {
        frame(&clock,&in,DAYNIGHT_CYCLE,rate,NAN,&out);
        DaynightSample s; daynight_sample(out.hour,in.azimuth,in.tilt,&s);
        double delta=fabs(out.hour-previous_hour), norm=0;
        for(int j=0;j<3;j++) {
            double d=out.weight*s.light[j]-previous_weight*previous.light[j]; norm+=d*d;
        }
        double phase_delta=fabs(s.phase-previous.phase);
        int advancing=in.active && !in.race && !in.paused && !in.override && in.dt>0;
        int wrap=advancing && previous_advancing && out.hour<previous_hour;
        ++checks;
        if(wrap ? (previous.phase!=.95f || s.phase!=.05f || previous.night!=1 || s.night!=1 ||
                   previous_weight!=1 || out.weight!=1) : phase_delta>slopes[1]*step*1.05) {
            fprintf(stderr,"FAIL phase rule rate=%.0f hour=%.12g wrap=%d phase=%.9f>%.9f "
                    "n=%.9f>%.9f w=%.9f>%.9f step=%.9g bound=%.9g\n",
                    rate,out.hour,wrap,previous.phase,s.phase,previous.night,s.night,
                    previous_weight,out.weight,phase_delta,slopes[1]*step*1.05); exit(1);
        }
        if(wrap) {
            ++wraps;
            equal(daynight_sky_factor(previous.phase,previous.light[2],in.tilt),1,0,"wrap sky before");
            equal(daynight_sky_factor(s.phase,s.light[2],in.tilt),1,0,"wrap sky after");
            float a[3],b[3]; double moon_step=0;
            daynight_sky_moon(previous.phase,previous.light,in.azimuth,a);
            daynight_sky_moon(s.phase,s.light,in.azimuth,b);
            for(int j=0;j<3;j++) moon_step+=(double)(b[j]-a[j])*(b[j]-a[j]);
            moon_step=sqrt(moon_step); moon_max=fmax(moon_max,moon_step);
            ++checks;
            if(!(moon_step<=83666*(3.141592653589793/12)*step*1.05)) {
                fprintf(stderr,"FAIL wrap moon step=%.9g bound=%.9g\n",moon_step,
                        83666*(3.141592653589793/12)*step*1.05); exit(1);
            }
        }
        if(!(out.hour>=0 && out.hour<24)) {
            fprintf(stderr,"FAIL wrapped range hour=%.12g frame=%d\n",out.hour,i); exit(1);
        }
        double changes[]={fmin(delta,24-delta),wrap ? 0 : phase_delta,
                          fabs(s.night-previous.night),sqrt(norm)};
        for(int j=0;j<4;j++) {
            if(changes[j]>largest[j]) largest[j]=changes[j];
            ++checks;
            if(!(changes[j]<=slopes[j]*step*1.05)) {
                fprintf(stderr,"FAIL sweep field=%d rate=%.1f hour=%.12g step=%.12g bound=%.12g\n",
                        j,rate,out.hour,changes[j],slopes[j]*step*1.05); exit(1);
            }
        }
        previous=s; previous_weight=out.weight; previous_hour=out.hour;
        previous_advancing=advancing;
    }
    equal(out.hour,start+step,1e-8,"sweep crosses midnight");
    equal(wraps,1,0,"one midnight wrap per day");
    printf("PASS sweep rate=%.0f: %d frames wraps=%d; step T/phase/n/wlight=%.9g/%.9g/%.9g/%.9g "
           "wrap moon=%.9g bound=%.9g\n",rate,count,wraps,largest[0],largest[1],largest[2],largest[3],
           moon_max,83666*(3.141592653589793/12)*step*1.05);
}
static Cpu cpu(uint32_t tod) { return (Cpu){.ecx=tod,.esp=0x4000,.ebx=11,.esi=22,.edi=33,.ebp=44}; }
static void tick(uint32_t tod) {
    Cpu c=cpu(tod); now+=16666667; f_00769360(&c);
    assert(c.esp==0x4004 && c.ebx==11 && c.esi==22 && c.edi==33 && c.ebp==44);
}
static void memory(void) {
    G=calloc(1,0xa00000); assert(G);
    M32(FLOW)=6; M32(0x0091E000)=0x6000; M32(0x009885E0)=0x7000;
    write_float(0x5008,.5f); write_float(0x5010,.5f); write_float(0x500c,3.28f);
    authored=(DaynightRecord){.diffuse={.6f,.48f,.22f},.specular={.58f,.53f,.42f},
               .ambient={.19f,.24f,.32f,4},.sky={.27f,.46f,.96f},.environment=1};
}
static void hooks(void) {
    memory(); settings_init();
    uint32_t tod=0x5000;
    enabled=0; Cpu c=cpu(tod); f_00769360(&c); assert(advances==1);
    c=cpu(tod); f_00769580(&c); assert(blends==1);
    enabled=1; M32(0x6000+0x1968)=1;
    tick(tod); double start=clock_state.hour;
    write_float(REAL_DT,150); tick(tod);
    equal(clock_state.hour,start,0,"actual-hook race 150 s hold");
    M32(0x6000+0x1968)=0; write_float(tod+8,.275f); tick(tod);
    equal(clock_state.hour,start+1,1e-6,"actual-hook roam rate; game write ignored");
    M32(0x7000+0x2c)=4; tick(tod);
    equal(clock_state.hour,start+1,1e-6,"actual-hook pause hold");
    write_float(tod+8,.275f); tick(tod);
    equal(clock_state.hour,9,1e-6,"actual-hook paused slider");
    M32(0x7000+0x2c)=0; write_float(REAL_DT,0);
    M32(FLOW)=3; tick(tod); M32(FLOW)=6; write_float(0x8008,.725f); tick(0x8000);
    equal(clock_state.hour,9,1e-6,"process lifetime across flow and object replacement");
    // A career event uses its authored time only in Game mode, then restores roam.
    M32(0x6000+0x1968)=1; M32(0x6000+0x1964)=2; write_float(tod+8,.725f);
    setenv("NFS_DAYNIGHT_MODE","game",1); tick(tod);
    equal(clock_state.hour,15,1e-6,"actual-hook career event");
    write_float(tod+8,.275f); tick(tod);
    equal(clock_state.hour,15,1e-6,"actual-hook authored hold");
    M32(0x6000+0x1968)=0; tick(tod);
    equal(clock_state.hour,9,1e-6,"actual-hook return to roam");
    assert(ticks==0 && diagnostic_lines==0); // disabled diagnostics do no clock sampling
    fixed=1;
    for(int h=0;h<=24;h+=6) {
        fixed_hour=h; tick(tod);
        DaynightSample s; daynight_sample(h,3.28f,.5f,&s);
        double w=fmin(1,fabs(s.sun[2])/DAYNIGHT_SUN_HEIGHT);
        for(int i=0;i<3;i++) equal(read_float(tod+0x180+4*i),s.light[i],1e-7,"raw direction unchanged");
        DaynightRecord night,expected,got; daynight_night_record(&night); expected=authored;
        daynight_mix(&expected,&night,s.night);
        for(int i=0;i<3;i++) { expected.diffuse[i]*=w; expected.specular[i]*=w; }
        for(int view=0;view<3;view++) {
            c=cpu(tod); f_00769580(&c); record_io(tod,&got,0);
            for(int i=0;i<3;i++) {
                equal(got.diffuse[i],expected.diffuse[i],1e-7,"weighted sun after mix");
                equal(got.specular[i],expected.specular[i],1e-7,"weighted specular after mix");
                equal(got.sky[i],expected.sky[i],1e-7,"sky mix preserved");
            }
            for(int i=0;i<4;i++) equal(got.ambient[i],expected.ambient[i],1e-7,"ambient preserved");
        }
    }
    setenv("NFS_DAYNIGHT_MODE","fixed",1); setenv("NFS_DAYNIGHT_HOUR","0",1);
    fixed=0; tick(tod); equal(clock_state.hour,0,0,"fixed mode midnight");
    fixed=1; fixed_hour=12; tick(tod); equal(clock_state.hour,12,0,"test hour priority");
    diag_on=1; fixed=0; tick(tod); assert(ticks==1 && diagnostic_lines==1);
    for(int i=0;i<30;i++)tick(tod);
    assert(diagnostic_lines==1);
    for(int i=0;i<31;i++)tick(tod);
    assert(diagnostic_lines==2);
    setenv("NFS_DAYNIGHT_MODE","cycle",1);
    M32(0x7000+0x2c)=4; write_float(tod+8,.95f); tick(tod);
    M32(0x7000+0x2c)=0; write_float(REAL_DT,899); tick(tod);
    write_float(REAL_DT,2); tick(tod);
    assert(wrap_lines==1);
    assert(strstr(last_wrap,"phase=0.949999988>0.050000001 n=1.000000000>1.000000000 "
                            "w=1.000000000>1.000000000 dlight="));
    // Decreases due to fixed/override/pause/race, including their first released
    // frame, must never be classified as midnight wraps.
    const char *holds[]={"fixed","override","pause","race"};
    for(int i=0;i<4;i++) {
        fixed=0; M32(0x6000+0x1968)=0; M32(0x7000+0x2c)=0;
        setenv("NFS_DAYNIGHT_MODE","cycle",1);
        write_float(REAL_DT,1); tick(tod);
        if(i==0) { setenv("NFS_DAYNIGHT_MODE","fixed",1); setenv("NFS_DAYNIGHT_HOUR","0",1); }
        if(i==1) { fixed=1; fixed_hour=0; }
        if(i==2) { M32(0x7000+0x2c)=4; write_float(tod+8,.95f); }
        if(i==3) M32(0x6000+0x1968)=1;
        tick(tod);
        if(i==0) setenv("NFS_DAYNIGHT_HOUR","23",1);
        if(i==1) fixed_hour=23;
        if(i==2) write_float(tod+8,.05f);
        tick(tod);
        fixed=0; M32(0x6000+0x1968)=0; M32(0x7000+0x2c)=0;
        setenv("NFS_DAYNIGHT_MODE","cycle",1); tick(tod);
        equal(wrap_lines,1,0,holds[i]);
    }
    now+=1000000000; tick(tod); assert(strstr(last_diagnostic,"wraps=1 "));
    free(G);
    printf("PASS hooks: race spread=0, native state flags/slider/flow, weighted fresh views, diagnostics gated at 1 Hz\n");
}
static void persistence(const char *action,const char *path) {
    pref_path=path;
    FILE *f=fopen(path,"r");
    if(f) {
        assert(fgets(pref_mode,sizeof pref_mode,f)); assert(fgets(pref_hour,sizeof pref_hour,f)); fclose(f);
        pref_mode[strcspn(pref_mode,"\r\n")]=0; pref_hour[strcspn(pref_hour,"\r\n")]=0;
    }
    assert(!getenv("NFS_DAYNIGHT_MODE") && !getenv("NFS_DAYNIGHT_HOUR"));
    memory(); settings_init();
    Setting *mode=settings_find("gameplay.daynight_mode"), *hour=settings_find("gameplay.daynight_hour");
    Setting *rows[4]; assert(mode && hour && mode->hidden && hour->hidden);
    assert(settings_rows(SET_GAMEPLAY,rows,4)==0);
    if(!strcmp(action,"save")) {
        assert(settings_get(mode->id)==DAYNIGHT_GAME);
        assert(settings_set(mode,DAYNIGHT_FIXED)); assert(settings_set(hour,1575));
    } else {
        assert(settings_get(mode->id)==DAYNIGHT_FIXED && settings_get(hour->id)==1575);
        assert(!strcmp(getenv("NFS_DAYNIGHT_MODE"),"fixed"));
        equal(strtod(getenv("NFS_DAYNIGHT_HOUR"),NULL),15.75,1e-6,"reloaded hour switch");
    }
    enabled=1; tick(0x5000); equal(clock_state.hour,15.75,1e-6,"saved mode read after settings_init");
    free(G); printf("PASS %s hidden preferences in fresh process: fixed 15.75\n",action);
}
int main(int argc,char **argv) {
    if(argc==2) { sweep(150,!strcmp(argv[1],"dusk") ? 12 : 0); return 0; }
    if(argc==3) { persistence(argv[1],argv[2]); return 0; }
    policies(); sweep(150,0); sweep(5,0); hooks();
    printf("PASS clock %d checks\n",checks);
}
'''


def main():
    source = (RUNTIME / 'daynight.c').read_text()
    a = source.index('static void drive(')
    b = source.index('\nvoid f_00769360_orig', a)
    baseline(source[:a] + BASE_DRIVE + source[b:])
    clock = (RUNTIME / 'daynight_clock.c').read_text()
    variants = {
        'production': (source, clock),
        'no_freeze_broken': (source, clock.replace('!input->race && !input->paused', '!input->paused')
                            .replace('clock->race_hour) : clock->hour', 'clock->hour) : clock->hour')),
        'no_weight_broken': (source, clock.replace('(float)fmin(1, height / DAYNIGHT_SUN_HEIGHT)',
                                                  '(float)(1 + 0 * height)')),
        'no_wrap_broken': (source, clock.replace('daynight_wrap(clock->hour + input->dt / rate)',
                                                '(clock->hour + input->dt / rate)')),
        'career_ignored_broken': (source, clock.replace('mode == DAYNIGHT_GAME && input->career == 2', '0')),
        'race_identity_broken': (source, clock.replace(' || input->race_id != clock->race_id', '')),
        'base_drive_broken': (source[:a] + BASE_DRIVE + source[b:], clock),
        'wrap_to_noon_broken': (source, clock.replace('daynight_wrap(clock->hour + input->dt / rate)',
            '(clock->hour + input->dt / rate >= 24 ? clock->hour + input->dt / rate - 12 : '
            'clock->hour + input->dt / rate)')),
        'phase_reset_at_dusk_broken': (source.replace(
            '0.05f + 0.9f * fmaxf(0, fminf(1, (float)((hour - 6) / 12)))',
            '0.05f + 0.9f * fmod((hour + 18) / 12, 1)'), clock),
    }
    flags = ['clang', '-std=c11', '-D_DARWIN_C_SOURCE', '-O1', '-g',
             '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
             '-I', str(RUNTIME), '-I', str(ROOT / 'recomp')]
    clean = {k:v for k,v in os.environ.items() if not k.startswith(('NFS_DAYNIGHT', 'NFS_TEST_DAYNIGHT'))}
    clean['NFS_DAYNIGHT'] = 'on'
    with tempfile.TemporaryDirectory(prefix='daynight-clock-') as temp:
        temp = Path(temp)
        harness = temp / 'check.c'; harness.write_text(HARNESS)
        sky = temp / 'sky.o'
        subprocess.run(['clang', '-std=c11', '-g', '-fsanitize=address,undefined',
                        '-DDN_SKY_CORE_ONLY=1', '-I', str(RUNTIME), '-I', str(ROOT / 'recomp'),
                        '-c', str(RUNTIME / 'daynight_sky.c'), '-o', str(sky)], check=True)
        for name, (driver_source, clock_source) in variants.items():
            if name != 'production': assert (driver_source, clock_source) != (source, clock)
            candidate = temp / 'daynight.c'; candidate.write_text(driver_source)
            unit = temp / 'clock.c'; unit.write_text(clock_source)
            exe = temp / name
            subprocess.run(flags + [f'-DDAYNIGHT_SOURCE="{candidate}"', str(harness), str(unit),
                                    str(sky),
                                    str(RUNTIME / 'settings.c'), '-o', str(exe)], check=True)
            args = ['dusk'] if name == 'phase_reset_at_dusk_broken' else ['wrap'] if name == 'wrap_to_noon_broken' else []
            result = subprocess.run([str(exe)] + args, text=True, capture_output=True, env=clean)
            if name.endswith('_broken'):
                assert result.returncode != 0 and 'FAIL ' in result.stderr, result.stdout + result.stderr
                if args: assert result.stderr.startswith('FAIL phase rule '), result.stderr
                print('PASS rejected ' + name + ': ' + result.stderr.strip().splitlines()[0])
            else:
                assert result.returncode == 0, result.stdout + result.stderr
                print(result.stdout.strip())
                prefs = temp / 'prefs.txt'
                for action in ('save', 'load'):
                    result = subprocess.run([str(exe), action, str(prefs)], text=True,
                                            capture_output=True, env=clean)
                    assert result.returncode == 0, result.stdout + result.stderr
                    print(result.stdout.strip())


if __name__ == '__main__':
    main()
