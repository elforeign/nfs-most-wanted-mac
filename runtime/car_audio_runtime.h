#ifndef CAR_AUDIO_RUNTIME_H
#define CAR_AUDIO_RUNTIME_H
#include "car_audio_engine_class.h"
#include "car_audio_backend.h"

/* Mac execution adapter. Hook and admission ownership belongs to car_audio.c.
 * The embedding loader serializes calls and verifies resource SHA-256 and the
 * complete instruction/state manifest before calling load. This is not an
 * untrusted x86 validator. Plans use original file offsets, never host pointers.
 */
typedef struct { uint32_t offset, size, selector; } CarAudioRuntimePlayerPlan;
typedef struct {
    uint32_t descriptor, entry, state, size, member_offset, destroy_offset;
    CarAudioCsisInterface interface;
    const CarAudioRuntimePlayerPlan *players;
    size_t player_count;
    const uint32_t *controls;
    size_t control_count;
} CarAudioRuntimeModule;
typedef struct {
    uint32_t site;
    CarAudioCsisInterface interface;
} CarAudioRuntimeInterface;
/* Synchronous, non-reentrant CAR_SputOutput service. Member inputs are borrowed
 * for the callback only. create returns 0 with a non-NULL owned object or a
 * negative error; an object returned on error is released immediately. The
 * callbacks/context outlive all attached instances. release consumes ownership.
 */
typedef struct {
    int (*create)(void *, const int32_t *, size_t, void **);
    void (*update)(void *, void *, const int32_t *, size_t);
    void (*release)(void *, void *);
    void *context;
} CarAudioRuntimeClasses;
typedef struct {
    uint32_t offset;
    void *object;
    uint8_t count, clamp;
} CarAudioRuntimeControl;
typedef struct CarAudioRuntimeInstance CarAudioRuntimeInstance;
typedef struct {
    const uint8_t *source;
    uint8_t *image;
    size_t size, module_count;
    uint32_t base;
    uint64_t identity;
    const CarAudioRuntimeModule *modules;
    CarAudioRuntimeInstance *instances;
    CarAudioBackend *backend;
    CarAudioRuntimeClasses classes;
} CarAudioRuntimeBank;
struct CarAudioRuntimeInstance {
    CarAudioRuntimeBank *bank;
    const CarAudioRuntimeModule *module;
    CarAudioRuntimeInstance *next;
    uint8_t *state;
    uint32_t address;
    CarAudioPlayer *players;
    CarAudioRuntimeControl *controls;
    CarAudioEngineClass engine;
    uint8_t destroying, executing;
};

/* All borrowed buffers/plans must remain stable until unload returns. Source,
 * image and instance storage are disjoint. On load failure image is unchanged.
 * Imports are relocated to their decoded MW entry points: dispatch MUST route
 * ordinals 4/27/38 to import below, never to the native player/destructor.
 */
int car_audio_runtime_load(CarAudioRuntimeBank *, const uint8_t *source,
    uint8_t *image, size_t size, uint32_t guest_base, uint64_t identity,
    const CarAudioRuntimeModule *, size_t module_count,
    const CarAudioRuntimeInterface *, size_t interface_count,
    const CarAudioCsisInterface *registry, size_t registry_count,
    CarAudioBackend *, const CarAudioRuntimeClasses *);
int car_audio_runtime_create(CarAudioRuntimeBank *, size_t module,
    CarAudioRuntimeInstance *, uint8_t *state, size_t bytes, uint32_t address,
    const int32_t *members, size_t member_count);
int car_audio_runtime_update(CarAudioRuntimeInstance *, const int32_t *, size_t);
/* Executor receives relocated entry and source callback context instance+24.
 * It executes the complete module through interp_run and propagates errors.
 * Destroy requested inside execution detaches resources immediately, deferring
 * storage release/unlink until the interpreter has returned to its caller.
 */
typedef int (*CarAudioRuntimeExecute)(void *, uint32_t entry, uint32_t context);
int car_audio_runtime_tick(CarAudioRuntimeInstance *, CarAudioRuntimeExecute, void *);
int car_audio_runtime_import(CarAudioRuntimeInstance *, unsigned ordinal,
                             uint32_t state_address, uint32_t *result);
void car_audio_runtime_destroy(CarAudioRuntimeInstance *);
void car_audio_runtime_unload(CarAudioRuntimeBank *);
#endif
