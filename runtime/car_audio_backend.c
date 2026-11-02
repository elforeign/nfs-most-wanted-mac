#include "car_audio_backend.h"
#include <limits.h>
#include <math.h>
#include <string.h>

static CarAudioVoice *lookup(CarAudioBackend *b, int32_t handle) {
    if (!b || handle < 0) return NULL;
    for (size_t i = 0; i < b->capacity; ++i)
        if (b->voices[i].owned && b->voices[i].handle == handle) return b->voices + i;
    return NULL; /* A stolen token remains ended; cannot address its replacement. */
}
static void retire(CarAudioVoice *v) {
    void (*fn)(void *) = v->sample.retire;
    void *owner = v->sample.owner;
    memset(v, 0, sizeof(*v));
    if (fn) fn(owner);
}
static int valid_sample(const CarAudioPCM *s) {
    /* Restrict frames so floor(cursor) and its successor are exact doubles. */
    return s && s->pcm && s->frames && s->frames <= UINT32_MAX &&
           isfinite(s->rate) && s->rate > 0.0f;
}
static void pan(CarAudioVoice *v, uint32_t offset) {
    uint16_t angle = (uint16_t)((uint32_t)v->base_angle + offset);
    double sine = sin((double)angle * (6.2831853071795864769 / 65536.0));
    v->pan[0] = (float)sqrt((1.0 - sine) * 0.5);
    v->pan[1] = (float)sqrt((1.0 + sine) * 0.5);
}
static uint64_t age_key(const CarAudioBackend *b, const CarAudioVoice *v) {
    /* 008B007F treats voices from the preceding command epoch as age zero. */
    return v->age == b->clock - 1 ? 0 : v->age;
}
static int allocate(CarAudioBackend *b, const CarAudioPCM *s, uint8_t priority,
                    uint16_t angle, int32_t *handle) {
    if (!b || !b->voices || !handle || !valid_sample(s) || priority > 100 ||
        b->next_handle > INT32_MAX || b->clock == UINT64_MAX) return -1;
    CarAudioVoice *slot = NULL;
    for (size_t i = 0; i < b->capacity; ++i) {
        CarAudioVoice *v = b->voices + i;
        if (!v->owned || v->ended) { slot = v; break; }
        if (v->priority >= 100.0f) continue;
        if (!slot || v->priority < slot->priority ||
            (v->priority == slot->priority &&
             (age_key(b, v) < age_key(b, slot) ||
              (age_key(b, v) == age_key(b, slot) && v->handle < slot->handle)))) slot = v;
    }
    if (!slot) return -1;
    if (slot->owned) retire(slot);
    *slot = (CarAudioVoice){.sample = *s, .handle = (int32_t)b->next_handle++,
        .age = b->clock, .priority = priority * 0.01f,
        .pitch = 1.0f, .volume = 1.0f, .dry = 1.0f,
        .base_angle = angle, .owned = 1};
    car_audio_filter_init(&slot->filter, b->rate);
    pan(slot, 0);
    *handle = slot->handle;
    return 0;
}
int car_audio_backend_init(CarAudioBackend *b, CarAudioVoice *storage, size_t count,
                           float rate, CarAudioResolve resolve, void *context) {
    if (!b || !storage || !count || count > SIZE_MAX / sizeof(*storage) ||
        !isfinite(rate) || rate <= 0.0f) return -1;
    memset(storage, 0, count * sizeof(*storage));
    *b = (CarAudioBackend){.voices = storage, .capacity = count, .rate = rate,
        .resolve = resolve, .context = context, .clock = 1};
    return 0;
}
int car_audio_backend_tick(CarAudioBackend *b) {
    if (!b || !b->voices || b->clock == UINT64_MAX) return -1;
    ++b->clock;
    return 0;
}
void car_audio_backend_destroy(CarAudioBackend *b) {
    if (!b) return;
    for (size_t i = 0; i < b->capacity; ++i)
        if (b->voices[i].owned) retire(b->voices + i);
    *b = (CarAudioBackend){0};
}
static int create(void *context, const CarAudioPlayerCreate *r, int32_t *handle) {
    CarAudioBackend *b = context;
    if (!b || !r || !handle || !b->resolve || r->sample < 0 ||
        (r->mode != 60 && r->mode != 80 && r->mode != 100)) return -1;
    CarAudioPCM sample = {0};
    if (b->resolve(b->context, r->bank_identity, r->sample, &sample) < 0) return -1;
    int rc = allocate(b, &sample, r->mode, (uint16_t)r->initialization[0], handle);
    if (rc < 0 && sample.retire) sample.retire(sample.owner);
    /* Options are delivered by the player after create. packed_offset is a
     * selector sentinel, not a PCM seek. Other angles are outside mono profile. */
    return rc;
}
static void release(void *context, int32_t handle) {
    CarAudioVoice *v = lookup(context, handle);
    if (v) retire(v);
}
static void pause_voice(void *context, int32_t handle) {
    CarAudioVoice *v = lookup(context, handle);
    if (v) v->paused = 1;
}
static void resume_voice(void *context, int32_t handle) {
    CarAudioVoice *v = lookup(context, handle);
    if (!v) return;
    /* 0089DAE3 restores +48, initialized to 1 by 0089D880; option 0
     * writes the pitch node, NOT +48. Do not restore the last option pitch. */
    v->pitch = 1.0f;
    v->paused = 0;
}
static void option(void *context, int32_t handle, uint8_t code, int32_t value) {
    CarAudioVoice *v = lookup(context, handle);
    if (!v) return;
    switch (code) {
    case 0: v->pitch = (float)value * (1.0f / 4096.0f); break;
    case 2: v->volume = (float)value * (1.0f / 32767.0f); break;
    case 3: pan(v, (uint32_t)value); break;
    case 5: v->wet = (float)value * (1.0f / 32767.0f); break;
    case 6: car_audio_filter_set_cutoff(&v->filter, (float)value); break;
    case 8: v->dry = (float)value * (1.0f / 32767.0f); break;
    default: break;
    }
}
static int status(void *context, int32_t handle) {
    CarAudioVoice *v = lookup(context, handle);
    return v && !v->ended;
}
const CarAudioPlayerBackend *car_audio_backend_callbacks(void) {
    static const CarAudioPlayerBackend callbacks = {
        create, release, pause_voice, resume_voice, option, status};
    return &callbacks;
}
int car_audio_backend_stock(CarAudioBackend *b, const CarAudioPCM *sample,
                            uint8_t priority, int32_t *handle) {
    return allocate(b, sample, priority, 0, handle);
}
void car_audio_backend_render(CarAudioBackend *b, float *dry, float *wet, size_t n) {
    if (!b || n > SIZE_MAX / (2 * sizeof(float))) return;
    for (size_t i = 0; i < b->capacity; ++i) {
        CarAudioVoice *v = b->voices + i;
        if (!v->owned || v->ended || v->paused) continue;
        double step = (double)v->pitch * v->sample.rate / b->rate;
        float dg = v->volume * v->dry, wg = v->volume * v->wet;
        for (size_t j = 0; j < n; ++j) {
            double length = (double)v->sample.frames;
            if (v->cursor < 0.0 || v->cursor >= length) {
                if (!v->sample.loop) { v->ended = 1; break; }
                v->cursor = fmod(v->cursor, length);
                if (v->cursor < 0.0) v->cursor += length;
                /* The negative sum can round up to exactly length. */
                if (v->cursor >= length) v->cursor = 0.0;
            }
            size_t a = (size_t)v->cursor, next = a + 1;
            if (next == v->sample.frames) next = v->sample.loop ? 0 : a;
            double fraction = v->cursor - (double)a;
            float pcm = (float)((1.0 - fraction) * v->sample.pcm[a] +
                               fraction * v->sample.pcm[next]);
            car_audio_filter_process(&v->filter, &pcm, &pcm, 1);
            for (size_t ch = 0; ch < 2; ++ch) {
                if (dry) dry[j * 2 + ch] += pcm * dg * v->pan[ch];
                if (wet) wet[j * 2 + ch] += pcm * wg * v->pan[ch];
            }
            v->cursor += step;
        }
        /* Publish one-shot completion on its last output frame, not a poll
         * or the next render block. Keep its resource until release/reuse. */
        if (!v->sample.loop && (v->cursor < 0.0 ||
            v->cursor >= (double)v->sample.frames)) v->ended = 1;
    }
}
