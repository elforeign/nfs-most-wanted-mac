#ifndef CAR_AUDIO_ENGINE_CLASS_H
#define CAR_AUDIO_ENGINE_CLASS_H
#include "car_audio_csis.h"

/* Dormant Mac helper. No native registration or sound admission is installed.
 * Members exclude the handle and are little-endian signed i32 source units.
 */
enum { CAR_AUDIO_ENGINE_WORDS = 100, CAR_AUDIO_ENGINE_BYTES = 400,
       CAR_AUDIO_ENGINE_CAR = 0, CAR_AUDIO_ENGINE_ENG = 1 };
typedef struct {
    uint8_t *members;             /* borrowed, stable, noncopyable while bound */
    unsigned kind;
    int32_t identity;             /* construction-time w0 after clamping */
} CarAudioEngineClass;

/* Exact source IDs (311C/0DD6 CAR, 311C/76EF CAR_ENG), kind 1, 100 words.
 * These descriptors do not bypass car_audio_csis_check/preflight.
 */
const CarAudioCsisInterface *car_audio_engine_class_interface(unsigned kind);
int car_audio_engine_class_matches(unsigned kind, const CarAudioCsisInterface *);
/* All-zero source-unit defaults, with validity w12=1. No calibration guessed.
 * Callers may replace every member before construction. */
void car_audio_engine_class_defaults(int32_t words[CAR_AUDIO_ENGINE_WORDS]);
int car_audio_engine_class_construct(CarAudioEngineClass *, unsigned kind,
    const CarAudioCsisInterface *, uint8_t *members, size_t bytes,
    const int32_t *words, size_t count);
/* Exact 100-word updates are transactional; identity w0 is immutable.
 * Same-object source/destination overlap is safe. No heap or guest pointers.
 */
int car_audio_engine_class_update(CarAudioEngineClass *, const int32_t *, size_t count);
void car_audio_engine_class_destroy(CarAudioEngineClass *);

/* Effective calibration blocks, in decoded source layout through +B0.
 * Both must be supplied, including inherited/default values. Controls holds
 * w0..29 in source units, independently for each export; no live game reads.
 * Tail mapping follows controller 00546640/00548090. w84 comes from primary
 * +74, even though w85..91 come from secondary +78..90.
 */
int car_audio_engine_class_inputs(int32_t words[CAR_AUDIO_ENGINE_WORDS],
    const int32_t *controls, size_t count,
    const uint8_t *primary, size_t primary_bytes,
    const uint8_t *secondary, size_t secondary_bytes);
int car_audio_engine_class_rpm(double rpm, double minimum, double maximum,
                              int32_t *value);
int car_audio_engine_class_pitch(double multiplier, int32_t *value);
#endif
