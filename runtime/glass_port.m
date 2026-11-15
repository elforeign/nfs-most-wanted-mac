// glass_port.m - building glass reflection V for players without console files (NFS_BUILDING_GLASS=port).
// The PC glossywindow keeps its shading, U, strength, shadows, fog and alpha; only the reflection V changes, from the
// PC's .5*UV0.y to UV0.y. Measured over the 213,687 PC glass vertices with a console counterpart (offline,
// runtime/glass_port_test.py): the console's reflection V equals UV0.y (wrapped) on 74.8 % of them, and the mean wrapped V
// error is .054 with UV0.y against .264 with .5*UV0.y and .190 with a whole-solid height projection.
#include "glass_port.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

// s with the first `what` (at or after `from`) replaced by `with`; NULL (s freed) when absent.
static char *swap(char *s, const char *from, const char *what, const char *with) {
    char *f = s && from ? strstr(s, from) : s, *at = f ? strstr(f, what) : NULL;
    if (!at) { free(s); return NULL; }
    size_t pre = (size_t)(at - s), keep = strlen(what), nl = strlen(with);
    char *o = malloc(strlen(s) - keep + nl + 1);
    if (o) { memcpy(o, s, pre); memcpy(o + pre, with, nl); strcpy(o + pre + nl, at + keep); }
    free(s);
    return o;
}
char *glass_port_vs(const char *pc_msl, int kbuf) {
    char args[160];
    snprintf(args, sizeof args, "constant SmVSAux &aux [[buffer(19)]], constant float4 *glass_k [[buffer(%d)]])", kbuf);
    // All three glossywindow vertex shaders write tex[1].y = .5 * TEXCOORD0.y (k.z/k.y = .5), so 2 * tex[1].y is UV0.y
    // exactly (a power-of-two scale).
    static const char v[] =
        "    r.texcoord1 = float4(so.tex[1].x, glass_k[27].w == 0.0f ? 2.0f * so.tex[1].y : so.tex[1].y, so.tex[1].zw);\n";
    char *s = pc_msl ? strdup(pc_msl) : NULL;
    s = swap(s, "vertex SmVSStageOut sm_main(", "constant SmVSAux &aux [[buffer(19)]])", args);
    s = swap(s, "SmVSStageOut r;", "    r.texcoord1 = so.tex[1];\n", v);
    return s;
}
