"""Sky identity, J2 orbit reconstruction and night-sky recipe proof.

Clear-moon regression (04:00, race 15.1.1, sim 25 s, held full X360 chain,
3440x2160): ring mean Y 207.604898 -> 6.922093 (limit <=60/255), inner-face
SD 0.508055 -> 44.244419 (>=15), disc-edge dY 1.060103 -> 177.548568 (>=19).
Ring spans 1.5..2.5 projected disc radii (54,611 pixels); face rho<.7 (6,611),
edge inner .85..95 (2,433), outer 1.05..1.15 (2,974). All Y statistics use
.2126R+.7152G+.0722B on source PNG bytes, before JPEG scaling. Clear z=.01;
overcast z=.8 mean absolute Y delta from the nightclouds overcast is .099373
(limit <=2), in the open-sky box (1500,340)-(1800,600), including the moon.
--check-moon-frames CLEAR OVERCAST BASELINE_OVERCAST ORIGINAL_GLOW checks
these final-chain limits and requires the original-glow control ring to fail.
The refreshed still includes full final frames and source-scale sky details.

One diagnostic held run: original ring 205.456305, face removed 3.278939,
face bounded 4.334571, bloom disabled 207.214450, untreated input 206.876371.
The white clamped face plane times the long-tailed glow fills the ring before
post; bounding alone leaves face SD 3.074689. The repair bounds the disc,
resolves the texture's shallow face contrast, and gives it a thin falling halo.
Cloud density/shading/rim are unchanged; full overcast keeps the old lunar term.

Offline Metal raster, same projected masks and real texture: clear ring
6.963441, face SD 47.680089, edge 183.939275; deliberately restored original
lunar term gives ring 228.905565, SD .009405, edge .217035 and exits 1.
Overcast max RGB difference .000030398/255 across 420,000 sky samples
(limit <=2). Off returns the original shader and uploads zero bindings;
noon is byte-identical in all three passes. Temporary game instrumentation
was removed; three guarded 150-second runs, no hangs or stalls.

Native weather density: frozen recorded WVP/scroll, n=1, 1144x720, sky box
(700,24)-(880,104), 14,400 pixels before post/HUD. Production w=0 mean
0.009113919 (limit <=.05), w=1 mean 0.729113539 (limit >=.6). Deliberately
broken multiplier=1 gives 0.729113539/0.729113539 and fails. Clear uses MW's
zero-weather floor, CloudIntensity.z=.01; overcast uses its .8 ceiling.
The production shader is compiled on Metal with only its output redirected
to density; the real private cloud texture, linear/repeat sampler and original
weather register are used. Missing private texture skips this asset check.
Three weather constants (.01/.25/.8) also exercise the full night-colour recipe:
324 comparisons per fixture, max error 7.62939453e-6 (limit 1e-4), including
byte-identical noon output; Off uploads no replacement bindings.
Matched final still: run_shots/nightclouds-midnight.jpg, 04:00, race 15.1.1,
sim 25 s, 3440x2160 sources. Two held passes use the clear/overcast native
constant endpoints (.01/.8), preserving view and cloud scroll. The temporary
diagnostic constant override is removed from the final production build.

Moon sweep: 432,171 samples over 24 h, with fine steps at blend endpoints and
both handovers, three azimuths/tilts and adjacent representable hours. Before:
180 degrees, 18,000 wrong bearings just before 06:00 and after 18:00
(outer sampled hours 5.99999001 and 18.00000999).
After: 2.83583403e-6 degrees at 1.657 h, zero wrong bearings.
Restoring the original pinned flip decision fails; disabling solar suppression
fails the producer check (98 half-hour/diagnostic-mode cases, plus Off).
Checks also cover n=0/.5/1, missing texture fallback, the polygon hook ABI,
the vertex-program view ray and separately compiled broken controls. The base
pass is compared with an independent reference of the decoded pixel program
(cloud density from alpha, shading from red, moon face from green). Numerical
results: 2,441
CPU comparisons, max error 5.51342964e-7; the earlier full-overcast GPU fixture
had max error 7.63e-6 against a limit of 1e-4. Cloud density read from green (the
earlier fault) fails by .00034857 at n=.5; a missing base blend fails by .39683628 and
constant-one cloud alpha by .5, both at n=.5. The recorder device-getter
control throws; production binding succeeds without that selector. The J2
midnight game log (0 solar / 12 sky / 0 night) fails the acceptance check.
The screen-space SunInfo producer (006D04E0) is independently suppressed in
98 half-hour/diagnostic-mode cases and forwarded when Off, preserving ret-4
stack cleanup and registers. Restoring it fails screen_sun_producers==before
at the first night sample; production passes all 98 plus Off. The new shader
fixtures include aligned moon gains 3.2 and 30: disabling the shoulder fails
by .25188124 at n=.5; production's maximum reference error is 7.62939453e-6.
Cloud emission remains .5 x sky green/.04; moon emission uploads 3.200000.
The previous .500000 binding is rejected. Off still writes no bindings, and
n=0 preserves the original shader output byte-for-byte in all three GPU
passes (memcmp of the four output floats).
Final-frame proof: 04:00, race 15.1.1, sim 25 s, open sky, identical recorded
projection/light/cloud-scroll constants. Moving frame: edge dY 13.5175 ->
19.0891, face SD(Y) 27.6863 -> 21.1028, disc mean Y 143.3347 -> 176.3977,
clipping 0 -> 0 percent. Held normal final composite (no stage bypass): edge
15.5585 -> 23.0910, face SD 26.0031 -> 9.9609, disc mean 149.6157 -> 190.6548.
The large old SD includes solar glare, not just face markings. Constant-white
face control SD is 0. The labelled image includes both final views; the held
view separates lunar markings from the moving frame's temporal softness.
Solar game proof: race 15.1.1, BOOT_RACE_AI=0 BOOT_AUTODRIVE=1 NFS_LOCKSTEP=1
NFS_SIMHZ=60 NFS_DAYNIGHT=1 NFS_DAYNIGHT_MODE=fixed NFS_DAYNIGHT_HOUR=4.5
NFS_TEST_DAYNIGHT_HOUR=4.5 NFS_POSTFX=x360, full-detail registry recipe,
BOOT_KEYS=seq:skipmovies, BOOT_DIAG_SIMSHOTS=25. Never use the menu preset
for this race: its RETURN keys pause before the capture. The original solar
counter is renderer draws inside 006BE750's type-21 submission context,
across ALL views, not pixels on the moon. Both producer masks exclude primary
view 1. At frames 180..3270 (30-frame sampling), fixed is 0/104 versus the
suppression-off control 104/104, for both solar GPU draws and polygons.
The separate primary-view five-degree centre-bearing counter sees 0/104
for every flare type in BOTH builds; it does not test off-centre sprite overlap.
Its independent fixture accepts 2/2 aligned polygons (types 3 and 21), rejects
2/2 opposing polygons; the broken cosine cutoff accepts 0/2 aligned polygons.
--check-game-log events.log 4.5 requires zero all-view solar draws and positive
night draws in 104 samples; the suppression-off game log must fail.
Face isolation: pre-post RGB at (1930,640) is 242/244/239, matching decoded
shader RGB .94854/.95522/.93657; the cloud-rim contribution is .62822. A
nearby face pixel exceeds 1 before post. Centre green footprint max/mean is
254/251. Held equilibrium-exposure control moves centre Y 241.9278->99.9278
and rim contrast .069708->.029906; it does not restore the face. See daynight.md
for all single-stage controls, raw-pixel coordinates and the isolating still.
--generate writes private pixels only below recomp/out; no source asset writes.
"""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / 'runtime'

# Original handover decision, pinned independently of the production source.
ORIGINAL_MOON_RULE = '''    int day = phase > .05f && phase < .95f;
    if (!day) {
        float height = fmaxf(.001f, fabsf(cosf(tilt)));
        day = asinf(fminf(1, fmaxf(0, light[2] / len / height))) * (12 / 3.14159265f) < 1e-5f;
    }
'''


