#include "car_audio.h"
#include "car_audio_runtime.h"
#include <CommonCrypto/CommonDigest.h>
#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <setjmp.h>
#include <stdlib.h>
#include <strings.h>
#include <sys/stat.h>
#include <unistd.h>

enum { MAX_RESOURCES=64, MAX_PACKAGES=76, MAX_ENGINES=64, CALIBRATION=0x12c };
typedef struct {
    const uint8_t *source, *plan;
    uint32_t size, plan_size, kind, sample_count;
    uint8_t digest[32];
    CarAudioPCM *samples;
    CarAudioRuntimeBank bank;
    CarAudioRuntimeModule *modules;
    CarAudioRuntimeInterface *refs;
    CarAudioCsisInterface *registry;
    size_t nm,nr;
    uint32_t image;
} Resource;
typedef struct {
    uint32_t key, count;
    int retiring;
    uint8_t *bytes, digest[32];
    size_t size;
    const uint8_t *calibration[2];
    uint32_t layer[2][3];
    Resource resource[MAX_RESOURCES];
} Package;
typedef struct {
    uint32_t object, state[2];
    int native_ginsu, replaced, recovering;
    uint32_t mix_gain;
    Package *package;
    CarAudioRuntimeInstance instances[2];
    /* Four native Ginsu streams: engine/exhaust, acceleration/deceleration. */
    struct { uint32_t data, synth, work, gin; int32_t voice; int constructed; } ginsu[4];
} Engine;
static Package packages[MAX_PACKAGES];
static Engine engines[MAX_ENGINES];
static CarAudioBackend backend;
static CarAudioVoice voices[512];
static uint64_t next_identity=1;
static _Thread_local CarAudioRuntimeInstance *executing;
static _Thread_local jmp_buf *import_escape;
static uint32_t native_call(Cpu *c,uint32_t fn,uint32_t object,int n,const uint32_t *args) {
    Cpu saved=*c; c->ecx=object;
    uint32_t value=call_guest(c,fn,n,args); *c=saved; return value;
}
static uint32_t float_word(float value) { uint32_t word; memcpy(&word,&value,4); return word; }

/* SNDPKTPLAY_start (0081DBAD) allocates at priority 101, just like stock
 * Ginsu StartSynthesis (004E8CA0). play[0] is volume, not priority. Packet
 * voices have group 0; the engine's NFSMix input at +10 carries its actual
 * volume/pause/fade group (004B8F40, 004EE3E0, 004EEA00, 004EDD90).
 * Extra transports must never evict an existing game sound: 0081F413 otherwise
 * steals any priority below 101. Admission and starts share the guest lock. */
static int free_native_voices(unsigned needed) {
    uint32_t pool=M32(0x009c21f4); unsigned count=M8(0x009c204a),free=0;
    /* 0081EC32 supplies [0, byte 009C204A) to 0081F413. The allocated
     * pool at 009C218C may contain slots outside that admission range. */
    unsigned capacity=M16(0x009c218c);
    if(!pool || count>capacity || capacity>256) return 0;
    for(unsigned i=0;i<count;i++) if(!M8(pool+i*0x90+0x6d)) ++free;
    return free>=needed;
}
static int voice_alive(Cpu *c,int32_t voice) {
    uint32_t token=(uint32_t)voice;
    return voice>=0 && (int32_t)native_call(c,0x0081f7cb,0,1,&token)>=0;
}
static uint32_t engine_mix_gain(const Engine *e) {
    uint32_t spatial=M32(e->object+0x10);
    return spatial?(M32(spatial)>>16)&32767:0;
}
static int engine_active(const Engine *e) {
    /* AEMS has no +8 test in 004EDD90. Ginsu freezes updates at +8==0;
     * its real Detach (004DC670) mutes voices and releases the controller. */
    return M32(e->object+0x28) && (!e->native_ginsu || M8(e->object+8));
}
static void sync_ginsu_volume(Cpu *,Engine *);

/* Source float dry/wet sends enter four ordinary mono packet voices. Native
 * panning is fixed at the two stereo endpoints; source option 3 has already
 * applied its continuous equal-power pan. Wet streams enter the native reverb
 * send, rather than being added to the dry master output. No device is opened.
 * All calls, including packet releases, run under the existing guest lock. */
