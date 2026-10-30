// Sampled observations of the original car-render rejection branches.
#pragma once
#include "runtime.h"

typedef struct DiagCarRender {
    struct DiagCarRender *previous;
    uint32_t self, ride, view, view_id, reflection, reject, seen;
    int32_t pixel, pixel_min, body_lod, tire_lod, visible, inside;
    uint64_t frame;
    int sampled;
} DiagCarRender;

void diag_car_init(void);
void diag_car_begin(Cpu *c, DiagCarRender *sample, uint64_t frame);
void diag_car_observe(Cpu *c, uint32_t site);
void diag_car_reject(uint32_t site);
void diag_car_end(Cpu *c, DiagCarRender *sample);
