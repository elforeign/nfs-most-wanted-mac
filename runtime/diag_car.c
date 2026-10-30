// BOOT_DIAG-only observations; no render decisions are replaced.
#include "host.h"
#include "diag_car.h"
#include <stdarg.h>
#include <stdlib.h>

uint32_t call_guest(Cpu *c, uint32_t fn, int n, const uint32_t *args);
uint32_t car85_diag_model_hash(Cpu *c, uint32_t ride, uint32_t part, int lod);
typedef struct CarSampleClock {
    struct CarSampleClock *next;
    uint32_t self, ride, type, view, reflection;
    uint64_t frame;
} CarSampleClock;
static _Thread_local CarSampleClock *clocks;
static _Thread_local DiagCarRender *active;
static uint32_t every = 120;

void diag_car_init(void) {
    const char *value = getenv("BOOT_DIAG_CAR_EVERY");
    char *end;
    unsigned long n = value ? strtoul(value, &end, 10) : 0;
    every = value && *value && !*end && n > 0 && n <= UINT32_MAX ? (uint32_t)n : 120;
}

void diag_car_begin(Cpu *c, DiagCarRender *s, uint64_t frame) {
    memset(s, 0, sizeof *s);
    s->previous = active;
    active = NULL;                         // unsampled nested calls cannot contaminate their parent
    s->self = c->ecx;
    s->ride = M32(s->self + 0x84);
    s->view = arg(c, 0);
    s->view_id = M32(s->view + 4);
    s->reflection = arg(c, 8);
    s->pixel_min = (int32_t)M32(s->view + 0x24);
    s->frame = frame;
    uint32_t type = s->ride ? M32(s->ride) : UINT32_MAX;
    CarSampleClock *p;
    for (p = clocks; p; p = p->next)
        if (p->self == s->self && p->ride == s->ride && p->type == type &&
            p->view == s->view_id && p->reflection == s->reflection) break;
    if (p && frame >= p->frame && frame - p->frame < every) return;
    if (!p) {
        p = calloc(1, sizeof *p);
        if (!p) return;
        p->self = s->self; p->ride = s->ride; p->type = type;
        p->view = s->view_id; p->reflection = s->reflection;
        p->next = clocks; clocks = p;
    }
    p->frame = frame;
    s->sampled = 1;
    active = s;
}

void diag_car_observe(Cpu *c, uint32_t site) {
    DiagCarRender *s = active;
    if (!s) return;
    switch (site) {
    case 0x0074E213: s->pixel = (int32_t)c->ebx; s->seen |= 1; break;
    case 0x0074E34B: s->visible = (int32_t)c->eax; s->seen |= 2; break;
    case 0x0074E5A2:
        s->body_lod = (int32_t)M32(c->esp + 0x14);
        s->tire_lod = (int32_t)M32(c->esp + 0x58);
        s->seen |= 4;
        break;
    case 0x0074E694: s->inside = c->eax != 0; s->seen |= 8; break;
    }
}

void diag_car_reject(uint32_t site) {
    if (active && !active->reject) active->reject = site;
}

static const char *reason(uint32_t site, unsigned result) {
    switch (site) {
    case 0x0074E223: return "pixel-size";
    case 0x0074E34D: return "visibility";
    case 0x0074E5B1: return "lod";
    case 0x0074E696: return "camera-inside";
    case 0x0074E2D4: case 0x0074E2DC: case 0x0074E2E8: case 0x0074E2F0: return "frame-allocation";
    default: return result ? "none" : "other-false";
    }
}

static void append(char *line, size_t capacity, size_t *used, const char *fmt, ...) {
    if (*used >= capacity) return;
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(line + *used, capacity - *used, fmt, ap);
    va_end(ap);
    if (n > 0) *used += (size_t)n < capacity - *used ? (size_t)n : capacity - *used;
}

static const char *observed(char buf[16], int seen, int32_t value) {
    if (!seen) return "na";
    snprintf(buf, 16, "%d", value);
    return buf;
}

