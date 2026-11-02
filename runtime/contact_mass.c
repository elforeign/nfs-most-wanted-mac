// Contact-only physical properties. Donor body/integrator data is never written.
#include "host.h"
#include <math.h>
#include <stdlib.h>
#if !defined(NFS_PUBLIC) || !NFS_PUBLIC
#include "car_physics.h"

static uint32_t body_key(Cpu *c,uint32_t body) {
    uint32_t vt=M32(body+0x60),owner=M32(body+0x34);
    if (!vt || M32(vt+0x2c)!=0x00676E10u || !owner) return 0;
    Cpu q=*c; q.ecx=owner;
    uint32_t inst=call_guest(&q,M32(M32(owner)+0x44),0,NULL);
    return inst && M32(inst+4)?M32(M32(inst+4)+0x20):0;
}

// IEntity at body+0x60 has GetMass 00676E10. Other entity implementations,
// including world entities, must not be interpreted as vehicle bodies.
static const CarPhysics *body_physics(Cpu *c,uint32_t body) {
    if (!car_physics_enabled() || !body) return NULL;
    return car85_contact_physics(body_key(c,body));
}
// Match 00698415-00698452's multiply/add order and x87 precision control.
static double contact_dot(const Cpu *c,uint32_t p,uint32_t n) {
    double z=x87_round(c,(double)MF32(p+0x28)*MF32(n+8));
    double y=x87_round(c,(double)MF32(p+0x24)*MF32(n+4));
    double x=x87_round(c,(double)MF32(p+0x20)*MF32(n));
    return x87_round(c,x87_round(c,z+y)+x);
}
static float body_mass(uint32_t body) { return MF32(M32(M32(body+0x78))+0x2c); }
static float contact_mass(Cpu *c,uint32_t body) {
    const CarPhysics *p=body_physics(c,body);
    float donor=body_mass(body);
    if (!p || !(donor>0)) return donor;
    float override=MF32(body+0x16c);
    return override>1e-6f ? override*(p->mass/donor) : p->mass;
}
static void source_moment(Cpu *c,uint32_t body,uint32_t moment) {
    const CarPhysics *p=body_physics(c,body);
    if (!p) return;
    // 006E8F50: sret output, mass, half dimensions x/y/z, tensor factors.
    // body+E0 is RBComplexParams' same half-dimension vector used at creation.
    Cpu q=*c; uint32_t scratch=(q.esp-128)&~15u; q.esp=scratch;
    memcpy(G+scratch+32,p->tensor,16);
    uint32_t args[6]={scratch+48,0,M32(body+0xe0),M32(body+0xe4),M32(body+0xe8),scratch+32};
    memcpy(args+1,&p->mass,4);
    call_guest(&q,0x006E8F50u,6,args);
    memcpy(G+moment+0x80,G+scratch+48,12);
    MF32(moment+0xd0)=p->mass; MF32(moment+0xd4)=1.0f/p->mass;
    if (diag_on) {
        static unsigned reports;
        if (reports++<16) diag_event("contact_mass: donor=%g contact=%g tensor=(%g,%g,%g)",
            body_mass(body),p->mass,MF32(moment+0x80),MF32(moment+0x84),MF32(moment+0x88));
    }
}
#endif

