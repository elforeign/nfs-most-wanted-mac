// dsound.c - DSOUND.dll for the guest: COM objects over port/host/audio (DirectSound 8 -> CoreAudio 5.1).
//
// The library owns devices, buffers, cursors and the mixer; this file owns the vtables, reference counts and
// guest memory. Buffer contents live in guest memory (gmalloc), so the game's Lock pointers are plain guest
// addresses and the mixer reads the same bytes.
#include <math.h>
#include <pthread.h>
#include <stdlib.h>
#include <string.h>

#include "../host/audio/dsound_host.h"
#include "host.h"

void threads_set_event_async(uint32_t h);   // threads.c
uint32_t diag_thread_id(void);
void threads_wait_desc(uint32_t tid, char *buf, size_t n);

enum { DA_PLAY, DA_STOP, DA_LOCK, DA_UNLOCK, DA_SEEK, DA_VOLUME, DA_POLL, DA_NOTIFY, DA_RESTORE, DA_NOPS };
typedef struct {
    uint64_t calls[DA_NOPS], logged[DA_NOPS], failures;
    uint64_t unlocked_bytes, last_unlock, max_unlock_gap, poll_moves;
    uint32_t writer, last_p, last_w;
} DsDiag;

typedef struct {
    int kind;                    // 1 device, 2 buffer, 3 notify
    int refs;
    uint32_t guest;
    DsDevice *dev;
    DsBuffer *buf;
    uint32_t mem, bytes;         // buffer memory (guest)
    uint32_t owner;              // notify: its buffer object; duplicate buffer: the source (memory is shared)
    DsDiag diag;                // touched only with BOOT_DIAG, under the guest lock
    struct {                    // audio-loop watchdog, always on, guest lock only (see dsound_watchdog_tick)
        uint64_t unlocks, seen, last_unlock, quiet_since, travel, max_quiet;
        uint32_t last_p;
        uint8_t looping, playing, fired, watched, locked;
    } wd;
} DsObj;
static DsObj **objs;
static unsigned nobjs;
static uint32_t vt_ds, vt_buf, vt_notify;

static DsObj *new_obj(int kind, uint32_t vt) {
    DsObj *o = calloc(1, sizeof *o);
    o->kind = kind;
    o->refs = 1;
    o->guest = galloc(16);
    M32(o->guest) = vt; M32(o->guest + 4) = nobjs; M32(o->guest + 8) = 0x0D50D0B1;
    objs = realloc(objs, (nobjs + 1) * sizeof *objs);
    objs[nobjs++] = o;
    return o;
}
static DsObj *obj(uint32_t g) {
    if (!g || M32(g + 8) != 0x0D50D0B1) return NULL;
    uint32_t i = M32(g + 4);
    return i < nobjs ? objs[i] : NULL;
}
#define THIS obj(arg(c, 0))
static void *gp(uint32_t a) { return a ? G + a : NULL; }

// First three calls, then at most one line/second per operation/buffer. Counters include suppressed calls;
// successful Unlock bytes/age distinguish a running cursor over stale memory from a stalled device clock.
static int buf_diag_call(Cpu *c, DsObj *b, unsigned op, DsResult r, uint32_t off, uint32_t bytes, uint32_t flags) {
    if (!diag_on) return 0;
    static const char *const names[] = {"Play", "Stop", "Lock", "Unlock", "Seek", "Volume", "Poll", "Notify", "Restore"};
    uint64_t now = host_ticks(), count = ++b->diag.calls[op];
    if (r < 0) ++b->diag.failures;
    if (op == DA_POLL && r >= 0) {
        // Count changes at the game's poll cadence: one-second snapshots can alias a half-second ring.
        if (count > 1 && (off != b->diag.last_p || bytes != b->diag.last_w)) ++b->diag.poll_moves;
        b->diag.last_p = off; b->diag.last_w = bytes;
    }
    if (op == DA_UNLOCK && r >= 0) {
        uint64_t gap = b->diag.last_unlock ? now - b->diag.last_unlock : 0;
        if (gap > b->diag.max_unlock_gap) b->diag.max_unlock_gap = gap;
        b->diag.last_unlock = now;
        b->diag.unlocked_bytes += bytes;
        b->diag.writer = diag_thread_id();
    }
    if (count > 3 && now - b->diag.logged[op] < 1000000000ull) return 0;
    b->diag.logged[op] = now;
    uint32_t p = 0, w = 0, s = 0;
    ds_buffer_get_current_position(b->buf, &p, &w);
    ds_buffer_get_status(b->buf, &s);
    diag_event("dsbuf %s b%08X t%.3f @%08X rc%08X off%u n%u f%X p%u w%u s%X #%llu",
               names[op], b->guest, now / 1e9, M32(c->esp), (uint32_t)r, off, bytes, flags, p, w, s,
               (unsigned long long)count);
    return 1;
}

