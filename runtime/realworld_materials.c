#include "realworld_materials.h"
#include "traffic_detail.h"
#include "../host/shaders/sm_coverage.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

typedef struct { uint32_t parent, index; float p[3]; double sum[3]; } PaneVertex;
static uint32_t pane_root(PaneVertex *v, uint32_t i) {
    while (v[i].parent != i) { v[i].parent = v[v[i].parent].parent; i = v[i].parent; }
    return i;
}
static int pane_position(const void *a, const void *b) {
    const PaneVertex *x=a, *y=b;
    for (int k=0;k<3;++k) { if (x->p[k]<y->p[k]) return -1; if (x->p[k]>y->p[k]) return 1; }
    return 0;
}
static float pane_vlt(const double n[3], int mask) {
    double horizontal=fabs(n[0])+fabs(n[1]), length2=n[0]*n[0]+n[1]*n[1]+n[2]*n[2];
    // Geometric outward direction; independent of authored normals and winding.
    if (n[2]>0 && n[2]*n[2]>=.98*.98*length2) return .10f;
    if (horizontal<1e-12) return mask ? .70f : .75f;
    if (fabs(n[0])<=.25*horizontal) return .70f;
    return n[0]<0 ? .20f : .75f;
}
// Each connected component of one material section gets one constant VLT.
// Weld coincident positions across UV/normal seams, without joining nearby panes.
float *realworld_materials_panes(const void *vertices, uint32_t nv, uint32_t stride, uint32_t position,
                               uint32_t normal, const void *indices, uint32_t ni, uint32_t isz, uint32_t first, int mask) {
    if (!vertices || !indices || !nv || !ni || ni%3 || (isz!=2 && isz!=4) ||
        stride<12 || position>stride-12 || normal>stride-12) return NULL;
    PaneVertex *v=calloc(nv,sizeof *v), *sorted=malloc((size_t)nv*sizeof *sorted);
    float *out=malloc((size_t)nv*sizeof *out);
    uint32_t *ix=malloc((size_t)ni*sizeof *ix);
    if (!v || !sorted || !out || !ix) { free(v);free(sorted);free(out);free(ix);return NULL; }
    for (uint32_t i=0;i<nv;++i) { v[i].parent=i; v[i].index=i; out[i]=mask?.70f:.75f; }
    for (uint32_t k=0;k<ni;++k) {
        uint32_t j=0;
        memcpy(&j,(const uint8_t *)indices+(size_t)k*isz,isz);
        if (j<first || j-first>=nv) goto invalid;
        ix[k]=j-first;
        const uint8_t *src=(const uint8_t *)vertices+(size_t)ix[k]*stride;
        memcpy(v[ix[k]].p,src+position,12);
        float n[3];memcpy(n,src+normal,12);
        for (int c=0;c<3;++c) if (!isfinite(v[ix[k]].p[c]) || !isfinite(n[c])) goto invalid;
    }
    // Only referenced vertices participate in welding.
    uint32_t used=0;
    for (uint32_t k=0;k<ni;++k) {
        uint32_t j=ix[k];
        if (!v[j].sum[0]) { sorted[used++]=v[j]; v[j].sum[0]=1; }
    }
    for (uint32_t j=0;j<nv;++j) v[j].sum[0]=0;
    qsort(sorted,used,sizeof *sorted,pane_position);
    for (uint32_t k=1;k<used;++k) if (!pane_position(&sorted[k-1],&sorted[k]))
        v[pane_root(v,sorted[k].index)].parent=pane_root(v,sorted[k-1].index);
    for (uint32_t k=0;k<ni;k+=3) {
        uint32_t root=pane_root(v,ix[k]);
        v[pane_root(v,ix[k+1])].parent=root;v[pane_root(v,ix[k+2])].parent=root;
    }
    for (uint32_t k=0;k<ni;k+=3) {
        double a[3],b[3],cross[3];
        for (int c=0;c<3;++c) { a[c]=(double)v[ix[k+1]].p[c]-v[ix[k]].p[c];b[c]=(double)v[ix[k+2]].p[c]-v[ix[k]].p[c]; }
        cross[0]=a[1]*b[2]-a[2]*b[1];cross[1]=a[2]*b[0]-a[0]*b[2];cross[2]=a[0]*b[1]-a[1]*b[0];
        // Car-local origin is inside/below the glazing. Orient each face away
        // from it, so reversed low-LOD normals AND winding cannot change tint.
        double radial=0;
        for (int c=0;c<3;++c) radial+=cross[c]*(v[ix[k]].p[c]+v[ix[k+1]].p[c]+v[ix[k+2]].p[c]);
        double sign=radial<0 ? -1 : 1;
        uint32_t root=pane_root(v,ix[k]);
        // Cross magnitude is twice the area: large faces dominate seam slivers.
        for (int c=0;c<3;++c) v[root].sum[c]+=sign*cross[c];
    }
    for (uint32_t k=0;k<used;++k) { uint32_t j=sorted[k].index;out[j]=pane_vlt(v[pane_root(v,j)].sum,mask); }
    free(v);free(sorted);free(ix);return out;
invalid:
    free(v);free(sorted);free(ix);free(out);return NULL;
}

