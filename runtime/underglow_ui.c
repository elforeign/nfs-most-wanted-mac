/* Bounded retail diagnostic fixture for the native underglow menu (underglow_menu.c): drives the career into the
 * customise shop with the car the game itself selected, or with the stock car that owned:<car> (lowercase
 * vehicle name, e.g. owned:carreragt) creates as a career car. */
#include "host.h"
#include "underglow_ui.h"
#include "underglow.h"
#include "underglow_render.h"
#include <strings.h>

static uint32_t fixture_profile, fixture_row;
static uint32_t fixture_stack;
static uint64_t fixture_started;
static int fixture_shop_started;
static Cpu fixture_cpu(void) {
    if (!fixture_stack) fixture_stack = galloc(65536);
    Cpu c = {0}; c.esp = fixture_stack + 65520;
    return c;
}
static int fixture_fail(const char *why, uint32_t profile, const char *pkg) {
    fprintf(stderr,"underglow fixture: FAIL %s profile=%08X name=%.32s package=%s\n",
            why,profile,profile ? (char *)G+profile : "",pkg ? pkg : "none");
    return -1;
}
int underglow_ui_step(const char *step, uint64_t elapsed, const char *pkg, const char **key) {
    *key = NULL;
    if (!diag_on) return -1;
    uint32_t db=M32(0x0091CF90u), profile=db ? M32(db+0x10) : 0;
    if (!elapsed) { fixture_started=host_ticks(); fixture_shop_started=0; }
    if (elapsed >= 1800 || host_ticks()-fixture_started >= 30000000000ull)
        return fixture_fail("30 second timeout",profile,pkg);
    if (!profile || memcmp(G+profile,"TEST\0",5))
        return fixture_fail("expected TEST profile",profile,pkg);
    Cpu c=fixture_cpu(); c.ecx=profile+0xA8;
    if (!(call_guest(&c,0x0050E310u,0,NULL)&255))
        return fixture_fail("TEST career has not started",profile,pkg);
    /* The preceding scripted Return can still be closing the one-time bonus
     * dialog. Do not enter customization until the safe house owns input. */
    if ((!strcmp(step,"shop") || !strncmp(step,"owned:",6)) && !fixture_row && pkg && !strcmp(pkg,"Dialog.fng")) return 0;
    if (!strncmp(step,"owned:",6)) {
        const char *want=step+6;
        if (!*want || strlen(want)>31) return fixture_fail("owned needs a car name",profile,pkg);
        if (!pkg || strcmp(pkg,"MainMenu_Sub.fng"))
            return fixture_fail("owned requires MainMenu_Sub.fng",profile,pkg);
        uint32_t stock=0;
        for (unsigned i=0;i<200;++i) {
            uint32_t row=profile+0x414+i*20;
            if (M32(row)==0xFFFFFFFFu || G[row+16]!=255 || (M32(row+12)&6)) continue;
            c.ecx=row;
            uint32_t type=call_guest(&c,0x005816B0u,0,NULL);
            if (type>=256) continue;
            uint32_t name=call_guest(&c,0x00668370u,1,&type);
            if (name && !strcasecmp((char *)G+name,want)) { stock=row; break; }
        }
        if (!stock) return fixture_fail("stock car not found",profile,pkg);
        uint32_t handle=M32(stock); c.ecx=profile+0x414;
        uint32_t row=call_guest(&c,0x00599D20u,1,&handle);
        if (!row || row<profile+0x414 || row>=profile+0x414+4000 ||
            !(M32(row+12)&2) || G[row+16]>=76)
            return fixture_fail("CreateNewCareerCar failed",profile,pkg);
        handle=M32(row); c.ecx=profile+0xA8;
        call_guest(&c,0x005660A0u,1,&handle);
        fixture_profile=profile; fixture_row=row;
        c.ecx=row; uint32_t type=call_guest(&c,0x005816B0u,0,NULL);
        diag_event("underglow fixture: owned %s profile=%08X row=%08X handle=%u type=%u keys=%08X/%08X custom=%u career=%u package=%s",
            want,profile,row,handle,type,M32(row+4),M32(row+8),G[row+16],G[row+17],pkg);
        return 1;
    }
    if (!strcmp(step,"shop") && !fixture_row) {
        /* No fixture car: the career's own selected car (BeginCarCustomize's argument in retail's shop entry). */
        if (!pkg || strcmp(pkg,"MainMenu_Sub.fng"))
            return fixture_fail("shop requires MainMenu_Sub.fng",profile,pkg);
        uint32_t handle=M32(profile+0xA8); c.ecx=profile+0x414;
        uint32_t row=call_guest(&c,0x0056ECC0u,1,&handle);
        if (!row || G[row+16]>=76 || !(M32(row+12)&6))
            return fixture_fail("selected car is not an owned career car",profile,pkg);
        fixture_profile=profile; fixture_row=row;
        diag_event("underglow fixture: selected car handle=%u row=%08X keys=%08X/%08X",handle,row,M32(row+4),M32(row+8));
    }
    if (profile!=fixture_profile || !fixture_row || M32(profile+0xA8)!=M32(fixture_row))
        return fixture_fail("owned selection changed",profile,pkg);
    if (!strncmp(step,"scene:",6)) return underglow_render_scene_step(step+6,elapsed);
    if (!strcmp(step,"shop")) {
        if (!fixture_shop_started) {
            uint32_t career=1; c.ecx=db;
            call_guest(&c,0x0050E4C0u,1,&career);
            uint32_t a[]={1,fixture_row}; call_guest(&c,0x00589D60u,2,a);
            fixture_shop_started=1;
        }
        if (pkg && !strcmp(pkg,"CustomizeMain.fng") && (M32(db+0x12C)&1) &&
            M32(0x0091D3D4u)==fixture_row) {
            diag_event("underglow fixture: shop career=%08X owned=%08X package=%s",M32(db+0x12C),fixture_row,pkg);
            return 1;
        }
        return 0;
    }
    return fixture_fail("unknown step",profile,pkg);
}
