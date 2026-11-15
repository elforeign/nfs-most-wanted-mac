/* Optional admitted source grip. Native steering, yaw speed and torque order
 * remain in DoWheelForces; only loaded-wheel ordinary forces and C51 differ. */
#include "host.h"
#include "car_grip.h"
#include "car_grip_wheel.h"
#include "car_grip_assist.h"
#include "settings.h"
#include <stdlib.h>
#include <math.h>

void f_006AA020_orig(Cpu *c);
void f_0069DA90_orig(Cpu *c);
void f_006A9760_orig(Cpu *c);

typedef struct GripScope {
    struct GripScope *previous;
    uint32_t suspension, state, shadow, native_layout;
    CarGripOwner owner;
    CarGripSelected selected;
    CarGripWheelOverlay overlay;
} GripScope;
static _Thread_local GripScope *scope;

static int grip_on, grip_requested;
/* A nested bypass hides scope while its parent's selection is still live. */
static unsigned grip_depth;
static void grip_apply(int on) {
    grip_requested=on!=0;
    if (!grip_depth) grip_on=grip_requested;
}
static const char *const grip_values[]={"off","on"};
static Setting grip_setting={
    .id="cars.added_grip", .category=SET_CARS, .order=56,
    .label="Added cars' own grip",
    .help="Added cars use the grip and steering help they were built with. Off keeps the borrowed grip of the car they replace. Takes effect from the next race or car change.",
    .type=SET_TOGGLE, .values=grip_values, .def=0,
    .env="NFS_ADDCAR_GRIP", .apply=grip_apply
};
__attribute__((constructor)) static void grip_register(void) { settings_add(&grip_setting); }

/* After settings_init exports saved choices; the environment still wins. */
void car_grip_init(void) {
    const char *v=getenv("NFS_ADDCAR_GRIP");
    grip_apply(v && !strcmp(v,"on"));
}
static void grip_leave(void) {
    if (!--grip_depth) grip_on=grip_requested;
}
static uint32_t bits(float f) { uint32_t u; memcpy(&u,&f,4); return u; }
static uint32_t invoke(Cpu *c,uint32_t self,uint32_t fn,int n,const uint32_t *a) {
    c->ecx=self; return call_guest(c,fn,n,a);
}
static uint32_t attribute(Cpu *c,uint32_t instance,uint32_t key,unsigned index) {
    uint32_t a[2]={key,index}; return invoke(c,instance,0x00454810,2,a);
}

/* ISimable is PhysicsObject's primary interface: GetAttributes 00686A40
 * returns +38, GetPlayer 00686A20 returns +58. Local list contains IPlayer
 * (+4C); its GetSimable thunk 006F8FB0 reads -8. No virtual call or guest
 * stack write is needed to reject stock, traffic or a remote player. */
static int identify(uint32_t suspension,CarGripOwner *owner,uint32_t *pv) {
    uint32_t sim=M32(suspension+0x34);
    if (!sim || !M32(sim) || M32(M32(sim)+0x44)!=0x00686A40) return 0;
    uint32_t player=M32(sim+0x58), list=M32(0x0092D87C), count=M32(0x0092D884);
    if (!player || !list || count>16) return 0;
    unsigned i;
    for (i=0;i<count;++i) if (M32(list+4*i)==player && M32(player+0x1c)==0) break;
    if (i==count || M32(player-8)!=sim) return 0;
    *pv=sim+0x38;
    /* Upgrades can install a dynamic collection; walk to the vault identity. */
    uint32_t coll=M32(*pv+4);
    for (unsigned depth=0;coll && depth<32;++depth,coll=M32(coll+0x10)) {
        uint32_t vault=M32(coll+0x24);
        if (vault) return car85_grip_owner(M32(coll+0x20),vault,sim,M32(player-8),owner);
    }
    return 0;
}

/* Resolve the tyre row by its part key, rather than assuming an upgrade enum.
 * put_maps entries are seven words; +8 part, +14 Junkman scale-table key.
 * The installed mask is distinct from GetJunkman's availability predicate. */
