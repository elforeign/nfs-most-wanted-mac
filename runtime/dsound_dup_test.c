/* audit2 #8: IDirectSoundBuffer::Lock on a DuplicateSoundBuffer copy must return pointers into the shared guest
 * buffer (the host buffer shares the source memory). Includes production dsound.c with stubbed runtime services,
 * links the real host library (manual sink, no device), makes a buffer, duplicates it, locks both at offsets 0 and 64.
 * Fixed code: duplicate's Lock pointer == source's, Unlock accepts it; prints "dup lock ok" exit 0.
 * Old code (mem=0 on the duplicate): Lock returns guest NULL / offset only -> "FAIL", exit 1.
 * Then releases the source, then the duplicate: the shared memory must be freed exactly once (the duplicate never dropped its source ref, leaking every duplicated source). Fixed: 1 free; before the fix: 0 -> FAIL.
 * Run: sh runtime/dsound_dup_test.sh [--old]
 */
#include "dsound.c"
uint8_t *G; int diag_on, boot_trace;
int movies_playing(void) { return 0; }
static uint32_t bump = 0x1000;
uint32_t galloc(uint32_t n) { uint32_t a = bump; bump += (n + 63) & ~63u; return a; }
uint32_t gmalloc(uint32_t n) { return galloc(n); }
static int frees;
void gfree(uint32_t a) { (void)a; ++frees; }
void diag_event(const char *f, ...) { (void)f; }
uint32_t diag_thread_id(void) { return 0; }
uint64_t host_ticks(void) { return 0; }
uint32_t host_fn(HostFn fn, const char *name) { (void)fn; (void)name; return 0; }
const char *app_pref(const char *k) { (void)k; return NULL; }
void threads_set_event_async(uint32_t h) { (void)h; }
void threads_wait_desc(uint32_t t, char *b, size_t n) { (void)t; if (n) *b = 0; }
void f_0081B4AC_orig(Cpu *c) { (void)c; }

static uint32_t sp = 0x00800000;
static Cpu cpu;
static int call(int (*fn)(Cpu *), int n, const uint32_t *a) {
    cpu.esp = sp;
    M32(sp) = 0;
    for (int i = 0; i < n; ++i) M32(sp + 4 + 4 * i) = a[i];
    fn(&cpu);
    return (int)cpu.eax;
}
int main(void) {
    G = calloc(1, 0x01000000);
    DsConfig cfg; ds_config_default(&cfg); cfg.sink = DS_SINK_MANUAL;
    DsObj *dev = new_obj(1, 0);
    if (ds_device_create(&cfg, &dev->dev) != DS_OK) return 2;
    uint32_t desc = 0x2000, wfx = 0x2100, out = 0x2200, out2 = 0x2210;
    M32(desc + 4) = 0x10000; M32(desc + 8) = 4096; M32(desc + 16) = wfx;   // GETCURRENTPOSITION2, 4096 bytes
    M16(wfx) = 1; M16(wfx + 2) = 2; M32(wfx + 4) = 48000; M32(wfx + 8) = 192000; M16(wfx + 12) = 4; M16(wfx + 14) = 16;
    if (call(d_CreateSoundBuffer, 3, (uint32_t[]){dev->guest, desc, out}) < 0) return 3;
    uint32_t src = M32(out);
    if (call(d_DuplicateSoundBuffer, 3, (uint32_t[]){dev->guest, src, out2}) < 0) return 4;
    uint32_t dup = M32(out2), bad = 0;
    for (uint32_t off = 0; off <= 64; off += 64) {
        uint32_t p1 = 0x3000, l1 = 0x3004, p2 = 0x3008, l2 = 0x300C, ps = 0, pd = 0;
        call(b_Lock, 8, (uint32_t[]){src, off, 32, p1, l1, p2, l2, 0}); ps = M32(p1);
        call(b_Lock, 8, (uint32_t[]){dup, off, 32, p1, l1, p2, l2, 0}); pd = M32(p1);
        printf("offset %u: source lock %08X, duplicate lock %08X\n", off, ps, pd);
        if (ps == 0 || ps != pd) bad = 1;
    }
    call(d_Release, 1, (uint32_t[]){src});
    int after_src = frees;
    call(d_Release, 1, (uint32_t[]){dup});
    printf("frees after source release %d, after duplicate release %d\n", after_src, frees);
    if (after_src != 0 || frees != 1) bad = 1;
    puts(bad ? "FAIL" : "dup lock ok");
    return bad;
}