// ---------------- audio-loop watchdog (always on) ----------------
// A stream the game keeps playing in a loop (Play with DSBPLAY_LOOPING, at least 16 KB, written at least 4 times) normally
// gets a Lock/Unlock every few ms. If its play cursor travels a whole buffer length while no Unlock arrived for over 1 s and
// the game isn't paused, the stream is cycling stale data (the user's "audio loop"): write audio_loop_N.txt into the session
// folder (buffer states, Snd thread waits, the last 32 buffer operations) and note it once (NFSMW.log, session.txt).
// Cost: one tick + counter per Unlock/Play/Stop/Lock, and at most one pass over <= 8 watched buffers per 250 ms (from the
// per-frame pass, so a stuck Snd thread can't stop it). NFS_TEST_AUDIOLOOP=<s> (test only) makes Lock of every watched
// stream fail from <s> seconds after start, so the game stops writing it and the cursor really cycles stale data.
typedef struct { uint64_t at; uint32_t buf, off, bytes; int32_t rc; uint8_t op; } WdOp;
static WdOp wd_ring[32];
static unsigned wd_head;
static DsObj *wd_list[8];
static unsigned wd_checks, wd_fired;
static void wd_op(const DsObj *b, unsigned op, uint32_t off, uint32_t bytes, DsResult r) {
    WdOp *w = &wd_ring[wd_head++ % 32];
    w->at = host_ticks(); w->buf = b->guest; w->op = (uint8_t)op; w->off = off; w->bytes = bytes; w->rc = (int32_t)r;
}
static double wd_test_after = -1;     // NFS_TEST_AUDIOLOOP seconds, -1 off
static uint64_t wd_start;
static int wd_test_block(const DsObj *b) {
    static int parsed;
    if (!parsed) { const char *e = getenv("NFS_TEST_AUDIOLOOP"); wd_test_after = e ? atof(e) : -1; parsed = 1; }
    if (wd_test_after < 0 || !b->wd.watched) return 0;
    if (!wd_start) wd_start = host_ticks();
    return (host_ticks() - wd_start) / 1e9 >= wd_test_after;
}
const char *report_session_dir(void);   // report.m
void report_note(const char *fmt, ...);
int threads_list(pthread_t *ths, Cpu **cpus, uint32_t *tids, int cap);
static void wd_snapshot(DsObj *hit, uint64_t now, double quiet, double cycles) {
    const char *dir = report_session_dir();
    char path[1300] = "";
    FILE *f = NULL;
    if (dir && *dir) { snprintf(path, sizeof path, "%s/audio_loop_%u.txt", dir, wd_fired); f = fopen(path, "w"); }
    uint32_t tid = M32(0x009C22ECu), sys = M32(0x009885E0u), state = sys ? M32(sys + 0x2C) : 0;
    char wait[160] = "";
    if (tid) threads_wait_desc(tid, wait, sizeof wait);
    if (f) {
        fprintf(f, "Audio-loop watchdog snapshot %u: buffer %08X cycled %.1f buffer lengths over %.2f s with no new Lock/Unlock write\n",
                wd_fired, hit->guest, cycles, quiet);
        fprintf(f, "game flow %u, pause %d, sim state %u, Snd worker thread %u run %u/%u clock %u stuck %u\n  worker wait: %s\n",
                M32(0x00925E90u), (int32_t)M32(0x0091CAE4u), state, tid, M8(0x009C2010u), M8(0x009C2011u), M32(0x009C2294u),
                M32(0x009C229Cu), *wait ? wait : "(none recorded)");
        fprintf(f, "\nwatched stream buffers (guest handle, bytes, host status, play/write cursor, Unlocks, last Unlock age s, quiet s):\n");
        for (unsigned i = 0; i < 8; ++i) {
            DsObj *b = wd_list[i];
            if (!b || !b->buf) continue;
            uint32_t p = 0, w = 0, st = 0;
            ds_buffer_get_current_position(b->buf, &p, &w); ds_buffer_get_status(b->buf, &st);
            fprintf(f, "  %08X %u status %X p %u w %u unlocks %llu last %.2f quiet %.2f%s\n", b->guest, b->bytes, st, p, w,
                    (unsigned long long)b->wd.unlocks, b->wd.last_unlock ? (now - b->wd.last_unlock) / 1e9 : -1.0,
                    b->wd.quiet_since ? (now - b->wd.quiet_since) / 1e9 : 0.0, b == hit ? "  <- stuck" : "");
        }
        pthread_t ths[64]; Cpu *cpus[64]; uint32_t tids[64];
        int n = threads_list(ths, cpus, tids, 64);
        fprintf(f, "\nguest threads (id, what each waits on):\n");
        for (int i = 0; i < n; ++i) {
            char w2[200]; threads_wait_desc(tids[i], w2, sizeof w2);
            fprintf(f, "  thread %u%s: %s\n", tids[i], tids[i] == tid ? " (Snd worker)" : "", *w2 ? w2 : "running or no wait recorded");
        }
        static const char *const names[] = {"Play", "Stop", "Lock", "Unlock", "Seek", "Volume", "Poll", "Notify", "Restore"};
        fprintf(f, "\nlast buffer operations, oldest first (age s, buffer, op, offset, bytes, result):\n");
        for (unsigned i = 0; i < 32; ++i) {
            const WdOp *w = &wd_ring[(wd_head + i) % 32];
            if (!w->at) continue;
            fprintf(f, "  -%.3f %08X %-7s off %u bytes %u rc %08X\n", (now - w->at) / 1e9, w->buf, names[w->op], w->off, w->bytes, (uint32_t)w->rc);
        }
        fclose(f);
    }
    report_note("audio: loop watchdog: stream %08X cycled %.1fx with no writes for %.1f s (snapshot %s)", hit->guest, cycles, quiet,
                f ? path : "not written");
    fprintf(stderr, "boot: audio loop watchdog: stream %08X cycled %.1fx with no new writes for %.1f s, snapshot %s\n", hit->guest,
            cycles, quiet, f ? path : "not written");
}
void dsound_watchdog_tick(void) {
    static uint64_t last;
    uint64_t now = host_ticks();
    if (now - last < 250000000ull) return;
    last = now;
    ++wd_checks;
    uint32_t paused = M32(0x0091CAE4u);
    for (unsigned i = 0; i < 8; ++i) {
        DsObj *b = wd_list[i];
        if (!b) continue;
        if (!b->buf || !b->refs) { b->wd.watched = 0; wd_list[i] = NULL; continue; }
        if (!b->wd.playing || !b->wd.looping || paused || b->wd.unlocks < 4) { b->wd.quiet_since = 0; b->wd.seen = b->wd.unlocks; continue; }
        uint32_t p = 0, w = 0;
        if (ds_buffer_get_current_position(b->buf, &p, &w) < 0 || !b->bytes) continue;
        if (b->wd.unlocks != b->wd.seen || !b->wd.quiet_since) {          // written since the last check (or first look)
            b->wd.seen = b->wd.unlocks; b->wd.quiet_since = now; b->wd.travel = 0; b->wd.last_p = p; b->wd.fired = 0;
            continue;
        }
        b->wd.travel += (uint64_t)((p + b->bytes - b->wd.last_p) % b->bytes); b->wd.last_p = p;
        uint64_t quiet = now - b->wd.quiet_since;
        if (quiet > b->wd.max_quiet) b->wd.max_quiet = quiet;
        if (!b->wd.fired && quiet > 1000000000ull && b->wd.travel >= b->bytes && wd_fired < 3) {
            b->wd.fired = 1; ++wd_fired;
            wd_snapshot(b, now, quiet / 1e9, (double)b->wd.travel / b->bytes);
        }
    }
}
// Largest quiet stretch (ms) seen on any watched stream and the check count: BOOT_DIAG's dsaudio line prints them.
static double wd_max_quiet_ms(void) {
    double m = 0;
    for (unsigned i = 0; i < 8; ++i) if (wd_list[i] && wd_list[i]->wd.max_quiet / 1e6 > m) m = wd_list[i]->wd.max_quiet / 1e6;
    return m;
}
static void wd_watch(DsObj *b) {
    if (b->wd.watched) return;
    for (unsigned i = 0; i < 8; ++i) if (!wd_list[i]) { wd_list[i] = b; b->wd.watched = 1; return; }
}

