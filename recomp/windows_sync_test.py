"""Mac synthetic Windows event/wait proof, discovered by offline_tests.sh.

Original win_kernel_import: 0/5 handled, regression exit 1 -> registered events
and waits: 5/5 handled, fixture exit 0. GIL-retaining waiter: progress 0,
WAIT_TIMEOUT (258), exit 1 -> fixed progress 1, WAIT_OBJECT_0 (0), exit 0.
Both controls use the same bounded progress check; no game or devices are used.
Native events are modeled by pthreads; native Windows semantics need SDK checks.
"""
from pathlib import Path
import hashlib
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
WIN = ROOT / 'runtime/win'

# A bounded original function, independent of repository history.
ORIGINAL_KERNEL = r'''int win_kernel_import(Cpu *c, const char *fn) {
#define IS(name) (!strcmp(fn, name))
#define RET(v,n) return win_ret(c,(uint32_t)(v),n)
    if (IS("GetLastError")) RET(M32(c->fs_base+0x34),0);
    if (IS("SetLastError")) { M32(c->fs_base+0x34)=win_arg(c,0); RET(0,1); }
    if (IS("GetCurrentThreadId")) RET(M32(c->fs_base+0x24),0);
    if (IS("TlsAlloc")) {
        for (unsigned i=0; i<64; ++i) if (!(tls_used & (UINT64_C(1)<<i))) {
            tls_used |= UINT64_C(1)<<i; RET(i,0);
        }
        RET(UINT32_MAX,0);
    }
    if (IS("TlsFree") || IS("TlsGetValue") || IS("TlsSetValue")) {
        uint32_t i=win_arg(c,0);
        if (i>=64 || !(tls_used & (UINT64_C(1)<<i))) { M32(c->fs_base+0x34)=87; RET(0,IS("TlsSetValue")?2:1); }
        if (IS("TlsFree")) { tls_used &= ~(UINT64_C(1)<<i); win_tls_clear(i); RET(1,1); }
        if (IS("TlsSetValue")) { M32(c->fs_base+0xe10+4*i)=win_arg(c,1); RET(1,2); }
        M32(c->fs_base+0x34)=0; RET(M32(c->fs_base+0xe10+4*i),1);
    }
    if (IS("QueryPerformanceCounter") || IS("QueryPerformanceFrequency")) {
        uint32_t out=win_arg(c,0);
        if (!out || !win_vm_commit(out,8)) RET(0,1);
        M64(out)=IS("QueryPerformanceCounter")?host_ticks():1000000000ull;
        RET(1,1);
    }
    if (IS("GetTickCount") || IS("timeGetTime")) RET(host_ticks()/1000000ull,0);
    if (IS("Sleep") || IS("SleepEx")) { win_guest_sleep(win_arg(c,0)); RET(0,IS("SleepEx")?2:1); }
    if (IS("CreateThread")) {
        uint32_t tid=0, out=win_arg(c,5);
        if (out && !win_vm_commit(out,4)) RET(0,6);
        uint32_t h=win_thread_create(win_arg(c,1),win_arg(c,2),win_arg(c,3),win_arg(c,4),&tid);
        if (out) M32(out)=tid;
        RET(h,6);
    }
    return 0;
}
'''
ORIGINAL_KERNEL_SHA = 'af21fb805d7c2d175a3a1f39a5e1cfa8e7a73b97ac165120e8185bf3e8ed0781'

PREMISE = r'''#include "host.h"
uint8_t *G;
int win_vm_commit(uint32_t p,uint64_t n) { (void)p;(void)n;return 0; }
uint64_t host_ticks(void) { return 0; }
void win_guest_sleep(uint32_t ms) { (void)ms; }
void win_tls_clear(unsigned i) { (void)i; }
uint32_t win_thread_create(uint32_t a,uint32_t b,uint32_t c,uint32_t d,uint32_t *e) {
    (void)a;(void)b;(void)c;(void)d;(void)e;return 0;
}
int main(void) {
    const char *names[]={"CreateEventA","SetEvent","ResetEvent","WaitForSingleObject","WaitForMultipleObjects"};
    Cpu c={0}; unsigned handled=0;
    for (unsigned i=0;i<5;++i) handled+=win_kernel_import(&c,names[i])!=0;
    printf("original handled=%u/5\n",handled);return handled!=5;
}
'''

