#include "car_audio_tmx.h"
#include <math.h>
#include <string.h>

static uint32_t u32(const uint8_t *p) {
    return (uint32_t)p[0]|(uint32_t)p[1]<<8|(uint32_t)p[2]<<16|(uint32_t)p[3]<<24;
}
static uint32_t be32(const uint8_t *p) {
    return (uint32_t)p[3]|(uint32_t)p[2]<<8|(uint32_t)p[1]<<16|(uint32_t)p[0]<<24;
}
static float f32(const uint8_t *p) { uint32_t u=u32(p); float f; memcpy(&f,&u,4); return f; }
static int inside(size_t n,size_t at,size_t size) { return at<=n && size<=n-at; }
static int zero(const uint8_t *p,size_t n) { for(size_t i=0;i<n;i++) if(p[i]) return 0; return 1; }
int car_audio_tmx_parse(CarAudioTmx *out,const uint8_t *p,size_t n) {
    if(!out || !p || n<96 || n>16*1024*1024 || memcmp(p+12,"1000",4) ||
       u32(p+16)!=4 || u32(p+20)!=4 || u32(p+24) ||
       (u32(p+40)!=4 && u32(p+40)!=6) || u32(p+48)!=2) return -1;
    uint32_t offsets[10];
    for(unsigned i=0;i<10;i++) {
        offsets[i]=u32(p+56+i*4);
        if(offsets[i]>n || (i && offsets[i]<offsets[i-1])) return -1;
    }
    if(offsets[0]!=96 || offsets[1]!=160 || !inside(n,96,64) ||
       memcmp(p,p+96,12) || memcmp(p,p+128,12) || u32(p+108) || u32(p+112)!=5 ||
       u32(p+140)!=1 || u32(p+144)!=4 || offsets[8]+36*u32(p+40)!=offsets[9]) return -1;
    CarAudioTmx t={0}; t.track_count=u32(p+40); size_t at=offsets[1];
    for(unsigned i=0;i<4;i++) {
        if(!inside(n,at,44)) return -1;
        CarAudioTmxControl *c=t.controls+i;
        c->curve=u32(p+at+4); c->input=u32(p+at+12);
        c->low=f32(p+at+24); c->range=f32(p+at+28);
        c->minimum=f32(p+at+32); c->maximum=f32(p+at+36);
        if(u32(p+at)!=i || c->curve>1 || c->input>=5 || u32(p+at+8) ||
           u32(p+at+16) || u32(p+at+20) || u32(p+at+40) ||
           !isfinite(c->low) || !isfinite(c->range) || !isfinite(c->minimum) || !isfinite(c->maximum) ||
           c->range<=0 || c->minimum<0 || c->maximum>1 || c->minimum>c->maximum) return -1;
        at+=44;
        if(i&1) { if(!inside(n,at,16) || !zero(p+at,16)) return -1; at+=16; }
    }
    if(at!=offsets[2]) return -1;
    for(unsigned i=0;i<4;i++) {
        if(!inside(n,at,28)) return -1;
        CarAudioTmxBin *b=t.bins+i;
        b->count=u32(p+at+12); b->input=u32(p+at+16); b->pairs=u32(p+at+4);
        if(u32(p+at)!=i || !b->pairs || b->pairs>3 || b->count>4 ||
           u32(p+at+8)!=28+b->count*4 || b->input>=5 || u32(p+at+20) || u32(p+at+24) ||
           !inside(n,at,28+b->count*4+64*b->pairs)) return -1;
        for(unsigned j=0;j<b->count;j++) {
            b->dependencies[j]=u32(p+at+28+j*4); if(b->dependencies[j]>=4) return -1;
            for(unsigned k=0;k<j;k++) if(b->dependencies[k]==b->dependencies[j]) return -1;
        }
        at+=28+b->count*4;
        for(unsigned j=0;j<2*b->pairs;j++,at+=32) {
            CarAudioTmxEnd *e=b->ends+j;
            e->track=u32(p+at+4); e->curve=u32(p+at+12);
            e->start=f32(p+at+16); e->width=f32(p+at+20); e->gain=f32(p+at+24);
            if(u32(p+at)!=j || e->track>=t.track_count || e->curve>1 || u32(p+at+8) || u32(p+at+28) ||
               !isfinite(e->start) || !isfinite(e->width) || !isfinite(e->gain) ||
               e->start<0 || e->width<0 || e->gain<0 || e->gain>1) return -1;
        }
        for(unsigned j=0;j<b->pairs;j++)
            if(b->ends[2*j].start>=b->ends[2*j+1].start ||
               b->ends[2*j].track!=b->ends[2*j+1].track) return -1;
    }
    if(at!=offsets[3] || at!=offsets[4] || at!=offsets[5]) return -1;
    uint32_t previous=0;
    for(unsigned i=0;i<t.track_count;i++) {
        at=offsets[8]+i*36;
        if(!inside(n,at,36)) return -1;
        uint32_t kind=u32(p+at),offset=u32(p+at+8);
        uint32_t stop=i+1<t.track_count?u32(p+at+44):(uint32_t)(n-offsets[9]);
        CarAudioTmxTrack *s=t.tracks+i;
        if((kind!=0x47494e20u && kind!=0x534e5220u) || u32(p+at+4)!=i ||
           u32(p+at+12) || u32(p+at+16) || u32(p+at+20)!=1 || u32(p+at+24) ||
           !isfinite(f32(p+at+28)) || f32(p+at+28)<=0 || f32(p+at+28)>20000 ||
           u32(p+at+32) || (i && offset<=previous) ||
           offset>=stop || !inside(n,offsets[9]+offset,stop-offset)) return -1;
        previous=offset; s->offset=offsets[9]+offset; s->size=stop-offset;
        s->gin=kind==0x47494e20u; s->frequency=f32(p+at+28);
        const uint8_t *raw=p+s->offset;
        if(s->gin) {
            t.gin_count++;
            if(s->size<40 || memcmp(raw,"Gnsu20",6)) return -1;
            uint32_t f=u32(raw+16),c=u32(raw+20); s->frames=u32(raw+24); s->rate=u32(raw+28);
            uint64_t bytes=40+4ull*(f+c)+((s->frames+31ull)/32)*19;
            if(!f || f>65536 || !c || c>1048576 || !s->frames || (s->rate!=44100 && s->rate!=48000) ||
               !isfinite(f32(raw+8)) || !isfinite(f32(raw+12)) || f32(raw+8)<=0 || f32(raw+8)>=f32(raw+12) ||
               bytes>s->size || s->size-bytes>3 || !zero(raw+bytes,s->size-(size_t)bytes)) return -1;
            s->size=(uint32_t)bytes;
        } else {
            t.snr_count++;
            if(s->size<20 || be32(raw)!=0x0400bb80 || be32(raw+4)>>30 || !(be32(raw+4)&0x20000000)) return -1;
            s->frames=be32(raw+4)&0x1fffffff; s->loop=be32(raw+8)+1; s->rate=48000;
            if(!s->frames || !s->loop || s->loop>s->frames) return -1;
        }
    }
    *out=t; return 0;
}
/* 00508210: a 512-point cosine table, reversed for the rising curve.
 * Quantize the argument as the original player does; table values round to
 * six decimals. The analytic reconstruction avoids embedding a source table. */
