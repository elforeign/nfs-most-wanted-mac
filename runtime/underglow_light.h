#pragma once
#include "underglow_render.h"
int underglow_light_enabled(void);
unsigned underglow_pool_strength(void); /* global Measured=0 / Stronger=1 */
void underglow_light_begin(const Cpu *cpu, UGCarCapture *capture);
void underglow_light_end(const Cpu *cpu, const UGCarCapture *capture);