def generate(output):
    output = Path(output).resolve()
    if not output.is_relative_to(ROOT / 'recomp/out'):
        raise ValueError('texture output must be under recomp/out')
    output.mkdir(parents=True, exist_ok=True)
    include = output / 'daynight_sky_private.inc'
    # Public builds never consume a private include, even when one exists.
    if os.environ.get('NFS_PUBLIC') == '1':
        print('daynight sky: public lighting-only fallback')
        return
    sys.path[:0] = [str(ROOT / 'tools_snapshot'), str(ROOT / 'tools_snapshot/worldmerge')]
    from merge import DEFAULT_CARBON
    source = DEFAULT_CARBON.parent / 'GLOBAL/GLOBALB.BUN'
    if not source.is_file():
        include.unlink(missing_ok=True)
        print('daynight sky: texture unavailable; lighting-only fallback')
        return
    raw = source.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    decoder = hashlib.sha256(b''.join(p.read_bytes() for p in
        [Path(__file__), *(ROOT / 'tools_snapshot' / n for n in ('carbon_car.py', 'later_tex.py', 'x360_tex.py'))])).hexdigest()
    manifest = output / 'daynight_sky_private.json'
    if include.is_file() and manifest.is_file():
        saved = json.loads(manifest.read_text())
        if (saved.get('source_sha256') == digest and saved.get('decoder_sha256') == decoder
                and saved.get('include_sha256') == hashlib.sha256(include.read_bytes()).hexdigest()):
            print('daynight sky: private texture unchanged')
            return
    from carbon_car import carbon_global_extras
    from later_tex import decode_record
    from x360_tex import decode_preview
    records = carbon_global_extras(source, {0xbc6f56d9})
    if 0xbc6f56d9 not in records:
        include.unlink(missing_ok=True)
        print('daynight sky: texture record missing; lighting-only fallback')
        return
    r, pl, pixels = records[0xbc6f56d9]
    kind, w, h, top = decode_record(r, pl, pixels, False)
    rgba = decode_preview(kind, w, h, top).tobytes()
    data = ('// Generated texture; keep in private build output.\n'
            f'#define DN_SKY_PIXELS 1\n#define DN_SKY_HASH 0xbc6f56d9u\n#define DN_SKY_WIDTH {w}\n#define DN_SKY_HEIGHT {h}\n'
            'static const unsigned char dn_sky_pixels[] = {\n')
    data += '\n'.join(','.join(str(x) for x in rgba[p:p + 32]) + ',' for p in range(0, len(rgba), 32))
    data += '\n};\n'
    include.write_text(data)
    manifest.write_text(json.dumps(dict(source=str(source), source_sha256=digest,
        decoder_sha256=decoder, include_sha256=hashlib.sha256(include.read_bytes()).hexdigest(),
        texture='SKY_NIGHT_CLOUDS_02', hash='bc6f56d9', width=w, height=h,
        rgba_sha256=hashlib.sha256(rgba).hexdigest(), format=kind), indent=2) + '\n')
    print(f'daynight sky: generated {w}x{h} RGBA ({len(rgba)} bytes) in private output')


HARNESS = r'''
#include <assert.h>
#include <stdio.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include "host.h"
#include "daynight.h"
#include "daynight_sky.h"
#include SKY_SOURCE
uint8_t *G;
int diag_on;
static unsigned calls, checks;
static unsigned solar;
static unsigned moon_context_seen;
static float expected;
static float max_error;
static void write_float(uint32_t a, float x) { MF32(a)=x; }
void diag_event(const char *f, ...) { (void)f; }
uint64_t diag_current_frame(void) { return 0; }
uint64_t host_ticks(void) { return 0; }
void f_00769360_orig(Cpu *c) { c->esp += 4; }
void f_00769580_orig(Cpu *c) { c->esp += 8; }
void f_00502130_orig(Cpu *c) { c->esp += 4; }
static unsigned sun_producers, screen_sun_producers;
void f_00503830_orig(Cpu *c) { ++sun_producers; c->esp += 4; }
void f_006D04E0_orig(Cpu *c) { ++screen_sun_producers; c->esp += 8; }
int postfx_look_variant(const char *v) { (void)v; return 0; }
static void close_value(float a, float b) {
    float e=fabsf(a-b); ++checks; if(e>max_error) max_error=e;
    if(!(e < 2e-6f)) { fprintf(stderr,"FAIL got=%.9g expected=%.9g error=%.9g\n",a,b,e); exit(1); }
}
static double moon_worst_hour;
static void check_moon(double hour, float azimuth, float tilt, double *worst,
                       unsigned *total, unsigned *bad, double *first, double *last) {
    DaynightSample s; float m[3]; daynight_sample(hour,azimuth,tilt,&s);
    daynight_sky_moon(s.phase,s.light,azimuth,m);
    // The clock's contract supplies the oracle, independently of phase and Z.
    double sign=hour>=6 && hour<=18 ? -1 : 1, dot=0, cross[3];
    for(int j=0;j<3;j++) {
        dot+=(double)m[j]*s.light[j]*sign;
        cross[j]=sign*((double)m[(j+1)%3]*s.light[(j+2)%3]-(double)m[(j+2)%3]*s.light[(j+1)%3]);
    }
    double error=atan2(sqrt(cross[0]*cross[0]+cross[1]*cross[1]+cross[2]*cross[2]),dot)*180/3.141592653589793;
    ++*total; if(error>*worst) { *worst=error; moon_worst_hour=hour; }
    if(error>.001) { if(!*bad || hour<*first)*first=hour; if(!*bad || hour>*last)*last=hour; ++*bad; }
}
static void moon_sweep(void) {
    double worst=0,first=0,last=0; unsigned total=0,bad=0;
    for(int t=0;t<3;t++) for(int a=0;a<3;a++) {
        float tilt=t*.5f, azimuth=a*3.28f;
        for(int i=0;i<=24000;i++) check_moon(i*.001,azimuth,tilt,&worst,&total,&bad,&first,&last);
        // Blend endpoints, sunrise and sunset: 36 microseconds of game time
        // between samples, plus the adjacent representable hours at handover.
        const double hours[]={5,6,7,17,18,19};
        for(unsigned h=0;h<sizeof hours/sizeof *hours;h++) {
            for(int i=-2000;i<=2000;i++) check_moon(hours[h]+i*1e-8,azimuth,tilt,&worst,&total,&bad,&first,&last);
            check_moon(nextafter(hours[h],0),azimuth,tilt,&worst,&total,&bad,&first,&last);
            check_moon(nextafter(hours[h],24),azimuth,tilt,&worst,&total,&bad,&first,&last);
        }
    }
    printf("moon sweep: %u samples worst=%.9g degrees at %.15g h wrong=%u hours=%.15g..%.15g\n",total,worst,moon_worst_hour,bad,first,last);
    if(bad) { fprintf(stderr,"FAIL moon bearing\n"); exit(1); }
}
void flare_streaks_poly(Cpu *c) { (void)c; }
void f_006BE750_orig(Cpu *c) {
    ++calls;
    solar += sun_context != 0;
    moon_context_seen += moon_flare_context != 0;
    for(int i=0;i<4;i++) close_value(M32(M32(c->esp+4)+0x80+i*4)>>24, expected);
    c->esp += 24;
}
int main(void) {
    moon_sweep();
    // Independent smoothstep reference, all 24 hours including both handovers.
    for(int i=0;i<=2400;i++) {
        double h=i*.01;
        DaynightSample s; daynight_sample(h,3.28f,.5f,&s);
        float got=daynight_sky_factor(s.phase,s.light[2],.5f);
        double x=h<12 ? (h-5)/2 : (h-17)/2;
        if(x<0)x=0; if(x>1)x=1;
        double n=h<12 ? 1-x*x*(3-2*x) : x*x*(3-2*x);
        close_value(got,n);
    }
    assert(daynight_sky_texture(0xbc6f56d9));
    assert(!daynight_sky_texture(0x94d70856));
    assert(!daynight_sky_texture(0));
    G=calloc(1,0xa00000); assert(G);
    M32(0x00925E90)=6; M32(0x009B392C)=0x1000;
    write_float(0x1010,.5f);
    uint32_t stack=0x4000, poly=0x6000, frame=0x8000, flare=0x9000;
    M32(stack)=0x00505e4e; M32(stack+4)=poly; M32(frame+12)=flare; M8(flare+0x2c)=21;
    for(int i=0;i<4;i++) M32(poly+0x80+4*i)=0xff123456;
    enabled=1; diag_on=1;
    // Only the solar-only producer is suppressed. Neither this hook nor its
    // diagnostic polygon hook edits a shared flare parameter or vertex colour.
    for(int d=0;d<2;d++) for(int j=0;j<=48;j++) {
        DaynightSample s; daynight_sample(j*.5,3.28f,.5f,&s);
        write_float(0x1008,s.phase); write_float(0x1188,s.light[2]);
        diag_on=d;
        Cpu c={.esp=stack,.ebp=frame,.eax=16,.ebx=17,.esi=18,.edi=19};
        unsigned before=sun_producers;
        f_00503830(&c);
        assert(sun_producers==before+(s.night<=.001f));
        assert(c.esp==stack+4 && c.ebp==frame && c.eax==16 && c.ebx==17 && c.esi==18 && c.edi==19);
        c.esp=stack; unsigned screen_before=screen_sun_producers;
        f_006D04E0(&c);
        assert(screen_sun_producers==screen_before+(s.night<=.001f));
        assert(c.esp==stack+8 && c.ebp==frame && c.eax==16 && c.ebx==17 && c.esi==18 && c.edi==19);
    }
    enabled=0;
    Cpu screen_off={.esp=stack}; unsigned screen_before=screen_sun_producers;
    f_006D04E0(&screen_off);
    assert(screen_sun_producers==screen_before+1 && screen_off.esp==stack+8);
    Cpu off={.esp=stack}; unsigned producers_before=sun_producers;
    f_00503830(&off); assert(sun_producers==producers_before+1 && off.esp==stack+4);
    enabled=1; diag_on=1;
    for(int j=0;j<3;j++) {
        DaynightSample s; daynight_sample(12+j*3,3.28f,.5f,&s);
        write_float(0x1008,s.phase); write_float(0x1188,s.light[2]);
        Cpu c={.esp=stack,.ebp=frame,.ebx=17,.esi=18,.edi=19};
        expected=255;
        unsigned before=calls;
        f_006BE750(&c);
        assert(c.esp==stack+24 && c.ebp==frame && c.ebx==17 && c.esi==18 && c.edi==19);
        assert(calls==before+1);
        for(int i=0;i<4;i++) assert(M32(poly+0x80+4*i)==0xff123456);
    }
    DaynightSample s; daynight_sample(0,3.28f,.5f,&s);
    write_float(0x1008,s.phase); write_float(0x1188,s.light[2]);
    Cpu c={.esp=stack,.ebp=frame}; unsigned before=calls;
    f_006BE750(&c); assert(calls==before+1 && c.esp==stack+24);
    // The diagnostic hook never changes sprites, including midnight and Off.
    expected=255; M8(flare+0x2c)=3; c.esp=stack; f_006BE750(&c);
    enabled=0; M8(flare+0x2c)=21; c.esp=stack; f_006BE750(&c);
    assert(solar==5 && sun_context==0);
    // Independent geometry for the diagnostic's five-degree primary-view cone.
    // Aligned headlights and solar polygons count; opposite rays never count.
    uint32_t view=0xa000, camera=0xb000;
    M32(view+4)=1; M32(view+0x40)=camera;
    daynight_sample(4.5,3.28f,.5f,&s);
    write_float(0x1008,s.phase); write_float(0x100c,3.28f);
    for(int j=0;j<3;j++) { write_float(0x1180+4*j,s.light[j]); write_float(camera+0x40+4*j,10+j); }
    for(int t=0;t<2;t++) {
        int type=t ? 21 : 3; M8(flare+0x2c)=type;
        for(int side=0;side<2;side++) {
            for(int i=0;i<4;i++) for(int j=0;j<3;j++)
                write_float(poly+i*16+j*4,10+j+(side ? -100 : 100)*s.light[j]);
            c=(Cpu){.esp=stack,.ebp=frame,.ecx=view}; f_006BE750(&c);
            assert(c.esp==stack+24 && moon_flare_context==0);
        }
        assert(moon_flare_polys[type]==1);
    }
    assert(moon_context_seen==2);
    printf("moon-bearing diagnostic: aligned 2/2, opposing 0/2\n");
    free(G);
    printf("PASS %u numeric checks max_error=%.9g; diagnostic hook ABI/Off/colour preservation\n",checks,max_error);
}
'''