static int select_live(Cpu *c,uint32_t pv,GripScope *s) {
    uint32_t map=0;
    for (unsigned i=0;i<7;++i) {
        uint32_t p=0x008F87A0+28*i;
        if (M32(p+8)==0xBD38D1CA) { map=p; break; }
    }
    if (!map || M32(map)>=7) return 0;
    Cpu q=*c;
    uint32_t a[2]={pv,M32(map)};
    CarGripLive live={0};
    live.level=(int)invoke(&q,0,0x00672D30,2,a);
    live.max_level=(int)invoke(&q,0,0x00672E80,2,a);
    uint32_t mask=attribute(&q,pv,0xCDC136E8,0);
    live.junkman=mask && (M32(mask)&(1u<<M32(map)))!=0;
    CarGripScale scales[CAR_GRIP_FIELDS];
    if (live.junkman) {
        uint32_t ref=attribute(&q,pv,0x171737E9,0);
        if (!ref) return 0;
        uint32_t instance=gmalloc(20);
        uint32_t ctor[3]={ref,0,0};
        invoke(&q,instance,0x00456CB0,3,ctor);
        live.scales=scales;
        unsigned i;
        for (i=0;i<=CAR_GRIP_FIELDS;++i) {
            uint32_t row=attribute(&q,instance,M32(map+20),i);
            if (!row) break;
            if (i<CAR_GRIP_FIELDS) scales[i]=(CarGripScale){M32(row+4),MF32(row+8)};
        }
        invoke(&q,instance,0x0045A430,0,NULL);
        gfree(instance);
        if (i>CAR_GRIP_FIELDS) return 0;
        live.scale_count=i;
    }
    return car85_grip_select(&s->owner,&live,&s->selected);
}

static int make_overlay(GripScope *s) {
    const CarGripField *d=car_grip_selected_field(&s->selected,0xAAFC01B3);
    const CarGripField *t=car_grip_selected_field(&s->selected,0xEC096166);
    const CarGripField *g=car_grip_selected_field(&s->selected,0xF177BE1B);
    const CarGripField *y=car_grip_selected_field(&s->selected,0x64C43C4B);
    if (!d || !t || !g || !y || d->count!=2 || t->count!=2 || g->count!=2 || !y->count || y->count>4) return 0;
    /* Native angle correction divides by this scale. Refuse zero and scales
     * whose reciprocal cannot be represented before publishing any shadow. */
    for (unsigned i=0;i<2;++i)
        if (!(g->values[i]>0) || !isfinite(g->values[i]) || !isfinite(1.0f/g->values[i])) return 0;
    memcpy(s->overlay.dynamic_grip,d->values,8);
    memcpy(s->overlay.static_grip,t->values,8);
    memcpy(s->overlay.grip_scale,g->values,8);
    memcpy(s->overlay.yaw_control,y->values,4*y->count);
    s->overlay.yaw_count=y->count;
    CarGripWheelTyre native={0}, result;
    return !car_grip_wheel_overlay(&native,&s->overlay,&result);
}

static __attribute__((noinline)) void grip_tick(Cpu *c) {
    ++grip_depth;
    GripScope s={.previous=scope,.suspension=c->ecx,.state=arg(c,0)};
    uint32_t pv=0;
    /* Clear even a bypassed nested scope: it must never inherit its parent. */
    scope=NULL;
    if (!identify(s.suspension,&s.owner,&pv) ||
        !select_live(c,pv,&s) || !make_overlay(&s)) {
        f_006AA020_orig(c); scope=s.previous; grip_leave(); return;
    }
    s.native_layout=M32(s.suspension+0xa0);
    if (!s.native_layout) { f_006AA020_orig(c); scope=s.previous; grip_leave(); return; }
    s.shadow=gmalloc(0x50);
    memcpy(G+s.shadow,G+s.native_layout,0x50);
    M16(s.shadow+2)=(uint16_t)s.overlay.yaw_count;
    memcpy(G+s.shadow+8,s.overlay.yaw_control,16);
    memcpy(G+s.shadow+0x18,s.overlay.grip_scale,8);
    memcpy(G+s.shadow+0x20,s.overlay.dynamic_grip,8);
    memcpy(G+s.shadow+0x38,s.overlay.static_grip,8);
    /* All tires refer to the suspension's own tires instance at +98. */
    M32(s.suspension+0xa0)=s.shadow;
    scope=&s;
    f_006AA020_orig(c);
    if (M32(s.suspension+0xa0)==s.shadow)
        M32(s.suspension+0xa0)=s.native_layout;
    scope=s.previous;
    gfree(s.shadow);
    grip_leave();
}

/* Keep the disabled tick a tail call: no scope frame, TLS or guest access. */
void f_006AA020(Cpu *c) {
    if (!grip_on) { f_006AA020_orig(c); return; }
    grip_tick(c);
}

static int current(GripScope *s) {
    if (!s || !grip_on || M32(s->suspension+0xa0)!=s->shadow) return 0;
    CarGripOwner now; uint32_t pv;
    return identify(s->suspension,&now,&pv) && now.key==s->owner.key &&
        now.vault==s->owner.vault && now.simable==s->owner.simable &&
        now.generation==s->owner.generation;
}

