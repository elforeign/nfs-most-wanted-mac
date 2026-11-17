/* Mounted emitters and a bounded projected light, using the retail textured
 * headlight ePoly submission path. Dry diffuse receiver assumption: rho=.18. */
#include "underglow_light.h"
#include "underglow.h"
#include "underglow_ui.h"
#include "underglow_pool.h"
#include "d3d9_internal.h"
#include <math.h>
#include <string.h>
#include <strings.h>
#include <stdlib.h>

FILE *dump_file(void);

static uint32_t ug_arg(const Cpu *c, unsigned i) { return M32(c->esp+4+4*i); }
static uint32_t hash(const char *s) { uint32_t h=~0u; while (*s) h=h*33+(uint8_t)*s++; return h; }
/* Unset follows the per-car native choice. Explicit 0/1 overrides it for
 * diagnostic comparisons, while a missing colour still submits nothing. */
static int light_override(void) {
    static int mode=-2;
    if(mode==-2) { const char *v=getenv("NFS_UNDERGLOW"); mode=v&&!strcmp(v,"0")?0:v&&!strcmp(v,"1")?1:-1; }
    return mode;
}
int underglow_light_enabled(void) { return light_override()!=0; }
static int light_on(uint32_t profile,uint32_t handle) {
    return light_override()==1 || (light_override()<0 && underglow_light_get(profile,handle));
}

/* UpdateCarParts (00755800) expands +90/+A0 from installed part models.
 * The ecar instance at +17AC owns the live layout pointer at +17B4:
 * TireOffsets four XYZR values at +10; TireSkidWidth at +DC.
 * UpdateWheelYRenderOffset (0074D780) applies kit offsets into +110 (four lateral centres).
 * The mixer truck's narrower frame has one decoded inset below. */
static int car_mounts(uint32_t self,UGStrip out[UG_STRIPS]) {
    uint32_t layout=M32(self+0x17b4);
    if(!layout || M16(layout+2)<4 || M16(layout+0xd6)<4) return 0;
    double lo[3],hi[3];
    for(unsigned k=0;k<3;++k) {
        lo[k]=MF32(self+0x90+4*k);hi[k]=MF32(self+0xa0+4*k);
        if(!isfinite(lo[k])||!isfinite(hi[k])||hi[k]<=lo[k]||fabs(lo[k])>20||fabs(hi[k])>20) return 0;
    }
    double rear=-INFINITY,front=INFINITY,side=INFINITY,wheel_z=0;
    double front_cross=INFINITY,rear_cross=-INFINITY;
    double xmin=INFINITY,xmax=-INFINITY,ymin=INFINITY,ymax=-INFINITY,rmax=0;
    for(unsigned i=0;i<4;++i) {
        double x=MF32(layout+0x10+16*i),y=MF32(self+0x110+4*i);
        double z=MF32(layout+0x18+16*i),radius=MF32(layout+0x1c+16*i),width=MF32(layout+0xdc+4*i);
        if(!isfinite(x)||!isfinite(y)||!isfinite(z)||!isfinite(radius)||!isfinite(width)||
           fabs(x)>20||fabs(y)>20||fabs(z)>20||radius<.1||radius>2||width<.05||width>2) return 0;
        wheel_z+=z*.25;
        xmin=fmin(xmin,x);xmax=fmax(xmax,x);ymin=fmin(ymin,y);ymax=fmax(ymax,y);rmax=fmax(rmax,radius);
        if(i<2) front=fmin(front,x-radius-.05); else rear=fmax(rear,x+radius+.05);
        side=fmin(side,fabs(y)-width*.5-.03);
        if(i<2) front_cross=fmin(front_cross,x+radius+.10);
        else rear_cross=fmax(rear_cross,x-radius-.10);
    }
    /* Body overhang may extend half an axle spacing past the tyre envelope.
     * This generous envelope admits wide kits, but not corrupt added-car
     * bounds that would mount emitters metres away from normal wheels. */
    double base=xmax-xmin,track=ymax-ymin;
    if(base<.2 || track<.2 || lo[0]<xmin-rmax-base*.5 || hi[0]>xmax+rmax+base*.5 ||
       lo[1]<ymin-track*.5 || hi[1]>ymax+track*.5 ||
       hi[0]-lo[0]<base*.5 || hi[1]-lo[1]<track*.5 ||
       lo[2]>wheel_z+rmax || hi[2]<wheel_z-rmax ||
       lo[2]<wheel_z-track || hi[2]>wheel_z+base) {
        if(diag_on) diag_event("underglow mounts rejected: inconsistent bounds/wheels self=%08X body=%.3fx%.3f base=%.3f track=%.3f",self,hi[0]-lo[0],hi[1]-lo[1],base,track);
        return 0;
    }
    rear=fmax(rear,lo[0]+.1);front=fmin(front,hi[0]-.1);
    /* CEMTR BODY_A narrows to 0.698085 m inside its tyres; retain 3 cm inset. */
    uint32_t type=M32(self+0x88);
    if(type && !strcasecmp((char *)G+type,"cemtr")) side=fmin(side,.668085);
    /* A global minimum can be an exhaust/bumper vertex below the sill.
     * Use the wheel-centre plane as the underside datum, bounded by this
     * chassis. This remains chassis-local, independent of the road plane. */
    double left=side,right=-side;
    front_cross=fmin(front_cross,hi[0]-.10);
    rear_cross=fmax(rear_cross,lo[0]+.10);
    double z=fmax(lo[2]-.005,fmin(hi[2]-.005,wheel_z-.01));
    double cross=side-.05;
    if(front-rear<.2 || left-right<.2 || cross<.1 || hi[0]-lo[0]<1) return 0;
    UGVec3 a[4]={{rear,right,z},{rear,left,z},{front_cross,-cross,z},{rear_cross,-cross,z}};
    UGVec3 b[4]={{front,right,z},{front,left,z},{front_cross,cross,z},{rear_cross,cross,z}};
    for(unsigned i=0;i<UG_STRIPS;++i)
        out[i]=(UGStrip){a[i],b[i],{0,0,-1},hypot(b[i].x-a[i].x,b[i].y-a[i].y),.01};
    return 1;
}

