// shaders_msl.h - the MSL behind every program gpu/shaders.md names. Moved
// verbatim from compositor.mm, performance_overlay.h and d3d_render.mm; the
// slot numbers are the ones shaders.md documents.
#pragma once

namespace gpu::metal {

// "compositor": compositor_vertex / compositor_fragment
inline const char *const kCompositorSource = R"MSL(
#include <metal_stdlib>
using namespace metal;
struct Quad { float4 rect; float4 uv; float2 drawable; uint opaque; float brightness; }; // brightness: 0 = unchanged (3.0.4)
struct Varying { float4 position [[position]]; float2 uv; };
vertex Varying compositor_vertex(uint id [[vertex_id]], constant Quad& q [[buffer(0)]]) {
    const float2 corners[4]={float2(0,0),float2(1,0),float2(0,1),float2(1,1)};
    float2 p=q.rect.xy+corners[id]*q.rect.zw;
    Varying v;
    v.position=float4(p.x/q.drawable.x*2-1,1-p.y/q.drawable.y*2,0,1);
    v.uv=q.uv.xy+corners[id]*q.uv.zw;
    return v;
}
fragment float4 compositor_fragment(Varying v [[stage_in]],
                                    constant Quad& q [[buffer(0)]],
                                    texture2d<float> tex [[texture(0)]]) {
    constexpr sampler nearest(coord::normalized,address::clamp_to_edge,filter::nearest);
    float4 c=tex.sample(nearest,v.uv);
    if(q.opaque) c.a=1;
    if(q.brightness>0.0) c.rgb=pow(max(c.rgb,0.0),q.brightness);   // 3.0.4 Brightness: black and white stay
    return c;
}
// Test86 HDR output (RGBA16F linear extended sRGB drawable, 1.0 = SDR white). mode 0: decode the sRGB
// source (UI, menus, movies); 1: the game frame - decode, then where its alpha marks the scene (the core
// mod sets it before the HUD; the HUD clears it) expand the highlights above `knee` toward `headroom`,
// hue kept; 2: the source is already linear (a re-fit of the composed frame).
// 3 (PLAN_R2 item 10, review/run14/hdr-design.md s3): the game frame is the renderer's float back buffer, values above 1
// kept: extended decode; under the scene mark identity to SDR white, then a hue-kept shoulder on the max channel m,
// f(m) = 1 + (H-1) u/(1+u), u = (m-1)/(H-1) (f(1)=1, f'(1)=1, f(inf)=H), hot cores whitened by `desat` (the spare
// slot); outside the mark (HUD, mirror frame) clipped at SDR white. 4: the same frame without a scene mark: clipped.
struct Hdr { float mode; float headroom; float knee; float desat; };
float3 hdr_shoulder(float3 lin, float H, float D) {
    const float m = max(max(lin.r, lin.g), lin.b);
    if (m <= 1.0 || H <= 1.001) return min(lin, 1.0);
    const float u = (m - 1.0) / (H - 1.0);
    const float f = 1.0 + (H - 1.0) * u / (1.0 + u);
    const float3 rgb = lin * (f / m);
    return mix(rgb, float3(f), D * (f - 1.0) / (H - 1.0));
}
float3 hdr_decode(float3 c) {
    c = max(c, 0.0);
    return select(pow((c + 0.055) / 1.055, 2.4), c / 12.92, c <= 0.04045);
}
fragment float4 compositor_hdr_fragment(Varying v [[stage_in]],
                                        constant Quad& q [[buffer(0)]],
                                        constant Hdr& h [[buffer(1)]],
                                        texture2d<float> tex [[texture(0)]]) {
    constexpr sampler nearest(coord::normalized,address::clamp_to_edge,filter::nearest);
    float4 c=tex.sample(nearest,v.uv);
    const float mask=c.a;
    if(q.opaque) c.a=1;
    if(q.brightness>0.0 && (h.mode<1.5 || h.mode>2.5)) c.rgb=pow(max(c.rgb,0.0),q.brightness);   // 3.0.4, encoded colour
    if(h.mode>4.5 && v.uv.y<0.1) {   // R2 #10 TEST bars: 1, 2, 4, 8 x SDR white through the mode-3 shoulder
        const float bar=exp2(floor(saturate(v.uv.x)*3.999));
        return float4(hdr_shoulder(float3(bar),h.headroom,h.desat),1.0);
    }
    if(h.mode>2.5) {   // item 10 (5 = 3 with the TEST bars)
        const float3 lin=hdr_decode(c.rgb);
        return float4((h.mode<3.5 || h.mode>4.5) && mask>0.5 ? hdr_shoulder(lin,h.headroom,h.desat) : min(lin,1.0), c.a);
    }
    if(h.mode>1.5) return c;
    float3 lin=hdr_decode(c.rgb);
    if(h.mode>0.5 && mask>0.5 && h.headroom>1.0) {
        const float L=dot(lin,float3(0.2126,0.7152,0.0722));
        const float t=saturate((L-h.knee)/max(1.0-h.knee,1e-3));
        lin*=1.0+(h.headroom-1.0)*t*t;
    }
    return float4(lin,c.a);
}
)MSL";

