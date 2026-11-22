#ifndef NFS_CAR_PERFORMANCE_ATTRIB_IMPL_H
#define NFS_CAR_PERFORMANCE_ATTRIB_IMPL_H

/* Included at the car collection append seam. Descriptors and new arrays
 * remain live through Attrib's vault teardown. */
typedef struct PerformanceAllocation {
    struct PerformanceAllocation *next;
    uint32_t vault, address;
} PerformanceAllocation;
static PerformanceAllocation *performance_allocations;

static uint32_t performance_alloc(uint32_t vault, uint32_t size) {
    PerformanceAllocation *p=malloc(sizeof *p);
    if (!p) fail("performance allocation bookkeeping");
    *p=(PerformanceAllocation){performance_allocations,vault,gmalloc(size)};
    performance_allocations=p;
    return p->address;
}

static void performance_release(uint32_t vault) {
    for (PerformanceAllocation **next=&performance_allocations; *next;) {
        PerformanceAllocation *p=*next;
        if (p->vault!=vault) { next=&p->next; continue; }
        *next=p->next;
        gfree(p->address);
        free(p);
    }
}

static void performance_link(Cpu *c, Source *owner, uint32_t ld, Addition *a,
                             unsigned link) {
    CarPerformanceLink spec=car_performance_links[link];
    uint32_t old=entry(owner->ld,spec.key);
    if (!old || (M8(old+10)&0x22)!=2) fail("performance donor reference array");
    uint32_t data=M32(old+4);
    unsigned count=M16(data+2);
    if (!count || count>3 || M16(data)<count || M16(data+4)!=12)
        fail("performance donor reference count");
    unsigned indices[3];
    count=car_performance_tiers(&a->performance,link,count,indices);
    uint32_t copy=performance_alloc(owner->vault,8+12*count);
    memcpy(G+copy,G+data,8);
    M16(copy)=M16(copy+2)=count;
    uint32_t stock=0;
    for (unsigned tier=0;tier<count;++tier) {
        uint32_t ref=copy+8+12*tier;
        uint32_t original=data+8+12*indices[tier];
        Source *donor=source(M32(original),M32(original+4));
        if (!donor || M32(original)!=spec.key || donor->vault!=owner->vault)
            fail("performance donor reference ownership");
        uint32_t endpoint=M32(donor->ld+0x1C);
        if (!endpoint) fail("performance donor layout missing");
        if (!tier) stock=endpoint;
        char name[80];
        snprintf(name,sizeof name,"nfsad_%s_perf_%u_%u",a->name,link,tier);
        size_t size=strlen(name)+1;
        uint32_t text=gmalloc((uint32_t)size);
        memcpy(G+text,name,size);
        uint32_t key=call_guest(c,0x004549B0,1,&text);
        gfree(text);
        if (!key || has_addition(donor->vault,spec.key,key))
            fail("performance linked identity collision");
        unsigned char layout[0x84];
        memcpy(layout,G+endpoint,spec.size);
        car_performance_layout(&a->performance,link,tier!=0,G+stock,G+endpoint,layout);
        append_collection_as(c,donor,spec.size,0,a,key,
                             a->render_fit ? spec.fit_kind : 0,layout);
        M32(ref)=spec.key;
        M32(ref+4)=key;
        M32(ref+8)=0;
    }
    uint32_t match=own_fit_entry(owner->ld,ld,spec.key,REFSPEC);
    M32(match+4)=copy;
}

static int apply_performance(Cpu *c,Source *owner,uint32_t ld,uint32_t lay,Addition *a) {
    if (!a->performance.version || !car_performance_enabled()) return 0;
    uint32_t handling=performance_alloc(owner->vault,16);
    car_performance_body(&a->performance,G+lay,G+handling);
    uint32_t attr=own_fit_entry(owner->ld,ld,0xAACBE2E7u,0x3C16EC5Eu);
    M32(attr+4)=handling;
    unsigned links=a->performance.version==1 && a->performance.source!=CAR_PERFORMANCE_PROSTREET
        ? CAR_PERFORMANCE_NOS_LINK : CAR_PERFORMANCE_LINKS;
    for (unsigned i=0;i<links;++i)
        performance_link(c,owner,ld,a,i);
    if (diag_on) diag_event("addcars: source performance name=%s key=%08X mass=%.3f",
                            a->name,a->key,MF32(lay+0x38));
    return 1;
}

