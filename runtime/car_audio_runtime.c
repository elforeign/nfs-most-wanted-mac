#include "car_audio_runtime.h"
#include <stdlib.h>
#include <string.h>

static uint32_t rd(const uint8_t *p) {
    return (uint32_t)p[0] | (uint32_t)p[1]<<8 | (uint32_t)p[2]<<16 | (uint32_t)p[3]<<24;
}
static uint16_t r16(const uint8_t *p) { return (uint16_t)(p[0] | (unsigned)p[1]<<8); }
static int32_t si(uint32_t n) { return n<=INT32_MAX?(int32_t)n:-1-(int32_t)(UINT32_MAX-n); }
static void wr(uint8_t *p,uint32_t v) { for(unsigned i=0;i<4;i++) p[i]=(uint8_t)(v>>(8*i)); }
static int span(size_t size,size_t at,size_t n) { return at<=size && n<=size-at; }
static int overlap(const void *a,size_t n,const void *b,size_t m) {
    uintptr_t x=(uintptr_t)a,y=(uintptr_t)b;
    return x<=y ? y-x<n : x-y<m;
}
static uint32_t target(unsigned ordinal) {
    switch(ordinal) {
    case 0:return 0x8196c2; case 1:return 0x8196ce; case 4:return 0x8196e8;
    case 6:return 0x8199d1; case 7:return 0x819a0f; case 8:return 0x819a32;
    case 10:return 0x819b13; case 13:return 0x819bbe; case 15:return 0x819cc6;
    case 16:return 0x819dc6; case 17:return 0x819e57; case 21:return 0x819f0b;
    case 22:return 0x819f57; case 25:return 0x819f92; case 27:return 0x81a52c;
    case 28:return 0x81a61d; case 29:return 0x81a707; case 30:return 0x81a7a6;
    case 31:return 0x81a7cd; case 35:return 0x819f3a; case 38:return 0x819875;
    default:return 0;
    }
}
static int same(const CarAudioCsisInterface *a,const CarAudioCsisInterface *b) {
    return a->id && b->id && a->id_size==b->id_size && a->member_words==b->member_words &&
        a->kind==b->kind && !memcmp(a->id,b->id,a->id_size);
}
static const CarAudioRuntimeInterface *find(const CarAudioRuntimeInterface *refs,size_t n,uint32_t site) {
    for(size_t i=0;i<n;i++) if(refs[i].site==site) return refs+i;
    return NULL;
}
int car_audio_runtime_load(CarAudioRuntimeBank *b,const uint8_t *s,uint8_t *image,
    size_t size,uint32_t base,uint64_t identity,const CarAudioRuntimeModule *mods,size_t nm,
    const CarAudioRuntimeInterface *refs,size_t nr,const CarAudioCsisInterface *reg,size_t nt,
    CarAudioBackend *backend,const CarAudioRuntimeClasses *classes) {
    if(!b || b->source || !s || !image || size<0x60 || size>16*1024*1024 ||
       base>UINT32_MAX-size || !mods || !nm || nm>1024 || !refs || !nr || nr>65536 ||
       !backend || !backend->voices || !classes || !classes->create || !classes->update ||
       !classes->release || overlap(s,size,image,size) || memcmp(s,"ABKC",4) ||
       rd(s+0x14)!=size || r16(s+10)!=nm) return -1;
    uint32_t table=rd(s+0x1c),end=rd(s+0x18),f[3]={rd(s+0x30),rd(s+0x34),rd(s+0x38)};
    if(table<0x5c || table>=end || end>size || f[0]<end || f[0]>=f[1] ||
       f[1]>=f[2] || !span(size,f[2],4)) return -1;
    /* A byte map prevents every overlapping/duplicate relocation, including
       partial overlaps between pointer, import and eight-byte CSIS writes. */
    uint8_t *used=calloc(end,1); if(!used) return -1;
    int error=-1;
    for(unsigned k=0;k<3;k++) {
        uint32_t n=rd(s+f[k]),stride=k==2?12:4;
        size_t limit=k==2?size:f[k+1];
        if(n>65536 || !span(limit,f[k]+4,(size_t)n*stride) || (k==2 && n!=nr)) goto fail;
        for(uint32_t i=0;i<n;i++) {
            uint32_t row=f[k]+4+i*stride,at=rd(s+row),width=k==2?8:4;
            if(at<table || !span(end,at,width)) goto fail;
            for(unsigned j=0;j<width;j++) { if(used[at+j]) goto fail; used[at+j]=1; }
            if(k==0 && !target(rd(s+at))) goto fail;
            if(k==1 && (rd(s+at)<table || rd(s+at)>=end)) goto fail;
            if(k==2) {
                const CarAudioRuntimeInterface *ref=find(refs,nr,at);
                uint32_t name=rd(s+row+4); size_t index;
                if(!ref || car_audio_csis_execution_resolve(&ref->interface,reg,nt,&index) ||
                   name<f[k]+4+n*12 || !span(size,name,ref->interface.id_size) ||
                   (uint8_t)rd(s+row+8)!=ref->interface.kind ||
                   memcmp(s+name,ref->interface.id,ref->interface.id_size)) goto fail;
            }
        }
    }
    for(size_t i=0;i<nm;i++) {
        const CarAudioRuntimeModule *m=mods+i;
        if(m->descriptor!=table || !span(end,table,60) || m->player_count>255 || m->control_count>255 ||
           (m->player_count && !m->players) || (m->control_count && !m->controls) ||
           !span(end,table+60,4*(m->player_count+m->control_count)) ||
           s[table+36]!=m->player_count || s[table+39]!=m->control_count ||
           rd(s+table+40)!=m->entry || rd(s+table+44)!=m->state ||
           rd(s+table+48)!=m->size || rd(s+table+52)!=m->destroy_offset ||
           m->entry<table+60 || m->entry>=m->state || !span(end,m->state,m->size) ||
           !span(m->size,m->destroy_offset,16) ||
           !span(m->size,m->member_offset,4*m->interface.member_words)) goto fail;
        uint32_t member_at=24+20*s[table+37]+28*r16(s+table+32);
        if(s[table+37]>1 || s[table+38]!=1 || m->member_offset!=member_at+20 ||
           !span(m->size,member_at,20) ||
           s[m->state+member_at+16]!=m->interface.member_words) goto fail;
        const CarAudioRuntimeInterface *ref=find(refs,nr,table+4);
        if(!ref || !same(&ref->interface,&m->interface)) goto fail;
        for(size_t j=0;j<m->player_count;j++) {
            const CarAudioRuntimePlayerPlan *p=m->players+j;
            if(rd(s+table+60+4*j)!=p->offset || !span(m->size,p->offset,p->size) ||
               p->size<28 || !span(end,p->selector,4) ||
               rd(s+m->state+p->offset+4)!=p->selector ||
               !span(end,p->selector+4,(size_t)rd(s+p->selector)*12)) goto fail;
            CarAudioPlayer probe={0};
            /* Validate using a private copy; a failed plan cannot write image. */
            uint8_t *copy=malloc(p->size); if(!copy) goto fail;
            memcpy(copy,s+m->state+p->offset,p->size);
            int rc=car_audio_player_attach(&probe,copy,p->size,s+p->selector,
                4+(size_t)rd(s+p->selector)*12,identity,car_audio_backend_callbacks(),backend);
            if(!rc) car_audio_player_detach(&probe);
            free(copy); if(rc) goto fail;
        }
        for(size_t j=0;j<m->control_count;j++) {
            uint32_t off=m->controls[j];
            if(rd(s+table+60+4*(m->player_count+j))!=off || !span(m->size,off,16)) goto fail;
            const uint8_t *c=s+m->state+off;
            ref=find(refs,nr,m->state+off);
            if(!ref || ref->interface.member_words!=1 ||
               strcmp((const char *)ref->interface.id+4,"CAR_SputOutput") || c[12]>1 ||
               !span(m->size,off+16,8+(size_t)(c[13]?c[13]:1)*4+(c[12]?(size_t)c[13]*8:0)) || rd(c+8)) goto fail;
        }
        table+=60+4*(uint32_t)(m->player_count+m->control_count);
    }
    memcpy(image,s,size);
    for(unsigned k=0;k<3;k++) for(uint32_t i=0;i<rd(s+f[k]);i++) {
        uint32_t row=f[k]+4+i*(k==2?12:4),at=rd(s+row);
        if(k==0) wr(image+at,target(rd(s+at))-(base+at+4));
        else if(k==1) wr(image+at,base+rd(s+at));
        else { size_t index=0; const CarAudioRuntimeInterface *ref=find(refs,nr,at);
            (void)car_audio_csis_execution_resolve(&ref->interface,reg,nt,&index);
            wr(image+at,(uint32_t)index+1); wr(image+at+4,r16(ref->interface.id+2)); }
    }
    *b=(CarAudioRuntimeBank){.source=s,.image=image,.size=size,.base=base,.identity=identity,
        .modules=mods,.module_count=nm,.backend=backend,.classes=*classes};
    error=0;
fail: free(used); return error;
}
int car_audio_runtime_create(CarAudioRuntimeBank *b,size_t module,CarAudioRuntimeInstance *p,
    uint8_t *state,size_t bytes,uint32_t address,const int32_t *words,size_t count) {
    if(!b || !b->source || module>=b->module_count || !p || p->bank || !state) return -1;
    const CarAudioRuntimeModule *m=b->modules+module;
    if(bytes!=m->size || address>UINT32_MAX-bytes || !words || count!=m->interface.member_words ||
       ((uint64_t)address<(uint64_t)b->base+b->size && (uint64_t)b->base<(uint64_t)address+bytes) ||
       overlap(state,bytes,b->source,b->size) || overlap(state,bytes,b->image,b->size)) return -1;
    for(CarAudioRuntimeInstance *q=b->instances;q;q=q->next)
        if(overlap(state,bytes,q->state,q->module->size) ||
           ((uint64_t)address<(uint64_t)q->address+q->module->size &&
            (uint64_t)q->address<(uint64_t)address+bytes)) return -1;
    CarAudioPlayer *players=calloc(m->player_count?m->player_count:1,sizeof(*players));
    CarAudioRuntimeControl *controls=calloc(m->control_count?m->control_count:1,sizeof(*controls));
    if(!players || !controls) { free(players); free(controls); return -1; }
    *p=(CarAudioRuntimeInstance){.bank=b,.module=m,.state=state,.address=address,
        .players=players,.controls=controls,.next=b->instances};
    b->instances=p;
    memcpy(state,b->image+m->state,bytes);
    for(size_t j=0;j<m->control_count;j++) {
        uint32_t off=m->controls[j];
        controls[j]=(CarAudioRuntimeControl){.offset=off,.count=state[off+13],.clamp=state[off+12]};
    }
    for(size_t j=0;j<m->player_count;j++) {
        const CarAudioRuntimePlayerPlan *plan=m->players+j;
        size_t size=4+(size_t)rd(b->source+plan->selector)*12;
        if(car_audio_player_attach(players+j,state+plan->offset,plan->size,b->source+plan->selector,
            size,b->identity,car_audio_backend_callbacks(),b->backend)) goto fail;
    }
    for(unsigned kind=0;kind<2;kind++) if(car_audio_engine_class_matches(kind,&m->interface)) {
        if(car_audio_engine_class_construct(&p->engine,kind,&m->interface,state+m->member_offset,
            400,words,count)) goto fail;
        return 0;
    }
    for(size_t i=0;i<count;i++) wr(state+m->member_offset+4*i,(uint32_t)words[i]);
    return 0;
fail: car_audio_runtime_destroy(p); return -1;
}
int car_audio_runtime_update(CarAudioRuntimeInstance *p,const int32_t *words,size_t count) {
    if(!p || !p->bank || p->destroying || !words || count!=p->module->interface.member_words) return -1;
    if(p->engine.members) return car_audio_engine_class_update(&p->engine,words,count);
    for(size_t i=0;i<count;i++) wr(p->state+p->module->member_offset+4*i,(uint32_t)words[i]);
    return 0;
}
static void detach(CarAudioRuntimeInstance *p) {
    car_audio_player_destroy(p->players,p->module->player_count);
    for(size_t i=0;i<p->module->control_count;i++) {
        CarAudioRuntimeControl *c=p->controls+i;
        if(c->object) p->bank->classes.release(p->bank->classes.context,c->object);
        c->object=NULL; wr(p->state+c->offset+8,0);
    }
    car_audio_engine_class_destroy(&p->engine);
    p->destroying=1;
}
int car_audio_runtime_import(CarAudioRuntimeInstance *p,unsigned ordinal,uint32_t address,uint32_t *result) {
    if(!p || !p->bank || !result || address<p->address || address-p->address>=p->module->size) return -1;
    uint32_t off=address-p->address; *result=0;
    if(ordinal==4) {
        if(off!=p->module->destroy_offset) return -1;
        if(rd(p->state+off+12)) detach(p);
        return 0;
    }
    if(p->destroying) return -1;
    if(ordinal==27) for(size_t i=0;i<p->module->player_count;i++) {
        if(off!=p->module->players[i].offset) continue;
        int rc=car_audio_player_update(p->players+i);
        if(rc<0) return rc;
        *result=(uint32_t)rc; return 0;
    }
    if(ordinal==38) for(size_t i=0;i<p->module->control_count;i++) {
        CarAudioRuntimeControl *c=p->controls+i;
        if(off!=c->offset) continue;
        uint8_t *s=p->state+off;
        if(s[12]!=c->clamp || s[13]!=c->count) return -1;
        size_t at=16+(c->clamp?8*c->count:0);
        if(rd(s+at+4)) {
            if(c->object) p->bank->classes.release(p->bank->classes.context,c->object);
            c->object=NULL;
        } else if((rd(s+at) && !c->object) || (!rd(s+at) && c->object)) {
            if(c->clamp) for(unsigned j=0;j<c->count;j++) {
                int32_t v=si(rd(s+at+8+4*j)),lo=si(rd(s+16+8*j)),hi=si(rd(s+20+8*j));
                if(lo>hi) return -1;
                wr(s+at+8+4*j,(uint32_t)(v<lo?lo:v>hi?hi:v));
            }
            int32_t value=si(rd(s+at+8));
            if(c->object) p->bank->classes.update(p->bank->classes.context,c->object,&value,1);
            else if(p->bank->classes.create(p->bank->classes.context,&value,1,&c->object)<0 || !c->object) {
                if(c->object) p->bank->classes.release(p->bank->classes.context,c->object);
                c->object=NULL; return -1;
            }
        }
        wr(s+8,c->object?(uint32_t)i+1:0); *result=c->object!=NULL; return 0;
    }
    return -1;
}
int car_audio_runtime_tick(CarAudioRuntimeInstance *p,CarAudioRuntimeExecute execute,void *context) {
    if(!p || !p->bank || p->destroying || !execute) return -1;
    int rc=execute(context,p->bank->base+p->module->entry,p->address+24);
    if(rc || p->destroying) car_audio_runtime_destroy(p);
    return rc;
}
void car_audio_runtime_destroy(CarAudioRuntimeInstance *p) {
    if(!p || !p->bank) return;
    detach(p);
    CarAudioRuntimeInstance **link=&p->bank->instances;
    while(*link && *link!=p) link=&(*link)->next;
    if(*link) *link=p->next;
    free(p->players); free(p->controls); memset(p,0,sizeof(*p));
}
void car_audio_runtime_unload(CarAudioRuntimeBank *b) {
    if(!b) return;
    while(b->instances) car_audio_runtime_destroy(b->instances);
    memset(b,0,sizeof(*b));
}
