// Nearby scenery lights add an independent diffuse term to cars at night.
// The shipped three-light rig (including its specular light) is never modified.
// Sum in-range lamps, using the scenery falloff or the street-flare radius fade,
// then apply the existing 0.75 gain and 0.8 total luminance ceiling.
// Lamp positions and mesh vertices share the rig's car-local space.
#include "host.h"
#include "auxlights.h"
#include "car_locallight.h"
#include "daynight.h"
#include <math.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#ifndef X360LC_09C0DE10_C255
#if __has_include("gen/x360_shader_lits.inc")
#include "gen/x360_shader_lits.inc"   // the scenery light shader's constants, from the player's files
#endif
#endif

unsigned materials_aux_serial(void);
int materials_aux_section(unsigned slot, uint32_t *id, uint32_t *instances, uint32_t *count);

typedef struct { float position[3], radius, rgb[3], gain, soft; } Lamp;   // falloff gain / (2 d2 + soft)
static Lamp *lamps;
static unsigned lamp_count, lamp_room, misplaced, serial_seen, street_count;
static uint32_t packs_seen;

#define FLARE_PACKS 0x0091757Cu   // list of loaded flare packs (LoaderLights 00501A10 links, 00501BF0 unlinks)
#define STREET_RADIUS 22.f
static const float street_rgb[3] = {182 / 255.f, 215 / 255.f, 240 / 255.f};
int car_locallight_on;
static int built;

uint64_t diag_current_frame(void);
void car_locallight_diag(unsigned entry, int work) {
    static uint64_t frame = UINT64_MAX;
    static unsigned calls[CAR_LOCAL_DIAG_COUNT], works[CAR_LOCAL_DIAG_COUNT];
    uint64_t now = diag_current_frame() + 1;
    if (now != frame) {
        if (frame != UINT64_MAX && frame % 60 == 0)
            diag_event("car_locallight_work: frame=%llu enabled=%d rotate=%u/%u select=%u/%u material=%u/%u "
                       "refresh=%u/%u lookup=%u/%u apply=%u/%u clone=%u/%u",
                       (unsigned long long)frame, car_locallight_on,
                       calls[0], works[0], calls[1], works[1], calls[2], works[2], calls[3], works[3],
                       calls[4], works[4], calls[5], works[5], calls[6], works[6]);
        frame = now;
        memset(calls, 0, sizeof calls);
        memset(works, 0, sizeof works);
    }
    ++calls[entry];
    works[entry] += work;
}

#define LIGHT_GAIN .75f
#define TOTAL_LUMINANCE .8f

static float luma(const float *c) { return .2126f * c[0] + .7152f * c[1] + .0722f * c[2]; }

static void add(const Lamp *lamp) {
    // The same world light is listed by every scenery instance it lights: keep one of each.
    for (unsigned k = 0; k < lamp_count; ++k) {
        const Lamp *o = &lamps[k];
        if (fabsf(o->position[0] - lamp->position[0]) < .05f && fabsf(o->position[1] - lamp->position[1]) < .05f &&
            fabsf(o->position[2] - lamp->position[2]) < .05f && o->radius == lamp->radius &&
            o->gain == lamp->gain && o->soft == lamp->soft && !memcmp(o->rgb, lamp->rgb, sizeof o->rgb)) return;
    }
    if (lamp_count == lamp_room) {
        Lamp *grown = realloc(lamps, (lamp_room ? lamp_room * 2 : 256) * sizeof *lamps);
        if (!grown) return;
        lamps = grown;
        lamp_room = lamp_room ? lamp_room * 2 : 256;
    }
    lamps[lamp_count++] = *lamp;
}

