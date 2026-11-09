#include "host.h"
#include "startup.h"
int win_first_import = 1;
typedef struct {
    char name[128];
    WinAdapter call;
    unsigned args;
} Adapter;
static Adapter imports[WIN_ADAPTER_LIMIT], hosts[WIN_ADAPTER_LIMIT];
static unsigned import_count, host_count;
static int adapter_set(Adapter *a, const char *name, WinAdapter call, unsigned args) {
    if (!name || !name[0] || !call || args>32) return 0;
    unsigned n=0;
    while (n<sizeof a->name && name[n]) ++n;
    if (n==sizeof a->name) return 0;
    for (unsigned i=0; i<=n; ++i) a->name[i]=name[i];
    a->call=call; a->args=args; return 1;
}
int win_import_register(const char *name, WinAdapter call, unsigned args) {
    if (!name || import_count==WIN_ADAPTER_LIMIT) return 0;
    for (unsigned i=0; i<import_count; ++i) if (!strcmp(imports[i].name,name)) return 0;
    if (!adapter_set(imports+import_count,name,call,args)) return 0;
    ++import_count; return 1;
}
uint32_t win_host_register(const char *name, WinAdapter call, unsigned args) {
    if (!name || host_count==WIN_ADAPTER_LIMIT) return 0;
    for (unsigned i=0; i<host_count; ++i) if (!strcmp(hosts[i].name,name)) return 0;
    if (!adapter_set(hosts+host_count,name,call,args)) return 0;
    return WIN_HOST_BASE+4*host_count++;
}
static void adapter_call(Cpu *c, const Adapter *a) {
    uint32_t before=c->esp;
    if (!win_vm_access(c->esp,4u+4u*a->args) ||
        (uint64_t)c->esp+4u+4u*a->args>UINT32_MAX ||
        !win_vm_access(c->fs_base,0x1000)) win_fail(a->name,c->esp);
    uint32_t value=a->call(c);
    if (c->esp!=before) win_fail(a->name,c->esp);
    win_ret(c,value,a->args);
}
int win_host_dispatch(Cpu *c, uint32_t target) {
    if (target<WIN_HOST_BASE || target>=WIN_HOST_END) return 0;
    unsigned i=(target-WIN_HOST_BASE)/4;
    if (i>=host_count || (target&3))
        win_fail(i<host_count?hosts[i].name:"unregistered host function",target);
    adapter_call(c,hosts+i); return 1;
}
void win_import_bind(void) {
    if (!win_import_table_valid(speed_imports, speed_import_count)) win_fail("invalid import table", 0);
    for (unsigned i=0; i<speed_import_count; ++i) {
        if (!win_vm_commit(speed_imports[i].slot, 4)) win_fail("IAT commit", speed_imports[i].slot);
        M32(speed_imports[i].slot)=WIN_THUNK_BASE+4*i;
    }
}
void win_import_stop(Cpu *c, const struct ImportSlot *entry) {
    c->eax=0;
    M32(c->fs_base+0x34)=120; // ERROR_CALL_NOT_IMPLEMENTED, per guest thread
    printf("win: %s import %s slot=%08X esp=%08X (headless stop)\n",
           win_first_import ? "first" : "unsupported", entry->name, entry->slot, c->esp);
    fflush(NULL);
    // Never guess a stdcall argument count for an unsupported import.
    exit(win_first_import ? WIN_STOP_IMPORT : WIN_FAIL);
}
void host_import(Cpu *c, uint32_t slot) {
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
    // Generated import names are DLL!name; ordinal-only names remain stubs.
    const char *fn=entry->name;
    for (const char *p=fn; *p; ++p) if (*p=='!' || *p==':') fn=p+1;
    // Only these DLLs own the tiny supported kernel/time surface.
    if ((!__builtin_strncmp(entry->name, "KERNEL32.dll!", 13) ||
         !__builtin_strncmp(entry->name, "WINMM.dll!", 10)) && win_kernel_import(c, fn)) return;
    win_import_stop(c, entry);
}
WinAdapter win_import_adapter(const char *name) {
    for (unsigned i=0; i<import_count; ++i)
        if (!strcmp(imports[i].name,name)) return imports[i].call;
    return NULL;
}