/* Provenance is recorded at the actual owned-car ride builder, and propagated
 * through the retail frontend's exact 0x310-byte copy. No model-only matching. */
typedef struct { uint32_t ride,profile,handle,keys[2]; uint8_t custom; } UGOwner;
/* RideInfo::GetPart/UpdateCarParts read 76 installed pointers at ride+48.
 * Bounds and wheel inputs also participate: a model/layout may be refreshed
 * in place. Cache rejected geometry too, so diagnostics do not repeat. */
typedef struct {
    uint32_t self,ride,profile,handle,keys[2],custom,type,layout,parts[76];
    uint16_t lengths[2];
    float bounds[6],wheels[16],offsets[4],widths[4];
} UGMountKey;
typedef struct { UGMountKey key; UGStrip mounts[UG_STRIPS]; int valid; } UGMountCache;
static UGMountCache mount_cache[32];
static unsigned mount_next;
static int cached_mounts(uint32_t self,uint32_t ride,UGOwner o,UGStrip out[UG_STRIPS]) {
    UGMountKey key={.self=self,.ride=ride,.profile=o.profile,.handle=o.handle,
        .keys={o.keys[0],o.keys[1]},.custom=o.custom,.type=M32(ride),.layout=M32(self+0x17b4)};
    memcpy(key.parts,G+ride+0x48,sizeof key.parts);
    memcpy(key.bounds,G+self+0x90,12);memcpy(key.bounds+3,G+self+0xa0,12);
    memcpy(key.offsets,G+self+0x110,sizeof key.offsets);
    if(key.layout) {
        key.lengths[0]=M16(key.layout+2);key.lengths[1]=M16(key.layout+0xd6);
        if(key.lengths[0]>=4 && key.lengths[1]>=4) {
            memcpy(key.wheels,G+key.layout+0x10,sizeof key.wheels);
            memcpy(key.widths,G+key.layout+0xdc,sizeof key.widths);
        }
    }
    UGMountCache *entry=NULL;
    for(unsigned i=0;i<32;++i) if(mount_cache[i].key.self==self) { entry=&mount_cache[i];break; }
    if(!entry) entry=&mount_cache[mount_next++%32];
    if(memcmp(&entry->key,&key,sizeof key)) {
        entry->key=key;
        entry->valid=car_mounts(self,entry->mounts);
    }
    if(entry->valid) memcpy(out,entry->mounts,sizeof entry->mounts);
    return entry->valid;
}
static UGOwner owners[32];
static unsigned owner_next;
static UGOwner owner(uint32_t ride) {
    for (unsigned i=0;i<32;++i) if (owners[i].ride==ride) {
        UGOwner o=owners[i]; uint32_t row=o.profile+0x414+o.handle*20;
        if (o.profile && M32(row)==o.handle && (M32(row+12)&6) && M32(row+4)==o.keys[0] && M32(row+8)==o.keys[1] && G[row+16]==o.custom) return o;
    }
    return (UGOwner){0};
}
static void remember(UGOwner o) {
    for(unsigned i=0;i<32;++i) if(owners[i].ride==o.ride) { owners[i]=o;return; }
    /* Unowned ride invalidations must not displace stored provenance. */
    if(!o.profile) return;
    owners[owner_next++%32]=o;
}
/* Diagnostic fixture only (underglow_ui.c, shop): its BeginCarCustomize call copies the built ride into
 * TopOrFullScreenRide without CarViewer::SetRideInfo, so name the car the fixture customizes as its owner. */
