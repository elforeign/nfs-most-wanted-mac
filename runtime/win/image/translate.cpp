// Build-time tool: parse the player's D3D9 bytecode using the shared decoder.
#include "hlsl.h"
#if defined(RECOMP_WINDOWS_COMPILE_ONLY)
// The COFF tool object is compile proof only, just like the emitter object.
extern "C" {
typedef struct ToolFile FILE;
FILE *fopen(const char *, const char *);
int fclose(FILE *); int fseek(FILE *, long, int); long ftell(FILE *); void rewind(FILE *);
size_t fread(void *, size_t, size_t, FILE *); size_t fwrite(const void *, size_t, size_t, FILE *);
int printf(const char *, ...);
}
#define SEEK_END 2
#else
#include <stdio.h>
#endif
int main(int argc, char **argv) {
    if (argc != 3) { printf("usage: shader-hlsl input.bin output.hlsl\n"); return 2; }
    FILE *f = fopen(argv[1], "rb");
    if (!f) return 2;
    fseek(f, 0, SEEK_END); long size = ftell(f); rewind(f);
    if (size < 8 || size > 65536 || size % 4) { fclose(f); return 2; }
    uint32_t tokens[16384];
    bool read = fread(tokens, 1, size, f) == size_t(size); fclose(f);
    sm::Shader s; std::string source, error;
    if (!read || !sm::parse(tokens, size_t(size) / 4, s) || !sm::emit_hlsl(s, source, error)) {
        printf("shader-hlsl: %s %s\n", s.error.c_str(), error.c_str()); return 1;
    }
    f = fopen(argv[2], "wb"); if (!f) return 2;
    bool written = fwrite(source.data(), 1, source.size(), f) == source.size();
    return fclose(f) || !written;
}