// Called by diag_frame with the guest lock held: no host audio callback reads guest state or writes this log.
// Also logs on pause/state changes so a short menu visit is visible between the one-second snapshots.
void dsound_diag_tick(void) {
    if (!diag_on) return;
    static uint64_t last;
    static uint32_t last_pause = ~0u, last_state = ~0u;
    uint64_t now = host_ticks();
    uint32_t pause = M32(0x0091CAE4u);   // FEManager::mPauseRequest (also used by diag_hud_features)
    uint32_t sys = M32(0x009885E0u), state = sys ? M32(sys + 0x2C) : 0;   // Sim::GetState, 006E8D20
    if (pause == last_pause && state == last_state && now - last < 1000000000ull) return;
    last = now; last_pause = pause; last_state = state;
    uint32_t sched = M32(0x009203B0u), tid = M32(0x009C22ECu);
    diag_event("dsaudio t%.3f flow%u pause%d tick%u sim%u:%.3f worker%u run%u/%u clock%u stuck%u",
               now / 1e9, M32(0x00925E90u), (int32_t)pause, sched ? M32(sched + 0x28) : 0,
               state, MF32(0x009885D8u), tid, M8(0x009C2010u), M8(0x009C2011u), M32(0x009C2294u), M32(0x009C229Cu));
    // Refill arithmetic at 008209B0: target = align16(write/stride + (base+adaptive ms)*rate/1000).
    diag_event("dsaudio fill b%08X frames%u stride%u head%u seen%u lead%.1f+%.1f mix%u ctl%X sfx%X",
               M32(0x009C22B0u), M32(0x009C22B8u), M8(0x009C228Cu), M32(0x009C2298u), M32(0x009C22A0u),
               MF32(0x0090BF3Cu), MF32(0x0090BF4Cu), M32(0x009C2190u), M32(0x00911FE8u), M32(0x00911FF0u));
    diag_event("dsaudio watchdog checks%u fired%u max_quiet_ms%.0f", wd_checks, wd_fired, wd_max_quiet_ms());
    for (unsigned i = 0; i < 8; ++i) {
        const DsObj *b = wd_list[i];
        if (b) diag_event("dsaudio watchdog b%08X playing%u looping%u unlocks%llu seen%llu quiet%.2fs travel%llu/%u pause%u", b->guest,
                          b->wd.playing, b->wd.looping, (unsigned long long)b->wd.unlocks, (unsigned long long)b->wd.seen,
                          b->wd.quiet_since ? (host_ticks() - b->wd.quiet_since) / 1e9 : 0.0, (unsigned long long)b->wd.travel, b->bytes,
                          M32(0x0091CAE4u));
    }
    if (tid) {
        char wait[128];
        threads_wait_desc(tid, wait, sizeof wait);
        if (*wait) diag_event("dsaudio worker%u wait %.128s", tid, wait);
    }
    for (unsigned i = 0; i < nobjs; ++i) {
        DsObj *b = objs[i];
        if (b->kind != 2 || !b->buf || !b->refs || !b->mem) continue;
        uint32_t p = 0, w = 0, s = 0, hz = 0;
        int32_t volume = 0;
        DsResult r = ds_buffer_get_current_position(b->buf, &p, &w);
        ds_buffer_get_status(b->buf, &s);
        ds_buffer_get_frequency(b->buf, &hz);
        ds_buffer_get_volume(b->buf, &volume);
        // Bounded fingerprint, same byte offsets each time; an unchanged hash alone is not proof of no writes.
        uint32_t hash = 2166136261u;
        uint32_t samples = b->bytes < 256 ? b->bytes : 256;
        for (uint32_t k = 0; k < samples; ++k)
            hash = (hash ^ M8(b->mem + (uint32_t)((uint64_t)k * b->bytes / samples))) * 16777619u;
        diag_event("dsbuf state b%08X t%.3f rc%08X s%X p%u w%u size%u hz%u vol%d hash%08X",
                   b->guest, now / 1e9, (uint32_t)r, s, p, w, b->bytes, hz, volume, hash);
        diag_event("dsbuf io b%08X P%llu S%llu L%llu U%llu Q%llu/%llu err%llu bytes%llu age%.1f gap%.1f writer%u",
                   b->guest, (unsigned long long)b->diag.calls[DA_PLAY], (unsigned long long)b->diag.calls[DA_STOP],
                   (unsigned long long)b->diag.calls[DA_LOCK], (unsigned long long)b->diag.calls[DA_UNLOCK],
                   (unsigned long long)b->diag.calls[DA_POLL], (unsigned long long)b->diag.poll_moves,
                   (unsigned long long)b->diag.failures,
                   (unsigned long long)b->diag.unlocked_bytes,
                   b->diag.last_unlock ? (now - b->diag.last_unlock) / 1e6 : -1.0, b->diag.max_unlock_gap / 1e6, b->diag.writer);
        b->diag.max_unlock_gap = 0;
    }
}

