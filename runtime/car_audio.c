#include "car_audio.h"
#include "car_audio_runtime.h"
#include "car_audio_tmx.h"
#include "x360abk.h"
#include <CommonCrypto/CommonDigest.h>
#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <setjmp.h>
#include <stdlib.h>
#include <strings.h>
#include <unistd.h>

enum { MAX_RESOURCES=64, MAX_PACKAGES=76, MAX_ENGINES=64, MAX_TIERS=4, MAX_GINSU=5, CALIBRATION=0x12c };
typedef struct {
    const uint8_t *source, *translated, *plan, *sample_source;
    uint32_t size, translated_size, plan_size, kind, sample_count;
    uint8_t digest[32];
    CarAudioPCM *samples;
    CarAudioRuntimeBank bank;
    CarAudioRuntimeModule *modules;
    CarAudioRuntimeInterface *refs;
    CarAudioCsisInterface *registry;
    size_t nm,nr,pcm_bytes;
    uint32_t image;
} Resource;
typedef struct {
    const uint8_t *calibration[2];
    uint32_t layer[2][3];
} Tier;
typedef struct {
    uint32_t key, version, count, tier_count, max_level, default_level;
    uint64_t vault;
    char name[16];
    int retiring;
    uint8_t *bytes, digest[32];
    size_t size;
    Tier tiers[MAX_TIERS];
    Resource resource[MAX_RESOURCES];
    CarAudioTmx tmx[2];
    float minimum_rpm[2], maximum_rpm[2];
} Package;
typedef struct {
    uint32_t object, state[2];
    int native_ginsu, replaced, recovering;
    uint32_t mix_gain;
    Package *package;
    const Tier *tier;
    int diag_started;
    int32_t resident[2][6];
    float tmx_gains[2][6];
    CarAudioRuntimeInstance instances[2];
    /* Carbon has four phase streams; TMX keeps each authored Ginsu track. */
    struct { uint32_t data, synth, work, gin; int32_t voice; int constructed; unsigned layer, track; } ginsu[MAX_GINSU];
} Engine;
static unsigned ginsu_count(const Engine *e) {
    return e->package->version==4?e->package->tmx[0].gin_count+e->package->tmx[1].gin_count:4;
}
static Package packages[MAX_PACKAGES];
static Engine engines[MAX_ENGINES];
static CarAudioBackend backend;
static CarAudioVoice voices[512];
static uint64_t next_identity=1, diag_nonzero_samples;
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
static float tmx_controller_gain(const Engine *e) {
    uint32_t member=M32(e->object+0x28);
    /* Native target volume w5 includes the engine controller and mix group;
     * source gain must honor that value, including the engine-volume setting. */
    return member?fmaxf(0,fminf(1,(float)((32767.0+(int32_t)M32(member+0x18))/32767.0))):0;
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
static void bridge_voice_gain(int32_t handle,float gain) {
    for(unsigned v=0;v<512;v++) if(voices[v].owned && voices[v].handle==handle)
        voices[v].volume*=gain;
}
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
            if(e->package->version==4)
                for(unsigned j=0;j<6;j++) bridge_voice_gain(e->resident[k][j],gain);
            CarAudioRuntimeInstance *p=e->instances+k;
            if(!p->bank) continue;
            for(size_t j=0;j<p->module->player_count;j++) if(p->players[j].owned)
                bridge_voice_gain(p->players[j].voice,gain);
        }
    }
    car_audio_backend_render(&backend,dry,wet,PACKET_FRAMES);
    for(unsigned i=0;i<512;i++) voices[i].volume=saved_volume[i];
    if(diag_on) {
        uint64_t before=diag_nonzero_samples;
        for(unsigned i=0;i<PACKET_FRAMES*2;i++)
            diag_nonzero_samples+=(dry[i]!=0)+(wet[i]!=0);
        if(!before && diag_nonzero_samples)
            diag_event("addcar_audio: first nonzero packet samples=%llu",
                (unsigned long long)diag_nonzero_samples);
    }
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
    const uint8_t *program=r->translated?r->translated:r->source;
    uint32_t size=r->translated?r->translated_size:r->size;
    r->image=gmalloc_try(size); if(!r->image) return -1;
    CarAudioRuntimeClasses classes={create_class,update_class,release_class,NULL};
    return car_audio_runtime_load(&r->bank,program,G+r->image,size,r->image,next_identity++,
        r->modules,r->nm,r->refs,r->nr,r->registry,r->nr,&backend,&classes);
}
static void clear_bank(Resource *r) {
    car_audio_runtime_unload(&r->bank);
    if(r->image) gfree(r->image);
    for(size_t j=0;j<r->nm && r->modules;j++) {
        free((void *)r->modules[j].players); free((void *)r->modules[j].controls);
    }
    for(uint32_t j=0;j<r->sample_count && r->samples;j++) free((void *)r->samples[j].pcm);
    free(r->samples); free(r->modules); free(r->refs); free(r->registry);
    r->image=0; r->samples=NULL; r->modules=NULL; r->refs=NULL; r->registry=NULL;
    r->nr=r->nm=r->pcm_bytes=0;
}
static void clear_package(Package *p) {
    if(diag_on && p->key)
        diag_event("addcar_audio: retired car=%s key=%08X vault=%llu bytes=%zu",
            p->name,p->key,(unsigned long long)p->vault,p->size);
    for(unsigned i=0;i<p->count;i++) clear_bank(p->resource+i);
    free(p->bytes); memset(p,0,sizeof(*p));
    for(unsigned i=0;i<MAX_PACKAGES;i++) if(packages[i].key) return;
    car_audio_backend_destroy(&backend);
}

