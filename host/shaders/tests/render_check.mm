// render_check.mm - end-to-end test of the stage wrappers. For every VS/PS pair bound by an
// effect pass (testdata/passes.tsv) it draws one point per pixel of a 64x64 RGBA32F target
// through the real vertex + fragment functions (attributes, constant buffers, half-pixel
// fix-up, varyings, texkill / alpha test, colour outputs), and compares every pixel with the
// CPU interpreter running the VS, then the PS, on the same inputs.
//
//   build/render_check [testdata] [--only substr]
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>

#include "../sm_ir.h"
#include "../sm_translate.h"
#include "common.h"
#include "sm_interp.h"

#include <cmath>
#include <map>

struct Rng {
    uint64_t s;
    explicit Rng(uint64_t seed) : s(seed * 0x9E3779B97F4A7C15ull + 1) {}
    uint32_t next() { s ^= s << 13; s ^= s >> 7; s ^= s << 17; return (uint32_t)(s >> 11); }
    float uni(float a, float b) { return a + (b - a) * (float)(next() & 0xFFFFFF) / (float)0x1000000; }
    int range(int a, int b) { return a + (int)(next() % (uint32_t)(b - a + 1)); }
};

static const int W = 64, H = 64;

struct Stage {
    std::vector<uint32_t> tok;
    sm::Shader sh;
    SmResult res = {};
    id<MTLFunction> fn = nil;
};

static bool load(id<MTLDevice> dev, const std::string &path, Stage &st, std::string &err) {
    st.tok = read_tokens(path);
    if (!sm::parse(st.tok.data(), st.tok.size(), st.sh)) { err = st.sh.error; return false; }
    SmOptions opt;
    sm_default_options(&opt);
    if (sm_translate(st.tok.data(), st.tok.size(), &opt, &st.res) != 0) { err = st.res.error; return false; }
    NSError *e = nil;
    id<MTLLibrary> lib = [dev newLibraryWithSource:[NSString stringWithUTF8String:st.res.msl] options:[MTLCompileOptions new] error:&e];
    if (!lib) { err = e.localizedDescription.UTF8String; return false; }
    st.fn = [lib newFunctionWithName:@"sm_main"];
    return true;
}

static bool close_enough(float a, float b, float tol) {
    if (a != a && b != b) return true;
    if (std::isinf(a) || std::isinf(b)) return a == b;
    return std::fabs(a - b) <= tol * std::max(1.0f, std::max(std::fabs(a), std::fabs(b)));
}