// One 64-byte scenery instance: bounds +0, flags +0x18, position +0x20, 3x3 basis (int16 / 8192) +0x2c; flag
// 0x200 = no transform (SceneryInstance::GetMatrix 006BEEB0, as materials.m reads it for the scenery draw).
static void instance_lights(uint32_t id, uint32_t instance) {
    const AuxLightContext *context = auxlights_find(id, G + instance);
    if (!context) return;
    float m[4][3] = {{1, 0, 0}, {0, 1, 0}, {0, 0, 1}, {0, 0, 0}}, bounds[6];
    if (!(M32(instance + 0x18) & 0x200)) {
        int16_t basis[9];
        memcpy(basis, G + instance + 0x2c, sizeof basis);
        for (int r = 0; r < 3; ++r) for (int k = 0; k < 3; ++k) m[r][k] = basis[r * 3 + k] / 8192.f;
        memcpy(m[3], G + instance + 0x20, 12);
    }
    memcpy(bounds, G + instance, sizeof bounds);
    // The shader weights a light's green and blue by its constant's x and y (red by 1).
    static float tint[3] = {1, 1, 1};
    static int tint_read;
    if (!tint_read) {
        tint_read = 1;
#ifdef X360LC_09C0DE10_C255
        float g, b;
        if (sscanf(X360LC_09C0DE10_C255, "float4(%f,%f", &g, &b) == 2 && g >= 0 && g <= 1 && b >= 0 && b <= 1) {
            tint[1] = g;
            tint[2] = b;
        }
#endif
    }
    for (unsigned j = 0; j < context->count; ++j) {
        const AuxLight *l = &context->lights[j];
        Lamp lamp = {.radius = l->radius, .rgb = {l->rgb[0] * tint[0], l->rgb[1] * tint[1], l->rgb[2] * tint[2]},
                     .gain = 3 * context->lights[0].intensity, .soft = l->intensity};
        int ok = isfinite(lamp.radius) && lamp.radius > 0 && isfinite(lamp.gain) && lamp.gain > 0 &&
                 isfinite(lamp.soft) && lamp.soft > 0;
        float gap = 0;
        for (int k = 0; k < 3; ++k) {
            lamp.position[k] = m[3][k] + l->position[0] * m[0][k] + l->position[1] * m[1][k] + l->position[2] * m[2][k];
            ok &= isfinite(lamp.position[k]) && isfinite(lamp.rgb[k]) && lamp.rgb[k] >= 0;
            float d = fmaxf(bounds[k] - lamp.position[k], fmaxf(0, lamp.position[k] - bounds[k + 3]));
            gap += d * d;
        }
        // A light further than four radii from its own instance is in the wrong space (auxlights_local's bound).
        if (ok && !(gap <= 16 * lamp.radius * lamp.radius)) { ++misplaced; ok = 0; }
        if (ok) add(&lamp);
    }
}

// Changes whenever a flare pack is linked or unlinked.
static uint32_t pack_signature(void) {
    uint32_t signature = 0, n = 0;
    for (uint32_t pack = M32(FLARE_PACKS); pack && pack != FLARE_PACKS && n < 1024; pack = M32(pack), ++n)
        signature = signature * 31 + pack;
    return signature + n;
}

// Pack header: flare count +0x50, flare list head +0x58; flare: position +0x10, type +0x2c (13 = lamp post).
static void street_lamps(void) {
    unsigned packs = 0;
    for (uint32_t pack = M32(FLARE_PACKS); pack && pack != FLARE_PACKS && packs < 1024; pack = M32(pack), ++packs) {
        uint32_t head = pack + 0x58, left = M16(pack + 0x50);
        for (uint32_t flare = M32(head); flare && flare != head && left; flare = M32(flare), --left) {
            if (M8(flare + 0x2c) != 13) continue;
            Lamp lamp = {.radius = STREET_RADIUS, .rgb = {street_rgb[0], street_rgb[1], street_rgb[2]}};
            int ok = 1;
            for (int k = 0; k < 3; ++k) ok &= isfinite(lamp.position[k] = MF32(flare + 0x10 + 4 * k));
            if (!ok) continue;
            unsigned before = lamp_count;
            add(&lamp);
            street_count += lamp_count - before;
        }
    }
}

