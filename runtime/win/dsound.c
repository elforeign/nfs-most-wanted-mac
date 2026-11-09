#include "dsound.h"

#define OBJECTS 256
#define DEVICE 1
#define BUFFER 2
#define NOTIFY 3
#define BAD ((uint32_t)DSERR_INVALIDPARAM)
typedef struct AudioObject {
    uint32_t guest, refs, kind, owner, memory, bytes;
    uint32_t notify_stack, notify_teb;
    uint32_t lock[4];
    int locked, manual;
    DsDevice *device;
    DsBuffer *buffer;
    WinAudioOutput *output;
} AudioObject;
static AudioObject objects[OBJECTS];
static uint32_t vt_device, vt_buffer, vt_notify;
static WinPcmEndpoint endpoint_override;
static int have_override, manual_output;
static AudioObject *object(uint32_t p) {
    for (unsigned i=0;i<OBJECTS;++i)
        if (objects[i].refs && objects[i].guest==p) return objects+i;
    return NULL;
}
static AudioObject *this_object(Cpu *c, unsigned kind) {
    AudioObject *o=object(win_arg(c,0));
    return o && o->kind==kind ? o : NULL;
}
static int output_ptr(uint32_t p) { return p && win_vm_access(p,4); }
static int format_ptr(uint32_t p) {
    if (!p || !win_vm_access(p,18)) return 0;
    return M16(p)!=0xfffe || (M16(p+16)>=22 && win_vm_access(p,40));
}
static AudioObject *new_object(unsigned kind,uint32_t vtable) {
    for (unsigned i=0;i<OBJECTS;++i) if (!objects[i].kind) {
        uint32_t p=win_alloc(4);
        if (!p) return NULL;
        memset(objects+i,0,sizeof objects[i]);
        objects[i].guest=p; objects[i].kind=kind; objects[i].refs=1;
        M32(p)=vtable; return objects+i;
    }
    return NULL;
}
static uint32_t release_object(AudioObject *o) {
    if (!o || !o->refs) return 0;
    if (--o->refs) return o->refs;
    uint32_t owner=o->owner;
    if (o->kind==DEVICE) {
        // An output thread may be signaling an event and waiting for the guest GIL.
        WinAudioOutput *out=o->output; DsDevice *dev=o->device;
        gil_release(); win_audio_output_destroy(out); ds_device_release(dev); gil_acquire();
    } else if (o->kind==BUFFER) ds_buffer_release(o->buffer);
    memset(o,0,sizeof *o);
    if (owner) release_object(object(owner));
    return 0;
}
static uint32_t addref(Cpu *c) {
    AudioObject *o=object(win_arg(c,0));
    return o && o->refs<UINT32_MAX ? ++o->refs : 0;
}
static uint32_t release(Cpu *c) { return release_object(object(win_arg(c,0))); }
static int iid(uint32_t p,uint32_t first,const uint8_t tail[12]) {
    return M32(p)==first && !__builtin_memcmp(G+p+4,tail,12);
}
static uint32_t query(Cpu *c) {
    AudioObject *o=object(win_arg(c,0)); uint32_t p=win_arg(c,1), out=win_arg(c,2);
    if (!o || !output_ptr(out) || !win_vm_access(p,16)) return BAD;
    M32(out)=0;
    const uint8_t unknown[12]={0,0,0,0,0xc0,0,0,0,0,0,0,0x46};
    const uint8_t ds[12]={0x81,0x49,0xce,0x11,0xa5,0x21,0,0x20,0xaf,0x0b,0xe5,0x60};
    const uint8_t ds8[12]={0x95,0xf3,0x34,0x48,0x9e,0xf6,0x7f,0xa9,0x9d,0xe5,0x09,0x66};
    const uint8_t buf8[12]={0x24,0x75,0x82,0x4d,0x92,0x0f,0x50,0xe3,0x6a,0xb3,0xab,0x1e};
    const uint8_t notify[12]={0xcd,0x89,0xd0,0x11,0xaf,0x08,0,0xa0,0xc9,0x25,0xcd,0x16};
    if (o->kind==NOTIFY && (iid(p,0,unknown) || iid(p,0x279afa85,ds) || iid(p,0x6825a449,buf8))) {
        AudioObject *owner=object(o->owner);
        if (!owner || owner->refs==UINT32_MAX) return (uint32_t)DSERR_OUTOFMEMORY;
        ++owner->refs; M32(out)=owner->guest; return 0;
    }
    if (iid(p,0,unknown) ||
        (o->kind==DEVICE && (iid(p,0x279afa83,ds) || iid(p,0xc50a7e93,ds8))) ||
        (o->kind==BUFFER && (iid(p,0x279afa85,ds) || iid(p,0x6825a449,buf8))) ||
        (o->kind==NOTIFY && iid(p,0xb0210783,notify))) {
        if (o->refs==UINT32_MAX) return (uint32_t)DSERR_OUTOFMEMORY;
        ++o->refs; M32(out)=o->guest; return 0;
    }
    if (o->kind==BUFFER && iid(p,0xb0210783,notify)) {
        // One stable notification interface preserves COM identity.
        for (unsigned i=0;i<OBJECTS;++i) if (objects[i].refs && objects[i].kind==NOTIFY && objects[i].owner==o->guest) {
            ++objects[i].refs; M32(out)=objects[i].guest; return 0;
        }
        AudioObject *n=new_object(NOTIFY,vt_notify);
        if (!n) return (uint32_t)DSERR_OUTOFMEMORY;
        n->owner=o->guest; n->buffer=o->buffer; ++o->refs; M32(out)=n->guest; return 0;
    }
    return (uint32_t)DSERR_NOINTERFACE;
}
static void notification(void *user,uint32_t event) {
    AudioObject *d=user;
    gil_acquire();
    WinAdapter set=win_import_adapter("KERNEL32.dll!SetEvent");
    if (set) {
        Cpu c={0}; c.esp=d->notify_stack; c.fs_base=d->notify_teb;
        M32(c.esp+4)=event; (void)set(&c);
    }
    gil_release();
}
void win_dsound_set_endpoint(const WinPcmEndpoint *endpoint,int manual) {
    have_override=endpoint!=NULL; manual_output=manual!=0;
    if (endpoint) endpoint_override=*endpoint;
}
static uint32_t create(Cpu *c) {
    uint32_t out=win_arg(c,1);
    if (!output_ptr(out)) return BAD;
    M32(out)=0;
    if (win_arg(c,2)) return (uint32_t)DSERR_NOAGGREGATION;
    if (win_arg(c,0)) return (uint32_t)DSERR_NODRIVER; // default playback endpoint only
    const char *mode=getenv("NFS_AUDIO");
    int null_sink=mode && (!strcmp(mode,"null") || !strcmp(mode,"off"));
    if (!have_override && !null_sink && mode && strcmp(mode,"pcm51") && strcmp(mode,"auto"))
        return (uint32_t)DSERR_UNSUPPORTED;
    AudioObject *d=new_object(DEVICE,vt_device);
    if (!d) return (uint32_t)DSERR_OUTOFMEMORY;
    d->notify_stack=win_alloc(16); d->notify_teb=win_alloc(0x1000);
    if (!d->notify_stack || !d->notify_teb) { memset(d,0,sizeof *d); return (uint32_t)DSERR_OUTOFMEMORY; }
    DsConfig cfg={0}; cfg.mode=DS_OUT_PCM51;
    cfg.sink=have_override || manual_output ? DS_SINK_MANUAL : null_sink ? DS_SINK_NULL : DS_SINK_DEVICE;
    cfg.mix_rate=48000; cfg.block_frames=256; cfg.notify=notification; cfg.notify_user=d;
    DsResult result=ds_device_create(&cfg,&d->device);
    if (result!=DS_OK) { memset(d,0,sizeof *d); return (uint32_t)result; }
    d->manual=manual_output;
    if (cfg.sink==DS_SINK_MANUAL) {
        WinPcmEndpoint endpoint=have_override?endpoint_override:null_sink?win_pcm_null_endpoint():win_pcm_endpoint();
        d->output=win_audio_output_create(d->device,&endpoint,!d->manual);
        if (!d->output) { ds_device_release(d->device); memset(d,0,sizeof *d); return (uint32_t)DSERR_NODRIVER; }
    }
    M32(out)=d->guest; return 0;
}
static uint32_t capture_create(Cpu *c) {
    if (!output_ptr(win_arg(c,1))) return BAD;
    M32(win_arg(c,1))=0; return (uint32_t)DSERR_NODRIVER;
}
static uint32_t create_buffer(Cpu *c) {
    AudioObject *d=this_object(c,DEVICE); uint32_t desc=win_arg(c,1), out=win_arg(c,2);
    if (!d || !output_ptr(out) || !win_vm_access(desc,20)) return BAD;
    M32(out)=0;
    if (win_arg(c,3)) return (uint32_t)DSERR_NOAGGREGATION;
    uint32_t size=M32(desc), flags=M32(desc+4), bytes=M32(desc+8), wfx=M32(desc+16);
    if ((size!=20 && size!=36) || !win_vm_access(desc,size)) return BAD;
    if (wfx && !format_ptr(wfx)) return BAD;
    if (!(flags&DSBCAPS_PRIMARYBUFFER) && (!bytes || bytes>0x0fffffffu || !wfx)) return BAD;
    AudioObject *b=new_object(BUFFER,vt_buffer);
    if (!b) return (uint32_t)DSERR_OUTOFMEMORY;
    if (!(flags&DSBCAPS_PRIMARYBUFFER)) {
        b->memory=win_alloc(bytes);
        if (!b->memory) { memset(b,0,sizeof *b); return (uint32_t)DSERR_OUTOFMEMORY; }
    }
    DsResult r=ds_device_create_buffer(d->device,flags,bytes,wfx?G+wfx:NULL,
                                      b->memory?G+b->memory:NULL,&b->buffer);
    if (r!=DS_OK) { memset(b,0,sizeof *b); return (uint32_t)r; }
    b->device=d->device; b->bytes=bytes; b->owner=d->guest; ++d->refs;
    M32(out)=b->guest; return 0;
}
static uint32_t duplicate(Cpu *c) {
    AudioObject *d=this_object(c,DEVICE), *s=object(win_arg(c,1)); uint32_t out=win_arg(c,2);
    if (!d || !s || s->kind!=BUFFER || s->device!=d->device || !output_ptr(out)) return BAD;
    M32(out)=0; AudioObject *b=new_object(BUFFER,vt_buffer);
    if (!b) return (uint32_t)DSERR_OUTOFMEMORY;
    DsResult r=ds_device_duplicate_buffer(d->device,s->buffer,&b->buffer);
    if (r!=DS_OK) { memset(b,0,sizeof *b); return (uint32_t)r; }
    b->device=d->device; b->bytes=s->bytes; b->memory=s->memory;
    b->owner=s->guest; ++s->refs; M32(out)=b->guest; return 0;
}
static uint32_t device_caps(Cpu *c) {
    AudioObject *d=this_object(c,DEVICE); uint32_t p=win_arg(c,1);
    if (!d || !win_vm_access(p,96)) return BAD;
    return (uint32_t)ds_device_get_caps(d->device,G+p);
}
static uint32_t cooperative(Cpu *c) {
    AudioObject *d=this_object(c,DEVICE);
    return d?(uint32_t)ds_device_set_cooperative_level(d->device,win_arg(c,2)):BAD;
}
static uint32_t speaker_get(Cpu *c) {
    AudioObject *d=this_object(c,DEVICE); uint32_t p=win_arg(c,1), value=0;
    if (!d || !output_ptr(p)) return BAD;
    DsResult r=ds_device_get_speaker_config(d->device,&value); M32(p)=value; return (uint32_t)r;
}
static uint32_t speaker_set(Cpu *c) {
    AudioObject *d=this_object(c,DEVICE);
    return d?(uint32_t)ds_device_set_speaker_config(d->device,win_arg(c,1)):BAD;
}
static uint32_t compact(Cpu *c) { return this_object(c,DEVICE)?0:BAD; }
static uint32_t initialized(Cpu *c) { (void)c; return (uint32_t)DSERR_ALREADYINITIALIZED; }
static uint32_t unsupported(Cpu *c) { (void)c; return (uint32_t)DSERR_UNSUPPORTED; }
static uint32_t buffer_caps(Cpu *c) {
    AudioObject *b=this_object(c,BUFFER); uint32_t p=win_arg(c,1);
    if (!b || !win_vm_access(p,20)) return BAD;
    return (uint32_t)ds_buffer_get_caps(b->buffer,G+p);
}
static uint32_t position(Cpu *c) {
    AudioObject *b=this_object(c,BUFFER); uint32_t p=win_arg(c,1), w=win_arg(c,2), pv=0,wv=0;
    if (!b || (p && !output_ptr(p)) || (w && !output_ptr(w))) return BAD;
    DsResult r=ds_buffer_get_current_position(b->buffer,&pv,&wv);
    if (p) M32(p)=pv;
    if (w) M32(w)=wv;
    return (uint32_t)r;
}
static uint32_t get_format(Cpu *c) {
    AudioObject *b=this_object(c,BUFFER); uint32_t p=win_arg(c,1), n=win_arg(c,2), out=win_arg(c,3), written=0;
    if (!b || (p && !win_vm_access(p,n)) || (out && !output_ptr(out))) return BAD;
    DsResult r=ds_buffer_get_format(b->buffer,p?G+p:NULL,n,&written);
    if (out) M32(out)=written;
    return (uint32_t)r;
}
#define GETTER(name,fn,type) static uint32_t name(Cpu *c) { \
    AudioObject *b=this_object(c,BUFFER); uint32_t p=win_arg(c,1); type value=0; \
    if (!b || !output_ptr(p)) return BAD; \
    DsResult r=fn(b->buffer,&value); M32(p)=(uint32_t)value; return (uint32_t)r; }