static float curve(float x,unsigned rising) {
    unsigned i=x<=0?0:x>=1?511:(unsigned)(x*511);
    if(rising) i=511-i;
    if(i==511) return 0;
    return roundf(cosf((float)i*(1.5707963267948966f/512))*1000000)*0.000001f;
}
int car_audio_tmx_gains(const CarAudioTmx *t,const float input[5],float out[6]) {
    if(!t || !input || !out) return -1;
    for(unsigned i=0;i<5;i++) if(!isfinite(input[i])) return -1;
    float xf[4],gains[6]={0};
    for(unsigned i=0;i<4;i++) {
        const CarAudioTmxControl *c=t->controls+i;
        float v=(input[c->input]-c->low)/c->range;
        xf[i]=curve(fminf(c->maximum,fmaxf(c->minimum,v)),c->curve);
    }
    /* 00510620: multiply dependency crossfades, then the selected RPM ramp
     * and authored track gain. Outside an interval the cleared track is silent. */
    for(unsigned i=0;i<4;i++) {
        const CarAudioTmxBin *b=t->bins+i;
        for(unsigned pair=0;pair<b->pairs;pair++) {
            const CarAudioTmxEnd *lo=b->ends+2*pair,*hi=lo+1,*e;
            float rpm=input[b->input],v;
            if(rpm<=lo->start || rpm>=hi->start+hi->width) continue;
            if(rpm<lo->start+lo->width) { e=lo; v=(rpm-lo->start)/lo->width; }
            else { e=hi; v=hi->width?(rpm-hi->start)/hi->width:0; }
            float gain=curve(v,e->curve)*e->gain;
            for(unsigned j=0;j<b->count;j++) gain*=xf[b->dependencies[j]];
            gains[e->track]=gain;
        }
    }
    memcpy(out,gains,sizeof gains); return 0;
}
