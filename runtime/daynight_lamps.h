#pragma once
#include "host.h"

void daynight_lamps_frame(uint64_t frame);
void daynight_lamps_render(Cpu *c, void (*render)(Cpu *));
