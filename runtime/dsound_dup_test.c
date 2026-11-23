/* audit2 #8: IDirectSoundBuffer::Lock on a DuplicateSoundBuffer copy must return pointers into the shared guest
 * buffer (the host buffer shares the source memory). Includes production dsound.c with stubbed runtime services,
 * links the real host library (manual sink, no device), makes a buffer, duplicates it, locks both at offsets 0 and 64.
 * Fixed code: duplicate's Lock pointer == source's, Unlock accepts it; prints "dup lock ok" exit 0.
 * Old code (mem=0 on the duplicate): Lock returns guest NULL / offset only -> "FAIL", exit 1.
 * Then releases the source, then the duplicate: the shared memory must be freed exactly once (the duplicate never dropped its source ref, leaking every duplicated source). Fixed: 1 free; before the fix: 0 -> FAIL.
 * F1 pre-fix -> fixed: wrapping Lock rendered 16/32 -> 0/32 markers before Unlock,
 * and 32/32 -> 32/32 after. In both release orders Notify-last had 1 -> 0 live
 * buffers and 0 -> 1 PCM frees. Duplicate chains had 1 -> 0 premature PCM frees.
 * 100 resets retained 300 -> 0 wrappers and allocated 300 -> 0 more headers.
 * Failed-create case kept 5 -> 0 wrappers, freed PCM once in both runs, and returned
 * FFFFFFFF -> 00000000 in the output slot of the failed duplicate.
 * Run: sh runtime/dsound_dup_test.sh [--old | --f1-control | --carbon]
 */
#include "dsound_host.h"

static unsigned live_buffers;
static int fail_device_create;
static DsResult manual_create(const DsConfig *cfg, DsDevice **out) {
    if (fail_device_create) return DSERR_BADFORMAT;
    DsConfig manual = *cfg;
    manual.sink = DS_SINK_MANUAL;
    manual.mix_rate = 48000;
    return ds_device_create(&manual, out);
}
static DsResult counted_create(DsDevice *d, uint32_t flags, uint32_t bytes, const void *wfx,
                               void *mem, DsBuffer **out) {
    DsResult r = ds_device_create_buffer(d, flags, bytes, wfx, mem, out);
    if (r == DS_OK) ++live_buffers;
    return r;
}
static DsResult counted_duplicate(DsDevice *d, DsBuffer *src, DsBuffer **out) {
    DsResult r = ds_device_duplicate_buffer(d, src, out);
    if (r == DS_OK) ++live_buffers;
    return r;
}
static void counted_release(DsBuffer *b) {
    if (b) --live_buffers;
    ds_buffer_release(b);
}

#define ds_device_create manual_create
#define ds_device_create_buffer counted_create
#define ds_device_duplicate_buffer counted_duplicate
#define ds_buffer_release counted_release
#include "dsound.c"
#undef ds_device_create
#undef ds_device_create_buffer
#undef ds_device_duplicate_buffer
#undef ds_buffer_release

uint8_t *G; int diag_on, boot_trace;
int movies_playing(void) { return 0; }
static uint32_t bump = 0x10000;
static unsigned bump_allocs;
static uint32_t bump_alloc(uint32_t n) { uint32_t a = bump; bump += (n + 63) & ~63u; return a; }
uint32_t galloc(uint32_t n) { ++bump_allocs; return bump_alloc(n); }
uint32_t gmalloc(uint32_t n) { return bump_alloc(n); }
uint32_t gmalloc_try(uint32_t n) { return gmalloc(n); }
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
static int create_device_call(Cpu *c) { return win32_dsound(c, "DirectSoundCreate"); }

