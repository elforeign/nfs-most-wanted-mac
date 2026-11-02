// Synthetic CRT-prefix fixture; compiled by windows_boot_test.py on either host.
#include "startup.h"
#ifdef NFS_WIN_TEST
#include "fake_win.inc"
#endif
#ifndef CHECK
#define CHECK(x) do { if (!(x)) { printf("failed line %d: %s\n",__LINE__,#x); return 1; } } while(0)
#endif
const struct ImportSlot speed_imports[]={
    {0x0089008c,"KERNEL32.dll!GetModuleHandleA"},
    {0x00890094,"KERNEL32.dll!GetProcAddress"},
    {0x008900e0,"KERNEL32.dll!GetVersionExA"},
    {0x00890188,"KERNEL32.dll!GetLastError"},
    {0x008901a4,"KERNEL32.dll!GetCommandLineA"},
    {0x008901b8,"KERNEL32.dll!EnterCriticalSection"},
    {0x008901bc,"KERNEL32.dll!LeaveCriticalSection"},
    {0x008901e8,"KERNEL32.dll!GetEnvironmentStringsW"},
    {0x008901ec,"KERNEL32.dll!SetHandleCount"},
    {0x008901f0,"KERNEL32.dll!GetStdHandle"},
    {0x008901f4,"KERNEL32.dll!GetFileType"},
    {0x008901f8,"KERNEL32.dll!GetStartupInfoA"},
    {0x00890200,"KERNEL32.dll!TlsAlloc"},
    {0x00890204,"KERNEL32.dll!GetCurrentThreadId"},
    {0x0089020c,"KERNEL32.dll!TlsSetValue"},
    {0x00890210,"KERNEL32.dll!TlsGetValue"},
    {0x00890214,"KERNEL32.dll!SetLastError"},
    {0x0089021c,"KERNEL32.dll!HeapCreate"},
    {0x00890238,"KERNEL32.dll!HeapAlloc"},
    {0x00890260,"KERNEL32.dll!InitializeCriticalSection"},
};
const unsigned speed_import_count=sizeof speed_imports/sizeof *speed_imports,guest_func_count=0;
const uint32_t speed_entry=0x007c4040,guest_func_addr[]={0};
void (*const guest_func_ptr[])(Cpu *)={NULL};
static unsigned calls;
static uint32_t call(Cpu *c,uint32_t slot,unsigned n,uint32_t a,uint32_t b,uint32_t d) {
    uint32_t sp=c->esp;
    M32(sp)=0x11223344; M32(sp+4)=a; M32(sp+8)=b; M32(sp+12)=d;
    uint32_t ebx=c->ebx,esi=c->esi,edi=c->edi,ebp=c->ebp;
    host_import(c,slot);
    if (c->esp!=sp+4+4*n || c->ebx!=ebx || c->esi!=esi || c->edi!=edi || c->ebp!=ebp)
        win_fail("fixture stdcall/register proof",slot);
    uint32_t value=c->eax;
    ++calls; c->esp=sp; return value;
}
static uint32_t str_guest(uint32_t p,const char *s) {
    unsigned i=0; do { M8(p+i)=(uint8_t)s[i]; } while (s[i++]); return p;
}
static uint32_t echo(Cpu *c) { return win_arg(c,0)^0xabc123u; }
static unsigned waiter_started,waiter_acquired;
static DWORD lock_waiter(void *arg) {
    Cpu *c=arg;
    win_cpu=c; gil_acquire();
    waiter_started=1;
    call(c,0x008901b8,1,0x43000,0,0);
    if (M32(0x43008)!=1 || M32(0x4300c)!=2) win_fail("waiter ownership",0);
    waiter_acquired=1;
    call(c,0x008901bc,1,0x43000,0,0);
    gil_release();win_cpu=NULL;return 0;
}
int main(int argc,char **argv) {
    const char *mode=argc>1?argv[1]:"good";
    CHECK(win_vm_init());
    CHECK(win_vm_commit(0x10000,0x40000));
    CHECK(win_vm_commit(0x400000,0x1000));
    CHECK(win_vm_commit(WIN_PEB,0x1000));
    Cpu c,other;
    win_cpu_init(&c,0x10000,0x20000,0x30000,1);
    win_cpu_init(&other,0x12000,0x30000,0x40000,2);
    c.ebx=11;c.esi=22;c.edi=33;c.ebp=44;
    win_cpu=&c; threads_init(); win_import_bind();
    CHECK(win_startup_init("C:\\Synthetic Path\\speed.exe"));
    win_first_import=!strcmp(mode,"first");
    if (!strcmp(mode,"heap-limit")) {
        uint32_t h=call(&c,0x0089021c,3,0,0,0x1000);
        CHECK(h && call(&c,0x00890238,3,h,0,0x1000));
        CHECK(!call(&c,0x00890238,3,h,0,0x1000));
        // Maximum is page rounded, and failed requests consume no capacity.
        h=call(&c,0x0089021c,3,0,0,0x1001);
        CHECK(h && !call(&c,0x00890238,3,h,0,0x2001));
        CHECK(call(&c,0x00890238,3,h,0,0x1000));
        CHECK(call(&c,0x00890238,3,h,0,0x1000));
        CHECK(!call(&c,0x00890238,3,h,0,1));
        h=call(&c,0x0089021c,3,0,0,1);
        CHECK(h && call(&c,0x00890238,3,h,0,0));
        CHECK(call(&c,0x00890238,3,h,0,0xff8));
        CHECK(!call(&c,0x00890238,3,h,0,1));
        puts("HEAP LIMIT PASS");return 0;
    }
    if (!strcmp(mode,"heap-error")) {
        uint32_t h=call(&c,0x0089021c,3,0,0,0);
        CHECK(h);
        call(&c,0x00890214,1,0x123,0,0);
        CHECK(!call(&c,0x00890238,3,h,0,UINT32_MAX));
        CHECK(call(&c,0x00890188,0,0,0,0)==0x123);
        puts("HEAP LASTERROR PASS");return 0;
    }
    if (!strcmp(mode,"lock-wait")) {
        call(&c,0x00890260,1,0x43000,0,0);
        call(&c,0x008901b8,1,0x43000,0,0);
        call(&c,0x008901b8,1,0x43000,0,0);
        HANDLE waiter_thread=CreateThread(NULL,0,lock_waiter,&other,0,NULL);
        CHECK(waiter_thread);
        while (!waiter_started) win_guest_sleep(1);
        CHECK(!waiter_acquired && M32(0x43008)==2 && M32(0x4300c)==1);
        call(&c,0x008901bc,1,0x43000,0,0);
        win_guest_sleep(10);
        CHECK(!waiter_acquired && M32(0x43008)==1);
        call(&c,0x008901bc,1,0x43000,0,0);
        gil_release();DWORD result=WaitForSingleObject(waiter_thread,INFINITE);gil_acquire();
        CHECK(result==WAIT_OBJECT_0 && CloseHandle(waiter_thread) && waiter_acquired);
        CHECK(M32(0x43008)==0 && M32(0x4300c)==0);
        puts("LOCK WAIT PASS");return 0;
    }
    if (!strcmp(mode,"unknown")) { host_import(&c,0x008901e8); return 1; }
    if (!strcmp(mode,"bad-slot")) { host_import(&c,0x00890265); return 1; }
    if (!strcmp(mode,"bad-stack")) { c.esp=UINT32_MAX-2; host_import(&c,0x008900e0); return 1; }
    if (!strcmp(mode,"uncommitted-stack")) { c.esp=0x12300000; host_import(&c,0x008900e0); return 1; }
    if (!strcmp(mode,"bad-startup")) { call(&c,0x008901f8,1,UINT32_MAX-32,0,0); return 1; }
    uint32_t v=0x41000;
    memset(G+v-4,0xa5,156); M32(v)=148;
    CHECK(call(&c,0x008900e0,1,v,0,0)==1);
    CHECK(M32(v)==148 && M32(v+4)==5 && M32(v+8)==1 && M32(v+12)==2600 && M32(v+16)==2);
    CHECK(!strcmp((char *)G+v+20,"Service Pack 2") && M8(v+147)==0);
    CHECK(M32(v-4)==0xa5a5a5a5 && M32(v+148)==0xa5a5a5a5);
    CHECK(call(&c,0x008900e0,1,0,0,0)==0 && M32(c.fs_base+0x34)==87);
    CHECK(call(&c,0x008900e0,1,0xffffffc0u,0,0)==0);
    CHECK(call(&c,0x008900e0,1,0x12300000,0,0)==0 && !win_vm_access(0x12300000,148));
    M32(v)=147;CHECK(call(&c,0x008900e0,1,v,0,0)==0 && M32(v)==147);M32(v)=148;
    CHECK(call(&c,0x0089008c,1,0,0,0)==0x400000);
    uint32_t module=call(&c,0x0089008c,1,str_guest(0x42000,"KeRnEl32.DlL"),0,0);
    CHECK(module==0x7c800000);
    CHECK(call(&c,0x0089008c,1,str_guest(0x42000,"other.dll"),0,0)==0 && M32(c.fs_base+0x34)==126);
    uint32_t h=call(&c,0x0089021c,3,0,0x1000,0);
    CHECK(h && h<WIN_HOST_BASE);
    uint32_t block=call(&c,0x00890238,3,h,8,0x8c);
    CHECK(block && !(block&15) && win_vm_access(block,0x8c));
    for (unsigned i=0;i<0x8c;++i) CHECK(!M8(block+i));
    uint32_t next=call(&c,0x00890238,3,h,0,0x480);
    CHECK(next && next!=block && next>=block+0x8c);
    CHECK(call(&c,0x00890238,3,h,0,0)!=0);
    call(&c,0x00890214,1,0x123,0,0);
    CHECK(call(&c,0x00890238,3,h,0,UINT32_MAX)==0 && M32(c.fs_base+0x34)==0x123);
    CHECK(call(&c,0x00890238,3,h,0,0xfffff000)==0);
    CHECK(call(&c,0x00890238,3,h+1,0,16)==0 && M32(c.fs_base+0x34)==0x123);
    CHECK(call(&c,0x00890238,3,h,0x100,16)==0 && M32(c.fs_base+0x34)==0x123);
    CHECK(!win_alloc(UINT32_MAX) && !win_alloc(0xfffff000));
    uint32_t fn=call(&c,0x00890094,2,module,str_guest(0x42000,"InitializeCriticalSectionAndSpinCount"),0);
    CHECK(fn>=WIN_HOST_BASE && fn<WIN_HOST_END && fn<WIN_THUNK_BASE);
    if (!strcmp(mode,"bad-host")) { dispatch(&c,fn+1); return 1; }
    if (!strcmp(mode,"missing-host")) { dispatch(&c,WIN_HOST_END-4); return 1; }
    if (!strcmp(mode,"host-end")) { dispatch(&c,WIN_HOST_END); return 1; }
    if (!strcmp(mode,"incomplete")) {
        win_boot_prefix=1;M32(c.esp)=0x007c414a;host_import(&c,0x008901a4);return 1;
    }
    uint32_t esp=c.esp,lock=0x43000;
    memset(G+lock-4,0xa5,32);
    M32(esp)=0x12345678;M32(esp+4)=lock;M32(esp+8)=4000;
    dispatch(&c,fn);
    CHECK(c.eax==1 && c.esp==esp+12); c.esp=esp;
    CHECK(M32(lock-4)==0xa5a5a5a5 && M32(lock+24)==0xa5a5a5a5);
    CHECK(M32(lock)==0 && M32(lock+4)==UINT32_MAX && M32(lock+8)==0 && M32(lock+12)==0 && M32(lock+16)==0 && M32(lock+20)==4000);
    for (unsigned i=0;i<2;++i) call(&c,0x008901b8,1,lock,0,0);
    CHECK(M32(lock+8)==2 && M32(lock+12)==1);
    for (unsigned i=0;i<2;++i) call(&c,0x008901bc,1,lock,0,0);
    CHECK(M32(lock+4)==UINT32_MAX && M32(lock+8)==0 && M32(lock+12)==0);
    CHECK(call(&c,0x00890094,2,module,str_guest(0x42000,"FlsAlloc"),0)==0 && M32(c.fs_base+0x34)==127);
    CHECK(call(&c,0x00890094,2,module,str_guest(0x42000,"FlsGetValue"),0)==0);
    CHECK(call(&c,0x00890094,2,module,str_guest(0x42000,"FlsSetValue"),0)==0);
    CHECK(call(&c,0x00890094,2,module,str_guest(0x42000,"FlsFree"),0)==0);
    CHECK(call(&c,0x00890094,2,module,0xfffffff0u,0)==0);
    CHECK(call(&c,0x00890094,2,module,str_guest(0x42000,"Unknown"),0)==0 && M32(c.fs_base+0x34)==127);
    CHECK(call(&c,0x00890200,0,0,0,0)==0);
    CHECK(call(&c,0x0089020c,2,0,block,0)==1);
    CHECK(call(&c,0x00890210,1,0,0,0)==block);
    CHECK(call(&other,0x00890210,1,0,0,0)==0);
    CHECK(call(&c,0x00890204,0,0,0,0)==1 && call(&other,0x00890204,0,0,0,0)==2);
    call(&c,0x00890214,1,0x123,0,0);call(&other,0x00890214,1,0x456,0,0);
    CHECK(call(&c,0x00890188,0,0,0,0)==0x123 && call(&other,0x00890188,0,0,0,0)==0x456);
    memset(G+v-4,0xa5,76);call(&c,0x008901f8,1,v,0,0);
    CHECK(M32(v)==68 && M32(v-4)==0xa5a5a5a5 && M32(v+68)==0xa5a5a5a5);
    for (unsigned i=4;i<68;++i) CHECK(!M8(v+i));
    for (unsigned i=0;i<3;++i) {
        uint32_t sh=call(&c,0x008901f0,1,(uint32_t)-10-i,0,0);
        CHECK(sh && sh!=UINT32_MAX && call(&c,0x008901f4,1,sh,0,0)==2);
    }
    CHECK(call(&c,0x008901f0,1,7,0,0)==UINT32_MAX && M32(c.fs_base+0x34)==87);
    CHECK(call(&c,0x008901f4,1,7,0,0)==0 && M32(c.fs_base+0x34)==6);
    CHECK(call(&c,0x008901ec,1,32,0,0)==32);
    uint32_t cmd=call(&c,0x008901a4,0,0,0,0);
    CHECK(cmd && !strcmp((char *)G+cmd,"\"C:\\Synthetic Path\\speed.exe\""));
    CHECK(call(&c,0x008901a4,0,0,0,0)==cmd);
    if (!strcmp(mode,"checkpoint")) {
        win_boot_prefix=1;M32(c.esp)=0x007c414a;host_import(&c,0x008901a4);return 1;
    }
    // A wrong call site may complete the import but must never mark the prefix.
    win_boot_prefix=1;CHECK(call(&c,0x008901a4,0,0,0,0)==cmd);win_boot_prefix=0;
    uint32_t thunk=win_host_register("fixture!echo",echo,1);
    CHECK(thunk && !win_host_register("fixture!echo",echo,1));
    M32(esp)=0;M32(esp+4)=0x1234;dispatch(&c,thunk);
    CHECK(c.eax==(0x1234^0xabc123u) && c.esp==esp+8);c.esp=esp;
    // Exercise a generated IAT thunk through the common dispatch entry point.
    unsigned index=0;while(speed_imports[index].slot!=0x008901a4)++index;
    M32(esp)=0;dispatch(&c,M32(speed_imports[index].slot));CHECK(c.eax==cmd && c.esp==esp+4);c.esp=esp;
    CHECK(!win_import_register(NULL,echo,0) && !win_import_register("bad",NULL,0));
    CHECK(!win_host_register("bad",echo,33) && !win_host_register("",echo,0));
    char longname[129];memset(longname,'x',128);longname[128]=0;
    CHECK(!win_import_register(longname,echo,0));
    unsigned ni=0,nh=0;
    char name[4]={'x','a','a',0};
    for (unsigned i=0;i<WIN_ADAPTER_LIMIT;++i) {
        name[1]=(char)('A'+i/26);name[2]=(char)('A'+i%26);
        ni+=win_import_register(name,echo,0)!=0;nh+=win_host_register(name,echo,0)!=0;
    }
    // CRT prefix: 19 imports; event/wait slice: 6 imports.
    CHECK(ni==WIN_ADAPTER_LIMIT-25 && nh==WIN_ADAPTER_LIMIT-2);
    CHECK(!win_import_register("full",echo,0) && !win_host_register("full",echo,0));
    gil_release();win_cpu=NULL;win_vm_destroy();
    printf("BOOT FIXTURE PASS: %u import calls, guest layouts, eax/esp, guards and bounded dispatch\n",calls);
    return 0;
}
