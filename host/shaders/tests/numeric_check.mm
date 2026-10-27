// numeric_check.mm - runs every translated shader on the GPU (Metal compute kernel
// sm_test_kernel = the generated shader body) on random inputs and compares all outputs
// with the CPU reference interpreter (sm_interp.cpp) executing the same bytecode.
//
//   build/numeric_check [testdata] [--math fast|relaxed|safe] [--n threads] [--only substr] [-v]
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>

#include "../sm_ir.h"
#include "../sm_translate.h"
#include "common.h"
#include "sm_interp.h"

#include <cmath>
#include <string>
#include <vector>

struct Rng {
    uint64_t s;
    explicit Rng(uint64_t seed) : s(seed * 0x9E3779B97F4A7C15ull + 1) {}
    uint32_t next() { s ^= s << 13; s ^= s >> 7; s ^= s << 17; return (uint32_t)(s >> 11); }
    float uni(float a, float b) { return a + (b - a) * (float)(next() & 0xFFFFFF) / (float)0x1000000; }
    int range(int a, int b) { return a + (int)(next() % (uint32_t)(b - a + 1)); }
};

static uint64_t hash_str(const std::string &s) {
    uint64_t h = 1469598103934665603ull;
    for (char c : s) h = (h ^ (uint8_t)c) * 1099511628211ull;
    return h;
}

static bool close_enough(float a, float b, float tol, float *err) {
    if (a != a && b != b) { *err = 0; return true; }
    if (std::isinf(a) || std::isinf(b)) { *err = (a == b) ? 0 : INFINITY; return a == b; }
    float d = std::fabs(a - b);
    float scale = std::max(1.0f, std::max(std::fabs(a), std::fabs(b)));
    *err = d / scale;
    return d <= tol * scale;
}

struct Stats { int shaders = 0, passed = 0, failed = 0, illcond = 0; };

