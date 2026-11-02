#ifndef CAR_AUDIO_PLAYER_H
#define CAR_AUDIO_PLAYER_H

#include <stddef.h>
#include <stdint.h>

/* Dormant ordinal-27 boundary. Wire data is little endian, never a host struct.
 * Mac port helper only; dormant additions leave Windows behavior unchanged.
 * The caller supplies the ORIGINAL selector rows, not the converted MW rows.
 * bank_identity binds the backend's resident sample collection; guest bank and
 * selector pointer words are retained but never dereferenced by this library.
 * No hook, mixer or allocation policy is installed here.
 */
typedef struct {
    uint64_t bank_identity;
    int16_t sample;
    uint8_t mode;
    uint32_t initialization[6]; /* row bytes 3..8, each promoted then << 8 */
    uint32_t packed_offset;     /* original row+8, modulo-2^32 offset */
    const uint8_t *options;     /* borrowed option records, stride 12 */
    uint8_t option_count;
} CarAudioPlayerCreate;

/* This is a SOURCE semantic backend contract, not a claim that MW's bank
 * factory implements these arguments. The decoded factory scales mode by
 * float32(0.01), queuing it to the voice's +34 field (0089DB7C/00899D3C).
 * Per-channel angle in degrees = initialization[i] * (360/65536);
 * the admitted mono profile consumes the first value. No scalar's downstream
 * mixer equivalence is asserted here; silently using MW defaults is invalid.
 * option receives exact signed source units (including azimuth ID 3), without
 * guessed saturation or conversion. Source setter 0089D8DD scales pitch by
 * 1/4096 and volume/wet/dry by float32(1/32767), without MW's input clamps;
 * dry/wet multiply volume, low-pass is passed as a float of the raw integer.
 * Azimuth 0089D98C wraps to 16 bits before scaling to degrees. Source object
 * pitch/volume/dry defaults are 1, wet is 0 (0089D880). Implementing these
 * consumer semantics on a concrete mixer remains the backend's duty.
 * create: 0 success with nonnegative voice (ZERO IS VALID), negative failure
 * with no retained resource. release consumes the voice exactly once, even
 * after status reports ended/error. pause/resume/option are infallible.
 * status: positive alive, zero ended, negative error. All callbacks synchronous;
 * no reentry; callbacks and context must outlive attachment.
 */
typedef struct {
    int (*create)(void *, const CarAudioPlayerCreate *, int32_t *voice);
    void (*release)(void *, int32_t voice);
    void (*pause)(void *, int32_t voice);
    void (*resume)(void *, int32_t voice);
    void (*option)(void *, int32_t voice, uint8_t code, int32_t value);
    int (*status)(void *, int32_t voice);
} CarAudioPlayerBackend;

enum { CAR_AUDIO_PLAYER_INVALID = -1, CAR_AUDIO_PLAYER_PROFILE = -2,
       CAR_AUDIO_PLAYER_BACKEND = -3 };

/* Caller-owned, zero-initialized, noncopyable sidecar. Wire state and selectors
 * are borrowed for its entire lifetime, at stable, nonoverlapping addresses.
 * Only option new-values, selected index and requested command may change while
 * attached. This object owns its voice; it never owns the wire buffers/backend.
 * +08/+10 mirror MW handle/type, but MUST NOT be sent to either native updater
 * or destructor: this adapter owns release and source command semantics.
 */
typedef struct {
    uint8_t *state;
    const uint8_t *selectors;
    size_t state_size, selectors_size;
    CarAudioPlayerBackend backend;
    void *context;
    uint64_t bank_identity;
    int32_t voice;
    int last_error;
    uint8_t owned, option_count;
} CarAudioPlayer;

/* Attach accepts stopped source templates only (+08=0,+10=FF,commands=0).
 * Reattaching an attached sidecar fails without dropping its existing voice.
 * Header/tail/selector checks precede any backend call. No heap allocation.
 */
int car_audio_player_attach(CarAudioPlayer *, uint8_t *state, size_t state_size,
                          const uint8_t *selectors, size_t selectors_size,
                          uint64_t bank_identity,
                          const CarAudioPlayerBackend *, void *context);
/* Returns source status 0/1/2 or a negative boundary-validation error.
 * Backend create/status failure returns source status 0, with last_error set.
 */
int car_audio_player_update(CarAudioPlayer *);
/* Ordinal-4 PLAYER cleanup, for A7's instance walk BEFORE instance/bank free.
 * Idempotent; releases owned voices from the sidecar even if wire is damaged.
 * Resets commands for a later attach. Does not destroy control-class objects,
 * unlink instances or free their storage; those remain lifecycle integration.
 */
void car_audio_player_detach(CarAudioPlayer *);
void car_audio_player_destroy(CarAudioPlayer *players, size_t count);

#endif