GPU_HARNESS = r'''
#import <Metal/Metal.h>
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include "host.h"
#include "daynight.h"
#ifdef TEST_SKY_BIND
#define DN_SKY_PIXELS 1
#ifdef TEST_SKY_WRONG_TEXTURE
#define DN_SKY_HASH 0xf43b323au
#else
#define DN_SKY_HASH 0xbc6f56d9u
#endif
#define DN_SKY_WIDTH 1
#define DN_SKY_HEIGHT 1
static const unsigned char dn_sky_pixels[] = {25,51,76,255};
#endif
#include SKY_SOURCE
uint8_t *G;
int diag_on;
static id<MTLSamplerState> fallback_state;
static unsigned fallback_calls;
static id<MTLSamplerState> fallback_sampler(int s, int shadow) { ++fallback_calls; return fallback_state; }
void diag_event(const char *f, ...) { (void)f; }
int diag_frame_wanted(uint64_t f, const char *e) { return 0; }
uint64_t diag_current_frame(void) { return 0; }
uint64_t host_ticks(void) { return 0; }
void flare_streaks_poly(Cpu *c) { (void)c; }
void f_006BE750_orig(Cpu *c) { c->esp+=24; }
void f_00769360_orig(Cpu *c) { c->esp+=4; }
void f_00769580_orig(Cpu *c) { c->esp+=8; }
void f_00502130_orig(Cpu *c) { c->esp += 4; }
void f_00503830_orig(Cpu *c) { c->esp += 4; }
void f_006D04E0_orig(Cpu *c) { c->esp += 8; }
int postfx_look_variant(const char *v) { (void)v; return 0; }
static const char *source =
"#include <metal_stdlib>\nusing namespace metal;\n"
"struct SmPSStageIn { float4 pos [[position]]; float2 texcoord0 [[user(texcoord0)]]; };\n"
"struct SmPSStageOut { float4 c0 [[color(0)]]; };\n"
"fragment SmPSStageOut sm_main(SmPSStageIn pi [[stage_in]], constant float4 *c [[buffer(0)]]) {\n"
"    SmPSStageOut r; r.c0 = float4(.8f,.6f,.4f,1.0f) + pi.texcoord0.x * 0.0f;\n"
"    return r;\n}\n";
// Appended after patching: only the patched base pass has the view-ray input.
static const char *vertex_plain =
"vertex SmPSStageIn dn_vertex(uint i [[vertex_id]]) { SmPSStageIn o; o.pos=float4(i==1?3.0f:-1.0f,i==2?3.0f:-1.0f,0,1); o.texcoord0=float2(.5f); return o; }\n";
static const char *vertex_ray =
"vertex SmPSStageIn dn_vertex(uint i [[vertex_id]]) { SmPSStageIn o; o.pos=float4(i==1?3.0f:-1.0f,i==2?3.0f:-1.0f,0,1); o.texcoord0=float2(.5f); o.texcoord4=float4(30.0f,50.0f,40.0f,.25f); return o; }\n";
// Independent reference: the decoded pixel program's arithmetic, constant by
// constant, on the reconstructed dome. px is one RGBA texel for both reads.
static void night_reference(const float *px, const float *k, float weather, double out[3]) {
    double v[3]={30,50,40}, l=sqrt(v[0]*v[0]+v[1]*v[1]+v[2]*v[2]); for(int i=0;i<3;i++) v[i]/=l;
    double R=12926.33, s=1/sqrt((v[0]*v[0]+v[1]*v[1])/(R*R)+v[2]*v[2]/(7000.0*7000.0)), P[3], D[3], dist=0;
    for(int i=0;i<3;i++) { P[i]=v[i]*s; D[i]=k[4+i]-P[i]; dist+=D[i]*D[i]; }
    dist=sqrt(dist); double cosine=0; for(int i=0;i<3;i++) { D[i]/=dist; cosine+=v[i]*D[i]; }
    double d=0,q[3]; for(int i=0;i<3;i++) { q[i]=P[i]+v[i]*(dist-cosine)-k[4+i]; d+=q[i]*q[i]; }
    double t1[3]={D[1]*.995037-D[2]*.0995037,-D[0]*.995037,D[0]*.0995037};
    double tl=sqrt(t1[0]*t1[0]+t1[1]*t1[1]+t1[2]*t1[2]); for(int i=0;i<3;i++)t1[i]/=tl;
    double t2[3]={D[1]*t1[2]-D[2]*t1[1],D[2]*t1[0]-D[0]*t1[2],D[0]*t1[1]-D[1]*t1[0]};
    double u=0,w=0; for(int i=0;i<3;i++) { u+=t1[i]*q[i]; w+=t2[i]*q[i]; }
    double rho=sqrt(pow(u*1.21,2)+w*w)/3200;
    double edge=fmin(1,fmax(0,(rho-.98)/.04)); edge=edge*edge*(3-2*edge);
    double disc=(.10+.75*fmin(1,fmax(0,(px[1]-.90)*10)))*(1-edge);
    double halo=.025*exp(-fmax(rho-1,0)*8);
    double glow=fmin(1/pow(sqrt(d)*0.000477,0.57),0.915);
    double zen=sqrt(P[0]*P[0]+P[1]*P[1]+(7000-P[2])*(7000-P[2]))*6.80272e-05; if(zen>1)zen=1;
    double a=(1-pow(zen,4))*px[3]*fmin(1,fmax(0,weather*1.25)), g=fmin(1,fmax(0,glow*0.357143));
    double rim=g*g*(3-2*g)*200*pow(a*(1-a),3.07), body=a*px[0]*px[0]*0.34*pow(1-glow,4);
    double cover=fmin(1,fmax(0,weather*1.25));
    double lunar=(disc+halo)*(k[7]/3.2)*(1-cover)+px[1]*pow(glow,1.7)*k[7]*cover;
    double tint[3]={0.99,1,0.92};
    for(int i=0;i<3;i++) out[i]=((1-P[2]*0.000142857)*k[1+i]+lunar*tint[i])*(1-a)+(rim+body)*k[2]*12.5;
    double peak=fmax(out[0],fmax(out[1],out[2]));
    if(peak>.7) { double mapped=.95-.25*exp((.7-peak)/.25); for(int i=0;i<3;i++) out[i]*=mapped/peak; }
}
// The renderer records only supported encoder messages on the game thread.
// In particular this object deliberately has no device getter.
@interface SkyEncoder : NSObject
@property unsigned writes;
@property float emission;
@property id<MTLRenderCommandEncoder> backing;
@end
@implementation SkyEncoder
- (void)setFragmentBytes:(const void *)p length:(NSUInteger)n atIndex:(NSUInteger)i { ++_writes; assert(n==32 && i==6); _emission=((const float *)p)[7]; [_backing setFragmentBytes:p length:n atIndex:i]; }
- (void)setFragmentTexture:(id<MTLTexture>)t atIndex:(NSUInteger)i { ++_writes; [_backing setFragmentTexture:t atIndex:i]; }
- (void)setFragmentSamplerState:(id<MTLSamplerState>)s atIndex:(NSUInteger)i { ++_writes; [_backing setFragmentSamplerState:s atIndex:i]; }
@end
int main(void) { @autoreleasepool {
    id<MTLDevice> d=MTLCreateSystemDefaultDevice(); assert(d);
    id<MTLCommandQueue> q=[d newCommandQueue]; assert(q);
    // No generated include is on this compilation's include path: a genuine
    // missing-texture public build must return the exact original translation.
    SmResult refl={.shader_type=SM_PIXEL,.msl=(char *)source};
    unsetenv("NFS_DAYNIGHT"); sky_configure(); assert(enabled==0);
    // Off returns the original translation without even reading shader tokens.
    assert(!daynight_sky_shader(NULL,1,&refl));
    puts("PASS NFS_DAYNIGHT unset: original shader passthrough");
    enabled=1;
    uint32_t token=0; // Deliberately unrelated input, not a shader program.
    assert(!daynight_sky_shader(&token,1,&refl));
    assert(!daynight_sky_patch("unrecognised translation",1));
    assert(!daynight_sky_patch(source,0));
    // The vertex program gains the view ray and cloud scroll, or is left alone.
    assert(!daynight_sky_patch_vertex("unrecognised translation"));
    char *vs=daynight_sky_patch_vertex("constant float4 *c [[buffer(16)]] si.v[0] r.texcoord4 = so.tex[4]; tail");
    assert(vs && strstr(vs,"r.texcoord4 = float4(si.v[0].xyz - c[10].xyz, c[11].x); tail") && !strstr(vs,"so.tex[4]")); free(vs);
    // Moon: the light itself at night, opposite the sun by day and at the handover.
    { float m[3], tilt=.5f; DaynightSample s;
      daynight_sample(0,3.28f,tilt,&s); daynight_sky_moon(s.phase,s.light,3.28f,m);
      for(int i=0;i<3;i++) assert(fabsf(m[i]-83666*s.light[i])<1);
      daynight_sample(12,3.28f,tilt,&s); daynight_sky_moon(s.phase,s.light,3.28f,m);
      for(int i=0;i<3;i++) assert(fabsf(m[i]+83666*s.sun[i])<1);
      daynight_sample(18,3.28f,tilt,&s); daynight_sky_moon(s.phase,s.light,3.28f,m);
      for(int i=0;i<3;i++) assert(fabsf(m[i]+83666*s.sun[i])<1);
      daynight_sample(21,3.28f,tilt,&s); daynight_sky_moon(s.phase,s.light,3.28f,m);
      for(int i=0;i<3;i++) assert(fabsf(m[i]+83666*s.sun[i])<1 && m[2]>0);
      float zero[3]={0,0,0}; daynight_sky_moon(.5f,zero,3.28f,m); assert(m[0]==-50000 && m[1]==60000 && m[2]==30000); }
    MTLTextureDescriptor *td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA32Float width:1 height:1 mipmapped:NO];
    td.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;
    td.storageMode=MTLStorageModeShared;
    id<MTLTexture> out=[d newTextureWithDescriptor:td], night=[d newTextureWithDescriptor:td];
    float px[4]={.6f,.9f,.3f,.5f};
    [night replaceRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0 withBytes:px bytesPerRow:16];
    MTLSamplerDescriptor *sd=[MTLSamplerDescriptor new];
    sd.minFilter=sd.magFilter=MTLSamplerMinMagFilterLinear;
    id<MTLSamplerState> sampler=[d newSamplerStateWithDescriptor:sd];
    fallback_state=sampler;
    unsigned checks=0; float max_error=0;
    for(int pass=1;pass<=3;pass++) {
        char *patched=daynight_sky_patch(source,pass); assert(patched);
        NSError *error=nil;
        id<MTLLibrary> lib=[d newLibraryWithSource:[NSString stringWithFormat:@"%s%s",patched,pass==1?vertex_ray:vertex_plain] options:nil error:&error];
        free(patched);
        if(!lib) { fprintf(stderr,"FAIL shader: %s\n",error.description.UTF8String); return 1; }
        MTLRenderPipelineDescriptor *pd=[MTLRenderPipelineDescriptor new];
        pd.vertexFunction=[lib newFunctionWithName:@"dn_vertex"];
        pd.fragmentFunction=[lib newFunctionWithName:@"sm_main"];
        pd.colorAttachments[0].pixelFormat=MTLPixelFormatRGBA32Float;
        id<MTLRenderPipelineState> pipe=[d newRenderPipelineStateWithDescriptor:pd error:&error]; assert(pipe);
        for(int weather=0;weather<3;weather++) for(int fixture=0;fixture<3;fixture++) for(int j=0;j<3;j++) {
            float intensity[4]={1,.8f,weather==0?.01f:weather==1?.25f:.8f,0};
            float n=j*.5f, k[8]={n,.02f,.04f,.08f,20000,60000,45000,1.125f};
            if(fixture) { float norm=sqrtf(30*30+50*50+40*40); k[4]=83666*30/norm; k[5]=83666*50/norm; k[6]=83666*40/norm; k[7]=fixture==1?3.2f:30.0f; }
            double ref[3]; night_reference(px,k,intensity[2],ref);
            MTLRenderPassDescriptor *rp=[MTLRenderPassDescriptor renderPassDescriptor];
            rp.colorAttachments[0].texture=out;
            rp.colorAttachments[0].loadAction=MTLLoadActionClear;
            rp.colorAttachments[0].storeAction=MTLStoreActionStore;
            id<MTLCommandBuffer> cb=[q commandBuffer];
            id<MTLRenderCommandEncoder> enc=[cb renderCommandEncoderWithDescriptor:rp];
            [enc setRenderPipelineState:pipe];
            [enc setFragmentBytes:intensity length:16 atIndex:0];
            [enc setFragmentBytes:k length:32 atIndex:6];
            [enc setFragmentTexture:night atIndex:14];
            [enc setFragmentSamplerState:sampler atIndex:14];
            [enc drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
            [enc endEncoding]; [cb commit]; [cb waitUntilCompleted]; assert(!cb.error);
            float got[4]; [out getBytes:got bytesPerRow:16 fromRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0];
            float day[4]={.8f,.6f,.4f,1};
            if(j==0) assert(memcmp(got,day,sizeof day)==0);
            for(int c=0;c<4;c++) {
                float expected=pass==1 ? (c==3?1:day[c]+n*((float)ref[c]-day[c])) : (c==3?1-n:day[c]);
                float err=fabsf(expected-got[c]); checks++; if(err>max_error)max_error=err;
                if(!(err<1e-4f)) { fprintf(stderr,"FAIL GPU pass=%d n=%.1f channel=%d got=%.8f expected=%.8f error=%.8f\n",pass,n,c,got[c],expected,err); return 1; }
            }
        }
    }
    // Exercise the real upload/binding function through the recorder contract.
    G=calloc(1,0xa00000); assert(G);
    M32(0x00925E90)=6; M32(0x009B392C)=0x1000;
    MF32(0x1008)=.05f; MF32(0x1188)=cosf(.5f); MF32(0x1010)=.5f;
    id<MTLCommandBuffer> cb=[q commandBuffer];
    MTLRenderPassDescriptor *rp=[MTLRenderPassDescriptor renderPassDescriptor];
    rp.colorAttachments[0].texture=out;
    rp.colorAttachments[0].loadAction=MTLLoadActionClear;
    rp.colorAttachments[0].storeAction=MTLStoreActionStore;
    id<MTLRenderCommandEncoder> enc=[cb renderCommandEncoderWithDescriptor:rp];
    SkyEncoder *record=[SkyEncoder new]; record.backing=enc;
    char *bound_source=daynight_sky_patch(source,1); assert(bound_source);
    int bound=daynight_sky_bind((id<MTLRenderCommandEncoder>)record,d,0xd39c842f6c968e98ull,bound_source,1,night,fallback_sampler);
#if defined(TEST_SKY_BIND) && !defined(TEST_SKY_WRONG_TEXTURE)
    assert(bound==1 && record.writes==3);
    printf("moon emission: got=%.6f expected=3.200000 (previous=0.500000)\n",record.emission);
    fflush(stdout);
    assert(fabsf(record.emission-3.2f)<1e-6f);
    assert(!daynight_sky_bind((id<MTLRenderCommandEncoder>)record,d,0xd39c842f6c968e98ull,source,1,night,fallback_sampler));
    assert(record.writes==3);
    enabled=0;
    assert(!daynight_sky_bind((id<MTLRenderCommandEncoder>)record,d,0xd39c842f6c968e98ull,bound_source,1,night,fallback_sampler));
    assert(record.writes==3 && fallback_calls==0);
    puts("PASS production texture upload/bind through recorder without device getter; Off binds=0");
#else
    assert(bound==0 && record.writes==0 && fallback_calls==0);
    puts("PASS missing/wrong texture: original shader and zero texture binds");
#endif
    free(bound_source);
    [enc endEncoding]; [cb commit]; [cb waitUntilCompleted]; assert(!cb.error);
    free(G);
    printf("PASS GPU: %u float comparisons, n=0/.5/1 all three passes, n=0 byte-identical, max_error=%.9g\n",checks,max_error);
} }
'''