static const uint32_t desc = 0x2000, wfx = 0x2100, out = 0x2200;
static void expect(int ok) {
    if (!ok) {
        fputs("FAIL: fixture setup\n", stderr);
        exit(2);
    }
}
static uint32_t device(void) {
    expect(call(create_device_call, 3, (uint32_t[]){0, out, 0}) == DS_OK);
    return M32(out);
}
static void descriptor(uint32_t flags, uint32_t bytes, uint16_t channels) {
    M32(desc + 4) = flags; M32(desc + 8) = bytes; M32(desc + 16) = channels ? wfx : 0;
    M16(wfx) = 1; M16(wfx + 2) = channels; M32(wfx + 4) = 48000;
    M32(wfx + 8) = 96000 * channels; M16(wfx + 12) = 2 * channels; M16(wfx + 14) = 16;
}
static uint32_t buffer(uint32_t d, uint32_t flags, uint32_t bytes, uint16_t channels) {
    descriptor(flags, bytes, channels);
    expect(call(d_CreateSoundBuffer, 4, (uint32_t[]){d, desc, out, 0}) == DS_OK);
    return M32(out);
}
static uint32_t duplicate(uint32_t d, uint32_t src) {
    expect(call(d_DuplicateSoundBuffer, 3, (uint32_t[]){d, src, out}) == DS_OK);
    return M32(out);
}
static void release(uint32_t handle) { call(d_Release, 1, &handle); }

static int limiter_test(uint32_t dev) {
    // Exercise the real source limiter -> stats windows -> diagnostic reader path, without a device.
    // Negative control removes PeakLimiter's minimum update; expected gain 0.445625, broken gain 1.
    ds_set_peak_limiter(1);
    float loud[256 * 6];
    for (int i = 0; i < 256 * 6; ++i) loud[i] = 2.0f;
    ds_device_limit_game_mix(obj(dev)->dev, loud, 256, 48000);
    double rms[6], gain = 1;
    uint64_t clips, limited;
    // The report reader consumes the host window first; the diagnostic reader must retain that minimum.
    dsound_mix_levels(0, NULL, NULL, NULL, NULL, NULL);
    if (!dsound_mix_rms(rms, &clips, &limited, &gain) || !limited || fabs(gain - 0.445625469) > 1e-7) {
        printf("FAIL: diagnostic limiter gain %.6f samples %llu\n", gain, (unsigned long long)limited);
        return 1;
    }
    printf("limit_samples %llu limit_min_gain %.6f\n", (unsigned long long)limited, gain);
    dsound_mix_rms(rms, &clips, &limited, &gain);
    if (gain != 1.0 || limited != 0) { puts("FAIL: diagnostic minimum window reset"); return 1; }
    ds_set_peak_limiter(0);
    return 0;
}

static int duplicate_lock_test(uint32_t dev) {
    uint32_t src = buffer(dev, DSBCAPS_GETCURRENTPOSITION2, 4096, 2);
    uint32_t dup = duplicate(dev, src);
    int bad = 0, start = frees;
    for (uint32_t off = 0; off <= 64; off += 64) {
        uint32_t p1 = 0x3000, l1 = 0x3004, p2 = 0x3008, l2 = 0x300C, ps = 0, pd = 0;
        call(b_Lock, 8, (uint32_t[]){src, off, 32, p1, l1, p2, l2, 0}); ps = M32(p1);
        call(b_Lock, 8, (uint32_t[]){dup, off, 32, p1, l1, p2, l2, 0}); pd = M32(p1);
        printf("offset %u: source lock %08X, duplicate lock %08X\n", off, ps, pd);
        if (ps == 0 || ps != pd) bad = 1;
        if (call(b_Unlock, 5, (uint32_t[]){dup, pd, M32(l1), 0, 0}) != DS_OK) bad = 1;
        expect(call(b_Unlock, 5, (uint32_t[]){src, ps, 32, 0, 0}) == DS_OK);
    }
    release(src);
    int after_src = frees - start;
    release(dup);
    printf("frees after source release %d, after duplicate release %d\n", after_src, frees - start);
    if (after_src != 0 || frees - start != 1) bad = 1;
    return bad;
}

