#include "d3d9_state.h"
static WDObject objects[WD_OBJECTS];
static uint32_t generation, headers;
static unsigned live, pending;
static uint64_t serial;
static WinD3D9Submit backend;
static void *backend_context;
/* Interface headers {vtable, generation} live in one guest block, 8 bytes per
 * table slot. A released slot's guest address is reused, as heap memory is. */
WDObject *wd_object(uint32_t guest) {
    if (!headers || guest<headers || (guest-headers)%8 || (guest-headers)/8>=WD_OBJECTS) return NULL;
    WDObject *o=objects+(guest-headers)/8;
    return o->kind?o:NULL;
}
WDObject *wd_live(uint32_t guest) {
    WDObject *o=wd_object(guest);
    return o && o->refs?o:NULL;
}
WDObject *wd_new(uint32_t kind, uint32_t vtable, uint32_t parent) {
    if (!headers && !(headers=win_alloc(WD_OBJECTS*8))) return NULL;
    for (unsigned i=0;i<WD_OBJECTS;++i) if (!objects[i].kind) {
        if (generation==UINT32_MAX) return NULL;
        WDDevice *device=NULL;
        if (kind==WD_DEVICE) {
            device=malloc(sizeof *device);
            if (!device) return NULL;
            memset(device,0,sizeof *device);
        }
        WDObject *o=objects+i;
        memset(o,0,sizeof *o);
        o->kind=kind; o->guest=headers+i*8; o->id=i+1; o->generation=++generation;
        o->refs=1; o->parent=parent; o->device=device;
        M32(o->guest)=vtable; M32(o->guest+4)=o->generation;
        if (parent) wd_addref(parent);
        ++live; return o;
    }
    return NULL;
}
static void destroy(WDObject *o) {
    WDDevice *d=o->device;
    M32(o->guest)=0; M32(o->guest+4)=0;
    memset(o,0,sizeof *o); --live;
    if (d) {
        wd_bind(&d->vs,0); wd_bind(&d->ps,0); wd_bind(&d->declaration,0); wd_bind(&d->indices,0);
        for (unsigned i=0;i<WD_STREAMS;++i) wd_bind(&d->streams[i].resource,0);
        for (unsigned i=0;i<WD_TEXTURES;++i) wd_bind(&d->textures[i],0);
        free(d);
    }
}
uint32_t wd_addref(uint32_t guest) {
    WDObject *o=wd_object(guest);
    if (!o || o->refs==UINT32_MAX) return 0;
    /* A bound object whose public count reached zero is revived by a Get*. */
    if (!o->refs && o->parent) wd_addref(o->parent);
    return ++o->refs;
}
uint32_t wd_release(uint32_t guest) {
    WDObject *o=wd_live(guest);
    if (!o) return 0;
    if (--o->refs) return o->refs;
    uint32_t parent=o->parent;
    if (!o->binds) destroy(o);
    if (parent) wd_release(parent);
    return 0;
}
void wd_bind(uint32_t *slot, uint32_t value) {
    uint32_t old=*slot;
    WDObject *o=wd_object(value);
    if (o) ++o->binds;
    *slot=value;
    if ((o=wd_object(old)) && !--o->binds && !o->refs) destroy(o);
}
unsigned win_d3d9_live_objects(void) { return live; }
unsigned win_d3d9_pending(void) { return pending; }
void win_d3d9_set_backend(WinD3D9Submit submit, void *context) {
    backend=submit; backend_context=context;
}
void wd_defaults(WDDevice *d, uint32_t w, uint32_t h, int depth) {
    d->state.viewport[2]=w; d->state.viewport[3]=h;
    d->state.viewport[5]=0x3f800000;
    d->state.scissor[2]=w; d->state.scissor[3]=h;
    d->state.rs[7]=depth?1:0; d->state.rs[8]=3; d->state.rs[9]=2;
    d->state.rs[14]=1; d->state.rs[19]=2; d->state.rs[20]=1;
    d->state.rs[22]=3; d->state.rs[23]=4; d->state.rs[25]=8;
    d->state.rs[53]=1; d->state.rs[54]=1; d->state.rs[55]=1;
    d->state.rs[56]=8; d->state.rs[58]=UINT32_MAX; d->state.rs[59]=UINT32_MAX;
    d->state.rs[137]=1; d->state.rs[168]=15; d->state.rs[171]=1;
    for (unsigned i=186;i<=188;++i) d->state.rs[i]=1;
    d->state.rs[189]=8; d->state.rs[193]=UINT32_MAX;
    for (unsigned i=190;i<=192;++i) d->state.rs[i]=15;
    d->state.rs[207]=2; d->state.rs[208]=1; d->state.rs[209]=1;
    for (unsigned i=0;i<8;++i) {
        uint32_t *t=d->state.tss[i];
        t[1]=i?1:4; t[2]=2; t[3]=1; t[4]=i?1:2; t[5]=2; t[6]=1;
        t[11]=i; t[26]=1; t[27]=1; t[28]=1;
    }
    for (unsigned i=0;i<WD_TEXTURES;++i) {
        d->state.sampler[i][1]=1; d->state.sampler[i][2]=1; d->state.sampler[i][3]=1;
        d->state.sampler[i][5]=1; d->state.sampler[i][6]=1; d->state.sampler[i][10]=1;
    }
}
static void bytes_free(WinD3D9Bytes *b) { free(b->bytes); }
void win_d3d9_complete(WinD3D9Snapshot *s) {
    if (!s) return;
    bytes_free(&s->vs); bytes_free(&s->ps); bytes_free(&s->declaration); bytes_free(&s->indices);
    for (unsigned i=0;i<WD_STREAMS;++i) bytes_free(&s->vertices[i]);
    for (unsigned i=0;i<WD_TEXTURES;++i) bytes_free(&s->textures[i]);
    free(s); --pending;
}
static int copy_bytes(WinD3D9Bytes *b, WDObject *o, uint32_t ptr, uint32_t size, uint32_t offset) {
    if (!size || size>WD_MAX_BYTES || !win_vm_access(ptr,size)) return 0;
    b->bytes=malloc(size);
    if (!b->bytes) return 0;
    memcpy(b->bytes,G+ptr,size); b->size=size; b->offset=offset;
    if (o) {
        b->id=o->id; b->generation=o->generation; b->version=o->version;
        b->format=o->format; b->width=o->width; b->height=o->height; b->pitch=o->pitch;
    }
    return 1;
}
static int copy_object(WinD3D9Bytes *b, uint32_t guest) {
    WDObject *o=wd_object(guest);
    return o && !o->locked && copy_bytes(b,o,o->data,o->size,0);
}
static uint32_t vertex_count(uint32_t type, uint32_t prims) {
    if (!prims || prims>0x100000) return 0;
    switch (type) {
    case 1: return prims;
    case 2: return prims*2;
    case 3: return prims+1;
    case 4: return prims*3;
    case 5: case 6: return prims+2;
    default: return 0;
    }
}
uint32_t wd_draw(WDObject *device, uint32_t type, uint32_t prims, int indexed,
                 int32_t base, uint32_t min, uint32_t num, uint32_t start,
                 uint32_t up_vertices, uint32_t up_stride, uint32_t up_indices, uint32_t up_format) {
    WDDevice *d=device->device;
    uint32_t count=vertex_count(type,prims);
    int up=up_vertices!=0;
    /* D3D9 has no indexed point lists. */
    if (!d->scene || !count || !d->declaration || (indexed && type==1)) return WD_INVALID;
    /* A NULL shader is valid fixed-function processing, which is not recorded. */
    if (!d->vs || !d->ps) win_fail("D3D9 outside the recorded subset: fixed-function draw",device->guest);
    /* Headless recording without a renderer stops instead of dropping draws. */
    if (!backend) win_fail("D3D9 draw without a backend (see windows-d3d9.md)",device->guest);
    if (pending>=WD_PENDING) return WD_OOM;
    if (!indexed) { min=start; num=count; base=0; }
    if (!num || num>0x100000 || (int64_t)base+min<0 ||
        (uint64_t)((int64_t)base+min)+num>UINT32_MAX) return WD_INVALID;
    WinD3D9Snapshot *s=malloc(sizeof *s);
    if (!s) return WD_OOM;
    memset(s,0,sizeof *s); ++pending;
    s->serial=++serial; s->device_id=device->id; s->device_generation=device->generation;
    s->topology=type; s->primitives=prims; s->indexed=(uint32_t)indexed; s->up=(uint32_t)up;
    s->base_vertex=base; s->min_vertex=min; s->num_vertices=num; s->start=start;
    memcpy(&s->state,&d->state,sizeof s->state);
    if (!copy_object(&s->vs,d->vs) || !copy_object(&s->ps,d->ps) ||
        !copy_object(&s->declaration,d->declaration)) goto invalid;
    if (indexed) {
        WDObject *ib=up?NULL:wd_object(d->indices);
        uint32_t fmt=up?up_format:(ib?ib->format:0), step=fmt==101?2:fmt==102?4:0;
        uint64_t off=up?0:(uint64_t)start*step, n=(uint64_t)count*step;
        if (!step || (!up && (!ib || ib->locked || off+n>ib->size))) goto invalid;
        uint32_t p=up?up_indices:ib->data+(uint32_t)off;
        if (!copy_bytes(&s->indices,ib,p,(uint32_t)n,(uint32_t)off)) goto invalid;
        s->index_format=fmt;
        for (uint32_t i=0;i<count;++i) {
            uint32_t v=step==2?M16(p+i*step):M32(p+i*step);
            if (v<min || (uint64_t)v>=(uint64_t)min+num) goto invalid;
        }
    }
    /* Declaration elements are copied and validated at creation. Compute the
     * largest accessed element in each stream, then validate signed base math. */
    uint32_t extent[WD_STREAMS]={0};
    const uint8_t *decl=s->declaration.bytes;
    for (unsigned i=0;i+8<s->declaration.size;i+=8) {
        uint32_t stream=(uint32_t)decl[i]|((uint32_t)decl[i+1]<<8);
        uint32_t off=(uint32_t)decl[i+2]|((uint32_t)decl[i+3]<<8), t=decl[i+4];
        uint32_t n=t<=3?4*(t+1):4;
        if (off+n>extent[stream]) extent[stream]=off+n;
    }
    for (unsigned i=0;i<WD_STREAMS;++i) {
        WinD3D9Stream *st=d->streams+i;
        uint32_t stride=up&&i==0?up_stride:st->stride;
        s->stride[i]=stride; s->stream_offset[i]=up&&i==0?0:st->offset;
        if (!extent[i]) continue;
        if (up && i) goto invalid;
        WDObject *vb=up?NULL:wd_object(st->resource);
        uint64_t off=(uint64_t)((int64_t)base+min)*stride+(up?0:st->offset);
        uint64_t n=(uint64_t)(num-1)*stride+extent[i];
        uint64_t p=up?(uint64_t)up_vertices+off:(vb?(uint64_t)vb->data+off:0);
        if (!stride || stride<extent[i] || n>WD_MAX_BYTES || p>UINT32_MAX ||
            (!up && (!vb || vb->locked || off+n>vb->size))) goto invalid;
        if (!copy_bytes(&s->vertices[i],vb,(uint32_t)p,(uint32_t)n,(uint32_t)off)) goto invalid;
    }
    for (unsigned i=0;i<WD_TEXTURES;++i)
        if (d->textures[i] && !copy_object(&s->textures[i],d->textures[i])) goto invalid;
    if (!backend(backend_context,s)) { win_d3d9_complete(s); return WD_UNAVAILABLE; }
    return 0;
invalid:
    win_d3d9_complete(s); return WD_INVALID;
}