DENSITY_MAIN = r'''
#include "daynight_sky_private.inc"
int main(void) { @autoreleasepool {
    id<MTLDevice> d=MTLCreateSystemDefaultDevice(); assert(d);
    id<MTLCommandQueue> q=[d newCommandQueue]; assert(q);
    MTLTextureDescriptor *td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm width:DN_SKY_WIDTH height:DN_SKY_HEIGHT mipmapped:NO];
    id<MTLTexture> cloud=[d newTextureWithDescriptor:td];
    [cloud replaceRegion:MTLRegionMake2D(0,0,DN_SKY_WIDTH,DN_SKY_HEIGHT) mipmapLevel:0 withBytes:dn_sky_pixels bytesPerRow:DN_SKY_WIDTH*4];
    td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA32Float width:1144 height:720 mipmapped:NO];
    td.usage=MTLTextureUsageRenderTarget; td.storageMode=MTLStorageModeShared;
    id<MTLTexture> out=[d newTextureWithDescriptor:td];
    MTLSamplerDescriptor *sd=[MTLSamplerDescriptor new];
    sd.minFilter=sd.magFilter=MTLSamplerMinMagFilterLinear;
    sd.sAddressMode=sd.tAddressMode=MTLSamplerAddressModeRepeat;
    id<MTLSamplerState> smp=[d newSamplerStateWithDescriptor:sd];
    char *patched=daynight_sky_patch(source,1); assert(patched);
    // Frozen primary-view WVP and scroll, independently recorded at midnight.
    const char *ray="        float2 xy = pi.pos.xy / float2(1144.0f,720.0f) * float2(2.0f,-2.0f) + float2(-1.0f,1.0f);\n"
        "        float3 r0=float3(-0.642004f,0.813899f,-0.000131902f), r1=float3(0.0208433f,0.0166848f,1.50322f), r3=float3(-0.785017f,-0.61922f,0.0177579f);\n"
        "        float3 dv=cross(r0-xy.x*r3,r1-xy.y*r3); if(dv.z<0)dv=-dv;\n"
        "        float dl=length(dv);\n";
    const char *old="        float3 dv = pi.texcoord4.xyz; float dl = length(dv);\n";
    char *r=splice(patched,strstr(patched,old),strlen(old),ray); free(patched);
    char *s=splice(r,strstr(r,"        float g ="),0,"        r.c0=float4(a,a,a,1.0f); return r;\n"); free(r);
    // All other arithmetic is production; only the observed output is density.
    r=splice(s,strstr(s,"pi.texcoord4.w"),strlen("pi.texcoord4.w"),"0.399337f"); free(s);
    NSError *error=nil;
    id<MTLLibrary> lib=[d newLibraryWithSource:[NSString stringWithFormat:@"%s%s",r,vertex_ray] options:nil error:&error]; free(r);
    if(!lib) { fprintf(stderr,"FAIL density shader: %s\n",error.description.UTF8String); return 1; }
    MTLRenderPipelineDescriptor *pd=[MTLRenderPipelineDescriptor new];
    pd.vertexFunction=[lib newFunctionWithName:@"dn_vertex"]; pd.fragmentFunction=[lib newFunctionWithName:@"sm_main"];
    pd.colorAttachments[0].pixelFormat=MTLPixelFormatRGBA32Float;
    id<MTLRenderPipelineState> pipe=[d newRenderPipelineStateWithDescriptor:pd error:&error]; assert(pipe);
    double means[2];
    for(int w=0;w<=1;w++) {
        float c[4]={w?1.0f:.333333f,w?.8f:.02f,w?.8f:.01f,0};
        float k[8]={1,.02f,.04f,.08f,20000,60000,45000,3.2f};
        MTLRenderPassDescriptor *rp=[MTLRenderPassDescriptor renderPassDescriptor];
        rp.colorAttachments[0].texture=out; rp.colorAttachments[0].loadAction=MTLLoadActionClear; rp.colorAttachments[0].storeAction=MTLStoreActionStore;
        id<MTLCommandBuffer> cb=[q commandBuffer]; id<MTLRenderCommandEncoder> enc=[cb renderCommandEncoderWithDescriptor:rp];
        [enc setRenderPipelineState:pipe]; [enc setFragmentBytes:c length:16 atIndex:0]; [enc setFragmentBytes:k length:32 atIndex:6];
        [enc setFragmentTexture:cloud atIndex:14]; [enc setFragmentSamplerState:smp atIndex:14];
        [enc drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3]; [enc endEncoding]; [cb commit]; [cb waitUntilCompleted]; assert(!cb.error);
        float pixels[180*80*4];
        [out getBytes:pixels bytesPerRow:180*16 fromRegion:MTLRegionMake2D(700,24,180,80) mipmapLevel:0];
        double sum=0; for(int i=0;i<180*80;i++)sum+=pixels[i*4]; means[w]=sum/(180*80);
    }
    printf("density: w=0 %.9f w=1 %.9f pixels=14400 limits=0.05/0.6\n",means[0],means[1]); fflush(stdout);
    return means[0]<=.05 && means[1]>=.6 ? 0 : 1;
} }
'''