static void refresh(void) {
    if (diag_on) car_locallight_diag(CAR_LOCAL_REFRESH, car_locallight_on);
    if (!car_locallight_on) return;
    uint32_t packs = pack_signature();
    if (built && serial_seen == materials_aux_serial() && packs_seen == packs) return;
    serial_seen = materials_aux_serial();
    packs_seen = packs;
    built = 1;
    lamp_count = misplaced = street_count = 0;
    unsigned sections = 0;
    for (unsigned slot = 0; slot < 1024; ++slot) {
        uint32_t id, instances, count;
        if (!auxlights_enabled() || !materials_aux_section(slot, &id, &instances, &count) || count > 65536) continue;
        ++sections;
        for (uint32_t i = 0; i < count; ++i) instance_lights(id, instances + i * 64);
    }
    street_lamps();
    static unsigned reported = UINT32_MAX;
    if (diag_on && lamp_count != reported)
        diag_event("car_locallight: sections=%u lamps=%u street=%u misplaced=%u", sections, reported = lamp_count,
                   street_count, misplaced);
}

void car_locallight_gather(const float position[3], const float (*rows)[3], CarLocalLight *out) {
    if (!car_locallight_on) return;
    memset(out, 0, sizeof *out);
    refresh();
    float total = 0;
    for (unsigned i = 0; i < lamp_count; ++i) {
        const Lamp *l = &lamps[i];
        float d[3], d2 = 0;
        for (int k = 0; k < 3; ++k) { d[k] = l->position[k] - position[k]; d2 += d[k] * d[k]; }
        if (!(d2 < l->radius * l->radius)) continue;
        // Stable scenery-list order at the cap; distance ordering would swap
        // lights at midpoints. Each retained light fades to zero at its radius.
        if (out->lamps == CAR_LOCAL_LIGHT_MAX) continue;
        float w = (l->gain > 0 ? l->gain / (2 * d2 + l->soft) : 1) * (1 - d2 / (l->radius * l->radius));
        float distance = sqrtf(d2);
        if (!out->lamps || distance < out->nearest) out->nearest = distance;
        CarLocalLamp *lamp = &out->lights[out->lamps++];
        for (int k = 0; k < 3; ++k) {
            lamp->position[k] = rows ? d[0] * rows[k][0] + d[1] * rows[k][1] + d[2] * rows[k][2] : d[k];
            lamp->rgb[k] = l->rgb[k] * w;
            lamp->world[k] = l->position[k];
        }
        total += luma(lamp->rgb);
    }
    float gain = LIGHT_GAIN;
    if (total * gain > TOTAL_LUMINANCE) gain = TOTAL_LUMINANCE / total;
    for (unsigned i = 0; i < out->lamps; ++i)
        for (int k = 0; k < 3; ++k) out->lights[i].rgb[k] *= gain;
}

static float light_luma(const CarLocalLight *light) {
    float sum = 0;
    for (unsigned i = 0; i < light->lamps; ++i) sum += luma(light->lights[i].rgb);
    return sum;
}

// BOOT_DIAG: per captured frame and once a second, what the cars of that frame received.
uint64_t diag_current_frame(void);
static uint64_t counted_frame = UINT64_MAX;
static unsigned calls, lit, most;
static float nearest, added, level_seen, first[3];
static void count(const CarLocalLight *g, const float *p, float level, float sum) {
    uint64_t frame = diag_current_frame() + 1;   // present counts the frame before capturing it
    if (frame != counted_frame) {
        if (counted_frame != UINT64_MAX && (counted_frame % 60 == 0 || diag_shot_due(counted_frame))) {
            // Where the frame's first car was, and the loaded light closest to it (to find and check lit places).
            const Lamp *close = NULL;
            float best = 0;
            for (unsigned i = 0; i < lamp_count; ++i) {
                float d = 0;
                for (int k = 0; k < 3; ++k) d += (lamps[i].position[k] - first[k]) * (lamps[i].position[k] - first[k]);
                if (!close || d < best) { close = &lamps[i]; best = d; }
            }
            diag_event("car_locallight: frame=%llu cars=%u lit=%u lamps=%u nearest=%.2f added=%.4f level=%.4f "
                       "car=%.1f,%.1f,%.1f closest=%.1f,%.1f,%.1f at=%.1f loaded=%u",
                       (unsigned long long)counted_frame, calls, lit, most, (double)nearest, (double)added,
                       (double)level_seen, (double)first[0], (double)first[1], (double)first[2],
                       close ? (double)close->position[0] : 0, close ? (double)close->position[1] : 0,
                       close ? (double)close->position[2] : 0, close ? sqrt(best) : -1, lamp_count);
        }
        counted_frame = frame;
        calls = lit = most = 0;
        nearest = added = 0;
        memcpy(first, p, sizeof first);
    }
    ++calls;
    level_seen = level;
    if (!g->lamps) return;
    if (!lit || g->nearest < nearest) nearest = g->nearest;
    ++lit;
    if (g->lamps > most) most = g->lamps;
    if (sum > added) added = sum;
}

