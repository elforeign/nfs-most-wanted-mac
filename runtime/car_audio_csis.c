#include "car_audio_csis.h"
#include <string.h>

static uint16_t word(const uint8_t *p)
{
    return (uint16_t)((unsigned)p[0] | (unsigned)p[1] << 8);
}

static int valid(const CarAudioCsisInterface *i)
{
    if (!i || !i->id || i->id_size < 6 || i->id_size > 260 || i->kind != 1)
        return 0;
    for (size_t n = 4; n < i->id_size - 1; ++n)
        if (i->id[n] < 32 || i->id[n] > 126) return 0;
    return i->id[i->id_size - 1] == 0;
}

int car_audio_csis_check(const CarAudioCsisInterface *source)
{
    if (!valid(source)) return CAR_AUDIO_CSIS_INVALID;
    if (word(source->id) != 0x311c) return CAR_AUDIO_CSIS_IDENTITY;
    const char *name = (const char *)source->id + 4;
    uint16_t signature, members;
    int result = CAR_AUDIO_CSIS_OK;
    if (!strcmp(name, "CAR")) {
        signature = 0x0dd6; members = 100; result = CAR_AUDIO_CSIS_CAR_MAP;
    } else if (!strcmp(name, "CAR_ENG")) {
        signature = 0x76ef; members = 100; result = CAR_AUDIO_CSIS_ENGINE_MAP;
    } else if (!strcmp(name, "CAR_SWTN")) {
        signature = 0x7859; members = 7;
    } else if (!strcmp(name, "CAR_WHINE")) {
        signature = 0x603f; members = 9;
    } else if (!strcmp(name, "CAR_Sputter")) {
        signature = 0x3686; members = 11;
    } else if (!strcmp(name, "CAR_SputOutput")) {
        signature = 0x1901; members = 1;
    } else return CAR_AUDIO_CSIS_IDENTITY;
    if (word(source->id + 2) != signature) return CAR_AUDIO_CSIS_IDENTITY;
    if (source->member_words != members) return CAR_AUDIO_CSIS_LAYOUT;
    return result;
}

int car_audio_csis_resolve(const CarAudioCsisInterface *source,
                          const CarAudioCsisInterface *targets, size_t count,
                          size_t *index)
{
    int result = car_audio_csis_check(source);
    if (result) return result;
    if ((!targets && count) || count > 65536) return CAR_AUDIO_CSIS_INVALID;
    /* Validate the whole borrowed registry before looking through it. */
    for (size_t n = 0; n < count; ++n)
        if (!valid(targets + n)) return CAR_AUDIO_CSIS_INVALID;
    for (unsigned fallback = 0; fallback < 2; ++fallback) {
        for (size_t n = 0; n < count; ++n) {
            const CarAudioCsisInterface *target = targets + n;
            if ((!fallback && word(target->id) != word(source->id)) ||
                word(target->id + 2) != word(source->id + 2) ||
                strcmp((const char *)target->id + 4,
                       (const char *)source->id + 4)) continue;
            /* Do not skip an unsafe first match to reach a nicer duplicate:
             * the normal loader would bind this first match instead.
             */
            if (target->member_words != source->member_words)
                return CAR_AUDIO_CSIS_LAYOUT;
            if (index) *index = n;
            return CAR_AUDIO_CSIS_OK;
        }
    }
    return CAR_AUDIO_CSIS_UNBOUND;
}

int car_audio_csis_preflight(const CarAudioCsisInterface *sources, size_t count,
                            const CarAudioCsisInterface *targets, size_t target_count)
{
    if (!sources || !count || count > 65536) return CAR_AUDIO_CSIS_INVALID;
    for (size_t n = 0; n < count; ++n) {
        int result = car_audio_csis_resolve(sources + n, targets, target_count, NULL);
        if (result) return result;
    }
    return CAR_AUDIO_CSIS_OK;
}

int car_audio_csis_execution_resolve(const CarAudioCsisInterface *source,
    const CarAudioCsisInterface *targets, size_t count, size_t *index)
{
    int error = car_audio_csis_check(source);
    if (error != CAR_AUDIO_CSIS_CAR_MAP && error != CAR_AUDIO_CSIS_ENGINE_MAP)
        return car_audio_csis_resolve(source, targets, count, index);
    if ((!targets && count) || count > 65536) return CAR_AUDIO_CSIS_INVALID;
    for (size_t n = 0; n < count; ++n)
        if (!valid(targets + n)) return CAR_AUDIO_CSIS_INVALID;
    for (size_t n = 0; n < count; ++n) {
        const CarAudioCsisInterface *t = targets + n;
        if (t->id_size != source->id_size ||
            memcmp(t->id, source->id, source->id_size)) continue;
        if (t->member_words != source->member_words) return CAR_AUDIO_CSIS_LAYOUT;
        if (index) *index = n;
        return 0;
    }
    return CAR_AUDIO_CSIS_UNBOUND;
}