def run_density():
    include = ROOT / 'recomp/out/daynight_sky_private.inc'
    if not include.is_file():
        print('SKIP density: private texture unavailable')
        return []
    source = (RUNTIME / 'daynight_sky.c').read_text()
    old = 'saturate(c[0].z * 1.25f)'
    assert old in source
    proof = []
    with tempfile.TemporaryDirectory(prefix='daynight-density-') as temp:
        temp = Path(temp)
        harness = temp / 'density.m'
        harness.write_text(GPU_HARNESS[:GPU_HARNESS.index('int main(void)')] + DENSITY_MAIN)
        for label, code in [('production', source), ('fixed-cloud-cover', source.replace(old, '1.0f'))]:
            test_source = temp / 'sky.c'
            test_source.write_text(code)
            exe = temp / label
            subprocess.run(['clang', '-fobjc-arc', '-DNFS_PUBLIC=1', f'-DSKY_SOURCE="{test_source}"',
                '-I', str(RUNTIME), '-I', str(ROOT / 'recomp'), '-I', str(include.parent),
                str(harness), str(RUNTIME / 'daynight.c'), '-framework', 'Metal', '-framework', 'Foundation',
                '-o', str(exe)], check=True, capture_output=True, text=True)
            result = subprocess.run([exe], capture_output=True, text=True)
            assert result.returncode == (0 if label == 'production' else 1), result.stdout + result.stderr
            line = label + ': ' + result.stdout.strip()
            print(line)
            proof.append(line)
    return proof


