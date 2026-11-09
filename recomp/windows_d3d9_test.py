"""Guest D3D9 CPU recording through the real IAT and vtables, discovered by offline_tests.sh.

The fixture calls d3d9.dll!Direct3DCreate9 through IAT slot 008904A8 and every
COM method through its guest vtable slot from a synthetic call [eax+disp] site,
checking eax, exact esp and callee-saved registers on every call. It records the
ordinary (fe) and masked (fe_mask) front-end vs_1_1/ps_1_1 fixtures (SHA-256
pinned below) into backend-owned snapshots: 16/32-bit indices, nonzero base
vertex, two streams, odd-pitch partial texture locks, invalid ranges, UP
unbinding, bound-resource lifetime, immutable earlier draws and zero retained
objects after teardown. All data is synthetic; nothing is translated.

Numbers (each check shown failing when broken):
  pinned original host_import, no D3D9 registration: exit 2 at Direct3DCreate9
  first-import mode: exit 10; fixed fixture: exit 0
  unsupported vtable slot (Present) / outside-subset render state / draw
  without backend: exit 2 each
  wrong-slot mutant (DrawIndexedPrimitive registered at 80): exit 2
  DrawPrimitiveUP stream-0 unbind removed: exit 1;
  DrawIndexedPrimitiveUP index unbind removed: exit 1
  bindings not keeping released resources alive: exit 1
  pinned pre-review lines, each against the fixed fixture: READONLY lock of a
  WRITEONLY VB accepted exit 1 (fixed 0); indexed POINTLIST recorded exit 1
  (fixed 0); NULL pixel shader returned INVALIDCALL exit 1 (fixed exit 2);
  full table leaking 16 MiB guest arena per failed VB creation exit 1 (fixed 0)
SDK-free COFF audit of the Windows host layer with the new units: unknown 0;
without d3d9_state.c: unknown 8 (rejected).
Audio integration: C-only host audit leaves 31 unknown; shared C++ units reduce
that to 0. Removing those units deliberately reproduces 31 unknown (rejected).
Hosted SDK-free COFF at O2, before the calloc allowlist entry: unknown 1
(UNKNOWN: calloc), exit 1; with the entry: unknown 0, exit 0.
Hosted O1 is also audited: unknown 0 before/after with clang 21, which only
folds the two malloc + memset sites into calloc at O2 (the desktop policy).
Use --desktop for the same fixture with actual Windows SDK/CRT headers.
"""
from pathlib import Path
import argparse
import hashlib
import os
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent
WIN = ROOT / 'runtime/win'
SHADERS = ROOT / 'host/shaders/testdata'
FIXTURES = {
    'fe/t0_world_p0_vs_1_1.bin': 'd052fd907941ee4a7f5194ef6fac3e402ea4c6cbbc34dece044fd7eae6e08019',
    'fe/t0_world_p0_ps_1_1.bin': 'adf1eb6cc3716d24759139d3af433f2d177f423bdb0649a814cae70aee07b326',
    'fe_mask/t0_world_p0_vs_1_1.bin': 'b5ef251e76682d1aa9980558ac99cecc3c71ecb883ede02620e34ec354c2a439',
    'fe_mask/t0_world_p0_ps_1_1.bin': '9a611e53e931af0f1ebdd4d7c1c12fa0923f58bd87dc7474a4c5d1ea0ae4dd6f',
}

# The dispatch body this proof was written against, before any D3D9 import.
ORIGINAL_IMPORT = r'''void host_import(Cpu *c, uint32_t slot) {
    const struct ImportSlot *entry=win_import_find(speed_imports, speed_import_count, slot);
    if (!entry) win_fail("unknown IAT slot", slot);
    if (win_first_import) win_import_stop(c, entry);
    gil_maybe_yield(c);
    for (unsigned i=0; i<import_count; ++i) if (!strcmp(imports[i].name,entry->name)) {
        uint32_t before=c->esp;
        if (!win_vm_access(before,4)) win_fail(entry->name,before);
        uint32_t caller=M32(before);
        adapter_call(c,imports+i);
        win_startup_checkpoint(c,entry,caller,before);
        return;
    }
    const char *fn=entry->name;
    for (const char *p=fn; *p; ++p) if (*p=='!' || *p==':') fn=p+1;
    if ((!__builtin_strncmp(entry->name, "KERNEL32.dll!", 13) ||
         !__builtin_strncmp(entry->name, "WINMM.dll!", 10)) && win_kernel_import(c, fn)) return;
    win_import_stop(c, entry);
}
'''
ORIGINAL_IMPORT_SHA = 'f42f4dc95a0d7ab2974d81b1e95d49751eced76b717e08232714caf9ccc738e6'

# Pre-review buffer creation: guest arena taken before the table slot.
OLD_BUFFER_NEW = ('''    WDObject *b=wd_new(kind,vt[kind],o->guest); if (!b) return WD_OOM;
    uint32_t mem=win_alloc(size); if (!mem) { wd_release(b->guest); return WD_OOM; }
    b->data=mem; b->size=size; b->usage=A(2);''', '''    uint32_t mem=win_alloc(size); if (!mem) return WD_OOM;
    WDObject *b=wd_new(kind,vt[kind],o->guest); if (!b) return WD_OOM;
    b->data=mem; b->size=size; b->usage=A(2);''')