void f_00487160_orig(Cpu *c);
void f_00487160(Cpu *c) {
#if !defined(NFS_PUBLIC) || !NFS_PUBLIC
    uint32_t entity=arg(c,0),moment=c->ecx;
#endif
    f_00487160_orig(c);
#if !defined(NFS_PUBLIC) || !NFS_PUBLIC
    if (entity>=0x60) source_moment(c,entity-0x60,moment);
#endif
}
void f_0069ABF0_orig(Cpu *c);
void f_0069ABF0(Cpu *c) {
#if !defined(NFS_PUBLIC) || !NFS_PUBLIC
    uint32_t body=c->ecx,moment=arg(c,2);
    const CarPhysics *p=body_physics(c,body);
    float ratio=p ? p->mass/body_mass(body) : 1.0f;
#endif
    f_0069ABF0_orig(c);
#if !defined(NFS_PUBLIC) || !NFS_PUBLIC
    // The native positive absolute override scales the tensor then sets mass.
    // Compose its donor-relative multiplier without changing its stored value,
    // zero-reset behavior, invulnerability, reactions or CG adjustments.
    if (p && MF32(body+0x16c)>1e-6f && !M8(moment+0xc8)) {
        MF32(moment+0xd0)*=ratio; MF32(moment+0xd4)=1.0f/MF32(moment+0xd0);
        for (unsigned i=0;i<3;++i) MF32(moment+0x80+4*i)*=ratio;
    }
#endif
}
void f_00698360_orig(Cpu *c);
void f_00698360(Cpu *c) {
#if !defined(NFS_PUBLIC) || !NFS_PUBLIC
    uint32_t a=arg(c,0),b=arg(c,2),normal=arg(c,4),point=arg(c,5);
    float depth=argf(c,6);
    if (car_physics_enabled() && depth>1e-6f && !(arg(c,1)&255) && !(arg(c,3)&255) &&
        (body_physics(c,a) || body_physics(c,b))) {
        uint32_t pa=M32(M32(a+0x78)),pb=M32(M32(b+0x78));
        float negative_a=(float)-contact_dot(c,pa,normal);
        float relative=(float)x87_round(c,contact_dot(c,pb,normal)+negative_a);
        // Only replace 00698568-0069856E's mass-weighted fallback. The
        // velocity-based branch and immobile paths remain translated retail.
        if (isnan(relative) || fabsf(relative)<=1e-6f || M32(a+0x108)!=M32(b+0x108) || M32(a+0x108)!=2) {
            float ma=contact_mass(c,a),mb=contact_mass(c,b);
            double fraction=x87_round(c,ma/x87_round(c,(double)ma+mb));
            double da=x87_round(c,x87_round(c,1.0-fraction)*depth);
            double db=x87_round(c,x87_round(c,fraction*depth)*-1.0);
            for (unsigned i=0;i<3;++i) {
                float n=MF32(normal+4*i);
                float va=(float)x87_round(c,da*n),vb=(float)x87_round(c,db*n);
                MF32(pa+0x10+4*i)+=va; MF32(pb+0x10+4*i)+=vb;
                MF32(point+4*i)+=(arg(c,7)&255)?va:vb;
            }
            c->eax=(c->eax&~255u)|1u; c->esp+=4; return;
        }
    }
#endif
    f_00698360_orig(c);
}
void f_006A85B0_orig(Cpu *c);
void f_006A85B0(Cpu *c) {
#if !defined(NFS_PUBLIC) || !NFS_PUBLIC
    // 006A8615/21 obtains IRigidBody from this[-0x20], then ordinary GetMass.
    // 006A862B and 006A8654 multiply it by Info+5C/+6C. Pass a private Info
    // copy with just those scalars corrected; never change a shared event.
    uint32_t owner=M32(c->ecx-0x20);
    if (car_physics_enabled() && owner) {
        Cpu q=*c; q.ecx=owner;
        uint32_t rb=call_guest(&q,M32(M32(owner)+0x54),0,NULL);
        if (rb>=0x48) {
            uint32_t body=rb-0x48;
            const CarPhysics *p=body_physics(c,body);
            if (p && body_mass(body)>0) {
                uint32_t info=arg(c,0),original_sp=c->esp;
                uint32_t copy=(original_sp-0xa0)&~15u;
                memcpy(G+copy,G+info,0x80);
                float ratio=p->mass/body_mass(body);
                MF32(copy+0x5c)*=ratio; MF32(copy+0x6c)*=ratio;
                // Run below the copy: the original's local stack cannot alias it.
                c->esp=copy-16; M32(c->esp)=M32(original_sp); M32(c->esp+4)=copy;
                f_006A85B0_orig(c); c->esp=original_sp+8; return;
            }
        }
    }
#endif
    f_006A85B0_orig(c);
}

#if !defined(NFS_PUBLIC) || !NFS_PUBLIC
// A diagnostic fixture waits for a real 300C/cop pair, then gives its contact
// Moments a central 10 m/s approach against a stationary cop. Native solving,
// modifiers, event generation and velocity writeback still run normally.
static _Thread_local uint32_t push_body_a,push_body_b;
static uint32_t push_player;
static int push_completed,push_placed;
static void push_reset(void) {
    push_body_a=push_body_b=push_player=0;
    push_completed=push_placed=0;
}
static int push_enabled(void) {
    static int configured,enabled;
    if (!diag_on) return 0;
    if (!configured) { enabled=getenv("NFS_TEST_CONTACT_PUSH")!=NULL; configured=1; }
    return enabled;
}

static int push_cop(uint32_t key) {
    static const char *const names[]={"copmidsize","copghost","copgto","copgtoghost",
        "copsport","copsportghost","copcross","copsuv","copsuvl","copsuvpatrol"};
    for (unsigned i=0;i<sizeof names/sizeof names[0];++i)
        if (key==car_physics_key(names[i])) return 1;
    return 0;
}

