#ifndef CAR_AUDIO_CSIS_H
#define CAR_AUDIO_CSIS_H

#include <stddef.h>
#include <stdint.h>

/* Mac port helper only; dormant additions leave Windows behavior unchanged.
 * Dormant CSIS boundary for the exact A4 interface manifest. Each ID is the
 * original bounded wire ID: u16 preferred system, u16 signature, ASCII name
 * with its terminating zero. ClassHandle kind is its LOW byte only; relocation
 * padding is not part of the kind. Members exclude the class handle.
 * The bank inspector must supply EVERY export and imported control class,
 * deriving member counts from the actual template/control state, not a name.
 */
typedef struct {
    const uint8_t *id;
    size_t id_size;
    uint16_t member_words;
    uint8_t kind;
} CarAudioCsisInterface;

enum {
    CAR_AUDIO_CSIS_OK = 0,
    CAR_AUDIO_CSIS_INVALID = -1,
    CAR_AUDIO_CSIS_IDENTITY = -2,
    CAR_AUDIO_CSIS_LAYOUT = -3,
    CAR_AUDIO_CSIS_CAR_MAP = -4,
    CAR_AUDIO_CSIS_ENGINE_MAP = -5,
    CAR_AUDIO_CSIS_UNBOUND = -6
};

/* Strict source profile check, independent of caller classification flags.
 * CAR 0DD6/100 and CAR_ENG 76EF/100 remain separate generic-admission blockers.
 * The explicit execution registry below composes their dormant bindings with
 * the runtime's instance ownership; adding a native registry row cannot do so.
 * Distinct source contracts:
 * - CAR: source 311C/0DD6, 100 packed i32 members; native 383F/198B, 26.
 *   Preserve the source units, ranges, defaults and engine/exhaust direction.
 *   Do not truncate, pad or rename the source block.
 * - CAR_ENG: source 311C/76EF, 100 packed i32 members, registered at 00500490;
 *   native registration 004AD0F0 has no route for this export. It needs its
 *   own controller binding and paired-engine execution, in addition to CAR.
 * No new instruction adapter was classified by A4. This boundary supplies no
 * speculative instruction translation or engine member values.
 */
int car_audio_csis_check(const CarAudioCsisInterface *source);

/* Identity-preserving lookup. Registry order and preferred-system/fallback
 * search match SetFast 0083132B. The selected target must also have the exact
 * word count. index is written only on success and may be NULL.
 */
int car_audio_csis_resolve(const CarAudioCsisInterface *source,
                          const CarAudioCsisInterface *targets, size_t count,
                          size_t *index);

/* Check the COMPLETE manifest before installation, relocation, allocation or
 * any guest write. Pure, no callbacks, owned handles or retained pointers.
 * This checks CSIS only: success is NOT executable/sound admission. A1 identity,
 * A4 instruction/import/state bounds, player backend and instance lifetime
 * proofs remain mandatory. Integration must keep the input and registry stable
 * between preflight and the normal loader under its existing lock.
 * No ID/signature/member, native registry or native bank is rewritten here.
 */
int car_audio_csis_preflight(const CarAudioCsisInterface *sources, size_t count,
                            const CarAudioCsisInterface *targets, size_t target_count);

/* Explicit dormant execution registry. Unlike generic preflight, this accepts
 * the two proved source engine layouts ONLY against exact source descriptors.
 * It does not alter the generic admission barrier or any native registry.
 */
int car_audio_csis_execution_resolve(const CarAudioCsisInterface *source,
    const CarAudioCsisInterface *targets, size_t count, size_t *index);

#endif