FIXTURE = r'''#include "fake_win.inc"
#include "sync.h"
#include "startup.h"
#include <stdarg.h>
enum { CREATE, SET, RESET, CLOSE, SINGLE, MULTIPLE };
const struct ImportSlot speed_imports[]={
    {0x401000,"KERNEL32.dll!CreateEventA"}, {0x401004,"KERNEL32.dll!SetEvent"},
    {0x401008,"KERNEL32.dll!ResetEvent"}, {0x40100c,"KERNEL32.dll!CloseHandle"},
    {0x401010,"KERNEL32.dll!WaitForSingleObject"}, {0x401014,"KERNEL32.dll!WaitForMultipleObjects"}
};
const unsigned speed_import_count=6,guest_func_count=1;
const uint32_t speed_entry=0x500000,guest_func_addr[]={0x500000};
static unsigned calls;
static _Noreturn void failed(unsigned line) { printf("failed line %u\n",line);exit(1); }
#define REQUIRE(x) do { if (!(x)) failed(__LINE__); } while (0)
static uint32_t call(Cpu *c,unsigned which,unsigned n,...) {
    uint32_t before=c->esp; c->esp-=64;
    uint32_t stack=c->esp; M32(stack)=0xdeadbeef;
    va_list ap; va_start(ap,n);
    for (unsigned i=0; i<n; ++i) M32(stack+4+4*i)=va_arg(ap,uint32_t);
    va_end(ap);
    c->ebx=0x13579bdf; c->esi=0x2468ace0; c->edi=0x98765432; c->ebp=0x12345678;
    host_import(c,speed_imports[which].slot); ++calls;
    REQUIRE(c->esp==stack+4+4*n && c->ebx==0x13579bdf && c->esi==0x2468ace0 &&
            c->edi==0x98765432 && c->ebp==0x12345678);
    uint32_t result=c->eax; c->esp=before; return result;
}
static uint32_t event(Cpu *c,int manual,int initial) {
    uint32_t h=call(c,CREATE,4,0u,(uint32_t)manual,(uint32_t)initial,0u);
    REQUIRE(h && h!=UINT32_MAX); return h;
}
static uint32_t one(Cpu *c,uint32_t h,uint32_t ms) { return call(c,SINGLE,2,h,ms); }
static uint32_t many(Cpu *c,uint32_t a,uint32_t b,uint32_t all,uint32_t ms) {
    M32(0x18000)=a; M32(0x18004)=b;
    return call(c,MULTIPLE,4,2u,0x18000u,all,ms);
}
static void close_event(Cpu *c,uint32_t h) { REQUIRE(call(c,CLOSE,1,h)==1); }
static void expect_error(Cpu *c,uint32_t value,uint32_t expected,uint32_t code) {
    REQUIRE(value==expected && M32(c->fs_base+0x34)==code);
}
typedef struct { uint32_t event,mode,result; unsigned done; } Task;
static Task tasks[4];
static void guest_thread(Cpu *c) {
    unsigned i=M32(c->esp+4); REQUIRE(i<4);
    Task *t=tasks+i;
    if (t->mode==1) {
        // A pre-wait GIL yield must not let the signal arrive before the wait.
        while (!atomic_load(&fake_event_waiting) && t->mode==1) win_guest_sleep(1);
        if (t->mode==1) t->result=call(c,SET,1,t->event);
    }
    else t->result=one(c,t->event,1000);
    ++t->done; c->eax=77;
}
void (*const guest_func_ptr[])(Cpu *)={guest_thread};
static uint32_t start(unsigned i,uint32_t h,uint32_t mode) {
    tasks[i]=(Task){h,mode,0,0}; uint32_t tid=0;
    uint32_t thread=win_thread_create(0,0x500000,i,0,&tid);
    REQUIRE(thread && tid>1); return thread;
}
static void join(uint32_t h) { uint32_t result=0; REQUIRE(win_thread_join(h,&result) && result==77); }
static void await_waiters(unsigned count) {
    uint64_t end=host_ticks()+2000000000ull;
    while (atomic_load(&fake_event_waiting)!=count && host_ticks()<end) win_guest_sleep(1);
    REQUIRE(atomic_load(&fake_event_waiting)==count);
}
static void basic(Cpu *c) {
    uint32_t a=event(c,0,0), b=event(c,1,1);
    M32(c->fs_base+0x34)=0x1234;
    REQUIRE(one(c,a,0)==WAIT_TIMEOUT && M32(c->fs_base+0x34)==0x1234);
    REQUIRE(call(c,SET,1,a)==1 && call(c,SET,1,a)==1);
    REQUIRE(one(c,a,0)==WAIT_OBJECT_0 && one(c,a,0)==WAIT_TIMEOUT);
    REQUIRE(one(c,b,0)==WAIT_OBJECT_0 && one(c,b,0)==WAIT_OBJECT_0);
    REQUIRE(call(c,RESET,1,b)==1 && one(c,b,0)==WAIT_TIMEOUT);
    uint64_t begin=host_ticks(); REQUIRE(one(c,b,25)==WAIT_TIMEOUT);
    REQUIRE(host_ticks()-begin>=20000000ull);
    REQUIRE(call(c,SET,1,b)==1);
    REQUIRE(many(c,a,b,0,0)==WAIT_OBJECT_0+1);
    REQUIRE(call(c,SET,1,a)==1);
    REQUIRE(many(c,a,b,0,0)==WAIT_OBJECT_0); // lowest ready index
    REQUIRE(many(c,a,b,1,0)==WAIT_TIMEOUT && one(c,b,0)==WAIT_OBJECT_0);
    REQUIRE(call(c,SET,1,a)==1 && many(c,a,b,1,0)==WAIT_OBJECT_0);
    REQUIRE(one(c,a,0)==WAIT_TIMEOUT && one(c,b,0)==WAIT_OBJECT_0);
    // Unsuccessful wait-all must not consume any auto-reset event.
    uint32_t d=event(c,0,0);
    REQUIRE(call(c,SET,1,a)==1 && many(c,a,d,1,0)==WAIT_TIMEOUT);
    REQUIRE(one(c,a,0)==WAIT_OBJECT_0);
    REQUIRE(call(c,SET,1,a)==1 && call(c,SET,1,d)==1);
    REQUIRE(many(c,a,d,1,0)==WAIT_OBJECT_0);
    REQUIRE(one(c,a,0)==WAIT_TIMEOUT && one(c,d,0)==WAIT_TIMEOUT);
    close_event(c,d); close_event(c,b);
    // Reusing the same table slot must change the handle generation.
    close_event(c,a); uint32_t replacement=event(c,0,1); REQUIRE(a!=replacement);
    expect_error(c,one(c,a,0),WAIT_FAILED,6);
    expect_error(c,call(c,SET,1,a),0,6);
    expect_error(c,call(c,RESET,1,a),0,6);
    expect_error(c,call(c,CLOSE,1,a),0,6);
    REQUIRE(one(c,replacement,0)==WAIT_OBJECT_0); close_event(c,replacement);
}
static void invalid(Cpu *c) {
    uint32_t a=event(c,0,1);
    expect_error(c,one(c,0,0),WAIT_FAILED,6);
    expect_error(c,one(c,0xffffffffu,0),WAIT_FAILED,6);
    expect_error(c,one(c,0x2000u,0),WAIT_FAILED,6); // thread handles are a follow-on
    expect_error(c,one(c,a+1,0),WAIT_FAILED,6);
    expect_error(c,one(c,a^0x200u,0),WAIT_FAILED,6);
    expect_error(c,many(c,a,0,0,0),WAIT_FAILED,6);
    expect_error(c,many(c,a,a,1,0),WAIT_FAILED,87);
    expect_error(c,many(c,a,a,0,0),WAIT_FAILED,87);
    REQUIRE(one(c,a,0)==WAIT_OBJECT_0); // invalid waits cannot consume the event
    expect_error(c,call(c,MULTIPLE,4,0u,0x18000u,0u,0u),WAIT_FAILED,87);
    expect_error(c,call(c,MULTIPLE,4,65u,0x18000u,0u,0u),WAIT_FAILED,87);
    expect_error(c,call(c,MULTIPLE,4,2u,0xfffffffcu,0u,0u),WAIT_FAILED,87);
    expect_error(c,call(c,MULTIPLE,4,1u,0x50000u,0u,0u),WAIT_FAILED,87);
    expect_error(c,call(c,MULTIPLE,4,1u,0u,0u,0u),WAIT_FAILED,87);
    expect_error(c,call(c,CREATE,4,1u,0u,0u,0u),0,120);
    expect_error(c,call(c,CREATE,4,0u,0u,0u,1u),0,120);
    fake_event_create_fail=1;
    expect_error(c,call(c,CREATE,4,0u,0u,0u,0u),0,8);
    fake_event_create_fail=0; fake_event_wait_fail=1;
    expect_error(c,one(c,a,0),WAIT_FAILED,87);
    fake_event_wait_fail=0;
    close_event(c,a);
}
static void capacity(Cpu *c) {
    uint32_t hs[WIN_EVENT_LIMIT];
    for (unsigned i=0;i<WIN_EVENT_LIMIT;++i) hs[i]=event(c,0,1);
    expect_error(c,call(c,CREATE,4,0u,0u,0u,0u),0,8);
    for (unsigned i=0;i<WIN_WAIT_LIMIT;++i) M32(0x18000+4*i)=hs[i];
    REQUIRE(call(c,MULTIPLE,4,WIN_WAIT_LIMIT,0x18000u,1u,0u)==WAIT_OBJECT_0);
    for (unsigned i=0;i<WIN_WAIT_LIMIT;++i) REQUIRE(one(c,hs[i],0)==WAIT_TIMEOUT);
    for (unsigned i=0;i<WIN_EVENT_LIMIT;++i) close_event(c,hs[i]);
    for (unsigned i=0;i<256;++i) { uint32_t h=event(c,0,0); close_event(c,h); }
    REQUIRE(!fake_event_live);
}
static int progress(Cpu *c,uint32_t ms) {
    uint32_t h=event(c,0,0), thread=start(0,h,1);
    uint32_t result=one(c,h,ms); unsigned completed=tasks[0].done;
    tasks[0].mode=2;
    printf("progress=%u result=%u\n",completed,result);
    join(thread); close_event(c,h);
    return completed==1 && result==WAIT_OBJECT_0 ? 0 : 1;
}
static void concurrent(Cpu *c) {
    uint32_t h=event(c,1,0), a=start(0,h,0), b=start(1,h,0);
    await_waiters(2); REQUIRE(call(c,SET,1,h)==1);
    join(a); join(b);
    REQUIRE(tasks[0].result==WAIT_OBJECT_0 && tasks[1].result==WAIT_OBJECT_0);
    REQUIRE(one(c,h,0)==WAIT_OBJECT_0); close_event(c,h);
    h=event(c,0,0); a=start(0,h,0); b=start(1,h,0); await_waiters(2);
    REQUIRE(call(c,SET,1,h)==1);
    uint64_t end=host_ticks()+2000000000ull;
    while (tasks[0].done+tasks[1].done<1 && host_ticks()<end) win_guest_sleep(1);
    REQUIRE(tasks[0].done+tasks[1].done==1);
    REQUIRE(call(c,SET,1,h)==1); join(a); join(b);
    REQUIRE(tasks[0].result==WAIT_OBJECT_0 && tasks[1].result==WAIT_OBJECT_0);
    REQUIRE(one(c,h,0)==WAIT_TIMEOUT); close_event(c,h);
    // A pending native wait pins a closed event and prevents slot reuse.
    h=event(c,0,0); a=start(0,h,0); await_waiters(1);
    REQUIRE(fake_event_live==1); close_event(c,h); REQUIRE(fake_event_live==1);
    uint32_t next=event(c,0,1); REQUIRE((h&0x1fcu)!=(next&0x1fcu));
    expect_error(c,call(c,SET,1,h),0,6);
    REQUIRE(one(c,next,0)==WAIT_OBJECT_0);
    join(a); REQUIRE(tasks[0].result==WAIT_TIMEOUT && fake_event_live==1);
    close_event(c,next); REQUIRE(!fake_event_live);
}
int main(int argc,char **argv) {
    REQUIRE(win_vm_init() && win_vm_commit(0x10000,0x20000));
    Cpu c; win_cpu_init(&c,0x10000,0x20000,0x30000,1);win_cpu=&c;
    threads_init(); REQUIRE(win_startup_init("synthetic.exe")); win_first_import=0;
    if (argc>1 && !strcmp(argv[1],"first")) { win_first_import=1; (void)event(&c,0,0); return 1; }
    if (argc>1 && !strcmp(argv[1],"bad-stack")) { c.esp=0x50000; host_import(&c,speed_imports[CREATE].slot); return 1; }
    int result=0;
    if (argc>1 && !strcmp(argv[1],"progress")) result=progress(&c,1000);
    else if (argc>1 && !strcmp(argv[1],"infinite")) result=progress(&c,INFINITE);
    else { basic(&c); invalid(&c); capacity(&c); concurrent(&c); result=progress(&c,1000); }
    REQUIRE(!fake_event_live);
    if (!result) printf("SYNC FIXTURE PASS: handled=5/5 calls=%u\n",calls);
    gil_release(); win_cpu=NULL; win_vm_destroy(); return result;
}
'''