GETTER(volume_get,ds_buffer_get_volume,int32_t)
GETTER(pan_get,ds_buffer_get_pan,int32_t)
GETTER(frequency_get,ds_buffer_get_frequency,uint32_t)
GETTER(status,ds_buffer_get_status,uint32_t)
#define SETTER(name,fn,type) static uint32_t name(Cpu *c) { \
    AudioObject *b=this_object(c,BUFFER); return b?(uint32_t)fn(b->buffer,(type)win_arg(c,1)):BAD; }
SETTER(volume_set,ds_buffer_set_volume,int32_t)
SETTER(pan_set,ds_buffer_set_pan,int32_t)
SETTER(frequency_set,ds_buffer_set_frequency,uint32_t)
SETTER(seek,ds_buffer_set_current_position,uint32_t)
static uint32_t set_format(Cpu *c) {
    AudioObject *b=this_object(c,BUFFER); uint32_t p=win_arg(c,1);
    if (!b || !format_ptr(p)) return BAD;
    return (uint32_t)ds_buffer_set_format(b->buffer,G+p);
}
static uint32_t lock_buffer(Cpu *c) {
    AudioObject *b=this_object(c,BUFFER);
    uint32_t p1=win_arg(c,3), l1=win_arg(c,4), p2=win_arg(c,5), l2=win_arg(c,6);
    if (!b || b->locked || !output_ptr(p1) || !output_ptr(l1) ||
        (p2 && !output_ptr(p2)) || (l2 && !output_ptr(l2)) || (!!p2!=!!l2)) return BAD;
    uint32_t o1=0,n1=0,o2=0,n2=0;
    DsResult r=ds_buffer_lock(b->buffer,win_arg(c,1),win_arg(c,2),win_arg(c,7),&o1,&n1,p2?&o2:NULL,p2?&n2:NULL);
    if (r!=DS_OK) return (uint32_t)r;
    M32(p1)=b->memory+o1; M32(l1)=n1;
    if (p2) { M32(p2)=n2?b->memory+o2:0; M32(l2)=n2; }
    b->lock[0]=M32(p1); b->lock[1]=n1; b->lock[2]=n2?b->memory+o2:0; b->lock[3]=n2;
    b->locked=1; return 0;
}
static uint32_t unlock_buffer(Cpu *c) {
    AudioObject *b=this_object(c,BUFFER);
    if (!b || !b->locked) return BAD;
    for (unsigned i=0;i<4;++i) if (win_arg(c,i+1)!=b->lock[i]) return BAD;
    DsResult r=ds_buffer_unlock(b->buffer,b->lock[0]-b->memory,b->lock[1],
                                b->lock[2]?b->lock[2]-b->memory:0,b->lock[3]);
    if (r==DS_OK) b->locked=0;
    return (uint32_t)r;
}
static uint32_t play(Cpu *c) {
    AudioObject *b=this_object(c,BUFFER);
    if (!b || win_arg(c,1) || win_arg(c,2) || (win_arg(c,3)&~DSBPLAY_LOOPING)) return BAD;
    return (uint32_t)ds_buffer_play(b->buffer,win_arg(c,3));
}
static uint32_t stop(Cpu *c) {
    AudioObject *b=this_object(c,BUFFER);
    if (!b) return BAD;
    ++b->refs;
    gil_release(); DsResult r=ds_buffer_stop(b->buffer); gil_acquire();
    release_object(b); return (uint32_t)r;
}
static uint32_t restore(Cpu *c) {
    AudioObject *b=this_object(c,BUFFER); return b?(uint32_t)ds_buffer_restore(b->buffer):BAD;
}
static uint32_t notifications(Cpu *c) {
    AudioObject *n=this_object(c,NOTIFY); uint32_t count=win_arg(c,1), p=win_arg(c,2);
    if (!n || count>256 || (count && !win_vm_access(p,8ull*count))) return BAD;
    uint32_t offsets[256], events[256];
    for (unsigned i=0;i<count;++i) { offsets[i]=M32(p+8*i); events[i]=M32(p+8*i+4); }
    return (uint32_t)ds_buffer_set_notification_positions(n->buffer,count,offsets,events);
}
DsResult win_dsound_render(uint32_t guest,size_t frames) {
    AudioObject *d=object(guest);
    if (!d || d->kind!=DEVICE || !d->manual) return DSERR_INVALIDPARAM;
    ++d->refs;
    gil_release(); DsResult r=win_audio_output_pump(d->output,frames); gil_acquire();
    release_object(d); return r;
}
typedef struct { const char *name; WinAdapter call; unsigned args; } Method;
static uint32_t vtable(const Method *methods,unsigned count) {
    uint32_t p=win_alloc(4*count);
    if (!p) return 0;
    for (unsigned i=0;i<count;++i) {
        uint32_t thunk=win_host_register(methods[i].name,methods[i].call,methods[i].args);
        if (!thunk) return 0;
        M32(p+4*i)=thunk;
    }
    return p;
}
int win_dsound_register(void) {
    static int registered;
    const Method device[]={
        {"DS.QueryInterface",query,3},{"DS.AddRef",addref,1},{"DS.Release",release,1},
        {"DS.CreateSoundBuffer",create_buffer,4},{"DS.GetCaps",device_caps,2},
        {"DS.DuplicateSoundBuffer",duplicate,3},{"DS.SetCooperativeLevel",cooperative,3},
        {"DS.Compact",compact,1},{"DS.GetSpeakerConfig",speaker_get,2},
        {"DS.SetSpeakerConfig",speaker_set,2},{"DS.Initialize",initialized,2},
        {"DS.VerifyCertification",unsupported,2}};
    const Method buffer[]={
        {"DSB.QueryInterface",query,3},{"DSB.AddRef",addref,1},{"DSB.Release",release,1},
        {"DSB.GetCaps",buffer_caps,2},{"DSB.GetCurrentPosition",position,3},
        {"DSB.GetFormat",get_format,4},{"DSB.GetVolume",volume_get,2},{"DSB.GetPan",pan_get,2},
        {"DSB.GetFrequency",frequency_get,2},{"DSB.GetStatus",status,2},{"DSB.Initialize",initialized,3},
        {"DSB.Lock",lock_buffer,8},{"DSB.Play",play,4},{"DSB.SetCurrentPosition",seek,2},
        {"DSB.SetFormat",set_format,2},{"DSB.SetVolume",volume_set,2},{"DSB.SetPan",pan_set,2},
        {"DSB.SetFrequency",frequency_set,2},{"DSB.Stop",stop,1},{"DSB.Unlock",unlock_buffer,5},
        {"DSB.Restore",restore,1},{"DSB.SetFX",unsupported,4},{"DSB.AcquireResources",unsupported,4},
        {"DSB.GetObjectInPath",unsupported,5}};
    const Method notify[]={
        {"DSN.QueryInterface",query,3},{"DSN.AddRef",addref,1},{"DSN.Release",release,1},
        {"DSN.SetNotificationPositions",notifications,3}};
    if (registered) return 1;
    if (vt_device || vt_buffer || vt_notify) return 0;
    vt_device=vtable(device,sizeof device/sizeof *device);
    vt_buffer=vtable(buffer,sizeof buffer/sizeof *buffer);
    vt_notify=vtable(notify,sizeof notify/sizeof *notify);
    if (!vt_device || !vt_buffer || !vt_notify) return 0;
#define REG(name,call) if (!win_import_register("DSOUND.dll!" name,call,3)) return 0
    REG("DirectSoundCreate",create);
    REG("DirectSoundCreate8",create);
    REG("#1",create); REG("#11",create);
    REG("#6",capture_create); REG("#12",capture_create);
#undef REG
    registered=1;
    return 1;
}
