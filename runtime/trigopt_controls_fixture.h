// Real native row and settings, fake FE allocation/text/dispatch only.
#include "settings.c"
static uint32_t trig_heap = 0xc00000;
static HostFn trig_callbacks[8];
static unsigned trig_ncallbacks, trig_writes;
static int trig_effect = 1;
static char trig_saved[8], trig_drawn[16];
void padfx_set_triggers(int on) { trig_effect = on != 0; }
const char *app_pref(const char *key) { return !strcmp(key, "controls.adaptive_triggers") && trig_saved[0] ? trig_saved : NULL; }
void app_pref_set(const char *key, const char *value) {
    CHECK(!strcmp(key, "controls.adaptive_triggers"));
    snprintf(trig_saved, sizeof trig_saved, "%s", value); ++trig_writes;
}
void app_pref_batch_begin(void) {}
void app_pref_batch_end(void) {}
void report_note(const char *fmt, ...) { (void)fmt; }
uint32_t galloc(uint32_t size) { uint32_t at = trig_heap; trig_heap = (at + size + 3) & ~3u; if (trig_heap >= 0x1000000) abort(); return at; }
uint32_t gstr(const char *s) { uint32_t at = galloc((uint32_t)strlen(s) + 1); strcpy((char *)G + at, s); return at; }
uint32_t host_fn(HostFn fn, const char *name) { (void)name; if (trig_ncallbacks == 8) abort(); trig_callbacks[trig_ncallbacks] = fn; return HOSTFN_BASE + 4 * trig_ncallbacks++; }
#ifndef TRIGOPT_SOURCE
#define TRIGOPT_SOURCE "trigopt.c"
#endif
#include TRIGOPT_SOURCE
static void trig_append(uint32_t end, uint32_t object) {
    uint32_t node = object + 4, prev = M32(end + 4);
    M32(prev) = node; M32(node + 4) = prev; M32(node) = end; M32(end + 4) = node;
}
static unsigned trig_rows(uint32_t self) {
    unsigned n = 0; uint32_t end = self + 0x3c;
    for (uint32_t p = M32(end); p && p != end; p = M32(p)) { if (++n > 64) abort(); }
    return n;
}
static void trig_rebuild(Cpu *c) {
    uint32_t self = c->ecx, end = self + 0x34;
    static int initialized;
    if (!initialized) { unsetenv("NFS_PAD_TRIGGERS"); initialized = 1; }
    settings_init();
    if (!M32(end)) {
        M32(self) = 0x720100; M32(0x720104) = 0x545f70;
        M32(self + 0x2c) = 0x720000; M32(0x720008) = 0x52ab90;
        M32(end) = M32(end + 4) = end;
        for (unsigned i = 0; i < 16; ++i) {
            uint32_t slot = 0x740000 + i * 0x40, label = 0x750000 + i / 2 * 0x100, value = 0x760000 + i * 0x100;
            M32(slot) = 0x720200; M32(0x720204) = 0x51d3a0;
            M32(slot + 0x14) = label; M32(slot + 0x18) = value; trig_append(end, slot);
            M32(label + 0x2c) = label + 0x80; MF32(label + 0xb8) = 1;
            M32(value + 0x2c) = value + 0x80; MF32(value + 0x9c) = i % 2 ? 400 : 200;
        }
        M32(self + 0x4c) = 2; M32(self + 0x50) = 8;
    }
    end = self + 0x3c; M32(end) = M32(end + 4) = end;
    for (unsigned i = 0; i < (M32(self + 0xe4) == 1 ? 36u : 38u); ++i) {
        uint32_t datum = galloc(0x120); M32(datum + 0x11c) = i / 2; trig_append(end, datum);
    }
    M32(self + 0x44) = M32(end) - 4;
}
static uint32_t trig_guest(Cpu *c, uint32_t fn, int n, const uint32_t *args, int *handled) {
    *handled = 1;
    if (fn >= HOSTFN_BASE && fn < HOSTFN_BASE + 4 * trig_ncallbacks) {
        Cpu q = *c;
        for (int i = n - 1; i >= 0; --i) { q.esp -= 4; M32(q.esp) = args[i]; }
        q.esp -= 4; M32(q.esp) = 0;
        trig_callbacks[(fn - HOSTFN_BASE) / 4](&q); return q.eax;
    }
    if (fn == 0x652ad0) return galloc(args[0]);
    if (fn == 0x51d330) { M32(c->ecx) = 0x89c244; return c->ecx; }
    if (fn == 0x59bd30) { trig_append(c->ecx + 0x10, args[0]); return 0; }
    if (fn == 0x52ab90) {
        uint32_t self = c->ecx - 0x2c, datum = M32(self + 0x40) - 4;
        for (unsigned i = 14; i < 16; ++i) {
            Cpu q = *c; q.ecx = 0x740000 + i * 0x40;
            uint32_t a[] = {datum, 1}; int h;
            trig_guest(&q, M32(M32(q.ecx) + 4), 2, a, &h);
        }
        return 0;
    }
    if (fn == 0x51d3a0 || fn == 0x5739d0) return 0;
    if (fn == 0x50d880) {
        const char *s = (const char *)G + args[0];
        if (!strcmp(s, "On") || !strcmp(s, "Off")) snprintf(trig_drawn, sizeof trig_drawn, "%s", s);
        return 0;
    }
    *handled = 0; return 0;
}
static void trig_toggle(uint32_t self) {
    CHECK(trig_rows(self) == (M32(self + 0xe4) == 1 ? 38u : 40u));
    uint32_t datum = M32(self + 0x40) - 4;
    CHECK(is_trigger(datum)); if (!is_trigger(datum)) return; M32(self + 0x44) = datum;
    Cpu c = {.ecx = self, .esp = 0x650000}; uint32_t args[] = {0x936a6a7f, 0, 0, 0};
    call_guest(&c, M32(M32(self) + 4), 4, args);
    CHECK(trig_effect == adaptive.value);
    CHECK(!strcmp(trig_drawn, adaptive.value ? "On" : "Off"));
    M32(self + 0x44) = M32(self + 0x3c) - 4;
}

static void trig_redraw(Cpu *c) {
    Cpu q = *c; q.ecx += 0x2c; int handled;
    trig_guest(&q, 0x52ab90, 0, NULL, &handled);
}