FIXTURE = r'''// Synthetic guest D3D9 fixture; compiled by windows_d3d9_test.py on either host.
#include "d3d9.h"
#include <stdarg.h>
#ifdef NFS_WIN_TEST
#include "fake_win.inc"
#endif
#include "shaders.inc"
#define CHECK(x) do { if (!(x)) { printf("failed line %d: %s\n",__LINE__,#x); return 1; } } while(0)
const struct ImportSlot speed_imports[]={{0x008904a8,"d3d9.dll!Direct3DCreate9"}};
const unsigned speed_import_count=1,guest_func_count=0;
const uint32_t speed_entry=0x00400000,guest_func_addr[]={0};
void (*const guest_func_ptr[])(Cpu *)={NULL};
static unsigned calls;
static uint32_t heap=0x40000;
static uint32_t scratch(uint32_t n) { uint32_t p=heap; heap+=(n+15)&~15u; memset(G+p,0,n); return p; }
static uint32_t put(const void *data, uint32_t n) { uint32_t p=scratch(n); memcpy(G+p,data,n); return p; }
// A guest call [eax+disp] site per slot, so returns look like real COM calls.
static uint32_t site(unsigned slot) {
    uint32_t p=0x401000+slot*8;
    M8(p)=0xff;
    if (slot<32) { M8(p+1)=0x50; M8(p+2)=(uint8_t)(slot*4); return p+3; }
    M8(p+1)=0x90; M32(p+2)=slot*4; return p+6;
}
static void regs_check(Cpu *c, uint32_t sp, unsigned n, unsigned slot) {
    if (c->esp!=sp+4+4*n || c->ebx!=11 || c->esi!=22 || c->edi!=33 || c->ebp!=44)
        win_fail("fixture stdcall/register proof",slot);
}
static uint32_t com(Cpu *c, uint32_t obj, unsigned slot, unsigned n, ...) {
    uint32_t sp=c->esp; va_list ap; va_start(ap,n);
    M32(sp)=site(slot); M32(sp+4)=obj;
    for (unsigned i=0;i<n;++i) M32(sp+8+4*i)=va_arg(ap,uint32_t);
    va_end(ap);
    dispatch(c,M32(M32(obj)+slot*4));
    regs_check(c,sp,n+1,slot);
    uint32_t v=c->eax; c->esp=sp; ++calls; return v;
}
static uint32_t create9(Cpu *c, uint32_t version) {
    uint32_t sp=c->esp; M32(sp)=0x401ffc; M32(sp+4)=version;
    dispatch(c,M32(0x008904a8));
    regs_check(c,sp,1,0);
    uint32_t v=c->eax; c->esp=sp; ++calls; return v;
}
static WinD3D9Snapshot *held[32];
static unsigned nheld;
static int refuse;
static int submit(void *context, WinD3D9Snapshot *s) {
    if (context!=held || refuse || nheld==32) return 0;
    held[nheld++]=s; return 1;
}
static uint32_t fval(float f) { uint32_t v; memcpy(&v,&f,4); return v; }
#define QI 0
#define ADDREF 1
#define RELEASE 2
#define INVALID 0x8876086cu
static const uint8_t iid_d3d9[16]={0xca,0xcb,0xbd,0x81,0xd4,0x64,0x6d,0x42,0xae,0x8d,0xad,0x01,0x47,0xf4,0x27,0x5c};
static const uint8_t iid_device[16]={0x96,0x3b,0x22,0xd0,0x7a,0xbf,0xfd,0x43,0x92,0xbd,0xa4,0x3b,0x0d,0x82,0xb9,0xeb};
static const uint8_t iid_vb[16]={0xb5,0xb1,0x4b,0xb6,0x70,0xfd,0xf6,0x4d,0xbf,0x91,0x19,0xd0,0xa1,0x24,0x55,0xe3};
static const uint8_t iid_resource[16]={0x5d,0xc0,0xee,0x05,0x7d,0x8f,0x62,0x43,0xb9,0x99,0xd1,0xba,0xf3,0x57,0xc7,0x04};
// fe: stream 0 = position, color, texcoord0 (24 bytes).
static const uint16_t decl_fe[]={0,0,2,0, 0,12,4,10, 0,16,1,5, 255,0,17,0};
// fe_mask: texcoord1 comes from stream 1 (8 bytes) at a nonzero stream offset.
static const uint16_t texcoord1[]={1,0,1,5|(1<<8), 255,0,17,0};
static uint16_t decl_mask[12+8];
static uint32_t out(void) { return scratch(4); }
static int same(const WinD3D9Bytes *b, const void *data, uint32_t n) { return b->bytes && b->size==n && !memcmp(b->bytes,data,n); }
int main(int argc,char **argv) {
    const char *mode=argc>1?argv[1]:"good";
    CHECK(win_vm_init());
    CHECK(win_vm_commit(0x10000,0x40000));
    CHECK(win_vm_commit(0x400000,0x2000));
    CHECK(win_vm_commit(WIN_PEB,0x1000));
    Cpu c; win_cpu_init(&c,0x10000,0x20000,0x30000,1);
    c.ebx=11;c.esi=22;c.edi=33;c.ebp=44; c.esp=0x2f000;
    win_cpu=&c; threads_init(); win_import_bind();
    win_first_import=!strcmp(mode,"first");
    if (strcmp(mode,"baseline")) {
        CHECK(win_d3d9_register());
        CHECK(!win_d3d9_register());
        printf("callbacks: %u host registrations\n",win_d3d9_callbacks());
        CHECK(win_d3d9_callbacks()<=WIN_ADAPTER_LIMIT-2);
    }
    CHECK(create9(&c,31)==0);
    uint32_t f=create9(&c,32);
    CHECK(f && win_d3d9_live_objects()==1);
    uint32_t p=out();
    CHECK(com(&c,f,QI,2,put(iid_d3d9,16),p)==0 && M32(p)==f);
    CHECK(com(&c,f,QI,2,put(iid_device,16),p)==0x80004002u && M32(p)==0);
    CHECK(com(&c,f,RELEASE,0)==1 && com(&c,f,ADDREF,0)==2 && com(&c,f,RELEASE,0)==1);
    CHECK(com(&c,f,4,0)==1);
    uint32_t caps=scratch(312);
    memset(G+caps,0xa5,312);
    CHECK(com(&c,f,14,3,0,1,caps+4)==0);
    CHECK(M32(caps)==0xa5a5a5a5 && M32(caps+308)==0xa5a5a5a5);
    CHECK(M32(caps+4+196)==0xfffe0101u && M32(caps+4+204)==0xffff0101u && M32(caps+4+188)==16);
    CHECK(com(&c,f,10,6,0,1,22,0,3,21)==0 && com(&c,f,10,6,0,1,22,0,3,0x31545844)==0x8876086au);
    uint32_t pp=scratch(56);
    M32(pp)=640; M32(pp+4)=480; M32(pp+8)=22; M32(pp+12)=1; M32(pp+24)=1; M32(pp+32)=1;
    M32(pp+36)=1; M32(pp+40)=75;
    uint32_t pd=out();
    CHECK(com(&c,f,16,6,0,1,0x1234,0x40,pp,pd)==0);
    uint32_t d=M32(pd);
    CHECK(d && d!=f && com(&c,f,ADDREF,0)==3 && com(&c,f,RELEASE,0)==2);
    CHECK(com(&c,d,6,1,p)==0 && M32(p)==f && com(&c,f,RELEASE,0)==2);
    CHECK(com(&c,d,QI,2,put(iid_device,16),p)==0 && M32(p)==d && com(&c,d,RELEASE,0)==1);
    CHECK(com(&c,d,58,2,7,p)==0 && M32(p)==1);
    CHECK(com(&c,d,58,2,22,p)==0 && M32(p)==3);
    CHECK(com(&c,d,66,3,0,1,p)==0 && M32(p)==4);
    CHECK(com(&c,d,66,3,1,1,p)==0 && M32(p)==1);
    if (!strcmp(mode,"arena")) {
        // Fill the table, then fail 16 MiB creations: no guest arena may be used.
        uint32_t q=out(), made=0;
        while (com(&c,d,26,6,16,0,0,1,q,0)==0) ++made;
        CHECK(made>200 && M32(q)==0);
        uint32_t before=win_alloc(1);
        for (unsigned i=0;i<80;++i) {
            CHECK(com(&c,d,26,6,WD_MAX_BYTES,0,0,1,q,0)==WD_OOM);
            CHECK(com(&c,d,23,8,2048,2048,1,0,21,1,q,0)==WD_OOM);
        }
        uint32_t after=win_alloc(1);
        CHECK(before && after==before+0x1000);
        puts("ARENA PASS"); return 0;
    }
    if (!strcmp(mode,"slot")) { com(&c,d,17,4,0,0,0,0); return 1; }
    if (!strcmp(mode,"beyond")) { com(&c,d,57,2,28,1); return 1; }
    // Shader and declaration objects; the bytes are the pinned fixtures.
    uint32_t pv=out(),pps=out(),mv=out(),mps=out(),dfe=out(),dmask=out();
    uint32_t bad=put(fe_vs,sizeof fe_vs); M32(bad)=0xfffe0200u;
    CHECK(com(&c,d,91,2,bad,p)==INVALID && M32(p)==0);
    bad=put(fe_ps,sizeof fe_ps-4);
    CHECK(com(&c,d,106,2,bad,p)==INVALID);
    CHECK(com(&c,d,91,2,put(fe_vs,sizeof fe_vs),pv)==0 && M32(pv));
    CHECK(com(&c,d,106,2,put(fe_ps,sizeof fe_ps),pps)==0);
    CHECK(com(&c,d,91,2,put(mask_vs,sizeof mask_vs),mv)==0);
    CHECK(com(&c,d,106,2,put(mask_ps,sizeof mask_ps),mps)==0);
    uint32_t n=out(), copy=scratch(sizeof mask_ps);
    CHECK(com(&c,M32(mps),4,2,0,n)==0 && M32(n)==sizeof mask_ps);
    CHECK(com(&c,M32(mps),4,2,copy,n)==0 && !memcmp(G+copy,mask_ps,sizeof mask_ps));
    memcpy(decl_mask,decl_fe,24); memcpy(decl_mask+12,texcoord1,16);
    CHECK(com(&c,d,86,2,put(decl_fe,sizeof decl_fe),dfe)==0);
    CHECK(com(&c,d,86,2,put(decl_mask,sizeof decl_mask),dmask)==0);
    CHECK(com(&c,M32(dmask),4,2,0,n)==0 && M32(n)==5);
    uint16_t bad_decl[]={0,0,2,0, 255,0,0,0};
    CHECK(com(&c,d,86,2,put(bad_decl,sizeof bad_decl),p)==INVALID);
    // Odd-pitch L8 texture: 3x2, partial and invalid lock rectangles.
    uint32_t pt=out(), lr=scratch(8), rect=scratch(16);
    CHECK(com(&c,d,23,8,3,2,1,0,50,1,pt,0)==0);
    uint32_t t=M32(pt);
    M32(rect)=1; M32(rect+4)=1; M32(rect+8)=3; M32(rect+12)=2;
    CHECK(com(&c,t,19,4,0,lr,rect,0)==0 && M32(lr)==3);
    uint32_t bits=M32(lr+4)-4;
    CHECK(com(&c,t,19,4,0,lr,rect,0)==INVALID);
    M8(bits+4)=0x77; M8(bits+5)=0x88;
    CHECK(com(&c,t,20,1,0)==0);
    M32(rect+8)=4;
    CHECK(com(&c,t,19,4,0,lr,rect,0)==INVALID);
    CHECK(com(&c,t,19,4,0,lr,0,0)==0 && M32(lr+4)==bits);
    for (unsigned i=0;i<4;++i) M8(bits+i)=(uint8_t)(0x10+i);
    CHECK(com(&c,t,20,1,0)==0);
    uint32_t desc=scratch(32);
    CHECK(com(&c,t,17,2,0,desc)==0 && M32(desc)==50 && M32(desc+24)==3 && M32(desc+28)==2);
    // Vertex and index buffers with partial and invalid locks.
    uint32_t pvb=out(), pvb1=out(), pib=out(), pib32=out(), lp=out();
    CHECK(com(&c,d,26,6,8*24,0x208,0,1,pvb,0)==0);
    uint32_t vb=M32(pvb);
    CHECK(com(&c,vb,11,4,8*24,0,lp,0)==INVALID);
    CHECK(com(&c,vb,11,4,24,8*24,lp,0)==INVALID);
    CHECK(com(&c,vb,QI,2,put(iid_resource,16),p)==0 && M32(p)==vb && com(&c,vb,RELEASE,0)==1);
    CHECK(com(&c,vb,QI,2,put(iid_vb,16),p)==0 && com(&c,vb,RELEASE,0)==1);
    CHECK(com(&c,vb,11,4,24,48,lp,0)==0);
    uint32_t vdata=M32(lp)-24;
    CHECK(com(&c,vb,11,4,0,0,lp,0)==INVALID);
    CHECK(com(&c,vb,12,0)==0);
    CHECK(com(&c,vb,11,4,0,0,lp,0)==0 && M32(lp)==vdata);
    for (unsigned i=0;i<8*24;++i) M8(vdata+i)=(uint8_t)i;
    CHECK(com(&c,vb,12,0)==0);
    CHECK(com(&c,d,26,6,64,0,0,1,pvb1,0)==0);
    uint32_t vb1=M32(pvb1);
    CHECK(com(&c,vb1,11,4,0,0,lp,0)==0);
    uint32_t v1data=M32(lp);
    for (unsigned i=0;i<64;++i) M8(v1data+i)=(uint8_t)(0xc0+i);
    CHECK(com(&c,vb1,12,0)==0);
    CHECK(com(&c,d,27,6,12,0,101,1,pib,0)==0);
    uint32_t ib=M32(pib);
    CHECK(com(&c,d,27,6,10,0,102,1,p,0)==INVALID);
    CHECK(com(&c,ib,11,4,0,0,lp,0)==0);
    static const uint16_t idx16[]={0,1,2,2,1,3};
    memcpy(G+M32(lp),idx16,12);
    CHECK(com(&c,ib,12,0)==0);
    CHECK(com(&c,d,27,6,24,0,102,1,pib32,0)==0);
    uint32_t ib32=M32(pib32);
    CHECK(com(&c,ib32,11,4,0,0,lp,0)==0);
    static const uint32_t idx32[]={5,4,3,3,4,2};
    memcpy(G+M32(lp),idx32,24);
    CHECK(com(&c,ib32,12,0)==0);
    // Ordinary front-end draw state.
    CHECK(com(&c,d,87,1,M32(dfe))==0 && com(&c,d,92,1,M32(pv))==0 && com(&c,d,107,1,M32(pps))==0);
    CHECK(com(&c,d,100,4,0,vb,0,24)==0 && com(&c,d,104,1,ib)==0 && com(&c,d,65,2,0,t)==0);
    CHECK(com(&c,d,100,4,17,vb,0,24)==INVALID && com(&c,d,100,4,1,vb,8*24,8)==INVALID);
    CHECK(com(&c,d,57,2,27,1)==0 && com(&c,d,57,2,19,5)==0 && com(&c,d,57,2,20,6)==0);
    CHECK(com(&c,d,69,3,0,5,2)==0 && com(&c,d,69,3,0,14,1)==INVALID);
    float m[5][4]={{1,0,0,0},{0,1,0,0},{0,0,1,0},{0,0,0,1},{0.5f,0.25f,0,0}};
    uint32_t mp=put(m,sizeof m);
    CHECK(com(&c,d,94,3,0,mp,5)==0 && com(&c,d,94,3,250,mp,7)==INVALID);
    uint32_t vp=scratch(24);
    M32(vp+8)=640; M32(vp+12)=480; M32(vp+20)=fval(1);
    CHECK(com(&c,d,47,1,vp)==0);
    M32(rect)=8; M32(rect+4)=4; M32(rect+8)=600; M32(rect+12)=400;
    CHECK(com(&c,d,75,1,rect)==0);
    CHECK(com(&c,d,82,6,4,1,0,4,0,2)==INVALID);
    CHECK(com(&c,d,41,0)==0 && com(&c,d,41,0)==INVALID);
    if (!strcmp(mode,"nobackend")) { com(&c,d,82,6,4,1,0,4,0,2); return 1; }
    win_d3d9_set_backend(submit,held);
    CHECK(com(&c,d,82,6,1,0,0,4,0,6)==INVALID && nheld==0);
    if (!strcmp(mode,"nops")) {
        com(&c,d,107,1,0); com(&c,d,82,6,4,1,0,4,0,2);
        puts("fixed-function draw returned"); return 1;
    }
    // Indexed, 16-bit, base vertex 1.
    CHECK(com(&c,d,82,6,4,1,0,4,0,2)==0 && nheld==1 && win_d3d9_pending()==1);
    WinD3D9Snapshot *s=held[0];
    uint8_t expect_vb[8*24]; memcpy(expect_vb,G+vdata,sizeof expect_vb);
    CHECK(s->indexed==1 && !s->up && s->topology==4 && s->primitives==2 && s->base_vertex==1);
    CHECK(s->min_vertex==0 && s->num_vertices==4 && s->index_format==101);
    CHECK(same(&s->indices,idx16,12) && s->indices.offset==0 && s->indices.id);
    CHECK(s->stride[0]==24 && s->vertices[0].offset==24 && same(&s->vertices[0],expect_vb+24,4*24));
    CHECK(!s->vertices[1].bytes && same(&s->vs,fe_vs,sizeof fe_vs) && same(&s->ps,fe_ps,sizeof fe_ps));
    CHECK(same(&s->declaration,decl_fe,sizeof decl_fe));
    CHECK(s->textures[0].pitch==3 && s->textures[0].format==50 && s->textures[0].version==3);
    CHECK(s->textures[0].bytes[0]==0x10 && s->textures[0].bytes[4]==0x77 && s->textures[0].bytes[5]==0x88);
    CHECK(s->vertices[0].version==3 && s->state.rs[27]==1 && s->state.rs[19]==5 && s->state.rs[20]==6);
    CHECK(s->state.sampler[0][5]==2 && s->state.vs_constants[4][1]==0.25f && s->state.vs_version==1);
    CHECK(s->state.viewport[2]==640 && s->state.scissor[0]==8 && s->state.scissor[3]==400);
    uint32_t vb_id=s->vertices[0].id, vb_gen=s->vertices[0].generation;
    // Invalid ranges are rejected and record nothing.
    CHECK(com(&c,d,82,6,4,1,0,3,0,2)==INVALID);
    CHECK(com(&c,d,82,6,4,0,0,4,4,2)==INVALID);
    CHECK(com(&c,d,82,6,4,5,0,4,0,2)==INVALID);
    CHECK(com(&c,d,82,6,4,(uint32_t)-1,0,4,0,2)==INVALID);
    CHECK(com(&c,d,81,3,4,7,1)==INVALID && nheld==1);
    // Non-indexed draw from start vertex 2.
    CHECK(com(&c,d,81,3,5,2,2)==0 && nheld==2 && held[1]->vertices[0].offset==48);
    CHECK(held[1]->num_vertices==4 && held[1]->vertices[0].size==4*24 && !held[1]->indices.bytes);
    // Masked front-end draw: 32-bit indices, base vertex 2, two streams.
    CHECK(com(&c,d,87,1,M32(dmask))==0 && com(&c,d,92,1,M32(mv))==0 && com(&c,d,107,1,M32(mps))==0);
    CHECK(com(&c,d,100,4,1,vb1,8,8)==0 && com(&c,d,104,1,ib32)==0);
    CHECK(com(&c,d,82,6,4,2,2,4,0,2)==INVALID);
    CHECK(com(&c,d,82,6,4,(uint32_t)-2,2,4,0,2)==0 && nheld==3);
    s=held[2];
    CHECK(s->index_format==102 && same(&s->indices,idx32,24) && s->base_vertex==-2);
    CHECK(s->vertices[0].offset==0 && same(&s->vertices[0],expect_vb,4*24));
    CHECK(s->stride[1]==8 && s->stream_offset[1]==8 && s->vertices[1].offset==8);
    CHECK(same(&s->vertices[1],G+v1data+8,4*8) && same(&s->vs,mask_vs,sizeof mask_vs));
    CHECK(same(&s->ps,mask_ps,sizeof mask_ps) && same(&s->declaration,decl_mask,sizeof decl_mask));
    // Later locks, constants, rebinding and releases must not touch held draws.
    CHECK(com(&c,vb,11,4,0,0,lp,0)==0);
    memset(G+vdata,0xee,8*24);
    CHECK(com(&c,vb,12,0)==0 && com(&c,vb,11,4,0,0,lp,0x10)==INVALID);
    CHECK(com(&c,ib,11,4,0,0,lp,0x10)==0 && com(&c,ib,12,0)==0);
    CHECK(com(&c,d,94,3,4,mp,1)==0 && com(&c,d,65,2,0,0)==0);
    CHECK(same(&held[0]->vertices[0],expect_vb+24,4*24) && held[0]->vertices[0].version==3);
    CHECK(held[0]->state.vs_constants[4][0]==0.5f && held[0]->textures[0].id);
    CHECK(held[0]->vertices[0].id==vb_id && held[0]->vertices[0].generation==vb_gen);
    // Bound-resource lifetime: a released but bound buffer stays recordable.
    unsigned live=win_d3d9_live_objects();
    CHECK(com(&c,vb1,RELEASE,0)==0 && win_d3d9_live_objects()==live);
    CHECK(com(&c,vb1,ADDREF,0)==0 && com(&c,vb1,12,0)==INVALID);
    CHECK(com(&c,d,82,6,4,(uint32_t)-2,2,4,0,2)==0 && nheld==4 && held[3]->vertices[1].id);
    uint32_t a=out(),b=out(),e=out();
    CHECK(com(&c,d,101,4,1,a,b,e)==0 && M32(a)==vb1 && M32(b)==8 && M32(e)==8);
    CHECK(com(&c,vb1,RELEASE,0)==0 && win_d3d9_live_objects()==live);
    CHECK(com(&c,d,100,4,1,0,0,0)==0 && win_d3d9_live_objects()==live-1);
    CHECK(com(&c,d,100,4,1,vb1,0,8)==INVALID);
    // A refusing backend owns nothing and the draw reports NOTAVAILABLE.
    CHECK(com(&c,d,87,1,M32(dfe))==0 && com(&c,d,92,1,M32(pv))==0 && com(&c,d,107,1,M32(pps))==0);
    refuse=1; CHECK(com(&c,d,81,3,5,0,2)==0x8876086au && win_d3d9_pending()==4); refuse=0;
    // DrawPrimitiveUP unbinds stream 0; DrawIndexedPrimitiveUP also indices.
    uint8_t up[4*24]; for (unsigned i=0;i<sizeof up;++i) up[i]=(uint8_t)(0x40+i);
    uint32_t upv=put(up,sizeof up), upi=put(idx16,12);
    CHECK(com(&c,d,83,4,5,2,upv,24)==0 && nheld==5 && held[4]->up && same(&held[4]->vertices[0],up,sizeof up));
    CHECK(com(&c,d,101,4,0,a,b,e)==0 && M32(a)==0 && M32(b)==0 && M32(e)==0);
    CHECK(com(&c,d,81,3,5,0,2)==INVALID);
    CHECK(com(&c,d,100,4,0,vb,0,24)==0 && com(&c,d,104,1,ib)==0);
    CHECK(com(&c,d,84,8,4,0,4,2,upi,101,upv,24)==0 && nheld==6);
    s=held[5];
    CHECK(s->up && s->indexed && s->index_format==101 && same(&s->indices,idx16,12) && same(&s->vertices[0],up,sizeof up));
    CHECK(com(&c,d,101,4,0,a,b,e)==0 && M32(a)==0 && com(&c,d,105,1,a)==0 && M32(a)==0);
    CHECK(com(&c,d,84,8,4,0,4,2,upi,101,0,24)==INVALID);
    CHECK(com(&c,d,42,0)==0 && com(&c,d,42,0)==INVALID);
    // Teardown: release everything; completion frees every owned snapshot.
    uint32_t objects[]={M32(pv),M32(pps),M32(mv),M32(mps),M32(dfe),M32(dmask),t,vb,ib,ib32};
    for (unsigned i=0;i<sizeof objects/4;++i) CHECK(com(&c,objects[i],RELEASE,0)==0);
    CHECK(win_d3d9_live_objects()>2);
    CHECK(com(&c,d,RELEASE,0)==0 && win_d3d9_live_objects()==1);
    CHECK(win_d3d9_pending()==6 && same(&held[2]->vs,mask_vs,sizeof mask_vs));
    CHECK(com(&c,f,RELEASE,0)==0 && win_d3d9_live_objects()==0);
    for (unsigned i=0;i<nheld;++i) win_d3d9_complete(held[i]);
    CHECK(win_d3d9_pending()==0);
    gil_release();win_cpu=NULL;win_vm_destroy();
    printf("D3D9 FIXTURE PASS: %u guest calls, %u snapshots, 0 retained objects\n",calls,nheld);
    return 0;
}
'''