void underglow_light_fixture_owner(uint32_t profile,uint32_t row) {
    remember((UGOwner){.ride=0x009BA6B0u,.profile=profile,.handle=M32(row),.keys={M32(row+4),M32(row+8)},.custom=G[row+16]});
}
void f_00739A70_orig(Cpu *c);
void f_00739A70(Cpu *c) {
    uint32_t ride=c->ecx;
    f_00739A70_orig(c);
    if(underglow_light_enabled()) remember((UGOwner){.ride=ride});
}
void f_005910E0_orig(Cpu *c);
void f_005910E0(Cpu *c) {
    uint32_t db=c->ecx,handle=ug_arg(c,0),ride=ug_arg(c,2);
    f_005910E0_orig(c);
    if(!underglow_light_enabled() || !ride || db<0x414) return;
    uint32_t profile=db-0x414;
    UGOwner o={.ride=ride};
    if(handle<200 && M32(db+handle*20)==handle && (M32(db+handle*20+12)&6) && G[db+handle*20+16]<76) {
        uint32_t row=db+handle*20;
        o.profile=profile;o.handle=handle;o.keys[0]=M32(row+4);o.keys[1]=M32(row+8);o.custom=G[row+16];
    }
    remember(o);
    if(diag_on && o.profile) diag_event("underglow owner: built ride=%08X handle=%u",ride,handle);
}
/* CarViewer copies the builder's temporary ride into TopOrFullScreenRide
 * before passing it to FrontEndRenderingCar::ReInit. */
void f_007A9DB0_orig(Cpu *c);
void f_007A9DB0(Cpu *c) {
    if(underglow_light_enabled()) { UGOwner o=owner(ug_arg(c,0));o.ride=0x009BA6B0u;remember(o); }
    f_007A9DB0_orig(c);
}
void f_007609B0_orig(Cpu *c);
void f_007609B0(Cpu *c) {
    uint32_t self=c->ecx,ride=ug_arg(c,0);
    UGOwner o=underglow_light_enabled()?owner(ride):(UGOwner){0};
    f_007609B0_orig(c);
    if(underglow_light_enabled()) { o.ride=self+8;remember(o);
        if(diag_on) diag_event("underglow owner: frontend ride=%08X source=%08X profile=%08X handle=%u",o.ride,ride,o.profile,o.handle); }
}

/* VehicleRenderConn::Load receives the actual customization record. Compare
 * all installed-part indices (139 shorts, 0056F2B0), then attach provenance to
 * the newly allocated ride. A separate simable check admits only local slot 0. */
