// Production hook/projection with fake FE calls; checks package ownership and Leave cleanup.
#include "host.h"
#include "roam_online.h"
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "roam_hud.c"
uint8_t *G; int diag_on; atomic_int roam_online_active;
static int checks,failed,guest_calls,icons,shown,hidden,allocated,current_label,originals;
static uint32_t labels[6];
#define CHECK(x) do { ++checks; if (!(x)) { ++failed; printf("FAIL %d: %s\n",__LINE__,#x); } } while (0)
void diag_event(const char *fmt,...) { (void)fmt; }
uint32_t galloc(uint32_t n) { CHECK(n==0x5000); return 0x20000; }
int roam_game_hud(RoamHudPlayer *p,int capacity) {
    CHECK(capacity>=1); *p=(RoamHudPlayer){.id=7,.name="Roadrunner",.pos={0,0,0},.direction={1,0},.drawable=1}; return 1;
}
void f_0057A110_orig(Cpu *c) { (void)c; ++originals; }
uint32_t call_guest(Cpu *c,uint32_t fn,int n,const uint32_t *a) {
    (void)n; ++guest_calls;
    switch(fn) {
    case 0x00516AF0: CHECK(c->ecx==0x3000 && a[0]==0x4000); return 0x5000;
    case 0x00573140:
        if (!strcmp((char*)G+a[0],"FONT_ARIAL")) return 0xabcdef;
        current_label=G[a[0]+10]-'0'; CHECK(current_label>=0 && current_label<6); return (uint32_t)current_label;
    case 0x00585B50: CHECK(a[0]==0xabcdef); return 0xc000;
    case 0x005B8550: CHECK(c->ecx==0x5000); return labels[a[0]];
    case 0x0050D8F0: {
        CHECK(c->ecx==0x6000); ++allocated; uint32_t label=0x7000+allocated*0x100;
        M32(label+0x2c)=0x10000+allocated*0x100; M32(label+0x58)=0xdeadbeef;
        M32(label+0x1c)=0x41;M16(label+0x20)=3;M32(label+0x74)=1;return label;
    }
    case 0x005B12F0: labels[current_label]=c->ecx; break;
    case 0x0050DB90: CHECK(c->ecx==0x5000 && a[0]==labels[current_label]);
        CHECK(!M32(a[0]+0x58) && M16(a[0]+0x22)==0xffff); break;
    case 0x005675D0: CHECK(c->ecx==0x1000 && a[2]>=0x8000); ++icons; break;
    case 0x00514C70: ++hidden; break;
    case 0x00514CC0:
        CHECK(M16(a[0]+0x20)==0 && M32(a[0]+0x24)==0xabcdef && !M32(a[0]+0x74));
        // Model the decoded render-list admission, not just the visibility flag.
        if (!(M32(a[0]+0x1c)&0x40) && MF32(M32(a[0]+0x2c)+0x24)>0) ++shown; break;
    case 0x005BCC10: CHECK(!strcmp((char*)G+a[0],"Roadrunner")); break;
    default: break;
    }
    return 0;
}
int main(void) {
    G=calloc(1,16*1024*1024); if(!G)return 2;
    Cpu c={.ecx=0x1000}; f_0057A110(&c); CHECK(!guest_calls && originals==1);
    M32(0x1010)=0x4000; M32(0x0091CADC)=0x3000; M32(0x3000)=0x3100;
    M32(0x504c)=0x6000; M32(0x6018)=2;
    for(int i=0;i<8;++i) {
        uint32_t icon=0x8000+i*0x100; M32(0x10f0+4*i)=icon;
        M32(icon+0xc)=i+1; M32(icon+0x2c)=0x18000+i*0x100;
        M32(0x18000+i*0x100)=0;
    }
    M32(0x00919650)=0x9000; M32(0x00982BE4)=1920; M32(0x00982BE8)=1080;
    for(int i=0;i<4;++i) MF32(0x9080+(i*4+i)*4)=1;
    MF32(0x9080+3*4)=0; MF32(0x9080+15*4)=10;
    float pos[3]={0,0,0},x,y;
    CHECK(project(pos,&x,&y) && fabsf(x)<0.001f && fabsf(y)<0.001f);
    MF32(0x9080+15*4)=-1;CHECK(!project(pos,&x,&y));MF32(0x9080+15*4)=10;
    pos[0]=1000;CHECK(!project(pos,&x,&y));pos[0]=0;pos[2]=-10;CHECK(!project(pos,&x,&y));
    pos[2]=0;MF32(0x9080+15*4)=NAN;CHECK(!project(pos,&x,&y));MF32(0x9080+15*4)=10;
    atomic_store(&roam_online_active,1);
    for(int i=0;i<8;++i){int before=allocated;c.ecx=0x1000;f_0057A110(&c);CHECK(allocated-before<=1);}
    CHECK(allocated==6 && icons==8 && M32(0x18000)==255 && M32(0x18004)==239 && M32(0x18008)==112 && M32(0x1800c)==255 && shown==8);
    // With AI present, the hook must consume only the spare icon slots.
    M32(0x0092CD24+3*0x18)=8;int old_icons=icons;c.ecx=0x1000;f_0057A110(&c);CHECK(icons==old_icons); CHECK(M32(0x18000)==0);
    atomic_store(&roam_online_active,0);int old_hidden=hidden,old_shown=shown;c.ecx=0x1000;f_0057A110(&c);
    CHECK(hidden-old_hidden==6 && shown==old_shown);
    int before=guest_calls;f_0057A110(&c);CHECK(before==guest_calls);
    printf("HUD: %d checks, %d failures; labels=%d icons=%d shown=%d\n",checks,failed,allocated,icons,shown);
    free(G);return failed?1:0;
}