static __attribute__((noinline)) void grip_drift(Cpu *c) {
    GripScope *s=scope;
    uint32_t self=c->ecx,state=arg(c,0);
    int admitted=s && self==s->suspension && state==s->state && current(s);
    f_006A9760_orig(c);
    if (!admitted || !current(s)) return;
    const CarGripField *f=car_grip_selected_field(&s->selected,CAR_GRIP_DRIFT_YAW_CONTROL);
    float q,torque;
    CarGripAssistMode mode={0}; /* Ordinary road: drift-only fields stay unavailable. */
    if (!f || car_grip_assist_yaw_control(f->values,f->count,MF32(state+0x9c),&q) ||
        car_grip_assist_yaw_torque(&mode,MF32(state+0xbc),(int16_t)M16(state+0xcc),
            MF32(state+0x4c),MF32(state+0x94),MF32(state+0xa4),MF32(self+0x108),q,1,&torque) || !torque) return;
    uint32_t body=M32(self+0xe8);
    if (!body) return;
    Cpu copy=*c;
    uint32_t vector=gmalloc(12);
    for (unsigned i=0;i<3;++i) MF32(vector+4*i)=torque*MF32(state+0x10+4*i);
    invoke(&copy,body,M32(M32(body)+0x94),1,&vector);
    gfree(vector);
}

void f_006A9760(Cpu *c) {
    if (!grip_on) { f_006A9760_orig(c); return; }
    grip_drift(c);
}

/* Shared native preparation (0069DA90..0069DDE1): torque, angle and lock.
 * All changes are staged on an owned guest Tire until the source solver accepts.
 * Curve sampling, brake lock and sign history use their unchanged MW routines. */
