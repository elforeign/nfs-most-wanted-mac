#include "host.h"
#include "user32.h"
#include "input.h"
#include "dinput.h"
#define INVALID 0x80070057u
#define NOINTERFACE 0x80004002u
#define NOTACQUIRED 0x8007000cu
#define NOTBUFFERED 0x80040207u
#define NOTREGISTERED 0x80040154u
#define UNSUPPORTED 0x80004001u
#define OBJECTS 32u
typedef struct { uint32_t guest,refs,kind,pad,acquired,buffer,cursor,size; int32_t rmin[WIN_PAD_AXES],rmax[WIN_PAD_AXES]; } Device;
static Device devices[OBJECTS];
static uint32_t root_vtable,device_vtable;
static Device *object(uint32_t p) {
    for (unsigned i=0;i<OBJECTS;++i) if (devices[i].refs && devices[i].guest==p) return devices+i;
    return NULL;
}
static Device *self(Cpu *c) { return object(win_arg(c,0)); }
static uint32_t new_object(unsigned kind,unsigned pad) {
    for (unsigned i=0;i<OBJECTS;++i) if (!devices[i].refs) {
        uint32_t p=win_alloc(336); if (!p) return 0;
        devices[i]=(Device){.guest=p,.refs=1,.kind=kind,.pad=pad,.size=kind==WIN_KEYBOARD?256:kind==WIN_MOUSE?20:272};
        for (unsigned k=0;k<WIN_PAD_AXES;++k) devices[i].rmax[k]=65535;
        M32(p)=kind?device_vtable:root_vtable; return p;
    }
    return 0;
}
static int guid(uint32_t p,uint32_t a,uint32_t b,uint32_t c,uint32_t d) {
    return win_vm_access(p,16) && M32(p)==a && M32(p+4)==b && M32(p+8)==c && M32(p+12)==d;
}
static int iid(uint32_t p,int root) {
    return guid(p,0,0,0xc0,0x46000000) || (root?guid(p,0xbf798030,0x4da2483a,0x645d99aa,0x009736ed):
        guid(p,0x54d41080,0x4833dc15,0x8f741ba4,0x7981a373));
}
static unsigned device_kind(uint32_t p,unsigned *pad) {
    if (!win_vm_access(p,16) || M32(p+4)!=0x11cfd5a0 || M32(p+8)!=0x4544c7bf || M32(p+12)!=0x00005453) return 0;
    uint32_t a=M32(p);
    if (a==0x6f1d2b61) return WIN_KEYBOARD;
    if (a==0x6f1d2b60) return WIN_MOUSE;
    if (a>=0x6f1d2b70 && a<0x6f1d2b70+WIN_PAD_COUNT) { *pad=a-0x6f1d2b70; return WIN_PAD; }
    return 0;
}
static void write_guid(uint32_t p,uint32_t a) { M32(p)=a; M32(p+4)=0x11cfd5a0; M32(p+8)=0x4544c7bf; M32(p+12)=0x00005453; }
static uint32_t query(Cpu *c) {
    Device *d=self(c); uint32_t p=win_arg(c,2);
    if (!d || !win_vm_access(p,4)) return INVALID;
    M32(p)=0;
    if (!iid(win_arg(c,1),!d->kind)) return NOINTERFACE;
    ++d->refs; M32(p)=d->guest; return 0;
}
static uint32_t addref(Cpu *c) { Device *d=self(c); return d?++d->refs:0; }
static uint32_t release(Cpu *c) { Device *d=self(c); return d?--d->refs:0; }
static uint32_t factory(Cpu *c) {
    uint32_t p=win_arg(c,3); if (!win_vm_access(p,4)) return INVALID;
    M32(p)=0;
    if (win_arg(c,4) || win_arg(c,1)!=0x800) return INVALID;
    if (!iid(win_arg(c,2),1)) return NOINTERFACE;
    M32(p)=new_object(0,0); return M32(p)?0:0x8007000eu;
}
static uint32_t create_device(Cpu *c) {
    Device *d=self(c); uint32_t p=win_arg(c,2); unsigned pad=0;
    if (!d || d->kind || !win_vm_access(p,4)) return INVALID;
    M32(p)=0;
    if (win_arg(c,3)) return INVALID;
    unsigned kind=device_kind(win_arg(c,1),&pad); if (!kind) return NOTREGISTERED;
    M32(p)=new_object(kind,pad); return M32(p)?0:0x8007000eu;
}
static void instance(uint32_t p,unsigned kind,unsigned pad) {
    memset(G+p,0,580); M32(p)=580;
    uint32_t id=kind==WIN_KEYBOARD?0x6f1d2b61:kind==WIN_MOUSE?0x6f1d2b60:0x6f1d2b70+pad;
    write_guid(p+4,id); write_guid(p+20,id);
    M32(p+36)=kind==WIN_KEYBOARD?0x413:kind==WIN_MOUSE?0x112:0x10215;
    const char *name=kind==WIN_KEYBOARD?"Keyboard":kind==WIN_MOUSE?"Mouse":pad?"Controller 2":"Controller 1";
    for (unsigned i=0;name[i];++i) { M8(p+40+i)=(uint8_t)name[i]; M8(p+300+i)=(uint8_t)name[i]; }
}
static uint32_t enumerate(Cpu *c) {
    Device *d=self(c); if (!d || d->kind || !win_arg(c,2)) return INVALID;
    uint32_t type=win_arg(c,1),p=win_alloc(580); if (!p) return 0x8007000eu;
    for (unsigned kind=1;kind<=3;++kind) {
        unsigned cls=kind==WIN_KEYBOARD?3:kind==WIN_MOUSE?2:4;
        unsigned subtype=kind==WIN_KEYBOARD?0x13:kind==WIN_MOUSE?0x12:0x15;
        if (type && type!=cls && type!=subtype && !(type==1 && kind!=WIN_PAD)) continue;
        for (unsigned pad=0;pad<(kind==WIN_PAD?WIN_PAD_COUNT:1);++pad) {
            instance(p,kind,pad); uint32_t args[2]={p,win_arg(c,3)};
            if (!win_callback(c,win_arg(c,2),2,args)) return 0;
        }
    }
    return 0;
}
static uint32_t status(Cpu *c) { unsigned pad=0; return device_kind(win_arg(c,1),&pad)?0:NOTREGISTERED; }
static uint32_t capabilities(Cpu *c) {
    Device *d=self(c); uint32_t p=win_arg(c,1); if (!d || !d->kind || !win_vm_access(p,44) || M32(p)!=44) return INVALID;
    memset(G+p+4,0,40); M32(p+4)=1; M32(p+8)=d->kind==WIN_KEYBOARD?0x413:d->kind==WIN_MOUSE?0x112:0x10215;
    M32(p+12)=d->kind==WIN_KEYBOARD?0:d->kind==WIN_MOUSE?3:WIN_PAD_AXES;
    M32(p+16)=d->kind==WIN_KEYBOARD?256:d->kind==WIN_MOUSE?8:12; M32(p+20)=d->kind==WIN_PAD?1:0; return 0;
}
static uint32_t get_info(Cpu *c) { Device *d=self(c); uint32_t p=win_arg(c,1); if (!d || !d->kind || !win_vm_access(p,580) || M32(p)!=580) return INVALID; instance(p,d->kind,d->pad); return 0; }
static uint32_t acquire(Cpu *c) { Device *d=self(c); if (!d || !d->kind) return INVALID; win_user32_pump(); if (d->acquired) return 1; d->acquired=1; d->cursor=win_input_tail(d->kind); return 0; }
static uint32_t unacquire(Cpu *c) { Device *d=self(c); if (!d || !d->kind) return INVALID; d->acquired=0; d->cursor=win_input_tail(d->kind); return 0; }
static uint32_t state(Cpu *c) {
    Device *d=self(c); uint32_t n=win_arg(c,1),p=win_arg(c,2);
    if (!d || !d->kind || !win_vm_access(p,n) || n!=d->size) return INVALID;
    if (!d->acquired) return NOTACQUIRED;
    win_user32_pump();
    if (d->kind!=WIN_PAD) win_input_state(d->kind,p,n);
    else {
        WinPadState s; win_input_pad_read(d->pad,&s); memset(G+p,0,n);
        for (unsigned i=0;i<WIN_PAD_AXES;++i) {
            uint32_t v=s.axes[i]>65535?65535:s.axes[i];
            int64_t range=(int64_t)d->rmax[i]-d->rmin[i];
            M32(p+4*i)=(uint32_t)((int64_t)d->rmin[i]+(range*v+32767)/65535);
        }
        for (unsigned i=0;i<4;++i) M32(p+32+4*i)=i?UINT32_MAX:s.pov;
        for (unsigned i=0;i<32;++i) M8(p+48+i)=(s.buttons&(1u<<i))?128:0;
    }
    return 0;
}
static uint32_t data(Cpu *c) {
    Device *d=self(c); uint32_t sz=win_arg(c,1),p=win_arg(c,2),inout=win_arg(c,3),flags=win_arg(c,4);
    if (!d || d->kind<1 || d->kind>2 || !win_vm_access(inout,4) || (sz!=16 && sz!=20) || flags>1) return INVALID;
    uint32_t requested=M32(inout); M32(inout)=0;
    if (!d->acquired) return NOTACQUIRED;
    if (!d->buffer) return NOTBUFFERED;
    if (p && requested && !win_vm_access(p,(uint64_t)sz*requested)) return INVALID;
    win_user32_pump(); uint32_t cursor=d->cursor; int overflow=0;
    WinInputEvent events[256]; unsigned cap=requested>256?256:requested;
    unsigned n=win_input_events(d->kind,&cursor,events,cap,d->buffer,&overflow);
    for (unsigned i=0;p && i<n;++i) {
        uint32_t e=p+i*sz; memset(G+e,0,sz); M32(e)=events[i].offset; M32(e+4)=events[i].data;
        M32(e+8)=events[i].time; M32(e+12)=events[i].sequence;
    }
    if (!(flags&1)) d->cursor=cursor;
    M32(inout)=n; return overflow?1:0;
}
static uint32_t property(Cpu *c,int set) {
    Device *d=self(c); uint32_t prop=win_arg(c,1),p=win_arg(c,2);
    if (!d || !d->kind || !win_vm_access(p,16) || M32(p+4)!=16) return INVALID;
    if (prop==4 && d->kind==WIN_PAD) { // DIPROP_RANGE: device, offset or object id
        if (!win_vm_access(p,24) || M32(p)!=24) return INVALID;
        uint32_t how=M32(p+12),index=M32(p+8),axis=0;
        if (how==1) { if (index%4 || index>=24) return INVALID; axis=index/4; }
        else if (how==2) { axis=(index>>8)&65535; if (!(index&3) || axis>=WIN_PAD_AXES) return INVALID; }
        else if (how || index) return INVALID;
        if (set) {
            if (d->acquired) return 0x800700aau;
            int32_t low=(int32_t)M32(p+16),high=(int32_t)M32(p+20); if (low>=high) return INVALID;
            for (unsigned i=how?axis:0;i<(how?axis+1:WIN_PAD_AXES);++i) { d->rmin[i]=low;d->rmax[i]=high; }
        } else { M32(p+16)=(uint32_t)d->rmin[axis];M32(p+20)=(uint32_t)d->rmax[axis]; }
        return 0;
    }
    if (prop!=1) return UNSUPPORTED;
    if (!win_vm_access(p,20) || M32(p)!=20 || M32(p+8) || M32(p+12)) return INVALID;
    if (set) { if (d->acquired) return 0x800700aau; if (M32(p+16)>256) return INVALID; d->buffer=M32(p+16); d->cursor=win_input_tail(d->kind); }
    else M32(p+16)=d->buffer;
    return 0;
}
static uint32_t get_property(Cpu *c) { return property(c,0); }
static uint32_t set_property(Cpu *c) { return property(c,1); }
static uint32_t format(Cpu *c) {
    Device *d=self(c); uint32_t p=win_arg(c,1);
    if (!d || !d->kind || !win_vm_access(p,24) || M32(p)!=24 || M32(p+4)!=16) return INVALID;
    uint32_t n=M32(p+12);
    if (M32(p+8)>2 || (d->kind==WIN_MOUSE && M32(p+8)==1)) return UNSUPPORTED;
    if (!((d->kind==WIN_KEYBOARD && n==256) || (d->kind==WIN_MOUSE && (n==16 || n==20)) || (d->kind==WIN_PAD && (n==80 || n==272)))) return UNSUPPORTED;
    uint32_t objects=M32(p+16),array=M32(p+20);
    if (objects>256 || (objects && !win_vm_access(array,16ull*objects))) return INVALID;
    for (unsigned i=0;i<objects;++i) {
        uint32_t entry=array+16*i,offset=M32(entry+4),type=M32(entry+8),id=(type>>8)&65535;
        if (offset>=n) return INVALID;
        // Standard joystick formats also describe optional sliders/velocity axes and extra POVs.
        if (d->kind==WIN_PAD && (type&0x80000000u) &&
            (((type&3) && offset>=24) || ((type&16) && offset!=32))) continue;
        uint32_t object_guid=M32(entry);
        if (object_guid) {
            if (!win_vm_access(object_guid,16)) return INVALID;
            uint32_t expected=(type&3)?(offset<12?0xa36d02e0+offset/4:offset==12?0xa36d02f4:offset==16?0xa36d02f5:0xa36d02e3):
                (type&16)?0xa36d02f2:d->kind==WIN_KEYBOARD?0x55728220:0xa36d02f0;
            if (!guid(object_guid,expected,d->kind==WIN_KEYBOARD?0x11cfd33c:0x11cfc9f3,0x4544c7bf,0x5453)) return UNSUPPORTED;
        }
        // This slice accepts the standard layouts; reject remaps instead of returning wrong offsets.
        if (d->kind==WIN_KEYBOARD) {
            if (!(type&12) || (id!=65535 && id!=offset)) return UNSUPPORTED;
        } else if (type&3) {
            unsigned axes=d->kind==WIN_MOUSE?3:WIN_PAD_AXES;
            if (offset%4 || offset>=4*axes || (id!=65535 && id!=offset/4)) return UNSUPPORTED;
        } else if (type&12) {
            unsigned base=d->kind==WIN_MOUSE?12:48;
            if (offset<base || (id!=65535 && id!=offset-base)) return UNSUPPORTED;
        } else if (!(d->kind==WIN_PAD && (type&16) && offset==32)) return UNSUPPORTED;
    }
    if (d->acquired) return 0x800700aau;
    d->size=n; return 0;
}
static uint32_t cooperative(Cpu *c) { return self(c) && win_user32_native(win_arg(c,1))?0:INVALID; }
static uint32_t ok(Cpu *c) { return self(c)?0:INVALID; }
static uint32_t unsupported(Cpu *c) { (void)c; return UNSUPPORTED; }
static unsigned object_count(const Device *d) { return d->kind==WIN_KEYBOARD?256:d->kind==WIN_MOUSE?11:19; }
static void object_info(const Device *d,unsigned i,uint32_t p) {
    unsigned axis=d->kind!=WIN_KEYBOARD && i<(d->kind==WIN_MOUSE?3:WIN_PAD_AXES);
    unsigned pov=d->kind==WIN_PAD && i==6;
    uint32_t type=axis?(d->kind==WIN_MOUSE?1:2):pov?16:d->kind==WIN_KEYBOARD?12:4;
    memset(G+p,0,316); M32(p)=316;
    uint32_t a=axis?(i<3?0xa36d02e0+i:i==3?0xa36d02f4:i==4?0xa36d02f5:0xa36d02e3):pov?0xa36d02f2:d->kind==WIN_KEYBOARD?0x55728220:0xa36d02f0;
    write_guid(p+4,a); M32(p+8)=d->kind==WIN_KEYBOARD?0x11cfd33c:0x11cfc9f3;
    unsigned index=axis?i:pov?0:d->kind==WIN_KEYBOARD?i:d->kind==WIN_MOUSE?i-3:i-7;
    M32(p+20)=axis?i*4:pov?32:d->kind==WIN_KEYBOARD?i:d->kind==WIN_MOUSE?12+index:48+index;
    M32(p+24)=type|(index<<8);
    if (d->kind==WIN_KEYBOARD) {
        int32_t scan=(int32_t)(((i&127)<<16)|((i&128)<<17));
        if (!GetKeyNameTextA(scan,(char *)G+p+32,260)) M8(p+32)='?';
    } else {
        static const char *const axes[]={"X Axis","Y Axis","LT","X Rotation","Y Rotation","RT"};
        const char *name=axis?(d->kind==WIN_MOUSE && i==2?"Wheel":axes[i]):pov?"D-Pad":"Button";
        for (unsigned k=0;name[k];++k) M8(p+32+k)=(uint8_t)name[k];
        if (!axis && !pov) { M8(p+38)=' ';M8(p+39)=(uint8_t)('0'+(index+1)/10);M8(p+40)=(uint8_t)('0'+(index+1)%10); }
    }
}
static uint32_t enum_objects(Cpu *c) {
    Device *d=self(c); if (!d || !d->kind || !win_arg(c,1)) return INVALID;
    uint32_t p=win_alloc(316); if (!p) return 0x8007000eu;
    for (unsigned i=0;i<object_count(d);++i) {
        object_info(d,i,p);
        if (win_arg(c,3) && !(win_arg(c,3)&M32(p+24)&255)) continue;
        uint32_t args[2]={p,win_arg(c,2)}; if (!win_callback(c,win_arg(c,1),2,args)) break;
    }
    return 0;
}
static uint32_t get_object(Cpu *c) {
    Device *d=self(c); uint32_t p=win_arg(c,1),value=win_arg(c,2),how=win_arg(c,3);
    if (!d || !d->kind || !win_vm_access(p,316) || M32(p)!=316 || (how!=1 && how!=2)) return INVALID;
    // Reuse a guest scratch description; no failed lookup overwrites the output.
    uint32_t scratch=d->guest+16;
    for (unsigned i=0;i<object_count(d);++i) {
        object_info(d,i,scratch);
        if (how==1?M32(scratch+20)==value:(M32(scratch+24)&0xffff1f)==(value&0xffff1f)) {
            for (unsigned k=0;k<316;++k) M8(p+k)=M8(scratch+k); return 0;
        }
    }
    return 0x80070002u;
}
static int slot(uint32_t table,unsigned i,int root,WinAdapter f,unsigned n) {
    char name[]="DInputDevice8.slot00";
    if (root) name[6]='R';
    name[18]=(char)('0'+i/10); name[19]=(char)('0'+i%10);
    uint32_t p=win_host_register(name,f,n); if (!p) return 0; M32(table+4*i)=p; return 1;
}
int win_dinput_register(void) {
    if (root_vtable) return 0;
    root_vtable=win_alloc(44); device_vtable=win_alloc(128); if (!root_vtable || !device_vtable) return 0;
    static const unsigned root_args[]={3,1,1,4,5,2,3,3,4,6,5};
    static const unsigned device_args[]={3,1,1,2,4,3,3,1,1,3,5,2,2,3,4,2,3,4,5,4,3,2,2,4,2,1,5,5,5,4,4,2};
    for (unsigned i=0;i<11;++i) {
        WinAdapter f=unsupported;
        switch (i) { case 0:f=query;break; case 1:f=addref;break; case 2:f=release;break;
            case 3:f=create_device;break; case 4:f=enumerate;break; case 5:f=status;break; case 7:f=ok;break; }
        if (!slot(root_vtable,i,1,f,root_args[i])) return 0;
    }
    for (unsigned i=0;i<32;++i) {
        WinAdapter f=unsupported;
        switch (i) { case 0:f=query;break; case 1:f=addref;break; case 2:f=release;break;
            case 3:f=capabilities;break; case 4:f=enum_objects;break; case 5:f=get_property;break;
            case 6:f=set_property;break; case 7:f=acquire;break; case 8:f=unacquire;break;
            case 9:f=state;break; case 10:f=data;break; case 11:f=format;break;
            case 13:f=cooperative;break; case 14:f=get_object;break; case 15:f=get_info;break; case 25:f=ok;break; }
        if (!slot(device_vtable,i,0,f,device_args[i])) return 0;
    }
    return win_import_register("DINPUT8.dll!DirectInput8Create",factory,5);
}