static void notify_cb(void *user, uint32_t event) { (void)user; threads_set_event_async(event); }
// The host's output changes (device created/released, output opened/kept/closed, device changes, underruns) also go to
// events.log in diagnostic mode; they are on stderr either way.
static void log_cb(const char *line) { diag_event("dsound: %s", line); }

// ---------------- IDirectSound ----------------
static int d_QueryInterface(Cpu *c) {          // (this, riid, ppv)
    DsObj *o = THIS;
    const uint8_t *g = G + arg(c, 1);
    // IID_IDirectSound {279AFA83-...}, IID_IDirectSound8 {C50A7E93-...}, IID_IDirectSoundBuffer {279AFA85-...},
    // IID_IDirectSoundBuffer8 {6825A449-...}, IID_IDirectSoundNotify {B0210783-...}
    uint32_t d1 = (uint32_t)g[0] | (uint32_t)g[1] << 8 | (uint32_t)g[2] << 16 | (uint32_t)g[3] << 24;
    if ((o->kind == 1 && (d1 == 0x279AFA83 || d1 == 0xC50A7E93)) || (o->kind == 2 && (d1 == 0x279AFA85 || d1 == 0x6825A449))) {
        ++o->refs;
        M32(arg(c, 2)) = o->guest;
        RET(0, 3);
    }
    if (o->kind == 2 && d1 == 0xB0210783) {
        DsObj *n = new_obj(3, vt_notify);
        n->owner = o->guest;
        n->buf = o->buf;
        ++o->refs;
        M32(arg(c, 2)) = n->guest;
        RET(0, 3);
    }
    M32(arg(c, 2)) = 0;
    RET(DSERR_NOINTERFACE, 3);
}
static int d_AddRef(Cpu *c) { DsObj *o = THIS; RET(o ? ++o->refs : 0, 1); }
static int d_Release(Cpu *c) {
    DsObj *o = THIS;
    if (!o) RET(0, 1);
    if (--o->refs > 0) RET(o->refs, 1);
    if (diag_on) diag_event("dsbuf Release obj%08X kind%d t%.3f @%08X size%u",
                           o->guest, o->kind, host_ticks() / 1e9, M32(c->esp), o->bytes);
    if (o->kind == 1 && o->dev) { ds_device_release(o->dev); o->dev = NULL; }
    if (o->kind == 2 && o->buf) { ds_buffer_release(o->buf); o->buf = NULL; if (o->mem && !o->owner) gfree(o->mem); o->mem = 0; }
    if (o->kind == 2 && o->owner) {            // a duplicate drops its hold on the source (taken in Duplicate);
        DsObj *s = obj(o->owner);              // the last one out frees the source the game already released
        o->owner = 0;
        if (s && --s->refs == 0 && s->buf) { ds_buffer_release(s->buf); s->buf = NULL; if (s->mem) gfree(s->mem); s->mem = 0; }
    }
    if (o->kind == 3) { DsObj *b = obj(o->owner); if (b) --b->refs; }
    RET(0, 1);
}
static int d_CreateSoundBuffer(Cpu *c) {       // (this, DSBUFFERDESC*, ppBuffer, outer)
    DsObj *d = THIS;
    uint32_t desc = arg(c, 1);
    uint32_t flags = M32(desc + 4), bytes = M32(desc + 8), wfx = M32(desc + 16);
    DsObj *b = new_obj(2, vt_buf);
    if (!(flags & DSBCAPS_PRIMARYBUFFER)) { b->mem = gmalloc(bytes ? bytes : 16); b->bytes = bytes; }
    DsResult r = ds_device_create_buffer(d->dev, flags, bytes, gp(wfx), b->mem ? G + b->mem : NULL, &b->buf);
    b->dev = d->dev;
    if (diag_on) diag_event("dsbuf Create b%08X dev%08X @%08X rc%08X flags%X size%u ch%u hz%u bits%u block%u",
                           b->guest, d->guest, M32(c->esp), (uint32_t)r, flags, bytes,
                           wfx ? M16(wfx + 2) : 0, wfx ? M32(wfx + 4) : 0,
                           wfx ? M16(wfx + 14) : 0, wfx ? M16(wfx + 12) : 0);
    if (boot_trace) fprintf(stderr, "      CreateSoundBuffer(flags %X, %u bytes, %s) -> %08X\n", flags, bytes,
                            wfx ? (M16(wfx + 2) == 6 ? "6 ch" : M16(wfx + 2) == 2 ? "stereo" : "mono") : "no format", (uint32_t)r);
    if (r < 0) { if (b->mem) gfree(b->mem); b->mem = 0; M32(arg(c, 2)) = 0; RET((uint32_t)r, 4); }
    M32(arg(c, 2)) = b->guest;
    RET(0, 4);
}
static int d_GetCaps(Cpu *c) { RET((uint32_t)ds_device_get_caps(THIS->dev, G + arg(c, 1)), 2); }
static int d_DuplicateSoundBuffer(Cpu *c) {    // (this, src, pp)
    DsObj *d = THIS, *s = obj(arg(c, 1));
    if (!s) RET((uint32_t)DSERR_INVALIDPARAM, 3);
    DsObj *b = new_obj(2, vt_buf);
    DsResult r = ds_device_duplicate_buffer(d->dev, s->buf, &b->buf);
    b->dev = d->dev;
    b->mem = s->mem; b->bytes = s->bytes;      // the host copy shares the source's memory; Lock/Unlock translate through it
    b->owner = s->guest;                       // not the owner: Release must not gfree it
    if (diag_on) diag_event("dsbuf Duplicate b%08X src%08X @%08X rc%08X", b->guest, s->guest, M32(c->esp), (uint32_t)r);
    if (r < 0) RET((uint32_t)r, 3);
    ++s->refs;                                 // keeps the shared memory alive
    M32(arg(c, 2)) = b->guest;
    RET(0, 3);
}
static int d_SetCooperativeLevel(Cpu *c) { RET((uint32_t)ds_device_set_cooperative_level(THIS->dev, arg(c, 2)), 3); }
static int d_Compact(Cpu *c) { RET(0, 1); }
static int d_GetSpeakerConfig(Cpu *c) { uint32_t v; DsResult r = ds_device_get_speaker_config(THIS->dev, &v); M32(arg(c, 1)) = v; RET((uint32_t)r, 2); }
static int d_SetSpeakerConfig(Cpu *c) { RET((uint32_t)ds_device_set_speaker_config(THIS->dev, arg(c, 1)), 2); }
static int d_Initialize(Cpu *c) { RET((uint32_t)DSERR_ALREADYINITIALIZED, 2); }

