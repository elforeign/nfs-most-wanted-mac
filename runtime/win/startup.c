#include "startup.h"
#include "sync.h"

// This prefix uses the same 32-bit XP SP2/NT profile as the translated CRT.
// Handles and structures below belong to the guest; none contains a host pointer.
#define HEAP_BASE 0x10000u
#define KERNEL_MODULE 0x7c800000u
#define STD_BASE 0x11000u
static struct { uint32_t flags, maximum, used; } heaps[16];
static unsigned heap_count;
static struct { uint32_t address, owner, depth; } locks[128];
static unsigned lock_count;
static uint32_t command_line, init_spin;
static unsigned reached;
int win_boot_prefix;

static uint32_t error(Cpu *c, uint32_t code, uint32_t result) {
    M32(c->fs_base+0x34)=code; return result;
}
static int string_equal(uint32_t p, const char *s, int fold) {
    if (!p) return 0;
    for (unsigned i=0; i<128; ++i) {
        if ((uint64_t)p+i>UINT32_MAX || !win_vm_access(p+i,1)) return 0;
        unsigned a=M8(p+i), b=(unsigned char)s[i];
        if (fold) { if (a>='A' && a<='Z') a+='a'-'A'; if (b>='A' && b<='Z') b+='a'-'A'; }
        if (a!=b) return 0;
        if (!a) return 1;
    }
    return 0;
}
static uint32_t version(Cpu *c) {
    uint32_t p=win_arg(c,0);
    if (!win_vm_access(p,148)) return error(c,87,0);
    if (M32(p)!=148) return error(c,87,0);
    memset(G+p,0,148); M32(p)=148;
    M32(p+4)=5; M32(p+8)=1; M32(p+12)=2600; M32(p+16)=2;
    const char sp[]="Service Pack 2";
    for (unsigned i=0; i<sizeof sp; ++i) M8(p+20+i)=(uint8_t)sp[i];
    reached|=1; return 1;
}
static uint32_t module(Cpu *c) {
    uint32_t p=win_arg(c,0);
    if (!p) { reached|=2; return 0x400000; }
    if (string_equal(p,"kernel32.dll",1)) return KERNEL_MODULE;
    return error(c,126,0);
}
static uint32_t proc(Cpu *c) {
    uint32_t p=win_arg(c,1);
    if (win_arg(c,0)!=KERNEL_MODULE) return error(c,6,0);
    if (string_equal(p,"InitializeCriticalSectionAndSpinCount",0)) return init_spin;
    // This guest profile deliberately exposes the CRT's TLS path, without FLS.
    if (string_equal(p,"FlsAlloc",0) || string_equal(p,"FlsGetValue",0) ||
        string_equal(p,"FlsSetValue",0) || string_equal(p,"FlsFree",0)) return error(c,127,0);
    // GetProcAddress has a defined not-found result; this never creates a thunk.
    return error(c,127,0);
}
static uint32_t heap_create(Cpu *c) {
    uint32_t flags=win_arg(c,0), initial=win_arg(c,1), maximum=win_arg(c,2);
    if ((flags&~1u) || (maximum && initial>maximum)) return error(c,87,0);
    if (heap_count==16 || initial>0x40000000u || maximum>0x40000000u) return error(c,8,0);
    heaps[heap_count].flags=flags; heaps[heap_count].maximum=(maximum+0xfffu)&~0xfffu;
    heaps[heap_count].used=0;
    reached|=4; return HEAP_BASE+4*heap_count++;
}
static uint32_t heap_alloc(Cpu *c) {
    uint32_t handle=win_arg(c,0), flags=win_arg(c,1), size=win_arg(c,2);
    if (handle<HEAP_BASE || (handle&3) || (handle-HEAP_BASE)/4>=heap_count) return 0;
    if (flags&~9u) return 0;
    unsigned i=(handle-HEAP_BASE)/4;
    // HeapAlloc never sets LastError, including exhaustion of a fixed heap.
    // Charge at least one 8-byte guest allocation unit, without 32-bit overflow.
    uint64_t charge=((uint64_t)(size?size:1)+7)&~UINT64_C(7);
    if (heaps[i].maximum && charge>heaps[i].maximum-heaps[i].used) return 0;
    uint32_t p=win_alloc(size?size:1);
    if (!p) return 0;
    if (heaps[i].maximum) heaps[i].used+=(uint32_t)charge;
    if (flags&8) memset(G+p,0,size);
    return p;
}
static unsigned lock_find(Cpu *c, uint32_t p) {
    if (!win_vm_access(p,24)) win_fail("critical section pointer",p);
    for (unsigned i=0; i<lock_count; ++i) if (locks[i].address==p) return i;
    (void)c; return lock_count;
}
static uint32_t lock_init(Cpu *c) {
    uint32_t p=win_arg(c,0);
    if (!win_vm_access(p,24)) return error(c,87,0);
    unsigned i=lock_find(c,p);
    if (i!=lock_count) win_fail("InitializeCriticalSection: already initialized",p);
    if (lock_count==128) return error(c,8,0);
    locks[i].address=p; locks[i].owner=locks[i].depth=0; ++lock_count;
    memset(G+p,0,24); M32(p+4)=UINT32_MAX;
    // SpinCount is a DWORD in the guest CRITICAL_SECTION at offset 20.
    M32(p+20)=win_arg(c,1); reached|=8; return 1;
}
static uint32_t lock_init_no_spin(Cpu *c) {
    // InitializeCriticalSection has only one stack argument.
    uint32_t p=win_arg(c,0);
    if (!win_vm_access(p,24)) win_fail("InitializeCriticalSection",p);
    unsigned i=lock_find(c,p);
    if (i!=lock_count || lock_count==128) win_fail("InitializeCriticalSection",p);
    locks[i].address=p; locks[i].owner=locks[i].depth=0; ++lock_count;
    memset(G+p,0,24); M32(p+4)=UINT32_MAX; reached|=8; return 0;
}
static uint32_t lock_enter(Cpu *c) {
    uint32_t p=win_arg(c,0), tid=M32(c->fs_base+0x24);
    unsigned i=lock_find(c,p);
    if (i==lock_count) win_fail("EnterCriticalSection",p);
    // The owner needs the guest GIL to leave. Recheck only after reacquiring it;
    // another waiter may have acquired this section while we were asleep.
    while (locks[i].depth && locks[i].owner!=tid) win_guest_sleep(1);
    if (locks[i].depth==UINT32_MAX) win_fail("EnterCriticalSection",p);
    locks[i].owner=tid; ++locks[i].depth;
    M32(p+4)=locks[i].depth-1; M32(p+8)=locks[i].depth; M32(p+12)=tid;
    return 0;
}
static uint32_t lock_leave(Cpu *c) {
    uint32_t p=win_arg(c,0);
    unsigned i=lock_find(c,p);
    if (i==lock_count || !locks[i].depth || locks[i].owner!=M32(c->fs_base+0x24))
        win_fail("LeaveCriticalSection",p);
    --locks[i].depth;
    if (!locks[i].depth) locks[i].owner=0;
    M32(p+4)=locks[i].depth-1; M32(p+8)=locks[i].depth; M32(p+12)=locks[i].owner;
    return 0;
}
static uint32_t startup_info(Cpu *c) {
    uint32_t p=win_arg(c,0);
    if (!win_vm_access(p,68)) win_fail("GetStartupInfoA",p);
    // Headless startup inherits no CRT descriptors, strings or native handles.
    memset(G+p,0,68); M32(p)=68; reached|=16; return 0;
}
static uint32_t std_handle(Cpu *c) {
    uint32_t which=win_arg(c,0);
    if (which<(uint32_t)-12 || which>(uint32_t)-10) return error(c,87,UINT32_MAX);
    return STD_BASE+4*((uint32_t)-10-which);
}
static uint32_t file_type(Cpu *c) {
    uint32_t h=win_arg(c,0);
    if (h<STD_BASE || h>STD_BASE+8 || (h&3)) return error(c,6,0);
    return 2; // Guest headless character devices; no file I/O adapter is installed.
}
static uint32_t handle_count(Cpu *c) { return win_arg(c,0); }
static uint32_t command(Cpu *c) { (void)c; return command_line; }
// Keep the existing TEB/TLS implementation and its exact Win32 semantics.
#define KERNEL_ADAPTER(name) static uint32_t k_##name(Cpu *c) { \
    Cpu copy=*c; if (!win_kernel_import(&copy,#name)) win_fail(#name,0); return copy.eax; }
KERNEL_ADAPTER(TlsAlloc)
KERNEL_ADAPTER(TlsGetValue)
KERNEL_ADAPTER(TlsSetValue)
KERNEL_ADAPTER(GetCurrentThreadId)
KERNEL_ADAPTER(GetLastError)
KERNEL_ADAPTER(SetLastError)

int win_startup_init(const char *path) {
    if (!path || command_line) return 0;
    // A single quoted executable argument; reject quotes instead of changing argv.
    uint32_t n=0;
    while (path[n]) { if (n==32764 || path[n]=='"') return 0; ++n; }
    command_line=win_alloc(n+3);
    if (!command_line) return 0;
    M8(command_line)='"';
    for (uint32_t i=0; i<n; ++i) M8(command_line+1+i)=(uint8_t)path[i];
    M8(command_line+1+n)='"'; M8(command_line+2+n)=0;
    init_spin=win_host_register("KERNEL32.dll!InitializeCriticalSectionAndSpinCount",lock_init,2);
    if (!init_spin) return 0;
#define REG(name,handler,args) if (!win_import_register("KERNEL32.dll!" name,handler,args)) return 0
    REG("GetVersionExA",version,1);
    REG("GetModuleHandleA",module,1);
    REG("GetProcAddress",proc,2);
    REG("HeapCreate",heap_create,3);
    REG("HeapAlloc",heap_alloc,3);
    REG("InitializeCriticalSection",lock_init_no_spin,1);
    REG("EnterCriticalSection",lock_enter,1);
    REG("LeaveCriticalSection",lock_leave,1);
    REG("GetStartupInfoA",startup_info,1);
    REG("GetStdHandle",std_handle,1);
    REG("GetFileType",file_type,1);
    REG("SetHandleCount",handle_count,1);
    REG("GetCommandLineA",command,0);
    REG("TlsAlloc",k_TlsAlloc,0);
    REG("TlsGetValue",k_TlsGetValue,1);
    REG("TlsSetValue",k_TlsSetValue,2);
    REG("GetCurrentThreadId",k_GetCurrentThreadId,0);
    REG("GetLastError",k_GetLastError,0);
    REG("SetLastError",k_SetLastError,1);
#undef REG
    return win_sync_register();
}
void win_startup_checkpoint(Cpu *c, const struct ImportSlot *entry, uint32_t caller, uint32_t before) {
    if (!win_boot_prefix || entry->slot!=0x008901a4u || caller!=0x007c414au ||
        strcmp(entry->name,"KERNEL32.dll!GetCommandLineA")) return;
    if (reached!=31 || c->eax!=command_line || c->esp!=before+4 ||
        !win_vm_access(command_line,1)) win_fail("incomplete CRT prefix",caller);
    printf("BOOT PREFIX PASS: 007C4144\n"); fflush(NULL); exit(WIN_STOP_PREFIX);
}