void f_0075D220_orig(Cpu *c);
void f_0075D220(Cpu *c) {
    uint32_t conn=c->ecx,record=ug_arg(c,3);
    UGOwner o={0};
    if(underglow_light_enabled() && record) {
        uint32_t db=M32(0x0091CF90u),profile=db?M32(db+0x10):0;
        /* QuickGame::CreateCars (006F47A0): the career's car in career mode (database +12C bit 0), otherwise the
         * car picked for the current quick-race type (GetQuickRaceSettings 0056DBE0, selection at +10). */
        uint32_t type=db?M32(db+0x18):~0u,handle=~0u;
        if(profile && (G[db+0x12c]&1)) handle=M32(profile+0xa8);
        else if(profile && type<11) handle=M32(db+0x1c+type*0x18+0x10);
        if(underglow_owned(profile,handle)) {
            uint32_t row=profile+0x414+handle*20;uint8_t custom=G[row+16];
            if(custom<76 && !memcmp(G+record,G+profile+0x414+4000+custom*0x198,139*2))
                o=(UGOwner){.profile=profile,.handle=handle,.keys={M32(row+4),M32(row+8)},.custom=custom};
        }
    }
    f_0075D220_orig(c);
    if(underglow_light_enabled() && (c->eax&255)) {
        o.ride=M32(conn+0x40);if(o.ride)remember(o);
        if(diag_on && o.profile)diag_event("underglow owner: road ride=%08X conn=%08X handle=%u parts=139",o.ride,conn,o.handle);
    }
}
static int primary_car(const Cpu *input,uint32_t self,uint32_t ride) {
    /* First(1) local-player vector (00425200), logical slot at IPlayer+1C
     * (006E99A0), GetSimable virtual +4 (005EB9E0). */
    uint32_t list=M32(0x0092D87Cu),count=M32(0x0092D884u),player=0;
    if(!list || !count || count>8) return 0;
    for(unsigned i=0;i<count;++i) { uint32_t p=M32(list+4*i);if(p && !M32(p+0x1c)) { player=p;break; } }
    if(!player) return 0;
    Cpu c=*input;c.ecx=player;
    uint32_t sim=call_guest(&c,M32(M32(player)+4),0,NULL);
    if(!sim) return 0;
    /* RenderVehicleFlares 00750B00 walks this live connection vector. Its
     * +30 is the physics world ID (007869C0), distinct from a simable handle. */
    uint32_t connections=M32(0x009B37CCu),n=M32(0x009B37D4u),id=0;
    if(!connections || n>256) return 0;
    for(unsigned i=0;i<n;++i) { uint32_t conn=M32(connections+4*i);
        if(conn && M32(conn+0x44)==self && M32(conn+0x40)==ride) { id=M32(conn+0x30);break; } }
    if(!id) return 0;
    /* ISimable vtable +70 is GetWorldID (00686AF0); +68 is the adjacent
     * GetLinearVelocity slot used by the native collision/feedback path. */
    c.ecx=sim;
    return call_guest(&c,M32(M32(sim)+0x70),0,NULL)==id;

}
#define SHOP_STRIP_LIFT .10f   /* metres, outside racing */
void underglow_light_begin(const Cpu *c, UGCarCapture *s) {
    memset(s,0,sizeof *s);
    if(!underglow_light_enabled()) return;
    if(diag_on && (postfx_look_variant("ugoff") || postfx_look_variant("ugrepeat"))) return;
    if(ug_arg(c,8)) return;
    uint32_t self=c->ecx,ride=M32(self+0x84),view=ug_arg(c,0);
    if(!ride || !view || M32(view+4)!=1) return;
    UGOwner o=owner(ride);
    if(!o.profile || !underglow_owned(o.profile,o.handle)) return;
    unsigned preset=underglow_get(o.profile,o.handle);
    if(!preset) return;
    if(!light_on(o.profile,o.handle)) return;
    if(M32(0x00925E90u)==6 && !primary_car(c,self,ride)) return;
    static unsigned preset_logs;
    if(diag_on && preset_logs++<6) diag_event("underglow colour admitted: self=%08X ride=%08X handle=%u preset=%u lod=%d",self,ride,o.handle,preset,(int)M32(self+0x1628));
    if(!cached_mounts(self,ride,o,s->mounts)) return;
    uint32_t basis=ug_arg(c,2),position=ug_arg(c,1);
    if(!basis||!position) return;
    memcpy(s->basis,G+basis,64);memcpy(s->position,G+position,12);
    /* The shop's strips hang 6.5 cm over the pool plane, which leaves a thin line under the body that one
     * side's camera barely sees; outside racing they sit 10 cm higher so the light reaches all four sides. */
    if(M32(0x00925E90u)!=6) s->position[2]+=SHOP_STRIP_LIFT;
    UGStrip strips[UG_STRIPS];
    if(!ug_strips_transform(s->mounts,s->basis,s->position,strips)) return;
    s->self=self;s->view=view;s->view_id=1;s->profile=o.profile;s->handle=o.handle;s->preset=preset;s->valid=1;
}