// "hud": hud_v / hud_f
inline const char *const kHudSource = R"MSL(
#include <metal_stdlib>
using namespace metal;
struct V { float4 p [[position]]; float2 uv; };
vertex V hud_v(uint i [[vertex_id]], constant float4& r [[buffer(0)]]) {
    float2 q = float2(i & 1, i >> 1);
    V v; v.p = float4(r.xy + q * r.zw, 0, 1); v.uv = q; return v;
}
fragment float4 hud_f(V v [[stage_in]], texture2d<float> t [[texture(0)]]) {
    constexpr sampler s(filter::linear);
    return t.sample(s, v.uv);
}
)MSL";

// "d3d", "surface_upload", "guest_readback", "guest_readback_fused", "native_brightness"
inline const char *const kD3DSource = R"MSL(
#include <metal_stdlib>
using namespace metal;

struct HVertex {
    float x, y, z, w;
    float u, v;
    float r, g, b, a;
    float sr, sg, sb, sa;
};
struct Uniforms {
    float4x4 mvp;
    uint  pretransformed;
    uint  textured;
    uint  texblend;
    uint  alphatest;
    uint  alphafunc;
    float alpharef;
    uint  specular;
    uint  texture_has_alpha;
    uint  fogmode;        // 0 off, 1 vertex, 2 exp, 3 exp2, 4 linear
    float fogstart, fogend, fogdensity;
    float fogr, fogg, fogb;
    float pointsize;
    uint terrain_detail;
};
struct VOut {
    float4 position [[position]];
    float  size [[point_size]];
    float2 uv;
    float4 color;
    float4 spec;
    float  fogdist;
};
// Colour, and the coverage mask that says this pixel was rasterized. A
// discarded fragment writes neither.
struct FOut {
    float4 color    [[color(0)]];
    float  coverage [[color(1)]];
};

vertex VOut d3d_vertex(uint vid [[vertex_id]],
                       const device HVertex* v [[buffer(0)]],
                       constant Uniforms& u [[buffer(1)]]) {
    HVertex s = v[vid];
    VOut o;
    o.position = u.pretransformed ? float4(s.x, s.y, s.z, s.w)
                                  : u.mvp * float4(s.x, s.y, s.z, 1.0);
    o.size = u.pointsize;
    o.uv = float2(s.u, s.v);
    o.color = float4(s.r, s.g, s.b, s.a);
    o.spec = float4(s.sr, s.sg, s.sb, s.sa);
    // Table fog is a function of eye-space distance, which after a standard
    // projection is the clip w.
    o.fogdist = abs(o.position.w);
    return o;
}

