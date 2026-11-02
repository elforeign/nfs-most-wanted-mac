#pragma once
#include "host.h"
// Bounded headless CRT environment, using the XP/NT guest compatibility profile.
// No environment/argv initialization or entry into game main is claimed.
int win_startup_init(const char *);
void win_startup_checkpoint(Cpu *, const struct ImportSlot *, uint32_t, uint32_t);
extern int win_boot_prefix;