static int marker_frames(DsDevice *d, uint32_t dup, uint32_t offset) {
    float pcm[32 * 6];
    expect(call(b_SetCurrentPosition, 2, (uint32_t[]){dup, offset}) == DS_OK);
    expect(ds_device_render(d, pcm, 32) == DS_OK);
    int markers = 0;
    for (unsigned i = 0; i < 32; ++i) markers += pcm[6 * i] == 0.5f;
    return markers;
}
static int wrapping_lock_test(uint32_t dev) {
    const uint32_t offset = 4096 - 16 * 4;
    uint32_t src = buffer(dev, DSBCAPS_GETCURRENTPOSITION2, 4096, 2);
    uint32_t dup = duplicate(dev, src);
    expect(call(b_Play, 4, (uint32_t[]){dup, 0, 0, DSBPLAY_LOOPING}) == DS_OK);
    expect(call(b_Lock, 8, (uint32_t[]){src, offset, 32 * 4, 0x3000, 0x3004, 0x3008, 0x300c, 0}) == DS_OK);
    uint32_t tail = M32(0x3000), tail_len = M32(0x3004), head = M32(0x3008), head_len = M32(0x300c);
    expect(tail_len == 16 * 4 && head_len == 16 * 4 && tail - head == offset);
    for (uint32_t i = 0; i < tail_len; i += 2) M16(tail + i) = 16384;
    DsDevice *d = obj(dev)->dev;
    int before = marker_frames(d, dup, offset);
    for (uint32_t i = 0; i < head_len; i += 2) M16(head + i) = 16384;
    expect(call(b_Unlock, 5, (uint32_t[]){src, tail, tail_len, head, head_len}) == DS_OK);
    int after = marker_frames(d, dup, offset);
    printf("wrapping Lock: before Unlock %d/32, after %d/32\n", before, after);
    release(src);
    release(dup);
    return before != 0 || after != 32;
}

static unsigned retained_wrappers(void) {
    unsigned retained = 0;
    for (unsigned i = 0; i < nobjs; ++i) retained += objs[i] != NULL;
    return retained;
}

static int notify_last_test(int device_first) {
    unsigned live = live_buffers;
    int start = frees;
    uint32_t d = device();
    uint32_t b = buffer(d, DSBCAPS_CTRL3D | DSBCAPS_CTRLPOSITIONNOTIFY, 16384, 1);
    expect(call(b_Play, 4, (uint32_t[]){b, 0, 0, DSBPLAY_LOOPING}) == DS_OK);
    expect(obj(b)->wd.watched);
    M32(0x3100) = 0xB0210783;
    expect(call(d_QueryInterface, 3, (uint32_t[]){b, 0x3100, out}) == DS_OK);
    uint32_t n = M32(out);
    if (device_first) {
        release(d);
        expect(call(b_GetStatus, 2, (uint32_t[]){b, out}) == DS_OK && M32(out) == DSBSTATUS_BUFFERLOST);
    }
    release(b);
    int bad = live_buffers != live + 1 || frees != start;
    release(n);
    unsigned remaining = live_buffers - live, watched = 0;
    for (unsigned i = 0; i < 8; ++i) watched += wd_list[i] != NULL;
    printf("notify last (%s first): %u live, %d PCM frees, %u watched\n",
           device_first ? "device" : "buffer", remaining, frees - start, watched);
    if (!device_first) {
        DsStats stats;
        expect(ds_device_get_stats(obj(d)->dev, &stats) == DS_OK);
        bad |= stats.buffers != remaining;
        release(d);
    }
    return bad || remaining != 0 || frees - start != 1 || watched != 0;
}

static int duplicate_chain_test(int device_first) {
    unsigned live = live_buffers;
    int start = frees;
    uint32_t d = device();
    uint32_t root = buffer(d, DSBCAPS_GETCURRENTPOSITION2, 4096, 2);
    uint32_t middle = duplicate(d, root);
    uint32_t leaf = duplicate(d, middle);
    uint32_t sibling = duplicate(d, root);
    if (device_first) {
        release(d);
        expect(call(b_GetStatus, 2, (uint32_t[]){leaf, out}) == DS_OK && M32(out) == DSBSTATUS_BUFFERLOST);
    }
    release(root);
    release(middle);
    release(leaf);
    int premature = frees - start;
    release(sibling);
    unsigned remaining = live_buffers - live;
    printf("duplicate chain (%s first): %d premature PCM frees, %d total, %u live\n",
           device_first ? "device" : "buffer", premature, frees - start, remaining);
    if (!device_first) release(d);
    return premature != 0 || frees - start != 1 || remaining != 0;
}

