"""Full-orbit and actual-hook proof, no game or private inputs required.

Tolerance 1e-6: n=0 preserves the block byte for byte; n=1 equals the night
record; 99 intermediate weights check every field independently. Broken ambient
and retail swing-back variants must fail their numerical checks. Production and
public fallback: 2,323 mix checks, max error 5.96e-8; 14,314 total numeric checks,
max error 1.20e-7. Missing ambient fails by 0.00178000331 at n=.01; retail
swing-back gives midnight sunZ +0.137284 instead of -0.877583 (error 1.014867).
Clock rate (150 s per game hour, 60 real minutes per day): from hour 3, dt 150 s must
give hour 4 and 24 more steps (3600 s) hour 4 again. With the old 90 s constant the
rate_broken variant fails: hour 4.66666651 instead of 4 (error 0.667); new constant: exact.
The harness compiles the production daynight.h interface as well as all three hooks.
Private expansion: 4 public math definitions, 0 guest accesses/constructors;
removing the MW fence gives 8 definitions, 61 guest tokens and 1 constructor.
Registration has zero calls while Off; omitted sun weight fails by .300000012.
Cars follow the hour (NFS_DAYNIGHT_CARS): the in-game rig's light 0 luminance at
midnight over noon, as fed by the elSetupLights hook, is 0.070506 (expected
Y(night sun)/Y(day sun) 0.070506; noon equals the shipped rig 0.744170 exactly).
With the switch off or the scaling removed (cars_broken) it stays 1: fails by 0.929494.
The expanded layer checker finds 0 MW reads with the fence, 2 without it.
"""
from pathlib import Path
import os
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / 'runtime'