static uint32_t scratch, textures[2];
static unsigned allocations,strip_draws,pool_draws;
enum { POOL_W=256,POOL_H=160 };
static void com(Cpu *c,uint32_t self,unsigned offset,unsigned n,const uint32_t *args) {
    uint32_t a[10];a[0]=self;for(unsigned i=0;i<n;++i)a[i+1]=args[i];
    call_guest(c,M32(M32(self)+offset),n+1,a);
}
static int texture(Cpu *c,unsigned slot,unsigned w,unsigned h,uint32_t white) {
    uint32_t ti=scratch+0x200+slot*0x100,pi=ti+0x80;
    if(!textures[slot]) {
        uint32_t args[]={w,h,1,0,116,1,scratch+0x10,0};
        com(c,dev.self->guest,0x5c,8,args);
        textures[slot]=M32(scratch+0x10);
        if(!textures[slot]) return 0;
        ++allocations;
        memcpy(G+ti,G+white,0x7c);memcpy(G+pi,G+M32(white),0x20);
        M32(ti)=pi;M32(pi+0x18)=textures[slot];
        M32(ti+0x24)=hash(slot?"UNDERGLOW_POOL":"UNDERGLOW_STRIP");
        G[ti+0x56]=2;G[ti+0x55]=0;G[ti+0x50]=0;
        M16(ti+0x44)=w;M16(ti+0x46)=h;
        Obj *t=obj(textures[slot]);if(t)t->ground_decal=1;
    }
    return 1;
}
/* Modulate RGB without refreshing the pool. Keep alpha opaque: the retail
 * road shader doubles alpha, which clamps and plateaus on an UNORM target. */
static double pulse_factor(unsigned on,double seconds) {
    return on ? 1-.7*(1-cos(M_PI*seconds))/2 : 1;
}
static void quad(Cpu *c,uint32_t view,const UGVec3 p[4],uint32_t ti,const UGVec3 *rectangle) {
    unsigned on=underglow_pulse();
    double seconds=on ? (host_ticks()%2000000000ull)*1e-9 : 0;
    uint32_t level=(uint32_t)lround(255*pulse_factor(on,seconds));
    uint32_t colour=0xFF000000u|level*0x010101u;
    uint32_t poly=scratch+0x500;
    c->ecx=poly;call_guest(c,0x006C0A30u,0,NULL);
    for(unsigned i=0;i<4;++i) {
        MF32(poly+16*i)=p[i].x;MF32(poly+16*i+4)=p[i].y;MF32(poly+16*i+8)=p[i].z;
        M32(poly+0x80+4*i)=colour;
        if(rectangle) {
            MF32(poly+0x40+8*i)=(p[i].x-rectangle[0].x)/(rectangle[2].x-rectangle[0].x);
            MF32(poly+0x44+8*i)=(p[i].y-rectangle[0].y)/(rectangle[2].y-rectangle[0].y);
        }
    }
    c->ecx=view;
    uint32_t args[]={poly,ti,0,0,0};
    call_guest(c,0x006BE750u,5,args);
}
/* Clip a road triangle to the existing texture rectangle. XYZ interpolation
 * keeps the receiver on that exact face, including road camber and kerbs. */
