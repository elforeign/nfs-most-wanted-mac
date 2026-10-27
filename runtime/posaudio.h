#pragma once
#include <stdint.h>

// Guest panner order: C, FR, SR, SL, FL (LFE is separately routed).
// Angles use the game's unsigned turn, 65536 = 360 degrees.
void posaudio_gains(double angle, int itu, float gains[5]);
uint16_t posaudio_map_default(uint16_t angle, int itu);
int posaudio_layout(void);
void posaudio_diag_second(void);
void posaudio_set_engine_sound(int realistic);   // F10 Audio > Engine sound: 1 = Realistic, 0 = Retail
int posaudio_engine_realistic(void);
uint32_t posaudio_attrib_hash(const char *s);   // Attrib::StringHash32
int posaudio_engine_layout(uint32_t key);       // player car's engine: 0 front, 1 front-mid, 2 mid, 3 rear, -1 unknown
void posaudio_engine_spread(double angle, double dist, int itu, double power, float gains[5]);