// Light contexts survive until the queued car is drawn. Keep the lamp beside the
// context, never inside guest rig storage. A frame stamp rejects recycled contexts.
typedef struct { uint32_t context; uint64_t frame; CarLocalLight light; } CarLampContext;
static CarLampContext initial_contexts[1024];
static CarLampContext *contexts = initial_contexts;
static unsigned context_room = 1024, context_used;
static uint64_t context_frame = UINT64_MAX;
static CarLocalLight selected;


static unsigned context_hash(uint32_t context) {
    // Guest contexts are aligned; mix both low and high address bits.
    context ^= context >> 16;
    context *= 0x7feb352du;
    context ^= context >> 15;
    return context;
}
static int grow_contexts(uint64_t frame) {
    unsigned room = context_room * 2;
    if (room <= context_room) return 0;
    CarLampContext *grown = calloc(room, sizeof *grown);
    if (!grown) return 0;   // retain every already queued part on allocation failure
    for (unsigned i = 0; i < context_room; ++i) {
        CarLampContext *old = &contexts[i];
        if (!old->context || old->frame != frame) continue;
        unsigned bucket = context_hash(old->context) & (room - 1);
        while (grown[bucket].context) bucket = (bucket + 1) & (room - 1);
        grown[bucket] = *old;
    }
    if (contexts != initial_contexts) free(contexts);
    contexts = grown;
    context_room = room;
    return 1;
}
// One hash lookup per source/target/select, never a linear table scan per part.
// A stale bucket terminates a probe: this frame only inserts, never deletes.
static CarLampContext *lookup_context(uint32_t context, int write) {
    if (diag_on) car_locallight_diag(CAR_LOCAL_LOOKUP, 1);
    if (!context) return NULL;
    uint64_t frame = diag_current_frame();
    if (context_frame != frame) { context_frame = frame; context_used = 0; }
    if (write && context_used >= context_room / 2 && !grow_contexts(frame)) return NULL;
    unsigned bucket = context_hash(context) & (context_room - 1);
    for (;;) {
        CarLampContext *slot = &contexts[bucket];
        if (!slot->context || slot->frame != frame) {
            if (!write) return NULL;
            ++context_used;
            *slot = (CarLampContext){.context = context, .frame = frame};
            return slot;
        }
        if (slot->context == context) {
            if (write) memset(&slot->light, 0, sizeof slot->light);
            return slot;
        }
        bucket = (bucket + 1) & (context_room - 1);
    }
}
void car_locallight_select(uint32_t context) {
    if (diag_on) car_locallight_diag(CAR_LOCAL_SELECT, car_locallight_on);
    if (!car_locallight_on) return;
    memset(&selected, 0, sizeof selected);
    CarLampContext *slot = lookup_context(context, 0);
    if (slot && slot->frame == diag_current_frame()) selected = slot->light;
}

// elRotateLightContext clones the car's world rig for every body/wheel part.
// Translate the lamp as a point, using the same inverse rotation as the rig's
// directions. Retaining its world position also handles nested part clones.
void car_locallight_clone(uint32_t target, uint32_t source, uint32_t local_world) {
    if (diag_on) car_locallight_diag(CAR_LOCAL_CLONE, car_locallight_on && target != 0);
    if (!car_locallight_on || !target) return;
    CarLampContext *from = lookup_context(source, 0);
    CarLocalLight light = {0};
    if (from && from->frame == diag_current_frame()) light = from->light;
    CarLampContext *to = lookup_context(target, 1);
    if (!to) return;
    to->light = light;
    if (!local_world || !light.lamps) return;
    for (unsigned i = 0; i < light.lamps; ++i) {
        float d[3];
        for (int k = 0; k < 3; ++k) d[k] = light.lights[i].world[k] - MF32(local_world + 48 + 4 * k);
        for (int r = 0; r < 3; ++r) {
            to->light.lights[i].position[r] = 0;
            for (int k = 0; k < 3; ++k) to->light.lights[i].position[r] += d[k] * MF32(local_world + 16 * r + 4 * k);
        }
    }
}

