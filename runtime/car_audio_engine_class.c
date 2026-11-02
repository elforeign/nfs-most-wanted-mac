#include "car_audio_engine_class.h"
#include <math.h>
#include <string.h>

static const uint8_t car_id[] = {0x1c,0x31,0xd6,0x0d,'C','A','R',0};
static const uint8_t eng_id[] = {0x1c,0x31,0xef,0x76,'C','A','R','_','E','N','G',0};
static const CarAudioCsisInterface interfaces[] = {
    {car_id, sizeof(car_id), 100, 1}, {eng_id, sizeof(eng_id), 100, 1}
};
const CarAudioCsisInterface *car_audio_engine_class_interface(unsigned kind)
{
    return kind < 2 ? interfaces + kind : NULL;
}
int car_audio_engine_class_matches(unsigned kind, const CarAudioCsisInterface *s)
{
    const CarAudioCsisInterface *t = car_audio_engine_class_interface(kind);
    return t && s && s->id && s->kind == t->kind &&
        s->member_words == t->member_words && s->id_size == t->id_size &&
        !memcmp(s->id, t->id, t->id_size);
}
static int32_t read32(const uint8_t *p)
{
    uint32_t n = (uint32_t)p[0] | (uint32_t)p[1]<<8 |
                 (uint32_t)p[2]<<16 | (uint32_t)p[3]<<24;
    return n <= INT32_MAX ? (int32_t)n : -1-(int32_t)(UINT32_MAX-n);
}
static int32_t clamp(int32_t value, int32_t lo, int32_t hi)
{
    return value < lo ? lo : value > hi ? hi : value;
}
/* Constructor 00527110 (CAR_ENG 00529090 has identical ranges). */
static int32_t member(unsigned n, int32_t value)
{
    int32_t lo = 0, hi = 32767;
    if (n == 0) hi = 99;
    else if (n == 1 || (n >= 30 && n <= 67)) hi = 10000;
    else if (n == 2 || n == 3 || (n >= 6 && n <= 9) || n == 17 || n == 23) hi = 1024;
    else if (n == 4 || n == 5 || n == 13 || n == 26) { lo = -32767; hi = 0; }
    else if (n == 11) hi = 4096;
    else if (n == 12) hi = 2;
    else if (n == 15 || n == 28) hi = 65536;
    else if (n == 16) { lo = -16383; hi = 16383; }
    else if (n >= 18 && n <= 20) { lo = -10000; hi = 10000; }
    else if (n == 21 || n == 22 || n == 29) hi = 25000;
    else if (n == 24) hi = 1;
    return clamp(value, lo, hi);
}
void car_audio_engine_class_defaults(int32_t words[100])
{
    if (words) { memset(words, 0, 400); words[12] = 1; }
}
static void store(uint8_t *dest, const int32_t *words)
{
    uint8_t copy[400] = {0};
    for (unsigned n = 0; n < 100; ++n) {
        uint32_t value = (uint32_t)member(n, words[n]);
        for (unsigned j = 0; j < 4; ++j) copy[4*n+j] = (uint8_t)(value>>(8*j));
    }
    memcpy(dest, copy, sizeof(copy));
}
int car_audio_engine_class_construct(CarAudioEngineClass *p, unsigned kind,
    const CarAudioCsisInterface *s, uint8_t *dest, size_t bytes,
    const int32_t *words, size_t count)
{
    if (!p || p->members || !dest || bytes != 400 || !words || count != 100 ||
        !car_audio_engine_class_matches(kind, s)) return CAR_AUDIO_CSIS_INVALID;
    store(dest, words);
    *p = (CarAudioEngineClass){dest, kind, read32(dest)};
    return 0;
}
int car_audio_engine_class_update(CarAudioEngineClass *p, const int32_t *words, size_t count)
{
    if (!p || !p->members || p->kind > 1 || !words || count != 100 ||
        words[0] != p->identity) return CAR_AUDIO_CSIS_INVALID;
    store(p->members, words);
    return 0;
}
void car_audio_engine_class_destroy(CarAudioEngineClass *p)
{
    if (!p) return;
    /* Caller detaches CSIS references before releasing borrowed member storage. */
    memset(p, 0, sizeof(*p));
}
int car_audio_engine_class_inputs(int32_t words[100], const int32_t *controls,
    size_t count, const uint8_t *primary, size_t psize,
    const uint8_t *secondary, size_t ssize)
{
    if (!words || !controls || count != 30 || !primary || !secondary ||
        psize < 0xb4 || ssize < 0xb4) return CAR_AUDIO_CSIS_INVALID;
    int32_t copy[100];
    memcpy(copy, controls, 30*sizeof(*copy));
    for (unsigned n = 0; n < 19; ++n) {
        copy[30+n] = read32(secondary+0x28+4*n);
        copy[49+n] = read32(primary+0x28+4*n);
    }
    for (unsigned n = 0; n < 8; ++n) {
        copy[68+n] = read32(secondary+0x94+4*n); /* gain block +94..B0 */
        copy[76+n] = read32(primary+0x94+4*n);
        copy[84+n] = read32((n ? secondary : primary)+0x74+4*n);
        copy[92+n] = read32(primary+0x74+4*n);
    }
    memcpy(words, copy, sizeof(copy));
    return 0;
}
int car_audio_engine_class_rpm(double rpm, double minimum, double maximum, int32_t *out)
{
    if (!out || !isfinite(rpm) || !isfinite(minimum) || !isfinite(maximum))
        return CAR_AUDIO_CSIS_INVALID;
    double v = minimum+(maximum-minimum)*(rpm-1000.0)/9000.0;
    if (!isfinite(v)) return CAR_AUDIO_CSIS_INVALID;
    *out = v < 0 ? 0 : v > 10000 ? 10000 : (int32_t)v;
    return 0;
}
int car_audio_engine_class_pitch(double multiplier, int32_t *out)
{
    if (!out || !isfinite(multiplier)) return CAR_AUDIO_CSIS_INVALID;
    double v = trunc(16383.0*multiplier)-16383.0;
    *out = v < -16383 ? -16383 : v > 16383 ? 16383 : (int32_t)v;
    return 0;
}