def run(argv, code=0):
    result = subprocess.run(list(map(str, argv)), capture_output=True, text=True, timeout=120)
    assert result.returncode == code, (f'{argv[0]} exit {result.returncode}, expected {code}: '
                                       f'{result.stdout[-3000:]} {result.stderr[-3000:]}')
    return result.stdout


def main():
    compiler = shutil.which('clang')
    if not compiler:
        print('SKIP: no C compiler')
        return 3
    assert hashlib.sha256(ORIGINAL_KERNEL.encode()).hexdigest() == ORIGINAL_KERNEL_SHA
    with tempfile.TemporaryDirectory(prefix='windows-sync-') as scratch:
        folder = Path(scratch)
        flags = [compiler, '-std=c11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                 '-fsanitize=address,undefined', '-DNFS_WIN_TEST', '-pthread',
                 '-I', WIN, '-I', ROOT/'recomp', '-I', folder]
        original = folder/'original.c'
        original.write_text('#include "host.h"\nstatic uint64_t tls_used;\n' + ORIGINAL_KERNEL)
        premise = folder/'premise.c'
        premise.write_text(PREMISE)
        baseline = folder/'baseline'
        run([*flags, original, premise, '-o', baseline])
        assert 'original handled=0/5' in run([baseline], 1)
        print('original: handled=0/5; regression exit 1')
        fake = (WIN/'fixture.h').read_text().split('const struct ImportSlot speed_imports[]')[0]
        (folder/'fake_win.inc').write_text(fake)
        fixture = folder/'fixture.c'
        fixture.write_text(FIXTURE)
        units = [WIN/n for n in ('portable.c', 'memory.c', 'core.c', 'kernel.c',
                                  'imports.c', 'startup.c', 'threads.c', 'sync.c')]
        fixed = folder/'fixed'
        run([*flags, *units, fixture, '-o', fixed])
        print(run([fixed]).strip())
        assert 'progress=1 result=0' in run([fixed, 'progress'])
        assert 'progress=1 result=0' in run([fixed, 'infinite'])
        assert 'first import KERNEL32.dll!CreateEventA' in run([fixed, 'first'], 10)
        assert 'CreateEventA' in run([fixed, 'bad-stack'], 2)
        text = (WIN/'sync.c').read_text()
        assert text.count('    gil_release();') == text.count('    gil_acquire();') == 1
        mutant = folder/'retained.c'
        mutant.write_text(text.replace('    gil_release();', '').replace('    gil_acquire();', ''))
        broken = folder/'retained'
        run([*flags, *(mutant if p.name == 'sync.c' else p for p in units), fixture, '-o', broken])
        assert 'progress=0 result=258' in run([broken, 'progress'], 1)
        print('GIL retained: progress=0 result=258 exit 1; fixed: progress=1 result=0 exit 0')
        print('controls: first-import exit 10; inaccessible stack exit 2; all stdcall stacks preserved')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