enum { PACKET_FRAMES=512, PACKET_CACHE=8 };
static struct {
    int live, stopping;
    uint32_t callback;
    uint64_t produced, sequence[4], cache_sequence[PACKET_CACHE];
    float cache[PACKET_CACHE][4][PACKET_FRAMES];
    struct {
        int32_t packet,voice;
        uint32_t work,pcm[2],descriptor;
        float source[2][PACKET_FRAMES];
    } stream[4];
} bridge;
static void bridge_render(uint64_t sequence) {
    float dry[PACKET_FRAMES*2]={0},wet[PACKET_FRAMES*2]={0};
    float saved_volume[512];
    for(unsigned i=0;i<512;i++) saved_volume[i]=voices[i].volume;
    /* Apply each engine's live NFSMix group to its own source players. The
     * source program already consumed mix_gain on the last control tick;
     * the ratio follows fades when that program is temporarily idle. Only
     * attenuate: source gain processing need not be linear on rising gain. */
    for(unsigned i=0;i<MAX_ENGINES;i++) if(engines[i].object) {
        Engine *e=engines+i;
        float gain=engine_active(e) && e->mix_gain && !e->recovering?fminf(1,(float)engine_mix_gain(e)/e->mix_gain):0;
        for(unsigned k=0;k<2;k++) {
            CarAudioRuntimeInstance *p=e->instances+k;
            if(!p->bank) continue;
            for(size_t j=0;j<p->module->player_count;j++) if(p->players[j].owned)
                for(unsigned v=0;v<512;v++) if(voices[v].owned && voices[v].handle==p->players[j].voice)
                    voices[v].volume*=gain;
        }
    }
    car_audio_backend_render(&backend,dry,wet,PACKET_FRAMES);
    for(unsigned i=0;i<512;i++) voices[i].volume=saved_volume[i];
    unsigned slot=(unsigned)(sequence%PACKET_CACHE);
    for(unsigned i=0;i<PACKET_FRAMES;i++) for(unsigned ch=0;ch<2;ch++) {
        bridge.cache[slot][ch][i]=dry[i*2+ch]; bridge.cache[slot][ch+2][i]=wet[i*2+ch];
    }
    bridge.cache_sequence[slot]=sequence; bridge.produced=sequence+1;
}
static void bridge_submit(Cpu *c,unsigned stream,uint32_t pcm) {
    uint32_t packet=bridge.stream[stream].descriptor; if(!packet) return;
    M32(packet+4)=PACKET_FRAMES; M32(packet+12)=pcm;
    uint32_t args[]={(uint32_t)bridge.stream[stream].packet,packet};
    native_call(c,0x0081deee,0,2,args);
}
static int packet_released(Cpu *c) {
    uint32_t pcm=arg(c,0),stream=arg(c,1);
    if(!bridge.live || bridge.stopping || stream>=4) RET(0,0);
    if(pcm!=bridge.stream[stream].pcm[0] && pcm!=bridge.stream[stream].pcm[1]) RET(0,0);
    uint64_t sequence=bridge.sequence[stream]++;
    if(sequence==bridge.produced) {
        for(unsigned i=0;i<MAX_ENGINES;i++) if(engines[i].object) sync_ginsu_volume(c,engines+i);
        bridge_render(sequence);
    }
    unsigned slot=(unsigned)(sequence%PACKET_CACHE);
    int available=sequence<bridge.produced && bridge.cache_sequence[slot]==sequence;
    for(unsigned i=0;i<PACKET_FRAMES;i++) {
        float value=available?bridge.cache[slot][stream][i]:0;
        unsigned buffer=pcm==bridge.stream[stream].pcm[1];
        bridge.stream[stream].source[buffer][i]=isfinite(value)?value:0;
        int n=!isfinite(value)?0:value<=-1?-32768:value>=1?32767:(int)lrintf(value*32768.0f);
        M16(pcm+2*i)=(uint16_t)n;
    }
    bridge_submit(c,stream,pcm); RET(0,0);
}
static void bridge_stop(Cpu *c) {
    if(!bridge.live) return;
    bridge.stopping=1;
    for(unsigned i=0;i<4;i++) {
        if(bridge.stream[i].packet>=0) {
            uint32_t arg0=(uint32_t)bridge.stream[i].packet;
            native_call(c,0x0081e274,0,1,&arg0); native_call(c,0x0081e063,0,1,&arg0);
        }
        if(bridge.stream[i].work) gfree(bridge.stream[i].work);
        if(bridge.stream[i].descriptor) gfree(bridge.stream[i].descriptor);
        for(unsigned j=0;j<2;j++) if(bridge.stream[i].pcm[j]) gfree(bridge.stream[i].pcm[j]);
    }
    uint32_t callback=bridge.callback; memset(&bridge,0,sizeof bridge); bridge.callback=callback;
}
static int bridge_start(Cpu *c) {
    if(bridge.live) return 0;
    if(!free_native_voices(4)) return -1;
    if(!bridge.callback) bridge.callback=host_fn(packet_released,"added-car PCM packet release");
    for(unsigned i=0;i<4;i++) bridge.stream[i].packet=-1;
    bridge.live=1;
    uint32_t count=8,overhead=native_call(c,0x0081dafd,0,1,&count);
    if(overhead<0x9c || overhead>65536) goto fail;
    /* _SND_attrsetdef writes 104 bytes; _SNDplaysetdef writes 24. */
    uint32_t descriptors=gmalloc_try(160); if(!descriptors) goto fail;
    for(unsigned i=0;i<4;i++) {
        bridge.stream[i].work=gmalloc_try(overhead);
        bridge.stream[i].descriptor=gmalloc_try(16);
        for(unsigned j=0;j<2;j++) bridge.stream[i].pcm[j]=gmalloc_try(PACKET_FRAMES*2);
        if(!bridge.stream[i].work || !bridge.stream[i].descriptor || !bridge.stream[i].pcm[0] || !bridge.stream[i].pcm[1]) { gfree(descriptors); goto fail; }
        uint32_t create[]={bridge.callback,0,i,bridge.stream[i].work,overhead};
        int32_t packet=(int32_t)native_call(c,0x0081db11,0,5,create);
        bridge.stream[i].packet=packet;
        if(packet<0) { gfree(descriptors); goto fail; }
        uint32_t attr=descriptors+16,play=descriptors+128;
        native_call(c,0x0081e2cb,0,1,&attr); native_call(c,0x0081d582,0,1,&play);
        M16(attr+8)=4; M8(play)=127;
        M16(descriptors)=44100; M8(descriptors+2)=1; M8(descriptors+3)=8;
        uint32_t start[]={(uint32_t)packet,descriptors,attr,play};
        int32_t voice=(int32_t)native_call(c,0x0081dbad,0,4,start); bridge.stream[i].voice=voice;
        if(voice<0) { gfree(descriptors); goto fail; }
        uint32_t pan[]={(uint32_t)voice,(i&1)?16384:49152,0}; native_call(c,0x0081e557,0,3,pan);
        uint32_t dry[]={(uint32_t)voice,i<2?127:0}; native_call(c,0x0081e3cb,0,2,dry);
        uint32_t wet[]={(uint32_t)voice,0,i<2?0:127}; native_call(c,0x0081e318,0,3,wet);
        for(unsigned j=0;j<2;j++) bridge_submit(c,i,bridge.stream[i].pcm[j]);
    }
    gfree(descriptors); return 0;
fail: bridge_stop(c); return -1;
}
static int bridge_recover(Cpu *c) {
    if(bridge.live) for(unsigned i=0;i<4;i++) if(!voice_alive(c,bridge.stream[i].voice)) {
        bridge_stop(c); break;
    }
    /* A stolen slot still belongs to its replacement. Wait for room rather
     * than evicting that sound or permanently restoring the donor engine. */
    if(!bridge.live && !free_native_voices(4)) return 1;
    return bridge_start(c);
}
static void sync_bridge_volume(Cpu *c) {
    if(!bridge.live) return;
    unsigned active=0;
    for(unsigned i=0;i<MAX_ENGINES;i++) if(engines[i].object)
        active|=engine_active(engines+i) && !engines[i].recovering && engine_mix_gain(engines+i)!=0;
    /* Mute already queued packets immediately, as native Ginsu Detach does.
     * Per-engine gains in bridge_render keep independent NFSMix memberships. */
    for(unsigned i=0;i<4;i++) {
        uint32_t volume[]={(uint32_t)bridge.stream[i].voice,active?127:0};
        native_call(c,0x0081d7ef,0,2,volume);
    }
}
/* Native packet decoder 00827182 calls the converter installed at 00822BF3
 * through 009C2528. Preserve source float headroom at that exact boundary;
 * unrelated PCM, including every native Ginsu packet, stays byte-identical.
 * The packet address still owns native cursor/retirement/resampling behavior.
 */