static unsigned pool_clip(const UGVec3 triangle[3],const UGVec3 rectangle[4],UGVec3 out[8]) {
    UGVec3 a[8],b[8];memcpy(a,triangle,3*sizeof *a);unsigned count=3;
    for(unsigned edge=0;edge<4 && count;++edge) {
        unsigned axis=edge/2,n=0;double bound=axis?(edge%2?rectangle[2].y:rectangle[0].y):(edge%2?rectangle[2].x:rectangle[0].x);
        for(unsigned i=0;i<count;++i) {
            UGVec3 p=a[i],q=a[(i+1)%count];
            double pv=axis?p.y:p.x,qv=axis?q.y:q.x;
            int pi=edge%2?pv<=bound:pv>=bound,qi=edge%2?qv<=bound:qv>=bound;
            if(pi)b[n++]=p;
            if(pi!=qi) {
                double t=(bound-pv)/(qv-pv);
                b[n++]=(UGVec3){p.x+t*(q.x-p.x),p.y+t*(q.y-p.y),p.z+t*(q.z-p.z)};
            }
        }
        count=n;memcpy(a,b,n*sizeof *a);
    }
    memcpy(out,a,count*sizeof *out);return count;
}
static unsigned ground_pool(Cpu *c,const UGCarCapture *s,const UGVec3 rectangle[4],uint32_t ti) {
    uint32_t collider=M32(s->self+0x44);if(!collider)return 0;
    /* Same cache/axes as WWorldPos::FindClosestFace (00779170): physics
     * (-render Y, render Z, render X), three 16-byte vertices per triangle. */
    uint32_t first=M32(collider+0x60),last=M32(collider+0x64);
    if(!first || last<first || (last-first)%4 || last-first>16384)return 0;
    unsigned draws=0;FILE *df=diag_on?dump_file():NULL;
    for(uint32_t at=first;at<last;at+=4) {
        uint32_t block=M32(at);if(!block)continue;
        uint32_t begin=M32(block+4),end=M32(block+8);
        if(!begin || end<begin || (end-begin)%48 || end-begin>48*4096)continue;
        for(uint32_t t=begin;t<end;t+=48) {
            UGVec3 triangle[3],polygon[8];int finite=1;
            for(unsigned i=0;i<3;++i) {
                triangle[i]=(UGVec3){MF32(t+16*i+8),-MF32(t+16*i),MF32(t+16*i+4)};
                finite &= isfinite(triangle[i].x)&&isfinite(triangle[i].y)&&isfinite(triangle[i].z);
            }
            if(!finite)continue;
            UGVec3 a={triangle[1].x-triangle[0].x,triangle[1].y-triangle[0].y,triangle[1].z-triangle[0].z};
            UGVec3 b={triangle[2].x-triangle[0].x,triangle[2].y-triangle[0].y,triangle[2].z-triangle[0].z};
            double nx=a.y*b.z-a.z*b.y,ny=a.z*b.x-a.x*b.z,nz=a.x*b.y-a.y*b.x;
            if(nz*nz<=nx*nx+ny*ny || fabs(nz)<1e-8)continue;
            unsigned n=pool_clip(triangle,rectangle,polygon);if(n<3)continue;
            int receiver=1;
            for(unsigned i=0;i<n;++i)receiver &= polygon[i].z<=s->position[2]+.1 && polygon[i].z>=s->position[2]-1;
            if(!receiver)continue;
            if(df) {
                fprintf(df,"underglow ground polygon %u:",n);
                for(unsigned i=0;i<n;++i)fprintf(df," %.9g %.9g %.9g",polygon[i].x,polygon[i].y,polygon[i].z);
                fputc('\n',df);
            }
            for(unsigned i=0;i<n;++i)polygon[i].z+=.002;
            if(n<=4) {
                UGVec3 p[4];for(unsigned i=0;i<4;++i)p[i]=polygon[i<n?i:n-1];
                quad(c,s->view,p,ti,rectangle);++draws;
            } else for(unsigned i=1;i+1<n;++i) {
                UGVec3 p[4]={polygon[0],polygon[i],polygon[i+1],polygon[i+1]};
                quad(c,s->view,p,ti,rectangle);++draws;
            }
        }
    }
    return draws;
}
static const double *preset_duties(unsigned preset) {
    static double values[8][3];
    static unsigned ready;
    if(preset>7) return values[0];
    if(!(ready&(1u<<preset))) { ug_preset_duties(preset,values[preset]);ready|=1u<<preset; }
    return values[preset];
}
static void rgb_lux(const double duties[3],const UGStrip *strips,UGVec3 p,double rgb[3]) {
    double xyz[3]={0};
    for(int c=0;c<3;++c) if(duties[c]) {
        double e=0;
        for(int i=0;i<UG_STRIPS;++i) { double v=0;ug_segment_lux(ug_primaries[c].flux*duties[c],strips[i].a,strips[i].b,strips[i].normal,p,(UGVec3){0,0,1},&v);e+=v; }
        double x=ug_primaries[c].x,y=ug_primaries[c].y;
        xyz[0]+=e*x/y;xyz[1]+=e;xyz[2]+=e*(1-x-y)/y;
    }
    double error;ug_xyz_rec709(xyz,rgb,&error);
}
static void ug_draw(const Cpu *result,const UGCarCapture *s) {
    if(s->valid && diag_on) { static unsigned result_logs; if(result_logs++<3) diag_event("underglow result: eax=%08X",result->eax); }
    if(!s->valid || !(result->eax&255) || !light_on(s->profile,s->handle) || underglow_get(s->profile,s->handle)!=s->preset) return;
    static unsigned end_logs;
    if(diag_on && end_logs++<6) diag_event("underglow prepared: handle=%u preset=%u effect=%08X z=%.6f",s->handle,s->preset,M32(0x00982C80u),s->position[2]);
    Cpu c=*result;
    /* Retail RenderTextureHeadlights uses a horizontal ePoly. The road's own
     * ambient-shadow cache was updated earlier in this same CarRenderInfo::Render.
     * Garage fixture floor is z=0; road height is cached at +1178. */
    float ground=M32(0x00925E90u)==6?MF32(s->self+0x1178):0;
    if(diag_on && M32(0x00925E90u)==6) {
        static unsigned plane_logs;
        if(plane_logs++<4) diag_event("underglow road plane: ground=%.6f car_z=%.6f face=%u",ground,s->position[2],G[s->self+0x78]&1);
    }
    if(!isfinite(ground)||s->position[2]-ground<0||s->position[2]-ground>1) return;
    if(M32(0x00925E90u)==6 && !(G[s->self+0x78]&1)) return;
    uint32_t effect=M32(0x00982C80u);
    if(!effect) return;
    uint32_t a[]={hash("WHITE16X16"),1,0};
    uint32_t white=call_guest(&c,0x00503400u,3,a);
    if(!white||!dev.self) return;
    if(!scratch) { scratch=galloc(0x1000);++allocations; }
    if(!texture(&c,0,1,1,white)||!texture(&c,1,POOL_W,POOL_H,white)) return;
    /* Immutable texture objects; contents are replaced only when the source
     * plane changes. World translation is removed before the optical math. */
    UGStrip strips[UG_STRIPS];float local[3]={0,0,s->position[2]-ground};
    if(!ug_strips_transform(s->mounts,s->basis,local,strips)) return;
    const double *duties=preset_duties(s->preset);double xyz[3],rgb[3];ug_mix_xyz(duties,xyz);double error;ug_xyz_rec709(xyz,rgb,&error);
    Obj *emitter=obj(textures[0]),*pool=obj(textures[1]);
    float *pixel=(float *)(G+emitter->mem[0]);
    for(int k=0;k<3;++k)pixel[k]=rgb[k]/(M_PI*.01*100);pixel[3]=1;emitter->dirty=1;
    double x0=INFINITY,y0=INFINITY,x1=-INFINITY,y1=-INFINITY;
    for(unsigned i=0;i<UG_STRIPS;++i) {
        x0=fmin(x0,fmin(strips[i].a.x,strips[i].b.x));x1=fmax(x1,fmax(strips[i].a.x,strips[i].b.x));
        y0=fmin(y0,fmin(strips[i].a.y,strips[i].b.y));y1=fmax(y1,fmax(strips[i].a.y,strips[i].b.y));
    }
    unsigned strength=underglow_pool_strength();
    /* Bound the 120 degree reference footprint at its one-metre test plane,
     * plus the existing 25 cm fade. Measured retains its exact old grid. */
    double margin=strength ? 1.7320508075688772+.25 : 1;
    x0-=margin;y0-=margin;x1+=margin;y1+=margin;
    static UGStrip prior_mounts[UG_STRIPS];
    static float prior_basis[16],prior_height=-100;static unsigned prior_preset,prior_strength;
    int basis_changed=0;
    for(int i=0;i<12;++i) basis_changed |= fabsf(prior_basis[i]-s->basis[i])>1e-4f;
    if(prior_strength!=strength||prior_preset!=s->preset||fabsf(prior_height-local[2])>.002||basis_changed||memcmp(prior_mounts,s->mounts,sizeof prior_mounts)) {
        float *pixels=(float *)(G+pool->mem[0]);
        double broad_rgb[3];
        if(strength) memcpy(broad_rgb,rgb,sizeof broad_rgb);
        for(int y=0;y<POOL_H;++y)for(int x=0;x<POOL_W;++x) {
            UGVec3 p={x0+(x1-x0)*(x+.5)/POOL_W,y0+(y1-y0)*(y+.5)/POOL_H,0};
            rgb_lux(duties,strips,p,rgb);
            if(strength) {
                double broad=ug_pool_broad(strips,p);
                for(int k=0;k<3;++k) rgb[k]+=broad_rgb[k]*broad;
            }
            double edge=fmin(fmin(fmin(p.x-x0,x1-p.x),fmin(p.y-y0,y1-p.y))/.25,1);
            float *v=pixels+4*(y*POOL_W+x);
            for(int k=0;k<3;++k)v[k]=rgb[k]*.18/(M_PI*100)*fmax(0,edge);v[3]=1;
        }
        pool->dirty=1;prior_strength=strength;prior_preset=s->preset;prior_height=local[2];memcpy(prior_basis,s->basis,64);memcpy(prior_mounts,s->mounts,sizeof prior_mounts);
    }
    /* Keep the original texture rectangle and photometry. Road receivers use
     * their cached collision faces; normal depth still covers walls/body. */
    UGVec3 p[4]={{s->position[0]+x0,s->position[1]+y0,ground+.002},
        {s->position[0]+x1,s->position[1]+y0,ground+.002},
        {s->position[0]+x1,s->position[1]+y1,ground+.002},
        {s->position[0]+x0,s->position[1]+y1,ground+.002}};
    unsigned submitted;
    if(M32(0x00925E90u)==6) submitted=ground_pool(&c,s,p,scratch+0x300);
    else {quad(&c,s->view,p,scratch+0x300,NULL);submitted=1;}
    if(!submitted)return;
    ++pool_draws;
    static int road_logged;
    if(diag_on && M32(0x00925E90u)==6 && !road_logged) {
        road_logged=1;
        uint32_t rain=M32(s->view+0x68);
        diag_event("underglow road draw: handle=%u preset=%u strips=4 emitters=0 pool=1 ground=%.6f car_z=%.6f face=%u wetness=%.6f local_slot=0 parts=139",
            s->handle,s->preset,ground,s->position[2],G[s->self+0x78]&1,rain?MF32(rain+0x36a0):-1);
    }
    static unsigned logs;
    if(diag_on && logs++<3) diag_event("underglow draw: handle=%u preset=%u strips=%u pool=%u allocations=%u ground=%.5f effect=%u",s->handle,s->preset,strip_draws,pool_draws,allocations,ground,M32(effect+4));
}

