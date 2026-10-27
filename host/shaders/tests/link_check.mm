// link_check.mm - for every effect pass in testdata/passes.tsv, translates the bound VS and
// PS (host variants, no test kernel), compiles them with the Metal runtime compiler and
// creates a real MTLRenderPipelineState (vertex descriptor from the VS reflection), which
// validates the vertex -> fragment varying interface. Also checks D3D-level linkage: every
// varying the PS reads is written by the VS.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>

#include "../sm_translate.h"
#include "common.h"

#include <map>

static id<MTLFunction> build(id<MTLDevice> dev, const std::string &path, SmResult &r, std::string &err) {
    std::vector<uint32_t> tok = read_tokens(path);
    SmOptions opt;
    sm_default_options(&opt);
    if (sm_translate(tok.data(), tok.size(), &opt, &r) != 0) { err = r.error; return nil; }
    NSError *e = nil;
    id<MTLLibrary> lib = [dev newLibraryWithSource:[NSString stringWithUTF8String:r.msl] options:[MTLCompileOptions new] error:&e];
    if (!lib) { err = e.localizedDescription.UTF8String; return nil; }
    return [lib newFunctionWithName:@"sm_main"];
}

int main(int argc, char **argv) {
    @autoreleasepool {
        std::string td = argc > 1 ? argv[1] : "testdata";
        id<MTLDevice> dev = MTLCreateSystemDefaultDevice();
        std::ifstream f(td + "/passes.tsv");
        std::string line;
        std::getline(f, line);
        int ok = 0, bad = 0, fixed = 0;
        while (std::getline(f, line)) {
            auto c = split(line, '\t');
            if (c.size() < 6) continue;
            std::string where = c[0] + " t" + c[1] + ":" + c[2] + "/" + c[3];
            if (c[4] == "-" || c[5] == "-") { fixed++; continue; }   // fixed-function pass
            SmResult vr, pr;
            std::string err;
            id<MTLFunction> vf = build(dev, td + "/" + c[0] + "/" + c[4], vr, err);
            id<MTLFunction> pf = vf ? build(dev, td + "/" + c[0] + "/" + c[5], pr, err) : nil;
            if (!vf || !pf) { printf("FAIL %s: %s\n", where.c_str(), err.c_str()); bad++; continue; }
            uint32_t missing = pr.varyings_read & ~vr.varyings_written;
            MTLRenderPipelineDescriptor *d = [MTLRenderPipelineDescriptor new];
            d.vertexFunction = vf;
            d.fragmentFunction = pf;
            MTLVertexDescriptor *vd = [MTLVertexDescriptor vertexDescriptor];
            for (int k = 0; k < vr.n_inputs; k++) {
                int reg = vr.inputs[k].reg;
                vd.attributes[reg].format = MTLVertexFormatFloat4;
                vd.attributes[reg].offset = 16 * k;
                vd.attributes[reg].bufferIndex = 0;
            }
            vd.layouts[0].stride = 16 * (vr.n_inputs ? vr.n_inputs : 1);
            d.vertexDescriptor = vd;
            for (int k = 0; k < 4; k++)
                if (pr.color_outputs & (1 << k)) d.colorAttachments[k].pixelFormat = MTLPixelFormatBGRA8Unorm;
            d.depthAttachmentPixelFormat = MTLPixelFormatDepth32Float;
            NSError *e = nil;
            id<MTLRenderPipelineState> pso = [dev newRenderPipelineStateWithDescriptor:d error:&e];
            if (!pso) { printf("FAIL %s: pipeline: %s\n", where.c_str(), e.localizedDescription.UTF8String); bad++; }
            else {
                ok++;
                if (missing) printf("note %s: PS reads varyings 0x%x the VS never writes (zero in D3D terms)\n", where.c_str(), missing);
            }
            sm_result_free(&vr);
            sm_result_free(&pr);
        }
        printf("link check: %d pipelines created, %d failed, %d fixed-function passes skipped\n", ok, bad, fixed);
        return bad ? 1 : 0;
    }
}