def run(argv, code=0):
    p = subprocess.run(list(map(str, argv)), capture_output=True, text=True, timeout=120)
    assert p.returncode == code, (f'{argv[0]} exit {p.returncode}, expected {code}: '
                                  f'{p.stdout[-2000:]} {p.stderr[-2000:]}')
    return p.stdout


def shaders_inc():
    lines = []
    for name, digest in FIXTURES.items():
        data = (SHADERS / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == digest, name
        var = ('mask_' if name.startswith('fe_mask') else 'fe_') + name.split('_')[-3]
        lines.append(f'static const uint8_t {var}[{len(data)}]={{{",".join(map(str, data))}}};')
    return '\n'.join(lines) + '\n'


def coff_audit(compiler, folder):
    """SDK-free Windows COFF closure, including hosted compiler libc lowering."""
    import importlib.util
    import json
    spec = importlib.util.spec_from_file_location('windows_symbols', ROOT / 'recomp/windows_symbols.py')
    symbols = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(symbols)
    stub = folder / 'generated.c'
    # Stands in for the generated units the host layer links against.
    stub.write_text('#include "host.h"\nconst unsigned guest_func_count=0;const uint32_t speed_entry=0,'
                    'guest_func_addr[1];void (*const guest_func_ptr[1])(Cpu *);\n')
    sources = sorted(p for p in WIN.glob('*.c') if not p.name.startswith('.'))
    sources += [ROOT / 'host/audio/dsound_host.cpp', WIN / 'audio_output.cpp']
    assert {'d3d9.c', 'd3d9_state.c'} <= {p.name for p in sources}
    # Keep the O1 proof, and cover the desktop build's O2 policy as well:
    # some clang versions only fold malloc + memset into calloc at O2.
    for mode, flags in (('freestanding', ['-O1', '-ffreestanding']),
                        ('hosted-O1', ['-O1']), ('hosted-O2', ['-O2'])):
        output = folder / mode
        output.mkdir()
        objects = []
        for source in [*sources, ROOT / 'runtime/imports.c', stub]:
            obj = output / f'coff_{source.parent.name}_{source.stem}.obj'
            language = ['-std=c11']
            if source.suffix == '.cpp':
                language = ['-std=c++17', '-fno-exceptions', '-fno-rtti',
                            '-DNFS_WIN_AUDIO', '-I', WIN / 'audio_compile']
            run([compiler, '-target', 'x86_64-pc-windows-msvc', *language, *flags,
                 '-DRECOMP_WINDOWS_COMPILE_ONLY', '-I', WIN, '-I', ROOT / 'recomp',
                 '-c', source, '-o', obj])
            objects.append(obj)
        print(f'COFF {mode}:')
        if mode == 'freestanding':
            # The host's C DirectSound adapters require both shared C++ units.
            audio_partial = [o for o in objects[:-1]
                             if o.stem not in {'coff_audio_dsound_host', 'coff_win_audio_output'}]
            assert len(audio_partial) == len(objects) - 3
            assert not symbols.audit(compiler, [objects[-1]], audio_partial, output, sdk_headers=False)
            missing = json.loads((output / 'symbol-closure.json').read_text())
            assert len(missing['unknown']) == 31, missing
            print('shared audio C++ units removed: UNKNOWN=31, rejected')
            # Broken control: without the state unit the recorder's wd_* calls are unknown.
            partial = [o for o in objects[:-1] if o.stem != 'coff_win_d3d9_state']
            assert len(partial) == len(objects) - 2
            assert not symbols.audit(compiler, [objects[-1]], partial, output, sdk_headers=False)
        if not symbols.audit(compiler, [objects[-1]], objects[:-1], output, sdk_headers=False):
            raise AssertionError(f'unknown symbols in the {mode} Windows host layer')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--desktop', action='store_true')
    args = parser.parse_args()
    if args.desktop and os.name != 'nt':
        parser.error('--desktop requires native Windows SDK/CRT')
    compiler = shutil.which('clang')
    if not compiler:
        print('SKIP: no C compiler')
        return 3
    if not all((SHADERS / name).is_file() for name in FIXTURES):
        print('SKIP: shader fixtures absent')
        return 3
    assert hashlib.sha256(ORIGINAL_IMPORT.encode()).hexdigest() == ORIGINAL_IMPORT_SHA
    with tempfile.TemporaryDirectory(prefix='windows-d3d9-') as scratch:
        folder = Path(scratch)
        (folder/'shaders.inc').write_text(shaders_inc())
        base = [compiler, '-std=c11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                '-I', WIN, '-I', ROOT/'recomp', '-I', folder]
        if os.name == 'nt':
            base += ['-fms-runtime-lib=static']
        else:
            base += ['-fsanitize=address,undefined', '-DNFS_WIN_TEST', '-pthread']
            fake = (WIN/'fixture.h').read_text().split('const struct ImportSlot speed_imports[]')[0]
            assert 'void *VirtualAlloc' in fake and 'int main' not in fake
            (folder/'fake_win.inc').write_text(fake)
        units = [WIN/n for n in ('portable.c', 'memory.c', 'loader.c', 'core.c', 'kernel.c',
                                  'imports.c', 'startup.c', 'threads.c', 'sync.c', 'd3d9.c', 'd3d9_state.c')]
        source = folder/'fixture.c'
        source.write_text(FIXTURE)
        suffix = '.exe' if os.name == 'nt' else ''

        def build(label, replace=None):
            target = units
            if replace:
                name, old, new = replace
                text = (WIN/name).read_text()
                assert text.count(old) == 1, (label, old)
                mutant = folder/(label+'.c')
                mutant.write_text(text.replace(old, new))
                target = [mutant if p.name == name else p for p in units]
            exe = folder/(label+suffix)
            run([*base, *target, source, '-o', exe])
            return exe

        fixed = build('fixed')
        output = run([fixed])
        assert 'D3D9 FIXTURE PASS:' in output, output
        print('fixed: exit 0; ' + ' '.join(output.split()))
        for mode, code, expected in (
                ('first', 10, 'first import d3d9.dll!Direct3DCreate9'),
                ('slot', 2, 'unsupported IDirect3DDevice9 slot 17'),
                ('beyond', 2, 'outside the recorded subset: render state'),
                ('nobackend', 2, 'draw without a backend'),
                ('nops', 2, 'outside the recorded subset: fixed-function draw'),
                ('arena', 0, 'ARENA PASS')):
            output = run([fixed, mode], code)
            assert expected in output, (mode, output)
        print('controls: first=10; unsupported slot, outside-subset state, no backend, '
              'fixed-function draw=2; full-table arena=0')
        # Pinned original dispatch with no D3D9 registration: the unsupported stop.
        current = (WIN/'imports.c').read_text()
        assert current.count('void host_import(') == 1
        original = folder/'original.c'
        original.write_text(current[:current.index('void host_import(')] + ORIGINAL_IMPORT)
        old_units = [original if p.name == 'imports.c' else p for p in units]
        exe = folder/('original'+suffix)
        run([*base, *old_units, source, '-o', exe])
        assert 'unsupported import d3d9.dll!Direct3DCreate9' in run([exe, 'baseline'], 2)
        print('pinned original: exit 2 at d3d9.dll!Direct3DCreate9; fixed exit 0')
        for label, code, change, expected, *mode in (
                ('wrong-slot', 2, ('d3d9.c', 'D(82,"DrawIndexedPrimitive"', 'D(80,"DrawIndexedPrimitive"'),
                 'unsupported IDirect3DDevice9 slot 82'),
                ('up-unbind', 1, ('d3d9.c', '    if (!result) { wd_bind(&d->streams[0].resource,0);',
                                  '    if (0) { wd_bind(&d->streams[0].resource,0);'), 'failed line'),
                ('iup-unbind', 1, ('d3d9.c', '        wd_bind(&d->indices,0);\n', '\n'), 'failed line'),
                ('bind-lifetime', 1, ('d3d9_state.c', '    if (o) ++o->binds;\n', '\n'), 'failed line'),
                # Pinned pre-review lines; each must fail the same fixture.
                ('old-readonly', 1, ('d3d9.c', '        ((flags&0x10) && (o->usage&0x8)) ||\n', ''), 'failed line'),
                ('old-pointlist', 1, ('d3d9_state.c', ' || (indexed && type==1)) return WD_INVALID;',
                                      ') return WD_INVALID;'), 'failed line'),
                ('old-nops', 1, ('d3d9_state.c', '    if (!d->vs || !d->ps) win_fail(',
                                 '    if (!d->vs || !d->ps) return WD_INVALID; if (0) win_fail('), 'draw returned', 'nops'),
                ('old-arena', 1, ('d3d9.c', OLD_BUFFER_NEW[0], OLD_BUFFER_NEW[1]), 'failed line', 'arena')):
            exe = build(label, change)
            output = run([exe, *mode], code)
            assert expected in output, (label, output)
            print(f'{label} mutant: exit {code}')
        if os.name != 'nt':
            coff_audit(compiler, folder)
        else:
            print('DESKTOP D3D9 CHECK PASS: synthetic guest only')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