int main(int argc, char **argv) {
    @autoreleasepool {
        std::string td = "testdata", only, math = "fast";
        int N = 1024, rounds = 3;
        bool verbose = false;
        int trace_thread = -1;
        bool shadow_all = false;   // every 2D sampler is a depth texture sampled with compare
        for (int i = 1; i < argc; i++) {
            std::string a = argv[i];
            if (a == "--math" && i + 1 < argc) math = argv[++i];
            else if (a == "--n" && i + 1 < argc) N = atoi(argv[++i]);
            else if (a == "--only" && i + 1 < argc) only = argv[++i];
            else if (a == "-v") verbose = true;
            else if (a == "--shadow") shadow_all = true;
            else if (a == "--rounds" && i + 1 < argc) rounds = atoi(argv[++i]);
            else if (a == "--trace" && i + 1 < argc) trace_thread = atoi(argv[++i]);
            else td = a;
        }
        id<MTLDevice> dev = MTLCreateSystemDefaultDevice();
        if (!dev) { fprintf(stderr, "no Metal device\n"); return 2; }
        id<MTLCommandQueue> q = [dev newCommandQueue];
        MTLCompileOptions *copt = [MTLCompileOptions new];
        if (@available(macOS 15.0, *)) {
            copt.mathMode = math == "safe" ? MTLMathModeSafe : math == "relaxed" ? MTLMathModeRelaxed : MTLMathModeFast;
        }
        printf("device: %s, math mode: %s, %d rounds x %d invocations per shader\n", dev.name.UTF8String, math.c_str(), rounds, N);
        const float tol = math == "fast" ? 2e-3f : 1e-3f;

        Stats st;
        for (const ManifestEntry &m : read_manifest(td)) {
            std::string sid = m.effect + "/" + m.file;
            if (!only.empty() && sid.find(only) == std::string::npos) continue;
            st.shaders++;
            std::vector<uint32_t> tok = read_tokens(td + "/" + sid);
            sm::Shader sh;
            if (!sm::parse(tok.data(), tok.size(), sh)) { printf("FAIL %s: parse\n", sid.c_str()); st.failed++; continue; }
            SmOptions opt;
            sm_default_options(&opt);
            opt.emit_test_kernel = 1;
            if (shadow_all) opt.shadow_samplers = 0xFFFF;
            SmResult res;
            if (sm_translate(tok.data(), tok.size(), &opt, &res) != 0) {
                printf("FAIL %s: %s\n", sid.c_str(), res.error);
                st.failed++;
                sm_result_free(&res);
                continue;
            }
            NSError *e = nil;
            id<MTLLibrary> lib = [dev newLibraryWithSource:[NSString stringWithUTF8String:res.msl] options:copt error:&e];
            if (!lib) { printf("FAIL %s: compile %s\n", sid.c_str(), e.localizedDescription.UTF8String); st.failed++; sm_result_free(&res); continue; }
            id<MTLFunction> fn = [lib newFunctionWithName:@"sm_test_kernel"];
            id<MTLComputePipelineState> pso = [dev newComputePipelineStateWithFunction:fn error:&e];
            if (!pso) { printf("FAIL %s: pipeline %s\n", sid.c_str(), e.localizedDescription.UTF8String); st.failed++; sm_result_free(&res); continue; }

            int bad = 0, nonfinite = 0, illcond = 0;
            float worst = 0;
            std::string first;
            for (int round = 0; round < rounds && !bad; round++) {
                Rng rng(hash_str(sid) + 7919u * (uint64_t)round);
                // constants
                std::vector<float> cf(256 * 4);
                for (float &x : cf) x = sh.ps ? rng.uni(-1.5f, 1.5f) : rng.uni(-2.0f, 2.0f);   // ps: beyond [-1,1] to exercise the ps_1_x clamp
                if (round == 0) for (int k = 0; k < 256; k += 7) cf[k * 4 + (k % 4)] = 0.0f;   // some exact zeros
                std::vector<int> ci(16 * 4), cb(16);
                for (int k = 0; k < 16; k++) { ci[k * 4] = rng.range(0, 4); ci[k * 4 + 1] = rng.range(0, 8); ci[k * 4 + 2] = rng.range(1, 2); ci[k * 4 + 3] = 0; }
                for (int &b : cb) b = rng.range(0, 1);
                SmPSAux aux = {};
                for (int s = 0; s < 8; s++) {
                    for (int k = 0; k < 4; k++) aux.bumpenv[s][k] = rng.uni(-0.5f, 0.5f);
                    aux.bumplum[s][0] = rng.uni(0.5f, 1.5f);
                    aux.bumplum[s][1] = rng.uni(-0.2f, 0.2f);
                }

                // textures
                interp::Texture texs[16];
                id<MTLTexture> mtex[16] = {nil};
                id<MTLSamplerState> msmp[16] = {nil};
                interp::Context ctx;
                ctx.c = cf.data();
                ctx.ci = ci.data();
                ctx.cb = cb.data();
                ctx.aux = aux;
                ctx.opt = opt;
                for (int k = 0; k < res.n_samplers; k++) {
                    const SmSampler &smp = res.samplers[k];
                    interp::Texture &t = texs[smp.reg];
                    t.type = smp.type;
                    t.w = t.h = smp.type == SM_TEX_3D ? 4 : 8;
                    t.d = smp.type == SM_TEX_3D ? 4 : smp.type == SM_TEX_CUBE ? 6 : 1;
                    t.depth = smp.shadow;
                    t.texels.resize((size_t)t.w * t.h * t.d * (t.depth ? 1 : 4));
                    for (float &x : t.texels) x = rng.uni(0.0f, 1.0f);
                    if (t.depth) {   // Depth32Float shadow map: private texture filled by a blit
                        MTLTextureDescriptor *dd = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatDepth32Float
                                                                                                     width:t.w height:t.h mipmapped:NO];
                        dd.usage = MTLTextureUsageShaderRead;
                        dd.storageMode = MTLStorageModePrivate;
                        mtex[smp.reg] = [dev newTextureWithDescriptor:dd];
                        id<MTLBuffer> src = [dev newBufferWithBytes:t.texels.data() length:t.texels.size() * 4 options:MTLResourceStorageModeShared];
                        id<MTLCommandBuffer> cb2 = [q commandBuffer];
                        id<MTLBlitCommandEncoder> bl = [cb2 blitCommandEncoder];
                        [bl copyFromBuffer:src sourceOffset:0 sourceBytesPerRow:t.w * 4 sourceBytesPerImage:t.w * t.h * 4
                                sourceSize:MTLSizeMake(t.w, t.h, 1) toTexture:mtex[smp.reg] destinationSlice:0 destinationLevel:0
                         destinationOrigin:MTLOriginMake(0, 0, 0)];
                        [bl endEncoding];
                        [cb2 commit];
                        [cb2 waitUntilCompleted];
                        MTLSamplerDescriptor *sd = [MTLSamplerDescriptor new];
                        sd.minFilter = sd.magFilter = MTLSamplerMinMagFilterNearest;
                        sd.sAddressMode = sd.tAddressMode = MTLSamplerAddressModeClampToEdge;
                        sd.compareFunction = MTLCompareFunctionLessEqual;
                        msmp[smp.reg] = [dev newSamplerStateWithDescriptor:sd];
                        ctx.tex[smp.reg] = &t;
                        continue;
                    }
                    MTLTextureDescriptor *td2 = [MTLTextureDescriptor new];
                    td2.pixelFormat = MTLPixelFormatRGBA32Float;
                    td2.width = t.w;
                    td2.height = t.h;
                    td2.textureType = smp.type == SM_TEX_CUBE ? MTLTextureTypeCube : smp.type == SM_TEX_3D ? MTLTextureType3D : MTLTextureType2D;
                    if (smp.type == SM_TEX_3D) td2.depth = t.d;
                    td2.usage = MTLTextureUsageShaderRead;
                    td2.storageMode = MTLStorageModeShared;
                    mtex[smp.reg] = [dev newTextureWithDescriptor:td2];
                    size_t row = (size_t)t.w * 16, slice = row * t.h;
                    if (smp.type == SM_TEX_CUBE) {
                        for (int f = 0; f < 6; f++)
                            [mtex[smp.reg] replaceRegion:MTLRegionMake2D(0, 0, t.w, t.h) mipmapLevel:0 slice:f
                                               withBytes:t.texels.data() + f * t.w * t.h * 4 bytesPerRow:row bytesPerImage:slice];
                    } else {
                        [mtex[smp.reg] replaceRegion:MTLRegionMake3D(0, 0, 0, t.w, t.h, t.d) mipmapLevel:0 slice:0
                                           withBytes:t.texels.data() bytesPerRow:row bytesPerImage:slice];
                    }
                    MTLSamplerDescriptor *sd = [MTLSamplerDescriptor new];
                    sd.minFilter = sd.magFilter = MTLSamplerMinMagFilterNearest;
                    sd.sAddressMode = sd.tAddressMode = sd.rAddressMode = MTLSamplerAddressModeClampToEdge;
                    msmp[smp.reg] = [dev newSamplerStateWithDescriptor:sd];
                    ctx.tex[smp.reg] = &t;
                }

                // inputs
                const int nin = sh.ps ? 12 : 16, nout = sh.ps ? 6 : 13;
                std::vector<float> in((size_t)N * nin * 4), out((size_t)N * nout * 4, NAN);
                for (int tdx = 0; tdx < N; tdx++) {
                    float *p = &in[(size_t)tdx * nin * 4];
                    bool zeros = round == 0 && (tdx % 8) == 7;
                    if (!sh.ps) {
                        for (int r = 0; r < 16; r++)
                            for (int k = 0; k < 4; k++) p[r * 4 + k] = rng.uni(-1.0f, 1.0f);
                        for (int k = 0; k < res.n_inputs; k++) {
                            const SmInput &si = res.inputs[k];
                            float *v = p + si.reg * 4;
                            switch (si.usage) {
                            case SM_USAGE_POSITION: for (int c = 0; c < 3; c++) v[c] = rng.uni(-10.0f, 10.0f); v[3] = 1.0f; break;
                            case SM_USAGE_BLENDINDICES: for (int c = 0; c < 4; c++) v[c] = (float)rng.range(0, 15); break;
                            case SM_USAGE_BLENDWEIGHT: case SM_USAGE_COLOR: for (int c = 0; c < 4; c++) v[c] = rng.uni(0.0f, 1.0f); break;
                            case SM_USAGE_TEXCOORD: for (int c = 0; c < 4; c++) v[c] = rng.uni(-1.0f, 2.0f); break;
                            default: break;
                            }
                            // exact zeros exercise rsq(0)/rcp(0)/0*inf; not for normals/tangents, where a
                            // zero vector creates exactly cancelling normalized vectors (ill-conditioned)
                            bool dir = si.usage == SM_USAGE_NORMAL || si.usage == SM_USAGE_TANGENT || si.usage == SM_USAGE_BINORMAL;
                            if (zeros && !dir && si.usage != SM_USAGE_BLENDINDICES) for (int c = 0; c < 3; c++) if (rng.range(0, 1)) v[c] = 0.0f;
                        }
                    } else {
                        for (int r = 0; r < 2; r++) for (int k = 0; k < 4; k++) p[r * 4 + k] = rng.uni(-0.1f, 1.1f);
                        for (int r = 2; r < 10; r++) {
                            for (int k = 0; k < 3; k++) p[r * 4 + k] = rng.uni(-0.2f, 1.2f);
                            p[r * 4 + 3] = rng.uni(0.5f, 2.0f);
                            if (zeros) for (int k = 0; k < 4; k++) if (rng.range(0, 3) == 0) p[r * 4 + k] = 0.0f;
                        }
                        for (int k = 0; k < 4; k++) p[40 + k] = rng.uni(-1.0f, 1.0f);
                        for (int k = 0; k < 4; k++) p[44 + k] = rng.uni(0.0f, 1.0f);
                    }
                }
                id<MTLBuffer> bin = [dev newBufferWithBytes:in.data() length:in.size() * 4 options:MTLResourceStorageModeShared];
                id<MTLBuffer> bout = [dev newBufferWithBytes:out.data() length:out.size() * 4 options:MTLResourceStorageModeShared];
                id<MTLCommandBuffer> cmd = [q commandBuffer];
                id<MTLComputeCommandEncoder> enc = [cmd computeCommandEncoder];
                [enc setComputePipelineState:pso];
                [enc setBuffer:bin offset:0 atIndex:0];
                [enc setBuffer:bout offset:0 atIndex:1];
                [enc setBytes:cf.data() length:cf.size() * 4 atIndex:2];
                [enc setBytes:ci.data() length:ci.size() * 4 atIndex:3];
                [enc setBytes:cb.data() length:cb.size() * 4 atIndex:4];
                [enc setBytes:&aux length:sizeof aux atIndex:5];
                for (int s = 0; s < 16; s++)
                    if (mtex[s]) { [enc setTexture:mtex[s] atIndex:s]; [enc setSamplerState:msmp[s] atIndex:s]; }
                [enc dispatchThreads:MTLSizeMake(N, 1, 1) threadsPerThreadgroup:MTLSizeMake(64, 1, 1)];
                [enc endEncoding];
                [cmd commit];
                [cmd waitUntilCompleted];
                memcpy(out.data(), bout.contents, out.size() * 4);

                // compare with the interpreter
                for (int tdx = 0; tdx < N; tdx++) {
                    const float *p = &in[(size_t)tdx * nin * 4];
                    const float *g = &out[(size_t)tdx * nout * 4];
                    auto reference = [&](std::vector<float> &ref, std::string &ierr) -> bool {
                        ref.assign(nout * 4, 0.0f);
                        if (!sh.ps) {
                            interp::VSIn vi;
                            for (int r = 0; r < 16; r++) for (int k = 0; k < 4; k++) vi.v[r][k] = p[r * 4 + k];
                            interp::VSOut vo;
                            if (!interp::run_vs(sh, ctx, vi, vo, &ierr)) return false;
                            const interp::V4 *rows[13] = {&vo.pos, &vo.col[0], &vo.col[1], &vo.tex[0], &vo.tex[1], &vo.tex[2], &vo.tex[3],
                                                          &vo.tex[4], &vo.tex[5], &vo.tex[6], &vo.tex[7], &vo.fog, &vo.psize};
                            for (int r = 0; r < 13; r++) for (int k = 0; k < 4; k++) ref[r * 4 + k] = (*rows[r])[k];
                        } else {
                            interp::PSIn pi;
                            for (int k = 0; k < 4; k++) {
                                pi.col[0][k] = p[k]; pi.col[1][k] = p[4 + k];
                                for (int r = 0; r < 8; r++) pi.tex[r][k] = p[(2 + r) * 4 + k];
                                pi.fog[k] = p[40 + k]; pi.vpos[k] = p[44 + k];
                            }
                            interp::PSOut po;
                            if (!interp::run_ps(sh, ctx, pi, po, &ierr)) return false;
                            for (int r = 0; r < 4; r++) for (int k = 0; k < 4; k++) ref[r * 4 + k] = po.oc[r][k];
                            for (int k = 0; k < 4; k++) { ref[16 + k] = po.depth; ref[20 + k] = po.killed ? 1.0f : 0.0f; }
                        }
                        return true;
                    };
                    std::string ierr;
                    interp::g_trace = tdx == trace_thread;
                    if (interp::g_trace) {
                        printf("trace thread %d inputs:\n", tdx);
                        for (int r = 0; r < nin; r++) printf("  in[%d] = %.9g %.9g %.9g %.9g\n", r, p[r*4], p[r*4+1], p[r*4+2], p[r*4+3]);
                        printf("gpu outputs:\n");
                        for (int r = 0; r < nout; r++) printf("  out[%d] = %.9g %.9g %.9g %.9g\n", r, g[r*4], g[r*4+1], g[r*4+2], g[r*4+3]);
                    }
                    std::vector<float> ref, refm, refp;
                    interp::g_ulp_nudge = 0;
                    if (!reference(ref, ierr)) { first = "interp: " + ierr; bad = N; break; }
                    interp::g_trace = false;
                    bool row_bad = false;
                    for (int k = 0; k < nout * 4; k++) {
                        float err;
                        if (!std::isfinite(ref[k])) nonfinite++;
                        bool ok = close_enough(g[k], ref[k], tol, &err);
                        if (!ok) {   // ill-conditioned? accept if a +-8 ulp nudged reference agrees
                            if (refm.empty()) {
                                interp::g_ulp_nudge = -1; reference(refm, ierr);
                                interp::g_ulp_nudge = 1; reference(refp, ierr);
                                interp::g_ulp_nudge = 0;
                            }
                            float e2;
                            float lo = std::min(ref[k], std::min(refm[k], refp[k])), hi = std::max(ref[k], std::max(refm[k], refp[k]));
                            float sc = std::max(1.0f, std::max(std::fabs(lo), std::fabs(hi)));
                            bool inside = g[k] >= lo - tol * sc && g[k] <= hi + tol * sc;
                            if (inside || close_enough(g[k], refm[k], tol, &e2) || close_enough(g[k], refp[k], tol, &e2)) { ok = true; illcond++; }
                        }
                        if (!ok) {
                            if (!row_bad && first.empty()) {
                                char b[256];
                                snprintf(b, sizeof b, "thread %d out[%d].%c gpu %.9g cpu %.9g", tdx, k / 4, "xyzw"[k % 4], g[k], ref[k]);
                                first = b;
                            }
                            row_bad = true;
                        }
                        if (std::isfinite(err)) worst = std::max(worst, err);
                    }
                    if (row_bad) bad++;
                }
            }
            if (bad) {
                printf("FAIL %-48s %4d/%d invocations differ; first: %s\n", sid.c_str(), bad, N, first.c_str());
                st.failed++;
            } else {
                st.passed++;
                if (illcond) st.illcond++;
                if (verbose || illcond) printf("ok   %-48s max rel err %.2e, %d non-finite ref values, %d ill-conditioned values\n", sid.c_str(), worst, nonfinite, illcond);
            }
            sm_result_free(&res);
        }
        printf("numeric check: %d shaders, %d match (%d with ill-conditioned values accepted via +-8 ulp reference), %d differ\n",
               st.shaders, st.passed, st.illcond, st.failed);
        return st.failed ? 1 : 0;
    }
}