/* Reviewed converter outputs. The digest binds the car, every ordered tier,
 * GIN and bank identity, calibration, executable plan and decoded PCM. */
static const struct { const char *car, *sha; } carbon_pc_packages[] = {
    {"240sx","fbe66eb609a07e9c7ee3519ee42433ec8e7ce50b77e4d0c51f25e811ad04c5cb"},
    {"300c","e6bd89177de2f5efba8b917a230ff9efd11bd950ae170a1f6f9d4b6d301e9084"},
    {"997gt3rs","54aed5eb7b94e40771b723928ffb62bd9d960520fc974ba0fcf864898e03dd17"},
    {"997tt","5298e813fb9c789f5072a7722091527399824d231701420b44bcbe387f4d9d27"},
    {"brera","841509440157085aa347d306701c43bcec3f1bb4cb71c3fc54d1215ac298b571"},
    {"camaron","0440ca26467b760f2145ddeec816e0f20d41d2be3f7b4e478802d56f3d9919f5"},
    {"ccx","1eb1b0ccb25b32cd9c477e2d4889a9a7cdc66708d441bf4a5437c17a4b91b979"},
    {"challenger71","becaab4a5771d219c545a909527f225b0867729aff24c6bae350bd3493cf16e3"},
    {"challengern","90192b1f49b28b9a935e7f59d6f4bc2af4eaebc05268b8674f937ed367f9ac9d"},
    {"charger06","51642a6b823fb4c9332989b10873553ec8d225d72e7b30b79d3920ad2d490cba"},
    {"charger69","09e41dff7d6998748248571038240393a2b3594db9da9e14c827358c404553ec"},
    {"chevelle","f65b6fc44e4572cfcc89f0e54cf92dc665910f557cb2ab6b46ae5c467561d185"},
    {"corolla","63427325cbac15ad03c1bbd3fc30c332058581ce793a549871063c59643cd7c9"},
    {"corvettez06","419535f2c6519e36685828e2abe3f0b958d45cd658904fae31d6c3e4cba6e065"},
    {"cuda","65d1a081d3bfa7524955a10af41a4ac10e11cb4362758d2ac5804101e0d0b236"},
    {"eclipse","9446cf4483219f7e60d3468742feb2d4a3871254d96f2e64b65c4007ec4c71cf"},
    {"europa","714241dc6a29aa11b8eadb1651a236b6f59813727d6ff9f9f58f165a69d91b59"},
    {"g35","08cf2e94774951a46c1d43582093ac65587be723087d120dd2c5ddd3c303ca16"},
    {"jaguarxk","3ee0fd04ddc6cb5768055cbe5c5cbca072aa2ca35b2484d500fbb0d7a29132b5"},
    {"lancerevo9","ad9cb69b0b9873da5bc058ee6e18d0a900e173e00fd9130606a77e8d9140e76e"},
    {"mazdaspeed3","32fe405f6f856f5f47d9789d95b0cac67d7026fd815709e620cc25ee4df3135a"},
    {"mr2","d83dbcf10b0c667d1211f4414171751ae593b983a8cd4a4bfef763519a446ae5"},
    {"murcielago640","a54249add9bc3ada995b52d128186e7a35e615f674116da53db6d867c11a1647"},
    {"mustangshlbyn","77171892255496bfb032110a12d968ca0b381dad445ff0e64de023f43c974e3a"},
    {"mustangshlbyo","5bf23f8a22f5a151adefa9c34d878e2c1e163a2369ca264d1b5c2fcdd982ccda"},
    {"r32","0da4b92d401f0bc1339fdfd82c6b6933e0d7727d0d481f272fb5d051e27ec6ab"},
    {"r8","10c2d964f0aadb018f318f2761e53cf6e652c36ef0b6d7e316c57766332b6d8c"},
    {"roadrunner","52978701b7b13838ae2aadfd22c500633ae1c8b80113d46843f234fd5d3ce959"},
    {"skyline","eb0ecd9ca7b6d9104a9283a063ac0cdb43a354ccda3bde403dd947a46cba24dd"},
    {"zonda","56dabea84ff2feb8edca601d68e23de63a8b66b2c45c4d289075dd4243003346"},
};
static const struct { const char *car, *sha; } carbon_x360_packages[] = {
    {"240sx","09f8cfc744202d6af67aff6b49f5240341f0d94a3debe0ae8ed558f53d0f14df"},
    {"997gt3rs","d9f186aba6f20c9e1b2db10d09d2c526673083e5e88492581ec6302550772176"},
    {"997tt","43fe5509e4172c5ae7cfd06ec6d8576f2f6124413e327b81583b055791dcd5fe"},
    {"brera","126642a5789995fca847e3e321ad2296611451d0c6e2c32a9403025cac790044"},
    {"camaron","13cfa5b3b67cd84ed3222e6f2704c6defbb16148cdbadc930967d0e797785072"},
    {"ccx","59982d1521f3ccf18c1c137b2f8fafb65a8ceff8b5901edc6c68f6cf2ada187b"},
    {"corvettez06","e3431aafbd83c72d81042a9dc6e8a3a551975b231d18c1279ca2c235415bc3b4"},
    {"europa","6ae195ad870d10049b974f9325c1c1ba220b16fe75ac2f7afd57415536b12d85"},
    {"g35","9dcd6843e5d26beb7b46fb409efddbd9919b19823330ae554cf2f8a0c982c663"},
    {"jaguarxk","5d35beb8f823eee5d5a5318d0660a47ec747f7d497d25b57b095153b76b45ee2"},
    {"mazdaspeed3","e5c8bb91248b2389cf2f74f2d0aa2ad251a620c2aec43ae3b1512bf729f56c58"},
    {"mr2","d6821a26d5910de2c40582805a650f8f5189b8eac8331017050eb285a9561aae"},
    {"r32","214032468863cffbca72156fb019ce43d59fab8581afc32fcaf5d879b0d2370e"},
    {"r8","7684f1aec405770755e2680b6c7a4d391caf429b413087a1a408f337740d8556"},
};
static const char *package_pin(const char *name,uint32_t version) {
    if(version==4 && !strcasecmp(name,"veyron"))
        return "471356c3aa5a0ba5c561c887ed61db3ca031388d22ff86d51faf6bd98c1925a7";
    if(version==1 && !strcasecmp(name,"jaguarxk"))
        return "66b587fa9e179b69180bcae26c8111d4b94c96dabc2a949f8e262c0289e2c02c";
    if(version==2) for(size_t i=0;i<sizeof carbon_pc_packages/sizeof *carbon_pc_packages;i++)
        if(!strcasecmp(name,carbon_pc_packages[i].car)) return carbon_pc_packages[i].sha;
    if(version==3) for(size_t i=0;i<sizeof carbon_x360_packages/sizeof *carbon_x360_packages;i++)
        if(!strcasecmp(name,carbon_x360_packages[i].car)) return carbon_x360_packages[i].sha;
    return NULL;
}

