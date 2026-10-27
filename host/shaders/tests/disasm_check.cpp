// disasm_check.cpp - decodes every testdata/*.bin with sm::parse and compares the
// disassembly line by line with the reference .asm (tools/scripts/extract_pc_effects.py,
// an independent decoder) in render_diff/hdr_dof/pc_effects.
#include "../sm_ir.h"
#include "common.h"
#include <cstdio>
#include <fstream>
#include <sstream>

int main(int argc, char **argv) {
    std::string td = argc > 1 ? argv[1] : "testdata";
    std::string ref = argc > 2 ? argv[2] : "../../../render_diff/hdr_dof/pc_effects";
    int ok = 0, bad = 0;
    for (const ManifestEntry &m : read_manifest(td)) {
        std::vector<uint32_t> tok = read_tokens(td + "/" + m.effect + "/" + m.file);
        sm::Shader sh;
        if (!sm::parse(tok.data(), tok.size(), sh)) {
            printf("FAIL %s/%s: %s\n", m.effect.c_str(), m.file.c_str(), sh.error.c_str());
            bad++;
            continue;
        }
        std::vector<std::string> mine = split_lines(sm::disassemble(sh));
        std::string asmf = ref + "/" + m.effect + "/" + m.file.substr(0, m.file.size() - 4) + ".asm";
        std::ifstream f(asmf);
        std::vector<std::string> theirs;
        std::string line;
        bool in = false;
        while (std::getline(f, line)) {
            std::string t = trim(strip_comment(line));
            if (!in) {
                if (!line.empty() && line.rfind("//", 0) != 0 && (t.rfind("vs_", 0) == 0 || t.rfind("ps_", 0) == 0)) {
                    in = true;
                    theirs.push_back(t);
                }
                continue;
            }
            if (t.empty()) break;
            theirs.push_back(t);
        }
        bool same = mine.size() == theirs.size();
        size_t first = 0;
        for (size_t i = 0; same && i < mine.size(); i++)
            if (trim(mine[i]) != theirs[i]) { same = false; first = i; }
        if (same) { ok++; continue; }
        bad++;
        printf("DIFF %s/%s (%zu vs %zu lines)\n", m.effect.c_str(), m.file.c_str(), mine.size(), theirs.size());
        for (size_t i = first; i < first + 3 && i < mine.size(); i++)
            printf("   mine : %s\n   ref  : %s\n", mine[i].c_str(), i < theirs.size() ? theirs[i].c_str() : "");
    }
    printf("disasm check: %d identical, %d different\n", ok, bad);
    return bad ? 1 : 0;
}
