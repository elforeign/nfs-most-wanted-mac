#include "host.h"
static uint64_t tls_used;
int win_kernel_import(Cpu *c, const char *fn) {
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
