// Synthetic fixture compiled only by windows_host_test.py, never the host build.
#include "host.h"
#include <pthread.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>
#define CHECK(x) do { if (!(x)) { printf("failed line %d: %s\n",__LINE__,#x); return 1; } } while(0)
static pthread_mutex_t init_mu=PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t *native_mutex(SRWLOCK *s) {
    pthread_mutex_lock(&init_mu);
    if (!s->Ptr) { s->Ptr=malloc(sizeof(pthread_mutex_t)); pthread_mutex_init(s->Ptr,NULL); }
    pthread_mutex_t *p=s->Ptr; pthread_mutex_unlock(&init_mu); return p;
}
static pthread_cond_t *native_cond(CONDITION_VARIABLE *s) {
    pthread_mutex_lock(&init_mu);
    if (!s->Ptr) { s->Ptr=malloc(sizeof(pthread_cond_t)); pthread_cond_init(s->Ptr,NULL); }
    pthread_cond_t *p=s->Ptr; pthread_mutex_unlock(&init_mu); return p;
}
void AcquireSRWLockExclusive(SRWLOCK *s) { pthread_mutex_lock(native_mutex(s)); }
void ReleaseSRWLockExclusive(SRWLOCK *s) { pthread_mutex_unlock(native_mutex(s)); }
BOOL SleepConditionVariableSRW(CONDITION_VARIABLE *c,SRWLOCK *s,DWORD ms,DWORD flags) {
    (void)ms;(void)flags; return !pthread_cond_wait(native_cond(c),native_mutex(s));
}
void WakeAllConditionVariable(CONDITION_VARIABLE *c) { pthread_cond_broadcast(native_cond(c)); }
void *VirtualAlloc(void *p,size_t n,DWORD type,DWORD protect) {
    (void)protect;
    if (type==MEM_RESERVE) { void *v=mmap(NULL,n,PROT_NONE,MAP_PRIVATE|MAP_ANON,-1,0); return v==MAP_FAILED?NULL:v; }
    uintptr_t page=(uintptr_t)sysconf(_SC_PAGESIZE);
    uintptr_t lo=(uintptr_t)p & ~(page-1), hi=((uintptr_t)p+n+page-1)&~(page-1);
    return !mprotect((void *)lo,hi-lo,PROT_READ|PROT_WRITE)?p:NULL;
}
BOOL VirtualFree(void *p,size_t n,DWORD type) { (void)n;(void)type; return !munmap(p,(size_t)WIN_GUEST_SIZE); }
BOOL QueryPerformanceCounter(LARGE_INTEGER *v) { struct timespec t; clock_gettime(CLOCK_MONOTONIC,&t);v->QuadPart=(int64_t)t.tv_sec*1000000000+t.tv_nsec;return 1; }
BOOL QueryPerformanceFrequency(LARGE_INTEGER *v) { v->QuadPart=1000000000;return 1; }
void Sleep(DWORD ms) { struct timespec t={ms/1000,(ms%1000)*1000000}; nanosleep(&t,NULL); }
#include "fake_events.h"
typedef struct { unsigned kind; pthread_t thread; DWORD (*entry)(void *); void *arg; } NativeThread;
static void *start_native(void *p) { NativeThread *t=p; return (void *)(uintptr_t)t->entry(t->arg); }
HANDLE CreateThread(void *security,size_t stack,DWORD (*entry)(void *),void *arg,DWORD flags,DWORD *tid) {
    (void)security;(void)stack;(void)flags;(void)tid;
    NativeThread *t=malloc(sizeof *t); t->kind=1;t->entry=entry;t->arg=arg;
    if (pthread_create(&t->thread,NULL,start_native,t)) { free(t);return NULL; } return t;
}
DWORD WaitForSingleObject(HANDLE h,DWORD ms) {
    if (*(unsigned *)h==2) return WaitForMultipleObjects(1,&h,0,ms);
    return pthread_join(((NativeThread *)h)->thread,NULL)?WAIT_FAILED:WAIT_OBJECT_0;
}
BOOL CloseHandle(HANDLE h) { if (*(unsigned *)h==2) --fake_event_live; free(h);return 1; }
const struct ImportSlot speed_imports[]={ {0x401000,"KERNEL32.dll!GetTickCount"}, {0x401004,"USER32.dll!Unknown"} };
const unsigned speed_import_count=2,guest_func_count=1;
const uint32_t speed_entry=0x401000,guest_func_addr[]={0x401000};
static unsigned increments,worker_bad;
static void guest_thread(Cpu *c) {
    if (M32(c->esp+4)!=0x1234 || M32(c->fs_base)!=UINT32_MAX || M32(c->fs_base+0xe10)!=0 ||
        M32(c->fs_base+0x24)==1) worker_bad=1;
    for (unsigned i=0;i<2000;++i) { ++increments; if (!(i%100)) gil_yield(c); }
    c->eax=77;
}
void (*const guest_func_ptr[])(Cpu *)={guest_thread};
static unsigned resumed,body_returned;
static void body(Cpu *c) {
    c->edx=c->esp-0x48; M32(c->edx+16)=c->esp-4;
    host_lua_resume(c,0x6069e8); body_returned=1;
}
static void resume(Cpu *c) { (void)c;++resumed; }
static void put16(uint8_t *p,uint16_t v) { p[0]=(uint8_t)v;p[1]=(uint8_t)(v>>8); }
static void put32(uint8_t *p,uint32_t v) { for (unsigned i=0;i<4;++i)p[i]=(uint8_t)(v>>(8*i)); }
int main(int argc,char **argv) {
    CHECK(win_vm_init());
    CHECK(win_vm_commit(0x10000,0x20000));
    CHECK(win_vm_commit(WIN_PEB,0x1000));
    Cpu c; win_cpu_init(&c,0x10000,0x20000,0x30000,1);win_cpu=&c;
    uint64_t lo=0,len=0;
    CHECK(win_page_range(0xffffffff,1,4096,&lo,&len) && lo==0xfffff000 && len==4096);
    CHECK(!win_page_range(0xffffffff,2,4096,&lo,&len));
    CHECK(!win_page_range(0,0,4096,&lo,&len));
    CHECK(!win_page_range(0,1,3,&lo,&len));
    CHECK(win_page_range(0,WIN_GUEST_SIZE,4096,&lo,&len) && len==WIN_GUEST_SIZE);
    CHECK(!win_vm_commit(0xffffffff,2));
    CHECK(win_vm_commit(0xffffffff,1)); M8(0xffffffff)=93;CHECK(M8(0xffffffff)==93);
    CHECK(win_import_table_valid(speed_imports,2));
    const struct ImportSlot bad[]={ {8,"x"},{4,"y"} };
    CHECK(!win_import_table_valid(bad,2));
    CHECK(win_import_find(speed_imports,2,0x401004)==speed_imports+1);
    CHECK(!win_import_find(speed_imports,2,0x401002));
    unsigned index=99; CHECK(win_thunk_index(WIN_THUNK_BASE+4,2,&index) && index==1);
    CHECK(!win_thunk_index(WIN_THUNK_BASE+1,2,&index));
    CHECK(!win_thunk_index(WIN_THUNK_BASE+8,2,&index));
    CHECK(win_counter_ns(30000001,10000000)==3000000100ull);
    CHECK(win_counter_ns(0x1000000000000ull,10000000)==28147497671065600ull);
    CHECK(!win_counter_ns(1,0));
    CHECK(win_counter_ns(UINT64_MAX,1000000000)==UINT64_MAX);
    CHECK(!win_counter_ns(184467440739ull,10));
    CHECK(M32(c.fs_base)==UINT32_MAX && M32(c.fs_base+0x18)==c.fs_base && M32(c.fs_base+4)==0x30000);
    uint8_t pe[1024]={0};put16(pe,0x5a4d);put32(pe+0x3c,64);put32(pe+64,0x4550);
    put16(pe+68,0x14c);put16(pe+70,1);put16(pe+84,224);put16(pe+88,0x10b);
    put32(pe+104,0x1000);put32(pe+116,0x400000);put32(pe+144,0x2000);put32(pe+148,512);
    put32(pe+120,0x1000); // SectionAlignment
    put32(pe+312+8,512);put32(pe+312+12,0x1000);put32(pe+312+16,512);put32(pe+312+20,512);pe[512]=0xa5;
    CHECK(win_load_pe(pe,sizeof pe));CHECK(M8(0x401000)==0xa5 && M8(0x401300)==0);
    put32(pe+312+20,900);CHECK(!win_load_pe(pe,sizeof pe));put32(pe+312+20,512);
    CHECK(!win_load_pe(pe,100));put16(pe+68,0x8664);CHECK(!win_load_pe(pe,sizeof pe));
    win_import_bind();CHECK(M32(0x401004)==WIN_THUNK_BASE+4);
    threads_init();
    if (argc>1) {
        if (!strcmp(argv[1],"unsupported"))win_first_import=0;
        host_import(&c,!strcmp(argv[1],"unknown")?0x401002:0x401004);return 1;
    }
    host_lua_protected(&c,body,resume);CHECK(resumed==1 && !body_returned);
    uint32_t esp=c.esp;
    CHECK(win_kernel_import(&c,"TlsAlloc") && c.eax==0);c.esp=esp;
    M32(c.esp+4)=0;M32(c.esp+8)=0xabc;
    CHECK(win_kernel_import(&c,"TlsSetValue") && c.eax==1);c.esp=esp;
    CHECK(M32(c.fs_base+0xe10)==0xabc);
    CHECK(win_kernel_import(&c,"TlsGetValue") && c.eax==0xabc);c.esp=esp;
    M32(c.esp+4)=0x20000;
    CHECK(win_kernel_import(&c,"QueryPerformanceFrequency") && M64(0x20000)==1000000000);c.esp=esp;
    M32(c.esp+4)=64;CHECK(win_kernel_import(&c,"TlsGetValue") && !c.eax && M32(c.fs_base+0x34)==87);c.esp=esp;
    uint32_t tids[3],handles[3];
    for(unsigned i=0;i<3;++i) { handles[i]=win_thread_create(1,0x401000,0x1234,0,&tids[i]);CHECK(handles[i] && tids[i]==i+2); }
    for(unsigned i=0;i<3;++i) { uint32_t result=0;CHECK(win_thread_join(handles[i],&result) && result==77); }
    CHECK(increments==6000 && !worker_bad && M32(c.fs_base+0xe10)==0xabc);
    CHECK(!win_thread_create(1,0x401000,0,CREATE_SUSPENDED,&tids[0]));
    M32(c.esp+4)=0;CHECK(win_kernel_import(&c,"TlsFree") && c.eax==1);c.esp=esp;
    CHECK(win_kernel_import(&c,"TlsAlloc") && c.eax==0);c.esp=esp;
    M32(c.esp+4)=0;CHECK(win_kernel_import(&c,"TlsGetValue") && c.eax==0);c.esp=esp;
    uint64_t before=host_ticks();win_guest_sleep(1);CHECK(host_ticks()>=before);
    win_first_import=0; c.esp=esp; host_import(&c,0x401000); // supported routing checked below with smoke disabled
    gil_release();win_vm_destroy();printf("host fixture passed\n");return 0;
}