int main(int argc, char **argv) {
    @autoreleasepool {
        std::string td = "testdata", only;
        for (int i = 1; i < argc; i++) {
            std::string a = argv[i];
            if (a == "--only" && i + 1 < argc) only = argv[++i];
            else td = a;
        }
        id<MTLDevice> dev = MTLCreateSystemDefaultDevice();
        id<MTLCommandQueue> q = [dev newCommandQueue];
        std::ifstream f(td + "/passes.tsv");
        std::string line;
        std::getline(f, line);
        int passes = 0, good = 0, badp = 0;
        long checked_px = 0;
        while (std::getline(f, line)) {
            auto c = split(line, '\t');
            if (c.size() < 6 || c[4] == "-" || c[5] == "-") continue;
            std::string where = c[0] + " t" + c[1] + ":" + c[2] + "/" + c[3];
            if (!only.empty() && where.find(only) == std::string::npos) continue;
            passes++;
            Stage vs, ps;
            std::string err;
            if (!load(dev, td + "/" + c[0] + "/" + c[4], vs, err) || !load(dev, td + "/" + c[0] + "/" + c[5], ps, err)) {
                printf("FAIL %s: %s\n", where.c_str(), err.c_str());
                badp++;
                continue;
            }
            for (int round = 0; round < 2; round++) {
                Rng rng(std::hash<std::string>()(where) + round * 31337u);
                // ---- constants
                std::vector<float> vc(256 * 4), pc(256 * 4);
                for (float &x : vc) x = rng.uni(-2.0f, 2.0f);
                for (float &x : pc) x = rng.uni(-1.5f, 1.5f);
                bool identity = false;
                for (int k = 0; k < vs.res.n_constants; k++) {
                    const SmConstant &k0 = vs.res.constants[k];
                    if (k0.set == 2 && std::string(k0.name) == "BlendMatrices")   // skinning palette: identity bones
                        for (int r = 0; r < k0.count; r++) for (int cc = 0; cc < 4; cc++) vc[(k0.reg + r) * 4 + cc] = (r % 4) == cc ? 1.0f : 0.0f;
                    if (k0.set == 2 && k0.count == 4 && std::string(k0.name) == "WorldViewProj") {
                        for (int r = 0; r < 4; r++) for (int cc = 0; cc < 4; cc++) vc[(k0.reg + r) * 4 + cc] = r == cc ? 1.0f : 0.0f;
                        identity = true;
                    }
                }
                std::vector<int> ci(64), cb(16);
                for (int k = 0; k < 16; k++) { ci[k * 4] = rng.range(0, 4); ci[k * 4 + 1] = rng.range(0, 8); ci[k * 4 + 2] = rng.range(1, 2); }
                for (int &b : cb) b = rng.range(0, 1);
                SmVSAux vaux = {{1.0f / W, -1.0f / H, 0, 0}, 1.0f, {0, 0, 0}};
                SmPSAux paux = {};
                if (round == 1) { paux.alpha_func = 5; paux.alpha_ref = 0.5f; }   // D3DCMP_GREATER 0.5
                // ---- textures (pixel shader)
                interp::Texture texs[16];
                id<MTLTexture> mtex[16] = {nil};
                id<MTLSamplerState> msmp[16] = {nil};
                interp::Context vctx, pctx;
                vctx.c = vc.data(); vctx.ci = ci.data(); vctx.cb = cb.data();
                pctx.c = pc.data(); pctx.ci = ci.data(); pctx.cb = cb.data(); pctx.aux = paux;
                sm_default_options(&vctx.opt);
                sm_default_options(&pctx.opt);
                for (int k = 0; k < ps.res.n_samplers; k++) {
                    const SmSampler &smp = ps.res.samplers[k];
                    interp::Texture &t = texs[smp.reg];
                    t.type = smp.type;
                    t.w = t.h = smp.type == SM_TEX_3D ? 4 : 8;
                    t.d = smp.type == SM_TEX_3D ? 4 : smp.type == SM_TEX_CUBE ? 6 : 1;
                    t.texels.resize((size_t)t.w * t.h * t.d * 4);
                    for (float &x : t.texels) x = rng.uni(0.0f, 1.0f);
                    MTLTextureDescriptor *d = [MTLTextureDescriptor new];
                    d.pixelFormat = MTLPixelFormatRGBA32Float;
                    d.width = t.w; d.height = t.h;
                    d.textureType = smp.type == SM_TEX_CUBE ? MTLTextureTypeCube : smp.type == SM_TEX_3D ? MTLTextureType3D : MTLTextureType2D;
                    if (smp.type == SM_TEX_3D) d.depth = t.d;
                    mtex[smp.reg] = [dev newTextureWithDescriptor:d];
                    size_t row = (size_t)t.w * 16, slice = row * t.h;
                    if (smp.type == SM_TEX_CUBE)
                        for (int fc = 0; fc < 6; fc++)
                            [mtex[smp.reg] replaceRegion:MTLRegionMake2D(0, 0, t.w, t.h) mipmapLevel:0 slice:fc
                                               withBytes:t.texels.data() + fc * t.w * t.h * 4 bytesPerRow:row bytesPerImage:slice];
                    else
                        [mtex[smp.reg] replaceRegion:MTLRegionMake3D(0, 0, 0, t.w, t.h, t.d) mipmapLevel:0 slice:0
                                           withBytes:t.texels.data() bytesPerRow:row bytesPerImage:slice];
                    MTLSamplerDescriptor *sd = [MTLSamplerDescriptor new];
                    sd.minFilter = sd.magFilter = MTLSamplerMinMagFilterNearest;
                    sd.sAddressMode = sd.tAddressMode = sd.rAddressMode = MTLSamplerAddressModeClampToEdge;
                    msmp[smp.reg] = [dev newSamplerStateWithDescriptor:sd];
                    pctx.tex[smp.reg] = &t;
                }
                // ---- vertices: one point per pixel, all 16 attribute registers
                const int NP = W * H;
                std::vector<float> verts((size_t)NP * 64);
                for (int p = 0; p < NP; p++) {
                    float *v = &verts[(size_t)p * 64];
                    for (int k = 0; k < 64; k++) v[k] = rng.uni(-1.0f, 1.0f);
                    for (int k = 0; k < vs.res.n_inputs; k++) {
                        const SmInput &si = vs.res.inputs[k];
                        float *a = v + si.reg * 4;
                        switch (si.usage) {
                        case SM_USAGE_POSITION: {
                            int px = p % W, py = p / W;
                            a[0] = 2.0f * px / W - 1.0f;   // D3D9 pixel centre (integer screen coords)
                            a[1] = 1.0f - 2.0f * py / H;
                            a[2] = rng.uni(0.1f, 0.9f);
                            a[3] = 1.0f;
                            break;
                        }
                        case SM_USAGE_BLENDINDICES: for (int cc = 0; cc < 4; cc++) a[cc] = (float)rng.range(0, 15); break;
                        case SM_USAGE_BLENDWEIGHT: case SM_USAGE_COLOR: for (int cc = 0; cc < 4; cc++) a[cc] = rng.uni(0.0f, 1.0f); break;
                        case SM_USAGE_TEXCOORD: for (int cc = 0; cc < 4; cc++) a[cc] = rng.uni(-1.0f, 2.0f); break;
                        default: break;
                        }
                    }
                }
                // ---- CPU reference: VS -> predicted pixel -> PS
                struct Expect { bool valid; bool killed; float rgba[4][4]; };
                std::vector<Expect> expect((size_t)W * H);
                std::vector<int> hits((size_t)W * H, 0);
                std::vector<int> owner((size_t)W * H, -1);
                std::vector<interp::VSOut> vouts(NP);
                for (int p = 0; p < NP; p++) {
                    interp::VSIn vi;
                    for (int r = 0; r < 16; r++) for (int k = 0; k < 4; k++) vi.v[r][k] = verts[(size_t)p * 64 + r * 4 + k];
                    interp::run_vs(vs.sh, vctx, vi, vouts[p]);
                    const interp::V4 &P = vouts[p].pos;
                    if (!(P[3] > 0) || P[2] < 0 || P[2] > P[3]) continue;
                    float xs = (P[0] / P[3] + 1.0f) * 0.5f * W, ys = (1.0f - P[1] / P[3]) * 0.5f * H;   // D3D screen
                    float fx = xs + 0.5f - std::floor(xs + 0.5f), fy = ys + 0.5f - std::floor(ys + 0.5f);
                    int ix = (int)std::floor(xs + 0.5f), iy = (int)std::floor(ys + 0.5f);
                    if (std::fabs(fx - 0.5f) > 0.4f || std::fabs(fy - 0.5f) > 0.4f) {
                        // too close to a pixel edge: may land in either neighbour, poison all of them
                        for (int dy = -1; dy <= 1; dy++)
                            for (int dx = -1; dx <= 1; dx++) {
                                int x = ix + dx, y = iy + dy;
                                if (x >= 0 && y >= 0 && x < W && y < H) hits[y * W + x] += 2;
                            }
                        continue;
                    }
                    if (ix < 0 || iy < 0 || ix >= W || iy >= H) continue;
                    hits[iy * W + ix]++;
                    owner[iy * W + ix] = p;
                }
                for (int px = 0; px < W * H; px++) {
                    Expect &e = expect[px];
                    e.valid = hits[px] == 1;
                    if (!e.valid) continue;
                    const interp::VSOut &vo = vouts[owner[px]];
                    interp::PSIn pi;
                    pi.col[0] = vo.col[0]; pi.col[1] = vo.col[1];
                    for (int k = 0; k < 8; k++) pi.tex[k] = vo.tex[k];
                    pi.fog = vo.fog;
                    pi.vpos = {(float)(px % W), (float)(px / W), vo.pos[2] / vo.pos[3], 1.0f};
                    interp::PSOut po;
                    interp::run_ps(ps.sh, pctx, pi, po);
                    bool atest = paux.alpha_func == 0 ||
                                 std::rint(std::min(std::max(po.oc[0][3], 0.0f), 1.0f) * 255.0f) > std::rint(paux.alpha_ref * 255.0f);
                    e.killed = po.killed || !atest;
                    for (int r = 0; r < 4; r++) for (int k = 0; k < 4; k++) e.rgba[r][k] = po.oc[r][k];
                }
                // ---- GPU
                MTLRenderPipelineDescriptor *pd = [MTLRenderPipelineDescriptor new];
                pd.vertexFunction = vs.fn;
                pd.fragmentFunction = ps.fn;
                MTLVertexDescriptor *vd = [MTLVertexDescriptor vertexDescriptor];
                for (int k = 0; k < vs.res.n_inputs; k++) {
                    int reg = vs.res.inputs[k].reg;
                    vd.attributes[reg].format = MTLVertexFormatFloat4;
                    vd.attributes[reg].offset = 16 * reg;
                    vd.attributes[reg].bufferIndex = 0;
                }
                vd.layouts[0].stride = 256;
                pd.vertexDescriptor = vd;
                pd.inputPrimitiveTopology = MTLPrimitiveTopologyClassPoint;
                id<MTLTexture> rt[4] = {nil};
                MTLRenderPassDescriptor *rp = [MTLRenderPassDescriptor renderPassDescriptor];
                for (int k = 0; k < 4; k++) {
                    if (!(ps.res.color_outputs & (1 << k))) continue;
                    pd.colorAttachments[k].pixelFormat = MTLPixelFormatRGBA32Float;
                    MTLTextureDescriptor *d = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA32Float width:W height:H mipmapped:NO];
                    d.usage = MTLTextureUsageRenderTarget;
                    d.storageMode = MTLStorageModeShared;
                    rt[k] = [dev newTextureWithDescriptor:d];
                    rp.colorAttachments[k].texture = rt[k];
                    rp.colorAttachments[k].loadAction = MTLLoadActionClear;
                    rp.colorAttachments[k].clearColor = MTLClearColorMake(-7, -7, -7, -7);
                    rp.colorAttachments[k].storeAction = MTLStoreActionStore;
                }
                NSError *e = nil;
                id<MTLRenderPipelineState> pso = [dev newRenderPipelineStateWithDescriptor:pd error:&e];
                if (!pso) { printf("FAIL %s: pipeline %s\n", where.c_str(), e.localizedDescription.UTF8String); badp++; break; }
                id<MTLBuffer> vb = [dev newBufferWithBytes:verts.data() length:verts.size() * 4 options:MTLResourceStorageModeShared];
                id<MTLCommandBuffer> cmd = [q commandBuffer];
                id<MTLRenderCommandEncoder> enc = [cmd renderCommandEncoderWithDescriptor:rp];
                [enc setRenderPipelineState:pso];
                [enc setVertexBuffer:vb offset:0 atIndex:0];
                [enc setVertexBytes:vc.data() length:vc.size() * 4 atIndex:SM_VS_BUF_FLOAT];
                [enc setVertexBytes:ci.data() length:ci.size() * 4 atIndex:SM_VS_BUF_INT];
                [enc setVertexBytes:cb.data() length:cb.size() * 4 atIndex:SM_VS_BUF_BOOL];
                [enc setVertexBytes:&vaux length:sizeof vaux atIndex:SM_VS_BUF_AUX];
                [enc setFragmentBytes:pc.data() length:pc.size() * 4 atIndex:SM_PS_BUF_FLOAT];
                [enc setFragmentBytes:ci.data() length:ci.size() * 4 atIndex:SM_PS_BUF_INT];
                [enc setFragmentBytes:cb.data() length:cb.size() * 4 atIndex:SM_PS_BUF_BOOL];
                [enc setFragmentBytes:&paux length:sizeof paux atIndex:SM_PS_BUF_AUX];
                for (int s = 0; s < 16; s++)
                    if (mtex[s]) { [enc setFragmentTexture:mtex[s] atIndex:s]; [enc setFragmentSamplerState:msmp[s] atIndex:s]; }
                [enc drawPrimitives:MTLPrimitiveTypePoint vertexStart:0 vertexCount:NP];
                [enc endEncoding];
                [cmd commit];
                [cmd waitUntilCompleted];
                // ---- compare
                int bad = 0, valid = 0, killed = 0;
                std::string first;
                std::vector<float> img[4];
                for (int k = 0; k < 4; k++)
                    if (rt[k]) {
                        img[k].resize((size_t)W * H * 4);
                        [rt[k] getBytes:img[k].data() bytesPerRow:W * 16 fromRegion:MTLRegionMake2D(0, 0, W, H) mipmapLevel:0];
                    }
                for (int px = 0; px < W * H; px++) {
                    const Expect &ex = expect[px];
                    if (!ex.valid) continue;
                    valid++;
                    if (ex.killed) killed++;
                    for (int k = 0; k < 4 && first.size() < 2; k++) {
                        if (!rt[k]) continue;
                        const float *g = &img[k][(size_t)px * 4];
                        for (int cc = 0; cc < 4; cc++) {
                            float want = ex.killed ? -7.0f : ex.rgba[k][cc];
                            if (!close_enough(g[cc], want, 2e-3f)) {
                                if (first.empty()) {
                                    char b[200];
                                    snprintf(b, sizeof b, "pixel (%d,%d) oC%d.%c gpu %.7g cpu %.7g%s", px % W, px / W, k, "xyzw"[cc], g[cc], want,
                                             ex.killed ? " (cpu: discarded)" : "");
                                    first = b;
                                }
                                bad++;
                                k = 4;
                                break;
                            }
                        }
                    }
                }
                checked_px += valid;
                if (bad || valid < W * H / 4) {
                    printf("FAIL %s round %d: %d/%d pixels differ (%d checked, WVP identity %d); first: %s\n", where.c_str(), round,
                           bad, valid, valid, identity, first.c_str());
                    badp++;
                    break;
                }
                if (round == 1) {
                    good++;
                    printf("ok   %-44s %4d px checked, %4d discarded (texkill / alpha test)\n", where.c_str(), valid, killed);
                }
            }
            sm_result_free(&vs.res);
            sm_result_free(&ps.res);
        }
        printf("render check: %d passes, %d match, %d differ, %ld pixels compared\n", passes, good, badp, checked_px);
        return badp ? 1 : 0;
    }
}