static int mode = -1, role, rim, glass_mask;
int realworld_materials_window_mask(void) { return glass_mask; }
void realworld_materials_set(int value) { mode = value >= 0 && value <= 3 ? value : 0; }
int realworld_materials_mode(void) {
    if (mode < 0) {
        const char *e = getenv("NFS_REALWORLD_MATERIALS");
        mode = !e ? 0 : !strcmp(e, "glass") ? 1 : !strcmp(e, "chrome") ? 2 : !strcmp(e, "all") ? 3 : 0;
    }
    return mode;
}
static uint32_t hash_name(const char *s) {
    uint32_t h = UINT32_MAX;
    while (*s) h = h * 33 + (uint8_t)*s++;
    return h;
}
int realworld_materials_role(uint32_t h, int car, int selection, uint32_t flags) {
    if (!car) return 0;
    h = traffic_detail_glass(h, flags);
    if ((selection & 1) && (h == hash_name("WINDSHIELD") || h == hash_name("WINDOWMASK"))) return 1;
    if ((selection & 2) && (h == hash_name("CHROME") || h == hash_name("MAGCHROME") || h == hash_name("USER_RIMS_CHROME"))) return 2;
    return 0;
}
// CompositeWheel bakes RGB but preserves diffuse alpha. Keep its separate mask
// with each reusable DUMMY_WHEELn, including when the feature is switched off.
static struct RimTexture { uint32_t hash, mask; int painted, paletted; struct RimTexture *next; } *rim_textures;
static uint32_t rim_mask;
static int rim_paletted;
// The renderer samples a stored mask long after the game may have released it: each entry holds a reference on its mask
// (hooks set by d3d9.m; unset in tests that run without a device).
static void (*hold_add)(uint32_t), (*hold_release)(uint32_t);
void realworld_materials_set_hold(void (*add)(uint32_t), void (*release)(uint32_t)) { hold_add = add; hold_release = release; }
uint32_t realworld_materials_rim_mask(void) { return rim_mask; }
void realworld_materials_rim_texture(uint32_t texture, int painted, uint32_t mask, int paletted) {
    if (!texture) return;
    struct RimTexture *r = rim_textures;
    while (r && r->hash != texture) r = r->next;
    if (!r) {
        r = calloc(1, sizeof *r);
        if (!r) return;
        r->hash = texture; r->next = rim_textures; rim_textures = r;
    }
    if (r->mask != mask) {
        if (mask && hold_add) hold_add(mask);
        if (r->mask && hold_release) hold_release(r->mask);
    }
    r->painted = painted; r->mask = mask; r->paletted = paletted;
}
void realworld_materials_capture(uint32_t h, int car, uint32_t flags) {
    rim_mask = 0; rim_paletted = 0;
    role = realworld_materials_role(h, car, realworld_materials_mode(), flags);
    glass_mask = role == 1 && h == hash_name("WINDOWMASK");
    rim = role == 2 && h == hash_name("USER_RIMS_CHROME");
}
// Lamp-state textures (headlight/brakelight _OFF/_ON/_DAMAGE0) as GetUsedCarTextureInfo derives them per car.
// Stock lamp units draw CHROME with these: their look (lit bulb, bowls, housing) is the texture, which
// zero-diffuse conductor shading would discard, so those draws keep the retail shader.
static uint32_t lamp_textures[2048];
void realworld_materials_lamp_texture(uint32_t texture) {
    if (!texture) return;
    for (uint32_t i = texture * 2654435761u >> 21, n = 0; n < 2048; ++n, i = (i + 1) & 2047)
        if (!lamp_textures[i] || lamp_textures[i] == texture) { lamp_textures[i] = texture; return; }
}
static int lamp_texture(uint32_t texture) {
    if (!texture) return 0;
    for (uint32_t i = texture * 2654435761u >> 21, n = 0; n < 2048 && lamp_textures[i]; ++n, i = (i + 1) & 2047)
        if (lamp_textures[i] == texture) return 1;
    return 0;
}
void realworld_materials_texture(uint32_t texture) {
    if (role == 2 && !rim && lamp_texture(texture)) role = 0;
    if (rim)
        for (struct RimTexture *r = rim_textures; r; r = r->next)
            if (r->hash == texture) {
                if (r->painted && r->mask) { role = 3; rim_mask = r->mask; rim_paletted = r->paletted; }
                break;
            }
}
int realworld_materials_draw(uint64_t ps, uint64_t vs) {
    if (!realworld_materials_mode() || !role) return 0;
    int t0 = ps == 0x89E9A52CCEE09BCFULL;
    int shadowed = ps == 0x393123DA8B3EC17FULL || ps == 0x2CD941122737DDF5ULL ||
        ps == 0xE3C84E78B9847542ULL || ps == 0x6F1A7154236357C9ULL || ps == 0xA8F9086D6BE6DB85ULL;
    if ((!t0 || vs != 0x1BEA38904139377AULL) && (!shadowed || vs != 0xB1CE127C28045F2CULL)) return 0;
    return role == 1 ? MAT_VARIANT_REAL_GLASS : MAT_VARIANT_REAL_CHROME;
}
int x360_post_rolloff(void);
void realworld_materials_constants(int id, const float (*vsc)[4], MatConsts *out) {
    int row = id == MAT_REAL_T0 ? 7 : 11;
    for (int i=0;i<3;++i) out->k[1][i]=vsc[row+i][0];
    out->k[1][3]=x360_post_rolloff() ? 1.0f : 0.0f;
    out->k[2][0]=role == 3 ? 1.0f : 0.0f;
    out->k[2][1]=(float)rim_paletted;
    out->k[2][2]=(float)glass_mask;
}
static char *replace(const char *s, const char *needle, const char *value) {
    const char *p = strstr(s, needle);
    if (!p || strstr(p + strlen(needle), needle)) return NULL;
    size_t pre = (size_t)(p - s), len = strlen(value);
    char *out = malloc(strlen(s) - strlen(needle) + len + 1);
    if (!out) return NULL;
    memcpy(out, s, pre); memcpy(out + pre, value, len); strcpy(out + pre + len, p + strlen(needle));
    return out;
}
char *realworld_materials_vertex(const char *source, int unshadowed, int chrome) {
    // Both layouts rotate object-space directions into the cube coordinate system.
    int row = unshadowed ? 4 : 8, light = unshadowed ? 10 : 14, eye = unshadowed ? 13 : 17;
    char values[1500];
    snprintf(values, sizeof values,
        "    float3 rwN=si.v[1].xyz, rwV=c[%d].xyz-si.v[0].xyz, rwL=c[%d].xyz;\n"
        "    r.texcoord3=float4(dot(rwL,c[%d].xyz),dot(rwL,c[%d].xyz),dot(rwL,c[%d].xyz),0);\n"
        "    r.texcoord4=float4(dot(rwN,c[%d].xyz),dot(rwN,c[%d].xyz),dot(rwN,c[%d].xyz),%s);\n"
        "    r.texcoord5=float4(dot(rwV,c[%d].xyz),dot(rwV,c[%d].xyz),dot(rwV,c[%d].xyz),0);\n"
        "    r.texcoord6=float4(si.v[0].xyz,saturate(2.0f*si.v[2].x));\n",
        eye, light, row,row+1,row+2,row,row+1,row+2,chrome?"0":"rwTint[rwID-rwBase]",row,row+1,row+2);
    char *s = chrome ? strdup(source) : replace(source,"vertex SmVSStageOut sm_main(",
        "vertex SmVSStageOut sm_main(uint rwID [[vertex_id]], constant float *rwTint [[buffer(20)]], constant uint &rwBase [[buffer(21)]], ");
    for (int i = 3; s && i <= 6; ++i) {
        char needle[64]; snprintf(needle, sizeof needle, "    r.texcoord%d = so.tex[%d];\n", i, i);
        char *next = replace(s, needle, i == 3 ? values : ""); free(s); s = next;
    }
    return s;
}

