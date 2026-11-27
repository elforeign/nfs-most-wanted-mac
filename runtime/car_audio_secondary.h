/* Private implementation included by car_audio.c after its engine services.
 * CARSFX_Shift retains its native event decisions and clamped member buffers.
 * Only the three decoded ClassHandle creates are redirected while that car's
 * UpdateParams is on the stack. Tokens never enter the native CSIS registry.
 */
typedef struct {
    uint32_t token, state;
    Engine *owner;
    CarAudioRuntimeInstance instance;
    unsigned words, kind, event_identity;
} Secondary;
static Secondary secondary[512];
static unsigned secondary_tokens;
static _Thread_local Engine *secondary_owner;

static Secondary *secondary_token(uint32_t token) {
    if(token && secondary_tokens) for(unsigned i=0;i<512;i++) if(secondary[i].token==token) return secondary+i;
    return NULL;
}
static void secondary_end(Secondary *s) {
    car_audio_runtime_destroy(&s->instance);
    if(s->state) gfree(s->state);
    s->state=0; s->owner=NULL;
}
static void secondary_destroy(Engine *e) {
    for(unsigned i=0;i<512;i++) if(secondary[i].owner==e) secondary_end(secondary+i);
    /* The native controller still owns its token until Release. GetRefCount
     * reports one after this detach, so its existing cleanup remains valid. */
}
static int secondary_tick(Cpu *c,Engine *e) {
    for(unsigned i=0;i<512;i++) {
        Secondary *s=secondary+i;
        if(s->owner!=e || !s->instance.bank) continue;
        CarAudioRuntimeInstance *previous=executing; executing=&s->instance;
        int rc=car_audio_runtime_tick(executing,execute,c); executing=previous;
        if(rc) return -1;
        if(!s->instance.bank) secondary_end(s);
    }
    return 0;
}
static int secondary_create(unsigned kind,uint32_t members,uint32_t out) {
    Engine *e=secondary_owner;
    /* The source shift controller follows CSTATE +98 -> car +264 ->
     * caraudio +18 (the exhaust NG reference), then SweetBanks[0]. */
    unsigned index=kind==0?1:kind==1?2:3;
    Resource *r=e->package->resource+e->tier->secondary[index];
    if(!r->bank.source) {
        if(plan(r) || prepare_samples(r)) { clear_bank(r); return -1; }
    }
    const char *name=kind==0?"CAR_SWTN":kind==1?"CAR_WHINE":"FX_SHIFTING_01";
    const CarAudioRuntimeModule *m=r->modules;
    unsigned words=kind==0?7:kind==1?9:6;
    if(r->nm!=1 || m->interface.member_words!=words ||
       strcmp((const char *)m->interface.id+4,name)) return -1;
    Secondary *s=NULL;
    for(unsigned i=0;i<512;i++) if(!secondary[i].token) { s=secondary+i; break; }
    if(!s) return -1;
    int32_t values[9];
    for(unsigned i=0;i<words;i++) values[i]=(int32_t)M32(members+4*i);
    if(kind==0) {
        if(r->event_identity>200) return -1;
        values[1]=(int32_t)r->event_identity;
    }
    s->state=gmalloc_try(m->size); s->token=gmalloc_try(4);
    if(!s->state || !s->token || car_audio_runtime_create(&r->bank,0,&s->instance,
        G+s->state,m->size,s->state,values,words)) {
        secondary_end(s); if(s->token) gfree(s->token); memset(s,0,sizeof(*s)); return -1;
    }
    s->owner=e; s->words=words; s->kind=kind; s->event_identity=r->event_identity;
    ++secondary_tokens;
    M32(s->token)=0; M32(out)=s->token;
    if(diag_on) {
        char id[65]; hex(r->digest,id);
        diag_event("addcar_audio: secondary car=%s export=%s bank=%s selector=%d tier=%u",
            e->package->name,name,id,values[0],(unsigned)(e->tier-e->package->tiers));
    }
    return 0;
}

void f_004F33B0_orig(Cpu *);
void f_00831A8A_orig(Cpu *);
void f_008311AA_orig(Cpu *);
void f_00831782_orig(Cpu *);
void f_0083179C_orig(Cpu *);
void f_004F33B0(Cpu *c) {
    Engine *previous=secondary_owner;
    secondary_owner=NULL;
    Package *p=car_audio_enabled()?for_object(c->ecx):NULL;
    if(p && p->version==7) for(unsigned i=0;i<MAX_ENGINES;i++) {
        Engine *e=engines+i;
        if(e->object && e->package==p && e->replaced &&
           M32(e->object+0x14)==M32(c->ecx+0x14)) { secondary_owner=e; break; }
    }
    f_004F33B0_orig(c);
    secondary_owner=previous;
}
void f_00831A8A(Cpu *c) {
    if(secondary_owner) {
        uint32_t handle=arg(c,0);
        unsigned kind=handle==0x009147ec?0:handle==0x0091488c?1:handle==0x00914874?2:3;
        if(kind<3 && !secondary_create(kind,arg(c,1),arg(c,2))) {
            c->eax=0; c->esp+=4; return;
        }
    }
    f_00831A8A_orig(c);
}
void f_008311AA(Cpu *c) {
    Secondary *s=secondary_token(c->ecx);
    if(!s) { f_008311AA_orig(c); return; }
    M32(arg(c,0))=s->instance.bank?2:1; c->eax=0; c->esp+=8;
}
void f_00831782(Cpu *c) {
    Secondary *s=secondary_token(c->ecx);
    if(!s) { f_00831782_orig(c); return; }
    secondary_end(s); gfree(s->token); memset(s,0,sizeof(*s)); --secondary_tokens; c->eax=0; c->esp+=4;
}
void f_0083179C(Cpu *c) {
    Secondary *s=secondary_token(c->ecx);
    if(!s) { f_0083179C_orig(c); return; }
    if(s->instance.bank) {
        int32_t values[9]; uint32_t at=arg(c,0);
        for(unsigned i=0;i<s->words;i++) values[i]=(int32_t)M32(at+4*i);
        if(s->kind==0) values[1]=(int32_t)s->event_identity;
        if(car_audio_runtime_update(&s->instance,values,s->words)) secondary_end(s);
    }
    c->eax=0; c->esp+=8;
}