/* World opaque geometry is queued during CarRenderInfo::Render. Flush the
 * road light with the retail vehicle lights, after that geometry has drawn;
 * otherwise a later opaque road draw would erase the projected pool. */
static UGCarCapture road_capture,menu_capture;
static uint64_t road_frame,menu_frame;
static int road_pending,menu_pending;
void underglow_light_end(const Cpu *result,const UGCarCapture *s) {
    if(!s->valid || !(result->eax&255)) return;
    if(M32(0x00925E90u)==6) {
        road_capture=*s;road_frame=dev.frames;road_pending=1;
    } else {
        menu_capture=*s;menu_frame=dev.frames;menu_pending=1;
    }
}
/* Front end: the car shadow (DrawAmbientShadow 00743EA0) is queued in the
 * render list, which draws at 006DEFBD after RenderFrontEndCars. The empty
 * stub 00667660 is called next at 006DF22A, inside the same 0093DEBC effect
 * scope; drawing there puts the pool over the shadow, as on the road. */
void f_00667660_orig(Cpu *c);
void f_00667660(Cpu *c) {
    f_00667660_orig(c);
    if(!menu_pending) return;
    menu_pending=0;
    if(menu_frame!=dev.frames || !underglow_light_enabled()) return;
    Cpu draw=*c;draw.eax=1;ug_draw(&draw,&menu_capture);
}
void f_00750B00_orig(Cpu *c);
void f_00750B00(Cpu *c) {
    uint32_t view=ug_arg(c,0);
    f_00750B00_orig(c);
    if(!underglow_light_enabled() || !road_pending || road_frame!=dev.frames || road_capture.view!=view) return;
    road_pending=0;
    if(diag_on && (postfx_look_variant("ugoff") || postfx_look_variant("ugrepeat"))) return;
    Cpu draw=*c;draw.eax=1;ug_draw(&draw,&road_capture);
}