#endif
// World unload and car replacement both destroy their rigid bodies through
// 006B7720, before the integrator storage at body+78 is released. Any retired
// body invalidates the diagnostic pair; the next live pair starts a new fixture.
void f_006B7720_orig(Cpu *c);
void f_006B7720(Cpu *c) {
#if !defined(NFS_PUBLIC) || !NFS_PUBLIC
    push_reset();
#endif
    f_006B7720_orig(c);
}
void f_00698650_orig(Cpu *c);
void f_00698650(Cpu *c) {
#if !defined(NFS_PUBLIC) || !NFS_PUBLIC
    if (!push_enabled()) { f_00698650_orig(c); return; }
    uint32_t saved_a=push_body_a,saved_b=push_body_b;
    push_body_a=arg(c,0); push_body_b=arg(c,1);
#endif
    f_00698650_orig(c);
#if !defined(NFS_PUBLIC) || !NFS_PUBLIC
    push_body_a=saved_a; push_body_b=saved_b;
#endif
}
void f_00488F70_orig(Cpu *c);
void f_00488F70(Cpu *c) {
#if !defined(NFS_PUBLIC) || !NFS_PUBLIC
    if (!push_enabled()) { f_00488F70_orig(c); return; }
    uint32_t a=c->ecx,b=arg(c,0),plane=arg(c,1);
    int fixture=0,source_a=0;
    if (!push_completed && push_body_a && push_body_b) {
        uint32_t ka=body_key(c,push_body_a),kb=body_key(c,push_body_b);
        uint32_t car=car_physics_key("300c");
        source_a=ka==car;
        uint32_t other=source_a?kb:ka;
        int cop=push_cop(other);
        if (ka==car || kb==car) {
            static unsigned candidates;
            if (candidates++<8) diag_event("contact_push: candidate keys=(%08X,%08X) masses=(%g,%g) immobile=(%u,%u) cop=%d",
                ka,kb,MF32(a+0xd0),MF32(b+0xd0),M8(a+0xc8),M8(b+0xc8),cop);
        }
        fixture=(ka==car || kb==car) && cop && !M8(a+0xc8) && !M8(b+0xc8);
        if (fixture) {
            push_completed=1;
            for (unsigned i=0;i<3;++i) {
                MF32(plane+4*i)=0; MF32(plane+0xc+4*i)=i==0?1:0;
                MF32(a+0x8c+4*i)=MF32(b+0x8c+4*i)=0;
                MF32(a+0xb0+4*i)=MF32(b+0xb0+4*i)=0;
                MF32(a+0xa4+4*i)=MF32(b+0xa4+4*i)=0;
                MF32(a+0x98+4*i)=source_a && i==0?-10:0;
                MF32(b+0x98+4*i)=!source_a && i==0?10:0;
            }
            diag_event("contact_push: input source_a=%d masses=(%g,%g) approach=10 stationary_cop=1 iterations=%u inverse=(%g,%g) reactions=(%g,%g)",source_a,MF32(a+0xd0),MF32(b+0xd0),arg(c,2),MF32(a+0xd4),MF32(b+0xd4),MF32(a+0x10c),MF32(b+0x10c));
        }
    }
#endif
    f_00488F70_orig(c);
#if !defined(NFS_PUBLIC) || !NFS_PUBLIC
    if (fixture) diag_event("contact_push: output velocities=(%g,%g) impulse=(%g,%g) angular=(%g,%g,%g;%g,%g,%g)",
        MF32(a+0x98),MF32(b+0x98),MF32(a+0xf0),MF32(b+0xf0),
        MF32(a+0xa4),MF32(a+0xa8),MF32(a+0xac),MF32(b+0xa4),MF32(b+0xa8),MF32(b+0xac));
#endif
}


void f_006978F0_orig(Cpu *c);
void f_006978F0(Cpu *c) {
#if !defined(NFS_PUBLIC) || !NFS_PUBLIC
    if (push_enabled() && !push_completed && !push_placed) {
        uint32_t body=c->ecx,key=body_key(c,body);
        if (key==car_physics_key("300c")) push_player=body;
        else if (push_player && push_cop(key)) {
            uint32_t player=M32(M32(push_player+0x78)),cop=M32(M32(body+0x78));
            float v[3]={MF32(player+0x20),MF32(player+0x24),MF32(player+0x28)};
            float speed=sqrtf(v[0]*v[0]+v[1]*v[1]+v[2]*v[2]);
            if (speed>10) {
                // Scratch fixture setup changes only the patrol car. Player
                // driving state and every normal integration call stay native.
                for (unsigned i=0;i<3;++i) {
                    MF32(cop+0x10+4*i)=MF32(player+0x10+4*i)+6.0f*v[i]/speed;
                    MF32(cop+0x20+4*i)=MF32(cop+0x30+4*i)=0;
                }
                push_placed=1;
                diag_event("contact_push: placed stationary patrol key=%08X donor=%g six_metres_ahead=1",key,body_mass(body));
            }
        }
    }
#endif
    f_006978F0_orig(c);
}