// ---------------- IDirectSoundBuffer ----------------
static int b_GetCaps(Cpu *c) { RET((uint32_t)ds_buffer_get_caps(THIS->buf, G + arg(c, 1)), 2); }
static int b_GetCurrentPosition(Cpu *c) {      // (this, play*, write*)
    DsObj *b = THIS;
    uint32_t sys = M32(0x009885E0u), sim = sys ? M32(sys + 0x2C) : 0;
    // Guest state is read under the guest lock, never by the host mixer. Only the startup front end is eligible;
    // no delayed reopen during an in-game pause/load or after Snd's later device re-creation.
    ds_device_launch_reopen_tick(b->dev, M32(0x0091CAE4u) != 0 || sim == 4 || M32(0x00925E90u) > 3);
    uint32_t p = 0, w = 0;
    DsResult r = ds_buffer_get_current_position(b->buf, &p, &w);
    if (arg(c, 1)) M32(arg(c, 1)) = p;
    if (arg(c, 2)) M32(arg(c, 2)) = w;
    buf_diag_call(c, b, DA_POLL, r, p, w, 0);
    RET((uint32_t)r, 3);
}
static int b_GetFormat(Cpu *c) {               // (this, wfx*, size, written*)
    uint32_t wr = 0;
    DsResult r = ds_buffer_get_format(THIS->buf, gp(arg(c, 1)), arg(c, 2), &wr);
    if (arg(c, 3)) M32(arg(c, 3)) = wr;
    RET((uint32_t)r, 4);
}
static int b_GetVolume(Cpu *c) { int32_t v = 0; DsResult r = ds_buffer_get_volume(THIS->buf, &v); M32(arg(c, 1)) = (uint32_t)v; RET((uint32_t)r, 2); }
static int b_GetPan(Cpu *c) { int32_t v = 0; DsResult r = ds_buffer_get_pan(THIS->buf, &v); M32(arg(c, 1)) = (uint32_t)v; RET((uint32_t)r, 2); }
static int b_GetFrequency(Cpu *c) { uint32_t v = 0; DsResult r = ds_buffer_get_frequency(THIS->buf, &v); M32(arg(c, 1)) = v; RET((uint32_t)r, 2); }
static int b_GetStatus(Cpu *c) { uint32_t v = 0; DsResult r = ds_buffer_get_status(THIS->buf, &v); M32(arg(c, 1)) = v; RET((uint32_t)r, 2); }
static int b_Initialize(Cpu *c) { RET((uint32_t)DSERR_ALREADYINITIALIZED, 3); }
static int b_Lock(Cpu *c) {                    // (this, off, bytes, p1*, l1*, p2*, l2*, flags)
    DsObj *b = THIS;
    uint32_t o1 = 0, l1 = 0, o2 = 0, l2 = 0;
    int two = arg(c, 5) != 0;
    if (wd_test_block(b)) { b->wd.locked = 0; RET((uint32_t)DSERR_BUFFERLOST, 8); }
    DsResult r = ds_buffer_lock(b->buf, arg(c, 1), arg(c, 2), arg(c, 7), &o1, &l1, two ? &o2 : NULL, two ? &l2 : NULL);
    wd_op(b, DA_LOCK, arg(c, 1), arg(c, 2), r);
    b->wd.locked = r >= 0;
    if (r >= 0) {
        if (arg(c, 3)) M32(arg(c, 3)) = b->mem + o1;
        if (arg(c, 4)) M32(arg(c, 4)) = l1;
        if (two) M32(arg(c, 5)) = l2 ? b->mem + o2 : 0;
        if (arg(c, 6)) M32(arg(c, 6)) = l2;
    }
    if (buf_diag_call(c, b, DA_LOCK, r, arg(c, 1), arg(c, 2), arg(c, 7)))
        diag_event("dsbuf regions b%08X lock %u+%u / %u+%u", b->guest, o1, l1, o2, l2);
    RET((uint32_t)r, 8);
}
static int b_Play(Cpu *c) {
    DsObj *b = THIS;
    DsResult r = ds_buffer_play(b->buf, arg(c, 3));
    wd_op(b, DA_PLAY, 0, b->bytes, r);
    if (r >= 0) { b->wd.playing = 1; b->wd.looping = (arg(c, 3) & DSBPLAY_LOOPING) != 0; b->wd.quiet_since = 0;
                  if (b->wd.looping && b->bytes >= 16384) wd_watch(b); }
    buf_diag_call(c, b, DA_PLAY, r, 0, b->bytes, arg(c, 3));
    RET((uint32_t)r, 4);
}
static int b_SetCurrentPosition(Cpu *c) {
    DsObj *b = THIS;
    DsResult r = ds_buffer_set_current_position(b->buf, arg(c, 1));
    wd_op(b, DA_SEEK, arg(c, 1), b->bytes, r);
    b->wd.quiet_since = 0;
    buf_diag_call(c, b, DA_SEEK, r, arg(c, 1), b->bytes, 0);
    RET((uint32_t)r, 2);
}
static int b_SetFormat(Cpu *c) {
    uint32_t w = arg(c, 1);
    if (boot_trace && w) fprintf(stderr, "      primary SetFormat(%u ch, %u Hz, %u bit, tag %04X)\n", M16(w + 2), M32(w + 4), M16(w + 14), M16(w));
    RET((uint32_t)ds_buffer_set_format(THIS->buf, gp(w)), 2);
}
static int b_SetVolume(Cpu *c) {
    DsObj *b = THIS;
    DsResult r = ds_buffer_set_volume(b->buf, (int32_t)arg(c, 1));
    buf_diag_call(c, b, DA_VOLUME, r, arg(c, 1), 0, 0);
    RET((uint32_t)r, 2);
}
static int b_SetPan(Cpu *c) { RET((uint32_t)ds_buffer_set_pan(THIS->buf, (int32_t)arg(c, 1)), 2); }
static int b_SetFrequency(Cpu *c) { RET((uint32_t)ds_buffer_set_frequency(THIS->buf, arg(c, 1)), 2); }
static int b_Stop(Cpu *c) {
    DsObj *b = THIS;
    DsResult r = ds_buffer_stop(b->buf);
    wd_op(b, DA_STOP, 0, b->bytes, r); b->wd.playing = 0; b->wd.quiet_since = 0;
    buf_diag_call(c, b, DA_STOP, r, 0, b->bytes, 0);
    RET((uint32_t)r, 1);
}
static int b_Unlock(Cpu *c) {                  // (this, p1, l1, p2, l2)
    DsObj *b = THIS;
    uint32_t p1 = arg(c, 1), p2 = arg(c, 3);
    if (wd_test_block(b)) { RET((uint32_t)DSERR_BUFFERLOST, 5); }   // test only: no write reaches the host
    DsResult r = ds_buffer_unlock(b->buf, p1 ? p1 - b->mem : 0, arg(c, 2), p2 ? p2 - b->mem : 0, arg(c, 4));
    wd_op(b, DA_UNLOCK, p1 ? p1 - b->mem : 0, arg(c, 2) + arg(c, 4), r);
    if (r >= 0 && b->wd.locked) { ++b->wd.unlocks; b->wd.last_unlock = host_ticks(); }
    b->wd.locked = 0;
    if (buf_diag_call(c, b, DA_UNLOCK, r, p1 ? p1 - b->mem : 0, arg(c, 2) + arg(c, 4), 0))
        diag_event("dsbuf regions b%08X unlock %u+%u / %u+%u", b->guest,
                   p1 ? p1 - b->mem : 0, arg(c, 2), p2 ? p2 - b->mem : 0, arg(c, 4));
    RET((uint32_t)r, 5);
}
static int b_Restore(Cpu *c) {
    DsObj *b = THIS;
    DsResult r = ds_buffer_restore(b->buf);
    buf_diag_call(c, b, DA_RESTORE, r, 0, b->bytes, 0);
    RET((uint32_t)r, 1);
}
static int n_SetNotificationPositions(Cpu *c) {   // (this, count, DSBPOSITIONNOTIFY*)
    DsObj *n = THIS;
    uint32_t cnt = arg(c, 1), p = arg(c, 2);
    uint32_t offs[64], evs[64];
    if (cnt > 64) cnt = 64;
    for (uint32_t i = 0; i < cnt; ++i) { offs[i] = M32(p + 8 * i); evs[i] = M32(p + 8 * i + 4); }
    DsResult r = ds_buffer_set_notification_positions(n->buf, cnt, offs, evs);
    DsObj *b = obj(n->owner);
    if (b && buf_diag_call(c, b, DA_NOTIFY, r, 0, cnt, 0))
        for (uint32_t i = 0; i < cnt; ++i)
            diag_event("dsbuf notify b%08X offset%u event%08X", b->guest, offs[i], evs[i]);
    RET((uint32_t)r, 3);
}

