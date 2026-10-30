// Online HUD behind Minimap::UpdateAiRacerElements. Reuse spare racer icons and clone
// six strings owned by the live HUD package (which destroys them on unload).
// Contracts: Minimap +0x10 package name, +0xf0 eight racer images (0059D500);
// FEPackage +0x4c objects; FEGroup +0x64 children; FEObject +0x18 type (005B1560).
#include "host.h"
#include "roam_online.h"
#include <math.h>
#include <stdio.h>
#include <string.h>
static uint32_t scratch;
static uint32_t hud_call(Cpu *c, uint32_t self, uint32_t fn, int n, const uint32_t *args) {
    Cpu q = *c; q.esp = scratch + 0x4000; q.ecx = self;
    return call_guest(&q, fn, n, args);
}
static uint32_t bits(float f) { uint32_t u; memcpy(&u, &f, 4); return u; }
static uint32_t string_template(uint32_t first, int depth) {
    if (depth > 8) return 0;
    for (int i = 0; first && i < 1024; ++i, first = M32(first + 4)) {
        if (M32(first + 0x18) == 2) return first;
        if (M32(first + 0x18) == 5) {
            uint32_t s = string_template(M32(first + 0x64), depth+1); if (s) return s;
        }
    }
    return 0;
}
// Main-world homogeneous projection; FE x spans +/-240*aspect after Hor+ correction.
static int project(const float pos[3], float *x, float *y) {
    uint32_t plat = M32(0x00919650u);
    if (!plat) return 0;
    float p[4] = {pos[0],pos[1],pos[2]+2.4f,1}, clip[4] = {0};
    for (int j = 0; j < 4; ++j) for (int i = 0; i < 4; ++i) clip[j] += p[i] * MF32(plat+0x80+(i*4+j)*4);
    if (diag_on) { static unsigned samples; if (!(samples++%300))
        diag_event("roam-hud: project pos %.2f %.2f %.2f clip %.3f %.3f %.3f %.3f plat %08X",pos[0],pos[1],pos[2],clip[0],clip[1],clip[2],clip[3],plat); }
    if (!isfinite(clip[3]) || clip[3] <= 0.01f || clip[2] < 0 || clip[2] > clip[3]) return 0;
    float nx=clip[0]/clip[3], ny=clip[1]/clip[3];
    if (!isfinite(nx) || !isfinite(ny) || fabsf(nx)>0.95f || fabsf(ny)>0.95f) return 0;
    unsigned h=M32(0x00982BE8u), w=M32(0x00982BE4u); if (!h || !w) return 0;
    *x=nx*240.0f*w/h; *y=-ny*240.0f; return 1;
}
// Restore stock icon colours before the game's next update (including Leave or AI slot reuse).
static struct { uint32_t icon, guid, data, rgba[4]; } icon_colours[8];
static void restore_icon_colours(uint32_t map) {
    for (int i=0; i<8; ++i) {
        uint32_t icon=M32(map+0xf0+4*i), data=icon_colours[i].data;
        if (icon && icon==icon_colours[i].icon && M32(icon+0xc)==icon_colours[i].guid && M32(icon+0x2c)==data) {
            memcpy(G+data,icon_colours[i].rgba,16);
            M32(icon+0x1c)|=0x2400000u;
        }
        icon_colours[i].icon=0;
    }
}
void f_0057A110_orig(Cpu *c);
void f_0057A110(Cpu *c) {
    uint32_t map = c->ecx;
    static int was_online;
    if (was_online) restore_icon_colours(map);
    f_0057A110_orig(c);
    int online = atomic_load_explicit(&roam_online_active, memory_order_relaxed);
    if (!online && !was_online) return;
    was_online = online;
    if (!scratch) scratch = galloc(0x5000);
    uint32_t package_name = M32(map+0x10), fe=M32(0x0091CADCu);
    if (!package_name || !fe) return;
    if (!M32(fe) || M32(fe)==0xffffff28u) return;
    uint32_t package=hud_call(c,fe,0x00516AF0u,1,&package_name);
    if (!package) return;
    RoamHudPlayer players[32]; int n=online ? roam_game_hud(players,32) : 0;
    if (diag_on) { static unsigned snapshots; if (!(snapshots++%300))
        diag_event("roam-hud: snapshot players %d drawable %d",n,n ? players[0].drawable : 0); }
    // Preserve the game's AI icons; free roam normally leaves all eight slots free.
    unsigned ai=M32(0x0092CD24u+3*0x18); if (ai>8) ai=8;
    for (int i=0; i<n && ai<8; ++i,++ai) {
        uint32_t icon=M32(map+0xf0+4*ai); if (!icon) continue;
        float pos[2]={players[i].pos[0],players[i].pos[1]};
        memcpy(G+scratch,pos,8); memcpy(G+scratch+16,players[i].direction,8);
        uint32_t args[4]={scratch,scratch+16,icon,0};
        uint32_t data=M32(icon+0x2c);
        icon_colours[ai].icon=icon; icon_colours[ai].guid=M32(icon+0xc); icon_colours[ai].data=data;
        memcpy(icon_colours[ai].rgba,G+data,16);
        hud_call(c,map,0x005675D0u,4,args);
        // Online friends use the nameplate colour, distinct from the local yellow arrow.
        // FEColor is four B/G/R/A integers. Tint only live data, not stock animation keys.
        M32(data)=255; M32(data+4)=239; M32(data+8)=112; M32(data+12)=255;
        M32(icon+0x1c)|=0x2400000u;
    }
    int label_player=0, created=0;
    for (int i=0; i<ROAM_CAR_BUDGET; ++i) {
        snprintf((char*)G+scratch+64,64,"ROAM_NAME_%d",i);
        uint32_t name=scratch+64, hash=hud_call(c,0,0x00573140u,1,&name);
        uint32_t label=hud_call(c,package,0x005B8550u,1,&hash);
        if (!label && online && !created) {
            uint32_t donor=string_template(M32(package+0x4c),0), zero=0;
            if (donor) label=hud_call(c,donor,0x0050D8F0u,1,&zero);
            if (label) {
                hud_call(c,label,0x005B12F0u,1,&name);
                hud_call(c,label,0x005B7DC0u,1,&zero); // no current animation script
                // A HUD template can be permanently hidden or inherit a group's transform.
                // These labels belong to the package root and use a full alphabet font.
                M32(label+0x1c) &= ~0x41u;
                M16(label+0x20) = 0;
                // FEObject's copy constructor leaves the runtime render-cache slot
                // uninitialised; only the renderer may populate this clone's cache.
                M32(label+0x58) = 0;
                M16(label+0x22) = 0xffff; // the chosen font is independent of the donor resource index
                // Clone allocates zeroed FEObjData. FEngine::RenderObject (005B7690)
                // admits only positive FE depth, so a root label at z=0 is culled.
                uint32_t data=M32(label+0x2c);
                MF32(data+0x24)=1.0f;
                MF32(data+0x34)=1.0f; MF32(data+0x40)=1.0f;
                M32(label+0x6c) = 5; M32(label+0x74) = 0; // centred, no template text-width clamp
                snprintf((char*)G+scratch+192,32,"FONT_ARIAL");
                uint32_t font_name=scratch+192, font_hash=hud_call(c,0,0x00573140u,1,&font_name);
                uint32_t font=hud_call(c,0,0x00585B50u,1,&font_hash);
                if (font) M32(label+0x24)=font_hash;
                if (diag_on) diag_event("roam-hud: font requested %08X found %08X texture %08X chosen %08X",font_hash,font,font?M32(font+8):0,M32(label+0x24));
                hud_call(c,package,0x0050DB90u,1,&label);
                uint32_t scale[2]={label,bits(1.0f)};
                hud_call(c,0,0x00515380u,2,scale); hud_call(c,0,0x00515450u,2,scale);
                uint32_t colour[2]={label,0xff70efffu}; hud_call(c,0,0x005157E0u,2,colour);
                created=1;
                if (diag_on) diag_event("roam-hud: created label %d package %08X template %08X",i,package,donor);
            }
        }
        if (!label) continue;
        hud_call(c,0,0x00514C70u,1,&label);
        while (label_player<n && !players[label_player].drawable) ++label_player;
        if (label_player>=n) continue;
        RoamHudPlayer *p=&players[label_player++]; float x,y;
        if (!project(p->label_pos,&x,&y)) continue;
        snprintf((char*)G+scratch+128,32,"%s",p->name);
        uint32_t text=scratch+128; hud_call(c,label,0x005BCC10u,1,&text);
        uint32_t centre[3]={label,bits(x),bits(y)}; hud_call(c,0,0x00525050u,3,centre);
        hud_call(c,0,0x00514CC0u,1,&label);
        if (diag_on) { static unsigned seen; if (!(seen++%300)) {
            uint32_t data=M32(label+0x2c);
            diag_event("roam-hud: name %s id %u at %.1f %.1f; minimap icons %d flags %08X data %08X rgba %u %u %u %u xyz %.1f %.1f %.1f q %.1f %.1f %.1f %.1f scale %.1f %.1f %.1f",p->name,p->id,x,y,n<8?n:8,M32(label+0x1c),data,M32(data),M32(data+4),M32(data+8),M32(data+12),MF32(data+0x1c),MF32(data+0x20),MF32(data+0x24),MF32(data+0x28),MF32(data+0x2c),MF32(data+0x30),MF32(data+0x34),MF32(data+0x38),MF32(data+0x3c),MF32(data+0x40));
        } }
    }
}