static const char optics[] =
"static float rw_surface(float ci) {\n"
"    const float n = 1.525139f;\n"
"    float ct = sqrt(1.0f - (1.0f-ci*ci)/(n*n));\n"
"    float rs = (ci-n*ct)/(ci+n*ct), rp = (n*ci-ct)/(n*ci+ct);\n"
"    return 0.5f*(rs*rs+rp*rp);\n"
"}\n"
"static float2 rw_pane(float ci) {\n"
"    ci = clamp(ci, 0.0f, 1.0f);\n"
"    float f = rw_surface(ci);\n"
"    float ct = sqrt(1.0f-(1.0f-ci*ci)/(1.525139f*1.525139f));\n"
"    float a = exp(-4.0f*3.141592653589793f*2.2e-7f*0.004f/(550e-9f*ct));\n"
"    float den = 1.0f-f*f*a*a;\n"
"    float t = (1.0f-f)*(1.0f-f)*a/den;\n"
"    float r = f+(1.0f-f)*(1.0f-f)*f*a*a/den;\n"
"    return float2(r,t);\n"
"}\n"
"static float2 rw_glass(float ci, float vlt) {\n"
"    float2 rt=rw_pane(ci);\n"
"    float ct=sqrt(1.0f-(1.0f-ci*ci)/(1.525139f*1.525139f));\n"
"    // Effective neutral absorbing layer, calibrated to whole-pane normal VLT.\n"
"    // Keep the existing pane Fresnel reflection; absorption grows with path.\n"
"    rt.y *= exp(log(vlt/rw_pane(1.0f).y)/ct);\n"
"    return rt;\n"
"}\n"
"static float rw_conductor(float c) {\n"
"    c = clamp(c, 0.0f, 1.0f);\n"
"    const float n = 3.1812121212f, k = 3.3290909091f;\n"
"    float s2=1.0f-c*c, a=n*n-k*k-s2;\n"
"    float b=sqrt(a*a+4.0f*n*n*k*k), u=sqrt(0.5f*(b+a));\n"
"    float rs=(b+c*c-2.0f*c*u)/(b+c*c+2.0f*c*u);\n"
"    float rp=rs*(b*c*c+s2*s2-2.0f*c*u*s2)/(b*c*c+s2*s2+2.0f*c*u*s2);\n"
"    return 0.5f*(rs+rp);\n"
"}\n"
"static float rw_g1(float c) {\n"
"    return 2.0f*c/(c+sqrt(0.000036f+(1.0f-0.000036f)*c*c));\n"
"}\n"
"static float rw_coat(float c) {\n"
"    // Polymer coat n=1.5: dielectric F0=((n-1)/(n+1))^2=.04.\n"
"    float x=1.0f-saturate(c); return 0.04f+0.96f*x*x*x*x*x;\n"
"}\n"
"static float rw_direct(float3 N, float3 V, float3 L) {\n"
"    float nv=saturate(dot(N,V)), nl=saturate(dot(N,L));\n"
"    if (nv<=0.0f || nl<=0.0f) return 0.0f;\n"
"    float3 H=normalize(V+L); float nh=saturate(dot(N,H));\n"
"    float d=(1.0f-nh*nh)+0.000036f*nh*nh;\n"
"    float D=0.000036f/(3.141592653589793f*d*d);\n"
"    return D*rw_g1(nv)*rw_g1(nl)*rw_conductor(saturate(dot(V,H)))/(4.0f*nv);\n"
"}\n"
"static float rw_direct(float3 N, float3 V, float3 L, float3 dx, float3 dy) {\n"
"    if (dot(dx,dx)+dot(dy,dy)<=1e-12f) return rw_direct(N,V,L);\n"
"    float nv=saturate(dot(N,V)), nl=saturate(dot(N,L));\n"
"    if (nv<=0.0f || nl<=0.0f) return 0.0f;\n"
"    float3 H=normalize(V+L); float nh=saturate(dot(N,H));\n"
"    float3 T=normalize(cross(abs(N.z)<0.99f?float3{0,0,1}:float3{1,0,0},N)), B=cross(N,T);\n"
"    float xt=dot(dx,T), xb=dot(dx,B), yt=dot(dy,T), yb=dot(dy,B);\n"
"    // Conservative GGX footprint: four times the box normal covariance.\n"
"    // Keep its full 2x2 matrix: isotropic widening loses thin-strip energy.\n"
"    float s=1.0f/max(3.0f,dot(dx,dx)+dot(dy,dy));\n"
"    float a=0.000036f+s*(xt*xt+yt*yt), b=s*(xt*xb+yt*yb), c=0.000036f+s*(xb*xb+yb*yb);\n"
"    float det=a*c-b*b, ht=dot(H,T), hb=dot(H,B);\n"
"    float q=(c*ht*ht-2.0f*b*ht*hb+a*hb*hb)/det+nh*nh;\n"
"    float D=1.0f/(3.141592653589793f*sqrt(det)*q*q);\n"
"    float vt=dot(V,T), vb=dot(V,B), lt=dot(L,T), lb=dot(L,B);\n"
"    float gv=2.0f*nv/(nv+sqrt(nv*nv+a*vt*vt+2.0f*b*vt*vb+c*vb*vb));\n"
"    float gl=2.0f*nl/(nl+sqrt(nl*nl+a*lt*lt+2.0f*b*lt*lb+c*lb*lb));\n"
"    return D*gv*gl*rw_conductor(saturate(dot(V,H)))/(4.0f*nv);\n"
"}\n";