static int reset_test(void) {
    unsigned retained = retained_wrappers(), slots = nobjs, allocs = bump_allocs, live = live_buffers;
    for (int i = 0; i < 100; ++i) {
        uint32_t d = device();
        uint32_t p = buffer(d, DSBCAPS_PRIMARYBUFFER, 0, 0);
        uint32_t b = buffer(d, DSBCAPS_GETCURRENTPOSITION2, 4096, 2);
        release(b);
        release(p);
        release(d);
    }
    printf("100 resets: %u retained wrappers, %u new slots, %u new headers\n",
           retained_wrappers() - retained, nobjs - slots, bump_allocs - allocs);
    return retained_wrappers() != retained || nobjs != slots || bump_allocs != allocs || live_buffers != live;
}

static int create_failure_test(void) {
    unsigned retained = retained_wrappers(), live = live_buffers;
    int start = frees;
    fail_device_create = 1;
    M32(out) = ~0u;
    expect(call(create_device_call, 3, (uint32_t[]){0, out, 0}) == DSERR_BADFORMAT && !M32(out));
    fail_device_create = 0;
    uint32_t d = device(), p = buffer(d, DSBCAPS_PRIMARYBUFFER, 0, 0);
    descriptor(DSBCAPS_CTRL3D, 4096, 2);
    M32(out) = ~0u;
    expect(call(d_CreateSoundBuffer, 4, (uint32_t[]){d, desc, out, 0}) == DSERR_INVALIDPARAM && !M32(out));
    M32(out) = ~0u;
    expect(call(d_DuplicateSoundBuffer, 3, (uint32_t[]){d, p, out}) == DSERR_INVALIDPARAM);
    int output_cleared = !M32(out);
    release(p);
    release(d);
    printf("failed creates: %u retained wrappers, %d PCM frees, duplicate output %08X\n",
           retained_wrappers() - retained, frees - start, M32(out));
    return retained_wrappers() != retained || live_buffers != live || frees - start != 1 || !output_cleared;
}

static int newest_device_test(void) {
    uint32_t older = device();
    uint32_t newer = device();
    expect(dsound_current_device() == obj(newer)->dev);
    release(older);
    uint32_t newest = device();
    int ok = newest == older && dsound_current_device() == obj(newest)->dev;
#if NFS_GAME == NFS_GAME_PRIVATE
    unsigned buffers, playing;
    private_mix_buffers(&buffers, &playing);
    ok &= buffers == 0 && playing == 0;
#endif
    release(newest);
    ok &= dsound_current_device() == obj(newer)->dev;
    release(newer);
    return !ok || dsound_current_device() != NULL;
}

int main(int argc, char **argv) {
    setenv("BOOT_DIAG", "1", 1);
    G = calloc(1, 0x01000000);
    expect(G != NULL);
    uint32_t d = device();
    int bad = limiter_test(d);
    bad |= duplicate_lock_test(d);
    // The historical mem=0 control exercises only the original duplicate test.
    if (argc == 1 || strcmp(argv[1], "--old")) {
        bad |= wrapping_lock_test(d);
        release(d);
        bad |= notify_last_test(0);
        bad |= notify_last_test(1);
        bad |= duplicate_chain_test(0);
        bad |= duplicate_chain_test(1);
        bad |= reset_test();
        if (argc == 1) {
            bad |= create_failure_test();
            bad |= newest_device_test();
        }
    } else release(d);
    puts(bad ? "FAIL" : "dup lock ok");
    return bad != 0;
}
