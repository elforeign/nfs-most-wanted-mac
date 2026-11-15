#include "atmosphere.h"
#include "host.h"
#include <math.h>
#include <string.h>

AtmosphereConstants atmosphere_constants(void) {
    // Hillaire, Production Ready Atmosphere Rendering, EGSR 2020, Table 1:
    // https://sebh.github.io/publications/egsr2020.pdf
    // Reference SetupEarthAtmosphere, coefficients and g (lines 29-35):
    // https://github.com/sebh/UnrealEngineSkyAtmosphere/blob/master/Application/SkyAtmosphereCommon.cpp
    // Sea-level Rayleigh scattering RGB (5.802,13.558,33.1) / 10^6 m;
    // aerosol scattering 3.996 / 10^6 m, absorption .444 / 10^6 m, g=.8.
    // These are a reference clear-air model, not measured local weather.
    // One game world unit is one metre: MPS2KPH, decomp/src/speed_0050d6c0.c
    // :863-872 (0050de10, velocity *3.6); no centimetre/kilometre factor here.
    return (AtmosphereConstants){.rayleigh={5.802e-6f,13.558e-6f,33.1e-6f},
        .mie_scattering={3.996e-6f,3.996e-6f,3.996e-6f},
        .mie_extinction={4.440e-6f,4.440e-6f,4.440e-6f},.hg_g=.8f};
}
static int unit(const float in[3],float out[3]) {
    double norm=0;
    for (int c=0;c<3;++c) { if (!isfinite(in[c])) return 0;norm+=(double)in[c]*in[c]; }
    if (norm<1e-20) return 0;
    for (int c=0;c<3;++c) out[c]=(float)(in[c]/sqrt(norm));
    return 1;
}
int atmosphere_update(AtmosphereLUT *lut,const AtmosphereInput *input) {
    if (!lut) return 0;
    AtmosphereInput key={0};
    if (!input || !unit(input->sun,key.sun) || !isfinite(input->fog_distance) || input->fog_distance<=0) {
        lut->valid=0;return 0;
    }
    key.fog_distance=input->fog_distance;
    for (int c=0;c<3;++c) {
        if (!isfinite(input->fog_colour[c]) || input->fog_colour[c]<0) { lut->valid=0;return 0; }
        key.fog_colour[c]=input->fog_colour[c];
    }
    if (lut->valid && !memcmp(&key,&lut->input,sizeof key)) return 1;
    lut->input=key;lut->constants=atmosphere_constants();
    const AtmosphereConstants *k=&lut->constants;
    // Beer-Lambert and the analytic constant-density single-scattering integral:
    // T=exp(-beta_ext*d), L=fogRGB*(beta_R*p_R+beta_Ms*p_HG)/beta_ext*(1-T).
    // PBRT 4e, Volume Scattering / Phase Functions and Transmittance:
    // https://pbr-book.org/4ed/Volume_Scattering/Phase_Functions
    // https://pbr-book.org/4ed/Volume_Scattering/Transmittance
    // mu=dot(eye-to-scene,towards-sun), so the HG forward lobe is at mu=+1
    // (PBRT's both-away convention has the opposite cosine sign).
    for (unsigned d=0;d<ATMOSPHERE_DISTANCES;++d) {
        double distance=(double)key.fog_distance*d/(ATMOSPHERE_DISTANCES-1);
        for (int c=0;c<3;++c) {
            double br=k->rayleigh[c];
#ifdef ATMOSPHERE_TEST_SWAPPED
            br=k->mie_scattering[c];
#endif
            double extinction=br+k->mie_extinction[c];
            double lost=-expm1(-extinction*distance);
            lut->transmittance[d][c]=(float)exp(-extinction*distance);
            lut->scatter_integral[d][c]=(float)(lost/extinction);
        }
    }
    ++lut->builds;lut->valid=1;return 1;
}
int atmosphere_sample(const AtmosphereLUT *lut,float distance,const float view[3],float t[3],float s[3]) {
    float v[3];
    if (!lut || !lut->valid || !isfinite(distance) || !unit(view,v)) return 0;
    double mu=0;for (int c=0;c<3;++c) mu+=(double)v[c]*lut->input.sun[c];
    double da=fmin(1,fmax(0,distance/lut->input.fog_distance))*(ATMOSPHERE_DISTANCES-1);
    unsigned d=(unsigned)da,d1=d+1<ATMOSPHERE_DISTANCES?d+1:d;
    double df=da-d;
    const double pi=3.14159265358979323846,g=lut->constants.hg_g;
    mu=fmin(1,fmax(-1,mu));
    // HG's forward peak is too sharp for a uniformly spaced angular table.
    // Cache only the radial integral and evaluate both phases at the actual mu.
    double rayleigh=3.0/(16*pi)*(1+mu*mu);
    double denom=1+g*g-2*g*mu;
    double hg=(1-g*g)/(4*pi*denom*sqrt(denom));
    for (int c=0;c<3;++c) {
        t[c]=(float)(lut->transmittance[d][c]*(1-df)+lut->transmittance[d1][c]*df);
        double br=lut->constants.rayleigh[c],bm=lut->constants.mie_scattering[c];
#ifdef ATMOSPHERE_TEST_SWAPPED
        double swap=br;br=bm;bm=swap;
#endif
        double integral=lut->scatter_integral[d][c]*(1-df)+lut->scatter_integral[d1][c]*df;
        s[c]=(float)(lut->input.fog_colour[c]*(br*rayleigh+bm*hg)*integral);
    }
    return 1;
}
int atmosphere_read_game(AtmosphereInput *input,uint32_t fog_record) {
    if (!input || !G) return 0;
    // TimeOfDayPointer is 009b392c (asm/speed_text.asm:1165917).
    // Sun: FUN_006c6450, decomp/src/speed_006c5350.c:949-953 normalizes
    // TOD+180/184/188; 006e44c0, speed_006d6820.c:11977 uses it towards sun.
    uint32_t tod=M32(0x009B392Cu);if (!tod || tod>0xFFFFFF00u-0x188u) return 0;
    if (!fog_record) fog_record=0x009AE364u;
    if (fog_record>UINT32_MAX-24) return 0;
    // SP calls FUN_00753d90 with ECX=009ae364 (asm:1165920-1165923).
    // Evaluated position/weather fog: speed_0074d050.c:5641-5655 (FogStart,
    // falloffs, packed RGB), overrides :5656-5667, rain blending :5711-5730.
    // The decompiler mis-types param[5] as float: asm/speed_text.asm:1353732
    // (00753f5e) MOV [esi+14],edi proves it is packed RGB bits, not numeric float.
    // FogStart is a sampling extent, NOT meteorological visibility: we do not
    // infer aerosol density from an artistic distance or double-apply game fog.
    uint32_t rgb=M32(fog_record+20);
    *input=(AtmosphereInput){.fog_distance=MF32(fog_record),
        .fog_colour={(rgb>>16&255)/255.f,(rgb>>8&255)/255.f,(rgb&255)/255.f}};
    for (int c=0;c<3;++c) input->sun[c]=MF32(tod+0x180+4*c);
    float normalized[3];
    return unit(input->sun,normalized) && isfinite(input->fog_distance) && input->fog_distance>0;
}
