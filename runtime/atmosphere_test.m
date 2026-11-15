// Hand references at d=1000m, white source, sun elevations 90 and 30 degrees.
// T=exp[-(beta_R+4.440e-6)*1000]; S=(1-T)*(beta_R*pR+3.996e-6*pHG)/ext.
// At mu=1: pR=.1193662073189215, pHG=3.580986219567645;
// at mu=.5: pR=.07460387957432594, pHG=.03721120275229589.
// Original angular table: 48,452 checks / 7,139 failures (max error 31.238194%).
// Analytic phase: 48,452 / 0 (max 0.000167332%); swapped: 48,452 / 35,822.
#include "atmosphere.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
uint8_t *G;
static unsigned checks,failures;
#define CHECK(x) do { ++checks;if (!(x)) {++failures;if(failures<8)fprintf(stderr,"FAIL %d: %s\n",__LINE__,#x);} } while(0)
static void put(uint32_t a,float f) { memcpy(G+a,&f,4); }
static void check_angles(AtmosphereLUT *lut) {
    const AtmosphereInput in={.sun={0,0,1},.fog_colour={1,1,1},.fog_distance=1000};
    const double br[3]={5.802e-6,13.558e-6,33.1e-6},pi=acos(-1.0),g=.8;
    double worst=0;
    CHECK(atmosphere_update(lut,&in));
    // Include the reported off-grid point, uniform off-grid samples, and a
    // denser forward lobe. Endpoints must also satisfy the analytic reference.
    for (unsigned i=0;i<12004;++i) {
        double mu=i==0?.98374:i==1?-1:i==2?1:
            i<10003?-1+2*(i-2.63)/10000: .95+.05*(i-10002.37)/2001;
        float view[3]={(float)sqrt(fmax(0,1-mu*mu)),0,(float)mu},t[3],s[3];
        CHECK(atmosphere_sample(lut,1000,view,t,s));
        double pr=3*(1+mu*mu)/(16*pi),den=1+g*g-2*g*mu;
        double pm=(1-g*g)/(4*pi*pow(den,1.5));
        for (unsigned c=0;c<3;++c) {
            double ext=br[c]+4.440e-6;
            double expected=-expm1(-ext*1000)*(br[c]*pr+3.996e-6*pm)/ext;
            double relative=fabs(s[c]/expected-1);worst=fmax(worst,relative);
            CHECK(relative<=.01);
        }
    }
    printf("off-grid angles: samples=12004 max_relative=%.9g\n",worst);
}
int main(void) {
    AtmosphereLUT *lut=calloc(1,sizeof *lut);
    AtmosphereInput in={.sun={0,0,1},.fog_colour={1,1,1},.fog_distance=1000};
    const double trans[3]={.98981027067770155,.98216299668232976,.96315589071465819};
    const double scatter[2][3]={{.01492561910007246,.01578550807336037,.01792213915945187},
                               {.0005785797111148985,.0011497973020842383,.002569552131565234}};
    const float view[3]={0,0,1};float t[3],s[3];
    for (unsigned sun=0;sun<2;++sun) {
        if (sun) { in.sun[0]=sqrtf(.75f);in.sun[2]=.5f; }
        CHECK(atmosphere_update(lut,&in));CHECK(lut->builds==sun+1);
        CHECK(atmosphere_sample(lut,1000,view,t,s));
        for (unsigned c=0;c<3;++c) {
            CHECK(fabs(t[c]-trans[c])<7e-8);
            CHECK(fabs(s[c]-scatter[sun][c])<scatter[sun][c]*1e-6);
        }
        printf("sun=%u T=%.9g,%.9g,%.9g S=%.9g,%.9g,%.9g\n",sun,t[0],t[1],t[2],s[0],s[1],s[2]);
        CHECK(atmosphere_update(lut,&in));CHECK(lut->builds==sun+1);
    }
    CHECK(atmosphere_sample(lut,0,view,t,s));
    for (int c=0;c<3;++c) CHECK(t[c]==1 && s[c]==0);
    for (unsigned d=1;d<ATMOSPHERE_DISTANCES;++d) for (int c=0;c<3;++c) {
        CHECK(lut->transmittance[d][c]<=lut->transmittance[d-1][c]);
        CHECK(lut->transmittance[d][c]>=0 && lut->transmittance[d][c]<=1);
    }
    in.fog_distance=2000;CHECK(atmosphere_update(lut,&in));CHECK(lut->builds==3);
    in.fog_colour[0]=.5f;CHECK(atmosphere_update(lut,&in));CHECK(lut->builds==4);
    CHECK(atmosphere_sample(lut,2000,view,t,s));float red=s[0];
    in.fog_colour[0]=1;CHECK(atmosphere_update(lut,&in));
    CHECK(atmosphere_sample(lut,2000,view,t,s));CHECK(s[0]==2*red);
    CHECK(atmosphere_sample(lut,4000,view,t,s));CHECK(s[0]==2*red);
    in.sun[0]=in.sun[1]=0;in.sun[2]=2;CHECK(atmosphere_update(lut,&in));
    uint64_t builds=lut->builds;in.sun[2]=1;CHECK(atmosphere_update(lut,&in));CHECK(lut->builds==builds);
    in.fog_distance=NAN;CHECK(!atmosphere_update(lut,&in));CHECK(!lut->valid);
    in.fog_distance=0;CHECK(!atmosphere_update(lut,&in));
    in.fog_distance=1000;in.sun[2]=0;CHECK(!atmosphere_update(lut,&in));
    in.sun[2]=1;in.fog_colour[1]=-1;CHECK(!atmosphere_update(lut,&in));
    in.fog_colour[1]=0;CHECK(atmosphere_update(lut,&in));
    CHECK(atmosphere_sample(lut,1000,view,t,s));CHECK(s[1]==0);
    CHECK(!atmosphere_sample(lut,NAN,view,t,s));
    // Fake guest uses the actual live layouts, with deliberately non-float RGB.
    G=calloc(1,0x1000000);uint32_t tod=0x1000,rgb=0x80C86432;
    memcpy(G+0x9B392C,&tod,4);put(tod+0x180,0);put(tod+0x184,0);put(tod+0x188,2);
    put(0x9AE364,700);memcpy(G+0x9AE364+20,&rgb,4);
    AtmosphereInput live={0};CHECK(atmosphere_read_game(&live,0));
    CHECK(live.sun[2]==2 && live.fog_distance==700);
    CHECK(live.fog_colour[0]==200/255.f && live.fog_colour[1]==100/255.f && live.fog_colour[2]==50/255.f);
    CHECK(atmosphere_update(lut,&live));CHECK(lut->input.sun[2]==1);
    put(0x2000,250);rgb=0x80204060;memcpy(G+0x2014,&rgb,4);
    CHECK(atmosphere_read_game(&live,0x2000));CHECK(live.fog_distance==250 && live.fog_colour[2]==96/255.f);
    put(0x2000,-1);CHECK(!atmosphere_read_game(&live,0x2000));
    put(tod+0x188,0);CHECK(!atmosphere_read_game(&live,0));
    check_angles(lut);
    free(G);free(lut);
    printf("atmosphere: checks=%u failures=%u\n",checks,failures);return failures?1:0;
}