void f_00825310_orig(Cpu *);
void f_00825310(Cpu *c) {
    uint32_t frames=arg(c,0),src=arg(c,1),dst=arg(c,2);
    if(bridge.live && car_audio_enabled() && frames) for(unsigned i=0;i<4;i++) for(unsigned j=0;j<2;j++) {
        uint32_t base=bridge.stream[i].pcm[j];
        if(!base || src<base || src-base>=PACKET_FRAMES*2 || (src-base)%2) continue;
        unsigned offset=(src-base)/2;
        if(frames>PACKET_FRAMES-offset) break;
        for(uint32_t k=0;k<frames;k++) MF32(dst+4*k)=bridge.stream[i].source[j][offset+k]*32768.0f;
        c->esp+=4; return;
    }
    f_00825310_orig(c);
}

static uint32_t rd(const uint8_t *p) {
    return (uint32_t)p[0]|(uint32_t)p[1]<<8|(uint32_t)p[2]<<16|(uint32_t)p[3]<<24;
}
static float rf(const uint8_t *p) { float f; memcpy(&f,p,4); return f; }
static int32_t ri(const uint8_t *p) { uint32_t u=rd(p); return u<=INT32_MAX?(int32_t)u:-1-(int32_t)(UINT32_MAX-u); }
typedef struct { const uint8_t *p; size_t n; int failed; } Reader;
static const uint8_t *take(Reader *r,size_t n) {
    if(r->failed || n>r->n) { r->failed=1; return NULL; }
    const uint8_t *p=r->p; r->p+=n; r->n-=n; return p;
}
static uint32_t word(Reader *r) { const uint8_t *p=take(r,4); return p?rd(p):0; }
static void digest(const void *p,size_t n,uint8_t out[32]) { CC_SHA256(p,(CC_LONG)n,out); }
static void hex(const uint8_t d[32],char out[65]) {
    static const char digits[]="0123456789abcdef";
    for(unsigned i=0;i<32;i++) { out[2*i]=digits[d[i]>>4]; out[2*i+1]=digits[d[i]&15]; } out[64]=0;
}
int car_audio_enabled(void) {
    const char *value=getenv("NFS_ADDCAR_AUDIO");
    return !value || !strcasecmp(value,"on") || !strcmp(value,"1");
}
static int resolve(void *context,uint64_t identity,int16_t sample,CarAudioPCM *out) {
    (void)context;
    for(unsigned i=0;i<MAX_PACKAGES;i++) if(packages[i].key)
        for(unsigned j=0;j<packages[i].count;j++) {
            Resource *r=packages[i].resource+j;
            if(r->bank.identity!=identity) continue;
            if(sample<0 || (uint32_t)sample>=r->sample_count || !r->samples[sample].pcm) return -1;
            *out=r->samples[sample]; return 0;
        }
    return -1;
}
static int create_class(void *context,const int32_t *v,size_t n,void **out) {
    (void)context; if(n!=1) return -1;
    int32_t *p=malloc(sizeof(*p)); if(!p) return -1; *p=*v; *out=p; return 0;
}
static void update_class(void *context,void *p,const int32_t *v,size_t n) { (void)context; if(n==1) *(int32_t *)p=*v; }
static void release_class(void *context,void *p) { (void)context; free(p); }
static int plan(Resource *r) {
    Reader in={r->plan,r->plan_size,0};
    r->nr=word(&in); r->nm=word(&in);
    if(!r->nr || r->nr>64 || r->nm!=1) return -1;
    r->refs=calloc(r->nr,sizeof(*r->refs)); r->registry=calloc(r->nr,sizeof(*r->registry));
    r->modules=calloc(r->nm,sizeof(*r->modules));
    if(!r->refs || !r->registry || !r->modules) return -1;
    for(size_t i=0;i<r->nr;i++) {
        uint32_t site=word(&in),n=word(&in),words=word(&in),kind=word(&in);
        if(n<6 || n>260 || words>100 || kind!=1) return -1;
        const uint8_t *id=take(&in,n); if(!id || id[n-1]) return -1;
        r->registry[i]=(CarAudioCsisInterface){id,n,(uint16_t)words,(uint8_t)kind};
        r->refs[i]=(CarAudioRuntimeInterface){site,r->registry[i]};
    }
    for(size_t i=0;i<r->nm;i++) {
        CarAudioRuntimeModule *m=r->modules+i;
        m->descriptor=word(&in); m->entry=word(&in); m->state=word(&in); m->size=word(&in);
        m->member_offset=word(&in); m->destroy_offset=word(&in);
        uint32_t id=word(&in); m->player_count=word(&in); m->control_count=word(&in);
        if(id>=r->nr || m->player_count>64 || m->control_count>64) return -1;
        m->interface=r->registry[id];
        CarAudioRuntimePlayerPlan *players=calloc(m->player_count?m->player_count:1,sizeof(*players));
        uint32_t *controls=calloc(m->control_count?m->control_count:1,sizeof(*controls));
        m->players=players; m->controls=controls;
        if(!players || !controls) return -1;
        for(size_t j=0;j<m->player_count;j++) {
            players[j].offset=word(&in); players[j].size=word(&in); players[j].selector=word(&in);
        }
        for(size_t j=0;j<m->control_count;j++) controls[j]=word(&in);
    }
    if(in.failed || in.n || !next_identity) return -1;
    r->image=gmalloc_try(r->size); if(!r->image) return -1;
    CarAudioRuntimeClasses classes={create_class,update_class,release_class,NULL};
    return car_audio_runtime_load(&r->bank,r->source,G+r->image,r->size,r->image,next_identity++,
        r->modules,r->nm,r->refs,r->nr,r->registry,r->nr,&backend,&classes);
}
static void clear_package(Package *p) {
    for(unsigned i=0;i<p->count;i++) {
        Resource *r=p->resource+i;
        car_audio_runtime_unload(&r->bank);
        if(r->image) gfree(r->image);
        for(size_t j=0;j<r->nm && r->modules;j++) { free((void *)r->modules[j].players); free((void *)r->modules[j].controls); }
        for(uint32_t j=0;j<r->sample_count && r->samples;j++) free((void *)r->samples[j].pcm);
        free(r->samples); free(r->modules); free(r->refs); free(r->registry);
    }
    free(p->bytes); memset(p,0,sizeof(*p));
    int loaded=0;
    for(unsigned i=0;i<MAX_PACKAGES;i++) loaded|=packages[i].key!=0;
    if(!loaded) car_audio_backend_destroy(&backend);
}
static int allowed(const Resource *r,unsigned layer) {
    char name[65]; hex(r->digest,name);
    return !strcmp(name,layer==0?
        "e5e2aaa143f345009f37a0436643aed099f438a9d975c1e26f828045b2ff7f64":
        "67d6f7fb267d3725729a66f827cee8f60e62c3f5971ad8d994adbbc91448ae7c");
}
int car_audio_load(uint32_t key,const char *name,const char *filename) {
    if(!car_audio_enabled()) return 0;
    if(!key || !name || !filename) return -1;
    if(strcasecmp(name,"jaguarxk")) {
        struct stat st;
        return stat(filename,&st) && errno==ENOENT?0:-1;
    }
    Package *p=NULL;
    for(unsigned i=0;i<MAX_PACKAGES;i++) {
        if(packages[i].key==key) { packages[i].retiring=0; return 1; }
        if(!packages[i].key && !p) p=packages+i;
    }
    if(!p || !filename) return -1;
    FILE *f=fopen(filename,"rb"); if(!f) return errno==ENOENT?0:-1;
    if(fseek(f,0,SEEK_END)) { fclose(f); return -1; }
    long bytes=ftell(f); rewind(f);
    if(bytes<68 || bytes>256*1024*1024) { fclose(f); return -1; }
    p->bytes=malloc((size_t)bytes); p->size=(size_t)bytes;
    int ok=p->bytes && fread(p->bytes,1,p->size,f)==p->size; fclose(f);
    if(!ok) goto fail;
    const uint8_t *b=p->bytes;
    if(memcmp(b,"A7CA",4) || rd(b+4)!=1 || rd(b+8)!=p->size ||
       memcmp(b+20,"jaguarxk\0\0\0\0\0\0\0\0",16)) goto fail;
    p->count=rd(b+12); if(!p->count || p->count>MAX_RESOURCES) { p->count=0; goto fail; }
    uint8_t sha[32]; digest(b+68,p->size-68,sha); if(memcmp(sha,b+36,32)) goto fail;
    digest(b,p->size,p->digest);
    char package_id[65]; hex(p->digest,package_id);
    if(strcmp(package_id,"66b587fa9e179b69180bcae26c8111d4b94c96dabc2a949f8e262c0289e2c02c")) goto fail;
    Reader in={b+68,p->size-68,0};
    uint32_t graph_size=rd(b+16); if(graph_size>4*1024*1024 || !take(&in,graph_size)) goto fail;
    for(unsigned k=0;k<2;k++) {
        p->calibration[k]=take(&in,CALIBRATION);
        if(!p->calibration[k]) goto fail;
        for(unsigned j=0;j<3;j++) { p->layer[k][j]=word(&in); if(p->layer[k][j]>=p->count) goto fail; }
        if(!isfinite(rf(p->calibration[k]+0x120)) || !isfinite(rf(p->calibration[k]+0xe8)) ||
           rd(p->calibration[k]+0x124)!=87) goto fail;
    }
    for(unsigned i=0;i<p->count;i++) {
        Resource *r=p->resource+i;
        r->kind=word(&in); r->size=word(&in); r->plan_size=word(&in); r->sample_count=word(&in);
        const uint8_t *d=take(&in,32); if(!d) goto fail; memcpy(r->digest,d,32);
        if((r->kind!=1 && r->kind!=2) || r->size>16*1024*1024 ||
           r->sample_count>4096 || r->plan_size>1024*1024) goto fail;
        r->source=take(&in,r->size); r->plan=take(&in,r->plan_size);
        if(!r->source || !r->plan) goto fail;
        digest(r->source,r->size,sha); if(memcmp(sha,r->digest,32)) goto fail;
        if(r->kind==1 && (r->size<40 || memcmp(r->source,"Gnsu20",6) || r->plan_size || r->sample_count)) goto fail;
        if(r->kind==2 && (r->size<0x60 || memcmp(r->source,"ABKC",4))) goto fail;
        for(unsigned j=0;j<i;j++) if(!memcmp(r->digest,p->resource[j].digest,32)) {
            /* Two authored names may share exact bytes, but conflicting kinds
             * or executable plans for the same digest are never accepted. */
            if(r->kind!=p->resource[j].kind || r->plan_size!=p->resource[j].plan_size) goto fail;
        }
        r->samples=calloc(r->sample_count?r->sample_count:1,sizeof(*r->samples)); if(!r->samples) goto fail;
        for(uint32_t j=0;j<r->sample_count;j++) {
            uint32_t frames=word(&in),rate=word(&in),loop=word(&in),n=word(&in);
            if(frames>10000000 || n!=(uint64_t)frames*2 || loop>1 || (frames && rate!=44100)) goto fail;
            const uint8_t *pcm=take(&in,n); if(!pcm) goto fail;
            if(!frames) { if(rate || loop) goto fail; continue; }
            float *samples=malloc((size_t)frames*sizeof(float)); if(!samples) goto fail;
            for(uint32_t k=0;k<frames;k++) {
                unsigned u=(unsigned)pcm[2*k]|(unsigned)pcm[2*k+1]<<8;
                samples[k]=(float)(u<=32767?(int)u:(int)u-65536)*(1.0f/32768.0f);
            }
            r->samples[j]=(CarAudioPCM){samples,frames,(float)rate,(int)loop,NULL,NULL};
        }
    }
    if(in.failed || in.n) goto fail;
    if(!backend.voices && car_audio_backend_init(&backend,voices,512,44100,resolve,NULL)) goto fail;
    for(unsigned k=0;k<2;k++) {
        Resource *r=p->resource+p->layer[k][0];
        if(r->kind!=2 || !allowed(r,k) || !r->plan_size || plan(r) ||
           !car_audio_engine_class_matches(k?0:1,&r->modules[0].interface)) goto fail;
        for(unsigned j=1;j<3;j++) if(p->resource[p->layer[k][j]].kind!=1) goto fail;
    }
    p->key=key;
    if(diag_on) diag_event("addcar_audio: loaded key=%08X bytes=%zu sha256=%s resources=%u",key,p->size,package_id,p->count);
    return 1;
fail: clear_package(p); return -1;
}
/* Resource identities are full SHA-256 pairs. A fresh unlinked file gives each
 * guest open an independent cursor and prevents all unrelated substitutions. */
