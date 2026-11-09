#pragma once
#include "runtime.h"
#include "api.h"
#define WIN_GUEST_SIZE (UINT64_C(1) << 32)
#define WIN_THUNK_BASE 0xf0000000u
#define WIN_HOST_BASE 0xe0000000u
#define WIN_HOST_END 0xe0010000u
#define WIN_ADAPTER_LIMIT 128u
#define WIN_MAIN_TEB 0x7ffde000u
#define WIN_PEB 0x7ffdf000u
#define WIN_STACK_TOP 0x7ff00000u
#define WIN_STACK_SIZE 0x100000u
#define WIN_STOP_IMPORT 10
#define WIN_STOP_PREFIX 12
#define WIN_FAIL 2
struct ImportSlot { uint32_t slot; const char *name; };
extern const struct ImportSlot speed_imports[];
extern const unsigned speed_import_count, guest_func_count;
extern const uint32_t speed_entry, guest_func_addr[];
extern void (*const guest_func_ptr[])(Cpu *);
extern int win_first_import;
extern _Thread_local Cpu *win_cpu;
_Noreturn void win_fail(const char *, uint32_t);
int win_page_range(uint32_t, uint64_t, uint32_t, uint64_t *, uint64_t *);
int win_import_table_valid(const struct ImportSlot *, unsigned);
const struct ImportSlot *win_import_find(const struct ImportSlot *, unsigned, uint32_t);
int win_thunk_index(uint32_t, unsigned, unsigned *);
uint64_t win_counter_ns(uint64_t, uint64_t);
void win_cpu_init(Cpu *, uint32_t, uint32_t, uint32_t, uint32_t);
int win_vm_init(void);
int win_vm_commit(uint32_t, uint64_t);
int win_vm_access(uint32_t, uint64_t);
void win_vm_destroy(void);
uint32_t win_alloc(uint32_t);
int win_load_pe(const uint8_t *, size_t);
void win_import_bind(void);
void win_import_stop(Cpu *, const struct ImportSlot *);
int win_kernel_import(Cpu *, const char *);
// Register while holding the guest GIL. Names are copied; registrations and
// thunk addresses last for the process. Dispatch validates the guest stack and
// performs stdcall cleanup after the handler returns eax.
typedef uint32_t (*WinAdapter)(Cpu *);
int win_import_register(const char *, WinAdapter, unsigned);
uint32_t win_host_register(const char *, WinAdapter, unsigned);
int win_host_dispatch(Cpu *, uint32_t);
uint32_t win_thread_create(uint32_t, uint32_t, uint32_t, uint32_t, uint32_t *);
int win_thread_join(uint32_t, uint32_t *);
void threads_init(void);
void win_tls_clear(unsigned);
void gil_acquire(void);
void gil_release(void);
void gil_yield(Cpu *);
void gil_maybe_yield(Cpu *);
void win_guest_sleep(uint32_t);
static inline uint32_t win_arg(Cpu *c, unsigned i) { return M32(c->esp + 4 + 4*i); }
static inline int win_ret(Cpu *c, uint32_t v, unsigned n) {
    c->eax = v; c->esp += 4 + 4*n; return 1;
}
// Internal adapter lookup, while holding the guest GIL. Allows native audio
// notifications to signal registered guest events without interpreting handles.
WinAdapter win_import_adapter(const char *);