HARNESS = r'''
#include <assert.h>
#include <stdarg.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include DAYNIGHT_SOURCE
uint8_t *G;
int diag_on;
static int advances, blends, checks;
static float max_error;
static DaynightRecord authored;
static int clock_calls;
static void registered_clock(const DaynightClockInput *in, DaynightClockOutput *out) {
    ++clock_calls;
    if (in->active) { out->hour=12; out->weight=.5f; }
}
uint64_t diag_current_frame(void) { return 0; }
uint64_t host_ticks(void) { return 6000000000ull; }
void diag_event(const char *format, ...) { (void)format; }
int postfx_look_variant(const char *v) { (void)v; return 0; }
void f_00769360_orig(Cpu *c) { advances++; c->eax=0x123; c->esp+=4; }
void f_00769580_orig(Cpu *c) {
    blends++;
    // The engine rebuilds all fields, including ambient's forced W=4.
    record_io(c->ecx, &authored, 1);
    c->eax=0x456; c->esp+=8;
}
static void close_value(float got, float expected, const char *field) {
    float error = fabsf(got - expected);
    if (error > max_error) max_error=error;
    ++checks;
    if (!(error <= 1e-6f)) {
        fprintf(stderr, "FAIL %s got=%.9g expected=%.9g error=%.9g\n", field,got,expected,error);
        exit(1);
    }
}
static void check_record(const DaynightRecord *got, const DaynightRecord *day,
                         const DaynightRecord *night, float n) {
#define V(f) close_value(got->f, (float)((1.0-(double)n)*day->f+(double)n*night->f), #f)
    for(int i=0;i<3;i++) { V(diffuse[i]); V(specular[i]); V(sky[i]); V(haze[i]); }
    for(int i=0;i<4;i++) { V(ambient[i]); }
    V(environment); V(sky_brightness); V(sky_scale); V(haze_scale);
    V(scatter); V(sun_falloff); V(distance_scale);
#undef V
}
static DaynightRecord day_record(void) {
    return (DaynightRecord){.diffuse={.6f,.48f,.22f}, .ambient={.19f,.24f,.32f,4},
        .specular={.58f,.53f,.42f}, .environment=1, .sky_brightness=2.5f,
        .sky={.27f,.46f,.96f}, .haze={.19f,.25f,.38f},
        .sky_scale=2, .haze_scale=.3f, .scatter=.4f, .sun_falloff=5, .distance_scale=8};
}
static Cpu cpu(uint32_t tod) {
    Cpu c={.ecx=tod,.esp=0x4000,.ebx=11,.esi=22,.edi=33,.ebp=44};
    return c;
}
static void abi(const Cpu *c, uint32_t esp) {
    assert(c->esp==esp && c->ebx==11 && c->esi==22 && c->edi==33 && c->ebp==44);
}
// The shipped in-game car rig (speed.exe .data 008F2E70): colour x intensity per light.
static const float RIG[3][4]={{.85f,.90f,1,.83f},{.55f,.60f,1,.40f},{.65f,.70f,1,.45f}};
static const float RIG_SUN[3]={.85f*.83f,.90f*.83f,1*.83f};
#define CONTEXT 0x6000u
static float luma(const float *v) { return .2126f*v[0]+.7152f*v[1]+.0722f*v[2]; }
// The original copies the rig colours to the context and returns 1 (cdecl, caller pops).
void f_00502130_orig(Cpu *c) {
    uint32_t ctx=arg(c,0);
    for(int l=0;l<3;l++) for(int i=0;i<3;i++) write_float(ctx+0x10+0x10*l+4*i,RIG[l][i]*RIG[l][3]);
    c->eax=1; c->esp+=4;
}
static float car_sun_y(uint32_t tod, double hour, uint32_t rig, int views) {
    fixed_hour=hour;
    Cpu c=cpu(tod); f_00769360(&c);
    for(int v=0;v<views;v++) { c=cpu(tod); f_00769580(&c); }
    c=cpu(0); M32(0x4004)=CONTEXT; M32(0x4008)=rig;
    f_00502130(&c); abi(&c,0x4004); assert(c.eax==1);
    float v[3]; for(int i=0;i<3;i++) v[i]=read_float(CONTEXT+0x10+4*i);
    return luma(v);
}
int main(void) {
    DaynightRecord day=day_record(), night, got;
    daynight_night_record(&night);
    for(int i=0;i<=100;i++) {
        float n=i/100.0f;
        got=day;
        daynight_mix(&got,&night,n);
        check_record(&got,&day,&night,n);
        if(i==0) assert(!memcmp(&got,&day,sizeof(got)));
        if(i==100) assert(!memcmp(&got,&night,sizeof(got)));
    }
    printf("PASS mix: %d scalar checks, max error %.9g (limit 1e-6)\n",checks,max_error);
    DaynightSample noon,dusk,midnight,dawn;
    daynight_sample(12,3.28f,.5f,&noon);
    daynight_sample(18,3.28f,.5f,&dusk);
    daynight_sample(0,3.28f,.5f,&midnight);
    daynight_sample(6,3.28f,.5f,&dawn);
    printf("orbit noonZ=%.6f midnightZ=%.6f duskN=%.6f\n",noon.sun[2],midnight.sun[2],dusk.night);
    close_value(noon.phase,.5f,"noon.phase");
    close_value(dawn.phase,.05f,"dawn.phase");
    close_value(dusk.phase,.95f,"dusk.phase");
    close_value(noon.night,0,"noon.n"); close_value(dusk.night,.5f,"dusk.n");
    close_value(midnight.night,1,"midnight.n");
    close_value(dawn.night,.5f,"dawn.n");
    close_value(midnight.sun[2],-cosf(.5f),"midnight sun below horizon");
    assert(midnight.moon && !noon.moon);
    for(int i=0;i<3;i++) {
        close_value(dawn.sun[i],-dusk.sun[i],"opposite horizons");
        close_value(midnight.light[i],noon.sun[i],"opposing moon");
    }
    for(int i=0;i<2400;i++) {
        double h=i*.01;
        DaynightSample a,b;
        daynight_sample(h,3.28f,.5f,&a); daynight_sample(h+24,3.28f,.5f,&b);
        float norm=0;
        for(int j=0;j<3;j++) { norm+=a.sun[j]*a.sun[j]; close_value(a.sun[j],b.sun[j],"24h wrap"); }
        close_value(norm,1,"unit orbit");
        assert(a.night>=0 && a.night<=1);
        if(h>7 && h<17) close_value(a.night,0,"day plateau");
        if(h>19 || h<5) close_value(a.night,1,"night plateau");
    }
    G=calloc(1,0xA00000); assert(G);
    uint32_t tod=0x5000;
    M32(TOD_POINTER)=tod; M32(FLOW)=6;
    write_float(tod+0xc,3.28f); write_float(tod+0x10,.5f);
    authored=day;
    if (lighting_mode_d65()) {
        assert(enabled && fixed && cars_enabled);
        Cpu c=cpu(tod); f_00769360(&c);
        c=cpu(tod); f_00769580(&c); record_io(tod,&got,0);
        for (int k=0;k<3;++k) {
            close_value(got.diffuse[k],luma(night.diffuse),"D65 moon");
            close_value(got.ambient[k],luma(night.ambient),"D65 ambient");
            close_value(got.specular[k],luma(night.specular),"D65 specular");
        }
        close_value(got.ambient[3],night.ambient[3],"D65 ambient W");
        car_sun_y(tod,0,CAR_RIG,1);
        for (int k=1;k<3;++k) close_value(read_float(CONTEXT+0x10+4*k),read_float(CONTEXT+0x10),"D65 rig");
        puts("PASS explicit D65 enables the clock and keeps the moon, ambient, specular and rig at D65");
        free(G);
        return 0;
    }
    // Off: exact original-call ABI and no object/config reads in either hook.
    enabled=0;
    Cpu c=cpu(tod); f_00769360(&c); abi(&c,0x4004); assert(c.eax==0x123 && advances==1);
    c=cpu(tod); f_00769580(&c); abi(&c,0x4008); assert(c.eax==0x456 && blends==1);
    record_io(tod,&got,0); assert(!memcmp(&got,&day,sizeof(got)));
    // Fixed-hour On: both hooks use the same sample and each view starts afresh.
    enabled=1; fixed=1;
    for(int h=0;h<=24;h+=6) {
        fixed_hour=h;
        c=cpu(tod); f_00769360(&c); abi(&c,0x4004);
        DaynightSample s; daynight_sample(h,3.28f,.5f,&s);
        for(int i=0;i<3;i++) close_value(read_float(tod+0x180+4*i),s.light[i],"live light");
        assert(M32(tod+4)==1);
        for(int view=0;view<3;view++) {
            c=cpu(tod); f_00769580(&c); abi(&c,0x4008);
            record_io(tod,&got,0); check_record(&got,&day,&night,s.night);
        }
    }
    assert(advances==1 && blends==16);
    // Clock rate: 150 s is exactly one game hour, 3600 s (24 steps) is one full day.
    fixed=0; clock_state.hour=3; clock_state.phase=read_float(tod+8); write_float(REAL_DT,150);
    c=cpu(tod); f_00769360(&c); close_value((float)clock_state.hour,4,"clock 150 s = 1 h");
    for(int i=0;i<24;i++){ c=cpu(tod); f_00769360(&c); }
    close_value((float)clock_state.hour,4,"clock 3600 s = full day");
    // Cycle: no swing-back at midnight, native phase setter reseeds daylight.
    fixed=0; clock_state.hour=23.999; clock_state.phase=read_float(tod+8);
    write_float(REAL_DT,.3f);
    c=cpu(tod); f_00769360(&c); close_value((float)clock_state.hour,.001f,"clock midnight wrap");
    write_float(tod+8,.275f); write_float(REAL_DT,0);
    c=cpu(tod); f_00769360(&c); close_value((float)clock_state.hour,9,"native slider");
    // Non-game flow remains wholly original; the next race reseeds the clock.
    M32(FLOW)=3; c=cpu(tod); f_00769360(&c); assert(advances==2 && !clock_state.ready);
    c=cpu(tod); f_00769580(&c); record_io(tod,&got,0); assert(!memcmp(&got,&day,sizeof(got)));
    // Padding, alpha and extra fields are outside the mix (sentinel round trip).
    memset(G+tod+0x20,0xa5,0xa0); record_io(tod,&day,1);
    assert(M32(tod+0x2c)==0xa5a5a5a5 && M32(tod+0x4c)==0xa5a5a5a5);
    assert(M32(tod+0x68)==0xa5a5a5a5 && M32(tod+0xac)==0xa5a5a5a5);
    // Cars follow the hour: the in-game rig's light 0 luminance, midnight over noon.
    M32(FLOW)=6; enabled=1; fixed=1; cars_enabled=1; record_io(tod,&day,1);
    float noon_y=car_sun_y(tod,12,CAR_RIG,1), mid_y=car_sun_y(tod,0,CAR_RIG,1);
    float expect=luma(night.diffuse)/luma(day.diffuse);
    printf("cars: rig light 0 Y noon=%.6f midnight=%.6f ratio=%.6f (expected %.6f)\n",
           noon_y,mid_y,mid_y/noon_y,expect);
    close_value(noon_y,luma(RIG_SUN),"car rig unchanged at noon");
    close_value(mid_y/noon_y,expect,"car sun follows the hour");
    float sky=read_float(CONTEXT+0x20)/(.55f*.40f);
    close_value(sky,luma(night.ambient)*night.ambient[3]/(luma(day.ambient)*day.ambient[3]),"car fills follow the sky");
    close_value(car_sun_y(tod,0,0x00900000u,1),luma(RIG_SUN),"other rigs untouched");
    cars_enabled=0;
    close_value(car_sun_y(tod,0,CAR_RIG,1),luma(RIG_SUN),"car switch off leaves the rig");
    // A stripped/public source uses the fallback palette and still executes.
    unsetenv("NFS_DAYNIGHT"); unsetenv("NFS_TEST_DAYNIGHT_HOUR");
    setenv("NFS_DAYNIGHT_CARS","on",1);
    daynight_configure(); assert(!enabled && !cars_enabled);
    setenv("NFS_DAYNIGHT","on",1); setenv("NFS_TEST_DAYNIGHT_HOUR","24",1);
    daynight_configure(); assert(enabled && fixed && fixed_hour==0 && cars_enabled);
    unsetenv("NFS_DAYNIGHT_CARS"); daynight_configure(); assert(enabled && !cars_enabled);
    // A separately registered provider adds no mandatory dependency to this unit.
    daynight_register_clock(registered_clock);
    enabled=0; c=cpu(tod); f_00769360(&c); assert(clock_calls==0);
    enabled=1; M32(FLOW)=6; c=cpu(tod); f_00769360(&c);
    assert(clock_calls==1 && clock_state.hour==12);
    c=cpu(tod); f_00769580(&c); record_io(tod,&got,0);
    for(int i=0;i<3;i++) {
        close_value(got.diffuse[i],day.diffuse[i]*.5f,"registered sun weight");
        close_value(got.specular[i],day.specular[i]*.5f,"registered specular weight");
    }
    for(int i=0;i<4;i++) close_value(got.ambient[i],day.ambient[i],"registered ambient unchanged");
    M32(FLOW)=3; c=cpu(tod); f_00769360(&c); assert(clock_calls==2);
    daynight_register_clock(NULL);
    free(G);
    printf("PASS orbit/hooks: %d total numeric checks, max error %.9g; Off, ABI, slider, repeated views\n",checks,max_error);
    return 0;
}
'''