int car_audio_open(const char *name) {
    if(!car_audio_enabled() || !name) return -1;
    char normalized[192]; size_t n=strlen(name); if(n>=sizeof normalized) return -1;
    for(size_t i=0;i<=n;i++) normalized[i]=name[i]=='\\'?'/':name[i];
    const char *path=normalized;
    if(path[0] && path[1]==':') path+=2;
    while(*path=='/') path++;
    if(!strncasecmp(path,"NFSMW/",6)) path+=6;
    if(strncasecmp(path,"SOUND/ADDCAR_AUDIO/",19)) return -1;
    path+=19; if(strlen(path)!=129 || path[64]!='/') return -1;
    for(unsigned i=0;i<MAX_PACKAGES;i++) if(packages[i].key) {
        char id[65]; hex(packages[i].digest,id); if(strncmp(path,id,64)) continue;
        for(unsigned j=0;j<packages[i].count;j++) {
            Resource *r=packages[i].resource+j; hex(r->digest,id); if(strcmp(path+65,id)) continue;
            FILE *f=tmpfile(); if(!f) return -1;
            if(fwrite(r->source,1,r->size,f)!=r->size || fflush(f) || fseek(f,0,SEEK_SET)) { fclose(f); return -1; }
            int fd=dup(fileno(f)); fclose(f); return fd;
        }
    }
    return -1;
}
int car_audio_code_region(uint32_t at) {
    return executing && executing->bank && at-executing->bank->base<executing->bank->size;
}
int car_audio_dispatch(Cpu *c,uint32_t target) {
    if(!executing) return 0;
    unsigned ordinal=target==0x8196e8?4:target==0x81a52c?27:target==0x819875?38:0;
    if(!ordinal) return 0;
    uint32_t result=0;
    if(car_audio_runtime_import(executing,ordinal,arg(c,0),&result)) {
        /* Unwind only this interpreter invocation. Its caller then retires
         * source readers before restoring the still-owned stock controller. */
        if(import_escape) longjmp(*import_escape,1);
        result=0;
    }
    c->eax=result; c->esp+=4; return 1;
}
static Engine *engine(uint32_t object) {
    for(unsigned i=0;i<MAX_ENGINES;i++) if(engines[i].object==object) return engines+i;
    return NULL;
}
static Package *for_object(uint32_t object) {
    uint32_t state=M32(object+0x14),car=state?M32(state+0x34):0,collection=car?M32(car+0x204):0;
    uint32_t key=collection?M32(collection+0x20):0;
    for(unsigned i=0;i<MAX_PACKAGES;i++) if(key && packages[i].key==key) return packages+i;
    return NULL;
}
static int execute(void *context,uint32_t entry,uint32_t input) {
    Cpu *caller=context,saved=*caller;
    uint32_t sp=caller->esp;
    jmp_buf escape; jmp_buf *previous=import_escape; import_escape=&escape;
    int rc;
    if(setjmp(escape)) { rc=-1; goto done; }
    caller->esp-=8; M32(caller->esp)=0xFEEDF00D; M32(caller->esp+4)=input;
    extern void interp_run(Cpu *,uint32_t);
    interp_run(caller,entry);
    rc=caller->esp!=sp-4;
done: import_escape=previous; *caller=saved; return rc?-1:0;
}
static int inputs(Cpu *c,Engine *e,unsigned layer,int32_t out[100]) {
    uint32_t object=e->object,member=M32(object+0x28),rpm_object=M32(object+0x38);
    if(!member || !rpm_object) return -1;
    int32_t controls[30]={0};
    for(unsigned i=0;i<26;i++) controls[i]=(int32_t)M32(member+4+4*i);
    Cpu saved=*c; c->ecx=rpm_object;
    call_guest(c,M32(M32(rpm_object)+0x44),0,NULL);
    double rpm=c->st[c->top]; *c=saved;
    const uint8_t *cal=e->package->calibration[layer];
    controls[0]=87; controls[12]=1;
    if(car_audio_engine_class_rpm(rpm,rf(cal+0x120),rf(cal+0xe8),controls+1) ||
       car_audio_engine_class_pitch(MF32(object+0x3c),controls+16)) return -1;
    controls[2]=controls[3]; controls[4]=controls[5];
    controls[26]=controls[13]; controls[27]=controls[14]; controls[28]=controls[15]; controls[29]=controls[21];
    return car_audio_engine_class_inputs(out,controls,30,e->package->calibration[1],CALIBRATION,
        e->package->calibration[0],CALIBRATION);
}
static int start_ginsu(Cpu *c,Engine *e) {
    if(!free_native_voices(4)) return -1;
    uint32_t packets=8,overhead=native_call(c,0x0081dafd,0,1,&packets);
    if(overhead<0x9c || overhead>65536) return -1;
    for(unsigned k=0;k<4;k++) {
        Resource *r=e->package->resource+e->package->layer[k/2][k%2+1];
        char pack[65],resource[65],name[192];
        hex(e->package->digest,pack); hex(r->digest,resource);
        snprintf(name,sizeof name,"SOUND/ADDCAR_AUDIO/%s/%s",pack,resource);
        int fd=car_audio_open(name); if(fd<0) return -1;
        e->ginsu[k].gin=gmalloc_try(r->size);
        if(!e->ginsu[k].gin) { close(fd); return -1; }
        size_t pos=0;
        while(pos<r->size) {
            ssize_t n=read(fd,G+e->ginsu[k].gin+pos,r->size-pos);
            if(n<0 && errno==EINTR) continue;
            if(n<=0) { close(fd); return -1; } pos+=(size_t)n;
        }
        close(fd);
        e->ginsu[k].data=gmalloc_try(0xb0); e->ginsu[k].synth=gmalloc_try(0x44);
        e->ginsu[k].work=gmalloc_try(overhead+0x8a4);
        if(!e->ginsu[k].data || !e->ginsu[k].synth || !e->ginsu[k].work) return -1;
        uint32_t gin=e->ginsu[k].gin;
        if(!(native_call(c,0x004e1280,e->ginsu[k].data,1,&gin)&255)) return -1;
        uint32_t args[]={e->ginsu[k].work,overhead+0x8a4};
        native_call(c,0x004e8be0,e->ginsu[k].synth,2,args);
        if((int32_t)M32(e->ginsu[k].synth+0x1c)<0) return -1;
        e->ginsu[k].constructed=1;
        uint32_t data=e->ginsu[k].data;
        if(!(native_call(c,0x004e1c10,e->ginsu[k].synth,1,&data)&255)) return -1;
        uint32_t rpm=float_word(rf(r->source+8));
        e->ginsu[k].voice=(int32_t)native_call(c,0x004e8ca0,e->ginsu[k].synth,1,&rpm);
        if(e->ginsu[k].voice<0) return -1;
    }
    return 0;
}
static void sync_ginsu_volume(Cpu *c,Engine *e) {
    if(!e->native_ginsu) return;
    uint32_t spatial=M32(e->object+0x10),state=M32(e->object+0x40);
    for(unsigned k=0;k<4;k++) {
        int32_t gain=state?(int32_t)M32(state+0x3c+4*(k%2)):0;
        int32_t authored=ri(e->package->calibration[k/2]+0x128)&65535;
        uint32_t base=spatial && engine_active(e) && !e->recovering?M32(spatial+4)&32767:0;
        int32_t level=(int32_t)((int64_t)gain*base*authored/(32767LL*32767*256));
        uint32_t volume[]={(uint32_t)e->ginsu[k].voice,(uint32_t)(level<0?0:level>127?127:level)};
        native_call(c,0x0081d7ef,0,2,volume);
    }
}
static int recover_ginsu(Cpu *c,Engine *e) {
    if(!e->native_ginsu) return 0;
    for(unsigned k=0;k<4;k++) if(!voice_alive(c,e->ginsu[k].voice)) {
        if(!free_native_voices(1)) return 1;
        float frequency=MF32(e->object+0x84);
        if(!isfinite(frequency) || frequency<=0) return -1;
        uint32_t rpm=float_word(frequency);
        e->ginsu[k].voice=(int32_t)native_call(c,0x004e8ca0,e->ginsu[k].synth,1,&rpm);
        if(e->ginsu[k].voice<0) return -1;
    }
    return 0;
}
static int update_ginsu(Cpu *c,Engine *e,const int32_t words[100],unsigned layer) {
    if(!e->native_ginsu) return 0;
    uint32_t object=e->object,state=M32(object+0x40);
    if(!state) return -1;
    double raw=MF32(state+0xb8);
    if(!isfinite(raw)) return -1;
    uint32_t pitch=(uint32_t)fmax(0,fmin(65535,4096.0*MF32(object+0x3c)));
    for(unsigned mode=0;mode<2;mode++) {
        unsigned index=layer*2+mode; uint32_t voice=(uint32_t)e->ginsu[index].voice;
        uint32_t pan[]={voice,(uint32_t)words[15],0}; native_call(c,0x0081e557,0,3,pan);
        uint32_t rate[]={voice,pitch}; native_call(c,0x0081e4be,0,2,rate);
        uint32_t filter[]={voice,(uint32_t)words[21]}; native_call(c,0x0081e469,0,2,filter);
        uint32_t dry[]={voice,(uint32_t)((32767+words[13])>>8)}; native_call(c,0x0081e3cb,0,2,dry);
        uint32_t wet[]={voice,0,(uint32_t)(words[14]>>8)}; native_call(c,0x0081e318,0,3,wet);
        uint32_t frequency[]={float_word((float)raw),float_word(60)};
        native_call(c,0x004e1cd0,e->ginsu[index].synth,2,frequency);
    }
    return 0;
}
static void restore_stock(Cpu *c,uint32_t object,int ginsu) {
    uint32_t member=M32(object+0x28);
    if(member && !M32(member)) {
        uint32_t args[]={0x00914824,member+4,member};
        if((int32_t)native_call(c,0x00831a8a,0,3,args)<0) {
            uint32_t id=0x008f0248; native_call(c,0x0083198b,0x00914824,1,&id);
            native_call(c,0x00831a8a,0,3,args);
        }
    }
    if(ginsu) for(unsigned i=0;i<2;i++) {
        uint32_t synth=M32(object+0x58+i*0x18),data=M32(object+0x54+i*0x18);
        float frequency=MF32(object+0x84);
        /* StartupGinsu uses the resident stock data's minimum frequency.
         * Invalid live observations must not enter its sample lookup. */
        if(!isfinite(frequency) || frequency<=0) frequency=data?MF32(data+4):0;
        if(synth && isfinite(frequency) && frequency>0) {
            uint32_t rpm=float_word(frequency);
            M32(object+0x68+i*0x18)=native_call(c,0x004e8ca0,synth,1,&rpm);
        }
    }
}
static void detach_engine(Cpu *c,Engine *e,int restore) {
    Cpu saved=*c;
    Package *package=e->package;
    uint32_t object=e->object; int replaced=e->replaced,ginsu=e->native_ginsu;
    for(unsigned i=0;i<2;i++) {
        car_audio_runtime_destroy(e->instances+i);
        if(e->state[i]) gfree(e->state[i]);
    }
    for(unsigned i=0;i<4;i++) {
        if(e->ginsu[i].synth) {
            if(e->ginsu[i].constructed) { c->ecx=e->ginsu[i].synth; call_guest(c,0x004e8c50,0,NULL); }
            gfree(e->ginsu[i].synth);
        }
        if(e->ginsu[i].data) gfree(e->ginsu[i].data);
        if(e->ginsu[i].work) gfree(e->ginsu[i].work);
        if(e->ginsu[i].gin) gfree(e->ginsu[i].gin);
    }
    *c=saved; memset(e,0,sizeof(*e));
    int active=0,borrowed=0;
    for(unsigned i=0;i<MAX_ENGINES;i++) { active|=engines[i].object!=0; borrowed|=engines[i].package==package; }
    if(!active) bridge_stop(c);
    if(restore && replaced) restore_stock(c,object,ginsu);
    if(package && package->retiring && !borrowed) clear_package(package);
    *c=saved;
}
void car_audio_unload(uint32_t key) {
    /* A retiring vault cannot release bytes still borrowed by an engine. Its
     * final real destructor finishes the deferred package unload. */
    for(unsigned i=0;i<MAX_ENGINES;i++) if(engines[i].package && engines[i].package->key==key) {
        engines[i].package->retiring=1; return;
    }
    for(unsigned i=0;i<MAX_PACKAGES;i++) if(packages[i].key==key) clear_package(packages+i);
}
static void init_engine(Cpu *c,uint32_t object,int ginsu) {
    if(!car_audio_enabled() || engine(object)) return;
    Package *p=for_object(object); if(!p) return;
    Engine *e=NULL; for(unsigned i=0;i<MAX_ENGINES;i++) if(!engines[i].object) { e=engines+i; break; }
    if(!e) return;
    e->object=object; e->package=p;
    e->mix_gain=engine_mix_gain(e);
    for(unsigned k=0;k<2;k++) {
        Resource *r=p->resource+p->layer[k][0]; int32_t words[100];
        if(inputs(c,e,k,words)) goto fail;
        e->state[k]=gmalloc_try(r->modules[0].size); if(!e->state[k]) goto fail;
        if(car_audio_runtime_create(&r->bank,0,e->instances+k,G+e->state[k],r->modules[0].size,e->state[k],words,100)) goto fail;
    }
    if(bridge_start(c) || (ginsu && start_ginsu(c,e))) goto fail;
    e->native_ginsu=ginsu;
    /* Keep the native 26-word controller buffer for live inputs, but release
     * its donor program before any source tick can produce duplicate layers. */
    uint32_t member=M32(object+0x28);
    if(member && M32(member)) { native_call(c,0x00831782,M32(member),0,NULL); M32(member)=0; }
    e->replaced=1;
    if(ginsu) for(unsigned i=0;i<2;i++) {
        uint32_t synth=M32(object+0x58+i*0x18);
        if(synth) native_call(c,0x004e1d70,synth,0,NULL);
        M32(object+0x68+i*0x18)=UINT32_MAX;
    }
    if(diag_on) {
        char engine_id[65],exhaust_id[65];
        hex(p->resource[p->layer[0][0]].digest,engine_id); hex(p->resource[p->layer[1][0]].digest,exhaust_id);
        diag_event("addcar_audio: object=%08X key=%08X engine=%s exhaust=%s source_blocks=2 native_ginsu=%u",
            object,p->key,engine_id,exhaust_id,ginsu?4:0);
    }
    return;
fail: detach_engine(c,e,1);
}
void car_audio_tick(Cpu *c,uint32_t object) {
    if(!car_audio_enabled()) return;
    Engine *e=engine(object); if(!e) return;
    Cpu saved=*c;
    sync_ginsu_volume(c,e);
    sync_bridge_volume(c);
    if(!engine_active(e)) { *c=saved; return; }
    /* Recovery: zero is ready, positive waits for admission, negative is a
     * real failure. Keep source state and mute this engine while waiting. */
    int recovery=bridge_recover(c),ginsu=recover_ginsu(c,e);
    if(recovery<0 || ginsu<0) goto fail;
    e->recovering=recovery || ginsu;
    sync_ginsu_volume(c,e);
    sync_bridge_volume(c);
    if(e->recovering) { *c=saved; return; }
    e->mix_gain=engine_mix_gain(e);
    for(unsigned k=0;k<2;k++) {
        int32_t words[100];
        if(inputs(c,e,k,words) || car_audio_runtime_update(e->instances+k,words,100)) goto fail;
        if(update_ginsu(c,e,words,k)) goto fail;
        CarAudioRuntimeInstance *previous=executing; executing=e->instances+k;
        int rc=car_audio_runtime_tick(executing,execute,c); executing=previous;
        if(rc || !e->instances[k].bank) goto fail;
    }
    car_audio_backend_tick(&backend); *c=saved; return;
fail: *c=saved; detach_engine(c,e,1);
}
void f_004DC360_orig(Cpu *);
void f_004EE1C0_orig(Cpu *);
void f_004F5CD0_orig(Cpu *);
void f_004F5D60_orig(Cpu *);
void f_004DC1D0_orig(Cpu *);
void f_004DC670_orig(Cpu *);
void f_004DC360(Cpu *c) { uint32_t object=c->ecx; f_004DC360_orig(c); init_engine(c,object,0); }
void f_004EE1C0(Cpu *c) { uint32_t object=c->ecx; f_004EE1C0_orig(c); init_engine(c,object,1); }
void f_004F5CD0(Cpu *c) { Engine *e=engine(c->ecx); if(e) detach_engine(c,e,0); f_004F5CD0_orig(c); }
void f_004F5D60(Cpu *c) { Engine *e=engine(c->ecx); if(e) detach_engine(c,e,0); f_004F5D60_orig(c); }
void f_004DC1D0(Cpu *c) { Engine *e=engine(c->ecx); if(e) detach_engine(c,e,0); f_004DC1D0_orig(c); }
void f_004DC670(Cpu *c) { Engine *e=engine(c->ecx); if(e) detach_engine(c,e,0); f_004DC670_orig(c); }