void diag_car_end(Cpu *c, DiagCarRender *s) {
    active = s->previous;
    if (!s->sampled) return;
    unsigned result = c->eax & 0xff;
    uint32_t self = s->self, type = s->ride ? M32(s->ride) : UINT32_MAX;
    uint32_t types = M32(0x009B09D8u);
    char name[33] = "unknown";
    if (types && type < 65536) {
        memcpy(name, G + types + type * 0xd0, 32);
        name[32] = 0;
        for (char *p = name; *p; ++p) if ((unsigned char)*p <= 32 || (unsigned char)*p > 126) *p = '_';
    }
    char line[24576], pixel[16], visible[16], body[16], tire[16], inside[16];
    size_t used = 0;
    append(line, sizeof line, &used,
           "car-reject: frame=%llu self=%08X car=%s type=%d view=%u refl=%u result=%u reason=%s site=%08X "
           "bounds=(%.9g,%.9g,%.9g)..(%.9g,%.9g,%.9g) radius=%.9g pixel=%s minpixel=%d "
           "lod=%d..%d chosen=%s/%s visible=%s camera_inside=%s models=",
           (unsigned long long)s->frame, self, name, (int32_t)type, s->view_id, s->reflection, result,
           reason(s->reject, result), s->reject,
           MF32(self + 0x90), MF32(self + 0x94), MF32(self + 0x98),
           MF32(self + 0xa0), MF32(self + 0xa4), MF32(self + 0xa8), MF32(self + 0x17a8),
           observed(pixel, s->seen & 1, s->pixel), s->pixel_min,
           (int32_t)M32(self + 0x1628), (int32_t)M32(self + 0x162c),
           observed(body, s->seen & 4, s->body_lod), observed(tire, s->seen & 4, s->tire_lod),
           observed(visible, s->seen & 2, s->visible), observed(inside, s->seen & 8, s->inside));

    // Failed connections have already freed their eModel in UpdateCarParts.
    // Recover requested hashes with the original read-only part/range queries.
    // Use a private Cpu and unused stack space, preserving the caller's registers.
    Cpu probe = *c;
    probe.esp -= 16;
    uint32_t scratch = probe.esp;
    int32_t ranges[0x4c][2];
    unsigned char special[0x4c] = {0};
    for (unsigned part = 0; part < 0x4c; ++part) {
        if (!s->ride || !M32(s->ride + 0x48 + part * 4)) continue;
        const uint32_t args[] = {part, scratch, scratch + 4, M32(0x00925E90u) == 3};
        probe.ecx = s->ride;
        special[part] = call_guest(&probe, 0x00739B40u, 4, args) != 0;
        ranges[part][0] = (int32_t)M32(scratch);
        ranges[part][1] = (int32_t)M32(scratch + 4);
    }
    for (int lod = 0; lod < 5; ++lod) {
        append(line, sizeof line, &used, "%sL%d[", lod ? ";" : "", lod);
        unsigned entries = 0;
        for (unsigned part = 0; part < 0x4c; ++part) {
            uint32_t model = M32(self + 0xb84 + part * 0x14 + lod * 4) & ~3u;
            uint32_t hash = model ? M32(model + 8) : 0;
            if (!model && s->ride && M32(s->ride + 0x48 + part * 4)) {
                int requested = lod;
                if (special[part]) {
                    if (requested < ranges[part][0]) requested = ranges[part][0];
                    if (requested > ranges[part][1]) requested = ranges[part][1];
                }
                hash = car85_diag_model_hash(&probe, s->ride, M32(s->ride + 0x48 + part * 4), requested);
            }
            uint32_t solid = model ? M32(model + 0xc) : 0;
            if (!hash && !model) continue;
            append(line, sizeof line, &used, "%s%02X:%08X->%s@%08X/%08X", entries++ ? "," : "",
                   part, hash, solid ? "found" : "null", model, solid);
        }
        append(line, sizeof line, &used, "]");
    }
    diag_event("%s", line);
}