static int prepare_samples(Resource *r) {
    r->samples=calloc(r->sample_count?r->sample_count:1,sizeof(*r->samples));
    if(!r->samples) return -1;
    X360Abk bank;
    char why[128];
    if(r->translated && (!x360abk_parse_carbon(r->source,r->size,&bank,why,sizeof why) ||
                         bank.count!=r->sample_count)) return -1;
    const uint8_t *pcm=r->sample_source;
    for(uint32_t j=0;j<r->sample_count;j++) {
        uint32_t frames=rd(pcm),rate=rd(pcm+4),loop=rd(pcm+8),n=rd(pcm+12);
        pcm+=16;
        int16_t *decoded=NULL;
        if(r->translated) {
            const X360AbkSound *s=bank.s+j;
            unsigned count;
            if(frames!=s->samples || rate!=s->rate || loop!=(s->loop?s->loop_start+1:0) ||
               (frames && !x360abk_decode_carbon(r->source,s,&decoded,&count,why,sizeof why))) return -1;
        }
        if(frames) {
            float *samples=malloc((size_t)frames*sizeof(float));
            if(!samples) { free(decoded); return -1; }
            for(uint32_t k=0;k<frames;k++) {
                int sample;
                if(decoded) sample=decoded[k];
                else {
                    unsigned u=(unsigned)pcm[2*k]|(unsigned)pcm[2*k+1]<<8;
                    sample=u<=32767?(int)u:(int)u-65536;
                }
                samples[k]=(float)sample*(1.0f/32768.0f);
            }
            free(decoded);
            r->samples[j]=(CarAudioPCM){samples,frames,(float)rate,loop!=0,NULL,NULL,loop?loop-1:0};
            r->pcm_bytes+=(size_t)frames*sizeof(float);
        }
        pcm+=n;
    }
    return 0;
}

static int prepare_bank(Resource *r,unsigned layer) {
    if(!r->bank.source) {
        if(!backend.voices && car_audio_backend_init(&backend,voices,512,44100,resolve,NULL)) return -1;
        if(plan(r) || prepare_samples(r)) goto fail;
    }
    return car_audio_engine_class_matches(layer?0:1,&r->modules[0].interface)?0:-1;
fail:
    clear_bank(r); return -1;
}