void f_00678CE0(Cpu *c) {
    if (!performance_allocations) { f_00678CE0_orig(c); return; }
    uint32_t stats=c->ecx, instance=M32(c->esp+4), collection=M32(instance+4);
    if (collection && (M8(instance+0x10)&1)) collection=M32(collection+0x10);
    uint32_t key=collection ? M32(collection+0x20) : 0, ref=0;
    for (unsigned i=0;i<nadditions;++i) {
        if (additions[i].key!=key || !additions[i].performance.source) continue;
        Cpu saved=*c;
        uint32_t args[]={car_performance_links[CAR_PERFORMANCE_ENGINE_LINK].key,0};
        c->ecx=instance;
        ref=call_guest(c,0x00454810,2,args);
        *c=saved;
        break;
    }
    f_00678CE0_orig(c);
    /* The retail integration loop can overwrite the limiter result when it
     * reaches the final gear. Bound the result by the actual ECU cutoff. */
    uint32_t engine=ref ? M32(ref+8) : 0;
    uint32_t layout=engine ? M32(engine+0x18) : 0;
    if (layout) MF32(stats+4)=car_performance_clamp_top_speed(G+layout,MF32(stats+4));
}

/* Retail rating normalization compares raw acceleration time, top speed and
 * handling in this list. Use the bounds from retail rows while source
 * performance is active, so imported cars cannot shift retail ratings. */
void f_006794E0(Cpu *c) {
    if (!performance_allocations) { f_006794E0_orig(c); return; }
    uint32_t head=M32(c->ecx+4);
    float direction=MF32(c->esp+4);
    uint32_t dest=M32(c->esp+8);
    float bounds[3]={0,0,0};
    unsigned seen=0;
    for (uint32_t row=M32(head);row!=head;row=M32(row)) {
        unsigned j=0;
        while (j<nadditions && additions[j].key!=M32(row+0x2c)) ++j;
        if (j<nadditions) continue;
        for (unsigned i=0;i<3;++i) {
            float value=MF32(row+8+4*i);
            if (!seen || direction*bounds[i]<direction*value) bounds[i]=value;
        }
        ++seen;
    }
    memcpy(G+dest,bounds,sizeof bounds);
    c->esp+=12;
}

void f_00683EB0(Cpu *c) {
    f_00683EB0_orig(c);
    if (!diag_on) return;
    uint32_t head=M32(0x0092C4E8);
    if (!head) return;
    diag_event("addcars: performance bounds speed=%.6f/%.6f handling=%.6f/%.6f time=%.6f/%.6f",
               MF32(0x0092C57C),MF32(0x009261A0),MF32(0x0092C580),MF32(0x009261A4),
               MF32(0x0092C578),MF32(0x0092619C));
    for (uint32_t row=M32(head);row!=head;row=M32(row)) {
        diag_event("addcars: performance row key=%08X speed=%.6f handling=%.6f time=%.6f bars=%.6f/%.6f/%.6f",
                   M32(row+0x2c),MF32(row+0xc),MF32(row+0x10),MF32(row+0x8),
                   MF32(row+0x14),MF32(row+0x18),MF32(row+0x1c));
        for (unsigned i=0;i<nadditions;++i) {
            Addition *a=&additions[i];
            if (M32(row+0x2c)==a->key)
                diag_event("addcars: performance bars name=%s top_speed=%.5f handling=%.5f acceleration=%.5f",
                           a->name,MF32(row+0x14),MF32(row+0x18),MF32(row+0x1c));
        }
    }
}

#endif
