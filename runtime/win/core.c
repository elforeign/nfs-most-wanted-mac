#include "host.h"
_Thread_local Cpu *win_cpu;
_Noreturn void win_fail(const char *why, uint32_t at) {
    printf("win: failure: %s at %08X\n", why, at); fflush(NULL); exit(WIN_FAIL);
}
uint64_t host_ticks(void) {
    LARGE_INTEGER counter, frequency;
    if (!QueryPerformanceCounter(&counter) || !QueryPerformanceFrequency(&frequency) ||
        counter.QuadPart < 0 || frequency.QuadPart <= 0) win_fail("QPC", 0);
    uint64_t ns = win_counter_ns((uint64_t)counter.QuadPart, (uint64_t)frequency.QuadPart);
    if (!ns && counter.QuadPart) win_fail("QPC conversion overflow", 0);
    return ns;
}
void host_cpuid(Cpu *c) {
    switch (c->eax) {
    case 0: c->eax=1; c->ebx=0x756e6547; c->edx=0x49656e69; c->ecx=0x6c65746e; break;
    case 1: c->eax=0xf29; c->ebx=0; c->ecx=0; c->edx=0x0080fbff; break;
    default: c->eax=c->ebx=c->ecx=c->edx=0;
    }
}
void guest_divide_error(Cpu *c, uint32_t at) { (void)c; win_fail("guest divide error", at); }
void guest_breakpoint(Cpu *c, uint32_t at) { (void)c; win_fail("guest breakpoint", at); }
void bad_jump(Cpu *c, uint32_t at) { (void)c; win_fail("bad jump", at); }
void untranslated(Cpu *c, uint32_t at) { (void)c; win_fail("untranslated function", at); }
void dispatch(Cpu *c, uint32_t target) {
    if (target == 0x006069e8u) host_lua_resume(c, target);
    if (win_host_dispatch(c, target)) return;
    unsigned i;
    if (win_thunk_index(target, speed_import_count, &i)) {
        host_import(c, speed_imports[i].slot); return;
    }
    unsigned lo=0, hi=guest_func_count;
    while (lo < hi) {
        unsigned m=lo+(hi-lo)/2;
        if (guest_func_addr[m] == target) { guest_func_ptr[m](c); return; }
        if (guest_func_addr[m] < target) lo=m+1; else hi=m;
    }
    win_fail("unknown dispatch target", target);
}
// Translated SEH registration frames stay in guest FS:[0]. Native Lua frames are
// thread-local; only the known continuation after translated CRT cleanup jumps.
typedef struct LuaFrame {
    struct LuaFrame *prev;
    Cpu *cpu;
    uint32_t buffer;
    jmp_buf env;
} LuaFrame;
static _Thread_local LuaFrame *lua_frame;
void host_lua_protected(Cpu *c, void (*body)(Cpu *), void (*resume)(Cpu *)) {
    LuaFrame frame = {.prev=lua_frame, .cpu=c, .buffer=c->esp-0x48u};
    lua_frame=&frame;
    if (!setjmp(frame.env)) body(c);
    else { ABI_SKIP(); resume(c); }
    lua_frame=frame.prev;
}
void host_lua_resume(Cpu *c, uint32_t target) {
    if (target != 0x006069e8u) return;
    for (LuaFrame *p=lua_frame; p; p=p->prev) {
        if (p->cpu != c || p->buffer != c->edx || c->esp != M32(p->buffer+16)+4) continue;
        lua_frame=p; longjmp(p->env, 1);
    }
}
