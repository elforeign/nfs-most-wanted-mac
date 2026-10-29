// Only the fingerprinted owner's server.dll; native bodies, guest PE data/addresses.
#include "host.h"
#include "server_loader.h"
#include <strings.h>
#include <stdlib.h>
#include <limits.h>

int server_library_name(const char *name) {
    return name && (!strcasecmp(name, "server") || !strcasecmp(name, "server.dll"));
}

#ifdef NFS_SERVER_LINKED
#include "server/server_tables.h"
#include <CommonCrypto/CommonDigest.h>
// All loader state is protected by the runtime's guest GIL. During entry/stop
// calls imports may yield it; reject recursive load/unload until they finish.
static unsigned refs;
static int registered, changing;
int threads_drain_module(uint32_t lo, uint32_t hi);
int threads_module_self(uint32_t lo, uint32_t hi);

static uint32_t word(const uint8_t *p) { uint32_t v; memcpy(&v, p, 4); return v; }
static uint16_t half(const uint8_t *p) { uint16_t v; memcpy(&v, p, 2); return v; }
static uint32_t entry(Cpu *c, uint32_t reason) {
    Cpu saved = *c;
    uint32_t a[] = {server_image_base, reason, 0};
    uint32_t result = call_guest(c, server_entry_addr, 3, a);
    *c = saved;
    return result;
}
int server_thread_attach(Cpu *c) {
    if (!registered || changing) return 0;
    entry(c, 2); // DLL_THREAD_ATTACH: CRT per-thread setup
    return 1;
}
void server_thread_detach(Cpu *c) {
    if (registered) entry(c, 3);                                     
}
uint32_t server_handle(void) { return refs && !changing ? server_image_base : 0; }
uint32_t server_load(Cpu *c, const char *path) {
    if (changing) { last_error = 1114; return 0; }
    if (getenv("NFS_TEST_SERVER") && !strcmp(getenv("NFS_TEST_SERVER"), "deny")) { last_error = 126; return 0; } // lanserver_check.sh's broken run
    if (refs) {
        if (refs == UINT_MAX) { last_error = 8; return 0; }
        ++refs;
        return server_image_base;
    }
    FILE *f = fopen(path, "rb");
    if (!f) { last_error = 126; return 0; }
    uint8_t *raw = malloc(server_file_size);
    int valid = raw && fread(raw, 1, server_file_size, f) == server_file_size && fgetc(f) == EOF && !ferror(f);
    fclose(f);
    if (valid) {
        uint8_t digest[CC_SHA256_DIGEST_LENGTH]; char hex[65];
        CC_SHA256(raw, server_file_size, digest);
        for (unsigned i = 0; i < sizeof digest; ++i) sprintf(hex + 2*i, "%02x", digest[i]);
        valid = !strcmp(hex, server_file_sha256);
    }
    if (!valid) { free(raw); last_error = 193; return 0; }
    // The strict size/hash profile validates every offset below before use.
    changing = 1;
    uint32_t pe = word(raw + 0x3c), opt = pe + 24;
    unsigned n = half(raw + pe + 6);
    uint32_t sections = opt + half(raw + pe + 20);
    memset(G + server_image_base, 0, server_reserved_end - server_image_base);
    memcpy(G + server_image_base, raw, word(raw + opt + 60));
    for (unsigned i = 0; i < n; ++i) {
        const uint8_t *s = raw + sections + 40*i;
        memcpy(G + server_image_base + word(s + 12), raw + word(s + 20), word(s + 16));
    }
    free(raw);
    for (unsigned i = 0; i < server_relocation_count; ++i)
        M32(server_image_base + server_relocation_rva[i]) += server_image_base - server_preferred_base;
    M32(server_image_base + opt + 28) = server_image_base;
    for (unsigned i = 0; i < server_import_count; ++i)
        M32(server_imports[i].slot) = SERVER_THUNK_BASE + 4*i;
    registered = 1; // entry's indirect calls and CRT imports need the table now
    uint32_t attached = entry(c, 1); // DLL_PROCESS_ATTACH, including CRT startup
    if (!attached) {
        threads_drain_module(server_image_base, server_reserved_end);
        entry(c, 0);
        registered = 0;
        memset(G + server_image_base, 0, server_reserved_end - server_image_base);
        changing = 0; last_error = 1114;
        return 0;
    }
    refs = 1;
    changing = 0;
    return server_image_base;
}
uint32_t server_proc(uint32_t h, uint32_t name) {
    if (h != server_handle() || !h) { last_error = 6; return 0; }
    for (unsigned i = 0; i < server_export_count; ++i) {
        const ServerExport *e = &server_exports[i];
        if (name <= 0xffff ? name == e->ordinal : !strcmp((char *)G + name, e->name)) return e->address;
    }
    last_error = 127;
    return 0;
}
int server_free(Cpu *c, uint32_t h) {
    if (!h || h != server_handle()) { last_error = 6; return 0; }
    if (refs > 1) { --refs; return 1; }
    if (threads_module_self(server_image_base, server_reserved_end)) { last_error = 170; return 0; }
    changing = 1;
    Cpu saved = *c;
    // StopServer requests cooperative exit, then may use TerminateThread after
                                                                           
    call_guest(c, 0x02001000u, 0, NULL);
    *c = saved;
    if (!threads_drain_module(server_image_base, server_reserved_end)) {
        changing = 0; last_error = 170; return 0;
    }
    entry(c, 0);                                                        
    registered = 0; refs = 0;
    memset(G + server_image_base, 0, server_reserved_end - server_image_base);
    changing = 0;
    return 1;
}
const char *server_import(uint32_t slot, unsigned *index) {
    if (!registered) return NULL;
    for (unsigned i = 0; i < server_import_count; ++i)
        if (server_imports[i].slot == slot) { if (index) *index = i; return server_imports[i].name; }
    return NULL;
}
int server_dispatch(Cpu *c, uint32_t t) {
    if (!registered) return 0;
    if (t >= SERVER_THUNK_BASE && t < SERVER_THUNK_BASE + 4*server_import_count && !(t & 3)) {
        host_import(c, server_imports[(t - SERVER_THUNK_BASE)/4].slot);
        return 1;
    }
    if (t < server_image_base || t >= server_reserved_end) return 0;
    unsigned lo = 0, hi = server_guest_func_count;
    while (lo < hi) {
        unsigned m = lo + (hi - lo)/2;
        if (server_guest_func_addr[m] < t) lo = m + 1; else hi = m;
    }
    if (lo == server_guest_func_count || server_guest_func_addr[lo] != t) return 0;
    server_guest_func_ptr[lo](c);
    return 1;
}
#else
uint32_t server_handle(void) { return 0; }
uint32_t server_load(Cpu *c, const char *path) { (void)c; (void)path; return 0; }
uint32_t server_proc(uint32_t h, uint32_t n) { (void)h; (void)n; return 0; }
int server_free(Cpu *c, uint32_t h) { (void)c; (void)h; return 0; }
int server_dispatch(Cpu *c, uint32_t t) { (void)c; (void)t; return 0; }
const char *server_import(uint32_t s, unsigned *i) { (void)s; (void)i; return NULL; }
int server_thread_attach(Cpu *c) { (void)c; return 0; }
void server_thread_detach(Cpu *c) { (void)c; }
#endif

