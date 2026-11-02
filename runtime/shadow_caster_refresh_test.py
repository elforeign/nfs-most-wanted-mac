"""Current-fit scenery selection, disabled parity and bounded frame-arena use.
Production refresh + translated visibility (006CF2B0), DrawAScenery (00729120)
and matrix allocator (00405ED0), ASan/UBSan, real X360/steady fits.
Current: 3,956 checks / 0 failures; pinned main: 4,559 / 65;
pinned reviewed branch: 6,378 / 88. Stale selections restored: main 0/10,
current 10/10. Solo both Off reculls: reviewed 18, main/current 0.
Frame arena bytes across 12 frames: main/current 23,424, reviewed 47,360;
every frame must match main and retain its last available matrix allocation.
Guard-omission control: 4,550 / 22 vs current 3,956 / 0.
Arena-leak control: 3,720 / 24 vs current 3,956 / 0.
Plane/recull omission controls fail 11/23. SHA-pinned function baselines;
no GPU, game or emulation. Cull traversal and LOD are recording sinks;
nonpacked matrix allocation and draw-table append execute translated code.
Autodiscovered by offline_tests.sh.
"""
from pathlib import Path
import hashlib
import re
import runpy
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = 'void split_shadow_casters(Cpu *c) {\n    if (!shadow_cull || !shadow_source || !shadow_split()) return;\n    Cpu saved = *c;\n    uint32_t n = M32(shadow_cull + 0x9C0);\n    for (uint32_t i = 0; i < n; ++i) {\n        uint32_t entry = shadow_cull + i * 0xD0;\n        if (M32(entry + 0x80) != 0x00919C70u) continue;\n        uint32_t a[3] = {0x00919C70u, entry, 0x4100};\n        call_guest(c, 0x004FAE40u, 3, a);\n        c->ecx = shadow_cull; call_guest(c, 0x00731B80u, 0, NULL);\n        break;\n    }\n    *c = saved;\n}'
ORIGINAL_SHA = "5c6d13dfe222b19bb90df6a3ab0557483f74651eae38d1d29790d6f8f38ad0d0"

REVIEWED = 'void x360_shadow_casters(Cpu *c, uint32_t cull, uint32_t light_view) {\n    uint32_t plat = M32(light_view);\n    if (!cull || !plat) return;\n    uint32_t n = M32(cull + 0x9C0);\n    for (uint32_t i = 0; i < n; ++i) {\n        uint32_t entry = cull + i * 0xD0;\n        if (M32(entry + 0x80) != light_view) continue;\n        const float *m = (const float *)(G + plat + 0x80);\n        float planes[6][4];\n        for (int k = 0; k < 6; ++k) {\n            double norm = 0;\n            for (int r = 0; r < 4; ++r) {\n                planes[k][r] = k == 4 ? m[4*r+2] :\n                    m[4*r+3] + (k < 4 && !(k&1) ? 1 : -1)*m[4*r+k/2];\n                if (r < 3) norm += (double)planes[k][r]*planes[k][r];\n            }\n            if (!isfinite(norm) || norm <= 0) return;\n            norm = sqrt(norm);\n            for (int r = 0; r < 4; ++r) planes[k][r] /= norm;\n        }\n        memcpy(G + plat + 0x140, planes, sizeof planes);\n        Cpu saved = *c;\n        uint32_t a[3] = {light_view, entry, 0x4100};\n        call_guest(c, 0x004FAE40u, 3, a);\n        c->ecx = cull;\n        call_guest(c, 0x00731B80u, 0, NULL);\n        *c = saved;\n        break;\n    }\n}'
REVIEWED_SHA = '633854ef8654cbd79bbcf09b736e964a727847089cfc427acc5249ddf6057607'

