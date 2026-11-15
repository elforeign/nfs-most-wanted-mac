// Optional section-owned flares. All links and drawing belong to the game.
#include "daynight_lamps.h"
#include <stdlib.h>
#include <string.h>

typedef struct { uint32_t section, offset, size; } LampPack;
#if !defined(NFS_PUBLIC) && __has_include("daynight_lamps_private.inc")
#include "daynight_lamps_private.inc"
#else
static const unsigned char lamp_bytes[] = {0};
static const LampPack lamp_packs[] = {{0,0,0}};
#define LAMP_PACK_COUNT 0
#endif

// AllocateUserInfo 007299e0 shares the scenery slot, adding one reference.
// LoaderLights 00501a10 owns the list links and +8 user-info binding; its
// UnloaderLights 00501bf0 clears them before the scenery slot is released.
static struct { uint32_t guest, owner; } loaded[sizeof lamp_packs / sizeof *lamp_packs];
static uint32_t scenery_load, scenery_unload, load_wrapper, unload_wrapper;
static unsigned held, loads;
static int enabled = -1;

static int active(void) {
    if (enabled < 0) {
        const char *e = getenv("NFS_DAYNIGHT_LAMPS");
        enabled = LAMP_PACK_COUNT && e && !strcmp(e,"1");
    }
    return enabled;
}

static uint32_t scenery_section(uint32_t chunk) {
    if (M32(chunk) != 0x80034100u) return UINT32_MAX;
    uint32_t size = M32(chunk+4), end = chunk+8+size;
    if (size > 0x1000000u || end < chunk) return UINT32_MAX;
    for (uint32_t p=chunk+8; p+8<=end;) {
        uint32_t n = M32(p+4);
        if (n > end-p-8) break;
        uint32_t next = p+8+n, h = (p+23)&~15u;
        if (M32(p)==0x34101u && h+0x30<=next) return M32(h+0xc);
        p = next;
    }
    return UINT32_MAX;
}

static void pack_load(Cpu *c, uint32_t chunk, uint32_t section) {
    for (unsigned i=0; i<LAMP_PACK_COUNT; ++i) {
        const LampPack *p = &lamp_packs[i];
        if (p->section != section || loaded[i].guest) continue;
        // Never replace an authored pack bound to the same section.
        if (section>=2800) return;
        uint32_t info = M32(0x009A3B50u + 0x60 + section*4);
        if (!info || M32(info+8)) return;
        uint32_t guest = gmalloc_try(p->size);
        if (!guest) return;
        memcpy(G+guest, lamp_bytes+p->offset, p->size);
        call_guest(c,0x00501A10u,1,&guest);
        loaded[i].guest=guest; loaded[i].owner=chunk;
        held += p->size; ++loads;
        if (diag_on) diag_event("daynight_lamps: load section=%u lamps=%u bytes=%u held=%u loads=%u",
                               section,M16(guest+16+0x50),p->size,held,loads);
        return;
    }
}

static int load_scenery(Cpu *c) {
    uint32_t chunk=arg(c,0), section=scenery_section(chunk);
    dispatch(c,scenery_load);
    Cpu saved=*c;
    if (c->eax && section!=UINT32_MAX) pack_load(c,chunk,section);
    *c=saved;
    return 1;
}

static int unload_scenery(Cpu *c) {
    uint32_t chunk=arg(c,0);
    Cpu saved=*c;
    for (unsigned i=0; i<LAMP_PACK_COUNT; ++i) if (loaded[i].guest && loaded[i].owner==chunk) {
        uint32_t guest=loaded[i].guest;
        call_guest(c,0x00501BF0u,1,&guest);
        gfree(guest); held-=lamp_packs[i].size;
        loaded[i].guest=loaded[i].owner=0;
    }
    *c=saved;
    dispatch(c,scenery_unload);
    return 1;
}

// CallChunkLoader 0064a8b0 and both unload paths ask FindLoader for the
// registered entry. Wrap only the scenery callbacks (+8/+c), keeping their
// existing material observers. One hook covers both streaming directions.
void f_0045D650_orig(Cpu *c);
void f_0045D650(Cpu *c) {
    uint32_t id=arg(c,0);
    f_0045D650_orig(c);
    if (id!=0x80034100u || !c->eax || !active()) return;
    uint32_t entry=c->eax;
    if (!load_wrapper) {
        scenery_load=M32(entry+8); scenery_unload=M32(entry+12);
        load_wrapper=host_fn(load_scenery,"daynight lamps load");
        unload_wrapper=host_fn(unload_scenery,"daynight lamps unload");
    }
    M32(entry+8)=load_wrapper; M32(entry+12)=unload_wrapper;
}

static uint64_t counted_frame=UINT64_MAX;
static unsigned world_queued, world_drawn, lamp_queued, lamp_drawn, packs_in_view;
static unsigned char seen[sizeof lamp_packs / sizeof *lamp_packs];

void daynight_lamps_frame(uint64_t frame) {
    if (!diag_on || frame==counted_frame) return;
    if (counted_frame!=UINT64_MAX && (counted_frame%60==0 || diag_frame_wanted(counted_frame,"BOOT_DIAG_SHOTS")))
        diag_event("daynight_lamps: frame=%llu world_queued=%u world_drawn=%u lamp_queued=%u lamp_drawn=%u packs_in_view=%u held=%u loads=%u",
                   (unsigned long long)counted_frame,world_queued,world_drawn,lamp_queued,lamp_drawn,packs_in_view,held,loads);
    counted_frame=frame;
    world_queued=world_drawn=lamp_queued=lamp_drawn=packs_in_view=0;
    memset(seen,0,sizeof seen);
}

void daynight_lamps_render(Cpu *c, void (*render)(Cpu *)) {
    // Return address identifies 00505e80's world loop; main view only,
    // excluding cars, mirrors, environment maps and reflected submissions.
    uint32_t flare=arg(c,1), view=arg(c,0);
    int world=M32(c->esp)==0x00505F3Du && view && M32(view+4)==1;
    int pack=-1;
    if (world) {
        daynight_lamps_frame(perf_nframes+1);
        ++world_queued;
        for (unsigned i=0; i<LAMP_PACK_COUNT; ++i)
            if (loaded[i].guest && flare>=loaded[i].guest && flare-loaded[i].guest<lamp_packs[i].size) {
                pack=(int)i; ++lamp_queued;
                if (!seen[i]) { seen[i]=1; ++packs_in_view; }
                break;
            }
    }
    render(c);
    if (world && c->eax) { ++world_drawn; if (pack>=0) ++lamp_drawn; }
}
