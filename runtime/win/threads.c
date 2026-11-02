#include "host.h"
// Fair ticket lock: generated code and all guest-visible host state are serial.
static SRWLOCK mutex=SRWLOCK_INIT;
static CONDITION_VARIABLE changed=CONDITION_VARIABLE_INIT;
static uint64_t next_ticket, serving;
static Cpu *main_cpu;
static _Thread_local int owns_gil;
static _Thread_local uint64_t held_since;
void gil_acquire(void) {
    if (owns_gil) win_fail("recursive GIL",0);
    AcquireSRWLockExclusive(&mutex);
    uint64_t ticket=next_ticket++;
    while (ticket!=serving)
        if (!SleepConditionVariableSRW(&changed,&mutex,INFINITE,0)) win_fail("GIL wait",0);
    owns_gil=1; held_since=host_ticks();
    ReleaseSRWLockExclusive(&mutex);
}
void gil_release(void) {
    if (!owns_gil) win_fail("GIL not held",0);
    AcquireSRWLockExclusive(&mutex);
    owns_gil=0; ++serving; WakeAllConditionVariable(&changed);
    ReleaseSRWLockExclusive(&mutex);
}
void gil_yield(Cpu *c) { (void)c; gil_release(); gil_acquire(); }
void gil_maybe_yield(Cpu *c) { if (host_ticks()-held_since>=2000000) gil_yield(c); }
void win_guest_sleep(uint32_t ms) { gil_release(); Sleep(ms); gil_acquire(); }
void threads_init(void) { main_cpu=win_cpu; gil_acquire(); }
// Guest handles never contain truncated native pointers. Fixed slots live until
// joined; native completion is waited for with the GIL released.
typedef struct { HANDLE handle; Cpu cpu; uint32_t start,param,result; } GuestThread;
static GuestThread workers[32];
static uint32_t next_tid=2;
void win_tls_clear(unsigned index) {
    if (main_cpu) M32(main_cpu->fs_base+0xe10+4*index)=0;
    for (unsigned i=0; i<32; ++i)
        if (workers[i].handle) M32(workers[i].cpu.fs_base+0xe10+4*index)=0;
}
static DWORD thread_entry(void *arg) {
    GuestThread *t=arg;
    win_cpu=&t->cpu; gil_acquire();
    t->cpu.esp-=4; M32(t->cpu.esp)=t->param;
    t->cpu.esp-=4; M32(t->cpu.esp)=0xdeadbeef;
    dispatch(&t->cpu,t->start); t->result=t->cpu.eax;
    gil_release(); win_cpu=NULL; return t->result;
}
uint32_t win_thread_create(uint32_t stack,uint32_t start,uint32_t param,uint32_t flags,uint32_t *tid) {
    // Suspension/resume/termination and guest thread-handle waits are follow-ons.
    if (flags) return 0;
    if (stack<WIN_STACK_SIZE) stack=WIN_STACK_SIZE;
    uint64_t aligned=((uint64_t)stack+0xfff)&~UINT64_C(0xfff);
    if (aligned>0x10000000) return 0;
    unsigned i;
    for (i=0; i<32 && workers[i].handle; ++i) {}
    if (i==32) return 0;
    uint32_t lo=win_alloc((uint32_t)aligned+0x1000);
    if (!lo) return 0;
    GuestThread *t=&workers[i];
    win_cpu_init(&t->cpu,lo+(uint32_t)aligned,lo,lo+(uint32_t)aligned,next_tid);
    t->start=start; t->param=param; t->result=0;
    // CreateThread can start immediately but cannot enter guest code until this
    // caller releases the GIL. Give translated recursion a 64 MiB native stack.
    t->handle=CreateThread(NULL,64u<<20,thread_entry,t,STACK_SIZE_PARAM_IS_A_RESERVATION,NULL);
    if (!t->handle) return 0;
    *tid=next_tid++; return 0x2000+4*i;
}
int win_thread_join(uint32_t handle,uint32_t *result) {
    if (handle<0x2000 || (handle&3) || (handle-0x2000)/4>=32) return 0;
    GuestThread *t=&workers[(handle-0x2000)/4];
    if (!t->handle || (uintptr_t)t->handle==1 || win_cpu==&t->cpu) return 0;
    HANDLE native=t->handle;
    // Prevent another guest caller from joining/reusing this slot meanwhile.
    t->handle=(HANDLE)(uintptr_t)1;
    gil_release(); DWORD wait=WaitForSingleObject(native,INFINITE); gil_acquire();
    if (wait!=WAIT_OBJECT_0) { t->handle=native; return 0; }
    *result=t->result; CloseHandle(native); t->handle=NULL; return 1;
}