void f_00502030_orig(Cpu *c);
// Keep argument preservation and its register spills entirely on the On path.
__attribute__((noinline)) static void rotate_lit(Cpu *c) {
    uint32_t target = arg(c, 0), source = arg(c, 1), matrix = arg(c, 2);
    f_00502030_orig(c);
    car_locallight_clone(target, source, matrix);
}
__attribute__((noinline)) static void rotate_diag(Cpu *c) {
    car_locallight_diag(CAR_LOCAL_ROTATE, car_locallight_on);
    if (car_locallight_on) rotate_lit(c);
    else f_00502030_orig(c);
}
void f_00502030(Cpu *c) {
    if (diag_on) { rotate_diag(c); return; }
    if (!car_locallight_on) { f_00502030_orig(c); return; }
    rotate_lit(c);
}

void car_locallight_constants(float out[CAR_LOCAL_LIGHT_CONSTANTS][4]) {
    if (!car_locallight_on) return;
    memset(out[12], 0, 2 * sizeof out[0]);
    memset(out[16], 0, (CAR_LOCAL_LIGHT_CONSTANTS - 16) * sizeof out[0]);
    for (unsigned i = 0; i < selected.lamps; ++i) {
        const CarLocalLamp *lamp = &selected.lights[i];
        if (!(luma(lamp->rgb) > 0)) continue;
        unsigned index = i ? 14 + 2 * i : 12;
        memcpy(out[index], lamp->position, sizeof lamp->position);
        memcpy(out[index + 1], lamp->rgb, sizeof lamp->rgb);
        // First alpha gates the term; its value also bounds the shader loop.
        out[index + 1][3] = 1;
    }
    if (light_luma(&selected) > 0) out[13][3] = selected.lamps;
}

void car_locallight_apply(uint32_t context, uint32_t position, uint32_t local_world, float level) {
    if (diag_on) car_locallight_diag(CAR_LOCAL_APPLY, car_locallight_on && context != 0);
    if (!car_locallight_on || !context) return;
    CarLampContext *slot = lookup_context(context, 1);
    if (!slot) return;
    int postfx_look_variant(const char *v);
    if (!position || !(level > 0) || (diag_on && postfx_look_variant("carlightoff"))) return;
    float p[3], rows[3][3];
    for (int k = 0; k < 3; ++k) p[k] = MF32(position + 4 * k);
    if (local_world)
        for (int r = 0; r < 3; ++r) for (int k = 0; k < 3; ++k) rows[r][k] = MF32(local_world + 16 * r + 4 * k);
    CarLocalLight *g = &slot->light;
    car_locallight_gather(p, local_world ? rows : NULL, g);
    if (level > 1) level = 1;
    for (unsigned i = 0; i < g->lamps; ++i)
        for (int k = 0; k < 3; ++k) g->lights[i].rgb[k] *= level;
#ifdef CAR_LOCALLIGHT_BROKEN
    for (unsigned i = 0; i < g->lamps; ++i) memset(g->lights[i].rgb, 0, sizeof g->lights[i].rgb);
#endif
    if (diag_on && postfx_look_variant("carlightzero")) for (unsigned i = 0; i < g->lamps; ++i) memset(g->lights[i].rgb, 0, sizeof g->lights[i].rgb);
    if (diag_on && !postfx_look_variant("carlightzero")) count(g, p, level, light_luma(g));
}

__attribute__((constructor)) static void car_locallight_register(void) {
    const char *e = getenv("NFS_CAR_LOCALLIGHT");
    car_locallight_on = !(e && !strcmp(e, "0"));
    daynight_register_car_light(car_locallight_on ? car_locallight_apply : NULL);
}
