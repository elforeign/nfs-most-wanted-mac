// Scene-lit aerosol cards; authored textures and particle alpha remain the base.
#pragma once
#include "materials.h"
#include "host.h"

typedef struct ParticleLamps { float k[17][4]; } ParticleLamps;

int particles_physical_enabled(void);
void particles_physical_set(int enabled);
char *particles_physical_shader(char *source);
void particles_physical_constants(MatConsts *out, unsigned view_id, const uint32_t viewport[4]);
void particles_physical_lamp(Cpu *cpu);
void particles_physical_lamps(ParticleLamps *out);
void lighting_particle_scene(float ambient[3], float sun[3], float direction[3]);
