// water_port.h - the port's own sea and harbour water (NFS_WATER=port): authored wave normals and Fresnel reflection
// added to the PC world shader of the watersea draw. No console input; materials.m chooses it before shader warming.
#pragma once
#include <stdint.h>

// The PC world shader's translation with the water term added, or NULL when its anchors are missing (PC kept).
char *water_port_shader(const char *pc_msl, int vertex);
// Fills the authored entries of the water constant block (rows 15-17 local to world, 18 local light direction and
// 19 local eye are already the PC's): 21 simulation time, 22 the PC's sun colour (DiffuseColour).
void water_port_constants(float out[32][4], const float sun[4], float sim_time);
// The compute source that renders the four wave-map slices for a simulation time (render.m dispatches it).
const char *water_port_kernel(void);