char *realworld_materials_pixel(const char *source, int variant, int shadow, int chrome) {
    // Reuse only the existing interface and shadow lookup, before legacy lighting.
    const char *body = strstr(source, "fragment Out sm_main(");
    const char *cut = strstr(source, "    // X360 09bb919c:");
    if (!body || !cut || body >= cut) return NULL;
    const char *tail =
"    float3 N=normalize(pi.texcoord4.xyz), V=normalize(pi.texcoord5.xyz), L=normalize(pi.texcoord3.xyz);\n"
"    if (dot(N,V)<0.0f) N=-N;\n"
"    float nv=saturate(dot(N,V)), coverage=saturate(tex.a);\n"
"    float3 R=reflect(-V,N);\n"
"    Out o;\n"
"#if RW_CHROME\n"
"    float3 dNdx=dfdx(N), dNdy=dfdy(N);\n"
"    float paint=0.0f;\n"
"    if (m.k[2].x>0.5f) {\n"
"        if (m.k[2].y<0.5f) paint=nmap.sample(smp0,pi.texcoord1.xy).g;\n"
"        else {\n"
"            // CompositeWheel8 uses the BGRA palette byte 0 high nibble / 15.\n"
"            // Decode each tap before interpolation, not the filtered byte.\n"
"            float2 size=float2(nmap.get_width(),nmap.get_height());\n"
"            float2 p=pi.texcoord1.xy*size-0.5f, base=floor(p), f=fract(p);\n"
"            float4 taps;\n"
"            taps.x=nmap.sample(smp0,(base+float2(.5f,.5f))/size,level(0)).b;\n"
"            taps.y=nmap.sample(smp0,(base+float2(1.5f,.5f))/size,level(0)).b;\n"
"            taps.z=nmap.sample(smp0,(base+float2(.5f,1.5f))/size,level(0)).b;\n"
"            taps.w=nmap.sample(smp0,(base+float2(1.5f,1.5f))/size,level(0)).b;\n"
"            taps=floor(taps*255.0f/16.0f+1e-5f)/15.0f;\n"
"            paint=mix(mix(taps.x,taps.y,f.x),mix(taps.z,taps.w,f.x),f.y);\n"
"        }\n"
"    }\n"
"    paint=saturate(paint); bool painted=paint>0.0f;\n"
"    float3 T=normalize(cross(abs(N.z)<0.99f?float3(0,0,1):float3(1,0,0),N)), B=cross(N,T);\n"
"    float3 ibl=float3(0), diffuse=float3(0);\n"
"    for (int i=0;i<16;++i) {\n"
"        float u=(float(i)+0.5f)/16.0f, phi=float(i)*2.39996323f;\n"
"        float ct=sqrt((1.0f-u)/(1.0f+(0.000036f-1.0f)*u)), st=sqrt(max(0.0f,1.0f-ct*ct));\n"
"        float3 H=normalize(N*ct+(T*cos(phi)+B*sin(phi))*st);\n"
"        float vh=saturate(dot(V,H)), nl; float3 Li=reflect(-V,H); nl=saturate(dot(N,Li));\n"
"        // Derivatives precede varying branches; Metal clamps to available mips.\n"
"        float3 dLdx=dfdx(Li), dLdy=dfdy(Li);\n"
"        if (nl>0.0f && nv>0.0f) ibl+=tex1.sample(smp1,Li,gradientcube(dLdx,dLdy)).rgb*mix(rw_conductor(vh),rw_coat(vh),paint)*rw_g1(nv)*rw_g1(nl)*vh/max(ct*nv,1e-8f);\n"
"        float3 Di=N*sqrt(1.0f-u)+(T*cos(phi)+B*sin(phi))*sqrt(u);\n"
"        float3 dDdx=dfdx(Di), dDdy=dfdy(Di);\n"
"        if (painted) diffuse+=tex1.sample(smp1,Di,gradientcube(dDdx,dDdy)).rgb;\n"
"    }\n"
"    // PC car vertex colour x: 0.5 is unoccluded; the VS clamps saturate(2 x) per vertex like retail, interpolated here.\n"
"    float ao=pi.texcoord6.w;\n"
"    o.c0=float4(ao*ibl/16.0f + rw_direct(N,V,L,dNdx,dNdy)*m.k[1].rgb*lit,coverage);\n"
"    if (painted) {\n"
"        float vh=sqrt(saturate((1.0f+dot(V,L))*0.5f));\n"
"        float direct=rw_direct(N,V,L,dNdx,dNdy)*mix(1.0f,rw_coat(vh)/max(rw_conductor(vh),1e-8f),paint);\n"
"        float3 irradiance=ao*diffuse/16.0f+m.k[1].rgb*(saturate(dot(N,L))*lit/3.141592653589793f);\n"
"        o.c0.rgb=ao*ibl/16.0f+direct*m.k[1].rgb*lit+paint*(1.0f-rw_coat(nv))*tex.rgb*irradiance;\n"
"    }\n"
"#else\n"
"    float2 rt=rw_glass(nv,pi.texcoord4.w); float alpha=coverage*(1.0f-rt.y);\n"
"    float3 reflected=tex1.sample(smp1,R,level(0)).rgb*rt.x*coverage;\n"
"    o.c0=float4(alpha>0.0f?reflected/alpha:float3(0),alpha);\n"
"#endif\n"
"    if (m.k[1].w>0.5f) { float mx=max(o.c0.r,max(o.c0.g,o.c0.b));\n"
"        if(mx>0.8f) { float y=0.8f+0.2f*(1.0f-exp(-(mx-0.8f)/0.2f));\n"
"            o.c0.rgb=mix(o.c0.rgb*(y/mx),float3(y),0.5f*saturate((mx-1.0f)*0.5f)); } }\n"
SM_ALPHA_COVERAGE_APPLY("o.c0.w")
"    if (aux.alpha_func!=0u && !alpha_test(aux.alpha_func,o.c0.a,aux.alpha_ref)) discard_fragment();\n"
"    return o;\n}\n";
    size_t size = (size_t)(cut-source)+strlen(optics)+strlen(tail)+256;
    char *out=malloc(size); if(!out)return NULL;
    int n=snprintf(out,size,"#define MAT_VARIANT %d\n#define MAT_SHADOW_HW %d\n#define MAT_CAR_HDR 1\n#define RW_CHROME %d\n",variant,shadow,chrome);
    memcpy(out+n,source,(size_t)(body-source)); n+=(int)(body-source);
    memcpy(out+n,optics,strlen(optics)); n+=(int)strlen(optics);
    memcpy(out+n,body,(size_t)(cut-body)); n+=(int)(cut-body); strcpy(out+n,tail);
    char *next=replace(out,"#if MAT_VARIANT == 1\n","#if MAT_VARIANT == 0\n    lit=1.0f;\n#elif MAT_VARIANT == 1\n");
    free(out);
    if (!variant && next) {
        // The unshadowed car technique binds cube s0 and diffuse s1. Keep the
        // resource names used by the optics, but match the native registers.
        out=replace(next,"tex0 [[texture(0)]], sampler smp0 [[sampler(0)]]",
                         "tex0 [[texture(1)]], sampler smp0 [[sampler(1)]]");
        free(next);
        next=out ? replace(out,"tex1 [[texture(1)]], sampler smp1 [[sampler(1)]]",
                              "tex1 [[texture(0)]], sampler smp1 [[sampler(0)]]") : NULL;
        free(out);
    }
    return next;
}
