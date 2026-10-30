#include "post_cuts.h"
#include <stdlib.h>
#include <string.h>
int post_cuts_enabled(void) {
    static int on = -1;
    if (on < 0) { const char *e = getenv("NFS_POST_CUTS"); on = e && !strcmp(e, "1"); }
    return on;
}
int post_cuts_full_target(int split, unsigned level, unsigned slice,
                          uint32_t w, uint32_t h, uint32_t ow, uint32_t oh) {
    return post_cuts_enabled() && !split && !level && !slice && w == ow && h == oh;
}
int post_cuts_curve(uint32_t w, uint32_t h, int split) {
    return post_cuts_enabled() && !split && w == 3840 && h == 2160;
}
unsigned post_cuts_ao(int proof, int ordinary_depth, int reuse, int empty) {
    if (!post_cuts_enabled() || proof || reuse || !ordinary_depth) return 0;
    // D16 is regenerated before every coverage request. Nonempty MS masks may
    // receive incremental draws later in the frame and must retain their samples.
    return POST_CUTS_AO_DEPTH | (empty ? POST_CUTS_AO_MS : 0);
}
const char *post_cuts_curve_source(void) {
    return
    "#include <metal_stdlib>\nusing namespace metal;\n"
    "struct VOut { float4 pos [[position]]; float2 uv; };\n"
    "vertex VOut vs_full(uint v [[vertex_id]]) { float2 p=float2((v<<1)&2,v&2); return {float4(p*float2(2,-2)+float2(-1,1),0,1),p}; }\n"
    "struct K { float4 off[16]; float4 wgt[16]; float4 p0; };\n"
    "fragment float4 fs_tdown(VOut i [[stage_in]], texture2d<float> s [[texture(0)]], sampler sm [[sampler(0)]], constant K &k [[buffer(0)]]) {\n"
    " const float p[5]={-3.5,-1.5,0.5,2.5,4.0}; const float w[5]={2,2,2,2,1}; float a=0;\n"
    " for(int y=0;y<5;++y) for(int x=0;x<5;++x) a+=s.sample(sm,i.uv+float2(p[x],p[y])*k.p0.zw).a*w[x]*w[y];\n"
    " return float4(0,0,0,a/81.0); }\n";
}