static int loaded(Cpu *c,GripScope *s,uint32_t tire) {
    float lateral=argf(c,0),forward=argf(c,1),speed=argf(c,2),load=argf(c,3),dt=argf(c,4);
    if (!(dt>0) || !isfinite(dt) || !(MF32(tire+0xc4)>0)) return 0;
    uint32_t work=gmalloc(0x140);
    memcpy(G+work,G+tire,0x140);
    Cpu q=*c;
    float radius=MF32(work+0xc4),omega=MF32(work+0xd0);
    if (MF32(work+0xd4)<=0 && !M8(work+0x134)) omega=forward/radius;
    MF32(work+0xd0)=omega;
    float road=(forward-MF32(work+0xfc))/dt;
    MF32(work+0xfc)=forward; MF32(work+0xd4)=fmaxf(0,load); MF32(work+0x130)=lateral;
    uint32_t brake=M32(M32(work+0x104)+8),axle=M32(work+0x10c);
    if (axle>1) { gfree(work); return 0; }
    float b=MF32(brake+8+axle*4)*1.3558f*MF32(0x008F8A90)*MF32(work+0xc8);
    float e=MF32(brake+0x10)*1.3558f*MF32(0x008F8A94)*MF32(work+0xcc);
    int locked=M8(work+0x134)!=0;
    if (!locked) {
        if (fabsf(forward)>=1) MF32(work+0xe4)+=(omega>0?-b:b)+(omega>0?-e:e);
        else {
            MF32(work+0xe0)*=1-MF32(work+0xcc);
            MF32(work+0xe4)-=forward*load/radius*(MF32(work+0xc8)+MF32(work+0xcc));
        }
    }
    uint32_t angles[2]={bits(lateral),bits(fabsf(forward))};
    invoke(&q,0,0x005CB5D0,2,angles);
    float angle=(float)q.st[q.top]; x87_pop(&q);
    MF32(work+0x118)=angle;
    float slip=omega*radius-forward;
    MF32(work+0xf0)=slip;
    CarGripWheelInput in={0}; in.axle=axle;
    in.sliding=(CarGripForceSliding){.load=fmaxf(0,load),.lateral_velocity=lateral,
        .forward_velocity=forward,.wheel_slip=slip,.speed=speed,.grip_multiplier=MF32(work+0xec),
        .speed_sensitive=M32(work+0xf8)>1,.forward_rotation=omega>=0,.locked=locked};
    float gain=1;
    if (in.sliding.speed_sensitive && in.sliding.forward_rotation && !locked)
        gain=(float)(0.85f+0.14999998f*fmin(1,fmax(0,((double)speed-13.4109f)*0.111849315f)));
    float norm=hypotf(slip,lateral);
    if (norm>1e-6f && (lateral!=0 || forward!=0)) {
        float dynamic=s->overlay.dynamic_grip[axle]*MF32(work+0xec)*gain;
        float limit=fmaxf(0,load)/hypotf(forward,lateral)*dynamic*fabsf(forward);
        uint32_t a=bits(limit); invoke(&q,work,0x0068E220,1,&a);
    }
    locked=M8(work+0x134)!=0;
    /* ComputeLateralForce includes GRIP_SCALE. Temporarily sample at unity,
     * then let the adapter apply the admitted scale exactly once. */
    float scale=MF32(s->shadow+0x18+4*axle);
    MF32(s->shadow+0x18+4*axle)=1;
    uint32_t curve[2]={bits(fmaxf(0,load)),bits(fabsf(angle))};
    invoke(&q,work,0x0068E090,2,curve);
    in.curve_force_per_grip=(float)q.st[q.top]; x87_pop(&q);
    MF32(s->shadow+0x18+4*axle)=scale;
    float torque=MF32(work+0xe4)+MF32(work+0xe0),inertia=MF32(0x008F8AB0);
    in.demand=(CarGripForceDemand){.prior_saturation=MF32(work+0x110),.lateral_velocity=lateral,
        .lateral_gain=MF32(work+0xe8),.wheel_torque=torque,.radius=radius,
        .angular_acceleration=MF32(work+0x108),.road_acceleration=road,.inertia=inertia,.locked=locked};
    uint32_t surface=M32(work+0xa4),state=s->state;
    in.finish=(CarGripForceOrdinary){.load=fmaxf(0,load),.grip_multiplier=MF32(work+0xec),
        .speed_gain=gain,.static_gain=MF32(work+0x12c),.speed=speed,
        .throttle=MF32(state+0x4c),.brake=MF32(state+0x5c),.ebrake=MF32(state+0x7c),
        .gear=(int16_t)M16(state+0xcc),.locked=locked,.slip=slip,.slip_threshold=MF32(work+0x124),
        .surface_lateral=MF32(surface+0xf0),.surface_longitudinal=MF32(surface+0xec),
        .lateral_velocity=lateral,.forward_velocity=forward,.slip_angle_turns=angle,
        .angle_correction=MF32(work+0x13c),.wheel_torque=torque,.radius=radius,
        .omega=MF32(work+0xd0),.angular_acceleration=MF32(work+0x108),.road_acceleration=road,
        .previous_feedback=MF32(work+0xf4),.rolling_friction=MF32(0x008F8AAC),
        .surface_rolling=MF32(surface+0xe4),.inertia=inertia,.dt=dt};
    CarGripForceOrdinaryResult out;
    if (car_grip_wheel_ordinary(&s->overlay,&in,&out)) { gfree(work); return 0; }
    MF32(work+0xd8)=out.force.lateral; MF32(work+0xdc)=out.force.longitudinal;
    MF32(work+0x110)=out.saturation; MF32(work+0xf0)=out.stored_slip;
    MF32(work+0xf4)=out.feedback; MF32(work+0x108)=out.angular_acceleration;
    MF32(work+0xd0)+=dt*out.angular_acceleration;
    invoke(&q,work,0x0068E2E0,0,NULL);
    memcpy(G+tire,G+work,0x140);
    gfree(work);
    x87_push(c,out.force.lateral); c->esp+=24;
    return 1;
}

/* A recursive tick can save its parent's shadow as the previous layout.
 * Unwrap only shadows owned by this suspension, leaving exit restoration local. */
static uint32_t donor_layout(const GripScope *s) {
    uint32_t layout=s->native_layout;
    for (const GripScope *p=s->previous;p;p=p->previous)
        if (p->suspension==s->suspension && layout==p->shadow) layout=p->native_layout;
    return layout;
}

static __attribute__((noinline)) void grip_loaded_tick(Cpu *c) {
    GripScope *s=scope;
    int active=s && current(s);
    if (active) {
        for (unsigned i=0;i<4;++i)
            if (c->ecx==M32(s->suspension+0x1b4+4*i) &&
                M32(c->ecx+0x100)==s->suspension+0x98 && loaded(c,s,c->ecx)) return;
    }
    /* Every refusal uses donor coefficients. A replacement layout belongs
     * to the new object and must remain untouched. */
    int restore=s && M32(s->suspension+0xa0)==s->shadow;
    uint32_t donor=restore?donor_layout(s):0;
    if (restore) M32(s->suspension+0xa0)=donor;
    f_0069DA90_orig(c);
    if (restore && M32(s->suspension+0xa0)==donor)
        M32(s->suspension+0xa0)=s->shadow;
}

void f_0069DA90(Cpu *c) {
    if (!grip_on) { f_0069DA90_orig(c); return; }
    grip_loaded_tick(c);
}
