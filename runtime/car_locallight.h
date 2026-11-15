// Independent nearby-lamp diffuse lighting; NFS_CAR_LOCALLIGHT=0 turns it Off (default On, only with NFS_DAYNIGHT=on).
#pragma once
#include <stdint.h>

// 19 point lights use 38 float4s plus the existing material/response prefix:
// 52 float4s (832 bytes), within the inline fragment constant limit.
enum { CAR_LOCAL_LIGHT_MAX = 19, CAR_LOCAL_LIGHT_CONSTANTS = 52 };
typedef struct {
    float rgb[3];       // this lamp's falloff and common luminance ceiling
    float position[3];  // lamp relative to the car, rotated into its local axes
    float world[3];     // absolute lamp position retained across cloned part contexts
} CarLocalLamp;
typedef struct {
    CarLocalLamp lights[CAR_LOCAL_LIGHT_MAX];
    unsigned lamps;
    float nearest;
} CarLocalLight;

                                                                                  
extern int car_locallight_on;
static inline int car_locallight_enabled(void) { return car_locallight_on; }
// Entry points leave feature state and output buffers untouched while Off.
// BOOT_DIAG only: entry counts and entries which perform feature work.
enum { CAR_LOCAL_ROTATE, CAR_LOCAL_SELECT, CAR_LOCAL_MATERIAL, CAR_LOCAL_REFRESH,
       CAR_LOCAL_LOOKUP, CAR_LOCAL_APPLY, CAR_LOCAL_CLONE, CAR_LOCAL_DIAG_COUNT };
void car_locallight_diag(unsigned entry, int work);
void car_locallight_gather(const float position[3], const float (*rows)[3], CarLocalLight *out);
// Record the lamp beside the guest context; never mutate the rig.
void car_locallight_apply(uint32_t context, uint32_t position, uint32_t local_world, float level);
// Called when the game's light-context setter selects a car for drawing.
void car_locallight_select(uint32_t context);
void car_locallight_clone(uint32_t target, uint32_t source, uint32_t local_world);
// Lamp 0 uses 12/13; 14/15 retain the native diffuse response; lamps 1-18 use 16-51.
void car_locallight_constants(float out[CAR_LOCAL_LIGHT_CONSTANTS][4]);