// ---------------- vtables ----------------
typedef struct { const char *name; HostFn fn; } Impl;
static int unimplemented(Cpu *c) { (void)c; return 0; }
static uint32_t make_vtable(const char *iface, const char *const *names, int n, const Impl *impl) {
    uint32_t vt = galloc(4 * (uint32_t)n);
    for (int i = 0; i < n; ++i) {
        char *full = malloc(strlen(iface) + strlen(names[i]) + 3);
        sprintf(full, "%s::%s", iface, names[i]);
        HostFn fn = unimplemented;
        for (const Impl *p = impl; p->name; ++p) if (!strcmp(p->name, names[i])) fn = p->fn;
        M32(vt + 4 * i) = host_fn(fn, full);
    }
    return vt;
}
static const char *const n_ds[] = {"QueryInterface", "AddRef", "Release", "CreateSoundBuffer", "GetCaps",
    "DuplicateSoundBuffer", "SetCooperativeLevel", "Compact", "GetSpeakerConfig", "SetSpeakerConfig", "Initialize"};
static const Impl i_ds[] = {{"QueryInterface", d_QueryInterface}, {"AddRef", d_AddRef}, {"Release", d_Release},
    {"CreateSoundBuffer", d_CreateSoundBuffer}, {"GetCaps", d_GetCaps}, {"DuplicateSoundBuffer", d_DuplicateSoundBuffer},
    {"SetCooperativeLevel", d_SetCooperativeLevel}, {"Compact", d_Compact}, {"GetSpeakerConfig", d_GetSpeakerConfig},
    {"SetSpeakerConfig", d_SetSpeakerConfig}, {"Initialize", d_Initialize}, {0, 0}};