# Original lunar contribution only: pinned independently of production, rather
# than reading git history or copying a whole shader into the test.
ORIGINAL_LUNAR = 'face * pow(glow, 1.7f) * dn[1].w'
ORIGINAL_LUNAR_SHA256 = 'f2fe0ac519b9513d64b1975934dd71a2abe5a6bc898842788fee65f4fc61f79d'


def run_moon():
    include = ROOT / 'recomp/out/daynight_sky_private.inc'
    if not include.is_file():
        print('SKIP moon: private texture unavailable')
        return []
    assert hashlib.sha256(ORIGINAL_LUNAR.encode()).hexdigest() == ORIGINAL_LUNAR_SHA256
    source = (RUNTIME / 'daynight_sky.c').read_text()
    # Undo just the clear-moon repair; cloud density and the shoulder survive.
    import re
    broken, count = re.subn(r'float lunar = .*?;\\n',
                           lambda _: 'float lunar = ' + ORIGINAL_LUNAR + ';\\n', source)
    assert count == 1 and broken != source
    # Reuse the texture upload and renderer from the density fixture, but run
    # the actual colour output on a recorded primary-view moon neighbourhood.
    harness = DENSITY_MAIN.replace('1144', '700').replace('720', '600')
    harness = harness.replace('pi.pos.xy / float2(700.0f,600.0f)',
                              '(pi.pos.xy + float2(1300.0f,200.0f)) / float2(3440.0f,2160.0f)')
    harness = harness.replace('float3(-0.642004f,0.813899f,-0.000131902f)',
                              'float3(-0.362796f,0.968497f,0.00152793f)')
    harness = harness.replace('float3(0.0208433f,0.0166848f,1.50322f)',
                              'float3(0.12923f,0.0460473f,1.49718f)')
    harness = harness.replace('float3(-0.785017f,-0.61922f,0.0177579f)',
                              'float3(-0.9325f,-0.349455f,0.0912373f)')
    harness = harness.replace('20000,60000,45000,3.2f',
                              '-68996.923886f,-29860.562732f,36711.887806f,3.2f')
    harness = harness.replace('r.c0=float4(a,a,a,1.0f); return r;',
                              '/* colour output retained */')
    harness = harness.replace('    NSError *error=nil;', '''    s=splice(r,strstr(r,"        r.c0.rgb = mix"),0,"        r.c0.a = rho;\\n"); free(r); r=s;
    NSError *error=nil;''')
    harness = harness.replace('    double means[2];', '    double means[2]; int clear_pass=0;')
    start = harness.index('        float pixels[180*80*4];')
    harness = harness[:start] + r'''
        float *pixels=malloc(700*600*4*sizeof(float)); assert(pixels);
        [out getBytes:pixels bytesPerRow:700*16 fromRegion:MTLRegionMake2D(0,0,700,600) mipmapLevel:0];
        if(w) {
            FILE *f=fopen(getenv("MOON_OVERCAST"),"wb"); assert(f);
            assert(fwrite(pixels,sizeof(float),700*600*4,f)==700*600*4); fclose(f);
        }
        double ring=0, face=0, face2=0, inner=0, outer=0;
        unsigned nr=0,nf=0,ni=0,no=0;
        for(unsigned i=0;i<700*600;i++) {
            float *p=pixels+i*4; double rho=p[3], y=255*(.2126*p[0]+.7152*p[1]+.0722*p[2]);
            if(rho>=1.5 && rho<2.5) { ring+=y; ++nr; }
            if(rho<.7) { face+=y; face2+=y*y; ++nf; }
            if(rho>=.85 && rho<.95) { inner+=y; ++ni; }
            if(rho>=1.05 && rho<1.15) { outer+=y; ++no; }
        }
        assert(nr && nf && ni && no);
        double sd=sqrt(fmax(0,face2/nf-pow(face/nf,2))), edge=inner/ni-outer/no;
        printf("moon w=%d ring=%.6f face_SD=%.6f edge=%.6f pixels=%u/%u/%u/%u\n",w,ring/nr,sd,edge,nr,nf,ni,no); fflush(stdout);
        means[w]=ring/nr;
        if(!w) clear_pass=means[0]<=60 && sd>=15 && edge>=19;
        free(pixels);
    }
    return clear_pass ? 0 : 1;
} }
'''
    proof = []
    with tempfile.TemporaryDirectory(prefix='daynight-moon-') as temp:
        temp = Path(temp)
        main = temp / 'moon.m'
        main.write_text(GPU_HARNESS[:GPU_HARNESS.index('int main(void)')] + harness)
        frames = []
        for label, code in [('production', source), ('original-glow', broken)]:
            test_source = temp / 'sky.c'
            test_source.write_text(code)
            exe = temp / label
            subprocess.run(['clang', '-fobjc-arc', '-DNFS_PUBLIC=1', f'-DSKY_SOURCE="{test_source}"',
                '-I', str(RUNTIME), '-I', str(ROOT / 'recomp'), '-I', str(include.parent),
                str(main), str(RUNTIME / 'daynight.c'), '-framework', 'Metal', '-framework', 'Foundation',
                '-o', str(exe)], check=True, capture_output=True, text=True)
            frame = temp / (label + '.rgba')
            env = dict(os.environ, MOON_OVERCAST=str(frame))
            result = subprocess.run([exe], capture_output=True, text=True, env=env)
            assert result.returncode == (1 if label == 'original-glow' else 0), result.stdout + result.stderr
            print(label + ': ' + result.stdout.strip())
            proof.append(label + ': ' + result.stdout.strip())
            frames.append(frame.read_bytes())
        from array import array
        left, right = array('f'), array('f')
        left.frombytes(frames[0]); right.frombytes(frames[1])
        delta = max(abs(a-b)*255 for i, (a,b) in enumerate(zip(left, right)) if i % 4 != 3)
        assert delta <= 2, f'overcast colour delta {delta} exceeds 2/255'
        proof.append(f'PASS moon overcast: max colour delta {delta:.9f}/255 (420000 pixels, limit 2)')
        print(proof[-1])
    return proof