def check_private_source(candidate):
    result = subprocess.run(
        ['clang', '-E', '-P', '-DNFS_GAME=2', '-I', str(ROOT / 'recomp'),
         '-I', str(RUNTIME), str(candidate)],
        check=True, capture_output=True, text=True)
    expanded = result.stdout
    definitions = re.findall(r'^(?:double|void|float)\s+(\w+)\([^;{}]*\)\s*\{',
                             expanded, re.M)
    expected = ['daynight_mix', 'daynight_night_record', 'daynight_sample', 'daynight_wrap']
    guest = re.findall(r'M32\s*\(|\bG\s*\+|f_00769', expanded)
    constructors = re.findall(r'__attribute__\s*\(\(constructor\)\)', expanded)
    assert sorted(definitions) == expected and not guest and not constructors, (
        f'private expansion: {len(definitions)} public definitions '
        f'{sorted(definitions)}, {len(guest)} guest tokens, {len(constructors)} constructors')


def main():
    source = (RUNTIME / 'daynight.c').read_text()
    with tempfile.TemporaryDirectory(prefix='daynight-test-') as directory:
        scratch = Path(directory)
        harness = scratch / 'harness.c'
        harness.write_text(HARNESS)
        flags = ['clang', '-std=c11', '-D_DARWIN_C_SOURCE', '-Wall', '-Wextra', '-Werror',
                 '-O1', '-g', '-fsanitize=address,undefined', '-I', str(ROOT / 'recomp'),
                 '-I', str(RUNTIME)]
        private = scratch / 'private.c'
        private.write_text(source)
        check_private_source(private)
        print('PASS private expansion: 4 public math definitions, 0 guest tokens, 0 constructors')
        fence = '#if NFS_GAME == NFS_GAME_MW\n'
        assert source.count(fence) == 1 and source.endswith('#endif\n')
        private.write_text(source.replace(fence, '', 1).removesuffix('#endif\n'))
        try:
            check_private_source(private)
        except AssertionError as error:
            print('PASS rejected missing MW fence: ' + str(error))
        else:
            raise AssertionError('accepted missing MW fence')
        variants = {
            'production': source,
            'ambient_broken': source.replace(
                'day->ambient[i] = mix(day->ambient[i], night->ambient[i], n);', '(void)i;'),
            'retail_swing_broken': source.replace(
                'double a = (hour - 12) * PI / 12;', 'double a = (out->phase - 0.5) * PI;'),
            'rate_broken': source.replace('#define SECONDS_PER_GAME_HOUR 150.0', '#define SECONDS_PER_GAME_HOUR 90.0'),
            'weight_broken': source.replace('after.diffuse[i] *= clock_weight;', '(void)clock_weight;'),
            'cars_broken': source.replace('write_float(a + 4 * i, rgb[i] * f);', '(void)f;'),
            'public': source,
        }
        for name, text in variants.items():
            assert name in ('production', 'public') or text != source
            candidate = scratch / (name + '.c')
            candidate.write_text(text)
            binary = scratch / name
            variant_flags = flags
            if name == 'public':
                # Exact source, with the excluded include truly absent from the
                # compiler's search path. The public headers remain unchanged.
                for header in ('host.h', 'daynight.h', 'game_select.h', 'lighting_colour.h'):
                    (scratch / header).write_bytes((RUNTIME / header).read_bytes())
                variant_flags = flags[:-2]
            subprocess.run(variant_flags + [f'-DDAYNIGHT_SOURCE="{candidate}"', str(harness), '-o', str(binary)], check=True)
            result = subprocess.run([str(binary)], text=True, capture_output=True,
                                    env=dict(os.environ, NFS_DAYNIGHT='off'))
            if name == 'production':
                env = dict(os.environ, NFS_WORLD_LIGHTING='neutral-d65',
                           NFS_DAYNIGHT='off', NFS_DAYNIGHT_CARS='on', NFS_TEST_DAYNIGHT_HOUR='0')
                neutral = subprocess.run([str(binary)], text=True, capture_output=True, env=env)
                assert neutral.returncode == 0, neutral.stderr
                print(neutral.stdout.strip().splitlines()[-1])
            if name.endswith('_broken'):
                assert result.returncode != 0 and 'FAIL ' in result.stderr, result.stdout + result.stderr
                print('PASS rejected ' + name + ': ' + result.stderr.strip().splitlines()[0])
            else:
                assert result.returncode == 0, result.stdout + result.stderr
                print(name + ': ' + result.stdout.strip())


if __name__ == '__main__':
    main()
