// xenos_translate - translates Xbox 360 shader containers (the newer 0x102A11xx layout) to text with the XenosRecomp
// translator core (MIT, LICENSE.md). This driver is this repository's own code (MIT). Built by
// tools_snapshot/x360_shader_body.py with the Command Line Tools' clang++ (no Xcode, no DXC); never run on anything but
// the containers that script rebuilt from the player's own ZZDATA0.BIN.
// usage: xenos_translate <container.bin> <out.txt> [<container.bin> <out.txt> ...]
#include "pch.h"
#include "shader_recompiler.h"

#include <cstdio>
#include <fstream>
#include <iterator>
#include <vector>

int main(int argc, char** argv)
{
    if (argc < 3 || (argc - 1) % 2)
    {
        std::fprintf(stderr, "usage: %s <container.bin> <out.txt> [...]\n", argv[0]);
        return 1;
    }
    for (int i = 1; i + 1 < argc; i += 2)
    {
        std::ifstream in(argv[i], std::ios::binary);
        if (!in)
        {
            std::fprintf(stderr, "xenos_translate: cannot read %s\n", argv[i]);
            return 1;
        }
        std::vector<uint8_t> data((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
        data.resize(data.size() + 64); // slack for reads past the last instruction

        ShaderRecompiler recompiler;
        recompiler.recompile(data.data(), "");

        std::ofstream out(argv[i + 1], std::ios::binary);
        out << recompiler.out;
        if (!out)
        {
            std::fprintf(stderr, "xenos_translate: cannot write %s\n", argv[i + 1]);
            return 1;
        }
    }
    return 0;
}
