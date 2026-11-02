#include "sync.h"

// All table access holds the guest GIL. Native events provide atomic wait-all
// and auto-reset consumption while guest code on another thread runs.
#define EVENT_TAG 0x40000000u
#define EVENT_GENERATIONS 0x1fffffu
typedef struct {
    HANDLE native;
    uint32_t generation, waiters;
    int open;
} GuestEvent;
static GuestEvent events[WIN_EVENT_LIMIT];

static uint32_t error(Cpu *c, uint32_t code, uint32_t result) {
    M32(c->fs_base+0x34)=code; return result;
}
static GuestEvent *lookup(uint32_t h) {
    if ((h&0xc0000003u)!=EVENT_TAG) return NULL;
    GuestEvent *e=&events[(h>>2)&(WIN_EVENT_LIMIT-1)];
    return e->open && e->generation==((h&0x3fffffffu)>>9) ? e : NULL;
}
static void retire(GuestEvent *e) {
    if (!e->open && !e->waiters) {
        if (!CloseHandle(e->native)) win_fail("event native close",0);
        e->native=NULL;
    }
}
static uint32_t create_event(Cpu *c) {
    // Named objects and SECURITY_ATTRIBUTES need a guest namespace/ACL adapter.
    if (win_arg(c,0) || win_arg(c,3)) return error(c,120,0);
    unsigned i;
    for (i=0; i<WIN_EVENT_LIMIT; ++i)
        if (!events[i].native && events[i].generation<EVENT_GENERATIONS) break;
    if (i==WIN_EVENT_LIMIT) return error(c,8,0);
    HANDLE native=CreateEventA(NULL,win_arg(c,1)!=0,win_arg(c,2)!=0,NULL);
    if (!native) return error(c,GetLastError(),0);
    GuestEvent *e=&events[i];
    e->native=native; e->open=1; ++e->generation;
    return EVENT_TAG | (e->generation<<9) | (i<<2);
}
static uint32_t set_event(Cpu *c) {
    GuestEvent *e=lookup(win_arg(c,0));
    if (!e) return error(c,6,0);
    return SetEvent(e->native) ? 1 : error(c,GetLastError(),0);
}
static uint32_t reset_event(Cpu *c) {
    GuestEvent *e=lookup(win_arg(c,0));
    if (!e) return error(c,6,0);
    return ResetEvent(e->native) ? 1 : error(c,GetLastError(),0);
}
static uint32_t close_event(Cpu *c) {
    GuestEvent *e=lookup(win_arg(c,0));
    if (!e) return error(c,6,0);
    // Closing during a native wait is undefined in Win32. Pin the object until
    // all existing waits return; new calls already see an invalid guest handle.
    e->open=0; retire(e); return 1;
}
static uint32_t wait_events(Cpu *c, uint32_t count, const uint32_t *handles,
                            int all, uint32_t ms) {
    GuestEvent *selected[WIN_WAIT_LIMIT];
    HANDLE native[WIN_WAIT_LIMIT];
    for (unsigned i=0; i<count; ++i) {
        selected[i]=lookup(handles[i]);
        if (!selected[i]) return error(c,6,WAIT_FAILED);
        for (unsigned j=0; j<i; ++j)
            if (handles[i]==handles[j]) return error(c,87,WAIT_FAILED);
        native[i]=selected[i]->native;
    }
    for (unsigned i=0; i<count; ++i) {
        if (selected[i]->waiters==UINT32_MAX) win_fail("event waiter overflow",0);
        ++selected[i]->waiters;
    }
    gil_release();
    DWORD result=WaitForMultipleObjects(count,native,all,ms);
    DWORD code=result==WAIT_FAILED ? GetLastError() : 0;
    gil_acquire();
    for (unsigned i=0; i<count; ++i) { --selected[i]->waiters; retire(selected[i]); }
    return result==WAIT_FAILED ? error(c,code,WAIT_FAILED) : result;
}
static uint32_t wait_single(Cpu *c) {
    uint32_t h=win_arg(c,0);
    return wait_events(c,1,&h,0,win_arg(c,1));
}
static uint32_t wait_multiple(Cpu *c) {
    uint32_t count=win_arg(c,0), p=win_arg(c,1), handles[WIN_WAIT_LIMIT];
    if (!count || count>WIN_WAIT_LIMIT || !win_vm_access(p,4ull*count))
        return error(c,87,WAIT_FAILED);
    for (unsigned i=0; i<count; ++i)
        __builtin_memcpy(handles+i,G+p+4*i,4);
    return wait_events(c,count,handles,win_arg(c,2)!=0,win_arg(c,3));
}
int win_sync_register(void) {
#define REG(name,handler,args) if (!win_import_register("KERNEL32.dll!" name,handler,args)) return 0
    REG("CreateEventA",create_event,4);
    REG("SetEvent",set_event,1);
    REG("ResetEvent",reset_event,1);
    REG("CloseHandle",close_event,1);
    REG("WaitForSingleObject",wait_single,2);
    REG("WaitForMultipleObjects",wait_multiple,4);
#undef REG
    return 1;
}