static const char *const n_buf[] = {"QueryInterface", "AddRef", "Release", "GetCaps", "GetCurrentPosition", "GetFormat",
    "GetVolume", "GetPan", "GetFrequency", "GetStatus", "Initialize", "Lock", "Play", "SetCurrentPosition", "SetFormat",
    "SetVolume", "SetPan", "SetFrequency", "Stop", "Unlock", "Restore"};
#define B(n) {#n, b_##n}
static const Impl i_buf[] = {{"QueryInterface", d_QueryInterface}, {"AddRef", d_AddRef}, {"Release", d_Release},
    B(GetCaps), B(GetCurrentPosition), B(GetFormat), B(GetVolume), B(GetPan), B(GetFrequency), B(GetStatus), B(Initialize),
    B(Lock), B(Play), B(SetCurrentPosition), B(SetFormat), B(SetVolume), B(SetPan), B(SetFrequency), B(Stop), B(Unlock),
    B(Restore), {0, 0}};
static const char *const n_notify[] = {"QueryInterface", "AddRef", "Release", "SetNotificationPositions"};
static const Impl i_notify[] = {{"QueryInterface", d_QueryInterface}, {"AddRef", d_AddRef}, {"Release", d_Release},
    {"SetNotificationPositions", n_SetNotificationPositions}, {0, 0}};

int win32_dsound(Cpu *c, const char *fn) {
    if (!vt_ds) {
        vt_ds = make_vtable("IDirectSound", n_ds, 11, i_ds);
        vt_buf = make_vtable("IDirectSoundBuffer", n_buf, 21, i_buf);
        vt_notify = make_vtable("IDirectSoundNotify", n_notify, 4, i_notify);
        if (diag_on) ds_set_log(log_cb);
    }
    if (!strcmp(fn, "#1") || !strcmp(fn, "#11") || !strcmp(fn, "DirectSoundCreate") || !strcmp(fn, "DirectSoundCreate8")) {
        // (guid, ppDS, outer)
        DsConfig cfg;
        ds_set_output_choice(app_pref("audio_output"));   // the Audio menu's choice (NFS_AUDIO overrides it)
        ds_config_default(&cfg);
        cfg.notify = notify_cb;
        DsObj *d = new_obj(1, vt_ds);
        DsResult r = ds_device_create(&cfg, &d->dev);
        if (boot_trace) fprintf(stderr, "      DirectSoundCreate -> %08X\n", (uint32_t)r);
        if (r < 0) { M32(arg(c, 1)) = 0; RET((uint32_t)r, 3); }
        M32(arg(c, 1)) = d->guest;
        RET(0, 3);
    }
    if (!strcmp(fn, "#6") || !strcmp(fn, "#12")) { M32(arg(c, 1)) = 0; RET((uint32_t)DSERR_NODRIVER, 3); }   // capture
    return 0;
}