def run_math():
    proof = []
    source = (RUNTIME / 'daynight_sky.c').read_text()
    assert hashlib.sha256(ORIGINAL_MOON_RULE.encode()).hexdigest() == '7a790d2f63d4ee554e90f4b2b0f9684df9f5e5ab754646f9e2b30434170c1cde'
    start = source.index('    // Rounding can pin phase')
    end = source.index('    float k = (day ?', start)
    flip = (source[:start] + ORIGINAL_MOON_RULE + source[end:]).replace(
        'const float light[3], float azimuth, float out[3]',
        'const float light[3], float tilt, float out[3]')
    no_suppression = source.replace('sky_night() > .001f', 'sky_night() > 1.0f')
    screen_unsuppressed = source.replace('if (sky_night() > .001f) { c->esp += 8; return; }',
                                         'if (sky_night() > 1.0f) { c->esp += 8; return; }')
    assert screen_unsuppressed != source
    no_bearing = source.replace('> .996194698f', '> 1.0f')
    assert flip != source and no_suppression != source
    assert '00503830    daynight_sky.c:' in (ROOT / 'recomp/hooks.txt').read_text()
    assert '006D04E0    daynight_sky.c:' in (ROOT / 'recomp/hooks.txt').read_text()
    with tempfile.TemporaryDirectory(prefix='daynight-sky-') as temp:
        temp = Path(temp)
        harness = temp / 'check.c'
        for label, code in [('production', source), ('restored-flip', flip), ('solar-fade-off', no_suppression), ('screen-sun-on', screen_unsuppressed), ('broken-bearing', no_bearing)]:
            harness.write_text(HARNESS.replace('s.phase,s.light,azimuth,m', 's.phase,s.light,tilt,m')
                               if label == 'restored-flip' else HARNESS)
            test_source = temp / 'sky.c'
            test_source.write_text(code)
            exe = temp / label
            cmd = ['clang', '-std=c11', '-g', '-fsanitize=address,undefined', '-DDN_SKY_CORE_ONLY=1',
                   f'-DSKY_SOURCE="{test_source}"', '-I', str(RUNTIME), '-I', str(ROOT / 'recomp'),
                   str(harness), str(RUNTIME / 'daynight.c'), '-lm', '-o', str(exe)]
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            result = subprocess.run([exe], capture_output=True, text=True)
            if label == 'production':
                assert result.returncode == 0, result.stdout + result.stderr
                print(result.stdout.strip())
                proof.append(result.stdout.strip())
            else:
                assert result.returncode != 0, label + ' control was accepted'
                failure = {'restored-flip': 'FAIL moon bearing', 'solar-fade-off': 'sun_producers==',
                           'screen-sun-on': 'screen_sun_producers==', 'broken-bearing': 'moon_flare_polys[type]==1'}[label]
                assert failure in result.stderr
                line = 'PASS rejected ' + label + ': ' + (result.stdout.strip() or result.stderr.splitlines()[0])
                print(line)
                proof.append(line)
    return proof


def run_gpu():
    proof = []
    source = (RUNTIME / 'daynight_sky.c').read_text()
    with tempfile.TemporaryDirectory(prefix='daynight-sky-metal-') as temp:
        temp = Path(temp)
        harness = temp / 'check.m'
        harness.write_text(GPU_HARNESS)
        broken = source.replace('mix(r.c0.rgb, night, dn[0].x);', 'mix(r.c0.rgb, night, 0.0f);')
        assert broken != source
        cloud = source.replace('r.c0.a *= 1.0f - dn[0].x;', 'r.c0.a *= 1.0f;')
        assert cloud != source
        # The fault this check exists for: cloud density read from the moon's plane.
        green = source.replace('* cl.a * saturate(', '* cl.g * saturate(')
        assert green != source
        recorder = source.replace('attempted = 1;', 'attempted = 1; device = [(id)encoder device];')
        assert recorder != source
        full_gain = source.replace('3.2f * night.sky[1] / 0.04f', '.5f * night.sky[1] / 0.04f')
        assert full_gain != source
        no_shoulder = source.replace('if (peak > 0.7f)', 'if (peak > 1e30f)')
        assert no_shoulder != source
        fixed_cover = source.replace('saturate(c[0].z * 1.25f)', '1.0f')
        assert fixed_cover != source
        for label, code in [('no-shoulder', no_shoulder), ('production', source), ('missing-texture', source), ('wrong-texture', source), ('missing-blend', broken), ('green-channel-clouds', green), ('constant-cloud-alpha', cloud), ('recorder-device', recorder), ('previous-emission', full_gain), ('fixed-cloud-cover', fixed_cover)]:
            test_source = temp / 'sky.c'
            test_source.write_text(code)
            exe = temp / label
            cmd = ['clang', '-fobjc-arc', '-DNFS_PUBLIC=1', f'-DSKY_SOURCE="{test_source}"',
                   '-I', str(RUNTIME), '-I', str(ROOT / 'recomp'), str(harness),
                   str(RUNTIME / 'daynight.c'), '-framework', 'Metal', '-framework', 'Foundation', '-o', str(exe)]
            if label != 'missing-texture':
                cmd.append('-DTEST_SKY_BIND=1')
            if label == 'wrong-texture':
                cmd.append('-DTEST_SKY_WRONG_TEXTURE=1')
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            result = subprocess.run([exe], capture_output=True, text=True)
            if label in ('production', 'missing-texture', 'wrong-texture'):
                assert result.returncode == 0, result.stdout + result.stderr
                print(result.stdout.strip())
                proof.append(result.stdout.strip())
            else:
                assert result.returncode != 0, label + ' control was accepted'
                line = 'PASS rejected ' + label + ': ' + result.stderr.splitlines()[0]
                print(line)
                proof.append(line)
    return proof


FOG_MAIN = r'''
int main(int argc,char **argv) { @autoreleasepool {
    id<MTLDevice> d=MTLCreateSystemDefaultDevice(); assert(d);
    unsigned count=0;
    for(int i=1;i<argc;++i) {
        NSData *bytecode=[NSData dataWithContentsOfFile:@(argv[i])];
        assert(bytecode.length>4 && bytecode.length%4==0);
        SmOptions opt;sm_default_options(&opt);SmResult sm={};
        assert(!sm_translate(bytecode.bytes,bytecode.length/4,&opt,&sm));
        int vertex=sm.shader_type==SM_VERTEX;
        char *sky=NULL;
        if (lighting_mode_d65() && !vertex && sky_pass(sm.program_key))
            sky=daynight_sky_patch(sm.msl,sky_pass(sm.program_key));
        if (lighting_mode_d65() && vertex && sm.program_key==0x43afa2430c786f3dull)
            sky=daynight_sky_patch_vertex(sm.msl);
        char *patched=daynight_fog_shader(sky?sky:sm.msl,bytecode.bytes,bytecode.length/4,vertex);
        if(lighting_mode_d65()) {
            assert(patched && strstr(patched,"dot(") && strstr(patched,"0.2126f, 0.7152f, 0.0722f"));
            NSError *error=nil;
            id<MTLLibrary> library=[d newLibraryWithSource:@(patched) options:nil error:&error];
            if(!library)fprintf(stderr,"%s: %s\n",argv[i],error.localizedDescription.UTF8String);
            assert(library);
        } else assert(!patched);
        free(sky);free(patched);sm_result_free(&sm);++count;
    }
    printf("MW fog/dome: %u program sources %s\n",count,lighting_mode_d65()?"patched and compiled":"passed through");
    return 0;
} }
'''


