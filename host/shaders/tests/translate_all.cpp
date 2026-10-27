// translate_all.cpp - translates every shader listed in testdata/manifest.tsv and writes
// <out>/<effect>/<name>.metal (host variant) and <name>.test.metal (with sm_test_kernel).
#include "../sm_translate.h"
#include "common.h"
#include <sys/stat.h>

int main(int argc, char **argv) {
    std::string td = argc > 1 ? argv[1] : "testdata";
    std::string out = argc > 2 ? argv[2] : "build/msl";
    mkdir(out.c_str(), 0755);
    int ok = 0, bad = 0;
    for (const ManifestEntry &m : read_manifest(td)) {
        std::vector<uint32_t> tok = read_tokens(td + "/" + m.effect + "/" + m.file);
        std::string base = m.file.substr(0, m.file.size() - 4);
        mkdir((out + "/" + m.effect).c_str(), 0755);
        for (int variant = 0; variant < 2; variant++) {
            SmOptions opt;
            sm_default_options(&opt);
            opt.emit_test_kernel = variant;
            SmResult r;
            if (sm_translate(tok.data(), tok.size(), &opt, &r) != 0) {
                printf("FAIL %s/%s: %s\n", m.effect.c_str(), m.file.c_str(), r.error);
                bad++;
                sm_result_free(&r);
                break;
            }
            std::string path = out + "/" + m.effect + "/" + base + (variant ? ".test.metal" : ".metal");
            FILE *f = fopen(path.c_str(), "w");
            fwrite(r.msl, 1, r.msl_len, f);
            fclose(f);
            if (!variant) ok++;
            sm_result_free(&r);
        }
    }
    printf("translated %d shaders, %d failed\n", ok, bad);
    return bad ? 1 : 0;
}