int car_audio_load_stream(uint32_t key,uint64_t vault,const char *name,unsigned expected_version,FILE *f) {
    if(!car_audio_enabled()) return 0;
    if(!key || !name || !f || !*name || strlen(name)>=sizeof packages[0].name) return -1;
    Package *p=NULL;
    for(unsigned i=0;i<MAX_PACKAGES;i++) {
        if(packages[i].key==key && packages[i].vault==vault)
            return !packages[i].retiring && !strcasecmp(packages[i].name,name) &&
                   (!expected_version || packages[i].version==expected_version)?1:-1;
        if(!packages[i].key && !p) p=packages+i;
    }
    if(!p) return -1;
    uint8_t header[68];
    if(fread(header,1,sizeof header,f)!=sizeof header || memcmp(header,"A7CA",4)) return -1;
    uint32_t version=rd(header+4), size=rd(header+8);
    const char *pin=package_pin(name,version);
    if(!pin || (expected_version && version!=expected_version) ||
       size<sizeof header || size>256*1024*1024) return -1;
    p->size=size; p->bytes=malloc(size);
    if(!p->bytes) goto fail;
    memcpy(p->bytes,header,sizeof header);
    if(fread(p->bytes+sizeof header,1,size-sizeof header,f)!=size-sizeof header ||
       fgetc(f)!=EOF || ferror(f)) goto fail;
    const uint8_t *b=p->bytes;
    size_t name_size=strlen(name);
    if(strncasecmp((const char *)b+20,name,name_size)) goto fail;
    for(size_t i=name_size;i<sizeof p->name;i++) if(b[20+i]) goto fail;
    uint8_t sha[32]; digest(b+68,p->size-68,sha); if(memcmp(sha,b+36,32)) goto fail;
    digest(b,p->size,p->digest);
    char package_id[65]; hex(p->digest,package_id);
    if(strcmp(package_id,pin)) goto fail;

    Reader in={b+68,p->size-68,0};
    uint32_t resources=rd(b+12),layout=rd(b+16);
    if(!resources || resources>MAX_RESOURCES) goto fail;
    p->count=resources;
    if(version==4) {
        if(layout!=1 || resources!=4) goto fail;
        p->tier_count=1;
        for(unsigned k=0;k<2;k++) {
            p->tiers[0].layer[k][0]=word(&in);
            if(p->tiers[0].layer[k][0]>=p->count) goto fail;
        }
        const uint8_t *rpm=take(&in,16);
        if(!rpm || !take(&in,32)) goto fail;
        for(unsigned k=0;k<2;k++) {
            p->minimum_rpm[k]=rf(rpm+8*k); p->maximum_rpm[k]=rf(rpm+8*k+4);
            if(!isfinite(p->minimum_rpm[k]) || !isfinite(p->maximum_rpm[k]) ||
               p->minimum_rpm[k]<=0 || p->minimum_rpm[k]>=p->maximum_rpm[k]) goto fail;
        }
    } else if(version==1) {
        if(layout>4*1024*1024 || !take(&in,layout)) goto fail;
        p->tier_count=1;
    } else {
        p->tier_count=layout; p->max_level=word(&in); p->default_level=word(&in);
        if(!layout || layout>MAX_TIERS || p->default_level>p->max_level || p->max_level>4) goto fail;
    }
    for(unsigned tier=0;version!=4 && tier<p->tier_count;tier++) for(unsigned k=0;k<2;k++) {
        Tier *t=p->tiers+tier;
        const uint8_t *cal=t->calibration[k]=take(&in,CALIBRATION);
        if(!cal || !isfinite(rf(cal+0x120)) || !isfinite(rf(cal+0xe8)) || rd(cal+0x124)>99) goto fail;
        for(unsigned j=0;j<3;j++) {
            t->layer[k][j]=word(&in); if(t->layer[k][j]>=p->count) goto fail;
        }
    }
    for(unsigned i=0;i<p->count;i++) {
        Resource *r=p->resource+i;
        r->kind=word(&in); r->size=word(&in); r->plan_size=word(&in); r->sample_count=word(&in);
        const uint8_t *d=take(&in,32); if(!d) goto fail; memcpy(r->digest,d,32);
        if((r->kind!=1 && r->kind!=2 && !(version==4 && r->kind==3)) || r->size>16*1024*1024 ||
           r->sample_count>4096 || r->plan_size>1024*1024) goto fail;
        r->source=take(&in,r->size); r->plan=take(&in,r->plan_size); r->sample_source=in.p;
        if(!r->source || !r->plan) goto fail;
        digest(r->source,r->size,sha); if(memcmp(sha,r->digest,32)) goto fail;
        if(r->kind==1 && (r->size<40 || memcmp(r->source,"Gnsu20",6) || r->plan_size || r->sample_count)) goto fail;
        if(r->kind==2 && (r->size<0x60 || memcmp(r->source,"ABKC",4))) goto fail;
        if(version==4 && (r->plan_size || r->sample_count>(r->kind==3?6u:0u))) goto fail;
        if(version==3 && r->kind==2) {
            Reader exec={r->plan,r->plan_size,0};
            r->translated_size=word(&exec);
            r->translated=take(&exec,r->translated_size);
            if(!r->translated || r->translated_size<0x60 || r->translated_size>256*1024 ||
               !exec.n || !r->sample_count) goto fail;
            r->plan=exec.p; r->plan_size=(uint32_t)exec.n;
        }
        for(uint32_t j=0;j<r->sample_count;j++) {
            uint32_t frames=word(&in),rate=word(&in),loop=word(&in),n=word(&in);
            if(frames>10000000 || n!=(version==3?0:(uint64_t)frames*2) || loop>frames ||
               rate!=(frames?(version==3 || version==4?48000u:44100u):0u) || !take(&in,n)) goto fail;
        }
    }
    if(in.failed || in.n) goto fail;
    for(unsigned tier=0;tier<p->tier_count;tier++) for(unsigned k=0;k<2;k++) {
        const uint32_t *ids=p->tiers[tier].layer[k];
        Resource *r=p->resource+ids[0];
        if(version==4) {
            if(r->kind!=3 || car_audio_tmx_parse(p->tmx+k,r->source,r->size) ||
               r->sample_count!=p->tmx[k].snr_count) goto fail;
            const uint8_t *pcm=r->sample_source;
            for(unsigned j=0;j<p->tmx[k].track_count;j++) if(!p->tmx[k].tracks[j].gin) {
                if(rd(pcm)!=p->tmx[k].tracks[j].frames || rd(pcm+8)!=p->tmx[k].tracks[j].loop) goto fail;
                pcm+=16+rd(pcm+12);
            }
            continue;
        }
        if(r->kind!=2 || !r->plan_size || p->resource[ids[1]].kind!=1 || p->resource[ids[2]].kind!=1) goto fail;
    }
    p->key=key; p->vault=vault; p->version=version;
    if(version==4 && (!p->tmx[0].gin_count || !p->tmx[1].gin_count ||
       p->tmx[0].gin_count+p->tmx[1].gin_count>MAX_GINSU)) goto fail;
    memcpy(p->name,b+20,sizeof p->name);
    if(diag_on) diag_event("addcar_audio: loaded car=%s key=%08X vault=%llu bytes=%zu sha256=%s resources=%u tiers=%u platform=%s",
        p->name,key,(unsigned long long)vault,p->size,package_id,p->count,p->tier_count,version==3?"x360":"pc");
    return 1;
fail:
    clear_package(p); return -1;
}
int car_audio_load(uint32_t key,uint64_t vault,const char *name,const char *filename) {
    if(!car_audio_enabled()) return 0;
    if(!filename) return -1;
    FILE *f=fopen(filename,"rb"); if(!f) return errno==ENOENT?0:-1;
    int result=car_audio_load_stream(key,vault,name,0,f);
    fclose(f); return result;
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
    Package *selected=NULL;
    for(unsigned i=0;i<MAX_PACKAGES;i++) {
        Package *p=packages+i;
        if(key && p->key==key && !p->retiring && (!selected || p->vault>selected->vault)) selected=p;
    }
    return selected;
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
    const uint8_t *cal=e->tier->calibration[layer];
    controls[0]=ri(cal+0x124); controls[12]=1;
    if(car_audio_engine_class_rpm(rpm,rf(cal+0x120),rf(cal+0xe8),controls+1) ||
       car_audio_engine_class_pitch(MF32(object+0x3c),controls+16)) return -1;
    controls[2]=controls[3]; controls[4]=controls[5];
    controls[26]=controls[13]; controls[27]=controls[14]; controls[28]=controls[15]; controls[29]=controls[21];
    return car_audio_engine_class_inputs(out,controls,30,e->tier->calibration[1],CALIBRATION,
        e->tier->calibration[0],CALIBRATION);
}
static int start_ginsu(Cpu *c,Engine *e) {
    unsigned count=ginsu_count(e); int tmx=e->package->version==4;
    if(!free_native_voices(count)) return -1;
    uint32_t packets=8,overhead=native_call(c,0x0081dafd,0,1,&packets);
    if(overhead<0x9c || overhead>65536) return -1;
    for(unsigned k=0;k<count;k++) {
        unsigned layer=k/2,index=0;
        const CarAudioTmxTrack *track=NULL;
        if(tmx) {
            layer=k<e->package->tmx[0].gin_count?0:1;
            unsigned remaining=layer?k-e->package->tmx[0].gin_count:k;
            for(index=0;index<e->package->tmx[layer].track_count;index++)
                if(e->package->tmx[layer].tracks[index].gin && !remaining--) break;
            track=e->package->tmx[layer].tracks+index;
        }
        e->ginsu[k].layer=layer; e->ginsu[k].track=index;
        Resource *r=e->package->resource+e->tier->layer[layer][tmx?0:k%2+1];
        uint32_t size=track?track->size:r->size;
        char pack[65],resource[65],name[192];
        hex(e->package->digest,pack); hex(r->digest,resource);
        snprintf(name,sizeof name,"SOUND/ADDCAR_AUDIO/%s/%s",pack,resource);
        int fd=car_audio_open(name); if(fd<0) return -1;
        if(track && lseek(fd,track->offset,SEEK_SET)!=(off_t)track->offset) { close(fd); return -1; }
        e->ginsu[k].gin=gmalloc_try(size);
        if(!e->ginsu[k].gin) { close(fd); return -1; }
        size_t pos=0;
        while(pos<size) {
            ssize_t n=read(fd,G+e->ginsu[k].gin+pos,size-pos);
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
        uint32_t rpm=float_word(rf(G+gin+8));
        e->ginsu[k].voice=(int32_t)native_call(c,0x004e8ca0,e->ginsu[k].synth,1,&rpm);
        if(e->ginsu[k].voice<0) return -1;
        if(tmx) {
            uint32_t mute[]={(uint32_t)e->ginsu[k].voice,0}; native_call(c,0x0081d7ef,0,2,mute);
        }
        if(tmx && diag_on) {
            uint8_t sha[32]; char id[65]; digest(G+gin,size,sha); hex(sha,id);
            diag_event("addcar_audio: tmx recording car=%s layer=%u track=%u sha256=%s",
                e->package->name,layer,index,id);
        }
    }
    return 0;
}
static void sync_ginsu_volume(Cpu *c,Engine *e) {
    int tmx=e->package->version==4;
    if(!e->native_ginsu && !tmx) return;
    uint32_t spatial=M32(e->object+0x10),state=M32(e->object+0x40);
    float changed=e->mix_gain?(float)engine_mix_gain(e)/e->mix_gain:0;
    float source_gain=tmx?tmx_controller_gain(e)*fminf(1,changed):0;
    for(unsigned k=0;k<ginsu_count(e);k++) {
        int32_t gain=state?(int32_t)M32(state+0x3c+4*(k%2)):0;
        int32_t authored=tmx?0:ri(e->tier->calibration[k/2]+0x128)&65535;
        uint32_t base=spatial && engine_active(e) && !e->recovering?M32(spatial+4)&32767:0;
        int32_t level=tmx?(int32_t)(e->tmx_gains[e->ginsu[k].layer][e->ginsu[k].track]*base*(127.0f/32767)*source_gain):
            (int32_t)((int64_t)gain*base*authored/(32767LL*32767*256));
        uint32_t volume[]={(uint32_t)e->ginsu[k].voice,(uint32_t)(level<0?0:level>127?127:level)};
        native_call(c,0x0081d7ef,0,2,volume);
    }
}
static int recover_ginsu(Cpu *c,Engine *e) {
    if(!e->native_ginsu && e->package->version!=4) return 0;
    for(unsigned k=0;k<ginsu_count(e);k++) if(!voice_alive(c,e->ginsu[k].voice)) {
        if(!free_native_voices(1)) return 1;
        float frequency=e->package->version==4?e->package->minimum_rpm[e->ginsu[k].layer]:MF32(e->object+0x84);
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
static int update_tmx(Cpu *c,Engine *e) {
    uint32_t object=e->object,member=M32(object+0x28),rpm_object=M32(object+0x38);
    if(!member || !rpm_object) return -1;
    Cpu saved=*c; c->ecx=rpm_object;
    call_guest(c,M32(M32(rpm_object)+0x44),0,NULL);
    double normalized=c->st[c->top]; *c=saved;
    float multiplier=MF32(object+0x3c);
    /* Both constructors run before the first update writes the multiplier
     * (0 at attach in a live race); the Carbon path accepts it the same way. */
    if(!isfinite(normalized) || !isfinite(multiplier) || multiplier<0) return -1;
    Package *p=e->package;
    /* MW exports its live throttle at w3 in 0..1024. The source TMX uses
     * normalized load/accelerator controls at input 2/4; both follow that
     * observation here, as the Carbon adapter's target/current throttle does. */
    float throttle=fmaxf(0,fminf(1,(int32_t)M32(member+0x10)*(1.0f/1024)));
    const CarAudioPlayerBackend *callbacks=car_audio_backend_callbacks();
    uint32_t pan=M32(member+0x40),dry=M32(member+0x38),wet=M32(member+0x3c),filter=M32(member+0x58);
    uint32_t spatial=M32(object+0x10);
    float gain=spatial?(M32(spatial+4)&32767)*(1.0f/32767)*tmx_controller_gain(e):0;
    unsigned gin=0;
    for(unsigned layer=0;layer<2;layer++) {
        float rpm=(float)(p->minimum_rpm[layer]+(p->maximum_rpm[layer]-p->minimum_rpm[layer])*(normalized-1000)/9000);
        rpm=fmaxf(p->minimum_rpm[layer],fminf(p->maximum_rpm[layer],rpm));
        float input[5]={rpm,multiplier,throttle,0,throttle};
        Resource *r=p->resource+e->tier->layer[layer][0];
        if(car_audio_tmx_gains(p->tmx+layer,input,e->tmx_gains[layer])) return -1;
        unsigned sample=0;
        for(unsigned track=0;track<p->tmx[layer].track_count;track++) {
            const CarAudioTmxTrack *recording=p->tmx[layer].tracks+track;
            if(!recording->gin) {
                int32_t *voice=e->resident[layer]+track;
                if(!callbacks->status(&backend,*voice)) {
                    callbacks->release(&backend,*voice);
                    if(car_audio_backend_stock(&backend,r->samples+sample,50,voice)) return -1;
                }
                callbacks->option(&backend,*voice,0,(int32_t)fminf(65535,fminf(12,rpm/recording->frequency)*multiplier*4096));
                callbacks->option(&backend,*voice,2,(int32_t)(e->tmx_gains[layer][track]*gain*32767));
                callbacks->option(&backend,*voice,3,(int32_t)pan);
                callbacks->option(&backend,*voice,5,(int32_t)wet);
                callbacks->option(&backend,*voice,6,(int32_t)filter);
                callbacks->option(&backend,*voice,8,(int32_t)(32767+dry));
                if(diag_on && !e->diag_started) {
                    uint8_t sha[32]; char id[65];
                    digest(r->source+recording->offset,recording->size,sha); hex(sha,id);
                    diag_event("addcar_audio: tmx resident car=%s layer=%u track=%u sha256=%s rate=%u frames=%u",
                        p->name,layer,track,id,recording->rate,recording->frames);
                }
                sample++; continue;
            }
            unsigned k=gin++; uint32_t voice=(uint32_t)e->ginsu[k].voice;
            uint32_t angle[]={voice,pan,0}; native_call(c,0x0081e557,0,3,angle);
            uint32_t pitch[]={voice,(uint32_t)fmax(0,fmin(65535,4096.0*multiplier))};
            native_call(c,0x0081e4be,0,2,pitch);
            uint32_t cutoff[]={voice,filter}; native_call(c,0x0081e469,0,2,cutoff);
            uint32_t send[]={voice,(32767+dry)>>8}; native_call(c,0x0081e3cb,0,2,send);
            uint32_t reverb[]={voice,0,wet>>8}; native_call(c,0x0081e318,0,3,reverb);
            uint32_t frequency[]={float_word(rpm),float_word(60)};
            native_call(c,0x004e1cd0,e->ginsu[k].synth,2,frequency);
        }
    }
    e->diag_started=1;
    sync_ginsu_volume(c,e);
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
        if(package->version==4) for(unsigned j=0;j<6;j++) if(e->resident[i][j]>=0)
            car_audio_backend_callbacks()->release(&backend,e->resident[i][j]);
        car_audio_runtime_destroy(e->instances+i);
        if(e->state[i]) gfree(e->state[i]);
    }
    for(unsigned i=0;i<MAX_GINSU;i++) {
        if(e->ginsu[i].synth) {
            if(e->ginsu[i].constructed) { c->ecx=e->ginsu[i].synth; call_guest(c,0x004e8c50,0,NULL); }
            gfree(e->ginsu[i].synth);
        }
        if(e->ginsu[i].data) gfree(e->ginsu[i].data);
        if(e->ginsu[i].work) gfree(e->ginsu[i].work);
        if(e->ginsu[i].gin) gfree(e->ginsu[i].gin);
    }
    if(diag_on && replaced)
        diag_event("addcar_audio: detached car=%s object=%08X restore=%d mixed_nonzero_samples=%llu",
            package->name,object,restore,(unsigned long long)diag_nonzero_samples);
    *c=saved; memset(e,0,sizeof(*e));
    int active=0,borrowed=0;
    for(unsigned i=0;i<MAX_ENGINES;i++) { active|=engines[i].object!=0; borrowed|=engines[i].package==package; }
    if(!active) bridge_stop(c);
    if(restore && replaced) restore_stock(c,object,ginsu);
    if(package && package->retiring && !borrowed) clear_package(package);
    *c=saved;
}
void car_audio_unload(uint64_t vault) {
    for(unsigned i=0;i<MAX_PACKAGES;i++) {
        Package *p=packages+i;
        if(!p->key || p->vault!=vault) continue;
        p->retiring=1;
        /* Instances borrow bank bytes until their final destructor. */
        int borrowed=0;
        for(unsigned j=0;j<MAX_ENGINES;j++) borrowed|=engines[j].package==p;
        if(!borrowed) clear_package(p);
    }
}
static unsigned tier_for_level(const Package *p,int32_t level) {
    if(level<1) return 0;
    if((unsigned)level>p->max_level) level=(int32_t)p->max_level;
    unsigned tier=level<1?0:p->max_level==1?1:(unsigned)(level-1);
    return tier<p->tier_count?tier:p->tier_count-1;
}
static unsigned engine_tier(Cpu *c,uint32_t object,const Package *p) {
    if(p->tier_count<2) return 0;
    uint32_t state=M32(object+0x14),car=M32(state+0x34),handle=M32(car+0x234);
    int32_t level=(int32_t)p->default_level;
    uint32_t sim=handle?native_call(c,0x0041ad40,0,1,&handle):0;
    if(sim && M32(sim) && M32(M32(sim)+0x44)==0x00686a40) {
        /* MW PhysicsObject::GetAttributes is +38. Engine is put_maps type 4. */
        uint32_t args[]={sim+0x38,4};
        level=(int32_t)native_call(c,0x00672d30,0,2,args);
    }
    unsigned tier=tier_for_level(p,level);
#if !defined(NFS_PUBLIC) || !NFS_PUBLIC
    const char *forced=getenv("NFS_TEST_UPGRADE_INTERVAL");
    if(forced && forced[0]>='0' && forced[0]<'0'+(int)p->tier_count && !forced[1])
        tier=(unsigned)(forced[0]-'0');
#endif
    return tier;
}
static void init_engine(Cpu *c,uint32_t object,int ginsu) {
    if(!car_audio_enabled() || engine(object)) return;
    Package *p=for_object(object); if(!p) return;
    Engine *e=NULL; for(unsigned i=0;i<MAX_ENGINES;i++) if(!engines[i].object) { e=engines+i; break; }
    if(!e) return;
    unsigned tier=engine_tier(c,object,p);
    e->object=object; e->package=p; e->tier=p->tiers+tier;
    for(unsigned k=0;k<2;k++) for(unsigned j=0;j<6;j++) e->resident[k][j]=-1;
    e->mix_gain=engine_mix_gain(e);
    if(p->version==4) {
        if(!backend.voices && car_audio_backend_init(&backend,voices,512,44100,resolve,NULL)) goto fail;
        for(unsigned k=0;k<2;k++) {
            Resource *r=p->resource+e->tier->layer[k][0];
            if(!r->samples && prepare_samples(r)) goto fail;
        }
        if(bridge_start(c) || start_ginsu(c,e)) goto fail;
        e->native_ginsu=ginsu;
        if(update_tmx(c,e)) goto fail;
        goto replace;
    }
    for(unsigned k=0;k<2;k++) {
        Resource *r=p->resource+e->tier->layer[k][0]; int32_t words[100];
        if(prepare_bank(r,k) || inputs(c,e,k,words)) goto fail;
        e->state[k]=gmalloc_try(r->modules[0].size); if(!e->state[k]) goto fail;
        if(car_audio_runtime_create(&r->bank,0,e->instances+k,G+e->state[k],r->modules[0].size,e->state[k],words,100)) goto fail;
    }
    if(bridge_start(c) || (ginsu && start_ginsu(c,e))) goto fail;
    e->native_ginsu=ginsu;
replace:;
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
        if(p->version==4) {
            char engine_id[65],exhaust_id[65];
            hex(p->resource[e->tier->layer[0][0]].digest,engine_id);
            hex(p->resource[e->tier->layer[1][0]].digest,exhaust_id);
            diag_event("addcar_audio: attached car=%s object=%08X key=%08X engine=%s exhaust=%s native_ginsu=%u source=prostreet",
                p->name,object,p->key,engine_id,exhaust_id,ginsu_count(e));
            return;
        }
        char ids[2][3][65];
        size_t resident=p->size;
        for(unsigned i=0;i<p->count;i++) {
            const Resource *r=p->resource+i;
            resident+=r->pcm_bytes+r->bank.size;
        }
        for(unsigned k=0;k<2;k++) for(unsigned j=0;j<3;j++)
            hex(p->resource[e->tier->layer[k][j]].digest,ids[k][j]);
        diag_event("addcar_audio: attached car=%s object=%08X key=%08X tier=%u engine=%s exhaust=%s "
                   "engine_accel=%s engine_decel=%s exhaust_accel=%s exhaust_decel=%s resident_bank_bytes=%zu native_ginsu=%u",
            p->name,object,p->key,tier,ids[0][0],ids[1][0],ids[0][1],ids[0][2],ids[1][1],ids[1][2],resident,ginsu?4:0);
    }
    return;
fail:
    if(diag_on) diag_event("addcar_audio: attach failed car=%s tier=%u",p->name,tier);
    detach_engine(c,e,1);
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
    if(e->package->version==4) {
        if(update_tmx(c,e)) goto fail;
        car_audio_backend_tick(&backend);
        *c=saved; return;
    }
    for(unsigned k=0;k<2;k++) {
        int32_t words[100];
        if(inputs(c,e,k,words) || car_audio_runtime_update(e->instances+k,words,100)) goto fail;
        if(update_ginsu(c,e,words,k)) goto fail;
        CarAudioRuntimeInstance *previous=executing; executing=e->instances+k;
        int rc=car_audio_runtime_tick(executing,execute,c); executing=previous;
        if(rc || !e->instances[k].bank) goto fail;
    }
    car_audio_backend_tick(&backend);
    if(diag_on && !e->diag_started) {
        unsigned source=0, native=0;
        for(unsigned i=0;i<512;i++) source+=voices[i].owned && !voices[i].ended;
        uint32_t pool=M32(0x009c21f4); unsigned capacity=M16(0x009c218c);
        if(pool && capacity<=256)
            for(unsigned i=0;i<capacity;i++) native+=M8(pool+i*0x90+0x6d)!=0;
        if(source) {
            diag_event("addcar_audio: race voices car=%s native=%u source=%u",e->package->name,native,source);
            e->diag_started=1;
        }
    }
    *c=saved; return;
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
