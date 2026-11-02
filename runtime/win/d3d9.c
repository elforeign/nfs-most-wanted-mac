#include "d3d9.h"
static uint32_t vt[9];
static unsigned callbacks;
#define A(n) win_arg(c,n)
#define SELF(k) WDObject *o=wd_live(A(0)); if (!o || o->kind!=(k)) return WD_INVALID
#define DEVICE SELF(WD_DEVICE); WDDevice *d=o->device
/* Valid D3D9 arguments that the recorded subset does not model stop the run
 * (exit 2) instead of returning an error the guest might ignore. */
#define BEYOND(what) win_fail("D3D9 outside the recorded subset: " what,A(0))
static int out(uint32_t p, uint64_t n) { return win_vm_access(p,n); }
static const char *const interface_names[]={"?","IDirect3D9","IDirect3DDevice9",
    "IDirect3DTexture9","IDirect3DVertexBuffer9","IDirect3DIndexBuffer9",
    "IDirect3DVertexDeclaration9","IDirect3DVertexShader9","IDirect3DPixelShader9"};
static uint32_t unsupported(Cpu *c) {
    /* All unimplemented slots share this terminal callback, so it names the
     * slot from the guest call site: call [r32+disp8] (FF 50+r) or
     * call [r32+disp32] (FF 90+r) just before the return address. It never
     * guesses the unsupported signature or returns through stdcall cleanup. */
    WDObject *o=wd_object(A(0));
    uint32_t ret=M32(c->esp), slot=UINT32_MAX;
    if (ret>=6 && win_vm_access(ret-6,6)) {
        if (M8(ret-3)==0xff && (M8(ret-2)&0xf8)==0x50 && M8(ret-2)!=0x54) slot=M8(ret-1)/4u;
        else if (M8(ret-6)==0xff && (M8(ret-5)&0xf8)==0x90 && M8(ret-5)!=0x94) slot=M32(ret-4)/4u;
    }
    printf("win: D3D9 unsupported %s slot %d (caller %08X)\n",
           interface_names[o?o->kind:0],slot==UINT32_MAX?-1:(int)slot,ret);
    win_fail("D3D9 unsupported vtable slot (see windows-d3d9.md)",A(0));
}
static int guid(uint32_t p, uint32_t a, uint32_t b, uint32_t e, uint32_t f) {
    return M32(p)==a && M32(p+4)==b && M32(p+8)==e && M32(p+12)==f;
}
static uint32_t query(Cpu *c) {
    WDObject *o=wd_live(A(0)); uint32_t p=A(1), dst=A(2);
    if (!out(dst,4)) return WD_INVALID;
    M32(dst)=0;
    if (!o || !out(p,16)) return WD_INVALID;
    int yes=guid(p,0,0,0x000000c0,0x46000000);
    switch (o->kind) {
    case WD_FACTORY: yes|=guid(p,0x81bdcbca,0x426d64d4,0x01ad8dae,0x5c27f447); break;
    case WD_DEVICE: yes|=guid(p,0xd0223b96,0x43fdbf7a,0x3ba4bd92,0xebb9820d); break;
    case WD_TEXTURE: yes|=guid(p,0x85c31227,0x4f003de5,0x1af13a9b,0xb5188cc3);
        yes|=guid(p,0x580ca87e,0x4d541d3c,0xd3b71d99,0xce98c2e3); break;
    case WD_VB: yes|=guid(p,0xb64bb1b5,0x4df6fd70,0xd01991bf,0xe35524a1); break;
    case WD_IB: yes|=guid(p,0x7c9dd65e,0x4529d3f7,0x5878eeac,0x35deac30); break;
    case WD_DECL: yes|=guid(p,0xdd13c59c,0x409836fa,0xedc7fba8,0x4685dc39); break;
    case WD_VS: yes|=guid(p,0xefc5557e,0x46136265,0x8543948a,0x36eb8978); break;
    case WD_PS: yes|=guid(p,0x6d3bdbdc,0x44155b02,0x5ece52b8,0x89b2cc8b); break;
    }
    if (o->kind==WD_TEXTURE || o->kind==WD_VB || o->kind==WD_IB)
        yes|=guid(p,0x05eec05d,0x43628f7d,0xbad199b9,0x04c757f3);
    if (!yes) return 0x80004002u;
    wd_addref(o->guest); M32(dst)=o->guest; return 0;
}
static uint32_t addref(Cpu *c) { return wd_live(A(0))?wd_addref(A(0)):0; }
static uint32_t release(Cpu *c) { return wd_release(A(0)); }
static uint32_t get_parent(Cpu *c) {
    WDObject *o=wd_live(A(0));
    if (!o || !o->parent || !out(A(1),4)) return WD_INVALID;
    wd_addref(o->parent); M32(A(1))=o->parent; return 0;
}
static void fill_caps(uint32_t p) {
    memset(G+p,0,304);
    M32(p)=1; M32(p+40)=255; M32(p+44)=0x3fff; M32(p+48)=0x3fff; M32(p+52)=255;
    M32(p+60)=0x100; M32(p+64)=0x03000300; M32(p+76)=0x3f;
    M32(p+88)=4096; M32(p+92)=4096; M32(p+108)=1;
    M32(p+148)=8; M32(p+152)=8;
    M32(p+180)=0x100000; M32(p+184)=0x100000; M32(p+188)=WD_STREAMS; M32(p+192)=2048;
    M32(p+196)=0xfffe0101u; M32(p+200)=256; M32(p+204)=0xffff0101u;
    M32(p+212)=1; M32(p+240)=1;
}
static uint32_t factory_caps(Cpu *c) {
    SELF(WD_FACTORY);
    if (A(1) || A(2)!=1 || !out(A(3),304)) return WD_INVALID;
    fill_caps(A(3)); return 0;
}
static uint32_t device_caps(Cpu *c) {
    SELF(WD_DEVICE); if (!out(A(1),304)) return WD_INVALID;
    fill_caps(A(1)); return 0;
}
static uint32_t adapter_count(Cpu *c) { SELF(WD_FACTORY); return 1; }
static uint32_t display_mode(Cpu *c) {
    SELF(WD_FACTORY); if (A(1) || !out(A(2),16)) return WD_INVALID;
    M32(A(2))=640; M32(A(2)+4)=480; M32(A(2)+8)=60; M32(A(2)+12)=22; return 0;
}
static unsigned bpp(uint32_t f) {
    switch (f) { case 21: case 22: return 4; case 23: return 2; case 28: case 50: return 1; default: return 0; }
}
static uint32_t check_format(Cpu *c) {
    SELF(WD_FACTORY);
    return !A(1) && A(2)==1 && (A(3)==21 || A(3)==22) &&
        !(A(4)&~0x200u) && A(5)==3 && bpp(A(6)) ? 0:WD_UNAVAILABLE;
}
static uint32_t check_type(Cpu *c) {
    SELF(WD_FACTORY);
    return !A(1) && A(2)==1 && A(3)==22 && (A(4)==21 || A(4)==22) && A(5)<=1 ? 0:WD_UNAVAILABLE;
}
static uint32_t check_multisample(Cpu *c) {
    SELF(WD_FACTORY);
    if (A(6) && !out(A(6),4)) return WD_INVALID;
    if (A(6)) M32(A(6))=0;
    if (A(1) || A(2)!=1 || !bpp(A(3)) || A(4)>1 || A(5)) return WD_UNAVAILABLE;
    if (A(6)) M32(A(6))=1;
    return 0;
}
static uint32_t create_device(Cpu *c) {
    SELF(WD_FACTORY); uint32_t pp=A(5), dst=A(6);
    if (!out(dst,4)) return WD_INVALID;
    M32(dst)=0;
    if (A(1) || A(2)!=1 || !out(pp,56) || !M32(pp) || !M32(pp+4) ||
        M32(pp)>4096 || M32(pp+4)>4096 || M32(pp+36)>1) return WD_INVALID;
    if ((M32(pp+8)!=21 && M32(pp+8)!=22) || M32(pp+16) || M32(pp+20) ||
        (M32(pp+36) && M32(pp+40)!=75 && M32(pp+40)!=80)) BEYOND("CreateDevice parameters");
    WDObject *v=wd_new(WD_DEVICE,vt[WD_DEVICE],o->guest);
    if (!v) return WD_OOM;
    wd_defaults(v->device,M32(pp),M32(pp+4),(int)M32(pp+36)); M32(dst)=v->guest; return 0;
}
static uint32_t factory(Cpu *c) {
    if (A(0)!=32) return 0;
    WDObject *o=wd_new(WD_FACTORY,vt[WD_FACTORY],0);
    return o?o->guest:0;
}
static uint32_t scene_begin(Cpu *c) { DEVICE; if (d->scene) return WD_INVALID; d->scene=1; return 0; }
static uint32_t scene_end(Cpu *c) { DEVICE; if (!d->scene) return WD_INVALID; d->scene=0; return 0; }
static uint32_t cooperative(Cpu *c) { SELF(WD_DEVICE); return 0; }
static uint32_t create_texture(Cpu *c) {
    SELF(WD_DEVICE); uint32_t dst=A(7), w=A(1), h=A(2), pitch;
    if (!out(dst,4)) return WD_INVALID;
    M32(dst)=0;
    if (!w || !h || w>4096 || h>4096 || (A(4)&~0x200u) || A(6)>3 || A(8)) return WD_INVALID;
    if (A(3)!=1 || !bpp(A(5))) BEYOND("CreateTexture levels/format");
    /* D3DPRESENT and resource outputs remain guest offsets. */
    pitch=w*bpp(A(5));
    uint64_t size=(uint64_t)pitch*h;
    if (size>WD_MAX_BYTES) return WD_INVALID;
    /* Take the table slot first: a full table must not consume guest arena. */
    WDObject *t=wd_new(WD_TEXTURE,vt[WD_TEXTURE],o->guest);
    if (!t) return WD_OOM;
    uint32_t mem=win_alloc((uint32_t)size);
    if (!mem) { wd_release(t->guest); return WD_OOM; }
    t->data=mem; t->size=(uint32_t)size; t->width=w; t->height=h; t->pitch=pitch;
    t->format=A(5); t->usage=A(4); t->pool=A(6); t->version=1;
    memset(G+mem,0,t->size); M32(dst)=t->guest; return 0;
}
static uint32_t create_buffer(Cpu *c, unsigned kind) {
    SELF(WD_DEVICE); uint32_t size=A(1), dst=A(5);
    if (!out(dst,4)) return WD_INVALID;
    M32(dst)=0;
    if (!size || size>WD_MAX_BYTES || (A(2)&~0x208u) || A(4)>3 || A(6) ||
        (kind==WD_IB && (A(3)!=101 && A(3)!=102))) return WD_INVALID;
    if (kind==WD_IB && size%(A(3)==101?2:4)) return WD_INVALID;
    WDObject *b=wd_new(kind,vt[kind],o->guest); if (!b) return WD_OOM;
    uint32_t mem=win_alloc(size); if (!mem) { wd_release(b->guest); return WD_OOM; }
    b->data=mem; b->size=size; b->usage=A(2); b->pool=A(4); b->version=1;
    if (kind==WD_IB) b->format=A(3); else b->fvf=A(3);
    memset(G+mem,0,size); M32(dst)=b->guest; return 0;
}
static uint32_t create_vb(Cpu *c) { return create_buffer(c,WD_VB); }
static uint32_t create_ib(Cpu *c) { return create_buffer(c,WD_IB); }
static int lock_flags(uint32_t flags) { return !(flags&~(0x10u|0x800u|0x1000u|0x2000u)) && !((flags&0x10) && (flags&0x2000)); }
static uint32_t buffer_lock(Cpu *c) {
    WDObject *o=wd_live(A(0)); uint32_t offset=A(1), size=A(2), dst=A(3), flags=A(4);
    if (!o || (o->kind!=WD_VB && o->kind!=WD_IB) || !out(dst,4)) return WD_INVALID;
    /* READONLY on a WRITEONLY buffer is invalid, as D3D9 specifies. */
    if (o->locked || offset>=o->size || !lock_flags(flags) ||
        ((flags&0x10) && (o->usage&0x8)) ||
        ((flags&0x3000) && !(o->usage&0x200))) return WD_INVALID;
    if (!size) size=o->size-offset;
    if ((uint64_t)offset+size>o->size || !out(o->data+offset,size)) return WD_INVALID;
    o->locked=1; o->lock_flags=flags; M32(dst)=o->data+offset; return 0;
}
static uint32_t buffer_unlock(Cpu *c) {
    WDObject *o=wd_live(A(0));
    if (!o || (o->kind!=WD_VB && o->kind!=WD_IB) || !o->locked) return WD_INVALID;
    if (!(o->lock_flags&0x10)) ++o->version;
    o->locked=0; return 0;
}
static uint32_t texture_lock(Cpu *c) {
    SELF(WD_TEXTURE); uint32_t dst=A(2), rect=A(3), flags=A(4);
    if (A(1) || o->locked || !out(dst,8) || !lock_flags(flags) || (flags&0x1000) ||
        ((flags&0x2000) && (!(o->usage&0x200) || rect))) return WD_INVALID;
    uint32_t left=0, top=0, right=o->width, bottom=o->height;
    if (rect) {
        if (!out(rect,16)) return WD_INVALID;
        left=M32(rect); top=M32(rect+4); right=M32(rect+8); bottom=M32(rect+12);
    }
    if (left>=right || top>=bottom || right>o->width || bottom>o->height) return WD_INVALID;
    o->locked=1; o->lock_flags=flags;
    M32(dst)=o->pitch; M32(dst+4)=o->data+top*o->pitch+left*bpp(o->format); return 0;
}
static uint32_t texture_unlock(Cpu *c) {
    SELF(WD_TEXTURE); if (A(1) || !o->locked) return WD_INVALID;
    if (!(o->lock_flags&0x10)) ++o->version;
    o->locked=0; return 0;
}
static uint32_t texture_levels(Cpu *c) { SELF(WD_TEXTURE); return 1; }
static uint32_t texture_desc(Cpu *c) {
    SELF(WD_TEXTURE); uint32_t p=A(2);
    if (A(1) || !out(p,32)) return WD_INVALID;
    M32(p)=o->format; M32(p+4)=1; M32(p+8)=o->usage; M32(p+12)=o->pool;
    M32(p+16)=0; M32(p+20)=0; M32(p+24)=o->width; M32(p+28)=o->height; return 0;
}
static uint32_t buffer_desc(Cpu *c) {
    WDObject *o=wd_live(A(0)); uint32_t p=A(1);
    if (!o || (o->kind!=WD_VB && o->kind!=WD_IB) || !out(p,o->kind==WD_VB?24:20)) return WD_INVALID;
    M32(p)=o->kind==WD_VB?100:o->format; M32(p+4)=o->kind==WD_VB?6:7;
    M32(p+8)=o->usage; M32(p+12)=o->pool; M32(p+16)=o->size;
    if (o->kind==WD_VB) M32(p+20)=o->fvf;
    return 0;
}
static uint32_t create_blob(Cpu *c, uint32_t kind) {
    SELF(WD_DEVICE); uint32_t ptr=A(1), dst=A(2), size=0;
    if (!out(dst,4)) return WD_INVALID;
    M32(dst)=0;
    if (kind==WD_DECL) {
        for (unsigned i=0;i<64;++i) {
            uint64_t p=(uint64_t)ptr+i*8;
            if (p>UINT32_MAX || !out((uint32_t)p,8)) return WD_INVALID;
            uint32_t stream=M16((uint32_t)p), type=M8((uint32_t)p+4);
            if (stream==255) {
                if (!i || M16((uint32_t)p+2) || type!=17 || M8((uint32_t)p+5) ||
                    M16((uint32_t)p+6)) return WD_INVALID;
                size=(i+1)*8; break;
            }
            if (stream>=WD_STREAMS || type>16 || M8((uint32_t)p+5)>6 ||
                M8((uint32_t)p+6)>13 || M8((uint32_t)p+7)>15) return WD_INVALID;
            if (type>5 || M8((uint32_t)p+5)) BEYOND("vertex element type/method");
        }
    } else {
        if (!out(ptr,4) || M32(ptr)!=(kind==WD_VS?0xfffe0101u:0xffff0101u)) return WD_INVALID;
        /* SM1 tokens: parameters have bit 31 set and instructions do not,
         * except def's four literals and sized comments. Lengths are therefore
         * exact without a per-opcode table. */
        unsigned i=1;
        while (i<4096) {
            uint64_t p=(uint64_t)ptr+i*4;
            if (p>UINT32_MAX || !out((uint32_t)p,4)) return WD_INVALID;
            uint32_t token=M32((uint32_t)p), op=token&0xffff, n=1;
            if (token==0xffff) { size=(i+1)*4; break; }
            if (token>>31) return WD_INVALID;
            if (op==0xfffe) n=((token>>16)&0x7fff)+1;
            else if (op==81) n=6;
            else while (n<8 && i+n<4096 && out((uint32_t)p+n*4,4) && M32((uint32_t)p+n*4)>>31) ++n;
            if ((n==1 && op) || i+n>=4096 || !out((uint32_t)p,(uint64_t)n*4)) return WD_INVALID;
            i+=n;
        }
    }
    if (!size) return WD_INVALID;
    WDObject *b=wd_new(kind,vt[kind],o->guest); if (!b) return WD_OOM;
    uint32_t mem=win_alloc(size); if (!mem) { wd_release(b->guest); return WD_OOM; }
    b->data=mem; b->size=size; b->version=1;
    memcpy(G+mem,G+ptr,size); M32(dst)=b->guest; return 0;
}
static uint32_t create_decl(Cpu *c) { return create_blob(c,WD_DECL); }
static uint32_t create_vs(Cpu *c) { return create_blob(c,WD_VS); }
static uint32_t create_ps(Cpu *c) { return create_blob(c,WD_PS); }
static uint32_t get_blob(Cpu *c, uint32_t kind) {
    SELF(kind); uint32_t dst=A(1), count=A(2), unit=kind==WD_DECL?8:1;
    if (!out(count,4)) return WD_INVALID;
    uint32_t required=o->size/unit, available=M32(count);
    M32(count)=required;
    if (!dst) return 0;
    if (available<required || !out(dst,o->size)) return WD_INVALID;
    memcpy(G+dst,G+o->data,o->size); return 0;
}
static uint32_t declaration_data(Cpu *c) { return get_blob(c,WD_DECL); }
static uint32_t get_shader(Cpu *c) {
    WDObject *o=wd_live(A(0));
    if (!o || (o->kind!=WD_VS && o->kind!=WD_PS)) return WD_INVALID;
    return get_blob(c,o->kind);
}
static int compatible(WDObject *o, uint32_t ptr, uint32_t kind) {
    WDObject *r=wd_live(ptr);
    return !ptr || (r && r->kind==kind && r->parent==o->guest);
}
#define BINDING(name,field,kind) \
static uint32_t set_##name(Cpu *c) { DEVICE; if (!compatible(o,A(1),kind)) return WD_INVALID; wd_bind(&d->field,A(1)); return 0; } \
static uint32_t get_##name(Cpu *c) { DEVICE; if (!out(A(1),4)) return WD_INVALID; M32(A(1))=d->field; if (d->field) wd_addref(d->field); return 0; }
BINDING(vs,vs,WD_VS)
BINDING(ps,ps,WD_PS)
BINDING(decl,declaration,WD_DECL)
BINDING(indices,indices,WD_IB)
static uint32_t set_texture(Cpu *c) {
    DEVICE; if (A(1)>=WD_TEXTURES || !compatible(o,A(2),WD_TEXTURE)) return WD_INVALID;
    wd_bind(&d->textures[A(1)],A(2)); return 0;
}
static uint32_t get_texture(Cpu *c) {
    DEVICE; if (A(1)>=WD_TEXTURES || !out(A(2),4)) return WD_INVALID;
    uint32_t p=d->textures[A(1)]; M32(A(2))=p; if (p) wd_addref(p); return 0;
}
static uint32_t set_stream(Cpu *c) {
    DEVICE;
    if (A(1)>=WD_STREAMS || !compatible(o,A(2),WD_VB) || A(4)>2048) return WD_INVALID;
    WDObject *b=wd_live(A(2));
    if (b && (!A(4) || A(3)>=b->size)) return WD_INVALID;
    WinD3D9Stream *s=d->streams+A(1);
    wd_bind(&s->resource,A(2)); s->offset=A(3); s->stride=A(4); return 0;
}
static uint32_t get_stream(Cpu *c) {
    DEVICE;
    if (A(1)>=WD_STREAMS || !out(A(2),4) || !out(A(3),4) || !out(A(4),4) ||
        A(2)==A(3) || A(2)==A(4) || A(3)==A(4)) return WD_INVALID;
    WinD3D9Stream *s=d->streams+A(1);
    M32(A(2))=s->resource; M32(A(3))=s->offset; M32(A(4))=s->stride;
    if (s->resource) wd_addref(s->resource);
    return 0;
}
static uint32_t constants(Cpu *c, int pixel, int get) {
    DEVICE; uint32_t start=A(1), ptr=A(2), count=A(3), limit=pixel?224:256;
    if (start>limit || count>limit-start || (count && !out(ptr,(uint64_t)count*16))) return WD_INVALID;
    void *native=pixel?(void *)d->state.ps_constants:(void *)d->state.vs_constants;
    if (count) {
        if (get) memcpy(G+ptr,(uint8_t *)native+start*16,count*16);
        else memcpy((uint8_t *)native+start*16,G+ptr,count*16);
    }
    if (!get) { if (pixel) ++d->state.ps_version; else ++d->state.vs_version; }
    return 0;
}
static uint32_t set_vsc(Cpu *c) { return constants(c,0,0); }
static uint32_t get_vsc(Cpu *c) { return constants(c,0,1); }
static uint32_t set_psc(Cpu *c) { return constants(c,1,0); }
static uint32_t get_psc(Cpu *c) { return constants(c,1,1); }
static int render_state(uint32_t n) {
    switch (n) {
    case 7: case 8: case 9: case 14: case 15: case 19: case 20: case 22: case 23:
    case 24: case 25: case 27: case 52: case 53: case 54: case 55: case 56: case 57:
    case 58: case 59: case 137: case 168: case 171: case 174: case 175: case 176:
    case 185: case 186: case 187: case 188: case 189: case 190: case 191: case 192:
    case 193: case 194: case 195: case 206: case 207: case 208: case 209: return 1;
    default: return 0;
    }
}
static uint32_t set_rs(Cpu *c) { DEVICE; if (!render_state(A(1))) BEYOND("render state"); d->state.rs[A(1)]=A(2); return 0; }
static uint32_t get_rs(Cpu *c) {
    DEVICE; if (!render_state(A(1))) BEYOND("render state");
    if (!out(A(2),4)) return WD_INVALID;
    M32(A(2))=d->state.rs[A(1)]; return 0;
}
static uint32_t set_sampler(Cpu *c) {
    DEVICE; if (A(1)>=WD_TEXTURES || A(2)<1 || A(2)>13) return WD_INVALID;
    d->state.sampler[A(1)][A(2)]=A(3); return 0;
}
static uint32_t get_sampler(Cpu *c) {
    DEVICE; if (A(1)>=WD_TEXTURES || A(2)<1 || A(2)>13 || !out(A(3),4)) return WD_INVALID;
    M32(A(3))=d->state.sampler[A(1)][A(2)]; return 0;
}
static int tss_state(uint32_t n) {
    return (n>=1 && n<=11) || (n>=22 && n<=24) || (n>=26 && n<=28) || n==32;
}
static uint32_t set_tss(Cpu *c) {
    DEVICE; if (A(1)>=8 || !tss_state(A(2))) return WD_INVALID;
    d->state.tss[A(1)][A(2)]=A(3); return 0;
}
static uint32_t get_tss(Cpu *c) {
    DEVICE; if (A(1)>=8 || !tss_state(A(2)) || !out(A(3),4)) return WD_INVALID;
    M32(A(3))=d->state.tss[A(1)][A(2)]; return 0;
}
static uint32_t set_viewport(Cpu *c) {
    DEVICE; uint32_t p=A(1);
    if (!out(p,24) || !M32(p+8) || !M32(p+12) ||
        (uint64_t)M32(p)+M32(p+8)>4096 || (uint64_t)M32(p+4)+M32(p+12)>4096 ||
        !(MF32(p+16)>=0 && MF32(p+20)<=1 && MF32(p+16)<=MF32(p+20))) return WD_INVALID;
    memcpy(d->state.viewport,G+p,24); return 0;
}
static uint32_t get_viewport(Cpu *c) { DEVICE; if (!out(A(1),24)) return WD_INVALID; memcpy(G+A(1),d->state.viewport,24); return 0; }
static uint32_t set_scissor(Cpu *c) {
    DEVICE; uint32_t p=A(1);
    if (!out(p,16) || M32(p)>M32(p+8) || M32(p+4)>M32(p+12) || M32(p+8)>4096 || M32(p+12)>4096) return WD_INVALID;
    memcpy(d->state.scissor,G+p,16); return 0;
}
static uint32_t get_scissor(Cpu *c) { DEVICE; if (!out(A(1),16)) return WD_INVALID; memcpy(G+A(1),d->state.scissor,16); return 0; }
static uint32_t draw(Cpu *c) {
    SELF(WD_DEVICE); return wd_draw(o,A(1),A(3),0,0,0,0,A(2),0,0,0,0);
}
static uint32_t draw_indexed(Cpu *c) {
    SELF(WD_DEVICE); return wd_draw(o,A(1),A(6),1,(int32_t)A(2),A(3),A(4),A(5),0,0,0,0);
}
static uint32_t draw_up(Cpu *c) {
    DEVICE;
    uint32_t result=A(3)?wd_draw(o,A(1),A(2),0,0,0,0,0,A(3),A(4),0,0):WD_INVALID;
    /* A recorded UP draw leaves stream 0 unbound, as D3D9 specifies. */
    if (!result) { wd_bind(&d->streams[0].resource,0); d->streams[0].offset=0; d->streams[0].stride=0; }
    return result;
}
static uint32_t draw_indexed_up(Cpu *c) {
    DEVICE;
    uint32_t result=A(7)?wd_draw(o,A(1),A(4),1,0,A(2),A(3),0,A(7),A(8),A(5),A(6)):WD_INVALID;
    /* Indexed UP also leaves the index buffer unbound. */
    if (!result) {
        wd_bind(&d->streams[0].resource,0); d->streams[0].offset=0; d->streams[0].stride=0;
        wd_bind(&d->indices,0);
    }
    return result;
}
static uint32_t register_callback(const char *name, WinAdapter call, unsigned args) {
    uint32_t p=win_host_register(name,call,args);
    if (p) ++callbacks;
    return p;
}
static int slot(unsigned kind, unsigned index, const char *name, WinAdapter call, unsigned args) {
    uint32_t p=register_callback(name,call,args);
    if (!p) return 0;
    M32(vt[kind]+index*4)=p; return 1;
}
unsigned win_d3d9_callbacks(void) { return callbacks; }
int win_d3d9_register(void) {
    static unsigned started;
    if (started) return 0;
    started=1;
    uint32_t fail=register_callback("D3D9.unsupported-slot",unsupported,1);
    uint32_t qi=register_callback("IUnknown.QueryInterface",query,3);
    uint32_t ar=register_callback("IUnknown.AddRef",addref,1);
    uint32_t re=register_callback("IUnknown.Release",release,1);
    uint32_t gp=register_callback("D3D9.GetParent",get_parent,2);
    if (!fail || !qi || !ar || !re || !gp) return 0;
    for (unsigned k=WD_FACTORY;k<=WD_PS;++k) {
        unsigned n=k==WD_FACTORY?17:k==WD_DEVICE?119:k==WD_TEXTURE?22:(k==WD_VB || k==WD_IB)?14:5;
        vt[k]=win_alloc(n*4); if (!vt[k]) return 0;
        for (unsigned i=0;i<n;++i) M32(vt[k]+i*4)=fail;
        M32(vt[k])=qi; M32(vt[k]+4)=ar; M32(vt[k]+8)=re;
        if (k>=WD_TEXTURE) M32(vt[k]+12)=gp;
    }
    M32(vt[WD_DEVICE]+24)=gp;
#define S(k,i,n,f,a) if (!slot(k,i,n,f,a)) return 0
#define F(i,n,f,a) S(WD_FACTORY,i,"IDirect3D9." n,f,a)
#define D(i,n,f,a) S(WD_DEVICE,i,"IDirect3DDevice9." n,f,a)
    F(4,"GetAdapterCount",adapter_count,1);
    F(8,"GetAdapterDisplayMode",display_mode,3);
    F(9,"CheckDeviceType",check_type,6);
    F(10,"CheckDeviceFormat",check_format,7);
    F(11,"CheckDeviceMultiSampleType",check_multisample,7);
    F(14,"GetDeviceCaps",factory_caps,4);
    F(16,"CreateDevice",create_device,7);
    D(3,"TestCooperativeLevel",cooperative,1);
    D(7,"GetDeviceCaps",device_caps,2);
    D(23,"CreateTexture",create_texture,9);
    D(26,"CreateVertexBuffer",create_vb,7);
    D(27,"CreateIndexBuffer",create_ib,7);
    D(41,"BeginScene",scene_begin,1); D(42,"EndScene",scene_end,1);
    D(47,"SetViewport",set_viewport,2); D(48,"GetViewport",get_viewport,2);
    D(57,"SetRenderState",set_rs,3); D(58,"GetRenderState",get_rs,3);
    D(64,"GetTexture",get_texture,3); D(65,"SetTexture",set_texture,3);
    D(66,"GetTextureStageState",get_tss,4); D(67,"SetTextureStageState",set_tss,4);
    D(68,"GetSamplerState",get_sampler,4); D(69,"SetSamplerState",set_sampler,4);
    D(75,"SetScissorRect",set_scissor,2); D(76,"GetScissorRect",get_scissor,2);
    D(81,"DrawPrimitive",draw,4); D(82,"DrawIndexedPrimitive",draw_indexed,7);
    D(83,"DrawPrimitiveUP",draw_up,5); D(84,"DrawIndexedPrimitiveUP",draw_indexed_up,9);
    D(86,"CreateVertexDeclaration",create_decl,3); D(87,"SetVertexDeclaration",set_decl,2);
    D(88,"GetVertexDeclaration",get_decl,2);
    D(91,"CreateVertexShader",create_vs,3); D(92,"SetVertexShader",set_vs,2);
    D(93,"GetVertexShader",get_vs,2);
    D(94,"SetVertexShaderConstantF",set_vsc,4); D(95,"GetVertexShaderConstantF",get_vsc,4);
    D(100,"SetStreamSource",set_stream,5); D(101,"GetStreamSource",get_stream,5);
    D(104,"SetIndices",set_indices,2); D(105,"GetIndices",get_indices,2);
    D(106,"CreatePixelShader",create_ps,3); D(107,"SetPixelShader",set_ps,2);
    D(108,"GetPixelShader",get_ps,2);
    D(109,"SetPixelShaderConstantF",set_psc,4); D(110,"GetPixelShaderConstantF",get_psc,4);
    /* Resource callbacks are shared between buffer interfaces. */
    uint32_t bl=register_callback("Buffer.Lock",buffer_lock,5);
    uint32_t bu=register_callback("Buffer.Unlock",buffer_unlock,1);
    uint32_t bd=register_callback("Buffer.GetDesc",buffer_desc,2);
    if (!bl || !bu || !bd) return 0;
    for (unsigned k=WD_VB;k<=WD_IB;++k) {
        M32(vt[k]+44)=bl; M32(vt[k]+48)=bu; M32(vt[k]+52)=bd;
    }
    S(WD_TEXTURE,13,"Texture.GetLevelCount",texture_levels,1);
    S(WD_TEXTURE,17,"Texture.GetLevelDesc",texture_desc,3);
    S(WD_TEXTURE,19,"Texture.LockRect",texture_lock,5);
    S(WD_TEXTURE,20,"Texture.UnlockRect",texture_unlock,2);
    S(WD_DECL,4,"Declaration.GetDeclaration",declaration_data,3);
    uint32_t gs=register_callback("Shader.GetFunction",get_shader,3);
    if (!gs) return 0;
    M32(vt[WD_VS]+16)=gs; M32(vt[WD_PS]+16)=gs;
#undef D
#undef F
#undef S
    return win_import_register("d3d9.dll!Direct3DCreate9",factory,1);
}