// Shade a world fragment using the guest material and optional terrain detail.
// Apply the legacy texture/alpha rules before writing color and coverage attachments.
fragment FOut d3d_fragment(VOut in [[stage_in]],
                           constant Uniforms& u [[buffer(1)]],
                           texture2d<float> tex [[texture(0)]],
                           sampler samp [[sampler(0)]],
                           texture2d<float> detail [[texture(1)]],
                           sampler detail_samp [[sampler(1)]]) {
    float4 c = in.color;
    if (u.textured != 0) {
        float4 t = tex.sample(samp, in.uv);
        if (u.terrain_detail != 0) {
            // An independent material sample supplies real fine structures;
            // enlarging the guest's 16/32 texels cannot create those. The
            // source remains the color/lighting/coastline mask. Blue water
            // fades this land-only layer out, including mixed shore texels.
            float3 chroma = t.rgb / max(max(t.r, t.g), max(t.b, 0.01f));
            float land = 1.0f - smoothstep(0.02f, 0.22f, chroma.b - chroma.r);
            float grass = smoothstep(-0.03f, 0.16f, chroma.g - chroma.r);
            float stone = 1.0f - smoothstep(0.06f, 0.28f, max(chroma.r, chroma.g) - min(chroma.r, min(chroma.g, chroma.b)));
            float sand = smoothstep(0.28f, 0.62f, max(t.r, t.g));
            float4 structure = (detail.sample(detail_samp, in.uv) * 255.0f - 128.0f) / 128.0f;
            float material = mix(structure.a, structure.g, sand);
            material = mix(material, structure.b, stone);
            material = mix(material, structure.r, grass);
            t.rgb = saturate(t.rgb * (1.0f + material * 0.85f * land));
        }
        switch (u.texblend) {
            case 1: case 7:                                   // DECAL, COPY
                c = t; break;
            case 2:                                           // MODULATE
                // The legacy rule: the texture's alpha when it has one,
                // otherwise the diffuse's. Taking the vertex alpha from a
                // vertex that never set it - and plenty of D3DTLVERTEX
                // colours leave it at zero - makes every blended pixel
                // invisible and the whole scene the colour of the clear.
                c = float4(t.rgb * in.color.rgb,
                           u.texture_has_alpha != 0 ? t.a : in.color.a);
                break;
            case 3:                                           // DECALALPHA
                c = float4(mix(in.color.rgb, t.rgb, t.a), in.color.a); break;
            case 5:                                           // DECALMASK
                c = float4(t.rgb, in.color.a); break;
            case 8:                                           // ADD
                c = float4(saturate(t.rgb + in.color.rgb), in.color.a); break;
            case 4:                                           // MODULATEALPHA
                c = float4(t.rgb * in.color.rgb, t.a * in.color.a); break;
            default:
                c = float4(t.rgb * in.color.rgb,
                           u.texture_has_alpha != 0 ? t.a : in.color.a);
                break;
        }
    }
    if (u.specular != 0) c = float4(saturate(c.rgb + in.spec.rgb), c.a);
    if (u.alphatest != 0) {
        bool pass;
        switch (u.alphafunc) {
            case 1: pass = false;                break;   // NEVER
            case 2: pass = c.a <  u.alpharef;    break;   // LESS
            case 3: pass = c.a == u.alpharef;    break;   // EQUAL
            case 4: pass = c.a <= u.alpharef;    break;   // LESSEQUAL
            case 5: pass = c.a >  u.alpharef;    break;   // GREATER
            case 6: pass = c.a != u.alpharef;    break;   // NOTEQUAL
            case 7: pass = c.a >= u.alpharef;    break;   // GREATEREQUAL
            default: pass = true;                break;   // ALWAYS
        }
        if (!pass) discard_fragment();
    }
    if (u.fogmode != 0) {
        // The fog factor is how much of the fragment survives: 1 is clear.
        float f = 1.0;
        float d = in.fogdist;
        switch (u.fogmode) {
            case 1: f = in.spec.a; break;                          // vertex fog
            case 2: f = exp(-u.fogdensity * d); break;             // EXP
            case 3: f = exp(-(u.fogdensity * d) * (u.fogdensity * d)); break;
            default:                                               // LINEAR
                f = (u.fogend - d) / max(u.fogend - u.fogstart, 1e-6);
                break;
        }
        f = saturate(f);
        c = float4(mix(float3(u.fogr, u.fogg, u.fogb), c.rgb, f), c.a);
    }
    FOut o;
    o.color = c;
    o.coverage = 1.0;
    return o;
}
// Reduce guest-visible reads on the GPU; never decompress a 4K texture on
// the CPU merely to select 640x480 pixel centres. Each lane also counts its
// disjoint native cell so existing full-resolution diagnostics stay exact.
kernel void guest_readback_fused(texture2d<float, access::read> color [[texture(0)]],
                           texture2d<float, access::read> coverage [[texture(1)]],
                           device uint2* output [[buffer(0)]],
                           constant uint4* p [[buffer(1)]],
                           uint2 tid [[thread_position_in_grid]]) {
    uint2 span=p[0].zw-p[0].xy;
    if(any(tid>=span)) return;
    uint2 guest=p[0].xy+tid, native_size=p[1].xy, guest_size=p[1].zw;
    uint2 pos=min(((2*guest+1)*native_size)/(2*guest_size),p[2].yz-1);
    uint3 rgb=uint3(round(color.read(pos).rgb*255.0f));
    uint covered=coverage.read(pos).r>0 ? 1:0;
    uint2 lo=(guest*native_size+guest_size-1)/guest_size;
    uint2 hi=((guest+1)*native_size+guest_size-1)/guest_size;
    uint lit=0;
    for(uint y=lo.y;y<hi.y;++y) for(uint x=lo.x;x<hi.x;++x)
        lit+=any(round(color.read(uint2(x,y)).rgb*255.0f)>8.0f) ? 1:0;
    output[p[2].x+tid.y*span.x+tid.x]=uint2(rgb.b|(rgb.g<<8)|(rgb.r<<16)|(covered<<24),lit);
}
// Guest sampling and native brightness reduction have different grids.
// Keeping the sampling lanes short avoids a variable native-cell loop in
// every guest lane. Packing and coverage rules match the fused reference.
kernel void guest_readback(texture2d<float, access::read> color [[texture(0)]],
                           texture2d<float, access::read> coverage [[texture(1)]],
                           device uint2* output [[buffer(0)]],
                           constant uint4* p [[buffer(1)]],
                           uint2 tid [[thread_position_in_grid]]) {
    uint2 span=p[0].zw-p[0].xy;
    if(any(tid>=span)) return;
    uint2 guest=p[0].xy+tid, native_size=p[1].xy, guest_size=p[1].zw;
    uint2 pos=min(((2*guest+1)*native_size)/(2*guest_size),p[2].yz-1);
    uint3 rgb=uint3(round(color.read(pos).rgb*255.0f));
    uint covered=coverage.read(pos).r>0 ? 1:0;
    output[p[2].x+tid.y*span.x+tid.x]=uint2(rgb.b|(rgb.g<<8)|(rgb.r<<16)|(covered<<24),0);
}
// A 16x16 threadgroup covers a 32x32 native tile. Each lane reads four
// adjacent pixels and each SIMD group writes one sum. Padded lanes contribute
// zero but participate in the reduction; no contended global atomic is used.
kernel void native_brightness(texture2d<float, access::read> color [[texture(0)]],
                              device uint* output [[buffer(0)]],
                              constant uint4* p [[buffer(1)]],
                              uint2 tid [[thread_position_in_grid]],
                              uint2 group [[threadgroup_position_in_grid]],
                              uint lane [[thread_index_in_simdgroup]],
                              uint simd_group [[simdgroup_index_in_threadgroup]]) {
    uint2 pos=p[0].xy+tid*2;
    uint lit=0;
    for(uint y=0;y<2;++y) for(uint x=0;x<2;++x) {
        uint2 q=pos+uint2(x,y);
        if(all(q<p[0].zw)) lit+=any(round(color.read(q).rgb*255.0f)>8.0f) ? 1:0;
    }
    uint sum=simd_sum(lit);
    if(lane==0) output[p[1].x+(group.y*p[1].y+group.x)*p[1].z+simd_group]=sum;
}
// Seed a native target directly from an immutable guest-sized buffer. Integer
// channel expansion and floor(x * guest / native) preserve the CPU upload's
// exact pixels without expanding the whole 4K image on the guest thread.
vertex float4 surface_upload_vertex(uint id [[vertex_id]]) {
    float2 corner=float2((id<<1)&2,id&2);
    return float4(corner*2.0f-1.0f,0,1);
}
fragment float4 surface_upload_fragment(float4 position [[position]],
    device const uchar* pixels [[buffer(0)]],
    constant uint4* p [[buffer(1)]], constant uint* palette [[buffer(2)]]) {
    uint2 xy=uint2(position.xy)*p[0].xy/p[0].zw;
    uint offset=xy.y*p[1].x+xy.x*(p[1].y==8 ? 1:(p[1].y<=16 ? 2:4));
    uint3 rgb;
    if(p[1].y==8) {
        uint index=pixels[offset];
        uint c=p[1].z ? palette[index] : index*0x010101u;
        rgb=uint3((c>>16)&255,(c>>8)&255,c&255);
    } else {
        uint value=uint(pixels[offset])|(uint(pixels[offset+1])<<8);
        if(p[1].y>16) value|=(uint(pixels[offset+2])<<16)|(uint(pixels[offset+3])<<24);
        rgb=((uint3(value)&p[2].xyz)>>p[3].xyz)*255/p[4].xyz;
    }
    return float4(float3(rgb)/255.0f,1);
}
)MSL";

} // namespace gpu::metal