// The newest live DirectSound device (Snd re-creates its device when it stalls). movies.c plays movie audio on it.
DsDevice *dsound_current_device(void) {
    for (unsigned i = nobjs; i-- > 0;)
        if (objs[i]->kind == 1 && objs[i]->dev) return objs[i]->dev;
    return NULL;
}

// ---------------- the game's output mode (recomp/hooks.txt) ----------------
// Snd::System::SetOutputMode(mode) (cdecl, 0081B4AC). The PC game only ever asks for mono/stereo (Audio Mode option
// 0) or ProLogic II (option 1, which its caps table rejects); the engine's discrete 5.1 mode 3 is never requested
// although the start-up probe marks 6-channel output supported. Every request becomes mode 3, so the whole game mixes
// to one 6-channel buffer (FL FR C LFE BL BR) and the host folds down if the output isn't 5.1.
// NFS_SND_MODE=game keeps the game's own choice; NFS_SND_MODE=<n> forces mode n.
void f_0081B4AC_orig(Cpu *c);
void f_0081B4AC(Cpu *c) {
    static int force = -2;
    if (force == -2) {
        const char *e = getenv("NFS_SND_MODE");
        force = !e ? 3 : !strcmp(e, "game") ? -1 : atoi(e);
    }
    uint32_t want = M32(c->esp + 4);
    if (force >= 0 && want != (uint32_t)force) {
        M32(c->esp + 4) = (uint32_t)force;
        f_0081B4AC_orig(c);
        M32(c->esp + 4) = want;
        if (diag_on) diag_event("Snd output mode %u requested, %d set -> %X", want, force, c->eax);
        if ((int32_t)c->eax >= 0) return;
    }
    f_0081B4AC_orig(c);
    if (diag_on) diag_event("Snd output mode %u -> %X", want, c->eax);
}

// The overlay's audio line: output mode name, live (1) / null sink (0) / waiting for a device (-1), underruns.
int dsound_output_info(const char **mode, int *live, uint64_t *underruns) {
    DsDevice *dev = dsound_current_device();
    DsOutputMode m;
    if (!dev || ds_device_output_info(dev, &m, live, underruns) != 0) return 0;
    *mode = m == DS_OUT_TRUEHD ? "Dolby TrueHD 5.1" : m == DS_OUT_DOLBY_DIGITAL_PLUS ? "Dolby Digital Plus 5.1"
          : m == DS_OUT_DOLBY_DIGITAL ? "Dolby Digital 5.1"
          : m == DS_OUT_STEREO ? "Stereo" : "PCM 5.1";
    return 1;
}

// Per-channel levels of the device mix since the caller's last read (skipped while a movie plays: movies.c reads the
// same window). The device has one stats window; every read folds it into each reader's own accumulator, so the
// report/session pass (reader 0) and BOOT_DIAG's stats line (reader 1) each see their whole second.
typedef struct { double sumsq[6]; float peak[6], dip[6]; uint64_t clipped[6], frames, limited; int have_dip; } MixAcc;
static MixAcc mix_acc[2];
static pthread_mutex_t mix_lock = PTHREAD_MUTEX_INITIALIZER;

int dsound_mix_levels(int reader, double rms[6], float peak[6], uint64_t clipped[6], float dip[6], uint64_t *limited) {
    int movies_playing(void);
    DsDevice *dev = dsound_current_device();
    DsStats st;
    if (!dev || movies_playing()) return 0;
    pthread_mutex_lock(&mix_lock);
    int ok = ds_device_get_stats(dev, &st) == 0;
    if (ok)
        for (int r = 0; r < 2; ++r) {
            MixAcc *a = &mix_acc[r];
            for (int k = 0; k < 6; ++k) {
                a->sumsq[k] += st.rms[k] * st.rms[k] * (double)st.window;
                if (st.peak[k] > a->peak[k]) a->peak[k] = st.peak[k];
                a->clipped[k] += st.clipped[k];
                if (st.dip[k] >= 0 && (!a->have_dip || st.dip[k] < a->dip[k])) a->dip[k] = st.dip[k];
            }
            if (st.dip[0] >= 0) a->have_dip = 1;
            a->frames += st.window;
            a->limited += st.limited;
        }
    MixAcc *a = &mix_acc[reader & 1];
    for (int k = 0; k < 6; ++k) {
        if (rms) rms[k] = a->frames ? sqrt(a->sumsq[k] / (double)a->frames) : 0;
        if (peak) peak[k] = a->peak[k];
        if (clipped) clipped[k] = a->clipped[k];
        if (dip) dip[k] = a->have_dip ? a->dip[k] : 0;
    }
    if (limited) *limited = a->limited;
    if (ok) memset(a, 0, sizeof *a);
    pthread_mutex_unlock(&mix_lock);
    return ok;
}

int dsound_mix_rms(double rms[6], uint64_t *clipped, uint64_t *limited) {
    uint64_t c[6] = {0};
    int ok = dsound_mix_levels(1, rms, NULL, c, NULL, limited);
    *clipped = 0;
    for (int k = 0; k < 6; ++k) *clipped += c[k];
    return ok;
}