HARNESS = r'''
#include "runtime.h"
#include <stdio.h>
#include <stdlib.h>
uint8_t *G;
static unsigned checks,failures,refreshes,mode;
static int fit=2, shadow_stable;
static unsigned allocations;
static uint32_t gmalloc_try(uint32_t n) { (void)n; return 0xD00000; }
static uint32_t shadow_source,shadow_cull;
static int shadow_split(void) { return mode; }
#define CHECK(x) do { ++checks; if(!(x)){if(failures<5)fprintf(stderr,"line %d: %s\n",__LINE__,#x);++failures;} } while(0)
void f_006BE800(Cpu *c) { (void)c; abort(); } // transformed bounds are not used
/* VISIBILITY */
/* ALLOCATOR */
void f_004FCA70(Cpu *c) { f_006CF2B0(c); }
void f_00729060(Cpu *c) { MF32(M32(c->esp+16))=1; c->eax=64; c->esp+=20; }
void f_006BF530(Cpu *c) { c->eax=0; c->esp+=4; }
void f_006C13F0(Cpu *c) { c->eax=2; c->esp+=4; }
void f_006BEEB0(Cpu *c) {
    uint32_t out=M32(c->esp+4);memset(G+out,0,64);
    for(unsigned i=0;i<4;i++)MF32(out+20*i)=1;
    ++allocations;c->esp+=8;
}
void f_006BEE80(Cpu *c) { c->esp+=8; }
void f_00501680(Cpu *c) { c->eax=0;c->esp+=8; }
#define UNREACHABLE(fn) void fn(Cpu *c) { (void)c;abort(); }
UNREACHABLE(f_006C29A0)
UNREACHABLE(f_007C4B80)
UNREACHABLE(f_0073CAD0)
UNREACHABLE(f_0045ECF0)
UNREACHABLE(f_004FC780)
UNREACHABLE(f_006BD920)
UNREACHABLE(f_0045F3C0)
void guest_divide_error(Cpu *c,uint32_t pc) { (void)c;(void)pc;abort(); }
/* DRAW */
static uint32_t visible(uint32_t view,const float lo[3],const float hi[3]) {
    memcpy(G+0x600000,lo,12);memcpy(G+0x600010,hi,12);
    Cpu c={.ecx=view,.esp=0x800000,.cw=0x37f};
    M32(c.esp)=0xDEAD;M32(c.esp+4)=0x600000;M32(c.esp+8)=0x600010;M32(c.esp+12)=0;
    f_006CF2B0(&c);CHECK(c.esp==0x800010);return c.eax;
}
static float boxes[64][2][3];
static unsigned count;
static uint64_t list_masks[3];
static unsigned expected_rebuilds, culls[3];
static void rebuild(uint32_t entry) {
    unsigned i=(entry-shadow_cull)/0xD0;
    ++culls[i];++expected_rebuilds;
    for(unsigned b=0;b<count;b++) {
        Cpu d={.ecx=0x400000,.esp=0x810000,.cw=0x37f};
        M32(d.esp)=0xDEAD;M32(d.esp+4)=b;M32(d.esp+8)=entry;M32(d.esp+12)=1;
        f_00729120(&d);CHECK(d.esp==0x810010);
    }
    list_masks[i]=0;
    for(uint32_t p=M32(entry+0x88);p<M32(entry+0x8C);p+=12) {
        unsigned b=(M32(p+8)-0x410000)/64;CHECK(b<count);list_masks[i]|=1ull<<b;
        if(!(M32(0x410000+b*64+0x18)&0x200))CHECK(M32(p+4)!=0);
    }
}
static void all_views(void) {
    uint32_t next=0x994368;
    for(unsigned i=0;i<3;i++) {
        uint32_t e=shadow_cull+i*0xD0;
        M32(e+0x88)=M32(e+0x8C)=next;M32(e+0x90)=0x9A2DC8;
        rebuild(e);next=M32(e+0x8C);
    }
    M32(shadow_cull+0x9C8)=next;
}
static uint32_t call_guest(Cpu *c,uint32_t fn,int n,const uint32_t *a) {
    if(fn==0x004FAE40) {
        CHECK(n==3 && a[0]==0x919C70 && a[1]==shadow_cull+0xD0 && a[2]==0x4100);
        M32(a[1]+0x80)=a[0];M32(a[1]+0x84)=0x4110;
        ++refreshes;
    } else if(fn==0x00731B80) {
        CHECK(n==0 && !a && c->ecx==shadow_cull);all_views();
    } else if(fn==0x00731B10) {
        CHECK(n==1 && a[0]==shadow_cull+0xD0 && c->ecx==shadow_cull);rebuild(a[0]);
    } else abort();
    memset(c,0xA5,sizeof *c);return 0;
}
/* REFRESH */
/* ORIGINAL */
static void update(Cpu *c,uint32_t cull,uint32_t view) {
#ifdef OLD
    (void)cull;(void)view;split_shadow_casters(c);
#else
    x360_shadow_casters(c,cull,view);
#endif
}
void x360_shadow_fit(const float *,float,float,const float *,float,float,float *);
void x360_shadow_stable_fit(const float *,float,float,const float *,float,int,int,float *);
static void view(float m[16],float x,float yaw) {
    memset(m,0,64);m[0]=cosf(yaw);m[2]=-sinf(yaw);m[4]=sinf(yaw);m[6]=cosf(yaw);m[9]=-1;m[15]=1;
    m[12]=-x*m[0];m[13]=1;m[14]=-x*m[2];
}
static int inverse(const float m[16],double out[4][4]) {
    double a[4][8];
    for(unsigned i=0;i<4;i++)for(unsigned j=0;j<8;j++)a[i][j]=j<4?m[4*i+j]:(j-4==i);
    for(unsigned i=0;i<4;i++) {
        unsigned p=i;for(unsigned j=i+1;j<4;j++)if(fabs(a[j][i])>fabs(a[p][i]))p=j;
        if(fabs(a[p][i])<1e-20)return 0;
        for(unsigned j=0;j<8;j++){double t=a[i][j];a[i][j]=a[p][j];a[p][j]=t;}
        double scale=a[i][i];for(unsigned j=0;j<8;j++)a[i][j]/=scale;
        for(unsigned k=0;k<4;k++)if(k!=i){double s=a[k][i];for(unsigned j=0;j<8;j++)a[k][j]-=s*a[i][j];}
    }
    for(unsigned i=0;i<4;i++)for(unsigned j=0;j<4;j++)out[i][j]=a[i][j+4];return 1;
}
static void planes(uint32_t plat,const float m[16]) {
    // Independent reference: inward D3D clip inequalities, no texture bias.
    for(unsigned k=0;k<6;k++) {
        unsigned axis=k/2;double q[4],norm=0;
        for(unsigned r=0;r<4;r++) {
            q[r]=k==4?m[4*r+2]:m[4*r+3]+(k%2?-1:1)*m[4*r+axis];
            if(r<3)norm+=q[r]*q[r];
        }
        norm=sqrt(norm);for(unsigned r=0;r<4;r++)MF32(plat+0x140+k*16+r*4)=q[r]/norm;
    }
}
static void candidates(const float m[16]) {
    double inv[4][4];CHECK(inverse(m,inv));
    // Interior and exterior samples at each of the six clip-volume faces.
    for(unsigned face=0;face<6;face++)for(unsigned side=0;side<2;side++) {
        double p[4]={0,0,.5,1},w[4]={0};
        unsigned axis=face/2;
        p[axis]=axis==2?(face%2?1:0):(face%2?1:-1);
        p[axis]+=(face%2?1:-1)*(side?.001:-.001);
        for(unsigned j=0;j<4;j++)for(unsigned k=0;k<4;k++)w[j]+=p[k]*inv[k][j];
        for(unsigned j=0;j<3;j++)boxes[count][0][j]=boxes[count][1][j]=w[j]/w[3];
        ++count;
    }
}
int main(void) {
    G=calloc(1,0x1000000);MF32(0x8933D4)=.5f;
    unsigned stale_cases=0,restored_cases=0;
    unsigned frame_main=0,frame_current=0,off_reculls=0;
    M32(0x400018)=0x420000;M32(0x400020)=0x410000;
    M32(0x420028)=M32(0x420030)=M32(0x420034)=0x500000;
    for(unsigned i=0;i<3;i++)M32(0x919650+i*0x70+0x40)=0x300000+i*0x1000;
    for(mode=0;mode<2;mode++)for(unsigned stable=0;stable<2;stable++)for(unsigned motion=0;motion<3;motion++) {
        fit=stable?0:2;shadow_stable=stable;
        memset(G+0x100000,0,0x1000);shadow_cull=0x100000;shadow_source=0x919650;
        M32(shadow_cull+0x9C0)=3;
        uint32_t views[3]={0x919650,0x919C70,0x9196C0};
        for(unsigned i=0;i<3;i++) {M32(views[i])=0x200000+i*0x1000;M32(shadow_cull+i*0xD0+0x80)=views[i];}
        float old[16],now[16],v[16],light[3]={-.467f,-.464f,.753f};
        view(v,0,0);
        if(stable)x360_shadow_stable_fit(v,.84569f,1.50345f,light,70,1600,1,old);
        else x360_shadow_fit(v,.84569f,1.50345f,light,20.1f,70,old);
        view(v,motion==1?0:1.25f,motion==0?0:.02f);
        if(stable)x360_shadow_stable_fit(v,.84569f,1.50345f,light,70,1600,1,now);
        else x360_shadow_fit(v,.84569f,1.50345f,light,20.1f,70,now);
        count=0;candidates(now);candidates(old);CHECK(count<=64);
        uint32_t plat=M32(0x919C70);memcpy(G+plat+0x80,now,64);
        planes(plat,now);uint64_t expected=0;
        for(unsigned b=0;b<count;b++)if(visible(0x919C70,boxes[b][0],boxes[b][1]))expected|=1ull<<b;
        planes(plat,old);uint64_t stale=0;
        for(unsigned b=0;b<count;b++)if(visible(0x919C70,boxes[b][0],boxes[b][1]))stale|=1ull<<b;
        // A yaw-invariant stable map can remain unchanged for yaw-only motion.
        if(stale!=expected)++stale_cases;
        for(unsigned b=0;b<count;b++) {
            memcpy(G+0x410000+b*64,boxes[b][0],12);memcpy(G+0x41000C+b*64,boxes[b][1],12);
            M32(0x410018+b*64)=b%2?0x200:0; // mixed packed/nonpacked scenery
        }
        for(unsigned i=0;i<3;i++) {
            uint32_t e=shadow_cull+i*0xD0;
            M32(e+0x84)=0x4110;M32(e+0xC4)=UINT32_MAX;
            if(i!=1)planes(M32(views[i]),old);
        }
        M32(0x915F84)=0xB00000;M32(0x915F88)=0xC00000;
        M32(0x915F94)=M32(0x915F98)=0;
        all_views();
        CHECK(list_masks[1]==stale);
        unsigned main_bytes=M32(0x915F84)-0xB00000;frame_main+=main_bytes;
        unsigned other_culls[2]={culls[0],culls[2]};
        uint8_t receiver_entries[2][0xD0], receiver_table[60000];
        memcpy(receiver_entries[0],G+shadow_cull,0xD0);
        memcpy(receiver_entries[1],G+shadow_cull+2*0xD0,0xD0);
        memcpy(receiver_table,G+0x994368,60000);
        uint64_t receiver_masks[2]={list_masks[0],list_masks[2]};
        unsigned before=refreshes,rebuilds=expected_rebuilds;
        Cpu c={.esp=0x800000,.eax=23,.ecx=43,.ebx=91,.ebp=0x700000,.cw=0x37f},saved=c;
        update(&c,shadow_cull,0x919C70);
        CHECK(!memcmp(&c,&saved,sizeof c));CHECK(refreshes==before+1);CHECK(expected_rebuilds==rebuilds+1);
        CHECK(list_masks[1]==expected);if(stale!=expected && list_masks[1]==expected)++restored_cases;
        CHECK(!memcmp(G+plat+0x80,now,64));
        CHECK(culls[0]==other_culls[0] && culls[2]==other_culls[1]);
        CHECK(list_masks[0]==receiver_masks[0] && list_masks[2]==receiver_masks[1]);
        CHECK(!memcmp(receiver_entries[0],G+shadow_cull,0xD0));
        CHECK(!memcmp(receiver_entries[1],G+shadow_cull+2*0xD0,0xD0));
        CHECK(!memcmp(receiver_table,G+0x994368,60000));
        frame_current+=M32(0x915F84)-0xB00000;
        CHECK(M32(0x915F84)-0xB00000==main_bytes);
        CHECK(M32(0x915F94)==0 && M32(0x915F98)==0);
        // The last available frame matrix must remain available for a later draw.
        M32(0x915F88)=M32(0x915F84)+65;
        update(&c,shadow_cull,0x919C70);
        Cpu tail={.esp=0x820000};M32(tail.esp+4)=1;f_00405ED0(&tail);
        CHECK(tail.eax!=0 && M32(0x915F94)==0);
        // Both switches Off: same memory/CPU, zero reculls and zero plane writes.
        fit=0;shadow_stable=0;
        planes(plat,old);
        uint8_t *off_memory=malloc(0x1000000);memcpy(off_memory,G,0x1000000);
        unsigned off_refreshes=refreshes,off_culls=expected_rebuilds;
        Cpu off=c;update(&c,shadow_cull,0x919C70);
        CHECK(!memcmp(&off,&c,sizeof c));
        CHECK(refreshes==off_refreshes && expected_rebuilds==off_culls);
        CHECK(!memcmp(off_memory,G,0x1000000));free(off_memory);
        if(!mode)off_reculls+=expected_rebuilds-off_culls;
        fit=2;
        for(unsigned k=0;k<24;k++)CHECK(isfinite(MF32(plat+0x140+k*4)));
        CHECK(M32(shadow_cull+0x80)==views[0] && M32(shadow_cull+2*0xD0+0x80)==views[2]);
    }
    CHECK(stale_cases>=8);CHECK(restored_cases==stale_cases);
    printf("frame arena bytes: main %u, current %u; matrix writes %u\n",frame_main,frame_current,allocations);
    printf("solo both Off reculls: %u\n",off_reculls);
    printf("caster refresh: %u checks, %u failures; stale cases %u, restored %u\n",checks,failures,stale_cases,restored_cases);
    free(G);return failures?1:0;
}
'''