def run_fog():
    shaders = ROOT/'host/shaders'
    archive = shaders/'build/libsmtranslate.a'
    if not archive.is_file():
        return []
    corpus = shaders/'testdata'
    vertices = [f for folder in ('world', 'tree', 'worldnormalmap', 'worldreflect', 'glossywindow')
                for f in sorted((corpus/folder).glob('*vs*.bin'))
                if f.name != 't6_worldnormalmap_1_1_p0_vs_1_1.bin' or folder == 'worldnormalmap']
    programs = vertices + sorted(f for f in (corpus/'skybox').glob('*.bin') if '_p1_vs_' not in f.name)
    assert len(programs) == 15
    with tempfile.TemporaryDirectory(prefix='daynight-fog-metal-') as temp:
        path = Path(temp)
        harness = path/'fog.m'
        harness.write_text(GPU_HARNESS[:GPU_HARNESS.index('int main(void)')] + FOG_MAIN)
        binary = path/'fog'
        subprocess.run(['clang', '-fobjc-arc', '-DNFS_PUBLIC=1',
                        f'-DSKY_SOURCE="{RUNTIME/"daynight_sky.c"}"', '-I', RUNTIME,
                        '-I', ROOT/'recomp', harness, RUNTIME/'daynight.c', archive, '-lc++',
                        '-framework', 'Metal', '-framework', 'Foundation', '-o', binary],
                       capture_output=True, check=True)
        results = []
        for profile in ('', 'neutral-d65'):
            result = subprocess.run([binary, *programs], capture_output=True, text=True,
                                    env={**os.environ, 'NFS_WORLD_LIGHTING': profile})
            assert result.returncode == 0, result.stdout + result.stderr
            results.append(result.stdout.strip())
            print(results[-1])
        return results


def check_moon_frames(clear, overcast, baseline, control):
    """Full-chain held PNG proof, using the independently recorded projection."""
    import numpy as np
    from PIL import Image
    matrix = np.array([[-.362796, .968497, .00152793],
                       [.12923, .0460473, 1.49718],
                       [-.9325, -.349455, .0912373]])
    moon = np.array([-68996.923886, -29860.562732, 36711.887806])
    y, x = np.mgrid[200:800, 1300:2000]
    ndc = np.stack([2*(x+.5)/3440-1, 1-2*(y+.5)/2160, np.ones_like(x)], axis=-1)
    view = np.einsum('...j,ij->...i', ndc, np.linalg.inv(matrix))
    view /= np.linalg.norm(view, axis=-1, keepdims=True)
    view[:, :, 2] = np.maximum(view[:, :, 2], 0) + .0001
    view /= np.linalg.norm(view, axis=-1, keepdims=True)
    dome = view / np.sqrt((view[:, :, 0]**2 + view[:, :, 1]**2)/12926.33**2
                          + view[:, :, 2]**2/7000**2)[:, :, None]
    to_moon = moon - dome
    distance = np.linalg.norm(to_moon, axis=-1)
    direction = to_moon / distance[:, :, None]
    tangent = np.cross(direction, [0, .0995037, .995037])
    tangent /= np.linalg.norm(tangent, axis=-1, keepdims=True)
    bitangent = np.cross(direction, tangent)
    offset = dome + view*(distance - np.sum(view*direction, axis=-1))[:, :, None] - moon
    uv = (np.stack([np.sum(tangent*offset, axis=-1)*1.21,
                    np.sum(bitangent*offset, axis=-1)], axis=-1) + 3200)*.00015625
    radius = np.linalg.norm(2*(uv-.5), axis=-1)
    ring = (radius >= 1.5) & (radius < 2.5)
    face = radius < .7
    inner = (radius >= .85) & (radius < .95)
    outer = (radius >= 1.05) & (radius < 1.15)
    weights = [.2126, .7152, .0722]

    def image(path):
        im = Image.open(path).convert('RGB')
        assert im.size == (3440, 2160), 'requires the recorded full-detail capture size'
        return im

    def measure(path):
        rgb = np.asarray(image(path).crop((1300, 200, 2000, 800)), dtype=float)
        luminance = np.sum(rgb*weights, axis=-1)
        return dict(ring=float(luminance[ring].mean()), face_SD=float(luminance[face].std()),
                    edge=float(luminance[inner].mean()-luminance[outer].mean()))

    result, broken = measure(clear), measure(control)
    # This open-sky box includes the disc; no terrain or trees contribute.
    box = (1500, 340, 1800, 600)
    a = np.asarray(image(overcast).crop(box), dtype=float)
    b = np.asarray(image(baseline).crop(box), dtype=float)
    delta = float(np.abs(np.sum((a-b)*weights, axis=-1)).mean())
    print(json.dumps(dict(clear=result, original_glow=broken, overcast_sky_box=box,
                          overcast_mean_abs_Y_delta=delta), sort_keys=True))
    assert result['ring'] <= 60 and result['face_SD'] >= 15 and result['edge'] >= 19
    assert broken['ring'] > 60, 'original-glow control did not expose the bug'
    assert delta <= 2, 'overcast sky changed by more than two levels'
    print('PASS held final moon, rejected original glow, preserved overcast')


def check_game_log(path, hour):
    import re
    rows = re.findall(r'daynight_sky: frame=(\d+) n=([\d.]+) sun_draws=(\d+) sun_polys=(\d+) sky_draws=(\d+) night_draws=(\d+)', Path(path).read_text())
    assert rows, 'no sky diagnostics'
    expected = 1 if hour in (0, 4, 4.5) else 0
    selected = [r for r in rows if abs(float(r[1]) - expected) < 1e-5 and int(r[4]) > 0]
    assert selected, 'no fixed-hour sky draw diagnostics'
    if hour == 4.5:
        assert len(selected) >= 104, f'need 104 fixed-night samples, got {len(selected)}'
        selected = selected[:104]
        solar = sum(int(r[2]) > 0 for r in selected)
        print(f'hour=4.5: solar draws in {solar}/104 frames ({selected[0][0]}..{selected[-1][0]})')
        assert solar == 0 and all(int(r[5]) > 0 for r in selected), 'all-view solar draws must be zero and night texture draws positive'
    elif hour in (0, 4):
        assert all(int(r[2]) == 0 and int(r[5]) > 0 for r in selected), 'night must have zero solar draws and positive night texture draws'
    else:
        assert all(int(r[5]) == 0 for r in selected), 'noon must use the midday sky'
    print(f'PASS hour={hour}: {len(selected)} frames; sun={sorted({int(r[2]) for r in selected})} sky={sorted({int(r[4]) for r in selected})} night={sorted({int(r[5]) for r in selected})}')


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--generate':
        generate(sys.argv[2])
    elif len(sys.argv) == 6 and sys.argv[1] == '--check-moon-frames':
        check_moon_frames(*sys.argv[2:])
    elif len(sys.argv) == 4 and sys.argv[1] == '--check-game-log':
        check_game_log(sys.argv[2], float(sys.argv[3]))
    else:
        proof = run_math() + run_gpu() + run_density() + run_moon() + run_fog()
        output = ROOT / '.metal-cache/daynight-sky-proof.txt'
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text('\n'.join(proof) + '\n')
