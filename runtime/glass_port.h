#pragma once

// Building glass on the port's own reflection V (NFS_BUILDING_GLASS=port): no console input, no generated table.
// The PC glossywindow vertex shader's translation with its reflection V (texcoord1.y, PC .5*UV0.y) set to the diffuse
// V (UV0.y); vertex buffer(`kbuf`) float4 27.w != 0 restores .5*UV0.y (the held-frame control).
// NULL when the translation is not the expected shape.
char *glass_port_vs(const char *pc_msl, int kbuf);