def function(source, name):
    return re.search(r'void '+name+r'\([^\n]*\).*?\n\}', source, re.S)[0]


def main():
    assert hashlib.sha256(ORIGINAL.encode()).hexdigest() == ORIGINAL_SHA
    assert hashlib.sha256(REVIEWED.encode()).hexdigest() == REVIEWED_SHA
    production = function((ROOT/'runtime/x360.m').read_text(), 'x360_shadow_casters')
    visibility = function((ROOT/'recomp/out/speed_40.c').read_text(), 'f_006CF2B0')
    allocator = function((ROOT/'recomp/out/speed_00.c').read_text(), 'f_00405ED0')
    draw = function((ROOT/'recomp/out/speed_45.c').read_text(), 'f_00729120_orig').replace('f_00729120_orig(', 'f_00729120(')
    fixture = HARNESS.replace('/* ALLOCATOR */', allocator).replace('/* DRAW */', draw).replace('/* VISIBILITY */', visibility).replace('/* ORIGINAL */', ORIGINAL)
    with tempfile.TemporaryDirectory(prefix='shadow-caster-') as directory:
        work = Path(directory)
        # Reuse reviewed, SHA-checked tuning so this fixture needs no game data.
        runpy.run_path(str(ROOT/'runtime/shadow_near_fit_test.py'))['pinned'](work)
        fit_source = work/'fit.cpp'
        fit_source.write_text((ROOT/'runtime/x360_shadow_fit.cpp').read_text())
        fit = work/'fit.o'
        subprocess.run(['clang++','-std=c++17','-fsanitize=address,undefined','-c',
                        '-I',str(ROOT/'runtime'),str(fit_source),'-o',str(fit)], check=True, capture_output=True)
        variants = [('current', production, False), ('original', production, True), ('reviewed', REVIEWED, False),
                    ('disabled refresh', production.replace('if (fit != 2 && !shadow_stable) return;', ''), False),
                    ('frame arena leak', production.replace('M32(0x00915F84u) = storage + table_bytes;', '').replace(
                        'M32(0x00915F88u) = storage + table_bytes + matrix_bytes;', '').replace(
                        'memcpy(G + 0x00915F84u, arena, sizeof arena);', ''), False),
                    ('stale planes', production.replace('memcpy(G + plat + 0x140, planes, sizeof planes);', ''), False),
                    ('skipped recull', production.replace('call_guest(c, 0x00731B10u, 1, &entry);',''), False)]
        for label, source, old in variants:
            path, binary = work/'test.c', work/'test'
            path.write_text(fixture.replace('/* REFRESH */', source))
            command = ['clang','-std=c11','-Wall','-Wextra','-Werror','-Wno-unused-function',
                       '-fsanitize=address,undefined','-I',str(ROOT/'recomp'), str(path),str(fit),'-lc++','-o',str(binary)]
            if old: command.insert(1,'-DOLD')
            compiled = subprocess.run(command, capture_output=True, text=True)
            if compiled.returncode: raise RuntimeError(compiled.stderr[-4000:])
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            print(label+': '+result.stdout.strip())
            assert result.returncode == (0 if label=='current' else 1), result.stderr[-3000:]
    print('SHADOW CASTER REFRESH PASS')


if __name__ == '__main__':
    main()
